"""Authenticated requirements-v2 editing API: GET a document for editing, and POST a list of typed operations to save it.

Admin and HR manager edit; every tenant role reads. Every decision that touches requirements data is made by services/requirements_edit.py,
which checks the role before any SQL. This module maps the service's typed errors to stable `detail.code` responses, builds the
read response from the approved whitelist, and owns the transaction: a read ends with a rollback, and a save commits only after the
save and its audit row have both succeeded.

Request and response models forbid extra fields. A read-only or server-owned field in a request is refused, never stored."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, StrictInt, model_serializer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.roles import RequireRequirementsEditor, RequireRequirementsReader
from database import get_db
from services import requirements_edit as E

router = APIRouter(prefix="/jobs", tags=["requirements"])

CategoryName = Literal["skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge",
                       "other_requirements"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- request ----

class SaveEditsRequest(_Strict):
    client_revision: StrictInt = Field(ge=0)
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=E.MAX_OPERATIONS)


# ---- response: the document (whitelisted fields only) ----

class ExperienceOut(_Strict):
    subject: str | None
    min_years: int | None


class GateOut(_Strict):
    """A confirmed Mandatory Gate. The confirming user is never returned."""
    mandatory: Literal[True]
    verification_method: Literal["cv_evidence", "candidate_question", "assessment", "manual"] | None
    failure_action: Literal["not_eligible", "hold_for_review"]
    linked_question_id: str | None
    confirmed_at: str


class GateSuggestionOut(_Strict):
    """What the extraction suggested from the job description's own explicit wording. It enforces nothing."""
    cue: str
    source_text: str | None


class ItemOut(_Strict):
    id: str
    text: str
    importance: Literal["required", "preferred"]
    weight: int | None
    origin: Literal["stated", "from_responsibilities", "recruiter_added"]
    source_text: str | None
    alternatives: list[str] | None
    experience: ExperienceOut | None
    gate: GateOut | None = None
    gate_suggestion: GateSuggestionOut | None = None


class CategoryOut(_Strict):
    weight: int
    items: list[ItemOut]


class ContextOut(_Strict):
    """Optional job context. Read-only and never scored."""
    location: str | None
    work_arrangement: Literal["onsite", "hybrid", "remote"] | None
    reports_to: str | None


class DocumentOut(_Strict):
    schema_version: int
    categories: dict[CategoryName, CategoryOut]
    context: ContextOut | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_context(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """A document without job context keeps the exact response shape it had before the context block existed."""
        data = handler(self)
        if data.get("context") is None:
            data.pop("context", None)
        return data


# ---- response: the read-only review, one section per EditView part ----

class ReasonOut(_Strict):
    code: str
    message: str
    category: str | None
    item_id: str | None


class ReadinessOut(_Strict):
    state: str
    can_proceed: bool
    scoring_mode: str | None
    reasons: list[ReasonOut]


class ConfirmationOut(_Strict):
    status: str
    confirmed_at: str | None


class WarningOut(_Strict):
    id: str
    code: str
    item_id: str
    category: str
    acknowledged: bool


class ClassificationOut(_Strict):
    acknowledgment_required: bool
    open_warnings: list[WarningOut]


class StructureOut(_Strict):
    pending_item_ids: list[str]


class FindingOut(_Strict):
    id: str
    code: str
    item_ids: list[str]
    source_text: str
    message: str


class StructuralOut(_Strict):
    status: str
    open_findings: list[FindingOut]


class ReviewOut(_Strict):
    readiness: ReadinessOut
    confirmation: ConfirmationOut
    classification: ClassificationOut
    structure: StructureOut
    structural: StructuralOut


class EditResponse(_Strict):
    job_id: str
    requirements_revision: int
    schema_version: int
    document: DocumentOut
    review: ReviewOut


class SaveResponse(_Strict):
    requirements_revision: int


def _edit_response(view: E.EditView) -> EditResponse:
    """The public read contract, built section by section from the view. Nothing outside these fields can be returned."""
    return EditResponse(
        job_id=view.job_id,
        requirements_revision=view.requirements_revision,
        schema_version=view.schema_version,
        document=DocumentOut.model_validate(view.document),
        review=ReviewOut(
            readiness=ReadinessOut(
                state=view.readiness.state,
                can_proceed=view.readiness.can_proceed,
                scoring_mode=view.readiness.scoring_mode,
                reasons=[ReasonOut(code=r.code, message=r.message, category=r.category, item_id=r.item_id)
                         for r in view.readiness.reasons],
            ),
            confirmation=ConfirmationOut(status=view.confirmation.status, confirmed_at=view.confirmation.confirmed_at),
            classification=ClassificationOut(
                acknowledgment_required=view.classification.acknowledgment_required,
                open_warnings=[WarningOut(id=w.id, code=w.code, item_id=w.item_id, category=w.category,
                                          acknowledged=w.acknowledged) for w in view.classification.open_warnings],
            ),
            structure=StructureOut(pending_item_ids=list(view.structure.pending_item_ids)),
            structural=StructuralOut(
                status=view.structural.status,
                open_findings=[FindingOut(id=f.id, code=f.code, item_ids=list(f.item_ids), source_text=f.source_text,
                                          message=f.message) for f in view.structural.open_findings],
            ),
        ),
    )


# ---- errors: every service error maps to one stable detail.code ----

def _detail(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def _service_error(exc: E.RequirementsEditError) -> HTTPException:
    if isinstance(exc, E.PermissionDenied):
        return _detail(status.HTTP_403_FORBIDDEN, "permission_denied",
                       "Your role cannot use this requirements action.")
    if isinstance(exc, E.JobNotFound):
        return _detail(status.HTTP_404_NOT_FOUND, "job_not_found", "Job not found.")
    if isinstance(exc, E.NotRequirementsV2):
        return _detail(status.HTTP_409_CONFLICT, "not_requirements_v2",
                       "This job does not use the requirements-v2 document.")
    if isinstance(exc, E.NoRequirementsDocument):
        return _detail(status.HTTP_409_CONFLICT, "no_requirements_document", "This job has no requirements document.")
    if isinstance(exc, E.RevisionConflict):
        return _detail(status.HTTP_409_CONFLICT, "revision_conflict",
                       "The requirements changed since you loaded them. Reload and apply your edits again.",
                       current_revision=exc.current_revision)
    if isinstance(exc, E.InvalidRequirements):
        return _detail(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_requirements",
                       "The requirements do not pass the saving rules.",
                       issues=[{"code": i.code, "category": i.category, "item_id": i.item_id} for i in exc.issues])
    if isinstance(exc, E.InvalidOperation):
        return _detail(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, str(exc))
    if isinstance(exc, E.StoredDocumentInvalid):
        return _detail(status.HTTP_500_INTERNAL_SERVER_ERROR, "stored_document_invalid",
                       "The stored requirements document is malformed.")
    if isinstance(exc, E.AuditWriteError):
        return _detail(status.HTTP_500_INTERNAL_SERVER_ERROR, "audit_failed",
                       "The audit event could not be recorded, so the edit was not saved.")
    return _detail(status.HTTP_500_INTERNAL_SERVER_ERROR, "write_failed", "The requirements could not be saved.")


def _editor(user) -> E.EditingUser:
    return E.EditingUser(user_id=user.user_id, tenant_id=user.tenant_id, role=user.role, email=user.email)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- routes ----

@router.get("/{job_id}/requirements", response_model=EditResponse, dependencies=[RequireRequirementsReader])
async def get_requirements(job_id: str, current_user: CurrentUserDep,
                           db: Annotated[AsyncSession, Depends(get_db)]):
    """Read the requirements document and its read-only review for editing. Never locks the row and never commits."""
    try:
        view = await E.load_for_edit(db, _editor(current_user), job_id)
        body = _edit_response(view)
    except E.RequirementsEditError as exc:
        await db.rollback()
        raise _service_error(exc) from exc
    await db.rollback()                              # end the read transaction; nothing was written
    return body


@router.post("/{job_id}/requirements/edits", response_model=SaveResponse, dependencies=[RequireRequirementsEditor])
async def save_requirements_edits(job_id: str, body: SaveEditsRequest, current_user: CurrentUserDep,
                                  db: Annotated[AsyncSession, Depends(get_db)]):
    """Save typed operations. The edit and its audit row share one transaction; it commits only after both succeed."""
    editor = _editor(current_user)
    try:
        E.check_editor(editor.role)
        ops = E.parse_operations(body.operations)
        result = await E.save_operations(db, editor, job_id, client_revision=body.client_revision, ops=ops, now=_now())
        await E.write_audit_event(db, editor, job_id, action=E.AUDIT_ACTION_SAVE, details=result.audit)
        await db.commit()
    except E.RequirementsEditError as exc:
        await db.rollback()
        raise _service_error(exc) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise _service_error(E.RequirementsEditError("The save could not be written.", code="write_failed")) from exc
    except Exception:
        await db.rollback()
        raise
    return SaveResponse(requirements_revision=result.revision)
