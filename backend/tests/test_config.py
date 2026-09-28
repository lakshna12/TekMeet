"""Tests for application settings and configuration loading."""

import pytest
from pydantic import SecretStr
from app.core.config import Settings


def test_settings_default_values():
    """Test default values when no environment variables are set."""
    custom_settings = Settings(
        AZURE_TENANT_ID=None,
        AZURE_CLIENT_ID=None,
        AZURE_CLIENT_SECRET=None,
    )
    is_valid, missing = custom_settings.validate_azure_credentials()
    assert is_valid is False
    assert set(missing) == {"AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"}
    assert custom_settings.get_masked_client_id() is None
    assert custom_settings.get_masked_tenant_id() is None
    assert custom_settings.authority_url == "https://login.microsoftonline.com/common"


def test_settings_with_values():
    """Test settings with valid Azure credentials."""
    custom_settings = Settings(
        AZURE_TENANT_ID="00000000-1111-2222-3333-444444444444",
        AZURE_CLIENT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        AZURE_CLIENT_SECRET=SecretStr("super_secret_value_12345"),
        MICROSOFT_GRAPH_SCOPES="https://graph.microsoft.com/.default",
    )
    is_valid, missing = custom_settings.validate_azure_credentials()
    assert is_valid is True
    assert missing == []
    assert custom_settings.authority_url == "https://login.microsoftonline.com/00000000-1111-2222-3333-444444444444"
    assert custom_settings.get_masked_client_id() == "aaaa****eeee"
    assert custom_settings.get_masked_tenant_id() == "0000****4444"
    # Ensure SecretStr does not leak in str representation
    assert "super_secret_value_12345" not in str(custom_settings.azure_client_secret)
    assert custom_settings.azure_client_secret.get_secret_value() == "super_secret_value_12345"


def test_settings_partial_missing():
    """Test partial credential configuration detection."""
    custom_settings = Settings(
        AZURE_TENANT_ID="tenant-123",
        AZURE_CLIENT_ID=None,
        AZURE_CLIENT_SECRET=SecretStr("secret-123"),
    )
    is_valid, missing = custom_settings.validate_azure_credentials()
    assert is_valid is False
    assert missing == ["AZURE_CLIENT_ID"]
