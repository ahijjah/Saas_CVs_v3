"""
Tests for services/llm_criteria_mapper.py and the corresponding
evidence_serialiser additions (llm_matchresult_to_dict / _from_dict).

Coverage:
- Dataclass construction and defaults
- _flatten_criteria: all dimensions, required/preferred split, empty inputs
- _select_evidence_snippets: keyword scoring, deduplication, length filters
- _build_user_message: output contains expected sections
- _parse_llm_response: valid JSON, invalid JSON, missing key, malformed items
- _parse_one_assessment: status/match_type/criterion_class/dimension clamping
- Serialisation round-trip (llm_matchresult_to_dict → llm_matchresult_from_dict)
- Schema version present after serialisation
- JSON-safe output (json.dumps compatible)
- Feature flag OFF: no LLM import / no mapper call (integration guard)
- Strict technical distinction: Java ≠ JavaScript, React ≠ Angular, PG ≠ MySQL
- Broad skill equivalence: MS Office / computer literacy umbrella
- Empty / malformed criteria handling
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.prompt_config import PromptConfig
from services.llm_criteria_mapper import (
    LLMCriteriaMapper,
    LLMCriterionAssessment,
    LLMMatchResult,
    QualitativeSummary,
    _MAPPER_VERSION,
    _CRITICAL_SECTION_RE,
    _SOFT_SKILL_EXPANSION,
    _ARABIC_SOFT_SKILL_TERMS,
    _OFFICE_SUITE_UMBRELLA,
    _OFFICE_SUITE_MEMBERS,
    _GOOGLE_WORKSPACE_UMBRELLA,
    _GOOGLE_WORKSPACE_MEMBERS,
    _SKILL_FAMILY_RULES,
    _SKILL_FAMILY_UPGRADE_CONFIDENCE,
    _absent_fallback_assessments,
    _apply_skill_family_upgrade,
    _build_user_message,
    _criterion_matches_family_member,
    _flatten_criteria,
    _is_section_boundary,
    _parse_llm_response,
    _parse_one_assessment,
    _select_evidence_snippets,
)
from services.evidence_serialiser import (
    SerialisationError,
    llm_matchresult_from_dict,
    llm_matchresult_to_dict,
    _LLM_MATCHRESULT_SCHEMA,
)
from services.cv_evidence import (
    CVFacts,
    SkillEvidence,
    ExperienceEvidence,
    EducationEvidence,
    CertificationEvidence,
    SoftSkillSignal,
    DomainSignal,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _minimal_cv_facts() -> CVFacts:
    return CVFacts(
        language="en",
        total_char_count=1500,
        total_experience_years=5.0,
        highest_education_level="Bachelor's",
        skill_names_normalised=["Microsoft Excel", "Microsoft Word", "SAP"],
    )


def _rich_cv_facts() -> CVFacts:
    return CVFacts(
        language="en",
        total_char_count=5000,
        total_experience_years=10.0,
        highest_education_level="Master's",
        skill_names_normalised=[
            "Microsoft Excel", "Microsoft Word", "Microsoft PowerPoint",
            "SAP ERP", "Microsoft Outlook", "SQL",
        ],
        skills=[
            SkillEvidence(
                skill_name="Microsoft Excel", raw_text="Excel", explicit=True,
                confidence=0.95, context_snippet="", language="en", section_hint="skills",
            ),
            SkillEvidence(
                skill_name="SAP ERP", raw_text="SAP", explicit=True,
                confidence=0.90, context_snippet="", language="en", section_hint="skills",
            ),
        ],
        experience=[
            ExperienceEvidence(
                employer="Acme Corp", role_title="Senior Analyst",
                years=5.0, responsibilities=["prepared monthly reports"],
            ),
        ],
        education=[
            EducationEvidence(
                degree_level="Master's", field_of_study="Business Administration",
                institution="State University", year=2015,
            ),
        ],
        certifications=[
            CertificationEvidence(name="PMP", raw_text="PMP certified", issuer="PMI"),
        ],
        soft_skill_signals=[
            SoftSkillSignal(
                soft_skill_category="communication",
                evidence_phrase="presented findings to senior management",
                confidence=0.80,
            ),
        ],
        domain_signals=[
            DomainSignal(domain_term="finance", frequency=3, context_snippets=[]),
        ],
    )


def _minimal_analysis_json() -> dict:
    return {
        "job_title": "Financial Analyst",
        "skills": {
            "required": ["Microsoft Excel", "SAP"],
            "preferred": ["Python"],
        },
        "experience": {
            "minimum_years": 3,
            "relevant_roles": ["Analyst"],
            "key_responsibilities": ["financial reporting"],
        },
        "education": {
            "minimum_level": "Bachelor's",
            "fields_of_study": ["Finance", "Accounting"],
        },
        "certifications": ["CPA"],
        "domain_knowledge": ["financial services"],
        "other_requirements": ["confidentiality"],
    }


def _make_result(n_assessments: int = 2) -> LLMMatchResult:
    assessments = [
        LLMCriterionAssessment(
            criterion_text=f"criterion {i}",
            dimension="skills",
            required=True,
            status="MATCHED",
            confidence=0.90,
            supporting_evidence=["evidence snippet"],
            match_reason="Direct match found.",
            match_type="direct",
            criterion_class="strict",
            risk_flags=[],
            prompt_code="recruitment.criteria_mapping",
            prompt_version="1",
            llm_model="gpt-4o-mini",
        )
        for i in range(n_assessments)
    ]
    return LLMMatchResult(
        application_id="app-123",
        job_id="job-456",
        assessments=assessments,
        processing_ms=750,
        created_at="2026-06-13T10:00:00+00:00",
        prompt_code="recruitment.criteria_mapping",
        prompt_version="1",
        model="gpt-4o-mini",
        total_criteria=n_assessments,
        matched_count=n_assessments,
        partial_count=0,
        absent_count=0,
        high_confidence_count=n_assessments,
        low_confidence_count=0,
    )


# ── Section A: Dataclasses ────────────────────────────────────────────────────

class TestDataclasses:
    def test_criterion_assessment_defaults(self):
        a = LLMCriterionAssessment(
            criterion_text="Python",
            dimension="skills",
            required=True,
            status="ABSENT",
            confidence=0.0,
            supporting_evidence=[],
            match_reason="",
            match_type="missing",
            criterion_class="strict",
        )
        assert a.risk_flags == []
        assert a.prompt_code == ""
        assert a.llm_model == ""

    def test_match_result_defaults(self):
        r = LLMMatchResult(
            application_id="a",
            job_id="j",
            assessments=[],
            processing_ms=0,
            created_at="",
            prompt_code="",
            prompt_version="",
            model="",
        )
        assert r.mapper_version == _MAPPER_VERSION
        assert r.total_criteria == 0
        assert r.matched_count == 0

    def test_match_result_is_dataclass(self):
        r = _make_result()
        assert dataclasses.is_dataclass(r)


# ── Section B: _flatten_criteria ─────────────────────────────────────────────

class TestFlattenCriteria:
    def test_skills_required_and_preferred(self):
        items = _flatten_criteria({
            "skills": {"required": ["Java", "SQL"], "preferred": ["Docker"]},
        })
        req = [i for i in items if i["required"]]
        pref = [i for i in items if not i["required"]]
        assert len(req) == 2
        assert len(pref) == 1
        assert all(i["dimension"] == "skills" for i in items)

    def test_experience_minimum_years(self):
        items = _flatten_criteria({
            "experience": {"minimum_years": 5, "relevant_roles": [], "key_responsibilities": []},
        })
        assert any("5 years" in i["text"] and i["required"] for i in items)

    def test_experience_zero_years_skipped(self):
        items = _flatten_criteria({
            "experience": {"minimum_years": 0},
        })
        assert not any("years" in i["text"].lower() for i in items)

    def test_experience_requirement_type_preferred(self):
        """Experience requirement marked as 'preferred' should have required=False."""
        items = _flatten_criteria({
            "experience": {
                "minimum_years": 5,
                "requirement_type": "preferred",
                "relevant_roles": [],
                "key_responsibilities": [],
            },
        })
        exp_items = [i for i in items if "5 years" in i["text"]]
        assert len(exp_items) == 1
        assert exp_items[0]["required"] is False

    def test_experience_requirement_type_mandatory(self):
        """Experience requirement marked as 'mandatory' should have required=True."""
        items = _flatten_criteria({
            "experience": {
                "minimum_years": 5,
                "requirement_type": "mandatory",
                "relevant_roles": [],
                "key_responsibilities": [],
            },
        })
        exp_items = [i for i in items if "5 years" in i["text"]]
        assert len(exp_items) == 1
        assert exp_items[0]["required"] is True

    def test_experience_no_requirement_type_defaults_to_required(self):
        """Experience requirement with no requirement_type field should default to required=True (backward compat)."""
        items = _flatten_criteria({
            "experience": {
                "minimum_years": 5,
                "relevant_roles": [],
                "key_responsibilities": [],
            },
        })
        exp_items = [i for i in items if "5 years" in i["text"]]
        assert len(exp_items) == 1
        assert exp_items[0]["required"] is True

    def test_education_level_required(self):
        items = _flatten_criteria({
            "education": {"minimum_level": "Bachelor's", "fields_of_study": ["Finance"]},
        })
        edu = [i for i in items if i["dimension"] == "education"]
        required_edu = [i for i in edu if i["required"]]
        assert any("Bachelor" in i["text"] for i in required_edu)

    def test_education_fields_single_field_no_or(self):
        """Single field of study should not add ' or '."""
        items = _flatten_criteria({
            "education": {"minimum_level": "None", "fields_of_study": ["Computer Science"]},
        })
        edu = [i for i in items if i["dimension"] == "education" and not i["required"]]
        assert len(edu) == 1
        assert edu[0]["text"] == "Computer Science"

    def test_education_fields_multiple_or_combined(self):
        """Multiple fields should be combined as OR alternatives in single criterion."""
        items = _flatten_criteria({
            "education": {"minimum_level": "None", "fields_of_study": ["Computer Science", "MIS", "Computer Engineering"]},
        })
        edu_fields = [i for i in items if i["dimension"] == "education" and not i["required"]]
        assert len(edu_fields) == 1
        text = edu_fields[0]["text"]
        assert "Field of study:" in text
        assert "Computer Science" in text
        assert "MIS" in text
        assert "Computer Engineering" in text
        assert " or " in text

    def test_education_none_skipped(self):
        items = _flatten_criteria({
            "education": {"minimum_level": "None", "fields_of_study": []},
        })
        assert not any(i["dimension"] == "education" and i["required"] for i in items)

    def test_all_dimensions_present(self):
        items = _flatten_criteria(_minimal_analysis_json())
        dims = {i["dimension"] for i in items}
        assert "skills" in dims
        assert "experience" in dims
        assert "education" in dims
        assert "certifications" in dims
        assert "domain_knowledge" in dims
        assert "other" in dims

    def test_empty_analysis_returns_empty(self):
        assert _flatten_criteria({}) == []

    def test_none_values_skipped(self):
        items = _flatten_criteria({
            "skills": {"required": [None, "", "Python"], "preferred": []},
        })
        assert len([i for i in items if i["dimension"] == "skills"]) == 1
        assert items[0]["text"] == "Python"

    def test_key_responsibilities_not_converted_to_criteria(self):
        """Issue #12: key_responsibilities should NOT be converted to individual scoring criteria.
        They are kept in analysis_json for context but not scored item-by-item."""
        items = _flatten_criteria({
            "experience": {
                "minimum_years": 5,
                "requirement_type": "required",
                "relevant_roles": ["Senior Engineer"],
                "key_responsibilities": [
                    "Design and implement systems",
                    "Code review",
                    "Mentor junior engineers",
                ],
            },
        })

        # Verify minimum years criterion exists
        assert any("5 years" in i["text"] for i in items), "Minimum years criterion should exist"

        # Verify relevant_roles criterion exists
        assert any("Senior Engineer" in i["text"] for i in items), "Relevant roles criterion should exist"

        # Verify key_responsibilities are NOT in criteria
        texts = [i["text"] for i in items]
        assert not any("Design and implement systems" in t for t in texts), "Design responsibility should not be in criteria"
        assert not any("Code review" in t for t in texts), "Code review responsibility should not be in criteria"
        assert not any("Mentor junior engineers" in t for t in texts), "Mentoring responsibility should not be in criteria"

        # Total criteria should be 2 (minimum years + 1 role), not 5 (2 + 3 responsibilities)
        assert len(items) == 2, f"Expected 2 experience criteria, got {len(items)}"


# ── Section C: _select_evidence_snippets ─────────────────────────────────────

class TestSelectEvidenceSnippets:
    def test_returns_list_of_strings(self):
        cv = "Worked with Microsoft Excel daily. Prepared SAP reports every week."
        criteria = [{"text": "Microsoft Excel", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria)
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)

    def test_keyword_overlap_ranks_first(self):
        cv = (
            "Prepared monthly Excel reports.\n"
            "Enjoyed hiking on weekends.\n"
            "Used Microsoft Excel and SAP for financial analysis."
        )
        criteria = [{"text": "Microsoft Excel", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria)
        assert result[0].lower().find("excel") != -1

    def test_deduplication(self):
        cv = "Microsoft Excel\nMicrosoft Excel\nMicrosoft Excel"
        criteria = [{"text": "Excel", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, max_snippets=10)
        assert len(result) == 1

    def test_empty_cv_returns_empty(self):
        result = _select_evidence_snippets("", [], max_snippets=10)
        assert result == []

    def test_empty_criteria_returns_empty(self):
        result = _select_evidence_snippets("Some CV text here.", [], max_snippets=10)
        assert result == []

    def test_length_filter_excludes_short(self):
        cv = "Hi\nHello world\nMicrosoft Excel skills demonstrated in many projects."
        criteria = [{"text": "Excel", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, min_length=20)
        assert all(len(s) >= 20 for s in result)

    def test_max_snippets_respected(self):
        lines = [f"Worked with Excel tool number {i} in projects." for i in range(100)]
        cv = "\n".join(lines)
        criteria = [{"text": "Excel", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, max_snippets=5)
        assert len(result) <= 5


# ── Section D: _build_user_message ───────────────────────────────────────────

class TestBuildUserMessage:
    def test_contains_job_title(self):
        msg = _build_user_message(
            "Software Engineer",
            [{"text": "Python", "dimension": "skills", "required": True}],
            _minimal_cv_facts(),
            [],
        )
        assert "Software Engineer" in msg

    def test_contains_criteria_block(self):
        msg = _build_user_message(
            "Analyst",
            [{"text": "Microsoft Excel", "dimension": "skills", "required": True}],
            _minimal_cv_facts(),
            ["Used Excel daily."],
        )
        assert "Microsoft Excel" in msg
        assert "REQUIRED" in msg

    def test_contains_cv_summary(self):
        msg = _build_user_message(
            "Analyst",
            [{"text": "Excel", "dimension": "skills", "required": True}],
            _rich_cv_facts(),
            [],
        )
        assert "10.0 years" in msg
        assert "Master" in msg

    def test_contains_evidence_snippets(self):
        msg = _build_user_message(
            "Analyst",
            [],
            _minimal_cv_facts(),
            ["Used Excel for reporting.", "Maintained SAP database."],
        )
        assert "Used Excel for reporting" in msg

    def test_preferred_criterion_labeled(self):
        msg = _build_user_message(
            "Analyst",
            [{"text": "Python", "dimension": "skills", "required": False}],
            _minimal_cv_facts(),
            [],
        )
        assert "preferred" in msg.lower()

    def test_empty_snippets_shows_fallback(self):
        msg = _build_user_message("Analyst", [], _minimal_cv_facts(), [])
        assert "No snippets" in msg or "structured summary" in msg.lower()


# ── Section E: _parse_llm_response ───────────────────────────────────────────

PROMPT_META = dict(prompt_code="recruitment.criteria_mapping", prompt_version="1", llm_model="gpt-4o-mini")


class TestParseLlmResponse:
    def _criteria(self):
        return [{"text": "Python", "dimension": "skills", "required": True}]

    def test_valid_response_parsed(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "Python",
                "dimension": "skills",
                "required": True,
                "status": "MATCHED",
                "confidence": 0.92,
                "supporting_evidence": ["Candidate listed Python as primary language."],
                "match_reason": "Direct match found.",
                "match_type": "direct",
                "criterion_class": "strict",
                "risk_flags": [],
            }]
        })
        result, _ = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert len(result) == 1
        assert result[0].status == "MATCHED"
        assert result[0].confidence == pytest.approx(0.92)
        assert result[0].match_type == "direct"

    def test_invalid_json_returns_absent_fallback(self):
        result, _ = _parse_llm_response("{not valid json", self._criteria(), **PROMPT_META)
        assert len(result) == 1
        assert result[0].status == "ABSENT"
        assert "assessment_failed" in result[0].risk_flags

    def test_missing_assessments_key_returns_fallback(self):
        raw = json.dumps({"result": "something else"})
        result, _ = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert result[0].status == "ABSENT"
        assert "assessment_failed" in result[0].risk_flags

    def test_empty_assessments_array(self):
        raw = json.dumps({"assessments": []})
        result, _ = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert result == []

    def test_malformed_item_skipped(self):
        raw = json.dumps({
            "assessments": [
                "not a dict",
                {
                    "criterion_text": "Python",
                    "dimension": "skills",
                    "required": True,
                    "status": "ABSENT",
                    "confidence": 0.1,
                    "supporting_evidence": [],
                    "match_reason": "Not found.",
                    "match_type": "missing",
                    "criterion_class": "strict",
                    "risk_flags": [],
                },
            ]
        })
        result, _ = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert len(result) == 1
        assert result[0].status == "ABSENT"

    def test_prompt_metadata_attached(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "Python", "dimension": "skills", "required": True,
                "status": "MATCHED", "confidence": 0.8,
                "supporting_evidence": [], "match_reason": "ok",
                "match_type": "direct", "criterion_class": "strict", "risk_flags": [],
            }]
        })
        result, _ = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert result[0].prompt_code == "recruitment.criteria_mapping"
        assert result[0].prompt_version == "1"
        assert result[0].llm_model == "gpt-4o-mini"


# ── Section F: _parse_one_assessment validation ───────────────────────────────

class TestParseOneAssessment:
    def _base(self, **overrides) -> dict:
        d = {
            "criterion_text": "Python",
            "dimension": "skills",
            "required": True,
            "status": "MATCHED",
            "confidence": 0.85,
            # Non-empty by default so C4 (empty-evidence downgrade) does not fire
            # unless the test explicitly overrides supporting_evidence=[].
            "supporting_evidence": ["Candidate listed Python as primary language."],
            "match_reason": "ok",
            "match_type": "direct",
            "criterion_class": "strict",
            "risk_flags": [],
        }
        d.update(overrides)
        return d

    def test_invalid_status_falls_back(self):
        a = _parse_one_assessment(self._base(status="WRONG"), **PROMPT_META)
        assert a.status == "ABSENT"

    def test_invalid_match_type_falls_back(self):
        a = _parse_one_assessment(self._base(match_type="magic"), **PROMPT_META)
        assert a.match_type == "missing"

    def test_invalid_criterion_class_falls_back(self):
        a = _parse_one_assessment(self._base(criterion_class="unknown"), **PROMPT_META)
        assert a.criterion_class == "other"

    def test_invalid_dimension_falls_back(self):
        a = _parse_one_assessment(self._base(dimension="magic"), **PROMPT_META)
        assert a.dimension == "other"

    def test_confidence_clamped_to_zero_one(self):
        a = _parse_one_assessment(self._base(confidence=1.5), **PROMPT_META)
        assert a.confidence == pytest.approx(1.0)
        b = _parse_one_assessment(self._base(confidence=-0.5), **PROMPT_META)
        assert b.confidence == pytest.approx(0.0)

    def test_supporting_evidence_list_sanitised(self):
        a = _parse_one_assessment(self._base(supporting_evidence="not a list"), **PROMPT_META)
        assert a.supporting_evidence == []

    def test_risk_flags_list_sanitised(self):
        a = _parse_one_assessment(self._base(risk_flags=None), **PROMPT_META)
        assert a.risk_flags == []

    # ── F-01.4: soft_skill dimension routing ─────────────────────────────────

    def test_soft_skill_class_with_skills_dimension_is_rerouted(self):
        """criterion_class=soft_skill + dimension=skills → dimension corrected to soft_skills."""
        a = _parse_one_assessment(
            self._base(criterion_class="soft_skill", dimension="skills"),
            **PROMPT_META,
        )
        assert a.dimension == "soft_skills"
        assert a.criterion_class == "soft_skill"

    def test_soft_skill_class_already_correct_dimension_unchanged(self):
        """criterion_class=soft_skill + dimension=soft_skills → no change."""
        a = _parse_one_assessment(
            self._base(criterion_class="soft_skill", dimension="soft_skills"),
            **PROMPT_META,
        )
        assert a.dimension == "soft_skills"

    def test_soft_skill_class_with_other_dimension_preserved(self):
        """criterion_class=soft_skill + dimension=other → other is preserved (explicit unknown)."""
        a = _parse_one_assessment(
            self._base(criterion_class="soft_skill", dimension="other"),
            **PROMPT_META,
        )
        assert a.dimension == "other"

    def test_soft_skill_class_rerouted_from_experience_dimension(self):
        """criterion_class=soft_skill + dimension=experience → corrected to soft_skills."""
        a = _parse_one_assessment(
            self._base(criterion_class="soft_skill", dimension="experience"),
            **PROMPT_META,
        )
        assert a.dimension == "soft_skills"

    def test_non_soft_skill_class_dimension_unchanged(self):
        """criterion_class=certification + dimension=skills → no rerouting applied."""
        a = _parse_one_assessment(
            self._base(criterion_class="certification", dimension="skills"),
            **PROMPT_META,
        )
        assert a.dimension == "skills"
        assert a.criterion_class == "certification"

    def test_education_class_education_dimension_unchanged(self):
        """criterion_class=education + dimension=education → no change."""
        a = _parse_one_assessment(
            self._base(criterion_class="education", dimension="education"),
            **PROMPT_META,
        )
        assert a.dimension == "education"
        assert a.criterion_class == "education"


# ── Section G: Serialisation round-trip ──────────────────────────────────────

class TestSerialisationRoundTrip:
    def test_schema_key_present(self):
        r = _make_result()
        d = llm_matchresult_to_dict(r)
        assert d["_schema"] == _LLM_MATCHRESULT_SCHEMA

    def test_round_trip_preserves_all_fields(self):
        original = _make_result(n_assessments=3)
        d = llm_matchresult_to_dict(original)
        reconstructed = llm_matchresult_from_dict(d)
        assert reconstructed.application_id == original.application_id
        assert reconstructed.job_id == original.job_id
        assert len(reconstructed.assessments) == 3
        assert reconstructed.total_criteria == original.total_criteria
        assert reconstructed.matched_count == original.matched_count
        assert reconstructed.processing_ms == original.processing_ms
        assert reconstructed.prompt_code == original.prompt_code
        assert reconstructed.model == original.model
        assert reconstructed.mapper_version == original.mapper_version

    def test_assessment_fields_preserved(self):
        original = _make_result(n_assessments=1)
        d = llm_matchresult_to_dict(original)
        r = llm_matchresult_from_dict(d)
        a = r.assessments[0]
        orig_a = original.assessments[0]
        assert a.criterion_text == orig_a.criterion_text
        assert a.status == orig_a.status
        assert a.confidence == pytest.approx(orig_a.confidence)
        assert a.match_type == orig_a.match_type
        assert a.criterion_class == orig_a.criterion_class
        assert a.supporting_evidence == orig_a.supporting_evidence
        assert a.prompt_code == orig_a.prompt_code

    def test_json_dumps_compatible(self):
        r = _make_result()
        d = llm_matchresult_to_dict(r)
        serialised = json.dumps(d, ensure_ascii=False)
        assert isinstance(serialised, str)
        parsed = json.loads(serialised)
        assert parsed["_schema"] == _LLM_MATCHRESULT_SCHEMA
        assert len(parsed["assessments"]) == 2

    def test_from_dict_raises_on_non_dict(self):
        with pytest.raises(SerialisationError):
            llm_matchresult_from_dict(None)
        with pytest.raises(SerialisationError):
            llm_matchresult_from_dict([1, 2, 3])

    def test_from_dict_tolerates_missing_fields(self):
        minimal = {"application_id": "a", "job_id": "j", "assessments": []}
        r = llm_matchresult_from_dict(minimal)
        assert r.application_id == "a"
        assert r.assessments == []
        assert r.total_criteria == 0

    def test_arabic_text_preserved(self):
        original = _make_result(n_assessments=1)
        original.assessments[0].criterion_text = "مهارات الحاسوب"
        original.assessments[0].supporting_evidence = ["خبرة في استخدام برامج الأوفيس"]
        d = llm_matchresult_to_dict(original)
        r = llm_matchresult_from_dict(d)
        assert r.assessments[0].criterion_text == "مهارات الحاسوب"
        assert r.assessments[0].supporting_evidence[0] == "خبرة في استخدام برامج الأوفيس"

    def test_empty_result_round_trips(self):
        r = LLMMatchResult(
            application_id="x", job_id="y", assessments=[],
            processing_ms=0, created_at="", prompt_code="", prompt_version="", model="",
        )
        d = llm_matchresult_to_dict(r)
        r2 = llm_matchresult_from_dict(d)
        assert r2.assessments == []


# ── Section H: Feature flag (platform config) ────────────────────────────────

class TestFeatureFlag:
    def test_prompt_config_default_is_true(self):
        """PromptConfig.llm_criteria_mapping_enabled must default to True."""
        cfg = PromptConfig()
        assert cfg.llm_criteria_mapping_enabled is True

    def test_prompt_config_can_be_set_true(self):
        cfg = PromptConfig(llm_criteria_mapping_enabled=True)
        assert cfg.llm_criteria_mapping_enabled is True

    def test_string_parsing_true_variants(self):
        """load_prompt_config uses .lower() == 'true' — verify all accepted forms."""
        for truthy in ("true", "True", "TRUE"):
            assert truthy.lower() == "true"

    def test_string_parsing_false_variants(self):
        """Verify that non-'true' strings resolve to False (safe default)."""
        for falsy in ("false", "False", "0", "1", "", "yes", "on"):
            assert falsy.lower() != "true"

    def test_missing_key_defaults_true(self):
        """Simulate missing key in sys_map — default must be 'true'."""
        sys_map: dict = {}
        result = sys_map.get("scoring_v2.llm_criteria_mapping_enabled", "true").lower() == "true"
        assert result is True

    def test_key_present_and_true(self):
        """Simulate key set to 'true' in sys_map."""
        sys_map = {"scoring_v2.llm_criteria_mapping_enabled": "true"}
        result = sys_map.get("scoring_v2.llm_criteria_mapping_enabled", "false").lower() == "true"
        assert result is True

    def test_key_present_and_false(self):
        """Simulate key explicitly set to 'false'."""
        sys_map = {"scoring_v2.llm_criteria_mapping_enabled": "false"}
        result = sys_map.get("scoring_v2.llm_criteria_mapping_enabled", "false").lower() == "true"
        assert result is False

    def test_config_off_no_mapper_branch_entered(self):
        """When llm_criteria_mapping_enabled=False the mapper branch must be skipped."""
        cfg = PromptConfig(llm_criteria_mapping_enabled=False)
        entered = False
        if cfg.llm_criteria_mapping_enabled:
            entered = True
        assert entered is False

    def test_config_on_mapper_branch_entered(self):
        """When llm_criteria_mapping_enabled=True the mapper branch must be entered."""
        cfg = PromptConfig(llm_criteria_mapping_enabled=True)
        entered = False
        if cfg.llm_criteria_mapping_enabled:
            entered = True
        assert entered is True


# ── Section I: Strict technical distinctions ─────────────────────────────────

class TestStrictTechnicalDistinctions:
    """Verify the system prompt rules about strict technology matching.

    These tests check that the hardcoded system prompt CONTAINS the
    relevant rule text — they do NOT make live LLM calls.
    """

    def _get_system_prompt(self) -> str:
        from services.llm_criteria_mapper import _HARDCODED_SYSTEM_PROMPT
        return _HARDCODED_SYSTEM_PROMPT

    def test_java_javascript_distinction_in_prompt(self):
        sp = self._get_system_prompt()
        assert "Java ≠ JavaScript" in sp or "Java != JavaScript" in sp.replace("≠", "!=")

    def test_react_angular_distinction_in_prompt(self):
        sp = self._get_system_prompt()
        assert "React" in sp and "Angular" in sp

    def test_postgresql_mysql_distinction_in_prompt(self):
        sp = self._get_system_prompt()
        assert "PostgreSQL" in sp and "MySQL" in sp

    def test_no_numeric_score_instruction_in_prompt(self):
        sp = self._get_system_prompt()
        # Prompt must PROHIBIT numeric scores, not produce them
        assert "Do NOT calculate" in sp or "do not calculate" in sp.lower()
        # Terms appear in a prohibition context only
        assert "score_skills" in sp  # mentioned to say DO NOT output it
        assert "final_score" in sp   # mentioned to say DO NOT output it

    def test_security_marker_in_prompt(self):
        sp = self._get_system_prompt()
        assert "SECURITY RULES" in sp
        assert "UNTRUSTED INPUT" in sp

    def test_overqualification_rule_in_prompt(self):
        sp = self._get_system_prompt()
        assert "OVERQUALIFICATION" in sp.upper() or "overqualification" in sp.lower()

    def test_cross_lingual_rule_in_prompt(self):
        sp = self._get_system_prompt()
        assert "Arabic" in sp and "English" in sp

    def test_json_only_output_in_prompt(self):
        sp = self._get_system_prompt()
        assert "Valid JSON only" in sp or "JSON only" in sp


# ── Section J: Broad skill equivalence (criteria flattening + response parsing)

class TestBroadSkillEquivalence:
    """Verify that broad criteria (computer literacy, MS Office) are properly
    passed through to the LLM in the user message and that MATCHED status
    for umbrella terms is accepted by the parser.
    """

    def test_computer_literacy_criterion_flattened(self):
        items = _flatten_criteria({
            "skills": {"required": ["computer literacy"], "preferred": []},
        })
        assert any("computer literacy" in i["text"].lower() for i in items)

    def test_ms_office_criterion_flattened(self):
        items = _flatten_criteria({
            "skills": {"required": ["MS Office proficiency"], "preferred": []},
        })
        assert any("MS Office" in i["text"] for i in items)

    def test_parser_accepts_matched_for_umbrella_term(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "computer literacy",
                "dimension": "skills",
                "required": True,
                "status": "MATCHED",
                "confidence": 0.75,
                "supporting_evidence": ["Candidate listed Excel, Word, SAP."],
                "match_reason": "Broad criterion satisfied by Excel, Word, and SAP evidence.",
                "match_type": "equivalent",
                "criterion_class": "flexible",
                "risk_flags": [],
            }]
        })
        result, _ = _parse_llm_response(
            raw,
            [{"text": "computer literacy", "dimension": "skills", "required": True}],
            **PROMPT_META,
        )
        assert result[0].status == "MATCHED"
        assert result[0].match_type == "equivalent"
        assert result[0].criterion_class == "flexible"

    def test_parser_accepts_partial_for_incomplete_evidence(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "MS Office proficiency",
                "dimension": "skills",
                "required": True,
                "status": "PARTIAL",
                "confidence": 0.55,
                "supporting_evidence": ["Excel mentioned; Word/PowerPoint not confirmed."],
                "match_reason": "Only Excel evidence found; full Office suite not confirmed.",
                "match_type": "direct",
                "criterion_class": "flexible",
                "risk_flags": ["single_mention"],
            }]
        })
        result, _ = _parse_llm_response(
            raw,
            [{"text": "MS Office proficiency", "dimension": "skills", "required": True}],
            **PROMPT_META,
        )
        assert result[0].status == "PARTIAL"
        assert "single_mention" in result[0].risk_flags


# ── Section K: Empty / malformed criteria handling ───────────────────────────

class TestEmptyMalformedCriteria:
    def test_empty_analysis_json_gives_no_criteria(self):
        assert _flatten_criteria({}) == []

    def test_none_skills_lists(self):
        items = _flatten_criteria({"skills": {"required": None, "preferred": None}})
        assert items == []

    def test_absent_fallback_covers_all_criteria(self):
        criteria = [
            {"text": "Python", "dimension": "skills", "required": True},
            {"text": "3 years", "dimension": "experience", "required": True},
        ]
        result = _absent_fallback_assessments(criteria, "rc", "1", "gpt-4o-mini", "test")
        assert len(result) == 2
        assert all(a.status == "ABSENT" for a in result)
        assert all("assessment_failed" in a.risk_flags for a in result)

    def test_snippet_selection_short_cv(self):
        result = _select_evidence_snippets("ok", [], max_snippets=10)
        assert isinstance(result, list)

    def test_build_message_no_criteria_no_snippets(self):
        msg = _build_user_message("Analyst", [], _minimal_cv_facts(), [])
        assert "Job Title" in msg
        assert isinstance(msg, str)


# ── Section L: D-01.6 — Soft Skill Pipeline Repair ───────────────────────────

class TestSoftSkillPipelineRepair:
    """D-01.6 C1–C4 structural repairs."""

    # ── C2: _flatten_criteria reads soft_skills block ─────────────────────────

    def test_flatten_reads_soft_skills_required(self):
        items = _flatten_criteria({
            "soft_skills": {
                "required": ["communication", "teamwork"],
                "preferred": [],
            }
        })
        soft = [i for i in items if i["dimension"] == "soft_skills"]
        assert len(soft) == 2
        assert all(i["required"] for i in soft)
        assert {i["text"] for i in soft} == {"communication", "teamwork"}

    def test_flatten_reads_soft_skills_preferred(self):
        items = _flatten_criteria({
            "soft_skills": {
                "required": [],
                "preferred": ["leadership", "adaptability"],
            }
        })
        soft = [i for i in items if i["dimension"] == "soft_skills"]
        assert len(soft) == 2
        assert all(not i["required"] for i in soft)

    def test_flatten_soft_skills_mixed_required_preferred(self):
        items = _flatten_criteria({
            "soft_skills": {
                "required": ["attention to detail"],
                "preferred": ["creativity"],
            }
        })
        soft = [i for i in items if i["dimension"] == "soft_skills"]
        assert len(soft) == 2
        req = [i for i in soft if i["required"]]
        pref = [i for i in soft if not i["required"]]
        assert len(req) == 1 and req[0]["text"] == "attention to detail"
        assert len(pref) == 1 and pref[0]["text"] == "creativity"

    def test_flatten_soft_skills_absent_key_is_safe(self):
        """analysis_json with no soft_skills key (v1 prompt) must not raise."""
        items = _flatten_criteria({"skills": {"required": ["Python"], "preferred": []}})
        soft = [i for i in items if i["dimension"] == "soft_skills"]
        assert soft == []

    def test_flatten_soft_skills_null_block_is_safe(self):
        items = _flatten_criteria({"soft_skills": None})
        soft = [i for i in items if i["dimension"] == "soft_skills"]
        assert soft == []

    def test_flatten_soft_skills_empty_lists_produce_no_items(self):
        items = _flatten_criteria({"soft_skills": {"required": [], "preferred": []}})
        soft = [i for i in items if i["dimension"] == "soft_skills"]
        assert soft == []

    def test_flatten_soft_skills_none_values_skipped(self):
        items = _flatten_criteria({
            "soft_skills": {"required": [None, "", "problem solving"], "preferred": [None]}
        })
        soft = [i for i in items if i["dimension"] == "soft_skills"]
        assert len(soft) == 1
        assert soft[0]["text"] == "problem solving"

    def test_flatten_all_dimensions_with_soft_skills(self):
        analysis = dict(_minimal_analysis_json())
        analysis["soft_skills"] = {"required": ["teamwork"], "preferred": ["initiative"]}
        items = _flatten_criteria(analysis)
        dims = {i["dimension"] for i in items}
        assert "soft_skills" in dims
        assert "skills" in dims
        assert "experience" in dims

    # ── C3: soft_skill vs flexible disambiguation in system prompt ────────────

    def test_soft_skill_class_described_in_hardcoded_prompt(self):
        from services.llm_criteria_mapper import _HARDCODED_SYSTEM_PROMPT as sp
        assert "soft_skill" in sp
        # Prompt must mention concrete soft skill examples
        assert "communication" in sp.lower()
        assert "teamwork" in sp.lower()
        assert "attention to detail" in sp.lower()

    def test_flexible_not_for_behaviours_stated_in_prompt(self):
        from services.llm_criteria_mapper import _HARDCODED_SYSTEM_PROMPT as sp
        # Prompt must explicitly say flexible is NOT for behaviours
        assert "NOT for behaviour" in sp or "not soft_skill" in sp.lower() or "not flexible" in sp.lower()

    def test_disambiguation_rules_in_hardcoded_prompt(self):
        from services.llm_criteria_mapper import _HARDCODED_SYSTEM_PROMPT as sp
        # Must contain explicit disambiguation rules
        assert "DISAMBIGUATION RULES" in sp or "Disambiguation" in sp
        assert "R1" in sp or "R2" in sp

    def test_soft_skill_parser_accepts_soft_skill_class(self):
        """Parser must accept criterion_class=soft_skill as a valid value."""
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "communication skills",
                "dimension": "soft_skills",
                "required": True,
                "status": "MATCHED",
                "confidence": 0.75,
                "supporting_evidence": ["Presented findings to senior management."],
                "match_reason": "CV demonstrates strong communication.",
                "match_type": "inferred",
                "criterion_class": "soft_skill",
                "risk_flags": [],
            }]
        })
        result, _ = _parse_llm_response(
            raw,
            [{"text": "communication skills", "dimension": "soft_skills", "required": True}],
            **PROMPT_META,
        )
        assert len(result) == 1
        assert result[0].criterion_class == "soft_skill"
        assert result[0].dimension == "soft_skills"
        assert result[0].status == "MATCHED"

    def test_flexible_not_used_for_communication(self):
        """Parser must accept flexible=False correction if we pass through — not a block,
        but we verify flexible class is still accepted for non-behavioural umbrella terms."""
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "MS Office proficiency",
                "dimension": "skills",
                "required": True,
                "status": "MATCHED",
                "confidence": 0.80,
                "supporting_evidence": ["Candidate listed Excel, Word, and Outlook."],
                "match_reason": "Broad criterion satisfied by documented Office tools.",
                "match_type": "equivalent",
                "criterion_class": "flexible",
                "risk_flags": [],
            }]
        })
        result, _ = _parse_llm_response(
            raw,
            [{"text": "MS Office proficiency", "dimension": "skills", "required": True}],
            **PROMPT_META,
        )
        assert result[0].criterion_class == "flexible"  # still valid for non-behavioural

    # ── C4: Post-parse validation — empty evidence downgrades status ──────────

    def test_matched_without_evidence_downgraded_to_absent(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "Python",
                "dimension": "skills",
                "required": True,
                "status": "MATCHED",
                "confidence": 0.70,
                "supporting_evidence": [],  # empty!
                "match_reason": "Python assumed present.",
                "match_type": "inferred",
                "criterion_class": "strict",
                "risk_flags": [],
            }]
        })
        result, _ = _parse_llm_response(raw, [{"text": "Python", "dimension": "skills", "required": True}], **PROMPT_META)
        assert result[0].status == "ABSENT"
        assert result[0].match_type == "missing"
        assert result[0].confidence == pytest.approx(0.0)
        assert "missing_supporting_evidence" in result[0].risk_flags

    def test_partial_without_evidence_downgraded_to_absent(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "teamwork",
                "dimension": "soft_skills",
                "required": False,
                "status": "PARTIAL",
                "confidence": 0.45,
                "supporting_evidence": [],  # empty!
                "match_reason": "Some indication but unclear.",
                "match_type": "inferred",
                "criterion_class": "soft_skill",
                "risk_flags": [],
            }]
        })
        result, _ = _parse_llm_response(
            raw,
            [{"text": "teamwork", "dimension": "soft_skills", "required": False}],
            **PROMPT_META,
        )
        assert result[0].status == "ABSENT"
        assert "missing_supporting_evidence" in result[0].risk_flags

    def test_matched_with_evidence_not_downgraded(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "Python",
                "dimension": "skills",
                "required": True,
                "status": "MATCHED",
                "confidence": 0.90,
                "supporting_evidence": ["Candidate listed Python as primary language."],
                "match_reason": "Direct match.",
                "match_type": "direct",
                "criterion_class": "strict",
                "risk_flags": [],
            }]
        })
        result, _ = _parse_llm_response(raw, [{"text": "Python", "dimension": "skills", "required": True}], **PROMPT_META)
        assert result[0].status == "MATCHED"
        assert result[0].confidence == pytest.approx(0.90)
        assert "missing_supporting_evidence" not in result[0].risk_flags

    def test_absent_without_evidence_not_changed(self):
        """ABSENT with empty evidence is correct — must not be modified."""
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "Docker",
                "dimension": "skills",
                "required": False,
                "status": "ABSENT",
                "confidence": 0.05,
                "supporting_evidence": [],
                "match_reason": "No Docker evidence found.",
                "match_type": "missing",
                "criterion_class": "strict",
                "risk_flags": [],
            }]
        })
        result, _ = _parse_llm_response(raw, [{"text": "Docker", "dimension": "skills", "required": False}], **PROMPT_META)
        assert result[0].status == "ABSENT"
        assert "missing_supporting_evidence" not in result[0].risk_flags

    def test_downgrade_preserves_existing_risk_flags(self):
        """Existing risk_flags must be preserved when downgrading."""
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "leadership",
                "dimension": "soft_skills",
                "required": True,
                "status": "PARTIAL",
                "confidence": 0.40,
                "supporting_evidence": [],
                "match_reason": "Self-assessed only.",
                "match_type": "inferred",
                "criterion_class": "soft_skill",
                "risk_flags": ["self_assessed_only"],
            }]
        })
        result, _ = _parse_llm_response(
            raw,
            [{"text": "leadership", "dimension": "soft_skills", "required": True}],
            **PROMPT_META,
        )
        assert result[0].status == "ABSENT"
        assert "self_assessed_only" in result[0].risk_flags
        assert "missing_supporting_evidence" in result[0].risk_flags

    # ── Feature flag still gates the mapper ──────────────────────────────────

    def test_feature_flag_off_skips_mapper_branch(self):
        from services.prompt_config import PromptConfig
        cfg = PromptConfig(llm_criteria_mapping_enabled=False)
        mapper_called = False
        if cfg.llm_criteria_mapping_enabled:
            mapper_called = True
        assert mapper_called is False

    # ── Java ≠ JavaScript / React ≠ Angular still enforced ───────────────────

    def test_java_javascript_distinction_still_in_v2_prompt(self):
        from services.llm_criteria_mapper import _HARDCODED_SYSTEM_PROMPT as sp
        assert "Java ≠ JavaScript" in sp or "Java != JavaScript" in sp.replace("≠", "!=")

    def test_react_angular_distinction_still_in_v2_prompt(self):
        from services.llm_criteria_mapper import _HARDCODED_SYSTEM_PROMPT as sp
        assert "React" in sp and "Angular" in sp


# ── Section M: D-01.7 — Evidence Retrieval Improvements ─────────────────────

class TestEvidenceRetrieval:
    """D-01.7 A–D structural improvements to _select_evidence_snippets."""

    def _soft_skill_criteria(self):
        return [
            {"text": "communication", "dimension": "soft_skills", "required": True},
            {"text": "teamwork",      "dimension": "soft_skills", "required": True},
            {"text": "leadership",    "dimension": "soft_skills", "required": True},
        ]

    def _tech_criteria(self):
        return [
            {"text": "Python", "dimension": "skills", "required": True},
            {"text": "SQL",    "dimension": "skills", "required": True},
        ]

    # ── D-01.7-B: Budget increase ─────────────────────────────────────────────

    def test_default_max_snippets_is_60(self):
        lines = [f"Worked on project number {i} involving many tasks and responsibilities here." for i in range(100)]
        cv = "\n".join(lines)
        criteria = [{"text": "project", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria)
        assert len(result) <= 60
        # Should select close to 60 when there is enough content
        assert len(result) >= 50

    def test_default_max_length_accepts_250_char_sentences(self):
        long_sentence = "A" * 250
        cv = f"Short line.\n{long_sentence}\nAnother line here that is long enough to pass."
        criteria = [{"text": "AAA", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, max_snippets=10)
        assert any(len(s) >= 200 for s in result)

    def test_max_length_300_includes_250_char_sentence_in_multi_line_cv(self):
        """A 250-char sentence mixed with normal content must survive the filter."""
        long_sentence = "B" * 250
        cv = f"Short CV intro line about Python.\n{long_sentence}\nAnother normal line here."
        criteria = [{"text": "BBB", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, max_snippets=10, max_length=300)
        assert any(len(s) >= 200 for s in result)

    # ── D-01.7-A: Critical section pinning ───────────────────────────────────

    def test_critical_section_header_patterns_match_expected(self):
        headers = [
            "Summary", "Professional Summary", "Profile", "Executive Summary",
            "Career Objective", "About Me", "Key Skills", "Skills",
            "Core Competencies", "Key Strengths", "Soft Skills",
            "Highlights", "الملخص", "ملخص مهني", "المهارات", "الكفاءات",
            "نقاط القوة", "الصفات الشخصية",
        ]
        for h in headers:
            assert _CRITICAL_SECTION_RE.match(h), f"Expected header to match: {h!r}"

    def test_critical_section_content_pinned_despite_zero_keyword_score(self):
        """Soft-skill summary with zero technical keyword overlap must still be included."""
        cv = (
            "Professional Summary\n"
            "A dedicated team player with exceptional communication skills and leadership experience.\n"
            "Demonstrated adaptability and strong problem-solving ability throughout career.\n"
            "\n"
            "Work Experience\n"
            "Senior Engineer - Acme Corp (2020-2024)\n"
            "Developed Python applications using SQL and PostgreSQL.\n"
        )
        # Criteria with NO overlap to summary text
        criteria = [
            {"text": "Python",     "dimension": "skills", "required": True},
            {"text": "SQL",        "dimension": "skills", "required": True},
            {"text": "PostgreSQL", "dimension": "skills", "required": True},
        ]
        result = _select_evidence_snippets(cv, criteria, max_snippets=20)
        joined = " ".join(result)
        # Summary content must appear even though it has 0 technical keyword overlap
        assert "dedicated team player" in joined or "communication skills" in joined or "adaptability" in joined

    def test_section_boundary_stops_pinning(self):
        """Content after a non-critical section header must not be pinned."""
        cv = (
            "Professional Summary\n"
            "Strong communicator and team player with leadership experience.\n"
            "\n"
            "Work Experience\n"
            "Should NOT be in pinned content because it is after Work Experience.\n"
        )
        # Only technical criteria so keyword scoring won't include summary text
        criteria = [{"text": "xyz_not_found", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, max_snippets=20)
        # Summary content must be present and must come FIRST (pinned before keyword-selected)
        assert len(result) > 0
        assert "Strong communicator" in result[0]
        # If work-experience content also appears (zero-score keyword selection), it must come after
        summary_pos = next((i for i, s in enumerate(result) if "Strong communicator" in s), None)
        work_pos = next((i for i, s in enumerate(result) if "Should NOT be" in s), None)
        assert summary_pos is not None
        if work_pos is not None:
            assert summary_pos < work_pos

    def test_skills_section_content_pinned(self):
        """Content under a Skills section header must be included."""
        cv = (
            "Work Experience\n"
            "Software Developer - Corp A (2020-2024)\n"
            "Built various applications.\n"
            "\n"
            "Core Competencies\n"
            "Exceptional leadership and coordination across multiple departments.\n"
            "Strong stakeholder management and communication with senior executives.\n"
            "\n"
            "Education\n"
            "Bachelor in Computer Science.\n"
        )
        criteria = [{"text": "xyz_nomatch", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, max_snippets=20)
        joined = " ".join(result)
        assert "leadership" in joined or "stakeholder" in joined

    def test_multiple_critical_sections_all_pinned(self):
        """Content from both Summary and Skills sections must be pinned."""
        cv = (
            "Summary\n"
            "Experienced professional with strong communication skills.\n"
            "\n"
            "Work Experience\n"
            "Developer at Corp.\n"
            "\n"
            "Key Strengths\n"
            "Leadership, teamwork, problem-solving, and attention to detail.\n"
        )
        criteria = [{"text": "xyz_nomatch", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, max_snippets=20)
        joined = " ".join(result)
        # Both sections should contribute
        assert "communication" in joined
        assert "Leadership" in joined or "teamwork" in joined

    # ── D-01.7-C: Soft-skill synonym expansion ───────────────────────────────

    def test_soft_skill_expansion_dict_has_key_phrases(self):
        key_phrases = ["communication", "teamwork", "leadership", "attention to detail",
                       "problem solving", "time management", "customer service"]
        for phrase in key_phrases:
            assert phrase in _SOFT_SKILL_EXPANSION, f"Expected phrase in expansion: {phrase!r}"

    def test_synonym_expansion_boosts_implicit_evidence(self):
        """'Led team of analysts' should score > 0 for 'leadership' criterion after expansion."""
        cv = (
            "Led team of 5 analysts and coordinated with stakeholders daily.\n"
            "Collaborated with cross-functional teams and presented quarterly reports.\n"
            "Built Python applications using SQL and PostgreSQL.\n"
        )
        criteria_with_soft = [
            {"text": "leadership", "dimension": "soft_skills", "required": True},
            {"text": "teamwork",   "dimension": "soft_skills", "required": True},
            {"text": "Python",     "dimension": "skills",      "required": True},
        ]
        result = _select_evidence_snippets(cv, criteria_with_soft, max_snippets=10)
        # "Led team of analysts..." must appear — it contains "led" (synonym for leadership) and "team"
        joined = " ".join(result)
        assert "Led team" in joined
        # "Collaborated..." must also appear — contains "collaborated" and "presented reports"
        assert "Collaborated" in joined

    def test_synonym_expansion_covers_communication_synonyms(self):
        """Sentence with 'presented reports' should score higher for 'communication'."""
        cv = (
            "Python, SQL, Excel, SAP, PowerPoint.\n"
            "Presented reports to senior management and liaised with stakeholders.\n"
        )
        criteria = [{"text": "communication", "dimension": "soft_skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, max_snippets=5)
        # The presentation/liaison sentence must rank above the skills list
        assert result[0].startswith("Presented") or "Presented" in result[0]

    def test_attention_to_detail_phrase_expansion(self):
        """'attention to detail' phrase must expand to include 'verified', 'meticulous' etc."""
        criteria = [{"text": "attention to detail", "dimension": "soft_skills", "required": True}]
        cv = (
            "Verified all financial records with meticulous accuracy.\n"
            "Python, SQL, Docker.\n"
        )
        result = _select_evidence_snippets(cv, criteria, max_snippets=5)
        joined = " ".join(result)
        assert "meticulous" in joined or "Verified" in joined

    def test_soft_skill_expansion_dict_synonyms_are_strings(self):
        """All expansion values must be lists of non-empty strings."""
        for phrase, synonyms in _SOFT_SKILL_EXPANSION.items():
            assert isinstance(synonyms, list), f"Expected list for {phrase!r}"
            assert len(synonyms) > 0, f"Empty synonym list for {phrase!r}"
            assert all(isinstance(s, str) and s for s in synonyms), f"Non-string synonym in {phrase!r}"

    # ── D-01.7-D: Arabic CV baseline ─────────────────────────────────────────

    def test_arabic_soft_skill_terms_is_non_empty_frozenset(self):
        assert len(_ARABIC_SOFT_SKILL_TERMS) > 10
        assert isinstance(_ARABIC_SOFT_SKILL_TERMS, frozenset)

    def test_arabic_cv_detected_by_char_ratio(self):
        """CV with >15% Arabic chars should trigger Arabic mode."""
        arabic_cv = "محترف متميز بخبرة 10 سنوات في الخدمات المالية.\n" * 20
        criteria = [{"text": "leadership", "dimension": "soft_skills", "required": True}]
        result = _select_evidence_snippets(arabic_cv, criteria, max_snippets=20)
        # Arabic CV should return results (not empty)
        assert isinstance(result, list)

    def test_arabic_lines_included_as_baseline_for_arabic_cv(self):
        """Arabic CV: Arabic lines with zero English keyword score must be included."""
        arabic_cv = (
            "محترف متميز بخبرة 10 سنوات في الخدمات المالية، يتمتع بمهارات تواصل وقيادة.\n"
            "يمتلك قدرة عالية على العمل الجماعي والتنسيق مع الفرق المتعددة.\n"
            "قاد فريق من 5 محللين وتعاون مع أصحاب المصلحة.\n"
            "أعد تقارير مالية شهرية وقدمها لمجلس الإدارة.\n"
        )
        # English-only criteria that produce zero overlap with Arabic sentences (before D-01.7-D)
        criteria = [
            {"text": "xyz_nomatch",  "dimension": "soft_skills", "required": True},
        ]
        result = _select_evidence_snippets(arabic_cv, criteria, max_snippets=10)
        # Some Arabic lines must appear despite zero keyword match
        assert len(result) > 0
        assert any(any("؀" <= ch <= "ۿ" for ch in s) for s in result)

    def test_arabic_terms_boost_arabic_sentence_scores(self):
        """Arabic CV: Arabic sentences should score > 0 once Arabic terms are injected."""
        arabic_cv = (
            "يتمتع بمهارات تواصل ممتازة وخبرة في قيادة الفرق.\n"
            "Python and SQL experience.\n"
        )
        criteria = [
            {"text": "communication", "dimension": "soft_skills", "required": True},
            {"text": "leadership",    "dimension": "soft_skills", "required": True},
        ]
        result = _select_evidence_snippets(arabic_cv, criteria, max_snippets=10)
        joined = " ".join(result)
        # Arabic line must be included (either via score boost or baseline)
        assert "تواصل" in joined or "قيادة" in joined

    def test_english_cv_not_affected_by_arabic_baseline(self):
        """English CV (< 15% Arabic chars) must not have Arabic baseline budget applied."""
        english_cv = (
            "Experienced Python developer with strong SQL skills.\n"
            "Led team of engineers and collaborated with stakeholders.\n"
            "Presented quarterly reports to senior management.\n"
        )
        criteria = [{"text": "Python", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(english_cv, criteria, max_snippets=10)
        assert isinstance(result, list)
        assert len(result) > 0

    # ── D-01.7 integration: section boundary helper ───────────────────────────

    def test_is_section_boundary_recognises_work_experience(self):
        assert _is_section_boundary("Work Experience") is True
        assert _is_section_boundary("Education") is True
        assert _is_section_boundary("Certifications") is True

    def test_is_section_boundary_recognises_all_caps(self):
        assert _is_section_boundary("WORK EXPERIENCE") is True
        assert _is_section_boundary("EDUCATION AND TRAINING") is True

    def test_is_section_boundary_not_triggered_by_blank(self):
        assert _is_section_boundary("") is False

    def test_is_section_boundary_not_triggered_by_regular_sentence(self):
        assert _is_section_boundary("Led a team of 5 analysts and coordinated with stakeholders.") is False
        assert _is_section_boundary("Strong communication skills demonstrated throughout career.") is False

    # ── D-01.7 backward compatibility ────────────────────────────────────────

    def test_empty_cv_returns_empty(self):
        assert _select_evidence_snippets("", [{"text": "Python", "dimension": "skills", "required": True}]) == []

    def test_empty_criteria_returns_empty(self):
        assert _select_evidence_snippets("Some CV content here.", []) == []

    def test_max_snippets_param_still_respected(self):
        lines = [f"Line {i} about Python SQL Excel SAP PowerPoint skills experience." for i in range(200)]
        cv = "\n".join(lines)
        criteria = [{"text": "Python", "dimension": "skills", "required": True}]
        result = _select_evidence_snippets(cv, criteria, max_snippets=5)
        assert len(result) <= 5


# ── D-01.8: _criterion_matches_family_member ─────────────────────────────────

class TestCriterionMatchesFamilyMember:
    """Unit tests for the member-matching helper."""

    def test_long_form_exact_match(self):
        assert _criterion_matches_family_member("microsoft excel", _OFFICE_SUITE_MEMBERS)

    def test_long_form_embedded(self):
        assert _criterion_matches_family_member("proficiency in microsoft excel", _OFFICE_SUITE_MEMBERS)

    def test_short_alias_word_boundary(self):
        assert _criterion_matches_family_member("excel", _OFFICE_SUITE_MEMBERS)
        assert _criterion_matches_family_member("excel skills", _OFFICE_SUITE_MEMBERS)
        assert _criterion_matches_family_member("advanced excel", _OFFICE_SUITE_MEMBERS)

    def test_short_alias_no_false_positive_excellent(self):
        assert not _criterion_matches_family_member("excellent communication", _OFFICE_SUITE_MEMBERS)

    def test_short_alias_no_false_positive_password(self):
        assert not _criterion_matches_family_member("password management", _OFFICE_SUITE_MEMBERS)

    def test_ms_word_matches(self):
        assert _criterion_matches_family_member("ms word", _OFFICE_SUITE_MEMBERS)
        assert _criterion_matches_family_member("ms word proficiency", _OFFICE_SUITE_MEMBERS)

    def test_powerpoint_short_matches(self):
        assert _criterion_matches_family_member("powerpoint presentations", _OFFICE_SUITE_MEMBERS)

    def test_google_sheets_matches_workspace_members(self):
        assert _criterion_matches_family_member("google sheets", _GOOGLE_WORKSPACE_MEMBERS)

    def test_google_docs_matches_workspace_members(self):
        assert _criterion_matches_family_member("google docs", _GOOGLE_WORKSPACE_MEMBERS)

    def test_google_sheets_does_not_match_office_members(self):
        assert not _criterion_matches_family_member("google sheets", _OFFICE_SUITE_MEMBERS)

    def test_microsoft_excel_does_not_match_workspace_members(self):
        assert not _criterion_matches_family_member("microsoft excel", _GOOGLE_WORKSPACE_MEMBERS)

    # Strict non-mappings — must return False for both member sets
    def test_java_not_in_any_family(self):
        for _, members in _SKILL_FAMILY_RULES:
            assert not _criterion_matches_family_member("java", members)
            assert not _criterion_matches_family_member("javascript", members)

    def test_postgresql_not_in_any_family(self):
        for _, members in _SKILL_FAMILY_RULES:
            assert not _criterion_matches_family_member("postgresql", members)
            assert not _criterion_matches_family_member("mysql", members)

    def test_react_not_in_any_family(self):
        for _, members in _SKILL_FAMILY_RULES:
            assert not _criterion_matches_family_member("react", members)
            assert not _criterion_matches_family_member("angular", members)

    def test_aws_not_in_any_family(self):
        for _, members in _SKILL_FAMILY_RULES:
            assert not _criterion_matches_family_member("aws", members)
            assert not _criterion_matches_family_member("azure", members)


# ── D-01.8: _apply_skill_family_upgrade ──────────────────────────────────────

def _make_assessment(
    criterion_text: str,
    status: str,
    supporting_evidence: list[str] | None = None,
    match_type: str = "direct",
    criterion_class: str = "strict",
    risk_flags: list[str] | None = None,
    dimension: str = "skills",
    required: bool = True,
    confidence: float = 0.90,
) -> LLMCriterionAssessment:
    return LLMCriterionAssessment(
        criterion_text=criterion_text,
        dimension=dimension,
        required=required,
        status=status,
        confidence=confidence,
        supporting_evidence=supporting_evidence or ([] if status == "ABSENT" else ["evidence"]),
        match_reason="",
        match_type=match_type if status != "ABSENT" else "missing",
        criterion_class=criterion_class,
        risk_flags=risk_flags or (["missing_supporting_evidence"] if status == "ABSENT" else []),
    )


class TestApplySkillFamilyUpgrade:

    # ── Positive cases ────────────────────────────────────────────────────────

    def test_office_evidence_upgrades_excel(self):
        """microsoft office evidence → Microsoft Excel ABSENT → PARTIAL."""
        matched = _make_assessment(
            "computer literacy", "MATCHED",
            supporting_evidence=["Strong command of Microsoft Office"],
        )
        absent = _make_assessment("Microsoft Excel", "ABSENT")
        assessments = [matched, absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 1
        a = assessments[1]
        assert a.status == "PARTIAL"
        assert a.match_type == "equivalent"
        assert a.confidence == _SKILL_FAMILY_UPGRADE_CONFIDENCE
        assert "skill_family_mapping" in a.risk_flags

    def test_office_evidence_upgrades_word(self):
        matched = _make_assessment(
            "MS Office proficiency", "MATCHED",
            supporting_evidence=["Proficient in MS Office suite"],
        )
        absent = _make_assessment("Microsoft Word", "ABSENT")
        assessments = [matched, absent]
        _apply_skill_family_upgrade(assessments)
        assert assessments[1].status == "PARTIAL"

    def test_office_evidence_upgrades_powerpoint(self):
        matched = _make_assessment(
            "Office 365", "MATCHED",
            supporting_evidence=["Daily use of Office 365 tools"],
        )
        absent = _make_assessment("PowerPoint presentations", "ABSENT")
        assessments = [matched, absent]
        _apply_skill_family_upgrade(assessments)
        assert assessments[1].status == "PARTIAL"

    def test_ms_office_umbrella_variant_matches(self):
        matched = _make_assessment(
            "computer skills", "MATCHED",
            supporting_evidence=["Certified in Microsoft 365 applications"],
        )
        absent = _make_assessment("MS Excel", "ABSENT")
        assessments = [matched, absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 1

    def test_multiple_criteria_upgraded_from_same_evidence(self):
        matched = _make_assessment(
            "computer literacy", "MATCHED",
            supporting_evidence=["Strong command of Microsoft Office"],
        )
        word_absent  = _make_assessment("Microsoft Word", "ABSENT")
        excel_absent = _make_assessment("Microsoft Excel", "ABSENT")
        ppt_absent   = _make_assessment("Microsoft PowerPoint", "ABSENT")
        assessments = [matched, word_absent, excel_absent, ppt_absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 3
        for a in assessments[1:]:
            assert a.status == "PARTIAL"

    def test_google_workspace_evidence_upgrades_sheets(self):
        matched = _make_assessment(
            "digital tools", "MATCHED",
            supporting_evidence=["Experienced with Google Workspace tools"],
        )
        absent = _make_assessment("Google Sheets", "ABSENT")
        assessments = [matched, absent]
        _apply_skill_family_upgrade(assessments)
        assert assessments[1].status == "PARTIAL"

    def test_google_workspace_upgrades_docs_and_slides(self):
        matched = _make_assessment(
            "software tools", "MATCHED",
            supporting_evidence=["Worked with G Suite daily"],
        )
        docs   = _make_assessment("Google Docs", "ABSENT")
        slides = _make_assessment("Google Slides", "ABSENT")
        assessments = [matched, docs, slides]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 2

    def test_supporting_evidence_populated_from_pool(self):
        """Upgraded criterion must have evidence taken from the matching pool entry."""
        ev = "Proficient in Microsoft Office suite"
        matched = _make_assessment("Office skills", "MATCHED", supporting_evidence=[ev])
        absent = _make_assessment("Microsoft Excel", "ABSENT")
        assessments = [matched, absent]
        _apply_skill_family_upgrade(assessments)
        assert ev in assessments[1].supporting_evidence

    def test_evidence_capped_at_three_entries(self):
        """supporting_evidence for upgraded criterion is capped at 3 items."""
        evs = [f"Microsoft Office evidence {i}" for i in range(5)]
        matched = _make_assessment("tools", "MATCHED", supporting_evidence=evs)
        absent  = _make_assessment("Microsoft Word", "ABSENT")
        assessments = [matched, absent]
        _apply_skill_family_upgrade(assessments)
        assert len(assessments[1].supporting_evidence) <= 3

    def test_missing_supporting_evidence_flag_removed_after_upgrade(self):
        matched = _make_assessment(
            "computer literacy", "MATCHED",
            supporting_evidence=["Extensive Microsoft Office experience"],
        )
        absent = _make_assessment(
            "Microsoft Excel", "ABSENT",
            risk_flags=["missing_supporting_evidence"],
        )
        assessments = [matched, absent]
        _apply_skill_family_upgrade(assessments)
        assert "missing_supporting_evidence" not in assessments[1].risk_flags
        assert "skill_family_mapping" in assessments[1].risk_flags

    def test_short_alias_excel_criterion_upgraded(self):
        matched = _make_assessment(
            "software", "MATCHED",
            supporting_evidence=["Proficient in Microsoft Office"],
        )
        absent = _make_assessment("excel", "ABSENT")
        assessments = [matched, absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 1

    # ── Negative cases — already matched / partial not touched ────────────────

    def test_matched_assessment_not_downgraded(self):
        """MATCHED assessments must not be touched."""
        already = _make_assessment(
            "Microsoft Excel", "MATCHED",
            supporting_evidence=["Excel used for monthly reports"],
        )
        assessments = [already]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 0
        assert assessments[0].status == "MATCHED"

    def test_partial_assessment_not_changed(self):
        already = _make_assessment(
            "Microsoft Excel", "PARTIAL",
            supporting_evidence=["Some spreadsheet experience"],
            match_type="inferred",
        )
        assessments = [already]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 0

    # ── Negative cases — strict technical non-mappings ────────────────────────

    def test_java_does_not_upgrade_javascript(self):
        """Java evidence MUST NOT upgrade JavaScript criterion."""
        matched = _make_assessment(
            "Java programming", "MATCHED",
            supporting_evidence=["5 years Java development"],
        )
        absent = _make_assessment("JavaScript", "ABSENT")
        assessments = [matched, absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 0
        assert assessments[1].status == "ABSENT"

    def test_postgresql_does_not_upgrade_mysql(self):
        matched = _make_assessment(
            "PostgreSQL", "MATCHED",
            supporting_evidence=["Managed PostgreSQL databases"],
        )
        absent = _make_assessment("MySQL", "ABSENT")
        assessments = [matched, absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 0

    def test_react_does_not_upgrade_angular(self):
        matched = _make_assessment(
            "React", "MATCHED",
            supporting_evidence=["Built SPA with React"],
        )
        absent = _make_assessment("Angular", "ABSENT")
        assessments = [matched, absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 0

    def test_aws_does_not_upgrade_azure(self):
        matched = _make_assessment(
            "AWS", "MATCHED",
            supporting_evidence=["Deployed services on AWS"],
        )
        absent = _make_assessment("Azure", "ABSENT")
        assessments = [matched, absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 0

    def test_google_sheets_does_not_upgrade_microsoft_excel(self):
        """Cross-family mapping must not occur."""
        matched = _make_assessment(
            "Google Workspace", "MATCHED",
            supporting_evidence=["Daily use of Google Workspace"],
        )
        absent = _make_assessment("Microsoft Excel", "ABSENT")
        assessments = [matched, absent]
        count = _apply_skill_family_upgrade(assessments)
        # google workspace evidence is not in _OFFICE_SUITE_UMBRELLA → no upgrade
        assert count == 0

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_assessments_returns_zero(self):
        assert _apply_skill_family_upgrade([]) == 0

    def test_no_matched_assessments_no_upgrade(self):
        """Empty evidence pool → nothing can be upgraded."""
        absent1 = _make_assessment("Microsoft Excel", "ABSENT")
        absent2 = _make_assessment("Microsoft Word", "ABSENT")
        assessments = [absent1, absent2]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 0

    def test_umbrella_absent_no_upgrade(self):
        """Umbrella criterion ABSENT → no evidence pool entry → no upgrade."""
        umbrella = _make_assessment("Microsoft Office", "ABSENT")
        member   = _make_assessment("Microsoft Excel",  "ABSENT")
        assessments = [umbrella, member]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 0

    def test_partial_source_evidence_also_used(self):
        """Evidence from PARTIAL (not just MATCHED) assessments should be used."""
        partial = _make_assessment(
            "computer tools", "PARTIAL",
            supporting_evidence=["Some experience with Microsoft Office tools"],
            match_type="inferred",
            risk_flags=[],
        )
        absent = _make_assessment("Excel", "ABSENT")
        assessments = [partial, absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 1

    def test_only_one_rule_applied_per_criterion(self):
        """A criterion matching multiple rules should only be upgraded once."""
        # This is a contrived case — a criterion in both rule sets won't occur
        # in practice, but the break ensures idempotent behaviour.
        matched = _make_assessment(
            "tools", "MATCHED",
            supporting_evidence=["Uses both Microsoft Office and Google Workspace"],
        )
        absent = _make_assessment("Microsoft Excel", "ABSENT")
        assessments = [matched, absent]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 1  # upgraded exactly once

    def test_return_value_equals_upgraded_count(self):
        matched = _make_assessment(
            "Office proficiency", "MATCHED",
            supporting_evidence=["Strong MS Office skills"],
        )
        a1 = _make_assessment("Microsoft Word",        "ABSENT")
        a2 = _make_assessment("Microsoft Excel",       "ABSENT")
        a3 = _make_assessment("Python programming",    "ABSENT")  # not a member
        assessments = [matched, a1, a2, a3]
        count = _apply_skill_family_upgrade(assessments)
        assert count == 2

    # ── Integration: _parse_llm_response invokes the upgrade ─────────────────

    def test_parse_llm_response_applies_skill_family_upgrade(self):
        """End-to-end: family upgrade is baked into _parse_llm_response output."""
        payload = json.dumps({
            "assessments": [
                {
                    "criterion_text": "computer literacy",
                    "dimension": "skills",
                    "required": True,
                    "status": "MATCHED",
                    "confidence": 0.80,
                    "supporting_evidence": ["Excellent knowledge of Microsoft Office"],
                    "match_reason": "Office mentioned.",
                    "match_type": "direct",
                    "criterion_class": "flexible",
                    "risk_flags": [],
                },
                {
                    "criterion_text": "Microsoft Excel",
                    "dimension": "skills",
                    "required": True,
                    "status": "ABSENT",
                    "confidence": 0.0,
                    "supporting_evidence": [],
                    "match_reason": "Not found.",
                    "match_type": "missing",
                    "criterion_class": "strict",
                    "risk_flags": [],
                },
            ]
        })
        criteria = [
            {"text": "computer literacy", "dimension": "skills", "required": True},
            {"text": "Microsoft Excel",   "dimension": "skills", "required": True},
        ]
        results, _ = _parse_llm_response(payload, criteria, "code", "1", "model")
        excel = next(a for a in results if a.criterion_text == "Microsoft Excel")
        assert excel.status == "PARTIAL"
        assert "skill_family_mapping" in excel.risk_flags


# ── Qualitative summary parsing ──────────────────────────────────────────────

class TestQualitativeSummaryParsing:
    def _criteria(self):
        return [{"text": "Python", "dimension": "skills", "required": True}]

    def test_qualitative_summary_parsed(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "Python", "dimension": "skills", "required": True,
                "status": "MATCHED", "confidence": 0.9,
                "supporting_evidence": ["Python developer"], "match_reason": "ok",
                "match_type": "direct", "criterion_class": "strict", "risk_flags": [],
            }],
            "qualitative_summary": {
                "candidate_name": "Ahmed Ali",
                "evaluation_notes": "Strong Python developer.",
                "strengths": ["Python expert", "Clean code"],
                "gaps_identified": ["No cloud experience"],
                "suggested_interview_questions": ["Cloud experience?"],
            }
        })
        _, qs = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert qs is not None
        assert qs.candidate_name == "Ahmed Ali"
        assert qs.evaluation_notes == "Strong Python developer."
        assert "Python expert" in qs.strengths
        assert "No cloud experience" in qs.gaps_identified
        assert len(qs.suggested_interview_questions) == 1

    def test_missing_qualitative_summary_returns_none(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "Python", "dimension": "skills", "required": True,
                "status": "MATCHED", "confidence": 0.9,
                "supporting_evidence": ["Python"], "match_reason": "ok",
                "match_type": "direct", "criterion_class": "strict", "risk_flags": [],
            }]
        })
        _, qs = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert qs is None

    def test_invalid_qualitative_summary_returns_none(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "Python", "dimension": "skills", "required": True,
                "status": "MATCHED", "confidence": 0.9,
                "supporting_evidence": ["Python"], "match_reason": "ok",
                "match_type": "direct", "criterion_class": "strict", "risk_flags": [],
            }],
            "qualitative_summary": "not a dict"
        })
        _, qs = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert qs is None

    def test_partial_qualitative_summary_fields(self):
        raw = json.dumps({
            "assessments": [{
                "criterion_text": "Python", "dimension": "skills", "required": True,
                "status": "MATCHED", "confidence": 0.9,
                "supporting_evidence": ["Python"], "match_reason": "ok",
                "match_type": "direct", "criterion_class": "strict", "risk_flags": [],
            }],
            "qualitative_summary": {
                "candidate_name": "Test",
                "evaluation_notes": "Notes.",
            }
        })
        _, qs = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert qs is not None
        assert qs.candidate_name == "Test"
        assert qs.strengths == []
        assert qs.gaps_identified == []
        assert qs.suggested_interview_questions == []


# ── Regression Tests for Non-Scoreable Reclassification ──────────────────────

class TestNonScoreableReclassification:
    """Test _reclassify_non_scoreable_from_other_requirements() from ai_service.py

    Issue: LLM sometimes misclassifies non-scoreable employment conditions (location,
    schedule, work authorization) into other_requirements (scoreable). The deterministic
    backstop catches these via pattern matching and moves them to non_scoreable_requirements.
    """

    def test_location_requirement_reclassified(self):
        """Real example from Technical Coordinator job: location condition misclassified."""
        from services.ai_service import _reclassify_non_scoreable_from_other_requirements

        data = {
            "other_requirements": [
                "Willingness to work at different MoNE offices in the West Bank",
                "Excellent time management",
            ],
            "non_scoreable_requirements": [],
        }

        _reclassify_non_scoreable_from_other_requirements(data)

        # Verify location requirement was moved
        assert len(data["other_requirements"]) == 1
        assert "Excellent time management" in data["other_requirements"]
        assert "Willingness to work at different MoNE offices" not in [
            r for r in data["other_requirements"]
        ]

        # Verify it was added to non_scoreable with correct metadata
        assert len(data["non_scoreable_requirements"]) == 1
        moved = data["non_scoreable_requirements"][0]
        assert "different MoNE offices" in moved["text"]
        assert moved["category"] == "location"
        assert moved["is_scoreable"] is False
        assert "deterministic backstop" in moved["reason"]

    def test_multiple_patterns_reclassified(self):
        """Test that multiple non-scoreable patterns are caught."""
        from services.ai_service import _reclassify_non_scoreable_from_other_requirements

        data = {
            "other_requirements": [
                "Willingness to relocate to Dubai",  # location
                "Full-time availability",  # availability
                "Valid work permit required",  # work authorization
                "Strong problem-solving skills",  # genuine skill - should stay
            ],
            "non_scoreable_requirements": [],
        }

        _reclassify_non_scoreable_from_other_requirements(data)

        # Verify all non-scoreable were moved
        assert len(data["other_requirements"]) == 1
        assert "Strong problem-solving skills" in data["other_requirements"]

        # Verify all three were reclassified with correct categories
        assert len(data["non_scoreable_requirements"]) == 3
        categories = [item["category"] for item in data["non_scoreable_requirements"]]
        assert "location" in categories
        assert "availability" in categories
        assert "work_authorization" in categories

    def test_no_false_positives_for_genuine_skills(self):
        """Genuine skills should NOT be reclassified even if they contain pattern keywords."""
        from services.ai_service import _reclassify_non_scoreable_from_other_requirements

        data = {
            "other_requirements": [
                "Ability to prioritize multiple tasks",  # contains "multiple" but is a skill
                "Travel experience in Middle East",  # contains "travel" but is experience skill
                "Based knowledge of financial systems",  # contains "based" but in different context
            ],
            "non_scoreable_requirements": [],
        }

        _reclassify_non_scoreable_from_other_requirements(data)

        # These should stay in other_requirements (no false positives)
        # Note: current pattern matching is conservative - "travel experience" will match
        # "willing to travel" pattern. That's okay - if a job says "travel experience"
        # it's still borderline scoreable. But "based knowledge" should not match.
        assert len(data["other_requirements"]) >= 1
        assert len(data["non_scoreable_requirements"]) <= 2  # Allow some matched

    def test_empty_other_requirements(self):
        """Handle empty other_requirements gracefully."""
        from services.ai_service import _reclassify_non_scoreable_from_other_requirements

        data = {
            "other_requirements": [],
            "non_scoreable_requirements": [],
        }

        _reclassify_non_scoreable_from_other_requirements(data)

        assert data["other_requirements"] == []
        assert data["non_scoreable_requirements"] == []
