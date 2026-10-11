"""Requirements-v2 safeguards at every evaluation entry point and every legacy edit endpoint.

Synthetic fixtures only (fake database sessions, patched edges): a job whose criteria row carries the v2 marker -- or
only a v2 analysis block -- must be refused with an explicit reason, never scored, retried, zeroed or edited, while
legacy jobs behave exactly as before. Nothing here touches a real database, a model or the network."""
from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Reuse the scoring-pipeline harness (it also restores the real config/sqlalchemy modules other tests stub).
import test_p001_scoring_path as p001
from services.requirements_guard import REASON_CODE, STOPPED_REASON, UnsupportedEvaluationError

BACKEND = pathlib.Path(__file__).resolve().parent.parent
V2_BLOCK = {"requirements": {"schema_version": 2, "categories": {}}}
JOB = "00000000-0000-0000-0000-0000000000aa"
TENANT = "00000000-0000-0000-0000-000000000001"
USER = SimpleNamespace(tenant_id=TENANT, role="admin", user_id="u-1", full_name="Admin", email="a@example.com")


@pytest.fixture(autouse=True)
def _real_modules():
    with patch.dict(sys.modules, p001._REAL_MODULES):
        yield


# ── a scripted fake database ──────────────────────────────────────────────────

class Result:
    def __init__(self, first=None, scalar=None, rows=None):
        self._first, self._scalar, self._rows = first, scalar, rows or []
        self.rowcount = 0

    def mappings(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class DB:
    """Answers the guard's marker lookup; other statements are scripted by SQL substring."""

    def __init__(self, marker=None, analysis=None, rules=()):
        self.marker, self.analysis, self.rules = marker, analysis, list(rules)
        self.sql: list[str] = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        if "to_jsonb(jc)" in sql and "AS requirements_schema_version" in sql:
            return Result(first={"requirements_schema_version": self.marker, "analysis_json": self.analysis})
        for needle, result in self.rules:
            if needle in sql:
                return result
        return Result()

    def wrote(self, verb: str) -> bool:
        return any(verb in s for s in self.sql)


V2_CASES = [pytest.param(2, None, id="marker"), pytest.param(None, V2_BLOCK, id="analysis-shape-only")]


@pytest.fixture
def real_intake():
    """The real intake service (the shared conftest replaces it with a stub), restored afterwards."""
    name = "services.application_intake_service"
    saved = sys.modules.pop(name, None)
    saved_ko = sys.modules.get("services.knockout_questions_service")
    try:
        yield importlib.import_module(name)
    finally:
        sys.modules.pop(name, None)
        if saved is not None:
            sys.modules[name] = saved


@pytest.fixture
def routers(real_intake):
    """routers.applications / bulk_upload / jobs / public imported against the real intake service."""
    ko = "services.knockout_questions_service"
    saved_ko = sys.modules.pop(ko, None)
    mods = {n: importlib.import_module(f"routers.{n}") for n in ("applications", "bulk_upload", "jobs", "public")}
    try:
        yield SimpleNamespace(**mods)
    finally:
        for n in mods:
            sys.modules.pop(f"routers.{n}", None)
        sys.modules.pop(ko, None)
        if saved_ko is not None:
            sys.modules[ko] = saved_ko


# ══ direct scoring task ═══════════════════════════════════════════════════════

class TestScoringTask:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("marker,analysis", V2_CASES)
    async def test_v2_job_is_refused_permanently_and_never_scored(self, tmp_path, monkeypatch, marker, analysis):
        row = dict(p001.CRITERIA_ROW, requirements_schema_version=marker,
                   analysis_json=analysis if analysis is not None else p001.CRITERIA_ROW["analysis_json"])
        monkeypatch.setattr(p001, "CRITERIA_ROW", row)

        log, mocks = await p001._run(tmp_path)          # returns normally: an exception would trigger a Celery retry

        mocks["mark_failed"].assert_awaited_once()
        (app_id, message), kwargs = mocks["mark_failed"].await_args
        assert app_id == p001.APP_ID and kwargs["stopped_reason"] == STOPPED_REASON == "evaluation_unsupported"
        assert REASON_CODE in message
        assert p001._score_inserts(log) == []                                  # no score row, not even a zero
        assert not any("decision" in s and "UPDATE applications" in s for s, _ in log)
        mocks["assess"].assert_not_awaited()                                   # mapper never reached
        mocks["score_cv"].assert_not_awaited()                                 # legacy LLM scorer never reached

    @pytest.mark.asyncio
    async def test_the_refusal_happens_before_any_file_work(self, tmp_path, monkeypatch):
        monkeypatch.setattr(p001, "CRITERIA_ROW", dict(p001.CRITERIA_ROW, requirements_schema_version=2))
        log, _ = await p001._run(tmp_path)
        statements = [s for s, _ in log]
        assert any("processing" in s for s in statements)                      # status moved to processing
        assert not any("application_files" in s for s in statements)          # nothing extracted or stored after it

    @pytest.mark.asyncio
    async def test_legacy_job_is_unaffected(self, tmp_path):
        log, mocks = await p001._run(tmp_path)
        assert len(p001._score_inserts(log)) == 1
        mocks["mark_failed"].assert_not_awaited()


# ══ intake: manual, public, email (all via process_cv_intake) ═════════════════

def _intake_kwargs(method: str) -> dict:
    return dict(intake_method=method, job_id=JOB, tenant_id=TENANT, candidate_name="Ahmad", candidate_email="a@x.com",
                content_type="application/pdf", content=b"%PDF-1.4 synthetic", original_filename="cv.pdf",
                submission_source=method, auto_score=True, files_base_path="/tmp/unused", max_file_size_mb=5)


class TestIntake:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["manual_upload", "public_apply", "platform_email", "email_forwarding"])
    @pytest.mark.parametrize("marker,analysis", V2_CASES)
    async def test_v2_job_is_rejected_with_an_explicit_reason_and_nothing_is_stored(
            self, real_intake, method, marker, analysis):
        db = DB(marker=marker, analysis=analysis)
        log = AsyncMock(return_value="log-1")
        create = AsyncMock(side_effect=AssertionError("no application may be created"))
        enqueue = MagicMock(side_effect=AssertionError("nothing may be queued"))
        with patch.object(real_intake, "check_job_applicant_limit", AsyncMock(return_value={"allowed": True})), \
             patch.object(real_intake, "log_intake", log), \
             patch.object(real_intake, "create_application_record", create), \
             patch.object(real_intake, "enqueue_scoring", enqueue):
            result = await real_intake.process_cv_intake(db, **_intake_kwargs(method))

        assert result.status == "INTAKE_BLOCKED" and not result.success
        assert REASON_CODE in result.error_message
        create.assert_not_awaited()
        enqueue.assert_not_called()
        log.assert_awaited_once()
        assert log.await_args.kwargs["status"] == "REJECTED"
        assert REASON_CODE in log.await_args.kwargs["error_message"]
        assert log.await_args.kwargs["intake_method"] == method
        assert not db.wrote("INSERT") and not db.wrote("UPDATE")

    @pytest.mark.asyncio
    async def test_legacy_job_passes_the_guard_and_continues_to_the_next_gate(self, real_intake):
        db = DB(marker=None, analysis=None, rules=[
            ("SELECT criteria_extraction_status FROM job_criteria", Result(scalar="completed"))])
        with patch.object(real_intake, "check_job_applicant_limit", AsyncMock(return_value={"allowed": True})), \
             patch.object(real_intake, "log_intake", AsyncMock(return_value="log-1")), \
             patch.object(real_intake, "can_process_cv",
                          AsyncMock(return_value={"allowed": False, "message": "quota reached"})):
            result = await real_intake.process_cv_intake(db, **_intake_kwargs("manual_upload"))
        assert result.status == "REJECTED" and result.error_message == "quota reached"     # got past the v2 guard

    @pytest.mark.asyncio
    async def test_manual_upload_route_returns_the_explicit_reason(self, routers, real_intake):
        blocked = real_intake.IntakeResult(status="INTAKE_BLOCKED",
                                           error_message=UnsupportedEvaluationError("intake:manual_upload", JOB).message)
        file = SimpleNamespace(read=AsyncMock(return_value=b"%PDF"), content_type="application/pdf", filename="cv.pdf")
        db = DB(rules=[("FROM jobs WHERE job_id", Result(first={"job_id": JOB, "title": "t"}))])
        with patch.object(routers.applications, "process_cv_intake", AsyncMock(return_value=blocked)):
            with pytest.raises(HTTPException) as e:
                await routers.applications.upload_cv(job_id=JOB, candidate_name="A", current_user=USER, db=db,
                                                     candidate_email=None, file=file)
        assert e.value.status_code == 422 and REASON_CODE in e.value.detail

    @pytest.mark.asyncio
    async def test_manual_upload_route_keeps_the_old_message_for_ordinary_blocks(self, routers, real_intake):
        blocked = real_intake.IntakeResult(status="INTAKE_BLOCKED", error_message="CV intake is disabled ...")
        file = SimpleNamespace(read=AsyncMock(return_value=b"%PDF"), content_type="application/pdf", filename="cv.pdf")
        db = DB(rules=[("FROM jobs WHERE job_id", Result(first={"job_id": JOB, "title": "t"}))])
        with patch.object(routers.applications, "process_cv_intake", AsyncMock(return_value=blocked)):
            with pytest.raises(HTTPException) as e:
                await routers.applications.upload_cv(job_id=JOB, candidate_name="A", current_user=USER, db=db,
                                                     candidate_email=None, file=file)
        assert e.value.detail == "CV intake is disabled because the job analysis is not completed."

    @pytest.mark.asyncio
    async def test_public_apply_route_gives_the_applicant_a_generic_message(self, routers, real_intake):
        blocked = real_intake.IntakeResult(status="INTAKE_BLOCKED",
                                           error_message=UnsupportedEvaluationError("intake:public_apply").message)
        job = {"job_id": JOB, "tenant_id": TENANT, "application_deadline": None, "max_applications": None}
        file = SimpleNamespace(read=AsyncMock(return_value=b"%PDF"), content_type="application/pdf", filename="cv.pdf")
        with patch.object(routers.public, "_get_active_public_job", AsyncMock(return_value=job)), \
             patch.object(routers.public, "process_cv_intake", AsyncMock(return_value=blocked)):
            with pytest.raises(HTTPException) as e:
                await routers.public._handle_public_submission(
                    db=DB(), job_code="JOB-1", candidate_name="A", email="a@x.com", phone=None,
                    cover_letter=None, file=file)
        assert e.value.status_code == 422
        assert REASON_CODE not in e.value.detail            # internal reason is logged, not shown to applicants

    @pytest.mark.asyncio
    async def test_email_path_surfaces_the_reason_instead_of_swallowing_it(self, real_intake):
        cv_intake = importlib.import_module("workers.cv_intake")
        blocked = real_intake.IntakeResult(status="INTAKE_BLOCKED",
                                           error_message=UnsupportedEvaluationError("intake:platform_email").message)
        cfg = SimpleNamespace(files_base_path="/tmp", max_file_size_mb=5)
        with patch.object(real_intake, "process_cv_intake", AsyncMock(return_value=blocked)):
            with pytest.raises(RuntimeError, match=REASON_CODE):
                await cv_intake._create_application_and_score(
                    DB(), JOB, TENANT, "Name", "a@x.com", b"%PDF", "application/pdf", "cv.pdf", cfg,
                    ingestion_mode="platform_email")


# ══ bulk import ═══════════════════════════════════════════════════════════════

class TestBulkImport:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("marker,analysis", V2_CASES)
    async def test_service_refuses_before_any_state_change(self, marker, analysis):
        from services.bulk_upload_import_service import run_batch_import
        db = DB(marker=marker, analysis=analysis)
        with pytest.raises(UnsupportedEvaluationError) as e:
            await run_batch_import(db=db, batch_id="b-1", tenant_id=TENANT, job_id=JOB, user_id=None,
                                   user_name=None, user_email=None, include_warnings=True, settings=None)
        assert REASON_CODE in e.value.message
        assert len(db.sql) == 1                      # only the marker lookup: no batch lookup, status change or import
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_route_answers_409_with_the_reason(self, routers):
        batch = {"batch_status": "validated", "job_id": JOB, "tenant_id": TENANT}
        unsupported = UnsupportedEvaluationError("bulk_import", JOB)
        with patch.object(routers.bulk_upload, "_require_batch", AsyncMock(return_value=batch)), \
             patch("services.bulk_upload_import_service.run_batch_import", AsyncMock(side_effect=unsupported)):
            with pytest.raises(HTTPException) as e:
                await routers.bulk_upload.import_batch(batch_id="b-1", current_user=USER, db=DB(),
                                                       include_warning_rows=True)
        assert e.value.status_code == 409 and REASON_CODE in e.value.detail


# ══ batch scoring of pending uploads ══════════════════════════════════════════

class TestBatchScoring:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("marker,analysis", V2_CASES)
    async def test_v2_job_is_refused_before_any_cv_is_claimed(self, routers, marker, analysis):
        db = DB(marker=marker, analysis=analysis, rules=[("SELECT job_id FROM jobs", Result(first=(JOB,)))])
        with patch.object(routers.applications, "score_cv_task") as task:
            with pytest.raises(HTTPException) as e:
                await routers.applications.score_pending_uploads(
                    body=routers.applications.ScorePendingRequest(job_id=JOB), current_user=USER, db=db)
        assert e.value.status_code == 409 and REASON_CODE in e.value.detail
        assert not db.wrote("UPDATE applications")             # pending CVs stay pending
        task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_legacy_job_still_claims_pending_cvs(self, routers):
        db = DB(rules=[("SELECT job_id FROM jobs", Result(first=(JOB,))),
                       ("WITH claimed AS", Result(rows=[]))])
        out = await routers.applications.score_pending_uploads(
            body=routers.applications.ScorePendingRequest(job_id=JOB), current_user=USER, db=db)
        assert out["queued"] == 0 and db.wrote("WITH claimed AS")


# ══ legacy criteria edit, retry and qualifying-context endpoints ═══════════════

class TestLegacyEditEndpoints:

    def _db(self, marker, analysis):
        return DB(marker=marker, analysis=analysis, rules=[
            ("SELECT job_id FROM jobs WHERE job_id", Result(first={"job_id": JOB})),
            ("FROM jobs j", Result(first={"description": "d", "title": "t", "department": None,
                                          "experience_level": None, "location": None, "job_type": None,
                                          "work_mode": None, "criteria_extraction_status": "failed",
                                          "criteria_extraction_retry_count": 0,
                                          "criteria_last_failed_description_hash": None}))])

    def _calls(self, jobs):
        return {
            "criteria": lambda db: jobs.update_criteria(JOB, jobs.UpdateCriteriaRequest(weight_skills=100),
                                                        USER, db),
            "content": lambda db: jobs.update_criteria_content(
                JOB, jobs.UpdateCriteriaContentRequest(required_skills=["x"]), USER, db),
            "retry": lambda db: jobs.retry_criteria_extraction(JOB, USER, db),
            "qc_edit": lambda db: jobs.edit_qualifying_context(
                JOB, jobs.EditQualifyingContextRequest(state="none", contexts=[],
                                                       expected_qualifying_context=None), USER, db),
            "qc_confirm": lambda db: jobs.confirm_qualifying_context(
                JOB, jobs.ConfirmQualifyingContextRequest(expected_qualifying_context=None), USER, db),
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["criteria", "content", "retry", "qc_edit", "qc_confirm"])
    @pytest.mark.parametrize("marker,analysis", V2_CASES)
    async def test_v2_job_is_rejected_with_409_and_nothing_is_written(self, routers, name, marker, analysis):
        db = self._db(marker, analysis)
        with patch("workers.criteria_worker.extract_criteria_task") as task:
            with pytest.raises(HTTPException) as e:
                await self._calls(routers.jobs)[name](db)
            task.delay.assert_not_called()
        assert e.value.status_code == 409 and REASON_CODE in e.value.detail
        assert not (db.wrote("UPDATE job_criteria") or db.wrote("INSERT") or db.wrote("UPDATE jobs"))
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,expected", [("criteria", 404), ("content", 404)])
    async def test_legacy_job_passes_the_guard(self, routers, name, expected):
        db = self._db(None, None)
        with pytest.raises(HTTPException) as e:
            await self._calls(routers.jobs)[name](db)
        assert e.value.status_code == expected                       # reached the endpoint's own logic ("criteria not found")


# ══ legacy extraction worker ══════════════════════════════════════════════════

class TestLegacyExtractionWorker:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("marker,analysis", V2_CASES)
    async def test_v2_job_is_never_reanalysed_by_the_legacy_extraction(self, marker, analysis):
        from workers import criteria_worker as cw
        import services.ai_service as ai
        db = DB(marker=marker, analysis=analysis)
        with patch.object(ai, "extract_job_criteria", AsyncMock(side_effect=AssertionError("no model call"))), \
             patch.object(ai, "load_active_prompt", AsyncMock(side_effect=AssertionError("no prompt load"))):
            await cw._extract_async(JOB, "a job description", lambda: db, None)
        failed = [s for s in db.sql if "criteria_extraction_status = 'failed'" in s]
        assert len(failed) == 1 and not db.wrote("analysis_json  ") and not db.wrote("CAST(:aj")
        assert not any("'processing'" in s for s in db.sql)
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_failure_message_names_the_reason(self):
        from workers import criteria_worker as cw
        seen = {}

        class Recorder(DB):
            async def execute(self, stmt, params=None):
                if params and "err" in params:
                    seen.update(params)
                return await super().execute(stmt, params)
        await cw._extract_async(JOB, "d", lambda: Recorder(marker=2), None)
        assert REASON_CODE in seen["err"] and seen["jid"] == JOB


# ══ scripts ═══════════════════════════════════════════════════════════════════

class TestBackfillScriptSkipsV2:

    def _module(self):
        path = BACKEND / "scripts"
        sys.path.insert(0, str(path))
        try:
            return importlib.import_module("backfill_deterministic_scores")
        finally:
            sys.path.remove(str(path))

    def test_every_selection_excludes_requirements_v2_jobs(self):
        from services.requirements_guard import IS_V2_SQL
        mod = self._module()
        seen: list[str] = []

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                seen.append(sql)

            def fetchone(self):
                return {"n": 0}

            def fetchall(self):
                return []

        conn = SimpleNamespace(cursor=lambda: Cur())
        mod._count_pending(conn)
        mod._count_pending(conn, recalculate=True)
        mod._fetch_pending_ids(conn, None)
        mod._fetch_pending_ids(conn, 5, recalculate=True)
        mod._fetch_row(conn, "a-1")
        mod._fetch_row(conn, "a-1", recalculate=True)
        assert len(seen) == 6 and all("NOT EXISTS" in s and IS_V2_SQL in s for s in seen)
        # the update statement is never reached for skipped rows, because they are never selected
        seen.clear()
        mod._count_requirements_v2_skipped(conn)
        assert len(seen) == 1 and "NOT EXISTS" not in seen[0] and IS_V2_SQL in seen[0]
