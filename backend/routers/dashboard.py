"""
Dashboard router — operational recruiter workspace KPIs and metrics.

Provides tenant-scoped aggregated data for the recruiter dashboard.
Answers: "What needs my attention now?" and "What is the current workload?"

Authorization:
- All authenticated users can access their tenant's dashboard
- Super admin: full access across all tenants (via RLS bypass)

TODO (when auth model is extended with tenant_type):
- Agency non-admins should be filtered by their assigned clients via
  agency_user_clients table. See campaigns.py _accessible_client_ids()
  for the reference implementation. Currently returns all tenant data
  as safe default because CurrentUser does not yet carry tenant_type.
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
async def get_dashboard_summary(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Dashboard KPI summary for the tenant.

    Returns:
    - Workflow distribution across all 9 statuses
    - Derived totals (total_applications, in_recruitment_process)
    - Failed/blocked processing count (AI scoring failures)
    - Active job/campaign counts
    - Single lightweight call — no pagination, no heavy analytics.

    Scoped to current user's tenant via RLS.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    tenant_filter = "" if is_super_admin else " WHERE j.tenant_id = CAST(:tid AS uuid)"
    tenant_filter_direct = "" if is_super_admin else " WHERE tenant_id = CAST(:tid AS uuid)"

    params: dict = {}
    if not is_super_admin:
        params["tid"] = current_user.tenant_id

    # ── Workflow distribution + total applications ─────────────────────────────
    # IMPORTANT: awaiting_review counts ONLY recruiter-actionable candidates
    # (processing_status = 'ai_scored'). Failed/blocked apps carry
    # workflow_status = 'awaiting_review' as a DB default but must never appear
    # in recruiter queues — they are separated here at the query level.
    apps_row = await db.execute(
        text(f"""
            SELECT
                COUNT(a.application_id)                                                        AS total_applications,
                COUNT(a.application_id) FILTER (
                    WHERE a.workflow_status = 'awaiting_review'
                      AND a.processing_status = 'ai_scored'
                )                                                                              AS awaiting_review,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'under_review')     AS under_review,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'interviewing')     AS interviewing,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'offer_made')       AS offer_made,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'hired')            AS hired,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'rejected')         AS rejected,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'withdrawn')        AS withdrawn,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'on_hold')          AS on_hold,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status IN (
                    'under_review', 'interviewing', 'offer_made'
                ))                                                                             AS in_recruitment_process,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status IN (
                    'under_review', 'interviewing', 'offer_made'
                ))                                                                             AS under_review_total,
                COUNT(a.application_id) FILTER (
                    WHERE a.workflow_status = 'hired'
                      AND DATE_TRUNC('month', a.updated_at) = DATE_TRUNC('month', NOW())
                )                                                                              AS hired_this_month
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            {tenant_filter}
        """),
        params,
    )
    ar = apps_row.mappings().first()

    # ── Failed / blocked AI processing count ──────────────────────────────────
    # Counts ALL system-stopped/failed applications regardless of workflow_status.
    # These are never recruiter-actionable and form their own operational category.
    failed_row = await db.execute(
        text(f"""
            SELECT
                COUNT(*) FILTER (WHERE a.processing_status IN (
                    'failed', 'security_blocked', 'duplicate_blocked',
                    'extraction_failed', 'processing_failed', 'stopped'
                ))                                                        AS failed_or_blocked,
                COUNT(*) FILTER (
                    WHERE a.processing_status = 'security_blocked'
                       OR a.stopped_reason = 'security_blocked'
                )                                                         AS security_warnings,
                COUNT(*) FILTER (
                    WHERE a.duplicate_status IN ('possible_duplicate', 'confirmed_duplicate')
                )                                                         AS possible_duplicates,
                COUNT(*) FILTER (
                    WHERE a.processing_status IN ('extraction_failed', 'processing_failed')
                )                                                         AS validation_issues,
                COUNT(*) FILTER (
                    WHERE a.processing_status = 'failed' AND a.stopped_reason IS NOT NULL
                )                                                         AS stopped_before_ai
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            {tenant_filter}
        """),
        params,
    )
    fr = failed_row.mappings().first()

    # ── Active job count ───────────────────────────────────────────────────────
    jobs_row = await db.execute(
        text(f"""
            SELECT COUNT(*) FILTER (WHERE LOWER(status) = 'active') AS active_jobs
            FROM jobs
            {tenant_filter_direct}
        """),
        params,
    )
    jr = jobs_row.mappings().first()

    # ── Active campaign count ──────────────────────────────────────────────────
    campaigns_row = await db.execute(
        text(f"""
            SELECT COUNT(*) FILTER (WHERE status = 'active') AS active_campaigns
            FROM job_campaigns
            {tenant_filter_direct}
        """),
        params,
    )
    cr = campaigns_row.mappings().first()

    failed_or_blocked = int(fr["failed_or_blocked"]) if fr else 0

    return {
        "kpis": {
            # Workspace overview
            "total_applications":    int(ar["total_applications"])    if ar else 0,
            "in_recruitment_process": int(ar["in_recruitment_process"]) if ar else 0,
            "hired_this_month":      int(ar["hired_this_month"])      if ar else 0,
            "active_jobs":           int(jr["active_jobs"])           if jr else 0,
            "active_campaigns":      int(cr["active_campaigns"])      if cr else 0,
            # Needs attention
            "awaiting_review":       int(ar["awaiting_review"])       if ar else 0,
            "failed_or_blocked":     failed_or_blocked,
            "security_warnings":     int(fr["security_warnings"])     if fr else 0,
            "possible_duplicates":   int(fr["possible_duplicates"])   if fr else 0,
            "validation_issues":     int(fr["validation_issues"])     if fr else 0,
            "stopped_before_ai":     int(fr["stopped_before_ai"])     if fr else 0,
            # Full pipeline breakdown
            "under_review":          int(ar["under_review"])          if ar else 0,
            "interviewing":          int(ar["interviewing"])          if ar else 0,
            "offer_made":            int(ar["offer_made"])            if ar else 0,
            "hired":                 int(ar["hired"])                 if ar else 0,
            "rejected":              int(ar["rejected"])              if ar else 0,
            "withdrawn":             int(ar["withdrawn"])             if ar else 0,
            "on_hold":               int(ar["on_hold"])               if ar else 0,
            # Legacy alias (kept for compatibility)
            "under_review_total":    int(ar["under_review_total"])    if ar else 0,
        },
    }


@router.get("/trends")
async def get_dashboard_trends(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = 30,
):
    """
    Lightweight daily trend series for the Home Dashboard "at a glance" charts.

    Returns per-day counts (oldest → newest) for:
    - applications_received: applications created on that day
    - hires: applications moved to 'hired' on that day (by updated_at)

    Single grouped aggregate query over a bounded date window — no joins beyond
    the tenant scope, no pagination. Scoped to current user's tenant via RLS.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    tenant_filter = "" if is_super_admin else " AND j.tenant_id = CAST(:tid AS uuid)"

    days = max(1, min(days, 90))
    params: dict = {"days": days}
    if not is_super_admin:
        params["tid"] = current_user.tenant_id

    rows = await db.execute(
        text(f"""
            WITH day_series AS (
                SELECT generate_series(
                    CURRENT_DATE - (:days - 1) * INTERVAL '1 day',
                    CURRENT_DATE,
                    INTERVAL '1 day'
                )::date AS day
            ),
            received AS (
                SELECT a.created_at::date AS day, COUNT(*) AS cnt
                FROM applications a
                JOIN jobs j ON j.job_id = a.job_id
                WHERE a.created_at >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
                  {tenant_filter}
                GROUP BY a.created_at::date
            ),
            hired AS (
                SELECT a.updated_at::date AS day, COUNT(*) AS cnt
                FROM applications a
                JOIN jobs j ON j.job_id = a.job_id
                WHERE a.workflow_status = 'hired'
                  AND a.updated_at >= CURRENT_DATE - (:days - 1) * INTERVAL '1 day'
                  {tenant_filter}
                GROUP BY a.updated_at::date
            )
            SELECT
                ds.day,
                COALESCE(received.cnt, 0) AS applications_received,
                COALESCE(hired.cnt, 0)    AS hires
            FROM day_series ds
            LEFT JOIN received ON received.day = ds.day
            LEFT JOIN hired    ON hired.day    = ds.day
            ORDER BY ds.day
        """),
        params,
    )

    series = [
        {
            "date": r["day"].isoformat(),
            "applications_received": int(r["applications_received"]),
            "hires": int(r["hires"]),
        }
        for r in rows.mappings().all()
    ]

    return {
        "days": days,
        "series": series,
        "total_applications": sum(d["applications_received"] for d in series),
        "total_hires": sum(d["hires"] for d in series),
    }
