import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, get_current_user
from config import get_settings
from database import get_db, set_rls_context
from services.ai_service import extract_job_criteria
from services.threshold_service import get_thresholds

router = APIRouter(prefix="/jobs", tags=["jobs"])
settings = get_settings()


# ── Schemas ──────────────────────────────────────────────────────────────────

class CreateJobRequest(BaseModel):
    title: str
    department: str | None = None
    description: str
    qualified_threshold: int | None = None
    partial_threshold: int | None = None


class UpdateCriteriaRequest(BaseModel):
    skills: list[str] | None = None
    experience: list[str] | None = None
    education: list[str] | None = None
    certifications: list[str] | None = None
    soft_skills: list[str] | None = None
    domain_knowledge: list[str] | None = None
    other_requirements: list[str] | None = None
    weight_skills: int | None = None
    weight_experience: int | None = None
    weight_education: int | None = None
    weight_certifications: int | None = None
    weight_soft_skills: int | None = None
    weight_domain_knowledge: int | None = None
    weight_other: int | None = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_jobs(current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT
                j.job_id, j.title, j.department, j.status,
                j.platform_email, j.created_at,
                COUNT(a.application_id)                                             AS applications_total,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'qualified')    AS applications_qualified,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'partial')      AS applications_partial,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'rejected')     AS applications_rejected
            FROM jobs j
            LEFT JOIN applications a ON a.job_id = j.job_id
            WHERE j.tenant_id = :tid
            GROUP BY j.job_id
            ORDER BY j.created_at DESC
        """),
        {"tid": current_user.tenant_id},
    )
    jobs = [dict(r) for r in rows.mappings()]
    for j in jobs:
        j["job_id"] = str(j["job_id"])
        j["created_at"] = j["created_at"].date().isoformat() if j["created_at"] else None
    return {"jobs": jobs}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(
    body: CreateJobRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Fetch tenant email domain for platform_email construction
    t_row = await db.execute(
        text("SELECT email_domain FROM tenants WHERE tenant_id = :tid"),
        {"tid": current_user.tenant_id},
    )
    tenant = t_row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    # Insert job
    job_result = await db.execute(
        text("""
            INSERT INTO jobs (tenant_id, created_by, title, department, description,
                              qualified_threshold, partial_threshold, status)
            VALUES (:tid, :uid, :title, :dept, :desc, :qt, :pt, 'active')
            RETURNING job_id
        """),
        {
            "tid": current_user.tenant_id,
            "uid": current_user.user_id,
            "title": body.title,
            "dept": body.department,
            "desc": body.description,
            "qt": body.qualified_threshold,
            "pt": body.partial_threshold,
        },
    )
    job_id = str(job_result.scalar_one())

    # Set platform email
    platform_email = f"{job_id}@{tenant['email_domain']}"
    await db.execute(
        text("UPDATE jobs SET platform_email = :email WHERE job_id = :jid"),
        {"email": platform_email, "jid": job_id},
    )

    # Create file storage directory
    job_dir = Path(settings.files_base_path) / "tenants" / current_user.tenant_id / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # AI criteria extraction
    try:
        ai_criteria = await extract_job_criteria(body.description)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI criteria extraction failed. Please try again.",
        )

    await db.execute(
        text("""
            INSERT INTO job_criteria (
                job_id,
                skills, experience, education, certifications,
                soft_skills, domain_knowledge, other_requirements,
                weight_skills, weight_experience, weight_education,
                weight_certifications, weight_soft_skills,
                weight_domain_knowledge, weight_other,
                ai_model, ai_generated_at
            ) VALUES (
                :jid,
                :skills, :experience, :education, :certs,
                :soft, :domain, :other,
                :w_skills, :w_exp, :w_edu, :w_cert, :w_soft, :w_domain, :w_other,
                :model, now()
            )
        """),
        {
            "jid": job_id,
            "skills": ai_criteria.get("skills", []),
            "experience": ai_criteria.get("experience", []),
            "education": ai_criteria.get("education", []),
            "certs": ai_criteria.get("certifications", []),
            "soft": ai_criteria.get("soft_skills", []),
            "domain": ai_criteria.get("domain_knowledge", []),
            "other": ai_criteria.get("other_requirements", []),
            "w_skills": ai_criteria.get("weight_skills", 30),
            "w_exp": ai_criteria.get("weight_experience", 25),
            "w_edu": ai_criteria.get("weight_education", 15),
            "w_cert": ai_criteria.get("weight_certifications", 10),
            "w_soft": ai_criteria.get("weight_soft_skills", 10),
            "w_domain": ai_criteria.get("weight_domain_knowledge", 5),
            "w_other": ai_criteria.get("weight_other", 5),
            "model": settings.openai_model,
        },
    )
    await db.commit()

    return {
        "success": True,
        "job_id": job_id,
        "platform_email": platform_email,
        "message": "Job created successfully",
    }


@router.get("/details")
async def get_job_details(
    job_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("""
            SELECT
                j.job_id, j.title, j.department, j.description,
                j.status, j.platform_email, j.cv_ingestion_mode,
                j.qualified_threshold, j.partial_threshold,
                j.created_at,
                COUNT(a.application_id)                                             AS applications_total,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'qualified')    AS applications_qualified,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'partial')      AS applications_partial,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'rejected')     AS applications_rejected,
                t.cv_ingestion_mode AS tenant_ingestion_mode,
                t.forwarding_email
            FROM jobs j
            LEFT JOIN applications a ON a.job_id = j.job_id
            JOIN tenants t ON t.tenant_id = j.tenant_id
            WHERE j.job_id = :jid AND j.tenant_id = :tid
            GROUP BY j.job_id, t.tenant_id
        """),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    job = job_row.mappings().first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    criteria_row = await db.execute(
        text("""
            SELECT skills, experience, education, certifications,
                   soft_skills, domain_knowledge, other_requirements,
                   weight_skills, weight_experience, weight_education,
                   weight_certifications, weight_soft_skills,
                   weight_domain_knowledge, weight_other,
                   ai_model, ai_generated_at, last_edited_at
            FROM job_criteria WHERE job_id = :jid
        """),
        {"jid": job_id},
    )
    criteria = criteria_row.mappings().first()

    ingestion_note = (
        f"Send CVs to: {job['platform_email']}"
        if job["tenant_ingestion_mode"] == "platform_email"
        else f"Forward CVs to: {job['forwarding_email']}"
    )

    return {
        "details": {
            "job_id": str(job["job_id"]),
            "title": job["title"],
            "department": job["department"],
            "description": job["description"],
            "status": job["status"],
            "platform_email": job["platform_email"],
            "created_at": job["created_at"].isoformat() if job["created_at"] else None,
            "applications_total": job["applications_total"],
            "applications_qualified": job["applications_qualified"],
            "applications_partial": job["applications_partial"],
            "applications_rejected": job["applications_rejected"],
            "qualified_threshold": job["qualified_threshold"],
            "partial_threshold": job["partial_threshold"],
            "ingestion_note": ingestion_note,
        },
        "analysis": dict(criteria) if criteria else None,
    }


@router.put("/{job_id}/criteria")
async def update_criteria(
    job_id: str,
    body: UpdateCriteriaRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Edit AI-generated scoring criteria for a job."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Verify job belongs to tenant
    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    # Fetch existing criteria for merge
    existing_row = await db.execute(
        text("""
            SELECT weight_skills, weight_experience, weight_education,
                   weight_certifications, weight_soft_skills,
                   weight_domain_knowledge, weight_other
            FROM job_criteria WHERE job_id = :jid
        """),
        {"jid": job_id},
    )
    existing = existing_row.mappings().first()
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Criteria not found for this job")

    # Merge weights
    w = {
        "weight_skills": body.weight_skills if body.weight_skills is not None else existing["weight_skills"],
        "weight_experience": body.weight_experience if body.weight_experience is not None else existing["weight_experience"],
        "weight_education": body.weight_education if body.weight_education is not None else existing["weight_education"],
        "weight_certifications": body.weight_certifications if body.weight_certifications is not None else existing["weight_certifications"],
        "weight_soft_skills": body.weight_soft_skills if body.weight_soft_skills is not None else existing["weight_soft_skills"],
        "weight_domain_knowledge": body.weight_domain_knowledge if body.weight_domain_knowledge is not None else existing["weight_domain_knowledge"],
        "weight_other": body.weight_other if body.weight_other is not None else existing["weight_other"],
    }
    total = sum(w.values())
    if total != 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Weights must sum to 100. Current total: {total}",
        )

    update_fields: dict = {**w, "last_edited_by": current_user.user_id, "jid": job_id}
    if body.skills is not None:
        update_fields["skills"] = body.skills
    if body.experience is not None:
        update_fields["experience"] = body.experience
    if body.education is not None:
        update_fields["education"] = body.education
    if body.certifications is not None:
        update_fields["certifications"] = body.certifications
    if body.soft_skills is not None:
        update_fields["soft_skills"] = body.soft_skills
    if body.domain_knowledge is not None:
        update_fields["domain_knowledge"] = body.domain_knowledge
    if body.other_requirements is not None:
        update_fields["other_requirements"] = body.other_requirements

    array_sets = " ".join(
        f", {col} = :{col}"
        for col in ["skills", "experience", "education", "certifications",
                    "soft_skills", "domain_knowledge", "other_requirements"]
        if col in update_fields
    )

    await db.execute(
        text(f"""
            UPDATE job_criteria SET
                weight_skills = :weight_skills,
                weight_experience = :weight_experience,
                weight_education = :weight_education,
                weight_certifications = :weight_certifications,
                weight_soft_skills = :weight_soft_skills,
                weight_domain_knowledge = :weight_domain_knowledge,
                weight_other = :weight_other,
                last_edited_by = :last_edited_by,
                last_edited_at = now()
                {array_sets}
            WHERE job_id = :jid
        """),
        update_fields,
    )
    await db.commit()
    return {"success": True, "message": "Criteria updated"}
