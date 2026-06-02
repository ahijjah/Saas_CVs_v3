import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context

router = APIRouter(prefix="/tenant/workflow-policies", tags=["workflow-policies"])

DEFAULT_POLICIES = {
    "require_interview_before_offer": False,
    "require_approval_before_hire":   False,
    "require_rejection_reason":       False,
    "allow_bulk_reject":              True,
    "require_interview_feedback":     False,
    "required_approval_stages":       [],
    "sla_thresholds": {
        "review_days": 14,
        "interview_feedback_days": 7,
        "approval_days": 10,
        "offer_response_days": 5,
    },
}


class SLAThresholds(BaseModel):
    review_days:               int = 14
    interview_feedback_days:   int = 7
    approval_days:             int = 10
    offer_response_days:       int = 5


class WorkflowPoliciesRequest(BaseModel):
    require_interview_before_offer: bool | None = None
    require_approval_before_hire:   bool | None = None
    require_rejection_reason:       bool | None = None
    allow_bulk_reject:              bool | None = None
    require_interview_feedback:     bool | None = None
    required_approval_stages:       list[str] | None = Field(None, max_length=20)
    sla_thresholds:                 SLAThresholds | None = None


def _parse_policies(raw) -> dict:
    if raw is None:
        return dict(DEFAULT_POLICIES)
    if isinstance(raw, str):
        raw = json.loads(raw)
    merged = dict(DEFAULT_POLICIES)
    merged.update(raw)
    return merged


@router.get("")
async def get_workflow_policies(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get the current tenant's workflow governance policies (all roles)."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    row = await db.execute(
        text("""
            SELECT p.policies, p.updated_at,
                   COALESCE(u.full_name, u.email) AS updated_by_name
            FROM tenant_workflow_policies p
            LEFT JOIN users u ON u.user_id = p.updated_by
            WHERE p.tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": current_user.tenant_id},
    )
    rec = row.mappings().first()
    if not rec:
        return {
            "policies": DEFAULT_POLICIES,
            "updated_at": None,
            "updated_by_name": None,
        }

    return {
        "policies": _parse_policies(rec["policies"]),
        "updated_at": rec["updated_at"].isoformat() if rec["updated_at"] else None,
        "updated_by_name": rec["updated_by_name"],
    }


@router.patch("")
async def update_workflow_policies(
    body: WorkflowPoliciesRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update workflow governance policies (admin only)."""
    if (current_user.role or "").lower() not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can update workflow policies.")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Fetch existing policies first
    row = await db.execute(
        text("SELECT policies FROM tenant_workflow_policies WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    rec = row.mappings().first()
    existing = _parse_policies(rec["policies"] if rec else None)

    # Merge in updates
    updates = body.model_dump(exclude_none=True)
    for k, v in updates.items():
        existing[k] = v

    # Validate stage names
    stages = existing.get("required_approval_stages", [])
    if not isinstance(stages, list):
        stages = []
    for s in stages:
        if not isinstance(s, str) or len(s.strip()) == 0:
            raise HTTPException(status_code=422, detail="Each approval stage must be a non-empty string.")
    existing["required_approval_stages"] = [s.strip() for s in stages]

    await db.execute(
        text("""
            INSERT INTO tenant_workflow_policies (tenant_id, policies, updated_by, updated_at)
            VALUES (CAST(:tid AS uuid), CAST(:pol AS jsonb), CAST(:uid AS uuid), now())
            ON CONFLICT (tenant_id) DO UPDATE
            SET policies   = CAST(:pol AS jsonb),
                updated_by = CAST(:uid AS uuid),
                updated_at = now()
        """),
        {
            "tid": current_user.tenant_id,
            "pol": json.dumps(existing),
            "uid": current_user.user_id,
        },
    )
    await db.commit()
    return {"policies": existing}
