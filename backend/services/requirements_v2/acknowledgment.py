"""
Classification-warning acknowledgment (requirements-v2, isolated: pure functions over plain dicts, no I/O).

A CLASSIFICATION WARNING says the job description does not establish that a Preferred item really is Preferred (the
extraction raised preferred_cue_missing / preferred_cue_not_in_job_description / preferred_cue_not_linked_to_item).
Only these warnings are covered here; every other extraction warning stays informational and never blocks anything.

Policy (an explicit argument of compute_readiness, never read from anywhere in this package)
  require_classification_acknowledgment = True   (default; fail closed)
      an unresolved classification warning keeps the job from being ready (state needs_classification_review)
  require_classification_acknowledgment = False
      warnings stay visible (Readiness.open_warning_ids) but do not block
  A warning is SETTLED by the recruiter in one of two ways: correct the classification (the item is no longer
  Preferred, or is removed), or explicitly ACCEPT that flagged classification (acknowledge_classification_warning).

  Lifecycle of a warning (it describes ONE item's Preferred classification):
    active     the item exists and is Preferred. Unresolved until validly acknowledged.
    inactive   the item is Required (or otherwise not Preferred): there is no Preferred classification to warn about.
               Any acknowledgment is dropped at that moment (reconcile). The warning is KEPT, so:
    reopened   if the same item returns to Preferred, the warning is active again. Its evidence (the cue and source
               wording the extraction could not establish) is stored with the warning and cannot become verified
               inside this module, so it "remains unverified" and the warning is reassessed as open. The old
               acknowledgment is NEVER restored: under policy Yes a fresh acknowledgment is required; under policy No
               the warning is visible and does not block.
    resolved   the item was removed (item ids are never reused). Permanent.
  Re-verifying evidence against the job description is a server-side re-extraction concern: a later stage would
  replace or resolve the stored warning; nothing here pretends to do it.
  Acknowledging never changes the item: a Preferred item keeps weight None, carries no weight and contributes nothing
  to a numerical score, before and after acknowledgment.

Contract (document["classification_review"]; SERVER-OWNED, absent in documents that never had a warning)
  {"warnings": [{"id": "<item_id>:<code>", "code", "item_id", "category", "status": "open" | "resolved",
                 "resolution": None | "reclassified" | "item_removed", "message",
                 "evidence": {"code", "cue", "source_text"}}],
   "acknowledgments": [{"warning_id", "code", "item_id", "user_id", "acknowledged_at",
                        "item_state": {...the item as acknowledged...}, "item_state_hash", "evidence_hash"}]}

An acknowledgment is valid only while BOTH hashes still match the current item and the stored warning evidence:
changing the item's wording, classification, OR-alternatives, structured experience or category, or the warning's
evidence, invalidates it. Validity is recomputed from the document every time (classification_status), so a stale
acknowledgment never counts even if nobody pruned it; reconcile_classification_review() prunes it and reports what
changed so that the caller can audit it.

SERVER-OWNED, like the preferred-only confirmation
  * warnings are created by the extraction (parser) and acknowledgments by acknowledge_classification_warning();
  * carry_classification_review(stored, incoming) is the ONLY way a save keeps them: the client's block is discarded
    entirely (it can neither add an acknowledgment, nor edit one, nor delete a warning) and the stored block is
    reconciled against the incoming items;
  * the hashes are unkeyed digests: they detect change, they prove nothing about WHO acknowledged. A matching hash
    in client input is not authorization.

FUTURE WIRING (documented, deliberately not implemented in this stage -- see INTEGRATION_NOTES.md)
  Admin setting   key   job_analysis.require_classification_acknowledgment   (system_config; "true" / "false")
                  default true; a missing, empty or unrecognised value means true (parse_acknowledgment_policy).
  Endpoints       acknowledge: admin and HR manager only, one warning per call, stored state read inside the same
                  locked transaction, audit-logged; the setting: admin only, audit-logged with old and new value.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from services.requirements_v2.contract import CATEGORIES, IMPORTANCE_PREFERRED, Issue

REVIEW_KEY = "classification_review"
POLICY_KEY = "job_analysis.require_classification_acknowledgment"      # future system_config key (not wired)

CUE_MISSING = "preferred_cue_missing"
CUE_NOT_IN_JD = "preferred_cue_not_in_job_description"
CUE_NOT_LINKED = "preferred_cue_not_linked_to_item"
CLASSIFICATION_WARNING_CODES = (CUE_MISSING, CUE_NOT_IN_JD, CUE_NOT_LINKED)

STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"                 # stored only when the item was removed
RESOLVED_ITEM_REMOVED = "item_removed"

WARNING_KEYS = frozenset({"id", "code", "item_id", "category", "status", "resolution", "message", "evidence"})
EVIDENCE_KEYS = frozenset({"code", "cue", "source_text"})
ACK_KEYS = frozenset({"warning_id", "code", "item_id", "user_id", "acknowledged_at", "item_state",
                      "item_state_hash", "evidence_hash"})

_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off"}


class AcknowledgmentError(ValueError):
    """The warning cannot be acknowledged in the document's current state. `code` is stable and machine-readable
    (unknown_warning | no_longer_applies | already_acknowledged | invalid_request) so that an API can map it."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def parse_acknowledgment_policy(value: Any) -> bool:
    """The admin setting as a boolean. Default Yes: a missing, empty or unrecognised value means acknowledgment IS
    required (fail closed). Only an explicit no / false / 0 / off turns the requirement off."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _FALSE:
            return False
        if v in _TRUE:
            return True
    return True


# ── hashing and identity ──────────────────────────────────────────────────────

def _digest(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def warning_id(item_id: str, code: str) -> str:
    return f"{item_id}:{code}"


def evidence_hash(evidence: dict) -> str:
    return _digest({k: evidence.get(k) for k in sorted(EVIDENCE_KEYS)})


def item_state(category: str, item: dict) -> dict:
    """What an acknowledgment is given for: identity, category, wording, classification, structure and source."""
    return {"id": item["id"], "category": category, "text": item["text"], "importance": item["importance"],
            "source_text": item.get("source_text"), "alternatives": copy.deepcopy(item.get("alternatives")),
            "experience": copy.deepcopy(item.get("experience"))}


def item_state_hash(state: dict) -> str:
    return _digest(state)


def build_warning(*, code: str, item_id: str, category: str, cue: Any, source_text: str | None, message: str) -> dict:
    """A new OPEN classification warning (used by the extraction)."""
    if code not in CLASSIFICATION_WARNING_CODES:
        raise ValueError(f"{code!r} is not a classification warning code")
    return {"id": warning_id(item_id, code), "code": code, "item_id": item_id, "category": category,
            "status": STATUS_OPEN, "resolution": None, "message": message,
            "evidence": {"code": code, "cue": cue if isinstance(cue, str) else None, "source_text": source_text}}


def empty_review() -> dict:
    return {"warnings": [], "acknowledgments": []}


def get_review(doc: Any) -> dict | None:
    block = doc.get(REVIEW_KEY) if isinstance(doc, dict) else None
    return block if isinstance(block, dict) else None


# ── structure validation (used by validation.py) ──────────────────────────────

def review_block_issues(block: Any) -> list[Issue]:
    """Shape problems of a classification_review block (None / absent is fine)."""
    if block is None:
        return []
    if not isinstance(block, dict) or set(block) != {"warnings", "acknowledgments"} \
            or not isinstance(block["warnings"], list) or not isinstance(block["acknowledgments"], list):
        return [Issue("bad_classification_review", "classification_review must be {'warnings': [], 'acknowledgments': []}.")]
    out: list[Issue] = []
    ids: set[str] = set()
    for w in block["warnings"]:
        ok = (isinstance(w, dict) and set(w) == WARNING_KEYS and w["code"] in CLASSIFICATION_WARNING_CODES
              and isinstance(w["item_id"], str) and w["category"] in CATEGORIES
              and w["id"] == warning_id(w["item_id"], w["code"]) and w["status"] in (STATUS_OPEN, STATUS_RESOLVED)
              and ((w["status"] == STATUS_OPEN and w["resolution"] is None)
                   or (w["status"] == STATUS_RESOLVED and w["resolution"] == RESOLVED_ITEM_REMOVED))
              and isinstance(w["message"], str)
              and isinstance(w["evidence"], dict) and set(w["evidence"]) == EVIDENCE_KEYS
              and w["evidence"]["code"] == w["code"]
              and all(w["evidence"][k] is None or isinstance(w["evidence"][k], str) for k in ("cue", "source_text")))
        if not ok:
            out.append(Issue("bad_classification_warning", "A classification warning is malformed."))
        elif w["id"] in ids:
            out.append(Issue("duplicate_classification_warning", "Classification warning ids must be unique.",
                             category=w["category"], item_id=w["item_id"]))
        else:
            ids.add(w["id"])
    seen_acks: set[str] = set()
    by_id = {w["id"]: w for w in block["warnings"] if isinstance(w, dict) and isinstance(w.get("id"), str)}
    for a in block["acknowledgments"]:
        ok = (isinstance(a, dict) and set(a) == ACK_KEYS and isinstance(a["warning_id"], str)
              and all(isinstance(a[k], str) and a[k] for k in ("user_id", "acknowledged_at", "item_state_hash", "evidence_hash"))
              and isinstance(a["item_state"], dict) and a["warning_id"] in by_id
              and by_id[a["warning_id"]]["code"] == a["code"] and by_id[a["warning_id"]]["item_id"] == a["item_id"])
        if not ok:
            out.append(Issue("bad_classification_acknowledgment",
                             "A classification acknowledgment is malformed or refers to an unknown warning."))
        elif a["warning_id"] in seen_acks:
            out.append(Issue("duplicate_classification_acknowledgment", "Only one acknowledgment per warning.",
                             item_id=a["item_id"]))
        else:
            seen_acks.add(a["warning_id"])
    return out


# ── status ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ClassificationStatus:
    """Warning ids by state. `open` = active: the item is Preferred; `acknowledged` = open with a valid
    acknowledgment; `unresolved` = open without one (this is what the policy can turn into a blocker);
    `inactive` = the item is Required now (the warning reopens if it returns to Preferred); `resolved` = the item was
    removed (permanent)."""
    open: tuple[str, ...] = ()
    acknowledged: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    inactive: tuple[str, ...] = ()
    resolved: tuple[str, ...] = ()
    stale_acknowledgments: tuple[str, ...] = ()


def find_item(doc: dict, item_id: str) -> tuple[str, dict] | None:
    for c in CATEGORIES:
        for i in doc["categories"][c]["items"]:
            if i["id"] == item_id:
                return c, i
    return None


def _ack_for(block: dict, wid: str) -> dict | None:
    return next((a for a in block["acknowledgments"] if a.get("warning_id") == wid), None)


def _ack_is_valid(ack: dict | None, warning: dict, category: str, item: dict) -> bool:
    return (ack is not None and ack.get("code") == warning["code"] and ack.get("item_id") == warning["item_id"]
            and ack.get("item_state_hash") == item_state_hash(item_state(category, item))
            and ack.get("evidence_hash") == evidence_hash(warning["evidence"]))


def classification_status(doc: Any) -> ClassificationStatus:
    """Evaluate every warning against the document as it is NOW. Assumes a structurally valid document."""
    block = get_review(doc)
    if not block:
        return ClassificationStatus()
    open_, acknowledged, unresolved, inactive, resolved, stale = [], [], [], [], [], []
    for w in block["warnings"]:
        found = find_item(doc, w["item_id"])
        ack = _ack_for(block, w["id"])
        if w["status"] == STATUS_RESOLVED or found is None or found[1]["importance"] != IMPORTANCE_PREFERRED:
            (resolved if (w["status"] == STATUS_RESOLVED or found is None) else inactive).append(w["id"])
            if ack is not None:
                stale.append(w["id"])
            continue
        open_.append(w["id"])
        if _ack_is_valid(ack, w, *found):
            acknowledged.append(w["id"])
        else:
            unresolved.append(w["id"])
            if ack is not None:
                stale.append(w["id"])
    return ClassificationStatus(tuple(open_), tuple(acknowledged), tuple(unresolved), tuple(inactive),
                                tuple(resolved), tuple(stale))


def unresolved_warnings(doc: Any) -> list[dict]:
    block = get_review(doc)
    ids = set(classification_status(doc).unresolved)
    return [w for w in (block or {}).get("warnings", []) if w["id"] in ids]


# ── acknowledging ─────────────────────────────────────────────────────────────

def acknowledge_classification_warning(doc: dict, warning_id_: str, *, user_id: str, acknowledged_at: str) -> dict:
    """A new document in which the recruiter has accepted the flagged classification of ONE item. Trusted server code
    only (the caller has authenticated the user and checked the role). The item is not modified: a Preferred item
    stays Preferred with no weight."""
    if not user_id or not acknowledged_at or not isinstance(user_id, str) or not isinstance(acknowledged_at, str):
        raise AcknowledgmentError("user_id and acknowledged_at are required.")
    block = get_review(doc)
    warning = next((w for w in (block or {}).get("warnings", []) if w["id"] == warning_id_), None)
    if warning is None:
        raise AcknowledgmentError(f"unknown classification warning {warning_id_!r}", "unknown_warning")
    status = classification_status(doc)
    if warning_id_ in status.resolved or warning_id_ in status.inactive:
        raise AcknowledgmentError("This warning no longer applies: the item was reclassified or removed.", "no_longer_applies")
    if warning_id_ in status.acknowledged:
        raise AcknowledgmentError("This classification is already acknowledged.", "already_acknowledged")
    category, item = find_item(doc, warning["item_id"])                      # open implies the item exists
    state = item_state(category, item)
    out = copy.deepcopy(doc)
    new_block = get_review(out)
    new_block["acknowledgments"] = [a for a in new_block["acknowledgments"] if a["warning_id"] != warning_id_]
    new_block["acknowledgments"].append({
        "warning_id": warning_id_, "code": warning["code"], "item_id": warning["item_id"], "user_id": user_id,
        "acknowledged_at": acknowledged_at, "item_state": state, "item_state_hash": item_state_hash(state),
        "evidence_hash": evidence_hash(warning["evidence"]),
    })
    return out


# ── reconcile / carry ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReconcileResult:
    doc: dict
    resolved: tuple[tuple[str, str], ...] = ()          # (warning id, resolution) newly resolved (item removed)
    invalidated: tuple[tuple[str, str], ...] = ()       # (warning id, reason) acknowledgments removed -> audit these


def reconcile_classification_review(doc: dict) -> ReconcileResult:
    """Bring the stored review block in line with the items.

      * a warning whose item was REMOVED becomes resolved (permanent: item ids are never reused);
      * a warning whose item is no longer Preferred stays stored but INACTIVE, and is reassessed as open the moment the
        item is Preferred again (its stored evidence is still unverified);
      * an acknowledgment is removed when it no longer matches the item or the evidence, and whenever its warning is
        inactive or resolved, so that an old acceptance can never come back by itself.
    Everything removed is reported (`invalidated`, with a reason) so that the caller can write the audit log."""
    out = copy.deepcopy(doc)
    block = get_review(out)
    if not block:
        return ReconcileResult(out)
    resolved: list[tuple[str, str]] = []
    invalidated: list[tuple[str, str]] = []
    for w in block["warnings"]:
        if w["status"] == STATUS_OPEN and find_item(out, w["item_id"]) is None:
            w["status"], w["resolution"] = STATUS_RESOLVED, RESOLVED_ITEM_REMOVED
            resolved.append((w["id"], RESOLVED_ITEM_REMOVED))
    kept = []
    by_id = {w["id"]: w for w in block["warnings"]}
    for a in block["acknowledgments"]:
        w = by_id.get(a["warning_id"])
        found = find_item(out, w["item_id"]) if w else None
        if w is None or w["status"] == STATUS_RESOLVED or found is None:
            invalidated.append((a["warning_id"], "warning_resolved"))
        elif found[1]["importance"] != IMPORTANCE_PREFERRED:
            invalidated.append((a["warning_id"], "warning_inactive"))
        elif not _ack_is_valid(a, w, *found):
            invalidated.append((a["warning_id"], "item_or_evidence_changed"))
        else:
            kept.append(a)
    block["acknowledgments"] = kept
    return ReconcileResult(out, tuple(resolved), tuple(invalidated))


def carry_classification_review(stored: Any, incoming: Any) -> dict:
    """The document to validate and persist for a save: `incoming` with its classification_review replaced by the
    STORED one (reconciled against the incoming items). Whatever block the client sent is discarded, so a client can
    neither forge or edit an acknowledgment nor remove a warning. `stored` must be the trusted persisted document;
    a non-object `incoming` is returned as an unchanged copy (validation rejects it)."""
    if not isinstance(incoming, dict):
        return copy.deepcopy(incoming)
    out = copy.deepcopy(incoming)
    prior = get_review(stored)
    out.pop(REVIEW_KEY, None)
    if not prior or review_block_issues(prior):
        return out
    out[REVIEW_KEY] = copy.deepcopy(prior)
    try:
        return reconcile_classification_review(out).doc
    except (KeyError, TypeError, AttributeError, IndexError):          # malformed incoming items; validation rejects
        return out
