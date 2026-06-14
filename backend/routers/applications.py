import csv
import io
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
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
from services.communication_events import process_communication_event
from services.knockout_questions_service import save_knockout_answers
from services.knockout_analysis_service import (
    run_knockout_analysis,
    get_knockout_suggestions,
    accept_knockout_suggestion,
    ignore_knockout_suggestion,
)
from services.screening_validation_service import (
    run_screening_validation,
    get_knockout_validations,
    get_latest_validation_run,
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
    application_ids: list[str] | None = None  # for bulk operations


class RecruiterNotesRequest(BaseModel):
    recruiter_notes: str | None


class AssignmentRequest(BaseModel):
    assigned_user_id: str | None = None  # None = unassign


class BulkAssignmentRequest(BaseModel):
    application_ids:  list[str]
    assigned_user_id: str | None = None  # None = unassign all


class KnockoutAnswerRequest(BaseModel):
    question_id:  str
    answer_value: str


class KnockoutAcceptRequest(BaseModel):
    question_id:     str
    override_answer: str | None = None  # if set, saves this instead of AI suggestion


@router.get("/assignable-users")
async def get_assignable_users(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return active users in the tenant for use in assignment dropdowns.
    Accessible to all authenticated users (not just admin).
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT user_id, email, COALESCE(full_name, email) AS full_name, role
            FROM users
            WHERE tenant_id = CAST(:tid AS uuid)
              AND status IN ('active', 'pending_email_verification')
            ORDER BY full_name ASC
        """),
        {"tid": current_user.tenant_id},
    )
    users = [
        {
            "user_id":   str(r["user_id"]),
            "email":     r["email"],
            "full_name": r["full_name"],
            "role":      r["role"],
        }
        for r in rows.mappings()
    ]
    return {"users": users}


def _build_candidate_filter_clause(
    current_user,
    is_admin: bool,
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
    assigned_to: str | None = None,
    tag_ids: str | None = None,
    talent_pool_only: bool = False,
    validation_issues: bool | None = None,
    gender: str | None = None,
) -> tuple[str, dict]:
    """
    Build the shared WHERE clause + bind params for tenant-wide candidate
    listing/export queries. Centralised here so the list endpoint and the
    export endpoint apply identical filtering and access-control rules —
    "do not duplicate filtering logic".

    Returns (where_clause, params).
    """
    where_parts = [
        "a.tenant_id = CAST(:tid AS uuid)",
    ]
    params: dict = {
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
    # Aliases for recruiter-facing simplified filter values:
    #   in_progress      → queued | processing (actively in the scoring pipeline)
    #   stopped_before_ai → failed AND stopped_reason IS NOT NULL (pre-AI gate failures)
    #   failed_or_blocked → legacy alias for all non-recoverable system states
    if processing_status:
        if processing_status == 'in_progress':
            where_parts.append("a.processing_status IN ('queued', 'processing')")
        elif processing_status == 'stopped_before_ai':
            where_parts.append("a.processing_status = 'failed' AND a.stopped_reason IS NOT NULL")
        elif processing_status == 'pre_ai_stopped':
            # All applications that never reached AI scoring — used by the "Stopped Before AI" workspace view
            where_parts.append(
                "a.processing_status IN ('security_blocked', 'duplicate_blocked', "
                "'extraction_failed', 'processing_failed', 'stopped', 'failed')"
            )
        elif processing_status == 'failed_or_blocked':
            # Legacy alias: all failed/stopped states (backward compat with old quick-views)
            where_parts.append(
                "a.processing_status = 'failed'"
            )
        else:
            where_parts.append("a.processing_status = :proc_status")
            params["proc_status"] = processing_status

    # AI decision filter (maps to 'decision' column)
    # 'rejected_low_match' is the UI alias for DB value 'rejected'.
    # AI decisions only exist for ai_scored applications; add that constraint here
    # so callers don't need to pass processing_status=ai_scored separately.
    if ai_decision:
        db_decision = "rejected" if ai_decision == "rejected_low_match" else ai_decision
        where_parts.append("a.processing_status = 'ai_scored' AND a.decision = :ai_dec")
        params["ai_dec"] = db_decision

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

    # Assignment filter
    if assigned_to == "me":
        where_parts.append("a.assigned_user_id = CAST(:uid AS uuid)")
    elif assigned_to == "unassigned":
        where_parts.append("a.assigned_user_id IS NULL")
    elif assigned_to:
        where_parts.append("a.assigned_user_id = CAST(:assigned_to_id AS uuid)")
        params["assigned_to_id"] = assigned_to

    # Tag filter (tenant-wide mode only; ignored if job_id provided)
    if tag_ids and not job_id:
        tag_list = [tid.strip() for tid in tag_ids.split(',') if tid.strip()]
        if tag_list:
            tag_params: dict = {}
            tag_placeholders: list[str] = []
            for i, tid in enumerate(tag_list):
                key = f"tag_id_{i}"
                tag_params[key] = tid
                tag_placeholders.append(f"CAST(:{key} AS uuid)")
            params.update(tag_params)
            where_parts.append(f"""
                a.application_id IN (
                    SELECT DISTINCT application_id FROM application_candidate_tags
                    WHERE tag_id IN ({', '.join(tag_placeholders)})
                )
            """)

    # Talent pool filter (tenant-wide mode only; ignored if job_id provided)
    if talent_pool_only and not job_id:
        where_parts.append("a.is_talent_pool = TRUE")

    # Validation issues filter — candidates with any contradiction/cannot_determine/cannot_validate
    if validation_issues:
        where_parts.append("""
            EXISTS(
                SELECT 1 FROM application_knockout_answer_validations kv
                WHERE kv.application_id = a.application_id
                  AND kv.validation_status IN ('contradicted_by_cv', 'cannot_validate', 'cannot_determine')
            )
        """)

    # Gender filter — metadata only, never affects scoring
    if gender and gender in ("male", "female", "unknown"):
        where_parts.append("a.gender_value = :gender_value")
        params["gender_value"] = gender

    # Decision CW-1: Exclude stopped-before-AI records from all normal tenant-wide views.
    # These records are only accessible via the explicit 'pre_ai_stopped' processing_status filter
    # (used by the "Stopped Before AI" quick view). The job-scoped backward-compatible mode
    # (job_id provided) is unaffected so existing job detail pages continue to work.
    _pre_ai_stopped_statuses = (
        "'security_blocked', 'duplicate_blocked', 'extraction_failed', "
        "'processing_failed', 'stopped', 'failed'"
    )
    if not job_id and processing_status != 'pre_ai_stopped':
        where_parts.append(
            f"a.processing_status NOT IN ({_pre_ai_stopped_statuses})"
        )

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

    return " AND ".join(where_parts), params


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
    assigned_to: str | None = None,
    tag_ids: str | None = None,
    talent_pool_only: bool = False,
    validation_issues: bool | None = None,
    gender: str | None = None,
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

    where_clause, params = _build_candidate_filter_clause(
        current_user,
        is_admin,
        job_id=job_id,
        workflow_status=workflow_status,
        processing_status=processing_status,
        ai_decision=ai_decision,
        possible_duplicate=possible_duplicate,
        has_notes=has_notes,
        applied_after=applied_after,
        applied_before=applied_before,
        search=search,
        campaign_id=campaign_id,
        client_organization_id=client_organization_id,
        assigned_to=assigned_to,
        tag_ids=tag_ids,
        talent_pool_only=talent_pool_only,
        validation_issues=validation_issues,
        gender=gender,
    )

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
            a.is_talent_pool,
            s.final_score                       AS score,
            s.evaluation_notes                  AS summary,
            j.allow_advanced_workflow_move      AS job_allow_advanced_workflow_move,
            a.assigned_user_id,
            a.assigned_at,
            COALESCE(au.full_name, au.email)    AS assigned_user_name,
            a.preferred_contact_email,
            a.preferred_contact_source,
            a.gender_value,
            a.security_check_status,
            a.security_risk_level,
            CASE
                WHEN EXISTS(SELECT 1 FROM application_knockout_answer_validations kv
                             WHERE kv.application_id = a.application_id
                               AND kv.validation_status = 'contradicted_by_cv')
                    THEN 'contradiction'
                WHEN EXISTS(SELECT 1 FROM application_knockout_answer_validations kv
                             WHERE kv.application_id = a.application_id
                               AND kv.validation_status IN ('cannot_validate', 'cannot_determine'))
                    THEN 'cannot_determine'
                WHEN EXISTS(SELECT 1 FROM application_knockout_answer_validations kv
                             WHERE kv.application_id = a.application_id
                               AND kv.validation_status = 'not_supported_by_cv')
                    THEN 'not_supported'
                WHEN EXISTS(SELECT 1 FROM application_knockout_answer_validations kv
                             WHERE kv.application_id = a.application_id
                               AND kv.validation_status = 'suggested')
                    THEN 'suggestion'
                WHEN EXISTS(SELECT 1 FROM application_knockout_answer_validations kv
                             WHERE kv.application_id = a.application_id
                               AND kv.validation_status = 'supported_by_cv')
                    THEN 'validated'
                ELSE NULL
            END AS validation_summary
        FROM applications a
        JOIN jobs j ON j.job_id = a.job_id
        LEFT JOIN application_scores s ON s.application_id = a.application_id
        LEFT JOIN job_campaigns jc ON jc.campaign_id = j.campaign_id
        LEFT JOIN client_organizations co ON co.client_organization_id = j.client_organization_id
        LEFT JOIN users au ON au.user_id = a.assigned_user_id
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
            # Only AI-scored applications enter the recruiter workflow queue.
            # Stopped-before-AI applications (processing_status = 'failed') must not
            # masquerade as 'awaiting_review' — return their actual DB value (None/null).
            "workflow_status": (
                r["workflow_status"] or "awaiting_review"
                if r["processing_status"] == "ai_scored"
                else r["workflow_status"]
            ),
            "recruiter_notes": r["recruiter_notes"],
            "applied_at": r["applied_at"].isoformat() if r["applied_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            "score": float(r["score"]) if r["score"] is not None else None,
            "summary": r["summary"],
            "job_allow_advanced_workflow_move": bool(r["job_allow_advanced_workflow_move"]),
            "assigned_user_id":   str(r["assigned_user_id"]) if r["assigned_user_id"] else None,
            "assigned_user_name": r["assigned_user_name"],
            "assigned_at":        r["assigned_at"].isoformat() if r["assigned_at"] else None,
            "is_talent_pool":           bool(r["is_talent_pool"]) if r["is_talent_pool"] is not None else False,
            "preferred_contact_email":  r["preferred_contact_email"],
            "preferred_contact_source": r["preferred_contact_source"],
            "gender_value":             r["gender_value"] or "unknown",
            "security_check_status":    r["security_check_status"],
            "security_risk_level":      r["security_risk_level"],
            "validation_summary":       r["validation_summary"],
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


# Export field order shared by CSV and Excel — matches the requested column set.
_EXPORT_COLUMNS: list[str] = [
    "Candidate Name",
    "Candidate Email",
    "Job Title",
    "Job Code",
    "Campaign",
    "Client",
    "AI Score",
    "AI Recommendation",
    "Workflow Status",
    "Assigned Recruiter",
    "Applied Date",
    "Security Status",
    "Duplicate Status",
    "Talent Pool",
    "Recruiter Notes",
]

# Maps raw `decision`/`status` DB values (and the UI's 'rejected_low_match' alias)
# to the human-readable AI recommendation labels used across CSV/Excel/PDF export.
# Falls back to the raw value (or "Not Scored" when null/empty) for anything unmapped.
_AI_RECOMMENDATION_LABELS: dict[str, str] = {
    "qualified":          "Qualified",
    "partial":            "Partial Match",
    "rejected":           "Rejected — Low Match",
    "rejected_low_match": "Rejected — Low Match",
    "low_match":          "Rejected — Low Match",
    "not_scored":         "Not Scored",
}


def _effective_workflow_status(r) -> str:
    processing_status = r["processing_status"]
    if processing_status == "ai_scored":
        return r["workflow_status"] or "awaiting_review"
    return r["workflow_status"] or ""


def _ai_recommendation_label(r) -> str:
    raw = r["status"] or ""
    if not raw:
        return "Not Scored"
    return _AI_RECOMMENDATION_LABELS.get(raw, raw)


def _export_row(r) -> list:
    """Map a candidate export query row to the requested export column order."""
    return [
        r["candidate_name"] or "",
        r["candidate_email"] or "",
        r["job_title"] or "",
        r["job_code"] or "",
        r["campaign_name"] or "",
        r["client_org_name"] or "",
        float(r["score"]) if r["score"] is not None else "",
        _ai_recommendation_label(r),
        _effective_workflow_status(r),
        r["assigned_user_name"] or "",
        r["applied_at"].strftime("%Y-%m-%d %H:%M") if r["applied_at"] else "",
        r["security_check_status"] or "",
        r["duplicate_status"] or "not_duplicate",
        "Yes" if r["is_talent_pool"] else "No",
        r["recruiter_notes"] or "",
    ]


# ── PDF report helpers ────────────────────────────────────────────────────────

# ReportLab's built-in base-14 fonts (Helvetica, Times, Courier) only cover
# Latin-1 and render any Arabic codepoint as a "tofu" box (■). Bundling a
# Unicode font with Arabic glyph coverage and registering it with ReportLab's
# pdfmetrics is required to render Arabic candidate names / job titles / etc.
_PDF_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_PDF_FONT_FAMILY = "Helvetica"   # safe fallback if the bundled font can't be loaded
_PDF_FONT_FAMILY_BOLD = "Helvetica-Bold"
_pdf_fonts_registered = False

_ARABIC_CHAR_RE = re.compile(
    r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]"
)


def _ensure_pdf_fonts_registered() -> tuple[str, str]:
    """Register the bundled Amiri Unicode font (Arabic + Latin coverage) with
    ReportLab so PDF exports can render Arabic text. Idempotent — registration
    only happens once per process. Returns (regular_font_name, bold_font_name).
    Falls back to Helvetica if the font files are missing for any reason."""
    global _pdf_fonts_registered, _PDF_FONT_FAMILY, _PDF_FONT_FAMILY_BOLD
    if _pdf_fonts_registered:
        return _PDF_FONT_FAMILY, _PDF_FONT_FAMILY_BOLD

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.registerFont(TTFont("Amiri", str(_PDF_FONT_DIR / "Amiri-Regular.ttf")))
        pdfmetrics.registerFont(TTFont("Amiri-Bold", str(_PDF_FONT_DIR / "Amiri-Bold.ttf")))
        pdfmetrics.registerFontFamily("Amiri", normal="Amiri", bold="Amiri-Bold")
        _PDF_FONT_FAMILY = "Amiri"
        _PDF_FONT_FAMILY_BOLD = "Amiri-Bold"
    except Exception:
        logger.warning(
            "Could not load bundled Arabic-capable PDF font (Amiri); "
            "falling back to Helvetica — Arabic text will render as boxes.",
            exc_info=True,
        )

    _pdf_fonts_registered = True
    return _PDF_FONT_FAMILY, _PDF_FONT_FAMILY_BOLD


def _pdf_text(value) -> str:
    """Prepare a string for rendering inside a ReportLab Paragraph.

    ReportLab lays text out in logical (storage) order and left-to-right —
    it has no bidi engine. Arabic text must therefore be:
      1. reshaped (arabic_reshaper) so each letter uses its correct
         contextual glyph form (initial/medial/final/isolated), and
      2. reordered into visual order (python-bidi's get_display) so RTL runs
         display right-to-left while any embedded LTR runs (e.g. numbers,
         English words) remain correctly ordered.

    Strings without Arabic characters are returned unchanged (no-op for the
    overwhelming majority of English-only export content).
    """
    text = "" if value is None else str(value)
    if not _ARABIC_CHAR_RE.search(text):
        return text

    import arabic_reshaper
    from bidi.algorithm import get_display

    return get_display(arabic_reshaper.reshape(text))


_WORKFLOW_STATUS_LABELS: dict[str, str] = {
    "awaiting_review": "Awaiting Review",
    "under_review":    "Under Review",
    "shortlisted":     "Shortlisted",
    "interviewing":    "Interviewing",
    "offer_made":      "Offer Made",
    "hired":           "Hired",
    "rejected":        "Rejected",
    "withdrawn":       "Withdrawn",
    "on_hold":         "On Hold",
}

# Human-readable labels for filters surfaced in the PDF "Applied Filters" section.
_AI_DECISION_FILTER_LABELS: dict[str, str] = {
    "qualified":           "Qualified",
    "partial":             "Partial Match",
    "rejected":            "Rejected — Low Match",
    "rejected_low_match":  "Rejected — Low Match",
    "not_scored":          "Not Scored",
}

_PROCESSING_STATUS_FILTER_LABELS: dict[str, str] = {
    "ai_scored":          "AI Scored",
    "in_progress":        "In Progress",
    "stopped_before_ai":  "Stopped Before AI",
    "pre_ai_stopped":     "Stopped Before AI",
    "failed_or_blocked":  "Failed / Blocked",
}


async def _describe_active_filters(
    db: AsyncSession,
    current_user,
    *,
    job_id, workflow_status, processing_status, ai_decision,
    possible_duplicate, has_notes, applied_after, applied_before,
    search, campaign_id, client_organization_id, assigned_to, tag_ids,
    talent_pool_only, validation_issues,
) -> list[str]:
    """Build a list of human-readable 'Label: value' strings describing the
    filters applied to this export, for the PDF report's Applied Filters section.
    Resolves IDs to display names via small tenant-scoped lookups."""
    labels: list[str] = []

    if search:
        labels.append(f"Search: \"{search}\"")

    if job_id:
        row = (await db.execute(
            text("SELECT title FROM jobs WHERE job_id = CAST(:jid AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
            {"jid": job_id, "tid": current_user.tenant_id},
        )).first()
        labels.append(f"Job: {row[0] if row else job_id}")

    if campaign_id:
        row = (await db.execute(
            text("SELECT name FROM job_campaigns WHERE campaign_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
            {"cid": campaign_id, "tid": current_user.tenant_id},
        )).first()
        labels.append(f"Campaign: {row[0] if row else campaign_id}")

    if client_organization_id:
        row = (await db.execute(
            text("SELECT organization_name FROM client_organizations WHERE client_organization_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
            {"cid": client_organization_id, "tid": current_user.tenant_id},
        )).first()
        labels.append(f"Client: {row[0] if row else client_organization_id}")

    if workflow_status:
        labels.append(f"Workflow Status: {_WORKFLOW_STATUS_LABELS.get(workflow_status, workflow_status)}")

    if processing_status:
        labels.append(f"Queue / View: {_PROCESSING_STATUS_FILTER_LABELS.get(processing_status, processing_status)}")

    if ai_decision:
        labels.append(f"AI Recommendation: {_AI_DECISION_FILTER_LABELS.get(ai_decision, ai_decision)}")

    if applied_after:
        labels.append(f"Applied From: {applied_after}")
    if applied_before:
        labels.append(f"Applied To: {applied_before}")

    if assigned_to == "me":
        labels.append(f"Assigned Recruiter: {current_user.full_name or current_user.email} (Me)")
    elif assigned_to == "unassigned":
        labels.append("Assigned Recruiter: Unassigned")
    elif assigned_to:
        row = (await db.execute(
            text("SELECT COALESCE(full_name, email) FROM users WHERE user_id = CAST(:uid AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
            {"uid": assigned_to, "tid": current_user.tenant_id},
        )).first()
        labels.append(f"Assigned Recruiter: {row[0] if row else assigned_to}")

    if tag_ids:
        tag_list = [tid.strip() for tid in tag_ids.split(',') if tid.strip()]
        if tag_list:
            tag_rows = (await db.execute(
                text("SELECT tag_name FROM candidate_tags WHERE tag_id = ANY(:tids) AND tenant_id = CAST(:tid AS uuid)"),
                {"tids": tag_list, "tid": current_user.tenant_id},
            )).all()
            tag_names = [r[0] for r in tag_rows] or tag_list
            labels.append(f"Tags: {', '.join(tag_names)}")

    if talent_pool_only:
        labels.append("Talent Pool: Yes")
    if possible_duplicate is not None:
        labels.append(f"Possible Duplicate: {'Yes' if possible_duplicate else 'No'}")
    if has_notes is not None:
        labels.append(f"Has Notes: {'Yes' if has_notes else 'No'}")
    if validation_issues:
        labels.append("View: Validation Issues")

    return labels


def _build_pdf_export(
    records: list,
    filter_labels: list[str],
    generated_by: str,
    generated_at: str,
) -> bytes:
    """Render the candidate export as a multi-page A4 portrait PDF report
    using reportlab platypus (Report Summary page + Candidate Details table)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    )

    pdf_font, pdf_font_bold = _ensure_pdf_fonts_registered()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=18, spaceAfter=4, fontName=pdf_font_bold)
    meta_style = ParagraphStyle("ReportMeta", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#475569"), fontName=pdf_font)
    h2_style = ParagraphStyle("SectionHeading", parent=styles["Heading2"], fontSize=12, spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#1e293b"), fontName=pdf_font_bold)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9, leading=12, fontName=pdf_font)
    cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=8, leading=10, fontName=pdf_font)
    header_cell_style = ParagraphStyle("HeaderCell", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.white, fontName=pdf_font_bold)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Candidates Export Report",
    )

    story = []

    # ── Page 1: Report Summary ──
    story.append(Paragraph("CV Analyzer — Recruitment Platform", meta_style))
    story.append(Paragraph("Candidates Export Report", title_style))
    story.append(Paragraph(f"Generated by: {generated_by}", meta_style))
    story.append(Paragraph(f"Generated on: {generated_at}", meta_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Applied Filters", h2_style))
    if filter_labels:
        for lbl in filter_labels:
            story.append(Paragraph(f"• {_pdf_text(lbl)}", body_style))
    else:
        story.append(Paragraph("All Candidates (no filters applied)", body_style))

    total = len(records)
    story.append(Paragraph("Summary Statistics", h2_style))
    story.append(Paragraph(f"Total candidates exported: <b>{total}</b>", body_style))

    # AI Recommendation summary
    ai_counts = {"Qualified": 0, "Partial Match": 0, "Not Recommended": 0}
    for r in records:
        decision = (r["status"] or "").lower()
        if decision == "qualified":
            ai_counts["Qualified"] += 1
        elif decision == "partial":
            ai_counts["Partial Match"] += 1
        elif decision:
            ai_counts["Not Recommended"] += 1

    story.append(Paragraph("AI Recommendation Summary", h2_style))
    ai_table = Table(
        [["Recommendation", "Count"]] + [[k, str(v)] for k, v in ai_counts.items()],
        colWidths=[90 * mm, 30 * mm],
    )
    ai_table.setStyle(_summary_table_style())
    story.append(ai_table)

    # Workflow summary
    workflow_counts = {label: 0 for label in _WORKFLOW_STATUS_LABELS.values()}
    for r in records:
        wf = _effective_workflow_status(r)
        label = _WORKFLOW_STATUS_LABELS.get(wf)
        if label:
            workflow_counts[label] += 1

    story.append(Paragraph("Workflow Summary", h2_style))
    wf_table = Table(
        [["Workflow Status", "Count"]] + [[k, str(v)] for k, v in workflow_counts.items()],
        colWidths=[90 * mm, 30 * mm],
    )
    wf_table.setStyle(_summary_table_style())
    story.append(wf_table)

    # Optional indicators — only shown when non-zero
    security_warnings = sum(1 for r in records if (r["security_check_status"] or "") in ("warning", "blocked"))
    possible_duplicates = sum(1 for r in records if (r["duplicate_status"] or "") in ("possible_duplicate", "confirmed_duplicate"))
    optional_rows = []
    if security_warnings:
        optional_rows.append(["Security Warnings", str(security_warnings)])
    if possible_duplicates:
        optional_rows.append(["Possible Duplicates", str(possible_duplicates)])

    if optional_rows:
        story.append(Paragraph("Additional Indicators", h2_style))
        opt_table = Table([["Indicator", "Count"]] + optional_rows, colWidths=[90 * mm, 30 * mm])
        opt_table.setStyle(_summary_table_style())
        story.append(opt_table)

    # ── Page 2+: Candidate Details ──
    story.append(PageBreak())
    story.append(Paragraph("Candidate Details", h2_style))

    detail_columns = [
        "Candidate Name", "Job Title", "AI Score", "AI Recommendation",
        "Workflow Status", "Assigned Recruiter", "Applied Date",
    ]
    table_data = [[Paragraph(c, header_cell_style) for c in detail_columns]]
    for r in records:
        score = r["score"]
        table_data.append([
            Paragraph(_pdf_text(r["candidate_name"]) or "", cell_style),
            Paragraph(_pdf_text(r["job_title"]) or "", cell_style),
            Paragraph(f"{float(score):.0f}" if score is not None else "—", cell_style),
            Paragraph(_ai_recommendation_label(r) or "—", cell_style),
            Paragraph(_WORKFLOW_STATUS_LABELS.get(_effective_workflow_status(r), "—"), cell_style),
            Paragraph(_pdf_text(r["assigned_user_name"]) or "Unassigned", cell_style),
            Paragraph(r["applied_at"].strftime("%Y-%m-%d") if r["applied_at"] else "—", cell_style),
        ])

    detail_table = Table(
        table_data,
        colWidths=[36 * mm, 36 * mm, 18 * mm, 28 * mm, 26 * mm, 30 * mm, 24 * mm],
        repeatRows=1,
    )
    detail_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), pdf_font_bold),
        ("FONTNAME", (0, 1), (-1, -1), pdf_font),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(detail_table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def _summary_table_style():
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])


@router.get("/export")
async def export_applications(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    format: str = "csv",
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
    assigned_to: str | None = None,
    tag_ids: str | None = None,
    talent_pool_only: bool = False,
    validation_issues: bool | None = None,
    sort_by: str = "applied_at",
    sort_order: str = "desc",
):
    """
    Export ALL candidates matching the current Candidates Workspace filters
    (not just the current page) as CSV, Excel/XLSX, or a formatted PDF report.

    Reuses the exact same filter-building and access-control logic as
    GET /applications (see _build_candidate_filter_clause) so export results
    always match what the recruiter sees in the workspace, scoped to their
    tenant and client access.
    """
    fmt = (format or "csv").lower()
    if fmt not in ("csv", "xlsx", "excel", "pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported export format. Supported formats: csv, xlsx, pdf.",
        )

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")

    valid_sort_fields = {"applied_at", "updated_at", "score", "candidate_name"}
    sort_by = sort_by if sort_by in valid_sort_fields else "applied_at"
    sort_order = "ASC" if sort_order.lower() == "asc" else "DESC"

    where_clause, params = _build_candidate_filter_clause(
        current_user,
        is_admin,
        job_id=job_id,
        workflow_status=workflow_status,
        processing_status=processing_status,
        ai_decision=ai_decision,
        possible_duplicate=possible_duplicate,
        has_notes=has_notes,
        applied_after=applied_after,
        applied_before=applied_before,
        search=search,
        campaign_id=campaign_id,
        client_organization_id=client_organization_id,
        assigned_to=assigned_to,
        tag_ids=tag_ids,
        talent_pool_only=talent_pool_only,
        validation_issues=validation_issues,
    )

    sort_column = {
        "applied_at": "a.applied_at",
        "updated_at": "a.scored_at",
        "score": "s.final_score",
        "candidate_name": "a.candidate_name",
    }.get(sort_by, "a.applied_at")

    # No LIMIT/OFFSET — export must cover every matching record, not one page.
    data_query = f"""
        SELECT
            a.candidate_name,
            a.candidate_email,
            j.title                          AS job_title,
            j.job_code,
            jc.name                          AS campaign_name,
            co.organization_name             AS client_org_name,
            s.final_score                    AS score,
            a.decision                       AS status,
            a.processing_status,
            a.workflow_status,
            COALESCE(au.full_name, au.email) AS assigned_user_name,
            a.applied_at,
            a.security_check_status,
            a.duplicate_status,
            a.is_talent_pool,
            a.recruiter_notes
        FROM applications a
        JOIN jobs j ON j.job_id = a.job_id
        LEFT JOIN application_scores s ON s.application_id = a.application_id
        LEFT JOIN job_campaigns jc ON jc.campaign_id = j.campaign_id
        LEFT JOIN client_organizations co ON co.client_organization_id = j.client_organization_id
        LEFT JOIN users au ON au.user_id = a.assigned_user_id
        WHERE {where_clause}
        ORDER BY {sort_column} {sort_order}
    """

    rows = await db.execute(text(data_query), params)
    records = list(rows.mappings())

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(_EXPORT_COLUMNS)
        for r in records:
            writer.writerow(_export_row(r))
        buffer.seek(0)
        filename = f"candidates_export_{timestamp}.csv"
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if fmt in ("xlsx", "excel"):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Candidates"
        ws.append(_EXPORT_COLUMNS)
        for r in records:
            ws.append(_export_row(r))

        xlsx_buffer = io.BytesIO()
        wb.save(xlsx_buffer)
        xlsx_buffer.seek(0)
        filename = f"candidates_export_{timestamp}.xlsx"
        return StreamingResponse(
            iter([xlsx_buffer.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # PDF report
    filter_labels = await _describe_active_filters(
        db, current_user,
        job_id=job_id, workflow_status=workflow_status, processing_status=processing_status,
        ai_decision=ai_decision, possible_duplicate=possible_duplicate, has_notes=has_notes,
        applied_after=applied_after, applied_before=applied_before, search=search,
        campaign_id=campaign_id, client_organization_id=client_organization_id,
        assigned_to=assigned_to, tag_ids=tag_ids, talent_pool_only=talent_pool_only,
        validation_issues=validation_issues,
    )
    pdf_bytes = _build_pdf_export(
        records,
        filter_labels,
        generated_by=current_user.full_name or current_user.email,
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )
    filename = f"candidates_export_{timestamp}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
                a.preferred_contact_email, a.preferred_contact_source,
                a.preferred_contact_confidence,
                a.gender_value, a.gender_confidence, a.gender_basis,
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
                s.det_score_json,
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

    # Fetch all knockout questions for this job, LEFT JOINing answers so unanswered
    # questions also appear (answer fields will be NULL for those rows).
    ko_rows = await db.execute(
        text("""
            SELECT
                a.answer_id,
                q.question_id,
                a.answer_value,
                a.is_disqualifying,
                a.answer_source,
                a.answer_method,
                a.evidence_text,
                a.confidence,
                a.updated_at   AS answer_updated_at,
                u.full_name    AS updated_by_name,
                q.question_text,
                q.question_type,
                q.is_required,
                q.options,
                q.passing_criteria,
                q.display_order
            FROM job_knockout_questions q
            LEFT JOIN application_knockout_answers a
                   ON a.question_id = q.question_id
                  AND a.application_id = CAST(:aid AS uuid)
            LEFT JOIN users u ON u.user_id = a.updated_by
            WHERE q.job_id = :jid
            ORDER BY q.display_order, q.created_at
        """),
        {"aid": application_id, "jid": str(app["job_id"])},
    )
    knockout_answers = [
        {
            "answer_id":        str(r["answer_id"]) if r["answer_id"] else None,
            "question_id":      str(r["question_id"]),
            "answer_value":     r["answer_value"],
            "is_disqualifying": r["is_disqualifying"],
            "answer_source":    r["answer_source"],
            "answer_method":    r["answer_method"],
            "evidence_text":    r["evidence_text"],
            "confidence":       float(r["confidence"]) if r["confidence"] is not None else None,
            "updated_at":       r["answer_updated_at"].isoformat() if r["answer_updated_at"] else None,
            "updated_by_name":  r["updated_by_name"],
            "question_text":    r["question_text"],
            "question_type":    r["question_type"],
            "is_required":      r["is_required"],
            "options":          r["options"],
            "passing_criteria": r["passing_criteria"],
            "display_order":    r["display_order"],
        }
        for r in ko_rows.mappings()
    ]

    # Fetch AI knockout suggestions
    knockout_suggestions = await get_knockout_suggestions(db, application_id)

    # Fetch screening validation results
    knockout_validations = await get_knockout_validations(db, application_id)
    latest_validation_run = await get_latest_validation_run(db, application_id)

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
        "preferred_contact_email": app["preferred_contact_email"],
        "preferred_contact_source": app["preferred_contact_source"],
        "preferred_contact_confidence": float(app["preferred_contact_confidence"]) if app["preferred_contact_confidence"] is not None else None,
        "gender_value":      app["gender_value"] or "unknown",
        "gender_confidence": float(app["gender_confidence"]) if app["gender_confidence"] is not None else 0.0,
        "gender_basis":      app["gender_basis"] or "unknown",
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
        "det_score": app["det_score_json"] or None,
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
        "knockout_suggestions":       knockout_suggestions,
        "knockout_validations":       knockout_validations,
        "latest_validation_run":      latest_validation_run,
        # Same business rule as the list endpoint (line ~465): only ai_scored
        # candidates get the 'awaiting_review' null-fallback; other processing
        # states return their actual DB value (None/null). Never surface the
        # legacy 'new' placeholder — it is not a valid recruiter workflow status.
        "workflow_status": (
            app["workflow_status"] or "awaiting_review"
            if app["processing_status"] == "ai_scored"
            else app["workflow_status"]
        ),
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
            SELECT a.application_id, a.workflow_status, a.processing_status, a.job_id,
                   j.allow_advanced_workflow_move
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

    # Only AI-scored candidates are managed through the recruiter workflow.
    # Stopped-before-AI candidates (processing_status != 'ai_scored') are system-managed
    # and must not be transitioned through recruiter workflow moves.
    if rec["processing_status"] != "ai_scored":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only AI-scored applications can be moved through the recruiter workflow.",
        )

    # Null-safe effective status: migration 077 made workflow_status nullable for
    # stopped-before-AI candidates. For ai_scored candidates, NULL means the record
    # predates the initial awaiting_review assignment and should be treated as such.
    effective_current_status: str = rec["workflow_status"] if rec["workflow_status"] else "awaiting_review"

    if new_status == effective_current_status:
        return {"workflow_status": effective_current_status}

    is_advanced = body.advanced_move

    if is_advanced:
        # Advanced move: privileged stage-jump — validate permission, tenant setting, job setting, and require a note
        # Check tenant setting
        tenant_row = await db.execute(
            text("SELECT allow_advanced_workflow_move FROM tenants WHERE tenant_id = :tid"),
            {"tid": current_user.tenant_id},
        )
        tenant = tenant_row.mappings().first()
        if not tenant or not tenant["allow_advanced_workflow_move"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Exceptional workflow moves are disabled for this tenant.",
            )

        # Check job-level setting
        if not rec["allow_advanced_workflow_move"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Exceptional workflow moves are disabled for this job.",
            )

        actor_role = (current_user.role or "").lower()
        if actor_role not in ("admin", "super_admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Exceptional workflow moves require admin or super_admin role.",
            )
        if not body.note or not body.note.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A note/reason is required for exceptional workflow moves.",
            )
        # Target must still be a valid workflow status, but no transition-graph check
    else:
        # Normal move: enforce the state-machine graph
        allowed = VALID_WORKFLOW_TRANSITIONS.get(effective_current_status, frozenset())
        if new_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot transition workflow from '{effective_current_status}' to '{new_status}'.",
            )

    # ── Workflow policy enforcement ────────────────────────────────────────────
    import json as _json
    _pol_row = await db.execute(
        text("SELECT policies FROM tenant_workflow_policies WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    _pol_rec = _pol_row.mappings().first()
    _pol: dict = {}
    if _pol_rec and _pol_rec["policies"]:
        _raw = _pol_rec["policies"]
        _pol = _json.loads(_raw) if isinstance(_raw, str) else dict(_raw)

    if new_status == "rejected":
        if _pol.get("require_rejection_reason") and not (body.note and body.note.strip()):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A rejection reason is required by your organisation's policy.",
            )

    if new_status == "offer_made" and _pol.get("require_interview_before_offer"):
        _iv_row = await db.execute(
            text("""
                SELECT COUNT(*) AS cnt FROM candidate_interviews
                WHERE application_id = CAST(:aid AS uuid)
                  AND tenant_id = CAST(:tid AS uuid)
                  AND status = 'completed'
            """),
            {"aid": application_id, "tid": current_user.tenant_id},
        )
        if (_iv_row.scalar() or 0) == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Policy requires at least one completed interview before making an offer.",
            )
        if _pol.get("require_interview_feedback"):
            _fb_row = await db.execute(
                text("""
                    SELECT COUNT(*) AS cnt
                    FROM candidate_interviews ci
                    JOIN candidate_interview_feedback cif ON cif.interview_id = ci.interview_id
                    WHERE ci.application_id = CAST(:aid AS uuid)
                      AND ci.tenant_id = CAST(:tid AS uuid)
                      AND ci.status = 'completed'
                """),
                {"aid": application_id, "tid": current_user.tenant_id},
            )
            if (_fb_row.scalar() or 0) == 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Policy requires interview feedback before making an offer.",
                )

    if new_status == "hired" and _pol.get("require_approval_before_hire"):
        _stages = _pol.get("required_approval_stages", [])
        if _stages:
            _ap_row = await db.execute(
                text("""
                    SELECT COUNT(*) AS cnt FROM candidate_approvals
                    WHERE application_id = CAST(:aid AS uuid)
                      AND tenant_id = CAST(:tid AS uuid)
                      AND decision != 'approved'
                """),
                {"aid": application_id, "tid": current_user.tenant_id},
            )
            pending_or_rejected = _ap_row.scalar() or 0
            _ap_total = await db.execute(
                text("""
                    SELECT COUNT(*) AS cnt FROM candidate_approvals
                    WHERE application_id = CAST(:aid AS uuid)
                      AND tenant_id = CAST(:tid AS uuid)
                """),
                {"aid": application_id, "tid": current_user.tenant_id},
            )
            total = _ap_total.scalar() or 0
            if total == 0 or pending_or_rejected > 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Policy requires all approval stages to be approved before hiring.",
                )
    # ──────────────────────────────────────────────────────────────────────────

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
            "from_s": effective_current_status,
            "to_s":   new_status,
            "note":   body.note,
            "is_adv": is_advanced,
        },
    )

    _EVENT_MAP = {
        "shortlisted": "workflow_shortlisted",
        "rejected": "workflow_rejected",
        "offer_made": "offer_made",
    }
    if new_status in _EVENT_MAP:
        await process_communication_event(
            db=db,
            application_id=application_id,
            event_type=_EVENT_MAP[new_status],
            tenant_id=current_user.tenant_id,
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


@router.patch("/{application_id}/knockout-answers", status_code=status.HTTP_200_OK)
async def update_knockout_answer(
    application_id: str,
    body: KnockoutAnswerRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Allow authenticated recruiters/admins to add or update a single knockout answer."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Verify the application belongs to this tenant
    row = await db.execute(
        text("""
            SELECT a.job_id FROM applications a
            WHERE a.application_id = CAST(:aid AS uuid) AND a.tenant_id = :tid
        """),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    app_row = row.mappings().first()
    if not app_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    await save_knockout_answers(
        db=db,
        application_id=application_id,
        job_id=str(app_row["job_id"]),
        answers=[{"question_id": body.question_id, "answer_value": body.answer_value}],
        answer_source="recruiter_entered",
        answer_method="manual_entry",
        updated_by=current_user.user_id,
    )
    await db.commit()
    return {"status": "ok"}


@router.post("/{application_id}/knockout-analysis", status_code=status.HTTP_200_OK)
async def trigger_knockout_analysis(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    force_all: bool = False,
):
    """Manually trigger AI knockout answer analysis for an application.

    force_all=true re-analyses even questions that already have a final answer.

    Returns:
        status "ok"       — success; suggestions list may be empty if all questions are answered
        status "no_content" — no CV text or email content available
        status "error"    — AI call or other failure; reason field contains safe message
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT a.job_id FROM applications a
            WHERE a.application_id = CAST(:aid AS uuid) AND a.tenant_id = :tid
        """),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    app_row = row.mappings().first()
    if not app_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    suggestions_raw, error_reason = await run_knockout_analysis(
        db=db,
        application_id=application_id,
        job_id=str(app_row["job_id"]),
        tenant_id=current_user.tenant_id,
        force_all=force_all,
    )

    if suggestions_raw:
        await db.commit()
        # Re-apply RLS context (SET LOCAL resets after commit)
        await set_rls_context(db, current_user.tenant_id, current_user.role)
        full_suggestions = await get_knockout_suggestions(db, application_id)
    else:
        full_suggestions = []

    if error_reason:
        # Distinguish "no content" from hard errors for frontend messaging
        outcome = "no_content" if "no cv text" in error_reason.lower() else "error"
        return {
            "status": outcome,
            "reason": error_reason,
            "suggestions": [],
            "suggestions_generated": 0,
        }

    return {
        "status": "ok",
        "reason": None,
        "suggestions": full_suggestions,
        "suggestions_generated": len(full_suggestions),
    }


@router.post("/{application_id}/screening-validation", status_code=status.HTTP_200_OK)
async def trigger_screening_validation(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Run Screening Answer Validation for all knockout questions.

    For each question:
    - If a final answer exists: validates it against CV evidence and returns
      supported_by_cv | contradicted_by_cv | not_supported_by_cv | cannot_validate
    - If no final answer exists: suggests an answer when CV evidence is strong,
      or returns cannot_determine when evidence is insufficient

    AI does not force answers. Existing final answers are never overwritten.
    Suggestions for unanswered questions are stored as pending and require
    recruiter acceptance before becoming final answers.

    Returns:
        status "ok"         — success; validations and suggestions populated
        status "no_content" — no CV text or email content available
        status "error"      — AI call or other failure
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT a.job_id FROM applications a
            WHERE a.application_id = CAST(:aid AS uuid) AND a.tenant_id = :tid
        """),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    app_row = row.mappings().first()
    if not app_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    validations_raw, error_reason = await run_screening_validation(
        db=db,
        application_id=application_id,
        job_id=str(app_row["job_id"]),
        tenant_id=current_user.tenant_id,
    )

    if error_reason == "already_completed":
        await set_rls_context(db, current_user.tenant_id, current_user.role)
        full_validations = await get_knockout_validations(db, application_id)
        latest_run = await get_latest_validation_run(db, application_id)
        return {
            "status":     "already_completed",
            "reason":     "Screening validation has already been completed for this application.",
            "validations": full_validations,
            "suggestions": [],
            "run":         latest_run,
        }

    if error_reason:
        outcome = "no_content" if "no cv text" in error_reason.lower() else "error"
        return {
            "status": outcome,
            "reason": error_reason,
            "validations": [],
            "suggestions": [],
        }

    await db.commit()
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    full_validations = await get_knockout_validations(db, application_id)
    full_suggestions = await get_knockout_suggestions(db, application_id)
    latest_run = await get_latest_validation_run(db, application_id)

    return {
        "status":           "ok",
        "reason":           None,
        "validations":      full_validations,
        "suggestions":      full_suggestions,
        "validations_count": len(full_validations),
        "run":              latest_run,
    }


@router.patch("/{application_id}/knockout-suggestions/accept", status_code=status.HTTP_200_OK)
async def accept_knockout_suggestion_endpoint(
    application_id: str,
    body: KnockoutAcceptRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Accept an AI suggestion (optionally with an edited answer), saving it as a final answer.

    Accept without edit (or edit to same value): answer_source = AI source, answer_method = recruiter_approved.
    Edit to a different value: answer_source = recruiter_entered, answer_method = manual_entry.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT a.job_id FROM applications a
            WHERE a.application_id = CAST(:aid AS uuid) AND a.tenant_id = :tid
        """),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    app_row = row.mappings().first()
    if not app_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    await accept_knockout_suggestion(
        db=db,
        application_id=application_id,
        job_id=str(app_row["job_id"]),
        question_id=body.question_id,
        accepted_by_user_id=current_user.user_id,
        override_answer=body.override_answer,
    )
    await db.commit()
    return {"status": "ok"}


@router.patch("/{application_id}/knockout-suggestions/ignore", status_code=status.HTTP_200_OK)
async def ignore_knockout_suggestion_endpoint(
    application_id: str,
    body: KnockoutAnswerRequest,   # reuse: only question_id matters, answer_value ignored
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Mark an AI suggestion as ignored (dismissed without accepting)."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT application_id FROM applications
            WHERE application_id = CAST(:aid AS uuid) AND tenant_id = :tid
        """),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    if not row.mappings().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    await ignore_knockout_suggestion(db=db, application_id=application_id, question_id=body.question_id)
    await db.commit()
    return {"status": "ok"}


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


@router.patch("/bulk-assignment")
async def bulk_update_assignment(
    body: BulkAssignmentRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Bulk assign (or unassign) candidates to a recruiter."""
    if not body.application_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one application_id is required.",
        )

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")

    # Verify target user exists in tenant (if assigning, not unassigning)
    if body.assigned_user_id:
        target_user = await db.execute(
            text("""
                SELECT user_id FROM users
                WHERE user_id   = CAST(:target_uid AS uuid)
                  AND tenant_id = CAST(:tid AS uuid)
                  AND status    = 'active'
            """),
            {"target_uid": body.assigned_user_id, "tid": current_user.tenant_id},
        )
        if not target_user.first():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target user not found or not active in this tenant.",
            )

        # Permission: recruiter can only assign to self
        if not is_admin and body.assigned_user_id != current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Recruiters can only assign candidates to themselves.",
            )

    await db.execute(
        text("""
            UPDATE applications
               SET assigned_user_id = CAST(:target_uid AS uuid),
                   assigned_at      = CASE WHEN CAST(:target_uid AS uuid) IS NOT NULL THEN now() ELSE NULL END,
                   assigned_by      = CASE WHEN CAST(:target_uid AS uuid) IS NOT NULL THEN CAST(:uid AS uuid) ELSE NULL END
             WHERE application_id = ANY(CAST(:aids AS uuid[]))
               AND tenant_id      = CAST(:tid AS uuid)
        """),
        {
            "target_uid": body.assigned_user_id,
            "uid":        current_user.user_id,
            "aids":       body.application_ids,
            "tid":        current_user.tenant_id,
        },
    )
    await db.commit()
    return {"success": True, "updated_count": len(body.application_ids)}


@router.patch("/{application_id}/assignment")
async def update_assignment(
    application_id: str,
    body: AssignmentRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Assign or unassign a single candidate to a recruiter."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")

    if body.assigned_user_id:
        target_user = await db.execute(
            text("""
                SELECT user_id FROM users
                WHERE user_id   = CAST(:target_uid AS uuid)
                  AND tenant_id = CAST(:tid AS uuid)
                  AND status    = 'active'
            """),
            {"target_uid": body.assigned_user_id, "tid": current_user.tenant_id},
        )
        if not target_user.first():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target user not found or not active in this tenant.",
            )

        if not is_admin and body.assigned_user_id != current_user.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Recruiters can only assign candidates to themselves.",
            )

    result = await db.execute(
        text("""
            UPDATE applications
               SET assigned_user_id = CAST(:target_uid AS uuid),
                   assigned_at      = CASE WHEN CAST(:target_uid AS uuid) IS NOT NULL THEN now() ELSE NULL END,
                   assigned_by      = CASE WHEN CAST(:target_uid AS uuid) IS NOT NULL THEN CAST(:uid AS uuid) ELSE NULL END
             WHERE application_id = CAST(:aid AS uuid)
               AND tenant_id      = CAST(:tid AS uuid)
            RETURNING application_id, assigned_user_id, assigned_at
        """),
        {
            "target_uid": body.assigned_user_id,
            "uid":        current_user.user_id,
            "aid":        application_id,
            "tid":        current_user.tenant_id,
        },
    )

    row = result.first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")

    # Fetch assignee name if assigned
    assigned_user_name = None
    if body.assigned_user_id:
        u_row = await db.execute(
            text("SELECT COALESCE(full_name, email) AS name FROM users WHERE user_id = CAST(:uid AS uuid)"),
            {"uid": body.assigned_user_id},
        )
        u = u_row.first()
        if u:
            assigned_user_name = u[0]

    await db.commit()
    return {
        "success": True,
        "assigned_user_id":   body.assigned_user_id,
        "assigned_user_name": assigned_user_name,
        "assigned_at":        row[2].isoformat() if row[2] else None,
    }


@router.patch("/bulk-workflow-status")
async def bulk_update_workflow_status(
    body: WorkflowStatusRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Bulk transition workflow status for multiple applications.

    - Only supports normal (non-advanced) moves for now
    - Skips system-managed or invalid candidates safely
    - Returns summary: updated_count, skipped_count, skipped_reasons
    """
    application_ids = (body.application_ids or []) if hasattr(body, 'application_ids') else []
    if not application_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one application_id is required.",
        )

    new_status = body.workflow_status.strip().lower()
    if new_status not in WORKFLOW_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid workflow_status '{new_status}'.",
        )

    if body.advanced_move:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Exceptional moves are not supported in bulk operations.",
        )

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # ── Bulk workflow policy gate ──────────────────────────────────────────────
    import json as _json
    _pol_row = await db.execute(
        text("SELECT policies FROM tenant_workflow_policies WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    _pol_rec = _pol_row.mappings().first()
    _pol: dict = {}
    if _pol_rec and _pol_rec["policies"]:
        _raw = _pol_rec["policies"]
        _pol = _json.loads(_raw) if isinstance(_raw, str) else dict(_raw)

    if new_status == "rejected":
        if not _pol.get("allow_bulk_reject", True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bulk rejection is disabled by your organisation's policy.",
            )
        if _pol.get("require_rejection_reason") and not (body.note and body.note.strip()):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A rejection reason is required by your organisation's policy.",
            )
    # ──────────────────────────────────────────────────────────────────────────

    # Fetch all applications with current status; skip system-managed
    rows = await db.execute(
        text("""
            SELECT a.application_id, a.workflow_status, a.processing_status
            FROM applications a
            WHERE a.application_id = ANY(CAST(:aids AS uuid[]))
              AND a.tenant_id = CAST(:tid AS uuid)
              AND a.processing_status = 'ai_scored'
        """),
        {"aids": application_ids, "tid": current_user.tenant_id},
    )
    valid_apps = {str(r["application_id"]): r["workflow_status"] for r in rows.mappings()}

    # Check for access permissions and client org restrictions
    rows = await db.execute(
        text("""
            SELECT DISTINCT a.application_id
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = ANY(CAST(:aids AS uuid[]))
              AND a.tenant_id = CAST(:tid AS uuid)
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
            "aids":     application_ids,
            "tid":      current_user.tenant_id,
            "uid":      current_user.user_id,
            "is_admin": (current_user.role or "").lower() in ("admin", "super_admin"),
        },
    )
    accessible_ids = {str(r["application_id"]) for r in rows.mappings()}

    # Build update list: only valid, accessible, non-draft, with valid transitions
    to_update: list[str] = []
    skipped_reasons: dict[str, str] = {}

    for aid in application_ids:
        if aid not in valid_apps:
            skipped_reasons[aid] = "Not ai_scored or not found"
            continue
        if aid not in accessible_ids:
            skipped_reasons[aid] = "No access to candidate's job/client"
            continue

        current_status = valid_apps[aid]
        allowed = VALID_WORKFLOW_TRANSITIONS.get(current_status, frozenset())
        if new_status not in allowed:
            skipped_reasons[aid] = f"Invalid transition from {current_status}"
            continue

        to_update.append(aid)

    # Batch update workflow status
    if to_update:
        await db.execute(
            text("""
                UPDATE applications
                SET workflow_status = :new_status, updated_at = now()
                WHERE application_id = ANY(CAST(:aids AS uuid[]))
                  AND tenant_id = CAST(:tid AS uuid)
            """),
            {"new_status": new_status, "aids": to_update, "tid": current_user.tenant_id},
        )

        # Batch insert workflow history (one per candidate)
        for aid in to_update:
            await db.execute(
                text("""
                    INSERT INTO application_workflow_history
                        (application_id, tenant_id, changed_by, changed_by_name,
                         from_status, to_status, note, is_advanced_move)
                    VALUES
                        (CAST(:aid AS uuid), CAST(:tid AS uuid), CAST(:uid AS uuid), :uname,
                         :from_s, :to_s, :note, FALSE)
                """),
                {
                    "aid":   aid,
                    "tid":   current_user.tenant_id,
                    "uid":   current_user.user_id,
                    "uname": current_user.full_name or current_user.email,
                    "from_s": valid_apps[aid],
                    "to_s":   new_status,
                    "note":   body.note,
                },
            )

    await db.commit()

    # Fetch candidate names for updated and skipped candidates
    all_ids = to_update + list(skipped_reasons.keys())
    updated_candidates = []
    skipped_candidates = []

    if all_ids:
        rows = await db.execute(
            text("""
                SELECT a.application_id, a.candidate_name, a.workflow_status
                FROM applications a
                WHERE a.application_id = ANY(CAST(:aids AS uuid[]))
                  AND a.tenant_id = CAST(:tid AS uuid)
            """),
            {"aids": all_ids, "tid": current_user.tenant_id},
        )
        candidates_by_id = {str(r["application_id"]): r for r in rows.mappings()}

        for aid in to_update:
            if aid in candidates_by_id:
                c = candidates_by_id[aid]
                updated_candidates.append({
                    "application_id": aid,
                    "candidate_name": c["candidate_name"],
                    "workflow_status": c["workflow_status"],
                })

        for aid, reason in skipped_reasons.items():
            if aid in candidates_by_id:
                c = candidates_by_id[aid]
                skipped_candidates.append({
                    "application_id": aid,
                    "candidate_name": c["candidate_name"],
                    "reason": reason,
                })

    return {
        "success": True,
        "updated_count": len(to_update),
        "skipped_count": len(skipped_reasons),
        "updated_candidates": updated_candidates,
        "skipped_candidates": skipped_candidates,
    }


# Import here to avoid circular import
from auth.dependencies import get_current_user  # noqa: E402
