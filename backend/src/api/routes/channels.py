import asyncio
import logging
import time
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import traceback

from src.services.channel_service import ChannelService
from src.services.wvp_service import WVPService

router = APIRouter(prefix="/channels", tags=["channels"])
channel_service = ChannelService()
wvp_service = WVPService()
logger = logging.getLogger("uvicorn.error")


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


class StartZonePreviewRequest(BaseModel):
    task_id: Optional[str] = None
    owner_task_id: Optional[str] = None
    zone_source: Optional[str] = None
    zones: Optional[list[dict]] = None


@router.get("")
async def list_channels(
    device_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    key: Optional[str] = Query(None),
):
    return await channel_service.list_channels(device_id=device_id, type_filter=type, key=key)


@router.get("/available-for-task")
async def list_available_for_task_channels():
    return await channel_service.list_available_for_task_channels()


@router.post("/sync-wvp")
async def sync_wvp_channels(
    background_tasks: BackgroundTasks,
    page: int = Query(1, ge=1),
    count: int = Query(100, ge=1, le=500),
    query: Optional[str] = Query(None),
):
    try:
        result = await asyncio.to_thread(wvp_service.sync_channels, page, count, query)
        play_info_refresh_requests = list(
            result.pop("_play_info_refresh_requests", []) or []
        )
        for item in list(result.get("items") or []):
            sync_owner = item.pop("_sync_owner", "video_ai")
            channel_id = str(item.get("id") or "").strip()
            if not channel_id:
                continue
            if sync_owner == "electronic_seal":
                continue
            try:
                channel_service.schedule_channel_snapshot(channel_id)
            except Exception:
                logger.exception("[ChannelAutoSnapshot] schedule failed after WVP sync channel_id=%s", channel_id)
        if play_info_refresh_requests:
            background_tasks.add_task(
                wvp_service.refresh_play_info_requests,
                play_info_refresh_requests,
            )
        result["play_info_refresh"] = {
            "mode": "background",
            "queued": len(play_info_refresh_requests),
        }
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Failed to sync WVP channels: {str(e)}")


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
async def get_channel_play_url(channel_id: str, mode: str = "auto", compact: bool = False):
    result = await channel_service.play_urls(channel_id, mode=mode, compact=compact)
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
        result = await channel_service.request_snapshot_refresh(channel_id)
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


@router.post("/{channel_id}/zone-preview/start")
async def start_channel_zone_preview(channel_id: str, request: Optional[StartZonePreviewRequest] = None):
    started_at = time.perf_counter()
    has_explicit_request = bool(
        request and (request.zones or request.zone_source or request.owner_task_id or request.task_id)
    )
    mode = "explicit" if has_explicit_request else "task-auto"
    zone_count = len(request.zones) if request and request.zones else 0
    owner_task_id = (request.owner_task_id or request.task_id) if request else None
    try:
        if has_explicit_request:
            result = await channel_service.start_zone_preview(
                channel_id,
                zones=request.zones if request else None,
                zone_source=request.zone_source if request else None,
                owner_task_id=owner_task_id,
            )
        else:
            result = await channel_service.start_task_zone_preview(channel_id)
        if result is None:
            raise HTTPException(404, "Channel not found")
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.info(
            "[ZonePreviewAPI] start success channel_id=%s mode=%s owner_task_id=%s zone_source=%s zone_count=%s status=%s elapsed_ms=%s",
            channel_id,
            mode,
            owner_task_id,
            request.zone_source if request else None,
            zone_count,
            result.get("status"),
            elapsed_ms,
        )
        return result
    except HTTPException as exc:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.warning(
            "[ZonePreviewAPI] start failed channel_id=%s mode=%s owner_task_id=%s zone_source=%s zone_count=%s http_status=%s detail=%s elapsed_ms=%s",
            channel_id,
            mode,
            owner_task_id,
            request.zone_source if request else None,
            zone_count,
            exc.status_code,
            exc.detail,
            elapsed_ms,
        )
        raise
    except ValueError as e:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.warning(
            "[ZonePreviewAPI] start failed channel_id=%s mode=%s owner_task_id=%s zone_source=%s zone_count=%s error=%s elapsed_ms=%s",
            channel_id,
            mode,
            owner_task_id,
            request.zone_source if request else None,
            zone_count,
            str(e),
            elapsed_ms,
        )
        raise HTTPException(400, str(e))
    except Exception:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.exception(
            "[ZonePreviewAPI] start unexpected_error channel_id=%s mode=%s owner_task_id=%s zone_source=%s zone_count=%s elapsed_ms=%s",
            channel_id,
            mode,
            owner_task_id,
            request.zone_source if request else None,
            zone_count,
            elapsed_ms,
        )
        raise


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


@router.get("/{channel_id}/fence/browser-play-url")
async def get_channel_fence_browser_play_url(channel_id: str):
    result = await channel_service.get_zone_preview_browser_play_url(channel_id)
    if result is None:
        raise HTTPException(404, "Channel not found")
    return result


