"""
Unit tests for services/criteria_matcher.py (Batch 2A-1).

Validates:
- object creation with required and optional fields
- default values
- type correctness
- serialisation-friendly structure (all fields JSON-serialisable)
- gap severity invariants
- MatchResult aggregate statistics defaults
"""
from __future__ import annotations

import dataclasses
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.criteria_matcher import (
    CriteriaMatchEngine,
    CriterionMatch,
    GapCandidate,
    MatchResult,
)
from services.cv_evidence import (
    CVFacts,
    CertificationEvidence,
    DomainSignal,
    EducationEvidence,
    ExperienceEvidence,
    SkillEvidence,
    SoftSkillSignal,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_json(obj) -> str:
    return json.dumps(dataclasses.asdict(obj))


def _make_criterion(
    criterion_text: str = "Microsoft Excel",
    dimension: str = "skills",
    required: bool = True,
    status: str = "MATCHED",
    confidence: float = 0.90,
    match_method: str = "exact",
    **kwargs,
) -> CriterionMatch:
    return CriterionMatch(
        criterion_text=criterion_text,
        dimension=dimension,
        required=required,
        status=status,
        confidence=confidence,
        match_method=match_method,
        **kwargs,
    )


# ── CriterionMatch ────────────────────────────────────────────────────────────

class TestCriterionMatch:
    def test_matched_criterion(self):
        cm = _make_criterion(
            criterion_text="Microsoft Excel",
            status="MATCHED",
            confidence=0.95,
            match_method="exact",
            supporting_evidence=["Excel proficiency listed in skills section"],
            evidence_confidence=[0.95],
        )
        assert cm.criterion_text == "Microsoft Excel"
        assert cm.status == "MATCHED"
        assert cm.confidence == 0.95
        assert len(cm.supporting_evidence) == 1
        assert cm.matched_via_translation is False
        assert cm.original_cv_term == ""
        assert cm.partial_reason == ""

    def test_partial_criterion(self):
        cm = _make_criterion(
            criterion_text="5 years experience",
            dimension="experience",
            status="PARTIAL",
            confidence=0.45,
            match_method="inferred",
            partial_reason="2 of 5 required years found",
        )
        assert cm.status == "PARTIAL"
        assert cm.partial_reason == "2 of 5 required years found"

    def test_absent_criterion(self):
        cm = _make_criterion(
            criterion_text="PMP Certification",
            dimension="certifications",
            status="ABSENT",
            confidence=0.05,
            match_method="absent",
        )
        assert cm.status == "ABSENT"
        assert cm.confidence == 0.05
        assert cm.match_method == "absent"

    def test_cross_lingual_match(self):
        cm = _make_criterion(
            criterion_text="Microsoft Excel",
            status="MATCHED",
            confidence=0.82,
            match_method="fuzzy",
            matched_via_translation=True,
            original_cv_term="إكسل",
        )
        assert cm.matched_via_translation is True
        assert cm.original_cv_term == "إكسل"

    def test_preferred_criterion(self):
        cm = _make_criterion(
            criterion_text="Tableau",
            required=False,
            status="ABSENT",
            confidence=0.0,
            match_method="absent",
        )
        assert cm.required is False

    def test_defaults(self):
        cm = _make_criterion()
        assert cm.supporting_evidence == []
        assert cm.evidence_confidence == []
        assert cm.partial_reason == ""
        assert cm.matched_via_translation is False
        assert cm.original_cv_term == ""

    def test_all_match_methods(self):
        for method in ("exact", "normalised", "fuzzy", "semantic", "inferred", "absent"):
            cm = _make_criterion(match_method=method, status="MATCHED", confidence=0.5)
            assert cm.match_method == method

    def test_all_dimensions(self):
        for dim in ("skills", "experience", "education", "certifications",
                    "soft_skills", "domain_knowledge", "other"):
            cm = _make_criterion(dimension=dim)
            assert cm.dimension == dim

    def test_json_serialisable(self):
        cm = _make_criterion(
            supporting_evidence=["Excel listed in CV skills"],
            evidence_confidence=[0.95],
        )
        payload = _to_json(cm)
        data = json.loads(payload)
        assert data["criterion_text"] == "Microsoft Excel"
        assert data["status"] == "MATCHED"
        assert isinstance(data["confidence"], float)
        assert isinstance(data["supporting_evidence"], list)
        assert isinstance(data["evidence_confidence"], list)

    def test_multiple_evidence_items(self):
        cm = _make_criterion(
            status="MATCHED",
            confidence=0.85,
            supporting_evidence=[
                "Excel listed in skills section",
                "Prepared Excel reports for management",
            ],
            evidence_confidence=[0.95, 0.45],
        )
        assert len(cm.supporting_evidence) == 2
        assert len(cm.evidence_confidence) == 2

    def test_dataclass_fields(self):
        field_names = {f.name for f in dataclasses.fields(CriterionMatch)}
        required = {
            "criterion_text", "dimension", "required", "status",
            "confidence", "match_method", "supporting_evidence",
            "evidence_confidence", "partial_reason",
            "matched_via_translation", "original_cv_term",
        }
        assert required.issubset(field_names)


# ── GapCandidate ──────────────────────────────────────────────────────────────

class TestGapCandidate:
    def _make_absent_criterion(self, required: bool = True) -> CriterionMatch:
        return _make_criterion(
            criterion_text="PMP Certification",
            dimension="certifications",
            required=required,
            status="ABSENT",
            confidence=0.05,
            match_method="absent",
        )

    def test_blocking_gap(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(required=True),
            severity="BLOCKING",
        )
        assert gap.severity == "BLOCKING"
        assert gap.suppressed is False
        assert gap.compensating_evidence == []
        assert gap.compensating_confidence == 0.0
        assert gap.suppression_reason == ""

    def test_significant_gap_with_compensation(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(required=True),
            severity="SIGNIFICANT",
            compensating_evidence=["10 years relevant project management experience"],
            compensating_confidence=0.65,
        )
        assert gap.severity == "SIGNIFICANT"
        assert gap.compensating_confidence == 0.65
        assert not gap.suppressed  # 0.65 < 0.70 threshold

    def test_minor_gap_suppressed(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(required=False),
            severity="MINOR",
            compensating_evidence=["Strong project management background"],
            compensating_confidence=0.75,
            suppressed=True,
            suppression_reason="Strong PM experience compensates for missing cert",
        )
        assert gap.suppressed is True
        assert gap.suppression_reason != ""

    def test_all_severities(self):
        for severity in ("BLOCKING", "SIGNIFICANT", "MINOR"):
            gap = GapCandidate(
                criterion=self._make_absent_criterion(),
                severity=severity,
            )
            assert gap.severity == severity

    def test_defaults(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(),
            severity="BLOCKING",
        )
        assert gap.compensating_evidence == []
        assert gap.compensating_confidence == 0.0
        assert gap.suppressed is False
        assert gap.suppression_reason == ""

    def test_json_serialisable(self):
        gap = GapCandidate(
            criterion=self._make_absent_criterion(),
            severity="BLOCKING",
        )
        payload = _to_json(gap)
        data = json.loads(payload)
        assert data["severity"] == "BLOCKING"
        assert data["suppressed"] is False
        assert isinstance(data["criterion"], dict)
        assert data["criterion"]["status"] == "ABSENT"

    def test_criterion_embedded_correctly(self):
        criterion = self._make_absent_criterion()
        gap = GapCandidate(criterion=criterion, severity="SIGNIFICANT")
        # Access the nested criterion through the dataclass
        assert gap.criterion.criterion_text == "PMP Certification"
        assert gap.criterion.dimension == "certifications"
        assert gap.criterion.status == "ABSENT"

    def test_dataclass_fields(self):
        field_names = {f.name for f in dataclasses.fields(GapCandidate)}
        required = {
            "criterion", "severity", "compensating_evidence",
            "compensating_confidence", "suppressed", "suppression_reason",
        }
        assert required.issubset(field_names)


# ── MatchResult ───────────────────────────────────────────────────────────────

class TestMatchResult:
    def _minimal_result(self, **kwargs) -> MatchResult:
        defaults = dict(
            application_id="app-uuid-001",
            job_id="job-uuid-001",
            criteria_version="abc123def456",
        )
        defaults.update(kwargs)
        return MatchResult(**defaults)

    def test_minimal_creation(self):
        mr = self._minimal_result()
        assert mr.application_id == "app-uuid-001"
        assert mr.job_id == "job-uuid-001"
        assert mr.criteria_version == "abc123def456"

    def test_defaults(self):
        mr = self._minimal_result()
        assert mr.matches == []
        assert mr.gap_candidates == []
        assert mr.required_match_pct == 0.0
        assert mr.preferred_match_pct == 0.0
        assert mr.partial_match_pct == 0.0
        assert mr.blocking_gap_count == 0
        assert mr.algorithmic_scores == {}
        assert mr.matcher_version == "0.0.0"
        assert mr.matching_method_summary == {}

    def test_with_matches(self):
        matches = [
            _make_criterion("Excel", status="MATCHED", confidence=0.95),
            _make_criterion("Python", status="ABSENT", confidence=0.0, match_method="absent"),
        ]
        mr = self._minimal_result(
            matches=matches,
            required_match_pct=50.0,
            blocking_gap_count=1,
        )
        assert len(mr.matches) == 2
        assert mr.required_match_pct == 50.0
        assert mr.blocking_gap_count == 1

    def test_algorithmic_scores(self):
        scores = {
            "skills": 72.5,
            "experience": 85.0,
            "education": 60.0,
            "certifications": 0.0,
            "soft_skills": 65.0,
            "domain_knowledge": 70.0,
            "other": 50.0,
        }
        mr = self._minimal_result(algorithmic_scores=scores)
        assert mr.algorithmic_scores["skills"] == 72.5
        assert mr.algorithmic_scores["experience"] == 85.0

    def test_matching_method_summary(self):
        mr = self._minimal_result(
            matching_method_summary={"exact": 3, "fuzzy": 2, "absent": 1}
        )
        assert mr.matching_method_summary["exact"] == 3

    def test_json_serialisable_minimal(self):
        mr = self._minimal_result()
        payload = _to_json(mr)
        data = json.loads(payload)
        assert data["application_id"] == "app-uuid-001"
        assert data["matches"] == []
        assert data["algorithmic_scores"] == {}

    def test_json_serialisable_with_matches(self):
        criterion = _make_criterion(
            "Excel",
            supporting_evidence=["Excel proficiency listed"],
            evidence_confidence=[0.95],
        )
        gap_crit = _make_criterion(
            "PMP", dimension="certifications", status="ABSENT",
            confidence=0.0, match_method="absent",
        )
        gap = GapCandidate(criterion=gap_crit, severity="BLOCKING")
        mr = MatchResult(
            application_id="app-001",
            job_id="job-001",
            criteria_version="deadbeef",
            matches=[criterion],
            gap_candidates=[gap],
            required_match_pct=50.0,
            blocking_gap_count=1,
            algorithmic_scores={"skills": 75.0, "certifications": 0.0},
            matcher_version="1.0.0",
            matching_method_summary={"exact": 1, "absent": 1},
        )
        payload = _to_json(mr)
        data = json.loads(payload)
        assert len(data["matches"]) == 1
        assert len(data["gap_candidates"]) == 1
        assert data["gap_candidates"][0]["severity"] == "BLOCKING"
        assert data["algorithmic_scores"]["skills"] == 75.0

    def test_aggregate_statistics_types(self):
        mr = self._minimal_result(
            required_match_pct=66.7,
            preferred_match_pct=33.3,
            partial_match_pct=16.7,
            blocking_gap_count=2,
        )
        assert isinstance(mr.required_match_pct, float)
        assert isinstance(mr.preferred_match_pct, float)
        assert isinstance(mr.partial_match_pct, float)
        assert isinstance(mr.blocking_gap_count, int)

    def test_dataclass_fields(self):
        field_names = {f.name for f in dataclasses.fields(MatchResult)}
        required = {
            "application_id", "job_id", "criteria_version",
            "matches", "gap_candidates",
            "required_match_pct", "preferred_match_pct",
            "partial_match_pct", "blocking_gap_count",
            "algorithmic_scores", "matcher_version",
            "matching_method_summary",
        }
        assert required.issubset(field_names)

    def test_criteria_version_is_string(self):
        mr = self._minimal_result(criteria_version="abc123456789abcd")
        assert isinstance(mr.criteria_version, str)

    def test_multiple_gap_candidates(self):
        gaps = [
            GapCandidate(
                criterion=_make_criterion(f"Skill {i}", status="ABSENT",
                                          confidence=0.0, match_method="absent"),
                severity="BLOCKING" if i == 0 else "SIGNIFICANT",
            )
            for i in range(3)
        ]
        mr = self._minimal_result(gap_candidates=gaps, blocking_gap_count=1)
        assert len(mr.gap_candidates) == 3
        assert mr.gap_candidates[0].severity == "BLOCKING"
        assert mr.gap_candidates[1].severity == "SIGNIFICANT"


# ── CriteriaMatchEngine — Batch 2A-5 ─────────────────────────────────────────

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _skill_ev(
    name: str,
    raw: str = "",
    language: str = "en",
    confidence: float = 0.90,
    section: str = "skills",
) -> SkillEvidence:
    return SkillEvidence(
        skill_name=name,
        raw_text=raw or name,
        explicit=True,
        confidence=confidence,
        context_snippet=f"{name} proficiency",
        language=language,
        section_hint=section,
    )


def _make_cv_facts(
    skills: list[str] | None = None,
    experience_years: float = 0.0,
    education_level: str = "None",
    certifications: list[str] | None = None,
    domain_signals: list[str] | None = None,
    soft_skill_categories: list[str] | None = None,
) -> CVFacts:
    skill_evs = [_skill_ev(s) for s in (skills or [])]
    cert_evs = [
        CertificationEvidence(name=c, raw_text=c)
        for c in (certifications or [])
    ]
    domain_evs = [
        DomainSignal(domain_term=t, frequency=1, context_snippets=[f"experience with {t}"])
        for t in (domain_signals or [])
    ]
    soft_evs = [
        SoftSkillSignal(
            soft_skill_category=cat,
            evidence_phrase=f"demonstrated {cat.replace('_', ' ')}",
            confidence=0.75,
        )
        for cat in (soft_skill_categories or [])
    ]
    edu_evs = (
        [EducationEvidence(degree_level=education_level, field_of_study="", institution="", year=None)]
        if education_level != "None" else []
    )
    return CVFacts(
        language="en",
        total_char_count=1000,
        skills=skill_evs,
        experience=[],
        education=edu_evs,
        certifications=cert_evs,
        soft_skill_signals=soft_evs,
        domain_signals=domain_evs,
        total_experience_years=experience_years,
        highest_education_level=education_level,
        skill_names_normalised=[s.skill_name for s in skill_evs],
        extractor_version="1.0.0",
        extraction_method="rule_based_v1",
    )


def _make_criteria(
    required_skills: list[str] | None = None,
    preferred_skills: list[str] | None = None,
    min_years: int = 0,
    min_education: str = "None",
    certifications: list[str] | None = None,
    domain_knowledge: list[str] | None = None,
    other_requirements: list[str] | None = None,
    key_responsibilities: list[str] | None = None,
    experience_requirement_type: str | None = None,
) -> dict:
    exp_block = {
        "minimum_years": min_years,
        "relevant_roles": [],
        "key_responsibilities": key_responsibilities or [],
    }
    if experience_requirement_type:
        exp_block["requirement_type"] = experience_requirement_type

    return {
        "skills": {
            "required": required_skills or [],
            "preferred": preferred_skills or [],
        },
        "experience": exp_block,
        "education": {
            "minimum_level": min_education,
            "fields_of_study": [],
        },
        "certifications": certifications or [],
        "domain_knowledge": domain_knowledge or [],
        "other_requirements": other_requirements or [],
    }


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------

class TestCriteriaMatchEngine:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.engine = CriteriaMatchEngine()

    # ── Skill matching ─────────────────────────────────────────────────────

    def test_exact_skill_match_python(self):
        facts = _make_cv_facts(skills=["Python"])
        criteria = _make_criteria(required_skills=["Python"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if "python" in x.criterion_text.lower())
        assert m.status == "MATCHED"
        assert m.match_method == "exact"
        assert m.confidence >= 0.85

    def test_no_false_positive_computer_literacy_vs_python_docker(self):
        """'computer literacy' must NOT fuzzy-match Python or Docker."""
        facts = _make_cv_facts(skills=["Python", "Docker"])
        criteria = _make_criteria(required_skills=["computer literacy"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if "computer literacy" in x.criterion_text.lower())
        assert m.status == "ABSENT"

    def test_abbreviated_skill_matches_via_synonym(self):
        """'Excel' criterion matches 'Microsoft Excel' in CVFacts via synonym map."""
        facts = _make_cv_facts(skills=["Microsoft Excel"])
        criteria = _make_criteria(required_skills=["Excel"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if "excel" in x.criterion_text.lower())
        assert m.status == "MATCHED"

    def test_arabic_excel_matches_english_criterion(self):
        """Microsoft Excel criterion matches إكسل raw_text in CVFacts."""
        facts = CVFacts(
            language="ar",
            total_char_count=300,
            skills=[_skill_ev("Microsoft Excel", raw="إكسل", language="ar")],
            skill_names_normalised=["Microsoft Excel"],
        )
        criteria = _make_criteria(required_skills=["Microsoft Excel"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if "excel" in x.criterion_text.lower())
        assert m.status == "MATCHED"
        assert m.matched_via_translation is True
        assert m.original_cv_term == "إكسل"

    def test_arabic_word_matches_english_criterion(self):
        """Microsoft Word criterion matches وورد raw_text in CVFacts."""
        facts = CVFacts(
            language="ar",
            total_char_count=300,
            skills=[_skill_ev("Microsoft Word", raw="وورد", language="ar")],
            skill_names_normalised=["Microsoft Word"],
        )
        criteria = _make_criteria(required_skills=["Microsoft Word"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if "word" in x.criterion_text.lower())
        assert m.status == "MATCHED"
        assert m.matched_via_translation is True

    def test_required_vs_preferred_distinction(self):
        facts = _make_cv_facts(skills=["Python"])
        criteria = _make_criteria(required_skills=["Python"], preferred_skills=["Tableau"])
        result = self.engine.match(facts, criteria)
        python_m = next(x for x in result.matches if x.criterion_text == "Python")
        tableau_m = next(x for x in result.matches if x.criterion_text == "Tableau")
        assert python_m.required is True
        assert tableau_m.required is False

    def test_preferred_skill_absent_method(self):
        facts = _make_cv_facts(skills=[])
        criteria = _make_criteria(preferred_skills=["Tableau"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.criterion_text == "Tableau")
        assert m.required is False
        assert m.status == "ABSENT"

    # ── Gap counting ───────────────────────────────────────────────────────

    def test_missing_required_skill_increases_blocking_gap_count(self):
        facts = _make_cv_facts(skills=["Python"])
        criteria = _make_criteria(required_skills=["Python", "SAP"])
        result = self.engine.match(facts, criteria)
        assert result.blocking_gap_count >= 1
        sap_gap = next(g for g in result.gap_candidates if "SAP" in g.criterion.criterion_text)
        assert sap_gap.severity == "BLOCKING"

    def test_missing_preferred_does_not_increase_blocking_gap_count(self):
        facts = _make_cv_facts(skills=["Python"])
        criteria = _make_criteria(required_skills=[], preferred_skills=["Tableau"])
        result = self.engine.match(facts, criteria)
        assert result.blocking_gap_count == 0
        tableau_gap = next(g for g in result.gap_candidates if "Tableau" in g.criterion.criterion_text)
        assert tableau_gap.severity == "MINOR"

    def test_required_match_pct_correct(self):
        facts = _make_cv_facts(skills=["Python"])
        criteria = _make_criteria(required_skills=["Python", "SAP"])
        result = self.engine.match(facts, criteria)
        assert result.required_match_pct == 50.0

    def test_preferred_match_pct_independent_of_blocking(self):
        facts = _make_cv_facts(skills=["Python"])
        criteria = _make_criteria(preferred_skills=["Python", "Tableau"])
        result = self.engine.match(facts, criteria)
        assert result.blocking_gap_count == 0
        assert result.preferred_match_pct == 50.0

    # ── Experience matching ────────────────────────────────────────────────

    def test_experience_years_matched(self):
        facts = _make_cv_facts(experience_years=6.0)
        criteria = _make_criteria(min_years=5)
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "experience")
        assert m.status == "MATCHED"

    def test_experience_years_partial(self):
        facts = _make_cv_facts(experience_years=3.0)
        criteria = _make_criteria(min_years=5)
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "experience")
        assert m.status == "PARTIAL"
        assert "3" in m.partial_reason

    def test_experience_years_absent(self):
        facts = _make_cv_facts(experience_years=0.0)
        criteria = _make_criteria(min_years=5)
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "experience")
        assert m.status == "ABSENT"

    def test_experience_years_zero_required_produces_no_criterion(self):
        facts = _make_cv_facts(experience_years=2.0)
        criteria = _make_criteria(min_years=0)
        result = self.engine.match(facts, criteria)
        exp_matches = [x for x in result.matches if x.dimension == "experience"]
        assert exp_matches == []

    def test_experience_requirement_type_preferred_insufficient_years(self):
        """Candidate below minimum years for a 'preferred' requirement should have required=False."""
        facts = _make_cv_facts(experience_years=2.0)
        criteria = _make_criteria(min_years=5, experience_requirement_type="preferred")
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "experience")
        assert m.required is False
        assert m.status in ("PARTIAL", "ABSENT")

    def test_experience_requirement_type_mandatory_insufficient_years(self):
        """Candidate below minimum years for a 'mandatory' requirement should have required=True."""
        facts = _make_cv_facts(experience_years=2.0)
        criteria = _make_criteria(min_years=5, experience_requirement_type="mandatory")
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "experience")
        assert m.required is True
        assert m.status in ("PARTIAL", "ABSENT")

    def test_experience_no_requirement_type_defaults_to_required(self):
        """Candidate below minimum years with no requirement_type field should default to required=True."""
        facts = _make_cv_facts(experience_years=2.0)
        criteria = _make_criteria(min_years=5)
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "experience")
        assert m.required is True
        assert m.status in ("PARTIAL", "ABSENT")

    def test_relevant_experience_no_role_overlap_returns_partial(self):
        """Unconditional relevance check: When job specifies relevant_roles or
        key_responsibilities, always run relevance overlap check.

        Sabrine's case: 8 years total experience, job requires 5 years and specifies
        HR-focused relevant_roles/key_responsibilities. Her roles (Laboratory Coordinator,
        Manager Assistant) don't match HR domain → should return PARTIAL, not MATCHED.
        """
        # Sabrine's experience: Laboratory Coordination, Manager Assistant
        exp_entries = [
            ExperienceEvidence(
                employer="Lab Company",
                role_title="Laboratory Coordinator",
                years=4.5,
                responsibilities=["Managed lab samples", "Prepared reports"],
            ),
            ExperienceEvidence(
                employer="Office Corp",
                role_title="Manager Assistant",
                years=3.5,
                responsibilities=["Scheduled meetings", "Handled correspondence"],
            ),
        ]
        facts = CVFacts(
            language="en",
            total_char_count=1000,
            skills=[],
            experience=exp_entries,
            education=[],
            certifications=[],
            soft_skill_signals=[],
            domain_signals=[],
            total_experience_years=8.0,
            highest_education_level="None",
            skill_names_normalised=[],
            extractor_version="1.0.0",
            extraction_method="rule_based_v1",
        )

        # Job specifies relevant_roles and key_responsibilities → triggers relevance check
        criteria = {
            "skills": {"required": [], "preferred": []},
            "experience": {
                "minimum_years": 5,
                "requirement_type": "preferred",
                "relevant_roles": ["HR officer", "HR administrator", "HR coordinator"],
                "key_responsibilities": [
                    "Manage HR operations",
                    "Recruit candidates",
                    "Handle payroll",
                ],
            },
            "education": {"minimum_level": "None", "fields_of_study": []},
            "certifications": [],
            "domain_knowledge": [],
            "other_requirements": [],
        }

        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "experience")

        # Should be PARTIAL: years >= 5 but domain keywords (HR) don't overlap
        assert m.status == "PARTIAL", f"Expected PARTIAL, got {m.status}"
        assert m.confidence < 0.60, f"Expected confidence < 0.60, got {m.confidence}"
        assert "relevance" in m.partial_reason.lower(), \
            f"Expected 'relevance' in partial_reason, got: {m.partial_reason}"

    def test_relevant_experience_with_role_overlap_returns_matched(self):
        """Unconditional relevance check positive case: Candidate has sufficient years
        AND domain keywords match.

        Candidate with HR Manager role matching HR domain keywords should return MATCHED.
        """
        exp_entries = [
            ExperienceEvidence(
                employer="HR Corp",
                role_title="HR Manager",
                years=6.0,
                responsibilities=[
                    "Managed recruitment processes",
                    "Handled payroll and benefits",
                    "Conducted performance reviews",
                ],
            ),
        ]
        facts = CVFacts(
            language="en",
            total_char_count=1000,
            skills=[],
            experience=exp_entries,
            education=[],
            certifications=[],
            soft_skill_signals=[],
            domain_signals=[],
            total_experience_years=6.0,
            highest_education_level="None",
            skill_names_normalised=[],
            extractor_version="1.0.0",
            extraction_method="rule_based_v1",
        )

        # Job specifies relevant_roles and key_responsibilities with HR domain keywords
        criteria = {
            "skills": {"required": [], "preferred": []},
            "experience": {
                "minimum_years": 5,
                "relevant_roles": ["HR officer", "HR manager", "HR coordinator"],
                "key_responsibilities": [
                    "Manage HR operations",
                    "Recruit candidates",
                    "Handle payroll",
                ],
            },
            "education": {"minimum_level": "None", "fields_of_study": []},
            "certifications": [],
            "domain_knowledge": [],
            "other_requirements": [],
        }

        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "experience")

        # Should be MATCHED because both duration and relevance satisfied
        assert m.status == "MATCHED", f"Expected MATCHED, got {m.status}"
        assert m.confidence >= 0.60, f"Expected confidence >= 0.60, got {m.confidence}"

    def test_experience_domain_keywords_distinguish_accounting_vs_hr(self):
        """Domain keywords should prevent 'Accounting Manager' from matching 'HR Manager' requirement."""
        facts = CVFacts(
            language="en",
            total_char_count=500,
            experience=[
                ExperienceEvidence(
                    employer="Acme Corp",
                    role_title="Manager",  # Generic title
                    years=5.0,
                    responsibilities=[
                        "Managed financial records and budgets",  # Accounting domain
                        "Performed audits",
                        "Led accounting team"
                    ],
                    raw_text="Manager - Accounting (2019-2024): Managed financial records..."
                )
            ],
            total_experience_years=5.0,
            education=[],
            highest_education_level="None",
            certifications=[],
        )
        criteria = {
            "skills": {"required": [], "preferred": []},
            "experience": {
                "minimum_years": 3,
                "relevant_roles": ["HR Manager", "Recruiter"],  # HR domain
                "key_responsibilities": ["recruitment", "hiring", "employee relations"]
            },
            "education": {"minimum_level": "None"},
            "certifications": [],
            "domain_knowledge": [],
            "other_requirements": [],
        }
        engine = CriteriaMatchEngine()
        result = engine.match(facts, criteria)

        exp_matches = [x for x in result.matches if x.dimension == "experience"]
        assert len(exp_matches) == 1
        m = exp_matches[0]
        # Should be PARTIAL because years match but domain is wrong (accounting vs HR)
        assert m.status == "PARTIAL", f"Expected PARTIAL, got {m.status}"
        assert "relevance to role not verified" in m.partial_reason

    def test_pure_duration_no_relevance_data_falls_back_to_numeric(self):
        """Fallback to pure numeric: When relevant_roles and key_responsibilities are
        both empty/missing, apply only numeric comparison.

        Same experience data as negative case, but job criteria has no relevance data
        → should match by duration alone (8 >= 5) → MATCHED.
        """
        # Same Sabrine case but job has no relevance data
        exp_entries = [
            ExperienceEvidence(
                employer="Lab Company",
                role_title="Laboratory Coordinator",
                years=4.5,
                responsibilities=["Managed lab samples"],
            ),
            ExperienceEvidence(
                employer="Office Corp",
                role_title="Manager Assistant",
                years=3.5,
                responsibilities=["Scheduled meetings"],
            ),
        ]
        facts = CVFacts(
            language="en",
            total_char_count=1000,
            skills=[],
            experience=exp_entries,
            education=[],
            certifications=[],
            soft_skill_signals=[],
            domain_signals=[],
            total_experience_years=8.0,
            highest_education_level="None",
            skill_names_normalised=[],
            extractor_version="1.0.0",
            extraction_method="rule_based_v1",
        )

        # Job requires 5 years with NO relevant_roles or key_responsibilities
        # → no relevance data available, fall back to pure numeric
        criteria = {
            "skills": {"required": [], "preferred": []},
            "experience": {
                "minimum_years": 5,
                # NOTE: No relevant_roles or key_responsibilities
            },
            "education": {"minimum_level": "None", "fields_of_study": []},
            "certifications": [],
            "domain_knowledge": [],
            "other_requirements": [],
        }

        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "experience")

        # Should be MATCHED: no relevance data, pure numeric comparison (8 >= 5)
        assert m.status == "MATCHED", f"Expected MATCHED, got {m.status}"
        assert m.confidence >= 0.85, f"Expected high confidence, got {m.confidence}"

    # ── Education matching ─────────────────────────────────────────────────

    def test_education_matched(self):
        facts = _make_cv_facts(education_level="Bachelor's")
        criteria = _make_criteria(min_education="Bachelor's")
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "education")
        assert m.status == "MATCHED"

    def test_education_above_requirement_is_matched(self):
        facts = _make_cv_facts(education_level="Master's")
        criteria = _make_criteria(min_education="Bachelor's")
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "education")
        assert m.status == "MATCHED"

    def test_education_partial_one_level_below(self):
        facts = _make_cv_facts(education_level="Diploma")
        criteria = _make_criteria(min_education="Bachelor's")
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "education")
        assert m.status == "PARTIAL"

    def test_education_absent_far_below(self):
        facts = _make_cv_facts(education_level="High School")
        criteria = _make_criteria(min_education="Master's")
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "education")
        assert m.status == "ABSENT"

    def test_education_none_required_produces_no_criterion(self):
        facts = _make_cv_facts(education_level="Bachelor's")
        criteria = _make_criteria(min_education="None")
        result = self.engine.match(facts, criteria)
        edu_matches = [x for x in result.matches if x.dimension == "education"]
        assert edu_matches == []

    # ── Field of study matching (NEW) ──────────────────────────────────────

    def test_education_field_of_study_exact_match(self):
        """Field of study: Computer Science matches Computer Science in CV."""
        facts = CVFacts(
            language="en",
            total_char_count=500,
            education=[
                EducationEvidence(
                    degree_level="Bachelor's",
                    field_of_study="Computer Science",
                    institution="MIT",
                    year=2020,
                    raw_text="BS Computer Science, MIT (2020)"
                )
            ],
            highest_education_level="Bachelor's",
        )
        criteria = {
            "skills": {"required": [], "preferred": []},
            "experience": {"minimum_years": 0, "relevant_roles": [], "key_responsibilities": []},
            "education": {
                "minimum_level": "Bachelor's",
                "fields_of_study": ["Computer Science"],
            },
            "certifications": [],
            "domain_knowledge": [],
            "other_requirements": [],
        }
        engine = CriteriaMatchEngine()
        result = engine.match(facts, criteria)

        # Should have 2 education matches: degree level + field of study
        edu_matches = [x for x in result.matches if x.dimension == "education"]
        assert len(edu_matches) == 2

        # Degree level should be MATCHED
        level_match = next(x for x in edu_matches if "Minimum education" in x.criterion_text)
        assert level_match.status == "MATCHED"

        # Field of study should be MATCHED
        field_match = next(x for x in edu_matches if "Field of study" in x.criterion_text)
        assert field_match.status == "MATCHED"

    def test_education_field_of_study_mismatch(self):
        """Field mismatch: Bachelor's in Business Admin should NOT match Computer Science requirement."""
        facts = CVFacts(
            language="en",
            total_char_count=500,
            education=[
                EducationEvidence(
                    degree_level="Bachelor's",
                    field_of_study="Business Administration",
                    institution="State University",
                    year=2015,
                    raw_text="BA Business Administration, State University (2015)"
                )
            ],
            highest_education_level="Bachelor's",
        )
        criteria = {
            "skills": {"required": [], "preferred": []},
            "experience": {"minimum_years": 0, "relevant_roles": [], "key_responsibilities": []},
            "education": {
                "minimum_level": "Bachelor's",
                "fields_of_study": ["Computer Science"],
            },
            "certifications": [],
            "domain_knowledge": [],
            "other_requirements": [],
        }
        engine = CriteriaMatchEngine()
        result = engine.match(facts, criteria)

        edu_matches = [x for x in result.matches if x.dimension == "education"]
        assert len(edu_matches) == 2

        # Degree level should be MATCHED
        level_match = next(x for x in edu_matches if "Minimum education" in x.criterion_text)
        assert level_match.status == "MATCHED"

        # Field of study should be ABSENT (different field)
        field_match = next(x for x in edu_matches if "Field of study" in x.criterion_text)
        assert field_match.status == "ABSENT"
        assert field_match.confidence == 0.0

    def test_education_field_of_study_partial_match(self):
        """Fuzzy match: 'Computer Science' should partially match 'CS' or similar variants."""
        facts = CVFacts(
            language="en",
            total_char_count=500,
            education=[
                EducationEvidence(
                    degree_level="Bachelor's",
                    field_of_study="BS Computer Science and Engineering",
                    institution="Stanford",
                    year=2018,
                    raw_text="BS Computer Science and Engineering, Stanford (2018)"
                )
            ],
            highest_education_level="Bachelor's",
        )
        criteria = {
            "skills": {"required": [], "preferred": []},
            "experience": {"minimum_years": 0, "relevant_roles": [], "key_responsibilities": []},
            "education": {
                "minimum_level": "Bachelor's",
                "fields_of_study": ["Computer Science"],
            },
            "certifications": [],
            "domain_knowledge": [],
            "other_requirements": [],
        }
        engine = CriteriaMatchEngine()
        result = engine.match(facts, criteria)

        edu_matches = [x for x in result.matches if x.dimension == "education"]
        field_match = next(x for x in edu_matches if "Field of study" in x.criterion_text)
        # Should be MATCHED because "Computer Science" is core to the field
        assert field_match.status in ("MATCHED", "PARTIAL")

    def test_education_field_of_study_missing_in_cv(self):
        """When CV has no field of study but job requires it, should be ABSENT."""
        facts = CVFacts(
            language="en",
            total_char_count=500,
            education=[
                EducationEvidence(
                    degree_level="Bachelor's",
                    field_of_study="",  # Missing field
                    institution="Some University",
                    year=2010,
                    raw_text="Bachelor Degree, Some University (2010)"
                )
            ],
            highest_education_level="Bachelor's",
        )
        criteria = {
            "skills": {"required": [], "preferred": []},
            "experience": {"minimum_years": 0, "relevant_roles": [], "key_responsibilities": []},
            "education": {
                "minimum_level": "Bachelor's",
                "fields_of_study": ["Computer Science"],
            },
            "certifications": [],
            "domain_knowledge": [],
            "other_requirements": [],
        }
        engine = CriteriaMatchEngine()
        result = engine.match(facts, criteria)

        edu_matches = [x for x in result.matches if x.dimension == "education"]
        field_match = next(x for x in edu_matches if "Field of study" in x.criterion_text)
        assert field_match.status == "ABSENT"
        assert "No field of study information" in field_match.partial_reason

    # ── Certification matching ─────────────────────────────────────────────

    def test_certification_exact_match(self):
        facts = _make_cv_facts(certifications=["PMP"])
        criteria = _make_criteria(certifications=["PMP"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "certifications")
        assert m.status == "MATCHED"
        assert m.match_method == "exact"

    def test_certification_absent(self):
        facts = _make_cv_facts(certifications=[])
        criteria = _make_criteria(certifications=["PMP"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "certifications")
        assert m.status == "ABSENT"

    # ── Domain signal matching ─────────────────────────────────────────────

    def test_domain_signal_exact_match(self):
        facts = _make_cv_facts(domain_signals=["records management"])
        criteria = _make_criteria(domain_knowledge=["records management"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "domain_knowledge")
        assert m.status == "MATCHED"
        assert m.match_method == "exact"

    def test_domain_signal_absent(self):
        facts = _make_cv_facts(domain_signals=[])
        criteria = _make_criteria(domain_knowledge=["records management"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.dimension == "domain_knowledge")
        assert m.status == "ABSENT"

    # ── Soft skill matching ────────────────────────────────────────────────

    def test_soft_skill_signal_match_leadership(self):
        facts = _make_cv_facts(soft_skill_categories=["leadership"])
        criteria = _make_criteria(key_responsibilities=["team leadership required"])
        result = self.engine.match(facts, criteria)
        soft = [x for x in result.matches if x.dimension == "soft_skills"]
        leadership = [x for x in soft if "leadership" in x.criterion_text.lower()]
        assert leadership, "leadership criterion should be generated from key_responsibilities"
        assert leadership[0].status in ("MATCHED", "PARTIAL")

    def test_soft_skill_absent_when_not_in_cv(self):
        facts = _make_cv_facts(soft_skill_categories=[])
        criteria = _make_criteria(key_responsibilities=["team leadership required"])
        result = self.engine.match(facts, criteria)
        soft = [x for x in result.matches if x.dimension == "soft_skills"]
        leadership = [x for x in soft if "leadership" in x.criterion_text.lower()]
        assert leadership
        assert leadership[0].status == "ABSENT"

    def test_soft_skill_no_criteria_uses_signal_fallback(self):
        # When no criteria mention soft skills, CV-detected signals still
        # produce preferred=False matches (so soft_skills score is non-zero).
        facts = _make_cv_facts(soft_skill_categories=["leadership"])
        criteria = _make_criteria()  # no key_responsibilities
        result = self.engine.match(facts, criteria)
        soft = [x for x in result.matches if x.dimension == "soft_skills"]
        assert len(soft) >= 1
        assert soft[0].required is False
        assert soft[0].status == "MATCHED"

    # ── Algorithmic scores ─────────────────────────────────────────────────

    def test_algorithmic_scores_all_dimensions_present(self):
        facts = _make_cv_facts(skills=["Python"])
        criteria = _make_criteria(required_skills=["Python"])
        result = self.engine.match(facts, criteria)
        for dim in ("skills", "experience", "education", "certifications",
                    "soft_skills", "domain_knowledge", "other"):
            assert dim in result.algorithmic_scores

    def test_algorithmic_scores_in_range(self):
        facts = _make_cv_facts(skills=["Python", "SQL"], experience_years=5.0)
        criteria = _make_criteria(required_skills=["Python"], min_years=5)
        result = self.engine.match(facts, criteria)
        for dim, score in result.algorithmic_scores.items():
            assert 0.0 <= score <= 100.0, f"Score for {dim} out of range: {score}"

    def test_algorithmic_score_skills_high_when_all_matched(self):
        facts = _make_cv_facts(skills=["Python", "SQL"])
        criteria = _make_criteria(required_skills=["Python", "SQL"])
        result = self.engine.match(facts, criteria)
        assert result.algorithmic_scores["skills"] >= 80.0

    def test_algorithmic_score_skills_zero_when_all_absent(self):
        facts = _make_cv_facts(skills=[])
        criteria = _make_criteria(required_skills=["Python", "SAP"])
        result = self.engine.match(facts, criteria)
        assert result.algorithmic_scores["skills"] == 0.0

    # ── MatchResult serialisation ──────────────────────────────────────────

    def test_match_result_serializes_with_evidence_serialiser(self):
        from services.evidence_serialiser import matchresult_to_dict, matchresult_from_dict
        facts = _make_cv_facts(
            skills=["Python"],
            experience_years=3.0,
            education_level="Bachelor's",
        )
        criteria = _make_criteria(
            required_skills=["Python"],
            min_years=5,
            min_education="Bachelor's",
        )
        result = self.engine.match(facts, criteria)

        d = matchresult_to_dict(result)
        assert "_schema" in d
        assert isinstance(d["matches"], list)

        restored = matchresult_from_dict(d)
        assert len(restored.matches) == len(result.matches)
        assert restored.matcher_version == result.matcher_version
        assert restored.blocking_gap_count == result.blocking_gap_count

    # ── Misc / edge cases ──────────────────────────────────────────────────

    def test_empty_criteria_returns_valid_result(self):
        facts = _make_cv_facts(skills=["Python"])
        result = self.engine.match(facts, {})
        assert isinstance(result, MatchResult)
        assert result.matches == []
        assert result.blocking_gap_count == 0

    def test_none_criteria_handled(self):
        facts = _make_cv_facts()
        result = self.engine.match(facts, None)
        assert isinstance(result, MatchResult)

    def test_criteria_version_is_non_empty_string(self):
        facts = _make_cv_facts()
        criteria = _make_criteria(required_skills=["Python"])
        result = self.engine.match(facts, criteria)
        assert isinstance(result.criteria_version, str)
        assert len(result.criteria_version) == 16

    def test_matcher_version(self):
        result = self.engine.match(_make_cv_facts(), {})
        assert result.matcher_version == "1.1.0"

    def test_gap_candidates_ordered_blocking_first(self):
        facts = _make_cv_facts(skills=[])
        criteria = _make_criteria(required_skills=["SAP"], preferred_skills=["Tableau"])
        result = self.engine.match(facts, criteria)
        _ORDER = {"BLOCKING": 0, "SIGNIFICANT": 1, "MINOR": 2}
        severities = [_ORDER[g.severity] for g in result.gap_candidates]
        assert severities == sorted(severities)

    def test_matching_method_summary_populated(self):
        facts = _make_cv_facts(skills=["Python"])
        criteria = _make_criteria(required_skills=["Python", "SAP"])
        result = self.engine.match(facts, criteria)
        assert result.matching_method_summary
        assert "absent" in result.matching_method_summary

    def test_supporting_evidence_populated_for_matched_skill(self):
        facts = _make_cv_facts(skills=["Python"])
        criteria = _make_criteria(required_skills=["Python"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if x.criterion_text == "Python")
        assert m.supporting_evidence  # should have a context snippet

    # ── Batch 2B-2: matching quality fixes ────────────────────────────────────

    # Task 2 — computer literacy umbrella matching

    def test_computer_literacy_matched_by_excel_and_word(self):
        """Computer literacy MATCHED when CV has multiple office tools."""
        facts = _make_cv_facts(skills=["Microsoft Excel", "Microsoft Word", "Microsoft Office"])
        criteria = _make_criteria(required_skills=["Computer literacy"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if "computer literacy" in x.criterion_text.lower())
        assert m.status in ("MATCHED", "PARTIAL")
        assert m.match_method == "inferred"

    def test_computer_literacy_matched_by_ms_office_alone(self):
        """Microsoft Office alone satisfies computer literacy."""
        facts = _make_cv_facts(skills=["Microsoft Office"])
        criteria = _make_criteria(required_skills=["Computer literacy"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if "computer literacy" in x.criterion_text.lower())
        assert m.status in ("MATCHED", "PARTIAL")

    def test_computer_literacy_absent_when_only_dev_tools_present(self):
        """Python and Docker do NOT satisfy computer literacy (preserved regression)."""
        facts = _make_cv_facts(skills=["Python", "Docker"])
        criteria = _make_criteria(required_skills=["Computer literacy"])
        result = self.engine.match(facts, criteria)
        m = next(x for x in result.matches if "computer literacy" in x.criterion_text.lower())
        assert m.status == "ABSENT"

    def test_computer_literacy_blocking_gap_count_reduced(self):
        """Candidate with office tools should have 0 blocking gaps for computer literacy."""
        facts = _make_cv_facts(skills=["Microsoft Excel", "Microsoft Word"])
        criteria = _make_criteria(required_skills=["Computer literacy"])
        result = self.engine.match(facts, criteria)
        assert result.blocking_gap_count == 0

    # Task 3 — Word detection

    def test_word_criterion_matched_when_word_in_cv(self):
        """'Strong proficiency in Microsoft Word' MATCHED when CV has Word."""
        facts = _make_cv_facts(skills=["Microsoft Word"])
        criteria = _make_criteria(required_skills=["Strong proficiency in Microsoft Word"])
        result = self.engine.match(facts, criteria)
        m = result.matches[0]
        assert m.status in ("MATCHED", "PARTIAL")

    def test_microsoft_word_criterion_matched_directly(self):
        """Plain 'Microsoft Word' criterion matches 'Microsoft Word' in CV."""
        facts = _make_cv_facts(skills=["Microsoft Word"])
        criteria = _make_criteria(required_skills=["Microsoft Word"])
        result = self.engine.match(facts, criteria)
        m = result.matches[0]
        assert m.status == "MATCHED"

    # Task 4 — confidentiality / transferable evidence

    def test_confidentiality_criterion_matched_via_domain_signal(self):
        """Confidentiality requirement MATCHED when CV has confidentiality domain signal."""
        facts = _make_cv_facts(
            domain_signals=["confidentiality", "information security"],
        )
        criteria = _make_criteria(
            other_requirements=["Adhere to confidentiality and non-disclosure agreements"]
        )
        result = self.engine.match(facts, criteria)
        other_matches = [x for x in result.matches if x.dimension == "other"]
        assert other_matches, "No other_requirements matches generated"
        assert other_matches[0].status in ("MATCHED", "PARTIAL")

    def test_information_security_domain_supports_confidentiality_requirement(self):
        """Information security domain signal supports a confidentiality requirement."""
        facts = _make_cv_facts(domain_signals=["information security", "compliance"])
        criteria = _make_criteria(
            other_requirements=["Maintain confidentiality of sensitive records"]
        )
        result = self.engine.match(facts, criteria)
        other_matches = [x for x in result.matches if x.dimension == "other"]
        # information security is in the cv_pool and 'confidentiality' is a token
        # in the requirement → token_set_ratio should be high enough to match
        assert other_matches[0].status in ("MATCHED", "PARTIAL")

    # Task 5 — soft skill scoring

    def test_soft_skills_score_nonzero_when_signals_detected(self):
        """soft_skills algorithmic score > 0 when CVFacts has soft skill signals."""
        facts = _make_cv_facts(soft_skill_categories=["communication", "leadership"])
        criteria = _make_criteria()  # no explicit soft skill criteria
        result = self.engine.match(facts, criteria)
        assert result.algorithmic_scores["soft_skills"] > 0.0

    def test_soft_skills_score_reflects_signal_confidence(self):
        """soft_skills score should roughly match signal confidence × 100."""
        facts = _make_cv_facts(soft_skill_categories=["communication"])
        criteria = _make_criteria()
        result = self.engine.match(facts, criteria)
        # Signal confidence is 0.75 in _make_cv_facts fixture → score ≈ 75
        score = result.algorithmic_scores["soft_skills"]
        assert 50.0 <= score <= 100.0

    def test_soft_skills_criteria_driven_plus_fallback_no_duplicates(self):
        """Criteria-driven match and fallback do not produce duplicate categories."""
        facts = _make_cv_facts(soft_skill_categories=["communication"])
        criteria = _make_criteria(
            key_responsibilities=["Strong communication and presentation skills required"]
        )
        result = self.engine.match(facts, criteria)
        soft = [x for x in result.matches if x.dimension == "soft_skills"]
        categories = [m.criterion_text for m in soft]
        # "Communication skills" should appear exactly once
        comm = [c for c in categories if "communication" in c.lower()]
        assert len(comm) == 1

    # Task 6 — blocking gap count

    def test_blocking_gap_reduced_when_broad_skills_satisfied(self):
        """Candidate with office tools: computer literacy is not a blocking gap."""
        facts = _make_cv_facts(
            skills=["Microsoft Excel", "Microsoft Word", "Microsoft Office"],
            experience_years=10.0,
        )
        criteria = _make_criteria(
            required_skills=["Computer literacy", "Microsoft Excel"],
            min_years=5,
        )
        result = self.engine.match(facts, criteria)
        # Neither computer literacy nor Excel should be ABSENT
        assert result.blocking_gap_count == 0

    def test_blocking_count_only_increments_for_genuinely_absent(self):
        """A genuinely absent required skill still creates a blocking gap."""
        facts = _make_cv_facts(skills=["Microsoft Excel"])
        criteria = _make_criteria(required_skills=["Computer literacy", "SAP"])
        result = self.engine.match(facts, criteria)
        # Computer literacy: satisfied (Excel present); SAP: absent → 1 blocking gap
        assert result.blocking_gap_count == 1
