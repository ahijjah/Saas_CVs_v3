"""
Centralized subscription quota helpers.

All enforcement points (manual upload, public apply, create job, email intake)
call these helpers so limit logic lives in one place.

Rolling 30-day window: COUNT applications where scored_at (or created_at) >= now() - 30 days.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _get_cv_limit(tenant_id: str, db: AsyncSession) -> int:
    """Return max_processed_cvs_per_month from the active subscription plan (0 = unlimited)."""
    row = await db.execute(
        text("""
            SELECT sp.max_processed_cvs_per_month
            FROM tenants t
            LEFT JOIN subscription_plans sp ON sp.plan_code = t.plan AND sp.status = 'active'
            WHERE t.tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": tenant_id},
    )
    r = row.mappings().first()
    if r is None:
        return 0
    v = r["max_processed_cvs_per_month"]
    return int(v) if v is not None else 0


async def _get_campaign_limit(tenant_id: str, db: AsyncSession) -> int:
    """Return max_campaigns from plan, falling back to tenants.max_jobs (0 = unlimited)."""
    row = await db.execute(
        text("""
            SELECT sp.max_campaigns, t.max_jobs
            FROM tenants t
            LEFT JOIN subscription_plans sp ON sp.plan_code = t.plan AND sp.status = 'active'
            WHERE t.tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": tenant_id},
    )
    r = row.mappings().first()
    if r is None:
        return 0
    if r["max_campaigns"] is not None:
        return int(r["max_campaigns"])
    return int(r["max_jobs"] or 0)


async def _count_rolling_cvs(tenant_id: str, db: AsyncSession) -> int:
    """Count applications processed in the last 30 rolling days (tenant-wide)."""
    row = await db.execute(
        text("""
            SELECT COUNT(*) FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE j.tenant_id = CAST(:tid AS uuid)
              AND a.processing_status = 'scored'
              AND COALESCE(a.scored_at, a.created_at) >= now() - INTERVAL '30 days'
        """),
        {"tid": tenant_id},
    )
    return int(row.scalar_one())


async def _count_active_campaigns(tenant_id: str, db: AsyncSession) -> int:
    """Count concurrently active campaigns (status = 'Active' only)."""
    row = await db.execute(
        text("""
            SELECT COUNT(*) FROM jobs
            WHERE tenant_id = CAST(:tid AS uuid) AND status = 'Active'
        """),
        {"tid": tenant_id},
    )
    return int(row.scalar_one())


async def can_process_cv(tenant_id: str, db: AsyncSession) -> dict:
    """
    Returns {"allowed": bool, "used": int, "limit": int, "message": str}.
    allowed=True when limit == 0 (unlimited) or used < limit.
    """
    limit = await _get_cv_limit(tenant_id, db)
    if limit == 0:
        return {"allowed": True, "used": 0, "limit": 0, "message": ""}

    used = await _count_rolling_cvs(tenant_id, db)
    if used >= limit:
        return {
            "allowed": False,
            "used": used,
            "limit": limit,
            "message": (
                f"CV processing limit reached ({used}/{limit} in the last 30 days). "
                "Upgrade your plan to process more CVs."
            ),
        }
    return {"allowed": True, "used": used, "limit": limit, "message": ""}


async def can_create_campaign(tenant_id: str, db: AsyncSession) -> dict:
    """
    Returns {"allowed": bool, "used": int, "limit": int, "message": str}.
    allowed=True when limit == 0 (unlimited) or used < limit.
    """
    limit = await _get_campaign_limit(tenant_id, db)
    if limit == 0:
        return {"allowed": True, "used": 0, "limit": 0, "message": ""}

    used = await _count_active_campaigns(tenant_id, db)
    if used >= limit:
        return {
            "allowed": False,
            "used": used,
            "limit": limit,
            "message": (
                f"Campaign limit reached ({used}/{limit} active campaigns). "
                "Close an existing campaign or upgrade your plan to create more."
            ),
        }
    return {"allowed": True, "used": used, "limit": limit, "message": ""}
