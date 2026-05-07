"""
Tenant-scoped user management endpoints.
Tenant admins can list, create, and activate/deactivate users within their own tenant.

ISOLATION GUARANTEE: every SQL statement that touches the users table carries an
explicit `tenant_id = :tid` predicate.  This is the authoritative enforcement
layer — it does NOT rely on RLS being active or correctly configured.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.password import hash_password
from database import get_db, set_rls_context

router = APIRouter(prefix="/tenant", tags=["tenant"])

# Roles a tenant admin is permitted to assign to new users.
# super_admin is intentionally excluded — only /admin/users can create those.
ALLOWED_ROLES = {"admin", "recruiter", "viewer"}


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateTenantUserRequest(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: str = "recruiter"

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        if v not in ALLOWED_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(ALLOWED_ROLES))}")
        return v


class UpdateUserStatusRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str) -> str:
        if v not in ("active", "disabled"):
            raise ValueError("Status must be 'active' or 'disabled'")
        return v


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_tenant_admin(current_user) -> None:
    """Allow only tenant admins.  Super-admins must use /admin/users instead."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin access required.",
        )


async def _get_active_count_and_limit(tenant_id: str, db) -> tuple[int, int]:
    """Return (active_user_count, max_users) scoped to a specific tenant_id."""
    count_row = await db.execute(
        text("""
            SELECT COUNT(*) FROM users
            WHERE tenant_id = :tid AND status = 'active'
        """),
        {"tid": tenant_id},
    )
    active_count = int(count_row.scalar_one())

    tenant_row = await db.execute(
        text("SELECT max_users FROM tenants WHERE tenant_id = :tid"),
        {"tid": tenant_id},
    )
    tenant = tenant_row.mappings().first()
    max_users = tenant["max_users"] if tenant else 0
    return active_count, max_users


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_tenant_users(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_tenant_admin(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT user_id, email, full_name, role, status, created_at, last_login_at
            FROM users
            WHERE tenant_id = :tid
            ORDER BY created_at ASC
        """),
        {"tid": current_user.tenant_id},
    )
    users = [
        {
            "user_id": str(r["user_id"]),
            "email": r["email"],
            "full_name": r["full_name"],
            "role": r["role"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "last_login_at": r["last_login_at"].isoformat() if r["last_login_at"] else None,
        }
        for r in rows.mappings().all()
    ]

    active_count = sum(1 for u in users if u["status"] == "active")
    _, max_users = await _get_active_count_and_limit(current_user.tenant_id, db)

    return {
        "success": True,
        "users": users,
        "active_count": active_count,
        "max_users": max_users,
    }


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_tenant_user(
    body: CreateTenantUserRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_tenant_admin(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    active_count, max_users = await _get_active_count_and_limit(current_user.tenant_id, db)
    if active_count >= max_users:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="User limit reached for your current plan.",
        )

    password_hash = hash_password(body.password)

    try:
        # tenant_id is always taken from the JWT — never from the request body.
        user_result = await db.execute(
            text("""
                INSERT INTO users (tenant_id, email, password_hash, full_name, role, status)
                VALUES (:tid, :email, :pw, :name, :role, 'active')
                RETURNING user_id, email, full_name, role, status, created_at
            """),
            {
                "tid": current_user.tenant_id,
                "email": body.email,
                "pw": password_hash,
                "name": body.full_name,
                "role": body.role,
            },
        )
        user = user_result.mappings().first()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists.",
            )
        raise

    return {
        "success": True,
        "user": {
            "user_id": str(user["user_id"]),
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "status": user["status"],
            "created_at": user["created_at"].isoformat() if user["created_at"] else None,
            "last_login_at": None,
        },
    }


@router.patch("/users/{user_id}/status")
async def update_tenant_user_status(
    user_id: str,
    body: UpdateUserStatusRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_tenant_admin(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # tenant_id = :tid ensures a cross-tenant user_id returns 404, not the real row.
    user_row = await db.execute(
        text("""
            SELECT user_id, role, status FROM users
            WHERE user_id = :uid AND tenant_id = :tid
        """),
        {"uid": user_id, "tid": current_user.tenant_id},
    )
    user = user_row.mappings().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user_id == current_user.user_id and body.status == "disabled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    if body.status == "active" and user["status"] != "active":
        active_count, max_users = await _get_active_count_and_limit(current_user.tenant_id, db)
        if active_count >= max_users:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="User limit reached for your current plan.",
            )

    # tenant_id = :tid in the UPDATE prevents cross-tenant writes even if user_id
    # somehow matched a row from another tenant (e.g., UUID collision).
    await db.execute(
        text("""
            UPDATE users SET status = :status
            WHERE user_id = :uid AND tenant_id = :tid
        """),
        {"status": body.status, "uid": user_id, "tid": current_user.tenant_id},
    )
    await db.commit()

    action = "activated" if body.status == "active" else "deactivated"
    return {"success": True, "message": f"User {action} successfully."}
