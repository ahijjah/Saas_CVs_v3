"""
EDU-02.2: Education Precision Fixes tests.

Covers all fixes from EDU-02.2:
  - False Master's detection ("master the process" must NOT match)
  - Institution cleanup: pipe-separated "Degree | Institution" extracts correct side
  - Field of study from "Bachelor of <Field>" ("of" preposition pattern)
  - Education/experience boundary: degree lines must not become role_title/employer
  - Real-CV validation set (Cases A, B, C from spec)
"""
from __future__ import annotations

import pytest

from services.cv_evidence import (
    CVFactsExtractor,
    _extract_education,
    _extract_institution_from_line,
)


# ── Issue 1: False Master's Detection ────────────────────────────────────────

class TestMastersFalsePositive:
    """Bare 'master' used as a verb must not produce a Master's education entry."""

    def test_master_the_process_not_education(self):
        entries, highest = _extract_education(
            "Master the seven key stages of the sales process"
        )
        assert not any(e.degree_level == "Master's" for e in entries)
        assert highest == "None"

    def test_master_sales_skills_not_education(self):
        entries, _ = _extract_education(
            "master sales skills and communication techniques"
        )
        assert not any(e.degree_level == "Master's" for e in entries)

    def test_master_the_art_not_education(self):
        entries, _ = _extract_education(
            "master the art of negotiation and communication"
        )
        assert not any(e.degree_level == "Master's" for e in entries)

    def test_master_your_skills_not_education(self):
        entries, _ = _extract_education("master your communication skills")
        assert not any(e.degree_level == "Master's" for e in entries)

    def test_master_level_skills_not_education(self):
        # "master-level" is not a degree — but "master" in it hits word boundary
        # The new pattern requires 's or 'of/in/degree' after bare 'master'
        entries, _ = _extract_education(
            "demonstrates master-level proficiency in customer relations"
        )
        # "master" followed by "-" not "s/of/in/degree" → no match
        assert not any(e.degree_level == "Master's" for e in entries)

    # ── Positive cases that MUST still match ──────────────────────────────────

    def test_masters_apostrophe_detected(self):
        entries, highest = _extract_education("Master's in Computer Science")
        assert highest == "Master's"

    def test_masters_plural_detected(self):
        entries, highest = _extract_education("Masters Degree in Business Administration")
        assert highest == "Master's"

    def test_master_of_detected(self):
        entries, highest = _extract_education("Master of Business Administration")
        assert highest == "Master's"

    def test_master_in_detected(self):
        entries, highest = _extract_education("Master in Public Administration")
        assert highest == "Master's"

    def test_mba_detected(self):
        entries, highest = _extract_education("MBA - Arab American University\n2019-2021")
        assert highest == "Master's"

    def test_msc_detected(self):
        entries, highest = _extract_education("MSc in Data Science")
        assert highest == "Master's"

    def test_ma_detected(self):
        entries, highest = _extract_education("M.A. in English Literature")
        assert highest == "Master's"

    def test_arabic_masters_detected(self):
        entries, highest = _extract_education("ماجستير إدارة أعمال\nجامعة بيرزيت")
        assert highest == "Master's"


# ── Issue 2: Institution Cleanup (pipe-separated) ─────────────────────────────

class TestInstitutionCleanup:
    """Pipe and dash-separated formats must return the institution name only."""

    def test_pipe_separated_degree_then_uni(self):
        """'Bachelor of X | Al-Quds Open University' → institution = Al-Quds Open University."""
        text = "Bachelor of Business Administration | Al-Quds Open University\n2016-2021"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.institution == "Al-Quds Open University"

    def test_pipe_no_degree_label_in_institution(self):
        text = "Bachelor of Business Administration | Al-Quds Open University\n2016-2021"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert "Bachelor" not in bs.institution
        assert "Administration" not in bs.institution

    def test_dash_separated_mba(self):
        """'MBA - Arab American University' → institution = Arab American University."""
        text = "MBA - Arab American University\n2019-2021"
        entries, _ = _extract_education(text)
        ms = next(e for e in entries if e.degree_level == "Master's")
        assert ms.institution == "Arab American University"

    def test_pipe_extraction_helper_with_pipe_delimiter(self):
        """_extract_institution_from_line recognises | as a delimiter."""
        line = "Bachelor of Business Administration | Al-Quds Open University"
        idx = line.index("University")
        result = _extract_institution_from_line(line, idx)
        assert result == "Al-Quds Open University"
        assert "Bachelor" not in result


# ── Issue 3: Field of Study from "of" preposition ────────────────────────────

class TestFieldOfStudyFromOf:
    """'Bachelor of <Field>' must extract <Field> as field_of_study."""

    def test_bachelor_of_business_administration(self):
        text = "Bachelor of Business Administration | Al-Quds Open University\n2016-2021"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Business Administration"

    def test_bachelor_of_finance_and_banking(self):
        text = "Bachelor of Finance and Banking\nBirzeit University\n2016-2020"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Finance and Banking"

    def test_bachelor_of_computer_science(self):
        text = "Bachelor of Computer Science\nJordan University\n2015-2019"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Computer Science"

    def test_bachelor_of_information_systems(self):
        text = "Bachelor of Information Systems\nAl-Quds University\n2018-2022"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Information Systems"

    def test_master_of_business_administration(self):
        text = "Master of Business Administration\nBirzeit University\n2020-2022"
        entries, _ = _extract_education(text)
        ms = next(e for e in entries if e.degree_level == "Master's")
        assert ms.field_of_study == "Business Administration"

    def test_in_preposition_still_works(self):
        """Existing 'in <Field>' format must not be broken."""
        text = "Bachelor's in Computer Science\nBirzeit University\n2016-2020"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Computer Science"

    def test_university_of_x_on_next_line_not_captured_as_field(self):
        """'University of Jordan' on the next line must NOT become field = 'Jordan'."""
        text = "Bachelor's\nUniversity of Jordan\n2016-2020"
        entries, _ = _extract_education(text)
        bs = next((e for e in entries if e.degree_level == "Bachelor's"), None)
        if bs:
            assert bs.field_of_study != "Jordan"


# ── Issue 4: Education/Experience Boundary Protection ────────────────────────

class TestEducationExperienceBoundary:
    """Education lines must not bleed into experience role_title or employer."""

    def test_degree_line_not_role_title(self):
        text = "Bachelor of Business Administration | Al-Quds Open University\n2016-2021"
        facts = CVFactsExtractor().extract(text)
        for exp in facts.experience:
            assert "Bachelor" not in exp.role_title
            assert "Administration" not in exp.role_title

    def test_degree_line_not_employer(self):
        text = "Bachelor of Business Administration | Al-Quds Open University\n2016-2021"
        facts = CVFactsExtractor().extract(text)
        for exp in facts.experience:
            assert "Bachelor" not in exp.employer

    def test_legitimate_experience_still_extracted(self):
        """A real experience block after the education section must still be extracted."""
        text = (
            "Bachelor of Business Administration\nAl-Quds University\n2012-2016\n\n"
            "Work Experience\n"
            "Financial Analyst\nABC Bank\n2016-2020"
        )
        facts = CVFactsExtractor().extract(text)
        # Education should be extracted
        assert facts.highest_education_level == "Bachelor's"
        # Experience should include the real job (date range 2016-2020)
        years = sum(e.years for e in facts.experience)
        assert years >= 4.0


# ── Real-CV Validation Set (spec Cases A, B, C) ──────────────────────────────

class TestRealCVCases:
    """Production examples from the EDU-02.2 specification."""

    def test_case_a_full_extraction(self):
        """Case A: Bachelor of Business Administration | Al-Quds Open University / 2016–2021."""
        text = "Bachelor of Business Administration | Al-Quds Open University\n2016-2021"
        entries, highest = _extract_education(text)
        assert highest == "Bachelor's"
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.field_of_study == "Business Administration"
        assert bs.institution == "Al-Quds Open University"
        assert bs.attendance_years == "2016–2021"

    def test_case_b_sales_master_produces_no_masters_entry(self):
        """Case B: 'Master the seven key stages...' must not yield a Master's entry."""
        text = "Master the seven key stages of the sales process"
        entries, highest = _extract_education(text)
        assert not any(e.degree_level == "Master's" for e in entries)
        assert highest == "None"

    def test_case_c_mba_institution_and_years(self):
        """Case C: MBA - Arab American University / 2019-2021."""
        text = "MBA - Arab American University\n2019-2021"
        entries, highest = _extract_education(text)
        assert highest == "Master's"
        ms = next(e for e in entries if e.degree_level == "Master's")
        assert ms.institution == "Arab American University"
        assert ms.attendance_years == "2019–2021"

    def test_case_a_basis_and_confidence(self):
        """Case A entry must be explicit (not inferred)."""
        text = "Bachelor of Business Administration | Al-Quds Open University\n2016-2021"
        entries, _ = _extract_education(text)
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.basis == "explicit_degree"
        assert bs.confidence == 1.0
        assert bs.supporting_evidence

    def test_case_c_no_field_for_mba(self):
        """MBA on its own line has no 'of/in' so field_of_study should be empty."""
        text = "MBA - Arab American University\n2019-2021"
        entries, _ = _extract_education(text)
        ms = next(e for e in entries if e.degree_level == "Master's")
        # MBA conveys the field implicitly; no spurious field extracted
        assert ms.field_of_study == ""
