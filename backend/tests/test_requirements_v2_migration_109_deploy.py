"""Deployment check of migration 109 (re-analysis history) on a REAL PostgreSQL 16: an isolated database migrated through 108 with existing
records, then 109 applied the way production applies it (as the owner). Verifies: existing rows are unchanged; the switch defaults to 'false';
re-applying 109 is harmless; the application role (cv_app, NOT a superuser and NOT the owner) can read and append the history but cannot update,
delete or truncate it, nor change its structure; and the rollback path that is allowed while the history is empty.

The clean build through 108 has four known failures in the stand-in schema (010, 024, 070, 087; see KNOWN_BUILD_FAILURES). Any other failure fails
this module, so a new break in the chain is never hidden.
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import realdb_helper as rh  # noqa: E402

MIGRATIONS = pathlib.Path(rh.DB_DIR) / "migrations"
M109 = MIGRATIONS / "109_requirements_analysis_recovery.sql"
SWITCH = "requirements_v2.recovery_enabled"
KNOWN_BUILD_FAILURES = ("010_platform_control.sql", "024_plan_features_seed.sql", "070_sla_thresholds_config.sql",
                        "087_autosave_deterministic_answers.sql")
pytestmark = pytest.mark.skipif(rh.available() is not None, reason=rh.available() or "")


@pytest.fixture(scope="module")
def cluster():
    c = rh.Cluster()
    yield c
    c.stop()


@pytest.fixture
def through_108(cluster):
    d = cluster.build(upto="108_requirements_v2_extraction.sql")
    unknown = [f for f in d.failed if not any(f.startswith(k) for k in KNOWN_BUILD_FAILURES)]
    assert unknown == [], unknown
    yield d
    d.drop()


def _seed_existing(d):
    """Records that exist before 109: tenant, users, a legacy job, a v2 job with its analysis, an audit row."""
    cur = d.conn.cursor()
    cur.execute("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
                "('00000000-0000-0000-0000-0000000000a1','Tenant One','t1.example','active','active','organization')")
    cur.execute("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES "
                "('00000000-0000-0000-0000-0000000000b1','00000000-0000-0000-0000-0000000000a1','admin@t1.example','x','Admin','admin','active')")
    analysis = {"requirements": {"schema_version": 2, "categories": {}}, "keep": {"me": True}}
    for jid, title in (("00000000-0000-0000-0000-0000000000c1", "Legacy Analyst"), ("00000000-0000-0000-0000-0000000000c2", "Backend Developer")):
        cur.execute("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (%s,'00000000-0000-0000-0000-0000000000a1',"
                    "'00000000-0000-0000-0000-0000000000b1',%s,'Synthetic description')", (jid, title))
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, analysis_json, requirements_schema_version) "
                "VALUES ('00000000-0000-0000-0000-0000000000c1','completed',%s,NULL)", (json.dumps({"skills": {"required": ["Excel"]}}),))
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, analysis_json, requirements_schema_version) "
                "VALUES ('00000000-0000-0000-0000-0000000000c2','completed',%s,2)", (json.dumps(analysis),))
    cur.execute("INSERT INTO audit_logs (tenant_id, user_id, action, resource_type, resource_id, details) VALUES "
                "('00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000b1','test_seed','job','c2','{}')")


def _fingerprint(d):
    """A digest of every existing row that 109 must not touch (the switch table is compared separately)."""
    cur = d.conn.cursor()
    out = {}
    for name, sql in (("jobs", "SELECT row_to_json(j)::text FROM jobs j ORDER BY job_id"),
                      ("job_criteria", "SELECT row_to_json(c)::text FROM job_criteria c ORDER BY job_id"),
                      ("audit_logs", "SELECT row_to_json(a)::text FROM audit_logs a ORDER BY log_id")):
        cur.execute(sql)
        out[name] = [r[0] for r in cur.fetchall()]
    return out


def _apply_109(d):
    d.conn.cursor().execute(M109.read_text(encoding="utf-8"))


def _app_connection(d):
    import psycopg2
    conn = psycopg2.connect(host="127.0.0.1", port=d.cluster.port, user="cv_app", dbname=d.name)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET search_path = cv_analyzer")
    return conn


def _owner_grants_like_production(d):
    """Production's grants for the application role are issued by schema.sql (ALL TABLES, at the time of its run). The history table is created later,
    so only 109's own grants can reach it. This reproduces that order exactly."""
    d.conn.cursor().execute("GRANT USAGE ON SCHEMA cv_analyzer TO cv_app; GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cv_analyzer TO cv_app; "
                            "GRANT USAGE ON ALL SEQUENCES IN SCHEMA cv_analyzer TO cv_app;")


def test_existing_records_are_unchanged_by_109(through_108):
    d = through_108
    _seed_existing(d)
    before = _fingerprint(d)
    _apply_109(d)
    assert _fingerprint(d) == before


def test_the_switch_is_created_off_and_the_history_starts_empty(through_108):
    d = through_108
    _apply_109(d)
    cur = d.conn.cursor()
    cur.execute("SELECT value, type, category FROM system_config WHERE key = %s", (SWITCH,))
    assert cur.fetchall() == [("false", "boolean", "ai")]
    cur.execute("SELECT count(*) FROM requirements_analysis_log")
    assert cur.fetchone()[0] == 0


def test_applying_109_twice_is_harmless_and_keeps_an_existing_switch_value(through_108):
    d = through_108
    _apply_109(d)
    d.conn.cursor().execute("UPDATE system_config SET value = 'true' WHERE key = %s", (SWITCH,))
    _apply_109(d)                                                    # no error, and an administrator's value is not overwritten
    cur = d.conn.cursor()
    cur.execute("SELECT value FROM system_config WHERE key = %s", (SWITCH,))
    assert cur.fetchone()[0] == "true"


def test_the_application_role_can_read_and_append_the_history(through_108):
    d = through_108
    _owner_grants_like_production(d)
    _apply_109(d)
    conn = _app_connection(d)
    cur = conn.cursor()
    cur.execute("SELECT rolsuper, rolname = current_user FROM pg_roles WHERE rolname = current_user")
    assert cur.fetchone() == (False, True)                           # the application role is not a superuser
    cur.execute("SELECT count(*) FROM requirements_analysis_log")
    assert cur.fetchone()[0] == 0
    cur.execute("INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event) VALUES "
                "('00000000-0000-0000-0000-0000000000c2','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000d1',2,'candidate','requested')")
    cur.execute("SELECT count(*) FROM requirements_analysis_log")
    assert cur.fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize("sql", [
    "UPDATE requirements_analysis_log SET event = 'discarded'",
    "DELETE FROM requirements_analysis_log",
    "TRUNCATE requirements_analysis_log",
    "ALTER TABLE requirements_analysis_log DISABLE TRIGGER requirements_analysis_log_no_change",
    "DROP TABLE requirements_analysis_log",
])
def test_the_application_role_cannot_change_or_remove_the_history(through_108, sql):
    import psycopg2
    d = through_108
    _owner_grants_like_production(d)
    _apply_109(d)
    d.conn.cursor().execute("INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event) VALUES "
                            "('00000000-0000-0000-0000-0000000000c2','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000d1',2,'candidate','requested')")
    conn = _app_connection(d)
    with pytest.raises(psycopg2.Error) as exc:
        conn.cursor().execute(sql)
    assert exc.value.pgcode in ("42501",), (sql, exc.value.pgcode, str(exc.value))   # insufficient_privilege: denied by privilege or by the trigger
    conn.close()
    cur = d.conn.cursor()
    cur.execute("SELECT count(*) FROM requirements_analysis_log")
    assert cur.fetchone()[0] == 1                                    # the one row is still there


def test_a_later_grant_on_all_tables_does_not_make_the_history_mutable(through_108):
    """schema.sql's GRANT ... ON ALL TABLES (re-run, or a later admin grant) restores UPDATE and DELETE on the table. The trigger still refuses."""
    import psycopg2
    d = through_108
    _apply_109(d)
    d.conn.cursor().execute("INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event) VALUES "
                            "('00000000-0000-0000-0000-0000000000c2','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000d1',2,'candidate','requested')")
    d.conn.cursor().execute("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA cv_analyzer TO cv_app")
    conn = _app_connection(d)
    for sql in ("UPDATE requirements_analysis_log SET event = 'discarded'", "DELETE FROM requirements_analysis_log",
                "TRUNCATE requirements_analysis_log"):
        with pytest.raises(psycopg2.Error) as exc:
            conn.cursor().execute(sql)
        assert "append-only" in str(exc.value), sql
    conn.close()


def test_rollback_while_the_history_is_empty_restores_the_108_state(through_108):
    d = through_108
    _apply_109(d)
    cur = d.conn.cursor()
    cur.execute("DROP TRIGGER requirements_analysis_log_no_change ON requirements_analysis_log")
    cur.execute("DROP TRIGGER requirements_analysis_log_no_truncate ON requirements_analysis_log")
    cur.execute("DROP TABLE requirements_analysis_log")
    cur.execute("DELETE FROM system_config WHERE key = %s", (SWITCH,))
    cur.execute("SELECT to_regclass('cv_analyzer.requirements_analysis_log'), (SELECT count(*) FROM system_config WHERE key = %s)", (SWITCH,))
    assert cur.fetchone() == (None, 0)


DEPLOY = pathlib.Path(__file__).resolve().parent.parent / "deploy"


def test_the_preflight_passes_through_108_and_refuses_once_109_is_applied(through_108):
    d = through_108
    d.conn.cursor().execute((DEPLOY / "recovery_109_preflight.sql").read_text(encoding="utf-8"))   # read-only, no error
    _apply_109(d)
    with pytest.raises(Exception) as exc:
        d.conn.cursor().execute((DEPLOY / "recovery_109_preflight.sql").read_text(encoding="utf-8"))
    assert "already applied" in str(exc.value)


def test_the_verification_passes_after_109_with_the_switch_off(through_108):
    d = through_108
    _apply_109(d)
    d.conn.cursor().execute((DEPLOY / "recovery_109_verify.sql").read_text(encoding="utf-8"))


def test_the_verification_refuses_when_the_switch_is_on(through_108):
    d = through_108
    _apply_109(d)
    d.conn.cursor().execute("UPDATE system_config SET value = 'true' WHERE key = %s", (SWITCH,))
    with pytest.raises(Exception) as exc:
        d.conn.cursor().execute((DEPLOY / "recovery_109_verify.sql").read_text(encoding="utf-8"))
    assert "must be false" in str(exc.value)


def test_the_scripted_rollback_refuses_once_history_exists_and_runs_when_empty(through_108):
    d = through_108
    _apply_109(d)
    d.conn.cursor().execute("INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event) VALUES "
                            "('00000000-0000-0000-0000-0000000000c2','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000d1',2,'candidate','requested')")
    with pytest.raises(Exception) as exc:
        d.conn.cursor().execute((DEPLOY / "recovery_109_rollback_empty.sql").read_text(encoding="utf-8"))
    assert "will not be dropped" in str(exc.value)
    cur = d.conn.cursor()
    cur.execute("ROLLBACK")                                          # psql with ON_ERROR_STOP ends the session the same way
    cur.execute("SELECT count(*) FROM requirements_analysis_log")
    assert cur.fetchone()[0] == 1                                    # the refused rollback changed nothing


def test_the_scripted_rollback_removes_the_empty_table_and_restores_the_108_state(through_108):
    d = through_108
    _apply_109(d)
    d.conn.cursor().execute((DEPLOY / "recovery_109_rollback_empty.sql").read_text(encoding="utf-8"))
    cur = d.conn.cursor()
    cur.execute("SELECT to_regclass('cv_analyzer.requirements_analysis_log'), (SELECT count(*) FROM system_config WHERE key = %s)", (SWITCH,))
    assert cur.fetchone() == (None, 0)


# ── what a SUPERUSER can and cannot do (corrected claim: triggers fire for a superuser, but a superuser can disable them) ───────────────

def test_the_trigger_fires_for_a_superuser_and_a_superuser_can_disable_it(through_108):
    """Correct statement of the guarantee: the immutability triggers apply to every role, superusers included, so an UPDATE by the owner-superuser
    is refused. A superuser can still switch the trigger off with ALTER TABLE ... DISABLE TRIGGER, a privileged operation that is logged by
    PostgreSQL only if the operator logs DDL. The application role must therefore be neither a superuser nor the owner."""
    import psycopg2
    d = through_108
    _apply_109(d)
    cur = d.conn.cursor()
    cur.execute("INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event) VALUES "
                "('00000000-0000-0000-0000-0000000000c2','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000d1',2,'candidate','requested')")
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute("UPDATE requirements_analysis_log SET event = 'discarded'")
    assert "append-only" in str(exc.value)                           # the trigger fires for the superuser
    cur.execute("ALTER TABLE requirements_analysis_log DISABLE TRIGGER requirements_analysis_log_no_change")   # privileged: succeeds
    cur.execute("UPDATE requirements_analysis_log SET event = 'discarded'")                                    # so the history CAN change now
    cur.execute("ALTER TABLE requirements_analysis_log ENABLE TRIGGER requirements_analysis_log_no_change")


def test_a_superuser_session_can_skip_the_triggers_with_session_replication_role(through_108):
    d = through_108
    _apply_109(d)
    cur = d.conn.cursor()
    cur.execute("INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event) VALUES "
                "('00000000-0000-0000-0000-0000000000c2','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000d1',2,'candidate','requested')")
    cur.execute("SET session_replication_role = replica")            # superuser-only setting: row triggers do not fire in this session
    cur.execute("UPDATE requirements_analysis_log SET event = 'discarded'")
    cur.execute("RESET session_replication_role")


# ── live settings are preserved: a seed default never overwrites a value that is already set ────────────────────────────────────

def test_live_settings_are_not_overwritten_by_migration_109(through_108):
    """Production had requirements_v2.enabled = true and an active extraction prompt before this release (evidence supplied by the operator).
    Migration 109 must leave both exactly as they are, and create the recovery switch as false."""
    d = through_108
    cur = d.conn.cursor()
    cur.execute("UPDATE system_config SET value = 'true' WHERE key = 'requirements_v2.enabled'")
    cur.execute("UPDATE ai_prompts SET is_active = TRUE WHERE prompt_code = 'criteria_extraction_v2' AND version = 3")
    _apply_109(d)
    cur.execute("SELECT value FROM system_config WHERE key = 'requirements_v2.enabled'")
    assert cur.fetchone()[0] == "true"
    cur.execute("SELECT is_active FROM ai_prompts WHERE prompt_code = 'criteria_extraction_v2' AND version = 3")
    assert cur.fetchone()[0] is True
    cur.execute("SELECT value FROM system_config WHERE key = %s", (SWITCH,))
    assert cur.fetchone()[0] == "false"


def test_the_settings_snapshot_runs_before_and_after_109_and_shows_only_the_new_switch(through_108):
    d = through_108
    cur = d.conn.cursor()
    before_sql = "SELECT key, value FROM system_config WHERE key LIKE 'requirements_v2.%' OR key LIKE 'job_analysis.%' ORDER BY key"
    cur.execute((DEPLOY / "settings_snapshot.sql").read_text(encoding="utf-8"))  # read-only; the last statement's rows are the stage's ai_stage_defaults
    cur.execute(before_sql)
    before = cur.fetchall()
    _apply_109(d)
    cur.execute(before_sql)
    after = cur.fetchall()
    assert [r for r in after if r not in before] == [(SWITCH, "false")]
    assert before == [r for r in after if r[0] != SWITCH]
