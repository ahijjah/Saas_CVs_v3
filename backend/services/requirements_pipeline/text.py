# PRODUCTION COPY of services/requirements_v2/extraction/text.py; identical apart from import paths (pinned by tests/test_requirements_pipeline_service.py). Change the source first, never this file alone.
"""
Text helpers for validating AI quotations against the job description. Pure functions, no I/O.

normalize_with_map() produces a comparison form of a text (NFKC, case-folded, whitespace collapsed, Arabic
diacritics / tatweel / invisible bidi marks removed) together with, for every normalized character, the index of the
original character it came from. locate_quote() uses it to find a quotation that differs from the job description only
in whitespace, case, diacritics or tatweel, and returns the EXACT slice of the original text, so the stored source
wording is always the job description's own characters, never the model's copy of them.
"""
from __future__ import annotations

import re
import unicodedata

_STRIPPED = {0x0640, 0x0670, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF, 0x2060}
_STRIPPED |= set(range(0x064B, 0x0660)) | set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """(normalized text, origin) where origin[i] is the index in `text` of normalized character i."""
    out: list[str] = []
    origin: list[int] = []
    for idx, ch in enumerate(text):
        for piece in unicodedata.normalize("NFKC", ch):
            if ord(piece) in _STRIPPED:
                continue
            if piece.isspace():
                if out and out[-1] != " ":
                    out.append(" ")
                    origin.append(idx)
                continue
            for folded in piece.casefold():
                out.append(folded)
                origin.append(idx)
    while out and out[-1] == " ":
        out.pop()
        origin.pop()
    if out and out[0] == " ":
        out.pop(0)
        origin.pop(0)
    return "".join(out), origin


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def locate_span(jd_text: str, quote: str) -> tuple[int, int] | None:
    """(start, end) in `jd_text` of the first occurrence of `quote`, or None when it is not in the text."""
    if not isinstance(jd_text, str) or not isinstance(quote, str):
        return None
    wanted = normalize(quote)
    if not wanted:
        return None
    haystack, origin = normalize_with_map(jd_text)
    start = haystack.find(wanted)
    if start < 0:
        return None
    return origin[start], origin[start + len(wanted) - 1] + 1


def locate_quote(jd_text: str, quote: str) -> str | None:
    """The exact slice of `jd_text` that `quote` refers to (first occurrence), or None when it is not in the text."""
    span = locate_span(jd_text, quote)
    return None if span is None else jd_text[span[0]:span[1]]


def contains_phrase(jd_text: str, phrase: str) -> bool:
    return locate_quote(jd_text, phrase) is not None


# ── years found in a piece of text (used only to cross-check an AI duration) ──

_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_EN_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
               "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20}
_NUMBER_RE = re.compile(r"\d+")
_WORD_RE = re.compile(r"[a-z]+")


def numbers_in(text: str) -> set[int]:
    """Whole numbers written as digits (Western, Arabic-Indic, Persian) or as English number words."""
    t = text.translate(_DIGIT_MAP).lower()
    found = {int(m) for m in _NUMBER_RE.findall(t)}
    found |= {_EN_NUMBERS[w] for w in _WORD_RE.findall(t) if w in _EN_NUMBERS}
    return found


# ── does a preferred-cue apply to a given item? ───────────────────────────────

_BULLET_RE = re.compile(r"^\s*(?:[-*•·–—▪●◦‣]|\(?[0-9٠-٩۰-۹]+[.)\-]|\(?[A-Za-z][.)])\s*")
_SENTENCE_END = (".", "!", "?", "؟", "。")
_SENTENCE_BREAK_RE = re.compile(r"[.!?؟。]\s+")
_HEADING_MAX_WORDS = 6


def _is_heading_line(line: str, next_line: str) -> bool:
    """A section heading: a non-bullet line that ends with a colon, or a short non-sentence line that introduces a
    bulleted / numbered list (the next non-empty line is a list entry). A short line followed by plain text is not
    treated as a heading: it may be an item itself."""
    stripped = line.strip()
    if not stripped or _BULLET_RE.match(stripped):
        return False
    if stripped.endswith((":", "\uff1a")):
        return True
    return (len(stripped.split()) <= _HEADING_MAX_WORDS and not stripped.endswith(_SENTENCE_END)
            and bool(_BULLET_RE.match(next_line.strip())))


def cue_relationship(jd_text: str, span: tuple[int, int], cue: str) -> str | None:
    """How `cue` is tied to the item whose wording occupies `span` of the job description:

      "inline"   the cue is inside the item's own wording ("LinkedIn Recruiter is a plus")
      "heading"  the cue is earlier in the item's own SENTENCE ("Preferred: Docker, Kubernetes"; a cue in an earlier
                 sentence of the same line does not count), or in the NEAREST heading above it ("Nice to have:"
                 followed by the list the item belongs to)
      None       not established: the cue may exist elsewhere in the job description (another requirement's wording,
                 another section's heading), which is not proof that it applies to this item.
    """
    start, end = span
    if contains_phrase(jd_text[start:end], cue):
        return "inline"
    line_start = jd_text.rfind("\n", 0, start) + 1
    same_sentence = _SENTENCE_BREAK_RE.split(jd_text[line_start:start])[-1]
    if contains_phrase(same_sentence, cue):
        return "heading"
    line_end = jd_text.find("\n", end)
    below = jd_text[line_start:len(jd_text) if line_end < 0 else line_end]       # the item's own line
    cursor = line_start
    while cursor > 0:
        prev_start = jd_text.rfind("\n", 0, cursor - 1) + 1
        line = jd_text[prev_start:cursor - 1]
        cursor = prev_start
        if _is_heading_line(line, below):
            return "heading" if contains_phrase(line, cue) else None
        if line.strip():
            below = line
    return None
