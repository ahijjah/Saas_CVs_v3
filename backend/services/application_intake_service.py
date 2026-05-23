"""
Unified CV intake service.

All intake paths — email (IMAP), public apply form, manual upload, future API —
call process_cv_intake() as their single entry point.  The function handles:

  • file-type and size validation
  • SHA-256 hash + exact duplicate check (hash + job_id)
  • tenant CV-quota check
  • application record creation
  • file persistence to disk
  • application_files record
  • application_intake_log write (new unified table)
  • Celery scoring enqueue (when auto_score=True)

Returns an IntakeResult dataclass so callers can inspect the outcome without
parsing exception messages.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.subscription_service import can_process_cv, increment_monthly_usage

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

ALLOWED_MIME_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
}


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class IntakeResult:
    status: str                              # mirrors IntakeStatus values
    application_id: str | None = None
    intake_log_id: str | None = None
    duplicate_log_id: str | None = None
    duplicate_reference_application_id: str | None = None
    error_message: str | None = None
    scoring_enqueued_at: datetime | None = None
    abs_file_path: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == "RECEIVED_SUCCESSFULLY"


# ── Validation helpers ─────────────────────────────────────────────────────────

class IntakeValidationError(ValueError):
    """Raised for invalid file type or size — callers should surface as HTTP 4xx."""
    def __init__(self, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.http_status = http_status


def validate_file(content_type: str, content: bytes, max_file_size_mb: float) -> None:
    """Raise IntakeValidationError on unsupported type or oversized file."""
    if content_type not in ALLOWED_MIME_TYPES:
        raise IntakeValidationError(
            "Only PDF, DOC, and DOCX files are accepted",
            http_status=415,
        )
    max_bytes = int(max_file_size_mb * 1024 * 1024)
    if len(content) > max_bytes:
        raise IntakeValidationError(
            f"File exceeds maximum size of {max_file_size_mb} MB",
            http_status=413,
        )


def compute_file_hash(content: bytes) -> str:
    """Return SHA-256 hex digest of raw file bytes."""
    return hashlib.sha256(content).hexdigest()


# ── Per-job applicant limit ────────────────────────────────────────────────────

async def check_job_applicant_limit(
    db: AsyncSession,
    job_id: str,
) -> dict:
    """
    Check whether a job has reached its max_applications cap.
    Returns {"allowed": bool, "used": int, "limit": int|None, "message": str}.
    Counts valid (non-failed, non-duplicate) applications only.
    """
    row = await db.execute(
        text("""
            SELECT j.max_applications,
                   t.job_application_controls_enabled,
                   COUNT(a.application_id) AS valid_count
            FROM jobs j
            JOIN tenants t ON t.tenant_id = j.tenant_id
            LEFT JOIN applications a ON a.job_id = j.job_id
                AND a.processing_status != 'failed'
                AND (a.duplicate_status IS NULL OR a.duplicate_status = 'not_duplicate')
            WHERE j.job_id = CAST(:jid AS uuid)
            GROUP BY j.job_id, t.job_application_controls_enabled
        """),
        {"jid": job_id},
    )
    r = row.mappings().first()
    if not r:
        return {"allowed": True, "used": 0, "limit": None, "message": ""}

    # If the tenant does not have application controls enabled, skip the limit check entirely
    if not r["job_application_controls_enabled"]:
        return {"allowed": True, "used": int(r["valid_count"]), "limit": None, "message": ""}

    limit = r["max_applications"]
    if limit is None:
        return {"allowed": True, "used": int(r["valid_count"]), "limit": None, "message": ""}

    used = int(r["valid_count"])
    limit = int(limit)
    if used >= limit:
        return {
            "allowed": False,
            "used": used,
            "limit": limit,
            "message": f"This position is no longer accepting applications (limit of {limit} reached).",
        }
    return {"allowed": True, "used": used, "limit": limit, "message": ""}


# ── Duplicate detection ────────────────────────────────────────────────────────

async def check_exact_file_duplicate(
    db: AsyncSession,
    job_id: str,
    file_hash: str,
) -> dict | None:
    """
    Return the first matching intake log row if an identical file was already
    accepted for this job (hash + job_id), else None.

    Only considers RECEIVED_SUCCESSFULLY entries so transient failures don't
    permanently block re-submission.
    """
    row = await db.execute(
        text("""
            SELECT intake_log_id, application_id, candidate_email, candidate_name,
                   original_filename, created_at
            FROM application_intake_log
            WHERE job_id = CAST(:jid AS uuid)
              AND file_hash = :hash
              AND status = 'RECEIVED_SUCCESSFULLY'
            ORDER BY created_at ASC
            LIMIT 1
        """),
        {"jid": job_id, "hash": file_hash},
    )
    found = row.mappings().first()
    return dict(found) if found else None


# ── Database helpers ───────────────────────────────────────────────────────────

async def create_application_record(
    db: AsyncSession,
    *,
    job_id: str,
    tenant_id: str,
    candidate_name: str,
    candidate_email: str | None,
    submission_source: str,
    processing_status: str = "pending",
    candidate_phone: str | None = None,
    cover_letter: str | None = None,
    email_sender_address: str | None = None,
    submitted_by_user_id: str | None = None,
    submitted_by_name: str | None = None,
    submitted_by_email: str | None = None,
) -> str:
    """Insert an applications row and return the new application_id as a string."""
    result = await db.execute(
        text("""
            INSERT INTO applications (
                job_id, tenant_id, candidate_name, candidate_email,
                candidate_phone, cover_letter, submission_source, processing_status,
                email_sender_address,
                submitted_by_user_id, submitted_by_name, submitted_by_email
            ) VALUES (
                CAST(:jid AS uuid), CAST(:tid AS uuid), :name, :email,
                :phone, :cover_letter, :source, :proc_status,
                :sender,
                CAST(:uploader_id AS uuid), :uploader_name, :uploader_email
            )
            RETURNING application_id
        """),
        {
            "jid":           job_id,
            "tid":           tenant_id,
            "name":          candidate_name,
            "email":         candidate_email,
            "phone":         candidate_phone,
            "cover_letter":  cover_letter,
            "source":        submission_source,
            "proc_status":   processing_status,
            "sender":        email_sender_address,
            "uploader_id":   submitted_by_user_id,
            "uploader_name": submitted_by_name,
            "uploader_email": submitted_by_email,
        },
    )
    return str(result.scalar_one())


def save_file(
    content: bytes,
    files_base_path: str,
    tenant_id: str,
    job_id: str,
    application_id: str,
    mime_type: str,
) -> tuple[str, str]:
    """
    Write content bytes to disk.
    Returns (abs_path_str, relative_path_str) where relative is relative to
    files_base_path.
    """
    ext = ALLOWED_MIME_TYPES[mime_type]
    file_dir = Path(files_base_path) / "tenants" / tenant_id / "jobs" / job_id
    file_dir.mkdir(parents=True, exist_ok=True)
    abs_path = file_dir / f"{application_id}.{ext}"
    abs_path.write_bytes(content)
    relative_path = str(abs_path.relative_to(files_base_path))
    return str(abs_path), relative_path


async def insert_file_record(
    db: AsyncSession,
    *,
    application_id: str,
    tenant_id: str,
    original_name: str | None,
    mime_type: str,
    file_path: str,
    file_size_bytes: int,
) -> None:
    await db.execute(
        text("""
            INSERT INTO application_files
                (application_id, tenant_id, original_name, mime_type,
                 file_path, file_size_bytes, extraction_status)
            VALUES (CAST(:aid AS uuid), CAST(:tid AS uuid), :orig, :mime, :path, :size, 'pending')
        """),
        {
            "aid":  application_id,
            "tid":  tenant_id,
            "orig": original_name,
            "mime": mime_type,
            "path": file_path,
            "size": file_size_bytes,
        },
    )


# ── Intake log ─────────────────────────────────────────────────────────────────

async def log_intake(
    db: AsyncSession,
    *,
    tenant_id: str,
    intake_method: str,
    status: str,
    job_id: str | None = None,
    application_id: str | None = None,
    candidate_email: str | None = None,
    candidate_name: str | None = None,
    source_identifier: str | None = None,
    source_message_id: str | None = None,
    sender_email: str | None = None,
    recipient_email: str | None = None,
    subject: str | None = None,
    original_filename: str | None = None,
    file_hash: str | None = None,
    file_size_bytes: int | None = None,
    mime_type: str | None = None,
    error_message: str | None = None,
    duplicate_reference_application_id: str | None = None,
    duplicate_log_id: str | None = None,
    received_at: datetime | None = None,
    processing_started_at: datetime | None = None,
) -> str:
    """Insert a row into application_intake_log and return intake_log_id."""
    result = await db.execute(
        text("""
            INSERT INTO application_intake_log (
                tenant_id, job_id, application_id,
                intake_method, status,
                candidate_email, candidate_name,
                source_identifier, source_message_id,
                sender_email, recipient_email, subject,
                original_filename, file_hash, file_size_bytes, mime_type,
                error_message,
                duplicate_reference_application_id, duplicate_log_id,
                received_at, processing_started_at
            ) VALUES (
                CAST(:tid AS uuid),
                CAST(:jid AS uuid),
                CAST(:aid AS uuid),
                :method, :status,
                :c_email, :c_name,
                :src_id, :msg_id,
                :sender, :recipient, :subject,
                :orig_name, :hash, :fsize, :mime,
                :err_msg,
                CAST(:dup_ref AS uuid), CAST(:dup_log AS uuid),
                :received_at, :started_at
            )
            RETURNING intake_log_id
        """),
        {
            "tid":       tenant_id,
            "jid":       job_id,
            "aid":       application_id,
            "method":    intake_method,
            "status":    status,
            "c_email":   candidate_email,
            "c_name":    candidate_name,
            "src_id":    source_identifier,
            "msg_id":    source_message_id,
            "sender":    sender_email,
            "recipient": recipient_email,
            "subject":   subject,
            "orig_name": original_filename,
            "hash":      file_hash,
            "fsize":     file_size_bytes,
            "mime":      mime_type,
            "err_msg":   error_message,
            "dup_ref":   duplicate_reference_application_id,
            "dup_log":   duplicate_log_id,
            "received_at": received_at,
            "started_at":  processing_started_at,
        },
    )
    return str(result.scalar_one())


async def update_intake_log_completion(
    db: AsyncSession,
    log_id: str,
    processed_at: datetime,
    duration_ms: int,
    scoring_enqueued_at: datetime | None = None,
    notification_queued_at: datetime | None = None,
) -> None:
    await db.execute(
        text("""
            UPDATE application_intake_log
            SET processed_at            = :processed_at,
                processing_duration_ms  = :duration_ms,
                scoring_enqueued_at     = COALESCE(scoring_enqueued_at, :enqueued),
                notification_queued_at  = COALESCE(notification_queued_at, :notified),
                updated_at              = now()
            WHERE intake_log_id = CAST(:lid AS uuid)
        """),
        {
            "processed_at": processed_at,
            "duration_ms":  duration_ms,
            "enqueued":     scoring_enqueued_at,
            "notified":     notification_queued_at,
            "lid":          log_id,
        },
    )


# ── Scoring enqueue ────────────────────────────────────────────────────────────

def enqueue_scoring(
    application_id: str,
    job_id: str,
    tenant_id: str,
    file_path: str,
    mime_type: str,
) -> datetime:
    """
    Call score_cv_task.delay() and return the enqueue timestamp.
    Import is deferred to avoid circular imports when the module is loaded
    by Celery workers that also import this service.
    """
    from workers.cv_score import score_cv_task  # noqa: PLC0415
    score_cv_task.delay(
        application_id=application_id,
        job_id=job_id,
        tenant_id=tenant_id,
        file_path=file_path,
        mime_type=mime_type,
    )
    return datetime.now(timezone.utc)


# ── Main entry point ───────────────────────────────────────────────────────────

async def process_cv_intake(
    db: AsyncSession,
    *,
    intake_method: str,
    job_id: str,
    tenant_id: str,
    candidate_name: str,
    candidate_email: str | None,
    content_type: str,
    content: bytes,
    original_filename: str | None,
    submission_source: str,
    auto_score: bool,
    files_base_path: str,
    max_file_size_mb: float,
    # Optional / path-specific extras
    candidate_phone: str | None = None,
    cover_letter: str | None = None,
    email_sender_address: str | None = None,
    submitted_by_user_id: str | None = None,
    submitted_by_name: str | None = None,
    submitted_by_email: str | None = None,
    source_identifier: str | None = None,
    source_message_id: str | None = None,
    sender_email: str | None = None,
    recipient_email: str | None = None,
    subject: str | None = None,
    processing_started_at: datetime | None = None,
    received_at: datetime | None = None,
) -> IntakeResult:
    """
    Unified intake pipeline.

    Steps:
      1. Validate file type + size
      2. Compute SHA-256 hash
      3. Check exact duplicate (same hash + job_id already accepted)
      4. Check tenant CV quota
      5. Create application record
      6. Save file to disk
      7. Insert application_files record
      8. Commit DB
      9. Enqueue scoring (if auto_score=True)
      10. Write application_intake_log

    Returns IntakeResult; never raises on business-logic failures (quota, dup)
    — only propagates unexpected DB/IO errors.
    """
    started = processing_started_at or datetime.now(timezone.utc)
    received = received_at or started

    # ── Step 1: validate ──────────────────────────────────────────────────────
    try:
        validate_file(content_type, content, max_file_size_mb)
    except IntakeValidationError as exc:
        status_code = (
            "FILE_SIZE_EXCEEDED" if exc.http_status == 413 else "UNSUPPORTED_FILE_TYPE"
        )
        await _safe_log(
            db,
            tenant_id=tenant_id,
            job_id=job_id,
            intake_method=intake_method,
            status=status_code,
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            original_filename=original_filename,
            file_size_bytes=len(content) if content else None,
            mime_type=content_type,
            error_message=str(exc),
            source_identifier=source_identifier,
            source_message_id=source_message_id,
            sender_email=sender_email,
            recipient_email=recipient_email,
            subject=subject,
            processing_started_at=started,
            received_at=received,
        )
        await db.commit()  # persist log row before raising; no other writes pending
        raise  # let HTTP layer convert to 4xx

    # ── Step 2: hash ──────────────────────────────────────────────────────────
    file_hash = compute_file_hash(content)

    # ── Step 3: exact duplicate check moved to scoring pipeline ──────────────
    # file_hash is stored in application_intake_log (Step 10 below) so the
    # scoring pipeline can look it up for unified pre-screening.  Intake no
    # longer blocks or rejects duplicates — it only saves and enqueues.

    # ── Step 4a: per-job applicant cap ───────────────────────────────────────
    job_limit_check = await check_job_applicant_limit(db, job_id)
    if not job_limit_check["allowed"]:
        log_id = await _safe_log(
            db,
            tenant_id=tenant_id,
            job_id=job_id,
            intake_method=intake_method,
            status="REJECTED",
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            original_filename=original_filename,
            file_hash=file_hash,
            file_size_bytes=len(content),
            mime_type=content_type,
            error_message=job_limit_check.get("message", "Job applicant limit reached"),
            source_identifier=source_identifier,
            source_message_id=source_message_id,
            sender_email=sender_email,
            recipient_email=recipient_email,
            subject=subject,
            processing_started_at=started,
            received_at=received,
        )
        await db.commit()
        return IntakeResult(
            status="REJECTED",
            intake_log_id=log_id,
            error_message=job_limit_check.get("message", "Job applicant limit reached"),
        )

    # ── Step 4b: criteria analysis gate ──────────────────────────────────────
    _INTAKE_BLOCKING_STATUSES = frozenset({"pending", "processing", "insufficient", "blocked"})
    criteria_status_row = await db.execute(
        text("SELECT criteria_extraction_status FROM job_criteria WHERE job_id = CAST(:jid AS uuid)"),
        {"jid": job_id},
    )
    criteria_status = criteria_status_row.scalar_one_or_none() or "pending"
    if criteria_status in _INTAKE_BLOCKING_STATUSES:
        log_id = await _safe_log(
            db,
            tenant_id=tenant_id,
            job_id=job_id,
            intake_method=intake_method,
            status="REJECTED",
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            original_filename=original_filename,
            file_hash=file_hash,
            file_size_bytes=len(content),
            mime_type=content_type,
            error_message=f"CV intake blocked: job analysis status is '{criteria_status}'",
            source_identifier=source_identifier,
            source_message_id=source_message_id,
            sender_email=sender_email,
            recipient_email=recipient_email,
            subject=subject,
            processing_started_at=started,
            received_at=received,
        )
        await db.commit()
        return IntakeResult(
            status="INTAKE_BLOCKED",
            intake_log_id=log_id,
            error_message="CV intake is disabled because the job analysis is not completed.",
        )

    # ── Step 4c: tenant CV quota (plan + hard monthly limit) ──────────────────
    cv_check = await can_process_cv(tenant_id, db)
    if not cv_check["allowed"]:
        log_id = await _safe_log(
            db,
            tenant_id=tenant_id,
            job_id=job_id,
            intake_method=intake_method,
            status="REJECTED",
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            original_filename=original_filename,
            file_hash=file_hash,
            file_size_bytes=len(content),
            mime_type=content_type,
            error_message=cv_check.get("message", "CV quota exceeded"),
            source_identifier=source_identifier,
            source_message_id=source_message_id,
            sender_email=sender_email,
            recipient_email=recipient_email,
            subject=subject,
            processing_started_at=started,
            received_at=received,
        )
        await db.commit()
        return IntakeResult(
            status="REJECTED",
            intake_log_id=log_id,
            error_message=cv_check.get("message", "CV quota exceeded"),
        )

    # ── Step 5: create application record ────────────────────────────────────
    application_id = await create_application_record(
        db,
        job_id=job_id,
        tenant_id=tenant_id,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        submission_source=submission_source,
        processing_status="pending",
        candidate_phone=candidate_phone,
        cover_letter=cover_letter,
        email_sender_address=email_sender_address,
        submitted_by_user_id=submitted_by_user_id,
        submitted_by_name=submitted_by_name,
        submitted_by_email=submitted_by_email,
    )

    # ── Step 6: save file ─────────────────────────────────────────────────────
    abs_path, rel_path = save_file(
        content, files_base_path, tenant_id, job_id, application_id, content_type
    )

    # ── Step 7: insert file record ────────────────────────────────────────────
    await insert_file_record(
        db,
        application_id=application_id,
        tenant_id=tenant_id,
        original_name=original_filename,
        mime_type=content_type,
        file_path=rel_path,
        file_size_bytes=len(content),
    )

    # ── Step 8: commit ────────────────────────────────────────────────────────
    await db.commit()

    # ── Step 8b: increment monthly usage counter (non-fatal) ─────────────────
    try:
        await increment_monthly_usage(tenant_id, db)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to increment monthly usage for tenant %s: %s", tenant_id, exc)

    # ── Step 9: enqueue scoring (non-fatal if Celery is down) ─────────────────
    scoring_enqueued_at: datetime | None = None
    if auto_score:
        try:
            scoring_enqueued_at = enqueue_scoring(
                application_id, job_id, tenant_id, abs_path, content_type
            )
            logger.info(
                "Intake: scoring queued for application %s (method=%s)",
                application_id, intake_method,
            )
        except Exception as exc:
            logger.error(
                "Intake: failed to queue scoring for application %s: %s",
                application_id, exc,
            )

    # ── Step 10: write intake log ─────────────────────────────────────────────
    processed_at = datetime.now(timezone.utc)
    duration_ms = int((processed_at - started).total_seconds() * 1000)

    log_id = await _safe_log(
        db,
        tenant_id=tenant_id,
        job_id=job_id,
        application_id=application_id,
        intake_method=intake_method,
        status="RECEIVED_SUCCESSFULLY",
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        original_filename=original_filename,
        file_hash=file_hash,
        file_size_bytes=len(content),
        mime_type=content_type,
        source_identifier=source_identifier,
        source_message_id=source_message_id,
        sender_email=sender_email,
        recipient_email=recipient_email,
        subject=subject,
        processing_started_at=started,
        received_at=received,
    )
    if log_id:
        await update_intake_log_completion(
            db, log_id,
            processed_at=processed_at,
            duration_ms=duration_ms,
            scoring_enqueued_at=scoring_enqueued_at,
        )
    await db.commit()

    return IntakeResult(
        status="RECEIVED_SUCCESSFULLY",
        application_id=application_id,
        intake_log_id=log_id,
        scoring_enqueued_at=scoring_enqueued_at,
        abs_file_path=abs_path,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _safe_log(db: AsyncSession, **kwargs) -> str | None:
    """Call log_intake but never let it propagate an exception."""
    try:
        return await log_intake(db, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.error("application_intake_log write failed: %s", exc)
        return None
