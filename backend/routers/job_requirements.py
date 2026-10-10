"""Requirements-v2 editing and review API (thin shell; all rules live in services/requirements_api.py).

  GET  /jobs/{job_id}/requirements                                   current, original, readiness, warnings, Edited flags
  PUT  /jobs/{job_id}/requirements                                   save the edited requirements (revision-checked)
  POST /jobs/{job_id}/requirements/classification-warnings/acknowledge   accept ONE flagged classification
  POST /jobs/{job_id}/requirements/confirm-no-numeric-score          confirm a preferred-only job without a score
  POST /jobs/{job_id}/requirements/structure-review/confirm          confirm one item's OR alternatives / experience

Writes: admin and HR manager of the job's own tenant only. Legacy jobs answer 409 and are never touched. The legacy
criteria endpoints (routers/jobs.py) are unchanged and still refuse v2 jobs; candidate evaluation of v2 jobs is still
refused by services/requirements_guard.py. Nothing here creates a v2 job or starts an extraction.
"""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.module_guards import RequireAIRecruitment
from database import get_db
from services import requirements_api as api
from services import requirements_pipeline as pipe

router = APIRouter(prefix="/jobs/{job_id}/requirements", tags=["job-requirements"], dependencies=[RequireAIRecruitment])


class _ServerOwnedEcho(BaseModel):
    """Fields a client may echo back from a GET (guard records, raw AI output, metadata, readiness, ...). They belong to the server: accepted so a
    round-tripped view is not rejected, never read, and listed in `discarded_client_fields` as body.<name>."""
    requirements_pipeline: Any = None
    pipeline: Any = None
    review_records: Any = None
    raw_response: Any = None
    raw_ai_output: Any = None
    component_versions: Any = None
    provenance: Any = None
    readiness: Any = None
    gates: Any = None
    unresolved_issues: Any = None
    normalized_warnings: Any = None
    informational: Any = None
    original: Any = None
    original_digest: Any = None
    extraction: Any = None

    def discarded(self) -> list[str]:
        return [k for k in pipe.CLIENT_FORBIDDEN_KEYS if getattr(self, k, None) is not None]


class SaveRequirementsRequest(_ServerOwnedEcho):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)         # the revision the recruiter was looking at
    requirements: dict[str, Any]


class AcknowledgeWarningRequest(_ServerOwnedEcho):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    warning_id: str
    gate: str = "classification"                       # "classification" | "conflict"; every other gate answers 409 (no acknowledgment exists)


class ConfirmStructureRequest(_ServerOwnedEcho):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    item_id: str


class ConfirmNoScoreRequest(_ServerOwnedEcho):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)


def _http(exc: api.ApiError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.detail())


@router.get("")
async def get_requirements(job_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        return await api.get_requirements(db, current_user, job_id)
    except api.ApiError as exc:
        raise _http(exc) from exc


@router.put("")
async def save_requirements(job_id: str, body: SaveRequirementsRequest, current_user: CurrentUserDep,
                            db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        return await api.save_requirements(db, current_user, job_id, body.expected_revision, body.requirements, client_discarded=body.discarded())
    except api.ApiError as exc:
        raise _http(exc) from exc


@router.post("/classification-warnings/acknowledge")
async def acknowledge_classification_warning(job_id: str, body: AcknowledgeWarningRequest,
                                             current_user: CurrentUserDep,
                                             db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        return await api.acknowledge_warning(db, current_user, job_id, body.expected_revision, body.warning_id, body.gate, client_discarded=body.discarded())
    except api.ApiError as exc:
        raise _http(exc) from exc


@router.post("/confirm-no-numeric-score")
async def confirm_no_numeric_score(job_id: str, body: ConfirmNoScoreRequest, current_user: CurrentUserDep,
                                   db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        return await api.confirm_no_score(db, current_user, job_id, body.expected_revision, client_discarded=body.discarded())
    except api.ApiError as exc:
        raise _http(exc) from exc


@router.post("/structure-review/confirm")
async def confirm_structure_review(job_id: str, body: ConfirmStructureRequest, current_user: CurrentUserDep,
                                   db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        return await api.confirm_structure_review(db, current_user, job_id, body.expected_revision, body.item_id, client_discarded=body.discarded())
    except api.ApiError as exc:
        raise _http(exc) from exc


@router.post("/extraction/retry")
async def retry_extraction(job_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    """Start a new requirements-v2 extraction attempt for a failed (or stuck) job. Admin / HR manager, feature switch on, job access checked."""
    try:
        return await api.request_extraction_retry(db, current_user, job_id)
    except api.ApiError as exc:
        raise _http(exc) from exc
