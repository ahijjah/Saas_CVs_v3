"""
Structured-requirement review (requirements-v2, pure: plain dicts, no I/O, no model).

An item may carry STRUCTURED FIELDS next to its wording: OR `alternatives` and (experience category) `experience`
{subject, min_years}. The wording is authoritative text for people; the structured fields are what later evaluation
would read. NOTHING here (or anywhere) can verify automatically that the two still agree, so after the wording changes
a person must say so.

An item "has structure" when it carries alternatives or an experience object. Its structure is SETTLED when ONE of:
  original   its current (wording, alternatives, experience) equal the original AI analysis' for the same item id
             (the AI's own reading is the baseline; nothing was edited, or the edit was reverted exactly);
  confirmed  a recruiter explicitly confirmed the CURRENT triple (confirm_structure);
  corrected  a recruiter changed the structured fields of an existing item (the save records it);
  entered    a recruiter created the item with structure.
Anything else NEEDS REVIEW -- in particular a wording-only edit. Saving the same structured values again is NOT a
confirmation: it creates no record, so it cannot settle anything.

The records live in document["structure_review"] = {"records": [...]} -- SERVER-OWNED exactly like classification_review
and scoring_confirmation: created only by confirm_structure / record_structure_edits (trusted server code with the
authenticated user and a server timestamp), kept across saves only by carry_structure_review (the client's block is
discarded), and valid only while the item's current triple still hashes to the recorded one. Any later change to the
wording OR the structure invalidates the record; reconcile_structure_review prunes it and reports why for the audit
log. A record never restores itself: reverting the wording makes the item match the record's hash again, but a stale
record was already pruned at the save that made it stale.

The hashes are unkeyed digests: they detect change, they do not prove who acted.
Required/Preferred, weights and classification warnings are not touched by anything in this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from services.requirements_v2.contract import CATEGORIES, Issue

STRUCTURE_KEY = "structure_review"
KIND_CONFIRMED, KIND_CORRECTED, KIND_ENTERED = "confirmed", "corrected", "entered"
KINDS = (KIND_CONFIRMED, KIND_CORRECTED, KIND_ENTERED)
RECORD_KEYS = frozenset({"item_id", "kind", "user_id", "recorded_at", "basis", "basis_hash"})
BASIS_KEYS = frozenset({"text", "alternatives", "experience"})

REASON_ITEM_REMOVED = "item_removed"
REASON_ITEM_CHANGED = "item_changed"

STATUS_ORIGINAL = "original"
STATUS_NEEDS_REVIEW = "needs_review"


class StructureError(ValueError):
    """The structure cannot be confirmed in the document's current state. `code` is stable and machine-readable
    (unknown_item | no_structure | already_settled | invalid_request)."""

    def __init__(self, message: str, code: str = "invalid_request"):
        super().__init__(message)
        self.code = code


def has_structure(item: dict) -> bool:
    return bool(item.get("alternatives")) or item.get("experience") is not None


def basis_of(item: dict) -> dict:
    """What a confirmation is given for: the wording and the structured fields, as they are right now."""
    return {"text": item["text"], "alternatives": copy.deepcopy(item.get("alternatives")),
            "experience": copy.deepcopy(item.get("experience"))}


def basis_hash(basis: dict) -> str:
    blob = json.dumps({k: basis.get(k) for k in sorted(BASIS_KEYS)}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get_block(doc: Any) -> dict | None:
    block = doc.get(STRUCTURE_KEY) if isinstance(doc, dict) else None
    return block if isinstance(block, dict) else None


def empty_block() -> dict:
    return {"records": []}


def block_issues(block: Any) -> list[Issue]:
    """Shape problems of a structure_review block (None / absent is fine)."""
    if block is None:
        return []
    if not isinstance(block, dict) or set(block) != {"records"} or not isinstance(block["records"], list):
        return [Issue("bad_structure_review", "structure_review must be {'records': []}.")]
    out: list[Issue] = []
    seen: set[str] = set()
    for r in block["records"]:
        ok = (isinstance(r, dict) and set(r) == RECORD_KEYS and isinstance(r["item_id"], str) and r["kind"] in KINDS
              and all(isinstance(r[k], str) and r[k] for k in ("user_id", "recorded_at", "basis_hash"))
              and isinstance(r["basis"], dict) and set(r["basis"]) == BASIS_KEYS
              and isinstance(r["basis"]["text"], str) and basis_hash(r["basis"]) == r["basis_hash"])
        if not ok:
            out.append(Issue("bad_structure_record", "A structure review record is malformed."))
        elif r["item_id"] in seen:
            out.append(Issue("duplicate_structure_record", "Only one structure record per item.", item_id=r["item_id"]))
        else:
            seen.add(r["item_id"])
    return out


def _index(doc: dict) -> dict[str, tuple[str, dict]]:
    return {i["id"]: (c, i) for c in CATEGORIES for i in doc["categories"][c]["items"]}


def find_text_item(doc: dict, item_id: str) -> tuple[str, dict]:
    return _index(doc)[item_id]


def _record_for(block: dict | None, item_id: str) -> dict | None:
    return next((r for r in (block or {}).get("records", []) if r.get("item_id") == item_id), None)


def _matches_original(item: dict, original_item: dict | None) -> bool:
    return original_item is not None and basis_of(item) == basis_of(original_item)


def _record_is_valid(record: dict | None, item: dict) -> bool:
    return record is not None and record.get("basis_hash") == basis_hash(basis_of(item))


@dataclass(frozen=True)
class StructureStatus:
    """Item ids by state, for items that have structure. `needs_review` is what blocks readiness."""
    original: tuple[str, ...] = ()
    confirmed: tuple[str, ...] = ()
    corrected: tuple[str, ...] = ()
    entered: tuple[str, ...] = ()
    needs_review: tuple[str, ...] = ()


def structure_status(doc: dict, original: dict | None) -> StructureStatus:
    """Evaluate every structured item against the document as it is NOW. `original` is the original AI analysis;
    None means "no baseline" (nothing is settled by the original, so everything unrecorded needs review: fail closed).
    Assumes a structurally valid document."""
    block = get_block(doc)
    base = _index(original) if isinstance(original, dict) and "categories" in original else {}
    out: dict[str, list[str]] = {"original": [], "confirmed": [], "corrected": [], "entered": [], "needs_review": []}
    for c in CATEGORIES:
        for item in doc["categories"][c]["items"]:
            if not has_structure(item):
                continue
            if _matches_original(item, base.get(item["id"], (None, None))[1]):
                out["original"].append(item["id"])
                continue
            rec = _record_for(block, item["id"])
            if _record_is_valid(rec, item):
                out[rec["kind"]].append(item["id"])
            else:
                out["needs_review"].append(item["id"])
    return StructureStatus(**{k: tuple(v) for k, v in out.items()})


def confirm_structure(doc: dict, item_id: str, *, user_id: str, confirmed_at: str, original: dict | None) -> dict:
    """A new document in which the recruiter has explicitly confirmed that the CURRENT structure of ONE item still
    matches its CURRENT wording. Trusted server code only. Allowed only while the item needs review. The item is not
    modified. This is a person's statement; nothing here checks it."""
    if not isinstance(user_id, str) or not user_id or not isinstance(confirmed_at, str) or not confirmed_at:
        raise StructureError("user_id and confirmed_at are required.")
    found = _index(doc).get(item_id)
    if found is None:
        raise StructureError(f"unknown item {item_id!r}", "unknown_item")
    category, item = found
    if not has_structure(item):
        raise StructureError("This item has no structured fields to confirm.", "no_structure")
    if item_id not in structure_status(doc, original).needs_review:
        raise StructureError("This item's structure is already settled.", "already_settled")
    out = copy.deepcopy(doc)
    block = get_block(out) or empty_block()
    block["records"] = [r for r in block["records"] if r["item_id"] != item_id]
    block["records"].append(_record(item, KIND_CONFIRMED, user_id, confirmed_at))
    out[STRUCTURE_KEY] = block
    return out


def _record(item: dict, kind: str, user_id: str, recorded_at: str) -> dict:
    basis = basis_of(item)
    return {"item_id": item["id"], "kind": kind, "user_id": str(user_id), "recorded_at": str(recorded_at),
            "basis": basis, "basis_hash": basis_hash(basis)}


@dataclass(frozen=True)
class StructureReconcileResult:
    doc: dict
    invalidated: tuple[tuple[str, str], ...] = ()      # (item id, reason): records removed -> audit these


def reconcile_structure_review(doc: dict) -> StructureReconcileResult:
    """Drop every record whose item was removed or whose wording / structure no longer hashes to the recorded basis."""
    out = copy.deepcopy(doc)
    block = get_block(out)
    if not block:
        return StructureReconcileResult(out)
    items = _index(out)
    kept, invalidated = [], []
    for r in block["records"]:
        found = items.get(r["item_id"])
        if found is None:
            invalidated.append((r["item_id"], REASON_ITEM_REMOVED))
        elif not _record_is_valid(r, found[1]):
            invalidated.append((r["item_id"], REASON_ITEM_CHANGED))
        else:
            kept.append(r)
    block["records"] = kept
    return StructureReconcileResult(out, tuple(invalidated))


def carry_structure_review(stored: Any, incoming: Any) -> dict:
    """`incoming` with its structure_review replaced by the STORED one, reconciled against the incoming items. The
    client's block is discarded entirely: it can neither add, edit nor keep a record."""
    if not isinstance(incoming, dict):
        return copy.deepcopy(incoming)
    out = copy.deepcopy(incoming)
    prior = get_block(stored)
    out.pop(STRUCTURE_KEY, None)
    if not prior or block_issues(prior):
        return out
    out[STRUCTURE_KEY] = copy.deepcopy(prior)
    try:
        return reconcile_structure_review(out).doc
    except (KeyError, TypeError, AttributeError, IndexError):          # malformed incoming items; validation rejects
        return out


def record_structure_edits(stored: dict, doc: dict, original: dict | None, *, user_id: str,
                           recorded_at: str) -> tuple[dict, tuple[tuple[str, str], ...]]:
    """After a save was rebuilt from trusted state: add a record for every item whose STRUCTURED FIELDS were changed
    by this save ("corrected") or that is new and carries structure ("entered"). A wording-only change records nothing,
    and neither does saving the same structured values. An item that already matches the original needs no record.
    Returns (new document, ((item id, kind), ...))."""
    out = copy.deepcopy(doc)
    before = _index(stored)
    base = _index(original) if isinstance(original, dict) and "categories" in original else {}
    block = get_block(out) or empty_block()
    recorded: list[tuple[str, str]] = []
    for c in CATEGORIES:
        for item in out["categories"][c]["items"]:
            prior = before.get(item["id"])
            if not has_structure(item) or _matches_original(item, base.get(item["id"], (None, None))[1]):
                continue
            if prior is None:
                kind = KIND_ENTERED
            elif (prior[1].get("alternatives"), prior[1].get("experience")) != (item.get("alternatives"), item.get("experience")):
                kind = KIND_CORRECTED
            else:
                continue
            block["records"] = [r for r in block["records"] if r["item_id"] != item["id"]]
            block["records"].append(_record(item, kind, user_id, recorded_at))
            recorded.append((item["id"], kind))
    if block["records"] or STRUCTURE_KEY in out:
        out[STRUCTURE_KEY] = block
    return out, tuple(recorded)
