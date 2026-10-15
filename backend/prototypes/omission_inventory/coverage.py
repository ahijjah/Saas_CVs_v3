"""Coverage of requirement units by quoted spans, computed on the ORIGINAL JD text. Offline prototype.

Rules (fixed before any replay was run):
  * A quote earns credit only if it is an exact substring of the original JD (the frozen verbatim check, unchanged).
  * A quote that occurs more than once in the JD is ambiguous: it earns no credit, and any requirement unit that it overlaps and that
    is otherwise uncovered is routed to review, not counted as an omission. Occurrence is never assumed.
  * A unit's covered share is the union of credited quote spans inside it, over its non-whitespace characters. Two overlapping quotes
    count once; a quote covering part of a unit covers only that part.
  * A unit is "covered" at COVER_THRESHOLD or more, "partial" above zero and below it, and "omitted" at zero (unless an ambiguous quote
    touches it, which makes it "ambiguous"). A short quote therefore cannot cover a long unit.
  * Whitespace-variant quotes (for example a quote joined across a line break with a space) are NOT credited. They are reported as a
    diagnostic only, so no normalised offsets are used for any decision.
What this measures is QUOTATION coverage only. "covered" means the credited quotes account for at least COVER_THRESHOLD of the unit's
non-space characters. It does NOT mean complete, and it does not mean that the answer's meaning matches the unit. A unit can be
"covered" while a short uncovered clause changes its requirement (for example "unless exempted in writing"). So every unit that has
any uncovered text is listed with its exact uncovered spans and flagged clause_review_required. Semantic omission is not decided here.
OR/AND and routing correctness are not coverage; they are reported separately (see replay.py).
"""
from __future__ import annotations

import unicodedata

COVER_THRESHOLD = 0.9


def occurrences(jd: str, quote: str) -> list[int]:
    out, i = [], jd.find(quote)
    while i != -1:
        out.append(i)
        i = jd.find(quote, i + 1)
    return out


def _collapsed(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def judge_quotes(jd: str, quotes: list) -> list[dict]:
    """One verdict per quote, in order: unique | ambiguous | not_verbatim | invalid. Credit is given only to unique quotes."""
    counts: dict[str, int] = {}
    for q in quotes:
        if isinstance(q, str):
            counts[q] = counts.get(q, 0) + 1
    verdicts = []
    for k, q in enumerate(quotes):
        if not isinstance(q, str) or not q.strip():
            verdicts.append({"index": k, "quote": q, "status": "invalid", "occurrences": [], "whitespace_variant": False, "repeated_in_answer": False})
            continue
        occ = occurrences(jd, q)
        if not occ:
            verdicts.append({"index": k, "quote": q, "status": "not_verbatim", "occurrences": [],
                             "whitespace_variant": _collapsed(q) in _collapsed(jd), "repeated_in_answer": counts.get(q, 0) > 1})
        elif len(occ) > 1:
            verdicts.append({"index": k, "quote": q, "status": "ambiguous", "occurrences": occ, "whitespace_variant": False,
                             "repeated_in_answer": counts.get(q, 0) > 1})
        else:
            verdicts.append({"index": k, "quote": q, "status": "unique", "occurrences": occ, "whitespace_variant": False,
                             "repeated_in_answer": counts.get(q, 0) > 1})
    return verdicts


def _has_word(text: str) -> bool:
    """A run that is only punctuation (for example the final full stop) is not a missing clause."""
    return any(ch.isalnum() for ch in text)


def _uncovered_runs(jd: str, unit, credited: bytearray) -> list[dict]:
    """Maximal runs of uncovered text in the unit, as exact original offsets. A run is closed by a covered character; uncovered
    whitespace is skipped at its edges, so each run starts and ends on a non-space character of the original text."""
    runs, start, end = [], None, None
    for a, b in unit.spans:
        for p in range(a, b):
            if credited[p]:
                if start is not None:
                    runs.append((start, end))
                    start = end = None
            elif not jd[p].isspace():
                if start is None:
                    start = p
                end = p + 1
    if start is not None:
        runs.append((start, end))
    return [{"start": a, "end": b, "text": jd[a:b]} for a, b in runs]


def cover(inv, jd: str, quotes: list) -> dict:
    """Quotation coverage of the inventory's requirement units by the given quotes. The JD must be the text the inventory was built from."""
    from inventory import sha256_text  # noqa: PLC0415
    if sha256_text(jd) != inv.jd_sha256:
        raise ValueError("the JD text does not match the inventory")
    verdicts = judge_quotes(jd, quotes)
    credited = bytearray(len(jd))
    ambiguous_spans = []
    for v in verdicts:
        if v["status"] == "unique":
            s = v["occurrences"][0]
            for p in range(s, s + len(v["quote"])):
                credited[p] = 1
        elif v["status"] == "ambiguous":
            ambiguous_spans.append((v["quote"], v["occurrences"]))

    units = []
    for u in inv.requirements():
        positions = [p for a, b in u.spans for p in range(a, b) if not jd[p].isspace()]
        total = len(positions)
        got = sum(1 for p in positions if credited[p])
        frac = got / total if total else 0.0
        touched = [q for q, occ in ambiguous_spans
                   if any(o < b and o + len(q) > a for o in occ for a, b in u.spans)]
        if frac >= COVER_THRESHOLD:
            status = "covered"
        elif frac > 0:
            status = "partial"
        elif touched:
            status = "ambiguous"
        else:
            status = "omitted"
        runs = _uncovered_runs(jd, u, credited)
        units.append({"unit_id": u.unit_id, "status": status, "fraction": round(frac, 4), "text": u.text,
                      "first_line": u.first_line, "first_offset": positions[0] if positions else None, "ambiguous_quotes": touched,
                      "uncovered_spans": runs, "clause_review_required": any(_has_word(r["text"]) for r in runs),
                      "all_text_quoted": not runs})
    summary = {s: sum(1 for u in units if u["status"] == s) for s in ("covered", "partial", "ambiguous", "omitted")}
    summary["requirement_units"] = len(units)
    summary["all_text_quoted"] = sum(1 for u in units if u["all_text_quoted"])
    summary["clause_review_required"] = sum(1 for u in units if u["clause_review_required"])
    return {"summary": summary, "units": units, "quotes": verdicts,
            "quote_counts": {s: sum(1 for v in verdicts if v["status"] == s) for s in ("unique", "ambiguous", "not_verbatim", "invalid")},
            "repeated_quotes_in_answer": sum(1 for v in verdicts if v["repeated_in_answer"])}
