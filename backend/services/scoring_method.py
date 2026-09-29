"""
P0-01 — scoring methodology identifiers (application_scores.scoring_method).

Values must match the pattern enforced by migration 104
('^[a-z][a-z0-9_]*_v[0-9]+$'), so future versions such as 'deterministic_v2'
need no schema change.
"""
from __future__ import annotations

DETERMINISTIC = "deterministic_v1"          # D-01 criteria mapping + F-01 deterministic engine
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
