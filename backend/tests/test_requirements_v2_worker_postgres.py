"""Requirements V2 extraction through the real worker persistence path, on a real PostgreSQL 16 built from the full chain.

The worker's claim, validation, persistence, release and final failure run unchanged, as the runtime role. Only the model call
(`worker._request_extraction`) is replaced, by deterministic fake responses. The English and Arabic responses quote the job
description exactly, as the pinned prompt asks.

Covers: a complete English and a complete Arabic document, the schema version, the initial revision, source-text preservation,
the readiness and classification review blocks, stable and unique ids that avoid retired ids, malformed and structurally invalid
output, retry after failure, retry after success, concurrent workers, cancellation, a lost claim, a database failure, tenant
isolation, and the untouched legacy row."""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import hashlib
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from database import set_rls_context
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from services import criteria_status as CS
from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.readiness import NEEDS_CLASSIFICATION_REVIEW, READY, compute_readiness
from workers import requirements_v2_worker as worker

PROBE_ROLE = "cv_v2_worker_probe"
EN_JD = "Senior Python developer.\nPython and SQL are required.\nFive years of backend experience is preferred."
AR_JD = "مطلوب مطور بايثون بخبرة في قواعد البيانات.\nمطلوب إتقان لغة SQL.\nخبرة سابقة في الذكاء الاصطناعي مفضلة."
CATEGORY_KEYS = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")


def _output(skills, experience=(), *, scoreability="scoreable", **extra) -> dict:
    cats = {c: [] for c in CATEGORY_KEYS}
    cats["skills"] = list(skills)
    cats["experience"] = list(experience)
    out = {"categories": cats, "category_weights": {"skills": 100}, "non_scoreable_requirements": [],
           "post_hiring_conditions": [], "informational_items": [], "warnings": [],
           "scoreability": {"status": scoreability, "reason": ""}}
    out.update(extra)
    return out


EN_OUTPUT = _output(
    [{"text": "Python", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"},
     {"text": "SQL", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"}],
    [{"text": "Backend experience", "importance": "preferred", "importance_cue": "preferred", "origin": "stated",
      "source_text": "Five years of backend experience is preferred",
      "experience": {"subject": "backend experience", "min_years": 5}}],
)
AR_OUTPUT = _output(
    [{"text": "بايثون", "importance": "required", "source_text": "مطلوب مطور بايثون بخبرة في قواعد البيانات", "origin": "stated"},
     {"text": "SQL", "importance": "required", "source_text": "مطلوب إتقان لغة SQL", "origin": "stated"}],
    [{"text": "خبرة في الذكاء الاصطناعي", "importance": "preferred", "importance_cue": "مفضلة", "origin": "stated",
      "source_text": "خبرة سابقة في الذكاء الاصطناعي مفضلة"}],
)
UNCUED_OUTPUT = _output(
    [{"text": "Python", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"}],
    [{"text": "Backend experience", "importance": "preferred", "importance_cue": "nice to have", "origin": "stated",
      "source_text": "Five years of backend experience is preferred"}],
)


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
        yield db


@pytest_asyncio.fixture()
async def engine(built):
    eng = create_async_engine(f"postgresql+asyncpg://{PROBE_ROLE}@127.0.0.1:{built.cluster.port}/{built.name}",
                              poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    yield eng
    await eng.dispose()


def _sessions(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _tenant(built) -> dict:
    cur = built.conn.cursor()
    tag = uuid.uuid4().hex[:10]
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"t {tag}", f"{tag}.example"))
    tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) "
                "VALUES (%s, %s, 'x', 'u', 'admin') RETURNING user_id", (tenant, f"{tag}@example.test"))
    return {"tenant": tenant, "user": str(cur.fetchone()[0])}


def _job(built, tenant, description, *, marker=2, analysis=None, status="pending", retired=None) -> str:
    """A job with a criteria row. marker 2 is a requirements-v2 row (pending by default); None is a legacy row."""
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description) VALUES (%s, %s, 't', %s) RETURNING job_id",
                (tenant["tenant"], tenant["user"], description))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, requirements_schema_version, analysis_json,"
                " requirements_retired_item_ids) VALUES (%s, %s, %s, %s, CAST(%s AS jsonb))",
                (job, status, marker, json.dumps(analysis) if analysis is not None else None, json.dumps(retired or [])))
    return job


def _row(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT criteria_extraction_status, criteria_extraction_error, requirements_schema_version,"
                " requirements_revision, analysis_json, original_analysis_json, requirements_retired_item_ids,"
                " weight_skills, weight_experience, ai_model FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _doc(built, job) -> dict:
    return _row(built, job)[4]["requirements"]


def _ids(doc) -> list[str]:
    return [i["id"] for c in C.CATEGORIES for i in doc["categories"][c]["items"]]


@pytest.fixture(autouse=True)
def configured_model(monkeypatch):
    """The stage has a model in these tests. The registry itself is covered by the unconfigured-stage test below."""
    async def resolved(Session):
        return SimpleNamespace(model_name="test-model", client=None)

    monkeypatch.setattr(worker, "_resolve_model", resolved)


def _fake_model(monkeypatch, *responses, calls=None, during=None):
    """Replace the single model call. Each call returns the next response; `during` runs before the call returns."""
    pending = list(responses)

    async def fake(description, resolved):
        if calls is not None:
            calls.append(description)
        if during is not None:
            await during()
        content = pending.pop(0) if len(pending) > 1 else pending[0]
        if isinstance(content, BaseException):
            raise content
        return _as_content(content), "stop"

    monkeypatch.setattr(worker, "_request_extraction", fake)


def _as_content(raw):
    return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)


async def _run(engine, job, description):
    claim: dict = {}
    await worker._extract_async(job, description, _sessions(engine), claim)
    return claim


# ---- the complete document ----

@pytest.mark.asyncio
async def test_an_english_analysis_persists_a_complete_v2_document(built, engine, monkeypatch):
    _fake_model(monkeypatch, EN_OUTPUT)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    await _run(engine, job, EN_JD)
    status, error, marker, revision, analysis, original, retired, w_skills, w_exp, model = _row(built, job)
    assert (status, error, marker, revision) == ("completed", None, 2, 1)
    doc = analysis["requirements"]
    assert doc["schema_version"] == 2 and doc["scoring_confirmation"] is None
    assert [i["text"] for i in doc["categories"]["skills"]["items"]] == ["Python", "SQL"]
    assert w_skills == 100 and w_exp == 0 and model == "test-model"
    assert analysis["extraction"]["prompt_code"] == "criteria_extraction_v2" and "skills" not in analysis
    assert original["requirements"]["schema_version"] == 2
    assert compute_readiness(doc, original=original["requirements"], jd_text=EN_JD).state == READY


@pytest.mark.asyncio
async def test_an_arabic_analysis_persists_a_complete_document_with_exact_arabic_wording(built, engine, monkeypatch):
    _fake_model(monkeypatch, AR_OUTPUT)
    tenant = _tenant(built)
    job = _job(built, tenant, AR_JD)
    await _run(engine, job, AR_JD)
    status, _, marker, revision, analysis, *_ = _row(built, job)
    assert (status, marker, revision) == ("completed", 2, 1)
    doc = analysis["requirements"]
    skill = doc["categories"]["skills"]["items"][0]
    assert skill["text"] == "بايثون" and skill["weight"] == 50        # two required skills share the category equally
    assert skill["source_text"] == "مطلوب مطور بايثون بخبرة في قواعد البيانات"
    assert AR_JD.count(skill["source_text"]) == 1
    assert doc["categories"]["experience"]["items"][0]["importance"] == "preferred"


@pytest.mark.asyncio
async def test_source_text_is_the_exact_job_description_wording_and_the_description_is_unchanged(built, engine, monkeypatch):
    _fake_model(monkeypatch, EN_OUTPUT)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    await _run(engine, job, EN_JD)
    doc = _doc(built, job)
    for item in (doc["categories"]["skills"]["items"] + doc["categories"]["experience"]["items"]):
        assert item["source_text"] in EN_JD
    cur = built.conn.cursor()
    cur.execute("SELECT description FROM jobs WHERE job_id = %s", (job,))
    assert cur.fetchone()[0] == EN_JD


@pytest.mark.asyncio
async def test_a_preferred_item_with_an_unestablished_cue_produces_a_classification_review_block(built, engine, monkeypatch):
    _fake_model(monkeypatch, UNCUED_OUTPUT)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    await _run(engine, job, EN_JD)
    doc = _doc(built, job)
    warnings = doc["classification_review"]["warnings"]
    assert [w["code"] for w in warnings] == ["preferred_cue_not_in_job_description"]
    readiness = compute_readiness(doc, require_classification_acknowledgment=True, original=_row(built, job)[5]["requirements"],
                                  jd_text=EN_JD)
    assert readiness.state == NEEDS_CLASSIFICATION_REVIEW


# ---- ids ----

@pytest.mark.asyncio
async def test_a_retired_id_is_never_generated_again(built, engine, monkeypatch):
    _fake_model(monkeypatch, EN_OUTPUT)
    tenant = _tenant(built)
    clean_job = _job(built, tenant, EN_JD)
    await _run(engine, clean_job, EN_JD)
    first_ids = _ids(_doc(built, clean_job))
    # The same description, with the first id retired: that id must be skipped, and the rest of the ids keep their positions.
    retired_job = _job(built, tenant, EN_JD, retired=[first_ids[0]])
    await _run(engine, retired_job, EN_JD)
    retry_ids = _ids(_doc(built, retired_job))
    assert first_ids[0] not in retry_ids
    assert len(set(retry_ids)) == len(retry_ids)
    assert _row(built, retired_job)[6] == [first_ids[0]]


@pytest.mark.asyncio
async def test_repeated_processing_of_a_completed_job_changes_nothing(built, engine, monkeypatch):
    calls = []
    _fake_model(monkeypatch, EN_OUTPUT, calls=calls)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    await _run(engine, job, EN_JD)
    before = _row(built, job)
    claim = await _run(engine, job, EN_JD)
    assert claim == {}                              # not claimed: the row is no longer pending
    assert len(calls) == 1                          # no second model call
    assert _row(built, job) == before               # same document, revision, ids and retired list


# ---- invalid model output ----

@pytest.mark.asyncio
async def test_malformed_model_output_writes_nothing_and_raises_for_retry(built, engine, monkeypatch):
    _fake_model(monkeypatch, "{not json")
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(worker.InvalidModelOutput, match="invalid_json"):
        await _run(engine, job, EN_JD)
    status, _, marker, revision, analysis, *_ = _row(built, job)
    assert (status, marker, revision, analysis) == ("processing", 2, 0, None)


@pytest.mark.asyncio
async def test_a_truncated_response_is_refused_and_writes_nothing(built, engine, monkeypatch):
    async def truncated(description, resolved):
        return json.dumps(EN_OUTPUT), "length"

    monkeypatch.setattr(worker, "_request_extraction", truncated)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(worker.InvalidModelOutput, match="output_truncated"):
        await _run(engine, job, EN_JD)
    assert _row(built, job)[4] is None


@pytest.mark.asyncio
async def test_structurally_invalid_output_writes_nothing(built, engine, monkeypatch):
    _fake_model(monkeypatch, {"categories": []})
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(worker.InvalidModelOutput, match="missing_categories"):
        await _run(engine, job, EN_JD)
    assert _row(built, job)[4] is None


# ---- retries and concurrency ----

@pytest.mark.asyncio
async def test_a_retry_after_failure_produces_the_document_with_the_same_ids_as_a_clean_run(built, engine, monkeypatch):
    tenant = _tenant(built)
    clean = _job(built, tenant, EN_JD)
    _fake_model(monkeypatch, EN_OUTPUT)
    await _run(engine, clean, EN_JD)

    retried = _job(built, tenant, EN_JD)
    _fake_model(monkeypatch, "{not json", EN_OUTPUT)
    with pytest.raises(worker.InvalidModelOutput):
        await _run(engine, retried, EN_JD)
    # The failed attempt is released to pending by the task, then the retry claims the row again.
    await worker._release_claim(retried, await _claim_token(engine, retried), _sessions(engine))
    await _run(engine, retried, EN_JD)

    status, _, _, revision, *_ = _row(built, retried)
    assert (status, revision) == ("completed", 1)
    assert _ids(_doc(built, retried)) == _ids(_doc(built, clean))


async def _claim_token(engine, job):
    """The token of the processing claim, read as the super administrator (test helper for the release)."""
    async with _sessions(engine)() as db:
        await set_rls_context(db, "", "super_admin")
        row = (await db.execute(text("SELECT updated_at FROM job_criteria WHERE job_id = :jid"), {"jid": job})).first()
        return row[0]


@pytest.mark.asyncio
async def test_a_retry_after_success_does_not_overwrite_the_completed_document(built, engine, monkeypatch):
    calls = []
    _fake_model(monkeypatch, EN_OUTPUT, UNCUED_OUTPUT, calls=calls)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    await _run(engine, job, EN_JD)
    before = _row(built, job)
    await _run(engine, job, EN_JD)
    assert len(calls) == 1
    assert _row(built, job) == before
    assert _doc(built, job)["categories"]["skills"]["items"][0]["text"] == "Python"


@pytest.mark.asyncio
async def test_concurrent_workers_produce_exactly_one_document(built, engine, monkeypatch):
    calls = []

    async def slow():
        await asyncio.sleep(0.2)

    _fake_model(monkeypatch, EN_OUTPUT, calls=calls, during=slow)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    first, second = {}, {}
    await asyncio.gather(
        worker._extract_async(job, EN_JD, _sessions(engine), first),
        worker._extract_async(job, EN_JD, _sessions(engine), second),
    )
    assert len(calls) == 1                          # only the claim winner calls the model
    assert sorted([bool(first), bool(second)]) == [False, True]
    status, _, _, revision, *_ = _row(built, job)
    assert (status, revision) == ("completed", 1)


@pytest.mark.asyncio
async def test_a_cancelled_attempt_persists_nothing(built, engine, monkeypatch):
    _fake_model(monkeypatch, asyncio.CancelledError())
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(asyncio.CancelledError):
        await _run(engine, job, EN_JD)
    status, error, _, revision, analysis, original, *_ = _row(built, job)
    assert (status, error, revision, analysis, original) == ("pending", CS.EXTRACTION_CANCELLED_MESSAGE, 0, None, None)


@pytest.mark.asyncio
async def test_a_claim_lost_during_extraction_is_discarded(built, engine, monkeypatch):
    async def steal():
        cur = built.conn.cursor()
        cur.execute("UPDATE job_criteria SET updated_at = now() + interval '1 second' WHERE job_id = %s", (job,))

    _fake_model(monkeypatch, EN_OUTPUT, during=steal)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    await _run(engine, job, EN_JD)
    status, _, _, revision, analysis, *_ = _row(built, job)
    assert (status, revision, analysis) == ("processing", 0, None)


# ---- transactions ----

@pytest.mark.asyncio
async def test_a_database_failure_while_writing_rolls_everything_back(built, engine, monkeypatch):
    _fake_model(monkeypatch, EN_OUTPUT)
    real_params = worker._persist_params

    def out_of_range(*args, **kwargs):
        params = real_params(*args, **kwargs)
        params["weight_skills"] = 150                 # the SMALLINT is in range, the weights rule is not: the CHECK refuses it
        return params

    monkeypatch.setattr(worker, "_persist_params", out_of_range)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(IntegrityError):              # the weights_sum_100 CHECK refuses the total, and the transaction rolls back
        await _run(engine, job, EN_JD)
    status, _, _, revision, analysis, original, *_ = _row(built, job)
    assert (status, revision, analysis, original) == ("processing", 0, None, None)


@pytest.mark.asyncio
async def test_a_failure_while_building_the_write_leaves_the_row_unfinished(built, engine, monkeypatch):
    _fake_model(monkeypatch, EN_OUTPUT)

    def broken(*args, **kwargs):
        raise RuntimeError("builder failed")

    monkeypatch.setattr(worker, "_persist_params", broken)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(RuntimeError, match="builder failed"):
        await _run(engine, job, EN_JD)
    status, _, _, revision, analysis, *_ = _row(built, job)
    assert (status, revision, analysis) == ("processing", 0, None)


# ---- the task wrapper: retry release and final failure ----

@pytest.mark.asyncio
async def test_the_task_retries_then_marks_the_row_failed_with_the_rejected_message(built, engine, monkeypatch):
    _fake_model(monkeypatch, "{not json")
    monkeypatch.setattr(worker, "_task_engine_and_session", lambda: (engine, _sessions(engine)))
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    retries = []
    original_retry = worker.extract_requirements_v2_task.retry

    def spy(*args, **kwargs):
        retries.append(_row(built, job)[0])
        return original_retry(*args, **kwargs)

    monkeypatch.setattr(worker.extract_requirements_v2_task, "retry", spy)
    result = await asyncio.to_thread(worker.extract_requirements_v2_task.apply, args=(job, EN_JD))
    assert retries == ["pending", "pending"]        # each intermediate attempt released the claim before the retry
    assert result.state == "FAILURE"
    status, error, _, revision, analysis, *_ = _row(built, job)
    assert (status, error, revision, analysis) == ("failed", CS.REQUIREMENTS_REJECTED_MESSAGE, 0, None)


# ---- tenant isolation and legacy rows ----

@pytest.mark.asyncio
async def test_another_tenant_cannot_load_the_persisted_document(built, engine, monkeypatch):
    _fake_model(monkeypatch, EN_OUTPUT)
    owner, other = _tenant(built), _tenant(built)
    job = _job(built, owner, EN_JD)
    other_job = _job(built, other, EN_JD)
    await _run(engine, job, EN_JD)
    async with _sessions(engine)() as db:
        async with db.begin():
            view = await E.load_for_edit(db, E.EditingUser(owner["user"], owner["tenant"], "admin"), job)
            assert view.schema_version == 2 and view.requirements_revision == 1
            with pytest.raises(E.JobNotFound):
                await E.load_for_edit(db, E.EditingUser(other["user"], other["tenant"], "admin"), job)
    assert _row(built, other_job)[0] == "pending"   # the other tenant's row is untouched


@pytest.mark.asyncio
async def test_a_legacy_row_is_never_claimed_or_changed_by_the_v2_worker(built, engine, monkeypatch):
    calls = []
    _fake_model(monkeypatch, EN_OUTPUT, calls=calls)
    legacy_analysis = {"skills": {"required": ["Python"]}, "scoreability": {"status": "scoreable"}}
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD, marker=None, analysis=legacy_analysis)
    before = _row(built, job)
    claim = await _run(engine, job, EN_JD)
    assert claim == {} and calls == []
    assert _row(built, job) == before
    assert before[4] == legacy_analysis and before[2] is None


# ---- an unconfigured stage fails visibly and is not retried ----

@pytest.mark.asyncio
async def test_an_unconfigured_model_stage_fails_the_row_visibly_without_a_model_call(built, engine, monkeypatch):
    calls = []
    _fake_model(monkeypatch, EN_OUTPUT, calls=calls)

    async def unconfigured(Session):
        return None

    monkeypatch.setattr(worker, "_resolve_model", unconfigured)
    monkeypatch.setattr(worker, "_task_engine_and_session", lambda: (engine, _sessions(engine)))
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    result = await asyncio.to_thread(worker.extract_requirements_v2_task.apply, args=(job, EN_JD))
    assert result.state == "FAILURE" and calls == []
    status, error, _, revision, analysis, *_ = _row(built, job)
    assert (status, error, revision, analysis) == ("failed", CS.MODEL_NOT_CONFIGURED_MESSAGE, 0, None)


# ---- cancellation: the row never stays processing, and cleanup is bound to the claim ----

def _set_row(built, job, **values):
    cur = built.conn.cursor()
    for column, value in values.items():
        cur.execute(f"UPDATE job_criteria SET {column} = %s WHERE job_id = %s", (value, job))


@pytest.mark.asyncio
async def test_cancellation_before_the_model_call_returns_the_row_to_pending(built, engine, monkeypatch):
    async def cancel_before_model(Session):
        raise asyncio.CancelledError()

    monkeypatch.setattr(worker, "_resolve_model", cancel_before_model)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(asyncio.CancelledError):
        await _run(engine, job, EN_JD)
    status, error, *_ = _row(built, job)
    assert (status, error) == ("pending", CS.EXTRACTION_CANCELLED_MESSAGE)


@pytest.mark.asyncio
async def test_cancellation_during_the_model_call_returns_the_row_to_pending(built, engine, monkeypatch):
    _fake_model(monkeypatch, asyncio.CancelledError())
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(asyncio.CancelledError):
        await _run(engine, job, EN_JD)
    assert _row(built, job)[0] == "pending"


@pytest.mark.asyncio
async def test_cancellation_immediately_before_persistence_returns_the_row_to_pending_with_no_document(built, engine, monkeypatch):
    _fake_model(monkeypatch, EN_OUTPUT)

    def cancel_building_the_write(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(worker, "_persist_params", cancel_building_the_write)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(asyncio.CancelledError):
        await _run(engine, job, EN_JD)
    status, error, _, revision, analysis, *_ = _row(built, job)
    assert (status, revision, analysis) == ("pending", 0, None)


@pytest.mark.asyncio
async def test_cancellation_after_a_successful_commit_does_not_revert_the_completed_document(built, engine, monkeypatch):
    _fake_model(monkeypatch, EN_OUTPUT)

    class _Log:
        def info(self, message, *args):
            if "completed" in message:
                raise asyncio.CancelledError()

        def warning(self, *args):
            pass

        def error(self, *args):
            pass

    monkeypatch.setattr(worker, "logger", _Log())
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(asyncio.CancelledError):
        await _run(engine, job, EN_JD)
    status, error, _, revision, analysis, *_ = _row(built, job)
    assert (status, revision) == ("completed", 1) and analysis["requirements"]["schema_version"] == 2


@pytest.mark.asyncio
async def test_cancellation_during_a_failing_commit_returns_the_row_to_pending_and_stores_nothing(built, engine, monkeypatch):
    _fake_model(monkeypatch, EN_OUTPUT)
    real_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    persist_commits = {"n": 0}

    class _FailingCommit:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def execute(self, *args, **kwargs):
            return await self._inner.execute(*args, **kwargs)

        async def commit(self):
            raise asyncio.CancelledError()

        async def rollback(self):
            await self._inner.rollback()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            await self._inner.close()

    class _Sessions:
        def __init__(self):
            self.calls = 0

        def __call__(self):
            self.calls += 1
            inner = real_session()
            # the second session opened by the attempt is the write: its commit is the one that fails
            return _FailingCommit(inner) if self.calls == 2 else _Passthrough(inner)

    class _Passthrough:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def __aenter__(self):
            return await self._inner.__aenter__()

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    claim = {}
    with pytest.raises(asyncio.CancelledError):
        await worker._extract_async(job, EN_JD, _Sessions(), claim)
    status, error, _, revision, analysis, *_ = _row(built, job)
    assert (status, revision, analysis) == ("pending", 0, None)


@pytest.mark.asyncio
async def test_cleanup_never_releases_another_workers_claim(built, engine, monkeypatch):
    """The row is taken over by another claim while this attempt is in its model call; this attempt is then cancelled. Its cleanup
    matches only its own token, so the other claim is left exactly as it was."""
    other_claim_time = {}

    async def take_over_then_cancel(description, resolved):
        cur = built.conn.cursor()
        cur.execute("UPDATE job_criteria SET updated_at = now() + interval '5 seconds' WHERE job_id = %s RETURNING updated_at", (job,))
        other_claim_time["value"] = cur.fetchone()[0]
        raise asyncio.CancelledError()

    monkeypatch.setattr(worker, "_request_extraction", take_over_then_cancel)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(asyncio.CancelledError):
        await _run(engine, job, EN_JD)
    status, error, *_ = _row(built, job)
    assert status == "processing" and error is None


@pytest.mark.asyncio
async def test_a_cancelled_row_is_retried_through_the_pending_cancellation_claim(built, engine, monkeypatch):
    """The cancellation message is the only thing that lets a pending row be claimed by the retry route; the claim is single."""
    _fake_model(monkeypatch, asyncio.CancelledError())
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(asyncio.CancelledError):
        await _run(engine, job, EN_JD)
    assert _row(built, job)[0:2] == ("pending", CS.EXTRACTION_CANCELLED_MESSAGE)
    _fake_model(monkeypatch, EN_OUTPUT)
    await _run(engine, job, EN_JD)
    assert _row(built, job)[0] == "completed"



@pytest.mark.asyncio
async def test_the_cancellation_release_never_changes_a_completed_document_even_with_its_current_token(built, engine, monkeypatch):
    """The release statement itself is guarded: a completed document is never returned to pending, even by a caller that presents the
    row's own current updated_at as its token."""
    _fake_model(monkeypatch, EN_OUTPUT)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    await _run(engine, job, EN_JD)
    before = _row(built, job)
    current_token = (await _current_updated_at(engine, job))
    await worker._release_cancelled(job, current_token, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
    after = _row(built, job)
    assert after[0] == "completed" and after[4] == before[4] and after[3] == before[3]


async def _current_updated_at(engine, job):
    async with AsyncSession(engine) as db:
        async with db.begin():
            await db.execute(text("SELECT set_config('app.current_tenant_id', '', true), set_config('app.current_role', 'super_admin', true)"))
            return (await db.execute(text("SELECT updated_at FROM job_criteria WHERE job_id = :j"), {"j": job})).scalar_one()


# ---- failed-description hash: written with the failed status, normalized, and guarded by the claim ----

def _failure(built, job):
    cur = built.conn.cursor()
    cur.execute("SELECT criteria_last_failed_description_hash, criteria_last_failed_at FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()


def _hash(description):
    return worker._normalize_description_hash(description)


@pytest.mark.asyncio
async def test_a_rejected_extraction_stores_the_normalized_hash_with_the_failed_status(built, engine, monkeypatch):
    _fake_model(monkeypatch, "{not json")
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    await _apply_task(monkeypatch, engine, job, EN_JD)
    status, error, *_ = _row(built, job)
    stored_hash, failed_at = _failure(built, job)
    assert (status, error) == ("failed", CS.REQUIREMENTS_REJECTED_MESSAGE)
    assert stored_hash == _hash(EN_JD)
    assert failed_at is not None


@pytest.mark.asyncio
async def test_the_stored_hash_is_the_normalized_description_not_the_raw_text(built, engine, monkeypatch):
    _fake_model(monkeypatch, "{not json")
    tenant = _tenant(built)
    raw = "  Senior   PYTHON developer.\n\nPython and SQL are required.  "
    job = _job(built, tenant, raw)
    await _apply_task(monkeypatch, engine, job, raw)
    stored_hash, _ = _failure(built, job)
    assert stored_hash == _hash(raw)
    assert stored_hash == _hash("senior python developer. python and sql are required.")
    assert stored_hash != hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.asyncio
async def test_a_transient_failure_stores_the_hash_and_keeps_the_fixed_transient_message(built, engine, monkeypatch):
    _fake_model(monkeypatch, RuntimeError("provider unavailable"))
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    await _apply_task(monkeypatch, engine, job, EN_JD)
    status, error, *_ = _row(built, job)
    assert (status, error) == ("failed", CS.WORKER_FAILED_MESSAGE)
    assert _failure(built, job)[0] == _hash(EN_JD)


@pytest.mark.asyncio
async def test_an_unconfigured_stage_stores_the_hash_and_the_configuration_message(built, engine, monkeypatch):
    async def unconfigured(Session):
        return None

    monkeypatch.setattr(worker, "_resolve_model", unconfigured)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    monkeypatch.setattr(worker, "_task_engine_and_session", lambda: (engine, _sessions(engine)))
    await asyncio.to_thread(worker.extract_requirements_v2_task.apply, args=(job, EN_JD))
    status, error, *_ = _row(built, job)
    assert (status, error) == ("failed", CS.MODEL_NOT_CONFIGURED_MESSAGE)
    assert _failure(built, job)[0] == _hash(EN_JD)


@pytest.mark.asyncio
async def test_a_stale_claim_cannot_overwrite_the_status_or_the_hash(built, engine, monkeypatch):
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    _set_row(built, job, criteria_extraction_status="processing", criteria_last_failed_description_hash="previous-hash")
    stale_token = datetime(2000, 1, 1, tzinfo=timezone.utc)   # not the row's current claim
    marked = await worker._mark_failed(job, stale_token, _sessions(engine), CS.REQUIREMENTS_REJECTED_MESSAGE, EN_JD)
    assert marked is False
    status, error, *_ = _row(built, job)
    assert status == "processing"
    assert _failure(built, job)[0] == "previous-hash"


@pytest.mark.asyncio
async def test_a_cancelled_attempt_is_not_recorded_as_a_failed_attempt(built, engine, monkeypatch):
    async def cancel_before_model(Session):
        raise asyncio.CancelledError()

    monkeypatch.setattr(worker, "_resolve_model", cancel_before_model)
    tenant = _tenant(built)
    job = _job(built, tenant, EN_JD)
    with pytest.raises(asyncio.CancelledError):
        await _run(engine, job, EN_JD)
    status, error, *_ = _row(built, job)
    assert (status, error) == ("pending", CS.EXTRACTION_CANCELLED_MESSAGE)
    assert _failure(built, job) == (None, None)


async def _apply_task(monkeypatch, engine, job, description):
    """Run the whole Celery task (with its retries) eagerly, as the broker would, against the test database."""
    monkeypatch.setattr(worker, "_task_engine_and_session", lambda: (engine, _sessions(engine)))
    return await asyncio.to_thread(worker.extract_requirements_v2_task.apply, args=(job, description))
