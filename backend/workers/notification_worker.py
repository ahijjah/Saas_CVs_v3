"""
Celery task: send a pending notification_log entry.

One task per notification_id. Reads the row, builds the email, sends it,
then marks the row sent or failed. Retries up to 3 times on transient errors
with a 60-second delay.

EVENT LOOP SAFETY
-----------------
Uses a fresh NullPool engine per asyncio.run() call — same pattern as
cv_intake.py — to avoid "Future is attached to a different loop" errors
when Celery prefork workers fork after module import.
"""
import asyncio
import logging

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="workers.notification_worker.send_notification_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_notification_task(self, notification_id: str) -> None:
    """Send the outbound email for one notification_log row."""
    try:
        asyncio.run(_send_async(notification_id))
    except Exception as exc:
        logger.error(
            "send_notification_task failed for notification=%s: %s",
            notification_id, exc, exc_info=True,
        )
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            # Best-effort: mark failed without losing the original error
            try:
                asyncio.run(_mark_failed(notification_id, str(exc)))
            except Exception as mf_exc:
                logger.error("Could not mark notification %s as failed: %s", notification_id, mf_exc)


# ── Async send implementation ─────────────────────────────────────────────────

async def _send_async(notification_id: str) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text
    from config import get_settings
    from services.email_service import _send as smtp_send
    from services.intake_notification_service import (
        IntakeStatus,
        build_candidate_email,
        build_recruiter_inactive_email,
        build_recruiter_unmatched_email,
    )

    cfg = get_settings()
    engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    make_session = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    try:
        async with make_session() as db:
            # Read the notification row (no RLS needed — super_admin context)
            from database import set_rls_context
            await set_rls_context(db, "", "super_admin")

            row = await db.execute(
                text("""
                    SELECT notification_id, notification_type, intake_status,
                           recipient_email, recipient_type,
                           tenant_id, job_id,
                           details, status, retry_count
                    FROM notification_log
                    WHERE notification_id = :nid
                """),
                {"nid": notification_id},
            )
            notif = row.mappings().first()

            if not notif:
                logger.warning("Notification not found: %s", notification_id)
                return

            if notif["status"] == "sent":
                logger.debug("Notification already sent: %s", notification_id)
                return

            details   = notif["details"] or {}
            ntype     = notif["notification_type"]
            istatus   = IntakeStatus(notif["intake_status"])
            recipient = notif["recipient_email"]
            from_name = cfg.email_from_name

            # Build email content based on notification type
            if ntype == "CANDIDATE_INTAKE":
                sender_name = details.get("sender_name") or ""
                job_title   = details.get("job_title")   or ""
                attachments = details.get("attachments") or []

                # Reconstruct AttachmentResult objects for MULTIPLE_ATTACHMENTS template
                from services.intake_notification_service import AttachmentResult
                results = [
                    AttachmentResult(
                        filename=a.get("filename"),
                        status=IntakeStatus(a["status"]),
                        application_id=a.get("application_id"),
                        duplicate_log_id=a.get("duplicate_log_id"),
                        error_detail=a.get("error_detail"),
                    )
                    for a in attachments
                    if "status" in a
                ]

                subject, text_body, html_body = build_candidate_email(
                    istatus, sender_name, job_title, results, from_name,
                )

            elif ntype == "RECRUITER_UNMATCHED_ALERT":
                count     = details.get("unmatched_count", 0)
                threshold = details.get("threshold", 50)
                subject, text_body, html_body = build_recruiter_unmatched_email(
                    count, threshold, from_name,
                )

            elif ntype == "RECRUITER_INACTIVE_ALERT":
                job_title      = details.get("job_title")      or "Unknown Position"
                recipient_name = details.get("recipient_name") or ""
                subject, text_body, html_body = build_recruiter_inactive_email(
                    job_title, recipient_name, from_name,
                )

            else:
                logger.error("Unknown notification_type '%s' for id=%s", ntype, notification_id)
                await _update_status(db, notification_id, "failed", "Unknown notification_type")
                await db.commit()
                return

            # Send
            try:
                await smtp_send(recipient, subject, html_body, text_body)
            except Exception as smtp_exc:
                logger.error(
                    "SMTP error for notification %s to %s: %s",
                    notification_id, recipient, smtp_exc,
                )
                await _update_status(
                    db, notification_id, "failed",
                    f"SMTP error: {smtp_exc}",
                    increment_retry=True,
                )
                await db.commit()
                raise  # re-raise so Celery can retry the task

            # Mark sent
            await _update_status(db, notification_id, "sent")
            await db.commit()

            logger.info(
                "Notification sent: id=%s type=%s status=%s to=%s",
                notification_id, ntype, istatus.value, recipient,
            )

    finally:
        await engine.dispose()


async def _update_status(
    db,
    notification_id: str,
    status: str,
    error_message: str | None = None,
    increment_retry: bool = False,
) -> None:
    from sqlalchemy import text
    await db.execute(
        text("""
            UPDATE notification_log
            SET status        = :status,
                error_message = :err,
                sent_at       = CASE WHEN :status = 'sent' THEN NOW() ELSE NULL END,
                retry_count   = retry_count + :inc
            WHERE notification_id = :nid
        """),
        {
            "nid":    notification_id,
            "status": status,
            "err":    error_message,
            "inc":    1 if increment_retry else 0,
        },
    )


async def _mark_failed(notification_id: str, error_message: str) -> None:
    """Standalone failure marker — used after MaxRetriesExceededError."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from config import get_settings
    from database import set_rls_context

    cfg = get_settings()
    engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    make_session = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False,
    )
    try:
        async with make_session() as db:
            await set_rls_context(db, "", "super_admin")
            await _update_status(db, notification_id, "failed", error_message)
            await db.commit()
    finally:
        await engine.dispose()
