"""Transcripts API router — Phase 4.

Endpoints to generate and inspect Speech-to-Text transcripts.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status

from app.models.transcript import Transcript, TranscriptionStatus
from app.services.phase4_pipeline import phase4_pipeline
from app.services.transcription_service import transcription_service

router = APIRouter(prefix="/api/v1/transcripts", tags=["Transcripts (Phase 4)"])


@router.post(
    "/generate",
    response_model=Transcript,
    summary="Generate speech-to-text transcript for a recording",
)
async def generate_transcript(
    event_id: str = Query(..., description="Microsoft Graph event ID"),
    file_name: str = Query(..., description="Recording file name or path (e.g. meeting_xxx.wav)"),
    call_id: Optional[str] = Query(None, description="Optional Graph call ID"),
) -> Transcript:
    """Generate or retrieve a transcript for a specified meeting recording file."""
    if not event_id.strip() or not file_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="event_id and file_name must be non-empty strings.",
        )

    transcript = await transcription_service.transcribe_recording(
        event_id=event_id,
        file_path_or_name=file_name,
        call_id=call_id,
    )

    if transcript.status == TranscriptionStatus.FAILED and "not found" in (transcript.error_message or "").lower():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=transcript.error_message,
        )

    return transcript


@router.get(
    "/{event_id}",
    response_model=Transcript,
    summary="Get transcript by meeting event ID",
)
def get_transcript_by_event_id(event_id: str) -> Transcript:
    """Retrieve an existing transcript from the pipeline registry by meeting event ID."""
    result = phase4_pipeline.get_result(event_id)
    if not result or not result.transcript:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No transcript found for meeting event ID '{event_id}'.",
        )
    return result.transcript
