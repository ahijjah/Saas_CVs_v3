"""
S2 — deterministic validation of the classifier response (S2-V0 .. S2-V9).

Never repairs, re-labels or guesses: it accepts the model's claims or returns
rule violations (prefixed with the rule ID; fed verbatim into the single repair
call). Accepted quotes are mapped to their exact ORIGINAL CV span; no
model-generated text is ever used as recruiter-facing evidence.

  S2-V0  JSON object with a ``results`` list of objects; field types correct.
  S2-V1  exactly one result per supplied entry: no missing/duplicate/unknown IDs.
  S2-V2  label / basis / missing values from the exact enums (case-sensitive).
  S2-V3  reason non-empty after trimming, <= 500 characters.
  S2-V4  quotes >= 1 for qualifying/related/not_relevant; insufficient needs
         >= 1 ``missing`` value; ``missing`` only with insufficient.
  S2-V5  each quote's line is one of THAT entry's supplied lines.
  S2-V6  quote text is a verbatim (normalised) substring of that masked line,
         with >= 3 non-space characters.
  S2-V7  quote must not contain or overlap the [dates] mask.
  S2-V8  basis=title -> >= 1 quote on the title line inside the title text;
         basis=context only for sector, or functional with a setting.
  S2-V9  any other field is ignored and never trusted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from services.s2_experience.masking import MASK_TOKEN, MaskedLine, locate_quote, normalize

LABELS = ("qualifying", "related", "not_relevant", "insufficient")
BASES = ("title", "responsibilities", "title_and_responsibilities", "context")
MISSING = ("responsibilities", "function", "setting", "employer_context", "role_level")
QUOTE_REQUIRED = ("qualifying", "related", "not_relevant")
MAX_REASON_CHARS = 500
MIN_QUOTE_CHARS = 3


@dataclass(frozen=True)
class EntryView:
    """What S2 sent for one entry (masked), plus what validation needs."""
    entry_id: str
    lines: dict[int, MaskedLine]          # 1-based line -> masked/original line
    title_line: int | None
    title_masked: str | None


@dataclass
class ValidatedResult:
    entry_id: str
    label: str
    basis: str
    reason: str
    missing: list[str]
    quotes: list[dict]                    # line, char_start, char_end, original_text, model_text, occurrence


@dataclass
class S2Validation:
    errors: list[str] = field(default_factory=list)
    results: list[ValidatedResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_response(raw: str | dict, entries: list[EntryView], *, policy: str,
                      has_setting: bool) -> S2Validation:
    out = S2Validation()
    err = out.errors
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            err.append(f"S2-V0 response is not valid JSON ({exc})")
            return out
    else:
        data = raw
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        err.append("S2-V0 response must be a JSON object with a 'results' list")
        return out

    by_id = {e.entry_id: e for e in entries}
    seen: dict[str, int] = {}
    for i, r in enumerate(data["results"]):
        w = f"results[{i}]"
        if not isinstance(r, dict):
            err.append(f"S2-V0 {w} must be an object")
            continue
        eid = r.get("entry_id")
        if not isinstance(eid, str):
            err.append(f"S2-V0 {w}: entry_id must be a string")
            continue
        seen[eid] = seen.get(eid, 0) + 1
        if eid not in by_id:
            err.append(f"S2-V1 {w}: unknown entry_id {eid!r}")
            continue
        if seen[eid] > 1:
            err.append(f"S2-V1 entry {eid} has more than one result")
            continue
        ent = by_id[eid]
        w = f"results[{i}] ({eid})"
        label, basis, reason = r.get("label"), r.get("basis"), r.get("reason")
        missing_raw, quotes_raw = r.get("missing", []), r.get("quotes", [])
        ok = True
        if label not in LABELS:
            err.append(f"S2-V2 {w}: label {label!r} not in {list(LABELS)}")
            ok = False
        if basis not in BASES:
            err.append(f"S2-V2 {w}: basis {basis!r} not in {list(BASES)}")
            ok = False
        if not isinstance(missing_raw, list) or not all(isinstance(m, str) for m in missing_raw):
            err.append(f"S2-V0 {w}: missing must be a list of strings")
            missing_raw, ok = [], False
        bad_missing = [m for m in missing_raw if m not in MISSING]
        if bad_missing:
            err.append(f"S2-V2 {w}: missing values {bad_missing} not in {list(MISSING)}")
            ok = False
        if not isinstance(reason, str) or not reason.strip():
            err.append(f"S2-V3 {w}: reason must be non-empty")
            ok = False
        elif len(reason.strip()) > MAX_REASON_CHARS:
            err.append(f"S2-V3 {w}: reason longer than {MAX_REASON_CHARS} characters")
            ok = False
        if not isinstance(quotes_raw, list):
            err.append(f"S2-V0 {w}: quotes must be a list")
            quotes_raw, ok = [], False
        if label in QUOTE_REQUIRED and not quotes_raw:
            err.append(f"S2-V4 {w}: label {label} requires at least one verbatim quote")
            ok = False
        if label == "insufficient" and not missing_raw:
            err.append(f"S2-V4 {w}: insufficient requires at least one 'missing' value")
            ok = False
        if label in QUOTE_REQUIRED and missing_raw:
            err.append(f"S2-V4 {w}: 'missing' is only allowed with insufficient")
            ok = False

        quotes: list[dict] = []
        for k, q in enumerate(quotes_raw):
            qw = f"{w}.quotes[{k}]"
            if not isinstance(q, dict) or not isinstance(q.get("line"), int) \
                    or isinstance(q.get("line"), bool) or not isinstance(q.get("text"), str):
                err.append(f"S2-V0 {qw}: quote must be {{line: int, text: str}}")
                ok = False
                continue
            ln, text = q["line"], q["text"]
            if ln not in ent.lines:
                err.append(f"S2-V5 {qw}: line {ln} is not one of entry {eid}'s lines "
                           f"{sorted(ent.lines)}")
                ok = False
                continue
            if MASK_TOKEN in text or "[dates" in text or "dates]" in text:
                err.append(f"S2-V7 {qw}: quotes must not contain the {MASK_TOKEN} placeholder")
                ok = False
                continue
            if len(normalize(text).replace(" ", "")) < MIN_QUOTE_CHARS:
                err.append(f"S2-V6 {qw}: quote is too short")
                ok = False
                continue
            span, why = locate_quote(text, ent.lines[ln])
            if span is None:
                if why == "overlaps_mask":
                    err.append(f"S2-V7 {qw}: quote overlaps the {MASK_TOKEN} placeholder on line {ln}")
                else:
                    err.append(f"S2-V6 {qw}: {text!r} is not verbatim on line {ln} "
                               f"({ent.lines[ln].masked[:80]!r})")
                ok = False
                continue
            quotes.append({"line": ln, "char_start": span.char_start, "char_end": span.char_end,
                           "original_text": span.original_text, "model_text": text,
                           "occurrence": span.occurrence})

        if ok and basis == "title":
            on_title = [q for q in quotes if q["line"] == ent.title_line and ent.title_masked
                        and normalize(q["model_text"]) in normalize(ent.title_masked)]
            if not on_title:
                err.append(f"S2-V8 {w}: basis=title requires a quote of the title on its title line")
                ok = False
        if ok and basis == "context" and not (policy == "sector" or (policy == "functional" and has_setting)):
            err.append(f"S2-V8 {w}: basis=context is only allowed for sector criteria or "
                       f"functional criteria with a setting")
            ok = False
        if ok:
            out.results.append(ValidatedResult(eid, label, basis, reason.strip(),
                                               list(missing_raw), quotes))
    for e in entries:
        if e.entry_id not in seen:
            err.append(f"S2-V1 entry {e.entry_id} has no result")
    if err:
        out.results = []                  # all-or-nothing
    else:
        order = {e.entry_id: k for k, e in enumerate(entries)}
        out.results.sort(key=lambda r: order[r.entry_id])
    return out

