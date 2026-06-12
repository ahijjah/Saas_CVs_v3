"""
Layer 2 — Criteria Matching dataclasses.

These dataclasses define the result of matching a parsed CVFacts object against
a job's criteria (sourced from analysis_json).  They are produced by the
CriteriaMatchEngine (Batch 2A-5) and persisted to
application_scores.match_results_json (Batch 2A-3).

No matching logic lives here — this file is pure data contracts.
No LLM calls, no DB access, no scoring changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from services.cv_evidence import MatchMethod


# ── Type aliases ──────────────────────────────────────────────────────────────

MatchStatus = Literal["MATCHED", "PARTIAL", "ABSENT"]
GapSeverity = Literal["BLOCKING", "SIGNIFICANT", "MINOR"]
Dimension = Literal[
    "skills",
    "experience",
    "education",
    "certifications",
    "soft_skills",
    "domain_knowledge",
    "other",
]


# ── Criterion-level results ───────────────────────────────────────────────────

@dataclass
class CriterionMatch:
    """Result of matching a single job criterion against CVFacts.

    One CriterionMatch is produced for every item in the job criteria
    (required skills, preferred skills, experience bullets, education
    requirements, certifications, domain keywords, other requirements).

    status semantics:
      MATCHED  — strong evidence found (confidence ≥ 0.60).
      PARTIAL  — weak or inferred evidence only (0.20 ≤ confidence < 0.60).
      ABSENT   — no credible evidence found (confidence < 0.20).
    """

    # ── Criterion identity ────────────────────────────────────────────────
    criterion_text: str
    """Exact criterion text from analysis_json (e.g. 'Microsoft Excel')."""

    dimension: Dimension
    """Which scoring dimension this criterion belongs to."""

    required: bool
    """True = mandatory criterion (from analysis_json.skills.required or
    equivalent).  False = preferred / nice-to-have."""

    # ── Match verdict ─────────────────────────────────────────────────────
    status: MatchStatus
    """MATCHED | PARTIAL | ABSENT."""

    confidence: float
    """Aggregate confidence 0.0–1.0.
    Computed as: max(evidence_confidence)*0.7 + mean(evidence_confidence)*0.3
    when multiple evidence items exist; equals the single item's confidence
    when only one item is found."""

    match_method: MatchMethod
    """Primary method that produced this match:
      'exact'      — token-for-token match
      'normalised' — variant spelling / abbreviation
      'fuzzy'      — rapidfuzz partial_ratio ≥ 80
      'semantic'   — embedding cosine similarity ≥ 0.72
      'inferred'   — contextual inference
      'absent'     — no match found"""

    # ── Evidence ──────────────────────────────────────────────────────────
    supporting_evidence: list[str] = field(default_factory=list)
    """CV text spans that support the match (≤200 chars each)."""

    evidence_confidence: list[float] = field(default_factory=list)
    """Per-item confidence score, parallel to supporting_evidence."""

    # ── Partial match explanation ─────────────────────────────────────────
    partial_reason: str = ""
    """Human-readable explanation of a PARTIAL match.
    E.g. '2 of 5 required years found' or 'related field, not exact match'."""

    # ── Cross-lingual metadata ────────────────────────────────────────────
    matched_via_translation: bool = False
    """True when the criterion was matched through the bilingual skill map
    (e.g. Arabic CV term matched to an English criterion)."""

    original_cv_term: str = ""
    """The raw CV term that produced the match, before normalisation.
    E.g. 'إكسل' matched to 'Microsoft Excel'."""


@dataclass
class GapCandidate:
    """A criterion that is ABSENT or PARTIAL — a potential scoring gap.

    GapCandidates are derived from CriterionMatch results by the
    CriteriaMatchEngine and are the structured predecessor to the current
    gaps_identified text list in the AI result.

    The suppressed flag indicates that compensating_evidence from other
    dimensions is strong enough to offset the gap.  Suppressed gaps are
    not shown to recruiters but are retained in the audit record.
    """

    criterion: CriterionMatch
    """The underlying CriterionMatch that produced this gap."""

    severity: GapSeverity
    """BLOCKING   — mandatory criterion, ABSENT, no credible compensation.
    SIGNIFICANT — mandatory criterion, PARTIAL, or ABSENT with weak compensation.
    MINOR       — preferred criterion ABSENT, or mandatory with strong compensation."""

    compensating_evidence: list[str] = field(default_factory=list)
    """Evidence from other dimensions that partially offsets this gap.
    E.g. 10 years of experience compensating for a missing certification."""

    compensating_confidence: float = 0.0
    """Aggregate confidence of the compensating evidence (0.0–1.0).
    0.0 = no compensation found.  ≥ 0.70 = strong enough to suppress."""

    suppressed: bool = False
    """True when compensating_confidence ≥ 0.70 and severity != BLOCKING.
    Suppressed gaps are excluded from recruiter-facing output."""

    suppression_reason: str = ""
    """Explanation of why the gap is suppressed.
    E.g. '10 years relevant experience compensates for missing PMP cert'."""


# ── Aggregate matching result ─────────────────────────────────────────────────

@dataclass
class MatchResult:
    """Complete Layer 2 output for a single application against a job.

    One MatchResult is produced per scoring run and persisted to
    application_scores.match_results_json.

    In Phase 2A this is stored silently alongside the existing LLM scoring
    result and has no effect on recruiter-facing output.  In Phase 3 it will
    provide the algorithmic_scores baseline that bounds LLM scoring deltas.
    """

    # ── Input references ──────────────────────────────────────────────────
    application_id: str
    """UUID of the application being scored."""

    job_id: str
    """UUID of the job the application is being scored against."""

    criteria_version: str
    """SHA-256 hash (first 16 chars) of the serialised criteria dict.
    Used to detect stale cached MatchResults when criteria are edited."""

    # ── Criterion-level results ───────────────────────────────────────────
    matches: list[CriterionMatch] = field(default_factory=list)
    """One CriterionMatch per criterion in the job criteria."""

    gap_candidates: list[GapCandidate] = field(default_factory=list)
    """Gaps derived from ABSENT/PARTIAL matches, ordered by severity."""

    # ── Aggregate statistics ──────────────────────────────────────────────
    required_match_pct: float = 0.0
    """Percentage of mandatory criteria with status MATCHED (0.0–100.0)."""

    preferred_match_pct: float = 0.0
    """Percentage of preferred criteria with status MATCHED (0.0–100.0)."""

    partial_match_pct: float = 0.0
    """Percentage of all criteria with status PARTIAL (0.0–100.0)."""

    blocking_gap_count: int = 0
    """Number of mandatory criteria with status ABSENT and no compensation."""

    # ── Algorithmic score baseline ─────────────────────────────────────────
    algorithmic_scores: dict[str, float] = field(default_factory=dict)
    """Per-dimension algorithmic score 0.0–100.0, computed from CriterionMatch
    results.  Used in Phase 3 as the floor/anchor for LLM scoring deltas.
    Keys: 'skills', 'experience', 'education', 'certifications',
          'soft_skills', 'domain_knowledge', 'other'."""

    # ── Metadata ──────────────────────────────────────────────────────────
    matcher_version: str = "0.0.0"
    """Semver of the CriteriaMatchEngine that produced this record."""

    matching_method_summary: dict[str, int] = field(default_factory=dict)
    """Count of each MatchMethod used: {'exact': 3, 'fuzzy': 2, 'absent': 1}."""
