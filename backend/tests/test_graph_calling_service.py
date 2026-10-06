"""Unit tests for GraphCallingService — Phase 2.

All tests use mocks; no real Graph API calls are made.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.auth.entra_auth import EntraAuthService
from app.models.call import CallState
from app.services.graph_calling_service import (
    GraphCallingService,
    parse_thread_id_from_join_url,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

REAL_JOIN_URL = (
    "https://teams.microsoft.com/l/meetup-join/"
    "19%3ameeting_ODkzMmRlNWQtNjJmZi00NTg5LWFjYzItYzBmMjg5ZTUwZmEz%40thread.v2/"
    "0?context=%7b%22Tid%22%3a%222da95c53%22%7d"
)
EXPECTED_THREAD_ID = "19:meeting_ODkzMmRlNWQtNjJmZi00NTg5LWFjYzItYzBmMjg5ZTUwZmEz@thread.v2"


@pytest.fixture
def mock_settings():
    return Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("mock_secret"),
        AZURE_BOT_APP_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_BOT_DISPLAY_NAME="TekMeet Notetaker",
        BOT_CALLBACK_URL="https://bot.example.com/api/messages",
    )


@pytest.fixture
def mock_auth():
    auth = MagicMock(spec=EntraAuthService)
    auth.get_access_token.return_value = "mock_bearer_token"
    return auth


# ── URL Parsing Tests ─────────────────────────────────────────────────────────

def test_parse_thread_id_standard_url():
    """Test parsing threadId and messageId from a standard Teams join URL."""
    thread_id, message_id = parse_thread_id_from_join_url(REAL_JOIN_URL)
    assert thread_id == EXPECTED_THREAD_ID
    assert message_id == "0"


def test_parse_thread_id_no_message_id():
    """Test that missing messageId path segment defaults to '0'."""
    url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2"
    thread_id, message_id = parse_thread_id_from_join_url(url)
    assert thread_id == "19:meeting_abc@thread.v2"
    assert message_id == "0"


def test_parse_thread_id_invalid_url():
    """Test that a non-Teams URL returns None threadId."""
    thread_id, message_id = parse_thread_id_from_join_url("https://example.com/not-a-teams-url")
    assert thread_id is None
    assert message_id == "0"


def test_parse_thread_id_empty_string():
    """Test that an empty join URL returns None threadId."""
    thread_id, message_id = parse_thread_id_from_join_url("")
    assert thread_id is None


# ── Payload Construction ──────────────────────────────────────────────────────

def test_build_join_payload_structure(mock_settings, mock_auth):
    """Test that the payload has the correct Graph Calling API structure."""
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)
    payload = service._build_join_payload(
        thread_id="19:meeting_abc@thread.v2",
        message_id="0",
    )

    assert payload["@odata.type"] == "#microsoft.graph.call"
    assert payload["callbackUri"] == "https://bot.example.com/api/messages"
    assert payload["requestedModalities"] == ["audio", "video"]

    # Media config — serviceHosted for Phase 2 (no local audio)
    assert payload["mediaConfig"]["@odata.type"] == "#microsoft.graph.serviceHostedMediaConfig"

    # chatInfo
    assert payload["chatInfo"]["@odata.type"] == "#microsoft.graph.chatInfo"
    assert payload["chatInfo"]["threadId"] == "19:meeting_abc@thread.v2"
    assert payload["chatInfo"]["messageId"] == "0"

    # meetingInfo — organizerMeetingInfo with bot identity
    assert payload["meetingInfo"]["@odata.type"] == "#microsoft.graph.organizerMeetingInfo"
    assert "application" in payload["meetingInfo"]["organizer"]
    assert payload["meetingInfo"]["organizer"]["application"]["id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert payload["meetingInfo"]["organizer"]["application"]["displayName"] == "TekMeet Notetaker"


def test_bot_app_id_fallback_to_client_id(mock_auth):
    """Test that AZURE_CLIENT_ID is used when AZURE_BOT_APP_ID is not set."""
    settings_no_bot_id = Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="fallback-client-id",
        AZURE_CLIENT_SECRET=SecretStr("mock_secret"),
        AZURE_BOT_APP_ID=None,
    )
    service = GraphCallingService(auth_service=mock_auth, app_settings=settings_no_bot_id)
    payload = service._build_join_payload("19:meeting_x@thread.v2", "0")
    assert payload["meetingInfo"]["organizer"]["application"]["id"] == "fallback-client-id"


def test_build_join_payload_same_tenant(mock_settings, mock_auth):
    """Test that same-tenant payload preserves exact standard join payload without source guest block."""
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)
    payload = service._build_join_payload(
        thread_id="19:meeting_same@thread.v2",
        message_id="0",
        tenant_id="11111111-2222-3333-4444-555555555555",  # same as bot tenant ID
    )
    assert payload["tenantId"] == "11111111-2222-3333-4444-555555555555"
    assert "source" not in payload


def test_build_join_payload_cross_tenant_guest(mock_settings, mock_auth):
    """Test that cross-tenant payload injects source.identity.guest for Calls.JoinGroupCallAsGuest.All."""
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)
    organizer_tenant_id = "2da95c53-29eb-4f80-b933-5aa77c2cfd92"
    organizer_user_id = "d2ce9fe8-3770-42ca-afee-2cf9ea6de352"

    payload = service._build_join_payload(
        thread_id="19:meeting_cross@thread.v2",
        message_id="0",
        organizer_id=organizer_user_id,
        tenant_id=organizer_tenant_id,
    )

    # Verify organizer tenant ID handling
    assert payload["tenantId"] == organizer_tenant_id
    assert payload["meetingInfo"]["organizer"]["user"]["id"] == organizer_user_id
    assert payload["meetingInfo"]["organizer"]["user"]["tenantId"] == organizer_tenant_id

    # Verify presence of guest identity under source block
    assert "source" in payload
    assert payload["source"]["@odata.type"] == "#microsoft.graph.participantInfo"
    assert payload["source"]["identity"]["@odata.type"] == "#microsoft.graph.identitySet"
    assert "guest" in payload["source"]["identity"]
    assert payload["source"]["identity"]["guest"]["@odata.type"] == "#microsoft.graph.identity"
    assert payload["source"]["identity"]["guest"]["id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert payload["source"]["identity"]["guest"]["displayName"] == "TekMeet Notetaker"


# ── Graph API Call Tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_join_meeting_success(mock_settings, mock_auth):
    """Test successful join returns CallRecord with call_id and non-FAILED state."""
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {
        "id": "call-id-abc123",
        "state": "establishing",
    }

    with patch.object(service, "fetch_media_worker_config_blob", new_callable=AsyncMock, return_value="dummy_media_blob"):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            record = await service.join_meeting(join_url=REAL_JOIN_URL, event_id="evt-001")

    assert record.call_id == "call-id-abc123"
    assert record.state == CallState.ESTABLISHING
    assert record.error_code is None
    assert record.http_status == 201


@pytest.mark.asyncio
async def test_join_meeting_403_forbidden(mock_settings, mock_auth):
    """Test 403 Forbidden returns CallRecord with FAILED state and error details."""
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.json.return_value = {
        "error": {
            "code": "Forbidden",
            "message": "Insufficient privileges to complete the operation.",
        }
    }
    mock_response.text = '{"error": {"code": "Forbidden"}}'

    with patch.object(service, "fetch_media_worker_config_blob", new_callable=AsyncMock, return_value="dummy_media_blob"):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            record = await service.join_meeting(join_url=REAL_JOIN_URL, event_id="evt-002")

    assert record.state == CallState.FAILED
    assert record.call_id is None
    assert record.error_code == "Forbidden"
    assert record.http_status == 403


@pytest.mark.asyncio
async def test_join_meeting_invalid_join_url(mock_settings, mock_auth):
    """Test that an unparseable join URL returns FAILED without making a network call."""
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)

    record = await service.join_meeting(
        join_url="https://zoom.us/j/12345",
        event_id="evt-003",
    )

    assert record.state == CallState.FAILED
    assert record.error_code == "invalid_join_url"
    assert record.call_id is None
    # Confirm no network calls were made
    mock_auth.get_access_token.assert_not_called()


@pytest.mark.asyncio
async def test_join_meeting_network_error(mock_settings, mock_auth):
    """Test network error is caught and returned as FAILED CallRecord."""
    import httpx as _httpx
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)

    with patch.object(service, "fetch_media_worker_config_blob", new_callable=AsyncMock, return_value="dummy_media_blob"):
        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            side_effect=_httpx.ConnectError("Connection refused"),
        ):
            record = await service.join_meeting(join_url=REAL_JOIN_URL, event_id="evt-004")

    assert record.state == CallState.FAILED
    assert record.error_code == "network_error"
    assert record.call_id is None
    assert record.http_status is None


@pytest.mark.asyncio
async def test_audiosocket_per_call_lifecycle_and_blob_uniqueness(mock_settings, mock_auth):
    """Verify consecutive media config requests fetch distinct media blobs and call parameters."""
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)

    mock_resp1 = MagicMock()
    mock_resp1.status_code = 200
    mock_resp1.json.return_value = {"blob": "blob_call_1_uuid_aaa", "initialized": True}

    mock_resp2 = MagicMock()
    mock_resp2.status_code = 200
    mock_resp2.json.return_value = {"blob": "blob_call_2_uuid_bbb", "initialized": True}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=[mock_resp1, mock_resp2]):
        blob1 = await service.fetch_media_worker_config_blob()
        blob2 = await service.fetch_media_worker_config_blob()

    assert blob1 == "blob_call_1_uuid_aaa"
    assert blob2 == "blob_call_2_uuid_bbb"
    assert blob1 != blob2


@pytest.mark.asyncio
async def test_get_call_state_connected(mock_settings, mock_auth):
    """Test get_call_state returns ESTABLISHED when Graph API reports connected/established state."""
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "call-123", "state": "established"}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        state = await service.get_call_state("call-123")

    assert state == CallState.ESTABLISHED


@pytest.mark.asyncio
async def test_get_call_state_terminated_when_404(mock_settings, mock_auth):
    """Test get_call_state returns TERMINATED when Graph API returns 404 Not Found."""
    service = GraphCallingService(auth_service=mock_auth, app_settings=mock_settings)
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        state = await service.get_call_state("call-123")

    assert state == CallState.TERMINATED


