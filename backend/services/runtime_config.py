"""
Runtime configuration service — reads secrets from platform_secrets DB table
with a fallback to os.getenv and then to a supplied default.

TTL cache (60 s) avoids a DB round-trip on every call.
Never crashes: any DB error is logged and the env/default fallback is used.
Never exposes secret values in logs or return values.
"""
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_TTL_SECONDS = 60
_cache: dict[str, tuple[str | None, float]] = {}  # key → (value, expires_at)


async def _fetch_from_db(key: str) -> str | None:
    """Read the raw value column from platform_secrets. Returns None on any error."""
    try:
        from database import AsyncSessionLocal, set_rls_context
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            await set_rls_context(db, "", "super_admin")
            row = await db.execute(
                text("SELECT value FROM platform_secrets WHERE key = :k"),
                {"k": key},
            )
            return row.scalar_one_or_none()
    except Exception as exc:
        logger.warning("runtime_config: DB read failed for key %r — using env fallback: %s", key, exc)
        return None


async def get_secret(key: str, default: str | None = None) -> str | None:
    """Return the runtime value for key; DB → env → default. Never logs the value."""
    now = time.monotonic()
    cached_val, expires_at = _cache.get(key, (None, 0.0))
    if expires_at > now:
        return cached_val if cached_val is not None else os.getenv(key, default)

    db_value = await _fetch_from_db(key)
    _cache[key] = (db_value, now + _TTL_SECONDS)

    if db_value is not None:
        return db_value
    return os.getenv(key, default)


async def get_int_secret(key: str, default: int = 0) -> int:
    """Return the runtime value as int. Falls back to default on parse error."""
    raw = await get_secret(key, str(default))
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning("runtime_config: could not parse %r as int; using default %d", key, default)
        return default


async def get_bool_secret(key: str, default: bool = False) -> bool:
    """Return the runtime value as bool. 'true'/'1'/'yes' → True, anything else → False."""
    raw = await get_secret(key, str(default))
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def invalidate(key: str | None = None) -> None:
    """Invalidate cache entry for key, or all entries if key is None."""
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)
