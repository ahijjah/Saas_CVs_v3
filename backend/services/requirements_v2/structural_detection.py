"""Structural detection driven by an explicit, validated expectation block (pure, offline). It produces the server-owned
structural_review block (open findings only) for the contract in structural_review.py.

  * alternatives_together: the expectation's alternatives must sit in one item. Two or more items quoting the same wording, none
    holding every alternative, that together cover them all, produce one open alternatives_split_across_items finding.
    Incomplete or missing alternatives produce nothing.
  * independent_items: the expectation's items must be separate. An item quoting the wording that holds every listed token
    produces one open independent_items_merged finding.

Nothing is inferred from AND/OR wording. No expectation means no finding. The expectation block is validated against the
description first (see structural_expectations.py), and it is never stored or read from the parser. Only open findings are
produced, and resolution is never read or written here.
"""
from __future__ import annotations

import copy
import hashlib
import json

from services.requirements_v2.contract import CATEGORIES
from services.requirements_v2.structural_expectations import (
    ALTERNATIVES_TOGETHER, INDEPENDENT_ITEMS, Expectation, has_token, jd_sha256, validate_expectations,
)
from services.requirements_v2.structural_review import (
    CODE_ALTERNATIVES_SPLIT, CODE_INDEPENDENT_MERGED, READINESS_INVALID, STATUS_OPEN, STRUCTURAL_KEY, STRUCTURAL_SCHEMA_VERSION,
    structural_status,
)
from services.requirements_v2.validation import validate_draft


def _finding_id(expectation: Expectation, code: str, item_ids: list[str], start: int, end: int, jd_hash: str) -> str:
    """Expectation identity, JD hash, offsets, code and affected item IDs. JSON encoding keeps the fields unambiguous."""
    payload = json.dumps([expectation.id, jd_hash, start, end, code, item_ids], ensure_ascii=False, separators=(",", ":"))
    return f"sd-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _finding(code: str, exp: Expectation, item_ids: list[str], jd_hash: str, message: str) -> dict:
    start, end = exp.source_start, exp.source_end
    return {"id": _finding_id(exp, code, item_ids, start, end, jd_hash), "code": code, "item_ids": item_ids,
            "source_text": exp.source_text, "source_start": start, "source_end": end, "message": message,
            "status": STATUS_OPEN, "resolution": None}


def _alternatives_findings(exp: Expectation, quoting: list[dict], jd_hash: str) -> list[dict]:
    if len(quoting) < 2:
        return []                                                       # one item: together, incomplete or missing
    if any(all(has_token(i["text"], t) for t in exp.tokens) for i in quoting):
        return []                                                       # one item holds every alternative: correct
    covered = {t for i in quoting for t in exp.tokens if has_token(i["text"], t)}
    if covered != set(exp.tokens):
        return []                                                       # an alternative is missing: not a split
    affected = [i["id"] for i in quoting if any(has_token(i["text"], t) for t in exp.tokens)]
    message = (f"{' and '.join(exp.tokens)} are returned as separate requirements. They are one requirement with alternatives, "
               "and the expected structure is one item holding both. Confirm the structure.")
    return [_finding(CODE_ALTERNATIVES_SPLIT, exp, affected, jd_hash, message)]


def _independent_findings(exp: Expectation, quoting: list[dict], jd_hash: str) -> list[dict]:
    out = []
    for i in quoting:
        if all(has_token(i["text"], t) for t in exp.tokens):
            message = (f"{' and '.join(exp.tokens)} are returned as one requirement. They are independent requirements and "
                       "need separate items. Confirm the structure.")
            out.append(_finding(CODE_INDEPENDENT_MERGED, exp, [i["id"]], jd_hash, message))
    return out


def detect(doc: dict, jd_text: str, expectation_block: dict | None = None) -> dict:
    """The structural_review block for a valid draft, from an explicitly supplied expectation block: {'schema_version': 1,
    'findings': [...]}, open findings only. None or an empty expectation list gives no findings. An invalid block, or one bound
    to another description, raises ExpectationError (a ValueError). A document that is not a valid draft raises ValueError."""
    if validate_draft(doc).errors:
        raise ValueError("structural detection runs only on a valid draft")
    expectations = () if expectation_block is None else validate_expectations(expectation_block, jd_text)
    jd_hash = jd_sha256(jd_text)
    findings: list[dict] = []
    seen: set[str] = set()
    for exp in expectations:
        quoting = [item for c in CATEGORIES for item in doc["categories"][c]["items"]
                   if item.get("source_text") == exp.source_text]
        if exp.kind == ALTERNATIVES_TOGETHER:
            found = _alternatives_findings(exp, quoting, jd_hash)
        elif exp.kind == INDEPENDENT_ITEMS:
            found = _independent_findings(exp, quoting, jd_hash)
        else:                                          # unreachable: validate_expectations admits only KINDS
            found = []
        for f in found:
            if f["id"] not in seen:
                seen.add(f["id"])
                findings.append(f)
    return {"schema_version": STRUCTURAL_SCHEMA_VERSION, "findings": findings}


def attach(doc: dict, jd_text: str, expectation_block: dict | None = None) -> dict:
    """A copy of the draft with the server-owned block, when there are findings. The result must pass the structural contract;
    if it does not, that is a bug in this module, so it fails loudly rather than returning an invalid block."""
    block = detect(doc, jd_text, expectation_block)
    out = copy.deepcopy(doc)
    if block["findings"]:
        out[STRUCTURAL_KEY] = block
        invalid = [i for i in structural_status(out, jd_text).issues if i.code == READINESS_INVALID]
        if invalid:
            raise RuntimeError(f"detector produced a block the contract rejects: {invalid[0].message}")
    return out
