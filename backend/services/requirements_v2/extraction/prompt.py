"""
criteria_extraction_v2 -- pinned prompt and request builder (OFFLINE ONLY: nothing here calls a model, reads the
database, or is registered as an active prompt).

  PROMPT_CODE / PROMPT_VERSION / PROMPT_SHA256   identity of the prompt text (verified on every load)
  EXTRACTION_CONFIG                              the PROPOSED call settings (not applied anywhere)
  build_user_message(jd, metadata)               the user message: optional job context + the JD between markers
  build_request(jd, metadata)                    the full chat-completion arguments (pure; no client)

The prompt is a separate prompt code from the legacy `criteria_extraction`, so activating it later can never change
the extraction of legacy jobs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

PROMPT_CODE = "criteria_extraction_v2"
PROMPT_VERSION = "criteria_extraction_v2-1"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "criteria_extraction_v2-1.txt"
PROMPT_SHA256 = "f2017d2879554935baa5d0d4f51d53fa0600e18858cc875d4a791c5ac0678b04"

# Proposed settings. The model itself is expected to come from the model registry (stage cv_analyzer) later.
EXTRACTION_CONFIG = MappingProxyType({
    "model": "gpt-4o-mini",
    "temperature": 0.1,
    "max_tokens": 6000,          # explicit: the legacy extraction call sends none. finish_reason "length" is a failure.
    "response_format": MappingProxyType({"type": "json_object"}),
})

_CONTEXT_FIELDS = (
    ("Job Title", "title"), ("Department", "department"), ("Seniority Level", "experience_level"),
    ("Location", "location"), ("Employment Type", "job_type"), ("Work Mode", "work_mode"),
)


class PromptIntegrityError(RuntimeError):
    """The prompt file is not the pinned criteria_extraction_v2-1 text."""


def load_prompt() -> str:
    raw = PROMPT_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PROMPT_SHA256:
        raise PromptIntegrityError(f"{PROMPT_PATH.name} sha256 {digest} != pinned {PROMPT_SHA256}")
    return raw.decode("utf-8")


def build_user_message(jd_text: str, job_metadata: Mapping[str, Any] | None = None) -> str:
    """Job context lines (only non-empty fields) followed by the job description, verbatim, between markers."""
    lines: list[str] = []
    context = [f"{label}: {job_metadata.get(key)}" for label, key in _CONTEXT_FIELDS
               if job_metadata and job_metadata.get(key)]
    if context:
        lines += ["Job Context:", *context, ""]
    lines += ["Job Description (verbatim, between the markers):", "<<<JD", jd_text or "", "JD>>>"]
    return "\n".join(lines)


def build_messages(jd_text: str, job_metadata: Mapping[str, Any] | None = None) -> list[dict]:
    return [{"role": "system", "content": load_prompt()},
            {"role": "user", "content": build_user_message(jd_text, job_metadata)}]


def build_request(jd_text: str, job_metadata: Mapping[str, Any] | None = None, *, model: str | None = None) -> dict:
    return {"model": model or EXTRACTION_CONFIG["model"], "messages": build_messages(jd_text, job_metadata),
            "temperature": EXTRACTION_CONFIG["temperature"], "max_tokens": EXTRACTION_CONFIG["max_tokens"],
            "response_format": dict(EXTRACTION_CONFIG["response_format"])}
