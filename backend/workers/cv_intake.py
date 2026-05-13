"""
Celery task: poll IMAP inbox for new CV emails.

Supports two routing modes per job:
  Option 1 — Forwarding: email sent to FORWARDING_EMAIL (from system_config),
             job identified by job_code in subject (e.g. JOB-2026-0001).
  Option 2 — Alias: email sent directly to {job_id}@{domain},
             job resolved from TO address automatically.

Deduplication: message_id (email level) + SHA-256 hash (file level).
"""
import asyncio
import email
import email.utils
import hashlib
import logging
import re
from email.header import decode_header
from pathlib import Path

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

ATTACHMENT_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/octet-stream": "pdf",  # fallback — treat unknown binary as PDF
}

# Matches JOB-2026-0001, JOB-2026-001, job-2026-1, etc.
JOB_CODE_RE = re.compile(r'\bJOB[-_](\d{4})[-_](\d{1,4})\b', re.IGNORECASE)


@celery_app.task(name="workers.cv_intake.poll_imap_inbox")
def poll_imap_inbox():
    asyncio.run(_poll_async())


async def _poll_async() -> None:
    import imaplib
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
        logger.info("IMAP: %d unseen messages", len(ids))

        for msg_id_bytes in ids:
            try:
                await _process_message(imap, msg_id_bytes)
            except Exception as exc:
                logger.error("Failed to process message %s: %s", msg_id_bytes, exc)

        imap.logout()
    except Exception as exc:
        logger.error("IMAP polling error: %s", exc)


async def _process_message(imap, msg_id_bytes: bytes) -> None:
    from config import get_settings
    from database import AsyncSessionLocal, set_rls_context
    from sqlalchemy import text

    cfg = get_settings()

    _, msg_data = imap.fetch(msg_id_bytes, "(RFC822)")
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    message_id = msg.get("Message-ID", "").strip()
    subject    = _decode_header_str(msg.get("Subject", ""))
    sender     = email.utils.parseaddr(msg.get("From", ""))[1].lower()
    sender_name = email.utils.parseaddr(msg.get("From", ""))[0] or sender.split("@")[0]
    # TO may be a list; grab the first address that's relevant
    to_header  = msg.get("To", "") or msg.get("Delivered-To", "")
    recipient  = email.utils.parseaddr(to_header)[1].lower()

    async with AsyncSessionLocal() as db:
        await set_rls_context(db, "", "super_admin")

        # ── Email-level deduplication ─────────────────────────────────────
        if message_id:
            dup = await db.execute(
                text("SELECT log_id FROM email_ingest_log WHERE message_id = :mid"),
                {"mid": message_id},
            )
            if dup.first():
                logger.debug("Skipping duplicate message_id %s", message_id)
                imap.store(msg_id_bytes, "+FLAGS", "\\Seen")
                return

        # Load FORWARDING_EMAIL from system_config
        fwd_row = await db.execute(
            text("SELECT value FROM system_config WHERE key = 'forwarding_email'")
        )
        forwarding_email = (fwd_row.scalar_one_or_none() or cfg.imap_user).lower()

        # ── Resolve routing ───────────────────────────────────────────────
        job_id, tenant_id, ingestion_mode, reject_reason = await _resolve_routing(
            db, recipient, subject, forwarding_email, sender=sender
        )

        if not job_id:
            logger.warning("Unroutable email from %s — %s", sender, reject_reason)
            await _log_ingest(db, message_id, sender, recipient, subject,
                              None, None, None, "unassigned", reject_reason, "unknown")
            imap.store(msg_id_bytes, "+FLAGS", "\\Seen")
            return

        # ── Process attachments ───────────────────────────────────────────
        processed_any = False
        for part in msg.walk():
            content_type = part.get_content_type().lower()
            disposition  = (part.get_content_disposition() or "").lower()

            # Accept attachments; also accept inline binaries with filenames
            filename = part.get_filename()
            if not filename and disposition not in ("attachment",):
                continue

            # Resolve MIME type — fall back to extension sniffing
            if content_type not in ATTACHMENT_MIME_TYPES:
                if filename:
                    ext = filename.rsplit(".", 1)[-1].lower()
                    if ext == "pdf":
                        content_type = "application/pdf"
                    elif ext in ("docx",):
                        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif ext in ("doc",):
                        content_type = "application/msword"
                    else:
                        await _log_ingest(db, message_id, sender, recipient, subject,
                                          tenant_id, job_id, None, "rejected",
                                          f"Unsupported file type: {filename}", ingestion_mode)
                        continue
                else:
                    continue

            attachment_bytes = part.get_payload(decode=True)
            if not attachment_bytes:
                continue

            filename = filename or f"cv.{ATTACHMENT_MIME_TYPES[content_type]}"
            file_hash = hashlib.sha256(attachment_bytes).hexdigest()

            # ── File-level deduplication ──────────────────────────────────
            hash_dup = await db.execute(
                text("""
                    SELECT log_id FROM email_ingest_log
                    WHERE attachment_hash = :hash AND job_id = :jid
                """),
                {"hash": file_hash, "jid": job_id},
            )
            if hash_dup.first():
                logger.info("Skipping duplicate file (hash %s) for job %s", file_hash[:8], job_id)
                await _log_ingest(db, message_id, sender, recipient, subject,
                                  tenant_id, job_id, None, "duplicate",
                                  "Identical file already processed", ingestion_mode,
                                  file_hash, filename)
                processed_any = True  # still mark as seen
                continue

            try:
                application_id = await _create_application_and_score(
                    db, job_id, tenant_id, sender_name, sender,
                    attachment_bytes, content_type, filename, cfg,
                    ingestion_mode=ingestion_mode,
                )
                await _log_ingest(db, message_id, sender, recipient, subject,
                                  tenant_id, job_id, application_id, "scored",
                                  None, ingestion_mode, file_hash, filename)
                processed_any = True
            except Exception as exc:
                logger.error("Failed processing %s: %s", filename, exc)
                await _log_ingest(db, message_id, sender, recipient, subject,
                                  tenant_id, job_id, None, "failed",
                                  str(exc), ingestion_mode, file_hash, filename)

        if not processed_any:
            await _log_ingest(db, message_id, sender, recipient, subject,
                              tenant_id, job_id, None, "skipped",
                              "No valid attachments found", ingestion_mode)

    imap.store(msg_id_bytes, "+FLAGS", "\\Seen")


async def _resolve_routing(
    db, recipient: str, subject: str, forwarding_email: str, sender: str = ""
) -> tuple[str | None, str | None, str, str | None]:
    """Return (job_id, tenant_id, ingestion_mode, reject_reason)."""
    from sqlalchemy import text

    # ── Option 2: Platform email alias — TO matches a job's platform_email ─
    job_row = await db.execute(
        text("""
            SELECT j.job_id, j.tenant_id, j.receive_cv_via_platform_email
            FROM jobs j
            WHERE LOWER(j.platform_email) = :email AND j.status = 'active'
        """),
        {"email": recipient},
    )
    job = job_row.mappings().first()
    if job:
        if not job["receive_cv_via_platform_email"]:
            return None, None, "platform_email", "Platform email receiving disabled for this job"
        return str(job["job_id"]), str(job["tenant_id"]), "platform_email", None

    # ── Option 1: Forwarding — recipient is the central FORWARDING_EMAIL ─
    if recipient == forwarding_email or recipient.split("@")[0] == forwarding_email.split("@")[0]:
        # Extract job_code from subject: JOB-2026-0001 or JOB-2026-1
        match = JOB_CODE_RE.search(subject)
        if not match:
            return None, None, "forwarding", f"No job code found in subject: '{subject}'"

        job_code = f"JOB-{match.group(1)}-{int(match.group(2)):04d}"

        fwd_row = await db.execute(
            text("""
                SELECT j.job_id, j.tenant_id,
                       j.receive_cv_via_forwarding_email,
                       j.restrict_forwarding_sender_to_tenant_email,
                       t.email_domain
                FROM jobs j
                JOIN tenants t ON t.tenant_id = j.tenant_id
                WHERE UPPER(j.job_code) = UPPER(:code) AND j.status = 'active'
            """),
            {"code": job_code},
        )
        fwd_job = fwd_row.mappings().first()
        if not fwd_job:
            return None, None, "forwarding", f"Job code '{job_code}' not found or not active"
        if not fwd_job["receive_cv_via_forwarding_email"]:
            return None, None, "forwarding", f"Forwarding receiving disabled for job '{job_code}'"
        # Sender-domain restriction: only allow emails from the tenant's own domain
        if fwd_job["restrict_forwarding_sender_to_tenant_email"] and fwd_job["email_domain"]:
            sender_domain = sender.split("@")[-1].lower() if "@" in sender else ""
            if sender_domain != fwd_job["email_domain"].lower():
                return None, None, "forwarding", (
                    f"Sender domain '{sender_domain}' not allowed for job '{job_code}' "
                    f"(tenant domain: {fwd_job['email_domain']})"
                )
        return str(fwd_job["job_id"]), str(fwd_job["tenant_id"]), "forwarding", None

    return None, None, "unknown", f"Recipient '{recipient}' not recognised"


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
    ingestion_mode: str = "forwarding",
) -> str:
    from sqlalchemy import text
    from workers.cv_score import score_cv_task

    submission_source = "platform_email" if ingestion_mode == "platform_email" else "email_forwarding"

    app_result = await db.execute(
        text("""
            INSERT INTO applications
                (job_id, tenant_id, candidate_name, candidate_email,
                 submission_source, processing_status)
            VALUES (:jid, :tid, :name, :email, :src, 'pending')
            RETURNING application_id
        """),
        {"jid": job_id, "tid": tenant_id, "name": candidate_name, "email": candidate_email,
         "src": submission_source},
    )
    application_id = str(app_result.scalar_one())

    ext = ATTACHMENT_MIME_TYPES.get(mime_type, "pdf")
    file_dir  = Path(cfg.files_base_path) / "tenants" / tenant_id / "jobs" / job_id
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / f"{application_id}.{ext}"
    file_path.write_bytes(file_bytes)

    await db.execute(
        text("""
            INSERT INTO application_files
                (application_id, tenant_id, original_name, mime_type,
                 file_path, file_size_bytes, extraction_status)
            VALUES (:aid, :tid, :orig, :mime, :path, :size, 'pending')
        """),
        {
            "aid":  application_id,
            "tid":  tenant_id,
            "orig": filename,
            "mime": mime_type,
            "path": str(file_path.relative_to(cfg.files_base_path)),
            "size": len(file_bytes),
        },
    )
    await db.commit()

    score_cv_task.delay(
        application_id=application_id,
        job_id=job_id,
        tenant_id=tenant_id,
        file_path=str(file_path),
        mime_type=mime_type,
    )
    return application_id


async def _log_ingest(
    db,
    message_id: str | None,
    sender: str,
    recipient: str,
    subject: str,
    tenant_id: str | None,
    job_id: str | None,
    application_id: str | None,
    log_status: str,
    error_msg: str | None,
    ingestion_mode: str = "unknown",
    attachment_hash: str | None = None,
    attachment_name: str | None = None,
) -> None:
    from sqlalchemy import text

    await db.execute(
        text("""
            INSERT INTO email_ingest_log (
                message_id, sender_email, recipient_email, subject,
                tenant_id, job_id, application_id,
                ingestion_mode, status, error_message,
                attachment_hash, attachment_name
            ) VALUES (
                :mid, :sender, :recipient, :subject,
                :tid, :jid, :aid,
                :mode, :status, :err,
                :hash, :att_name
            )
            ON CONFLICT (message_id) DO UPDATE SET
                status        = EXCLUDED.status,
                error_message = EXCLUDED.error_message
        """),
        {
            "mid":      message_id,
            "sender":   sender,
            "recipient": recipient,
            "subject":  subject,
            "tid":      tenant_id,
            "jid":      job_id,
            "aid":      application_id,
            "mode":     ingestion_mode,
            "status":   log_status,
            "err":      error_msg,
            "hash":     attachment_hash,
            "att_name": attachment_name,
        },
    )
    await db.commit()


def _decode_header_str(raw: str) -> str:
    parts = decode_header(raw)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)
