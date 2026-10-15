# PRODUCTION COPY of parser_candidates/requirements_v2_split_or_guard_1/guard.py; identical apart from import paths (pinned by tests/test_requirements_pipeline_service.py). Change the source first, never this file alone.
"""
requirements-v2 split-OR guard 1 (candidate, offline, pure; stdlib + the frozen requirements_v2 package + the injection guard).

PROBLEM. "Python or Java" is ONE requirement with two accepted options. The model sometimes returns two items instead, one per option,
that share the same source sentence. v2-1 gave each a single-entry alternatives list pointing at the other ("Python" / ["Java"] and
"Java" / ["Python"]); the frozen parser then drops those invalid lists (alternatives_invalid_dropped) and the job looks like two independent
required items. v2-2 gave each the COMPLETE list ("Python" / ["Python","Java"] and "Java" / ["Python","Java"]); that passes validation,
nothing is flagged, and the one requirement carries two weight slots.

WHAT THIS DOES. Inspect the RAW extraction (before the parser drops anything), find such groups, record them in a separate server-owned
review record and make readiness answer NEEDS_SPLIT_OR_REVIEW under both classification-policy settings. It never merges, deletes,
reclassifies or reweights anything and never touches the raw output or the original snapshot. There is no acknowledgment.

RESOLUTION (derived from the current document on every call). A group's issue is open until the recruiter, with the existing editing
operations, EITHER removes every item of the group, OR keeps exactly one of them with alternatives that cover all the options
(set_structure; remove_item for the redundant one), OR (a false positive) edits the items so that they no longer point at each other.
An unrelated edit changes nothing; restoring the split reopens the issue.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import re
from typing import Any

from services.requirements_pipeline.injection import NEEDS_INJECTION_REVIEW, guarded_readiness as injection_guarded_readiness
from services.requirements_pipeline.injection import open_issues as injection_open_issues
from services.requirements_v2.contract import CATEGORIES, Issue
from services.requirements_pipeline.text import normalize
from services.requirements_v2.readiness import Readiness

SPLIT_OR_VERSION = "requirements-v2-split-or-guard-1"
NEEDS_SPLIT_OR_REVIEW = "needs_split_or_review"
STATUS_OPEN, STATUS_RESOLVED = "open", "resolved"
RES_GROUP_REMOVED, RES_MERGED_ONE, RES_DECOUPLED = "group_removed", "kept_one_item_with_full_alternatives", "decoupled_by_recruiter"
FORM_SINGLE, FORM_COMPLETE, FORM_MIXED = "single_entry_mutual", "complete_alternatives_repeated", "mixed"

_OR_CONNECTIVE = re.compile(r"\b(?:or|either)\b|\w\s*/\s*\w|(?:^|\s)(?:أو|او)(?:\s|$)", re.I)   # or / either / x/y / أو
_TOKEN = re.compile(r"[^\W_]+", re.U)


# ── matching helpers ────────────────────────────────────────────────────────────────────────────────────────────
def _norm(s: Any) -> str:
    return normalize(s) if isinstance(s, str) else ""


def _tokens(s: str) -> set[str]:
    return set(_TOKEN.findall(s))


def same_option(a: str, b: str) -> bool:
    """Two option texts name the same option: equal after normalization, or one's words are all inside the other's
    ("اللغة العربية" / "العربية", "Python" / "Python 3"); containment needs at least 3 characters, equality any length."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:                                                   # any length: "R" is a real option
        return True
    if min(len(na), len(nb)) < 3:
        return False
    ta, tb = _tokens(na), _tokens(nb)
    return bool(ta and tb and (ta <= tb or tb <= ta))


def _alt_list(v: Any) -> list[str]:
    return [x.strip() for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


def _points_at(alts: list[str], text: str) -> bool:
    return any(same_option(a, text) for a in alts)


def _linked(members: list[dict]) -> bool:
    """members: [{text, alts}] sharing one OR-bearing source sentence. Linked when (a) every member's alternatives point at another
    member's text, or (b) one member's alternatives name ALL the others (a complete list repeated or partially repeated)."""
    if len(members) < 2:
        return False
    def out_edges(i):
        return [j for j, m in enumerate(members) if j != i and _points_at(members[i]["alts"], m["text"])]
    if all(out_edges(i) for i in range(len(members))):
        return True
    return any(len(out_edges(i)) == len(members) - 1 for i in range(len(members)))


# ── pairing raw items with document items (before the parser dropped anything) ───────────────────────────────────────
def _usable(raw_item: Any) -> tuple[bool, str, Any, Any]:
    if isinstance(raw_item, str):
        return bool(raw_item.strip()), raw_item.strip(), None, None
    if isinstance(raw_item, dict) and isinstance(raw_item.get("text"), str) and raw_item["text"].strip():
        return True, raw_item["text"].strip(), raw_item.get("alternatives"), raw_item.get("source_text")
    return False, "", None, None


def _paired(raw_ai_output: Any, doc: dict) -> dict[str, dict]:
    """document item id -> {raw_alts, raw_source}; a category whose raw and document items do not line up is skipped (fail closed to 'no finding')."""
    out: dict[str, dict] = {}
    cats = raw_ai_output.get("categories") if isinstance(raw_ai_output, dict) else None
    if not isinstance(cats, dict):
        return out
    for c in CATEGORIES:
        raw_list = cats.get(c)
        usable = [u for u in (_usable(r) for r in raw_list) if u[0]] if isinstance(raw_list, list) else []
        items = doc["categories"][c]["items"]
        if len(usable) != len(items) or any(u[1] != i["text"] for u, i in zip(usable, items)):
            continue
        for (_, _, alts, src), item in zip(usable, items):
            out[item["id"]] = {"raw_alts": _alt_list(alts), "raw_source": src if isinstance(src, str) else None}
    return out


def _issue_id(category: str, ids: list[str]) -> str:
    return "or_" + hashlib.sha1(f"{category}|{'|'.join(sorted(ids))}".encode("utf-8")).hexdigest()[:12]


# ── detection ───────────────────────────────────────────────────────────────────────────────────────────────────
def detect(raw_ai_output: Any, doc: dict) -> dict:
    """The split-OR review record for one extraction. Pure; `doc` is the frozen parser's draft document (never modified)."""
    pairing = _paired(raw_ai_output, doc)
    issues: list[dict] = []
    for c in CATEGORIES:
        groups: dict[str, list[dict]] = {}
        for item in doc["categories"][c]["items"]:
            p = pairing.get(item["id"])
            if p is None:
                continue
            evidence = _norm(item.get("source_text") or p["raw_source"])
            if evidence:
                groups.setdefault(evidence, []).append(item)
        for evidence, items in groups.items():
            if len(items) < 2 or not _OR_CONNECTIVE.search(evidence):
                continue                                  # unrelated items, or a sentence that offers no choice ("Excel and Power BI")
            members = [{"id": i["id"], "text": i["text"], "alts": pairing[i["id"]]["raw_alts"]} for i in items]
            if not _linked(members):
                continue
            lens = {len(m["alts"]) for m in members}
            form = FORM_SINGLE if lens == {1} else FORM_COMPLETE if min(lens) >= 2 else FORM_MIXED
            options: list[str] = []
            for text in [m["text"] for m in members] + [a for m in members for a in m["alts"]]:
                if not any(same_option(text, o) for o in options):
                    options.append(text)
            ids = [m["id"] for m in members]
            issues.append({
                "id": _issue_id(c, ids), "kind": "split_or", "status": STATUS_OPEN, "category": c, "item_ids": ids,
                "item_texts": {m["id"]: m["text"] for m in members}, "form": form, "options": options,
                "shared_evidence": items[0].get("source_text") or pairing[ids[0]]["raw_source"],
                "raw_alternatives": {m["id"]: m["alts"] for m in members},
                "document_alternatives_at_detection": {i["id"]: copy.deepcopy(i.get("alternatives")) for i in items},
                "rule_codes": ["same_category", "same_source_evidence", "or_connective_in_evidence", "alternatives_link_items"],
                "reason": "These items quote the same sentence and point at each other as options: they look like one OR requirement split into "
                          "one item per option.",
                "resolution": None, "history": []})
    return {"guard_version": SPLIT_OR_VERSION, "issues": issues}


def inspect_result(result) -> dict:
    """detect() for a frozen-parser ExtractionResult (its raw AI output is only read)."""
    return detect(result.raw_ai_output, result.requirements)


# ── status, resolution, readiness ───────────────────────────────────────────────────────────────────────────────
def _present(issue: dict, doc: dict) -> list[dict]:
    return [i for i in doc["categories"][issue["category"]]["items"] if i["id"] in issue["item_ids"]]


def _effective_alts(issue: dict, item: dict) -> list[str]:
    """The alternatives that count: what the recruiter has set if she changed them, otherwise what the model raw-returned (the frozen
    parser may have dropped an invalid list; that must not make the split disappear)."""
    if item.get("alternatives") != issue["document_alternatives_at_detection"].get(item["id"]):
        return _alt_list(item.get("alternatives"))
    return issue["raw_alternatives"][item["id"]]


def _is_open(issue: dict, doc: dict) -> bool:
    present = _present(issue, doc)
    if not present:
        return False                                                     # the whole group was removed
    if len(present) == 1:                                                # kept one: it must carry every option
        alts = _alt_list(present[0].get("alternatives"))
        return not (len(alts) >= 2 and all(_points_at(alts, o) for o in issue["options"]))
    return _linked([{"text": i["text"], "alts": _effective_alts(issue, i)} for i in present])


def open_issues(review: dict | None, doc: dict) -> list[dict]:
    """Issues that block right now (derived from the current document; no stored flag can close one)."""
    return [i for i in (review or {}).get("issues", []) if _is_open(i, doc)]


def _resolution_kind(issue: dict, doc: dict) -> str:
    n = len(_present(issue, doc))
    return RES_GROUP_REMOVED if n == 0 else RES_MERGED_ONE if n == 1 else RES_DECOUPLED


def reconcile(review: dict | None, doc: dict, *, user_id: str | None = None, at: str | None = None) -> tuple[dict | None, list[dict]]:
    """(new review, events). Call on every save with the TRUSTED stored review and the new document. Idempotent."""
    if not review:
        return review, []
    new, events = copy.deepcopy(review), []
    for i in new["issues"]:
        now_open = _is_open(i, doc)
        if now_open and i["status"] == STATUS_RESOLVED:
            i["status"], i["resolution"] = STATUS_OPEN, None
            i["history"].append({"event": "reopened", "by": user_id, "at": at})
            events.append({"event": "reopened", "issue_id": i["id"], "kind": "split_or"})
        elif not now_open and i["status"] == STATUS_OPEN:
            kind = _resolution_kind(i, doc)
            i["status"], i["resolution"] = STATUS_RESOLVED, {"kind": kind, "by": user_id, "at": at}
            i["history"].append({"event": "resolved", "kind": kind, "by": user_id, "at": at})
            events.append({"event": "resolved", "issue_id": i["id"], "kind": "split_or", "resolution": kind})
    return new, events


def carry_split_or_review(stored: dict | None, incoming: object = None) -> dict | None:
    """Server-owned: whatever the client sent is discarded; only the trusted stored record survives."""
    return copy.deepcopy(stored) if stored else None


def validate_review(review: object) -> list[str]:
    if not isinstance(review, dict) or review.get("guard_version") != SPLIT_OR_VERSION:
        return ["not a split-OR review record of this guard version"]
    errs = []
    for i in review.get("issues", []):
        if i.get("kind") != "split_or" or i.get("status") not in (STATUS_OPEN, STATUS_RESOLVED) or len(i.get("item_ids", [])) < 2:
            errs.append(f"bad record {i.get('id')}")
        if i.get("status") == STATUS_RESOLVED and not i.get("resolution"):
            errs.append(f"{i.get('id')}: resolved without a resolution")
        if not i.get("reason") or not i.get("shared_evidence"):
            errs.append(f"{i.get('id')}: evidence and reason are required")
    return errs


def composed_readiness(doc: dict, injection_review: dict | None, split_or_review: dict | None, *, require_classification_acknowledgment: bool = True,
                       original: dict | None | str = "not_evaluated") -> Readiness:
    """The frozen readiness composed with BOTH guards, under either policy value:
       injection issue open            -> needs_injection_review (always wins; open split-OR reasons are listed too)
       else split-OR issue open        -> needs_split_or_review
       else                            -> the frozen result
    An invalid document keeps its own NEEDS_REVIEW. Fixing the OR issue can never clear an injection blocker."""
    base = injection_guarded_readiness(doc, injection_review, require_classification_acknowledgment=require_classification_acknowledgment, original=original)
    live = open_issues(split_or_review, doc)
    if not live or base.state == "needs_review":
        return base
    reasons = tuple(Issue("split_or_requirement", f"{i['reason']} ({' / '.join(i['options'])})", category=i["category"], item_id=i["item_ids"][0]) for i in live)
    if base.state == NEEDS_INJECTION_REVIEW:
        return dataclasses.replace(base, reasons=base.reasons + reasons)
    return dataclasses.replace(base, state=NEEDS_SPLIT_OR_REVIEW, scoring_mode=None, reasons=reasons + tuple(base.reasons))
