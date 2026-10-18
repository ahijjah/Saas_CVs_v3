"""The real candidate-scoring pipeline (`cv_score._score_cv_async`) on a real PostgreSQL 16 chain, for requirements-v2 jobs.

Only external components are replaced: the PDF text extraction, the local gatekeeper's semantic model, the AI scoring call and the
e-mail sender. Every database step is real: the criteria load, the readiness gate, the revision lock, the score row, the application
status, and every requirements edit (made through the production save path, E.save_operations)."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import config
from realdb_helper import EXPECTED_PRE_EXISTING_FAILURES, built_chain, check_chain, unavailable_reason
from services import ai_service
from services import requirements_edit as E
from services.requirements_v2 import contract as C
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.scoring import RequirementsRevisionChanged, ScoringNotReady
from workers import cv_score
from workers import requirements_v2_worker as v2_worker

CV_TEXT = (
    "Jane Candidate. Python engineer with five years of backend experience. "
    "Built REST APIs and PostgreSQL schemas for payments and reporting platforms. "
    "Strong SQL and Python skills, with query tuning, migrations and data modelling on large tables. "
    "Led a team of three engineers, ran code reviews, wrote the deployment runbooks and mentored juniors. "
    "Education: BSc in Computer Science. Certifications: none listed. Languages: English and Arabic. "
    "Projects include an event pipeline in Python, a reporting service on PostgreSQL and internal tooling for operations teams."
)
NOW = "2026-10-18T10:00:00+00:00"
EN_JD = "Senior developer.\nPython and SQL are required.\nFive years of backend experience is preferred."
SPLIT_JD = "Senior developer.\nPython or Java is required.\nFive years of experience is preferred."
AR_JD = "مطور أول.\nمطلوب بايثون أو جافا.\nخبرة خمس سنوات مفضلة."


def _ai_result():
    return {"score_skills": 80, "score_experience": 70, "score_education": 60, "score_certifications": 50,
            "score_soft_skills": 50, "score_domain_knowledge": 50, "score_other": 50, "strengths": [], "gaps_identified": [],
            "red_flags": [], "evaluation_notes": "fixture", "interview_questions": [], "reasoning": {}, "score_details": {}}


def _build(jd, categories, weights):
    cats = {c: [] for c in C.CATEGORIES}
    cats.update(categories)
    raw = {"categories": cats, "category_weights": weights, "scoreability": {"status": "scoreable"}}
    parsed = parse_response(json.dumps(raw, ensure_ascii=False), jd, "stop", id_factory=v2_worker.stable_id_factory(jd))
    doc, expectations = v2_worker.finalise_document(v2_worker.checked_document(parsed), jd)
    return doc, parsed.original, expectations


def _ready(jd=EN_JD):
    """Python and SQL required (one category); the preferred experience item carries an established cue."""
    return _build(jd, {"skills": [
        {"text": "Python", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"},
        {"text": "SQL", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"}]},
        {"skills": 100})


def _preferred_only():
    return _build(EN_JD, {"experience": [
        {"text": "Backend experience", "importance": "preferred", "importance_cue": "preferred", "origin": "stated",
         "source_text": "Five years of backend experience is preferred"}]}, {})


def _classification_warning():
    doc, original, expectations = _build(EN_JD, {"skills": [
        {"text": "Python", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"}],
        "experience": [{"text": "Backend experience", "importance": "preferred", "importance_cue": "nice to have",
                        "origin": "stated", "source_text": "Five years of backend experience is preferred"}]},
        {"skills": 100})
    return doc, original, expectations


def _structural_split(jd=SPLIT_JD, source="Python or Java is required", alternatives=("Python", "Java")):
    items = [{"text": t, "importance": "required", "source_text": source, "alternatives": list(alternatives), "origin": "stated"}
             for t in alternatives]
    return _build(jd, {"skills": items}, {"skills": 100})


def _pending_structure():
    """A required experience item with structure: changing its wording later leaves its structure unsettled."""
    return _build(EN_JD, {
        "skills": [{"text": "Python", "importance": "required", "source_text": "Python and SQL are required", "origin": "stated"}],
        "experience": [{"text": "Backend experience", "importance": "required", "source_text": "Five years of backend experience",
                        "origin": "stated", "experience": {"subject": "backend experience", "min_years": 5}}]},
        {"skills": 60, "experience": 40})


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
def pipeline(built, monkeypatch, tmp_path):
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
    state = {"calls": [], "during": None}

    async def fake_score(cv_text, criteria, job_title, **kwargs):
        state["calls"].append(criteria)
        if state["during"] is not None:
            await state["during"]()
        return _ai_result(), {"model": "test-model", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                              "finish_reason": "stop"}

    monkeypatch.setattr(ai_service, "score_cv", fake_score)
    engine = create_async_engine(dsn, poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    yield _Pipeline(engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), state, tmp_path, built)


async def _none(*args, **kwargs):
    return None


def _gatekeeper_pass(**kwargs):
    from services.local_processor import GatekeeperResult
    return GatekeeperResult(cv_language="en", jd_language="en", semantic_similarity=0.9, semantic_similarity_pct=90.0,
                            skill_match_ratio=100.0, matched_skills=["Python", "SQL"], missing_skills=[], gatekeeper_passed=True,
                            cleaned_cv_text=CV_TEXT)


class _Pipeline:
    def __init__(self, engine, Session, state, root, built):
        self.engine, self.Session, self.state, self.root, self.built = engine, Session, state, root, built

    @property
    def calls(self):
        return self.state["calls"]

    async def score(self, app, job, tenant, *, tenant_id=None):
        await cv_score._score_cv_async(app, job, tenant_id or tenant["tenant"], self.file_of(app), "application/pdf", {}, self.Session)

    def file_of(self, app):
        cur = self.built.conn.cursor()
        cur.execute("SELECT file_path FROM application_files WHERE application_id = %s", (app,))
        return str(self.root / cur.fetchone()[0])

    async def save(self, job, tenant, revision, ops):
        async with self.Session() as session:
            async with session.begin():
                user = E.EditingUser(user_id=tenant["user"], tenant_id=tenant["tenant"], role="admin")
                return await E.save_operations(session, user, job, client_revision=revision, ops=E.parse_operations(ops), now=NOW)


def _tenant(built) -> dict:
    cur = built.conn.cursor()
    tag = uuid.uuid4().hex[:8]
    cur.execute("INSERT INTO tenants (name, email_domain) VALUES (%s, %s) RETURNING tenant_id", (f"t {tag}", f"{tag}.example"))
    tenant = str(cur.fetchone()[0])
    cur.execute("INSERT INTO users (tenant_id, email, password_hash, full_name, role) VALUES (%s, %s, 'x', 'u', 'admin') "
                "RETURNING user_id", (tenant, f"{tag}@example.test"))
    return {"tenant": tenant, "user": str(cur.fetchone()[0])}


def _v2_job(built, tenant, jd, bundle, *, revision=1) -> str:
    document, original, expectations = bundle
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, status) VALUES (%s, %s, 'Senior', %s, 'active')"
                " RETURNING job_id", (tenant["tenant"], tenant["user"], jd))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, requirements_schema_version, requirements_revision,"
                " analysis_json, original_analysis_json, weight_skills, weight_experience, weight_education, weight_certifications,"
                " weight_soft_skills, weight_domain_knowledge, weight_other)"
                " VALUES (%s, 'completed', 2, %s, CAST(%s AS jsonb), CAST(%s AS jsonb), %s, %s, %s, %s, %s, %s, %s)",
                (job, revision, json.dumps({"requirements": document}, ensure_ascii=False),
                 json.dumps({"requirements": original, "structural_expectations": expectations}, ensure_ascii=False),
                 *[document["categories"][c]["weight"] for c in C.CATEGORIES]))
    return job


def _application(built, tenant, job, root) -> str:
    cur = built.conn.cursor()
    cur.execute("INSERT INTO applications (job_id, tenant_id, candidate_name, candidate_email, candidate_phone, submission_source,"
                " processing_status) VALUES (%s, %s, 'Jane', %s, '+100', 'manual_upload', 'pending') RETURNING application_id",
                (job, tenant["tenant"], f"{uuid.uuid4().hex[:8]}@example.test"))
    app = str(cur.fetchone()[0])
    relative = f"tenants/{tenant['tenant']}/jobs/{job}/{app}.pdf"
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / relative).write_bytes(b"%PDF-1.4 fixture")
    cur.execute("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path, file_size_bytes,"
                " extracted_text, extraction_status) VALUES (%s, %s, 'cv.pdf', 'application/pdf', %s, 16, %s, 'pending')",
                (app, tenant["tenant"], relative, CV_TEXT))
    return app


def _scores(built, app):
    cur = built.conn.cursor()
    cur.execute("SELECT final_score, requirements_revision_used, weights_snapshot FROM application_scores WHERE application_id = %s",
                (app,))
    return cur.fetchall()


def _status(built, app):
    cur = built.conn.cursor()
    cur.execute("SELECT processing_status FROM applications WHERE application_id = %s", (app,))
    return cur.fetchone()[0]


# ---- a ready document scores, and the score records the revision it used ----

@pytest.mark.asyncio
async def test_a_ready_v2_document_scores_and_the_score_records_the_revision_used(built, pipeline):
    tenant = _tenant(built)
    job = _v2_job(built, tenant, EN_JD, _ready(), revision=3)
    app = _application(built, tenant, job, pipeline.root)
    await pipeline.score(app, job, tenant)
    (final_score, revision_used, weights), = _scores(built, app)
    assert revision_used == 3 and weights["weight_skills"] == 100
    assert _status(built, app) == "ai_scored"
    assert pipeline.calls[0]["skills"] == ["Python", "SQL"]


# ---- readiness gates, each lifted by the review action that settles it ----

@pytest.mark.asyncio
async def test_a_preferred_only_document_is_blocked_before_confirmation_and_confirmation_enables_scoring(built, pipeline):
    tenant = _tenant(built)
    job = _v2_job(built, tenant, EN_JD, _preferred_only(), revision=1)
    app = _application(built, tenant, job, pipeline.root)
    with pytest.raises(ScoringNotReady) as blocked:
        await pipeline.score(app, job, tenant)
    assert blocked.value.state == "needs_confirmation"
    assert pipeline.calls == [] and _scores(built, app) == [] and _status(built, app) == "failed"
    await pipeline.save(job, tenant, 1, [{"op": "confirm_no_numeric_score"}])
    app = _application(built, tenant, job, pipeline.root)
    await pipeline.score(app, job, tenant)
    (_, revision_used, _), = _scores(built, app)
    assert revision_used == 2 and len(pipeline.calls) == 1


@pytest.mark.asyncio
async def test_a_classification_warning_blocks_scoring_until_it_is_acknowledged(built, pipeline):
    tenant = _tenant(built)
    job = _v2_job(built, tenant, EN_JD, _classification_warning(), revision=1)
    app = _application(built, tenant, job, pipeline.root)
    with pytest.raises(ScoringNotReady) as blocked:
        await pipeline.score(app, job, tenant)
    assert blocked.value.state == "needs_classification_review"
    cur = built.conn.cursor()
    cur.execute("SELECT requirements_revision FROM job_criteria WHERE job_id = %s", (job,))
    assert cur.fetchone()[0] == 1 and pipeline.calls == []
    warning_id = _warning_id(built, job)
    await pipeline.save(job, tenant, 1, [{"op": "acknowledge_classification_warning", "warning_id": warning_id}])
    app = _application(built, tenant, job, pipeline.root)
    await pipeline.score(app, job, tenant)
    (_, revision_used, _), = _scores(built, app)
    assert revision_used == 2


def _warning_id(built, job) -> str:
    cur = built.conn.cursor()
    cur.execute("SELECT analysis_json FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()[0]["requirements"]["classification_review"]["warnings"][0]["id"]


@pytest.mark.asyncio
async def test_an_open_structural_finding_blocks_scoring_until_it_is_resolved(built, pipeline):
    tenant = _tenant(built)
    job = _v2_job(built, tenant, SPLIT_JD, _structural_split(), revision=1)
    app = _application(built, tenant, job, pipeline.root)
    with pytest.raises(ScoringNotReady) as blocked:
        await pipeline.score(app, job, tenant)
    assert blocked.value.state == "needs_structural_review"
    finding_id = _finding_id(built, job)
    await pipeline.save(job, tenant, 1, [{"op": "resolve_structural_finding", "finding_id": finding_id,
                                          "decision": "accepted_as_is", "note": "Both tools are acceptable.",
                                          "evidence_item_ids": None}])
    app = _application(built, tenant, job, pipeline.root)
    await pipeline.score(app, job, tenant)
    (_, revision_used, _), = _scores(built, app)
    assert revision_used == 2


def _finding_id(built, job) -> str:
    cur = built.conn.cursor()
    cur.execute("SELECT analysis_json FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()[0]["requirements"]["structural_review"]["findings"][0]["id"]


@pytest.mark.asyncio
async def test_a_pending_structure_blocks_scoring_until_it_is_confirmed(built, pipeline):
    tenant = _tenant(built)
    job = _v2_job(built, tenant, EN_JD, _pending_structure(), revision=1)
    item_id = _experience_item_id(built, job)
    await pipeline.save(job, tenant, 1, [{"op": "set_text", "item_id": item_id, "text": "Backend engineering experience"}])
    app = _application(built, tenant, job, pipeline.root)
    with pytest.raises(ScoringNotReady) as blocked:
        await pipeline.score(app, job, tenant)
    assert blocked.value.state == "needs_structure_review"
    await pipeline.save(job, tenant, 2, [{"op": "confirm_structure", "item_id": item_id}])
    app = _application(built, tenant, job, pipeline.root)
    await pipeline.score(app, job, tenant)
    (_, revision_used, _), = _scores(built, app)
    assert revision_used == 3


def _experience_item_id(built, job) -> str:
    cur = built.conn.cursor()
    cur.execute("SELECT analysis_json FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()[0]["requirements"]["categories"]["experience"]["items"][0]["id"]


# ---- a race, a cancellation and a retry ----

@pytest.mark.asyncio
async def test_an_edit_during_scoring_is_refused_at_the_write_so_no_mixed_revision_is_stored(built, pipeline):
    tenant = _tenant(built)
    job = _v2_job(built, tenant, EN_JD, _ready(), revision=1)
    app = _application(built, tenant, job, pipeline.root)
    item_id = _skill_item_id(built, job)

    async def edit_while_scoring():
        await pipeline.save(job, tenant, 1, [{"op": "set_text", "item_id": item_id, "text": "Python 3"}])

    pipeline.state["during"] = edit_while_scoring
    with pytest.raises(RequirementsRevisionChanged):
        await pipeline.score(app, job, tenant)
    assert _scores(built, app) == [] and _status(built, app) == "failed"


def _skill_item_id(built, job) -> str:
    cur = built.conn.cursor()
    cur.execute("SELECT analysis_json FROM job_criteria WHERE job_id = %s", (job,))
    return cur.fetchone()[0]["requirements"]["categories"]["skills"]["items"][0]["id"]


@pytest.mark.asyncio
async def test_a_cancelled_scoring_rolls_back_the_score_and_the_status(built, pipeline, monkeypatch):
    from services.requirements_v2 import scoring

    real_lock = scoring.lock_revision

    async def lock_then_cancel(db, job_id, revision):
        await real_lock(db, job_id, revision)
        raise asyncio.CancelledError()

    monkeypatch.setattr(scoring, "lock_revision", lock_then_cancel)
    tenant = _tenant(built)
    job = _v2_job(built, tenant, EN_JD, _ready(), revision=1)
    app = _application(built, tenant, job, pipeline.root)
    with pytest.raises(asyncio.CancelledError):
        await pipeline.score(app, job, tenant)
    assert _scores(built, app) == [] and _status(built, app) != "ai_scored"


@pytest.mark.asyncio
async def test_a_retry_of_a_scored_application_is_idempotent(built, pipeline):
    tenant = _tenant(built)
    job = _v2_job(built, tenant, EN_JD, _ready(), revision=1)
    app = _application(built, tenant, job, pipeline.root)
    await pipeline.score(app, job, tenant)
    await pipeline.score(app, job, tenant)
    assert len(_scores(built, app)) == 1 and len(pipeline.calls) == 1 and _status(built, app) == "ai_scored"


# ---- tenancy and the legacy path ----

@pytest.mark.asyncio
async def test_scoring_under_another_tenant_finds_no_criteria_and_writes_nothing(built, pipeline):
    owner, other = _tenant(built), _tenant(built)
    job = _v2_job(built, owner, EN_JD, _ready(), revision=1)
    app = _application(built, owner, job, pipeline.root)
    with pytest.raises(RuntimeError, match="No criteria found"):
        await pipeline.score(app, job, owner, tenant_id=other["tenant"])
    assert pipeline.calls == [] and _scores(built, app) == []


@pytest.mark.asyncio
async def test_a_legacy_job_is_scored_from_its_flat_columns_and_records_no_revision(built, pipeline):
    tenant = _tenant(built)
    cur = built.conn.cursor()
    cur.execute("INSERT INTO jobs (tenant_id, created_by, title, description, status) VALUES (%s, %s, 'Legacy', %s, 'active')"
                " RETURNING job_id", (tenant["tenant"], tenant["user"], EN_JD))
    job = str(cur.fetchone()[0])
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, skills, analysis_json, weight_skills, weight_experience,"
                " weight_education, weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other)"
                " VALUES (%s, 'completed', ARRAY['Legacy Python', 'Legacy SQL'], CAST(%s AS jsonb), 40, 20, 10, 10, 10, 5, 5)",
                (job, json.dumps({"scoreability": {"status": "scoreable"}})))
    app = _application(built, tenant, job, pipeline.root)
    await pipeline.score(app, job, tenant)
    assert pipeline.calls[0]["skills"] == ["Legacy Python", "Legacy SQL"]
    (final_score, revision_used, weights), = _scores(built, app)
    assert revision_used is None and weights["weight_skills"] == 40
