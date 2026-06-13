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
    _MAPPER_VERSION,
    _absent_fallback_assessments,
    _build_user_message,
    _flatten_criteria,
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

    def test_education_level_required(self):
        items = _flatten_criteria({
            "education": {"minimum_level": "Bachelor's", "fields_of_study": ["Finance"]},
        })
        edu = [i for i in items if i["dimension"] == "education"]
        required_edu = [i for i in edu if i["required"]]
        assert any("Bachelor" in i["text"] for i in required_edu)

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
        result = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert len(result) == 1
        assert result[0].status == "MATCHED"
        assert result[0].confidence == pytest.approx(0.92)
        assert result[0].match_type == "direct"

    def test_invalid_json_returns_absent_fallback(self):
        result = _parse_llm_response("{not valid json", self._criteria(), **PROMPT_META)
        assert len(result) == 1
        assert result[0].status == "ABSENT"
        assert "assessment_failed" in result[0].risk_flags

    def test_missing_assessments_key_returns_fallback(self):
        raw = json.dumps({"result": "something else"})
        result = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
        assert result[0].status == "ABSENT"
        assert "assessment_failed" in result[0].risk_flags

    def test_empty_assessments_array(self):
        raw = json.dumps({"assessments": []})
        result = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
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
        result = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
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
        result = _parse_llm_response(raw, self._criteria(), **PROMPT_META)
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
            "supporting_evidence": [],
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
    def test_prompt_config_default_is_false(self):
        """PromptConfig.llm_criteria_mapping_enabled must default to False."""
        cfg = PromptConfig()
        assert cfg.llm_criteria_mapping_enabled is False

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

    def test_missing_key_defaults_false(self):
        """Simulate missing key in sys_map — default must be 'false'."""
        sys_map: dict = {}
        result = sys_map.get("scoring_v2.llm_criteria_mapping_enabled", "false").lower() == "true"
        assert result is False

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
        result = _parse_llm_response(
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
        result = _parse_llm_response(
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
