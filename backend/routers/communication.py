from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context

template_router = APIRouter(prefix="/communication/templates", tags=["communication"])
comm_router = APIRouter(prefix="/applications", tags=["communication"])


# ── Pydantic Models ────────────────────────────────────────────────────────────


class TemplateCreate(BaseModel):
    name: str
    category: str = "general"
    subject: str
    body: str
    language: str = "en"
    is_active: bool = True


class TemplateUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    subject: str | None = None
    body: str | None = None
    language: str | None = None
    is_active: bool | None = None


class CommunicationLog(BaseModel):
    subject: str | None = None
    body: str | None = None
    status: str = "draft"
    template_id: str | None = None


VALID_CATEGORIES = {"interview_invitation", "rejection", "shortlisted", "offer", "request_info", "talent_pool", "general"}


# ── Template Endpoints ─────────────────────────────────────────────────────────


@template_router.get("/")
async def list_templates(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    category: str | None = None,
    active_only: bool = True,
):
    """List communication templates for the tenant."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    query = """
        SELECT t.template_id, t.name, t.category, t.subject, t.body,
               t.language, t.is_active, t.created_at, t.updated_at,
               COALESCE(u.full_name, u.email) AS created_by_name
        FROM candidate_message_templates t
        LEFT JOIN users u ON u.user_id = t.created_by
        WHERE t.tenant_id = CAST(:tid AS uuid)
    """
    params: dict = {"tid": current_user.tenant_id}

    if active_only:
        query += " AND t.is_active = TRUE"
    if category:
        query += " AND t.category = :category"
        params["category"] = category

    query += " ORDER BY t.name"

    rows = await db.execute(text(query), params)
    templates = []
    for r in rows.mappings():
        row = dict(r)
        row["template_id"] = str(row["template_id"])
        row["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
        row["updated_at"] = row["updated_at"].isoformat() if row["updated_at"] else None
        templates.append(row)

    return {"templates": templates}


@template_router.post("/")
async def create_template(
    body: TemplateCreate,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new communication template (admin only)."""
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can create templates")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="name cannot be empty")
    if not body.subject or not body.subject.strip():
        raise HTTPException(status_code=400, detail="subject cannot be empty")
    if not body.body or not body.body.strip():
        raise HTTPException(status_code=400, detail="body cannot be empty")
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}")

    try:
        result = await db.execute(
            text("""
                INSERT INTO candidate_message_templates
                    (tenant_id, name, category, subject, body, language, is_active, created_by)
                VALUES
                    (CAST(:tid AS uuid), :name, :category, :subject, :body, :language, :is_active, CAST(:uid AS uuid))
                RETURNING template_id, name, category, subject, body, language, is_active, created_at, updated_at
            """),
            {
                "tid": current_user.tenant_id,
                "name": body.name.strip(),
                "category": body.category,
                "subject": body.subject.strip(),
                "body": body.body.strip(),
                "language": body.language,
                "is_active": body.is_active,
                "uid": current_user.user_id,
            },
        )
        row = result.mappings().first()
        await db.commit()

        r = dict(row)
        r["template_id"] = str(r["template_id"])
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        r["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else None
        return r
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail=f"Template '{body.name.strip()}' already exists")
        raise HTTPException(status_code=400, detail="Failed to create template")


@template_router.patch("/{template_id}")
async def update_template(
    template_id: str,
    body: TemplateUpdate,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a communication template (admin only)."""
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can edit templates")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    check = await db.execute(
        text("""
            SELECT template_id FROM candidate_message_templates
            WHERE template_id = CAST(:tid AS uuid)
              AND tenant_id = CAST(:tenant_id AS uuid)
        """),
        {"tid": template_id, "tenant_id": current_user.tenant_id},
    )
    if not check.scalars().first():
        raise HTTPException(status_code=404, detail="Template not found")

    if body.category is not None and body.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}")

    set_parts = ["updated_at = NOW()"]
    params: dict = {"tid": template_id}

    if body.name is not None:
        set_parts.append("name = :name")
        params["name"] = body.name.strip()
    if body.category is not None:
        set_parts.append("category = :category")
        params["category"] = body.category
    if body.subject is not None:
        set_parts.append("subject = :subject")
        params["subject"] = body.subject
    if body.body is not None:
        set_parts.append("body = :body")
        params["body"] = body.body
    if body.language is not None:
        set_parts.append("language = :language")
        params["language"] = body.language
    if body.is_active is not None:
        set_parts.append("is_active = :is_active")
        params["is_active"] = body.is_active

    result = await db.execute(
        text(f"""
            UPDATE candidate_message_templates
            SET {', '.join(set_parts)}
            WHERE template_id = CAST(:tid AS uuid)
            RETURNING template_id, name, category, subject, body, language, is_active, created_at, updated_at
        """),
        params,
    )
    row = result.mappings().first()
    await db.commit()

    r = dict(row)
    r["template_id"] = str(r["template_id"])
    r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
    r["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else None
    return r


@template_router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a communication template (admin only)."""
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can delete templates")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    result = await db.execute(
        text("""
            DELETE FROM candidate_message_templates
            WHERE template_id = CAST(:tid AS uuid)
              AND tenant_id = CAST(:tenant_id AS uuid)
            RETURNING template_id
        """),
        {"tid": template_id, "tenant_id": current_user.tenant_id},
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Template not found")

    await db.commit()
    return {"status": "template deleted"}


# ── Communication History Endpoints ───────────────────────────────────────────


@comm_router.get("/{application_id}/communications")
async def list_communications(
    application_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List communication history for a candidate application."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Verify application belongs to this tenant
    app_check = await db.execute(
        text("""
            SELECT a.application_id FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = CAST(:app_id AS uuid)
              AND j.tenant_id = CAST(:tid AS uuid)
        """),
        {"app_id": application_id, "tid": current_user.tenant_id},
    )
    if not app_check.scalars().first():
        raise HTTPException(status_code=404, detail="Application not found")

    rows = await db.execute(
        text("""
            SELECT
                c.communication_id, c.channel, c.direction, c.subject,
                c.body, c.status, c.created_at,
                t.name AS template_name, t.category AS template_category,
                COALESCE(u.full_name, u.email) AS created_by_name
            FROM candidate_communications c
            LEFT JOIN candidate_message_templates t ON t.template_id = c.template_id
            LEFT JOIN users u ON u.user_id = c.created_by
            WHERE c.application_id = CAST(:app_id AS uuid)
            ORDER BY c.created_at DESC
        """),
        {"app_id": application_id},
    )

    comms = []
    for r in rows.mappings():
        row = dict(r)
        row["communication_id"] = str(row["communication_id"])
        row["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
        comms.append(row)

    return {"communications": comms}


@comm_router.post("/{application_id}/communications/log")
async def log_communication(
    application_id: str,
    body: CommunicationLog,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Log a drafted/simulated communication for a candidate. Does NOT send email."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    # Fetch application info to get candidate details and verify tenant
    app_check = await db.execute(
        text("""
            SELECT a.application_id, a.candidate_name, a.candidate_email
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = CAST(:app_id AS uuid)
              AND j.tenant_id = CAST(:tid AS uuid)
        """),
        {"app_id": application_id, "tid": current_user.tenant_id},
    )
    app_row = app_check.mappings().first()
    if not app_row:
        raise HTTPException(status_code=404, detail="Application not found")

    if body.status not in ("draft", "logged"):
        body.status = "draft"

    result = await db.execute(
        text("""
            INSERT INTO candidate_communications
                (tenant_id, application_id, candidate_email, candidate_name,
                 channel, direction, subject, body, status, template_id, created_by)
            VALUES
                (CAST(:tid AS uuid), CAST(:app_id AS uuid), :candidate_email, :candidate_name,
                 'email', 'outbound', :subject, :body, :status,
                 CAST(:template_id AS uuid), CAST(:uid AS uuid))
            RETURNING communication_id, channel, direction, subject, body, status, created_at
        """),
        {
            "tid": current_user.tenant_id,
            "app_id": application_id,
            "candidate_email": app_row["candidate_email"],
            "candidate_name": app_row["candidate_name"],
            "subject": body.subject,
            "body": body.body,
            "status": body.status,
            "template_id": body.template_id,
            "uid": current_user.user_id,
        },
    )
    row = result.mappings().first()
    await db.commit()

    r = dict(row)
    r["communication_id"] = str(r["communication_id"])
    r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
    return r
