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

    METADATA_DB_ENABLED: bool = True
    METADATA_DB_STRICT: bool = False
    ENABLE_TRAINING: bool = False
    METADATA_DATABASE_URL: str = ""
    SQLITE_DB_PATH: Path = DATA_DIR / "metadata.db"

    MYSQL_HOST: str = "mysql"
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: str = "yolo_meta"
    MYSQL_USER: str = "yolo"
    MYSQL_PASSWORD: str = "yolo123456"
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
            self.MEDIA_NODES_DIR,
            self.EVENTS_DIR,
            self.RECORDINGS_DIR,
            self.SNAPSHOTS_DIR,
            self.TRANSCODE_LOGS_DIR,
            self.MODELS_DIR,
            self.REGISTRY_DIR,
            self.INFERENCE_RESULTS_DIR,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)


_load_config_toml_to_environment(Path(__file__).resolve().parent.parent.parent)
settings = Settings()
settings.init_directories()
