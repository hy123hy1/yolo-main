import asyncio
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.core.settings import settings
from src.services.platform_storage import list_json_items, load_json


class RecordingService:
    def __init__(self):
        self.recordings_dir: Path = settings.RECORDINGS_DIR

    def _get_path(self, recording_id: str) -> Path:
        return self.recordings_dir / f"{recording_id}.json"

    async def list_recordings(self, channel_id: str | None = None):
        def _list():
            items = list_json_items(self.recordings_dir)
            if channel_id:
                items[:] = [item for item in items if item.get("channel_id") == channel_id]
            return {"items": sorted(items, key=lambda x: x.get("started_at", ""), reverse=True)}

        return await asyncio.to_thread(_list)

    async def get_recording(self, recording_id: str):
        path = self._get_path(recording_id)
        if not await asyncio.to_thread(path.exists):
            return None
        return await asyncio.to_thread(load_json, path)

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
