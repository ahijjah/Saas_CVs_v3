"""Driver for test_job_code_http.py: concurrent job creation over HTTP through the real route, the real pool and the real session, in one event
loop, in both database modes (the superuser, or the restricted role under CV_TEST_RUNTIME_ROLE). Nothing is sent; the legacy extraction enqueue
is recorded, not run. Writes one JSON object to argv[1]."""
import asyncio
import datetime as dt
import json
import os
import pathlib
import sys
import traceback
import warnings

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND)]
warnings.filterwarnings("ignore")

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

import database  # noqa: E402
from auth.jwt import create_access_token  # noqa: E402
from main import app  # noqa: E402
import routers.jobs as jobs_router  # noqa: E402
import workers.criteria_worker as legacy_worker  # noqa: E402

SEED = json.loads(os.environ["HTTP_SEED"])
OUT: dict = {"steps": {}}
JOB_TEXT = ("Responsibilities: own the delivery of backend services, review code, and mentor two junior engineers. "
            "Required skills: Python, PostgreSQL, and five years of experience building production web APIs. "
            "Qualifications: a degree in computer science or equivalent practical experience.")
legacy_worker.extract_criteria_task.delay = lambda *a, **k: None
YEAR = dt.datetime.now().year


from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

# The observer is the superuser connection (OBSERVER_URL), never the role under test: the counter table is not granted to the restricted role.
OBSERVER = create_async_engine(os.environ["OBSERVER_URL"], poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})


def sql(statement, params=None):
    async def go():
        async with OBSERVER.begin() as conn:
            await conn.execute(text("SELECT set_config('app.current_tenant_id', '', true), set_config('app.current_role', 'super_admin', true)"))
            res = await conn.execute(text(statement), params or {})
            return [dict(r._mapping) for r in res] if res.returns_rows else []
    async def wrapped():
        try:
            return await go()
        finally:
            await database.engine.dispose()
    return asyncio.run(wrapped())


def bearer(who):
    uid, tenant, role = SEED["users"][who]
    return create_access_token({"sub": uid, "tenant_id": tenant, "role": role})


def step(name):
    def deco(fn):
        try:
            OUT["steps"][name] = fn() or {}
        except Exception:
            OUT["steps"][name] = {"exception": traceback.format_exc()[-1500:]}
    return deco


async def create_one(client, who, title):
    r = await client.post("/jobs", headers={"Authorization": f"Bearer {bearer(who)}"},
                          json={"title": title, "description": JOB_TEXT})
    body = r.json() if r.content else None
    return {"who": who, "title": title, "status": r.status_code, "job_code": (body or {}).get("job_code")}


async def concurrent_round(n_per_tenant):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
        tasks = []
        for i in range(n_per_tenant):
            tasks.append(create_one(c, "hr", f"T1 concurrent {i}"))
            tasks.append(create_one(c, "other", f"T2 concurrent {i}"))
        return await asyncio.gather(*tasks)


def run_loop(coro):
    """One event loop per step; the pooled connections of the app engine belong to that loop and are disposed with it."""
    async def wrapper():
        try:
            return await coro
        finally:
            await database.engine.dispose()
    return asyncio.run(wrapper())


@step("concurrent_creation_across_tenants")
def _concurrent():
    results = run_loop(concurrent_round(6))
    codes = sorted(r["job_code"] for r in results if r["job_code"])
    return {"results": results, "codes": codes}


@step("retry_after_a_collision")
def _retry():
    """Force one collision: the allocator returns a code that exists the first time. The savepoint rolls back, the insert is retried with a fresh
    allocation, and the request succeeds. The next code continues the sequence (the failed attempt left no gap)."""
    real = jobs_router._allocate_job_code
    calls = {"n": 0}
    existing = f"JOB-{YEAR}-{13:04d}"

    async def first_collides(db):
        calls["n"] += 1
        if calls["n"] == 1:
            return existing
        return await real(db)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
            first = await create_one(c, "hr", "Retry job")
            calls_for_retry = calls["n"]
            second = await create_one(c, "hr", "After retry job")
            return first, second, calls_for_retry

    sql("INSERT INTO jobs (tenant_id, created_by, title, description, job_code, status) VALUES "
        "((SELECT tenant_id FROM users WHERE user_id = :u), :u, 'Pre-existing', 'd', :c, 'active')",
        {"u": SEED["users"]["hr"][0], "c": existing})
    jobs_router._allocate_job_code = first_collides
    try:
        first, second, calls_for_retry = run_loop(go())
    finally:
        jobs_router._allocate_job_code = real
    return {"retry": first, "after": second, "allocator_calls_for_retry": calls_for_retry, "allocator_calls_total": calls["n"]}


@step("counter_matches_codes_after_the_round")
def _counter():
    rows = sql("SELECT last_seq FROM job_code_counters WHERE year = :y", {"y": YEAR})
    codes = [r["job_code"] for r in sql("SELECT job_code FROM jobs WHERE job_code LIKE :p", {"p": f"JOB-{YEAR}-%"})]
    return {"last_seq": rows[0]["last_seq"] if rows else None, "codes_in_year": len(codes), "max_code_seq": max(int(c.rsplit("-", 1)[1]) for c in codes) if codes else 0,
            "distinct_codes": len(set(codes))}


@step("failure_after_allocation_leaves_no_gap")
def _failed_after_allocation():
    """A create that fails inside the insert, after its code was allocated (an invalid client-organisation UUID). The savepoint rolls the allocation
    back, so the next successful create takes the number the failed one would have used."""
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
            r = await c.post("/jobs", headers={"Authorization": f"Bearer {bearer('hr')}"},
                             json={"title": "Broken job", "description": JOB_TEXT, "client_organization_id": "not-a-uuid"})
            failed = {"status": r.status_code}
            ok = await create_one(c, "hr", "Job after failure")
            return failed, ok
    failed, ok = run_loop(go())
    return {"failed": failed, "after": ok}


if __name__ == "__main__":
    pathlib.Path(sys.argv[1]).write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
