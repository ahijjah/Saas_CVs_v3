"""
Router: E-01 Bulk Upload API (Phase 2 + Phase 3).

Endpoints:
  POST /bulk-upload/batches                       Create a new batch for a job
  POST /bulk-upload/batches/{batch_id}/zip        Upload ZIP of CVs
  POST /bulk-upload/batches/{batch_id}/excel      Upload Excel → validate
  POST /bulk-upload/batches/{batch_id}/import     Confirm import → create applications
  GET  /bulk-upload/batches/{batch_id}            Batch details + summary
  GET  /bulk-upload/batches/{batch_id}/rows       Paginated row list
  GET  /bulk-upload/batches/{batch_id}/validation Full validation report
  GET  /bulk-upload/jobs/{job_id}/batches         All batches for a job

Phase 3 boundary:
  - Creates real application records using the same helpers as all other intake methods.
  - Saves matched knockout answers to application_knockout_answers.
  - Does NOT enqueue scoring (Phase 4).
  - Does NOT trigger processing pipeline.
"""

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from config import get_settings
from database import get_db, set_rls_context
from services import bulk_upload_service as svc
from services.bulk_upload_processor import (
    _cell_str,
    build_candidate_data,
    build_knockout_answers,
    compute_zip_summary,
    extract_zip_metadata,
    find_duplicate_filenames,
    map_columns,
    match_cv_files,
    parse_excel,
    validate_row,
)

logger = logging.getLogger(__name__)

router  = APIRouter(prefix="/bulk-upload", tags=["bulk-upload"])
settings = get_settings()

_MAX_EXCEL_BYTES = 10  * 1024 * 1024   # 10 MB
_MAX_ZIP_BYTES   = 200 * 1024 * 1024   # 200 MB — holds 100-1000 CVs


# ── Internal helpers ──────────────────────────────────────────────────────────

def _batch_dir(tenant_id: str, job_id: str, batch_id: str) -> Path:
    return (
        Path(settings.files_base_path)
        / "tenants" / tenant_id
        / "jobs" / job_id
        / "bulk_uploads" / batch_id
    )


async def _require_job(db: AsyncSession, job_id: str, tenant_id: str) -> None:
    row = await db.execute(
        text("""
            SELECT 1 FROM jobs
             WHERE job_id    = CAST(:jid AS uuid)
               AND tenant_id = CAST(:tid AS uuid)
        """),
        {"jid": job_id, "tid": tenant_id},
    )
    if not row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")


async def _require_batch(db: AsyncSession, batch_id: str, tenant_id: str) -> dict:
    batch = await svc.get_bulk_upload_batch(db, batch_id)
    if not batch or batch["tenant_id"] != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return batch


# ── Endpoint: create batch ────────────────────────────────────────────────────

class CreateBatchRequest(BaseModel):
    job_id: str


@router.post("/batches", status_code=status.HTTP_201_CREATED)
async def create_batch(
    body: CreateBatchRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new bulk upload batch for a job."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_job(db, body.job_id, current_user.tenant_id)

    batch = await svc.create_bulk_upload_batch(
        db,
        tenant_id  = current_user.tenant_id,
        job_id     = body.job_id,
        created_by = current_user.user_id,
    )
    await db.commit()
    logger.info("[bulk_upload] Created batch %s for job %s", batch["batch_id"], body.job_id)
    return batch


# ── Endpoint: upload ZIP ──────────────────────────────────────────────────────

_ZIP_MIME_TYPES = frozenset({
    "application/zip",
    "application/x-zip-compressed",
    "application/x-zip",
    "application/octet-stream",
    "multipart/x-zip",
})


@router.post("/batches/{batch_id}/zip", status_code=status.HTTP_200_OK)
async def upload_zip(
    batch_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
):
    """
    Upload the ZIP archive of CV files for a batch.

    Stores the archive on disk and extracts file metadata (names, extensions,
    sizes) without loading CV content into memory.  Status advances to
    'uploaded' if the batch was still in 'created' state.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    batch = await _require_batch(db, batch_id, current_user.tenant_id)

    # Loose MIME check — browsers / HTTP clients vary widely for ZIPs
    fname = (file.filename or "").lower()
    if file.content_type not in _ZIP_MIME_TYPES and not fname.endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File must be a ZIP archive (.zip)",
        )

    content = await file.read()
    if len(content) > _MAX_ZIP_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"ZIP file too large — maximum {_MAX_ZIP_BYTES // 1024 // 1024} MB",
        )

    try:
        zip_files = extract_zip_metadata(content)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot read ZIP file — archive may be corrupt or password-protected",
        )

    # Persist to disk
    dest_dir = _batch_dir(current_user.tenant_id, batch["job_id"], batch_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "cvs.zip"
    zip_path.write_bytes(content)

    # Persist metadata + advance status
    await db.execute(
        text("""
            UPDATE bulk_upload_batches
               SET zip_file_path     = :zpath,
                   zip_file_metadata = CAST(:zmeta AS jsonb),
                   batch_status      = CASE WHEN batch_status = 'created'
                                            THEN 'uploaded'
                                            ELSE batch_status END,
                   updated_at        = now()
             WHERE batch_id = CAST(:bid AS uuid)
        """),
        {
            "bid":   batch_id,
            "zpath": str(zip_path),
            "zmeta": json.dumps(zip_files),
        },
    )
    await db.commit()

    supported   = sum(1 for f in zip_files if f["is_supported"])
    unsupported = len(zip_files) - supported
    logger.info(
        "[bulk_upload] ZIP uploaded for batch %s — %d files (%d supported)",
        batch_id, len(zip_files), supported,
    )
    return {
        "batch_id":          batch_id,
        "total_files":       len(zip_files),
        "supported_files":   supported,
        "unsupported_files": unsupported,
        "files": [
            {
                "filename":     f["filename"],
                "extension":    f["extension"],
                "is_supported": f["is_supported"],
                "size_bytes":   f["size_bytes"],
            }
            for f in zip_files
        ],
    }


# ── Endpoint: upload Excel + run validation ───────────────────────────────────

_EXCEL_MIME_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",
})


@router.post("/batches/{batch_id}/excel", status_code=status.HTTP_200_OK)
async def upload_excel(
    batch_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
):
    """
    Upload the Excel spreadsheet, parse all rows, match against the ZIP,
    run validation, and persist results.

    Returns the full validation result immediately (no async task needed for
    the Phase 2 workload of 100-1000 rows).

    Phase boundary: this endpoint stops after writing validation results to
    bulk_upload_rows.  It does NOT create application records.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    batch = await _require_batch(db, batch_id, current_user.tenant_id)

    fname = (file.filename or "").lower()
    if file.content_type not in _EXCEL_MIME_TYPES and not fname.endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File must be an Excel spreadsheet (.xlsx)",
        )

    content = await file.read()
    if len(content) > _MAX_EXCEL_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Excel file too large — maximum {_MAX_EXCEL_BYTES // 1024 // 1024} MB",
        )

    # ── Parse ───────────────────────────────────────────────────────────────
    try:
        headers, rows = parse_excel(content)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot read Excel file: {exc}",
        )

    if not headers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Excel file is empty or contains no header row",
        )

    col_mapping = map_columns(headers)
    if not col_mapping["cv_filename_col"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Required column 'CV File Name' not found in the spreadsheet. "
                "Accepted names include: 'CV File Name', 'CV Filename', "
                "'Resume', 'Filename', 'CV', 'File Name'."
            ),
        )

    # ── Persist Excel to disk ────────────────────────────────────────────────
    dest_dir = _batch_dir(current_user.tenant_id, batch["job_id"], batch_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "candidates.xlsx").name
    excel_path = dest_dir / safe_name
    excel_path.write_bytes(content)

    # ── Load ZIP metadata (may be empty if ZIP not yet uploaded) ─────────────
    zip_meta_row = await db.execute(
        text("""
            SELECT zip_file_metadata FROM bulk_upload_batches
             WHERE batch_id = CAST(:bid AS uuid)
        """),
        {"bid": batch_id},
    )
    zrow = zip_meta_row.mappings().first()
    zip_files: list[dict] = (zrow["zip_file_metadata"] or []) if zrow else []

    # ── Transition → validating ──────────────────────────────────────────────
    await svc.update_bulk_upload_batch_status(db, batch_id, "validating")

    # ── Match + validate all rows ────────────────────────────────────────────
    duplicate_filenames = find_duplicate_filenames(rows, col_mapping["cv_filename_col"])
    referenced_filenames: set[str] = set()
    row_results: list[dict] = []

    for row in rows:
        row_number = row["_row_number"]
        cv_col     = col_mapping["cv_filename_col"]
        filename   = _cell_str(row.get(cv_col))

        match_result   = match_cv_files(filename, zip_files)
        errors, warns  = validate_row(row, col_mapping, match_result, duplicate_filenames)

        # Determine the CV filename to persist
        matched_fn: str | None = None
        if match_result["status"] == "matched":
            matched_fn = match_result["matched_file"]["filename"]
            referenced_filenames.add(matched_fn)
        elif filename:
            matched_fn = filename   # keep what the spreadsheet said even if not found

        candidate_data   = build_candidate_data(row, col_mapping)
        knockout_answers = build_knockout_answers(row, col_mapping)

        # Upsert row (resets statuses if row was already parsed)
        await svc.create_or_update_bulk_upload_row(
            db,
            batch_id            = batch_id,
            tenant_id           = current_user.tenant_id,
            job_id              = batch["job_id"],
            row_number          = row_number,
            candidate_data      = candidate_data,
            knockout_answers    = knockout_answers,
            matched_cv_filename = matched_fn,
        )

        # Determine validation status
        vs = "error" if errors else ("warning" if warns else "ready")

        # Fetch row_id just inserted/updated to update its status
        row_id_res = await db.execute(
            text("""
                SELECT row_id FROM bulk_upload_rows
                 WHERE batch_id = CAST(:bid AS uuid) AND row_number = :rnum
            """),
            {"bid": batch_id, "rnum": row_number},
        )
        row_id_row = row_id_res.mappings().first()
        if row_id_row:
            await svc.update_bulk_upload_row_status(
                db,
                row_id              = str(row_id_row["row_id"]),
                validation_status   = vs,
                validation_warnings = warns or None,
                validation_errors   = errors or None,
            )

        row_results.append({
            "row_number":        row_number,
            "name":              candidate_data.get("name", ""),
            "cv_filename":       matched_fn or "",
            "validation_status": vs,
            "match_type":        match_result.get("match_type"),
            "errors":            errors,
            "warnings":          warns,
        })

    # ── ZIP summary ──────────────────────────────────────────────────────────
    zip_summary = compute_zip_summary(zip_files, referenced_filenames)

    # ── Persist Excel path + column mapping + unmatched count ─────────────────
    await db.execute(
        text("""
            UPDATE bulk_upload_batches
               SET source_file_path     = :xpath,
                   original_filename    = :xname,
                   excel_column_mapping = CAST(:mapping AS jsonb),
                   unmatched_cv_count   = :unmatched,
                   updated_at           = now()
             WHERE batch_id = CAST(:bid AS uuid)
        """),
        {
            "bid":      batch_id,
            "xpath":    str(excel_path),
            "xname":    safe_name,
            "mapping":  json.dumps(col_mapping),
            "unmatched": zip_summary["unmatched_cv_count"],
        },
    )

    # ── Recompute summary counters and advance status → validated ─────────────
    await svc.update_bulk_upload_batch_summary(db, batch_id)
    await svc.update_bulk_upload_batch_status(db, batch_id, "validated")
    await db.commit()

    batch_updated = await svc.get_bulk_upload_batch(db, batch_id)

    logger.info(
        "[bulk_upload] Validated batch %s — %d rows (ready=%d warn=%d err=%d)",
        batch_id, len(rows),
        batch_updated["valid_row_count"],
        batch_updated["warning_row_count"],
        batch_updated["error_row_count"],
    )

    return {
        "batch_id":       batch_id,
        "batch_status":   "validated",
        "column_mapping": col_mapping,
        "summary": {
            "total_rows":               len(rows),
            "ready_rows":               batch_updated["valid_row_count"],
            "warning_rows":             batch_updated["warning_row_count"],
            "error_rows":               batch_updated["error_row_count"],
            "unmatched_cv_count":       zip_summary["unmatched_cv_count"],
            "duplicate_filename_count": len(duplicate_filenames),
        },
        "zip_summary": zip_summary,
        "rows": row_results,
    }


# ── Endpoint: confirm import ─────────────────────────────────────────────────

_IMPORTABLE_STATUSES = frozenset({"validated", "importing", "imported"})


@router.post("/batches/{batch_id}/import", status_code=status.HTTP_200_OK)
async def import_batch(
    batch_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    include_warning_rows: bool = Query(
        True,
        description="Include rows with validation_status=warning in the import",
    ),
):
    """
    Confirm import of validated rows and create real application records.

    Eligible rows (ready, and optionally warning) get:
      - An application record (submission_source=bulk_excel_upload)
      - CV file persisted to disk (same path structure as all other intakes)
      - Knockout answers saved to application_knockout_answers where column
        headers match question_text for the job
      - An application_intake_log entry for auditability

    Error rows are always skipped.
    Rows that already have an application_id are not duplicated.

    The endpoint is idempotent — calling it again re-processes any rows that
    were not imported in a previous pass and returns already_imported_rows for
    those that were.

    Phase 3 boundary: scoring is NOT triggered.  That is Phase 4.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    batch = await _require_batch(db, batch_id, current_user.tenant_id)

    if batch["batch_status"] not in _IMPORTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Batch cannot be imported in status '{batch['batch_status']}'. "
                f"Batch must be validated first."
            ),
        )

    from services.bulk_upload_import_service import run_batch_import

    summary = await run_batch_import(
        db               = db,
        batch_id         = batch_id,
        tenant_id        = current_user.tenant_id,
        job_id           = batch["job_id"],
        user_id          = current_user.user_id,
        user_name        = getattr(current_user, "full_name", None),
        user_email       = getattr(current_user, "email", None),
        include_warnings = include_warning_rows,
        settings         = settings,
    )
    return summary


# ── Endpoint: get batch ───────────────────────────────────────────────────────

@router.get("/batches/{batch_id}", status_code=status.HTTP_200_OK)
async def get_batch(
    batch_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return batch record with row-count summary."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    batch = await _require_batch(db, batch_id, current_user.tenant_id)

    # Augment with Phase 2 fields stored outside _row_to_batch_dict
    extra = await db.execute(
        text("""
            SELECT unmatched_cv_count, excel_column_mapping
              FROM bulk_upload_batches
             WHERE batch_id = CAST(:bid AS uuid)
        """),
        {"bid": batch_id},
    )
    erow = extra.mappings().first()
    if erow:
        batch["unmatched_cv_count"]   = erow["unmatched_cv_count"] or 0
        batch["excel_column_mapping"] = erow["excel_column_mapping"]

    return batch


# ── Endpoint: list rows (paginated) ──────────────────────────────────────────

@router.get("/batches/{batch_id}/rows", status_code=status.HTTP_200_OK)
async def list_rows(
    batch_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    validation_status: str | None = Query(None, description="Filter: pending|ready|warning|error"),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0,   ge=0),
):
    """Return rows for a batch, ordered by row_number, with optional validation_status filter."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_batch(db, batch_id, current_user.tenant_id)

    rows = await svc.list_bulk_upload_rows_for_batch(
        db,
        batch_id          = batch_id,
        validation_status = validation_status,
        limit             = limit,
        offset            = offset,
    )
    return {"batch_id": batch_id, "rows": rows, "count": len(rows)}


# ── Endpoint: full validation report ─────────────────────────────────────────

@router.get("/batches/{batch_id}/validation", status_code=status.HTTP_200_OK)
async def get_validation_result(
    batch_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Return the full validation result: batch summary + per-row details.

    Designed to populate the frontend preview table.  Only validation-phase
    data is returned — import and processing fields are omitted.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    batch = await _require_batch(db, batch_id, current_user.tenant_id)

    # Fetch Phase 2 summary fields
    extra = await db.execute(
        text("""
            SELECT unmatched_cv_count, excel_column_mapping
              FROM bulk_upload_batches
             WHERE batch_id = CAST(:bid AS uuid)
        """),
        {"bid": batch_id},
    )
    erow = extra.mappings().first()
    unmatched_cv_count = (erow["unmatched_cv_count"] or 0) if erow else 0

    rows = await svc.list_bulk_upload_rows_for_batch(
        db, batch_id=batch_id, limit=5000,
    )

    row_details = [
        {
            "row_number":        r["row_number"],
            "name":              (r["candidate_data"] or {}).get("name", ""),
            "email":             (r["candidate_data"] or {}).get("email", ""),
            "cv_filename":       r["matched_cv_filename"] or "",
            "validation_status": r["validation_status"],
            "warnings":          r["validation_warnings"] or [],
            "errors":            r["validation_errors"]   or [],
        }
        for r in rows
    ]

    return {
        "batch_id":     batch_id,
        "batch_status": batch["batch_status"],
        "summary": {
            "total_rows":       batch["row_count"],
            "ready_rows":       batch["valid_row_count"],
            "warning_rows":     batch["warning_row_count"],
            "error_rows":       batch["error_row_count"],
            "unmatched_cv_count": unmatched_cv_count,
        },
        "rows": row_details,
    }


# ── Endpoint: list batches for a job ─────────────────────────────────────────

@router.get("/jobs/{job_id}/batches", status_code=status.HTTP_200_OK)
async def list_batches_for_job(
    job_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit:  int = Query(20, ge=1, le=100),
    offset: int = Query(0,  ge=0),
):
    """Return all batches for a job (newest first)."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_job(db, job_id, current_user.tenant_id)

    batches = await svc.list_bulk_upload_batches_for_job(
        db,
        job_id    = job_id,
        tenant_id = current_user.tenant_id,
        limit     = limit,
        offset    = offset,
    )
    return {"job_id": job_id, "batches": batches, "count": len(batches)}
