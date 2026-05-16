"""
Public (unauthenticated) endpoints for the candidate-facing apply flow.

GET  /jobs/public/{job_code}  — returns active job info, intake status
POST /applications/public      — candidate submits CV + metadata
"""
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi import status as http_status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db, set_rls_context

router = APIRouter(tags=["public"])
settings = get_settings()

ALLOWED_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
}


async def _get_active_public_job(job_code: str, db: AsyncSession) -> dict:
    """Return the job row (as mapping) for an active job by job_code.
    Raises 404 if not found or not active. Caller must have set RLS context."""
    row = await db.execute(
        text("""
            SELECT
                j.job_id, j.tenant_id, j.job_code, j.title, j.department,
                j.location, j.description, j.job_type, j.experience_level,
                j.work_mode, j.application_deadline,
                j.max_applications, j.auto_close_when_limit_reached,
                j.status
            FROM jobs j
            WHERE j.job_code = :code
              AND j.status = 'active'
        """),
        {"code": job_code},
    )
    job = row.mappings().first()
    if not job:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Position not found or no longer accepting applications",
        )
    return dict(job)


async def _count_valid_applications(job_id: str, db: AsyncSession) -> int:
    """Count non-duplicate, non-failed applications for a job."""
    result = await db.execute(
        text("""
            SELECT COUNT(*) FROM applications
            WHERE job_id = :jid
              AND (duplicate_status IS NULL OR duplicate_status = 'not_duplicate')
              AND processing_status != 'failed'
        """),
        {"jid": job_id},
    )
    return int(result.scalar_one())


@router.get("/jobs/public/{job_code}")
async def get_public_job(
    job_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return public job details for the candidate apply page. No auth required."""
    await set_rls_context(db, "", "super_admin")
    job = await _get_active_public_job(job_code, db)

    # Check application deadline
    deadline = job["application_deadline"]
    deadline_passed = bool(deadline and deadline < date.today())

    # Determine intake open/closed state
    intake_open = True
    if deadline_passed:
        intake_open = False

    applications_count: int | None = None
    if job["max_applications"] is not None:
        applications_count = await _count_valid_applications(str(job["job_id"]), db)
        if applications_count >= job["max_applications"]:
            intake_open = False

    return {
        "job_title":           job["title"],
        "job_code":            job["job_code"],
        "job_client":          job["department"] or "",
        "location":            job["location"] or "",
        "job_type":            job["job_type"] or "",
        "work_mode":           job["work_mode"] or "",
        "experience_level":    job["experience_level"] or "",
        "description":         job["description"] or "",
        "application_deadline": deadline.isoformat() if deadline else None,
        "intake_open":         intake_open,
        "deadline_passed":     deadline_passed,
        "max_applications":    job["max_applications"],
        "applications_count":  applications_count,
    }


@router.post("/applications/public", status_code=http_status.HTTP_201_CREATED)
async def submit_public_application(
    db: Annotated[AsyncSession, Depends(get_db)],
    job_code: Annotated[str, Form()],
    candidate_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    phone: Annotated[str | None, Form()] = None,
    cover_letter: Annotated[str | None, Form()] = None,
    file: UploadFile = File(...),
):
    """Accept a public CV submission. No auth required."""

    # ── Validate file ──────────────────────────────────────────────────────
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF, DOC, and DOCX files are accepted",
        )
    content = await file.read()
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.max_file_size_mb} MB",
        )

    await set_rls_context(db, "", "super_admin")

    # ── Validate job and intake state ──────────────────────────────────────
    job = await _get_active_public_job(job_code, db)
    job_id = str(job["job_id"])
    tenant_id = str(job["tenant_id"])

    deadline = job["application_deadline"]
    if deadline and deadline < date.today():
        raise HTTPException(
            status_code=http_status.HTTP_410_GONE,
            detail="The application deadline for this position has passed",
        )

    if job["max_applications"] is not None:
        count = await _count_valid_applications(job_id, db)
        if count >= job["max_applications"]:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="This position is no longer accepting applications",
            )

    # ── Insert application record ─────────────────────────────────────────
    app_result = await db.execute(
        text("""
            INSERT INTO applications
                (job_id, tenant_id, candidate_name, candidate_email,
                 candidate_phone, cover_letter, submission_source, processing_status)
            VALUES
                (:jid, :tid, :name, :email,
                 :phone, :cover_letter, 'public_apply', 'pending')
            RETURNING application_id
        """),
        {
            "jid":          job_id,
            "tid":          tenant_id,
            "name":         candidate_name.strip(),
            "email":        email.strip().lower(),
            "phone":        phone.strip() if phone else None,
            "cover_letter": cover_letter.strip() if cover_letter else None,
        },
    )
    application_id = str(app_result.scalar_one())

    # ── Save file ─────────────────────────────────────────────────────────
    ext = ALLOWED_MIME_TYPES[file.content_type]
    file_dir = Path(settings.files_base_path) / "tenants" / tenant_id / "jobs" / job_id
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / f"{application_id}.{ext}"
    file_path.write_bytes(content)
    relative_path = str(file_path.relative_to(settings.files_base_path))

    await db.execute(
        text("""
            INSERT INTO application_files
                (application_id, tenant_id, original_name, mime_type,
                 file_path, file_size_bytes, extraction_status)
            VALUES (:aid, :tid, :orig, :mime, :path, :size, 'pending')
        """),
        {
            "aid":  application_id,
            "tid":  tenant_id,
            "orig": file.filename,
            "mime": file.content_type,
            "path": relative_path,
            "size": len(content),
        },
    )
    await db.commit()

    # ── Auto-close if limit now reached ──────────────────────────────────
    if job["max_applications"] is not None and job["auto_close_when_limit_reached"]:
        new_count = await _count_valid_applications(job_id, db)
        if new_count >= job["max_applications"]:
            await db.execute(
                text("UPDATE jobs SET status = 'closed' WHERE job_id = :jid"),
                {"jid": job_id},
            )
            await db.commit()

    return {
        "application_id": application_id,
        "message": "Application received successfully",
    }
