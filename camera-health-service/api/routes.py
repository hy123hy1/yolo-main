from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from baseline.baseline_manager import BaselineManager
from storage.repository import CameraHealthRepository
from worker.channel_worker import WVPChannelSyncService
from worker.manager import WorkerManager
from worker.person_count_coordinator import PersonCountCoordinator


class PersonCountConfigRequest(BaseModel):
    enabled: bool


class CameraCodeRequest(BaseModel):
    camera_code: str


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

    @router.put("/channels/{channel_id}/person-count/config")
    async def update_person_count_config(
        channel_id: str,
        body: PersonCountConfigRequest,
    ) -> dict[str, Any]:
        channel = repository.update_person_count_config(
            channel_id,
            enabled=body.enabled,
        )
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        manager: Any | None = app.state.worker_manager
        if manager is not None:
            manager.reconcile(channel)
        return channel

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
            "last_inference_at": status.get("last_inference_at") if status else None,
            "last_success_at": status.get("last_success_at") if status else None,
            "last_error": status.get("last_error") if status else None,
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
    async def person_count_snapshot(record_id: str) -> FileResponse:
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
    async def reload_snapshot(
        channel_id: str,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        channel = repository.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="channel not found")
        background_tasks.add_task(_reload_snapshot, app, channel_id)
        return {
            "channel_id": channel_id,
            "status": "queued",
        }

    app.include_router(router)
    return app


def _reload_snapshot(app: FastAPI, channel_id: str) -> None:
    repository: CameraHealthRepository = app.state.repository
    manager: BaselineManager = app.state.baseline_manager
    channel = repository.get_channel(channel_id)
    if not channel:
        return
    result = manager.ensure(channel, force=True)
    repository.update_snapshot_path(channel_id, result["snapshot_path"])
