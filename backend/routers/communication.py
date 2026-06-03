from datetime import datetime
from typing import Annotated
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import CurrentUserDep
from database import get_db, set_rls_context
from services.email_service import _send

template_router = APIRouter(prefix="/communication/templates", tags=["communication"])
comm_router = APIRouter(prefix="/applications", tags=["communication"])
automation_router = APIRouter(prefix="/communication/automation-rules", tags=["communication"])

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


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


class CommunicationSend(BaseModel):
    subject: str
    body: str
    to_email: str | None = None    # explicit one-message override; backend falls back to preferred_contact_email
    template_id: str | None = None


class CommunicationUpdate(BaseModel):
    subject: str | None = None
    body: str | None = None
    template_id: str | None = None


class PreferredContactUpdate(BaseModel):
    preferred_contact_email: str | None = None


class AutomationRuleCreate(BaseModel):
    event_type: str
    template_id: str | None = None
    mode: str = "draft_only"
    is_active: bool = True
    delay_minutes: int = 0


class AutomationRuleUpdate(BaseModel):
    template_id: str | None = None
    mode: str | None = None
    is_active: bool | None = None
    delay_minutes: int | None = None


VALID_CATEGORIES = {"interview_invitation", "rejection", "shortlisted", "offer", "request_info", "talent_pool", "general"}
VALID_EVENT_TYPES = {
    "workflow_shortlisted", "workflow_rejected", "offer_made",
    "interview_scheduled", "interview_completed", "talent_pool_added",
}
VALID_MODES = {"disabled", "draft_only", "auto_send"}

# Full application query for communication endpoints — includes all email fields
_APP_EMAIL_QUERY = """
    SELECT a.application_id, a.candidate_name,
           a.candidate_email, a.candidate_email_from_cv,
           a.email_sender_address, a.confirmation_email_recipient,
           a.preferred_contact_email, a.preferred_contact_locked,
           a.submission_source
    FROM applications a
    JOIN jobs j ON j.job_id = a.job_id
    WHERE a.application_id = CAST(:app_id AS uuid)
      AND j.tenant_id = CAST(:tid AS uuid)
"""

# Intake methods where submission email belongs to a recruiter/forwarder, not the candidate.
_FORWARDER_INTAKE_SOURCES = {"email_forwarding", "platform_email", "manual_upload"}


def _resolve_recipient(app_row: dict, explicit_override: str | None = None) -> str | None:
    """Return the best available recipient email using the canonical fallback chain.

    For forwarded/platform_email/manual_upload intake, the CV-extracted email is the
    candidate's own address and is preferred over the submission email, which may belong
    to a recruiter or email forwarder, not the actual candidate.
    """
    preferred  = app_row.get("preferred_contact_email")
    confirm    = app_row.get("confirmation_email_recipient")
    cv_email   = app_row.get("candidate_email_from_cv")
    sub_email  = app_row.get("candidate_email")
    sender     = app_row.get("email_sender_address")
    source     = app_row.get("submission_source") or ""

    if source in _FORWARDER_INTAKE_SOURCES:
        # submission email may be a recruiter/forwarder — prefer CV-extracted address
        return (explicit_override or preferred or confirm or cv_email or sub_email) or None

    # public_apply: submission email is the candidate's own address
    return (explicit_override or preferred or confirm or sub_email or cv_email or sender) or None


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
                c.body, c.status, c.error_message, c.candidate_email, c.created_at,
                c.is_automated, c.event_type,
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
    """Log a drafted/simulated communication. Does NOT send email. Draft saving allowed without email."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    app_check = await db.execute(text(_APP_EMAIL_QUERY), {"app_id": application_id, "tid": current_user.tenant_id})
    app_row = app_check.mappings().first()
    if not app_row:
        raise HTTPException(status_code=404, detail="Application not found")

    if body.status not in ("draft", "logged"):
        body.status = "draft"

    # Cache the resolved email (may be NULL — drafts are allowed without email)
    cached_email = _resolve_recipient(dict(app_row))

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
            "candidate_email": cached_email,
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


@comm_router.post("/{application_id}/communications/send")
async def send_email_to_candidate(
    application_id: str,
    body: CommunicationSend,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Send an email to a candidate using the preferred contact fallback chain."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    app_check = await db.execute(text(_APP_EMAIL_QUERY), {"app_id": application_id, "tid": current_user.tenant_id})
    app_row = app_check.mappings().first()
    if not app_row:
        raise HTTPException(status_code=404, detail="Application not found")

    # Resolve recipient: explicit override → preferred_contact_email → fallback chain
    to_email = _resolve_recipient(dict(app_row), explicit_override=body.to_email)
    if not to_email:
        raise HTTPException(status_code=400, detail="Candidate email address is missing. Set a preferred contact email or provide a recipient override.")

    text_body = _strip_html(body.body)

    error_message: str | None = None
    try:
        await _send(to_email, body.subject, body.body, text_body)
        comm_status = "sent"
    except Exception as e:
        comm_status = "failed"
        error_message = str(e)

    # Store actual to_email used (not candidate_email from application — the real recipient)
    result = await db.execute(
        text("""
            INSERT INTO candidate_communications
                (tenant_id, application_id, candidate_email, candidate_name,
                 channel, direction, subject, body, status, error_message, template_id, created_by)
            VALUES
                (CAST(:tid AS uuid), CAST(:app_id AS uuid), :candidate_email, :candidate_name,
                 'email', 'outbound', :subject, :body, :status, :error_message,
                 CAST(:template_id AS uuid), CAST(:uid AS uuid))
            RETURNING communication_id, channel, direction, subject, body, status, error_message, created_at
        """),
        {
            "tid": current_user.tenant_id,
            "app_id": application_id,
            "candidate_email": to_email,
            "candidate_name": app_row["candidate_name"],
            "subject": body.subject,
            "body": body.body,
            "status": comm_status,
            "error_message": error_message,
            "template_id": body.template_id,
            "uid": current_user.user_id,
        },
    )
    row = result.mappings().first()
    await db.commit()

    if comm_status == "failed":
        raise HTTPException(status_code=500, detail=f"Email send failed: {error_message}")

    r = dict(row)
    r["communication_id"] = str(r["communication_id"])
    r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
    return r


def _strip_html(html: str) -> str:
    """Very basic HTML-to-text for plain text email parts."""
    text = html.replace("<br/>", "\n").replace("<br>", "\n").replace("<p>", "").replace("</p>", "\n")
    return re.sub(r"<[^>]+>", "", text).strip()


async def _verify_comm_ownership(
    db: AsyncSession,
    application_id: str,
    communication_id: str,
    tenant_id: str,
    allowed_statuses: list[str] | None = None,
) -> dict:
    """Return communication record or raise 404/403. Optionally restrict by status."""
    row = await db.execute(
        text("""
            SELECT c.communication_id, c.subject, c.body, c.status, c.template_id,
                   c.candidate_email, c.candidate_name, c.error_message, c.created_at
            FROM candidate_communications c
            JOIN applications a ON a.application_id = c.application_id
            JOIN jobs j ON j.job_id = a.job_id
            WHERE c.communication_id = CAST(:cid AS uuid)
              AND c.application_id = CAST(:app_id AS uuid)
              AND j.tenant_id = CAST(:tid AS uuid)
        """),
        {"cid": communication_id, "app_id": application_id, "tid": tenant_id},
    )
    comm = row.mappings().first()
    if not comm:
        raise HTTPException(status_code=404, detail="Communication not found")
    if allowed_statuses and comm["status"] not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"This action is only allowed on {' or '.join(allowed_statuses)} communications",
        )
    return dict(comm)


@comm_router.patch("/{application_id}/preferred-contact")
async def update_preferred_contact(
    application_id: str,
    body: PreferredContactUpdate,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Set or update the preferred contact email for a candidate application."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    if body.preferred_contact_email:
        email = body.preferred_contact_email.strip().lower()
        if not _EMAIL_RE.match(email):
            raise HTTPException(status_code=400, detail="Invalid email format")
        body.preferred_contact_email = email

    check = await db.execute(
        text("""
            SELECT a.application_id FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            WHERE a.application_id = CAST(:app_id AS uuid)
              AND j.tenant_id = CAST(:tid AS uuid)
        """),
        {"app_id": application_id, "tid": current_user.tenant_id},
    )
    if not check.scalars().first():
        raise HTTPException(status_code=404, detail="Application not found")

    result = await db.execute(
        text("""
            UPDATE applications SET
                preferred_contact_email = :email,
                preferred_contact_source = 'manual_override'
            WHERE application_id = CAST(:app_id AS uuid)
            RETURNING preferred_contact_email, preferred_contact_source
        """),
        {
            "app_id": application_id,
            "email": body.preferred_contact_email,
        },
    )
    row = result.mappings().first()
    await db.commit()

    return {
        "preferred_contact_email": row["preferred_contact_email"],
        "preferred_contact_source": row["preferred_contact_source"],
    }


@comm_router.patch("/{application_id}/communications/{communication_id}")
async def update_communication(
    application_id: str,
    communication_id: str,
    body: CommunicationUpdate,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Edit a draft communication. Only draft records can be updated."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    await _verify_comm_ownership(
        db, application_id, communication_id, current_user.tenant_id,
        allowed_statuses=["draft"],
    )

    set_parts: list[str] = []
    params: dict = {"cid": communication_id}

    if body.subject is not None:
        set_parts.append("subject = :subject")
        params["subject"] = body.subject
    if body.body is not None:
        set_parts.append("body = :body")
        params["body"] = body.body
    if body.template_id is not None:
        set_parts.append("template_id = CAST(:template_id AS uuid)")
        params["template_id"] = body.template_id

    if not set_parts:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await db.execute(
        text(f"""
            UPDATE candidate_communications
            SET {', '.join(set_parts)}
            WHERE communication_id = CAST(:cid AS uuid)
            RETURNING communication_id, subject, body, status, error_message, created_at
        """),
        params,
    )
    row = result.mappings().first()
    await db.commit()

    r = dict(row)
    r["communication_id"] = str(r["communication_id"])
    r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
    return r


@comm_router.post("/{application_id}/communications/{communication_id}/send")
async def send_existing_communication(
    application_id: str,
    communication_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Send an existing draft or failed communication. Updates in-place — no duplicate created."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    comm = await _verify_comm_ownership(
        db, application_id, communication_id, current_user.tenant_id,
        allowed_statuses=["draft", "failed"],
    )

    # Use stored candidate_email; if NULL (old drafts without email), resolve from application
    to_email = comm["candidate_email"]
    if not to_email:
        app_check = await db.execute(text(_APP_EMAIL_QUERY), {"app_id": application_id, "tid": current_user.tenant_id})
        app_row = app_check.mappings().first()
        if app_row:
            to_email = _resolve_recipient(dict(app_row))

    if not to_email:
        raise HTTPException(status_code=400, detail="Candidate email address is missing. Set a preferred contact email first.")

    subject = comm["subject"] or "(no subject)"
    html_body = comm["body"] or ""
    text_body = _strip_html(html_body)

    error_message: str | None = None
    try:
        await _send(to_email, subject, html_body, text_body)
        new_status = "sent"
    except Exception as e:
        new_status = "failed"
        error_message = str(e)

    # Update status and store the actual to_email used
    result = await db.execute(
        text("""
            UPDATE candidate_communications
            SET status = :status, error_message = :error_message, candidate_email = :to_email
            WHERE communication_id = CAST(:cid AS uuid)
            RETURNING communication_id, subject, body, status, error_message, candidate_email, created_at
        """),
        {"cid": communication_id, "status": new_status, "error_message": error_message, "to_email": to_email},
    )
    row = result.mappings().first()
    await db.commit()

    if new_status == "failed":
        raise HTTPException(status_code=500, detail=f"Email send failed: {error_message}")

    r = dict(row)
    r["communication_id"] = str(r["communication_id"])
    r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
    return r


@comm_router.delete("/{application_id}/communications/{communication_id}")
async def delete_communication(
    application_id: str,
    communication_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a draft communication. Sent, logged, and failed records are preserved for audit."""
    await set_rls_context(db, current_user.tenant_id, current_user.role)

    await _verify_comm_ownership(
        db, application_id, communication_id, current_user.tenant_id,
        allowed_statuses=["draft"],
    )

    await db.execute(
        text("DELETE FROM candidate_communications WHERE communication_id = CAST(:cid AS uuid)"),
        {"cid": communication_id},
    )
    await db.commit()
    return {"status": "communication deleted"}


# ── Automation Rules Endpoints ─────────────────────────────────────────────────


@automation_router.get("/")
async def list_automation_rules(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all communication automation rules for the tenant (admin only)."""
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view automation rules")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    rows = await db.execute(
        text("""
            SELECT r.rule_id, r.event_type, r.category, r.mode, r.is_active,
                   r.delay_minutes, r.created_at, r.updated_at,
                   r.template_id, t.name AS template_name
            FROM communication_automation_rules r
            LEFT JOIN candidate_message_templates t ON t.template_id = r.template_id
            WHERE r.tenant_id = CAST(:tid AS uuid)
            ORDER BY r.event_type
        """),
        {"tid": current_user.tenant_id},
    )

    rules = []
    for r in rows.mappings():
        row = dict(r)
        row["rule_id"] = str(row["rule_id"])
        row["template_id"] = str(row["template_id"]) if row["template_id"] else None
        row["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
        row["updated_at"] = row["updated_at"].isoformat() if row["updated_at"] else None
        rules.append(row)

    return {"rules": rules}


@automation_router.post("/")
async def create_automation_rule(
    body: AutomationRuleCreate,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a communication automation rule (admin only). One rule per event type per tenant."""
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can create automation rules")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    if body.event_type not in VALID_EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid event_type. Must be one of: {', '.join(sorted(VALID_EVENT_TYPES))}")
    if body.mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Must be one of: {', '.join(sorted(VALID_MODES))}")
    if body.delay_minutes < 0:
        raise HTTPException(status_code=400, detail="delay_minutes cannot be negative")

    if body.template_id:
        tpl_check = await db.execute(
            text("SELECT template_id FROM candidate_message_templates WHERE template_id = CAST(:tid AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)"),
            {"tid": body.template_id, "tenant_id": current_user.tenant_id},
        )
        if not tpl_check.scalars().first():
            raise HTTPException(status_code=404, detail="Template not found")

    try:
        result = await db.execute(
            text("""
                INSERT INTO communication_automation_rules
                    (tenant_id, event_type, category, template_id, mode, is_active, delay_minutes, created_by)
                VALUES
                    (CAST(:tid AS uuid), :event_type, 'workflow',
                     CAST(:template_id AS uuid), :mode, :is_active, :delay_minutes, CAST(:uid AS uuid))
                RETURNING rule_id, event_type, category, mode, is_active, delay_minutes, template_id, created_at, updated_at
            """),
            {
                "tid": current_user.tenant_id,
                "event_type": body.event_type,
                "template_id": body.template_id,
                "mode": body.mode,
                "is_active": body.is_active,
                "delay_minutes": body.delay_minutes,
                "uid": current_user.user_id,
            },
        )
        row = result.mappings().first()
        await db.commit()

        r = dict(row)
        r["rule_id"] = str(r["rule_id"])
        r["template_id"] = str(r["template_id"]) if r["template_id"] else None
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        r["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else None
        return r
    except Exception as e:
        if "uq_automation_event_per_tenant" in str(e) or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail=f"An automation rule for '{body.event_type}' already exists. Update the existing rule instead.")
        raise HTTPException(status_code=400, detail="Failed to create automation rule")


@automation_router.patch("/{rule_id}")
async def update_automation_rule(
    rule_id: str,
    body: AutomationRuleUpdate,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update an existing automation rule (admin only)."""
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can update automation rules")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    check = await db.execute(
        text("SELECT rule_id FROM communication_automation_rules WHERE rule_id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid)"),
        {"rid": rule_id, "tid": current_user.tenant_id},
    )
    if not check.scalars().first():
        raise HTTPException(status_code=404, detail="Automation rule not found")

    if body.mode is not None and body.mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Must be one of: {', '.join(sorted(VALID_MODES))}")
    if body.delay_minutes is not None and body.delay_minutes < 0:
        raise HTTPException(status_code=400, detail="delay_minutes cannot be negative")

    if body.template_id is not None:
        tpl_check = await db.execute(
            text("SELECT template_id FROM candidate_message_templates WHERE template_id = CAST(:tid AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)"),
            {"tid": body.template_id, "tenant_id": current_user.tenant_id},
        )
        if not tpl_check.scalars().first():
            raise HTTPException(status_code=404, detail="Template not found")

    set_parts = ["updated_at = NOW()"]
    params: dict = {"rid": rule_id}

    if body.template_id is not None:
        set_parts.append("template_id = CAST(:template_id AS uuid)")
        params["template_id"] = body.template_id
    if body.mode is not None:
        set_parts.append("mode = :mode")
        params["mode"] = body.mode
    if body.is_active is not None:
        set_parts.append("is_active = :is_active")
        params["is_active"] = body.is_active
    if body.delay_minutes is not None:
        set_parts.append("delay_minutes = :delay_minutes")
        params["delay_minutes"] = body.delay_minutes

    if len(set_parts) == 1:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await db.execute(
        text(f"""
            UPDATE communication_automation_rules
            SET {', '.join(set_parts)}
            WHERE rule_id = CAST(:rid AS uuid)
            RETURNING rule_id, event_type, category, mode, is_active, delay_minutes, template_id, created_at, updated_at
        """),
        params,
    )
    row = result.mappings().first()
    await db.commit()

    r = dict(row)
    r["rule_id"] = str(r["rule_id"])
    r["template_id"] = str(r["template_id"]) if r["template_id"] else None
    r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
    r["updated_at"] = r["updated_at"].isoformat() if r["updated_at"] else None
    return r


@automation_router.delete("/{rule_id}")
async def delete_automation_rule(
    rule_id: str,
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete an automation rule (admin only)."""
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Only admins can delete automation rules")

    await set_rls_context(db, current_user.tenant_id, current_user.role)

    result = await db.execute(
        text("DELETE FROM communication_automation_rules WHERE rule_id = CAST(:rid AS uuid) AND tenant_id = CAST(:tid AS uuid) RETURNING rule_id"),
        {"rid": rule_id, "tid": current_user.tenant_id},
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Automation rule not found")

    await db.commit()
    return {"status": "automation rule deleted"}
