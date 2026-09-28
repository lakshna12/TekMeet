"""Meetings API router for querying upcoming calendar events and past meeting summaries."""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, status

from app.core.config import settings
from app.models.meeting import (
    MeetingEvent,
    MeetingInfo,
    MeetingSummaryViewResponse,
    PastMeetingItem,
    RecordingInfo,
    UpcomingMeetingsResponse,
)
from app.services.calendar_service import CalendarServiceError, calendar_service
from app.services.meeting_dispatcher import meeting_dispatcher
from app.services.meeting_watcher import meeting_watcher
from app.services.storage_service import storage_service

router = APIRouter(prefix="/api/v1/meetings", tags=["Meetings & Calendar"])


@router.get(
    "",
    response_model=List[PastMeetingItem],
    summary="List all past and processed meetings",
)
def list_past_meetings() -> List[PastMeetingItem]:
    """Return a list of previously processed or tracked meetings."""
    event_ids = set()
    for summary in storage_service.get_all_summaries():
        event_ids.add(summary.event_id)
    for transcript in storage_service.get_all_transcripts():
        event_ids.add(transcript.event_id)
    for delivery in storage_service.get_all_delivery_records():
        event_ids.add(delivery.event_id)
    for job in meeting_dispatcher.get_all_jobs():
        event_ids.add(job.event_id)

    items: List[PastMeetingItem] = []
    for eid in sorted(event_ids):
        job = meeting_dispatcher.get_job(eid)
        transcript = storage_service.get_transcript(eid)
        summary = storage_service.get_summary(eid)
        delivery = storage_service.get_delivery_record(eid)

        subject = job.subject if job else f"Meeting {eid[:8]}"
        start_dt = job.start_time if job else (transcript.created_at if transcript else (summary.created_at if summary else None))
        end_dt = job.end_time if job else None
        email = (job.organizer_email if job else None) or (delivery.recipient_email if delivery else None)

        item = PastMeetingItem(
            event_id=eid,
            subject=subject,
            start_time=start_dt,
            end_time=end_dt,
            organizer_email=email,
            has_transcript=transcript is not None,
            has_summary=summary is not None,
            delivery_status=delivery.status if delivery else None,
            processed_at=summary.created_at if summary else (transcript.created_at if transcript else None),
        )
        items.append(item)

    return items


@router.get(
    "/upcoming",
    response_model=UpcomingMeetingsResponse,
    summary="Get upcoming Microsoft Teams meetings",
)
async def get_upcoming_teams_meetings(
    user_email: Optional[str] = Query(
        None,
        description="Target mailbox email or UPN (defaults to AZURE_BOT_USER_EMAIL from settings)",
    ),
    lookahead_minutes: Optional[int] = Query(
        None,
        ge=1,
        le=10080,  # up to 7 days
        description="Lookahead window in minutes (defaults to MEETING_LOOKAHEAD_MINUTES, e.g. 1440 for 24h)",
    ),
    only_unseen: bool = Query(
        False,
        description="If True, filters out meetings already marked as seen during polling",
    ),
) -> UpcomingMeetingsResponse:
    """Retrieve upcoming Microsoft Teams meetings within the lookahead window."""
    result = await meeting_watcher.poll_upcoming_teams_meetings(
        user_id_or_email=user_email,
        lookahead_minutes=lookahead_minutes,
        only_unseen=only_unseen,
    )
    if not result.success and result.error:
        # If target user was missing or Graph failed, return structured response or bad request
        if "target mailbox" in result.error.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.error,
            )
    return result


@router.get(
    "/calendar-events",
    response_model=List[MeetingEvent],
    summary="Get all raw calendar events in lookahead window",
)
async def get_all_calendar_events(
    user_email: Optional[str] = Query(
        None,
        description="Target mailbox email or UPN (defaults to AZURE_BOT_USER_EMAIL)",
    ),
    lookahead_minutes: Optional[int] = Query(
        None,
        ge=1,
        le=10080,
        description="Lookahead window in minutes",
    ),
) -> List[MeetingEvent]:
    """Retrieve all calendar events (both Teams and non-Teams) for diagnostic purposes."""
    try:
        from datetime import datetime, timedelta, timezone

        now_utc = datetime.now(timezone.utc)
        mins = lookahead_minutes or settings.meeting_lookahead_minutes
        end_utc = now_utc + timedelta(minutes=mins)

        events = await calendar_service.get_calendar_events(
            user_id_or_email=user_email,
            start_time=now_utc,
            end_time=end_utc,
        )
        return events
    except CalendarServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code or status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=exc.message,
        ) from exc


@router.get(
    "/{event_id}/summary",
    response_model=MeetingSummaryViewResponse,
    summary="Get complete past meeting summary and delivery view",
)
def get_past_meeting_summary(event_id: str) -> MeetingSummaryViewResponse:
    """Retrieve full past meeting details including transcript, AI summary, recording URL, and delivery status."""
    job = meeting_dispatcher.get_job(event_id)
    transcript = storage_service.get_transcript(event_id)
    summary = storage_service.get_summary(event_id)
    delivery = storage_service.get_delivery_record(event_id)

    if not job and not transcript and not summary and not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No meeting found with event_id '{event_id}'.",
        )

    # Resolve meeting metadata
    if job:
        meeting_info = MeetingInfo(
            subject=job.subject,
            start_time=job.start_time,
            end_time=job.end_time,
            organizer_email=job.organizer_email,
        )
    else:
        email = delivery.recipient_email if delivery else None
        meeting_info = MeetingInfo(
            subject=f"Meeting {event_id[:8]}",
            organizer_email=email,
        )

    # Resolve recording information & download URL
    file_name = transcript.recording_file if (transcript and transcript.recording_file) else f"meeting_{event_id}.wav"
    download_url = f"{settings.media_worker_url}/api/media/recordings/download?file={file_name}"
    recording_info = RecordingInfo(
        file_name=file_name,
        download_url=download_url,
    )

    return MeetingSummaryViewResponse(
        event_id=event_id,
        meeting=meeting_info,
        transcript=transcript,
        summary=summary,
        recording=recording_info,
        delivery=delivery,
    )

