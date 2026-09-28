"""Summary data models for TekMeet Phase 4 LLM Summarization."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class SummarizationStatus(str, Enum):
    """Lifecycle status of a summarization job."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    EMPTY = "empty"
    FAILED = "failed"


class ActionItem(BaseModel):
    """Action item or assigned task extracted from the transcript."""

    task: str = Field(..., description="Action item description")
    assignee: Optional[str] = Field(None, description="Person assigned to the task (only if explicitly stated)")
    deadline: Optional[str] = Field(None, description="Due date or deadline (only if explicitly stated)")


class Decision(BaseModel):
    """Key decision made during the meeting."""

    title: str = Field(..., description="Decision summary title")
    details: Optional[str] = Field(None, description="Additional context or rationale for the decision")


class MeetingSummary(BaseModel):
    """Structured representation of an AI-generated meeting summary."""

    summary_id: str = Field(..., description="Unique summary identifier")
    event_id: str = Field(..., description="Associated meeting event ID")
    transcript_id: Optional[str] = Field(None, description="Associated transcript ID")
    status: SummarizationStatus = Field(default=SummarizationStatus.PROCESSING, description="Current status of the summary")
    overview: str = Field(default="", description="Concise executive overview of the meeting")
    key_points: List[str] = Field(default_factory=list, description="Bullet points summarizing main discussion topics")
    action_items: List[ActionItem] = Field(default_factory=list, description="List of identified action items")
    decisions: List[Decision] = Field(default_factory=list, description="List of agreed decisions")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Generation timestamp")
    error_message: Optional[str] = Field(None, description="Detailed error message if summarization failed")
