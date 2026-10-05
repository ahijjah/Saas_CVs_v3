"""
Qualifying-context persistence: pure merge of one criteria-extraction run into job_criteria.analysis_json
(Architecture C phase P2). No I/O, no model call; the criteria worker supplies the values it read INSIDE its
locked persistence transaction.

  merge_qualifying_context(existing, fresh, run, *, generated_at, current_jd_sha256) -> MergeOutcome
      .analysis   -> written to analysis_json            (:aj)
      .original   -> COALESCE candidate for original_analysis_json (:orig); AI-only, never recruiter data

Stored keys:
  analysis_json.experience.qualifying_context   the frozen {state, contexts, source} object (absent = not assessed)
  analysis_json.qualifying_context_audit         {"schema": "qc_audit_v1", "current": ..., "latest_run": ...}
      current      describes the qualifying_context actually stored (null when none is stored)
      latest_run   the most recent attempted run, applied or not, with the AI reading in "result"

Rules (first match wins once a run exists):
  any qualifying_context / qualifying_context_audit in the main extraction output is discarded
  no run (flag OFF, or main analysis insufficient) -> existing object and audit carried over exactly
  existing object with source "recruiter"          -> kept byte-for-byte; not_applied_reason recruiter_owned
  run failed (technical or validation)             -> previous object and audit.current kept; run_failed
  run ok but the JD changed while the task ran     -> previous object and audit.current kept; jd_changed
  run ok and the JD is unchanged                   -> the AI object is stored; audit.current = this run
A failure never creates, replaces or removes a qualifying_context, and nothing here ever produces state "none"
or "uncertain" on its own.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from services.qualifying_context.runner import (
    PROMPT_SHA256, PROMPT_VERSION, QC_CONFIG, jd_sha256,
)
from services.qualifying_context.schema import (
    RUN_FAILED_TECHNICAL, RUN_FAILED_VALIDATION, SOURCE_ANALYSIS, SOURCE_RECRUITER, QCRunResult,
)

AUDIT_KEY = "qualifying_context_audit"
QC_KEY = "qualifying_context"
AUDIT_SCHEMA = "qc_audit_v1"
RAW_MAX_CHARS = 2000
ERROR_MAX_CHARS = 500

REASON_RECRUITER_OWNED = "recruiter_owned"
REASON_JD_CHANGED = "jd_changed"
REASON_RUN_FAILED = "run_failed"
NOT_APPLIED_REASONS = (REASON_RECRUITER_OWNED, REASON_JD_CHANGED, REASON_RUN_FAILED, None)


@dataclass(frozen=True)
class MergeOutcome:
    analysis: dict
    original: dict
    applied: bool
    not_applied_reason: str | None


def technical_failure(jd_text: str, error: str) -> QCRunResult:
    """A failed_technical result for an exception raised outside the runner (e.g. PromptIntegrityError)."""
    return QCRunResult(status=RUN_FAILED_TECHNICAL, qualifying_context=None,
                       error=(error or "unknown error")[:ERROR_MAX_CHARS], prompt_version=PROMPT_VERSION,
                       prompt_sha256=PROMPT_SHA256, model=QC_CONFIG["model"],
                       temperature=QC_CONFIG["temperature"], max_tokens=QC_CONFIG["max_tokens"],
                       jd_sha256=jd_sha256(jd_text))


def strip_ai_supplied(analysis: dict) -> dict:
    """A deep copy without any qualifying-context keys the main extraction model may have emitted."""
    out = copy.deepcopy(analysis) if isinstance(analysis, dict) else {}
    out.pop(AUDIT_KEY, None)
    exp = out.get("experience")
    if isinstance(exp, dict):
        exp.pop(QC_KEY, None)
    return out


def _experience(d: dict) -> dict:
    exp = d.get("experience")
    if not isinstance(exp, dict):
        exp = {}
        d["experience"] = exp
    return exp


def run_record(run: QCRunResult, generated_at: str) -> dict[str, Any]:
    """audit.latest_run without the applied / not_applied_reason fields."""
    raw = run.raw[:RAW_MAX_CHARS] if run.status == RUN_FAILED_VALIDATION and run.raw is not None else None
    return {
        "status": run.status,
        "error": None if run.error is None else run.error[:ERROR_MAX_CHARS],
        "prompt_version": run.prompt_version, "prompt_sha256": run.prompt_sha256, "model": run.model,
        "temperature": run.temperature, "max_tokens": run.max_tokens, "jd_sha256": run.jd_sha256,
        "generated_at": generated_at, "finish_reason": run.finish_reason, "response_model": run.response_model,
        "usage": run.usage, "raw": raw, "ungrounded": list(run.ungrounded),
        "result": run.qualifying_context.to_dict() if run.ok else None,
    }


def _current_for(run: QCRunResult, generated_at: str) -> dict[str, Any]:
    return {"source": SOURCE_ANALYSIS, "prompt_version": run.prompt_version, "prompt_sha256": run.prompt_sha256,
            "model": run.model, "temperature": run.temperature, "max_tokens": run.max_tokens,
            "jd_sha256": run.jd_sha256, "generated_at": generated_at}


def _carried_current(prev_qc: Any, prev_audit: Any) -> dict | None:
    """audit.current for a kept object: the existing description when there is one."""
    if isinstance(prev_audit, dict) and "current" in prev_audit:
        return copy.deepcopy(prev_audit["current"])
    if isinstance(prev_qc, dict):
        return {"source": prev_qc.get("source")}
    return None


def merge_qualifying_context(existing: dict | None, fresh: dict, run: QCRunResult | None, *,
                             generated_at: str | None, current_jd_sha256: str | None) -> MergeOutcome:
    existing = existing if isinstance(existing, dict) else {}
    base = strip_ai_supplied(fresh)
    prev_exp = existing.get("experience") if isinstance(existing.get("experience"), dict) else {}
    has_prev_qc, prev_qc = QC_KEY in prev_exp, prev_exp.get(QC_KEY)
    has_prev_audit, prev_audit = AUDIT_KEY in existing, existing.get(AUDIT_KEY)

    out = copy.deepcopy(base)
    if has_prev_qc:
        _experience(out)[QC_KEY] = copy.deepcopy(prev_qc)
    if has_prev_audit:
        out[AUDIT_KEY] = copy.deepcopy(prev_audit)

    if run is None:                                   # no QC run: existing state carried over exactly
        return MergeOutcome(out, copy.deepcopy(base), False, None)

    rec = run_record(run, generated_at or "")
    if isinstance(prev_qc, dict) and prev_qc.get("source") == SOURCE_RECRUITER:
        applied, reason = False, REASON_RECRUITER_OWNED
    elif not run.ok:
        applied, reason = False, REASON_RUN_FAILED
    elif current_jd_sha256 != run.jd_sha256:
        applied, reason = False, REASON_JD_CHANGED
    else:
        applied, reason = True, None

    if applied:
        _experience(out)[QC_KEY] = run.qualifying_context.to_dict()
        current = _current_for(run, generated_at or "")
    else:
        current = _carried_current(prev_qc, prev_audit) if has_prev_qc else None
    out[AUDIT_KEY] = {"schema": AUDIT_SCHEMA, "current": current,
                      "latest_run": {**rec, "applied": applied, "not_applied_reason": reason}}

    # original_analysis_json candidate: what this AI run alone produced (no prior state, never recruiter data)
    original = copy.deepcopy(base)
    original[AUDIT_KEY] = {"schema": AUDIT_SCHEMA,
                           "current": _current_for(run, generated_at or "") if run.ok else None,
                           "latest_run": {**rec, "applied": run.ok,
                                          "not_applied_reason": None if run.ok else REASON_RUN_FAILED}}
    if run.ok:
        _experience(original)[QC_KEY] = run.qualifying_context.to_dict()
    return MergeOutcome(out, original, applied, reason)
