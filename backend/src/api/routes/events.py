from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.services.event_service import EventService

router = APIRouter(prefix="/events", tags=["events"])
event_service = EventService()


class UpdateEventRequest(BaseModel):
    event_type: Optional[str] = None
    class_name: Optional[str] = None
    conf: Optional[float] = None
    snapshot_path: Optional[str] = None
    remark: Optional[str] = None
    status: Optional[str] = None


@router.get("")
async def list_events(
    channel_id: Optional[str] = Query(None),
    class_name: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    return await event_service.list_events(
        channel_id=channel_id,
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


@router.get("/{event_id}/snapshot")
async def get_event_snapshot(event_id: str):
    event = await event_service.get_event(event_id)
    if not event:
        raise HTTPException(404, "Event not found")

    snapshot_path = event.get("snapshot_path")
    if not snapshot_path:
        raise HTTPException(404, "Event snapshot not found")

    path = Path(snapshot_path)
    if not path.exists():
        raise HTTPException(404, "Event snapshot file not found")

    return FileResponse(
        path,
        media_type="image/jpeg",
        filename=path.name,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
        },
    )
