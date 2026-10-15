"""
Requirements-v2 evaluation guard.

A job whose analysis uses the requirements-v2 format (marker job_criteria.requirements_schema_version, or an
analysis_json carrying a v2 "requirements" block) must never enter the LEGACY evaluation pipeline: the gatekeeper,
the criteria matcher, the LLM criteria mapper and the legacy LLM scorer all read legacy keys and would silently
produce zeros or wrong scores. Candidate evaluation for v2 is not implemented, so every evaluation entry point
refuses such a job with an explicit reason instead of scoring it, retrying it or falling back.

Two kinds of check, one source of truth:

  assert_job_evaluable(...)      for ENTRY points (intake, bulk import, batch scoring, the scoring task).
                                 Refuses v2 unless REQUIREMENTS_V2_SCORING_SUPPORTED is True. That constant is a CODE
                                 switch (changing it needs a deployment) and stays False until a compatible v2
                                 evaluation exists.
  assert_legacy_component(...)   for LEGACY components. Always refuses v2, whatever the switch says: these components
                                 can never evaluate v2 requirements, so a future v2 evaluation path must not call them.

Existing jobs have a NULL marker and a legacy analysis, so none of this changes their behavior. The SQL helpers read
the marker through to_jsonb(), so they also work before the migration that adds the column has been applied.

This module is deliberately independent of services.requirements_v2.
"""
from __future__ import annotations

from typing import Any

from services import requirements_model_inputs as NI

# CODE kill switch for v2 candidate evaluation. Flip only together with a compatible evaluation implementation.
REQUIREMENTS_V2_SCORING_SUPPORTED = False

SCHEMA_VERSION_V2 = 2

REASON_CODE = "requirements_v2_evaluation_unsupported"
STOPPED_REASON = "evaluation_unsupported"          # applications.stopped_reason (migration 106)

# Reads the marker without referencing the column by name: NULL when the column does not exist yet.
# Requires the job_criteria row to be aliased `jc`.
MARKER_SQL = "to_jsonb(jc) ->> 'requirements_schema_version'"
# SQL predicate: this job_criteria row (alias jc) is a v2 job (marker set, or a v2 block inside the analysis).
IS_V2_SQL = (
    "(" + MARKER_SQL + " IS NOT NULL OR "
    "(jc.analysis_json -> 'requirements' ->> 'schema_version') = '2')"
)


class UnsupportedEvaluationError(Exception):
    """A requirements-v2 job reached a legacy evaluation path. Permanent: never retried, never degraded."""

    def __init__(self, where: str, job_id: str | None = None, detail: str | None = None):
        self.code = REASON_CODE
        self.where = where
        self.job_id = job_id
        self.detail = detail
        super().__init__(self.message)

    @property
    def message(self) -> str:
        job = f" (job {self.job_id})" if self.job_id else ""
        extra = f" {self.detail}" if self.detail else ""
        return (f"{REASON_CODE}: this job uses the requirements-v2 format{job}, which candidate evaluation does not "
                f"support yet; refused at {self.where}.{extra}")


def marker_is_set(marker: Any) -> bool:
    """Any non-NULL marker means 'not legacy' (fail closed: an unknown version is also refused)."""
    return marker is not None and marker != ""


def analysis_declares_v2(analysis_json: Any) -> bool:
    """True when the analysis carries a v2 requirements block. Legacy analyses never do."""
    if not isinstance(analysis_json, dict):
        return False
    block = analysis_json.get("requirements")
    return isinstance(block, dict) and block.get("schema_version") == SCHEMA_VERSION_V2


def is_requirements_v2(marker: Any = None, analysis_json: Any = None) -> bool:
    return marker_is_set(marker) or analysis_declares_v2(analysis_json)


def assert_legacy_component(*, component: str, marker: Any = None, analysis_json: Any = None,
                            job_id: str | None = None) -> None:
    """Called at the top of legacy evaluation components. Always refuses a v2 job."""
    if is_requirements_v2(marker, analysis_json):
        raise UnsupportedEvaluationError(component, job_id)


class InjectionReviewRequiredError(UnsupportedEvaluationError):
    """A requirements-v2 job whose server-owned injection review is still open. Permanent, like its parent: never retried or degraded."""

    def __init__(self, where: str, job_id: str | None = None, detail: str | None = None):
        super().__init__(where, job_id, detail)
        self.code = INJECTION_REVIEW_REASON_CODE


INJECTION_REVIEW_REASON_CODE = "requirements_v2_injection_review_required"

# Copied from services/requirements_pipeline/injection.py (approved). This module must not import the pipeline package (only the API layer and
# its router may; tests/test_requirements_pipeline_service.py enforces it). tests/test_requirements_v2_live_injection_gate.py checks these
# constants and the open-issue rule against the approved implementation, so a change there fails loudly here.
_INJECTION_GUARD_VERSION = "requirements-v2-injection-guard-1.1"
_ISSUE_REQUIREMENT, _ISSUE_WEIGHTS = "requirement", "weights"
_STATUS_OPEN = "open"


def _injection_issue_is_open(issue: dict, doc: dict) -> bool:
    """The approved rule, re-derived from the stored document: a requirement issue is open while its item is in the document; a weights issue
    is open while its category's weight is the contaminated applied value or the directed (proposed) value."""
    if issue["kind"] == _ISSUE_REQUIREMENT:
        return any(i.get("id") == issue.get("item_id") for cat in doc.get("categories", {}).values() for i in cat.get("items", []))
    weight = doc["categories"][issue["category"]].get("weight")
    return weight == issue["contaminated_applied_weight"] or weight == issue["proposed_weight"]


def v2_injection_blockers(analysis_json: Any) -> list[str]:
    """Why the injection gate of a v2 job still blocks, as reasons; empty when no injection issue is open.
    Fails closed: a missing or invalid review record, or a missing document, blocks. It reads only the server-owned record and the stored
    document, and derives open issues from that document (no stored status can close one)."""
    analysis = analysis_json
    if isinstance(analysis, str):
        import json
        analysis = json.loads(analysis)
    if not isinstance(analysis, dict):
        return ["no analysis document"]
    doc = analysis.get("requirements")
    record = analysis.get("requirements_pipeline")
    if not isinstance(doc, dict) or not isinstance(doc.get("categories"), dict):
        return ["the requirements document is missing"]
    if not isinstance(record, dict):
        return ["the server-owned extraction record is missing"]
    review = (record.get("review_records") or {}).get("injection") if isinstance(record.get("review_records"), dict) else None
    if not isinstance(review, dict) or review.get("guard_version") != _INJECTION_GUARD_VERSION or not isinstance(review.get("issues"), list):
        return ["the injection review record is missing or invalid"]
    for issue in review["issues"]:
        if issue.get("kind") not in (_ISSUE_REQUIREMENT, _ISSUE_WEIGHTS) or (issue.get("kind") == _ISSUE_WEIGHTS and (
                issue.get("category") not in doc["categories"] or "contaminated_applied_weight" not in issue or "proposed_weight" not in issue)):
            return ["the injection review record is invalid"]
    return [f"open injection issue {i['id']} ({i['kind']}, category {i.get('category')})"
            for i in review["issues"] if _injection_issue_is_open(i, doc)]


def assert_job_evaluable(*, entry: str, marker: Any = None, analysis_json: Any = None,
                         job_id: str | None = None, current_inputs: dict | None = None) -> None:
    """Called at evaluation entry points. Refuses a v2 job unless v2 evaluation is supported (it is not yet). Once it is supported, the
    server-owned injection gate still applies: an open injection issue (or a missing or invalid review record) refuses the job, so the switch
    alone cannot release a draft that the injection review has not cleared."""
    if not is_requirements_v2(marker, analysis_json):
        return
    if not REQUIREMENTS_V2_SCORING_SUPPORTED:
        raise UnsupportedEvaluationError(entry, job_id)
    blockers = v2_injection_blockers(analysis_json)
    if blockers:
        raise InjectionReviewRequiredError(entry, job_id, detail=f"Blocked by: {'; '.join(blockers)}.")
    # the analysis must still be bound to the job's CURRENT description and context; the caller supplies them (fail closed without them)
    if not isinstance(current_inputs, dict):
        raise InjectionReviewRequiredError(entry, job_id, detail="Blocked by: the job's current inputs were not supplied.")
    record = (analysis_json if isinstance(analysis_json, dict) else {}).get("requirements_pipeline")
    bound = NI.input_blockers(record, current_inputs.get("description"), current_inputs.get("job_metadata"))
    if bound:
        raise InjectionReviewRequiredError(entry, job_id, detail=f"Blocked by: {'; '.join(b['message'] for b in bound)}")


_LOAD_SQL = f"""
    SELECT {MARKER_SQL} AS requirements_schema_version, jc.analysis_json
    FROM job_criteria jc
    WHERE jc.job_id = CAST(:jid AS uuid)
"""


async def load_job_marker(db: Any, job_id: str) -> tuple[Any, Any]:
    """(marker, analysis_json) of a job; (None, None) when the job has no criteria row."""
    from sqlalchemy import text
    row = (await db.execute(text(_LOAD_SQL), {"jid": str(job_id)})).mappings().first()
    if not row:
        return None, None
    return row.get("requirements_schema_version"), row.get("analysis_json")


async def load_job_inputs(db: Any, job_id: str) -> dict | None:
    """The job's description and six context fields as they are now (the inputs the analysis must be bound to)."""
    from sqlalchemy import text
    row = (await db.execute(text("""
        SELECT description, title, department, experience_level, location, job_type, work_mode
        FROM jobs WHERE job_id = CAST(:jid AS uuid)"""), {"jid": str(job_id)})).mappings().first()
    if not row:
        return None
    return {"description": row["description"], "job_metadata": {k: row[k] for k in NI.CONTEXT_KEYS}}


async def ensure_job_evaluable(db: Any, job_id: str, entry: str) -> None:
    """Load the job's marker and inputs and refuse a v2 job at an evaluation entry point (raises UnsupportedEvaluationError)."""
    marker, analysis = await load_job_marker(db, job_id)
    inputs = await load_job_inputs(db, job_id) if (REQUIREMENTS_V2_SCORING_SUPPORTED and is_requirements_v2(marker, analysis)) else None
    assert_job_evaluable(entry=entry, marker=marker, analysis_json=analysis, job_id=str(job_id), current_inputs=inputs)


async def ensure_job_legacy(db: Any, job_id: str, component: str) -> None:
    """Like ensure_job_evaluable, for legacy-only code paths (edits, legacy extraction): always refuses v2."""
    marker, analysis = await load_job_marker(db, job_id)
    assert_legacy_component(component=component, marker=marker, analysis_json=analysis, job_id=str(job_id))
