"""Transcript data models for TekMeet Phase 4 AI Transcription."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class TranscriptionStatus(str, Enum):
    """Lifecycle status of a transcription job."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    EMPTY = "empty"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class TranscriptSegment(BaseModel):
    """Timestamped segment/chunk of transcribed text."""

    id: int = Field(..., description="Segment index")
    start: float = Field(..., description="Start timestamp in seconds")
    end: float = Field(..., description="End timestamp in seconds")
    text: str = Field(..., description="Transcribed text for this segment")
    speaker: Optional[str] = Field(
        None,
        description="Optional speaker identifier (future/provider-dependent, not guaranteed by Whisper)",
    )
    confidence: Optional[float] = Field(None, description="Segment confidence score if provided by provider")


class Transcript(BaseModel):
    """Structured representation of a completed or failed transcription."""

    transcript_id: str = Field(..., description="Unique transcript identifier")
    event_id: str = Field(..., description="Associated meeting event ID")
    call_id: Optional[str] = Field(None, description="Graph call ID if available")
    recording_file: str = Field(..., description="Name or path of the input recording file")
    status: TranscriptionStatus = Field(default=TranscriptionStatus.PROCESSING, description="Current transcription status")
    full_text: str = Field(default="", description="Complete transcribed text concatenated across segments")
    language: str = Field(default="en", description="Detected or specified audio language")
    duration_seconds: float = Field(default=0.0, description="Total duration of the audio in seconds")
    segments: List[TranscriptSegment] = Field(default_factory=list, description="Timestamped segments list")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp when transcript was generated")
    error_message: Optional[str] = Field(None, description="Detailed error message if transcription failed")
