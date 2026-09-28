"""Meeting and calendar data models for TekMeet."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class AttendeeInfo(BaseModel):
    """Details of a meeting participant."""

    email: Optional[str] = Field(None, description="Email address of the attendee")
    name: Optional[str] = Field(None, description="Display name of the attendee")
    attendee_type: Optional[str] = Field(None, description="Required, Optional, or Resource attendee")
    response_status: Optional[str] = Field(None, description="Response status (accepted, declined, tentative, etc.)")


class MeetingEvent(BaseModel):
    """Normalized internal representation of a calendar event."""

    event_id: str = Field(..., description="Microsoft Graph unique event ID")
    subject: str = Field(default="Untitled Meeting", description="Meeting subject / title")
    start_time: Optional[datetime] = Field(None, description="Meeting scheduled start time (UTC timezone-aware)")
    end_time: Optional[datetime] = Field(None, description="Meeting scheduled end time (UTC timezone-aware)")
    organizer_name: Optional[str] = Field(None, description="Organizer display name")
    organizer_email: Optional[str] = Field(None, description="Organizer email address")
    attendees: List[AttendeeInfo] = Field(default_factory=list, description="List of invited attendees")

    # Online / Teams meeting specific properties
    is_online_meeting: bool = Field(default=False, description="Whether this event is configured as an online meeting")
    online_meeting_provider: Optional[str] = Field(
        None, description="Provider identifier (e.g. teamsForBusiness, skypeForBusiness, unknown)"
    )
    is_teams_meeting: bool = Field(default=False, description="True if detected as a Microsoft Teams meeting")
    join_url: Optional[str] = Field(None, description="Microsoft Teams join URL if available")
    meeting_id: Optional[str] = Field(None, description="Structured online meeting ID or thread ID if provided")
    conference_id: Optional[str] = Field(None, description="Audio conferencing ID if provided")

    # Additional metadata
    location: Optional[str] = Field(None, description="Physical location or meeting room name")
    is_cancelled: bool = Field(default=False, description="Whether the event has been cancelled")
    web_link: Optional[str] = Field(None, description="Link to open the event in Outlook on the Web")


class UpcomingMeetingsResponse(BaseModel):
    """Response model for upcoming meetings query."""

    success: bool = Field(..., description="Whether the calendar query succeeded")
    target_user: Optional[str] = Field(None, description="Target mailbox / user principal name queried")
    window_start_utc: datetime = Field(..., description="Start of lookahead time window in UTC")
    window_end_utc: datetime = Field(..., description="End of lookahead time window in UTC")
    total_events_retrieved: int = Field(default=0, description="Total calendar events found in the window")
    teams_meetings_count: int = Field(default=0, description="Count of identified Microsoft Teams meetings")
    meetings: List[MeetingEvent] = Field(default_factory=list, description="List of upcoming meetings")
    error: Optional[str] = Field(None, description="Error message if query failed")


# --- Past Meeting Summary & View Response Models (Phase 5 Step 5) ---

from app.models.delivery import DeliveryRecord
from app.models.summary import MeetingSummary
from app.models.transcript import Transcript


class MeetingInfo(BaseModel):
    """Basic metadata summary for a meeting."""

    subject: str = Field(default="Untitled Meeting", description="Meeting title / subject")
    start_time: Optional[datetime] = Field(None, description="Scheduled start time")
    end_time: Optional[datetime] = Field(None, description="Scheduled end time")
    organizer_email: Optional[str] = Field(None, description="Organizer email address")


class RecordingInfo(BaseModel):
    """Recording file reference and download URL."""

    file_name: str = Field(..., description="Recording filename (.wav or .mp4)")
    download_url: str = Field(..., description="MediaWorker recording download URL")


class MeetingSummaryViewResponse(BaseModel):
    """Structured response model for GET /api/v1/meetings/{event_id}/summary."""

    event_id: str = Field(..., description="Microsoft Graph event ID")
    meeting: MeetingInfo = Field(..., description="Meeting metadata details")
    transcript: Optional[Transcript] = Field(None, description="Transcript model if available")
    summary: Optional[MeetingSummary] = Field(None, description="Meeting summary model if available")
    recording: Optional[RecordingInfo] = Field(None, description="Recording file information")
    delivery: Optional[DeliveryRecord] = Field(None, description="Email delivery record if available")


class PastMeetingItem(BaseModel):
    """Summary item representation for GET /api/v1/meetings."""

    event_id: str = Field(..., description="Microsoft Graph event ID")
    subject: str = Field(default="Untitled Meeting", description="Meeting title")
    start_time: Optional[datetime] = Field(None, description="Start time")
    end_time: Optional[datetime] = Field(None, description="End time")
    organizer_email: Optional[str] = Field(None, description="Organizer email")
    has_transcript: bool = Field(default=False, description="True if transcript exists")
    has_summary: bool = Field(default=False, description="True if summary exists")
    delivery_status: Optional[str] = Field(None, description="Delivery status string if available")
    processed_at: Optional[datetime] = Field(None, description="Timestamp of processing")

