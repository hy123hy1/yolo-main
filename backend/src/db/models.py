from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


METRIC_PK_TYPE = BigInteger().with_variant(Integer, "sqlite")
CHANNEL_ID_TYPE = String(64).with_variant(
    mysql.VARCHAR(64, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
    "mysql",
)
ELECTRONIC_SEAL_DATETIME_TYPE = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
ELECTRONIC_SEAL_FLOAT_TYPE = Float().with_variant(mysql.DOUBLE, "mysql")


class DeviceMeta(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(64), index=True)
    host: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    password: Mapped[str | None] = mapped_column(String(255))
    remark: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class MediaNodeMeta(Base):
    __tablename__ = "media_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(64), index=True)
    api_base: Mapped[str | None] = mapped_column(String(255))
    play_base: Mapped[str | None] = mapped_column(String(255))
    webrtc_base: Mapped[str | None] = mapped_column(String(255))
    transcode_rtmp_host: Mapped[str | None] = mapped_column(String(255))
    public_rtsp_host: Mapped[str | None] = mapped_column(String(255))
    public_rtmp_host: Mapped[str | None] = mapped_column(String(255))
    public_http_host: Mapped[str | None] = mapped_column(String(255))
    rtsp_port: Mapped[int | None] = mapped_column(Integer)
    rtmp_port: Mapped[int | None] = mapped_column(Integer)
    http_port: Mapped[int | None] = mapped_column(Integer)
    https_port: Mapped[int | None] = mapped_column(Integer)
    hls_file: Mapped[str | None] = mapped_column(String(255))
    httpflv_suffix: Mapped[str | None] = mapped_column(String(255))
    webrtc_path: Mapped[str | None] = mapped_column(String(255))
    secret: Mapped[str | None] = mapped_column(String(255))
    is_default: Mapped[bool | None] = mapped_column(Boolean, index=True)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class ChannelMeta(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(CHANNEL_ID_TYPE, primary_key=True)
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    gb_id: Mapped[str | None] = mapped_column(String(128), index=True)
    media_node_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(64), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    flv: Mapped[str | None] = mapped_column(Text)
    app: Mapped[str | None] = mapped_column(String(255), index=True)
    stream: Mapped[str | None] = mapped_column(String(255), index=True)
    is_online: Mapped[bool | None] = mapped_column(Boolean, index=True)
    is_playing: Mapped[bool | None] = mapped_column(Boolean, index=True)
    record_mode: Mapped[str | None] = mapped_column(String(64), index=True)
    snapshot_url: Mapped[str | None] = mapped_column(Text)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    webrtc_api: Mapped[str | None] = mapped_column(Text)
    hls: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class ElectronicSealChannelMeta(Base):
    __tablename__ = "electronic_seal_channels"
    __table_args__ = (
        UniqueConstraint("stream", name="uq_electronic_seal_channels_stream"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(CHANNEL_ID_TYPE, primary_key=True)
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    gb_id: Mapped[str | None] = mapped_column(String(128), index=True)
    media_node_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(64), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    flv: Mapped[str | None] = mapped_column(Text)
    app: Mapped[str | None] = mapped_column(String(255), index=True)
    stream: Mapped[str | None] = mapped_column(String(255), index=True)
    is_online: Mapped[bool | None] = mapped_column(Boolean, index=True)
    is_playing: Mapped[bool | None] = mapped_column(Boolean, index=True)
    record_mode: Mapped[str | None] = mapped_column(String(64), index=True)
    snapshot_url: Mapped[str | None] = mapped_column(Text)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    webrtc_api: Mapped[str | None] = mapped_column(Text)
    hls: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class ElectronicSealChannelOnlineHistoryMeta(Base):
    """Hourly online-state samples for electronic-seal-owned channels."""

    __tablename__ = "electronic_seal_channel_online_history"
    __table_args__ = (
        Index(
            "idx_electronic_seal_online_history_create_time",
            "create_time",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(CHANNEL_ID_TYPE, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_online: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
    create_time: Mapped[DateTime] = mapped_column(
        ELECTRONIC_SEAL_DATETIME_TYPE,
        primary_key=True,
    )


class ElectronicSealChannelProfileMeta(Base):
    """Persistent ownership and role for an electronic-seal channel."""

    __tablename__ = "electronic_seal_channel_profiles"
    __table_args__ = (
        Index("idx_electronic_seal_profile_role", "role"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    channel_id: Mapped[str] = mapped_column(
        CHANNEL_ID_TYPE,
        ForeignKey("electronic_seal_channels.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(64))
    bound: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"), index=True)
    runtime_owner: Mapped[str | None] = mapped_column(String(32), index=True)
    runtime_owner_id: Mapped[str | None] = mapped_column(String(128))
    runtime_lease_until: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE, index=True)
    profile_version: Mapped[str] = mapped_column(
        String(64),
        default="role-v1",
        server_default=text("'role-v1'"),
    )
    initialized_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    created_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ChannelZoneMeta(Base):
    __tablename__ = "channel_zones"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    color: Mapped[str | None] = mapped_column(String(32))
    labels_json: Mapped[list | None] = mapped_column(JSON)
    coordinates_json: Mapped[list | None] = mapped_column(JSON)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class TaskMeta(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(64), index=True)
    channel_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("channels.id", ondelete="CASCADE"),
        index=True,
    )
    channel_name: Mapped[str | None] = mapped_column(String(255), index=True)
    frame_interval: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    algorithm_count: Mapped[int | None] = mapped_column(Integer)
    region_count: Mapped[int | None] = mapped_column(Integer)
    is_running: Mapped[bool | None] = mapped_column(Boolean, index=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    stopped_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    last_analysis_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    last_event_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class TaskAlgorithmMeta(Base):
    __tablename__ = "task_algorithms"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    algorithm_type: Mapped[str | None] = mapped_column(String(64), index=True)
    scene_key: Mapped[str | None] = mapped_column(String(128), index=True)
    scene_name: Mapped[str | None] = mapped_column(String(255))
    rule_type: Mapped[str | None] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("models.id", ondelete="RESTRICT"),
        index=True,
    )
    enabled: Mapped[bool | None] = mapped_column(Boolean, index=True)
    labels_json: Mapped[list | None] = mapped_column(JSON)
    detect_all_classes: Mapped[bool | None] = mapped_column(Boolean)
    label_source: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    alert_interval: Mapped[int | None] = mapped_column(Integer)
    need_snapshot: Mapped[bool | None] = mapped_column(Boolean)
    need_video_clip: Mapped[bool | None] = mapped_column(Boolean)
    video_before_seconds: Mapped[int | None] = mapped_column(Integer)
    video_after_seconds: Mapped[int | None] = mapped_column(Integer)
    need_overlay: Mapped[bool | None] = mapped_column(Boolean, index=True)
    requires_region: Mapped[bool | None] = mapped_column(Boolean)
    overlay_color: Mapped[str | None] = mapped_column(String(32))
    postprocess_config_json: Mapped[dict | None] = mapped_column(JSON)
    sort_order: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class AlgorithmRegionMeta(Base):
    __tablename__ = "algorithm_regions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    algorithm_id: Mapped[str] = mapped_column(String(64), ForeignKey("task_algorithms.id", ondelete="CASCADE"), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(64), index=True)
    color: Mapped[str | None] = mapped_column(String(32))
    coordinates_json: Mapped[list | None] = mapped_column(JSON)
    enabled: Mapped[bool | None] = mapped_column(Boolean, index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class TaskRegionMeta(Base):
    __tablename__ = "task_regions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(64), index=True)
    color: Mapped[str | None] = mapped_column(String(32))
    coordinates_json: Mapped[list | None] = mapped_column(JSON)
    enabled: Mapped[bool | None] = mapped_column(Boolean, index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class TaskAlgorithmRegionMeta(Base):
    __tablename__ = "task_algorithm_regions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    algorithm_id: Mapped[str] = mapped_column(String(64), ForeignKey("task_algorithms.id", ondelete="CASCADE"), index=True)
    region_id: Mapped[str] = mapped_column(String(64), ForeignKey("task_regions.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)


class TaskAlgorithmMetricMeta(Base):
    __tablename__ = "task_algorithm_metrics"
    __table_args__ = (
        UniqueConstraint("task_id", "algorithm_id", "metric_type", "scope_key", name="uk_task_algorithm_metrics_scope"),
    )

    id: Mapped[int] = mapped_column(METRIC_PK_TYPE, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    algorithm_id: Mapped[str] = mapped_column(String(64), ForeignKey("task_algorithms.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), index=True)
    metric_type: Mapped[str | None] = mapped_column(String(64), index=True)
    metric_value: Mapped[float | None] = mapped_column(Float)
    region_id: Mapped[str | None] = mapped_column(String(64), index=True)
    region_name: Mapped[str | None] = mapped_column(String(255))
    scope_key: Mapped[str] = mapped_column(String(64), index=True, default="global")
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class TaskAlgorithmMetricHistoryMeta(Base):
    __tablename__ = "task_algorithm_metric_history"

    id: Mapped[int] = mapped_column(METRIC_PK_TYPE, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    algorithm_id: Mapped[str] = mapped_column(String(64), index=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), index=True)
    metric_type: Mapped[str | None] = mapped_column(String(64), index=True)
    metric_value: Mapped[float | None] = mapped_column(Float)
    region_id: Mapped[str | None] = mapped_column(String(64), index=True)
    region_name: Mapped[str | None] = mapped_column(String(255))
    sampled_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class CameraHealthConfigMeta(Base):
    __tablename__ = "camera_health_configs"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "algorithm_type",
            name="uk_camera_health_config_channel_algorithm",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        CHANNEL_ID_TYPE,
        ForeignKey("channels.id", ondelete="CASCADE"),
        index=True,
    )
    algorithm_type: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sample_interval_ms: Mapped[int] = mapped_column(Integer, default=1000)
    warmup_seconds: Mapped[int] = mapped_column(Integer, default=30)
    alarm_hold_seconds: Mapped[int] = mapped_column(Integer, default=10)
    recovery_hold_seconds: Mapped[int] = mapped_column(Integer, default=15)
    alert_interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    need_snapshot: Mapped[bool] = mapped_column(Boolean, default=True)
    need_video_clip: Mapped[bool] = mapped_column(Boolean, default=False)
    threshold_config_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[DateTime] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(), index=True)


class CameraHealthBaselineMeta(Base):
    __tablename__ = "camera_health_baselines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        CHANNEL_ID_TYPE,
        ForeignKey("channels.id", ondelete="CASCADE"),
        index=True,
    )
    algorithm_type: Mapped[str] = mapped_column(String(64), index=True)
    image_path: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    quality_score: Mapped[float | None] = mapped_column(Float)
    calibration_params_json: Mapped[dict | None] = mapped_column(JSON)
    captured_at: Mapped[DateTime] = mapped_column(DateTime(), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class CameraHealthStateMeta(Base):
    __tablename__ = "camera_health_states"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "algorithm_type",
            name="uk_camera_health_state_channel_algorithm",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        CHANNEL_ID_TYPE,
        ForeignKey("channels.id", ondelete="CASCADE"),
        index=True,
    )
    algorithm_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_score: Mapped[float | None] = mapped_column(Float)
    last_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    bad_since: Mapped[DateTime | None] = mapped_column(DateTime())
    last_good_at: Mapped[DateTime | None] = mapped_column(DateTime())
    last_event_id: Mapped[str | None] = mapped_column(String(64), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(), index=True)


class CameraHealthEventMeta(Base):
    __tablename__ = "camera_health_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        CHANNEL_ID_TYPE,
        ForeignKey("channels.id", ondelete="CASCADE"),
        index=True,
    )
    algorithm_type: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    started_at: Mapped[DateTime] = mapped_column(DateTime(), index=True)
    ended_at: Mapped[DateTime | None] = mapped_column(DateTime())
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    video_path: Mapped[str | None] = mapped_column(Text)
    handling_status: Mapped[int] = mapped_column(Integer, default=0, index=True)
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(), index=True)


class ModelMetaRecord(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    tags_json: Mapped[list | None] = mapped_column(JSON)
    classes_json: Mapped[list | None] = mapped_column(JSON)
    weights_path: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_size_mb: Mapped[float | None] = mapped_column(Float)
    job_id: Mapped[str | None] = mapped_column(String(64), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    alert_interval: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class ElectronicSealModelMetaRecord(Base):
    __tablename__ = "electronic_seal_models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    tags_json: Mapped[list | None] = mapped_column(JSON)
    classes_json: Mapped[list | None] = mapped_column(JSON)
    weights_path: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_size_mb: Mapped[float | None] = mapped_column(Float)
    job_id: Mapped[str | None] = mapped_column(String(64), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    alert_interval: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class EventMeta(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    algorithm_id: Mapped[str | None] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event_type: Mapped[str | None] = mapped_column(String(64), index=True)
    class_name: Mapped[str | None] = mapped_column(String(255), index=True)
    conf: Mapped[float | None] = mapped_column(Float)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    video_path: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    handling_status: Mapped[int | None] = mapped_column(Integer, index=True)
    remark: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class RecordingMeta(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    algorithm_id: Mapped[str | None] = mapped_column(String(64), index=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    ended_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    duration: Mapped[float | None] = mapped_column(Float)
    file_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class ElectronicSealConfigMeta(Base):
    __tablename__ = "electronic_seal_configs"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "algorithm_type",
            name="uk_electronic_seal_config_channel_algorithm",
        ),
        Index("idx_electronic_seal_config_channel", "channel_id"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        CHANNEL_ID_TYPE,
        ForeignKey("electronic_seal_channels.id", ondelete="CASCADE"),
    )
    algorithm_type: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("electronic_seal_models.id", ondelete="RESTRICT"),
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    sample_interval_ms: Mapped[int] = mapped_column(Integer, default=1000, server_default=text("1000"))
    camera_health_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
    )
    alarm_hold_seconds: Mapped[int] = mapped_column(Integer, default=10, server_default=text("10"))
    recovery_hold_seconds: Mapped[int] = mapped_column(Integer, default=15, server_default=text("15"))
    need_snapshot: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    need_video_clip: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    line_config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    region_config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    threshold_config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ElectronicSealBaselineMeta(Base):
    __tablename__ = "electronic_seal_baselines"
    __table_args__ = (
        Index(
            "idx_electronic_seal_baseline_lookup",
            "channel_id",
            "algorithm_type",
            "status",
            "captured_at",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        CHANNEL_ID_TYPE,
        ForeignKey("electronic_seal_channels.id", ondelete="CASCADE"),
    )
    algorithm_type: Mapped[str] = mapped_column(String(64))
    image_path: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    quality_score: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    calibration_params_json: Mapped[dict | None] = mapped_column(JSON)
    captured_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(32), default="active", server_default=text("'active'"))


class ElectronicSealStateMeta(Base):
    __tablename__ = "electronic_seal_states"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "algorithm_type",
            name="uk_electronic_seal_state_channel_algorithm",
        ),
        Index("idx_electronic_seal_state_status", "status"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        CHANNEL_ID_TYPE,
        ForeignKey("electronic_seal_channels.id", ondelete="CASCADE"),
    )
    algorithm_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    current_score: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    last_metrics_json: Mapped[dict | None] = mapped_column(JSON)
    runtime_json: Mapped[dict | None] = mapped_column(JSON)
    last_run_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    bad_since: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    last_good_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    last_event_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ElectronicSealEventMeta(Base):
    __tablename__ = "electronic_seal_events"
    __table_args__ = (
        Index("idx_electronic_seal_event_channel_time", "channel_id", "created_at"),
        Index("idx_electronic_seal_event_algorithm_time", "algorithm_type", "created_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(CHANNEL_ID_TYPE)
    algorithm_type: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    started_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    ended_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    duration_seconds: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    score: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    evidence_snapshot_path: Mapped[str | None] = mapped_column(Text)
    evidence_video_path: Mapped[str | None] = mapped_column(Text)
    handling_status: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(32), default="new", server_default=text("'new'"))
    created_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ElectronicSealEventIndexMeta(Base):
    """Common index for electronic-seal events exposed to integrations."""

    __tablename__ = "electronic_seal_event_index"
    __table_args__ = (
        Index("idx_electronic_seal_event_index_channel_time", "channel_id", "created_at", "id"),
        Index("idx_electronic_seal_event_index_algorithm_time", "algorithm_type", "created_at", "id"),
        Index("idx_electronic_seal_event_index_upload_status", "upload_status", "detected_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(CHANNEL_ID_TYPE)
    algorithm_type: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    detail_table: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    detected_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    ended_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    uploaded_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    upload_status: Mapped[str] = mapped_column(String(32), default="not_required", server_default=text("'not_required'"))
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    score: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    handling_status: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(32), default="new", server_default=text("'new'"))
    created_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ElectronicSealTruckDepartureEventMeta(Base):
    __tablename__ = "electronic_seal_truck_departure_events"
    __table_args__ = (
        Index("idx_electronic_seal_truck_departure_channel_time", "channel_id", "created_at"),
        Index("idx_electronic_seal_truck_departure_direction_time", "direction", "crossing_timestamp"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("electronic_seal_event_index.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[str] = mapped_column(CHANNEL_ID_TYPE, index=True)
    event_type: Mapped[str] = mapped_column(String(64), default="truck_departure", server_default=text("'truck_departure'"))
    direction: Mapped[str] = mapped_column(String(64))
    track_id: Mapped[str | None] = mapped_column(String(128), index=True)
    vehicle_label: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    line_id: Mapped[str | None] = mapped_column(String(128))
    crossing_timestamp: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE, index=True)
    bbox_json: Mapped[list | None] = mapped_column(JSON)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    ended_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    score: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(32), default="new", server_default=text("'new'"))
    handling_status: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    evidence_snapshot_path: Mapped[str | None] = mapped_column(Text)
    evidence_video_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ElectronicSealRoomEmptyEventMeta(Base):
    __tablename__ = "electronic_seal_room_empty_events"
    __table_args__ = (
        Index("idx_electronic_seal_room_empty_channel_time", "channel_id", "created_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("electronic_seal_event_index.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[str] = mapped_column(CHANNEL_ID_TYPE, index=True)
    event_type: Mapped[str] = mapped_column(String(64), default="room_empty", server_default=text("'room_empty'"))
    person_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    empty_seconds: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    ended_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    score: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    severity: Mapped[str] = mapped_column(String(16), default="alarm", server_default=text("'alarm'"))
    status: Mapped[str] = mapped_column(String(32), default="new", server_default=text("'new'"))
    handling_status: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    evidence_snapshot_path: Mapped[str | None] = mapped_column(Text)
    evidence_video_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ElectronicSealCameraOcclusionEventMeta(Base):
    __tablename__ = "electronic_seal_camera_occlusion_events"
    __table_args__ = (
        Index("idx_electronic_seal_camera_occlusion_channel_time", "channel_id", "created_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("electronic_seal_event_index.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[str] = mapped_column(CHANNEL_ID_TYPE, index=True)
    event_type: Mapped[str] = mapped_column(String(64), default="camera_occlusion", server_default=text("'camera_occlusion'"))
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    ended_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    score: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    severity: Mapped[str] = mapped_column(String(16), default="alarm", server_default=text("'alarm'"))
    status: Mapped[str] = mapped_column(String(32), default="new", server_default=text("'new'"))
    handling_status: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    evidence_snapshot_path: Mapped[str | None] = mapped_column(Text)
    evidence_video_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ElectronicSealCameraViewShiftEventMeta(Base):
    __tablename__ = "electronic_seal_camera_view_shift_events"
    __table_args__ = (
        Index("idx_electronic_seal_camera_view_shift_channel_time", "channel_id", "created_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("electronic_seal_event_index.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[str] = mapped_column(CHANNEL_ID_TYPE, index=True)
    event_type: Mapped[str] = mapped_column(String(64), default="camera_view_shift", server_default=text("'camera_view_shift'"))
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    ended_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    score: Mapped[float | None] = mapped_column(ELECTRONIC_SEAL_FLOAT_TYPE)
    severity: Mapped[str] = mapped_column(String(16), default="alarm", server_default=text("'alarm'"))
    status: Mapped[str] = mapped_column(String(32), default="new", server_default=text("'new'"))
    handling_status: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    evidence_snapshot_path: Mapped[str | None] = mapped_column(Text)
    evidence_video_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ElectronicSealTruckCountMeta(Base):
    __tablename__ = "electronic_seal_truck_counts"
    __table_args__ = {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_0900_ai_ci",
    }

    channel_id: Mapped[str] = mapped_column(
        CHANNEL_ID_TYPE,
        ForeignKey("electronic_seal_channels.id", ondelete="CASCADE"),
        primary_key=True,
    )
    out_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    last_event_id: Mapped[str | None] = mapped_column(String(64))
    last_event_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)


class ElectronicSealUploadJobMeta(Base):
    __tablename__ = "electronic_seal_upload_jobs"
    __table_args__ = (
        UniqueConstraint("event_id", "target", name="uk_electronic_seal_upload_event_target"),
        Index("idx_electronic_seal_upload_status_retry", "status", "next_retry_at"),
        Index("idx_electronic_seal_upload_claim_token", "claim_token"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("electronic_seal_event_index.id", ondelete="CASCADE"),
    )
    target: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    next_retry_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    claimed_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    lease_expires_at: Mapped[DateTime | None] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    claim_token: Mapped[str | None] = mapped_column(String(128))
    remote_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
    updated_at: Mapped[DateTime] = mapped_column(ELECTRONIC_SEAL_DATETIME_TYPE)
