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
    policy = None
    async with Session() as db:
        await set_rls_context(db, "", "super_admin")
        tenant = await _tenant_of(db, job_id)
        if not await ext.claim_attempt(db, job_id, token):
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
    state = ext.analyse(description, call, prompt, require_classification_acknowledgment=policy)
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
