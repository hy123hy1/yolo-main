from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import BIGINT as MySQLBigInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AlgorithmDefinition(Base):
    __tablename__ = "ai_algorithm_definitions"
    __table_args__ = (
        UniqueConstraint("algorithm_code", name="uk_ai_algorithm_code"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger()
        .with_variant(Integer, "sqlite")
        .with_variant(MySQLBigInteger(unsigned=True), "mysql"),
        primary_key=True,
        autoincrement=True,
    )
    algorithm_code: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm_name: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_code: Mapped[str] = mapped_column(String(64), nullable=False)
    default_interval: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ChannelAlgorithmConfig(Base):
    __tablename__ = "channel_algorithm_configs"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "algorithm_code",
            name="uk_channel_algorithm_config",
        ),
        Index("idx_channel_algorithm_enabled", "channel_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger()
        .with_variant(Integer, "sqlite")
        .with_variant(MySQLBigInteger(unsigned=True), "mysql"),
        primary_key=True,
        autoincrement=True,
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_code: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    analysis_interval: Mapped[float | None] = mapped_column(Float)
    config_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("gb_id", name="uk_channels_gb_id"),
        Index("idx_channels_device_id", "device_id"),
        Index("idx_channels_is_online", "is_online"),
        Index("idx_channels_updated_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    gb_id: Mapped[str] = mapped_column(String(128), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="wvp")
    source_url: Mapped[str | None] = mapped_column(Text)
    flv: Mapped[str | None] = mapped_column(Text)
    stream: Mapped[str | None] = mapped_column(String(255))
    is_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    camera_code: Mapped[str | None] = mapped_column(String(20))
    person_count_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    person_entry_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    person_entry_line_config_json: Mapped[str | None] = mapped_column(Text)
    person_entry_region_config_json: Mapped[str | None] = mapped_column(Text)
    person_analysis_enabled: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        default=False,
    )
    person_analysis_mode: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        default="count_and_entry",
    )
    person_analysis_direction: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        default="outside_to_inside",
    )
    person_analysis_line_config_json: Mapped[str | None] = mapped_column(Text)
    person_analysis_region_config_json: Mapped[str | None] = mapped_column(Text)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class CameraHealthState(Base):
    __tablename__ = "camera_health_states"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "algorithm_type",
            name="uk_camera_health_states_channel_algorithm",
        ),
        Index("idx_camera_health_states_channel", "channel_id"),
        Index("idx_camera_health_states_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    current_score: Mapped[float | None] = mapped_column(Float)
    last_reason: Mapped[str | None] = mapped_column(String(255))
    last_metrics_json: Mapped[str | None] = mapped_column(Text)
    bad_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    recovery_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class CameraHealthEvent(Base):
    __tablename__ = "camera_health_events"
    __table_args__ = (
        Index("idx_camera_health_events_channel", "channel_id"),
        Index("idx_camera_health_events_algorithm", "algorithm_type"),
        Index("idx_camera_health_events_status", "status"),
        Index("idx_camera_health_events_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_type: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_type: Mapped[str] = mapped_column(
        String(2), nullable=False, default="03", server_default="03"
    )
    analysis_case: Mapped[str] = mapped_column(String(4), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    metrics_json: Mapped[str | None] = mapped_column(Text)
    event_path: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PersonCountRecord(Base):
    __tablename__ = "person_count"
    __table_args__ = (
        Index("idx_person_count_channel_created", "channel_id", "created_at"),
        Index("idx_person_count_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger()
        .with_variant(Integer, "sqlite")
        .with_variant(MySQLBigInteger(unsigned=True), "mysql"),
        primary_key=True,
        autoincrement=True,
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_type: Mapped[str] = mapped_column(String(2), nullable=False, default="01")
    analysis_case: Mapped[str] = mapped_column(String(4), nullable=False)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PersonEntryEvent(Base):
    __tablename__ = "person_entry_events"
    __table_args__ = (
        Index("idx_person_entry_events_channel_time", "channel_id", "crossing_timestamp"),
        Index("idx_person_entry_events_direction_time", "direction", "crossing_timestamp"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger()
        .with_variant(Integer, "sqlite")
        .with_variant(MySQLBigInteger(unsigned=True), "mysql"),
        primary_key=True,
        autoincrement=True,
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_type: Mapped[str] = mapped_column(
        String(2), nullable=False, default="01"
    )
    analysis_case: Mapped[str] = mapped_column(
        String(4), nullable=False, default="0000"
    )
    track_id: Mapped[str | None] = mapped_column(String(128))
    line_id: Mapped[str] = mapped_column(String(128), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    bbox_json: Mapped[str | None] = mapped_column(Text)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    crossing_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PersonBehaviorEvent(Base):
    __tablename__ = "person_behavior_events"
    __table_args__ = (
        Index("idx_person_behavior_events_channel_time", "channel_id", "event_timestamp"),
        Index("idx_person_behavior_events_algorithm", "algorithm_code", "event_timestamp"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger()
        .with_variant(Integer, "sqlite")
        .with_variant(MySQLBigInteger(unsigned=True), "mysql"),
        primary_key=True,
        autoincrement=True,
    )
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_code: Mapped[str] = mapped_column(String(32), nullable=False)
    behavior_code: Mapped[str] = mapped_column(String(64), nullable=False)
    track_id: Mapped[str | None] = mapped_column(String(128))
    related_track_ids_json: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    bbox_json: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[str | None] = mapped_column(Text)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
