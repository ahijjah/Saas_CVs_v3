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
ALLOWED_ROLES = {"admin", "hr_manager", "recruiter", "viewer"}


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


class UpdateUserProfileRequest(BaseModel):
    """Admin can update a team member's display name and role. Email/password are excluded."""
    full_name: str | None = None
    role: str | None = None

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(ALLOWED_ROLES))}")
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


@router.patch("/users/{user_id}")
async def update_tenant_user_profile(
    user_id: str,
    body: UpdateUserProfileRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Admin can update a team member's name and/or role. Email and password are immutable here."""
    _require_tenant_admin(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    user_row = await db.execute(
        text("SELECT user_id, role FROM users WHERE user_id = :uid AND tenant_id = :tid"),
        {"uid": user_id, "tid": current_user.tenant_id},
    )
    user = user_row.mappings().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    updates: dict = {}
    if body.full_name is not None:
        updates["full_name"] = body.full_name
    if body.role is not None:
        # Prevent demoting the only remaining admin to a non-admin role
        if user["role"] == "admin" and body.role != "admin":
            admin_count = await db.execute(
                text("SELECT COUNT(*) FROM users WHERE tenant_id = :tid AND role = 'admin' AND status = 'active'"),
                {"tid": current_user.tenant_id},
            )
            if int(admin_count.scalar_one()) <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot change the role of the last active admin.",
                )
        updates["role"] = body.role

    if not updates:
        return {"success": True, "message": "No changes to apply."}

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["uid"] = user_id
    updates["tid"] = current_user.tenant_id
    await db.execute(
        text(f"UPDATE users SET {set_clauses} WHERE user_id = :uid AND tenant_id = :tid"),
        updates,
    )
    await db.commit()
    return {"success": True, "message": "User updated successfully."}


# ── Tenant self-service: usage & plan info ────────────────────────────────────

@router.get("/usage")
async def get_tenant_usage(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return current tenant's usage vs plan limits + plan features. Admin and HR Manager."""
    if current_user.role not in ("admin", "hr_manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin or HR Manager access required",
        )
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    tenant_row = await db.execute(
        text("""
            SELECT t.tenant_id, t.name, t.plan, t.max_users, t.max_jobs,
                   t.subscription_status, t.trial_end_at,
                   t.subscription_started_at, t.subscription_ends_at,
                   t.tenant_type,
                   t.monthly_cv_processing_soft_limit,
                   t.monthly_cv_processing_hard_limit
            FROM tenants t
            WHERE t.tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": current_user.tenant_id},
    )
    tenant = tenant_row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    plan_row = await db.execute(
        text("""
            SELECT plan_id, plan_name, max_campaigns, max_processed_cvs_per_month, max_users
            FROM subscription_plans WHERE plan_code = :code
        """),
        {"code": tenant["plan"]},
    )
    plan = plan_row.mappings().first()

    limits = {
        "max_campaigns": plan["max_campaigns"] if plan else (tenant["max_jobs"] or 5),
        "max_users": plan["max_users"] if plan else (tenant["max_users"] or 3),
        "max_processed_cvs_per_month": plan["max_processed_cvs_per_month"] if plan else 500,
    }

    usage_row = await db.execute(
        text("""
            SELECT
                (SELECT COUNT(*) FROM jobs
                 WHERE tenant_id = CAST(:tid AS uuid) AND status = 'Active') AS active_campaigns,
                (SELECT COUNT(*) FROM users
                 WHERE tenant_id = CAST(:tid AS uuid) AND status = 'active') AS active_users,
                (SELECT COUNT(*) FROM applications a
                 JOIN jobs j ON j.job_id = a.job_id
                 WHERE j.tenant_id = CAST(:tid AS uuid)
                   AND a.processing_status = 'scored'
                   AND COALESCE(a.scored_at, a.created_at) >= now() - INTERVAL '30 days') AS cvs_processed_rolling
        """),
        {"tid": current_user.tenant_id},
    )
    usage_data = usage_row.mappings().first()
    usage = {
        "active_campaigns": int(usage_data["active_campaigns"]),
        "active_users":     int(usage_data["active_users"]),
        "processed_cvs_this_month": int(usage_data["cvs_processed_rolling"]),
    }

    def pct(used: int, limit: int) -> float:
        return round(min(used / limit * 100, 100), 1) if limit > 0 else 0.0

    # Plan features from plan_features table (empty list if no seed yet)
    features: list[dict] = []
    if plan and plan["plan_id"]:
        feat_rows = await db.execute(
            text("""
                SELECT feature_key, feature_name, description, value_type,
                       value_boolean, value_number, value_text, display_order
                FROM plan_features
                WHERE plan_id = CAST(:pid AS uuid)
                ORDER BY display_order, feature_key
            """),
            {"pid": str(plan["plan_id"])},
        )
        features = [
            {
                "feature_key":  r["feature_key"],
                "feature_name": r["feature_name"],
                "description":  r["description"],
                "value_type":   r["value_type"],
                "value_boolean": r["value_boolean"],
                "value_number": float(r["value_number"]) if r["value_number"] is not None else None,
                "value_text":   r["value_text"],
                "display_order": r["display_order"],
            }
            for r in feat_rows.mappings()
        ]

    cv_pct   = pct(usage["processed_cvs_this_month"], limits["max_processed_cvs_per_month"])
    camp_pct = pct(usage["active_campaigns"],         limits["max_campaigns"])

    return {
        "plan_code":   tenant["plan"],
        "plan_name":   plan["plan_name"] if plan else tenant["plan"],
        "tenant_type": tenant["tenant_type"] or "organization",
        "subscription_status": tenant["subscription_status"] or "trial",
        "trial_end_at": tenant["trial_end_at"].isoformat() if tenant["trial_end_at"] else None,
        "subscription_started_at": (
            tenant["subscription_started_at"].isoformat()
            if tenant["subscription_started_at"] else None
        ),
        "subscription_ends_at": (
            tenant["subscription_ends_at"].isoformat()
            if tenant["subscription_ends_at"] else None
        ),
        "limits": limits,
        "usage": usage,
        "percentage_used": {
            "campaigns": camp_pct,
            "users":     pct(usage["active_users"], limits["max_users"]),
            "cvs":       cv_pct,
        },
        "at_limit": {
            "campaigns": limits["max_campaigns"] > 0 and usage["active_campaigns"] >= limits["max_campaigns"],
            "cvs":       limits["max_processed_cvs_per_month"] > 0 and usage["processed_cvs_this_month"] >= limits["max_processed_cvs_per_month"],
        },
        "near_limit": {
            "campaigns": camp_pct >= 80,
            "cvs":       cv_pct >= 80,
        },
        "features": features,
    }


# ── Plan limits ───────────────────────────────────────────────────────────────

_PLAN_LIMITS: dict[str, dict] = {
    "trial": {
        "max_users": 2, "max_jobs": 3, "max_clients": 1,
        "api_access_enabled": False, "branding_level": "none",
    },
    "starter": {
        "max_users": 3, "max_jobs": 10, "max_clients": 1,
        "api_access_enabled": False, "branding_level": "none",
    },
    "professional": {
        "max_users": 10, "max_jobs": 50, "max_clients": 20,
        "api_access_enabled": False, "branding_level": "basic",
    },
    "enterprise": {
        "max_users": 9999, "max_jobs": 9999, "max_clients": None,  # NULL = unlimited
        "api_access_enabled": True, "branding_level": "white_label",
    },
}


@router.post("/subscription/activate-trial")
async def activate_trial(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_tenant_admin(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    tenant_row = await db.execute(
        text("SELECT subscription_status, email_domain FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    tenant = tenant_row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if tenant["subscription_status"] != "pending_plan_selection":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trial can only be activated for accounts pending plan selection.",
        )

    # Anti-abuse: one trial per email domain
    if tenant["email_domain"]:
        await set_rls_context(db, "", "super_admin")
        abuse_row = await db.execute(
            text("""
                SELECT COUNT(*) FROM tenants
                WHERE email_domain = :domain
                  AND tenant_id != CAST(:tid AS uuid)
                  AND subscription_status IN ('trial', 'active', 'expired', 'suspended')
            """),
            {"domain": tenant["email_domain"], "tid": current_user.tenant_id},
        )
        if int(abuse_row.scalar_one()) > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A trial has already been used for this email domain.",
            )
        await set_rls_context(db, current_user.tenant_id, current_user.role)

    lim = _PLAN_LIMITS["trial"]
    await db.execute(
        text("""
            UPDATE tenants
            SET plan                = 'trial',
                pending_plan        = NULL,
                subscription_status = 'trial',
                trial_start_at      = now(),
                trial_end_at        = now() + INTERVAL '14 days',
                max_users           = :max_users,
                max_jobs            = :max_jobs,
                max_clients         = :max_clients,
                api_access_enabled  = :api_access,
                branding_level      = :branding
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        {
            "max_users": lim["max_users"],
            "max_jobs":  lim["max_jobs"],
            "max_clients": lim["max_clients"],
            "api_access":  lim["api_access_enabled"],
            "branding":    lim["branding_level"],
            "tid":         current_user.tenant_id,
        },
    )
    await db.commit()
    return {"success": True, "message": "Free trial activated. Enjoy your 14-day trial!"}


class SelectPlanRequest(BaseModel):
    plan: str

    @field_validator("plan")
    @classmethod
    def valid_plan(cls, v: str) -> str:
        if v not in ("starter", "professional", "enterprise"):
            raise ValueError("plan must be one of: starter, professional, enterprise")
        return v


@router.post("/subscription/select-plan")
async def select_plan(
    body: SelectPlanRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_tenant_admin(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    tenant_row = await db.execute(
        text("SELECT subscription_status FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    tenant = tenant_row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if body.plan == "enterprise":
        new_status = "pending_sales_contact"
        message = "Your enterprise enquiry has been received. Our sales team will contact you shortly."
    else:
        new_status = "pending_payment"
        message = "Plan selected. Complete payment to activate your subscription."

    # Store the chosen plan as pending_plan only — plan and limits are set on payment success.
    await db.execute(
        text("""
            UPDATE tenants
            SET pending_plan        = :pending_plan,
                subscription_status = :sub_status
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        {
            "pending_plan": body.plan,
            "sub_status":   new_status,
            "tid":          current_user.tenant_id,
        },
    )
    await db.commit()
    return {"success": True, "subscription_status": new_status, "message": message}


class SimulatePaymentRequest(BaseModel):
    plan: str
    billing_cycle: str = "monthly"
    result: str

    @field_validator("plan")
    @classmethod
    def valid_plan(cls, v: str) -> str:
        if v not in ("starter", "professional"):
            raise ValueError("plan must be starter or professional")
        return v

    @field_validator("billing_cycle")
    @classmethod
    def valid_billing_cycle(cls, v: str) -> str:
        if v not in ("monthly", "yearly"):
            raise ValueError("billing_cycle must be monthly or yearly")
        return v

    @field_validator("result")
    @classmethod
    def valid_result(cls, v: str) -> str:
        if v not in ("success", "failed"):
            raise ValueError("result must be success or failed")
        return v


@router.post("/subscription/simulate-payment")
async def simulate_payment(
    body: SimulatePaymentRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_tenant_admin(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    tenant_row = await db.execute(
        text("SELECT subscription_status, pending_plan FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    tenant = tenant_row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if tenant["subscription_status"] != "pending_payment":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Simulated payment is only available when subscription_status is pending_payment.",
        )

    if tenant["pending_plan"] and body.plan != tenant["pending_plan"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Requested plan '{body.plan}' does not match selected plan '{tenant['pending_plan']}'.",
        )

    if body.result == "failed":
        return {
            "success": False,
            "subscription_status": "pending_payment",
            "message": "Payment failed or was cancelled. Please try again or choose a different plan.",
        }

    lim = _PLAN_LIMITS[body.plan]
    ends_interval = "1 year" if body.billing_cycle == "yearly" else "1 month"

    result_row = await db.execute(
        text(f"""
            UPDATE tenants
            SET plan                    = :plan,
                pending_plan            = NULL,
                subscription_status     = 'active',
                billing_cycle           = :billing_cycle,
                subscription_started_at = now(),
                subscription_ends_at    = now() + INTERVAL '{ends_interval}',
                max_users               = :max_users,
                max_jobs                = :max_jobs,
                max_clients             = :max_clients,
                api_access_enabled      = :api_access,
                branding_level          = :branding
            WHERE tenant_id = CAST(:tid AS uuid)
            RETURNING subscription_ends_at
        """),
        {
            "plan":          body.plan,
            "billing_cycle": body.billing_cycle,
            "max_users":     lim["max_users"],
            "max_jobs":      lim["max_jobs"],
            "max_clients":   lim["max_clients"],
            "api_access":    lim["api_access_enabled"],
            "branding":      lim["branding_level"],
            "tid":           current_user.tenant_id,
        },
    )
    row = result_row.mappings().first()
    await db.commit()
    return {
        "success": True,
        "subscription_status": "active",
        "plan": body.plan,
        "billing_cycle": body.billing_cycle,
        "subscription_ends_at": row["subscription_ends_at"].isoformat() if row and row["subscription_ends_at"] else None,
        "message": f"Payment successful! Your {body.plan.title()} plan ({body.billing_cycle}) is now active.",
    }
