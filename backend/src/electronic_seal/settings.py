from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


SUPPORTED_ALGORITHMS = frozenset(
    {
        "truck_departure",
        "room_empty",
        "camera_occlusion",
        "camera_view_shift",
    }
)

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = _BACKEND_DIR / "data" / "electronic_seal"
_DEFAULT_EVIDENCE_DIR = _DEFAULT_DATA_DIR / "evidence"
_DEFAULT_SNAPSHOT_DIR = _DEFAULT_DATA_DIR / "snapshots"
_DEFAULT_MODEL_REGISTRY_DIR = _BACKEND_DIR / "models" / "registry"
_DEFAULT_DEFAULTS_FILE = _BACKEND_DIR / "configs" / "electronic_seal_defaults.toml"


class ElectronicSealSettings(BaseSettings):
    enabled: bool = True
    device: str = "cpu"
    max_workers: int = Field(default=2, ge=1)
    max_channel_workers: int = Field(default=8, ge=1)
    sample_interval_ms: int = Field(default=1000, ge=1)
    online_status_interval_seconds: int = Field(default=3600, ge=60)
    camera_health_interval_seconds: int = Field(default=1, ge=1)
    frame_first_frame_grace_seconds: float = Field(default=15.0, gt=0)
    baseline_warmup_seconds: float = Field(default=15.0, ge=0)
    baseline_min_valid_frames: int = Field(default=10, ge=1)
    baseline_max_calibration_seconds: float = Field(default=60.0, gt=0)
    runtime_lease_seconds: float = Field(default=30.0, gt=0)
    runtime_heartbeat_seconds: float = Field(default=10.0, gt=0)
    data_dir: Path = _DEFAULT_DATA_DIR
    evidence_dir: Path = _DEFAULT_EVIDENCE_DIR
    snapshot_dir: Path = _DEFAULT_SNAPSHOT_DIR
    model_registry_dir: Path = _DEFAULT_MODEL_REGISTRY_DIR
    defaults_file: Path = _DEFAULT_DEFAULTS_FILE
    reconcile_retry_seconds: float = Field(default=10.0, ge=1.0)
    upload_max_attempts: int = Field(default=5, ge=1)
    upload_retry_base_seconds: float = Field(default=5.0, ge=0)
    upload_retry_max_seconds: float = Field(default=300.0, ge=0)
    supervision_base_url: str = ""
    supervision_event_path: str = "/events"
    supervision_evidence_path: str = "/events/{event_id}/evidence"
    supervision_request_timeout_seconds: float = Field(default=10.0, gt=0)
    upload_deadline_seconds: float = Field(default=30.0, gt=0)
    upload_poll_seconds: float = Field(default=1.0, gt=0)
    evidence_video_before_seconds: float = Field(default=10.0, ge=0)
    evidence_video_after_seconds: float = Field(default=10.0, ge=0)
    evidence_video_fps: float = Field(default=25.0, gt=0)
    evidence_buffer_fps: float = Field(default=30.0, gt=0)
    evidence_buffer_max_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    evidence_buffer_jpeg_quality: int = Field(default=75, ge=10, le=95)
    mine_code: str = Field(default="140421007866", min_length=12, max_length=12, pattern=r"^\d{12}$")
    camera_code: str = Field(default="20", min_length=2, max_length=2, pattern=r"^\d{2}$")
    analysis_type_code: str = Field(default="04", min_length=2, max_length=2, pattern=r"^\d{2}$")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ELECTRONIC_SEAL_",
        extra="ignore",
        protected_namespaces=(),
    )

    @model_validator(mode="after")
    def derive_evidence_dir(self) -> "ElectronicSealSettings":
        if self.evidence_dir == _DEFAULT_EVIDENCE_DIR and self.data_dir != _DEFAULT_DATA_DIR:
            self.evidence_dir = self.data_dir / "evidence"
        if self.snapshot_dir == _DEFAULT_SNAPSHOT_DIR and self.data_dir != _DEFAULT_DATA_DIR:
            self.snapshot_dir = self.data_dir / "snapshots"
        return self


__all__ = ["ElectronicSealSettings", "SUPPORTED_ALGORITHMS"]
