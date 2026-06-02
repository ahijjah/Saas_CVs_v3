"""
Campaign business rules — shared validation for the campaign grouping layer.

Campaigns are an OPTIONAL organizational layer over jobs. They are used for
filtering / grouping / reporting only and are NEVER a permission or security
boundary. Access control remains governed entirely by tenant RLS and
jobs.client_organization_id.

Core invariants enforced here (application layer):
  • One campaign belongs to one client only (or is public when client is NULL).
  • A campaign must not mix jobs from different clients.
      - Client campaign  → linked jobs must share the same client_organization_id.
      - Public campaign  → linked jobs must also be public (client_organization_id IS NULL).
  • A campaign's client cannot be changed while it still has linked jobs.
  • Status transitions follow a defined lifecycle:
      draft → active → on_hold ↔ active → closed | cancelled
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Lifecycle ─────────────────────────────────────────────────────────────────

CAMPAIGN_STATUSES = frozenset(("draft", "active", "on_hold", "closed", "cancelled"))

# Valid transitions: {current_status: {allowed_next_statuses}}
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft":     frozenset({"active", "cancelled"}),
    "active":    frozenset({"on_hold", "closed", "cancelled"}),
    "on_hold":   frozenset({"active", "closed", "cancelled"}),
    "closed":    frozenset(),   # terminal
    "cancelled": frozenset(),   # terminal
}

# Human-readable labels for error messages
STATUS_LABELS = {
    "draft":     "Draft",
    "active":    "Active",
    "on_hold":   "On Hold",
    "closed":    "Closed",
    "cancelled": "Cancelled",
}


def validate_status_transition(current: str, new: str) -> None:
    """
    Raise HTTPException 422 if the status transition is not permitted.
    Silently returns when current == new (idempotent).
    """
    if current == new:
        return
    allowed = VALID_TRANSITIONS.get(current, frozenset())
    if new not in allowed:
        current_label = STATUS_LABELS.get(current, current)
        new_label = STATUS_LABELS.get(new, new)
        allowed_labels = [STATUS_LABELS.get(s, s) for s in sorted(allowed)]
        detail = (
            f"Cannot transition from '{current_label}' to '{new_label}'. "
        )
        if allowed_labels:
            detail += f"Allowed next statuses: {', '.join(allowed_labels)}."
        else:
            detail += f"'{current_label}' is a terminal status and cannot be changed."
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )


# ── Fetch ─────────────────────────────────────────────────────────────────────

async def fetch_campaign(
    db: AsyncSession, campaign_id: str, tenant_id: str
) -> dict | None:
    """Return the full campaign row (as a dict) scoped to the tenant, or None."""
    row = await db.execute(
        text("""
            SELECT
                camp.campaign_id,
                camp.tenant_id,
                camp.client_organization_id,
                camp.name,
                camp.description,
                camp.status,
                camp.start_date,
                camp.end_date,
                camp.target_hire_count,
                camp.campaign_owner_id,
                camp.notes,
                camp.public_title,
                camp.is_publicly_listed,
                camp.created_by,
                camp.created_at,
                camp.updated_at
            FROM job_campaigns camp
            WHERE camp.campaign_id = CAST(:cid AS uuid)
              AND camp.tenant_id   = CAST(:tid AS uuid)
        """),
        {"cid": campaign_id, "tid": tenant_id},
    )
    r = row.mappings().first()
    return dict(r) if r else None


# ── Client-alignment validation ───────────────────────────────────────────────

async def validate_job_campaign_link(
    db: AsyncSession,
    *,
    tenant_id: str,
    campaign_id: str,
    job_client_organization_id: str | None,
) -> dict:
    """
    Validate that a job with the given client may be linked to the campaign.

    Raises HTTPException (404/422) on any violation. Returns the campaign row
    on success.

    Rule:
      • Campaign has a client       → job must have the SAME client.
      • Campaign is public (NULL)    → job must ALSO be public (NULL client).
    """
    campaign = await fetch_campaign(db, campaign_id, tenant_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found.",
        )

    campaign_client = (
        str(campaign["client_organization_id"])
        if campaign["client_organization_id"] is not None
        else None
    )
    job_client = job_client_organization_id or None

    if campaign_client is not None:
        if job_client != campaign_client:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "This campaign belongs to a specific client. The job must be "
                    "assigned to the same client organisation as the campaign."
                ),
            )
    else:
        # Public / shared campaign — only public jobs allowed
        if job_client is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "This is a public/shared campaign and can only contain public "
                    "jobs (jobs not assigned to any client organisation)."
                ),
            )

    return campaign


# ── Utilities ─────────────────────────────────────────────────────────────────

async def campaign_has_linked_jobs(
    db: AsyncSession, campaign_id: str, tenant_id: str
) -> bool:
    """True if any job is currently linked to this campaign."""
    row = await db.execute(
        text("""
            SELECT 1 FROM jobs
            WHERE campaign_id = CAST(:cid AS uuid)
              AND tenant_id   = CAST(:tid AS uuid)
            LIMIT 1
        """),
        {"cid": campaign_id, "tid": tenant_id},
    )
    return row.first() is not None
