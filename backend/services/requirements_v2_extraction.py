"""
Requirements-v2 extraction stage: prompt and model resolution, the model call, the production pipeline, and the transactional persistence of an attempt.

Everything here is used ONLY by workers/requirements_v2_extraction_worker.py (the model call and the write) and services/requirements_api.py
(the retry request). It never touches the legacy criteria path, its prompt key (`criteria_extraction`), its stage (`cv_analyzer`) or its
settings.

FEATURE SWITCH: system_config 'requirements_v2.enabled' (default 'false'). Read at job creation and again at worker start; a switch that is off
means no v2 job is created and no model is called.

CONFIGURATION: the prompt text, version, temperature and max_tokens come from ai_prompts (prompt_code 'criteria_extraction_v2'), and the model
from the model registry's stage default for 'requirements_v2_extraction'. An administrator changes them through the existing prompt and model
admin API (super_admin, validated there), with no code deployment. The worker checks what it reads with the same rules as that API, refuses an
empty text, and never falls back to another model. Every attempt records the prompt version, the SHA-256 of the text, the requested and returned
model, the settings actually sent, the usage and the live acknowledgment policy it ran under.

The output contract (the JSON response format) and the per-call timeout are code constants: the production pipeline parses that output.

PERSISTENCE: an attempt has an attempt token (job_criteria.requirements_extraction_token). Every write re-checks the token under a row lock; a late
or superseded result is discarded and never overwrites a newer attempt, a recruiter's edit or the original snapshot.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from services.requirements_pipeline import STORAGE_KEY, PipelineError
from services import requirements_model_inputs as NI
from services.requirements_pipeline import core as pipeline_core
from services.requirements_v2.extraction.prompt import EXTRACTION_CONFIG, build_user_message

logger = logging.getLogger(__name__)

FEATURE_KEY = "requirements_v2.enabled"
RECOVERY_KEY = "requirements_v2.recovery_enabled"
STAGE = "requirements_v2_extraction"
PROMPT_CODE = "criteria_extraction_v2"
PROVIDER = "openai"
RESPONSE_FORMAT = {"type": "json_object"}     # the output contract the production pipeline parses; not configurable
STALE_AFTER_S = 30 * 60                       # a claimed attempt that never finished can be retried after this
PER_CALL_TIMEOUT_S = 90                       # the approved per-call timeout (ev.PLAN["per_call_timeout_s"] in the benchmark)
MAX_ATTEMPT_ERRORS = 2000

# error codes that are terminal (no retry, the attempt is failed with the code); everything else is a transport error and may retry
TERMINAL_CALL_KINDS = ("auth", "model_unavailable", "bad_request")
RETRYABLE_CALL_KINDS = ("rate_limit", "timeout", "transport", "http_error_5xx")


class ExtractionUnavailable(Exception):
    """A precondition of the v2 extraction is not met (feature off, no active prompt, prompt empty or out of range, stage not configured). `code` is stable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class TransportError(Exception):
    """A model call failed. `kind` is one of TERMINAL_CALL_KINDS or RETRYABLE_CALL_KINDS; the provider's message is never kept."""

    def __init__(self, kind: str, status: int | None = None):
        super().__init__(kind)
        self.kind, self.status = kind, status

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_CALL_KINDS


@dataclass
class PromptRef:
    prompt_id: str | None
    version: int
    label: str                       # "criteria_extraction_v2-<version>", the same naming the benchmark uses
    sha256: str                      # SHA-256 of the system prompt text as stored
    system_prompt: str
    temperature: float
    max_tokens: int

    @property
    def settings(self) -> dict:
        return {"temperature": self.temperature, "max_tokens": self.max_tokens, "response_format": dict(RESPONSE_FORMAT)}


@dataclass
class CallResult:
    raw: str
    finish_reason: str | None
    requested_model: str
    returned_model: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    settings: dict = field(default_factory=dict)      # the settings actually sent (temperature, max_tokens, response_format, timeout_s)


# ── feature switch ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
async def feature_enabled(db) -> bool:
    """Exactly "true" (trimmed, lower-cased) enables it. A missing row, a read error or any other value means OFF."""
    return await _switch_on(db, FEATURE_KEY)


async def recovery_enabled(db) -> bool:
    """The re-analysis switch (migration 109). Off by default; read on every request and on every candidate attempt."""
    return await _switch_on(db, RECOVERY_KEY)


async def _switch_on(db, key: str) -> bool:
    try:
        row = await db.execute(text("SELECT value FROM system_config WHERE key = :k"), {"k": key})
        value = row.scalar_one_or_none()
    except Exception as exc:                                    # noqa: BLE001 - fail closed
        logger.warning("requirements-v2 feature switch unreadable (treated as OFF): %s", exc)
        try:
            await db.rollback()
        except Exception:                                       # noqa: BLE001
            pass
        return False
    return isinstance(value, str) and value.strip().lower() == "true"


# ── prompt ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
async def load_prompt(db) -> PromptRef:
    """The active v2 prompt as configured by an administrator. The text is never compared with a code list: the SHA-256 of the text that was used
    is recorded with the attempt instead. Temperature and max_tokens are checked with the prompt admin API's own request model, and an empty text is
    refused. Raises ExtractionUnavailable."""
    from pydantic import ValidationError
    from routers.ai_prompts import UpdatePromptRequest        # the admin API's bounds for temperature and max_tokens
    row = (await db.execute(text("""
        SELECT prompt_id, version, system_prompt, temperature, max_tokens
        FROM ai_prompts WHERE prompt_code = :code AND is_active = TRUE
        ORDER BY version DESC LIMIT 1
    """), {"code": PROMPT_CODE})).mappings().first()
    if row is None:
        raise ExtractionUnavailable("prompt_not_active", f"No active {PROMPT_CODE} prompt is configured.")
    version = int(row["version"])
    body = row["system_prompt"] or ""
    if not body.strip():
        raise ExtractionUnavailable("prompt_empty", f"{PROMPT_CODE} version {version} has an empty text.")
    try:
        temperature, max_tokens = float(row["temperature"]), int(row["max_tokens"])
        UpdatePromptRequest(temperature=temperature, max_tokens=max_tokens)
    except (TypeError, ValueError, ValidationError):
        raise ExtractionUnavailable("prompt_settings_invalid",
                                    f"{PROMPT_CODE} version {version} has a temperature or max_tokens outside the allowed range.") from None
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return PromptRef(str(row["prompt_id"]), version, f"{PROMPT_CODE}-{version}", digest, body, temperature, max_tokens)


# ── model ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
async def resolve_model(db):
    """The stage's registry model and client. No fallback to settings or to a legacy stage: any failure refuses the call."""
    from services.ai_model_registry_service import resolve_stage_client
    resolved = await resolve_stage_client(db, STAGE)
    if resolved is None or resolved.client is None:
        raise ExtractionUnavailable("model_not_configured", f"No usable model is configured for the {STAGE} stage.")
    if resolved.is_fallback:
        # the registry would answer with its fallback model: this stage never uses one (fail closed)
        raise ExtractionUnavailable("primary_model_unavailable", f"The primary model of the {STAGE} stage is unavailable; no fallback is used.")
    return resolved


def build_messages(prompt: PromptRef, jd_text: str, job_metadata: dict | None) -> list[dict]:
    return [{"role": "system", "content": prompt.system_prompt},
            {"role": "user", "content": build_user_message(jd_text, job_metadata)}]


# ── the call (the only network point; `client` is the registry's AsyncOpenAI-compatible client, or a test double) ──────────────────────
def _kind_of(exc: Exception) -> tuple[str, int | None]:
    import openai
    if isinstance(exc, openai.AuthenticationError):
        return "auth", 401
    if isinstance(exc, openai.PermissionDeniedError):
        return "auth", 403
    if isinstance(exc, openai.NotFoundError):
        return "model_unavailable", 404
    if isinstance(exc, openai.BadRequestError):
        return "bad_request", 400
    if isinstance(exc, openai.RateLimitError):
        return "rate_limit", 429
    if isinstance(exc, openai.APITimeoutError):
        return "timeout", None
    if isinstance(exc, openai.APIConnectionError):
        return "transport", None
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", None)
        return ("http_error_5xx" if status and status >= 500 else "http_error"), status
    return "transport", None


async def call_model(client, prompt: PromptRef, messages: list[dict], requested_model: str, *, timeout_s: float) -> CallResult:
    """One chat completion with the configured settings. Maps every provider error to a TransportError; never keeps a provider message."""
    started = time.monotonic()
    try:
        response = await client.with_options(timeout=timeout_s, max_retries=0).chat.completions.create(
            model=requested_model, messages=messages, temperature=prompt.temperature, max_tokens=prompt.max_tokens,
            response_format=dict(RESPONSE_FORMAT))
    except TransportError:
        raise
    except Exception as exc:                                    # noqa: BLE001 - classified below
        kind, status = _kind_of(exc)
        raise TransportError(kind, status) from None
    choice = response.choices[0]
    usage = getattr(response, "usage", None)
    return CallResult(raw=choice.message.content or "", finish_reason=choice.finish_reason, requested_model=requested_model,
                      returned_model=getattr(response, "model", None),
                      prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0), completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                      latency_ms=int((time.monotonic() - started) * 1000),
                      settings={**prompt.settings, "timeout_s": timeout_s})


# ── analysis: the production pipeline (the approved one, unchanged) ────────────────────────────────────────────────────────────────
def analyse(jd_text: str, call: CallResult, prompt: PromptRef, *, require_classification_acknowledgment: bool,
            job_metadata: dict | None = None) -> dict:
    """The approved pipeline on the model's raw response, plus the binding of what the model was given (the exact description and context) and
    the context review (AI-directed text in the context fields, found by the approved scanner). Blockers are draft-with-blockers, never failures."""
    from parser_candidates.requirements_v2_pipeline_1 import extract
    state = extract(jd_text, call.raw, finish_reason=call.finish_reason,
                    extraction_prompt={"version": prompt.label, "sha256": prompt.sha256},
                    require_classification_acknowledgment=require_classification_acknowledgment)
    state["model_inputs"] = NI.model_inputs(jd_text, job_metadata)
    state["context_review"] = scan_context(job_metadata)
    return state


def scan_context(job_metadata: dict | None) -> dict:
    """AI-directed text in the context fields, found by the approved instruction scanner (the rules used for the description). Each issue
    keeps the exact text of its span, so the recruiter sees what was found and where."""
    from services.requirements_pipeline.injection import instruction_spans
    issues = []
    for key in NI.CONTEXT_KEYS:
        text_value = NI.sent_text((job_metadata or {}).get(key))
        if not text_value:
            continue
        for n, span in enumerate(instruction_spans(text_value)):
            issues.append({"id": f"ctx_{key}_{n}", "kind": "context_instruction", "field": key, "status": "open",
                           "start": span["start"], "end": span["end"], "snippet": text_value[span["start"]:span["end"]],
                           "rule_codes": list(span["rule_codes"])})
    return {"guard_version": NI.CONTEXT_SCAN_VERSION, "issues": issues}


async def current_inputs(db, job_id: str, *, lock: bool = True) -> tuple[str | None, dict | None]:
    """The job's description and context fields as they are NOW. With lock (the default) the row is read under a share lock: a writer (the job edit)
    waits until the caller's transaction ends, so a value read here is still the value when the result is written. A read passes lock=False."""
    row = (await db.execute(text("""
        SELECT description, title, department, experience_level, location, job_type, work_mode
        FROM jobs WHERE job_id = CAST(:jid AS uuid)""" + (" FOR SHARE" if lock else "")), {"jid": str(job_id)})).mappings().first()
    if row is None:
        return None, None
    return row["description"], {k: row[k] for k in NI.CONTEXT_KEYS}


def weights_ok(doc: dict) -> bool:
    total = sum(doc["categories"][c]["weight"] for c in doc["categories"])
    return total in (0, 100)


# ── usage ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
async def record_usage(db, *, job_id: str, tenant_id: str | None, prompt: PromptRef | None, call: CallResult | None,
                       status: str, error_type: str | None, retry_count: int) -> None:
    """One ai_usage_log row per attempt call (never raises). Requested and returned model, prompt version/hash and settings are kept in metadata."""
    from services.ai_usage_service import log_ai_usage
    await log_ai_usage(
        db=db, stage=STAGE, provider=PROVIDER, model=(call.returned_model if call and call.returned_model else (call.requested_model if call else "unknown")),
        prompt_tokens=call.prompt_tokens if call else 0, completion_tokens=call.completion_tokens if call else 0,
        total_tokens=(call.prompt_tokens + call.completion_tokens) if call else 0, latency_ms=call.latency_ms if call else None,
        request_status=status, retry_count=retry_count, error_type=error_type, tenant_id=tenant_id, job_id=job_id,
        prompt_key=PROMPT_CODE, metadata={
            "requested_model": call.requested_model if call else None, "returned_model": call.returned_model if call else None,
            "prompt_id": prompt.prompt_id if prompt else None, "prompt_version": prompt.version if prompt else None,
            "prompt_label": prompt.label if prompt else None, "prompt_sha256": prompt.sha256 if prompt else None,
            "settings": call.settings if call else (prompt.settings if prompt else None), "finish_reason": call.finish_reason if call else None})


# ── persistence (every function runs inside the caller's transaction and locks the job's criteria row) ────────────────────────────────
@dataclass
class Locked:
    job_id: str
    tenant_id: str
    marker: Any
    status: str
    token: str | None
    original_present: bool
    revision: int
    analysis: Any
    started_at: Any


async def lock_criteria(db, job_id: str) -> Locked | None:
    row = (await db.execute(text("""
        SELECT jc.job_id, j.tenant_id, jc.requirements_schema_version AS marker, jc.criteria_extraction_status AS status,
               jc.requirements_extraction_token AS token, (jc.original_analysis_json IS NOT NULL) AS original_present,
               jc.requirements_revision AS revision, jc.analysis_json AS analysis, jc.requirements_extraction_started_at AS started_at
        FROM job_criteria jc JOIN jobs j ON j.job_id = jc.job_id
        WHERE jc.job_id = CAST(:jid AS uuid) FOR UPDATE OF jc
    """), {"jid": str(job_id)})).mappings().first()
    if row is None:
        return None
    return Locked(str(row["job_id"]), str(row["tenant_id"]), row["marker"], row["status"], str(row["token"]) if row["token"] else None,
                  bool(row["original_present"]), int(row["revision"]), row["analysis"], row["started_at"])


def _stale_reason(lk: Locked, token: str) -> str | None:
    if lk.marker != 2:
        return "not_requirements_v2"
    if lk.original_present:
        return "original_snapshot_exists"
    if lk.token != token:
        return "superseded_attempt"
    if lk.status != "processing":
        return f"status_{lk.status}"
    return None


async def claim_attempt(db, job_id: str, token: str) -> bool:
    """Take the attempt. Allowed from pending or failed, or from processing with the SAME token (a Celery retry of this attempt), or from
    processing when the previous claim is older than STALE_AFTER_S (a crashed worker). Never when a snapshot exists."""
    res = await db.execute(text("""
        UPDATE job_criteria SET
            criteria_extraction_status = 'processing',
            requirements_extraction_token = CAST(:tok AS uuid),
            requirements_extraction_started_at = now(),
            criteria_extraction_error = NULL
        WHERE job_id = CAST(:jid AS uuid)
          AND requirements_schema_version = 2
          AND original_analysis_json IS NULL
          AND (
                criteria_extraction_status IN ('pending', 'failed')
             OR (criteria_extraction_status = 'processing' AND requirements_extraction_token = CAST(:tok AS uuid))
             OR (criteria_extraction_status = 'processing'
                 AND requirements_extraction_started_at < now() - make_interval(secs => :stale))
          )
    """), {"jid": str(job_id), "tok": token, "stale": STALE_AFTER_S})
    return getattr(res, "rowcount", 0) == 1


async def finish_success(db, *, job_id: str, token: str, state: dict, call: CallResult, prompt: PromptRef, policy: bool, actor_tenant: str) -> str:
    """Write the draft, its snapshot, the pipeline record and the weight columns, bump the revision, and audit — all in the caller's transaction.
    Returns 'completed' or 'stale'. A stale result changes nothing."""
    from services.audit_service import log_action
    lk = await lock_criteria(db, job_id)
    if lk is None:
        return "stale"
    reason = _stale_reason(lk, token)
    if reason:
        await log_action(db, actor_tenant, None, None, "requirements_extraction_stale_discarded", resource_type="job", resource_id=str(job_id),
                         details={"job_id": str(job_id), "reason": reason, "returned_model": call.returned_model}, strict=False)
        return "stale"
    doc = state["requirements"]
    if not weights_ok(doc):                                     # the category columns are constrained to 100 (or all 0 for v2)
        raise PipelineError("The category weights do not total 100 (or 0).", "weights_not_normalized")
    # Accept the result only if the description and the context are still what the model was given. Otherwise nothing is written: the caller
    # records a failed attempt, and a new attempt (requested by a person) sends the current inputs.
    desc_now, meta_now = await current_inputs(db, job_id)
    if desc_now is None and meta_now is None:
        return "stale"
    changed = NI.changed_fields(state["model_inputs"], NI.model_inputs(desc_now, meta_now))
    if changed:
        await log_action(db, actor_tenant, None, None, "requirements_extraction_inputs_changed", resource_type="job", resource_id=str(job_id),
                         details={"job_id": str(job_id), "attempt_token": token, "changed_fields": changed}, strict=False)
        return "inputs_changed"
    record = pipeline_core.to_record_v2(state, model=call.returned_model or call.requested_model, model_inputs=state["model_inputs"],
                                        context_review=state["context_review"])
    analysis = {"requirements": copy.deepcopy(doc), STORAGE_KEY: record}
    original = {"requirements": copy.deepcopy(state["original"])}
    w = {c: doc["categories"][c]["weight"] for c in doc["categories"]}
    await db.execute(text("""
        UPDATE job_criteria SET
            analysis_json = CAST(:aj AS jsonb),
            original_analysis_json = CAST(:orig AS jsonb),
            weight_skills = :w_skills, weight_experience = :w_experience, weight_education = :w_education,
            weight_certifications = :w_certifications, weight_soft_skills = :w_soft_skills,
            weight_domain_knowledge = :w_domain_knowledge, weight_other = :w_other_requirements,
            requirements_revision = requirements_revision + 1,
            criteria_extraction_status = 'completed', criteria_extraction_error = NULL,
            criteria_extracted_at = now(), ai_model = :model, ai_generated_at = now(),
            requirements_extraction_token = NULL
        WHERE job_id = CAST(:jid AS uuid) AND requirements_extraction_token = CAST(:tok AS uuid)
    """), {"aj": json.dumps(analysis, ensure_ascii=False), "orig": json.dumps(original, ensure_ascii=False), "jid": str(job_id), "tok": token,
           "w_skills": w["skills"], "w_experience": w["experience"], "w_education": w["education"], "w_certifications": w["certifications"],
           "w_soft_skills": w["soft_skills"], "w_domain_knowledge": w["domain_knowledge"], "w_other_requirements": w["other_requirements"],
           "model": call.returned_model or call.requested_model})
    # the audit snapshot records everything this attempt ran with; readiness shown later is recomputed under the live policy
    await log_action(db, actor_tenant, None, None, "requirements_extraction_completed", resource_type="job", resource_id=str(job_id), strict=True,
                     details={"job_id": str(job_id), "attempt_token": token, "prompt_id": prompt.prompt_id, "prompt_code": PROMPT_CODE,
                              "prompt_version": prompt.version, "prompt_label": prompt.label, "prompt_sha256": prompt.sha256,
                              "requested_model": call.requested_model, "returned_model": call.returned_model, "settings": call.settings,
                              "usage": {"prompt_tokens": call.prompt_tokens, "completion_tokens": call.completion_tokens},
                              "acknowledgment_policy": bool(policy), "readiness_state": state["readiness"]["state"],
                              "original_digest": state["original_digest"], "revision": lk.revision + 1, "weights": w,
                              "model_inputs_sha256": state["model_inputs"]["sha256"], "context_findings": len(state["context_review"]["issues"])})
    return "completed"


async def finish_failure(db, *, job_id: str, token: str, code: str, message: str, actor_tenant: str, call: CallResult | None = None) -> str:
    from services.audit_service import log_action
    lk = await lock_criteria(db, job_id)
    if lk is None:
        return "stale"
    reason = _stale_reason(lk, token)
    if reason:
        await log_action(db, actor_tenant, None, None, "requirements_extraction_stale_discarded", resource_type="job", resource_id=str(job_id),
                         details={"job_id": str(job_id), "reason": reason, "failure": code}, strict=False)
        return "stale"
    await db.execute(text("""
        UPDATE job_criteria SET criteria_extraction_status = 'failed', criteria_extraction_error = :err,
                                requirements_extraction_token = NULL,
                                criteria_last_failed_at = now()
        WHERE job_id = CAST(:jid AS uuid) AND requirements_extraction_token = CAST(:tok AS uuid)
    """), {"err": (message or code)[:MAX_ATTEMPT_ERRORS], "jid": str(job_id), "tok": token})
    await log_action(db, actor_tenant, None, None, "requirements_extraction_failed", resource_type="job", resource_id=str(job_id), strict=True,
                     details={"job_id": str(job_id), "attempt_token": token, "code": code, "message": (message or "")[:500],
                              "returned_model": call.returned_model if call else None,
                              "finish_reason": call.finish_reason if call else None})
    return "failed"


async def request_retry(db, *, job_id: str, actor_user: str, actor_tenant: str) -> str | None:
    """Start a new attempt on a job that has no document and whose attempt is pending (queued, not yet running), failed, or processing but stuck.
    Returns the new token, or None when a retry is not allowed right now (a live attempt is running, or the document exists). The superseded
    attempt's token no longer matches, so its late task is refused at claim. The caller checks the role and the feature switch, and enqueues the
    task after commit."""
    from services.audit_service import log_action
    lk = await lock_criteria(db, job_id)
    if lk is None or lk.marker != 2 or lk.original_present:
        return None
    stuck = lk.status == "processing" and lk.started_at is not None and (time.time() - lk.started_at.timestamp()) > STALE_AFTER_S
    if lk.status not in ("pending", "failed") and not stuck:
        return None
    token = str(uuid.uuid4())
    await db.execute(text("""
        UPDATE job_criteria SET criteria_extraction_status = 'pending', requirements_extraction_token = CAST(:tok AS uuid),
                                requirements_extraction_started_at = NULL, criteria_extraction_error = NULL
        WHERE job_id = CAST(:jid AS uuid)
    """), {"tok": token, "jid": str(job_id)})
    await log_action(db, actor_tenant, actor_user, None, "requirements_extraction_retry_requested", resource_type="job", resource_id=str(job_id),
                     strict=True, details={"job_id": str(job_id), "new_attempt_token": token, "previous_status": lk.status, "stuck": stuck})
    return token


# ── versions, candidates and superseded drafts: the append-only log (migration 109) ────────────────────────────────────────────────
# Every event of every analysis version is one row of requirements_analysis_log; the database refuses updates and deletes. The version that the
# original extraction produced has no 'requested' row: its identity is derived from the job (initial_version_id) and its data stays in job_criteria.
# A version's status is the most recent decision recorded for it (see version_status).

_INITIAL_NS = uuid.UUID("6f1c2a3e-8d4b-4c59-9a7e-2b1d3f5e7a90")
TERMINAL_EVENTS = ("applied", "discarded")


def initial_version_id(job_id) -> str:
    """The stable identity of the version the original extraction produced (version 1)."""
    return str(uuid.uuid5(_INITIAL_NS, f"{job_id}/1"))


def _as_json(value: Any) -> Any:
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    return value


async def append_log(db, *, job_id: str, tenant_id: str, version_id: str, version_no: int, kind: str, event: str,
                     request_key: str | None = None, attempt_token: str | None = None, actor_user_id: str | None = None,
                     job_revision: int | None = None, input_sha256: str | None = None, payload: Any = None,
                     details: dict | None = None) -> None:
    """Append one event. The database refuses a second 'requested', 'completed', 'rejected', 'applied', 'discarded' or 'superseded' for a version, and a
    second terminal decision (applied or discarded) for a candidate; the caller's transaction is then rolled back."""
    await db.execute(text("""
        INSERT INTO requirements_analysis_log (job_id, tenant_id, version_id, version_no, kind, event, request_key, attempt_token, actor_user_id,
                                               job_revision, input_sha256, payload, details)
        VALUES (CAST(:jid AS uuid), CAST(:tid AS uuid), CAST(:vid AS uuid), :vno, :kind, :event, CAST(:rk AS uuid), CAST(:tok AS uuid),
                CAST(:uid AS uuid), :rev, :sha, CAST(:payload AS jsonb), CAST(:details AS jsonb))
    """), {"jid": str(job_id), "tid": str(tenant_id), "vid": version_id, "vno": version_no, "kind": kind, "event": event,
           "rk": request_key, "tok": attempt_token, "uid": actor_user_id, "rev": job_revision, "sha": input_sha256,
           "payload": None if payload is None else json.dumps(payload, ensure_ascii=False),
           "details": json.dumps(details or {}, ensure_ascii=False)})


async def read_versions(db, job_id: str) -> list[dict]:
    """Every version of the job, in the order they were first recorded, each with its events by name and its status."""
    rows = (await db.execute(text("""
        SELECT version_id::text AS version_id, version_no, kind, event, request_key::text AS request_key, attempt_token::text AS attempt_token,
               actor_user_id::text AS actor_user_id, job_revision, input_sha256, payload, details, created_at
        FROM requirements_analysis_log WHERE job_id = CAST(:jid AS uuid) ORDER BY log_id
    """), {"jid": str(job_id)})).mappings().all()
    versions: dict[str, dict] = {}
    for r in rows:
        v = versions.setdefault(r["version_id"], {"version_id": r["version_id"], "version_no": int(r["version_no"]), "kind": r["kind"], "events": {}})
        v["events"][r["event"]] = {"request_key": r["request_key"], "attempt_token": r["attempt_token"], "actor_user_id": r["actor_user_id"],
                                   "job_revision": r["job_revision"], "input_sha256": r["input_sha256"], "payload": _as_json(r["payload"]),
                                   "details": _as_json(r["details"]) or {}, "created_at": r["created_at"]}
    out = list(versions.values())
    for v in out:
        v["status"] = version_status(v["events"])
    return out


def version_status(events: dict) -> str:
    if "superseded" in events:
        return "superseded"
    for name in ("applied", "discarded", "rejected", "completed", "requested"):
        if name in events:
            return name
    return "initial"


def open_candidate(versions: list[dict]) -> dict | None:
    """The one candidate that is requested or completed and not yet decided (at most one at a time; the request checks this under the job lock)."""
    for v in versions:
        if v["kind"] == "candidate" and v["status"] in ("requested", "completed"):
            return v
    return None


async def next_version_no(db, job_id: str) -> int:
    row = (await db.execute(text("SELECT COALESCE(MAX(version_no), 1) + 1 FROM requirements_analysis_log WHERE job_id = CAST(:jid AS uuid)"),
                            {"jid": str(job_id)})).scalar_one()
    return int(row)


CODE_INPUTS_CHANGED_DURING_CALL = "inputs_changed_during_call"


async def finish_candidate(db, *, job_id: str, version_id: str, token: str, state: dict, call: CallResult, prompt: PromptRef, policy: bool,
                           actor_tenant: str) -> str:
    """Record the outcome of a candidate's model call in ONE transaction under the job's locks (criteria row first, then the job row, as the first
    extraction does). Completed only if the description and the context are still what the model was given; otherwise rejected, with nothing stored
    as a candidate. Returns 'completed', 'rejected' or 'stale' (the request is no longer open for this token)."""
    from services.audit_service import log_action
    lk = await lock_criteria(db, job_id)
    if lk is None or lk.marker != 2:
        return "stale"
    cand = next((v for v in await read_versions(db, job_id) if v["version_id"] == version_id), None)
    if cand is None or "requested" not in cand["events"] or cand["status"] != "requested":
        return "stale"
    if cand["events"]["requested"]["attempt_token"] != token:
        return "stale"
    desc_now, meta_now = await current_inputs(db, job_id)
    if desc_now is None and meta_now is None:
        return "stale"
    changed = NI.changed_fields(state["model_inputs"], NI.model_inputs(desc_now, meta_now))
    req = cand["events"]["requested"]
    if changed:
        await append_log(db, job_id=job_id, tenant_id=actor_tenant, version_id=version_id, version_no=cand["version_no"], kind="candidate",
                         event="rejected", attempt_token=token, job_revision=lk.revision, input_sha256=state["model_inputs"]["sha256"],
                         details={"code": CODE_INPUTS_CHANGED_DURING_CALL, "changed_fields": changed, "returned_model": call.returned_model})
        await log_action(db, actor_tenant, None, None, "requirements_analysis_rejected", resource_type="job", resource_id=str(job_id), strict=True,
                         details={"job_id": str(job_id), "version_id": version_id, "version_no": cand["version_no"], "code": CODE_INPUTS_CHANGED_DURING_CALL,
                                  "changed_fields": changed, "input_sha256": state["model_inputs"]["sha256"], "revision": lk.revision})
        return "rejected"
    record = pipeline_core.to_record_v2(state, model=call.returned_model or call.requested_model, model_inputs=state["model_inputs"],
                                        context_review=state["context_review"])
    payload = {"original": state["original"], "record": record, "document": state["requirements"], "model_inputs": state["model_inputs"],
               "context_review": state["context_review"]}
    await append_log(db, job_id=job_id, tenant_id=actor_tenant, version_id=version_id, version_no=cand["version_no"], kind="candidate",
                     event="completed", attempt_token=token, job_revision=lk.revision, input_sha256=state["model_inputs"]["sha256"], payload=payload,
                     details={"returned_model": call.returned_model, "requested_model": call.requested_model, "prompt_sha256": prompt.sha256,
                              "job_revision_at_request": req["job_revision"], "job_revision_at_completion": lk.revision,
                              "context_findings": len(state["context_review"]["issues"])})
    await log_action(db, actor_tenant, None, None, "requirements_analysis_completed", resource_type="job", resource_id=str(job_id), strict=True,
                     details={"job_id": str(job_id), "version_id": version_id, "version_no": cand["version_no"], "attempt_token": token,
                              "input_sha256": state["model_inputs"]["sha256"], "revision": lk.revision,
                              "job_revision_at_request": req["job_revision"], "returned_model": call.returned_model})
    return "completed"


async def finish_candidate_failure(db, *, job_id: str, version_id: str, token: str, code: str, message: str, actor_tenant: str,
                                   call: CallResult | None = None) -> str:
    """A candidate whose model call failed or whose output could not be used: rejected, with the reason. The job's draft is not touched."""
    from services.audit_service import log_action
    lk = await lock_criteria(db, job_id)
    if lk is None:
        return "stale"
    cand = next((v for v in await read_versions(db, job_id) if v["version_id"] == version_id), None)
    if cand is None or cand["status"] != "requested" or cand["events"]["requested"]["attempt_token"] != token:
        return "stale"
    await append_log(db, job_id=job_id, tenant_id=actor_tenant, version_id=version_id, version_no=cand["version_no"], kind="candidate",
                     event="rejected", attempt_token=token, job_revision=lk.revision,
                     details={"code": code, "message": (message or "")[:500], "returned_model": call.returned_model if call else None})
    await log_action(db, actor_tenant, None, None, "requirements_analysis_rejected", resource_type="job", resource_id=str(job_id), strict=True,
                     details={"job_id": str(job_id), "version_id": version_id, "version_no": cand["version_no"], "code": code, "revision": lk.revision})
    return "rejected"
