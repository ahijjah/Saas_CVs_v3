"""Legacy criteria status (L1) on a real PostgreSQL 16 built from the full migration chain.

The worker, the retry route, the copy-of-the-enqueue compensation and the two legacy PUT routes run against the chain as the runtime
role cv_app, which is subject to row-level security (the cluster's superuser is used only to set up and inspect rows). Only the
authenticated user, the session factory, the task's broker call, the AI call and the database settings are replaced.

Covers: the claim compare-and-set, the token-guarded results, a discarded result after a lost claim, a v2 row that is never claimed
or overwritten, the Celery retry release, exhaustion, a duplicate delivery, the retry claim (single enqueue under concurrency), stale
recovery, the retry refusals, the publication failure compensation, and the legacy PUT guards."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import config
from auth.dependencies import CurrentUser, get_current_user
from database import get_db, set_rls_context
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from routers import jobs as jobs_router
from services import ai_service
from services import criteria_status as CS
from workers import criteria_worker as worker

RUNTIME_ROLE = "cv_app"
JD = ("Senior Python developer to build REST APIs, design PostgreSQL schemas and mentor two engineers. "
      "Five years of Python required.")
DEFAULT_WEIGHTS = (30, 25, 15, 10, 10, 5, 5)
WEIGHT_BODY = {"weight_skills": 40, "weight_experience": 20, "weight_education": 10, "weight_certifications": 10,
               "weight_soft_skills": 10, "weight_domain_knowledge": 5, "weight_other": 5}
SUFFICIENT = {"scoreability": {"status": "scoreable"}, "skills": {"required": ["Python"]}}
INSUFFICIENT = {"scoreability": {"status": "insufficient", "reason": "AI SECRET REASON"}}


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


@pytest.fixture(scope="module", autouse=True)
def chain_read_grants(built):
    """TEST SETUP ONLY, not production evidence. The migrated chain grants cv_app no privilege on these tables (a separately
    recorded grant defect), and the job list and details routes read them. Only SELECT is granted, only on these tables, only for
    this module. Every other table keeps the grant the chain actually gives cv_app."""
    cur = built.conn.cursor()
    cur.execute(f"GRANT SELECT ON cv_analyzer.client_organizations, cv_analyzer.job_campaigns,"
                f" cv_analyzer.job_knockout_questions TO {RUNTIME_ROLE}")


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


def _job(built, tenant, *, status="pending", error=None, retry=0, desc_hash=None, schema=None, analysis=None,
         minutes_ago=None, description=JD) -> str:
    """A job with a criteria row in the given state. `minutes_ago` back-dates updated_at (triggers are off for this write only)."""
    cur = _cur(built)
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) "
                "SELECT %s, user_id, 't', %s FROM users WHERE tenant_id = %s LIMIT 1 RETURNING job_id",
                (tenant["tenant"], description, tenant["tenant"]))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status) VALUES (%s, 'pending')", (job,))
    _set(built, job, status=status, error=error, retry=retry, desc_hash=desc_hash, schema=schema, analysis=analysis,
         minutes_ago=minutes_ago)
    return job


def _set(built, job, *, status, error=None, retry=0, desc_hash=None, schema=None, analysis=None, minutes_ago=None):
    cur = _cur(built)
    cur.execute("SET session_replication_role = replica")
    try:
        cur.execute(
            "UPDATE job_criteria SET criteria_extraction_status = %s, criteria_extraction_error = %s,"
            " criteria_extraction_retry_count = %s, criteria_last_failed_description_hash = %s,"
            " requirements_schema_version = %s, analysis_json = COALESCE(%s::jsonb, analysis_json),"
            " updated_at = now() - make_interval(mins => %s) WHERE job_id = %s",
            (status, error, retry, desc_hash, schema, json.dumps(analysis) if analysis is not None else None,
             minutes_ago or 0, job))
    finally:
        cur.execute("SET session_replication_role = origin")


def _row(built, job):
    cur = _cur(built)
    cur.execute("SELECT criteria_extraction_status, criteria_extraction_error, criteria_extraction_retry_count,"
                " criteria_last_failed_description_hash, requirements_schema_version, analysis_json,"
                " weight_skills, weight_experience, weight_education, weight_certifications, weight_soft_skills,"
                " weight_domain_knowledge, weight_other, skills, updated_at"
                " FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _max_retries(built) -> int:
    cur = _cur(built)
    cur.execute("SELECT value FROM system_config WHERE key = 'criteria_extraction_max_retries'")
    return int(cur.fetchone()[0])


@pytest.fixture(scope="module")
def dsn(built):
    return f"postgresql+asyncpg://postgres@127.0.0.1:{built.cluster.port}/{built.name}"


@pytest_asyncio.fixture()
async def engine(built):
    eng = create_async_engine(f"postgresql+asyncpg://{RUNTIME_ROLE}@127.0.0.1:{built.cluster.port}/{built.name}",
                              poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    yield eng
    await eng.dispose()


@pytest.fixture()
def worker_settings(monkeypatch, dsn):
    """The worker and its helpers read the database and the model from config. Both are pointed at this test database, and no AI
    model is resolved (the registry lookup returns None, as an unconfigured stage does)."""
    fake = SimpleNamespace(database_url=dsn, db_schema="cv_analyzer", openai_model="test-model")
    monkeypatch.setattr(config, "get_settings", lambda: fake)

    async def no_registry(db, stage):
        return None

    import services.ai_model_registry_service as registry
    monkeypatch.setattr(registry, "resolve_stage_client", no_registry)
    return fake


def _session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _patch_ai(monkeypatch, *, analysis=None, exc=None, during=None):
    """Replace the AI call. `during` runs before the call returns, to simulate another writer between claim and result."""
    async def fake(description, prompt_override=None, openai_client=None):
        if during is not None:
            during()
        if exc is not None:
            raise exc
        return analysis

    monkeypatch.setattr(ai_service, "extract_job_criteria", fake)


# ---- the claim and the result, through the worker's own functions ----

@pytest.mark.asyncio
async def test_the_claim_moves_pending_to_processing_once(built, engine):
    tenant = _tenant(built)
    job = _job(built, tenant)
    async with _session_factory(engine)() as db:
        await set_rls_context(db, "", "super_admin")        # as _extract_async does before its claim
        first = await worker._claim_pending(db, job)
        await db.commit()
    async with _session_factory(engine)() as db:
        await set_rls_context(db, "", "super_admin")
        second = await worker._claim_pending(db, job)
        await db.commit()
    assert first is not None and second is None
    assert _row(built, job)[0] == "processing"


@pytest.mark.asyncio
async def test_a_sufficient_result_completes_under_its_own_claim(built, engine, worker_settings, monkeypatch):
    tenant = _tenant(built)
    job = _job(built, tenant)
    _patch_ai(monkeypatch, analysis=SUFFICIENT)
    await worker._extract_async(job, JD, _session_factory(engine), {})
    stored = _row(built, job)
    assert stored[0] == "completed" and stored[1] is None


@pytest.mark.asyncio
async def test_an_insufficient_result_is_failed_with_the_fixed_message_and_no_ai_text(built, engine, worker_settings, monkeypatch):
    tenant = _tenant(built)
    job = _job(built, tenant)
    _patch_ai(monkeypatch, analysis=INSUFFICIENT)
    await worker._extract_async(job, JD, _session_factory(engine), {})
    status, error, _retry, desc_hash, *_ = _row(built, job)
    assert status == "failed"
    assert error == CS.INSUFFICIENT_MESSAGE
    assert "AI SECRET" not in error
    assert desc_hash == worker._normalize_description_hash(JD)


@pytest.mark.asyncio
async def test_a_result_is_discarded_when_the_claim_was_lost_during_extraction(built, engine, worker_settings, monkeypatch):
    tenant = _tenant(built)
    job = _job(built, tenant)

    def stale_recovery():
        _set(built, job, status="failed", error=CS.WORKER_FAILED_MESSAGE, retry=1)

    _patch_ai(monkeypatch, analysis=SUFFICIENT, during=stale_recovery)
    await worker._extract_async(job, JD, _session_factory(engine), {})
    status, error, retry, *_ = _row(built, job)
    assert (status, error, retry) == ("failed", CS.WORKER_FAILED_MESSAGE, 1)
    assert _row(built, job)[5] is None          # the AI result was not written


@pytest.mark.asyncio
async def test_a_requirements_v2_row_is_never_claimed(built, engine, worker_settings, monkeypatch):
    tenant = _tenant(built)
    analysis = {"requirements": {"schema_version": 2}}
    job = _job(built, tenant, schema=2, analysis=analysis)
    called = []
    _patch_ai(monkeypatch, analysis=SUFFICIENT, during=lambda: called.append(1))
    await worker._extract_async(job, JD, _session_factory(engine), {})
    assert called == []
    stored = _row(built, job)
    assert stored[0] == "pending" and stored[4] == 2 and stored[5] == analysis


@pytest.mark.asyncio
async def test_a_row_that_becomes_requirements_v2_during_extraction_is_not_overwritten(built, engine, worker_settings, monkeypatch):
    tenant = _tenant(built)
    job = _job(built, tenant, analysis={"keep": True})

    def become_v2():
        cur = _cur(built)
        cur.execute("UPDATE job_criteria SET requirements_schema_version = 2 WHERE job_id = %s", (job,))

    _patch_ai(monkeypatch, analysis=SUFFICIENT, during=become_v2)
    await worker._extract_async(job, JD, _session_factory(engine), {})
    stored = _row(built, job)
    assert stored[4] == 2 and stored[5] == {"keep": True}
    assert stored[0] != "completed"


@pytest.mark.asyncio
async def test_a_duplicate_delivery_does_no_work_while_the_row_is_processing(built, engine, worker_settings, monkeypatch):
    tenant = _tenant(built)
    job = _job(built, tenant, status="processing")
    called = []
    _patch_ai(monkeypatch, analysis=SUFFICIENT, during=lambda: called.append(1))
    await worker._extract_async(job, JD, _session_factory(engine), {})
    assert called == []
    assert _row(built, job)[0] == "processing"


# ---- the Celery task: retry release and exhaustion ----

# The Celery task runs its own event loop, so these tests are synchronous. They run the task through Celery's apply(), which
# follows the real bound-task retry path: self.retry raises Retry, the task is re-run, and an exhausted task ends as FAILURE.

def _spy_retries(monkeypatch, built, job):
    """Record the row's status at each moment a retry is requested (the retry itself is still Celery's)."""
    task = worker.extract_criteria_task
    original_retry = task.retry
    seen = []

    def spy(*args, **kwargs):
        seen.append(_row(built, job)[0])
        return original_retry(*args, **kwargs)

    monkeypatch.setattr(task, "retry", spy)
    return seen


def _failing_ai(monkeypatch, calls):
    async def fake(description, prompt_override=None, openai_client=None):
        calls.append(1)
        raise RuntimeError("provider secret detail")

    monkeypatch.setattr(ai_service, "extract_job_criteria", fake)


def test_intermediate_failures_release_to_pending_and_the_final_failure_ends_failed(built, worker_settings, monkeypatch):
    tenant = _tenant(built)
    job = _job(built, tenant)
    seen = _spy_retries(monkeypatch, built, job)
    calls = []
    _failing_ai(monkeypatch, calls)
    result = worker.extract_criteria_task.apply(args=(job, JD))
    assert seen == ["pending", "pending"]         # each intermediate attempt released its row before the retry was requested
    assert len(calls) == 3                        # max_retries = 2: three attempts in all
    assert result.state == "FAILURE"              # the task is reported as failed, not successful
    status, error, *_ = _row(built, job)
    assert status == "failed" and error == CS.WORKER_FAILED_MESSAGE
    assert "provider secret" not in error
    assert "provider secret" not in str(result.result)


def test_a_retried_attempt_claims_the_released_row_and_can_complete(built, worker_settings, monkeypatch):
    tenant = _tenant(built)
    job = _job(built, tenant)
    seen = _spy_retries(monkeypatch, built, job)
    attempts = []

    async def flaky(description, prompt_override=None, openai_client=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return SUFFICIENT

    monkeypatch.setattr(ai_service, "extract_job_criteria", flaky)
    result = worker.extract_criteria_task.apply(args=(job, JD))
    assert seen == ["pending"]
    assert len(attempts) == 2
    assert result.state == "SUCCESS"
    assert _row(built, job)[0] == "completed"


async def _with_runtime_session(dsn, action):
    engine = create_async_engine(dsn, poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    try:
        return await action(async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
    finally:
        await engine.dispose()


def _mark_failed_as_runtime(runtime_dsn, job, token):
    async def action(Session):
        return await worker._mark_failed(job, token, Session)

    return asyncio.run(_with_runtime_session(runtime_dsn, action))


@pytest.fixture()
def runtime_dsn(built):
    return f"postgresql+asyncpg://{RUNTIME_ROLE}@127.0.0.1:{built.cluster.port}/{built.name}"


def test_a_stale_claim_cannot_mark_the_row_failed(built, runtime_dsn):
    tenant = _tenant(built)
    job = _job(built, tenant, status="processing")
    current = _row(built, job)[14]
    marked = _mark_failed_as_runtime(runtime_dsn, job, current - timedelta(microseconds=1))
    assert marked is False
    assert _row(built, job)[0] == "processing"


def test_a_pending_row_is_never_marked_failed_even_with_a_matching_token(built, runtime_dsn):
    tenant = _tenant(built)
    job = _job(built, tenant, status="pending")
    token = _row(built, job)[14]
    assert _mark_failed_as_runtime(runtime_dsn, job, token) is False
    assert _row(built, job)[0] == "pending"


def test_the_matching_claim_marks_the_row_failed_with_the_fixed_message(built, runtime_dsn):
    tenant = _tenant(built)
    job = _job(built, tenant, status="processing")
    token = _row(built, job)[14]
    assert _mark_failed_as_runtime(runtime_dsn, job, token) is True
    status, error, *_ = _row(built, job)
    assert (status, error) == ("failed", CS.WORKER_FAILED_MESSAGE)


# ---- the retry route ----

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


@pytest.fixture()
def enqueued(monkeypatch):
    calls = []
    monkeypatch.setattr(worker.extract_criteria_task, "delay", lambda job_id, description: calls.append(job_id))
    return calls


@pytest.mark.asyncio
async def test_a_failed_row_is_retried_once_with_its_error_cleared(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, status="failed", error=CS.WORKER_FAILED_MESSAGE, retry=0)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 202
    assert response.json()["criteria_extraction_retry_count"] == 1
    status, error, retry, *_ = _row(built, job)
    assert (status, error, retry) == ("pending", None, 1)
    assert enqueued == [job]


@pytest.mark.asyncio
async def test_concurrent_retries_enqueue_once(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, status="failed", error=CS.WORKER_FAILED_MESSAGE, retry=0)
    app = _app(_user(tenant), engine)
    async with _client(app) as first, _client(app) as second:
        a, b = await asyncio.gather(first.post(f"/jobs/{job}/criteria/retry"), second.post(f"/jobs/{job}/criteria/retry"))
    assert sorted([a.status_code, b.status_code]) == [202, 400]
    assert enqueued == [job]
    assert _row(built, job)[2] == 1


@pytest.mark.asyncio
async def test_a_recent_processing_row_cannot_be_reclaimed_and_is_unchanged(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, status="processing", retry=0)
    before = _row(built, job)[:3]
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 400
    assert "processing" in response.json()["detail"]
    assert _row(built, job)[:3] == before and enqueued == []


@pytest.mark.asyncio
async def test_a_stale_processing_row_is_recovered_and_claimed_once(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, status="processing", retry=0, minutes_ago=31)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 202
    status, error, retry, *_ = _row(built, job)
    assert (status, error, retry) == ("pending", None, 1)
    assert enqueued == [job]


@pytest.mark.asyncio
async def test_the_retry_cap_is_refused_without_writing_blocked(built, engine, enqueued):
    tenant = _tenant(built)
    cap = _max_retries(built)
    job = _job(built, tenant, status="failed", error=CS.WORKER_FAILED_MESSAGE, retry=cap)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 400
    assert response.json()["detail"] == "Maximum analysis retries reached for this campaign."
    status, error, retry, *_ = _row(built, job)
    assert (status, error, retry) == ("failed", CS.WORKER_FAILED_MESSAGE, cap)
    assert enqueued == []


@pytest.mark.asyncio
async def test_an_unchanged_insufficient_description_is_refused_without_changing_the_row(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, status="failed", error=CS.INSUFFICIENT_MESSAGE, retry=0,
               desc_hash=worker._normalize_description_hash(JD))
    before = _row(built, job)[:4]
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 400
    assert "more meaningful details" in response.json()["detail"]
    assert _row(built, job)[:4] == before and enqueued == []


@pytest.mark.asyncio
async def test_a_changed_description_after_an_insufficient_result_can_be_retried(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, status="failed", error=CS.INSUFFICIENT_MESSAGE, retry=0,
               desc_hash=worker._normalize_description_hash("an older description"))
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 202
    assert enqueued == [job]


@pytest.mark.asyncio
async def test_a_requirements_v2_job_is_refused_before_any_write(built, engine, enqueued):
    tenant = _tenant(built)
    job = _job(built, tenant, status="failed", error=CS.WORKER_FAILED_MESSAGE, retry=0, schema=2,
               analysis={"requirements": {"schema_version": 2}})
    before = _row(built, job)[:5]
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 409
    assert _row(built, job)[:5] == before and enqueued == []


@pytest.mark.asyncio
async def test_a_publication_failure_moves_the_claimed_attempt_to_failed_and_returns_503(built, engine, monkeypatch):
    tenant = _tenant(built)
    job = _job(built, tenant, status="failed", error=CS.WORKER_FAILED_MESSAGE, retry=0)

    def refuse(job_id, description):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(worker.extract_criteria_task, "delay", refuse)
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 503
    status, error, retry, *_ = _row(built, job)
    assert (status, error, retry) == ("failed", CS.ENQUEUE_FAILED_MESSAGE, 1)


@pytest.mark.asyncio
async def test_the_enqueue_compensation_moves_only_the_claimed_pending_attempt(built, engine):
    tenant = _tenant(built)
    pending = _job(built, tenant, status="pending", retry=2)
    v2 = _job(built, tenant, status="pending", retry=0, schema=2, analysis={"requirements": {}})
    async with _session_factory(engine)() as db:
        await set_rls_context(db, tenant["tenant"], "admin")    # as create_job and the retry route do
        wrong_count = await db.execute(text(jobs_router._ENQUEUE_FAILED_SQL),
                                       {"jid": pending, "claimed": 1, "message": CS.ENQUEUE_FAILED_MESSAGE})
        v2_write = await db.execute(text(jobs_router._ENQUEUE_FAILED_SQL),
                                    {"jid": v2, "claimed": 0, "message": CS.ENQUEUE_FAILED_MESSAGE})
        right_count = await db.execute(text(jobs_router._ENQUEUE_FAILED_SQL),
                                       {"jid": pending, "claimed": 2, "message": CS.ENQUEUE_FAILED_MESSAGE})
        await db.commit()
    assert (wrong_count.rowcount, v2_write.rowcount, right_count.rowcount) == (0, 0, 1)
    assert _row(built, pending)[0] == "failed"
    assert _row(built, v2)[0] == "pending"


# ---- the legacy PUT routes refuse requirements-v2 rows ----

@pytest.mark.asyncio
async def test_put_criteria_refuses_a_requirements_v2_row_and_leaves_it_unchanged(built, engine):
    tenant = _tenant(built)
    job = _job(built, tenant, status="completed", schema=2, analysis={"requirements": {"schema_version": 2}})
    before = _row(built, job)[:7]
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.put(f"/jobs/{job}/criteria", json=WEIGHT_BODY)
    assert response.status_code == 409
    assert _row(built, job)[:7] == before


@pytest.mark.asyncio
async def test_put_criteria_content_refuses_a_requirements_v2_row_and_leaves_it_unchanged(built, engine):
    tenant = _tenant(built)
    analysis = {"requirements": {"schema_version": 2}, "skills": {"required": ["Go"]}}
    job = _job(built, tenant, status="completed", schema=2, analysis=analysis)
    before = _row(built, job)[5]
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.put(f"/jobs/{job}/criteria/content", json={"required_skills": ["Python"]})
    assert response.status_code == 409
    assert _row(built, job)[5] == before


@pytest.mark.asyncio
async def test_put_criteria_content_still_updates_a_legacy_row(built, engine):
    tenant = _tenant(built)
    job = _job(built, tenant, status="completed", analysis={"skills": {"required": ["Go"]}})
    async with _client(_app(_user(tenant), engine)) as client:
        response = await client.put(f"/jobs/{job}/criteria/content", json={"required_skills": ["Python"]})
    assert response.status_code == 200
    assert _row(built, job)[5]["skills"]["required"] == ["Python"]


# ---- the API reports the derived status, from the stored status and the message ----

@pytest.mark.asyncio
@pytest.mark.parametrize("stored, error, at_cap, expected", [
    ("failed", CS.INSUFFICIENT_MESSAGE, False, "insufficient"),
    ("failed", CS.WORKER_FAILED_MESSAGE, True, "blocked"),
    ("failed", CS.WORKER_FAILED_MESSAGE, False, "failed"),
    ("pending", None, False, "pending"),
])
async def test_the_job_list_and_details_report_the_derived_status(built, engine, stored, error, at_cap, expected):
    tenant = _tenant(built)
    retry = _max_retries(built) if at_cap else 0
    job = _job(built, tenant, status=stored, error=error, retry=retry)
    async with _client(_app(_user(tenant), engine)) as client:
        listing = await client.get("/jobs")
        details = await client.get("/jobs/details", params={"job_id": job})
    assert listing.status_code == 200 and details.status_code == 200, (listing.text, details.text)
    listed = [j for j in listing.json() if j["job_id"] == job]
    assert [j["criteria_extraction_status"] for j in listed] == [expected]
    assert details.json()["details"]["criteria_extraction_status"] == expected


# ---- authorization: only admin and recruiter (exact match), before any SQL ----

def _count_sql(engine):
    """Every statement the engine runs is recorded. A refused request must record none."""
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    return statements


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "recruiter"])
async def test_the_retry_route_allows_admin_and_recruiter(built, engine, enqueued, role):
    tenant = _tenant(built)
    job = _job(built, tenant, status="failed", error=CS.WORKER_FAILED_MESSAGE, retry=0)
    async with _client(_app(_user(tenant, role), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 202
    assert enqueued == [job]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, "", "owner", "Admin", "ADMIN", "Recruiter", "viewer", "super_admin", "hr_manager"])
async def test_the_retry_route_refuses_every_other_role_before_any_sql(built, engine, enqueued, role):
    tenant = _tenant(built)
    job = _job(built, tenant, status="failed", error=CS.WORKER_FAILED_MESSAGE, retry=0)
    before = _row(built, job)[:3]
    statements = _count_sql(engine)
    async with _client(_app(_user(tenant, role), engine)) as client:
        response = await client.post(f"/jobs/{job}/criteria/retry")
    assert response.status_code == 403
    assert statements == []
    assert _row(built, job)[:3] == before and enqueued == []
