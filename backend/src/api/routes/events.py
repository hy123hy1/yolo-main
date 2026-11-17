from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.services.event_service import EventService
from src.core.settings import settings

router = APIRouter(prefix="/events", tags=["events"])
event_service = EventService()


def resolve_data_file(path_value: str | None) -> Path | None:
    if not path_value:
        return None

    normalized = str(path_value).replace("\\", "/")
    if normalized.startswith("/static/"):
        candidate = settings.DATA_DIR / normalized.replace("/static/", "", 1)
        return candidate if candidate.exists() else None

    candidates = [Path(normalized)]
    if normalized.startswith("/app/data/"):
        relative = normalized.replace("/app/data/", "", 1)
        candidates.insert(0, settings.DATA_DIR / relative)
    else:
        candidates.append(settings.DATA_DIR / normalized.lstrip("/"))
        candidates.append(settings.DATA_DIR / "snapshots" / Path(normalized).name)
        candidates.append(settings.DATA_DIR / "recordings" / Path(normalized).name)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.exists():
                return resolved
        except Exception:
            continue
    return None


class UpdateEventRequest(BaseModel):
    event_type: Optional[str] = None
    class_name: Optional[str] = None
    conf: Optional[float] = None
    snapshot_path: Optional[str] = None
    video_path: Optional[str] = None
    handling_status: Optional[int] = None
    remark: Optional[str] = None
    status: Optional[str] = None


@router.get("")
async def list_events(
    channel_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    algorithm_id: Optional[str] = Query(None),
    class_name: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    return await event_service.list_events(
        channel_id=channel_id,
        task_id=task_id,
        algorithm_id=algorithm_id,
        class_name=class_name,
        page=page,
        page_size=page_size,
    )


@router.get("/{event_id}")
async def get_event(event_id: str):
    result = await event_service.get_event(event_id)
    if not result:
        raise HTTPException(404, "Event not found")
    return result


@router.put("/{event_id}")
async def update_event(event_id: str, request: UpdateEventRequest):
    result = await event_service.update_event(event_id, request)
    if not result:
        raise HTTPException(404, "Event not found")
    return result


@router.delete("/{event_id}")
async def delete_event(event_id: str):
    result = await event_service.delete_event(event_id)
    if not result:
        raise HTTPException(404, "Event not found")
    return result


@router.delete("/{event_id}/media")
async def delete_event_media(event_id: str):
    result = await event_service.delete_event_media(event_id)
    if not result:
        raise HTTPException(404, "Event not found")
    return result


@router.get("/{event_id}/snapshot")
async def get_event_snapshot(event_id: str):
    event = await event_service.get_event(event_id)
    if not event:
        raise HTTPException(404, "Event not found")

    snapshot_path = event.get("snapshot_path")
    if not snapshot_path:
        raise HTTPException(404, "Event snapshot not found")

    path = resolve_data_file(snapshot_path)
    if not path:
        raise HTTPException(404, "Event snapshot file not found")

    return FileResponse(
        path,
        media_type="image/jpeg",
        filename=path.name,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
        },
    )
