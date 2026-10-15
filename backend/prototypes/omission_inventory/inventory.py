"""Source-unit inventory for one job description. Offline prototype: not imported by production code.

The JD text is never modified. A unit is a logical line of the ORIGINAL text: one physical line, or several physical lines joined when a
line starts with a lowercase Latin letter and the line before it does not end in terminal punctuation (a soft wrap). Arabic has no case,
so Arabic lines are never joined.

Headings and background are not recognised by length or punctuation. They are the lines listed exactly in a reviewed annotation file
whose jd_sha256 matches this JD. Every other non-blank line is a requirement candidate. A stale annotation (a listed line that is not
in the JD) is an error, not a silent miss.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

TERMINAL = (".", ";", ":", "!", "?", "…")


@dataclass(frozen=True)
class Unit:
    unit_id: str            # "<sha12>:L<first line>:<start>-<end>", offsets into the original JD text
    kind: str               # "requirement" | "heading" | "background"
    text: str               # display copy of the unit (physical lines joined by a newline); matching never uses it
    spans: tuple            # ((start, end), ...), one per physical line, end exclusive, newline and carriage return excluded
    first_line: int         # 1-based


@dataclass(frozen=True)
class Inventory:
    jd_sha256: str
    jd_length: int
    units: tuple
    diagnostics: dict

    def requirements(self):
        return [u for u in self.units if u.kind == "requirement"]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _physical_lines(jd: str):
    """(line number, start, end, text) per physical line. Offsets index the original string; \\r is excluded from the span."""
    out, pos = [], 0
    for n, raw in enumerate(jd.split("\n"), start=1):
        body = raw[:-1] if raw.endswith("\r") else raw
        out.append((n, pos, pos + len(body), body))
        pos += len(raw) + 1
    return out


def _is_soft_wrap(prev_text: str, cur_text: str) -> bool:
    cur = cur_text.lstrip()
    if not cur or not ("a" <= cur[0] <= "z"):
        return False
    return not prev_text.rstrip().endswith(TERMINAL)


def build_inventory(jd: str, annotation: dict | None = None) -> Inventory:
    sha = sha256_text(jd)
    annotation = annotation or {}
    if annotation and annotation.get("jd_sha256") != sha:
        raise ValueError("annotation belongs to a different JD text (jd_sha256 mismatch)")
    headings = {h.strip() for h in annotation.get("headings", [])}
    background = {b.strip() for b in annotation.get("background", [])}
    if headings & background:
        raise ValueError(f"a line is listed as both heading and background: {sorted(headings & background)}")

    physical = _physical_lines(jd)
    lines = [ln for ln in physical if ln[3].strip()]
    present = {ln[3].strip() for ln in lines}
    stale = sorted((headings | background) - present)
    if stale:
        raise ValueError(f"annotation lists lines that are not in the JD: {stale}")

    def kind_of(text: str) -> str:
        s = text.strip()
        if s in headings:
            return "heading"
        if s in background:
            return "background"
        return "requirement"

    groups: list[list] = []
    merged = []
    prev_no = None                  # line number of the previous PHYSICAL line; a blank line breaks any soft wrap
    for ln in lines:
        kind = kind_of(ln[3])
        adjacent = prev_no == ln[0] - 1
        if (groups and adjacent and kind == "requirement" and groups[-1][0][4] == "requirement"
                and _is_soft_wrap(groups[-1][-1][3], ln[3])):
            groups[-1].append(ln)
            merged.append(ln[0])
        else:
            groups.append([(*ln, kind)])
        prev_no = ln[0]

    units = []
    for g in groups:
        first = g[0][0]
        start, end = g[0][1], g[-1][2]
        kind = g[0][4]
        spans = tuple((ln[1], ln[2]) for ln in g)
        text = "\n".join(ln[3] for ln in g)
        units.append(Unit(unit_id=f"{sha[:12]}:L{first}:{start}-{end}", kind=kind, text=text, spans=spans, first_line=first))

    diagnostics = {
        "physical_lines": len(physical),
        "blank_lines_skipped": len(physical) - len(lines),
        "units": len(units),
        "requirement_units": sum(1 for u in units if u.kind == "requirement"),
        "heading_units": sum(1 for u in units if u.kind == "heading"),
        "background_units": sum(1 for u in units if u.kind == "background"),
        "soft_wrap_merged_lines": merged,
        "crlf_lines": jd.count("\r\n"),
    }
    return Inventory(jd_sha256=sha, jd_length=len(jd), units=tuple(units), diagnostics=diagnostics)
