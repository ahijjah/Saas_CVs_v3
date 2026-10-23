"""Migration 103 on a real PostgreSQL 16: it is additive and idempotent, leaves legacy scores untouched, enforces the storage rules in the database,
protects tenants with row-level security, and its rollback removes exactly what it added."""
from __future__ import annotations

import json
import pathlib
import uuid

import psycopg2
import pytest

from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason

BACKEND = pathlib.Path(__file__).resolve().parent.parent
UP = (BACKEND / "db/migrations/103_item_level_scoring.sql").read_text(encoding="utf-8")
DOWN = (BACKEND / "db/rollback/103_item_level_scoring.down.sql").read_text(encoding="utf-8")
TABLES = ("application_scoring_runs", "application_item_evaluations", "application_item_evaluation_events")


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


def _seed(db):
    cur = db.conn.cursor()
    tag = uuid.uuid4().hex[:8]
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"m{tag}", f"m{tag}.example"))
    tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id",
                (tenant, f"m{tag}@example.test"))
    user = str(cur.fetchone()[0])
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', 'd') RETURNING job_id", (tenant, user))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, candidate_email, candidate_phone, submission_source, processing_status,"
                " decision) VALUES (%s, %s, 'J', %s, '+1', 'manual_upload', 'ai_scored', 'qualified') RETURNING application_id",
                (job, tenant, f"{tag}@example.test"))
    app = str(cur.fetchone()[0])
    return tenant, user, job, app


def _run(db, tenant, job, app, **kw):
    cur = db.conn.cursor()
    values = {"status": "review_required", "min": 0, "max": 50, "raw": None, "score": None, "decision": None}
    values.update(kw)
    cur.execute("INSERT INTO application_scoring_runs (application_id, tenant_id, job_id, requirements_revision, status, category_weights,"
                " final_score_min, final_score_max, final_score_raw, final_score, decision) VALUES (%s, %s, %s, 1, %s, '{}', %s, %s, %s, %s, %s)"
                " RETURNING run_id", (app, tenant, job, values["status"], values["min"], values["max"], values["raw"], values["score"], values["decision"]))
    return str(cur.fetchone()[0])


def test_the_migration_is_idempotent_and_adds_no_default_that_changes_existing_rows(built):
    cur = built.conn.cursor()
    tenant, user, job, app = _seed(built)
    cur.execute(UP)
    cur.execute(UP)
    cur.execute("SELECT scoring_status, item_final_score, decision FROM applications WHERE application_id = %s", (app,))
    assert cur.fetchone() == (None, None, "qualified")                      # an existing application is untouched: legacy scoring, no status


def test_a_review_required_run_needs_a_range_and_a_scored_run_needs_a_score_raw_value_and_decision(built):
    tenant, user, job, app = _seed(built)
    cur = built.conn.cursor()
    for kw in ({"min": None, "max": None}, {"min": 60, "max": 50}):
        with pytest.raises(psycopg2.errors.CheckViolation):
            _run(built, tenant, job, app, **kw)
    with pytest.raises(psycopg2.errors.CheckViolation):
        _run(built, tenant, job, app, status="scored", score=80, raw=None, decision="partial")
    with pytest.raises(psycopg2.errors.CheckViolation):
        _run(built, tenant, job, app, status="review_required", score=80, raw=80, decision="partial")      # a final score while review is required
    assert _run(built, tenant, job, app, status="scored", min=79.5, max=79.5, raw=79.5, score=80, decision="partial")


def test_a_preferred_item_cannot_carry_a_weight_and_results_must_match_the_importance(built):
    tenant, user, job, app = _seed(built)
    run = _run(built, tenant, job, app)
    cur = built.conn.cursor()

    def insert(importance, weight, result):
        cur.execute("INSERT INTO application_item_evaluations (run_id, application_id, tenant_id, requirements_revision, item_id, category, importance,"
                    " item_text, item_weight, ai_result, result, evaluation_source) VALUES (%s, %s, %s, 1, %s, 'skills', %s, 't', %s, %s, %s, 'x')",
                    (run, app, tenant, uuid.uuid4().hex[:8], importance, weight, result, result))

    for importance, weight, result in (("preferred", 20, "yes"), ("required", None, "met"), ("required", 20, "yes"), ("preferred", None, "met"),
                                       ("required", 20, "not_clear"), ("required", 0, "met"), ("required", 101, "met")):
        with pytest.raises(psycopg2.errors.CheckViolation):
            insert(importance, weight, result)
    insert("required", 20, "met")
    insert("preferred", None, "not_clear")


def test_a_resolution_needs_a_reason_and_the_event_history_needs_one_too(built):
    tenant, user, job, app = _seed(built)
    run = _run(built, tenant, job, app)
    cur = built.conn.cursor()
    cur.execute("INSERT INTO application_item_evaluations (run_id, application_id, tenant_id, requirements_revision, item_id, category, importance,"
                " item_text, item_weight, ai_result, result, evaluation_source) VALUES (%s, %s, %s, 1, 'a', 'skills', 'required', 't', 20,"
                " 'unclear', 'unclear', 'x') RETURNING evaluation_id", (run, app, tenant))
    evaluation = str(cur.fetchone()[0])
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("UPDATE application_item_evaluations SET result = 'met', resolved_by = %s, resolved_at = now() WHERE evaluation_id = %s", (user, evaluation))
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("UPDATE application_item_evaluations SET result = 'met' WHERE evaluation_id = %s", (evaluation,))     # no resolution record at all
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO application_item_evaluation_events (evaluation_id, run_id, application_id, tenant_id, event_type, previous_result,"
                    " new_result, user_id) VALUES (%s, %s, %s, %s, 'resolved', 'unclear', 'met', %s)", (evaluation, run, app, tenant, user))
    cur.execute("UPDATE application_item_evaluations SET result = 'met', resolved_by = %s, resolved_at = now(), resolution_reason = 'checked'"
                " WHERE evaluation_id = %s", (user, evaluation))


def test_all_three_tables_have_row_level_security_with_tenant_and_worker_policies(built):
    cur = built.conn.cursor()
    cur.execute("SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY(%s)", (list(TABLES),))
    assert dict(cur.fetchall()) == {t: True for t in TABLES}
    cur.execute("SELECT policyname FROM pg_policies WHERE tablename = ANY(%s)", (list(TABLES),))
    names = {r[0] for r in cur.fetchall()}
    assert {f"tenant_scope_{t}" for t in TABLES} | {f"cv_worker_access_{t}" for t in TABLES} <= names


def test_rollback_removes_exactly_what_103_added_and_loses_no_application_or_legacy_score():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        cur = db.conn.cursor()
        tenant, user, job, app = _seed(db)
        cur.execute("INSERT INTO application_scores (application_id, score_skills, score_experience, score_education, score_certifications,"
                    " score_soft_skills, score_domain_knowledge, score_other, final_score, weights_snapshot, ai_model)"
                    " VALUES (%s, 80, 70, 60, 0, 0, 0, 0, 72, '{}', 'old')", (app,))
        _run(db, tenant, job, app)
        cur.execute(DOWN)
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ANY(%s)", (list(TABLES),))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'applications' AND column_name IN ('scoring_status', 'item_final_score')")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT decision FROM applications WHERE application_id = %s", (app,))
        assert cur.fetchone() == ("qualified",)
        cur.execute("SELECT final_score FROM application_scores WHERE application_id = %s", (app,))
        assert int(cur.fetchone()[0]) == 72
        cur.execute(DOWN)                                                    # a second run changes nothing and does not fail
        cur.execute(UP)                                                      # and the upgrade can be applied again
        cur.execute("SELECT scoring_status FROM applications WHERE application_id = %s", (app,))
        assert cur.fetchone() == (None,)
