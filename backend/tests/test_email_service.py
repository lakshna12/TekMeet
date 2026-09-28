"""Unit tests for Phase 5 EmailService.

Maps to Test Cases:
  - TC #18: Automatic delivery after meeting completion (Email rendering, recipient validation, success tracking).
  - TC #19: Delivery step fails (invalid email, missing SMTP settings, provider failure logged/flagged).
"""

import pytest
from app.models.delivery import DeliveryProvider, DeliveryStatus
from app.models.summary import ActionItem, Decision, MeetingSummary, SummarizationStatus
from app.services.email_service import EmailService, email_service


@pytest.fixture
def sample_meeting_summary() -> MeetingSummary:
    """Fixture providing a sample MeetingSummary."""
    return MeetingSummary(
        summary_id="sum_test_123",
        event_id="event_email_test",
        transcript_id="tr_email_456",
        status=SummarizationStatus.COMPLETED,
        overview="The team aligned on the Phase 5 release deployment plan.",
        key_points=[
            "Verified Microsoft Graph Mail.Send app permissions.",
            "Configured HTML template with action items table.",
        ],
        action_items=[
            ActionItem(task="Deploy Phase 5 Backend", assignee="Developer", deadline="Friday"),
        ],
        decisions=[
            Decision(title="Use Graph API as primary email provider", details="Fall back to SMTP if needed"),
        ],
    )


def test_email_address_validation():
    """Test syntactic validation of recipient email addresses."""
    service = EmailService()

    assert service.validate_email_address("organizer@contoso.com") is True
    assert service.validate_email_address("user.name+tag@domain.co.uk") is True
    assert service.validate_email_address("invalid-email") is False
    assert service.validate_email_address("@missinguser.com") is False
    assert service.validate_email_address("") is False
    assert service.validate_email_address(None) is False


def test_render_summary_email_html(sample_meeting_summary: MeetingSummary):
    """Test rich HTML email template rendering with public URL resolution."""
    from app.core.config import Settings
    custom_settings = Settings(_env_file=None, public_backend_url="https://api.tekmeet.com")
    service = EmailService(app_settings=custom_settings)

    html = service.render_summary_email_html(
        summary=sample_meeting_summary,
        meeting_title="TekMeet Architecture Review",
        recording_url="https://api.tekmeet.com/api/media/recordings/download?file=test.wav",
        transcript_url="http://localhost:8000/api/v1/transcripts/event_email_test",
    )

    assert "TekMeet Meeting Summary" in html
    assert "TekMeet Architecture Review" in html
    assert "Phase 5 release deployment plan" in html
    assert "Verified Microsoft Graph Mail.Send" in html
    assert "Deploy Phase 5 Backend" in html
    assert "Developer" in html
    assert "Download Recording (WAV)" in html
    assert "https://api.tekmeet.com/api/v1/transcripts/event_email_test" in html
    assert "View Full Transcript" in html


def test_render_summary_email_html_localhost_fallback(sample_meeting_summary: MeetingSummary):
    """Test localhost URL rendering fallback when public_backend_url is unconfigured."""
    from app.core.config import Settings
    custom_settings = Settings(_env_file=None, public_backend_url=None)
    service = EmailService(app_settings=custom_settings)

    html = service.render_summary_email_html(
        summary=sample_meeting_summary,
        meeting_title="TekMeet Architecture Review",
        recording_url="http://localhost:5050/api/media/recordings/download?file=test.wav",
        transcript_url="http://localhost:8000/api/v1/transcripts/event_email_test",
    )

    assert "Available on TekMeet Server (Public URL not configured)" in html
    assert "http://localhost:8000" not in html


@pytest.mark.asyncio
async def test_tc18_mock_email_delivery_success(sample_meeting_summary: MeetingSummary):
    """TC #18: Test successful email delivery via mock provider."""
    service = EmailService()

    record = await service.send_summary_email(
        event_id="event_email_test",
        recipient_email="organizer@contoso.com",
        summary=sample_meeting_summary,
        meeting_title="Sprint Demo",
        provider_override=DeliveryProvider.MOCK,
    )

    assert record is not None
    assert record.status == DeliveryStatus.SENT
    assert record.recipient_email == "organizer@contoso.com"
    assert record.provider == DeliveryProvider.MOCK
    assert record.sent_at is not None
    assert record.error_message is None
    assert len(service.mock_sent_emails) == 1
    assert service.mock_sent_emails[0].recipient_email == "organizer@contoso.com"


@pytest.mark.asyncio
async def test_tc19_invalid_recipient_email_failure(sample_meeting_summary: MeetingSummary):
    """TC #19: Test delivery fails gracefully when recipient email is invalid."""
    service = EmailService()

    record = await service.send_summary_email(
        event_id="event_email_invalid",
        recipient_email="invalid-email-address",
        summary=sample_meeting_summary,
        provider_override=DeliveryProvider.MOCK,
    )

    assert record is not None
    assert record.status == DeliveryStatus.FAILED
    assert "Invalid or missing recipient email" in record.error_message
    assert record.sent_at is None
    assert len(service.mock_sent_emails) == 0  # No mock email dispatched


@pytest.mark.asyncio
async def test_tc19_smtp_missing_host_failure(sample_meeting_summary: MeetingSummary):
    """TC #19: Test delivery fails gracefully when SMTP host is missing."""
    from app.core.config import Settings
    custom_settings = Settings(smtp_host=None)
    service = EmailService(app_settings=custom_settings)

    record = await service.send_summary_email(
        event_id="event_smtp_fail",
        recipient_email="organizer@contoso.com",
        summary=sample_meeting_summary,
        provider_override=DeliveryProvider.SMTP,
        max_retries=2,
    )

    assert record is not None
    assert record.status == DeliveryStatus.FAILED
    assert record.retry_count == 2
    assert "SMTP Host is not configured" in record.error_message
