"""Summarization Service for Phase 4 LLM Summarization using Google Gemini API.

Transforms structured Transcript models into concise MeetingSummary objects containing:
  - Executive Overview
  - Key Discussion Points
  - Action Items (assignees & deadlines only if explicitly present)
  - Key Decisions (empty list if none)

Handles empty transcripts, missing API keys, long transcripts, API timeouts, and malformed responses.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import List, Optional

import httpx

from app.core.config import Settings, settings
from app.models.summary import ActionItem, Decision, MeetingSummary, SummarizationStatus
from app.models.transcript import Transcript, TranscriptionStatus

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 80000  # Safeguard boundary for context windows (~20k tokens)


class SummarizationServiceError(Exception):
    """Custom exception raised during summarization errors."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SummarizationService:
    """Production LLM Summarization Service using Google Gemini API."""

    def __init__(self, app_settings: Optional[Settings] = None):
        self.settings = app_settings or settings

    def _get_claude_key(self) -> Optional[str]:
        """Retrieve Claude API key safely from settings."""
        key = getattr(self.settings, "claude_api_key", None) or getattr(self.settings, "anthropic_api_key", None)
        if key and hasattr(key, "get_secret_value"):
            return key.get_secret_value()
        if isinstance(key, str):
            return key
        return None

    def _get_gemini_key(self) -> Optional[str]:
        """Retrieve Gemini API key safely from settings."""
        key = getattr(self.settings, "gemini_api_key", None)
        if key and hasattr(key, "get_secret_value"):
            return key.get_secret_value()
        if isinstance(key, str):
            return key
        return None

    def _get_api_key(self) -> Optional[str]:
        """Retrieve primary API key (Claude or Gemini)."""
        return self._get_claude_key() or self._get_gemini_key()

    async def summarize_transcript(
        self,
        transcript: Transcript,
        mock_provider_override: Optional[dict] = None,
    ) -> MeetingSummary:
        """Generate a structured MeetingSummary from a Transcript.

        Args:
            transcript: The input Transcript model.
            mock_provider_override: Optional dict mock payload for testing.

        Returns:
            MeetingSummary instance (COMPLETED, EMPTY, or FAILED).
        """
        summary_id = f"sum_{uuid.uuid4().hex[:12]}"
        logger.info(
            "[AI] Starting meeting summary\n"
            "[AI] Transcript ID: %s\n"
            "[AI] Call ID: %s",
            transcript.transcript_id,
            transcript.call_id or "N/A",
        )

        # Handle explicit mock override (used in unit tests)
        if mock_provider_override is not None:
            sm = self._build_summary_from_dict(
                summary_id=summary_id,
                event_id=transcript.event_id,
                transcript_id=transcript.transcript_id,
                data=mock_provider_override,
            )
            logger.info(
                "[AI] Meeting summary generated\n"
                "[AI] Summary ID: %s\n"
                "[AI] Summary saved",
                sm.summary_id,
            )
            return sm

        # Step 1: Check for empty / silent / unsupported transcript (TC #17)
        full_text_trimmed = (transcript.full_text or "").strip()
        if (
            transcript.status in (TranscriptionStatus.EMPTY, TranscriptionStatus.UNSUPPORTED)
            or not full_text_trimmed
            or full_text_trimmed == "No speech detected."
            or full_text_trimmed == "No speech detected (empty recording)."
        ):
            logger.info(
                "[AI] Meeting summary generated\n"
                "[AI] Summary ID: %s\n"
                "[AI] Summary saved",
                summary_id,
            )
            return MeetingSummary(
                summary_id=summary_id,
                event_id=transcript.event_id,
                transcript_id=transcript.transcript_id,
                status=SummarizationStatus.COMPLETED,
                overview="No meaningful meeting discussion was captured in the transcript.",
                key_points=[],
                action_items=[],
                decisions=[],
            )

        # Step 2: Handle failed transcripts
        if transcript.status == TranscriptionStatus.FAILED:
            err_msg = f"Cannot summarize failed transcript: {transcript.error_message or 'Unknown error'}"
            logger.error("[AI] ERROR: %s", err_msg)
            return MeetingSummary(
                summary_id=summary_id,
                event_id=transcript.event_id,
                transcript_id=transcript.transcript_id,
                status=SummarizationStatus.FAILED,
                error_message=err_msg,
            )

        # Step 3: Verify Claude API Key presence
        api_key = self._get_api_key()
        if not api_key:
            err_msg = "Claude/Gemini API Key is not configured in environment."
            logger.error("[AI] ERROR: %s", err_msg)
            return MeetingSummary(
                summary_id=summary_id,
                event_id=transcript.event_id,
                transcript_id=transcript.transcript_id,
                status=SummarizationStatus.FAILED,
                error_message=err_msg,
            )

        # Step 4: Handle long transcripts safely (Truncation / Safe Chunking)
        processed_text = full_text_trimmed
        if len(processed_text) > MAX_TRANSCRIPT_CHARS:
            processed_text = processed_text[:MAX_TRANSCRIPT_CHARS] + "\n\n[...Transcript truncated for length...]"

        # Step 5: Invoke Google Gemini LLM Summarization API
        try:
            gemini_key = self._get_gemini_key()

            if gemini_key:
                raw_response_text = await self._call_gemini_api(api_key=gemini_key, transcript_text=processed_text)
            else:
                err_msg = "Google Gemini API Key (GEMINI_API_KEY) is not configured."
                logger.error("[AI] ERROR: %s", err_msg)
                return MeetingSummary(
                    summary_id=summary_id,
                    event_id=transcript.event_id,
                    transcript_id=transcript.transcript_id,
                    status=SummarizationStatus.FAILED,
                    error_message=err_msg,
                )

            # Step 6: Parse structured JSON from response
            summary_dict = self._parse_json_response(raw_response_text)

            sm = self._build_summary_from_dict(
                summary_id=summary_id,
                event_id=transcript.event_id,
                transcript_id=transcript.transcript_id,
                data=summary_dict,
            )
            logger.info(
                "[AI] Meeting summary generated\n"
                "[AI] Summary ID: %s\n"
                "[AI] Summary saved",
                sm.summary_id,
            )
            return sm

        except Exception as exc:
            redacted_err = str(exc)
            claude_key = self._get_claude_key()
            gemini_key = self._get_gemini_key()
            if claude_key:
                redacted_err = redacted_err.replace(claude_key, "[REDACTED_API_KEY]")
            if gemini_key:
                redacted_err = redacted_err.replace(gemini_key, "[REDACTED_API_KEY]")
            logger.error("[AI] ERROR: Error calling LLM API - %s", redacted_err)
            return MeetingSummary(
                summary_id=summary_id,
                event_id=transcript.event_id,
                transcript_id=transcript.transcript_id,
                status=SummarizationStatus.FAILED,
                error_message=f"LLM API error: {redacted_err}",
            )

    def _get_claude_key(self) -> Optional[str]:
        """Retrieve configured Claude/Anthropic API key from settings."""
        key = getattr(self.settings, "claude_api_key", None) or getattr(self.settings, "anthropic_api_key", None)
        if key and hasattr(key, "get_secret_value"):
            val = key.get_secret_value()
            return val.strip() if val else None
        if isinstance(key, str) and key.strip():
            return key.strip()
        return None

    def _get_gemini_key(self) -> Optional[str]:
        """Retrieve configured Gemini API key from settings."""
        key = getattr(self.settings, "gemini_api_key", None)
        if key and hasattr(key, "get_secret_value"):
            val = key.get_secret_value()
            return val.strip() if val else None
        if isinstance(key, str) and key.strip():
            return key.strip()
        return None

    def _get_api_key(self) -> Optional[str]:
        """Retrieve configured Claude or Gemini API key from settings."""
        return self._get_claude_key() or self._get_gemini_key()

    async def _call_claude_api(self, api_key: str, transcript_text: str) -> str:
        """Send HTTP POST request to Anthropic Claude Messages REST API."""
        system_prompt = (
            "You are summarizing a Teams meeting. Use ONLY the provided meeting transcript. "
            "Do not use application logs, system events, meeting metadata, recording status, bot status, "
            "API responses, or any information outside the transcript. Do not invent missing information.\n\n"
            "STRICT FACTUALITY RULES:\n"
            "- Extract ONLY facts, key discussion points, decisions, and action items explicitly stated in the transcript.\n"
            "- Do NOT invent or infer information, assignees, deadlines, or decisions not present.\n"
            "- If assignee or deadline is not mentioned for an action item, set it to null.\n"
            "- If no decisions were explicitly made in the transcript, return an empty list for decisions.\n\n"
            "OUTPUT FORMAT:\n"
            "You MUST respond ONLY with a valid JSON object adhering to this exact JSON schema (no markdown wrap, no conversation text):\n"
            "{\n"
            '  "overview": "Concise executive summary paragraph (2-4 sentences based ONLY on transcript content)",\n'
            '  "key_points": ["Actual topic discussed 1", "Actual topic discussed 2"],\n'
            '  "action_items": [{"task": "Task description from transcript", "assignee": "Name or null", "deadline": "Date/Time or null"}],\n'
            '  "decisions": [{"title": "Decision title", "details": "Context or null"}]\n'
            "}"
        )

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.settings.claude_model,
            "max_tokens": 1500,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": f"MEETING TRANSCRIPT:\n{transcript_text}",
                }
            ],
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise SummarizationServiceError(
                    f"Claude API returned status HTTP {resp.status_code}: {resp.text.replace(api_key, '[REDACTED_API_KEY]')}",
                    status_code=resp.status_code,
                )

            res_data = resp.json()
            content_blocks = res_data.get("content", [])
            if not content_blocks or "text" not in content_blocks[0]:
                raise SummarizationServiceError("Claude API response missing text in content blocks.")

            return content_blocks[0]["text"]

    async def _call_gemini_api(self, api_key: str, transcript_text: str) -> str:
        """Send HTTP POST request to Google Gemini REST API with retry support for transient 503 spikes."""
        prompt = (
            "You are summarizing a Teams meeting. Use ONLY the provided meeting transcript. "
            "Do not use application logs, system events, meeting metadata, recording status, bot status, "
            "API responses, or any information outside the transcript. Do not invent missing information.\n\n"
            "STRICT FACTUALITY RULES:\n"
            "- Extract ONLY facts, key discussion points, decisions, and action items explicitly stated in the transcript.\n"
            "- Do NOT invent or infer information, assignees, deadlines, or decisions not present.\n"
            "- If assignee or deadline is not mentioned for an action item, set it to null.\n"
            "- If no decisions were explicitly made in the transcript, return an empty list for decisions.\n\n"
            "OUTPUT FORMAT:\n"
            "You MUST respond ONLY with a valid JSON object adhering to this exact JSON schema (no markdown wrap, no conversation text):\n"
            "{\n"
            '  "overview": "Concise executive summary paragraph (2-4 sentences based ONLY on transcript content)",\n'
            '  "key_points": ["Actual topic discussed 1", "Actual topic discussed 2"],\n'
            '  "action_items": [{"task": "Task description from transcript", "assignee": "Name or null", "deadline": "Date/Time or null"}],\n'
            '  "decisions": [{"title": "Decision title", "details": "Context or null"}]\n'
            "}\n\n"
            f"MEETING TRANSCRIPT:\n{transcript_text}"
        )

        models_to_try = [
            self.settings.gemini_model,
            "gemini-3.8-flash",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest",
            "gemini-pro-latest",
        ]
        # Deduplicate while preserving order
        unique_models = []
        for m in models_to_try:
            if m and m not in unique_models:
                unique_models.append(m)

        last_error = None
        for model in unique_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 1500,
                    "responseMimeType": "application/json",
                },
            }
            params = {"key": api_key}

            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                try:
                    async with httpx.AsyncClient(timeout=45.0) as client:
                        resp = await client.post(url, headers=headers, json=payload, params=params)
                        if resp.status_code == 200:
                            res_data = resp.json()
                            candidates = res_data.get("candidates", [])
                            if not candidates or "content" not in candidates[0]:
                                raise SummarizationServiceError("Gemini API response missing candidates content block.")

                            parts = candidates[0]["content"].get("parts", [])
                            if not parts or "text" not in parts[0]:
                                raise SummarizationServiceError("Gemini API response missing text in content parts.")

                            return parts[0]["text"]

                        elif resp.status_code in (503, 429) and attempt < max_attempts:
                            logger.warning(
                                "[SummarizationService] Gemini API model '%s' returned HTTP %d (attempt %d/%d). Retrying in 2s...",
                                model, resp.status_code, attempt, max_attempts
                            )
                            await asyncio.sleep(2.0)
                        else:
                            last_error = f"Gemini API model '{model}' returned status HTTP {resp.status_code}: {resp.text.replace(api_key, '[REDACTED_API_KEY]')}"
                            logger.warning("[SummarizationService] %s. Trying fallback model...", last_error)
                            break
                except Exception as exc:
                    last_error = f"Gemini model '{model}' request exception: {exc}"
                    logger.warning("[SummarizationService] %s", last_error)
                    break

        raise SummarizationServiceError(f"Gemini API call failed after trying models: {last_error}")

    def _parse_json_response(self, text: str) -> dict:
        """Parse JSON from raw Gemini response string, stripping markdown if present."""
        cleaned = text.strip()
        # Remove markdown code fence if present
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning("[SummarizationService] Direct JSON parse failed: %s. Attempting regex extraction.", exc)
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    pass
            raise SummarizationServiceError(f"Failed to parse valid JSON from Gemini response: {text[:200]}")

    def _build_summary_from_dict(
        self, summary_id: str, event_id: str, transcript_id: Optional[str], data: dict
    ) -> MeetingSummary:
        """Construct MeetingSummary model from dict data payload supporting alias key names."""
        # Key discussion points flexible resolution
        raw_kp = data.get("key_points") or data.get("topics_discussed") or data.get("key_discussion_points") or data.get("discussion_points") or []
        key_points = [str(kp) for kp in raw_kp if kp]

        # Action items flexible resolution
        raw_ai = data.get("action_items") or data.get("tasks") or []
        action_items = []
        for ai in raw_ai:
            if isinstance(ai, dict) and ai.get("task"):
                action_items.append(
                    ActionItem(
                        task=str(ai["task"]),
                        assignee=ai.get("assignee"),
                        deadline=ai.get("deadline"),
                    )
                )

        # Decisions flexible resolution
        raw_dec = data.get("decisions") or data.get("key_decisions") or []
        decisions = []
        for dec in raw_dec:
            if isinstance(dec, dict):
                title = dec.get("title") or dec.get("decision")
                if title:
                    decisions.append(
                        Decision(
                            title=str(title),
                            details=dec.get("details"),
                        )
                    )
            elif isinstance(dec, str) and dec.strip():
                decisions.append(Decision(title=dec.strip(), details=None))

        # Overview flexible resolution
        overview = data.get("overview") or data.get("executive_summary") or data.get("summary")
        if not overview or overview.strip() == "Meeting overview generated.":
            if key_points:
                overview = f"Meeting summary covering {len(key_points)} main discussion topics."
            else:
                overview = "No executive overview provided for this meeting."

        status_val = data.get("status", SummarizationStatus.COMPLETED)
        status_enum = SummarizationStatus(status_val) if isinstance(status_val, str) else status_val

        return MeetingSummary(
            summary_id=summary_id,
            event_id=event_id,
            transcript_id=transcript_id,
            status=status_enum,
            overview=overview,
            key_points=key_points,
            action_items=action_items,
            decisions=decisions,
            error_message=data.get("error_message"),
        )


# Global singleton instance
summarization_service = SummarizationService()
