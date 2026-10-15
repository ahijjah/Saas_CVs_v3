"""
D-02 — Job Analysis Quality & Requirement Classification

Tests that:
- post-hiring/admin conditions (background check, reference check, etc.)
  are classified as non-scoreable and excluded from other_requirements
- non-scoreable screening conditions (right to work, etc.) are excluded
- job metadata (title, department, etc.) is passed to the AI via the context builder
- _sanitise_classification correctly categorises items found in other_requirements
- existing criteria analysis format is backward-compatible
"""

import pytest
from services.ai_service import (
    _sanitise_classification,
    _build_job_context_message,
)


# ── _sanitise_classification ──────────────────────────────────────────────────

def _make_analysis(**kwargs):
    base = {
        "skills": {"required": ["Python"], "preferred": []},
        "experience": {"minimum_years": 3, "relevant_roles": [], "key_responsibilities": []},
        "education": {"minimum_level": "Bachelor's", "fields_of_study": []},
        "certifications": [],
        "domain_knowledge": [],
        "other_requirements": [],
        "scoring_weights": {
            "skills": 40, "experience": 30, "education": 15,
            "certifications": 5, "soft_skills": 5, "domain_knowledge": 5, "other_requirements": 0,
        },
    }
    base.update(kwargs)
    return base


class TestSanitiseClassification:
    def test_background_check_removed_from_other_requirements(self):
        data = _make_analysis(other_requirements=["Must pass a background check"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert len(data["post_hiring_conditions"]) == 1
        item = data["post_hiring_conditions"][0]
        assert item["is_scoreable"] is False
        assert item["category"] == "background_check"
        assert "background check" in item["text"].lower()

    def test_reference_check_removed_from_other_requirements(self):
        data = _make_analysis(other_requirements=["Provide references on request"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert any("reference" in i["text"].lower() for i in data["post_hiring_conditions"])
        item = data["post_hiring_conditions"][0]
        assert item["is_scoreable"] is False
        assert item["category"] == "reference_check"

    def test_criminal_record_check_removed(self):
        data = _make_analysis(other_requirements=["Subject to criminal record check"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert len(data["post_hiring_conditions"]) == 1

    def test_police_clearance_removed(self):
        data = _make_analysis(other_requirements=["Police clearance certificate required"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        item = data["post_hiring_conditions"][0]
        assert item["category"] == "police_clearance"

    def test_medical_check_removed(self):
        data = _make_analysis(other_requirements=["Must pass medical check before joining"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        item = data["post_hiring_conditions"][0]
        assert item["category"] == "medical_check"

    def test_right_to_work_removed(self):
        data = _make_analysis(other_requirements=["Must have the right to work in the UK"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert len(data["non_scoreable_requirements"]) == 1
        item = data["non_scoreable_requirements"][0]
        assert item["is_scoreable"] is False

    def test_work_permit_removed(self):
        data = _make_analysis(other_requirements=["Valid work permit required"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert any("work permit" in i["text"].lower() for i in data["non_scoreable_requirements"])

    def test_scoreable_requirement_kept(self):
        data = _make_analysis(other_requirements=["Strong attention to detail", "Team player"])
        _sanitise_classification(data)
        assert "Strong attention to detail" in data["other_requirements"]
        assert "Team player" in data["other_requirements"]
        assert data["post_hiring_conditions"] == []
        assert data["non_scoreable_requirements"] == []

    def test_mixed_requirements_separated(self):
        data = _make_analysis(other_requirements=[
            "Attention to detail",
            "Must pass background check",
            "Provide 2 references",
            "Must have right to work in UAE",
            "Excellent written communication",
        ])
        _sanitise_classification(data)
        assert set(data["other_requirements"]) == {"Attention to detail", "Excellent written communication"}
        assert len(data["post_hiring_conditions"]) == 2
        assert len(data["non_scoreable_requirements"]) == 1

    def test_warnings_generated_for_removed_items(self):
        data = _make_analysis(other_requirements=["Must pass background check"])
        _sanitise_classification(data)
        assert len(data["warnings"]) >= 1
        assert any("background check" in w.lower() for w in data["warnings"])

    def test_new_fields_always_present_after_sanitise(self):
        """Even if AI returned no classification fields, _sanitise_classification adds them."""
        data = _make_analysis()  # no classification fields at all
        _sanitise_classification(data)
        assert "non_scoreable_requirements" in data
        assert "post_hiring_conditions" in data
        assert "informational_items" in data
        assert "warnings" in data

    def test_existing_classification_fields_preserved(self):
        """Items already placed by AI in the correct bucket are not duplicated."""
        existing = [{"text": "Pass security clearance", "category": "background_check",
                     "is_scoreable": False, "reason": "pre-classified", "source_field": "description"}]
        data = _make_analysis(post_hiring_conditions=existing)
        _sanitise_classification(data)
        # Only one item in post_hiring_conditions (not doubled)
        assert len(data["post_hiring_conditions"]) == 1

    def test_case_insensitive_matching(self):
        data = _make_analysis(other_requirements=["MUST PASS BACKGROUND CHECK"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert len(data["post_hiring_conditions"]) == 1

    def test_is_scoreable_false_for_all_extracted_conditions(self):
        data = _make_analysis(other_requirements=[
            "Must pass background check",
            "Provide references",
            "Work permit required",
        ])
        _sanitise_classification(data)
        for item in data["post_hiring_conditions"] + data["non_scoreable_requirements"]:
            assert item["is_scoreable"] is False, f"Expected is_scoreable=False for: {item['text']}"


# ── _build_job_context_message ────────────────────────────────────────────────

class TestBuildJobContextMessage:
    def test_includes_description(self):
        msg = _build_job_context_message("Need a Python developer", None)
        assert "Need a Python developer" in msg

    def test_includes_job_metadata_fields(self):
        meta = {
            "title": "Senior Software Engineer",
            "department": "Engineering",
            "experience_level": "Senior",
            "location": "Dubai",
            "job_type": "Full-time",
            "work_mode": "Hybrid",
        }
        msg = _build_job_context_message("Python developer required", meta)
        assert "Senior Software Engineer" in msg
        assert "Engineering" in msg
        assert "Senior" in msg
        assert "Dubai" in msg
        assert "Full-time" in msg
        assert "Hybrid" in msg
        assert "Python developer required" in msg

    def test_none_metadata_falls_back_to_description_only(self):
        msg = _build_job_context_message("Python developer", None)
        assert "Job Context:" not in msg
        assert "Python developer" in msg

    def test_partial_metadata_only_shows_present_fields(self):
        meta = {"title": "Accountant", "department": None, "location": "Riyadh"}
        msg = _build_job_context_message("Accounting role", meta)
        assert "Accountant" in msg
        assert "Riyadh" in msg
        # None values should not appear
        assert "None" not in msg

    def test_metadata_appears_before_description(self):
        meta = {"title": "HR Manager"}
        msg = _build_job_context_message("Full description here", meta)
        assert msg.index("HR Manager") < msg.index("Full description here")

    def test_empty_description_handled_gracefully(self):
        msg = _build_job_context_message("", {"title": "Driver"})
        assert "Driver" in msg  # metadata still included

    def test_description_without_metadata_has_no_context_header(self):
        msg = _build_job_context_message("Just a description", {})
        assert "Job Context:" not in msg


# ── Scoring safety rule ───────────────────────────────────────────────────────

class TestScoringSafetyRule:
    """
    The scoring pipeline must never receive post-hiring conditions as scoring criteria.
    After _sanitise_classification, other_requirements must contain only scoreable items.
    """

    def test_other_requirements_contains_only_scoreable_after_sanitise(self):
        non_scoreable_inputs = [
            "Must pass background check",
            "Provide 3 professional references",
            "Criminal record check required",
            "Medical fitness test before joining",
            "Police clearance certificate",
            "Must have right to work in Saudi Arabia",
            "Valid UAE work permit required",
        ]
        for item in non_scoreable_inputs:
            data = _make_analysis(other_requirements=[item, "Proficiency in Excel"])
            _sanitise_classification(data)
            assert item not in data["other_requirements"], (
                f"Non-scoreable item leaked into scoring criteria: '{item}'"
            )
            assert "Proficiency in Excel" in data["other_requirements"]

    def test_empty_other_requirements_is_valid(self):
        data = _make_analysis(other_requirements=["Must pass background check"])
        _sanitise_classification(data)
        assert isinstance(data["other_requirements"], list)
        assert len(data["other_requirements"]) == 0
