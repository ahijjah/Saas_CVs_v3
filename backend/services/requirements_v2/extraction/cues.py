"""
The job description's own optional / advantage wording, and the check that a requirement is explicitly marked with it.

PREFERRED_RE      the approved vocabulary (English words match as whole words, Arabic as written). A cue joined to another word by a
                  hyphen ("non-preferred") is part of that word, not a statement.
jd_marks_preferred(jd, span)
                  True when the job description itself marks the requirement whose wording occupies `span` as preferred, with no
                  help from the model: the cue is in the requirement's own sentence (before OR after the quoted words), or in the
                  nearest heading above it, and that sentence does not also say the requirement is required. Anything less is not
                  established, so the caller keeps its fail-closed warning.
"""
from __future__ import annotations

import re

from services.requirements_v2.extraction.text import heading_above, normalize

_PREFERRED_EN = (r"preferred", r"preferably", r"desirable", r"nice[- ]to[- ]have", r"(?:a|an|is|are|big|major) plus",
                 r"(?:an|is|are) advantage", r"advantageous", r"ideally", r"(?:an|is|are) asset", r"(?:a|is) bonus",
                 r"optional")
_PREFERRED_AR = ("يفضل", "مفضل", "تفضل", "ميزة", "أفضلية", "افضلية", "مرغوب", "اختيارية")
PREFERRED_RE = re.compile(r"(?<![^\W_]|-)(?:" + "|".join(_PREFERRED_EN) + r")(?![^\W_])|" + "|".join(_PREFERRED_AR))

# Wording that makes a requirement mandatory. In the same sentence as an optional cue it makes the classification ambiguous, unless
# it is negated ("preferred but not required").
_REQUIRED_EN = (r"required", r"requires?", r"must", r"mandatory", r"essential", r"compulsory", r"necessary")
_REQUIRED_AR = ("يجب", "إلزامي", "الزامي", "ضروري", "لازم", "مطلوب")
REQUIRED_RE = re.compile(r"(?<![^\W_])(?:" + "|".join(_REQUIRED_EN) + r")(?![^\W_])|" + "|".join(_REQUIRED_AR))
_NEGATED_REQUIRED_RE = re.compile(
    r"(?<![^\W_])(?:not|isn't|aren't|never|non)[- ]+(?:be\s+)?(?:required|mandatory|essential|necessary|compulsory)(?![^\W_])"
    r"|(?<![^\W_])(?:is|are)\s+not\s+(?:required|mandatory|essential|necessary|compulsory)(?![^\W_])")

_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?؟。])\s+")


def sentence_around(jd_text: str, span: tuple[int, int]) -> str:
    """The sentence of the job description that holds `span`, on its own line: words before and after the span both belong to it."""
    start, end = span
    line_start = jd_text.rfind("\n", 0, start) + 1
    line_end = jd_text.find("\n", end)
    line = jd_text[line_start:len(jd_text) if line_end < 0 else line_end]
    low, high = start - line_start, end - line_start
    first, last = 0, len(line)
    for match in _SENTENCE_BREAK_RE.finditer(line):
        if match.end() <= low:
            first = match.end()
        elif match.start() >= high:
            last = match.start()
            break
    return line[first:last]


def states_required(folded: str) -> bool:
    return bool(REQUIRED_RE.search(_NEGATED_REQUIRED_RE.sub(" ", folded)))


def jd_marks_preferred(jd_text: str, span: tuple[int, int] | None) -> bool:
    if span is None:
        return False
    sentence = normalize(sentence_around(jd_text, span))
    if states_required(sentence):
        return False
    if PREFERRED_RE.search(sentence):
        return True
    heading = heading_above(jd_text, span)
    return heading is not None and bool(PREFERRED_RE.search(normalize(heading)))
