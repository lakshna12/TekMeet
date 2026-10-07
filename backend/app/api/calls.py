import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta

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
    "/join",
    summary="Join a Teams meeting by URL",
    status_code=status.HTTP_201_CREATED,
)
async def join_meeting_endpoint(payload: dict = Body(...)):
    """Initiate bot join into a Microsoft Teams meeting given a join_url."""
    join_url = payload.get("join_url") or payload.get("joinUrl") or payload.get("url")
    if not join_url:
        raise HTTPException(status_code=400, detail="Missing required 'join_url' field in request body.")

    event_id = payload.get("event_id") or payload.get("eventId")
    if not event_id:
        import uuid
        event_id = f"event_{uuid.uuid4().hex[:8]}"

    from app.services.graph_calling_service import graph_calling_service
    from app.models.call import CallState, CallRecord

    # Prevent duplicate join requests for already active/recording calls
    for existing_job in meeting_dispatcher.get_jobs_by_status(MeetingStatus.TRIGGERED):
        if existing_job.join_url and join_url and existing_job.join_url.strip() == join_url.strip():
            logger.info("[CallsAPI] Meeting is already actively joined (call_id: %s). Skipping duplicate join.", existing_job.call_id)
            return CallRecord(
                call_id=existing_job.call_id,
                event_id=existing_job.event_id,
                status=CallState.ESTABLISHED,
                scenario="app_hosted" if settings.use_app_hosted_media else "service_hosted",
                is_active=True,
            )

    record = await graph_calling_service.join_meeting(join_url, event_id)

    if record.call_id:
        now = datetime.now(timezone.utc)
        job = ScheduledMeetingJob(
            event_id=event_id,
            subject=payload.get("subject") or "Teams Meeting",
            start_time=now,
            end_time=now + timedelta(hours=4),
            call_id=record.call_id,
            join_url=join_url,
            status=MeetingStatus.TRIGGERED,
        )
        meeting_dispatcher._jobs[event_id] = job

    return record


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
        result_info = resource_data.get("resultInfo") or item.get("resultInfo")

        if result_info:
            logger.info(
                "[GRAPH Webhook] ResultInfo for call %s | Code: %s | Subcode: %s | Message: %s",
                call_id or "N/A",
                result_info.get("code"),
                result_info.get("subcode"),
                result_info.get("message"),
            )

        logger.info(
            "[GRAPH Webhook] Call state transition | Call ID: %s | State: %s | ChangeType: %s",
            call_id or "N/A",
            call_state.upper() if call_state else "UNKNOWN",
            change_type or "N/A",
        )

        if call_state in ("terminated", "deleted") or change_type == "deleted":
            logger.info(
                "[GRAPH] Call termination received\n"
                "[GRAPH] Call ID: %s",
                call_id or "N/A",
            )
            job = None
            if call_id:
                job = meeting_dispatcher.get_job_by_call_id(call_id)
            if not job:
                active_jobs = meeting_dispatcher.get_jobs_by_status(MeetingStatus.TRIGGERED)
                if active_jobs:
                    job = active_jobs[0]
            if not job:
                # Fallback job creation so pipeline is never lost
                import uuid
                fallback_event_id = f"event_{uuid.uuid4().hex[:8]}"
                job = ScheduledMeetingJob(
                    event_id=fallback_event_id,
                    subject="Teams Meeting",
                    start_time=datetime.now(timezone.utc),
                    end_time=datetime.now(timezone.utc),
                    call_id=call_id,
                    status=MeetingStatus.TRIGGERED,
                )
                meeting_dispatcher._jobs[job.event_id] = job

            completed_job = meeting_dispatcher.handle_actual_meeting_end(job, call_id=call_id)
            if completed_job:
                asyncio.create_task(meeting_dispatcher.trigger_phase5_pipeline(completed_job))
                triggered_events.append(completed_job.event_id)

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
    if not job:
        import uuid
        fallback_event_id = event_id or f"event_{uuid.uuid4().hex[:8]}"
        job = ScheduledMeetingJob(
            event_id=fallback_event_id,
            subject="Teams Meeting",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            call_id=call_id,
            status=MeetingStatus.TRIGGERED,
        )
        meeting_dispatcher._jobs[job.event_id] = job

    completed_job = meeting_dispatcher.handle_actual_meeting_end(job, call_id=call_id)
    if completed_job:
        asyncio.create_task(meeting_dispatcher.trigger_phase5_pipeline(completed_job, file_path_or_name=file_name))
    logger.info("[CallsAPI Webhook] Recording-stopped callback acknowledged for event %s (file: %s)", job.event_id if job else "N/A", file_name)
    return {"status": "accepted", "event_id": job.event_id if job else None, "message": "Recording stopped accepted"}



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

