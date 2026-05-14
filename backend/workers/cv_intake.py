"""
Celery task: poll IMAP inbox for new CV emails.

Supports two routing modes per job:
  Option 1 — Forwarding: email sent to FORWARDING_EMAIL (from system_config),
             job identified by job_code in subject (e.g. JOB-2026-0001).
  Option 2 — Alias: email sent directly to {job_id}@{domain},
             job resolved from TO address automatically.

Deduplication: message_id (email level) + SHA-256 hash (file level).

EVENT LOOP SAFETY
-----------------
Celery prefork workers fork after module import. The global async engine in
database.py holds asyncpg connection pool futures bound to the pre-fork event
loop. When asyncio.run() is called inside a worker task it creates a NEW event
loop; any pre-existing asyncpg futures are "attached to a different loop".

Fix: create a fresh SQLAlchemy async engine with NullPool at the start of
every poll run and dispose it in a finally block. NullPool never reuses
connections, so no futures survive between asyncio.run() calls.
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

_IMAP_LOCK_KEY = "cv_intake:imap_poll_lock"
_IMAP_LOCK_TTL = 300  # seconds — generous upper bound for a single poll run


# ── Distributed lock ──────────────────────────────────────────────────────────

def _acquire_poll_lock() -> bool:
    """Acquire a Redis SET NX EX lock. Returns True if acquired."""
    try:
        import redis as redis_lib
        from config import get_settings
        client = redis_lib.Redis.from_url(get_settings().redis_url, socket_connect_timeout=2)
        acquired = client.set(_IMAP_LOCK_KEY, "1", nx=True, ex=_IMAP_LOCK_TTL)
        client.close()
        return bool(acquired)
    except Exception as exc:
        logger.warning("IMAP lock acquire failed (proceeding without lock): %s", exc)
        return True  # fail open — better to poll than to permanently skip


def _release_poll_lock() -> None:
    try:
        import redis as redis_lib
        from config import get_settings
        client = redis_lib.Redis.from_url(get_settings().redis_url, socket_connect_timeout=2)
        client.delete(_IMAP_LOCK_KEY)
        client.close()
    except Exception as exc:
        logger.warning("IMAP lock release failed: %s", exc)


# ── Celery task entry point ───────────────────────────────────────────────────

@celery_app.task(name="workers.cv_intake.poll_imap_inbox")
def poll_imap_inbox():
    if not _acquire_poll_lock():
        logger.info("IMAP poll skipped — previous poll still running")
        return
    try:
        asyncio.run(_poll_async())
    finally:
        _release_poll_lock()


# ── IMAP polling (runs inside a fresh asyncio.run() each time) ────────────────

async def _poll_async() -> None:
    import imaplib
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from config import get_settings
    from services.runtime_config import get_bool_secret, get_int_secret, get_secret

    cfg = get_settings()

    # Resolve IMAP config from DB (runtime_config is synchronous — safe here)
    imap_host     = get_secret("IMAP_HOST",     cfg.imap_host)
    imap_port     = get_int_secret("IMAP_PORT", cfg.imap_port)
    imap_user     = get_secret("IMAP_USER",     cfg.imap_user)
    imap_password = get_secret("IMAP_PASSWORD", cfg.imap_password)
    imap_use_ssl  = get_bool_secret("IMAP_USE_SSL", cfg.imap_use_ssl)

    logger.info(
        "IMAP poll starting — host=%s port=%d user=%s ssl=%s",
        imap_host, imap_port, imap_user, imap_use_ssl,
    )

    # ── Fresh async engine scoped to this asyncio.run() call ─────────────────
    # NullPool: no connection is ever held between awaits — completely loop-safe.
    # The engine and all its connections are disposed in the finally block before
    # asyncio.run() closes this event loop.
    engine = create_async_engine(
        cfg.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": cfg.db_schema}},
    )
    make_session = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    imap = None
    try:
        if imap_use_ssl:
            imap = imaplib.IMAP4_SSL(imap_host, imap_port)
        else:
            imap = imaplib.IMAP4(imap_host, imap_port)

        imap.login(imap_user, imap_password)
        imap.select("INBOX")

        _, msg_ids = imap.search(None, "UNSEEN")
        if not msg_ids or not msg_ids[0]:
            logger.info("IMAP poll complete — inbox empty")
            return

        ids = msg_ids[0].split()
        logger.info("IMAP: %d unseen message(s) detected", len(ids))

        for msg_id_bytes in ids:
            try:
                await _process_message(imap, msg_id_bytes, make_session, cfg)
            except Exception as exc:
                logger.error(
                    "Unhandled error processing IMAP uid=%s: %s",
                    msg_id_bytes, exc, exc_info=True,
                )

    except Exception as exc:
        logger.error("IMAP polling error: %s", exc, exc_info=True)
    finally:
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass
        # Dispose engine BEFORE asyncio.run() closes the loop — prevents
        # "Future is attached to a different loop" on the next poll run.
        await engine.dispose()


# ── Per-message processing ────────────────────────────────────────────────────

async def _process_message(imap, msg_id_bytes: bytes, make_session, cfg) -> None:
    from database import set_rls_context
    from sqlalchemy import text

    _, msg_data = imap.fetch(msg_id_bytes, "(RFC822)")
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    message_id  = msg.get("Message-ID", "").strip()
    subject     = _decode_header_str(msg.get("Subject", ""))
    sender      = email.utils.parseaddr(msg.get("From", ""))[1].lower()
    sender_name = email.utils.parseaddr(msg.get("From", ""))[0] or sender.split("@")[0]
    to_header   = msg.get("To", "") or msg.get("Delivered-To", "")
    recipient   = email.utils.parseaddr(to_header)[1].lower()

    logger.info(
        "Processing IMAP uid=%s from=%s to=%s subject=%r",
        msg_id_bytes, sender, recipient, subject[:80],
    )

    had_processing_error = False

    # Each message gets its own session — lifecycle is strictly local to this call.
    async with make_session() as db:
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
            logger.warning("Unroutable email from=%s — %s", sender, reject_reason)
            await _log_ingest(db, message_id, sender, recipient, subject,
                              None, None, None, "unassigned", reject_reason, "unknown")
            imap.store(msg_id_bytes, "+FLAGS", "\\Seen")
            return

        # ── Attachment processing ─────────────────────────────────────────
        processed_any = False

        for part in msg.walk():
            content_type = part.get_content_type().lower()
            disposition  = (part.get_content_disposition() or "").lower()
            filename     = part.get_filename()

            if not filename and disposition not in ("attachment",):
                continue

            # MIME type resolution with extension sniffing fallback
            if content_type not in ATTACHMENT_MIME_TYPES:
                if filename:
                    ext = filename.rsplit(".", 1)[-1].lower()
                    if ext == "pdf":
                        content_type = "application/pdf"
                    elif ext == "docx":
                        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif ext == "doc":
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

            filename  = filename or f"cv.{ATTACHMENT_MIME_TYPES[content_type]}"
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
                logger.info(
                    "Duplicate file hash=%s filename=%s job=%s — skipping",
                    file_hash[:8], filename, job_id,
                )
                await _log_ingest(db, message_id, sender, recipient, subject,
                                  tenant_id, job_id, None, "duplicate",
                                  "Identical file already processed", ingestion_mode,
                                  file_hash, filename)
                processed_any = True  # already in system, safe to mark seen
                continue

            try:
                application_id = await _create_application_and_score(
                    db, job_id, tenant_id, sender_name, sender,
                    attachment_bytes, content_type, filename, cfg,
                    ingestion_mode=ingestion_mode,
                )
                logger.info(
                    "Application inserted: id=%s file=%s job=%s tenant=%s uid=%s",
                    application_id, filename, job_id, tenant_id, msg_id_bytes,
                )
                await _log_ingest(db, message_id, sender, recipient, subject,
                                  tenant_id, job_id, application_id, "scored",
                                  None, ingestion_mode, file_hash, filename)
                logger.info(
                    "Ingest logged — application_id=%s uid=%s", application_id, msg_id_bytes
                )
                processed_any = True

            except Exception as exc:
                had_processing_error = True
                logger.error(
                    "FAILED: file=%s uid=%s job=%s error=%s",
                    filename, msg_id_bytes, job_id, exc, exc_info=True,
                )
                # Rollback any partial transaction so the session is usable for logging.
                try:
                    await db.rollback()
                except Exception as rb_exc:
                    logger.warning("Rollback after processing error failed: %s", rb_exc)
                try:
                    await _log_ingest(db, message_id, sender, recipient, subject,
                                      tenant_id, job_id, None, "failed",
                                      str(exc), ingestion_mode, file_hash, filename)
                except Exception as log_exc:
                    logger.error("Could not write failure log: %s", log_exc)

        if not processed_any and not had_processing_error:
            await _log_ingest(db, message_id, sender, recipient, subject,
                              tenant_id, job_id, None, "skipped",
                              "No valid attachments found", ingestion_mode)

    # ── Mark as seen ONLY after session closes cleanly ────────────────────────
    # If any attachment processing raised an exception we keep the email UNSEEN
    # so the next poll will retry it — preventing silent data loss.
    if had_processing_error:
        logger.warning(
            "IMAP uid=%s kept UNSEEN — processing errors occurred, will retry on next poll",
            msg_id_bytes,
        )
    else:
        imap.store(msg_id_bytes, "+FLAGS", "\\Seen")
        logger.info("IMAP uid=%s marked as seen", msg_id_bytes)


# ── Routing resolution ────────────────────────────────────────────────────────

async def _resolve_routing(
    db, recipient: str, subject: str, forwarding_email: str, sender: str = ""
) -> tuple[str | None, str | None, str, str | None]:
    """Return (job_id, tenant_id, ingestion_mode, reject_reason)."""
    from sqlalchemy import text

    # Option 2: platform_email alias — TO matches a job's dedicated address
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

    # Option 1: forwarding — recipient is the central inbox, job from subject
    if recipient == forwarding_email or recipient.split("@")[0] == forwarding_email.split("@")[0]:
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
        if fwd_job["restrict_forwarding_sender_to_tenant_email"] and fwd_job["email_domain"]:
            sender_domain = sender.split("@")[-1].lower() if "@" in sender else ""
            if sender_domain != fwd_job["email_domain"].lower():
                return None, None, "forwarding", (
                    f"Sender domain '{sender_domain}' not allowed for job '{job_code}' "
                    f"(tenant domain: {fwd_job['email_domain']})"
                )
        return str(fwd_job["job_id"]), str(fwd_job["tenant_id"]), "forwarding", None

    return None, None, "unknown", f"Recipient '{recipient}' not recognised"


# ── Application creation + score enqueue ─────────────────────────────────────

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
    """
    Insert application + file records, commit, save file to disk, enqueue scoring.

    Order ensures atomicity:
      1. INSERT applications          → get application_id
      2. Save file bytes to disk      → get file_path
      3. INSERT application_files     → link file record
      4. db.commit()                  → DB writes persisted
      5. score_cv_task.delay()        → enqueue; exception here keeps email unseen for retry
    """
    from sqlalchemy import text
    from workers.cv_score import score_cv_task

    submission_source = "platform_email" if ingestion_mode == "platform_email" else "email_forwarding"

    # Step 1: application record
    app_result = await db.execute(
        text("""
            INSERT INTO applications
                (job_id, tenant_id, candidate_name, candidate_email,
                 submission_source, processing_status)
            VALUES (:jid, :tid, :name, :email, :src, 'pending')
            RETURNING application_id
        """),
        {"jid": job_id, "tid": tenant_id, "name": candidate_name,
         "email": candidate_email, "src": submission_source},
    )
    application_id = str(app_result.scalar_one())

    # Step 2: save attachment
    ext      = ATTACHMENT_MIME_TYPES.get(mime_type, "pdf")
    file_dir = Path(cfg.files_base_path) / "tenants" / tenant_id / "jobs" / job_id
    file_dir.mkdir(parents=True, exist_ok=True)
    file_path = file_dir / f"{application_id}.{ext}"
    file_path.write_bytes(file_bytes)
    logger.debug("Attachment saved: path=%s bytes=%d", file_path, len(file_bytes))

    # Step 3: file record
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

    # Step 4: commit all DB writes
    await db.commit()
    logger.debug("DB commit OK — application_id=%s", application_id)

    # Step 5: enqueue scoring (after commit so scorer can read the rows)
    score_cv_task.delay(
        application_id=application_id,
        job_id=job_id,
        tenant_id=tenant_id,
        file_path=str(file_path),
        mime_type=mime_type,
    )
    logger.info("Scoring enqueued — application_id=%s filename=%s", application_id, filename)

    return application_id


# ── Ingest audit log ──────────────────────────────────────────────────────────

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


# ── Header decoding ───────────────────────────────────────────────────────────

def _decode_header_str(raw: str) -> str:
    parts = decode_header(raw)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)
