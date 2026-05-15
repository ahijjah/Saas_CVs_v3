"""
Intake-phase email notification service.

Queues candidate notifications and recruiter alerts for events that occur
during IMAP CV intake (cv_intake.py). Notifications are written to
notification_log with status='pending' and sent asynchronously by
notification_worker.send_notification_task.

Intake statuses
---------------
RECEIVED_SUCCESSFULLY   CV accepted, queued for scoring
DUPLICATE_APPLICATION   Identical file hash already exists for this job
NO_ATTACHMENT           Email had no recognisable CV attachment
UNSUPPORTED_FILE_TYPE   Attachment format not PDF / DOCX / DOC
FILE_CORRUPTED          Text extraction from CV failed
FILE_PASSWORD_PROTECTED CV file is password-protected
EMPTY_CV_CONTENT        CV file has no extractable text
FILE_SIZE_EXCEEDED      Attachment exceeds the configured max size
JOB_NOT_IDENTIFIED      Email to forwarding inbox; no matching job code
JOB_INACTIVE            Matched job exists but is no longer active
MULTIPLE_ATTACHMENTS    Multi-attachment message with mixed per-file results

Recruiter alerts
----------------
RECRUITER_UNMATCHED_ALERT  Threshold-based: too many unrouted emails
RECRUITER_INACTIVE_ALERT   Per-job: email received for an inactive job
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from html import escape as _esc

logger = logging.getLogger(__name__)


# ── Intake status constants ────────────────────────────────────────────────────

class IntakeStatus(str, Enum):
    """Outcome codes for each CV intake event."""
    RECEIVED_SUCCESSFULLY   = "RECEIVED_SUCCESSFULLY"
    DUPLICATE_APPLICATION   = "DUPLICATE_APPLICATION"
    NO_ATTACHMENT           = "NO_ATTACHMENT"
    UNSUPPORTED_FILE_TYPE   = "UNSUPPORTED_FILE_TYPE"
    FILE_CORRUPTED          = "FILE_CORRUPTED"
    FILE_PASSWORD_PROTECTED = "FILE_PASSWORD_PROTECTED"
    EMPTY_CV_CONTENT        = "EMPTY_CV_CONTENT"
    FILE_SIZE_EXCEEDED      = "FILE_SIZE_EXCEEDED"
    JOB_NOT_IDENTIFIED      = "JOB_NOT_IDENTIFIED"
    JOB_INACTIVE            = "JOB_INACTIVE"
    MULTIPLE_ATTACHMENTS    = "MULTIPLE_ATTACHMENTS"


# ── Per-attachment result ──────────────────────────────────────────────────────

@dataclass
class AttachmentResult:
    """Processing outcome for one attachment within an inbound email."""
    filename:         str | None
    status:           IntakeStatus
    application_id:   str | None = None
    duplicate_log_id: str | None = None
    error_detail:     str | None = None


# ── Email template helpers ─────────────────────────────────────────────────────

def _candidate_subject(status: IntakeStatus, job_title: str) -> str:
    t = job_title or "the advertised position"
    mapping = {
        IntakeStatus.RECEIVED_SUCCESSFULLY:   f"Your CV has been received – {t}",
        IntakeStatus.DUPLICATE_APPLICATION:   f"CV already received – {t}",
        IntakeStatus.NO_ATTACHMENT:           "Your application is missing an attachment",
        IntakeStatus.UNSUPPORTED_FILE_TYPE:   "Unable to process your CV",
        IntakeStatus.FILE_CORRUPTED:          "Unable to process your CV",
        IntakeStatus.FILE_PASSWORD_PROTECTED: "Unable to process your CV",
        IntakeStatus.EMPTY_CV_CONTENT:        "Unable to process your CV",
        IntakeStatus.FILE_SIZE_EXCEEDED:      "CV file too large",
        IntakeStatus.JOB_NOT_IDENTIFIED:      "We couldn’t identify the position you applied for",
        IntakeStatus.JOB_INACTIVE:            "Position no longer accepting applications",
        IntakeStatus.MULTIPLE_ATTACHMENTS:    f"Your application – {t}",
    }
    return mapping.get(status, f"Regarding your application – {t}")


def _attachment_label(s: IntakeStatus) -> str:
    """Short human label for one attachment status in a consolidated email."""
    return {
        IntakeStatus.RECEIVED_SUCCESSFULLY:   "Received successfully",
        IntakeStatus.DUPLICATE_APPLICATION:   "Already received (duplicate — no action needed)",
        IntakeStatus.UNSUPPORTED_FILE_TYPE:   "Unsupported format (please use PDF or Word)",
        IntakeStatus.FILE_CORRUPTED:          "Could not read file (possibly corrupted)",
        IntakeStatus.FILE_PASSWORD_PROTECTED: "File is password-protected",
        IntakeStatus.EMPTY_CV_CONTENT:        "File appears to be empty",
        IntakeStatus.FILE_SIZE_EXCEEDED:      "File too large",
        IntakeStatus.NO_ATTACHMENT:           "No attachment found",
    }.get(s, "Processing error")


def build_candidate_email(
    status: IntakeStatus,
    sender_name: str,
    job_title: str,
    results: list[AttachmentResult],
    from_name: str,
) -> tuple[str, str, str]:
    """
    Return (subject, text_body, html_body) for the given intake status.
    `results` is used only for MULTIPLE_ATTACHMENTS.
    """
    subject = _candidate_subject(status, job_title)
    name = sender_name or "Applicant"
    t    = job_title or "the advertised position"
    greeting = f"Dear {name},"
    sig = f"\n\nThe {from_name} Recruitment Team"

    if status == IntakeStatus.RECEIVED_SUCCESSFULLY:
        body = (
            f"{greeting}\n\n"
            f"We have received your CV for the position: {t}.\n"
            "Our team will review your application. Thank you for your interest."
        )
    elif status == IntakeStatus.DUPLICATE_APPLICATION:
        body = (
            f"{greeting}\n\n"
            f"We have already received your CV for the position: {t}.\n"
            "There is no need to resubmit. Thank you for your interest."
        )
    elif status == IntakeStatus.NO_ATTACHMENT:
        body = (
            f"{greeting}\n\n"
            f"We received your email for the position: {t}, "
            "but it did not include a CV attachment.\n"
            "Please reply with your CV attached as a PDF or Word document (.pdf, .docx)."
        )
    elif status == IntakeStatus.UNSUPPORTED_FILE_TYPE:
        body = (
            f"{greeting}\n\n"
            f"We were unable to process the CV file you sent for: {t}.\n"
            "Please submit your CV as a PDF (.pdf) or Word document (.docx, .doc)."
        )
    elif status == IntakeStatus.FILE_CORRUPTED:
        body = (
            f"{greeting}\n\n"
            f"We were unable to read the CV file you sent for: {t}.\n"
            "The file may be damaged or in an unsupported format. "
            "Please check the file and resubmit."
        )
    elif status == IntakeStatus.FILE_PASSWORD_PROTECTED:
        body = (
            f"{greeting}\n\n"
            f"The CV file you sent for: {t} appears to be password-protected.\n"
            "Please send an unprotected version of your CV."
        )
    elif status == IntakeStatus.EMPTY_CV_CONTENT:
        body = (
            f"{greeting}\n\n"
            f"Your CV file for the position: {t} appears to be empty.\n"
            "Please check the file and resubmit."
        )
    elif status == IntakeStatus.FILE_SIZE_EXCEEDED:
        body = (
            f"{greeting}\n\n"
            f"The CV file you sent for: {t} exceeds our maximum file size limit.\n"
            "Please reduce the file size and resubmit."
        )
    elif status == IntakeStatus.JOB_NOT_IDENTIFIED:
        body = (
            f"{greeting}\n\n"
            "We received your email but were unable to identify the position "
            "you are applying for.\n"
            "Please include the job reference code in the subject line of your email "
            "(for example: JOB-2024-0001) and try again."
        )
    elif status == IntakeStatus.JOB_INACTIVE:
        body = (
            f"{greeting}\n\n"
            f"We’re sorry, but the position you applied for ({t}) "
            "is no longer accepting applications.\n"
            "Thank you for your interest."
        )
    elif status == IntakeStatus.MULTIPLE_ATTACHMENTS:
        lines = [
            f"{greeting}\n\n"
            f"Thank you for applying for: {t}.\n\n"
            "Here is the status for each file you attached:\n"
        ]
        for r in results:
            fname = r.filename or "(unnamed)"
            lines.append(f"  • {fname}: {_attachment_label(r.status)}")
        lines.append(
            "\nIf any file was rejected, please resubmit it as a PDF or Word document."
        )
        body = "\n".join(lines)
    else:
        body = f"{greeting}\n\nThank you for contacting us."

    text_body = body + sig
    html_body = _html_wrap(subject, text_body, from_name)
    return subject, text_body, html_body


def build_recruiter_unmatched_email(
    count: int,
    threshold: int,
    from_name: str,
) -> tuple[str, str, str]:
    subject = f"Alert: {count} unidentified CV email(s) received – action may be needed"
    text_body = (
        f"Dear Recruiter,\n\n"
        f"{count} email(s) addressed to your CV intake inbox could not be matched to any "
        f"active job position (threshold: {threshold}).\n\n"
        "Common causes:\n"
        "  • Applicants did not include a job reference code in the email subject\n"
        "  • An incorrect job code was used\n"
        "  • The job is no longer active\n\n"
        "Please review your intake configuration and ensure applicants are aware of "
        "the required job code format (e.g. JOB-2024-0001).\n\n"
        f"The {from_name} Platform"
    )
    html_body = _html_wrap(subject, text_body, from_name)
    return subject, text_body, html_body


def build_recruiter_inactive_email(
    job_title: str,
    recipient_name: str,
    from_name: str,
) -> tuple[str, str, str]:
    subject = f"Alert: CV received for inactive position – {job_title}"
    name_line = f"Dear {recipient_name}," if recipient_name else "Dear Recruiter,"
    text_body = (
        f"{name_line}\n\n"
        f"An email was received for the position “{job_title}” which is "
        "no longer accepting applications.\n\n"
        "If this position should be reopened, please update its status in the system.\n\n"
        f"The {from_name} Platform"
    )
    html_body = _html_wrap(subject, text_body, from_name)
    return subject, text_body, html_body


def _html_wrap(subject: str, text_body: str, from_name: str) -> str:
    """Convert newline-delimited plain text to minimal HTML email."""
    paras = "".join(
        f"<p style='margin:0 0 8px'>{_esc(line)}</p>" if line.strip() else "<br>"
        for line in text_body.split("\n")
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
        "<body style='font-family:Arial,sans-serif;font-size:14px;color:#333;"
        "max-width:600px;margin:0 auto;padding:20px'>"
        f"<div style='border-bottom:3px solid #4F46E5;padding-bottom:12px;margin-bottom:20px'>"
        f"<strong style='color:#4F46E5'>{_esc(from_name)}</strong></div>"
        f"{paras}"
        "<div style='border-top:1px solid #eee;margin-top:24px;padding-top:12px;"
        "font-size:12px;color:#999'>This is an automated message.</div>"
        "</body></html>"
    )


# ── Internal DB helpers ────────────────────────────────────────────────────────

async def _insert_notification(
    db,
    *,
    notification_type: str,
    intake_status: IntakeStatus,
    recipient_email: str,
    recipient_type: str,
    tenant_id: str | None,
    job_id: str | None,
    application_id: str | None,
    intake_log_id: str | None,
    duplicate_log_id: str | None,
    details: dict | None,
) -> str | None:
    """
    Insert a pending notification_log row.
    Returns the notification_id, or None if a duplicate row already exists
    (idempotency enforced by unique index on candidate notifications).
    Caller is responsible for committing the session.
    """
    from sqlalchemy import text

    notification_id = str(uuid.uuid4())
    try:
        result = await db.execute(
            text("""
                INSERT INTO notification_log
                    (notification_id, notification_type, intake_status,
                     recipient_email, recipient_type,
                     tenant_id, job_id, application_id,
                     intake_log_id, duplicate_log_id,
                     details, status)
                VALUES
                    (:nid, :ntype, :istatus,
                     :email, :rtype,
                     :tid, :jid, :aid,
                     :lid, :dlid,
                     :details::jsonb, 'pending')
                ON CONFLICT (intake_log_id, intake_status, recipient_email)
                    WHERE intake_log_id IS NOT NULL AND recipient_type = 'candidate'
                DO NOTHING
                RETURNING notification_id
            """),
            {
                "nid":     notification_id,
                "ntype":   notification_type,
                "istatus": intake_status.value,
                "email":   recipient_email.lower(),
                "rtype":   recipient_type,
                "tid":     tenant_id,
                "jid":     job_id,
                "aid":     application_id,
                "lid":     intake_log_id,
                "dlid":    duplicate_log_id,
                "details": json.dumps(details) if details is not None else None,
            },
        )
        row = result.fetchone()
        if row is None:
            logger.debug(
                "Notification skipped (duplicate) — intake_log=%s status=%s email=%s",
                intake_log_id, intake_status.value, recipient_email,
            )
            return None
        return str(row[0])
    except Exception as exc:
        logger.error("Failed to insert notification_log: %s", exc, exc_info=True)
        return None


async def _get_job_title(db, job_id: str | None) -> str:
    if not job_id:
        return "the advertised position"
    from sqlalchemy import text
    try:
        row = await db.execute(
            text("SELECT title FROM jobs WHERE job_id = :jid"),
            {"jid": job_id},
        )
        return row.scalar_one_or_none() or "the advertised position"
    except Exception:
        return "the advertised position"


async def _check_send_toggle(db, job_id: str) -> bool:
    """Return True if send_intake_notifications is enabled for this job (default true)."""
    from sqlalchemy import text
    try:
        row = await db.execute(
            text("SELECT send_intake_notifications FROM jobs WHERE job_id = :jid"),
            {"jid": job_id},
        )
        val = row.scalar_one_or_none()
        return val if val is not None else True
    except Exception:
        return True


def _enqueue(notification_id: str) -> None:
    """Enqueue the notification task. Import is deferred to avoid circular imports."""
    from workers.notification_worker import send_notification_task
    send_notification_task.delay(notification_id)


# ── Public API ─────────────────────────────────────────────────────────────────

async def queue_candidate_notification(
    db,
    *,
    sender_email: str,
    sender_name: str,
    tenant_id: str | None,
    job_id: str | None,
    ingestion_mode: str,
    attachment_results: list[AttachmentResult],
    intake_log_id: str | None,
    job_title: str | None = None,
) -> None:
    """
    Queue ONE consolidated candidate email for all attachment outcomes in
    a single inbound message.

    Rules:
    - Skips silently for 'unknown' ingestion mode with no job_id (spam/internal).
    - Respects per-job send_intake_notifications toggle (default true).
    - If all attachments succeeded → RECEIVED_SUCCESSFULLY.
    - If all attachments had the same error → that error status.
    - Otherwise → MULTIPLE_ATTACHMENTS (shows per-file table).
    """
    if not sender_email:
        return

    # Don't email unrecognised/internal sources
    if ingestion_mode == "unknown" and job_id is None:
        return

    if not attachment_results:
        return

    # Per-job toggle check
    if job_id and not await _check_send_toggle(db, job_id):
        return

    resolved_title = job_title or await _get_job_title(db, job_id)

    # Derive consolidated status
    statuses = {r.status for r in attachment_results}
    if len(attachment_results) == 1 or len(statuses) == 1:
        final_status = attachment_results[0].status
    else:
        final_status = IntakeStatus.MULTIPLE_ATTACHMENTS

    # Best single reference IDs
    application_id   = next((r.application_id   for r in attachment_results if r.application_id),   None)
    duplicate_log_id = next((r.duplicate_log_id  for r in attachment_results if r.duplicate_log_id), None)

    details = {
        "sender_name":    sender_name,
        "job_title":      resolved_title,
        "ingestion_mode": ingestion_mode,
        "attachments": [
            {
                "filename":         r.filename,
                "status":           r.status.value,
                "application_id":   r.application_id,
                "duplicate_log_id": r.duplicate_log_id,
                "error_detail":     r.error_detail,
            }
            for r in attachment_results
        ],
    }

    notification_id = await _insert_notification(
        db,
        notification_type="CANDIDATE_INTAKE",
        intake_status=final_status,
        recipient_email=sender_email,
        recipient_type="candidate",
        tenant_id=tenant_id,
        job_id=job_id,
        application_id=application_id,
        intake_log_id=intake_log_id,
        duplicate_log_id=duplicate_log_id,
        details=details,
    )

    if notification_id:
        _enqueue(notification_id)
        logger.info(
            "Candidate notification queued: id=%s status=%s to=%s",
            notification_id, final_status.value, sender_email,
        )

    await db.commit()


async def queue_recruiter_unmatched_alert(
    db,
    *,
    intake_log_id: str | None = None,
) -> None:
    """
    Check the count of unidentified inbound emails against the configured
    threshold. If reached, queue a recruiter alert to the address in
    system_config.unmatched_alert_email.

    Threshold logic:
    - Count email_ingest_log rows with status='unassigned' since the last
      RECRUITER_UNMATCHED_ALERT that was successfully sent.
    - If the last alert is recent (< 1 h), suppress to avoid alert storms.
    """
    from sqlalchemy import text

    # Load system_config keys
    try:
        cfg_rows = await db.execute(
            text("""
                SELECT key, value FROM system_config
                WHERE key IN ('unmatched_alert_threshold', 'unmatched_alert_email')
            """)
        )
        cfg = {row["key"]: row["value"] for row in cfg_rows.mappings()}
    except Exception as exc:
        logger.warning("Could not load system_config for unmatched alert: %s", exc)
        return

    alert_email = (cfg.get("unmatched_alert_email") or "").strip()
    if not alert_email:
        return  # alerts disabled

    try:
        threshold = int(cfg.get("unmatched_alert_threshold", "50") or "50")
    except (ValueError, TypeError):
        threshold = 50

    # Timestamp of last successfully sent alert
    last_row = await db.execute(
        text("""
            SELECT MAX(sent_at)
            FROM notification_log
            WHERE notification_type = 'RECRUITER_UNMATCHED_ALERT'
              AND status = 'sent'
        """)
    )
    last_sent_at = last_row.scalar_one_or_none()

    # Count unassigned emails since last alert (or all-time if none sent yet)
    if last_sent_at:
        count_row = await db.execute(
            text("""
                SELECT COUNT(*) FROM email_ingest_log
                WHERE status = 'unassigned' AND received_at > :since
            """),
            {"since": last_sent_at},
        )
    else:
        count_row = await db.execute(
            text("SELECT COUNT(*) FROM email_ingest_log WHERE status = 'unassigned'")
        )
    count = count_row.scalar_one() or 0

    if count < threshold:
        return

    # Suppress if a pending/recent alert exists (avoid duplicates during a storm)
    recent = await db.execute(
        text("""
            SELECT 1 FROM notification_log
            WHERE notification_type = 'RECRUITER_UNMATCHED_ALERT'
              AND status IN ('pending', 'sent')
              AND created_at > NOW() - INTERVAL '1 hour'
            LIMIT 1
        """)
    )
    if recent.first():
        logger.debug("Unmatched recruiter alert suppressed — recent alert exists")
        return

    details = {"unmatched_count": count, "threshold": threshold}
    notification_id = await _insert_notification(
        db,
        notification_type="RECRUITER_UNMATCHED_ALERT",
        intake_status=IntakeStatus.JOB_NOT_IDENTIFIED,
        recipient_email=alert_email,
        recipient_type="recruiter",
        tenant_id=None,
        job_id=None,
        application_id=None,
        intake_log_id=intake_log_id,
        duplicate_log_id=None,
        details=details,
    )
    if notification_id:
        _enqueue(notification_id)
        logger.info(
            "Recruiter unmatched alert queued: id=%s count=%d threshold=%d to=%s",
            notification_id, count, threshold, alert_email,
        )
    await db.commit()


async def queue_recruiter_inactive_alert(
    db,
    *,
    job_id: str,
    intake_log_id: str | None = None,
) -> None:
    """
    Send a recruiter alert when an email arrives for an inactive/disabled job,
    but only if notify_recruiter_on_inactive is enabled for that job.
    Falls back to the job creator's email if recruiter_alert_email is null.
    Suppressed if an alert for this job was sent within the last hour.
    """
    from sqlalchemy import text

    try:
        job_row = await db.execute(
            text("""
                SELECT j.tenant_id, j.title,
                       j.notify_recruiter_on_inactive,
                       j.recruiter_alert_email,
                       u.email      AS creator_email,
                       u.full_name  AS creator_name
                FROM jobs  j
                JOIN users u ON u.user_id = j.created_by
                WHERE j.job_id = :jid
            """),
            {"jid": job_id},
        )
        job = job_row.mappings().first()
    except Exception as exc:
        logger.warning("Could not load job for inactive alert job_id=%s: %s", job_id, exc)
        return

    if not job or not job["notify_recruiter_on_inactive"]:
        return

    alert_email = (job["recruiter_alert_email"] or job["creator_email"] or "").strip()
    if not alert_email:
        return

    # Suppress duplicate alerts within 1-hour window
    recent = await db.execute(
        text("""
            SELECT 1 FROM notification_log
            WHERE notification_type = 'RECRUITER_INACTIVE_ALERT'
              AND job_id = :jid
              AND status IN ('pending', 'sent')
              AND created_at > NOW() - INTERVAL '1 hour'
            LIMIT 1
        """),
        {"jid": job_id},
    )
    if recent.first():
        return

    details = {
        "job_title":      job["title"],
        "recipient_name": job["creator_name"],
    }
    notification_id = await _insert_notification(
        db,
        notification_type="RECRUITER_INACTIVE_ALERT",
        intake_status=IntakeStatus.JOB_INACTIVE,
        recipient_email=alert_email,
        recipient_type="recruiter",
        tenant_id=str(job["tenant_id"]),
        job_id=job_id,
        application_id=None,
        intake_log_id=intake_log_id,
        duplicate_log_id=None,
        details=details,
    )
    if notification_id:
        _enqueue(notification_id)
        logger.info(
            "Recruiter inactive alert queued: id=%s job=%s to=%s",
            notification_id, job_id, alert_email,
        )
    await db.commit()
