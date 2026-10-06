"""Unit tests for meeting-end lifecycle and actual call termination handling."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.models.meeting import MeetingEvent
from app.models.scheduler import MeetingStatus, ScheduledMeetingJob
from app.services.meeting_dispatcher import MeetingDispatcher


@pytest.fixture
def mock_settings():
    return Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("mock_secret"),
        JOIN_BUFFER_SECONDS=60,
        USE_APP_HOSTED_MEDIA=False,
    )


@pytest.mark.asyncio
async def test_meeting_ends_early_at_5_minutes_not_scheduled_30_minutes(mock_settings, caplog):
    """Test 1 & 3: Meeting scheduled for 30 mins (18:30->19:00), actual call ends at 5 mins (18:35).
    Verifies recording stops immediately at actual termination without waiting for 19:00.
    """
    caplog.set_level(logging.INFO)

    mock_pipeline = MagicMock()
    mock_pipeline.process_end_to_end_job = AsyncMock(return_value=MagicMock(
        status="completed",
        pipeline_id="pipe_123",
        transcript=MagicMock(status=MagicMock(value="completed"), segments=[]),
        summary=MagicMock(status=MagicMock(value="completed")),
        delivery_record=MagicMock(status=MagicMock(value="sent")),
        recipient_email="organizer@test.com",
    ))

    dispatcher = MeetingDispatcher(pipeline=mock_pipeline, app_settings=mock_settings)

    start_time = datetime(2026, 9, 30, 18, 30, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 9, 30, 19, 0, 0, tzinfo=timezone.utc)

    event = MeetingEvent(
        event_id="evt_30min_scheduled",
        subject="30-Min Standup",
        start_time=start_time,
        end_time=end_time,
        is_teams_meeting=True,
        join_url="https://teams.microsoft.com/meet/30min",
    )

    # 1. Register meeting
    dispatcher.register_or_update_meetings([event], now_utc=start_time)
    job = dispatcher.get_job("evt_30min_scheduled")
    assert job.status == MeetingStatus.SCHEDULED

    # 2. Dispatch meeting at 18:30
    dispatcher.join_handler.handle_join = AsyncMock(return_value=True)
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=start_time)
    job = dispatcher.get_job("evt_30min_scheduled")
    assert job.status == MeetingStatus.TRIGGERED
    job.call_id = "call_30min_123"

    # 3. Simulate continuous evaluation at 18:35 (25 mins BEFORE scheduled end)
    actual_end_time = datetime(2026, 9, 30, 18, 35, 0, tzinfo=timezone.utc)

    # Calling evaluate_and_dispatch_due_meetings at 18:35 MUST NOT complete job based on calendar end time
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=actual_end_time)
    assert job.status == MeetingStatus.TRIGGERED, "Job must remain TRIGGERED until actual termination call arrives"

    # 4. Trigger actual call termination event at 18:35
    completed_job = dispatcher.handle_actual_meeting_end(
        job,
        call_id="call_30min_123",
        actual_end_time=actual_end_time,
    )

    assert completed_job is not None
    assert completed_job.status == MeetingStatus.COMPLETED
    assert completed_job.updated_at == actual_end_time

    # Verify log output format requirements
    assert "[MEETING] Meeting ended" in caplog.text
    assert "Event ID: evt_30min_scheduled" in caplog.text
    assert "Call ID: call_30min_123" in caplog.text
    assert "Actual end time (IST)" in caplog.text
    assert "Meeting duration: 5m 0s" in caplog.text or "5m" in caplog.text


@pytest.mark.asyncio
async def test_actual_termination_stops_recording_and_triggers_phase5(mock_settings, caplog):
    """Test 2 & 7: Actual termination stops recording and triggers Phase 5 pipeline correctly."""
    caplog.set_level(logging.INFO)
    mock_pipeline = MagicMock()

    mock_pipeline.process_end_to_end_job = AsyncMock(return_value=MagicMock(
        status="completed",
        pipeline_id="pipe_456",
        transcript=MagicMock(status=MagicMock(value="completed"), segments=[]),
        summary=MagicMock(status=MagicMock(value="completed")),
        delivery_record=MagicMock(status=MagicMock(value="sent")),
        recipient_email="user@test.com",
    ))

    dispatcher = MeetingDispatcher(pipeline=mock_pipeline, app_settings=mock_settings)

    now = datetime.now(timezone.utc)
    job = ScheduledMeetingJob(
        event_id="evt_term_test",
        subject="Termination Test",
        start_time=now,
        end_time=now + timedelta(minutes=60),
        status=MeetingStatus.TRIGGERED,
        call_id="call_term_999",
        created_at=now,
        updated_at=now,
    )
    dispatcher._jobs[job.event_id] = job

    # Trigger actual call termination
    dispatcher.handle_actual_meeting_end(job, call_id="call_term_999", actual_end_time=now + timedelta(minutes=7))
    result = await dispatcher.trigger_phase5_pipeline(job)

    assert result is not None
    assert result.status == "completed"
    assert "[MEDIA] Recording stop requested" in caplog.text
    assert "[MEDIA] WAV finalized successfully" in caplog.text


@pytest.mark.asyncio
async def test_correct_event_and_call_id_correlation(mock_settings):
    """Test 4: Correct Event ID and Call ID are correlated during termination."""
    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    now = datetime.now(timezone.utc)

    job1 = ScheduledMeetingJob(
        event_id="evt_alpha",
        subject="Alpha Call",
        start_time=now,
        end_time=now + timedelta(minutes=30),
        status=MeetingStatus.TRIGGERED,
        call_id="call_alpha_111",
        created_at=now,
        updated_at=now,
    )
    job2 = ScheduledMeetingJob(
        event_id="evt_beta",
        subject="Beta Call",
        start_time=now,
        end_time=now + timedelta(minutes=30),
        status=MeetingStatus.TRIGGERED,
        call_id="call_beta_222",
        created_at=now,
        updated_at=now,
    )

    dispatcher._jobs[job1.event_id] = job1
    dispatcher._jobs[job2.event_id] = job2

    # Terminate call_beta_222 specifically
    res = dispatcher.handle_actual_meeting_end("evt_beta", call_id="call_beta_222")
    assert res.event_id == "evt_beta"
    assert res.status == MeetingStatus.COMPLETED

    # Verify job1 remains TRIGGERED
    assert dispatcher.get_job("evt_alpha").status == MeetingStatus.TRIGGERED


@pytest.mark.asyncio
async def test_duplicate_termination_events_ignored(mock_settings, caplog):
    """Test 5: Duplicate termination events do not stop/finalize the same recording twice."""
    caplog.set_level(logging.INFO)
    mock_pipeline = MagicMock()

    mock_pipeline.process_end_to_end_job = AsyncMock()

    dispatcher = MeetingDispatcher(pipeline=mock_pipeline, app_settings=mock_settings)
    now = datetime.now(timezone.utc)

    job = ScheduledMeetingJob(
        event_id="evt_dup",
        subject="Dup Test",
        start_time=now,
        end_time=now + timedelta(minutes=30),
        status=MeetingStatus.TRIGGERED,
        call_id="call_dup_555",
        created_at=now,
        updated_at=now,
    )
    dispatcher._jobs[job.event_id] = job

    # First termination event
    res1 = dispatcher.handle_actual_meeting_end(job, call_id="call_dup_555")
    assert res1.status == MeetingStatus.COMPLETED

    # Second (duplicate) termination event
    res2 = dispatcher.handle_actual_meeting_end(job, call_id="call_dup_555")
    assert res2.status == MeetingStatus.COMPLETED
    assert "Duplicate termination ignored" in caplog.text


@pytest.mark.asyncio
async def test_different_active_meeting_not_affected(mock_settings):
    """Test 6: A different active meeting is not affected by another call's termination."""
    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    now = datetime.now(timezone.utc)

    meeting_a = ScheduledMeetingJob(
        event_id="evt_A",
        subject="Meeting A",
        start_time=now,
        end_time=now + timedelta(minutes=45),
        status=MeetingStatus.TRIGGERED,
        call_id="call_A",
        created_at=now,
        updated_at=now,
    )
    meeting_b = ScheduledMeetingJob(
        event_id="evt_B",
        subject="Meeting B",
        start_time=now,
        end_time=now + timedelta(minutes=45),
        status=MeetingStatus.TRIGGERED,
        call_id="call_B",
        created_at=now,
        updated_at=now,
    )

    dispatcher._jobs[meeting_a.event_id] = meeting_a
    dispatcher._jobs[meeting_b.event_id] = meeting_b

    # Terminate Meeting A
    dispatcher.handle_actual_meeting_end(meeting_a, call_id="call_A")

    # Assert Meeting A is completed while Meeting B stays triggered
    assert dispatcher.get_job("evt_A").status == MeetingStatus.COMPLETED
    assert dispatcher.get_job("evt_B").status == MeetingStatus.TRIGGERED
