"""Microsoft Graph Cloud Communications Calling Service.

Implements the actual Teams meeting join by calling:
  POST https://graph.microsoft.com/v1.0/communications/calls

The Graph Calling API does NOT accept a raw joinWebUrl directly.
Instead the joinWebUrl must be parsed to extract:
  - chatInfo.threadId and chatInfo.messageId
  - meetingInfo organizer context

Per Microsoft Graph docs (v1.0):
https://learn.microsoft.com/en-us/graph/api/application-post-calls

The correct payload for joining a scheduled meeting is:
{
  "@odata.type": "#microsoft.graph.call",
  "callbackUri": "<bot messaging endpoint>",
  "requestedModalities": ["audio"],
  "mediaConfig": {
    "@odata.type": "#microsoft.graph.serviceHostedMediaConfig"
  },
  "meetingInfo": {
    "@odata.type": "#microsoft.graph.organizerMeetingInfo",
    "organizer": {
      "@odata.type": "#microsoft.graph.identitySet",
      "user": {
        "@odata.type": "#microsoft.graph.identity",
        "id": "<organizer-oid>",
        "tenantId": "<tenant-id>"
      }
    }
  },
  "chatInfo": {
    "@odata.type": "#microsoft.graph.chatInfo",
    "threadId": "<extracted from joinWebUrl>",
    "messageId": "0"
  }
}

Phase 2 scope: join-only (serviceHostedMediaConfig, no local audio capture).
Phase 3 will switch to appHostedMediaConfig + Bot Framework Media SDK.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.auth.entra_auth import EntraAuthService, entra_auth_service
from app.core.config import Settings, settings
from app.models.call import CallRecord, CallState

logger = logging.getLogger(__name__)

# ── Join URL parsing ───────────────────────────────────────────────────────────
# Teams join URLs have the form:
#   https://teams.microsoft.com/l/meetup-join/<encoded-thread-id>/<message-id>?context=...
#
# The encoded-thread-id is a URL-encoded string like:
#   19%3ameeting_<base64>%40thread.v2
# context query parameter contains URL-encoded JSON with "Tid" (tenantId) and "Oid" (organizerId).

_JOIN_URL_THREAD_PATTERN = re.compile(
    r"teams\.microsoft\.com/l/meetup-join/([^/?\s]+)(?:/([^/?#\s]+))?",
    re.IGNORECASE,
)
_SHORT_MEET_PATTERN = re.compile(
    r"teams\.microsoft\.com/meet/([^/?\s]+)",
    re.IGNORECASE,
)


def parse_thread_id_from_join_url(join_url: str) -> tuple[Optional[str], str]:
    """Extract (threadId, messageId) from a Teams join URL.

    Returns:
        (thread_id, message_id) — thread_id is None if parsing fails.
    """
    url_type, param1, param2, _, _ = parse_teams_join_url_details(join_url)
    if url_type == "MEETUP_JOIN":
        return param1, param2
    return None, "0"


def parse_teams_join_url_details(
    join_url: str,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Extract URL details from a Teams join URL.

    Returns:
        (url_type, param1, param2, organizer_id, tenant_id)
        - For MEETUP_JOIN: ("MEETUP_JOIN", thread_id, message_id, organizer_id, tenant_id)
        - For SHORT_MEET:  ("SHORT_MEET", meeting_id, passcode, None, None)
    """
    # 1. Check for short meet format: /meet/<meeting_id>?p=<passcode>
    short_match = _SHORT_MEET_PATTERN.search(join_url)
    if short_match:
        meeting_id = short_match.group(1).replace(" ", "").replace("-", "")
        parsed_url = urlparse(join_url)
        query_params = parse_qs(parsed_url.query)
        passcode = query_params.get("p", [None])[0]
        return "SHORT_MEET", meeting_id, passcode, None, None

    # 2. Check for standard meetup-join format
    match = _JOIN_URL_THREAD_PATTERN.search(join_url)
    if not match:
        return None, None, None, None, None

    raw_thread = match.group(1)
    raw_message = match.group(2) or "0"

    thread_id = unquote(raw_thread)
    message_id = unquote(raw_message) if raw_message else "0"

    organizer_id = None
    tenant_id = None

    try:
        parsed_url = urlparse(join_url)
        query_params = parse_qs(parsed_url.query)
        if "context" in query_params:
            ctx_str = query_params["context"][0]
            ctx = json.loads(ctx_str)
            organizer_id = ctx.get("Oid") or ctx.get("oid")
            tenant_id = ctx.get("Tid") or ctx.get("tid")
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("[GraphCallingService] Could not parse context from join URL: %s", exc)

    return "MEETUP_JOIN", thread_id, message_id, organizer_id, tenant_id


class GraphCallingError(Exception):
    """Raised when the Graph Cloud Communications Calling API returns an error."""

    def __init__(self, message: str, http_status: Optional[int] = None, error_code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.error_code = error_code


class GraphCallingService:
    """Service to join Teams meetings via Graph Cloud Communications API.

    Wraps POST /communications/calls with the service-hosted media payload
    appropriate for a silent listener / notetaker bot (Phase 2).
    """

    def __init__(
        self,
        auth_service: Optional[EntraAuthService] = None,
        app_settings: Optional[Settings] = None,
    ):
        self.auth_service = auth_service or entra_auth_service
        self.settings = app_settings or settings

    def _get_headers(self, tenant_id: Optional[str] = None) -> dict:
        """Build authorisation headers using a fresh Graph token for the target tenant."""
        token = self.auth_service.get_access_token(tenant_id=tenant_id)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"{self.settings.app_name}/{self.settings.app_version}",
        }

    def _build_join_payload(
        self,
        thread_id: str,
        message_id: str,
        organizer_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        media_blob: Optional[str] = None,
    ) -> dict:
        """Construct the Graph POST /communications/calls request body for meetup-join URLs."""
        bot_app_id = self.settings.azure_bot_app_id or self.settings.azure_client_id
        bot_tenant_id = self.settings.azure_tenant_id
        target_tenant_id = tenant_id or bot_tenant_id

        # Detect cross-tenant join condition
        is_cross_tenant = bool(
            target_tenant_id and bot_tenant_id and target_tenant_id.lower().strip() != bot_tenant_id.lower().strip()
        )

        if organizer_id:
            organizer_identity = {
                "@odata.type": "#microsoft.graph.identitySet",
                "user": {
                    "@odata.type": "#microsoft.graph.identity",
                    "id": organizer_id,
                    "tenantId": target_tenant_id,
                },
            }
        else:
            organizer_identity = {
                "@odata.type": "#microsoft.graph.identitySet",
                "application": {
                    "@odata.type": "#microsoft.graph.identity",
                    "id": bot_app_id,
                    "displayName": self.settings.azure_bot_display_name,
                    "tenantId": target_tenant_id,
                },
            }

        if media_blob:
            media_config = {
                "@odata.type": "#microsoft.graph.appHostedMediaConfig",
                "blob": media_blob,
            }
        else:
            media_config = {
                "@odata.type": "#microsoft.graph.serviceHostedMediaConfig",
            }

        payload = {
            "@odata.type": "#microsoft.graph.call",
            "callbackUri": self.settings.bot_callback_url,
            "requestedModalities": ["audio", "video"],
            "mediaConfig": media_config,
            "chatInfo": {
                "@odata.type": "#microsoft.graph.chatInfo",
                "threadId": thread_id,
                "messageId": message_id,
            },
            "meetingInfo": {
                "@odata.type": "#microsoft.graph.organizerMeetingInfo",
                "organizer": organizer_identity,
            },
            "tenantId": target_tenant_id,
        }

        # If cross-tenant guest join is detected, add source participant info with guest identity
        # as required for Calls.JoinGroupCallAsGuest.All
        if is_cross_tenant:
            guest_display_name = self.settings.azure_bot_display_name or "TekMeet Bot"
            payload["source"] = {
                "@odata.type": "#microsoft.graph.participantInfo",
                "identity": {
                    "@odata.type": "#microsoft.graph.identitySet",
                    "guest": {
                        "@odata.type": "#microsoft.graph.identity",
                        "id": bot_app_id or "tekmeet-bot-guest",
                        "displayName": guest_display_name,
                    },
                },
            }

        return payload

    def _build_short_meet_payload(self, meeting_id: str, passcode: str, media_blob: Optional[str] = None) -> dict:
        """Construct the Graph POST /communications/calls request body for short /meet/<id>?p=<passcode> URLs."""
        if media_blob:
            media_config = {
                "@odata.type": "#microsoft.graph.appHostedMediaConfig",
                "blob": media_blob,
            }
        else:
            media_config = {
                "@odata.type": "#microsoft.graph.serviceHostedMediaConfig",
            }

        payload = {
            "@odata.type": "#microsoft.graph.call",
            "callbackUri": self.settings.bot_callback_url,
            "requestedModalities": ["audio", "video"],
            "mediaConfig": media_config,
            "meetingInfo": {
                "@odata.type": "#microsoft.graph.joinMeetingIdMeetingInfo",
                "joinMeetingId": meeting_id,
                "passcode": passcode,
            },
        }
        return payload


    async def fetch_media_worker_config_blob(self) -> Optional[str]:
        """Query the C# Media Worker Service to retrieve the appHostedMediaConfig Base64 blob."""
        url = f"{self.settings.media_worker_url}/api/media/config"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    blob = data.get("blob")
                    if blob:
                        logger.info("[GraphCallingService] Obtained appHostedMediaConfig blob from Media Worker.")
                        return blob
        except Exception as exc:
            logger.warning("[GraphCallingService] Could not connect to Media Worker at %s: %s", url, exc)
        return None

    async def update_recording_status(self, call_id: str, status: str = "recording") -> bool:
        """Update the recording status of a call to trigger the native Teams recording banner."""
        endpoint = f"{self.settings.microsoft_graph_base_url}/communications/calls/{call_id}/updateRecordingStatus"
        headers = self._get_headers()
        payload = {
            "clientContext": "tekmeet-phase3-recording",
            "status": status,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(endpoint, json=payload, headers=headers)
                if resp.status_code in (200, 202, 204):
                    logger.info("[GraphCallingService] Updated recording status for call %s to '%s'", call_id, status)
                    return True
                else:
                    logger.warning(
                        "[GraphCallingService] updateRecordingStatus for %s returned status %d: %s",
                        call_id, resp.status_code, resp.text[:200]
                    )
        except Exception as exc:
            logger.error("[GraphCallingService] Exception updating recording status for call %s: %s", call_id, exc)
        return False

    async def join_meeting(self, join_url: str, event_id: str) -> CallRecord:
        """Join a Teams meeting by initiating a Graph Cloud Communications API call."""
        now_utc = datetime.now(timezone.utc)
        record = CallRecord(event_id=event_id, join_url=join_url, started_at=now_utc)

        # 1. Parse URL details
        url_type, param1, param2, organizer_id, tenant_id = parse_teams_join_url_details(join_url)
        if not url_type:
            logger.error(
                "[GraphCallingService] Failed to parse Teams join URL: %s", join_url
            )
            record.state = CallState.FAILED
            record.error_code = "invalid_join_url"
            record.error_message = f"Could not extract meeting details from Teams join URL: {join_url[:80]}"
            return record

        # 2. Fetch Media Worker blob if USE_APP_HOSTED_MEDIA is enabled
        media_blob = None
        if self.settings.use_app_hosted_media:
            media_blob = await self.fetch_media_worker_config_blob()
            if not media_blob:
                logger.warning(
                    "[GraphCallingService] Could not obtain appHostedMediaConfig blob from Media Worker at %s. Falling back to serviceHostedMediaConfig.",
                    self.settings.media_worker_url,
                )
            else:
                logger.info(
                    "[GraphCallingService] Phase 3 Application-Hosted Media mode enabled with appHostedMediaConfig for event_id=%s",
                    event_id,
                )
        else:
            logger.info(
                "[GraphCallingService] Service-Hosted Media mode enabled (USE_APP_HOSTED_MEDIA=false / Phase 2) for event_id=%s",
                event_id,
            )

        # 3. Build payload based on URL type
        if url_type == "SHORT_MEET":
            logger.info(
                "[GraphCallingService] Joining Teams meeting (short URL) | event_id=%s | meeting_id=%s",
                event_id,
                param1,
            )
            payload = self._build_short_meet_payload(meeting_id=param1, passcode=param2 or "", media_blob=media_blob)
        else:
            logger.info(
                "[GraphCallingService] Joining Teams meeting | event_id=%s | thread_id=%s | message_id=%s",
                event_id,
                param1,
                param2,
            )
            payload = self._build_join_payload(
                thread_id=param1,
                message_id=param2,
                organizer_id=organizer_id,
                tenant_id=tenant_id,
                media_blob=media_blob,
            )

        try:
            headers = self._get_headers(tenant_id=tenant_id)
        except Exception as exc:
            logger.error("[GraphCallingService] Auth error acquiring token for tenant %s: %s", tenant_id, exc)
            record.state = CallState.FAILED
            record.error_code = getattr(exc, "error_code", "auth_error")
            record.error_message = str(exc)
            record.http_status = None
            return record

        endpoint = f"{self.settings.microsoft_graph_base_url}/communications/calls"

        # 4. POST to Graph
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
        except httpx.RequestError as exc:
            logger.error("[GraphCallingService] Network error calling Graph: %s", exc)
            record.state = CallState.FAILED
            record.error_code = "network_error"
            record.error_message = str(exc)
            record.http_status = None
            return record

        record.http_status = response.status_code

        # 5. Parse response
        if response.status_code == 201:
            resp_data = response.json()
            call_id = resp_data.get("id")
            call_state = resp_data.get("state", CallState.ESTABLISHING)
            record.call_id = call_id
            try:
                record.state = CallState(call_state)
            except ValueError:
                record.state = CallState.ESTABLISHING

            logger.info(
                "[GraphCallingService] Successfully joined Teams meeting | event_id=%s | call_id=%s | state=%s",
                event_id,
                call_id,
                record.state,
            )

            # Trigger recording status update if call is already ESTABLISHED
            if record.state == CallState.ESTABLISHED:
                await self.update_recording_status(call_id=call_id, status="recording")
        else:
            # Extract Graph error details
            error_json = {}
            try:
                error_json = response.json().get("error", {})
            except Exception:  # pylint: disable=broad-except
                pass

            error_code = error_json.get("code", f"http_{response.status_code}")
            error_message = error_json.get("message", response.text[:300])

            logger.error(
                "[GraphCallingService] Graph POST /communications/calls failed | "
                "event_id=%s | status=%d | code=%s | message=%s",
                event_id,
                response.status_code,
                error_code,
                error_message,
            )
            record.state = CallState.FAILED
            record.error_code = error_code
            record.error_message = error_message

        return record



# Reusable singleton
graph_calling_service = GraphCallingService()
