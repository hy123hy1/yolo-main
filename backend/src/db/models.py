from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


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
    ai_enabled: Mapped[bool | None] = mapped_column(Boolean, index=True)
    record_mode: Mapped[str | None] = mapped_column(String(64), index=True)
    snapshot_url: Mapped[str | None] = mapped_column(Text)
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


class ChannelAIConfigMeta(Base):
    __tablename__ = "channel_ai_configs"

    channel_id: Mapped[str] = mapped_column(String(64), ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool | None] = mapped_column(Boolean, index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), index=True)
    labels_json: Mapped[list | None] = mapped_column(JSON)
    conf: Mapped[float | None] = mapped_column(Float)
    frame_interval: Mapped[int | None] = mapped_column(Integer)
    zone_ids_json: Mapped[list | None] = mapped_column(JSON)
    save_snapshot: Mapped[bool | None] = mapped_column(Boolean)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class ModelMetaRecord(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    tags_json: Mapped[list | None] = mapped_column(JSON)
    classes_json: Mapped[list | None] = mapped_column(JSON)
    weights_path: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_size_mb: Mapped[float | None] = mapped_column(Float)
    job_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class EventMeta(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event_type: Mapped[str | None] = mapped_column(String(64), index=True)
    class_name: Mapped[str | None] = mapped_column(String(255), index=True)
    conf: Mapped[float | None] = mapped_column(Float)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    video_path: Mapped[str | None] = mapped_column(Text)
    remark: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class RecordingMeta(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    ended_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    duration: Mapped[float | None] = mapped_column(Float)
    file_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
