"""
Regression test — knockout question payload isolation.

Verifies that:
- question_text is saved exactly as received (no description text injected).
- The defensive frontend strip (description prepended by browser autofill) is
  validated at the logic level.
- description and knockout_questions fields are fully independent in the payload.
"""

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_payload(description: str, raw_question_texts: list[str]) -> dict:
    """
    Mirror the frontend handleSubmit question-building logic including the
    defensive strip added to fix the browser-autofill bug.

    Returns a dict with 'description' and optionally 'knockout_questions'.
    """
    questions = []
    for raw in raw_question_texts:
        text = raw.strip()
        # Defensive strip — mirrors the fix in AddJobModal.tsx handleSubmit
        if text.startswith(description):
            text = text[len(description):].lstrip()
        if text:
            questions.append({"question_text": text})

    payload: dict = {"description": description}
    if questions:
        payload["knockout_questions"] = questions
    return payload


# ── Tests: clean payloads ──────────────────────────────────────────────────────

class TestCleanPayload:
    def test_description_and_question_are_separate_fields(self):
        """description must not appear inside knockout_questions[0].question_text."""
        description = "We need an experienced accountant with CPA certification."
        question = "Do you hold a CPA certification?"

        payload = _build_payload(description, [question])

        assert payload["description"] == description
        assert payload["knockout_questions"][0]["question_text"] == question
        assert not payload["knockout_questions"][0]["question_text"].startswith(description)

    def test_clean_question_unchanged(self):
        """A normally entered question must pass through unchanged."""
        desc = "Python developer needed with 5 years experience."
        q = "Do you have 5+ years of Python experience?"
        payload = _build_payload(desc, [q])
        assert payload["knockout_questions"][0]["question_text"] == q

    def test_multiple_clean_questions(self):
        """Multiple clean questions must all be preserved correctly."""
        desc = "Senior engineer role."
        questions = [
            "Are you available immediately?",
            "Do you have AWS certification?",
            "Are you willing to relocate?",
        ]
        payload = _build_payload(desc, questions)
        kqs = payload["knockout_questions"]
        for i, q in enumerate(questions):
            assert kqs[i]["question_text"] == q

    def test_whitespace_trimmed_from_question(self):
        """Leading/trailing whitespace in question_text is stripped."""
        desc = "We are hiring."
        payload = _build_payload(desc, ["  Do you have experience?  "])
        assert payload["knockout_questions"][0]["question_text"] == "Do you have experience?"


# ── Tests: defensive strip (browser autofill regression) ─────────────────────

class TestDefensiveStripRegression:
    """
    Regression: when browser autofill puts the description before the question,
    the defensive strip in handleSubmit must remove it so only the actual
    question text is submitted to the backend.
    """

    def test_description_prefix_stripped(self):
        """Description prepended to first question is removed."""
        desc = "Python developer needed with 5 years experience."
        actual_q = "Do you have 5+ years of Python experience?"
        contaminated = desc + actual_q

        payload = _build_payload(desc, [contaminated])
        assert payload["knockout_questions"][0]["question_text"] == actual_q

    def test_description_prefix_with_space_gap_stripped(self):
        """Description + space + question: space is consumed by lstrip()."""
        desc = "Senior Software Engineer role at a fast-growing startup."
        actual_q = "Are you available to start within 2 weeks?"
        contaminated = desc + " " + actual_q

        payload = _build_payload(desc, [contaminated])
        assert payload["knockout_questions"][0]["question_text"] == actual_q

    def test_only_first_question_contaminated(self):
        """
        Browser autofill typically only contaminates the first input that
        appears in the DOM. Later questions must remain untouched.
        """
        desc = "Senior engineer role."
        q1_contaminated = desc + "Are you available immediately?"
        q2_clean = "Do you have AWS certification?"

        payload = _build_payload(desc, [q1_contaminated, q2_clean])
        kqs = payload["knockout_questions"]
        assert kqs[0]["question_text"] == "Are you available immediately?"
        assert kqs[1]["question_text"] == q2_clean

    def test_clean_question_not_affected_by_strip(self):
        """If description is NOT a prefix, the question is left unchanged."""
        description = "Senior Software Engineer role at a fast-growing startup."
        question = "Are you available to start within 2 weeks?"

        payload = _build_payload(description, [question])
        assert payload["knockout_questions"][0]["question_text"] == question

    def test_long_description_with_newlines(self):
        """A multi-line description prepended to a question is fully stripped."""
        desc = (
            "We are looking for a talented Python developer with 5+ years of experience. "
            "You will join our growing engineering team and work on exciting products."
        )
        actual_q = "Do you have a valid UAE work permit?"
        contaminated = desc + actual_q

        payload = _build_payload(desc, [contaminated])
        assert payload["knockout_questions"][0]["question_text"] == actual_q

    def test_no_description_leaks_into_any_question(self):
        """After building payload, no question_text must start with description."""
        desc = "We are a global logistics company seeking a warehouse coordinator."
        questions = [
            "Do you have a valid forklift licence?",
            "Are you willing to work night shifts?",
        ]
        payload = _build_payload(desc, questions)
        for kq in payload.get("knockout_questions", []):
            assert not kq["question_text"].startswith(desc), (
                f"Description leaked into question: {kq['question_text'][:80]}"
            )

    def test_empty_question_after_strip_excluded(self):
        """
        If stripping the description leaves nothing (question_text WAS the
        description), the question is excluded from the payload.
        """
        desc = "Python developer role."
        contaminated = desc  # no actual question text after the description

        payload = _build_payload(desc, [contaminated])
        # After stripping description, text is '' → filtered out
        assert "knockout_questions" not in payload or len(payload.get("knockout_questions", [])) == 0
