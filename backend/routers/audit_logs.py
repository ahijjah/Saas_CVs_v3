"""
Audit Logs — Super Admin only.

Provides paginated, filterable access to the audit_logs table.
Read-only endpoint: no mutation operations.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin
from database import get_db, set_rls_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/audit-logs", tags=["audit-logs"])

_PAGE_SIZE = 50


@router.get("", dependencies=[RequireSuperAdmin])
async def list_audit_logs(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    tenant_id: str | None = Query(None),
    user_email: str | None = Query(None),
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    filters: list[str] = []
    params: dict = {"limit": _PAGE_SIZE, "offset": (page - 1) * _PAGE_SIZE}

    if action:
        filters.append("al.action ILIKE :action")
        params["action"] = f"%{action}%"
    if resource_type:
        filters.append("al.resource_type = :resource_type")
        params["resource_type"] = resource_type
    if tenant_id:
        filters.append("al.tenant_id = :filter_tenant_id")
        params["filter_tenant_id"] = tenant_id
    if user_email:
        filters.append("al.user_email ILIKE :user_email")
        params["user_email"] = f"%{user_email}%"

    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

    rows = await db.execute(
        text(f"""
            SELECT
                al.log_id, al.tenant_id, al.user_id, al.user_email,
                al.action, al.resource_type, al.resource_id,
                al.details, al.ip_address, al.created_at,
                t.name AS tenant_name
            FROM audit_logs al
            LEFT JOIN tenants t ON t.tenant_id = al.tenant_id
            {where_clause}
            ORDER BY al.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    count_row = await db.execute(
        text(f"SELECT COUNT(*) FROM audit_logs al {where_clause}"),
        count_params,
    )
    total = count_row.scalar_one()

    logs = []
    for r in rows.mappings():
        logs.append({
            "log_id":        str(r["log_id"]),
            "tenant_id":     str(r["tenant_id"]) if r["tenant_id"] else None,
            "tenant_name":   r["tenant_name"] or "",
            "user_id":       str(r["user_id"]) if r["user_id"] else None,
            "user_email":    r["user_email"] or "",
            "action":        r["action"],
            "resource_type": r["resource_type"] or "",
            "resource_id":   r["resource_id"] or "",
            "details":       r["details"],
            "ip_address":    r["ip_address"] or "",
            "created_at":    r["created_at"].isoformat() if r["created_at"] else None,
        })

    return {
        "success": True,
        "logs":    logs,
        "total":   total,
        "page":    page,
        "pages":   max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE),
    }
