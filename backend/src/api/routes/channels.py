from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import traceback

from src.services.channel_service import ChannelService

router = APIRouter(prefix="/channels", tags=["channels"])
channel_service = ChannelService()


class ChannelRequest(BaseModel):
    device_id: Optional[str] = None
    name: str
    type: str
    source_url: str
    app: Optional[str] = None
    stream: Optional[str] = None
    media_node_id: Optional[str] = None
    record_mode: Optional[str] = "none"


class UpdateChannelRequest(BaseModel):
    device_id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    source_url: Optional[str] = None
    app: Optional[str] = None
    stream: Optional[str] = None
    media_node_id: Optional[str] = None
    record_mode: Optional[str] = None


class ZoneRequest(BaseModel):
    name: str
    color: str = "#ff4d4f"
    labels: list[str] = []
    coordinates: list[list[float]]


class UpdateZoneRequest(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    labels: Optional[list[str]] = None
    coordinates: Optional[list[list[float]]] = None


class AIConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    model_id: Optional[str] = None
    labels: Optional[list[str]] = None
    conf: Optional[float] = None
    frame_interval: Optional[int] = None
    zone_ids: Optional[list[str]] = None
    save_snapshot: Optional[bool] = None
    models: Optional[list[dict]] = None


@router.get("")
async def list_channels(
    device_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    key: Optional[str] = Query(None),
):
    return await channel_service.list_channels(device_id=device_id, type_filter=type, key=key)


@router.post("")
async def create_channel(request: ChannelRequest):
    try:
        return await channel_service.create_channel(request)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Failed to create channel: {str(e)}")


@router.get("/{channel_id}")
async def get_channel(channel_id: str):
    result = await channel_service.get_channel(channel_id)
    if not result:
        raise HTTPException(404, "Channel not found")
    return result


@router.put("/{channel_id}")
async def update_channel(channel_id: str, request: UpdateChannelRequest):
    result = await channel_service.update_channel(channel_id, request)
    if not result:
        raise HTTPException(404, "Channel not found")
    return result


@router.delete("/{channel_id}")
async def delete_channel(channel_id: str):
    result = await channel_service.delete_channel(channel_id)
    if not result:
        raise HTTPException(404, "Channel not found")
    return result


@router.post("/{channel_id}/play")
async def play_channel(channel_id: str):
    try:
        result = await channel_service.play(channel_id)
        if not result:
            raise HTTPException(404, "Channel not found")
        return result
    except ValueError as e:
        raise HTTPException(502, str(e))


@router.post("/{channel_id}/stop")
async def stop_channel(channel_id: str):
    result = await channel_service.stop(channel_id)
    if not result:
        raise HTTPException(404, "Channel not found")
    return result


@router.get("/{channel_id}/play-url")
async def get_channel_play_url(channel_id: str):
    result = await channel_service.play_urls(channel_id)
    if not result:
        raise HTTPException(404, "Channel not found")
    return result


@router.get("/{channel_id}/inspect")
async def inspect_channel_stream(channel_id: str):
    try:
        result = await channel_service.inspect_stream(channel_id)
        if not result:
            raise HTTPException(404, "Channel not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{channel_id}/snapshot")
async def snapshot_channel(channel_id: str):
    try:
        result = await channel_service.snapshot(channel_id)
        if not result:
            raise HTTPException(404, "Channel not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{channel_id}/snapshot/latest")
async def get_latest_snapshot(channel_id: str):
    result = await channel_service.latest_snapshot(channel_id)
    if not result:
        raise HTTPException(404, "Channel not found")
    return result


@router.get("/{channel_id}/zones")
async def get_channel_zones(channel_id: str):
    result = await channel_service.get_zones(channel_id)
    if not result:
        raise HTTPException(404, "Channel not found")
    return result


@router.post("/{channel_id}/zones")
async def add_channel_zone(channel_id: str, request: ZoneRequest):
    result = await channel_service.add_zone(channel_id, request)
    if not result:
        raise HTTPException(404, "Channel not found")
    return result


@router.put("/{channel_id}/zones/{zone_id}")
async def update_channel_zone(channel_id: str, zone_id: str, request: UpdateZoneRequest):
    result = await channel_service.update_zone(channel_id, zone_id, request)
    if not result:
        raise HTTPException(404, "Zone or channel not found")
    return result


@router.delete("/{channel_id}/zones/{zone_id}")
async def delete_channel_zone(channel_id: str, zone_id: str):
    result = await channel_service.delete_zone(channel_id, zone_id)
    if not result:
        raise HTTPException(404, "Zone or channel not found")
    return result


@router.get("/{channel_id}/ai-config")
async def get_channel_ai_config(channel_id: str):
    result = await channel_service.get_ai_config(channel_id)
    if result is None:
        raise HTTPException(404, "Channel not found")
    return result


@router.put("/{channel_id}/ai-config")
async def update_channel_ai_config(channel_id: str, request: AIConfigRequest):
    result = await channel_service.update_ai_config(channel_id, request)
    if result is None:
        raise HTTPException(404, "Channel not found")
    return result


@router.post("/{channel_id}/ai/start")
async def start_channel_ai(channel_id: str):
    result = await channel_service.start_ai(channel_id)
    if result is None:
        raise HTTPException(404, "Channel not found")
    return result


@router.post("/{channel_id}/ai/stop")
async def stop_channel_ai(channel_id: str):
    result = await channel_service.stop_ai(channel_id)
    if result is None:
        raise HTTPException(404, "Channel not found")
    return result


@router.get("/{channel_id}/ai-status")
async def get_channel_ai_status(channel_id: str):
    result = await channel_service.get_ai_status(channel_id)
    if result is None:
        raise HTTPException(404, "Channel not found")
    return result


@router.post("/{channel_id}/zone-preview/start")
async def start_channel_zone_preview(channel_id: str):
    try:
        result = await channel_service.start_zone_preview(channel_id)
        if result is None:
            raise HTTPException(404, "Channel not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{channel_id}/zone-preview/stop")
async def stop_channel_zone_preview(channel_id: str):
    result = await channel_service.stop_zone_preview(channel_id)
    if result is None:
        raise HTTPException(404, "Channel not found")
    return result


@router.get("/{channel_id}/zone-preview/status")
async def get_channel_zone_preview_status(channel_id: str):
    result = await channel_service.get_zone_preview_status(channel_id)
    if result is None:
        raise HTTPException(404, "Channel not found")
    return result


@router.get("/{channel_id}/zone-preview/play-url")
async def get_channel_zone_preview_play_url(channel_id: str):
    result = await channel_service.get_zone_preview_play_urls(channel_id)
    if result is None:
        raise HTTPException(404, "Channel not found")
    return result


@router.post("/{channel_id}/ai/debug-once")
async def debug_channel_ai_once(channel_id: str):
    try:
        result = await channel_service.debug_ai_once(channel_id)
        if result is None:
            raise HTTPException(404, "Channel not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
