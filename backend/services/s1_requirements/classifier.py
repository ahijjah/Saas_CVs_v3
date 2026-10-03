"""
S1 classifier — ONE AI call per job labels every enumerated experience
criterion; deterministic validation and assembly do the rest. SHADOW ONLY.

  classify_job(job_id, jd_text, analysis_json, *, recruiter_fields=None,
               client=None, model=S1_MODEL, cache=None) -> S1JobResult

Input to the model (no candidate data, no job title, no domain_knowledge):
  numbered non-blank JD lines, duration candidates D1.. parsed from the JD,
  and per criterion: criterion_id, display_text, has_years, target hints T1..
  (the analysis_json relevant_roles feeding it).

Call flow (all-or-nothing per job; technical failures are never business states):
  no experience criteria             -> no call, no artifacts
  request over the input budget      -> failed_technical exceeds_model_context (cached)
  main call exception                -> failed_technical ai_unavailable (retryable; not cached)
  finish_reason == "length"          -> failed_technical output_truncated (not cached)
  invalid -> ONE repair call with the exact violations:
      over budget / exception / length -> failed_technical (as above)
      still invalid                    -> failed_validation validation_failed (cached)
  valid                              -> per criterion resolved | needs_confirmation
  assembly invariant broken          -> failed_technical internal_error (retryable; not cached)

The cache stores the VALIDATED AI labels (or the cacheable failure), keyed by
the exact request; recruiter provenance is applied at assembly time, so it is
never part of the cached AI output.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from services.ai_service import _SECURITY_HARDENING_SUFFIX
from services.s0_experience import llm_call
from services.s1_requirements.assemble import assemble_artifact, check_recruiter_fields, failed_artifact
from services.s1_requirements.criteria import CriterionInput, enumerate_experience_criteria, out_of_scope_items
from services.s1_requirements.durations import DurationMatch
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.schema import (
    REASON_AI_UNAVAILABLE, REASON_EXCEEDS_MODEL_CONTEXT, REASON_INTERNAL_ERROR, REASON_OUTPUT_TRUNCATED,
    REASON_VALIDATION_FAILED, S1_INPUT_VERSION, S1_MAX_TOKENS, S1_MODEL, S1_PROMPT_CODE, S1_PROMPT_VERSION,
    S1_TEMPERATURE, S1_VERSION, S1Artifact, canonical, sha256,
)
from services.s1_requirements.validator import ParsedCriterion, hint_ids, validate_response

logger = logging.getLogger(__name__)

S1_MAX_INPUT_TOKENS = llm_call.MAX_INPUT_TOKENS
CACHEABLE = frozenset({None, REASON_VALIDATION_FAILED, REASON_EXCEEDS_MODEL_CONTEXT})

S1_SYSTEM_PROMPT = """You label the EXPERIENCE requirements of ONE job description (JD). You never see or judge candidates.

INPUT (JSON): "jd_lines" (line number + verbatim text), "duration_candidates" (id, line, text: every duration found in the JD) and "criteria" (criterion_id, display_text, has_years, target_hints with ids T1, T2, ...). target_hints come from an earlier automatic extraction: they are fixed, and they are not proof that the JD says them.

For EVERY criterion return exactly one result.

1 REQUIREMENT_SPANS: the JD sentence(s) or bullet(s) that state THIS experience requirement, as [{"line": n, "text": "verbatim"}]. Quote the whole requirement statement; if it continues on the next line, quote each line. Never quote company descriptions, "about us", duties/responsibilities, other requirements (education, skills, licences, languages) or headings. If the JD does not state this requirement, return [] and report "requirement_not_in_jd".
  Mark a span {"line": n, "text": "verbatim", "experience_requirement": true} ONLY when the quoted text itself states this experience requirement but contains neither its duration, nor a hint word for word, nor a phrase you map (e.g. "Experience in nursing is preferred."). Never mark company descriptions or context (e.g. "We are a leading bank."). Such a criterion stays unconfirmed: never add a jd_span just to anchor a span.

2 TARGETS
  a. Criterion WITH target_hints: return every hint exactly once as {"hint": "T1", "type": "role" | "function", "jd_span": null | {"line": n, "text": "verbatim"}}. Never drop, rename, merge, split, translate or add targets.
  b. type "role": something a person can BE, a position title (Project Manager, Civil Engineer, Accountant, Assistant Project Manager). type "function": something a person DOES or works IN, a kind of work, discipline or field (project management, procurement, software implementation, accounting). Being supplied as a hint, or stored as a "role", never makes a target a role. If the type cannot be decided, use "function" and report "ambiguous_relevance".
  c. Criterion WITHOUT target_hints: select targets only as verbatim text inside its requirement_spans, {"line": n, "text": "verbatim", "type": ...}, and only when the requirement names a role or function.

3 JD_SPAN (target mapping), for each hint:
  - A mapping means: the hint and a JD phrase name SUBSTANTIALLY THE SAME required role or function, and differ ONLY in wording, language or form (translation, abbreviation vs full form, noun vs verb form). It does NOT mean related, similar, compatible, adjacent, same family, narrower or broader.
  - If the hint appears word for word inside this criterion's requirement_spans as the complete role/function, set jd_span = null (it is matched automatically).
  - If the hint appears word for word only as part of a longer phrase that adds a material qualifier (e.g. "Project Manager" inside "Assistant Project Manager", "software implementation" inside "enterprise software implementation"), set jd_span = null and report "ambiguous_relevance".
  - Otherwise you MAY set jd_span to the verbatim phrase inside THIS criterion's requirement_spans that names the same role or function. The phrase must keep EVERY qualifier that the JD attaches to that role/function: copy the complete phrase, never a shorter part that drops a qualifier (e.g. never "مدير مشروع" when the JD says "مدير مشروع إنشائي"). Copy whole words exactly as written, including attached letters (e.g. "كمدير مشروع إنشائي").
  - A material qualifier is any word that adds, removes or changes: sector, industry, technology or platform, project type, role level or seniority (assistant, senior, junior, lead, head), professional specialisation, functional scope (coordination vs management, support vs administration) or environment/context. If the hint and the JD phrase differ in ANY material qualifier, in either direction, set jd_span = null.
  - Never map to: working with, reporting to, supporting or being supervised by the target; the same employer, sector, project or industry; an adjacent profession; a transferable skill; general relevance; the duration; the setting; or text outside this criterion's requirement_spans.
  - An abbreviation maps only when the requirement span itself makes its meaning unambiguous.
  - A phrase may be mapped by at most one hint; if two hints seem to match the same phrase, return null for both.
  - If you are not certain the meaning is the same, return null. null is always acceptable; a wrong mapping is not.
  - A mapping never changes the target: the hint text stays exactly as supplied.

4 POLICY
  explicit_role  every target is a role: the candidate must have HELD one of the named positions.
  functional     every target is a function: the candidate must have PERFORMED the named work, whatever the title (e.g. "experience managing construction projects" is functional: no position is named).
  mixed          the alternatives include at least one role and at least one function.
  sector         (no target_hints only) the requirement names only an industry/sector/setting, no role or function.
  pure_duration  (no target_hints only) total professional experience with no restriction. If the JD says "relevant", "related", "similar" or "in the field" without naming what, use pure_duration AND report "ambiguous_relevance".
  For criteria with target_hints the policy follows from the target types; sector and pure_duration are never used for them.

5 SETTING: null, or the shortest complete verbatim phrase INSIDE this criterion's requirement_spans that restricts WHERE the experience must have been gained (industry, sector, project type or environment, e.g. "oil and gas sector", "commercial construction projects", "hospital"). Never take it from other JD text or the job title. Never use the hiring company's name, a location, seniority, tools or generic adjectives ("dynamic", "fast-paced", "multinational"). Do not extract a setting that is already part of a target (e.g. "Construction" in "Construction Project Manager"). If the setting does not clearly apply to EVERY alternative of the criterion, return null and report "ambiguous_relevance". sector needs a setting; pure_duration takes none.

6 DURATION: null, or the id of the duration candidate inside this criterion's requirement_spans that states its minimum experience. Never compute or restate a number. If more than one candidate could be this criterion's minimum, return null and report "multiple_durations". Only for has_years criteria.

7 AMBIGUITY: [] or any of:
  "ambiguous_relevance"       what counts as relevant, a target's type, a target embedded in a longer qualified phrase, or the setting's scope is unclear;
  "multiple_durations"        more than one duration could be this criterion's minimum;
  "conflicting_requirements"  the JD states this requirement differently in different places, or names other roles/functions than the hints;
  "requirement_not_in_jd"     the JD does not state this requirement.
  Report ambiguity instead of guessing; never broaden a requirement.

8 NOTE: one short sentence; for every non-null jd_span state that the only difference is wording, language or form.

OUTPUT: JSON only:
{"criteria": [{"criterion_id": "...", "policy": "explicit_role",
  "requirement_spans": [{"line": 7, "text": "verbatim"}],
  "targets": [{"hint": "T1", "type": "role", "jd_span": null}],
  "setting": null, "duration": "D1", "ambiguity": [], "note": "one sentence"}]}""" + _SECURITY_HARDENING_SUFFIX


def prompt_fingerprint() -> str:
    return sha256(S1_SYSTEM_PROMPT)[:12]


class S1Cache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def set(self, key: str, value: dict) -> None: ...


class InMemoryS1Cache:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        return self.store.get(key)

    def set(self, key: str, value: dict) -> None:
        self.store[key] = value


@dataclass
class BuiltRequest:
    payload: dict
    jd: JDText
    criteria: list[CriterionInput]
    durations: dict[str, tuple[int, DurationMatch]]

    @property
    def user_message(self) -> str:
        return "INPUT:\n" + canonical(self.payload)

    @property
    def input_hash(self) -> str:
        return sha256(self.user_message)


def build_request(jd: JDText, criteria: list[CriterionInput]) -> BuiltRequest:
    durs = {did: (ln, m) for did, ln, m in jd.durations()}
    payload = {
        "s1_input_version": S1_INPUT_VERSION,
        "jd_lines": jd.numbered(),
        "duration_candidates": [{"id": did, "line": ln, "text": m.text} for did, (ln, m) in durs.items()],
        "criteria": [{"criterion_id": c.criterion_id, "display_text": c.display_text, "has_years": c.has_years,
                      "target_hints": [{"id": hid, "text": t} for hid, t in hint_ids(c).items()]}
                     for c in criteria],
    }
    return BuiltRequest(payload, jd, criteria, durs)


def s1_cache_key(req: BuiltRequest, *, model: str = S1_MODEL) -> str:
    return sha256(f"s1|{S1_VERSION}|{req.input_hash}|{S1_PROMPT_VERSION}:{prompt_fingerprint()}|{model}")


def repair_note(errors: list[str]) -> str:
    return ("Your previous JSON violates these rules. Return the COMPLETE corrected JSON with exactly one "
            "result for every criterion, fixing every violation and changing nothing else. Quote JD text "
            "verbatim and never add, drop or rewrite targets. A jd_span is only for the SAME role/function in "
            "different wording; when in doubt set it to null. Quote only the criterion's own requirement "
            "statement:\n- " + "\n- ".join(errors[:40]))


@dataclass
class S1JobResult:
    job_id: str
    status: str                     # "ok" | "failed" | "empty"
    status_reason: str | None
    artifacts: list[S1Artifact] = field(default_factory=list)
    out_of_scope: list[dict] = field(default_factory=list)
    cache_key: str | None = None
    meta: dict = field(default_factory=dict)
    validation: dict = field(default_factory=lambda: {"errors": [], "repair_errors": []})


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = llm_call.create_client()
    return _client


def _serialise_parsed(results: dict[str, ParsedCriterion]) -> dict:
    return {cid: {"policy": p.policy, "requirement_spans": [s.to_dict() for s in p.requirement_spans],
                  "targets": [{"text": t.text, "type": t.type, "hint_id": t.hint_id,
                               "span": t.span.to_dict() if t.span else None} for t in p.targets],
                  "setting": p.setting.to_dict() if p.setting else None, "duration": p.duration_id,
                  "ambiguity": list(p.ambiguity), "note": p.note,
                  "statement_anchored": p.statement_anchored} for cid, p in results.items()}


def _deserialise_parsed(d: dict) -> dict[str, ParsedCriterion]:
    from services.s1_requirements.schema import Span
    from services.s1_requirements.validator import ParsedTarget
    return {cid: ParsedCriterion(
        cid, p["policy"], tuple(Span.from_dict(s) for s in p["requirement_spans"]),
        tuple(ParsedTarget(t["text"], t["type"], t.get("hint_id"), Span.from_dict(t.get("span")))
              for t in p["targets"]),
        Span.from_dict(p.get("setting")), p.get("duration"), tuple(p.get("ambiguity") or ()), p.get("note", ""),
        bool(p.get("statement_anchored")))
        for cid, p in d.items()}


async def classify_job(job_id: str, jd_text: str, analysis_json: dict | None, *,
                       recruiter_fields: Mapping[str, str] | None = None, client: Any = None,
                       model: str = S1_MODEL, cache: S1Cache | None = None) -> S1JobResult:
    rf = check_recruiter_fields(recruiter_fields)
    criteria = enumerate_experience_criteria(job_id, analysis_json)
    oos = out_of_scope_items(analysis_json)
    if not criteria:
        return S1JobResult(str(job_id), "empty", None, out_of_scope=oos)
    jd = JDText(jd_text)
    req = build_request(jd, criteria)
    key = s1_cache_key(req, model=model)
    meta: dict[str, Any] = {
        "prompt_code": S1_PROMPT_CODE, "prompt_version": S1_PROMPT_VERSION,
        "prompt_fingerprint": prompt_fingerprint(), "model": model, "temperature": S1_TEMPERATURE,
        "max_output_tokens": S1_MAX_TOKENS, "max_input_tokens": S1_MAX_INPUT_TOKENS,
        "token_count_method": llm_call.TOKEN_COUNT_METHOD, "input_hash": req.input_hash,
        "request_token_upper_bound": None, "repair_request_token_upper_bound": None,
        "calls": 0, "repair_used": False, "call_log": [], "cache_hit": False,
    }

    outcome = cache.get(key) if cache is not None else None
    if outcome is not None:
        meta = {**outcome["meta"], "cache_hit": True}
    else:
        outcome = await _run(req, client, model, meta)
        if cache is not None and outcome["reason"] in CACHEABLE:
            cache.set(key, copy.deepcopy(outcome))

    run = {"input_hash": req.input_hash, "prompt_version": S1_PROMPT_VERSION,
           "prompt_fingerprint": prompt_fingerprint(), "model": model,
           "call": {k: meta.get(k) for k in ("calls", "repair_used", "outcome", "cache_hit")}}
    validation = outcome["validation"]
    res = S1JobResult(str(job_id), "failed" if outcome["reason"] else "ok", outcome["reason"],
                      out_of_scope=oos, cache_key=key, meta=meta, validation=validation)
    if outcome["reason"]:
        res.artifacts = [failed_artifact(c, outcome["reason"], run=run, validation=validation,
                                         recruiter_fields=rf) for c in criteria]
        return res
    try:
        parsed = _deserialise_parsed(outcome["parsed"])
        res.artifacts = [assemble_artifact(c, parsed[c.criterion_id], jd, req.durations, run=run,
                                           recruiter_fields=rf, validation=validation) for c in criteria]
    except Exception as exc:                    # assembly invariant broken = bug, never a business state
        logger.exception("S1 assembly failed")
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        res.status, res.status_reason = "failed", REASON_INTERNAL_ERROR
        res.artifacts = [failed_artifact(c, REASON_INTERNAL_ERROR, run=run, validation=validation,
                                         recruiter_fields=rf) for c in criteria]
    return res


async def _run(req: BuiltRequest, client, model: str, meta: dict) -> dict:
    def _out(reason, validation=None, parsed=None):
        return {"reason": reason, "parsed": parsed, "meta": meta,
                "validation": validation or {"errors": [], "repair_errors": []}}

    messages = [{"role": "system", "content": S1_SYSTEM_PROMPT},
                {"role": "user", "content": req.user_message}]
    bound = llm_call.request_token_upper_bound(messages)
    meta["request_token_upper_bound"] = bound
    if bound > S1_MAX_INPUT_TOKENS:
        return _out(REASON_EXCEEDS_MODEL_CONTEXT)
    client = client or _get_client()
    first_errors: list[str] = []
    try:
        raw, finish = await llm_call.chat_json_call(
            client, model=model, messages=messages, temperature=S1_TEMPERATURE,
            max_tokens=S1_MAX_TOKENS, call_log=meta["call_log"], kind="main")
        meta["calls"] = 1
        if finish == "length":
            return _out(REASON_OUTPUT_TRUNCATED)
        val = validate_response(raw, req.jd, req.criteria, req.durations)
        outcome = "validated"
        if not val.ok:
            first_errors = list(val.errors)
            repair_messages = messages + [{"role": "assistant", "content": raw},
                                          {"role": "user", "content": repair_note(first_errors)}]
            rb = llm_call.request_token_upper_bound(repair_messages)
            meta["repair_request_token_upper_bound"] = rb
            if rb > S1_MAX_INPUT_TOKENS:
                return _out(REASON_EXCEEDS_MODEL_CONTEXT, {"errors": first_errors, "repair_errors": []})
            meta["repair_used"] = True
            raw, finish = await llm_call.chat_json_call(
                client, model=model, messages=repair_messages, temperature=S1_TEMPERATURE,
                max_tokens=S1_MAX_TOKENS, call_log=meta["call_log"], kind="repair")
            meta["calls"] = 2
            if finish == "length":
                return _out(REASON_OUTPUT_TRUNCATED, {"errors": first_errors, "repair_errors": []})
            val = validate_response(raw, req.jd, req.criteria, req.durations)
            outcome = "repaired"
            if not val.ok:
                return _out(REASON_VALIDATION_FAILED, {"errors": first_errors, "repair_errors": list(val.errors)})
    except Exception as exc:                    # network / API / auth / timeout
        logger.warning("S1 classifier unavailable: %s", exc)
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return _out(REASON_AI_UNAVAILABLE, {"errors": first_errors, "repair_errors": []})
    meta["outcome"] = outcome
    return _out(None, {"errors": first_errors, "repair_errors": []}, _serialise_parsed(val.results))


__all__ = ["classify_job", "build_request", "S1JobResult", "InMemoryS1Cache", "s1_cache_key",
           "S1_SYSTEM_PROMPT", "prompt_fingerprint", "repair_note"]
