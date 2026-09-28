"""Microsoft Graph API Service.

Wraps Microsoft Graph endpoints using the Entra ID application token.
"""

import logging
from typing import Any, Dict, Optional
import httpx
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field

from app.auth.entra_auth import EntraAuthService, entra_auth_service
from app.core.config import Settings, settings

logger = logging.getLogger(__name__)


class GraphConnectivityResult(BaseModel):
    """Safe model summarizing Microsoft Graph API connectivity test."""

    success: bool = Field(..., description="Whether the call to Graph API succeeded")
    status_code: Optional[int] = Field(None, description="HTTP status code returned by Microsoft Graph")
    endpoint_tested: str = Field(..., description="Graph endpoint used for verification")
    tenant_id_masked: Optional[str] = Field(None, description="Masked tenant ID")
    client_id_masked: Optional[str] = Field(None, description="Masked client ID")
    organization_name: Optional[str] = Field(None, description="Display name of tenant organization if available")
    error_code: Optional[str] = Field(None, description="Error code if Graph request failed")
    error_message: Optional[str] = Field(None, description="Sanitized error description")
    message: str = Field(..., description="Human readable summary of connectivity")


class MicrosoftGraphService:
    """Service to interact with Microsoft Graph API using application permissions."""

    def __init__(
        self,
        auth_service: Optional[EntraAuthService] = None,
        app_settings: Optional[Settings] = None,
    ):
        """Initialize Graph service with auth service and configuration."""
        self.auth_service = auth_service or entra_auth_service
        self.settings = app_settings or settings

    def _get_headers(self) -> Dict[str, str]:
        """Construct HTTP headers with Bearer token."""
        token = self.auth_service.get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"{self.settings.app_name}/{self.settings.app_version}",
        }

    async def verify_graph_connectivity(self, target_email: Optional[str] = None) -> GraphConnectivityResult:
        """Validate Graph API connectivity for calendar access.

        Checks for a target email, then performs a lightweight ``/users/{email}/calendarView`` request
        with a one‑minute window and ``$top=1``. Returns a :class:`GraphConnectivityResult`.
        """
        # Resolve the effective email, preferring the explicit argument.
        effective_email = target_email
        # If not provided, attempt to use the configured email, but only if it was explicitly set.
        if effective_email is None:
            effective_email = getattr(self.settings, "azure_bot_user_email", None)
        # If still missing, return a controlled error without making any network calls.
        if not effective_email:
            return GraphConnectivityResult(
                success=False,
                status_code=None,
                endpoint_tested="N/A",
                tenant_id_masked=self.settings.get_masked_tenant_id(),
                client_id_masked=self.settings.get_masked_client_id(),
                organization_name=None,
                error_code="missing_target_email",
                error_message="Target email is required for Graph connectivity verification. Set AZURE_BOT_USER_EMAIL.",
                message="Microsoft Graph connectivity verification failed: missing target email.",
            )
        # Optional domain check – log warning if not expected domain.
        if "@" in effective_email and not effective_email.lower().endswith("@csharptek.com"):
            logger.warning("Target email %s does not belong to the expected domain.", effective_email)
        now_utc = datetime.now(timezone.utc)
        start_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = (now_utc + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        endpoint = f"{self.settings.microsoft_graph_base_url}/users/{effective_email}/calendarView"
        params = {"startDateTime": start_iso, "endDateTime": end_iso, "$top": 1}
        headers = self._get_headers()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(endpoint, params=params, headers=headers)
        except Exception as exc:
            return GraphConnectivityResult(
                success=False,
                status_code=None,
                endpoint_tested=endpoint,
                tenant_id_masked=self.settings.get_masked_tenant_id(),
                client_id_masked=self.settings.get_masked_client_id(),
                organization_name=None,
                error_code="network_error",
                error_message=str(exc),
                message="Network error during Microsoft Graph connectivity verification.",
            )
        if response.status_code == 200:
            return GraphConnectivityResult(
                success=True,
                status_code=response.status_code,
                endpoint_tested=endpoint,
                tenant_id_masked=self.settings.get_masked_tenant_id(),
                client_id_masked=self.settings.get_masked_client_id(),
                organization_name=None,
                error_code=None,
                error_message=None,
                message="Microsoft Graph API connectivity verified successfully.",
            )
        else:
            error_json = {}
            try:
                error_json = response.json().get("error", {})
            except Exception:
                pass
            err_code = error_json.get("code", f"http_{response.status_code}")
            err_msg = error_json.get("message", response.text[:200])
            return GraphConnectivityResult(
                success=False,
                status_code=response.status_code,
                endpoint_tested=endpoint,
                tenant_id_masked=self.settings.get_masked_tenant_id(),
                client_id_masked=self.settings.get_masked_client_id(),
                organization_name=None,
                error_code=err_code,
                error_message=err_msg,
                message=f"Microsoft Graph API connectivity test failed: {err_code}",
            )
# Reusable singleton instance
graph_service = MicrosoftGraphService()
