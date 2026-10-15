"""
S1 deterministic duration parser (JD requirement text only; never CV text).

  parse_durations(text) -> [DurationMatch]   non-overlapping, left to right

Supported (no fuzzy matching, no synonym tables):
  numbers   ASCII digits incl. decimals (1.5), Arabic-Indic ٠-٩ / Extended ۰-۹
            digits (translated 1:1, so offsets index the ORIGINAL text),
            English words one..twenty, thirty, Arabic words واحد..عشرة,
            "five (5)" (the parenthesised digits give the value)
  units     years / yrs / year, months / mos; سنوات سنين سنة أعوام عام عاما,
            أشهر شهور شهر شهرا; months are converted to years (value / 12)
  duals     سنتين سنتان عامين عامان (2 years), شهرين شهران (2 months)
  singular  سنة واحدة / عام واحد / شهر واحد (1)
  open      "5+ years", "5 or more years", "5 سنوات أو أكثر" / "فأكثر"
  ranges    "3-5 years", "3 – 5", "3 to 5", "3 إلى 5": value = lower bound,
            upper kept; bound = "range"

``years`` is the normalised LOWER bound in years. The existing S2 threshold
masking is NOT changed or reused (S1 does not depend on S2).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")

EN_WORDS = {w: i for i, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split(), 1)}
EN_WORDS["thirty"] = 30
AR_WORDS = {"واحد": 1, "واحدة": 1, "اثنين": 2, "اثنتين": 2, "اثنان": 2, "اثنتان": 2,
            "ثلاث": 3, "ثلاثة": 3, "أربع": 4, "أربعة": 4, "اربع": 4, "اربعة": 4,
            "خمس": 5, "خمسة": 5, "ست": 6, "ستة": 6, "سبع": 7, "سبعة": 7,
            "ثمان": 8, "ثماني": 8, "ثمانية": 8, "تسع": 9, "تسعة": 9, "عشر": 10, "عشرة": 10}
_WORDS = {**EN_WORDS, **AR_WORDS}

YEAR_UNITS = ("years", "year", "yrs", "yr", "سنوات", "سنين", "سنة", "أعوام", "اعوام", "عامًا", "عاما", "عام")
MONTH_UNITS = ("months", "month", "mos", "mo", "أشهر", "اشهر", "شهور", "شهرا", "شهر")
DUALS = {"سنتين": ("years", 2), "سنتان": ("years", 2), "عامين": ("years", 2), "عامان": ("years", 2),
         "شهرين": ("months", 2), "شهران": ("months", 2)}


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_NUM = rf"(?:\d+(?:\.\d+)?|{_alt(_WORDS)})"
_PAREN = r"(?:\s*\(\s*(?P<{0}>\d+(?:\.\d+)?)\s*\))?"
_UNIT = _alt(YEAR_UNITS + MONTH_UNITS)
_MORE_PRE = r"(?:\s+or\s+more|\s+or\s+above)"
_MORE_POST = r"(?:\s+or\s+more|\s+(?:أو|او)\s+(?:أكثر|اكثر)|\s*فأكثر|\s*فاكثر|\s+فما\s+فوق)"

_RE = re.compile(
    rf"(?<![\w.])(?P<dual>{_alt(DUALS)})(?!\w)(?P<dmore>{_MORE_POST})?"
    rf"|(?<![\w.])(?P<sunit>سنة|عام|شهر)\s+(?P<one>واحدة|واحد)(?!\w)"
    rf"|(?<![\w.])(?P<a>{_NUM}){_PAREN.format('ap')}(?P<plus1>\s*\+)?"
    rf"(?:\s*(?P<sep>-|–|—|to|إلى|الى)\s*(?P<b>{_NUM}){_PAREN.format('bp')}(?P<plus2>\s*\+)?)?"
    rf"(?P<pmore>{_MORE_PRE})?\s*(?P<unit>{_UNIT})(?!\w)(?P<more>{_MORE_POST})?",
    re.IGNORECASE | re.UNICODE)


@dataclass(frozen=True)
class DurationMatch:
    value: float            # lower bound in the source unit
    upper: float | None     # range upper bound in the source unit
    unit: str               # "years" | "months"
    years: float            # normalised lower bound in years
    upper_years: float | None
    bound: str              # "exact" | "at_least" | "range"
    text: str               # exact source substring
    start: int
    end: int

    @property
    def is_range(self) -> bool:
        return self.bound == "range"

    @property
    def open_ended(self) -> bool:
        return self.bound == "at_least"

    def to_dict(self) -> dict:
        return asdict(self)


def _num(token: str | None, paren: str | None) -> float | None:
    if paren:
        return float(paren)
    if token is None:
        return None
    t = token.lower()
    return float(_WORDS[t]) if t in _WORDS else float(t)


def _to_years(v: float | None, unit: str) -> float | None:
    if v is None:
        return None
    return v if unit == "years" else round(v / 12.0, 4)


def _unit_kind(u: str) -> str:
    return "years" if u.lower() in YEAR_UNITS else "months"


def parse_durations(text: str) -> list[DurationMatch]:
    src = text or ""
    ascii_digits = src.translate(_DIGITS)          # 1:1 characters: offsets unchanged
    out: list[DurationMatch] = []
    for m in _RE.finditer(ascii_digits):
        if m.group("dual"):
            unit, value = DUALS[m.group("dual")]
            upper, bound = None, "at_least" if m.group("dmore") else "exact"
            value = float(value)
        elif m.group("sunit"):
            unit, value, upper, bound = _unit_kind(m.group("sunit")), 1.0, None, "exact"
        else:
            unit = _unit_kind(m.group("unit"))
            value = _num(m.group("a"), m.group("ap"))
            upper = _num(m.group("b"), m.group("bp"))
            if upper is not None:
                bound = "range"
                if upper < value:
                    value, upper = upper, value
            elif m.group("plus1") or m.group("plus2") or m.group("pmore") or m.group("more"):
                bound = "at_least"
            else:
                bound = "exact"
        if not value or value <= 0:
            continue
        out.append(DurationMatch(value=value, upper=upper, unit=unit, years=_to_years(value, unit),
                                 upper_years=_to_years(upper, unit), bound=bound,
                                 text=src[m.start():m.end()], start=m.start(), end=m.end()))
    return out
