"""DB0: the repaired chain. A clean database built from the files has zero migration failures, the repaired objects exist with
the expected definitions, and valid existing rows survive replacement 097. Every test uses the real runner. None grants a
privilege or creates a role; the chain is built by the superuser fixture, exactly as the harness has always done."""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import psycopg2
import pytest

import realdb_helper as H
from db.runner import runner
from db.runner.manifest import MANIFEST

REPLACEMENTS = {
    "010_platform_control.sql": "095_repair_010_platform_control.sql",
    "070_sla_thresholds_config.sql": "096_repair_070_sla_policy_index.sql",
    "087_autosave_deterministic_answers.sql": "097_repair_087_knockout_answer_tenant.sql",
}


def _scalar(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()[0]


@contextmanager
def schema_database():
    """A throw-away database with schema.sql applied and no migrations: the starting point of a fresh chain run."""
    reason = H.unavailable_reason()
    if reason:
        pytest.skip(reason)
    cluster = H.Cluster()
    try:
        name = "c_" + uuid.uuid4().hex[:10]
        admin = psycopg2.connect(cluster.dsn())
        admin.autocommit = True
        admin.cursor().execute(f"CREATE DATABASE {name} ENCODING 'UTF8' TEMPLATE template0")
        conn = psycopg2.connect(cluster.dsn(name))
        conn.autocommit = True
        conn.cursor().execute(H.schema_sql())
        try:
            yield conn
        finally:
            conn.close()
            admin.cursor().execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
            admin.close()
    finally:
        cluster.stop()


def test_clean_chain_builds_with_zero_expected_or_unexpected_failures():
    with H.built_chain() as db:
        assert db.failures == []
        H.check_chain(db.failures)
        assert H.EXPECTED_PRE_EXISTING_FAILURES == frozenset()


def test_starter_professional_enterprise_and_trial_plans_exist():
    with H.built_chain() as db:
        codes = _scalar(db.conn, "SELECT array_agg(plan_code ORDER BY plan_code) FROM cv_analyzer.subscription_plans "
                                 "WHERE plan_code IN ('trial', 'starter', 'professional', 'enterprise')")
        assert codes == ["enterprise", "professional", "starter", "trial"]


def test_corrected_sla_index_is_a_gin_index_on_the_parenthesised_expression():
    with H.built_chain() as db:
        cur = db.conn.cursor()
        cur.execute("""
            SELECT am.amname, i.indnatts, i.indexprs IS NOT NULL, i.indisunique,
                   i.indrelid = 'cv_analyzer.tenant_workflow_policies'::regclass
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_am am ON am.oid = c.relam
            WHERE i.indexrelid = 'cv_analyzer.idx_workflow_policies_sla'::regclass
        """)
        row = cur.fetchone()
        assert row == ("gin", 1, True, False, True)
        cur.execute("SELECT pg_get_indexdef('cv_analyzer.idx_workflow_policies_sla'::regclass)")
        assert "sla_thresholds" in cur.fetchone()[0]


def test_sla_defaults_are_back_filled_on_every_policy_row():
    with H.built_chain() as db:
        assert _scalar(db.conn, "SELECT count(*) FROM cv_analyzer.tenant_workflow_policies "
                                "WHERE (policies -> 'sla_thresholds') IS NULL") == 0


def test_knockout_answer_tenant_id_is_uuid_and_not_null():
    with H.built_chain() as db:
        cur = db.conn.cursor()
        cur.execute("""SELECT data_type, udt_name, is_nullable FROM information_schema.columns
                       WHERE table_schema = 'cv_analyzer' AND table_name = 'application_knockout_answers'
                         AND column_name = 'tenant_id'""")
        assert cur.fetchone() == ("uuid", "uuid", "NO")


def test_metadata_and_subscription_columns_from_010_exist_with_their_types():
    with H.built_chain() as db:
        cur = db.conn.cursor()
        cur.execute("""SELECT table_name, column_name, udt_name FROM information_schema.columns
                       WHERE table_schema = 'cv_analyzer'
                         AND ((table_name = 'system_config' AND column_name IN ('type','category','editable','updated_by'))
                           OR (table_name = 'tenants' AND column_name IN ('subscription_status','trial_start_at',
                                 'trial_end_at','subscription_started_at','subscription_ends_at')))
                       ORDER BY 1, 2""")
        rows = cur.fetchall()
        assert len(rows) == 9
        assert ("system_config", "category", "varchar") in rows
        assert ("tenants", "subscription_status", "varchar") in rows
        assert ("tenants", "trial_end_at", "timestamptz") in rows


def test_ledger_records_each_original_superseded_and_each_replacement_applied():
    with H.built_chain() as db:
        cur = db.conn.cursor()
        cur.execute("SELECT filename, status, replaced_by FROM cv_analyzer.schema_migrations "
                    "WHERE filename IN %s ORDER BY filename", (tuple(REPLACEMENTS) + tuple(REPLACEMENTS.values()),))
        rows = {f: (s, r) for f, s, r in cur.fetchall()}
        for original, replacement in REPLACEMENTS.items():
            assert rows[original] == ("superseded", replacement)
            assert rows[replacement] == ("applied", None)


def test_ledger_has_one_row_per_manifest_entry_on_a_clean_build():
    with H.built_chain() as db:
        assert _scalar(db.conn, "SELECT count(*) FROM cv_analyzer.schema_migrations") == len(MANIFEST)


def test_replacements_run_at_their_original_positions_before_dependent_migrations():
    """A fresh run applies exactly the manifest order, without the superseded originals. 095 therefore precedes 024, which
    needs the starter and professional plans that 095 creates."""
    with schema_database() as conn:
        report = runner.run(conn, allow_fresh=True)
    expected = [e.file for e in MANIFEST if not e.superseded_by]
    assert report.applied == expected
    assert report.applied.index("095_repair_010_platform_control.sql") < report.applied.index("024_plan_features_seed.sql")
    assert report.applied.index("096_repair_070_sla_policy_index.sql") < report.applied.index(
        expected[expected.index("096_repair_070_sla_policy_index.sql") + 1])
    assert sorted(report.superseded) == sorted(REPLACEMENTS)


def test_second_invocation_on_the_built_chain_performs_no_migrations():
    with H.built_chain() as db:
        report = runner.run(db.conn)
        assert report.applied == [] and report.pending() == []


def test_dry_run_on_the_built_chain_writes_nothing():
    with H.built_chain() as db:
        def snapshot():
            return (_scalar(db.conn, "SELECT count(*) FROM cv_analyzer.schema_migrations"),
                    _scalar(db.conn, "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                     "WHERE n.nspname = 'cv_analyzer'"))
        before = snapshot()
        report = runner.run(db.conn, dry_run=True)
        assert report.pending() == []
        assert snapshot() == before


def test_valid_knockout_rows_survive_replacement_097_and_candidate_email_suggestions_are_promoted():
    """Build up to 097, insert pre-existing answers and one promotable suggestion, then run 097. The existing answer keeps
    its value and gains the tenant of its application; the promotable suggestion becomes a final answer."""
    with schema_database() as conn:
        runner.run(conn, allow_fresh=True, stop_before="097_repair_087_knockout_answer_tenant.sql")
        cur = conn.cursor()
        cur.execute("SET session_replication_role = replica")     # test data only: skips foreign-key triggers
        tenant = uuid.uuid4()
        cur.execute("INSERT INTO cv_analyzer.tenants (tenant_id, name, email_domain) VALUES (%s, 'T', 'example.test')",
                    (str(tenant),))
        cur.execute("INSERT INTO cv_analyzer.users (tenant_id, email, password_hash, full_name) "
                    "VALUES (%s, 'owner@example.test', 'x', 'Owner') RETURNING user_id", (str(tenant),))
        owner = cur.fetchone()[0]
        cur.execute("INSERT INTO cv_analyzer.jobs (tenant_id, created_by, title, description) "
                    "VALUES (%s, %s, 'J', 'D') RETURNING job_id", (str(tenant), str(owner)))
        job = cur.fetchone()[0]
        apps = []
        for _ in range(2):
            cur.execute("INSERT INTO cv_analyzer.applications (job_id, tenant_id, candidate_name, submission_source) "
                        "VALUES (%s, %s, 'c', 'manual_upload') RETURNING application_id", (str(job), str(tenant)))
            apps.append(cur.fetchone()[0])
        questions = []
        for text in ("Do you hold a licence?", "Do you have five years of experience?"):
            cur.execute("INSERT INTO cv_analyzer.job_knockout_questions (job_id, tenant_id, question_text) "
                        "VALUES (%s, %s, %s) RETURNING question_id", (str(job), str(tenant), text))
            questions.append(cur.fetchone()[0])
        existing_question, promoted_question = questions
        cur.execute("INSERT INTO cv_analyzer.application_knockout_answers "
                    "(application_id, question_id, answer_value, answer_source, answer_method) "
                    "VALUES (%s, %s, 'yes', 'manual', 'direct')", (str(apps[0]), str(existing_question)))
        cur.execute("""INSERT INTO cv_analyzer.application_knockout_answer_suggestions
                       (application_id, tenant_id, question_id, suggested_answer, suggested_source, answer_method,
                        ai_model, verification_status, confidence, status, evidence_text)
                       VALUES (%s, %s, %s, 'yes', 'candidate_email', 'direct_statement',
                               'deterministic_extractor', 'verified', 0.97, 'pending', 'evidence')""",
                    (str(apps[1]), str(tenant), str(promoted_question)))
        cur.execute("SET session_replication_role = origin")

        runner.run(conn)

        cur.execute("SELECT tenant_id::text, answer_value FROM cv_analyzer.application_knockout_answers "
                    "WHERE application_id = %s AND question_id = %s", (str(apps[0]), str(existing_question)))
        assert cur.fetchone() == (str(tenant), "yes")
        cur.execute("SELECT tenant_id::text, answer_value, answer_source FROM cv_analyzer.application_knockout_answers "
                    "WHERE application_id = %s AND question_id = %s", (str(apps[1]), str(promoted_question)))
        assert cur.fetchone() == (str(tenant), "yes", "candidate_email")
        cur.execute("SELECT status FROM cv_analyzer.application_knockout_answer_suggestions "
                    "WHERE application_id = %s", (str(apps[1]),))
        assert cur.fetchone()[0] == "accepted"
