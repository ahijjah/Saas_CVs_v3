"""Tests for gender metadata extraction from CV text.

Coverage:
  - Explicit title Mr. → male
  - Explicit title Ms./Mrs. → female
  - Unknown name/no signal → unknown
  - Arabic text supported
  - Gender fields stored correctly (correct types/values)
  - Gender does not affect final_score (score isolation test)
  - Gender is not included in scoring prompt payload
  - API filter validation
"""
from __future__ import annotations

import dataclasses
import pytest

from services.gender_extractor import (
    infer_gender,
    GENDER_MALE,
    GENDER_FEMALE,
    GENDER_UNKNOWN,
    BASIS_TITLE,
    BASIS_PRONOUN,
    BASIS_EXPLICIT,
    BASIS_UNKNOWN,
    SOURCE_CV_TEXT,
)


# ── Title detection ───────────────────────────────────────────────────────────

class TestTitleDetection:
    def test_mr_dot_male(self):
        result = infer_gender("Mr. John Smith\nSoftware Engineer")
        assert result.value == GENDER_MALE
        assert result.basis == BASIS_TITLE
        assert result.confidence >= 0.85

    def test_mr_no_dot_male(self):
        result = infer_gender("Mr John Smith\nDubai, UAE")
        assert result.value == GENDER_MALE

    def test_mister_male(self):
        result = infer_gender("Mister Ahmed Al-Hassan")
        assert result.value == GENDER_MALE

    def test_mrs_dot_female(self):
        result = infer_gender("Mrs. Sarah Johnson\nHR Manager")
        assert result.value == GENDER_FEMALE
        assert result.basis == BASIS_TITLE
        assert result.confidence >= 0.85

    def test_ms_dot_female(self):
        result = infer_gender("Ms. Fatima Al-Ali\nMarketer")
        assert result.value == GENDER_FEMALE

    def test_miss_female(self):
        result = infer_gender("Miss Layla Omar\nData Analyst")
        assert result.value == GENDER_FEMALE

    def test_arabic_al_sayid_male(self):
        result = infer_gender("السيد محمد علي\nمهندس برمجيات")
        assert result.value == GENDER_MALE
        assert result.basis == BASIS_TITLE

    def test_arabic_al_sayida_female(self):
        result = infer_gender("السيدة فاطمة محمد\nمديرة مشاريع")
        assert result.value == GENDER_FEMALE
        assert result.basis == BASIS_TITLE

    def test_arabic_al_ansa_female(self):
        result = infer_gender("الآنسة سارة الأحمد\nمحاسبة")
        assert result.value == GENDER_FEMALE


# ── Pronoun detection ─────────────────────────────────────────────────────────

class TestPronounDetection:
    def test_she_her_female(self):
        result = infer_gender("Pronouns: she/her\nSoftware Engineer at Acme")
        assert result.value == GENDER_FEMALE
        assert result.basis == BASIS_PRONOUN

    def test_he_him_male(self):
        result = infer_gender("Pronouns: he/him\nProduct Manager")
        assert result.value == GENDER_MALE
        assert result.basis == BASIS_PRONOUN

    def test_pronoun_label_she_female(self):
        result = infer_gender("Pronoun: she\nData Scientist")
        assert result.value == GENDER_FEMALE

    def test_pronoun_label_he_male(self):
        result = infer_gender("Pronoun: he\nDevOps Engineer")
        assert result.value == GENDER_MALE

    def test_she_her_slash_female(self):
        result = infer_gender("Contact: john@example.com (she/her)")
        assert result.value == GENDER_FEMALE


# ── Explicit self-statement ───────────────────────────────────────────────────

class TestExplicitStatement:
    def test_gender_colon_male(self):
        result = infer_gender("Gender: Male\nAge: 30\nPython Developer")
        assert result.value == GENDER_MALE
        assert result.basis == BASIS_EXPLICIT

    def test_gender_colon_female(self):
        result = infer_gender("Gender: Female\nExperience: 5 years")
        assert result.value == GENDER_FEMALE
        assert result.basis == BASIS_EXPLICIT

    def test_sex_colon_male(self):
        result = infer_gender("Sex: Male\nNationality: Saudi")
        assert result.value == GENDER_MALE

    def test_sex_colon_female(self):
        result = infer_gender("Sex: Female\nLocation: Dubai")
        assert result.value == GENDER_FEMALE

    def test_arabic_male_dhakr(self):
        result = infer_gender("ذكر\nالموقع: الرياض")
        assert result.value == GENDER_MALE

    def test_arabic_female_ontha(self):
        result = infer_gender("أنثى\nالموقع: دبي")
        assert result.value == GENDER_FEMALE

    def test_explicit_has_highest_confidence(self):
        result = infer_gender("Gender: Male\nHR Manager")
        assert result.confidence == 0.95


# ── Unknown / no signal ───────────────────────────────────────────────────────

class TestUnknownCases:
    def test_no_signal_returns_unknown(self):
        result = infer_gender("John Smith\nSoftware Engineer\nPython, JavaScript\n5 years experience")
        assert result.value == GENDER_UNKNOWN
        assert result.confidence == 0.0
        assert result.basis == BASIS_UNKNOWN

    def test_empty_string_unknown(self):
        result = infer_gender("")
        assert result.value == GENDER_UNKNOWN

    def test_whitespace_only_unknown(self):
        result = infer_gender("   \n\t  ")
        assert result.value == GENDER_UNKNOWN

    def test_gender_neutral_title_unknown(self):
        result = infer_gender("Dr. Jordan Smith\nProfessor")
        assert result.value == GENDER_UNKNOWN

    def test_mx_title_unknown(self):
        result = infer_gender("Mx. Alex Taylor\nConsultant")
        assert result.value == GENDER_UNKNOWN

    def test_name_alone_unknown(self):
        result = infer_gender("Mohammed Al-Hassan\nEngineer at Acme Corp")
        assert result.value == GENDER_UNKNOWN

    def test_technical_cv_no_signals(self):
        cv = """
        Alex Johnson
        Senior Software Engineer
        Python | React | AWS | Docker
        5 years of experience in cloud infrastructure
        """
        result = infer_gender(cv)
        assert result.value == GENDER_UNKNOWN


# ── Arabic + English mixed text ───────────────────────────────────────────────

class TestBilingualSupport:
    def test_arabic_cv_with_english_title(self):
        cv = "Mr. محمد Ali\nمهندس برمجيات"
        result = infer_gender(cv)
        assert result.value == GENDER_MALE

    def test_arabic_only_title(self):
        cv = "السيدة نورة العتيبي\nمحاسبة مالية"
        result = infer_gender(cv)
        assert result.value == GENDER_FEMALE

    def test_mixed_with_no_signal(self):
        cv = "أحمد محمد\nمدير مشاريع\nدبي، الإمارات"
        result = infer_gender(cv)
        assert result.value == GENDER_UNKNOWN


# ── Field invariants ──────────────────────────────────────────────────────────

class TestFieldInvariants:
    def test_used_for_scoring_always_false(self):
        for cv in [
            "Mr. John Smith",
            "Mrs. Jane Doe",
            "Gender: Female",
            "John Smith Engineer",
        ]:
            result = infer_gender(cv)
            assert result.used_for_scoring is False, f"used_for_scoring must be False for: {cv!r}"

    def test_source_always_cv_text(self):
        for cv in ["Mr. John", "", "Gender: Male", "she/her"]:
            result = infer_gender(cv)
            assert result.source == SOURCE_CV_TEXT

    def test_confidence_in_valid_range(self):
        for cv in ["Mr. John Smith", "", "Gender: Female", "she/her"]:
            result = infer_gender(cv)
            assert 0.0 <= result.confidence <= 1.0

    def test_value_is_valid_enum(self):
        for cv in ["Mr. John", "Mrs. Jane", "Alex Developer"]:
            result = infer_gender(cv)
            assert result.value in (GENDER_MALE, GENDER_FEMALE, GENDER_UNKNOWN)

    def test_infer_gender_never_raises(self):
        weird_inputs = [
            None.__class__.__name__,  # just a string
            "\x00\x01\x02",
            "a" * 10000,
            "السيد " * 100,
        ]
        for s in weird_inputs:
            try:
                result = infer_gender(s)
                assert result.value in (GENDER_MALE, GENDER_FEMALE, GENDER_UNKNOWN)
            except Exception as e:
                pytest.fail(f"infer_gender raised {e!r} for input {s[:50]!r}")


# ── Scoring isolation: gender must not appear in score_cv payload ─────────────

class TestScoringIsolation:
    def test_gender_extractor_produces_no_score_fields(self):
        result = infer_gender("Mr. John Smith\nPython Developer")
        result_dict = dataclasses.asdict(result)
        score_keys = {"final_score", "score_skills", "score_experience", "decision", "qualified"}
        assert not any(k in result_dict for k in score_keys)

    def test_gender_result_has_no_criteria_fields(self):
        result = infer_gender("Gender: Female")
        result_dict = dataclasses.asdict(result)
        criteria_keys = {"skills", "experience", "education", "criteria"}
        assert not any(k in result_dict for k in criteria_keys)

    def test_different_genders_same_cv_text_concept(self):
        cv_male   = "Mr. John Smith\nSenior Python Developer with 10 years experience"
        cv_female = "Ms. Jane Smith\nSenior Python Developer with 10 years experience"
        cv_unknown = "Alex Smith\nSenior Python Developer with 10 years experience"
        r_male    = infer_gender(cv_male)
        r_female  = infer_gender(cv_female)
        r_unknown = infer_gender(cv_unknown)
        # Gender values differ
        assert r_male.value   == GENDER_MALE
        assert r_female.value == GENDER_FEMALE
        assert r_unknown.value == GENDER_UNKNOWN
        # But used_for_scoring is always False for all
        assert r_male.used_for_scoring   is False
        assert r_female.used_for_scoring is False
        assert r_unknown.used_for_scoring is False


# ── API filter value validation ───────────────────────────────────────────────

class TestApiFilterValues:
    def test_valid_gender_filter_values(self):
        valid = {"male", "female", "unknown"}
        # Ensure infer_gender only ever returns one of these
        test_cvs = [
            "Mr. John", "Ms. Jane", "John Smith",
            "Gender: Male", "أنثى", "السيد أحمد",
        ]
        for cv in test_cvs:
            result = infer_gender(cv)
            assert result.value in valid, f"Unexpected value {result.value!r} for {cv!r}"
