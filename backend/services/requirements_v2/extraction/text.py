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


def locate_quote(jd_text: str, quote: str) -> str | None:
    """The exact slice of `jd_text` that `quote` refers to (first occurrence), or None when it is not in the text."""
    if not isinstance(jd_text, str) or not isinstance(quote, str):
        return None
    wanted = normalize(quote)
    if not wanted:
        return None
    haystack, origin = normalize_with_map(jd_text)
    start = haystack.find(wanted)
    if start < 0:
        return None
    first, last = origin[start], origin[start + len(wanted) - 1]
    return jd_text[first:last + 1]


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
