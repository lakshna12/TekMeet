"""Unit and Integration Tests for Phase 4 TranscriptionService (Deepgram STT).

Maps to Test Cases:
  - TC #14: Transcription of a saved recording with clear audio (Valid recording -> Transcript model).
  - TC #15: Handling of invalid/empty/silent/missing recordings (Graceful degradation, no crashes).
  - Deepgram STT REST API response parsing & error handling.
"""

import os
import tempfile
import wave
import struct
import pytest
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.models.transcript import TranscriptionStatus
from app.services.transcription_service import TranscriptionService, transcription_service


def create_synthetic_wav_file(file_path: str, duration_seconds: float = 2.0, sample_rate: int = 16000, has_audio: bool = True):
    """Generate a valid WAV audio file for testing."""
    num_samples = int(duration_seconds * sample_rate)
    with wave.open(file_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        if has_audio:
            # Generate simple 440Hz sine wave tone
            import math
            samples = [int(10000 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(num_samples)]
            pcm_bytes = struct.pack(f"<{len(samples)}h", *samples)
        else:
            # Silence (zeros)
            pcm_bytes = b"\x00" * (num_samples * 2)
        w.writeframes(pcm_bytes)


@pytest.mark.asyncio
async def test_tc14_transcription_valid_recording_mock():
    """TC #14: Transcription of a valid recording using structured response."""
    service = TranscriptionService()

    mock_data = {
        "status": "completed",
        "full_text": "Welcome to the TekMeet project discussion. We are reviewing Phase 4 requirements.",
        "language": "en",
        "duration_seconds": 15.5,
        "segments": [
            {"id": 0, "start": 0.0, "end": 5.0, "text": "Welcome to the TekMeet project discussion."},
            {"id": 1, "start": 5.0, "end": 15.5, "text": "We are reviewing Phase 4 requirements."},
        ],
    }

    transcript = await service.transcribe_recording(
        event_id="event_tc14_test",
        file_path_or_name="test_meeting.wav",
        call_id="call_tc14_123",
        mock_provider_override=mock_data,
    )

    assert transcript is not None
    assert transcript.status == TranscriptionStatus.COMPLETED
    assert transcript.event_id == "event_tc14_test"
    assert transcript.call_id == "call_tc14_123"
    assert "TekMeet" in transcript.full_text
    assert len(transcript.segments) == 2
    assert transcript.segments[0].start == 0.0
    assert transcript.segments[0].end == 5.0
    assert transcript.duration_seconds == 15.5
    assert transcript.error_message is None


@pytest.mark.asyncio
async def test_deepgram_api_successful_response():
    """Verify Deepgram API response parsing (metadata, utterances, confidence, duration)."""
    custom_settings = Settings(deepgram_api_key=SecretStr("mock_deepgram_test_key_12345"))
    service = TranscriptionService(app_settings=custom_settings)

    mock_deepgram_json = {
        "metadata": {
            "duration": 12.4,
            "channels": 1,
        },
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "Hello team, welcome to the weekly sync.",
                            "confidence": 0.98,
                            "language": "en",
                        }
                    ]
                }
            ],
            "utterances": [
                {
                    "start": 0.5,
                    "end": 4.2,
                    "transcript": "Hello team,",
                    "confidence": 0.99,
                    "speaker": 0,
                },
                {
                    "start": 4.3,
                    "end": 12.4,
                    "transcript": "welcome to the weekly sync.",
                    "confidence": 0.97,
                    "speaker": 0,
                },
            ],
        },
    }

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        create_synthetic_wav_file(tmp.name, duration_seconds=2.0)
        tmp_path = tmp.name

    try:
        from unittest.mock import MagicMock
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_deepgram_json

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            transcript = await service.transcribe_recording(
                event_id="event_deepgram_test",
                file_path_or_name=tmp_path,
                call_id="call_deepgram_99",
            )

        assert transcript.status == TranscriptionStatus.COMPLETED
        assert transcript.event_id == "event_deepgram_test"
        assert transcript.duration_seconds == 12.4
        assert transcript.full_text == "Hello team, welcome to the weekly sync."
        assert len(transcript.segments) == 2
        assert transcript.segments[0].text == "Hello team,"
        assert transcript.segments[0].start == 0.5
        assert transcript.segments[0].end == 4.2
        assert transcript.segments[0].speaker == "Speaker 0"
        assert transcript.segments[1].text == "welcome to the weekly sync."

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@pytest.mark.asyncio
async def test_deepgram_api_http_error_handling():
    """Verify Deepgram API HTTP error response (e.g. 401 Unauthorized) is handled gracefully without leaking keys."""
    custom_settings = Settings(deepgram_api_key=SecretStr("mock_key_401"))
    service = TranscriptionService(app_settings=custom_settings)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        create_synthetic_wav_file(tmp.name, duration_seconds=1.0)
        tmp_path = tmp.name

    try:
        from unittest.mock import MagicMock
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Invalid credentials provided"

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            transcript = await service.transcribe_recording(
                event_id="event_deepgram_401",
                file_path_or_name=tmp_path,
            )

        assert transcript.status == TranscriptionStatus.FAILED
        assert "401" in transcript.error_message
        assert "mock_key_401" not in transcript.error_message

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@pytest.mark.asyncio
async def test_tc14_transcription_preserves_original_file():
    """TC #14: Verify original audio file remains unchanged after transcription."""
    service = TranscriptionService()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        create_synthetic_wav_file(tmp_path, duration_seconds=1.0)
        original_size = os.path.getsize(tmp_path)
        assert original_size > 0

        mock_data = {
            "status": "completed",
            "full_text": "Sample speech test.",
            "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "Sample speech test."}],
        }

        transcript = await service.transcribe_recording(
            event_id="event_file_check",
            file_path_or_name=tmp_path,
            mock_provider_override=mock_data,
        )

        assert transcript.status == TranscriptionStatus.COMPLETED
        assert os.path.exists(tmp_path)
        assert os.path.getsize(tmp_path) == original_size  # File intact!

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@pytest.mark.asyncio
async def test_tc15_handling_empty_recording():
    """TC #15: Handling empty/zero-byte recording file gracefully."""
    service = TranscriptionService()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(b"")  # 0 bytes
        tmp_path = tmp.name

    try:
        transcript = await service.transcribe_recording(
            event_id="event_tc15_empty",
            file_path_or_name=tmp_path,
        )

        assert transcript is not None
        assert transcript.status == TranscriptionStatus.EMPTY
        assert "empty" in transcript.full_text.lower() or "no speech" in transcript.full_text.lower()

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@pytest.mark.asyncio
async def test_tc15_handling_unsupported_file_extension():
    """TC #15: Handling unsupported file extension gracefully (e.g. .txt)."""
    service = TranscriptionService()

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp.write(b"This is a text file, not audio.")
        tmp_path = tmp.name

    try:
        transcript = await service.transcribe_recording(
            event_id="event_tc15_unsupported",
            file_path_or_name=tmp_path,
        )

        assert transcript is not None
        assert transcript.status == TranscriptionStatus.UNSUPPORTED
        assert "unsupported" in transcript.error_message.lower()

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@pytest.mark.asyncio
async def test_tc15_handling_missing_recording_file():
    """TC #15: Handling non-existent/missing recording file gracefully."""
    service = TranscriptionService()

    non_existent_file = "non_existent_file_99999.wav"

    transcript = await service.transcribe_recording(
        event_id="event_tc15_missing",
        file_path_or_name=non_existent_file,
    )

    assert transcript is not None
    assert transcript.status == TranscriptionStatus.FAILED
    assert "not found" in transcript.error_message.lower()


@pytest.mark.asyncio
async def test_tc15_handling_missing_api_key():
    """TC #15: Verify missing DEEPGRAM_API_KEY returns FAILED status without exposing secrets or crashing."""
    custom_settings = Settings(deepgram_api_key=None)
    service = TranscriptionService(app_settings=custom_settings)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        create_synthetic_wav_file(tmp.name, duration_seconds=1.0)
        tmp_path = tmp.name

    try:
        transcript = await service.transcribe_recording(
            event_id="event_tc15_nokey",
            file_path_or_name=tmp_path,
        )

        assert transcript is not None
        assert transcript.status == TranscriptionStatus.FAILED
        assert "deepgram_api_key" in transcript.error_message.lower() or "not configured" in transcript.error_message.lower()

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
