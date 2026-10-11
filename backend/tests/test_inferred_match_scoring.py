"""
Regression tests for generalised inferred-match scoring.

Root cause: The LLM can output status='MATCHED'/'PARTIAL' with supporting
evidence but match_type='missing', which zero-out effective_credit via the
_MATCH_TYPE_FACTOR table.  The engine normalises these to match_type='inferred'
so evidence-backed assessments receive their appropriate partial credit.

Tests cover the examples the user described as incorrectly scored:
  Computer Literacy ← Word & Excel
  MS Office proficiency ← Word, Excel, PowerPoint
  ERP systems ← SAP
  Communication skills ← client-facing roles
  Leadership ← team management
  Stakeholder Management ← vendor coordination
  Financial Services ← banking experience
  Public Sector ← government project experience
Plus negative cases that must remain ABSENT.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.deterministic_scoring import (
    DeterministicScoringConfig,
    DeterministicScoringEngine,
)


# ── Minimal assessment stub ───────────────────────────────────────────────────

@dataclass
class _Assessment:
    criterion_text:    str
    dimension:         str        = "skills"
    required:          bool       = True
    status:            str        = "MATCHED"
    match_type:        str        = "missing"
    criterion_class:   str        = "flexible"
    confidence:        float      = 0.85
    supporting_evidence: list     = field(default_factory=list)
    risk_flags:        list       = field(default_factory=list)


def _score(assessment: _Assessment, cfg: DeterministicScoringConfig | None = None):
    """Run a single assessment through _score_criterion and return the result."""
    cfg = cfg or DeterministicScoringConfig()
    return DeterministicScoringEngine._score_criterion(assessment, cfg)


# ── TestInferredNormalization ─────────────────────────────────────────────────

class TestInferredNormalization:
    """
    When status=MATCHED/PARTIAL + evidence + confidence≥threshold
    but match_type='missing', the engine must reclassify to 'inferred'.
    """

    # ── Computer Literacy examples ────────────────────────────────────────────

    def test_computer_literacy_word_excel_gets_inferred_credit(self):
        """Computer literacy ← 'Microsoft Word & Excel' must earn 0.65 credit."""
        a = _Assessment(
            criterion_text="Computer literacy",
            status="MATCHED",
            match_type="missing",
            confidence=0.85,
            supporting_evidence=["Microsoft Word & Excel"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit == pytest_approx(0.65)
        assert "inferred_from_evidence" in r.risk_flags

    def test_computer_literacy_partial_status_gets_inferred_credit(self):
        """Computer literacy ← evidence, status=PARTIAL, match_type='missing' → 0.325."""
        a = _Assessment(
            criterion_text="Computer literacy",
            status="PARTIAL",
            match_type="missing",
            confidence=0.70,
            supporting_evidence=["Proficient in Word"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        # PARTIAL status_credit = 0.50; quality_factor = 0.65 → 0.325
        assert abs(r.effective_credit - 0.325) < 1e-9
        assert "inferred_from_evidence" in r.risk_flags

    # ── MS Office / Office suite examples ────────────────────────────────────

    def test_ms_office_from_word_excel_powerpoint(self):
        """MS Office proficiency ← Word, Excel, PowerPoint evidence → inferred."""
        a = _Assessment(
            criterion_text="MS Office proficiency",
            status="MATCHED",
            match_type="missing",
            confidence=0.80,
            supporting_evidence=["Microsoft Word, Excel, and PowerPoint"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit > 0
        assert "inferred_from_evidence" in r.risk_flags

    # ── ERP systems ──────────────────────────────────────────────────────────

    def test_erp_from_sap_experience(self):
        """ERP systems ← 'SAP' in evidence → inferred credit."""
        a = _Assessment(
            criterion_text="ERP systems experience",
            status="MATCHED",
            match_type="missing",
            confidence=0.75,
            supporting_evidence=["5 years SAP implementation"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit == pytest_approx(0.65)

    # ── Soft skills — Communication ───────────────────────────────────────────

    def test_communication_from_client_facing_role(self):
        """Communication skills ← client-facing evidence → inferred credit."""
        a = _Assessment(
            criterion_text="Communication skills",
            dimension="soft_skills",
            criterion_class="soft_skill",
            status="MATCHED",
            match_type="missing",
            confidence=0.78,
            supporting_evidence=["Liaised with clients and presented weekly reports"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit == pytest_approx(0.65)

    # ── Leadership ───────────────────────────────────────────────────────────

    def test_leadership_from_team_management(self):
        """Leadership ← team management evidence → inferred credit."""
        a = _Assessment(
            criterion_text="Leadership",
            dimension="soft_skills",
            criterion_class="soft_skill",
            status="MATCHED",
            match_type="missing",
            confidence=0.82,
            supporting_evidence=["Managed a team of 8 engineers"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit == pytest_approx(0.65)

    # ── Stakeholder Management ────────────────────────────────────────────────

    def test_stakeholder_management_from_vendor_coordination(self):
        """Stakeholder management ← vendor coordination evidence → inferred credit."""
        a = _Assessment(
            criterion_text="Stakeholder management",
            dimension="soft_skills",
            criterion_class="soft_skill",
            status="MATCHED",
            match_type="missing",
            confidence=0.72,
            supporting_evidence=["Coordinated with external vendors and C-suite executives"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit > 0

    # ── Domain knowledge examples ─────────────────────────────────────────────

    def test_financial_services_from_banking_experience(self):
        """Financial services knowledge ← banking experience → inferred credit."""
        a = _Assessment(
            criterion_text="Financial services knowledge",
            dimension="domain_knowledge",
            criterion_class="flexible",
            status="MATCHED",
            match_type="missing",
            confidence=0.80,
            supporting_evidence=["10 years in retail banking at HSBC"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit == pytest_approx(0.65)

    def test_public_sector_from_government_projects(self):
        """Public sector experience ← government project evidence → inferred credit."""
        a = _Assessment(
            criterion_text="Public sector experience",
            dimension="experience",
            criterion_class="flexible",
            status="MATCHED",
            match_type="missing",
            confidence=0.76,
            supporting_evidence=["Project delivery for Ministry of Finance"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit == pytest_approx(0.65)


# ── TestInferredNormalizationNegativeCases ────────────────────────────────────

class TestInferredNormalizationNegativeCases:
    """Cases that must NOT be normalized — effective_credit must remain 0."""

    def test_absent_status_no_normalization(self):
        """ABSENT status is never upgraded even with evidence."""
        a = _Assessment(
            criterion_text="Python programming",
            status="ABSENT",
            match_type="missing",
            confidence=0.50,
            supporting_evidence=["Used JavaScript"],
        )
        r = _score(a)
        assert r.match_type == "missing"
        assert r.effective_credit == 0.0
        assert "inferred_from_evidence" not in r.risk_flags

    def test_matched_no_evidence_no_normalization(self):
        """status=MATCHED, match_type='missing', no evidence → must stay 0."""
        a = _Assessment(
            criterion_text="Python programming",
            status="MATCHED",
            match_type="missing",
            confidence=0.85,
            supporting_evidence=[],
        )
        r = _score(a)
        assert r.match_type == "missing"
        assert r.effective_credit == 0.0

    def test_low_confidence_below_threshold_not_normalized(self):
        """Confidence below threshold (0.35) must not trigger normalization."""
        a = _Assessment(
            criterion_text="Computer literacy",
            status="MATCHED",
            match_type="missing",
            confidence=0.20,   # below 0.35 threshold
            supporting_evidence=["Mentioned computers once"],
        )
        r = _score(a)
        assert r.match_type == "missing"
        assert r.effective_credit == 0.0

    def test_confidence_exactly_at_threshold_is_normalized(self):
        """Confidence exactly at threshold (0.35) triggers normalization."""
        a = _Assessment(
            criterion_text="Computer literacy",
            status="MATCHED",
            match_type="missing",
            confidence=0.35,
            supporting_evidence=["Microsoft Word"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit > 0

    def test_normalization_disabled_via_config(self):
        """When inferred_normalization_enabled=False, no normalization occurs."""
        cfg = DeterministicScoringConfig(inferred_normalization_enabled=False)
        a = _Assessment(
            criterion_text="Computer literacy",
            status="MATCHED",
            match_type="missing",
            confidence=0.85,
            supporting_evidence=["Microsoft Word & Excel"],
        )
        r = _score(a, cfg)
        assert r.match_type == "missing"
        assert r.effective_credit == 0.0

    def test_already_inferred_match_type_unchanged(self):
        """Existing inferred match_type passes through unmodified."""
        a = _Assessment(
            criterion_text="Computer literacy",
            status="MATCHED",
            match_type="inferred",
            confidence=0.65,
            supporting_evidence=["Microsoft Word & Excel"],
        )
        r = _score(a)
        assert r.match_type == "inferred"
        assert r.effective_credit == pytest_approx(0.65)
        assert "inferred_from_evidence" not in r.risk_flags


# ── TestStrictClassCapping ────────────────────────────────────────────────────

class TestStrictClassCapping:
    """Strict/certification criteria must be capped even after normalization."""

    def test_strict_class_inferred_capped_at_040(self):
        """After normalization, strict class caps inferred at 0.40."""
        cfg = DeterministicScoringConfig()
        a = _Assessment(
            criterion_text="CPA certification",
            dimension="certifications",
            criterion_class="strict",
            status="MATCHED",
            match_type="missing",
            confidence=0.75,
            supporting_evidence=["Part I CPA exam completed"],
        )
        r = _score(a, cfg)
        assert r.match_type == "inferred"
        # MATCHED status_credit=1.0; inferred+strict cap=0.40
        assert r.effective_credit == pytest_approx(0.40)
        assert "inferred_from_evidence" in r.risk_flags

    def test_flexible_class_inferred_not_capped(self):
        """Flexible class does not apply the strict cap."""
        a = _Assessment(
            criterion_text="Computer literacy",
            criterion_class="flexible",
            status="MATCHED",
            match_type="missing",
            confidence=0.85,
            supporting_evidence=["Microsoft Word & Excel"],
        )
        r = _score(a)
        assert r.effective_credit == pytest_approx(0.65)


# ── TestRiskFlagPreservation ──────────────────────────────────────────────────

class TestRiskFlagPreservation:
    """Existing risk flags must be preserved alongside the new flag."""

    def test_existing_risk_flags_preserved(self):
        """Pre-existing flags survive normalization."""
        a = _Assessment(
            criterion_text="Computer literacy",
            status="MATCHED",
            match_type="missing",
            confidence=0.85,
            supporting_evidence=["Microsoft Word & Excel"],
            risk_flags=["low_confidence"],
        )
        r = _score(a)
        assert "low_confidence" in r.risk_flags
        assert "inferred_from_evidence" in r.risk_flags

    def test_missing_supporting_evidence_flag_stripped(self):
        """The 'missing_supporting_evidence' stale flag is removed on normalization."""
        a = _Assessment(
            criterion_text="Computer literacy",
            status="MATCHED",
            match_type="missing",
            confidence=0.85,
            supporting_evidence=["Microsoft Word & Excel"],
            risk_flags=["missing_supporting_evidence"],
        )
        r = _score(a)
        assert "missing_supporting_evidence" not in r.risk_flags
        assert "inferred_from_evidence" in r.risk_flags


# ── TestConfigurableThreshold ─────────────────────────────────────────────────

class TestConfigurableThreshold:
    """The normalization confidence threshold is configurable."""

    def test_custom_high_threshold_blocks_normalization(self):
        """With threshold=0.90, confidence=0.85 is below threshold."""
        cfg = DeterministicScoringConfig(inferred_normalization_min_confidence=0.90)
        a = _Assessment(
            criterion_text="Computer literacy",
            status="MATCHED",
            match_type="missing",
            confidence=0.85,
            supporting_evidence=["Microsoft Word & Excel"],
        )
        r = _score(a, cfg)
        assert r.match_type == "missing"
        assert r.effective_credit == 0.0

    def test_custom_low_threshold_allows_normalization(self):
        """With threshold=0.30, confidence=0.35 triggers normalization."""
        cfg = DeterministicScoringConfig(inferred_normalization_min_confidence=0.30)
        a = _Assessment(
            criterion_text="Computer literacy",
            status="MATCHED",
            match_type="missing",
            confidence=0.35,
            supporting_evidence=["Microsoft Word"],
        )
        r = _score(a, cfg)
        assert r.match_type == "inferred"
        assert r.effective_credit > 0


# ── pytest_approx shim (avoid pytest import at module level in prod code) ─────

try:
    from pytest import approx as pytest_approx
except ImportError:
    def pytest_approx(x, **_):  # type: ignore[misc]
        return x
