from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.services.recording_service import RecordingService

router = APIRouter(prefix="/recordings", tags=["recordings"])
recording_service = RecordingService()


@router.get("")
async def list_recordings(channel_id: Optional[str] = Query(None)):
    return await recording_service.list_recordings(channel_id=channel_id)


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
