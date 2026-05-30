"""
Campaign business rules — shared validation for the campaign grouping layer.

Campaigns are an OPTIONAL organizational layer over jobs. They are used for
filtering / grouping / reporting only and are NEVER a permission or security
boundary. Access control remains governed entirely by tenant RLS and
jobs.client_organization_id.

Core invariants enforced here (application layer, v1):
  • One campaign belongs to one client only (or is public when client is NULL).
  • A campaign must not mix jobs from different clients.
      - Client campaign  → linked jobs must share the same client_organization_id.
      - Public campaign  → linked jobs must also be public (client_organization_id IS NULL).
  • A campaign's client cannot be changed while it still has linked jobs.
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def fetch_campaign(
    db: AsyncSession, campaign_id: str, tenant_id: str
) -> dict | None:
    """Return the campaign row (as a dict) scoped to the tenant, or None."""
    row = await db.execute(
        text("""
            SELECT campaign_id, tenant_id, client_organization_id, name,
                   description, status, created_by, created_at, updated_at
            FROM job_campaigns
            WHERE campaign_id = CAST(:cid AS uuid)
              AND tenant_id   = CAST(:tid AS uuid)
        """),
        {"cid": campaign_id, "tid": tenant_id},
    )
    r = row.mappings().first()
    return dict(r) if r else None


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
