"""
E-02 Phase 1 — Feature Flag Service.

Central service for evaluating per-tenant feature flags.  All functions
default to ENABLED (True) when:
  * The tenant_features table has no row for the (tenant_id, feature_code) pair
  * Any DB error occurs

This "default open" policy guarantees zero production behaviour change.
Future phases will tighten the default for new tenants once subscription
packaging is in place.

Caching
-------
A per-process TTL cache (60 s success, 5 s on DB error) avoids per-request
DB round trips.  The cache is invalidated explicitly on write operations.
Threading.Lock is used so the cache is safe from both async FastAPI workers
and synchronous Celery prefork workers.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from constants.features import ALL_CODES, MODULES

logger = logging.getLogger(__name__)

# ── In-process TTL cache ──────────────────────────────────────────────────────
# key: (tenant_id, feature_code) → (is_enabled: bool, expires_at: float)
# A missing entry means "go to DB".  A stale entry means "go to DB".

_TTL_OK  = 60.0   # seconds to cache a successful DB read
_TTL_ERR =  5.0   # seconds to keep a negative-cache entry after a DB error

_cache: dict[tuple[str, str], tuple[bool, float]] = {}
_cache_lock = threading.Lock()


def _cache_get(tenant_id: str, feature_code: str) -> bool | None:
    """Return cached value or None if absent / stale."""
    key = (tenant_id, feature_code)
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.monotonic() > expires_at:
        return None
    return value


def _cache_set(tenant_id: str, feature_code: str, value: bool, *, ttl: float) -> None:
    key = (tenant_id, feature_code)
    with _cache_lock:
        _cache[key] = (value, time.monotonic() + ttl)


def _cache_invalidate(tenant_id: str, feature_code: str | None = None) -> None:
    """Invalidate one (tenant, feature) pair or all entries for a tenant."""
    with _cache_lock:
        if feature_code is not None:
            _cache.pop((tenant_id, feature_code), None)
        else:
            for k in [k for k in _cache if k[0] == tenant_id]:
                del _cache[k]


# ── Public API ────────────────────────────────────────────────────────────────

async def is_feature_enabled(
    db: AsyncSession,
    tenant_id: str,
    feature_code: str,
) -> bool:
    """
    Return True if the feature is enabled for this tenant.

    Falls back to True on any error — never blocks existing functionality.
    """
    cached = _cache_get(tenant_id, feature_code)
    if cached is not None:
        return cached

    try:
        row = await db.execute(
            text(
                "SELECT is_enabled "
                "FROM cv_analyzer.tenant_features "
                "WHERE tenant_id = CAST(:tid AS uuid) "
                "  AND feature_code = :code "
                "LIMIT 1"
            ),
            {"tid": tenant_id, "code": feature_code},
        )
        result = row.scalar_one_or_none()

        # No row → feature not explicitly configured → default enabled
        enabled = result if result is not None else True
        _cache_set(tenant_id, feature_code, enabled, ttl=_TTL_OK)
        return enabled

    except Exception as exc:
        logger.warning(
            "feature_service: DB error checking %r for tenant %s — defaulting to enabled: %s",
            feature_code, tenant_id, exc,
        )
        _cache_set(tenant_id, feature_code, True, ttl=_TTL_ERR)
        return True


async def get_tenant_features(
    db: AsyncSession,
    tenant_id: str,
) -> dict[str, bool]:
    """
    Return a dict of {feature_code: is_enabled} for all known feature codes.

    Uses DB rows where present; defaults missing codes to True.
    """
    try:
        rows = await db.execute(
            text(
                "SELECT feature_code, is_enabled "
                "FROM cv_analyzer.tenant_features "
                "WHERE tenant_id = CAST(:tid AS uuid)"
            ),
            {"tid": tenant_id},
        )
        db_map: dict[str, bool] = {
            r["feature_code"]: r["is_enabled"]
            for r in rows.mappings()
        }
    except Exception as exc:
        logger.warning(
            "feature_service: DB error fetching features for tenant %s — all default enabled: %s",
            tenant_id, exc,
        )
        db_map = {}

    # Merge: DB values override the default-enabled fallback for all known codes
    result: dict[str, bool] = {code: db_map.get(code, True) for code in ALL_CODES}

    # Populate cache from bulk fetch
    now = time.monotonic()
    with _cache_lock:
        for code, enabled in result.items():
            _cache[(tenant_id, code)] = (enabled, now + _TTL_OK)

    return result


async def log_feature_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    feature_code: str,
    old_value: bool | None,
    new_value: bool,
    changed_by: str,
) -> None:
    """Insert one audit row. Never raises — fire-and-forget."""
    try:
        await db.execute(
            text(
                "INSERT INTO cv_analyzer.tenant_feature_audit "
                "    (tenant_id, feature_code, old_value, new_value, changed_by) "
                "VALUES (CAST(:tid AS uuid), :code, :old, :new, CAST(:by AS uuid))"
            ),
            {
                "tid": tenant_id,
                "code": feature_code,
                "old": old_value,
                "new": new_value,
                "by": changed_by,
            },
        )
    except Exception as exc:
        logger.warning("feature_service: audit log failed for %r: %s", feature_code, exc)


async def _get_current_value(db: AsyncSession, tenant_id: str, feature_code: str) -> bool | None:
    """Read current is_enabled for one row. Returns None if no row exists."""
    try:
        row = await db.execute(
            text(
                "SELECT is_enabled FROM cv_analyzer.tenant_features "
                "WHERE tenant_id = CAST(:tid AS uuid) AND feature_code = :code LIMIT 1"
            ),
            {"tid": tenant_id, "code": feature_code},
        )
        return row.scalar_one_or_none()
    except Exception:
        return None


async def enable_feature(
    db: AsyncSession,
    tenant_id: str,
    feature_code: str,
    *,
    changed_by: str | None = None,
) -> None:
    """Enable a single feature for a tenant (upsert). Audits if changed_by is supplied."""
    old = await _get_current_value(db, tenant_id, feature_code)
    await db.execute(
        text(
            "INSERT INTO cv_analyzer.tenant_features "
            "    (tenant_id, feature_code, is_enabled) "
            "VALUES (CAST(:tid AS uuid), :code, TRUE) "
            "ON CONFLICT (tenant_id, feature_code) "
            "DO UPDATE SET is_enabled = TRUE, updated_at = NOW()"
        ),
        {"tid": tenant_id, "code": feature_code},
    )
    if changed_by:
        await log_feature_audit(
            db, tenant_id=tenant_id, feature_code=feature_code,
            old_value=old, new_value=True, changed_by=changed_by,
        )
    await db.commit()
    _cache_invalidate(tenant_id, feature_code)


async def disable_feature(
    db: AsyncSession,
    tenant_id: str,
    feature_code: str,
    *,
    changed_by: str | None = None,
) -> None:
    """Disable a single feature for a tenant (upsert). Audits if changed_by is supplied."""
    old = await _get_current_value(db, tenant_id, feature_code)
    await db.execute(
        text(
            "INSERT INTO cv_analyzer.tenant_features "
            "    (tenant_id, feature_code, is_enabled) "
            "VALUES (CAST(:tid AS uuid), :code, FALSE) "
            "ON CONFLICT (tenant_id, feature_code) "
            "DO UPDATE SET is_enabled = FALSE, updated_at = NOW()"
        ),
        {"tid": tenant_id, "code": feature_code},
    )
    if changed_by:
        await log_feature_audit(
            db, tenant_id=tenant_id, feature_code=feature_code,
            old_value=old, new_value=False, changed_by=changed_by,
        )
    await db.commit()
    _cache_invalidate(tenant_id, feature_code)


async def bulk_enable_module(
    db: AsyncSession,
    tenant_id: str,
    module_code: str,
    *,
    changed_by: str | None = None,
) -> None:
    """Enable the module sentinel and all its feature codes for a tenant."""
    codes = [module_code] + list(MODULES.get(module_code, []))
    for code in codes:
        old = await _get_current_value(db, tenant_id, code)
        await db.execute(
            text(
                "INSERT INTO cv_analyzer.tenant_features "
                "    (tenant_id, feature_code, is_enabled) "
                "VALUES (CAST(:tid AS uuid), :code, TRUE) "
                "ON CONFLICT (tenant_id, feature_code) "
                "DO UPDATE SET is_enabled = TRUE, updated_at = NOW()"
            ),
            {"tid": tenant_id, "code": code},
        )
        if changed_by:
            await log_feature_audit(
                db, tenant_id=tenant_id, feature_code=code,
                old_value=old, new_value=True, changed_by=changed_by,
            )
    await db.commit()
    _cache_invalidate(tenant_id)


async def bulk_disable_module(
    db: AsyncSession,
    tenant_id: str,
    module_code: str,
    *,
    changed_by: str | None = None,
) -> None:
    """Disable the module sentinel and all its feature codes for a tenant."""
    codes = [module_code] + list(MODULES.get(module_code, []))
    for code in codes:
        old = await _get_current_value(db, tenant_id, code)
        await db.execute(
            text(
                "INSERT INTO cv_analyzer.tenant_features "
                "    (tenant_id, feature_code, is_enabled) "
                "VALUES (CAST(:tid AS uuid), :code, FALSE) "
                "ON CONFLICT (tenant_id, feature_code) "
                "DO UPDATE SET is_enabled = FALSE, updated_at = NOW()"
            ),
            {"tid": tenant_id, "code": code},
        )
        if changed_by:
            await log_feature_audit(
                db, tenant_id=tenant_id, feature_code=code,
                old_value=old, new_value=False, changed_by=changed_by,
            )
    await db.commit()
    _cache_invalidate(tenant_id)
