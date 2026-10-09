"""Requirements-v2 editing and review API (thin shell; all rules live in services/requirements_api.py).

  GET  /jobs/{job_id}/requirements                                   current, original, readiness, warnings, Edited flags
  PUT  /jobs/{job_id}/requirements                                   save the edited requirements (revision-checked)
  POST /jobs/{job_id}/requirements/classification-warnings/acknowledge   accept ONE flagged classification
  POST /jobs/{job_id}/requirements/confirm-no-numeric-score          confirm a preferred-only job without a score

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

router = APIRouter(prefix="/jobs/{job_id}/requirements", tags=["job-requirements"], dependencies=[RequireAIRecruitment])


class SaveRequirementsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)         # the revision the recruiter was looking at
    requirements: dict[str, Any]


class AcknowledgeWarningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    warning_id: str


class ConfirmNoScoreRequest(BaseModel):
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
        return await api.save_requirements(db, current_user, job_id, body.expected_revision, body.requirements)
    except api.ApiError as exc:
        raise _http(exc) from exc


@router.post("/classification-warnings/acknowledge")
async def acknowledge_classification_warning(job_id: str, body: AcknowledgeWarningRequest,
                                             current_user: CurrentUserDep,
                                             db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        return await api.acknowledge_warning(db, current_user, job_id, body.expected_revision, body.warning_id)
    except api.ApiError as exc:
        raise _http(exc) from exc


@router.post("/confirm-no-numeric-score")
async def confirm_no_numeric_score(job_id: str, body: ConfirmNoScoreRequest, current_user: CurrentUserDep,
                                   db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        return await api.confirm_no_score(db, current_user, job_id, body.expected_revision)
    except api.ApiError as exc:
        raise _http(exc) from exc
