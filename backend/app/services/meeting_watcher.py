"""Meeting Watcher Service.

Coordinates periodic inspection of calendars, detects upcoming Microsoft Teams meetings,
and provides deduplicated meeting batches for downstream processing.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set

from app.core.config import Settings, settings
from app.models.meeting import MeetingEvent, UpcomingMeetingsResponse
from app.services.calendar_service import CalendarService, CalendarServiceError, calendar_service

logger = logging.getLogger(__name__)


class MeetingWatcher:
    """Service to watch, poll, and filter upcoming Teams meetings."""

    def __init__(
        self,
        cal_service: Optional[CalendarService] = None,
        app_settings: Optional[Settings] = None,
    ):
        """Initialize watcher with calendar service and settings."""
        self.calendar_service = cal_service or calendar_service
        self.settings = app_settings or settings
        # Track seen event IDs to avoid duplicate processing across polling cycles
        self._seen_event_ids: Set[str] = set()

    def mark_event_seen(self, event_id: str) -> None:
        """Mark an event ID as processed in watcher memory."""
        self._seen_event_ids.add(event_id)

    def is_event_seen(self, event_id: str) -> bool:
        """Check if an event ID has already been recorded as seen."""
        return event_id in self._seen_event_ids

    def clear_seen_cache(self) -> None:
        """Clear the in-memory cache of tracked event IDs."""
        self._seen_event_ids.clear()

    async def poll_upcoming_teams_meetings(
        self,
        user_id_or_email: Optional[str] = None,
        lookahead_minutes: Optional[int] = None,
        only_unseen: bool = False,
    ) -> UpcomingMeetingsResponse:
        """Poll the calendar for Microsoft Teams meetings starting within the lookahead window.

        Args:
            user_id_or_email: Target mailbox / UPN. Defaults to AZURE_BOT_USER_EMAIL.
            lookahead_minutes: Window size in minutes. Defaults to MEETING_LOOKAHEAD_MINUTES.
            only_unseen: If True, filters out meetings that were previously marked as seen.

        Returns:
            UpcomingMeetingsResponse: Structured summary with filtered Teams meetings.
        """
        target_user = user_id_or_email or self.settings.azure_bot_user_email
        window_minutes = lookahead_minutes or self.settings.meeting_lookahead_minutes

        now_utc = datetime.now(timezone.utc)
        window_end_utc = now_utc + timedelta(minutes=window_minutes)

        try:
            raw_events = await self.calendar_service.get_calendar_events(
                user_id_or_email=target_user,
                start_time=now_utc,
                end_time=window_end_utc,
            )

            # Filter for active Teams meetings
            teams_meetings: List[MeetingEvent] = []
            for event in raw_events:
                if event.is_teams_meeting and not event.is_cancelled:
                    if only_unseen and self.is_event_seen(event.event_id):
                        logger.debug("Skipping already seen meeting: %s (%s)", event.subject, event.event_id)
                        continue
                    teams_meetings.append(event)
                    start_str = event.start_time.isoformat() if event.start_time else "N/A"
                    end_str = event.end_time.isoformat() if event.end_time else "N/A"
                    now_str = now_utc.isoformat()
                    buffer_delta = timedelta(seconds=self.settings.join_buffer_seconds)
                    if event.start_time and event.end_time:
                        if now_utc < (event.start_time - buffer_delta):
                            m_status = "scheduled"
                        elif (event.start_time - buffer_delta) <= now_utc <= event.end_time:
                            m_status = "due"
                        else:
                            m_status = "already started"
                    else:
                        m_status = "scheduled"

                    logger.info(
                        "[CALENDAR] Teams meeting detected\n"
                        "[CALENDAR] Event ID: %s\n"
                        "[CALENDAR] Subject: %s\n"
                        "[CALENDAR] Scheduled start: %s\n"
                        "[CALENDAR] Scheduled end: %s\n"
                        "[CALENDAR] Current time: %s\n"
                        "[CALENDAR] Meeting status: %s",
                        event.event_id,
                        event.subject,
                        start_str,
                        end_str,
                        now_str,
                        m_status,
                    )

            # Sort ascending by start_time
            teams_meetings.sort(key=lambda m: m.start_time or datetime.max.replace(tzinfo=timezone.utc))

            logger.info(
                "Meeting Watcher poll completed: %d total calendar events, %d Teams meetings found for '%s'",
                len(raw_events),
                len(teams_meetings),
                target_user,
            )

            return UpcomingMeetingsResponse(
                success=True,
                target_user=target_user,
                window_start_utc=now_utc,
                window_end_utc=window_end_utc,
                total_events_retrieved=len(raw_events),
                teams_meetings_count=len(teams_meetings),
                meetings=teams_meetings,
            )

        except CalendarServiceError as exc:
            logger.error("Meeting Watcher poll failed: %s", exc.message)
            return UpcomingMeetingsResponse(
                success=False,
                target_user=target_user,
                window_start_utc=now_utc,
                window_end_utc=window_end_utc,
                total_events_retrieved=0,
                teams_meetings_count=0,
                meetings=[],
                error=exc.message,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Unexpected error during meeting polling")
            return UpcomingMeetingsResponse(
                success=False,
                target_user=target_user,
                window_start_utc=now_utc,
                window_end_utc=window_end_utc,
                total_events_retrieved=0,
                teams_meetings_count=0,
                meetings=[],
                error=f"Unexpected polling error: {str(exc)}",
            )


# Reusable singleton instance
meeting_watcher = MeetingWatcher()
