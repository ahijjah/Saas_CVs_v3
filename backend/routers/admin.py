import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin, get_current_user
from auth.password import hash_password
from config import get_settings
from database import get_db, set_rls_context
from services.email_service import send_invite_email

router = APIRouter(prefix="/admin", tags=["admin"])
settings = get_settings()


# ── Schemas ──────────────────────────────────────────────────────────────────

class CreateTenantRequest(BaseModel):
    name: str
    email_domain: str
    plan: str = "starter"
    max_users: int = 3
    max_jobs: int = 10


class UpdateUserStatusRequest(BaseModel):
    user_id: str
    status: str

    def validate_status(self) -> None:
        if self.status not in ("active", "disabled"):
            raise ValueError("status must be 'active' or 'disabled'")


class UpdateTenantStatusRequest(BaseModel):
    tenant_id: str
    status: str

    def validate_status(self) -> None:
        if self.status not in ("active", "suspended"):
            raise ValueError("status must be 'active' or 'suspended'")


class InviteUserRequest(BaseModel):
    email: EmailStr
    role: str = "recruiter"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/dashboard", dependencies=[RequireSuperAdmin])
async def admin_dashboard(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    stats_row = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM tenants WHERE status = 'active')   AS active_tenants,
            (SELECT COUNT(*) FROM tenants)                           AS total_tenants,
            (SELECT COUNT(*) FROM users WHERE status = 'active')     AS active_users,
            (SELECT COUNT(*) FROM users)                             AS total_users,
            (SELECT COUNT(*) FROM jobs WHERE status = 'active')      AS active_jobs,
            (SELECT COUNT(*) FROM jobs)                              AS total_jobs,
            (SELECT COUNT(*) FROM applications)                      AS total_applications,
            (SELECT COUNT(*) FROM email_ingest_log)                  AS total_emails_ingested
    """))
    stats = dict(stats_row.mappings().first())

    tenant_rows = await db.execute(text("""
        SELECT
            t.tenant_id, t.name, t.plan, t.status, t.created_at,
            COUNT(DISTINCT u.user_id)   AS user_count,
            COUNT(DISTINCT j.job_id)    AS job_count,
            COUNT(DISTINCT a.application_id) AS application_count
        FROM tenants t
        LEFT JOIN users u ON u.tenant_id = t.tenant_id
        LEFT JOIN jobs j ON j.tenant_id = t.tenant_id
        LEFT JOIN applications a ON a.tenant_id = t.tenant_id
        GROUP BY t.tenant_id
        ORDER BY t.created_at DESC
        LIMIT 10
    """))
    tenants = []
    for r in tenant_rows.mappings():
        tenants.append({
            "tenant_id": str(r["tenant_id"]),
            "tenant_name": r["name"],
            "plan": r["plan"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "user_count": r["user_count"],
            "job_count": r["job_count"],
            "application_count": r["application_count"],
        })

    return {"overview": stats, "tenants": tenants}


@router.get("/tenants", dependencies=[RequireSuperAdmin])
async def list_tenants(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    status_filter: str | None = None,
    current_user: CurrentUserDep = Depends(get_current_user),
    db: Annotated[AsyncSession, Depends(get_db)] = Depends(get_db),
):
    limit = min(limit, 100)
    offset = (page - 1) * limit
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    where_clauses = []
    params: dict = {"limit": limit, "offset": offset}

    if search:
        where_clauses.append("(t.name ILIKE :search OR t.email_domain ILIKE :search)")
        params["search"] = f"%{search}%"
    if status_filter:
        where_clauses.append("t.status = :status_filter")
        params["status_filter"] = status_filter

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    count_row = await db.execute(
        text(f"SELECT COUNT(*) FROM tenants t {where_sql}"), params
    )
    total = count_row.scalar_one()

    rows = await db.execute(
        text(f"""
            SELECT t.tenant_id, t.name, t.email_domain, t.plan,
                   t.max_users, t.max_jobs, t.status, t.created_at,
                   COUNT(DISTINCT u.user_id) AS user_count
            FROM tenants t
            LEFT JOIN users u ON u.tenant_id = t.tenant_id
            {where_sql}
            GROUP BY t.tenant_id
            ORDER BY t.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    tenants = []
    for r in rows.mappings():
        tenants.append({
            "tenant_id": str(r["tenant_id"]),
            "tenant_name": r["name"],
            "email_domain": r["email_domain"],
            "plan": r["plan"],
            "max_users": r["max_users"],
            "max_jobs": r["max_jobs"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "user_count": r["user_count"],
        })

    return {"tenants": tenants, "total": total, "page": page, "limit": limit}


@router.post("/tenants", status_code=status.HTTP_201_CREATED, dependencies=[RequireSuperAdmin])
async def create_tenant(
    body: CreateTenantRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    result = await db.execute(
        text("""
            INSERT INTO tenants (name, email_domain, plan, max_users, max_jobs, status)
            VALUES (:name, :domain, :plan, :max_users, :max_jobs, 'active')
            RETURNING tenant_id
        """),
        {
            "name": body.name,
            "domain": body.email_domain,
            "plan": body.plan,
            "max_users": body.max_users,
            "max_jobs": body.max_jobs,
        },
    )
    tenant_id = str(result.scalar_one())
    await db.commit()
    return {"success": True, "tenant_id": tenant_id}


@router.patch("/tenants/status", dependencies=[RequireSuperAdmin])
async def update_tenant_status(
    body: UpdateTenantStatusRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.status not in ("active", "suspended"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status")

    await set_rls_context(db, current_user.tenant_id, "super_admin")

    await db.execute(
        text("UPDATE tenants SET status = :status WHERE tenant_id = :tid"),
        {"status": body.status, "tid": body.tenant_id},
    )

    # Cascade: suspending a tenant disables all its users
    if body.status == "suspended":
        await db.execute(
            text("UPDATE users SET status = 'disabled' WHERE tenant_id = :tid"),
            {"tid": body.tenant_id},
        )

    await db.commit()
    return {"success": True, "message": f"Tenant {body.status}"}


@router.get("/users", dependencies=[RequireSuperAdmin])
async def list_users(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    role: str | None = None,
    status_filter: str | None = None,
    tenant_id: str | None = None,
    current_user: CurrentUserDep = Depends(get_current_user),
    db: Annotated[AsyncSession, Depends(get_db)] = Depends(get_db),
):
    limit = min(limit, 100)
    offset = (page - 1) * limit
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    where_clauses: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if search:
        where_clauses.append("(u.email ILIKE :search OR u.full_name ILIKE :search)")
        params["search"] = f"%{search}%"
    if role:
        where_clauses.append("u.role = :role")
        params["role"] = role
    if status_filter:
        where_clauses.append("u.status = :status_filter")
        params["status_filter"] = status_filter
    if tenant_id:
        where_clauses.append("u.tenant_id = :tenant_id")
        params["tenant_id"] = tenant_id

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    count_row = await db.execute(text(f"SELECT COUNT(*) FROM users u {where_sql}"), params)
    total = count_row.scalar_one()

    rows = await db.execute(
        text(f"""
            SELECT u.user_id, u.email, u.full_name, u.role, u.status,
                   u.last_login_at, u.created_at,
                   t.tenant_id, t.name AS tenant_name
            FROM users u
            JOIN tenants t ON t.tenant_id = u.tenant_id
            {where_sql}
            ORDER BY u.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    users = []
    for r in rows.mappings():
        users.append({
            "user_id": str(r["user_id"]),
            "email": r["email"],
            "full_name": r["full_name"],
            "role": r["role"],
            "status": r["status"],
            "last_login_at": r["last_login_at"].isoformat() if r["last_login_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "tenant_id": str(r["tenant_id"]),
            "tenant_name": r["tenant_name"],
        })

    return {"users": users, "total": total, "page": page, "limit": limit}


@router.post("/users", status_code=status.HTTP_201_CREATED, dependencies=[RequireSuperAdmin])
async def create_user(
    tenant_id: str,
    body: InviteUserRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    # Check email uniqueness
    existing = await db.execute(
        text("SELECT user_id FROM users WHERE email = :email"), {"email": body.email}
    )
    if existing.first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

    await db.execute(
        text("""
            INSERT INTO user_invites (tenant_id, invited_by, email, role, token_hash, expires_at)
            VALUES (:tid, :inviter, :email, :role, :token_hash, :expires_at)
        """),
        {
            "tid": tenant_id,
            "inviter": current_user.user_id,
            "email": body.email,
            "role": body.role,
            "token_hash": token_hash,
            "expires_at": expires_at,
        },
    )

    # Fetch tenant info for invite email
    t_row = await db.execute(
        text("SELECT name FROM tenants WHERE tenant_id = :tid"), {"tid": tenant_id}
    )
    tenant = t_row.mappings().first()

    await db.commit()

    invite_link = f"{settings.app_base_url}/accept-invite?token={token}"
    await send_invite_email(
        to_email=body.email,
        inviter_name=current_user.full_name,
        tenant_name=tenant["name"] if tenant else "CV Analyzer",
        invite_link=invite_link,
    )

    return {"success": True, "message": "Invitation sent"}


@router.patch("/users/status")
async def update_user_status(
    body: UpdateUserStatusRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.status not in ("active", "disabled"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status")

    # Admins can manage their own tenant's users; super_admin can manage all
    if current_user.role not in ("super_admin", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    # Cannot disable yourself
    if body.user_id == current_user.user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change your own status")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    result = await db.execute(
        text("UPDATE users SET status = :status WHERE user_id = :uid RETURNING user_id"),
        {"status": body.status, "uid": body.user_id},
    )
    if not result.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await db.commit()
    return {"success": True, "message": f"User {body.status}"}
