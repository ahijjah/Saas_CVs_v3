"""
Completeness checks on an extraction, OFFLINE and pure (no model call, no database).

A model can answer with valid JSON and still leave out a sentence the job description states plainly. Two checks catch that,
using only the job description and the draft that was built from the answer. Neither is matched on particular requirements:

  missed_preferred_statements   a sentence (or a list entry under a "Preferred:" heading) that carries an explicit preferred /
                                optional cue and is not the source wording of ANY item, non-scoreable condition or context.
  missed_context_statements     a sentence that states a reporting line or a work arrangement while the draft has no context.

The worker asks the model once more with these sentences named (see workers/requirements_v2_worker.py). A preferred statement that
is still uncovered after that fails the extraction instead of reporting success; a missed context statement does not.
"""
from __future__ import annotations

import re
from typing import Iterable

from services.requirements_v2.contract import CATEGORIES
from services.requirements_v2.extraction.cues import PREFERRED_RE
from services.requirements_v2.extraction.text import locate_span, normalize

_PREFERRED_RE = PREFERRED_RE        # the one vocabulary of the job description's optional / advantage wording (see cues.py)

# A reporting line or a work arrangement (the closed vocabulary the context block accepts).
_CONTEXT_RE = re.compile(
    r"(?<![^\W_])(?:reports?|reporting)\s+(?:directly\s+)?to(?![^\W_])|(?<![^\W_])(?:hybrid|remote|on-?site|in[- ]office)(?![^\W_])"
    r"|يرفع\s+تقريره|يتبع\s+(?:ل|إلى)|هجين|مختلط|عن\s+بع[دُ]|حضوري")

_BULLET_RE = re.compile(r"^\s*(?:[-*•·–—▪●◦‣]|\(?[0-9٠-٩۰-۹]+[.)\-]|\(?[A-Za-z][.)])\s*")
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?؟。])\s+")
_WORD_RE = re.compile(r"[^\W_]+")


def _segments(jd: str) -> Iterable[tuple[int, int, bool]]:
    """(start, end, under_preferred_heading) for every sentence of the job description. A line that ends with a colon is a heading,
    not a statement; when it carries a preferred cue, the list entries below it are statements under that cue."""
    offset = 0
    heading_cue = False
    for line in jd.split("\n"):
        line_start, offset = offset, offset + len(line) + 1
        stripped = line.strip()
        if not stripped:
            heading_cue = False
            continue
        if stripped.endswith((":", "：")) and not _BULLET_RE.match(stripped):
            heading_cue = bool(_PREFERRED_RE.search(normalize(stripped)))
            continue
        under = heading_cue and bool(_BULLET_RE.match(stripped))
        if heading_cue and not under:
            heading_cue = False
        cursor = 0
        for piece in _SENTENCE_BREAK_RE.split(line):
            at = line.find(piece, cursor)
            cursor = at + len(piece)
            if piece.strip():
                yield line_start + at, line_start + cursor, under


def _spans_of(jd: str, wordings: Iterable[object]) -> list[tuple[int, int]]:
    spans = []
    for wording in wordings:
        if isinstance(wording, str) and wording.strip():
            span = locate_span(jd, wording)
            if span:
                spans.append(span)
    return spans


def _covered(segment: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(start < segment[1] and segment[0] < end for start, end in spans)


def _statement(jd: str, start: int, end: int) -> str:
    return " ".join(_BULLET_RE.sub("", jd[start:end].strip(), count=1).split())


def covered_spans(jd: str, doc: dict, conditions: dict | None = None) -> list[tuple[int, int]]:
    """Where in the job description the draft already has something: item source wordings, the three non-scoreable condition
    lists, and the context's source wording."""
    wordings: list[object] = []
    for category in CATEGORIES:
        wordings += [item.get("source_text") for item in doc.get("categories", {}).get(category, {}).get("items", [])]
    for entries in (conditions or {}).values():
        wordings += [entry.get("source_text") for entry in entries if isinstance(entry, dict)]
    return _spans_of(jd, wordings)


def missed_preferred_statements(jd: str, doc: dict, conditions: dict | None = None, *, limit: int = 8) -> tuple[str, ...]:
    """Sentences that state a preferred requirement in the job description's own words but are covered by nothing in the draft."""
    spans = covered_spans(jd, doc, conditions)
    context_source = (doc.get("context") or {}).get("source_text")
    spans += _spans_of(jd, [context_source])
    missed: list[str] = []
    for start, end, under_heading in _segments(jd):
        text = _statement(jd, start, end)
        folded = normalize(text)
        cue = _PREFERRED_RE.search(folded)
        if not (cue or under_heading):
            continue
        remainder = folded[:cue.start()] + " " + folded[cue.end():] if cue else folded
        if len(_WORD_RE.findall(remainder)) < 1 or _covered((start, end), spans):
            continue
        missed.append(text)
        if len(missed) >= limit:
            break
    return tuple(missed)


def missed_context_statements(jd: str, doc: dict, *, limit: int = 4) -> tuple[str, ...]:
    """Sentences that state a reporting line or work arrangement when the draft has no context block at all."""
    if doc.get("context"):
        return ()
    missed: list[str] = []
    for start, end, _ in _segments(jd):
        text = _statement(jd, start, end)
        if _CONTEXT_RE.search(normalize(text)):
            missed.append(text)
            if len(missed) >= limit:
                break
    return tuple(missed)
