"""
requirements-v2 extraction pipeline 1 (candidate, offline, pure): ONE entry point and ONE result contract over
  * the frozen requirements-v2 parser (services.requirements_v2.extraction.parse_response, unchanged),
  * injection guard 1.1, split-OR guard 1, model-warning adapter 1 (the candidate guards, unchanged).

    state = extract(jd_text, raw_model_response_text, finish_reason="stop", extraction_prompt={...})   # no model call: the response is an input
    state = reconcile(stored_state, new_document, user_id=..., at=...)                                # every edit, one operation
    state = acknowledge(stored_state, "classification" | "conflict", warning_id, user_id=..., at=...)
    state = confirm_structure(...) / confirm_no_numeric_score(...)                                    # the frozen confirmations, guarded

The state is a plain JSON-serializable dict (contract in README.md). Raw output, the original snapshot and the draft stay exactly as the parser
produced them; every review record is SERVER-OWNED; readiness, gates and the unresolved-issue list are DERIVED from the current document on every
call (`evaluate`), so no stored or client-supplied review state can unblock anything.

PRECEDENCE (both classification-policy settings unless noted)
  extraction failed / invalid document (needs_review) > injection (needs_injection_review) > split-OR (needs_split_or_review)
  > [policy Yes] frozen classification review > [policy Yes] model importance conflict (needs_conflict_review)
  > frozen structure review > frozen preferred-only confirmation > ready.
Policy No: classification and conflict items stay visible and never block. Injection, split-OR, structure review and the preferred-only
confirmation do not depend on the policy. Generic model notes, duplicates and similarity warnings are informational and never block.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from parser_candidates.requirements_v2_injection_guard_1 import GUARD_VERSION as INJECTION_VERSION
from parser_candidates.requirements_v2_injection_guard_1 import inspect_result as inspect_injection
from parser_candidates.requirements_v2_injection_guard_1 import open_issues as injection_open
from parser_candidates.requirements_v2_injection_guard_1 import reconcile as reconcile_injection
from parser_candidates.requirements_v2_split_or_guard_1 import SPLIT_OR_VERSION
from parser_candidates.requirements_v2_split_or_guard_1 import inspect_result as inspect_split_or
from parser_candidates.requirements_v2_split_or_guard_1 import open_issues as split_or_open
from parser_candidates.requirements_v2_split_or_guard_1 import reconcile as reconcile_split_or
from parser_candidates.requirements_v2_warning_adapter_1 import ADAPTER_VERSION
from parser_candidates.requirements_v2_warning_adapter_1 import acknowledge as acknowledge_conflict
from parser_candidates.requirements_v2_warning_adapter_1 import build_review as build_warning_review
from parser_candidates.requirements_v2_warning_adapter_1 import reconcile as reconcile_warnings
from parser_candidates.requirements_v2_warning_adapter_1 import readiness as adapter_readiness
from parser_candidates.requirements_v2_warning_adapter_1 import status as warning_status
from parser_candidates.requirements_v2_warning_adapter_1 import validate_review as validate_warning_review
from parser_candidates.requirements_v2_warning_adapter_1 import visible_warnings
from services.requirements_v2.acknowledgment import (
    REVIEW_KEY, AcknowledgmentError, acknowledge_classification_warning, classification_status, get_review as get_classification_review,
    reconcile_classification_review, review_block_issues,
)
from services.requirements_v2.extraction import parse_response
from services.requirements_v2.readiness import (
    carry_confirmation, compute_readiness, confirm_no_numeric_score as frozen_confirm_no_numeric_score, is_preferred_only,
)
from services.requirements_v2.similarity import find_similar_items
from services.requirements_v2.structure import (
    STRUCTURE_KEY, StructureError, carry_structure_review, confirm_structure as frozen_confirm_structure, get_block as get_structure_block,
    reconcile_structure_review, structure_status,
)
from services.requirements_v2.validation import validate_final

PIPELINE_VERSION = "requirements-v2-pipeline-1"
CONTRACT_VERSION = "requirements-v2-pipeline-result-1"
FROZEN_BENCHMARK_COMMIT = "059c56b"
STATE_FAILED = "extraction_failed"
GATE_ORDER = ("extraction", "validation", "injection", "split_or", "classification", "conflict", "structure", "confirmation", "no_items")


class PipelineError(ValueError):
    """An operation is not allowed in the current state. `code` is stable: not_acknowledgeable | unknown_gate | extraction_failed | not_ready_for_confirmation."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain(obj):
    """Pure JSON types (the frozen parser returns some tuples): the state must round-trip through JSON unchanged."""
    return json.loads(json.dumps(obj))


def _issue_dicts(issues) -> list[dict]:
    return [{"code": i.code, "message": i.message, "category": i.category, "item_id": i.item_id} for i in issues]


def component_versions(parser_prompt: dict) -> dict:
    return {"pipeline": PIPELINE_VERSION, "result_contract": CONTRACT_VERSION,
            "parser": {"name": "services.requirements_v2.extraction.parse_response", "frozen_benchmark_commit": FROZEN_BENCHMARK_COMMIT},
            "extraction_prompt": parser_prompt, "injection_guard": INJECTION_VERSION, "split_or_guard": SPLIT_OR_VERSION, "warning_adapter": ADAPTER_VERSION}


# ── entry point ───────────────────────────────────────────────────────────────────────────────────────────────────
def extract(jd_text: str, raw_response_text: str, *, finish_reason: str | None = "stop", extraction_prompt: dict | None = None,
            require_classification_acknowledgment: bool = True) -> dict:
    """The model's raw response for `jd_text` -> the result contract. Pure: no model, network or database. `extraction_prompt` (version, sha256)
    records which prompt produced the response (the frozen parser only knows its pinned v2-1); `require_classification_acknowledgment` is the admin
    policy used for the readiness shown under "readiness" (both policies are always reported under "readiness.by_policy")."""
    res = parse_response(raw_response_text, jd_text, finish_reason)
    parser_prompt = dict(extraction_prompt) if extraction_prompt else {"version": res.prompt_version, "sha256": res.prompt_sha256, "source": "parser_default"}
    state: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION, "component_versions": component_versions(parser_prompt), "ok": res.ok, "status": res.status,
        "errors": _issue_dicts(res.errors), "job_description_sha256": _sha(jd_text),
        "raw_response": {"text": raw_response_text if isinstance(raw_response_text, str) else None, "sha256": _sha(raw_response_text) if isinstance(raw_response_text, str) else None,
                         "finish_reason": finish_reason},
        "raw_ai_output": copy.deepcopy(res.raw_ai_output), "requirements": None, "original": None, "original_digest": None,
        "extraction": None, "review_records": None, "policy": {"require_classification_acknowledgment": bool(require_classification_acknowledgment)}}
    if not res.ok:
        return evaluate(state, require_classification_acknowledgment=require_classification_acknowledgment)
    state["requirements"] = copy.deepcopy(res.requirements)
    state["original"], state["original_digest"] = copy.deepcopy(res.original), res.original_digest
    state["extraction"] = _plain({"scoreability": copy.deepcopy(res.scoreability), "category_weights": copy.deepcopy(res.category_weights),
                           "conditions": copy.deepcopy(res.conditions), "unmapped": copy.deepcopy(res.unmapped),
                           "parser_review": _issue_dicts(res.review), "parser_ai_warnings_kept": list(res.ai_warnings)})
    state["review_records"] = {"injection": inspect_injection(jd_text, res), "split_or": inspect_split_or(res),
                               "model_warnings": build_warning_review(jd_text, res.raw_ai_output, res.requirements)}
    return evaluate(state, require_classification_acknowledgment=require_classification_acknowledgment)


# ── derived view: readiness, gates, unresolved issues, informational items ──────────────────────────────────────────────────
def _readiness_dict(r) -> dict:
    return {"state": r.state, "can_proceed": r.can_proceed, "scoring_mode": r.scoring_mode, "reasons": _issue_dicts(r.reasons)}


def _compose(state: dict, policy: bool):
    doc, rr = state["requirements"], state["review_records"]
    return adapter_readiness(doc, rr["injection"], rr["split_or"], rr["model_warnings"], require_classification_acknowledgment=policy, original=state["original"])


def _issues(state: dict) -> list[dict]:
    doc, rr = state["requirements"], state["review_records"]
    out: list[dict] = []

    def add(gate, iid, kind, message, items=(), category=None, yes=True, no=True, resolve=()):
        out.append({"gate": gate, "id": iid, "kind": kind, "message": message, "item_ids": list(items), "category": category,
                    "blocks_when_ack_required": yes, "blocks_when_ack_not_required": no, "resolution_options": list(resolve)})
    val = validate_final(doc)
    for e in val.errors:
        add("validation", f"validation:{e.code}:{e.item_id or e.category or ''}", e.code, e.message, [e.item_id] if e.item_id else [], e.category,
            resolve=["edit the document until it validates (for example rebalance weights)"])
    if not val.ok:
        return out                                                  # the frozen readiness stops here too; nothing else is evaluated on an invalid document
    for i in injection_open(rr["injection"], doc):
        add("injection", i["id"], f"injection_{i['kind']}", i["reason"], [i["item_id"]] if i["item_id"] else [], i["category"] if i["category"] != "ALL" else None,
            resolve=["remove the item (and add your own requirement if it is genuine)"] if i["kind"] == "requirement"
            else [f"set the {i['category']} weight to a different value than {i['contaminated_applied_weight']} (applied) and {i['proposed_weight']} (proposed)"])
    for i in split_or_open(rr["split_or"], doc):
        add("split_or", i["id"], "split_or_requirement", i["reason"], i["item_ids"], i["category"],
            resolve=["keep one item with alternatives covering all options and remove the others", "remove all of them", "clear the links if they are independent"])
    cs = classification_status(doc)
    for wid in cs.unresolved:
        w = next(x for x in get_classification_review(doc)["warnings"] if x["id"] == wid)
        add("classification", wid, w["code"], w["message"], [w["item_id"]], w["category"], yes=True, no=False, resolve=["acknowledge", "reclassify as Required", "remove the item"])
    ws = warning_status(rr["model_warnings"], doc)
    by_id = {w["id"]: w for w in rr["model_warnings"]["item_warnings"]}
    for wid in ws.unresolved:
        w = by_id[wid]
        add("conflict", wid, w["code"], w["message"], [w["item_id"]], w["category"], yes=True, no=False, resolve=["acknowledge", "reclassify the item", "remove the item"])
    for iid in structure_status(doc, state["original"]).needs_review:
        add("structure", f"structure:{iid}", "structure_review_pending", "The wording of this item changed (or its structure was never confirmed).", [iid],
            resolve=["confirm the structure", "correct the alternatives / experience", "remove the item"])
    if is_preferred_only(doc) and doc.get("scoring_confirmation") is None:
        add("confirmation", "confirmation:no_numeric_score", "preferred_only_unconfirmed", "Only preferred items exist: explicit confirmation of 'no numerical score' is required.",
            resolve=["confirm no numeric score", "add a required item"])
    from services.requirements_v2.readiness import count_items
    if sum(count_items(doc)) == 0:
        add("no_items", "no_items", "no_items", "There are no requirements. Add at least one before proceeding.", resolve=["add a requirement"])
    out.sort(key=lambda x: GATE_ORDER.index(x["gate"]))
    return out


def evaluate(state: dict, *, require_classification_acknowledgment: bool = True) -> dict:
    """A new state with every DERIVED part recomputed from the current document and the server-owned records: readiness (for the given policy and for
    both), gates, unresolved issues, normalized warnings, informational items. Stored statuses and stale or client-supplied values are never used."""
    out = copy.deepcopy(state)
    out["policy"] = {"require_classification_acknowledgment": bool(require_classification_acknowledgment)}
    if not out["ok"]:
        failed = {"state": STATE_FAILED, "can_proceed": False, "scoring_mode": None,
                  "reasons": [{"code": e["code"], "message": e["message"], "category": e["category"], "item_id": e["item_id"]} for e in out["errors"]]}
        out.update(readiness={**failed, "policy_ack_required": bool(require_classification_acknowledgment), "by_policy": {"ack_required": failed, "ack_not_required": failed}},
                   gates={"extraction": {"open": len(out["errors"]), "blocks_when_ack_required": True, "blocks_when_ack_not_required": True}},
                   unresolved_issues=[{"gate": "extraction", "id": f"extraction:{e['code']}", "kind": e["code"], "message": e["message"], "item_ids": [], "category": None,
                                       "blocks_when_ack_required": True, "blocks_when_ack_not_required": True, "resolution_options": ["re-run the extraction"]} for e in out["errors"]],
                   normalized_warnings=[], informational={"similarity_warnings": [], "parser_review": [], "generic_model_notes": []})
        return out
    doc, rr = out["requirements"], out["review_records"]
    chosen, both = _compose(out, require_classification_acknowledgment), {"ack_required": _compose(out, True), "ack_not_required": _compose(out, False)}
    out["readiness"] = {**_readiness_dict(chosen), "policy_ack_required": bool(require_classification_acknowledgment),
                        "by_policy": {k: _readiness_dict(v) for k, v in both.items()}}
    issues = _issues(out)
    out["unresolved_issues"] = issues
    gates = {}
    for g in GATE_ORDER:
        items = [i for i in issues if i["gate"] == g]
        if items:
            gates[g] = {"open": len(items), "blocks_when_ack_required": any(i["blocks_when_ack_required"] for i in items),
                        "blocks_when_ack_not_required": any(i["blocks_when_ack_not_required"] for i in items)}
    out["gates"] = gates
    warnings = visible_warnings(rr["model_warnings"])
    out["normalized_warnings"] = warnings
    out["informational"] = {"similarity_warnings": find_similar_items(doc) if validate_final(doc).ok else [], "parser_review": out["extraction"]["parser_review"],
                            "generic_model_notes": [w for w in warnings if w["informational"]]}
    return out


# ── unified reconcile ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _carry(stored_doc: dict, incoming: dict) -> tuple[dict, dict]:
    """carry_server_owned (frozen), written out so the audit events are available: the client's confirmation / classification / structure blocks are
    discarded and the STORED ones are reconciled against the incoming items."""
    out = carry_confirmation(stored_doc, incoming)
    events: dict[str, Any] = {"classification_resolved": [], "classification_invalidated": [], "structure_invalidated": []}
    prior = get_classification_review(stored_doc)
    out.pop(REVIEW_KEY, None)
    if prior and not review_block_issues(prior):
        out[REVIEW_KEY] = copy.deepcopy(prior)
        try:
            rr = reconcile_classification_review(out)
            out, events["classification_resolved"], events["classification_invalidated"] = rr.doc, list(rr.resolved), list(rr.invalidated)
        except (KeyError, TypeError, AttributeError, IndexError):
            pass
    out = carry_structure_review(stored_doc, out)
    prior_s = get_structure_block(stored_doc)
    if prior_s:
        tmp = copy.deepcopy(out)
        tmp[STRUCTURE_KEY] = copy.deepcopy(prior_s)
        try:
            events["structure_invalidated"] = list(reconcile_structure_review(tmp).invalidated)
        except (KeyError, TypeError, AttributeError, IndexError):
            pass
    return out, events


def _review_events(stored: dict, doc: dict, user_id, at) -> tuple[dict, list[dict]]:
    rr, events = copy.deepcopy(stored["review_records"]), []
    rr["injection"], ev = reconcile_injection(rr["injection"], doc, user_id=user_id, at=at)
    events += [{"component": "injection_guard", **e} for e in ev]
    rr["split_or"], ev = reconcile_split_or(rr["split_or"], doc, user_id=user_id, at=at)
    events += [{"component": "split_or_guard", **e} for e in ev]
    rr["model_warnings"], ev = reconcile_warnings(rr["model_warnings"], doc)
    events += [{"component": "warning_adapter", "event": "resolved", "warning_id": w, "resolution": r} for w, r in ev["resolved"]]
    events += [{"component": "warning_adapter", "event": "acknowledgment_invalidated", "warning_id": w, "reason": r} for w, r in ev["invalidated"]]
    return rr, events


def reconcile(stored: dict, new_document: dict, *, user_id: str | None = None, at: str | None = None, client_state: Any = None,
              require_classification_acknowledgment: bool = True) -> tuple[dict, list[dict]]:
    """The ONE operation for an edit/save. `stored` is the TRUSTED persisted state; `new_document` the edited document. Everything server-owned is rebuilt
    from `stored`: the document's confirmation / classification / structure blocks come from the stored document (reconciled against the new items), and
    the three guard records from the stored records. `client_state` (anything the client sent besides the document: review records, readiness, statuses)
    is accepted only so that it can be ignored. Returns (new state, audit events)."""
    if not stored.get("ok"):
        raise PipelineError("The extraction failed; there is nothing to reconcile.", "extraction_failed")
    doc, carry_events = _carry(stored["requirements"], new_document)
    rr, events = _review_events(stored, doc, user_id, at)
    events += [{"component": "classification_review", "event": "resolved", "warning_id": w, "resolution": r} for w, r in carry_events["classification_resolved"]]
    events += [{"component": "classification_review", "event": "acknowledgment_invalidated", "warning_id": w, "reason": r} for w, r in carry_events["classification_invalidated"]]
    events += [{"component": "structure_review", "event": "record_invalidated", "item_id": i, "reason": r} for i, r in carry_events["structure_invalidated"]]
    new = copy.deepcopy(stored)
    new["requirements"], new["review_records"] = doc, rr
    return evaluate(new, require_classification_acknowledgment=require_classification_acknowledgment), events


def _commit(stored: dict, doc: dict, rr: dict, policy: bool) -> dict:
    new = copy.deepcopy(stored)
    new["requirements"], new["review_records"] = doc, rr
    return evaluate(new, require_classification_acknowledgment=policy)


def acknowledge(stored: dict, gate: str, warning_id: str, *, user_id: str, at: str, require_classification_acknowledgment: bool = True) -> dict:
    """Trusted server code only (the caller authenticated the user and checked the role). Only the policy-governed gates can be acknowledged; the injection and
    split-OR blockers have no acknowledgment at all. Acknowledging one gate never changes another."""
    if not stored.get("ok"):
        raise PipelineError("The extraction failed.", "extraction_failed")
    if gate in ("injection", "split_or", "structure", "confirmation", "validation", "no_items", "extraction"):
        raise PipelineError(f"The {gate} gate cannot be acknowledged.", "not_acknowledgeable")
    doc, rr = copy.deepcopy(stored["requirements"]), copy.deepcopy(stored["review_records"])
    if gate == "classification":
        doc = acknowledge_classification_warning(doc, warning_id, user_id=user_id, acknowledged_at=at)
    elif gate == "conflict":
        rr["model_warnings"] = acknowledge_conflict(rr["model_warnings"], doc, warning_id, user_id=user_id, acknowledged_at=at)
    else:
        raise PipelineError(f"unknown gate {gate!r}", "unknown_gate")
    return _commit(stored, doc, rr, require_classification_acknowledgment)


def confirm_structure(stored: dict, item_id: str, *, user_id: str, at: str, require_classification_acknowledgment: bool = True) -> dict:
    """The frozen structure confirmation (a person's statement that the item's alternatives/experience still match its wording)."""
    doc = frozen_confirm_structure(stored["requirements"], item_id, user_id=user_id, confirmed_at=at, original=stored["original"])
    return _commit(stored, doc, copy.deepcopy(stored["review_records"]), require_classification_acknowledgment)


def confirm_no_numeric_score(stored: dict, *, user_id: str, at: str, require_classification_acknowledgment: bool = True) -> dict:
    """The frozen preferred-only confirmation, allowed only when the PIPELINE readiness is needs_confirmation: no injection, split-OR or (policy Yes)
    unsettled classification/conflict review may stand."""
    state = evaluate(stored, require_classification_acknowledgment=require_classification_acknowledgment)
    if state["readiness"]["state"] != "needs_confirmation":
        raise PipelineError(f"Nothing to confirm (readiness is {state['readiness']['state']!r}).", "not_ready_for_confirmation")
    doc = frozen_confirm_no_numeric_score(stored["requirements"], user_id=user_id, confirmed_at=at, require_classification_acknowledgment=require_classification_acknowledgment,
                                          original=stored["original"])
    return _commit(stored, doc, copy.deepcopy(stored["review_records"]), require_classification_acknowledgment)


# ── contract check ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
REQUIRED_KEYS = ("contract_version", "component_versions", "ok", "status", "errors", "job_description_sha256", "raw_response", "raw_ai_output", "requirements", "original",
                 "original_digest", "extraction", "review_records", "policy", "readiness", "gates", "unresolved_issues", "normalized_warnings", "informational")


def validate_state(state: Any, jd_text: str | None = None) -> list[str]:
    """Shape and integrity problems of a state (empty = a well-formed contract). With `jd_text` (the stored job description) the guard records are also
    checked against a fresh detection from the stored raw response, so a record whose issues were deleted from storage is caught."""
    if not isinstance(state, dict):
        return ["not an object"]
    errs = [f"missing key {k}" for k in REQUIRED_KEYS if k not in state]
    if errs:
        return errs
    if state["contract_version"] != CONTRACT_VERSION:
        errs.append("unexpected contract version")
    raw = state["raw_response"]
    if isinstance(raw.get("text"), str) and raw.get("sha256") != _sha(raw["text"]):
        errs.append("raw response hash mismatch")
    try:
        json.dumps(state)
    except (TypeError, ValueError):
        errs.append("state is not JSON serializable")
    if state["ok"]:
        rr = state["review_records"]
        if not isinstance(rr, dict) or set(rr) != {"injection", "split_or", "model_warnings"}:
            errs.append("review_records must hold injection, split_or and model_warnings")
        else:
            if rr["injection"]["jd_sha256"] != state["job_description_sha256"]:
                errs.append("injection record belongs to a different job description")
            errs += validate_warning_review(rr["model_warnings"])
            if rr["model_warnings"]["jd_sha256"] != state["job_description_sha256"]:
                errs.append("warning record belongs to a different job description")
        if state["original_digest"] is None or state["original"] is None:
            errs.append("original snapshot missing")
        if isinstance(rr, dict) and "model_warnings" in rr and state["normalized_warnings"] != visible_warnings(rr["model_warnings"]):
            errs.append("normalized_warnings out of date (run evaluate)")
    if jd_text is not None and state["ok"] and not errs:
        if _sha(jd_text) != state["job_description_sha256"]:
            errs.append("job description does not match the state")
        else:
            fresh = extract(jd_text, state["raw_response"]["text"], finish_reason=state["raw_response"]["finish_reason"])
            for name in ("injection", "split_or"):
                sig = lambda st: sorted((i["kind"], i["category"]) for i in st["review_records"][name]["issues"])
                if sig(fresh) != sig(state):
                    errs.append(f"{name} record does not match a fresh detection")
    return errs
