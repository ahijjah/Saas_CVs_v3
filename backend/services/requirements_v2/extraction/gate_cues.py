"""
Explicit eligibility wording in the job description, and the check that it is bound to ONE requirement.

The extraction may SUGGEST that a Required requirement is a Mandatory Gate, and only when the job description's own words say so
explicitly: must, mandatory, required, minimum, essential, a valid licence, legally required (and the Arabic equivalents). A suggestion
enforces nothing: it is kept on the item (and in the stored original) so a person can accept or refuse it, and a gate is enforced only after an
admin or an HR manager confirms it (services/requirements_v2/gates.py).

Binding follows the same rule as the preferred-wording check (cues.py): the cue must sit in the requirement's own clause or in the
heading above it, never merely somewhere in the same paragraph, and a negated or optional statement ("not required", "preferred") never
suggests a gate. A requirement whose wording could not be found in the job description gets no suggestion.
"""
from __future__ import annotations

import re

from services.requirements_v2.extraction.cues import (
    PREFERRED_RE, _NEGATED_REQUIRED_RE, _clauses, _sentence_bounds,
)
from services.requirements_v2.extraction.text import heading_above, normalize, normalize_with_map

_GATE_EN = (r"must", r"mandatory", r"required", r"requires?", r"minimum", r"essential", r"compulsory",
            r"valid\s+(?:driving\s+|driver'?s\s+)?licen[cs]e", r"legally\s+(?:required|obliged|mandated)", r"legal\s+requirement")
_GATE_AR = ("يجب", "إلزامي", "الزامي", "ضروري", "لا بد", "الحد الأدنى", "الحد الادنى", "رخصة سارية", "ساري المفعول", "مطلوب قانونا", "مطلوب قانوناً")
GATE_RE = re.compile(r"(?<![^\W_])(?:" + "|".join(_GATE_EN) + r")(?![^\W_])|" + "|".join(_GATE_AR))

CUE_LIMIT = 80


def _first_cue(folded: str) -> tuple[int, int] | None:
    """The first explicit gate cue in a folded clause that is not negated."""
    cleaned = _NEGATED_REQUIRED_RE.sub(lambda m: " " * (m.end() - m.start()), folded)
    match = GATE_RE.search(cleaned)
    return (match.start(), match.end()) if match else None


def suggest_gate(jd_text: str, span: tuple[int, int] | None) -> dict | None:
    """{"suggested": True, "cue": <the job description's own words>, "source_text": None} when explicit gate wording is bound to the
    requirement that occupies `span` of the job description, else None. The caller supplies the item's source text."""
    if span is None or not isinstance(jd_text, str):
        return None
    first, last = _sentence_bounds(jd_text, span)
    folded, origin = normalize_with_map(jd_text[first:last])
    low, high = span[0] - first, span[1] - first
    n_lo = next((i for i, o in enumerate(origin) if o >= low), len(folded))
    n_hi = max((i + 1 for i, o in enumerate(origin) if o < high), default=0)
    if n_hi <= n_lo:
        return None
    owners = [(a, b) for a, b in _clauses(folded) if a < n_hi and b > n_lo]
    if len(owners) != 1:
        return None
    start, end = owners[0]
    clause = folded[start:end]
    if PREFERRED_RE.search(clause):                        # optional wording in the requirement's own clause: never a gate
        return None
    hit = _first_cue(clause)
    if hit is not None:
        # the job description's own characters, mapped back from the comparison form
        cue = jd_text[first + origin[start + hit[0]]:first + origin[start + hit[1] - 1] + 1]
    else:
        heading = heading_above(jd_text, span)
        heading_hit = _first_cue(normalize(heading)) if heading else None
        if heading is None or heading_hit is None or PREFERRED_RE.search(normalize(heading)):
            return None
        cue = normalize(heading)[heading_hit[0]:heading_hit[1]]
    return {"suggested": True, "cue": cue[:CUE_LIMIT], "source_text": None}
