"""
Public (unauthenticated) endpoints for the candidate-facing apply flow.

GET  /jobs/public/{job_code}  — active job info + intake status, no auth
POST /applications/public      — candidate submits CV + metadata, no auth
"""
import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi import status as http_status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db, set_rls_context
from services.application_intake_service import (
    IntakeValidationError,
    process_cv_intake,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["public"])
settings = get_settings()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_active_public_job(job_code: str, db: AsyncSession) -> dict:
    """Return active job row by job_code. Caller must have set RLS context."""
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
    """Count non-duplicate, non-failed applications for intake limit enforcement."""
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


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/jobs/public/{job_code}")
async def get_public_job(
    job_code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return public job details for the candidate apply page. No auth required."""
    await set_rls_context(db, "", "super_admin")
    job = await _get_active_public_job(job_code, db)

    deadline = job["application_deadline"]
    deadline_passed = bool(deadline and deadline < date.today())

    intake_open = True
    if deadline_passed:
        intake_open = False

    applications_count: int | None = None
    if job["max_applications"] is not None:
        applications_count = await _count_valid_applications(str(job["job_id"]), db)
        if applications_count >= job["max_applications"]:
            intake_open = False

    from services.knockout_questions_service import get_public_job_knockout_questions
    knockout_questions = await get_public_job_knockout_questions(db, str(job["job_id"]))

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
        "knockout_questions":  knockout_questions,
    }


@router.post("/applications/public", status_code=http_status.HTTP_201_CREATED)
async def submit_public_application(
    db: Annotated[AsyncSession, Depends(get_db)],
    job_code: Annotated[str, Form()],
    candidate_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    phone: Annotated[str | None, Form()] = None,
    cover_letter: Annotated[str | None, Form()] = None,
    knockout_answers: Annotated[str | None, Form()] = None,
    file: UploadFile = File(...),
):
    """Accept a public CV submission. No auth required."""
    import json as _json
    parsed_answers: list[dict] = []
    if knockout_answers:
        try:
            parsed_answers = _json.loads(knockout_answers)
        except Exception:
            pass

    try:
        return await _handle_public_submission(
            db=db,
            job_code=job_code,
            candidate_name=candidate_name,
            email=email,
            phone=phone,
            cover_letter=cover_letter,
            file=file,
            knockout_answers=parsed_answers,
        )
    except HTTPException:
        raise
    except IntakeValidationError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail=str(exc),
        ) from exc
    except IntegrityError as exc:
        logger.error("DB constraint violation on public apply for job %s: %s", job_code, exc)
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Submission could not be saved — please try again or contact support.",
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error on public apply for job %s: %s", job_code, exc)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        ) from exc


async def _handle_public_submission(
    *,
    db: AsyncSession,
    job_code: str,
    candidate_name: str,
    email: str,
    phone: str | None,
    cover_letter: str | None,
    file: UploadFile,
    knockout_answers: list[dict] | None = None,
) -> dict:
    content = await file.read()

    await set_rls_context(db, "", "super_admin")

    # ── Validate job and intake state ─────────────────────────────────────────
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

    # ── Delegate to unified intake service ────────────────────────────────────
    result = await process_cv_intake(
        db,
        intake_method="public_apply",
        job_id=job_id,
        tenant_id=tenant_id,
        candidate_name=candidate_name.strip(),
        candidate_email=email.strip().lower(),
        content_type=file.content_type,
        content=content,
        original_filename=file.filename,
        submission_source="public_apply",
        auto_score=True,
        files_base_path=settings.files_base_path,
        max_file_size_mb=settings.max_file_size_mb,
        candidate_phone=phone.strip() if phone else None,
        cover_letter=cover_letter.strip() if cover_letter else None,
    )

    if result.status == "INTAKE_BLOCKED":
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This position is not currently accepting applications. Please try again later.",
        )
    if result.status == "DUPLICATE_APPLICATION":
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="This CV has already been submitted for this position.",
        )
    if result.status == "REJECTED":
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This position is temporarily unable to accept applications. Please try again later.",
        )

    if knockout_answers and result.application_id:
        try:
            from services.knockout_questions_service import save_knockout_answers
            await save_knockout_answers(db, result.application_id, job_id, knockout_answers)
            await db.commit()
        except Exception as exc:
            logger.error("Failed to save knockout answers for application %s: %s", result.application_id, exc)

    # ── Auto-close job if intake limit now reached ────────────────────────────
    if job["max_applications"] is not None and job["auto_close_when_limit_reached"]:
        try:
            new_count = await _count_valid_applications(job_id, db)
            if new_count >= job["max_applications"]:
                await db.execute(
                    text("UPDATE jobs SET status = 'closed' WHERE job_id = :jid"),
                    {"jid": job_id},
                )
                await db.commit()
                logger.info(
                    "Job %s auto-closed: %d/%d applications reached",
                    job_id, new_count, job["max_applications"],
                )
        except Exception as exc:
            logger.error("Auto-close check failed for job %s: %s", job_id, exc)

    return {
        "application_id": result.application_id,
        "message": "Application received successfully",
    }
