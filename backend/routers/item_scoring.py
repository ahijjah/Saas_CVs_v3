"""Item-level scoring of an application (Requirements V2): the breakdown, a reviewer's resolution of an unclear Required item, and an explicit rescore.

Every tenant role reads. Resolving an item and rescoring are admin and HR manager actions (auth.roles.REQUIREMENTS_EDITOR_ROLES): a recruiter or a
viewer is refused with 403 before the body is read or any row is looked up. A missing application and another tenant's application are the
same 404. Audit rows carry ids and results only: no reason text, no CV content. The model never produces a number: services/item_scoring."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.roles import RequireRequirementsEditor, RequireRequirementsReader
from database import get_db, set_rls_context
from routers.gates import _audit, _detail, _scoped
from services import item_scoring as _pkg  # noqa: F401  (package marker)
from services.eligibility_service import effective_decision, evaluate_application
from services.item_scoring import service as S

router = APIRouter(prefix="/applications", tags=["item-scoring"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolveRequest(_Strict):
    result: Literal["met", "partially_met", "not_met"]
    reason: str = Field(min_length=1, max_length=S.REASON_LIMIT)


async def _view(db: AsyncSession, application_id: str) -> dict:
    """The item-level breakdown, or the small legacy / none marker, always with the eligibility and effective decision."""
    view = await S.load_view(db, application_id)
    app = (await db.execute(text("""
        SELECT a.decision, a.eligibility_status, a.scoring_status, jc.requirements_revision AS current_revision,
               jc.requirements_schema_version AS schema_version,
               s.requirements_revision_used AS legacy_revision, (s.application_id IS NOT NULL) AS has_legacy
        FROM applications a LEFT JOIN job_criteria jc ON jc.job_id = a.job_id LEFT JOIN application_scores s ON s.application_id = a.application_id
        WHERE a.application_id = CAST(:aid AS uuid)"""), {"aid": application_id})).mappings().first()
    effective = effective_decision(app["eligibility_status"], app["decision"], app["scoring_status"])
    if view is None:
        legacy_stale = bool(app["has_legacy"] and app["schema_version"] == 2 and app["legacy_revision"] is not None
                            and app["current_revision"] is not None and app["legacy_revision"] != app["current_revision"])
        return {"application_id": application_id, "scoring_method": "legacy_category" if app["has_legacy"] else "none",
                "requirements_revision_used": app["legacy_revision"], "current_revision": app["current_revision"], "stale": legacy_stale,
                "decision": app["decision"], "eligibility_status": app["eligibility_status"], "effective_decision": effective}
    view["effective_decision"] = effective
    return view


@router.get("/{application_id}/item-scoring", dependencies=[RequireRequirementsReader])
async def get_item_scoring(application_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    try:
        await _scoped(db, current_user, application_id)
        return await _view(db, application_id)
    finally:
        await db.rollback()


@router.post("/{application_id}/item-scoring/items/{item_id}/resolve", dependencies=[RequireRequirementsEditor])
async def resolve_item(application_id: str, item_id: str, body: ResolveRequest, current_user: CurrentUserDep,
                       db: Annotated[AsyncSession, Depends(get_db)]):
    """Resolve an unclear Required item as met, partially met or not met, with a reason. Scores and gates are recalculated in the same
    transaction; the decision is stored only when the last unclear item is resolved."""
    try:
        await _scoped(db, current_user, application_id)
        outcome = await S.resolve_item(db, application_id=application_id, item_id=item_id, result=body.result, reason=body.reason,
                                       user_id=current_user.user_id)
        await evaluate_application(db, application_id, run_cv_check=True)      # the gates follow the stored item results: no model call
        await _audit(db, current_user, application_id, "item_result_resolved", {
            "item_id": item_id, "previous_result": outcome["previous_result"], "new_result": outcome["new_result"],
            "scoring_status": outcome["status"], "final_score": outcome["final_score"]})
        await db.commit()
    except S.ItemScoringError as exc:
        await db.rollback()
        raise _detail(exc.status, exc.code, str(exc)) from exc
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _detail(500, "write_failed", "The resolution could not be saved.") from exc
    try:
        await set_rls_context(db, current_user.tenant_id, current_user.role)
        return await _view(db, application_id)
    finally:
        await db.rollback()


@router.post("/{application_id}/item-scoring/rescore", status_code=202, dependencies=[RequireRequirementsEditor])
async def rescore(application_id: str, current_user: CurrentUserDep, db: Annotated[AsyncSession, Depends(get_db)]):
    """Queue a rescore of this one application against the CURRENT requirements revision. Earlier scoring stays as history; no other
    application is touched."""
    from services.requirements_v2.scoring import ScoringNotReady, scoring_inputs
    from workers.cv_score import rescore_application_task
    try:
        await _scoped(db, current_user, application_id)
        row = (await db.execute(text("""
            SELECT a.job_id::text AS job_id, jc.analysis_json, jc.original_analysis_json, jc.requirements_revision, jc.requirements_schema_version,
                   j.description,
                   EXISTS (SELECT 1 FROM application_files f WHERE f.application_id = a.application_id AND f.extraction_status = 'done'
                           AND f.extracted_text IS NOT NULL AND length(btrim(f.extracted_text)) > 0) AS has_text
            FROM applications a JOIN jobs j ON j.job_id = a.job_id JOIN job_criteria jc ON jc.job_id = a.job_id
            WHERE a.application_id = CAST(:aid AS uuid)"""), {"aid": application_id})).mappings().first()
        if row is None or row["requirements_schema_version"] != 2:
            raise _detail(409, "not_requirements_v2", "Only a Requirements V2 job can be scored at item level.")
        if not row["has_text"]:
            raise _detail(409, "no_cv_text", "No extracted CV text is stored for this application.")
        try:
            scoring_inputs(row["analysis_json"], row["original_analysis_json"], row["description"] or "", row["requirements_revision"])
        except ScoringNotReady as exc:
            raise _detail(409, "requirements_not_ready", f"The requirements are not ready for scoring ({exc.state}).") from exc
        await _audit(db, current_user, application_id, "item_rescore_requested", {"requirements_revision": row["requirements_revision"]})
        await db.commit()
        job_id = row["job_id"]
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _detail(500, "write_failed", "The rescore could not be requested.") from exc
    rescore_application_task.delay(application_id, job_id, current_user.tenant_id, current_user.user_id)
    return {"queued": True}
