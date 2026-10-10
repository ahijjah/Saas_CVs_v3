"""
Requirements-v2 extraction stage: prompt and model resolution, the model call, the production pipeline, and the transactional persistence of an attempt.

Everything here is used ONLY by workers/requirements_v2_extraction_worker.py (the model call and the write) and services/requirements_api.py
(the retry request). It never touches the legacy criteria path, its prompt key (`criteria_extraction`), its stage (`cv_analyzer`) or its
settings.

FEATURE SWITCH: system_config 'requirements_v2.enabled' (default 'false'). Read at job creation and again at worker start; a switch that is off
means no v2 job is created and no model is called.

PROMPT: ai_prompts row (prompt_code 'criteria_extraction_v2', integer version N) is the configurable source of the text. The text must hash to a
value on APPROVED_PROMPTS (a code allowlist, not configuration), and its temperature / max_tokens must match the approved settings. A row that
is inactive, missing, unapproved or differently configured is refused before any call.

MODEL: the stage 'requirements_v2_extraction' in the model registry (no fallback to a legacy model). The model that answered is recorded from the
response, next to the requested model.

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
from services.requirements_pipeline import core as pipeline_core
from services.requirements_v2.extraction.prompt import EXTRACTION_CONFIG, build_user_message

logger = logging.getLogger(__name__)

FEATURE_KEY = "requirements_v2.enabled"
STAGE = "requirements_v2_extraction"
PROMPT_CODE = "criteria_extraction_v2"
PROVIDER = "openai"
MODEL_SETTINGS = {"temperature": 0.1, "max_tokens": 6000, "response_format": {"type": "json_object"}}
# The approved text(s). Adding a version is a code change with a review, never a database edit.
APPROVED_PROMPTS = {
    3: {"label": "criteria_extraction_v2-3", "sha256": "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"},
}
STALE_AFTER_S = 30 * 60                       # a claimed attempt that never finished can be retried after this
PER_CALL_TIMEOUT_S = 90                       # the approved per-call timeout (ev.PLAN["per_call_timeout_s"] in the benchmark)
MAX_ATTEMPT_ERRORS = 2000

# error codes that are terminal (no retry, the attempt is failed with the code); everything else is a transport error and may retry
TERMINAL_CALL_KINDS = ("auth", "model_unavailable", "bad_request")
RETRYABLE_CALL_KINDS = ("rate_limit", "timeout", "transport", "http_error_5xx")


class ExtractionUnavailable(Exception):
    """A precondition of the v2 extraction is not met (feature off, prompt not approved, stage not configured). `code` is stable."""

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
    label: str
    sha256: str
    system_prompt: str
    temperature: float
    max_tokens: int


@dataclass
class CallResult:
    raw: str
    finish_reason: str | None
    requested_model: str
    returned_model: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    settings: dict = field(default_factory=lambda: copy.deepcopy(MODEL_SETTINGS))


# ── feature switch ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
async def feature_enabled(db) -> bool:
    """Exactly "true" (trimmed, lower-cased) enables it. A missing row, a read error or any other value means OFF."""
    try:
        row = await db.execute(text("SELECT value FROM system_config WHERE key = :k"), {"k": FEATURE_KEY})
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
    """The active v2 prompt, verified against the approved list and the approved settings. Raises ExtractionUnavailable."""
    row = (await db.execute(text("""
        SELECT prompt_id, version, system_prompt, temperature, max_tokens, is_active
        FROM ai_prompts WHERE prompt_code = :code AND is_active = TRUE
        ORDER BY version DESC LIMIT 1
    """), {"code": PROMPT_CODE})).mappings().first()
    if row is None:
        raise ExtractionUnavailable("prompt_not_active", f"No active {PROMPT_CODE} prompt is configured.")
    version = int(row["version"])
    approved = APPROVED_PROMPTS.get(version)
    if approved is None:
        raise ExtractionUnavailable("prompt_not_approved", f"{PROMPT_CODE} version {version} is not on the approved list.")
    digest = hashlib.sha256((row["system_prompt"] or "").encode("utf-8")).hexdigest()
    if digest != approved["sha256"]:
        raise ExtractionUnavailable("prompt_hash_mismatch", f"The stored text of {approved['label']} does not match its approved hash.")
    if (float(row["temperature"]), int(row["max_tokens"])) != (MODEL_SETTINGS["temperature"], MODEL_SETTINGS["max_tokens"]):
        raise ExtractionUnavailable("prompt_settings_mismatch", "The prompt row's temperature / max_tokens differ from the approved settings.")
    return PromptRef(str(row["prompt_id"]), version, approved["label"], digest, row["system_prompt"],
                     float(row["temperature"]), int(row["max_tokens"]))


# ── model ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
async def resolve_model(db):
    """The stage's registry model and client. No fallback to settings or to a legacy stage: any failure refuses the call."""
    from services.ai_model_registry_service import resolve_stage_client
    resolved = await resolve_stage_client(db, STAGE)
    if resolved is None or resolved.client is None:
        raise ExtractionUnavailable("model_not_configured", f"No usable model is configured for the {STAGE} stage.")
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
    """One chat completion with the approved settings. Maps every provider error to a TransportError; never keeps a provider message."""
    started = time.monotonic()
    try:
        response = await client.with_options(timeout=timeout_s, max_retries=0).chat.completions.create(
            model=requested_model, messages=messages, temperature=prompt.temperature, max_tokens=prompt.max_tokens,
            response_format=dict(MODEL_SETTINGS["response_format"]))
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
                      latency_ms=int((time.monotonic() - started) * 1000))


# ── analysis: the production pipeline (the approved one, unchanged) ────────────────────────────────────────────────────────────────
def analyse(jd_text: str, call: CallResult, prompt: PromptRef, *, require_classification_acknowledgment: bool) -> dict:
    from parser_candidates.requirements_v2_pipeline_1 import extract
    return extract(jd_text, call.raw, finish_reason=call.finish_reason,
                   extraction_prompt={"version": prompt.label, "sha256": prompt.sha256},
                   require_classification_acknowledgment=require_classification_acknowledgment)


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
            "prompt_version": prompt.label if prompt else None, "prompt_sha256": prompt.sha256 if prompt else None,
            "settings": call.settings if call else MODEL_SETTINGS, "finish_reason": call.finish_reason if call else None})


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


async def finish_success(db, *, job_id: str, token: str, state: dict, call: CallResult, actor_tenant: str) -> str:
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
    record = pipeline_core.to_record(state, model=call.returned_model or call.requested_model)
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
    await log_action(db, actor_tenant, None, None, "requirements_extraction_completed", resource_type="job", resource_id=str(job_id), strict=True,
                     details={"job_id": str(job_id), "attempt_token": token, "prompt_version": PROMPT_CODE, "requested_model": call.requested_model,
                              "returned_model": call.returned_model, "settings": call.settings, "usage": {"prompt_tokens": call.prompt_tokens,
                              "completion_tokens": call.completion_tokens}, "readiness_state": state["readiness"]["state"],
                              "original_digest": state["original_digest"], "revision": lk.revision + 1, "weights": w})
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
