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

    # --- Transcript Persistence ---

    def save_transcript(self, transcript: Transcript) -> bool:
        """Save or update a Transcript record in transcript_registry.json and Database."""
        with self._lock:
            data = self._load_json_file(self.transcript_file)
            data[transcript.transcript_id] = transcript.model_dump(mode="json")
            data[f"event_{transcript.event_id}"] = transcript.model_dump(mode="json")
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

    def get_transcript(self, event_id_or_transcript_id: str) -> Optional[Transcript]:
        """Retrieve a Transcript record by transcript ID or event ID."""
        with self._lock:
            data = self._load_json_file(self.transcript_file)
            item = data.get(event_id_or_transcript_id) or data.get(f"event_{event_id_or_transcript_id}")
            if not item:
                for k, v in data.items():
                    if isinstance(v, dict) and (v.get("transcript_id") == event_id_or_transcript_id or v.get("event_id") == event_id_or_transcript_id):
                        item = v
                        break
            if not item:
                return None
            try:
                return Transcript.model_validate(item)
            except Exception as exc:
                logger.error("[StorageService] Failed to validate Transcript for key '%s': %s", event_id_or_transcript_id, exc)
                return None

    def get_transcript_by_recording(self, recording_file: str, call_id: Optional[str] = None) -> Optional[Transcript]:
        """Retrieve a Transcript matching a specific recording filename or call ID."""
        target_name = os.path.basename(recording_file).lower()
        with self._lock:
            data = self._load_json_file(self.transcript_file)
            for item in data.values():
                if isinstance(item, dict):
                    rec_file = os.path.basename(item.get("recording_file", "")).lower()
                    item_call = item.get("call_id")
                    if rec_file and rec_file == target_name:
                        try:
                            return Transcript.model_validate(item)
                        except Exception:
                            pass
                    if call_id and item_call and item_call == call_id:
                        try:
                            return Transcript.model_validate(item)
                        except Exception:
                            pass
        return None

    def get_all_transcripts(self) -> List[Transcript]:
        """Retrieve all stored Transcript records."""
        with self._lock:
            data = self._load_json_file(self.transcript_file)
            result = []
            seen_ids = set()
            for k, item in data.items():
                if isinstance(item, dict):
                    t_id = item.get("transcript_id")
                    if t_id and t_id not in seen_ids:
                        try:
                            result.append(Transcript.model_validate(item))
                            seen_ids.add(t_id)
                        except Exception:
                            pass
            return result

    # --- Meeting Summary Persistence ---

    def save_summary(self, summary: MeetingSummary) -> bool:
        """Save or update a MeetingSummary record in summary_registry.json and Database."""
        with self._lock:
            data = self._load_json_file(self.summary_file)
            data[summary.summary_id] = summary.model_dump(mode="json")
            data[f"event_{summary.event_id}"] = summary.model_dump(mode="json")
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

    def get_summary(self, event_id_or_summary_id: str) -> Optional[MeetingSummary]:
        """Retrieve a MeetingSummary record by summary ID or event ID."""
        with self._lock:
            data = self._load_json_file(self.summary_file)
            item = data.get(event_id_or_summary_id) or data.get(f"event_{event_id_or_summary_id}")
            if not item:
                for k, v in data.items():
                    if isinstance(v, dict) and (v.get("summary_id") == event_id_or_summary_id or v.get("event_id") == event_id_or_summary_id):
                        item = v
                        break
            if not item:
                return None
            try:
                return MeetingSummary.model_validate(item)
            except Exception as exc:
                logger.error("[StorageService] Failed to validate MeetingSummary for key '%s': %s", event_id_or_summary_id, exc)
                return None

    def get_summary_by_transcript_id(self, transcript_id: str) -> Optional[MeetingSummary]:
        """Retrieve a MeetingSummary record matching a specific transcript_id."""
        with self._lock:
            data = self._load_json_file(self.summary_file)
            for item in data.values():
                if isinstance(item, dict) and item.get("transcript_id") == transcript_id:
                    try:
                        return MeetingSummary.model_validate(item)
                    except Exception:
                        pass
        return None

    def get_all_summaries(self) -> List[MeetingSummary]:
        """Retrieve all stored MeetingSummary records."""
        with self._lock:
            data = self._load_json_file(self.summary_file)
            result = []
            seen_ids = set()
            for k, item in data.items():
                if isinstance(item, dict):
                    s_id = item.get("summary_id")
                    if s_id and s_id not in seen_ids:
                        try:
                            result.append(MeetingSummary.model_validate(item))
                            seen_ids.add(s_id)
                        except Exception:
                            pass
            return result

    # --- Delivery Record Persistence ---

    def save_delivery_record(self, record: DeliveryRecord) -> bool:
        """Save or update a DeliveryRecord in delivery_registry.json and Database."""
        with self._lock:
            data = self._load_json_file(self.delivery_file)
            data[record.delivery_id] = record.model_dump(mode="json")
            data[f"event_{record.event_id}"] = record.model_dump(mode="json")
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

    def get_transcript_by_call_id(self, call_id: str) -> Optional[Transcript]:
        """Retrieve a Transcript matching a specific call ID."""
        if not call_id:
            return None
        with self._lock:
            data = self._load_json_file(self.transcript_file)
            for item in data.values():
                if isinstance(item, dict) and item.get("call_id") == call_id:
                    try:
                        return Transcript.model_validate(item)
                    except Exception:
                        pass
        return None

    def get_delivery_record(self, event_id_or_delivery_id: str) -> Optional[DeliveryRecord]:
        """Retrieve a DeliveryRecord by delivery ID or event ID."""
        with self._lock:
            data = self._load_json_file(self.delivery_file)
            item = data.get(event_id_or_delivery_id) or data.get(f"event_{event_id_or_delivery_id}")
            if not item:
                for k, v in data.items():
                    if isinstance(v, dict) and (v.get("delivery_id") == event_id_or_delivery_id or v.get("event_id") == event_id_or_delivery_id):
                        item = v
                        break
            if not item:
                return None
            try:
                return DeliveryRecord.model_validate(item)
            except Exception as exc:
                logger.error("[StorageService] Failed to validate DeliveryRecord for key '%s': %s", event_id_or_delivery_id, exc)
                return None

    def get_delivery_record_by_recording(self, recording_file: str) -> Optional[DeliveryRecord]:
        """Retrieve a DeliveryRecord matching a specific recording filename."""
        if not recording_file:
            return None
        target_name = os.path.basename(recording_file).lower()
        with self._lock:
            data = self._load_json_file(self.delivery_file)
            for item in data.values():
                if isinstance(item, dict):
                    raw_rf = item.get("recording_file") or ""
                    rec_file = os.path.basename(raw_rf).lower()
                    if rec_file and rec_file == target_name:
                        try:
                            return DeliveryRecord.model_validate(item)
                        except Exception:
                            pass
        return None

    def get_delivery_record_by_call_id(self, call_id: str) -> Optional[DeliveryRecord]:
        """Retrieve a DeliveryRecord matching a specific call ID."""
        if not call_id:
            return None
        with self._lock:
            data = self._load_json_file(self.delivery_file)
            for item in data.values():
                if isinstance(item, dict) and item.get("call_id") == call_id:
                    try:
                        return DeliveryRecord.model_validate(item)
                    except Exception:
                        pass
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
