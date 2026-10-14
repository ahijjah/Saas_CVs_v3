"""Job-code allocation (migration 112) on REAL PostgreSQL, in both database modes.

Allocation is atomic and global: a per-year counter incremented inside the caller's transaction, through a SECURITY DEFINER function that
the restricted role may execute and nothing else. Covered: format, sequence, a year transition, skipping an existing code, no gap after a
rollback, backfill from existing codes, the grants (EXECUTE only; the counter is unreadable), and concurrent allocation within and across
tenants with unique codes, with and without rollbacks.
Each mode runs the concurrency checks as its own role: the superuser (CV_TEST_RUNTIME_ROLE unset) or the restricted role (CV_TEST_RUNTIME_ROLE=cv_runtime,
with the grants applied). The grant checks run only under the restricted role.
"""
import os
import pathlib
import threading
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND)]
import realdb_helper as rh  # noqa: E402

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

RUNTIME_ROLE = os.environ.get("CV_TEST_RUNTIME_ROLE", "")
GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
JOB_GRANTS = BACKEND / "deploy" / "runtime_role" / "job_code_allocation_grants.sql"
MIG_112 = BACKEND / "db" / "migrations" / "112_job_code_allocation.sql"
YEAR = 2031                                 # a year no other test uses
T1, T2 = str(uuid.UUID(int=0x701)), str(uuid.UUID(int=0x702))
U1, U2 = str(uuid.UUID(int=0x711)), str(uuid.UUID(int=0x712))


def _seed(d):
    d.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
        "(%s,'T1','t1.example','active','active','organization'),(%s,'T2','t2.example','active','active','organization')", (T1, T2))
    for uid, tid in ((U1, T1), (U2, T2)):
        d.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x','U','admin','active')",
            (uid, tid, f"{uid[-4:]}@x.example"))


@pytest.fixture(scope="module")
def cluster():
    c = rh.Cluster()
    yield c
    c.stop()


@pytest.fixture
def db(cluster):
    d = cluster.build()
    assert all(f.startswith(("010_", "024_", "070_", "087_")) for f in d.failed), d.failed
    _seed(d)
    if RUNTIME_ROLE:
        d.conn.cursor().execute(GRANTS.read_text(encoding="utf-8"))
        d.conn.cursor().execute(JOB_GRANTS.read_text(encoding="utf-8"))
    yield d
    d.drop()


def connect(d, user=None, tenant=None):
    import psycopg2
    conn = psycopg2.connect(host="127.0.0.1", port=d.cluster.port, user=user or (RUNTIME_ROLE or "postgres"), dbname=d.name,
                            options="-c search_path=cv_analyzer")
    conn.autocommit = False
    if tenant is not None:
        cur = conn.cursor()
        cur.execute("SELECT set_config('app.current_tenant_id', %s, false), set_config('app.current_role', 'recruiter', false)", (tenant,))
        conn.commit()
    return conn


def allocate(cur, year=YEAR):
    cur.execute("SELECT allocate_job_code(%s)", (year,))
    return cur.fetchone()[0]


def code(seq, year=YEAR):
    return f"JOB-{year}-{seq:04d}"


# ── format, sequence, year transition, existing codes, rollback ─────────────────────────────────────────────────────────────────
def test_the_first_codes_are_sequential_in_the_documented_format(db):
    conn = connect(db, user="postgres")
    cur = conn.cursor()
    got = [allocate(cur) for _ in range(3)]
    conn.commit()
    assert got == [code(1), code(2), code(3)]


def test_a_new_year_starts_at_0001_and_the_previous_year_continues(db):
    conn = connect(db, user="postgres")
    cur = conn.cursor()
    assert allocate(cur, YEAR) == code(1, YEAR)
    assert allocate(cur, YEAR + 1) == code(1, YEAR + 1)
    assert allocate(cur, YEAR) == code(2, YEAR)
    conn.commit()


def test_an_existing_code_is_skipped_not_reissued(db):
    conn = connect(db, user="postgres")
    cur = conn.cursor()
    assert allocate(cur) == code(1)
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,'Taken','d',%s,'active')",
                (T1, U1, code(2)))
    conn.commit()
    assert allocate(cur) == code(3)                  # 0002 exists, so it is skipped
    conn.commit()


def test_a_rolled_back_allocation_returns_its_number(db):
    conn = connect(db, user="postgres")
    cur = conn.cursor()
    assert allocate(cur) == code(1)
    conn.commit()
    assert allocate(cur) == code(2)
    conn.rollback()                                  # the transaction that took 0002 is abandoned
    assert allocate(cur) == code(2)                  # no gap
    conn.commit()


def test_backfill_continues_each_year_after_its_highest_existing_code(db):
    """Migration 112 on a database that already has codes: build to 111, insert legacy codes (with a gap and a foreign format), then apply 112."""
    conn = connect(db, user="postgres")
    cur = conn.cursor()
    # Remove the counter the fixture's migration run created, then reproduce a legacy database: codes present, no counter.
    cur.execute("DELETE FROM job_code_counters")
    for c in (code(1, 2025), code(2, 2025), code(10, 2025), "LEGACY-7"):
        cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,'Legacy','d',%s,'active')",
                    (T1, U1, c))
    conn.commit()
    cur.execute(MIG_112.read_text(encoding="utf-8").replace("BEGIN;", "").replace("COMMIT;", ""))
    conn.commit()
    assert allocate(cur, 2025) == code(11, 2025)     # continues after 0010, the highest existing
    assert allocate(cur, 2026) == code(1, 2026)      # a year with no codes starts at 0001
    conn.commit()


# ── grants ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not RUNTIME_ROLE, reason="the grant checks are about the restricted role")
def test_the_restricted_role_may_execute_the_allocator_and_nothing_else(db):
    conn = connect(db)                               # cv_runtime
    cur = conn.cursor()
    assert allocate(cur) == code(1)
    conn.commit()
    import psycopg2
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute("SELECT count(*) FROM job_code_counters")
    assert exc.value.pgcode == "42501"
    conn.rollback()
    cur.execute("SELECT has_function_privilege('cv_runtime', 'allocate_job_code(integer)', 'EXECUTE')")
    assert cur.fetchone()[0] is True
    cur.execute("SELECT has_table_privilege('cv_runtime', 'job_code_counters', 'SELECT')")
    assert cur.fetchone()[0] is False
    conn.rollback()


# ── concurrency: within and across tenants ──────────────────────────────────────────────────────────────────────────────────────
def _run_threads(d, n, tenants, rollback_every=0):
    """n concurrent transactions, each on its own connection. Each allocates a code and inserts the job (so the unique index is enforced). Every
    rollback_every-th transaction rolls back after allocating. Returns (committed codes, errors)."""
    codes, errors = [], []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker(i):
        tenant = tenants[i % len(tenants)]
        try:
            conn = connect(d, tenant=tenant)
            cur = conn.cursor()
            barrier.wait()
            c = allocate(cur)
            cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES (%s,%s,%s,'d',%s,'active')",
                        (tenant, U1 if tenant == T1 else U2, f"Concurrent {i}", c))
            if rollback_every and i % rollback_every == rollback_every - 1:
                conn.rollback()
            else:
                conn.commit()
                with lock:
                    codes.append(c)
            conn.close()
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(repr(exc)[:200])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return codes, errors


def test_concurrent_allocation_across_tenants_gives_unique_contiguous_codes(db):
    codes, errors = _run_threads(db, 20, [T1, T2])
    assert errors == [], errors
    assert len(codes) == 20 and len(set(codes)) == 20
    assert sorted(codes) == sorted(code(i) for i in range(1, 21))


def test_concurrent_allocation_within_one_tenant_gives_unique_contiguous_codes(db):
    codes, errors = _run_threads(db, 12, [T1])
    assert errors == [], errors
    assert sorted(codes) == sorted(code(i) for i in range(1, 13))


def test_rollbacks_during_concurrency_leave_no_gap_and_no_duplicate(db):
    """Every third transaction rolls back after allocating. The committed codes are unique and contiguous: the rolled-back numbers were returned."""
    codes, errors = _run_threads(db, 15, [T1, T2], rollback_every=3)
    assert errors == [], errors
    committed = 15 - 5
    assert len(codes) == committed and len(set(codes)) == committed
    assert sorted(codes) == sorted(code(i) for i in range(1, committed + 1))
