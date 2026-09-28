"""Microsoft Entra ID (Azure AD) Client Credentials Authentication Service.

Provides secure OAuth 2.0 token acquisition and validation for background services
communicating with Microsoft Graph API using application permissions.
"""

import logging
from typing import Any, Dict, List, Optional
import msal
from pydantic import BaseModel, Field

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)


class EntraAuthError(Exception):
    """Base exception for Entra ID authentication errors."""

    def __init__(self, message: str, error_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


class MissingCredentialsError(EntraAuthError):
    """Raised when one or more required Azure credentials are not set."""

    def __init__(self, missing_fields: List[str]):
        super().__init__(
            message=f"Missing required Azure credentials in environment: {', '.join(missing_fields)}",
            error_code="missing_credentials",
            details={"missing_fields": missing_fields},
        )
        self.missing_fields = missing_fields


class TokenVerificationResult(BaseModel):
    """Safe model representing authentication verification outcome without leaking tokens or secrets."""

    success: bool = Field(..., description="Whether token acquisition was successful")
    token_type: Optional[str] = Field(None, description="OAuth 2.0 token type (e.g. Bearer)")
    expires_in: Optional[int] = Field(None, description="Token lifetime in seconds remaining")
    scopes: List[str] = Field(default_factory=list, description="Scopes granted in token")
    tenant_id_masked: Optional[str] = Field(None, description="Masked Azure tenant ID")
    client_id_masked: Optional[str] = Field(None, description="Masked Azure application/client ID")
    authority: Optional[str] = Field(None, description="Entra ID authority URL used")
    error_code: Optional[str] = Field(None, description="Error code if authentication failed")
    error_description: Optional[str] = Field(None, description="Sanitized failure description")
    message: str = Field(..., description="Human-readable result summary")


class EntraAuthService:
    """Service to handle Microsoft Entra ID Client Credentials authentication using MSAL."""

    def __init__(self, app_settings: Optional[Settings] = None):
        """Initialize authentication service with provided or global settings."""
        self.settings = app_settings or settings
        self._app_instance: Optional[msal.ConfidentialClientApplication] = None

    def _get_msal_app(self, tenant_id: Optional[str] = None) -> msal.ConfidentialClientApplication:
        """Create or return cached MSAL ConfidentialClientApplication."""
        is_valid, missing = self.settings.validate_azure_credentials()
        if not is_valid:
            raise MissingCredentialsError(missing_fields=missing)

        target_tenant = tenant_id or self.settings.azure_tenant_id
        authority = f"https://login.microsoftonline.com/{target_tenant}"

        if target_tenant == self.settings.azure_tenant_id:
            if self._app_instance is None:
                client_id = self.settings.azure_client_id
                client_secret = self.settings.azure_client_secret.get_secret_value()  # type: ignore

                self._app_instance = msal.ConfidentialClientApplication(
                    client_id=client_id,
                    client_credential=client_secret,
                    authority=authority,
                    validate_authority=True,
                )
            return self._app_instance
        else:
            client_id = self.settings.azure_client_id
            client_secret = self.settings.azure_client_secret.get_secret_value()  # type: ignore
            return msal.ConfidentialClientApplication(
                client_id=client_id,
                client_credential=client_secret,
                authority=authority,
                validate_authority=True,
            )

    def get_access_token(self, scopes: Optional[List[str]] = None, tenant_id: Optional[str] = None) -> str:
        """Acquire a raw access token for internal backend usage (never expose to clients/logs).

        Args:
            scopes: Optional custom scopes list. Defaults to configured settings.
            tenant_id: Optional target tenant ID for cross-tenant token acquisition.

        Returns:
            str: Bearer access token string.

        Raises:
            MissingCredentialsError: When required environment variables are absent.
            EntraAuthError: When Microsoft Entra ID rejects the credentials or token acquisition fails.
        """
        requested_scopes = scopes or self.settings.graph_scopes_list
        msal_app = self._get_msal_app(tenant_id=tenant_id)

        logger.debug("Requesting access token for client credentials flow with scopes: %s on tenant: %s", requested_scopes, tenant_id or self.settings.azure_tenant_id)
        result = msal_app.acquire_token_for_client(scopes=requested_scopes)

        if not result or "access_token" not in result:
            error_code = result.get("error", "token_acquisition_failed") if result else "empty_response"
            error_desc = (
                result.get("error_description", "No access token was returned by Entra ID")
                if result
                else "Empty response from MSAL"
            )
            # Log failure with sanitized details (never log secret or token)
            logger.error(
                "Entra ID token acquisition failed. Error: %s, Description: %s, Tenant: %s, Client: %s",
                error_code,
                error_desc,
                tenant_id or self.settings.get_masked_tenant_id(),
                self.settings.get_masked_client_id(),
            )
            raise EntraAuthError(
                message=f"Failed to acquire Microsoft Graph token: {error_desc}",
                error_code=error_code,
                details={"error": error_code, "description": error_desc},
            )

        logger.info(
            "Successfully acquired Microsoft Graph access token (type: %s, expires in: %ss, tenant: %s)",
            result.get("token_type", "Bearer"),
            result.get("expires_in"),
            tenant_id or self.settings.azure_tenant_id,
        )
        return result["access_token"]

    def verify_authentication(self, scopes: Optional[List[str]] = None) -> TokenVerificationResult:
        """Perform a safe verification test of the Entra ID credentials without leaking secrets or tokens.

        Returns:
            TokenVerificationResult: Safe diagnostics model.
        """
        requested_scopes = scopes or self.settings.graph_scopes_list
        tenant_masked = self.settings.get_masked_tenant_id()
        client_masked = self.settings.get_masked_client_id()
        authority = self.settings.authority_url

        # Check for missing credentials first
        is_valid, missing = self.settings.validate_azure_credentials()
        if not is_valid:
            return TokenVerificationResult(
                success=False,
                error_code="missing_credentials",
                error_description=f"Missing required environment variables: {', '.join(missing)}",
                tenant_id_masked=tenant_masked,
                client_id_masked=client_masked,
                authority=authority,
                scopes=requested_scopes,
                message=(
                    f"Authentication verification failed: missing required environment variables ({', '.join(missing)}). "
                    "Please configure them in your local .env file."
                ),
            )

        try:
            msal_app = self._get_msal_app()
            result = msal_app.acquire_token_for_client(scopes=requested_scopes)

            if not result or "access_token" not in result:
                err_code = result.get("error", "unknown_auth_error") if result else "empty_response"
                err_desc = (
                    result.get("error_description", "Unknown error acquiring token from Entra ID")
                    if result
                    else "Empty response from MSAL"
                )
                return TokenVerificationResult(
                    success=False,
                    error_code=err_code,
                    error_description=err_desc,
                    tenant_id_masked=tenant_masked,
                    client_id_masked=client_masked,
                    authority=authority,
                    scopes=requested_scopes,
                    message=f"Authentication failed: {err_code} - {err_desc}",
                )

            # Verification succeeded! Note: We specifically do NOT include 'access_token' in the output model.
            return TokenVerificationResult(
                success=True,
                token_type=result.get("token_type", "Bearer"),
                expires_in=result.get("expires_in"),
                scopes=requested_scopes,
                tenant_id_masked=tenant_masked,
                client_id_masked=client_masked,
                authority=authority,
                message="Microsoft Entra ID Client Credentials authentication succeeded.",
            )

        except MissingCredentialsError as exc:
            return TokenVerificationResult(
                success=False,
                error_code=exc.error_code,
                error_description=exc.message,
                tenant_id_masked=tenant_masked,
                client_id_masked=client_masked,
                authority=authority,
                scopes=requested_scopes,
                message=exc.message,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Unexpected error during Entra ID verification")
            return TokenVerificationResult(
                success=False,
                error_code="unexpected_error",
                error_description=str(exc),
                tenant_id_masked=tenant_masked,
                client_id_masked=client_masked,
                authority=authority,
                scopes=requested_scopes,
                message=f"Unexpected authentication error: {str(exc)}",
            )


# Reusable service instance
entra_auth_service = EntraAuthService()
