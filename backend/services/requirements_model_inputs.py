"""Model inputs of a requirements-v2 extraction: the exact job description and the six context fields the model receives, bound by hash.

PURE: no database, no model call, and NO import of the approved pipeline package (only the API layer, its router and the v2 extraction
service may import that package; tests/test_requirements_pipeline_service.py enforces it). The scanner that finds AI-directed text in the
context fields lives in services/requirements_v2_extraction.py, which may import the pipeline.

A stored analysis is bound to the inputs it was produced from. Nothing here rebinds a stored record: a difference between the bound inputs and the
current inputs is reported as a blocker, and the draft and the immutable original are left as they are.

Context fields are the ones build_user_message sends (services/requirements_v2/extraction/prompt.py, _CONTEXT_FIELDS). A field the prompt does not
send (empty or None) is bound as None, so an empty value and a missing value are the same input.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

MODEL_INPUTS_VERSION = "requirements-v2-model-inputs-1"
CONTEXT_SCAN_VERSION = "requirements-v2-context-scan-1"
CONTEXT_KEYS = ("title", "department", "experience_level", "location", "job_type", "work_mode")
BINDING_FIELDS = ("description", *CONTEXT_KEYS)

# blocker codes (the reasons shown to the recruiter and used by the entry guard)
DESCRIPTION_CHANGED = "description_changed"
CONTEXT_CHANGED = "context_changed"
CONTEXT_UNVERIFIED = "context_unverified"
CONTEXT_INSTRUCTION = "context_instruction"
BINDING_INVALID = "binding_invalid"
INPUTS_UNAVAILABLE = "inputs_unavailable"

# readiness states that the binding can put on a job (the approved pipeline never produces them)
STATE_INPUTS_CHANGED = "inputs_changed"
STATE_INPUTS_UNVERIFIED = "inputs_unverified"
STATE_INJECTION_REVIEW = "needs_injection_review"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sent_text(value: Any) -> str | None:
    """The text build_user_message sends for a context value: nothing when the value is empty, otherwise its string form."""
    return None if not value else str(value)


def model_inputs(description: str | None, job_metadata: Mapping[str, Any] | None) -> dict:
    """The binding block for the inputs the model will receive: a hash per field and one hash over all of them."""
    fields: dict[str, str | None] = {"description": _sha(description or "")}
    for key in CONTEXT_KEYS:
        text = sent_text((job_metadata or {}).get(key))
        fields[key] = _sha(text) if text is not None else None
    body = json.dumps({"version": MODEL_INPUTS_VERSION, "fields": fields}, sort_keys=True, separators=(",", ":"))
    return {"version": MODEL_INPUTS_VERSION, "fields": fields, "sha256": _sha(body)}


def well_formed(block: Any) -> bool:
    """A binding block whose combined hash matches its field hashes and that names every binding field."""
    if not isinstance(block, dict) or block.get("version") != MODEL_INPUTS_VERSION or not isinstance(block.get("fields"), dict):
        return False
    fields = block["fields"]
    if set(fields) != set(BINDING_FIELDS):
        return False
    if any(v is not None and not (isinstance(v, str) and len(v) == 64) for v in fields.values()):
        return False
    body = json.dumps({"version": MODEL_INPUTS_VERSION, "fields": fields}, sort_keys=True, separators=(",", ":"))
    return block.get("sha256") == _sha(body)


def changed_fields(bound: dict, current: dict) -> list[str]:
    """Binding fields whose value differs. Empty when the inputs are the same."""
    return [k for k in BINDING_FIELDS if bound["fields"].get(k) != current["fields"].get(k)]


def reason(code: str, message: str, field: str | None = None) -> dict:
    """A blocker in the same shape as the readiness reasons (code, message, category, item_id), with the field it concerns."""
    return {"code": code, "message": message, "category": None, "item_id": None, "field": field}


def input_blockers(record: Any, current_description: str | None, current_metadata: Mapping[str, Any] | None) -> list[dict]:
    """Why a stored analysis may not progress, given the job's CURRENT inputs. Empty list = bound to the current inputs with no context instruction
    open. Fails closed: unreadable inputs block. An older record (no model_inputs block) is never claimed as verified for its context."""
    if not isinstance(record, dict):
        return []
    if current_description is None and current_metadata is None:
        return [reason(INPUTS_UNAVAILABLE, "The job's current inputs could not be read, so the analysis cannot be checked against them.")]
    current = model_inputs(current_description, current_metadata)
    if "model_inputs" not in record:                                        # an analysis made before context binding
        out = []
        if _sha(current_description or "") != record.get("job_description_sha256"):
            out.append(reason(DESCRIPTION_CHANGED, "The job description changed after the analysis was made. Progression is blocked.", "description"))
        out.append(reason(CONTEXT_UNVERIFIED, "This analysis was made before the job context was bound to it, so the context it used cannot be "
                                              "verified. Progression is blocked.", None))
        return out
    bound = record["model_inputs"]
    if not well_formed(bound):
        return [reason(BINDING_INVALID, "The stored input binding is malformed. Progression is blocked.")]
    out = []
    for field in changed_fields(bound, current):
        if field == "description":
            out.append(reason(DESCRIPTION_CHANGED, "The job description changed after the analysis was made. Progression is blocked.", field))
        else:
            out.append(reason(CONTEXT_CHANGED, f"The job's {field.replace('_', ' ')} changed after the analysis was made. Progression is blocked.", field))
    cr = record.get("context_review")
    if not isinstance(cr, dict) or not isinstance(cr.get("issues"), list):
        out.append(reason(BINDING_INVALID, "The stored context review is malformed. Progression is blocked."))
    else:
        for issue in cr["issues"]:
            if issue.get("status") == "open":
                out.append(reason(CONTEXT_INSTRUCTION, f"The job's {issue['field'].replace('_', ' ')} contains text addressed to the AI system, "
                                                       "not a requirement. It is not used as a requirement.", issue["field"]))
    return out


def binding_state(blockers: list[dict]) -> str | None:
    """The readiness state these blockers put on the job, most severe first (None when there are none)."""
    codes = {b["code"] for b in blockers}
    if codes & {DESCRIPTION_CHANGED, CONTEXT_CHANGED}:
        return STATE_INPUTS_CHANGED
    if codes & {CONTEXT_UNVERIFIED, BINDING_INVALID, INPUTS_UNAVAILABLE}:
        return STATE_INPUTS_UNVERIFIED
    if CONTEXT_INSTRUCTION in codes:
        return STATE_INJECTION_REVIEW
    return None


def overlay_readiness(readiness: dict, blockers: list[dict]) -> dict:
    """The readiness shown to the recruiter, with the binding blockers applied over the pipeline's own readiness. Never more permissive."""
    if not blockers:
        return readiness
    out = dict(readiness)
    out["state"] = binding_state(blockers)
    out["can_proceed"] = False
    out["scoring_mode"] = None
    out["reasons"] = list(blockers) + [r for r in readiness.get("reasons", [])]
    return out
