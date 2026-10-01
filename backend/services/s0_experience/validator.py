"""
S0 v2 — deterministic validation of the S0c structurer response (S0d).

The validator never repairs, re-assigns or guesses. It accepts the structurer's
claims or returns the list of rule violations (each prefixed with its rule ID,
fed verbatim into the single repair call).

Rules
  V0  response is a JSON object with list fields ``entries`` and ``ignored_anchors``;
      every field has the documented type.
  V1  every anchor ID is accounted for EXACTLY ONCE — assigned to one entry or
      listed once in ignored_anchors; unknown IDs are rejected.
  V2  an entry without an anchor must give a non-empty ``undated_reason``.
  V3  kind / disposition / ownership come from the allowed sets.
  V4  line numbers are integers within the document; title/employer lines are
      non-blank header lines and their text is a verbatim substring of that line.
  V5  body ranges are [a, b] with a <= b inside the document; every entry owns at
      least one line.
  V6  a line is claimed by at most one entry — except a SHARED EMPLOYER HEADER:
      the same line may be several entries' employer line when all of those
      entries come after it and no other entry's lines lie between it and them.
  V7  an entry's anchor lies inside or within 3 lines of the lines it claims, and
      an anchor line claimed by any entry is claimed by that anchor's entry.
  V8  no other entry's anchor inside an entry's body; an ignored
      ``inside_responsibility`` anchor must lie inside its owner entry's lines.
  V9  an anchor ignored as education/training/certification must not lie inside
      an experience-kind entry's lines.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from services.s0_experience.dates import Anchor
from services.s0_experience.schema import (
    DISPOSITIONS, ENTRY_KINDS, EXPERIENCE_KINDS, OWNERSHIP_CERTAIN, OWNERSHIP_UNCERTAIN,
)
from services.s0_experience.text import norm

ANCHOR_PROXIMITY_LINES = 3


@dataclass
class ParsedEntry:
    index: int
    anchor_id: str | None
    kind: str
    title_line: int | None
    title_text: str | None
    employer_line: int | None
    employer_text: str | None
    header_lines: list[int]
    body_lines: list[tuple[int, int]]
    ownership: str
    undated_reason: str | None

    def lines(self) -> set[int]:
        out = set(self.header_lines)
        for a, b in self.body_lines:
            out.update(range(a, b + 1))
        return out

    def body_set(self) -> set[int]:
        out: set[int] = set()
        for a, b in self.body_lines:
            out.update(range(a, b + 1))
        return out


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    entries: list[ParsedEntry] = field(default_factory=list)
    ignored: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_structure(raw: str | dict, lines: list[str], anchors: list[Anchor]) -> ValidationResult:
    res = ValidationResult()
    err = res.errors
    n = len(lines)

    # ── V0 shape ────────────────────────────────────────────────────────────
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            err.append(f"V0 response is not valid JSON ({exc})")
            return res
    else:
        data = raw
    if not isinstance(data, dict):
        err.append("V0 response must be a JSON object")
        return res
    ents, ign = data.get("entries"), data.get("ignored_anchors")
    if not isinstance(ents, list):
        err.append("V0 'entries' must be a list")
        return res
    if not isinstance(ign, list):
        err.append("V0 'ignored_anchors' must be a list")
        return res

    anchor_by_id = {a.anchor_id: a for a in anchors}

    def _line_ok(v: Any, where: str) -> bool:
        if not _is_int(v) or not (1 <= v <= n):
            err.append(f"V4 {where}: line {v!r} is not a line number between 1 and {n}")
            return False
        return True

    for i, e in enumerate(ents):
        w = f"entries[{i}]"
        if not isinstance(e, dict):
            err.append(f"V0 {w} must be an object")
            continue
        kind, own = e.get("kind"), e.get("ownership", OWNERSHIP_CERTAIN)
        if kind not in ENTRY_KINDS:
            err.append(f"V3 {w}: kind {kind!r} not in {list(ENTRY_KINDS)}")
        if own not in (OWNERSHIP_CERTAIN, OWNERSHIP_UNCERTAIN):
            err.append(f"V3 {w}: ownership {own!r} must be 'certain' or 'uncertain'")
        aid = e.get("anchor_id")
        if aid is not None and not isinstance(aid, str):
            err.append(f"V0 {w}: anchor_id must be a string or null")
            aid = None
        reason = e.get("undated_reason")
        if aid is None and not (isinstance(reason, str) and reason.strip()):
            err.append(f"V2 {w}: entry without anchor_id needs a non-empty undated_reason")
        hdr = e.get("header_lines", [])
        if not isinstance(hdr, list) or not all(_is_int(x) for x in hdr):
            err.append(f"V0 {w}: header_lines must be a list of line numbers")
            hdr = []
        hdr = [x for x in hdr if _line_ok(x, f"{w}.header_lines")]
        body_raw = e.get("body_lines", [])
        body: list[tuple[int, int]] = []
        if not isinstance(body_raw, list):
            err.append(f"V0 {w}: body_lines must be a list of [first, last] ranges")
        else:
            for r in body_raw:
                if (not isinstance(r, list) or len(r) != 2 or not all(_is_int(x) for x in r)):
                    err.append(f"V5 {w}: body range {r!r} must be [first, last]")
                    continue
                a, b = r
                if not (1 <= a <= b <= n):
                    err.append(f"V5 {w}: body range {r!r} must satisfy 1 <= first <= last <= {n}")
                    continue
                body.append((a, b))
        pe = ParsedEntry(i, aid, kind, None, None, None, None, hdr, body, own,
                         reason.strip() if isinstance(reason, str) else None)
        for fld in ("title", "employer"):
            ln, tx = e.get(f"{fld}_line"), e.get(f"{fld}_text")
            if ln is None and tx in (None, ""):
                continue
            if not _line_ok(ln, f"{w}.{fld}_line"):
                continue
            if not isinstance(tx, str) or not tx.strip():
                err.append(f"V4 {w}: {fld}_text must be the verbatim {fld} text on line {ln}")
                continue
            if ln not in hdr:
                err.append(f"V4 {w}: {fld}_line {ln} must be one of the entry's header_lines")
            if not lines[ln - 1].strip() or norm(tx) not in norm(lines[ln - 1]):
                err.append(f"V4 {w}: {fld}_text {tx!r} is not verbatim on line {ln} "
                           f"({lines[ln - 1][:80]!r})")
            setattr(pe, f"{fld}_line", ln)
            setattr(pe, f"{fld}_text", tx)
        if not pe.lines():
            err.append(f"V5 {w}: entry claims no lines")
        res.entries.append(pe)

    for j, g in enumerate(ign):
        w = f"ignored_anchors[{j}]"
        if not isinstance(g, dict) or not isinstance(g.get("anchor_id"), str):
            err.append(f"V0 {w} must be an object with an anchor_id")
            continue
        if g.get("disposition") not in DISPOSITIONS:
            err.append(f"V3 {w}: disposition {g.get('disposition')!r} not in {list(DISPOSITIONS)}")
        owner = g.get("owner_entry")
        if g.get("disposition") == "inside_responsibility":
            if not _is_int(owner) or not (0 <= owner < len(ents)):
                err.append(f"V8 {w}: inside_responsibility needs owner_entry = index of the owning entry")
                owner = None
        res.ignored.append({"anchor_id": g["anchor_id"], "disposition": g.get("disposition"),
                            "owner_entry": owner if _is_int(owner) else None})

    # ── V1 every anchor exactly once ────────────────────────────────────────
    seen: dict[str, list[str]] = {}
    for pe in res.entries:
        if pe.anchor_id is not None:
            seen.setdefault(pe.anchor_id, []).append(f"entries[{pe.index}]")
    for j, g in enumerate(res.ignored):
        seen.setdefault(g["anchor_id"], []).append(f"ignored_anchors[{j}]")
    for aid, where in seen.items():
        if aid not in anchor_by_id:
            err.append(f"V1 unknown anchor_id {aid!r} in {', '.join(where)}")
        elif len(where) > 1:
            err.append(f"V1 anchor {aid} accounted for more than once ({', '.join(where)})")
    for a in anchors:
        if a.anchor_id not in seen:
            err.append(f"V1 anchor {a.anchor_id} ({a.text!r}, line {a.line}) is not accounted for")

    # ── V6 exclusive ownership (shared employer header excepted) ────────────
    claims: dict[int, list[ParsedEntry]] = {}
    for pe in res.entries:
        for ln in pe.lines():
            claims.setdefault(ln, []).append(pe)
    for ln, pes in sorted(claims.items()):
        if len(pes) < 2:
            continue
        shared_ok = all(pe.employer_line == ln and ln in pe.header_lines and ln not in pe.body_set()
                        for pe in pes)
        if shared_ok:
            group = {pe.index for pe in pes}
            last_start = max(min(pe.lines() - {ln}, default=ln) for pe in pes)
            between = [o for o in res.entries if o.index not in group
                       and any(ln < x < last_start for x in o.lines())]
            if all(min(pe.lines() - {ln}, default=ln) > ln for pe in pes) and not between:
                continue
        err.append(f"V6 line {ln} is claimed by several entries "
                   f"({', '.join(f'entries[{pe.index}]' for pe in pes)}); a line may belong to "
                   f"only one entry unless it is a shared employer header line placed directly "
                   f"above those entries")

    # ── V7 anchor proximity / anchor line ownership ─────────────────────────
    for pe in res.entries:
        a = anchor_by_id.get(pe.anchor_id) if pe.anchor_id else None
        if a is None or not pe.lines():
            continue
        lo, hi = min(pe.lines()), max(pe.lines())
        if a.line < lo - ANCHOR_PROXIMITY_LINES or a.end_line > hi + ANCHOR_PROXIMITY_LINES:
            err.append(f"V7 entries[{pe.index}]: anchor {a.anchor_id} (line {a.line}) is more than "
                       f"{ANCHOR_PROXIMITY_LINES} lines from the entry's lines {lo}-{hi}")
        for ln in {a.line, a.end_line}:
            for o in claims.get(ln, []):
                if o.index != pe.index:
                    err.append(f"V7 anchor {a.anchor_id} line {ln} is claimed by entries[{o.index}], "
                               f"not by its own entry entries[{pe.index}]")

    # ── V8 / V9 anchors inside bodies ───────────────────────────────────────
    entry_of_anchor = {pe.anchor_id: pe for pe in res.entries if pe.anchor_id}
    ign_by_anchor = {g["anchor_id"]: g for g in res.ignored}
    for pe in res.entries:
        body = pe.body_set()
        for a in anchors:
            if a.line not in body:
                continue
            other = entry_of_anchor.get(a.anchor_id)
            if other is not None and other.index != pe.index:
                err.append(f"V8 entries[{pe.index}] body contains anchor {a.anchor_id} (line {a.line}) "
                           f"assigned to entries[{other.index}] — two jobs merged?")
            g = ign_by_anchor.get(a.anchor_id)
            if g is not None and g["disposition"] in ("education", "training", "certification") \
                    and pe.kind in EXPERIENCE_KINDS:
                err.append(f"V9 anchor {a.anchor_id} ignored as {g['disposition']} lies inside "
                           f"{pe.kind} entries[{pe.index}]")
    for g in res.ignored:
        if g["disposition"] != "inside_responsibility" or g["owner_entry"] is None:
            continue
        a = anchor_by_id.get(g["anchor_id"])
        owner = next((pe for pe in res.entries if pe.index == g["owner_entry"]), None)
        if a is not None and owner is not None and a.line not in owner.lines():
            err.append(f"V8 anchor {a.anchor_id} (line {a.line}) ignored as inside_responsibility "
                       f"is not inside the lines of its owner entries[{owner.index}]")
    return res
