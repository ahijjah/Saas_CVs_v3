"""
Tests for D-03.1: _build_coverage_context() in services/ai_service.py

Covers:
  - All-matched required criteria
  - All-absent required criteria (verifies absent list is populated)
  - Mixed required criteria (matched + partial + absent)
  - Preferred criteria included when present
  - Preferred criteria omitted when absent
  - Absent required list capped at 8
  - No required criteria defined (req_total == 0)
  - Exception / bad input returns empty string (never raises)
  - score_cv() accepts coverage_context kwarg without error (signature check)
  - coverage_note injected into user_prompt when coverage_context provided
  - coverage_note omitted from user_prompt when coverage_context is None

Run with:
    pytest backend/tests/test_ai_service_coverage.py -v
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Stub heavy dependencies that are unavailable in the test environment so that
# importing services.ai_service works without openai / sqlalchemy installed.
# ─────────────────────────────────────────────────────────────────────────────

def _stub_module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# openai
_openai_mod = _stub_module("openai")
_openai_mod.AsyncOpenAI = MagicMock  # type: ignore[attr-defined]

# sqlalchemy (and sub-packages)
_sa = _stub_module("sqlalchemy")
_sa.text = MagicMock()  # type: ignore[attr-defined]
_stub_module("sqlalchemy.ext")
_stub_module("sqlalchemy.ext.asyncio", AsyncSession=MagicMock)

# config
_config = _stub_module("config")


class _FakeSettings:
    openai_api_key = "sk-test"
    openai_model = "gpt-4o-mini"
    openai_base_url = None
    scoring_output_language = "English"


_config.get_settings = lambda: _FakeSettings()  # type: ignore[attr-defined]

# Now safe to import from services.ai_service
from services.ai_service import _build_coverage_context, score_cv  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_assessment(required: bool, status: str, criterion_text: str = "criterion") -> Any:
    """Create a simple namespace that mimics an LLMCriteriaAssessment."""
    return types.SimpleNamespace(
        required=required,
        status=status,
        criterion_text=criterion_text,
    )


def _make_llm_result(assessments: list[Any]) -> Any:
    """Create a simple namespace that mimics an LLMMatchResult."""
    return types.SimpleNamespace(assessments=assessments)


def _base_criteria() -> dict[str, Any]:
    return {
        "skills": [],
        "skills_required": [],
        "skills_preferred": [],
        "experience": [],
        "education": [],
        "certifications": [],
        "soft_skills": [],
        "domain_knowledge": [],
        "other_requirements": [],
        "weight_skills": 40,
        "weight_experience": 20,
        "weight_education": 10,
        "weight_certifications": 5,
        "weight_soft_skills": 10,
        "weight_domain_knowledge": 10,
        "weight_other": 5,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests for _build_coverage_context
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildCoverageContext:
    def test_all_required_matched(self):
        result = _make_llm_result([
            _make_assessment(True, "MATCHED", "Python"),
            _make_assessment(True, "MATCHED", "Django"),
        ])
        ctx = _build_coverage_context(result)
        assert "Required criteria: 2 total" in ctx
        assert "✓ Matched:  2" in ctx
        assert "△ Partial:  0" in ctx
        assert "✗ Absent:   0" in ctx
        assert "Coverage:   100%" in ctx
        assert "Absent required criteria:" not in ctx

    def test_all_required_absent(self):
        result = _make_llm_result([
            _make_assessment(True, "ABSENT", "Python"),
            _make_assessment(True, "ABSENT", "Django"),
        ])
        ctx = _build_coverage_context(result)
        assert "Required criteria: 2 total" in ctx
        assert "✗ Absent:   2" in ctx
        assert "Coverage:   0%" in ctx
        assert "Absent required criteria: Python; Django" in ctx

    def test_mixed_required_criteria(self):
        result = _make_llm_result([
            _make_assessment(True, "MATCHED", "Python"),
            _make_assessment(True, "PARTIAL", "SQL"),
            _make_assessment(True, "ABSENT", "Kubernetes"),
        ])
        ctx = _build_coverage_context(result)
        assert "Required criteria: 3 total" in ctx
        assert "✓ Matched:  1" in ctx
        assert "△ Partial:  1" in ctx
        assert "✗ Absent:   1" in ctx
        assert "Coverage:   33%" in ctx
        assert "Absent required criteria: Kubernetes" in ctx

    def test_preferred_criteria_included(self):
        result = _make_llm_result([
            _make_assessment(True, "MATCHED", "Python"),
            _make_assessment(False, "MATCHED", "Docker"),
            _make_assessment(False, "ABSENT", "Terraform"),
        ])
        ctx = _build_coverage_context(result)
        assert "Preferred criteria: 2 total" in ctx
        assert "Coverage:   50%" in ctx  # 1/2 pref matched

    def test_no_preferred_criteria_omits_section(self):
        result = _make_llm_result([
            _make_assessment(True, "MATCHED", "Python"),
        ])
        ctx = _build_coverage_context(result)
        assert "Preferred criteria:" not in ctx

    def test_absent_required_list_capped_at_8(self):
        assessments = [
            _make_assessment(True, "ABSENT", f"skill_{i}") for i in range(15)
        ]
        result = _make_llm_result(assessments)
        ctx = _build_coverage_context(result)
        for i in range(8):
            assert f"skill_{i}" in ctx
        for i in range(8, 15):
            assert f"skill_{i}" not in ctx

    def test_no_required_criteria_defined(self):
        result = _make_llm_result([
            _make_assessment(False, "MATCHED", "Docker"),
        ])
        ctx = _build_coverage_context(result)
        assert "Required criteria: 0 total" in ctx
        assert "No required criteria defined." in ctx

    def test_empty_assessments(self):
        result = _make_llm_result([])
        ctx = _build_coverage_context(result)
        assert "Required criteria: 0 total" in ctx
        assert "No required criteria defined." in ctx

    def test_exception_returns_empty_string(self):
        """Any exception must be swallowed — never raises."""
        bad = types.SimpleNamespace()  # no .assessments attribute
        ctx = _build_coverage_context(bad)
        assert ctx == ""

    def test_none_input_returns_empty_string(self):
        ctx = _build_coverage_context(None)
        assert ctx == ""

    def test_returns_calibration_footer(self):
        result = _make_llm_result([
            _make_assessment(True, "MATCHED", "Python"),
        ])
        ctx = _build_coverage_context(result)
        assert "Apply MANDATORY COVERAGE CALIBRATION rules above" in ctx

    def test_header_present(self):
        result = _make_llm_result([
            _make_assessment(True, "MATCHED", "Python"),
        ])
        ctx = _build_coverage_context(result)
        assert "=== MANDATORY CRITERIA COVERAGE (from evidence analysis) ===" in ctx

    def test_100_percent_required_coverage(self):
        result = _make_llm_result([
            _make_assessment(True, "MATCHED", "A"),
            _make_assessment(True, "MATCHED", "B"),
            _make_assessment(True, "MATCHED", "C"),
        ])
        ctx = _build_coverage_context(result)
        assert "Coverage:   100%" in ctx

    def test_partial_only_required(self):
        """Only partial matches — coverage should be 0% (matched only)."""
        result = _make_llm_result([
            _make_assessment(True, "PARTIAL", "A"),
            _make_assessment(True, "PARTIAL", "B"),
        ])
        ctx = _build_coverage_context(result)
        assert "Coverage:   0%" in ctx
        assert "△ Partial:  2" in ctx


# ─────────────────────────────────────────────────────────────────────────────
# Integration-style: score_cv() signature and coverage_note injection
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreCvCoverageIntegration:
    """Verify coverage_context is wired into score_cv() correctly."""

    def _make_fake_response(self) -> MagicMock:
        fake_choice = MagicMock()
        fake_choice.message.content = (
            '{"overall_score": 75, "score_details": {}, "reasoning": {}, '
            '"red_flags": [], "evaluation_notes": "", "strengths": [], '
            '"gaps_identified": [], "interview_questions": [], '
            '"candidate_name": "", "candidate_email": "", "candidate_phone": ""}'
        )
        fake_choice.finish_reason = "stop"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]
        fake_response.usage = MagicMock(
            prompt_tokens=100, completion_tokens=50, total_tokens=150
        )
        return fake_response

    @pytest.mark.asyncio
    async def test_score_cv_accepts_coverage_context_kwarg(self):
        """score_cv() must accept coverage_context without TypeError."""
        fake_response = self._make_fake_response()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

        result, usage = await score_cv(
            cv_text="I am a Python developer.",
            criteria=_base_criteria(),
            job_title="Software Engineer",
            openai_client=mock_client,
            coverage_context="=== MANDATORY CRITERIA COVERAGE ===\nRequired criteria: 1 total",
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_coverage_note_injected_into_prompt(self):
        """When coverage_context is provided, it must appear in the user prompt."""
        captured_messages: list[Any] = []
        fake_response = self._make_fake_response()
        mock_client = MagicMock()

        async def capture_create(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return fake_response

        mock_client.chat.completions.create = capture_create

        coverage = "=== MANDATORY CRITERIA COVERAGE (from evidence analysis) ===\nRequired criteria: 2 total"
        await score_cv(
            cv_text="I am a developer.",
            criteria=_base_criteria(),
            job_title="Engineer",
            openai_client=mock_client,
            coverage_context=coverage,
        )
        user_message = next(m for m in captured_messages if m["role"] == "user")
        assert coverage in user_message["content"]

    @pytest.mark.asyncio
    async def test_no_coverage_note_when_none(self):
        """When coverage_context is None, no coverage block in user prompt."""
        captured_messages: list[Any] = []
        fake_response = self._make_fake_response()
        mock_client = MagicMock()

        async def capture_create(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            return fake_response

        mock_client.chat.completions.create = capture_create

        await score_cv(
            cv_text="I am a developer.",
            criteria=_base_criteria(),
            job_title="Engineer",
            openai_client=mock_client,
            coverage_context=None,
        )
        user_message = next(m for m in captured_messages if m["role"] == "user")
        assert "MANDATORY CRITERIA COVERAGE" not in user_message["content"]
