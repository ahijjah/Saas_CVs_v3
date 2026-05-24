"""
Watchdog task: mark stuck scoring jobs as failed.

Any application that has been in 'queued' or 'processing' status for more
than 15 minutes is considered stuck (worker crash, network hang, OOM, etc.)
and is automatically transitioned to 'failed' so that:

  - The manual-upload page stops polling and re-enables the submit button.
  - Operators can see the failure reason and re-queue if needed.
  - Stats dashboards are not inflated by ghost "processing" counts.

This task is registered in celery_app.beat_schedule and runs every 5 minutes.
"""
import asyncio
import logging

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_STUCK_TIMEOUT_MINUTES = 15
_WATCHDOG_REASON = (
    f"Application was stuck in processing/queued status for more than "
    f"{_STUCK_TIMEOUT_MINUTES} minutes and was automatically marked as failed "
    f"by the watchdog. The worker may have crashed or been restarted. "
    f"Please re-queue this application if needed."
)


@celery_app.task(name="workers.watchdog.reset_stuck_scoring_tasks")
def reset_stuck_scoring_tasks() -> None:
    """Periodic task: find stuck applications and mark them failed."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        count = loop.run_until_complete(_reset_stuck_async())
        if count:
            logger.warning("Watchdog marked %d stuck application(s) as failed.", count)
        else:
            logger.debug("Watchdog: no stuck applications found.")
    except Exception as exc:
        logger.error("Watchdog task failed: %s", exc, exc_info=True)
    finally:
        try:
            loop.close()
        except Exception:
            pass
        asyncio.set_event_loop(None)


async def _reset_stuck_async() -> int:
    """
    Update stuck applications in a single bulk query.
    Returns the number of rows updated.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text
    from config import get_settings

    cfg = get_settings()

    engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with Session() as db:
            # Bypass RLS — watchdog operates across all tenants
            await db.execute(text(
                "SELECT set_config('app.current_tenant_id', '', true), "
                "set_config('app.current_role', 'super_admin', true)"
            ))

            result = await db.execute(
                text("""
                    UPDATE applications
                    SET processing_status      = 'failed',
                        stopped_reason         = 'processing_error',
                        evaluation_exit_reason = :reason,
                        scored_at              = now()
                    WHERE processing_status IN ('queued', 'processing')
                      AND queued_at < now() - (:timeout * INTERVAL '1 minute')
                    RETURNING application_id
                """),
                {"reason": _WATCHDOG_REASON, "timeout": _STUCK_TIMEOUT_MINUTES},
            )
            rows = result.fetchall()

            await db.execute(
                text("""
                    UPDATE application_files
                    SET extraction_status = 'failed'
                    WHERE application_id IN (
                        SELECT application_id FROM applications
                        WHERE processing_status = 'failed'
                          AND evaluation_exit_reason = :reason
                          AND scored_at >= now() - INTERVAL '1 minute'
                    )
                    AND extraction_status = 'pending'
                """),
                {"reason": _WATCHDOG_REASON},
            )

            await db.commit()

            for row in rows:
                logger.info("Watchdog: marked application %s as failed (stuck).", row[0])

            return len(rows)
    except Exception as exc:
        logger.error("_reset_stuck_async failed: %s", exc, exc_info=True)
        return 0
    finally:
        await engine.dispose()
