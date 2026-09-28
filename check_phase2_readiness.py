"""Phase 2 Azure readiness check script."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.core.config import settings
from app.auth.entra_auth import entra_auth_service
import httpx


async def check():
    print("=== PHASE 2 AZURE READINESS CHECK ===")
    print()

    # ── 1. .env values ──────────────────────────────────────────────────────────
    print("[1] Environment Config")
    print(f"   AZURE_TENANT_ID     : {settings.get_masked_tenant_id()}")
    print(f"   AZURE_CLIENT_ID     : {settings.get_masked_client_id()}")
    secret = settings.azure_client_secret
    print(f"   AZURE_CLIENT_SECRET : {'SET' if secret and secret.get_secret_value() else 'MISSING'}")
    print(f"   AZURE_BOT_USER_EMAIL: {settings.azure_bot_user_email or 'MISSING'}")
    bot_app_id_attr = getattr(settings, "azure_bot_app_id", None)
    print(f"   AZURE_BOT_APP_ID    : {bot_app_id_attr or 'NOT IN CONFIG'}")
    bot_name_attr = getattr(settings, "azure_bot_display_name", None)
    print(f"   AZURE_BOT_DISPLAY_NAME: {bot_name_attr or 'NOT IN CONFIG'}")
    print()

    token = entra_auth_service.get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    base = "https://graph.microsoft.com/v1.0"
    client_id = settings.azure_client_id
    tenant_id = settings.azure_tenant_id

    async with httpx.AsyncClient(timeout=25) as c:

        # ── 2. Granted Application Permissions ──────────────────────────────────
        print("[2] App Role Assignments (application permissions with admin consent)")
        sp_resp = await c.get(
            f"{base}/servicePrincipals?$filter=appId eq '{client_id}'",
            headers=headers,
        )
        sp_id = None
        if sp_resp.status_code == 200 and sp_resp.json().get("value"):
            sp = sp_resp.json()["value"][0]
            sp_id = sp["id"]
            print(f"   Service Principal   : {sp.get('displayName','?')} (objectId: {sp_id[:8]}...)")

            # Resolve Graph SP role names
            graph_sp_resp = await c.get(
                f"{base}/servicePrincipals?$filter=appId eq '00000003-0000-0000-c000-000000000000'",
                headers=headers,
            )
            role_map = {}
            if graph_sp_resp.status_code == 200 and graph_sp_resp.json().get("value"):
                for ar in graph_sp_resp.json()["value"][0].get("appRoles", []):
                    role_map[ar["id"]] = ar["value"]

            roles_resp = await c.get(
                f"{base}/servicePrincipals/{sp_id}/appRoleAssignments",
                headers=headers,
            )
            if roles_resp.status_code == 200:
                roles = roles_resp.json().get("value", [])
                granted = [role_map.get(r["appRoleId"], r["appRoleId"]) for r in roles]
                print(f"   Granted permissions : {granted}")
                calls_join = any("Calls.JoinGroupCall" in n for n in granted)
                calls_media = any("Calls.AccessMedia" in n for n in granted)
                cal_read    = any("Calendars.Read" in n for n in granted)
                user_read   = any("User.Read" in n for n in granted)
                print(f"   Calendars.Read.All         : {'YES v' if cal_read else 'NO x'}")
                print(f"   User.Read.All              : {'YES v' if user_read else 'NO x'}")
                print(f"   Calls.JoinGroupCall.All    : {'YES v' if calls_join else 'NO - MISSING for Phase 2'}")
                print(f"   Calls.AccessMedia.All      : {'YES v' if calls_media else 'NO - needed for Phase 3'}")
            else:
                print(f"   Could not list app roles: {roles_resp.status_code}")
        else:
            print(f"   SP lookup failed: {sp_resp.status_code} {sp_resp.text[:120]}")
        print()

        # ── 3. Azure Bot Service (via ARM) ───────────────────────────────────────
        print("[3] Azure Bot Service Registration (via Azure Resource Manager)")
        try:
            arm_token = entra_auth_service.get_access_token(
                scopes=["https://management.azure.com/.default"]
            )
            arm_h = {"Authorization": f"Bearer {arm_token}", "Accept": "application/json"}
            sub_resp = await c.get(
                "https://management.azure.com/subscriptions?api-version=2022-12-01",
                headers=arm_h,
            )
            if sub_resp.status_code == 200:
                subs = sub_resp.json().get("value", [])
                print(f"   Azure Subscriptions : {len(subs)} accessible")
                for sub in subs:
                    sub_id = sub["subscriptionId"]
                    sub_name = sub.get("displayName", "?")
                    print(f"   -> {sub_name} ({sub_id})")
                    bot_resp = await c.get(
                        f"https://management.azure.com/subscriptions/{sub_id}/providers"
                        f"/Microsoft.BotService/botServices?api-version=2022-09-15",
                        headers=arm_h,
                    )
                    if bot_resp.status_code == 200:
                        bots = bot_resp.json().get("value", [])
                        print(f"      Bot Services found: {len(bots)}")
                        for bot in bots:
                            props = bot.get("properties", {})
                            bot_name = bot.get("name", "?")
                            bot_app_id = props.get("msaAppId", "?")
                            endpoint = props.get("endpoint", "(none)")
                            matches_client = bot_app_id == client_id

                            ch_resp = await c.get(
                                f"https://management.azure.com{bot['id']}/channels?api-version=2022-09-15",
                                headers=arm_h,
                            )
                            channel_names = []
                            teams_channel = False
                            if ch_resp.status_code == 200:
                                for ch in ch_resp.json().get("value", []):
                                    cname = ch.get("name", "?")
                                    channel_names.append(cname)
                                    if "MsTeams" in cname or "teams" in cname.lower():
                                        teams_channel = True

                            print(f"      Bot Name      : {bot_name}")
                            print(f"      Bot App ID    : {bot_app_id[:8]}...")
                            print(f"      Endpoint      : {endpoint}")
                            print(f"      Channels      : {channel_names}")
                            print(f"      App ID == AZURE_CLIENT_ID : {'YES v' if matches_client else 'NO - MISMATCH'}")
                            print(f"      Teams Channel configured  : {'YES v' if teams_channel else 'NO - MISSING for Phase 2'}")
                    else:
                        print(f"      Bot query: {bot_resp.status_code} {bot_resp.text[:80]}")
            else:
                print(f"   ARM query: {sub_resp.status_code} - ARM scope likely not granted")
        except Exception as e:
            print(f"   ARM check error: {e}")
        print()

        # ── 4. Calendar / Join URL check ─────────────────────────────────────────
        print("[4] Live Calendar -- Teams meeting join URL availability")
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=7)
        cal_resp = await c.get(
            f"{base}/users/{settings.azure_bot_user_email}/calendarView",
            params={
                "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "$top": 10,
                "$select": "id,subject,start,end,isOnlineMeeting,onlineMeetingProvider,onlineMeeting,onlineMeetingUrl",
            },
            headers=headers,
        )
        if cal_resp.status_code == 200:
            events = cal_resp.json().get("value", [])
            print(f"   Calendar events (next 7 days) : {len(events)}")
            teams_count = 0
            for ev in events:
                subject = ev.get("subject", "?")
                provider = ev.get("onlineMeetingProvider", "")
                om = ev.get("onlineMeeting") or {}
                join_url = om.get("joinUrl") or ev.get("onlineMeetingUrl", "")
                is_teams = "teams" in str(provider).lower() or "teams.microsoft.com" in str(join_url).lower()
                if is_teams:
                    teams_count += 1
                    masked_url = join_url[:70] + "..." if len(join_url) > 70 else join_url
                    print(f"   [TEAMS] \"{subject}\"")
                    print(f"           joinWebUrl : {masked_url}")
                    print(f"           joinUrl present: {'YES v' if join_url else 'NO - cannot join without this'}")
                else:
                    print(f"   [OTHER] \"{subject}\" (provider={provider or 'none'})")
            if teams_count == 0:
                print("   No Teams meetings in next 7 days.")
                print("   ACTION: Schedule a Teams meeting on the bot calendar to test Phase 2.")
        else:
            err = cal_resp.json().get("error", {})
            print(f"   Calendar query: {cal_resp.status_code} - {err.get('code','?')}: {err.get('message','')[:100]}")


asyncio.run(check())
