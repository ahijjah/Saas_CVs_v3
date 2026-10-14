"""PROTOTYPE, on a disposable database only: the dedicated allocator owner (deploy/runtime_role/prototypes/job_code_owner_prototype.sql).

Shows (a) why SELECT(job_code) alone is not enough under row-level security, (b) that the narrow policy makes the allocator skip existing
codes, (c) what the owner can and cannot do, and (d) that the restricted runtime role gains nothing. Nothing here is applied to production.
"""
import os
import pathlib
import re
import uuid

import psycopg2
import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND)]
import realdb_helper as rh  # noqa: E402

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

PROTO = (BACKEND / "deploy" / "runtime_role" / "prototypes" / "job_code_owner_prototype.sql").read_text(encoding="utf-8")
GRANTS = (BACKEND / "deploy" / "runtime_role" / "grants.sql").read_text(encoding="utf-8")
YEAR = 2032
T1, T2 = str(uuid.UUID(int=0x901)), str(uuid.UUID(int=0x902))
U1, U2 = str(uuid.UUID(int=0x911)), str(uuid.UUID(int=0x912))


def proto_sql(with_policy=True):
    """The prototype with its CREATE ROLE made idempotent (the cluster is shared by the tests) and, optionally, without the policy."""
    text_ = PROTO.replace("CREATE ROLE cv_code_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;",
                          "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cv_code_owner') THEN "
                          "CREATE ROLE cv_code_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT; END IF; END $$;")
    if not with_policy:
        text_ = re.sub(r"CREATE POLICY job_code_allocator_read.*?USING \(true\);", "", text_, flags=re.S)
    return "\n".join(line for line in text_.splitlines() if not line.startswith("--"))


@pytest.fixture(scope="module")
def cluster():
    c = rh.Cluster()
    yield c
    c.stop()


def build(cluster, with_policy=True):
    d = cluster.build()
    cur = d.conn.cursor()
    cur.execute("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
                "(%s,'T1','t1.example','active','active','organization'),(%s,'T2','t2.example','active','active','organization')", (T1, T2))
    for uid, tid in ((U1, T1), (U2, T2)):
        cur.execute("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x','U','admin','active')",
                    (uid, tid, f"{uid[-4:]}@x.example"))
    cur.execute(GRANTS)                                # the restricted role, and its allocator EXECUTE
    cur.execute(proto_sql(with_policy))                # the prototype
    return d


def connect(d, user="postgres", tenant=None):
    conn = psycopg2.connect(host="127.0.0.1", port=d.cluster.port, user=user, dbname=d.name, options="-c search_path=cv_analyzer")
    conn.autocommit = True
    if tenant:
        conn.cursor().execute("SELECT set_config('app.current_tenant_id', %s, false), set_config('app.current_role', 'recruiter', false)", (tenant,))
    return conn


def allocate(conn, year=YEAR):
    cur = conn.cursor()
    cur.execute("SELECT cv_analyzer.allocate_job_code(%s)", (year,))
    return cur.fetchone()[0]


# ── (a) and (b): why the policy is needed ───────────────────────────────────────────────────────────────────────────────────────
def test_without_the_policy_the_owner_cannot_see_existing_codes_and_hands_out_a_taken_one(cluster):
    d = build(cluster, with_policy=False)
    try:
        c = connect(d)
        c.cursor().execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,'Taken','d',%s,'active')",
                           (T1, U1, f"JOB-{YEAR}-0001"))
        assert allocate(c) == f"JOB-{YEAR}-0001"        # the existence check is blind: the taken code comes back
        c.close()
    finally:
        d.drop()


def test_with_the_policy_the_owner_sees_every_code_and_skips_the_taken_one(cluster):
    d = build(cluster, with_policy=True)
    try:
        c = connect(d)
        c.cursor().execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,'Taken','d',%s,'active')",
                           (T2, U2, f"JOB-{YEAR}-0001"))   # another tenant's job: the owner must see it
        assert allocate(c) == f"JOB-{YEAR}-0002"
        c.close()
    finally:
        d.drop()


# ── (c): what the owner can and cannot do ──────────────────────────────────────────────────────────────────────────────────────
def test_the_owner_reads_only_the_job_code_column_and_writes_nothing_on_jobs(cluster):
    d = build(cluster, with_policy=True)
    try:
        boss = connect(d)
        boss.cursor().execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,'Other','d','JOB-2032-0009','active')",
                              (T2, U2))
        cur = boss.cursor()
        cur.execute("SET ROLE cv_code_owner")
        cur.execute("SELECT count(*) FROM jobs WHERE job_code LIKE 'JOB-2032-%'")
        assert cur.fetchone()[0] == 1                  # every tenant's row, through the policy
        for sql in ("SELECT title FROM jobs", "UPDATE jobs SET title = 'x'", "DELETE FROM jobs", "INSERT INTO jobs (tenant_id, created_by, title, description, status) VALUES (%s,%s,'x','d','active')"):
            with pytest.raises(psycopg2.Error) as exc:
                cur.execute(sql, (T1, U1) if sql.startswith("INSERT") else None)
            assert exc.value.pgcode == "42501", (sql, exc.value)
        cur.execute("RESET ROLE")
        boss.close()
    finally:
        d.drop()


def test_the_owner_cannot_bypass_row_level_security_and_has_no_membership(cluster):
    d = build(cluster, with_policy=True)
    try:
        c = connect(d)
        cur = c.cursor()
        cur.execute("SELECT rolbypassrls, rolsuper, rolcanlogin FROM pg_roles WHERE rolname = 'cv_code_owner'")
        assert cur.fetchone() == (False, False, False)
        cur.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = 'cv_code_owner'")
        assert cur.fetchone()[0] == 0
        c.close()
    finally:
        d.drop()


# ── (d): the restricted runtime role gains nothing ─────────────────────────────────────────────────────────────────────────────
def test_the_runtime_role_still_reads_only_its_own_tenant_and_still_allocates(cluster):
    d = build(cluster, with_policy=True)
    try:
        boss = connect(d)
        boss.cursor().execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,'Other','d',%s,'active')",
                              (T2, U2, f"JOB-{YEAR}-0001"))
        rt = connect(d, user="cv_runtime", tenant=T1)
        rc = rt.cursor()
        rc.execute("SELECT count(*) FROM jobs WHERE tenant_id = %s", (T2,))
        assert rc.fetchone()[0] == 0                   # the other tenant's row is invisible to the runtime role
        assert allocate(rt) == f"JOB-{YEAR}-0002"      # and the allocator still works for it
        with pytest.raises(psycopg2.Error) as exc:
            rc.execute("SET ROLE cv_code_owner")
        assert exc.value.pgcode == "42501"             # it cannot assume the owner
        rt.close()
        boss.close()
    finally:
        d.drop()


def test_the_policy_names_only_the_owner(cluster):
    d = build(cluster, with_policy=True)
    try:
        c = connect(d)
        cur = c.cursor()
        cur.execute("SELECT roles::text FROM pg_policies WHERE schemaname = 'cv_analyzer' AND policyname = 'job_code_allocator_read'")
        assert cur.fetchall() == [("{cv_code_owner}",)]
        c.close()
    finally:
        d.drop()
