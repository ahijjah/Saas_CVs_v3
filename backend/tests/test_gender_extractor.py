"""Tests for gender metadata extraction from CV text — v1.1.

Coverage:
  - Explicit labeled field: Gender/Sex/الجنس with all separator formats
  - Standalone value on own line: Male / Female / ذكر / أنثى
  - Formal titles: Mr. / Mrs. / Ms. / السيد / السيدة
  - Stated pronouns: she/her, he/him
  - Unknown / no signal → unknown, 0.0
  - Arabic text and mixed bilingual text
  - Scoring isolation: gender never reaches score_cv / scoring prompts
  - API filter value invariants
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


# ── Explicit labeled field — English ─────────────────────────────────────────

class TestExplicitLabeledEnglish:
    """Gender: / Sex: patterns with all separator variants. confidence = 1.0."""

    def test_gender_colon_male(self):
        r = infer_gender("Gender: Male")
        assert r.value == GENDER_MALE
        assert r.basis == BASIS_EXPLICIT
        assert r.confidence == 1.0

    def test_gender_colon_female(self):
        r = infer_gender("Gender: Female")
        assert r.value == GENDER_FEMALE
        assert r.basis == BASIS_EXPLICIT
        assert r.confidence == 1.0

    def test_sex_colon_male(self):
        r = infer_gender("Sex: Male")
        assert r.value == GENDER_MALE
        assert r.basis == BASIS_EXPLICIT

    def test_sex_colon_female(self):
        r = infer_gender("Sex: Female")
        assert r.value == GENDER_FEMALE
        assert r.basis == BASIS_EXPLICIT

    def test_gender_space_colon_male(self):
        r = infer_gender("Gender : Male")
        assert r.value == GENDER_MALE
        assert r.basis == BASIS_EXPLICIT

    def test_gender_space_colon_female(self):
        r = infer_gender("Gender : Female")
        assert r.value == GENDER_FEMALE
        assert r.basis == BASIS_EXPLICIT

    def test_gender_dash_male(self):
        r = infer_gender("Gender- Male")
        assert r.value == GENDER_MALE

    def test_gender_dash_female(self):
        r = infer_gender("Gender - Female")
        assert r.value == GENDER_FEMALE

    def test_sex_space_colon_female(self):
        r = infer_gender("Sex : Female")
        assert r.value == GENDER_FEMALE

    def test_gender_no_separator_male(self):
        r = infer_gender("Gender Male")
        assert r.value == GENDER_MALE

    def test_gender_no_separator_female(self):
        r = infer_gender("Gender Female")
        assert r.value == GENDER_FEMALE

    def test_case_insensitive_upper(self):
        r = infer_gender("GENDER: MALE")
        assert r.value == GENDER_MALE

    def test_case_insensitive_mixed(self):
        r = infer_gender("gender: female")
        assert r.value == GENDER_FEMALE

    def test_embedded_in_cv_body_at_end(self):
        cv = "\n".join([
            "John Smith",
            "Python Developer | 8 years experience",
            "Skills: Python, JavaScript, AWS, Docker",
            "Education: BSc Computer Science",
            "",
            "Personal Information",
            "Date of Birth: 15-03-1990",
            "Nationality: British",
            "Gender: Male",
        ])
        r = infer_gender(cv)
        assert r.value == GENDER_MALE
        assert r.basis == BASIS_EXPLICIT

    def test_embedded_in_cv_body_female_at_end(self):
        long_cv = "Skills: Python\n" * 200 + "\nGender: Female\n"
        r = infer_gender(long_cv)
        assert r.value == GENDER_FEMALE

    def test_gender_field_after_2000_chars(self):
        padding = "x" * 3000
        cv = f"{padding}\nGender: Male\n"
        r = infer_gender(cv)
        assert r.value == GENDER_MALE

    def test_confidence_is_1_0_for_labeled(self):
        r = infer_gender("Sex: Female")
        assert r.confidence == 1.0


# ── Explicit labeled field — Arabic ──────────────────────────────────────────

class TestExplicitLabeledArabic:
    """الجنس: patterns with all separator variants."""

    def test_al_jins_colon_dhakar(self):
        r = infer_gender("الجنس: ذكر")
        assert r.value == GENDER_MALE
        assert r.basis == BASIS_EXPLICIT
        assert r.confidence == 1.0

    def test_al_jins_colon_ontha(self):
        r = infer_gender("الجنس: أنثى")
        assert r.value == GENDER_FEMALE
        assert r.basis == BASIS_EXPLICIT
        assert r.confidence == 1.0

    def test_al_jins_space_colon_dhakar(self):
        r = infer_gender("الجنس : ذكر")
        assert r.value == GENDER_MALE

    def test_al_jins_space_colon_ontha(self):
        r = infer_gender("الجنس : أنثى")
        assert r.value == GENDER_FEMALE

    def test_al_jins_space_dhakar(self):
        r = infer_gender("الجنس ذكر")
        assert r.value == GENDER_MALE

    def test_al_jins_space_ontha(self):
        r = infer_gender("الجنس أنثى")
        assert r.value == GENDER_FEMALE

    def test_arabic_cv_with_gender_in_personal_section(self):
        cv = "\n".join([
            "محمد أحمد",
            "مهندس برمجيات | خبرة 8 سنوات",
            "المهارات: بايثون، جافاسكريبت",
            "التعليم: بكالوريوس هندسة حاسوب",
            "",
            "المعلومات الشخصية",
            "تاريخ الميلاد: 15-03-1990",
            "الجنسية: سعودي",
            "الجنس: ذكر",
        ])
        r = infer_gender(cv)
        assert r.value == GENDER_MALE

    def test_arabic_female_cv(self):
        cv = "نورة العتيبي\nمحاسبة مالية\nالجنس: أنثى\n"
        r = infer_gender(cv)
        assert r.value == GENDER_FEMALE


# ── Standalone value on own line ──────────────────────────────────────────────

class TestStandaloneOnOwnLine:
    """Single word on its own line — confidence 1.0."""

    def test_standalone_male_en(self):
        r = infer_gender("John Smith\nMale\nSoftware Engineer")
        assert r.value == GENDER_MALE
        assert r.confidence == 1.0
        assert r.basis == BASIS_EXPLICIT

    def test_standalone_female_en(self):
        r = infer_gender("Jane Smith\nFemale\nHR Manager")
        assert r.value == GENDER_FEMALE
        assert r.confidence == 1.0

    def test_standalone_male_ar(self):
        r = infer_gender("محمد علي\nذكر\nمهندس برمجيات")
        assert r.value == GENDER_MALE

    def test_standalone_female_ar(self):
        r = infer_gender("فاطمة محمد\nأنثى\nمحاسبة")
        assert r.value == GENDER_FEMALE

    def test_standalone_with_surrounding_whitespace(self):
        r = infer_gender("Name: Alex\n  Male  \nAge: 28")
        assert r.value == GENDER_MALE

    def test_male_in_middle_of_text_not_matched(self):
        # "male" as part of a sentence should not match standalone pattern
        r = infer_gender("Seeking a male candidate for this role")
        # This sentence has 'male' but NOT on its own line
        # standalone pattern uses ^...$, so it won't match; but labeled also won't
        # so should be unknown
        assert r.value == GENDER_UNKNOWN


# ── Title detection ───────────────────────────────────────────────────────────

class TestTitleDetection:
    def test_mr_dot_male(self):
        r = infer_gender("Mr. John Smith\nSoftware Engineer")
        assert r.value == GENDER_MALE
        assert r.basis == BASIS_TITLE
        assert r.confidence == 0.90

    def test_mr_no_dot_male(self):
        r = infer_gender("Mr John Smith\nDubai, UAE")
        assert r.value == GENDER_MALE

    def test_mister_male(self):
        r = infer_gender("Mister Ahmed Al-Hassan")
        assert r.value == GENDER_MALE

    def test_mrs_dot_female(self):
        r = infer_gender("Mrs. Sarah Johnson\nHR Manager")
        assert r.value == GENDER_FEMALE
        assert r.basis == BASIS_TITLE

    def test_ms_dot_female(self):
        r = infer_gender("Ms. Fatima Al-Ali\nMarketer")
        assert r.value == GENDER_FEMALE

    def test_miss_female(self):
        r = infer_gender("Miss Layla Omar\nData Analyst")
        assert r.value == GENDER_FEMALE

    def test_arabic_al_sayid_male(self):
        r = infer_gender("السيد محمد علي\nمهندس برمجيات")
        assert r.value == GENDER_MALE
        assert r.basis == BASIS_TITLE

    def test_arabic_al_sayida_female(self):
        r = infer_gender("السيدة فاطمة محمد\nمديرة مشاريع")
        assert r.value == GENDER_FEMALE
        assert r.basis == BASIS_TITLE

    def test_arabic_al_ansa_female(self):
        r = infer_gender("الآنسة سارة الأحمد\nمحاسبة")
        assert r.value == GENDER_FEMALE

    def test_mrs_not_mr(self):
        # Ensure "Mrs." does not trigger male pattern
        r = infer_gender("Mrs. Jane Doe\nDirector")
        assert r.value == GENDER_FEMALE


# ── Pronoun detection ─────────────────────────────────────────────────────────

class TestPronounDetection:
    def test_she_her_female(self):
        r = infer_gender("Pronouns: she/her\nSoftware Engineer")
        assert r.value == GENDER_FEMALE
        assert r.basis == BASIS_PRONOUN

    def test_he_him_male(self):
        r = infer_gender("Pronouns: he/him\nProduct Manager")
        assert r.value == GENDER_MALE
        assert r.basis == BASIS_PRONOUN

    def test_pronoun_label_she(self):
        r = infer_gender("Pronoun: she\nData Scientist")
        assert r.value == GENDER_FEMALE

    def test_pronoun_label_he(self):
        r = infer_gender("Pronoun: he\nDevOps Engineer")
        assert r.value == GENDER_MALE

    def test_pronoun_bracket_she(self):
        r = infer_gender("Contact: john@example.com (she/her)")
        assert r.value == GENDER_FEMALE


# ── Unknown / no signal ───────────────────────────────────────────────────────

class TestUnknownNoCases:
    def test_no_signal_returns_unknown(self):
        r = infer_gender("John Smith\nSoftware Engineer\nPython, JavaScript\n5 years")
        assert r.value == GENDER_UNKNOWN
        assert r.confidence == 0.0
        assert r.basis == BASIS_UNKNOWN

    def test_empty_string_unknown(self):
        assert infer_gender("").value == GENDER_UNKNOWN

    def test_whitespace_only_unknown(self):
        assert infer_gender("   \n\t  ").value == GENDER_UNKNOWN

    def test_gender_neutral_title_dr_unknown(self):
        r = infer_gender("Dr. Jordan Smith\nProfessor")
        assert r.value == GENDER_UNKNOWN

    def test_mx_title_unknown(self):
        r = infer_gender("Mx. Alex Taylor\nConsultant")
        assert r.value == GENDER_UNKNOWN

    def test_name_alone_unknown(self):
        r = infer_gender("Mohammed Al-Hassan\nEngineer at Acme Corp")
        assert r.value == GENDER_UNKNOWN

    def test_technical_cv_no_signals(self):
        cv = """
        Alex Johnson
        Senior Software Engineer
        Python | React | AWS | Docker
        5 years of experience in cloud infrastructure
        """
        assert infer_gender(cv).value == GENDER_UNKNOWN

    def test_word_male_inside_sentence_unknown(self):
        r = infer_gender("We are looking for a motivated male or female professional")
        assert r.value == GENDER_UNKNOWN

    def test_word_female_inside_sentence_unknown(self):
        r = infer_gender("This female-friendly workplace welcomes all applicants")
        assert r.value == GENDER_UNKNOWN


# ── Negative tests: no false positives ───────────────────────────────────────

class TestNoFalsePositives:
    def test_dhakar_as_adjective_not_matched(self):
        # ذكري = masculine adjective — must NOT match
        r = infer_gender("تفكير ذكري")
        assert r.value == GENDER_UNKNOWN

    def test_excellence_word_not_male(self):
        # "excellent" contains "male" pattern? No — but let's verify
        r = infer_gender("Excellent communication skills")
        assert r.value == GENDER_UNKNOWN

    def test_female_candidates_sentence(self):
        r = infer_gender("Female candidates are encouraged to apply")
        # Not on its own line, no labeled field → unknown
        assert r.value == GENDER_UNKNOWN

    def test_mrs_triggers_female_not_male(self):
        r = infer_gender("Mrs. Johnson is the hiring manager")
        assert r.value == GENDER_FEMALE
        assert r.value != GENDER_MALE


# ── Arabic + English mixed text ───────────────────────────────────────────────

class TestBilingualSupport:
    def test_arabic_cv_with_english_title(self):
        r = infer_gender("Mr. محمد Ali\nمهندس برمجيات")
        assert r.value == GENDER_MALE

    def test_arabic_only_title(self):
        r = infer_gender("السيدة نورة العتيبي\nمحاسبة مالية")
        assert r.value == GENDER_FEMALE

    def test_mixed_with_no_signal(self):
        r = infer_gender("أحمد محمد\nمدير مشاريع\nدبي، الإمارات")
        assert r.value == GENDER_UNKNOWN

    def test_arabic_gender_field_in_mixed_cv(self):
        cv = "Ahmed Al-Hassan\nSoftware Engineer\n\nالجنس: ذكر\n"
        r = infer_gender(cv)
        assert r.value == GENDER_MALE
        assert r.basis == BASIS_EXPLICIT


# ── Field invariants ──────────────────────────────────────────────────────────

class TestFieldInvariants:
    def test_used_for_scoring_always_false(self):
        for cv in [
            "Mr. John Smith",
            "Mrs. Jane Doe",
            "Gender: Female",
            "Sex: Male",
            "الجنس: ذكر",
            "John Smith Engineer",
        ]:
            r = infer_gender(cv)
            assert r.used_for_scoring is False, f"must be False for {cv!r}"

    def test_source_always_cv_text(self):
        for cv in ["Mr. John", "", "Gender: Male", "she/her", "الجنس: أنثى"]:
            assert infer_gender(cv).source == SOURCE_CV_TEXT

    def test_confidence_in_valid_range(self):
        for cv in ["Mr. John Smith", "", "Gender: Female", "she/her"]:
            r = infer_gender(cv)
            assert 0.0 <= r.confidence <= 1.0

    def test_value_is_valid_enum(self):
        for cv in ["Mr. John", "Mrs. Jane", "Alex Developer"]:
            assert infer_gender(cv).value in (GENDER_MALE, GENDER_FEMALE, GENDER_UNKNOWN)

    def test_explicit_confidence_is_exactly_1_0(self):
        for cv in ["Gender: Male", "Gender: Female", "Sex: Male", "الجنس: ذكر"]:
            r = infer_gender(cv)
            assert r.confidence == 1.0, f"explicit confidence must be 1.0 for {cv!r}"

    def test_infer_gender_never_raises(self):
        for s in ["\x00\x01\x02", "a" * 10000, "السيد " * 100, "Gender: " * 50]:
            try:
                r = infer_gender(s)
                assert r.value in (GENDER_MALE, GENDER_FEMALE, GENDER_UNKNOWN)
            except Exception as exc:
                pytest.fail(f"infer_gender raised {exc!r} for {s[:30]!r}")


# ── Scoring isolation ─────────────────────────────────────────────────────────

class TestScoringIsolation:
    def test_gender_result_has_no_score_fields(self):
        r = infer_gender("Mr. John Smith\nPython Developer")
        d = dataclasses.asdict(r)
        assert not any(k in d for k in {"final_score", "score_skills", "decision"})

    def test_gender_result_has_no_criteria_fields(self):
        r = infer_gender("Gender: Female")
        d = dataclasses.asdict(r)
        assert not any(k in d for k in {"skills", "experience", "education"})

    def test_used_for_scoring_false_for_all_outcomes(self):
        cvs = [
            "Gender: Male",
            "Gender: Female",
            "Mr. John",
            "Ms. Jane",
            "she/her",
            "No signal at all",
        ]
        for cv in cvs:
            assert infer_gender(cv).used_for_scoring is False

    def test_different_genders_same_cv_skills(self):
        base = "Senior Python Developer with 10 years experience\nSkills: Python, AWS"
        r_male    = infer_gender(f"Gender: Male\n{base}")
        r_female  = infer_gender(f"Gender: Female\n{base}")
        r_unknown = infer_gender(f"Alex Smith\n{base}")
        assert r_male.value   == GENDER_MALE
        assert r_female.value == GENDER_FEMALE
        assert r_unknown.value == GENDER_UNKNOWN
        assert all(r.used_for_scoring is False for r in [r_male, r_female, r_unknown])


# ── API filter value invariants ───────────────────────────────────────────────

class TestApiFilterValues:
    def test_output_is_always_valid_filter_value(self):
        valid = {"male", "female", "unknown"}
        test_cvs = [
            "Gender: Male", "Gender: Female", "الجنس: ذكر", "الجنس: أنثى",
            "Mr. John", "Ms. Jane", "she/her", "he/him",
            "John Smith", "", "   ",
        ]
        for cv in test_cvs:
            r = infer_gender(cv)
            assert r.value in valid, f"Unexpected value {r.value!r} for {cv!r}"
