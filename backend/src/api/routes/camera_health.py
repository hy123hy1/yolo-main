from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.core.settings import settings
from src.services.camera_health_defaults import CAMERA_OCCLUSION, CAMERA_VIEW_SHIFT
from src.services.camera_health_manager import camera_health_manager
from src.services.camera_health_store import CameraHealthStore


router = APIRouter(prefix="/camera-health", tags=["camera-health"])
camera_health_store = CameraHealthStore()
SUPPORTED_ALGORITHMS = {CAMERA_OCCLUSION, CAMERA_VIEW_SHIFT}


class CameraHealthConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    sample_interval_ms: Optional[int] = Field(default=None, ge=200)
    warmup_seconds: Optional[int] = Field(default=None, ge=0)
    alarm_hold_seconds: Optional[int] = Field(default=None, ge=0)
    recovery_hold_seconds: Optional[int] = Field(default=None, ge=1)
    alert_interval_seconds: Optional[int] = Field(default=None, ge=0)
    need_snapshot: Optional[bool] = None
    need_video_clip: Optional[bool] = None
    threshold_config_json: Optional[dict] = None


class CameraHealthEventRequest(BaseModel):
    handling_status: Optional[int] = None
    status: Optional[str] = None
    snapshot_path: Optional[str] = None
    video_path: Optional[str] = None


def _validate_algorithm(algorithm_type: str) -> None:
    if algorithm_type not in SUPPORTED_ALGORITHMS:
        raise HTTPException(400, f"Unsupported camera health algorithm: {algorithm_type}")


def _resolve_snapshot(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    root = settings.CAMERA_HEALTH_SNAPSHOT_DIR.resolve()
    candidate = Path(path_value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.exists() and candidate.is_file() else None


@router.get("/configs")
async def list_camera_health_configs(channel_id: Optional[str] = Query(None)):
    return {"items": camera_health_store.list_configs(channel_id)}


@router.put("/channels/{channel_id}/configs/{algorithm_type}")
async def update_camera_health_config(
    channel_id: str,
    algorithm_type: str,
    request: CameraHealthConfigRequest,
):
    _validate_algorithm(algorithm_type)
    payload = request.model_dump(exclude_none=True)
    if payload.get("recovery_hold_seconds", 1) < 1:
        raise HTTPException(400, "recovery_hold_seconds must be >= 1")
    try:
        return camera_health_manager.apply_config(channel_id, algorithm_type, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to update camera health config: {exc}") from exc


@router.post("/channels/{channel_id}/recalibrate")
async def recalibrate_camera_health(channel_id: str, algorithm_type: str = Query(...)):
    _validate_algorithm(algorithm_type)
    try:
        return camera_health_manager.recalibrate(channel_id, algorithm_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/channels/{channel_id}/status")
async def get_camera_health_status(channel_id: str):
    return camera_health_manager.get_status(channel_id)


@router.get("/events")
async def list_camera_health_events(
    channel_id: Optional[str] = Query(None),
    algorithm_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    if algorithm_type:
        _validate_algorithm(algorithm_type)
    return camera_health_store.list_events(channel_id, algorithm_type, page, page_size)


@router.get("/events/{event_id}")
async def get_camera_health_event(event_id: str):
    result = camera_health_store.get_event(event_id)
    if not result:
        raise HTTPException(404, "Camera health event not found")
    return result


@router.put("/events/{event_id}")
async def update_camera_health_event(event_id: str, request: CameraHealthEventRequest):
    result = camera_health_store.update_event(event_id, request.model_dump(exclude_none=True))
    if not result:
        raise HTTPException(404, "Camera health event not found")
    return result


@router.get("/events/{event_id}/snapshot")
async def get_camera_health_event_snapshot(event_id: str):
    event = camera_health_store.get_event(event_id)
    if not event:
        raise HTTPException(404, "Camera health event not found")
    path = _resolve_snapshot(event.get("snapshot_path"))
    if not path:
        raise HTTPException(404, "Camera health event snapshot not found")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)
