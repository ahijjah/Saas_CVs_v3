"""Celery task: poll IMAP inbox for new CV emails."""
import asyncio
import email
import hashlib
import imaplib
import logging
import re
import tempfile
from email.header import decode_header
from pathlib import Path

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

ATTACHMENT_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    # Common alternate MIME type for DOCX
    "application/msword": "doc",
}


@celery_app.task(name="workers.cv_intake.poll_imap_inbox")
def poll_imap_inbox():
    asyncio.run(_poll_async())


async def _poll_async() -> None:
    from config import get_settings
    cfg = get_settings()

    try:
        if cfg.imap_use_ssl:
            imap = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
        else:
            imap = imaplib.IMAP4(cfg.imap_host, cfg.imap_port)

        imap.login(cfg.imap_user, cfg.imap_password)
        imap.select("INBOX")

        _, msg_ids = imap.search(None, "UNSEEN")
        if not msg_ids or not msg_ids[0]:
            imap.logout()
            return

        ids = msg_ids[0].split()
        logger.info("IMAP: found %d unseen messages", len(ids))

        for msg_id_bytes in ids:
            try:
                await _process_message(imap, msg_id_bytes)
            except Exception as exc:
                logger.error("Failed to process message %s: %s", msg_id_bytes, exc)

        imap.logout()
    except Exception as exc:
        logger.error("IMAP polling error: %s", exc)


async def _process_message(imap: imaplib.IMAP4, msg_id_bytes: bytes) -> None:
    from config import get_settings
    from database import AsyncSessionLocal, set_rls_context
    from services.docx_service import convert_docx_to_pdf
    from services.pdf_service import extract_text_from_pdf
    from sqlalchemy import text

    cfg = get_settings()

    _, msg_data = imap.fetch(msg_id_bytes, "(RFC822)")
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    # Decode headers
    message_id = msg.get("Message-ID", "").strip()
    subject = _decode_header(msg.get("Subject", ""))
    sender = email.utils.parseaddr(msg.get("From", ""))[1]
    recipient = email.utils.parseaddr(msg.get("To", ""))[1].lower()

    async with AsyncSessionLocal() as db:
        await set_rls_context(db, "", "super_admin")

        # Deduplication check
        if message_id:
            dup = await db.execute(
                text("SELECT log_id FROM email_ingest_log WHERE message_id = :mid"),
                {"mid": message_id},
            )
            if dup.first():
                logger.debug("Skipping duplicate message_id %s", message_id)
                imap.store(msg_id_bytes, "+FLAGS", "\\Seen")
                return

        # Determine ingestion mode: platform_email or forwarding
        job_id, tenant_id, ingestion_mode = await _resolve_routing(db, recipient, subject, sender, cfg)

        if not job_id or not tenant_id:
            await _log_ingest(db, message_id, sender, recipient, subject, None, None, None, "skipped",
                              "Could not resolve job or tenant")
            imap.store(msg_id_bytes, "+FLAGS", "\\Seen")
            return

        # Fetch job title
        job_row = await db.execute(
            text("SELECT title FROM jobs WHERE job_id = :jid AND status = 'active'"),
            {"jid": job_id},
        )
        job = job_row.mappings().first()
        if not job:
            await _log_ingest(db, message_id, sender, recipient, subject, tenant_id, job_id,
                              None, "skipped", "Job not active")
            imap.store(msg_id_bytes, "+FLAGS", "\\Seen")
            return

        # Extract candidate name from sender
        sender_name = email.utils.parseaddr(msg.get("From", ""))[0] or sender.split("@")[0]

        # Process attachments
        processed_any = False
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type not in ATTACHMENT_MIME_TYPES:
                continue
            if part.get_content_disposition() not in ("attachment", "inline"):
                continue

            filename = part.get_filename() or f"cv.{ATTACHMENT_MIME_TYPES[content_type]}"
            attachment_bytes = part.get_payload(decode=True)
            if not attachment_bytes:
                continue

            try:
                application_id = await _create_application_and_score(
                    db, job_id, tenant_id, sender_name, sender,
                    attachment_bytes, content_type, filename, cfg,
                )
                await _log_ingest(db, message_id, sender, recipient, subject,
                                  tenant_id, job_id, application_id, "scored", None, ingestion_mode)
                processed_any = True
            except Exception as exc:
                logger.error("Failed processing attachment %s: %s", filename, exc)
                await _log_ingest(db, message_id, sender, recipient, subject,
                                  tenant_id, job_id, None, "failed", str(exc), ingestion_mode)

        if not processed_any:
            await _log_ingest(db, message_id, sender, recipient, subject,
                              tenant_id, job_id, None, "skipped", "No valid attachments", ingestion_mode)

        imap.store(msg_id_bytes, "+FLAGS", "\\Seen")


async def _resolve_routing(db, recipient: str, subject: str, sender: str, cfg) -> tuple[str | None, str | None, str]:
    """Return (job_id, tenant_id, ingestion_mode)."""
    from sqlalchemy import text

    # Platform email mode: {job_id}@{email_domain}
    local_part = recipient.split("@")[0] if "@" in recipient else ""
    domain_part = recipient.split("@")[1] if "@" in recipient else ""

    # Try to match job by platform_email
    job_row = await db.execute(
        text("""
            SELECT j.job_id, j.tenant_id FROM jobs j
            WHERE j.platform_email = :email AND j.status = 'active'
        """),
        {"email": recipient},
    )
    job = job_row.mappings().first()
    if job:
        return str(job["job_id"]), str(job["tenant_id"]), "platform_email"

    # Forwarding mode: recipient is a tenant's forwarding_email
    # Extract job code from subject (e.g. [JOB-<uuid>] or job_id directly)
    job_code_match = re.search(r"[Jj][Oo][Bb][- _]([0-9a-f-]{36})", subject)
    if job_code_match:
        candidate_job_id = job_code_match.group(1)
        fwd_row = await db.execute(
            text("""
                SELECT j.job_id, j.tenant_id FROM jobs j
                WHERE j.job_id = :jid AND j.status = 'active'
            """),
            {"jid": candidate_job_id},
        )
        fwd_job = fwd_row.mappings().first()
        if fwd_job:
            return str(fwd_job["job_id"]), str(fwd_job["tenant_id"]), "forwarding"

    # Validate sender belongs to a registered tenant (sender domain check)
    sender_domain = sender.split("@")[1] if "@" in sender else ""
    tenant_row = await db.execute(
        text("SELECT tenant_id FROM tenants WHERE email_domain = :domain AND status = 'active'"),
        {"domain": sender_domain},
    )
    tenant = tenant_row.mappings().first()
    if tenant:
        # Try to find most recent active job for this tenant if only one exists
        jobs_row = await db.execute(
            text("""
                SELECT job_id FROM jobs
                WHERE tenant_id = :tid AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
            """),
            {"tid": str(tenant["tenant_id"])},
        )
        j = jobs_row.mappings().first()
        if j:
            return str(j["job_id"]), str(tenant["tenant_id"]), "forwarding"

    return None, None, "unknown"


async def _create_application_and_score(
    db,
    job_id: str,
    tenant_id: str,
    candidate_name: str,
    candidate_email: str,
    file_bytes: bytes,
    mime_type: str,
    filename: str,
    cfg,
) -> str:
    from services.docx_service import convert_docx_to_pdf
    from services.pdf_service import extract_text_from_pdf
    from sqlalchemy import text
    from workers.cv_score import score_cv_task

    # Create application
    app_result = await db.execute(
        text("""
            INSERT INTO applications
                (job_id, tenant_id, candidate_name, candidate_email, submission_source, processing_status)
            VALUES (:jid, :tid, :name, :email, 'email', 'pending')
            RETURNING application_id
        """),
        {"jid": job_id, "tid": tenant_id, "name": candidate_name, "email": candidate_email},
    )
    application_id = str(app_result.scalar_one())

    # Save file
    ext = ATTACHMENT_MIME_TYPES.get(mime_type, "pdf")
    file_dir = Path(cfg.files_base_path) / "tenants" / tenant_id / "jobs" / job_id
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / f"{application_id}.{ext}"
    file_path.write_bytes(file_bytes)

    relative_path = str(file_path.relative_to(cfg.files_base_path))
    await db.execute(
        text("""
            INSERT INTO application_files
                (application_id, tenant_id, original_name, mime_type, file_path, file_size_bytes, extraction_status)
            VALUES (:aid, :tid, :orig, :mime, :path, :size, 'pending')
        """),
        {
            "aid": application_id,
            "tid": tenant_id,
            "orig": filename,
            "mime": mime_type,
            "path": relative_path,
            "size": len(file_bytes),
        },
    )
    await db.commit()

    # Enqueue scoring (async Celery task)
    score_cv_task.delay(
        application_id=application_id,
        job_id=job_id,
        tenant_id=tenant_id,
        file_path=str(file_path),
        mime_type=mime_type,
    )

    return application_id


async def _log_ingest(
    db, message_id, sender, recipient, subject,
    tenant_id, job_id, application_id, log_status, error_msg,
    ingestion_mode="unknown",
) -> None:
    from sqlalchemy import text

    await db.execute(
        text("""
            INSERT INTO email_ingest_log
                (message_id, sender_email, recipient_email, subject,
                 tenant_id, job_id, application_id, ingestion_mode, status, error_message)
            VALUES (:mid, :sender, :recipient, :subject,
                    :tid, :jid, :aid, :mode, :status, :err)
            ON CONFLICT (message_id) DO NOTHING
        """),
        {
            "mid": message_id or None,
            "sender": sender,
            "recipient": recipient,
            "subject": subject,
            "tid": tenant_id,
            "jid": job_id,
            "aid": application_id,
            "mode": ingestion_mode,
            "status": log_status,
            "err": error_msg,
        },
    )
    await db.commit()


def _decode_header(raw: str) -> str:
    parts = decode_header(raw)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)
