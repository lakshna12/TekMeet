"""Unit tests for MeetingDispatcher service."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.models.meeting import MeetingEvent
from app.models.scheduler import MeetingStatus
from app.services.meeting_dispatcher import MeetingDispatcher, MeetingJoinHandler


@pytest.fixture
def mock_settings():
    return Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("mock_secret"),
        JOIN_BUFFER_SECONDS=60,  # 60s early join
        USE_APP_HOSTED_MEDIA=False,
    )


@pytest.mark.asyncio
async def test_register_and_update_meeting_jobs(mock_settings):
    """Test registering various meeting states (future, cancelled, past)."""
    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)

    meetings = [
        # Future scheduled meeting
        MeetingEvent(
            event_id="evt_future",
            subject="Future Standup",
            start_time=now + timedelta(minutes=30),
            end_time=now + timedelta(minutes=60),
            is_teams_meeting=True,
            join_url="https://teams.microsoft.com/meet/future",
        ),
        # Cancelled meeting
        MeetingEvent(
            event_id="evt_cancelled",
            subject="Cancelled Sync",
            start_time=now + timedelta(minutes=10),
            end_time=now + timedelta(minutes=40),
            is_teams_meeting=True,
            is_cancelled=True,
        ),
        # Past meeting
        MeetingEvent(
            event_id="evt_past",
            subject="Yesterday Review",
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=1),
            is_teams_meeting=True,
        ),
    ]

    jobs = dispatcher.register_or_update_meetings(meetings, now_utc=now)
    assert len(jobs) == 3

    job_future = dispatcher.get_job("evt_future")
    assert job_future is not None
    assert job_future.status == MeetingStatus.SCHEDULED

    job_cancelled = dispatcher.get_job("evt_cancelled")
    assert job_cancelled is not None
    assert job_cancelled.status == MeetingStatus.CANCELLED

    job_past = dispatcher.get_job("evt_past")
    assert job_past is not None
    assert job_past.status == MeetingStatus.MISSED


@pytest.mark.asyncio
async def test_evaluate_and_dispatch_due_meeting(mock_settings):
    """Test that a meeting starting within the join buffer is dispatched and marked TRIGGERED."""
    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)

    # Meeting starts in 30 seconds (within 60s join buffer)
    due_meeting = MeetingEvent(
        event_id="evt_due_now",
        subject="Urgent Planning",
        start_time=now + timedelta(seconds=30),
        end_time=now + timedelta(minutes=30),
        is_teams_meeting=True,
        join_url="https://teams.microsoft.com/meet/due",
    )

    # Meeting starts in 10 minutes (outside join buffer)
    later_meeting = MeetingEvent(
        event_id="evt_later",
        subject="Later Planning",
        start_time=now + timedelta(minutes=10),
        end_time=now + timedelta(minutes=40),
        is_teams_meeting=True,
        join_url="https://teams.microsoft.com/meet/later",
    )

    dispatcher.register_or_update_meetings([due_meeting, later_meeting], now_utc=now)

    # Evaluate dispatch
    dispatched = await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=now)
    assert len(dispatched) == 1
    assert dispatched[0].event_id == "evt_due_now"

    job_due = dispatcher.get_job("evt_due_now")
    assert job_due.status == MeetingStatus.TRIGGERED
    assert job_due.dispatched_at == now

    job_later = dispatcher.get_job("evt_later")
    assert job_later.status == MeetingStatus.SCHEDULED


@pytest.mark.asyncio
async def test_lifecycle_transition_to_completed(mock_settings):
    """Test that an already TRIGGERED meeting transitions to COMPLETED when past end_time."""
    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    start_time = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 9, 11, 12, 30, 0, tzinfo=timezone.utc)

    meeting = MeetingEvent(
        event_id="evt_lifecycle",
        subject="Sprint Review",
        start_time=start_time,
        end_time=end_time,
        is_teams_meeting=True,
    )

    # 1. Register at 11:59:30 (starts in 30s) -> TRIGGERED
    t1 = start_time - timedelta(seconds=30)
    dispatcher.register_or_update_meetings([meeting], now_utc=t1)
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=t1)
    assert dispatcher.get_job("evt_lifecycle").status == MeetingStatus.TRIGGERED

    # 2. Re-evaluate at 12:35:00 (past end_time) -> transitions to COMPLETED
    t2 = end_time + timedelta(minutes=5)
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=t2)
    assert dispatcher.get_job("evt_lifecycle").status == MeetingStatus.COMPLETED


@pytest.mark.asyncio
async def test_join_handler_failure_marks_job_failed(mock_settings):
    """Test that a failing join handler sets job status to FAILED."""
    mock_handler = MagicMock(spec=MeetingJoinHandler)
    mock_handler.handle_join = AsyncMock(return_value=False)

    dispatcher = MeetingDispatcher(join_handler=mock_handler, app_settings=mock_settings)
    now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)

    meeting = MeetingEvent(
        event_id="evt_fail",
        subject="Faulty Call",
        start_time=now + timedelta(seconds=10),
        end_time=now + timedelta(minutes=30),
        is_teams_meeting=True,
    )

    dispatcher.register_or_update_meetings([meeting], now_utc=now)
    dispatched = await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=now)

    assert len(dispatched) == 0
    job = dispatcher.get_job("evt_fail")
    assert job.status == MeetingStatus.FAILED
    assert "returned failure" in job.dispatch_message


@pytest.mark.asyncio
async def test_automatic_phase5_pipeline_trigger_on_completed(mock_settings):
    """Test that transitioning a meeting to COMPLETED automatically invokes Phase5Pipeline."""
    mock_pipeline = AsyncMock()
    mock_pipeline.process_end_to_end_job = AsyncMock(
        return_value=MagicMock(status="completed", pipeline_id="p5_test123")
    )

    dispatcher = MeetingDispatcher(pipeline=mock_pipeline, app_settings=mock_settings)
    start_time = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 9, 11, 12, 30, 0, tzinfo=timezone.utc)

    meeting = MeetingEvent(
        event_id="evt_auto_p5",
        subject="Strategy Sync",
        start_time=start_time,
        end_time=end_time,
        is_teams_meeting=True,
        organizer_email="organizer@contoso.com",
    )

    # 1. Register and trigger
    t1 = start_time - timedelta(seconds=30)
    dispatcher.register_or_update_meetings([meeting], now_utc=t1)
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=t1)
    assert dispatcher.get_job("evt_auto_p5").status == MeetingStatus.TRIGGERED

    # 2. Re-evaluate past end_time -> transitions to COMPLETED
    t2 = end_time + timedelta(minutes=5)
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=t2)
    assert dispatcher.get_job("evt_auto_p5").status == MeetingStatus.COMPLETED

    # 3. Wait for background pipeline tasks to complete
    await dispatcher.wait_for_active_pipelines()

    # Verify pipeline process_end_to_end_job was called with the job
    mock_pipeline.process_end_to_end_job.assert_called_once()
    call_kwargs = mock_pipeline.process_end_to_end_job.call_args.kwargs
    assert call_kwargs["job"].event_id == "evt_auto_p5"


@pytest.mark.asyncio
async def test_trigger_phase5_pipeline_media_worker_stop(mock_settings, monkeypatch):
    """Test that trigger_phase5_pipeline stops recording on MediaWorker when use_app_hosted_media is True."""
    mock_settings.use_app_hosted_media = True
    mock_settings.media_worker_url = "http://localhost:5050"

    mock_pipeline = AsyncMock()
    mock_pipeline.process_end_to_end_job = AsyncMock(
        return_value=MagicMock(status="completed", pipeline_id="p5_mw_stop")
    )

    # Mock httpx AsyncClient GET request for media worker stop recording
    mock_get = AsyncMock(return_value=MagicMock(text='{"status":"stopped"}'))
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    dispatcher = MeetingDispatcher(pipeline=mock_pipeline, app_settings=mock_settings)
    monkeypatch.setattr(dispatcher, "check_recording_readiness", AsyncMock(return_value=True))

    from app.models.scheduler import ScheduledMeetingJob
    now = datetime.now(timezone.utc)
    job = ScheduledMeetingJob(
        event_id="evt_mw_stop",
        subject="Hosted Media Meeting",
        start_time=now,
        end_time=now,
        status=MeetingStatus.COMPLETED,
        created_at=now,
        updated_at=now,
    )

    res = await dispatcher.trigger_phase5_pipeline(job)
    assert res is not None
    assert res.status == "completed"
    mock_get.assert_any_call("http://localhost:5050/api/media/recording/stop")
    mock_pipeline.process_end_to_end_job.assert_called_once_with(job=job, file_path_or_name="meeting_evt_mw_stop.wav", force_refresh=False)


@pytest.mark.asyncio
async def test_check_recording_readiness_immediately_available(mock_settings, tmp_path, monkeypatch):
    """Test that check_recording_readiness returns True immediately when local file exists and is non-empty."""
    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    test_file = tmp_path / "meeting_evt_ready_now.wav"
    test_file.write_bytes(b"RIFF_MOCK_WAV_HEADER_DATA_1234567890")

    monkeypatch.chdir(tmp_path)
    ready = await dispatcher.check_recording_readiness(event_id="evt_ready_now", max_attempts=3, delay_seconds=0.01)
    assert ready is True


@pytest.mark.asyncio
async def test_check_recording_readiness_delayed_availability(mock_settings, monkeypatch):
    """Test that check_recording_readiness retries and succeeds when file becomes available on second attempt."""
    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    call_count = 0

    def mock_exists(path):
        nonlocal call_count
        call_count += 1
        return call_count >= 2

    monkeypatch.setattr("os.path.exists", mock_exists)
    monkeypatch.setattr("os.path.isfile", lambda p: True)
    monkeypatch.setattr("os.path.getsize", lambda p: 1024)

    ready = await dispatcher.check_recording_readiness(event_id="evt_delayed", max_attempts=3, delay_seconds=0.01)
    assert ready is True
    assert call_count >= 2


@pytest.mark.asyncio
async def test_check_recording_readiness_never_available(mock_settings, monkeypatch):
    """Test that check_recording_readiness returns False after max_attempts if file is never ready."""
    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    monkeypatch.setattr("os.path.exists", lambda p: False)

    ready = await dispatcher.check_recording_readiness(event_id="evt_never", max_attempts=3, delay_seconds=0.01)
    assert ready is False


@pytest.mark.asyncio
async def test_phase5_pipeline_not_called_before_recording_readiness(mock_settings, monkeypatch):
    """Test that Phase5Pipeline process_end_to_end_job is NOT called before readiness check completes."""
    call_sequence = []

    async def mock_readiness(*args, **kwargs):
        call_sequence.append("readiness_check")
        return True

    mock_pipeline = AsyncMock()

    async def mock_process(*args, **kwargs):
        call_sequence.append("process_end_to_end_job")
        return MagicMock(status="completed", pipeline_id="p5_seq")

    mock_pipeline.process_end_to_end_job = mock_process

    dispatcher = MeetingDispatcher(pipeline=mock_pipeline, app_settings=mock_settings)
    monkeypatch.setattr(dispatcher, "check_recording_readiness", mock_readiness)

    from app.models.scheduler import ScheduledMeetingJob
    now = datetime.now(timezone.utc)
    job = ScheduledMeetingJob(
        event_id="evt_seq",
        subject="Sequence Test",
        start_time=now,
        end_time=now,
        status=MeetingStatus.COMPLETED,
        created_at=now,
        updated_at=now,
    )

    await dispatcher.trigger_phase5_pipeline(job, max_readiness_attempts=1, readiness_delay_seconds=0.01)

    assert call_sequence == ["readiness_check", "process_end_to_end_job"]


@pytest.mark.asyncio
async def test_media_worker_stop_failure_does_not_crash_meeting_completion(mock_settings, monkeypatch):
    """Test that failure of MediaWorker StopRecording endpoint does not crash meeting completion or pipeline."""
    mock_settings.use_app_hosted_media = True
    mock_settings.media_worker_url = "http://localhost:5050"

    mock_pipeline = AsyncMock()
    mock_pipeline.process_end_to_end_job = AsyncMock(
        return_value=MagicMock(status="completed", pipeline_id="p5_stop_fail")
    )

    # Mock httpx AsyncClient GET request to raise a ConnectionError
    mock_get = AsyncMock(side_effect=Exception("MediaWorker offline/unreachable"))
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

    dispatcher = MeetingDispatcher(pipeline=mock_pipeline, app_settings=mock_settings)
    monkeypatch.setattr(dispatcher, "check_recording_readiness", AsyncMock(return_value=True))

    from app.models.scheduler import ScheduledMeetingJob
    now = datetime.now(timezone.utc)
    job = ScheduledMeetingJob(
        event_id="evt_stop_fail",
        subject="Resilient Meeting",
        start_time=now,
        end_time=now,
        status=MeetingStatus.COMPLETED,
        created_at=now,
        updated_at=now,
    )

    res = await dispatcher.trigger_phase5_pipeline(job, max_readiness_attempts=1, readiness_delay_seconds=0.01)
    assert res is not None
    assert res.status == "completed"
    mock_pipeline.process_end_to_end_job.assert_called_once()


@pytest.mark.asyncio
async def test_duplicate_completion_does_not_create_duplicate_processing(mock_settings):
    """Test that re-evaluating an already COMPLETED meeting does not trigger duplicate pipeline tasks."""
    mock_pipeline = AsyncMock()
    mock_pipeline.process_end_to_end_job = AsyncMock(
        return_value=MagicMock(status="completed", pipeline_id="p5_dedup")
    )

    dispatcher = MeetingDispatcher(pipeline=mock_pipeline, app_settings=mock_settings)
    start_time = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2026, 9, 11, 12, 30, 0, tzinfo=timezone.utc)

    meeting = MeetingEvent(
        event_id="evt_dedup",
        subject="Duplicate Evaluation Test",
        start_time=start_time,
        end_time=end_time,
        is_teams_meeting=True,
    )

    # 1. Register & trigger meeting
    t1 = start_time - timedelta(seconds=30)
    dispatcher.register_or_update_meetings([meeting], now_utc=t1)
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=t1)

    # 2. First transition to COMPLETED -> spawns background task
    t2 = end_time + timedelta(minutes=5)
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=t2)
    assert dispatcher.get_job("evt_dedup").status == MeetingStatus.COMPLETED

    # 3. Re-evaluate multiple times while already COMPLETED
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=t2 + timedelta(minutes=10))
    await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=t2 + timedelta(minutes=20))

    await dispatcher.wait_for_active_pipelines()

    # Verify pipeline was called exactly ONCE despite multiple evaluate calls
    assert mock_pipeline.process_end_to_end_job.call_count == 1


