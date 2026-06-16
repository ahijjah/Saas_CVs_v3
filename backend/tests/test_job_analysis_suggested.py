"""
D-03 — suggested_requirements Support

Tests that:
- suggested_requirements is preserved in analysis_json when present
- approved-criteria duplicates are removed from suggested
- non-scoreable conditions are removed from suggested
- cross-category duplicates within suggested are deduplicated
- empty categories are dropped from suggested output
- responsibility-derived competency phrases are kept when not in approved criteria
- flatten_criteria_for_scoring() never includes suggested items
- old analysis_json without suggested_requirements continues to work
- _validate_criteria() safely normalises malformed suggested values
- _clean_suggested_requirements() is idempotent
"""

import copy
import pytest

from services.ai_service import (
    _clean_suggested_requirements,
    _clean_job_analysis,
    _validate_criteria,
    flatten_criteria_for_scoring,
)


# ─── shared helpers ────────────────────────────────────────────────────────────

def _base_analysis(**kwargs) -> dict:
    """Minimal valid analysis_json, extended by kwargs."""
    base = {
        "skills": {"required": ["Microsoft Excel", "Data Entry"], "preferred": ["SAP"]},
        "soft_skills": {"required": ["Attention to detail"], "preferred": []},
        "experience": {
            "minimum_years": 3,
            "relevant_roles": ["HR Officer", "People Operations Specialist"],
            "key_responsibilities": [],
        },
        "education": {"minimum_level": "Bachelor's", "fields_of_study": ["Human Resources"]},
        "certifications": ["SHRM-CP"],
        "domain_knowledge": ["Labour law", "Payroll processing"],
        "other_requirements": ["Proficiency in written English"],
        "scoring_weights": {
            "skills": 35, "experience": 30, "education": 10,
            "certifications": 5, "soft_skills": 10,
            "domain_knowledge": 10, "other_requirements": 0,
        },
    }
    base.update(kwargs)
    return base


def _with_suggested(suggested: dict, **kwargs) -> dict:
    data = _base_analysis(**kwargs)
    data["suggested_requirements"] = suggested
    return data


# ─── _clean_suggested_requirements ────────────────────────────────────────────

class TestCleanSuggestedRequirements:

    def test_no_suggested_key_is_noop(self):
        data = _base_analysis()
        _clean_suggested_requirements(data)
        assert "suggested_requirements" not in data or data.get("suggested_requirements") == {}

    def test_empty_dict_is_noop(self):
        data = _with_suggested({})
        _clean_suggested_requirements(data)
        assert data["suggested_requirements"] == {}

    def test_item_not_in_approved_is_kept(self):
        data = _with_suggested({"skills": ["HRIS systems", "Recruitment coordination"]})
        _clean_suggested_requirements(data)
        assert "HRIS systems" in data["suggested_requirements"]["skills"]
        assert "Recruitment coordination" in data["suggested_requirements"]["skills"]

    def test_item_already_in_required_skills_removed(self):
        """Microsoft Excel is in skills.required — must not appear in suggested."""
        data = _with_suggested({"skills": ["Microsoft Excel", "Tableau"]})
        _clean_suggested_requirements(data)
        assert "Microsoft Excel" not in data["suggested_requirements"].get("skills", [])
        assert "Tableau" in data["suggested_requirements"]["skills"]

    def test_item_already_in_preferred_skills_removed(self):
        data = _with_suggested({"skills": ["SAP", "Oracle HCM"]})
        _clean_suggested_requirements(data)
        assert "SAP" not in data["suggested_requirements"].get("skills", [])
        assert "Oracle HCM" in data["suggested_requirements"]["skills"]

    def test_item_already_in_domain_knowledge_removed(self):
        data = _with_suggested({"domain_knowledge": ["Labour law", "Employment contracts"]})
        _clean_suggested_requirements(data)
        assert "Labour law" not in data["suggested_requirements"].get("domain_knowledge", [])
        assert "Employment contracts" in data["suggested_requirements"]["domain_knowledge"]

    def test_item_already_in_certifications_removed(self):
        data = _with_suggested({"certifications": ["SHRM-CP", "CIPD Level 5"]})
        _clean_suggested_requirements(data)
        assert "SHRM-CP" not in data["suggested_requirements"].get("certifications", [])
        assert "CIPD Level 5" in data["suggested_requirements"]["certifications"]

    def test_item_in_other_requirements_removed(self):
        data = _with_suggested({"other": ["Proficiency in written English", "Analytical mindset"]})
        _clean_suggested_requirements(data)
        assert "Proficiency in written English" not in data["suggested_requirements"].get("other", [])
        assert "Analytical mindset" in data["suggested_requirements"]["other"]

    # ── non-scoreable filtering ────────────────────────────────────────────────

    def test_background_check_removed_from_suggested(self):
        data = _with_suggested({"other": ["Must pass background check", "Team collaboration"]})
        _clean_suggested_requirements(data)
        items = data["suggested_requirements"].get("other", [])
        assert not any("background check" in i.lower() for i in items)
        assert "Team collaboration" in items

    def test_reference_check_removed_from_suggested(self):
        data = _with_suggested({"other": ["Provide 2 professional references"]})
        _clean_suggested_requirements(data)
        assert data["suggested_requirements"].get("other") is None or \
               not any("reference" in i.lower() for i in data["suggested_requirements"].get("other", []))

    def test_right_to_work_removed_from_suggested(self):
        data = _with_suggested({"other": ["Must have right to work in UAE"]})
        _clean_suggested_requirements(data)
        assert not data["suggested_requirements"].get("other")

    def test_visa_removed_from_suggested(self):
        data = _with_suggested({"other": ["Visa sponsorship required", "Strong communication"]})
        _clean_suggested_requirements(data)
        others = data["suggested_requirements"].get("other", [])
        assert not any("visa" in i.lower() for i in others)
        assert "Strong communication" in others

    def test_immediate_start_removed_from_suggested(self):
        data = _with_suggested({"other": ["Must be available to start immediately"]})
        _clean_suggested_requirements(data)
        assert not data["suggested_requirements"].get("other")

    def test_work_permit_removed_from_suggested(self):
        data = _with_suggested({"other": ["Valid work permit required"]})
        _clean_suggested_requirements(data)
        assert not data["suggested_requirements"].get("other")

    # ── cross-category deduplication ──────────────────────────────────────────

    def test_cross_category_duplicate_removed(self):
        """Same phrase in two suggested categories → keep first occurrence only."""
        data = _with_suggested({
            "skills": ["Conflict resolution"],
            "soft_skills": ["Conflict resolution"],
        })
        _clean_suggested_requirements(data)
        in_skills = "Conflict resolution" in data["suggested_requirements"].get("skills", [])
        in_soft = "Conflict resolution" in data["suggested_requirements"].get("soft_skills", [])
        assert in_skills != in_soft  # exactly one copy survives
        assert in_skills  # first category wins

    def test_within_category_duplicate_removed(self):
        data = _with_suggested({"skills": ["Python scripting", "python scripting"]})
        _clean_suggested_requirements(data)
        assert len(data["suggested_requirements"].get("skills", [])) == 1

    # ── empty category pruning ────────────────────────────────────────────────

    def test_empty_category_removed(self):
        data = _with_suggested({
            "skills": ["Microsoft Excel"],   # will be removed (approved)
            "soft_skills": ["Resilience"],   # kept
        })
        _clean_suggested_requirements(data)
        assert "skills" not in data["suggested_requirements"]
        assert "soft_skills" in data["suggested_requirements"]

    def test_all_empty_categories_gives_empty_dict(self):
        data = _with_suggested({"skills": ["Microsoft Excel"]})  # all approved
        _clean_suggested_requirements(data)
        assert data["suggested_requirements"] == {}

    # ── case-insensitive matching ──────────────────────────────────────────────

    def test_approved_match_is_case_insensitive(self):
        data = _with_suggested({"skills": ["MICROSOFT EXCEL", "oracle hcm"]})
        _clean_suggested_requirements(data)
        items = data["suggested_requirements"].get("skills", [])
        assert not any("excel" in i.lower() for i in items)
        assert "oracle hcm" in [i.lower() for i in items]

    def test_non_scoreable_match_is_case_insensitive(self):
        data = _with_suggested({"other": ["MUST PASS BACKGROUND CHECK"]})
        _clean_suggested_requirements(data)
        assert not data["suggested_requirements"].get("other")

    # ── responsibility-derived competencies ───────────────────────────────────

    def test_hr_administration_kept(self):
        data = _with_suggested({"skills": ["HR administration"]})
        _clean_suggested_requirements(data)
        assert "HR administration" in data["suggested_requirements"].get("skills", [])

    def test_payroll_administration_kept(self):
        data = _with_suggested({"skills": ["Payroll administration"]})
        _clean_suggested_requirements(data)
        assert "Payroll administration" in data["suggested_requirements"].get("skills", [])

    def test_document_verification_kept(self):
        data = _with_suggested({"skills": ["Document verification"]})
        _clean_suggested_requirements(data)
        assert "Document verification" in data["suggested_requirements"].get("skills", [])

    def test_client_coordination_kept(self):
        data = _with_suggested({"skills": ["Client coordination"]})
        _clean_suggested_requirements(data)
        assert "Client coordination" in data["suggested_requirements"].get("skills", [])

    def test_stakeholder_coordination_kept(self):
        data = _with_suggested({"skills": ["Stakeholder coordination"]})
        _clean_suggested_requirements(data)
        assert "Stakeholder coordination" in data["suggested_requirements"].get("skills", [])

    def test_project_coordination_kept(self):
        data = _with_suggested({"skills": ["Project coordination"]})
        _clean_suggested_requirements(data)
        assert "Project coordination" in data["suggested_requirements"].get("skills", [])

    def test_records_management_kept(self):
        data = _with_suggested({"domain_knowledge": ["Records management"]})
        _clean_suggested_requirements(data)
        assert "Records management" in data["suggested_requirements"].get("domain_knowledge", [])

    def test_vendor_management_kept(self):
        data = _with_suggested({"domain_knowledge": ["Vendor management"]})
        _clean_suggested_requirements(data)
        assert "Vendor management" in data["suggested_requirements"].get("domain_knowledge", [])

    def test_user_support_kept_as_phrase(self):
        """'user support' is a meaningful phrase and must not be removed as a weak skill."""
        data = _with_suggested({"skills": ["User support"]})
        _clean_suggested_requirements(data)
        assert "User support" in data["suggested_requirements"].get("skills", [])

    def test_reporting_kept(self):
        data = _with_suggested({"skills": ["Reporting"]})
        _clean_suggested_requirements(data)
        assert "Reporting" in data["suggested_requirements"].get("skills", [])

    # ── malformed inputs ──────────────────────────────────────────────────────

    def test_non_list_category_skipped_gracefully(self):
        data = _with_suggested({"skills": None, "soft_skills": ["Resilience"]})
        _clean_suggested_requirements(data)
        assert "skills" not in data["suggested_requirements"]
        assert "Resilience" in data["suggested_requirements"].get("soft_skills", [])

    def test_empty_string_items_dropped(self):
        data = _with_suggested({"skills": ["", "  ", "HRIS systems"]})
        _clean_suggested_requirements(data)
        items = data["suggested_requirements"].get("skills", [])
        assert "" not in items
        assert "  " not in items
        assert "HRIS systems" in items

    def test_non_string_items_coerced_to_string(self):
        data = _with_suggested({"other": [42, "Strong negotiation"]})
        _clean_suggested_requirements(data)
        items = data["suggested_requirements"].get("other", [])
        assert "Strong negotiation" in items

    # ── idempotency ───────────────────────────────────────────────────────────

    def test_double_run_is_idempotent(self):
        data = _with_suggested({
            "skills": ["HRIS systems", "Oracle HCM"],
            "soft_skills": ["Resilience"],
        })
        _clean_suggested_requirements(data)
        snapshot = copy.deepcopy(data["suggested_requirements"])
        _clean_suggested_requirements(data)
        assert data["suggested_requirements"] == snapshot


# ─── _validate_criteria with suggested_requirements ───────────────────────────

class TestValidateCriteriaWithSuggested:

    def test_none_value_in_suggested_normalised_to_list(self):
        data = _base_analysis(suggested_requirements={"skills": None, "soft_skills": ["X"]})
        _validate_criteria(data)
        assert data["suggested_requirements"]["skills"] == []
        assert data["suggested_requirements"]["soft_skills"] == ["X"]

    def test_int_value_in_suggested_normalised_to_list(self):
        data = _base_analysis(suggested_requirements={"skills": 42})
        _validate_criteria(data)
        assert data["suggested_requirements"]["skills"] == []

    def test_valid_lists_unchanged_by_validate(self):
        data = _base_analysis(suggested_requirements={"skills": ["HRIS", "Oracle"]})
        _validate_criteria(data)
        assert data["suggested_requirements"]["skills"] == ["HRIS", "Oracle"]

    def test_no_suggested_key_validate_does_not_add_it(self):
        data = _base_analysis()
        _validate_criteria(data)
        assert "suggested_requirements" not in data

    def test_non_dict_suggested_value_left_as_is(self):
        """If suggested_requirements is a list (malformed), validate skips it."""
        data = _base_analysis(suggested_requirements=["item1", "item2"])
        _validate_criteria(data)
        assert data["suggested_requirements"] == ["item1", "item2"]


# ─── flatten_criteria_for_scoring excludes suggested ──────────────────────────

class TestFlattenExcludesSuggested:

    def test_suggested_not_in_flattened_skills(self):
        data = _with_suggested({"skills": ["Oracle HCM", "Workday"]})
        flat = flatten_criteria_for_scoring(data)
        for item in ["Oracle HCM", "Workday"]:
            assert item not in flat["skills"]

    def test_suggested_not_in_flattened_experience(self):
        data = _with_suggested({"experience": ["Talent acquisition experience"]})
        flat = flatten_criteria_for_scoring(data)
        assert "Talent acquisition experience" not in flat["experience"]

    def test_suggested_not_in_flattened_certifications(self):
        data = _with_suggested({"certifications": ["CIPD Level 5"]})
        flat = flatten_criteria_for_scoring(data)
        assert "CIPD Level 5" not in flat["certifications"]

    def test_suggested_not_in_flattened_domain_knowledge(self):
        data = _with_suggested({"domain_knowledge": ["HR analytics", "Workforce planning"]})
        flat = flatten_criteria_for_scoring(data)
        for item in ["HR analytics", "Workforce planning"]:
            assert item not in flat["domain_knowledge"]

    def test_suggested_not_in_any_flat_field(self):
        data = _with_suggested({
            "skills": ["Power BI"],
            "soft_skills": ["Resilience"],
            "domain_knowledge": ["Compensation benchmarking"],
        })
        flat = flatten_criteria_for_scoring(data)
        all_values: list[str] = []
        for v in flat.values():
            if isinstance(v, list):
                all_values.extend(v)
        assert "Power BI" not in all_values
        assert "Resilience" not in all_values
        assert "Compensation benchmarking" not in all_values

    def test_weights_unaffected_by_suggested(self):
        data = _with_suggested({"skills": ["Oracle HCM"] * 20})
        flat = flatten_criteria_for_scoring(data)
        total = sum(flat[k] for k in [
            "weight_skills", "weight_experience", "weight_education",
            "weight_certifications", "weight_soft_skills",
            "weight_domain_knowledge", "weight_other",
        ])
        assert total == 100

    def test_without_suggested_flatten_unchanged(self):
        data = _base_analysis()
        flat_with = flatten_criteria_for_scoring({**data, "suggested_requirements": {"skills": ["Workday"]}})
        flat_without = flatten_criteria_for_scoring(data)
        for key in flat_without:
            assert flat_with[key] == flat_without[key]


# ─── backward compatibility ───────────────────────────────────────────────────

class TestBackwardCompatibility:

    def test_old_analysis_json_without_suggested_still_valid(self):
        data = _base_analysis()
        assert "suggested_requirements" not in data
        _clean_job_analysis(data)
        # must not raise and must not inject the key
        assert "suggested_requirements" not in data

    def test_old_analysis_json_flattens_normally(self):
        data = _base_analysis()
        flat = flatten_criteria_for_scoring(data)
        assert flat["skills"]
        assert flat["weight_skills"] == 35

    def test_clean_job_analysis_without_suggested_is_noop_on_suggested(self):
        data = _base_analysis()
        _clean_job_analysis(data)
        assert "suggested_requirements" not in data

    def test_analysis_json_with_suggested_passes_clean_job_analysis(self):
        data = _with_suggested({
            "skills": ["HRIS systems", "Microsoft Excel"],  # Excel will be removed (approved)
            "other": ["Background check required"],          # will be removed (non-scoreable)
        })
        _clean_job_analysis(data)
        sugg = data.get("suggested_requirements", {})
        items = sugg.get("skills", [])
        assert "HRIS systems" in items
        assert "Microsoft Excel" not in items
        assert not sugg.get("other")


# ─── _clean_job_analysis integration ─────────────────────────────────────────

class TestCleanJobAnalysisIntegrationSuggested:

    def test_pipeline_end_to_end_with_suggested(self):
        """Realistic HR/Admin job: suggested cleans correctly alongside main criteria."""
        data = {
            "skills": {
                "required": ["Microsoft Excel", "HR software", "Data Entry"],
                "preferred": ["SAP SuccessFactors"],
            },
            "soft_skills": {"required": ["Attention to detail", "Confidentiality"], "preferred": []},
            "experience": {
                "minimum_years": 3,
                "relevant_roles": ["HR Administrator", "People Operations"],
                "key_responsibilities": ["Payroll coordination", "Onboarding"],
            },
            "education": {"minimum_level": "Bachelor's", "fields_of_study": ["Human Resources"]},
            "certifications": [],
            "domain_knowledge": ["Labour law", "Payroll processing"],
            "other_requirements": ["Must pass background check", "Excellent communication"],
            "scoring_weights": {
                "skills": 35, "experience": 30, "education": 10,
                "certifications": 5, "soft_skills": 10,
                "domain_knowledge": 10, "other_requirements": 0,
            },
            "suggested_requirements": {
                "skills": [
                    "HR administration",          # valid competency → kept
                    "Payroll administration",      # valid competency → kept
                    "Document verification",       # valid competency → kept
                    "Microsoft Excel",             # already in approved → removed
                    "HR software",                 # already in approved → removed
                ],
                "soft_skills": [
                    "Attention to detail",         # already in soft_skills → removed
                    "Discretion",                  # not in approved → kept
                ],
                "other": [
                    "Must have right to work",     # non-scoreable → removed
                    "Records management",          # valid competency → kept
                ],
                "certifications": [
                    "CIPD Level 3",                # not in approved → kept
                ],
            },
        }

        _clean_job_analysis(data)

        sugg = data.get("suggested_requirements", {})

        # approved duplicates removed
        assert "Microsoft Excel" not in sugg.get("skills", [])
        assert "HR software" not in sugg.get("skills", [])
        assert "Attention to detail" not in sugg.get("soft_skills", [])

        # non-scoreable removed
        other = sugg.get("other", [])
        assert not any("right to work" in i.lower() for i in other)

        # responsibility-derived competencies kept
        assert "HR administration" in sugg.get("skills", [])
        assert "Payroll administration" in sugg.get("skills", [])
        assert "Document verification" in sugg.get("skills", [])
        assert "Discretion" in sugg.get("soft_skills", [])
        assert "Records management" in sugg.get("other", [])
        assert "CIPD Level 3" in sugg.get("certifications", [])

        # background check in other_requirements is sanitised by _sanitise_classification
        # (called before _clean_job_analysis in the real pipeline — here we check main criteria)
        # scoring unaffected
        flat = flatten_criteria_for_scoring(data)
        all_flat: list[str] = []
        for v in flat.values():
            if isinstance(v, list):
                all_flat.extend(v)
        assert "HR administration" not in all_flat
        assert "CIPD Level 3" not in all_flat

    def test_weights_still_sum_to_100_with_suggested_present(self):
        data = _with_suggested({"skills": ["Workday", "Oracle HCM"] * 10})
        _clean_job_analysis(data)
        weights = data.get("scoring_weights", {})
        assert sum(weights.values()) == 100

    def test_suggested_survives_dedup_pass(self):
        """_dedup_requirements must not touch suggested_requirements."""
        data = _with_suggested({"skills": ["HRIS systems"]})
        # Add same item to main skills to test they don't cross-pollinate
        data["skills"]["preferred"].append("HRIS systems")
        _clean_job_analysis(data)
        # HRIS systems is now in approved → removed from suggested
        assert "HRIS systems" not in data["suggested_requirements"].get("skills", [])
        # But it's still in main skills.preferred
        assert "HRIS systems" in data["skills"]["preferred"]


# ─── Realistic HR/Admin before-after example ─────────────────────────────────

class TestRealisticHRAdminExample:
    """
    Validates the complete transformation for a realistic HR/Admin + Client Coordination job.

    Raw LLM output (before):
      - skills.required: typical HR tools
      - suggested_requirements: mix of competencies, duplicates, non-scoreable items
      - other_requirements: includes a background check (handled by _sanitise_classification)

    After _clean_job_analysis:
      - duplicates and non-scoreable items removed from suggested
      - responsibility-derived competencies preserved
      - scoring/weights unchanged
    """

    def _raw_llm_output(self) -> dict:
        return {
            "skills": {
                "required": [
                    "Microsoft Word", "Microsoft Excel", "Document management systems",
                ],
                "preferred": ["HRIS software"],
            },
            "soft_skills": {
                "required": ["Client communication", "Discretion"],
                "preferred": [],
            },
            "experience": {
                "minimum_years": 2,
                "relevant_roles": ["HR Coordinator", "Administrative Assistant"],
                "key_responsibilities": [
                    "Coordinating onboarding", "Maintaining employee records",
                ],
            },
            "education": {
                "minimum_level": "Bachelor's",
                "fields_of_study": ["Human Resources", "Business Administration"],
            },
            "certifications": [],
            "domain_knowledge": ["HR policies", "Recruitment processes"],
            "other_requirements": ["Strong attention to detail"],
            "scoring_weights": {
                "skills": 35, "experience": 30, "education": 10,
                "certifications": 0, "soft_skills": 10,
                "domain_knowledge": 15, "other_requirements": 0,
            },
            "suggested_requirements": {
                "skills": [
                    "HR administration",           # competency from responsibilities → keep
                    "Document verification",        # competency from responsibilities → keep
                    "Client coordination",          # competency from responsibilities → keep
                    "Microsoft Excel",              # duplicate of approved → remove
                    "HRIS software",               # duplicate of approved → remove
                ],
                "soft_skills": [
                    "Stakeholder coordination",    # competency → keep
                    "Client communication",        # duplicate of soft_skills.required → remove
                    "Confidentiality",             # competency → keep
                ],
                "domain_knowledge": [
                    "Records management",          # competency → keep
                    "HR policies",                 # duplicate of domain_knowledge → remove
                    "Vendor management",           # competency → keep
                ],
                "certifications": [
                    "CIPD Level 3",               # not in approved → keep
                    "SHRM-CP",                    # not in approved → keep (certs is empty)
                ],
                "other": [
                    "Must have right to work in UAE",     # non-scoreable → remove
                    "Must pass background check",          # non-scoreable → remove
                    "Available to start immediately",      # non-scoreable → remove
                    "Reporting skills",                    # competency → keep
                ],
            },
        }

    def test_raw_llm_output_cleaned_correctly(self):
        data = self._raw_llm_output()
        _clean_job_analysis(data)

        sugg = data.get("suggested_requirements", {})

        # ── skills ──────────────────────────────────────────────────────────
        assert "HR administration" in sugg.get("skills", [])
        assert "Document verification" in sugg.get("skills", [])
        assert "Client coordination" in sugg.get("skills", [])
        assert "Microsoft Excel" not in sugg.get("skills", [])
        assert "HRIS software" not in sugg.get("skills", [])

        # ── soft_skills ──────────────────────────────────────────────────────
        assert "Stakeholder coordination" in sugg.get("soft_skills", [])
        assert "Confidentiality" in sugg.get("soft_skills", [])
        assert "Client communication" not in sugg.get("soft_skills", [])

        # ── domain_knowledge ─────────────────────────────────────────────────
        assert "Records management" in sugg.get("domain_knowledge", [])
        assert "Vendor management" in sugg.get("domain_knowledge", [])
        assert "HR policies" not in sugg.get("domain_knowledge", [])

        # ── certifications ───────────────────────────────────────────────────
        assert "CIPD Level 3" in sugg.get("certifications", [])
        assert "SHRM-CP" in sugg.get("certifications", [])

        # ── other ────────────────────────────────────────────────────────────
        others = sugg.get("other", [])
        assert "Reporting skills" in others
        assert not any("right to work" in i.lower() for i in others)
        assert not any("background check" in i.lower() for i in others)
        assert not any("immediately" in i.lower() for i in others)

    def test_scoring_unaffected_by_suggested(self):
        data = self._raw_llm_output()
        flat_before = flatten_criteria_for_scoring(copy.deepcopy(data))

        _clean_job_analysis(data)
        flat_after = flatten_criteria_for_scoring(data)

        assert flat_before == flat_after

    def test_weights_sum_to_100_after_clean(self):
        data = self._raw_llm_output()
        _clean_job_analysis(data)
        assert sum(data["scoring_weights"].values()) == 100

    def test_suggested_absent_from_flat_criteria(self):
        data = self._raw_llm_output()
        _clean_job_analysis(data)
        flat = flatten_criteria_for_scoring(data)

        all_flat: list[str] = []
        for v in flat.values():
            if isinstance(v, list):
                all_flat.extend(str(x) for x in v)

        for should_be_absent in [
            "HR administration", "Document verification", "Client coordination",
            "Stakeholder coordination", "Records management", "Vendor management",
            "CIPD Level 3", "SHRM-CP", "Reporting skills",
        ]:
            assert should_be_absent not in all_flat, (
                f"Suggested item '{should_be_absent}' leaked into flat scoring criteria"
            )
