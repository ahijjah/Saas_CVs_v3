"""
S2 Phase 1 — semantic classification of validated S0 experience entries
against ONE stored RequirementSpec. SHADOW ONLY: nothing in production imports
this package.

  classify_criterion(s0_doc, spec, extracted_text=..., client=..., cache=...)
      -> S2Result (``to_dict()`` = the s2_result_v1 audit object)

S2 labels each owned experience entry qualifying / related / not_relevant /
insufficient. It never computes years, dates or criterion status (S4/S5 own
that), never sees dates (masked as [dates]) or the numeric threshold (masked
as [N]), and never sees entry kind, unowned/uncertain lines or job context
beyond the spec. One call per criterion with ALL owned entries; no batching.

Call flow (all-or-nothing per criterion; technical failures are never labels):
  pure_duration                      -> skipped (no call)
  S0 unverified / failed             -> not_run (integration guard)
  zero trusted experience entries    -> ok, empty results (no call)
  request over the input budget      -> failed exceeds_model_context (no call; cached)
  main call exception                -> failed ai_unavailable (retryable; not cached)
  main finish_reason == "length"     -> failed output_truncated (no repair; not cached)
  main valid                         -> ok (validated; cached)
  main invalid -> ONE repair call (previous response + exact violations):
      repair over budget             -> failed exceeds_model_context (cached)
      exception                      -> failed ai_unavailable (retryable)
      finish_reason == "length"      -> failed output_truncated
      valid                          -> ok (repaired; cached)
      invalid                        -> failed validation_failed (cached)
  masking / mapping invariant broken -> failed internal_error (retryable; not cached)
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from services.ai_service import _SECURITY_HARDENING_SUFFIX
from services.experience_accounting import POLICY_PURE_DURATION, RequirementSpec
from services.s0_experience import llm_call
from services.s0_experience.schema import (
    STRUCTURE_FAILED, STRUCTURE_REPAIRED, STRUCTURE_VALIDATED, TRUSTED_STRUCTURE, S0Document,
)
from services.s0_experience.text import split_lines, text_sha256
from services.s2_experience.masking import (
    MASK_TOKEN, MaskingError, date_spans_by_line, mask_free_text, mask_line, mask_threshold,
)
from services.s2_experience.validator import EntryView, validate_response

logger = logging.getLogger(__name__)

S2_SCHEMA = "s2_result_v1"
S2_VERSION = "1.0.0"                   # input format + masking + validator
S2_INPUT_VERSION = "s2-in-1"
S2_PROMPT_CODE = "recruitment.experience_relevance"
S2_PROMPT_VERSION = "s2-1"
S2_MODEL = "gpt-4o-mini"
S2_TEMPERATURE = 0.0
S2_MAX_TOKENS = llm_call.MAX_OUTPUT_TOKENS
S2_MAX_INPUT_TOKENS = llm_call.MAX_INPUT_TOKENS
SEMANTIC_POLICIES = ("explicit_role", "functional", "sector")

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_NOT_RUN = "not_run"

REASON_EXCEEDS_MODEL_CONTEXT = "exceeds_model_context"
REASON_AI_UNAVAILABLE = "ai_unavailable"
REASON_OUTPUT_TRUNCATED = "output_truncated"
REASON_VALIDATION_FAILED = "validation_failed"
REASON_INTERNAL_ERROR = "internal_error"
REASON_PURE_DURATION = "pure_duration"
REASON_STRUCTURE_UNVERIFIED = "structure_unverified"
REASON_STRUCTURE_FAILED = "structure_failed"
REASON_NO_ENTRIES = "no_experience_entries"
RETRYABLE_REASONS = frozenset({REASON_AI_UNAVAILABLE, REASON_INTERNAL_ERROR})
CACHEABLE = frozenset({None, REASON_VALIDATION_FAILED, REASON_EXCEEDS_MODEL_CONTEXT})

S2_SYSTEM_PROMPT = """You classify a candidate's CV experience entries against ONE job experience requirement.

INPUT (JSON): "criterion" (policy, targets, setting, criterion_text, source_spans) and "entries" (entry_id, title, employer, lines with line numbers). "[dates]" marks removed dates and "[N]" a removed number: ignore both and NEVER quote "[dates]".

For EVERY entry return exactly one result with one label:
- qualifying, related, not_relevant or insufficient.

HARD RULES
1. Judge each entry only against the stated requirement and only from that entry's own lines.
2. Never reason about duration, dates, years, how long something lasted, or seniority-by-time. Never compute years or decide whether the requirement as a whole is met.
3. A job title is not required: responsibilities can establish qualifying. A title alone qualifies only when it inherently denotes the target and nothing in the entry contradicts it (basis "title").
4. "Substantial" means a regular or main responsibility; incidental, occasional or exposure-level involvement is related, not qualifying.
5. insufficient means the entry LACKS INFORMATION (say what is missing); it is not for being unsure. A clearly described job must get one of the other three labels.
6. Quotes must be copied VERBATIM from a line of the SAME entry (part of a line is fine). Never invent or paraphrase evidence.

POLICY: explicit_role (candidate must have HELD one of the target roles)
- qualifying: title is a target role or plain equivalent; OR responsibilities show that role's accountability (owning delivery, budget, team, programme/project) as the main substance of the job.
- related: assistant/associate/deputy/supporting role; supports the target role or does tasks it oversees; same domain at lower/supporting accountability; does the role's duties only occasionally.
- not_relevant: enough information to see no material connection to the role's function or domain.
- insufficient: generic title/context without information about the accountability held.

POLICY: functional (candidate must have PERFORMED the target function, whatever the title)
- qualifying: performs the target function substantially (shown by responsibilities, or a title that inherently denotes doing it); if a setting is given, the setting must also be shown.
- related: adjacent, supporting or user-only work, or incidental involvement; OR the function performed outside a required setting.
- not_relevant: a different function with no material involvement in the target.
- insufficient: the entry does not show what work was done (or, with a setting requirement, the setting cannot be identified while the function is shown: include "setting" in missing).

POLICY: sector (candidate must have worked IN the target sector)
- qualifying: employer/programme/context shows the target sector (basis "context" allowed) AND any function constraint in the criterion is met.
- related: adjacent sector; OR correct sector but the criterion's function constraint is not met.
- not_relevant: clearly outside the sector.
- insufficient: employer/context cannot be identified.

EVIDENCE
- qualifying, related, not_relevant: at least one quote that shows what the experience actually was.
- insufficient: list what is missing in "missing" (responsibilities, function, setting, employer_context, role_level); quotes optional.
- basis: "title" | "responsibilities" | "title_and_responsibilities" | "context" ("context" only for sector, or functional with a setting).

OUTPUT — JSON only:
{"results": [{"entry_id": "E1", "label": "qualifying", "basis": "responsibilities",
  "quotes": [{"line": 32, "text": "verbatim text from line 32"}],
  "reason": "one sentence", "missing": []}]}""" + _SECURITY_HARDENING_SUFFIX


class S2Cache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def set(self, key: str, value: dict) -> None: ...


class InMemoryS2Cache:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        return self.store.get(key)

    def set(self, key: str, value: dict) -> None:
        self.store[key] = value


def prompt_fingerprint() -> str:
    return hashlib.sha256(S2_SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def spec_semantic_hash(spec: RequirementSpec, criterion_text_masked: str) -> str:
    """Hash of exactly the spec fields S2 can see. required_years is excluded."""
    return hashlib.sha256(_canonical({
        "criterion_id": spec.criterion_id, "spec_version": spec.spec_version, "policy": spec.policy,
        "targets": list(spec.targets), "setting": spec.setting, "source_spans": list(spec.source_spans),
        "criterion_text_masked": criterion_text_masked,
    }).encode("utf-8")).hexdigest()


def s2_cache_key(s0_cache_key: str, spec_hash: str, *, prompt_version: str = S2_PROMPT_VERSION,
                 model: str = S2_MODEL) -> str:
    """No as_of, no required_years, no job/application identity."""
    ident = f"s2|{S2_VERSION}|{s0_cache_key}|{spec_hash}|{prompt_version}:{prompt_fingerprint()}|{model}"
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()


@dataclass
class S2Result:
    status: str
    status_reason: str | None
    retryable: bool
    criterion_id: str
    spec_version: str
    spec_hash: str | None
    s0_cache_key: str
    s0_version: str
    s0_structure_status: str
    cache_key: str | None = None
    structurer: dict = field(default_factory=dict)
    validation: dict = field(default_factory=lambda: {"errors": [], "repair_errors": []})
    results: list[dict] = field(default_factory=list)
    s2_version: str = S2_VERSION
    schema: str = S2_SCHEMA

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def labels(self) -> dict[str, str]:
        """entry_id -> label for S5. Only valid for an ok result."""
        if not self.ok:
            raise RuntimeError(f"S2 result is {self.status}/{self.status_reason}: no labels")
        return {r["entry_id"]: r["label"] for r in self.results}

    def to_dict(self) -> dict:
        return {
            "_schema": self.schema, "s2_version": self.s2_version, "cache_key": self.cache_key,
            "criterion_id": self.criterion_id, "spec_version": self.spec_version, "spec_hash": self.spec_hash,
            "s0_cache_key": self.s0_cache_key, "s0_version": self.s0_version,
            "s0_structure_status": self.s0_structure_status,
            "status": self.status, "status_reason": self.status_reason, "retryable": self.retryable,
            "structurer": dict(self.structurer), "validation": dict(self.validation),
            "results": [dict(r) for r in self.results],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "S2Result":
        if d.get("_schema") != S2_SCHEMA:
            raise ValueError(f"not an {S2_SCHEMA} object")
        return cls(status=d["status"], status_reason=d.get("status_reason"),
                   retryable=bool(d.get("retryable")), criterion_id=d["criterion_id"],
                   spec_version=d.get("spec_version", ""), spec_hash=d.get("spec_hash"),
                   s0_cache_key=d.get("s0_cache_key", ""), s0_version=d.get("s0_version", ""),
                   s0_structure_status=d.get("s0_structure_status", ""), cache_key=d.get("cache_key"),
                   structurer=dict(d.get("structurer") or {}), validation=dict(d.get("validation") or {}),
                   results=[dict(r) for r in d.get("results") or []], s2_version=d.get("s2_version", S2_VERSION))


@dataclass
class BuiltRequest:
    payload: dict
    entries: list[EntryView]
    criterion_text_masked: str

    @property
    def user_message(self) -> str:
        return "INPUT:\n" + _canonical(self.payload)


def build_request(s0_doc: S0Document, spec: RequirementSpec, extracted_text: str) -> BuiltRequest:
    """Deterministic, masked S2 input for one criterion (no prohibited fields)."""
    lines = split_lines(extracted_text)
    spans = date_spans_by_line(s0_doc, lines)
    views: list[EntryView] = []
    payload_entries: list[dict] = []
    for e in s0_doc.experience_entries():
        masked = {ln: mask_line(ln, lines, spans) for ln in e.owned_lines()}
        for ln, ml in masked.items():
            if ml.original != lines[ln - 1]:
                raise MaskingError(f"line {ln} mapping mismatch")
        title_m = mask_free_text(e.title.text) if e.title else None
        employer_m = mask_free_text(e.employer.text) if e.employer else None
        views.append(EntryView(e.entry_id, masked, e.title.line if e.title else None, title_m))
        payload_entries.append({
            "entry_id": e.entry_id, "title": title_m, "employer": employer_m,
            "lines": [{"line": ln, "text": masked[ln].masked} for ln in sorted(masked)],
        })
    crit_masked = mask_threshold(spec.criterion_text)
    payload = {
        "s2_input_version": S2_INPUT_VERSION,
        "criterion": {
            "criterion_id": spec.criterion_id, "policy": spec.policy, "targets": list(spec.targets),
            "setting": spec.setting, "criterion_text": crit_masked, "source_spans": list(spec.source_spans),
        },
        "entries": payload_entries,
    }
    return BuiltRequest(payload, views, crit_masked)


def repair_note(errors: list[str]) -> str:
    return ("Your previous JSON violates these rules. Return the COMPLETE corrected JSON with exactly "
            "one result for every entry, fixing every violation and changing nothing else. Quotes must be "
            f"verbatim from the entry's own lines and must not contain {MASK_TOKEN}:\n- "
            + "\n- ".join(errors[:40]))


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = llm_call.create_client()
    return _client


async def classify_criterion(s0_doc: S0Document | dict, spec: RequirementSpec, *, extracted_text: str,
                             client: Any = None, model: str = S2_MODEL,
                             cache: S2Cache | None = None) -> S2Result:
    doc = s0_doc if isinstance(s0_doc, S0Document) else S0Document.from_dict(dict(s0_doc))
    base = dict(criterion_id=spec.criterion_id, spec_version=spec.spec_version,
                s0_cache_key=doc.cache_key, s0_version=doc.s0_version,
                s0_structure_status=doc.structure_status)
    if spec.policy == POLICY_PURE_DURATION:
        return S2Result(STATUS_SKIPPED, REASON_PURE_DURATION, False, spec_hash=None, **base)
    if spec.policy not in SEMANTIC_POLICIES:
        raise ValueError(f"unsupported policy {spec.policy!r}")
    if not spec.criterion_id:
        raise ValueError("RequirementSpec.criterion_id is required for S2")
    if doc.structure_status not in TRUSTED_STRUCTURE:
        reason = REASON_STRUCTURE_FAILED if doc.structure_status == STRUCTURE_FAILED else REASON_STRUCTURE_UNVERIFIED
        return S2Result(STATUS_NOT_RUN, reason, False, spec_hash=None, **base)
    if text_sha256(extracted_text) != doc.text_sha256:
        raise ValueError("extracted_text does not match the S0 document (text_sha256 differs)")

    meta: dict[str, Any] = {
        "prompt_code": S2_PROMPT_CODE, "prompt_version": S2_PROMPT_VERSION,
        "prompt_fingerprint": prompt_fingerprint(), "model": model, "temperature": S2_TEMPERATURE,
        "max_output_tokens": S2_MAX_TOKENS, "max_input_tokens": S2_MAX_INPUT_TOKENS,
        "token_count_method": llm_call.TOKEN_COUNT_METHOD, "input_sha256": None,
        "request_token_upper_bound": None, "repair_request_token_upper_bound": None,
        "calls": 0, "repair_used": False, "call_log": [],
    }
    try:
        req = build_request(doc, spec, extracted_text)
    except Exception as exc:                          # masking invariant = bug
        logger.exception("S2 request build failed")
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return S2Result(STATUS_FAILED, REASON_INTERNAL_ERROR, True, spec_hash=None, structurer=meta, **base)
    spec_hash = spec_semantic_hash(spec, req.criterion_text_masked)
    if not req.entries:
        return S2Result(STATUS_OK, REASON_NO_ENTRIES, False, spec_hash=spec_hash, structurer=meta, **base)

    key = s2_cache_key(doc.cache_key, spec_hash, model=model)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return S2Result.from_dict(hit)
    res = await _run(req, spec, client, model, meta, base, spec_hash, key)
    if cache is not None and res.status in (STATUS_OK, STATUS_FAILED) and res.status_reason in CACHEABLE:
        cache.set(key, res.to_dict())
    return res


async def _run(req: BuiltRequest, spec: RequirementSpec, client, model, meta, base, spec_hash, key) -> S2Result:
    def _failed(reason: str, validation: dict | None = None) -> S2Result:
        return S2Result(STATUS_FAILED, reason, reason in RETRYABLE_REASONS, spec_hash=spec_hash,
                        cache_key=key, structurer=meta,
                        validation=validation or {"errors": [], "repair_errors": []}, **base)

    messages = [{"role": "system", "content": S2_SYSTEM_PROMPT},
                {"role": "user", "content": req.user_message}]
    meta["input_sha256"] = hashlib.sha256(req.user_message.encode("utf-8")).hexdigest()
    bound = llm_call.request_token_upper_bound(messages)
    meta["request_token_upper_bound"] = bound
    if bound > S2_MAX_INPUT_TOKENS:
        return _failed(REASON_EXCEEDS_MODEL_CONTEXT)

    client = client or _get_client()
    has_setting = bool(spec.setting)
    first_errors: list[str] = []
    try:
        raw, finish = await llm_call.chat_json_call(
            client, model=model, messages=messages, temperature=S2_TEMPERATURE,
            max_tokens=S2_MAX_TOKENS, call_log=meta["call_log"], kind="main")
        meta["calls"] = 1
        if finish == "length":
            return _failed(REASON_OUTPUT_TRUNCATED)
        val = validate_response(raw, req.entries, policy=spec.policy, has_setting=has_setting)
        status_kind = STRUCTURE_VALIDATED
        if not val.ok:
            first_errors = list(val.errors)
            repair_messages = messages + [{"role": "assistant", "content": raw},
                                          {"role": "user", "content": repair_note(first_errors)}]
            rb = llm_call.request_token_upper_bound(repair_messages)
            meta["repair_request_token_upper_bound"] = rb
            if rb > S2_MAX_INPUT_TOKENS:
                return _failed(REASON_EXCEEDS_MODEL_CONTEXT, {"errors": first_errors, "repair_errors": []})
            meta["repair_used"] = True
            raw, finish = await llm_call.chat_json_call(
                client, model=model, messages=repair_messages, temperature=S2_TEMPERATURE,
                max_tokens=S2_MAX_TOKENS, call_log=meta["call_log"], kind="repair")
            meta["calls"] = 2
            if finish == "length":
                return _failed(REASON_OUTPUT_TRUNCATED, {"errors": first_errors, "repair_errors": []})
            val = validate_response(raw, req.entries, policy=spec.policy, has_setting=has_setting)
            status_kind = STRUCTURE_REPAIRED
            if not val.ok:
                return _failed(REASON_VALIDATION_FAILED, {"errors": first_errors, "repair_errors": list(val.errors)})
    except Exception as exc:                           # network / API / auth / timeout
        logger.warning("S2 classifier unavailable: %s", exc)
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return _failed(REASON_AI_UNAVAILABLE, {"errors": first_errors, "repair_errors": []})

    meta["outcome"] = status_kind                       # "validated" | "repaired"
    return S2Result(STATUS_OK, None, False, spec_hash=spec_hash, cache_key=key, structurer=meta,
                    validation={"errors": first_errors, "repair_errors": []},
                    results=[{"entry_id": r.entry_id, "label": r.label, "basis": r.basis,
                              "reason": r.reason, "missing": list(r.missing), "quotes": list(r.quotes)}
                             for r in val.results], **base)


__all__ = ["classify_criterion", "build_request", "S2Result", "InMemoryS2Cache", "s2_cache_key",
           "spec_semantic_hash", "S2_SYSTEM_PROMPT", "S2_PROMPT_VERSION", "S2_MODEL", "repair_note"]
