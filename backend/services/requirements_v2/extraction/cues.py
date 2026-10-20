"""
The job description's own optional / advantage wording, and the check that it is bound to ONE specific requirement.

PREFERRED_RE      the approved vocabulary (English words match as whole words, Arabic as written). A cue joined to another word by a
                  hyphen ("non-preferred") is part of that word, not a statement.
jd_marks_preferred(jd, span)
                  True only when the optional wording can be tied to the requirement whose words occupy `span` by structure, not by
                  guessing. A sentence is cut into clauses only where BOTH sides of a separator (";", a comma, a dash,
                  "and / or / but / while / whereas / however", Arabic equivalents) already carry their own optional or required
                  wording ("Python is required; Docker is preferred", "Python is required and Docker is optional"). The requirement's clause must not say it is required, and then
                  the cue binds when it is
                    - inside the quoted words, with no list comma left in them ("Docker is a plus");
                    - at the start of the clause, ahead of the requirement ("Preferably Docker, Kubernetes"; "Preferred: Docker");
                    - after it, in a clause that is not a comma list ("ITIL certification is preferred", "Python and Docker are preferred");
                  or the nearest heading above the requirement carries the cue ("Preferred qualifications:").
                  Only the job description's own approved wording counts. A cue the model names (even a phrase from the requirement
                  itself) establishes nothing; the parser keeps it only to explain a warning.
                  Anything else, for example a cue at the end of a comma list ("Python, Docker is a plus"), is not reliably tied to one
                  item, so the caller keeps its fail-closed warning and the recruiter decides.
"""

from __future__ import annotations

import re

from services.requirements_v2.extraction.text import heading_above, normalize, normalize_with_map

_PREFERRED_EN = (r"preferred", r"preferably", r"desirable", r"nice[- ]to[- ]have", r"(?:a|an|is|are|big|major) plus",
                 r"(?:an|is|are) advantage", r"advantageous", r"ideally", r"(?:an|is|are) asset", r"(?:a|is) bonus",
                 r"optional")
_PREFERRED_AR = ("يفضل", "مفضل", "تفضل", "ميزة", "أفضلية", "افضلية", "مرغوب", "اختياري")
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


def _sentence_bounds(jd_text: str, span: tuple[int, int]) -> tuple[int, int]:
    """(start, end) in the job description of the sentence that holds `span`, on its own line."""
    start, end = span
    line_start = jd_text.rfind("\n", 0, start) + 1
    line_end = jd_text.find("\n", end)
    line_end = len(jd_text) if line_end < 0 else line_end
    line = jd_text[line_start:line_end]
    low, high = start - line_start, end - line_start
    first, last = 0, len(line)
    for match in _SENTENCE_BREAK_RE.finditer(line):
        if match.end() <= low:
            first = match.end()
        elif match.start() >= high:
            last = match.start()
            break
    return line_start + first, line_start + last


def sentence_around(jd_text: str, span: tuple[int, int]) -> str:
    """The sentence of the job description that holds `span`, on its own line."""
    first, last = _sentence_bounds(jd_text, span)
    return jd_text[first:last]


def states_required(folded: str) -> bool:
    return bool(REQUIRED_RE.search(_NEGATED_REQUIRED_RE.sub(" ", folded)))


_LIST_COMMA_RE = re.compile(r"[,،;؛]")
# Where a sentence may be cut into clauses. A cut is made only when BOTH sides already carry their own optional or required wording
# ("Python is required; Docker is preferred", "Python is required and Docker is optional"). Otherwise the separator is just a list
# separator ("Python, Docker is a plus") or part of one requirement ("preferred but not required"), and nothing is cut.
_SEPARATOR_RE = re.compile(
    r"[,،;؛]|—|–|(?<![^\W_])(?:and|or|but|while|whereas|however)(?![^\W_])|(?<!\S)(?:و|أو|لكن|بينما)(?!\S)")


def _has_marker(folded: str) -> bool:
    return bool(PREFERRED_RE.search(folded)) or states_required(folded)


def _clauses(folded: str) -> list[tuple[int, int]]:
    """The clauses of one folded sentence (see the module docstring for where it is cut)."""
    clauses: list[tuple[int, int]] = []
    cut = 0
    for match in _SEPARATOR_RE.finditer(folded):
        if _has_marker(folded[cut:match.start()]) and _has_marker(folded[match.end():]):
            clauses.append((cut, match.start()))
            cut = match.end()
    clauses.append((cut, len(folded)))
    return clauses


def _only_punctuation(text: str) -> bool:
    return not re.search(r"[^\W_]", text)


def jd_marks_preferred(jd_text: str, span: tuple[int, int] | None) -> bool:
    if span is None:
        return False
    first, last = _sentence_bounds(jd_text, span)
    folded, origin = normalize_with_map(jd_text[first:last])
    low, high = span[0] - first, span[1] - first
    n_lo = next((i for i, o in enumerate(origin) if o >= low), len(folded))
    n_hi = max((i + 1 for i, o in enumerate(origin) if o < high), default=0)
    if n_hi <= n_lo:
        return False
    owners = [(a, b) for a, b in _clauses(folded) if a < n_hi and b > n_lo]
    if len(owners) != 1:                                    # the quoted words run across clauses: which one is meant is not clear
        return False
    start, end = owners[0]
    if states_required(folded[start:end]):
        return False
    for a, b in ((m.start(), m.end()) for m in PREFERRED_RE.finditer(folded) if m.start() >= start and m.end() <= end):
        if a < n_hi and b > n_lo:                           # inside the quoted words
            quoted_without_cue = folded[n_lo:max(a, n_lo)] + folded[min(b, n_hi):n_hi]
            if not _LIST_COMMA_RE.search(quoted_without_cue):
                return True
        elif b <= n_lo:                                     # ahead of the requirement: only when the clause begins with the cue
            if _only_punctuation(folded[start:a]):
                return True
        elif not _LIST_COMMA_RE.search(folded[start:a] + folded[b:end]):   # after it: only when the clause is not a comma list
            return True
    heading = heading_above(jd_text, span)
    return heading is not None and bool(PREFERRED_RE.search(normalize(heading)))
