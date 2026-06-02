"""
AI Model Registry Service

Resolves stage → model → provider secret → client at call time.
Always reads from DB so changes take effect on next task run without restart.

NEVER raises — callers fall back to settings.openai_* on None return.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class ResolvedModel:
    provider: str
    model_name: str
    client: Any           # AsyncOpenAI-compatible client
    model_id: str | None  # UUID string from ai_model_registry
    is_fallback: bool = False
    fallback_reason: str | None = None


async def _load_registry_model(db: AsyncSession, model_id: str) -> dict | None:
    """Load a single model row from the registry."""
    try:
        row = await db.execute(
            text("""
                SELECT model_id, provider, model_name, display_name,
                       provider_secret_key, base_url, enabled, supported_stages
                FROM cv_analyzer.ai_model_registry
                WHERE model_id = :mid
            """),
            {"mid": model_id},
        )
        r = row.mappings().first()
        return dict(r) if r else None
    except Exception as exc:
        logger.warning("ai_model_registry: failed to load model %s: %s", model_id, exc)
        return None


async def _get_api_key(db: AsyncSession, secret_key: str | None) -> str:
    """Read API key from platform_secrets (DB), fall back to env."""
    if not secret_key:
        return ""
    try:
        row = await db.execute(
            text("SELECT value FROM cv_analyzer.platform_secrets WHERE key = :k"),
            {"k": secret_key},
        )
        db_val = (row.scalar_one_or_none() or "").strip()
        if db_val:
            return db_val
    except Exception as exc:
        logger.warning("ai_model_registry: secret lookup failed for %s: %s", secret_key, exc)
    # Env fallback — matches existing behavior for OPENAI_API_KEY
    return os.environ.get(secret_key, "").strip()


def _build_client(provider: str, api_key: str, base_url: str | None) -> Any | None:
    """Build an async LLM client for the given provider."""
    if not api_key:
        logger.warning("ai_model_registry: no API key for provider=%s", provider)
        return None
    try:
        if provider in ("openai", "azure_openai", "deepseek", "openai_compatible"):
            from openai import AsyncOpenAI
            kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            return AsyncOpenAI(**kwargs)
        # Future providers: anthropic, google — return None until SDK installed
        logger.info("ai_model_registry: provider=%s not yet supported for client build", provider)
        return None
    except Exception as exc:
        logger.warning("ai_model_registry: client build failed provider=%s: %s", provider, exc)
        return None


async def _resolve_model(
    db: AsyncSession, model_id: str, stage: str, is_fallback: bool = False,
    fallback_reason: str | None = None,
) -> ResolvedModel | None:
    """Attempt to resolve a single model_id into a ResolvedModel."""
    model = await _load_registry_model(db, model_id)
    if not model:
        return None
    if not model["enabled"]:
        logger.info("ai_model_registry: model %s is disabled", model["model_name"])
        return None
    supported = list(model.get("supported_stages") or [])
    if stage not in supported and stage != "fallback":
        logger.info(
            "ai_model_registry: model %s does not support stage %s", model["model_name"], stage
        )
        return None
    api_key = await _get_api_key(db, model.get("provider_secret_key"))
    client = _build_client(model["provider"], api_key, model.get("base_url"))
    if client is None:
        return None
    return ResolvedModel(
        provider=model["provider"],
        model_name=model["model_name"],
        client=client,
        model_id=str(model["model_id"]),
        is_fallback=is_fallback,
        fallback_reason=fallback_reason,
    )


async def resolve_stage_client(
    db: AsyncSession, stage: str
) -> ResolvedModel | None:
    """
    Resolve stage → platform default model → client.

    Resolution order:
      1. ai_stage_defaults.primary_model_id → ai_model_registry → platform_secret → client
      2. ai_stage_defaults.fallback_model_id (if primary fails)
      3. Returns None → callers fall back to settings.openai_* (backward compat)

    NEVER raises.
    """
    try:
        row = await db.execute(
            text("""
                SELECT sd.primary_model_id, sd.fallback_model_id
                FROM cv_analyzer.ai_stage_defaults sd
                WHERE sd.stage = :stage
            """),
            {"stage": stage},
        )
        defaults = row.mappings().first()
        if not defaults:
            # No stage config — caller uses its own defaults
            return None

        primary_id  = str(defaults["primary_model_id"])  if defaults["primary_model_id"]  else None
        fallback_id = str(defaults["fallback_model_id"]) if defaults["fallback_model_id"] else None

        if primary_id:
            resolved = await _resolve_model(db, primary_id, stage)
            if resolved:
                return resolved
            logger.warning(
                "ai_model_registry: primary model %s failed for stage=%s, trying fallback",
                primary_id, stage,
            )

        if fallback_id and fallback_id != primary_id:
            resolved = await _resolve_model(
                db, fallback_id, stage,
                is_fallback=True,
                fallback_reason=f"primary model {primary_id} unavailable",
            )
            if resolved:
                return resolved

        logger.warning(
            "ai_model_registry: no usable model for stage=%s, caller will use defaults", stage
        )
        return None

    except Exception as exc:
        logger.warning(
            "ai_model_registry: resolve_stage_client failed for stage=%s: %s", stage, exc
        )
        return None


async def check_provider_secret_status(db: AsyncSession) -> dict[str, bool]:
    """Return {secret_key: has_value} for all provider secret keys in the registry. Never raises."""
    try:
        rows = await db.execute(
            text("""
                SELECT DISTINCT provider_secret_key
                FROM cv_analyzer.ai_model_registry
                WHERE provider_secret_key IS NOT NULL AND enabled = TRUE
            """)
        )
        keys = [r[0] for r in rows if r[0]]
        if not keys:
            return {}
        status_rows = await db.execute(
            text("SELECT key, has_value FROM cv_analyzer.platform_secrets WHERE key = ANY(:keys)"),
            {"keys": keys},
        )
        return {r["key"]: bool(r["has_value"]) for r in status_rows.mappings()}
    except Exception as exc:
        logger.warning("ai_model_registry: secret status check failed: %s", exc)
        return {}
