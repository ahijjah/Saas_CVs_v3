"""
Client organisations router.

Used by agency and individual_recruiter tenants to manage the companies they
recruit on behalf of.  Direct organisation tenants (tenant_type='organization')
cannot access these endpoints.

Authorization summary
─────────────────────
• Tenant admin     → full CRUD on all client orgs under their tenant
• hr_manager       → read + update (no create/delete)
• recruiter/viewer → read only (their assigned client orgs only)
• super_admin      → full access across all tenants
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context

router = APIRouter(prefix="/client-organizations", tags=["client-organizations"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateClientOrgRequest(BaseModel):
    organization_name: str
    industry: str | None = None
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    logo_url: str | None = None
    notes: str | None = None


class UpdateClientOrgRequest(BaseModel):
    organization_name: str | None = None
    industry: str | None = None
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    logo_url: str | None = None
    notes: str | None = None
    status: str | None = None


class AssignUserRequest(BaseModel):
    user_id: str
    role_for_client: str = "recruiter"


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _require_agency_tenant(tenant_id: str, db: AsyncSession) -> str:
    """
    Return tenant_type.  Raise 403 if this is a direct organisation tenant
    (agency/individual_recruiter workflow only).
    """
    row = await db.execute(
        text("SELECT tenant_type FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": tenant_id},
    )
    r = row.mappings().first()
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if r["tenant_type"] == "organization":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client organisations are only available for agency and individual recruiter accounts.",
        )
    return r["tenant_type"]


def _require_admin_or_super(current_user) -> None:
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


def _require_write_access(current_user) -> None:
    if current_user.role not in ("admin", "hr_manager", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Write access requires admin or HR manager role.",
        )


def _serialize_client_org(r: dict) -> dict:
    return {
        "client_organization_id": str(r["client_organization_id"]),
        "tenant_id":              str(r["tenant_id"]),
        "organization_name":      r["organization_name"],
        "industry":               r["industry"],
        "contact_person":         r["contact_person"],
        "email":                  r["email"],
        "phone":                  r["phone"],
        "logo_url":               r["logo_url"],
        "notes":                  r["notes"],
        "status":                 r["status"],
        "created_at":             r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at":             r["updated_at"].isoformat() if r["updated_at"] else None,
    }


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_client_organizations(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    include_inactive: bool = False,
):
    """
    List client organisations for the current tenant.
    Non-admin agency users see only their assigned clients.
    """
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_agency_tenant(current_user.tenant_id, db)

    is_super = current_user.role == "super_admin"
    is_admin = current_user.role == "admin"

    params: dict = {"tid": current_user.tenant_id}
    extra_where = ""

    if not (is_super or is_admin):
        # Non-admin: restrict to assigned client orgs
        extra_where = """
            AND co.client_organization_id IN (
                SELECT client_organization_id FROM agency_user_clients
                WHERE user_id = CAST(:uid AS uuid)
            )
        """
        params["uid"] = current_user.user_id

    status_filter = "" if include_inactive else "AND co.status = 'active'"

    rows = await db.execute(
        text(f"""
            SELECT co.*,
                   COUNT(DISTINCT j.job_id) AS active_jobs_count,
                   COUNT(DISTINCT auc.user_id) AS assigned_users_count
            FROM client_organizations co
            LEFT JOIN jobs j ON j.client_organization_id = co.client_organization_id
                AND j.status = 'Active'
            LEFT JOIN agency_user_clients auc
                ON auc.client_organization_id = co.client_organization_id
            WHERE co.tenant_id = CAST(:tid AS uuid)
            {status_filter}
            {extra_where}
            GROUP BY co.client_organization_id
            ORDER BY co.organization_name
        """),
        params,
    )

    orgs = []
    for r in rows.mappings():
        d = _serialize_client_org(r)
        d["active_jobs_count"] = int(r["active_jobs_count"])
        d["assigned_users_count"] = int(r["assigned_users_count"])
        orgs.append(d)

    return {"client_organizations": orgs, "total": len(orgs)}


# ── Create ────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_client_organization(
    body: CreateClientOrgRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_admin_or_super(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_agency_tenant(current_user.tenant_id, db)

    # Enforce per-tenant client organisation limit (NULL max_clients = unlimited)
    limit_row = await db.execute(
        text("SELECT max_clients FROM tenants WHERE tenant_id = CAST(:tid AS uuid)"),
        {"tid": current_user.tenant_id},
    )
    limit_data = limit_row.mappings().first()
    if limit_data and limit_data["max_clients"] is not None:
        count_row = await db.execute(
            text("SELECT COUNT(*) FROM client_organizations WHERE tenant_id = CAST(:tid AS uuid) AND status = 'active'"),
            {"tid": current_user.tenant_id},
        )
        if int(count_row.scalar_one()) >= limit_data["max_clients"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Client organisation limit reached ({limit_data['max_clients']}). Upgrade your plan to add more.",
            )

    result = await db.execute(
        text("""
            INSERT INTO client_organizations
                (tenant_id, organization_name, industry, contact_person,
                 email, phone, logo_url, notes, status)
            VALUES
                (CAST(:tid AS uuid), :name, :industry, :contact,
                 :email, :phone, :logo_url, :notes, 'active')
            RETURNING *
        """),
        {
            "tid":      current_user.tenant_id,
            "name":     body.organization_name,
            "industry": body.industry,
            "contact":  body.contact_person,
            "email":    body.email,
            "phone":    body.phone,
            "logo_url": body.logo_url,
            "notes":    body.notes,
        },
    )
    row = result.mappings().first()
    await db.commit()
    return {"success": True, "client_organization": _serialize_client_org(row)}


# ── Get one ───────────────────────────────────────────────────────────────────

@router.get("/{client_org_id}")
async def get_client_organization(
    client_org_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_agency_tenant(current_user.tenant_id, db)

    is_admin = current_user.role in ("admin", "super_admin")
    params: dict = {"cid": client_org_id, "tid": current_user.tenant_id}

    access_check = ""
    if not is_admin:
        access_check = """
            AND co.client_organization_id IN (
                SELECT client_organization_id FROM agency_user_clients
                WHERE user_id = CAST(:uid AS uuid)
            )
        """
        params["uid"] = current_user.user_id

    row = await db.execute(
        text(f"""
            SELECT co.*,
                   COUNT(DISTINCT j.job_id) AS active_jobs_count
            FROM client_organizations co
            LEFT JOIN jobs j ON j.client_organization_id = co.client_organization_id
                AND j.status = 'Active'
            WHERE co.client_organization_id = CAST(:cid AS uuid)
              AND co.tenant_id = CAST(:tid AS uuid)
              {access_check}
            GROUP BY co.client_organization_id
        """),
        params,
    )
    r = row.mappings().first()
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client organisation not found")

    d = _serialize_client_org(r)
    d["active_jobs_count"] = int(r["active_jobs_count"])
    return d


# ── Update ────────────────────────────────────────────────────────────────────

@router.patch("/{client_org_id}")
async def update_client_organization(
    client_org_id: str,
    body: UpdateClientOrgRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    _require_write_access(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_agency_tenant(current_user.tenant_id, db)

    # Verify exists and belongs to tenant
    check = await db.execute(
        text("""
            SELECT client_organization_id FROM client_organizations
            WHERE client_organization_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid)
        """),
        {"cid": client_org_id, "tid": current_user.tenant_id},
    )
    if not check.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client organisation not found")

    if body.status and body.status not in ("active", "inactive"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="status must be 'active' or 'inactive'")

    updates: dict = {}
    field_map = {
        "organization_name": body.organization_name,
        "industry":          body.industry,
        "contact_person":    body.contact_person,
        "email":             body.email,
        "phone":             body.phone,
        "logo_url":          body.logo_url,
        "notes":             body.notes,
        "status":            body.status,
    }
    for k, v in field_map.items():
        if v is not None:
            updates[k] = v

    if not updates:
        return {"success": True, "message": "No changes to apply."}

    updates["updated_at"] = "now()"  # handled specially below
    set_parts = [f"{k} = :{k}" for k in updates if k != "updated_at"]
    set_parts.append("updated_at = now()")
    updates.pop("updated_at")
    updates["cid"] = client_org_id
    updates["tid"] = current_user.tenant_id

    result = await db.execute(
        text(f"""
            UPDATE client_organizations
            SET {', '.join(set_parts)}
            WHERE client_organization_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid)
            RETURNING *
        """),
        updates,
    )
    row = result.mappings().first()
    await db.commit()
    return {"success": True, "client_organization": _serialize_client_org(row)}


# ── Delete / deactivate ───────────────────────────────────────────────────────

@router.delete("/{client_org_id}", status_code=status.HTTP_200_OK)
async def deactivate_client_organization(
    client_org_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Deactivates (soft-deletes) a client organisation. Does not delete linked jobs."""
    _require_admin_or_super(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_agency_tenant(current_user.tenant_id, db)

    result = await db.execute(
        text("""
            UPDATE client_organizations
            SET status = 'inactive', updated_at = now()
            WHERE client_organization_id = CAST(:cid AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
            RETURNING client_organization_id
        """),
        {"cid": client_org_id, "tid": current_user.tenant_id},
    )
    if not result.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client organisation not found")
    await db.commit()
    return {"success": True, "message": "Client organisation deactivated."}


# ── User assignment ───────────────────────────────────────────────────────────

@router.get("/{client_org_id}/users")
async def list_client_org_users(
    client_org_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List users assigned to a specific client organisation."""
    _require_write_access(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_agency_tenant(current_user.tenant_id, db)

    rows = await db.execute(
        text("""
            SELECT auc.id, auc.user_id, auc.role_for_client, auc.created_at,
                   u.email, u.full_name, u.role AS tenant_role, u.status
            FROM agency_user_clients auc
            JOIN users u ON u.user_id = auc.user_id
            WHERE auc.client_organization_id = CAST(:cid AS uuid)
              AND auc.tenant_id = CAST(:tid AS uuid)
            ORDER BY u.full_name
        """),
        {"cid": client_org_id, "tid": current_user.tenant_id},
    )
    users = [
        {
            "assignment_id":    str(r["id"]),
            "user_id":          str(r["user_id"]),
            "email":            r["email"],
            "full_name":        r["full_name"],
            "tenant_role":      r["tenant_role"],
            "user_status":      r["status"],
            "role_for_client":  r["role_for_client"],
            "assigned_at":      r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows.mappings()
    ]
    return {"users": users, "total": len(users)}


@router.post("/{client_org_id}/users", status_code=status.HTTP_201_CREATED)
async def assign_user_to_client(
    client_org_id: str,
    body: AssignUserRequest,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Assign a tenant user to a client organisation with a specific role."""
    _require_admin_or_super(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_agency_tenant(current_user.tenant_id, db)

    if body.role_for_client not in ("account_manager", "recruiter", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role_for_client must be one of: account_manager, recruiter, viewer",
        )

    # Verify client org belongs to tenant
    corg_check = await db.execute(
        text("""
            SELECT client_organization_id FROM client_organizations
            WHERE client_organization_id = CAST(:cid AS uuid) AND tenant_id = CAST(:tid AS uuid)
        """),
        {"cid": client_org_id, "tid": current_user.tenant_id},
    )
    if not corg_check.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client organisation not found")

    # Verify user belongs to tenant
    user_check = await db.execute(
        text("""
            SELECT user_id FROM users
            WHERE user_id = CAST(:uid AS uuid) AND tenant_id = CAST(:tid AS uuid) AND status = 'active'
        """),
        {"uid": body.user_id, "tid": current_user.tenant_id},
    )
    if not user_check.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active user not found in this tenant")

    try:
        result = await db.execute(
            text("""
                INSERT INTO agency_user_clients (tenant_id, user_id, client_organization_id, role_for_client)
                VALUES (CAST(:tid AS uuid), CAST(:uid AS uuid), CAST(:cid AS uuid), :role)
                ON CONFLICT (user_id, client_organization_id)
                DO UPDATE SET role_for_client = EXCLUDED.role_for_client
                RETURNING id
            """),
            {
                "tid":  current_user.tenant_id,
                "uid":  body.user_id,
                "cid":  client_org_id,
                "role": body.role_for_client,
            },
        )
        await db.commit()
        return {"success": True, "assignment_id": str(result.scalar_one())}
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to assign user: {exc}",
        )


@router.delete("/{client_org_id}/users/{user_id}")
async def remove_user_from_client(
    client_org_id: str,
    user_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Remove a user's assignment from a client organisation."""
    _require_admin_or_super(current_user)
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_agency_tenant(current_user.tenant_id, db)

    result = await db.execute(
        text("""
            DELETE FROM agency_user_clients
            WHERE user_id = CAST(:uid AS uuid)
              AND client_organization_id = CAST(:cid AS uuid)
              AND tenant_id = CAST(:tid AS uuid)
            RETURNING id
        """),
        {"uid": user_id, "cid": client_org_id, "tid": current_user.tenant_id},
    )
    if not result.first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    await db.commit()
    return {"success": True, "message": "User removed from client organisation."}


# ── User's own assignments (for non-admin users to discover their clients) ────

@router.get("/my/assignments")
async def my_client_assignments(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return the list of client organisations assigned to the calling user."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)
    await _require_agency_tenant(current_user.tenant_id, db)

    rows = await db.execute(
        text("""
            SELECT co.client_organization_id, co.organization_name, co.industry,
                   co.status, auc.role_for_client, auc.created_at AS assigned_at
            FROM agency_user_clients auc
            JOIN client_organizations co ON co.client_organization_id = auc.client_organization_id
            WHERE auc.user_id = CAST(:uid AS uuid)
              AND auc.tenant_id = CAST(:tid AS uuid)
              AND co.status = 'active'
            ORDER BY co.organization_name
        """),
        {"uid": current_user.user_id, "tid": current_user.tenant_id},
    )
    assignments = [
        {
            "client_organization_id": str(r["client_organization_id"]),
            "organization_name":      r["organization_name"],
            "industry":               r["industry"],
            "status":                 r["status"],
            "role_for_client":        r["role_for_client"],
            "assigned_at":            r["assigned_at"].isoformat() if r["assigned_at"] else None,
        }
        for r in rows.mappings()
    ]
    return {"assignments": assignments, "total": len(assignments)}
