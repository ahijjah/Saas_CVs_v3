"""
LLM provider abstraction for secondary/comparison scoring.

Currently supports:
  - DeepSeek (deepseek-chat) — OpenAI-compatible API at https://api.deepseek.com/v1

The comparison client is optional. If DEEPSEEK_API_KEY is not set in the
environment, get_comparison_client() returns None and the worker silently
skips the comparison run.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
_DEEPSEEK_MODEL    = "deepseek-chat"


@dataclass
class LLMClient:
    provider: str
    model: str
    client: Any


_comparison_client: LLMClient | None = None
_comparison_init_attempted = False


def get_comparison_client() -> LLMClient | None:
    """Return the configured secondary LLM client, or None if not configured."""
    global _comparison_client, _comparison_init_attempted

    if _comparison_init_attempted:
        return _comparison_client

    _comparison_init_attempted = True

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        logger.debug("DEEPSEEK_API_KEY not set — AI comparison mode disabled")
        return None

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=_DEEPSEEK_BASE_URL)
        _comparison_client = LLMClient(
            provider="deepseek",
            model=_DEEPSEEK_MODEL,
            client=client,
        )
        logger.info("DeepSeek comparison client initialised (model=%s)", _DEEPSEEK_MODEL)
    except Exception as exc:
        logger.warning("Could not initialise DeepSeek client: %s", exc)

    return _comparison_client
