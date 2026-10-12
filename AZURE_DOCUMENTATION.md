# TekMeet - Azure Cloud Services & Architecture Documentation

## 1. Executive Summary

TekMeet is an automated AI meeting assistant and notetaker designed for Microsoft Teams. To seamlessly detect scheduled meetings, join live Teams calls, capture real-time audio, and distribute AI-generated meeting summaries, TekMeet leverages an integrated suite of **Microsoft Azure** and **Microsoft 365 / Entra ID** cloud services.

This document details every Azure service utilized by TekMeet, why each service is required, the networking and security configurations, deployment instructions, and troubleshooting procedures.

---

## 2. End-to-End Azure Architecture Overview

TekMeet uses a hybrid architecture combining a **FastAPI Backend (Python)**, a **Real-Time Media Worker on an Azure Windows Virtual Machine (.NET Framework 4.8)**, and **Microsoft Entra ID / Azure Bot Service** for Teams interoperability.

```mermaid
flowchart TD
    subgraph Azure_Cloud ["Microsoft Azure Cloud & M365"]
        subgraph Entra ["Microsoft Entra ID (Azure AD)"]
            AppReg["App Registration\n(Client ID, Secret, Tenant)"]
            GraphPerms["API Permissions:\n• Calendars.Read.All\n• User.Read.All\n• Calls.JoinGroupCall.All\n• Calls.AccessMedia.All\n• Mail.Send"]
        end

        subgraph BotService ["Azure Bot Service"]
            BotReg["Azure Bot Channels Registration\n(TekMeet Notetaker)"]
            TeamsChannel["Teams Channel Configuration\n(Calling Enabled)"]
        end

        subgraph MediaVM ["Azure Windows Virtual Machine (East US)"]
            NSG["Network Security Group (NSG)\nTCP 8445, 5050 | UDP 20000-20050"]
            PublicIP["Static Public IP & Azure FQDN\n(tekmeet-media-vm231.eastus.cloudapp.azure.com)"]
            CertStore["Windows Certificate Store\nLocalMachine\\My (TLS Certificate)"]
            MW["MediaWorker.exe (.NET 4.8 x64)\nMicrosoft.Skype.Bots.Media SDK"]
        end

        TeamsRelay["Microsoft Teams Media Relays"]
    end

    subgraph TekMeet_Backend ["TekMeet Core Backend (FastAPI)"]
        Scheduler["Meeting Scheduler / Poller"]
        CallingService["Graph Calling Service"]
        AI_Pipeline["AI Pipeline\n(Deepgram + Claude / Gemini)"]
        EmailService["Email Delivery Service"]
    end

    %% Interactions
    Scheduler -->|1. Poll Calendar Events| AppReg
    AppReg -->|OAuth Token| TekMeet_Backend
    CallingService -->|2. Get Media Config Blob| MW
    CallingService -->|3. POST /communications/calls| BotReg
    BotReg -->|Signaling Webhook| CallingService
    TeamsRelay <-->|4. Live SRTP/RTP Audio Stream (UDP 20000-20050)| MW
    MW -->|5. Save WAV Audio| TekMeet_Backend
    TekMeet_Backend -->|6. Transcribe & Summarize| AI_Pipeline
    AI_Pipeline -->|7. Send Email via Graph API| EmailService
    EmailService -->|POST /users/{email}/sendMail| AppReg
```

---

## 3. Inventory of Azure Services Used

| Azure Component | Resource Type / Technology | Purpose in TekMeet |
| :--- | :--- | :--- |
| **Microsoft Entra ID** | App Registration & Enterprise Service Principal | Authentication, OAuth2 client credentials flow, calendar polling, and API authorization. |
| **Azure Bot Service** | Azure Bot (`Microsoft.BotService/botServices`) | Identity for the bot within Microsoft Teams, webhook callback routing, and Teams calling channel. |
| **Azure Virtual Machine** | Windows Server 2022 Datacenter x64 VM | Hosts the C# Media Worker. Required because `Microsoft.Skype.Bots.Media` SDK requires native Windows drivers. |
| **Azure Public IP & DNS** | Static IPv4 + Azure FQDN (`.cloudapp.azure.com`) | Publicly resolvable domain and static IP for Teams real-time media exchange. |
| **Azure Virtual Network & NSG** | VNet, Subnet, Network Security Group | Restricts and routes TCP signaling (8445), HTTP bridge (5050), and UDP media streams (20000–20050). |
| **Microsoft Graph Communications** | Cloud Communications API (`/communications/calls`) | API interface used to join meetings, manage participant states, and trigger recording banners. |

---

## 4. Deep Dive: Component Details & Configuration

### 4.1 Microsoft Entra ID (Azure Active Directory)

Entra ID acts as the identity and authorization authority for TekMeet.

#### Required App Registration Settings
* **Supported Account Types**: Accounts in this organizational directory only (Single tenant) or Multitenant (depending on enterprise requirements).
* **Application (client) ID**: Referenced in `.env` as `AZURE_CLIENT_ID` / `AZURE_BOT_APP_ID`.
* **Directory (tenant) ID**: Referenced in `.env` as `AZURE_TENANT_ID`.
* **Client Secret**: Generated under *Certificates & secrets* -> `AZURE_CLIENT_SECRET`.

#### Application Permissions (Microsoft Graph)
These permissions require **Grant admin consent for <Tenant>**:

| Permission Name | Type | Justification / Usage in TekMeet |
| :--- | :--- | :--- |
| **`Calendars.Read.All`** | Application | Allows background polling service to read scheduled meeting events from the bot user's calendar (`/users/{email}/calendarView`). |
| **`User.Read.All`** | Application | Resolves meeting attendee emails, display names, and organizer details. |
| **`Calls.JoinGroupCall.All`** | Application | Allows the bot to autonomously join scheduled Teams group meetings via Graph Calling API. |
| **`Calls.AccessMedia.All`** | Application | Authorizes access to app-hosted media streams (real-time audio stream). |
| **`Mail.Send`** | Application | Enables sending meeting summary and action item emails directly to attendees via `/users/{email}/sendMail`. |

---

### 4.2 Azure Bot Service (Bot Channels Registration)

Azure Bot Service acts as the public entry point and persona for the meeting bot in Microsoft Teams.

#### Configuration Requirements
1. **Bot Profile**:
   * **Display Name**: `TekMeet Notetaker` (`AZURE_BOT_DISPLAY_NAME`).
   * **Microsoft App ID**: Must match the Entra ID Application ID (`AZURE_CLIENT_ID`).
2. **Configuration Settings**:
   * **Messaging Endpoint**: `https://<public_backend_url>/api/v1/calls/callback` (or your reverse-proxy / dev tunnel URL).
   * **Calling Webhook**: In the Bot settings, configure the Graph communications callback URI.
3. **Channels**:
   * Add the **Microsoft Teams** channel.
   * Under the Teams channel settings, navigate to the **Calling** tab:
     * Check **"Enable calling"**.
     * Set the Webhook URL for incoming call notifications.

---

### 4.3 Azure Virtual Machine (Real-Time Media Worker)

#### Why an Azure VM is Mandatory
Microsoft's real-time media pipeline for Teams relies on `Microsoft.Skype.Bots.Media` (.NET Framework 4.8 x64). This SDK uses native C++ Windows components that:
* Cannot run on Linux or standard Linux Docker containers.
* Require direct access to the Windows network stack and certificate store.
* Cannot be hosted on standard serverless architectures (Azure Functions / AWS Lambda) due to continuous socket connections and UDP media streaming.

#### VM Specifications
* **Operating System**: Windows Server 2022 Datacenter (or Windows 10/11 Enterprise x64).
* **Architecture**: x64 (AMD64).
* **Location**: Azure East US (recommended near the Teams tenant media region).
* **FQDN**: `tekmeet-media-vm231.eastus.cloudapp.azure.com`
* **Static Public IP**: E.g., `168.62.176.182`

#### Network Security Group (NSG) Rules
The following inbound security rules must be configured in Azure NSG for the VM's Network Interface:

| Priority | Name | Port Range | Protocol | Source | Purpose |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **100** | `Allow-Call-Signaling` | `8445` | TCP | Any / Azure Cloud | Media signaling mutual-TLS communication with Microsoft Media Relays. |
| **110** | `Allow-Media-Streams` | `20000 - 20050` | UDP | Any / Teams IPs | Direct RTP/SRTP real-time audio packet exchange. |
| **120** | `Allow-Http-Bridge` | `5050` | TCP | Backend IP / VNet | Internal HTTP bridge for TekMeet Backend to fetch media configuration blobs and retrieve recordings. |
| **300** | `Allow-RDP` | `3389` | TCP | Admin IP Range | Remote management access (restricted). |

---

### 4.4 Windows Certificate Store & Media Platform SSL

Microsoft Media Platform requires mutual TLS authentication using an X.509 certificate.

1. **Certificate Requirements**:
   * Subject Name or Subject Alternative Name (SAN) must match `MEDIA_FQDN` (e.g. `tekmeet-media-vm231.eastus.cloudapp.azure.com`).
   * Key usage: Digital Signature, Key Encipherment.
   * Exportable private key.
2. **Storage Location**:
   * Must be imported into Windows `Cert:\LocalMachine\My` (`StoreLocation.LocalMachine`, `StoreName.My`).
   * The application account running `MediaWorker.exe` must have Read permissions on the certificate's private key.
3. **Configuration**:
   * The certificate's SHA-1 thumbprint without spaces (e.g. `88D6F42FF4F7139D0A434E2CFAB43E3B05045AC4`) is passed to `CERTIFICATE_THUMBPRINT`.

---

## 5. Environment Variables Reference (.env)

The table below outlines all Azure-related configuration keys in TekMeet:

```ini
# ==============================================================================
# Microsoft Entra ID (Azure AD) Credentials
# ==============================================================================
AZURE_TENANT_ID=<your-azure-tenant-id>
AZURE_CLIENT_ID=<your-azure-client-id>
AZURE_CLIENT_SECRET=<your-azure-client-secret>

# Target Calendar Mailbox (User Principal Name or email address)
AZURE_BOT_USER_EMAIL="tekmeettest@yourtenant.onmicrosoft.com"

# ==============================================================================
# Azure Bot Service Identity
# ==============================================================================
AZURE_BOT_APP_ID=<your-azure-client-id>
AZURE_BOT_DISPLAY_NAME="TekMeet Notetaker"
BOT_CALLBACK_URL=https://<your-backend-fqdn>/api/v1/calls/callback

# ==============================================================================
# Azure VM Media Worker Configuration
# ==============================================================================
MEDIA_FQDN=tekmeet-media-vm.eastus.cloudapp.azure.com
PUBLIC_IP=<your-vm-public-ip>
CERTIFICATE_THUMBPRINT=<your-cert-thumbprint>
MEDIA_INTERNAL_PORT=8445
MEDIA_PUBLIC_PORT=8445
MEDIA_UDP_PORT_START=20000
MEDIA_UDP_PORT_END=20050

# Mode Switch: Set to true for live VM audio capture
USE_APP_HOSTED_MEDIA=true
MEDIA_WORKER_URL=http://<your-vm-or-local-ip>:5050

# ==============================================================================
# Azure Graph Mail Delivery
# ==============================================================================
EMAIL_PROVIDER=graph
EMAIL_SENDER_ADDRESS=tekmeettest@yourtenant.onmicrosoft.com
EMAIL_SENDER_NAME="TekMeet"
```

---

## 6. End-to-End Execution Flow

1. **Calendar Polling**:
   * TekMeet's `calendar_service.py` runs on a scheduled interval (`POLL_INTERVAL_SECONDS=30`).
   * It calls `GET https://graph.microsoft.com/v1.0/users/{AZURE_BOT_USER_EMAIL}/calendarView`.
   * When an online Teams meeting is detected within `JOIN_BUFFER_SECONDS`, join dispatch starts.

2. **Media Worker Configuration Exchange**:
   * `graph_calling_service.py` connects to the Azure VM via `GET http://168.62.176.182:5050/api/media/config`.
   * The C# service initializes `MediaPlatform` using the public FQDN, public IP, and certificate thumbprint.
   * It returns an opaque Base64 `appHostedMediaConfig` blob.

3. **Joining the Teams Call**:
   * Backend issues `POST https://graph.microsoft.com/v1.0/communications/calls` with:
     * `mediaConfig`: `appHostedMediaConfig` containing the blob.
     * `callbackUri`: Registered `BOT_CALLBACK_URL`.
     * `meetingInfo`: Extracted Teams `threadId` and `organizer` identity.
   * Microsoft Graph notifies Azure Bot Service, which displays "TekMeet Notetaker" in the meeting roster.

4. **Recording Status Banner**:
   * Upon establishing the call, `update_recording_status()` sends:
     `POST /communications/calls/{call_id}/updateRecordingStatus` with `status: "recording"`.
   * This displays the standard Teams compliance banner: *"Recording has started. By attending, you consent to being recorded."*

5. **Audio Capture on Azure VM**:
   * Audio frames arrive at the VM on UDP ports `20000-20050`.
   * `AudioRecordingService.cs` captures real-time 16-bit PCM uncompressed audio samples.
   * Upon call completion, the audio file is converted/saved as `.wav`.

6. **Transcription, Summarization & Notification**:
   * Backend downloads or reads the WAV audio.
   * Deepgram converts speech to text.
   * Claude or Gemini generates formatted summaries, key decisions, and action items.
   * `email_service.py` sends the summary to all meeting attendees via Microsoft Graph Mail API (`Mail.Send`).

---

## 7. Diagnostics and Verification Scripts

TekMeet includes built-in diagnostic scripts to verify your Azure setup at each layer:

### 1. Entra ID & Bot Readiness Check
Run the comprehensive Phase 2 readiness check to verify credentials, admin consent, ARM bot service status, and calendar events:
```bash
python check_phase2_readiness.py
```

### 2. Azure VM Media Worker Health Check
Verify that the Azure VM is running and its HTTP bridge is reachable:
```python
import urllib.request

# Health endpoint
resp = urllib.request.urlopen("http://168.62.176.182:5050/health", timeout=5)
print("VM Status:", resp.read().decode())

# Media config blob generation
cfg = urllib.request.urlopen("http://168.62.176.182:5050/api/media/config", timeout=5)
print("Media Config Blob:", cfg.read().decode()[:80] + "...")
```

### 3. Verify Certificate Installation (PowerShell on Azure VM)
```powershell
Get-ChildItem Cert:\LocalMachine\My | Select-Object Subject, Thumbprint, NotAfter
```

---

## 8. Troubleshooting Common Azure Issues

| Symptom | Root Cause | Resolution |
| :--- | :--- | :--- |
| `401 Unauthorized` on Graph endpoints | Expired or incorrect `AZURE_CLIENT_SECRET`. | Generate a new client secret in Entra ID and update `.env`. |
| `403 Forbidden` on `/communications/calls` | Missing Application Permissions or Admin Consent. | In Entra ID, ensure `Calls.JoinGroupCall.All` and `Calls.AccessMedia.All` are added with Admin Consent granted. |
| `Could not connect to Media Worker at http://...:5050` | Azure VM is stopped, or NSG port `5050` is blocked. | Start the Azure VM in the Azure portal and verify inbound rule on NSG for port 5050. |
| `MediaPlatform initialization failed: Certificate not found` | The TLS certificate is in `CurrentUser\My` instead of `LocalMachine\My`, or thumbprint mismatch. | Run `install_cert.ps1` as Administrator on the VM to place certificate into `LocalMachine\My`. |
| Bot joins call but immediately disconnects | Media signaling ports (TCP 8445) or UDP (20000–20050) are unreachable by Microsoft relays. | Ensure Azure NSG allows inbound TCP 8445 and UDP 20000–20050 from any source. |
| Meeting summary emails fail to send | `Mail.Send` permission missing or target sender mailbox unauthorized. | Grant `Mail.Send` in Entra ID and verify `EMAIL_SENDER_ADDRESS` is a licensed Exchange mailbox. |
