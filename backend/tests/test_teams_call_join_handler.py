"""Unit tests for TeamsCallJoinHandler — Phase 2."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.models.call import CallRecord, CallState
from app.models.meeting import MeetingEvent
from app.models.scheduler import MeetingStatus
from app.services.graph_calling_service import GraphCallingService
from app.services.meeting_dispatcher import MeetingDispatcher
from app.services.teams_call_join_handler import TeamsCallJoinHandler


@pytest.fixture
def mock_settings():
    return Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("mock_secret"),
        JOIN_BUFFER_SECONDS=60,
    )


TEAMS_JOIN_URL = (
    "https://teams.microsoft.com/l/meetup-join/"
    "19%3ameeting_abc%40thread.v2/0?context=%7b%7d"
)


@pytest.mark.asyncio
async def test_handle_join_success_sets_call_id(mock_settings):
    """Test that a successful join sets call_id on the ScheduledMeetingJob."""
    mock_calling_service = MagicMock(spec=GraphCallingService)
    mock_calling_service.join_meeting = AsyncMock(
        return_value=CallRecord(
            call_id="graph-call-xyz",
            event_id="evt-join-1",
            join_url=TEAMS_JOIN_URL,
            state=CallState.ESTABLISHING,
            started_at=datetime.now(timezone.utc),
        )
    )

    handler = TeamsCallJoinHandler(calling_service=mock_calling_service)
    now = datetime.now(timezone.utc)

    dispatcher = MeetingDispatcher(join_handler=handler, app_settings=mock_settings)
    meeting = MeetingEvent(
        event_id="evt-join-1",
        subject="Phase 2 Test Meeting",
        start_time=now + timedelta(seconds=30),
        end_time=now + timedelta(minutes=30),
        is_teams_meeting=True,
        join_url=TEAMS_JOIN_URL,
    )
    dispatcher.register_or_update_meetings([meeting], now_utc=now)
    dispatched = await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=now)

    assert len(dispatched) == 1
    job = dispatcher.get_job("evt-join-1")
    assert job is not None
    assert job.status == MeetingStatus.TRIGGERED
    assert job.call_id == "graph-call-xyz"


@pytest.mark.asyncio
async def test_handle_join_no_join_url():
    """Test that a meeting with no join URL returns False without calling Graph."""
    mock_calling_service = MagicMock(spec=GraphCallingService)
    mock_calling_service.join_meeting = AsyncMock()

    handler = TeamsCallJoinHandler(calling_service=mock_calling_service)

    from app.models.scheduler import ScheduledMeetingJob
    job = ScheduledMeetingJob(
        event_id="evt-no-url",
        subject="No URL Meeting",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc) + timedelta(hours=1),
        join_url=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = await handler.handle_join(job)

    assert result is False
    mock_calling_service.join_meeting.assert_not_called()


@pytest.mark.asyncio
async def test_handle_join_graph_failure_marks_job_failed(mock_settings):
    """Test that a failed Graph response causes dispatcher to mark the job FAILED."""
    mock_calling_service = MagicMock(spec=GraphCallingService)
    mock_calling_service.join_meeting = AsyncMock(
        return_value=CallRecord(
            call_id=None,
            event_id="evt-fail",
            join_url=TEAMS_JOIN_URL,
            state=CallState.FAILED,
            started_at=datetime.now(timezone.utc),
            error_code="Forbidden",
            error_message="Insufficient privileges.",
            http_status=403,
        )
    )

    handler = TeamsCallJoinHandler(calling_service=mock_calling_service)
    now = datetime.now(timezone.utc)

    dispatcher = MeetingDispatcher(join_handler=handler, app_settings=mock_settings)
    meeting = MeetingEvent(
        event_id="evt-fail",
        subject="Forbidden Meeting",
        start_time=now + timedelta(seconds=10),
        end_time=now + timedelta(minutes=30),
        is_teams_meeting=True,
        join_url=TEAMS_JOIN_URL,
    )
    dispatcher.register_or_update_meetings([meeting], now_utc=now)
    dispatched = await dispatcher.evaluate_and_dispatch_due_meetings(now_utc=now)

    assert len(dispatched) == 0
    job = dispatcher.get_job("evt-fail")
    assert job is not None
    assert job.status == MeetingStatus.FAILED
    assert job.call_id is None
