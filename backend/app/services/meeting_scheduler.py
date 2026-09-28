"""Meeting Scheduler Service.

Runs continuous background polling cycles, queries upcoming calendar events,
and coordinates with the MeetingDispatcher to trigger join actions at meeting start times.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.config import Settings, settings
from app.models.scheduler import PollCycleResult, SchedulerStatusResponse, MeetingStatus
from app.services.meeting_dispatcher import MeetingDispatcher, meeting_dispatcher
from app.services.meeting_watcher import MeetingWatcher, meeting_watcher

logger = logging.getLogger(__name__)


class MeetingSchedulerService:
    """Coordinates background polling and scheduled dispatch execution."""

    def __init__(
        self,
        watcher: Optional[MeetingWatcher] = None,
        dispatcher: Optional[MeetingDispatcher] = None,
        app_settings: Optional[Settings] = None,
    ):
        """Initialize scheduler with dependencies and configuration."""
        self.watcher = watcher or meeting_watcher
        self.dispatcher = dispatcher or meeting_dispatcher
        self.settings = app_settings or settings
        self._is_running = False
        self._task: Optional[asyncio.Task] = None
        self._last_poll_time: Optional[datetime] = None
        self._last_poll_result: Optional[str] = None

    @property
    def is_running(self) -> bool:
        """Return whether the background polling task is currently active."""
        return self._is_running and self._task is not None and not self._task.done()

    def get_status(self) -> SchedulerStatusResponse:
        """Return diagnostic status metrics of the scheduler."""
        jobs = self.dispatcher.get_all_jobs()
        scheduled_count = len([j for j in jobs if j.status == MeetingStatus.SCHEDULED])
        triggered_count = len([j for j in jobs if j.status == MeetingStatus.TRIGGERED])
        completed_count = len([j for j in jobs if j.status == MeetingStatus.COMPLETED])

        return SchedulerStatusResponse(
            is_running=self.is_running,
            poll_interval_seconds=self.settings.poll_interval_seconds,
            join_buffer_seconds=self.settings.join_buffer_seconds,
            total_tracked_jobs=len(jobs),
            scheduled_jobs_count=scheduled_count,
            triggered_jobs_count=triggered_count,
            completed_jobs_count=completed_count,
            last_poll_time_utc=self._last_poll_time,
            last_poll_result=self._last_poll_result,
        )

    async def poll_and_dispatch_cycle(
        self,
        target_user: Optional[str] = None,
        now_utc: Optional[datetime] = None,
    ) -> PollCycleResult:
        """Execute a single polling and dispatch pass.

        1. Poll upcoming Teams meetings via MeetingWatcher.
        2. Register/update discovered meetings in MeetingDispatcher.
        3. Evaluate and trigger any meetings that have entered their join window.

        Returns:
            PollCycleResult: Structured summary of the execution cycle.
        """
        current_time = now_utc or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        self._last_poll_time = current_time

        try:
            # 1. Query upcoming meetings from calendar
            watch_resp = await self.watcher.poll_upcoming_teams_meetings(
                user_id_or_email=target_user,
                lookahead_minutes=self.settings.meeting_lookahead_minutes,
            )

            if not watch_resp.success:
                msg = f"Calendar poll failed: {watch_resp.error}"
                self._last_poll_result = msg
                logger.warning(msg)
                return PollCycleResult(
                    success=False,
                    polled_at_utc=current_time,
                    events_found=0,
                    teams_meetings_found=0,
                    newly_scheduled=0,
                    newly_dispatched=0,
                    message=msg,
                )

            # 2. Register with dispatcher
            existing_job_ids = {j.event_id for j in self.dispatcher.get_all_jobs()}
            updated_jobs = self.dispatcher.register_or_update_meetings(
                meetings=watch_resp.meetings,
                now_utc=current_time,
            )
            newly_scheduled = sum(1 for j in updated_jobs if j.event_id not in existing_job_ids)

            # 3. Evaluate due meetings
            dispatched = await self.dispatcher.evaluate_and_dispatch_due_meetings(now_utc=current_time)
            dispatched_ids = [j.event_id for j in dispatched]

            summary_msg = (
                f"Poll cycle OK: {watch_resp.total_events_retrieved} total events, "
                f"{watch_resp.teams_meetings_count} Teams meetings, "
                f"{newly_scheduled} newly scheduled, {len(dispatched)} dispatched for join."
            )
            self._last_poll_result = summary_msg
            logger.info(summary_msg)

            return PollCycleResult(
                success=True,
                polled_at_utc=current_time,
                events_found=watch_resp.total_events_retrieved,
                teams_meetings_found=watch_resp.teams_meetings_count,
                newly_scheduled=newly_scheduled,
                newly_dispatched=len(dispatched),
                dispatched_meeting_ids=dispatched_ids,
                message=summary_msg,
            )

        except Exception as exc:  # pylint: disable=broad-except
            err_msg = f"Unexpected error in scheduler poll cycle: {str(exc)}"
            self._last_poll_result = err_msg
            logger.exception(err_msg)
            return PollCycleResult(
                success=False,
                polled_at_utc=current_time,
                events_found=0,
                teams_meetings_found=0,
                newly_scheduled=0,
                newly_dispatched=0,
                message=err_msg,
            )

    async def _run_loop(self) -> None:
        """Internal background loop running at poll_interval_seconds."""
        logger.info(
            "Starting MeetingScheduler background loop (poll interval: %ds, join buffer: %ds)",
            self.settings.poll_interval_seconds,
            self.settings.join_buffer_seconds,
        )
        while self._is_running:
            try:
                await self.poll_and_dispatch_cycle()
            except asyncio.CancelledError:
                logger.info("MeetingScheduler loop received cancellation signal.")
                break
            except Exception as exc:  # pylint: disable=broad-except
                logger.exception("Error in scheduler loop execution: %s", exc)

            try:
                await asyncio.sleep(self.settings.poll_interval_seconds)
            except asyncio.CancelledError:
                break
        logger.info("MeetingScheduler background loop stopped.")

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
        """Start the background scheduler task if not already running."""
        if self.is_running:
            logger.warning("MeetingScheduler is already running.")
            return False

        self._is_running = True

        try:
            running_loop = loop or asyncio.get_running_loop()
            self._task = running_loop.create_task(self._run_loop())
            logger.info("MeetingScheduler background task spawned successfully.")
        except RuntimeError:
            # No active event loop in current thread
            logger.info("MeetingScheduler enabled (awaiting event loop).")

        return True

    def stop(self) -> bool:
        """Stop the background scheduler task gracefully.

        Returns True if a running task was found and stopped, False otherwise.
        """
        if not self.is_running:
            self._is_running = False
            return False

        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("MeetingScheduler background task cancellation requested.")
            return True

        return False



# Reusable singleton instance
meeting_scheduler = MeetingSchedulerService()
