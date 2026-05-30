"""
Dashboard router — operational recruiter workspace KPIs and metrics.

Provides tenant-scoped aggregated data for the recruiter dashboard.
Answers: "What needs my attention now?"

Authorization:
- All authenticated users can access their tenant's dashboard
- Super admin: full access across all tenants (via RLS bypass)
- Agency non-admins: filtered by assigned clients (via agency_user_clients)
"""
import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def _accessible_client_ids(current_user, db: AsyncSession) -> list[str] | None:
    """
    Return list of client org IDs accessible by this user.
    - None = org tenant or admin (all clients accessible)
    - ['id1', 'id2', ...] = agency non-admin (assigned clients only)
    """
    tenant_type = current_user.tenant_type or 'organization'
    role = (current_user.role or '').lower()

    if tenant_type == 'organization' or role == 'admin':
        return None

    # Agency non-admin: fetch assigned clients
    result = await db.execute(
        text("""
            SELECT client_organization_id::text
            FROM agency_user_clients
            WHERE user_id = CAST(:uid AS uuid) AND tenant_id = CAST(:tid AS uuid)
        """),
        {"uid": current_user.user_id, "tid": current_user.tenant_id},
    )
    return [str(row[0]) for row in result.fetchall()]


@router.get("/summary")
async def get_dashboard_summary(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Dashboard KPI summary for the tenant.

    Returns workflow distribution (all 9 statuses), active job/campaign counts,
    and plan usage. Single call for all dashboard metrics.

    Respects tenant RLS and client scoping (for agencies).
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    client_ids = await _accessible_client_ids(current_user, db)

    # Build client scoping filter (if needed)
    client_filter = ""
    params = {"tid": current_user.tenant_id}

    if client_ids is not None:  # Agency non-admin
        if not client_ids:
            # Non-admin agency user with no assigned clients
            return {
                "kpis": {
                    "awaiting_review": 0,
                    "under_review": 0,
                    "interviewing": 0,
                    "offer_made": 0,
                    "hired": 0,
                    "rejected": 0,
                    "withdrawn": 0,
                    "on_hold": 0,
                    "under_review_total": 0,
                    "hired_this_month": 0,
                    "active_jobs": 0,
                    "active_campaigns": 0,
                },
                "plan_usage": {
                    "max_campaigns": None,
                    "active_campaigns": 0,
                    "max_processed_cvs": None,
                    "processed_cvs_this_month": 0,
                },
            }
        client_filter = f"AND j.client_organization_id IN ({', '.join([f'CAST(\\'{cid}\\' AS uuid)' for cid in client_ids])})"

    # ── Workflow distribution ──────────────────────────────────────────────────
    apps_row = await db.execute(
        text(f"""
            SELECT
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'awaiting_review') AS awaiting_review,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'under_review') AS under_review,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'interviewing') AS interviewing,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'offer_made') AS offer_made,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'hired') AS hired,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'rejected') AS rejected,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'withdrawn') AS withdrawn,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'on_hold') AS on_hold,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status IN ('under_review', 'interviewing', 'offer_made')) AS under_review_total,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'hired' AND DATE_TRUNC('month', a.updated_at) = DATE_TRUNC('month', NOW())) AS hired_this_month
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE j.tenant_id = CAST(:tid AS uuid)
            {client_filter}
        """),
        params,
    )
    ar = apps_row.mappings().first()

    # ── Job and campaign counts ────────────────────────────────────────────────
    jobs_row = await db.execute(
        text(f"""
            SELECT COUNT(*) FILTER (WHERE LOWER(status) = 'active') AS active_jobs
            FROM jobs
            WHERE tenant_id = CAST(:tid AS uuid)
            {client_filter}
        """),
        params,
    )
    jr = jobs_row.mappings().first()

    campaigns_row = await db.execute(
        text("""
            SELECT COUNT(*) FILTER (WHERE status = 'active') AS active_campaigns
            FROM job_campaigns
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": current_user.tenant_id},
    )
    cr = campaigns_row.mappings().first()

    return {
        "kpis": {
            "awaiting_review": int(ar["awaiting_review"]) if ar else 0,
            "under_review": int(ar["under_review"]) if ar else 0,
            "interviewing": int(ar["interviewing"]) if ar else 0,
            "offer_made": int(ar["offer_made"]) if ar else 0,
            "hired": int(ar["hired"]) if ar else 0,
            "rejected": int(ar["rejected"]) if ar else 0,
            "withdrawn": int(ar["withdrawn"]) if ar else 0,
            "on_hold": int(ar["on_hold"]) if ar else 0,
            "under_review_total": int(ar["under_review_total"]) if ar else 0,
            "hired_this_month": int(ar["hired_this_month"]) if ar else 0,
            "active_jobs": int(jr["active_jobs"]) if jr else 0,
            "active_campaigns": int(cr["active_campaigns"]) if cr else 0,
        },
        "plan_usage": {
            "max_campaigns": None,
            "active_campaigns": int(cr["active_campaigns"]) if cr else 0,
            "max_processed_cvs": None,
            "processed_cvs_this_month": 0,
        },
    }
