"""
S2 — deterministic masking and quote → original-span mapping.

The S2 model must never see dates or the numeric duration threshold:

  * entry lines: every S0 anchor span (single-line, two-line, open-ended,
    invalid, ignored, inside responsibilities) and every recorded unparsed date
    text is replaced by the token ``[dates]``; overlapping/adjacent spans are
    merged first. Each masked line keeps an offset map back to the original
    line so an accepted quote can be mapped to the exact original CV span.
  * title / employer strings: the S0 date grammar is re-run over the string and
    any range / unparsed date text is masked the same way.
  * criterion text: the number in every quantity-of-time expression is replaced
    by ``[N]`` (digits, Arabic-Indic digits, English and Arabic number words,
    ranges, "5+", "5-year", Arabic dual forms); a residual check refuses to
    return text that still contains a quantity of time.

Free-text duration phrases inside CV lines are NOT masked (Phase-1 decision).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from services.s0_experience.dates import extract_anchors

MASK_TOKEN = "[dates]"
THRESHOLD_TOKEN = "[N]"


class MaskingError(RuntimeError):
    """A deterministic masking invariant failed (a bug, never a candidate result)."""


# ── line masking ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MaskedLine:
    line: int
    original: str
    masked: str
    origin: tuple[int | None, ...]      # per masked char: original index, None inside a token

    @property
    def mask_count(self) -> int:
        return self.masked.count(MASK_TOKEN)


def merge_spans(spans) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted((max(0, s), e) for s, e in spans if e > s):
        if out and s <= out[-1][1]:            # overlapping or adjacent
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def mask_text(original: str, spans, line: int = 0) -> MaskedLine:
    masked: list[str] = []
    origin: list[int | None] = []
    pos = 0
    for s, e in merge_spans((s, min(e, len(original))) for s, e in spans):
        for k in range(pos, s):
            masked.append(original[k])
            origin.append(k)
        masked.append(MASK_TOKEN)
        origin.extend([None] * len(MASK_TOKEN))
        pos = e
    for k in range(pos, len(original)):
        masked.append(original[k])
        origin.append(k)
    return MaskedLine(line, original, "".join(masked), tuple(origin))


def _find_all(hay: str, needle: str):
    i = hay.find(needle)
    while needle and i != -1:
        yield i
        i = hay.find(needle, i + 1)


def date_spans_by_line(doc, original_lines: list[str]) -> dict[int, list[tuple[int, int]]]:
    """1-based line -> spans of every S0 anchor and recorded unparsed date text."""
    spans: dict[int, list[tuple[int, int]]] = {}
    for a in doc.anchors:
        if a.end_line == a.line:
            spans.setdefault(a.line, []).append((a.char_start, a.char_end))
        else:
            spans.setdefault(a.line, []).append((a.char_start, len(original_lines[a.line - 1])))
            spans.setdefault(a.end_line, []).append((0, a.char_end))
    for u in doc.unparsed_date_texts:
        ln, txt = u.get("line"), u.get("text") or ""
        if ln and 1 <= ln <= len(original_lines):
            for i in _find_all(original_lines[ln - 1], txt):
                spans.setdefault(ln, []).append((i, i + len(txt)))
    return spans


def mask_line(line_no: int, original_lines: list[str], spans_by_line) -> MaskedLine:
    return mask_text(original_lines[line_no - 1], spans_by_line.get(line_no, ()), line_no)


def mask_free_text(text: str) -> str:
    """Mask date ranges / unparsed date text found by the S0 grammar inside a
    standalone string (title, employer)."""
    if not text:
        return text
    scan = extract_anchors([text], (2100, 12))       # validity month irrelevant for masking
    spans = [(a.char_start, a.char_end) for a in scan.anchors]
    for u in scan.unparsed:
        for i in _find_all(text, u["text"]):
            spans.append((i, i + len(u["text"])))
    return mask_text(text, spans).masked


# ── criterion threshold masking ──────────────────────────────────────────────

_EN_NUM_WORDS = ("one two three four five six seven eight nine ten eleven twelve thirteen "
                 "fourteen fifteen sixteen seventeen eighteen nineteen twenty twenty-one "
                 "twenty-two twenty-three twenty-four twenty-five twenty-six twenty-seven "
                 "twenty-eight twenty-nine thirty").split()
_AR_NUM_WORDS = ("واحد واحدة اثنين اثنتين اثنان ثلاث ثلاثة أربع أربعة اربع اربعة خمس خمسة "
                 "ست ستة سبع سبعة ثمان ثماني ثمانية تسع تسعة عشر عشرة").split()
_NUM = (r"(?:\d+(?:[.,]\d+)?|[٠-٩۰-۹]+(?:[.,][٠-٩۰-۹]+)?|"
        + "|".join(sorted((re.escape(w) for w in _EN_NUM_WORDS + _AR_NUM_WORDS), key=len, reverse=True))
        + ")")
_UNIT = (r"(?:years?|yrs?|months?|mos?|سنوات|سنين|سنة|أعوام|اعوام|عاما|عامًا|عام|"
         r"أشهر|اشهر|شهور|شهرا|شهر)")
_DUAL = r"(?:سنتين|سنتان|عامين|عامان|شهرين|شهران)"
_QUANTITY_RE = re.compile(
    rf"(?<![\w\[])(?P<num>{_NUM}(?:\s*\(\s*{_NUM}\s*\))?"
    rf"(?:\s*(?:-|–|—|to|or|إلى|الى|أو|او)\s*{_NUM}(?:\s*\(\s*{_NUM}\s*\))?)?)"
    rf"\s*(?:\+|plus)?\s*(?:-|–)?\s*(?P<unit>{_UNIT})(?!\w)",
    re.IGNORECASE | re.UNICODE)
_DUAL_RE = re.compile(rf"(?<!\w){_DUAL}(?!\w)", re.UNICODE)


def mask_threshold(criterion_text: str) -> str:
    out = _QUANTITY_RE.sub(lambda m: f"{THRESHOLD_TOKEN} {m.group('unit')}", criterion_text or "")
    out = _DUAL_RE.sub(f"{THRESHOLD_TOKEN} سنوات", out)
    if _QUANTITY_RE.search(out) or _DUAL_RE.search(out):
        raise MaskingError("criterion text still contains a quantity of time after masking")
    return out


# ── quote location / original-span mapping ───────────────────────────────────

_WS = re.compile(r"\s")


def _norm_with_index(s: str) -> tuple[str, list[int]]:
    """NFKC + casefold + whitespace collapse, with a pointer per output char
    back to the index of the input char it came from."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(s):
        for c in unicodedata.normalize("NFKC", ch).casefold():
            if _WS.match(c):
                if prev_space:
                    continue
                c, prev_space = " ", True
            else:
                prev_space = False
            out.append(c)
            idx.append(i)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def normalize(s: str) -> str:
    return _norm_with_index(s)[0]


@dataclass(frozen=True)
class QuoteSpan:
    char_start: int
    char_end: int
    original_text: str
    occurrence: str                       # "only" | "first_of_<n>"


def locate_quote(quote: str, ml: MaskedLine) -> tuple[QuoteSpan | None, str | None]:
    """Find ``quote`` in the masked line (normalised comparison). Returns the
    original span of the first occurrence that does not touch a mask token, or
    (None, reason) with reason "not_found" / "overlaps_mask"."""
    q = normalize(quote)
    if not q:
        return None, "not_found"
    hay, idx = _norm_with_index(ml.masked)
    clean: list[tuple[int, int]] = []
    found_any = False
    for i in _find_all(hay, q):
        found_any = True
        ms, me = idx[i], idx[i + len(q) - 1] + 1
        if any(o is None for o in ml.origin[ms:me]):
            continue
        clean.append((ms, me))
    if not clean:
        return None, ("overlaps_mask" if found_any else "not_found")
    ms, me = clean[0]
    os_, oe = ml.origin[ms], ml.origin[me - 1] + 1
    return QuoteSpan(os_, oe, ml.original[os_:oe],
                     "only" if len(clean) == 1 else f"first_of_{len(clean)}"), None
