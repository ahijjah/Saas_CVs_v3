"""
S1 JD text: deterministic normalisation, stable line numbering and verbatim spans.

  * Lines: the JD is split on line breaks; each line keeps its text verbatim
    (tabs -> spaces, trailing whitespace removed). Line numbers are 1-based and
    stable: blank lines keep their numbers and are omitted from the prompt.
  * Comparison form: NFKC + casefold + whitespace collapse, with an index map
    back to the original characters, so every match is reported as the ORIGINAL
    substring (Span). Matching is exact on the comparison form and must sit on
    word boundaries (an Arabic proclitic chain such as ك / و / فب may precede
    the start of a span; see _left_boundary_ok). No fuzzy matching, no synonym
    tables.
  * Canonical words (s1-5.1, one implementation for every word-level check):
    words() splits a text into comparison-form words; a dotted acronym
    ("P.M.", "U.X.") is ONE word whose key drops the periods ("pm").
    locate_words() finds a word run inside another word run under the same
    boundary rule as spans: whole words, except that the first word may sit
    right after an Arabic proclitic chain (proclitic_chain_ok).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

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


# Arabic attached proclitics: an optional conjunction (و "and", ف "so") followed by an optional
# preposition (ب "with/in", ك "as", ل "for"), written joined to the next word. A span may start right after
# such a chain (e.g. "مدير" inside "كمدير", "ومدير", "فبمدير"). Orthographic only: no meaning is attached,
# Latin script is unaffected and the right (suffix) boundary stays strict.
_AR_PROCLITIC_CHAIN = re.compile(r"[وف]?[بكل]?")


def _arabic_letter(ch: str) -> bool:
    return "\u0621" <= ch <= "\u064a"


def proclitic_chain_ok(prefix: str, next_char: str) -> bool:
    """The ONE approved left-side orthographic residue: an Arabic proclitic chain of 1-2 letters directly
    attached to an Arabic word (ك / و / فب ...). Nothing else may be stripped from a word."""
    return (1 <= len(prefix) <= 2 and _arabic_letter(next_char) and all(_arabic_letter(ch) for ch in prefix)
            and _AR_PROCLITIC_CHAIN.fullmatch(prefix) is not None)


def _left_boundary_ok(hay: str, a: int) -> bool:
    if a == 0 or not (hay[a - 1].isalnum() and hay[a].isalnum()):
        return True
    if not _arabic_letter(hay[a]):
        return False
    w = a
    while w > 0 and hay[w - 1].isalnum():
        w -= 1
    return proclitic_chain_ok(hay[w:a], hay[a])


def _boundary_ok(hay: str, a: int, b: int) -> bool:
    after = hay[b] if b < len(hay) else " "
    return _left_boundary_ok(hay, a) and not (after.isalnum() and hay[b - 1].isalnum())


# a dotted acronym: single letters each followed by a period ("p.m.", "u.x"), never part of a longer word
_WORD = re.compile(r"(?<!\w)[^\W\d_](?:\.[^\W\d_](?!\w))+\.?|\w+")


def words(s: str) -> list[str]:
    """Canonical word keys of ``s`` (comparison form; a dotted acronym is one word without its periods)."""
    return [m.group().replace(".", "") for m in _WORD.finditer(normalize(s))]


def locate_words(part: list[str], whole: list[str]) -> list[tuple[int, str]]:
    """Every start index i where the canonical word run ``part`` occurs in ``whole``, with the proclitic
    residue left in front of its first word ("" if none). Right edges and all later words are exact."""
    n, out = len(part), []
    if not n:
        return out
    for i in range(len(whole) - n + 1):
        if whole[i + 1:i + n] != part[1:]:
            continue
        w0, p0 = whole[i], part[0]
        if w0 == p0:
            out.append((i, ""))
        elif w0.endswith(p0) and proclitic_chain_ok(w0[:len(w0) - len(p0)], p0[0]):
            out.append((i, w0[:len(w0) - len(p0)]))
    return out


def acronym_key(original: str) -> str | None:
    """An all-capitals Latin acronym, dots allowed ("PM", "P.M.", "UX") -> its lower-case letters, else None."""
    letters = (original or "").replace(".", "").strip()
    if 2 <= len(letters) <= 6 and letters.isascii() and letters.isalpha() and letters.isupper():
        return letters.lower()
    return None


# s1-5.2.2 JD-defined abbreviations: tokens of the ORIGINAL text (parentheses kept, case kept)
_DEF_TOKEN = re.compile(r"[()]|(?<!\w)[^\W\d_](?:\.[^\W\d_](?!\w))+\.?|\w+")


@dataclass(frozen=True)
class AbbreviationDefinition:
    """A local "Full Form (ACR)" / "ACR (Full Form)" construction; offsets are into the analysed text."""
    acronym: str                       # canonical key ("pm")
    full_words: tuple[str, ...]        # canonical words of the full form
    acronym_text: str
    full_text: str
    text: str                          # the whole construction, parentheses included
    start: int
    end: int
    acronym_start: int
    acronym_end: int
    full_start: int
    full_end: int

    def to_dict(self) -> dict:
        return {"text": self.text, "acronym": self.acronym_text, "full_form": self.full_text}


def _is_word(tok: tuple) -> bool:
    return tok[0] not in "()"


def _capitalised(tok: tuple | None) -> bool:
    return tok is not None and _is_word(tok) and tok[0][:1].isupper()


def abbreviation_definitions(text: str) -> list[AbbreviationDefinition]:
    """Strict, local, dictionary-free detection. A construction counts only when:
    - the parentheses are top level and contain no other parentheses;
    - "Full Form (ACR)": exactly one acronym token inside; the full form is the n alphabetic words right before
      "(" with n = len(acronym), all capitalised (a bounded title run), and their strict initials (one per
      word) equal the acronym letters;
    - "ACR (Full Form)": exactly n alphabetic words inside, strict initials equal the acronym right before "(";
    - no capitalised word directly before or after the construction (a title qualifier such as "Senior" would
      make the defined phrase only part of the title).
    Anything else (several acronyms, nested or unbalanced parentheses, mixed tokens) is not a definition."""
    toks = [(m.group(), m.start(), m.end()) for m in _DEF_TOKEN.finditer(text or "")]
    out: list[AbbreviationDefinition] = []
    depth = 0
    for i, tok in enumerate(toks):
        if tok[0] == ")":
            depth = max(0, depth - 1)
            continue
        if tok[0] != "(":
            continue
        top, depth = depth == 0, depth + 1
        j = i + 1
        while j < len(toks) and _is_word(toks[j]):
            j += 1
        if not (top and j < len(toks) and toks[j][0] == ")" and j > i + 1):
            continue
        inner = toks[i + 1:j]
        after = toks[j + 1] if j + 1 < len(toks) else None
        if len(inner) == 1 and acronym_key(inner[0][0]):                    # Full Form (ACR)
            key = acronym_key(inner[0][0])
            n = len(key)
            full = toks[i - n:i] if i >= n else []
            before = toks[i - n - 1] if i - n - 1 >= 0 else None
            acr, first, last = inner[0], full[0] if full else None, full[-1] if full else None
            if not all(_capitalised(t) for t in full):
                continue            # outside parentheses only a capitalised (title) run is a bounded full form
        elif i >= 1 and _is_word(toks[i - 1]) and acronym_key(toks[i - 1][0]):  # ACR (Full Form)
            key = acronym_key(toks[i - 1][0])
            full = inner
            before = toks[i - 2] if i >= 2 else None
            acr, first, last = toks[i - 1], inner[0], inner[-1]
        else:
            continue
        if not (len(full) == len(key) and all(_is_word(t) and t[0].isalpha() for t in full)
                and "".join(t[0][0].lower() for t in full) == key):
            continue
        if _capitalised(before) or _capitalised(after):
            continue
        start = min(acr[1], first[1])
        end = max(toks[j][2], acr[2])
        out.append(AbbreviationDefinition(
            key, tuple(words(text[first[1]:last[2]])), acr[0], text[first[1]:last[2]], text[start:end], start, end,
            acr[1], acr[2], first[1], last[2]))
    return out


def exact_definition(text: str) -> AbbreviationDefinition | None:
    """The single definition construction that makes up the whole of ``text`` (edges: whitespace/punctuation)."""
    defs = abbreviation_definitions(text)
    if len(defs) != 1:
        return None
    d = defs[0]
    edge = re.compile(r"[\s.,;:]*")
    if edge.fullmatch(text[:d.start]) and edge.fullmatch(text[d.end:]):
        return d
    return None


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
