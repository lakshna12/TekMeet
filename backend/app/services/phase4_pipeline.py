"""Phase 4 Pipeline Orchestrator for TekMeet.

Connects recording audio -> TranscriptionService -> SummarizationService.
Provides in-memory caching and deduplication to avoid repeated API calls.
Handles empty recordings, transcription failures, and LLM failures gracefully.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from app.models.summary import MeetingSummary, SummarizationStatus
from app.models.transcript import Transcript, TranscriptionStatus
from app.services.summarization_service import SummarizationService, summarization_service
from app.services.transcription_service import TranscriptionService, transcription_service

logger = logging.getLogger(__name__)


class Phase4PipelineResult(BaseModel):
    """Combined output model for Phase 4 processing (Transcript + MeetingSummary)."""

    event_id: str = Field(..., description="Microsoft Graph event ID")
    call_id: Optional[str] = Field(None, description="Graph call ID")
    recording_file: str = Field(..., description="Recording file name or path")
    transcript: Optional[Transcript] = Field(None, description="Generated Transcript model")
    summary: Optional[MeetingSummary] = Field(None, description="Generated MeetingSummary model")
    status: str = Field(default="processing", description="Overall pipeline status ('completed', 'partial', 'failed')")
    processed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Completion timestamp")
    error_message: Optional[str] = Field(None, description="Overall error message if pipeline failed")


class Phase4Pipeline:
    """Production Orchestration Pipeline for Phase 4."""

    def __init__(
        self,
        stt_service: Optional[TranscriptionService] = None,
        llm_service: Optional[SummarizationService] = None,
    ):
        self.stt_service = stt_service or transcription_service
        self.llm_service = llm_service or summarization_service
        self._registry: Dict[str, Phase4PipelineResult] = {}

    def get_result(self, event_id: str) -> Optional[Phase4PipelineResult]:
        """Retrieve stored pipeline result by event ID."""
        return self._registry.get(event_id)

    def get_all_results(self) -> List[Phase4PipelineResult]:
        """Retrieve all completed pipeline results in the registry."""
        return list(self._registry.values())

    async def process_recording(
        self,
        event_id: str,
        file_path_or_name: str,
        call_id: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        mock_transcription_override: Optional[dict] = None,
        mock_summarization_override: Optional[dict] = None,
        force_refresh: bool = False,
    ) -> Phase4PipelineResult:
        """Run full Phase 4 Pipeline: Audio Recording -> STT Transcript -> LLM Summary.

        Deduplicates repeated calls for the same event_id unless force_refresh is True.

        Args:
            event_id: Meeting event ID.
            file_path_or_name: Recording file path or name.
            call_id: Optional Graph call ID.
            file_bytes: Optional in-memory audio bytes.
            mock_transcription_override: Mock dictionary override for STT testing.
            mock_summarization_override: Mock dictionary override for Summary testing.
            force_refresh: If True, ignores cached result and re-runs pipeline.

        Returns:
            Structured Phase4PipelineResult.
        """
        # Deduplication check
        if not force_refresh and event_id in self._registry:
            logger.info("[Phase4Pipeline] Returning cached pipeline result for event '%s' (deduplicated)", event_id)
            return self._registry[event_id]

        logger.info("[Phase4Pipeline] Starting Phase 4 pipeline execution for event '%s'", event_id)

        # Step 1: Run Speech-to-Text Transcription
        transcript = await self.stt_service.transcribe_recording(
            event_id=event_id,
            file_path_or_name=file_path_or_name,
            call_id=call_id,
            file_bytes=file_bytes,
            mock_provider_override=mock_transcription_override,
        )

        if transcript.status == TranscriptionStatus.FAILED:
            err_msg = f"Transcription failed: {transcript.error_message or 'Unknown STT error'}"
            logger.error("[Phase4Pipeline] Pipeline failed at STT stage for event '%s': %s", event_id, err_msg)
            result = Phase4PipelineResult(
                event_id=event_id,
                call_id=call_id,
                recording_file=file_path_or_name,
                transcript=transcript,
                summary=None,
                status="failed",
                error_message=err_msg,
            )
            self._registry[event_id] = result
            return result

        # Step 2: Run LLM Summarization
        summary = await self.llm_service.summarize_transcript(
            transcript=transcript,
            mock_provider_override=mock_summarization_override,
        )

        overall_status = "completed"
        overall_error = None

        if summary.status == SummarizationStatus.FAILED:
            overall_status = "partial"
            overall_error = f"Summarization failed: {summary.error_message or 'Unknown LLM error'}"
            logger.warning("[Phase4Pipeline] Pipeline completed with partial status (STT OK, LLM Failed) for event '%s'", event_id)

        result = Phase4PipelineResult(
            event_id=event_id,
            call_id=call_id,
            recording_file=file_path_or_name,
            transcript=transcript,
            summary=summary,
            status=overall_status,
            error_message=overall_error,
        )

        self._registry[event_id] = result
        logger.info("[Phase4Pipeline] Successfully completed pipeline for event '%s' | Status: %s", event_id, overall_status)
        return result


# Global singleton instance
phase4_pipeline = Phase4Pipeline()
