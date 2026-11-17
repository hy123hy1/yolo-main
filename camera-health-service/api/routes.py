from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import logging
from pathlib import Path
import time
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from baseline.baseline_manager import BaselineManager
from config import settings
from person_count.entry_tracker import generate_region_line
from storage.repository import (
    CameraHealthRepository,
    effective_person_analysis_config,
)
from worker.channel_worker import WVPChannelSyncService
from worker.manager import WorkerManager
from worker.person_count_coordinator import PersonCountCoordinator


async def _person_count_retention_loop(repository: CameraHealthRepository) -> None:
    logger = logging.getLogger(__name__)
    while True:
        cutoff = datetime.now() - timedelta(days=settings.person_count_retention_days)
        try:
            person_count_paths = await asyncio.to_thread(
                repository.purge_person_count_records_before,
                cutoff,
            )
            health_event_paths = await asyncio.to_thread(
                repository.purge_health_events_before,
                cutoff,
            )
            person_entry_paths = await asyncio.to_thread(
                repository.purge_person_entry_events_before,
                cutoff,
            )
            person_behavior_paths = await asyncio.to_thread(
                repository.purge_person_behavior_events_before,
                cutoff,
            )
            for path in person_count_paths:
                _remove_retained_file(path, settings.person_count_snapshot_dir)
            for path in health_event_paths:
                _remove_retained_file(path, settings.snapshot_dir)
            for path in person_entry_paths:
                _remove_retained_file(path, settings.person_count_snapshot_dir)
            for path in person_behavior_paths:
                _remove_retained_file(path, settings.person_count_snapshot_dir)
            if (
                person_count_paths
                or health_event_paths
                or person_entry_paths
                or person_behavior_paths
            ):
                logger.info(
                    "camera health retention deleted person_count=%s health_events=%s "
                    "person_entry=%s person_behavior=%s",
                    len(person_count_paths),
                    len(health_event_paths),
                    len(person_entry_paths),
                    len(person_behavior_paths),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("person count retention cleanup failed")
        await asyncio.sleep(settings.person_count_cleanup_interval_seconds)


def _remove_retained_file(path: str, root: Path) -> None:
    candidate = Path(str(path)).resolve()
    base = Path(root).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        logging.getLogger(__name__).warning(
            "skip retained file outside configured root path=%s root=%s",
            candidate,
            base,
        )
        return
    candidate.unlink(missing_ok=True)


def _points_only_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("geometry config must be an object")
    raw_points = config.get("points")
    if not isinstance(raw_points, (list, tuple)):
        if not raw_points and not config:
            return {}
        raise ValueError("geometry config must contain points")
    points: list[list[float]] = []
    for point in raw_points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("geometry point must contain two numbers")
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("geometry point must contain two numbers") from exc
        if not all(0.0 <= value <= 1.0 for value in (x, y)):
            raise ValueError("geometry coordinates must be between 0 and 1")
        points.append([x, y])
    return {"points": points}


def _region_only_config(config: dict[str, Any]) -> dict[str, Any]:
    if not config:
        return {}
    normalized = _points_only_config(config)
    points = normalized.get("points") or []
    if len(points) != 4:
        raise ValueError("region_config must contain exactly four points")
    generate_region_line(points)
    return normalized


class PersonAnalysisConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    region_config: dict[str, Any] | None = None


class CameraCodeRequest(BaseModel):
    camera_code: str


class ChannelAlgorithmConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    analysis_interval: float | None = None
    config: dict[str, Any] | None = None


def create_app(
    *,
    repository: CameraHealthRepository,
    sync_service: WVPChannelSyncService,
    baseline_manager: BaselineManager | None = None,
    snapshot_dir: Path | str | None = None,
    person_count_snapshot_dir: Path | str | None = None,
    worker_manager: WorkerManager | None = None,
    person_count_coordinator: PersonCountCoordinator | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        retention_task = asyncio.create_task(
            _person_count_retention_loop(repository)
        )
        coordinator: PersonCountCoordinator | None = _app.state.person_count_coordinator
        if coordinator is not None:
            coordinator.start()
        manager: WorkerManager | None = _app.state.worker_manager
        if manager is not None:
            for channel in repository.list_channels():
                if str(channel.get("source_url") or "").strip() or str(
                    channel.get("flv") or ""
                ).strip():
                    try:
                        await asyncio.to_thread(manager.reconcile, channel)
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "channel startup recovery failed channel_id=%s",
                            channel.get("id"),
                        )
        try:
            yield
        finally:
            retention_task.cancel()
            await asyncio.gather(retention_task, return_exceptions=True)
            if manager is not None:
                manager.close()
            if coordinator is not None:
                coordinator.close()

    app = FastAPI(
        title="Camera Health Service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.sync_service = sync_service
    app.state.baseline_manager = baseline_manager or sync_service.baseline_manager
    app.state.snapshot_dir = Path(
        snapshot_dir or app.state.baseline_manager.snapshot_dir
    ).resolve()
    app.state.person_count_snapshot_dir = Path(
        person_count_snapshot_dir
        or getattr(
            app.state.baseline_manager,
            "person_count_snapshot_dir",
            "/app/data/camera_health/person_count",
        )
    ).resolve()
    app.state.worker_manager = worker_manager
    app.state.person_count_coordinator = person_count_coordinator

    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/person-analysis")
    async def person_analysis_page() -> FileResponse:
        page = Path(__file__).resolve().parent.parent / "static" / "person-analysis.html"
        return FileResponse(page, media_type="text/html; charset=utf-8")

    @router.get("/channels")
    async def list_channels() -> dict[str, Any]:
        items = repository.list_channels()
        return {"total": len(items), "items": items}

    @router.get("/channels/{channel_id}")
    async def get_channel(channel_id: str) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        return channel

    @router.get("/algorithms")
    async def list_algorithms() -> dict[str, Any]:
        items = repository.list_algorithm_definitions()
        return {"total": len(items), "items": items}

    @router.get("/channels/{channel_id}/algorithms")
    async def list_channel_algorithms(channel_id: str) -> dict[str, Any]:
        if not repository.get_channel(channel_id):
            raise HTTPException(status_code=404, detail="channel not found")
        items = repository.list_channel_algorithm_configs(channel_id)
        return {"channel_id": channel_id, "total": len(items), "items": items}

    @router.put("/channels/{channel_id}/algorithms/{algorithm_code}")
    async def update_channel_algorithm(
        channel_id: str,
        algorithm_code: str,
        body: ChannelAlgorithmConfigRequest,
    ) -> dict[str, Any]:
        try:
            config = repository.upsert_channel_algorithm_config(
                channel_id,
                algorithm_code,
                enabled=body.enabled,
                analysis_interval=body.analysis_interval,
                config=body.config,
            )
        except ValueError as exc:
            detail = str(exc)
            if detail in {"channel not found", "algorithm not found"}:
                raise HTTPException(status_code=404, detail=detail) from exc
            raise HTTPException(status_code=422, detail=detail) from exc
        channel = repository.get_channel(channel_id)
        manager: Any | None = app.state.worker_manager
        if manager is not None and channel is not None:
            manager.reconcile(channel)
        return config

    @router.get("/channels/{channel_id}/health")
    async def channel_health(channel_id: str) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        items = repository.list_health_states(channel_id)
        return {"channel_id": channel_id, "total": len(items), "items": items}

    @router.get("/channels/{channel_id}/health/events")
    async def channel_health_events(channel_id: str) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        items = repository.list_health_events(channel_id)
        return {"channel_id": channel_id, "total": len(items), "items": items}

    @router.get("/workers")
    async def workers() -> dict[str, Any]:
        manager: WorkerManager | None = app.state.worker_manager
        items = manager.list_status() if manager is not None else []
        return {"total": len(items), "items": items}

    @router.get("/channels/{channel_id}/worker")
    async def channel_worker(channel_id: str) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        manager: WorkerManager | None = app.state.worker_manager
        status = manager.get_status(channel_id) if manager is not None else None
        return {
            "channel_id": channel_id,
            "running": bool(status and status.get("running")),
            "status": status,
        }

    @router.put("/channels/{channel_id}/camera-code")
    async def set_camera_code(
        channel_id: str,
        body: CameraCodeRequest,
    ) -> dict[str, Any]:
        try:
            channel = repository.set_camera_code(channel_id, body.camera_code)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        return channel

    @router.put("/channels/{channel_id}/person-analysis/config")
    async def update_person_analysis_config(
        channel_id: str,
        body: PersonAnalysisConfigRequest,
    ) -> dict[str, Any]:
        try:
            region_config = (
                _region_only_config(body.region_config)
                if body.region_config is not None
                else {}
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        channel = repository.update_person_analysis_config(
            channel_id,
            enabled=body.enabled,
            mode="count_and_entry",
            direction="toward_first_edge",
            line_config={},
            region_config=region_config,
        )
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        manager: Any | None = app.state.worker_manager
        if manager is not None:
            manager.reconcile(channel)
        return _public_person_analysis_config(channel)

    @router.get("/channels/{channel_id}/person-analysis/config")
    async def person_analysis_config(channel_id: str) -> dict[str, Any]:
        config = repository.get_person_analysis_config(channel_id)
        if config is None:
            raise HTTPException(status_code=404, detail="channel not found")
        return _public_person_analysis_config(config)

    @router.get("/channels/{channel_id}/person-analysis/status")
    async def person_analysis_status(channel_id: str) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        config = effective_person_analysis_config(channel)
        manager: Any | None = app.state.worker_manager
        status = (
            manager.get_person_count_status(channel_id)
            if manager is not None and hasattr(manager, "get_person_count_status")
            else None
        )
        return {
            "channel_id": channel_id,
            "enabled": config["enabled"],
            "entry_configured": config["entry_configured"],
            "running": bool(status and status.get("running")),
            "current_count": status.get("current_count") if status else None,
            "last_count_inference_at": (
                status.get("last_count_inference_at") if status else None
            ),
            "last_entry_inference_at": (
                status.get("last_entry_inference_at") if status else None
            ),
            "last_entry_at": status.get("last_entry_at") if status else None,
            "last_error": status.get("last_error") if status else None,
            "consecutive_failures": (
                status.get("consecutive_failures", 0) if status else 0
            ),
        }

    @router.get("/channels/{channel_id}/person-entry/events")
    async def person_entry_events(
        channel_id: str,
        page: int = 1,
        page_size: int = 20,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        try:
            result = repository.list_person_entry_events(
                channel_id,
                page=page,
                page_size=page_size,
                start_time=start_time,
                end_time=end_time,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for item in result["items"]:
            item["snapshot_url"] = (
                f"/person-entry/events/{item['id']}/snapshot"
                if item.get("snapshot_path")
                else None
            )
        result["channel_id"] = channel_id
        return result

    @router.get("/channels/{channel_id}/person-entry/summary")
    async def person_entry_summary(
        channel_id: str,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        try:
            entry_count = repository.count_person_entry_events(
                channel_id,
                start_time=start_time,
                end_time=end_time,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "channel_id": channel_id,
            "entry_count": entry_count,
            "start_time": start_time,
            "end_time": end_time,
        }

    @router.get("/channels/{channel_id}/person-behavior/events")
    async def person_behavior_events(
        channel_id: str,
        page: int = 1,
        page_size: int = 20,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        try:
            result = repository.list_person_behavior_events(
                channel_id,
                page=page,
                page_size=page_size,
                start_time=start_time,
                end_time=end_time,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for item in result["items"]:
            item["snapshot_url"] = (
                f"/person-behavior/events/{item['id']}/snapshot"
                if item.get("snapshot_path")
                else None
            )
        result["channel_id"] = channel_id
        return result

    @router.get("/person-behavior/events/{event_id}/snapshot")
    async def person_behavior_snapshot(event_id: int) -> FileResponse:
        event = repository.get_person_behavior_event(event_id)
        if not event or not event.get("snapshot_path"):
            raise HTTPException(status_code=404, detail="snapshot not found")
        path = Path(str(event["snapshot_path"])).resolve()
        try:
            path.relative_to(app.state.person_count_snapshot_dir)
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail="person behavior snapshot path is outside snapshot directory",
            ) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="snapshot not found")
        return FileResponse(path, media_type="image/jpeg")

    @router.get("/channels/{channel_id}/person-count/status")
    async def person_count_status(channel_id: str) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        manager: Any | None = app.state.worker_manager
        status = (
            manager.get_person_count_status(channel_id)
            if manager is not None and hasattr(manager, "get_person_count_status")
            else None
        )
        return {
            "channel_id": channel_id,
            "enabled": bool(channel.get("person_count_enabled")),
            "running": bool(status and status.get("running")),
            "current_count": status.get("current_count") if status else None,
            "last_inference_at": (
                status.get(
                    "last_count_inference_at",
                    status.get("last_inference_at"),
                )
                if status
                else None
            ),
            "last_success_at": status.get("last_success_at") if status else None,
            "last_recorded_at": status.get("last_recorded_at") if status else None,
            "last_error": status.get("last_error") if status else None,
            "consecutive_failures": (
                status.get("consecutive_failures", 0) if status else 0
            ),
            "last_inference_ms": status.get("last_inference_ms") if status else None,
        }

    @router.get("/channels/{channel_id}/person-count/records")
    async def person_count_records(
        channel_id: str,
        page: int = 1,
        page_size: int = 20,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        try:
            result = repository.list_person_count_records(
                channel_id,
                page=page,
                page_size=page_size,
                start_time=start_time,
                end_time=end_time,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for item in result["items"]:
            if item.get("snapshot_path"):
                item["snapshot_url"] = (
                    f"/person-count/records/{item['id']}/snapshot"
                )
        return result

    @router.get("/person-count/records/{record_id}/snapshot")
    async def person_count_snapshot(record_id: int) -> FileResponse:
        record = repository.get_person_count_record(record_id)
        if not record or not record.get("snapshot_path"):
            raise HTTPException(status_code=404, detail="snapshot not found")
        path = Path(str(record["snapshot_path"])).resolve()
        try:
            path.relative_to(app.state.person_count_snapshot_dir)
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail="person count snapshot path is outside snapshot directory",
            ) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="snapshot not found")
        return FileResponse(path, media_type="image/jpeg")

    @router.get("/person-entry/events/{event_id}/snapshot")
    async def person_entry_snapshot(event_id: int) -> FileResponse:
        event = repository.get_person_entry_event(event_id)
        if not event or not event.get("snapshot_path"):
            raise HTTPException(status_code=404, detail="snapshot not found")
        path = Path(str(event["snapshot_path"])).resolve()
        try:
            path.relative_to(app.state.person_count_snapshot_dir)
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail="person entry snapshot path is outside snapshot directory",
            ) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="snapshot not found")
        return FileResponse(path, media_type="image/jpeg")

    @router.post("/channels/sync-wvp")
    async def sync_wvp_channels() -> dict[str, Any]:
        return await asyncio.to_thread(sync_service.sync_channels)

    @router.get("/channels/{channel_id}/snapshot/latest")
    async def latest_snapshot(channel_id: str) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        snapshot_path = channel.get("snapshot_path")
        return {
            "channel_id": channel_id,
            "snapshot_path": snapshot_path,
            "snapshot_url": (
                f"/channels/{channel_id}/snapshot/image" if snapshot_path else None
            ),
        }

    @router.get("/channels/{channel_id}/snapshot/image")
    async def snapshot_image(channel_id: str) -> FileResponse:
        channel = repository.get_channel(channel_id)
        if not channel or not channel.get("snapshot_path"):
            raise HTTPException(status_code=404, detail="snapshot not found")
        path = Path(str(channel["snapshot_path"])).resolve()
        try:
            path.relative_to(app.state.snapshot_dir)
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail="snapshot path is outside snapshot directory",
            ) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="snapshot not found")
        return FileResponse(path, media_type="image/jpeg")

    @router.post("/channels/{channel_id}/snapshot/reload")
    async def reload_snapshot(channel_id: str) -> dict[str, Any]:
        started_at = time.perf_counter()
        request_status = "failed"
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        try:
            result = await asyncio.to_thread(_reload_snapshot, app, channel_id)
            request_status = "succeeded"
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "baseline snapshot reload failed channel_id=%s",
                channel_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"baseline snapshot reload failed: {exc}",
            ) from exc
        finally:
            logging.getLogger("uvicorn.error").info(
                "baseline reload request completed channel_id=%s status=%s "
                "request_elapsed_ms=%.1f",
                channel_id,
                request_status,
                (time.perf_counter() - started_at) * 1000,
            )
        return {
            "channel_id": channel_id,
            "status": "succeeded",
            "snapshot_path": result["snapshot_path"],
            "snapshot_url": f"/channels/{channel_id}/snapshot/image",
        }

    app.include_router(router)
    return app


def _public_person_analysis_config(channel_or_config: dict[str, Any]) -> dict[str, Any]:
    config = (
        channel_or_config
        if "mode" in channel_or_config and "count_enabled" in channel_or_config
        else effective_person_analysis_config(channel_or_config)
    )
    region_config = config.get("region_config") or {}
    try:
        region_config = _points_only_config(region_config)
    except (TypeError, ValueError):
        region_config = {}
    generated_line: list[list[float]] = []
    if len(region_config.get("points") or []) == 4:
        try:
            generated_line = generate_region_line(region_config["points"])
        except (TypeError, ValueError):
            generated_line = []
    return {
        "channel_id": config.get("channel_id"),
        "enabled": bool(config.get("enabled")),
        "region_config": region_config,
        "entry_configured": bool(config.get("entry_configured")),
        "generated_line": generated_line,
    }


def _reload_snapshot(app: FastAPI, channel_id: str) -> dict[str, Any]:
    repository: CameraHealthRepository = app.state.repository
    manager: BaselineManager = app.state.baseline_manager
    channel = repository.get_channel(channel_id)
    if not channel:
        raise ValueError("channel not found")
    result: dict[str, Any] | None = None
    worker_manager = getattr(app.state, "worker_manager", None)
    fast_capture = (
        getattr(worker_manager, "capture_baseline", None)
        if worker_manager is not None
        else None
    )
    if fast_capture is not None:
        try:
            result = fast_capture(channel_id)
        except Exception as exc:
            logging.getLogger("uvicorn.error").warning(
                "baseline Worker buffer capture failed; falling back to stream "
                "channel_id=%s error=%s",
                channel_id,
                exc,
            )
    if result is None:
        result = manager.ensure(channel, force=True)
    updated = repository.update_snapshot_path(channel_id, result["snapshot_path"])
    if not updated:
        raise ValueError("channel not found")
    if worker_manager is not None:
        worker_manager.reload_baseline(channel_id, updated["snapshot_path"])
    return {
        "snapshot_path": updated["snapshot_path"],
    }
