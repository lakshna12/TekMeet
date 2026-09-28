"""Summaries & Pipeline API router — Phase 4.

Endpoints to generate meeting summaries and trigger Phase 4 orchestration pipeline.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status

from app.models.summary import MeetingSummary, SummarizationStatus
from app.services.phase4_pipeline import Phase4PipelineResult, phase4_pipeline

router = APIRouter(prefix="/api/v1/summaries", tags=["Summaries & Pipeline (Phase 4)"])


@router.post(
    "/generate",
    response_model=MeetingSummary,
    summary="Generate meeting summary via Phase 4 Pipeline",
)
async def generate_summary(
    event_id: str = Query(..., description="Microsoft Graph event ID"),
    file_name: str = Query(..., description="Recording file name or path (e.g. meeting_xxx.wav)"),
    call_id: Optional[str] = Query(None, description="Optional Graph call ID"),
) -> MeetingSummary:
    """Generate or retrieve a MeetingSummary for a specified meeting recording using Phase 4 pipeline."""
    if not event_id.strip() or not file_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="event_id and file_name must be non-empty strings.",
        )

    pipeline_result = await phase4_pipeline.process_recording(
        event_id=event_id,
        file_path_or_name=file_name,
        call_id=call_id,
    )

    if pipeline_result.status == "failed" or not pipeline_result.summary:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=pipeline_result.error_message or "Pipeline failed to generate summary.",
        )

    return pipeline_result.summary


@router.get(
    "/{event_id}",
    response_model=MeetingSummary,
    summary="Get meeting summary by event ID",
)
def get_summary_by_event_id(event_id: str) -> MeetingSummary:
    """Retrieve an existing meeting summary from the pipeline registry by event ID."""
    result = phase4_pipeline.get_result(event_id)
    if not result or not result.summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No summary found for meeting event ID '{event_id}'.",
        )
    return result.summary


@router.post(
    "/pipeline/process",
    response_model=Phase4PipelineResult,
    summary="Trigger full Phase 4 pipeline (STT + LLM Summarization)",
)
async def process_phase4_pipeline(
    event_id: str = Query(..., description="Microsoft Graph event ID"),
    file_name: str = Query(..., description="Recording file name or path"),
    call_id: Optional[str] = Query(None, description="Optional Graph call ID"),
    force_refresh: bool = Query(False, description="Whether to bypass cached result and re-process"),
) -> Phase4PipelineResult:
    """Execute end-to-end Phase 4 Pipeline connecting audio -> STT transcript -> LLM summary."""
    if not event_id.strip() or not file_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="event_id and file_name must be non-empty strings.",
        )

    return await phase4_pipeline.process_recording(
        event_id=event_id,
        file_path_or_name=file_name,
        call_id=call_id,
        force_refresh=force_refresh,
    )
