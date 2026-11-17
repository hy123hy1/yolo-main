from typing import Optional
import math

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from src.services.analysis_service import analysis_service
from src.services.external_video_capture import VideoCaptureError
from src.services.recording_service import RecordingService

router = APIRouter(prefix="/recordings", tags=["recordings"])
recording_service = RecordingService()
external_video_capture = analysis_service.external_video_capture


class VideoCaptureRequest(BaseModel):
    channel_id: str = Field(min_length=1, max_length=64)
    timestamp: float = Field(description="Event Unix timestamp, in seconds or milliseconds")
    task_id: Optional[str] = Field(default=None, min_length=1, max_length=64)

    @field_validator("channel_id", "task_id")
    @classmethod
    def clean_identifier(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("identifier cannot be blank")
        return value

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("timestamp must be a number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("timestamp must be finite and positive")
        return value / 1000 if value >= 100_000_000_000 else value


@router.post("/capture", status_code=202)
async def capture_video(request: VideoCaptureRequest):
    try:
        return await external_video_capture.capture(
            request.channel_id, request.timestamp, request.task_id,
        )
    except VideoCaptureError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("")
async def list_recordings(
    channel_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    algorithm_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    return await recording_service.list_recordings(
        channel_id=channel_id,
        task_id=task_id,
        algorithm_id=algorithm_id,
        page=page,
        page_size=page_size,
    )


@router.get("/timeline")
async def get_recording_timeline(channel_id: Optional[str] = Query(None)):
    return await recording_service.timeline(channel_id=channel_id)


@router.get("/monthly")
async def get_recording_monthly(channel_id: Optional[str] = Query(None)):
    return await recording_service.monthly(channel_id=channel_id)


@router.get("/{recording_id}")
async def get_recording(recording_id: str):
    result = await recording_service.get_recording(recording_id)
    if not result:
        raise HTTPException(404, "Recording not found")
    return result
