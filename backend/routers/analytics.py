import json
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ── Pydantic Models ────────────────────────────────────────────────────────


class FunnelStageMetric(BaseModel):
    workflow_status: str
    stage_count: int
    conversion_from_previous: float | None
    percentage_of_pipeline: float
    avg_days_in_stage: float | None
    median_days_in_stage: float | None


class FunnelMetricsResponse(BaseModel):
    stages: list[FunnelStageMetric]
    total_applications: int
    date_range: dict
    filters: dict


class RecruiterProductivityMetric(BaseModel):
    user_id: str
    recruiter_name: str
    total_applications_assigned: int
    applications_in_review: int
    workflow_moves_made: int
    interviews_completed: int
    feedback_provided: int
    approvals_decided: int
    avg_days_assigned: float | None


class RecruiterProductivityResponse(BaseModel):
    recruiters: list[RecruiterProductivityMetric]
    date_range: dict


class AgingMetric(BaseModel):
    application_id: str
    candidate_name: str
    job_id: str
    job_title: str
    workflow_status: str
    assigned_user_id: str | None
    assigned_user_name: str | None
    days_in_status: float
    sla_status: str
    pending_approvals: int


class AgingMetricsResponse(BaseModel):
    metrics: list[AgingMetric]
    total_count: int
    red_count: int
    amber_count: int
    green_count: int
    filters: dict


class SLAThresholds(BaseModel):
    review_days: int = 14
    interview_feedback_days: int = 7
    approval_days: int = 10
    offer_response_days: int = 5


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("/funnel")
async def get_funnel_metrics(
    job_id: str | None = None,
    campaign_id: str | None = None,
    recruiter_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    current_user: CurrentUserDep = Depends(),
    db: Annotated[AsyncSession, Depends(get_db)] = Depends(),
):
    """Get recruitment funnel metrics with conversion rates and time-per-stage."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Parse dates with defaults
    try:
        if date_from:
            df = datetime.fromisoformat(date_from)
        else:
            df = datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if date_to:
            dt = datetime.fromisoformat(date_to)
        else:
            dt = datetime.now()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use ISO 8601.")

    rows = await db.execute(
        text("""
            WITH stage_counts AS (
              SELECT
                a.workflow_status,
                COUNT(*) as stage_count,
                AVG(EXTRACT(EPOCH FROM (COALESCE(a.updated_at, NOW()) -
                  COALESCE(a.created_at, NOW()))) / 86400) as avg_days_in_stage,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                  EXTRACT(EPOCH FROM (COALESCE(a.updated_at, NOW()) -
                  COALESCE(a.created_at, NOW())) / 86400)
                ) as median_days_in_stage
              FROM applications a
              JOIN jobs j ON j.job_id = a.job_id
              WHERE j.tenant_id = CAST(:tid AS uuid)
                AND (COALESCE(:job_id::uuid, NULL) IS NULL OR a.job_id = CAST(:job_id AS uuid))
                AND (COALESCE(:campaign_id::uuid, NULL) IS NULL OR a.campaign_id = CAST(:campaign_id AS uuid))
                AND a.created_at >= :date_from
                AND a.created_at <= :date_to
              GROUP BY a.workflow_status
            )
            SELECT
              workflow_status,
              stage_count,
              ROUND(100.0 * stage_count / SUM(stage_count) OVER (), 2) as percentage_of_pipeline,
              avg_days_in_stage,
              median_days_in_stage,
              ROUND(100.0 * stage_count / LAG(stage_count, 1) OVER (ORDER BY
                CASE workflow_status
                  WHEN 'awaiting_review' THEN 1
                  WHEN 'under_review' THEN 2
                  WHEN 'shortlisted' THEN 3
                  WHEN 'interviewing' THEN 4
                  WHEN 'offer_made' THEN 5
                  WHEN 'hired' THEN 6
                  ELSE 7
                END
              ), 2) as conversion_from_previous
            FROM stage_counts
            ORDER BY
              CASE workflow_status
                WHEN 'awaiting_review' THEN 1
                WHEN 'under_review' THEN 2
                WHEN 'shortlisted' THEN 3
                WHEN 'interviewing' THEN 4
                WHEN 'offer_made' THEN 5
                WHEN 'hired' THEN 6
                ELSE 7
              END
        """),
        {
            "tid": current_user.tenant_id,
            "job_id": job_id,
            "campaign_id": campaign_id,
            "date_from": df,
            "date_to": dt,
        },
    )

    stages = []
    total_count = 0
    for row in rows.mappings():
        r = dict(row)
        metric = FunnelStageMetric(
            workflow_status=r["workflow_status"],
            stage_count=r["stage_count"],
            conversion_from_previous=r["conversion_from_previous"],
            percentage_of_pipeline=r["percentage_of_pipeline"],
            avg_days_in_stage=r["avg_days_in_stage"],
            median_days_in_stage=r["median_days_in_stage"],
        )
        stages.append(metric)
        total_count += r["stage_count"]

    return FunnelMetricsResponse(
        stages=stages,
        total_applications=total_count,
        date_range={"from": df.isoformat(), "to": dt.isoformat()},
        filters={
            "job_id": job_id,
            "campaign_id": campaign_id,
            "recruiter_id": recruiter_id,
        },
    )


@router.get("/recruiter-productivity")
async def get_recruiter_productivity(
    recruiter_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    current_user: CurrentUserDep = Depends(),
    db: Annotated[AsyncSession, Depends(get_db)] = Depends(),
):
    """Get recruiter productivity metrics (reviews, moves, feedback, approvals)."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Parse dates
    try:
        if date_from:
            df = datetime.fromisoformat(date_from)
        else:
            df = (datetime.now() - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        if date_to:
            dt = datetime.fromisoformat(date_to)
        else:
            dt = datetime.now()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use ISO 8601.")

    rows = await db.execute(
        text("""
            SELECT
              u.user_id,
              COALESCE(u.full_name, u.email) as recruiter_name,
              COUNT(DISTINCT a.application_id) as total_applications_assigned,
              COUNT(DISTINCT CASE
                WHEN a.workflow_status IN ('under_review', 'shortlisted')
                THEN a.application_id END) as applications_in_review,
              COUNT(DISTINCT awh.history_id) as workflow_moves_made,
              COUNT(DISTINCT CASE
                WHEN ci.status = 'completed'
                THEN ci.interview_id END) as interviews_completed,
              COUNT(DISTINCT CASE
                WHEN cif.reviewer_id = u.user_id
                THEN cif.feedback_id END) as feedback_provided,
              COUNT(DISTINCT CASE
                WHEN ca.approver_id = u.user_id AND ca.decision != 'pending'
                THEN ca.approval_id END) as approvals_decided,
              AVG(EXTRACT(EPOCH FROM (COALESCE(a.updated_at, NOW()) - a.created_at)) / 86400)
                as avg_days_assigned
            FROM users u
            JOIN applications a ON a.assigned_user_id = u.user_id
            LEFT JOIN application_workflow_history awh ON awh.application_id = a.application_id
            LEFT JOIN candidate_interviews ci ON ci.application_id = a.application_id
            LEFT JOIN candidate_interview_feedback cif ON cif.interview_id = ci.interview_id
            LEFT JOIN candidate_approvals ca ON ca.application_id = a.application_id
            WHERE u.tenant_id = CAST(:tid AS uuid)
              AND (COALESCE(:recruiter_id::uuid, NULL) IS NULL OR u.user_id = CAST(:recruiter_id AS uuid))
              AND a.created_at >= :date_from
              AND a.created_at <= :date_to
            GROUP BY u.user_id, u.full_name, u.email
            ORDER BY workflow_moves_made DESC, interviews_completed DESC
        """),
        {
            "tid": current_user.tenant_id,
            "recruiter_id": recruiter_id,
            "date_from": df,
            "date_to": dt,
        },
    )

    recruiters = []
    for row in rows.mappings():
        r = dict(row)
        metric = RecruiterProductivityMetric(
            user_id=str(r["user_id"]),
            recruiter_name=r["recruiter_name"],
            total_applications_assigned=r["total_applications_assigned"],
            applications_in_review=r["applications_in_review"],
            workflow_moves_made=r["workflow_moves_made"],
            interviews_completed=r["interviews_completed"],
            feedback_provided=r["feedback_provided"],
            approvals_decided=r["approvals_decided"],
            avg_days_assigned=r["avg_days_assigned"],
        )
        recruiters.append(metric)

    return RecruiterProductivityResponse(
        recruiters=recruiters,
        date_range={"from": df.isoformat(), "to": dt.isoformat()},
    )


@router.get("/aging")
async def get_aging_metrics(
    workflow_status: str | None = None,
    days_threshold: int = 0,
    current_user: CurrentUserDep = Depends(),
    db: Annotated[AsyncSession, Depends(get_db)] = Depends(),
):
    """Get SLA aging metrics with breach status (green/amber/red)."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Fetch SLA thresholds
    pol_row = await db.execute(
        text("""
            SELECT policies FROM tenant_workflow_policies
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": current_user.tenant_id},
    )
    pol_rec = pol_row.mappings().first()
    sla_thresholds = {"review_days": 14, "interview_feedback_days": 7, "approval_days": 10, "offer_response_days": 5}
    if pol_rec and pol_rec["policies"]:
        raw = pol_rec["policies"]
        policies = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if "sla_thresholds" in policies:
            sla_thresholds.update(policies["sla_thresholds"])

    # Use review_days as default aging threshold for MVP
    review_days = sla_thresholds.get("review_days", 14)

    rows = await db.execute(
        text("""
            WITH aging_data AS (
              SELECT
                a.application_id,
                a.candidate_name,
                a.job_id,
                j.title as job_title,
                a.workflow_status,
                a.assigned_user_id,
                COALESCE(u.full_name, u.email) as assigned_user_name,
                EXTRACT(EPOCH FROM (NOW() -
                  COALESCE(a.updated_at, a.created_at)
                ) / 86400) as days_in_status,
                COUNT(DISTINCT CASE
                  WHEN ca.decision = 'pending'
                  THEN ca.approval_id END) as pending_approvals
              FROM applications a
              JOIN jobs j ON j.job_id = a.job_id
              LEFT JOIN users u ON u.user_id = a.assigned_user_id
              LEFT JOIN candidate_approvals ca ON ca.application_id = a.application_id
              WHERE j.tenant_id = CAST(:tid AS uuid)
                AND (COALESCE(:workflow_status::varchar, NULL) IS NULL
                     OR a.workflow_status = :workflow_status)
                AND a.workflow_status NOT IN ('hired', 'rejected', 'withdrawn')
              GROUP BY a.application_id, a.candidate_name, a.job_id, j.title,
                       a.workflow_status, a.assigned_user_id, u.full_name, u.email
            )
            SELECT
              application_id,
              candidate_name,
              job_id,
              job_title,
              workflow_status,
              assigned_user_id,
              assigned_user_name,
              days_in_status,
              CASE
                WHEN days_in_status > CAST(:review_days AS numeric) THEN 'red'
                WHEN days_in_status > CAST(:review_days AS numeric) * 0.5 THEN 'amber'
                ELSE 'green'
              END as sla_status,
              pending_approvals
            FROM aging_data
            WHERE days_in_status >= CAST(:days_threshold AS numeric)
            ORDER BY days_in_status DESC, application_id
        """),
        {
            "tid": current_user.tenant_id,
            "workflow_status": workflow_status,
            "days_threshold": days_threshold,
            "review_days": review_days,
        },
    )

    metrics = []
    red_count = 0
    amber_count = 0
    green_count = 0

    for row in rows.mappings():
        r = dict(row)
        metric = AgingMetric(
            application_id=str(r["application_id"]),
            candidate_name=r["candidate_name"],
            job_id=str(r["job_id"]),
            job_title=r["job_title"],
            workflow_status=r["workflow_status"],
            assigned_user_id=str(r["assigned_user_id"]) if r["assigned_user_id"] else None,
            assigned_user_name=r["assigned_user_name"],
            days_in_status=float(r["days_in_status"]),
            sla_status=r["sla_status"],
            pending_approvals=r["pending_approvals"],
        )
        metrics.append(metric)

        if r["sla_status"] == "red":
            red_count += 1
        elif r["sla_status"] == "amber":
            amber_count += 1
        else:
            green_count += 1

    return AgingMetricsResponse(
        metrics=metrics,
        total_count=len(metrics),
        red_count=red_count,
        amber_count=amber_count,
        green_count=green_count,
        filters={"workflow_status": workflow_status, "days_threshold": days_threshold},
    )
