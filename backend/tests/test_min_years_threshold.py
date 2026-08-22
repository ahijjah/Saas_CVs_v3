"""
Minimum years of experience — threshold scoring tests.

"Minimum N years of experience" criteria are thresholds: if the candidate's
extracted years >= N, the criterion should receive full credit (quality_factor=1.0)
regardless of how the LLM classified match_type.

Tests:
1. candidate years == required years → full credit
2. candidate years > required years → full credit
3. candidate years < required years → NOT promoted to direct
4. non-years criteria unaffected by the threshold rule
5. missing evidence → no promotion
6. ABSENT status → not promoted
7. PARTIAL status → not promoted (threshold rule requires MATCHED)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.deterministic_scoring import (
    DeterministicScoringConfig,
    DeterministicScoringEngine,
    _check_min_years_threshold,
)


@dataclass
class _Assessment:
    criterion_text:      str
    dimension:           str   = "experience"
    required:            bool  = True
    status:              str   = "MATCHED"
    match_type:          str   = "inferred"
    criterion_class:     str   = "flexible"
    confidence:          float = 0.85
    supporting_evidence: list  = field(default_factory=list)
    risk_flags:          list  = field(default_factory=list)


def _score(a: _Assessment, cfg=None):
    return DeterministicScoringEngine._score_criterion(
        a, cfg or DeterministicScoringConfig()
    )


# ── _check_min_years_threshold unit tests ────────────────────────────────────

class TestCheckMinYearsThreshold:
    def test_meets_exactly(self):
        r = _check_min_years_threshold(
            "Minimum 3 years of experience",
            ["Total Experience: 3.0 years"],
        )
        assert r is True

    def test_exceeds(self):
        r = _check_min_years_threshold(
            "Minimum 1 years of relevant experience",
            ["Total Experience: 10.0 years"],
        )
        assert r is True

    def test_below_returns_none(self):
        r = _check_min_years_threshold(
            "Minimum 5 years of experience",
            ["Total Experience: 2.0 years"],
        )
        assert r is None

    def test_no_years_in_evidence_returns_none(self):
        r = _check_min_years_threshold(
            "Minimum 3 years of experience",
            ["Has relevant background in finance"],
        )
        assert r is None

    def test_not_a_years_criterion_returns_none(self):
        r = _check_min_years_threshold(
            "Computer Literacy",
            ["Total Experience: 10.0 years"],
        )
        assert r is None

    def test_partial_years_extracted(self):
        r = _check_min_years_threshold(
            "Minimum 2 years of experience",
            ["3.5 years of relevant work"],
        )
        assert r is True

    def test_years_plus_format(self):
        r = _check_min_years_threshold(
            "Minimum 5 years of experience",
            ["10+ years in the industry"],
        )
        assert r is True


# ── Full credit when threshold is met ─────────────────────────────────────────

class TestMinYearsFullCredit:
    def test_exact_years_match_gets_full_credit(self):
        s = _score(_Assessment(
            criterion_text="Minimum 3 years of experience",
            status="MATCHED",
            match_type="inferred",
            supporting_evidence=["Total Experience: 3.0 years"],
        ))
        assert s.effective_credit == 1.0
        assert s.quality_factor == 1.0
        assert s.match_type == "direct"
        assert "min_years_threshold_met" in s.risk_flags

    def test_exceeds_required_years_gets_full_credit(self):
        s = _score(_Assessment(
            criterion_text="Minimum 1 years of relevant experience",
            status="MATCHED",
            match_type="inferred",
            supporting_evidence=["Total Experience: 14.0 years"],
        ))
        assert s.effective_credit == 1.0
        assert s.quality_factor == 1.0
        assert s.match_type == "direct"

    def test_10_years_experience_min_1(self):
        s = _score(_Assessment(
            criterion_text="Minimum 1 years of relevant experience",
            status="MATCHED",
            match_type="inferred",
            supporting_evidence=["Total Experience: 10.0 years"],
        ))
        assert s.effective_credit == 1.0

    def test_transferable_match_type_also_promoted(self):
        s = _score(_Assessment(
            criterion_text="Minimum 2 years of experience",
            status="MATCHED",
            match_type="transferable",
            supporting_evidence=["5 years in a related role"],
        ))
        assert s.effective_credit == 1.0
        assert s.match_type == "direct"

    def test_missing_match_type_normalized_then_promoted(self):
        """match_type=missing + MATCHED + evidence → first becomes 'inferred'
        via normalization, then 'direct' via threshold rule."""
        s = _score(_Assessment(
            criterion_text="Minimum 2 years of experience",
            status="MATCHED",
            match_type="missing",
            supporting_evidence=["Total Experience: 8.0 years"],
            confidence=0.85,
        ))
        assert s.effective_credit == 1.0
        assert s.match_type == "direct"


# ── No promotion when threshold is NOT met ───────────────────────────────────

class TestMinYearsNotPromoted:
    def test_below_required_years_stays_inferred(self):
        s = _score(_Assessment(
            criterion_text="Minimum 5 years of experience",
            status="MATCHED",
            match_type="inferred",
            supporting_evidence=["Total Experience: 2.0 years"],
        ))
        assert s.match_type == "inferred"
        assert s.quality_factor == 0.65
        assert s.effective_credit == 0.65

    def test_no_years_in_evidence_stays_inferred(self):
        s = _score(_Assessment(
            criterion_text="Minimum 3 years of experience",
            status="MATCHED",
            match_type="inferred",
            supporting_evidence=["Has relevant background"],
        ))
        assert s.match_type == "inferred"
        assert s.quality_factor == 0.65

    def test_absent_status_not_promoted(self):
        s = _score(_Assessment(
            criterion_text="Minimum 3 years of experience",
            status="ABSENT",
            match_type="missing",
            supporting_evidence=[],
        ))
        assert s.effective_credit == 0.0

    def test_partial_status_not_promoted(self):
        s = _score(_Assessment(
            criterion_text="Minimum 3 years of experience",
            status="PARTIAL",
            match_type="inferred",
            supporting_evidence=["Total Experience: 5.0 years"],
        ))
        assert s.status == "PARTIAL"
        assert s.match_type == "inferred"
        assert s.quality_factor == 0.65


# ── Non-years criteria unaffected ────────────────────────────────────────────

class TestNonYearsCriteriaUnaffected:
    def test_computer_literacy_stays_inferred(self):
        s = _score(_Assessment(
            criterion_text="Computer Literacy",
            dimension="skills",
            status="MATCHED",
            match_type="inferred",
            supporting_evidence=["Microsoft Word & Excel"],
        ))
        assert s.match_type == "inferred"
        assert s.quality_factor == 0.65

    def test_leadership_stays_inferred(self):
        s = _score(_Assessment(
            criterion_text="Leadership skills",
            dimension="soft_skills",
            status="MATCHED",
            match_type="inferred",
            supporting_evidence=["Managed a team of 5"],
        ))
        assert s.match_type == "inferred"
        assert s.quality_factor == 0.65

    def test_direct_match_type_unchanged(self):
        s = _score(_Assessment(
            criterion_text="Minimum 3 years of experience",
            status="MATCHED",
            match_type="direct",
            supporting_evidence=["Total Experience: 10.0 years"],
        ))
        assert s.match_type == "direct"
        assert s.quality_factor == 1.0
        assert s.effective_credit == 1.0
