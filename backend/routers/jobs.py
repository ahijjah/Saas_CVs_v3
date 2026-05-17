import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, get_current_user
from config import get_settings
from database import get_db, set_rls_context
from services.subscription_service import can_create_campaign

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
    receive_cv_via_forwarding_email: bool | None = None
    receive_cv_via_platform_email: bool | None = None
    restrict_forwarding_sender_to_tenant_email: bool | None = None
    # Legacy field names kept for backward compat
    forwarding_enabled: bool | None = None
    alias_enabled: bool | None = None


class UpdateJobSettingsRequest(BaseModel):
    send_confirmation_to_cv_email_for_upload:         bool | None = None
    send_confirmation_to_cv_email_for_forwarding:     bool | None = None
    send_confirmation_to_sender_for_forwarding:       bool | None = None
    send_confirmation_to_cv_email_for_platform_email: bool | None = None
    enable_ai_comparison:                             bool | None = None


class UpdateCriteriaContentRequest(BaseModel):
    required_skills:    list[str] | None = None
    preferred_skills:   list[str] | None = None
    minimum_years:      int | None = None
    relevant_roles:     list[str] | None = None
    minimum_education:  str | None = None
    fields_of_study:    list[str] | None = None
    certifications:     list[str] | None = None
    domain_knowledge:   list[str] | None = None
    other_requirements: list[str] | None = None


class UpdateJobMetadataRequest(BaseModel):
    title: str | None = None
    department: str | None = None
    location: str | None = None
    job_type: str | None = None
    duration: str | None = None
    experience_level: str | None = None
    work_mode: str | None = None
    application_deadline: str | None = None  # ISO date string YYYY-MM-DD
    vacancies_count: int | None = None
    status: str | None = None  # active / inactive / closed
    max_applications: int | None = None  # 0 = clear limit (set to NULL)
    auto_close_when_limit_reached: bool | None = None


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

    is_super_admin = (current_user.role or "").lower() == "super_admin"

    # super_admin sees all tenants; others see only their own
    where_clause = "" if is_super_admin else "WHERE j.tenant_id = :tid"

    rows = await db.execute(
        text(f"""
            SELECT
                j.job_id,
                j.job_code,
                j.title           AS job_title,
                j.department      AS job_client,
                INITCAP(j.status) AS job_status,
                j.platform_email,
                j.receive_cv_via_forwarding_email,
                j.receive_cv_via_platform_email,
                j.created_at,
                t.name AS tenant_name,
                COUNT(a.application_id)                                             AS applications_total,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'qualified')    AS applications_qualified,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'partial')      AS applications_partial,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'rejected')     AS applications_rejected
            FROM jobs j
            JOIN tenants t ON t.tenant_id = j.tenant_id
            LEFT JOIN applications a ON a.job_id = j.job_id
            {where_clause}
            GROUP BY j.job_id, t.name
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
            "tenant_name":        r["tenant_name"],
            "receive_cv_via_forwarding_email": r["receive_cv_via_forwarding_email"],
            "receive_cv_via_platform_email":   r["receive_cv_via_platform_email"],
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

    # Verify tenant exists
    t_row = await db.execute(
        text("SELECT tenant_id FROM tenants WHERE tenant_id = :tid"),
        {"tid": current_user.tenant_id},
    )
    tenant = t_row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    # Enforce campaign limit via centralized subscription service
    campaign_check = await can_create_campaign(current_user.tenant_id, db)
    if not campaign_check["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=campaign_check["message"],
        )

    # Platform alias must use the platform domain, never the tenant domain.
    # Read from system_config so super admin can change it without a redeploy.
    domain_row = await db.execute(
        text("SELECT value FROM system_config WHERE key = 'platform_email_domain'")
    )
    platform_domain = (domain_row.scalar_one_or_none() or "ai970.cloud").strip()

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

    platform_email = f"{job_code.lower()}@{platform_domain}"
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
                j.location, j.job_type, j.duration,
                j.experience_level, j.work_mode,
                j.application_deadline, j.vacancies_count,
                j.status, j.platform_email,
                j.receive_cv_via_forwarding_email,
                j.receive_cv_via_platform_email,
                j.restrict_forwarding_sender_to_tenant_email,
                j.send_confirmation_to_cv_email_for_upload,
                j.send_confirmation_to_cv_email_for_forwarding,
                j.send_confirmation_to_sender_for_forwarding,
                j.send_confirmation_to_cv_email_for_platform_email,
                j.enable_ai_comparison,
                j.qualified_threshold, j.partial_threshold,
                j.max_applications, j.auto_close_when_limit_reached,
                j.created_at, j.updated_at,
                cu.full_name AS created_by_name,
                uu.full_name AS updated_by_name,
                COUNT(a.application_id)                                             AS applications_total,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'qualified')    AS applications_qualified,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'partial')      AS applications_partial,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'rejected')     AS applications_rejected,
                COUNT(a.application_id) FILTER (
                    WHERE (a.duplicate_status IS NULL OR a.duplicate_status = 'not_duplicate')
                      AND a.processing_status != 'failed'
                ) AS applications_valid_count,
                t.forwarding_email AS tenant_forwarding_email
            FROM jobs j
            LEFT JOIN applications a ON a.job_id = j.job_id
            JOIN tenants t ON t.tenant_id = j.tenant_id
            LEFT JOIN users cu ON cu.user_id = j.created_by
            LEFT JOIN users uu ON uu.user_id = j.updated_by
            WHERE j.job_id = :jid AND j.tenant_id = :tid
            GROUP BY j.job_id, t.tenant_id, cu.full_name, uu.full_name
        """),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    job = job_row.mappings().first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    criteria_row = await db.execute(
        text("""
            SELECT analysis_json,
                   original_analysis_json,
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

    forwarding_email = await _get_forwarding_email(db)

    job_code = job["job_code"] or str(job["job_id"])[:8].upper()

    extraction_status    = criteria["criteria_extraction_status"] if criteria else "pending"
    extraction_error     = criteria["criteria_extraction_error"]  if criteria else None
    analysis_json        = criteria["analysis_json"]          if criteria else None
    original_analysis_json = criteria["original_analysis_json"] if criteria else None

    via_forwarding = job["receive_cv_via_forwarding_email"]
    via_platform   = job["receive_cv_via_platform_email"]

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
            "receive_cv_via_forwarding_email":            via_forwarding,
            "receive_cv_via_platform_email":              via_platform,
            "restrict_forwarding_sender_to_tenant_email": job["restrict_forwarding_sender_to_tenant_email"],
            "send_confirmation_to_cv_email_for_upload":         job["send_confirmation_to_cv_email_for_upload"],
            "send_confirmation_to_cv_email_for_forwarding":     job["send_confirmation_to_cv_email_for_forwarding"],
            "send_confirmation_to_sender_for_forwarding":       job["send_confirmation_to_sender_for_forwarding"],
            "send_confirmation_to_cv_email_for_platform_email": job["send_confirmation_to_cv_email_for_platform_email"],
            "enable_ai_comparison":           job["enable_ai_comparison"],
            "created_at":         job["created_at"].isoformat() if job["created_at"] else None,
            "location":             job["location"] or "",
            "job_type":             job["job_type"] or "",
            "duration":             job["duration"] or "",
            "experience_level":     job["experience_level"] or "",
            "work_mode":            job["work_mode"] or "",
            "application_deadline": job["application_deadline"].isoformat() if job["application_deadline"] else None,
            "vacancies_count":      job["vacancies_count"],
            "updated_at":           job["updated_at"].isoformat() if job["updated_at"] else None,
            "created_by_name":      job["created_by_name"] or "",
            "updated_by_name":      job["updated_by_name"] or "",
            "applications_total":       job["applications_total"],
            "applications_qualified":   job["applications_qualified"],
            "applications_partial":     job["applications_partial"],
            "applications_rejected":    job["applications_rejected"],
            "applications_valid_count": job["applications_valid_count"],
            "qualified_threshold":      job["qualified_threshold"],
            "partial_threshold":        job["partial_threshold"],
            "max_applications":               job["max_applications"],
            "auto_close_when_limit_reached":  job["auto_close_when_limit_reached"],
            "criteria_extraction_status": extraction_status,
            "criteria_extraction_error":  extraction_error,
            "ingestion_note": (
                f"Send CVs directly to: {job['platform_email']}"
                if via_platform
                else f"Forward CVs to: {forwarding_email} — include {job_code} in subject"
            ),
        },
        "analysis": analysis_json,
        "original_analysis": original_analysis_json,
    }


@router.put("/{job_id}/ingestion")
async def update_ingestion_settings(
    job_id: str,
    body: UpdateIngestionRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update per-job CV ingestion channel settings."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    updates: dict = {}
    # New semantic names take precedence; legacy names are accepted as aliases
    if body.receive_cv_via_forwarding_email is not None:
        updates["receive_cv_via_forwarding_email"] = body.receive_cv_via_forwarding_email
    elif body.forwarding_enabled is not None:
        updates["receive_cv_via_forwarding_email"] = body.forwarding_enabled

    if body.receive_cv_via_platform_email is not None:
        updates["receive_cv_via_platform_email"] = body.receive_cv_via_platform_email
    elif body.alias_enabled is not None:
        updates["receive_cv_via_platform_email"] = body.alias_enabled

    if body.restrict_forwarding_sender_to_tenant_email is not None:
        updates["restrict_forwarding_sender_to_tenant_email"] = body.restrict_forwarding_sender_to_tenant_email

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


@router.put("/{job_id}/settings")
async def update_job_settings(
    job_id: str,
    body: UpdateJobSettingsRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update per-job confirmation email toggles and AI comparison mode."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    is_super_admin = (current_user.role or "").lower() == "super_admin"

    updates: dict = {}
    for field in (
        "send_confirmation_to_cv_email_for_upload",
        "send_confirmation_to_cv_email_for_forwarding",
        "send_confirmation_to_sender_for_forwarding",
        "send_confirmation_to_cv_email_for_platform_email",
    ):
        val = getattr(body, field, None)
        if val is not None:
            updates[field] = val

    # enable_ai_comparison is a super_admin-only field; ignore silently for others
    if is_super_admin and body.enable_ai_comparison is not None:
        updates["enable_ai_comparison"] = body.enable_ai_comparison

    if not updates:
        return {"success": True, "message": "No changes"}

    set_sql = ", ".join(f"{k} = :{k}" for k in updates)
    updates["jid"] = job_id
    await db.execute(
        text(f"UPDATE jobs SET {set_sql} WHERE job_id = :jid"),
        updates,
    )
    await db.commit()
    return {"success": True, "message": "Job settings updated"}


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


@router.get("/{job_id}/duplicate-logs")
async def get_duplicate_logs(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    tenant_id = current_user.tenant_id
    role = current_user.role
    await set_rls_context(db, tenant_id, role)

    rows = await db.execute(
        text("""
            SELECT
                dl.log_id,
                dl.duplicate_email,
                dl.duplicate_name,
                dl.attachment_hash,
                dl.received_at,
                dl.original_application_id,
                dl.email_message_id,
                dl.raw_filename,
                dl.notes,
                dl.source,
                dl.submitted_by_name,
                dl.submitted_by_email,
                -- Duplicate CV file availability
                (dl.duplicate_file_path IS NOT NULL) AS has_duplicate_cv,
                dl.duplicate_original_filename,
                dl.duplicate_content_type,
                dl.duplicate_file_size_bytes,
                -- Use stored columns; fall back to 'file_hash_match'/100 for old email records
                COALESCE(dl.duplicate_reason, 'file_hash_match')   AS duplicate_reason,
                COALESCE(dl.duplicate_similarity_score, 100.0)     AS duplicate_similarity_score,
                -- Original application context
                a.candidate_name                AS original_candidate_name,
                a.applied_at                    AS original_applied_at,
                af.original_name                AS original_cv_filename,
                af.file_path                    AS original_cv_file_path
            FROM duplicate_application_logs dl
            LEFT JOIN applications a
                ON a.application_id = dl.original_application_id
            LEFT JOIN application_files af
                ON af.application_id = dl.original_application_id
            WHERE dl.job_id = :job_id AND dl.tenant_id = :tenant_id
            ORDER BY dl.received_at DESC
            LIMIT 200
        """),
        {"job_id": job_id, "tenant_id": tenant_id},
    )

    logs = []
    for r in rows.mappings():
        entry = dict(r)
        # Serialise timestamps
        if entry.get("received_at"):
            entry["received_at"] = entry["received_at"].isoformat()
        if entry.get("original_applied_at"):
            entry["original_applied_at"] = entry["original_applied_at"].isoformat()
        # Strip internal file_path from response (clients use the API endpoint)
        entry.pop("original_cv_file_path", None)
        # Cast UUIDs to strings
        for key in ("log_id", "original_application_id"):
            if entry.get(key):
                entry[key] = str(entry[key])
        logs.append(entry)

    return {"duplicate_logs": logs}


@router.get("/{job_id}/duplicate-logs/{log_id}/cv")
async def download_duplicate_cv(
    job_id: str,
    log_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUserDep,
):
    """Serve the stored duplicate CV file for a specific duplicate log entry."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT duplicate_file_path, duplicate_original_filename, duplicate_content_type
            FROM duplicate_application_logs
            WHERE log_id = :log_id
              AND job_id = :job_id
              AND tenant_id = :tenant_id
        """),
        {
            "log_id": log_id,
            "job_id": job_id,
            "tenant_id": current_user.tenant_id,
        },
    )
    rec = row.mappings().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Duplicate log entry not found")
    if not rec["duplicate_file_path"]:
        raise HTTPException(status_code=404, detail="Duplicate CV file was not stored for this entry")

    full_path = Path(settings.files_base_path) / rec["duplicate_file_path"]
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Duplicate CV file not found on disk")

    return FileResponse(
        path=str(full_path),
        filename=rec["duplicate_original_filename"] or full_path.name,
        media_type=rec["duplicate_content_type"] or "application/octet-stream",
    )


@router.put("/{job_id}/criteria/content")
async def update_criteria_content(
    job_id: str,
    body: UpdateCriteriaContentRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update AI criteria content (skills, experience, education, etc.) for a job.
    Admin and HR Manager only. Does NOT touch original_analysis_json so the AI baseline is preserved."""
    role = (current_user.role or "").lower()
    if role not in ("admin", "hr_manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant admins and HR managers can edit evaluation criteria",
        )
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    criteria_row = await db.execute(
        text("SELECT analysis_json FROM job_criteria WHERE job_id = :jid"),
        {"jid": job_id},
    )
    existing = criteria_row.mappings().first()
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Criteria not found for this job")

    current = dict(existing["analysis_json"] or {})

    # Merge provided fields into the existing analysis_json
    if body.required_skills is not None or body.preferred_skills is not None:
        skills = dict(current.get("skills", {}))
        if body.required_skills is not None:
            skills["required"] = [s.strip() for s in body.required_skills if s.strip()]
        if body.preferred_skills is not None:
            skills["preferred"] = [s.strip() for s in body.preferred_skills if s.strip()]
        current["skills"] = skills

    if body.minimum_years is not None or body.relevant_roles is not None:
        exp = dict(current.get("experience", {}))
        if body.minimum_years is not None:
            exp["minimum_years"] = max(0, body.minimum_years)
        if body.relevant_roles is not None:
            exp["relevant_roles"] = [r.strip() for r in body.relevant_roles if r.strip()]
        current["experience"] = exp

    if body.minimum_education is not None or body.fields_of_study is not None:
        edu = dict(current.get("education", {}))
        if body.minimum_education is not None:
            edu["minimum_level"] = body.minimum_education.strip()
        if body.fields_of_study is not None:
            edu["fields_of_study"] = [f.strip() for f in body.fields_of_study if f.strip()]
        current["education"] = edu

    if body.certifications is not None:
        current["certifications"] = [c.strip() for c in body.certifications if c.strip()]
    if body.domain_knowledge is not None:
        current["domain_knowledge"] = [d.strip() for d in body.domain_knowledge if d.strip()]
    if body.other_requirements is not None:
        current["other_requirements"] = [o.strip() for o in body.other_requirements if o.strip()]

    # Rebuild flat scoring arrays (preserving existing weights)
    from services.ai_service import flatten_criteria_for_scoring
    flat = flatten_criteria_for_scoring(current)

    await db.execute(
        text("""
            UPDATE job_criteria SET
                analysis_json     = CAST(:aj AS jsonb),
                skills            = :skills,
                experience        = :experience,
                education         = :education,
                certifications    = :certifications,
                soft_skills       = :soft_skills,
                domain_knowledge  = :domain_knowledge,
                other_requirements= :other_requirements
            WHERE job_id = :jid
        """),
        {
            "aj":                json.dumps(current, ensure_ascii=False),
            "skills":            flat["skills"],
            "experience":        flat["experience"],
            "education":         flat["education"],
            "certifications":    flat["certifications"],
            "soft_skills":       flat.get("soft_skills", []),
            "domain_knowledge":  flat["domain_knowledge"],
            "other_requirements":flat["other_requirements"],
            "jid":               job_id,
        },
    )
    await db.commit()
    return {"success": True, "message": "Criteria content updated"}


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


@router.put("/{job_id}")
async def update_job_metadata(
    job_id: str,
    body: UpdateJobMetadataRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update job metadata and/or status. Admin and HR Manager only."""
    role = (current_user.role or "").lower()
    if role not in ("admin", "hr_manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant admins and HR managers can edit job metadata",
        )

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    ALLOWED_STATUS = {"active", "inactive", "closed"}
    updates: dict = {}

    if body.title is not None:
        updates["title"] = body.title.strip()
    if body.department is not None:
        updates["department"] = body.department.strip() or None
    if body.location is not None:
        updates["location"] = body.location.strip() or None
    if body.job_type is not None:
        updates["job_type"] = body.job_type.strip() or None
    if body.duration is not None:
        updates["duration"] = body.duration.strip() or None
    if body.experience_level is not None:
        updates["experience_level"] = body.experience_level.strip() or None
    if body.work_mode is not None:
        updates["work_mode"] = body.work_mode.strip() or None
    if "application_deadline" in body.model_fields_set:
        raw_dl = body.application_deadline
        if raw_dl:
            try:
                updates["application_deadline"] = date.fromisoformat(raw_dl)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid date format for application_deadline: '{raw_dl}'. Expected YYYY-MM-DD.",
                )
        else:
            updates["application_deadline"] = None
    if body.vacancies_count is not None:
        updates["vacancies_count"] = max(1, body.vacancies_count)
    if body.max_applications is not None:
        # 0 means "clear the limit" → store as NULL
        updates["max_applications"] = body.max_applications if body.max_applications > 0 else None
    if body.auto_close_when_limit_reached is not None:
        updates["auto_close_when_limit_reached"] = body.auto_close_when_limit_reached
    if body.status is not None:
        s = body.status.lower()
        if s not in ALLOWED_STATUS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Status must be one of: {', '.join(ALLOWED_STATUS)}",
            )
        updates["status"] = s

    if not updates:
        return {"success": True, "message": "No changes"}

    updates["updated_by"] = current_user.user_id
    set_sql = ", ".join(f"{k} = :{k}" for k in updates)
    updates["jid"] = job_id
    try:
        await db.execute(
            text(f"UPDATE jobs SET {set_sql} WHERE job_id = :jid"),
            updates,
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update job: {exc}",
        ) from exc
    return {"success": True, "message": "Job updated"}
