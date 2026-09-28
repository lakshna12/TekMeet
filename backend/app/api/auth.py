"""Authentication verification API router."""

from fastapi import APIRouter, status
from pydantic import BaseModel
from typing import Optional, List

from app.auth.entra_auth import (
    TokenVerificationResult,
    entra_auth_service,
)
from app.core.config import settings
from app.services.graph_service import (
    GraphConnectivityResult,
    graph_service,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


class AuthStatusResponse(BaseModel):
    """Safe model summarizing local configuration readiness."""

    is_configured: bool
    missing_variables: List[str]
    tenant_id_masked: Optional[str]
    client_id_masked: Optional[str]
    authority: str
    graph_scopes: List[str]


@router.get(
    "/status",
    response_model=AuthStatusResponse,
    summary="Check local Azure credentials configuration status",
)
def get_auth_status() -> AuthStatusResponse:
    """Check if all required Azure credentials are set in the environment without exposing secrets."""
    is_valid, missing = settings.validate_azure_credentials()
    return AuthStatusResponse(
        is_configured=is_valid,
        missing_variables=missing,
        tenant_id_masked=settings.get_masked_tenant_id(),
        client_id_masked=settings.get_masked_client_id(),
        authority=settings.authority_url,
        graph_scopes=settings.graph_scopes_list,
    )


@router.get(
    "/verify",
    response_model=TokenVerificationResult,
    summary="Verify Microsoft Entra ID Token Acquisition",
)
def verify_entra_authentication() -> TokenVerificationResult:
    """Verify Microsoft Entra ID client-credentials token acquisition.

    Acquires an application token from Entra ID and returns diagnostic metadata.
    Never exposes or returns the token value itself.
    """
    return entra_auth_service.verify_authentication()


@router.get(
    "/verify-graph",
    response_model=GraphConnectivityResult,
    summary="Verify Microsoft Graph API Live Connectivity",
)
async def verify_graph_connectivity() -> GraphConnectivityResult:
    """Verify that acquired Entra ID token is accepted by Microsoft Graph API.

    Calls Microsoft Graph organization endpoint using the application token.
    Never exposes secrets or token values.
    """
    return await graph_service.verify_graph_connectivity()
