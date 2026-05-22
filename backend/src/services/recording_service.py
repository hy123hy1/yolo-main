import asyncio
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.core.settings import settings
from src.services.platform_storage import generate_id, list_json_items, load_json, now_iso, save_json


class RecordingService:
    def __init__(self):
        self.recordings_dir: Path = settings.RECORDINGS_DIR

    def _get_path(self, recording_id: str) -> Path:
        return self.recordings_dir / f"{recording_id}.json"

    def _resolve_static_url(self, file_path: str | None) -> str | None:
        if not file_path:
            return None

        normalized = str(file_path).replace("\\", "/")
        if normalized.startswith("/static/"):
            return normalized

        candidates = [
            settings.DATA_DIR / normalized.lstrip("/"),
            settings.DATA_DIR / "recordings" / Path(normalized).name,
            settings.DATA_DIR / "snapshots" / Path(normalized).name,
        ]

        if normalized.startswith("/app/data/"):
            relative = normalized.replace("/app/data/", "", 1)
            candidates.insert(0, settings.DATA_DIR / relative)

        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved.exists():
                    relative = resolved.relative_to(settings.DATA_DIR.resolve()).as_posix()
                    return f"/static/{relative}"
            except Exception:
                continue
        return None

    def _decorate_item(self, item: dict) -> dict:
        next_item = dict(item)
        file_path = next_item.get("file_path")
        next_item["video_url"] = self._resolve_static_url(file_path)
        raw_json = next_item.get("raw_json") or {}
        next_item["event_id"] = raw_json.get("event_id")
        next_item["event_type"] = raw_json.get("event_type") or next_item.get("status")
        snapshot_path = raw_json.get("snapshot_path")
        next_item["snapshot_url"] = self._resolve_static_url(snapshot_path)
        return next_item

    def _normalize_recording(self, payload: dict) -> dict:
        item = {
            "id": payload.get("id") or generate_id("rec"),
            "channel_id": payload.get("channel_id"),
            "started_at": payload.get("started_at"),
            "ended_at": payload.get("ended_at"),
            "duration": payload.get("duration"),
            "file_path": payload.get("file_path"),
            "status": payload.get("status", "ready"),
            "created_at": payload.get("created_at") or now_iso(),
            "updated_at": payload.get("updated_at") or now_iso(),
            "raw_json": payload.get("raw_json") or {},
        }
        if payload.get("event_id"):
            item["raw_json"] = {**item["raw_json"], "event_id": payload.get("event_id")}
        return item

    def create_recording_sync(self, payload: dict) -> dict:
        item = self._normalize_recording(payload)
        save_json(self._get_path(item["id"]), item)
        return item

    def update_recording_sync(self, recording_id: str, payload: dict) -> dict | None:
        path = self._get_path(recording_id)
        if not path.exists():
            return None
        current = load_json(path)
        current.update(payload)
        current["updated_at"] = now_iso()
        save_json(path, current)
        return current

    async def create_recording(self, payload: dict):
        return await asyncio.to_thread(self.create_recording_sync, payload)

    async def update_recording(self, recording_id: str, payload: dict):
        return await asyncio.to_thread(self.update_recording_sync, recording_id, payload)

    async def list_recordings(self, channel_id: str | None = None):
        def _list():
            items = list_json_items(self.recordings_dir)
            if channel_id:
                items[:] = [item for item in items if item.get("channel_id") == channel_id]
            items = sorted(items, key=lambda x: x.get("started_at", ""), reverse=True)
            return {"items": [self._decorate_item(item) for item in items]}

        return await asyncio.to_thread(_list)

    async def get_recording(self, recording_id: str):
        path = self._get_path(recording_id)
        if not await asyncio.to_thread(path.exists):
            return None
        return self._decorate_item(await asyncio.to_thread(load_json, path))

    async def timeline(self, channel_id: str | None = None):
        data = await self.list_recordings(channel_id)
        return {
            "items": [
                {
                    "id": item.get("id"),
                    "channel_id": item.get("channel_id"),
                    "started_at": item.get("started_at"),
                    "ended_at": item.get("ended_at"),
                    "duration": item.get("duration"),
                }
                for item in data["items"]
            ]
        }

    async def monthly(self, channel_id: str | None = None):
        data = await self.list_recordings(channel_id)

        def _monthly():
            stats: dict[str, int] = defaultdict(int)
            for item in data["items"]:
                started_at = item.get("started_at")
                if not started_at:
                    continue
                try:
                    month_key = datetime.fromisoformat(started_at).strftime("%Y-%m")
                except ValueError:
                    continue
                stats[month_key] += 1
            return {"items": [{"month": key, "count": value} for key, value in sorted(stats.items())]}

        return await asyncio.to_thread(_monthly)
