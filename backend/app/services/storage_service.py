"""Storage Service for Phase 5 Disk-Backed Data Persistence.

Persists Transcripts, Meeting Summaries, and Delivery Records as JSON files under backend/app/data/.
Uses atomic file replacements (write to .tmp + os.replace) to prevent JSON corruption.
Handles missing/corrupted files gracefully, thread-safe, and survives application restarts.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

from app.db.database import SessionLocal
from app.db.repository import db_repository
from app.models.delivery import DeliveryRecord
from app.models.summary import MeetingSummary
from app.models.transcript import Transcript

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StorageService:
    """Production Thread-Safe Disk-Backed Storage Service using Atomic JSON replacements."""

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir:
            self.data_dir = Path(data_dir).resolve()
        else:
            self.data_dir = Path(__file__).resolve().parent.parent / "data"

        self.transcript_file = self.data_dir / "transcript_registry.json"
        self.summary_file = self.data_dir / "summary_registry.json"
        self.delivery_file = self.data_dir / "delivery_registry.json"

        self._lock = threading.Lock()
        self._ensure_data_dir_exists()

    def _ensure_data_dir_exists(self):
        """Create backend/app/data directory if it does not exist."""
        try:
            if not self.data_dir.exists():
                self.data_dir.mkdir(parents=True, exist_ok=True)
                logger.info("[StorageService] Created data directory at: %s", self.data_dir)
        except Exception as exc:
            logger.error("[StorageService] Error creating data directory '%s': %s", self.data_dir, exc)

    def _atomic_write_json(self, file_path: Path, data: dict) -> bool:
        """Write JSON atomically by writing to a .tmp file and using os.replace."""
        temp_path = file_path.with_suffix(f".tmp_{os.getpid()}")
        try:
            self._ensure_data_dir_exists()
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp_path, file_path)
            return True
        except Exception as exc:
            logger.error("[StorageService] Atomic JSON write failed for '%s': %s", file_path, exc)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            return False

    def _load_json_file(self, file_path: Path) -> dict:
        """Load JSON file safely. If file is missing or corrupted, returns empty dict without crashing."""
        if not file_path.exists():
            return {}

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("[StorageService] JSON file '%s' is malformed/corrupted: %s. Falling back to empty dict.", file_path, exc)
            return {}
        except Exception as exc:
            logger.error("[StorageService] Error reading JSON file '%s': %s. Falling back to empty dict.", file_path, exc)
            return {}

    # --- Transcript Persistence ---

    def save_transcript(self, transcript: Transcript) -> bool:
        """Save or update a Transcript record in transcript_registry.json and Database."""
        with self._lock:
            data = self._load_json_file(self.transcript_file)
            data[transcript.event_id] = transcript.model_dump(mode="json")
            res = self._atomic_write_json(self.transcript_file, data)

        try:
            with SessionLocal() as db:
                db_repository.save_recording_metadata(
                    db=db,
                    recording_id=transcript.transcript_id or f"rec_{transcript.event_id}",
                    meeting_id=transcript.event_id,
                    audio_storage_path=transcript.recording_file or "",
                    transcript_text=transcript.full_text,
                    duration_seconds=transcript.duration_seconds,
                )
        except Exception as exc:
            logger.debug("[StorageService] DB sync for transcript failed (JSON storage OK): %s", exc)

        return res

    def get_transcript(self, event_id: str) -> Optional[Transcript]:
        """Retrieve a Transcript record by event ID."""
        with self._lock:
            data = self._load_json_file(self.transcript_file)
            item = data.get(event_id)
            if not item:
                return None
            try:
                return Transcript.model_validate(item)
            except Exception as exc:
                logger.error("[StorageService] Failed to validate Transcript for event '%s': %s", event_id, exc)
                return None

    def get_all_transcripts(self) -> List[Transcript]:
        """Retrieve all stored Transcript records."""
        with self._lock:
            data = self._load_json_file(self.transcript_file)
            result = []
            for k, item in data.items():
                try:
                    result.append(Transcript.model_validate(item))
                except Exception:
                    pass
            return result

    # --- Meeting Summary Persistence ---

    def save_summary(self, summary: MeetingSummary) -> bool:
        """Save or update a MeetingSummary record in summary_registry.json and Database."""
        with self._lock:
            data = self._load_json_file(self.summary_file)
            data[summary.event_id] = summary.model_dump(mode="json")
            res = self._atomic_write_json(self.summary_file, data)

        try:
            with SessionLocal() as db:
                db_repository.save_meeting_summary(
                    db=db,
                    summary_id=summary.summary_id or f"sum_{summary.event_id}",
                    meeting_id=summary.event_id,
                    summary_text_json=summary.model_dump(mode="json"),
                )
        except Exception as exc:
            logger.debug("[StorageService] DB sync for summary failed (JSON storage OK): %s", exc)

        return res

    def get_summary(self, event_id: str) -> Optional[MeetingSummary]:
        """Retrieve a MeetingSummary record by event ID."""
        with self._lock:
            data = self._load_json_file(self.summary_file)
            item = data.get(event_id)
            if not item:
                return None
            try:
                return MeetingSummary.model_validate(item)
            except Exception as exc:
                logger.error("[StorageService] Failed to validate MeetingSummary for event '%s': %s", event_id, exc)
                return None

    def get_all_summaries(self) -> List[MeetingSummary]:
        """Retrieve all stored MeetingSummary records."""
        with self._lock:
            data = self._load_json_file(self.summary_file)
            result = []
            for k, item in data.items():
                try:
                    result.append(MeetingSummary.model_validate(item))
                except Exception:
                    pass
            return result

    # --- Delivery Record Persistence ---

    def save_delivery_record(self, record: DeliveryRecord) -> bool:
        """Save or update a DeliveryRecord in delivery_registry.json and Database."""
        with self._lock:
            data = self._load_json_file(self.delivery_file)
            data[record.event_id] = record.model_dump(mode="json")
            res = self._atomic_write_json(self.delivery_file, data)

        try:
            status_str = str(record.status).lower()
            if "sent" in status_str:
                with SessionLocal() as db:
                    db_repository.update_delivery_timestamp(
                        db=db,
                        meeting_id=record.event_id,
                        delivered_at=record.sent_at,
                    )
        except Exception as exc:
            logger.debug("[StorageService] DB sync for delivery record failed (JSON storage OK): %s", exc)

        return res

    def get_delivery_record(self, event_id: str) -> Optional[DeliveryRecord]:
        """Retrieve a DeliveryRecord by event ID."""
        with self._lock:
            data = self._load_json_file(self.delivery_file)
            item = data.get(event_id)
            if not item:
                return None
            try:
                return DeliveryRecord.model_validate(item)
            except Exception as exc:
                logger.error("[StorageService] Failed to validate DeliveryRecord for event '%s': %s", event_id, exc)
                return None

    def get_all_delivery_records(self) -> List[DeliveryRecord]:
        """Retrieve all stored DeliveryRecord objects."""
        with self._lock:
            data = self._load_json_file(self.delivery_file)
            result = []
            for k, item in data.items():
                try:
                    result.append(DeliveryRecord.model_validate(item))
                except Exception:
                    pass
            return result


# Global singleton instance
storage_service = StorageService()
