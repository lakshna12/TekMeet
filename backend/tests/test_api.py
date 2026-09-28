"""Unit tests for FastAPI endpoints."""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth.entra_auth import TokenVerificationResult

client = TestClient(app)


def test_root_endpoint():
    """Test health check root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "TekMeet Backend"
    assert data["status"] == "online"
    assert "auth_configured" in data


def test_auth_status_endpoint():
    """Test auth status endpoint structure."""
    response = client.get("/api/v1/auth/status")
    assert response.status_code == 200
    data = response.json()
    assert "is_configured" in data
    assert "missing_variables" in data
    assert "authority" in data
    assert "graph_scopes" in data


def test_verify_auth_endpoint_mocked():
    """Test verify endpoint returns proper verification schema without token leaks."""
    mock_result = TokenVerificationResult(
        success=True,
        token_type="Bearer",
        expires_in=3599,
        scopes=["https://graph.microsoft.com/.default"],
        tenant_id_masked="1234****5678",
        client_id_masked="abcd****efgh",
        authority="https://login.microsoftonline.com/1234-5678",
        message="Authentication succeeded",
    )

    with patch("app.api.auth.entra_auth_service.verify_authentication", return_value=mock_result):
        response = client.get("/api/v1/auth/verify")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] == 3599
        assert "access_token" not in data


def test_upcoming_meetings_endpoint_mocked():
    """Test /api/v1/meetings/upcoming endpoint with mocked watcher."""
    from datetime import datetime, timezone
    from app.models.meeting import MeetingEvent, UpcomingMeetingsResponse

    mock_resp = UpcomingMeetingsResponse(
        success=True,
        target_user="bot@contoso.com",
        window_start_utc=datetime.now(timezone.utc),
        window_end_utc=datetime.now(timezone.utc),
        total_events_retrieved=1,
        teams_meetings_count=1,
        meetings=[
            MeetingEvent(
                event_id="evt_test_1",
                subject="Test Teams Sync",
                is_teams_meeting=True,
                join_url="https://teams.microsoft.com/meet/999",
            )
        ],
    )

    with patch("app.api.meetings.meeting_watcher.poll_upcoming_teams_meetings", return_value=mock_resp):
        response = client.get("/api/v1/meetings/upcoming?user_email=bot@contoso.com")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["teams_meetings_count"] == 1
        assert data["meetings"][0]["event_id"] == "evt_test_1"
        assert data["meetings"][0]["join_url"] == "https://teams.microsoft.com/meet/999"


def test_calendar_events_endpoint_mocked():
    """Test /api/v1/meetings/calendar-events endpoint with mocked calendar_service."""
    from app.models.meeting import MeetingEvent

    mock_events = [
        MeetingEvent(
            event_id="evt_cal_1",
            subject="General Sync",
            is_teams_meeting=False,
        )
    ]

    with patch("app.api.meetings.calendar_service.get_calendar_events", return_value=mock_events):
        response = client.get("/api/v1/meetings/calendar-events?user_email=bot@contoso.com")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["event_id"] == "evt_cal_1"


def test_scheduler_status_endpoint():
    """Test GET /api/v1/scheduler/status endpoint."""
    response = client.get("/api/v1/scheduler/status")
    assert response.status_code == 200
    data = response.json()
    assert "is_running" in data
    assert "poll_interval_seconds" in data
    assert "join_buffer_seconds" in data
    assert "total_tracked_jobs" in data


def test_scheduler_jobs_endpoint():
    """Test GET /api/v1/scheduler/jobs endpoint."""
    response = client.get("/api/v1/scheduler/jobs")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_scheduler_start_stop_endpoints():
    """Test POST /api/v1/scheduler/start and /stop endpoints."""
    # Test start
    resp_start = client.post("/api/v1/scheduler/start")
    assert resp_start.status_code == 200
    assert resp_start.json()["is_running"] is True

    # Test stop
    resp_stop = client.post("/api/v1/scheduler/stop")
    assert resp_stop.status_code == 200
    assert resp_stop.json()["is_running"] is False


def test_scheduler_poll_now_endpoint():
    """Test POST /api/v1/scheduler/poll-now endpoint with mock."""
    from datetime import datetime, timezone
    from app.models.scheduler import PollCycleResult

    mock_res = PollCycleResult(
        success=True,
        polled_at_utc=datetime.now(timezone.utc),
        events_found=2,
        teams_meetings_found=1,
        newly_scheduled=1,
        newly_dispatched=0,
        message="Poll OK",
    )

    with patch("app.api.scheduler.meeting_scheduler.poll_and_dispatch_cycle", return_value=mock_res):
        response = client.post("/api/v1/scheduler/poll-now?target_user=bot@contoso.com")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["teams_meetings_found"] == 1


