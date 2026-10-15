"""
Celery task: requirements-v2 extraction for ONE attempt of a requirements-v2 job.

Separate from the legacy criteria worker (workers/criteria_worker.py), which is unchanged and which refuses v2 jobs.

Phases (each with its own short transaction; the model call is outside every transaction):
  A. feature switch (system_config 'requirements_v2.enabled'). Off -> the attempt is failed with code feature_disabled; no model call.
  B. claim the attempt: the job must be v2, have no original snapshot, be pending/failed (or processing with this same token, or stale). Otherwise the
     task is a no-op (superseded / already done).
  C. prompt (approved text, approved settings) and model (stage registry, no fallback). A refusal fails the attempt with its code; no model call.
  D. one model call with the approved settings and the configured timeout. Transport errors that may succeed later (rate limit, timeout, connection,
     5xx) are retried by Celery with the SAME token, up to MAX_RETRIES. Anything else fails the attempt.
  E. the approved production pipeline (services.requirements_pipeline via parser_candidates' extract). A malformed, truncated or empty response fails the
     attempt; it is never retried (the same prompt would likely produce it again; the recruiter can request a new attempt).
  F. persist: the draft, the immutable original snapshot, the pipeline record, the weight columns, the revision and the audit row, in ONE transaction that
     re-checks the attempt token under a row lock. A late result for a superseded attempt is discarded (audited as stale, nothing else changes).

A readiness blocker in the draft is NOT an extraction failure: the draft is stored with status 'completed' and the editor shows the blockers.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from config import get_settings
from database import set_rls_context
from services import requirements_v2_extraction as ext
from services.extraction_gate import job_entitlement
from services.requirements_api import load_policy
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)
MAX_RETRIES = 2
RETRY_DELAY_S = 30


def _session_factory(cfg):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    engine = create_async_engine(cfg.database_url, poolclass=NullPool, connect_args={"server_settings": {"search_path": cfg.db_schema}})
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _tenant_of(db, job_id: str) -> str | None:
    row = (await db.execute(text("SELECT tenant_id FROM jobs WHERE job_id = CAST(:jid AS uuid)"), {"jid": job_id})).scalar_one_or_none()
    return str(row) if row else None


async def run_attempt(job_id: str, token: str, description: str, job_metadata: dict | None, Session, *, retries_used: int) -> dict:
    """One pass through phases A-F. Returns {"outcome": "retry"|"completed"|"failed"|"stale", "code": ...}."""
    # ── A-C: feature, claim, prompt and model ─────────────────────────────────────────────────────────────────────────────────────────
    # Access gate, before any claim or model call: a tenant without current access leaves this attempt exactly as it is (pending, with its
    # token). Nothing is claimed or failed; resume dispatches it again once the tenant is entitled. See services/extraction_gate.py.
    async with Session() as gate_db:
        await set_rls_context(gate_db, "", "super_admin")
        if not (await job_entitlement(gate_db, job_id)).full:
            await gate_db.rollback()
            logger.info("[job:%s] requirements-v2 extraction paused: tenant is not entitled", job_id)
            return {"outcome": "paused", "code": "access_paused"}
    policy = None
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        tenant = await _tenant_of(db, job_id)
        if not await ext.claim_attempt(db, job_id, token, celery_retry=retries_used > 0):
            await db.rollback()
            return {"outcome": "stale", "code": "not_claimable"}
        if not await ext.feature_enabled(db):
            # the attempt is ours (claimed above): record why it did not run, so the recruiter can see it and retry once the switch is on
            await ext.finish_failure(db, job_id=job_id, token=token, code="feature_disabled",
                                     message="The requirements-v2 feature is switched off.", actor_tenant=tenant or "")
            await db.commit()
            return {"outcome": "failed", "code": "feature_disabled"}
        policy = await load_policy(db)                  # the live platform setting this attempt runs under (recorded in the audit)
        await db.commit()
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        try:
            prompt = await ext.load_prompt(db)
            resolved = await ext.resolve_model(db)
        except ext.ExtractionUnavailable as exc:
            await ext.finish_failure(db, job_id=job_id, token=token, code=exc.code, message=exc.message, actor_tenant=tenant or "")
            await db.commit()
            return {"outcome": "failed", "code": exc.code}
        requested, client = resolved.model_name, resolved.client
        await db.commit()
    messages = ext.build_messages(prompt, description, job_metadata)

    # ── D: the one model call ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
    try:
        call = await ext.call_model(client, prompt, messages, requested, timeout_s=ext.PER_CALL_TIMEOUT_S)
    except ext.TransportError as exc:
        async with Session() as db:
            await set_rls_context(db, "", "super_admin")
            await ext.record_usage(db, job_id=job_id, tenant_id=tenant, prompt=prompt, call=None, status="failed", error_type=exc.kind,
                                   retry_count=retries_used)
            if exc.retryable and retries_used < MAX_RETRIES:
                await db.commit()
                return {"outcome": "retry", "code": exc.kind}
            await ext.finish_failure(db, job_id=job_id, token=token, code=exc.kind,
                                     message=f"The model call failed ({exc.kind}{f', HTTP {exc.status}' if exc.status else ''}).", actor_tenant=tenant or "")
            await db.commit()
            return {"outcome": "failed", "code": exc.kind}

    # ── E: the production pipeline; malformed or truncated output fails the attempt (no retry) ──────────────────────────────────────────
    state = ext.analyse(description, call, prompt, require_classification_acknowledgment=policy, job_metadata=job_metadata)
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        await ext.record_usage(db, job_id=job_id, tenant_id=tenant, prompt=prompt, call=call, status="success", error_type=None, retry_count=retries_used)
        if not state["ok"]:
            code = (state["errors"][0]["code"] if state["errors"] else "extraction_failed")
            message = (state["errors"][0]["message"] if state["errors"] else "The model output could not be used.")
            await ext.finish_failure(db, job_id=job_id, token=token, code=code, message=message, actor_tenant=tenant or "", call=call)
            await db.commit()
            return {"outcome": "failed", "code": code}

        # ── F: persist, atomically, under the attempt token ───────────────────────────────────────────────────────────────────────────────
        try:
            result = await ext.finish_success(db, job_id=job_id, token=token, state=state, call=call, prompt=prompt, policy=policy,
                                              actor_tenant=tenant or "")
            if result == "inputs_changed":
                # the description or the context changed during the model call: nothing was written; the attempt fails and a person may request a new one
                await ext.finish_failure(db, job_id=job_id, token=token, code="inputs_changed_during_call",
                                         message="The job description or context changed while the analysis was running. Request a new analysis.",
                                         actor_tenant=tenant or "", call=call)
                await db.commit()
                return {"outcome": "failed", "code": "inputs_changed_during_call"}
            await db.commit()
        except Exception as exc:                                  # noqa: BLE001 - the whole write is rolled back, then the attempt is failed
            await db.rollback()
            code = getattr(exc, "code", None) or type(exc).__name__
            logger.error("[job:%s] requirements-v2 write rolled back: %s", job_id, code)
            async with Session() as db2:
                await set_rls_context(db2, "", "super_admin")
                await ext.finish_failure(db2, job_id=job_id, token=token, code=code, message=str(exc), actor_tenant=tenant or "", call=call)
                await db2.commit()
            return {"outcome": "failed", "code": code}
    return {"outcome": result, "code": None}


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


@celery_app.task(bind=True, max_retries=MAX_RETRIES, default_retry_delay=RETRY_DELAY_S, name="workers.requirements_v2_extraction_worker.extract_requirements_v2_task")
def extract_requirements_v2_task(self, job_id: str, attempt_token: str, description: str, job_metadata: dict | None = None) -> dict:
    cfg = get_settings()
    engine, Session = _session_factory(cfg)
    try:
        result = _run(run_attempt(job_id, attempt_token, description, job_metadata, Session, retries_used=self.request.retries))
    finally:
        _run(engine.dispose())
    if result["outcome"] == "retry":
        logger.warning("[job:%s] requirements-v2 extraction: transient %s; retrying (attempt %d)", job_id, result["code"], self.request.retries + 2)
        raise self.retry(exc=ext.TransportError(result["code"]), countdown=RETRY_DELAY_S)
    logger.info("[job:%s] requirements-v2 extraction: %s%s", job_id, result["outcome"], f" ({result['code']})" if result.get("code") else "")
    return result


# ── re-analysis: one candidate, generated beside the draft (migration 109) ──────────────────────────────────────────────────────────
# The same prompt, model and approved pipeline as the first extraction. The outcome is written ONLY as an event of the candidate in the append-only
# log: the draft, the job's original snapshot and the weight columns are never touched here. No retry: a failed candidate is rejected with its
# reason, and a person requests a new one.

async def run_candidate_attempt(job_id: str, version_id: str, token: str, description: str, job_metadata: dict | None, Session) -> dict:
    # Access gate first: a paused candidate stays requested (no started event, no decision) and resume dispatches it again
    async with Session() as gate_db:
        await set_rls_context(gate_db, "", "super_admin")
        if not (await job_entitlement(gate_db, job_id)).full:
            await gate_db.rollback()
            logger.info("[job:%s] requirements-v2 candidate paused: tenant is not entitled", job_id)
            return {"outcome": "paused", "code": "access_paused"}
    # A: the feature, the request that this attempt belongs to, the prompt and the model
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        tenant = await _tenant_of(db, job_id) or ""
        if not await ext.recovery_enabled(db):
            await ext.finish_candidate_failure(db, job_id=job_id, version_id=version_id, token=token, code="recovery_disabled",
                                               message="Re-analysis is switched off.", actor_tenant=tenant)
            await db.commit()
            return {"outcome": "rejected", "code": "recovery_disabled"}
        cand = next((v for v in await ext.read_versions(db, job_id) if v["version_id"] == version_id and v["kind"] == "candidate"), None)
        if cand is None or cand["status"] != "requested" or cand["events"]["requested"]["attempt_token"] != token:
            await db.rollback()
            return {"outcome": "stale", "code": "not_requested"}
        policy = await load_policy(db)
        try:
            prompt = await ext.load_prompt(db)
            resolved = await ext.resolve_model(db)
        except ext.ExtractionUnavailable as exc:
            await ext.finish_candidate_failure(db, job_id=job_id, version_id=version_id, token=token, code=exc.code, message=exc.message,
                                               actor_tenant=tenant)
            await db.commit()
            return {"outcome": "rejected", "code": exc.code}
        requested, client = resolved.model_name, resolved.client
        # the claim: recorded under the job's lock before the call, so a duplicate delivery of this task cannot make a second model call
        if not await ext.claim_candidate(db, job_id, version_id, token, actor_tenant=tenant):
            await db.rollback()
            return {"outcome": "stale", "code": "already_started"}
        await db.commit()
    messages = ext.build_messages(prompt, description, job_metadata)

    # D: the one model call (no retry)
    try:
        call = await ext.call_model(client, prompt, messages, requested, timeout_s=ext.PER_CALL_TIMEOUT_S)
    except ext.TransportError as exc:
        async with Session() as db:
            await set_rls_context(db, "", "super_admin")
            await ext.record_usage(db, job_id=job_id, tenant_id=tenant, prompt=prompt, call=None, status="failed", error_type=exc.kind, retry_count=0)
            await ext.finish_candidate_failure(db, job_id=job_id, version_id=version_id, token=token, code=exc.kind,
                                               message=f"The model call failed ({exc.kind}).", actor_tenant=tenant)
            await db.commit()
        return {"outcome": "rejected", "code": exc.kind}

    # E: the approved pipeline on the response; F: the outcome, under the job's locks
    state = ext.analyse(description, call, prompt, require_classification_acknowledgment=policy, job_metadata=job_metadata)
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        await ext.record_usage(db, job_id=job_id, tenant_id=tenant, prompt=prompt, call=call, status="success", error_type=None, retry_count=0)
        if not state["ok"]:
            code = state["errors"][0]["code"] if state["errors"] else "extraction_failed"
            message = state["errors"][0]["message"] if state["errors"] else "The model output could not be used."
            await ext.finish_candidate_failure(db, job_id=job_id, version_id=version_id, token=token, code=code, message=message,
                                               actor_tenant=tenant, call=call)
            await db.commit()
            return {"outcome": "rejected", "code": code}
        outcome = await ext.finish_candidate(db, job_id=job_id, version_id=version_id, token=token, state=state, call=call, prompt=prompt,
                                             policy=policy, actor_tenant=tenant)
        await db.commit()
    code = ext.CODE_INPUTS_CHANGED_DURING_CALL if outcome == "rejected" else None
    return {"outcome": outcome, "code": code}


@celery_app.task(name="workers.requirements_v2_extraction_worker.extract_candidate_task")
def extract_candidate_task(job_id: str, version_id: str, token: str, description: str, job_metadata: dict | None = None) -> dict:
    cfg = get_settings()
    engine, Session = _session_factory(cfg)
    try:
        result = _run(run_candidate_attempt(job_id, version_id, token, description, job_metadata, Session))
    finally:
        _run(engine.dispose())
    logger.info("[job:%s] requirements-v2 candidate %s: %s%s", job_id, version_id, result["outcome"], f" ({result['code']})" if result.get("code") else "")
    return result
