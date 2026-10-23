"""The effective decision in SQL: one rule for lists, filters, exports and analytics, matching services.eligibility_service.effective_decision.

    not_eligible      a mandatory gate failed
    review_required   a gate needs verification, or an item-level score is not final (an unclear Required item)
    not_scored        item-level scoring has no Required item (a preferred-only job)
    otherwise         the stored scoring decision (applications.decision), never rewritten
"""
from __future__ import annotations


def effective_decision_sql(alias: str = "a") -> str:
    return (f"(CASE WHEN {alias}.eligibility_status = 'not_eligible' THEN 'not_eligible' "
            f"WHEN {alias}.eligibility_status = 'review_required' OR {alias}.scoring_status = 'review_required' THEN 'review_required' "
            f"WHEN {alias}.scoring_status = 'not_scored' THEN 'not_scored' ELSE {alias}.decision END)")


EFFECTIVE_DECISION_SQL = effective_decision_sql("a")
