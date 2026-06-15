import json
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.module_guards import RequireATSManagement
from database import get_db, set_rls_context

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[RequireATSManagement])


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


class RecruitmentInsight(BaseModel):
    insight_id: str
    type: str
    severity: str           # info | warning | critical
    title: str
    description: str
    metric_value: float | None
    threshold: float | None
    suggested_action: str
    related_entity_type: str | None
    related_entity_id: str | None


class InsightsResponse(BaseModel):
    insights: list[RecruitmentInsight]
    generated_at: str


class TimeToHireMetrics(BaseModel):
    avg_days: float | None
    median_days: float | None
    min_days: float | None
    max_days: float | None
    sample_size: int


class FilledJobInfo(BaseModel):
    job_id: str
    job_title: str
    job_code: str | None
    days_to_fill: float


class TimeToFillMetrics(BaseModel):
    avg_days: float | None
    median_days: float | None
    fastest_job: FilledJobInfo | None
    slowest_job: FilledJobInfo | None
    sample_size: int


class ReviewCycleStage(BaseModel):
    workflow_status: str
    avg_days: float | None
    sample_size: int


class LongestOpenJob(BaseModel):
    job_id: str
    job_title: str
    job_code: str | None
    days_open: float
    applications: int
    awaiting_review: int
    under_review: int
    interviewing: int
    hired: int


class RecruiterEfficiencyMetric(BaseModel):
    user_id: str
    recruiter_name: str
    avg_time_to_hire_days: float | None
    hires_completed: int
    candidates_managed: int


class RecruitmentEfficiencyResponse(BaseModel):
    time_to_hire: TimeToHireMetrics
    time_to_fill: TimeToFillMetrics
    review_cycle: list[ReviewCycleStage]
    longest_open_jobs: list[LongestOpenJob]
    recruiter_efficiency: list[RecruiterEfficiencyMetric]
    date_range: dict
    filters: dict


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("/funnel")
async def get_funnel_metrics(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    job_id: str | None = None,
    campaign_id: str | None = None,
    recruiter_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Get recruitment funnel metrics with conversion rates and time-per-stage."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    try:
        df = datetime.fromisoformat(date_from) if date_from else datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        dt = datetime.fromisoformat(date_to) if date_to else datetime.now()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use ISO 8601.")

    # Build optional WHERE fragments — only when values are provided to avoid
    # asyncpg AmbiguousParameterError on nullable params used in IS NULL checks.
    extra_where = ""
    params: dict = {
        "tid":       current_user.tenant_id,
        "date_from": df,
        "date_to":   dt,
    }
    if job_id:
        extra_where += " AND a.job_id = CAST(:job_id AS uuid)"
        params["job_id"] = job_id
    if campaign_id:
        extra_where += " AND a.campaign_id = CAST(:campaign_id AS uuid)"
        params["campaign_id"] = campaign_id

    rows = await db.execute(
        text(f"""
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
                AND a.workflow_status IS NOT NULL
                AND a.created_at >= :date_from
                AND a.created_at <= :date_to
                {extra_where}
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
        params,
    )

    stages = []
    total_count = 0
    for row in rows.mappings():
        r = dict(row)
        stages.append(FunnelStageMetric(
            workflow_status=r["workflow_status"],
            stage_count=r["stage_count"],
            conversion_from_previous=r["conversion_from_previous"],
            percentage_of_pipeline=r["percentage_of_pipeline"],
            avg_days_in_stage=r["avg_days_in_stage"],
            median_days_in_stage=r["median_days_in_stage"],
        ))
        total_count += r["stage_count"]

    return FunnelMetricsResponse(
        stages=stages,
        total_applications=total_count,
        date_range={"from": df.isoformat(), "to": dt.isoformat()},
        filters={"job_id": job_id, "campaign_id": campaign_id, "recruiter_id": recruiter_id},
    )


@router.get("/recruiter-productivity")
async def get_recruiter_productivity(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    recruiter_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Get recruiter productivity metrics (reviews, moves, feedback, approvals)."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    try:
        df = datetime.fromisoformat(date_from) if date_from else (datetime.now() - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        dt = datetime.fromisoformat(date_to) if date_to else datetime.now()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use ISO 8601.")

    extra_where = ""
    params: dict = {
        "tid":       current_user.tenant_id,
        "date_from": df,
        "date_to":   dt,
    }
    if recruiter_id:
        extra_where = " AND u.user_id = CAST(:recruiter_id AS uuid)"
        params["recruiter_id"] = recruiter_id

    rows = await db.execute(
        text(f"""
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
              AND a.workflow_status IS NOT NULL
              AND a.created_at >= :date_from
              AND a.created_at <= :date_to
              {extra_where}
            GROUP BY u.user_id, u.full_name, u.email
            ORDER BY workflow_moves_made DESC, interviews_completed DESC
        """),
        params,
    )

    recruiters = []
    for row in rows.mappings():
        r = dict(row)
        recruiters.append(RecruiterProductivityMetric(
            user_id=str(r["user_id"]),
            recruiter_name=r["recruiter_name"],
            total_applications_assigned=r["total_applications_assigned"],
            applications_in_review=r["applications_in_review"],
            workflow_moves_made=r["workflow_moves_made"],
            interviews_completed=r["interviews_completed"],
            feedback_provided=r["feedback_provided"],
            approvals_decided=r["approvals_decided"],
            avg_days_assigned=r["avg_days_assigned"],
        ))

    return RecruiterProductivityResponse(
        recruiters=recruiters,
        date_range={"from": df.isoformat(), "to": dt.isoformat()},
    )


@router.get("/aging")
async def get_aging_metrics(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    workflow_status: str | None = None,
    days_threshold: int = 0,
):
    """Get SLA aging metrics with breach status (green/amber/red)."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Fetch SLA thresholds
    pol_row = await db.execute(
        text("SELECT policies FROM tenant_workflow_policies WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    pol_rec = pol_row.mappings().first()
    sla_thresholds = {"review_days": 14, "interview_feedback_days": 7, "approval_days": 10, "offer_response_days": 5}
    if pol_rec and pol_rec["policies"]:
        raw = pol_rec["policies"]
        policies = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if "sla_thresholds" in policies:
            sla_thresholds.update(policies["sla_thresholds"])

    review_days = sla_thresholds.get("review_days", 14)

    extra_where = ""
    params: dict = {
        "tid":            current_user.tenant_id,
        "days_threshold": days_threshold,
        "review_days":    review_days,
    }
    if workflow_status:
        extra_where = " AND a.workflow_status = :workflow_status"
        params["workflow_status"] = workflow_status

    rows = await db.execute(
        text(f"""
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
                AND a.workflow_status IS NOT NULL
                AND a.workflow_status NOT IN ('hired', 'rejected', 'withdrawn')
                {extra_where}
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
        params,
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


@router.get("/insights")
async def get_insights(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return rule-based operational recruitment insights derived from SQL aggregations."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Load SLA thresholds from tenant policy (fallback to defaults)
    pol_row = await db.execute(
        text("SELECT policies FROM tenant_workflow_policies WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    pol_rec = pol_row.mappings().first()
    sla = {"review_days": 14, "interview_feedback_days": 7, "approval_days": 10, "offer_response_days": 5}
    if pol_rec and pol_rec["policies"]:
        raw = pol_rec["policies"]
        policies = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if "sla_thresholds" in policies:
            sla.update(policies["sla_thresholds"])

    review_days = sla["review_days"]
    interview_days = sla["interview_feedback_days"]

    # ── Single aggregation query for all rule inputs ────────────────────────
    agg = await db.execute(
        text("""
            SELECT
              COUNT(*) FILTER (WHERE a.workflow_status NOT IN ('hired','rejected','withdrawn'))
                AS active_total,
              COUNT(*) FILTER (WHERE a.workflow_status = 'shortlisted')
                AS shortlisted,
              COUNT(*) FILTER (WHERE a.workflow_status = 'rejected')
                AS rejected,
              COUNT(*) AS grand_total,
              COUNT(*) FILTER (
                WHERE a.workflow_status NOT IN ('hired','rejected','withdrawn')
                  AND EXTRACT(EPOCH FROM (NOW() - COALESCE(a.updated_at, a.created_at))) / 86400
                      > CAST(:review_days AS numeric)
              ) AS sla_overdue,
              COUNT(*) FILTER (
                WHERE a.workflow_status IN ('awaiting_review','under_review')
              ) AS in_review_queue,
              COUNT(*) FILTER (
                WHERE a.workflow_status = 'interviewing'
                  AND EXTRACT(EPOCH FROM (NOW() - COALESCE(a.updated_at, a.created_at))) / 86400
                      > CAST(:interview_days AS numeric)
              ) AS interview_aging
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE j.tenant_id = CAST(:tid AS uuid)
              AND a.workflow_status IS NOT NULL
        """),
        {"tid": current_user.tenant_id, "review_days": review_days, "interview_days": interview_days},
    )
    row = dict(agg.mappings().first() or {})

    active_total   = int(row.get("active_total", 0) or 0)
    shortlisted    = int(row.get("shortlisted", 0) or 0)
    rejected       = int(row.get("rejected", 0) or 0)
    grand_total    = int(row.get("grand_total", 0) or 0)
    sla_overdue    = int(row.get("sla_overdue", 0) or 0)
    in_review_queue = int(row.get("in_review_queue", 0) or 0)
    interview_aging = int(row.get("interview_aging", 0) or 0)

    # Recruiter workload: find max/min assigned among active recruiters
    rec_rows = await db.execute(
        text("""
            SELECT u.user_id, COUNT(a.application_id) AS assigned
            FROM users u
            JOIN applications a ON a.assigned_user_id = u.user_id
            JOIN jobs j ON j.job_id = a.job_id
            WHERE j.tenant_id = CAST(:tid AS uuid)
              AND a.workflow_status IS NOT NULL
              AND a.workflow_status NOT IN ('hired','rejected','withdrawn')
            GROUP BY u.user_id
            HAVING COUNT(a.application_id) > 0
        """),
        {"tid": current_user.tenant_id},
    )
    workloads = [int(r["assigned"]) for r in rec_rows.mappings()]

    insights: list[RecruitmentInsight] = []

    # ── Rule A: SLA Overdue ──────────────────────────────────────────────────
    if sla_overdue > 0:
        insights.append(RecruitmentInsight(
            insight_id="sla_overdue",
            type="sla_bottleneck",
            severity="critical" if sla_overdue >= 5 else "warning",
            title="Overdue Candidates Detected",
            description=f"{sla_overdue} candidate{'s are' if sla_overdue != 1 else ' is'} overdue based on your SLA threshold of {review_days} days.",
            metric_value=float(sla_overdue),
            threshold=float(review_days),
            suggested_action="Review and reassign overdue candidates, or update SLA thresholds in Settings.",
            related_entity_type="sla_status",
            related_entity_id="red",
        ))

    # ── Rule B: Low Shortlist Rate ───────────────────────────────────────────
    SHORTLIST_THRESHOLD = 0.15
    if grand_total >= 10:
        shortlist_rate = shortlisted / grand_total
        if shortlist_rate < SHORTLIST_THRESHOLD:
            insights.append(RecruitmentInsight(
                insight_id="low_shortlist_rate",
                type="low_shortlist_rate",
                severity="warning",
                title="Low Shortlist Rate",
                description=f"Only {shortlist_rate:.1%} of applications have been shortlisted (threshold: {SHORTLIST_THRESHOLD:.0%}).",
                metric_value=round(shortlist_rate * 100, 1),
                threshold=SHORTLIST_THRESHOLD * 100,
                suggested_action="Review job requirements or screening criteria to improve candidate quality.",
                related_entity_type=None,
                related_entity_id=None,
            ))

    # ── Rule C: High Rejection Rate ──────────────────────────────────────────
    REJECTION_THRESHOLD = 0.60
    if grand_total >= 10:
        rejection_rate = rejected / grand_total
        if rejection_rate > REJECTION_THRESHOLD:
            insights.append(RecruitmentInsight(
                insight_id="high_rejection_rate",
                type="high_rejection_rate",
                severity="critical" if rejection_rate > 0.80 else "warning",
                title="High Rejection Rate",
                description=f"{rejection_rate:.1%} of applications have been rejected (threshold: {REJECTION_THRESHOLD:.0%}).",
                metric_value=round(rejection_rate * 100, 1),
                threshold=REJECTION_THRESHOLD * 100,
                suggested_action="Revisit sourcing channels or job descriptions to attract better-fit candidates.",
                related_entity_type=None,
                related_entity_id=None,
            ))

    # ── Rule D: Review Queue Bottleneck ──────────────────────────────────────
    REVIEW_QUEUE_THRESHOLD = 20
    if in_review_queue >= REVIEW_QUEUE_THRESHOLD:
        insights.append(RecruitmentInsight(
            insight_id="review_bottleneck",
            type="review_bottleneck",
            severity="warning" if in_review_queue < 40 else "critical",
            title="Review Queue Building Up",
            description=f"{in_review_queue} candidates are currently in the review queue (awaiting review or under review).",
            metric_value=float(in_review_queue),
            threshold=float(REVIEW_QUEUE_THRESHOLD),
            suggested_action="Assign additional reviewers or prioritise the review queue to avoid SLA breaches.",
            related_entity_type="workflow_status",
            related_entity_id="under_review",
        ))

    # ── Rule E: Interview Bottleneck ─────────────────────────────────────────
    if interview_aging > 0:
        insights.append(RecruitmentInsight(
            insight_id="interview_bottleneck",
            type="interview_bottleneck",
            severity="warning",
            title="Interview Stage Has Aging Candidates",
            description=f"{interview_aging} candidate{'s have' if interview_aging != 1 else ' has'} been in the interview stage for more than {interview_days} days.",
            metric_value=float(interview_aging),
            threshold=float(interview_days),
            suggested_action="Chase interview feedback or reschedule overdue interviews.",
            related_entity_type="workflow_status",
            related_entity_id="interviewing",
        ))

    # ── Rule F: Recruiter Workload Imbalance ─────────────────────────────────
    if len(workloads) >= 2:
        max_load = max(workloads)
        min_load = min(workloads)
        if min_load > 0 and (max_load / min_load) >= 3:
            insights.append(RecruitmentInsight(
                insight_id="workload_imbalance",
                type="workload_imbalance",
                severity="info",
                title="Recruiter Workload Imbalance",
                description=f"The busiest recruiter has {max_load} active candidates vs {min_load} for the least loaded recruiter (ratio {max_load / min_load:.1f}×).",
                metric_value=round(max_load / min_load, 1),
                threshold=3.0,
                suggested_action="Consider redistributing open applications to balance recruiter workload.",
                related_entity_type=None,
                related_entity_id=None,
            ))

    return InsightsResponse(
        insights=insights,
        generated_at=datetime.utcnow().isoformat(),
    )


@router.get("/efficiency")
async def get_recruitment_efficiency(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    job_id: str | None = None,
    campaign_id: str | None = None,
    recruiter_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """
    Recruitment efficiency metrics: time-to-hire, time-to-fill, review-cycle
    durations, longest-open jobs, and recruiter efficiency.

    Built entirely from existing application/job timestamps and
    application_workflow_history — no new business logic, no predictions.
    All aggregation happens server-side; the frontend only renders results.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    try:
        df = datetime.fromisoformat(date_from) if date_from else datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        dt = datetime.fromisoformat(date_to) if date_to else datetime.now()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use ISO 8601.")

    extra_where = ""
    params: dict = {
        "tid":       current_user.tenant_id,
        "date_from": df,
        "date_to":   dt,
    }
    if job_id:
        extra_where += " AND a.job_id = CAST(:job_id AS uuid)"
        params["job_id"] = job_id
    if campaign_id:
        extra_where += " AND a.campaign_id = CAST(:campaign_id AS uuid)"
        params["campaign_id"] = campaign_id
    if recruiter_id:
        extra_where += " AND a.assigned_user_id = CAST(:recruiter_id AS uuid)"
        params["recruiter_id"] = recruiter_id

    # ── 1. Time to Hire: application created_at → first 'hired' transition ────
    tth_row = await db.execute(
        text(f"""
            WITH hire_events AS (
              SELECT
                a.application_id,
                a.created_at AS app_created_at,
                MIN(awh.created_at) AS hired_at
              FROM applications a
              JOIN jobs j ON j.job_id = a.job_id
              JOIN application_workflow_history awh
                ON awh.application_id = a.application_id AND awh.to_status = 'hired'
              WHERE j.tenant_id = CAST(:tid AS uuid)
                AND a.created_at >= :date_from
                AND a.created_at <= :date_to
                {extra_where}
              GROUP BY a.application_id, a.created_at
            ),
            durations AS (
              SELECT EXTRACT(EPOCH FROM (hired_at - app_created_at)) / 86400 AS days_to_hire
              FROM hire_events
            )
            SELECT
              AVG(days_to_hire)                                                   AS avg_days,
              PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_hire)           AS median_days,
              MIN(days_to_hire)                                                   AS min_days,
              MAX(days_to_hire)                                                   AS max_days,
              COUNT(*)                                                            AS sample_size
            FROM durations
        """),
        params,
    )
    tth = tth_row.mappings().first()
    time_to_hire = TimeToHireMetrics(
        avg_days=float(tth["avg_days"]) if tth and tth["avg_days"] is not None else None,
        median_days=float(tth["median_days"]) if tth and tth["median_days"] is not None else None,
        min_days=float(tth["min_days"]) if tth and tth["min_days"] is not None else None,
        max_days=float(tth["max_days"]) if tth and tth["max_days"] is not None else None,
        sample_size=int(tth["sample_size"]) if tth else 0,
    )

    # ── 2. Time to Fill: job created_at (open) → first hire for that job ──────
    job_extra_where = ""
    fill_params: dict = {"tid": current_user.tenant_id}
    if job_id:
        job_extra_where += " AND j.job_id = CAST(:job_id AS uuid)"
        fill_params["job_id"] = job_id
    if campaign_id:
        job_extra_where += " AND a.campaign_id = CAST(:campaign_id AS uuid)"
        fill_params["campaign_id"] = campaign_id

    ttf_rows = await db.execute(
        text(f"""
            WITH job_first_hire AS (
              SELECT a.job_id, MIN(awh.created_at) AS first_hired_at
              FROM applications a
              JOIN jobs j ON j.job_id = a.job_id
              JOIN application_workflow_history awh
                ON awh.application_id = a.application_id AND awh.to_status = 'hired'
              WHERE j.tenant_id = CAST(:tid AS uuid)
                {job_extra_where}
              GROUP BY a.job_id
            )
            SELECT
              j.job_id,
              j.title AS job_title,
              j.job_code,
              EXTRACT(EPOCH FROM (jfh.first_hired_at - j.created_at)) / 86400 AS days_to_fill
            FROM job_first_hire jfh
            JOIN jobs j ON j.job_id = jfh.job_id
        """),
        fill_params,
    )
    fill_durations = [
        {
            "job_id": str(r["job_id"]),
            "job_title": r["job_title"],
            "job_code": r["job_code"],
            "days_to_fill": float(r["days_to_fill"]),
        }
        for r in ttf_rows.mappings().all()
    ]

    if fill_durations:
        days_values = sorted(d["days_to_fill"] for d in fill_durations)
        n = len(days_values)
        avg_fill = sum(days_values) / n
        median_fill = (
            days_values[n // 2] if n % 2 == 1
            else (days_values[n // 2 - 1] + days_values[n // 2]) / 2
        )
        fastest = min(fill_durations, key=lambda d: d["days_to_fill"])
        slowest = max(fill_durations, key=lambda d: d["days_to_fill"])
        time_to_fill = TimeToFillMetrics(
            avg_days=avg_fill,
            median_days=median_fill,
            fastest_job=FilledJobInfo(**fastest),
            slowest_job=FilledJobInfo(**slowest),
            sample_size=n,
        )
    else:
        time_to_fill = TimeToFillMetrics(
            avg_days=None, median_days=None, fastest_job=None, slowest_job=None, sample_size=0,
        )

    # ── 3. Review Cycle: avg days spent in each workflow status ───────────────
    # Duration in a status = time between consecutive workflow_history transitions
    # (open-ended stages use NOW() as the end boundary).
    cycle_rows = await db.execute(
        text(f"""
            WITH ordered_history AS (
              SELECT
                awh.application_id,
                awh.to_status,
                awh.created_at,
                LEAD(awh.created_at) OVER (
                  PARTITION BY awh.application_id ORDER BY awh.created_at
                ) AS next_at
              FROM application_workflow_history awh
              JOIN applications a ON a.application_id = awh.application_id
              JOIN jobs j ON j.job_id = a.job_id
              WHERE j.tenant_id = CAST(:tid AS uuid)
                AND a.created_at >= :date_from
                AND a.created_at <= :date_to
                {extra_where}
            )
            SELECT
              to_status,
              AVG(EXTRACT(EPOCH FROM (COALESCE(next_at, NOW()) - created_at)) / 86400) AS avg_days,
              COUNT(*) AS sample_size
            FROM ordered_history
            WHERE to_status IN ('awaiting_review', 'under_review', 'interviewing', 'offer_made')
            GROUP BY to_status
        """),
        params,
    )
    cycle_by_status = {r["to_status"]: r for r in cycle_rows.mappings().all()}
    review_cycle = [
        ReviewCycleStage(
            workflow_status=status,
            avg_days=float(cycle_by_status[status]["avg_days"]) if status in cycle_by_status and cycle_by_status[status]["avg_days"] is not None else None,
            sample_size=int(cycle_by_status[status]["sample_size"]) if status in cycle_by_status else 0,
        )
        for status in ("awaiting_review", "under_review", "interviewing", "offer_made")
    ]

    # ── 4. Longest Open Jobs: currently-active jobs ranked by days open ───────
    longest_extra_where = ""
    longest_params: dict = {"tid": current_user.tenant_id}
    if job_id:
        longest_extra_where += " AND j.job_id = CAST(:job_id AS uuid)"
        longest_params["job_id"] = job_id

    longest_rows = await db.execute(
        text(f"""
            SELECT
              j.job_id,
              j.title AS job_title,
              j.job_code,
              EXTRACT(EPOCH FROM (NOW() - j.created_at)) / 86400 AS days_open,
              COUNT(a.application_id) AS applications,
              COUNT(*) FILTER (
                WHERE a.workflow_status = 'awaiting_review' AND a.processing_status = 'ai_scored'
              ) AS awaiting_review,
              COUNT(*) FILTER (WHERE a.workflow_status = 'under_review') AS under_review,
              COUNT(*) FILTER (WHERE a.workflow_status = 'interviewing') AS interviewing,
              COUNT(*) FILTER (WHERE a.workflow_status = 'hired')        AS hired
            FROM jobs j
            LEFT JOIN applications a ON a.job_id = j.job_id
            WHERE j.tenant_id = CAST(:tid AS uuid)
              AND j.status = 'active'
              {longest_extra_where}
            GROUP BY j.job_id, j.title, j.job_code, j.created_at
            ORDER BY days_open DESC
            LIMIT 15
        """),
        longest_params,
    )
    longest_open_jobs = [
        LongestOpenJob(
            job_id=str(r["job_id"]),
            job_title=r["job_title"],
            job_code=r["job_code"],
            days_open=float(r["days_open"]),
            applications=int(r["applications"]),
            awaiting_review=int(r["awaiting_review"]),
            under_review=int(r["under_review"]),
            interviewing=int(r["interviewing"]),
            hired=int(r["hired"]),
        )
        for r in longest_rows.mappings().all()
    ]

    # ── 5. Recruiter Efficiency: avg time-to-hire, hires, candidates managed ──
    recruiter_rows = await db.execute(
        text(f"""
            WITH hire_durations AS (
              SELECT
                a2.application_id,
                a2.assigned_user_id,
                EXTRACT(EPOCH FROM (MIN(awh.created_at) - a2.created_at)) / 86400 AS days_to_hire
              FROM applications a2
              JOIN application_workflow_history awh
                ON awh.application_id = a2.application_id AND awh.to_status = 'hired'
              GROUP BY a2.application_id, a2.assigned_user_id
            )
            SELECT
              u.user_id,
              COALESCE(u.full_name, u.email) AS recruiter_name,
              AVG(hd.days_to_hire)                          AS avg_time_to_hire_days,
              COUNT(DISTINCT hd.application_id)             AS hires_completed,
              COUNT(DISTINCT a.application_id)              AS candidates_managed
            FROM users u
            JOIN applications a ON a.assigned_user_id = u.user_id
            JOIN jobs j ON j.job_id = a.job_id
            LEFT JOIN hire_durations hd ON hd.application_id = a.application_id
            WHERE u.tenant_id = CAST(:tid AS uuid)
              AND a.created_at >= :date_from
              AND a.created_at <= :date_to
              {extra_where}
            GROUP BY u.user_id, u.full_name, u.email
            ORDER BY hires_completed DESC, avg_time_to_hire_days ASC NULLS LAST
        """),
        params,
    )
    recruiter_efficiency = [
        RecruiterEfficiencyMetric(
            user_id=str(r["user_id"]),
            recruiter_name=r["recruiter_name"],
            avg_time_to_hire_days=float(r["avg_time_to_hire_days"]) if r["avg_time_to_hire_days"] is not None else None,
            hires_completed=int(r["hires_completed"]),
            candidates_managed=int(r["candidates_managed"]),
        )
        for r in recruiter_rows.mappings().all()
    ]

    return RecruitmentEfficiencyResponse(
        time_to_hire=time_to_hire,
        time_to_fill=time_to_fill,
        review_cycle=review_cycle,
        longest_open_jobs=longest_open_jobs,
        recruiter_efficiency=recruiter_efficiency,
        date_range={"from": df.isoformat(), "to": dt.isoformat()},
        filters={"job_id": job_id, "campaign_id": campaign_id, "recruiter_id": recruiter_id},
    )
