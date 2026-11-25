"""Reads the AI's interpretation of how a requirement combines (options, parts, examples, ambiguity, merged statements) and checks it
against the job description. Offline: no model, no database. Nothing here names a tool, a qualification, an industry or a language
beyond the connective and exception words of the grammar itself.

What code does:
  * normalizes the AI's `logic` block (services/requirements_v2/logic.py): ids are assigned here, unusable parts are dropped and reported
  * an option or part whose words are not found in the job description is NOT accepted as an automatic alternative: it is demoted to
    "review" (a person decides) and reported. An invented alternative can never satisfy a requirement by itself.
  * `also_stated_in` statements must be found in the job description. When one of them states a different number, or carries an exception
    or condition the first statement does not, the merge may have lost a distinct obligation: the item keeps every statement and gets an
    ambiguity note, so the recruiter verifies it. Nothing is split, dropped or merged silently.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from services.requirements_v2.extraction.text import locate_span, normalize, numbers_in
from services.requirements_v2.logic import ACCEPT_REVIEW, normalize as normalize_logic

COVERAGE_MIN = 0.75
MAX_ALSO = 5
_WORD = re.compile(r"\w+", re.UNICODE)
# Grammar of exceptions and conditions (connective words, not subject matter).
_CONDITION_RE = re.compile(
    r"(?<![^\W_])(?:unless|except|excluding|only if|provided that|subject to|but not|other than|as long as)(?![^\W_])"
    r"|(?<!\S)(?:إلا|باستثناء|ما لم|بشرط|عدا|شريطة)(?!\S)", re.IGNORECASE)


_AR_PREFIXES = ("وبال", "وال", "بال", "كال", "فال", "لل", "ال")


def _stem(word: str) -> str:
    """The word without an Arabic definite article or conjunction prefix, or a final English plural s: wording changes that do not change what is meant."""
    for prefix in _AR_PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= 3:
            return word[len(prefix):]
    return word[:-1] if len(word) > 4 and word.endswith("s") and word.isascii() else word


def _tokens(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(normalize(text)) if len(w) >= 3]


def _covered(text: str, window_tokens: set[str]) -> bool:
    words = _tokens(text)
    if not words:
        return True
    return sum(1 for w in words if w in window_tokens) / len(words) >= COVERAGE_MIN


def convert_logic(raw: Any, jd: str, source: str | None, span: tuple[int, int] | None,
                  note: Callable[..., None], item_id: str) -> dict | None:
    """The validated logic block for one item, or None. `note(code, message, item_id, prefix)` records recruiter-visible review issues."""
    if raw is None:
        return None
    logic, notes = normalize_logic(raw)
    for message in notes:
        note("logic_adjusted", message, item_id)
    if logic is None:
        return None
    # The window the options must come from: the sentence of the source wording, widened to the whole span; with no located source, the job description.
    window = _tokens(jd[max(0, span[0] - 200):span[1] + 200]) if span else _tokens(jd)
    window_set = set(window)
    for option in logic["options"] or []:
        texts = list(option["all_of"] or [option["text"]])
        if not all(_covered(t, window_set) for t in texts):
            if option["acceptance"] != ACCEPT_REVIEW:
                option["acceptance"] = ACCEPT_REVIEW
            note("logic_option_not_in_job_description",
                 f"the option \"{option['text'][:80]}\" is not worded in the job description; it will be accepted only after a person decides",
                 item_id, prefix="Needs review: ")
    if logic["ambiguity"]:
        note("requirement_ambiguous", f"the AI flagged an ambiguity: {logic['ambiguity']['note'][:160]}", item_id, prefix="Needs review: ")
    return logic


def convert_also(raw: Any, jd: str, source: str | None, note: Callable[..., None], item_id: str) -> tuple[list[str] | None, str | None]:
    """(the located additional statements, an ambiguity note when the merge may have lost something). Statements not found in the job
    description are dropped and reported."""
    if raw is None:
        return None, None
    if not isinstance(raw, list):
        note("also_stated_in_invalid", "also_stated_in must be a list of exact statements; ignored", item_id)
        return None, None
    found: list[str] = []
    for claimed in raw[:MAX_ALSO]:
        span = locate_span(jd, claimed) if isinstance(claimed, str) and claimed.strip() else None
        if span is None:
            note("also_source_not_found", f"a merged statement was not found in the job description: \"{str(claimed)[:80]}\"", item_id)
            continue
        text = jd[span[0]:span[1]]
        if text != source and text not in found:
            found.append(text)
    if not found:
        return None, None
    primary = source or ""
    risks: list[str] = []
    for text in found:
        if numbers_in(text) != numbers_in(primary):
            risks.append("a different number or threshold")
        if bool(_CONDITION_RE.search(text)) != bool(_CONDITION_RE.search(primary)):
            risks.append("an exception or condition")
    if not risks:
        return found, None
    reason = " and ".join(sorted(set(risks)))
    return found, f"Statements merged into this criterion differ in {reason}; verify that no distinct obligation was lost."
