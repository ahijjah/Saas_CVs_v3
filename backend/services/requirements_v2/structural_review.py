"""Server-owned structural-review contract (schema version 1). Validation and carry only: this module detects nothing.

    document["structural_review"] = {"schema_version": 1, "findings": [finding, ...]}

    finding = {id, code, item_ids, source_text, source_start, source_end, message, status, resolution}
      code        alternatives_split_across_items | independent_items_merged | importance_conflict_unresolved
      status      "open" (resolution must be None) | "resolved" (resolution must be complete)
      resolution  {decision, reviewer_user_id, resolved_at, note?, evidence_item_ids?}
                  decision: fixed | dismissed_false_positive | accepted_as_is

Ownership: a detector or the parser may create OPEN findings only. Resolution belongs to a future authenticated server review
service. carry_structural_review keeps the stored block on every save and discards whatever block the client sent. A new
analysis version never inherits resolutions. Source-unit omissions are not part of this contract.

Item ids: an open finding's item_ids must all exist in the current document. A resolved finding decided 'fixed' may cite
items that no longer exist (its evidence_item_ids must name current items); the other decisions require the original
item_ids to exist and a non-empty note.
Malformed blocks, unknown codes, bad item ids, bad offsets and incomplete resolutions all fail closed: readiness reports them
as structural_review_invalid. A document without the block is unchanged.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from services.requirements_v2.contract import Issue, collect_item_ids

STRUCTURAL_KEY = "structural_review"
STRUCTURAL_SCHEMA_VERSION = 1
CODE_ALTERNATIVES_SPLIT = "alternatives_split_across_items"
CODE_INDEPENDENT_MERGED = "independent_items_merged"
CODE_IMPORTANCE_CONFLICT = "importance_conflict_unresolved"
STRUCTURAL_CODES = (CODE_ALTERNATIVES_SPLIT, CODE_INDEPENDENT_MERGED, CODE_IMPORTANCE_CONFLICT)
STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
DECISIONS = ("fixed", "dismissed_false_positive", "accepted_as_is")
DECISION_FIXED = "fixed"
DECISION_NOT_A_FINDING = ("dismissed_false_positive", "accepted_as_is")
BLOCK_KEYS = frozenset({"schema_version", "findings"})
FINDING_KEYS = frozenset({"id", "code", "item_ids", "source_text", "source_start", "source_end", "message", "status", "resolution"})
RESOLUTION_REQUIRED = frozenset({"decision", "reviewer_user_id", "resolved_at"})
RESOLUTION_KEYS = RESOLUTION_REQUIRED | {"note", "evidence_item_ids"}
READINESS_OPEN = "structural_review_open"
READINESS_INVALID = "structural_review_invalid"


@dataclass(frozen=True)
class StructuralStatus:
    open_ids: tuple[str, ...]        # open finding ids (blocking)
    issues: tuple[Issue, ...]        # empty when the block is valid and nothing is open


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _resolution_problems(res: Any, items: set[str]) -> list[str]:
    """Rules by decision. fixed: evidence_item_ids is required and must name current items (the original items may be gone).
    dismissed_false_positive / accepted_as_is: a non-empty note is required; evidence is optional but must name current items."""
    if not isinstance(res, dict):
        return ["a resolved finding needs a resolution object"]
    out: list[str] = []
    if not RESOLUTION_REQUIRED <= set(res) or not set(res) <= RESOLUTION_KEYS:
        return ["a resolution must contain decision, reviewer_user_id and resolved_at, and no other fields"]
    decision = res["decision"]
    if decision not in DECISIONS:
        out.append(f"resolution decision must be one of {', '.join(DECISIONS)}")
    if not isinstance(res["reviewer_user_id"], str) or not res["reviewer_user_id"].strip():
        out.append("resolution reviewer_user_id must be a non-empty string")
    try:
        stamp = datetime.fromisoformat(res["resolved_at"]) if isinstance(res["resolved_at"], str) else None
    except ValueError:
        stamp = None
    if stamp is None or stamp.tzinfo is None:
        out.append("resolution resolved_at must be an ISO 8601 timestamp with a timezone")
    note = res.get("note")
    if note is not None and not isinstance(note, str):
        out.append("resolution note must be text or null")
    elif decision in DECISION_NOT_A_FINDING and not (isinstance(note, str) and note.strip()):
        out.append(f"decision {decision} needs a non-empty note explaining the decision")
    evidence = res.get("evidence_item_ids")
    if decision == DECISION_FIXED and not (isinstance(evidence, list) and evidence):
        out.append("decision fixed needs a non-empty evidence_item_ids list naming the corrected items")
    if evidence is not None:
        if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence) \
                or len(set(evidence)) != len(evidence) or not all(e in items for e in evidence):
            out.append("resolution evidence_item_ids must be unique ids of items in the current document")
    return out


def block_problems(block: Any, doc: dict, jd_text: Any) -> list[str]:
    """Every reason the block is not valid (empty list = valid). jd_text is needed to check the source offsets."""
    if not isinstance(jd_text, str):
        return ["the job description text is needed to check the source offsets of the structural findings"]
    if not isinstance(block, dict) or set(block) != BLOCK_KEYS:
        return ["structural_review must be {'schema_version', 'findings'} and nothing else"]
    if not _is_int(block["schema_version"]) or block["schema_version"] != STRUCTURAL_SCHEMA_VERSION:
        return [f"structural_review schema_version must be {STRUCTURAL_SCHEMA_VERSION}"]
    findings = block["findings"]
    if not isinstance(findings, list):
        return ["structural_review findings must be a list"]
    out: list[str] = []
    items = collect_item_ids(doc)
    seen: set[str] = set()
    for pos, f in enumerate(findings):
        where = f"finding {pos}"
        if not isinstance(f, dict) or set(f) != FINDING_KEYS:
            out.append(f"{where} must have exactly the contract fields")
            continue
        fid = f["id"]
        if not isinstance(fid, str) or not fid.strip() or fid in seen:
            out.append(f"{where} id must be a unique non-empty string")
        else:
            seen.add(fid)
        if f["code"] not in STRUCTURAL_CODES:
            out.append(f"{where} code {f['code']!r} is not an approved structural code")
        ids = f["item_ids"]
        historical = (f["status"] == STATUS_RESOLVED and isinstance(f["resolution"], dict)
                      and f["resolution"].get("decision") == DECISION_FIXED)
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
            out.append(f"{where} item_ids must be a non-empty list of unique ids")
        elif not historical and not all(i in items for i in ids):
            out.append(f"{where} item_ids must name items in the current document (only a fixed resolution may cite items that no longer exist)")
        text, start, end = f["source_text"], f["source_start"], f["source_end"]
        if not isinstance(text, str) or not text:
            out.append(f"{where} source_text must be a non-empty string")
        elif not (_is_int(start) and _is_int(end) and 0 <= start < end <= len(jd_text)):
            out.append(f"{where} source offsets must be integers with 0 <= start < end <= the description length")
        elif jd_text[start:end] != text:
            out.append(f"{where} source_text is not the description text at its offsets")
        if not isinstance(f["message"], str) or not f["message"].strip():
            out.append(f"{where} message must be a non-empty string")
        if f["status"] == STATUS_OPEN:
            if f["resolution"] is not None:
                out.append(f"{where} is open and must not carry a resolution")
        elif f["status"] == STATUS_RESOLVED:
            out.extend(f"{where}: {p}" for p in _resolution_problems(f["resolution"], items))
        else:
            out.append(f"{where} status {f['status']!r} is not open or resolved")
    return out


def structural_status(doc: Any, jd_text: Any) -> StructuralStatus:
    """No block: nothing. An invalid block: invalid issues only (fail closed). Otherwise the open findings."""
    if not isinstance(doc, dict) or STRUCTURAL_KEY not in doc:
        return StructuralStatus((), ())
    problems = block_problems(doc[STRUCTURAL_KEY], doc, jd_text)
    if problems:
        return StructuralStatus((), tuple(Issue(READINESS_INVALID, p) for p in problems))
    open_findings = [f for f in doc[STRUCTURAL_KEY]["findings"] if f["status"] == STATUS_OPEN]
    return StructuralStatus(tuple(f["id"] for f in open_findings), tuple(
        Issue(READINESS_OPEN, f["message"], item_id=f["item_ids"][0]) for f in open_findings))


def has_structural_block(doc: Any) -> bool:
    """A draft may carry no structural_review block at all, open or resolved: the server adds open findings only after draft
    validation, and resolution is server-owned."""
    return isinstance(doc, dict) and STRUCTURAL_KEY in doc


def carry_structural_review(stored: Any, incoming: Any) -> dict:
    """The document to persist for a save: `incoming` with its structural_review replaced by the STORED block (copied). Whatever
    block the client sent is discarded, so a client can neither add a resolution nor remove a finding. A stored finding is
    never dropped here; if an item it names no longer exists, readiness reports it as invalid."""
    out = copy.deepcopy(incoming) if isinstance(incoming, dict) else {}
    out.pop(STRUCTURAL_KEY, None)
    if isinstance(stored, dict) and STRUCTURAL_KEY in stored:
        out[STRUCTURAL_KEY] = copy.deepcopy(stored[STRUCTURAL_KEY])
    return out


class StructuralResolutionError(ValueError):
    """A finding cannot be resolved. `code` is stable and machine-readable; the message is for the editing service's caller."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def resolve_finding(doc: dict, finding_id: str, *, decision: str, user_id: str, resolved_at: str, jd_text: str,
                    note: str | None = None, evidence_item_ids: list[str] | None = None) -> dict:
    """A new document in which ONE open finding is resolved. Trusted server code only: the caller supplies the reviewer's identity
    and the time, and the document is validated against the description before it is returned. The decision, note and evidence are the
    reviewer's statements; nothing here checks them beyond the contract."""
    if decision not in DECISIONS:
        raise StructuralResolutionError(f"decision must be one of {', '.join(DECISIONS)}", "unknown_decision")
    if not isinstance(user_id, str) or not user_id or not isinstance(resolved_at, str) or not resolved_at:
        raise StructuralResolutionError("user_id and resolved_at are required.", "missing_stamp")
    block = doc.get(STRUCTURAL_KEY) if isinstance(doc, dict) else None
    if not isinstance(block, dict) or not isinstance(block.get("findings"), list):
        raise StructuralResolutionError("This document has no structural review.", "unknown_finding")
    out = copy.deepcopy(doc)
    finding = next((f for f in out[STRUCTURAL_KEY]["findings"] if f.get("id") == finding_id), None)
    if finding is None:
        raise StructuralResolutionError(f"unknown finding {finding_id!r}", "unknown_finding")
    if finding["status"] != STATUS_OPEN:
        raise StructuralResolutionError("This finding is already resolved.", "already_resolved")
    resolution: dict = {"decision": decision, "reviewer_user_id": user_id, "resolved_at": resolved_at}
    if note is not None:
        resolution["note"] = note
    if evidence_item_ids is not None:
        resolution["evidence_item_ids"] = list(evidence_item_ids)
    finding["status"], finding["resolution"] = STATUS_RESOLVED, resolution
    problems = block_problems(out[STRUCTURAL_KEY], out, jd_text)
    if problems:
        raise StructuralResolutionError(problems[0], "invalid_resolution")
    return out
