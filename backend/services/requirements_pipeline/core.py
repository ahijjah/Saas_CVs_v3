"""
Production service entry point for the requirements-v2 extraction pipeline (promoted from parser_candidates/requirements_v2_pipeline_1, which stays
unchanged). PURE: functions over plain dicts; no database, no model call, no extraction, no configuration. services/requirements_api.py is the only
caller; v2 creation, live extraction and scoring stay disabled.

STORED RECORD (analysis_json["requirements_pipeline"], next to analysis_json["requirements"], which stays the ONLY editable document)
  record_version, component_versions, provenance {extraction_prompt {version, sha256}, model}, job_description_sha256, raw_response {text, sha256,
  finish_reason}, raw_ai_output, extraction {...}, original_digest, review_records {injection, split_or, model_warnings}
It holds NO requirements document, NO snapshot (original_analysis_json is never written), and NO derived value: readiness, gates, unresolved issues,
normalized warnings and informational items are recomputed from (record, current document, original snapshot, admin policy) on every call. Whatever a
client sends for any of these is discarded before it gets here.

PRECEDENCE (both policies unless noted)
  invalid document > injection > split-OR > [policy Yes] classification review > [policy Yes] model importance conflict > structure review >
  preferred-only confirmation > ready. Injection and split-OR cannot be acknowledged.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from services.requirements_pipeline.injection import GUARD_VERSION as INJECTION_VERSION
from services.requirements_pipeline.injection import open_issues as injection_open
from services.requirements_pipeline.injection import reconcile as reconcile_injection
from services.requirements_pipeline.split_or import SPLIT_OR_VERSION
from services.requirements_pipeline.split_or import open_issues as split_or_open
from services.requirements_pipeline.split_or import reconcile as reconcile_split_or
from services.requirements_pipeline.warning_adapter import ADAPTER_VERSION
from services.requirements_pipeline.warning_adapter import acknowledge as acknowledge_conflict
from services.requirements_pipeline.warning_adapter import readiness as adapter_readiness
from services.requirements_pipeline.warning_adapter import reconcile as reconcile_warnings
from services.requirements_pipeline.warning_adapter import status as warning_status
from services.requirements_pipeline.warning_adapter import validate_review as validate_warning_review
from services.requirements_pipeline.warning_adapter import visible_warnings
from services.requirements_v2.acknowledgment import classification_status, get_review as get_classification_review
from services.requirements_v2.comparison import original_digest, snapshot_original
from services.requirements_v2.readiness import is_preferred_only
from services.requirements_v2.similarity import find_similar_items
from services.requirements_v2.structure import structure_status
from services.requirements_v2.validation import validate_final

PIPELINE_VERSION = "requirements-v2-pipeline-1"
CONTRACT_VERSION = "requirements-v2-pipeline-result-1"
RECORD_VERSION = "requirements-v2-pipeline-record-1"
STORAGE_KEY = "requirements_pipeline"
STATE_FAILED = "extraction_failed"
STATE_RECORD_INVALID = "pipeline_record_invalid"
GATE_ORDER = ("extraction", "validation", "injection", "split_or", "classification", "conflict", "structure", "confirmation", "no_items")
NOT_ACKNOWLEDGEABLE_GATES = ("injection", "split_or", "structure", "confirmation", "validation", "no_items", "extraction")
RECORD_KEYS = ("record_version", "component_versions", "provenance", "job_description_sha256", "raw_response", "raw_ai_output", "extraction",
                "original_digest", "review_records")
# every name a client might send for state that belongs to the server (discarded, listed in discarded_client_fields)
CLIENT_FORBIDDEN_KEYS = (STORAGE_KEY, "pipeline", "review_records", "raw_response", "raw_ai_output", "component_versions", "provenance", "readiness",
                         "gates", "unresolved_issues", "normalized_warnings", "informational", "original", "original_digest", "extraction")


class PipelineError(ValueError):
    """Stable code: not_acknowledgeable | unknown_gate | not_ready_for_confirmation | record_invalid."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def _issue_dicts(issues) -> list[dict]:
    return [{"code": i.code, "message": i.message, "category": i.category, "item_id": i.item_id} for i in issues]


def _readiness_dict(r) -> dict:
    return {"state": r.state, "can_proceed": r.can_proceed, "scoring_mode": r.scoring_mode, "reasons": _issue_dicts(r.reasons)}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plain(obj):
    """Pure JSON types (the frozen parser returns some tuples): the state must round-trip through JSON unchanged."""
    return json.loads(json.dumps(obj))


def _issue_dicts(issues) -> list[dict]:
    return [{"code": i.code, "message": i.message, "category": i.category, "item_id": i.item_id} for i in issues]


# ── derived view: readiness, gates, unresolved issues, informational items ──────────────────────────────────────────────────
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




# ── guard records: reconcile / acknowledge (the document itself is carried by the caller) ──────────────────────────
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


def reconcile_records(records: dict, doc: dict, *, user_id: str | None, at: str | None) -> tuple[dict, list[dict]]:
    """The three guard records reconciled against the document the server is about to store. (new records, audit events). `records` are the STORED
    records; nothing the client sent is ever passed here."""
    return _review_events({"review_records": records}, doc, user_id, at)


def acknowledge_gate(records: dict, doc: dict, gate: str, warning_id: str, *, user_id: str, at: str) -> dict:
    """New records after a person acknowledged ONE conflict warning. Only the conflict gate lives in the records; the classification acknowledgment is the
    frozen one on the document, done by the caller. Injection, split-OR and every other gate refuse."""
    if gate in NOT_ACKNOWLEDGEABLE_GATES:
        raise PipelineError(f"The {gate} gate cannot be acknowledged.", "not_acknowledgeable")
    if gate != "conflict":
        raise PipelineError(f"unknown gate {gate!r}", "unknown_gate")
    out = copy.deepcopy(records)
    out["model_warnings"] = acknowledge_conflict(out["model_warnings"], doc, warning_id, user_id=user_id, acknowledged_at=at)
    return out


def component_versions(extraction_prompt: dict | None) -> dict:
    return {"pipeline": PIPELINE_VERSION, "result_contract": CONTRACT_VERSION, "record": RECORD_VERSION, "extraction_prompt": extraction_prompt,
            "injection_guard": INJECTION_VERSION, "split_or_guard": SPLIT_OR_VERSION, "warning_adapter": ADAPTER_VERSION}


def to_record(state: dict, *, model: str | None = None) -> dict:
    """The storable part of an `extract` result (candidate or future worker): provenance and the server-owned records, nothing editable, nothing derived,
    no snapshot. Raises PipelineError when the extraction failed."""
    if not state.get("ok") or state.get("review_records") is None:
        raise PipelineError("A failed extraction has no record to store.", "record_invalid")
    cv = copy.deepcopy(state["component_versions"])
    return json.loads(json.dumps({
        "record_version": RECORD_VERSION, "component_versions": cv,
        "provenance": {"extraction_prompt": cv.get("extraction_prompt"), "model": model},
        "job_description_sha256": state["job_description_sha256"], "raw_response": state["raw_response"], "raw_ai_output": state["raw_ai_output"],
        "extraction": state["extraction"], "original_digest": state["original_digest"], "review_records": state["review_records"]}))


def verify_record(record: Any, original: dict | None) -> list[str]:
    """Integrity problems of a stored record (empty = usable). A record that fails is never silently ignored: the caller fails closed."""
    if not isinstance(record, dict):
        return ["record is not an object"]
    errs = [f"missing key {k}" for k in RECORD_KEYS if k not in record]
    if errs:
        return errs
    if record["record_version"] != RECORD_VERSION:
        errs.append("unexpected record version")
    raw = record["raw_response"]
    if not isinstance(raw, dict) or (isinstance(raw.get("text"), str) and raw.get("sha256") != _sha(raw["text"])):
        errs.append("raw response hash mismatch")
    rr = record["review_records"]
    if not isinstance(rr, dict) or set(rr) != {"injection", "split_or", "model_warnings"}:
        return errs + ["review_records must hold injection, split_or and model_warnings"]
    try:
        for name in ("injection", "split_or"):
            if not isinstance(rr[name], dict) or not isinstance(rr[name].get("issues"), list):
                errs.append(f"{name} record malformed")
        if isinstance(rr["injection"], dict) and rr["injection"].get("jd_sha256") != record["job_description_sha256"]:
            errs.append("injection record belongs to a different job description")
        errs += validate_warning_review(rr["model_warnings"])
        if rr["model_warnings"].get("jd_sha256") != record["job_description_sha256"]:
            errs.append("warning record belongs to a different job description")
    except (KeyError, TypeError, AttributeError):
        errs.append("review records malformed")
    if not isinstance(original, dict):
        errs.append("original snapshot missing")
    elif original_digest(snapshot_original(original)) != record["original_digest"]:
        errs.append("original snapshot does not match the pipeline record")
    return errs


def assemble_state(record: dict, doc: dict, original: dict) -> dict:
    """The result-contract state for (stored record, CURRENT document, original snapshot). The document is the single editable source: nothing in the
    record is read for requirements."""
    return {"contract_version": CONTRACT_VERSION, "component_versions": copy.deepcopy(record["component_versions"]), "ok": True, "status": "draft", "errors": [],
            "job_description_sha256": record["job_description_sha256"], "raw_response": copy.deepcopy(record["raw_response"]),
            "raw_ai_output": copy.deepcopy(record["raw_ai_output"]), "requirements": copy.deepcopy(doc), "original": copy.deepcopy(original),
            "original_digest": record["original_digest"], "extraction": copy.deepcopy(record["extraction"]),
            "review_records": copy.deepcopy(record["review_records"]), "policy": {"require_classification_acknowledgment": True}}


def with_records(record: dict, records: dict) -> dict:
    out = copy.deepcopy(record)
    out["review_records"] = copy.deepcopy(records)
    return out
