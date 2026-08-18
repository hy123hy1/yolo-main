from __future__ import annotations

import asyncio
from pathlib import Path
from datetime import datetime
import math
from typing import Any, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.settings import settings
from src.electronic_seal.manager import electronic_seal_manager
from src.electronic_seal.settings import ElectronicSealSettings, SUPPORTED_ALGORITHMS


router = APIRouter(prefix="/electronic-seal", tags=["electronic-seal"])


class ElectronicSealConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    model_id: Optional[str] = Field(default=None, max_length=64)
    sample_interval_ms: Optional[int] = Field(default=None, ge=200)
    camera_health_interval_seconds: Optional[int] = Field(default=None, ge=1)
    alarm_hold_seconds: Optional[int] = Field(default=None, ge=0)
    recovery_hold_seconds: Optional[int] = Field(default=None, ge=0)
    need_snapshot: Optional[bool] = None
    need_video_clip: Optional[bool] = None
    line_config_json: Optional[dict] = None
    region_config_json: Optional[dict] = None
    threshold_config_json: Optional[dict] = None


class ElectronicSealProfileRequest(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    bound: Optional[bool] = None


class ElectronicSealChannelClaimRequest(BaseModel):
    channel_id: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=64)


class ElectronicSealChannelBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    streams: list[str] = Field(min_length=1, max_length=100)

    @field_validator("streams")
    @classmethod
    def normalize_streams(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            item = str(value or "").strip()
            if not item:
                raise ValueError("streams must not contain blank values")
            if len(item) > 255:
                raise ValueError("stream must contain at most 255 characters")
            normalized.append(item)
        return normalized

class ElectronicSealEventHandlingRequest(BaseModel):
    handling_status: Optional[int] = Field(default=None, ge=0)
    status: Optional[str] = Field(default=None, min_length=1, max_length=32)


def _validate_algorithm(algorithm_type: str) -> None:
    if algorithm_type not in SUPPORTED_ALGORITHMS:
        raise HTTPException(400, f"Unsupported electronic-seal algorithm: {algorithm_type}")


def _public_evidence_url(event_id: str, path_value: str) -> str:
    filename = Path(path_value).name
    return f"/electronic-seal/events/{quote(str(event_id), safe='')}/{quote(filename, safe='')}"


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    """Expose integration URLs without leaking worker filesystem paths."""
    result = dict(event)
    event_id = result.get("id")
    snapshot_path = result.get("evidence_snapshot_path")
    video_path = result.get("evidence_video_path")
    result.pop("snapshot_url", None)
    result.pop("video_url", None)
    if event_id and snapshot_path:
        result["snapshot_url"] = _public_evidence_url(event_id, snapshot_path)
    if event_id and video_path:
        result["video_url"] = _public_evidence_url(event_id, video_path)
    result.pop("evidence_snapshot_path", None)
    result.pop("evidence_video_path", None)
    if "metrics_json" in result and "metrics" not in result:
        result["metrics"] = result["metrics_json"]
    return result


def _redact_source_url(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        parsed = urlsplit(value)
        if not parsed.username and not parsed.password:
            return value
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = f":{parsed.port}" if parsed.port is not None else ""
        except ValueError:
            port = ""
        return urlunsplit((parsed.scheme, f"***:***@{host}{port}", parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return value


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    result = dict(status)
    result["source_url"] = _redact_source_url(result.get("source_url"))
    frame = result.get("frame")
    if isinstance(frame, dict):
        frame_result = dict(frame)
        for field in ("source_url", "primary_source_url", "fallback_source_url"):
            frame_result[field] = _redact_source_url(frame_result.get(field))
        result["frame"] = frame_result
    return result


def _public_overview(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    public_items = []
    for item in payload.get("items") or []:
        public_item = dict(item)
        stream = public_item.get("stream")
        if isinstance(stream, dict):
            public_item["stream"] = _public_status(stream)
        public_items.append(public_item)
    result["items"] = public_items
    return result


def _public_flv_url(value: Any) -> Any:
    if not isinstance(value, str) or not value or not settings.WVP_PUBLIC_FLV_BASE:
        return value
    try:
        source = urlsplit(value)
        public_base = urlsplit(settings.WVP_PUBLIC_FLV_BASE)
        if not public_base.scheme or not public_base.netloc:
            return value
        return urlunsplit(
            (
                public_base.scheme,
                public_base.netloc,
                source.path,
                source.query,
                source.fragment,
            )
        )
    except ValueError:
        return value


def _public_channel(channel: dict[str, Any]) -> dict[str, Any]:
    result = dict(channel)
    result["flv"] = _public_flv_url(result.get("flv"))
    return result


def _public_channels(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["items"] = [_public_channel(item) for item in payload.get("items") or []]
    return result


def _resolve_evidence_path(path_value: Any, evidence_root: Path, filename: str | None = None) -> Path:
    if not path_value:
        raise HTTPException(404, "Electronic-seal evidence not found")
    path = Path(str(path_value)).resolve()
    if filename is not None and path.name != filename:
        raise HTTPException(404, "Electronic-seal evidence file not found")
    try:
        path.relative_to(evidence_root.resolve())
    except ValueError as exc:
        raise HTTPException(404, "Electronic-seal evidence file not found") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Electronic-seal evidence file not found")
    return path


def _evidence_response(path: Path, *, kind: str | None = None) -> FileResponse:
    if kind == "snapshot" or path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        media_type = "image/jpeg"
    elif kind == "video" or path.suffix.lower() == ".mp4":
        media_type = "video/mp4"
    else:
        media_type = "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


def _public_events(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["items"] = [_public_event(item) for item in payload.get("items") or []]
    return result


def _validate_geometry_config(algorithm_type: str, payload: dict[str, Any]) -> None:
    field = "line_config_json" if algorithm_type == "truck_departure" else "region_config_json"
    if field not in payload or payload[field] in (None, {}):
        return
    config = payload[field]
    if not isinstance(config, dict):
        raise ValueError(f"{field} must be an object")
    coordinate_space = str(config.get("coordinate_space") or "normalized").lower()
    if coordinate_space not in {"normalized", "relative", "ratio", "pixel", "pixels"}:
        raise ValueError("coordinate_space must be normalized or pixel")
    points = config.get("points") or config.get("polygon") or config.get("line")
    if points is None and algorithm_type == "truck_departure":
        points = [config.get("line_start"), config.get("line_end")]
    if not isinstance(points, (list, tuple)):
        raise ValueError(f"{field} must contain points")
    if algorithm_type == "truck_departure" and len(points) != 2:
        raise ValueError(f"{field} requires exactly 2 points")
    if algorithm_type != "truck_departure" and len(points) < 3:
        raise ValueError(f"{field} requires at least 3 points")
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("geometry point must contain two numbers")
        try:
            values = [float(point[0]), float(point[1])]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("geometry point must contain two numbers") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError("geometry point must be finite")
        if coordinate_space in {"normalized", "relative", "ratio"} and not all(0.0 <= value <= 1.0 for value in values):
            raise ValueError("normalized geometry coordinates must be between 0 and 1")
    if algorithm_type == "truck_departure":
        direction = str(config.get("direction") or "inside_to_outside").lower()
        if direction not in {"inside_to_outside", "outbound", "departure", "left_to_right", "right_to_left", "top_to_bottom", "bottom_to_top"}:
            raise ValueError("unsupported truck departure direction")
        inside_side = str(config.get("inside_side") or "left").lower()
        if inside_side not in {"left", "right"}:
            raise ValueError("inside_side must be left or right")
    else:
        for key in ("hold_seconds", "max_frame_gap_seconds"):
            if key not in config:
                continue
            try:
                value = float(config[key])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{key} must be numeric") from exc
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be non-negative")


@router.get("/configs")
async def list_electronic_seal_configs(
    channel_id: Optional[str] = Query(None),
    include_common: bool = Query(False),
):
    return electronic_seal_manager.list_configs(
        channel_id,
        include_common=include_common,
    )


@router.get("/profiles")
async def list_electronic_seal_profiles(channel_id: Optional[str] = Query(None)):
    return electronic_seal_manager.list_profiles(channel_id)


@router.get("/channels")
async def list_electronic_seal_channels():
    return _public_channels(electronic_seal_manager.list_channels())


@router.post("/channels/claim")
async def claim_electronic_seal_channel(request: ElectronicSealChannelClaimRequest):
    try:
        return await asyncio.to_thread(
            electronic_seal_manager.claim_channel,
            request.channel_id,
            request.role,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to claim electronic-seal channel: {exc}") from exc


@router.post("/channels/bind")
async def bind_electronic_seal_channel(request: ElectronicSealChannelBindRequest):
    try:
        return electronic_seal_manager.bind_channels(request.streams)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to bind electronic-seal channel: {exc}") from exc


@router.delete("/channels/{channel_id}/binding")
async def release_electronic_seal_channel(channel_id: str):
    try:
        return electronic_seal_manager.release_channel(channel_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 409
        raise HTTPException(status_code, message) from exc
    except Exception as exc:
        raise HTTPException(
            500,
            f"Failed to release electronic-seal channel: {exc}",
        ) from exc


@router.get("/channels/{channel_id}")
async def get_electronic_seal_channel(channel_id: str):
    try:
        return _public_channel(electronic_seal_manager.get_channel(channel_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/channels/{channel_id}/play")
async def play_electronic_seal_channel(channel_id: str):
    try:
        return await electronic_seal_manager.play_channel(channel_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 502
        raise HTTPException(status_code, message) from exc


@router.post("/channels/{channel_id}/snapshot")
async def capture_electronic_seal_snapshot(channel_id: str):
    try:
        return await asyncio.to_thread(
            electronic_seal_manager.capture_snapshot,
            channel_id,
        )
    except ValueError as exc:
        message = str(exc)
        if "channel not found" in message.lower():
            status_code = 404
        elif "source is not configured" in message.lower():
            status_code = 400
        else:
            status_code = 502
        raise HTTPException(status_code, message) from exc


@router.get("/channels/{channel_id}/snapshot/latest")
async def get_latest_electronic_seal_snapshot(channel_id: str):
    try:
        return electronic_seal_manager.latest_snapshot(channel_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 500
        raise HTTPException(status_code, message) from exc


@router.get("/channels/{channel_id}/snapshot/image")
async def get_electronic_seal_snapshot_image(channel_id: str):
    try:
        path = electronic_seal_manager.resolve_snapshot_image(channel_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="image/jpeg")


@router.get("/models")
async def list_electronic_seal_models():
    return electronic_seal_manager.list_models()


@router.get("/models/{model_id}")
async def get_electronic_seal_model(model_id: str):
    try:
        return electronic_seal_manager.get_model(model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/channels/{channel_id}/profile")
async def update_electronic_seal_profile(
    channel_id: str,
    request: ElectronicSealProfileRequest,
):
    try:
        return await asyncio.to_thread(
            electronic_seal_manager.apply_profile,
            channel_id,
            request.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to update electronic-seal profile: {exc}") from exc


@router.get("/overview")
async def get_electronic_seal_overview():
    return _public_overview(electronic_seal_manager.overview())


@router.post("/online-status/sync")
async def sync_electronic_seal_online_status():
    return await asyncio.to_thread(electronic_seal_manager.sample_online_status)


@router.get("/online-rates")
async def get_electronic_seal_online_rates(
    period: str = Query("day", pattern="^(day|week)$"),
    channel_id: Optional[str] = Query(None),
):
    try:
        return await asyncio.to_thread(
            electronic_seal_manager.online_rates,
            period,
            channel_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/channels/{channel_id}/configs/{algorithm_type}")
async def update_electronic_seal_config(
    channel_id: str,
    algorithm_type: str,
    request: ElectronicSealConfigRequest,
):
    _validate_algorithm(algorithm_type)
    try:
        payload = request.model_dump(exclude_none=True)
        _validate_geometry_config(algorithm_type, payload)
        model_id = payload.get("model_id")
        if model_id:
            try:
                electronic_seal_manager.get_model(model_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        return electronic_seal_manager.apply_config(
            channel_id,
            algorithm_type,
            payload,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to update electronic-seal config: {exc}") from exc


@router.post("/channels/{channel_id}/recalibrate")
async def recalibrate_electronic_seal(channel_id: str, algorithm_type: str = Query(...)):
    _validate_algorithm(algorithm_type)
    try:
        return electronic_seal_manager.recalibrate(channel_id, algorithm_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/channels/{channel_id}/status")
async def get_electronic_seal_status(channel_id: str):
    return _public_status(electronic_seal_manager.get_status(channel_id))


@router.get("/events")
async def list_electronic_seal_events(
    channel_id: Optional[str] = Query(None),
    algorithm_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    cursor: Optional[str] = Query(None),
):
    if algorithm_type:
        _validate_algorithm(algorithm_type)
    if start_time is not None or end_time is not None or cursor:
        try:
            result = electronic_seal_manager.list_events(
                channel_id,
                algorithm_type,
                page,
                page_size,
                start_time=start_time,
                end_time=end_time,
                cursor=cursor,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        result = electronic_seal_manager.list_events(channel_id, algorithm_type, page, page_size)
    return _public_events(result)


@router.get("/events/{event_id}")
async def get_electronic_seal_event(event_id: str):
    result = electronic_seal_manager.get_event(event_id)
    if not result:
        raise HTTPException(404, "Electronic-seal event not found")
    return _public_event(result)


@router.put("/events/{event_id}/handling")
async def update_electronic_seal_event_handling(
    event_id: str,
    request: ElectronicSealEventHandlingRequest,
):
    payload = request.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(400, "handling_status or status is required")
    result = electronic_seal_manager.update_event(event_id, payload)
    if not result:
        raise HTTPException(404, "Electronic-seal event not found")
    return _public_event(result)


@router.get("/events/{event_id}/evidence")
async def get_electronic_seal_evidence(event_id: str, kind: str = Query("snapshot")):
    if kind not in {"snapshot", "video"}:
        raise HTTPException(400, "kind must be snapshot or video")
    event = electronic_seal_manager.get_event(event_id)
    if not event:
        raise HTTPException(404, "Electronic-seal event not found")
    path_value = (
        event.get("evidence_snapshot_path")
        if kind == "snapshot"
        else event.get("evidence_video_path")
    )
    path = _resolve_evidence_path(path_value, ElectronicSealSettings().evidence_dir)
    return _evidence_response(path, kind=kind)


@router.get("/events/{event_id}/{filename}")
async def get_electronic_seal_evidence_by_filename(event_id: str, filename: str):
    if Path(filename).name != filename:
        raise HTTPException(404, "Electronic-seal evidence file not found")
    event = electronic_seal_manager.get_event(event_id)
    if not event:
        raise HTTPException(404, "Electronic-seal event not found")
    paths = (
        event.get("evidence_snapshot_path"),
        event.get("evidence_video_path"),
    )
    path_value = next(
        (candidate for candidate in paths if candidate and Path(str(candidate)).name == filename),
        None,
    )
    path = _resolve_evidence_path(path_value, ElectronicSealSettings().evidence_dir, filename)
    return _evidence_response(path)


@router.post("/uploads/{job_id}/retry")
async def retry_electronic_seal_upload(job_id: str):
    try:
        return electronic_seal_manager.retry_upload(job_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/channels/{channel_id}/counts/truck_departure")
async def get_electronic_seal_truck_count(channel_id: str):
    result = electronic_seal_manager.get_truck_count(channel_id)
    if result is None:
        raise HTTPException(404, "Electronic-seal channel not found")
    return result


@router.get("/channels/{channel_id}/counts/truck_departure/history")
async def list_electronic_seal_truck_departures(
    channel_id: str,
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
):
    result = electronic_seal_manager.list_truck_departures(
        channel_id,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )
    if result is None:
        raise HTTPException(404, "Electronic-seal channel not found")
    return result


__all__ = ["router"]
