"""Unit & Integration Tests for TekMeet Database Layer (SQLAlchemy ORM & DatabaseRepository).

Tests:
  - Scheduled meeting creation and status updates (Scheduled, Joined, Completed, Failed).
  - Recording metadata & STT transcript persistence linked to meeting via Foreign Key.
  - Meeting summary JSON persistence & delivered_at timestamp updates linked to meeting.
  - Foreign key cascade deletion enforcement.
  - Repository lookup queries.

Uses isolated SQLite in-memory database to avoid requiring real SQL Server credentials.
"""

import json
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import MeetingRecordingDB, MeetingSummaryDB, ScheduledMeetingDB
from app.db.repository import DatabaseRepository


@pytest.fixture
def db_session():
    """Fixture providing an isolated SQLite in-memory database session."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    # Enable foreign keys in SQLite
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON;")

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_create_and_update_scheduled_meeting(db_session):
    """Test creating and updating status of a scheduled meeting."""
    repo = DatabaseRepository()
    now = datetime.now(timezone.utc)

    # 1. Create meeting
    meeting = repo.create_or_update_scheduled_meeting(
        db=db_session,
        meeting_id="evt_test_db_101",
        meeting_link_or_id="https://teams.microsoft.com/l/meetup-join/101",
        scheduled_time=now,
        organizer_email="organizer@contoso.com",
        status="Scheduled",
    )

    assert meeting is not None
    assert meeting.id == "evt_test_db_101"
    assert meeting.organizer_email == "organizer@contoso.com"
    assert meeting.status == "Scheduled"

    # 2. Update status to Joined, Completed
    updated = repo.update_meeting_status(db=db_session, meeting_id="evt_test_db_101", status="Completed")
    assert updated.status == "Completed"

    # 3. Retrieve from DB
    fetched = repo.get_scheduled_meeting(db=db_session, meeting_id="evt_test_db_101")
    assert fetched is not None
    assert fetched.status == "Completed"


def test_save_recording_and_transcript_linked_to_meeting(db_session):
    """Test recording metadata and transcript text persistence linked to meeting via foreign key."""
    repo = DatabaseRepository()
    now = datetime.now(timezone.utc)

    # Create meeting first
    repo.create_or_update_scheduled_meeting(
        db=db_session,
        meeting_id="evt_rec_test_202",
        meeting_link_or_id="https://teams.microsoft.com/l/meetup-join/202",
        scheduled_time=now,
        organizer_email="alice@contoso.com",
        status="Joined",
    )

    # Save recording
    rec = repo.save_recording_metadata(
        db=db_session,
        recording_id="rec_file_202",
        meeting_id="evt_rec_test_202",
        audio_storage_path="recordings/evt_rec_test_202.wav",
        transcript_text="Alice discussed the quarterly roadmap with Bob.",
        duration_seconds=180.5,
    )

    assert rec is not None
    assert rec.id == "rec_file_202"
    assert rec.meeting_id == "evt_rec_test_202"
    assert "quarterly roadmap" in rec.transcript_text
    assert rec.duration_seconds == 180.5

    # Verify relationship
    fetched_rec = repo.get_recording(db=db_session, meeting_id="evt_rec_test_202")
    assert fetched_rec is not None
    assert fetched_rec.meeting.organizer_email == "alice@contoso.com"


def test_save_summary_and_update_delivered_at(db_session):
    """Test summary JSON persistence & delivered_at timestamp updates linked to meeting."""
    repo = DatabaseRepository()
    now = datetime.now(timezone.utc)

    repo.create_or_update_scheduled_meeting(
        db=db_session,
        meeting_id="evt_sum_test_303",
        meeting_link_or_id="https://teams.microsoft.com/l/meetup-join/303",
        scheduled_time=now,
        organizer_email="bob@contoso.com",
        status="Completed",
    )

    summary_payload = {
        "overview": "The team agreed on Version 2.0 release date.",
        "key_points": ["Version 2.0 readiness confirmed."],
        "action_items": [{"task": "Deploy backend", "assignee": "Bob", "deadline": "Friday"}],
        "decisions": [{"title": "Release on Friday", "details": "Agreed by team"}],
    }

    # Save summary
    sum_rec = repo.save_meeting_summary(
        db=db_session,
        summary_id="sum_rec_303",
        meeting_id="evt_sum_test_303",
        summary_text_json=summary_payload,
        delivered_at=None,
    )

    assert sum_rec is not None
    assert sum_rec.id == "sum_rec_303"
    assert sum_rec.meeting_id == "evt_sum_test_303"
    assert sum_rec.delivered_at is None
    parsed = json.loads(sum_rec.summary_text_json)
    assert parsed["overview"] == "The team agreed on Version 2.0 release date."

    # Update delivery timestamp
    delivered_time = datetime.now(timezone.utc)
    updated_sum = repo.update_delivery_timestamp(
        db=db_session,
        meeting_id="evt_sum_test_303",
        delivered_at=delivered_time,
    )

    assert updated_sum is not None
    assert updated_sum.delivered_at is not None

    fetched_sum = repo.get_summary(db=db_session, meeting_id="evt_sum_test_303")
    assert fetched_sum.delivered_at is not None
    assert fetched_sum.meeting.organizer_email == "bob@contoso.com"


def test_foreign_key_cascade_deletion(db_session):
    """Test that deleting a meeting cascades deletion to associated recordings and summaries."""
    repo = DatabaseRepository()
    now = datetime.now(timezone.utc)

    # 1. Create meeting with recording and summary
    meeting = repo.create_or_update_scheduled_meeting(
        db=db_session,
        meeting_id="evt_cascade_404",
        meeting_link_or_id="https://teams.microsoft.com/l/meetup-join/404",
        scheduled_time=now,
        organizer_email="charlie@contoso.com",
    )

    repo.save_recording_metadata(
        db=db_session,
        recording_id="rec_404",
        meeting_id="evt_cascade_404",
        audio_storage_path="path/to/audio.wav",
        transcript_text="Test transcript",
    )

    repo.save_meeting_summary(
        db=db_session,
        summary_id="sum_404",
        meeting_id="evt_cascade_404",
        summary_text_json={"overview": "Test overview"},
    )

    # Verify all records exist
    assert db_session.query(ScheduledMeetingDB).filter_by(id="evt_cascade_404").count() == 1
    assert db_session.query(MeetingRecordingDB).filter_by(meeting_id="evt_cascade_404").count() == 1
    assert db_session.query(MeetingSummaryDB).filter_by(meeting_id="evt_cascade_404").count() == 1

    # Delete parent meeting
    db_session.delete(meeting)
    db_session.commit()

    # Verify recordings and summaries were cascade-deleted
    assert db_session.query(ScheduledMeetingDB).filter_by(id="evt_cascade_404").count() == 0
    assert db_session.query(MeetingRecordingDB).filter_by(meeting_id="evt_cascade_404").count() == 0
    assert db_session.query(MeetingSummaryDB).filter_by(meeting_id="evt_cascade_404").count() == 0
