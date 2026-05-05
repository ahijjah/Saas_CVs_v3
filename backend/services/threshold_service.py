"""Three-level threshold cascade: system → tenant → job."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SYSTEM_DEFAULTS = {"qualified": 85, "partial": 65}


async def get_thresholds(
    db: AsyncSession,
    tenant_id: str,
    job_id: str | None = None,
) -> tuple[int, int]:
    """Return (qualified_threshold, partial_threshold) applying cascade logic."""
    # System defaults
    q_default = _SYSTEM_DEFAULTS["qualified"]
    p_default = _SYSTEM_DEFAULTS["partial"]

    try:
        row = await db.execute(
            text("SELECT key, value FROM system_config WHERE key IN ('default_qualified_threshold','default_partial_threshold')")
        )
        for r in row.mappings():
            if r["key"] == "default_qualified_threshold":
                q_default = int(r["value"])
            elif r["key"] == "default_partial_threshold":
                p_default = int(r["value"])
    except Exception:
        pass

    q_thresh = q_default
    p_thresh = p_default

    # Tenant override
    row = await db.execute(
        text("SELECT qualified_threshold, partial_threshold FROM tenants WHERE tenant_id = :tid"),
        {"tid": tenant_id},
    )
    tenant = row.mappings().first()
    if tenant:
        if tenant["qualified_threshold"] is not None:
            q_thresh = tenant["qualified_threshold"]
        if tenant["partial_threshold"] is not None:
            p_thresh = tenant["partial_threshold"]

    # Job override
    if job_id:
        row = await db.execute(
            text("SELECT qualified_threshold, partial_threshold FROM jobs WHERE job_id = :jid"),
            {"jid": job_id},
        )
        job = row.mappings().first()
        if job:
            if job["qualified_threshold"] is not None:
                q_thresh = job["qualified_threshold"]
            if job["partial_threshold"] is not None:
                p_thresh = job["partial_threshold"]

    return q_thresh, p_thresh
