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
            "[DISPATCHER] Starting meeting join\n"
            "[DISPATCHER] Event ID: %s\n"
            "[DISPATCHER] Join URL detected\n"
            "[GRAPH] Joining Teams meeting\n"
            "[GRAPH] Event ID: %s",
            job.event_id,
            job.event_id,
        )

        record = await self.calling_service.join_meeting(
            join_url=job.join_url,
            event_id=job.event_id,
        )

        if record.state not in (CallState.FAILED,) and record.call_id:
            # Success — store the Graph call ID back onto the job for tracking
            job.call_id = record.call_id
            state_str = record.state.value if hasattr(record.state, "value") else str(record.state)
            logger.info(
                "[GRAPH] Teams join successful\n"
                "[GRAPH] Call ID: %s\n"
                "[GRAPH] Initial call state: %s\n"
                "[DISPATCHER] Graph join successful\n"
                "[DISPATCHER] Call ID: %s",
                record.call_id,
                state_str,
                record.call_id,
            )

            # In Phase 3 (App-Hosted Media), trigger MediaWorker to start recording audio to a .wav file
            from app.core.config import settings
            import httpx
            if settings.use_app_hosted_media:
                rec_path = f"meeting_{job.event_id}.wav"
                logger.info(
                    "[RECORDING] Starting recording\n"
                    "[RECORDING] Event ID: %s\n"
                    "[RECORDING] Call ID: %s\n"
                    "[DISPATCHER] Starting MediaWorker recording\n"
                    "[DISPATCHER] Event ID: %s\n"
                    "[DISPATCHER] Call ID: %s",
                    job.event_id,
                    record.call_id,
                    job.event_id,
                    record.call_id,
                )
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        rec_url = f"{settings.media_worker_url}/api/media/recording/start?eventId={job.event_id}&callId={record.call_id}"
                        resp = await client.get(rec_url)
                        if resp.status_code == 200:
                            logger.info(
                                "[DISPATCHER] Recording started successfully\n"
                                "[DISPATCHER] Recording path: %s",
                                rec_path,
                            )
                        else:
                            logger.error(
                                "[MEDIA] Recording start FAILED\n"
                                "[MEDIA] HTTP status: %d\n"
                                "[MEDIA] Error: %s",
                                resp.status_code,
                                resp.text,
                            )
                except Exception as exc:
                    logger.error(
                        "[MEDIA] Recording start FAILED\n"
                        "[MEDIA] Error: %s\n"
                        "[DISPATCHER] ERROR: Failed to start MediaWorker recording - %s",
                        exc,
                        exc,
                    )

            return True

        err_detail = f"http_status: {record.http_status} | error: {record.error_code} - {record.error_message}"
        logger.error(
            "[GRAPH] ERROR: Failed to join Teams meeting - %s\n"
            "[DISPATCHER] ERROR: Graph join failed - %s",
            err_detail,
            err_detail,
        )
        return False


# Reusable singleton
teams_call_join_handler = TeamsCallJoinHandler()
