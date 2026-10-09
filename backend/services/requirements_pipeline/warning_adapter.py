# PRODUCTION COPY of parser_candidates/requirements_v2_warning_adapter_1/adapter.py; identical apart from import paths (pinned by tests/test_requirements_pipeline_service.py). Change the source first, never this file alone.
"""
requirements-v2 warning adapter 1 (candidate, offline, pure; stdlib + the frozen requirements_v2 package + the two candidate guards).

PROBLEM. The model's `warnings` field is where it reports ambiguities and conflicts ("PostgreSQL is optional but listed as required"). The frozen
parser keeps only STRING warnings (as `ai_warnings`) and silently drops anything else, e.g. {"text", "reason", "source_text"} objects; nothing links a
warning to an item, so even a perfect conflict warning is invisible to the recruiter workflow.

WHAT THIS DOES (it never changes items, classifications, structure or weights; the raw output and the original snapshot stay as they are):
 1. normalize_warnings(): every warning is preserved. Strings and objects (text / reason / source_text) are normalized without losing content; other
    object fields are kept under `extra`; malformed or unrecognized warnings are kept verbatim with a format issue and are NEVER interpreted.
 2. classify + link: a warning becomes an ITEM-SPECIFIC IMPORTANCE CONFLICT only when (a) it uses importance wording (optional / required / preferred /
    mandatory ...), (b) it mentions an extracted item, and (c) the job description ITSELF states that item with contradictory importance (a required
    statement and a preferred/optional statement about the same item, ignoring AI-directed sentences). If (c) fails, or no item is identified, the
    warning stays informational and the limitation is written on it. Generic ambiguity, duplicates and unrelated warnings are informational.
 3. Item-specific conflicts follow the EXISTING classification-acknowledgment lifecycle (same hashing, same states): policy Yes -> unresolved ones block
    (needs_conflict_review) until the recruiter corrects the item (reclassifies or removes it) or acknowledges it; policy No -> visible, non-blocking.
    Item/evidence changes invalidate an acknowledgment; a later reversal reopens the warning and an old acknowledgment is never restored.
 4. readiness() composes with BOTH candidate guards: injection and split-OR blockers come first, under either policy, and no acknowledgment of a
    conflict can bypass them.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
import unicodedata
from typing import Any

from services.requirements_pipeline.injection import _mask_quotes, _sentences, instruction_spans
from services.requirements_pipeline.split_or import composed_readiness
from services.requirements_v2.acknowledgment import AcknowledgmentError, find_item, item_state, item_state_hash
from services.requirements_v2.contract import CATEGORIES, Issue
from services.requirements_pipeline.text import locate_span
from services.requirements_v2.readiness import Readiness

ADAPTER_VERSION = "requirements-v2-warning-adapter-1"
NEEDS_CONFLICT_REVIEW = "needs_conflict_review"
CODE_CONFLICT = "model_importance_conflict"
STATUS_OPEN, STATUS_RESOLVED, RES_ITEM_REMOVED = "open", "resolved", "item_removed"

KIND_CONFLICT, KIND_UNCORROBORATED, KIND_NOT_IDENTIFIED = "importance_conflict", "importance_note_uncorroborated", "importance_conflict_item_not_identified"
KIND_DUPLICATE, KIND_GENERIC, KIND_UNRELATED, KIND_MALFORMED = "duplicate", "generic", "unrelated", "malformed"
SUPPORTED_FIELDS = ("text", "reason", "source_text")


# ── text canonicalization (Arabic letters folded, spaces kept) ───────────────────────────────────────────────────────────
_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_INVISIBLE = re.compile("[ـ​-‏⁠﻿­]")


def _canon(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.translate(_INDIC))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _INVISIBLE.sub("", t).replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"\s+", " ", t).lower().strip()


_OPTIONAL = re.compile(
    r"\b(?:optional(?:ly)?|preferred|prefer|nice[- ]to[- ]have|desirable|advantage|a plus|bonus|not (?:strictly )?(?:required|mandatory|essential|necessary)|"
    r"(?:isn'?t|is not|aren'?t|are not) (?:required|mandatory|essential)|none of it is required)\b|"
    r"اختياري|اختياريه|مفضل|مفضله|يفضل|ميزه|افضليه|اضافي|اضافيه|غير مطلوب|غير الزامي|ليس شرطا|ليس الزاميا")
_REQUIRED = re.compile(r"\b(?:required|mandatory|compulsory|essential|must|necessary)\b|مطلوب|الزامي|الزاميه|ضروري|اساسي|يجب|لازم")
_CONTRADICTION = re.compile(r"contradict|conflict|inconsisten|at odds|incompatible|differs from|تعارض|يتعارض|متعارض|تناقض|يتناقض|متناقض|يخالف|مخالف")
_AMBIGUITY = re.compile(r"ambigu|unclear|vague|not clear|not specified|missing|غامض|غير واضح|مبهم|غير محدد|لم يذكر|لم يتم ذكر")
_DUPLICATE = re.compile(r"duplicat|repeat|redundan|already (?:listed|stated|mentioned|exists)|same requirement|تكرار|مكرر|متكرر|موجود بالفعل")
_HEAD_PREFERRED = re.compile(r"preferred|nice[- ]to[- ]have|desirable|\bplus\b|bonus|optional|additional|extra|يفضل|مفضل|مفضله|اضافي|اضافيه|ميزه|اختياري")
_HEAD_REQUIRED = re.compile(r"requirement|required|must[- ]have|qualification|mandatory|essential|المتطلبات|المؤهلات|الشروط|مطلوب|الزامي")

_STOP = set("""a an the of in on at to for from by with and or is are was be been being as this that these those it its role job position candidate applicant
skill skills experience knowledge proficiency ability familiarity understanding working strong good basic excellent years year degree certification
note recruiter manager hr company team also all any some other new not no than then which who will would should can may we you your our their
او و في من على الى إلى عن مع هذه هذا هذه الوظيفه الوظيفة خبره خبرة معرفه معرفة اجاده إجادة القدره القدرة مهارات مهاره مهارة سنوات سنه سنة ملاحظه ملاحظة
مسؤول التوظيف شركه شركة لهذه لهذا بين كما ايضا أيضا""".split())
_LATIN = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")
_ARABIC = re.compile(r"[؀-ۿ]+")


def _tokens(text: str) -> set[str]:
    t = _canon(text)
    out = set()
    for m in _LATIN.findall(t):
        m = m.strip(".-")
        if m and m not in _STOP and not _OPTIONAL.fullmatch(m) and not _REQUIRED.fullmatch(m) and (len(m) > 1 or m.isalpha()):
            out.add(m)
    for m in _ARABIC.findall(t):
        for pre in ("وال", "بال", "لل", "ال"):
            if m.startswith(pre) and len(m) > len(pre) + 1:
                m = m[len(pre):]
                break
        if len(m) > 2 and m not in _STOP and not _OPTIONAL.search(m) and not _REQUIRED.search(m):
            out.add(m)
    return out


def _mentions(item_tokens: set[str], text_tokens: set[str]) -> bool:
    return bool(item_tokens) and len(item_tokens & text_tokens) / len(item_tokens) >= 0.5


def _wording(text: str) -> str | None:
    c = _canon(text)
    opt = bool(_OPTIONAL.search(c))
    req = bool(_REQUIRED.search(_OPTIONAL.sub(" ", c)))
    return "preferred" if opt and not req else "required" if req and not opt else None


# ── normalization of the model's warnings ────────────────────────────────────────────────────────────────────────────────────
def normalize_warnings(raw_ai_output: Any) -> list[dict]:
    """One record per warning, in order. Nothing is dropped: `raw` is the verbatim original; supported object fields (text, reason, source_text) are
    normalized; other fields go to `extra`; malformed/unrecognized warnings are kept with a format issue and are never interpreted."""
    if not isinstance(raw_ai_output, dict) or raw_ai_output.get("warnings") is None:
        return []
    ws = raw_ai_output["warnings"]
    entries = ws if isinstance(ws, list) else [ws]
    out = []
    for i, e in enumerate(entries):
        rec = {"index": i, "raw": copy.deepcopy(e), "form": "malformed", "text": None, "reason": None, "source_text": None, "extra": {}, "format_issues": []}
        if isinstance(e, str):
            if e.strip():
                rec["form"], rec["text"] = "string", e.strip()
            else:
                rec["format_issues"].append("empty_warning")
        elif isinstance(e, dict):
            for k, v in e.items():
                if k in SUPPORTED_FIELDS and isinstance(v, str):
                    if v.strip():
                        rec[k] = v.strip()
                else:
                    rec["extra"][k] = copy.deepcopy(v)
                    rec["format_issues"].append(f"field_not_string:{k}" if k in SUPPORTED_FIELDS else f"unrecognized_field:{k}")
            if rec["text"] or rec["reason"]:
                rec["form"] = "object"
            else:
                rec["format_issues"].append("unrecognized_warning_format")
        else:
            rec["format_issues"].append(f"unsupported_type:{type(e).__name__}")
        rec["combined"] = " ".join(x for x in (rec["text"], rec["reason"], rec["source_text"]) if x) if rec["form"] != "malformed" else ""
        out.append(rec)
    return out


# ── the job description's own statements about an item ─────────────────────────────────────────────────────────────────────────
def _statements(jd: str) -> list[dict]:
    """Non-heading, non-AI-directed sentences with their importance class (own wording first, else the governing heading)."""
    masked = _mask_quotes(jd)
    ai = instruction_spans(jd)
    sents = [(s, e) for s, e, _ in _sentences(masked)]
    heads = [(s, e, jd[s:e].rstrip().endswith(":") or jd[s:e].rstrip().endswith("：")) for s, e in sents]
    out = []
    for idx, (s, e) in enumerate(sents):
        text = jd[s:e]
        if heads[idx][2]:
            continue
        if sum(max(0, min(e, x["end"]) - max(s, x["start"])) for x in ai) / max(1, e - s) >= 0.5:
            continue                                                       # text addressed to the AI is not a statement of the role
        cls = _wording(text)
        if cls is None:                                                    # fall back on the governing heading, if the list is contiguous
            for j in range(idx - 1, -1, -1):
                if re.search(r"\n\s*\n", jd[sents[j][1]:s]):
                    break
                if heads[j][2]:
                    h = _canon(jd[sents[j][0]:sents[j][1]])
                    cls = "preferred" if _HEAD_PREFERRED.search(h) else "required" if _HEAD_REQUIRED.search(h) else None
                    break
        out.append({"start": s, "end": e, "text": text, "class": cls, "tokens": _tokens(text)})
    return out


def _item_statements(item: dict, statements: list[dict]) -> list[dict]:
    it = _tokens(item["text"])
    return [x for x in statements if x["class"] and _mentions(it, x["tokens"])]


def _contradictory(stmts: list[dict]) -> bool:
    return {"required", "preferred"} <= {x["class"] for x in stmts}


# ── classification and linkage ───────────────────────────────────────────────────────────────────────────────────────────────
def _classify(rec: dict, jd: str, doc: dict, statements: list[dict]) -> dict:
    if rec["form"] == "malformed":
        return {"kind": KIND_MALFORMED, "linked": [], "limitation": "The warning is malformed or in an unrecognized format; it is preserved for inspection and not interpreted."}
    c = _canon(rec["combined"])
    imp, contra, dup, amb = bool(_OPTIONAL.search(c) or _REQUIRED.search(c)), bool(_CONTRADICTION.search(c)), bool(_DUPLICATE.search(c)), bool(_AMBIGUITY.search(c))
    if dup and not contra:
        return {"kind": KIND_DUPLICATE, "linked": [], "limitation": "A note about a repeated requirement; informational."}
    if not imp:
        kind = KIND_GENERIC if (contra or amb) else KIND_UNRELATED
        return {"kind": kind, "linked": [], "limitation": "No importance wording (required / optional / preferred ...): it cannot be tied to a Required/Preferred conflict."
                if kind == KIND_GENERIC else "Not about requirement importance; informational."}
    wt = _tokens(rec["combined"])
    span = locate_span(jd, rec["source_text"]) if rec["source_text"] else None
    mentioned, linked, uncorroborated = [], [], []
    for cat in CATEGORIES:
        for item in doc["categories"][cat]["items"]:
            it = _tokens(item["text"])
            ispan = locate_span(jd, item["source_text"]) if item.get("source_text") else None
            if not (_mentions(it, wt) or (span and ispan and min(span[1], ispan[1]) > max(span[0], ispan[0]))):
                continue
            mentioned.append(item["id"])
            stm = _item_statements(item, statements)
            if _contradictory(stm):
                linked.append({"item_id": item["id"], "category": cat, "statements": [{"class": x["class"], "text": x["text"].strip(), "span": [x["start"], x["end"]]} for x in stm]})
            else:
                uncorroborated.append(item["id"])
    if linked:
        return {"kind": KIND_CONFLICT, "linked": linked, "explicit_conflict_wording": contra, "limitation": None}
    if mentioned:
        return {"kind": KIND_UNCORROBORATED, "linked": [], "limitation": "The warning mentions an item, but the job description does not state that item with contradictory "
                "importance (an ordinary optional note, or an unverified model claim); not treated as a conflict."}
    return {"kind": KIND_NOT_IDENTIFIED, "linked": [], "limitation": "Importance wording was found but no extracted item could be identified from the warning; "
            "linkage is uncertain, so it stays informational."}


def _evidence_hash(evidence: dict) -> str:
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_review(jd_text: str, raw_ai_output: Any, doc: dict) -> dict:
    """The server-owned warning record for one extraction. Pure; `doc` and the raw output are only read."""
    statements = _statements(jd_text)
    records, by_item = [], {}
    for rec in normalize_warnings(raw_ai_output):
        cl = _classify(rec, jd_text, doc, statements)
        out = {k: v for k, v in rec.items() if k != "combined"}
        out.update(kind=cl["kind"], linked_item_ids=[l["item_id"] for l in cl["linked"]], limitation=cl["limitation"], blocking_candidate=bool(cl["linked"]),
                   informational=not cl["linked"])
        records.append(out)
        for l in cl["linked"]:
            by_item.setdefault(l["item_id"], {"category": l["category"], "statements": l["statements"], "model_warnings": []})["model_warnings"].append(
                {"index": rec["index"], "text": rec["text"], "reason": rec["reason"], "source_text": rec["source_text"]})
    items = []
    for item_id, info in by_item.items():
        category, item = find_item(doc, item_id)
        evidence = {"code": CODE_CONFLICT, "model_warnings": info["model_warnings"], "jd_statements": info["statements"]}
        items.append({"id": f"{item_id}:{CODE_CONFLICT}", "code": CODE_CONFLICT, "item_id": item_id, "category": category, "flagged_importance": item["importance"],
                      "status": STATUS_OPEN, "resolution": None, "evidence": evidence, "evidence_hash": _evidence_hash(evidence),
                      "message": "The model reported contradictory Required/Preferred wording for this item and the job description confirms it "
                                 f"({'; '.join(s['class'] + ': ' + s['text'] for s in info['statements'])}). Decide which reading is right."})
    raw = json.dumps(raw_ai_output, sort_keys=True, ensure_ascii=False, default=str)
    return {"adapter_version": ADAPTER_VERSION, "jd_sha256": hashlib.sha256(jd_text.encode("utf-8")).hexdigest(),
            "raw_ai_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "model_warnings": records, "item_warnings": items, "acknowledgments": []}


def visible_warnings(review: dict | None) -> list[dict]:
    """Every model warning, for display: kind, normalized fields, extras, format issues, linked items, limitation. Nothing is hidden."""
    return copy.deepcopy((review or {}).get("model_warnings", []))


# ── lifecycle (mirrors the frozen classification-acknowledgment lifecycle) ───────────────────────────────────────────────────
@dataclasses.dataclass(frozen=True)
class WarningStatus:
    open: tuple[str, ...] = ()            # active: the item exists with the flagged classification
    acknowledged: tuple[str, ...] = ()    # active with a valid acknowledgment
    unresolved: tuple[str, ...] = ()      # active without one (the policy may turn these into a blocker)
    inactive: tuple[str, ...] = ()        # the item was reclassified (the recruiter chose the other reading); reopens if reversed
    resolved: tuple[str, ...] = ()        # the item was removed (permanent: item ids are never reused)
    stale_acknowledgments: tuple[str, ...] = ()


def _ack_for(review: dict, wid: str) -> dict | None:
    return next((a for a in review["acknowledgments"] if a.get("warning_id") == wid), None)


def _ack_valid(ack: dict | None, w: dict, category: str, item: dict) -> bool:
    return (ack is not None and ack.get("item_id") == w["item_id"] and ack.get("item_state_hash") == item_state_hash(item_state(category, item))
            and ack.get("evidence_hash") == _evidence_hash(w["evidence"]) == w["evidence_hash"])


def status(review: dict | None, doc: dict) -> WarningStatus:
    if not review:
        return WarningStatus()
    o, a, u, i, r, s = [], [], [], [], [], []
    for w in review["item_warnings"]:
        found, ack = find_item(doc, w["item_id"]), _ack_for(review, w["id"])
        if w["status"] == STATUS_RESOLVED or found is None:
            r.append(w["id"])
            s += [w["id"]] if ack else []
        elif found[1]["importance"] != w["flagged_importance"]:
            i.append(w["id"])
            s += [w["id"]] if ack else []
        else:
            o.append(w["id"])
            if _ack_valid(ack, w, *found):
                a.append(w["id"])
            else:
                u.append(w["id"])
                s += [w["id"]] if ack else []
    return WarningStatus(tuple(o), tuple(a), tuple(u), tuple(i), tuple(r), tuple(s))


def acknowledge(review: dict, doc: dict, warning_id: str, *, user_id: str, acknowledged_at: str) -> dict:
    """A new review in which the recruiter accepted the flagged classification of ONE item. Trusted server code only (the caller authenticated the user
    and checked the role). The item is not modified."""
    if not user_id or not acknowledged_at or not isinstance(user_id, str) or not isinstance(acknowledged_at, str):
        raise AcknowledgmentError("user_id and acknowledged_at are required.")
    w = next((x for x in review["item_warnings"] if x["id"] == warning_id), None)
    if w is None:
        raise AcknowledgmentError(f"unknown conflict warning {warning_id!r}", "unknown_warning")
    st = status(review, doc)
    if warning_id in st.resolved or warning_id in st.inactive:
        raise AcknowledgmentError("This warning no longer applies: the item was reclassified or removed.", "no_longer_applies")
    if warning_id in st.acknowledged:
        raise AcknowledgmentError("This conflict is already acknowledged.", "already_acknowledged")
    category, item = find_item(doc, w["item_id"])
    state = item_state(category, item)
    out = copy.deepcopy(review)
    out["acknowledgments"] = [x for x in out["acknowledgments"] if x["warning_id"] != warning_id] + [{
        "warning_id": warning_id, "item_id": w["item_id"], "user_id": user_id, "acknowledged_at": acknowledged_at, "item_state": state,
        "item_state_hash": item_state_hash(state), "evidence_hash": w["evidence_hash"]}]
    return out


def reconcile(review: dict | None, doc: dict) -> tuple[dict | None, dict]:
    """(new review, {"resolved": [...], "invalidated": [(warning id, reason)]}) for the audit log. Call on EVERY save with the trusted stored review:
    a warning whose item was removed becomes resolved; an acknowledgment is pruned when it no longer matches the item or the evidence, and whenever
    its warning is inactive or resolved, so an old acceptance never comes back by itself (a reversal needs a fresh one)."""
    if not review:
        return review, {"resolved": [], "invalidated": []}
    new, resolved, invalidated = copy.deepcopy(review), [], []
    for w in new["item_warnings"]:
        if w["status"] == STATUS_OPEN and find_item(doc, w["item_id"]) is None:
            w["status"], w["resolution"] = STATUS_RESOLVED, RES_ITEM_REMOVED
            resolved.append((w["id"], RES_ITEM_REMOVED))
    kept, by_id = [], {w["id"]: w for w in new["item_warnings"]}
    for a in new["acknowledgments"]:
        w = by_id.get(a["warning_id"])
        found = find_item(doc, w["item_id"]) if w else None
        if w is None or w["status"] == STATUS_RESOLVED or found is None:
            invalidated.append((a["warning_id"], "warning_resolved"))
        elif found[1]["importance"] != w["flagged_importance"]:
            invalidated.append((a["warning_id"], "warning_inactive"))
        elif not _ack_valid(a, w, *found):
            invalidated.append((a["warning_id"], "item_or_evidence_changed"))
        else:
            kept.append(a)
    new["acknowledgments"] = kept
    return new, {"resolved": resolved, "invalidated": invalidated}


def carry_warning_review(stored: dict | None, incoming: object = None) -> dict | None:
    """Server-owned: whatever the client sent is discarded; only the trusted stored record survives."""
    return copy.deepcopy(stored) if stored else None


def validate_review(review: object) -> list[str]:
    if not isinstance(review, dict) or review.get("adapter_version") != ADAPTER_VERSION:
        return ["not a warning review record of this adapter version"]
    errs, ids = [], set()
    for r in review.get("model_warnings", []):
        if "raw" not in r or "kind" not in r:
            errs.append(f"warning {r.get('index')}: raw and kind are required")
        if r.get("kind") == KIND_MALFORMED and r.get("linked_item_ids"):
            errs.append(f"warning {r.get('index')}: a malformed warning cannot be linked")
        if r.get("blocking_candidate") and r.get("kind") != KIND_CONFLICT:
            errs.append(f"warning {r.get('index')}: only a confirmed importance conflict can be a blocking candidate")
    for w in review.get("item_warnings", []):
        if w.get("code") != CODE_CONFLICT or w.get("id") != f"{w.get('item_id')}:{CODE_CONFLICT}" or w.get("status") not in (STATUS_OPEN, STATUS_RESOLVED) or not w.get("evidence"):
            errs.append(f"bad item warning {w.get('id')}")
        elif w["id"] in ids:
            errs.append(f"duplicate item warning {w['id']}")
        ids.add(w.get("id"))
    for a in review.get("acknowledgments", []):
        if a.get("warning_id") not in ids or not a.get("user_id") or not a.get("acknowledged_at"):
            errs.append("an acknowledgment is malformed or refers to an unknown warning")
    return errs


# ── readiness composed with both candidate guards ─────────────────────────────────────────────────────────────────────────
def readiness(doc: dict, injection_review: dict | None, split_or_review: dict | None, warning_review: dict | None, *,
              require_classification_acknowledgment: bool = True, original: dict | None | str = "not_evaluated") -> Readiness:
    """Order: invalid document / injection / split-OR blockers (mandatory under either policy; no acknowledgment reaches them) -> the frozen
    classification review -> NEEDS_CONFLICT_REVIEW (policy Yes and an unresolved item-specific conflict) -> the frozen result. Under policy No a
    conflict is visible (status()/visible_warnings()) and never blocks."""
    base = composed_readiness(doc, injection_review, split_or_review, require_classification_acknowledgment=require_classification_acknowledgment, original=original)
    st = status(warning_review, doc)
    if not require_classification_acknowledgment or not st.unresolved:
        return base
    by_id = {w["id"]: w for w in warning_review["item_warnings"]}
    reasons = tuple(Issue("model_importance_conflict_unresolved", by_id[i]["message"], category=by_id[i]["category"], item_id=by_id[i]["item_id"]) for i in st.unresolved)
    if base.state in ("needs_injection_review", "needs_split_or_review", "needs_review", "needs_items", "needs_classification_review"):
        return dataclasses.replace(base, reasons=base.reasons + reasons) if base.state != "needs_review" else base
    return dataclasses.replace(base, state=NEEDS_CONFLICT_REVIEW, scoring_mode=None, reasons=reasons + tuple(base.reasons))
