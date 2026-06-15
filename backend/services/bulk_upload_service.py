"""
Service: bulk_upload_batches + bulk_upload_rows.

Phase 1 foundation only — provides CRUD and status-update helpers for the
E-01 Bulk Excel Upload feature.  Excel parsing, ZIP processing, validation
engine, import logic, and report export are NOT implemented here.

Internal JSONB shapes
─────────────────────
bulk_upload_rows.candidate_data
    {
        "first_name":    "Jane",
        "last_name":     "Doe",
        "email":         "jane@example.com",
        "phone":         "+971 50 000 0000",
        "linkedin_url":  "https://linkedin.com/in/janedoe",
        ... (any extra columns preserved as-is)
    }

bulk_upload_rows.knockout_answers
    {
        "<question_id or question_text>": "<answer_value>",
        ...
    }
    Key is question_id (UUID string) when questions have been matched,
    or raw column header text before matching.

bulk_upload_rows.validation_warnings / validation_errors
    ["Phone number missing", "Email format looks unusual"]

Batch status lifecycle
──────────────────────
created → uploaded → validating → validated → importing →
imported → processing → completed | failed

Row validation status: pending | ready | warning | error
Row import   status:   pending | imported | skipped  | failed
Row processing status: pending | queued  | processing | completed | failed
"""
import json
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Valid status values ────────────────────────────────────────────────────────

BATCH_STATUSES = frozenset({
    "created", "uploaded", "validating", "validated",
    "importing", "imported", "processing", "completed", "failed",
})
ROW_VALIDATION_STATUSES = frozenset({"pending", "ready", "warning", "error"})
ROW_IMPORT_STATUSES     = frozenset({"pending", "imported", "skipped", "failed"})
ROW_PROCESSING_STATUSES = frozenset({"pending", "queued", "processing", "completed", "failed"})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_batch_dict(r) -> dict:
    return {
        "batch_id":                 str(r["batch_id"]),
        "tenant_id":                str(r["tenant_id"]),
        "job_id":                   str(r["job_id"]),
        "created_by":               str(r["created_by"]) if r["created_by"] else None,
        "batch_status":             r["batch_status"],
        "original_filename":        r["original_filename"],
        "source_file_path":         r["source_file_path"],
        "zip_file_path":            r["zip_file_path"],
        "row_count":                r["row_count"],
        "valid_row_count":          r["valid_row_count"],
        "warning_row_count":        r["warning_row_count"],
        "error_row_count":          r["error_row_count"],
        "imported_row_count":       r["imported_row_count"],
        "skipped_row_count":        r["skipped_row_count"],
        "failed_row_count":         r["failed_row_count"],
        "completed_row_count":      r["completed_row_count"],
        "error_message":            r["error_message"],
        "created_at":               r["created_at"],
        "updated_at":               r["updated_at"],
        "validated_at":             r["validated_at"],
        "imported_at":              r["imported_at"],
        "processing_started_at":    r["processing_started_at"],
        "processing_completed_at":  r["processing_completed_at"],
    }


def _row_to_row_dict(r) -> dict:
    return {
        "row_id":                   str(r["row_id"]),
        "batch_id":                 str(r["batch_id"]),
        "tenant_id":                str(r["tenant_id"]),
        "job_id":                   str(r["job_id"]),
        "row_number":               r["row_number"],
        "validation_status":        r["validation_status"],
        "import_status":            r["import_status"],
        "processing_status":        r["processing_status"],
        "candidate_data":           r["candidate_data"],
        "knockout_answers":         r["knockout_answers"],
        "matched_cv_filename":      r["matched_cv_filename"],
        "application_id":           str(r["application_id"]) if r["application_id"] else None,
        "validation_warnings":      r["validation_warnings"],
        "validation_errors":        r["validation_errors"],
        "error_message":            r["error_message"],
        "created_at":               r["created_at"],
        "updated_at":               r["updated_at"],
        "validated_at":             r["validated_at"],
        "imported_at":              r["imported_at"],
        "processing_started_at":    r["processing_started_at"],
        "processing_completed_at":  r["processing_completed_at"],
    }


# ── Batch functions ───────────────────────────────────────────────────────────

async def create_bulk_upload_batch(
    db: AsyncSession,
    tenant_id: str,
    job_id: str,
    created_by: str | None = None,
    original_filename: str | None = None,
    source_file_path: str | None = None,
    zip_file_path: str | None = None,
) -> dict:
    """Create a new bulk upload batch record and return it as a dict."""
    result = await db.execute(
        text("""
            INSERT INTO bulk_upload_batches
                (tenant_id, job_id, created_by, original_filename,
                 source_file_path, zip_file_path)
            VALUES
                (CAST(:tid AS uuid), CAST(:jid AS uuid),
                 CAST(:uid AS uuid), :filename, :src_path, :zip_path)
            RETURNING *
        """),
        {
            "tid":      tenant_id,
            "jid":      job_id,
            "uid":      created_by,
            "filename": original_filename,
            "src_path": source_file_path,
            "zip_path": zip_file_path,
        },
    )
    row = result.mappings().first()
    logger.info("[bulk_upload] Created batch %s for job %s", row["batch_id"], job_id)
    return _row_to_batch_dict(row)


async def get_bulk_upload_batch(
    db: AsyncSession,
    batch_id: str,
) -> dict | None:
    """Return a single batch by batch_id, or None if not found."""
    result = await db.execute(
        text("SELECT * FROM bulk_upload_batches WHERE batch_id = CAST(:bid AS uuid)"),
        {"bid": batch_id},
    )
    row = result.mappings().first()
    return _row_to_batch_dict(row) if row else None


async def list_bulk_upload_batches_for_job(
    db: AsyncSession,
    job_id: str,
    tenant_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Return batches for a job, newest first."""
    result = await db.execute(
        text("""
            SELECT * FROM bulk_upload_batches
            WHERE job_id    = CAST(:jid AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
            ORDER BY created_at DESC
            LIMIT :lim OFFSET :off
        """),
        {"jid": job_id, "tid": tenant_id, "lim": limit, "off": offset},
    )
    return [_row_to_batch_dict(r) for r in result.mappings()]


async def update_bulk_upload_batch_status(
    db: AsyncSession,
    batch_id: str,
    batch_status: str,
    error_message: str | None = None,
) -> None:
    """Transition a batch to a new status, setting relevant timestamp(s)."""
    if batch_status not in BATCH_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid batch_status '{batch_status}'. "
                   f"Must be one of: {sorted(BATCH_STATUSES)}.",
        )
    now = _now_utc()
    # Map status → which optional timestamp to set
    ts_col = {
        "validated":  "validated_at",
        "imported":   "imported_at",
        "processing": "processing_started_at",
        "completed":  "processing_completed_at",
        "failed":     "processing_completed_at",
    }.get(batch_status)

    ts_clause = f", {ts_col} = :{ts_col}" if ts_col else ""
    params: dict = {
        "bid":          batch_id,
        "status":       batch_status,
        "error":        error_message,
        "updated_at":   now,
    }
    if ts_col:
        params[ts_col] = now

    await db.execute(
        text(f"""
            UPDATE bulk_upload_batches
               SET batch_status  = :status,
                   error_message = :error,
                   updated_at    = :updated_at
                   {ts_clause}
             WHERE batch_id = CAST(:bid AS uuid)
        """),
        params,
    )


async def update_bulk_upload_batch_summary(
    db: AsyncSession,
    batch_id: str,
) -> None:
    """
    Recompute row-count summary columns from the current state of
    bulk_upload_rows for this batch and write them back to the batch row.

    Should be called after any bulk change to bulk_upload_rows
    (e.g. after validation or import completes for the whole batch).
    """
    await db.execute(
        text("""
            UPDATE bulk_upload_batches b
               SET row_count          = s.total,
                   valid_row_count    = s.valid,
                   warning_row_count  = s.warn,
                   error_row_count    = s.err,
                   imported_row_count = s.imported,
                   skipped_row_count  = s.skipped,
                   failed_row_count   = s.failed,
                   completed_row_count= s.completed,
                   updated_at         = now()
              FROM (
                SELECT
                    COUNT(*)                                              AS total,
                    COUNT(*) FILTER (WHERE validation_status = 'ready')  AS valid,
                    COUNT(*) FILTER (WHERE validation_status = 'warning') AS warn,
                    COUNT(*) FILTER (WHERE validation_status = 'error')  AS err,
                    COUNT(*) FILTER (WHERE import_status = 'imported')   AS imported,
                    COUNT(*) FILTER (WHERE import_status = 'skipped')    AS skipped,
                    COUNT(*) FILTER (WHERE import_status = 'failed')     AS failed,
                    COUNT(*) FILTER (WHERE processing_status = 'completed') AS completed
                FROM bulk_upload_rows
               WHERE batch_id = CAST(:bid AS uuid)
              ) s
             WHERE b.batch_id = CAST(:bid AS uuid)
        """),
        {"bid": batch_id},
    )


# ── Row functions ─────────────────────────────────────────────────────────────

async def create_or_update_bulk_upload_row(
    db: AsyncSession,
    batch_id: str,
    tenant_id: str,
    job_id: str,
    row_number: int,
    candidate_data: dict | None = None,
    knockout_answers: dict | None = None,
    matched_cv_filename: str | None = None,
) -> dict:
    """
    Upsert a row for (batch_id, row_number).

    If the row already exists (e.g. re-parse after correction), it is
    updated in place, resetting statuses to pending so the validation
    pipeline can re-run.
    """
    if row_number < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="row_number must be >= 1 (1-indexed).",
        )
    result = await db.execute(
        text("""
            INSERT INTO bulk_upload_rows
                (batch_id, tenant_id, job_id, row_number,
                 candidate_data, knockout_answers, matched_cv_filename)
            VALUES
                (CAST(:bid AS uuid), CAST(:tid AS uuid), CAST(:jid AS uuid),
                 :rnum,
                 CAST(:cdata AS jsonb), CAST(:kanswers AS jsonb), :cv_fname)
            ON CONFLICT (batch_id, row_number) DO UPDATE
                SET candidate_data      = EXCLUDED.candidate_data,
                    knockout_answers    = EXCLUDED.knockout_answers,
                    matched_cv_filename = EXCLUDED.matched_cv_filename,
                    validation_status   = 'pending',
                    import_status       = 'pending',
                    processing_status   = 'pending',
                    validation_warnings = NULL,
                    validation_errors   = NULL,
                    error_message       = NULL,
                    validated_at        = NULL,
                    imported_at         = NULL,
                    processing_started_at   = NULL,
                    processing_completed_at = NULL,
                    updated_at          = now()
            RETURNING *
        """),
        {
            "bid":      batch_id,
            "tid":      tenant_id,
            "jid":      job_id,
            "rnum":     row_number,
            "cdata":    json.dumps(candidate_data) if candidate_data is not None else None,
            "kanswers": json.dumps(knockout_answers) if knockout_answers is not None else None,
            "cv_fname": matched_cv_filename,
        },
    )
    row = result.mappings().first()
    return _row_to_row_dict(row)


async def get_bulk_upload_row(
    db: AsyncSession,
    row_id: str,
) -> dict | None:
    """Return a single bulk_upload_row by row_id, or None if not found."""
    result = await db.execute(
        text("SELECT * FROM bulk_upload_rows WHERE row_id = CAST(:rid AS uuid)"),
        {"rid": row_id},
    )
    row = result.mappings().first()
    return _row_to_row_dict(row) if row else None


async def list_bulk_upload_rows_for_batch(
    db: AsyncSession,
    batch_id: str,
    validation_status: str | None = None,
    import_status: str | None = None,
    processing_status: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """Return rows for a batch, ordered by row_number.  Optional status filters."""
    filters = ["batch_id = CAST(:bid AS uuid)"]
    params: dict = {"bid": batch_id, "lim": limit, "off": offset}

    if validation_status is not None:
        filters.append("validation_status = :vs")
        params["vs"] = validation_status
    if import_status is not None:
        filters.append("import_status = :is_")
        params["is_"] = import_status
    if processing_status is not None:
        filters.append("processing_status = :ps")
        params["ps"] = processing_status

    where = " AND ".join(filters)
    result = await db.execute(
        text(f"""
            SELECT * FROM bulk_upload_rows
             WHERE {where}
             ORDER BY row_number
             LIMIT :lim OFFSET :off
        """),
        params,
    )
    return [_row_to_row_dict(r) for r in result.mappings()]


async def update_bulk_upload_row_status(
    db: AsyncSession,
    row_id: str,
    validation_status: str | None = None,
    import_status: str | None = None,
    processing_status: str | None = None,
    application_id: str | None = None,
    validation_warnings: list[str] | None = None,
    validation_errors: list[str] | None = None,
    error_message: str | None = None,
) -> None:
    """
    Partial-update a row's status fields.  Only non-None arguments are
    written; at least one must be provided.

    Sets the corresponding timestamp column when the status advances:
        validation_status=ready|warning|error  → validated_at
        import_status=imported|skipped|failed  → imported_at
        processing_status=processing           → processing_started_at
        processing_status=completed|failed     → processing_completed_at
    """
    if all(v is None for v in (validation_status, import_status,
                                processing_status, application_id,
                                validation_warnings, validation_errors,
                                error_message)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="update_bulk_upload_row_status: at least one field must be provided.",
        )

    if validation_status is not None and validation_status not in ROW_VALIDATION_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid validation_status '{validation_status}'.",
        )
    if import_status is not None and import_status not in ROW_IMPORT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid import_status '{import_status}'.",
        )
    if processing_status is not None and processing_status not in ROW_PROCESSING_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid processing_status '{processing_status}'.",
        )

    now = _now_utc()
    parts = ["updated_at = :updated_at"]
    params: dict = {"rid": row_id, "updated_at": now}

    if validation_status is not None:
        parts.append("validation_status = :vs")
        params["vs"] = validation_status
        if validation_status in ("ready", "warning", "error"):
            parts.append("validated_at = :validated_at")
            params["validated_at"] = now

    if import_status is not None:
        parts.append("import_status = :is_")
        params["is_"] = import_status
        if import_status in ("imported", "skipped", "failed"):
            parts.append("imported_at = :imported_at")
            params["imported_at"] = now

    if processing_status is not None:
        parts.append("processing_status = :ps")
        params["ps"] = processing_status
        if processing_status == "processing":
            parts.append("processing_started_at = :ps_started")
            params["ps_started"] = now
        elif processing_status in ("completed", "failed"):
            parts.append("processing_completed_at = :ps_completed")
            params["ps_completed"] = now

    if application_id is not None:
        parts.append("application_id = CAST(:app_id AS uuid)")
        params["app_id"] = application_id

    if validation_warnings is not None:
        parts.append("validation_warnings = CAST(:vw AS jsonb)")
        params["vw"] = json.dumps(validation_warnings)

    if validation_errors is not None:
        parts.append("validation_errors = CAST(:ve AS jsonb)")
        params["ve"] = json.dumps(validation_errors)

    if error_message is not None:
        parts.append("error_message = :err")
        params["err"] = error_message

    set_clause = ", ".join(parts)
    await db.execute(
        text(f"""
            UPDATE bulk_upload_rows
               SET {set_clause}
             WHERE row_id = CAST(:rid AS uuid)
        """),
        params,
    )
