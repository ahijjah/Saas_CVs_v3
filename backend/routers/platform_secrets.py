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
import logging
import os
import subprocess
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep, RequireSuperAdmin
from database import get_db, set_rls_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/platform-secrets", tags=["platform-secrets"])

_CRITICAL_KEYS = {"JWT_SECRET", "DB_PASSWORD", "OPENAI_API_KEY"}

# Fixed list of services the restart endpoint may act on.
# Never derived from user input.
_MANAGED_SERVICES = ["api", "worker", "beat"]

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
        "restart_required": meta.get("restart_required", False),
    }


# ── POST /admin/platform-secrets/restart-services ────────────────────────────

@router.post("/restart-services", dependencies=[RequireSuperAdmin])
async def restart_services(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Request a controlled restart of the api, worker, and beat services.

    Security:
    - RequireSuperAdmin only.
    - Service list is hardcoded — no user input accepted.
    - All attempts are logged with requesting user identity.

    Behaviour:
    - Checks for Docker socket at /var/run/docker.sock.
    - If available: runs `docker compose restart api worker beat` (fixed args, no shell).
    - If not available: returns success=False with a manual-restart message.
    """
    await set_rls_context(db, current_user.tenant_id, "super_admin")

    logger.warning(
        "Service restart requested by %s (user_id=%s)",
        current_user.email,
        current_user.user_id,
    )

    docker_socket = "/var/run/docker.sock"
    if not os.path.exists(docker_socket):
        logger.info(
            "Restart request by %s denied — Docker socket not accessible",
            current_user.email,
        )
        return {
            "success": False,
            "message": (
                "Restart must be performed manually from the server. "
                "The Docker socket is not accessible from within this container. "
                "Run: docker compose restart api worker beat"
            ),
        }

    # Docker socket is accessible — attempt restart with fixed service list only.
    cmd = ["docker", "compose", "restart"] + _MANAGED_SERVICES
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.warning(
                "Services %s restarted successfully by %s",
                _MANAGED_SERVICES,
                current_user.email,
            )
            return {
                "success": True,
                "message": (
                    f"Backend services restart requested successfully "
                    f"({', '.join(_MANAGED_SERVICES)}). "
                    "The API may be briefly unavailable while it comes back up."
                ),
            }
        else:
            logger.error(
                "docker compose restart failed (code %d): %s",
                result.returncode,
                result.stderr[:500],
            )
            return {
                "success": False,
                "message": (
                    "Restart command failed. Please restart services manually. "
                    f"Error: {result.stderr[:200]}"
                ),
            }
    except subprocess.TimeoutExpired:
        logger.error("docker compose restart timed out for %s", current_user.email)
        return {
            "success": False,
            "message": "Restart command timed out. Services may still be restarting — check server status.",
        }
    except (FileNotFoundError, OSError) as exc:
        logger.error("docker command unavailable: %s", exc)
        return {
            "success": False,
            "message": "Restart must be performed manually from the server — docker command not found.",
        }
