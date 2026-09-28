"""Scheduler and Dispatcher data models for TekMeet."""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class MeetingStatus(str, Enum):
    """Lifecycle state of a scheduled meeting in the dispatcher."""

    SCHEDULED = "scheduled"  # Meeting is scheduled in the future, awaiting start time
    TRIGGERED = "triggered"  # Start time reached (with join buffer), dispatched to join handler
    COMPLETED = "completed"  # Meeting end time has passed
    CANCELLED = "cancelled"  # Meeting was cancelled on the calendar
    MISSED = "missed"        # Meeting was detected only after its end time passed
    FAILED = "failed"        # Join dispatch handler encountered an error


class ScheduledMeetingJob(BaseModel):
    """Internal tracking model for a scheduled meeting job."""

    event_id: str = Field(..., description="Microsoft Graph unique event ID")
    subject: str = Field(default="Untitled Meeting", description="Meeting title / subject")
    start_time: datetime = Field(..., description="Scheduled start time (UTC)")
    end_time: datetime = Field(..., description="Scheduled end time (UTC)")
    join_url: Optional[str] = Field(None, description="Microsoft Teams meeting join URL")
    organizer_email: Optional[str] = Field(None, description="Organizer email address")
    status: MeetingStatus = Field(default=MeetingStatus.SCHEDULED, description="Current lifecycle state")
    dispatched_at: Optional[datetime] = Field(None, description="Timestamp when meeting was dispatched (UTC)")
    dispatch_message: Optional[str] = Field(None, description="Message or note from join handler")
    # Phase 2: populated by TeamsCallJoinHandler after Graph POST /communications/calls succeeds
    call_id: Optional[str] = Field(None, description="Graph-assigned call resource ID for the active call")
    created_at: datetime = Field(..., description="When the job was first registered (UTC)")
    updated_at: datetime = Field(..., description="When the job was last updated (UTC)")


class SchedulerStatusResponse(BaseModel):
    """Diagnostic model for background meeting scheduler status."""

    is_running: bool = Field(..., description="Whether the background polling task is active")
    poll_interval_seconds: int = Field(..., description="Configured poll interval in seconds")
    join_buffer_seconds: int = Field(..., description="Buffer in seconds to trigger join ahead of start time")
    total_tracked_jobs: int = Field(default=0, description="Total meeting jobs currently tracked in memory")
    scheduled_jobs_count: int = Field(default=0, description="Count of jobs waiting for start time")
    triggered_jobs_count: int = Field(default=0, description="Count of jobs successfully triggered")
    completed_jobs_count: int = Field(default=0, description="Count of completed jobs")
    last_poll_time_utc: Optional[datetime] = Field(None, description="Timestamp of the most recent polling cycle")
    last_poll_result: Optional[str] = Field(None, description="Summary of the last polling cycle")


class PollCycleResult(BaseModel):
    """Result summary of a single polling and dispatch execution."""

    success: bool
    polled_at_utc: datetime
    events_found: int
    teams_meetings_found: int
    newly_scheduled: int
    newly_dispatched: int
    dispatched_meeting_ids: List[str] = Field(default_factory=list)
    message: str
