"""Requirements V2 routing on a real PostgreSQL 16 built from the full chain: the retry route dispatches by schema version.

A v2 row is retried through the v2 worker only; a completed v2 document is never claimed; concurrent retries yield one claim;
a legacy row keeps the legacy worker. The task enqueue is recorded, not run: the claim and the route's own writes are real."""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from auth.dependencies import CurrentUser, get_current_user
from database import get_db
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from routers import jobs as jobs_router
from services import criteria_status as CS
from workers import criteria_worker as legacy_worker
from workers import requirements_v2_worker as v2_worker

PROBE_ROLE = "cv_v2_route_probe"
JD = ("Senior Python developer to build REST APIs, design PostgreSQL schemas and mentor two engineers. "
      "Five years of Python required.")


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        cur = db.conn.cursor()
        cur.execute(f"CREATE ROLE {PROBE_ROLE} LOGIN")
        cur.execute(f"GRANT USAGE ON SCHEMA cv_analyzer TO {PROBE_ROLE}")
        cur.execute(f"GRANT SELECT, UPDATE ON cv_analyzer.jobs, cv_analyzer.job_criteria TO {PROBE_ROLE}")
        cur.execute(f"GRANT SELECT ON cv_analyzer.system_config TO {PROBE_ROLE}")
        yield db


@pytest_asyncio.fixture()
async def engine(built):
    eng = create_async_engine(f"postgresql+asyncpg://{PROBE_ROLE}@127.0.0.1:{built.cluster.port}/{built.name}",
                              poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    yield eng
    await eng.dispose()


@pytest.fixture()
def enqueued(monkeypatch):
    calls = {"v2": [], "legacy": []}
    monkeypatch.setattr(v2_worker.extract_requirements_v2_task, "delay", lambda job_id, description: calls["v2"].append(job_id))
    monkeypatch.setattr(legacy_worker.extract_criteria_task, "delay", lambda job_id, description: calls["legacy"].append(job_id))
    return calls


def _tenant(built) -> dict:
    cur = built.conn.cursor()
    tag = uuid.uuid4().hex[:10]
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"t {tag}", f"{tag}.example"))
    tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) "
                "VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id", (tenant, f"{tag}@example.test"))
    return {"tenant": tenant, "user": str(cur.fetchone()[0])}


def _job(built, tenant, *, marker, status="failed", analysis=None, retry=0) -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', %s) RETURNING job_id",
                (tenant["tenant"], tenant["user"], JD))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, criteria_extraction_error,"
                " criteria_extraction_retry_count, requirements_schema_version, analysis_json)"
                " VALUES (%s, %s, %s, %s, %s, CAST(%s AS jsonb))",
                (job, status, CS.WORKER_FAILED_MESSAGE if status == "failed" else None, retry, marker,
                 json.dumps(analysis) if analysis is not None else None))
    return job


def _row(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT criteria_extraction_status, criteria_extraction_error, criteria_extraction_retry_count,"
                " requirements_schema_version, analysis_json FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _app(user, engine):
    app = FastAPI()
    app.include_router(jobs_router.router)
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app


def _user(tenant, role="admin"):
    return CurrentUser(user_id=tenant["user"], tenant_id=tenant["tenant"], email="u@example.test", role=role, full_name="u")


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_a_failed_v2_row_is_retried_through_the_v2_worker_only(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, marker=2)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 202
    assert enqueued == {"v2": [job], "legacy": []}
    status, error, retry, marker, analysis = _row(built, job)
    assert (status, error, retry, marker, analysis) == ("pending", None, 1, 2, None)


@pytest.mark.asyncio
async def test_a_legacy_failed_row_is_still_retried_through_the_legacy_worker(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, marker=None)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 202
    assert enqueued == {"v2": [], "legacy": [job]}


@pytest.mark.asyncio
async def test_a_v2_row_with_a_stored_document_is_never_reclaimed(built, engine, enqueued):
    tenant = _tenant(built)
    document = {"requirements": {"schema_version": 2}}
    job = _job(built, tenant, marker=2, status="failed", analysis=document)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 409
    assert enqueued == {"v2": [], "legacy": []}
    assert _row(built, job)[4] == document


@pytest.mark.asyncio
async def test_a_completed_v2_row_is_refused_and_nothing_is_enqueued(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, marker=2, status="completed", analysis={"requirements": {"schema_version": 2}})
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 400
    assert enqueued == {"v2": [], "legacy": []}


@pytest.mark.asyncio
async def test_concurrent_v2_retries_produce_exactly_one_claim(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, marker=2)
    app = _app(_user(tenant), engine)
    async with _client(app) as first, _client(app) as second:
        a, b = await asyncio.gather(first.post(f"/jobs/{job}/criteria/retry"), second.post(f"/jobs/{job}/criteria/retry"))
    assert sorted([a.status_code, b.status_code]) in ([202, 400], [202, 409])   # one claim; the loser is refused
    assert enqueued["v2"] == [job]
    assert _row(built, job)[2] == 1


# ---- a new job is a requirements-v2 job, analysed by the v2 worker ----

def _creation_app(user, engine, monkeypatch):
    monkeypatch.setattr(jobs_router, "can_create_campaign", _allowed)
    return _app(user, engine)


async def _allowed(*args, **kwargs):
    return {"allowed": True}


@pytest_asyncio.fixture()
async def superuser_engine(built):
    eng = create_async_engine(f"postgresql+asyncpg://postgres@127.0.0.1:{built.cluster.port}/{built.name}", poolclass=NullPool,
                              connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_a_new_job_gets_a_v2_criteria_row_and_the_v2_worker_is_enqueued(built, superuser_engine, enqueued, monkeypatch):
    tenant = _tenant(built)
    app = _creation_app(_user(tenant), superuser_engine, monkeypatch)
    async with _client(app) as client:
        response = await client.post("/jobs", json={"title": "Senior Python Developer", "description": JD})
    assert response.status_code == 201, response.text
    job = response.json()["job_id"]
    status, error, retry, marker, analysis = _row(built, job)
    assert (status, marker, analysis) == ("pending", 2, None)
    assert enqueued == {"v2": [job], "legacy": []}
