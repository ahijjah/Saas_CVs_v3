"""
AI Usage & Cost Tracking Service

Logs every LLM API call to ai_usage_log with token counts, latency, and
estimated cost. Pricing is loaded from ai_model_pricing at log time so that
historical snapshots reflect the price that was active when the call was made.

CRITICAL: log_ai_usage() MUST NEVER raise. Any exception is caught and logged
so that billing failures never interrupt CV scoring.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def _load_pricing(
    db: AsyncSession,
    provider: str,
    model: str,
) -> tuple[float | None, float | None]:
    """Return (input_price_per_1m, output_price_per_1m) or (None, None) if not found."""
    try:
        row = await db.execute(
            text("""
                SELECT input_price_per_1m_tokens, output_price_per_1m_tokens
                FROM cv_analyzer.ai_model_pricing
                WHERE provider = :provider AND model = :model AND active = TRUE
                LIMIT 1
            """),
            {"provider": provider, "model": model},
        )
        r = row.mappings().first()
        if r:
            return float(r["input_price_per_1m_tokens"]), float(r["output_price_per_1m_tokens"])
    except Exception as exc:
        logger.warning("ai_usage_service: pricing lookup failed: %s", exc)
    return None, None


async def log_ai_usage(
    *,
    db: AsyncSession,
    stage: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    latency_ms: int | None = None,
    request_status: str = "success",
    retry_count: int = 0,
    error_type: str | None = None,
    error_message: str | None = None,
    tenant_id: str | None = None,
    job_id: str | None = None,
    application_id: str | None = None,
    prompt_key: str | None = None,
    prompt_version_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Persist one AI call record to ai_usage_log.

    This function NEVER raises — all exceptions are caught and logged.
    """
    try:
        input_price, output_price = await _load_pricing(db, provider, model)

        estimated_cost: float | None = None
        if input_price is not None and output_price is not None:
            estimated_cost = (
                (prompt_tokens / 1_000_000) * input_price
                + (completion_tokens / 1_000_000) * output_price
            )

        await db.execute(
            text("""
                INSERT INTO cv_analyzer.ai_usage_log (
                    tenant_id, job_id, application_id,
                    stage, provider, model,
                    prompt_key, prompt_version_id,
                    prompt_tokens, completion_tokens, total_tokens,
                    input_price_per_1m_tokens, output_price_per_1m_tokens,
                    estimated_cost_usd,
                    request_status, latency_ms, retry_count,
                    error_type, error_message,
                    metadata
                ) VALUES (
                    :tenant_id, :job_id, :application_id,
                    :stage, :provider, :model,
                    :prompt_key, :prompt_version_id,
                    :prompt_tokens, :completion_tokens, :total_tokens,
                    :input_price, :output_price,
                    :estimated_cost,
                    :request_status, :latency_ms, :retry_count,
                    :error_type, :error_message,
                    :metadata
                )
            """),
            {
                "tenant_id":       tenant_id,
                "job_id":          job_id,
                "application_id":  application_id,
                "stage":           stage,
                "provider":        provider,
                "model":           model,
                "prompt_key":      prompt_key,
                "prompt_version_id": prompt_version_id,
                "prompt_tokens":   prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens":    total_tokens,
                "input_price":     input_price,
                "output_price":    output_price,
                "estimated_cost":  estimated_cost,
                "request_status":  request_status,
                "latency_ms":      latency_ms,
                "retry_count":     retry_count,
                "error_type":      error_type,
                "error_message":   error_message,
                "metadata":        __import__("json").dumps(metadata) if metadata else None,
            },
        )
        await db.flush()
    except Exception as exc:
        logger.error(
            "ai_usage_service: log_ai_usage failed (non-critical): %s", exc, exc_info=True
        )
