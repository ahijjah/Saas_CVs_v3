"""Job codes (JOB-YYYY-NNNN) come from the global sequence cv_analyzer.job_code_seq (migration 101), never from a count.

Checks: the definition; a fresh database; a database with existing codes (the sequence starts above the largest one); concurrent
tenants under the tenant login with row-level security; a rollback never reissues a code; a duplicate code is refused across tenants;
a re-run of migration 101 never reissues a committed code; and no code is formatted anywhere except the sequence path.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys
import threading
import uuid

import psycopg2
import psycopg2.errors
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import realdb_helper as H  # noqa: E402
from runtime_engines import async_engine  # noqa: E402
from security_fixtures import bootstrap, connect  # noqa: E402

pytestmark = pytest.mark.skipif(H.unavailable_reason() is not None, reason=H.unavailable_reason() or "")
MIGRATION_101 = BACKEND / "db" / "migrations" / "101_job_code_sequence.sql"


@pytest.fixture(scope="module")
def built():
    with H.built_chain() as db:
        H.check_chain(db.failures)
        yield db


@pytest.fixture(scope="module")
def tenants(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    out = {}
    for label in ("a", "b"):
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id",
                    (f"code {label} {uuid.uuid4().hex[:6]}", f"{uuid.uuid4().hex[:8]}.example"))
        tenant = str(cur.fetchone()[0])
        cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id",
                    (tenant, f"{uuid.uuid4().hex[:8]}@example.test"))
        out[label] = {"tenant": tenant, "user": str(cur.fetchone()[0])}
    conn.close()
    return out


def _next_code_as(built, tenant: str | None) -> str:
    """The job-code value the application issues, through the tenant login and its row-level security."""
    import routers.jobs as jobs

    async def issue() -> str:
        engine = async_engine(built, "cv_app")
        try:
            async with AsyncSession(engine) as session:
                if tenant is not None:
                    await session.execute(_tenant_config(tenant))
                return await jobs._next_job_code(session)
        finally:
            await engine.dispose()

    return asyncio.run(issue())


def _tenant_config(tenant: str):
    from sqlalchemy import text
    return text("SELECT set_config('app.current_tenant_id', :t, false)").bindparams(t=tenant)


# ---- the definition ----

def test_the_sequence_is_a_bigint_starting_at_one_and_granted_to_the_job_path_only(built):
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("SELECT data_type, start_value, minimum_value FROM information_schema.sequences "
                "WHERE sequence_schema = 'cv_analyzer' AND sequence_name = 'job_code_seq'")
    row = cur.fetchone()
    assert row[0] == "bigint" and row[1] == "1" and row[2] == "1"
    conn.close()
    granted = []
    for role in ("cv_app", "cv_worker", "cv_admin", "cv_secrets"):
        conn = bootstrap(built)
        cur = conn.cursor()
        cur.execute("SELECT has_sequence_privilege(%s, 'cv_analyzer.job_code_seq', 'USAGE')", (role,))
        if cur.fetchone()[0]:
            granted.append(role)
        conn.close()
    assert granted == ["cv_app"]


def test_the_job_creation_path_is_the_only_code_formatter_and_it_uses_the_sequence():
    source = (BACKEND / "routers" / "jobs.py").read_text(encoding="utf-8")
    function = re.search(r"async def _next_job_code\(.*?\n(?=\n# ──|\n\n@router|\Z)", source, re.S)
    assert function, "_next_job_code not found"
    body = function.group(0)
    assert "nextval('cv_analyzer.job_code_seq')" in body
    assert not re.search(r"count\(", body, re.I), "a count generates codes"
    workers_and_others = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"JOB-\{|f\"JOB-|'JOB-' *\+|\"JOB-\" *\+", text):
            workers_and_others.append(str(path.relative_to(BACKEND)))
    # cv_intake formats a code found in an e-mail so it can match an existing job. It issues no code: it never inserts one.
    assert workers_and_others == ["routers/jobs.py", "workers/cv_intake.py"], workers_and_others
    intake = (BACKEND / "workers" / "cv_intake.py").read_text(encoding="utf-8")
    assert not re.search(r"INSERT INTO jobs[^;]*job_code", intake, re.S | re.I)
    assert "job_code_seq" not in intake


# ---- the value a fresh and an existing database issue ----

def test_a_fresh_database_issues_the_first_code_as_1():
    with H.built_chain() as fresh:
        H.check_chain(fresh.failures)
        conn = bootstrap(fresh)
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM cv_analyzer.jobs")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
        assert cur.fetchone()[0] == 1
        conn.close()


def _at_098_with_codes(tmp_path, codes):
    """A database that has migrations 001 to 098, with jobs carrying the given codes, then migration 101 applied as the migrator."""
    from test_operator_upgrade_postgres import _previous_main_state
    cluster, database = _previous_main_state(tmp_path)
    cur = database.conn.cursor()
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES ('seq', 'seq.example') RETURNING tenant_id")
    tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, 'seq@example.test', 'x', 'u', 'admin') "
                "RETURNING user_id", (tenant,))
    user = str(cur.fetchone()[0])
    for code in codes:
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code) VALUES (%s, %s, 't', 'd', %s)",
                    (tenant, user, code))
    cur.execute(MIGRATION_101.read_text(encoding="utf-8"))
    return cluster, database


def test_an_existing_database_starts_above_its_largest_code(tmp_path):
    cluster, database = _at_098_with_codes(tmp_path, ["JOB-2025-0042", "JOB-2026-0007", "JOB-2026-0010"])
    try:
        cur = database.conn.cursor()
        cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
        assert cur.fetchone()[0] == 43
    finally:
        database.close()
        cluster.stop()


def test_codes_that_do_not_end_in_digits_do_not_break_the_seed(tmp_path):
    cluster, database = _at_098_with_codes(tmp_path, ["LEGACY-ABC", "JOB-2026-0003"])
    try:
        cur = database.conn.cursor()
        cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
        assert cur.fetchone()[0] == 4
    finally:
        database.close()
        cluster.stop()


def test_an_existing_database_with_no_jobs_starts_at_one(tmp_path):
    cluster, database = _at_098_with_codes(tmp_path, [])
    try:
        cur = database.conn.cursor()
        cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
        assert cur.fetchone()[0] == 1
    finally:
        database.close()
        cluster.stop()


# ---- concurrency, rollback and uniqueness under the tenant login ----

def test_concurrent_tenants_under_row_level_security_never_receive_the_same_code(built, tenants):
    """Two tenants, four concurrent sessions, each issuing many codes through the tenant login. Every code is distinct, although each
    session sees only its own tenant's jobs."""
    issued: list[str] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker(tenant: str):
        try:
            codes = [_next_code_as(built, tenant) for _ in range(15)]
            with lock:
                issued.extend(codes)
        except BaseException as exc:      # reported below, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(tenants[label]["tenant"],)) for label in ("a", "b", "a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(issued) == 60
    assert len(set(issued)) == 60, "two concurrent issues produced the same code"


def test_a_tenant_with_no_visible_jobs_is_not_issued_a_code_another_tenant_holds(built, tenants):
    conn = bootstrap(built)
    cur = conn.cursor()
    code = _next_code_as(built, tenants["a"]["tenant"])
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code) VALUES (%s, %s, 'held', 'd', %s)",
                (tenants["a"]["tenant"], tenants["a"]["user"], code))
    conn.close()
    other = _next_code_as(built, tenants["b"]["tenant"])
    assert other != code


def test_a_rollback_consumes_a_value_but_never_reissues_one(built, tenants):
    conn = connect(built, "cv_app", tenant=tenants["a"]["tenant"])
    cur = conn.cursor()
    cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
    rolled_back = cur.fetchone()[0]
    conn.rollback()
    cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
    after = cur.fetchone()[0]
    conn.commit()
    conn.close()
    assert after > rolled_back, "a sequence value from a rolled-back job was given out again"


def test_the_unique_index_refuses_a_duplicate_code_across_tenants_even_though_row_level_security_hides_the_other_tenant(built, tenants):
    conn = bootstrap(built)
    cur = conn.cursor()
    cur.execute("SELECT job_code FROM jobs WHERE tenant_id = %s LIMIT 1", (tenants["b"]["tenant"],))
    held = cur.fetchone()
    if held is None:
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code) VALUES (%s, %s, 'b', 'd', %s) RETURNING job_code",
                    (tenants["b"]["tenant"], tenants["b"]["user"], _next_code_as(built, tenants["b"]["tenant"])))
        held = cur.fetchone()
    conn.close()
    tenant_a = connect(built, "cv_app", tenant=tenants["a"]["tenant"])
    acur = tenant_a.cursor()
    with pytest.raises(psycopg2.errors.UniqueViolation):
        acur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code) VALUES (%s, %s, 'dup', 'd', %s)",
                     (tenants["a"]["tenant"], tenants["a"]["user"], held[0]))
    tenant_a.close()


def test_rerunning_migration_101_never_reissues_a_committed_code(tmp_path):
    """A code issued and stored in a job is committed. Re-running 101 resets the sequence to the largest stored code plus one, so the
    committed code is never issued again."""
    cluster, database = _at_098_with_codes(tmp_path, ["JOB-2026-0005"])
    try:
        cur = database.conn.cursor()
        cur.execute("SELECT job_id FROM cv_analyzer.jobs LIMIT 1")
        tenant_user = cur.execute("SELECT tenant_id, created_by FROM cv_analyzer.jobs LIMIT 1") or cur.fetchone()
        cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
        committed = cur.fetchone()[0]
        cur.execute("INSERT INTO cv_analyzer.jobs (tenant_id, created_by, title, description, job_code) "
                    "SELECT tenant_id, created_by, 'stored', 'd', %s FROM cv_analyzer.jobs LIMIT 1", (f"JOB-2026-{committed:04d}",))
        cur.execute(MIGRATION_101.read_text(encoding="utf-8"))
        cur.execute("SELECT nextval('cv_analyzer.job_code_seq')")
        after_rerun = cur.fetchone()[0]
        assert after_rerun > committed, (after_rerun, committed)
    finally:
        database.close()
        cluster.stop()


def test_migration_101_is_forward_only_and_documents_its_rollback_boundary():
    text = MIGRATION_101.read_text(encoding="utf-8")
    assert "CREATE SEQUENCE IF NOT EXISTS" in text and "setval" in text
    assert "DROP SEQUENCE" not in text and "ALTER SEQUENCE" not in text
    doc = (BACKEND.parent / "docs" / "DATABASE_ROLES.md").read_text(encoding="utf-8")
    assert "101" in doc and "forward-only" in doc.lower() or "Migrations are forward-only" in doc
