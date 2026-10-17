"""Copy-to-job (candidate_reuse), the submission_source default removal, score-pending, and the four intake methods, on a real
PostgreSQL 16 built from the full migration chain.

Routes and the intake pipeline run as the runtime role cv_app (subject to row-level security). The cluster's superuser sets up and
inspects rows only. The scoring task's broker call is replaced by a recorder, so nothing is enqueued for real."""
from __future__ import annotations

import uuid

import httpx
import psycopg2
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from auth.dependencies import CurrentUser, get_current_user
from database import get_db, set_rls_context
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from routers import applications as applications_router
from routers import candidate_tags as candidate_router
from services import criteria_status as CS
from services.application_intake_service import process_cv_intake
from workers import cv_score

RUNTIME_ROLE = "cv_app"
JD = ("Senior Python developer to build REST APIs, design PostgreSQL schemas and mentor two engineers. "
      "Five years of Python required.")
PDF = b"%PDF-1.4 minimal candidate cv for the intake gate test"


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


def _cur(built):
    return built.conn.cursor()


def _tenant(built):
    cur = _cur(built)
    tag = uuid.uuid4().hex[:10]
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id",
                (f"tenant {tag}", f"{tag}.example"))
    tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) "
                "VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id", (tenant, f"{tag}@example.test"))
    return {"tenant": tenant, "user": str(cur.fetchone()[0])}


def _job(built, tenant, *, status="active", criteria="completed") -> str:
    cur = _cur(built)
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, status) VALUES (%s, %s, 't', %s, %s)"
                " RETURNING job_id", (tenant["tenant"], tenant["user"], JD, status))
    job = str(cur.fetchone()[0])
    if criteria is not None:
        cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status) VALUES (%s, %s)", (job, criteria))
    return job


def _application(built, tenant, job, *, source="manual_upload", processing="pending", email=None, with_file=True) -> str:
    cur = _cur(built)
    cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, candidate_email, submission_source,"
                " processing_status) VALUES (%s, %s, 'Candidate', %s, %s, %s) RETURNING application_id",
                (job, tenant["tenant"], email or f"{uuid.uuid4().hex[:8]}@example.test", source, processing))
    app = str(cur.fetchone()[0])
    if with_file:
        cur.execute("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path)"
                    " VALUES (%s, %s, 'cv.pdf', 'application/pdf', %s)", (app, tenant["tenant"], f"tenants/x/{app}.pdf"))
    return app


@pytest.fixture(scope="module", autouse=True)
def runtime_grants(built):
    """schema.sql grants the runtime role every table that exists at that point. Tables created by later migrations (for example
    tenant_usage_monthly) have no grant in the chain, so the runtime role is granted the schema as schema.sql intends."""
    cur = _cur(built)
    cur.execute(f"GRANT USAGE ON SCHEMA cv_analyzer TO {RUNTIME_ROLE}")
    cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA cv_analyzer TO {RUNTIME_ROLE}")
    cur.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA cv_analyzer TO {RUNTIME_ROLE}")


@pytest_asyncio.fixture()
async def engine(built):
    eng = create_async_engine(f"postgresql+asyncpg://{RUNTIME_ROLE}@127.0.0.1:{built.cluster.port}/{built.name}",
                              poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    yield eng
    await eng.dispose()


def _session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _app(user, engine):
    app = FastAPI()
    app.include_router(candidate_router.app_router)
    app.include_router(applications_router.router)
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app


def _user(tenant):
    return CurrentUser(user_id=tenant["user"], tenant_id=tenant["tenant"], email="u@example.test", role="admin", full_name="u")


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture()
def scored(monkeypatch):
    calls = []
    monkeypatch.setattr(cv_score.score_cv_task, "delay", lambda **kwargs: calls.append(kwargs["application_id"]))
    return calls


# ---- the migration: default removed, candidate_reuse allowed ----

def test_the_submission_source_default_is_removed_and_candidate_reuse_is_allowed(built):
    cur = _cur(built)
    cur.execute("SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d JOIN pg_attribute a"
                " ON a.attrelid = d.adrelid AND a.attnum = d.adnum"
                " WHERE d.adrelid = 'cv_analyzer.applications'::regclass AND a.attname = 'submission_source'")
    assert cur.fetchall() == []
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'applications_submission_source_check'")
    definition = cur.fetchone()[0]
    assert "candidate_reuse" in definition and "'email'" not in definition


def test_an_insert_that_omits_submission_source_is_refused_and_email_is_refused(built):
    tenant = _tenant(built)
    job = _job(built, tenant)
    conn = built.conn
    conn.autocommit = False
    try:
        cur = conn.cursor()
        for sql, params, code in (
            ("INSERT INTO applications (job_id, tenant_id, candidate_name) VALUES (%s, %s, 'x')", (job, tenant["tenant"]), "23502"),
            ("INSERT INTO applications (job_id, tenant_id, candidate_name, submission_source) VALUES (%s, %s, 'x', 'email')",
             (job, tenant["tenant"]), "23514"),
        ):
            cur.execute("SAVEPOINT p")
            with pytest.raises(psycopg2.Error) as excinfo:
                cur.execute(sql, params)
            assert excinfo.value.pgcode == code
            cur.execute("ROLLBACK TO SAVEPOINT p")
    finally:
        conn.rollback()
        conn.autocommit = True


# ---- copy-to-job ----

@pytest.mark.asyncio
async def test_a_copy_to_an_active_job_with_complete_criteria_inserts_a_candidate_reuse_row_and_scores_nothing(built, engine, scored):
    tenant = _tenant(built)
    source_job = _job(built, tenant)
    target = _job(built, tenant)
    source = _application(built, tenant, source_job, email="copy@example.test")
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 200, response.text
    new_id = response.json()["new_application_id"]
    cur = _cur(built)
    cur.execute("SELECT job_id, tenant_id, submission_source, workflow_status, processing_status FROM applications"
                " WHERE application_id = %s", (new_id,))
    row = cur.fetchone()
    assert (str(row[0]), str(row[1]), row[2], row[3], row[4]) == (
        target, tenant["tenant"], "candidate_reuse", "awaiting_review", "pending")
    assert scored == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target_status, criteria", [
    ("closed", "completed"),
    ("active", "failed"),
    ("active", "pending"),
    ("active", "processing"),
])
async def test_a_copy_is_refused_for_an_inactive_target_or_incomplete_criteria(built, engine, scored, target_status, criteria):
    tenant = _tenant(built)
    source = _application(built, tenant, _job(built, tenant), email=f"refused{uuid.uuid4().hex[:6]}@example.test")
    target = _job(built, tenant, status=target_status, criteria=criteria)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 409
    cur = _cur(built)
    cur.execute("SELECT count(*) FROM applications WHERE job_id = %s", (target,))
    assert cur.fetchone()[0] == 0
    assert scored == []


@pytest.mark.asyncio
async def test_a_copy_to_another_tenants_job_is_not_found(built, engine, scored):
    tenant = _tenant(built)
    other = _tenant(built)
    source = _application(built, tenant, _job(built, tenant))
    foreign = _job(built, other)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": foreign})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_duplicate_candidate_on_the_target_is_still_refused(built, engine, scored):
    tenant = _tenant(built)
    email = f"dup{uuid.uuid4().hex[:6]}@example.test"
    source = _application(built, tenant, _job(built, tenant), email=email)
    target = _job(built, tenant)
    _application(built, tenant, target, email=email)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/applications/{source}/reuse-to-job", json={"target_job_id": target})
    assert response.status_code == 409


# ---- score-pending never selects a copy ----

@pytest.mark.asyncio
async def test_score_pending_enqueues_the_manual_upload_and_never_the_copy(built, engine, scored):
    tenant = _tenant(built)
    job = _job(built, tenant)
    manual = _application(built, tenant, job, source="manual_upload")
    copied = _application(built, tenant, job, source="candidate_reuse")
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post("/applications/score-pending", json={"job_id": job})
    assert response.status_code == 202, response.text
    assert scored == [manual]
    assert copied not in scored


# ---- the four intake methods refuse failed criteria ----

INTAKE_METHODS = [
    ("manual_upload", "manual_upload"),
    ("public_apply", "public_apply"),
    ("email_forwarding", "email_forwarding"),
    ("platform_email", "platform_email"),
]


async def _intake(engine, tenant, job, method, source, tmp_path):
    async with AsyncSession(engine) as db:
        await set_rls_context(db, tenant["tenant"], "admin")
        result = await process_cv_intake(
            db, intake_method=method, job_id=job, tenant_id=tenant["tenant"], candidate_name="Candidate",
            candidate_email=f"{uuid.uuid4().hex[:8]}@example.test", content_type="application/pdf", content=PDF,
            original_filename="cv.pdf", submission_source=source, auto_score=False,
            files_base_path=str(tmp_path), max_file_size_mb=10)
        return result


def _applications_for(built, job) -> int:
    cur = _cur(built)
    cur.execute("SELECT count(*) FROM applications WHERE job_id = %s", (job,))
    return cur.fetchone()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("method, source", INTAKE_METHODS)
@pytest.mark.parametrize("criteria", ["failed", "pending", "processing"])
async def test_each_intake_method_refuses_a_job_whose_criteria_are_not_complete(built, engine, tmp_path, method, source, criteria):
    tenant = _tenant(built)
    job = _job(built, tenant, criteria=criteria)
    result = await _intake(engine, tenant, job, method, source, tmp_path)
    assert result.status == "INTAKE_BLOCKED", result.status
    assert _applications_for(built, job) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method, source", INTAKE_METHODS)
async def test_each_intake_method_accepts_a_job_with_completed_criteria(built, engine, tmp_path, method, source):
    """The control: the same call proceeds past the criteria gate when the criteria are complete."""
    tenant = _tenant(built)
    job = _job(built, tenant, criteria="completed")
    result = await _intake(engine, tenant, job, method, source, tmp_path)
    assert result.status != "INTAKE_BLOCKED", result.status
