"""
Shared LLM call safety for the isolated S0 / S2 experience stages.

  - model-context budget for gpt-4o-mini (OpenAI published limits: 128,000
    context tokens, 16,384 max output tokens), safe input budget
    int((128000 - 16384) * 0.9) = 100,454;
  - request_token_upper_bound(): deterministic UTF-8-byte UPPER BOUND on input
    tokens (byte-level BPE: every token covers >= 1 byte) + per-message overhead;
    no tokenizer dependency, never under-counts;
  - chat_json_call(): one JSON-mode chat call that records finish_reason and
    token usage in a call_log and returns (content, finish_reason).

Callers own the decision logic (repair, truncation, failure reasons).
Nothing here imports D-01 or production scoring code.
"""
from __future__ import annotations

MODEL_CONTEXT_TOKENS = 128_000
MAX_OUTPUT_TOKENS = 16_384
INPUT_SAFETY = 0.90
MAX_INPUT_TOKENS = int((MODEL_CONTEXT_TOKENS - MAX_OUTPUT_TOKENS) * INPUT_SAFETY)   # 100,454
PER_MESSAGE_OVERHEAD_TOKENS = 16
CLIENT_MAX_RETRIES = 1
CLIENT_TIMEOUT_S = 120.0
TOKEN_COUNT_METHOD = "utf8_bytes_upper_bound"


def request_token_upper_bound(messages: list[dict]) -> int:
    """Deterministic UPPER BOUND on a chat request's input tokens."""
    return sum(len((m.get("content") or "").encode("utf-8")) + PER_MESSAGE_OVERHEAD_TOKENS
               for m in messages)


def create_client():
    """Lazy-import AsyncOpenAI with the stage limits (1 retry, 120 s)."""
    from openai import AsyncOpenAI
    from config import get_settings
    return AsyncOpenAI(api_key=get_settings().openai_api_key,
                       max_retries=CLIENT_MAX_RETRIES, timeout=CLIENT_TIMEOUT_S)


async def chat_json_call(client, *, model: str, messages: list[dict], temperature: float,
                         max_tokens: int, call_log: list, kind: str) -> tuple[str, str | None]:
    """One JSON-mode chat call. Appends {call, finish_reason, prompt/completion/
    total tokens} to ``call_log`` (None where the API omits them)."""
    resp = await client.chat.completions.create(
        model=model, messages=messages, temperature=temperature,
        max_tokens=max_tokens, response_format={"type": "json_object"})
    choice = resp.choices[0]
    finish = getattr(choice, "finish_reason", None)
    usage = getattr(resp, "usage", None)
    call_log.append({
        "call": kind, "finish_reason": finish,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    })
    return choice.message.content or "", finish
