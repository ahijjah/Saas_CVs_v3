"""
P0-02a — candidate-list score expressions and ordering (SQL fragments).

Shared by GET /applications and GET /applications/export. Assumes the
queries alias applications as `a` and application_scores as `s`.
"""
from __future__ import annotations

# Verified score = what the CV evidence supports (unchanged "score" column).
# Upper/pending/required-to-verify come from det_score_json (det_score_v3);
# rows without them (deterministic_v1, legacy, gatekeeper) read as
# upper = verified, pending = 0, required_to_verify = 0.
SQL_VERIFIED_SCORE = "COALESCE(s.det_final_score, s.final_score)"
SQL_PENDING_POINTS = (
    "COALESCE(CASE WHEN jsonb_typeof(s.det_score_json -> 'pending_points') = 'number' "
    "THEN (s.det_score_json ->> 'pending_points')::numeric END, 0)"
)
SQL_SCORE_UPPER = f"({SQL_VERIFIED_SCORE} + {SQL_PENDING_POINTS})"
SQL_REQUIRED_TO_VERIFY = (
    "COALESCE(CASE WHEN jsonb_typeof(s.det_score_json -> 'required_summary' -> 'cannot_determine') = 'number' "
    "THEN (s.det_score_json -> 'required_summary' ->> 'cannot_determine')::int END, 0)"
)

CANDIDATE_SORT_FIELDS = frozenset({
    "applied_at", "updated_at", "score", "score_upper", "pending_points", "candidate_name",
})


def candidate_order_by(sort_by: str, sort_order: str) -> str:
    """ORDER BY for candidate list/export queries (P0-02a).

    Default stays newest-first. Every ordering ends with a.application_id so
    rows with identical keys come back in a stable order. Score orderings put
    unscored rows last in either direction.

      score           verified desc, fewer required-to-verify, fewer pending
                      points, newest, application_id
      score_upper     upper desc, verified desc, newest, application_id
      pending_points  pending desc, verified desc, newest, application_id
    """
    direction = "ASC" if (sort_order or "").lower() == "asc" else "DESC"
    if sort_by == "score":
        return (
            f"{SQL_VERIFIED_SCORE} {direction} NULLS LAST, "
            f"{SQL_REQUIRED_TO_VERIFY} ASC, {SQL_PENDING_POINTS} ASC, "
            "a.applied_at DESC, a.application_id"
        )
    if sort_by == "score_upper":
        return (
            f"{SQL_SCORE_UPPER} {direction} NULLS LAST, "
            f"{SQL_VERIFIED_SCORE} DESC NULLS LAST, a.applied_at DESC, a.application_id"
        )
    if sort_by == "pending_points":
        return (
            f"(CASE WHEN {SQL_VERIFIED_SCORE} IS NULL THEN NULL ELSE {SQL_PENDING_POINTS} END) "
            f"{direction} NULLS LAST, "
            f"{SQL_VERIFIED_SCORE} DESC NULLS LAST, a.applied_at DESC, a.application_id"
        )
    column = {
        "applied_at": "a.applied_at",
        "updated_at": "a.scored_at",
        "candidate_name": "a.candidate_name",
    }.get(sort_by, "a.applied_at")
    return f"{column} {direction}, a.application_id"
