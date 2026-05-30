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

    params = {"tid": current_user.tenant_id}

    # ── Workflow distribution + total applications ─────────────────────────────
    apps_row = await db.execute(
        text("""
            SELECT
                COUNT(a.application_id)                                                        AS total_applications,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'awaiting_review')  AS awaiting_review,
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
            WHERE j.tenant_id = CAST(:tid AS uuid)
        """),
        params,
    )
    ar = apps_row.mappings().first()

    # ── Failed / blocked AI processing count ──────────────────────────────────
    # "Needs attention": applications where AI scoring failed or is stuck
    failed_row = await db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE a.processing_status = 'failed') AS failed,
                COUNT(*) FILTER (
                    WHERE a.processing_status IN ('queued', 'processing')
                      AND a.updated_at < NOW() - INTERVAL '30 minutes'
                )                                                        AS stuck
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE j.tenant_id = CAST(:tid AS uuid)
        """),
        params,
    )
    fr = failed_row.mappings().first()

    # ── Active job count ───────────────────────────────────────────────────────
    jobs_row = await db.execute(
        text("""
            SELECT COUNT(*) FILTER (WHERE LOWER(status) = 'active') AS active_jobs
            FROM jobs
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        params,
    )
    jr = jobs_row.mappings().first()

    # ── Active campaign count ──────────────────────────────────────────────────
    campaigns_row = await db.execute(
        text("""
            SELECT COUNT(*) FILTER (WHERE status = 'active') AS active_campaigns
            FROM job_campaigns
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        params,
    )
    cr = campaigns_row.mappings().first()

    failed_count  = int(fr["failed"]) if fr else 0
    stuck_count   = int(fr["stuck"])  if fr else 0
    failed_or_blocked = failed_count + stuck_count

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
