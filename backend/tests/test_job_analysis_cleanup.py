"""
Tests for job analysis LLM output cleanup and validation.

Covers:
  1. Generic weak standalone skill removal (e.g. "support" alone is removed,
     "user support" is kept).
  2. Non-academic education field removal (e.g. "Customer Service" removed,
     "Business Administration" kept).
  3. Logistical/availability condition reclassification to non_scoreable_requirements
     (full-time, on-site, travel, schedule, location, availability date).
  4. Cross-list deduplication across all requirement groups.
  5. Weight normalization after cleanup (certifications=0 when empty,
     other_requirements=0 when empty, redistributed weight sums to 100).
  6. Backward-compatibility: old analysis_json structures still work.
"""
from __future__ import annotations

import pytest
from services.ai_service import (
    _clean_job_analysis,
    _clean_education_fields,
    _dedup_requirements,
    _normalise_weights,
    _remove_weak_skills,
    _sanitise_classification,
    _validate_criteria,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_analysis(**overrides) -> dict:
    base = {
        "skills": {"required": ["Python"], "preferred": []},
        "experience": {"minimum_years": 3, "relevant_roles": [], "key_responsibilities": []},
        "education": {"minimum_level": "Bachelor's", "fields_of_study": []},
        "certifications": [],
        "soft_skills": {"required": [], "preferred": []},
        "domain_knowledge": [],
        "other_requirements": [],
        "scoring_weights": {
            "skills": 40, "experience": 30, "education": 15,
            "certifications": 5, "soft_skills": 5, "domain_knowledge": 5,
            "other_requirements": 0,
        },
    }
    base.update(overrides)
    return base


def _weights_sum(data: dict) -> int:
    w = data.get("scoring_weights", {})
    keys = ["skills", "experience", "education", "certifications",
            "soft_skills", "domain_knowledge", "other_requirements"]
    return sum(int(w.get(k, 0)) for k in keys)


# ── TestWeakSkillRemoval ──────────────────────────────────────────────────────

class TestWeakSkillRemoval:

    def test_standalone_support_removed_from_required_skills(self):
        data = _make_analysis(skills={"required": ["support"], "preferred": []})
        _remove_weak_skills(data)
        assert data["skills"]["required"] == []

    def test_standalone_support_removed_from_preferred_skills(self):
        data = _make_analysis(skills={"required": [], "preferred": ["support"]})
        _remove_weak_skills(data)
        assert data["skills"]["preferred"] == []

    def test_user_support_phrase_kept(self):
        data = _make_analysis(skills={"required": ["user support"], "preferred": []})
        _remove_weak_skills(data)
        assert "user support" in data["skills"]["required"]

    def test_project_coordination_kept(self):
        data = _make_analysis(skills={"required": ["project coordination"], "preferred": []})
        _remove_weak_skills(data)
        assert "project coordination" in data["skills"]["required"]

    def test_client_coordination_kept(self):
        data = _make_analysis(skills={"required": ["client coordination"], "preferred": []})
        _remove_weak_skills(data)
        assert "client coordination" in data["skills"]["required"]

    def test_hr_support_kept(self):
        data = _make_analysis(other_requirements=["HR support"])
        _remove_weak_skills(data)
        assert "HR support" in data["other_requirements"]

    def test_all_weak_words_removed(self):
        weak = ["support", "assist", "help", "coordinate", "follow up"]
        data = _make_analysis(skills={"required": weak, "preferred": []})
        _remove_weak_skills(data)
        assert data["skills"]["required"] == []

    def test_mixed_keeps_phrase_removes_standalone(self):
        data = _make_analysis(skills={
            "required": ["support", "user support", "help", "technical help desk"],
            "preferred": [],
        })
        _remove_weak_skills(data)
        assert "support" not in data["skills"]["required"]
        assert "user support" in data["skills"]["required"]
        assert "help" not in data["skills"]["required"]
        assert "technical help desk" in data["skills"]["required"]

    def test_weak_skill_in_soft_skills_removed(self):
        data = _make_analysis(soft_skills={"required": ["coordinate"], "preferred": ["assist"]})
        _remove_weak_skills(data)
        assert data["soft_skills"]["required"] == []
        assert data["soft_skills"]["preferred"] == []

    def test_weak_skill_in_domain_knowledge_removed(self):
        data = _make_analysis(domain_knowledge=["support", "finance"])
        _remove_weak_skills(data)
        assert "support" not in data["domain_knowledge"]
        assert "finance" in data["domain_knowledge"]

    def test_weak_skill_in_other_requirements_removed(self):
        data = _make_analysis(other_requirements=["help", "attention to detail"])
        _remove_weak_skills(data)
        assert "help" not in data["other_requirements"]
        assert "attention to detail" in data["other_requirements"]

    def test_case_insensitive_matching(self):
        data = _make_analysis(skills={"required": ["Support", "ASSIST", "Help"], "preferred": []})
        _remove_weak_skills(data)
        assert data["skills"]["required"] == []

    def test_stakeholder_coordination_kept(self):
        data = _make_analysis(soft_skills={"required": ["stakeholder coordination"], "preferred": []})
        _remove_weak_skills(data)
        assert "stakeholder coordination" in data["soft_skills"]["required"]


# ── TestEducationFieldsCleanup ────────────────────────────────────────────────

class TestEducationFieldsCleanup:

    def test_customer_service_removed(self):
        edu = {"minimum_level": "Bachelor's", "fields_of_study": ["Customer Service"]}
        data = {"education": edu}
        _clean_education_fields(data)
        assert "Customer Service" not in data["education"]["fields_of_study"]

    def test_communication_removed(self):
        edu = {"minimum_level": "Diploma", "fields_of_study": ["Communication"]}
        data = {"education": edu}
        _clean_education_fields(data)
        assert data["education"]["fields_of_study"] == []

    def test_teamwork_removed(self):
        data = {"education": {"minimum_level": "None", "fields_of_study": ["Teamwork"]}}
        _clean_education_fields(data)
        assert data["education"]["fields_of_study"] == []

    def test_leadership_removed(self):
        data = {"education": {"minimum_level": "None", "fields_of_study": ["Leadership"]}}
        _clean_education_fields(data)
        assert data["education"]["fields_of_study"] == []

    def test_problem_solving_removed(self):
        data = {"education": {"minimum_level": "None", "fields_of_study": ["Problem Solving"]}}
        _clean_education_fields(data)
        assert data["education"]["fields_of_study"] == []

    def test_technical_support_removed(self):
        data = {"education": {"minimum_level": "None", "fields_of_study": ["Technical Support"]}}
        _clean_education_fields(data)
        assert data["education"]["fields_of_study"] == []

    def test_administrative_support_removed(self):
        data = {"education": {"minimum_level": "None", "fields_of_study": ["Administrative Support"]}}
        _clean_education_fields(data)
        assert data["education"]["fields_of_study"] == []

    def test_business_administration_kept(self):
        data = {"education": {"minimum_level": "Bachelor's", "fields_of_study": ["Business Administration"]}}
        _clean_education_fields(data)
        assert "Business Administration" in data["education"]["fields_of_study"]

    def test_valid_fields_kept(self):
        valid = [
            "Business Administration", "Management", "Human Resources",
            "Accounting", "Finance", "Computer Science",
            "Information Technology", "Engineering", "Marketing",
        ]
        data = {"education": {"minimum_level": "Bachelor's", "fields_of_study": valid}}
        _clean_education_fields(data)
        assert data["education"]["fields_of_study"] == valid

    def test_mixed_removes_invalid_keeps_valid(self):
        data = {"education": {"minimum_level": "Bachelor's", "fields_of_study": [
            "Business Administration", "Customer Service", "Finance", "Teamwork",
        ]}}
        _clean_education_fields(data)
        assert set(data["education"]["fields_of_study"]) == {"Business Administration", "Finance"}

    def test_case_insensitive(self):
        data = {"education": {"minimum_level": "None", "fields_of_study": [
            "CUSTOMER SERVICE", "customer service", "Customer Service",
        ]}}
        _clean_education_fields(data)
        assert data["education"]["fields_of_study"] == []

    def test_missing_education_key_does_not_raise(self):
        data = {}
        _clean_education_fields(data)  # should not raise

    def test_empty_fields_of_study_unchanged(self):
        data = {"education": {"minimum_level": "Bachelor's", "fields_of_study": []}}
        _clean_education_fields(data)
        assert data["education"]["fields_of_study"] == []


# ── TestLogisticalNonScoreable ────────────────────────────────────────────────

class TestLogisticalNonScoreable:

    def test_full_time_availability_moved_to_non_scoreable(self):
        data = _make_analysis(other_requirements=["Full-time availability required"])
        _sanitise_classification(data)
        assert not any("full-time availability" in r.lower() for r in data["other_requirements"])
        cats = [r["category"] for r in data["non_scoreable_requirements"]]
        assert "logistical_requirement" in cats

    def test_onsite_work_moved_to_non_scoreable(self):
        data = _make_analysis(other_requirements=["On-site work required"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert len(data["non_scoreable_requirements"]) == 1
        assert data["non_scoreable_requirements"][0]["category"] == "logistical_requirement"

    def test_willingness_to_travel_moved(self):
        data = _make_analysis(other_requirements=["Willingness to travel as needed"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert data["non_scoreable_requirements"][0]["category"] == "logistical_requirement"

    def test_work_schedule_moved(self):
        data = _make_analysis(other_requirements=["Flexible work schedule required"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert data["non_scoreable_requirements"][0]["category"] == "logistical_requirement"

    def test_location_requirement_moved(self):
        data = _make_analysis(other_requirements=["Location requirement: must be based in Dubai"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert data["non_scoreable_requirements"][0]["category"] == "logistical_requirement"

    def test_availability_date_moved(self):
        data = _make_analysis(other_requirements=["Availability date: immediate availability preferred"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert data["non_scoreable_requirements"][0]["category"] == "logistical_requirement"

    def test_immediate_start_moved(self):
        data = _make_analysis(other_requirements=["Must be available to start immediately"])
        _sanitise_classification(data)
        assert data["other_requirements"] == []

    def test_logistical_is_not_scoreable(self):
        data = _make_analysis(other_requirements=["On-site work required"])
        _sanitise_classification(data)
        assert data["non_scoreable_requirements"][0]["is_scoreable"] is False

    def test_scoreable_requirement_unchanged(self):
        data = _make_analysis(other_requirements=["Proficiency in Excel", "Attention to detail"])
        _sanitise_classification(data)
        assert "Proficiency in Excel" in data["other_requirements"]
        assert "Attention to detail" in data["other_requirements"]

    def test_mixed_keeps_scoreable_moves_logistical(self):
        data = _make_analysis(other_requirements=[
            "Attention to detail",
            "On-site work required",
            "Willingness to travel",
            "Team player",
        ])
        _sanitise_classification(data)
        assert set(data["other_requirements"]) == {"Attention to detail", "Team player"}
        assert len(data["non_scoreable_requirements"]) == 2

    def test_existing_post_hiring_patterns_still_work(self):
        data = _make_analysis(other_requirements=[
            "Must pass background check",
            "On-site work required",
        ])
        _sanitise_classification(data)
        assert data["other_requirements"] == []
        assert len(data["post_hiring_conditions"]) == 1
        assert len(data["non_scoreable_requirements"]) == 1


# ── TestDeduplication ────────────────────────────────────────────────────────

class TestDeduplication:

    def test_duplicate_within_required_skills_removed(self):
        data = _make_analysis(skills={"required": ["Python", "python", "PYTHON"], "preferred": []})
        _dedup_requirements(data)
        assert len(data["skills"]["required"]) == 1

    def test_duplicate_across_required_and_preferred_removed(self):
        data = _make_analysis(skills={"required": ["Python"], "preferred": ["Python"]})
        _dedup_requirements(data)
        # Python in required → removed from preferred
        assert "Python" in data["skills"]["required"]
        assert "Python" not in data["skills"]["preferred"]

    def test_item_in_skills_and_soft_skills_kept_in_skills(self):
        data = _make_analysis(
            skills={"required": ["Communication"], "preferred": []},
            soft_skills={"required": ["Communication"], "preferred": []},
        )
        _dedup_requirements(data)
        assert "Communication" in data["skills"]["required"]
        assert "Communication" not in data["soft_skills"]["required"]

    def test_item_in_skills_and_domain_knowledge_kept_in_skills(self):
        data = _make_analysis(
            skills={"required": ["Finance"], "preferred": []},
            domain_knowledge=["Finance"],
        )
        _dedup_requirements(data)
        assert "Finance" in data["skills"]["required"]
        assert "Finance" not in data["domain_knowledge"]

    def test_item_in_domain_knowledge_and_other_removed_from_other(self):
        data = _make_analysis(
            domain_knowledge=["Healthcare"],
            other_requirements=["Healthcare experience", "Attention to detail"],
        )
        _dedup_requirements(data)
        # "Healthcare experience" is different from "Healthcare" → both kept
        assert "Healthcare" in data["domain_knowledge"]
        assert "Healthcare experience" in data["other_requirements"]

    def test_exact_duplicate_across_domain_and_other_removed_from_other(self):
        data = _make_analysis(
            domain_knowledge=["Healthcare"],
            other_requirements=["Healthcare"],
        )
        _dedup_requirements(data)
        assert "Healthcare" in data["domain_knowledge"]
        assert "Healthcare" not in data["other_requirements"]

    def test_no_duplicates_unchanged(self):
        data = _make_analysis(
            skills={"required": ["Python", "SQL"], "preferred": ["Excel"]},
            domain_knowledge=["Finance"],
            other_requirements=["Attention to detail"],
        )
        _dedup_requirements(data)
        assert len(data["skills"]["required"]) == 2
        assert len(data["skills"]["preferred"]) == 1
        assert len(data["domain_knowledge"]) == 1
        assert len(data["other_requirements"]) == 1

    def test_empty_lists_unchanged(self):
        data = _make_analysis()
        _dedup_requirements(data)
        assert data["skills"]["required"] == ["Python"]

    def test_preferred_skill_cross_dedup_with_soft_skill(self):
        data = _make_analysis(
            skills={"required": [], "preferred": ["Leadership"]},
            soft_skills={"required": ["Leadership"], "preferred": []},
        )
        _dedup_requirements(data)
        # skills.preferred has higher priority than soft_skills.required
        assert "Leadership" in data["skills"]["preferred"]
        assert "Leadership" not in data["soft_skills"]["required"]


# ── TestWeightNormalization ───────────────────────────────────────────────────

class TestWeightNormalization:

    def test_weights_sum_to_100_when_certifications_empty(self):
        data = _make_analysis(
            certifications=[],
            scoring_weights={
                "skills": 35, "experience": 30, "education": 15,
                "certifications": 10, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 0,
            },
        )
        _normalise_weights(data)
        assert _weights_sum(data) == 100

    def test_certifications_weight_zeroed_when_list_empty(self):
        data = _make_analysis(
            certifications=[],
            scoring_weights={
                "skills": 35, "experience": 30, "education": 15,
                "certifications": 10, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 0,
            },
        )
        _normalise_weights(data)
        assert data["scoring_weights"]["certifications"] == 0

    def test_other_weight_zeroed_when_other_requirements_empty(self):
        data = _make_analysis(
            other_requirements=[],
            scoring_weights={
                "skills": 35, "experience": 25, "education": 15,
                "certifications": 5, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 10,
            },
        )
        _normalise_weights(data)
        assert data["scoring_weights"]["other_requirements"] == 0
        assert _weights_sum(data) == 100

    def test_freed_weight_redistributed_to_skills_and_experience(self):
        data = _make_analysis(
            certifications=[],
            scoring_weights={
                "skills": 40, "experience": 20, "education": 15,
                "certifications": 10, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 5,
            },
        )
        _normalise_weights(data)
        w = data["scoring_weights"]
        # Freed 10 from certifications, skills=40 experience=20 → skills gets more
        assert w["skills"] > 40 or w["experience"] > 20  # at least one increased
        assert _weights_sum(data) == 100

    def test_no_change_when_all_non_empty(self):
        data = _make_analysis(
            certifications=["AWS Certified"],
            other_requirements=["Attention to detail"],
            scoring_weights={
                "skills": 35, "experience": 30, "education": 10,
                "certifications": 10, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 5,
            },
        )
        original_weights = dict(data["scoring_weights"])
        _normalise_weights(data)
        assert data["scoring_weights"] == original_weights

    def test_both_empty_sections_both_zeroed_weights_sum_100(self):
        data = _make_analysis(
            certifications=[],
            other_requirements=[],
            scoring_weights={
                "skills": 35, "experience": 25, "education": 15,
                "certifications": 10, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 5,
            },
        )
        _normalise_weights(data)
        w = data["scoring_weights"]
        assert w["certifications"] == 0
        assert w["other_requirements"] == 0
        assert _weights_sum(data) == 100

    def test_existing_zero_certifications_weight_unchanged(self):
        data = _make_analysis(
            certifications=[],
            scoring_weights={
                "skills": 40, "experience": 30, "education": 15,
                "certifications": 0, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 5,
            },
        )
        _normalise_weights(data)
        assert data["scoring_weights"]["certifications"] == 0
        assert _weights_sum(data) == 100


# ── TestCleanJobAnalysisIntegration ──────────────────────────────────────────

class TestCleanJobAnalysisIntegration:
    """End-to-end tests through _clean_job_analysis()."""

    def test_full_pipeline_weights_sum_to_100(self):
        data = _make_analysis(
            skills={"required": ["Python", "support"], "preferred": ["assist"]},
            certifications=[],
            other_requirements=["Attention to detail"],
            scoring_weights={
                "skills": 35, "experience": 25, "education": 10,
                "certifications": 10, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 10,
            },
        )
        _clean_job_analysis(data)
        assert _weights_sum(data) == 100

    def test_weak_skill_and_education_cleanup_combined(self):
        data = _make_analysis(
            skills={"required": ["support", "Python"], "preferred": ["assist"]},
            education={
                "minimum_level": "Bachelor's",
                "fields_of_study": ["Customer Service", "Business Administration"],
            },
        )
        _clean_job_analysis(data)
        assert "support" not in data["skills"]["required"]
        assert "Python" in data["skills"]["required"]
        assert "Customer Service" not in data["education"]["fields_of_study"]
        assert "Business Administration" in data["education"]["fields_of_study"]

    def test_dedup_and_weak_skill_removal_do_not_interact_badly(self):
        data = _make_analysis(skills={
            "required": ["support", "Python", "Python"],
            "preferred": ["Python"],
        })
        _clean_job_analysis(data)
        assert "support" not in data["skills"]["required"]
        assert data["skills"]["required"].count("Python") == 1
        assert "Python" not in data["skills"]["preferred"]

    def test_old_analysis_json_structure_still_works(self):
        """Backward compat: analysis_json without soft_skills block is handled."""
        data = {
            "skills": {"required": ["Excel"], "preferred": []},
            "experience": {"minimum_years": 2, "relevant_roles": [], "key_responsibilities": []},
            "education": {"minimum_level": "None", "fields_of_study": ["Communication"]},
            "certifications": [],
            "domain_knowledge": [],
            "other_requirements": [],
            "scoring_weights": {
                "skills": 40, "experience": 30, "education": 15,
                "certifications": 5, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 0,
            },
        }
        # No soft_skills key — must not raise
        _clean_job_analysis(data)
        assert "Communication" not in data["education"]["fields_of_study"]
        assert _weights_sum(data) == 100

    def test_validate_then_sanitise_then_clean_pipeline(self):
        """Full pipeline: validate → sanitise → clean."""
        data = {
            "skills": {"required": ["Python", "support"], "preferred": []},
            "experience": {"minimum_years": 3, "relevant_roles": [], "key_responsibilities": []},
            "education": {
                "minimum_level": "Bachelor's",
                "fields_of_study": ["Customer Service", "Finance"],
            },
            "certifications": [],
            "domain_knowledge": ["support"],
            "other_requirements": [
                "Attention to detail",
                "On-site work required",
                "Willingness to travel",
            ],
            "scoring_weights": {
                "skills": 35, "experience": 25, "education": 10,
                "certifications": 10, "soft_skills": 5, "domain_knowledge": 5,
                "other_requirements": 10,
            },
        }
        _validate_criteria(data)
        _sanitise_classification(data)
        _clean_job_analysis(data)

        # Generic weak skill removed
        assert "support" not in data["skills"]["required"]
        assert "support" not in data["domain_knowledge"]

        # Non-academic education field removed
        assert "Customer Service" not in data["education"]["fields_of_study"]
        assert "Finance" in data["education"]["fields_of_study"]

        # Logistical requirements moved to non_scoreable_requirements
        assert "On-site work required" not in data["other_requirements"]
        assert "Willingness to travel" not in data["other_requirements"]
        assert "Attention to detail" in data["other_requirements"]
        assert len(data["non_scoreable_requirements"]) == 2

        # Weights sum to 100 and empty sections have 0 weight
        assert data["scoring_weights"]["certifications"] == 0
        assert _weights_sum(data) == 100
