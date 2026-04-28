"""Email notification service — sends SMTP alerts on job completion/failure.

Uses stdlib smtplib (no extra dependencies) wrapped in asyncio.to_thread
so the event loop is never blocked.

Configure via environment variables:
    SMTP_HOST              (default: smtp.gmail.com)
    SMTP_PORT              (default: 587)
    SMTP_USER              sender address / login
    SMTP_PASSWORD          app password (Gmail) or API key (SendGrid)
    NOTIFY_EMAILS          comma-separated recipients (e.g. a@x.com,b@x.com)
    EMAIL_NOTIFICATIONS_ENABLED  (default: false)
"""
import asyncio
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _send_smtp(subject: str, body_html: str) -> None:
    """Synchronous SMTP send — runs in a thread via asyncio.to_thread."""
    recipients = [e.strip() for e in settings.notify_emails.split(",") if e.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        server.ehlo()
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_user, recipients, msg.as_string())


async def send_job_notification(
    *,
    site_key: str,
    job_id: str,
    status: str,
    progress: dict | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    start_url: str | None = None,
    warnings_count: int = 0,
) -> None:
    """Fire-and-forget email after a job finishes.

    Does nothing if EMAIL_NOTIFICATIONS_ENABLED is False or credentials are missing.
    Exceptions are caught and logged — a failed email must never crash the job.
    """
    if not settings.email_notifications_enabled:
        return
    if not settings.smtp_user or not settings.smtp_password or not settings.notify_emails:
        logger.warning("Email notifications enabled but SMTP credentials are missing — skipping")
        return

    progress = progress or {}
    scraped = progress.get("listings_scraped", 0)
    found = progress.get("listings_found", 0)
    errors = progress.get("errors", 0)
    pages = progress.get("pages_visited", 0)
    new_count = progress.get("new_listings", 0)
    updated_count = progress.get("updated_listings", 0)

    # Derived values
    success_rate = f"{int(scraped / found * 100)}% ({scraped}/{found})" if found else "—"
    finished_str = finished_at.strftime("%d %b %Y, %H:%M UTC") if finished_at else "—"
    duration = "—"
    if started_at and finished_at:
        total_seconds = int((finished_at - started_at).total_seconds())
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        duration = f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")

    is_success = status == "completed"
    status_label = "Completed" if is_success else "Failed"
    subject = f"[Pearls] {site_key} — {status_label}"

    # Colour tokens — neutral palette, one accent per state
    accent = "#18181b" if is_success else "#dc2626"      # zinc-900 | red-600
    accent_light = "#f4f4f5" if is_success else "#fef2f2" # zinc-100 | red-50
    accent_text = "#3f3f46" if is_success else "#991b1b"  # zinc-700 | red-800
    status_dot = (
        '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
        f'background:{accent};margin-right:6px;vertical-align:middle;"></span>'
    )

    # Error block
    error_block = ""
    if error_message:
        error_block = f"""
        <tr>
          <td style="padding:0 32px 24px;">
            <div style="background:#fafafa;border:1px solid #e4e4e7;border-left:3px solid #dc2626;
                        padding:14px 16px;border-radius:4px;font-family:monospace;font-size:12px;
                        color:#3f3f46;line-height:1.6;word-break:break-word;">
              {error_message}
            </div>
          </td>
        </tr>"""

    # CTA button
    dashboard_button = ""
    if settings.frontend_url:
        _url = f"{settings.frontend_url}/jobs/{job_id}"
        dashboard_button = f"""
        <tr>
          <td style="padding:0 32px 32px;">
            <a href="{_url}"
               style="display:inline-block;background:#18181b;color:#ffffff;font-size:12px;
                      font-weight:600;letter-spacing:0.04em;text-transform:uppercase;
                      padding:10px 20px;border-radius:4px;text-decoration:none;">
              View job details
            </a>
          </td>
        </tr>"""

    # Stats rows helper
    def stat_row(label: str, value: str, value_color: str = "#18181b") -> str:
        return (
            f'<tr>'
            f'<td style="padding:10px 0;font-size:13px;color:#71717a;border-bottom:1px solid #f4f4f5;">{label}</td>'
            f'<td style="padding:10px 0;font-size:13px;font-weight:500;color:{value_color};'
            f'text-align:right;border-bottom:1px solid #f4f4f5;">{value}</td>'
            f'</tr>'
        )

    errors_color = "#dc2626" if errors else "#18181b"
    warnings_color = "#d97706" if warnings_count else "#18181b"

    body_html = f"""<!DOCTYPE html>
<html lang="pt">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#fafafa;
             font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;">

  <table width="100%" cellpadding="0" cellspacing="0" style="background:#fafafa;padding:40px 16px;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border:1px solid #e4e4e7;border-radius:6px;overflow:hidden;">

        <!-- Top accent bar -->
        <tr>
          <td style="background:{accent};height:3px;font-size:0;line-height:0;">&nbsp;</td>
        </tr>

        <!-- Header -->
        <tr>
          <td style="padding:28px 32px 20px;">
            <p style="margin:0 0 4px;font-size:11px;font-weight:600;letter-spacing:0.08em;
                      text-transform:uppercase;color:#a1a1aa;">Pearls of Portugal</p>
            <p style="margin:0;font-size:22px;font-weight:600;color:#18181b;letter-spacing:-0.02em;">
              Scrape Report
            </p>
          </td>
        </tr>

        <!-- Status pill + site -->
        <tr>
          <td style="padding:0 32px 20px;">
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="background:{accent_light};border-radius:4px;padding:5px 10px;">
                  {status_dot}
                  <span style="font-size:12px;font-weight:600;color:{accent_text};
                               letter-spacing:0.02em;">{status_label.upper()}</span>
                </td>
                <td style="padding-left:10px;font-size:13px;color:#71717a;">
                  <span style="color:#a1a1aa;">site /</span>
                  <strong style="color:#3f3f46;">{site_key}</strong>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Divider -->
        <tr>
          <td style="padding:0 32px;">
            <div style="height:1px;background:#f4f4f5;"></div>
          </td>
        </tr>

        <!-- Stats table -->
        <tr>
          <td style="padding:8px 32px 24px;">
            <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
              {stat_row("Finished", finished_str)}
              {stat_row("Duration", duration)}
              {stat_row("Pages visited", str(pages))}
              {stat_row("Success rate", success_rate)}
              {stat_row("New listings", str(new_count))}
              {stat_row("Updated", str(updated_count))}
              {stat_row("Warnings", str(warnings_count), warnings_color)}
              <tr>
                <td style="padding:10px 0;font-size:13px;color:#71717a;">Errors</td>
                <td style="padding:10px 0;font-size:13px;font-weight:500;color:{errors_color};text-align:right;">
                  {errors}
                </td>
              </tr>
            </table>
          </td>
        </tr>

        {error_block}
        {dashboard_button}

        <!-- Footer -->
        <tr>
          <td style="padding:16px 32px;background:#fafafa;border-top:1px solid #f4f4f5;">
            <p style="margin:0;font-size:11px;color:#a1a1aa;line-height:1.6;">
              Job ID: <span style="font-family:monospace;color:#71717a;">{job_id}</span>
              {"<br>URL: " + start_url if start_url else ""}
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>

</body>
</html>"""

    try:
        await asyncio.to_thread(_send_smtp, subject, body_html)
        logger.info("Job notification email sent for %s (%s)", site_key, status)
    except Exception as exc:
        logger.warning("Failed to send job notification email: %s", exc)