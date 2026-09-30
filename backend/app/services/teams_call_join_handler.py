"""Teams Call Join Handler for Phase 2.

Implements the MeetingJoinHandler protocol using GraphCallingService to join
a scheduled Teams meeting via the Graph Cloud Communications API.

This handler is injected into MeetingDispatcher as the replacement for
DefaultMockJoinHandler. When the dispatcher detects a meeting is due,
it calls handle_join(), which:

  1. Validates the join URL is present.
  2. Delegates to GraphCallingService.join_meeting(). 
  3. Stores the returned Graph call_id on the ScheduledMeetingJob.
  4. Returns True on success, False on any failure.
"""

import logging
from typing import Optional

from app.models.scheduler import MeetingStatus, ScheduledMeetingJob
from app.services.graph_calling_service import GraphCallingService, graph_calling_service
from app.models.call import CallState

logger = logging.getLogger(__name__)


class TeamsCallJoinHandler:
    """Production MeetingJoinHandler that joins Teams meetings via Graph Calling API."""

    def __init__(self, calling_service: Optional[GraphCallingService] = None):
        self.calling_service = calling_service or graph_calling_service

    async def handle_join(self, job: ScheduledMeetingJob) -> bool:
        """Join a Teams meeting using Graph Cloud Communications API.

        Args:
            job: The scheduled meeting job to join. Must have a valid join_url.

        Returns:
            True if the Graph API accepted the join (HTTP 201 Created).
            False on any error — the dispatcher will mark the job FAILED.
        """
        if not job.join_url:
            logger.error(
                "[TeamsCallJoinHandler] Cannot join meeting '%s' (%s): no join_url available.",
                job.subject,
                job.event_id,
            )
            return False

        if job.call_id or job.status == MeetingStatus.TRIGGERED:
            logger.info(
                "[Duplicate Join Ignored] Meeting '%s' (event_id: %s) is already joined/triggered (call_id: %s).",
                job.subject,
                job.event_id,
                job.call_id,
            )
            return True

        logger.info(
            "[TeamsCallJoinHandler] Initiating join for meeting '%s' (event_id: %s, start: %s)",
            job.subject,
            job.event_id,
            job.start_time.isoformat(),
        )

        record = await self.calling_service.join_meeting(
            join_url=job.join_url,
            event_id=job.event_id,
        )

        if record.state not in (CallState.FAILED,) and record.call_id:
            # Success — store the Graph call ID back onto the job for tracking
            job.call_id = record.call_id
            logger.info(
                "[TeamsCallJoinHandler] Successfully joined meeting '%s' | call_id: %s | state: %s",
                job.subject,
                record.call_id,
                record.state,
            )

            # In Phase 3 (App-Hosted Media), trigger MediaWorker to start recording audio to a .wav file
            from app.core.config import settings
            import httpx
            if settings.use_app_hosted_media:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        rec_url = f"{settings.media_worker_url}/api/media/recording/start?eventId={job.event_id}&callId={record.call_id}"
                        resp = await client.get(rec_url)
                        logger.info("[TeamsCallJoinHandler] Triggered MediaWorker StartRecording: %s", resp.text)
                except Exception as exc:
                    logger.warning("[TeamsCallJoinHandler] Failed to trigger MediaWorker StartRecording: %s", exc)

            return True

        logger.error(
            "[TeamsCallJoinHandler] Failed to join meeting '%s' | http_status: %s | error: %s - %s",
            job.subject,
            record.http_status,
            record.error_code,
            record.error_message,
        )
        return False


# Reusable singleton
teams_call_join_handler = TeamsCallJoinHandler()
