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
from auth.module_guards import RequireAIRecruitment
from config import get_settings
from database import get_db, set_rls_context
from services.job_description_quality import evaluate_description_quality, validate_job_title
from services.subscription_service import can_create_campaign

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[RequireAIRecruitment])
settings = get_settings()


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateJobRequest(BaseModel):
    title: str
    department: str | None = None
    location: str | None = None
    job_type: str | None = None
    work_mode: str | None = None
    experience_level: str | None = None
    duration: str | None = None
    application_deadline: str | None = None  # ISO date string YYYY-MM-DD
    description: str
    qualified_threshold: int | None = None
    partial_threshold: int | None = None
    client_organization_id: str | None = None  # NULL = General job for agency tenants
    campaign_id: str | None = None  # NULL = standalone job (optional campaign grouping)
    vacancies_count: int | None = None
    knockout_questions: list[dict] | None = None


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
    allow_advanced_workflow_move:                     bool | None = None


class UpdateCriteriaContentRequest(BaseModel):
    required_skills:      list[str] | None = None
    preferred_skills:     list[str] | None = None
    required_soft_skills: list[str] | None = None
    preferred_soft_skills: list[str] | None = None
    minimum_years:        int | None = None
    relevant_roles:       list[str] | None = None
    minimum_education:    str | None = None
    fields_of_study:      list[str] | None = None
    certifications:       list[str] | None = None
    domain_knowledge:     list[str] | None = None
    other_requirements:   list[str] | None = None


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
    description: str | None = None  # full job description text
    knockout_questions: list[dict] | None = None  # None = don't touch; [] = clear all
    campaign_id: str | None = None  # set to link to a campaign; empty/null = detach (standalone)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_tenant_info(tenant_id: str, db) -> dict:
    """Return tenant_type and any other fields needed for business logic."""
    row = await db.execute(
        text("SELECT tenant_type FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": tenant_id},
    )
    r = row.mappings().first()
    return dict(r) if r else {"tenant_type": "organization"}


async def _get_accessible_client_org_ids(
    user_id: str,
    tenant_id: str,
    role: str,
    tenant_type: str,
    db,
) -> list[str] | None:
    """
    Returns:
    - None  → no client org filter should be applied (org tenant, or admin of agency tenant)
    - []    → user has no assigned clients; they should see nothing
    - [ids] → explicit list of client_organization_ids the user may access
    """
    if tenant_type == "organization":
        return None  # standard org tenant: no agency filtering
    if role in ("admin", "super_admin"):
        return None  # agency admin sees all clients
    rows = await db.execute(
        text("""
            SELECT client_organization_id::text
            FROM agency_user_clients
            WHERE user_id = CAST(:uid AS uuid) AND tenant_id = CAST(:tid AS uuid)
        """),
        {"uid": user_id, "tid": tenant_id},
    )
    return [r[0] for r in rows]


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
async def list_jobs(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    client_organization_id: str | None = None,
    campaign_id: str | None = None,
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"

    params: dict = {"tid": current_user.tenant_id}
    extra_filters: list[str] = []

    if is_super_admin:
        base_where = ""
    else:
        base_where = "WHERE j.tenant_id = :tid"

        # Agency user scoping: non-admin users on agency/recruiter tenants see only
        # jobs for their assigned client organisations.
        tenant_info = await _get_tenant_info(current_user.tenant_id, db)
        client_ids = await _get_accessible_client_org_ids(
            current_user.user_id,
            current_user.tenant_id,
            current_user.role,
            tenant_info["tenant_type"],
            db,
        )
        if client_ids is not None:
            if len(client_ids) == 0:
                # No assigned clients — only General jobs (NULL client) are visible
                extra_filters.append("j.client_organization_id IS NULL")
            else:
                placeholders = ", ".join(f"CAST(:cid{i} AS uuid)" for i in range(len(client_ids)))
                extra_filters.append(
                    f"(j.client_organization_id IS NULL OR j.client_organization_id IN ({placeholders}))"
                )
                for i, cid in enumerate(client_ids):
                    params[f"cid{i}"] = cid

    # Optional explicit client_org filter (for admin/super-admin filtering by client)
    if client_organization_id:
        extra_filters.append("j.client_organization_id = CAST(:filter_cid AS uuid)")
        params["filter_cid"] = client_organization_id

    # Optional campaign filter (group jobs by campaign)
    if campaign_id:
        extra_filters.append("j.campaign_id = CAST(:filter_campaign AS uuid)")
        params["filter_campaign"] = campaign_id

    if extra_filters:
        joined = " AND ".join(extra_filters)
        # base_where already starts with WHERE for non-super-admins; otherwise this
        # is the first predicate and must introduce its own WHERE.
        extra_sql = (" AND " + joined) if base_where else (" WHERE " + joined)
    else:
        extra_sql = ""

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
                j.client_organization_id,
                co.organization_name AS client_org_name,
                j.campaign_id,
                camp.name AS campaign_name,
                t.name AS tenant_name,
                jc.criteria_extraction_status,
                COUNT(a.application_id)                                                                          AS applications_total,
                -- AI Scored: primary classification by processing_status
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'ai_scored')                           AS applications_scored,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'ai_scored'
                    AND a.decision = 'qualified')                                                                 AS applications_qualified,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'ai_scored'
                    AND a.decision = 'partial')                                                                   AS applications_partial,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'ai_scored'
                    AND a.decision = 'rejected')                                                                  AS applications_rejected,
                -- Stopped Before AI: processing_status = 'failed'
                -- stopped_reason is authoritative; fall back to security_check_status for historical rows
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'failed'
                    AND (
                        a.stopped_reason = 'security_blocked'
                        OR (a.stopped_reason IS NULL AND a.security_check_status = 'blocked')
                    ))                                                                                            AS applications_security_blocked,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'failed'
                    AND a.stopped_reason = 'duplicate_blocked')                                                  AS applications_duplicate_blocked,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'failed'
                    AND a.stopped_reason IS DISTINCT FROM 'security_blocked'
                    AND a.stopped_reason IS DISTINCT FROM 'duplicate_blocked'
                    AND NOT (a.stopped_reason IS NULL AND a.security_check_status = 'blocked'))                  AS applications_failed_needs_review,
                -- Possible duplicate: flag only, not a blocked/stopped category
                COUNT(a.application_id) FILTER (WHERE a.duplicate_status = 'possible_duplicate')                 AS applications_possible_duplicate,
                -- In-progress
                COUNT(a.application_id) FILTER (
                    WHERE a.processing_status IN ('pending', 'queued', 'processing')
                ) AS applications_in_progress
            FROM jobs j
            JOIN tenants t ON t.tenant_id = j.tenant_id
            LEFT JOIN client_organizations co ON co.client_organization_id = j.client_organization_id
            LEFT JOIN job_campaigns camp ON camp.campaign_id = j.campaign_id
            LEFT JOIN job_criteria jc ON jc.job_id = j.job_id
            LEFT JOIN applications a ON a.job_id = j.job_id
            {base_where}
            {extra_sql}
            GROUP BY j.job_id, t.name, co.organization_name, camp.name, jc.criteria_extraction_status
            ORDER BY j.created_at DESC
        """),
        params,
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
            "client_organization_id": str(r["client_organization_id"]) if r["client_organization_id"] else None,
            "client_org_name":    r["client_org_name"],
            "campaign_id":        str(r["campaign_id"]) if r["campaign_id"] else None,
            "campaign_name":      r["campaign_name"],
            "receive_cv_via_forwarding_email": r["receive_cv_via_forwarding_email"],
            "receive_cv_via_platform_email":   r["receive_cv_via_platform_email"],
            "criteria_extraction_status":      r["criteria_extraction_status"] or "pending",
            "posted_date":        r["created_at"].date().isoformat() if r["created_at"] else None,
            "applications_total":               r["applications_total"],
            "applications_qualified":           r["applications_qualified"],
            "applications_partial":             r["applications_partial"],
            "applications_rejected":            r["applications_rejected"],
            "applications_in_progress":         int(r["applications_in_progress"]),
            "applications_scored":              int(r["applications_scored"]),
            "applications_security_blocked":    int(r["applications_security_blocked"]),
            "applications_duplicate_blocked":   int(r["applications_duplicate_blocked"]),
            "applications_possible_duplicate":  int(r["applications_possible_duplicate"]),
            "applications_failed_needs_review": int(r["applications_failed_needs_review"]),
        })
    return jobs


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(
    body: CreateJobRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Title validation gate
    title_valid, title_error = validate_job_title(body.title)
    if not title_valid:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=title_error)

    # Quality-gate: reject clearly meaningless descriptions before hitting the DB
    desc_quality = evaluate_description_quality(body.description)
    if desc_quality.state == "insufficient":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=desc_quality.rejection_message(),
        )

    # Verify tenant exists and get tenant_type
    t_row = await db.execute(
        text("SELECT tenant_id, tenant_type FROM tenants WHERE tenant_id = :tid"),
        {"tid": current_user.tenant_id},
    )
    tenant = t_row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    # Agency / individual_recruiter tenants: client_organization_id is optional (NULL = General job)
    tenant_type = tenant["tenant_type"] or "organization"
    if tenant_type in ("agency", "individual_recruiter") and body.client_organization_id:
        # Verify the provided client org belongs to this tenant
        corg_row = await db.execute(
            text("""
                SELECT client_organization_id FROM client_organizations
                WHERE client_organization_id = CAST(:cid AS uuid)
                  AND tenant_id = CAST(:tid AS uuid)
                  AND status = 'active'
            """),
            {"cid": body.client_organization_id, "tid": current_user.tenant_id},
        )
        if not corg_row.first():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client organisation not found or inactive.",
            )

    # Optional campaign grouping: validate the job aligns with the campaign client.
    if body.campaign_id:
        from services.campaign_service import validate_job_campaign_link
        await validate_job_campaign_link(
            db,
            tenant_id=current_user.tenant_id,
            campaign_id=body.campaign_id,
            job_client_organization_id=body.client_organization_id,
        )

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
                              job_type, work_mode, experience_level,
                              duration, application_deadline, description,
                              qualified_threshold, partial_threshold, job_code, status,
                              client_organization_id, campaign_id, vacancies_count)
            VALUES (:tid, :uid, :title, :dept, :location, :job_type, :work_mode, :experience_level,
                    :duration, CAST(NULLIF(:application_deadline, '') AS DATE), :desc,
                    :qt, :pt, :job_code, 'active',
                    CAST(:coid AS uuid), CAST(:campaign_id AS uuid), :vacancies_count)
            RETURNING job_id
        """),
        {
            "tid":                current_user.tenant_id,
            "uid":                current_user.user_id,
            "title":              body.title,
            "dept":               body.department,
            "location":           body.location,
            "job_type":           body.job_type,
            "work_mode":          body.work_mode,
            "experience_level":   body.experience_level,
            "duration":           body.duration,
            "application_deadline": body.application_deadline,
            "desc":               body.description,
            "qt":                 body.qualified_threshold,
            "pt":                 body.partial_threshold,
            "job_code":           job_code,
            "coid":               body.client_organization_id,
            "campaign_id":        body.campaign_id,
            "vacancies_count":    max(1, body.vacancies_count) if body.vacancies_count else 1,
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

    if body.knockout_questions:
        from services.knockout_questions_service import save_job_knockout_questions
        await save_job_knockout_questions(
            db, job_id, current_user.tenant_id, body.knockout_questions,
            job_description=body.description,
        )
        await db.commit()

    # Queue async AI extraction — does not block job creation
    from workers.criteria_worker import extract_criteria_task
    _job_meta = {
        "title":            body.title,
        "department":       body.department,
        "experience_level": body.experience_level,
        "location":         body.location,
        "job_type":         body.job_type,
        "work_mode":        body.work_mode,
    }
    extract_criteria_task.delay(job_id, body.description, _job_meta)

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

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")
    tenant_filter = "" if is_super_admin else "AND j.tenant_id = :tid"

    # For super_admin, allow all tenants. For regular users, check client_organization_id access
    client_org_filter = "" if is_super_admin else """AND auc.tenant_id = CAST(:tid AS uuid)"""

    params: dict = {
        "jid": job_id,
        "uid": current_user.user_id,
        "is_admin": is_admin,
    }
    if not is_super_admin:
        params["tid"] = current_user.tenant_id

    job_row = await db.execute(
        text(f"""
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
                j.client_organization_id,
                co.organization_name AS client_org_name,
                j.campaign_id,
                camp.name AS campaign_name,
                cu.full_name AS created_by_name,
                uu.full_name AS updated_by_name,
                COUNT(a.application_id)                                                                          AS applications_total,
                -- AI Scored: primary classification by processing_status
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'ai_scored')                           AS applications_scored,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'ai_scored'
                    AND a.decision = 'qualified')                                                                 AS applications_qualified,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'ai_scored'
                    AND a.decision = 'partial')                                                                   AS applications_partial,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'ai_scored'
                    AND a.decision = 'rejected')                                                                  AS applications_rejected,
                -- Stopped Before AI: processing_status = 'failed'
                -- stopped_reason is authoritative; fall back to security_check_status for historical rows
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'failed'
                    AND (
                        a.stopped_reason = 'security_blocked'
                        OR (a.stopped_reason IS NULL AND a.security_check_status = 'blocked')
                    ))                                                                                            AS applications_security_blocked,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'failed'
                    AND a.stopped_reason = 'duplicate_blocked')                                                  AS applications_duplicate_blocked,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'failed'
                    AND a.stopped_reason IS DISTINCT FROM 'security_blocked'
                    AND a.stopped_reason IS DISTINCT FROM 'duplicate_blocked'
                    AND NOT (a.stopped_reason IS NULL AND a.security_check_status = 'blocked'))                  AS applications_failed_needs_review,
                -- Possible duplicate: flag only, not a blocked/stopped category
                COUNT(a.application_id) FILTER (WHERE a.duplicate_status = 'possible_duplicate')                 AS applications_possible_duplicate,
                -- Legacy valid-count kept for controls logic
                COUNT(a.application_id) FILTER (
                    WHERE (a.duplicate_status IS NULL OR a.duplicate_status = 'not_duplicate')
                      AND a.processing_status != 'failed'
                ) AS applications_valid_count,
                -- In-progress
                COUNT(a.application_id) FILTER (
                    WHERE a.processing_status IN ('pending', 'queued', 'processing')
                ) AS applications_in_progress,
                -- Workflow status counts
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'awaiting_review') AS applications_awaiting_review,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'under_review')   AS applications_under_review,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'shortlisted')    AS applications_shortlisted,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'interviewing')   AS applications_interviewing,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'offer_made')     AS applications_offer_made,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'hired')          AS applications_hired,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'rejected')       AS applications_rejected_workflow,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'withdrawn')      AS applications_withdrawn,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'on_hold')        AS applications_on_hold,
                t.forwarding_email AS tenant_forwarding_email,
                t.job_application_controls_enabled,
                j.allow_advanced_workflow_move
            FROM jobs j
            LEFT JOIN applications a ON a.job_id = j.job_id
            JOIN tenants t ON t.tenant_id = j.tenant_id
            LEFT JOIN client_organizations co ON co.client_organization_id = j.client_organization_id
            LEFT JOIN job_campaigns camp ON camp.campaign_id = j.campaign_id
            LEFT JOIN users cu ON cu.user_id = j.created_by
            LEFT JOIN users uu ON uu.user_id = j.updated_by
            WHERE j.job_id = :jid {tenant_filter}
              AND (
                :is_admin = TRUE
                OR j.client_organization_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM agency_user_clients auc
                    WHERE auc.user_id = CAST(:uid AS uuid)
                      AND auc.client_organization_id = j.client_organization_id
                      {client_org_filter}
                )
              )
            GROUP BY j.job_id, t.tenant_id, co.organization_name, camp.name, cu.full_name, uu.full_name
        """),
        params,
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
                   criteria_extraction_retry_count,
                   criteria_last_failed_description_hash,
                   criteria_last_failed_at,
                   weight_skills, weight_experience, weight_education,
                   weight_certifications, weight_soft_skills,
                   weight_domain_knowledge, weight_other,
                   ai_model, ai_generated_at, last_edited_at, last_edited_by
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

    # Retry control fields
    from workers.criteria_worker import _normalize_description_hash
    retry_count      = int(criteria["criteria_extraction_retry_count"] or 0) if criteria else 0
    last_failed_hash = (criteria["criteria_last_failed_description_hash"] if criteria else None)
    cfg_row = await db.execute(
        text("SELECT value FROM system_config WHERE key = 'criteria_extraction_max_retries'"), {},
    )
    max_retries = int(cfg_row.scalar_one_or_none() or 3)

    current_description_hash = _normalize_description_hash(job["description"] or "")
    if extraction_status == "blocked" or retry_count >= max_retries:
        retry_blocked_reason: str | None = "max_retries_reached"
    elif last_failed_hash and current_description_hash == last_failed_hash:
        retry_blocked_reason = "description_unchanged"
    else:
        retry_blocked_reason = None
    criteria_retry_allowed = retry_blocked_reason is None and extraction_status in ("insufficient", "failed")

    via_forwarding = job["receive_cv_via_forwarding_email"]
    via_platform   = job["receive_cv_via_platform_email"]

    from services.knockout_questions_service import get_job_knockout_questions
    knockout_questions = await get_job_knockout_questions(db, job_id)

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
            "applications_total":               job["applications_total"],
            "applications_qualified":           job["applications_qualified"],
            "applications_partial":             job["applications_partial"],
            "applications_rejected":            job["applications_rejected"],
            "applications_valid_count":         job["applications_valid_count"],
            "applications_in_progress":         int(job["applications_in_progress"]),
            "applications_scored":              int(job["applications_scored"]),
            "applications_security_blocked":    int(job["applications_security_blocked"]),
            "applications_duplicate_blocked":   int(job["applications_duplicate_blocked"]),
            "applications_possible_duplicate":  int(job["applications_possible_duplicate"]),
            "applications_failed_needs_review": int(job["applications_failed_needs_review"]),
            "applications_awaiting_review":     int(job["applications_awaiting_review"]),
            "applications_under_review":        int(job["applications_under_review"]),
            "applications_shortlisted":         int(job["applications_shortlisted"]),
            "applications_interviewing":        int(job["applications_interviewing"]),
            "applications_offer_made":          int(job["applications_offer_made"]),
            "applications_hired":               int(job["applications_hired"]),
            "applications_rejected_workflow":   int(job["applications_rejected_workflow"]),
            "applications_withdrawn":           int(job["applications_withdrawn"]),
            "applications_on_hold":             int(job["applications_on_hold"]),
            "client_organization_id": str(job["client_organization_id"]) if job["client_organization_id"] else None,
            "client_org_name":        job["client_org_name"],
            "campaign_id":            str(job["campaign_id"]) if job["campaign_id"] else None,
            "campaign_name":          job["campaign_name"],
            "job_application_controls_enabled": bool(job["job_application_controls_enabled"]),
            "allow_advanced_workflow_move": bool(job["allow_advanced_workflow_move"]),
            "qualified_threshold":      job["qualified_threshold"],
            "partial_threshold":        job["partial_threshold"],
            "max_applications":               job["max_applications"],
            "auto_close_when_limit_reached":  job["auto_close_when_limit_reached"],
            "criteria_extraction_status":     extraction_status,
            "criteria_extraction_error":      extraction_error,
            "criteria_extraction_retry_count": retry_count,
            "criteria_extraction_max_retries": max_retries,
            "criteria_retry_allowed":          criteria_retry_allowed,
            "criteria_retry_blocked_reason":   retry_blocked_reason,
            "criteria_last_edited_by":         str(criteria["last_edited_by"]) if criteria and criteria["last_edited_by"] else None,
            "knockout_questions":              knockout_questions,
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
    """Update per-job CV ingestion channel settings.

    Permission rules:
    - admin / super_admin / hr_manager: always allowed.
    - recruiter: never allowed.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    if not job_row.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    # Permission check
    role = (current_user.role or "").lower()
    if role not in ("admin", "super_admin", "hr_manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify intake settings for this job")

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

    # allow_advanced_workflow_move is admin/super_admin-only
    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")
    if is_admin and body.allow_advanced_workflow_move is not None:
        updates["allow_advanced_workflow_move"] = body.allow_advanced_workflow_move

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
            JOIN jobs j ON j.job_id = dl.job_id
            LEFT JOIN applications a
                ON a.application_id = dl.original_application_id
            LEFT JOIN application_files af
                ON af.application_id = dl.original_application_id
            WHERE dl.job_id = :job_id AND dl.tenant_id = :tenant_id
              AND (
                :is_admin = TRUE
                OR j.client_organization_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM agency_user_clients auc
                    WHERE auc.user_id = CAST(:uid AS uuid)
                      AND auc.client_organization_id = j.client_organization_id
                      AND auc.tenant_id = CAST(:tenant_id AS uuid)
                )
              )
            ORDER BY dl.received_at DESC
            LIMIT 200
        """),
        {
            "job_id":    job_id,
            "tenant_id": tenant_id,
            "uid":       current_user.user_id,
            "is_admin":  (role or "").lower() in ("admin", "super_admin"),
        },
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
            SELECT dl.duplicate_file_path, dl.duplicate_original_filename, dl.duplicate_content_type
            FROM duplicate_application_logs dl
            JOIN jobs j ON j.job_id = dl.job_id
            WHERE dl.log_id = :log_id
              AND dl.job_id = :job_id
              AND dl.tenant_id = :tenant_id
              AND (
                :is_admin = TRUE
                OR j.client_organization_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM agency_user_clients auc
                    WHERE auc.user_id = CAST(:uid AS uuid)
                      AND auc.client_organization_id = j.client_organization_id
                      AND auc.tenant_id = CAST(:tenant_id AS uuid)
                )
              )
        """),
        {
            "log_id":    log_id,
            "job_id":    job_id,
            "tenant_id": current_user.tenant_id,
            "uid":       current_user.user_id,
            "is_admin":  (current_user.role or "").lower() in ("admin", "super_admin"),
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

    if body.required_soft_skills is not None or body.preferred_soft_skills is not None:
        soft_skills = dict(current.get("soft_skills", {}))
        if body.required_soft_skills is not None:
            soft_skills["required"] = [s.strip() for s in body.required_soft_skills if s.strip()]
        if body.preferred_soft_skills is not None:
            soft_skills["preferred"] = [s.strip() for s in body.preferred_soft_skills if s.strip()]
        current["soft_skills"] = soft_skills

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
                other_requirements= :other_requirements,
                last_edited_by    = :uid,
                last_edited_at    = now()
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
            "uid":               str(current_user.user_id),
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
    """Re-queue AI criteria extraction subject to retry limits and description-change check."""
    from workers.criteria_worker import _normalize_description_hash, extract_criteria_task

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT j.description, j.title, j.department, j.experience_level,
                   j.location, j.job_type, j.work_mode,
                   jc.criteria_extraction_status,
                   jc.criteria_extraction_retry_count,
                   jc.criteria_last_failed_description_hash
            FROM jobs j
            LEFT JOIN job_criteria jc ON jc.job_id = j.job_id
            WHERE j.job_id = :jid AND j.tenant_id = :tid
        """),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    job = row.mappings().first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    cfg_row = await db.execute(
        text("SELECT value FROM system_config WHERE key = 'criteria_extraction_max_retries'"),
        {},
    )
    max_retries = int(cfg_row.scalar_one_or_none() or 3)

    current_status = job["criteria_extraction_status"] or "pending"
    retry_count    = int(job["criteria_extraction_retry_count"] or 0)
    description    = job["description"] or ""

    # Terminal block — already exhausted
    if current_status == "blocked":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum analysis retries reached for this campaign.",
        )

    # Only retryable from these states
    if current_status not in ("insufficient", "failed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Criteria extraction cannot be retried in status '{current_status}'.",
        )

    # Enforce retry cap — transition to blocked
    if retry_count >= max_retries:
        await db.execute(
            text("UPDATE job_criteria SET criteria_extraction_status = 'blocked' WHERE job_id = :jid"),
            {"jid": job_id},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum analysis retries reached for this campaign.",
        )

    # Quality gate: reject if description still insufficient for AI analysis
    desc_quality = evaluate_description_quality(description)
    if not desc_quality.is_sufficient_for_ai:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=desc_quality.rejection_message(),
        )

    # For insufficient: require a meaningful description change (hash check)
    if current_status == "insufficient":
        last_hash = job["criteria_last_failed_description_hash"]
        if last_hash and _normalize_description_hash(description) == last_hash:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please update the job description with more meaningful details before retrying analysis.",
            )

    # Allowed — increment counter and re-queue
    await db.execute(
        text("""
            UPDATE job_criteria
            SET criteria_extraction_status        = 'pending',
                criteria_extraction_retry_count   = criteria_extraction_retry_count + 1,
                criteria_extraction_error         = NULL
            WHERE job_id = :jid
        """),
        {"jid": job_id},
    )
    await db.commit()

    _retry_meta = {
        "title":            job.get("title"),
        "department":       job.get("department"),
        "experience_level": job.get("experience_level"),
        "location":         job.get("location"),
        "job_type":         job.get("job_type"),
        "work_mode":        job.get("work_mode"),
    }
    extract_criteria_task.delay(job_id, description, _retry_meta)
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
        text("SELECT job_id, client_organization_id FROM jobs WHERE job_id = :jid AND tenant_id = :tid"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    existing_job = job_row.mappings().first()
    if not existing_job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    job_client_org_id = (
        str(existing_job["client_organization_id"])
        if existing_job["client_organization_id"] else None
    )

    # If application controls are being updated, verify the feature is enabled for this tenant
    controls_fields = {"max_applications", "auto_close_when_limit_reached"}
    requested_controls = controls_fields & body.model_fields_set
    if requested_controls:
        flag_row = await db.execute(
            text("SELECT job_application_controls_enabled FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
            {"tid": current_user.tenant_id},
        )
        flag = flag_row.scalar_one_or_none()
        if not flag:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Job application controls are not enabled for this tenant.",
            )

    ALLOWED_STATUS = {"active", "inactive", "closed"}
    updates: dict = {}

    if body.title is not None:
        title_valid, title_error = validate_job_title(body.title)
        if not title_valid:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=title_error)
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
    if body.description is not None:
        updates["description"] = body.description.strip()
    if body.status is not None:
        s = body.status.lower()
        if s not in ALLOWED_STATUS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Status must be one of: {', '.join(ALLOWED_STATUS)}",
            )
        updates["status"] = s

    # Campaign link (optional grouping). Explicit presence in the request body is
    # required to change it: a value links/moves the job; empty/null detaches it.
    if "campaign_id" in body.model_fields_set:
        new_campaign = (body.campaign_id or "").strip() or None
        if new_campaign is not None:
            from services.campaign_service import validate_job_campaign_link
            await validate_job_campaign_link(
                db,
                tenant_id=current_user.tenant_id,
                campaign_id=new_campaign,
                job_client_organization_id=job_client_org_id,
            )
        updates["campaign_id"] = new_campaign

    # Handle knockout_questions separately (None = leave alone, [] = clear all)
    if body.knockout_questions is not None:
        from services.knockout_questions_service import save_job_knockout_questions
        await save_job_knockout_questions(
            db, job_id, current_user.tenant_id, body.knockout_questions,
            job_description=body.description,
        )

    if not updates and body.knockout_questions is None:
        return {"success": True, "message": "No changes"}

    if updates:
        updates["updated_by"] = current_user.user_id
        set_parts = [
            "campaign_id = CAST(:campaign_id AS uuid)" if k == "campaign_id" else f"{k} = :{k}"
            for k in updates
        ]
        set_sql = ", ".join(set_parts) + ", updated_at = NOW()"
        updates["jid"] = job_id
        try:
            await db.execute(
                text(f"UPDATE jobs SET {set_sql} WHERE job_id = :jid"),
                updates,
            )
        except Exception as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update job: {exc}",
            ) from exc

    await db.commit()
    return {"success": True, "message": "Job updated"}
