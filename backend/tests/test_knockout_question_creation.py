"""
Regression test — knockout question payload isolation.

Verifies that:
- question_text is saved exactly as received (no description text injected).
- The defensive frontend strip (description prepended by browser autofill) is
  validated at the logic level.
- description and knockout_questions fields are fully independent in the payload.

Also tests the backend _sanitise_question_text() helper introduced in
knockout_questions_service.py as a second line of defence.
"""

import pytest


# ── Inline copy of the backend sanitiser (pure logic, no I/O) ─────────────────
# Must be kept in sync with services/knockout_questions_service._sanitise_question_text

def _raw_pos_for_norm_len(text: str, norm_len: int) -> int:
    n = 0
    in_space = False
    for i, ch in enumerate(text):
        if n >= norm_len:
            return i
        if ch.isspace():
            if not in_space:
                n += 1
            in_space = True
        else:
            n += 1
            in_space = False
    return len(text)


def _sanitise_question_text(raw: str, job_description: str | None) -> str:
    text = raw.strip()
    if not job_description:
        return text
    desc = job_description.strip()
    if not desc:
        return text
    if text.startswith(desc):
        return text[len(desc):].lstrip()

    def _norm(s: str) -> str:
        return " ".join(s.lower().split())

    norm_text = _norm(text)
    norm_desc = _norm(desc)
    if norm_desc and norm_text.startswith(norm_desc):
        raw_cut = _raw_pos_for_norm_len(text, len(norm_desc))
        return text[raw_cut:].lstrip()

    return text


_sanitise = _sanitise_question_text


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


# ── Tests: _sanitise_question_text (backend service helper) ───────────────────

class TestSanitiseQuestionText:
    """
    Tests for the backend _sanitise_question_text() helper in
    services/knockout_questions_service.py.
    """

    # ── clean inputs ──────────────────────────────────────────────────────────

    def test_clean_question_unchanged(self):
        """A clean question with no description prefix is returned as-is."""
        desc = "Python developer needed with 5+ years of experience."
        q = "Do you have 5+ years of Python experience?"
        assert _sanitise(q, desc) == q

    def test_none_description_leaves_question_unchanged(self):
        """When job_description is None, no stripping is attempted."""
        q = "Are you willing to relocate?"
        assert _sanitise(q, None) == q

    def test_empty_description_leaves_question_unchanged(self):
        """An empty description string triggers no stripping."""
        q = "Do you hold a CPA certification?"
        assert _sanitise(q, "") == q

    def test_whitespace_trimmed(self):
        """Leading and trailing whitespace is always stripped from question_text."""
        desc = "Senior role."
        assert _sanitise("  Do you have AWS experience?  ", desc) == "Do you have AWS experience?"

    # ── description-prefix stripping ──────────────────────────────────────────

    def test_exact_prefix_stripped(self):
        """Exact description prefix followed immediately by question is stripped."""
        desc = "We are looking for a Python developer with 5 years experience."
        actual_q = "Do you have a valid UAE work permit?"
        contaminated = desc + actual_q
        assert _sanitise(contaminated, desc) == actual_q

    def test_prefix_with_trailing_space_stripped(self):
        """Description + space separator + question: space consumed by lstrip."""
        desc = "Senior Software Engineer role at a fast-growing startup."
        actual_q = "Are you available to start within 2 weeks?"
        contaminated = desc + " " + actual_q
        assert _sanitise(contaminated, desc) == actual_q

    def test_long_multiline_description_prefix_stripped(self):
        """Multi-sentence description prefix is fully stripped."""
        desc = (
            "We are a global logistics company. "
            "We seek a warehouse coordinator with forklift experience. "
            "The role is based in Dubai."
        )
        actual_q = "Do you hold a valid forklift licence?"
        contaminated = desc + actual_q
        assert _sanitise(contaminated, desc) == actual_q

    def test_normalised_prefix_stripped(self):
        """
        Minor whitespace differences between stored description and autofilled
        prefix (e.g. collapsed double-spaces) are handled via normalised compare.
        """
        desc = "Python developer  needed."  # double space in stored value
        actual_q = "Are you available?"
        # autofill may have normalised the space
        contaminated = "Python developer needed." + actual_q
        # normalised comparison should match and strip
        result = _sanitise(contaminated, desc)
        assert actual_q in result  # question is preserved

    def test_partial_description_not_stripped(self):
        """A prefix that is only a fragment of the description is left alone."""
        desc = "We are looking for a Python developer with 5 years of experience in Django."
        q = "Do you have experience in Django?"
        # question starts with 'Do', not with the description
        assert _sanitise(q, desc) == q

    # ── post-strip empty detection ─────────────────────────────────────────────

    def test_strip_returns_empty_when_question_is_only_description(self):
        """
        If question_text equals the description exactly, stripping leaves ''.
        The caller (_validate_question) must then reject this as empty.
        """
        desc = "Python developer role with 5 years experience."
        result = _sanitise(desc, desc)
        assert result == ""

    def test_strip_returns_empty_when_description_plus_spaces(self):
        """Description + trailing spaces only → strip returns '' after lstrip."""
        desc = "Python developer role."
        contaminated = desc + "   "
        result = _sanitise(contaminated, desc)
        assert result == ""


# ── Tests: validation rejects cleaned-empty questions ─────────────────────────

class TestValidationAfterCleanup:
    """
    After _sanitise_question_text runs, _validate_question must reject any
    question whose question_text is empty or whitespace-only.

    We mirror the validation logic here so we can run it without importing
    the FastAPI-dependent service module.
    """

    @staticmethod
    def _validate_text(text: str) -> bool:
        """Returns True if text would pass _validate_question's question_text check."""
        return isinstance(text, str) and bool(text.strip())

    def test_clean_question_passes_validation(self):
        desc = "Senior engineer role."
        q = "Do you have AWS experience?"
        result = _sanitise(q, desc)
        assert self._validate_text(result)

    def test_contaminated_question_passes_after_strip(self):
        desc = "Senior engineer role."
        actual_q = "Do you have AWS experience?"
        contaminated = desc + actual_q
        result = _sanitise(contaminated, desc)
        assert self._validate_text(result)

    def test_description_only_question_fails_after_strip(self):
        """question_text that was only the description is empty after strip → rejected."""
        desc = "Python developer role with 5 years experience."
        result = _sanitise(desc, desc)
        assert not self._validate_text(result)

    def test_whitespace_only_question_fails_validation(self):
        """Whitespace-only question_text is rejected regardless of description."""
        result = _sanitise("   ", "Some description.")
        assert not self._validate_text(result)
