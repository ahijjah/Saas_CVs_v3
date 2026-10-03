"""
S1 JD text: deterministic normalisation, stable line numbering and verbatim spans.

  * Lines: the JD is split on line breaks; each line keeps its text verbatim
    (tabs -> spaces, trailing whitespace removed). Line numbers are 1-based and
    stable: blank lines keep their numbers and are omitted from the prompt.
  * Comparison form: NFKC + casefold + whitespace collapse, with an index map
    back to the original characters, so every match is reported as the ORIGINAL
    substring (Span). Matching is exact on the comparison form and must sit on
    word boundaries. No fuzzy matching, no synonym tables.
"""
from __future__ import annotations

import re
import unicodedata

from services.s1_requirements.durations import DurationMatch, parse_durations
from services.s1_requirements.schema import Span, sha256

_WS = re.compile(r"\s")


def split_jd_lines(text: str) -> list[str]:
    return [ln.replace("\t", " ").rstrip() for ln in (text or "").splitlines()]


def norm_with_index(s: str) -> tuple[str, list[int]]:
    """Comparison form plus, per output char, the index of its source char."""
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
    return norm_with_index(s)[0]


def _boundary_ok(hay: str, a: int, b: int) -> bool:
    before = hay[a - 1] if a > 0 else " "
    after = hay[b] if b < len(hay) else " "
    return not (before.isalnum() and hay[a].isalnum()) and not (after.isalnum() and hay[b - 1].isalnum())


class JDText:
    def __init__(self, text: str):
        self.raw = text or ""
        self.lines = split_jd_lines(self.raw)
        self._norm = [norm_with_index(ln) for ln in self.lines]

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def text_sha256(self) -> str:
        """Hash of the line-normalised JD (what S1 addresses)."""
        return sha256("\n".join(self.lines))

    def numbered(self) -> list[dict]:
        return [{"line": i, "text": ln} for i, ln in enumerate(self.lines, 1) if ln.strip()]

    def line(self, n: int) -> str:
        if not (isinstance(n, int) and 1 <= n <= len(self.lines)):
            raise IndexError(f"JD line {n!r} out of range")
        return self.lines[n - 1]

    def _spans_on(self, n: int, needle: str, *, boundaries: bool) -> list[Span]:
        nd = normalize(needle)
        if not nd:
            return []
        hay, idx = self._norm[n - 1]
        out, pos = [], hay.find(nd)
        while pos != -1:
            end = pos + len(nd)
            if not boundaries or _boundary_ok(hay, pos, end):
                s, e = idx[pos], idx[end - 1] + 1
                out.append(Span(n, s, e, self.lines[n - 1][s:e]))
            pos = hay.find(nd, pos + 1)
        return out

    def span_on_line(self, line: int, text: str, *, boundaries: bool = False) -> Span | None:
        """First occurrence of ``text`` (comparison form) on ``line``, as an original span."""
        if not (isinstance(line, int) and 1 <= line <= len(self.lines)):
            return None
        found = self._spans_on(line, text, boundaries=boundaries)
        return found[0] if found else None

    def find(self, needle: str) -> list[Span]:
        """All word-bounded verbatim (comparison-form) occurrences across the JD."""
        return [sp for n in range(1, len(self.lines) + 1) for sp in self._spans_on(n, needle, boundaries=True)]

    def durations(self) -> list[tuple[str, int, DurationMatch]]:
        """(candidate id D1.., line, match) for every duration in the JD, in order."""
        out = []
        for n, ln in enumerate(self.lines, 1):
            for m in parse_durations(ln):
                out.append((f"D{len(out) + 1}", n, m))
        return out
