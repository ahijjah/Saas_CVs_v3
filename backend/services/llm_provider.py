"""
LLM provider abstraction for secondary/comparison scoring.

Supports any OpenAI-compatible API.  Configuration is read from system_config
(DB) at task execution time so super admins can change settings without
restarting the service.

Fallback: if system_config is unavailable, falls back to env vars
(DEEPSEEK_API_KEY / SECONDARY_LLM_API_KEY).

System config keys (all in cv_analyzer.system_config):
  ai_comparison_mode_enabled  — 'true' / 'false'
  secondary_llm_provider      — provider name label (e.g. 'deepseek')
  secondary_llm_base_url      — OpenAI-compatible base URL
  secondary_llm_model         — model identifier
  secondary_llm_temperature   — float string (default '0.2')
  secondary_llm_max_tokens    — int string (default '2048')

Platform secret:
  SECONDARY_LLM_API_KEY       — stored in platform_secrets.value
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
_DEFAULT_MODEL    = "deepseek-chat"
_DEFAULT_PROVIDER = "deepseek"


@dataclass
class LLMClient:
    provider: str
    model: str
    client: Any


async def get_comparison_client_async(db) -> LLMClient | None:
    """Return secondary LLM client configured in system_config/platform_secrets.

    Reads live DB config so changes take effect on next scoring run without
    requiring a service restart.  Returns None if comparison mode is disabled
    or no API key is configured.
    """
    from sqlalchemy import text

    try:
        # Load all relevant system_config keys in one query
        cfg_rows = await db.execute(
            text("""
                SELECT key, value FROM system_config
                WHERE key IN (
                    'ai_comparison_mode_enabled',
                    'secondary_llm_provider',
                    'secondary_llm_base_url',
                    'secondary_llm_model',
                    'secondary_llm_temperature',
                    'secondary_llm_max_tokens'
                )
            """)
        )
        cfg = {r["key"]: r["value"] for r in cfg_rows.mappings()}

        enabled = (cfg.get("ai_comparison_mode_enabled") or "false").lower() == "true"
        if not enabled:
            logger.debug("AI comparison mode disabled in system_config")
            return None

        provider = cfg.get("secondary_llm_provider") or _DEFAULT_PROVIDER
        base_url = cfg.get("secondary_llm_base_url")  or _DEFAULT_BASE_URL
        model    = cfg.get("secondary_llm_model")      or _DEFAULT_MODEL

        # Load API key from platform_secrets (DB) first, then env vars as fallback
        key_row = await db.execute(
            text("SELECT value FROM platform_secrets WHERE key = 'SECONDARY_LLM_API_KEY'")
        )
        db_key = (key_row.scalar_one_or_none() or "").strip()

        api_key = (
            db_key
            or os.environ.get("SECONDARY_LLM_API_KEY", "").strip()
            or os.environ.get("DEEPSEEK_API_KEY", "").strip()
        )

        if not api_key:
            logger.debug("No secondary LLM API key found — comparison disabled")
            return None

        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        logger.info(
            "Secondary LLM client ready: provider=%s model=%s base_url=%s",
            provider, model, base_url,
        )
        return LLMClient(provider=provider, model=model, client=client)

    except Exception as exc:
        logger.warning("Could not initialise secondary LLM client: %s", exc)
        return None


def get_comparison_client() -> LLMClient | None:
    """Synchronous fallback — reads only from environment variables.

    Kept for backward compatibility.  Prefer get_comparison_client_async(db)
    in async worker contexts.
    """
    api_key = (
        os.environ.get("SECONDARY_LLM_API_KEY", "").strip()
        or os.environ.get("DEEPSEEK_API_KEY", "").strip()
    )
    if not api_key:
        logger.debug("No secondary LLM API key in env — comparison disabled")
        return None

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=_DEFAULT_BASE_URL)
        return LLMClient(provider=_DEFAULT_PROVIDER, model=_DEFAULT_MODEL, client=client)
    except Exception as exc:
        logger.warning("Could not initialise secondary LLM client (env fallback): %s", exc)
        return None
