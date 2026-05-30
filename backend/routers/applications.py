import logging
import uuid
from pathlib import Path
from typing import Annotated

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, get_current_user
from config import get_settings
from database import get_db, set_rls_context
from services.application_intake_service import (
    IntakeValidationError,
    process_cv_intake,
)
from workers.cv_score import score_cv_task

router = APIRouter(prefix="/applications", tags=["applications"])
settings = get_settings()


WORKFLOW_STATUSES = frozenset((
    "awaiting_review", "under_review", "shortlisted", "interviewing",
    "offer_made", "hired", "rejected", "withdrawn", "on_hold",
))

VALID_WORKFLOW_TRANSITIONS: dict[str, frozenset] = {
    "awaiting_review": frozenset({"under_review", "on_hold", "rejected", "withdrawn"}),
    "under_review":    frozenset({"shortlisted", "on_hold", "rejected", "withdrawn"}),
    "shortlisted":     frozenset({"interviewing", "under_review", "on_hold", "rejected", "withdrawn"}),
    "interviewing":    frozenset({"offer_made", "shortlisted", "on_hold", "rejected", "withdrawn"}),
    "offer_made":      frozenset({"hired", "interviewing", "on_hold", "rejected", "withdrawn"}),
    "hired":           frozenset(),
    "rejected":        frozenset({"awaiting_review", "under_review"}),
    "withdrawn":       frozenset({"awaiting_review", "under_review"}),
    "on_hold":         frozenset({"awaiting_review", "under_review", "shortlisted", "interviewing", "offer_made", "rejected", "withdrawn"}),
}


class ScorePendingRequest(BaseModel):
    job_id: str


class ResetStuckRequest(BaseModel):
    job_id: str


class WorkflowStatusRequest(BaseModel):
    workflow_status: str
    note: str | None = None
    advanced_move: bool = False


class RecruiterNotesRequest(BaseModel):
    recruiter_notes: str | None


@router.get("")
async def list_applications(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    job_id: str | None = None,
    workflow_status: str | None = None,
    processing_status: str | None = None,
    ai_decision: str | None = None,
    possible_duplicate: bool | None = None,
    has_notes: bool | None = None,
    applied_after: str | None = None,
    applied_before: str | None = None,
    search: str | None = None,
    campaign_id: str | None = None,
    client_organization_id: str | None = None,
    sort_by: str = "applied_at",
    sort_order: str = "desc",
    page: int = 1,
    limit: int = 50,
):
    """
    List applications with flexible filtering and pagination.

    Backward compatible mode:
    - If job_id provided: returns applications for that job (existing behavior)
    - If job_id not provided: returns all tenant applications (new global search)

    Supports multi-dimensional filtering:
    - workflow_status: awaiting_review, under_review, interviewing, etc.
    - processing_status: ai_scored, pending, failed, security_blocked, etc.
    - ai_decision: qualified, partial, rejected_low_match, not_scored
    - possible_duplicate: true/false
    - has_notes: true/false
    - date range: applied_after, applied_before (ISO format)
    - search: candidate name substring match
    - campaign_id: filter by campaign (tenant-wide mode only)
    - client_organization_id: agency/freelancer client filter

    Supports pagination and sorting.

    Access control:
    - Respects RLS tenant isolation
    - Respects agency/freelancer client scoping
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")

    # Validate pagination
    page = max(1, page)
    limit = max(1, min(limit, 500))  # Cap at 500 per page for safety
    offset = (page - 1) * limit

    # Validate sorting
    valid_sort_fields = {"applied_at", "updated_at", "score", "candidate_name"}
    sort_by = sort_by if sort_by in valid_sort_fields else "applied_at"
    sort_order = "ASC" if sort_order.lower() == "asc" else "DESC"

    # Build WHERE clause dynamically
    where_parts = [
        "a.tenant_id = CAST(:tid AS uuid)",
    ]
    params = {
        "tid": current_user.tenant_id,
        "uid": current_user.user_id,
        "is_admin": is_admin,
    }

    # Mode 1: Job-scoped (backward compatible) - job_id required
    if job_id:
        where_parts.append("a.job_id = CAST(:jid AS uuid)")
        params["jid"] = job_id

    # Workflow status filter
    if workflow_status:
        where_parts.append("a.workflow_status = :wf_status")
        params["wf_status"] = workflow_status

    # Processing status filter
    # Special alias 'failed_or_blocked' expands to all non-recoverable system states
    # so the frontend can send a single param without multi-value URL encoding.
    FAILED_OR_BLOCKED_STATUSES = (
        "'failed', 'security_blocked', 'duplicate_blocked', "
        "'extraction_failed', 'processing_failed', 'stopped'"
    )
    if processing_status:
        if processing_status == 'failed_or_blocked':
            where_parts.append(f"a.processing_status IN ({FAILED_OR_BLOCKED_STATUSES})")
        else:
            where_parts.append("a.processing_status = :proc_status")
            params["proc_status"] = processing_status

    # AI decision filter (maps to 'decision' column)
    if ai_decision:
        where_parts.append("a.decision = :ai_dec")
        params["ai_dec"] = ai_decision

    # Possible duplicate flag
    if possible_duplicate is not None:
        if possible_duplicate:
            where_parts.append("a.duplicate_status IN ('possible_duplicate', 'confirmed_duplicate')")
        else:
            where_parts.append("a.duplicate_status IS NULL OR a.duplicate_status = 'not_duplicate'")

    # Has notes filter
    if has_notes is not None:
        if has_notes:
            where_parts.append("a.recruiter_notes IS NOT NULL AND a.recruiter_notes != ''")
        else:
            where_parts.append("(a.recruiter_notes IS NULL OR a.recruiter_notes = '')")

    # Date range filters
    if applied_after:
        where_parts.append("a.applied_at >= CAST(:applied_after AS timestamp)")
        params["applied_after"] = applied_after

    if applied_before:
        where_parts.append("a.applied_at < CAST(:applied_before AS timestamp) + INTERVAL '1 day'")
        params["applied_before"] = applied_before

    # Candidate name search (LIKE, case-insensitive)
    if search:
        where_parts.append("a.candidate_name ILIKE :search")
        params["search"] = f"%{search}%"

    # Campaign filter (tenant-wide mode only; ignored if job_id provided)
    if campaign_id and not job_id:
        where_parts.append("j.campaign_id = CAST(:campaign_id AS uuid)")
        params["campaign_id"] = campaign_id

    # Client organization filter (agency/freelancer scoping)
    if client_organization_id and not job_id:
        where_parts.append("j.client_organization_id = CAST(:client_org_id AS uuid)")
        params["client_org_id"] = client_organization_id

    # Always enforce access control: admin OR no client OR assigned via agency_user_clients
    where_parts.append("""
        (
            :is_admin = TRUE
            OR j.client_organization_id IS NULL
            OR EXISTS (
                SELECT 1 FROM agency_user_clients auc
                WHERE auc.user_id = CAST(:uid AS uuid)
                  AND auc.client_organization_id = j.client_organization_id
                  AND auc.tenant_id = CAST(:tid AS uuid)
            )
        )
    """)

    where_clause = " AND ".join(where_parts)

    # Map sort_by to actual column
    sort_column = {
        "applied_at": "a.applied_at",
        "updated_at": "a.scored_at",
        "score": "s.final_score",
        "candidate_name": "a.candidate_name",
    }.get(sort_by, "a.applied_at")

    # Count total for pagination
    count_query = f"""
        SELECT COUNT(*) as total
        FROM applications a
        JOIN jobs j ON j.job_id = a.job_id
        WHERE {where_clause}
    """
    count_result = await db.execute(text(count_query), params)
    total = count_result.scalar() or 0

    # Fetch applications with job and campaign metadata
    data_query = f"""
        SELECT
            a.application_id,
            a.candidate_name,
            a.job_id,
            j.title                             AS job_title,
            j.job_code,
            j.campaign_id,
            jc.name                             AS campaign_name,
            j.client_organization_id,
            co.organization_name                AS client_org_name,
            a.decision                          AS status,
            a.processing_status,
            a.stopped_reason,
            a.duplicate_status,
            a.duplicate_reason,
            a.duplicate_reference_application_id,
            a.evaluation_exit_reason,
            a.workflow_status,
            a.recruiter_notes,
            a.applied_at,
            a.scored_at                         AS updated_at,
            s.final_score                       AS score,
            s.evaluation_notes                  AS summary
        FROM applications a
        JOIN jobs j ON j.job_id = a.job_id
        LEFT JOIN application_scores s ON s.application_id = a.application_id
        LEFT JOIN job_campaigns jc ON jc.campaign_id = j.campaign_id
        LEFT JOIN client_organizations co ON co.client_organization_id = j.client_organization_id
        WHERE {where_clause}
        ORDER BY {sort_column} {sort_order}
        LIMIT :limit OFFSET :offset
    """

    params["limit"] = limit
    params["offset"] = offset

    rows = await db.execute(text(data_query), params)

    apps = []
    for r in rows.mappings():
        apps.append({
            "application_id": str(r["application_id"]),
            "candidate_name": r["candidate_name"],
            "job_id": str(r["job_id"]),
            "job_title": r["job_title"],
            "job_code": r["job_code"],
            "campaign_id": str(r["campaign_id"]) if r["campaign_id"] else None,
            "campaign_name": r["campaign_name"],
            "client_organization_id": str(r["client_organization_id"]) if r["client_organization_id"] else None,
            "client_org_name": r["client_org_name"],
            "status": r["status"],
            "processing_status": r["processing_status"],
            "stopped_reason": r["stopped_reason"],
            "duplicate_status": r["duplicate_status"] or "not_duplicate",
            "duplicate_reason": r["duplicate_reason"],
            "duplicate_reference_application_id": str(r["duplicate_reference_application_id"]) if r["duplicate_reference_application_id"] else None,
            "evaluation_exit_reason": r["evaluation_exit_reason"],
            "workflow_status": r["workflow_status"] or "awaiting_review",
            "recruiter_notes": r["recruiter_notes"],
            "applied_at": r["applied_at"].isoformat() if r["applied_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "score": float(r["score"]) if r["score"] is not None else None,
            "summary": r["summary"],
        })

    # Backward compatibility mode: if job_id provided, return array (existing behavior)
    if job_id:
        return apps

    # New tenant-wide mode: return object with pagination metadata
    return {
        "candidates": apps,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "has_more": (offset + limit) < total,
        },
    }


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
                a.candidate_email_from_cv, a.candidate_phone_from_cv,
                a.email_sender_address,
                a.submitted_by_user_id, a.submitted_by_name, a.submitted_by_email,
                a.decision, a.submission_source, a.processing_status,
                a.stopped_reason,
                a.evaluation_stage, a.evaluation_exit_reason,
                a.gatekeeper_passed,
                a.applied_at, a.scored_at,
                a.qualified_threshold_used, a.partial_threshold_used,
                a.duplicate_status,
                a.duplicate_reference_application_id,
                a.duplicate_similarity_score,
                a.duplicate_reason,
                a.duplicate_checked_at,
                a.security_check_status,
                a.security_risk_level,
                a.security_risk_score,
                a.security_reason_codes,
                a.security_detected_patterns,
                a.security_detected_snippets,
                a.security_checked_at,
                a.workflow_status,
                a.recruiter_notes,
                j.title AS job_title, j.job_id,
                (SELECT af2.original_name FROM application_files af2
                 WHERE af2.application_id = a.application_id LIMIT 1) AS original_filename,
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
                s.cv_language, s.gatekeeper_passed AS score_gatekeeper_passed,
                s.ai_model,
                s.score_details,
                s.scoring_prompt_code, s.scoring_prompt_version,
                s.level2_prompt_code,  s.level2_prompt_version,
                s.scoring_provider
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            LEFT JOIN application_scores s ON s.application_id = a.application_id
            WHERE a.application_id = :aid AND a.tenant_id = :tid
              AND (
                :is_admin = TRUE
                OR j.client_organization_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM agency_user_clients auc
                    WHERE auc.user_id = CAST(:uid AS uuid)
                      AND auc.client_organization_id = j.client_organization_id
                      AND auc.tenant_id = CAST(:tid AS uuid)
                )
              )
        """),
        {
            "aid":      application_id,
            "tid":      current_user.tenant_id,
            "uid":      current_user.user_id,
            "is_admin": (current_user.role or "").lower() in ("admin", "super_admin"),
        },
    )
    app = row.mappings().first()
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    # Fetch duplicate reference candidate info if present
    dup_ref_info = None
    dup_ref_id = app["duplicate_reference_application_id"]
    if dup_ref_id:
        ref_row = await db.execute(
            text("""
                SELECT candidate_name, applied_at
                FROM applications
                WHERE application_id = :rid AND tenant_id = :tid
            """),
            {"rid": str(dup_ref_id), "tid": current_user.tenant_id},
        )
        ref = ref_row.mappings().first()
        if ref:
            dup_ref_info = {
                "application_id": str(dup_ref_id),
                "candidate_name": ref["candidate_name"],
                "applied_at":     ref["applied_at"].isoformat() if ref["applied_at"] else None,
            }

    # Fetch knockout question answers with question metadata
    ko_rows = await db.execute(
        text("""
            SELECT
                a.answer_id,
                a.question_id,
                a.answer_value,
                a.is_disqualifying,
                q.question_text,
                q.question_type,
                q.is_required,
                q.options,
                q.passing_criteria,
                q.display_order
            FROM application_knockout_answers a
            JOIN job_knockout_questions q ON q.question_id = a.question_id
            WHERE a.application_id = CAST(:aid AS uuid)
            ORDER BY q.display_order, q.created_at
        """),
        {"aid": application_id},
    )
    knockout_answers = [
        {
            "answer_id":        str(r["answer_id"]),
            "question_id":      str(r["question_id"]),
            "answer_value":     r["answer_value"],
            "is_disqualifying": r["is_disqualifying"],
            "question_text":    r["question_text"],
            "question_type":    r["question_type"],
            "is_required":      r["is_required"],
            "options":          r["options"],
            "passing_criteria": r["passing_criteria"],
            "display_order":    r["display_order"],
        }
        for r in ko_rows.mappings()
    ]

    # Fetch workflow history
    wf_rows = await db.execute(
        text("""
            SELECT history_id, from_status, to_status, note, changed_by_name,
                   created_at, is_advanced_move
            FROM application_workflow_history
            WHERE application_id = CAST(:aid AS uuid)
            ORDER BY created_at DESC
            LIMIT 50
        """),
        {"aid": application_id},
    )
    workflow_history = [
        {
            "history_id":      str(r["history_id"]),
            "from_status":     r["from_status"],
            "to_status":       r["to_status"],
            "note":            r["note"],
            "changed_by_name": r["changed_by_name"],
            "created_at":      r["created_at"].isoformat() if r["created_at"] else None,
            "is_advanced_move": bool(r["is_advanced_move"]),
        }
        for r in wf_rows.mappings()
    ]

    # Fetch AI comparison results if available
    comp_rows = await db.execute(
        text("""
            SELECT provider, model, final_score,
                   score_skills, score_experience, score_education,
                   score_certifications, score_soft_skills,
                   score_domain_knowledge, score_other,
                   score_details, evaluation_notes, strengths,
                   gaps_identified, scoring_prompt_code, scoring_prompt_version,
                   created_at
            FROM application_score_comparisons
            WHERE application_id = :aid
            ORDER BY created_at DESC
        """),
        {"aid": application_id},
    )
    comparisons = [dict(r) for r in comp_rows.mappings()]
    for c in comparisons:
        if c.get("created_at"):
            c["created_at"] = c["created_at"].isoformat()

    weights = app["weights_snapshot"] or {}

    def build_dim(score_key: str, weight_key: str) -> dict:
        score = app[score_key] or 0
        weight = weights.get(weight_key, 0)
        return {"achieved": score, "max": 100, "weight": weight}

    reasoning = app["reasoning"] or {}

    # Derive display decision label: evaluation_stage=1 gatekeeper-rejected rows
    # now have decision='rejected' but are displayed as Level 1 Low Match
    stage = app["evaluation_stage"]
    gk_passed = app["gatekeeper_passed"]
    display_decision = app["decision"]
    if stage == 1 and gk_passed is False and display_decision == "rejected":
        display_decision = "low_match"  # frontend display alias only

    return {
        "application_id": str(app["application_id"]),
        "candidate_name": app["candidate_name"],
        "candidate_email": app["candidate_email"],
        "candidate_email_from_cv": app["candidate_email_from_cv"],
        "candidate_phone_from_cv": app["candidate_phone_from_cv"],
        "email_sender_address": app["email_sender_address"],
        "submitted_by_user_id": str(app["submitted_by_user_id"]) if app["submitted_by_user_id"] else None,
        "submitted_by_name":  app["submitted_by_name"],
        "submitted_by_email": app["submitted_by_email"],
        "original_filename":  app["original_filename"],
        "decision": display_decision,
        "overall_score": int(app["final_score"]) if app["final_score"] is not None else 0,
        "submission_source": app["submission_source"],
        "processing_status": app["processing_status"],
        "stopped_reason":    app["stopped_reason"],
        "evaluation_stage": stage,
        "evaluation_exit_reason": app["evaluation_exit_reason"],
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
        "score_details": app["score_details"] or {},
        "analysis": {
            "summary":                       app["evaluation_notes"] or "",
            "strengths":                     app["strengths"] or [],
            "risks":                         app["gaps_identified"] or [],
            "gaps_identified":               app["gaps_identified"] or [],
            "evaluation_notes":              app["evaluation_notes"],
            "interview_suggested_questions": app["interview_questions"] or [],
            "interview_focus_points":        [],
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
        "scoring_provider":       app["scoring_provider"],
        "scoring_prompt_code":    app["scoring_prompt_code"],
        "scoring_prompt_version": app["scoring_prompt_version"],
        "level2_prompt_code":     app["level2_prompt_code"],
        "level2_prompt_version":  app["level2_prompt_version"],
        "raw_ai_response":        app["raw_ai_response"],
        "ai_comparisons":         comparisons,
        "duplicate_status":                   app["duplicate_status"] or "not_duplicate",
        "duplicate_reference_application_id": str(dup_ref_id) if dup_ref_id else None,
        "duplicate_similarity_score":         float(app["duplicate_similarity_score"]) if app["duplicate_similarity_score"] is not None else None,
        "duplicate_reason":                   app["duplicate_reason"],
        "duplicate_checked_at":               app["duplicate_checked_at"].isoformat() if app["duplicate_checked_at"] else None,
        "duplicate_reference":                dup_ref_info,
        "security_check_status":    app["security_check_status"],
        "security_risk_level":      app["security_risk_level"],
        "security_risk_score":      int(app["security_risk_score"]) if app["security_risk_score"] is not None else None,
        "security_reason_codes":      list(app["security_reason_codes"] or []),
        "security_detected_patterns": list(app["security_detected_patterns"] or []),
        "security_detected_snippets": list(app["security_detected_snippets"] or []),
        "security_checked_at":        app["security_checked_at"].isoformat() if app["security_checked_at"] else None,
        "knockout_answers":           knockout_answers,
        "workflow_status":            app["workflow_status"] or "new",
        "recruiter_notes":            app["recruiter_notes"],
        "workflow_history":           workflow_history,
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
    content = await file.read()

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    job_row = await db.execute(
        text("SELECT job_id, title FROM jobs WHERE job_id = :jid AND tenant_id = :tid AND status = 'active'"),
        {"jid": job_id, "tid": current_user.tenant_id},
    )
    if not job_row.mappings().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active job not found")

    try:
        result = await process_cv_intake(
            db,
            intake_method="manual_upload",
            job_id=job_id,
            tenant_id=current_user.tenant_id,
            candidate_name=candidate_name,
            candidate_email=candidate_email,
            content_type=file.content_type,
            content=content,
            original_filename=file.filename,
            submission_source="manual_upload",
            auto_score=False,
            files_base_path=settings.files_base_path,
            max_file_size_mb=settings.max_file_size_mb,
            submitted_by_user_id=current_user.user_id,
            submitted_by_name=current_user.full_name or current_user.email,
            submitted_by_email=current_user.email,
        )
    except IntakeValidationError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    if result.status == "INTAKE_BLOCKED":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="CV intake is disabled because the job analysis is not completed.",
        )
    if result.status == "DUPLICATE_APPLICATION":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This CV file has already been uploaded for this position.",
        )
    if result.status == "REJECTED":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.error_message or "CV quota exceeded for this plan.",
        )

    return {
        "application_id": result.application_id,
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
            JOIN jobs j ON j.job_id = a.job_id
            LEFT JOIN application_files af ON af.application_id = a.application_id
            LEFT JOIN application_scores s ON s.application_id = a.application_id
            WHERE a.job_id = :jid
              AND a.tenant_id = :tid
              AND a.submission_source = 'manual_upload'
              AND (
                :is_admin = TRUE
                OR j.client_organization_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM agency_user_clients auc
                    WHERE auc.user_id = CAST(:uid AS uuid)
                      AND auc.client_organization_id = j.client_organization_id
                      AND auc.tenant_id = CAST(:tid AS uuid)
                )
              )
            ORDER BY a.applied_at DESC
        """),
        {
            "jid":      job_id,
            "tid":      current_user.tenant_id,
            "uid":      current_user.user_id,
            "is_admin": (current_user.role or "").lower() in ("admin", "super_admin"),
        },
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

    from datetime import datetime, timezone

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
        # Update intake log with scoring_enqueued_at if a log row exists
        enqueued_at = datetime.now(timezone.utc)
        try:
            log_row = await db.execute(
                text("""
                    SELECT intake_log_id FROM application_intake_log
                    WHERE application_id = CAST(:aid AS uuid)
                      AND intake_method = 'manual_upload'
                    ORDER BY created_at DESC LIMIT 1
                """),
                {"aid": str(row["application_id"])},
            )
            log_rec = log_row.mappings().first()
            if log_rec:
                await db.execute(
                    text("""
                        UPDATE application_intake_log
                        SET scoring_enqueued_at = COALESCE(scoring_enqueued_at, :enqueued),
                            updated_at = now()
                        WHERE intake_log_id = CAST(:lid AS uuid)
                    """),
                    {"enqueued": enqueued_at, "lid": str(log_rec["intake_log_id"])},
                )
        except Exception as exc:
            logger.warning(
                "Could not update intake_log for application %s: %s",
                row["application_id"], exc,
            )
        count += 1

    await db.commit()
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
                COUNT(*) FILTER (WHERE processing_status = 'ai_scored') AS completed,
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


@router.patch("/{application_id}/workflow-status")
async def update_workflow_status(
    application_id: str,
    body: WorkflowStatusRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Transition an application's recruiter workflow status."""
    new_status = body.workflow_status.strip().lower()
    if new_status not in WORKFLOW_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid workflow_status '{new_status}'.",
        )

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT a.application_id, a.workflow_status
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = :aid AND a.tenant_id = :tid
              AND (
                :is_admin = TRUE
                OR j.client_organization_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM agency_user_clients auc
                    WHERE auc.user_id = CAST(:uid AS uuid)
                      AND auc.client_organization_id = j.client_organization_id
                      AND auc.tenant_id = CAST(:tid AS uuid)
                )
              )
        """),
        {
            "aid":      application_id,
            "tid":      current_user.tenant_id,
            "uid":      current_user.user_id,
            "is_admin": (current_user.role or "").lower() in ("admin", "super_admin"),
        },
    )
    rec = row.mappings().first()
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    current_status = rec["workflow_status"] or "awaiting_review"
    if new_status == current_status:
        return {"workflow_status": current_status}

    is_advanced = body.advanced_move

    if is_advanced:
        # Advanced move: privileged stage-jump — validate permission and require a note
        actor_role = (current_user.role or "").lower()
        if actor_role not in ("admin", "super_admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Advanced workflow moves require admin or super_admin role.",
            )
        if not body.note or not body.note.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A note/reason is required for advanced workflow moves.",
            )
        # Target must still be a valid workflow status, but no transition-graph check
    else:
        # Normal move: enforce the state-machine graph
        allowed = VALID_WORKFLOW_TRANSITIONS.get(current_status, frozenset())
        if new_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot transition workflow from '{current_status}' to '{new_status}'.",
            )

    await db.execute(
        text("""
            UPDATE applications
            SET workflow_status = :new_status, updated_at = now()
            WHERE application_id = CAST(:aid AS uuid) AND tenant_id = :tid
        """),
        {"new_status": new_status, "aid": application_id, "tid": current_user.tenant_id},
    )

    await db.execute(
        text("""
            INSERT INTO application_workflow_history
                (application_id, tenant_id, changed_by, changed_by_name,
                 from_status, to_status, note, is_advanced_move)
            VALUES
                (CAST(:aid AS uuid), CAST(:tid AS uuid), CAST(:uid AS uuid), :uname,
                 :from_s, :to_s, :note, :is_adv)
        """),
        {
            "aid":    application_id,
            "tid":    current_user.tenant_id,
            "uid":    current_user.user_id,
            "uname":  current_user.full_name or current_user.email,
            "from_s": current_status,
            "to_s":   new_status,
            "note":   body.note,
            "is_adv": is_advanced,
        },
    )

    await db.commit()
    return {"workflow_status": new_status}


@router.patch("/{application_id}/recruiter-notes")
async def update_recruiter_notes(
    application_id: str,
    body: RecruiterNotesRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Save or clear recruiter notes for an application."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT a.application_id
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = :aid AND a.tenant_id = :tid
              AND (
                :is_admin = TRUE
                OR j.client_organization_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM agency_user_clients auc
                    WHERE auc.user_id = CAST(:uid AS uuid)
                      AND auc.client_organization_id = j.client_organization_id
                      AND auc.tenant_id = CAST(:tid AS uuid)
                )
              )
        """),
        {
            "aid":      application_id,
            "tid":      current_user.tenant_id,
            "uid":      current_user.user_id,
            "is_admin": (current_user.role or "").lower() in ("admin", "super_admin"),
        },
    )
    if not row.mappings().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    await db.execute(
        text("""
            UPDATE applications
            SET recruiter_notes = :notes, updated_at = now()
            WHERE application_id = CAST(:aid AS uuid) AND tenant_id = :tid
        """),
        {"notes": body.recruiter_notes, "aid": application_id, "tid": current_user.tenant_id},
    )
    await db.commit()
    return {"recruiter_notes": body.recruiter_notes}


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


@router.get("/{application_id}/cv")
async def download_cv(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Serve the CV file for an application. Tenant-isolated."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT af.file_path, af.original_name, af.mime_type
            FROM application_files af
            JOIN applications a ON a.application_id = af.application_id
            JOIN jobs j ON j.job_id = a.job_id
            WHERE af.application_id = :aid AND a.tenant_id = :tid
              AND (
                :is_admin = TRUE
                OR j.client_organization_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM agency_user_clients auc
                    WHERE auc.user_id = CAST(:uid AS uuid)
                      AND auc.client_organization_id = j.client_organization_id
                      AND auc.tenant_id = CAST(:tid AS uuid)
                )
              )
            LIMIT 1
        """),
        {
            "aid":      application_id,
            "tid":      current_user.tenant_id,
            "uid":      current_user.user_id,
            "is_admin": (current_user.role or "").lower() in ("admin", "super_admin"),
        },
    )
    rec = row.mappings().first()
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV file not found")

    full_path = Path(settings.files_base_path) / rec["file_path"]
    if not full_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV file not found on disk")

    return FileResponse(
        path=str(full_path),
        filename=rec["original_name"] or full_path.name,
        media_type=rec["mime_type"] or "application/octet-stream",
    )


# Import here to avoid circular import
from auth.dependencies import get_current_user  # noqa: E402
