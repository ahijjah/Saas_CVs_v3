import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, get_current_user
from config import get_settings
from database import get_db, set_rls_context

router = APIRouter(prefix="/jobs", tags=["jobs"])
settings = get_settings()


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateJobRequest(BaseModel):
    title: str
    department: str | None = None
    location: str | None = None
    job_type: str | None = None
    duration: str | None = None
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


class UpdateIngestionRequest(BaseModel):
    forwarding_enabled: bool | None = None
    alias_enabled: bool | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_forwarding_email(db) -> str:
    """Load FORWARDING_EMAIL from system_config (falls back to env IMAP_USER)."""
    row = await db.execute(
        text("SELECT value FROM system_config WHERE key = 'forwarding_email'")
    )
    result = row.scalar_one_or_none()
    return result or settings.imap_user


async def _next_job_code(db) -> str:
    """Generate next sequential job code: JOB-YYYY-NNNN."""
    year = datetime.now().year
    count_row = await db.execute(
        text("""
            SELECT COUNT(*) FROM jobs
            WHERE EXTRACT(YEAR FROM created_at) = :year
        """),
        {"year": year},
    )
    seq = count_row.scalar_one() + 1
    return f"JOB-{year}-{seq:04d}"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_jobs(current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT
                j.job_id,
                j.job_code,
                j.title           AS job_title,
                j.department      AS job_client,
                INITCAP(j.status) AS job_status,
                j.platform_email,
                j.forwarding_enabled,
                j.alias_enabled,
                j.created_at,
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
    jobs = []
    for r in rows.mappings():
        uid = str(r["job_id"])
        jobs.append({
            "job_id":             uid,
            "job_code":           r["job_code"] or uid[:8].upper(),
            "job_title":          r["job_title"],
            "job_client":         r["job_client"] or "",
            "job_status":         r["job_status"],
            "platform_email":     r["platform_email"],
            "forwarding_enabled": r["forwarding_enabled"],
            "alias_enabled":      r["alias_enabled"],
            "posted_date":        r["created_at"].date().isoformat() if r["created_at"] else None,
            "applications_total":     r["applications_total"],
            "applications_qualified": r["applications_qualified"],
            "applications_partial":   r["applications_partial"],
            "applications_rejected":  r["applications_rejected"],
        })
    return jobs


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(
    body: CreateJobRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    t_row = await db.execute(
        text("SELECT email_domain FROM tenants WHERE tenant_id = :tid"),
        {"tid": current_user.tenant_id},
    )
    tenant = t_row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    job_code = await _next_job_code(db)

    job_result = await db.execute(
        text("""
            INSERT INTO jobs (tenant_id, created_by, title, department, location,
                              job_type, duration, description,
                              qualified_threshold, partial_threshold, job_code, status)
            VALUES (:tid, :uid, :title, :dept, :location, :job_type, :duration, :desc,
                    :qt, :pt, :job_code, 'active')
            RETURNING job_id
        """),
        {
            "tid":      current_user.tenant_id,
            "uid":      current_user.user_id,
            "title":    body.title,
            "dept":     body.department,
            "location": body.location,
            "job_type": body.job_type,
            "duration": body.duration,
            "desc":     body.description,
            "qt":       body.qualified_threshold,
            "pt":       body.partial_threshold,
            "job_code": job_code,
        },
    )
    job_id = str(job_result.scalar_one())

    platform_email = f"{job_code}@{tenant['email_domain']}"
    await db.execute(
        text("UPDATE jobs SET platform_email = :email WHERE job_id = :jid"),
        {"email": platform_email, "jid": job_id},
    )

    # Insert a pending criteria row immediately — AI extraction runs in background.
    # Default weights (30+25+15+10+10+5+5=100) satisfy the weights_sum_100 constraint.
    await db.execute(
        text("""
            INSERT INTO job_criteria (job_id, criteria_extraction_status)
            VALUES (:jid, 'pending')
        """),
        {"jid": job_id},
    )
    await db.commit()

    job_dir = Path(settings.files_base_path) / "tenants" / current_user.tenant_id / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Queue async AI extraction — does not block job creation
    from workers.criteria_worker import extract_criteria_task
    extract_criteria_task.delay(job_id, body.description)

    return {
        "success": True,
        "job_id": job_id,
        "job_code": job_code,
        "platform_email": platform_email,
        "criteria_extraction_status": "pending",
        "message": "Job created successfully. AI criteria extraction started in background.",
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
                j.job_id, j.job_code, j.title, j.department, j.description,
                j.status, j.platform_email,
                j.forwarding_enabled, j.alias_enabled,
                j.qualified_threshold, j.partial_threshold,
                j.created_at,
                COUNT(a.application_id)                                             AS applications_total,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'qualified')    AS applications_qualified,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'partial')      AS applications_partial,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'rejected')     AS applications_rejected,
                t.cv_ingestion_mode AS tenant_ingestion_mode,
                t.forwarding_email  AS tenant_forwarding_email
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
            SELECT analysis_json,
                   criteria_extraction_status,
                   criteria_extraction_error,
                   criteria_extracted_at,
                   weight_skills, weight_experience, weight_education,
                   weight_certifications, weight_soft_skills,
                   weight_domain_knowledge, weight_other,
                   ai_model, ai_generated_at, last_edited_at
            FROM job_criteria WHERE job_id = :jid
        """),
        {"jid": job_id},
    )
    criteria = criteria_row.mappings().first()

    # Load FORWARDING_EMAIL from system_config
    forwarding_email = await _get_forwarding_email(db)

    job_code = job["job_code"] or str(job["job_id"])[:8].upper()

    extraction_status = criteria["criteria_extraction_status"] if criteria else "pending"
    extraction_error  = criteria["criteria_extraction_error"]  if criteria else None

    # analysis_json is the nested AnalysisJson structure; fall back to None when pending
    analysis_json = criteria["analysis_json"] if criteria else None

    return {
        "details": {
            "job_id":             str(job["job_id"]),
            "job_code":           job_code,
            "job_title":          job["title"],
            "job_client":         job["department"] or "",
            "job_status":         job["status"].capitalize(),
            "description":        job["description"],
            "platform_email":     job["platform_email"],
            "forwarding_email":   forwarding_email,
            "forwarding_enabled": job["forwarding_enabled"],
            "alias_enabled":      job["alias_enabled"],
            "created_at":         job["created_at"].isoformat() if job["created_at"] else None,
            "applications_total":     job["applications_total"],
            "applications_qualified": job["applications_qualified"],
            "applications_partial":   job["applications_partial"],
            "applications_rejected":  job["applications_rejected"],
            "qualified_threshold":    job["qualified_threshold"],
            "partial_threshold":      job["partial_threshold"],
            "criteria_extraction_status": extraction_status,
            "criteria_extraction_error":  extraction_error,
            # Legacy field — kept for backward compat
            "ingestion_note": (
                f"Send CVs directly to: {job['platform_email']}"
                if job["alias_enabled"]
                else f"Forward CVs to: {forwarding_email} — include {job_code} in subject"
            ),
        },
        "analysis": analysis_json,
    }


@router.put("/{job_id}/ingestion")
async def update_ingestion_settings(
    job_id: str,
    body: UpdateIngestionRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Toggle forwarding_enabled / alias_enabled for a job."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    updates: dict = {}
    if body.forwarding_enabled is not None:
        updates["forwarding_enabled"] = body.forwarding_enabled
    if body.alias_enabled is not None:
        updates["alias_enabled"] = body.alias_enabled

    if not updates:
        return {"success": True, "message": "No changes"}

    set_sql = ", ".join(f"{k} = :{k}" for k in updates)
    updates["jid"] = job_id
    await db.execute(
        text(f"UPDATE jobs SET {set_sql} WHERE job_id = :jid"),
        updates,
    )
    await db.commit()
    return {"success": True, "message": "Ingestion settings updated"}


@router.put("/{job_id}/criteria")
async def update_criteria(
    job_id: str,
    body: UpdateCriteriaRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

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

    # Validate individual weight ranges
    incoming_weights = {
        "weight_skills":           body.weight_skills,
        "weight_experience":       body.weight_experience,
        "weight_education":        body.weight_education,
        "weight_certifications":   body.weight_certifications,
        "weight_soft_skills":      body.weight_soft_skills,
        "weight_domain_knowledge": body.weight_domain_knowledge,
        "weight_other":            body.weight_other,
    }
    for field, val in incoming_weights.items():
        if val is not None and not (0 <= val <= 100):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Each weight must be between 0 and 100. '{field}' is {val}.",
            )

    w = {
        "weight_skills":           body.weight_skills           if body.weight_skills           is not None else existing["weight_skills"],
        "weight_experience":       body.weight_experience       if body.weight_experience       is not None else existing["weight_experience"],
        "weight_education":        body.weight_education        if body.weight_education        is not None else existing["weight_education"],
        "weight_certifications":   body.weight_certifications   if body.weight_certifications   is not None else existing["weight_certifications"],
        "weight_soft_skills":      body.weight_soft_skills      if body.weight_soft_skills      is not None else existing["weight_soft_skills"],
        "weight_domain_knowledge": body.weight_domain_knowledge if body.weight_domain_knowledge is not None else existing["weight_domain_knowledge"],
        "weight_other":            body.weight_other            if body.weight_other            is not None else existing["weight_other"],
    }
    total = sum(w.values())
    if total != 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Evaluation weights must total exactly 100%. Current total: {total}%.",
        )

    # Sync scoring_weights inside analysis_json so GET /details reflects updated values
    new_scoring_weights = json.dumps({
        "skills":           w["weight_skills"],
        "experience":       w["weight_experience"],
        "education":        w["weight_education"],
        "certifications":   w["weight_certifications"],
        "soft_skills":      w["weight_soft_skills"],
        "domain_knowledge": w["weight_domain_knowledge"],
        "other_requirements": w["weight_other"],
    })

    update_fields: dict = {**w, "new_scoring_weights": new_scoring_weights, "last_edited_by": current_user.user_id, "jid": job_id}
    for col in ["skills", "experience", "education", "certifications",
                "soft_skills", "domain_knowledge", "other_requirements"]:
        val = getattr(body, col, None)
        if val is not None:
            update_fields[col] = val

    array_sets = "".join(
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
                analysis_json = CASE
                    WHEN analysis_json IS NOT NULL THEN
                        jsonb_set(analysis_json, '{{scoring_weights}}', CAST(:new_scoring_weights AS jsonb), true)
                    ELSE analysis_json
                END,
                last_edited_by = :last_edited_by,
                last_edited_at = now()
                {array_sets}
            WHERE job_id = :jid
        """),
        update_fields,
    )
    await db.commit()
    return {"success": True, "message": "Criteria updated"}


@router.post("/{job_id}/criteria/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_criteria_extraction(
    job_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Re-queue AI criteria extraction for a job whose extraction previously failed."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT description FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    job = job_row.mappings().first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    await db.execute(
        text("""
            UPDATE job_criteria
            SET criteria_extraction_status = 'pending',
                criteria_extraction_error  = NULL
            WHERE job_id = :jid
        """),
        {"jid": job_id},
    )
    await db.commit()

    from workers.criteria_worker import extract_criteria_task
    extract_criteria_task.delay(job_id, job["description"])

    return {"success": True, "message": "Criteria extraction re-queued."}
