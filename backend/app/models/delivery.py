"""Delivery data models for TekMeet Phase 5 Email Notification Delivery."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class DeliveryStatus(str, Enum):
    """Lifecycle status of an email delivery attempt."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class DeliveryProvider(str, Enum):
    """Available email delivery provider types."""

    GRAPH = "graph"
    SMTP = "smtp"
    MOCK = "mock"


class EmailPayload(BaseModel):
    """Encapsulates the rendered content and recipient metadata for an email."""

    event_id: str = Field(..., description="Microsoft Graph event ID")
    recipient_email: str = Field(..., description="Target recipient email address")
    recipient_name: Optional[str] = Field(None, description="Recipient display name")
    subject: str = Field(..., description="Email subject line")
    body_html: str = Field(..., description="Rich HTML email content")
    body_text: str = Field(..., description="Plain text fallback email content")


class DeliveryRecord(BaseModel):
    """Structured record tracking the outcome and metadata of an email delivery attempt."""

    delivery_id: str = Field(..., description="Unique delivery attempt ID")
    event_id: str = Field(..., description="Associated meeting event ID")
    recipient_email: str = Field(..., description="Target recipient email address")
    provider: DeliveryProvider = Field(default=DeliveryProvider.GRAPH, description="Delivery provider used")
    status: DeliveryStatus = Field(default=DeliveryStatus.PENDING, description="Current status of the delivery")
    retry_count: int = Field(default=0, description="Count of retry attempts performed")
    sent_at: Optional[datetime] = Field(None, description="Timestamp when email was successfully sent")
    error_message: Optional[str] = Field(None, description="Detailed error message if delivery failed")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Record creation timestamp")
