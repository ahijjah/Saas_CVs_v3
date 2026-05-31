from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context

router = APIRouter(prefix="/applications", tags=["approvals"])

VALID_DECISIONS = {"approved", "rejected", "needs_revision"}


class InitiateApprovalsRequest(BaseModel):
    """Initiate the approval chain using the tenant's required_approval_stages."""
    approver_ids: dict[str, str] = Field(
        default_factory=dict,
        description="Optional map of stage_name -> user_id to pre-assign approvers",
    )


class DecideApprovalRequest(BaseModel):
    decision: str  # approved | rejected | needs_revision
    comment:  str | None = None


class CreateApprovalStageRequest(BaseModel):
    """Manually add a single approval stage (admin only)."""
    approval_stage: str = Field(..., min_length=1, max_length=100)
    stage_order:    int = 0
    approver_id:    str | None = None


def _serialize_approval(row: dict) -> dict:
    return {
        "approval_id":       str(row["approval_id"]),
        "application_id":    str(row["application_id"]),
        "approval_stage":    row["approval_stage"],
        "stage_order":       row["stage_order"],
        "approver_id":       str(row["approver_id"]) if row.get("approver_id") else None,
        "approver_name":     row.get("approver_name"),
        "decision":          row["decision"],
        "comment":           row.get("comment"),
        "decided_at":        row["decided_at"].isoformat() if row.get("decided_at") else None,
        "requested_by":      str(row["requested_by"]) if row.get("requested_by") else None,
        "requested_by_name": row.get("requested_by_name"),
        "created_at":        row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at":        row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


async def _check_application_access(application_id: str, current_user, db: AsyncSession):
    row = await db.execute(
        text("""
            SELECT a.application_id FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = CAST(:aid AS uuid)
              AND a.tenant_id = CAST(:tid AS uuid)
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
        {
            "aid":      application_id,
            "tid":      current_user.tenant_id,
            "uid":      current_user.user_id,
            "is_admin": (current_user.role or "").lower() in ("admin", "super_admin"),
        },
    )
    if not row.mappings().first():
        raise HTTPException(status_code=404, detail="Application not found")


def _build_approval_insert_sql(approver_id: str | None) -> tuple[str, dict]:
    """Return (sql, extra_params) for the approver_id column.

    asyncpg struggles to infer the correct codec when the same parameter
    appears in both a NULL-check context and a CAST expression.  Building
    the SQL fragment in Python — using a literal NULL or a typed CAST —
    eliminates any type-inference ambiguity.
    """
    if approver_id:
        approver_sql = "CAST(:appr AS uuid)"
        extra: dict = {"appr": approver_id}
    else:
        approver_sql = "NULL"
        extra = {}
    return approver_sql, extra


@router.get("/{application_id}/approvals")
async def list_approvals(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _check_application_access(application_id, current_user, db)

    rows = await db.execute(
        text("""
            SELECT ap.approval_id, ap.application_id, ap.approval_stage, ap.stage_order,
                   ap.approver_id, ap.decision, ap.comment, ap.decided_at,
                   ap.requested_by, ap.created_at, ap.updated_at,
                   COALESCE(ua.full_name, ua.email) AS approver_name,
                   COALESCE(ur.full_name, ur.email) AS requested_by_name
            FROM candidate_approvals ap
            LEFT JOIN users ua ON ua.user_id = ap.approver_id
            LEFT JOIN users ur ON ur.user_id = ap.requested_by
            WHERE ap.application_id = CAST(:aid AS uuid)
              AND ap.tenant_id = CAST(:tid AS uuid)
            ORDER BY ap.stage_order ASC, ap.created_at ASC
        """),
        {"aid": application_id, "tid": current_user.tenant_id},
    )
    return {"approvals": [_serialize_approval(dict(r)) for r in rows.mappings()]}


@router.post("/{application_id}/approvals/initiate", status_code=status.HTTP_201_CREATED)
async def initiate_approvals(
    application_id: str,
    body: InitiateApprovalsRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create approval records for all required_approval_stages from the tenant policy."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _check_application_access(application_id, current_user, db)

    # Fetch configured approval stages from tenant policy
    pol_row = await db.execute(
        text("""
            SELECT policies->>'required_approval_stages' AS stages_json
            FROM tenant_workflow_policies
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": current_user.tenant_id},
    )
    pol_rec = pol_row.mappings().first()
    import json
    stages: list[str] = []
    if pol_rec and pol_rec["stages_json"]:
        stages = json.loads(pol_rec["stages_json"])

    if not stages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No required_approval_stages configured. Set them in Recruitment Pipeline Policies.",
        )

    # Delete pending approvals before re-initiating (idempotent)
    await db.execute(
        text("""
            DELETE FROM candidate_approvals
            WHERE application_id = CAST(:aid AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
              AND decision = 'pending'
        """),
        {"aid": application_id, "tid": current_user.tenant_id},
    )

    created = []
    for order, stage_name in enumerate(stages):
        approver_id = body.approver_ids.get(stage_name) or None
        approver_sql, extra_params = _build_approval_insert_sql(approver_id)

        row = await db.execute(
            text(f"""
                INSERT INTO candidate_approvals
                    (application_id, tenant_id, approval_stage, stage_order,
                     approver_id, requested_by)
                VALUES
                    (CAST(:aid AS uuid), CAST(:tid AS uuid), :stage,
                     CAST(:order AS smallint), {approver_sql},
                     CAST(:uid AS uuid))
                RETURNING
                    approval_id, application_id, tenant_id, approval_stage, stage_order,
                    approver_id, decision, comment, decided_at, requested_by,
                    created_at, updated_at
            """),
            {
                "aid":   application_id,
                "tid":   current_user.tenant_id,
                "stage": stage_name,
                "order": order,
                "uid":   current_user.user_id,
                **extra_params,
            },
        )
        rec = dict(row.mappings().first())
        rec["approver_name"] = None
        rec["requested_by_name"] = current_user.full_name or current_user.email
        created.append(_serialize_approval(rec))

    await db.commit()
    return {"approvals": created}


@router.post("/{application_id}/approvals", status_code=status.HTTP_201_CREATED)
async def create_approval_stage(
    application_id: str,
    body: CreateApprovalStageRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Manually add an approval stage (admin only)."""
    if (current_user.role or "").lower() not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can manually add approval stages.")

    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _check_application_access(application_id, current_user, db)

    approver_sql, extra_params = _build_approval_insert_sql(body.approver_id)

    row = await db.execute(
        text(f"""
            INSERT INTO candidate_approvals
                (application_id, tenant_id, approval_stage, stage_order,
                 approver_id, requested_by)
            VALUES
                (CAST(:aid AS uuid), CAST(:tid AS uuid), :stage,
                 CAST(:order AS smallint), {approver_sql},
                 CAST(:uid AS uuid))
            RETURNING
                approval_id, application_id, tenant_id, approval_stage, stage_order,
                approver_id, decision, comment, decided_at, requested_by,
                created_at, updated_at
        """),
        {
            "aid":   application_id,
            "tid":   current_user.tenant_id,
            "stage": body.approval_stage,
            "order": body.stage_order,
            "uid":   current_user.user_id,
            **extra_params,
        },
    )
    await db.commit()
    result = dict(row.mappings().first())
    result["approver_name"] = None
    result["requested_by_name"] = current_user.full_name or current_user.email
    return {"approval": _serialize_approval(result)}


@router.patch("/{application_id}/approvals/{approval_id}")
async def decide_approval(
    application_id: str,
    approval_id: str,
    body: DecideApprovalRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Approve, reject, or request revision on an approval stage."""
    if body.decision not in VALID_DECISIONS:
        raise HTTPException(status_code=422, detail=f"Invalid decision '{body.decision}'.")

    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _check_application_access(application_id, current_user, db)

    row = await db.execute(
        text("""
            SELECT ap.approval_id, ap.approver_id, ap.decision
            FROM candidate_approvals ap
            WHERE ap.approval_id = CAST(:apid AS uuid)
              AND ap.application_id = CAST(:aid AS uuid)
              AND ap.tenant_id = CAST(:tid AS uuid)
        """),
        {"apid": approval_id, "aid": application_id, "tid": current_user.tenant_id},
    )
    rec = row.mappings().first()
    if not rec:
        raise HTTPException(status_code=404, detail="Approval stage not found")

    is_admin = (current_user.role or "").lower() in ("admin", "super_admin")
    assigned_approver = str(rec["approver_id"]) if rec["approver_id"] else None
    if not is_admin and assigned_approver and assigned_approver != current_user.user_id:
        raise HTTPException(status_code=403, detail="This approval stage is assigned to another approver.")

    updated = await db.execute(
        text("""
            UPDATE candidate_approvals
            SET decision   = :decision,
                comment    = :comment,
                decided_at = now(),
                updated_at = now()
            WHERE approval_id = CAST(:apid AS uuid) AND tenant_id = CAST(:tid AS uuid)
            RETURNING
                approval_id, application_id, tenant_id, approval_stage, stage_order,
                approver_id, decision, comment, decided_at, requested_by,
                created_at, updated_at
        """),
        {
            "apid":     approval_id,
            "tid":      current_user.tenant_id,
            "decision": body.decision,
            "comment":  body.comment,
        },
    )
    await db.commit()
    result = dict(updated.mappings().first())
    result["approver_name"] = current_user.full_name or current_user.email
    result["requested_by_name"] = None
    return {"approval": _serialize_approval(result)}


@router.delete("/{application_id}/approvals/{approval_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_approval(
    application_id: str,
    approval_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Cancel/delete an approval stage (admin only)."""
    if (current_user.role or "").lower() not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can delete approval stages.")

    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _check_application_access(application_id, current_user, db)

    row = await db.execute(
        text("""
            DELETE FROM candidate_approvals
            WHERE approval_id = CAST(:apid AS uuid)
              AND application_id = CAST(:aid AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
            RETURNING approval_id
        """),
        {"apid": approval_id, "aid": application_id, "tid": current_user.tenant_id},
    )
    if not row.mappings().first():
        raise HTTPException(status_code=404, detail="Approval stage not found")
    await db.commit()
