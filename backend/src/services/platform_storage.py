import json
import logging
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4


logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now().isoformat()


def generate_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"


def load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    with open(fd, "w", encoding="utf-8", closefd=True) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
    tmp_path.replace(path)
    try:
        from src.services.metadata_db_sync import sync_json_document

        sync_json_document(path, data)
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
