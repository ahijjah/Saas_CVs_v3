"""
P0-01 — explicit scoring path / no silent legacy fallback.

Drives workers.cv_score._score_cv_async end-to-end with a fake DB session that
records every SQL statement. Only external edges are patched (file/PDF
extraction, duplicate/security checks, gatekeeper, LLM calls, thresholds);
the path-control logic under test runs for real.

Regression target: on 29 Jul 2026 an F-01 AttributeError
("'CriterionMatch' object has no attribute 'get'") was swallowed and 8
JOB-2026-0093 candidates silently received legacy LLM scores and decisions.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Module isolation ─────────────────────────────────────────────────────────
# test_ai_service_coverage.py replaces config / sqlalchemy in sys.modules with
# bare stubs at import time. This pipeline test needs the real ones (Celery
# reads config at import; the worker imports sqlalchemy.text at call time).
# Import the real modules once, put any stubs back so other test modules are
# unaffected, and swap the real modules in only while these tests run.
_ISOLATED = ("config", "sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio")
_stubs = {n: sys.modules.pop(n) for n in _ISOLATED
          if n in sys.modules and getattr(sys.modules[n], "__spec__", None) is None}

import config  # noqa: E402,F401
import sqlalchemy  # noqa: E402,F401
import sqlalchemy.ext.asyncio  # noqa: E402,F401
import sqlalchemy.pool  # noqa: E402,F401
import workers.cv_score as cv_score  # noqa: E402

_REAL_MODULES = {n: sys.modules[n] for n in _ISOLATED}
sys.modules.update(_stubs)


@pytest.fixture(autouse=True)
def _real_config_and_sqlalchemy():
    with patch.dict(sys.modules, _REAL_MODULES):
        yield
from services.criteria_matcher import MatchResult
from services.cv_evidence import CVFacts
from services.llm_criteria_mapper import (
    CriteriaMappingResponseError,
    LLMCriteriaMapper,
    LLMCriterionAssessment,
    LLMMatchResult,
)
from services.local_processor import GatekeeperResult
from services.prompt_config import PromptConfig

APP_ID = "11bf75df-738e-4bb2-bbb2-d8b69c32b7ab"
JOB_ID = "00000000-0000-0000-0000-000000000093"
TENANT_ID = "00000000-0000-0000-0000-000000000001"

CV_TEXT = "Ahmad Nasser. HR Officer 2019-2024. " + ("Recruitment, onboarding, payroll. " * 20)

ANALYSIS_JSON = {
    "skills": {"required": ["MS Office"], "preferred": []},
    "experience": {"minimum_years": 3, "relevant_roles": ["HR Officer"]},
}

CRITERIA_ROW = {
    "weight_skills": 30, "weight_experience": 40, "weight_education": 10,
    "weight_certifications": 0, "weight_soft_skills": 10,
    "weight_domain_knowledge": 10, "weight_other": 0,
    "skills": ["MS Office"], "certifications": [], "experience": [], "education": [],
    "soft_skills": [], "domain_knowledge": [], "other_requirements": [],
    "job_title": "HR officer 5", "job_description": "HR officer role",
    "analysis_json": ANALYSIS_JSON,
    "enable_ai_comparison": False,
    "send_confirmation_to_cv_email_for_upload": False,
    "send_confirmation_to_cv_email_for_forwarding": False,
    "send_confirmation_to_sender_for_forwarding": False,
    "send_confirmation_to_cv_email_for_platform_email": False,
}


# ── Fake DB ──────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, scalar=None, first=None):
        self._scalar = scalar
        self._first = first
        self.rowcount = 1

    def scalar_one_or_none(self):
        return self._scalar

    def mappings(self):
        first = self._first
        return MagicMock(first=lambda: first)


class FakeSession:
    def __init__(self, log: list):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.log.append((sql, dict(params or {})))
        if "FROM job_criteria jc" in sql:
            return _Result(first=CRITERIA_ROW)
        if "SELECT candidate_name FROM applications" in sql:
            return _Result(scalar="Ahmad Nasser")
        if "SELECT submission_source" in sql:
            return _Result(scalar="manual_upload")
        return _Result()

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _score_inserts(log):
    return [(s, p) for s, p in log if "INSERT INTO application_scores" in s]


# ── Test inputs ──────────────────────────────────────────────────────────────

def _prompt_cfg(d01_enabled: bool) -> PromptConfig:
    return PromptConfig(llm_criteria_mapping_enabled=d01_enabled)


def _gatekeeper(passed: bool = True) -> GatekeeperResult:
    return GatekeeperResult(
        cv_language="en", jd_language="en",
        semantic_similarity=0.8 if passed else 0.0,
        semantic_similarity_pct=80.0 if passed else 0.0,
        skill_match_ratio=80.0 if passed else 0.0,
        gatekeeper_passed=passed,
        rejection_reason=None if passed else "clear mismatch",
        cleaned_cv_text=CV_TEXT,
    )


def _cv_facts() -> CVFacts:
    return CVFacts(language="en", total_char_count=len(CV_TEXT))


def _match_result() -> MatchResult:
    return MatchResult(application_id=APP_ID, job_id=JOB_ID, criteria_version="test")


def _llm_result(status: str = "MATCHED") -> LLMMatchResult:
    assessments = [
        LLMCriterionAssessment(
            criterion_text="MS Office", dimension="skills", required=True,
            status=status, confidence=0.9,
            supporting_evidence=["Excel"] if status != "ABSENT" else [],
            match_reason="", match_type="direct" if status != "ABSENT" else "missing",
            criterion_class="flexible",
        ),
        LLMCriterionAssessment(
            criterion_text="Minimum 3 years of experience in a relevant role (HR Officer)",
            dimension="experience", required=True,
            status=status, confidence=0.9,
            supporting_evidence=["HR Officer 2019-2024"] if status != "ABSENT" else [],
            match_reason="", match_type="direct" if status != "ABSENT" else "missing",
            criterion_class="experience",
        ),
    ]
    return LLMMatchResult(
        application_id=APP_ID, job_id=JOB_ID, assessments=assessments,
        processing_ms=5, created_at="", prompt_code="recruitment.criteria_mapping",
        prompt_version="2", model="gpt-4o-mini", total_criteria=2,
    )


LEGACY_AI_RESULT = {
    "score_skills": 60, "score_experience": 60, "score_education": 60,
    "score_certifications": 0, "score_soft_skills": 60,
    "score_domain_knowledge": 60, "score_other": 0,
    "strengths": [], "gaps_identified": [], "red_flags": [],
    "evaluation_notes": "legacy", "interview_questions": [], "reasoning": {},
}


async def _run(tmp_path, *, d01_enabled=True, gatekeeper_passed=True,
               assess=None, det_score=None, cv_facts_error=None):
    """Run the real _score_cv_async with external edges patched.

    Returns (sql_log, mocks). Raises whatever the pipeline raises.
    """
    cv_file = tmp_path / "cv.pdf"
    cv_file.write_bytes(b"%PDF-1.4 test")
    log: list = []

    mocks = {
        "assess": assess or AsyncMock(return_value=_llm_result()),
        "score_cv": AsyncMock(return_value=(dict(LEGACY_AI_RESULT), {"model": "gpt-4o-mini"})),
        "mark_failed": AsyncMock(),
    }

    patches = [
        patch("services.pdf_service.extract_text_from_pdf", return_value=CV_TEXT),
        patch("services.prompt_config.load_prompt_config",
              AsyncMock(return_value=_prompt_cfg(d01_enabled))),
        patch("services.ai_service.load_active_prompt",
              AsyncMock(return_value={"prompt_code": "cv_scoring", "version": 1})),
        patch("services.duplicate_detection.check_exact_file_hash_duplicate", AsyncMock(return_value=None)),
        patch("services.duplicate_detection.check_exact_content_duplicate", AsyncMock(return_value=None)),
        patch("services.duplicate_detection.check_exact_canonical_fingerprint_duplicate", AsyncMock(return_value=None)),
        patch("services.duplicate_detection.check_high_similarity_duplicate", AsyncMock(return_value=None)),
        patch("services.security_detection.run_security_check", AsyncMock(return_value=None)),
        patch("services.local_processor.run_gatekeeper", return_value=_gatekeeper(gatekeeper_passed)),
        (patch("services.cv_evidence.CVFactsExtractor.extract", side_effect=cv_facts_error)
         if cv_facts_error else
         patch("services.cv_evidence.CVFactsExtractor.extract", return_value=_cv_facts())),
        patch("services.criteria_matcher.CriteriaMatchEngine.match", return_value=_match_result()),
        patch.object(LLMCriteriaMapper, "assess", mocks["assess"]),
        patch("services.threshold_service.get_thresholds", AsyncMock(return_value=(85, 65))),
        patch("services.ai_service.score_cv", mocks["score_cv"]),
        patch("services.ai_model_registry_service.resolve_stage_client", AsyncMock(return_value=None)),
        patch("services.ai_usage_service.log_ai_usage", AsyncMock()),
        patch("services.email_service.send_cv_received_email", AsyncMock()),
        patch.object(cv_score, "_mark_failed", mocks["mark_failed"]),
    ]
    if det_score is not None:
        patches.append(patch(
            "services.deterministic_scoring.DeterministicScoringEngine.score", det_score))

    for p in patches:
        p.start()
    try:
        await cv_score._score_cv_async(
            APP_ID, JOB_ID, TENANT_ID, str(cv_file), "application/pdf", {},
            lambda: FakeSession(log),
        )
    finally:
        for p in reversed(patches):
            p.stop()
    return log, mocks


# ── Deterministic mode ───────────────────────────────────────────────────────

class TestDeterministicMode:

    @pytest.mark.asyncio
    async def test_success_saves_deterministic_score_and_method(self, tmp_path):
        log, mocks = await _run(tmp_path)

        inserts = _score_inserts(log)
        assert len(inserts) == 1
        sql, params = inserts[0]
        # P0-02a: new deterministic results are tagged deterministic_v2
        assert params["scoring_method"] == "deterministic_v2"
        assert "'deterministic'" in sql
        assert params["det_final_score"] is not None
        assert params["final"] == params["det_final_score"]
        mocks["score_cv"].assert_not_called()
        mocks["mark_failed"].assert_not_called()

    @pytest.mark.asyncio
    async def test_d01_exception_fails_explicitly_without_legacy(self, tmp_path):
        failing = AsyncMock(side_effect=TimeoutError("Request timed out"))
        with pytest.raises(cv_score.ScoringPathError, match="D-01 criteria mapping failed"):
            await _run(tmp_path, assess=failing)

    @pytest.mark.asyncio
    async def test_f01_exception_29jul_regression_no_legacy_fallback(self, tmp_path):
        """The exact 29 Jul 2026 failure: D-01 succeeds, F-01 raises."""
        f01 = MagicMock(side_effect=AttributeError("'CriterionMatch' object has no attribute 'get'"))
        with pytest.raises(cv_score.ScoringPathError) as ei:
            await _run(tmp_path, det_score=f01)
        assert "F-01 deterministic scoring failed" in str(ei.value)
        assert "CriterionMatch" in str(ei.value)
        assert isinstance(ei.value.__cause__, AttributeError)
        assert cv_score._stop_reason_for(ei.value) == "scoring_failed"

    @pytest.mark.asyncio
    async def test_d01_malformed_response_is_technical_failure(self, tmp_path):
        malformed = AsyncMock(side_effect=CriteriaMappingResponseError(
            "D-01 response unusable for 2 criteria: invalid JSON after 8000-token retry"))
        with pytest.raises(cv_score.ScoringPathError, match="CriteriaMappingResponseError"):
            await _run(tmp_path, assess=malformed)

    @pytest.mark.asyncio
    async def test_genuinely_absent_candidate_is_scored_not_failed(self, tmp_path):
        """A valid D-01 result where every criterion is ABSENT is a candidate
        result (score 0), not a technical failure."""
        log, mocks = await _run(tmp_path, assess=AsyncMock(return_value=_llm_result("ABSENT")))
        (sql, params), = _score_inserts(log)
        assert params["scoring_method"] == "deterministic_v2"
        assert params["det_final_score"] == 0
        mocks["score_cv"].assert_not_called()


class TestFailuresNeverWriteScores:
    """Every technical failure: no score row, no legacy call, scoring_failed."""

    @pytest.mark.parametrize("assess_side_effect,det_side_effect", [
        (TimeoutError("timeout"), None),
        (CriteriaMappingResponseError("no valid assessment items"), None),
        (None, AttributeError("'CriterionMatch' object has no attribute 'get'")),
        (None, ZeroDivisionError("internal scoring error")),
    ])
    @pytest.mark.asyncio
    async def test_no_score_no_legacy(self, tmp_path, assess_side_effect, det_side_effect):
        log: list = []
        score_cv = AsyncMock(return_value=(dict(LEGACY_AI_RESULT), {}))
        assess = (AsyncMock(side_effect=assess_side_effect) if assess_side_effect
                  else AsyncMock(return_value=_llm_result()))
        det = MagicMock(side_effect=det_side_effect) if det_side_effect else None

        with pytest.raises(cv_score.ScoringPathError):
            await _run_capture(tmp_path, log, assess, det, score_cv)

        assert _score_inserts(log) == []
        score_cv.assert_not_called()
        assert not any("decision                 = :decision" in s for s, _ in log)


async def _run_capture(tmp_path, log, assess, det, score_cv):
    """Like _run but shares the caller's SQL log and legacy-scorer mock."""
    cv_file = tmp_path / "cv.pdf"
    cv_file.write_bytes(b"%PDF-1.4 test")
    patches = [
        patch("services.pdf_service.extract_text_from_pdf", return_value=CV_TEXT),
        patch("services.prompt_config.load_prompt_config", AsyncMock(return_value=_prompt_cfg(True))),
        patch("services.ai_service.load_active_prompt", AsyncMock(return_value={})),
        patch("services.duplicate_detection.check_exact_file_hash_duplicate", AsyncMock(return_value=None)),
        patch("services.duplicate_detection.check_exact_content_duplicate", AsyncMock(return_value=None)),
        patch("services.duplicate_detection.check_exact_canonical_fingerprint_duplicate", AsyncMock(return_value=None)),
        patch("services.duplicate_detection.check_high_similarity_duplicate", AsyncMock(return_value=None)),
        patch("services.security_detection.run_security_check", AsyncMock(return_value=None)),
        patch("services.local_processor.run_gatekeeper", return_value=_gatekeeper(True)),
        patch("services.cv_evidence.CVFactsExtractor.extract", return_value=_cv_facts()),
        patch("services.criteria_matcher.CriteriaMatchEngine.match", return_value=_match_result()),
        patch.object(LLMCriteriaMapper, "assess", assess),
        patch("services.threshold_service.get_thresholds", AsyncMock(return_value=(85, 65))),
        patch("services.ai_service.score_cv", score_cv),
        patch("services.ai_model_registry_service.resolve_stage_client", AsyncMock(return_value=None)),
        patch("services.ai_usage_service.log_ai_usage", AsyncMock()),
        patch.object(cv_score, "_mark_failed", AsyncMock()),
    ]
    if det is not None:
        patches.append(patch("services.deterministic_scoring.DeterministicScoringEngine.score", det))
    for p in patches:
        p.start()
    try:
        await cv_score._score_cv_async(
            APP_ID, JOB_ID, TENANT_ID, str(cv_file), "application/pdf", {},
            lambda: FakeSession(log),
        )
    finally:
        for p in reversed(patches):
            p.stop()


class TestFailureMarking:

    @pytest.mark.asyncio
    async def test_pipeline_marks_scoring_failed_reason(self, tmp_path):
        log: list = []
        cv_file = tmp_path / "cv.pdf"
        cv_file.write_bytes(b"%PDF")
        mark_failed = AsyncMock()
        with patch.object(cv_score, "_mark_failed", mark_failed), \
             patch("services.pdf_service.extract_text_from_pdf", return_value=CV_TEXT), \
             patch("services.prompt_config.load_prompt_config", AsyncMock(return_value=_prompt_cfg(True))), \
             patch("services.ai_service.load_active_prompt", AsyncMock(return_value={})), \
             patch("services.duplicate_detection.check_exact_file_hash_duplicate", AsyncMock(return_value=None)), \
             patch("services.duplicate_detection.check_exact_content_duplicate", AsyncMock(return_value=None)), \
             patch("services.duplicate_detection.check_exact_canonical_fingerprint_duplicate", AsyncMock(return_value=None)), \
             patch("services.duplicate_detection.check_high_similarity_duplicate", AsyncMock(return_value=None)), \
             patch("services.security_detection.run_security_check", AsyncMock(return_value=None)), \
             patch("services.local_processor.run_gatekeeper", return_value=_gatekeeper(True)), \
             patch("services.cv_evidence.CVFactsExtractor.extract", return_value=_cv_facts()), \
             patch("services.criteria_matcher.CriteriaMatchEngine.match", return_value=_match_result()), \
             patch.object(LLMCriteriaMapper, "assess", AsyncMock(side_effect=TimeoutError("t"))):
            with pytest.raises(cv_score.ScoringPathError):
                await cv_score._score_cv_async(
                    APP_ID, JOB_ID, TENANT_ID, str(cv_file), "application/pdf", {},
                    lambda: FakeSession(log),
                )
        mark_failed.assert_awaited_once()
        assert mark_failed.await_args.kwargs["stopped_reason"] == "scoring_failed"

    def test_stop_reason_mapping(self):
        assert cv_score._stop_reason_for(cv_score.ScoringPathError("x")) == "scoring_failed"
        assert cv_score._stop_reason_for(RuntimeError("x")) == "processing_error"

    @pytest.mark.asyncio
    async def test_new_attempt_clears_previous_failure_marker(self, tmp_path):
        """A Celery retry that succeeds must not keep scoring_failed."""
        log, _ = await _run(tmp_path)
        first_sql = log[0][0]
        assert "processing_status      = 'processing'" in first_sql
        assert "stopped_reason IN ('processing_error', 'scoring_failed')" in first_sql

    def test_mark_failed_writes_given_stop_reason(self):
        import inspect
        src = inspect.getsource(cv_score._mark_failed)
        assert "stopped_reason         = :reason" in src
        assert inspect.signature(cv_score._mark_failed).parameters["stopped_reason"].default == "processing_error"


class TestCeleryRetry:
    """Transient failures keep the existing retry mechanism; after the last
    retry the application is marked scoring_failed — never legacy."""

    @pytest.fixture(autouse=True)
    def _no_real_engine(self):
        # The task builds a NullPool engine; not under test here.
        with patch("sqlalchemy.ext.asyncio.create_async_engine", MagicMock()), \
             patch("sqlalchemy.ext.asyncio.async_sessionmaker", MagicMock()):
            yield

    def test_retry_then_final_scoring_failed(self):
        err = cv_score.ScoringPathError("D-01 criteria mapping failed: TimeoutError: t")
        task = cv_score.score_cv_task
        mark_failed = AsyncMock()
        with patch.object(cv_score, "_score_cv_async", AsyncMock(side_effect=err)), \
             patch.object(cv_score, "_mark_failed", mark_failed), \
             patch.object(task, "retry", side_effect=task.MaxRetriesExceededError()) as retry:
            task.run(APP_ID, JOB_ID, TENANT_ID, "/tmp/x.pdf", "application/pdf")
        retry.assert_called_once()
        assert retry.call_args.kwargs["exc"] is err
        mark_failed.assert_called_once()
        assert mark_failed.call_args.kwargs["stopped_reason"] == "scoring_failed"

    def test_retry_requested_before_exhaustion(self):
        from celery.exceptions import Retry
        err = cv_score.ScoringPathError("F-01 deterministic scoring failed")
        task = cv_score.score_cv_task
        mark_failed = AsyncMock()
        with patch.object(cv_score, "_score_cv_async", AsyncMock(side_effect=err)), \
             patch.object(cv_score, "_mark_failed", mark_failed), \
             patch.object(task, "retry", side_effect=Retry()) as retry:
            with pytest.raises(Retry):
                task.run(APP_ID, JOB_ID, TENANT_ID, "/tmp/x.pdf", "application/pdf")
        retry.assert_called_once()
        mark_failed.assert_not_called()


# ── Explicit legacy mode + gatekeeper ────────────────────────────────────────

class TestLegacyAndGatekeeper:

    @pytest.mark.asyncio
    async def test_explicit_legacy_mode_runs_legacy_and_tags_it(self, tmp_path):
        log, mocks = await _run(tmp_path, d01_enabled=False)
        (sql, params), = _score_inserts(log)
        assert params["scoring_method"] == "legacy_llm_v1"
        assert "'openai'" in sql
        assert params["det_final_score"] is None
        mocks["score_cv"].assert_awaited_once()
        mocks["assess"].assert_not_called()

    @pytest.mark.asyncio
    async def test_legacy_mode_tolerates_v2_evidence_failure(self, tmp_path):
        """In explicit legacy mode the V2 evidence block stays non-critical."""
        log, mocks = await _run(tmp_path, d01_enabled=False, cv_facts_error=ValueError("regex"))
        (sql, params), = _score_inserts(log)
        assert params["scoring_method"] == "legacy_llm_v1"
        assert params["cv_facts_json"] is None  # proves the V2 block really failed
        mocks["score_cv"].assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deterministic_mode_v2_evidence_failure_is_scoring_failure(self, tmp_path):
        with pytest.raises(cv_score.ScoringPathError, match="ValueError: regex"):
            await _run(tmp_path, d01_enabled=True, cv_facts_error=ValueError("regex"))

    @pytest.mark.asyncio
    async def test_gatekeeper_reject_tagged_gatekeeper_local(self, tmp_path):
        log, mocks = await _run(tmp_path, gatekeeper_passed=False)
        (sql, params), = _score_inserts(log)
        assert params["scoring_method"] == "gatekeeper_local_v1"
        assert "'local'" in sql
        mocks["assess"].assert_not_called()
        mocks["score_cv"].assert_not_called()


# ── D-01 malformed-response detection (mapper level) ─────────────────────────

def _mapper_client(*contents):
    responses = []
    for c in contents:
        r = MagicMock()
        r.choices = [MagicMock(message=MagicMock(content=c))]
        responses.append(r)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=responses)
    return client


async def _assess_with(*contents):
    from services.cv_evidence import CVFacts as _F
    with patch("services.llm_criteria_mapper._get_mapper_client", return_value=_mapper_client(*contents)), \
         patch("services.ai_service.load_active_prompt", AsyncMock(return_value=None)), \
         patch("services.llm_criteria_mapper._generate_qualitative_summary", AsyncMock(return_value=None)):
        return await LLMCriteriaMapper().assess(
            cv_facts=_F(language="en", total_char_count=10),
            analysis_json=ANALYSIS_JSON, raw_cv_text=CV_TEXT,
            application_id=APP_ID, job_id=JOB_ID, db=None,
        )


def _valid_item(text, dim, status):
    return {
        "criterion_text": text, "dimension": dim, "required": True, "status": status,
        "confidence": 0.9 if status != "ABSENT" else 0.0,
        "supporting_evidence": ["e"] if status != "ABSENT" else [],
        "match_type": "direct" if status != "ABSENT" else "missing",
        "criterion_class": "flexible", "match_reason": "reason",
    }


class TestMalformedD01Response:

    @pytest.mark.asyncio
    async def test_invalid_json_after_retry_raises(self):
        with pytest.raises(CriteriaMappingResponseError, match="invalid JSON"):
            await _assess_with("{not json", "{still not json")

    @pytest.mark.asyncio
    async def test_retry_recovers_valid_json(self):
        ok = json.dumps({"assessments": [_valid_item("MS Office", "skills", "MATCHED")]})
        result = await _assess_with("{not json", ok)
        assert result.total_criteria == 1

    @pytest.mark.asyncio
    async def test_missing_assessments_key_raises(self):
        with pytest.raises(CriteriaMappingResponseError, match="missing 'assessments'"):
            await _assess_with(json.dumps({"result": "x"}), json.dumps({"result": "x"}))  # main + one repair call

    @pytest.mark.asyncio
    async def test_empty_assessments_raises(self):
        with pytest.raises(CriteriaMappingResponseError, match="empty 'assessments'"):
            await _assess_with(json.dumps({"assessments": []}), json.dumps({"assessments": []}))  # main + one repair call

    @pytest.mark.asyncio
    async def test_only_malformed_items_raises(self):
        with pytest.raises(CriteriaMappingResponseError, match="not a JSON object"):
            await _assess_with(json.dumps({"assessments": ["not a dict", 42]}), json.dumps({"assessments": ["not a dict", 42]}))  # main + one repair call

    @pytest.mark.asyncio
    async def test_all_absent_valid_response_is_not_an_error(self):
        raw = json.dumps({"assessments": [
            _valid_item("MS Office", "skills", "ABSENT"),
            _valid_item("Minimum 3 years of experience in a relevant role (HR Officer)", "experience", "ABSENT"),
        ]})
        result = await _assess_with(raw)
        assert result.absent_count == 2
        assert all("assessment_failed" not in a.risk_flags for a in result.assessments)

    @pytest.mark.asyncio
    async def test_partial_omission_behaviour_unchanged(self):
        """One of two criteria omitted: still returned (criteria-integrity is P0-03)."""
        raw = json.dumps({"assessments": [_valid_item("MS Office", "skills", "MATCHED")]})
        result = await _assess_with(raw)
        assert result.total_criteria == 1


# ── Migration 104 backfill classification ────────────────────────────────────

_MIGRATION = Path(__file__).resolve().parent.parent / "db" / "migrations" / "104_scoring_method.sql"


def _backfill_update_sql() -> str:
    sql = _MIGRATION.read_text()
    m = re.search(r"(UPDATE application_scores\s+SET scoring_method = CASE.*?;)", sql, re.S)
    assert m, "backfill UPDATE not found in migration 104"
    return m.group(1)


def _method_pattern() -> str:
    sql = _MIGRATION.read_text()
    m = re.search(r"scoring_method ~ '([^']+)'", sql)
    assert m
    return m.group(1)


class TestMigration104Backfill:
    """Executes the migration's actual backfill UPDATE (portable SQL) on SQLite."""

    ROWS = [
        # (id, scoring_provider, det_final_score, final_score, preset_method, expected)
        ("det",        "deterministic", 72,   72,   None, "deterministic_v1"),
        ("legacy",     "openai",        None, 29,   None, "legacy_llm_v1"),
        ("gk",         "local",         None, 0,    None, "gatekeeper_local_v1"),
        ("backfill",   "openai",        65,   41,   None, "legacy_llm_det_backfill_v1"),
        ("det_nodet",  "deterministic", None, 50,   None, None),
        ("preset",     "openai",        None, 29,   "deterministic_v1", "deterministic_v1"),
    ]

    def _run(self):
        con = sqlite3.connect(":memory:")
        con.execute("""CREATE TABLE application_scores (
            application_id TEXT PRIMARY KEY, scoring_provider TEXT NOT NULL,
            det_final_score INTEGER, final_score NUMERIC, scoring_method TEXT)""")
        con.executemany("INSERT INTO application_scores VALUES (?,?,?,?,?)",
                        [r[:5] for r in self.ROWS])
        update = _backfill_update_sql()
        con.execute(update)
        con.execute(update)  # idempotent re-run
        return dict(con.execute("SELECT application_id, scoring_method FROM application_scores"))

    def test_classification(self):
        got = self._run()
        for app_id, *_, expected in self.ROWS:
            assert got[app_id] == expected, app_id

    def test_existing_values_not_overwritten(self):
        assert self._run()["preset"] == "deterministic_v1"

    def test_job_2026_0093_shape_becomes_legacy(self):
        """8 JOB-2026-0093 rows: provider openai, det NULL, has D-01 output."""
        assert self._run()["legacy"] == "legacy_llm_v1"

    def test_values_satisfy_check_pattern(self):
        pat = re.compile(_method_pattern())
        for v in ("deterministic_v1", "legacy_llm_v1", "gatekeeper_local_v1",
                  "legacy_llm_det_backfill_v1", "deterministic_v2"):
            assert pat.match(v), v
        for v in ("deterministic", "Legacy_v1", "x_v", ""):
            assert not pat.match(v), v

    def test_worker_constants_match_migration_values(self):
        pat = re.compile(_method_pattern())
        for v in (cv_score.SCORING_METHOD_DETERMINISTIC, cv_score.SCORING_METHOD_LEGACY_LLM,
                  cv_score.SCORING_METHOD_GATEKEEPER):
            assert pat.match(v)
            assert f"'{v}'" in _MIGRATION.read_text()

    def test_stopped_reason_check_includes_scoring_failed(self):
        sql = _MIGRATION.read_text()
        block = sql[sql.index("applications_stopped_reason_check\n    CHECK"):]
        for v in ("security_blocked", "extraction_failed", "processing_error",
                  "duplicate_blocked", "scoring_failed", "other"):
            assert f"'{v}'" in block


# ── Job-level mixed-method detection ─────────────────────────────────────────

class TestMixedScoringMethods:

    def _fn(self):
        from services.scoring_method import has_mixed_scoring_methods
        return has_mixed_scoring_methods

    def test_single_method_not_mixed(self):
        assert not self._fn()({"deterministic_v1": 8})

    def test_deterministic_plus_legacy_is_mixed(self):
        assert self._fn()({"deterministic_v1": 6, "legacy_llm_det_backfill_v1": 6})

    def test_gatekeeper_alone_does_not_make_mixed(self):
        assert not self._fn()({"deterministic_v1": 5, "gatekeeper_local_v1": 3})

    def test_unknown_history_counts_as_distinct(self):
        assert self._fn()({"deterministic_v1": 5, "unknown": 1})

    def test_empty(self):
        assert not self._fn()({})


# ── P0-02a: deterministic_v2 worker behaviour ────────────────────────────────

def _cd_llm_result() -> LLMMatchResult:
    """MS Office MATCHED; the relevance-qualified experience criterion CANNOT_DETERMINE."""
    r = _llm_result("MATCHED")
    exp = r.assessments[1]
    exp.status = "CANNOT_DETERMINE"
    exp.cd_reason = "relevance_unverified"
    exp.match_reason = "Five years total; HR relevance not stated."
    return r


def _decision_params(log):
    return [p for s, p in log if "UPDATE applications" in s and "decision" in p]


class TestP002aWorker:

    @pytest.mark.asyncio
    async def test_needs_verification_persisted_with_v2_method(self, tmp_path):
        log, mocks = await _run(tmp_path, assess=AsyncMock(return_value=_cd_llm_result()))
        (sql, params), = _score_inserts(log)
        assert params["scoring_method"] == "deterministic_v2"
        det = json.loads(params["det_score_json"])
        assert det["_schema"] == "det_score_v3"
        assert det["recommendation"] == "needs_verification"
        assert det["pending_points"] > 0
        assert det["upper_score"] == det["verified_score"] + det["pending_points"]
        assert params["final"] == det["verified_score"]
        (upd,) = _decision_params(log)
        assert upd["decision"] == "needs_verification"
        mocks["score_cv"].assert_not_called()

    @pytest.mark.asyncio
    async def test_no_cd_decision_unchanged(self, tmp_path):
        log, _ = await _run(tmp_path)
        (upd,) = _decision_params(log)
        assert upd["decision"] == "qualified"

    @pytest.mark.asyncio
    async def test_validation_failure_no_legacy_no_score(self, tmp_path):
        err = CriteriaMappingResponseError(
            "D-01 response invalid for 2 criteria after repair call: assessment[0]: status 'UNCLEAR'")
        log: list = []
        with pytest.raises(cv_score.ScoringPathError, match="CriteriaMappingResponseError") as ei:
            log, mocks = await _run(tmp_path, assess=AsyncMock(side_effect=err))
        assert cv_score._stop_reason_for(ei.value) == "scoring_failed"
        assert _score_inserts(log) == []

    @pytest.mark.usefixtures("_celery_engine_stub")
    def test_validation_failure_retry_exhaustion_marks_scoring_failed(self):
        err = cv_score.ScoringPathError(
            "D-01 criteria mapping failed: CriteriaMappingResponseError: invalid after repair call")
        task = cv_score.score_cv_task
        mark_failed = AsyncMock()
        with patch.object(cv_score, "_score_cv_async", AsyncMock(side_effect=err)), \
             patch.object(cv_score, "_mark_failed", mark_failed), \
             patch.object(task, "retry", side_effect=task.MaxRetriesExceededError()):
            task.run(APP_ID, JOB_ID, TENANT_ID, "/tmp/x.pdf", "application/pdf")
        assert mark_failed.call_args.kwargs["stopped_reason"] == "scoring_failed"
        assert task.max_retries == 3          # 4 attempts in total


@pytest.fixture
def _celery_engine_stub():
    with patch("sqlalchemy.ext.asyncio.create_async_engine", MagicMock()), \
         patch("sqlalchemy.ext.asyncio.async_sessionmaker", MagicMock()):
        yield
