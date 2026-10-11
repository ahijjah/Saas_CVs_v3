"""
Strict parsing and deterministic validation of a candidate_qc-1 answer.

  parse_response(raw)          -> (dict, None) | (None, error)      structure only
  validate_response(raw, jd)   -> (QualifyingContext, None, ()) | (None, error, ungrounded)

Parse rules (same codes and verdicts as the frozen evaluator's parse_qc, scripts/qc_context_eval.py):
  None -> no_response; not JSON -> invalid_json; not an object -> not_an_object; keys != {state, contexts,
  source} -> bad_keys; state not in STATES -> bad_state; source != "analysis" -> bad_source; contexts not a
  list of strings that each contain at least one word -> bad_contexts; two contexts with the same canonical
  words -> duplicate_contexts.
  One intentional, stricter-only difference: the evaluator also accepts a full-analysis wrapper
  {"experience": {"qualifying_context": {...}}}; production accepts ONLY the bare three-key object (the wrapper
  has unknown top-level keys -> bad_keys).
Validation rules (the evaluator's consistency_ok / grounding_ok checks, turned into rejections):
  identified without contexts, or none with contexts -> inconsistent_state;
  any context (also an uncertain candidate) not a verbatim, word-bounded JD phrase -> ungrounded_context.
Grounding is jd_text.JDText.find (NFKC + casefold + whitespace collapse, word boundaries, an Arabic proclitic
chain allowed before the first word only); no fuzzy matching, no paraphrase, no translation.
Nothing here ever converts a failure into "none", "uncertain" or an empty successful result.
"""
from __future__ import annotations

import json

from services.qualifying_context.schema import (
    ERR_BAD_CONTEXTS, ERR_BAD_KEYS, ERR_BAD_SOURCE, ERR_BAD_STATE, ERR_DUPLICATE_CONTEXTS, ERR_INCONSISTENT_STATE,
    ERR_INVALID_JSON, ERR_NO_RESPONSE, ERR_NOT_AN_OBJECT, ERR_UNGROUNDED_CONTEXT, QC_KEYS, SOURCE_ANALYSIS,
    STATE_IDENTIFIED, STATE_NONE, STATES, QualifyingContext,
)
from services.s1_requirements.jd_text import JDText, words


def parse_response(raw: str | None) -> tuple[dict | None, str | None]:
    if raw is None:
        return None, ERR_NO_RESPONSE
    if not isinstance(raw, str):
        return None, ERR_INVALID_JSON
    try:
        obj = json.loads(raw)
    except ValueError:
        return None, ERR_INVALID_JSON
    if not isinstance(obj, dict):
        return None, ERR_NOT_AN_OBJECT
    if set(obj) != QC_KEYS:
        return None, ERR_BAD_KEYS
    if obj["state"] not in STATES:
        return None, ERR_BAD_STATE
    if obj["source"] != SOURCE_ANALYSIS:
        return None, ERR_BAD_SOURCE
    ctx = obj["contexts"]
    if not isinstance(ctx, list) or not all(isinstance(c, str) and words(c) for c in ctx):
        return None, ERR_BAD_CONTEXTS
    keys = [tuple(words(c)) for c in ctx]
    if len(set(keys)) != len(keys):
        return None, ERR_DUPLICATE_CONTEXTS
    return {"state": obj["state"], "contexts": list(ctx), "source": SOURCE_ANALYSIS}, None


def consistent(state: str, contexts: list[str]) -> bool:
    if state == STATE_IDENTIFIED:
        return len(contexts) >= 1
    if state == STATE_NONE:
        return not contexts
    return True                                   # uncertain: candidates optional


def grounded(jd: JDText, phrase: str) -> bool:
    """Verbatim (comparison-form, word-bounded, Arabic proclitic allowed on the left) occurrence in the JD."""
    return bool(words(phrase)) and bool(jd.find(phrase))


def validate_response(raw: str | None, jd_text: str) -> tuple[QualifyingContext | None, str | None, tuple[str, ...]]:
    """-> (QualifyingContext, None, ()) when acceptable, else (None, error, ungrounded contexts)."""
    obj, err = parse_response(raw)
    if err is not None:
        return None, err, ()
    if not consistent(obj["state"], obj["contexts"]):
        return None, ERR_INCONSISTENT_STATE, ()
    jd = JDText(jd_text)
    ungrounded = tuple(c for c in obj["contexts"] if not grounded(jd, c))
    if ungrounded:
        return None, ERR_UNGROUNDED_CONTEXT, ungrounded
    return QualifyingContext(obj["state"], tuple(obj["contexts"]), obj["source"]), None, ()
