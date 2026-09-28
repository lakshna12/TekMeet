"""Database Repository Service for TekMeet CRUD operations.

Provides database access for:
  - Scheduled meetings (create/update status)
  - Recording metadata & transcript text storage
  - Summary JSON & delivery tracking
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import MeetingRecordingDB, MeetingSummaryDB, ScheduledMeetingDB

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class DatabaseRepository:
    """Database repository layer for TekMeet entities."""

    # --- Scheduled Meetings ---

    def create_or_update_scheduled_meeting(
        self,
        db: Session,
        meeting_id: str,
        meeting_link_or_id: str,
        scheduled_time: datetime,
        organizer_email: str,
        status: str = "Scheduled",
    ) -> ScheduledMeetingDB:
        """Create or update a scheduled meeting record."""
        meeting = db.query(ScheduledMeetingDB).filter(ScheduledMeetingDB.id == meeting_id).first()
        if not meeting:
            meeting = ScheduledMeetingDB(
                id=meeting_id,
                meeting_link_or_id=meeting_link_or_id,
                scheduled_time=scheduled_time,
                organizer_email=organizer_email,
                status=status,
            )
            db.add(meeting)
        else:
            meeting.meeting_link_or_id = meeting_link_or_id
            meeting.scheduled_time = scheduled_time
            meeting.organizer_email = organizer_email
            meeting.status = status

        db.commit()
        db.refresh(meeting)
        return meeting

    def get_scheduled_meeting(self, db: Session, meeting_id: str) -> Optional[ScheduledMeetingDB]:
        """Retrieve scheduled meeting by ID."""
        return db.query(ScheduledMeetingDB).filter(ScheduledMeetingDB.id == meeting_id).first()

    def update_meeting_status(self, db: Session, meeting_id: str, status: str) -> Optional[ScheduledMeetingDB]:
        """Update meeting status (e.g. Scheduled, Joined, Completed, Failed)."""
        meeting = db.query(ScheduledMeetingDB).filter(ScheduledMeetingDB.id == meeting_id).first()
        if meeting:
            meeting.status = status
            db.commit()
            db.refresh(meeting)
        return meeting

    # --- Meeting Recordings & Transcripts ---

    def save_recording_metadata(
        self,
        db: Session,
        recording_id: str,
        meeting_id: str,
        audio_storage_path: str,
        transcript_text: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> MeetingRecordingDB:
        """Store or update recording metadata and transcript text."""
        # Ensure parent scheduled_meeting exists to satisfy FK constraint if auto-creating
        parent_meeting = db.query(ScheduledMeetingDB).filter(ScheduledMeetingDB.id == meeting_id).first()
        if not parent_meeting:
            parent_meeting = ScheduledMeetingDB(
                id=meeting_id,
                meeting_link_or_id=f"https://teams.microsoft.com/l/meetup-join/{meeting_id}",
                scheduled_time=utc_now(),
                organizer_email="organizer@tekmeet.local",
                status="Completed",
            )
            db.add(parent_meeting)
            db.flush()

        rec = db.query(MeetingRecordingDB).filter(MeetingRecordingDB.id == recording_id).first()
        if not rec:
            rec = MeetingRecordingDB(
                id=recording_id,
                meeting_id=meeting_id,
                audio_storage_path=audio_storage_path,
                transcript_text=transcript_text,
                duration_seconds=duration_seconds,
            )
            db.add(rec)
        else:
            rec.meeting_id = meeting_id
            rec.audio_storage_path = audio_storage_path
            if transcript_text is not None:
                rec.transcript_text = transcript_text
            if duration_seconds is not None:
                rec.duration_seconds = duration_seconds

        db.commit()
        db.refresh(rec)
        return rec

    def get_recording(self, db: Session, meeting_id: str) -> Optional[MeetingRecordingDB]:
        """Retrieve recording by meeting ID."""
        return db.query(MeetingRecordingDB).filter(MeetingRecordingDB.meeting_id == meeting_id).first()

    # --- Meeting Summaries & Delivery Tracking ---

    def save_meeting_summary(
        self,
        db: Session,
        summary_id: str,
        meeting_id: str,
        summary_text_json: Union[str, dict],
        delivered_at: Optional[datetime] = None,
    ) -> MeetingSummaryDB:
        """Store or update meeting summary JSON payload."""
        # Ensure parent scheduled_meeting exists
        parent_meeting = db.query(ScheduledMeetingDB).filter(ScheduledMeetingDB.id == meeting_id).first()
        if not parent_meeting:
            parent_meeting = ScheduledMeetingDB(
                id=meeting_id,
                meeting_link_or_id=f"https://teams.microsoft.com/l/meetup-join/{meeting_id}",
                scheduled_time=utc_now(),
                organizer_email="organizer@tekmeet.local",
                status="Completed",
            )
            db.add(parent_meeting)
            db.flush()

        json_str = summary_text_json if isinstance(summary_text_json, str) else json.dumps(summary_text_json)

        summary_rec = db.query(MeetingSummaryDB).filter(MeetingSummaryDB.id == summary_id).first()
        if not summary_rec:
            summary_rec = MeetingSummaryDB(
                id=summary_id,
                meeting_id=meeting_id,
                summary_text_json=json_str,
                delivered_at=delivered_at,
            )
            db.add(summary_rec)
        else:
            summary_rec.meeting_id = meeting_id
            summary_rec.summary_text_json = json_str
            if delivered_at is not None:
                summary_rec.delivered_at = delivered_at

        db.commit()
        db.refresh(summary_rec)
        return summary_rec

    def update_delivery_timestamp(
        self,
        db: Session,
        meeting_id: str,
        delivered_at: Optional[datetime] = None,
    ) -> Optional[MeetingSummaryDB]:
        """Update delivered_at timestamp for a meeting summary."""
        target_time = delivered_at or utc_now()
        summary_rec = db.query(MeetingSummaryDB).filter(MeetingSummaryDB.meeting_id == meeting_id).first()
        if summary_rec:
            summary_rec.delivered_at = target_time
            db.commit()
            db.refresh(summary_rec)
        return summary_rec

    def get_summary(self, db: Session, meeting_id: str) -> Optional[MeetingSummaryDB]:
        """Retrieve summary by meeting ID."""
        return db.query(MeetingSummaryDB).filter(MeetingSummaryDB.meeting_id == meeting_id).first()


# Global singleton instance
db_repository = DatabaseRepository()
