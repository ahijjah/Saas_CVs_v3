"""
Regression tests for criterion display status normalization.

Root cause: The scoring engine can output status='MATCHED' while
effective_credit=0 (when quality_factor=0 zeroes out the credit).
The frontend must not render a green checkmark when effective_credit=0.

These tests verify the displayStatus() logic as pure Python (mirrors the
TypeScript helper in ApplicationDetails.tsx).
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Mirror of DetCriterionScore (subset of fields needed for display) ────────

@dataclass
class CriterionScore:
    status: str          # 'MATCHED' | 'PARTIAL' | 'ABSENT'
    match_type: str      # 'direct' | 'equivalent' | ... | 'missing'
    effective_credit: float
    confidence: float
    supporting_evidence: list = field(default_factory=list)


# ── Inline copy of displayStatus() logic from ApplicationDetails.tsx ─────────

def display_status(c: CriterionScore) -> str:
    """Normalize criterion status for display.

    Safety rule: effective_credit=0 must never render a green check,
    even if the raw status is 'MATCHED'.
    """
    if (c.effective_credit or 0) == 0:
        return 'ABSENT'
    return c.status


# ── TestDisplayStatusNormalization ────────────────────────────────────────────

class TestDisplayStatusNormalization:

    # ── MATCHED criteria ──────────────────────────────────────────────────────

    def test_matched_full_credit_stays_matched(self):
        """MATCHED + credit 1.0 → MATCHED (green ✓)."""
        c = CriterionScore(status='MATCHED', match_type='direct',
                           effective_credit=1.0, confidence=0.95,
                           supporting_evidence=['Microsoft Excel'])
        assert display_status(c) == 'MATCHED'

    def test_matched_high_credit_stays_matched(self):
        """MATCHED + credit 0.80 → MATCHED."""
        c = CriterionScore(status='MATCHED', match_type='inferred',
                           effective_credit=0.80, confidence=0.90)
        assert display_status(c) == 'MATCHED'

    def test_matched_zero_credit_becomes_absent(self):
        """MATCHED + credit 0 → ABSENT (✗). Core regression case."""
        c = CriterionScore(status='MATCHED', match_type='missing',
                           effective_credit=0.0, confidence=0.85,
                           supporting_evidence=['Microsoft Word & Excel'])
        assert display_status(c) == 'ABSENT'

    def test_matched_zero_credit_no_evidence_becomes_absent(self):
        """MATCHED + credit 0 + no evidence → ABSENT."""
        c = CriterionScore(status='MATCHED', match_type='missing',
                           effective_credit=0.0, confidence=0.85)
        assert display_status(c) == 'ABSENT'

    # ── PARTIAL criteria ──────────────────────────────────────────────────────

    def test_partial_credit_50_stays_partial(self):
        """PARTIAL + credit 0.50 → PARTIAL (△)."""
        c = CriterionScore(status='PARTIAL', match_type='transferable',
                           effective_credit=0.50, confidence=0.70)
        assert display_status(c) == 'PARTIAL'

    def test_partial_low_credit_stays_partial(self):
        """PARTIAL + credit 0.10 → PARTIAL."""
        c = CriterionScore(status='PARTIAL', match_type='transferable',
                           effective_credit=0.10, confidence=0.60)
        assert display_status(c) == 'PARTIAL'

    def test_partial_zero_credit_becomes_absent(self):
        """PARTIAL + credit 0 → ABSENT (not △, not green)."""
        c = CriterionScore(status='PARTIAL', match_type='missing',
                           effective_credit=0.0, confidence=0.40)
        assert display_status(c) == 'ABSENT'

    # ── ABSENT criteria ───────────────────────────────────────────────────────

    def test_absent_credit_0_stays_absent(self):
        """ABSENT + credit 0 → ABSENT (✗)."""
        c = CriterionScore(status='ABSENT', match_type='missing',
                           effective_credit=0.0, confidence=0.0)
        assert display_status(c) == 'ABSENT'

    def test_absent_credit_0_with_evidence_stays_absent(self):
        """ABSENT + credit 0 + evidence present → ABSENT (evidence shown under ✗)."""
        c = CriterionScore(status='ABSENT', match_type='missing',
                           effective_credit=0.0, confidence=0.30,
                           supporting_evidence=['Some evidence'])
        assert display_status(c) == 'ABSENT'

    # ── Safety rule: evidence/confidence do not override zero credit ──────────

    def test_evidence_present_with_zero_credit_is_absent(self):
        """Evidence present + credit 0 must not show green check."""
        c = CriterionScore(status='MATCHED', match_type='missing',
                           effective_credit=0.0, confidence=0.85,
                           supporting_evidence=['Microsoft Word & Excel'])
        assert display_status(c) != 'MATCHED'
        assert display_status(c) == 'ABSENT'

    def test_high_confidence_with_zero_credit_is_absent(self):
        """Confidence > 0 + credit 0 must not show green check."""
        c = CriterionScore(status='MATCHED', match_type='inferred',
                           effective_credit=0.0, confidence=0.99)
        assert display_status(c) == 'ABSENT'

    def test_none_effective_credit_treated_as_zero(self):
        """None effective_credit (missing field) must not show green check."""
        c = CriterionScore(status='MATCHED', match_type='direct',
                           effective_credit=None, confidence=0.70)
        assert display_status(c) == 'ABSENT'

    # ── Threshold edge cases ──────────────────────────────────────────────────

    def test_tiny_positive_credit_is_not_absent(self):
        """Any non-zero credit should not be downgraded to ABSENT."""
        c = CriterionScore(status='PARTIAL', match_type='transferable',
                           effective_credit=0.01, confidence=0.50)
        assert display_status(c) == 'PARTIAL'

    def test_full_credit_direct_match_is_matched(self):
        """Classic full match: status='MATCHED', credit=1.0 → green check."""
        c = CriterionScore(status='MATCHED', match_type='direct',
                           effective_credit=1.0, confidence=1.0,
                           supporting_evidence=['Microsoft Excel'])
        assert display_status(c) == 'MATCHED'


# ── TestDisplayStatusIconMapping ──────────────────────────────────────────────

def _icon(ds: str) -> str:
    """Mirror of statusIcon() in ApplicationDetails.tsx."""
    if ds == 'MATCHED': return '✓'
    if ds == 'PARTIAL': return '△'
    return '✗'


class TestDisplayStatusIconMapping:
    """Verify that displayStatus drives the correct icon."""

    def test_matched_credit_100_shows_checkmark(self):
        c = CriterionScore(status='MATCHED', match_type='direct',
                           effective_credit=1.0, confidence=0.9)
        assert _icon(display_status(c)) == '✓'

    def test_partial_credit_50_shows_triangle(self):
        c = CriterionScore(status='PARTIAL', match_type='transferable',
                           effective_credit=0.5, confidence=0.7)
        assert _icon(display_status(c)) == '△'

    def test_absent_credit_0_shows_cross(self):
        c = CriterionScore(status='ABSENT', match_type='missing',
                           effective_credit=0.0, confidence=0.0)
        assert _icon(display_status(c)) == '✗'

    def test_matched_status_zero_credit_shows_cross_not_checkmark(self):
        """The exact bug: status=MATCHED, credit=0 must show ✗, not ✓."""
        c = CriterionScore(status='MATCHED', match_type='missing',
                           effective_credit=0.0, confidence=0.85,
                           supporting_evidence=['Microsoft Word & Excel'])
        assert _icon(display_status(c)) == '✗'
        assert _icon(display_status(c)) != '✓'
