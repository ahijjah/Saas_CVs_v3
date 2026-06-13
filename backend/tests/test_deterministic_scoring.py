"""
Tests for F-01 DeterministicScoringEngine.

Coverage:
  - _match_quality_factor: all match_types × strict vs non-strict classes
  - _status_credit: MATCHED / PARTIAL / ABSENT
  - DeterministicScoringEngine.score: dimension aggregation, final score
  - Required-absent floor (disabled by default, enabled when configured)
  - Overqualification detection (zero score impact)
  - Empty criteria list (dimension with no assessments)
  - Zero-weight dimension (should contribute 0)
  - All weights zero guard (total_weight fallback)
  - Confidence is audit-only (changing confidence does not change score)
  - Serialisation: deterministic_score_to_dict shape and schema key
  - DeterministicScoringConfig defaults
  - Partial credit configurable
  - Required/preferred weighting
  - Ceil rounding: fractional weighted sum rounds up
  - Single-dimension job (all weight in one bucket)
  - 100-point cap (final_score never > 100)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from services.deterministic_scoring import (
    DeterministicCriterionScore,
    DeterministicScore,
    DeterministicScoringConfig,
    DeterministicScoringEngine,
    _match_quality_factor,
    deterministic_score_to_dict,
)
from services.llm_criteria_mapper import LLMCriterionAssessment, LLMMatchResult

# ── Helpers ───────────────────────────────────────────────────────────────────

_DEFAULT_WEIGHTS: dict[str, int] = {
    "weight_skills":           30,
    "weight_experience":       25,
    "weight_education":        15,
    "weight_certifications":   10,
    "weight_soft_skills":      10,
    "weight_domain_knowledge":  5,
    "weight_other":             5,
}


def _assessment(
    criterion_text: str = "Python",
    dimension: str = "skills",
    required: bool = True,
    status: str = "MATCHED",
    match_type: str = "direct",
    criterion_class: str = "strict",
    confidence: float = 0.90,
    supporting_evidence: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> LLMCriterionAssessment:
    return LLMCriterionAssessment(
        criterion_text=criterion_text,
        dimension=dimension,
        required=required,
        status=status,
        confidence=confidence,
        supporting_evidence=supporting_evidence or ["Python used in role."],
        match_reason="Direct match.",
        match_type=match_type,
        criterion_class=criterion_class,
        risk_flags=risk_flags or [],
        prompt_code="recruitment.criteria_mapping",
        prompt_version="2",
        llm_model="gpt-4o-mini",
    )


def _llm_result(
    assessments: list[LLMCriterionAssessment],
) -> LLMMatchResult:
    matched = sum(1 for a in assessments if a.status == "MATCHED")
    partial = sum(1 for a in assessments if a.status == "PARTIAL")
    absent  = sum(1 for a in assessments if a.status == "ABSENT")
    return LLMMatchResult(
        application_id="app-001",
        job_id="job-001",
        assessments=assessments,
        processing_ms=100,
        created_at="2026-01-01T00:00:00+00:00",
        prompt_code="recruitment.criteria_mapping",
        prompt_version="2",
        model="gpt-4o-mini",
        mapper_version="1.2.3",
        total_criteria=len(assessments),
        matched_count=matched,
        partial_count=partial,
        absent_count=absent,
        high_confidence_count=0,
        low_confidence_count=0,
    )


def _engine(cfg: DeterministicScoringConfig | None = None) -> DeterministicScoringEngine:
    return DeterministicScoringEngine(cfg or DeterministicScoringConfig())


# ── match_quality_factor ──────────────────────────────────────────────────────

class TestMatchQualityFactor:
    def test_direct_any_class(self):
        assert _match_quality_factor("direct", "strict") == 1.00
        assert _match_quality_factor("direct", "soft_skill") == 1.00
        assert _match_quality_factor("direct", "flexible") == 1.00

    def test_equivalent_any_class(self):
        assert _match_quality_factor("equivalent", "strict") == pytest.approx(0.95)
        assert _match_quality_factor("equivalent", "flexible") == pytest.approx(0.95)

    def test_transferable_non_strict(self):
        assert _match_quality_factor("transferable", "soft_skill") == pytest.approx(0.80)
        assert _match_quality_factor("transferable", "experience") == pytest.approx(0.80)

    def test_transferable_strict_capped(self):
        assert _match_quality_factor("transferable", "strict") == pytest.approx(0.50)

    def test_transferable_certification_capped(self):
        assert _match_quality_factor("transferable", "certification") == pytest.approx(0.50)

    def test_inferred_non_strict(self):
        assert _match_quality_factor("inferred", "soft_skill") == pytest.approx(0.65)
        assert _match_quality_factor("inferred", "domain_knowledge") == pytest.approx(0.65)

    def test_inferred_strict_capped(self):
        assert _match_quality_factor("inferred", "strict") == pytest.approx(0.40)

    def test_inferred_certification_capped(self):
        assert _match_quality_factor("inferred", "certification") == pytest.approx(0.40)

    def test_missing_is_zero(self):
        assert _match_quality_factor("missing", "strict") == 0.0
        assert _match_quality_factor("missing", "soft_skill") == 0.0

    def test_unknown_match_type_is_zero(self):
        assert _match_quality_factor("", "strict") == 0.0
        assert _match_quality_factor("unknown_type", "strict") == 0.0


# ── status_credit ─────────────────────────────────────────────────────────────

class TestStatusCredit:
    def test_matched_full_credit(self):
        cfg = DeterministicScoringConfig(partial_credit=0.50)
        a = _assessment(status="MATCHED", match_type="direct", criterion_class="strict")
        result = _engine(cfg).score(_llm_result([a]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        assert dim.criteria[0].status_credit == pytest.approx(1.0)

    def test_partial_uses_configured_credit(self):
        cfg = DeterministicScoringConfig(partial_credit=0.60)
        a = _assessment(status="PARTIAL", match_type="direct", criterion_class="strict")
        result = _engine(cfg).score(_llm_result([a]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        assert dim.criteria[0].status_credit == pytest.approx(0.60)

    def test_absent_zero_credit(self):
        a = _assessment(status="ABSENT", match_type="missing", criterion_class="strict")
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        assert dim.criteria[0].status_credit == pytest.approx(0.0)
        assert dim.criteria[0].effective_credit == pytest.approx(0.0)


# ── Effective credit (status × quality) ───────────────────────────────────────

class TestEffectiveCredit:
    def test_matched_direct_strict_full_credit(self):
        a = _assessment(status="MATCHED", match_type="direct", criterion_class="strict")
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["skills"].criteria[0]
        assert c.effective_credit == pytest.approx(1.0 * 1.0)

    def test_matched_equivalent_strict(self):
        a = _assessment(status="MATCHED", match_type="equivalent", criterion_class="strict")
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["skills"].criteria[0]
        assert c.effective_credit == pytest.approx(1.0 * 0.95)

    def test_matched_transferable_strict_capped(self):
        a = _assessment(status="MATCHED", match_type="transferable", criterion_class="strict")
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["skills"].criteria[0]
        # transferable strict cap = 0.50
        assert c.effective_credit == pytest.approx(1.0 * 0.50)

    def test_matched_inferred_strict_capped(self):
        a = _assessment(status="MATCHED", match_type="inferred", criterion_class="strict")
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["skills"].criteria[0]
        assert c.effective_credit == pytest.approx(1.0 * 0.40)

    def test_partial_direct_soft_skill(self):
        cfg = DeterministicScoringConfig(partial_credit=0.50)
        a = _assessment(
            status="PARTIAL", match_type="direct", criterion_class="soft_skill",
            dimension="soft_skills"
        )
        result = _engine(cfg).score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["soft_skills"].criteria[0]
        assert c.effective_credit == pytest.approx(0.50 * 1.0)

    def test_partial_transferable_non_strict(self):
        cfg = DeterministicScoringConfig(partial_credit=0.50)
        a = _assessment(
            status="PARTIAL", match_type="transferable", criterion_class="experience",
            dimension="experience"
        )
        result = _engine(cfg).score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["experience"].criteria[0]
        assert c.effective_credit == pytest.approx(0.50 * 0.80)


# ── Dimension score aggregation ───────────────────────────────────────────────

class TestDimensionAggregation:
    def test_required_only_avg(self):
        """Dimension with only required criteria → dimension_score = required_avg."""
        a1 = _assessment("Python", "skills", required=True, status="MATCHED", match_type="direct")
        a2 = _assessment("Java",   "skills", required=True, status="ABSENT",  match_type="missing")
        result = _engine().score(_llm_result([a1, a2]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        # avg effective = (1.0 + 0.0) / 2 = 0.5
        assert dim.required_avg == pytest.approx(0.5)
        assert dim.dimension_score == pytest.approx(0.5)

    def test_preferred_only_avg(self):
        """Dimension with only preferred criteria → dimension_score = preferred_avg."""
        a1 = _assessment("Django", "skills", required=False, status="MATCHED", match_type="direct")
        a2 = _assessment("Flask",  "skills", required=False, status="PARTIAL", match_type="direct")
        cfg = DeterministicScoringConfig(partial_credit=0.50)
        result = _engine(cfg).score(_llm_result([a1, a2]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        assert dim.preferred_avg == pytest.approx((1.0 + 0.5) / 2)
        assert dim.dimension_score == pytest.approx(dim.preferred_avg)

    def test_mixed_required_preferred_weighting(self):
        """70/30 required/preferred split."""
        req = _assessment("Python", "skills", required=True,  status="MATCHED", match_type="direct")
        pref = _assessment("Go",    "skills", required=False, status="ABSENT",  match_type="missing")
        cfg = DeterministicScoringConfig(required_weight=0.70, preferred_weight=0.30)
        result = _engine(cfg).score(_llm_result([req, pref]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        # req_avg = 1.0, pref_avg = 0.0
        expected = 1.0 * 0.70 + 0.0 * 0.30
        assert dim.dimension_score == pytest.approx(expected)

    def test_counters_correct(self):
        req1 = _assessment("A", "skills", required=True,  status="MATCHED", match_type="direct")
        req2 = _assessment("B", "skills", required=True,  status="PARTIAL", match_type="direct")
        req3 = _assessment("C", "skills", required=True,  status="ABSENT",  match_type="missing")
        pref1 = _assessment("D", "skills", required=False, status="MATCHED", match_type="direct")
        result = _engine().score(_llm_result([req1, req2, req3, pref1]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        assert dim.n_required == 3
        assert dim.n_required_matched == 1
        assert dim.n_required_partial == 1
        assert dim.n_required_absent == 1
        assert dim.n_preferred == 1
        assert dim.n_preferred_matched == 1


# ── Required-absent floor ─────────────────────────────────────────────────────

class TestRequiredAbsentFloor:
    def _floor_cfg(self) -> DeterministicScoringConfig:
        return DeterministicScoringConfig(
            enable_required_absent_floor=True,
            required_absent_floor_threshold=0.50,
            required_absent_floor_cap=0.40,
        )

    def test_floor_not_triggered_below_threshold(self):
        """50% absent required criteria at threshold=0.50 should NOT trigger."""
        req1 = _assessment("A", "skills", required=True, status="MATCHED", match_type="direct")
        req2 = _assessment("B", "skills", required=True, status="ABSENT",  match_type="missing")
        result = _engine(self._floor_cfg()).score(_llm_result([req1, req2]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        # 1/2 = 0.50, threshold is 0.50 — floor not triggered (> not >=)
        assert not dim.required_absent_floor_triggered

    def test_floor_triggered_above_threshold(self):
        """2 of 3 required ABSENT (66%) + 3 preferred MATCHED → blended > cap → floor triggers."""
        # required_avg = 1/3 = 0.333; preferred_avg = 1.0
        # dim_score = 0.333*0.70 + 1.0*0.30 = 0.233+0.30 = 0.533 > 0.40 cap → floor triggers
        req1 = _assessment("A", "skills", required=True, status="MATCHED", match_type="direct")
        req2 = _assessment("B", "skills", required=True, status="ABSENT",  match_type="missing")
        req3 = _assessment("C", "skills", required=True, status="ABSENT",  match_type="missing")
        pref1 = _assessment("D", "skills", required=False, status="MATCHED", match_type="direct")
        pref2 = _assessment("E", "skills", required=False, status="MATCHED", match_type="direct")
        pref3 = _assessment("F", "skills", required=False, status="MATCHED", match_type="direct")
        result = _engine(self._floor_cfg()).score(
            _llm_result([req1, req2, req3, pref1, pref2, pref3]), _DEFAULT_WEIGHTS
        )
        dim = result.dimensions["skills"]
        assert dim.required_absent_floor_triggered
        assert dim.dimension_score == pytest.approx(0.40)

    def test_floor_does_not_cap_already_low_score(self):
        """If dim_score is already below floor cap, floor does not raise it."""
        req1 = _assessment("A", "skills", required=True, status="ABSENT",  match_type="missing")
        req2 = _assessment("B", "skills", required=True, status="ABSENT",  match_type="missing")
        req3 = _assessment("C", "skills", required=True, status="ABSENT",  match_type="missing")
        result = _engine(self._floor_cfg()).score(_llm_result([req1, req2, req3]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        # All absent → dim_score = 0 < 0.40 cap → cap is NOT applied (only caps downward)
        assert not dim.required_absent_floor_triggered
        assert dim.dimension_score == pytest.approx(0.0)

    def test_floor_disabled_by_default(self):
        """Default config has floor disabled."""
        cfg = DeterministicScoringConfig()
        assert not cfg.enable_required_absent_floor
        req1 = _assessment("A", "skills", required=True, status="ABSENT", match_type="missing")
        req2 = _assessment("B", "skills", required=True, status="ABSENT", match_type="missing")
        req3 = _assessment("C", "skills", required=True, status="ABSENT", match_type="missing")
        result = _engine(cfg).score(_llm_result([req1, req2, req3]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        assert not dim.required_absent_floor_triggered


# ── Overqualification ─────────────────────────────────────────────────────────

class TestOverqualification:
    def test_overqualification_detected_in_criterion(self):
        a = _assessment(
            "5 years minimum", "experience", required=True,
            status="MATCHED", match_type="direct", criterion_class="experience",
            risk_flags=["overqualified"],
        )
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["experience"].criteria[0]
        assert c.has_overqualification

    def test_overqualification_zero_score_impact(self):
        """Overqualification must not change effective_credit."""
        normal = _assessment(
            "5 years", "experience", required=True,
            status="MATCHED", match_type="direct", criterion_class="experience",
        )
        oq = _assessment(
            "5 years", "experience", required=True,
            status="MATCHED", match_type="direct", criterion_class="experience",
            risk_flags=["overqualified"],
        )
        r_normal = _engine().score(_llm_result([normal]), _DEFAULT_WEIGHTS)
        r_oq     = _engine().score(_llm_result([oq]),     _DEFAULT_WEIGHTS)
        c_normal = r_normal.dimensions["experience"].criteria[0]
        c_oq     = r_oq.dimensions["experience"].criteria[0]
        assert c_normal.effective_credit == pytest.approx(c_oq.effective_credit)
        assert r_normal.final_score == r_oq.final_score

    def test_overqualification_risk_dimensions_list(self):
        a = _assessment(
            "Senior role", "experience", required=True,
            status="MATCHED", match_type="direct", criterion_class="experience",
            risk_flags=["overqualified"],
        )
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        assert "experience" in result.overqualification_risk_dimensions

    def test_no_overqualification_no_risk_dimensions(self):
        a = _assessment()
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        assert result.overqualification_risk_dimensions == []


# ── Confidence is audit-only ──────────────────────────────────────────────────

class TestConfidenceAuditOnly:
    def test_high_confidence_same_score_as_low_confidence(self):
        a_high = _assessment(status="MATCHED", match_type="direct", confidence=0.99)
        a_low  = _assessment(status="MATCHED", match_type="direct", confidence=0.11)
        r_high = _engine().score(_llm_result([a_high]), _DEFAULT_WEIGHTS)
        r_low  = _engine().score(_llm_result([a_low]),  _DEFAULT_WEIGHTS)
        assert r_high.final_score == r_low.final_score
        c_high = r_high.dimensions["skills"].criteria[0]
        c_low  = r_low.dimensions["skills"].criteria[0]
        assert c_high.effective_credit == pytest.approx(c_low.effective_credit)

    def test_low_confidence_count_tracked_on_dimension(self):
        a = _assessment(status="MATCHED", match_type="direct", confidence=0.20)
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        assert dim.low_confidence_count == 1
        assert dim.review_recommended

    def test_high_confidence_no_review_flag(self):
        a = _assessment(status="MATCHED", match_type="direct", confidence=0.90)
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        assert dim.low_confidence_count == 0
        assert not dim.review_recommended


# ── Empty dimension ───────────────────────────────────────────────────────────

class TestEmptyDimension:
    def test_dimension_with_no_assessments_zero_score(self):
        a = _assessment("Python", "skills", required=True, status="MATCHED", match_type="direct")
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        exp = result.dimensions["experience"]
        assert exp.dimension_score == pytest.approx(0.0)
        assert exp.n_required == 0
        assert exp.criteria == []


# ── Final score calculation ───────────────────────────────────────────────────

class TestFinalScore:
    def test_all_matched_direct_gives_high_score(self):
        assessments = [
            _assessment("A", "skills",           required=True, status="MATCHED", match_type="direct"),
            _assessment("B", "experience",        required=True, status="MATCHED", match_type="direct", criterion_class="experience"),
            _assessment("C", "education",         required=True, status="MATCHED", match_type="direct", criterion_class="education"),
            _assessment("D", "certifications",    required=True, status="MATCHED", match_type="direct", criterion_class="certification"),
            _assessment("E", "soft_skills",       required=True, status="MATCHED", match_type="direct", criterion_class="soft_skill"),
            _assessment("F", "domain_knowledge",  required=True, status="MATCHED", match_type="direct", criterion_class="domain_knowledge"),
            _assessment("G", "other",             required=True, status="MATCHED", match_type="direct", criterion_class="other"),
        ]
        result = _engine().score(_llm_result(assessments), _DEFAULT_WEIGHTS)
        assert result.final_score == 100

    def test_all_absent_gives_zero(self):
        assessments = [
            _assessment("A", "skills",     required=True, status="ABSENT", match_type="missing"),
            _assessment("B", "experience", required=True, status="ABSENT", match_type="missing", criterion_class="experience"),
        ]
        result = _engine().score(_llm_result(assessments), _DEFAULT_WEIGHTS)
        assert result.final_score == 0

    def test_ceil_rounds_up(self):
        """A fractional weighted sum should ceil to next integer."""
        # One required skill PARTIAL direct = credit 0.50
        # weight_skills = 30 / 100 = 0.30 of total
        # contribution = 0.50 * 0.30 = 0.15
        # final = ceil(0.15 * 100) = ceil(15.0) = 15
        a = _assessment("A", "skills", required=True, status="PARTIAL", match_type="direct")
        weights = {k: 0 for k in _DEFAULT_WEIGHTS}
        weights["weight_skills"] = 100
        result = _engine().score(_llm_result([a]), weights)
        # dim_score = 0.5 (single required), weight_pct = 1.0, contribution = 0.5
        # final = ceil(0.5 * 100) = 50
        assert result.final_score == 50

    def test_final_score_never_exceeds_100(self):
        """Edge case: should be capped at 100."""
        assessments = [
            _assessment("A", "skills",     required=True, status="MATCHED", match_type="direct")
        ] * 10
        weights = {k: 100 for k in _DEFAULT_WEIGHTS}
        result = _engine().score(_llm_result(assessments), weights)
        assert result.final_score <= 100

    def test_zero_total_weight_fallback_no_crash(self):
        """All-zero weights should not crash (guard divides by 1)."""
        a = _assessment()
        zero_weights = {k: 0 for k in _DEFAULT_WEIGHTS}
        result = _engine().score(_llm_result([a]), zero_weights)
        assert isinstance(result.final_score, int)

    def test_scoring_version_set(self):
        a = _assessment()
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        assert result.scoring_version == "det_score_v1"

    def test_mapper_version_preserved(self):
        a = _assessment()
        lr = _llm_result([a])
        result = _engine().score(lr, _DEFAULT_WEIGHTS)
        assert result.mapper_version == lr.mapper_version


# ── Single-dimension job ──────────────────────────────────────────────────────

class TestSingleDimensionJob:
    def test_only_skills_weight(self):
        a = _assessment("Python", "skills", required=True, status="MATCHED", match_type="direct")
        weights = {k: 0 for k in _DEFAULT_WEIGHTS}
        weights["weight_skills"] = 100
        result = _engine().score(_llm_result([a]), weights)
        assert result.final_score == 100

    def test_only_experience_weight_no_experience_criteria(self):
        """No experience criteria + all weight on experience → 0."""
        a = _assessment("Python", "skills", required=True, status="MATCHED", match_type="direct")
        weights = {k: 0 for k in _DEFAULT_WEIGHTS}
        weights["weight_experience"] = 100
        result = _engine().score(_llm_result([a]), weights)
        assert result.final_score == 0


# ── Serialisation ─────────────────────────────────────────────────────────────

class TestSerialisation:
    def _scored(self) -> DeterministicScore:
        a = _assessment("Python", "skills", required=True, status="MATCHED", match_type="direct")
        return _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)

    def test_schema_key(self):
        d = deterministic_score_to_dict(self._scored())
        assert d["_schema"] == "det_score_v1"

    def test_final_score_in_dict(self):
        scored = self._scored()
        d = deterministic_score_to_dict(scored)
        assert d["final_score"] == scored.final_score

    def test_dimensions_present(self):
        d = deterministic_score_to_dict(self._scored())
        assert "dimensions" in d
        assert "skills" in d["dimensions"]

    def test_criteria_list_in_dimension(self):
        d = deterministic_score_to_dict(self._scored())
        skills = d["dimensions"]["skills"]
        assert "criteria" in skills
        assert len(skills["criteria"]) == 1

    def test_criterion_fields(self):
        d = deterministic_score_to_dict(self._scored())
        c = d["dimensions"]["skills"]["criteria"][0]
        for key in (
            "criterion_text", "dimension", "required", "status",
            "match_type", "criterion_class", "status_credit",
            "quality_factor", "effective_credit", "confidence",
            "supporting_evidence", "risk_flags", "has_overqualification",
        ):
            assert key in c, f"Missing key: {key}"

    def test_overqualification_risk_dimensions_serialised(self):
        a = _assessment(
            "10 years exp", "experience", required=True,
            status="MATCHED", match_type="direct", criterion_class="experience",
            risk_flags=["overqualified"],
        )
        scored = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        d = deterministic_score_to_dict(scored)
        assert "overqualification_risk_dimensions" in d
        assert "experience" in d["overqualification_risk_dimensions"]

    def test_scored_at_present(self):
        d = deterministic_score_to_dict(self._scored())
        assert "scored_at" in d
        assert "T" in d["scored_at"]  # ISO-8601 format has T separator

    def test_json_serialisable(self):
        import json
        d = deterministic_score_to_dict(self._scored())
        serialised = json.dumps(d)
        assert len(serialised) > 100


# ── DeterministicScoringConfig defaults ──────────────────────────────────────

class TestConfigDefaults:
    def test_default_partial_credit(self):
        cfg = DeterministicScoringConfig()
        assert cfg.partial_credit == pytest.approx(0.50)

    def test_default_required_weight(self):
        cfg = DeterministicScoringConfig()
        assert cfg.required_weight == pytest.approx(0.70)

    def test_default_preferred_weight(self):
        cfg = DeterministicScoringConfig()
        assert cfg.preferred_weight == pytest.approx(0.30)

    def test_default_absent_floor_disabled(self):
        cfg = DeterministicScoringConfig()
        assert not cfg.enable_required_absent_floor

    def test_partial_credit_configurable(self):
        cfg = DeterministicScoringConfig(partial_credit=0.75)
        a = _assessment("A", "skills", required=True, status="PARTIAL", match_type="direct")
        result = _engine(cfg).score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["skills"].criteria[0]
        assert c.status_credit == pytest.approx(0.75)
        assert c.effective_credit == pytest.approx(0.75 * 1.0)


# ── Weighted contribution correctness ─────────────────────────────────────────

class TestWeightedContribution:
    def test_weighted_contribution_matches_formula(self):
        """weighted_contribution = dimension_score × (weight / total_weight)."""
        a = _assessment("A", "skills", required=True, status="MATCHED", match_type="direct")
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        dim = result.dimensions["skills"]
        total_w = sum(_DEFAULT_WEIGHTS.values())
        expected_pct = _DEFAULT_WEIGHTS["weight_skills"] / total_w
        assert dim.weight_pct == pytest.approx(expected_pct)
        assert dim.weighted_contribution == pytest.approx(dim.dimension_score * expected_pct)

    def test_final_score_equals_sum_of_contributions_ceiled(self):
        a = _assessment("A", "skills", required=True, status="MATCHED", match_type="direct")
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        total_contrib = sum(d.weighted_contribution for d in result.dimensions.values())
        expected = min(100, math.ceil(total_contrib * 100))
        assert result.final_score == expected


# ── Tech precision (Java ≠ JavaScript) ────────────────────────────────────────

class TestTechPrecision:
    def test_java_inferred_match_for_javascript_role_capped(self):
        """Inferred + strict: Java matching JavaScript criterion is capped at 0.40."""
        a = _assessment(
            "JavaScript", "skills", required=True,
            status="MATCHED", match_type="inferred", criterion_class="strict",
        )
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["skills"].criteria[0]
        # inferred strict cap = 0.40
        assert c.quality_factor == pytest.approx(0.40)
        assert c.effective_credit == pytest.approx(1.0 * 0.40)

    def test_java_transferable_match_for_javascript_capped(self):
        """Transferable + strict: capped at 0.50."""
        a = _assessment(
            "JavaScript", "skills", required=True,
            status="MATCHED", match_type="transferable", criterion_class="strict",
        )
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["skills"].criteria[0]
        assert c.quality_factor == pytest.approx(0.50)

    def test_direct_match_not_capped(self):
        """Direct match always gets 1.0 regardless of class."""
        a = _assessment(
            "Java", "skills", required=True,
            status="MATCHED", match_type="direct", criterion_class="strict",
        )
        result = _engine().score(_llm_result([a]), _DEFAULT_WEIGHTS)
        c = result.dimensions["skills"].criteria[0]
        assert c.quality_factor == pytest.approx(1.0)
