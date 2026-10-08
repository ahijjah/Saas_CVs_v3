"""
PASS A runner: the model call of the experience TARGET pass (prompt s1a-1.1). SHADOW ONLY: nothing in production
imports it, and it never runs unless a caller passes (or lets it build) a client.

  build_pass_a_messages(req)      -> [system = pinned s1a-1.1, user = "INPUT:\n" + canonical payload]
  build_pass_a_call(req, model)   -> the full chat-completion arguments (pure; no client)
  await run_pass_a(jd, criteria, client=..., cache=...)          -> PassAOutcome
  await run_pass_a_job(job_id, jd_text, analysis_json, client=...) -> PassAJobResult (frozen targets | failures)

Call policy (the s1-5.2 orchestration, Pass A wire): one main call; if invalid, the deterministic
duplicate-representation narrowing, then at most ONE repair call whose answer is merged by the frozen scoped
merge (services.s1_requirements.repair.merge_repair: restrictions are never dropped, a non-empty list never
becomes [], material qualifiers are locked), then the same narrowing and the deterministic withdrawal of a lone
failing equivalent claim. Nothing else; no third call, no fallback model.

Pass A additions:
  target_basis   taken from the repair only where the repair was asked for it (scope target_basis), or where the
                 merge took the criterion / its restrictions from the repair (the basis must follow the list).
  F6             a repair is never the sole source of a no-target reading: after the merge, a criterion without
                 target hints whose basis is total_experience / setting_only / unspecified must carry the SAME basis
                 in the main answer, and a non-empty main restriction list never ends up []. Otherwise the job fails
                 validation (fail closed; never a no-target reading).
  repair note    built only from repair-safe messages (pass_a._repair_safe): no context vocabulary reaches the
                 model.
Failures follow the v3 reasons: ai_unavailable / output_truncated / exceeds_model_context / internal_error
(technical) and validation_failed (contract). A failure is never "no target" (F1, assemble.failed_v4).
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol

from services.s0_experience import llm_call
from services.s1_requirements.criteria import CriterionInput, enumerate_experience_criteria
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.repair import (
    apply_withdrawal, merge_repair, normalize_duplicate_representations, pair_repair_guidance, plan_withdrawal,
)
from services.s1_requirements.schema import (
    REASON_AI_UNAVAILABLE, REASON_EXCEEDS_MODEL_CONTEXT, REASON_INTERNAL_ERROR, REASON_OUTPUT_TRUNCATED,
    REASON_VALIDATION_FAILED,
)
from services.s1_requirements.validator import SCOPE_CRITERION, hint_ids
from services.s1_two_pass.assemble import FrozenTarget, failed_v4, freeze_targets, split_recruiter_fields
from services.s1_two_pass.pass_a import (
    SCOPE_TARGET_BASIS, PassARequest, PassAValidation, build_pass_a_request, pass_a_cache_key, validate_pass_a,
)
from services.s1_two_pass.prompt_a import S1A_PROMPT_SHA256, load_pass_a_prompt, pass_a_prompt_fingerprint
from services.s1_two_pass.schema import (
    ABSENT_BASES, BASIS_UNSPECIFIED, S1A_MAX_TOKENS, S1A_MODEL, S1A_PROMPT_CODE, S1A_PROMPT_VERSION, S1A_TEMPERATURE, S1ArtifactV4,
)

logger = logging.getLogger(__name__)

S1A_MAX_INPUT_TOKENS = llm_call.MAX_INPUT_TOKENS
CACHEABLE = frozenset({None, REASON_VALIDATION_FAILED, REASON_EXCEEDS_MODEL_CONTEXT})
F6_ERROR = "a repair is never the sole source of a no-target reading"
# F6 (strengthened after the s1a-1.0 MAIN forensics, CM09): every reading without a role/function target counts,
# unspecified included; a repair alone can never introduce any of them
NO_TARGET_BASES = ABSENT_BASES + (BASIS_UNSPECIFIED,)


class PassACache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def set(self, key: str, value: dict) -> None: ...


class InMemoryPassACache:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        return self.store.get(key)

    def set(self, key: str, value: dict) -> None:
        self.store[key] = value


def build_pass_a_messages(req: PassARequest) -> list[dict]:
    return [{"role": "system", "content": load_pass_a_prompt()}, {"role": "user", "content": req.user_message}]


def build_pass_a_call(req: PassARequest, model: str = S1A_MODEL) -> dict:
    return {"model": model, "messages": build_pass_a_messages(req), "temperature": S1A_TEMPERATURE,
            "max_tokens": S1A_MAX_TOKENS, "response_format": {"type": "json_object"}}


def pass_a_repair_note(errors: list[str], pair_guidance: list[str] = ()) -> str:
    pairs = ("\nPair-level repair:\n- " + "\n- ".join(pair_guidance)) if pair_guidance else ""
    return ("Some fields of your previous JSON violate the rules below. Return the COMPLETE JSON again with "
            "exactly one result for every criterion. ONLY the fields, targets and restrictions named in these "
            "errors will be taken from your new answer; everything else is kept exactly as in your previous "
            "answer, so fix these and change nothing else. Quote JD text verbatim and never add, drop or rewrite "
            "targets. A jd_span is only for match \"equivalent\" (the SAME role/function in different wording, "
            "with an alignment accounting for every word); when in doubt use match \"none\". Never drop a "
            "restriction: that would broaden the requirement. Restriction kinds are role, function or vague only. "
            "target_basis is required and must agree with your targets/restrictions. Exactly ONE object per hint "
            "id. Relations: same = letter for letter the same word; form = the same word in another grammatical "
            "form of the same language (plural, verb/noun form), never a synonym; translation = EVERY pair "
            "between two languages; abbreviation = acronym and expansion. Never return a policy:\n- "
            + "\n- ".join(errors[:40]) + pairs)


@dataclass
class PassAOutcome:
    reason: str | None                       # None = ok; else a v3 failure reason
    raw: str | None                          # the final validated answer (merged / narrowed / withdrawn)
    validation: PassAValidation | None
    meta: dict = field(default_factory=dict)
    cache_key: str | None = None
    errors: dict = field(default_factory=lambda: {"errors": [], "repair_errors": []})

    @property
    def ok(self) -> bool:
        return self.reason is None


def _items(raw: str | None) -> dict[str, dict]:
    try:
        data = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return {}
    out: dict[str, dict] = {}
    for it in (data.get("criteria") if isinstance(data, dict) and isinstance(data.get("criteria"), list) else []):
        if isinstance(it, dict) and isinstance(it.get("criterion_id"), str) and it["criterion_id"] not in out:
            out[it["criterion_id"]] = it
    return out


def merge_pass_a(main_raw: str, repair_raw: str, val: PassAValidation, criteria: list[CriterionInput]
                 ) -> tuple[str, dict, list[str]]:
    """Frozen scoped merge + target_basis rule + F6. -> (merged raw, merge info, F6 violations)."""
    merged_raw, info = merge_repair(main_raw, repair_raw, val.scoped, criteria)
    main, rep = _items(main_raw), _items(repair_raw)
    try:
        data = json.loads(merged_raw)
    except ValueError:
        return merged_raw, info, []
    scopes: dict[str, set[str]] = {}
    for e in val.scoped:
        if e.criterion_id:
            scopes.setdefault(e.criterion_id, set()).update(e.scopes)
    taken = {(t.get("criterion_id"), t.get("field")) for t in info.get("taken", [])}
    violations: list[str] = []
    items = data.get("criteria") if isinstance(data, dict) and isinstance(data.get("criteria"), list) else []
    by_id = {c.criterion_id: c for c in criteria}
    for g in items:
        if not isinstance(g, dict) or g.get("criterion_id") not in by_id:
            continue
        cid = g["criterion_id"]
        m, r = main.get(cid), rep.get(cid)
        follows = (SCOPE_TARGET_BASIS in scopes.get(cid, set()) or (cid, "restrictions") in taken)
        if follows and r is not None and info.get("mode") == "scoped" and (cid, "criterion") not in taken:
            if "target_basis" in r:
                g["target_basis"] = copy.deepcopy(r["target_basis"])
                info["taken"].append({"criterion_id": cid, "field": "target_basis"})
        if hint_ids(by_id[cid]):
            continue
        fb, mb = g.get("target_basis"), (m or {}).get("target_basis")
        if fb in NO_TARGET_BASES and fb != mb:
            violations.append(f"criterion {cid}: {F6_ERROR} (target_basis {fb!r} only in the repair)")
        mr, gr = (m or {}).get("restrictions"), g.get("restrictions")
        if isinstance(mr, list) and mr and isinstance(gr, list) and not gr:
            violations.append(f"criterion {cid}: {F6_ERROR} (a non-empty restriction list became [])")
    info["f6_violations"] = list(violations)
    return json.dumps({"criteria": items}, ensure_ascii=False), info, violations


def _annotate(val: PassAValidation, meta: dict) -> PassAValidation:
    """Audit records of withdrawn / narrowed claims on the final parsed results (also after a cache hit)."""
    results = dict(val.results)
    for w in meta.get("alignment_withdrawn", []):
        cid = w["criterion_id"]
        if cid in results:
            rec = {k: v for k, v in w.items() if k != "criterion_id"}
            results[cid] = replace(results[cid], parsed=replace(results[cid].parsed, withdrawn=(rec,)))
    for rec in meta.get("span_normalized", []):
        pc = results.get(rec["criterion_id"])
        t = next((t for t in pc.parsed.targets if t.hint_id == rec["hint"]), None) if pc else None
        if t is not None and t.span is not None and t.span.text == rec["normalized_span"]:
            results[rec["criterion_id"]] = replace(pc, parsed=replace(pc.parsed,
                                                                       normalized=pc.parsed.normalized + (rec,)))
    return replace(val, results=results)


def _base_meta(req: PassARequest, model: str) -> dict:
    return {"prompt_code": S1A_PROMPT_CODE, "prompt_version": S1A_PROMPT_VERSION,
            "prompt_fingerprint": pass_a_prompt_fingerprint(), "prompt_sha256": S1A_PROMPT_SHA256,
            "model": model, "temperature": S1A_TEMPERATURE, "max_output_tokens": S1A_MAX_TOKENS,
            "max_input_tokens": S1A_MAX_INPUT_TOKENS, "token_count_method": llm_call.TOKEN_COUNT_METHOD,
            "input_hash": req.input_hash, "request_token_upper_bound": None,
            "repair_request_token_upper_bound": None, "calls": 0, "repair_used": False, "call_log": [],
            "cache_hit": False}


async def _call(client, model: str, messages: list[dict], meta: dict, kind: str) -> tuple[str, str | None]:
    return await llm_call.chat_json_call(client, model=model, messages=messages, temperature=S1A_TEMPERATURE,
                                        max_tokens=S1A_MAX_TOKENS, call_log=meta["call_log"], kind=kind)


async def _run(req: PassARequest, client, model: str, meta: dict) -> dict:
    def out(reason, raw=None, errors=(), repair_errors=()):
        return {"reason": reason, "raw": raw, "meta": meta,
                "errors": {"errors": list(errors), "repair_errors": list(repair_errors)}}

    messages = build_pass_a_messages(req)
    meta["request_token_upper_bound"] = llm_call.request_token_upper_bound(messages)
    if meta["request_token_upper_bound"] > S1A_MAX_INPUT_TOKENS:
        return out(REASON_EXCEEDS_MODEL_CONTEXT)
    if client is None:
        client = llm_call.create_client()
    jd, crits, durs = req.jd, req.criteria, req.durations
    first_errors: list[str] = []
    try:
        raw, finish = await _call(client, model, messages, meta, "main")
        meta["calls"] = 1
        if finish == "length":
            return out(REASON_OUTPUT_TRUNCATED)
        val = validate_pass_a(raw, jd, crits, durs)
        outcome = "validated"
        normalized: list[dict] = []
        if not val.ok:
            narrowed, normalized = normalize_duplicate_representations(val.scoped, raw, crits)
            normalized = [{**r, "stage": "pre_repair", "claim_source": "main"} for r in normalized]
            if normalized:
                main_errors = list(val.errors)
                raw, val = narrowed, validate_pass_a(narrowed, jd, crits, durs)
                meta["span_normalized"] = normalized
                if val.ok:
                    first_errors, outcome = main_errors, "normalized"
        if not val.ok:
            first_errors, main_val, main_raw = list(val.errors), val, raw
            note = pass_a_repair_note([e.message for e in val.scoped], pair_repair_guidance(val.scoped))
            repair_messages = messages + [{"role": "assistant", "content": raw}, {"role": "user", "content": note}]
            meta["repair_request_token_upper_bound"] = llm_call.request_token_upper_bound(repair_messages)
            if meta["repair_request_token_upper_bound"] > S1A_MAX_INPUT_TOKENS:
                return out(REASON_EXCEEDS_MODEL_CONTEXT, errors=first_errors)
            meta["repair_used"] = True
            meta["repair_note"] = note
            rep_raw, finish = await _call(client, model, repair_messages, meta, "repair")
            meta["calls"] = 2
            if finish == "length":
                return out(REASON_OUTPUT_TRUNCATED, errors=first_errors)
            try:
                merged, meta["repair_merge"], f6 = merge_pass_a(main_raw, rep_raw, main_val, crits)
            except Exception as exc:            # deterministic merge failed = bug, never an AI outage
                logger.exception("Pass A repair merge failed")
                meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
                return out(REASON_INTERNAL_ERROR, errors=first_errors)
            if f6:
                meta["outcome"] = "f6_rejected"
                return out(REASON_VALIDATION_FAILED, errors=first_errors, repair_errors=f6)
            val = validate_pass_a(merged, jd, crits, durs)
            outcome = "repaired"
            if not val.ok:
                narrowed, post = normalize_duplicate_representations(val.scoped, merged, crits)
                if post:
                    post = [{**r, "stage": "post_repair"} for r in post]
                    merged, val = narrowed, validate_pass_a(narrowed, jd, crits, durs)
                    normalized = normalized + post
                    meta["span_normalized"] = normalized
                    if val.ok:
                        outcome = "repaired_normalized"
            if not val.ok:
                plan = plan_withdrawal(val.scoped, merged, crits)
                if plan:
                    narrowed, withdrawn = apply_withdrawal(merged, plan)
                    wval = validate_pass_a(narrowed, jd, crits, durs)
                    if wval.ok:
                        merged, val, outcome = narrowed, wval, "repaired_withdrawn"
                        meta["alignment_withdrawn"] = [{"criterion_id": cid, **w} for cid, w in withdrawn.items()]
            if not val.ok:
                meta["outcome"] = "failed_validation"
                return out(REASON_VALIDATION_FAILED, errors=first_errors, repair_errors=val.errors)
            raw = merged
    except Exception as exc:                    # network / API / auth / timeout
        logger.warning("Pass A unavailable: %s", exc)
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return out(REASON_AI_UNAVAILABLE, errors=first_errors)
    meta["outcome"] = outcome
    return out(None, raw=raw, errors=first_errors)


async def run_pass_a(jd: JDText, criteria: list[CriterionInput], *, client: Any = None, model: str = S1A_MODEL,
                     cache: PassACache | None = None) -> PassAOutcome:
    req = build_pass_a_request(jd, criteria)
    key = pass_a_cache_key(req, model=model)
    stored = cache.get(key) if cache is not None else None
    if stored is not None:
        res = copy.deepcopy(stored)
        res["meta"] = {**res["meta"], "cache_hit": True}
    else:
        res = await _run(req, client, model, _base_meta(req, model))
        if cache is not None and res["reason"] in CACHEABLE:
            cache.set(key, copy.deepcopy(res))
    val = None
    if res["reason"] is None:
        val = validate_pass_a(res["raw"], jd, criteria, req.durations)     # deterministic; also after a cache hit
        if not val.ok:                                                    # cannot happen unless a rule changed
            res["meta"]["error"] = "final answer no longer validates"
            return PassAOutcome(REASON_INTERNAL_ERROR, None, val, res["meta"], key, res["errors"])
        val = _annotate(val, res["meta"])
    return PassAOutcome(res["reason"], res["raw"], val, res["meta"], key, res["errors"])


@dataclass
class PassAJobResult:
    job_id: str
    outcome: PassAOutcome | None
    frozen: list[FrozenTarget] = field(default_factory=list)       # ok: one per criterion
    failed: list[S1ArtifactV4] = field(default_factory=list)       # failure: one target_failed artefact each

    @property
    def status(self) -> str:
        if self.outcome is None:
            return "empty"
        return "ok" if self.outcome.ok else "failed"


def run_record(outcome: PassAOutcome) -> dict:
    m = outcome.meta
    return {"pass_a": {"input_hash": m.get("input_hash"), "prompt_version": m.get("prompt_version"),
                       "prompt_fingerprint": m.get("prompt_fingerprint"), "model": m.get("model"),
                       "calls": m.get("calls"), "repair_used": m.get("repair_used"), "outcome": m.get("outcome"),
                       "cache_hit": m.get("cache_hit"), "reason": outcome.reason}}


async def run_pass_a_job(job_id: str, jd_text: str, analysis_json: dict | None, *, client: Any = None,
                         model: str = S1A_MODEL, cache: PassACache | None = None,
                         recruiter_fields: Mapping[str, str] | None = None) -> PassAJobResult:
    """Pass A for one job: frozen targets for every criterion, or one target_failed artefact per criterion (F1).
    Reads only the S1 inputs of analysis_json (criteria enumeration); never any qualifying-context field."""
    rf, _ = split_recruiter_fields(recruiter_fields)
    criteria = enumerate_experience_criteria(job_id, analysis_json)
    if not criteria:
        return PassAJobResult(str(job_id), None)
    jd = JDText(jd_text)
    outcome = await run_pass_a(jd, criteria, client=client, model=model, cache=cache)
    run = run_record(outcome)
    if not outcome.ok:
        validation = {**outcome.errors}
        return PassAJobResult(str(job_id), outcome, failed=[failed_v4(c, outcome.reason, run=run,
                                                                      validation=validation, recruiter_fields=rf)
                                                            for c in criteria])
    durs = build_pass_a_request(jd, criteria).durations
    frozen = [freeze_targets(c, outcome.validation.results[c.criterion_id], jd, durs, run=run, recruiter_fields=rf)
              for c in criteria]
    return PassAJobResult(str(job_id), outcome, frozen=frozen)


__all__ = ["run_pass_a", "run_pass_a_job", "build_pass_a_messages", "build_pass_a_call", "pass_a_repair_note",
           "merge_pass_a", "InMemoryPassACache", "PassAOutcome", "PassAJobResult", "F6_ERROR"]
