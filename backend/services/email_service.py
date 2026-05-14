"""Transactional email sending via SMTP (aiosmtplib)."""
import logging

import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import get_settings
from services.runtime_config import get_bool_secret, get_int_secret, get_secret

settings = get_settings()
logger = logging.getLogger(__name__)


async def _send(to_email: str, subject: str, html_body: str, text_body: str) -> None:
    smtp_host     = get_secret("SMTP_HOST",     settings.smtp_host)
    smtp_port     = get_int_secret("SMTP_PORT", settings.smtp_port)
    smtp_user     = get_secret("SMTP_USER",     settings.smtp_user)
    smtp_password = get_secret("SMTP_PASSWORD", settings.smtp_password)
    smtp_use_tls  = get_bool_secret("SMTP_USE_TLS", settings.smtp_use_tls)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_password,
            use_tls=smtp_use_tls,
        )
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to_email, exc)
        raise


async def send_password_reset_email(to_email: str, to_name: str, reset_link: str) -> None:
    subject = "Reset your CV Analyzer password"
    text_body = (
        f"Hi {to_name},\n\n"
        f"Click the link below to reset your password (expires in 15 minutes):\n{reset_link}\n\n"
        "If you did not request this, ignore this email."
    )
    html_body = f"""
    <html><body>
    <p>Hi {to_name},</p>
    <p>Click the button below to reset your password. This link expires in <strong>15 minutes</strong>.</p>
    <p><a href="{reset_link}" style="background:#4F46E5;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;">Reset Password</a></p>
    <p>If you did not request this, you can safely ignore this email.</p>
    </body></html>
    """
    await _send(to_email, subject, html_body, text_body)


async def send_cv_received_email(to_email: str, candidate_name: str, job_title: str) -> None:
    subject = f"CV received — {job_title}"
    text_body = (
        f"Dear {candidate_name},\n\n"
        f"We have received your CV for the position: {job_title}.\n"
        "Our team will review your application and be in touch.\n\n"
        "Thank you for your interest."
    )
    html_body = f"""
    <html><body>
    <p>Dear {candidate_name},</p>
    <p>We have received your CV for the position: <strong>{job_title}</strong>.</p>
    <p>Our team will review your application and be in touch.</p>
    <p>Thank you for your interest.</p>
    </body></html>
    """
    await _send(to_email, subject, html_body, text_body)


async def send_invite_email(to_email: str, inviter_name: str, tenant_name: str, invite_link: str) -> None:
    subject = f"You've been invited to {tenant_name} on CV Analyzer"
    text_body = (
        f"Hi,\n\n"
        f"{inviter_name} has invited you to join {tenant_name} on CV Analyzer.\n"
        f"Accept your invitation here (expires in 48 hours):\n{invite_link}\n\n"
    )
    html_body = f"""
    <html><body>
    <p>Hi,</p>
    <p><strong>{inviter_name}</strong> has invited you to join <strong>{tenant_name}</strong> on CV Analyzer.</p>
    <p><a href="{invite_link}" style="background:#4F46E5;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;">Accept Invitation</a></p>
    <p>This link expires in 48 hours.</p>
    </body></html>
    """
    await _send(to_email, subject, html_body, text_body)
