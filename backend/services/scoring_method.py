"""
P0-01 — scoring methodology identifiers (application_scores.scoring_method).

Values must match the pattern enforced by migration 104
('^[a-z][a-z0-9_]*_v[0-9]+$'), so future versions such as 'deterministic_v2'
need no schema change.
"""
from __future__ import annotations

DETERMINISTIC = "deterministic_v1"          # historical: D-01 + F-01 before P0-02a (three-state contract)
DETERMINISTIC_V2 = "deterministic_v2"       # P0-02a: four-state D-01 contract, strict validation,
                                            # verified/upper scores (det_score_v3)
LEGACY_LLM = "legacy_llm_v1"                # single-call LLM scorer (cv_scoring prompt)
GATEKEEPER_LOCAL = "gatekeeper_local_v1"    # Level-1 local gatekeeper rejection, no AI
LEGACY_LLM_DET_BACKFILL = "legacy_llm_det_backfill_v1"  # historical: legacy row + backfilled det score


def has_mixed_scoring_methods(scoring_methods: dict[str, int]) -> bool:
    """True when a job's scores come from more than one ranking methodology.

    gatekeeper_local_v1 is a pre-AI rejection (score 0), not a ranking method,
    so it never makes a job "mixed" on its own. Unclassified history
    ('unknown') counts as its own method.
    """
    ranking = {m for m, n in scoring_methods.items() if n and m != GATEKEEPER_LOCAL}
    return len(ranking) > 1


def verification_summary(det_score_json: dict | None, verified_score: int | float | None) -> dict:
    """P0-02a verification fields for API responses.

    Reads det_score_v3 fields; any older payload (deterministic_v1, legacy,
    backfill, gatekeeper, none) reads as fully verified: upper = verified,
    pending = 0, nothing to verify.
    """
    d = det_score_json if isinstance(det_score_json, dict) else {}

    def _num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def _count(summary_key: str) -> int:
        summary = d.get(summary_key)
        n = _num(summary.get("cannot_determine")) if isinstance(summary, dict) else None
        return int(n) if n is not None else 0

    pending = _num(d.get("pending_points")) or 0
    if verified_score is None:
        upper = None
        pending = 0
    else:
        upper = _num(d.get("upper_score"))
        if upper is None:
            upper = verified_score + pending
    decision_if_verified = d.get("decision_if_verified")
    return {
        "verified_score":       verified_score,
        "score_upper":          upper,
        "pending_points":       pending,
        "required_to_verify":   _count("required_summary"),
        "preferred_to_verify":  _count("preferred_summary"),
        "decision_if_verified": decision_if_verified if isinstance(decision_if_verified, str) else None,
    }
