"""Unit tests for Microsoft Entra ID Authentication Service."""

from unittest.mock import MagicMock, patch
import pytest
from pydantic import SecretStr

from app.auth.entra_auth import (
    EntraAuthError,
    EntraAuthService,
    MissingCredentialsError,
)
from app.core.config import Settings


@pytest.fixture
def mock_settings():
    """Create fixture settings with dummy Azure credentials."""
    return Settings(
        AZURE_TENANT_ID="11111111-2222-3333-4444-555555555555",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("mock_client_secret_xyz123"),
        MICROSOFT_GRAPH_SCOPES="https://graph.microsoft.com/.default",
    )


@pytest.fixture
def empty_settings():
    """Create fixture settings without credentials."""
    return Settings(
        AZURE_TENANT_ID=None,
        AZURE_CLIENT_ID=None,
        AZURE_CLIENT_SECRET=None,
    )


def test_missing_credentials_raises_error(empty_settings):
    """Test get_access_token raises MissingCredentialsError if env is unconfigured."""
    service = EntraAuthService(app_settings=empty_settings)
    with pytest.raises(MissingCredentialsError) as exc_info:
        service.get_access_token()
    assert "missing_credentials" in exc_info.value.error_code


def test_verify_auth_with_missing_credentials(empty_settings):
    """Test verify_authentication returns safe failure model when credentials missing."""
    service = EntraAuthService(app_settings=empty_settings)
    result = service.verify_authentication()
    assert result.success is False
    assert result.error_code == "missing_credentials"
    assert "missing required environment variables" in result.message.lower()


def test_successful_token_acquisition(mock_settings):
    """Test successful token acquisition with MSAL mock."""
    service = EntraAuthService(app_settings=mock_settings)

    mock_msal_app = MagicMock()
    mock_msal_app.acquire_token_for_client.return_value = {
        "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.mock_token_content",
        "token_type": "Bearer",
        "expires_in": 3599,
        "ext_expires_in": 3599,
    }

    with patch("msal.ConfidentialClientApplication", return_value=mock_msal_app):
        token = service.get_access_token()
        assert token == "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.mock_token_content"
        mock_msal_app.acquire_token_for_client.assert_called_once_with(
            scopes=["https://graph.microsoft.com/.default"]
        )


def test_verify_authentication_success_does_not_leak_token(mock_settings):
    """Test verify_authentication returns success model and never contains raw access token."""
    service = EntraAuthService(app_settings=mock_settings)

    mock_msal_app = MagicMock()
    mock_token = "secret_raw_token_xyz"
    mock_msal_app.acquire_token_for_client.return_value = {
        "access_token": mock_token,
        "token_type": "Bearer",
        "expires_in": 3600,
    }

    with patch("msal.ConfidentialClientApplication", return_value=mock_msal_app):
        result = service.verify_authentication()
        assert result.success is True
        assert result.token_type == "Bearer"
        assert result.expires_in == 3600
        assert result.tenant_id_masked == "1111****5555"
        assert result.client_id_masked == "aaaa****eeee"

        # Crucial security check: Ensure the raw access token is not present anywhere in the result
        result_dict = result.model_dump()
        assert "access_token" not in result_dict
        assert mock_token not in str(result_dict)


def test_authentication_failure_handling(mock_settings):
    """Test MSAL authentication failure (e.g. invalid secret error)."""
    service = EntraAuthService(app_settings=mock_settings)

    mock_msal_app = MagicMock()
    mock_msal_app.acquire_token_for_client.return_value = {
        "error": "invalid_client",
        "error_description": "AADSTS7000215: Invalid client secret provided.",
    }

    with patch("msal.ConfidentialClientApplication", return_value=mock_msal_app):
        # 1. get_access_token should raise EntraAuthError
        with pytest.raises(EntraAuthError) as exc_info:
            service.get_access_token()
        assert exc_info.value.error_code == "invalid_client"
        assert "AADSTS7000215" in exc_info.value.message

        # 2. verify_authentication should return safe failure model
        result = service.verify_authentication()
        assert result.success is False
        assert result.error_code == "invalid_client"
        assert "AADSTS7000215" in result.error_description
