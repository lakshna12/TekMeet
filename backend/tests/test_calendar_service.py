"""Unit tests for Microsoft Graph Calendar Service."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import SecretStr

from app.auth.entra_auth import EntraAuthService
from app.core.config import Settings
from app.services.calendar_service import CalendarService, CalendarServiceError


@pytest.fixture
def mock_settings():
    return Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("mock_secret"),
        AZURE_BOT_USER_EMAIL="bot@contoso.onmicrosoft.com",
        MEETING_LOOKAHEAD_MINUTES=1440,
    )


@pytest.fixture
def calendar_service_instance(mock_settings):
    auth_service = MagicMock(spec=EntraAuthService)
    auth_service.get_access_token.return_value = "mock_token"
    return CalendarService(auth_service=auth_service, app_settings=mock_settings)


def test_parse_teams_for_business_event():
    """Test parsing an official Teams for Business online meeting."""
    raw_event = {
        "id": "event_12345",
        "subject": "Weekly Team Sync",
        "start": {"dateTime": "2026-09-11T16:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-09-11T17:00:00.0000000", "timeZone": "UTC"},
        "organizer": {
            "emailAddress": {"name": "Alice Organizer", "address": "alice@contoso.com"}
        },
        "attendees": [
            {
                "type": "required",
                "status": {"response": "accepted"},
                "emailAddress": {"name": "TekMeet Bot", "address": "bot@contoso.onmicrosoft.com"},
            }
        ],
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
        "onlineMeeting": {
            "joinUrl": "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc123/0",
            "conferenceId": "123456789",
            "id": "meeting_thread_id_999",
        },
        "isCancelled": False,
    }

    event = CalendarService.parse_graph_event(raw_event)
    assert event.event_id == "event_12345"
    assert event.subject == "Weekly Team Sync"
    assert event.organizer_email == "alice@contoso.com"
    assert event.organizer_name == "Alice Organizer"
    assert len(event.attendees) == 1
    assert event.attendees[0].email == "bot@contoso.onmicrosoft.com"
    assert event.is_online_meeting is True
    assert event.online_meeting_provider == "teamsForBusiness"
    assert event.is_teams_meeting is True
    assert event.join_url == "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc123/0"
    assert event.conference_id == "123456789"
    assert event.meeting_id == "meeting_thread_id_999"
    assert event.start_time == datetime(2026, 9, 11, 16, 0, 0, tzinfo=timezone.utc)
    assert event.end_time == datetime(2026, 9, 11, 17, 0, 0, tzinfo=timezone.utc)


def test_parse_teams_from_join_url():
    """Test detecting Teams meeting when onlineMeetingProvider is missing but joinUrl has Teams domain."""
    raw_event = {
        "id": "event_join_url",
        "subject": "Project Standup",
        "start": {"dateTime": "2026-09-11T18:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-09-11T18:30:00Z", "timeZone": "UTC"},
        "isOnlineMeeting": True,
        "onlineMeetingUrl": "https://teams.microsoft.com/meet/555123",
    }

    event = CalendarService.parse_graph_event(raw_event)
    assert event.is_teams_meeting is True
    assert event.join_url == "https://teams.microsoft.com/meet/555123"


def test_parse_non_teams_meeting():
    """Test that non-Teams events (in-person or external like Zoom) are not marked as Teams meetings."""
    # In-person meeting
    in_person_event = {
        "id": "event_in_person",
        "subject": "Coffee Catchup",
        "start": {"dateTime": "2026-09-11T10:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-09-11T10:30:00Z", "timeZone": "UTC"},
        "isOnlineMeeting": False,
        "location": {"displayName": "Cafeteria 3rd Floor"},
    }
    event1 = CalendarService.parse_graph_event(in_person_event)
    assert event1.is_teams_meeting is False
    assert event1.is_online_meeting is False
    assert event1.location == "Cafeteria 3rd Floor"

    # Zoom meeting
    zoom_event = {
        "id": "event_zoom",
        "subject": "Client Call",
        "start": {"dateTime": "2026-09-11T11:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-09-11T11:30:00Z", "timeZone": "UTC"},
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "zoom",
        "onlineMeetingUrl": "https://zoom.us/j/123456789",
    }
    event2 = CalendarService.parse_graph_event(zoom_event)
    assert event2.is_teams_meeting is False
    assert event2.online_meeting_provider == "zoom"


def test_parse_sparse_or_malformed_event():
    """Test handling of sparse/empty event payload without exceptions."""
    raw_event = {}
    event = CalendarService.parse_graph_event(raw_event)
    assert event.event_id == "unknown_event_id"
    assert event.subject == "Untitled Meeting"
    assert event.start_time is None
    assert event.end_time is None
    assert event.is_teams_meeting is False
    assert event.attendees == []


@pytest.mark.asyncio
async def test_get_calendar_events_missing_user_error():
    """Test error raised when no user email is provided and config has none."""
    empty_settings = Settings(
        AZURE_TENANT_ID="111",
        AZURE_CLIENT_ID="222",
        AZURE_CLIENT_SECRET=SecretStr("333"),
        AZURE_BOT_USER_EMAIL=None,
    )
    service = CalendarService(app_settings=empty_settings)
    with pytest.raises(CalendarServiceError) as exc_info:
        await service.get_calendar_events()
    assert "Target mailbox / user principal name is not specified" in exc_info.value.message


@pytest.mark.asyncio
async def test_get_calendar_events_with_pagination(calendar_service_instance):
    """Test fetching calendar events with @odata.nextLink pagination."""
    page_1_data = {
        "value": [
            {
                "id": "evt_1",
                "subject": "Meeting 1",
                "isOnlineMeeting": True,
                "onlineMeetingProvider": "teamsForBusiness",
                "start": {"dateTime": "2026-09-11T12:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-09-11T13:00:00Z", "timeZone": "UTC"},
            }
        ],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/users/bot@contoso.onmicrosoft.com/calendarView?skip=1",
    }
    page_2_data = {
        "value": [
            {
                "id": "evt_2",
                "subject": "Meeting 2",
                "isOnlineMeeting": True,
                "onlineMeetingProvider": "teamsForBusiness",
                "start": {"dateTime": "2026-09-11T14:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-09-11T15:00:00Z", "timeZone": "UTC"},
            }
        ],
    }

    mock_resp_1 = MagicMock()
    mock_resp_1.status_code = 200
    mock_resp_1.json.return_value = page_1_data

    mock_resp_2 = MagicMock()
    mock_resp_2.status_code = 200
    mock_resp_2.json.return_value = page_2_data

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [mock_resp_1, mock_resp_2]

        events = await calendar_service_instance.get_calendar_events(
            user_id_or_email="bot@contoso.onmicrosoft.com"
        )

        assert len(events) == 2
        assert events[0].event_id == "evt_1"
        assert events[1].event_id == "evt_2"
        assert mock_get.call_count == 2


@pytest.mark.asyncio
async def test_get_calendar_events_http_error(calendar_service_instance):
    """Test handling of 403 Forbidden Graph API error."""
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"
    mock_resp.json.return_value = {
        "error": {
            "code": "ErrorAccessDenied",
            "message": "Access is denied. Check credentials and permissions.",
        }
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp

        with pytest.raises(CalendarServiceError) as exc_info:
            await calendar_service_instance.get_calendar_events(
                user_id_or_email="bot@contoso.onmicrosoft.com"
            )

        assert exc_info.value.status_code == 403
        assert "ErrorAccessDenied" in exc_info.value.message
