from __future__ import annotations

from sqlalchemy.engine import Engine

from src.db.database import engine as default_engine
from src.db.models import (
    Base,
    CameraHealthBaselineMeta,
    CameraHealthConfigMeta,
    CameraHealthEventMeta,
    CameraHealthStateMeta,
)


CAMERA_HEALTH_TABLES = [
    CameraHealthConfigMeta.__table__,
    CameraHealthBaselineMeta.__table__,
    CameraHealthStateMeta.__table__,
    CameraHealthEventMeta.__table__,
]


def ensure_camera_health_schema(bind: Engine | None = None) -> None:
    Base.metadata.create_all(bind=bind or default_engine, tables=CAMERA_HEALTH_TABLES)
