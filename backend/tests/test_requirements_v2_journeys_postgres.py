"""End-to-end requirements-v2 journeys on a real PostgreSQL 16 chain, through the real HTTP routes, the real extraction worker and
the real scoring pipeline. Only the model call, the Celery broker and the external components of the scorer are replaced.

A. English: create the job through POST /jobs -> the v2 row -> the queued v2 task -> extraction -> GET requirements -> edit ->
   reorder -> ready -> scoring -> a score that records the revision used.
B. Arabic: the same journey on Arabic wording.
C. Invalid extraction -> failed state with no document -> retry through the route -> successful extraction.
D. A legacy job retries through the legacy task, and never through the v2 task.
E. Two concurrent retries -> one claim, one enqueue, one stored document.
F. A racing edit during scoring -> no score against mixed revisions (see the scoring pipeline suite for the write-time check)."""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import config
from auth.dependencies import CurrentUser, get_current_user
from database import get_db
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from routers import job_requirements
from routers import jobs as jobs_router
from services import ai_service
from services import criteria_status as CS
from services.requirements_v2 import contract as C
from workers import criteria_worker as legacy_worker
from workers import cv_score
from workers import requirements_v2_worker as v2_worker

from test_requirements_v2_scoring_pipeline_postgres import CV_TEXT, _gatekeeper_pass, _none  # shared fixtures' helpers

EN_JD = ("Senior Python developer.\nPython and SQL are required for every candidate.\n"
         "Five years of backend experience is preferred.\nThe role builds reporting APIs.")
AR_JD = ("مطور أول لبناء واجهات برمجية.\nمطلوب بايثون وإتقان لغة SQL لكل مرشح.\n"
         "خبرة سابقة في الذكاء الاصطناعي مفضلة.\nيعمل الفريق على تقارير الأداء.")
NOW = "2026-10-18T10:00:00+00:00"


def _response(jd, lang):
    if lang == "ar":
        skills = [{"text": "بايثون", "importance": "required", "source_text": "مطلوب بايثون وإتقان لغة SQL",
                   "origin": "stated"},
                  {"text": "SQL", "importance": "required", "source_text": "إتقان لغة SQL", "origin": "stated"}]
        experience = [{"text": "خبرة في الذكاء الاصطناعي", "importance": "preferred", "importance_cue": "مفضلة",
                       "origin": "stated", "source_text": "خبرة سابقة في الذكاء الاصطناعي مفضلة"}]
    else:
        skills = [{"text": "Python", "importance": "required", "source_text": "Python and SQL are required for every candidate",
                   "origin": "stated"},
                  {"text": "SQL", "importance": "required", "source_text": "Python and SQL are required for every candidate",
                   "origin": "stated"}]
        experience = [{"text": "Backend experience", "importance": "preferred", "importance_cue": "preferred", "origin": "stated",
                       "source_text": "Five years of backend experience is preferred"}]
    cats = {c: [] for c in C.CATEGORIES}
    cats["skills"], cats["experience"] = skills, experience
    return json.dumps({"categories": cats, "category_weights": {"skills": 100},
                       "scoreability": {"status": "scoreable"}}, ensure_ascii=False)


@pytest.fixture(scope="module")
def built():
    reason = unavailable_reason()
    if reason:
        pytest.skip(reason)
    with built_chain() as db:
        check_chain(db.failures)
        assert {f.file for f in db.failures} == EXPECTED_PRE_EXISTING_FAILURES
        yield db


@pytest.fixture()
def world(built, monkeypatch, tmp_path):
    """The real settings, pointed at this database; the broker captures tasks; the model returns the scripted responses."""
    dsn = f"postgresql+asyncpg://postgres@127.0.0.1:{built.cluster.port}/{built.name}"
    real = config.get_settings()
    monkeypatch.setattr(config, "get_settings", lambda: real.model_copy(update={
        "database_url": dsn, "db_schema": "cv_analyzer", "files_base_path": str(tmp_path)}))
    import services.ai_model_registry_service as registry
    monkeypatch.setattr(registry, "resolve_stage_client", _none)
    import services.local_processor as local_processor
    monkeypatch.setattr(local_processor, "run_gatekeeper", _gatekeeper_pass)
    import services.pdf_service as pdf_service
    monkeypatch.setattr(pdf_service, "extract_text_from_pdf", lambda data: CV_TEXT)
    import services.email_service as email_service
    monkeypatch.setattr(email_service, "send_cv_received_email", _none)
    monkeypatch.setattr(jobs_router, "can_create_campaign", _allowed)
    monkeypatch.setattr(v2_worker, "_resolve_model", _configured_model)
    queued = {"v2": [], "legacy": []}
    monkeypatch.setattr(v2_worker.extract_requirements_v2_task, "delay", lambda job_id, description: queued["v2"].append((job_id, description)))
    monkeypatch.setattr(legacy_worker.extract_criteria_task, "delay", lambda job_id, description: queued["legacy"].append(job_id))
    script = {"responses": []}

    async def model(description, resolved):
        if not script["responses"]:
            raise AssertionError("the model was called more times than the journey scripted")
        return script["responses"].pop(0), "stop"

    monkeypatch.setattr(v2_worker, "_request_extraction", model)
    scored = []
    world_hooks = {"during": None}

    async def fake_score(cv_text, criteria, job_title, **kwargs):
        scored.append(criteria)
        if world_hooks["during"] is not None:
            await world_hooks["during"]()
        return {"score_skills": 80, "score_experience": 70, "score_education": 60, "score_certifications": 50,
                "score_soft_skills": 50, "score_domain_knowledge": 50, "score_other": 50, "strengths": [], "gaps_identified": [],
                "red_flags": [], "evaluation_notes": "fixture", "interview_questions": [], "reasoning": {}, "score_details": {}}, \
            {"model": "test-model", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "finish_reason": "stop"}

    monkeypatch.setattr(ai_service, "score_cv", fake_score)
    engine = create_async_engine(dsn, poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield SimpleNamespace(engine=engine, Session=Session, queued=queued, script=script, scored=scored, root=tmp_path, built=built,
                          hooks=world_hooks)


async def _allowed(*args, **kwargs):
    return {"allowed": True}


async def _configured_model(Session):
    return SimpleNamespace(model_name="test-model", client=None)


def _app(user, world):
    app = FastAPI()
    app.include_router(jobs_router.router)
    app.include_router(job_requirements.router)
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with AsyncSession(world.engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _user(built, role="admin"):
    cur = built.conn.cursor()
    tag = uuid.uuid4().hex[:8]
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"t {tag}", f"{tag}.example"))
    tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', %s) RETURNING user_id",
                (tenant, f"{tag}@example.test", role))
    return CurrentUser(user_id=str(cur.fetchone()[0]), tenant_id=tenant, email="u@example.test", role=role, full_name="u")


async def _run_worker(world, job_id, description):
    claim = {}
    await v2_worker._extract_async(job_id, description, world.Session, claim)


def _criteria(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT criteria_extraction_status, requirements_schema_version, requirements_revision, analysis_json,"
                " criteria_extraction_error FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _scores(built, app):
    cur = built.conn.cursor()
    cur.execute("SELECT final_score, requirements_revision_used FROM application_scores WHERE application_id = %s", (app,))
    return cur.fetchall()


def _application(built, tenant_id, user_id, job, root) -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, candidate_email, candidate_phone, submission_source,"
                " processing_status) VALUES (%s, %s, 'Jane', %s, '+100', 'manual_upload', 'pending') RETURNING application_id",
                (job, tenant_id, f"{uuid.uuid4().hex[:8]}@example.test"))
    app = str(cur.fetchone()[0])
    relative = f"tenants/{tenant_id}/jobs/{job}/{app}.pdf"
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / relative).write_bytes(b"%PDF-1.4 fixture")
    cur.execute("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path, file_size_bytes,"
                " extracted_text, extraction_status) VALUES (%s, %s, 'cv.pdf', 'application/pdf', %s, 16, %s, 'pending')",
                (app, tenant_id, relative, CV_TEXT))
    return app


async def _score(world, app, job, tenant_id):
    await cv_score._score_cv_async(app, job, tenant_id, str(world.root / f"tenants/{tenant_id}/jobs/{job}/{app}.pdf"),
                                   "application/pdf", {}, world.Session)


async def _create(world, user, title, description):
    async with _client(_app(user, world)) as client:
        response = await client.post("/jobs", json={"title": title, "description": description})
    assert response.status_code == 201, response.text
    return response.json()["job_id"]


# ---- A and B: a complete journey, English and Arabic ----

@pytest.mark.asyncio
@pytest.mark.parametrize("lang, jd", [("en", EN_JD), ("ar", AR_JD)])
async def test_a_new_job_is_extracted_reviewed_scored_and_its_score_records_the_revision(world, lang, jd):
    built = world.built
    user = _user(built)
    job = await _create(world, user, "Senior Developer", jd)

    # the queued task is the v2 task, and it carries the job id and the description
    assert [j for j, _ in world.queued["v2"]] == [job] and world.queued["legacy"] == []
    cur = built.conn.cursor()
    cur.execute("SELECT requirements_schema_version FROM job_criteria WHERE job_id = %s", (job,))
    assert cur.fetchone()[0] == 2

    world.script["responses"].append(_response(jd, lang))
    await _run_worker(world, job, jd)
    status, marker, revision, analysis, _ = _criteria(built, job)
    assert (status, marker, revision) == ("completed", 2, 1)

    async with _client(_app(user, world)) as client:
        view = (await client.get(f"/jobs/{job}/requirements")).json()
        assert view["requirements_revision"] == 1 and view["review"]["readiness"]["can_proceed"] is True
        items = view["document"]["categories"]["skills"]["items"]
        assert len(items) == 2 and all(i["source_text"] for i in items)
        assert "retired_item_ids" not in json.dumps(view) and "basis_hash" not in json.dumps(view)

        first, second = items[0]["id"], items[1]["id"]
        edit = await client.post(f"/jobs/{job}/requirements/edits", json={"client_revision": 1, "operations": [
            {"op": "reorder_items", "category": "skills", "item_ids": [second, first]}]})
        assert edit.status_code == 200 and edit.json()["requirements_revision"] == 2
        view = (await client.get(f"/jobs/{job}/requirements")).json()
        assert [i["id"] for i in view["document"]["categories"]["skills"]["items"]] == [second, first]

    app = _application(built, user.tenant_id, user.user_id, job, world.root)
    await _score(world, app, job, user.tenant_id)
    (final_score, revision_used), = _scores(built, app)
    assert revision_used == 2
    assert world.scored[0]["skills"] and len(world.scored[0]["skills"]) == 2


# ---- C: an invalid extraction leaves a failed state, and a retry succeeds ----

@pytest.mark.asyncio
async def test_an_invalid_extraction_leaves_a_failed_state_and_a_retry_through_the_route_succeeds(world, monkeypatch):
    built = world.built
    user = _user(built)
    job = await _create(world, user, "Senior Developer", EN_JD)
    world.script["responses"].extend(["{not json", "{not json", "{not json"])
    monkeypatch.setattr(v2_worker, "_task_engine_and_session", lambda: (world.engine, world.Session))
    await asyncio.to_thread(v2_worker.extract_requirements_v2_task.apply, args=(job, EN_JD))
    status, marker, revision, analysis, error = _criteria(built, job)
    assert (status, error, revision, analysis) == ("failed", CS.WORKER_FAILED_MESSAGE, 0, None)

    world.queued["v2"].clear()
    world.script["responses"].append(_response(EN_JD, "en"))
    async with _client(_app(user, world)) as client:
        retry = await client.post(f"/jobs/{job}/criteria/retry")
    assert retry.status_code == 202 and [j for j, _ in world.queued["v2"]] == [job]
    await _run_worker(world, job, EN_JD)
    status, marker, revision, analysis, error = _criteria(built, job)
    assert (status, revision) == ("completed", 1) and analysis["requirements"]["schema_version"] == 2


# ---- D: a legacy job keeps the legacy path ----

@pytest.mark.asyncio
async def test_a_legacy_job_retries_through_the_legacy_task_and_never_the_v2_task(world):
    built = world.built
    user = _user(built)
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, status) VALUES (%s, %s, 'Legacy', %s, 'active')"
                " RETURNING job_id", (user.tenant_id, user.user_id, EN_JD))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, criteria_extraction_error) VALUES (%s, 'failed', %s)",
                (job, CS.WORKER_FAILED_MESSAGE))
    async with _client(_app(user, world)) as client:
        retry = await client.post(f"/jobs/{job}/criteria/retry")
    assert retry.status_code == 202
    assert world.queued == {"v2": [], "legacy": [job]}


# ---- E: two concurrent retries produce one claim and one document ----

@pytest.mark.asyncio
async def test_two_concurrent_retries_produce_one_claim_and_one_stored_document(world):
    built = world.built
    user = _user(built)
    job = await _create(world, user, "Senior Developer", EN_JD)
    cur = built.conn.cursor()
    cur.execute("UPDATE job_criteria SET criteria_extraction_status = 'failed', criteria_extraction_error = %s WHERE job_id = %s",
                (CS.WORKER_FAILED_MESSAGE, job))
    world.queued["v2"].clear()
    app = _app(user, world)
    async with _client(app) as first, _client(app) as second:
        a, b = await asyncio.gather(first.post(f"/jobs/{job}/criteria/retry"), second.post(f"/jobs/{job}/criteria/retry"))
    assert sorted([a.status_code, b.status_code]) in ([202, 400], [202, 409])
    assert len(world.queued["v2"]) == 1
    world.script["responses"].append(_response(EN_JD, "en"))
    await _run_worker(world, job, EN_JD)
    status, marker, revision, analysis, _ = _criteria(built, job)
    assert (status, revision) == ("completed", 1)


# ---- F: an edit that lands while the score is being computed is never scored against mixed revisions ----

@pytest.mark.asyncio
async def test_an_edit_that_lands_during_scoring_is_refused_at_the_write_and_nothing_is_stored(world):
    from services.requirements_v2.scoring import RequirementsRevisionChanged
    built = world.built
    user = _user(built)
    job = await _create(world, user, "Senior Developer", EN_JD)
    world.script["responses"].append(_response(EN_JD, "en"))
    await _run_worker(world, job, EN_JD)
    item_id = _criteria(built, job)[3]["requirements"]["categories"]["skills"]["items"][0]["id"]

    async def edit_while_scoring():
        async with _client(_app(user, world)) as client:
            response = await client.post(f"/jobs/{job}/requirements/edits", json={"client_revision": 1, "operations": [
                {"op": "set_text", "item_id": item_id, "text": "Python 3"}]})
        assert response.status_code == 200

    world.hooks["during"] = edit_while_scoring
    app = _application(built, user.tenant_id, user.user_id, job, world.root)
    with pytest.raises(RequirementsRevisionChanged):
        await _score(world, app, job, user.tenant_id)
    assert _scores(built, app) == []
