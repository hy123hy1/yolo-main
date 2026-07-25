import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from src.services.realtime_detection_service import realtime_detection_hub
from src.services.task_service import ElectronicSealChannelOwnedError, TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])
task_service = TaskService()


class RegionRequest(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = "区域"
    type: Optional[str] = "polygon"
    color: Optional[str] = "#3b82f6"
    coordinates: list[list[float]] = Field(default_factory=list)
    enabled: bool = True


class AlgorithmRequest(BaseModel):
    id: Optional[str] = None
    algorithm_type: Optional[str] = "detection"
    scene_key: Optional[str] = None
    scene_name: Optional[str] = None
    rule_type: Optional[str] = None
    model_id: str
    enabled: bool = True
    labels: list[str] = Field(default_factory=list)
    detect_all_classes: Optional[bool] = None
    label_source: Optional[str] = None
    confidence: Optional[float] = None
    alert_interval: Optional[int] = None
    need_snapshot: bool = True
    need_video_clip: bool = True
    video_before_seconds: int = 10
    video_after_seconds: int = 10
    need_overlay: bool = False
    requires_region: Optional[bool] = None
    overlay_color: str = "#ff4d4f"
    postprocess_config: dict = Field(default_factory=dict)
    region_ids: list[str] = Field(default_factory=list)
    regions: list[RegionRequest] = Field(default_factory=list)


class TaskCreateRequest(BaseModel):
    name: str
    source_url: Optional[str] = None
    source_type: Optional[str] = None
    channel_id: Optional[str] = None
    frame_interval: int = 10
    algorithms: list[AlgorithmRequest] = Field(default_factory=list)
    regions: list[RegionRequest] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    name: Optional[str] = None
    source_url: Optional[str] = None
    source_type: Optional[str] = None
    channel_id: Optional[str] = None
    frame_interval: Optional[int] = None
    status: Optional[str] = None
    algorithms: Optional[list[AlgorithmRequest]] = None
    regions: Optional[list[RegionRequest]] = None


class RegionBatchRequest(BaseModel):
    regions: list[RegionRequest] = Field(default_factory=list)


@router.get("")
async def list_tasks():
    return await task_service.list_tasks()


@router.post("/metadata/resync-all")
async def resync_all_task_metadata():
    return await task_service.resync_all_task_metadata()


@router.post("")
async def create_task(request: TaskCreateRequest):
    try:
        return await task_service.create_task(request.model_dump())
    except ElectronicSealChannelOwnedError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{task_id}")
async def get_task(task_id: str):
    result = await task_service.get_task(task_id)
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.get("/{task_id}/algorithms")
async def list_task_algorithms(task_id: str):
    result = await task_service.list_task_algorithms(task_id)
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.get("/{task_id}/algorithms/{algorithm_id}/regions")
async def list_algorithm_regions(task_id: str, algorithm_id: str):
    result = await task_service.list_algorithm_regions(task_id, algorithm_id)
    if not result:
        raise HTTPException(404, "Task or algorithm not found")
    return result


@router.get("/{task_id}/available-models")
async def list_task_available_models(task_id: str):
    result = await task_service.list_task_available_models(task_id)
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.put("/{task_id}")
async def update_task(task_id: str, request: TaskUpdateRequest):
    try:
        result = await task_service.update_task(task_id, request.model_dump(exclude_none=True))
    except ElectronicSealChannelOwnedError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    result = await task_service.delete_task(task_id)
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.post("/{task_id}/start")
async def start_task(task_id: str):
    try:
        result = await task_service.start_task(task_id)
    except ElectronicSealChannelOwnedError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.post("/{task_id}/stop")
async def stop_task(task_id: str):
    result = await task_service.stop_task(task_id)
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.get("/{task_id}/status")
async def get_task_status(task_id: str):
    result = await task_service.get_task_status(task_id)
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.websocket("/{task_id}/detections/ws")
async def task_detection_websocket(task_id: str, websocket: WebSocket):
    await websocket.accept()
    subscription = realtime_detection_hub.subscribe(task_id)
    disconnect_task = asyncio.create_task(websocket.receive())
    try:
        await websocket.send_json({"type": "ready", "task_id": task_id})
        while True:
            queue_task = asyncio.create_task(subscription.queue.get())
            done, _ = await asyncio.wait(
                (queue_task, disconnect_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                queue_task.cancel()
                await asyncio.gather(queue_task, return_exceptions=True)
                message = disconnect_task.result()
                if message.get("type") == "websocket.disconnect":
                    break
                disconnect_task = asyncio.create_task(websocket.receive())
                continue

            payload = queue_task.result()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)
        subscription.unsubscribe()


@router.get("/{task_id}/metrics")
async def list_task_metrics(task_id: str):
    result = await task_service.list_task_metrics(task_id)
    if result is None:
        raise HTTPException(404, "Task not found")
    return result


@router.get("/{task_id}/metrics/history")
async def list_task_metric_history(
    task_id: str,
    algorithm_id: Optional[str] = Query(None),
    metric_type: Optional[str] = Query(None),
    region_id: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
):
    result = await task_service.list_task_metric_history(
        task_id,
        {
            "algorithm_id": algorithm_id,
            "metric_type": metric_type,
            "region_id": region_id,
            "start_time": start_time,
            "end_time": end_time,
            "page": page,
            "page_size": page_size,
        },
    )
    if result is None:
        raise HTTPException(404, "Task not found")
    return result


@router.post("/{task_id}/metadata/resync")
async def resync_task_metadata(task_id: str):
    result = await task_service.resync_task_metadata(task_id)
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.get("/{task_id}/metadata")
async def get_task_metadata_snapshot(task_id: str):
    result = await task_service.get_task_metadata_snapshot(task_id)
    if not result:
        raise HTTPException(404, "Task metadata not found")
    return result


@router.get("/{task_id}/metadata/task-view")
async def get_task_from_metadata(task_id: str):
    result = await task_service.get_task_from_metadata(task_id)
    if not result:
        raise HTTPException(404, "Task metadata task-view not found")
    return result


@router.get("/{task_id}/metadata/compare")
async def compare_task_views(task_id: str):
    result = await task_service.compare_task_views(task_id)
    if not result:
        raise HTTPException(404, "Task compare view not found")
    return result


@router.post("/{task_id}/algorithms")
async def add_algorithm(task_id: str, request: AlgorithmRequest):
    try:
        result = await task_service.add_algorithm(task_id, request.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Task not found")
    return result


@router.put("/{task_id}/algorithms/{algorithm_id}")
async def update_algorithm(task_id: str, algorithm_id: str, request: AlgorithmRequest):
    try:
        result = await task_service.update_algorithm(task_id, algorithm_id, request.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Task or algorithm not found")
    return result


@router.delete("/{task_id}/algorithms/{algorithm_id}")
async def delete_algorithm(task_id: str, algorithm_id: str):
    result = await task_service.delete_algorithm(task_id, algorithm_id)
    if not result:
        raise HTTPException(404, "Task or algorithm not found")
    return result


@router.put("/{task_id}/algorithms/{algorithm_id}/regions")
async def replace_regions(task_id: str, algorithm_id: str, request: RegionBatchRequest):
    try:
        result = await task_service.replace_regions(task_id, algorithm_id, request.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Task or algorithm not found")
    return result
