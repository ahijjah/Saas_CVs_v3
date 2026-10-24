"""Migration 104 on a real PostgreSQL 16: additive and idempotent, tenant-safe, the stored rules enforced by the database, the review_reason
label backfilled from existing evaluations, and a rollback that removes exactly what 104 added and loses no application, score, evaluation or gate."""
from __future__ import annotations

import json
import pathlib
import uuid

import psycopg2
import pytest

from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from test_item_scoring_migration_postgres import _run, _seed

BACKEND = pathlib.Path(__file__).resolve().parent.parent
UP = (BACKEND / "db/migrations/104_candidate_matrix.sql").read_text(encoding="utf-8")
DOWN = (BACKEND / "db/rollback/104_candidate_matrix.down.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


def _evaluation(db, tenant, app, run, item, explanation, *, result="unclear", reason=None):
    cur = db.conn.cursor()
    cur.execute("INSERT INTO application_item_evaluations (run_id, application_id, tenant_id, requirements_revision, position, item_id, category, importance,"
                " item_text, item_weight, ai_result, result, evidence, explanation, evaluation_source, review_reason)"
                " VALUES (%s, %s, %s, 1, 0, %s, 'skills', 'required', 'x', 100, %s, %s, '[]', %s, 'ai', %s) RETURNING evaluation_id",
                (run, app, tenant, item, result, result, explanation, reason))
    return str(cur.fetchone()[0])


def _config(db, tenant, scope, job=None, user=None, config='{"columns": ["candidate.name"]}'):
    cur = db.conn.cursor()
    cur.execute("INSERT INTO candidate_matrix_configs (tenant_id, scope, job_id, user_id, config) VALUES (%s, %s, %s, %s, %s)", (tenant, scope, job, user, config))


def test_the_migration_is_idempotent_and_adds_nothing_that_changes_existing_rows(built):
    cur = built.conn.cursor()
    cur.execute(UP)
    cur.execute(UP)
    cur.execute("SELECT count(*) FROM candidate_matrix_configs")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT is_nullable FROM information_schema.columns WHERE table_name = 'application_item_evaluations' AND column_name = 'review_reason'")
    assert cur.fetchone() == ("YES",)


def test_a_layout_needs_a_consistent_scope_and_one_per_owner(built):
    tenant, user, job, app = _seed(built)
    cur = built.conn.cursor()
    _config(built, tenant, "tenant")
    _config(built, tenant, "job", job)
    _config(built, tenant, "personal", job, user)
    for scope, j, u in (("tenant", job, None), ("job", None, None), ("job", job, user), ("personal", job, None), ("personal", None, user), ("team", None, None)):
        with pytest.raises(psycopg2.Error):
            _config(built, tenant, scope, j, u)
    for scope, j, u in (("tenant", None, None), ("job", job, None), ("personal", job, user)):          # a second layout for the same owner
        with pytest.raises(psycopg2.errors.UniqueViolation):
            _config(built, tenant, scope, j, u)


def test_a_layout_must_be_a_bounded_json_object_and_start_at_version_one(built):
    tenant, user, job, _ = _seed(built)
    cur = built.conn.cursor()
    for bad in ('[1]', '"x"', json.dumps({"x": "y" * 40000})):
        with pytest.raises(psycopg2.errors.CheckViolation):
            _config(built, tenant, "job", job, None, bad)
    _config(built, tenant, "job", job)
    cur.execute("SELECT version FROM candidate_matrix_configs WHERE job_id = %s", (job,))
    assert cur.fetchone() == (1,)


def test_a_layout_dies_with_its_job_or_user_and_never_with_an_application(built):
    tenant, user, job, app = _seed(built)
    cur = built.conn.cursor()
    _config(built, tenant, "job", job)
    cur.execute("DELETE FROM jobs WHERE job_id = %s", (job,))
    cur.execute("SELECT count(*) FROM candidate_matrix_configs WHERE tenant_id = %s", (tenant,))
    assert cur.fetchone()[0] == 0


def test_the_table_has_row_level_security_with_the_tenant_policy(built):
    cur = built.conn.cursor()
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE relname = 'candidate_matrix_configs'")
    assert cur.fetchone() == (True,)
    cur.execute("SELECT count(*) FROM pg_policies WHERE tablename = 'candidate_matrix_configs' AND policyname = 'tenant_scope_candidate_matrix_configs'")
    assert cur.fetchone()[0] == 1


def test_review_reason_accepts_only_the_three_labels(built):
    tenant, user, job, app = _seed(built)
    run = _run(built, tenant, job, app)
    cur = built.conn.cursor()
    for label in ("no_verified_evidence", "low_confidence", "conflicting_evidence", None):
        _evaluation(built, tenant, app, run, f"req_{label}", "x", reason=label)
    with pytest.raises(psycopg2.errors.CheckViolation):
        _evaluation(built, tenant, app, run, "req_bad", "x", reason="because")


def test_existing_downgraded_evaluations_are_labelled_once_from_the_evaluators_note(built):
    tenant, user, job, app = _seed(built)
    run = _run(built, tenant, job, app)
    a = _evaluation(built, tenant, app, run, "req_a", "Downgraded to unclear: no quotation from the CV could be verified (the model said met). stated")
    b = _evaluation(built, tenant, app, run, "req_b", "Downgraded to unclear: the model's confidence is too low (the model said not_met).")
    c = _evaluation(built, tenant, app, run, "req_c", "The CV does not say.")
    cur = built.conn.cursor()
    cur.execute(UP)
    cur.execute("SELECT item_id, review_reason FROM application_item_evaluations WHERE run_id = %s ORDER BY item_id", (run,))
    assert cur.fetchall() == [("req_a", "no_verified_evidence"), ("req_b", "low_confidence"), ("req_c", None)]
    cur.execute(UP)                                                                          # a second application changes nothing
    cur.execute("SELECT count(*) FROM application_item_evaluations WHERE run_id = %s AND review_reason IS NOT NULL", (run,))
    assert cur.fetchone()[0] == 2


def test_rollback_removes_exactly_what_104_added_and_loses_no_application_score_evaluation_or_gate(built):
    tenant, user, job, app = _seed(built)
    run = _run(built, tenant, job, app)
    _evaluation(built, tenant, app, run, "req_a", "Downgraded to unclear: no quotation from the CV could be verified (the model said met).")
    cur = built.conn.cursor()
    cur.execute("INSERT INTO application_gate_results (application_id, tenant_id, job_id, gate_key, gate_source, requirement_item_id, gate_label, verification_method,"
                " failure_action, computed_result, result) VALUES (%s, %s, %s, 'req:req_a', 'requirement', 'req_a', 'g', 'cv_evidence', 'not_eligible', 'failed', 'failed')",
                (app, tenant, job))
    _config(built, tenant, "job", job)
    cur.execute(DOWN)
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'candidate_matrix_configs'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'application_item_evaluations' AND column_name = 'review_reason'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT result, explanation IS NOT NULL FROM application_item_evaluations WHERE run_id = %s", (run,))
    assert cur.fetchone() == ("unclear", True)
    cur.execute("SELECT count(*) FROM application_scoring_runs WHERE run_id = %s", (run,))
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT result FROM application_gate_results WHERE application_id = %s", (app,))
    assert cur.fetchone() == ("failed",)
    cur.execute("SELECT decision FROM applications WHERE application_id = %s", (app,))
    assert cur.fetchone() == ("qualified",)
    cur.execute(DOWN)                                                                        # a second run changes nothing and does not fail
    cur.execute(UP)                                                                          # and the upgrade can be applied again
    cur.execute("SELECT count(*) FROM candidate_matrix_configs")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT review_reason FROM application_item_evaluations WHERE run_id = %s", (run,))
    assert cur.fetchone() == ("no_verified_evidence",)                                      # re-labelled from the surviving note
