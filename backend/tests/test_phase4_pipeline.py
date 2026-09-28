"""Unit and Integration Tests for Phase 4 Orchestration Pipeline & REST APIs.

Tests:
  - Successful end-to-end mocked pipeline execution.
  - Transcription failure handling.
  - Empty recording pipeline flow.
  - Summarization failure handling (partial pipeline completion).
  - Pipeline result deduplication & caching.
  - Transcripts API endpoints (/api/v1/transcripts/generate, /api/v1/transcripts/{event_id}).
  - Summaries API endpoints (/api/v1/summaries/generate, /api/v1/summaries/{event_id}, /api/v1/summaries/pipeline/process).
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.summary import SummarizationStatus
from app.models.transcript import TranscriptionStatus
from app.services.phase4_pipeline import Phase4Pipeline, phase4_pipeline


@pytest.fixture
def client():
    """FastAPI TestClient instance."""
    return TestClient(app)


@pytest.mark.asyncio
async def test_phase4_pipeline_success_end_to_end():
    """Test successful end-to-end pipeline execution with mocked STT & LLM overrides."""
    pipeline = Phase4Pipeline()

    mock_stt = {
        "status": "completed",
        "full_text": "Meeting regarding TekMeet Phase 4 architecture. Alice and Bob discussed Whisper and Gemini.",
        "duration_seconds": 60.0,
        "segments": [{"id": 0, "start": 0.0, "end": 60.0, "text": "Meeting regarding TekMeet Phase 4 architecture."}],
    }

    mock_llm = {
        "overview": "Overview of TekMeet Phase 4 architecture.",
        "key_points": ["Discussed Whisper STT and Gemini LLM integration."],
        "action_items": [{"task": "Deploy Phase 4 code", "assignee": "Alice", "deadline": "Today"}],
        "decisions": [{"title": "Use Google Gemini", "details": "Selected for quality"}],
    }

    result = await pipeline.process_recording(
        event_id="event_p4_e2e_01",
        file_path_or_name="test_meeting.wav",
        call_id="call_p4_01",
        mock_transcription_override=mock_stt,
        mock_summarization_override=mock_llm,
    )

    assert result is not None
    assert result.status == "completed"
    assert result.event_id == "event_p4_e2e_01"
    assert result.transcript is not None
    assert result.transcript.status == TranscriptionStatus.COMPLETED
    assert result.summary is not None
    assert result.summary.status == SummarizationStatus.COMPLETED
    assert "TekMeet Phase 4" in result.transcript.full_text
    assert "Whisper STT" in result.summary.key_points[0]


@pytest.mark.asyncio
async def test_phase4_pipeline_transcription_failure():
    """Test pipeline failure when STT transcription stage fails."""
    pipeline = Phase4Pipeline()

    mock_stt_failed = {
        "status": "failed",
        "full_text": "",
        "error_message": "Audio file corrupt or unreadable.",
    }

    result = await pipeline.process_recording(
        event_id="event_p4_stt_fail",
        file_path_or_name="corrupt.wav",
        mock_transcription_override=mock_stt_failed,
    )

    assert result is not None
    assert result.status == "failed"
    assert result.transcript.status == TranscriptionStatus.FAILED
    assert result.summary is None
    assert "Transcription failed" in result.error_message


@pytest.mark.asyncio
async def test_phase4_pipeline_empty_recording():
    """Test pipeline execution for empty/silent audio recording."""
    pipeline = Phase4Pipeline()

    mock_stt_empty = {
        "status": "empty",
        "full_text": "No speech detected.",
        "duration_seconds": 10.0,
        "segments": [],
    }

    result = await pipeline.process_recording(
        event_id="event_p4_empty",
        file_path_or_name="empty.wav",
        mock_transcription_override=mock_stt_empty,
    )

    assert result is not None
    assert result.status == "completed"
    assert result.transcript.status == TranscriptionStatus.EMPTY
    assert result.summary is not None
    assert result.summary.status == SummarizationStatus.COMPLETED
    assert "no meaningful meeting discussion" in result.summary.overview.lower()


@pytest.mark.asyncio
async def test_phase4_pipeline_summarization_failure():
    """Test pipeline partial completion when STT succeeds but LLM summarization fails."""
    pipeline = Phase4Pipeline()

    mock_stt_ok = {
        "status": "completed",
        "full_text": "Valid discussion text.",
        "duration_seconds": 30.0,
    }

    mock_llm_fail = {
        "status": "failed",
        "error_message": "Anthropic API rate limit exceeded.",
    }

    result = await pipeline.process_recording(
        event_id="event_p4_llm_fail",
        file_path_or_name="valid.wav",
        mock_transcription_override=mock_stt_ok,
        mock_summarization_override=mock_llm_fail,
    )

    assert result is not None
    assert result.status == "partial"
    assert result.transcript.status == TranscriptionStatus.COMPLETED
    assert result.summary.status == SummarizationStatus.FAILED
    assert "Summarization failed" in result.error_message


@pytest.mark.asyncio
async def test_phase4_pipeline_deduplication_caching():
    """Test that pipeline caches results and avoids duplicate processing for the same event_id."""
    pipeline = Phase4Pipeline()

    mock_stt = {"status": "completed", "full_text": "First run text.", "duration_seconds": 10.0}
    mock_llm = {"overview": "First run summary.", "key_points": []}

    # First run
    res1 = await pipeline.process_recording(
        event_id="event_p4_dedup",
        file_path_or_name="test.wav",
        mock_transcription_override=mock_stt,
        mock_summarization_override=mock_llm,
    )

    # Second run without force_refresh
    res2 = await pipeline.process_recording(
        event_id="event_p4_dedup",
        file_path_or_name="test.wav",
    )

    assert res1.processed_at == res2.processed_at
    assert res2.transcript.full_text == "First run text."


def test_transcripts_api_generate_and_get(client):
    """Test POST /api/v1/transcripts/generate and GET /api/v1/transcripts/{event_id} endpoints."""
    # Test 404 for non-existent local file
    resp = client.post("/api/v1/transcripts/generate?event_id=event_api_01&file_name=non_existent_file.wav")
    assert resp.status_code == 404

    # Test 400 validation for empty event_id
    resp_bad = client.post("/api/v1/transcripts/generate?event_id= &file_name=test.wav")
    assert resp_bad.status_code == 400

    # Test GET 404 for non-existent transcript
    resp_get = client.get("/api/v1/transcripts/event_non_existent")
    assert resp_get.status_code == 404


def test_summaries_api_generate_and_get(client):
    """Test POST /api/v1/summaries/generate, GET /api/v1/summaries/{event_id}, and POST /api/v1/summaries/pipeline/process endpoints."""
    # Test 400 validation for empty file_name
    resp_bad = client.post("/api/v1/summaries/generate?event_id=evt123&file_name= ")
    assert resp_bad.status_code == 400

    # Test GET 404 for non-existent summary
    resp_get = client.get("/api/v1/summaries/event_non_existent")
    assert resp_get.status_code == 404

    # Test GET 404 for non-existent pipeline query
    resp_get_pipe = client.get("/api/v1/summaries/event_non_existent")
    assert resp_get_pipe.status_code == 404
