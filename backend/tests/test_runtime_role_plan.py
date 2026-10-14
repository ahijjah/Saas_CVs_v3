"""Restricted runtime role (cv_runtime) on a REAL, disposable PostgreSQL 16 built from the full migration chain. The role is created and granted by
deploy/runtime_role/grants.sql (generated from the source). Nothing here touches production.

Groups: the role's shape (no superuser, no BYPASSRLS, no ownership, no membership, no privilege escalation); tenant isolation under RLS (with
context, without context, across pooled connections, after an in-transaction commit); the recovery history (append-only for the runtime role).
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import realdb_helper as rh  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parent.parent
RUNTIME = "cv_runtime"
GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
T1 = "00000000-0000-0000-0000-0000000000a1"
T2 = "00000000-0000-0000-0000-0000000000a2"
JOB1 = "00000000-0000-0000-0000-0000000000c1"
JOB2 = "00000000-0000-0000-0000-0000000000c2"
U1 = "00000000-0000-0000-0000-0000000000b1"
U2 = "00000000-0000-0000-0000-0000000000b2"
pytestmark = pytest.mark.skipif(rh.available() is not None, reason=rh.available() or "")


@pytest.fixture(scope="module")
def cluster():
    c = rh.Cluster()
    yield c
    c.stop()


@pytest.fixture
def db(cluster):
    d = cluster.build()
    assert d.failed and all(f.startswith(("010_", "024_", "070_", "087_")) for f in d.failed), d.failed     # the known stand-in failures only
    cur = d.conn.cursor()
    cur.execute("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
                f"('{T1}','Tenant One','t1.example','active','active','organization'),('{T2}','Tenant Two','t2.example','active','active','organization')")
    for uid, tid in ((U1, T1), (U2, T2)):
        cur.execute("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES "
                    f"('{uid}','{tid}','{uid[-4:]}@x.example','x','U','admin','active')")
    for jid, tid, uid in ((JOB1, T1, U1), (JOB2, T2, U2)):
        cur.execute("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (%s,%s,%s,'Role','Synthetic')", (jid, tid, uid))
        cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status) VALUES (%s,'completed')", (jid,))
    cur.execute(GRANTS.read_text(encoding="utf-8"))                       # the plan under test
    yield d
    d.drop()


def connect(d, user=RUNTIME):
    import psycopg2
    conn = psycopg2.connect(host="127.0.0.1", port=d.cluster.port, user=user, dbname=d.name, options="-c search_path=cv_analyzer")
    conn.autocommit = False
    return conn


def set_context(cur, tenant, role):
    cur.execute("SELECT set_config('app.current_tenant_id', %s, true), set_config('app.current_role', %s, true)", (tenant, role))


def scalar(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def expect_denied(conn, sql, params=None):
    import psycopg2
    cur = conn.cursor()
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute(sql, params)
    conn.rollback()
    return exc.value


# ── the role's shape ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_runtime_role_has_no_superuser_bypassrls_or_inheritance(db):
    cur = db.conn.cursor()
    cur.execute("SELECT rolsuper, rolbypassrls, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin FROM pg_roles WHERE rolname = %s", (RUNTIME,))
    assert cur.fetchone() == (False, False, False, False, False, True)


def test_the_runtime_role_owns_nothing_and_belongs_to_no_role(db):
    cur = db.conn.cursor()
    assert scalar(cur, "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = %s", (RUNTIME,)) == 0
    assert scalar(cur, "SELECT count(*) FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner WHERE r.rolname = %s", (RUNTIME,)) == 0
    assert scalar(cur, "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = %s", (RUNTIME,)) == 0


def test_the_runtime_role_cannot_assume_a_privileged_role(db):
    import psycopg2
    conn = connect(db)
    for target in ("postgres", "cv_app"):
        with pytest.raises(psycopg2.Error):
            conn.cursor().execute(f"SET ROLE {target}")
        conn.rollback()
    conn.close()


def test_the_runtime_role_cannot_change_structure_or_disable_protections(db):
    conn = connect(db)
    cases = {
        "CREATE TABLE cv_analyzer.x_probe (a int)": "create",
        "DROP TABLE cv_analyzer.requirements_analysis_log": "drop",
        "TRUNCATE cv_analyzer.requirements_analysis_log": "truncate",
        "ALTER TABLE cv_analyzer.requirements_analysis_log DISABLE TRIGGER requirements_analysis_log_no_change": "disable trigger",
        "CREATE TRIGGER probe BEFORE UPDATE ON cv_analyzer.jobs FOR EACH ROW EXECUTE FUNCTION cv_analyzer.set_updated_at()": "create trigger",
        "SET session_replication_role = replica": "replication role",
    }
    for sql in cases:
        expect_denied(conn, sql)
    conn.close()


def test_a_grant_by_the_runtime_role_changes_nothing(db):
    """PostgreSQL does not raise an error for a GRANT by a role without grant option: it warns and grants nothing. The effect is what matters."""
    conn = connect(db)
    cur = conn.cursor()
    cur.execute("GRANT ALL ON cv_analyzer.jobs TO PUBLIC")
    cur.execute("GRANT CREATE ON SCHEMA cv_analyzer TO cv_runtime")
    assert any("no privileges were granted" in n.lower() for n in conn.notices)
    conn.rollback()
    cur = db.conn.cursor()
    assert scalar(cur, "SELECT has_table_privilege('public', 'cv_analyzer.jobs', 'DELETE')") is False
    assert scalar(cur, "SELECT has_schema_privilege('cv_runtime', 'cv_analyzer', 'CREATE')") is False
    conn.close()


def test_the_runtime_role_cannot_alter_the_schema(db):
    conn = connect(db)
    expect_denied(conn, "ALTER SCHEMA cv_analyzer RENAME TO x_probe")
    conn.close()


# ── tenant isolation under RLS ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_with_a_tenant_context_only_that_tenants_jobs_are_visible_and_writable(db):
    conn = connect(db)
    cur = conn.cursor()
    set_context(cur, T1, "admin")
    assert scalar(cur, "SELECT count(*) FROM jobs") == 1
    assert scalar(cur, "SELECT count(*) FROM jobs WHERE tenant_id = %s", (T2,)) == 0
    cur.execute("UPDATE jobs SET title = 'hijack' WHERE job_id = %s", (JOB2,))
    assert cur.rowcount == 0
    conn.rollback()
    conn.close()


def test_a_cross_tenant_insert_is_refused(db):
    import psycopg2
    conn = connect(db)
    cur = conn.cursor()
    set_context(cur, T1, "admin")
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (gen_random_uuid(), %s, %s, 'x', 'y')", (T2, U2))
    assert exc.value.pgcode == "42501"                                   # row-level security: the new row violates the policy
    conn.rollback()
    conn.close()


def test_without_a_context_no_tenant_row_is_visible_and_nothing_can_be_written(db):
    import psycopg2
    conn = connect(db)
    cur = conn.cursor()
    assert scalar(cur, "SELECT count(*) FROM jobs") == 0
    with pytest.raises(psycopg2.Error):
        cur.execute("INSERT INTO jobs (job_id, tenant_id, created_by, title, description) VALUES (gen_random_uuid(), %s, %s, 'x', 'y')", (T1, U1))
    conn.rollback()
    conn.close()


def test_a_pooled_connection_does_not_carry_one_tenant_into_the_next_request(db):
    """One physical connection, two requests back to back. The first sets tenant 1 and commits; the second sets nothing; the third sets tenant 2."""
    conn = connect(db)
    cur = conn.cursor()
    set_context(cur, T1, "admin")
    assert scalar(cur, "SELECT count(*) FROM jobs") == 1
    conn.commit()
    assert scalar(cur, "SELECT count(*) FROM jobs") == 0                 # the next request sees no tenant, not tenant 1
    conn.rollback()
    set_context(cur, T2, "admin")
    assert scalar(cur, "SELECT count(*) FROM jobs") == 1
    assert scalar(cur, "SELECT count(*) FROM jobs WHERE tenant_id = %s", (T1,)) == 0
    conn.rollback()
    conn.close()


def test_a_commit_inside_one_request_drops_the_context_for_the_rest_of_it(db):
    """DOCUMENTS a defect the current code has: set_config(..., true) lasts only to the end of the transaction. A query that runs after a commit in
    the same request sees no tenant. Under the current bypass this is invisible; under the runtime role it returns no rows. The corrected helper
    (deploy/runtime_role/proposed_rls_context.py) re-applies the context at every transaction start."""
    conn = connect(db)
    cur = conn.cursor()
    set_context(cur, T1, "admin")
    conn.commit()
    assert scalar(cur, "SELECT count(*) FROM jobs") == 0
    conn.rollback()
    conn.close()


def test_the_super_admin_context_sees_every_tenant_for_the_background_paths(db):
    """Workers, login and public intake set app.current_role = 'super_admin' in code (the policies allow it). The runtime role does not need
    BYPASSRLS for that. It is the GUC, set by the application, that is trusted here: see the open decision in the plan."""
    conn = connect(db)
    cur = conn.cursor()
    set_context(cur, "", "super_admin")
    assert scalar(cur, "SELECT count(*) FROM jobs") == 2
    conn.rollback()
    conn.close()


# ── the recovery history, as the runtime role ─────────────────────────────────────────────────────────────────────────────────────

HISTORY_ROW = ("INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event) VALUES "
               "('00000000-0000-0000-0000-0000000000c1','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000d1',2,'candidate','requested')")


def test_the_runtime_role_can_read_and_append_the_history(db):
    conn = connect(db)
    cur = conn.cursor()
    cur.execute(HISTORY_ROW)
    conn.commit()
    assert scalar(cur, "SELECT count(*) FROM requirements_analysis_log") == 1
    conn.rollback()
    conn.close()


@pytest.mark.parametrize("sql", [
    "UPDATE requirements_analysis_log SET event = 'discarded'",
    "DELETE FROM requirements_analysis_log",
    "TRUNCATE requirements_analysis_log",
    "ALTER TABLE requirements_analysis_log DISABLE TRIGGER requirements_analysis_log_no_change",
    "DROP TABLE requirements_analysis_log",
])
def test_the_runtime_role_cannot_modify_delete_truncate_or_disable_the_history(db, sql):
    conn = connect(db)
    expect_denied(conn, sql)
    conn.close()


def test_a_later_grant_on_all_tables_does_not_make_the_history_mutable_for_the_runtime_role(db):
    """The privilege grants are not durable: a later GRANT ... ON ALL TABLES restores UPDATE and DELETE. The trigger still refuses (for ordinary roles,
    and the runtime role is not superuser or owner, so it cannot disable it)."""
    import psycopg2
    cur = db.conn.cursor()
    cur.execute(HISTORY_ROW)
    cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA cv_analyzer TO cv_runtime")
    conn = connect(db)
    c = conn.cursor()
    with pytest.raises(psycopg2.Error) as exc:
        c.execute("UPDATE requirements_analysis_log SET event = 'discarded'")
    assert "append-only" in str(exc.value)
    conn.close()


# ── the proposed context helper (deploy/runtime_role/proposed_rls_context.py): pooled async sessions, after commits ──────────────────

def test_the_proposed_helper_keeps_the_context_across_commits_and_never_leaks_it_through_a_pool(db):
    import asyncio
    sys.path.insert(0, str(BACKEND / "deploy" / "runtime_role"))
    import proposed_rls_context as prc
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    prc.install_rls_context()
    url = f"postgresql+asyncpg://{RUNTIME}@127.0.0.1:{db.cluster.port}/{db.name}"
    engine = create_async_engine(url, pool_size=1, max_overflow=0,
                                 connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def run():
        async with Session() as s:                                    # request 1: tenant 1, commits midway, then keeps reading
            await prc.set_request_context(s, T1, "admin")
            assert (await s.execute(text("SELECT count(*) FROM jobs"))).scalar() == 1
            await s.commit()
            assert (await s.execute(text("SELECT count(*) FROM jobs"))).scalar() == 1      # the context came back with the new transaction
        async with Session() as s:                                    # request 2 on the SAME pooled connection: no context
            assert (await s.execute(text("SELECT count(*) FROM jobs"))).scalar() == 0
        async with Session() as s:                                    # request 3: tenant 2
            await prc.set_request_context(s, T2, "admin")
            assert (await s.execute(text("SELECT count(*) FROM jobs"))).scalar() == 1
            assert (await s.execute(text("SELECT count(*) FROM jobs WHERE tenant_id = :t"), {"t": T1})).scalar() == 0

    try:
        asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())


# ── the recovery history: tenant isolation today (gap) and with the proposed policy ───────────────────────────────────────────────

def _two_tenant_history(d):
    cur = d.conn.cursor()
    for tid, jid in ((T1, JOB1), (T2, JOB2)):
        cur.execute("INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event) VALUES "
                    "(%s,%s,gen_random_uuid(),2,'candidate','requested')", (jid, tid))


def test_today_the_runtime_role_can_read_another_tenants_history(db):
    """DOCUMENTS the gap in the current schema: the history has no row-level security, so a tenant-1 session sees tenant 2's events. The services
    filter by job, which is the only protection today."""
    _two_tenant_history(db)
    conn = connect(db)
    cur = conn.cursor()
    set_context(cur, T1, "admin")
    assert scalar(cur, "SELECT count(*) FROM requirements_analysis_log WHERE tenant_id = %s", (T2,)) == 1
    conn.close()


def test_the_proposed_history_policy_isolates_tenants_and_keeps_the_background_path(db):
    import psycopg2
    _two_tenant_history(db)
    db.conn.cursor().execute((BACKEND / "deploy" / "runtime_role" / "proposed_history_rls.sql").read_text(encoding="utf-8"))
    conn = connect(db)
    cur = conn.cursor()
    set_context(cur, T1, "admin")
    assert scalar(cur, "SELECT count(*) FROM requirements_analysis_log") == 1
    assert scalar(cur, "SELECT count(*) FROM requirements_analysis_log WHERE tenant_id = %s", (T2,)) == 0
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute("INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event) VALUES "
                    "(%s,%s,gen_random_uuid(),3,'candidate','requested')", (JOB2, T2))
    assert exc.value.pgcode == "42501"
    conn.rollback()
    set_context(cur, "", "super_admin")
    assert scalar(cur, "SELECT count(*) FROM requirements_analysis_log") == 2
    conn.rollback()
    set_context(cur, "", "")
    assert scalar(cur, "SELECT count(*) FROM requirements_analysis_log") == 0       # no context: nothing
    conn.close()


# ── the plan's own artefacts: the grants file is the generator's output, applying it twice is harmless, and the rollback removes only the role ─

def test_the_committed_grants_file_is_exactly_what_the_generator_produces():
    sys.path.insert(0, str(BACKEND / "deploy" / "runtime_role"))
    import generate_grants
    assert generate_grants.render() == GRANTS.read_text(encoding="utf-8")


def test_applying_the_grants_twice_is_harmless(db):
    db.conn.cursor().execute(GRANTS.read_text(encoding="utf-8"))            # the fixture applied it once
    conn = connect(db)
    cur = conn.cursor()
    assert scalar(cur, "SELECT has_table_privilege('cv_runtime', 'cv_analyzer.jobs', 'SELECT')") is True
    assert scalar(cur, "SELECT has_table_privilege('cv_runtime', 'cv_analyzer.requirements_analysis_log', 'UPDATE')") is False
    conn.close()


def test_the_rollback_removes_the_runtime_role_and_leaves_the_owner_and_app_role_alone(db):
    cur = db.conn.cursor()
    before = scalar(cur, "SELECT has_table_privilege('cv_app', 'cv_analyzer.jobs', 'SELECT')")
    cur.execute((BACKEND / "deploy" / "runtime_role" / "rollback.sql").read_text(encoding="utf-8"))
    assert scalar(cur, "SELECT count(*) FROM pg_roles WHERE rolname = 'cv_runtime'") == 0
    assert scalar(cur, "SELECT has_table_privilege('cv_app', 'cv_analyzer.jobs', 'SELECT')") == before
    assert scalar(cur, "SELECT count(*) FROM pg_namespace WHERE nspname = 'cv_analyzer'") == 1


def test_the_runtime_role_can_set_the_super_admin_context_itself(db):
    """EXPOSURE, documented: the policies trust the session setting app.current_role. A session of the runtime role can set it to 'super_admin'
    and then read every tenant. The privileges cannot stop this; the protection is the application code that sets the setting. This test asserts
    the exposure so that any change to it is a deliberate one (see the plan: a separate system role or a privileged function)."""
    conn = connect(db)
    cur = conn.cursor()
    set_context(cur, T1, "admin")
    set_context(cur, T1, "super_admin")
    assert scalar(cur, "SELECT count(*) FROM jobs") == 2
    conn.close()
