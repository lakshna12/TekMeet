"""Application configuration and environment variable loading."""

import os
from pathlib import Path
from typing import List, Optional, Tuple
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """TekMeet backend configuration settings loaded from environment or .env."""

    model_config = SettingsConfigDict(
        env_file=(
            Path(__file__).resolve().parent.parent.parent / ".env",
            Path(__file__).resolve().parent.parent.parent.parent / ".env",
            ".env",
        ),
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    # Entra ID / Azure App Registration credentials
    azure_tenant_id: Optional[str] = Field(
        default=None,
        validation_alias="AZURE_TENANT_ID",
        description="Microsoft Entra ID (Azure AD) Directory / Tenant ID",
    )
    azure_client_id: Optional[str] = Field(
        default=None,
        validation_alias="AZURE_CLIENT_ID",
        description="Microsoft Entra ID Application / Client ID",
    )
    azure_client_secret: Optional[SecretStr] = Field(
        default=None,
        validation_alias="AZURE_CLIENT_SECRET",
        description="Microsoft Entra ID Application Client Secret",
    )

    # Microsoft Graph configuration
    microsoft_graph_scopes: str = Field(
        default="https://graph.microsoft.com/.default",
        validation_alias="MICROSOFT_GRAPH_SCOPES",
        description="OAuth2 scopes requested from Microsoft Graph",
    )
    microsoft_graph_base_url: str = Field(
        default="https://graph.microsoft.com/v1.0",
        validation_alias="MICROSOFT_GRAPH_BASE_URL",
        description="Microsoft Graph API base URL",
    )

    # Application settings
    app_name: str = "TekMeet Backend"
    app_version: str = "0.1.0"
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # Calendar & Polling settings
    meeting_lookahead_minutes: int = Field(
        default=1440,
        validation_alias="MEETING_LOOKAHEAD_MINUTES",
        description="Time window in minutes to look ahead for upcoming meetings (default 24h / 1440m)",
    )
    azure_bot_user_email: Optional[str] = Field(
        default=None,
        validation_alias="AZURE_BOT_USER_EMAIL",
        description="Target Microsoft 365 mailbox/user principal name for calendar querying with application permissions",
    )
    # Phase 2: Bot identity for Graph Cloud Communications Calling API
    azure_bot_app_id: Optional[str] = Field(
        default=None,
        validation_alias="AZURE_BOT_APP_ID",
        description="Azure Bot Service App ID (typically same as AZURE_CLIENT_ID). Used in Graph Calling API identity payload.",
    )
    azure_bot_display_name: str = Field(
        default="TekMeet Notetaker",
        validation_alias="AZURE_BOT_DISPLAY_NAME",
        description="Display name shown in the Teams participant list when the bot joins a call.",
    )
    bot_callback_url: str = Field(
        default="https://placeholder.example.com/api/messages",
        validation_alias="BOT_CALLBACK_URL",
        description="Bot messaging endpoint registered in Azure Bot Service. Required by Graph Calling API.",
    )
    poll_interval_seconds: int = Field(
        default=60,
        validation_alias="POLL_INTERVAL_SECONDS",
        description="Frequency in seconds for continuous background meeting polling",
    )
    join_buffer_seconds: int = Field(
        default=60,
        validation_alias="JOIN_BUFFER_SECONDS",
        description="Seconds before scheduled start_time to trigger meeting join dispatch (e.g. 60s early)",
    )
    auto_start_scheduler: bool = Field(
        default=False,
        validation_alias="AUTO_START_SCHEDULER",
        description="Whether to automatically start the background scheduler loop on application startup",
    )
    media_worker_url: str = Field(
        default="http://localhost:5050",
        validation_alias="MEDIA_WORKER_URL",
        description="URL of the C# Media Worker Service for fetching appHostedMediaConfig blobs.",
    )
    use_app_hosted_media: bool = Field(
        default=False,
        validation_alias="USE_APP_HOSTED_MEDIA",
        description="Whether to use appHostedMediaConfig (true, Phase 3 local C# worker) or serviceHostedMediaConfig (false, Phase 2 Microsoft cloud join mode)",
    )
    public_backend_url: Optional[str] = Field(
        default=None,
        validation_alias="PUBLIC_BACKEND_URL",
        description="Public backend base URL (e.g. https://api.tekmeet.com) used for email links sent to external users",
    )

    # Phase 4: AI Transcription & LLM Summarization configuration
    deepgram_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="DEEPGRAM_API_KEY",
        description="Deepgram API Key for Speech-to-Text transcription",
    )
    deepgram_model: str = Field(
        default="nova-2",
        validation_alias="DEEPGRAM_MODEL",
        description="Deepgram STT model identifier",
    )
    openai_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        description="OpenAI API Key for Whisper speech-to-text transcription",
    )
    whisper_model: str = Field(
        default="whisper-1",
        validation_alias="WHISPER_MODEL",
        description="Model name for OpenAI Whisper transcription API",
    )
    claude_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="CLAUDE_API_KEY",
        description="Anthropic Claude API Key for meeting transcript summarization",
    )
    claude_model: str = Field(
        default="claude-3-5-sonnet-20241022",
        validation_alias="CLAUDE_MODEL",
        description="Anthropic Claude LLM model identifier for summarization",
    )
    anthropic_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Alternative alias for Anthropic Claude API Key",
    )
    gemini_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
        description="Google Gemini API Key for meeting transcript summarization",
    )
    gemini_model: str = Field(
        default="gemini-1.5-flash",
        validation_alias="GEMINI_MODEL",
        description="Google Gemini LLM model identifier for summarization",
    )

    # Database Configuration - SQL Server / SQLAlchemy
    database_url: Optional[SecretStr] = Field(
        default=None,
        validation_alias="DATABASE_URL",
        description="SQL Server connection URL for SQLAlchemy (e.g. mssql+pyodbc://user:pass@host/dbname?driver=ODBC+Driver+17+for+SQL+Server)",
    )

    # Phase 5: Email Notification Delivery configuration
    email_provider: str = Field(
        default="graph",
        validation_alias="EMAIL_PROVIDER",
        description="Delivery provider: 'graph' (Microsoft Graph API), 'smtp', or 'mock'",
    )
    email_sender_address: Optional[str] = Field(
        default=None,
        validation_alias="EMAIL_SENDER_ADDRESS",
        description="Sender email address for outgoing meeting summary emails (e.g. tekmeet@csharptek.com)",
    )
    email_sender_name: str = Field(
        default="TekMeet",
        validation_alias="EMAIL_SENDER_NAME",
        description="Visible display name for outgoing meeting summary emails",
    )
    smtp_host: Optional[str] = Field(
        default=None,
        validation_alias="SMTP_HOST",
        description="SMTP Server Hostname (e.g. smtp.office365.com / smtp.gmail.com)",
    )
    smtp_port: int = Field(
        default=587,
        validation_alias="SMTP_PORT",
        description="SMTP Server Port (default 587 TLS)",
    )
    smtp_username: Optional[str] = Field(
        default=None,
        validation_alias="SMTP_USERNAME",
        description="SMTP Authentication Username / Sender Email",
    )
    smtp_password: Optional[SecretStr] = Field(
        default=None,
        validation_alias="SMTP_PASSWORD",
        description="SMTP Authentication Password / App Password",
    )
    smtp_from_email: Optional[str] = Field(
        default=None,
        validation_alias="SMTP_FROM_EMAIL",
        description="Sender email address displayed in From header",
    )
    smtp_use_tls: bool = Field(
        default=True,
        validation_alias="SMTP_USE_TLS",
        description="Whether to use TLS for SMTP connection",
    )

    @property
    def authority_url(self) -> str:
        """Return the Microsoft Entra ID OAuth 2.0 token endpoint authority URL."""
        if not self.azure_tenant_id:
            return "https://login.microsoftonline.com/common"
        return f"https://login.microsoftonline.com/{self.azure_tenant_id}"

    @property
    def graph_scopes_list(self) -> List[str]:
        """Return graph scopes as a list of strings."""
        if not self.microsoft_graph_scopes:
            return ["https://graph.microsoft.com/.default"]
        return [s.strip() for s in self.microsoft_graph_scopes.split(",") if s.strip()]

    def validate_azure_credentials(self) -> Tuple[bool, List[str]]:
        """Validate presence of all required Azure authentication settings.

        Returns:
            Tuple[bool, List[str]]: (is_valid, list_of_missing_keys)
        """
        missing = []
        if not self.azure_tenant_id or not self.azure_tenant_id.strip():
            missing.append("AZURE_TENANT_ID")
        if not self.azure_client_id or not self.azure_client_id.strip():
            missing.append("AZURE_CLIENT_ID")
        if not self.azure_client_secret or not self.azure_client_secret.get_secret_value().strip():
            missing.append("AZURE_CLIENT_SECRET")
        return (len(missing) == 0, missing)

    def get_masked_client_id(self) -> Optional[str]:
        """Return a safely masked client ID for diagnostics (e.g. 1234****abcd)."""
        if not self.azure_client_id:
            return None
        raw = self.azure_client_id.strip()
        if len(raw) <= 8:
            return "****"
        return f"{raw[:4]}****{raw[-4:]}"

    def get_masked_tenant_id(self) -> Optional[str]:
        """Return a safely masked tenant ID for diagnostics."""
        if not self.azure_tenant_id:
            return None
        raw = self.azure_tenant_id.strip()
        if len(raw) <= 8:
            return "****"
        return f"{raw[:4]}****{raw[-4:]}"

    def get_database_url(self) -> str:
        """Return database URL string, defaulting to local SQLite fallback if not explicitly configured."""
        if self.database_url and self.database_url.get_secret_value().strip():
            return self.database_url.get_secret_value().strip()
        return "sqlite:///./backend/app/data/tekmeet.db"


# Global settings singleton
settings = Settings()
