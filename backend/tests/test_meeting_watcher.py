"""Unit tests for MeetingWatcher service."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.core.config import Settings
from app.models.meeting import MeetingEvent
from app.services.calendar_service import CalendarService, CalendarServiceError
from app.services.meeting_watcher import MeetingWatcher


@pytest.fixture
def mock_calendar_service():
    return MagicMock(spec=CalendarService)


@pytest.fixture
def sample_events():
    now = datetime.now(timezone.utc)
    return [
        MeetingEvent(
            event_id="evt_teams_later",
            subject="Architecture Review",
            start_time=now + timedelta(hours=3),
            end_time=now + timedelta(hours=4),
            is_teams_meeting=True,
            is_cancelled=False,
            join_url="https://teams.microsoft.com/meet/123",
        ),
        MeetingEvent(
            event_id="evt_teams_soon",
            subject="Daily Standup",
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=1, minutes=30),
            is_teams_meeting=True,
            is_cancelled=False,
            join_url="https://teams.microsoft.com/meet/456",
        ),
        MeetingEvent(
            event_id="evt_teams_cancelled",
            subject="Cancelled Sync",
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(hours=2, minutes=30),
            is_teams_meeting=True,
            is_cancelled=True,
        ),
        MeetingEvent(
            event_id="evt_in_person",
            subject="Lunch Meeting",
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(hours=3),
            is_teams_meeting=False,
            is_cancelled=False,
        ),
    ]


@pytest.mark.asyncio
async def test_meeting_watcher_filters_and_sorts(mock_calendar_service, sample_events):
    """Test MeetingWatcher filters out cancelled/non-Teams meetings and sorts by start_time."""
    mock_calendar_service.get_calendar_events = AsyncMock(return_value=sample_events)

    watcher = MeetingWatcher(cal_service=mock_calendar_service)
    response = await watcher.poll_upcoming_teams_meetings(user_id_or_email="bot@contoso.com")

    assert response.success is True
    assert response.total_events_retrieved == 4
    assert response.teams_meetings_count == 2
    assert len(response.meetings) == 2

    # Verify order: evt_teams_soon (1h later) should be before evt_teams_later (3h later)
    assert response.meetings[0].event_id == "evt_teams_soon"
    assert response.meetings[1].event_id == "evt_teams_later"


@pytest.mark.asyncio
async def test_meeting_watcher_deduplication(mock_calendar_service, sample_events):
    """Test deduplication when only_unseen=True."""
    mock_calendar_service.get_calendar_events = AsyncMock(return_value=sample_events)

    watcher = MeetingWatcher(cal_service=mock_calendar_service)

    # First poll
    res1 = await watcher.poll_upcoming_teams_meetings(user_id_or_email="bot@contoso.com")
    assert len(res1.meetings) == 2

    # Mark first meeting as seen
    watcher.mark_event_seen("evt_teams_soon")
    assert watcher.is_event_seen("evt_teams_soon") is True

    # Second poll with only_unseen=True
    res2 = await watcher.poll_upcoming_teams_meetings(
        user_id_or_email="bot@contoso.com",
        only_unseen=True,
    )
    assert len(res2.meetings) == 1
    assert res2.meetings[0].event_id == "evt_teams_later"

    # Clear cache
    watcher.clear_seen_cache()
    assert watcher.is_event_seen("evt_teams_soon") is False


@pytest.mark.asyncio
async def test_meeting_watcher_calendar_error_handled(mock_calendar_service):
    """Test that CalendarService errors are caught and surfaced cleanly."""
    mock_calendar_service.get_calendar_events = AsyncMock(
        side_effect=CalendarServiceError("Graph access token expired or denied", status_code=401)
    )

    watcher = MeetingWatcher(cal_service=mock_calendar_service)
    response = await watcher.poll_upcoming_teams_meetings(user_id_or_email="bot@contoso.com")

    assert response.success is False
    assert response.teams_meetings_count == 0
    assert "Graph access token expired or denied" in response.error
