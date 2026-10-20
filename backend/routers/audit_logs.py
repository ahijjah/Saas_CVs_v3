"""
Audit Logs — a tenant admin reads their own tenant's records; a super admin reads across tenants.

Provides paginated, filterable access to the audit_logs table.
Read-only endpoint: no mutation operations.

The runtime tenant login (cv_app) has no SELECT on audit_logs, so both callers read through the administration session. That session
is opened only after the caller's role is verified (admin or super_admin), and for an admin the tenant is taken from the verified
user, never from the request: the query always carries `al.tenant_id = <the caller's tenant>`, and a tenant_id query parameter that
names another tenant is refused with 403 instead of being used.
"""
import logging
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUser
from auth.roles import ADMIN_OR_PLATFORM_ROLES, SUPER_ADMIN, require_roles
from database import AdminSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/audit-logs", tags=["audit-logs"])

_PAGE_SIZE = 50


async def get_audit_db(
    current_user: Annotated[CurrentUser, Depends(require_roles(ADMIN_OR_PLATFORM_ROLES))],
) -> AsyncGenerator[AsyncSession, None]:
    """The administration session for the audit log, opened only for a verified tenant admin or super admin."""
    async with AdminSessionLocal() as session:
        yield session


@router.get("")
async def list_audit_logs(
    current_user: Annotated[CurrentUser, Depends(require_roles(ADMIN_OR_PLATFORM_ROLES))],
    db: Annotated[AsyncSession, Depends(get_audit_db)],
    page: int = Query(1, ge=1),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    tenant_id: str | None = Query(None),
    user_email: str | None = Query(None),
):

    filters: list[str] = []
    params: dict = {"limit": _PAGE_SIZE, "offset": (page - 1) * _PAGE_SIZE}

    if current_user.role != SUPER_ADMIN:
        # A tenant admin: the scope is the verified user's own tenant. A different tenant_id is refused, not honoured.
        if tenant_id and str(tenant_id).strip().lower() != str(current_user.tenant_id).lower():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        tenant_id = str(current_user.tenant_id)

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
