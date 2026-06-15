"""
E-01 Phase 3 — Bulk Upload Import Service.

Orchestrates the import of validated bulk-upload rows into real application
records.  Uses the same underlying helpers as every other intake method
(create_application_record, save_file, insert_file_record, log_intake,
save_knockout_answers) to guarantee identical data structures.

Phase boundary
──────────────
This module STOPS once application records are created and linked.
  • No Celery scoring task is enqueued (auto_score is always False here).
  • No processing pipeline changes are triggered.
  • Phase 4 will handle scoring and processing-status tracking.

Idempotency
───────────
Rows that already carry an application_id are counted as "already_imported"
and skipped without creating a duplicate.  The endpoint is safe to call
multiple times; the second call will only process rows that weren't imported
in the first pass.

Knockout answer matching
────────────────────────
bulk_upload_rows.knockout_answers stores {raw_column_header: value}.
Phase 3 matches those headers to job_knockout_questions.question_text
(exact → case-insensitive) and passes the matched {question_id, answer_value}
pairs to save_knockout_answers().  Unmatched columns remain in the JSONB
field and are preserved for potential future use.
"""

import logging
import zipfile
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.application_intake_service import (
    create_application_record,
    insert_file_record,
    log_intake,
    save_file,
)
from services.knockout_questions_service import save_knockout_answers

logger = logging.getLogger(__name__)

# ── Extension → MIME type map for CV files ────────────────────────────────────

_EXT_TO_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc":  "application/msword",
}


# ── Pure helpers (no DB / no I/O) ────────────────────────────────────────────

def ext_to_mime(ext: str) -> str | None:
    """
    Map a file extension (lower-case, with dot) to a MIME type accepted by the
    intake service.  Returns None for unsupported types (e.g. .txt, .jpg).
    """
    return _EXT_TO_MIME.get(ext.lower())


def match_answers_to_questions(
    knockout_answers: dict,
    job_questions: list[dict],
) -> tuple[list[dict], dict]:
    """
    Match raw column-header answers to job knockout question IDs.

    knockout_answers : {column_header: answer_value}  (from bulk_upload_rows)
    job_questions    : [{question_id, question_text, ...}] (from job_knockout_questions)

    Returns
    -------
    matched   : [{question_id, answer_value}]  — ready for save_knockout_answers()
    unmatched : {column_header: answer_value}  — headers that had no question match
    """
    if not knockout_answers:
        return [], {}

    # Build lookup tables for fast matching
    exact_lookup: dict[str, str] = {}       # question_text → question_id
    lower_lookup: dict[str, str] = {}       # question_text.lower() → question_id
    for q in job_questions:
        qt = q.get("question_text", "")
        qid = str(q["question_id"])
        exact_lookup[qt] = qid
        lower_lookup[qt.lower()] = qid

    matched:   list[dict] = []
    unmatched: dict       = {}

    for header, value in knockout_answers.items():
        if not header:
            continue
        # Exact match first, then case-insensitive
        qid = exact_lookup.get(header) or lower_lookup.get(header.lower())
        if qid:
            matched.append({"question_id": qid, "answer_value": str(value) if value is not None else ""})
        else:
            unmatched[header] = value

    return matched, unmatched


def is_row_eligible(row: dict, include_warnings: bool) -> tuple[bool, str]:
    """
    Determine whether a bulk_upload_row is eligible for import.

    Returns (eligible, reason_if_not).
    """
    vs = row.get("validation_status", "pending")
    if vs == "error":
        return False, "validation_status=error"
    if vs == "warning" and not include_warnings:
        return False, "validation_status=warning and include_warning_rows=false"
    if vs == "pending":
        return False, "validation_status=pending (not yet validated)"
    return True, ""


def determine_final_batch_status(
    imported: int,
    skipped: int,
    failed: int,
    already_imported: int,
) -> str:
    """
    Determine the terminal batch status after import completes.

    'imported'   — at least one row was imported (new or previous)
    'failed'     — zero rows imported, at least one failed
    'imported'   — zero rows imported, all skipped (clean batch, nothing eligible)
    """
    total_imported = imported + already_imported
    if total_imported > 0:
        return "imported"
    if failed > 0:
        return "failed"
    return "imported"   # all skipped = clean run, treat as imported


# ── CV extraction from ZIP ────────────────────────────────────────────────────

def load_cv_from_zip(
    zf: "zipfile.ZipFile",
    zip_meta_lookup: dict,
    cv_filename: str,
) -> tuple[bytes, str] | None:
    """
    Read a CV file from an open ZipFile.

    zip_meta_lookup : {filename → {zip_path, extension, ...}}

    Returns (content_bytes, mime_type) or None if not loadable.
    Raises ValueError with a human-readable message on known failures.
    """
    meta = zip_meta_lookup.get(cv_filename) or zip_meta_lookup.get(cv_filename.lower())
    if not meta:
        raise ValueError(f"CV file '{cv_filename}' not found in ZIP metadata")

    mime = ext_to_mime(meta["extension"])
    if not mime:
        raise ValueError(
            f"Unsupported CV file type for import: '{meta['extension']}' "
            f"(accepted: .pdf, .docx, .doc)"
        )

    try:
        content = zf.read(meta["zip_path"])
    except KeyError:
        raise ValueError(
            f"CV file '{cv_filename}' listed in metadata but missing from ZIP archive"
        )

    return content, mime


# ── Async import helpers ──────────────────────────────────────────────────────

async def _get_job_questions(db: AsyncSession, job_id: str) -> list[dict]:
    """Return all knockout questions for the job."""
    rows = await db.execute(
        text("""
            SELECT question_id, question_text
              FROM job_knockout_questions
             WHERE job_id = CAST(:jid AS uuid)
             ORDER BY display_order, created_at
        """),
        {"jid": job_id},
    )
    return [
        {"question_id": str(r["question_id"]), "question_text": r["question_text"]}
        for r in rows.mappings()
    ]


async def _import_single_row(
    db: AsyncSession,
    row: dict,
    zf: "zipfile.ZipFile",
    zip_meta_lookup: dict,
    job_id: str,
    tenant_id: str,
    user_id: str | None,
    user_name: str | None,
    user_email: str | None,
    job_questions: list[dict],
    settings,
) -> tuple[str, str | None]:
    """
    Import one eligible row.

    Returns (application_id, None) on success.
    Returns (None, error_message) on failure.
    Raises on unexpected exceptions — caller must catch and mark as failed.
    """
    candidate_data   = row.get("candidate_data")   or {}
    knockout_answers = row.get("knockout_answers")  or {}
    cv_filename      = row.get("matched_cv_filename")

    if not cv_filename:
        raise ValueError("Row has no matched_cv_filename")

    # Read CV bytes from ZIP
    cv_content, mime_type = load_cv_from_zip(zf, zip_meta_lookup, cv_filename)

    # Candidate fields (name is required by applications table)
    candidate_name  = str(candidate_data.get("name") or "").strip() or cv_filename
    candidate_email = str(candidate_data.get("email") or "").strip() or None
    candidate_phone = str(candidate_data.get("phone") or "").strip() or None

    # Step 1: create application record
    application_id = await create_application_record(
        db,
        job_id              = job_id,
        tenant_id           = tenant_id,
        candidate_name      = candidate_name,
        candidate_email     = candidate_email,
        submission_source   = "bulk_excel_upload",
        processing_status   = "pending",
        candidate_phone     = candidate_phone,
        submitted_by_user_id  = user_id,
        submitted_by_name     = user_name,
        submitted_by_email    = user_email,
    )

    # Step 2: persist CV file to disk
    abs_path, rel_path = save_file(
        cv_content,
        settings.files_base_path,
        tenant_id,
        job_id,
        application_id,
        mime_type,
    )

    # Step 3: insert application_files record
    await insert_file_record(
        db,
        application_id  = application_id,
        tenant_id       = tenant_id,
        original_name   = cv_filename,
        mime_type       = mime_type,
        file_path       = rel_path,
        file_size_bytes = len(cv_content),
    )

    # Step 4: save matched knockout answers
    matched_answers, _ = match_answers_to_questions(knockout_answers, job_questions)
    if matched_answers:
        try:
            await save_knockout_answers(
                db,
                application_id = application_id,
                job_id         = job_id,
                answers        = matched_answers,
                answer_source  = "bulk_excel_upload",
                answer_method  = "direct_statement",
                updated_by     = user_id,
            )
        except Exception as exc:
            logger.warning(
                "[bulk_import] Failed to save knockout answers for application %s: %s",
                application_id, exc,
            )

    # Step 5: write intake log for auditability
    try:
        await log_intake(
            db,
            tenant_id        = tenant_id,
            intake_method    = "bulk_excel_upload",
            status           = "RECEIVED_SUCCESSFULLY",
            job_id           = job_id,
            application_id   = application_id,
            candidate_email  = candidate_email,
            candidate_name   = candidate_name,
            original_filename= cv_filename,
            file_size_bytes  = len(cv_content),
            mime_type        = mime_type,
            source_identifier= str(row.get("batch_id", "")),
        )
    except Exception as exc:
        logger.warning(
            "[bulk_import] Failed to write intake log for application %s: %s",
            application_id, exc,
        )

    return application_id, None


# ── Main import orchestrator ──────────────────────────────────────────────────

async def run_batch_import(
    db: AsyncSession,
    batch_id: str,
    tenant_id: str,
    job_id: str,
    user_id: str | None,
    user_name: str | None,
    user_email: str | None,
    include_warnings: bool,
    settings,
) -> dict:
    """
    Import all eligible rows for a batch.

    Called from the router after auth/ownership checks.  Manages:
      - batch status transitions (validating → importing → imported | failed)
      - per-row create_application_record + file + file_record + knockout answers
      - row import_status updates
      - batch summary recomputation

    Returns the import summary dict consumed by the endpoint response.
    """
    from services.bulk_upload_service import (
        get_bulk_upload_batch,
        list_bulk_upload_rows_for_batch,
        update_bulk_upload_batch_status,
        update_bulk_upload_batch_summary,
        update_bulk_upload_row_status,
    )
    from database import set_rls_context

    # ── Load batch metadata ───────────────────────────────────────────────────
    batch = await get_bulk_upload_batch(db, batch_id)

    # Fetch Phase 2 fields (zip_file_path is already in the batch; we need zip_file_metadata)
    meta_row = await db.execute(
        text("""
            SELECT zip_file_path, zip_file_metadata
              FROM bulk_upload_batches
             WHERE batch_id = CAST(:bid AS uuid)
        """),
        {"bid": batch_id},
    )
    mrow = meta_row.mappings().first()
    zip_file_path: str | None = mrow["zip_file_path"] if mrow else None
    zip_file_metadata: list[dict] = mrow["zip_file_metadata"] or [] if mrow else []

    # Build lookup by filename (case-insensitive fallback)
    zip_meta_lookup: dict = {}
    for fm in zip_file_metadata:
        zip_meta_lookup[fm["filename"]]        = fm
        zip_meta_lookup[fm["normalized_name"]] = fm

    # ── Validate ZIP is available ─────────────────────────────────────────────
    zip_available = bool(zip_file_path and Path(zip_file_path).exists())

    # ── Load job knockout questions for answer matching ───────────────────────
    job_questions = await _get_job_questions(db, job_id)

    # ── Transition batch → importing ──────────────────────────────────────────
    await update_bulk_upload_batch_status(db, batch_id, "importing")
    await db.commit()
    await set_rls_context(db, tenant_id, "recruiter")

    # ── Load all rows ─────────────────────────────────────────────────────────
    all_rows = await list_bulk_upload_rows_for_batch(db, batch_id=batch_id, limit=10000)

    # ── Per-row import ────────────────────────────────────────────────────────
    imported_count       = 0
    skipped_count        = 0
    failed_count         = 0
    already_imported_count = 0
    row_details: list[dict] = []

    zf_ctx = None
    if zip_available:
        try:
            zf_ctx = zipfile.ZipFile(zip_file_path, "r")
        except Exception as exc:
            logger.error("[bulk_import] Cannot open ZIP %s: %s", zip_file_path, exc)
            zip_available = False

    try:
        for row in all_rows:
            row_number = row["row_number"]
            row_id     = row["row_id"]

            # ── Idempotency: already imported ──────────────────────────────
            if row.get("application_id"):
                already_imported_count += 1
                row_details.append({
                    "row_number":     row_number,
                    "import_status":  "already_imported",
                    "application_id": row["application_id"],
                })
                continue

            # ── Eligibility check ──────────────────────────────────────────
            eligible, reason = is_row_eligible(row, include_warnings)
            if not eligible:
                if row.get("import_status") != "skipped":
                    try:
                        await update_bulk_upload_row_status(
                            db,
                            row_id       = row_id,
                            import_status = "skipped",
                            error_message = reason,
                        )
                        await db.commit()
                        await set_rls_context(db, tenant_id, "recruiter")
                    except Exception:
                        pass
                skipped_count += 1
                row_details.append({
                    "row_number":    row_number,
                    "import_status": "skipped",
                    "reason":        reason,
                })
                continue

            # ── Require ZIP for actual import ──────────────────────────────
            if not zip_available or zf_ctx is None:
                err_msg = "ZIP archive not available — upload the ZIP before importing"
                try:
                    await update_bulk_upload_row_status(
                        db,
                        row_id        = row_id,
                        import_status = "failed",
                        error_message = err_msg,
                    )
                    await db.commit()
                    await set_rls_context(db, tenant_id, "recruiter")
                except Exception:
                    pass
                failed_count += 1
                row_details.append({
                    "row_number":    row_number,
                    "import_status": "failed",
                    "error":         err_msg,
                })
                continue

            # ── Import ─────────────────────────────────────────────────────
            try:
                application_id, _ = await _import_single_row(
                    db,
                    row            = row,
                    zf             = zf_ctx,
                    zip_meta_lookup= zip_meta_lookup,
                    job_id         = job_id,
                    tenant_id      = tenant_id,
                    user_id        = user_id,
                    user_name      = user_name,
                    user_email     = user_email,
                    job_questions  = job_questions,
                    settings       = settings,
                )
                await update_bulk_upload_row_status(
                    db,
                    row_id         = row_id,
                    import_status  = "imported",
                    application_id = application_id,
                )
                await db.commit()
                await set_rls_context(db, tenant_id, "recruiter")

                imported_count += 1
                row_details.append({
                    "row_number":     row_number,
                    "import_status":  "imported",
                    "application_id": application_id,
                })

            except Exception as exc:
                err_msg = str(exc)
                logger.error(
                    "[bulk_import] Row %d import failed (batch=%s): %s",
                    row_number, batch_id, exc,
                )
                try:
                    await db.rollback()
                    await set_rls_context(db, tenant_id, "recruiter")
                    await update_bulk_upload_row_status(
                        db,
                        row_id        = row_id,
                        import_status = "failed",
                        error_message = err_msg,
                    )
                    await db.commit()
                    await set_rls_context(db, tenant_id, "recruiter")
                except Exception as rollback_exc:
                    logger.error(
                        "[bulk_import] Could not update failed status for row %d: %s",
                        row_number, rollback_exc,
                    )
                failed_count += 1
                row_details.append({
                    "row_number":    row_number,
                    "import_status": "failed",
                    "error":         err_msg,
                })

    finally:
        if zf_ctx is not None:
            try:
                zf_ctx.close()
            except Exception:
                pass

    # ── Update batch summary and terminal status ──────────────────────────────
    try:
        await update_bulk_upload_batch_summary(db, batch_id)
        terminal_status = determine_final_batch_status(
            imported_count, skipped_count, failed_count, already_imported_count,
        )
        await update_bulk_upload_batch_status(db, batch_id, terminal_status)
        await db.commit()
        await set_rls_context(db, tenant_id, "recruiter")
    except Exception as exc:
        logger.error("[bulk_import] Failed to update batch %s final status: %s", batch_id, exc)

    eligible_rows_count = imported_count + failed_count
    total_rows = len(all_rows)

    logger.info(
        "[bulk_import] Batch %s complete — imported=%d skipped=%d failed=%d already=%d",
        batch_id, imported_count, skipped_count, failed_count, already_imported_count,
    )

    return {
        "batch_id":              batch_id,
        "total_rows":            total_rows,
        "eligible_rows":         eligible_rows_count,
        "imported_rows":         imported_count,
        "skipped_rows":          skipped_count,
        "failed_rows":           failed_count,
        "already_imported_rows": already_imported_count,
        "rows": row_details,
    }
