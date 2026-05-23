import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin
from auth.password import hash_password
from config import get_settings
from database import get_db, set_rls_context
from services.email_service import send_invite_email

router = APIRouter(prefix="/admin", tags=["admin"])
settings = get_settings()


class CreateTenantRequest(BaseModel):
    name: str
    email_domain: str
    plan: str = "starter"
    max_users: int = 3
    max_jobs: int = 10
    max_clients: int | None = None          # NULL = unlimited
    api_access_enabled: bool = False
    branding_level: str = "none"
    tenant_type: str = "organization"  # organization | agency | individual_recruiter
    monthly_cv_processing_soft_limit: int | None = None
    monthly_cv_processing_hard_limit: int | None = None
    # First admin user (all three required together or all omitted)
    admin_full_name: str | None = None
    admin_email: EmailStr | None = None
    admin_password: str | None = None


class UpdateUserStatusRequest(BaseModel):
    user_id: str
    status: str


class UpdateTenantStatusRequest(BaseModel):
    tenant_id: str
    status: str


class UpdateTenantSubscriptionRequest(BaseModel):
    action: str          # assign_plan | extend_trial | suspend | reactivate
    plan_code: str | None = None
    trial_end_at: str | None = None   # ISO date string for extend_trial


class InviteUserRequest(BaseModel):
    email: EmailStr
    role: str = "recruiter"


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
            COUNT(DISTINCT u.user_id) AS user_count,
            COUNT(DISTINCT j.job_id) AS job_count,
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
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    status_filter: str | None = None,
):
    limit = min(limit, 100)
    offset = (page - 1) * limit
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    where_clauses = []
    params = {"limit": limit, "offset": offset}

    if search:
        where_clauses.append("(t.name ILIKE :search OR t.email_domain ILIKE :search)")
        params["search"] = f"%{search}%"

    if status_filter:
        where_clauses.append("t.status = :status_filter")
        params["status_filter"] = status_filter

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    count_row = await db.execute(
        text(f"SELECT COUNT(*) FROM tenants t {where_sql}"),
        params,
    )
    total = count_row.scalar_one()

    rows = await db.execute(
        text(f"""
            SELECT t.tenant_id, t.name, t.email_domain, t.plan, t.pending_plan,
                   t.max_users, t.max_jobs, t.status, t.created_at,
                   t.tenant_type, t.subscription_status,
                   t.trial_end_at, t.subscription_started_at, t.subscription_ends_at,
                   t.monthly_cv_processing_soft_limit,
                   t.monthly_cv_processing_hard_limit,
                   t.job_application_controls_enabled,
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
            "tenant_id":   str(r["tenant_id"]),
            "tenant_name": r["name"],
            "email_domain": r["email_domain"],
            "plan":        r["plan"],
            "pending_plan": r["pending_plan"],
            "max_users":   r["max_users"],
            "max_jobs":    r["max_jobs"],
            "status":      r["status"],
            "tenant_type": r["tenant_type"] or "organization",
            "subscription_status": r["subscription_status"],
            "trial_end_at": r["trial_end_at"].isoformat() if r["trial_end_at"] else None,
            "subscription_started_at": r["subscription_started_at"].isoformat() if r["subscription_started_at"] else None,
            "subscription_ends_at": r["subscription_ends_at"].isoformat() if r["subscription_ends_at"] else None,
            "monthly_cv_processing_soft_limit": r["monthly_cv_processing_soft_limit"],
            "monthly_cv_processing_hard_limit": r["monthly_cv_processing_hard_limit"],
            "job_application_controls_enabled": bool(r["job_application_controls_enabled"]),
            "created_at":  r["created_at"].isoformat() if r["created_at"] else None,
            "user_count":  r["user_count"],
        })

    return {"tenants": tenants, "total": total, "page": page, "limit": limit}


@router.post("/tenants", status_code=status.HTTP_201_CREATED, dependencies=[RequireSuperAdmin])
async def create_tenant(
    body: CreateTenantRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    valid_types = ("organization", "agency", "individual_recruiter")
    if body.tenant_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"tenant_type must be one of: {', '.join(valid_types)}",
        )

    # All three admin fields must be provided together or all omitted
    admin_fields = (body.admin_full_name, body.admin_email, body.admin_password)
    has_admin = any(f is not None for f in admin_fields)
    if has_admin and not all(f for f in admin_fields):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="admin_full_name, admin_email, and admin_password must all be provided together.",
        )
    if body.admin_password and len(body.admin_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="admin_password must be at least 8 characters.",
        )

    try:
        result = await db.execute(
            text("""
                INSERT INTO tenants (name, email_domain, plan, max_users, max_jobs,
                                     max_clients, api_access_enabled, branding_level,
                                     status, tenant_type,
                                     monthly_cv_processing_soft_limit,
                                     monthly_cv_processing_hard_limit)
                VALUES (:name, :domain, :plan, :max_users, :max_jobs,
                        :max_clients, :api_access, :branding,
                        'active', :tenant_type, :soft_limit, :hard_limit)
                RETURNING tenant_id
            """),
            {
                "name":        body.name,
                "domain":      body.email_domain,
                "plan":        body.plan,
                "max_users":   body.max_users,
                "max_jobs":    body.max_jobs,
                "max_clients": body.max_clients,
                "api_access":  body.api_access_enabled,
                "branding":    body.branding_level,
                "tenant_type": body.tenant_type,
                "soft_limit":  body.monthly_cv_processing_soft_limit,
                "hard_limit":  body.monthly_cv_processing_hard_limit,
            },
        )
        tenant_id = str(result.scalar_one())

        user_id: str | None = None
        _verify_token: str | None = None
        if has_admin:
            import hashlib as _hl, secrets as _sec
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz2
            pw_hash = hash_password(body.admin_password)  # type: ignore[arg-type]
            _verify_token = _sec.token_urlsafe(48)
            _verify_hash = _hl.sha256(_verify_token.encode()).hexdigest()
            _verify_exp = _dt.now(_tz2.utc) + _td(hours=48)
            user_result = await db.execute(
                text("""
                    INSERT INTO users (
                        tenant_id, email, password_hash, full_name, role,
                        status, must_change_password,
                        email_verification_token_hash, email_verification_expires_at
                    )
                    VALUES (
                        :tid, :email, :pw, :name, 'admin',
                        'pending_email_verification', true,
                        :token_hash, :expires
                    )
                    RETURNING user_id
                """),
                {
                    "tid":        tenant_id,
                    "email":      str(body.admin_email),
                    "pw":         pw_hash,
                    "name":       body.admin_full_name,
                    "token_hash": _verify_hash,
                    "expires":    _verify_exp,
                },
            )
            user_id = str(user_result.scalar_one())

        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with that email already exists.",
            )
        raise

    # Send activation email to new admin — fire-and-forget
    if user_id and has_admin and _verify_token:
        try:
            from services.email_service import send_activation_email as _send_activation
            import asyncio as _asyncio
            _base_url = getattr(settings, "app_base_url", "https://app.ai970.cloud")
            verify_link = f"{_base_url}/verify-email?token={_verify_token}"
            _asyncio.create_task(_send_activation(
                to_email=str(body.admin_email),
                name=body.admin_full_name or "",
                company_name=body.name,
                verify_link=verify_link,
                role="admin",
            ))
        except Exception:
            pass

    response: dict = {"success": True, "tenant_id": tenant_id}
    if user_id:
        response["user_id"] = user_id
    return response


@router.patch("/tenants/status", dependencies=[RequireSuperAdmin])
async def update_tenant_status(
    body: UpdateTenantStatusRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.status not in ("active", "suspended"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid status",
        )

    await set_rls_context(db, current_user.tenant_id, "super_admin")

    await db.execute(
        text("UPDATE tenants SET status = :status WHERE tenant_id = :tid"),
        {"status": body.status, "tid": body.tenant_id},
    )

    if body.status == "suspended":
        await db.execute(
            text("UPDATE users SET status = 'disabled' WHERE tenant_id = :tid"),
            {"tid": body.tenant_id},
        )

    await db.commit()

    return {"success": True, "message": f"Tenant {body.status}"}


@router.get("/users", dependencies=[RequireSuperAdmin])
async def list_users(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    role: str | None = None,
    status_filter: str | None = None,
    tenant_id: str | None = None,
):
    limit = min(limit, 100)
    offset = (page - 1) * limit
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    where_clauses = []
    params = {"limit": limit, "offset": offset}

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

    count_row = await db.execute(
        text(f"SELECT COUNT(*) FROM users u {where_sql}"),
        params,
    )
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

    existing = await db.execute(
        text("SELECT user_id FROM users WHERE email = :email"),
        {"email": body.email},
    )

    if existing.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

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

    tenant_row = await db.execute(
        text("SELECT name FROM tenants WHERE tenant_id = :tid"),
        {"tid": tenant_id},
    )
    tenant = tenant_row.mappings().first()

    await db.commit()

    invite_link = f"{settings.app_base_url}/accept-invite?token={token}"

    await send_invite_email(
        to_email=body.email,
        inviter_name=current_user.full_name,
        tenant_name=tenant["name"] if tenant else "CV Analyzer",
        invite_link=invite_link,
    )

    return {"success": True, "message": "Invitation sent"}


@router.get("/tenants/{tenant_id}/usage", dependencies=[RequireSuperAdmin])
async def get_tenant_usage(
    tenant_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return current usage vs plan limits for a single tenant."""
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    tenant_row = await db.execute(
        text("""
            SELECT t.tenant_id, t.name, t.plan, t.pending_plan, t.max_users, t.max_jobs,
                   t.subscription_status, t.trial_end_at,
                   t.subscription_started_at, t.subscription_ends_at
            FROM tenants t
            WHERE t.tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": tenant_id},
    )
    tenant = tenant_row.mappings().first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    plan_row = await db.execute(
        text("""
            SELECT max_campaigns, max_processed_cvs_per_month, max_users,
                   api_access, advanced_analytics, priority_support, custom_ai_prompts
            FROM subscription_plans WHERE plan_code = :code
        """),
        {"code": tenant["plan"]},
    ) if tenant["plan"] else None
    plan = plan_row.mappings().first() if plan_row else None

    limits = {
        "max_campaigns": plan["max_campaigns"] if plan else (tenant["max_jobs"] or 0),
        "max_users": plan["max_users"] if plan else (tenant["max_users"] or 0),
        "max_processed_cvs_per_month": plan["max_processed_cvs_per_month"] if plan else 0,
    }

    usage_row = await db.execute(
        text("""
            SELECT
                (SELECT COUNT(*) FROM jobs
                 WHERE tenant_id = CAST(:tid AS uuid) AND status = 'active') AS active_campaigns,
                (SELECT COUNT(*) FROM users
                 WHERE tenant_id = CAST(:tid AS uuid) AND status = 'active') AS active_users,
                (SELECT COUNT(*) FROM applications a
                 JOIN jobs j ON j.job_id = a.job_id
                 WHERE j.tenant_id = CAST(:tid AS uuid)
                   AND a.processing_status = 'scored'
                   AND date_trunc('month', a.scored_at) = date_trunc('month', now())) AS cvs_processed_this_month
        """),
        {"tid": tenant_id},
    )
    usage_data = usage_row.mappings().first()

    usage = {
        "active_campaigns": int(usage_data["active_campaigns"]),
        "active_users": int(usage_data["active_users"]),
        "processed_cvs_this_month": int(usage_data["cvs_processed_this_month"]),
    }

    def pct(used: int, limit: int) -> float:
        return round(min(used / limit * 100, 100), 1) if limit > 0 else 0.0

    return {
        "tenant_id": tenant_id,
        "tenant_name": tenant["name"],
        "plan": tenant["plan"],
        "pending_plan": tenant["pending_plan"],
        "subscription_status": tenant["subscription_status"],
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
            "campaigns": pct(usage["active_campaigns"], limits["max_campaigns"]),
            "users": pct(usage["active_users"], limits["max_users"]),
            "cvs": pct(usage["processed_cvs_this_month"], limits["max_processed_cvs_per_month"]),
        },
        "plan_features": {
            "api_access": plan["api_access"] if plan else False,
            "advanced_analytics": plan["advanced_analytics"] if plan else False,
            "priority_support": plan["priority_support"] if plan else False,
            "custom_ai_prompts": plan["custom_ai_prompts"] if plan else False,
        },
    }


@router.patch("/tenants/{tenant_id}/subscription", dependencies=[RequireSuperAdmin])
async def update_tenant_subscription(
    tenant_id: str,
    body: UpdateTenantSubscriptionRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Assign plan, extend trial, suspend, or reactivate a tenant subscription."""
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    existing = await db.execute(
        text("SELECT tenant_id FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": tenant_id},
    )
    if not existing.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if body.action == "assign_plan":
        if not body.plan_code:
            raise HTTPException(status_code=422, detail="plan_code required for assign_plan")
        plan_check = await db.execute(
            text("SELECT plan_id FROM subscription_plans WHERE plan_code = :code AND status = 'active'"),
            {"code": body.plan_code},
        )
        if not plan_check.first():
            raise HTTPException(status_code=422, detail="Plan not found or not active")
        await db.execute(
            text("""
                UPDATE tenants SET
                    plan = :plan_code,
                    subscription_status = 'active',
                    subscription_started_at = COALESCE(subscription_started_at, now())
                WHERE tenant_id = CAST(:tid AS uuid)
            """),
            {"plan_code": body.plan_code, "tid": tenant_id},
        )

    elif body.action == "extend_trial":
        if not body.trial_end_at:
            raise HTTPException(status_code=422, detail="trial_end_at required for extend_trial")
        try:
            new_end = datetime.fromisoformat(body.trial_end_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="trial_end_at must be a valid ISO date")
        await db.execute(
            text("""
                UPDATE tenants SET
                    subscription_status = 'trial',
                    trial_start_at = COALESCE(trial_start_at, now()),
                    trial_end_at = :end_at
                WHERE tenant_id = CAST(:tid AS uuid)
            """),
            {"end_at": new_end, "tid": tenant_id},
        )

    elif body.action == "suspend":
        await db.execute(
            text("""
                UPDATE tenants SET subscription_status = 'suspended'
                WHERE tenant_id = CAST(:tid AS uuid)
            """),
            {"tid": tenant_id},
        )

    elif body.action == "reactivate":
        await db.execute(
            text("""
                UPDATE tenants SET
                    subscription_status = 'active',
                    subscription_started_at = COALESCE(subscription_started_at, now())
                WHERE tenant_id = CAST(:tid AS uuid)
            """),
            {"tid": tenant_id},
        )

    else:
        raise HTTPException(
            status_code=422,
            detail="action must be one of: assign_plan, extend_trial, suspend, reactivate",
        )

    await db.commit()
    return {"success": True, "action": body.action}


class UpdateTenantFeaturesRequest(BaseModel):
    job_application_controls_enabled: bool | None = None


@router.patch("/tenants/{tenant_id}/features", dependencies=[RequireSuperAdmin])
async def update_tenant_features(
    tenant_id: str,
    body: UpdateTenantFeaturesRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Toggle feature flags for a tenant (Super Admin only)."""
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    existing = await db.execute(
        text("SELECT tenant_id FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": tenant_id},
    )
    if not existing.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    updates: dict = {}
    if body.job_application_controls_enabled is not None:
        updates["job_application_controls_enabled"] = body.job_application_controls_enabled

    if not updates:
        return {"success": True, "message": "No changes to apply."}

    set_parts = ", ".join(f"{k} = :{k}" for k in updates)
    updates["tid"] = tenant_id
    await db.execute(
        text(f"UPDATE tenants SET {set_parts} WHERE tenant_id = CAST(:tid AS uuid)"),
        updates,
    )
    await db.commit()
    return {"success": True}


class UpdateTenantFairUsageRequest(BaseModel):
    tenant_type: str | None = None
    monthly_cv_processing_soft_limit: int | None = None  # -1 to clear
    monthly_cv_processing_hard_limit: int | None = None  # -1 to clear


@router.patch("/tenants/{tenant_id}/fair-usage", dependencies=[RequireSuperAdmin])
async def update_tenant_fair_usage(
    tenant_id: str,
    body: UpdateTenantFairUsageRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update internal Fair Usage Policy limits and tenant type for a tenant."""
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    existing = await db.execute(
        text("SELECT tenant_id FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": tenant_id},
    )
    if not existing.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    updates: dict = {}
    if body.tenant_type is not None:
        valid = ("organization", "agency", "individual_recruiter")
        if body.tenant_type not in valid:
            raise HTTPException(status_code=422, detail=f"tenant_type must be one of: {', '.join(valid)}")
        updates["tenant_type"] = body.tenant_type
    if body.monthly_cv_processing_soft_limit is not None:
        updates["monthly_cv_processing_soft_limit"] = (
            None if body.monthly_cv_processing_soft_limit < 0 else body.monthly_cv_processing_soft_limit
        )
    if body.monthly_cv_processing_hard_limit is not None:
        updates["monthly_cv_processing_hard_limit"] = (
            None if body.monthly_cv_processing_hard_limit < 0 else body.monthly_cv_processing_hard_limit
        )

    if not updates:
        return {"success": True, "message": "No changes to apply."}

    set_parts = ", ".join(f"{k} = :{k}" for k in updates)
    updates["tid"] = tenant_id
    await db.execute(
        text(f"UPDATE tenants SET {set_parts} WHERE tenant_id = CAST(:tid AS uuid)"),
        updates,
    )
    await db.commit()
    return {"success": True, "message": "Tenant fair usage settings updated."}


@router.patch("/users/status")
async def update_user_status(
    body: UpdateUserStatusRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.status not in ("active", "disabled"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid status",
        )

    if current_user.role not in ("super_admin", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    if body.user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own status",
        )

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    result = await db.execute(
        text("UPDATE users SET status = :status WHERE user_id = :uid RETURNING user_id"),
        {"status": body.status, "uid": body.user_id},
    )

    if not result.first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    await db.commit()

    return {"success": True, "message": f"User {body.status}"}


@router.post("/backfill-canonical-fingerprints", dependencies=[RequireSuperAdmin])
async def trigger_canonical_fingerprint_backfill(batch_size: int = 200):
    """
    Enqueue a background task to compute canonical_text_fingerprint for all
    application_files rows where it is currently NULL (rows scored before
    migration 043 or before the enhanced canonical normalisation was deployed).

    The task runs asynchronously via Celery — this endpoint returns immediately
    with the task ID.  Progress can be monitored via Celery task status.

    batch_size controls how many rows are processed per DB round-trip (default 200).
    """
    from workers.backfill_canonical_fp import backfill_canonical_fingerprints_task

    task = backfill_canonical_fingerprints_task.delay(batch_size=batch_size)
    return {
        "success": True,
        "task_id": task.id,
        "message": (
            f"Canonical fingerprint backfill enqueued (batch_size={batch_size}). "
            "Check Celery task logs for progress."
        ),
    }
