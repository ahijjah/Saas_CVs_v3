"""
E-01 Phase 5.1 — Bulk Upload Processing Service (counter synchronization fix).

Counters derive from the live applications JOIN, not from stale
bulk_upload_rows.processing_status.  Every GET /processing call persists
the live counts back to bulk_upload_batches so that the DB always reflects
the true processing state.

Deleted-duplicate handling: when Celery's exact-duplicate detection removes
an application from the DB, the LEFT JOIN returns NULL for that row.  Such
rows are now counted as terminal (they will never be scored).

E-01 Phase 4 — Bulk Upload Processing Service.

Provides monitoring functions that expose real-time processing progress for
bulk-upload batches.  Uses a pull-based approach: monitoring queries JOIN
bulk_upload_rows with the applications table to read live processing_status,
avoiding any need to modify the existing Celery scoring pipeline.

Processing stage mapping
────────────────────────
The existing pipeline's processing_status values map to stage labels:

  applications.processing_status → processing_stage  | progress%
  ──────────────────────────────   ──────────────────   ──────────
  pending                        → pending             0
  queued                         → queued              5
  processing                     → processing          50
  ai_scored                      → completed           100
  low_match                      → completed           100
  failed                         → failed              0

More granular sub-stages (extracting, duplicate_check, ai_analysis, etc.)
are not observable from outside the Celery task without modifying cv_score.py,
which is explicitly out of scope for Phase 4.

Batch completion detection
──────────────────────────
The batch is marked 'completed' lazily when the GET /processing endpoint
detects that all imported rows have reached a terminal application status
(ai_scored, low_match, failed).  This avoids needing a Celery callback.

Failure classification
──────────────────────
applications.stopped_reason  → processing_failure_reason label
────────────────────────────   ──────────────────────────────
extraction_failed            → extraction_failed
security_blocked             → extraction_failed
duplicate_exact              → duplicate_rejected
duplicate_possible           → duplicate_rejected
low_match_gatekeeper         → knockout_processing_failed  (not strictly KO)
ai_failed                    → ai_processing_failed
scoring_failed               → scoring_failed
<anything else / None>       → unexpected_error
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Stage / progress constants ────────────────────────────────────────────────

# Map applications.processing_status → bulk-upload stage label
_APP_STATUS_TO_STAGE: dict[str, str] = {
    "pending":    "pending",
    "queued":     "queued",
    "processing": "processing",
    "ai_scored":  "completed",
    "low_match":  "completed",
    "failed":     "failed",
}

# Map stage label → progress percentage
_STAGE_TO_PROGRESS: dict[str, int] = {
    "pending":    0,
    "queued":     5,
    "processing": 50,
    "completed":  100,
    "failed":     0,
}

# Terminal application statuses (no further processing expected)
_TERMINAL_APP_STATUSES = frozenset({"ai_scored", "low_match", "failed"})

# Map applications.stopped_reason → failure_reason label
_STOPPED_REASON_MAP: dict[str, str] = {
    "extraction_failed":      "extraction_failed",
    "security_blocked":       "extraction_failed",
    "duplicate_exact":        "duplicate_rejected",
    "duplicate_possible":     "duplicate_rejected",
    "low_match_gatekeeper":   "ai_processing_failed",
    "ai_failed":              "ai_processing_failed",
    "scoring_failed":         "scoring_failed",
}


# ── Pure helper functions (no DB / no I/O) ───────────────────────────────────

def app_status_to_stage(app_processing_status: str | None) -> str:
    """Map applications.processing_status to a processing_stage label."""
    if not app_processing_status:
        return "pending"
    return _APP_STATUS_TO_STAGE.get(app_processing_status, "pending")


def stage_to_progress(stage: str) -> int:
    """Map a processing_stage label to a 0-100 progress percentage."""
    return _STAGE_TO_PROGRESS.get(stage, 0)


def classify_failure_reason(stopped_reason: str | None) -> str | None:
    """
    Map applications.stopped_reason to a human-readable failure_reason label.
    Returns None if processing has not failed.
    """
    if not stopped_reason:
        return None
    return _STOPPED_REASON_MAP.get(stopped_reason, "unexpected_error")


def compute_batch_progress_percentage(
    total_imported: int,
    completed: int,
    failed: int,
    processing: int,
    queued: int,
) -> int:
    """
    Compute overall batch progress percentage.

    Uses a weighted sum based on each status group's progress:
      completed/failed → 100%, processing → 50%, queued → 5%, pending → 0%

    Returns 0 when total_imported is 0.
    """
    if total_imported <= 0:
        return 0
    terminal = completed + failed  # both count as "done" for progress
    weighted = (terminal * 100) + (processing * 50) + (queued * 5)
    return round(weighted / total_imported)


def is_batch_fully_processed(
    total_imported: int,
    terminal_count: int,
) -> bool:
    """Return True when all imported rows have reached a terminal state."""
    return total_imported > 0 and terminal_count >= total_imported


# ── Async query functions ─────────────────────────────────────────────────────

async def _persist_live_counts(
    db: AsyncSession,
    batch_id: str,
    *,
    queued: int,
    processing: int,
    completed: int,
    failed: int,
    imported: int,
) -> None:
    """Write live JOIN-derived counts to bulk_upload_batches."""
    await db.execute(
        text("""
            UPDATE bulk_upload_batches
               SET queued_row_count      = :queued,
                   processing_row_count  = :processing,
                   completed_row_count   = :completed,
                   failed_row_count      = :failed,
                   imported_row_count    = :imported,
                   updated_at            = now()
             WHERE batch_id = CAST(:bid AS uuid)
        """),
        {
            "bid":        batch_id,
            "queued":     queued,
            "processing": processing,
            "completed":  completed,
            "failed":     failed,
            "imported":   imported,
        },
    )


async def get_batch_processing_summary(
    db: AsyncSession,
    batch_id: str,
) -> dict | None:
    """
    Return real-time batch processing summary.

    JOINs bulk_upload_rows with applications to get live processing_status
    without requiring any sync from the Celery pipeline.  Also persists the
    live counts back to bulk_upload_batches on every call so the DB stays
    accurate.

    Deleted-duplicate handling: when Celery removes an exact-duplicate
    application from the DB, the LEFT JOIN returns NULL for that row.
    We treat (bur.application_id IS NOT NULL AND a.application_id IS NULL)
    as a terminal state so those rows don't block batch completion.

    Returns None if the batch does not exist.
    """
    # Batch header
    batch_row = await db.execute(
        text("""
            SELECT batch_id, batch_status, row_count,
                   imported_row_count, queued_row_count, processing_row_count,
                   completed_row_count, failed_row_count,
                   processing_started_at, processing_completed_at
              FROM bulk_upload_batches
             WHERE batch_id = CAST(:bid AS uuid)
        """),
        {"bid": batch_id},
    )
    batch = batch_row.mappings().first()
    if not batch:
        return None

    # Live counts from applications JOIN.
    # Rows where bur.application_id is set but the application was deleted
    # (exact-duplicate detection) count as terminal — they will never be scored.
    counts_row = await db.execute(
        text("""
            SELECT
                COUNT(bur.row_id)                                                       AS total_imported,
                COUNT(*) FILTER (
                    WHERE a.processing_status IN ('ai_scored','low_match','failed')
                       OR (bur.application_id IS NOT NULL AND a.application_id IS NULL)
                )                                                                        AS terminal_count,
                COUNT(*) FILTER (WHERE a.processing_status IN ('ai_scored','low_match')) AS completed_count,
                COUNT(*) FILTER (
                    WHERE a.processing_status = 'failed'
                       OR (bur.application_id IS NOT NULL AND a.application_id IS NULL)
                )                                                                        AS failed_count,
                COUNT(*) FILTER (WHERE a.processing_status = 'processing')              AS processing_count,
                COUNT(*) FILTER (WHERE a.processing_status = 'queued')                 AS queued_count,
                COUNT(*) FILTER (
                    WHERE a.processing_status = 'pending'
                       OR (bur.application_id IS NULL AND a.application_id IS NULL)
                )                                                                        AS pending_count
              FROM bulk_upload_rows bur
              LEFT JOIN applications a ON a.application_id = bur.application_id
             WHERE bur.batch_id = CAST(:bid AS uuid)
               AND bur.import_status = 'imported'
        """),
        {"bid": batch_id},
    )
    counts = counts_row.mappings().first()

    total_imported  = counts["total_imported"]  or 0
    completed_count = counts["completed_count"] or 0
    failed_count    = counts["failed_count"]    or 0
    processing_count= counts["processing_count"]or 0
    queued_count    = counts["queued_count"]    or 0
    terminal_count  = counts["terminal_count"]  or 0

    progress_pct = compute_batch_progress_percentage(
        total_imported, completed_count, failed_count, processing_count, queued_count,
    )

    # Persist live counts so DB stays in sync with actual application state
    await _persist_live_counts(
        db, batch_id,
        queued=queued_count,
        processing=processing_count,
        completed=completed_count,
        failed=failed_count,
        imported=total_imported,
    )

    return {
        "batch_id":               str(batch["batch_id"]),
        "batch_status":           batch["batch_status"],
        "total_rows":             batch["row_count"] or 0,
        "imported_rows":          total_imported,
        "queued_rows":            queued_count,
        "processing_rows":        processing_count,
        "completed_rows":         completed_count,
        "failed_rows":            failed_count,
        "pending_rows":           counts["pending_count"] or 0,
        "progress_percentage":    progress_pct,
        "processing_started_at":  batch["processing_started_at"],
        "processing_completed_at":batch["processing_completed_at"],
        "is_fully_processed":     is_batch_fully_processed(total_imported, terminal_count),
    }


async def list_rows_processing_status(
    db: AsyncSession,
    batch_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    Return paginated row-level processing status.

    JOINs bulk_upload_rows with applications to expose live scoring results
    (final_score, decision, processing_status) without storing duplicates.
    """
    result = await db.execute(
        text("""
            SELECT
                bur.row_id,
                bur.row_number,
                bur.import_status,
                bur.processing_status     AS row_processing_status,
                bur.processing_failure_reason,
                bur.application_id,
                bur.candidate_data,
                bur.matched_cv_filename,
                bur.processing_started_at,
                bur.processing_completed_at,
                a.processing_status       AS app_processing_status,
                a.final_score,
                a.decision,
                a.stopped_reason,
                a.evaluation_stage,
                a.scored_at
              FROM bulk_upload_rows bur
              LEFT JOIN applications a ON a.application_id = bur.application_id
             WHERE bur.batch_id = CAST(:bid AS uuid)
             ORDER BY bur.row_number
             LIMIT :lim OFFSET :off
        """),
        {"bid": batch_id, "lim": limit, "off": offset},
    )

    rows = []
    for r in result.mappings():
        candidate_data    = r["candidate_data"] or {}
        candidate_name    = (
            candidate_data.get("name")
            or candidate_data.get("first_name")
            or r["matched_cv_filename"]
            or f"Row {r['row_number']}"
        )

        # Derive live stage from the application record (more accurate than stored row status)
        app_status = r["app_processing_status"]
        stage      = app_status_to_stage(app_status)
        progress   = stage_to_progress(stage)
        failure_reason = (
            r["processing_failure_reason"]
            or classify_failure_reason(r["stopped_reason"])
        ) if stage == "failed" else None

        rows.append({
            "row_number":        r["row_number"],
            "candidate_name":    candidate_name,
            "application_id":    str(r["application_id"]) if r["application_id"] else None,
            "import_status":     r["import_status"],
            "processing_stage":  stage,
            "progress_percentage": progress,
            "app_processing_status": app_status,
            "final_score":       r["final_score"],
            "decision":          r["decision"],
            "knockout_result":   None,       # Phase 5: expose KO evaluation result
            "failure_reason":    failure_reason,
            "evaluation_stage":  r["evaluation_stage"],
            "scored_at":         r["scored_at"],
            "processing_started_at":  r["processing_started_at"],
            "processing_completed_at":r["processing_completed_at"],
        })
    return rows


async def sync_batch_completion(
    db: AsyncSession,
    batch_id: str,
    tenant_id: str,
) -> str | None:
    """
    Lazily detect when all imported rows are in a terminal state and
    transition the batch from 'processing' to 'completed' or 'failed'.

    Returns the new batch_status if a transition occurred, otherwise None.
    Called by the monitoring endpoint to avoid a separate Celery callback.

    Uses the live summary already computed by get_batch_processing_summary()
    (which has already persisted the counts) — does NOT call
    update_bulk_upload_batch_summary() to avoid overwriting live counts with
    stale bulk_upload_rows.processing_status values.
    """
    from services.bulk_upload_service import (
        get_bulk_upload_batch,
        update_bulk_upload_batch_status,
    )

    batch = await get_bulk_upload_batch(db, batch_id)
    if not batch or batch["batch_status"] != "processing":
        return None

    # get_batch_processing_summary persists live counts as a side effect
    summary = await get_batch_processing_summary(db, batch_id)
    if not summary or not summary["is_fully_processed"]:
        return None

    # Determine terminal status: 'failed' only if everything failed
    if summary["completed_rows"] > 0:
        new_status = "completed"
    elif summary["failed_rows"] > 0:
        new_status = "failed"
    else:
        new_status = "completed"  # all deleted duplicates / edge case

    # update_bulk_upload_batch_status sets processing_completed_at automatically
    await update_bulk_upload_batch_status(db, batch_id, new_status)
    logger.info(
        "[bulk_processing] Batch %s transitioned to '%s' "
        "(completed=%d, failed=%d)",
        batch_id, new_status, summary["completed_rows"], summary["failed_rows"],
    )
    return new_status
