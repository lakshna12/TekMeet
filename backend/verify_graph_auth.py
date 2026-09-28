#!/usr/bin/env python3
"""TekMeet Phase 1 - Microsoft Graph Authentication Verification CLI.

Executes a local test of the Microsoft Entra ID Client Credentials authentication flow
against Microsoft Graph, validating credentials without leaking secrets or tokens.

Usage:
    python backend/verify_graph_auth.py
    or:
    python verify_graph_auth.py (from inside backend/)
"""

import asyncio
import os
import sys
from pathlib import Path

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from app.auth.entra_auth import entra_auth_service
from app.services.graph_service import graph_service


def print_banner():
    print("=" * 70)
    print("  TekMeet -- Phase 1: Microsoft Graph Authentication Verification")
    print("=" * 70)


def print_troubleshooting(error_code: str, error_desc: str):
    """Print targeted troubleshooting guidance based on Entra ID error codes."""
    print("\n  Troubleshooting Guidance:")
    desc_lower = (error_desc or "").lower()

    if "aadsts700016" in desc_lower or "application was not found" in desc_lower:
        print("  - [!] The AZURE_CLIENT_ID was not found in the specified directory.")
        print("    -> Check if AZURE_CLIENT_ID matches the Application (client) ID in Azure Portal.")
        print("    -> Check if AZURE_TENANT_ID matches the Directory (tenant) ID in Azure Portal.")
    elif "aadsts7000215" in desc_lower or "invalid client secret" in desc_lower:
        print("  - [!] The AZURE_CLIENT_SECRET provided is incorrect or has expired.")
        print("    -> In Azure Portal > App registrations > Certificates & secrets, generate a new client secret.")
        print("    -> Make sure you copy the 'Value' field, not the 'Secret ID'.")
    elif "aadsts90002" in desc_lower or "tenant" in desc_lower:
        print("  - [!] The AZURE_TENANT_ID was not found.")
        print("    -> Confirm the Tenant ID from the Overview page of your Azure Portal.")
    elif "aadsts65001" in desc_lower or "consent" in desc_lower:
        print("  - [!] Admin Consent has not been granted for the requested Graph permissions.")
        print("    -> In Azure Portal > App registrations > API permissions, click 'Grant admin consent for [Tenant]'.")
    elif error_code == "missing_credentials":
        print("  - [!] Required environment variables are missing.")
        print("    -> Create a '.env' file in project root or backend folder with:")
        print("       AZURE_TENANT_ID=<your-tenant-id>")
        print("       AZURE_CLIENT_ID=<your-client-id>")
        print("       AZURE_CLIENT_SECRET=<your-client-secret>")
    else:
        print(f"  - Error Code: {error_code}")
        print(f"  - Description: {error_desc}")
        print("  -> Verify that the App Registration exists in Microsoft Entra ID and has client secrets configured.")


async def run_verification():
    print_banner()

    # Step 1: Check Environment Configuration
    print("\n[1/3] Checking Environment Configuration...")
    is_valid, missing = settings.validate_azure_credentials()

    tenant_masked = settings.get_masked_tenant_id() or "NOT SET"
    client_masked = settings.get_masked_client_id() or "NOT SET"
    secret_status = "SET (masked)" if (settings.azure_client_secret and settings.azure_client_secret.get_secret_value()) else "NOT SET"

    print(f"  - Tenant ID:     {tenant_masked}")
    print(f"  - Client ID:     {client_masked}")
    print(f"  - Client Secret: {secret_status}")
    print(f"  - Authority:     {settings.authority_url}")
    print(f"  - Scopes:        {settings.microsoft_graph_scopes}")

    if not is_valid:
        print("\n[FAIL] Missing required configuration:")
        for m in missing:
            print(f"  * {m}")
        print_troubleshooting("missing_credentials", "")
        return 1

    print("  -> Environment configuration variables are present.")

    # Step 2: Test Entra ID Token Acquisition
    print("\n[2/3] Requesting Access Token via Entra ID Client Credentials Flow...")
    auth_result = entra_auth_service.verify_authentication()

    if not auth_result.success:
        print("\n[FAIL] Entra ID Token Acquisition Failed!")
        print(f"  - Error Code: {auth_result.error_code}")
        print(f"  - Details:    {auth_result.error_description}")
        print_troubleshooting(auth_result.error_code or "", auth_result.error_description or "")
        return 1

    print("  -> [PASS] Access Token successfully acquired from Microsoft Entra ID!")
    print(f"     * Token Type: {auth_result.token_type}")
    print(f"     * Lifetime:   {auth_result.expires_in} seconds")
    print(f"     * Scope:      {auth_result.scopes}")
    print("     * Access token is kept private and never displayed.")

    # Step 3: Test Microsoft Graph API Connectivity
    print("\n[3/3] Testing Microsoft Graph API Connectivity...")
    graph_result = await graph_service.verify_graph_connectivity()

    if not graph_result.success:
        print(f"  - [WARN] Token acquired, but Graph endpoint returned status: {graph_result.status_code}")
        print(f"  - Error Code:    {graph_result.error_code}")
        print(f"  - Error Message: {graph_result.error_message}")
        print("\n  -> Note: Token acquisition succeeded. If Graph endpoint access is restricted, verify")
        print("     that Microsoft Graph Application permissions have been granted admin consent in Azure Portal.")
        return 0  # Token acquisition succeeded, which is the primary success criteria for Step 2

    print("  -> [PASS] Microsoft Graph API connectivity verified successfully!")
    if graph_result.organization_name:
        print(f"     * Verified Organization: {graph_result.organization_name}")

    print("\n" + "=" * 70)
    print("  SUMMARY: Phase 1  Authentication is FULLY OPERATIONAL")
    print("=" * 70)
    return 0


def main():
    try:
        exit_code = asyncio.run(run_verification())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
