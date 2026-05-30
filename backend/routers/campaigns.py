"""
Campaigns router — optional job grouping layer.

A campaign groups zero or more jobs for filtering / reporting convenience. It is
NOT a permission or security boundary: access continues to be governed by tenant
RLS and jobs.client_organization_id. Campaigns are available to every tenant type
(organization campaigns are simply public — client_organization_id = NULL).

Authorization summary
─────────────────────
• Tenant admin / hr_manager → create / update / delete campaigns
• All tenant users          → list / view campaigns (agency non-admins see only
                              public campaigns + campaigns for their assigned clients)
• super_admin               → full access across all tenants
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context
from services.campaign_service import (
    campaign_has_linked_jobs,
    fetch_campaign,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateCampaignRequest(BaseModel):
    name: str
    description: str | None = None
    client_organization_id: str | None = None  # NULL = public/shared campaign


class UpdateCampaignRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None  # active / archived
    client_organization_id: str | None = None  # only changeable while no jobs linked


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_write_access(current_user) -> None:
    if (current_user.role or "").lower() not in ("admin", "hr_manager", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creating or editing campaigns requires admin or HR manager role.",
        )


async def _accessible_client_ids(current_user, db: AsyncSession) -> list[str] | None:
    """
    Mirrors the job-list scoping convenience (NOT a security gate).

    Returns:
      • None  → no client filtering (organization tenant, or admin/super_admin)
      • [ids] → explicit list of client_organization_ids the user may access
                (public campaigns, client_organization_id IS NULL, are always visible)
    """
    role = (current_user.role or "").lower()
    if role in ("admin", "super_admin"):
        return None

    t_row = await db.execute(
        text("SELECT tenant_type FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    tenant_type = (t_row.scalar_one_or_none() or "organization")
    if tenant_type == "organization":
        return None

    rows = await db.execute(
        text("""
            SELECT client_organization_id::text
            FROM agency_user_clients
            WHERE user_id = CAST(:uid AS uuid) AND tenant_id = CAST(:tid AS uuid)
        """),
        {"uid": current_user.user_id, "tid": current_user.tenant_id},
    )
    return [r[0] for r in rows]


def _serialize(r: dict) -> dict:
    return {
        "campaign_id":            str(r["campaign_id"]),
        "tenant_id":              str(r["tenant_id"]),
        "client_organization_id": str(r["client_organization_id"]) if r["client_organization_id"] else None,
        "client_org_name":        r.get("client_org_name"),
        "name":                   r["name"],
        "description":            r["description"],
        "status":                 r["status"],
        "created_by":             str(r["created_by"]) if r["created_by"] else None,
        "created_at":             r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at":             r["updated_at"].isoformat() if r["updated_at"] else None,
    }


async def _validate_client_org(client_org_id: str, tenant_id: str, db: AsyncSession) -> None:
    """Ensure a client organisation exists, belongs to the tenant, and is active."""
    row = await db.execute(
        text("""
            SELECT 1 FROM client_organizations
            WHERE client_organization_id = CAST(:cid AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
              AND status = 'active'
        """),
        {"cid": client_org_id, "tid": tenant_id},
    )
    if not row.first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client organisation not found or inactive.",
        )


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_campaigns(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    client_organization_id: str | None = None,
    include_archived: bool = False,
):
    """List campaigns for the current tenant, with job counts."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super = (current_user.role or "").lower() == "super_admin"
    params: dict = {"tid": current_user.tenant_id}
    filters: list[str] = []

    if not is_super:
        filters.append("camp.tenant_id = CAST(:tid AS uuid)")

    client_ids = await _accessible_client_ids(current_user, db)
    if client_ids is not None:
        # Non-admin agency user: public campaigns + assigned clients only
        if client_ids:
            placeholders = ", ".join(f"CAST(:acl_{i} AS uuid)" for i in range(len(client_ids)))
            filters.append(
                f"(camp.client_organization_id IS NULL OR camp.client_organization_id IN ({placeholders}))"
            )
            for i, cid in enumerate(client_ids):
                params[f"acl_{i}"] = cid
        else:
            filters.append("camp.client_organization_id IS NULL")

    if client_organization_id:
        filters.append("camp.client_organization_id = CAST(:filter_cid AS uuid)")
        params["filter_cid"] = client_organization_id

    if not include_archived:
        filters.append("camp.status = 'active'")

    where_sql = ("WHERE " + " AND ".join(filters)) if filters else ""

    rows = await db.execute(
        text(f"""
            SELECT camp.*,
                   co.organization_name AS client_org_name,
                   COUNT(j.job_id)                                                        AS jobs_total,
                   COUNT(j.job_id) FILTER (WHERE LOWER(j.status) = 'active')              AS jobs_active
            FROM job_campaigns camp
            LEFT JOIN client_organizations co ON co.client_organization_id = camp.client_organization_id
            LEFT JOIN jobs j ON j.campaign_id = camp.campaign_id
            {where_sql}
            GROUP BY camp.campaign_id, co.organization_name
            ORDER BY camp.created_at DESC
        """),
        params,
    )
    campaigns = []
    for r in rows.mappings():
        d = _serialize(r)
        d["jobs_total"] = int(r["jobs_total"])
        d["jobs_active"] = int(r["jobs_active"])
        campaigns.append(d)
    return {"campaigns": campaigns, "total": len(campaigns)}


# ── Create ────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CreateCampaignRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_write_access(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Campaign name is required.")

    if body.client_organization_id:
        await _validate_client_org(body.client_organization_id, current_user.tenant_id, db)

    result = await db.execute(
        text("""
            INSERT INTO job_campaigns
                (tenant_id, client_organization_id, name, description, status, created_by)
            VALUES
                (CAST(:tid AS uuid), CAST(:coid AS uuid), :name, :descr, 'active', CAST(:uid AS uuid))
            RETURNING *
        """),
        {
            "tid":   current_user.tenant_id,
            "coid":  body.client_organization_id,
            "name":  name,
            "descr": (body.description or "").strip() or None,
            "uid":   current_user.user_id,
        },
    )
    row = result.mappings().first()
    await db.commit()
    return {"success": True, "campaign": _serialize(dict(row))}


# ── Get one (with linked jobs) ────────────────────────────────────────────────

@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    campaign = await fetch_campaign(db, campaign_id, current_user.tenant_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    # Convenience scoping (not a security gate): hide client campaigns a non-admin
    # agency user is not assigned to.
    client_ids = await _accessible_client_ids(current_user, db)
    if client_ids is not None and campaign["client_organization_id"] is not None:
        if str(campaign["client_organization_id"]) not in client_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    # Client org name
    co_name = None
    if campaign["client_organization_id"]:
        co_row = await db.execute(
            text("SELECT organization_name FROM client_organizations WHERE client_organization_id = :cid"),
            {"cid": str(campaign["client_organization_id"])},
        )
        co_name = co_row.scalar_one_or_none()

    enriched = {**campaign, "client_org_name": co_name}
    out = _serialize(enriched)

    # Linked jobs
    jobs_rows = await db.execute(
        text("""
            SELECT job_id, job_code, title, INITCAP(status) AS status,
                   client_organization_id, created_at
            FROM jobs
            WHERE campaign_id = CAST(:cid AS uuid)
            ORDER BY created_at DESC
        """),
        {"cid": campaign_id},
    )
    out["jobs"] = [
        {
            "job_id":                  str(j["job_id"]),
            "job_code":                j["job_code"],
            "job_title":               j["title"],
            "job_status":              j["status"],
            "client_organization_id":  str(j["client_organization_id"]) if j["client_organization_id"] else None,
            "created_at":              j["created_at"].isoformat() if j["created_at"] else None,
        }
        for j in jobs_rows.mappings()
    ]
    out["jobs_total"] = len(out["jobs"])
    return out


# ── Update ────────────────────────────────────────────────────────────────────

@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    body: UpdateCampaignRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_write_access(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    campaign = await fetch_campaign(db, campaign_id, current_user.tenant_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    updates: dict = {}

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="Campaign name cannot be empty.")
        updates["name"] = name

    if body.description is not None:
        updates["description"] = body.description.strip() or None

    if body.status is not None:
        s = body.status.lower()
        if s not in ("active", "archived"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="status must be 'active' or 'archived'.")
        updates["status"] = s

    # Changing the campaign's client is only allowed while NO jobs are linked,
    # otherwise we could end up mixing clients within one campaign.
    if "client_organization_id" in body.model_fields_set:
        new_client = body.client_organization_id or None
        current_client = str(campaign["client_organization_id"]) if campaign["client_organization_id"] else None
        if new_client != current_client:
            if await campaign_has_linked_jobs(db, campaign_id, current_user.tenant_id):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "Cannot change the campaign's client while jobs are linked. "
                        "Detach all jobs first, then change the client."
                    ),
                )
            if new_client is not None:
                await _validate_client_org(new_client, current_user.tenant_id, db)
            updates["client_organization_id"] = new_client

    if not updates:
        return {"success": True, "message": "No changes to apply."}

    set_parts = []
    params: dict = {"cid": campaign_id, "tid": current_user.tenant_id}
    for k, v in updates.items():
        if k == "client_organization_id":
            set_parts.append("client_organization_id = CAST(:client_organization_id AS uuid)")
        else:
            set_parts.append(f"{k} = :{k}")
        params[k] = v
    set_parts.append("updated_at = now()")

    result = await db.execute(
        text(f"""
            UPDATE job_campaigns
            SET {', '.join(set_parts)}
            WHERE campaign_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid)
            RETURNING *
        """),
        params,
    )
    row = result.mappings().first()
    await db.commit()
    return {"success": True, "campaign": _serialize(dict(row))}


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/{campaign_id}", status_code=status.HTTP_200_OK)
async def delete_campaign(
    campaign_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Delete a campaign. Linked jobs are automatically detached (campaign_id → NULL
    via ON DELETE SET NULL) and become standalone again. Jobs and applications are
    never deleted.
    """
    _require_write_access(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    result = await db.execute(
        text("""
            DELETE FROM job_campaigns
            WHERE campaign_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid)
            RETURNING campaign_id
        """),
        {"cid": campaign_id, "tid": current_user.tenant_id},
    )
    if not result.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
    await db.commit()
    return {"success": True, "message": "Campaign deleted. Linked jobs are now standalone."}
