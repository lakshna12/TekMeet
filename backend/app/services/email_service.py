"""Email Service for Phase 5 Automatic Delivery.

Supports Microsoft Graph Mail API (primary), SMTP (secondary), and Mock (testing).
Renders rich HTML summary emails, validates recipients, tracks retries, and prevents secret exposure.
"""

import logging
import re
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional, Tuple

import httpx

from app.auth.entra_auth import entra_auth_service
from app.core.config import Settings, settings
from app.models.delivery import DeliveryProvider, DeliveryRecord, DeliveryStatus, EmailPayload
from app.models.summary import MeetingSummary

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class EmailServiceError(Exception):
    """Custom exception raised during email delivery errors."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class EmailService:
    """Production Email Delivery Service with Graph, SMTP, and Mock support."""

    def __init__(self, app_settings: Optional[Settings] = None):
        self.settings = app_settings or settings
        self.mock_sent_emails: List[EmailPayload] = []

    def validate_email_address(self, email: Optional[str]) -> bool:
        """Validate if an email address has a valid syntactic structure."""
        if not email or not email.strip():
            return False
        return bool(EMAIL_REGEX.match(email.strip()))

    def _resolve_safe_url(self, raw_url: Optional[str]) -> Tuple[Optional[str], bool]:
        """Resolve URL checking for public_backend_url or localhost inaccessible state."""
        if not raw_url or not raw_url.strip():
            return None, False

        url_str = raw_url.strip()
        is_local = "localhost" in url_str.lower() or "127.0.0.1" in url_str

        # If public_backend_url is configured, replace local host
        if is_local and self.settings.public_backend_url and self.settings.public_backend_url.strip():
            public_base = self.settings.public_backend_url.strip().rstrip("/")
            if "localhost:8000" in url_str or "127.0.0.1:8000" in url_str:
                url_str = url_str.replace("http://localhost:8000", public_base).replace("http://127.0.0.1:8000", public_base)
            elif "localhost:5050" in url_str or "127.0.0.1:5050" in url_str:
                url_str = url_str.replace("http://localhost:5050", public_base).replace("http://127.0.0.1:5050", public_base)
            else:
                url_str = re.sub(r"http://(?:localhost|127\.0\.0\.1)(?::\d+)?", public_base, url_str)
            is_local = False

        return url_str, is_local

    def render_summary_email_html(
        self,
        summary: MeetingSummary,
        meeting_title: str,
        recording_url: Optional[str] = None,
        transcript_url: Optional[str] = None,
    ) -> str:
        """Render a rich HTML email body for a MeetingSummary."""
        title = meeting_title or "Teams Meeting"
        overview = (summary.overview or "").strip()
        if not overview or overview == "Meeting overview generated.":
            overview = "No executive overview provided for this meeting."

        # Key points HTML
        if summary.key_points:
            kp_html = "".join(f"<li style='margin-bottom:8px; line-height:1.5;'>{kp}</li>" for kp in summary.key_points)
        else:
            kp_html = "<li style='margin-bottom:6px; color:#666;'>No key discussion points recorded for this meeting.</li>"

        # Action items HTML
        ai_rows = ""
        if summary.action_items:
            for ai in summary.action_items:
                assignee = ai.assignee or "Unassigned"
                deadline = ai.deadline or "TBD"
                ai_rows += (
                    f"<tr>"
                    f"<td style='padding:10px 12px; border:1px solid #e1e4e8; color:#24292e; vertical-align:top;'>{ai.task}</td>"
                    f"<td style='padding:10px 12px; border:1px solid #e1e4e8; color:#586069; vertical-align:top;'>{assignee}</td>"
                    f"<td style='padding:10px 12px; border:1px solid #e1e4e8; color:#586069; vertical-align:top;'>{deadline}</td>"
                    f"</tr>"
                )
        else:
            ai_rows = (
                "<tr>"
                "<td colspan='3' style='padding:12px; text-align:center; color:#6a737d; background-color:#f8f9fa; border:1px solid #e1e4e8;'>"
                "No action items assigned for this meeting."
                "</td>"
                "</tr>"
            )

        # Decisions HTML
        if summary.decisions:
            dec_html = "".join(
                f"<li style='margin-bottom:8px; line-height:1.5;'><strong>{d.title}</strong>{': ' + d.details if d.details else ''}</li>"
                for d in summary.decisions
            )
        else:
            dec_html = "<li style='margin-bottom:6px; color:#666;'>No decisions were explicitly made.</li>"

        # Resolve Recording link & dynamic format label (.wav / .mp4)
        rec_url, rec_is_local = self._resolve_safe_url(recording_url)
        if rec_url and not rec_is_local:
            ext = "MP4"
            if ".wav" in rec_url.lower():
                ext = "WAV"
            elif ".mp4" in rec_url.lower():
                ext = "MP4"
            elif "." in rec_url:
                ext_match = re.search(r"\.([a-zA-Z0-9]+)(?:\?|$)", rec_url)
                if ext_match:
                    ext = ext_match.group(1).upper()
            rec_link = f"<a href='{rec_url}' style='color:#0078d4; font-weight:bold; text-decoration:none;'>Download Recording ({ext})</a>"
        elif rec_url and rec_is_local:
            rec_link = "<span style='color:#666;'>Available on TekMeet Server (Public URL not configured)</span>"
        else:
            rec_link = "<span style='color:#888;'>Not Available</span>"

        # Resolve Transcript link
        trans_url, trans_is_local = self._resolve_safe_url(transcript_url)
        if trans_url and not trans_is_local:
            trans_link = f"<a href='{trans_url}' style='color:#0078d4; font-weight:bold; text-decoration:none;'>View Full Transcript</a>"
        elif trans_url and trans_is_local:
            trans_link = "<span style='color:#666;'>Available on TekMeet Server (Public URL not configured)</span>"
        else:
            trans_link = "<span style='color:#888;'>Not Available</span>"

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif; background-color: #f4f5f7; margin: 0; padding: 20px;">
  <div style="max-width: 650px; background: #ffffff; margin: 0 auto; padding: 25px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); border: 1px solid #e1e4e8;">
    <div style="border-bottom: 2px solid #0078d4; padding-bottom: 12px; margin-bottom: 20px;">
      <h2 style="color: #0078d4; margin: 0; font-size: 20px;">TekMeet Meeting Summary</h2>
      <h4 style="color: #24292e; margin: 6px 0 0 0; font-size: 16px;">{title}</h4>
    </div>
    <div style="margin-bottom: 22px;">
      <h3 style="color: #0078d4; font-size: 15px; margin: 0 0 8px 0; text-transform: uppercase; letter-spacing: 0.5px;">Executive Overview</h3>
      <p style="color: #24292e; line-height: 1.6; font-size: 14px; margin: 0; background: #f8f9fa; padding: 12px 14px; border-radius: 6px; border-left: 3px solid #0078d4;">{overview}</p>
    </div>
    <div style="margin-bottom: 22px;">
      <h3 style="color: #0078d4; font-size: 15px; margin: 0 0 8px 0; text-transform: uppercase; letter-spacing: 0.5px;">Key Discussion Points</h3>
      <ul style="color: #24292e; padding-left: 20px; font-size: 14px; margin: 0;">{kp_html}</ul>
    </div>
    <div style="margin-bottom: 22px;">
      <h3 style="color: #0078d4; font-size: 15px; margin: 0 0 8px 0; text-transform: uppercase; letter-spacing: 0.5px;">Action Items</h3>
      <table style="width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 4px;">
        <thead>
          <tr style="background-color: #f0f4f8; text-align: left; color: #24292e;">
            <th style="padding: 10px 12px; border: 1px solid #d0d7de; width: 50%;">Task</th>
            <th style="padding: 10px 12px; border: 1px solid #d0d7de; width: 25%;">Assignee</th>
            <th style="padding: 10px 12px; border: 1px solid #d0d7de; width: 25%;">Deadline</th>
          </tr>
        </thead>
        <tbody>{ai_rows}</tbody>
      </table>
    </div>
    <div style="margin-bottom: 22px;">
      <h3 style="color: #0078d4; font-size: 15px; margin: 0 0 8px 0; text-transform: uppercase; letter-spacing: 0.5px;">Decisions Made</h3>
      <ul style="color: #24292e; padding-left: 20px; font-size: 14px; margin: 0;">{dec_html}</ul>
    </div>
    <div style="background: #f6f8fa; padding: 14px; border-radius: 6px; font-size: 13px; color: #444; border: 1px solid #e1e4e8;">
      <p style="margin: 0 0 6px 0;"><strong>Recording:</strong> {rec_link}</p>
      <p style="margin: 0;"><strong>Transcript:</strong> {trans_link}</p>
    </div>
    <div style="border-top: 1px solid #e1e4e8; margin-top: 25px; padding-top: 15px; text-align: center; color: #586069; font-size: 12px;">
      Automated meeting summary delivered by TekMeet Bot Engine.
    </div>
  </div>
</body>
</html>"""

    async def send_summary_email(
        self,
        event_id: str,
        recipient_email: str,
        summary: MeetingSummary,
        meeting_title: str = "Teams Meeting",
        recording_url: Optional[str] = None,
        transcript_url: Optional[str] = None,
        provider_override: Optional[DeliveryProvider] = None,
        max_retries: int = 3,
    ) -> DeliveryRecord:
        """Send a MeetingSummary email via Microsoft Graph API, SMTP, or Mock provider.

        Args:
            event_id: Associated event ID.
            recipient_email: Target organizer email.
            summary: MeetingSummary object.
            meeting_title: Subject/title of meeting.
            recording_url: Optional MP4/WAV download URL.
            transcript_url: Optional transcript URL.
            provider_override: Force delivery provider (GRAPH, SMTP, MOCK).
            max_retries: Retry attempts for transient errors.

        Returns:
            DeliveryRecord tracking outcome status.
        """
        delivery_id = f"del_{uuid.uuid4().hex[:12]}"
        target_provider = provider_override or self._get_configured_provider()

        logger.info(
            "[EmailService] Initiating email delivery (ID: %s) for event '%s' via %s to '%s'",
            delivery_id,
            event_id,
            target_provider,
            recipient_email,
        )

        # Recipient address validation (TC #19)
        if not self.validate_email_address(recipient_email):
            err_msg = f"Invalid or missing recipient email address '{recipient_email}'."
            logger.error("[EmailService] %s", err_msg)
            return DeliveryRecord(
                delivery_id=delivery_id,
                event_id=event_id,
                recipient_email=recipient_email or "missing@invalid",
                provider=target_provider,
                status=DeliveryStatus.FAILED,
                error_message=err_msg,
            )

        # Summary availability validation
        if not summary or getattr(summary, "status", None) == "failed":
            err_msg = "Meeting summary unavailable because transcript/Claude summary was not generated."
            logger.error("[EmailService] %s", err_msg)
            return DeliveryRecord(
                delivery_id=delivery_id,
                event_id=event_id,
                recipient_email=recipient_email,
                provider=target_provider,
                status=DeliveryStatus.FAILED,
                error_message=err_msg,
            )

        subject = f"Meeting Summary: {meeting_title}"
        body_html = self.render_summary_email_html(summary, meeting_title, recording_url, transcript_url)
        body_text = f"Meeting Summary: {meeting_title}\n\nOverview:\n{summary.overview}\n\nKey Points:\n" + "\n".join(f"- {kp}" for kp in summary.key_points)

        payload = EmailPayload(
            event_id=event_id,
            recipient_email=recipient_email.strip(),
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )

        # Retry loop for transient provider errors
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                if target_provider == DeliveryProvider.MOCK:
                    self.mock_sent_emails.append(payload)
                    logger.info("[EmailService] [MOCK] Successfully delivered email to '%s'", recipient_email)
                    return DeliveryRecord(
                        delivery_id=delivery_id,
                        event_id=event_id,
                        recipient_email=recipient_email.strip(),
                        provider=DeliveryProvider.MOCK,
                        status=DeliveryStatus.SENT,
                        retry_count=attempt - 1,
                        sent_at=datetime.now(timezone.utc),
                    )

                elif target_provider == DeliveryProvider.GRAPH:
                    await self._send_via_graph_api(payload)
                    logger.info("[EmailService] [GRAPH] Successfully delivered email to '%s'", recipient_email)
                    return DeliveryRecord(
                        delivery_id=delivery_id,
                        event_id=event_id,
                        recipient_email=recipient_email.strip(),
                        provider=DeliveryProvider.GRAPH,
                        status=DeliveryStatus.SENT,
                        retry_count=attempt - 1,
                        sent_at=datetime.now(timezone.utc),
                    )

                elif target_provider == DeliveryProvider.SMTP:
                    self._send_via_smtp(payload)
                    logger.info("[EmailService] [SMTP] Successfully delivered email to '%s'", recipient_email)
                    return DeliveryRecord(
                        delivery_id=delivery_id,
                        event_id=event_id,
                        recipient_email=recipient_email.strip(),
                        provider=DeliveryProvider.SMTP,
                        status=DeliveryStatus.SENT,
                        retry_count=attempt - 1,
                        sent_at=datetime.now(timezone.utc),
                    )

            except Exception as exc:
                last_error = str(exc)
                logger.warning("[EmailService] Delivery attempt %d/%d failed via %s: %s", attempt, max_retries, target_provider, exc)

        err_msg = f"Delivery failed after {max_retries} attempts. Last error: {last_error}"
        logger.error("[EmailService] %s", err_msg)
        return DeliveryRecord(
            delivery_id=delivery_id,
            event_id=event_id,
            recipient_email=recipient_email.strip(),
            provider=target_provider,
            status=DeliveryStatus.FAILED,
            retry_count=max_retries,
            error_message=err_msg,
        )

    def _get_configured_provider(self) -> DeliveryProvider:
        """Determine configured email provider from settings."""
        p_str = (self.settings.email_provider or "graph").lower()
        if p_str == "smtp":
            return DeliveryProvider.SMTP
        elif p_str == "mock":
            return DeliveryProvider.MOCK
        return DeliveryProvider.GRAPH

    async def _send_via_graph_api(self, payload: EmailPayload) -> None:
        """Send email via Microsoft Graph API POST /v1.0/users/{sender}/sendMail endpoint."""
        try:
            token = entra_auth_service.get_access_token()
        except Exception as exc:
            raise EmailServiceError(f"Entra ID Token acquisition failed: {str(exc)}")

        sender_email = self.settings.email_sender_address or self.settings.azure_bot_user_email or "me"
        sender_name = self.settings.email_sender_name or "TekMeet"
        url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/sendMail"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        graph_payload = {
            "message": {
                "subject": payload.subject,
                "body": {"contentType": "HTML", "content": payload.body_html},
                "from": {
                    "emailAddress": {
                        "name": sender_name,
                        "address": sender_email,
                    }
                },
                "toRecipients": [{"emailAddress": {"address": payload.recipient_email}}],
            },
            "saveToSentItems": "true",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=graph_payload)
            if resp.status_code not in (200, 202):
                raise EmailServiceError(f"Graph sendMail API returned HTTP {resp.status_code}: {resp.text}", status_code=resp.status_code)

    def _send_via_smtp(self, payload: EmailPayload) -> None:
        """Send email via standard SMTP server."""
        if not self.settings.smtp_host:
            raise EmailServiceError("SMTP Host is not configured in settings (SMTP_HOST).")

        from_email = self.settings.smtp_from_email or self.settings.smtp_username or "noreply@tekmeet.local"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = payload.subject
        msg["From"] = from_email
        msg["To"] = payload.recipient_email

        msg.attach(MIMEText(payload.body_text, "plain"))
        msg.attach(MIMEText(payload.body_html, "html"))

        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as server:
            if self.settings.smtp_use_tls:
                server.starttls()
            if self.settings.smtp_username and self.settings.smtp_password:
                pass_str = self.settings.smtp_password.get_secret_value()
                server.login(self.settings.smtp_username, pass_str)
            server.sendmail(from_email, [payload.recipient_email], msg.as_string())


# Global singleton instance
email_service = EmailService()
