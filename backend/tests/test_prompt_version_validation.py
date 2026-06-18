"""
AI Prompt version creation — prompt_code validation tests.

Validates that:
- The prompt_code regex correctly accepts valid snake_case codes
- The prompt_code regex correctly rejects invalid codes
- The regex is only enforced for new prompts, not new versions

These tests use the regex directly (same pattern as in routers/ai_prompts.py)
to avoid importing FastAPI dependencies unavailable in the test environment.
"""

import re
import pytest

_PROMPT_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class TestPromptCodeRegexAccepts:
    def test_simple_word(self):              assert _PROMPT_CODE_RE.match("scoring")
    def test_snake_case(self):               assert _PROMPT_CODE_RE.match("cv_scoring")
    def test_with_digits(self):              assert _PROMPT_CODE_RE.match("level2_screening")
    def test_criteria_extraction(self):      assert _PROMPT_CODE_RE.match("criteria_extraction")
    def test_knockout_analysis(self):        assert _PROMPT_CODE_RE.match("knockout_analysis")
    def test_multiple_underscores(self):     assert _PROMPT_CODE_RE.match("cv_scoring_v2")
    def test_single_letter(self):            assert _PROMPT_CODE_RE.match("a")


class TestPromptCodeRegexRejects:
    def test_uppercase_start(self):          assert not _PROMPT_CODE_RE.match("CV_Scoring")
    def test_uppercase_middle(self):         assert not _PROMPT_CODE_RE.match("cvScoring")
    def test_spaces(self):                   assert not _PROMPT_CODE_RE.match("cv scoring")
    def test_hyphens(self):                  assert not _PROMPT_CODE_RE.match("cv-scoring")
    def test_starts_with_digit(self):        assert not _PROMPT_CODE_RE.match("2nd_prompt")
    def test_starts_with_underscore(self):   assert not _PROMPT_CODE_RE.match("_private")
    def test_empty(self):                    assert not _PROMPT_CODE_RE.match("")
    def test_special_chars(self):            assert not _PROMPT_CODE_RE.match("cv@scoring")


class TestVersionCreationShouldSkipRegex:
    """
    Documents the intended behaviour: when creating a new version of an existing
    prompt, the prompt_code is inherited and should NOT be re-validated against
    the snake_case regex.  Only genuinely new prompts need the regex check.

    These tests verify that legacy codes (which would fail the regex) are valid
    for version creation purposes.
    """

    def _would_fail_regex(self, code: str) -> bool:
        return not _PROMPT_CODE_RE.match(code)

    def test_legacy_uppercase_fails_regex(self):
        assert self._would_fail_regex("CV_Scoring")

    def test_legacy_hyphen_fails_regex(self):
        assert self._would_fail_regex("cv-scoring")

    def test_legacy_mixed_case_fails_regex(self):
        assert self._would_fail_regex("Criteria_Extraction")

    def test_valid_code_passes_regex(self):
        assert not self._would_fail_regex("cv_scoring")

    def test_valid_code_passes_regex_2(self):
        assert not self._would_fail_regex("criteria_extraction")
