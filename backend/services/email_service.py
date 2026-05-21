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


async def send_welcome_email(to_email: str, admin_name: str, company_name: str, login_url: str) -> None:
    subject = f"Your CV Analyzer workspace is ready — {company_name}"
    text_body = (
        f"Hi {admin_name},\n\n"
        f"Your {company_name} workspace has been created on CV Analyzer.\n\n"
        f"To activate your workspace, log in and select a subscription plan.\n\n"
        f"Once activated, your team will have access to:\n"
        f"  • AI-powered CV screening and scoring\n"
        f"  • Bilingual recruitment support (English / Arabic)\n"
        f"  • Team collaboration and role-based access\n"
        f"  • Automated CV ingestion via email\n\n"
        f"Get started:\n{login_url}\n\n"
        "If you have any questions, reply to this email.\n\n"
        "The CV Analyzer Team"
    )
    html_body = f"""
    <html><body style="font-family:sans-serif;color:#1e293b;max-width:560px;margin:0 auto;">
    <div style="background:linear-gradient(135deg,#6366f1,#2563eb);padding:32px 24px;border-radius:12px 12px 0 0;">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:800;">Your workspace is ready</h1>
      <p style="color:#c7d2fe;margin:8px 0 0;font-size:14px;">{company_name} · CV Analyzer</p>
    </div>
    <div style="background:#fff;padding:32px 24px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;">
      <p style="margin:0 0 16px;">Hi <strong>{admin_name}</strong>,</p>
      <p style="margin:0 0 20px;">Your <strong>{company_name}</strong> workspace has been successfully created on CV Analyzer.</p>
      <p style="margin:0 0 12px;font-weight:600;color:#1e293b;">Next step: select a subscription plan to activate your workspace.</p>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin:0 0 24px;">
        <p style="margin:0 0 6px;font-size:13px;font-weight:600;color:#475569;">Once activated, your team gains access to:</p>
        <p style="margin:0;font-size:13px;color:#64748b;line-height:1.7;">
          ✦ AI-powered CV screening and automated scoring<br/>
          ✦ Bilingual platform — English and Arabic<br/>
          ✦ Team collaboration with role-based access control<br/>
          ✦ Automated CV ingestion via email forwarding
        </p>
      </div>
      <p style="text-align:center;margin:0 0 24px;">
        <a href="{login_url}" style="background:linear-gradient(135deg,#6366f1,#2563eb);color:#fff;padding:14px 32px;text-decoration:none;border-radius:8px;font-weight:700;display:inline-block;">
          Activate Your Workspace →
        </a>
      </p>
      <p style="margin:0;font-size:12px;color:#94a3b8;">If you did not create this account, you can safely ignore this email.</p>
    </div>
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


async def send_registration_welcome_email(
    to_email: str, admin_name: str, company_name: str, login_url: str
) -> None:
    """Sent immediately after a tenant self-registers (pending_plan_selection state)."""
    subject = "Welcome to CV Analyzer — your account is ready"
    text_body = (
        f"Hi {admin_name},\n\n"
        f"Your CV Analyzer account for {company_name} has been created.\n\n"
        "To start using the platform, log in and select a subscription plan to activate your workspace.\n\n"
        f"Log in here:\n{login_url}\n\n"
        "If you did not create this account, please ignore this email.\n\n"
        "The CV Analyzer Team"
    )
    html_body = f"""
    <html><body style="font-family:sans-serif;color:#1e293b;max-width:560px;margin:0 auto;">
    <div style="background:linear-gradient(135deg,#6366f1,#2563eb);padding:32px 24px;border-radius:12px 12px 0 0;">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:800;">Account created</h1>
      <p style="color:#c7d2fe;margin:8px 0 0;font-size:14px;">{company_name} · CV Analyzer</p>
    </div>
    <div style="background:#fff;padding:32px 24px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;">
      <p style="margin:0 0 16px;">Hi <strong>{admin_name}</strong>,</p>
      <p style="margin:0 0 20px;">Your CV Analyzer account for <strong>{company_name}</strong> has been created successfully.</p>
      <p style="margin:0 0 20px;">To activate your workspace and start screening CVs, log in and select a subscription plan.</p>
      <p style="text-align:center;margin:0 0 24px;">
        <a href="{login_url}" style="background:linear-gradient(135deg,#6366f1,#2563eb);color:#fff;padding:14px 32px;text-decoration:none;border-radius:8px;font-weight:700;display:inline-block;">
          Log In &amp; Select Plan &#x2192;
        </a>
      </p>
      <p style="margin:0;font-size:12px;color:#94a3b8;">If you did not create this account, you can safely ignore this email.</p>
    </div>
    </body></html>
    """
    await _send(to_email, subject, html_body, text_body)


async def send_tenant_admin_created_email(
    to_email: str, admin_name: str, company_name: str, login_url: str
) -> None:
    """Sent when a Super Admin creates a new tenant and admin user."""
    subject = f"Your CV Analyzer account — {company_name}"
    text_body = (
        f"Hi {admin_name},\n\n"
        f"A CV Analyzer account has been created for you as the administrator of {company_name}.\n\n"
        "Your account was set up by the platform team.\n\n"
        "For security, please change your password after your first login.\n\n"
        f"Log in here:\n{login_url}\n\n"
        "The CV Analyzer Team"
    )
    html_body = f"""
    <html><body style="font-family:sans-serif;color:#1e293b;max-width:560px;margin:0 auto;">
    <div style="background:linear-gradient(135deg,#0f172a,#1e3a5f);padding:32px 24px;border-radius:12px 12px 0 0;">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:800;">Account created for you</h1>
      <p style="color:#94a3b8;margin:8px 0 0;font-size:14px;">{company_name} · CV Analyzer</p>
    </div>
    <div style="background:#fff;padding:32px 24px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;">
      <p style="margin:0 0 16px;">Hi <strong>{admin_name}</strong>,</p>
      <p style="margin:0 0 16px;">
        A CV Analyzer administrator account has been created for you for <strong>{company_name}</strong>.
        Your account was provisioned by the platform team.
      </p>
      <div style="background:#fef9c3;border:1px solid #fde047;border-radius:8px;padding:12px 16px;margin:0 0 24px;font-size:13px;color:#713f12;">
        <strong>Security reminder:</strong> Please change your temporary password after your first login.
      </div>
      <p style="text-align:center;margin:0 0 24px;">
        <a href="{login_url}" style="background:#1e3a5f;color:#fff;padding:14px 32px;text-decoration:none;border-radius:8px;font-weight:700;display:inline-block;">
          Log In to CV Analyzer &#x2192;
        </a>
      </p>
      <p style="margin:0;font-size:12px;color:#94a3b8;">If you were not expecting this email, please contact support.</p>
    </div>
    </body></html>
    """
    await _send(to_email, subject, html_body, text_body)


async def send_email_verification_email(
    to_email: str, name: str, company_name: str, verify_link: str
) -> None:
    """Sent after public tenant self-registration — user must click to activate account."""
    subject = "Verify your email — CV Analyzer"
    text_body = (
        f"Hi {name},\n\n"
        f"Thanks for registering {company_name} on CV Analyzer.\n\n"
        "Please verify your email address to activate your account by clicking the link below:\n\n"
        f"{verify_link}\n\n"
        "This link expires in 48 hours.\n\n"
        "If your link expires, you can request a new verification email from the login page.\n\n"
        "If you did not create this account, you can safely ignore this email.\n\n"
        "The CV Analyzer Team"
    )
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f1f5f9;padding:32px 16px;">
  <tr><td align="center">
    <table width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%;">

      <!-- Header -->
      <tr>
        <td style="background-color:#2563eb;padding:32px 28px;border-radius:8px 8px 0 0;">
          <p style="margin:0;font-size:22px;font-weight:700;color:#ffffff;">Verify your email address</p>
          <p style="margin:8px 0 0;font-size:14px;color:#bfdbfe;">{company_name} &middot; CV Analyzer</p>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="background-color:#ffffff;padding:32px 28px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 8px 8px;">

          <p style="margin:0 0 16px;font-size:15px;color:#1e293b;">Hi <strong>{name}</strong>,</p>

          <p style="margin:0 0 12px;font-size:15px;color:#1e293b;">
            Thanks for registering <strong>{company_name}</strong> on CV Analyzer.
          </p>

          <p style="margin:0 0 28px;font-size:15px;color:#1e293b;">
            Please verify your email address to activate your account. Click the button below to get started.
          </p>

          <!-- CTA button (table-based for email client compatibility) -->
          <table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto 28px;">
            <tr>
              <td align="center" style="background-color:#2563eb;border-radius:6px;">
                <a href="{verify_link}"
                   style="display:inline-block;padding:14px 32px;font-size:16px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:6px;letter-spacing:0.3px;">
                  Verify Email
                </a>
              </td>
            </tr>
          </table>

          <!-- Fallback URL -->
          <p style="margin:0 0 6px;font-size:13px;color:#475569;">
            If the button does not work, copy and paste the following link into your browser:
          </p>
          <p style="margin:0 0 28px;font-size:12px;color:#1e293b;word-break:break-all;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:4px;padding:10px 12px;">
            {verify_link}
          </p>

          <!-- Expiry + resend -->
          <p style="margin:0 0 8px;font-size:13px;color:#64748b;">
            &#x23F1; This verification link expires in <strong>48 hours</strong>.
          </p>
          <p style="margin:0 0 24px;font-size:13px;color:#64748b;">
            If your link expires, you can request a new verification email from the login page.
          </p>

          <p style="margin:0;font-size:12px;color:#94a3b8;">
            If you did not create this account, you can safely ignore this email.
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""
    await _send(to_email, subject, html_body, text_body)


async def send_activation_email(
    to_email: str, name: str, company_name: str, verify_link: str, role: str | None = None
) -> None:
    """Sent when a Super Admin or tenant admin creates a user account on their behalf."""
    role_display = role.replace("_", " ").title() if role else "Administrator"
    subject = f"Activate your CV Analyzer account — {company_name}"
    text_body = (
        f"Hi {name},\n\n"
        f"An account has been created for you on {company_name}'s CV Analyzer workspace.\n\n"
        f"Your role: {role_display}\n\n"
        "Click the link below to verify your email and activate your account:\n\n"
        f"{verify_link}\n\n"
        "This link expires in 48 hours. After activation you will be prompted to set your password.\n\n"
        "If your link expires, you can request a new verification email from the login page.\n\n"
        "If you were not expecting this email, please contact your account administrator.\n\n"
        "The CV Analyzer Team"
    )
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f1f5f9;padding:32px 16px;">
  <tr><td align="center">
    <table width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%;">

      <!-- Header -->
      <tr>
        <td style="background-color:#2563eb;padding:32px 28px;border-radius:8px 8px 0 0;">
          <p style="margin:0;font-size:22px;font-weight:700;color:#ffffff;">Activate your account</p>
          <p style="margin:8px 0 0;font-size:14px;color:#bfdbfe;">{company_name} &middot; CV Analyzer</p>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="background-color:#ffffff;padding:32px 28px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 8px 8px;">

          <p style="margin:0 0 16px;font-size:15px;color:#1e293b;">Hi <strong>{name}</strong>,</p>

          <p style="margin:0 0 12px;font-size:15px;color:#1e293b;">
            An account has been created for you on <strong>{company_name}</strong>'s CV Analyzer workspace
            with the role of <strong>{role_display}</strong>.
          </p>

          <p style="margin:0 0 28px;font-size:15px;color:#1e293b;">
            Click the button below to verify your email and set your password to get started.
          </p>

          <!-- CTA button (table-based for email client compatibility) -->
          <table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto 28px;">
            <tr>
              <td align="center" style="background-color:#2563eb;border-radius:6px;">
                <a href="{verify_link}"
                   style="display:inline-block;padding:14px 32px;font-size:16px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:6px;letter-spacing:0.3px;">
                  Activate Account
                </a>
              </td>
            </tr>
          </table>

          <!-- Fallback URL -->
          <p style="margin:0 0 6px;font-size:13px;color:#475569;">
            If the button does not work, copy and paste the following link into your browser:
          </p>
          <p style="margin:0 0 28px;font-size:12px;color:#1e293b;word-break:break-all;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:4px;padding:10px 12px;">
            {verify_link}
          </p>

          <!-- Expiry + resend -->
          <p style="margin:0 0 8px;font-size:13px;color:#64748b;">
            &#x23F1; This activation link expires in <strong>48 hours</strong>.
          </p>
          <p style="margin:0 0 24px;font-size:13px;color:#64748b;">
            If your link expires, you can request a new verification email from the login page.
          </p>

          <p style="margin:0;font-size:12px;color:#94a3b8;">
            If you were not expecting this email, please contact your account administrator.
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""
    await _send(to_email, subject, html_body, text_body)


async def send_user_created_welcome_email(
    to_email: str, user_name: str, company_name: str, role: str, login_url: str
) -> None:
    """Sent when a tenant admin creates a new team member."""
    role_display = role.replace("_", " ").title()
    subject = f"You've been added to {company_name} on CV Analyzer"
    text_body = (
        f"Hi {user_name},\n\n"
        f"You have been added as a team member on {company_name}'s CV Analyzer workspace.\n\n"
        f"Your role: {role_display}\n\n"
        "For security, please change your password after your first login.\n\n"
        f"Log in here:\n{login_url}\n\n"
        "The CV Analyzer Team"
    )
    html_body = f"""
    <html><body style="font-family:sans-serif;color:#1e293b;max-width:560px;margin:0 auto;">
    <div style="background:linear-gradient(135deg,#059669,#0284c7);padding:32px 24px;border-radius:12px 12px 0 0;">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:800;">You've been added to a team</h1>
      <p style="color:#a7f3d0;margin:8px 0 0;font-size:14px;">{company_name} · CV Analyzer</p>
    </div>
    <div style="background:#fff;padding:32px 24px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;">
      <p style="margin:0 0 16px;">Hi <strong>{user_name}</strong>,</p>
      <p style="margin:0 0 16px;">
        You have been added to <strong>{company_name}</strong>'s workspace on CV Analyzer
        with the role of <strong>{role_display}</strong>.
      </p>
      <div style="background:#fef9c3;border:1px solid #fde047;border-radius:8px;padding:12px 16px;margin:0 0 24px;font-size:13px;color:#713f12;">
        <strong>Security reminder:</strong> Please change your temporary password after your first login.
      </div>
      <p style="text-align:center;margin:0 0 24px;">
        <a href="{login_url}" style="background:linear-gradient(135deg,#059669,#0284c7);color:#fff;padding:14px 32px;text-decoration:none;border-radius:8px;font-weight:700;display:inline-block;">
          Log In to CV Analyzer &#x2192;
        </a>
      </p>
      <p style="margin:0;font-size:12px;color:#94a3b8;">If you were not expecting this email, please contact your account administrator.</p>
    </div>
    </body></html>
    """
    await _send(to_email, subject, html_body, text_body)
