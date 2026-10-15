"""Server-owned structural detection (pure, offline). It produces the structural_review block that structural_review.py validates.

Scope, deliberately narrow:
  * It looks only at the reviewed source units in REVIEWED_UNITS (server-owned expectations). Every other OR or AND is ignored.
  * alternatives_split_across_items: a reviewed alternatives unit quoted by two or more items, none of which holds every
    alternative, that together cover all of them. Incomplete or missing alternatives are not reported as a split.
  * independent_items_merged: a reviewed independent-items unit quoted by one item whose text holds every listed token.
  * Connected competencies (communication, coordination, teamwork, ...) are never structural evidence.
  * Conflicts and source-unit omissions are not detected here.

Only open findings are produced. detect() runs after draft validation and never reads or writes a resolution. attach() returns a
copy with the block, and the parser never calls this module.
"""
from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass

from services.requirements_v2.contract import CATEGORIES
from services.requirements_v2.structural_review import (
    CODE_ALTERNATIVES_SPLIT, CODE_INDEPENDENT_MERGED, READINESS_INVALID, STATUS_OPEN, STRUCTURAL_KEY, STRUCTURAL_SCHEMA_VERSION,
    structural_status,
)
from services.requirements_v2.validation import validate_draft

ALTERNATIVES = "alternatives"
INDEPENDENT = "independent"


@dataclass(frozen=True)
class ReviewedUnit:
    """A reviewed source unit, bound to one exact job description (by SHA-256) and to fixed offsets in it."""
    unit_id: str              # stable identity of the reviewed unit
    jd_sha256: str            # SHA-256 of the complete job description (UTF-8) the unit was reviewed in
    source_start: int         # offsets of source_text in that description
    source_end: int
    source_text: str          # exact wording of the reviewed source unit
    kind: str                 # ALTERNATIVES | INDEPENDENT
    tokens: tuple[str, ...]   # the alternatives, or the independent items, named in the unit


REVIEWED_UNITS: tuple[ReviewedUnit, ...] = (
    ReviewedUnit("B02_en_data_analyst:L6", "964ec821ef2c5e5c2abd76014934e31bc69b1d6391ceebb15e3986944a1a9df8",
                 142, 168, "Proficiency in Python or R", ALTERNATIVES, ("Python", "R")),
    ReviewedUnit("B12_ar_injection:L6", "945b2154c3f26ae216d53eb9dfdd25fce6f6e7f57fd0bc4778404c474d8323c2",
                 103, 121, "معرفة بـ CSS وHTML", INDEPENDENT, ("CSS", "HTML")),
)


def _has(text: str, token: str) -> bool:
    """Whole-token, case-insensitive. Only ASCII letters and digits extend a token, so 'وHTML' still matches 'HTML'."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def _finding_id(unit: ReviewedUnit, code: str, item_ids: list[str], start: int, end: int) -> str:
    """Unit identity, JD hash and offsets are part of the ID, so two distinct reviewed units never collide."""
    parts = [unit.unit_id, unit.jd_sha256, str(start), str(end), code, *item_ids]
    return f"sd-{hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:16]}"


def _finding(code: str, unit: ReviewedUnit, item_ids: list[str], start: int, end: int, message: str) -> dict:
    return {"id": _finding_id(unit, code, item_ids, start, end), "code": code, "item_ids": item_ids, "source_text": unit.source_text,
            "source_start": start, "source_end": end, "message": message, "status": STATUS_OPEN, "resolution": None}


def _alternatives_findings(unit: ReviewedUnit, quoting: list[dict], start: int, end: int) -> list[dict]:
    if len(quoting) < 2:
        return []                                                       # one item: together, incomplete or missing
    if any(all(_has(i["text"], t) for t in unit.tokens) for i in quoting):
        return []                                                       # one item holds every alternative: correct
    covered = {t for i in quoting for t in unit.tokens if _has(i["text"], t)}
    if covered != set(unit.tokens):
        return []                                                       # some alternative is missing: not a split
    affected = [i["id"] for i in quoting if any(_has(i["text"], t) for t in unit.tokens)]
    joined = " and ".join(unit.tokens)
    message = (f"{joined} are returned as separate requirements. They are one requirement with alternatives, and the "
               "correct structure is one item holding both. Confirm the structure.")
    return [_finding(CODE_ALTERNATIVES_SPLIT, unit, affected, start, end, message)]


def _independent_findings(unit: ReviewedUnit, quoting: list[dict], start: int, end: int) -> list[dict]:
    out = []
    for i in quoting:
        if all(_has(i["text"], t) for t in unit.tokens):
            message = (f"{' and '.join(unit.tokens)} are returned as one requirement. They are independent requirements "
                       "and need separate items. Confirm the structure.")
            out.append(_finding(CODE_INDEPENDENT_MERGED, unit, [i["id"]], start, end, message))
    return out


def detect(doc: dict, jd_text: str) -> dict:
    """The structural_review block for a valid draft: {'schema_version': 1, 'findings': [...]}, with open findings only.
    Raises ValueError when the document is not a valid draft, including when it already carries a structural block."""
    if validate_draft(doc).errors:
        raise ValueError("structural detection runs only on a valid draft")
    jd_hash = hashlib.sha256(jd_text.encode("utf-8")).hexdigest()
    findings: list[dict] = []
    seen: set[str] = set()
    for unit in REVIEWED_UNITS:
        if unit.jd_sha256 != jd_hash:                  # a different description: the unit does not apply
            continue
        start, end = unit.source_start, unit.source_end
        if jd_text[start:end] != unit.source_text:     # the offsets must hold the reviewed wording
            continue
        quoting = [item for c in CATEGORIES for item in doc["categories"][c]["items"]
                   if item.get("source_text") == unit.source_text]
        if unit.kind == ALTERNATIVES:
            found = _alternatives_findings(unit, quoting, start, end)
        else:
            found = _independent_findings(unit, quoting, start, end)
        for f in found:
            if f["id"] not in seen:
                seen.add(f["id"])
                findings.append(f)
    return {"schema_version": STRUCTURAL_SCHEMA_VERSION, "findings": findings}


def attach(doc: dict, jd_text: str) -> dict:
    """A copy of the draft with the server-owned block, when there are findings. The result must pass the structural contract;
    if it does not, that is a bug in this module, so it fails loudly rather than returning an invalid block."""
    block = detect(doc, jd_text)
    out = copy.deepcopy(doc)
    if block["findings"]:
        out[STRUCTURAL_KEY] = block
        invalid = [i for i in structural_status(out, jd_text).issues if i.code == READINESS_INVALID]
        if invalid:
            raise RuntimeError(f"detector produced a block the contract rejects: {invalid[0].message}")
    return out
