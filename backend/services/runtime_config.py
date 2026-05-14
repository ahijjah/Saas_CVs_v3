"""
Runtime configuration service — fully synchronous, Celery prefork-safe.

Reads secrets from platform_secrets DB table using a dedicated synchronous
SQLAlchemy engine (psycopg2, NullPool). No asyncpg, no event loops.

Resolution order: DB value → os.getenv → supplied default.
TTL cache: 60 s for successful reads, 5 s for failed reads (so transient DB
errors recover quickly without hammering the DB).
Thread-safe via a lock on both engine creation and cache access.
Never exposes secret values in logs.
"""
import logging
import os
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_TTL_SECONDS = 60
_NEGATIVE_TTL = 5   # short retry window after a DB failure

# key → (db_value_or_None, expires_at, db_succeeded)
_cache: dict[str, tuple[Optional[str], float, bool]] = {}
_cache_lock = threading.Lock()

_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    """Lazy-create a per-process synchronous SQLAlchemy engine (thread-safe)."""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            from sqlalchemy import create_engine, pool
            from config import get_settings

            db_url = get_settings().database_url
            # Replace asyncpg driver with psycopg2 for sync access
            db_url = re.sub(r'\+(asyncpg|aiopg)\b', '+psycopg2', db_url)
            if not re.search(r'\+\w+', db_url.split('://')[0]):
                db_url = db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

            _engine = create_engine(
                db_url,
                poolclass=pool.NullPool,  # no shared connections across fork boundary
                pool_pre_ping=True,
                connect_args={"options": "-csearch_path=cv_analyzer"},
            )
        except Exception as exc:
            logger.error("runtime_config: failed to create sync engine: %s", exc)
            return None
    return _engine


def _fetch_from_db(key: str) -> Optional[str]:
    """Query platform_secrets for key. Returns the raw value or None on any error."""
    engine = _get_engine()
    if engine is None:
        return None
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM cv_analyzer.platform_secrets WHERE key = :k"),
                {"k": key},
            )
            value = row.scalar_one_or_none()
        logger.debug("runtime_config: loaded %r from DB", key)
        return value
    except Exception as exc:
        logger.warning(
            "runtime_config: DB read failed for key %r — using env fallback: %s",
            key, exc,
        )
        return None


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return the runtime value for key (DB → env → default). Never logs the value."""
    now = time.monotonic()

    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None:
            cached_val, expires_at, db_ok = entry
            if expires_at > now:
                if db_ok and cached_val is not None:
                    return cached_val
                # DB miss or prior failure — fall through to env
                return os.getenv(key, default)

    # Cache stale or missing — hit DB outside the lock to avoid blocking other threads
    db_value = _fetch_from_db(key)
    now = time.monotonic()

    with _cache_lock:
        if db_value is not None:
            _cache[key] = (db_value, now + _TTL_SECONDS, True)
            if os.getenv(key):
                logger.info("runtime_config: loaded %r from DB (overrides env)", key)
            else:
                logger.info("runtime_config: loaded %r from DB", key)
            return db_value
        else:
            # Short negative TTL so transient failures don't lock out DB for a full minute
            _cache[key] = (None, now + _NEGATIVE_TTL, False)
            env_val = os.getenv(key)
            if env_val:
                logger.info("runtime_config: fallback to env for %r", key)
            return env_val if env_val is not None else default


def get_int_secret(key: str, default: int = 0) -> int:
    """Return the runtime value as int. Falls back to default on parse error."""
    raw = get_secret(key, str(default))
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning(
            "runtime_config: cannot parse %r as int — using default %d", key, default
        )
        return default


def get_bool_secret(key: str, default: bool = False) -> bool:
    """Return the runtime value as bool. 'true'/'1'/'yes' → True, else False."""
    raw = get_secret(key, str(default))
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def invalidate(key: Optional[str] = None) -> None:
    """Invalidate a single cache entry or all entries when key is None."""
    with _cache_lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)
