"""
Platform Secrets Management — Super Admin only.

SECURITY CONTRACT:
- The `value` column is NEVER returned by any endpoint.
- Only `masked_value` (last 4 chars + dots) is exposed.
- PUT replaces the value; the new masked form is computed and stored.
- GET returns has_value=True only when a value has been set; never the value itself.

IMPORTANT — Runtime Integration Note:
The application currently reads secrets from environment variables at startup.
Values stored here are an auditable management layer. To make runtime components
use a DB-stored secret, the relevant service code must be updated to load from
this table at startup or reload, followed by a service restart.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin
from database import get_db, set_rls_context

router = APIRouter(prefix="/admin/platform-secrets", tags=["platform-secrets"])

_CRITICAL_KEYS = {"JWT_SECRET", "DB_PASSWORD", "OPENAI_API_KEY"}


def _mask(value: str) -> str:
    """Return ••••••••abcd — last 4 chars visible, rest replaced with dots."""
    if not value:
        return ""
    suffix = value[-4:] if len(value) >= 4 else value
    return "••••••••" + suffix


class UpdateSecretRequest(BaseModel):
    value: str


# ── GET /admin/platform-secrets ───────────────────────────────────────────────

@router.get("", dependencies=[RequireSuperAdmin])
async def list_secrets(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    rows = await db.execute(text("""
        SELECT key, masked_value, description, category, is_critical,
               has_value, updated_at, updated_by_email
        FROM platform_secrets
        ORDER BY is_critical DESC, category, key
    """))

    secrets = []
    for r in rows.mappings():
        secrets.append({
            "key":              r["key"],
            "masked_value":     r["masked_value"] or "",
            "description":      r["description"] or "",
            "category":         r["category"],
            "is_critical":      r["is_critical"],
            "has_value":        r["has_value"],
            "updated_at":       r["updated_at"].isoformat() if r["updated_at"] else None,
            "updated_by_email": r["updated_by_email"] or "",
        })

    return {"success": True, "secrets": secrets}


# ── PUT /admin/platform-secrets/{key} ─────────────────────────────────────────

@router.put("/{key}", dependencies=[RequireSuperAdmin])
async def update_secret(
    key: str,
    body: UpdateSecretRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    if not body.value or not body.value.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Secret value cannot be empty.")

    # Verify the key exists
    row = await db.execute(
        text("SELECT key, is_critical FROM platform_secrets WHERE key = :k"),
        {"k": key},
    )
    existing = row.mappings().first()
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Secret key '{key}' not found.")

    masked = _mask(body.value.strip())

    await db.execute(text("""
        UPDATE platform_secrets
        SET value            = :val,
            masked_value     = :masked,
            has_value        = TRUE,
            updated_at       = NOW(),
            updated_by       = :uid,
            updated_by_email = :email
        WHERE key = :k
    """), {
        "val":    body.value.strip(),
        "masked": masked,
        "uid":    current_user.user_id,
        "email":  current_user.email,
        "k":      key,
    })
    await db.commit()

    is_critical = existing["is_critical"]
    warning = (
        f"⚠️ '{key}' is a critical secret. "
        "Ensure the running service is restarted or reloaded to pick up the new value. "
        "JWT_SECRET changes invalidate all active user sessions."
    ) if is_critical else None

    return {
        "success":      True,
        "key":          key,
        "masked_value": masked,
        "warning":      warning,
    }
