"""Pure mapping from stored results to what the matrix shows. No database, no model.

The numbers (counts, eligibility, flag totals) are computed once, in SQL (query.py), from the CURRENT scoring run and the CURRENT effective
gate results. This module only names them: it never recomputes a score and never changes a gate outcome.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from services.matrix import registry as R

# Stored Required results -> what the matrix shows. "unclear" is shown as itself and flagged: it is never turned into a score here.
_MANDATORY = {"met": "met", "partially_met": "partially_met", "not_met": "not_met", "unclear": "unclear"}
# Stored Preferred results are yes / no / not_clear. The business statuses are Yes / No / Unclear: partial compliance does not exist for a
# Preferred item (the evaluator and a database constraint refuse it), and anything unexpected is shown as Unclear, never as partial.
_PREFERRED = {"yes": "yes", "no": "no", "not_clear": "unclear", "unclear": "unclear"}
# Stored effective gate result -> matrix status. A human override is the effective result: shown as passed, with overridden = true.
_GATE = {"passed": "passed", "overridden": "passed", "failed": "failed", "needs_verification": "review_required",
         "not_assessed": "not_assessed"}


def mandatory_status(stored: str | None) -> str:
    return _MANDATORY.get(stored or "", "not_assessed")


def preferred_status(stored: str | None) -> str:
    if stored is None:
        return "not_assessed"
    return _PREFERRED.get(stored, "unclear")


def gate_status(stored: str | None) -> str:
    return _GATE.get(stored or "", "not_assessed")


def _int(value: Any) -> int:
    return int(value) if value is not None else 0


def mandatory_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    if row.get("m_total") is None:
        return {"assessed": False, "met": None, "partially_met": None, "not_met": None, "unclear": None, "total": None}
    return {"assessed": True, "met": _int(row["m_met"]), "partially_met": _int(row["m_partial"]), "not_met": _int(row["m_not_met"]),
            "unclear": _int(row["m_unclear"]), "total": _int(row["m_total"])}


def preferred_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    if row.get("p_total") is None:
        return {"assessed": False, "yes": None, "no": None, "unclear": None, "total": None}
    return {"assessed": True, "yes": _int(row["p_yes"]), "no": _int(row["p_no"]), "unclear": _int(row["p_unclear"]), "total": _int(row["p_total"])}


def gate_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    total = _int(row.get("g_total"))
    if total == 0:
        return {"assessed": False, "passed": None, "failed": None, "review_required": None, "not_assessed": None, "total": 0}
    return {"assessed": True, "passed": _int(row["g_passed"]), "failed": _int(row["g_failed"]), "review_required": _int(row["g_review"]),
            "not_assessed": _int(row["g_na"]), "total": total}


def flag_total(row: Mapping[str, Any]) -> int:
    """Distinct things a person must look at: flagged items + gates needing verification + a processing error. Not a risk score."""
    return _int(row.get("fl_items")) + _int(row.get("g_review")) + _int(row.get("fl_proc"))


def flags(row: Mapping[str, Any], flagged: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The flag summary of one application. `flagged` lists the flagged evaluations of its current run:
    {item_id, importance, result, review_reason, gate_linked}. Severity is a plain label of how directly the flag can change the
    outcome (gate-linked or processing error: high; a Required item: medium; otherwise low); it is not a score."""
    def targets(predicate) -> list[dict[str, str]]:
        return [{"type": "item", "id": f["item_id"]} for f in flagged if predicate(f)]

    unclear_required = [f for f in flagged if f["importance"] == "required" and f["result"] == "unclear"]
    unclear_preferred = [f for f in flagged if f["importance"] == "preferred" and f["result"] == "not_clear"]
    entries: list[dict[str, Any]] = []

    def add(kind: str, severity: str, found: list[dict[str, str]]) -> None:
        if found:
            entries.append({"kind": kind, "severity": severity, "count": len(found), "targets": found})

    unclear_targets = [{"type": "item", "id": f["item_id"]} for f in unclear_required + unclear_preferred]
    unclear_severity = "high" if any(f.get("gate_linked") for f in unclear_required) else ("medium" if unclear_required else "low")
    add("unclear_criteria", unclear_severity, unclear_targets)
    add("conflicting_evidence", "medium", targets(lambda f: f["review_reason"] == "conflicting_evidence"))
    add("evidence_not_found", "medium", targets(lambda f: f["review_reason"] == "no_verified_evidence"))
    add("low_confidence", "low", targets(lambda f: f["review_reason"] == "low_confidence"))
    if _int(row.get("fl_proc")):
        add("processing_error", "high", [{"type": "application", "id": str(row["application_id"])}])
    review_targets = [{"type": "item", "id": f["item_id"]} for f in unclear_required] + list(row.get("review_gate_targets") or [])
    add("unresolved_review", "high", review_targets)
    return {"total": flag_total(row), "items": entries}


def eligibility_label_key(status: str | None) -> str:
    return status if status in R.ELIGIBILITY else "not_assessed"
