"""Unit tests for Microsoft Graph Service."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import SecretStr

from app.auth.entra_auth import EntraAuthService
from app.core.config import Settings
from app.services.graph_service import MicrosoftGraphService


@pytest.fixture
def mock_settings():
    return Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("mock_secret"),
        AZURE_BOT_USER_EMAIL="bot@example.com",
    )


@pytest.mark.asyncio
async def test_graph_service_successful_connectivity(mock_settings):
    """Test successful connectivity to Microsoft Graph calendarView endpoint."""
    auth_service = MagicMock(spec=EntraAuthService)
    auth_service.get_access_token.return_value = "mock_bearer_token"

    service = MicrosoftGraphService(auth_service=auth_service, app_settings=mock_settings)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": []}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        result = await service.verify_graph_connectivity()

        assert result.success is True
        assert result.status_code == 200
        # organization_name is not applicable for calendarView
        assert result.organization_name is None
        assert result.tenant_id_masked == "1111****5555"


@pytest.mark.asyncio
async def test_graph_service_http_error_handling(mock_settings):
    """Test graph service returns controlled error when bot email is absent (no network call made)."""
    auth_service = MagicMock(spec=EntraAuthService)
    auth_service.get_access_token.return_value = "mock_bearer_token"

    # Explicitly pass AZURE_BOT_USER_EMAIL=None so pydantic-settings does not
    # fall back to the real .env value, keeping this test fully isolated.
    no_email_settings = Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("mock_secret"),
        AZURE_BOT_USER_EMAIL=None,
    )

    service = MicrosoftGraphService(auth_service=auth_service, app_settings=no_email_settings)

    result = await service.verify_graph_connectivity()

    assert result.success is False
    assert result.error_code == "missing_target_email"
    assert "Target email" in result.error_message
    # Confirm auth service was never called — no network round-trip happened
    auth_service.get_access_token.assert_not_called()
