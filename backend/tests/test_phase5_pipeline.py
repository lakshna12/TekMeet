"""Unit and Integration Tests for Phase5Pipeline (End-to-End Orchestration).

Maps to Test Cases:
  - TC #18: Automatic delivery after meeting completion (Mock verified).
  - TC #19: Delivery step fails (invalid organizer email, logged/flagged, transcript/summary preserved).
  - TC #22: Run full pipeline end-to-end on single meeting (Pipeline orchestration).
"""

import tempfile
from pathlib import Path
import pytest

from app.models.delivery import DeliveryProvider, DeliveryStatus
from app.models.scheduler import MeetingStatus, ScheduledMeetingJob
from app.models.summary import SummarizationStatus
from app.models.transcript import TranscriptionStatus
from app.services.email_service import EmailService
from app.services.phase5_pipeline import Phase5Pipeline
from app.services.storage_service import StorageService
from app.services.summarization_service import SummarizationService
from app.services.transcription_service import TranscriptionService


@pytest.fixture
def temp_storage_dir():
    """Fixture providing a temporary directory for isolated storage tests."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.fixture
def sample_job() -> ScheduledMeetingJob:
    """Fixture providing a sample completed ScheduledMeetingJob."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return ScheduledMeetingJob(
        event_id="event_p5_test_01",
        subject="Sprint Planning Meeting",
        start_time=now,
        end_time=now,
        join_url="https://teams.microsoft.com/l/meetup-join/test",
        organizer_email="organizer@contoso.com",
        status=MeetingStatus.COMPLETED,
        call_id="call_p5_01",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_1_full_successful_pipeline_mock(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Test 1: Full successful pipeline using mocked external services."""
    storage = StorageService(data_dir=temp_storage_dir)
    pipeline = Phase5Pipeline(db_service=storage)

    mock_stt = {"status": "completed", "full_text": "Meeting speech text.", "duration_seconds": 30.0}
    mock_summary = {"overview": "Meeting summary text.", "key_points": ["Point 1"]}

    result = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="meeting_test.wav",
        mock_stt_override=mock_stt,
        mock_summary_override=mock_summary,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert result is not None
    assert result.status == "completed"
    assert result.event_id == "event_p5_test_01"
    assert result.transcript.status == TranscriptionStatus.COMPLETED
    assert result.summary.status == SummarizationStatus.COMPLETED
    assert result.delivery_record.status == DeliveryStatus.SENT
    assert result.recipient_email == "organizer@contoso.com"


@pytest.mark.asyncio
async def test_2_recording_missing_handling(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Test 2: Pipeline handles missing/unreadable recording file gracefully."""
    storage = StorageService(data_dir=temp_storage_dir)
    pipeline = Phase5Pipeline(db_service=storage)

    result = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="non_existent_file_9999.wav",
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert result is not None
    assert result.status == "failed"
    assert "not found" in result.error_message.lower()
    assert result.delivery_record.status == DeliveryStatus.FAILED


@pytest.mark.asyncio
async def test_3_transcription_failure_handling(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Test 3: Pipeline handles transcription stage failure gracefully."""
    storage = StorageService(data_dir=temp_storage_dir)
    pipeline = Phase5Pipeline(db_service=storage)

    mock_stt_failed = {"status": "failed", "error_message": "Audio format error."}

    result = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="corrupt.wav",
        mock_stt_override=mock_stt_failed,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert result.status == "failed"
    assert result.transcript.status == TranscriptionStatus.FAILED
    assert result.summary is None
    assert result.delivery_record.status == DeliveryStatus.FAILED


@pytest.mark.asyncio
async def test_4_summary_failure_handling(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Test 4: Pipeline handles summarization failure (partial completion status)."""
    storage = StorageService(data_dir=temp_storage_dir)
    pipeline = Phase5Pipeline(db_service=storage)

    mock_stt_ok = {"status": "completed", "full_text": "Valid transcript.", "duration_seconds": 15.0}
    mock_summary_fail = {"status": "failed", "error_message": "Gemini rate limit."}

    result = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="valid.wav",
        mock_stt_override=mock_stt_ok,
        mock_summary_override=mock_summary_fail,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert result.status == "partial"
    assert result.transcript.status == TranscriptionStatus.COMPLETED
    assert result.summary.status == SummarizationStatus.FAILED
    assert result.delivery_record.status == DeliveryStatus.FAILED


@pytest.mark.asyncio
async def test_6_7_email_delivery_failure_preserves_transcript_summary(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Test 6 & 7: Failed email delivery does NOT delete completed transcript or summary."""
    storage = StorageService(data_dir=temp_storage_dir)

    # Use custom settings with missing SMTP host to force delivery failure
    from app.core.config import Settings
    custom_settings = Settings(_env_file=None, email_provider="smtp", smtp_host=None)
    mail_service = EmailService(app_settings=custom_settings)

    pipeline = Phase5Pipeline(db_service=storage, mail_service=mail_service)

    mock_stt_ok = {"status": "completed", "full_text": "Important meeting notes.", "duration_seconds": 20.0}
    mock_summary_ok = {"overview": "Important overview.", "key_points": ["Point 1"]}

    result = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="meeting.wav",
        mock_stt_override=mock_stt_ok,
        mock_summary_override=mock_summary_ok,
    )

    assert result.status == "partial"
    assert result.delivery_record.status == DeliveryStatus.FAILED

    # Verify transcript and summary remain intact in disk storage!
    persisted_transcript = storage.get_transcript(sample_job.event_id)
    persisted_summary = storage.get_summary(sample_job.event_id)

    assert persisted_transcript is not None
    assert persisted_transcript.full_text == "Important meeting notes."
    assert persisted_summary is not None
    assert persisted_summary.overview == "Important overview."


@pytest.mark.asyncio
async def test_8_invalid_missing_organizer_email(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Test 8 (TC #19): Invalid/missing organizer email sets DeliveryStatus.FAILED without crashing."""
    storage = StorageService(data_dir=temp_storage_dir)

    # Set invalid email on job
    sample_job.organizer_email = "invalid_email_syntax"

    from app.core.config import Settings
    custom_settings = Settings(azure_bot_user_email=None)
    pipeline = Phase5Pipeline(db_service=storage, app_settings=custom_settings)

    mock_stt_ok = {"status": "completed", "full_text": "Speech text.", "duration_seconds": 10.0}
    mock_summary_ok = {"overview": "Overview.", "key_points": []}

    result = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="test.wav",
        mock_stt_override=mock_stt_ok,
        mock_summary_override=mock_summary_ok,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert result.status == "partial"
    assert result.delivery_record.status == DeliveryStatus.FAILED
    assert "Invalid or missing organizer email" in result.delivery_record.error_message


@pytest.mark.asyncio
async def test_9_10_idempotent_duplicate_protection(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Tests 9 & 10: Deduplication prevents sending duplicate emails and reuses existing transcript/summary."""
    storage = StorageService(data_dir=temp_storage_dir)
    pipeline = Phase5Pipeline(db_service=storage)

    mock_stt = {"status": "completed", "full_text": "First run text.", "duration_seconds": 10.0}
    mock_summary = {"overview": "First run summary.", "key_points": []}

    # First run -> SENT
    res1 = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="test.wav",
        mock_stt_override=mock_stt,
        mock_summary_override=mock_summary,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert res1.status == "completed"
    assert res1.delivery_record.status == DeliveryStatus.SENT

    # Second run for same event_id
    res2 = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="test.wav",
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert res2.status == "idempotent_duplicate"
    assert res2.transcript.full_text == "First run text."
    assert res2.summary.overview == "First run summary."


@pytest.mark.asyncio
async def test_11_delivery_record_persistence(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Test 11: Verify delivery record is persisted in StorageService disk registry."""
    storage = StorageService(data_dir=temp_storage_dir)
    pipeline = Phase5Pipeline(db_service=storage)

    mock_stt = {"status": "completed", "full_text": "Speech.", "duration_seconds": 5.0}
    mock_summary = {"overview": "Summary.", "key_points": []}

    res = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="test.wav",
        mock_stt_override=mock_stt,
        mock_summary_override=mock_summary,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    persisted_delivery = storage.get_delivery_record(sample_job.event_id)
    assert persisted_delivery is not None
    assert persisted_delivery.delivery_id == res.delivery_record.delivery_id
    assert persisted_delivery.status == DeliveryStatus.SENT


@pytest.mark.asyncio
async def test_user_sample_transcript_to_email_e2e(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Verify actual transcript -> summary -> email pipeline flow for required sample meeting transcript.
    
    Sample Transcript:
    "We discussed the TekMeet Phase 4 implementation.
    Lakshna will complete the transcription integration by Monday.
    The team decided to use Claude API for summarization.
    We will test the complete pipeline tomorrow."
    """
    storage = StorageService(data_dir=temp_storage_dir)
    email_svc = EmailService()
    pipeline = Phase5Pipeline(db_service=storage, mail_service=email_svc)

    mock_stt = {
        "status": "completed",
        "full_text": (
            "We discussed the TekMeet Phase 4 implementation. "
            "Lakshna will complete the transcription integration by Monday. "
            "The team decided to use Claude API for summarization. "
            "We will test the complete pipeline tomorrow."
        ),
        "duration_seconds": 30.0,
    }

    mock_summary = {
        "overview": "The team discussed the TekMeet Phase 4 implementation and planned integration and testing milestones.",
        "key_points": [
            "Phase 4 implementation was discussed.",
            "Complete pipeline testing is scheduled for tomorrow."
        ],
        "action_items": [
            {"task": "Complete transcription integration", "assignee": "Lakshna", "deadline": "Monday"}
        ],
        "decisions": [
            {"title": "Use Claude API for summarization", "details": None}
        ]
    }

    result = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="sample_meeting.wav",
        mock_stt_override=mock_stt,
        mock_summary_override=mock_summary,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert result.status == "completed"
    assert result.delivery_record.status == DeliveryStatus.SENT

    # Verify email payload content
    assert len(email_svc.mock_sent_emails) == 1
    sent_payload = email_svc.mock_sent_emails[0]
    html_content = sent_payload.body_html.lower()

    # 1. Must contain real meeting points
    assert "phase 4" in html_content
    assert "lakshna" in html_content
    assert "monday" in html_content
    assert "claude api" in html_content

    # 2. Must NOT contain test notification content
    forbidden_test_phrases = [
        "graph api mail.send integration operational",
        "rich html email formatting verified",
        "confirm receipt of meeting summary email",
        "email delivery verified via microsoft graph api"
    ]
    for phrase in forbidden_test_phrases:
        assert phrase not in html_content, f"Test notification phrase '{phrase}' found in email body!"


@pytest.mark.asyncio
async def test_e2e_pipeline_unavailable_summary_handling(temp_storage_dir: Path, sample_job: ScheduledMeetingJob):
    """Verify that when STT or summary is unavailable, fake summaries are NOT sent and clear error is logged."""
    storage = StorageService(data_dir=temp_storage_dir)
    email_svc = EmailService()
    pipeline = Phase5Pipeline(db_service=storage, mail_service=email_svc)

    mock_stt_failed = {"status": "failed", "error_message": "Audio file corrupt"}

    result = await pipeline.process_end_to_end_job(
        job=sample_job,
        file_path_or_name="corrupt.wav",
        mock_stt_override=mock_stt_failed,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert result.status == "failed"
    assert result.delivery_record.status == DeliveryStatus.FAILED
    assert "meeting summary unavailable because transcript/claude summary was not generated" in result.delivery_record.error_message.lower()
    assert len(email_svc.mock_sent_emails) == 0  # No fake email sent!
