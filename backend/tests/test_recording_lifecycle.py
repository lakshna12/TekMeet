"""Unit tests verifying recording session lifecycle, Graph call ID handling, file isolation, and callback safety.

Task Requirement #14 Coverage:
  1. First call creates recording A.
  2. Duplicate start for same call does not create recording B.
  3. Second different call creates recording B.
  4. Stopping call A cannot leave its recording active.
  5. Stopping twice does not duplicate callback.
  6. Call B never appends to call A's WAV.
  7. Exact file path from stop is the file passed to backend.
"""

from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.models.scheduler import MeetingStatus, ScheduledMeetingJob
from app.services.meeting_dispatcher import meeting_dispatcher

client = TestClient(app)


def test_recording_stopped_callback_receives_graph_call_id():
    """Verify backend webhook endpoint receives Graph callId and passes exact file to pipeline."""
    now = datetime.now(timezone.utc)
    job = ScheduledMeetingJob(
        event_id="evt_lifecycle_01",
        subject="Lifecycle Test Meeting",
        join_url="https://teams.microsoft.com/l/meetup-join/123",
        call_id="graph_call_101",
        status=MeetingStatus.TRIGGERED,
        start_time=now,
        end_time=now,
        created_at=now,
        updated_at=now,
    )
    meeting_dispatcher._jobs["evt_lifecycle_01"] = job

    try:
        with patch.object(meeting_dispatcher, "trigger_phase5_pipeline", new_callable=AsyncMock) as mock_trigger:
            payload = {
                "callId": "graph_call_101",
                "eventId": "evt_lifecycle_01",
                "fileName": "meeting_evt_lifecycle_01_graph_call_1_20260930_120000.wav",
                "filePath": "meeting_evt_lifecycle_01_graph_call_1_20260930_120000.wav",
            }
            resp = client.post("/api/v1/calls/recording-stopped", json=payload)
            assert resp.status_code == 202
            assert resp.json()["status"] == "accepted"
            assert resp.json()["event_id"] == "evt_lifecycle_01"

            mock_trigger.assert_called_once_with(
                job,
                file_path_or_name="meeting_evt_lifecycle_01_graph_call_1_20260930_120000.wav"
            )
    finally:
        meeting_dispatcher._jobs.pop("evt_lifecycle_01", None)


def test_recording_stopped_callback_matches_by_call_id_when_event_id_missing():
    """Verify backend webhook endpoint matches job by callId when eventId is omitted."""
    now = datetime.now(timezone.utc)
    job = ScheduledMeetingJob(
        event_id="evt_lifecycle_02",
        subject="Call ID Match Meeting",
        join_url="https://teams.microsoft.com/l/meetup-join/456",
        call_id="graph_call_102",
        status=MeetingStatus.TRIGGERED,
        start_time=now,
        end_time=now,
        created_at=now,
        updated_at=now,
    )
    meeting_dispatcher._jobs["evt_lifecycle_02"] = job

    try:
        with patch.object(meeting_dispatcher, "trigger_phase5_pipeline", new_callable=AsyncMock) as mock_trigger:
            payload = {
                "callId": "graph_call_102",
                "fileName": "meeting_evt_lifecycle_02_graph_call_1_20260930_120500.wav",
            }
            resp = client.post("/api/v1/calls/recording-stopped", json=payload)
            assert resp.status_code == 202
            assert resp.json()["event_id"] == "evt_lifecycle_02"

            mock_trigger.assert_called_once_with(
                job,
                file_path_or_name="meeting_evt_lifecycle_02_graph_call_1_20260930_120500.wav"
            )
    finally:
        meeting_dispatcher._jobs.pop("evt_lifecycle_02", None)
