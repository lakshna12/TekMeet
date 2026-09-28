"""Calls API router — Phase 2.

Provides endpoints to inspect active and past call records initiated
by the bot via the Graph Cloud Communications Calling API.
"""

from typing import Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from app.models.call import CallRecord, CallState
from app.models.scheduler import ScheduledMeetingJob, MeetingStatus
from app.services.meeting_dispatcher import meeting_dispatcher

router = APIRouter(prefix="/api/v1/calls", tags=["Calls (Phase 2)"])


def _get_call_records_from_jobs() -> List[CallRecord]:
    """Derive CallRecord summaries from jobs that have been dispatched (have a call_id)."""
    records = []
    for job in meeting_dispatcher.get_all_jobs():
        if job.call_id:
            # Infer call state from job lifecycle status
            if job.status == MeetingStatus.TRIGGERED:
                call_state = CallState.ESTABLISHED
            elif job.status == MeetingStatus.COMPLETED:
                call_state = CallState.TERMINATED
            elif job.status == MeetingStatus.FAILED:
                call_state = CallState.FAILED
            else:
                call_state = CallState.ESTABLISHING

            records.append(
                CallRecord(
                    call_id=job.call_id,
                    event_id=job.event_id,
                    join_url=job.join_url or "",
                    state=call_state,
                    started_at=job.dispatched_at or job.updated_at,
                    ended_at=job.updated_at if job.status == MeetingStatus.COMPLETED else None,
                )
            )
    return records


@router.get(
    "",
    response_model=List[CallRecord],
    summary="List all call records for dispatched meetings",
)
def list_call_records() -> List[CallRecord]:
    """Return call records for all meetings that have been dispatched to join.

    Records are derived from the in-memory dispatcher job registry — jobs that
    have a call_id set from a successful Graph Calling API response.
    """
    return _get_call_records_from_jobs()


@router.get(
    "/{call_id}",
    response_model=CallRecord,
    summary="Get call record by Graph call ID",
)
def get_call_record(call_id: str) -> CallRecord:
    """Retrieve a specific call record by its Graph-assigned call ID."""
    for record in _get_call_records_from_jobs():
        if record.call_id == call_id:
            return record
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No call record found with call_id '{call_id}'.",
    )
