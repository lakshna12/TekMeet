import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Body

from app.models.call import CallRecord, CallState
from app.models.scheduler import ScheduledMeetingJob, MeetingStatus
from app.services.meeting_dispatcher import meeting_dispatcher

logger = logging.getLogger(__name__)

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


@router.post(
    "/callback",
    summary="Graph Calling API notification callback webhook endpoint",
    status_code=status.HTTP_202_ACCEPTED,
)
@router.post(
    "/notifications",
    summary="Graph Calling API notification callback webhook endpoint (alias)",
    status_code=status.HTTP_202_ACCEPTED,
)
async def handle_graph_call_notification(notification: dict = Body(...)):
    """Receive call state notifications from Microsoft Graph.

    When a call state changes to 'terminated' or 'deleted', automatically finalize
    recording and trigger the Phase 5 End-to-End pipeline in the background.
    """
    logger.info("[CallsAPI Webhook] Received Graph call notification payload: %s", notification)

    values = notification.get("value", [notification]) if isinstance(notification, dict) else []
    triggered_events = []

    for item in values:
        resource_data = item.get("resourceData", {}) if isinstance(item, dict) else {}
        call_id = resource_data.get("id") or item.get("id")
        call_state = (resource_data.get("state") or item.get("state") or "").lower()
        change_type = (item.get("changeType") or "").lower()

        if call_state in ("terminated", "deleted") or change_type == "deleted":
            logger.info(
                "[CallsAPI Webhook] Call %s transitioned to '%s' (changeType: '%s'). Triggering automatic post-meeting pipeline.",
                call_id, call_state, change_type
            )
            job = None
            if call_id:
                job = meeting_dispatcher.get_job_by_call_id(call_id)
            if not job:
                active_jobs = meeting_dispatcher.get_jobs_by_status(MeetingStatus.TRIGGERED)
                if active_jobs:
                    job = active_jobs[0]

            if job:
                job.status = MeetingStatus.COMPLETED
                asyncio.create_task(meeting_dispatcher.trigger_phase5_pipeline(job))
                triggered_events.append(job.event_id)

    return {
        "status": "accepted",
        "message": f"Processed notification. Triggered automatic pipeline for {len(triggered_events)} meetings.",
        "triggered_events": triggered_events,
    }


@router.post(
    "/recording-stopped",
    summary="MediaWorker recording finalized webhook callback endpoint",
    status_code=status.HTTP_202_ACCEPTED,
)
async def handle_recording_stopped_callback(payload: dict = Body(...)):
    """Receive notification from MediaWorker when a recording session finishes."""
    logger.info("[CallsAPI Webhook] Received MediaWorker recording-stopped callback: %s", payload)
    call_id = payload.get("callId")
    event_id = payload.get("eventId")
    file_name = payload.get("filePath") or payload.get("fileName")

    job = None
    if event_id:
        job = meeting_dispatcher.get_job(event_id)
    if not job and call_id:
        job = meeting_dispatcher.get_job_by_call_id(call_id)
    if not job:
        active_jobs = meeting_dispatcher.get_jobs_by_status(MeetingStatus.TRIGGERED)
        if active_jobs:
            job = active_jobs[0]

    if job:
        job.status = MeetingStatus.COMPLETED
        asyncio.create_task(meeting_dispatcher.trigger_phase5_pipeline(job, file_path_or_name=file_name))
        return {"status": "accepted", "event_id": job.event_id, "message": "Phase 5 pipeline started in background"}

    return {"status": "ignored", "message": "No matching meeting job found"}


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

