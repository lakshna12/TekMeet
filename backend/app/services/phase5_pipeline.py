"""Phase 5 Pipeline Orchestration for TekMeet.

Connects the full End-to-End lifecycle:
  Meeting Completed -> Recording Finalized -> Speech-to-Text (TranscriptionService) ->
  LLM Summarization (SummarizationService) -> Disk Persistence (StorageService) ->
  HTML Summary Email Delivery (EmailService) -> Delivery Status Persisted.

Provides deduplication, idempotent duplicate meeting protection, transient retry support,
and graceful degradation so delivery failures never delete completed transcripts or summaries.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.config import Settings, settings
from app.models.delivery import DeliveryProvider, DeliveryRecord, DeliveryStatus
from app.models.scheduler import ScheduledMeetingJob
from app.models.summary import MeetingSummary, SummarizationStatus
from app.models.transcript import Transcript, TranscriptionStatus
from app.services.email_service import EmailService, email_service
from app.services.storage_service import StorageService, storage_service
from app.services.summarization_service import SummarizationService, summarization_service
from app.services.transcription_service import TranscriptionService, transcription_service

logger = logging.getLogger(__name__)


class Phase5PipelineResult(BaseModel):
    """Structured output model for Phase 5 End-to-End Pipeline execution."""

    pipeline_id: str = Field(..., description="Unique pipeline execution ID")
    event_id: str = Field(..., description="Microsoft Graph event ID")
    call_id: Optional[str] = Field(None, description="Graph call ID")
    recording_file: str = Field(..., description="Name or path of finalized recording file")
    recipient_email: Optional[str] = Field(None, description="Target email recipient")
    transcript: Optional[Transcript] = Field(None, description="Persisted Transcript model")
    summary: Optional[MeetingSummary] = Field(None, description="Persisted MeetingSummary model")
    delivery_record: Optional[DeliveryRecord] = Field(None, description="Persisted DeliveryRecord model")
    status: str = Field(default="processing", description="Overall pipeline status ('completed', 'partial', 'failed', 'idempotent_duplicate')")
    processed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Completion timestamp")
    error_message: Optional[str] = Field(None, description="Detailed error message if pipeline failed")


class Phase5Pipeline:
    """Production End-to-End Orchestration Pipeline for Phase 5."""

    def __init__(
        self,
        stt_service: Optional[TranscriptionService] = None,
        llm_service: Optional[SummarizationService] = None,
        mail_service: Optional[EmailService] = None,
        db_service: Optional[StorageService] = None,
        app_settings: Optional[Settings] = None,
    ):
        self.stt_service = stt_service or transcription_service
        self.llm_service = llm_service or summarization_service
        self.mail_service = mail_service or email_service
        self.db_service = db_service or storage_service
        self.settings = app_settings or settings

    def _resolve_organizer_email(self, job: Optional[ScheduledMeetingJob], direct_email: Optional[str] = None) -> Optional[str]:
        """Determine recipient email address using job.organizer_email with settings fallback."""
        if direct_email and direct_email.strip():
            return direct_email.strip()
        if job and job.organizer_email and job.organizer_email.strip():
            return job.organizer_email.strip()
        if self.settings.azure_bot_user_email and self.settings.azure_bot_user_email.strip():
            return self.settings.azure_bot_user_email.strip()
        return None

    async def process_end_to_end_job(
        self,
        job: Optional[ScheduledMeetingJob] = None,
        event_id: Optional[str] = None,
        file_path_or_name: Optional[str] = None,
        call_id: Optional[str] = None,
        organizer_email: Optional[str] = None,
        meeting_title: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        mock_stt_override: Optional[dict] = None,
        mock_summary_override: Optional[dict] = None,
        mock_email_provider: Optional[DeliveryProvider] = None,
        force_refresh: bool = False,
    ) -> Phase5PipelineResult:
        """Execute full End-to-End Phase 5 Pipeline:
        Audio -> STT Transcript -> Persist -> LLM Summary -> Persist -> Email Delivery -> Persist.

        Idempotency: Prevents sending duplicate emails for the same event_id unless force_refresh is True.
        """
        pipeline_id = f"p5_{uuid.uuid4().hex[:12]}"
        target_event_id = event_id or (job.event_id if job else f"event_{uuid.uuid4().hex[:8]}")
        target_call_id = call_id or (job.call_id if job else None)
        title = meeting_title or (job.subject if job else "Teams Meeting")
        target_recording_file = file_path_or_name or f"meeting_{target_event_id}.wav"
        recipient = self._resolve_organizer_email(job, organizer_email)

        logger.info("[Phase5Pipeline] Starting E2E Pipeline (ID: %s) for event '%s' (Recipient: '%s')", pipeline_id, target_event_id, recipient)

        # Step 1: Idempotency & Duplicate Delivery Protection
        existing_delivery = self.db_service.get_delivery_record(target_event_id)
        if not force_refresh and existing_delivery and existing_delivery.status == DeliveryStatus.SENT:
            logger.info("[Phase5Pipeline] Event '%s' has already been successfully delivered. Returning idempotent duplicate status.", target_event_id)
            existing_transcript = self.db_service.get_transcript(target_event_id)
            existing_summary = self.db_service.get_summary(target_event_id)
            return Phase5PipelineResult(
                pipeline_id=pipeline_id,
                event_id=target_event_id,
                call_id=target_call_id,
                recording_file=target_recording_file,
                recipient_email=recipient,
                transcript=existing_transcript,
                summary=existing_summary,
                delivery_record=existing_delivery,
                status="idempotent_duplicate",
            )

        # Step 2: Speech-to-Text Transcription
        # Check existing transcript reuse
        transcript = self.db_service.get_transcript(target_event_id) if not force_refresh else None
        if not transcript:
            transcript = await self.stt_service.transcribe_recording(
                event_id=target_event_id,
                file_path_or_name=target_recording_file,
                call_id=target_call_id,
                file_bytes=file_bytes,
                mock_provider_override=mock_stt_override,
            )
            self.db_service.save_transcript(transcript)

        # Handle STT failure
        if transcript.status == TranscriptionStatus.FAILED:
            err_msg = f"Meeting summary unavailable because transcript/Claude summary was not generated (STT failed: {transcript.error_message or 'Unknown STT error'})."
            logger.error("[Phase5Pipeline] E2E Pipeline failed at STT stage for event '%s': %s", target_event_id, err_msg)
            failed_delivery = DeliveryRecord(
                delivery_id=f"del_{uuid.uuid4().hex[:12]}",
                event_id=target_event_id,
                recipient_email=recipient or "missing@invalid",
                status=DeliveryStatus.FAILED,
                error_message=err_msg,
            )
            self.db_service.save_delivery_record(failed_delivery)
            return Phase5PipelineResult(
                pipeline_id=pipeline_id,
                event_id=target_event_id,
                call_id=target_call_id,
                recording_file=target_recording_file,
                recipient_email=recipient,
                transcript=transcript,
                summary=None,
                delivery_record=failed_delivery,
                status="failed",
                error_message=err_msg,
            )

        # Step 3: LLM Summarization
        # Check existing summary reuse
        summary = self.db_service.get_summary(target_event_id) if not force_refresh else None
        if not summary:
            summary = await self.llm_service.summarize_transcript(
                transcript=transcript,
                mock_provider_override=mock_summary_override,
            )
            self.db_service.save_summary(summary)

        # Handle Summary failure
        if summary.status == SummarizationStatus.FAILED:
            err_msg = f"Meeting summary unavailable because transcript/Claude summary was not generated (LLM failed: {summary.error_message or 'Unknown LLM error'})."
            logger.warning("[Phase5Pipeline] E2E Pipeline partial failure at LLM stage for event '%s': %s", target_event_id, err_msg)
            failed_delivery = DeliveryRecord(
                delivery_id=f"del_{uuid.uuid4().hex[:12]}",
                event_id=target_event_id,
                recipient_email=recipient or "missing@invalid",
                status=DeliveryStatus.FAILED,
                error_message=err_msg,
            )
            self.db_service.save_delivery_record(failed_delivery)
            return Phase5PipelineResult(
                pipeline_id=pipeline_id,
                event_id=target_event_id,
                call_id=target_call_id,
                recording_file=target_recording_file,
                recipient_email=recipient,
                transcript=transcript,
                summary=summary,
                delivery_record=failed_delivery,
                status="partial",
                error_message=err_msg,
            )

        # Step 4: Organizer Email Selection & Validation
        base_backend_url = self.settings.public_backend_url or "http://localhost:8000"
        recording_download_url = f"{self.settings.media_worker_url}/api/media/recordings/download?file={target_recording_file}"
        transcript_view_url = f"{base_backend_url.rstrip('/')}/api/v1/transcripts/{target_event_id}"

        if not recipient or not self.mail_service.validate_email_address(recipient):
            err_msg = f"Invalid or missing organizer email address '{recipient}' for event '{target_event_id}'."
            logger.error("[Phase5Pipeline] %s", err_msg)
            failed_delivery = DeliveryRecord(
                delivery_id=f"del_{uuid.uuid4().hex[:12]}",
                event_id=target_event_id,
                recipient_email=recipient or "missing@invalid",
                status=DeliveryStatus.FAILED,
                error_message=err_msg,
            )
            self.db_service.save_delivery_record(failed_delivery)
            return Phase5PipelineResult(
                pipeline_id=pipeline_id,
                event_id=target_event_id,
                call_id=target_call_id,
                recording_file=target_recording_file,
                recipient_email=recipient,
                transcript=transcript,
                summary=summary,
                delivery_record=failed_delivery,
                status="partial",
                error_message=err_msg,
            )

        # Step 5: Email Delivery Stage
        delivery_record = await self.mail_service.send_summary_email(
            event_id=target_event_id,
            recipient_email=recipient,
            summary=summary,
            meeting_title=title,
            recording_url=recording_download_url,
            transcript_url=transcript_view_url,
            provider_override=mock_email_provider,
        )
        self.db_service.save_delivery_record(delivery_record)

        overall_status = "completed" if delivery_record.status == DeliveryStatus.SENT else "partial"
        overall_error = None if delivery_record.status == DeliveryStatus.SENT else f"Email delivery failed: {delivery_record.error_message}"

        logger.info(
            "[Phase5Pipeline] Completed E2E Pipeline for event '%s' | Status: %s | Delivery: %s",
            target_event_id,
            overall_status,
            delivery_record.status,
        )

        return Phase5PipelineResult(
            pipeline_id=pipeline_id,
            event_id=target_event_id,
            call_id=target_call_id,
            recording_file=target_recording_file,
            recipient_email=recipient,
            transcript=transcript,
            summary=summary,
            delivery_record=delivery_record,
            status=overall_status,
            error_message=overall_error,
        )


# Global singleton instance
phase5_pipeline = Phase5Pipeline()
