from pathlib import Path
import re
from urllib.parse import quote

from src.core.settings import settings


def alarm_video_path(event_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", event_id):
        raise ValueError("invalid event id for alarm video")
    return settings.RECORDINGS_DIR / f"alarm_{event_id}.mp4"


def public_media_url(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).replace("\\", "/")
    if normalized.startswith("/static/"):
        relative = Path(normalized[len("/static/"):])
    elif normalized.startswith("/app/data/"):
        relative = Path(normalized[len("/app/data/"):])
    else:
        try:
            relative = Path(normalized).resolve().relative_to(settings.DATA_DIR.resolve())
        except ValueError as exc:
            raise ValueError("alarm media must be inside DATA_DIR") from exc
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("invalid alarm media path")
    return f"{settings.ALARM_PUSH_PUBLIC_BASE_URL.rstrip('/')}/static/{quote(relative.as_posix(), safe='/')}"
