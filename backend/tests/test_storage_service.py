"""Unit tests for StorageService disk-backed persistence.

Verifies:
  1. Save and retrieve Transcript.
  2. Save and retrieve MeetingSummary.
  3. Save and retrieve DeliveryRecord.
  4. Data survives creation of a new StorageService instance (app restart simulation).
  5. Missing registry files created automatically.
  6. Corrupted JSON handled safely without crashing.
  7. Multiple event IDs remain isolated.
  8. Updating one event does not overwrite another.
  9. Atomic write behavior (no partial file corruption).
 10. Secrets (e.g. passwords, tokens) are not persisted in storage data models.
"""

import json
import os
import tempfile
from pathlib import Path
import pytest

from app.models.delivery import DeliveryProvider, DeliveryRecord, DeliveryStatus
from app.models.summary import ActionItem, Decision, MeetingSummary, SummarizationStatus
from app.models.transcript import Transcript, TranscriptSegment, TranscriptionStatus
from app.services.storage_service import StorageService


@pytest.fixture
def temp_storage_dir():
    """Fixture providing a temporary directory for isolated storage tests."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


def test_1_save_and_retrieve_transcript(temp_storage_dir: Path):
    """Test 1: Save and retrieve a Transcript model."""
    storage = StorageService(data_dir=temp_storage_dir)

    transcript = Transcript(
        transcript_id="tr_save_101",
        event_id="event_save_101",
        call_id="call_save_101",
        recording_file="meeting_101.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text="Test speech text for transcript 101.",
        duration_seconds=45.0,
        segments=[TranscriptSegment(id=0, start=0.0, end=45.0, text="Test speech text for transcript 101.")],
    )

    saved_ok = storage.save_transcript(transcript)
    assert saved_ok is True

    retrieved = storage.get_transcript("event_save_101")
    assert retrieved is not None
    assert retrieved.transcript_id == "tr_save_101"
    assert retrieved.event_id == "event_save_101"
    assert retrieved.full_text == "Test speech text for transcript 101."
    assert len(retrieved.segments) == 1


def test_2_save_and_retrieve_summary(temp_storage_dir: Path):
    """Test 2: Save and retrieve a MeetingSummary model."""
    storage = StorageService(data_dir=temp_storage_dir)

    summary = MeetingSummary(
        summary_id="sum_save_202",
        event_id="event_save_202",
        transcript_id="tr_save_202",
        status=SummarizationStatus.COMPLETED,
        overview="Summary overview for meeting 202.",
        key_points=["Point 1", "Point 2"],
        action_items=[ActionItem(task="Task 1", assignee="Alice", deadline="Tomorrow")],
        decisions=[Decision(title="Decision 1", details="Context 1")],
    )

    saved_ok = storage.save_summary(summary)
    assert saved_ok is True

    retrieved = storage.get_summary("event_save_202")
    assert retrieved is not None
    assert retrieved.summary_id == "sum_save_202"
    assert retrieved.overview == "Summary overview for meeting 202."
    assert len(retrieved.key_points) == 2
    assert retrieved.action_items[0].assignee == "Alice"


def test_3_save_and_retrieve_delivery_record(temp_storage_dir: Path):
    """Test 3: Save and retrieve a DeliveryRecord model."""
    storage = StorageService(data_dir=temp_storage_dir)

    record = DeliveryRecord(
        delivery_id="del_save_303",
        event_id="event_save_303",
        recipient_email="organizer@domain.com",
        provider=DeliveryProvider.GRAPH,
        status=DeliveryStatus.SENT,
        retry_count=0,
    )

    saved_ok = storage.save_delivery_record(record)
    assert saved_ok is True

    retrieved = storage.get_delivery_record("event_save_303")
    assert retrieved is not None
    assert retrieved.delivery_id == "del_save_303"
    assert retrieved.recipient_email == "organizer@domain.com"
    assert retrieved.status == DeliveryStatus.SENT


def test_4_data_survives_instance_recreation(temp_storage_dir: Path):
    """Test 4: Verify data survives creation of a new StorageService instance (app restart)."""
    storage1 = StorageService(data_dir=temp_storage_dir)

    t1 = Transcript(
        transcript_id="tr_restart_404",
        event_id="event_restart_404",
        recording_file="meeting_404.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text="Persistent transcript text across restart.",
    )
    storage1.save_transcript(t1)

    s1 = MeetingSummary(
        summary_id="sum_restart_404",
        event_id="event_restart_404",
        status=SummarizationStatus.COMPLETED,
        overview="Persistent summary overview.",
    )
    storage1.save_summary(s1)

    # Re-instantiate storage service pointing to same directory
    storage2 = StorageService(data_dir=temp_storage_dir)

    t2 = storage2.get_transcript("event_restart_404")
    assert t2 is not None
    assert t2.full_text == "Persistent transcript text across restart."

    s2 = storage2.get_summary("event_restart_404")
    assert s2 is not None
    assert s2.overview == "Persistent summary overview."


def test_5_missing_registry_files_created(temp_storage_dir: Path):
    """Test 5: Missing JSON registry files are created automatically."""
    storage = StorageService(data_dir=temp_storage_dir)

    assert temp_storage_dir.exists()
    # Save a record to trigger file creation
    record = DeliveryRecord(
        delivery_id="del_505",
        event_id="event_505",
        recipient_email="user@test.com",
        status=DeliveryStatus.SENT,
    )
    storage.save_delivery_record(record)

    assert storage.delivery_file.exists()


def test_6_corrupted_json_handled_safely(temp_storage_dir: Path):
    """Test 6: Corrupted JSON file is handled safely without crashing."""
    storage = StorageService(data_dir=temp_storage_dir)

    # Intentionally corrupt the transcript_registry.json file
    storage.save_transcript(
        Transcript(
            transcript_id="tr_606",
            event_id="event_606",
            recording_file="test.wav",
            status=TranscriptionStatus.COMPLETED,
        )
    )
    assert storage.transcript_file.exists()

    with open(storage.transcript_file, "w", encoding="utf-8") as f:
        f.write("{ INVALID MALFORMED JSON content... ")

    # Querying corrupted file should return None, not raise exception
    res = storage.get_transcript("event_606")
    assert res is None

    # Saving new record should safely overwrite corrupted file
    saved_ok = storage.save_transcript(
        Transcript(
            transcript_id="tr_606_recovered",
            event_id="event_606",
            recording_file="test.wav",
            status=TranscriptionStatus.COMPLETED,
            full_text="Recovered transcript.",
        )
    )
    assert saved_ok is True
    res2 = storage.get_transcript("event_606")
    assert res2 is not None
    assert res2.full_text == "Recovered transcript."


def test_7_8_multiple_events_isolation_and_no_overwrite(temp_storage_dir: Path):
    """Tests 7 & 8: Multiple event IDs remain isolated and updating one does not overwrite another."""
    storage = StorageService(data_dir=temp_storage_dir)

    t_eventA = Transcript(
        transcript_id="tr_A",
        event_id="event_A",
        recording_file="a.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text="Transcript for Event A.",
    )
    t_eventB = Transcript(
        transcript_id="tr_B",
        event_id="event_B",
        recording_file="b.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text="Transcript for Event B.",
    )

    storage.save_transcript(t_eventA)
    storage.save_transcript(t_eventB)

    # Verify both exist independently
    resA = storage.get_transcript("event_A")
    resB = storage.get_transcript("event_B")
    assert resA.full_text == "Transcript for Event A."
    assert resB.full_text == "Transcript for Event B."

    # Update Event A only
    t_eventA_updated = Transcript(
        transcript_id="tr_A_v2",
        event_id="event_A",
        recording_file="a.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text="Updated Transcript for Event A.",
    )
    storage.save_transcript(t_eventA_updated)

    # Verify Event A updated while Event B remained untouched
    resA_v2 = storage.get_transcript("event_A")
    resB_untouched = storage.get_transcript("event_B")
    assert resA_v2.full_text == "Updated Transcript for Event A."
    assert resB_untouched.full_text == "Transcript for Event B."


def test_9_atomic_write_behavior(temp_storage_dir: Path):
    """Test 9: Atomic write behavior guarantees file exists after atomic save."""
    storage = StorageService(data_dir=temp_storage_dir)

    record = DeliveryRecord(
        delivery_id="del_909",
        event_id="event_909",
        recipient_email="test9@domain.com",
        status=DeliveryStatus.SENT,
    )

    saved_ok = storage.save_delivery_record(record)
    assert saved_ok is True
    assert storage.delivery_file.exists()

    # Ensure temporary file .tmp was cleaned up
    tmp_files = list(temp_storage_dir.glob("*.tmp*"))
    assert len(tmp_files) == 0


def test_10_no_secrets_persisted(temp_storage_dir: Path):
    """Test 10: Verify no sensitive credentials/tokens are written into JSON registries."""
    storage = StorageService(data_dir=temp_storage_dir)

    summary = MeetingSummary(
        summary_id="sum_1010",
        event_id="event_1010",
        status=SummarizationStatus.COMPLETED,
        overview="Overview text.",
    )
    storage.save_summary(summary)

    with open(storage.summary_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Verify sensitive keywords are not in file content
    assert "AZURE_CLIENT_SECRET" not in content
    assert "OPENAI_API_KEY" not in content
    assert "GEMINI_API_KEY" not in content
    assert "SMTP_PASSWORD" not in content
