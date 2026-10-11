from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.module_guards import RequireATSManagement
from database import get_db, set_rls_context
from services.communication_events import process_communication_event

router = APIRouter(prefix="/applications", tags=["interviews"], dependencies=[RequireATSManagement])

VALID_INTERVIEW_TYPES = {"phone_screen", "technical", "hr", "panel", "final", "other"}
VALID_INTERVIEW_STATUSES = {"scheduled", "completed", "cancelled", "no_show"}
VALID_RECOMMENDATIONS = {"strong_yes", "yes", "neutral", "no", "strong_no"}


class CreateInterviewRequest(BaseModel):
    interview_type: str
    scheduled_at:   str | None = None
    duration_min:   int | None = None
    location:       str | None = None
    interviewers:   list[str] = Field(default_factory=list)
    status:         str = "scheduled"
    notes:          str | None = None


class UpdateInterviewRequest(BaseModel):
    interview_type: str | None = None
    scheduled_at:   str | None = None
    duration_min:   int | None = None
    location:       str | None = None
    interviewers:   list[str] | None = None
    status:         str | None = None
    notes:          str | None = None


class FeedbackRequest(BaseModel):
    overall_rating: int | None = Field(None, ge=1, le=5)
    recommendation: str | None = None
    scorecard:      dict = Field(default_factory=dict)
    notes:          str | None = None


def _parse_scheduled_at(value: str | None) -> datetime | None:
    """Parse ISO-8601 datetime string from the frontend into a Python datetime.

    asyncpg encodes TIMESTAMPTZ parameters using the datetime codec, which
    expects a Python datetime object, not a raw string.
    The HTML datetime-local input emits format "YYYY-MM-DDTHH:MM" (no timezone).
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid scheduled_at format '{value}'. Use ISO 8601 (e.g. 2025-01-15T10:00).",
        )


def _serialize_interview(row: dict) -> dict:
    return {
        "interview_id":    str(row["interview_id"]),
        "application_id":  str(row["application_id"]),
        "interview_type":  row["interview_type"],
        "scheduled_at":    row["scheduled_at"].isoformat() if row["scheduled_at"] else None,
        "duration_min":    row["duration_min"],
        "location":        row["location"],
        "interviewers":    list(row["interviewers"] or []),
        "status":          row["status"],
        "notes":           row["notes"],
        "created_by":      str(row["created_by"]) if row["created_by"] else None,
        "created_by_name": row.get("created_by_name"),
        "created_at":      row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at":      row["updated_at"].isoformat() if row["updated_at"] else None,
        "feedback_count":  int(row.get("feedback_count") or 0),
    }


def _serialize_feedback(row: dict) -> dict:
    import json
    scorecard = row["scorecard"]
    if isinstance(scorecard, str):
        scorecard = json.loads(scorecard)
    return {
        "feedback_id":    str(row["feedback_id"]),
        "interview_id":   str(row["interview_id"]),
        "reviewer_id":    str(row["reviewer_id"]),
        "reviewer_name":  row.get("reviewer_name"),
        "overall_rating": row["overall_rating"],
        "recommendation": row["recommendation"],
        "scorecard":      scorecard or {},
        "notes":          row["notes"],
        "created_at":     row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at":     row["updated_at"].isoformat() if row["updated_at"] else None,
        "is_own":         str(row["reviewer_id"]) == row.get("_current_user_id", ""),
    }


async def _check_application_access(
    application_id: str,
    current_user,
    db: AsyncSession,
    is_super_admin: bool = False,
):
    """Verify current user can access this application (tenant + client org check).

    Super admin bypasses tenant isolation to access applications from any tenant.
    """
    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")
    tenant_filter = "" if is_super_admin else "AND a.tenant_id = CAST(:tid AS uuid)"

    params = {
        "aid":      application_id,
        "uid":      current_user.user_id,
        "is_admin": is_admin,
    }
    if not is_super_admin:
        params["tid"] = current_user.tenant_id

    row = await db.execute(
        text(f"""
            SELECT a.application_id, a.job_id
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = CAST(:aid AS uuid)
              {tenant_filter}
              AND (
                :is_admin = TRUE
                OR j.client_organization_id IS NULL
                OR EXISTS (
                    SELECT 1 FROM agency_user_clients auc
                    WHERE auc.user_id = CAST(:uid AS uuid)
                      AND auc.client_organization_id = j.client_organization_id
                      AND auc.tenant_id = CAST(:tid AS uuid)
                )
              )
        """),
        params,
    )
    rec = row.mappings().first()
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return rec


@router.get("/{application_id}/interviews")
async def list_interviews(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    await _check_application_access(application_id, current_user, db, is_super_admin=is_super_admin)

    tenant_filter = "" if is_super_admin else " AND i.tenant_id = CAST(:tid AS uuid)"
    params: dict = {"aid": application_id}
    if not is_super_admin:
        params["tid"] = current_user.tenant_id

    rows = await db.execute(
        text(f"""
            SELECT i.interview_id, i.application_id, i.interview_type, i.scheduled_at,
                   i.duration_min, i.location, i.interviewers, i.status, i.notes,
                   i.created_by, i.created_at, i.updated_at,
                   COALESCE(u.full_name, u.email) AS created_by_name,
                   COUNT(f.feedback_id)            AS feedback_count
            FROM candidate_interviews i
            LEFT JOIN users u ON u.user_id = i.created_by
            LEFT JOIN candidate_interview_feedback f ON f.interview_id = i.interview_id
            WHERE i.application_id = CAST(:aid AS uuid)
              {tenant_filter}
            GROUP BY i.interview_id, i.application_id, i.interview_type, i.scheduled_at,
                     i.duration_min, i.location, i.interviewers, i.status, i.notes,
                     i.created_by, i.created_at, i.updated_at, u.full_name, u.email
            ORDER BY i.scheduled_at ASC NULLS LAST, i.created_at ASC
        """),
        params,
    )
    return {"interviews": [_serialize_interview(dict(r)) for r in rows.mappings()]}


@router.post("/{application_id}/interviews", status_code=status.HTTP_201_CREATED)
async def create_interview(
    application_id: str,
    body: CreateInterviewRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if body.interview_type not in VALID_INTERVIEW_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid interview_type '{body.interview_type}'.")
    if body.status not in VALID_INTERVIEW_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status '{body.status}'.")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    app_rec = await _check_application_access(application_id, current_user, db, is_super_admin=is_super_admin)

    # asyncpg requires Python datetime for TIMESTAMPTZ parameters — not raw strings.
    scheduled_at = _parse_scheduled_at(body.scheduled_at)

    row = await db.execute(
        text("""
            INSERT INTO candidate_interviews
                (application_id, tenant_id, job_id, interview_type,
                 scheduled_at, duration_min, location, interviewers,
                 status, notes, created_by)
            VALUES
                (CAST(:aid AS uuid), CAST(:tid AS uuid), CAST(:jid AS uuid), :itype,
                 :scheduled_at, :duration_min, :location, CAST(:interviewers AS text[]),
                 :status, :notes, CAST(:uid AS uuid))
            RETURNING
                interview_id, application_id, tenant_id, job_id, interview_type,
                scheduled_at, duration_min, location, interviewers,
                status, notes, created_by, created_at, updated_at
        """),
        {
            "aid":          application_id,
            "tid":          current_user.tenant_id,
            "jid":          str(app_rec["job_id"]),
            "itype":        body.interview_type,
            "scheduled_at": scheduled_at,
            "duration_min": body.duration_min,
            "location":     body.location,
            "interviewers": body.interviewers or [],
            "status":       body.status,
            "notes":        body.notes,
            "uid":          current_user.user_id,
        },
    )
    await db.commit()
    rec = dict(row.mappings().first())
    rec["created_by_name"] = current_user.full_name or current_user.email
    rec["feedback_count"] = 0

    if body.status == "scheduled":
        try:
            await process_communication_event(
                db=db,
                application_id=application_id,
                event_type="interview_scheduled",
                tenant_id=current_user.tenant_id,
            )
            await db.commit()
        except Exception:
            pass

    return {"interview": _serialize_interview(rec)}


@router.patch("/{application_id}/interviews/{interview_id}")
async def update_interview(
    application_id: str,
    interview_id: str,
    body: UpdateInterviewRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    await _check_application_access(application_id, current_user, db, is_super_admin=is_super_admin)

    tenant_filter = "" if is_super_admin else " AND tenant_id = CAST(:tid AS uuid)"
    select_params: dict = {"iid": interview_id, "aid": application_id}
    if not is_super_admin:
        select_params["tid"] = current_user.tenant_id

    row = await db.execute(
        text(f"""
            SELECT interview_id, created_by FROM candidate_interviews
            WHERE interview_id = CAST(:iid AS uuid)
              AND application_id = CAST(:aid AS uuid)
              {tenant_filter}
        """),
        select_params,
    )
    rec = row.mappings().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Interview not found")

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")
    if not is_admin and str(rec["created_by"]) != current_user.user_id:
        raise HTTPException(status_code=403, detail="Only the creator or admin can edit this interview.")

    sets: list[str] = ["updated_at = now()"]
    params: dict = {"iid": interview_id}
    if not is_super_admin:
        params["tid"] = current_user.tenant_id

    if body.interview_type is not None:
        if body.interview_type not in VALID_INTERVIEW_TYPES:
            raise HTTPException(status_code=422, detail=f"Invalid interview_type '{body.interview_type}'.")
        sets.append("interview_type = :itype")
        params["itype"] = body.interview_type

    if body.scheduled_at is not None:
        sets.append("scheduled_at = :scheduled_at")
        params["scheduled_at"] = _parse_scheduled_at(body.scheduled_at)

    if body.duration_min is not None:
        sets.append("duration_min = :duration_min")
        params["duration_min"] = body.duration_min

    if body.location is not None:
        sets.append("location = :location")
        params["location"] = body.location

    if body.interviewers is not None:
        sets.append("interviewers = CAST(:interviewers AS text[])")
        params["interviewers"] = body.interviewers

    if body.status is not None:
        if body.status not in VALID_INTERVIEW_STATUSES:
            raise HTTPException(status_code=422, detail=f"Invalid status '{body.status}'.")
        sets.append("status = :status")
        params["status"] = body.status

    if body.notes is not None:
        sets.append("notes = :notes")
        params["notes"] = body.notes

    updated = await db.execute(
        text(f"""
            UPDATE candidate_interviews
            SET {', '.join(sets)}
            WHERE interview_id = CAST(:iid AS uuid) AND tenant_id = CAST(:tid AS uuid)
            RETURNING
                interview_id, application_id, tenant_id, job_id, interview_type,
                scheduled_at, duration_min, location, interviewers,
                status, notes, created_by, created_at, updated_at
        """),
        params,
    )
    await db.commit()
    result = dict(updated.mappings().first())
    result["created_by_name"] = None
    result["feedback_count"] = 0
    return {"interview": _serialize_interview(result)}


@router.delete("/{application_id}/interviews/{interview_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_interview(
    application_id: str,
    interview_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    await _check_application_access(application_id, current_user, db, is_super_admin=is_super_admin)

    tenant_filter = "" if is_super_admin else " AND tenant_id = CAST(:tid AS uuid)"
    select_params: dict = {"iid": interview_id, "aid": application_id}
    if not is_super_admin:
        select_params["tid"] = current_user.tenant_id

    row = await db.execute(
        text(f"""
            SELECT interview_id, created_by FROM candidate_interviews
            WHERE interview_id = CAST(:iid AS uuid)
              AND application_id = CAST(:aid AS uuid)
              {tenant_filter}
        """),
        select_params,
    )
    rec = row.mappings().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Interview not found")

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")
    if not is_admin and str(rec["created_by"]) != current_user.user_id:
        raise HTTPException(status_code=403, detail="Only the creator or admin can delete this interview.")

    delete_params: dict = {"iid": interview_id}
    if not is_super_admin:
        delete_params["tid"] = current_user.tenant_id

    await db.execute(
        text(f"""
            DELETE FROM candidate_interviews
            WHERE interview_id = CAST(:iid AS uuid)
              {tenant_filter}
        """),
        delete_params,
    )
    await db.commit()


# ── Feedback endpoints ──────────────────────────────────────────────────────

@router.get("/{application_id}/interviews/{interview_id}/feedback")
async def list_feedback(
    application_id: str,
    interview_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    await _check_application_access(application_id, current_user, db, is_super_admin=is_super_admin)

    tenant_filter = "" if is_super_admin else " AND f.tenant_id = CAST(:tid AS uuid)"
    params: dict = {"iid": interview_id}
    if not is_super_admin:
        params["tid"] = current_user.tenant_id

    rows = await db.execute(
        text(f"""
            SELECT f.feedback_id, f.interview_id, f.application_id, f.tenant_id,
                   f.reviewer_id, f.overall_rating, f.recommendation, f.scorecard,
                   f.notes, f.created_at, f.updated_at,
                   COALESCE(u.full_name, u.email) AS reviewer_name
            FROM candidate_interview_feedback f
            LEFT JOIN users u ON u.user_id = f.reviewer_id
            WHERE f.interview_id = CAST(:iid AS uuid)
              {tenant_filter}
            ORDER BY f.created_at ASC
        """),
        params,
    )
    uid = current_user.user_id
    items = []
    for r in rows.mappings():
        d = dict(r)
        d["_current_user_id"] = uid
        items.append(_serialize_feedback(d))
    return {"feedback": items}


@router.post("/{application_id}/interviews/{interview_id}/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    application_id: str,
    interview_id: str,
    body: FeedbackRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    await _check_application_access(application_id, current_user, db, is_super_admin=is_super_admin)

    if body.recommendation and body.recommendation not in VALID_RECOMMENDATIONS:
        raise HTTPException(status_code=422, detail=f"Invalid recommendation '{body.recommendation}'.")

    import json
    row = await db.execute(
        text("""
            INSERT INTO candidate_interview_feedback
                (interview_id, application_id, tenant_id, reviewer_id,
                 overall_rating, recommendation, scorecard, notes)
            VALUES
                (CAST(:iid AS uuid), CAST(:aid AS uuid), CAST(:tid AS uuid), CAST(:uid AS uuid),
                 :rating, :recommendation, CAST(:scorecard AS jsonb), :notes)
            ON CONFLICT (interview_id, reviewer_id)
            DO UPDATE SET
                overall_rating = EXCLUDED.overall_rating,
                recommendation = EXCLUDED.recommendation,
                scorecard      = EXCLUDED.scorecard,
                notes          = EXCLUDED.notes,
                updated_at     = now()
            RETURNING
                feedback_id, interview_id, application_id, tenant_id, reviewer_id,
                overall_rating, recommendation, scorecard, notes, created_at, updated_at
        """),
        {
            "iid":            interview_id,
            "aid":            application_id,
            "tid":            current_user.tenant_id,
            "uid":            current_user.user_id,
            "rating":         body.overall_rating,
            "recommendation": body.recommendation,
            "scorecard":      json.dumps(body.scorecard),
            "notes":          body.notes,
        },
    )
    await db.commit()
    result = dict(row.mappings().first())
    result["reviewer_name"] = current_user.full_name or current_user.email
    result["_current_user_id"] = current_user.user_id
    return {"feedback": _serialize_feedback(result)}


@router.patch("/{application_id}/interviews/{interview_id}/feedback/{feedback_id}")
async def update_feedback(
    application_id: str,
    interview_id: str,
    feedback_id: str,
    body: FeedbackRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super_admin = (current_user.role or "").lower() == "super_admin"
    await _check_application_access(application_id, current_user, db, is_super_admin=is_super_admin)

    tenant_filter = "" if is_super_admin else " AND tenant_id = CAST(:tid AS uuid)"
    select_params: dict = {"fid": feedback_id, "iid": interview_id}
    if not is_super_admin:
        select_params["tid"] = current_user.tenant_id

    row = await db.execute(
        text(f"""
            SELECT feedback_id, reviewer_id FROM candidate_interview_feedback
            WHERE feedback_id = CAST(:fid AS uuid)
              AND interview_id = CAST(:iid AS uuid)
              {tenant_filter}
        """),
        select_params,
    )
    rec = row.mappings().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Feedback not found")

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")
    if not is_admin and str(rec["reviewer_id"]) != current_user.user_id:
        raise HTTPException(status_code=403, detail="Only the author or admin can edit feedback.")

    if body.recommendation and body.recommendation not in VALID_RECOMMENDATIONS:
        raise HTTPException(status_code=422, detail=f"Invalid recommendation '{body.recommendation}'.")

    import json
    update_params: dict = {
        "fid":            feedback_id,
        "rating":         body.overall_rating,
        "recommendation": body.recommendation,
        "scorecard":      json.dumps(body.scorecard),
        "notes":          body.notes,
    }
    if not is_super_admin:
        update_params["tid"] = current_user.tenant_id

    updated = await db.execute(
        text(f"""
            UPDATE candidate_interview_feedback
            SET overall_rating = :rating,
                recommendation = :recommendation,
                scorecard      = CAST(:scorecard AS jsonb),
                notes          = :notes,
                updated_at     = now()
            WHERE feedback_id = CAST(:fid AS uuid)
              {tenant_filter}
            RETURNING
                feedback_id, interview_id, application_id, tenant_id, reviewer_id,
                overall_rating, recommendation, scorecard, notes, created_at, updated_at
        """),
        update_params,
    )
    await db.commit()
    result = dict(updated.mappings().first())
    result["reviewer_name"] = None
    result["_current_user_id"] = current_user.user_id
    return {"feedback": _serialize_feedback(result)}
