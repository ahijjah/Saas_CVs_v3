"""
criteria_extraction_v2 -- pinned prompt and request builder (OFFLINE ONLY: nothing here calls a model, reads the
database, or is registered as an active prompt).

  PROMPT_CODE / PROMPT_VERSION / PROMPT_SHA256   identity of the prompt text (verified on every load)
  EXTRACTION_CONFIG                              the PROPOSED call settings (not applied anywhere)
  build_user_message(jd, metadata, missed)       the user message: optional job context + the JD between markers (+ a repair note)
  build_request(jd, metadata, missed_statements) the full chat-completion arguments (pure; no client)

The prompt is a separate prompt code from the legacy `criteria_extraction`, so activating it later can never change
the extraction of legacy jobs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

PROMPT_CODE = "criteria_extraction_v2"
PROMPT_VERSION = "criteria_extraction_v2-3"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "criteria_extraction_v2-3.txt"
PROMPT_SHA256 = "f819010376ba287c8b7febb14639b2a3128d6a7a51be74a4f148d575a1fd5125"

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
    """The prompt file is not the pinned criteria_extraction_v2-3 text."""


def load_prompt() -> str:
    raw = PROMPT_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PROMPT_SHA256:
        raise PromptIntegrityError(f"{PROMPT_PATH.name} sha256 {digest} != pinned {PROMPT_SHA256}")
    return raw.decode("utf-8")


def build_user_message(jd_text: str, job_metadata: Mapping[str, Any] | None = None,
                       missed_statements: Sequence[str] = ()) -> str:
    """Job context lines (only non-empty fields) followed by the job description, verbatim, between markers. After a first answer
    that left stated sentences uncovered, `missed_statements` adds a short note naming them (one repair request, see the worker)."""
    lines: list[str] = []
    context = [f"{label}: {job_metadata.get(key)}" for label, key in _CONTEXT_FIELDS
               if job_metadata and job_metadata.get(key)]
    if context:
        lines += ["Job Context:", *context, ""]
    lines += ["Job Description (verbatim, between the markers):", "<<<JD", jd_text or "", "JD>>>"]
    if missed_statements:
        lines += ["", "Review note: a previous answer to this job description did not cover the statements below. Return the complete "
                      "JSON object again, with every requirement the description states and an item or the context object for each of "
                      "these statements (apply rules 3, 4, 14 and 15):"]
        lines += [f"- {' '.join(str(s).split())}" for s in missed_statements]
    return "\n".join(lines)


def build_messages(jd_text: str, job_metadata: Mapping[str, Any] | None = None,
                   missed_statements: Sequence[str] = ()) -> list[dict]:
    return [{"role": "system", "content": load_prompt()},
            {"role": "user", "content": build_user_message(jd_text, job_metadata, missed_statements)}]


def build_request(jd_text: str, job_metadata: Mapping[str, Any] | None = None, *, model: str | None = None,
                  missed_statements: Sequence[str] = ()) -> dict:
    return {"model": model or EXTRACTION_CONFIG["model"], "messages": build_messages(jd_text, job_metadata, missed_statements),
            "temperature": EXTRACTION_CONFIG["temperature"], "max_tokens": EXTRACTION_CONFIG["max_tokens"],
            "response_format": dict(EXTRACTION_CONFIG["response_format"])}
