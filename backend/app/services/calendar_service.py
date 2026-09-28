"""Microsoft Graph Calendar Service.

Retrieves and parses calendar events using Microsoft Graph API with application permissions.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
from dateutil import parser as date_parser

from app.auth.entra_auth import EntraAuthService, entra_auth_service
from app.core.config import Settings, settings
from app.models.meeting import AttendeeInfo, MeetingEvent

logger = logging.getLogger(__name__)


class CalendarServiceError(Exception):
    """Base exception for calendar service operations."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class CalendarService:
    """Service to interact with Microsoft Graph Calendar APIs."""

    def __init__(
        self,
        auth_service: Optional[EntraAuthService] = None,
        app_settings: Optional[Settings] = None,
    ):
        """Initialize calendar service with auth service and configuration."""
        self.auth_service = auth_service or entra_auth_service
        self.settings = app_settings or settings

    def _get_headers(self) -> Dict[str, str]:
        """Obtain headers including Authorization Bearer token."""
        token = self.auth_service.get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"{self.settings.app_name}/{self.settings.app_version}",
            "Prefer": 'outlook.timezone="UTC"',
        }

    @staticmethod
    def _parse_datetime(dt_obj: Optional[Dict[str, Any]]) -> Optional[datetime]:
        """Safely parse Graph dateTime object ({'dateTime': '...', 'timeZone': 'UTC'}) to UTC datetime."""
        if not dt_obj or not isinstance(dt_obj, dict):
            return None
        dt_str = dt_obj.get("dateTime")
        if not dt_str:
            return None

        try:
            parsed = date_parser.isoparse(dt_str)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
            return parsed
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to parse event dateTime '%s': %s", dt_str, exc)
            return None

    @staticmethod
    def parse_graph_event(raw_event: Dict[str, Any]) -> MeetingEvent:
        """Parse and normalize a raw Microsoft Graph event payload into a MeetingEvent model.

        Detects Microsoft Teams meetings by evaluating isOnlineMeeting, onlineMeetingProvider,
        and joinUrl signatures.
        """
        event_id = raw_event.get("id") or "unknown_event_id"
        subject = raw_event.get("subject") or "Untitled Meeting"
        start_time = CalendarService._parse_datetime(raw_event.get("start"))
        end_time = CalendarService._parse_datetime(raw_event.get("end"))

        # Extract organizer
        organizer_raw = raw_event.get("organizer", {}) or {}
        email_address = organizer_raw.get("emailAddress", {}) or {}
        organizer_name = email_address.get("name")
        organizer_email = email_address.get("address")

        # Extract attendees
        attendees: List[AttendeeInfo] = []
        for att in raw_event.get("attendees", []) or []:
            if isinstance(att, dict):
                att_email_info = att.get("emailAddress", {}) or {}
                status_info = att.get("status", {}) or {}
                attendees.append(
                    AttendeeInfo(
                        email=att_email_info.get("address"),
                        name=att_email_info.get("name"),
                        attendee_type=att.get("type"),
                        response_status=status_info.get("response"),
                    )
                )

        # Online Meeting & Teams Detection Logic
        is_online = bool(raw_event.get("isOnlineMeeting", False))
        provider = raw_event.get("onlineMeetingProvider")
        provider_str = str(provider).lower() if provider else ""

        online_meeting_obj = raw_event.get("onlineMeeting") or {}
        online_meeting_url = raw_event.get("onlineMeetingUrl")

        # Extract Join URL from official fields or fallback to body/location
        join_url = None
        if isinstance(online_meeting_obj, dict) and online_meeting_obj.get("joinUrl"):
            join_url = online_meeting_obj["joinUrl"]
        elif online_meeting_url:
            join_url = online_meeting_url

        # Fallback: Extract Teams URL from body content or location if missing in official fields
        body_obj = raw_event.get("body", {}) or {}
        body_content = body_obj.get("content", "") if isinstance(body_obj, dict) else ""
        location_raw = raw_event.get("location", {}) or {}
        location_displayName = location_raw.get("displayName", "") if isinstance(location_raw, dict) else ""

        if not join_url and (body_content or location_displayName):
            import re
            # Regex for meetup-join or meet short URLs
            url_match = re.search(
                r"https://teams\.microsoft\.com/(?:l/meetup-join|meet)/[^\s\"'<>]+",
                f"{body_content} {location_displayName}",
                re.IGNORECASE,
            )
            if url_match:
                join_url = url_match.group(0)
            else:
                # Check if Meeting ID and Passcode exist in body
                m_id_match = re.search(r"Meeting\s*ID:\s*([\d\s-]+)", body_content, re.IGNORECASE)
                pass_match = re.search(r"Passcode:\s*([^\s<\"'&]+)", body_content, re.IGNORECASE)
                if m_id_match and pass_match:
                    clean_m_id = m_id_match.group(1).replace(" ", "").replace("-", "")
                    clean_pass = pass_match.group(1).strip()
                    join_url = f"https://teams.microsoft.com/meet/{clean_m_id}?p={clean_pass}"

        # Extract Meeting / Conference IDs if available
        meeting_id = None
        conference_id = None
        if isinstance(online_meeting_obj, dict):
            meeting_id = online_meeting_obj.get("id")
            conference_id = online_meeting_obj.get("conferenceId")

        # Teams Meeting Detection:
        # 1. Provider is explicitly 'teamsForBusiness' or 'teamsForConsumer' or contains 'teams'
        # 2. Join URL points to teams.microsoft.com or teams.live.com
        is_teams = False
        if "teams" in provider_str:
            is_teams = True
        elif join_url and ("teams.microsoft.com" in join_url.lower() or "teams.live.com" in join_url.lower()):
            is_teams = True
        elif is_online and provider_str == "" and join_url:
            if "teams.microsoft.com" in join_url.lower() or "teams.live.com" in join_url.lower():
                is_teams = True

        if join_url and ("teams.microsoft.com" in join_url.lower() or "teams.live.com" in join_url.lower()):
            is_teams = True
            is_online = True

        # Location details
        location_name = location_displayName if location_displayName else None

        return MeetingEvent(
            event_id=event_id,
            subject=subject,
            start_time=start_time,
            end_time=end_time,
            organizer_name=organizer_name,
            organizer_email=organizer_email,
            attendees=attendees,
            is_online_meeting=is_online,
            online_meeting_provider=provider or ("teamsForBusiness" if is_teams else None),
            is_teams_meeting=is_teams,
            join_url=join_url,
            meeting_id=meeting_id,
            conference_id=conference_id,
            location=location_name,
            is_cancelled=bool(raw_event.get("isCancelled", False)),
            web_link=raw_event.get("webLink"),
        )

    async def get_calendar_events(
        self,
        user_id_or_email: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        top: int = 50,
        max_total_events: int = 200,
    ) -> List[MeetingEvent]:
        target_user = user_id_or_email or self.settings.azure_bot_user_email
        if not target_user:
            raise CalendarServiceError(
                message=(
                    "Target mailbox / user principal name is not specified. "
                    "Provide 'user_id_or_email' or configure AZURE_BOT_USER_EMAIL in your .env file."
                ),
                status_code=400,
            )

        now_utc = datetime.now(timezone.utc)
        from datetime import timedelta
        # Default start window includes a 15-minute lookback buffer to capture recently started meetings
        start_dt = start_time if start_time is not None else (now_utc - timedelta(minutes=15))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        else:
            start_dt = start_dt.astimezone(timezone.utc)

        if end_time is not None:
            end_dt = end_time
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            else:
                end_dt = end_dt.astimezone(timezone.utc)
        else:
            end_dt = start_dt + timedelta(minutes=self.settings.meeting_lookahead_minutes)

        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Microsoft Graph calendarView endpoint expands recurring instances within the start/end window
        endpoint = f"{self.settings.microsoft_graph_base_url}/users/{target_user}/calendarView"
        params = {
            "startDateTime": start_iso,
            "endDateTime": end_iso,
            "$top": min(top, 50),
            "$orderby": "start/dateTime",
            "$select": "id,subject,start,end,organizer,attendees,isOnlineMeeting,onlineMeetingProvider,onlineMeeting,onlineMeetingUrl,location,isCancelled,webLink,body",
        }

        headers = self._get_headers()
        events: List[MeetingEvent] = []
        next_url: Optional[str] = endpoint
        request_params: Optional[Dict[str, Any]] = params

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                while next_url and len(events) < max_total_events:
                    logger.debug("Fetching calendar events from Graph: %s", next_url)
                    response = await client.get(next_url, params=request_params, headers=headers)

                    if response.status_code != 200:
                        error_json = {}
                        try:
                            error_json = response.json().get("error", {})
                        except Exception:  # pylint: disable=broad-except
                            pass

                        err_code = error_json.get("code", f"http_{response.status_code}")
                        err_msg = error_json.get("message", response.text[:200])

                        logger.error(
                            "Microsoft Graph calendar query failed. Code: %s, Message: %s, Status: %s",
                            err_code,
                            err_msg,
                            response.status_code,
                        )
                        raise CalendarServiceError(
                            message=f"Microsoft Graph calendar query error: {err_code} - {err_msg}",
                            status_code=response.status_code,
                            details={"error_code": err_code, "error_message": err_msg},
                        )

                    data = response.json()
                    raw_items = data.get("value", [])
                    for raw_item in raw_items:
                        try:
                            parsed_event = self.parse_graph_event(raw_item)
                            events.append(parsed_event)
                        except Exception as exc:  # pylint: disable=broad-except
                            logger.warning("Failed parsing calendar item: %s", exc)

                    # Follow pagination (@odata.nextLink) if present
                    next_url = data.get("@odata.nextLink")
                    request_params = None  # NextLink already encapsulates full query params

        except httpx.RequestError as exc:
            logger.error("Network error querying Microsoft Graph calendar: %s", exc)
            raise CalendarServiceError(
                message=f"Network error communicating with Microsoft Graph: {str(exc)}",
                status_code=503,
            ) from exc

        logger.info(
            "Retrieved %d calendar events for user '%s' between %s and %s",
            len(events),
            target_user,
            start_iso,
            end_iso,
        )
        return events


# Reusable singleton instance
calendar_service = CalendarService()
