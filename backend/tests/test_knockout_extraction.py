"""
Tests for knockout_answer_extraction_service.

Validates all four extraction patterns and answer normalisation against
the canonical test cases specified in the feature requirements.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from services.knockout_answer_extraction_service import (
    _parse_body,
    _validate_answer,
    _normalize_yes_no,
    _normalize_number,
    _normalize_choice,
    _match_label,
    ExtractionResult,
)

# ── Canonical validation test body (spec §13) ────────────────────────────────

FORMSITE_PLAIN_BODY = """\
What is the highest level of education that you have?
X
Bachelor's Degree or equivalent

How many years of relevant professional experience do you have?
X
2 years

Do you have experience working with archives?
X
Yes

Are you willing and able to perform physical archive duties, including lifting boxes, shelving records, and standing for extended periods?
X
Yes
"""

FORMSITE_TABLE_BODY = """\
What is the highest level of education that you have?: Bachelor's Degree or equivalent
How many years of relevant professional experience do you have?: 2 years
Do you have experience working with archives?: Yes
Are you willing and able to perform physical archive duties, including lifting boxes, shelving records, and standing for extended periods?: Yes
"""

INLINE_CHECKBOX_BODY = """\
What is the highest level of education that you have?
[x] Bachelor's Degree or equivalent
[ ] Master's Degree or equivalent
[ ] PhD

Do you have experience working with archives?
[x] Yes
[ ] No
"""


# ── Mock knockout questions matching the test body ───────────────────────────

MOCK_QUESTIONS = [
    {
        "question_id":   "aaaaaaaa-0001-0000-0000-000000000001",
        "question_text": "What is the highest level of education that you have?",
        "question_type": "single_choice",
        "options":       ["Bachelor's Degree or equivalent", "Master's Degree or equivalent", "PhD or equivalent", "High School Diploma"],
        "is_required":   True,
    },
    {
        "question_id":   "aaaaaaaa-0001-0000-0000-000000000002",
        "question_text": "How many years of relevant professional experience do you have?",
        "question_type": "number",
        "options":       None,
        "is_required":   True,
    },
    {
        "question_id":   "aaaaaaaa-0001-0000-0000-000000000003",
        "question_text": "Do you have experience working with archives?",
        "question_type": "yes_no",
        "options":       None,
        "is_required":   True,
    },
    {
        "question_id":   "aaaaaaaa-0001-0000-0000-000000000004",
        "question_text": "Are you willing and able to perform physical archive duties, including lifting boxes, shelving records, and standing for extended periods?",
        "question_type": "yes_no",
        "options":       None,
        "is_required":   True,
    },
]


# ── Helper: run full extraction pipeline ─────────────────────────────────────

def _run_extraction(body: str, questions: list[dict]) -> list[ExtractionResult]:
    """Run parse → match → validate pipeline synchronously for tests."""
    raw_pairs = _parse_body(body)
    matched: dict[str, ExtractionResult] = {}

    for pair in raw_pairs:
        question, label_score = _match_label(pair.label, questions)
        if question is None:
            continue
        qid    = str(question["question_id"])
        q_type = question["question_type"]
        opts   = question.get("options") or []

        normalised, type_bonus = _validate_answer(pair.value, q_type, opts)
        if normalised is None:
            continue

        base       = label_score / 100.0
        structured = 0.03 if pair.pattern == "label_colon_value" else 0.0
        marked     = 0.02 if pair.marked else 0.0
        confidence = min(1.0, base + structured + marked + type_bonus)

        existing = matched.get(qid)
        if existing is None or confidence > existing.confidence:
            matched[qid] = ExtractionResult(
                question_id        = qid,
                question_text      = question["question_text"],
                question_type      = q_type,
                options            = opts or None,
                suggested_answer   = normalised,
                confidence         = confidence,
                evidence_text      = (pair.evidence or pair.label)[:300],
                label_match_score  = label_score,
                extraction_pattern = pair.pattern,
                resolved           = confidence >= 0.80,
            )
    return list(matched.values())


# ── Pattern B tests: Question + X + Answer ───────────────────────────────────

class TestPatternB:
    """Formsite plain-text 'Question\nX\nAnswer' format."""

    def test_all_four_questions_extracted(self):
        pairs = _parse_body(FORMSITE_PLAIN_BODY)
        labels_found = {p.label.lower() for p in pairs}
        assert any("education" in l for l in labels_found), "Education question not extracted"
        assert any("experience" in l and "years" in l for l in labels_found), "Experience question not extracted"
        assert any("archive" in l for l in labels_found), "Archive experience question not extracted"
        assert any("physical" in l for l in labels_found), "Physical duties question not extracted"

    def test_all_pairs_have_marked_true(self):
        pairs = [p for p in _parse_body(FORMSITE_PLAIN_BODY) if p.pattern == "question_x_answer"]
        assert len(pairs) == 4, f"Expected 4 question_x_answer pairs, got {len(pairs)}"
        assert all(p.marked for p in pairs), "All Q+X+A pairs should have marked=True"

    def test_education_value_extracted(self):
        pairs = _parse_body(FORMSITE_PLAIN_BODY)
        edu = next((p for p in pairs if "education" in p.label.lower()), None)
        assert edu is not None
        assert "bachelor" in edu.value.lower()

    def test_experience_value_extracted(self):
        pairs = _parse_body(FORMSITE_PLAIN_BODY)
        exp = next((p for p in pairs if "years" in p.label.lower() and "experience" in p.label.lower()), None)
        assert exp is not None
        assert "2" in exp.value

    def test_archive_value_extracted(self):
        pairs = _parse_body(FORMSITE_PLAIN_BODY)
        arch = next((p for p in pairs if "archive" in p.label.lower() and "physical" not in p.label.lower()), None)
        assert arch is not None
        assert arch.value.strip().lower() == "yes"

    def test_physical_value_extracted(self):
        pairs = _parse_body(FORMSITE_PLAIN_BODY)
        phys = next((p for p in pairs if "physical" in p.label.lower()), None)
        assert phys is not None
        assert phys.value.strip().lower() == "yes"


# ── Pattern A tests: "Label: Value" rows ─────────────────────────────────────

class TestPatternA:
    def test_all_four_extracted(self):
        pairs = _parse_body(FORMSITE_TABLE_BODY)
        labels = {p.label.lower() for p in pairs}
        assert any("education" in l for l in labels)
        assert any("years" in l for l in labels)
        assert any("archive" in l for l in labels)
        assert any("physical" in l for l in labels)

    def test_pattern_label(self):
        pairs = _parse_body(FORMSITE_TABLE_BODY)
        assert all(p.pattern == "label_colon_value" for p in pairs)

    def test_structured_bonus_present(self):
        """label_colon_value gets +0.03 structured bonus."""
        results = _run_extraction(FORMSITE_TABLE_BODY, MOCK_QUESTIONS)
        for r in results:
            if r.resolved:
                # confidence should include structured bonus (≥ 0.90 range)
                assert r.confidence >= 0.88, f"Expected high confidence for structured source, got {r.confidence:.2f}"


# ── Pattern C tests: Inline checkbox ─────────────────────────────────────────

class TestPatternC:
    def test_selected_checkbox_extracted(self):
        pairs = [p for p in _parse_body(INLINE_CHECKBOX_BODY) if p.pattern == "inline_checkbox"]
        assert len(pairs) >= 2, f"Expected ≥2 inline_checkbox pairs, got {len(pairs)}"

    def test_education_checkbox(self):
        pairs = _parse_body(INLINE_CHECKBOX_BODY)
        edu = next((p for p in pairs if "education" in p.label.lower()), None)
        assert edu is not None
        assert "bachelor" in edu.value.lower()

    def test_archive_checkbox(self):
        pairs = _parse_body(INLINE_CHECKBOX_BODY)
        arch = next((p for p in pairs if "archive" in p.label.lower()), None)
        assert arch is not None
        assert arch.value.strip().lower() == "yes"


# ── Full pipeline tests ───────────────────────────────────────────────────────

class TestFullPipeline:
    """End-to-end extraction → fuzzy match → validate for the canonical test case."""

    def test_all_four_resolved_plain_body(self):
        results = _run_extraction(FORMSITE_PLAIN_BODY, MOCK_QUESTIONS)
        resolved = [r for r in results if r.resolved]
        assert len(resolved) == 4, (
            f"Expected all 4 questions resolved, got {len(resolved)}.\n"
            + "\n".join(f"  {r.question_id[:8]} resolved={r.resolved} conf={r.confidence:.2f} "
                        f"answer={r.suggested_answer!r}" for r in results)
        )

    def test_education_answer_and_provenance(self):
        results = _run_extraction(FORMSITE_PLAIN_BODY, MOCK_QUESTIONS)
        r = next(r for r in results if "education" in r.question_text.lower())
        assert r.resolved
        assert r.suggested_answer is not None
        assert "bachelor" in r.suggested_answer.lower()
        assert r.extraction_pattern == "question_x_answer"

    def test_experience_answer_is_number(self):
        results = _run_extraction(FORMSITE_PLAIN_BODY, MOCK_QUESTIONS)
        r = next(r for r in results if "years" in r.question_text.lower())
        assert r.resolved
        assert r.suggested_answer == "2"   # normalised from "2 years"
        assert r.question_type == "number"

    def test_archive_answer_is_yes(self):
        results = _run_extraction(FORMSITE_PLAIN_BODY, MOCK_QUESTIONS)
        r = next(r for r in results if "archive" in r.question_text.lower()
                 and "physical" not in r.question_text.lower())
        assert r.resolved
        assert r.suggested_answer == "yes"

    def test_physical_answer_is_yes(self):
        results = _run_extraction(FORMSITE_PLAIN_BODY, MOCK_QUESTIONS)
        r = next(r for r in results if "physical" in r.question_text.lower())
        assert r.resolved
        assert r.suggested_answer == "yes"

    def test_confidence_high(self):
        """All results should have confidence ≥ 0.90 for exact-match question text."""
        results = _run_extraction(FORMSITE_PLAIN_BODY, MOCK_QUESTIONS)
        for r in results:
            if r.resolved:
                assert r.confidence >= 0.90, (
                    f"Low confidence for {r.question_text[:40]!r}: {r.confidence:.2f}"
                )

    def test_all_four_resolved_table_body(self):
        results = _run_extraction(FORMSITE_TABLE_BODY, MOCK_QUESTIONS)
        resolved = [r for r in results if r.resolved]
        assert len(resolved) == 4, f"Expected 4 resolved from table body, got {len(resolved)}"

    def test_all_four_resolved_checkbox_body(self):
        results = _run_extraction(INLINE_CHECKBOX_BODY, MOCK_QUESTIONS)
        resolved = [r for r in results if r.resolved]
        assert len(resolved) >= 2, f"Expected ≥2 resolved from checkbox body, got {len(resolved)}"


# ── Normalisation unit tests ──────────────────────────────────────────────────

class TestNormalisation:
    def test_yes_variants(self):
        for v in ["Yes", "YES", "y", "Y", "true", "1", "✓", "checked", "نعم"]:
            assert _normalize_yes_no(v) == "yes", f"Expected 'yes' for {v!r}"

    def test_no_variants(self):
        for v in ["No", "NO", "n", "N", "false", "0", "لا"]:
            assert _normalize_yes_no(v) == "no", f"Expected 'no' for {v!r}"

    def test_number_with_units(self):
        assert _normalize_number("2 years") == "2"
        assert _normalize_number("5.5 years") == "5.5"
        assert _normalize_number("about 3") == "3"

    def test_choice_exact_match(self):
        opts = ["Bachelor's Degree or equivalent", "Master's Degree or equivalent"]
        assert _normalize_choice("Bachelor's Degree or equivalent", opts) == opts[0]

    def test_choice_fuzzy_match(self):
        opts = ["Bachelor's Degree or equivalent", "Master's Degree or equivalent"]
        assert _normalize_choice("bachelors degree", opts) == opts[0]

    def test_yes_no_normalisation_in_validate(self):
        norm, bonus = _validate_answer("Yes", "yes_no", None)
        assert norm == "yes"
        assert bonus == 0.02

    def test_number_normalisation_in_validate(self):
        norm, bonus = _validate_answer("2 years", "number", None)
        assert norm == "2"
        assert bonus == 0.02


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
