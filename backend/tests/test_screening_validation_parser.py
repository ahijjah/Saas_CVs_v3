"""
Tests for screening_validation_service._parse_screening_results().

Prevents regressions in AI response parsing. Every test case maps to a
production bug or edge case discovered when the AI returned alternative
field names or partial JSON.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Stub external dependencies so the service can be imported without
#    a running database, Redis, or OpenAI client. ──────────────────────────────
from unittest.mock import MagicMock

for _mod in (
    "openai", "openai.AsyncOpenAI",
    "sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio",
    "config",
    "services.ai_service",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Give sqlalchemy.text a pass-through stub so `text(...)` doesn't blow up.
import sqlalchemy as _sa
_sa.text = lambda s: s  # type: ignore[assignment]

# ─────────────────────────────────────────────────────────────────────────────

import pytest
from services.screening_validation_service import (
    _parse_screening_results,
    _SUGGESTION_DEFAULT_CONFIDENCE,
    _VALID_STATUSES,
    ValidationResult,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

QID_UNANSWERED = "aaaaaaaa-0000-0000-0000-000000000001"
QID_ANSWERED   = "aaaaaaaa-0000-0000-0000-000000000002"

QUESTIONS = [
    {"question_id": QID_UNANSWERED},
    {"question_id": QID_ANSWERED},
]

# QID_ANSWERED has a final answer; QID_UNANSWERED does not.
FINAL_ANSWERS = {
    QID_ANSWERED: {"value": "Bachelor's Degree", "source": "candidate_form"},
}


def _parse(raw_results, final_answers=None):
    return _parse_screening_results(raw_results, QUESTIONS, final_answers or {})


# ── 1. Status field variants ──────────────────────────────────────────────────

class TestStatusFieldVariants:
    def test_validation_status_key(self):
        results = _parse([{"question_id": QID_ANSWERED, "validation_status": "supported_by_cv"}], FINAL_ANSWERS)
        assert results[0].validation_status == "supported_by_cv"

    def test_validation_result_key(self):
        """AI returned 'validation_result' instead of 'validation_status'."""
        results = _parse([{"question_id": QID_UNANSWERED, "validation_result": "suggested",
                           "suggested_answer": "Master's Degree or equivalent"}])
        assert results[0].validation_status == "suggested"

    def test_result_key(self):
        """Root cause of production bug: AI returns 'result' key."""
        results = _parse([{"question_id": QID_UNANSWERED, "result": "suggested",
                           "suggested_answer": "Master's Degree or equivalent"}])
        assert results[0].validation_status == "suggested"

    @pytest.mark.parametrize("field_name", ["validation_status", "validation_result", "result"])
    def test_all_key_variants_map_to_same_output(self, field_name):
        results = _parse([{"question_id": QID_UNANSWERED, field_name: "suggested",
                           "suggested_answer": "Master's"}])
        assert results[0].validation_status == "suggested"
        assert results[0].suggested_answer == "Master's"


# ── 2. Suggested answer preservation ─────────────────────────────────────────

class TestSuggestedAnswerPreservation:
    def test_production_case_result_key_suggestion_preserved(self):
        """Complete production-failing case:
        AI returns result=suggested + suggested_answer — parser must store both.
        """
        results = _parse([{
            "question_id": QID_UNANSWERED,
            "result": "suggested",
            "suggested_answer": "Master's Degree or equivalent",
        }])
        r = results[0]
        assert r.validation_status == "suggested"
        assert r.suggested_answer == "Master's Degree or equivalent"

    def test_suggested_answer_stripped(self):
        results = _parse([{"question_id": QID_UNANSWERED, "result": "suggested",
                           "suggested_answer": "  Bachelor's  "}])
        assert results[0].suggested_answer == "Bachelor's"

    def test_suggested_answer_nulled_when_cannot_determine(self):
        results = _parse([{"question_id": QID_UNANSWERED,
                           "validation_status": "cannot_determine",
                           "suggested_answer": "should be discarded"}])
        assert results[0].suggested_answer is None

    def test_suggested_answer_nulled_for_answered_question(self):
        """Answered question must never get a suggested_answer."""
        results = _parse([{"question_id": QID_ANSWERED, "validation_status": "suggested",
                           "suggested_answer": "something"}], FINAL_ANSWERS)
        assert results[0].suggested_answer is None


# ── 3. Missing confidence handling ───────────────────────────────────────────

class TestMissingConfidence:
    def test_missing_confidence_yields_none_in_validation(self):
        results = _parse([{"question_id": QID_UNANSWERED, "result": "suggested",
                           "suggested_answer": "Master's"}])
        assert results[0].confidence is None

    def test_confidence_float_when_provided(self):
        results = _parse([{"question_id": QID_UNANSWERED, "result": "suggested",
                           "suggested_answer": "Master's", "confidence": 0.8}])
        assert results[0].confidence == pytest.approx(0.8)

    def test_confidence_clamped_to_1(self):
        results = _parse([{"question_id": QID_UNANSWERED, "result": "suggested",
                           "suggested_answer": "X", "confidence": 99}])
        assert results[0].confidence == pytest.approx(1.0)

    def test_confidence_clamped_to_0(self):
        results = _parse([{"question_id": QID_UNANSWERED, "result": "suggested",
                           "suggested_answer": "X", "confidence": -5}])
        assert results[0].confidence == pytest.approx(0.0)

    def test_invalid_confidence_string_yields_none(self):
        results = _parse([{"question_id": QID_UNANSWERED, "result": "suggested",
                           "suggested_answer": "X", "confidence": "high"}])
        assert results[0].confidence is None

    def test_cannot_determine_forces_confidence_none(self):
        results = _parse([{"question_id": QID_UNANSWERED,
                           "validation_status": "cannot_determine", "confidence": 0.9}])
        assert results[0].confidence is None

    def test_cannot_validate_forces_confidence_none(self):
        results = _parse([{"question_id": QID_ANSWERED,
                           "validation_status": "cannot_validate", "confidence": 0.9}], FINAL_ANSWERS)
        assert results[0].confidence is None


# ── 4. Invalid / unsupported status → fallback ──────────────────────────────

class TestInvalidStatusFallback:
    def test_invalid_status_unanswered_defaults_cannot_determine(self):
        results = _parse([{"question_id": QID_UNANSWERED, "validation_status": "banana"}])
        assert results[0].validation_status == "cannot_determine"

    def test_invalid_status_answered_defaults_cannot_validate(self):
        results = _parse([{"question_id": QID_ANSWERED, "validation_status": "banana"}], FINAL_ANSWERS)
        assert results[0].validation_status == "cannot_validate"

    def test_missing_status_unanswered_defaults_cannot_determine(self):
        results = _parse([{"question_id": QID_UNANSWERED}])
        assert results[0].validation_status == "cannot_determine"

    def test_missing_status_answered_defaults_cannot_validate(self):
        results = _parse([{"question_id": QID_ANSWERED}], FINAL_ANSWERS)
        assert results[0].validation_status == "cannot_validate"

    def test_empty_string_status_treated_as_missing(self):
        results = _parse([{"question_id": QID_UNANSWERED, "validation_status": ""}])
        assert results[0].validation_status == "cannot_determine"


# ── 5. cannot_determine nulls suggestion + confidence + evidence ─────────────

class TestCannotDetermineNulls:
    def test_all_nullable_fields_cleared(self):
        results = _parse([{
            "question_id":    QID_UNANSWERED,
            "validation_status": "cannot_determine",
            "suggested_answer": "any",
            "confidence":    0.9,
            "evidence_text": "some evidence",
        }])
        r = results[0]
        assert r.suggested_answer is None
        assert r.confidence      is None
        assert r.evidence_text   is None

    def test_reasoning_summary_still_available_for_cannot_determine(self):
        """reasoning_summary is not cleared — it can explain why no determination was made."""
        results = _parse([{
            "question_id":       QID_UNANSWERED,
            "validation_status": "cannot_determine",
            "reasoning_summary": "CV did not mention education level.",
        }])
        assert results[0].reasoning_summary == "CV did not mention education level."


# ── 6. supported_by_cv with answered question ─────────────────────────────────

class TestSupportedByCv:
    def test_stores_correctly_with_full_fields(self):
        results = _parse([{
            "question_id":       QID_ANSWERED,
            "validation_status": "supported_by_cv",
            "confidence":        0.95,
            "evidence_text":     "Found: Bachelor degree mentioned on page 1.",
        }], FINAL_ANSWERS)
        r = results[0]
        assert r.validation_status  == "supported_by_cv"
        assert r.has_final_answer   is True
        assert r.final_answer       == "Bachelor's Degree"
        assert r.confidence         == pytest.approx(0.95)
        assert r.evidence_text      == "Found: Bachelor degree mentioned on page 1."

    def test_evidence_truncated_at_300_chars(self):
        results = _parse([{
            "question_id":       QID_ANSWERED,
            "validation_status": "supported_by_cv",
            "evidence_text":     "x" * 500,
        }], FINAL_ANSWERS)
        assert len(results[0].evidence_text) == 300

    def test_reasoning_truncated_at_500_chars(self):
        results = _parse([{
            "question_id":       QID_ANSWERED,
            "validation_status": "supported_by_cv",
            "reasoning_summary": "r" * 600,
        }], FINAL_ANSWERS)
        assert len(results[0].reasoning_summary) == 500


# ── 7. Suggestion insert default confidence never null ────────────────────────

class TestSuggestionDefaultConfidence:
    def test_suggested_default_confidence_is_nonzero(self):
        assert _SUGGESTION_DEFAULT_CONFIDENCE["suggested"] > 0.0

    def test_all_valid_statuses_have_default(self):
        for status in _VALID_STATUSES:
            assert status in _SUGGESTION_DEFAULT_CONFIDENCE, (
                f"Status '{status}' missing from _SUGGESTION_DEFAULT_CONFIDENCE"
            )

    def test_all_defaults_are_floats(self):
        for status, val in _SUGGESTION_DEFAULT_CONFIDENCE.items():
            assert isinstance(val, float), f"Default for '{status}' is {type(val)}, expected float"

    def test_all_defaults_in_valid_range(self):
        for status, val in _SUGGESTION_DEFAULT_CONFIDENCE.items():
            assert 0.0 <= val <= 1.0, f"Default for '{status}' out of range: {val}"


# ── 8. Field compatibility — all three key variants ───────────────────────────

class TestFieldCompatibility:
    @pytest.mark.parametrize("field_name,status", [
        ("validation_status", "supported_by_cv"),
        ("validation_result", "supported_by_cv"),
        ("result",            "supported_by_cv"),
    ])
    def test_answered_question_all_field_names(self, field_name, status):
        results = _parse([{"question_id": QID_ANSWERED, field_name: status}], FINAL_ANSWERS)
        assert results[0].validation_status == status

    @pytest.mark.parametrize("field_name", ["validation_status", "validation_result", "result"])
    def test_unanswered_suggested_all_field_names(self, field_name):
        results = _parse([{"question_id": QID_UNANSWERED, field_name: "suggested",
                           "suggested_answer": "Master's"}])
        assert results[0].validation_status == "suggested"
        assert results[0].suggested_answer  == "Master's"


# ── 9. Edge cases ─────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_unknown_question_id_silently_skipped(self):
        results = _parse([{"question_id": "00000000-dead-dead-dead-000000000000",
                           "validation_status": "suggested"}])
        assert len(results) == 0

    def test_empty_results_returns_empty_list(self):
        assert _parse([]) == []

    def test_multiple_questions_parsed_independently(self):
        results = _parse([
            {"question_id": QID_UNANSWERED, "result": "suggested",
             "suggested_answer": "Master's Degree", "confidence": 0.8},
            {"question_id": QID_ANSWERED, "validation_status": "supported_by_cv",
             "confidence": 0.9, "evidence_text": "Evidence here."},
        ], FINAL_ANSWERS)
        assert len(results) == 2
        r_unanswered = next(r for r in results if r.question_id == QID_UNANSWERED)
        r_answered   = next(r for r in results if r.question_id == QID_ANSWERED)
        assert r_unanswered.validation_status == "suggested"
        assert r_unanswered.suggested_answer  == "Master's Degree"
        assert r_answered.validation_status   == "supported_by_cv"


# ── 10. Cross-field consistency corrections ───────────────────────────────────

class TestCrossFieldConsistency:
    def test_suggested_for_answered_becomes_cannot_validate(self):
        """AI incorrectly returns 'suggested' for a question that already has a final answer."""
        results = _parse([{"question_id": QID_ANSWERED, "validation_status": "suggested",
                           "suggested_answer": "something"}], FINAL_ANSWERS)
        r = results[0]
        assert r.validation_status == "cannot_validate"
        assert r.suggested_answer  is None

    def test_supported_for_unanswered_becomes_cannot_determine(self):
        """AI incorrectly returns 'supported_by_cv' for a question with no final answer."""
        results = _parse([{"question_id": QID_UNANSWERED, "validation_status": "supported_by_cv"}])
        assert results[0].validation_status == "cannot_determine"

    def test_cannot_determine_for_unanswered_stays_cannot_determine(self):
        results = _parse([{"question_id": QID_UNANSWERED, "validation_status": "cannot_determine"}])
        assert results[0].validation_status == "cannot_determine"

    def test_cannot_validate_for_answered_stays_cannot_validate(self):
        results = _parse([{"question_id": QID_ANSWERED, "validation_status": "cannot_validate"}],
                         FINAL_ANSWERS)
        assert results[0].validation_status == "cannot_validate"
