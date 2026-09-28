"""SQLAlchemy ORM Data Models for TekMeet Database Layer.

Tables:
  1. scheduled_meetings
  2. meeting_recordings
  3. meeting_summaries
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import relationship

from app.db.database import Base


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class ScheduledMeetingDB(Base):
    """ORM Model representing a scheduled meeting in TekMeet."""

    __tablename__ = "scheduled_meetings"

    id = Column(String(100), primary_key=True, index=True, comment="Meeting event ID or unique identifier")
    meeting_link_or_id = Column(String(500), nullable=False, comment="Teams meeting join link or meeting ID")
    scheduled_time = Column(DateTime(timezone=True), nullable=False, index=True, comment="Scheduled start time")
    organizer_email = Column(String(255), nullable=False, index=True, comment="Organizer email address")
    status = Column(String(50), nullable=False, default="Scheduled", index=True, comment="Status: Scheduled, Joined, Completed, Failed")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, comment="Record creation timestamp")

    # ORM Relationships
    recordings = relationship("MeetingRecordingDB", back_populates="meeting", cascade="all, delete-orphan")
    summaries = relationship("MeetingSummaryDB", back_populates="meeting", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_scheduled_meetings_status_time", "status", "scheduled_time"),
    )


class MeetingRecordingDB(Base):
    """ORM Model representing recording & transcript metadata for a meeting."""

    __tablename__ = "meeting_recordings"

    id = Column(String(100), primary_key=True, index=True, comment="Recording ID or transcript ID")
    meeting_id = Column(String(100), ForeignKey("scheduled_meetings.id", ondelete="CASCADE"), nullable=False, index=True, comment="Associated meeting ID")
    audio_storage_path = Column(String(500), nullable=False, comment="File path to saved MP4/WAV audio recording")
    transcript_text = Column(Text, nullable=True, comment="Full STT transcript text")
    duration_seconds = Column(Float, nullable=True, comment="Recording duration in seconds")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, comment="Record creation timestamp")

    # ORM Relationship
    meeting = relationship("ScheduledMeetingDB", back_populates="recordings")


class MeetingSummaryDB(Base):
    """ORM Model representing AI meeting summary and delivery tracking."""

    __tablename__ = "meeting_summaries"

    id = Column(String(100), primary_key=True, index=True, comment="Summary ID")
    meeting_id = Column(String(100), ForeignKey("scheduled_meetings.id", ondelete="CASCADE"), nullable=False, index=True, comment="Associated meeting ID")
    summary_text_json = Column(Text, nullable=False, comment="JSON string containing structured overview, key points, action items, decisions")
    delivered_at = Column(DateTime(timezone=True), nullable=True, index=True, comment="Timestamp when summary email was successfully delivered")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, comment="Record creation timestamp")

    # ORM Relationship
    meeting = relationship("ScheduledMeetingDB", back_populates="summaries")
