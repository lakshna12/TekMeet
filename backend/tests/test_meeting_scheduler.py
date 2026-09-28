"""Unit tests for MeetingSchedulerService."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.models.meeting import MeetingEvent, UpcomingMeetingsResponse
from app.models.scheduler import MeetingStatus
from app.services.meeting_dispatcher import MeetingDispatcher
from app.services.meeting_scheduler import MeetingSchedulerService
from app.services.meeting_watcher import MeetingWatcher


@pytest.fixture
def mock_settings():
    return Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("mock_secret"),
        AZURE_BOT_USER_EMAIL="bot@contoso.com",
        POLL_INTERVAL_SECONDS=10,
        JOIN_BUFFER_SECONDS=60,
    )


@pytest.mark.asyncio
async def test_poll_and_dispatch_cycle_success(mock_settings):
    """Test a full poll and dispatch cycle with MeetingSchedulerService."""
    now = datetime(2026, 9, 11, 14, 0, 0, tzinfo=timezone.utc)

    mock_watcher = MagicMock(spec=MeetingWatcher)
    mock_watcher.poll_upcoming_teams_meetings = AsyncMock(
        return_value=UpcomingMeetingsResponse(
            success=True,
            target_user="bot@contoso.com",
            window_start_utc=now,
            window_end_utc=now + timedelta(hours=24),
            total_events_retrieved=2,
            teams_meetings_count=2,
            meetings=[
                MeetingEvent(
                    event_id="evt_due_1",
                    subject="Due Meeting",
                    start_time=now + timedelta(seconds=20),
                    end_time=now + timedelta(minutes=30),
                    is_teams_meeting=True,
                ),
                MeetingEvent(
                    event_id="evt_later_1",
                    subject="Later Meeting",
                    start_time=now + timedelta(hours=2),
                    end_time=now + timedelta(hours=3),
                    is_teams_meeting=True,
                ),
            ],
        )
    )

    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    scheduler = MeetingSchedulerService(watcher=mock_watcher, dispatcher=dispatcher, app_settings=mock_settings)

    # Run cycle
    result = await scheduler.poll_and_dispatch_cycle(now_utc=now)

    assert result.success is True
    assert result.events_found == 2
    assert result.teams_meetings_found == 2
    assert result.newly_scheduled == 2
    assert result.newly_dispatched == 1
    assert result.dispatched_meeting_ids == ["evt_due_1"]

    # Check status response
    status = scheduler.get_status()
    assert status.total_tracked_jobs == 2
    assert status.scheduled_jobs_count == 1
    assert status.triggered_jobs_count == 1
    assert status.completed_jobs_count == 0


@pytest.mark.asyncio
async def test_poll_and_dispatch_cycle_watcher_error(mock_settings):
    """Test scheduler handling when watcher returns an error response."""
    now = datetime.now(timezone.utc)
    mock_watcher = MagicMock(spec=MeetingWatcher)
    mock_watcher.poll_upcoming_teams_meetings = AsyncMock(
        return_value=UpcomingMeetingsResponse(
            success=False,
            window_start_utc=now,
            window_end_utc=now,
            total_events_retrieved=0,
            teams_meetings_count=0,
            meetings=[],
            error="Microsoft Graph 403 Forbidden",
        )
    )

    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    scheduler = MeetingSchedulerService(watcher=mock_watcher, dispatcher=dispatcher, app_settings=mock_settings)

    result = await scheduler.poll_and_dispatch_cycle(now_utc=now)
    assert result.success is False
    assert "Microsoft Graph 403 Forbidden" in result.message


@pytest.mark.asyncio
async def test_scheduler_start_stop_lifecycle(mock_settings):
    """Test starting and stopping the scheduler background loop."""
    mock_watcher = MagicMock(spec=MeetingWatcher)
    dispatcher = MeetingDispatcher(app_settings=mock_settings)
    scheduler = MeetingSchedulerService(watcher=mock_watcher, dispatcher=dispatcher, app_settings=mock_settings)

    assert scheduler.is_running is False

    started = scheduler.start()
    assert started is True
    assert scheduler.is_running is True

    # Starting again should return False (already running)
    assert scheduler.start() is False

    stopped = scheduler.stop()
    assert stopped is True
    assert scheduler.is_running is False
