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


def assert_job_evaluable(*, entry: str, marker: Any = None, analysis_json: Any = None,
                         job_id: str | None = None) -> None:
    """Called at evaluation entry points. Refuses a v2 job unless v2 evaluation is supported (it is not yet)."""
    if is_requirements_v2(marker, analysis_json) and not REQUIREMENTS_V2_SCORING_SUPPORTED:
        raise UnsupportedEvaluationError(entry, job_id)


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


async def ensure_job_evaluable(db: Any, job_id: str, entry: str) -> None:
    """Load the job's marker and refuse a v2 job at an evaluation entry point (raises UnsupportedEvaluationError)."""
    marker, analysis = await load_job_marker(db, job_id)
    assert_job_evaluable(entry=entry, marker=marker, analysis_json=analysis, job_id=str(job_id))


async def ensure_job_legacy(db: Any, job_id: str, component: str) -> None:
    """Like ensure_job_evaluable, for legacy-only code paths (edits, legacy extraction): always refuses v2."""
    marker, analysis = await load_job_marker(db, job_id)
    assert_legacy_component(component=component, marker=marker, analysis_json=analysis, job_id=str(job_id))
