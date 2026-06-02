import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, get_current_user
from auth.jwt import create_access_token
from auth.password import hash_password, verify_password
from config import get_settings
from database import get_db, set_rls_context
from services.email_service import send_password_reset_email

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

_VERIFY_TOKEN_EXPIRY_HOURS = 48


# ── Schemas ──────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < settings.password_min_length:
            raise ValueError(f"Password must be at least {settings.password_min_length} characters")
        return v


class RegisterRequest(BaseModel):
    tenant_name: str
    email_domain: str
    admin_name: str
    email: EmailStr
    password: str
    tenant_type: str = "organization"

    @field_validator("password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < settings.password_min_length:
            raise ValueError(f"Password must be at least {settings.password_min_length} characters")
        return v

    @field_validator("tenant_type")
    @classmethod
    def valid_tenant_type(cls, v: str) -> str:
        valid = {"organization", "agency", "individual_recruiter"}
        if v not in valid:
            raise ValueError(f"tenant_type must be one of: {', '.join(sorted(valid))}")
        return v


class UpdateProfileRequest(BaseModel):
    admin_name: str | None = None
    tenant_name: str | None = None
    email_domain: str | None = None
    cv_ingestion_mode: str | None = None
    forwarding_email: str | None = None

    @field_validator("cv_ingestion_mode")
    @classmethod
    def valid_mode(cls, v: str | None) -> str | None:
        if v is None:
            return v
        _map = {
            'platform_email': 'platform_email',
            'IMAP': 'platform_email',
            'forwarding': 'forwarding',
            'FORWARD': 'forwarding',
        }
        normalized = _map.get(v)
        if normalized is None:
            raise ValueError("cv_ingestion_mode must be 'platform_email' or 'forwarding'")
        return normalized


class ChangePasswordRequest(BaseModel):
    old_password: str | None = None
    current_password: str | None = None
    new_password: str
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < settings.password_min_length:
            raise ValueError(f"Password must be at least {settings.password_min_length} characters")
        return v


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < settings.password_min_length:
            raise ValueError(f"Password must be at least {settings.password_min_length} characters")
        return v


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(48)


async def _fetch_profile(user_id: str, tenant_id: str, db) -> dict:
    """Fetch full profile data and return serialized dict."""
    row = await db.execute(
        text("""
            SELECT u.user_id, u.full_name, u.email, u.role, u.must_change_password,
                   t.tenant_id, t.name AS tenant_name, t.email_domain,
                   t.cv_ingestion_mode, t.forwarding_email,
                   t.plan, t.pending_plan, t.max_users, t.max_jobs,
                   t.tenant_type, t.status AS tenant_status,
                   t.created_at AS tenant_created_at,
                   t.subscription_status, t.trial_end_at,
                   t.job_application_controls_enabled, t.allow_advanced_workflow_move
            FROM users u
            JOIN tenants t ON t.tenant_id = u.tenant_id
            WHERE u.user_id = :uid
        """),
        {"uid": user_id},
    )
    p = row.mappings().first()
    if not p:
        return None

    count_row = await db.execute(
        text("""
            SELECT COUNT(*)
            FROM users
            WHERE tenant_id = :tenant_id
              AND status IN ('active', 'pending_email_verification')
        """),
        {"tenant_id": p["tenant_id"]},
    )
    active_count = int(count_row.scalar_one())

    intake_method = "IMAP" if p["cv_ingestion_mode"] == "platform_email" else "FORWARD"

    return {
        "user_id": str(p["user_id"]),
        "tenant_id": str(p["tenant_id"]),
        "tenant_name": p["tenant_name"],
        "admin_name": p["full_name"],
        "email": p["email"],
        "role": p["role"],
        "must_change_password": bool(p["must_change_password"]),
        "intake_method": intake_method,
        "forwarding_email": p["forwarding_email"],
        "email_domain": p["email_domain"],
        "plan": p["plan"],
        "pending_plan": p["pending_plan"],
        "tenant_type": p["tenant_type"] or "organization",
        "max_users": p["max_users"],
        "max_jobs": p["max_jobs"],
        "tenant_status": p["tenant_status"],
        "subscription_status": p["subscription_status"] or "active",
        "trial_end_at": p["trial_end_at"].isoformat() if p["trial_end_at"] else None,
        "tenant_created_at": p["tenant_created_at"].isoformat() if p["tenant_created_at"] else None,
        "active_users_count": active_count,
        "job_application_controls_enabled": bool(p["job_application_controls_enabled"]),
        "allow_advanced_workflow_move": bool(p["allow_advanced_workflow_move"]),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    await set_rls_context(db, "", "super_admin")

    row = await db.execute(
        text("""
            SELECT u.user_id, u.tenant_id, u.email, u.full_name, u.role, u.status,
                   u.password_hash, u.must_change_password,
                   t.cv_ingestion_mode, t.status AS tenant_status,
                   t.subscription_status, t.name AS tenant_name, t.tenant_type,
                   t.allow_advanced_workflow_move
            FROM users u
            JOIN tenants t ON t.tenant_id = u.tenant_id
            WHERE u.email = :email
        """),
        {"email": body.email},
    )
    user = row.mappings().first()

    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if user["status"] == "pending_email_verification":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before logging in. Check your inbox for the verification link.",
        )
    if user["status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    if user["tenant_status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant suspended")

    await db.execute(
        text("UPDATE users SET last_login_at = now() WHERE user_id = :uid"),
        {"uid": str(user["user_id"])},
    )
    await db.commit()

    token = create_access_token({
        "sub": str(user["user_id"]),
        "tenant_id": str(user["tenant_id"]),
        "email": user["email"],
        "role": user["role"],
    })

    return {
        "success": True,
        "token": token,
        "must_change_password": bool(user["must_change_password"]),
        "user": {
            "user_id": str(user["user_id"]),
            "sub": str(user["user_id"]),
            "tenant_id": str(user["tenant_id"]),
            "email": user["email"],
            "role": user["role"],
            "tenant_name": user["tenant_name"],
            "tenant_type": user["tenant_type"] or "organization",
            "subscription_status": user["subscription_status"] or "active",
            "must_change_password": bool(user["must_change_password"]),
            "allow_advanced_workflow_move": bool(user["allow_advanced_workflow_move"]),
        },
        "cv_ingestion_mode": user["cv_ingestion_mode"],
        "message": "Login successful",
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    await set_rls_context(db, "", "super_admin")

    existing = await db.execute(
        text("SELECT user_id FROM users WHERE email = :email"),
        {"email": body.email},
    )
    if existing.first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    password_hash = hash_password(body.password)

    tenant_result = await db.execute(
        text("""
            INSERT INTO tenants (
                name, email_domain, max_users, max_jobs,
                cv_ingestion_mode, status, tenant_type,
                subscription_status
            )
            VALUES (
                :name, :domain, 3, 10,
                'platform_email', 'active', :tenant_type,
                'pending_plan_selection'
            )
            RETURNING tenant_id
        """),
        {"name": body.tenant_name, "domain": body.email_domain, "tenant_type": body.tenant_type},
    )
    tenant_id = str(tenant_result.scalar_one())

    # Create admin in pending_email_verification state (cannot login until verified)
    verify_token = _generate_token()
    verify_token_hash = _hash_token(verify_token)
    verify_expires = datetime.now(timezone.utc) + timedelta(hours=_VERIFY_TOKEN_EXPIRY_HOURS)

    user_result = await db.execute(
        text("""
            INSERT INTO users (
                tenant_id, email, password_hash, full_name, role, status,
                email_verification_token_hash, email_verification_expires_at,
                must_change_password
            )
            VALUES (
                :tid, :email, :pw, :name, 'admin', 'pending_email_verification',
                :token_hash, :expires,
                false
            )
            RETURNING user_id
        """),
        {
            "tid": tenant_id,
            "email": body.email,
            "pw": password_hash,
            "name": body.admin_name,
            "token_hash": verify_token_hash,
            "expires": verify_expires,
        },
    )
    user_id = str(user_result.scalar_one())
    await db.commit()

    # Send email verification link — fire-and-forget
    try:
        from services.email_service import send_email_verification_email as _send_verify
        import asyncio as _asyncio
        verify_link = f"{settings.app_base_url}/verify-email?token={verify_token}"
        _asyncio.create_task(_send_verify(
            to_email=body.email,
            name=body.admin_name,
            company_name=body.tenant_name,
            verify_link=verify_link,
        ))
    except Exception:
        pass

    return {
        "success": True,
        "needs_verification": True,
        "email": body.email,
        "message": "Registration successful. Please check your email to verify your account.",
    }


@router.get("/verify-email")
async def verify_email(
    token: str = Query(..., description="Email verification token"),
    db: AsyncSession = Depends(get_db),
):
    """Verify email address via token from verification email. Issues a JWT on success."""
    await set_rls_context(db, "", "super_admin")

    token_hash = _hash_token(token)
    row = await db.execute(
        text("""
            SELECT u.user_id, u.tenant_id, u.email, u.role, u.must_change_password,
                   t.status AS tenant_status, t.subscription_status,
                   t.name AS tenant_name, t.tenant_type, t.cv_ingestion_mode
            FROM users u
            JOIN tenants t ON t.tenant_id = u.tenant_id
            WHERE u.email_verification_token_hash = :hash
              AND u.email_verification_expires_at > now()
              AND u.status = 'pending_email_verification'
        """),
        {"hash": token_hash},
    )
    user = row.mappings().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link. Please register again or contact support.",
        )

    if user["tenant_status"] != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant suspended")

    # Activate the user
    await db.execute(
        text("""
            UPDATE users
            SET status                         = 'active',
                email_verified_at              = now(),
                email_verification_token_hash  = NULL,
                email_verification_expires_at  = NULL,
                last_login_at                  = now()
            WHERE user_id = :uid
        """),
        {"uid": str(user["user_id"])},
    )
    await db.commit()

    jwt_token = create_access_token({
        "sub": str(user["user_id"]),
        "tenant_id": str(user["tenant_id"]),
        "email": user["email"],
        "role": user["role"],
    })

    return {
        "success": True,
        "token": jwt_token,
        "must_change_password": bool(user["must_change_password"]),
        "user": {
            "user_id": str(user["user_id"]),
            "sub": str(user["user_id"]),
            "tenant_id": str(user["tenant_id"]),
            "email": user["email"],
            "role": user["role"],
            "tenant_name": user["tenant_name"],
            "tenant_type": user["tenant_type"] or "organization",
            "subscription_status": user["subscription_status"] or "active",
            "must_change_password": bool(user["must_change_password"]),
        },
        "cv_ingestion_mode": user["cv_ingestion_mode"],
        "message": "Email verified successfully. Welcome!",
    }


@router.get("/me")
async def get_profile(current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    profile = await _fetch_profile(current_user.user_id, current_user.tenant_id, db)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    return {"success": True, "profile": profile}


@router.put("/me")
async def update_profile(
    body: UpdateProfileRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    if body.admin_name:
        await db.execute(
            text("UPDATE users SET full_name = :name WHERE user_id = :uid"),
            {"name": body.admin_name, "uid": current_user.user_id},
        )

    tenant_updates: dict = {}
    if body.tenant_name:
        tenant_updates["name"] = body.tenant_name
    if body.email_domain:
        tenant_updates["email_domain"] = body.email_domain
    if body.cv_ingestion_mode is not None:
        tenant_updates["cv_ingestion_mode"] = body.cv_ingestion_mode
    if body.forwarding_email is not None:
        tenant_updates["forwarding_email"] = body.forwarding_email

    if tenant_updates:
        set_clauses = ", ".join(f"{k} = :{k}" for k in tenant_updates)
        tenant_updates["tid"] = current_user.tenant_id
        await db.execute(
            text(f"UPDATE tenants SET {set_clauses} WHERE tenant_id = :tid"),
            tenant_updates,
        )

    await db.commit()

    profile = await _fetch_profile(current_user.user_id, current_user.tenant_id, db)
    return {"success": True, "profile": profile, "message": "Profile updated"}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("SELECT password_hash, must_change_password FROM users WHERE user_id = :uid"),
        {"uid": current_user.user_id},
    )
    user = row.mappings().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # If not a forced change, verify old password
    if not user["must_change_password"]:
        actual_old = body.old_password or body.current_password
        if not actual_old:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is required")
        if not verify_password(actual_old, user["password_hash"]):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect current password")
        if body.new_password == actual_old:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must differ from old password")

    new_hash = hash_password(body.new_password)
    await db.execute(
        text("""
            UPDATE users
            SET password_hash       = :pw,
                must_change_password = false
            WHERE user_id = :uid
        """),
        {"pw": new_hash, "uid": current_user.user_id},
    )
    await db.commit()
    return {"success": True, "message": "Password changed successfully"}


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    await set_rls_context(db, "", "super_admin")

    row = await db.execute(
        text("SELECT user_id, full_name FROM users WHERE email = :email AND status = 'active'"),
        {"email": body.email},
    )
    user = row.mappings().first()

    if not user:
        return {"success": True, "message": "If the email exists, a reset link has been sent"}

    token = _generate_token()
    token_hash = _hash_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

    await db.execute(
        text("UPDATE users SET reset_token_hash = :hash, reset_token_expires = :exp WHERE user_id = :uid"),
        {"hash": token_hash, "exp": expires_at, "uid": str(user["user_id"])},
    )
    await db.commit()

    reset_link = f"{settings.app_base_url}/reset-password?token={token}"
    await send_password_reset_email(
        to_email=body.email,
        to_name=user["full_name"],
        reset_link=reset_link,
    )

    return {"success": True, "message": "If the email exists, a reset link has been sent"}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match")

    await set_rls_context(db, "", "super_admin")

    token_hash = _hash_token(body.token)
    row = await db.execute(
        text("""
            SELECT user_id FROM users
            WHERE reset_token_hash = :hash
              AND reset_token_expires > now()
              AND status = 'active'
        """),
        {"hash": token_hash},
    )
    user = row.mappings().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    new_hash = hash_password(body.new_password)
    await db.execute(
        text("""
            UPDATE users
            SET password_hash       = :pw,
                reset_token_hash    = NULL,
                reset_token_expires = NULL,
                must_change_password = false
            WHERE user_id = :uid
        """),
        {"pw": new_hash, "uid": str(user["user_id"])},
    )
    await db.commit()
    return {"success": True, "message": "Password reset successfully"}
