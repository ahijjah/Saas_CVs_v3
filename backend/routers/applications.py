import os
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, get_current_user
from config import get_settings
from database import get_db, set_rls_context
from workers.cv_score import score_cv_task

router = APIRouter(prefix="/applications", tags=["applications"])
settings = get_settings()

ALLOWED_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
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
        # Shape matches ApplicationDetailedAnalysis.scores in types.ts
        "scores": {
            "skills":           build_dim("score_skills",           "weight_skills"),
            "experience":       build_dim("score_experience",       "weight_experience"),
            "education":        build_dim("score_education",        "weight_education"),
            "certifications":   build_dim("score_certifications",   "weight_certifications"),
            "soft_skills":      build_dim("score_soft_skills",      "weight_soft_skills"),
            "domain_knowledge": build_dim("score_domain_knowledge", "weight_domain_knowledge"),
            "other_requirements": build_dim("score_other",          "weight_other"),
        },
        # Shape matches ApplicationDetailedAnalysis.analysis in types.ts
        "analysis": {
            "summary":                    app["evaluation_notes"] or "",
            "strengths":                  app["strengths"] or [],
            "risks":                      app["gaps_identified"] or [],
            "gaps_identified":            app["gaps_identified"] or [],
            "evaluation_notes":           app["evaluation_notes"],
            "interview_suggested_questions": app["interview_questions"] or [],
            "interview_focus_points":     [],
        },
        # Intelligence fields from Gatekeeper + LLM
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
    # Validate file type
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF and DOCX files are accepted",
        )

    # Validate file size
    content = await file.read()
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.max_file_size_mb}MB",
        )

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Verify job exists and belongs to tenant
    job_row = await db.execute(
        text("SELECT job_id, title FROM jobs WHERE job_id = :jid AND tenant_id = :tid AND status = 'active'"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    job = job_row.mappings().first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active job not found")

    # Create application record
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

    # Store file
    ext = ALLOWED_MIME_TYPES[file.content_type]
    file_name = f"{application_id}.{ext}"
    file_dir = Path(settings.files_base_path) / "tenants" / current_user.tenant_id / "jobs" / job_id
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / file_name
    file_path.write_bytes(content)

    relative_path = str(file_path.relative_to(settings.files_base_path))

    # Create file record
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

    # Enqueue scoring task
    score_cv_task.delay(
        application_id=application_id,
        job_id=job_id,
        tenant_id=current_user.tenant_id,
        file_path=str(file_path),
        mime_type=file.content_type,
    )

    return {
        "application_id": application_id,
        "status": "processing",
        "message": "CV uploaded and queued for scoring",
    }


# Import here to avoid circular import
from auth.dependencies import get_current_user  # noqa: E402
