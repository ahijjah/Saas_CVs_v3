"""
Communication event processor for recruitment workflow automation.

Handles draft creation and auto-send for tenant-configured event rules.
Never raises — failures are logged and swallowed so the caller's transaction
(workflow status change, interview creation) is never rolled back by this module.
"""

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ALLOWED_EVENT_TYPES = frozenset({
    "workflow_shortlisted",
    "workflow_rejected",
    "offer_made",
    "interview_scheduled",
    "interview_completed",
    "talent_pool_added",
})


def _render(template_text: str, ctx: dict) -> str:
    """Replace {{variable}} placeholders; missing keys → empty string."""
    def _sub(m: re.Match) -> str:
        return str(ctx.get(m.group(1).strip(), ""))
    return re.sub(r"\{\{([^}]+)\}\}", _sub, template_text)


def _strip_html(html: str) -> str:
    text = html.replace("<br/>", "\n").replace("<br>", "\n").replace("<p>", "").replace("</p>", "\n")
    return re.sub(r"<[^>]+>", "", text).strip()


async def process_communication_event(
    db: AsyncSession,
    application_id: str,
    event_type: str,
    tenant_id: str,
    context: dict | None = None,
) -> None:
    """
    Process a recruitment event against the tenant's automation rules.
    Inserts a communication record (draft or sent) if a matching active rule exists.
    Must be called BEFORE the caller's db.commit() — the INSERT is part of the same transaction.
    All exceptions are caught internally; this function never raises.
    """
    if event_type not in ALLOWED_EVENT_TYPES:
        return

    ctx = context or {}

    try:
        # 1. Load active rule + template for this tenant + event
        rule_row = await db.execute(
            text("""
                SELECT r.rule_id, r.mode, r.template_id,
                       t.subject AS tpl_subject, t.body AS tpl_body
                FROM communication_automation_rules r
                LEFT JOIN candidate_message_templates t
                    ON t.template_id = r.template_id
                    AND t.tenant_id = CAST(:tid AS uuid)
                WHERE r.tenant_id = CAST(:tid AS uuid)
                  AND r.event_type = :event_type
                  AND r.is_active = TRUE
                  AND r.mode != 'disabled'
                LIMIT 1
            """),
            {"tid": tenant_id, "event_type": event_type},
        )
        rule = rule_row.mappings().first()
        if not rule:
            return

        # 2. Deduplication: skip if an active automated record already exists for this event
        dup = await db.execute(
            text("""
                SELECT 1 FROM candidate_communications
                WHERE application_id = CAST(:app_id AS uuid)
                  AND event_type = :event_type
                  AND is_automated = TRUE
                  AND status != 'failed'
                LIMIT 1
            """),
            {"app_id": application_id, "event_type": event_type},
        )
        if dup.scalars().first():
            return

        # 3. Fetch application + job for context and recipient resolution
        app_row = await db.execute(
            text("""
                SELECT a.candidate_name,
                       a.preferred_contact_email,
                       a.confirmation_email_recipient,
                       a.candidate_email,
                       a.candidate_email_from_cv,
                       a.email_sender_address,
                       a.submission_source,
                       j.title AS job_title
                FROM applications a
                JOIN jobs j ON j.job_id = a.job_id
                WHERE a.application_id = CAST(:app_id AS uuid)
                  AND a.tenant_id = CAST(:tid AS uuid)
            """),
            {"app_id": application_id, "tid": tenant_id},
        )
        app = app_row.mappings().first()
        if not app:
            return

        # 4. Resolve recipient — for forwarded/platform/manual intake the submission
        #    email may belong to a recruiter, not the candidate; prefer CV-extracted.
        _forwarder_sources = {"email_forwarding", "platform_email", "manual_upload"}
        _src = app.get("submission_source") or ""
        if _src in _forwarder_sources:
            to_email = (
                app["preferred_contact_email"]
                or app["confirmation_email_recipient"]
                or app["candidate_email_from_cv"]
                or app["candidate_email"]
            ) or None
        else:
            to_email = (
                app["preferred_contact_email"]
                or app["confirmation_email_recipient"]
                or app["candidate_email"]
                or app["candidate_email_from_cv"]
                or app["email_sender_address"]
            ) or None

        # 5. Render template with available context variables
        tpl_ctx = {
            "candidate_name": app["candidate_name"] or "",
            "job_title": app["job_title"] or "",
            **ctx,
        }
        subject = _render(rule["tpl_subject"] or "", tpl_ctx) or "(no subject)"
        body = _render(rule["tpl_body"] or "", tpl_ctx)

        rule_id = str(rule["rule_id"])
        template_id = str(rule["template_id"]) if rule["template_id"] else None
        mode = rule["mode"]

        # 6. Create communication record based on mode
        if mode == "draft_only":
            await db.execute(
                text("""
                    INSERT INTO candidate_communications
                        (tenant_id, application_id, candidate_email, candidate_name,
                         channel, direction, subject, body, status,
                         template_id, event_type, automation_rule_id, is_automated)
                    VALUES
                        (CAST(:tid AS uuid), CAST(:app_id AS uuid), :to_email, :candidate_name,
                         'email', 'outbound', :subject, :body, 'draft',
                         CAST(:template_id AS uuid), :event_type, CAST(:rule_id AS uuid), TRUE)
                """),
                {
                    "tid": tenant_id, "app_id": application_id,
                    "to_email": to_email, "candidate_name": app["candidate_name"],
                    "subject": subject, "body": body,
                    "template_id": template_id, "event_type": event_type, "rule_id": rule_id,
                },
            )

        elif mode == "auto_send":
            comm_status = "draft"
            error_message = None

            if to_email:
                try:
                    from services.email_service import _send
                    await _send(to_email, subject, body, _strip_html(body))
                    comm_status = "sent"
                except Exception as e:
                    comm_status = "failed"
                    error_message = str(e)
                    logger.error(
                        "automation auto_send failed app=%s event=%s: %s",
                        application_id, event_type, e,
                    )
            # No email → fall through with draft so recruiter can review and fill in recipient

            await db.execute(
                text("""
                    INSERT INTO candidate_communications
                        (tenant_id, application_id, candidate_email, candidate_name,
                         channel, direction, subject, body, status, error_message,
                         template_id, event_type, automation_rule_id, is_automated)
                    VALUES
                        (CAST(:tid AS uuid), CAST(:app_id AS uuid), :to_email, :candidate_name,
                         'email', 'outbound', :subject, :body, :status, :error_message,
                         CAST(:template_id AS uuid), :event_type, CAST(:rule_id AS uuid), TRUE)
                """),
                {
                    "tid": tenant_id, "app_id": application_id,
                    "to_email": to_email, "candidate_name": app["candidate_name"],
                    "subject": subject, "body": body,
                    "status": comm_status, "error_message": error_message,
                    "template_id": template_id, "event_type": event_type, "rule_id": rule_id,
                },
            )

    except Exception:
        logger.exception(
            "process_communication_event failed app=%s event=%s", application_id, event_type
        )
