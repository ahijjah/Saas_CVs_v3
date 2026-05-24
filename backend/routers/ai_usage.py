"""
AI Usage & Cost Tracking — super_admin only endpoints.

All endpoints require super_admin role.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from auth.dependencies import require_role

router = APIRouter(prefix="/admin/ai-usage", tags=["ai-usage"])

RequireSuperAdmin = Depends(require_role("super_admin"))

# ── Helpers ────────────────────────────────────────────────────────────────────

def _date_filter(col: str, from_date: date | None, to_date: date | None) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if from_date:
        clauses.append(f"{col} >= :from_date")
        params["from_date"] = from_date
    if to_date:
        clauses.append(f"{col} < :to_date + interval '1 day'")
        params["to_date"] = to_date
    return (" AND ".join(clauses), params)


def _build_where(
    *,
    tenant_id: str | None,
    job_id: str | None,
    application_id: str | None,
    model: str | None,
    stage: str | None,
    request_status: str | None,
    from_date: date | None,
    to_date: date | None,
) -> tuple[str, dict]:
    parts: list[str] = []
    params: dict[str, Any] = {}

    if tenant_id:
        parts.append("tenant_id = :tenant_id")
        params["tenant_id"] = tenant_id
    if job_id:
        parts.append("job_id = :job_id")
        params["job_id"] = job_id
    if application_id:
        parts.append("application_id = :application_id")
        params["application_id"] = application_id
    if model:
        parts.append("model = :model")
        params["model"] = model
    if stage:
        parts.append("stage = :stage")
        params["stage"] = stage
    if request_status:
        parts.append("request_status = :request_status")
        params["request_status"] = request_status

    date_clause, date_params = _date_filter("created_at", from_date, to_date)
    if date_clause:
        parts.append(date_clause)
        params.update(date_params)

    where = ("WHERE " + " AND ".join(parts)) if parts else ""
    return where, params


# ── Overview ───────────────────────────────────────────────────────────────────

@router.get("/overview", dependencies=[RequireSuperAdmin])
async def get_overview(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    tenant_id: str | None = Query(None),
    model: str | None = Query(None),
    stage: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    where, params = _build_where(
        tenant_id=tenant_id, job_id=None, application_id=None,
        model=model, stage=stage, request_status=None,
        from_date=from_date, to_date=to_date,
    )
    row = (await db.execute(text(f"""
        SELECT
            COUNT(*)                                                      AS total_calls,
            COALESCE(SUM(total_tokens), 0)                                AS total_tokens,
            COALESCE(SUM(prompt_tokens), 0)                               AS total_prompt_tokens,
            COALESCE(SUM(completion_tokens), 0)                           AS total_completion_tokens,
            COALESCE(SUM(estimated_cost_usd), 0)                          AS total_cost_usd,
            COUNT(*) FILTER (WHERE request_status = 'failed')             AS failed_calls,
            COUNT(*) FILTER (WHERE request_status = 'success')            AS successful_calls,
            COALESCE(AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL), 0) AS avg_latency_ms,
            COALESCE(AVG(estimated_cost_usd) FILTER (WHERE estimated_cost_usd IS NOT NULL), 0)
                                                                          AS avg_cost_per_call,
            COUNT(DISTINCT application_id)                                AS unique_applications,
            COUNT(DISTINCT tenant_id)                                     AS unique_tenants
        FROM cv_analyzer.ai_usage_log
        {where}
    """), params)).mappings().first()

    return {
        "total_calls":           int(row["total_calls"]),
        "total_tokens":          int(row["total_tokens"]),
        "total_prompt_tokens":   int(row["total_prompt_tokens"]),
        "total_completion_tokens": int(row["total_completion_tokens"]),
        "total_cost_usd":        float(row["total_cost_usd"]),
        "failed_calls":          int(row["failed_calls"]),
        "successful_calls":      int(row["successful_calls"]),
        "avg_latency_ms":        float(row["avg_latency_ms"]),
        "avg_cost_per_call":     float(row["avg_cost_per_call"]),
        "unique_applications":   int(row["unique_applications"]),
        "unique_tenants":        int(row["unique_tenants"]),
    }


# ── By Tenant ─────────────────────────────────────────────────────────────────

@router.get("/by-tenant", dependencies=[RequireSuperAdmin])
async def get_by_tenant(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    model: str | None = Query(None),
    stage: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    where, params = _build_where(
        tenant_id=None, job_id=None, application_id=None,
        model=model, stage=stage, request_status=None,
        from_date=from_date, to_date=to_date,
    )
    rows = (await db.execute(text(f"""
        SELECT
            u.tenant_id,
            t.name                                AS tenant_name,
            COUNT(*)                              AS total_calls,
            COALESCE(SUM(u.total_tokens), 0)      AS total_tokens,
            COALESCE(SUM(u.estimated_cost_usd), 0) AS total_cost_usd,
            COUNT(*) FILTER (WHERE u.request_status = 'failed') AS failed_calls,
            COALESCE(AVG(u.latency_ms) FILTER (WHERE u.latency_ms IS NOT NULL), 0) AS avg_latency_ms,
            COUNT(DISTINCT u.application_id)      AS unique_applications
        FROM cv_analyzer.ai_usage_log u
        LEFT JOIN cv_analyzer.tenants t ON t.tenant_id = u.tenant_id
        {where}
        GROUP BY u.tenant_id, t.name
        ORDER BY total_cost_usd DESC
        LIMIT 200
    """), params)).mappings().all()

    return [
        {
            "tenant_id":           str(r["tenant_id"]) if r["tenant_id"] else None,
            "tenant_name":         r["tenant_name"],
            "total_calls":         int(r["total_calls"]),
            "total_tokens":        int(r["total_tokens"]),
            "total_cost_usd":      float(r["total_cost_usd"]),
            "failed_calls":        int(r["failed_calls"]),
            "avg_latency_ms":      float(r["avg_latency_ms"]),
            "unique_applications": int(r["unique_applications"]),
        }
        for r in rows
    ]


# ── By Job ─────────────────────────────────────────────────────────────────────

@router.get("/by-job", dependencies=[RequireSuperAdmin])
async def get_by_job(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    tenant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    where, params = _build_where(
        tenant_id=tenant_id, job_id=None, application_id=None,
        model=None, stage=None, request_status=None,
        from_date=from_date, to_date=to_date,
    )
    rows = (await db.execute(text(f"""
        SELECT
            u.job_id,
            j.title                               AS job_title,
            t.name                                AS tenant_name,
            COUNT(*)                              AS total_calls,
            COALESCE(SUM(u.total_tokens), 0)      AS total_tokens,
            COALESCE(SUM(u.estimated_cost_usd), 0) AS total_cost_usd,
            COUNT(DISTINCT u.application_id)      AS unique_applications,
            COUNT(*) FILTER (WHERE u.request_status = 'failed') AS failed_calls
        FROM cv_analyzer.ai_usage_log u
        LEFT JOIN cv_analyzer.jobs j ON j.job_id = u.job_id
        LEFT JOIN cv_analyzer.tenants t ON t.tenant_id = u.tenant_id
        {where}
        GROUP BY u.job_id, j.title, t.name
        ORDER BY total_cost_usd DESC
        LIMIT 200
    """), params)).mappings().all()

    return [
        {
            "job_id":              str(r["job_id"]) if r["job_id"] else None,
            "job_title":           r["job_title"],
            "tenant_name":         r["tenant_name"],
            "total_calls":         int(r["total_calls"]),
            "total_tokens":        int(r["total_tokens"]),
            "total_cost_usd":      float(r["total_cost_usd"]),
            "unique_applications": int(r["unique_applications"]),
            "failed_calls":        int(r["failed_calls"]),
        }
        for r in rows
    ]


# ── By Model ───────────────────────────────────────────────────────────────────

@router.get("/by-model", dependencies=[RequireSuperAdmin])
async def get_by_model(
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    date_clause, params = _date_filter("created_at", from_date, to_date)
    where = ("WHERE " + date_clause) if date_clause else ""
    rows = (await db.execute(text(f"""
        SELECT
            provider,
            model,
            COUNT(*)                              AS total_calls,
            COALESCE(SUM(total_tokens), 0)        AS total_tokens,
            COALESCE(SUM(prompt_tokens), 0)       AS total_prompt_tokens,
            COALESCE(SUM(completion_tokens), 0)   AS total_completion_tokens,
            COALESCE(SUM(estimated_cost_usd), 0)  AS total_cost_usd,
            COUNT(*) FILTER (WHERE request_status = 'failed') AS failed_calls,
            COALESCE(AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL), 0) AS avg_latency_ms
        FROM cv_analyzer.ai_usage_log
        {where}
        GROUP BY provider, model
        ORDER BY total_cost_usd DESC
    """), params)).mappings().all()

    return [
        {
            "provider":              r["provider"],
            "model":                 r["model"],
            "total_calls":           int(r["total_calls"]),
            "total_tokens":          int(r["total_tokens"]),
            "total_prompt_tokens":   int(r["total_prompt_tokens"]),
            "total_completion_tokens": int(r["total_completion_tokens"]),
            "total_cost_usd":        float(r["total_cost_usd"]),
            "failed_calls":          int(r["failed_calls"]),
            "avg_latency_ms":        float(r["avg_latency_ms"]),
        }
        for r in rows
    ]


# ── Detailed Logs (paginated) ──────────────────────────────────────────────────

@router.get("/logs", dependencies=[RequireSuperAdmin])
async def get_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    tenant_id: str | None = Query(None),
    job_id: str | None = Query(None),
    application_id: str | None = Query(None),
    stage: str | None = Query(None),
    model: str | None = Query(None),
    request_status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    where, params = _build_where(
        tenant_id=tenant_id, job_id=job_id, application_id=application_id,
        model=model, stage=stage, request_status=request_status,
        from_date=from_date, to_date=to_date,
    )
    offset = (page - 1) * limit
    params["limit"] = limit
    params["offset"] = offset

    total_row = (await db.execute(text(f"""
        SELECT COUNT(*) AS cnt FROM cv_analyzer.ai_usage_log {where}
    """), params)).mappings().first()
    total = int(total_row["cnt"])

    rows = (await db.execute(text(f"""
        SELECT
            usage_id, tenant_id, job_id, application_id,
            stage, provider, model, prompt_key,
            prompt_tokens, completion_tokens, total_tokens,
            input_price_per_1m_tokens, output_price_per_1m_tokens,
            estimated_cost_usd,
            request_status, latency_ms, retry_count,
            error_type, error_message, metadata, created_at
        FROM cv_analyzer.ai_usage_log
        {where}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """), params)).mappings().all()

    return {
        "total":   total,
        "page":    page,
        "limit":   limit,
        "pages":   (total + limit - 1) // limit,
        "results": [
            {
                "usage_id":                  str(r["usage_id"]),
                "tenant_id":                 str(r["tenant_id"]) if r["tenant_id"] else None,
                "job_id":                    str(r["job_id"]) if r["job_id"] else None,
                "application_id":            str(r["application_id"]) if r["application_id"] else None,
                "stage":                     r["stage"],
                "provider":                  r["provider"],
                "model":                     r["model"],
                "prompt_key":                r["prompt_key"],
                "prompt_tokens":             r["prompt_tokens"],
                "completion_tokens":         r["completion_tokens"],
                "total_tokens":              r["total_tokens"],
                "input_price_per_1m_tokens": float(r["input_price_per_1m_tokens"]) if r["input_price_per_1m_tokens"] is not None else None,
                "output_price_per_1m_tokens": float(r["output_price_per_1m_tokens"]) if r["output_price_per_1m_tokens"] is not None else None,
                "estimated_cost_usd":        float(r["estimated_cost_usd"]) if r["estimated_cost_usd"] is not None else None,
                "request_status":            r["request_status"],
                "latency_ms":                r["latency_ms"],
                "retry_count":               r["retry_count"],
                "error_type":                r["error_type"],
                "error_message":             r["error_message"],
                "metadata":                  r["metadata"],
                "created_at":                r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }


# ── Pricing Config ─────────────────────────────────────────────────────────────

@router.get("/pricing", dependencies=[RequireSuperAdmin])
async def get_pricing(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT pricing_id, provider, model,
               input_price_per_1m_tokens, output_price_per_1m_tokens,
               active, created_at, updated_at
        FROM cv_analyzer.ai_model_pricing
        ORDER BY provider, model
    """))).mappings().all()

    return [
        {
            "pricing_id":               str(r["pricing_id"]),
            "provider":                 r["provider"],
            "model":                    r["model"],
            "input_price_per_1m_tokens":  float(r["input_price_per_1m_tokens"]),
            "output_price_per_1m_tokens": float(r["output_price_per_1m_tokens"]),
            "active":                   r["active"],
            "created_at":               r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at":               r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]


@router.put("/pricing/{pricing_id}", dependencies=[RequireSuperAdmin])
async def update_pricing(
    pricing_id: str,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    allowed = {"input_price_per_1m_tokens", "output_price_per_1m_tokens", "active"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="No valid fields to update")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["pricing_id"] = pricing_id

    await db.execute(text(f"""
        UPDATE cv_analyzer.ai_model_pricing
        SET {set_clause}, updated_at = now()
        WHERE pricing_id = :pricing_id
    """), updates)
    await db.commit()
    return {"ok": True}
