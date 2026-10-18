"""PUT /jobs/{job_id}/criteria (the legacy criteria route): only admin and recruiter may write.

The real route runs against a disposable PostgreSQL 16 built from the full migration chain, through a real async session. Only the
authenticated user and the session factory are overridden. A refused role is refused before any SQL runs, so nothing changes.
Tenant isolation and the missing-job and missing-criteria responses are unchanged."""
from __future__ import annotations

import json
import uuid

import httpx
import psycopg2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from auth.dependencies import CurrentUser, get_current_user
from database import get_db
from runtime_engines import async_engine
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from routers import jobs as jobs_router

pytestmark = pytest.mark.asyncio

PROBE_ROLE = "cv_app"
JD = "A job description."
BODY = {"weight_skills": 40, "weight_experience": 20, "weight_education": 10, "weight_certifications": 10,
        "weight_soft_skills": 10, "weight_domain_knowledge": 5, "weight_other": 5}           # totals 100
DEFAULT_WEIGHTS = (30, 25, 15, 10, 10, 5, 5)
REFUSED_ROLES = [None, "", "owner", "Admin", "ADMIN", "viewer", "super_admin", "hr_manager"]


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        cur = db.conn.cursor()
        yield db


@pytest.fixture()
def seeded(built):
    """Two tenants. Tenant a has an admin, a recruiter and a viewer, and a job with a criteria row. Tenant b has an admin and
    its own job. Every job has a criteria row unless a test removes it."""
    cur = built.conn.cursor()
    tags = {}
    for label in ("a", "b"):
        tag = uuid.uuid4().hex[:10]
        cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id",
                    (f"tenant {label} {tag}", f"{label}-{tag}.example"))
        tenant = str(cur.fetchone()[0])
        users = {}
        for role in (("admin", "recruiter", "viewer") if label == "a" else ("admin",)):
            cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) "
                        "VALUES (%s, %s, 'x', 'u', %s) RETURNING user_id",
                        (tenant, f"{label}-{role}-{tag}@example.test", role))
            users[role] = str(cur.fetchone()[0])
        tags[label] = {"tenant": tenant, "users": users, "job": _job(built, tenant, users["admin"])}
    return tags


def _job(built, tenant, creator, *, with_criteria=True) -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', %s) RETURNING job_id",
                (tenant, creator, JD))
    job = str(cur.fetchone()[0])
    if with_criteria:
        analysis = {"scoring_weights": dict(zip(
            ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge",
             "other_requirements"), DEFAULT_WEIGHTS)), "extraction_notes": {"model": "fixture-model"}}
        cur.execute(
            "INSERT INTO job_criteria (job_id, analysis_json, weight_skills, weight_experience, weight_education,"
            " weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (job, json.dumps(analysis), *DEFAULT_WEIGHTS))
    return job


def _snapshot(built, job):
    """Everything the route can change, read on the untouched superuser connection."""
    cur = built.conn.cursor()
    cur.execute("SELECT weight_skills, weight_experience, weight_education, weight_certifications, weight_soft_skills,"
                " weight_domain_knowledge, weight_other, analysis_json, last_edited_by, last_edited_at, updated_at,"
                " skills, experience, education, certifications, soft_skills, domain_knowledge, other_requirements"
                " FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _user(role, tenant, user_id) -> CurrentUser:
    return CurrentUser(user_id=user_id, tenant_id=tenant, email="u@example.test", role=role, full_name="u")


@pytest_asyncio.fixture()
async def engines(built):
    made = []

    def make(role: str = "cv_app"):
        engine = async_engine(built, role)
        made.append(engine)
        return engine

    yield make
    for engine in made:
        await engine.dispose()


def _app(user, engine):
    """The real router. Only the authenticated user and the session factory are replaced."""
    app = FastAPI()
    app.include_router(jobs_router.router)
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app


async def _put(app, job_id):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.put(f"/jobs/{job_id}/criteria", json=BODY)


def _statements(engine):
    seen: list[str] = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda *args, **kwargs: seen.append(args[2]))
    return seen


# ---- allowed roles ----

async def test_an_admin_can_update_the_criteria(built, engines, seeded):
    job, admin = seeded["a"]["job"], seeded["a"]["users"]["admin"]
    engine = engines()
    response = await _put(_app(_user("admin", seeded["a"]["tenant"], admin), engine), job)
    assert response.status_code == 200, response.text
    row = _snapshot(built, job)
    assert row[:7] == (40, 20, 10, 10, 10, 5, 5)
    assert str(row[8]) == admin


async def test_a_recruiter_can_update_the_criteria(built, engines, seeded):
    job, recruiter = seeded["a"]["job"], seeded["a"]["users"]["recruiter"]
    engine = engines()
    response = await _put(_app(_user("recruiter", seeded["a"]["tenant"], recruiter), engine), job)
    assert response.status_code == 200, response.text
    row = _snapshot(built, job)
    assert row[:7] == (40, 20, 10, 10, 10, 5, 5)
    assert str(row[8]) == recruiter


# ---- every other role is refused before any SQL, and nothing changes ----

@pytest.mark.parametrize("role", REFUSED_ROLES, ids=[repr(r) for r in REFUSED_ROLES])
async def test_every_other_role_is_refused_and_changes_nothing(built, engines, seeded, role):
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    engine = engines()
    statements = _statements(engine)
    response = await _put(_app(_user(role, seeded["a"]["tenant"], seeded["a"]["users"]["admin"]), engine), job)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Only tenant admins and recruiters can edit evaluation criteria"
    assert statements == [], "the role must be checked before any SQL runs"
    assert _snapshot(built, job) == before


# ---- tenant isolation and missing data are unchanged ----

async def test_cross_tenant_access_is_not_found_and_changes_nothing(built, engines, seeded):
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    response = await _put(_app(_user("admin", seeded["b"]["tenant"], seeded["b"]["users"]["admin"]), engines()), job)
    assert response.status_code == 404 and response.json()["detail"] == "Job not found"
    assert _snapshot(built, job) == before


async def test_cross_tenant_access_is_refused_under_row_level_security(built, engines, seeded):
    """A non-owner login role, so row-level security applies on top of the route's own tenant filter."""
    job = seeded["a"]["job"]
    before = _snapshot(built, job)
    response = await _put(_app(_user("admin", seeded["b"]["tenant"], seeded["b"]["users"]["admin"]),
                               engines(PROBE_ROLE)), job)
    assert response.status_code == 404 and response.json()["detail"] == "Job not found"
    assert _snapshot(built, job) == before


async def test_the_owning_tenant_still_updates_under_row_level_security(built, engines, seeded):
    """Positive control for the test above: the same non-owner role updates its own tenant's criteria."""
    job, admin = seeded["a"]["job"], seeded["a"]["users"]["admin"]
    response = await _put(_app(_user("admin", seeded["a"]["tenant"], admin), engines(PROBE_ROLE)), job)
    assert response.status_code == 200, response.text


async def test_a_missing_job_is_not_found(built, engines, seeded):
    response = await _put(_app(_user("admin", seeded["a"]["tenant"], seeded["a"]["users"]["admin"]), engines()),
                          str(uuid.uuid4()))
    assert response.status_code == 404 and response.json()["detail"] == "Job not found"


async def test_a_job_without_criteria_is_not_found(built, engines, seeded):
    job = _job(built, seeded["a"]["tenant"], seeded["a"]["users"]["admin"], with_criteria=False)
    response = await _put(_app(_user("admin", seeded["a"]["tenant"], seeded["a"]["users"]["admin"]), engines()), job)
    assert response.status_code == 404 and response.json()["detail"] == "Criteria not found for this job"
