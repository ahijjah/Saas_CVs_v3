"""
Celery task: poll IMAP inbox for new CV emails.

Supports two routing modes per job:
  Option 1 — Forwarding: email sent to FORWARDING_EMAIL (from system_config),
             job identified by job_code extracted from subject then body.
  Option 2 — Alias: email sent directly to {job_id}@{domain},
             job resolved from TO address automatically.

Job-code extraction order (forwarding mode):
  1. Subject line
  2. Plain-text body
  3. HTML body (safe text stripping via stdlib html.parser)
  First match wins; source is logged for diagnostics.

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
import uuid
import re
from datetime import datetime, timezone
from email.header import decode_header
from html.parser import HTMLParser
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


# ── HTML safe-text extraction ─────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Strip HTML tags and collect visible text nodes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_tags = {"script", "style", "head"}
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _html_to_text(html_bytes: bytes, charset: str = "utf-8") -> str:
    """Safely extract visible text from HTML bytes. Never raises."""
    try:
        raw = html_bytes.decode(charset, errors="replace")
        extractor = _TextExtractor()
        extractor.feed(raw)
        return extractor.get_text()
    except Exception as exc:
        logger.debug("HTML text extraction failed: %s", exc)
        return ""


# ── Email body extraction ─────────────────────────────────────────────────────

_EMAIL_BODY_MAX_CHARS = 6000

def _extract_body_plain(msg) -> str | None:
    """
    Extract the plain-text body of an email message.

    Preference: text/plain part.  Falls back to text/html stripped to visible
    text via _html_to_text().  Returns None if no readable body is found.
    Truncated to _EMAIL_BODY_MAX_CHARS to stay within AI token budgets.
    """
    if msg is None:
        return None

    plain_text: str | None = None
    html_bytes: bytes | None = None
    html_charset = "utf-8"

    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain" and plain_text is None:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    plain_text = payload.decode(charset, errors="replace")
                except Exception:
                    plain_text = payload.decode("utf-8", errors="replace")
        elif ct == "text/html" and html_bytes is None:
            html_bytes = part.get_payload(decode=True) or b""
            html_charset = part.get_content_charset() or "utf-8"

    body = plain_text or (_html_to_text(html_bytes, html_charset) if html_bytes else None)
    if not body:
        return None
    body = body.strip()
    if not body:
        return None

    full_len = len(body)
    if full_len > _EMAIL_BODY_MAX_CHARS:
        logger.warning(
            "Email body truncated: full=%d chars stored=%d chars (%.0f%% lost) — "
            "candidate answers beyond char %d will not reach knockout AI",
            full_len, _EMAIL_BODY_MAX_CHARS,
            (full_len - _EMAIL_BODY_MAX_CHARS) / full_len * 100,
            _EMAIL_BODY_MAX_CHARS,
        )
    logger.debug(
        "Email body head (0:500): %r",
        body[:500],
    )
    return body[:_EMAIL_BODY_MAX_CHARS]


# ── Job-code extraction ───────────────────────────────────────────────────────

def _extract_job_code_from_message(subject: str, msg) -> tuple[str | None, str]:
    """
    Extract and normalise a job code from the email, trying in priority order:
      1. Subject line
      2. Plain-text body part
      3. HTML body part (safe text extraction)

    Returns (normalised_code_or_None, source_label).
    source_label is one of: 'subject' | 'text_body' | 'html_body' | 'none'
    """
    # 1. Subject
    match = JOB_CODE_RE.search(subject)
    if match:
        return f"JOB-{match.group(1)}-{int(match.group(2)):04d}", "subject"

    if msg is None:
        return None, "none"

    # Collect body parts in a single walk (avoid double walk)
    plain_text: str | None = None
    html_bytes: bytes | None = None
    html_charset = "utf-8"

    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain" and plain_text is None:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    plain_text = payload.decode(charset, errors="replace")
                except Exception:
                    plain_text = payload.decode("utf-8", errors="replace")
        elif ct == "text/html" and html_bytes is None:
            html_bytes = part.get_payload(decode=True) or b""
            html_charset = part.get_content_charset() or "utf-8"

    # 2. Plain-text body
    if plain_text:
        match = JOB_CODE_RE.search(plain_text)
        if match:
            return f"JOB-{match.group(1)}-{int(match.group(2)):04d}", "text_body"

    # 3. HTML body
    if html_bytes:
        html_text = _html_to_text(html_bytes, html_charset)
        match = JOB_CODE_RE.search(html_text)
        if match:
            return f"JOB-{match.group(1)}-{int(match.group(2)):04d}", "html_body"

    return None, "none"


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

    # Capture processing start time before any I/O — written to every log row.
    msg_processing_started = datetime.now(timezone.utc)

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

        # ── Resolve routing (passes full msg for body fallback) ───────────
        from services.intake_notification_service import (
            AttachmentResult,
            IntakeStatus,
            queue_candidate_notification,
            queue_recruiter_inactive_alert,
            queue_recruiter_unmatched_alert,
        )

        # Extract plain-text body once for the whole message (used for knockout analysis).
        # Done before attachment loop so it's available regardless of per-attachment outcome.
        email_body_plain: str | None = _extract_body_plain(msg)
        logger.info(
            "Email body extraction — uid=%s body_chars=%d",
            msg_id_bytes,
            len(email_body_plain) if email_body_plain else 0,
        )

        job_id, tenant_id, ingestion_mode, reject_reason, inactive_job_id, job_code_source = (
            await _resolve_routing(db, recipient, subject, forwarding_email, sender=sender, msg=msg)
        )

        if not job_id:
            logger.warning("Unroutable email from=%s — %s", sender, reject_reason)
            intake_log_id = await _log_ingest(
                db, message_id, sender, recipient, subject,
                None, None, None, "unassigned", reject_reason, "unknown",
                processing_started_at=msg_processing_started,
            )

            # Candidate notification for identifiable unrouted cases
            if inactive_job_id:
                await queue_candidate_notification(
                    db,
                    sender_email=sender,
                    sender_name=sender_name,
                    tenant_id=None,
                    job_id=inactive_job_id,
                    ingestion_mode=ingestion_mode,
                    attachment_results=[AttachmentResult(filename=None, status=IntakeStatus.JOB_INACTIVE)],
                    intake_log_id=intake_log_id,
                )
                await queue_recruiter_inactive_alert(
                    db, job_id=inactive_job_id, intake_log_id=intake_log_id,
                )
            elif ingestion_mode == "forwarding":
                # Forwarding inbox: no job code or unknown job code — tell candidate
                await queue_candidate_notification(
                    db,
                    sender_email=sender,
                    sender_name=sender_name,
                    tenant_id=None,
                    job_id=None,
                    ingestion_mode=ingestion_mode,
                    attachment_results=[AttachmentResult(filename=None, status=IntakeStatus.JOB_NOT_IDENTIFIED)],
                    intake_log_id=intake_log_id,
                )
                await queue_recruiter_unmatched_alert(db, intake_log_id=intake_log_id)

            processed_at = datetime.now(timezone.utc)
            duration_ms = int((processed_at - msg_processing_started).total_seconds() * 1000)
            if intake_log_id:
                await _update_log_completion(db, intake_log_id, processed_at, duration_ms, None)
            imap.store(msg_id_bytes, "+FLAGS", "\\Seen")
            return

        # ── Attachment processing ─────────────────────────────────────────
        processed_any    = False
        attachment_results: list[AttachmentResult] = []
        last_intake_log_id: str | None = None

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
                        last_intake_log_id = await _log_ingest(
                            db, message_id, sender, recipient, subject,
                            tenant_id, job_id, None, "rejected",
                            f"Unsupported file type: {filename}", ingestion_mode,
                            job_code_source=job_code_source,
                            processing_started_at=msg_processing_started,
                        )
                        attachment_results.append(AttachmentResult(
                            filename=filename,
                            status=IntakeStatus.UNSUPPORTED_FILE_TYPE,
                            error_detail=f"Unsupported extension: {ext}",
                        ))
                        continue
                else:
                    continue

            attachment_bytes = part.get_payload(decode=True)
            if not attachment_bytes:
                continue

            # File size check
            max_bytes = cfg.max_file_size_mb * 1024 * 1024
            if len(attachment_bytes) > max_bytes:
                fname_for_log = filename or f"cv.{ATTACHMENT_MIME_TYPES.get(content_type, 'pdf')}"
                last_intake_log_id = await _log_ingest(
                    db, message_id, sender, recipient, subject,
                    tenant_id, job_id, None, "rejected",
                    f"File size {len(attachment_bytes)} bytes exceeds limit {max_bytes}",
                    ingestion_mode,
                    job_code_source=job_code_source,
                    processing_started_at=msg_processing_started,
                )
                attachment_results.append(AttachmentResult(
                    filename=fname_for_log,
                    status=IntakeStatus.FILE_SIZE_EXCEEDED,
                    error_detail=f"Size {len(attachment_bytes)} > limit {max_bytes}",
                ))
                continue

            filename  = filename or f"cv.{ATTACHMENT_MIME_TYPES[content_type]}"
            file_hash = hashlib.sha256(attachment_bytes).hexdigest()

            # File-level deduplication is handled by the scoring pipeline (Step 2b)
            # via application_intake_log.file_hash and canonical_text_fingerprint.
            # Email intake always creates an application and enqueues it; the
            # scoring worker detects duplicates uniformly across all intake methods.

            try:
                # Enforce rolling 30-day CV quota before creating application
                from services.subscription_service import can_process_cv
                cv_check = await can_process_cv(tenant_id, db)
                if not cv_check["allowed"]:
                    logger.warning(
                        "CV quota exceeded for tenant=%s (%d/%d in last 30 days) — skipping file=%s",
                        tenant_id, cv_check["used"], cv_check["limit"], filename,
                    )
                    last_intake_log_id = await _log_ingest(
                        db, message_id, sender, recipient, subject,
                        tenant_id, job_id, None, "rejected",
                        "CV quota exceeded", ingestion_mode, file_hash, filename,
                        job_code_source=job_code_source,
                        processing_started_at=msg_processing_started,
                    )
                    attachment_results.append(AttachmentResult(
                        filename=filename,
                        status=IntakeStatus.REJECTED,
                    ))
                    processed_any = True
                    continue

                application_id, scoring_enqueued_at = await _create_application_and_score(
                    db, job_id, tenant_id, sender_name, sender,
                    attachment_bytes, content_type, filename, cfg,
                    ingestion_mode=ingestion_mode,
                    email_body_plain=email_body_plain,
                    email_subject=subject,
                    email_sender=sender,
                )
                logger.info(
                    "Application inserted — id=%s file=%s job=%s tenant=%s routing=%s uid=%s",
                    application_id, filename, job_id, tenant_id,
                    ingestion_mode, msg_id_bytes,
                )
                last_intake_log_id = await _log_ingest(
                    db, message_id, sender, recipient, subject,
                    tenant_id, job_id, application_id, "scored",
                    None, ingestion_mode, file_hash, filename,
                    job_code_source=job_code_source,
                    processing_started_at=msg_processing_started,
                    scoring_enqueued_at=scoring_enqueued_at,
                )
                attachment_results.append(AttachmentResult(
                    filename=filename,
                    status=IntakeStatus.RECEIVED_SUCCESSFULLY,
                    application_id=application_id,
                ))
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
                    exc_str = str(exc).lower()
                    if "password" in exc_str or "encrypted" in exc_str:
                        err_status = IntakeStatus.FILE_PASSWORD_PROTECTED
                    elif "empty" in exc_str or "no text" in exc_str:
                        err_status = IntakeStatus.EMPTY_CV_CONTENT
                    else:
                        err_status = IntakeStatus.FILE_CORRUPTED
                    last_intake_log_id = await _log_ingest(
                        db, message_id, sender, recipient, subject,
                        tenant_id, job_id, None, "failed",
                        str(exc), ingestion_mode, file_hash, filename,
                        job_code_source=job_code_source,
                        processing_started_at=msg_processing_started,
                    )
                    attachment_results.append(AttachmentResult(
                        filename=filename,
                        status=err_status,
                        error_detail=str(exc),
                    ))
                except Exception as log_exc:
                    logger.error("Could not write failure log: %s", log_exc)

        if not processed_any and not had_processing_error:
            last_intake_log_id = await _log_ingest(
                db, message_id, sender, recipient, subject,
                tenant_id, job_id, None, "skipped",
                "No valid attachments found", ingestion_mode,
                job_code_source=job_code_source,
                processing_started_at=msg_processing_started,
            )
            attachment_results.append(AttachmentResult(
                filename=None,
                status=IntakeStatus.NO_ATTACHMENT,
            ))

        # ── Consolidated candidate notification ───────────────────────────
        notification_queued_at: datetime | None = None
        if attachment_results:
            try:
                await queue_candidate_notification(
                    db,
                    sender_email=sender,
                    sender_name=sender_name,
                    tenant_id=tenant_id,
                    job_id=job_id,
                    ingestion_mode=ingestion_mode,
                    attachment_results=attachment_results,
                    intake_log_id=last_intake_log_id,
                )
                notification_queued_at = datetime.now(timezone.utc)
                logger.info(
                    "Candidate notification queued — uid=%s job=%s", msg_id_bytes, job_id
                )
            except Exception as notif_exc:
                logger.warning(
                    "Notification queue failed for uid=%s: %s", msg_id_bytes, notif_exc,
                )

        # ── Record completion timestamps ──────────────────────────────────
        processed_at = datetime.now(timezone.utc)
        duration_ms = int((processed_at - msg_processing_started).total_seconds() * 1000)
        logger.info(
            "Message processing complete — uid=%s duration=%dms job=%s routing=%s",
            msg_id_bytes, duration_ms, job_id, ingestion_mode,
        )
        if last_intake_log_id:
            await _update_log_completion(
                db, last_intake_log_id, processed_at, duration_ms, notification_queued_at
            )

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
    db,
    recipient: str,
    subject: str,
    forwarding_email: str,
    sender: str = "",
    msg=None,
) -> tuple[str | None, str | None, str, str | None, str | None, str | None]:
    """
    Return (job_id, tenant_id, ingestion_mode, reject_reason, inactive_job_id, job_code_source).

    job_code_source is 'subject' | 'text_body' | 'html_body' | None.
    inactive_job_id is populated when a job WAS found by address/code but its
    status is not 'active'. Callers use this to trigger per-job recruiter alerts
    and send candidates a JOB_INACTIVE notification.
    """
    from sqlalchemy import text

    # Option 2: platform_email alias — TO matches a job's dedicated address
    job_row = await db.execute(
        text("""
            SELECT j.job_id, j.tenant_id, j.status, j.receive_cv_via_platform_email
            FROM jobs j
            WHERE LOWER(j.platform_email) = :email
        """),
        {"email": recipient},
    )
    job = job_row.mappings().first()
    if job:
        logger.info("Routing: platform_email alias matched recipient=%s", recipient)
        if job["status"] != "active":
            return (
                None, None, "platform_email",
                f"Job is not active (status: {job['status']})",
                str(job["job_id"]),
                None,
            )
        if not job["receive_cv_via_platform_email"]:
            return None, None, "platform_email", "Platform email receiving disabled for this job", None, None
        return str(job["job_id"]), str(job["tenant_id"]), "platform_email", None, None, None

    # Option 1: forwarding — recipient is the central inbox, job from subject/body
    if recipient == forwarding_email or recipient.split("@")[0] == forwarding_email.split("@")[0]:
        job_code, job_code_source = _extract_job_code_from_message(subject, msg)

        if not job_code:
            logger.info(
                "Routing: forwarding mode — no job code found in subject, text body, or HTML body"
            )
            return None, None, "forwarding", f"No job code found in subject: '{subject}'", None, None

        logger.info(
            "Routing: forwarding mode — job_code=%s extracted from %s",
            job_code, job_code_source,
        )

        fwd_row = await db.execute(
            text("""
                SELECT j.job_id, j.tenant_id, j.status,
                       j.receive_cv_via_forwarding_email,
                       j.restrict_forwarding_sender_to_tenant_email,
                       t.email_domain
                FROM jobs j
                JOIN tenants t ON t.tenant_id = j.tenant_id
                WHERE UPPER(j.job_code) = UPPER(:code)
            """),
            {"code": job_code},
        )
        fwd_job = fwd_row.mappings().first()
        if not fwd_job:
            return None, None, "forwarding", f"Job code '{job_code}' not found", None, job_code_source
        if fwd_job["status"] != "active":
            return (
                None, None, "forwarding",
                f"Job '{job_code}' is not active (status: {fwd_job['status']})",
                str(fwd_job["job_id"]),
                job_code_source,
            )
        if not fwd_job["receive_cv_via_forwarding_email"]:
            return None, None, "forwarding", f"Forwarding receiving disabled for job '{job_code}'", None, job_code_source
        if fwd_job["restrict_forwarding_sender_to_tenant_email"] and fwd_job["email_domain"]:
            sender_domain = sender.split("@")[-1].lower() if "@" in sender else ""
            if sender_domain != fwd_job["email_domain"].lower():
                return None, None, "forwarding", (
                    f"Sender domain '{sender_domain}' not allowed for job '{job_code}' "
                    f"(tenant domain: {fwd_job['email_domain']})"
                ), None, job_code_source
        return str(fwd_job["job_id"]), str(fwd_job["tenant_id"]), "forwarding", None, None, job_code_source

    return None, None, "unknown", f"Recipient '{recipient}' not recognised", None, None


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
    email_body_plain: str | None = None,
    email_subject: str | None = None,
    email_sender: str | None = None,
) -> tuple[str, datetime]:
    """
    Delegate to the shared intake service and return (application_id, scoring_enqueued_at).

    application/octet-stream is remapped to application/pdf before the call
    since email intake allows it as a fallback but the service only recognises
    the three canonical MIME types.
    """
    from services.application_intake_service import process_cv_intake

    submission_source = "platform_email" if ingestion_mode == "platform_email" else "email_forwarding"
    effective_mime = mime_type if mime_type != "application/octet-stream" else "application/pdf"

    result = await process_cv_intake(
        db,
        intake_method=ingestion_mode,
        job_id=job_id,
        tenant_id=tenant_id,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        content_type=effective_mime,
        content=file_bytes,
        original_filename=filename,
        submission_source=submission_source,
        auto_score=True,
        files_base_path=cfg.files_base_path,
        max_file_size_mb=cfg.max_file_size_mb,
        email_sender_address=candidate_email,
        email_body_plain=email_body_plain,
        subject=email_subject,
        sender_email=email_sender,
    )

    if not result.success:
        raise RuntimeError(f"Intake service returned {result.status}: {result.error_message}")

    logger.info(
        "Application created via intake service — id=%s intake_log=%s body_stored=%s",
        result.application_id, result.intake_log_id,
        "yes" if email_body_plain else "no (empty/absent)",
    )
    return result.application_id, result.scoring_enqueued_at


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
    *,
    job_code_source: str | None = None,
    processing_started_at: datetime | None = None,
    scoring_enqueued_at: datetime | None = None,
) -> str | None:
    """
    Insert / upsert an email_ingest_log row and return its log_id.

    ON CONFLICT (message_id): status and error_message are always refreshed.
    processing_started_at and scoring_enqueued_at use COALESCE so the first
    non-null value wins across multiple attachment-level calls for the same email.
    """
    from sqlalchemy import text

    result = await db.execute(
        text("""
            INSERT INTO email_ingest_log (
                message_id, sender_email, recipient_email, subject,
                tenant_id, job_id, application_id,
                ingestion_mode, status, error_message,
                attachment_hash, attachment_name,
                job_code_source, processing_started_at, scoring_enqueued_at
            ) VALUES (
                :mid, :sender, :recipient, :subject,
                :tid, :jid, :aid,
                :mode, :status, :err,
                :hash, :att_name,
                :job_code_source, :processing_started_at, :scoring_enqueued_at
            )
            ON CONFLICT (message_id) DO UPDATE SET
                status                = EXCLUDED.status,
                error_message         = EXCLUDED.error_message,
                processing_started_at = COALESCE(
                    email_ingest_log.processing_started_at,
                    EXCLUDED.processing_started_at
                ),
                scoring_enqueued_at   = COALESCE(
                    email_ingest_log.scoring_enqueued_at,
                    EXCLUDED.scoring_enqueued_at
                ),
                job_code_source       = COALESCE(
                    email_ingest_log.job_code_source,
                    EXCLUDED.job_code_source
                )
            RETURNING log_id
        """),
        {
            "mid":                   message_id,
            "sender":                sender,
            "recipient":             recipient,
            "subject":               subject,
            "tid":                   tenant_id,
            "jid":                   job_id,
            "aid":                   application_id,
            "mode":                  ingestion_mode,
            "status":                log_status,
            "err":                   error_msg,
            "hash":                  attachment_hash,
            "att_name":              attachment_name,
            "job_code_source":       job_code_source,
            "processing_started_at": processing_started_at,
            "scoring_enqueued_at":   scoring_enqueued_at,
        },
    )
    row = result.fetchone()
    await db.commit()
    return str(row[0]) if row else None


async def _update_log_completion(
    db,
    log_id: str,
    processed_at: datetime,
    processing_duration_ms: int,
    notification_queued_at: datetime | None,
) -> None:
    """Set final completion timestamps on an email_ingest_log row by log_id."""
    from sqlalchemy import text

    await db.execute(
        text("""
            UPDATE email_ingest_log
            SET processed_at           = :processed_at,
                processing_duration_ms = :duration_ms,
                notification_queued_at = :notification_queued_at
            WHERE log_id = CAST(:lid AS uuid)
        """),
        {
            "processed_at":           processed_at,
            "duration_ms":            processing_duration_ms,
            "notification_queued_at": notification_queued_at,
            "lid":                    log_id,
        },
    )
    await db.commit()
    logger.debug(
        "Log completion recorded — log_id=%s duration=%dms", log_id, processing_duration_ms
    )


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
