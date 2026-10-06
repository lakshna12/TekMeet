"""Meeting Dispatcher Service.

Evaluates scheduled Teams meetings against current time, manages meeting lifecycle transitions,
and dispatches due meetings to the call-join handler (prepared for Phase 2 integration).
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Set, Union, runtime_checkable

import httpx

from app.core.config import Settings, settings
from app.models.meeting import MeetingEvent
from app.models.scheduler import MeetingStatus, ScheduledMeetingJob
from app.services.phase5_pipeline import Phase5Pipeline, phase5_pipeline, Phase5PipelineResult

logger = logging.getLogger(__name__)


@runtime_checkable
class MeetingJoinHandler(Protocol):
    """Interface for handling meeting join triggers.

    Phase 2 will implement this protocol with the Graph Cloud Communications Calling Service.
    """

    async def handle_join(self, job: ScheduledMeetingJob) -> bool:
        """Execute or queue the join action for a scheduled meeting.

        Args:
            job: The scheduled meeting job details.

        Returns:
            bool: True if the dispatch/join succeeded, False otherwise.
        """
        ...


class DefaultMockJoinHandler:
    """Default join handler used in Phase 1 for logging and recording dispatch events."""

    async def handle_join(self, job: ScheduledMeetingJob) -> bool:
        logger.info(
            "[DISPATCH TRIGGER] Meeting '%s' (ID: %s) is DUE. Start: %s, Join URL: %s. "
            "(Prepared for Phase 2 Teams Call Join)",
            job.subject,
            job.event_id,
            job.start_time.isoformat(),
            job.join_url,
        )
        return True


class MeetingDispatcher:
    """Manages scheduled meeting lifecycle and dispatches due meetings."""

    def __init__(
        self,
        join_handler: Optional[MeetingJoinHandler] = None,
        pipeline: Optional[Phase5Pipeline] = None,
        app_settings: Optional[Settings] = None,
    ):
        """Initialize dispatcher with join handler, pipeline, and settings."""
        self.join_handler: MeetingJoinHandler = join_handler or DefaultMockJoinHandler()
        self.pipeline: Phase5Pipeline = pipeline or phase5_pipeline
        self.settings = app_settings or settings
        # In-memory registry of tracked meeting jobs keyed by event_id
        self._jobs: Dict[str, ScheduledMeetingJob] = {}
        # Background task set to prevent premature garbage collection of pipeline tasks
        self._pipeline_tasks: Set[asyncio.Task] = set()
        # Active pipeline tracker to prevent duplicate concurrent runs for the same event
        self._running_pipelines: Set[str] = set()

    def get_all_jobs(self) -> List[ScheduledMeetingJob]:
        """Return all tracked meeting jobs sorted by scheduled start time."""
        jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.start_time)
        return jobs

    def get_job(self, event_id: str) -> Optional[ScheduledMeetingJob]:
        """Retrieve a specific meeting job by event ID."""
        return self._jobs.get(event_id)

    def get_job_by_call_id(self, call_id: str) -> Optional[ScheduledMeetingJob]:
        """Retrieve a specific meeting job by its Graph call ID."""
        for job in self._jobs.values():
            if job.call_id == call_id:
                return job
        return None

    def get_jobs_by_status(self, status: MeetingStatus) -> List[ScheduledMeetingJob]:
        """Retrieve all jobs with a given lifecycle status."""
        return [job for job in self._jobs.values() if job.status == status]

    def clear_jobs(self) -> None:
        """Clear all in-memory jobs."""
        self._jobs.clear()

    def register_or_update_meetings(
        self,
        meetings: List[MeetingEvent],
        now_utc: Optional[datetime] = None,
    ) -> List[ScheduledMeetingJob]:
        """Register newly discovered calendar meetings or update existing ones.

        Args:
            meetings: List of MeetingEvent models from CalendarService / MeetingWatcher.
            now_utc: Optional current UTC time for evaluation.

        Returns:
            List[ScheduledMeetingJob]: All updated or newly added jobs.
        """
        current_time = now_utc or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        updated_jobs: List[ScheduledMeetingJob] = []

        for m in meetings:
            if not m.event_id:
                continue

            # Default start/end fallback if missing
            start_dt = m.start_time or current_time
            end_dt = m.end_time or (start_dt + timedelta(hours=1))

            # Check by event_id or by matching join_url
            existing_job = self._jobs.get(m.event_id)
            if not existing_job and m.join_url:
                for j in self._jobs.values():
                    if j.join_url and j.join_url.strip() == m.join_url.strip():
                        existing_job = j
                        break

            if existing_job:
                # Update details if subject or URL changed
                existing_job.subject = m.subject or existing_job.subject
                existing_job.join_url = m.join_url or existing_job.join_url
                existing_job.start_time = start_dt
                existing_job.end_time = end_dt
                existing_job.updated_at = current_time

                if m.is_cancelled and existing_job.status == MeetingStatus.SCHEDULED:
                    existing_job.status = MeetingStatus.CANCELLED
                    existing_job.dispatch_message = "Cancelled on calendar"

                updated_jobs.append(existing_job)
            else:
                # New meeting detected
                initial_status = MeetingStatus.SCHEDULED
                initial_message = "Scheduled and waiting for join window"

                if m.is_cancelled:
                    initial_status = MeetingStatus.CANCELLED
                    initial_message = "Meeting is cancelled"
                elif current_time > end_dt:
                    initial_status = MeetingStatus.MISSED
                    initial_message = "Meeting was already in the past when detected"

                new_job = ScheduledMeetingJob(
                    event_id=m.event_id,
                    subject=m.subject or "Untitled Meeting",
                    start_time=start_dt,
                    end_time=end_dt,
                    join_url=m.join_url,
                    organizer_email=m.organizer_email,
                    status=initial_status,
                    dispatch_message=initial_message,
                    created_at=current_time,
                    updated_at=current_time,
                )
                self._jobs[m.event_id] = new_job
                updated_jobs.append(new_job)

        return updated_jobs

    async def evaluate_and_dispatch_due_meetings(
        self,
        now_utc: Optional[datetime] = None,
    ) -> List[ScheduledMeetingJob]:
        """Evaluate all tracked jobs against current time and dispatch those due for joining.

        A meeting is due when:
        (start_time - join_buffer_seconds) <= now_utc <= end_time
        and its status is SCHEDULED.

        Returns:
            List[ScheduledMeetingJob]: List of newly dispatched jobs in this evaluation pass.
        """
        current_time = now_utc or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        buffer_seconds = self.settings.join_buffer_seconds
        buffer_delta = timedelta(seconds=buffer_seconds)

        dispatched_jobs: List[ScheduledMeetingJob] = []

        for job in list(self._jobs.values()):
            trigger_window_start = job.start_time - buffer_delta

            if job.status == MeetingStatus.SCHEDULED:
                if trigger_window_start <= current_time <= job.end_time:
                    # Due for join dispatch!
                    logger.info(
                        "Dispatching meeting '%s' (%s) scheduled for %s (current time: %s, buffer: %ds)",
                        job.subject,
                        job.event_id,
                        job.start_time.isoformat(),
                        current_time.isoformat(),
                        buffer_seconds,
                    )
                    try:
                        success = await self.join_handler.handle_join(job)
                        if success:
                            job.status = MeetingStatus.TRIGGERED
                            job.dispatched_at = current_time
                            job.dispatch_message = f"Dispatched at {current_time.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                            job.updated_at = current_time
                            dispatched_jobs.append(job)
                        else:
                            job.status = MeetingStatus.FAILED
                            job.dispatch_message = "Join handler returned failure"
                            job.updated_at = current_time
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.exception("Error executing join handler for meeting %s", job.event_id)
                        job.status = MeetingStatus.FAILED
                        job.dispatch_message = f"Handler exception: {str(exc)}"
                        job.updated_at = current_time

                elif current_time > job.end_time:
                    # Meeting was scheduled but current time is past end time without trigger
                    job.status = MeetingStatus.MISSED
                    job.dispatch_message = "Past end time without dispatch"
                    job.updated_at = current_time

            elif job.status == MeetingStatus.TRIGGERED:
                # Active call state verification via Graph API polling & webhook fallback.
                # If participants manually end/leave the Teams call, Graph GET /communications/calls/{call_id}
                # returns state 'terminated' / 'terminating' or HTTP 404 (deleted call resource).
                is_terminated = False
                if job.call_id:
                    try:
                        from app.services.graph_calling_service import graph_calling_service
                        from app.models.call import CallState
                        cs = await graph_calling_service.get_call_state(job.call_id)
                        if cs == CallState.TERMINATED:
                            is_terminated = True
                            logger.info(
                                "[GRAPH] Active call state check detected call termination for job '%s' (event_id: %s, call_id: %s)",
                                job.subject,
                                job.event_id,
                                job.call_id,
                            )
                    except Exception as exc:
                        logger.debug("Error checking call state for job %s: %s", job.event_id, exc)

                safety_cutoff = job.end_time + timedelta(hours=2)
                if is_terminated or current_time > job.end_time:
                    logger.info("[DISPATCHER] termination_reason=GRAPH_CALL_ENDED")
                    logger.info("[DISPATCHER] safety_cutoff_check=SKIPPED")
                    completed_job = self.handle_actual_meeting_end(job, call_id=job.call_id, actual_end_time=current_time)
                    if completed_job:
                        try:
                            task = asyncio.create_task(self.trigger_phase5_pipeline(completed_job))
                            self._pipeline_tasks.add(task)
                            task.add_done_callback(self._pipeline_tasks.discard)
                        except Exception as exc:
                            logger.warning("Could not spawn Phase 5 pipeline background task for %s: %s", job.event_id, exc)
                elif current_time > safety_cutoff:
                    logger.warning(
                        "[MeetingDispatcher] Safety cutoff fallback reached for meeting '%s' (%s) — 2 hours past scheduled end. Finalizing call.",
                        job.subject,
                        job.event_id,
                    )
                    logger.info("[DISPATCHER] safety_cutoff_check=TRIGGERED")
                    completed_job = self.handle_actual_meeting_end(job, call_id=job.call_id, actual_end_time=current_time)
                    if completed_job:
                        try:
                            task = asyncio.create_task(self.trigger_phase5_pipeline(completed_job))
                            self._pipeline_tasks.add(task)
                            task.add_done_callback(self._pipeline_tasks.discard)
                        except Exception as exc:
                            logger.warning("Could not spawn Phase 5 pipeline background task for safety fallback on %s: %s", job.event_id, exc)

        return dispatched_jobs

    def handle_actual_meeting_end(
        self,
        event_id_or_job: Union[ScheduledMeetingJob, str],
        call_id: Optional[str] = None,
        actual_end_time: Optional[datetime] = None,
    ) -> Optional[ScheduledMeetingJob]:
        """Process actual Teams call termination.

        Calculates actual meeting duration, logs termination details with IST timestamp,
        updates status to COMPLETED, and returns the updated job. Ignores duplicate calls.
        """
        job = event_id_or_job if isinstance(event_id_or_job, ScheduledMeetingJob) else self.get_job(event_id_or_job)
        if not job and call_id:
            job = self.get_job_by_call_id(call_id)

        if not job:
            logger.warning("[MeetingDispatcher] Received termination for unknown event/call (call_id: %s)", call_id)
            return None

        if call_id and not job.call_id:
            job.call_id = call_id

        # Duplicate termination event protection
        if job.status == MeetingStatus.COMPLETED:
            logger.info("[MeetingDispatcher] Meeting '%s' (event_id: %s) is ALREADY completed. Duplicate termination ignored.", job.subject, job.event_id)
            return job

        end_dt = actual_end_time or datetime.now(timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        job.status = MeetingStatus.COMPLETED
        job.updated_at = end_dt
        job.dispatch_message = f"Completed at {end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}"

        start_dt = job.dispatched_at or job.start_time
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        duration_sec = max(0.0, (end_dt - start_dt).total_seconds())
        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        duration_str = f"{mins}m {secs}s ({duration_sec:.1f}s)" if mins > 0 else f"{secs}s ({duration_sec:.1f}s)"

        ist_dt = end_dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
        ist_str = ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")

        logger.info(
            "[MEETING] Meeting ended\n"
            "[MEETING] Event ID: %s\n"
            "[MEETING] Call ID: %s\n"
            "[MEETING] Actual end time (IST): %s\n"
            "[MEETING] Meeting duration: %s",
            job.event_id,
            job.call_id or "N/A",
            ist_str,
            duration_str,
        )

        return job

    async def check_recording_readiness(
        self,
        event_id: str,
        file_path_or_name: Optional[str] = None,
        max_attempts: int = 5,
        delay_seconds: float = 0.5,
    ) -> bool:
        """Check whether the meeting recording file exists and is readable (non-empty).

        Performs a bounded poll (max_attempts * delay_seconds) checking:
          1. Local disk paths (cwd, cwd/recordings/).
          2. MediaWorker HTTP download endpoint if use_app_hosted_media is True.

        Returns True if recording is ready & readable, False if timeout reached.
        """
        filename = file_path_or_name or f"meeting_{event_id}.wav"
        candidate_paths = [
            filename,
            os.path.join(os.getcwd(), filename),
            os.path.join(os.getcwd(), "recordings", filename),
            os.path.join(os.getcwd(), "recordings", f"meeting_{event_id}.mp4"),
            os.path.join(os.getcwd(), f"meeting_{event_id}.mp4"),
        ]

        for attempt in range(1, max_attempts + 1):
            # Check 1: Local disk existence & non-empty
            for p in candidate_paths:
                if os.path.exists(p) and os.path.isfile(p):
                    try:
                        if os.path.getsize(p) > 0:
                            logger.info(
                                "[MeetingDispatcher] Recording file '%s' is ready & readable on disk (%d bytes, attempt %d/%d).",
                                p,
                                os.path.getsize(p),
                                attempt,
                                max_attempts,
                            )
                            return True
                    except OSError:
                        pass

            # Check 2: MediaWorker download endpoint
            if self.settings.use_app_hosted_media:
                download_url = f"{self.settings.media_worker_url}/api/media/recordings/download?file={filename}"
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.get(download_url)
                        if resp.status_code == 200 and len(resp.content) > 0:
                            logger.info(
                                "[MeetingDispatcher] Recording file '%s' is ready on MediaWorker (%d bytes, HTTP 200, attempt %d/%d).",
                                filename,
                                len(resp.content),
                                attempt,
                                max_attempts,
                            )
                            return True
                except Exception as exc:
                    logger.debug("[MeetingDispatcher] MediaWorker readiness check attempt %d/%d failed: %s", attempt, max_attempts, exc)

            if attempt < max_attempts:
                await asyncio.sleep(delay_seconds)

        logger.warning(
            "[MeetingDispatcher] Recording file '%s' was not verified ready after %d attempts (total %.1fs).",
            filename,
            max_attempts,
            max_attempts * delay_seconds,
        )
        return False

    async def trigger_phase5_pipeline(
        self,
        job: ScheduledMeetingJob,
        file_path_or_name: Optional[str] = None,
        force_refresh: bool = False,
        max_readiness_attempts: int = 5,
        readiness_delay_seconds: float = 0.5,
    ) -> Optional[Phase5PipelineResult]:
        """Finalize recording on MediaWorker if needed, wait for recording readiness, then execute Phase 5 E2E pipeline for job."""
        if job.event_id in self._running_pipelines:
            logger.info("[MeetingDispatcher] Phase 5 Pipeline is ALREADY running for event '%s'. Skipping duplicate execution.", job.event_id)
            return None

        self._running_pipelines.add(job.event_id)
        final_rec_path = file_path_or_name or f"meeting_{job.event_id}.wav"
        logger.info(
            "[DISPATCHER] Meeting termination detected\n"
            "[DISPATCHER] Event ID: %s\n"
            "[DISPATCHER] Call ID: %s",
            job.event_id,
            job.call_id or "N/A",
        )
        logger.info("[MEDIA] Recording stop requested")

        try:
            # 1. Finalize recording on MediaWorker
            if self.settings.use_app_hosted_media:
                try:
                    stop_url = f"{self.settings.media_worker_url}/api/media/recording/stop"
                    if job.call_id:
                        stop_url += f"?callId={job.call_id}"
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.get(stop_url)
                        if resp.status_code == 200:
                            try:
                                data = resp.json()
                                fp = data.get("filePath") or data.get("fileName") or data.get("filename")
                                if fp:
                                    final_rec_path = Path(fp).name
                            except Exception:
                                pass
                        if final_rec_path == f"meeting_{job.event_id}.wav" or not Path(final_rec_path).exists():
                            try:
                                list_resp = await client.get(f"{self.settings.media_worker_url}/api/media/recordings/list")
                                if list_resp.status_code == 200:
                                    files = list_resp.json()
                                    safe_eid = re.sub(r'[^a-zA-Z0-9_-]', '_', job.event_id)[:20] if job.event_id else ""
                                    safe_cid = re.sub(r'[^a-zA-Z0-9_-]', '_', job.call_id)[:12] if job.call_id else ""
                                    for f in sorted(files, key=lambda x: x.get("createdAt", ""), reverse=True):
                                        fname = f.get("name", "")
                                        if fname.endswith(".wav") and f.get("sizeBytes", 0) > 100:
                                            if (safe_cid and safe_cid in fname) or (safe_eid and safe_eid in fname):
                                                final_rec_path = fname
                                                break
                            except Exception:
                                pass

                        logger.info(
                            "[DISPATCHER] Recording stop response received (status %d)\n"
                            "[DISPATCHER] Final recording path: %s",
                            resp.status_code,
                            final_rec_path,
                        )
                except Exception as exc:
                    logger.error("[DISPATCHER] ERROR: Stopping MediaWorker recording failed - %s", exc)

            logger.info("[MEDIA] WAV finalized successfully")

            # 2. Check recording file readiness (bounded retry loop)
            is_ready = await self.check_recording_readiness(
                event_id=job.event_id,
                file_path_or_name=final_rec_path,
                max_attempts=max_readiness_attempts,
                delay_seconds=readiness_delay_seconds,
            )

            if not is_ready:
                if self.settings.use_app_hosted_media:
                    logger.error("[DISPATCHER] ERROR: Recording file readiness check failed for '%s'. Aborting Phase 5 pipeline.", final_rec_path)
                    return None
                else:
                    logger.warning("[DISPATCHER] Recording file '%s' readiness check failed, but proceeding in mock/test mode.", final_rec_path)


            # 3. Execute Phase 5 Pipeline
            kwargs = {
                "job": job,
                "file_path_or_name": final_rec_path,
                "force_refresh": force_refresh,
            }

            result = await self.pipeline.process_end_to_end_job(**kwargs)



            if result.transcript and result.transcript.status.value == "completed":
                logger.info("[Deepgram Completed] STT transcript generated for event %s (%d segments)", job.event_id, len(result.transcript.segments))

            if result.summary and result.summary.status.value == "completed":
                logger.info("[Gemini Completed] LLM meeting summary generated for event %s", job.event_id)

            if result.delivery_record and result.delivery_record.status.value == "sent":
                logger.info("[Email Sent] Summary email sent to '%s' for event %s", result.recipient_email, job.event_id)

            logger.info(
                "[MeetingDispatcher] Phase 5 Pipeline finished for event %s | Status: %s | Pipeline ID: %s",
                job.event_id,
                result.status,
                result.pipeline_id,
            )
            return result
        except Exception as exc:
            logger.exception("[MeetingDispatcher] Exception running Phase 5 Pipeline for event %s: %s", job.event_id, exc)
            return None
        finally:
            self._running_pipelines.discard(job.event_id)

    async def wait_for_active_pipelines(self) -> None:
        """Wait for all active background Phase 5 pipeline tasks to finish."""
        if self._pipeline_tasks:
            await asyncio.gather(*list(self._pipeline_tasks), return_exceptions=True)


# Phase 2: inject TeamsCallJoinHandler as the real join implementation
from app.services.teams_call_join_handler import teams_call_join_handler  # noqa: E402

meeting_dispatcher = MeetingDispatcher(join_handler=teams_call_join_handler)
