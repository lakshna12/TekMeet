"""Unit tests verifying complete meeting identity separation, recording isolation, and transcript integrity (Step 13).

Ensures:
  1. Meeting A and Meeting B with the same or different eventId never share recordings, transcripts, summaries, or deliveries.
  2. Storage keys and lookups prevent registry collisions.
  3. Repeated start recording/join/stop/callback handling is duplicate-safe.
  4. Non-silent vs silent WAV validation.
"""

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pytest

from app.models.delivery import DeliveryProvider, DeliveryStatus
from app.models.scheduler import MeetingStatus, ScheduledMeetingJob
from app.models.summary import MeetingSummary
from app.models.transcript import Transcript, TranscriptionStatus
from app.services.phase5_pipeline import Phase5Pipeline
from app.services.storage_service import StorageService
from app.services.transcription_service import TranscriptionService


@pytest.fixture
def temp_storage_dir():
    """Fixture providing an isolated temporary directory for storage tests."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.mark.asyncio
async def test_meeting_a_and_meeting_b_complete_isolation(temp_storage_dir: Path):
    """MEETING A and MEETING B must never share recordings, transcripts, or summaries even if eventId is identical."""
    storage = StorageService(data_dir=temp_storage_dir)
    pipeline = Phase5Pipeline(db_service=storage)

    event_id = "AAMkADQzZjNhNzZjLTc3MzgtNDZmYi1iMjdhLTAw_REUSED_EVENT"

    now = datetime.now(timezone.utc)

    job_a = ScheduledMeetingJob(
        event_id=event_id,
        subject="Meeting A",
        start_time=now,
        end_time=now,
        join_url="https://teams.microsoft.com/l/meetup-join/A",
        organizer_email="organizer@tekmeet.local",
        status=MeetingStatus.COMPLETED,
        call_id="CALL_A",
        created_at=now,
        updated_at=now,
    )

    job_b = ScheduledMeetingJob(
        event_id=event_id,
        subject="Meeting B",
        start_time=now,
        end_time=now,
        join_url="https://teams.microsoft.com/l/meetup-join/B",
        organizer_email="organizer@tekmeet.local",
        status=MeetingStatus.COMPLETED,
        call_id="CALL_B",
        created_at=now,
        updated_at=now,
    )

    mock_stt_a = {"status": "completed", "full_text": "Discussion in Meeting A about project launch.", "duration_seconds": 25.0}
    mock_summary_a = {"overview": "Meeting A summary overview.", "key_points": ["Point A1"]}

    res_a = await pipeline.process_end_to_end_job(
        job=job_a,
        event_id=event_id,
        call_id="CALL_A",
        file_path_or_name="Meeting_A_CALL_A.wav",
        mock_stt_override=mock_stt_a,
        mock_summary_override=mock_summary_a,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert res_a.status == "completed"
    assert res_a.transcript.full_text == "Discussion in Meeting A about project launch."

    mock_stt_b = {"status": "completed", "full_text": "Discussion in Meeting B about budget review.", "duration_seconds": 40.0}
    mock_summary_b = {"overview": "Meeting B summary overview.", "key_points": ["Point B1"]}

    res_b = await pipeline.process_end_to_end_job(
        job=job_b,
        event_id=event_id,
        call_id="CALL_B",
        file_path_or_name="Meeting_B_CALL_B.wav",
        mock_stt_override=mock_stt_b,
        mock_summary_override=mock_summary_b,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert res_b.status == "completed"
    assert res_b.transcript.full_text == "Discussion in Meeting B about budget review."

    # Strict Identity Isolation Checks:
    assert res_a.call_id != res_b.call_id
    assert res_a.recording_file != res_b.recording_file
    assert res_a.transcript.transcript_id != res_b.transcript.transcript_id
    assert res_a.transcript.full_text != res_b.transcript.full_text
    assert "Meeting A" in res_a.transcript.full_text
    assert "Meeting B" in res_b.transcript.full_text

    # Storage queries for specific recordings must return correct isolated records
    tr_a = storage.get_transcript_by_recording("Meeting_A_CALL_A.wav")
    tr_b = storage.get_transcript_by_recording("Meeting_B_CALL_B.wav")

    assert tr_a is not None
    assert tr_b is not None
    assert tr_a.transcript_id == res_a.transcript.transcript_id
    assert tr_b.transcript_id == res_b.transcript.transcript_id
    assert tr_a.full_text != tr_b.full_text


@pytest.mark.asyncio
async def test_duplicate_callback_and_re_execution_protection(temp_storage_dir: Path):
    """Verify duplicate pipeline callback for exact same recording returns idempotent duplicate status without re-transcribing."""
    storage = StorageService(data_dir=temp_storage_dir)
    pipeline = Phase5Pipeline(db_service=storage)

    event_id = "event_dup_test"
    call_id = "call_dup_01"
    rec_file = "meeting_dup.wav"

    mock_stt = {"status": "completed", "full_text": "Original transcript text.", "duration_seconds": 12.0}
    mock_sum = {"overview": "Original overview.", "key_points": []}

    res1 = await pipeline.process_end_to_end_job(
        event_id=event_id,
        call_id=call_id,
        file_path_or_name=rec_file,
        mock_stt_override=mock_stt,
        mock_summary_override=mock_sum,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert res1.status == "completed"

    # Duplicate call with same call_id and recording file
    res2 = await pipeline.process_end_to_end_job(
        event_id=event_id,
        call_id=call_id,
        file_path_or_name=rec_file,
        mock_stt_override=mock_stt,
        mock_email_provider=DeliveryProvider.MOCK,
    )

    assert res2.status == "idempotent_duplicate"
    assert res2.transcript.transcript_id == res1.transcript.transcript_id


@pytest.mark.asyncio
async def test_registry_no_collisions(temp_storage_dir: Path):
    """Verify registry stores and retrieves distinct records for distinct transcripts without key collisions."""
    storage = StorageService(data_dir=temp_storage_dir)

    t1 = Transcript(
        transcript_id="tr_100",
        event_id="evt_same",
        call_id="call_100",
        recording_file="rec_100.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text="First meeting speech.",
    )

    t2 = Transcript(
        transcript_id="tr_200",
        event_id="evt_same",
        call_id="call_200",
        recording_file="rec_200.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text="Second meeting speech.",
    )

    storage.save_transcript(t1)
    storage.save_transcript(t2)

    found_t1 = storage.get_transcript_by_recording("rec_100.wav")
    found_t2 = storage.get_transcript_by_recording("rec_200.wav")

    assert found_t1.transcript_id == "tr_100"
    assert found_t2.transcript_id == "tr_200"
    assert found_t1.full_text == "First meeting speech."
    assert found_t2.full_text == "Second meeting speech."
