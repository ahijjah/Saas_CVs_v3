"""
Campaigns router — recruitment operational objects (grouping + lifecycle).

A campaign represents a recruitment initiative or hiring wave. It groups zero or
more jobs for filtering, reporting, and operational management. Campaigns are NOT
a permission or security boundary: access control remains governed by tenant RLS
and jobs.client_organization_id. Campaigns are available to every tenant type.

Authorization summary
─────────────────────
• Tenant admin / hr_manager → create / update / transition campaigns
• Tenant admin only         → delete campaigns (administrative action)
• All tenant users          → list / view campaigns (agency non-admins see only
                              public campaigns + campaigns for their assigned clients)
• super_admin               → full access across all tenants

Status lifecycle
────────────────
  draft → active → on_hold ↔ active → closed | cancelled
  Transitions are validated in campaign_service.validate_status_transition().
  Campaign status NEVER cascades to linked jobs.
"""
import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from auth.module_guards import RequireAIRecruitment
from database import get_db, set_rls_context
from services.campaign_service import (
    CAMPAIGN_STATUSES,
    campaign_has_linked_jobs,
    fetch_campaign,
    validate_status_transition,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"], dependencies=[RequireAIRecruitment])


# ── Date parsing helpers ──────────────────────────────────────────────────────

def _parse_iso_date(date_str: str | None) -> datetime.date | None:
    """
    Parse ISO date string (YYYY-MM-DD) to Python date object.
    Returns None if input is None or empty string.
    Raises HTTPException 422 on invalid format.
    """
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.date.fromisoformat(date_str.strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid date format '{date_str}'. Expected ISO format (YYYY-MM-DD).",
        )


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateCampaignRequest(BaseModel):
    name: str
    description: str | None = None
    client_organization_id: str | None = None
    status: str = "draft"                          # 'draft' or 'active' only on create
    start_date: str | None = None                  # ISO date string YYYY-MM-DD
    end_date: str | None = None
    target_hire_count: int | None = Field(None, gt=0)
    campaign_owner_id: str | None = None
    notes: str | None = None
    public_title: str | None = None
    is_publicly_listed: bool = False


class UpdateCampaignRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    client_organization_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    target_hire_count: int | None = Field(None, gt=0)
    campaign_owner_id: str | None = None
    notes: str | None = None
    public_title: str | None = None
    is_publicly_listed: bool | None = None


class CampaignStatusRequest(BaseModel):
    status: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_write_access(current_user) -> None:
    if (current_user.role or "").lower() not in ("admin", "hr_manager", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Creating or editing campaigns requires admin or HR manager role.",
        )


def _require_admin(current_user) -> None:
    if (current_user.role or "").lower() not in ("admin", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant admins can perform this action.",
        )


async def _accessible_client_ids(current_user, db: AsyncSession) -> list[str] | None:
    """
    Mirrors the job-list scoping convenience (NOT a security gate).

    Returns:
      • None  → no client filtering (organization tenant, or admin/super_admin)
      • [ids] → explicit list of client_organization_ids the user may access
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
    """Serialize a job_campaigns row to a response dict."""
    return {
        "campaign_id":            str(r["campaign_id"]),
        "tenant_id":              str(r["tenant_id"]),
        "client_organization_id": str(r["client_organization_id"]) if r["client_organization_id"] else None,
        "client_org_name":        r.get("client_org_name"),
        "name":                   r["name"],
        "description":            r.get("description"),
        "status":                 r["status"],
        "start_date":             r["start_date"].isoformat() if r.get("start_date") else None,
        "end_date":               r["end_date"].isoformat() if r.get("end_date") else None,
        "target_hire_count":      r.get("target_hire_count"),
        "campaign_owner_id":      str(r["campaign_owner_id"]) if r.get("campaign_owner_id") else None,
        "campaign_owner_name":    r.get("campaign_owner_name"),
        "notes":                  r.get("notes"),
        "public_title":           r.get("public_title"),
        "is_publicly_listed":     bool(r.get("is_publicly_listed", False)),
        "created_by":             str(r["created_by"]) if r.get("created_by") else None,
        "created_at":             r["created_at"].isoformat() if r.get("created_at") else None,
        "updated_at":             r["updated_at"].isoformat() if r.get("updated_at") else None,
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


async def _validate_owner(owner_id: str, tenant_id: str, db: AsyncSession) -> None:
    """Ensure a user exists in the tenant and is active."""
    row = await db.execute(
        text("""
            SELECT 1 FROM users
            WHERE user_id = CAST(:uid AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
              AND status = 'active'
        """),
        {"uid": owner_id, "tid": tenant_id},
    )
    if not row.first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign owner user not found or inactive.",
        )


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_campaigns(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    client_organization_id: str | None = None,
    show_completed: bool = False,      # True = include closed + cancelled campaigns
):
    """
    List campaigns for the current tenant, with job counts.

    By default returns operational campaigns: draft, active, on_hold.
    Pass show_completed=true to also include closed and cancelled.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    is_super = (current_user.role or "").lower() == "super_admin"
    params: dict = {"tid": current_user.tenant_id}
    filters: list[str] = []

    if not is_super:
        filters.append("camp.tenant_id = CAST(:tid AS uuid)")

    client_ids = await _accessible_client_ids(current_user, db)
    if client_ids is not None:
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

    if show_completed:
        # All statuses visible
        pass
    else:
        filters.append("camp.status IN ('draft', 'active', 'on_hold')")

    where_sql = ("WHERE " + " AND ".join(filters)) if filters else ""

    rows = await db.execute(
        text(f"""
            SELECT camp.*,
                   co.organization_name         AS client_org_name,
                   u.full_name                  AS campaign_owner_name,
                   COUNT(j.job_id)              AS jobs_total,
                   COUNT(j.job_id) FILTER (WHERE LOWER(j.status) = 'active') AS jobs_active
            FROM job_campaigns camp
            LEFT JOIN client_organizations co ON co.client_organization_id = camp.client_organization_id
            LEFT JOIN users u ON u.user_id = camp.campaign_owner_id
            LEFT JOIN jobs j ON j.campaign_id = camp.campaign_id
            {where_sql}
            GROUP BY camp.campaign_id, co.organization_name, u.full_name
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

    # Validate initial status: only draft or active allowed on create
    initial_status = (body.status or "draft").lower()
    if initial_status not in ("draft", "active"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Initial status must be 'draft' or 'active'.")

    if body.client_organization_id:
        await _validate_client_org(body.client_organization_id, current_user.tenant_id, db)

    owner_id = body.campaign_owner_id
    if owner_id:
        await _validate_owner(owner_id, current_user.tenant_id, db)

    # Parse dates from ISO strings to date objects
    start_date = _parse_iso_date(body.start_date)
    end_date = _parse_iso_date(body.end_date)

    result = await db.execute(
        text("""
            INSERT INTO job_campaigns (
                tenant_id, client_organization_id, name, description, status,
                start_date, end_date, target_hire_count, campaign_owner_id,
                notes, public_title, is_publicly_listed, created_by
            ) VALUES (
                CAST(:tid AS uuid), CAST(:coid AS uuid), :name, :descr, :status,
                :start_date, :end_date, :target_hire_count,
                CAST(:owner_id AS uuid), :notes, :public_title, :is_publicly_listed,
                CAST(:uid AS uuid)
            )
            RETURNING *
        """),
        {
            "tid":                current_user.tenant_id,
            "coid":               body.client_organization_id,
            "name":               name,
            "descr":              (body.description or "").strip() or None,
            "status":             initial_status,
            "start_date":         start_date,
            "end_date":           end_date,
            "target_hire_count":  body.target_hire_count,
            "owner_id":           owner_id,
            "notes":              (body.notes or "").strip() or None,
            "public_title":       (body.public_title or "").strip() or None,
            "is_publicly_listed": body.is_publicly_listed,
            "uid":                current_user.user_id,
        },
    )
    row = result.mappings().first()
    await db.commit()
    return {"success": True, "campaign": _serialize(dict(row))}


# ── Get one (with linked jobs + aggregate stats) ──────────────────────────────

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

    # Convenience scoping (not a security gate)
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

    # Owner name
    owner_name = None
    if campaign["campaign_owner_id"]:
        own_row = await db.execute(
            text("SELECT full_name FROM users WHERE user_id = CAST(:uid AS uuid)"),
            {"uid": str(campaign["campaign_owner_id"])},
        )
        owner_name = own_row.scalar_one_or_none()

    enriched = {**campaign, "client_org_name": co_name, "campaign_owner_name": owner_name}
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
    out["jobs_active"] = sum(1 for j in out["jobs"] if (j["job_status"] or "").lower() == "active")

    # Aggregate application stats across all linked jobs
    stats_row = await db.execute(
        text("""
            SELECT
                COUNT(a.application_id)                                              AS applications_total,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'qualified')     AS applications_qualified,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'partial')       AS applications_partial,
                COUNT(a.application_id) FILTER (WHERE a.decision = 'rejected')      AS applications_rejected,
                COUNT(a.application_id) FILTER (WHERE a.processing_status = 'ai_scored') AS applications_scored
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE j.campaign_id = CAST(:cid AS uuid)
              AND j.tenant_id   = CAST(:tid AS uuid)
        """),
        {"cid": campaign_id, "tid": current_user.tenant_id},
    )
    sr = stats_row.mappings().first()
    if sr:
        out["applications_total"]     = int(sr["applications_total"])
        out["applications_qualified"] = int(sr["applications_qualified"])
        out["applications_partial"]   = int(sr["applications_partial"])
        out["applications_rejected"]  = int(sr["applications_rejected"])
        out["applications_scored"]    = int(sr["applications_scored"])
    else:
        out["applications_total"] = out["applications_qualified"] = 0
        out["applications_partial"] = out["applications_rejected"] = 0
        out["applications_scored"] = 0

    return out


# ── Update metadata ───────────────────────────────────────────────────────────

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

    if body.start_date is not None:
        updates["start_date"] = _parse_iso_date(body.start_date)

    if body.end_date is not None:
        updates["end_date"] = _parse_iso_date(body.end_date)

    if body.target_hire_count is not None:
        updates["target_hire_count"] = body.target_hire_count

    if body.notes is not None:
        updates["notes"] = body.notes.strip() or None

    if body.public_title is not None:
        updates["public_title"] = body.public_title.strip() or None

    if body.is_publicly_listed is not None:
        updates["is_publicly_listed"] = body.is_publicly_listed

    if body.campaign_owner_id is not None:
        owner = body.campaign_owner_id or None
        if owner:
            await _validate_owner(owner, current_user.tenant_id, db)
        updates["campaign_owner_id"] = owner

    # Changing the campaign's client is only allowed while NO jobs are linked
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
        elif k == "campaign_owner_id":
            set_parts.append("campaign_owner_id = CAST(:campaign_owner_id AS uuid)")
        else:
            # start_date, end_date, and other fields can be bound directly
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


# ── Status transition ─────────────────────────────────────────────────────────

@router.post("/{campaign_id}/status")
async def transition_campaign_status(
    campaign_id: str,
    body: CampaignStatusRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Transition a campaign to a new lifecycle status.

    Allowed transitions:
      draft     → active | cancelled
      active    → on_hold | closed | cancelled
      on_hold   → active | closed | cancelled
      closed    → (terminal, no transitions)
      cancelled → (terminal, no transitions)

    Campaign status NEVER cascades to linked jobs.
    """
    _require_write_access(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    campaign = await fetch_campaign(db, campaign_id, current_user.tenant_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    new_status = (body.status or "").lower()
    if new_status not in CAMPAIGN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status '{new_status}'. Must be one of: {', '.join(sorted(CAMPAIGN_STATUSES))}.",
        )

    validate_status_transition(campaign["status"], new_status)

    if new_status == campaign["status"]:
        return {"success": True, "message": "Status unchanged.", "status": new_status}

    await db.execute(
        text("""
            UPDATE job_campaigns
            SET status = :new_status, updated_at = now()
            WHERE campaign_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid)
        """),
        {"new_status": new_status, "cid": campaign_id, "tid": current_user.tenant_id},
    )
    await db.commit()
    return {"success": True, "status": new_status}


# ── Summary (KPI aggregation for the Overview tab) ───────────────────────────

@router.get("/{campaign_id}/summary")
async def get_campaign_summary(
    campaign_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Aggregated KPI summary for the Campaign Overview tab.

    Returns workflow distribution across all applications in all linked jobs,
    plus job counts and time-open. Intentionally separate from GET /{id} to
    keep the base response cheap.

    Fill rate = hired / target_hire_count (not qualified).
    Time open = days since start_date (fallback: created_at).
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    campaign = await fetch_campaign(db, campaign_id, current_user.tenant_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    # Respect same client-scoping as GET /{id}
    client_ids = await _accessible_client_ids(current_user, db)
    if client_ids is not None and campaign["client_organization_id"] is not None:
        if str(campaign["client_organization_id"]) not in client_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    # ── Job counts ────────────────────────────────────────────────────────────
    jobs_row = await db.execute(
        text("""
            SELECT
                COUNT(*)                                                      AS jobs_total,
                COUNT(*) FILTER (WHERE LOWER(status) = 'active')             AS jobs_active
            FROM jobs
            WHERE campaign_id = CAST(:cid AS uuid)
              AND tenant_id   = CAST(:tid AS uuid)
        """),
        {"cid": campaign_id, "tid": current_user.tenant_id},
    )
    jr = jobs_row.mappings().first()

    # ── Application aggregates ────────────────────────────────────────────────
    apps_row = await db.execute(
        text("""
            SELECT
                COUNT(a.application_id)                                                       AS total_applications,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'awaiting_review')  AS awaiting_review,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'under_review')     AS under_review,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'shortlisted')      AS shortlisted,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'interviewing')     AS interviewing,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'offer_made')       AS offer_made,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'hired')            AS hired,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'rejected')         AS rejected,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'withdrawn')        AS withdrawn,
                COUNT(a.application_id) FILTER (WHERE a.workflow_status = 'on_hold')          AS on_hold
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE j.campaign_id = CAST(:cid AS uuid)
              AND j.tenant_id   = CAST(:tid AS uuid)
        """),
        {"cid": campaign_id, "tid": current_user.tenant_id},
    )
    ar = apps_row.mappings().first()

    # ── Derived metrics ───────────────────────────────────────────────────────
    hired_count = int(ar["hired"]) if ar else 0
    target = campaign.get("target_hire_count")

    fill_rate_pct: float | None = None
    if target and target > 0:
        fill_rate_pct = round(min(100.0, hired_count / target * 100), 1)

    # time_open: days since start_date (fallback: created_at)
    ref = campaign.get("start_date") or campaign.get("created_at")
    time_open_days: int | None = None
    if ref is not None:
        ref_date = ref.date() if hasattr(ref, "date") else ref
        time_open_days = (datetime.date.today() - ref_date).days

    return {
        "campaign_id":        campaign_id,
        "jobs_total":         int(jr["jobs_total"])  if jr else 0,
        "active_jobs":        int(jr["jobs_active"]) if jr else 0,
        "total_applications": int(ar["total_applications"]) if ar else 0,
        "awaiting_review":    int(ar["awaiting_review"])    if ar else 0,
        "under_review":       int(ar["under_review"])       if ar else 0,
        "shortlisted":        int(ar["shortlisted"])        if ar else 0,
        "interviewing":       int(ar["interviewing"])       if ar else 0,
        "offer_made":         int(ar["offer_made"])         if ar else 0,
        "hired":              hired_count,
        "rejected":           int(ar["rejected"])           if ar else 0,
        "withdrawn":          int(ar["withdrawn"])          if ar else 0,
        "on_hold":            int(ar["on_hold"])            if ar else 0,
        "target_hire_count":  target,
        "fill_rate_pct":      fill_rate_pct,
        "time_open_days":     time_open_days,
    }


# ── Candidates (applications for jobs in this campaign) ─────────────────────────

@router.get("/{campaign_id}/candidates")
async def get_campaign_candidates(
    campaign_id: str,
    workflow_status: str | None = None,
    current_user: CurrentUserDep = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
):
    """
    List all applications for jobs under this campaign.

    Optional filter by workflow_status (e.g. 'awaiting_review', 'hired').
    Respects campaign access control and client scoping from parent campaign.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    campaign = await fetch_campaign(db, campaign_id, current_user.tenant_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    # Respect same client-scoping as GET /{id}
    client_ids = await _accessible_client_ids(current_user, db)
    if client_ids is not None and campaign["client_organization_id"] is not None:
        if str(campaign["client_organization_id"]) not in client_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    # Build query: applications for jobs under this campaign
    filters = [
        "j.campaign_id = CAST(:cid AS uuid)",
        "j.tenant_id   = CAST(:tid AS uuid)",
    ]
    params = {"cid": campaign_id, "tid": current_user.tenant_id}

    if workflow_status:
        filters.append("a.workflow_status = :wf")
        params["wf"] = workflow_status.lower()

    where_sql = "WHERE " + " AND ".join(filters)

    rows = await db.execute(
        text(f"""
            SELECT
                a.application_id,
                a.job_id,
                a.candidate_name,
                a.workflow_status,
                a.processing_status,
                a.applied_at,
                COALESCE(s.det_final_score, s.final_score) AS ai_score,
                j.title       AS job_title
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            LEFT JOIN application_scores s ON s.application_id = a.application_id
            {where_sql}
            ORDER BY a.applied_at DESC
        """),
        params,
    )

    candidates = []
    for r in rows.mappings():
        candidates.append({
            "application_id":  str(r["application_id"]),
            "job_id":          str(r["job_id"]),
            "candidate_name":  r["candidate_name"],
            "job_title":       r["job_title"],
            "workflow_status": (
                r["workflow_status"] or "awaiting_review"
                if r["processing_status"] == "ai_scored"
                else r["workflow_status"]
            ),
            "ai_score":        float(r["ai_score"]) if r["ai_score"] is not None else None,
            "applied_at":      r["applied_at"].isoformat() if r["applied_at"] else None,
        })

    return {"candidates": candidates, "total": len(candidates)}


# ── Delete (admin only — not a normal UI action) ──────────────────────────────

@router.delete("/{campaign_id}", status_code=status.HTTP_200_OK)
async def delete_campaign(
    campaign_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Permanently delete a campaign. Admin role required.

    Linked jobs are automatically detached (campaign_id → NULL via ON DELETE SET NULL)
    and become standalone again. Jobs and applications are never deleted.

    This is an administrative action, not a normal UI workflow. Users should
    close or cancel campaigns instead of deleting them.
    """
    _require_admin(current_user)
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
