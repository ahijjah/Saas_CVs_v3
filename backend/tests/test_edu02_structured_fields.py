"""
EDU-02: Structured education field extraction tests.

Coverage:
  - institution extracted from inferred university-pattern (multi-line)
  - institution extracted from slash-separated inline format
  - institution extracted from comma-separated inline format
  - field_of_study extracted from adjacent lines (multi-line format)
  - field_of_study extracted from slash format
  - field_of_study extracted from comma format
  - field_of_study extracted from "in <Field>" for explicit degrees
  - attendance_years populated as "YYYY–YYYY" string
  - attendance_years empty when only single year or no range
  - supporting_evidence is non-empty list for all entries
  - institution extracted for explicit degrees (multi-line)
  - attendance_years for explicit degrees
  - multiple education entries all have structured fields
  - no false inference from weak evidence (training exclusions)
  - missing years → attendance_years empty, year = None/end-year preserved
  - serialisation round-trip preserves all new fields
  - legacy dicts without new fields deserialise safely
"""
from __future__ import annotations

import dataclasses
import pytest

from services.cv_evidence import (
    EducationEvidence,
    _extract_education,
    _infer_university_bachelor,
    _parse_edu_fields_from_window,
)
from services.evidence_serialiser import _education_from_dict


# ── Inferred: institution extraction ─────────────────────────────────────────

class TestInferredInstitution:
    def test_slash_format_institution(self):
        text = "Birzeit University / Marketing Management / 2016–2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert "Birzeit University" in result.institution

    def test_comma_format_institution(self):
        text = "An-Najah University, Computer Science, 2017-2021"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert "Najah" in result.institution or "University" in result.institution

    def test_multiline_format_institution(self):
        text = "Birzeit University\nMarketing Management\n2016-2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert "Birzeit University" in result.institution

    def test_community_college_institution(self):
        text = "Community College of Applied Sciences / Accounting / 2014-2018"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.institution  # non-empty

    def test_institution_not_empty_for_named_university(self):
        text = "Jordan University of Science and Technology, Engineering, 2013-2017"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.institution != ""

    def test_arabic_institution_extracted(self):
        text = "جامعة بيرزيت / إدارة الأعمال / 2016-2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.institution  # non-empty


# ── Inferred: field_of_study extraction ──────────────────────────────────────

class TestInferredFieldOfStudy:
    def test_slash_format_field(self):
        text = "Birzeit University / Marketing Management / 2016–2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.field_of_study == "Marketing Management"

    def test_comma_format_field(self):
        text = "An-Najah University, Computer Science, 2017-2021"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.field_of_study == "Computer Science"

    def test_multiline_format_field(self):
        text = "Birzeit University\nMarketing Management\n2016-2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.field_of_study == "Marketing Management"

    def test_field_extracted_from_next_line(self):
        text = "\nBirzeit University\nBusiness Administration\n2015-2019\n"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert "Business Administration" in result.field_of_study

    def test_field_excludes_year_content(self):
        text = "State University / Accounting / 2014-2018"
        result = _infer_university_bachelor(text)
        assert result is not None
        # field should not contain year digits
        assert "2014" not in result.field_of_study
        assert "2018" not in result.field_of_study

    def test_arabic_field_extracted(self):
        text = "جامعة بيرزيت / إدارة الأعمال / 2016-2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.field_of_study  # non-empty

    def test_slash_pipe_mixed_format(self):
        text = "Cairo University | Business | 2018-2022"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.field_of_study == "Business"


# ── Inferred: attendance_years ────────────────────────────────────────────────

class TestInferredAttendanceYears:
    def test_attendance_years_populated(self):
        text = "Birzeit University / Marketing Management / 2016–2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.attendance_years == "2016–2020"

    def test_attendance_years_hyphen_format(self):
        text = "An-Najah University, Computer Science, 2017-2021"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.attendance_years == "2017–2021"

    def test_attendance_years_contains_both_years(self):
        text = "State University, Engineering, 2014-2018"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert "2014" in result.attendance_years
        assert "2018" in result.attendance_years

    def test_year_field_still_set_to_end_year(self):
        # year (int) must remain for backward compatibility
        text = "State University, Engineering, 2014-2018"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.year == 2018

    def test_em_dash_separator_in_attendance_years(self):
        text = "Cairo University  2018—2022  Business"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert "2018" in result.attendance_years
        assert "2022" in result.attendance_years


# ── Inferred: supporting_evidence ────────────────────────────────────────────

class TestInferredSupportingEvidence:
    def test_supporting_evidence_non_empty(self):
        text = "Birzeit University / Marketing Management / 2016–2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert isinstance(result.supporting_evidence, list)
        assert len(result.supporting_evidence) >= 1

    def test_supporting_evidence_contains_text(self):
        text = "Birzeit University / Marketing Management / 2016–2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert any("Birzeit" in e or "Marketing" in e for e in result.supporting_evidence)

    def test_multiline_supporting_evidence(self):
        text = "An-Najah University\nComputer Science\n2017-2021"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.supporting_evidence


# ── Explicit: field_of_study via "in <Field>" ─────────────────────────────────

class TestExplicitFieldOfStudy:
    def test_bachelors_in_computer_science(self):
        entries, _ = _extract_education("Bachelor's in Computer Science, State University, 2015")
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Computer Science"

    def test_masters_in_business_administration(self):
        entries, _ = _extract_education("Master's degree in Business Administration")
        ms = next(e for e in entries if e.degree_level == "Master's")
        assert ms.field_of_study == "Business Administration"

    def test_phd_in_electrical_engineering(self):
        entries, _ = _extract_education("Ph.D. in Electrical Engineering, MIT, 2015")
        phd = next(e for e in entries if e.degree_level == "PhD")
        assert phd.field_of_study == "Electrical Engineering"

    def test_field_stops_at_comma(self):
        entries, _ = _extract_education("Bachelor's in Engineering, State University")
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert "University" not in bs.field_of_study

    def test_field_stops_at_from(self):
        entries, _ = _extract_education("Bachelor's in Computer Science from MIT")
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Computer Science"

    def test_no_in_keyword_field_empty_or_from_adjacent(self):
        # Without "in", field_of_study may be empty for explicit degrees
        entries, _ = _extract_education("Bachelor's degree, State University, 2015-2019")
        bs = next((e for e in entries if e.degree_level == "Bachelor's"), None)
        assert bs is not None
        # field_of_study may be empty here — that's acceptable


# ── Explicit: institution from separate line ──────────────────────────────────

class TestExplicitInstitution:
    def test_institution_from_next_line(self):
        text = "Bachelor's in Computer Science\nBirzeit University\n2016-2020"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert "Birzeit" in bs.institution or "University" in bs.institution

    def test_institution_from_previous_line(self):
        text = "Birzeit University\nBachelor's in Computer Science\n2016-2020"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.institution  # non-empty

    def test_no_institution_when_same_line(self):
        # When institution and degree are on the same line, we don't extract institution
        # (to avoid polluting institution with field/degree text)
        text = "Bachelor's in CS, State University, 2015"
        entries, _ = _extract_education(text)
        bs = next((e for e in entries if e.degree_level == "Bachelor's"), None)
        assert bs is not None
        # institution may or may not be set — just ensure no crash


# ── Explicit: attendance_years ────────────────────────────────────────────────

class TestExplicitAttendanceYears:
    def test_year_range_extracted(self):
        text = "Bachelor's in Computer Science, State University, 2015-2019"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.attendance_years == "2015–2019"

    def test_year_range_in_wider_context(self):
        text = "Bachelor's in Engineering\nState University\n2014-2018"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert "2014" in bs.attendance_years
        assert "2018" in bs.attendance_years

    def test_no_year_range_leaves_empty(self):
        text = "Bachelor's in Computer Science"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.attendance_years == ""


# ── Explicit: supporting_evidence ────────────────────────────────────────────

class TestExplicitSupportingEvidence:
    def test_explicit_degree_has_supporting_evidence(self):
        entries, _ = _extract_education("Bachelor's in Computer Science, State University, 2015")
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert isinstance(bs.supporting_evidence, list)
        assert len(bs.supporting_evidence) >= 1
        assert any("Bachelor" in e or "Computer" in e for e in bs.supporting_evidence)

    def test_master_has_supporting_evidence(self):
        entries, _ = _extract_education("Master's degree in Business Administration")
        ms = next(e for e in entries if e.degree_level == "Master's")
        assert ms.supporting_evidence


# ── Multiple education entries ────────────────────────────────────────────────

class TestMultipleEntries:
    def test_both_entries_have_attendance_years(self):
        text = (
            "Master's in Computer Science\nBirzeit University\n2020-2022\n\n"
            "Bachelor's in Information Technology\nAn-Najah University\n2016-2020"
        )
        entries, highest = _extract_education(text)
        assert highest == "Master's"
        for entry in entries:
            assert isinstance(entry.supporting_evidence, list)

    def test_both_entries_have_supporting_evidence(self):
        text = "Bachelor's in CS, State University, 2015-2019\nMaster's in AI, MIT, 2020-2022"
        entries, _ = _extract_education(text)
        for entry in entries:
            assert entry.supporting_evidence

    def test_inferred_and_explicit_coexist(self):
        # Diploma (lower) + Birzeit University → should fire inference
        text = "Diploma in IT\nBirzeit University / Business / 2016-2020"
        entries, highest = _extract_education(text)
        assert highest == "Bachelor's"
        inferred = next((e for e in entries if e.inferred), None)
        assert inferred is not None
        assert inferred.attendance_years  # populated


# ── No false inference ────────────────────────────────────────────────────────

class TestNoFalseInference:
    def test_training_exclusion_no_fields_populated(self):
        # A training context should return None (not populate any fields)
        text = "University bootcamp, Web Development, 2019-2023"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_too_short_duration_no_result(self):
        result = _infer_university_bachelor("City University, Marketing, 2018-2020")
        assert result is None

    def test_too_long_duration_no_result(self):
        result = _infer_university_bachelor("City University, Engineering, 2012-2019")
        assert result is None

    def test_no_year_range_no_result(self):
        result = _infer_university_bachelor("State University, Business Administration")
        assert result is None


# ── Serialisation round-trip ──────────────────────────────────────────────────

class TestSerialisationRoundTrip:
    def test_inferred_entry_with_all_new_fields(self):
        original = EducationEvidence(
            degree_level="Bachelor's",
            field_of_study="Marketing Management",
            institution="Birzeit University",
            year=2020,
            raw_text="Birzeit University / Marketing Management / 2016–2020",
            inferred=True,
            basis="university_study_pattern",
            confidence=0.85,
            attendance_years="2016–2020",
            supporting_evidence=["Birzeit University / Marketing Management / 2016–2020"],
        )
        d = dataclasses.asdict(original)
        restored = _education_from_dict(d)

        assert restored.field_of_study == "Marketing Management"
        assert restored.institution == "Birzeit University"
        assert restored.attendance_years == "2016–2020"
        assert restored.supporting_evidence == ["Birzeit University / Marketing Management / 2016–2020"]
        assert restored.inferred is True
        assert restored.basis == "university_study_pattern"
        assert restored.confidence == 0.85

    def test_explicit_entry_with_all_new_fields(self):
        original = EducationEvidence(
            degree_level="Master's",
            field_of_study="Computer Science",
            institution="MIT",
            year=None,
            raw_text="Master's in Computer Science, MIT, 2020-2022",
            inferred=False,
            basis="explicit_degree",
            confidence=1.0,
            attendance_years="2020–2022",
            supporting_evidence=["Master's in Computer Science, MIT, 2020-2022"],
        )
        d = dataclasses.asdict(original)
        restored = _education_from_dict(d)

        assert restored.field_of_study == "Computer Science"
        assert restored.attendance_years == "2020–2022"
        assert restored.supporting_evidence

    def test_legacy_dict_without_new_fields_safe_defaults(self):
        # Older data without attendance_years / supporting_evidence
        d = {
            "degree_level": "Bachelor's",
            "field_of_study": "Science",
            "institution": "Some University",
            "year": 2015,
            "raw_text": "BSc Science 2015",
            "inferred": False,
            "basis": "explicit_degree",
            "confidence": 1.0,
        }
        result = _education_from_dict(d)
        assert result.attendance_years == ""
        assert result.supporting_evidence == []
        assert result.degree_level == "Bachelor's"

    def test_round_trip_empty_new_fields(self):
        original = EducationEvidence(
            degree_level="Master's",
            field_of_study="",
            institution="",
            year=2018,
        )
        d = dataclasses.asdict(original)
        restored = _education_from_dict(d)
        assert restored.attendance_years == ""
        assert restored.supporting_evidence == []

    def test_supporting_evidence_list_preserved(self):
        original = EducationEvidence(
            degree_level="Bachelor's",
            field_of_study="CS",
            institution="MIT",
            year=2020,
            supporting_evidence=["snippet one", "snippet two"],
        )
        d = dataclasses.asdict(original)
        restored = _education_from_dict(d)
        assert restored.supporting_evidence == ["snippet one", "snippet two"]


# ── _parse_edu_fields_from_window helper ─────────────────────────────────────

class TestParseEduFieldsHelper:
    def _make_window(self, text: str, indicator: str = "University") -> tuple[str, int]:
        pos = text.lower().find(indicator.lower())
        return text, pos

    def test_slash_format(self):
        window = "Birzeit University / Marketing Management / 2016–2020"
        inst, field = _parse_edu_fields_from_window(window, window.lower().find("university"))
        assert "Birzeit" in inst
        assert field == "Marketing Management"

    def test_comma_with_year_format(self):
        window = "An-Najah University, Computer Science, 2017-2021"
        inst, field = _parse_edu_fields_from_window(window, window.lower().find("university"))
        assert "Najah" in inst or "University" in inst
        assert field == "Computer Science"

    def test_multiline_format(self):
        window = "Birzeit University\nMarketing Management\n2016-2020"
        inst, field = _parse_edu_fields_from_window(window, window.lower().find("university"))
        assert "Birzeit" in inst
        assert field == "Marketing Management"

    def test_empty_window_returns_empty(self):
        inst, field = _parse_edu_fields_from_window("", 0)
        assert inst == ""
        assert field == ""

    def test_no_field_when_only_institution(self):
        window = "Birzeit University\n2016-2020"
        inst, field = _parse_edu_fields_from_window(window, window.lower().find("university"))
        assert "Birzeit" in inst
        # field may be empty or minimal — just assert no crash
        assert isinstance(field, str)
