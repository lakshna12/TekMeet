"""Unit and integration tests for Meetings API router (Phase 5 Step 5)."""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.delivery import DeliveryProvider, DeliveryRecord, DeliveryStatus
from app.models.scheduler import MeetingStatus, ScheduledMeetingJob
from app.models.summary import ActionItem, Decision, MeetingSummary, SummarizationStatus
from app.models.transcript import Transcript, TranscriptSegment, TranscriptionStatus
from app.services.meeting_dispatcher import meeting_dispatcher
from app.services.storage_service import StorageService

client = TestClient(app)


@pytest.fixture
def mock_storage(tmp_path):
    """Fixture providing a clean isolated StorageService instance for tests."""
    srv = StorageService(data_dir=tmp_path)
    return srv


@pytest.mark.asyncio
async def test_get_complete_past_meeting_summary(monkeypatch, mock_storage):
    """Test retrieving a complete past meeting summary containing all components."""
    event_id = "evt_full_summary_123"
    now = datetime.now(timezone.utc)

    # 1. Populate mock storage
    t = Transcript(
        transcript_id="tr_101",
        event_id=event_id,
        recording_file="meeting_evt_full_summary_123.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text="Welcome everyone to the quarterly review.",
        segments=[TranscriptSegment(id=0, start=0.0, end=5.0, text="Welcome everyone.")],
    )
    s = MeetingSummary(
        summary_id="sum_101",
        event_id=event_id,
        transcript_id="tr_101",
        status=SummarizationStatus.COMPLETED,
        overview="Quarterly review covered revenue growth.",
        key_points=["Revenue up 15%", "Hired 5 engineers"],
        action_items=[ActionItem(task="Send Q3 report", assignee="Alice", deadline="Friday")],
        decisions=[Decision(title="Approved Q4 Budget", details="Allocated 100k")],
    )
    d = DeliveryRecord(
        delivery_id="del_101",
        event_id=event_id,
        recipient_email="organizer@contoso.com",
        provider=DeliveryProvider.GRAPH,
        status=DeliveryStatus.SENT,
        retry_count=0,
        sent_at=now,
    )
    mock_storage.save_transcript(t)
    mock_storage.save_summary(s)
    mock_storage.save_delivery_record(d)

    # Monkeypatch storage_service in API router
    monkeypatch.setattr("app.api.meetings.storage_service", mock_storage)

    # 2. Call API
    response = client.get(f"/api/v1/meetings/{event_id}/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["event_id"] == event_id

    # Check components
    assert data["transcript"]["full_text"] == "Welcome everyone to the quarterly review."
    assert data["summary"]["overview"] == "Quarterly review covered revenue growth."
    assert len(data["summary"]["key_points"]) == 2
    assert len(data["summary"]["action_items"]) == 1
    assert data["summary"]["action_items"][0]["assignee"] == "Alice"
    assert len(data["summary"]["decisions"]) == 1
    assert data["recording"]["file_name"] == "meeting_evt_full_summary_123.wav"
    assert "/api/media/recordings/download?file=meeting_evt_full_summary_123.wav" in data["recording"]["download_url"]
    assert data["delivery"]["status"] == "sent"
    assert data["delivery"]["recipient_email"] == "organizer@contoso.com"


@pytest.mark.asyncio
async def test_missing_transcript_handling(monkeypatch, mock_storage):
    """Test retrieving summary view when transcript is missing (e.g. summary available, transcript absent)."""
    event_id = "evt_no_transcript"

    s = MeetingSummary(
        summary_id="sum_202",
        event_id=event_id,
        status=SummarizationStatus.COMPLETED,
        overview="Summary generated without persisted transcript.",
    )
    mock_storage.save_summary(s)
    monkeypatch.setattr("app.api.meetings.storage_service", mock_storage)

    response = client.get(f"/api/v1/meetings/{event_id}/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["event_id"] == event_id
    assert data["transcript"] is None
    assert data["summary"]["overview"] == "Summary generated without persisted transcript."
    assert data["delivery"] is None


@pytest.mark.asyncio
async def test_missing_summary_handling(monkeypatch, mock_storage):
    """Test retrieving summary view when AI summary is missing."""
    event_id = "evt_no_summary"

    t = Transcript(
        transcript_id="tr_303",
        event_id=event_id,
        recording_file="rec.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text="Raw audio text only.",
    )
    mock_storage.save_transcript(t)
    monkeypatch.setattr("app.api.meetings.storage_service", mock_storage)

    response = client.get(f"/api/v1/meetings/{event_id}/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["transcript"]["full_text"] == "Raw audio text only."
    assert data["summary"] is None


@pytest.mark.asyncio
async def test_missing_delivery_record_handling(monkeypatch, mock_storage):
    """Test retrieving summary view when email delivery record is missing."""
    event_id = "evt_no_delivery"

    s = MeetingSummary(
        summary_id="sum_404",
        event_id=event_id,
        overview="Meeting overview.",
    )
    mock_storage.save_summary(s)
    monkeypatch.setattr("app.api.meetings.storage_service", mock_storage)

    response = client.get(f"/api/v1/meetings/{event_id}/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["delivery"] is None


@pytest.mark.asyncio
async def test_unknown_event_id_returns_404(monkeypatch, mock_storage):
    """Test querying a non-existent event ID returns 404 Not Found."""
    monkeypatch.setattr("app.api.meetings.storage_service", mock_storage)

    response = client.get("/api/v1/meetings/evt_non_existent_999/summary")
    assert response.status_code == 404
    data = response.json()
    assert "No meeting found" in data["detail"]


@pytest.mark.asyncio
async def test_list_past_meetings(monkeypatch, mock_storage):
    """Test listing all past meetings from storage and dispatcher."""
    # Populate multiple distinct events
    s1 = MeetingSummary(summary_id="sum_a", event_id="evt_alpha", overview="Alpha Overview")
    s2 = MeetingSummary(summary_id="sum_b", event_id="evt_beta", overview="Beta Overview")

    mock_storage.save_summary(s1)
    mock_storage.save_summary(s2)

    monkeypatch.setattr("app.api.meetings.storage_service", mock_storage)

    response = client.get("/api/v1/meetings")
    assert response.status_code == 200

    data = response.json()
    assert len(data) >= 2
    event_ids = [item["event_id"] for item in data]
    assert "evt_alpha" in event_ids
    assert "evt_beta" in event_ids


@pytest.mark.asyncio
async def test_multiple_event_ids_isolation(monkeypatch, mock_storage):
    """Test that data from multiple event IDs remain strictly isolated."""
    s1 = MeetingSummary(summary_id="sum_iso1", event_id="evt_iso1", overview="Iso 1 overview")
    s2 = MeetingSummary(summary_id="sum_iso2", event_id="evt_iso2", overview="Iso 2 overview")

    mock_storage.save_summary(s1)
    mock_storage.save_summary(s2)
    monkeypatch.setattr("app.api.meetings.storage_service", mock_storage)

    resp1 = client.get("/api/v1/meetings/evt_iso1/summary")
    resp2 = client.get("/api/v1/meetings/evt_iso2/summary")

    assert resp1.json()["summary"]["overview"] == "Iso 1 overview"
    assert resp2.json()["summary"]["overview"] == "Iso 2 overview"


@pytest.mark.asyncio
async def test_no_secrets_exposed_in_api_response(monkeypatch, mock_storage):
    """Test that secrets (API keys, secrets, authorization headers) are not exposed in responses."""
    s = MeetingSummary(summary_id="sum_sec", event_id="evt_sec", overview="Confidential test overview")
    mock_storage.save_summary(s)
    monkeypatch.setattr("app.api.meetings.storage_service", mock_storage)

    response = client.get("/api/v1/meetings/evt_sec/summary")
    assert response.status_code == 200
    content = response.text.lower()

    for forbidden in ["client_secret", "api_key", "password", "access_token", "bearer_token"]:
        assert forbidden not in content
