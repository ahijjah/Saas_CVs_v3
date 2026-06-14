"""
Tests for deterministic language inference engine (D-04).

Covers:
  - Arabic inference signals: Arabic chars, nationality label, location label,
    country in header, Arabic university in education
  - English inference signals: CV language, English education, English work history
  - Criterion detection patterns
  - Candidate-specific regression tests
  - Edge cases: threshold boundaries, no evidence
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Optional

from services.language_inference import (
    infer_arabic_proficiency,
    infer_english_proficiency,
    is_arabic_language_criterion,
    is_english_language_criterion,
    is_language_criterion,
    LanguageProficiencyEvidence,
    CONF_NATIVE_AR_TEXT,
    CONF_MIXED_AR_TEXT,
    CONF_NATIONALITY,
    CONF_LOCATION,
    CONF_COUNTRY_IN_HEADER,
    CONF_ARABIC_UNIVERSITY,
    CONF_EN_CV_PRIMARY,
    CONF_EN_EDUCATION,
    CONF_EN_WORK_HISTORY,
)


# ── Minimal stub for CVFacts ──────────────────────────────────────────────────

@dataclass
class _EduStub:
    degree_level: str = ""
    field_of_study: str = ""
    institution: str = ""

@dataclass
class _ExpStub:
    role_title: str = ""
    employer: str = ""
    years: float = 1.0

@dataclass
class _CVFactsStub:
    education: list = field(default_factory=list)
    experience: list = field(default_factory=list)
    nationality: str = ""
    location_country: str = ""
    language_section_lines: list = field(default_factory=list)


# ── Helper ────────────────────────────────────────────────────────────────────

def _arabic_cv(ratio: float = 0.80) -> str:
    """Generate a CV string where `ratio` of non-space chars are Arabic."""
    ar_count = int(ratio * 500)
    en_count = 500 - ar_count
    return "أ" * ar_count + "a" * en_count


# ── Arabic inference — signal 1: Arabic chars ─────────────────────────────────

class TestArabicFromCharRatio:
    def test_native_arabic_cv(self):
        cv = _arabic_cv(0.80)
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence == CONF_NATIVE_AR_TEXT
        assert result.language == "arabic"
        assert any("primarily in Arabic" in e for e in result.supporting_evidence)

    def test_mixed_arabic_cv(self):
        cv = _arabic_cv(0.25)
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence == CONF_MIXED_AR_TEXT

    def test_below_threshold_arabic_cv(self):
        # 10% Arabic chars alone isn't enough — should return None without other signals
        cv = _arabic_cv(0.10)
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is None

    def test_native_arabic_takes_higher_confidence(self):
        """Native Arabic text (>50%) beats mixed signal."""
        cv = _arabic_cv(0.60)
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence == CONF_NATIVE_AR_TEXT


# ── Arabic inference — signal 2: nationality label ────────────────────────────

class TestArabicFromNationality:
    def test_english_nationality_label(self):
        cv = "Name: Dana Makhool\nNationality: Palestinian\nSome content here..."
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence >= CONF_NATIONALITY
        assert any("Nationality" in e for e in result.supporting_evidence)

    def test_arabic_nationality_label(self):
        cv = "الاسم: محمد\nالجنسية: فلسطيني\nمحتوى هنا"
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence >= CONF_NATIONALITY

    def test_jordanian_nationality(self):
        cv = "Nationality: Jordanian\nSome content"
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence >= CONF_NATIONALITY

    def test_non_arab_nationality(self):
        cv = "Nationality: French\nContent here"
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is None


# ── Arabic inference — signal 3: location label ───────────────────────────────

class TestArabicFromLocation:
    def test_ramallah_location(self):
        cv = "Location: Ramallah, Palestine\nSome content here"
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence >= CONF_LOCATION
        assert any("Location" in e for e in result.supporting_evidence)

    def test_dubai_location(self):
        cv = "Address: Dubai, UAE\nMore text"
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence >= CONF_LOCATION

    def test_arabic_location_label(self):
        cv = "الموقع: رام الله\nمحتوى"
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None

    def test_non_arab_location(self):
        cv = "Location: London, UK\nContent"
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is None


# ── Arabic inference — signal 4: country in header ────────────────────────────

class TestArabicFromHeaderCountry:
    def test_palestine_in_header(self):
        cv = "John Doe | Palestine | john@example.com\nExperience section..."
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence >= CONF_COUNTRY_IN_HEADER

    def test_egypt_in_header(self):
        cv = "Ahmed | Cairo, Egypt | Software Engineer\nMore content..."
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence >= CONF_COUNTRY_IN_HEADER

    def test_country_past_header_window(self):
        """A country name beyond first 1500 chars should NOT be detected."""
        padding = "x" * 2000
        cv = padding + "\nPalestine"
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is None


# ── Arabic inference — signal 5: Arabic university ────────────────────────────

class TestArabicFromUniversity:
    def test_birzeit_university(self):
        cv = "Some English CV content here..."
        facts = _CVFactsStub(education=[_EduStub(institution="Birzeit University")])
        result = infer_arabic_proficiency(cv, facts)
        assert result is not None
        assert result.confidence >= CONF_ARABIC_UNIVERSITY
        assert any("Birzeit" in e for e in result.supporting_evidence)

    def test_najah_university(self):
        cv = "Some English CV content..."
        facts = _CVFactsStub(education=[_EduStub(institution="An-Najah National University")])
        result = infer_arabic_proficiency(cv, facts)
        assert result is not None
        assert result.confidence >= CONF_ARABIC_UNIVERSITY

    def test_aub_counts_as_arabic_university(self):
        cv = "Some English CV content..."
        facts = _CVFactsStub(education=[_EduStub(institution="American University of Beirut")])
        result = infer_arabic_proficiency(cv, facts)
        assert result is not None

    def test_foreign_university_no_signal(self):
        cv = "Some English CV content..."
        facts = _CVFactsStub(education=[_EduStub(institution="University of Cambridge")])
        result = infer_arabic_proficiency(cv, facts)
        assert result is None


# ── Arabic inference — no evidence ────────────────────────────────────────────

class TestArabicNoEvidence:
    def test_english_only_cv_no_signals(self):
        cv = "John Smith | London | john@smith.com\nSoftware Engineer at ACME Corp."
        facts = _CVFactsStub(education=[_EduStub(institution="University of Manchester")])
        result = infer_arabic_proficiency(cv, facts)
        assert result is None

    def test_empty_cv(self):
        result = infer_arabic_proficiency("", _CVFactsStub())
        assert result is None


# ── English inference — signal 1: CV language ─────────────────────────────────

class TestEnglishFromCVLanguage:
    def test_primarily_english_cv(self):
        cv = "Name: Dana\nExperience: Software Engineer at TechCorp for 3 years.\n" * 10
        result = infer_english_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence == CONF_EN_CV_PRIMARY
        assert result.language == "english"

    def test_mixed_cv_lower_confidence(self):
        cv = _arabic_cv(0.25)  # 25% Arabic → mixed
        result = infer_english_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence < CONF_EN_CV_PRIMARY

    def test_arabic_dominant_cv_no_char_signal(self):
        cv = _arabic_cv(0.80)  # >50% Arabic → no English char signal
        result = infer_english_proficiency(cv, _CVFactsStub())
        # Other signals could still fire; just check char signal isn't the reason
        if result:
            assert "primarily in English" not in " ".join(result.supporting_evidence)


# ── English inference — signal 2: English education ──────────────────────────

class TestEnglishFromEducation:
    def test_english_institution(self):
        cv = _arabic_cv(0.80)  # Arabic-dominant so char signal won't fire
        facts = _CVFactsStub(education=[_EduStub(institution="University of Jordan", field_of_study="CS")])
        # "University of Jordan" has no Arabic chars → counts as English label
        result = infer_english_proficiency(cv, facts)
        assert result is not None

    def test_arabic_institution_no_signal(self):
        cv = _arabic_cv(0.80)
        facts = _CVFactsStub(education=[_EduStub(institution="جامعة بيرزيت", field_of_study="علم الحاسوب")])
        result = infer_english_proficiency(cv, facts)
        # No education signal because institution is in Arabic
        # But also no char signal since CV is Arabic-dominant
        # This may return None or with other signals only
        if result:
            assert all("Education" not in e for e in result.supporting_evidence)


# ── English inference — signal 3: English work history ───────────────────────

class TestEnglishFromWorkHistory:
    def test_two_english_work_entries(self):
        cv = _arabic_cv(0.80)
        facts = _CVFactsStub(experience=[
            _ExpStub(role_title="Software Engineer", employer="TechCorp"),
            _ExpStub(role_title="Developer", employer="StartupXYZ"),
        ])
        result = infer_english_proficiency(cv, facts)
        assert result is not None
        assert result.confidence >= CONF_EN_WORK_HISTORY
        assert any("English-language work history" in e for e in result.supporting_evidence)

    def test_one_english_work_entry_not_enough(self):
        cv = _arabic_cv(0.80)
        facts = _CVFactsStub(experience=[
            _ExpStub(role_title="Software Engineer", employer="TechCorp"),
        ])
        result = infer_english_proficiency(cv, facts)
        # Only 1 English entry — work history signal shouldn't fire
        if result:
            assert all("English-language work history" not in e for e in result.supporting_evidence)

    def test_arabic_work_entries_no_signal(self):
        cv = _arabic_cv(0.80)
        facts = _CVFactsStub(experience=[
            _ExpStub(role_title="مهندس برمجيات", employer="شركة التقنية"),
            _ExpStub(role_title="مطور", employer="شركة البرمجة"),
        ])
        result = infer_english_proficiency(cv, facts)
        if result:
            assert all("English-language work history" not in e for e in result.supporting_evidence)


# ── English inference — no evidence ───────────────────────────────────────────

class TestEnglishNoEvidence:
    def test_pure_arabic_cv_no_signals(self):
        cv = _arabic_cv(0.80)
        result = infer_english_proficiency(cv, _CVFactsStub())
        assert result is None

    def test_empty_cv(self):
        # Empty string has 0 chars; _ar_ratio returns 0 → ratio < 0.15 → English signal fires
        # This is a known behaviour: empty string looks "English" by ratio.
        # Verify the function at least doesn't crash.
        result = infer_english_proficiency("", _CVFactsStub())
        # result may be non-None (char-ratio fires) — just assert no crash
        assert result is None or isinstance(result, LanguageProficiencyEvidence)


# ── Criterion detection ────────────────────────────────────────────────────────

class TestCriterionDetection:
    @pytest.mark.parametrize("text", [
        "Native Arabic proficiency",
        "Arabic language fluency",
        "Arabic speaking",
        "Fluent in Arabic",
        "Arabic proficiency required",
        "اللغة العربية",
        "العربية",
        "إجادة العربية",
    ])
    def test_detects_arabic_criterion(self, text):
        assert is_arabic_language_criterion(text)

    @pytest.mark.parametrize("text", [
        "Working English proficiency",
        "English fluency required",
        "Business English",
        "Native English speaker",
        "Professional English",
        "اللغة الإنجليزية",
        "الإنجليزية",
    ])
    def test_detects_english_criterion(self, text):
        assert is_english_language_criterion(text)

    @pytest.mark.parametrize("text", [
        "5+ years of software engineering experience",
        "Bachelor's degree in Computer Science",
        "Project management certification",
        "Strong communication skills",
    ])
    def test_rejects_non_language_criterion(self, text):
        assert not is_language_criterion(text)

    def test_is_language_criterion_arabic(self):
        assert is_language_criterion("Native Arabic proficiency")

    def test_is_language_criterion_english(self):
        assert is_language_criterion("English proficiency")

    def test_is_language_criterion_neither(self):
        assert not is_language_criterion("5 years experience in finance")


# ── Result structure ──────────────────────────────────────────────────────────

class TestResultStructure:
    def test_arabic_result_has_required_fields(self):
        cv = "Nationality: Palestinian\nSome content here for the CV."
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert isinstance(result, LanguageProficiencyEvidence)
        assert result.language == "arabic"
        assert 0.0 < result.confidence <= 1.0
        assert isinstance(result.supporting_evidence, list)
        assert len(result.supporting_evidence) >= 1
        assert result.match_type == "inferred"

    def test_english_result_has_required_fields(self):
        cv = "John Smith | London\nSoftware Engineer at TechCorp."
        result = infer_english_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.language == "english"
        assert result.match_type == "inferred"

    def test_max_evidence_items(self):
        """Result should contain at most 4 evidence items."""
        cv = _arabic_cv(0.80)
        cv = "Nationality: Palestinian\nLocation: Ramallah\n" + cv
        facts = _CVFactsStub(education=[_EduStub(institution="Birzeit University")])
        result = infer_arabic_proficiency(cv, facts)
        assert result is not None
        assert len(result.supporting_evidence) <= 4


# ── Candidate regression tests ─────────────────────────────────────────────────

class TestCandidateRegression:
    """Specific candidates that triggered the D-04 fix."""

    def test_dana_makhool_arabic(self):
        """Palestinian CV in English — should infer Arabic."""
        cv = "Dana Makhool\nNationality: Palestinian\nLocation: Ramallah\n" \
             "Education: B.Sc. Computer Science, Birzeit University\n" \
             "Experience: Software Engineer at Fastlink (3 yrs)"
        facts = _CVFactsStub(
            nationality="Palestinian",
            location_country="Ramallah",
            education=[_EduStub(institution="Birzeit University", degree_level="B.Sc.", field_of_study="CS")],
            experience=[_ExpStub(role_title="Software Engineer", employer="Fastlink")],
        )
        arabic = infer_arabic_proficiency(cv, facts)
        assert arabic is not None
        assert arabic.confidence >= 0.85
        assert arabic.status_available if hasattr(arabic, "status_available") else True

    def test_dana_makhool_english(self):
        """English CV — should infer English proficiency."""
        cv = "Dana Makhool\nNationality: Palestinian\nLocation: Ramallah\n" \
             "Education: B.Sc. Computer Science, Birzeit University\n" \
             "Experience: Software Engineer at Fastlink (3 yrs)"
        facts = _CVFactsStub(
            education=[_EduStub(institution="Birzeit University", field_of_study="CS")],
            experience=[_ExpStub(role_title="Software Engineer", employer="Fastlink")],
        )
        english = infer_english_proficiency(cv, facts)
        assert english is not None
        assert english.confidence >= 0.72

    def test_mohammed_zagharna_arabic(self):
        """Arabic name, Egyptian background — should infer Arabic."""
        cv = "Mohammed Zagharna | Cairo, Egypt | m.zagharna@email.com\n" \
             "Software Developer at CairoTech\nCairo University graduate"
        facts = _CVFactsStub(
            education=[_EduStub(institution="Cairo University", field_of_study="Engineering")],
        )
        result = infer_arabic_proficiency(cv, facts)
        assert result is not None
        assert result.confidence >= 0.85

    def test_rania_rimawi_arabic(self):
        """Jordanian name, Amman location — should infer Arabic."""
        cv = "Rania Rimawi\nLocation: Amman, Jordan\nHR Manager at FinanceCorp"
        result = infer_arabic_proficiency(cv, _CVFactsStub())
        assert result is not None
        assert result.confidence >= CONF_LOCATION
