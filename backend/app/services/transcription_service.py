"""Transcription Service for Phase 4 Speech-to-Text via Deepgram API.

Converts recorded meeting audio (.wav / .mp4 / .mp3 / .webm) into structured Transcript models with
timestamped segments. Handles missing, empty, silent, corrupt, and unsupported files gracefully.
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Optional, Tuple

import httpx

from app.core.config import Settings, settings
from app.models.transcript import Transcript, TranscriptSegment, TranscriptionStatus

logger = logging.getLogger(__name__)


class TranscriptionServiceError(Exception):
    """Custom exception raised during transcription failures."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class TranscriptionService:
    """Production Speech-to-Text Transcription Service using Deepgram REST API."""

    def __init__(self, app_settings: Optional[Settings] = None):
        self.settings = app_settings or settings

    def _get_api_key(self) -> Optional[str]:
        """Retrieve Deepgram API key safely from settings."""
        if self.settings.deepgram_api_key:
            return self.settings.deepgram_api_key.get_secret_value()
        return None

    def _get_media_type(self, file_name: str) -> str:
        """Map file extension to standard audio MIME type."""
        ext = Path(file_name).suffix.lower()
        mime_map = {
            ".wav": "audio/wav",
            ".mp4": "audio/mp4",
            ".m4a": "audio/m4a",
            ".mp3": "audio/mpeg",
            ".webm": "audio/webm",
            ".ogg": "audio/ogg",
            ".flac": "audio/flac",
        }
        return mime_map.get(ext, "application/octet-stream")

    async def transcribe_recording(
        self,
        event_id: str,
        file_path_or_name: str,
        call_id: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        mock_provider_override: Optional[dict] = None,
    ) -> Transcript:
        """Transcribe a meeting recording file (WAV / MP4) into a structured Transcript.

        Args:
            event_id: Microsoft Graph event ID.
            file_path_or_name: Path to local file or filename served by MediaWorker.
            call_id: Optional Graph call ID.
            file_bytes: Optional pre-loaded binary data for in-memory testing.
            mock_provider_override: Optional dictionary mock for testing scenarios.

        Returns:
            Structured Transcript model (Status COMPLETED, EMPTY, FAILED, or UNSUPPORTED).
        """
        transcript_id = f"tr_{uuid.uuid4().hex[:12]}"
        logger.info("[TranscriptionService] Initiating Deepgram transcription for event '%s' (file: %s)", event_id, file_path_or_name)

        # Handle explicit mock override (used in unit tests)
        if mock_provider_override is not None:
            return self._build_transcript_from_dict(
                transcript_id=transcript_id,
                event_id=event_id,
                call_id=call_id,
                file_name=file_path_or_name,
                data=mock_provider_override,
            )

        # Step 1: Validate or retrieve audio bytes/path
        local_file_path, audio_bytes, file_name, err_status = await self._resolve_audio_source(file_path_or_name, file_bytes)
        if err_status:
            logger.error("[TranscriptionService] Audio source resolution failed for '%s': %s", file_path_or_name, err_status[1])
            return Transcript(
                transcript_id=transcript_id,
                event_id=event_id,
                call_id=call_id,
                recording_file=file_path_or_name,
                status=err_status[0],
                full_text="",
                error_message=err_status[1],
            )

        # Step 2: Validate file format (.wav, .mp4, .m4a, .mp3, .webm supported)
        ext = Path(file_name).suffix.lower()
        if ext not in [".wav", ".mp4", ".m4a", ".mp3", ".webm", ".ogg", ".flac"]:
            logger.warning("[TranscriptionService] File format '%s' is unsupported.", ext)
            return Transcript(
                transcript_id=transcript_id,
                event_id=event_id,
                call_id=call_id,
                recording_file=file_name,
                status=TranscriptionStatus.UNSUPPORTED,
                full_text="",
                error_message=f"Unsupported file format '{ext}'. Expected .wav or .mp4.",
            )

        # Step 3: Check for empty or zero-byte file
        if len(audio_bytes) < 100:  # Header-only or empty file
            logger.info("[TranscriptionService] Audio file '%s' is empty or zero-byte (%d bytes).", file_name, len(audio_bytes))
            return Transcript(
                transcript_id=transcript_id,
                event_id=event_id,
                call_id=call_id,
                recording_file=file_name,
                status=TranscriptionStatus.EMPTY,
                full_text="No speech detected (empty recording).",
            )

        # Step 4: Check Deepgram API Key & Credentials
        api_key = self._get_api_key()
        if not api_key:
            err_msg = "Deepgram API Key is not configured in environment (DEEPGRAM_API_KEY)."
            logger.error("[TranscriptionService] %s", err_msg)
            return Transcript(
                transcript_id=transcript_id,
                event_id=event_id,
                call_id=call_id,
                recording_file=file_name,
                status=TranscriptionStatus.FAILED,
                full_text="",
                error_message=err_msg,
            )

        # Step 5: Send file to Deepgram REST API
        try:
            model_name = self.settings.deepgram_model
            logger.info("[TranscriptionService] Sending audio to Deepgram API (model: %s)...", model_name)
            
            url = f"https://api.deepgram.com/v1/listen?punctuate=true&utterances=true&smart_format=true&model={model_name}"
            headers = {
                "Authorization": f"Token {api_key}",
                "Content-Type": self._get_media_type(file_name),
            }

            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(url, headers=headers, content=audio_bytes)

            if response.status_code != 200:
                safe_err_text = response.text.replace(api_key, "[REDACTED_API_KEY]")
                logger.error("[TranscriptionService] Deepgram API returned status %d: %s", response.status_code, safe_err_text)
                return Transcript(
                    transcript_id=transcript_id,
                    event_id=event_id,
                    call_id=call_id,
                    recording_file=file_name,
                    status=TranscriptionStatus.FAILED,
                    full_text="",
                    error_message=f"Deepgram API HTTP {response.status_code}: {safe_err_text}",
                )

            res_data = response.json()
            return self._parse_deepgram_response(
                transcript_id=transcript_id,
                event_id=event_id,
                call_id=call_id,
                file_name=file_name,
                data=res_data,
            )

        except Exception as exc:
            safe_exc = str(exc).replace(api_key, "[REDACTED_API_KEY]") if api_key else str(exc)
            logger.error("[TranscriptionService] Exception during Deepgram API call: %s", safe_exc)
            return Transcript(
                transcript_id=transcript_id,
                event_id=event_id,
                call_id=call_id,
                recording_file=file_name,
                status=TranscriptionStatus.FAILED,
                full_text="",
                error_message=f"Deepgram API error: {safe_exc}",
            )

    def _parse_deepgram_response(
        self, transcript_id: str, event_id: str, call_id: Optional[str], file_name: str, data: dict
    ) -> Transcript:
        """Parse Deepgram JSON response object into a Transcript model."""
        metadata = data.get("metadata", {})
        duration = float(metadata.get("duration", 0.0) or 0.0)

        results = data.get("results", {})
        channels = results.get("channels", [])
        alternatives = channels[0].get("alternatives", []) if channels else []
        full_text = alternatives[0].get("transcript", "") if alternatives else ""
        language = alternatives[0].get("language", "en") if alternatives else "en"

        raw_utterances = results.get("utterances", [])
        segments = []

        if raw_utterances:
            for i, utt in enumerate(raw_utterances):
                utt_text = utt.get("transcript", "").strip()
                if not utt_text:
                    continue
                speaker_id = utt.get("speaker")
                confidence_val = utt.get("confidence")
                segments.append(
                    TranscriptSegment(
                        id=i,
                        start=float(utt.get("start", 0.0)),
                        end=float(utt.get("end", 0.0)),
                        text=utt_text,
                        speaker=f"Speaker {speaker_id}" if speaker_id is not None else None,
                        confidence=float(confidence_val) if confidence_val is not None else None,
                    )
                )
        elif alternatives and alternatives[0].get("words"):
            # Fallback to word-level segments grouped if utterances not present
            words = alternatives[0].get("words", [])
            if words:
                segments.append(
                    TranscriptSegment(
                        id=0,
                        start=float(words[0].get("start", 0.0)),
                        end=float(words[-1].get("end", duration)),
                        text=full_text.strip(),
                        confidence=float(alternatives[0].get("confidence", 1.0)) if "confidence" in alternatives[0] else None,
                    )
                )

        full_text_trimmed = full_text.strip()
        status_enum = TranscriptionStatus.COMPLETED if full_text_trimmed else TranscriptionStatus.EMPTY
        if not full_text_trimmed:
            full_text_trimmed = "No speech detected."

        logger.info(
            "[TranscriptionService] Deepgram transcription successful! Duration: %.1fs | Segments: %d | Status: %s",
            duration,
            len(segments),
            status_enum,
        )

        return Transcript(
            transcript_id=transcript_id,
            event_id=event_id,
            call_id=call_id,
            recording_file=file_name,
            status=status_enum,
            full_text=full_text_trimmed,
            language=language or "en",
            duration_seconds=duration,
            segments=segments,
        )

    async def _resolve_audio_source(
        self, file_path_or_name: str, file_bytes: Optional[bytes]
    ) -> Tuple[Optional[str], bytes, str, Optional[Tuple[TranscriptionStatus, str]]]:
        """Resolve file path or bytes from local filesystem or MediaWorker HTTP endpoint."""
        file_name = Path(file_path_or_name).name

        if file_bytes is not None:
            return None, file_bytes, file_name, None

        # Check if local path exists on disk
        if os.path.exists(file_path_or_name):
            try:
                with open(file_path_or_name, "rb") as f:
                    data = f.read()
                return file_path_or_name, data, file_name, None
            except Exception as exc:
                return None, b"", file_name, (TranscriptionStatus.FAILED, f"Could not read local file: {exc}")

        # Attempt to fetch from MediaWorker endpoint if not found locally
        download_url = f"{self.settings.media_worker_url}/api/media/recordings/download?file={file_name}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(download_url)
                if resp.status_code == 200 and resp.content:
                    return None, resp.content, file_name, None
                return (
                    None,
                    b"",
                    file_name,
                    (TranscriptionStatus.FAILED, f"File not found on MediaWorker (HTTP {resp.status_code})"),
                )
        except Exception as exc:
            return None, b"", file_name, (TranscriptionStatus.FAILED, f"File not found locally or on MediaWorker: {exc}")

    def _build_transcript_from_dict(
        self, transcript_id: str, event_id: str, call_id: Optional[str], file_name: str, data: dict
    ) -> Transcript:
        """Construct Transcript model from a dict data payload (for mock / test support)."""
        raw_segments = data.get("segments", [])
        segments = []
        for i, s in enumerate(raw_segments):
            segments.append(
                TranscriptSegment(
                    id=s.get("id", i),
                    start=float(s.get("start", 0.0)),
                    end=float(s.get("end", 0.0)),
                    text=s.get("text", ""),
                    speaker=s.get("speaker"),
                    confidence=s.get("confidence"),
                )
            )

        status_str = data.get("status", TranscriptionStatus.COMPLETED)
        status_enum = TranscriptionStatus(status_str) if isinstance(status_str, str) else status_str

        return Transcript(
            transcript_id=transcript_id,
            event_id=event_id,
            call_id=call_id,
            recording_file=file_name,
            status=status_enum,
            full_text=data.get("full_text", ""),
            language=data.get("language", "en"),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            segments=segments,
            error_message=data.get("error_message"),
        )


# Global singleton instance
transcription_service = TranscriptionService()
