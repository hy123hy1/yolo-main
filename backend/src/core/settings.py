import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings


def _set_env_if_absent(name: str, value: Any) -> None:
    if value is None or name in os.environ:
        return
    if isinstance(value, bool):
        os.environ[name] = "true" if value else "false"
        return
    os.environ[name] = str(value)


def _load_config_toml_to_environment(base_dir: Path) -> None:
    config_hint = os.environ.get("APP_CONFIG_FILE", "configs/config.toml")
    config_path = Path(config_hint)
    if not config_path.is_absolute():
        config_path = base_dir / config_path
    if not config_path.exists():
        return

    with config_path.open("rb") as fp:
        config = tomllib.load(fp)

    server = config.get("Server", {})
    server_http = server.get("HTTP", {}) if isinstance(server.get("HTTP"), dict) else {}
    _set_env_if_absent("HOST", server.get("Host"))
    _set_env_if_absent("PORT", server_http.get("Port", server.get("Port")))
    _set_env_if_absent("ENABLE_TRAINING", server.get("EnableTraining"))
    server_web = server.get("Web", {}) if isinstance(server.get("Web"), dict) else {}
    _set_env_if_absent("WEB_PUBLIC_BASE", server_web.get("PublicBase"))
    monitor = server.get("Monitor", {}) if isinstance(server.get("Monitor"), dict) else {}
    _set_env_if_absent("HOST_MONITOR_AGENT_URL", monitor.get("HostAgentURL"))
    _set_env_if_absent("HOST_MONITOR_AGENT_TIMEOUT_SECONDS", monitor.get("HostAgentTimeoutSeconds"))
    _set_env_if_absent("HOST_MONITOR_METRICS_FILE", monitor.get("HostMetricsFile"))
    _set_env_if_absent("HOST_MONITOR_METRICS_MAX_AGE_SECONDS", monitor.get("HostMetricsMaxAgeSeconds"))
    startup = server.get("Startup", {}) if isinstance(server.get("Startup"), dict) else {}
    _set_env_if_absent("STARTUP_TASK_RESTORE_INITIAL_DELAY_SECONDS", startup.get("TaskRestoreInitialDelaySeconds"))
    _set_env_if_absent("TASK_RESTORE_STARTUP_STAGGER_SECONDS", startup.get("TaskRestoreStaggerSeconds"))
    _set_env_if_absent("TASK_PREVIEW_WARMUP_STARTUP_DELAY_SECONDS", startup.get("TaskPreviewWarmupStartupDelaySeconds"))
    _set_env_if_absent("TASK_PREVIEW_WARMUP_MAX_CONCURRENCY", startup.get("TaskPreviewWarmupMaxConcurrency"))
    _set_env_if_absent("TASK_ANALYSIS_STARTUP_WARMUP_SECONDS", startup.get("TaskAnalysisStartupWarmupSeconds"))
    _set_env_if_absent("TASK_ANALYSIS_ALGORITHM_MAX_CONCURRENCY", startup.get("TaskAnalysisAlgorithmMaxConcurrency"))
    _set_env_if_absent("ALARM_VIDEO_BEFORE_SECONDS", startup.get("AlarmVideoBeforeSeconds"))
    _set_env_if_absent("ALARM_VIDEO_AFTER_SECONDS", startup.get("AlarmVideoAfterSeconds"))
    _set_env_if_absent("ALARM_VIDEO_JOB_MAX_WORKERS", startup.get("AlarmVideoJobMaxWorkers"))
    _set_env_if_absent("TASK_ALARM_VIDEO_CAPTURE_TIMEOUT_SECONDS", startup.get("TaskAlarmVideoCaptureTimeoutSeconds"))
    _set_env_if_absent("TASK_ALARM_VIDEO_READ_FAIL_LIMIT", startup.get("TaskAlarmVideoReadFailLimit"))

    camera_health = config.get("CameraHealth", {})
    if isinstance(camera_health, dict):
        _set_env_if_absent("CAMERA_HEALTH_ENABLED", camera_health.get("Enabled"))
        _set_env_if_absent("CAMERA_HEALTH_SAMPLE_INTERVAL_MS", camera_health.get("SampleIntervalMs"))
        _set_env_if_absent("CAMERA_HEALTH_FRAME_STALE_SECONDS", camera_health.get("FrameStaleSeconds"))
        _set_env_if_absent("CAMERA_HEALTH_WARMUP_SECONDS", camera_health.get("WarmupSeconds"))
        _set_env_if_absent("CAMERA_HEALTH_ALARM_HOLD_SECONDS", camera_health.get("AlarmHoldSeconds"))
        _set_env_if_absent("CAMERA_HEALTH_RECOVERY_HOLD_SECONDS", camera_health.get("RecoveryHoldSeconds"))
        _set_env_if_absent("CAMERA_HEALTH_MAX_WORKERS", camera_health.get("MaxWorkers"))
        _set_env_if_absent("CAMERA_HEALTH_THRESHOLDS_FILE", camera_health.get("ThresholdsFile"))

    ai_cfg = config.get("AI", {}) if isinstance(config.get("AI"), dict) else {}
    _set_env_if_absent("AI_INFERENCE_DEVICE", ai_cfg.get("InferenceDevice"))
    _set_env_if_absent("AI_TASK_GPU_ADAPTIVE", ai_cfg.get("TaskGpuAdaptive"))
    _set_env_if_absent("AI_GPU_CANDIDATES", ai_cfg.get("GpuCandidates"))
    _set_env_if_absent("AI_GPU_MIN_FREE_MB", ai_cfg.get("GpuMinFreeMB"))
    _set_env_if_absent("AI_GPU_TASK_RESERVE_MB", ai_cfg.get("GpuTaskReserveMB"))
    _set_env_if_absent("AI_INFERENCE_BACKEND", ai_cfg.get("InferenceBackend"))
    _set_env_if_absent("ASCEND_DEVICE_ID", ai_cfg.get("AscendDeviceId"))

    push = config.get("AlarmPush", {})
    _set_env_if_absent("ALARM_PUSH_ENABLED", push.get("Enabled"))
    _set_env_if_absent("ALARM_PUSH_URL", push.get("PushURL"))
    _set_env_if_absent("ALARM_PUSH_PUBLIC_BASE_URL", push.get("PublicBaseURL"))
    _set_env_if_absent("ALARM_PUSH_TIMEOUT_SECONDS", push.get("TimeoutSeconds"))
    _set_env_if_absent("ALARM_PUSH_MAX_ATTEMPTS", push.get("MaxAttempts"))

    data_cfg = config.get("Data", {})
    _set_env_if_absent("METADATA_DB_ENABLED", data_cfg.get("MetadataDBEnabled"))
    _set_env_if_absent("METADATA_DB_STRICT", data_cfg.get("MetadataDBStrict"))

    database_cfg = data_cfg.get("Database", {}) if isinstance(data_cfg.get("Database"), dict) else {}
    _set_env_if_absent("METADATA_DATABASE_URL", database_cfg.get("Dsn"))

    media = config.get("Media", {})
    _set_env_if_absent("ZLM_API_BASE", media.get("APIBase"))
    _set_env_if_absent("ZLM_PLAY_BASE", media.get("PlayBase"))
    _set_env_if_absent("ZLM_WEBRTC_BASE", media.get("WebRTCBase"))
    _set_env_if_absent("FFMPEG_BIN", media.get("FFmpegBin"))
    _set_env_if_absent("ZLM_SECRET", media.get("Secret"))
    _set_env_if_absent("ZLM_PUBLIC_RTSP_HOST", media.get("PublicRTSPHost"))
    _set_env_if_absent("ZLM_PUBLIC_RTMP_HOST", media.get("PublicRTMPHost"))
    _set_env_if_absent("ZLM_PUBLIC_HTTP_HOST", media.get("PublicHTTPHost"))
    _set_env_if_absent("ZLM_RTSP_PORT", media.get("RTSPPort"))
    _set_env_if_absent("ZLM_RTMP_PORT", media.get("RTMPPort"))
    _set_env_if_absent("ZLM_HTTP_PORT", media.get("HTTPPort"))
    _set_env_if_absent("ZLM_HTTPS_PORT", media.get("HTTPSPort"))
    _set_env_if_absent("ZLM_HLS_FILE", media.get("HLSFile"))
    _set_env_if_absent("ZLM_HTTPFLV_SUFFIX", media.get("HTTPFLVSuffix"))
    _set_env_if_absent("ZLM_WEBRTC_PATH", media.get("WebRTCPath"))

    wvp = config.get("WVP", {})
    _set_env_if_absent("WVP_ENABLED", wvp.get("Enabled"))
    _set_env_if_absent("WVP_API_BASE", wvp.get("APIBase"))
    _set_env_if_absent("WVP_CHANNEL_LIST_PATH", wvp.get("ChannelListPath"))
    _set_env_if_absent("WVP_CHANNEL_PLAY_PATH", wvp.get("ChannelPlayPath"))
    _set_env_if_absent("WVP_PUBLIC_FLV_BASE", wvp.get("PublicFLVBase"))


class Settings(BaseSettings):
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    CONFIG_DIR: Path = BASE_DIR / "configs"
    CONFIG_FILE: Path = CONFIG_DIR / "config.toml"

    DATA_DIR: Path = BASE_DIR / "data"
    UPLOADS_DIR: Path = DATA_DIR / "uploads"
    DATASETS_DIR: Path = DATA_DIR / "datasets"
    ANNOTATIONS_DIR: Path = DATA_DIR / "annotations"
    JOBS_DIR: Path = DATA_DIR / "jobs"
    INFERENCE_RESULTS_DIR: Path = DATA_DIR / "inference_results"
    DEVICES_DIR: Path = DATA_DIR / "devices"
    CHANNELS_DIR: Path = DATA_DIR / "channels"
    TASKS_DIR: Path = DATA_DIR / "tasks"
    MEDIA_NODES_DIR: Path = DATA_DIR / "media_nodes"
    EVENTS_DIR: Path = DATA_DIR / "events"
    RECORDINGS_DIR: Path = DATA_DIR / "recordings"
    SNAPSHOTS_DIR: Path = DATA_DIR / "snapshots"
    TRANSCODE_LOGS_DIR: Path = DATA_DIR / "transcode_logs"

    MODELS_DIR: Path = BASE_DIR / "models"
    REGISTRY_DIR: Path = MODELS_DIR / "registry"

    API_PREFIX: str = ""
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WEB_PUBLIC_BASE: str = "http://localhost:3000"
    ALARM_PUSH_ENABLED: bool = False
    ALARM_PUSH_URL: str = ""
    ALARM_PUSH_PUBLIC_BASE_URL: str = "http://192.168.1.61:8000"
    ALARM_PUSH_TIMEOUT_SECONDS: float = 10.0
    ALARM_PUSH_MAX_ATTEMPTS: int = 3
    HOST_MONITOR_AGENT_URL: str = ""
    HOST_MONITOR_AGENT_TIMEOUT_SECONDS: float = 2
    HOST_MONITOR_METRICS_FILE: Path = DATA_DIR / "host_monitor_resources.json"
    HOST_MONITOR_METRICS_MAX_AGE_SECONDS: float = 10
    STARTUP_TASK_RESTORE_INITIAL_DELAY_SECONDS: float = 2.0
    TASK_RESTORE_STARTUP_STAGGER_SECONDS: float = 0.5
    TASK_PREVIEW_WARMUP_STARTUP_DELAY_SECONDS: float = 2.0
    TASK_PREVIEW_WARMUP_MAX_CONCURRENCY: int = 1
    TASK_ANALYSIS_STARTUP_WARMUP_SECONDS: float = 5.0
    TASK_ANALYSIS_ALGORITHM_MAX_CONCURRENCY: int = 2

    FFMPEG_BIN: str = "ffmpeg"
    ZLM_API_BASE: str = "http://host.docker.internal:8010"
    ZLM_PLAY_BASE: str = "http://localhost:8010"
    ZLM_WEBRTC_BASE: str = "webrtc://localhost:8010"
    ZLM_SECRET: str = ""
    ZLM_PUBLIC_RTSP_HOST: str = "localhost"
    ZLM_PUBLIC_RTMP_HOST: str = "localhost"
    ZLM_PUBLIC_HTTP_HOST: str = "localhost"
    ZLM_RTSP_PORT: int = 554
    ZLM_RTMP_PORT: int = 1935
    ZLM_HTTP_PORT: int = 8010
    ZLM_HTTPS_PORT: int = 443
    ZLM_HLS_FILE: str = "hls.fmp4.m3u8"
    ZLM_HTTPFLV_SUFFIX: str = ".live.flv"
    ZLM_WEBRTC_PATH: str = "/index/api/webrtc"
    ALARM_VIDEO_BEFORE_SECONDS: int = 10
    ALARM_VIDEO_AFTER_SECONDS: int = 10
    ALARM_VIDEO_JOB_MAX_WORKERS: int = 2
    TASK_ALARM_VIDEO_CAPTURE_TIMEOUT_SECONDS: float = 0
    TASK_ALARM_VIDEO_READ_FAIL_LIMIT: int = 20
    AI_INFERENCE_DEVICE: str = "auto"
    AI_TASK_GPU_ADAPTIVE: bool = False
    AI_GPU_CANDIDATES: str = ""
    AI_GPU_MIN_FREE_MB: int = 2048
    AI_GPU_TASK_RESERVE_MB: int = 1024
    AI_INFERENCE_BACKEND: str = "auto"
    ASCEND_DEVICE_ID: int = 0

    CAMERA_HEALTH_ENABLED: bool = True
    CAMERA_HEALTH_SAMPLE_INTERVAL_MS: int = 1000
    CAMERA_HEALTH_FRAME_STALE_SECONDS: float = 5.0
    CAMERA_HEALTH_WARMUP_SECONDS: int = 30
    CAMERA_HEALTH_ALARM_HOLD_SECONDS: int = 10
    CAMERA_HEALTH_RECOVERY_HOLD_SECONDS: int = 15
    CAMERA_HEALTH_MAX_WORKERS: int = 8
    CAMERA_HEALTH_THRESHOLDS_FILE: Path = CONFIG_DIR / "camera_health_thresholds.toml"
    CAMERA_HEALTH_SNAPSHOT_DIR: Path = DATA_DIR / "camera_health_snapshots"

    WVP_ENABLED: bool = False
    WVP_API_BASE: str = ""
    WVP_CHANNEL_LIST_PATH: str = "/api/common/channel/list"
    WVP_CHANNEL_PLAY_PATH: str = "/api/common/channel/play"
    WVP_PUBLIC_FLV_BASE: str = ""

    METADATA_DB_ENABLED: bool = True
    METADATA_DB_STRICT: bool = False
    ENABLE_TRAINING: bool = False
    METADATA_DATABASE_URL: str = ""
    SQLITE_DB_PATH: Path = DATA_DIR / "metadata.db"

    MYSQL_HOST: str = "192.168.1.58"
    MYSQL_PORT: int = 3006
    MYSQL_DATABASE: str = "sp-ai-yolo"
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "root"
    MYSQL_CHARSET: str = "utf8mb4"

    class Config:
        env_file = ".env"

    @property
    def resolved_metadata_database_url(self) -> str:
        if self.METADATA_DATABASE_URL:
            if "://" in self.METADATA_DATABASE_URL:
                return self.METADATA_DATABASE_URL
            sqlite_path = Path(self.METADATA_DATABASE_URL)
            if not sqlite_path.is_absolute():
                sqlite_path = self.BASE_DIR / sqlite_path
            return f"sqlite:///{sqlite_path.resolve().as_posix()}"

        sqlite_path = self.SQLITE_DB_PATH
        if not sqlite_path.is_absolute():
            sqlite_path = self.BASE_DIR / sqlite_path
        return f"sqlite:///{sqlite_path.resolve().as_posix()}"

    @property
    def resolved_host_monitor_metrics_file(self) -> Path:
        metrics_path = Path(self.HOST_MONITOR_METRICS_FILE)
        if not metrics_path.is_absolute():
            metrics_path = self.BASE_DIR / metrics_path
        return metrics_path

    @property
    def ALARM_VIDEO_SECONDS(self) -> int:
        before_seconds = max(int(self.ALARM_VIDEO_BEFORE_SECONDS or 0), 0)
        after_seconds = max(int(self.ALARM_VIDEO_AFTER_SECONDS or 0), 0)
        return max(before_seconds + after_seconds, 0)

    def init_directories(self) -> None:
        for dir_path in [
            self.CONFIG_DIR,
            self.DATA_DIR,
            self.UPLOADS_DIR,
            self.DATASETS_DIR,
            self.ANNOTATIONS_DIR,
            self.JOBS_DIR,
            self.DEVICES_DIR,
            self.CHANNELS_DIR,
            self.TASKS_DIR,
            self.MEDIA_NODES_DIR,
            self.EVENTS_DIR,
            self.RECORDINGS_DIR,
            self.SNAPSHOTS_DIR,
            self.CAMERA_HEALTH_SNAPSHOT_DIR,
            self.TRANSCODE_LOGS_DIR,
            self.MODELS_DIR,
            self.REGISTRY_DIR,
            self.INFERENCE_RESULTS_DIR,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)


_load_config_toml_to_environment(Path(__file__).resolve().parent.parent.parent)
settings = Settings()
settings.init_directories()
