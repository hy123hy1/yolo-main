from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


METRIC_PK_TYPE = BigInteger().with_variant(Integer, "sqlite")


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

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    media_node_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(64), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
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
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
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
