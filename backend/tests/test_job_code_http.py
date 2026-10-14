"""Concurrent job creation over the REAL route, the REAL pool and session, on REAL PostgreSQL, in both database modes.

  * 12 concurrent POST /jobs across two tenants: every request succeeds, the codes are JOB-YYYY-0001..0012 exactly once each.
  * A forced collision: the first allocation returns a code that exists; the savepoint is rolled back, the insert is retried, and the request
    succeeds with the next free code. The failed attempt leaves no gap: the counter equals the highest code.
Superuser: CV_TEST_RUNTIME_ROLE unset. Restricted role: CV_TEST_RUNTIME_ROLE=cv_runtime, with the migration-112 grant file applied.
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND), str(BACKEND / "services")]
import realdb_helper as rh  # noqa: E402

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

DRIVER = BACKEND / "tests" / "job_code_http_driver.py"
RUNTIME_ROLE = os.environ.get("CV_TEST_RUNTIME_ROLE", "")
GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
JOB_GRANTS = BACKEND / "deploy" / "runtime_role" / "job_code_allocation_grants.sql"
T1, T2 = str(uuid.UUID(int=0x801)), str(uuid.UUID(int=0x802))
USERS = {"hr": (str(uuid.UUID(int=0x811)), T1, "hr_manager"), "other": (str(uuid.UUID(int=0x812)), T2, "admin")}
YEAR = dt.datetime.now().year


@pytest.fixture(scope="module")
def obs(tmp_path_factory):
    cluster = rh.Cluster()
    db = cluster.build()
    try:
        db.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type, max_jobs) VALUES "
             "(%s,'T1','t1.example','active','active','organization',1000),(%s,'T2','t2.example','active','active','organization',1000)", (T1, T2))
        for key, (uid, tenant, role) in USERS.items():
            db.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x',%s,%s,'active')",
                 (uid, tenant, f"{key}-{uid[-4:]}@x.example", key, role))
        url = db.async_url
        if RUNTIME_ROLE:
            db.conn.cursor().execute(GRANTS.read_text(encoding="utf-8"))
            db.conn.cursor().execute(JOB_GRANTS.read_text(encoding="utf-8"))
            url = url.replace("postgres@", f"{RUNTIME_ROLE}@")
        out = tmp_path_factory.mktemp("jobcode") / "obs.json"
        env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "test-only-secret", "PYTHONIOENCODING": "utf-8", "ENVIRONMENT": "production",
               "HTTP_SEED": json.dumps({"users": USERS}), "OBSERVER_URL": db.async_url.replace("postgresql+asyncpg://", "postgresql+asyncpg://")}
        proc = subprocess.run([sys.executable, "-I", str(DRIVER), str(out)], env=env, capture_output=True, text=True, timeout=900, cwd=str(BACKEND))
        assert proc.returncode == 0, proc.stderr[-4000:]
        return json.loads(out.read_text(encoding="utf-8"))
    finally:
        db.drop()
        cluster.stop()


def S(obs, name):
    step = obs["steps"][name]
    assert "exception" not in step, step["exception"]
    return step


def test_twelve_concurrent_creations_across_two_tenants_all_succeed(obs):
    st = S(obs, "concurrent_creation_across_tenants")
    assert all(r["status"] == 201 for r in st["results"]), st["results"]


def test_concurrent_creations_get_unique_contiguous_codes(obs):
    st = S(obs, "concurrent_creation_across_tenants")
    assert st["codes"] == [f"JOB-{YEAR}-{i:04d}" for i in range(1, 13)], st["codes"]


def test_a_forced_collision_is_retried_with_a_fresh_code(obs):
    st = S(obs, "retry_after_a_collision")
    assert st["allocator_calls_for_retry"] == 2, st               # the first call collided, the second succeeded
    assert st["retry"]["status"] == 201 and st["retry"]["job_code"] == f"JOB-{YEAR}-0014", st


def test_the_next_code_continues_with_no_gap_after_the_retry(obs):
    st = S(obs, "retry_after_a_collision")
    assert st["after"]["status"] == 201 and st["after"]["job_code"] == f"JOB-{YEAR}-0015", st


def test_the_counter_equals_the_highest_code_and_no_code_is_duplicated(obs):
    st = S(obs, "counter_matches_codes_after_the_round")
    assert st["last_seq"] == st["max_code_seq"] == 15, st
    assert st["codes_in_year"] == st["distinct_codes"] == 15, st


def test_a_failed_create_after_allocation_leaves_no_gap(obs):
    """The allocation is rolled back with the failed insert: the next successful create takes the number the failed one would have used."""
    st = S(obs, "failure_after_allocation_leaves_no_gap")
    assert st["failed"]["status"] >= 400, st
    assert st["after"]["status"] == 201 and st["after"]["job_code"] == f"JOB-{YEAR}-0016", st
