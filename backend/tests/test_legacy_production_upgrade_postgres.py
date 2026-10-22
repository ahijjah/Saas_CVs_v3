"""The legacy production database, converted for the current release, end to end on disposable PostgreSQL 16 clusters.

The old state is rebuilt from the committed legacy files (tests/legacy_production_helper.py): the legacy schema.sql, main's migrations 002
to 092, and the sixteen legacy migrations 093 to 108, run as the legacy application role `cv_app` (LOGIN, SUPERUSER, BYPASSRLS), with no
migration ledger. Representative rows are inserted across every table family the legacy migrations touched. Then the whole procedure runs
in order, as an operator would run it (docs/LEGACY_PRODUCTION_UPGRADE.md):

    db/ops/0000  ->  scripts.legacy_upgrade prepare  ->  scripts.migrate  ->  db/ops/0004 + 0005  ->  preflight --phase after

and the result is checked: every row survives, the ledger is accepted by the runner, the preflight is all PASS, the runtime grants equal the
manifest, row-level security holds, the real API and a real worker path start on the converted database, and no job code is issued twice.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

import psycopg2
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import legacy_production_helper as LP  # noqa: E402
import realdb_helper as H  # noqa: E402
from auth.password import hash_password  # noqa: E402
from db.legacy import fingerprint as F  # noqa: E402
from db.legacy import lineage as L  # noqa: E402
from db.legacy import upgrade as U  # noqa: E402
from db.runner import runner  # noqa: E402
from db.runner.manifest import MANIFEST  # noqa: E402
from scripts import db_preflight as P  # noqa: E402
from scripts import legacy_upgrade as CLI  # noqa: E402
from services.requirements_v2 import contract as C  # noqa: E402
from test_privilege_justifications import _manifest, diff, expected_tuples, live_tuples  # noqa: E402

pytestmark = pytest.mark.skipif(H.unavailable_reason() is not None, reason=H.unavailable_reason() or "")

PASSWORD = "Pw-" + uuid.uuid4().hex          # the seeded tenant admin's API password, generated for this run
MIGRATOR_ROLE = "cv_migrator"


@pytest.fixture(scope="module")
def cluster():
    c = H.Cluster()
    try:
        yield c
    finally:
        c.stop()


@pytest.fixture()
def legacy(cluster):
    database = LP.build_legacy(cluster, mode="production")
    try:
        yield database
    finally:
        database.close()


def _connect(database, user="postgres", password=None):
    kwargs = dict(host="127.0.0.1", port=database.cluster.port, user=user, dbname=database.name,
                  options="-c search_path=cv_analyzer,public")
    if password:
        kwargs["password"] = password
    conn = psycopg2.connect(**kwargs)
    conn.set_client_encoding("UTF8")
    return conn


# ---------------------------------------------------------------------------------------------------------------------------------
# Representative data, across every table family that migrations 093 to 108 touched.
# ---------------------------------------------------------------------------------------------------------------------------------

def _requirements_document() -> dict:
    doc = C.empty_requirements()
    doc["categories"]["skills"] = {"weight": 100, "items": [
        C.make_item("Python", C.IMPORTANCE_REQUIRED, item_id="req_py", weight=100, origin=C.ORIGIN_STATED, source_text="Python")]}
    return doc


def seed(database) -> dict:
    conn = database.bootstrap()
    cur = conn.cursor()
    cur.execute("SET search_path = cv_analyzer, public")
    ids: dict = {}
    for label, domain in (("a", "acme.example"), ("b", "other.example")):
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"tenant {label}", domain))
        ids["tenant_" + label] = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role, status, must_change_password) "
                "VALUES (%s, 'admin@acme.example', %s, 'Admin A', 'admin', 'active', false) RETURNING user_id",
                (ids["tenant_a"], hash_password(PASSWORD)))
    ids["admin_a"] = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role, status, must_change_password) "
                "VALUES (%s, 'admin@other.example', %s, 'Admin B', 'admin', 'active', false) RETURNING user_id",
                (ids["tenant_b"], hash_password(PASSWORD)))
    ids["admin_b"] = str(cur.fetchone()[0])

    def job(tenant, creator, title, code):
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code) VALUES (%s, %s, %s, 'Python and SQL.', %s) "
                    "RETURNING job_id", (tenant, creator, title, code))
        return str(cur.fetchone()[0])

    ids["job_legacy"] = job(ids["tenant_a"], ids["admin_a"], "Legacy analysis job", "JOB-2026-0007")
    ids["job_v2"] = job(ids["tenant_a"], ids["admin_a"], "Requirements V2 job", "JOB-2026-0123")
    ids["job_b"] = job(ids["tenant_b"], ids["admin_b"], "Other tenant job", "JOB-2026-0045")
    cur.execute("INSERT INTO job_criteria (job_id, weight_skills, weight_experience, weight_education, weight_certifications, "
                "weight_soft_skills, weight_domain_knowledge, weight_other) VALUES (%s, 30, 25, 15, 10, 10, 5, 5)", (ids["job_legacy"],))
    cur.execute(
        "INSERT INTO job_criteria (job_id, analysis_json, original_analysis_json, requirements_schema_version, requirements_revision, "
        "requirements_retired_item_ids, requirements_extraction_token, requirements_extraction_started_at, criteria_extraction_status, "
        "weight_skills, weight_experience, weight_education, weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other) "
        "VALUES (%s, %s, %s, 2, 3, %s, gen_random_uuid(), now(), 'completed', 100, 0, 0, 0, 0, 0, 0)",
        (ids["job_v2"], json.dumps({"requirements": _requirements_document()}), json.dumps({"requirements": _requirements_document()}),
         json.dumps(["req_retired"])))
    cur.execute("INSERT INTO job_criteria (job_id, weight_skills, weight_experience, weight_education, weight_certifications, "
                "weight_soft_skills, weight_domain_knowledge, weight_other) VALUES (%s, 30, 25, 15, 10, 10, 5, 5)", (ids["job_b"],))

    def application(tenant, jobid, name, source, **extra):
        row = {"job_id": jobid, "tenant_id": tenant, "candidate_name": name, "submission_source": source,
               "processing_status": "ai_scored", "workflow_status": "awaiting_review", **extra}
        columns, values = list(row), list(row.values())
        cur.execute(f"INSERT INTO applications ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(values))}) RETURNING application_id",
                    values)
        return str(cur.fetchone()[0])

    ids["app_manual"] = application(ids["tenant_a"], ids["job_legacy"], "Sam Manual", "manual_upload", gender_value="male",
                                    gender_confidence=0.9)
    ids["app_bulk"] = application(ids["tenant_a"], ids["job_legacy"], "Bea Bulk", "bulk_excel_upload", gender_value="female",
                                  gender_confidence=0.5)
    ids["app_stuck"] = application(ids["tenant_a"], ids["job_legacy"], "Stu Stuck", "manual_upload", processing_status="queued",
                                   workflow_status=None)
    cur.execute("UPDATE applications SET queued_at = now() - interval '2 hours' WHERE application_id = %s", (ids["app_stuck"],))
    for app in (ids["app_manual"], ids["app_bulk"]):
        cur.execute(
            "INSERT INTO application_scores (application_id, score_skills, score_experience, score_education, score_certifications, "
            "score_soft_skills, score_domain_knowledge, score_other, final_score, weights_snapshot, ai_model, cv_facts_json, "
            "match_results_json, llm_match_results_json, det_final_score, det_score_json, scoring_method, scoring_provider) "
            "VALUES (%s, 70, 70, 70, 70, 70, 70, 70, 71, '{}', 'fixture-model', '{\"_schema\":\"cv_facts_v1\"}', "
            "'{\"_schema\":\"match_v1\"}', '{\"_schema\":\"llm_match_result_v1\"}', 68, '{\"_schema\":\"det_score_v1\"}', "
            "'deterministic_v1', 'deterministic')", (app,))
    cur.execute("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path) "
                "VALUES (%s, %s, 'cv.pdf', 'application/pdf', '/files/cv.pdf')", (ids["app_manual"], ids["tenant_a"]))
    cur.execute("INSERT INTO bulk_upload_batches (tenant_id, job_id, batch_status, row_count, imported_row_count, queued_row_count) "
                "VALUES (%s, %s, 'completed', 2, 2, 2) RETURNING batch_id", (ids["tenant_a"], ids["job_legacy"]))
    ids["batch"] = str(cur.fetchone()[0])
    cur.execute("INSERT INTO bulk_upload_rows (batch_id, tenant_id, job_id, row_number, application_id, import_status, "
                "processing_status, candidate_data) VALUES (%s, %s, %s, 1, %s, 'imported', 'completed', '{\"name\":\"Bea Bulk\"}')",
                (ids["batch"], ids["tenant_a"], ids["job_legacy"], ids["app_bulk"]))
    cur.execute("INSERT INTO tenant_features (tenant_id, feature_code, is_enabled) VALUES (%s, 'AI_SCORING', true), "
                "(%s, 'AI_BULK_UPLOAD', false)", (ids["tenant_a"], ids["tenant_a"]))
    cur.execute("INSERT INTO tenant_feature_audit (tenant_id, feature_code, old_value, new_value, changed_by) "
                "VALUES (%s, 'AI_BULK_UPLOAD', true, false, %s)", (ids["tenant_a"], ids["admin_a"]))
    cur.execute("INSERT INTO job_knockout_questions (job_id, tenant_id, question_text) VALUES (%s, %s, 'Do you hold a licence?') "
                "RETURNING question_id", (ids["job_legacy"], ids["tenant_a"]))
    question = cur.fetchone()[0]
    cur.execute("INSERT INTO application_knockout_answers (application_id, question_id, answer_value, tenant_id) "
                "VALUES (%s, %s, 'yes', %s)", (ids["app_manual"], question, ids["tenant_a"]))
    cur.execute("INSERT INTO candidate_comments (application_id, tenant_id, user_id, comment_text) VALUES (%s, %s, %s, 'Strong profile')",
                (ids["app_manual"], ids["tenant_a"], ids["admin_a"]))
    cur.execute("INSERT INTO tenant_workflow_policies (tenant_id, policies) VALUES (%s, '{}'::jsonb)", (ids["tenant_a"],))
    cur.execute("UPDATE system_config SET value = '0.65' WHERE key = 'scoring_v2.det_partial_credit'")
    conn.close()
    return ids


IGNORED_COLUMNS = {"tenant_workflow_policies": {"policies", "updated_at"}}      # 096 back-fills the SLA defaults, as the chain intended


def _tables(cur) -> list[str]:
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'cv_analyzer' AND tablename NOT IN %s ORDER BY 1",
                (tuple(F.OPERATOR_TABLES),))
    return [r[0] for r in cur.fetchall()]


def _rows(cur, table: str) -> list[dict]:
    cur.execute(f'SELECT to_jsonb(t) FROM cv_analyzer."{table}" t')
    return [r[0] for r in cur.fetchall()]


def snapshot_rows(database) -> dict[str, list[dict]]:
    conn = database.bootstrap()
    try:
        cur = conn.cursor()
        return {t: _rows(cur, t) for t in _tables(cur)}
    finally:
        conn.close()


def assert_rows_survive(before: dict[str, list[dict]], after: dict[str, list[dict]]) -> int:
    """Every row that existed before still exists after, with every value it had in a column that existed before. New columns and new
    rows are allowed. Returns the number of rows checked."""
    checked = 0
    for table, rows in before.items():
        assert table in after, f"table {table} disappeared"
        ignored = IGNORED_COLUMNS.get(table, set())
        keys = set(rows[0]) - ignored if rows else set()
        have = {json.dumps({k: r.get(k) for k in keys}, sort_keys=True, default=str) for r in after[table]}
        for row in rows:
            projected = json.dumps({k: row[k] for k in keys}, sort_keys=True, default=str)
            assert projected in have, f"a row of {table} changed or disappeared: {projected[:200]}"
            checked += 1
    return checked


# ---------------------------------------------------------------------------------------------------------------------------------
# The procedure
# ---------------------------------------------------------------------------------------------------------------------------------

def run_procedure(database, *, acknowledged=frozenset()):
    """0000, prepare, migrate, 0004 + 0005 -- as the operator runs them. Returns (prepare result, migrate report)."""
    H.provision_migration_roles(database)
    boot = database.bootstrap()
    try:
        result = U.prepare(boot, acknowledged=acknowledged)
    finally:
        boot.close()
    operator = _connect(database, "cv_migration_operator", database.runtime_passwords["cv_migration_operator"])
    try:
        report = runner.run(operator, owner_role=MIGRATOR_ROLE)
    finally:
        operator.close()
    H.provision_runtime_logins(database)
    return result, report


def preflight(database, phase="after"):
    conn = _connect(database)
    conn.autocommit = False
    try:
        conn.set_session(readonly=True)
        return P.run(conn, phase=phase)
    finally:
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------------------------------------------------------------
# 1. The reference and the fixtures
# ---------------------------------------------------------------------------------------------------------------------------------

def test_the_legacy_files_match_the_checksums_the_reference_records():
    recorded = {item["file"]: item["sha256"] for item in L.legacy_files()}
    on_disk = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((L.LEGACY_DIR / "migrations").glob("*.sql"))}
    assert recorded == on_disk
    assert sorted(on_disk) == [
        "093_scoring_v2_evidence_columns.sql", "094_llm_criteria_mapping_results.sql", "095_d01_soft_skill_repair.sql",
        "096_deterministic_scoring.sql", "097_d03_1_coverage_context.sql", "098_gender_metadata.sql", "099_bulk_upload_foundation.sql",
        "100_bulk_upload_phase2.sql", "101_bulk_upload_phase4.sql", "102_tenant_features.sql", "103_tenant_feature_audit.sql",
        "104_scoring_method.sql", "105_needs_verification.sql", "106_requirements_v2_safeguards.sql",
        "107_requirements_v2_revision.sql", "108_requirements_v2_extraction.sql"]
    assert hashlib.sha256((L.LEGACY_DIR / "schema.sql").read_bytes()).hexdigest() == L.REFERENCE["legacy_schema_sha256"]


def test_the_committed_reference_is_what_a_clean_build_produces():
    from scripts.generate_legacy_reference import generate
    fresh = json.loads(json.dumps(generate(), sort_keys=True, ensure_ascii=False))
    assert fresh == json.loads(json.dumps(L.REFERENCE, sort_keys=True, ensure_ascii=False)), \
        "db/legacy/reference.json is stale: python -m scripts.generate_legacy_reference > db/legacy/reference.json"


def test_the_legacy_schema_differs_from_this_schema_only_in_two_forward_foreign_keys():
    assert L.REFERENCE["schema_equivalence"] == {
        "different": [], "extra_in_legacy": [],
        "missing_in_legacy": ["constraint|subscription_plans.subscription_plans_updated_by_fkey",
                              "constraint|system_config.system_config_updated_by_fkey"]}


# ---------------------------------------------------------------------------------------------------------------------------------
# 2. The assessment
# ---------------------------------------------------------------------------------------------------------------------------------

def test_the_production_state_is_recognised_exactly_and_only_the_sla_repair_is_needed(legacy):
    seed(legacy)
    conn = legacy.bootstrap()
    try:
        a = U.assess(conn)
    finally:
        conn.close()
    assert not a.blocking, U.report_lines(a)
    assert a.repairs == ["096_repair_070_sla_policy_index.sql"]
    assert [e for e in a.production if e["disposition"] != "repair"] == [] and a.steps == []       # no production variation is needed here
    assert set(a.data) == set(L.DATA_CHECK_FILES) and all(n == 0 for n in a.data.values())
    for item in L.legacy_files():
        introduced = [k for k in item["set"] if L.SETTER_LEGACY.get(k) == item["file"]]
        assert a.verified_keys[item["file"]] == len(introduced), item["file"]


def test_cv_app_is_the_legacy_superuser_that_owns_everything(legacy):
    conn = legacy.bootstrap()
    cur = conn.cursor()
    cur.execute("SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = 'cv_app'")
    assert cur.fetchone() == (True, True, True)
    cur.execute("SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'cv_analyzer' AND c.relkind IN ('r','S')")
    assert [r[0] for r in cur.fetchall()] == ["cv_app"]
    cur.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('cv_worker','cv_admin','cv_secrets','cv_migrator')")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT to_regclass('cv_analyzer.schema_migrations')")
    assert cur.fetchone()[0] is None
    conn.close()


# ---------------------------------------------------------------------------------------------------------------------------------
# 3. The conversion, end to end
# ---------------------------------------------------------------------------------------------------------------------------------

def test_the_legacy_database_converts_keeps_every_row_and_passes_the_preflight(legacy):
    ids = seed(legacy)
    before = snapshot_rows(legacy)
    assert sum(len(v) for v in before.values()) > 40

    result, report = run_procedure(legacy)
    assert result.pending == U.EXPECTED_PENDING_AFTER_PREPARE
    assert report.applied == U.EXPECTED_PENDING_AFTER_PREPARE

    # every row survives with every value it had
    after = snapshot_rows(legacy)
    assert assert_rows_survive(before, after) > 40
    for table in ("bulk_upload_batches", "bulk_upload_rows", "tenant_features", "tenant_feature_audit", "application_scores"):
        assert len(after[table]) >= len(before[table]) > 0
    # the operator-tuned configuration value is untouched, and the SLA defaults 096 back-fills are present
    assert [r["value"] for r in after["system_config"] if r["key"] == "scoring_v2.det_partial_credit"] == ["0.65"]
    assert all("sla_thresholds" in r["policies"] for r in after["tenant_workflow_policies"])

    # the preflight is all PASS
    checks = preflight(legacy)
    assert [c for c in checks if c.status != P.PASS] == []
    assert P._exit_code(checks) == 0
    names = {c.name for c in checks}
    assert "legacy lineage: every legacy migration is recorded with its source checksum" in names

    # the ledger is accepted by the runner: nothing pending, nothing to apply, every row checksummed against the repository
    operator = _connect(legacy, "cv_migration_operator", legacy.runtime_passwords["cv_migration_operator"])
    try:
        assert runner.run(operator, dry_run=True, owner_role=MIGRATOR_ROLE).pending() == []
        assert runner.run(operator, owner_role=MIGRATOR_ROLE).applied == []
    finally:
        operator.close()
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("SELECT filename, sha256, status, replaced_by FROM cv_analyzer.schema_migrations")
    rows = {r[0]: r for r in cur.fetchall()}
    assert set(rows) == {e.file for e in MANIFEST}
    for entry in MANIFEST:
        assert rows[entry.file][1] == hashlib.sha256((H.DB_DIR / "migrations" / entry.file).read_bytes()).hexdigest()
        assert rows[entry.file][2] == ("superseded" if entry.superseded_by else "applied")
    assert {f: rows[f][3] for f in L.SUPERSEDED_ORIGINALS} == L.SUPERSEDED_ORIGINALS

    # the evidence table records the lineage: sixteen legacy files with their source checksums, how each base file was proved
    cur.execute("SELECT item_kind, item, legacy_sha256, main_sha256, proof, detail FROM cv_analyzer.schema_migration_legacy_evidence")
    evidence = cur.fetchall()
    legacy_rows = {r[1]: r[2] for r in evidence if r[0] == "legacy_migration"}
    assert legacy_rows == {i["file"]: i["sha256"] for i in L.legacy_files()}
    proofs = {r[1]: r[4] for r in evidence if r[0] == "baselined_migration"}
    assert proofs[L.BRIDGE_RUN] == "ran" and proofs[L.BRIDGE_RECONCILED] == "catalog"
    assert [f for f, p in proofs.items() if p == "attested"] == ["005_job_code_alias.sql"]
    assert proofs["059_rename_scored_to_ai_scored.sql"] in ("data", "catalog+data")
    assert {r[1] for r in evidence if r[0] == "repair_run"} == {"096_repair_070_sla_policy_index.sql"}
    assert {r[1] for r in evidence if r[0] == "reconciliation"} == {s.name for s in L.RECONCILIATIONS}
    assert all(r[3] is None or len(r[3]) == 64 for r in evidence)
    boot.close()

    # no phrase of a credential is stored anywhere in the evidence
    blob = json.dumps([[str(c) for c in r] for r in evidence])
    assert PASSWORD not in blob and legacy.cv_app_password not in blob and "CHANGE_ME" not in blob
    del ids


def test_runtime_roles_are_hardened_and_the_grants_equal_the_manifest(legacy):
    seed(legacy)
    run_procedure(legacy)
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, rolcreaterole, rolinherit FROM pg_roles "
                "WHERE rolname LIKE 'cv\\_%' ORDER BY rolname")
    roles = {r[0]: r for r in cur.fetchall()}
    assert set(roles) == {"cv_app", "cv_worker", "cv_admin", "cv_secrets", "cv_migrator", "cv_migration_operator"}
    for name in ("cv_app", "cv_worker", "cv_admin", "cv_secrets"):
        assert roles[name][1:] == (False, False, True, False, False, False), name        # login only; nothing else
    assert roles["cv_migrator"][1:4] == (False, False, False) and roles["cv_migration_operator"][1:4] == (False, False, True)
    # nothing in cv_analyzer is owned by a runtime role, nor is the database or schema public
    cur.execute("SELECT pg_get_userbyid(relowner), count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'cv_analyzer' AND c.relkind IN ('r','S','v','m') GROUP BY 1")
    assert dict(cur.fetchall()) .keys() == {MIGRATOR_ROLE}
    cur.execute("SELECT pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'cv_analyzer'")
    assert {r[0] for r in cur.fetchall()} == {MIGRATOR_ROLE}
    boot.close()
    # the live grants equal the privilege manifest in both directions, and the legacy-only tables hold none
    conn = psycopg2.connect(host="127.0.0.1", port=legacy.cluster.port, user="postgres", dbname=legacy.name)
    undeclared, missing = diff(expected_tuples(_manifest()), live_tuples(conn))
    assert not undeclared and not missing, (undeclared, missing)
    cur = conn.cursor()
    for table in list(L.LEGACY_PRESERVED_TABLES) + list(F.OPERATOR_TABLES):
        for role in ("cv_app", "cv_worker", "cv_admin", "cv_secrets"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"cv_analyzer.{table}", privilege))
                assert cur.fetchone()[0] is False, (role, privilege, table)
    conn.close()


def test_row_level_security_holds_on_the_converted_database(legacy):
    ids = seed(legacy)
    run_procedure(legacy)
    app = _connect(legacy, "cv_app", legacy.runtime_passwords["cv_app"])
    cur = app.cursor()
    cur.execute("SHOW is_superuser")
    assert cur.fetchone()[0] == "off"
    cur.execute("SELECT count(*) FROM jobs")          # no tenant context: fails closed
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT set_config('app.current_tenant_id', %s, false)", (ids["tenant_a"],))
    cur.execute("SELECT job_code FROM jobs ORDER BY job_code")
    assert [r[0] for r in cur.fetchall()] == ["JOB-2026-0007", "JOB-2026-0123"]
    # a forged role variable grants nothing
    cur.execute("SELECT set_config('app.current_role', 'super_admin', false)")
    cur.execute("SELECT set_config('app.current_tenant_id', %s, false)", (ids["tenant_b"],))
    cur.execute("SELECT job_code FROM jobs")
    assert [r[0] for r in cur.fetchall()] == ["JOB-2026-0045"]
    # the legacy-only tables are sealed from the tenant login, and the ledger and evidence are too
    app.close()
    for table in list(L.LEGACY_PRESERVED_TABLES) + list(F.OPERATOR_TABLES):
        sealed = _connect(legacy, "cv_app", legacy.runtime_passwords["cv_app"])       # a new session: no tenant context
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            sealed.cursor().execute(f"SELECT 1 FROM cv_analyzer.{table}")
        sealed.close()
    # the legacy-only tables keep row-level security, and no policy reads the forgeable role variable
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'cv_analyzer' "
                "AND relname = ANY(%s) AND NOT relrowsecurity", (list(L.LEGACY_PRESERVED_TABLES),))
    assert cur.fetchall() == []
    cur.execute("SELECT count(*) FROM pg_policies WHERE schemaname = 'cv_analyzer' AND (qual LIKE '%app.current_role%' "
                "OR with_check LIKE '%app.current_role%')")
    assert cur.fetchone()[0] == 0
    # cv_admin sees both tenants' tenant_features through its role; a worker has no access to them
    boot.close()
    admin = _connect(legacy, "cv_admin", legacy.runtime_passwords["cv_admin"])
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        admin.cursor().execute("SELECT 1 FROM cv_analyzer.tenant_features")
    admin.close()


def test_the_job_code_sequence_never_issues_an_existing_code(legacy):
    seed(legacy)
    run_procedure(legacy)
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("SELECT max(substring(job_code FROM '([0-9]+)$')::bigint) FROM cv_analyzer.jobs")
    highest = cur.fetchone()[0]
    assert highest == 123
    cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
    assert cur.fetchone()[0] == highest + 1
    boot.close()


def test_a_prepare_dry_run_changes_nothing(legacy):
    seed(legacy)
    H.provision_migration_roles(legacy)
    boot = legacy.bootstrap()
    before = F.snapshot(boot.cursor())
    result = U.prepare(boot, dry_run=True)
    assert result.pending == U.EXPECTED_PENDING_AFTER_PREPARE and result.ledger_rows > 80
    boot.close()
    boot = legacy.bootstrap()
    cur = boot.cursor()
    assert F.snapshot(cur) == before
    cur.execute("SELECT to_regclass('cv_analyzer.schema_migrations'), (SELECT count(*) FROM pg_roles WHERE rolname = 'cv_worker')")
    assert cur.fetchone() == (None, 0)
    cur.execute("SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'cv_analyzer' AND c.relkind = 'r'")
    assert [r[0] for r in cur.fetchall()] == ["cv_app"]
    boot.close()


def test_a_second_conversion_is_refused(legacy):
    seed(legacy)
    run_procedure(legacy)
    boot = legacy.bootstrap()
    with pytest.raises(U.ConversionError, match="not in the exact legacy state"):
        U.prepare(boot)
    boot.close()


def _failing(checks):
    return {c.name for c in checks if c.status == P.FAIL}


def test_the_preflight_fails_when_a_converted_database_is_tampered_with(legacy):
    seed(legacy)
    run_procedure(legacy)
    assert _failing(preflight(legacy)) == set()
    boot = legacy.bootstrap()
    cur = boot.cursor()
    # a runtime grant on a legacy-only table
    cur.execute("GRANT SELECT ON cv_analyzer.bulk_upload_rows TO cv_app")
    assert "table privileges hold no grant beyond the matrix" in _failing(preflight(legacy))
    cur.execute("REVOKE SELECT ON cv_analyzer.bulk_upload_rows FROM cv_app")
    # row-level security switched off on a legacy-only table
    cur.execute("ALTER TABLE cv_analyzer.bulk_upload_rows DISABLE ROW LEVEL SECURITY")
    assert "legacy-only tables keep row-level security enabled" in _failing(preflight(legacy))
    cur.execute("ALTER TABLE cv_analyzer.bulk_upload_rows ENABLE ROW LEVEL SECURITY")
    # a legacy checksum in the evidence that is not the committed one
    cur.execute("UPDATE cv_analyzer.schema_migration_legacy_evidence SET legacy_sha256 = repeat('0', 64) "
                "WHERE item_kind = 'legacy_migration' AND item = '093_scoring_v2_evidence_columns.sql'")
    assert "legacy lineage: every legacy migration is recorded with its source checksum" in _failing(preflight(legacy))
    cur.execute("UPDATE cv_analyzer.schema_migration_legacy_evidence SET legacy_sha256 = %s WHERE item_kind = 'legacy_migration' "
                "AND item = '093_scoring_v2_evidence_columns.sql'", (L.legacy_files()[0]["sha256"],))
    # a ledger checksum that no longer matches the evidence
    cur.execute("UPDATE cv_analyzer.schema_migrations SET sha256 = repeat('1', 64) WHERE filename = '059_rename_scored_to_ai_scored.sql'")
    failing = _failing(preflight(legacy))
    assert "applied migrations match their checksums" in failing and "legacy lineage: baselined migrations match their ledger rows" in failing
    # a table the matrix does not know
    cur.execute("CREATE TABLE cv_analyzer.stray_table (id int)")
    assert "table stray_table has a matrix entry" in _failing(preflight(legacy))
    boot.close()


def test_a_database_that_was_not_converted_does_not_get_the_legacy_allowances(cluster):
    """A legacy-only table name does not pass the preflight on a database without the conversion's evidence."""
    with H.built_chain() as fresh:
        H.check_chain(fresh.failures)
        fresh.conn.cursor().execute("CREATE TABLE cv_analyzer.bulk_upload_rows (row_id uuid PRIMARY KEY)")
        checks = preflight(fresh)
    assert "table bulk_upload_rows has a matrix entry" in _failing(checks)


# ---------------------------------------------------------------------------------------------------------------------------------
# 4. Fail-closed: what the conversion refuses, and that a refusal changes nothing
# ---------------------------------------------------------------------------------------------------------------------------------

def _unchanged_after_refusal(database, before_snapshot):
    conn = database.bootstrap()
    cur = conn.cursor()
    assert F.snapshot(cur) == before_snapshot
    cur.execute("SELECT to_regclass('cv_analyzer.schema_migrations'), (SELECT count(*) FROM pg_roles WHERE rolname = 'cv_worker')")
    assert cur.fetchone() == (None, 0)
    cur.execute("SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'cv_analyzer' AND c.relkind = 'r'")
    assert [r[0] for r in cur.fetchall()] == ["cv_app"]
    conn.close()


def test_a_clean_chain_remnant_is_refused_and_nothing_changes(cluster):
    """The chain exactly as the files produce it: 010, 024, 070 and 087 failed. Their effects are absent, and are not repaired out of
    order (095 and 097 validate against the state at their own position)."""
    database = LP.build_legacy(cluster, mode="clean")
    try:
        assert set(database.failures) == {"010_platform_control.sql", "024_plan_features_seed.sql", "070_sla_thresholds_config.sql",
                                          "087_autosave_deterministic_answers.sql"}
        seed_ok = database.bootstrap()
        before = F.snapshot(seed_ok.cursor())
        seed_ok.close()
        H.provision_migration_roles(database)
        boot = database.bootstrap()
        a = U.assess(boot)
        assert a.blocking
        text = "\n".join(U.report_lines(a))
        assert "application_knockout_answers.tenant_id" in text and "seed|subscription_plans|starter" in text
        with pytest.raises(U.ConversionError, match="not in the exact legacy state"):
            U.prepare(boot)
        boot.close()
        _unchanged_after_refusal(database, before)
    finally:
        database.close()


def test_an_unknown_table_stops_the_conversion(legacy):
    seed(legacy)
    boot = legacy.bootstrap()
    boot.cursor().execute("CREATE TABLE cv_analyzer.cv_score_events (event_id uuid PRIMARY KEY)")
    before = F.snapshot(boot.cursor())
    H.provision_migration_roles(legacy)
    a = U.assess(boot)                       # the assessment itself names it, before the conversion's own postcondition could
    assert a.blocking and any(n == "unknown object" and "cv_score_events" in d for _, n, d in a.findings)
    with pytest.raises(U.ConversionError, match="cv_score_events"):
        U.prepare(boot)
    boot.close()
    boot = legacy.bootstrap()
    assert F.snapshot(boot.cursor()) == before
    boot.close()


def test_a_missing_legacy_column_stops_the_conversion(legacy):
    seed(legacy)
    boot = legacy.bootstrap()
    boot.cursor().execute("ALTER TABLE cv_analyzer.application_scores DROP COLUMN det_final_score")
    boot.close()
    boot = legacy.bootstrap()
    a = U.assess(boot)
    assert a.blocking and any("det_final_score" in d for _, _, d in a.findings)
    boot.close()


def test_a_changed_constraint_stops_the_conversion(legacy):
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("ALTER TABLE cv_analyzer.applications DROP CONSTRAINT applications_decision_check")
    cur.execute("ALTER TABLE cv_analyzer.applications ADD CONSTRAINT applications_decision_check "
                "CHECK (decision IN ('qualified', 'partial', 'rejected'))")
    a = U.assess(boot)
    assert a.blocking and any("applications_decision_check" in d for _, _, d in a.findings)
    boot.close()


def test_a_data_correction_that_did_not_hold_stops_unless_acknowledged_and_the_acknowledgement_is_recorded(legacy):
    seed(legacy)
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("UPDATE cv_analyzer.applications SET workflow_status = NULL WHERE processing_status = 'ai_scored'")
    before = F.snapshot(cur)
    H.provision_migration_roles(legacy)
    a = U.assess(boot)
    assert a.blocking and a.data["091_fix_null_workflow_status_ai_scored.sql"] == 2
    with pytest.raises(U.ConversionError):
        U.prepare(boot)
    boot.close()
    boot = legacy.bootstrap()
    assert F.snapshot(boot.cursor()) == before
    result = U.prepare(boot, acknowledged=frozenset({"091_fix_null_workflow_status_ai_scored.sql"}))
    assert result.pending == U.EXPECTED_PENDING_AFTER_PREPARE
    cur = boot.cursor()
    cur.execute("SELECT detail FROM cv_analyzer.schema_migration_legacy_evidence WHERE item_kind = 'conversion'")
    assert cur.fetchone()[0]["acknowledged"] == ["091_fix_null_workflow_status_ai_scored.sql"]
    boot.close()


def test_a_seed_row_an_operator_deleted_stops_unless_it_is_named_and_the_acceptance_is_recorded(legacy):
    seed(legacy)
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("DELETE FROM cv_analyzer.ai_prompts WHERE prompt_code = 'recruitment.criteria_mapping' AND version = 1")
    key = "seed|ai_prompts|recruitment.criteria_mapping|1"
    a = U.assess(boot)
    assert a.blocking and any(key in d for _, _, d in a.findings)
    H.provision_migration_roles(legacy)
    with pytest.raises(U.ConversionError):
        U.prepare(boot)
    result = U.prepare(boot, accepted_seeds=frozenset({key}))
    assert result.pending == U.EXPECTED_PENDING_AFTER_PREPARE
    cur.execute("SELECT detail FROM cv_analyzer.schema_migration_legacy_evidence WHERE item_kind = 'conversion'")
    assert cur.fetchone()[0]["accepted_missing_seeds"] == [key]
    boot.close()


def test_open_runtime_sessions_stop_the_conversion(legacy):
    seed(legacy)
    H.provision_migration_roles(legacy)
    live = psycopg2.connect(host="127.0.0.1", port=legacy.cluster.port, user="cv_app", password=legacy.cv_app_password, dbname=legacy.name)
    boot = legacy.bootstrap()
    try:
        with pytest.raises(U.ConversionError, match="open session"):
            U.prepare(boot)
    finally:
        live.close()
        boot.close()


def test_a_runtime_role_is_refused_as_the_bootstrap_login(legacy):
    seed(legacy)
    H.provision_migration_roles(legacy)
    conn = psycopg2.connect(host="127.0.0.1", port=legacy.cluster.port, user="cv_app", password=legacy.cv_app_password, dbname=legacy.name)
    try:
        with pytest.raises(U.ConversionError, match="runtime role"):
            U.prepare(conn)
    finally:
        conn.close()


def test_without_the_migration_roles_the_conversion_stops(legacy):
    boot = legacy.bootstrap()
    with pytest.raises(U.ConversionError, match="cv_migrator"):
        U.prepare(boot)
    boot.close()


def test_the_complete_report_lists_every_difference_with_both_definitions_and_no_row_content(legacy, capsys, monkeypatch):
    """`check --json` is the document a production difference is classified from. It must not be capped, must carry the live and the expected
    definition of every object, must describe data violations by status-like columns only, and must hold no personal data."""
    seed(legacy)
    boot = legacy.bootstrap()
    cur = boot.cursor()
    for n in range(45):                                                       # more unknown columns than the text report prints
        cur.execute(f"ALTER TABLE cv_analyzer.jobs ADD COLUMN extra_{n:02d} text")
    cur.execute("ALTER TABLE cv_analyzer.applications DROP CONSTRAINT applications_decision_check")
    cur.execute("ALTER TABLE cv_analyzer.applications ADD CONSTRAINT applications_decision_check CHECK (decision IN ('qualified', 'partial', 'rejected'))")
    cur.execute("ALTER TABLE cv_analyzer.application_scores DROP COLUMN det_final_score")
    cur.execute("UPDATE cv_analyzer.applications SET workflow_status = NULL WHERE processing_status = 'ai_scored'")
    boot.close()
    monkeypatch.setenv("LEGACY_UPGRADE_DATABASE_URL", f"postgresql://postgres@127.0.0.1:{legacy.cluster.port}/{legacy.name}")
    assert CLI.main(["check", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    structure = report["structure"]
    assert len(structure["unknown"]) == 45 and all(u["key"].startswith("column|jobs.extra_") and u["live"] for u in structure["unknown"])
    different = {d["key"]: d for d in structure["different"]}
    assert "ANY" in different["constraint|applications.applications_decision_check"]["live"]
    assert "needs_verification" in different["constraint|applications.applications_decision_check"]["expected"]
    assert "needs_verification" not in different["constraint|applications.applications_decision_check"]["live"]
    missing = {m["key"]: m for m in structure["missing"]}
    assert missing["column|application_scores.det_final_score"]["set_by"] == "096_deterministic_scoring.sql"
    assert "index|idx_workflow_policies_sla" in missing                      # listed too, though it is the repairable one
    assert report["data"]["091_fix_null_workflow_status_ai_scored.sql"] == 2
    assert report["data_breakdown"]["091_fix_null_workflow_status_ai_scored.sql"] == [["manual_upload", 1], ["bulk_excel_upload", 1]] or \
        sorted(report["data_breakdown"]["091_fix_null_workflow_status_ai_scored.sql"]) == [["bulk_excel_upload", 1], ["manual_upload", 1]]
    text = json.dumps(report)
    for personal in ("Sam Manual", "Bea Bulk", "Strong profile", "admin@acme.example", "Do you hold a licence", "Python and SQL", "cv.pdf"):
        assert personal not in text
    # the text report says how many it did not print, and where the rest is
    assert CLI.main(["check"]) == 1
    printed = capsys.readouterr().out
    assert "and 5 more (the complete list is in `check --json`)" in printed


# ---------------------------------------------------------------------------------------------------------------------------------
# 5. Why each reconciliation exists: the unreconciled chain fails, so the failure is real and not assumed
# ---------------------------------------------------------------------------------------------------------------------------------

def test_main_migration_100_stops_on_the_legacy_tenant_features_policy_without_the_reconciliation(legacy, monkeypatch):
    seed(legacy)
    monkeypatch.setattr(L, "RECONCILIATIONS", tuple(s for s in L.RECONCILIATIONS if s.name != "tenant_features_admin_policy"))
    H.provision_migration_roles(legacy)
    boot = legacy.bootstrap()
    U.prepare(boot)
    boot.close()
    operator = _connect(legacy, "cv_migration_operator", legacy.runtime_passwords["cv_migration_operator"])
    try:
        with pytest.raises(runner.MigrationError, match="100_runtime_rls_identity"):
            runner.run(operator, owner_role=MIGRATOR_ROLE)
    finally:
        operator.close()
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("SELECT filename FROM cv_analyzer.schema_migrations WHERE filename >= '098' AND filename < '1' ORDER BY 1")
    assert [r[0] for r in cur.fetchall()] == ["098_application_scores_requirements_revision.sql", "099_platform_secret_accessors.sql"]
    boot.close()


def test_main_migration_094_as_written_fails_when_a_bulk_application_exists(legacy):
    seed(legacy)
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("BEGIN")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute((H.DB_DIR / "migrations" / L.BRIDGE_RECONCILED).read_text(encoding="utf-8").replace("BEGIN;", "").replace("COMMIT;", ""))
    boot.rollback()
    boot.close()


def test_the_reconciled_submission_source_check_keeps_bulk_rows_and_allows_copy_to_job(legacy):
    seed(legacy)
    run_procedure(legacy)
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'applications_submission_source_check'")
    definition = cur.fetchone()[0]
    for value in ("manual_upload", "email_forwarding", "platform_email", "public_apply", "candidate_reuse", "bulk_excel_upload"):
        assert value in definition
    cur.execute("SELECT column_default FROM information_schema.columns WHERE table_schema = 'cv_analyzer' AND table_name = 'applications' "
                "AND column_name = 'submission_source'")
    assert cur.fetchone()[0] is None
    cur.execute("SELECT count(*) FROM cv_analyzer.applications WHERE submission_source = 'bulk_excel_upload'")
    assert cur.fetchone()[0] == 1
    boot.close()


# ---------------------------------------------------------------------------------------------------------------------------------
# 6. The final structure: this release's own chain plus exactly the legacy additions
# ---------------------------------------------------------------------------------------------------------------------------------

def test_the_converted_catalogue_is_this_releases_catalogue_plus_the_legacy_additions(legacy):
    seed(legacy)
    run_procedure(legacy)
    boot = legacy.bootstrap()
    converted = F.snapshot(boot.cursor())
    boot.close()
    with H.built_chain() as fresh:           # a fresh install of this release, on its own cluster (roles are cluster-wide)
        H.check_chain(fresh.failures)
        fresh_snapshot = F.snapshot(fresh.conn.cursor())
    missing, different, extra = F.diff(fresh_snapshot, converted)
    assert missing == [], f"the converted database lacks objects a fresh install of this release has: {missing[:10]}"
    # Everything that differs or is extra must trace to a legacy migration or a declared reconciliation.
    legacy_keys = set(L.EXPECTED_LEGACY) - set(L.EXPECTED_BASE) | {k for f in L.legacy_files() for k in f["set"]}
    declared = {k for s in L.RECONCILIATIONS for k in s.changes}
    unexplained = [k for k in different + extra if not k.startswith("seed|") and k not in legacy_keys and k not in declared]
    assert unexplained == [], unexplained[:10]


# ---------------------------------------------------------------------------------------------------------------------------------
# 7. The new API and a worker path run on the converted database
# ---------------------------------------------------------------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _url(database, role, driver="postgresql+asyncpg"):
    return f"{driver}://{role}:{database.runtime_passwords[role]}@127.0.0.1:{database.cluster.port}/{database.name}"


def _runtime_env(database) -> dict:
    return {**os.environ, "DATABASE_URL": _url(database, "cv_app"), "ADMIN_DATABASE_URL": _url(database, "cv_admin"),
            "WORKER_DATABASE_URL": _url(database, "cv_worker"), "SECRETS_DATABASE_URL": _url(database, "cv_secrets"),
            "DB_SCHEMA": "cv_analyzer", "JWT_SECRET": uuid.uuid4().hex + uuid.uuid4().hex,
            "PYTHONPATH": os.pathsep.join([str(BACKEND), str(REPO)])}


def _http(method, url, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def test_the_new_api_starts_on_the_converted_database_and_serves_the_legacy_data(legacy, tmp_path):
    ids = seed(legacy)
    run_procedure(legacy)
    port = _free_port()
    log = (tmp_path / "api.log").open("w")
    process = subprocess.Popen(
        [sys.executable, str(REPO / "e2e" / "support" / "backend_app.py"), "--port", str(port), "--origin", "http://127.0.0.1:1",
         "--files-root", str(tmp_path)], env=_runtime_env(legacy), cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 120
        while True:
            assert process.poll() is None, (tmp_path / "api.log").read_text()[-2000:]
            try:
                if _http("GET", base + "/health")[0] == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            assert time.time() < deadline, "the API did not start"
            time.sleep(0.5)
        # the lifespan refused nothing: both pools are non-superuser logins. Now use it as the tenant admin of the legacy tenant.
        status, login = _http("POST", base + "/auth/login", body={"email": "admin@acme.example", "password": PASSWORD})
        assert status == 200, login
        token = login["token"]
        status, jobs = _http("GET", base + "/jobs", token)
        assert status == 200
        listing = jobs if isinstance(jobs, list) else jobs.get("jobs") or jobs.get("data") or []
        codes = {j.get("job_code") for j in listing}
        assert {"JOB-2026-0007", "JOB-2026-0123"} <= codes and "JOB-2026-0045" not in codes      # tenant scoping holds
        status, doc = _http("GET", base + f"/jobs/{ids['job_v2']}/requirements", token)
        assert status == 200 and doc["requirements_revision"] == 3 and doc["schema_version"] == 2   # Requirements V2 data is served
        status, created = _http("POST", base + "/jobs", token, {"title": "Created after the upgrade", "description": (
            "Senior Python developer. Responsibilities: build reporting APIs and PostgreSQL schemas for finance teams. "
            "Requirements: five years of backend experience, strong SQL, Python and Docker skills, a bachelor's degree.")})
        assert status == 201, created
        status, listing_after = _http("GET", base + "/jobs", token)
        after = listing_after if isinstance(listing_after, list) else listing_after.get("jobs") or listing_after.get("data") or []
        new_codes = sorted({j["job_code"] for j in after} - codes)
        assert len(new_codes) == 1                                                                    # exactly the job just created
        assert int(new_codes[0].rsplit("-", 1)[1]) > 123                                               # above every code issued before
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


def test_a_worker_path_runs_on_the_worker_login_of_the_converted_database(legacy, tmp_path):
    ids = seed(legacy)
    run_procedure(legacy)
    script = (
        "import asyncio, sys\n"
        "from workers.celery_app import celery_app\n"
        "import importlib\n"
        "for module in celery_app.conf.include:\n"                    # every worker module the Celery app lists imports cleanly
        "    importlib.import_module(module)\n"
        "assert 'workers.watchdog.reset_stuck_scoring_tasks' in celery_app.tasks\n"
        "assert 'workers.cv_score.score_cv_task' in celery_app.tasks\n"
        "from workers.watchdog import _reset_stuck_async\n"
        "from database import worker_engine, assert_runtime_role\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "async def go():\n"
        "    async with AsyncSession(worker_engine) as s:\n"          # the same startup check the API makes
        "        await assert_runtime_role(s)\n"
        "    return await _reset_stuck_async()\n"
        "print('stuck=%d' % asyncio.run(go()))\n"
    )
    done = subprocess.run([sys.executable, "-c", script], env=_runtime_env(legacy), cwd=BACKEND, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    assert "stuck=1" in done.stdout
    boot = legacy.bootstrap()
    cur = boot.cursor()
    cur.execute("SELECT processing_status, stopped_reason FROM cv_analyzer.applications WHERE application_id = %s", (ids["app_stuck"],))
    assert cur.fetchone() == ("failed", "processing_error")
    boot.close()


# ---------------------------------------------------------------------------------------------------------------------------------
# 8. The command line
# ---------------------------------------------------------------------------------------------------------------------------------

def test_the_command_line_reports_and_never_prints_the_connection_password(legacy, capsys, monkeypatch):
    seed(legacy)
    secret = "pa55-" + uuid.uuid4().hex
    monkeypatch.setenv("LEGACY_UPGRADE_DATABASE_URL", f"postgresql://postgres:{secret}@127.0.0.1:1/none")
    assert CLI.main(["check"]) == 2
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    monkeypatch.setenv("LEGACY_UPGRADE_DATABASE_URL", f"postgresql://postgres@127.0.0.1:{legacy.cluster.port}/{legacy.name}")
    assert CLI.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "REPAIR  096_repair_070_sla_policy_index.sql" in out and "FAIL" not in out
    assert CLI.main(["prepare", "--dry-run"]) == 1          # the migration roles do not exist yet: stopped, nothing written
    assert "cv_migrator" in capsys.readouterr().err
    assert CLI.main(["check", "--acknowledge-data", "no_such_file.sql"]) == 2
    assert CLI.main(["check", "--acknowledge-missing-seed", "column|jobs.title"]) == 2
    monkeypatch.delenv("LEGACY_UPGRADE_DATABASE_URL")
    assert CLI.main(["check"]) == 2
