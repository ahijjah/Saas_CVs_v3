"""
EDU-02.1: Education extraction accuracy improvement tests.

Covers all fixes from EDU-02.1:
  - same-line degree + institution (colon-separated)
  - same-line degree + institution (dash-separated)
  - multi-line degree + institution
  - institution with city suffix stripped
  - field of study preserved in full ("Finance and Banking", not "Financial")
  - field NOT matched from experience section when wide_ctx spans it
  - graduation year only (no range): "Graduated 2024"
  - single year on its own line
  - year range still works
  - Arabic degree + field (no "in" keyword)
  - Arabic institution extracted
  - Arabic graduation year
  - multiple education entries
  - backward compatibility: existing EDU-02 tests still pass
"""
from __future__ import annotations

import pytest

from services.cv_evidence import (
    EducationEvidence,
    _extract_education,
    _infer_university_bachelor,
    _extract_institution_from_line,
    _strip_city_suffix,
)
import dataclasses
from services.evidence_serialiser import _education_from_dict


# ── Spec example (exact reproduction from EDU-02.1 ticket) ───────────────────

class TestSpecExample:
    """The exact before/after example from the EDU-02.1 specification."""

    TEXT = (
        "Bachelor's Degree in Finance and Banking:\n"
        "Birzeit University, Ramallah\n"
        "Graduated 2024"
    )

    def test_institution_correct(self):
        entries, _ = _extract_education(self.TEXT)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.institution == "Birzeit University"

    def test_field_of_study_correct(self):
        entries, _ = _extract_education(self.TEXT)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Finance and Banking"

    def test_attendance_years_correct(self):
        entries, _ = _extract_education(self.TEXT)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.attendance_years == "2024"

    def test_degree_level_correct(self):
        entries, highest = _extract_education(self.TEXT)
        assert highest == "Bachelor's"

    def test_explainability_fields_present(self):
        entries, _ = _extract_education(self.TEXT)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.basis == "explicit_degree"
        assert bs.confidence == 1.0
        assert bs.supporting_evidence


# ── Field of study: correct extraction, no pollution from experience section ──

class TestFieldOfStudy:
    def test_field_preserved_in_full_with_colon(self):
        """'Finance and Banking:' preserves the full field, not just 'Financial'."""
        text = "Bachelor's Degree in Finance and Banking:\nBirzeit University\nGraduated 2024"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Finance and Banking"

    def test_experience_section_not_polluting_field(self):
        """'in Financial Services' in experience must NOT become field_of_study."""
        text = (
            "Bachelor's Degree in Finance and Banking:\nBirzeit University\nGraduated 2024\n\n"
            "Work Experience:\n"
            "Financial Analyst in Financial Services sector\n"
            "Managed financial operations in a dynamic environment"
        )
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Finance and Banking"
        assert "Services" not in bs.field_of_study
        assert "Financial" not in bs.field_of_study

    def test_field_stops_at_colon(self):
        entries, _ = _extract_education("Bachelor's in Information Systems: State University")
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Information Systems"
        assert "State" not in bs.field_of_study

    def test_field_public_administration(self):
        entries, _ = _extract_education("Bachelor's in Public Administration, Al-Quds University, 2018-2022")
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Public Administration"

    def test_field_library_and_information_science(self):
        entries, _ = _extract_education("Master's in Library and Information Science\nBirzeit University")
        ms = next(e for e in entries if e.degree_level == "Master's")
        assert ms.field_of_study == "Library and Information Science"

    def test_field_business_administration(self):
        entries, _ = _extract_education("Bachelor's in Business Administration\nJordan University\n2015-2019")
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Business Administration"

    def test_field_computer_science(self):
        entries, _ = _extract_education("B.Sc. in Computer Science from Birzeit University")
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Computer Science"


# ── Institution: same-line extraction ─────────────────────────────────────────

class TestInstitutionSameLine:
    def test_colon_separated_institution(self):
        """'degree in Field: Institution' — institution after the colon."""
        text = "Bachelor's Degree in Finance and Banking: Birzeit University, Ramallah"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert "Birzeit University" in bs.institution

    def test_mba_dash_institution(self):
        """'MBA - Arab American University' — institution after the dash."""
        text = "MBA - Arab American University\n2019-2021"
        entries, _ = _extract_education(text)
        ms = next(e for e in entries if e.degree_level == "Master's")
        assert "Arab American University" in ms.institution

    def test_same_line_institution_does_not_include_degree_text(self):
        """Institution field must not contain the degree keyword itself."""
        text = "Bachelor's Degree in Finance and Banking: Birzeit University, Ramallah"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert "Bachelor" not in bs.institution
        assert "Finance" not in bs.institution

    def test_comma_after_institution_on_same_line_stripped(self):
        """'Birzeit University, Ramallah' on same line → 'Birzeit University'."""
        text = "Bachelor's in CS: Birzeit University, Ramallah"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.institution == "Birzeit University"
        assert "Ramallah" not in bs.institution


# ── Institution: multi-line extraction ────────────────────────────────────────

class TestInstitutionMultiLine:
    def test_institution_next_line(self):
        text = "Bachelor's in Computer Science\nBirzeit University\n2016-2020"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert "Birzeit" in bs.institution

    def test_institution_with_city_suffix_stripped(self):
        """'Birzeit University, Ramallah' on its own line → city stripped."""
        text = "Bachelor's in CS\nBirzeit University, Ramallah\n2016-2020"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.institution == "Birzeit University"
        assert "Ramallah" not in bs.institution

    def test_institution_before_degree_line(self):
        text = "Birzeit University\nBachelor's in Computer Science\n2016-2020"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.institution  # non-empty


# ── Attendance years: range ────────────────────────────────────────────────────

class TestAttendanceYearsRange:
    def test_year_range_extracted(self):
        entries, _ = _extract_education("Bachelor's in CS, State University, 2015-2019")
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.attendance_years == "2015–2019"

    def test_year_range_multiline(self):
        text = "Bachelor's in Engineering\nState University\n2014-2018"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert "2014" in bs.attendance_years
        assert "2018" in bs.attendance_years


# ── Attendance years: single graduation year ──────────────────────────────────

class TestAttendanceYearsSingle:
    def test_graduated_year(self):
        text = "Bachelor's Degree in Finance\nBirzeit University\nGraduated 2024"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.attendance_years == "2024"

    def test_expected_graduation(self):
        text = "Bachelor's in Computer Science\nAn-Najah University\nExpected Graduation 2025"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.attendance_years == "2025"

    def test_single_year_on_own_line(self):
        text = "Bachelor's Degree in Finance and Banking:\nBirzeit University, Ramallah\n2024"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.attendance_years == "2024"

    def test_no_year_leaves_empty(self):
        text = "Bachelor's in Computer Science\nBirzeit University"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.attendance_years == ""


# ── Arabic education patterns ─────────────────────────────────────────────────

class TestArabicEducation:
    def test_arabic_field_extracted(self):
        """'بكالوريوس إدارة أعمال' → field = 'إدارة أعمال'"""
        text = "بكالوريوس إدارة أعمال\nجامعة بيرزيت\n2024"
        entries, _ = _extract_education(text)
        bs = next((e for e in entries if e.degree_level == "Bachelor's"), None)
        assert bs is not None
        assert bs.field_of_study == "إدارة أعمال"

    def test_arabic_institution_extracted(self):
        """Arabic institution 'جامعة بيرزيت' on separate line is extracted."""
        text = "بكالوريوس إدارة أعمال\nجامعة بيرزيت\n2024"
        entries, _ = _extract_education(text)
        bs = next((e for e in entries if e.degree_level == "Bachelor's"), None)
        assert bs is not None
        assert "بيرزيت" in bs.institution

    def test_arabic_graduation_year(self):
        """Standalone year '2024' on its own line after Arabic education."""
        text = "بكالوريوس إدارة أعمال\nجامعة بيرزيت\n2024"
        entries, _ = _extract_education(text)
        bs = next((e for e in entries if e.degree_level == "Bachelor's"), None)
        assert bs is not None
        assert bs.attendance_years == "2024"

    def test_arabic_computer_science(self):
        text = "بكالوريوس علوم حاسوب\nجامعة النجاح الوطنية\n2018-2022"
        entries, _ = _extract_education(text)
        bs = next((e for e in entries if e.degree_level == "Bachelor's"), None)
        assert bs is not None
        assert bs.field_of_study == "علوم حاسوب"
        assert "النجاح" in bs.institution

    def test_arabic_masters(self):
        text = "ماجستير إدارة أعمال\nجامعة بيرزيت\n2022-2024"
        entries, highest = _extract_education(text)
        assert highest == "Master's"
        ms = next((e for e in entries if e.degree_level == "Master's"), None)
        assert ms is not None
        assert ms.field_of_study == "إدارة أعمال"


# ── Multiple education entries ────────────────────────────────────────────────

class TestMultipleEntries:
    def test_bachelor_and_master_both_extracted(self):
        text = (
            "Master's in Computer Science\nBirzeit University\n2020-2022\n\n"
            "Bachelor's in Information Technology\nAn-Najah University\n2016-2020"
        )
        entries, highest = _extract_education(text)
        assert highest == "Master's"
        degrees = {e.degree_level for e in entries}
        assert "Master's" in degrees
        assert "Bachelor's" in degrees

    def test_each_entry_has_supporting_evidence(self):
        text = (
            "Master's in Business Administration\nBirzeit University\n2020-2022\n\n"
            "Bachelor's in Accounting\nAn-Najah University\n2016-2020"
        )
        entries, _ = _extract_education(text)
        for entry in entries:
            assert entry.supporting_evidence


# ── Helpers unit tests ────────────────────────────────────────────────────────

class TestExtractInstitutionFromLine:
    def test_colon_separated(self):
        line = "Bachelor's Degree in Finance and Banking: Birzeit University, Ramallah"
        # indicator_pos = position of "University" in the line
        idx = line.index("University")
        result = _extract_institution_from_line(line, idx)
        assert result == "Birzeit University"

    def test_dash_separated(self):
        line = "MBA - Arab American University"
        idx = line.index("University")
        result = _extract_institution_from_line(line, idx)
        assert result == "Arab American University"

    def test_no_delimiter_full_line(self):
        line = "Birzeit University"
        idx = line.index("University")
        result = _extract_institution_from_line(line, idx)
        assert "Birzeit University" in result

    def test_empty_line(self):
        assert _extract_institution_from_line("", 0) == ""

    def test_comma_at_end_of_university_name(self):
        line = "Bachelor in CS: An-Najah University, Nablus"
        idx = line.index("University")
        result = _extract_institution_from_line(line, idx)
        assert result == "An-Najah University"
        assert "Nablus" not in result


class TestStripCitySuffix:
    def test_strips_ramallah(self):
        assert _strip_city_suffix("Birzeit University, Ramallah") == "Birzeit University"

    def test_strips_amman(self):
        assert _strip_city_suffix("University of Jordan, Amman") == "University of Jordan"

    def test_does_not_strip_university_name(self):
        # "Engineering" has no university indicator → still stripped, which is acceptable
        # "School of Engineering" has "school" → kept
        result = _strip_city_suffix("University of Technology, School of Engineering")
        assert "University of Technology" in result

    def test_no_comma_unchanged(self):
        assert _strip_city_suffix("Birzeit University") == "Birzeit University"

    def test_empty_string(self):
        assert _strip_city_suffix("") == ""


# ── Serialisation round-trip with new fields ──────────────────────────────────

class TestSerialisationRoundTrip:
    def test_full_round_trip(self):
        original = EducationEvidence(
            degree_level="Bachelor's",
            field_of_study="Finance and Banking",
            institution="Birzeit University",
            year=None,
            raw_text="Bachelor's Degree in Finance and Banking: Birzeit University\nGraduated 2024",
            inferred=False,
            basis="explicit_degree",
            confidence=1.0,
            attendance_years="2024",
            supporting_evidence=["Bachelor's Degree in Finance and Banking:..."],
        )
        d = dataclasses.asdict(original)
        restored = _education_from_dict(d)
        assert restored.field_of_study == "Finance and Banking"
        assert restored.institution == "Birzeit University"
        assert restored.attendance_years == "2024"
        assert restored.supporting_evidence

    def test_legacy_dict_no_new_fields(self):
        d = {
            "degree_level": "Bachelor's",
            "field_of_study": "Finance",
            "institution": "Some University",
            "year": 2015,
            "raw_text": "BSc Finance 2015",
        }
        result = _education_from_dict(d)
        assert result.attendance_years == ""
        assert result.supporting_evidence == []
