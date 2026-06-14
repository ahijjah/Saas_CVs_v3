"""
Tests for university-pattern education inference in cv_evidence.py.

Coverage:
  - Explicit degree keywords still work and set inferred=False, basis='explicit_degree'
  - University + 3-6 year range → inferred Bachelor's (confidence 0.85)
  - Duration outside 3-6 years → no inference
  - Training/bootcamp exclusions suppress inference
  - Arabic university indicators
  - Slashes and dashes as separators in the CV text
  - Inference does NOT fire when explicit Bachelor's+ is already found
  - New EducationEvidence fields serialise/deserialise correctly
"""
from __future__ import annotations

import pytest

from services.cv_evidence import (
    EDUCATION_LEVELS,
    EducationEvidence,
    _extract_education,
    _infer_university_bachelor,
)
from services.evidence_serialiser import _education_from_dict
import dataclasses


# ---------------------------------------------------------------------------
# Explicit degree detection — unchanged behaviour, new fields set correctly
# ---------------------------------------------------------------------------

class TestExplicitDegreeFields:
    def test_explicit_bachelors_inferred_false(self):
        entries, highest = _extract_education("Bachelor's in Computer Science, 2018")
        assert highest == "Bachelor's"
        bs = next(e for e in entries if e.degree_level == "Bachelor's")
        assert bs.inferred is False
        assert bs.basis == "explicit_degree"
        assert bs.confidence == 1.0

    def test_explicit_masters_inferred_false(self):
        entries, highest = _extract_education("Master's degree in Business Administration")
        assert highest == "Master's"
        ms = next(e for e in entries if e.degree_level == "Master's")
        assert ms.inferred is False
        assert ms.confidence == 1.0

    def test_explicit_phd_inferred_false(self):
        entries, highest = _extract_education("Ph.D. in Electrical Engineering, MIT, 2015")
        assert highest == "PhD"
        phd = next(e for e in entries if e.degree_level == "PhD")
        assert phd.inferred is False

    def test_no_degree_no_university_returns_none(self):
        entries, highest = _extract_education("Worked as a cashier for 5 years.")
        assert highest == "None"
        assert entries == []


# ---------------------------------------------------------------------------
# University-pattern inference — happy paths
# ---------------------------------------------------------------------------

class TestUniversityPatternInference:
    def test_birzeit_slash_format(self):
        text = "Birzeit University / Marketing Management / 2016–2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.degree_level == "Bachelor's"
        assert result.inferred is True
        assert result.basis == "university_study_pattern"
        assert result.confidence == 0.85
        assert result.year == 2020

    def test_extract_education_uses_inference(self):
        text = "Birzeit University / Marketing Management / 2016–2020"
        entries, highest = _extract_education(text)
        assert highest == "Bachelor's"
        inferred_entry = next(e for e in entries if e.inferred)
        assert inferred_entry.degree_level == "Bachelor's"

    def test_hyphen_year_separator(self):
        text = "An-Najah University, Computer Science, 2017-2021"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.degree_level == "Bachelor's"

    def test_en_dash_year_separator(self):
        text = "Jordan University of Technology – Engineering – 2015–2019"
        result = _infer_university_bachelor(text)
        assert result is not None

    def test_em_dash_year_separator(self):
        text = "Cairo University  2018—2022  Business"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.year == 2022

    def test_college_indicator(self):
        text = "Community College of Applied Sciences, Accounting, 2014-2018"
        result = _infer_university_bachelor(text)
        assert result is not None

    def test_institute_of_technology(self):
        text = "Institute of Technology, Mechanical Engineering, 2013-2017"
        result = _infer_university_bachelor(text)
        assert result is not None

    def test_polytechnic(self):
        text = "Polytechnic of Namibia, Civil Engineering, 2012-2016"
        result = _infer_university_bachelor(text)
        assert result is not None

    def test_minimum_duration_3_years(self):
        text = "State University, Education, 2017-2020"
        result = _infer_university_bachelor(text)
        assert result is not None

    def test_maximum_duration_6_years(self):
        text = "School of Medicine University, Medicine, 2014-2020"
        result = _infer_university_bachelor(text)
        assert result is not None

    def test_arabic_جامعة(self):
        text = "جامعة بيرزيت / إدارة الأعمال / 2016-2020"
        result = _infer_university_bachelor(text)
        assert result is not None
        assert result.degree_level == "Bachelor's"

    def test_arabic_كلية(self):
        text = "كلية العلوم التطبيقية، المحاسبة، 2015-2019"
        result = _infer_university_bachelor(text)
        assert result is not None

    def test_arabic_معهد(self):
        text = "معهد تقني متخصص، هندسة، 2016-2020"
        result = _infer_university_bachelor(text)
        assert result is not None


# ---------------------------------------------------------------------------
# Duration boundary conditions
# ---------------------------------------------------------------------------

class TestDurationBoundaries:
    def test_duration_2_years_no_inference(self):
        text = "City University, Marketing, 2018-2020"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_duration_7_years_no_inference(self):
        text = "City University, Engineering, 2012-2019"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_duration_1_year_no_inference(self):
        text = "City University, Short Programme, 2019-2020"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_no_year_range_no_inference(self):
        text = "City University, Business Administration"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_single_year_no_inference(self):
        text = "City University, Computer Science, 2020"
        result = _infer_university_bachelor(text)
        assert result is None


# ---------------------------------------------------------------------------
# Training / bootcamp exclusions
# ---------------------------------------------------------------------------

class TestTrainingExclusions:
    def test_bootcamp_suppresses_inference(self):
        text = "University bootcamp, Web Development, 2019-2023"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_training_centre_suppresses_inference(self):
        text = "University training, Python, 2018-2022"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_online_course_suppresses_inference(self):
        text = "University online course, Data Science, 2017-2021"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_certificate_course_suppresses_inference(self):
        text = "University certificate course, Finance, 2016-2020"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_coursera_suppresses_inference(self):
        text = "University via Coursera, Machine Learning, 2018-2022"
        result = _infer_university_bachelor(text)
        assert result is None

    def test_arabic_training_suppresses(self):
        text = "جامعة دورة تدريبية في التسويق 2017-2021"
        result = _infer_university_bachelor(text)
        assert result is None


# ---------------------------------------------------------------------------
# Interaction: explicit degree takes precedence over inference
# ---------------------------------------------------------------------------

class TestExplicitPrecedence:
    def test_explicit_bachelor_no_duplicate_inferred(self):
        text = "Bachelor's in Engineering, Birzeit University, 2016-2020"
        entries, highest = _extract_education(text)
        assert highest == "Bachelor's"
        inferred_entries = [e for e in entries if e.inferred]
        assert inferred_entries == [], "Should not create inferred entry when explicit degree found"

    def test_explicit_masters_no_bachelor_inferred(self):
        text = "Master's degree, Birzeit University, 2016-2020"
        entries, highest = _extract_education(text)
        assert highest == "Master's"
        inferred_entries = [e for e in entries if e.inferred]
        assert inferred_entries == []

    def test_explicit_phd_no_bachelor_inferred(self):
        text = "Ph.D. candidate, State University, 2014-2020"
        entries, highest = _extract_education(text)
        assert highest == "PhD"
        inferred_entries = [e for e in entries if e.inferred]
        assert inferred_entries == []

    def test_diploma_triggers_inference(self):
        # Diploma (idx=2) < Bachelor's (idx=4), so inference should still fire
        text = "Diploma in IT\nBirzeit University, Business, 2016-2020"
        entries, highest = _extract_education(text)
        assert highest == "Bachelor's"
        assert any(e.inferred for e in entries)


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------

class TestSerialisation:
    def test_inferred_entry_round_trips(self):
        original = EducationEvidence(
            degree_level="Bachelor's",
            field_of_study="Marketing",
            institution="Birzeit University",
            year=2020,
            raw_text="Birzeit University / Marketing / 2016–2020",
            inferred=True,
            basis="university_study_pattern",
            confidence=0.85,
        )
        d = dataclasses.asdict(original)
        reconstructed = _education_from_dict(d)
        assert reconstructed.inferred is True
        assert reconstructed.basis == "university_study_pattern"
        assert reconstructed.confidence == 0.85
        assert reconstructed.year == 2020

    def test_explicit_entry_round_trips(self):
        original = EducationEvidence(
            degree_level="Master's",
            field_of_study="Engineering",
            institution="MIT",
            year=2018,
            raw_text="Master's in Engineering, MIT, 2018",
            inferred=False,
            basis="explicit_degree",
            confidence=1.0,
        )
        d = dataclasses.asdict(original)
        reconstructed = _education_from_dict(d)
        assert reconstructed.inferred is False
        assert reconstructed.basis == "explicit_degree"
        assert reconstructed.confidence == 1.0

    def test_legacy_dict_without_new_fields(self):
        # Older serialised data without inferred/basis/confidence should deserialise safely
        d = {
            "degree_level": "Bachelor's",
            "field_of_study": "Science",
            "institution": "Some University",
            "year": 2015,
            "raw_text": "BSc Science 2015",
        }
        result = _education_from_dict(d)
        assert result.inferred is False
        assert result.basis == ""
        assert result.confidence == 1.0

    def test_confidence_stored_as_float(self):
        d = {"degree_level": "Bachelor's", "field_of_study": "", "institution": "",
             "year": None, "raw_text": "", "inferred": True,
             "basis": "university_study_pattern", "confidence": 0.85}
        result = _education_from_dict(d)
        assert isinstance(result.confidence, float)
        assert abs(result.confidence - 0.85) < 1e-9
