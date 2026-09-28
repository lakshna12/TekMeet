"""Scheduler API router for managing background polling and job lifecycle."""

from typing import List, Optional
from fastapi import APIRouter, Query, status

from app.models.scheduler import (
    MeetingStatus,
    PollCycleResult,
    ScheduledMeetingJob,
    SchedulerStatusResponse,
)
from app.services.meeting_dispatcher import meeting_dispatcher
from app.services.meeting_scheduler import meeting_scheduler

router = APIRouter(prefix="/api/v1/scheduler", tags=["Scheduler & Auto-Dispatch"])


@router.get(
    "/status",
    response_model=SchedulerStatusResponse,
    summary="Get background meeting scheduler status",
)
def get_scheduler_status() -> SchedulerStatusResponse:
    """Retrieve runtime diagnostics for the background meeting scheduler."""
    return meeting_scheduler.get_status()


@router.get(
    "/jobs",
    response_model=List[ScheduledMeetingJob],
    summary="Get all tracked scheduled meeting jobs",
)
def get_scheduled_jobs(
    status_filter: Optional[MeetingStatus] = Query(
        None,
        description="Optional filter by job lifecycle status (e.g. scheduled, triggered, completed)",
    ),
) -> List[ScheduledMeetingJob]:
    """Retrieve all tracked meeting jobs, optionally filtered by status."""
    if status_filter:
        return meeting_dispatcher.get_jobs_by_status(status_filter)
    return meeting_dispatcher.get_all_jobs()


@router.post(
    "/start",
    summary="Start continuous background scheduler",
)
async def start_scheduler():
    """Launch the continuous background meeting polling and dispatch loop."""
    started = meeting_scheduler.start()
    return {
        "status": "started" if started else "already_running",
        "is_running": meeting_scheduler.is_running,
        "poll_interval_seconds": meeting_scheduler.settings.poll_interval_seconds,
    }


@router.post(
    "/stop",
    summary="Stop continuous background scheduler",
)
async def stop_scheduler():
    """Stop the background polling loop gracefully."""
    stopped = meeting_scheduler.stop()
    return {
        "status": "stopped" if stopped else "already_stopped",
        "is_running": meeting_scheduler.is_running,
    }


@router.post(
    "/poll-now",
    response_model=PollCycleResult,
    summary="Trigger an immediate poll and dispatch cycle",
)
async def trigger_poll_now(
    target_user: Optional[str] = Query(
        None,
        description="Target mailbox or UPN (defaults to AZURE_BOT_USER_EMAIL)",
    ),
) -> PollCycleResult:
    """Manually invoke a single calendar polling and meeting evaluation cycle."""
    return await meeting_scheduler.poll_and_dispatch_cycle(target_user=target_user)
