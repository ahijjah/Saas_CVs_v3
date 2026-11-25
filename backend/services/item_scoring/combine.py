"""The approved logic of one criterion, applied to the evidence for each of its options. Pure: no model, no database.

The model assesses LEAVES: one per option, or one per part of an option that has parts (services/requirements_v2/logic.py). It never
decides how they combine. This module does, the same way every time:

    part results of one option      all met -> met | any not_met -> not_met | any unclear -> unclear | otherwise partially_met   (AND)
    the criterion, over its options
        a DIRECT option is met                  -> met           (the first one in the document's order is the matched option)
        else a REVIEW option is met             -> unclear, reason conditional_alternative: a person decides; never an automatic pass or fail
        else any option is unclear              -> unclear       (missing, inconclusive or unverifiable evidence is never "not met")
        else any option is partially met        -> partially_met
        else every option is not met            -> not_met       (each not_met already carries a verified quotation: evaluator.py)
    flagged ambiguity: a criterion the AI flagged as ambiguous and the recruiter left so can only be MET by evidence; a would-be partially_met
        or not_met becomes unclear (ambiguous_requirement). An ambiguous or conditional criterion therefore never fails a gate by itself.

One criterion has one weight: meeting several options earns nothing extra and there is nothing to double count. The outcome carries
the matched option and every option's result so each screen can show which alternative was met and why a case needs review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Sequence

from services.requirements_v2.logic import ACCEPT_DIRECT, ACCEPT_REVIEW, leaves

MET, PARTIALLY_MET, NOT_MET, UNCLEAR = "met", "partially_met", "not_met", "unclear"
CONDITIONAL_ALTERNATIVE = "conditional_alternative"
AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
MAX_QUOTES = 5


@dataclass(frozen=True)
class LeafResult:
    result: str                                    # met | partially_met | not_met | unclear
    evidence: tuple[str, ...] = ()
    explanation: str = ""
    confidence: Decimal | None = None
    review_reason: str | None = None               # no_verified_evidence | low_confidence | conflicting_evidence


@dataclass
class Outcome:
    result: str
    matched_option: str | None
    review_reason: str | None
    evidence: tuple[str, ...]
    explanation: str
    confidence: Decimal | None
    option_results: list[dict] = field(default_factory=list)


def _and(results: Sequence[str]) -> str:
    if all(r == MET for r in results):
        return MET
    if any(r == NOT_MET for r in results):
        return NOT_MET
    if any(r == UNCLEAR for r in results):
        return UNCLEAR
    return PARTIALLY_MET


def _unique(quotes: Sequence[str]) -> tuple[str, ...]:
    out: list[str] = []
    for q in quotes:
        if q not in out:
            out.append(q)
    return tuple(out[:MAX_QUOTES])


def option_outcome(option: dict, leaf_results: Mapping[str, LeafResult]) -> dict:
    """One option's result from its leaf (or its parts' leaves)."""
    mine = [(leaf, leaf_results.get(leaf["leaf_id"], LeafResult(UNCLEAR))) for leaf in leaves({"options": [option]})]
    result = _and([r.result for _, r in mine])
    reasons = [r.review_reason for _, r in mine if r.review_reason]
    confidences = [r.confidence for _, r in mine if r.confidence is not None]
    return {
        "option_id": option["id"], "text": option["text"], "acceptance": option["acceptance"], "result": result,
        "parts": None if not option.get("all_of") else [{"text": leaf["text"], "result": r.result} for leaf, r in mine],
        "evidence": list(_unique([q for _, r in mine for q in r.evidence])),
        "explanation": " ".join(e for e in (r.explanation for _, r in mine) if e)[:1000],
        "confidence": None if not confidences else str(min(confidences)),
        "review_reason": reasons[0] if reasons else None,
    }


def combine(logic: dict, leaf_results: Mapping[str, LeafResult]) -> Outcome:
    """The criterion's outcome. `logic["options"]` must be present (an item with only examples or an ambiguity note has no options to combine)."""
    options = logic.get("options") or []
    per_option = [option_outcome(o, leaf_results) for o in options]
    direct = [o for o in per_option if o["acceptance"] == ACCEPT_DIRECT]
    review = [o for o in per_option if o["acceptance"] == ACCEPT_REVIEW]
    matched: dict | None = None
    reason: str | None = None
    if any(o["result"] == MET for o in direct):
        matched = next(o for o in direct if o["result"] == MET)
        result = MET
    elif any(o["result"] == MET for o in review):
        matched = next(o for o in review if o["result"] == MET)
        result, reason = UNCLEAR, CONDITIONAL_ALTERNATIVE
    elif any(o["result"] == UNCLEAR for o in per_option):
        result = UNCLEAR
        reason = next((o["review_reason"] for o in per_option if o["result"] == UNCLEAR and o["review_reason"]), None)
    elif any(o["result"] == PARTIALLY_MET for o in per_option):
        result = PARTIALLY_MET
        matched = next(o for o in per_option if o["result"] == PARTIALLY_MET)
    else:
        result = NOT_MET
    if (logic.get("ambiguity") or None) and result in (NOT_MET, PARTIALLY_MET):
        result, reason = UNCLEAR, AMBIGUOUS_REQUIREMENT
    basis = matched or next((o for o in per_option if o["evidence"]), per_option[0] if per_option else None)
    evidence = tuple(basis["evidence"]) if basis else ()
    if result == UNCLEAR and matched is None:
        evidence = _unique([q for o in per_option for q in o["evidence"]])
    note = {CONDITIONAL_ALTERNATIVE: "An alternative that is accepted only after review was met, so a person decides.",
            AMBIGUOUS_REQUIREMENT: "The requirement is ambiguous and the evidence does not establish it, so a person decides."}.get(reason or "", "")
    explanation = " ".join(x for x in (note, basis["explanation"] if basis else "") if x)[:1000]
    confidence = Decimal(basis["confidence"]) if basis and basis["confidence"] is not None else None
    return Outcome(result, matched["option_id"] if matched else None, reason, evidence, explanation, confidence, per_option)
