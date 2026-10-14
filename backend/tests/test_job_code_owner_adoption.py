"""Adopted allocator owner (migration 112): cv_code_owner. On REAL PostgreSQL 16.

The owner role is CLUSTER-WIDE, so every test that makes it absent, unsafe or a member of something uses its own fresh cluster. Tests that
only read the adopted state share one module cluster and never change the role.

Covered:
  * an absent owner is created with the exact safe attributes; an existing owner is accepted only in that exact shape; an unsafe attribute,
    a membership in either direction, or a foreign owned object refuses the migration with NO change to the database;
  * ownership: the allocator and the counter belong to the owner; the allocator is SECURITY DEFINER with a pinned search path;
  * the owner reads SELECT(job_code) across tenants through one policy and nothing else of jobs; it cannot write jobs and cannot log in;
  * cv_runtime: EXECUTE on the allocator only; no membership; cannot assume the owner; reads no other tenant's rows; cannot read the counter;
  * PUBLIC cannot execute the allocator;
  * rollback (deploy/runtime_role/rollback_112.sql) keeps the counter state and the owner; re-applying 112 and the grants resumes from it.

This file does not depend on CV_TEST_RUNTIME_ROLE: the restricted role is always probed explicitly. The mode-dependent suites are
test_job_code_allocation.py and test_job_code_http.py.
"""
import contextlib
import pathlib
import sys
import time
import uuid

import psycopg2
import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND)]
import realdb_helper as rh  # noqa: E402

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

MIG_112 = (BACKEND / "db" / "migrations" / "112_job_code_allocation.sql").read_text(encoding="utf-8")
GRANTS = (BACKEND / "deploy" / "runtime_role" / "grants.sql").read_text(encoding="utf-8")
ROLLBACK = (BACKEND / "deploy" / "runtime_role" / "rollback_112.sql").read_text(encoding="utf-8")
BEFORE_112 = "111_tenant_policies_use_the_applied_settings.sql"
OWNER_CREATE = "CREATE ROLE cv_code_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT"
SAFE = (False, False, False, False, False, False, False)  # rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication, rolinherit
PRE_EXISTING_FAILURES = ("010_", "024_", "070_", "087_")  # failures already present on schema.sql, pre-existing and unrelated
T1, T2 = str(uuid.UUID(int=0xA01)), str(uuid.UUID(int=0xA02))
U1, U2 = str(uuid.UUID(int=0xA11)), str(uuid.UUID(int=0xA12))
YEAR = 2033


@pytest.fixture(scope="module")
def cluster():
    c = rh.Cluster()
    yield c
    c.stop()


@pytest.fixture
def fresh():
    c = rh.Cluster()
    yield c
    c.stop()


@pytest.fixture
def adopted(cluster):
    d = cluster.build()                                   # the full chain, including 112
    assert all(f.startswith(PRE_EXISTING_FAILURES) for f in d.failed), d.failed
    seed(d)
    d.conn.cursor().execute(GRANTS)                       # the restricted role's grants, after 112, as the rollout requires
    yield d
    d.drop()


def seed(d):
    d.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
        "(%s,'T1','t1.example','active','active','organization'),(%s,'T2','t2.example','active','active','organization')", (T1, T2))
    for uid, tid in ((U1, T1), (U2, T2)):
        d.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x','U','admin','active')",
            (uid, tid, f"{uid[-4:]}@x.example"))


@contextlib.contextmanager
def session(d, user="postgres", tenant=None):
    c = psycopg2.connect(host="127.0.0.1", port=d.cluster.port, user=user, dbname=d.name, options="-c search_path=cv_analyzer")
    c.autocommit = True
    try:
        if tenant:
            c.cursor().execute("SELECT set_config('app.current_tenant_id', %s, false), set_config('app.current_role', 'recruiter', false)", (tenant,))
        yield c
    finally:
        c.close()


def apply_112(d):
    """Run migration 112 as the migration role (the superuser here). None on success; the first error line on refusal, with the open
    transaction rolled back so that nothing from the refused run remains."""
    try:
        d.conn.cursor().execute(MIG_112)
        return None
    except psycopg2.Error as exc:
        d.conn.cursor().execute("ROLLBACK")
        return str(exc).splitlines()[0]


def wait_no_runtime(d, timeout=5.0):
    """The rollback refuses while any cv_runtime session is open. A closed client socket can leave its backend listed for a moment."""
    end = time.time() + timeout
    while time.time() < end:
        if not d.q("SELECT 1 FROM pg_stat_activity WHERE usename = 'cv_runtime'"):
            return
        time.sleep(0.1)
    raise AssertionError("cv_runtime sessions are still open")


def role_state(d):
    return d.q("SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication, rolinherit "
               "FROM pg_roles WHERE rolname = 'cv_code_owner'")


# ── the owner role: created when absent, accepted only in the exact safe shape, never partially applied ──────────────────────
def test_the_owner_is_created_with_the_exact_safe_attributes_when_absent(fresh):
    d = fresh.build(upto=BEFORE_112)
    seed(d)
    try:
        assert d.q("SELECT 1 FROM pg_roles WHERE rolname = 'cv_code_owner'") == []
        assert apply_112(d) is None
        assert role_state(d) == [SAFE]
    finally:
        d.drop()


def test_an_existing_owner_in_the_exact_safe_shape_is_accepted(fresh):
    d = fresh.build(upto=BEFORE_112)
    seed(d)
    try:
        d.q(OWNER_CREATE)
        assert apply_112(d) is None
        assert role_state(d) == [SAFE]
    finally:
        d.drop()


UNSAFE = {"LOGIN": "rolcanlogin", "SUPERUSER": "rolsuper", "BYPASSRLS": "rolbypassrls", "INHERIT": "rolinherit", "CREATEROLE": "rolcreaterole"}


@pytest.mark.parametrize("attr", list(UNSAFE))
def test_an_existing_owner_with_an_unsafe_attribute_refuses_the_migration_and_is_not_repaired(fresh, attr):
    d = fresh.build(upto=BEFORE_112)
    seed(d)
    try:
        d.q(OWNER_CREATE)
        d.q(f"ALTER ROLE cv_code_owner {attr}")
        err = apply_112(d)
        assert err is not None and "unsafe attributes" in err, err
        assert d.q(f"SELECT {UNSAFE[attr]} FROM pg_roles WHERE rolname = 'cv_code_owner'") == [(True,)], "the unsafe attribute must not be silently repaired"
        assert d.q("SELECT to_regclass('cv_analyzer.job_code_counters') IS NULL, to_regprocedure('cv_analyzer.allocate_job_code(integer)') IS NULL") == [(True, True)], \
            "a refused migration must leave no table and no function behind"
    finally:
        d.drop()


@pytest.mark.parametrize("direction", ["owner_is_member", "owner_has_member"])
def test_an_owner_with_a_membership_in_either_direction_refuses_the_migration(fresh, direction):
    d = fresh.build(upto=BEFORE_112)
    seed(d)
    try:
        d.q(OWNER_CREATE)
        d.q("CREATE ROLE cv_probe_role NOLOGIN")
        if direction == "owner_is_member":
            d.q("GRANT cv_probe_role TO cv_code_owner")
        else:
            d.q("GRANT cv_code_owner TO cv_probe_role")
        err = apply_112(d)
        assert err is not None and "memberships" in err, err
        assert d.q("SELECT to_regclass('cv_analyzer.job_code_counters') IS NULL") == [(True,)]
    finally:
        d.drop()


def test_an_owner_that_owns_a_foreign_object_refuses_the_migration(fresh):
    d = fresh.build(upto=BEFORE_112)
    seed(d)
    try:
        assert apply_112(d) is None                       # the owner now exists in the safe shape
        d2 = fresh.build(upto=BEFORE_112)
        seed(d2)
        try:
            d2.q("CREATE FUNCTION public.owner_probe() RETURNS integer LANGUAGE sql AS 'SELECT 1'")
            d2.q("ALTER FUNCTION public.owner_probe() OWNER TO cv_code_owner")
            err = apply_112(d2)
            assert err is not None and "owns objects" in err, err
            assert d2.q("SELECT to_regclass('cv_analyzer.job_code_counters') IS NULL") == [(True,)]
        finally:
            d2.drop()
    finally:
        d.drop()


# ── after adoption: ownership, privileges, the owner's one read path, and the restricted role ──────────────────────────────
def test_the_owner_has_the_exact_attributes_and_no_membership_either_way(adopted):
    assert role_state(adopted) == [SAFE]
    assert adopted.q("SELECT count(*) FROM pg_auth_members m JOIN pg_roles o ON o.oid IN (m.roleid, m.member) "
                     "WHERE o.rolname = 'cv_code_owner'") == [(0,)]


def test_the_allocator_and_the_counter_belong_to_the_owner_and_the_allocator_is_definer_with_a_pinned_path(adopted):
    owner, secdef, proconfig, body = adopted.q(
        "SELECT pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig, pg_get_functiondef(p.oid) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'cv_analyzer' AND p.proname = 'allocate_job_code'")[0]
    assert owner == "cv_code_owner" and secdef is True and proconfig == ["search_path=pg_catalog, pg_temp"]
    assert "cv_analyzer.jobs" in body and "cv_analyzer.job_code_counters" in body
    assert adopted.q("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname = 'job_code_counters'") == [("cv_code_owner",)]


def test_the_owner_reads_only_job_code_and_only_through_its_one_policy(adopted):
    assert adopted.q("SELECT has_column_privilege('cv_code_owner', 'cv_analyzer.jobs', 'job_code', 'SELECT'), "
                     "has_column_privilege('cv_code_owner', 'cv_analyzer.jobs', 'title', 'SELECT'), "
                     "has_table_privilege('cv_code_owner', 'cv_analyzer.jobs', 'INSERT'), "
                     "has_table_privilege('cv_code_owner', 'cv_analyzer.jobs', 'UPDATE'), "
                     "has_table_privilege('cv_code_owner', 'cv_analyzer.jobs', 'DELETE')") == [(True, False, False, False, False)]
    assert adopted.q("SELECT policyname, roles::text, cmd, qual FROM pg_policies WHERE schemaname = 'cv_analyzer' AND tablename = 'jobs' "
                     "AND roles::text LIKE '%cv_code_owner%'") == [("job_code_allocator_read", "{cv_code_owner}", "SELECT", "true")]


def test_the_owner_cannot_log_in(adopted):
    with pytest.raises(psycopg2.OperationalError):
        with session(adopted, user="cv_code_owner"):
            pass


def test_the_owner_sees_every_tenants_code_and_nothing_else_of_jobs(adopted):
    adopted.q("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,'Other','d',%s,'active')",
              (T2, U2, f"JOB-{YEAR}-0001"))
    with session(adopted) as c:
        cur = c.cursor()
        cur.execute("SET ROLE cv_code_owner")
        cur.execute("SELECT count(*) FROM jobs WHERE job_code = %s", (f"JOB-{YEAR}-0001",))
        assert cur.fetchone()[0] == 1, "the owner must see another tenant's code through its policy"
        for sql, args in (("SELECT title FROM jobs", None), ("UPDATE jobs SET title = 'x'", None), ("DELETE FROM jobs", None),
                          ("INSERT INTO jobs (tenant_id, created_by, title, description, status) VALUES (%s,%s,'x','d','active')", (T1, U1))):
            with pytest.raises(psycopg2.Error) as exc:
                cur.execute(sql, args)
            assert exc.value.pgcode == "42501", (sql, exc.value)


def test_the_runtime_role_cannot_assume_the_owner_or_read_another_tenant_or_the_counter(adopted):
    adopted.q("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,'Other','d',%s,'active')",
              (T2, U2, f"JOB-{YEAR + 5}-0009"))           # another year, so YEAR's counter starts at 0001
    with session(adopted, user="cv_runtime", tenant=T1) as rt:
        rc = rt.cursor()
        with pytest.raises(psycopg2.Error) as exc:
            rc.execute("SET ROLE cv_code_owner")
        assert exc.value.pgcode == "42501"
        rc.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles o ON o.oid = m.roleid WHERE o.rolname = 'cv_code_owner'")
        assert rc.fetchone()[0] == 0
        rc.execute("SELECT count(*) FROM jobs WHERE tenant_id = %s", (T2,))
        assert rc.fetchone()[0] == 0, "the runtime role must not see another tenant's jobs"
        with pytest.raises(psycopg2.Error) as exc:
            rc.execute("SELECT last_seq FROM job_code_counters")
        assert exc.value.pgcode == "42501"
        rc.execute("SELECT cv_analyzer.allocate_job_code(%s)", (YEAR,))
        assert rc.fetchone()[0] == f"JOB-{YEAR}-0001"


def test_public_cannot_execute_the_allocator_and_the_restricted_role_can(adopted):
    assert adopted.q("SELECT has_function_privilege('public', 'cv_analyzer.allocate_job_code(integer)', 'EXECUTE'), "
                     "has_function_privilege('cv_runtime', 'cv_analyzer.allocate_job_code(integer)', 'EXECUTE')") == [(False, True)]


def test_the_allocator_skips_a_code_another_tenant_holds(adopted):
    with session(adopted, user="cv_runtime", tenant=T1) as rt:
        rc = rt.cursor()
        rc.execute("SELECT cv_analyzer.allocate_job_code(%s)", (YEAR + 1,))
        assert rc.fetchone()[0] == f"JOB-{YEAR + 1}-0001"
        # tenant 2 now holds the NEXT number, which the restricted role cannot see; the owner's policy makes it visible to the allocator
        adopted.q("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,'Held','d',%s,'active')",
                  (T2, U2, f"JOB-{YEAR + 1}-0002"))
        rc.execute("SELECT cv_analyzer.allocate_job_code(%s)", (YEAR + 1,))
        assert rc.fetchone()[0] == f"JOB-{YEAR + 1}-0003"


# ── rollback: the counter state, the owner and the allocator survive; re-applying resumes from the counter ──────────────────
def test_the_rollback_keeps_the_counter_state_and_the_owner_and_resumes_on_reapply(adopted):
    with session(adopted, user="cv_runtime", tenant=T1) as rt:
        rc = rt.cursor()
        rc.execute("SELECT cv_analyzer.allocate_job_code(%s)", (YEAR + 2,))
        assert rc.fetchone()[0] == f"JOB-{YEAR + 2}-0001"
        rc.execute("SELECT cv_analyzer.allocate_job_code(%s)", (YEAR + 2,))
        assert rc.fetchone()[0] == f"JOB-{YEAR + 2}-0002"
    before = adopted.q("SELECT year, last_seq FROM job_code_counters ORDER BY year")
    assert (YEAR + 2, 2) in before

    wait_no_runtime(adopted)
    adopted.conn.cursor().execute(ROLLBACK)
    assert adopted.q("SELECT year, last_seq FROM job_code_counters ORDER BY year") == before, "the default rollback must keep the counter state"
    assert adopted.q("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname = 'job_code_counters'") == [("cv_code_owner",)]
    assert adopted.q("SELECT has_column_privilege('cv_code_owner', 'cv_analyzer.jobs', 'job_code', 'SELECT'), "
                     "has_function_privilege('cv_runtime', 'cv_analyzer.allocate_job_code(integer)', 'EXECUTE')") == [(False, False)]
    assert adopted.q("SELECT count(*) FROM pg_policies WHERE policyname = 'job_code_allocator_read'") == [(0,)]
    with session(adopted, user="cv_runtime", tenant=T1) as rt:
        with pytest.raises(psycopg2.Error) as exc:
            rt.cursor().execute("SELECT cv_analyzer.allocate_job_code(%s)", (YEAR + 2,))
        assert exc.value.pgcode == "42501"

    # re-applying the migration and the grants resumes from the preserved counter: the next code is the one after the last issued
    assert apply_112(adopted) is None
    adopted.conn.cursor().execute(GRANTS)
    with session(adopted, user="cv_runtime", tenant=T1) as rt:
        rc = rt.cursor()
        rc.execute("SELECT cv_analyzer.allocate_job_code(%s)", (YEAR + 2,))
        assert rc.fetchone()[0] == f"JOB-{YEAR + 2}-0003"
