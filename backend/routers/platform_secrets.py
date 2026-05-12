"""
Platform Secrets Management — Super Admin only.

SECURITY CONTRACT:
- The `value` column is NEVER returned by any endpoint.
- Only `masked_value` (last 4 chars + dots) is exposed.
- PUT replaces the value; the new masked form is computed and stored.
- GET returns has_value=True only when a value has been set; never the value itself.

IMPORTANT — Runtime Integration Note:
The application currently reads secrets from environment variables at startup.
Values stored here are an auditable management layer. After updating a secret here,
the corresponding environment variable must also be updated and the service restarted
for the new value to take effect at runtime.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin
from database import get_db, set_rls_context

router = APIRouter(prefix="/admin/platform-secrets", tags=["platform-secrets"])

_CRITICAL_KEYS = {"JWT_SECRET", "DB_PASSWORD", "OPENAI_API_KEY"}

# Per-key metadata returned to the frontend for display and validation.
# source: where the runtime actually reads the value from.
# restart_required: whether a service restart is needed after update.
# min_length: minimum accepted value length for the PUT endpoint.
_SECRET_META: dict[str, dict] = {
    "OPENAI_API_KEY":         {"source": "env", "restart_required": True, "min_length": 20},
    "JWT_SECRET":             {"source": "env", "restart_required": True, "min_length": 32},
    "SMTP_PASSWORD":          {"source": "env", "restart_required": True, "min_length": 1},
    "IMAP_PASSWORD":          {"source": "env", "restart_required": True, "min_length": 1},
    "REDIS_PASSWORD":         {"source": "env", "restart_required": True, "min_length": 1},
    "DB_PASSWORD":            {"source": "env", "restart_required": True, "min_length": 1},
    "SMTP_USER":              {"source": "env", "restart_required": True, "min_length": 3},
    "IMAP_USER":              {"source": "env", "restart_required": True, "min_length": 3},
    "EMAIL_FROM_ADDRESS":     {"source": "env", "restart_required": True, "min_length": 5},
    "SMTP_HOST":              {"source": "env", "restart_required": True, "min_length": 3},
    "IMAP_HOST":              {"source": "env", "restart_required": True, "min_length": 3},
    "REDIS_URL":              {"source": "env", "restart_required": True, "min_length": 8},
    "CELERY_BROKER_URL":      {"source": "env", "restart_required": True, "min_length": 8},
    "CELERY_RESULT_BACKEND":  {"source": "env", "restart_required": True, "min_length": 8},
}
_DEFAULT_META: dict = {"source": "env", "restart_required": True, "min_length": 1}

# Per-key warning messages shown in the confirmation modal.
_CRITICAL_WARNINGS: dict[str, str] = {
    "JWT_SECRET": (
        "Changing JWT_SECRET immediately invalidates ALL active user sessions. "
        "Every logged-in user will be logged out and will need to sign in again."
    ),
    "OPENAI_API_KEY": (
        "Without a valid OPENAI_API_KEY the entire AI scoring pipeline stops working. "
        "CV evaluations will fail until a working key is active."
    ),
    "DB_PASSWORD": (
        "Changing DB_PASSWORD will cause the service to lose database connectivity "
        "until it is restarted with the matching password in the environment."
    ),
}


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
        meta = _SECRET_META.get(r["key"], _DEFAULT_META)
        secrets.append({
            "key":              r["key"],
            "masked_value":     r["masked_value"] or "",
            "description":      r["description"] or "",
            "category":         r["category"],
            "is_critical":      r["is_critical"],
            "has_value":        r["has_value"],
            "updated_at":       r["updated_at"].isoformat() if r["updated_at"] else None,
            "updated_by_email": r["updated_by_email"] or "",
            "source":           meta["source"],
            "restart_required": meta["restart_required"],
            "min_length":       meta.get("min_length", 1),
            "critical_warning": _CRITICAL_WARNINGS.get(r["key"]),
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

    val = body.value.strip() if body.value else ""
    if not val:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Secret value cannot be empty.",
        )

    meta = _SECRET_META.get(key, _DEFAULT_META)
    min_len = meta.get("min_length", 1)
    if len(val) < min_len:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{key}' must be at least {min_len} characters long.",
        )

    row = await db.execute(
        text("SELECT key, is_critical FROM platform_secrets WHERE key = :k"),
        {"k": key},
    )
    existing = row.mappings().first()
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Secret key '{key}' not found.",
        )

    masked = _mask(val)

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
        "val":    val,
        "masked": masked,
        "uid":    current_user.user_id,
        "email":  current_user.email,
        "k":      key,
    })
    await db.commit()

    is_critical = existing["is_critical"]
    if is_critical:
        warning = (
            _CRITICAL_WARNINGS.get(key)
            or f"'{key}' is a critical secret. Restart the service for the new value to take effect."
        )
    elif meta.get("restart_required"):
        warning = "Service restart required for the new value to take effect at runtime."
    else:
        warning = None

    return {
        "success":      True,
        "key":          key,
        "masked_value": masked,
        "warning":      warning,
    }
