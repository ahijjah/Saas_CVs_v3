import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, get_current_user
from config import get_settings
from database import get_db, set_rls_context
from workers.cv_score import score_cv_task

router = APIRouter(prefix="/applications", tags=["applications"])
settings = get_settings()


class ScorePendingRequest(BaseModel):
    job_id: str


class ResetStuckRequest(BaseModel):
    job_id: str


ALLOWED_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
}


@router.get("")
async def list_applications(
    job_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT
                a.application_id,
                a.candidate_name,
                a.decision        AS status,
                s.final_score     AS score,
                a.applied_at::date AS applied_date,
                s.evaluation_notes AS summary
            FROM applications a
            LEFT JOIN application_scores s ON s.application_id = a.application_id
            WHERE a.job_id = :jid AND a.tenant_id = :tid
            ORDER BY a.applied_at DESC
        """),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    apps = []
    for r in rows.mappings():
        apps.append({
            "application_id": str(r["application_id"]),
            "candidate_name": r["candidate_name"],
            "status": r["status"],
            "score": float(r["score"]) if r["score"] is not None else None,
            "applied_date": r["applied_date"].isoformat() if r["applied_date"] else None,
            "summary": r["summary"],
        })
    return apps


@router.get("/details")
async def get_application_details(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT
                a.application_id, a.candidate_name, a.candidate_email,
                a.decision, a.submission_source, a.processing_status,
                a.applied_at, a.scored_at,
                a.qualified_threshold_used, a.partial_threshold_used,
                j.title AS job_title, j.job_id,
                s.final_score,
                s.score_skills, s.score_experience, s.score_education,
                s.score_certifications, s.score_soft_skills,
                s.score_domain_knowledge, s.score_other,
                s.weights_snapshot,
                s.strengths, s.gaps_identified, s.red_flags,
                s.evaluation_notes, s.interview_questions,
                s.reasoning, s.raw_ai_response,
                s.local_similarity_score, s.skill_match_ratio,
                s.matched_skills, s.missing_skills,
                s.cv_language, s.gatekeeper_passed,
                s.ai_model
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            LEFT JOIN application_scores s ON s.application_id = a.application_id
            WHERE a.application_id = :aid AND a.tenant_id = :tid
        """),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    app = row.mappings().first()
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    weights = app["weights_snapshot"] or {}

    def build_dim(score_key: str, weight_key: str) -> dict:
        score = app[score_key] or 0
        weight = weights.get(weight_key, 0)
        return {"achieved": score, "max": 100, "weight": weight}

    reasoning = app["reasoning"] or {}

    return {
        "application_id": str(app["application_id"]),
        "candidate_name": app["candidate_name"],
        "candidate_email": app["candidate_email"],
        "decision": app["decision"],
        "overall_score": float(app["final_score"]) if app["final_score"] is not None else 0,
        "submission_source": app["submission_source"],
        "processing_status": app["processing_status"],
        "applied_at": app["applied_at"].isoformat() if app["applied_at"] else None,
        "scored_at": app["scored_at"].isoformat() if app["scored_at"] else None,
        "job_id": str(app["job_id"]),
        "job_title": app["job_title"],
        "qualified_threshold_used": app["qualified_threshold_used"],
        "partial_threshold_used": app["partial_threshold_used"],
        "scores": {
            "skills":           build_dim("score_skills",           "weight_skills"),
            "experience":       build_dim("score_experience",       "weight_experience"),
            "education":        build_dim("score_education",        "weight_education"),
            "certifications":   build_dim("score_certifications",   "weight_certifications"),
            "soft_skills":      build_dim("score_soft_skills",      "weight_soft_skills"),
            "domain_knowledge": build_dim("score_domain_knowledge", "weight_domain_knowledge"),
            "other_requirements": build_dim("score_other",          "weight_other"),
        },
        "analysis": {
            "summary":                    app["evaluation_notes"] or "",
            "strengths":                  app["strengths"] or [],
            "risks":                      app["gaps_identified"] or [],
            "gaps_identified":            app["gaps_identified"] or [],
            "evaluation_notes":           app["evaluation_notes"],
            "interview_suggested_questions": app["interview_questions"] or [],
            "interview_focus_points":     [],
        },
        "red_flags":              app["red_flags"] or [],
        "reasoning":              reasoning,
        "cv_language":            app["cv_language"],
        "local_similarity_score": float(app["local_similarity_score"]) if app["local_similarity_score"] is not None else None,
        "skill_match_ratio":      float(app["skill_match_ratio"]) if app["skill_match_ratio"] is not None else None,
        "matched_skills":         app["matched_skills"] or [],
        "missing_skills":         app["missing_skills"] or [],
        "gatekeeper_passed":      app["gatekeeper_passed"],
        "ai_model":               app["ai_model"],
        "raw_ai_response":        app["raw_ai_response"],
    }


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_cv(
    job_id: Annotated[str, Form()],
    candidate_name: Annotated[str, Form()],
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    candidate_email: Annotated[str | None, Form()] = None,
    file: UploadFile = File(...),
):
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF and DOCX files are accepted",
        )

    content = await file.read()
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.max_file_size_mb}MB",
        )

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id, title FROM jobs WHERE job_id = :jid AND tenant_id = :tid AND status = 'active'"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    job = job_row.mappings().first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active job not found")

    app_result = await db.execute(
        text("""
            INSERT INTO applications
                (job_id, tenant_id, candidate_name, candidate_email, submission_source, processing_status)
            VALUES (:jid, :tid, :name, :email, 'manual_upload', 'pending')
            RETURNING application_id
        """),
        {
            "jid": job_id,
            "tid": current_user.tenant_id,
            "name": candidate_name,
            "email": candidate_email,
        },
    )
    application_id = str(app_result.scalar_one())

    ext = ALLOWED_MIME_TYPES[file.content_type]
    file_name = f"{application_id}.{ext}"
    file_dir = Path(settings.files_base_path) / "tenants" / current_user.tenant_id / "jobs" / job_id
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / file_name
    file_path.write_bytes(content)

    relative_path = str(file_path.relative_to(settings.files_base_path))

    await db.execute(
        text("""
            INSERT INTO application_files
                (application_id, tenant_id, original_name, mime_type, file_path, file_size_bytes, extraction_status)
            VALUES (:aid, :tid, :orig, :mime, :path, :size, 'pending')
        """),
        {
            "aid": application_id,
            "tid": current_user.tenant_id,
            "orig": file.filename,
            "mime": file.content_type,
            "path": relative_path,
            "size": len(content),
        },
    )
    await db.commit()

    return {
        "application_id": application_id,
        "status": "pending",
        "message": "CV uploaded. Click 'Score uploaded CVs' to start scoring.",
    }


@router.get("/uploaded")
async def list_uploaded_cvs(
    job_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List manually uploaded CVs for a job with their processing status."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT
                a.application_id,
                a.candidate_name,
                a.processing_status,
                a.decision,
                a.evaluation_stage,
                a.evaluation_exit_reason,
                s.final_score,
                a.applied_at,
                af.original_name
            FROM applications a
            LEFT JOIN application_files af ON af.application_id = a.application_id
            LEFT JOIN application_scores s ON s.application_id = a.application_id
            WHERE a.job_id = :jid
              AND a.tenant_id = :tid
              AND a.submission_source = 'manual_upload'
            ORDER BY a.applied_at DESC
        """),
        {"jid": job_id, "tid": current_user.tenant_id},
    )

    _stage_labels = {
        1: "Level 1 — Local Pre-screening",
        2: "Level 2 — Lightweight AI Evaluation",
        3: "Level 3 — Full AI Scoring",
    }

    uploads = []
    for r in rows.mappings():
        stage = r["evaluation_stage"]
        uploads.append({
            "application_id":        str(r["application_id"]),
            "candidate_name":        r["candidate_name"],
            "processing_status":     r["processing_status"],
            "decision":              r["decision"],
            "evaluation_stage":      stage,
            "evaluation_stage_label": _stage_labels.get(stage) if stage else None,
            "evaluation_exit_reason": r["evaluation_exit_reason"],
            "score":       float(r["final_score"]) if r["final_score"] is not None else None,
            "uploaded_at": r["applied_at"].isoformat() if r["applied_at"] else None,
            "original_filename": r["original_name"],
        })
    return uploads


@router.post("/score-pending", status_code=status.HTTP_202_ACCEPTED)
async def score_pending_uploads(
    body: ScorePendingRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Atomically claim all pending CVs (pending→queued) then enqueue scoring tasks.

    The CTE UPDATE is atomic: concurrent requests see each CV in 'pending' exactly
    once, eliminating double-enqueue on rapid double-click.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": body.job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    batch_id = str(uuid.uuid4())

    # Atomic claim: UPDATE pending→queued and return file info in one statement.
    # Any concurrent request will find zero 'pending' rows and claim nothing.
    rows = await db.execute(
        text("""
            WITH claimed AS (
                UPDATE applications
                SET processing_status = 'queued',
                    scoring_batch_id   = CAST(:batch_id AS uuid),
                    queued_at          = now()
                WHERE job_id = :jid
                  AND tenant_id = :tid
                  AND submission_source = 'manual_upload'
                  AND processing_status = 'pending'
                RETURNING application_id
            )
            SELECT c.application_id, af.file_path, af.mime_type
            FROM claimed c
            JOIN application_files af ON af.application_id = c.application_id
        """),
        {"batch_id": batch_id, "jid": body.job_id, "tid": current_user.tenant_id},
    )
    claimed = rows.mappings().all()
    await db.commit()

    if not claimed:
        return {"success": True, "queued": 0, "batch_id": None, "message": "No pending CVs to score."}

    count = 0
    for row in claimed:
        full_path = str(Path(settings.files_base_path) / row["file_path"])
        score_cv_task.delay(
            application_id=str(row["application_id"]),
            job_id=body.job_id,
            tenant_id=current_user.tenant_id,
            file_path=full_path,
            mime_type=row["mime_type"],
        )
        count += 1

    return {"success": True, "queued": count, "batch_id": batch_id, "message": f"Queued scoring for {count} CV(s)"}


@router.get("/queue-status")
async def get_queue_status(
    job_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Backend-driven progress snapshot for manual upload queue."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE processing_status = 'pending')    AS pending,
                COUNT(*) FILTER (WHERE processing_status = 'queued')     AS queued,
                COUNT(*) FILTER (WHERE processing_status = 'processing') AS processing,
                COUNT(*) FILTER (WHERE processing_status IN ('scored', 'low_match')) AS completed,
                COUNT(*) FILTER (WHERE processing_status = 'failed')     AS failed,
                COUNT(*) FILTER (
                    WHERE processing_status IN ('queued', 'processing')
                      AND queued_at IS NOT NULL
                      AND queued_at < now() - INTERVAL '10 minutes'
                ) AS stuck
            FROM applications
            WHERE job_id = :jid
              AND tenant_id = :tid
              AND submission_source = 'manual_upload'
        """),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    r = row.mappings().first()
    if not r:
        return {
            "total": 0, "pending": 0, "queued": 0, "processing": 0,
            "completed": 0, "failed": 0,
            "is_processing": False, "has_stuck": False, "percentage": 0,
        }

    total = int(r["total"])
    completed = int(r["completed"])
    in_flight = int(r["queued"]) + int(r["processing"])
    percentage = round((completed / total) * 100) if total > 0 else 0

    return {
        "total": total,
        "pending": int(r["pending"]),
        "queued": int(r["queued"]),
        "processing": int(r["processing"]),
        "completed": completed,
        "failed": int(r["failed"]),
        "is_processing": in_flight > 0,
        "has_stuck": int(r["stuck"]) > 0,
        "percentage": percentage,
    }


@router.post("/reset-stuck", status_code=status.HTTP_200_OK)
async def reset_stuck_cvs(
    body: ResetStuckRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Reset queued/processing CVs that have been stuck for >10 minutes back to pending."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": body.job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    result = await db.execute(
        text("""
            UPDATE applications
            SET processing_status = 'pending',
                scoring_batch_id  = NULL,
                queued_at         = NULL
            WHERE job_id = :jid
              AND tenant_id = :tid
              AND submission_source = 'manual_upload'
              AND processing_status IN ('queued', 'processing')
              AND queued_at IS NOT NULL
              AND queued_at < now() - INTERVAL '10 minutes'
            RETURNING application_id
        """),
        {"jid": body.job_id, "tid": current_user.tenant_id},
    )
    reset_count = len(result.mappings().all())
    await db.commit()

    return {
        "success": True,
        "reset": reset_count,
        "message": f"Reset {reset_count} stuck CV(s) back to pending.",
    }


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_uploaded_cv(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a manually uploaded CV. Only allowed when status=pending. Tenant-isolated."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT a.application_id
            FROM applications a
            WHERE a.application_id = :aid
              AND a.tenant_id = :tid
              AND a.submission_source = 'manual_upload'
              AND a.processing_status = 'pending'
        """),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    if not row.first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found or cannot be deleted in its current state",
        )

    file_row = await db.execute(
        text("SELECT file_path FROM application_files WHERE application_id = :aid AND tenant_id = :tid"),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    file_record = file_row.mappings().first()

    await db.execute(
        text("DELETE FROM application_scores WHERE application_id = :aid"),
        {"aid": application_id},
    )
    await db.execute(
        text("DELETE FROM application_files WHERE application_id = :aid AND tenant_id = :tid"),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    await db.execute(
        text("DELETE FROM applications WHERE application_id = :aid AND tenant_id = :tid"),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    await db.commit()

    if file_record and file_record["file_path"]:
        try:
            full_path = Path(settings.files_base_path) / file_record["file_path"]
            if full_path.exists():
                full_path.unlink()
        except OSError:
            pass


# Import here to avoid circular import
from auth.dependencies import get_current_user  # noqa: E402
