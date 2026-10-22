"""Gate results of an application: the Gate Summary, recorded assessment / manual results, and overrides.

Every tenant role reads. Recording a result, overriding it, clearing an override and re-running the evaluation are admin and HR manager
actions (auth.roles.REQUIREMENTS_EDITOR_ROLES); a recruiter or a viewer is refused with 403 before the body is read or any row is looked
up. A missing application and another tenant's application are the same 404. Audit rows carry ids, results and counts only: no reason, no
wording, no candidate data."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.roles import RequireRequirementsEditor, RequireRequirementsReader
from database import get_db, set_rls_context
from services import eligibility_service as E
from services.audit_service import log_action

router = APIRouter(prefix="/applications", tags=["gates"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverrideRequest(_Strict):
    reason: str = Field(min_length=1, max_length=E.REASON_LIMIT)


class RecordResultRequest(_Strict):
    result: Literal["passed", "failed", "needs_verification"]
    summary: str | None = Field(default=None, max_length=E.SUMMARY_LIMIT)


def _detail(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


async def _scoped(db: AsyncSession, user, application_id: str) -> None:
    """Set the tenant context and prove the application is this tenant's; 404 otherwise."""
    try:
        import uuid
        uuid.UUID(str(application_id))
    except ValueError:
        raise _detail(404, "application_not_found", "Application not found.")
    await set_rls_context(db, user.tenant_id, user.role)
    row = (await db.execute(text("SELECT 1 FROM applications WHERE application_id = CAST(:aid AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
                            {"aid": application_id, "tid": user.tenant_id})).first()
    if row is None:
        raise _detail(404, "application_not_found", "Application not found.")


async def _audit(db: AsyncSession, user, application_id: str, action: str, details: dict[str, Any]) -> None:
    await log_action(db, user.tenant_id, user.user_id, getattr(user, "email", None), action, "application", application_id, details)


@router.get("/{application_id}/gates", dependencies=[RequireRequirementsReader])
async def get_gates(application_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        await _scoped(db, current_user, application_id)
        view = await E.gate_view(db, application_id)
    finally:
        await db.rollback()
    if view is None:
        raise _detail(404, "application_not_found", "Application not found.")
    return view


async def _write(db: AsyncSession, user, application_id: str, action: str, work, details: dict[str, Any]):
    try:
        await _scoped(db, user, application_id)
        status = await work()
        await _audit(db, user, application_id, action, {**details, "eligibility_status": status})
        await db.commit()
    except E.EligibilityError as exc:
        await db.rollback()
        raise _detail(exc.status, exc.code, str(exc)) from exc
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _detail(500, "write_failed", "The gate result could not be saved.") from exc
    return await _view_after(db, user, application_id)


async def _view_after(db: AsyncSession, user, application_id: str):
    try:
        await set_rls_context(db, user.tenant_id, user.role)
        return await E.gate_view(db, application_id)
    finally:
        await db.rollback()


@router.post("/{application_id}/gates/{gate_key}/override", dependencies=[RequireRequirementsEditor])
async def override_gate(application_id: str, gate_key: str, body: OverrideRequest, current_user: CurrentUserDep,
                        db: Annotated[AsyncSession, Depends(get_db)]):
    return await _write(db, current_user, application_id, "gate_result_overridden",
                        lambda: E.override_gate(db, application_id=application_id, gate_key=gate_key, reason=body.reason,
                                                user_id=current_user.user_id),
                        {"gate_key": gate_key})


@router.delete("/{application_id}/gates/{gate_key}/override", dependencies=[RequireRequirementsEditor])
async def clear_override(application_id: str, gate_key: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    return await _write(db, current_user, application_id, "gate_override_cleared",
                        lambda: E.clear_override(db, application_id=application_id, gate_key=gate_key, user_id=current_user.user_id),
                        {"gate_key": gate_key})


@router.post("/{application_id}/gates/{gate_key}/result", dependencies=[RequireRequirementsEditor])
async def record_gate_result(application_id: str, gate_key: str, body: RecordResultRequest, current_user: CurrentUserDep,
                             db: Annotated[AsyncSession, Depends(get_db)]):
    """Record the result of an assessment or a manual verification. This is also the API an assessment integration will call."""
    return await _write(db, current_user, application_id, "gate_result_recorded",
                        lambda: E.record_result(db, application_id=application_id, gate_key=gate_key, result=body.result,
                                                summary=body.summary, user_id=current_user.user_id),
                        {"gate_key": gate_key, "result": body.result})


@router.post("/{application_id}/gates/evaluate", dependencies=[RequireRequirementsEditor])
async def evaluate_gates(application_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    """Re-run the evaluation, including the CV evidence check."""
    return await _write(db, current_user, application_id, "gates_evaluated",
                        lambda: E.evaluate_application(db, application_id, run_cv_check=True), {})
