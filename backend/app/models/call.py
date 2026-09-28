"""Call state and record data models for TekMeet Phase 2.

Tracks active Graph Cloud Communications API calls and their lifecycle state.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CallState(str, Enum):
    """Lifecycle state values returned by the Graph Cloud Communications Calling API."""

    ESTABLISHING = "establishing"
    ESTABLISHED = "established"
    HOLD = "hold"
    TRANSFERRING = "transferring"
    TRANSFER_ACCEPTED = "transferAccepted"
    REDIRECTING = "redirecting"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    # Local-only states (not Graph values)
    PENDING = "pending"       # join request sent, awaiting 201 response
    FAILED = "failed"         # Graph API returned an error


class CallRecord(BaseModel):
    """Tracks a single Graph Cloud Communications API call initiated by the bot."""

    call_id: Optional[str] = Field(None, description="Graph-assigned call resource ID (set after 201 Created)")
    event_id: str = Field(..., description="TekMeet ScheduledMeetingJob event_id this call is linked to")
    join_url: str = Field(..., description="Original Teams join URL used to initiate the call")
    state: CallState = Field(default=CallState.PENDING, description="Current lifecycle state")
    started_at: datetime = Field(..., description="UTC timestamp when join was initiated")
    established_at: Optional[datetime] = Field(None, description="UTC timestamp when call state became 'established'")
    ended_at: Optional[datetime] = Field(None, description="UTC timestamp when the call was terminated")
    error_code: Optional[str] = Field(None, description="Graph error code if join failed")
    error_message: Optional[str] = Field(None, description="Human-readable error description if join failed")
    http_status: Optional[int] = Field(None, description="HTTP status code returned by Graph POST /communications/calls")
