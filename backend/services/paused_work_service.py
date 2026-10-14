"""Pause and resume CV scoring while a tenant has no access. Never a failure, never a retry storm, never duplicate work.

Pause: the scoring task checks entitlement before it starts. If the tenant is not entitled, the application STAYS 'queued' and is marked
stopped_reason = 'access_paused'. The task then returns without raising, so Celery does not retry it. The watchdog ignores rows marked
access_paused, so they are not moved to 'failed' as stuck.

Resume: after platform provisioning makes the tenant entitled, resume_paused_applications claims the paused rows in ONE atomic UPDATE. A row
is claimed only while it still carries the access_paused marker, so a second resume (or a concurrent one) claims nothing. The marker is
cleared and queued_at is reset in that same statement, then the scoring task is dispatched once per claimed row. If a dispatch fails, the row
is re-marked as paused, so it is not left queued without a worker.
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.entitlement_service import load_entitlement

logger = logging.getLogger(__name__)

PAUSED_REASON = "access_paused"


async def pause_if_not_entitled(db: AsyncSession, application_id: str, tenant_id: str) -> bool:
    """Return True when scoring may proceed. Otherwise mark the application paused (it stays queued) and return False.
    The caller sets the RLS context and does not commit; this function commits the marker itself."""
    entitlement = await load_entitlement(db, tenant_id)
    if entitlement.full:
        return True
    await db.execute(
        text("""
            UPDATE applications SET stopped_reason = :reason
            WHERE application_id = CAST(:aid AS uuid) AND processing_status = 'queued'
        """),
        {"reason": PAUSED_REASON, "aid": application_id},
    )
    await db.commit()
    logger.info("[%s] scoring paused: tenant is not entitled (%s)", application_id, entitlement.state)
    return False


async def resume_paused_applications(db: AsyncSession, tenant_id: str) -> int:
    """Re-dispatch the paused applications of one tenant, once each, if the tenant is entitled now. Returns how many were dispatched.
    Never raises: a failure is logged and the affected rows are re-paused."""
    entitlement = await load_entitlement(db, tenant_id)
    if not entitlement.full:
        return 0

    claimed = (await db.execute(
        text("""
            WITH claimed AS (
                UPDATE applications a
                SET stopped_reason = NULL, queued_at = now()
                WHERE a.processing_status = 'queued'
                  AND a.stopped_reason = :reason
                  AND a.job_id IN (SELECT j.job_id FROM jobs j WHERE j.tenant_id = CAST(:tid AS uuid))
                  AND EXISTS (SELECT 1 FROM application_files f WHERE f.application_id = a.application_id)
                RETURNING a.application_id, a.job_id
            )
            SELECT c.application_id::text AS application_id, c.job_id::text AS job_id,
                   f.file_path, f.mime_type
            FROM claimed c
            JOIN LATERAL (
                SELECT file_path, mime_type FROM application_files af
                WHERE af.application_id = c.application_id
                ORDER BY af.created_at
                LIMIT 1
            ) f ON true
        """),
        {"reason": PAUSED_REASON, "tid": tenant_id},
    )).mappings().all()
    await db.commit()
    if not claimed:
        return 0

    from workers.cv_score import score_cv_task  # noqa: PLC0415 (deferred: workers import services)

    dispatched = 0
    for row in claimed:
        try:
            score_cv_task.delay(
                application_id=row["application_id"],
                job_id=row["job_id"],
                tenant_id=tenant_id,
                file_path=row["file_path"],
                mime_type=row["mime_type"],
            )
            dispatched += 1
        except Exception as exc:  # noqa: BLE001 - re-pause this row; the rest continue
            logger.error("[%s] resume dispatch failed, re-pausing: %s", row["application_id"], exc)
            await db.execute(
                text("UPDATE applications SET stopped_reason = :reason WHERE application_id = CAST(:aid AS uuid)"),
                {"reason": PAUSED_REASON, "aid": row["application_id"]},
            )
            await db.commit()
    return dispatched
