"""
Centralized subscription quota helpers.

All enforcement points (manual upload, public apply, create job, email intake)
call these helpers so limit logic lives in one place.

Rolling 30-day window: COUNT applications where scored_at (or created_at) >= now() - 30 days.
Monthly calendar window (for Fair Usage Policy): uses tenant_usage_monthly table.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ── Plan-level limit helpers ───────────────────────────────────────────────────

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


# ── Fair Usage Policy (tenant-level hard/soft limits) ─────────────────────────

async def get_monthly_usage(tenant_id: str, db: AsyncSession) -> dict:
    """
    Return current calendar-month usage for this tenant.
    Returns {"cvs_processed": int, "soft_limit": int|None, "hard_limit": int|None}.
    """
    row = await db.execute(
        text("""
            SELECT
                t.monthly_cv_processing_soft_limit,
                t.monthly_cv_processing_hard_limit,
                COALESCE(m.cvs_processed, 0) AS cvs_processed
            FROM tenants t
            LEFT JOIN tenant_usage_monthly m
                ON m.tenant_id = t.tenant_id
                AND m.usage_month = DATE_TRUNC('month', CURRENT_DATE)::date
            WHERE t.tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": tenant_id},
    )
    r = row.mappings().first()
    if not r:
        return {"cvs_processed": 0, "soft_limit": None, "hard_limit": None}
    return {
        "cvs_processed": int(r["cvs_processed"]),
        "soft_limit":    int(r["monthly_cv_processing_soft_limit"]) if r["monthly_cv_processing_soft_limit"] is not None else None,
        "hard_limit":    int(r["monthly_cv_processing_hard_limit"]) if r["monthly_cv_processing_hard_limit"] is not None else None,
    }


async def check_hard_cv_limit(tenant_id: str, db: AsyncSession) -> dict:
    """
    Check whether the tenant's internal hard monthly CV processing limit is reached.
    Returns {"allowed": bool, "used": int, "limit": int|None, "message": str}.
    """
    usage = await get_monthly_usage(tenant_id, db)
    hard = usage["hard_limit"]
    used = usage["cvs_processed"]

    if hard is None:
        return {"allowed": True, "used": used, "limit": None, "message": ""}

    if used >= hard:
        return {
            "allowed": False,
            "used": used,
            "limit": hard,
            "message": (
                f"Monthly CV processing limit reached ({used}/{hard} this month). "
                "Processing has been paused. Please contact support."
            ),
        }
    return {"allowed": True, "used": used, "limit": hard, "message": ""}


async def is_near_soft_limit(tenant_id: str, db: AsyncSession) -> bool:
    """
    Return True if the tenant has reached or exceeded their soft monthly CV limit.
    Does NOT block processing — for monitoring/alerting only.
    """
    usage = await get_monthly_usage(tenant_id, db)
    soft = usage["soft_limit"]
    if soft is None:
        return False
    return usage["cvs_processed"] >= soft


async def increment_monthly_usage(tenant_id: str, db: AsyncSession) -> None:
    """
    Increment cvs_processed counter for the current calendar month.
    Uses UPSERT so the row is created if it doesn't exist yet.
    Callers must call db.commit() themselves — this function does not commit.
    """
    await db.execute(
        text("""
            INSERT INTO tenant_usage_monthly (tenant_id, usage_month, cvs_processed)
            VALUES (
                CAST(:tid AS uuid),
                DATE_TRUNC('month', CURRENT_DATE)::date,
                1
            )
            ON CONFLICT (tenant_id, usage_month)
            DO UPDATE SET
                cvs_processed = tenant_usage_monthly.cvs_processed + 1,
                updated_at    = now()
        """),
        {"tid": tenant_id},
    )


# ── Public API ─────────────────────────────────────────────────────────────────

async def can_process_cv(tenant_id: str, db: AsyncSession) -> dict:
    """
    Returns {"allowed": bool, "used": int, "limit": int, "message": str}.
    Checks BOTH the subscription plan rolling-30-day limit AND the tenant-level
    hard monthly cap (whichever is stricter).
    allowed=True when both checks pass (or limits are 0/None = unlimited).
    """
    # 1. Tenant-level hard cap (calendar month, highest priority)
    hard_check = await check_hard_cv_limit(tenant_id, db)
    if not hard_check["allowed"]:
        return {
            "allowed": False,
            "used":    hard_check["used"],
            "limit":   hard_check["limit"] or 0,
            "message": hard_check["message"],
        }

    # 2. Plan-level rolling 30-day limit
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
