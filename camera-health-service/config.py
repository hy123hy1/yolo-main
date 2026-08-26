from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "CAMERA_HEALTH_DATABASE_URL",
        "sqlite:///./camera_health.sqlite3",
    )
    snapshot_dir: Path = Path(
        os.getenv(
            "CAMERA_HEALTH_SNAPSHOT_DIR",
            "/app/data/camera_health/snapshots",
        )
    )
    wvp_api_base: str = os.getenv("WVP_API_BASE", "")
    wvp_channel_list_path: str = os.getenv(
        "WVP_CHANNEL_LIST_PATH",
        "/api/common/channel/list",
    )
    wvp_channel_play_path: str = os.getenv(
        "WVP_CHANNEL_PLAY_PATH",
        "/api/common/channel/play",
    )
    wvp_timeout_seconds: float = float(
        os.getenv("WVP_TIMEOUT_SECONDS", "20")
    )
    ffmpeg_bin: str = os.getenv("FFMPEG_BIN", "ffmpeg")
    ffmpeg_timeout_seconds: float = float(
        os.getenv("CAMERA_HEALTH_FFMPEG_TIMEOUT_SECONDS", "15")
    )
    frame_width: int = int(os.getenv("CAMERA_HEALTH_FRAME_WIDTH", "1280"))
    frame_height: int = int(os.getenv("CAMERA_HEALTH_FRAME_HEIGHT", "720"))
    alarm_hold_seconds: float = float(
        os.getenv("CAMERA_HEALTH_ALARM_HOLD_SECONDS", "10")
    )
    recovery_hold_seconds: float = float(
        os.getenv("CAMERA_HEALTH_RECOVERY_HOLD_SECONDS", "5")
    )
    analysis_interval_seconds: float = float(
        os.getenv("CAMERA_HEALTH_ANALYSIS_INTERVAL_SECONDS", "0.5")
    )
    frame_timeout_seconds: float = float(
        os.getenv("CAMERA_HEALTH_FRAME_TIMEOUT_SECONDS", "5")
    )
    baseline_stable_frames: int = int(
        os.getenv("CAMERA_HEALTH_BASELINE_STABLE_FRAMES", "10")
    )
    mine_code: str = os.getenv("CAMERA_HEALTH_MINE_CODE", "").strip()
    person_count_model_path: Path = Path(
        os.getenv(
            "CAMERA_HEALTH_PERSON_COUNT_MODEL_PATH",
            "/app/camera-health-service/model/yolo11s.pt",
        )
    )
    person_count_interval_seconds: float = float(
        os.getenv("CAMERA_HEALTH_PERSON_COUNT_INTERVAL_SECONDS", "1.0")
    )
    person_count_confidence: float = float(
        os.getenv("CAMERA_HEALTH_PERSON_COUNT_CONFIDENCE", "0.25")
    )
    person_count_image_size: int = int(
        os.getenv("CAMERA_HEALTH_PERSON_COUNT_IMAGE_SIZE", "640")
    )
    person_count_device: str = os.getenv(
        "CAMERA_HEALTH_PERSON_COUNT_DEVICE",
        "cuda:0",
    ).strip()
    person_count_snapshot_dir: Path = Path(
        os.getenv(
            "CAMERA_HEALTH_PERSON_COUNT_SNAPSHOT_DIR",
            "/app/data/camera_health/person_count",
        )
    )

    def __post_init__(self) -> None:
        if self.person_count_interval_seconds <= 0:
            raise ValueError(
                "CAMERA_HEALTH_PERSON_COUNT_INTERVAL_SECONDS must be greater than 0"
            )
        if not 0 <= self.person_count_confidence <= 1:
            raise ValueError(
                "CAMERA_HEALTH_PERSON_COUNT_CONFIDENCE must be between 0 and 1"
            )
        if self.person_count_image_size <= 0:
            raise ValueError(
                "CAMERA_HEALTH_PERSON_COUNT_IMAGE_SIZE must be greater than 0"
            )


HEALTH_DETECTOR_THRESHOLDS: dict[str, dict[str, float | int]] = {
    "occlusion": {
        "low_brightness": 12.0,
        "high_brightness": 245.0,
        "min_entropy": 1.2,
        "min_edge_density": 0.005,
        "min_blur_variance": 20.0,
        "max_uniform_ratio": 0.85,
        "max_baseline_difference": 0.55,
        "min_bad_signals": 2,
        "local_occlusion_ratio": 0.35,
    },
    "view_shift": {
        "ratio_test": 0.75,
        "min_good_matches": 20,
        "ransac_reproj_threshold": 5.0,
        "min_inlier_ratio": 0.35,
        "max_horizontal_shift_ratio": 0.08,
        "max_vertical_shift_ratio": 0.08,
        "max_scale_change_ratio": 0.12,
        "roll_angle_deg": 8.0,
        "max_perspective_change": 0.15,
    },
    "blur": {"min_blur_variance": 20.0},
    "exposure": {
        "low_brightness": 12.0,
        "high_brightness": 245.0,
        "min_extreme_ratio": 0.7,
    },
    "freeze": {"max_difference": 0.001},
    "video_loss": {"max_stale_seconds": 5.0},
    "shake": {
        "max_shift_ratio": 0.02,
        "min_response": 0.1,
        "window_size": 5,
        "min_bad_samples": 3,
    },
    "resolution": {
        "expected_width": int(os.getenv("CAMERA_HEALTH_FRAME_WIDTH", "1280")),
        "expected_height": int(os.getenv("CAMERA_HEALTH_FRAME_HEIGHT", "720")),
        "tolerance_pixels": 0,
    },
}


settings = Settings()
