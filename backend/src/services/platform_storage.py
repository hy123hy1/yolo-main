import json
import logging
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4


logger = logging.getLogger(__name__)

DATETIME_FIELD_NAMES = {
    "created_at",
    "updated_at",
    "started_at",
    "stopped_at",
    "ended_at",
    "last_analysis_at",
    "last_event_at",
    "uploaded_at",
    "prepared_at",
    "completed_at",
    "failed_at",
    "crashed_at",
    "resumed_at",
    "checked_at",
}


def format_datetime(value: datetime | str | None) -> str | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    text = str(value).strip()
    if not text:
        return None

    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return text

    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def normalize_datetime_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        normalized: dict[str, Any] = {}
        for key, value in payload.items():
            if key in DATETIME_FIELD_NAMES or key.endswith("_at"):
                normalized[key] = format_datetime(value)
            else:
                normalized[key] = normalize_datetime_fields(value)
        return normalized

    if isinstance(payload, list):
        return [normalize_datetime_fields(item) for item in payload]

    return payload


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def generate_id(_prefix: str) -> str:
    return uuid4().hex[:8]


def load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return normalize_datetime_fields(json.load(f))


def save_json(path: Path, data: dict[str, Any], *, sync_metadata: bool = True) -> None:
    normalized_data = normalize_datetime_fields(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    with open(fd, "w", encoding="utf-8", closefd=True) as f:
        json.dump(normalized_data, f, indent=2, ensure_ascii=False)
        f.flush()
    tmp_path.replace(path)
    if not sync_metadata:
        return
    try:
        from src.services.metadata_db_sync import sync_json_document

        sync_json_document(path, normalized_data)
    except Exception:
        logger.exception("Failed to sync metadata document into database: %s", path)


def list_json_items(directory: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not directory.exists():
        return items

    for file in directory.glob("*.json"):
        try:
            items.append(load_json(file))
        except Exception:
            continue
    return items
