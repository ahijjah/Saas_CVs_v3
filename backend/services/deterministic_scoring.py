"""
F-01 — Deterministic Scoring Engine (Silent Mode)

Pure-calculation layer: takes LLMMatchResult + job weights and produces a
deterministic score stored alongside the LLM score.  No database writes.

Design
──────
1. Per-criterion effective credit:
       status_credit × match_quality_factor(match_type, criterion_class)
   - MATCHED  → status_credit = 1.0
   - PARTIAL  → status_credit = cfg.partial_credit  (default 0.50)
   - ABSENT   → status_credit = 0.0
   - CANNOT_DETERMINE → verified credit 0.0, upper credit 1.0 (P0-02a).
     Every CD criterion stays in the denominator; the verified score is what
     the CV evidence supports and the upper score is the maximum if every
     unresolved criterion is later verified as fully met.

2. match_quality_factor:
   - direct:       1.00
   - equivalent:   0.95
   - transferable: 0.80  (capped at 0.50 for strict/certification classes)
   - inferred:     0.65  (capped at 0.40 for strict/certification classes)
   - missing:      0.00

3. Dimension score = required_avg × req_w + preferred_avg × pref_w
   Defaults: req_w = 0.70, pref_w = 0.30

4. Optional required-absent floor (default disabled):
   If fraction of ABSENT required criteria > threshold, cap dimension at floor cap.

5. Final score = ceil(Σ(dim_score × weight) / Σ(weight) × 100)
   Weights from job_criteria table (weight_skills etc.)

6. Confidence is audit-only — never affects score.

7. Overqualification risk_flag captured but does not affect score.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.llm_criteria_mapper import (
    STATUS_CANNOT_DETERMINE,
    LLMMatchResult,
    QualitativeSummary,
)

logger = logging.getLogger(__name__)

try:
    from services.criteria_matcher import MatchResult
except ImportError:
    MatchResult = None  # Optional import for Phase 3 reconciliation

_ENGINE_VERSION = "det_score_v3"  # P0-02a: verified/upper scores, CANNOT_DETERMINE

# ── Match-type factors ────────────────────────────────────────────────────────

_MATCH_TYPE_FACTOR: dict[str, float] = {
    "direct":       1.00,
    "equivalent":   0.95,
    "transferable": 0.80,
    "inferred":     0.65,
    "missing":      0.00,
}

# Classes where precision matters — apply tighter caps on non-direct evidence
_STRICT_CLASSES: frozenset[str] = frozenset({"strict", "certification"})

# Cap applied to transferable and inferred for strict/certification classes
_STRICT_TRANSFERABLE_CAP: float = 0.50
_STRICT_INFERRED_CAP:     float = 0.40

# Dimension key aliases: LLMCriterionAssessment.dimension → weight key
_DIMENSION_WEIGHT_KEY: dict[str, str] = {
    "skills":           "weight_skills",
    "experience":       "weight_experience",
    "education":        "weight_education",
    "certifications":   "weight_certifications",
    "soft_skills":      "weight_soft_skills",
    "domain_knowledge": "weight_domain_knowledge",
    "other":            "weight_other",
}


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class DeterministicScoringConfig:
    """Tunable parameters loaded from system_config."""
    partial_credit:                  float = 0.50
    required_weight:                 float = 0.70
    preferred_weight:                float = 0.30
    enable_required_absent_floor:    bool  = False
    required_absent_floor_threshold: float = 0.50
    required_absent_floor_cap:       float = 0.40
    # When match_type='missing' contradicts status='MATCHED'/'PARTIAL' + evidence,
    # reclassify to 'inferred' so credit is not silently zeroed out.
    inferred_normalization_enabled:        bool  = True
    inferred_normalization_min_confidence: float = 0.35


# ── Output dataclasses ────────────────────────────────────────────────────────

@dataclass
class DeterministicCriterionScore:
    """Per-criterion result with full explainability."""
    criterion_text:       str
    dimension:            str
    required:             bool
    status:               str            # MATCHED | PARTIAL | ABSENT | CANNOT_DETERMINE
    match_type:           str
    criterion_class:      str
    status_credit:        float          # 0.0 | partial_credit | 1.0
    quality_factor:       float          # after class cap
    effective_credit:     float          # status_credit × quality_factor
    confidence:           float          # audit-only, never affects score
    supporting_evidence:  list[str]
    risk_flags:           list[str]
    has_overqualification: bool
    cd_reason:            str | None = None
    upper_credit:         float | None = None   # None → equals effective_credit
    pending_worth:        float = 0.0           # final-score points (CD rows only)
    match_reason:         str = ""
    reconciled_from:      dict[str, Any] | None = None  # set only when F-01 changed the status

    @property
    def verified_credit(self) -> float:
        return self.effective_credit

    @property
    def upper(self) -> float:
        return self.effective_credit if self.upper_credit is None else self.upper_credit


@dataclass
class DeterministicDimensionScore:
    """Per-dimension aggregation."""
    dimension:                  str
    dimension_score:            float    # 0.0–1.0
    weighted_contribution:      float    # dimension_score × weight_pct
    weight_pct:                 float    # normalised weight fraction 0.0–1.0
    n_required:                 int
    n_required_matched:         int
    n_required_partial:         int
    n_required_absent:          int
    n_preferred:                int
    n_preferred_matched:        int
    n_preferred_partial:        int
    n_preferred_absent:         int
    required_avg:               float
    preferred_avg:              float
    required_absent_floor_triggered: bool
    has_overqualification_risk: bool
    low_confidence_count:       int
    review_recommended:         bool
    criteria:                   list[DeterministicCriterionScore]
    dimension_score_upper:      float = 0.0
    weighted_contribution_upper: float = 0.0
    n_required_cannot_determine:  int = 0
    n_preferred_cannot_determine: int = 0


@dataclass
class DeterministicScore:
    """Top-level result returned by DeterministicScoringEngine.score()."""
    final_score:                     int             # 0–100 integer
    scoring_version:                 str             # "det_score_v1"
    scored_at:                       str             # ISO-8601 UTC
    mapper_version:                  str
    overqualification_risk_dimensions: list[str]
    dimensions:                      dict[str, DeterministicDimensionScore]
    qualitative_summary:             QualitativeSummary | None = None
    upper_score:                     int | None = None   # None → equals final_score


# ── Core calculation helpers ──────────────────────────────────────────────────

def _compute_semantic_similarity(criterion_text: str, evidence: str) -> float:
    """
    Compute semantic similarity between criterion and evidence using embeddings.
    Reuses paraphrase-multilingual-MiniLM-L12-v2 from gatekeeper pipeline.
    Returns [0, 1] representing evidence relevance to criterion.
    Gracefully falls back to keyword overlap if embedding model unavailable.
    """
    if not criterion_text or not evidence:
        return 0.0

    try:
        from services.local_processor import compute_semantic_similarity
        # compute_semantic_similarity handles text cleaning, truncation, normalization
        similarity = compute_semantic_similarity(criterion_text, evidence)
        return max(0.0, min(1.0, similarity))
    except (ImportError, Exception):
        # Fallback: keyword overlap if embedding unavailable
        from rapidfuzz import fuzz
        try:
            # Simple fallback: partial_ratio of criterion in evidence
            score = fuzz.partial_ratio(criterion_text.lower(), evidence.lower()) / 100.0
            return max(0.0, min(1.0, score))
        except ImportError:
            # Ultimate fallback: word overlap
            criterion_words = set(criterion_text.lower().split())
            evidence_words = set(evidence.lower().split())
            overlap = criterion_words & evidence_words
            if not criterion_words:
                return 0.0
            return len(overlap) / len(criterion_words)


def _match_quality_factor(match_type: str, criterion_class: str) -> float:
    """
    Return the match-quality factor for a given match_type and criterion_class.
    Applies tighter caps for strict/certification classes.
    """
    base = _MATCH_TYPE_FACTOR.get(match_type, 0.0)
    if criterion_class in _STRICT_CLASSES:
        if match_type == "transferable":
            return min(base, _STRICT_TRANSFERABLE_CAP)
        if match_type == "inferred":
            return min(base, _STRICT_INFERRED_CAP)
    return base


def _status_credit(status: str, cfg: DeterministicScoringConfig) -> float:
    if status == "MATCHED":
        return 1.0
    if status == "PARTIAL":
        return cfg.partial_credit
    return 0.0


def _upper_status_credit(status: str, cfg: DeterministicScoringConfig) -> float:
    """Status credit for the upper bound: CANNOT_DETERMINE counts as fully met."""
    if status == STATUS_CANNOT_DETERMINE:
        return 1.0
    return _status_credit(status, cfg)


def _check_evidence_criterion_overlap(
    criterion_text: str, evidence_list: list[str]
) -> float:
    """
    Check semantic relevance between criterion and evidence using HYBRID approach.
    Returns a quality_factor score 0.0-1.0 indicating how closely evidence addresses criterion.

    HYBRID DESIGN (Option B):
    Combines semantic embeddings + fuzzy matching to balance robustness:
    - Primary: paraphrase-multilingual-MiniLM-L12-v2 (document-level semantics)
    - Fallback: RapidFuzz token matching (phrase-level precision)
    - Final: max(embedding_score, fuzzy_score * 0.9) * 0.95

    RATIONALE:
    - Embeddings (trained on long docs) return conservative scores for short phrases
    - Fuzzy matching catches obvious phrase relationships embeddings might underscore
    - max() ensures we don't lose valid matches due to either signal's limitations
    - No artificial floor: irrelevant evidence (low on both signals) → low qf
    - Scales fairness: semantically related evidence gets credit regardless of hardcoded phrase list

    DETERMINISM: Both embeddings and fuzzy matching are deterministic (no LLM sampling).
    Same inputs always produce same quality_factors.
    """
    if not criterion_text or not evidence_list:
        return 0.0

    combined_evidence = " ".join(evidence_list)

    try:
        from rapidfuzz import fuzz

        # Compute semantic similarity using embedding model
        semantic_score = _compute_semantic_similarity(criterion_text, combined_evidence)

        # Compute fuzzy similarity as secondary signal (phrase-level precision)
        # Check each evidence snippet and use the best match
        # Using partial_ratio which is better for substring/phrase matching than token_set_ratio
        fuzzy_scores = []
        for evid in evidence_list:
            if evid.strip():
                # partial_ratio is better for phrase-in-text matching
                partial_score = fuzz.partial_ratio(criterion_text.lower(), evid.lower()) / 100.0
                # token_set_ratio for order-independent token matching
                token_score = fuzz.token_set_ratio(criterion_text.lower(), evid.lower()) / 100.0
                # Use best of both fuzzy approaches
                fuzzy_scores.append(max(partial_score, token_score))

        fuzzy_score = max(fuzzy_scores) if fuzzy_scores else 0.0

        # HYBRID: Weighted max favoring strong fuzzy matches
        # - If fuzzy_score is high (0.7+), it indicates clear phrase-level match → weight it heavily
        # - Otherwise use semantic score or a blend
        # Formula: max(semantic_score, fuzzy_score * 1.1) ensures fuzzy matches aren't bottlenecked
        # The 1.1 weighting acknowledges that high fuzzy scores indicate strong relevance
        combined_score = max(semantic_score, fuzzy_score * 1.1)

        # Map to quality_factor with no artificial floor, capped at 1.0
        qf = combined_score * 0.95

        return max(0.0, min(1.0, qf))

    except ImportError:
        # Fallback without RapidFuzz: embedding only
        semantic_score = _compute_semantic_similarity(criterion_text, combined_evidence)
        qf = semantic_score * 0.95
        return max(0.0, min(1.0, qf))


# ── Total-years-only evidence backstop (P0-02a) ──────────────────────────────
# The D-01 user message carries one computed line, "Total Experience: X.X
# years" (llm_criteria_mapper._build_user_message). A CANNOT_DETERMINE whose
# only evidence is that line has no CV quote for the criterion at all, so
# nothing is pending verification: the criterion is reconciled to ABSENT.
# Pure-duration criteria ("Minimum 3 years of experience") are excluded — for
# them the total is the right evidence and the CD is left for D-01 to resolve.

_COMPUTED_TOTAL_YEARS_RE = re.compile(
    r"""^\s*["']?total experience:\s*\d+(?:\.\d+)?\s*years?\.?["']?\s*$""",
    re.IGNORECASE,
)
_PURE_DURATION_CRITERION_RE = re.compile(
    r"^\s*minimum\s+\d+\s+years?\s+(?:of\s+)?"
    r"(?:professional\s+|work\s+|total\s+)?experience\s*\.?\s*$",
    re.IGNORECASE,
)
TOTAL_YEARS_ONLY_RULE = "total_years_only_evidence"


def _evidence_is_total_years_only(evidence: list[Any]) -> bool:
    """True when every non-empty evidence item is the computed total-years line."""
    items = [str(e) for e in evidence if str(e or "").strip()]
    return bool(items) and all(_COMPUTED_TOTAL_YEARS_RE.match(e) for e in items)


# ── Minimum-years threshold detection ────────────────────────────────────────

_MIN_YEARS_CRITERION_RE = re.compile(
    r"[Mm]inim(?:um|al)\s+(\d+)\s+years?", re.IGNORECASE
)
_EVIDENCE_YEARS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:\+\s*)?years?", re.IGNORECASE
)


def _check_min_years_threshold(
    criterion_text: str, evidence: list[str]
) -> bool | None:
    """Return True if criterion is a minimum-years requirement and evidence
    shows the candidate meets or exceeds it.  Return None if the criterion
    is not a minimum-years requirement or no years value is found in evidence.
    """
    crit_match = _MIN_YEARS_CRITERION_RE.search(criterion_text)
    if not crit_match:
        return None
    required_years = float(crit_match.group(1))

    for ev in evidence:
        ev_match = _EVIDENCE_YEARS_RE.search(ev)
        if ev_match:
            candidate_years = float(ev_match.group(1))
            if candidate_years >= required_years:
                return True
    return None


def _min_years_shortfall(criterion_text: str, evidence: list[str]) -> bool:
    """True when a minimum-years criterion has years values in the evidence and
    every one of them is below the requirement (an established shortfall)."""
    crit_match = _MIN_YEARS_CRITERION_RE.search(criterion_text or "")
    if not crit_match:
        return False
    required_years = float(crit_match.group(1))
    found = [
        float(m.group(1))
        for ev in evidence
        for m in [_EVIDENCE_YEARS_RE.search(ev or "")]
        if m
    ]
    return bool(found) and max(found) < required_years


# ── Phase 3 Reconciliation: Local matcher relevance bounds on LLM results ─────

def _is_relevance_qualified_experience_criterion(criterion_text: str) -> bool:
    """Check if this is a relevance-qualified experience criterion.

    Returns True for criteria like "Minimum X years of relevant experience"
    but False for pure "Minimum X years of experience" without relevance qualifier.
    """
    if not criterion_text:
        return False
    text_lower = criterion_text.lower()
    # Must have both "years" and "relevant" to be a relevance-qualified criterion
    return "year" in text_lower and "relevant" in text_lower


def _extract_years_from_criterion(criterion_text: str) -> float | None:
    """Extract minimum years value from criterion_text like 'Minimum X years...'

    Examples:
      "Minimum 5 years experience" → 5.0
      "Minimum 7 years of relevant experience" → 7.0
      "Bachelor's degree" → None
    """
    match = _MIN_YEARS_CRITERION_RE.search(criterion_text or "")
    if match:
        return float(match.group(1))
    return None


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    """Safely get a field from either a dict or dataclass object."""
    if isinstance(obj, dict):
        return obj.get(field, default)
    else:
        return getattr(obj, field, default)


def _find_matching_local_criterion(
    llm_criterion_text: str,
    local_matches: list[Any],
    llm_assessment: Any = None,
) -> Any | None:
    """Find the corresponding criterion in local matcher results.

    Robust matching: When local and LLM paths construct criterion_text differently
    (e.g. local: "Minimum 5 years experience" vs LLM: "Minimum 5 years of relevant experience"),
    we match semantically on the years value + required flag instead of exact text.

    Handles both dict (from JSON) and dataclass (from CriteriaMatchEngine) formats.

    Returns the matching CriterionMatch from local_matches, or None if not found.
    """
    if not local_matches:
        return None

    # First try exact-text match (for cases where both use identical phrasing)
    for local_match in local_matches:
        local_crit_text = _get_field(local_match, "criterion_text", "")
        if (local_crit_text or "").strip() == (llm_criterion_text or "").strip():
            return local_match

    # Fall back to semantic match: years-value + required flag
    # This handles real-world case where local/LLM construct text differently
    llm_years = _extract_years_from_criterion(llm_criterion_text)
    llm_required = _get_field(llm_assessment, "required") if llm_assessment else None

    if llm_years is not None:
        for local_match in local_matches:
            local_crit_text = _get_field(local_match, "criterion_text", "")
            local_years = _extract_years_from_criterion(local_crit_text or "")
            local_required = _get_field(local_match, "required")
            local_dimension = _get_field(local_match, "dimension", "")

            # Match if both are experience criteria with same years requirement and required status
            if (
                local_years == llm_years
                and local_required == llm_required
                and local_dimension == "experience"
            ):
                return local_match

    return None


def _apply_local_relevance_bound(
    llm_assessment: Any,
    local_criterion: Any | None,
) -> tuple[str | None, float | None]:
    """
    Phase 3 reconciliation: if local matcher found relevance issues but LLM
    didn't, bound the LLM's result to the local matcher's lower status.

    Returns (risk_flag_to_add, new_confidence) if bounding should be applied,
    or (None, None) if no change needed.

    Bounding rule:
    - LLM: MATCHED, Local: PARTIAL due to relevance → cap to PARTIAL
    - LLM: MATCHED, Local: ABSENT due to relevance → cap to ABSENT
    - LLM: MATCHED, Local: MATCHED/PARTIAL/ABSENT non-relevance → no change
    - Any other case → no change (LLM not lower, or local not lower)
    """
    if not local_criterion:
        return None, None

    llm_status = llm_assessment.status or "ABSENT"
    local_status = _get_field(local_criterion, "status") or "ABSENT"
    local_partial_reason = _get_field(local_criterion, "partial_reason") or ""

    # Only bound if LLM says MATCHED and local says something lower
    if llm_status == "MATCHED" and local_status in ("PARTIAL", "ABSENT"):
        # Only apply bounding if local's reason is relevance-related
        reason_lower = local_partial_reason.lower()
        if "relevance" in reason_lower or "relevant" in reason_lower:
            return "llm_local_relevance_disagreement", float(_get_field(local_criterion, "confidence", 0.45))

    return None, None


def _local_relevance_shortfall_established(
    criterion_text: str,
    llm_evidence: list[str],
    local_criterion: Any,
) -> bool:
    """P0-02a: does the local Phase 3 result record an ESTABLISHED shortfall?

    Established (the local PARTIAL/ABSENT stands, as before):
      - the local matcher's numeric years comparison did not pass
        (local ABSENT: "No verifiable years and no clear role relevance"), or
      - years found in the LLM/local evidence are all below the requirement.
    Not established (→ CANNOT_DETERMINE, relevance_unverified):
      - local PARTIAL because years passed but role relevance is not verified
        (a relevance-only disagreement is not established non-compliance).
    """
    local_status = _get_field(local_criterion, "status") or "ABSENT"
    if local_status != "PARTIAL":
        return True
    local_evidence = list(_get_field(local_criterion, "supporting_evidence", []) or [])
    return _min_years_shortfall(criterion_text, list(llm_evidence) + local_evidence)


# ── Engine ────────────────────────────────────────────────────────────────────

class DeterministicScoringEngine:
    """
    Compute a deterministic score from LLM criteria mapping output.

    Usage::

        cfg = DeterministicScoringConfig(partial_credit=0.50, ...)
        engine = DeterministicScoringEngine(cfg)
        det_score = engine.score(llm_match_result, weights)
    """

    def __init__(self, cfg: DeterministicScoringConfig | None = None) -> None:
        self._cfg = cfg or DeterministicScoringConfig()

    def score(
        self,
        llm_match_result: LLMMatchResult,
        weights: dict[str, int],
        match_result: Any = None,
    ) -> DeterministicScore:
        """
        Score a candidate against criteria using the deterministic engine.

        Parameters
        ----------
        llm_match_result:
            Output of LLMCriteriaMapper.assess().
        weights:
            Job-level dimension weights, e.g.
            {"weight_skills": 30, "weight_experience": 25, ...}.
            All 7 keys should be present; missing keys default to 0.
        match_result:
            Optional output of CriteriaMatchEngine.compare() for Phase 3
            reconciliation. When provided, local matcher results will bound
            LLM results for relevance-qualified experience criteria.

        Returns
        -------
        DeterministicScore with per-dimension breakdown and final_score.
        """
        cfg = self._cfg
        # Handle both MatchResult object (from production) and list (for testing)
        if match_result is None:
            local_matches = None
        elif isinstance(match_result, list):
            local_matches = match_result
        else:
            local_matches = getattr(match_result, "matches", None)

        # ── Group assessments by dimension ────────────────────────────────────
        by_dimension: dict[str, list[DeterministicCriterionScore]] = {}
        for assessment in llm_match_result.assessments:
            dim = assessment.dimension or "other"
            crit = self._score_criterion(assessment, cfg, local_matches)
            by_dimension.setdefault(dim, []).append(crit)

        # ── Compute per-dimension scores ──────────────────────────────────────
        total_weight = sum(
            weights.get(wk, 0)
            for wk in _DIMENSION_WEIGHT_KEY.values()
        )
        if total_weight <= 0:
            total_weight = 1  # guard against zero-weight jobs

        dimension_scores: dict[str, DeterministicDimensionScore] = {}
        weighted_sum: float = 0.0
        weighted_sum_upper: float = 0.0

        for dim, wk in _DIMENSION_WEIGHT_KEY.items():
            w = weights.get(wk, 0)
            criteria_list = by_dimension.get(dim, [])
            dim_score_obj = self._score_dimension(dim, criteria_list, w, total_weight, cfg)
            dimension_scores[dim] = dim_score_obj
            weighted_sum += dim_score_obj.weighted_contribution
            weighted_sum_upper += dim_score_obj.weighted_contribution_upper

        # ── Final score ───────────────────────────────────────────────────────
        # weighted_contribution already accounts for normalised weights,
        # so the raw sum × 100 gives the final score in [0, 100].
        final_score = min(100, math.ceil(weighted_sum * 100))
        upper_score = max(final_score, min(100, math.ceil(weighted_sum_upper * 100)))

        # ── Overqualification risk dimensions ────────────────────────────────
        oq_dims = [
            dim
            for dim, ds in dimension_scores.items()
            if ds.has_overqualification_risk
        ]

        return DeterministicScore(
            final_score=final_score,
            scoring_version=_ENGINE_VERSION,
            scored_at=datetime.now(timezone.utc).isoformat(),
            mapper_version=llm_match_result.mapper_version,
            overqualification_risk_dimensions=oq_dims,
            dimensions=dimension_scores,
            qualitative_summary=llm_match_result.qualitative_summary,
            upper_score=upper_score,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _score_criterion(
        assessment: Any,
        cfg: DeterministicScoringConfig,
        local_matches: list[Any] | None = None,
    ) -> DeterministicCriterionScore:
        status          = assessment.status or "ABSENT"
        match_type      = assessment.match_type or "missing"
        criterion_class = assessment.criterion_class or "other"
        confidence      = float(assessment.confidence or 0.0)
        evidence        = list(assessment.supporting_evidence or [])
        risk_flags      = list(assessment.risk_flags or [])
        cd_reason       = getattr(assessment, "cd_reason", None) if status == STATUS_CANNOT_DETERMINE else None

        # CANNOT_DETERMINE skips every status-specific adjustment below
        # (inferred normalisation, min-years, overlap upgrade, severity
        # scaling): its verified credit is 0 and its upper credit is 1.0.

        # Normalize contradictory status/match_type: when the LLM finds evidence
        # and sets status=MATCHED/PARTIAL but also sets match_type='missing',
        # the scoring factor would silently zero out effective_credit. Reclassify
        # to 'inferred' so the evidence earns its appropriate partial credit.
        if (
            cfg.inferred_normalization_enabled
            and match_type == "missing"
            and status in ("MATCHED", "PARTIAL")
            and evidence
            and confidence >= cfg.inferred_normalization_min_confidence
        ):
            match_type = "inferred"
            risk_flags = [f for f in risk_flags if f != "missing_supporting_evidence"]
            risk_flags.append("inferred_from_evidence")

        # ── Minimum-years threshold rule ─────────────────────────────────
        # "Minimum N years of experience" is a threshold, not a precision
        # match.  If the evidence shows candidate_years >= required_years
        # and status is MATCHED, award full credit (quality_factor=1.0)
        # regardless of how the LLM classified match_type.
        if (
            status == "MATCHED"
            and match_type in ("inferred", "transferable")
            and evidence
        ):
            years_result = _check_min_years_threshold(
                assessment.criterion_text or "", evidence
            )
            if years_result is not None:
                match_type = "direct"
                if "min_years_threshold_met" not in risk_flags:
                    risk_flags.append("min_years_threshold_met")

        # ── Phase 3 Reconciliation: Relevance-qualified experience bounding ──
        # For "Minimum X years of relevant experience" criteria, if the local
        # matcher independently found relevance issues (PARTIAL/ABSENT) but the
        # LLM found MATCHED, bound the LLM's result to the local matcher's
        # conservative assessment. This prevents false positives where the LLM
        # missed relevance qualification evidence the keyword-based matcher caught.
        criterion_text = assessment.criterion_text or ""
        local_role_relevance_score = 0.0  # Capture for use in severity scaling
        if (
            local_matches
            and _is_relevance_qualified_experience_criterion(criterion_text)
            and (assessment.dimension or "other") == "experience"
        ):
            local_match = _find_matching_local_criterion(criterion_text, local_matches, assessment)
            risk_flag, new_confidence = _apply_local_relevance_bound(assessment, local_match)
            if risk_flag is not None and not _local_relevance_shortfall_established(
                criterion_text, evidence, local_match
            ):
                # P0-02a: relevance-only disagreement, no established shortfall
                # → the criterion is unresolved, not failed.
                status = STATUS_CANNOT_DETERMINE
                cd_reason = "relevance_unverified"
                if new_confidence is not None:
                    confidence = new_confidence
                for flag in (risk_flag, "local_relevance_check"):
                    if flag not in risk_flags:
                        risk_flags.append(flag)
            elif risk_flag is not None:
                # Bound the LLM's result to the local matcher's status
                status = _get_field(local_match, "status", "ABSENT")
                if new_confidence is not None:
                    confidence = new_confidence
                if risk_flag not in risk_flags:
                    risk_flags.append(risk_flag)
                # Capture local matcher's role_relevance_score for severity scaling
                # (used when LLM+local disagreement on relevance)
                local_role_relevance_score = _get_field(local_match, "role_relevance_score", 0.0) or 0.0

        # ── P0-02a backstop: CD supported only by the computed total-years line ──
        # Runs after relevance reconciliation so it also covers the CDs that
        # step produces. MATCHED / PARTIAL / ABSENT are never touched.
        reconciled_from = None
        if (
            status == STATUS_CANNOT_DETERMINE
            and _evidence_is_total_years_only(evidence)
            and not _PURE_DURATION_CRITERION_RE.match(criterion_text)
        ):
            reconciled_from = {
                "status": STATUS_CANNOT_DETERMINE,
                "cd_reason": cd_reason,
                "rule": TOTAL_YEARS_ONLY_RULE,
            }
            status = "ABSENT"
            cd_reason = None
            if TOTAL_YEARS_ONLY_RULE not in risk_flags:
                risk_flags.append(TOTAL_YEARS_ONLY_RULE)

        sc        = _status_credit(status, cfg)
        qf        = _match_quality_factor(match_type, criterion_class)

        # Post-processing: when match_type="inferred" but evidence has high textual
        # overlap with criterion, upgrade quality_factor to 0.95 (equivalent tier).
        # This prevents LLM inconsistency from unfairly penalizing candidates whose
        # evidence is substantively correct but classified as "inferred" by the LLM.
        # EXCEPTION: Do NOT upgrade if this is a minimum-years criterion and either:
        # 1. The threshold is NOT met, OR
        # 2. The status is not MATCHED (threshold rule requires both)
        # Numeric thresholds take precedence over text similarity.
        criterion_text = assessment.criterion_text or ""

        # Check if this is a minimum-years criterion and whether threshold is met
        is_years_criterion = bool(_MIN_YEARS_CRITERION_RE.search(criterion_text))
        years_threshold_met = _check_min_years_threshold(criterion_text, evidence) if is_years_criterion else True

        # Allow overlap upgrade only if:
        # - Not a minimum-years criterion, OR
        # - Is a minimum-years criterion AND threshold is met AND status is MATCHED
        allow_overlap_upgrade = (
            status != STATUS_CANNOT_DETERMINE
            and match_type == "inferred"
            and evidence
            and criterion_text
            and (not is_years_criterion or (years_threshold_met is True and status == "MATCHED"))
        )

        if allow_overlap_upgrade:
            overlap_score = _check_evidence_criterion_overlap(criterion_text, evidence)
            if overlap_score >= 0.65:  # 65%+ textual overlap threshold
                qf = max(qf, 0.95)  # Upgrade to equivalent tier
                if "inferred_with_strong_evidence_overlap" not in risk_flags:
                    risk_flags.append("inferred_with_strong_evidence_overlap")

        # ── Experience role-relevance severity scaling (scoped to this case only) ───
        # Apply severity scaling for role-relevance mismatches via TWO paths:
        # PATH 1: match_type="inferred" (LLM uncertain) + status=PARTIAL (local found mismatch)
        # PATH 2: match_type="direct" (LLM confident) but "llm_local_relevance_disagreement"
        #         (Phase 3 bounding: local matcher's evidence-based role check caught mismatch)
        # Both paths have fuzzy-score evidence via role_relevance_score from local matcher.
        # SCOPE: Only experience criteria with confirmed mismatch (role_relevance_score > 0)
        # Do NOT apply to other dimensions or mismatches without role data.

        # Determine which fuzzy score to use (from assessment or local_match)
        relevance_score_for_severity = 0.0
        is_confirmed_mismatch = False

        # PATH 1: inferred match with local role_relevance_score
        if (match_type == "inferred"
            and status == "PARTIAL"
            and hasattr(assessment, 'role_relevance_score')
            and assessment.role_relevance_score > 0):
            relevance_score_for_severity = assessment.role_relevance_score
            is_confirmed_mismatch = True

        # PATH 2: direct match but LLM/local disagreement on relevance
        elif (match_type == "direct"
              and status == "PARTIAL"
              and "llm_local_relevance_disagreement" in risk_flags
              and local_role_relevance_score > 0):
            relevance_score_for_severity = local_role_relevance_score
            is_confirmed_mismatch = True

        # Apply severity scaling if confirmed mismatch found
        if (assessment.dimension == "experience"
            and is_confirmed_mismatch
            and relevance_score_for_severity > 0):
            # Confirmed mismatch: scale quality_factor by how bad the mismatch is
            # severity_multiplier = max(0.15, fuzzy_score / 100.0)
            # This preserves "some experience exists" signal without rewarding irrelevant experience
            severity_multiplier = max(0.15, relevance_score_for_severity / 100.0)
            qf = qf * severity_multiplier
            if "experience_role_mismatch_severity_scaled" not in risk_flags:
                risk_flags.append("experience_role_mismatch_severity_scaled")

        effective = sc * qf
        upper = _upper_status_credit(status, cfg) if status == STATUS_CANNOT_DETERMINE else effective

        has_oq = "overqualified" in risk_flags

        return DeterministicCriterionScore(
            criterion_text=assessment.criterion_text or "",
            dimension=assessment.dimension or "other",
            required=bool(assessment.required),
            status=status,
            match_type=match_type,
            criterion_class=criterion_class,
            status_credit=sc,
            quality_factor=qf,
            effective_credit=effective,
            confidence=confidence,
            supporting_evidence=evidence,
            risk_flags=risk_flags,
            has_overqualification=has_oq,
            cd_reason=cd_reason,
            upper_credit=upper,
            match_reason=str(getattr(assessment, "match_reason", "") or ""),
            reconciled_from=reconciled_from,
        )

    @staticmethod
    def _score_dimension(
        dimension: str,
        criteria: list[DeterministicCriterionScore],
        weight: int,
        total_weight: int,
        cfg: DeterministicScoringConfig,
    ) -> DeterministicDimensionScore:
        required_crit  = [c for c in criteria if c.required]
        preferred_crit = [c for c in criteria if not c.required]

        # Per-group averages (verified and upper; every criterion, including
        # CANNOT_DETERMINE, stays in the denominator)
        def _avg(clist: list[DeterministicCriterionScore], upper: bool = False) -> float:
            if not clist:
                return 0.0
            return sum((c.upper if upper else c.effective_credit) for c in clist) / len(clist)

        req_avg  = _avg(required_crit)
        pref_avg = _avg(preferred_crit)
        req_avg_upper  = _avg(required_crit, upper=True)
        pref_avg_upper = _avg(preferred_crit, upper=True)

        # Weighted dimension score
        if required_crit and preferred_crit:
            dim_score = req_avg * cfg.required_weight + pref_avg * cfg.preferred_weight
            dim_score_upper = req_avg_upper * cfg.required_weight + pref_avg_upper * cfg.preferred_weight
            req_share, pref_share = cfg.required_weight, cfg.preferred_weight
        elif required_crit:
            dim_score = req_avg
            dim_score_upper = req_avg_upper
            req_share, pref_share = 1.0, 0.0
        else:
            dim_score = pref_avg
            dim_score_upper = pref_avg_upper
            req_share, pref_share = 0.0, 1.0

        # Optional required-absent floor
        absent_floor_triggered = False
        if cfg.enable_required_absent_floor and required_crit:
            n_req_absent = sum(1 for c in required_crit if c.status == "ABSENT")
            absent_frac = n_req_absent / len(required_crit)
            if absent_frac > cfg.required_absent_floor_threshold:
                if dim_score > cfg.required_absent_floor_cap:
                    dim_score = cfg.required_absent_floor_cap
                    absent_floor_triggered = True
                # The floor counts ABSENT only; the same cap bounds the upper.
                dim_score_upper = min(dim_score_upper, cfg.required_absent_floor_cap)
        dim_score_upper = max(dim_score_upper, dim_score)

        # Normalised weight fraction
        weight_pct = weight / total_weight if total_weight > 0 else 0.0
        weighted_contrib = dim_score * weight_pct
        weighted_contrib_upper = dim_score_upper * weight_pct

        # Final-score points each unresolved criterion could add if verified
        for group, share in ((required_crit, req_share), (preferred_crit, pref_share)):
            for c in group:
                if c.status == STATUS_CANNOT_DETERMINE:
                    c.pending_worth = round(weight_pct * share / len(group) * 100, 1)

        # Counters
        def _count(clist: list[DeterministicCriterionScore], status: str) -> int:
            return sum(1 for c in clist if c.status == status)

        low_conf = sum(
            1 for c in criteria
            if c.confidence < 0.35 and c.status in ("MATCHED", "PARTIAL")
        )
        has_oq = any(c.has_overqualification for c in criteria)
        review = low_conf > 0 or has_oq

        return DeterministicDimensionScore(
            dimension=dimension,
            dimension_score=dim_score,
            weighted_contribution=weighted_contrib,
            weight_pct=weight_pct,
            n_required=len(required_crit),
            n_required_matched=_count(required_crit, "MATCHED"),
            n_required_partial=_count(required_crit, "PARTIAL"),
            n_required_absent=_count(required_crit, "ABSENT"),
            n_preferred=len(preferred_crit),
            n_preferred_matched=_count(preferred_crit, "MATCHED"),
            n_preferred_partial=_count(preferred_crit, "PARTIAL"),
            n_preferred_absent=_count(preferred_crit, "ABSENT"),
            required_avg=req_avg,
            preferred_avg=pref_avg,
            required_absent_floor_triggered=absent_floor_triggered,
            has_overqualification_risk=has_oq,
            low_confidence_count=low_conf,
            review_recommended=review,
            criteria=criteria,
            dimension_score_upper=dim_score_upper,
            weighted_contribution_upper=weighted_contrib_upper,
            n_required_cannot_determine=_count(required_crit, STATUS_CANNOT_DETERMINE),
            n_preferred_cannot_determine=_count(preferred_crit, STATUS_CANNOT_DETERMINE),
        )


# ── Serialisation ─────────────────────────────────────────────────────────────

def _criterion_to_dict(c: DeterministicCriterionScore) -> dict[str, Any]:
    d = {
        "criterion_text":       c.criterion_text,
        "dimension":            c.dimension,
        "required":             c.required,
        "status":               c.status,
        "match_type":           c.match_type,
        "criterion_class":      c.criterion_class,
        "status_credit":        round(c.status_credit, 4),
        "quality_factor":       round(c.quality_factor, 4),
        "effective_credit":     round(c.effective_credit, 4),
        "confidence":           round(c.confidence, 4),
        "supporting_evidence":  c.supporting_evidence,
        "risk_flags":           c.risk_flags,
        "has_overqualification": c.has_overqualification,
        "cd_reason":            c.cd_reason,
        "match_reason":         c.match_reason,
        "verified_credit":      round(c.verified_credit, 4),
        "upper_credit":         round(c.upper, 4),
        "pending_worth":        c.pending_worth,
    }
    if c.reconciled_from is not None:
        d["reconciled_from"] = dict(c.reconciled_from)
    return d


def _dimension_to_dict(ds: DeterministicDimensionScore) -> dict[str, Any]:
    return {
        "dimension":                     ds.dimension,
        "dimension_score":               round(ds.dimension_score, 4),
        "dimension_score_upper":         round(ds.dimension_score_upper, 4),
        "weighted_contribution":         round(ds.weighted_contribution, 4),
        "weight_pct":                    round(ds.weight_pct, 4),
        "n_required":                    ds.n_required,
        "n_required_matched":            ds.n_required_matched,
        "n_required_partial":            ds.n_required_partial,
        "n_required_absent":             ds.n_required_absent,
        "n_preferred":                   ds.n_preferred,
        "n_preferred_matched":           ds.n_preferred_matched,
        "n_preferred_partial":           ds.n_preferred_partial,
        "n_preferred_absent":            ds.n_preferred_absent,
        "n_required_cannot_determine":   ds.n_required_cannot_determine,
        "n_preferred_cannot_determine":  ds.n_preferred_cannot_determine,
        "required_avg":                  round(ds.required_avg, 4),
        "preferred_avg":                 round(ds.preferred_avg, 4),
        "required_absent_floor_triggered": ds.required_absent_floor_triggered,
        "has_overqualification_risk":    ds.has_overqualification_risk,
        "low_confidence_count":          ds.low_confidence_count,
        "review_recommended":            ds.review_recommended,
        "criteria":                      [_criterion_to_dict(c) for c in ds.criteria],
    }


def _req_signal(coverage_pct: float | None) -> str:
    if coverage_pct is None:
        return "no_required_criteria"
    if coverage_pct == 100.0:
        return "fully_covered"
    if coverage_pct >= 80.0:
        return "mostly_covered"
    if coverage_pct >= 50.0:
        return "partially_covered"
    return "poorly_covered"


def _pref_signal(coverage_pct: float | None) -> str:
    if coverage_pct is None:
        return "no_preferred_criteria"
    if coverage_pct >= 70.0:
        return "well_covered"
    if coverage_pct >= 30.0:
        return "partially_covered"
    return "poorly_covered"


def _recruiter_signal(
    req_total: int,
    req_coverage: float | None,
    pref_coverage: float | None,
    partial_or_matched_pct: float | None = None,
    blocking_gaps: int = 0,
) -> tuple[str, str]:
    if req_total == 0:
        return ("NO_REQUIRED_CRITERIA", "No mandatory criteria defined")

    rc = req_coverage or 0.0
    pm = partial_or_matched_pct or 0.0
    pc = pref_coverage or 0.0

    if blocking_gaps == 0:
        if rc == 100.0:
            if pc >= 70.0:
                return ("STRONG_FULL_MATCH", "Excellent match")
            if pc >= 30.0:
                return ("STRONG_REQUIRED_WEAK_PREFERRED", "Core match, limited extras")
            return ("STRONG_REQUIRED_NO_PREFERRED", "Minimum viable match")
        if pm == 100.0:
            return ("NEAR_MATCH", "Near match")
        return ("MOSTLY_MET", "Mostly met, minor gaps")

    if blocking_gaps == 1 and pm >= 80.0:
        return ("MOSTLY_MET", "Mostly met, minor gaps")

    if rc >= 80.0:
        return ("NEAR_MATCH", "Near match")
    if rc >= 50.0 or pm >= 60.0:
        return ("PARTIAL_REQUIRED", "Significant gaps")
    return ("POOR_REQUIRED_COVERAGE", "Does not meet requirements")


_SIGNAL_TO_DECISION: dict[str, str] = {
    "STRONG_FULL_MATCH":              "qualified",
    "STRONG_REQUIRED_WEAK_PREFERRED": "qualified",
    "STRONG_REQUIRED_NO_PREFERRED":   "qualified",
    "NEAR_MATCH":                     "qualified",
    "MOSTLY_MET":                     "partial",
    "PARTIAL_REQUIRED":               "partial",
    "NO_REQUIRED_CRITERIA":           "partial",
    "POOR_REQUIRED_COVERAGE":         "rejected",
}


def decision_from_signal(signal: str) -> str:
    """Map recruiter_signal to a hiring decision (qualified/partial/rejected)."""
    return _SIGNAL_TO_DECISION.get(signal, "partial")


DECISION_NEEDS_VERIFICATION = "needs_verification"


def _signal_for_counts(
    req_total: int, req_matched: int, req_partial: int, req_absent: int,
    pref_total: int, pref_matched: int,
) -> tuple[str, str]:
    req_coverage  = round(req_matched / req_total * 100, 1) if req_total > 0 else None
    req_pm_pct    = round((req_matched + req_partial) / req_total * 100, 1) if req_total > 0 else None
    pref_coverage = round(pref_matched / pref_total * 100, 1) if pref_total > 0 else None
    return _recruiter_signal(
        req_total, req_coverage, pref_coverage,
        partial_or_matched_pct=req_pm_pct,
        blocking_gaps=req_absent,
    )


def recommendation_from_decisions(n_required_cd: int, d_lower: str, d_upper: str) -> str:
    """P0-02a recommendation rule. Never rejects because of CANNOT_DETERMINE.

    d_lower: decision with required CD counted in the total only.
    d_upper: decision with every required CD counted as MATCHED.
    """
    if n_required_cd == 0:
        return d_lower
    if d_upper == "qualified" and d_lower != "qualified":
        return DECISION_NEEDS_VERIFICATION
    if d_lower == "rejected" and d_upper != "rejected":
        return d_upper
    return d_lower


def deterministic_score_to_dict(score: DeterministicScore) -> dict[str, Any]:
    """Serialise a DeterministicScore to a JSON-safe dict (det_score_json schema)."""
    dims = score.dimensions

    req_total   = sum(ds.n_required          for ds in dims.values())
    req_matched = sum(ds.n_required_matched  for ds in dims.values())
    req_partial = sum(ds.n_required_partial  for ds in dims.values())
    req_absent  = sum(ds.n_required_absent   for ds in dims.values())
    pref_total   = sum(ds.n_preferred         for ds in dims.values())
    pref_matched = sum(ds.n_preferred_matched for ds in dims.values())
    pref_partial = sum(ds.n_preferred_partial for ds in dims.values())
    pref_absent  = sum(ds.n_preferred_absent  for ds in dims.values())
    req_cd  = sum(ds.n_required_cannot_determine  for ds in dims.values())
    pref_cd = sum(ds.n_preferred_cannot_determine for ds in dims.values())

    req_coverage  = round(req_matched / req_total * 100, 1)  if req_total  > 0 else None
    req_pm_pct    = round((req_matched + req_partial) / req_total * 100, 1) if req_total > 0 else None
    pref_coverage = round(pref_matched / pref_total * 100, 1) if pref_total > 0 else None
    pref_pm_pct   = round((pref_matched + pref_partial) / pref_total * 100, 1) if pref_total > 0 else None

    blocking_gaps  = req_absent  # confirmed gaps only; CANNOT_DETERMINE is never blocking
    fully_covered  = req_total > 0 and req_absent == 0 and req_partial == 0 and req_cd == 0
    # D_L: required CD counted in the total only (not matched/partial/absent)
    signal, label  = _signal_for_counts(
        req_total, req_matched, req_partial, req_absent, pref_total, pref_matched,
    )
    decision_verified_basis = decision_from_signal(signal)
    # D_U: every required CD counted as MATCHED
    signal_u, _ = _signal_for_counts(
        req_total, req_matched + req_cd, req_partial, req_absent, pref_total, pref_matched,
    )
    decision_if_verified = decision_from_signal(signal_u)
    recommendation = recommendation_from_decisions(
        req_cd, decision_verified_basis, decision_if_verified,
    )

    upper_score = score.upper_score if score.upper_score is not None else score.final_score
    verification_items = [
        {
            "criterion_text": c.criterion_text,
            "dimension":      c.dimension,
            "required":       c.required,
            "cd_reason":      c.cd_reason,
            "pending_worth":  c.pending_worth,
        }
        for ds in dims.values()
        for c in ds.criteria
        if c.status == STATUS_CANNOT_DETERMINE
    ]
    verification_items.sort(key=lambda i: (not i["required"], -i["pending_worth"]))

    required_summary = {
        "total":         req_total,
        "matched":       req_matched,
        "partial":       req_partial,
        "absent":        req_absent,
        "cannot_determine": req_cd,
        "coverage_pct":  req_coverage,
        "partial_or_matched_pct": req_pm_pct,
        "blocking_gaps": blocking_gaps,
        "fully_covered": fully_covered,
        "signal":        _req_signal(req_coverage),
    }

    preferred_summary = {
        "total":         pref_total,
        "matched":       pref_matched,
        "partial":       pref_partial,
        "absent":        pref_absent,
        "cannot_determine": pref_cd,
        "coverage_pct":  pref_coverage,
        "partial_or_matched_pct": pref_pm_pct,
        "signal":        _pref_signal(pref_coverage),
    }

    qs = score.qualitative_summary
    qs_dict = None
    if qs is not None:
        qs_dict = {
            "candidate_name":                qs.candidate_name,
            "evaluation_notes":              qs.evaluation_notes,
            "strengths":                     qs.strengths,
            "gaps_identified":               qs.gaps_identified,
            "suggested_interview_questions": qs.suggested_interview_questions,
        }

    return {
        "_schema":                          score.scoring_version,
        "final_score":                      score.final_score,
        "verified_score":                   score.final_score,
        "upper_score":                      upper_score,
        "pending_points":                   upper_score - score.final_score,
        "decision_verified_basis":          decision_verified_basis,
        "decision_if_verified":             decision_if_verified,
        "recommendation":                   recommendation,
        "verification_items":               verification_items,
        "scoring_version":                  score.scoring_version,
        "scored_at":                        score.scored_at,
        "mapper_version":                   score.mapper_version,
        "overqualification_risk_dimensions": score.overqualification_risk_dimensions,
        "required_summary":                 required_summary,
        "preferred_summary":                preferred_summary,
        "recruiter_signal":                 signal,
        "recruiter_label":                  label,
        "qualitative_summary":              qs_dict,
        "dimensions": {
            dim: _dimension_to_dict(ds)
            for dim, ds in score.dimensions.items()
        },
    }
