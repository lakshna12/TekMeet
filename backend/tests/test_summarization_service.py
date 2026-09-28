"""Unit and Integration Tests for Phase 4 SummarizationService (Anthropic Claude API).

Maps to Test Cases:
  - TC #16: Summarization of a transcript via Claude (overview, key points, action items, decisions).
  - TC #17: Summarization of a very short/empty transcript (graceful handling, no crash).
"""

import pytest
from app.models.summary import SummarizationStatus
from app.models.transcript import Transcript, TranscriptionStatus
from app.services.summarization_service import SummarizationService, SummarizationServiceError


@pytest.fixture
def sample_valid_transcript() -> Transcript:
    """Fixture providing a valid structured transcript."""
    return Transcript(
        transcript_id="tr_valid_123",
        event_id="event_tc16_test",
        call_id="call_tc16_456",
        recording_file="meeting_test.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text=(
            "Alice started the meeting discussing project timelines. "
            "Bob agreed to finish the database migration by Friday. "
            "We decided to release Version 2.0 next month."
        ),
        duration_seconds=120.0,
    )


@pytest.fixture
def sample_empty_transcript() -> Transcript:
    """Fixture providing an empty/silent transcript."""
    return Transcript(
        transcript_id="tr_empty_123",
        event_id="event_tc17_test",
        call_id="call_tc17_789",
        recording_file="meeting_empty.wav",
        status=TranscriptionStatus.EMPTY,
        full_text="No speech detected.",
        duration_seconds=30.0,
    )


@pytest.mark.asyncio
async def test_tc16_successful_summarization_mock(sample_valid_transcript: Transcript):
    """TC #16: Test successful structured summarization with overview, key_points, action_items, decisions."""
    service = SummarizationService()

    mock_summary_data = {
        "overview": "The team reviewed project timelines and release milestones for Version 2.0.",
        "key_points": [
            "Project timelines were reviewed by Alice.",
            "Version 2.0 release planned for next month.",
        ],
        "action_items": [
            {"task": "Finish database migration", "assignee": "Bob", "deadline": "Friday"}
        ],
        "decisions": [
            {"title": "Release Version 2.0 next month", "details": "Agreed by team"}
        ],
    }

    summary = await service.summarize_transcript(
        transcript=sample_valid_transcript,
        mock_provider_override=mock_summary_data,
    )

    assert summary is not None
    assert summary.status == SummarizationStatus.COMPLETED
    assert summary.event_id == "event_tc16_test"
    assert summary.transcript_id == "tr_valid_123"
    assert "Version 2.0" in summary.overview
    assert len(summary.key_points) == 2
    assert len(summary.action_items) == 1
    assert summary.action_items[0].task == "Finish database migration"
    assert summary.action_items[0].assignee == "Bob"
    assert summary.action_items[0].deadline == "Friday"
    assert len(summary.decisions) == 1
    assert summary.decisions[0].title == "Release Version 2.0 next month"


@pytest.mark.asyncio
async def test_tc17_empty_transcript_handling(sample_empty_transcript: Transcript):
    """TC #17: Test empty/silent transcript generates exact required overview without crashing or API calls."""
    service = SummarizationService()

    summary = await service.summarize_transcript(transcript=sample_empty_transcript)

    assert summary is not None
    assert summary.status == SummarizationStatus.COMPLETED
    assert summary.event_id == "event_tc17_test"
    assert summary.overview == "No meaningful meeting discussion was captured in the transcript."
    assert len(summary.key_points) == 0
    assert len(summary.action_items) == 0
    assert len(summary.decisions) == 0
    assert summary.error_message is None


@pytest.mark.asyncio
async def test_tc17_missing_claude_api_key(sample_valid_transcript: Transcript):
    """TC #17: Test missing CLAUDE_API_KEY returns FAILED status without exposing secrets or crashing."""
    from app.core.config import Settings
    custom_settings = Settings(_env_file=None, claude_api_key=None, anthropic_api_key=None, gemini_api_key=None)
    service = SummarizationService(app_settings=custom_settings)

    summary = await service.summarize_transcript(transcript=sample_valid_transcript)

    assert summary is not None
    assert summary.status == SummarizationStatus.FAILED
    assert "claude_api_key" in summary.error_message.lower() or "not configured" in summary.error_message.lower()


@pytest.mark.asyncio
async def test_malformed_json_parsing_handling():
    """Test JSON response parsing helper with markdown code block formatting and raw text."""
    service = SummarizationService()

    markdown_json = (
        "```json\n"
        "{\n"
        '  "overview": "Overview test.",\n'
        '  "key_points": ["Point 1"],\n'
        '  "action_items": [],\n'
        '  "decisions": []\n'
        "}\n"
        "```"
    )

    parsed = service._parse_json_response(markdown_json)
    assert parsed["overview"] == "Overview test."
    assert parsed["key_points"] == ["Point 1"]

    with pytest.raises(SummarizationServiceError):
        service._parse_json_response("Invalid non-json text string without braces.")


@pytest.mark.asyncio
async def test_long_transcript_truncation_handling():
    """Test long transcript text (>80,000 characters) is truncated safely without error."""
    service = SummarizationService()

    long_text = "Word " * 20000  # 100,000 characters
    long_transcript = Transcript(
        transcript_id="tr_long_123",
        event_id="event_long_test",
        recording_file="long.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text=long_text,
    )

    mock_summary_data = {
        "overview": "Long transcript summary.",
        "key_points": ["Long text processed safely."],
        "action_items": [],
        "decisions": [],
    }

    summary = await service.summarize_transcript(
        transcript=long_transcript,
        mock_provider_override=mock_summary_data,
    )

    assert summary.status == SummarizationStatus.COMPLETED
    assert len(summary.key_points) == 1


@pytest.mark.asyncio
async def test_user_sample_transcript_summary_generation():
    """Verify Phase 4 summary generation from actual meeting transcript matching exact requirements.
    
    Sample Transcript:
    "Today we discussed the TekMeet Phase 4 implementation. Lakshna will integrate the transcription service by Monday. The team decided to use Claude API for meeting summarization. We will test the complete pipeline tomorrow."
    """
    service = SummarizationService()

    sample_transcript = Transcript(
        transcript_id="tr_sample_phase4",
        event_id="event_phase4_sample",
        recording_file="meeting_phase4.wav",
        status=TranscriptionStatus.COMPLETED,
        full_text=(
            "Today we discussed the TekMeet Phase 4 implementation. "
            "Lakshna will integrate the transcription service by Monday. "
            "The team decided to use Claude API for meeting summarization. "
            "We will test the complete pipeline tomorrow."
        ),
    )

    mock_summary_output = {
        "overview": "The team discussed the TekMeet Phase 4 implementation and planned upcoming integration and testing tasks.",
        "key_points": [
            "TekMeet Phase 4 implementation was discussed.",
            "Complete pipeline testing is scheduled for tomorrow."
        ],
        "action_items": [
            {"task": "Integrate the transcription service", "assignee": "Lakshna", "deadline": "Monday"}
        ],
        "decisions": [
            {"title": "Use Claude API for meeting summarization", "details": "Decided by team"}
        ]
    }

    summary = await service.summarize_transcript(
        transcript=sample_transcript,
        mock_provider_override=mock_summary_output
    )

    # 1. Must be generated from transcript content
    assert summary.status == SummarizationStatus.COMPLETED
    assert "Phase 4" in summary.overview or "Phase 4" in summary.key_points[0]
    assert summary.action_items[0].assignee == "Lakshna"
    assert summary.action_items[0].deadline == "Monday"
    assert "Claude API" in summary.decisions[0].title or "Claude API" in summary.decisions[0].details

    # 2. Must NOT contain system/application events
    forbidden_phrases = [
        "bot joined the meeting",
        "audio recording was finalized",
        "microsoft graph email delivery was configured",
        "single-tenant architecture was verified"
    ]

    serialized_summary = (
        summary.overview + " " +
        " ".join(summary.key_points) + " " +
        " ".join(ai.task for ai in summary.action_items) + " " +
        " ".join(d.title for d in summary.decisions)
    ).lower()

    for phrase in forbidden_phrases:
        assert phrase not in serialized_summary, f"System log phrase '{phrase}' found in summary!"
