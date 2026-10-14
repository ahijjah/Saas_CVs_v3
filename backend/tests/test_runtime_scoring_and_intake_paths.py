"""Three paths that run a statement after a commit on a row-level-security table, exercised on REAL PostgreSQL with REAL sessions:

  * routers/applications.py score_pending_uploads   (claims pending CVs, then reads the intake log after its commit)
  * routers/public.py     _handle_public_submission  (intake, then the auto-close UPDATE on jobs after its commit)
  * workers/cv_score.py   _score_cv_async            (the scoring pipeline; the database is real, only the AI and file edges are stubs)

Each runs as the superuser and, with CV_TEST_RUNTIME_ROLE=cv_runtime, as the restricted role (NOSUPERUSER, NOBYPASSRLS, owns nothing). The
tenant-isolation assertions are checked only when the role is restricted: a superuser sees every row by design. The AI model, the email
and the Celery enqueue are replaced; nothing is sent and no model is called.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND), str(BACKEND / "services")]
import realdb_helper as rh  # noqa: E402


def _load_real_intake_routes():
    """tests/conftest.py stubs services.application_intake_service for the stub-based suites. The routes under test need the real service:
    import it (and the two routers that use it) while the real module is in place, then put the stub back for the other suites."""
    stub = sys.modules.get("services.application_intake_service")
    if stub is None or hasattr(stub, "process_cv_intake"):
        return
    del sys.modules["services.application_intake_service"]
    try:
        import services.application_intake_service  # noqa: F401
        import routers.applications  # noqa: F401
        import routers.public  # noqa: F401
    finally:
        sys.modules["services.application_intake_service"] = stub


_load_real_intake_routes()

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

RUNTIME = os.environ.get("CV_TEST_RUNTIME_ROLE", "")
GRANTS = BACKEND / "deploy" / "runtime_role" / "grants.sql"
T1, T2 = str(uuid.UUID(int=0x301)), str(uuid.UUID(int=0x302))
U_HR, U_OTHER = str(uuid.UUID(int=0x311)), str(uuid.UUID(int=0x312))
JOB_LEGACY = str(uuid.UUID(int=0x321))
JOB_PUBLIC = str(uuid.UUID(int=0x322))
JOB_CODE = "RT-PUBLIC-1"


def _user(tenant, role="hr_manager", uid=U_HR):
    return SimpleNamespace(tenant_id=tenant, role=role, user_id=uid, full_name="HR", email="hr@example.com")


@pytest.fixture(scope="module")
def cluster():
    c = rh.Cluster()
    yield c
    c.stop()


@pytest.fixture
def world(cluster):
    """A fresh database per test: two tenants, a legacy job with criteria, a public job with a limit of one application, and the grants of the
    restricted role when CV_TEST_RUNTIME_ROLE is set (applied after every migration, so every table exists)."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    d = cluster.build()
    assert all(f.startswith(("010_", "024_", "070_", "087_")) for f in d.failed), d.failed      # the known stand-in failures only
    cur = d.conn.cursor()
    cur.execute(f"INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES "
                f"('{T1}','Tenant One','t1.example','active','active','organization'),('{T2}','Tenant Two','t2.example','active','active','organization')")
    d.entitle_active_tenants()   # fixture: an active tenant needs a validity date (see realdb_helper)
    for uid, tid, role in ((U_HR, T1, "hr_manager"), (U_OTHER, T2, "hr_manager")):
        cur.execute("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES "
                    f"('{uid}','{tid}','{uid[-4:]}@x.example','x','U','{role}','active')")
    cur.execute("INSERT INTO jobs (job_id, tenant_id, created_by, title, description, status) VALUES "
                f"('{JOB_LEGACY}','{T1}','{U_HR}','HR officer','Synthetic','active')")
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, skills, weight_skills, weight_experience, weight_education, "
                "weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other, analysis_json) VALUES "
                f"('{JOB_LEGACY}','completed',ARRAY['MS Office'],30,40,10,0,10,10,0,"
                "'{\"skills\": {\"required\": [\"MS Office\"], \"preferred\": []}, \"experience\": {\"minimum_years\": 3}}')")
    cur.execute("INSERT INTO jobs (job_id, tenant_id, created_by, title, description, status, job_code, max_applications, "
                "auto_close_when_limit_reached) VALUES "
                f"('{JOB_PUBLIC}','{T1}','{U_HR}','Public role','Synthetic','active','{JOB_CODE}',1,TRUE)")
    cur.execute("INSERT INTO job_criteria (job_id, criteria_extraction_status, skills, weight_skills, weight_experience, weight_education, "
                "weight_certifications, weight_soft_skills, weight_domain_knowledge, weight_other, analysis_json) VALUES "
                f"('{JOB_PUBLIC}','completed',ARRAY['MS Office'],30,40,10,0,10,10,0,"
                "'{\"skills\": {\"required\": [\"MS Office\"], \"preferred\": []}, \"experience\": {\"minimum_years\": 3}}')")
    if RUNTIME:
        cur.execute(GRANTS.read_text(encoding="utf-8"))
    url = d.async_url.replace("postgres@", f"{RUNTIME or 'postgres'}@")
    engine = create_async_engine(url, poolclass=NullPool, connect_args={"server_settings": {"search_path": "cv_analyzer"}})
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield SimpleNamespace(db=d, engine=engine, sessions=sessions)
    asyncio.run(engine.dispose())
    d.drop()


def _insert_app(world, job, tenant, name, status="pending", path=None):
    app = world.db.q("INSERT INTO applications (job_id, tenant_id, candidate_name, submission_source, processing_status) "
                     "VALUES (%s,%s,%s,'manual_upload',%s) RETURNING application_id::text", (job, tenant, name, status))[0][0]
    if path:
        world.db.q("INSERT INTO application_files (application_id, tenant_id, original_name, mime_type, file_path) "
                   "VALUES (%s,%s,'cv.pdf','application/pdf',%s)", (app, tenant, path))
    return app


def _scalar_as(world, tenant, role, sql, params=()):
    """Run one statement on a fresh session as a tenant user (the context the API sets for a request)."""
    from database import set_rls_context
    from sqlalchemy import text

    async def go():
        async with world.sessions() as s:
            await set_rls_context(s, tenant, role)
            res = await s.execute(text(sql), dict(zip(_names(sql), params)) if params else {})
            return res.scalar()
    return asyncio.run(go())


def _names(sql):
    import re
    return re.findall(r":(\w+)", sql)


# ── score_pending_uploads ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_score_pending_claims_the_tenants_pending_cvs_and_writes_the_intake_log_after_its_commit(world):
    from routers import applications
    from workers import cv_score

    pending = _insert_app(world, JOB_LEGACY, T1, "Pending One", path="cvs/one.pdf")
    world.db.q("INSERT INTO application_intake_log (application_id, tenant_id, intake_method, status) VALUES (%s,%s,'manual_upload','accepted')",
               (pending, T1))
    other = _insert_app(world, JOB_LEGACY, T2, "Other Tenant", path="cvs/other.pdf")
    enqueued: list = []

    async def go(user):
        async with world.sessions() as s:
            from database import set_rls_context
            await set_rls_context(s, user.tenant_id, user.role)
            return await applications.score_pending_uploads(applications.ScorePendingRequest(job_id=JOB_LEGACY), user, s)

    with patch.object(cv_score.score_cv_task, "delay", lambda **kw: enqueued.append(kw["application_id"])):
        if RUNTIME:
            # the other tenant's job id is not visible to it: the handler answers 404 before claiming anything
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                asyncio.run(go(_user(T2, uid=U_OTHER)))
            assert exc.value.status_code == 404
        result = asyncio.run(go(_user(T1)))

    assert result["success"] is True and result["queued"] == 1
    assert enqueued == [pending]
    assert _scalar_as(world, T1, "hr_manager", "SELECT processing_status FROM applications WHERE application_id = :a", (pending,)) == "queued"
    # the intake log is read AFTER the claim's commit (routers/applications.py); its scoring_enqueued_at must now be set
    assert _scalar_as(world, T1, "hr_manager", "SELECT scoring_enqueued_at IS NOT NULL FROM application_intake_log WHERE application_id = :a",
                      (pending,)) is True
    assert _scalar_as(world, T2, "hr_manager", "SELECT processing_status FROM applications WHERE application_id = :a", (other,)) == "pending"


def test_score_pending_refuses_another_tenants_job_under_the_restricted_role(world):
    """Tenant isolation at the route: a tenant-2 caller naming tenant 1's job gets 404 and claims nothing (RLS hides the job; the handler also
    filters by tenant). Runs in both modes; the isolation proof is the restricted role's."""
    from routers import applications
    from workers import cv_score

    pending = _insert_app(world, JOB_LEGACY, T1, "Stays Pending", path="cvs/x.pdf")
    from fastapi import HTTPException

    async def go():
        async with world.sessions() as s:
            from database import set_rls_context
            await set_rls_context(s, T2, "hr_manager")
            return await applications.score_pending_uploads(applications.ScorePendingRequest(job_id=JOB_LEGACY), _user(T2, uid=U_OTHER), s)

    with patch.object(cv_score.score_cv_task, "delay", lambda **kw: pytest.fail("nothing may be enqueued")):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(go())
    assert exc.value.status_code == 404
    assert _scalar_as(world, T1, "hr_manager", "SELECT processing_status FROM applications WHERE application_id = :a", (pending,)) == "pending"


# ── public submission ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_public_submission_intakes_then_auto_closes_the_job_after_its_commit(world, tmp_path):
    from fastapi import HTTPException, UploadFile
    from routers import public
    from workers import cv_score

    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    settings = SimpleNamespace(files_base_path=str(tmp_path / "files"), max_file_size_mb=10)
    enqueued: list = []

    async def submit(name, email):
        async with world.sessions() as s:
            upload = UploadFile(file=open(pdf, "rb"), filename="cv.pdf", headers={"content-type": "application/pdf"})
            return await public._handle_public_submission(db=s, job_code=JOB_CODE, candidate_name=name, email=email, phone=None,
                                                          cover_letter=None, file=upload)

    with patch.object(public, "settings", settings), \
            patch.object(cv_score.score_cv_task, "delay", lambda **kw: enqueued.append(kw["application_id"])), \
            patch("services.email_service.send_cv_received_email", AsyncMock(return_value=None)):
        first = asyncio.run(submit("First Candidate", "first@example.com"))
        assert first["message"] == "Application received successfully"
        assert _scalar_as(world, T1, "hr_manager", "SELECT count(*) FROM applications WHERE application_id = :a",
                          (first["application_id"],)) == 1
        # the job reached its limit (one application): the auto-close UPDATE on jobs runs after the intake commit
        assert _scalar_as(world, T1, "hr_manager", "SELECT status FROM jobs WHERE job_id = :j", (JOB_PUBLIC,)) == "closed"
        with pytest.raises(HTTPException) as exc:
            asyncio.run(submit("Second Candidate", "second@example.com"))
        assert exc.value.status_code == 404          # closed jobs are not public any more: refused before any write

    assert _scalar_as(world, T1, "hr_manager", "SELECT count(*) FROM applications WHERE job_id = :j", (JOB_PUBLIC,)) == 1
    if RUNTIME:
        # the tenant-2 user sees none of tenant 1's application for that job
        assert _scalar_as(world, T2, "hr_manager", "SELECT count(*) FROM applications WHERE job_id = :j", (JOB_PUBLIC,)) == 0


# ── scoring pipeline ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_scoring_saves_the_score_through_the_post_commit_reads_under_the_role(world, tmp_path):
    """The legacy pipeline end to end on the real database: the guard, the duplicate checks and the final score write all run as the
    restricted role, and the score row is readable by its own tenant only. The AI, the gatekeeper's file edge and the email are stubs."""
    import test_p001_scoring_path as p001
    from workers import cv_score

    cv_file = tmp_path / "cv.pdf"
    cv_file.write_bytes(b"%PDF-1.4 synthetic")
    app = _insert_app(world, JOB_LEGACY, T1, "Ahmad Nasser", path=str(cv_file))
    score_cv = AsyncMock(return_value=(dict(p001.LEGACY_AI_RESULT), {"model": "gpt-4o-mini"}))
    patches = [
        patch("services.pdf_service.extract_text_from_pdf", return_value=p001.CV_TEXT),
        patch("services.prompt_config.load_prompt_config", AsyncMock(return_value=p001._prompt_cfg(False))),
        patch("services.ai_service.load_active_prompt", AsyncMock(return_value={})),
        patch("services.duplicate_detection.check_exact_file_hash_duplicate", AsyncMock(return_value=None)),
        patch("services.duplicate_detection.check_exact_content_duplicate", AsyncMock(return_value=None)),
        patch("services.duplicate_detection.check_exact_canonical_fingerprint_duplicate", AsyncMock(return_value=None)),
        patch("services.duplicate_detection.check_high_similarity_duplicate", AsyncMock(return_value=None)),
        patch("services.security_detection.run_security_check", AsyncMock(return_value=None)),
        patch("services.local_processor.run_gatekeeper", return_value=p001._gatekeeper(True)),
        patch("services.cv_evidence.CVFactsExtractor.extract", return_value=p001._cv_facts()),
        patch("services.criteria_matcher.CriteriaMatchEngine.match", return_value=p001._match_result()),
        patch("services.threshold_service.get_thresholds", AsyncMock(return_value=(85, 65))),
        patch("services.ai_service.score_cv", score_cv),
        patch("services.ai_model_registry_service.resolve_stage_client", AsyncMock(return_value=None)),
        patch("services.ai_usage_service.log_ai_usage", AsyncMock()),
        patch("services.email_service.send_cv_received_email", AsyncMock()),
    ]
    for pt in patches:
        pt.start()
    try:
        asyncio.run(cv_score._score_cv_async(app, JOB_LEGACY, T1, str(cv_file), "application/pdf", {}, world.sessions))
    finally:
        for pt in reversed(patches):
            pt.stop()

    assert score_cv.await_count == 1
    assert _scalar_as(world, T1, "hr_manager", "SELECT processing_status FROM applications WHERE application_id = :a", (app,)) == "ai_scored"
    assert _scalar_as(world, T1, "hr_manager", "SELECT count(*) FROM application_scores WHERE application_id = :a", (app,)) == 1
    if RUNTIME:
        assert _scalar_as(world, T2, "hr_manager", "SELECT count(*) FROM application_scores WHERE application_id = :a", (app,)) == 0
        assert _scalar_as(world, T2, "hr_manager", "SELECT count(*) FROM applications WHERE application_id = :a", (app,)) == 0
