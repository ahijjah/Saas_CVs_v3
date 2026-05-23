"""
Celery task: backfill canonical_text_fingerprint for existing application_files.

Processes rows where canonical_text_fingerprint IS NULL and extracted_text IS NOT
NULL in batches, computing and persisting the fingerprint with the current
(enhanced) normalisation algorithm.

Event-loop safety: same NullPool pattern as cv_score.py.
"""
import asyncio
import logging

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="workers.backfill_canonical_fp.backfill_canonical_fingerprints_task",
    max_retries=0,
)
def backfill_canonical_fingerprints_task(self, batch_size: int = 200) -> dict:
    """
    Backfill canonical_text_fingerprint for all application_files rows where
    it is currently NULL.  Processes in batches to avoid memory pressure.

    Returns a summary dict: {"processed": int, "skipped": int, "errors": int}
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    from config import get_settings

    cfg = get_settings()
    engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            _backfill_async(Session, batch_size)
        )
        return result
    finally:
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:
            pass
        asyncio.set_event_loop(None)
        engine.dispose()


async def _backfill_async(Session, batch_size: int) -> dict:
    from services.duplicate_detection import compute_canonical_text_fingerprint
    from sqlalchemy import text

    processed = 0
    skipped = 0
    errors = 0
    offset = 0

    logger.info("Canonical fingerprint backfill starting (batch_size=%d)", batch_size)

    while True:
        async with Session() as db:
            # RLS bypass: super_admin with empty tenant sees all rows
            await db.execute(
                text(
                    "SELECT set_config('app.current_tenant_id', '', true), "
                    "       set_config('app.current_role', 'super_admin', true)"
                )
            )

            rows = await db.execute(
                text("""
                    SELECT application_id, extracted_text
                    FROM application_files
                    WHERE canonical_text_fingerprint IS NULL
                      AND extraction_status = 'done'
                      AND extracted_text IS NOT NULL
                      AND length(extracted_text) > 0
                    ORDER BY application_id
                    LIMIT :limit OFFSET :offset
                """),
                {"limit": batch_size, "offset": offset},
            )
            batch = rows.mappings().all()

        if not batch:
            break

        logger.info(
            "Backfill batch: offset=%d rows=%d", offset, len(batch)
        )

        for row in batch:
            aid = str(row["application_id"])
            try:
                fp = compute_canonical_text_fingerprint(row["extracted_text"])
                async with Session() as db:
                    await db.execute(
                        text(
                            "SELECT set_config('app.current_tenant_id', '', true), "
                            "       set_config('app.current_role', 'super_admin', true)"
                        )
                    )
                    result = await db.execute(
                        text("""
                            UPDATE application_files
                            SET canonical_text_fingerprint = :fp
                            WHERE application_id = :aid
                              AND canonical_text_fingerprint IS NULL
                        """),
                        {"fp": fp, "aid": aid},
                    )
                    await db.commit()
                    if result.rowcount > 0:
                        processed += 1
                    else:
                        skipped += 1
            except Exception as exc:
                errors += 1
                logger.error(
                    "Backfill failed for application_id=%s: %s", aid, exc
                )

        offset += batch_size

    logger.info(
        "Canonical fingerprint backfill complete: processed=%d skipped=%d errors=%d",
        processed, skipped, errors,
    )
    return {"processed": processed, "skipped": skipped, "errors": errors}
