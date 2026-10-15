"""
Pinned candidate_qc-1 runner (the exact validated configuration; not wired into any production flow).

  build_messages(jd_text)  -> [system = frozen prompt, user = JD in the validated wrapper]
  build_request(jd_text)   -> the full chat-completion arguments (pure; no client)
  await run_qualifying_context(jd_text, client=None) -> QCRunResult

Pinned, by design not configurable: prompt candidate_qc-1 (byte/SHA-verified on every load), model gpt-4o-mini,
temperature 0.2, max_tokens 200, response_format json_object. The input is the JD text only — no job title or
metadata, no DB prompt (load_active_prompt), no model registry (resolve_stage_client), no tenant model.
One call per run: zero SDK retries, no repair call, no fallback model. A failure is returned as
failed_technical / failed_validation with NO qualifying_context object. The OpenAI client is imported and
built lazily inside make_client(), so importing this module never loads OpenAI or makes a call.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType

from services.qualifying_context.schema import (
    ERR_OUTPUT_TRUNCATED, RUN_FAILED_TECHNICAL, RUN_FAILED_VALIDATION, RUN_OK, QCRunResult,
)
from services.qualifying_context.validation import validate_response

PROMPT_VERSION = "candidate_qc-1"
PROMPT_SHA256 = "fc979dd4a48b2aa5922c87f62e613700f898da13599e43e62a4fbc1304c851df"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "qc-1.txt"
USER_TEMPLATE = "Job description (verbatim, between the markers):\n<<<JD\n{jd}\nJD>>>"

QC_CONFIG = MappingProxyType({
    "model": "gpt-4o-mini",
    "temperature": 0.2,
    "max_tokens": 200,
    "response_format": MappingProxyType({"type": "json_object"}),
})
CLIENT_MAX_RETRIES = 0
CLIENT_TIMEOUT_S = 60.0


class PromptIntegrityError(RuntimeError):
    """The production prompt copy is not the frozen, validated candidate_qc-1."""


def load_prompt() -> str:
    raw = PROMPT_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PROMPT_SHA256:
        raise PromptIntegrityError(f"{PROMPT_PATH.name} sha256 {digest} != frozen {PROMPT_SHA256}")
    return raw.decode("utf-8")


def jd_sha256(jd_text: str) -> str:
    return hashlib.sha256(jd_text.encode("utf-8")).hexdigest()


def build_messages(jd_text: str) -> list[dict]:
    return [{"role": "system", "content": load_prompt()},
            {"role": "user", "content": USER_TEMPLATE.replace("{jd}", jd_text)}]


def build_request(jd_text: str) -> dict:
    return {"model": QC_CONFIG["model"], "messages": build_messages(jd_text),
            "temperature": QC_CONFIG["temperature"], "max_tokens": QC_CONFIG["max_tokens"],
            "response_format": dict(QC_CONFIG["response_format"])}


def make_client():
    """Lazily built async client: the project's OpenAI key, NO SDK retries, fixed timeout."""
    from openai import AsyncOpenAI
    from config import get_settings
    return AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=CLIENT_MAX_RETRIES,
                       timeout=CLIENT_TIMEOUT_S)


def _result(status: str, jd_text: str, **kw) -> QCRunResult:
    return QCRunResult(status=status, prompt_version=PROMPT_VERSION, prompt_sha256=PROMPT_SHA256,
                       model=QC_CONFIG["model"], temperature=QC_CONFIG["temperature"],
                       max_tokens=QC_CONFIG["max_tokens"], jd_sha256=jd_sha256(jd_text), **kw)


async def run_qualifying_context(jd_text: str, *, client=None) -> QCRunResult:
    """Exactly one pinned call. Never retries, never repairs, never substitutes a model or a default answer."""
    payload = build_request(jd_text)              # raises PromptIntegrityError before any call
    try:
        client = client if client is not None else make_client()
        resp = await client.chat.completions.create(**payload)
        choice = resp.choices[0]
        raw = choice.message.content
        finish = getattr(choice, "finish_reason", None)
        usage = getattr(resp, "usage", None)
        meta = {"raw": raw, "finish_reason": finish, "response_model": getattr(resp, "model", None),
                "usage": None if usage is None else {k: getattr(usage, k, None)
                                                     for k in ("prompt_tokens", "completion_tokens",
                                                               "total_tokens")}}
    except Exception as exc:                      # noqa: BLE001 - recorded as a technical failure
        return _result(RUN_FAILED_TECHNICAL, jd_text, qualifying_context=None,
                       error=f"{type(exc).__name__}: {exc}"[:500])
    if finish == "length":
        return _result(RUN_FAILED_VALIDATION, jd_text, qualifying_context=None, error=ERR_OUTPUT_TRUNCATED,
                       **meta)
    qc, err, ungrounded = validate_response(raw, jd_text)
    if err is not None:
        return _result(RUN_FAILED_VALIDATION, jd_text, qualifying_context=None, error=err,
                       ungrounded=ungrounded, **meta)
    return _result(RUN_OK, jd_text, qualifying_context=qc, error=None, **meta)
