"""
S0 v2 — deterministic date anchors (S0b) and month-interval arithmetic (S0e).

Every date RANGE in the whole CV text becomes an anchor (A1..An, document
order). Anchors are facts parsed here and nowhere else: the S0c AI structurer
may only reference anchor IDs, never write or compute dates.

Grammar (case-insensitive; Arabic-Indic / Extended Arabic-Indic digits are
normalised to ASCII before matching — one char each, so offsets are kept):

  point forms                         example                 precision
  ─────────────────────────────────── ─────────────────────── ─────────
  day + month name + year             15 March 2019           month
  month name + day + , year           Mar 15, 2019            month
  month name (+ . , -) + year         Sept 2019, Mar. 2019    month
  DD/MM/YYYY  (also . and -)          15/03/2019              month
  YYYY/MM     (also . and -)          2019/03, 2019-03        month
  MM/YYYY     (also . and -)          03/2019                 month
  YYYY                                2019                    year

  range       = point SEP end
  end         = point | present-word | YY (only after a bare YYYY: 2019-21)
  SEP         = - – — ‐ − ~ / to till until through thru إلى الى حتى
  present     = present current now ongoing today date "till date" "to date"
                "till now" + Arabic equivalents
  open range  = (since | from | starting [from] | منذ) point, not followed by
                SEP  -> is_current and open_end
  cross-line  = a line ending in "point SEP" followed by a line starting with
                an end -> one anchor spanning both lines

DD/MM vs MM/DD: day-first is assumed; when both numbers are <= 12 and differ the
anchor is flagged ``day_month_ambiguous``; when the second number is > 12 the
form is read as MM/DD.

Validity (sanity, never repaired): start <= end, span <= 50 years, years
1950-2099, no start after the build month and no closed end after it. These are
parse-time facts about the CV, checked against the S0 build month
(``built_as_of``). An invalid range is still an anchor (it must be accounted
for) but has no interval.

Present / open-ended ranges store NO end year/month and NO interval: the end is
the fact "present", not a month. Their duration is computed deterministically
by ``Anchor.interval_at(as_of)`` / ``duration_at(as_of)`` with the scoring
run's ``as_of`` — never with the build month and never with the system clock.
Closed ranges keep their stored interval, identical at any ``as_of``.

Duration convention (unchanged from the S4 design and the legacy extractor):
both months known -> month arithmetic; otherwise whole years. Interval is
[start, end) on a month index (year*12 + month-1, January when the month is not
used). Zero-length ranges have unknown duration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5, "يونيو": 6,
    "يوليو": 7, "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10,
    "نوفمبر": 11, "ديسمبر": 12,
    "كانون الثاني": 1, "شباط": 2, "آذار": 3, "نيسان": 4, "أيار": 5, "حزيران": 6,
    "تموز": 7, "آب": 8, "أيلول": 9, "تشرين الأول": 10, "تشرين الثاني": 11, "كانون الأول": 12,
}
_MONTH_RE = "(?:" + "|".join(
    re.escape(m).replace(r"\ ", r"\s+") for m in sorted(_MONTHS, key=len, reverse=True)) + ")"

_YEAR = r"(?:19[5-9]\d|20\d\d)"
_NUM_SEP = r"[/.\-]"
_P_DAY_MONTH_YEAR = rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH_RE}\.?,?\s+{_YEAR}"
_P_MONTH_DAY_YEAR = rf"{_MONTH_RE}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,\s*{_YEAR}"
_P_MONTH_YEAR = rf"{_MONTH_RE}\.?[\s,\-]*{_YEAR}"
_P_DMY = rf"\d{{1,2}}{_NUM_SEP}\d{{1,2}}{_NUM_SEP}{_YEAR}"
_P_YM = rf"{_YEAR}{_NUM_SEP}(?:0?[1-9]|1[0-2])(?![\d/.\-])"
_P_MY = rf"(?:0?[1-9]|1[0-2]){_NUM_SEP}{_YEAR}"
_P_Y = _YEAR
_POINT = "(?:" + "|".join([_P_DAY_MONTH_YEAR, _P_MONTH_DAY_YEAR, _P_MONTH_YEAR,
                           _P_DMY, _P_YM, _P_MY, _P_Y]) + ")"

_PRESENT = (r"(?:present|current(?:ly)?|now|ongoing|today|till\s+date|to\s+date|till\s+now|date"
            r"|حاليا|حالياً|الآن|حتى\s+الآن|إلى\s+الآن|الى\s+الآن|الوقت\s+الحاضر|الحالي)")
_SEP = r"(?:-|–|—|‐|−|~|/|\bto\b|\btill\b|\buntil\b|\bthrough\b|\bthru\b|إلى|الى|حتى)"
_YY = r"\d{2}(?![\d/.\-])"
_LEFT = r"(?<![\w/.\-])"
_RIGHT = r"(?![\w])"

_RANGE_RE = re.compile(
    rf"{_LEFT}(?P<start>{_POINT})\s*(?P<sep>{_SEP})\s*(?P<end>{_PRESENT}|{_POINT}|{_YY}){_RIGHT}",
    re.IGNORECASE | re.UNICODE)
_OPEN_RE = re.compile(
    rf"(?P<kw>\bsince\b|\bfrom\b|\bstarting(?:\s+from)?\b|منذ)\s+(?P<start>{_POINT}){_RIGHT}",
    re.IGNORECASE | re.UNICODE)
_LINE_END_START_RE = re.compile(rf"{_LEFT}(?P<start>{_POINT})\s*(?P<sep>{_SEP})\s*$",
                                re.IGNORECASE | re.UNICODE)
_LINE_START_END_RE = re.compile(rf"^\s*(?P<end>{_PRESENT}|{_POINT}){_RIGHT}",
                                re.IGNORECASE | re.UNICODE)
_PRESENT_FULL = re.compile(rf"^{_PRESENT}$", re.IGNORECASE | re.UNICODE)

# Date-like text that is NOT parsed (recorded, never guessed).
_UNPARSED_RES = (
    ("month_apostrophe_year", re.compile(r"\b[A-Za-z]{3,9}\.?\s*['’]\d{2}\b")),
    ("quarter", re.compile(r"\bQ[1-4]\s*[/\-]?\s*(?:\d{2}|19\d\d|20\d\d)\b", re.IGNORECASE)),
)

_POINT_FORMS = (
    ("day_month_name", re.compile(
        rf"^(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{_MONTH_RE})\.?,?\s+(?P<y>{_YEAR})$", re.I)),
    ("month_name_day", re.compile(
        rf"^(?P<m>{_MONTH_RE})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,\s*(?P<y>{_YEAR})$", re.I)),
    ("month_name", re.compile(rf"^(?P<m>{_MONTH_RE})\.?[\s,\-]*(?P<y>{_YEAR})$", re.I)),
    ("dd/mm/yyyy", re.compile(rf"^(?P<a>\d{{1,2}}){_NUM_SEP}(?P<b>\d{{1,2}}){_NUM_SEP}(?P<y>{_YEAR})$")),
    ("yyyy/mm", re.compile(rf"^(?P<y>{_YEAR}){_NUM_SEP}(?P<mn>0?[1-9]|1[0-2])$")),
    ("mm/yyyy", re.compile(rf"^(?P<mn>0?[1-9]|1[0-2]){_NUM_SEP}(?P<y>{_YEAR})$")),
    ("yyyy", re.compile(rf"^(?P<y>{_YEAR})$")),
)

MAX_SPAN_YEARS = 50


@dataclass(frozen=True)
class DatePoint:
    year: int
    month: int | None
    form: str
    day_month_ambiguous: bool = False


def parse_point(text: str) -> DatePoint | None:
    """Parse one date point (already digit-normalised). None when unparseable."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    for form, rx in _POINT_FORMS:
        m = rx.match(s)
        if not m:
            continue
        y = int(m.group("y"))
        if form in ("day_month_name", "month_name_day", "month_name"):
            month = _MONTHS.get(re.sub(r"\s+", " ", m.group("m").lower().rstrip(".")))
            if month is None:
                return None
            return DatePoint(y, month, form)
        if form == "dd/mm/yyyy":
            a, b = int(m.group("a")), int(m.group("b"))
            if b > 12 and 1 <= a <= 12:          # MM/DD/YYYY
                return DatePoint(y, a, form)
            if 1 <= b <= 12 and 1 <= a <= 31:    # DD/MM/YYYY (default)
                return DatePoint(y, b, form, day_month_ambiguous=(a <= 12 and a != b))
            return None
        if form in ("yyyy/mm", "mm/yyyy"):
            return DatePoint(y, int(m.group("mn")), form)
        return DatePoint(y, None, form)
    return None


@dataclass(frozen=True)
class Anchor:
    anchor_id: str
    line: int                    # 1-based line of the range start
    end_line: int                # == line unless the range wraps onto the next line
    char_start: int              # offsets within ``line``
    char_end: int                # offset within ``end_line``
    text: str                    # verbatim (original digits)
    grammar: str                 # e.g. "month_name→present", "yyyy→yy", "open:month_name"
    start_year: int
    start_month: int | None
    end_year: int | None         # None when is_current: the end is "present", not a month
    end_month: int | None
    is_current: bool
    open_end: bool
    precision: str               # "month" | "year"
    parse_status: str            # "ok" | "invalid"
    invalid_reason: str | None
    interval: tuple[int, int] | None        # closed ranges only; None when is_current
    duration_months: int | None             # closed ranges only; None when is_current
    digits_normalized: bool = False
    day_month_ambiguous: bool = False

    @property
    def duration_known(self) -> bool:
        """Whether a duration is determinable (independent of any as_of): a valid
        current range, or a valid closed range of non-zero length."""
        return self.parse_status == "ok" and (self.is_current or self.interval is not None)

    def interval_at(self, as_of: tuple[int, int]) -> tuple[int, int] | None:
        """[start, end) month index at the scoring month ``as_of``. Closed ranges
        return their stored interval; current ones end at ``as_of`` (month
        arithmetic only when the start month is known, else whole years)."""
        if self.parse_status != "ok":
            return None
        if not self.is_current:
            return self.interval
        y, m = check_as_of(as_of)
        return month_interval(self.start_year, self.start_month, y,
                              m if self.start_month is not None else None)

    def duration_at(self, as_of: tuple[int, int]) -> int | None:
        iv = self.interval_at(as_of)
        return None if iv is None else iv[1] - iv[0]

    def to_dict(self) -> dict:
        return {
            "anchor_id": self.anchor_id, "line": self.line, "end_line": self.end_line,
            "char_start": self.char_start, "char_end": self.char_end, "text": self.text,
            "grammar": self.grammar,
            "start": {"year": self.start_year, "month": self.start_month},
            "end": {"year": self.end_year, "month": self.end_month},
            "is_current": self.is_current, "open_end": self.open_end, "precision": self.precision,
            "parse_status": self.parse_status, "invalid_reason": self.invalid_reason,
            "interval": list(self.interval) if self.interval else None,
            "duration_months": self.duration_months,
            "digits_normalized": self.digits_normalized,
            "day_month_ambiguous": self.day_month_ambiguous,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Anchor":
        return cls(
            anchor_id=d["anchor_id"], line=d["line"], end_line=d.get("end_line", d["line"]),
            char_start=d["char_start"], char_end=d["char_end"], text=d["text"], grammar=d["grammar"],
            start_year=d["start"]["year"], start_month=d["start"]["month"],
            end_year=d["end"]["year"], end_month=d["end"]["month"],
            is_current=d["is_current"], open_end=d["open_end"], precision=d["precision"],
            parse_status=d["parse_status"], invalid_reason=d.get("invalid_reason"),
            interval=tuple(d["interval"]) if d.get("interval") else None,
            duration_months=d.get("duration_months"),
            digits_normalized=d.get("digits_normalized", False),
            day_month_ambiguous=d.get("day_month_ambiguous", False),
        )


def check_as_of(as_of) -> tuple[int, int]:
    if (not isinstance(as_of, tuple) or len(as_of) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in as_of)
            or not 1 <= as_of[1] <= 12):
        raise ValueError(f"as_of must be a (year, month) tuple, got {as_of!r}")
    return as_of


def month_interval(sy: int | None, sm: int | None, ey: int | None, em: int | None
                   ) -> tuple[int, int] | None:
    """[start, end) month index; whole years unless both months are known.
    None when a bound is missing or the range has zero/negative length."""
    if sy is None or ey is None:
        return None
    if sm is not None and em is not None:
        start, end = sy * 12 + (sm - 1), ey * 12 + (em - 1)
    else:
        start, end = sy * 12, ey * 12
    return (start, end) if end > start else None


def _validity(start: DatePoint, ey: int, em: int | None, current: bool,
              built: tuple[int, int]) -> str | None:
    if not (1950 <= start.year <= 2099 and 1950 <= ey <= 2099):
        return "year_out_of_range"
    s = start.year * 12 + ((start.month or 1) - 1)
    e = ey * 12 + ((em or 1) - 1)
    if e < s:
        return "end_before_start"
    if ey - start.year > MAX_SPAN_YEARS:
        return "span_over_50_years"
    now = built[0] * 12 + (built[1] - 1)
    if s > now:
        return "start_in_future"
    if not current and e > now:
        return "end_in_future"
    return None


def _make_anchor(idx: int, line: int, end_line: int, cs: int, ce: int, text: str,
                 start_txt: str, end_txt: str | None, built: tuple[int, int],
                 normalized: bool, open_kw: bool) -> Anchor | None:
    start = parse_point(start_txt)
    if start is None:
        return None
    current = open_kw
    end_form = "present" if open_kw else ""
    if open_kw:
        ey, em = built
    elif _PRESENT_FULL.match(re.sub(r"\s+", " ", end_txt.strip())):
        ey, em, current, end_form = built[0], built[1], True, "present"
    elif re.fullmatch(r"\d{2}", end_txt.strip()):
        if start.form != "yyyy":
            return None
        yy = int(end_txt)
        ey = start.year // 100 * 100 + yy
        if ey < start.year:
            ey += 100
        em, end_form = None, "yy"
    else:
        end = parse_point(end_txt)
        if end is None:
            return None
        ey, em, end_form = end.year, end.month, end.form
        start = DatePoint(start.year, start.month, start.form,
                          start.day_month_ambiguous or end.day_month_ambiguous)
    reason = _validity(start, ey, em, current, built)
    if current:
        # The end is the fact "present": no end month, no stored interval.
        ey = em = None
        interval = None
        precision = "month" if start.month is not None else "year"
    else:
        interval = None if reason else month_interval(start.year, start.month, ey, em)
        precision = "month" if (start.month is not None and em is not None) else "year"
    grammar = (f"open:{start.form}" if open_kw else f"{start.form}→{end_form}")
    return Anchor(
        anchor_id=f"A{idx}", line=line, end_line=end_line, char_start=cs, char_end=ce, text=text,
        grammar=grammar, start_year=start.year, start_month=start.month, end_year=ey, end_month=em,
        is_current=current, open_end=open_kw, precision=precision,
        parse_status="invalid" if reason else "ok", invalid_reason=reason,
        interval=interval, duration_months=None if interval is None else interval[1] - interval[0],
        digits_normalized=normalized, day_month_ambiguous=start.day_month_ambiguous)


@dataclass
class AnchorScan:
    anchors: list[Anchor] = field(default_factory=list)
    unparsed: list[dict] = field(default_factory=list)


def extract_anchors(lines: list[str], built: tuple[int, int]) -> AnchorScan:
    """S0b — every date range in the document, in (line, char) order.
    ``built`` is the S0 BUILD month, used only for the validity checks."""
    found: list[tuple[int, int, int, int, str, str, str | None, bool, bool]] = []
    norm = [ln.translate(_DIGITS) for ln in lines]
    consumed: dict[int, list[tuple[int, int]]] = {}

    def _free(i: int, a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in consumed.get(i, []))

    for i, nl in enumerate(norm):
        for m in _RANGE_RE.finditer(nl):
            found.append((i, i, m.start(), m.end(), lines[i][m.start():m.end()],
                          m.group("start"), m.group("end"), nl != lines[i], False))
            consumed.setdefault(i, []).append((m.start(), m.end()))
    # Cross-line ranges: "Jan 2019 –" / "Present"
    for i, nl in enumerate(norm):
        m = _LINE_END_START_RE.search(nl)
        if not m or not _free(i, m.start(), m.end()):
            continue
        j = i + 1
        while j < len(norm) and not norm[j].strip():
            j += 1
        if j >= len(norm):
            continue
        m2 = _LINE_START_END_RE.match(norm[j])
        if not m2 or not _free(j, m2.start("end"), m2.end("end")):
            continue
        text = lines[i][m.start():].rstrip() + " " + lines[j][m2.start("end"):m2.end("end")]
        found.append((i, j, m.start(), m2.end("end"), text, m.group("start"), m2.group("end"),
                      nl != lines[i] or norm[j] != lines[j], False))
        consumed.setdefault(i, []).append((m.start(), len(nl)))
        consumed.setdefault(j, []).append((m2.start("end"), m2.end("end")))
    # Open-ended "Since/From X"
    for i, nl in enumerate(norm):
        for m in _OPEN_RE.finditer(nl):
            if not _free(i, m.start("start"), m.end("start")):
                continue
            found.append((i, i, m.start(), m.end(), lines[i][m.start():m.end()],
                          m.group("start"), None, nl != lines[i], True))
            consumed.setdefault(i, []).append((m.start(), m.end()))

    found.sort(key=lambda f: (f[0], f[2]))
    scan = AnchorScan()
    for (i, j, cs, ce, text, st, en, normalized, open_kw) in found:
        a = _make_anchor(len(scan.anchors) + 1, i + 1, j + 1, cs, ce, text, st, en, built,
                         normalized, open_kw)
        if a is None:
            scan.unparsed.append({"line": i + 1, "text": text, "kind": "unparseable_range"})
        else:
            scan.anchors.append(a)
    for i, nl in enumerate(norm):
        for kind, rx in _UNPARSED_RES:
            for m in rx.finditer(nl):
                if _free(i, m.start(), m.end()):
                    scan.unparsed.append({"line": i + 1, "text": lines[i][m.start():m.end()],
                                          "kind": kind})
    scan.unparsed.sort(key=lambda u: u["line"])
    return scan


def merged_months(intervals: Iterable[tuple[int, int]]) -> int:
    merged: list[list[int]] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return sum(e - s for s, e in merged)
