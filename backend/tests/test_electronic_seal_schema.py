from pathlib import Path

from sqlalchemy import UniqueConstraint, create_engine, event, inspect
from sqlalchemy.dialects import mysql

from src.db.models import (
    Base,
    ChannelMeta,
    ElectronicSealChannelProfileMeta,
    ElectronicSealBaselineMeta,
    ElectronicSealConfigMeta,
    ElectronicSealEventMeta,
    ElectronicSealModelMetaRecord,
    ElectronicSealStateMeta,
    ElectronicSealUploadJobMeta,
    ModelMetaRecord,
)
from src.electronic_seal.store import ensure_electronic_seal_schema


def _sqlite_engine(url="sqlite://", **kwargs):
    engine = create_engine(url, future=True, **kwargs)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


def test_channel_id_columns_compile_with_the_mysql_channel_collation():
    expected = "VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
    columns = [
        ChannelMeta.__table__.c.id,
        ElectronicSealConfigMeta.__table__.c.channel_id,
        ElectronicSealBaselineMeta.__table__.c.channel_id,
        ElectronicSealStateMeta.__table__.c.channel_id,
        ElectronicSealEventMeta.__table__.c.channel_id,
        ElectronicSealChannelProfileMeta.__table__.c.channel_id,
    ]

    assert {str(column.type.compile(dialect=mysql.dialect())) for column in columns} == {expected}


def test_electronic_seal_float_columns_compile_as_mysql_double():
    float_columns = [
        (ElectronicSealBaselineMeta, "quality_score"),
        (ElectronicSealStateMeta, "current_score"),
        (ElectronicSealEventMeta, "duration_seconds"),
        (ElectronicSealEventMeta, "score"),
    ]

    for model, column_name in float_columns:
        assert str(model.__table__.c[column_name].type.compile(dialect=mysql.dialect())) == "DOUBLE"


def test_electronic_seal_scalar_server_defaults_match_mysql_migration():
    expected_defaults = {
        ElectronicSealConfigMeta: {
            "enabled": "0",
            "sample_interval_ms": "1000",
                "camera_health_interval_seconds": "1",
            "alarm_hold_seconds": "10",
            "recovery_hold_seconds": "15",
            "need_snapshot": "1",
            "need_video_clip": "0",
        },
        ElectronicSealBaselineMeta: {
            "version": "1",
            "status": "'active'",
        },
        ElectronicSealEventMeta: {
            "severity": "'warning'",
            "handling_status": "0",
            "status": "'new'",
        },
        ElectronicSealUploadJobMeta: {
            "status": "'pending'",
            "attempts": "0",
        },
    }

    for model, columns in expected_defaults.items():
        for column_name, expected in columns.items():
            server_default = model.__table__.c[column_name].server_default
            assert server_default is not None
            assert str(server_default.arg) == expected


def test_electronic_seal_models_define_only_the_five_prefixed_tables():
    expected_tables = {
        "electronic_seal_configs",
        "electronic_seal_baselines",
        "electronic_seal_states",
        "electronic_seal_events",
        "electronic_seal_upload_jobs",
    }

    assert expected_tables.issubset(Base.metadata.tables)
    assert all(table.startswith("electronic_seal_") for table in expected_tables)


def test_electronic_seal_model_table_matches_models_columns():
    assert set(ElectronicSealModelMetaRecord.__table__.columns.keys()) == set(
        ModelMetaRecord.__table__.columns.keys()
    )


def test_electronic_seal_config_model_id_targets_isolated_table():
    foreign_keys = list(ElectronicSealConfigMeta.__table__.c.model_id.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "electronic_seal_models.id"

    config_table = ElectronicSealConfigMeta.__table__
    assert {
        "channel_id",
        "algorithm_type",
        "model_id",
        "camera_health_interval_seconds",
        "line_config_json",
        "region_config_json",
        "threshold_config_json",
    }.issubset(config_table.columns.keys())
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uk_electronic_seal_config_channel_algorithm"
        for constraint in config_table.constraints
    )

    state_table = ElectronicSealStateMeta.__table__
    assert {"last_metrics_json", "runtime_json", "last_run_at"}.issubset(state_table.columns.keys())
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uk_electronic_seal_state_channel_algorithm"
        for constraint in state_table.constraints
    )

    event_table = ElectronicSealEventMeta.__table__
    assert {"evidence_snapshot_path", "evidence_video_path"}.issubset(event_table.columns.keys())
    assert {"idx_electronic_seal_event_channel_time", "idx_electronic_seal_event_algorithm_time"}.issubset(
        {index.name for index in event_table.indexes}
    )

    upload_job_table = ElectronicSealUploadJobMeta.__table__
    assert {"claimed_at", "lease_expires_at", "claim_token", "payload_json"}.issubset(upload_job_table.columns.keys())
    assert "idx_electronic_seal_upload_claim_token" in {index.name for index in upload_job_table.indexes}
    assert str(upload_job_table.c.claimed_at.type.compile(dialect=mysql.dialect())) == "DATETIME(6)"
    assert str(upload_job_table.c.lease_expires_at.type.compile(dialect=mysql.dialect())) == "DATETIME(6)"
    assert str(upload_job_table.c.claim_token.type.compile(dialect=mysql.dialect())) == "VARCHAR(128)"


def test_electronic_seal_models_match_mysql_table_options_and_named_indexes():
    tables = {
        ElectronicSealConfigMeta.__table__,
        ElectronicSealBaselineMeta.__table__,
        ElectronicSealStateMeta.__table__,
        ElectronicSealEventMeta.__table__,
        ElectronicSealUploadJobMeta.__table__,
    }
    expected_indexes = {
        "electronic_seal_configs": {
            "idx_electronic_seal_config_channel": ("channel_id",),
            "ix_electronic_seal_configs_model_id": ("model_id",),
        },
        "electronic_seal_baselines": {
            "idx_electronic_seal_baseline_lookup": (
                "channel_id",
                "algorithm_type",
                "status",
                "captured_at",
            ),
        },
        "electronic_seal_states": {
            "idx_electronic_seal_state_status": ("status",),
        },
        "electronic_seal_events": {
            "idx_electronic_seal_event_channel_time": ("channel_id", "created_at"),
            "idx_electronic_seal_event_algorithm_time": ("algorithm_type", "created_at"),
        },
        "electronic_seal_upload_jobs": {
            "idx_electronic_seal_upload_status_retry": ("status", "next_retry_at"),
            "idx_electronic_seal_upload_claim_token": ("claim_token",),
        },
    }

    for table in tables:
        assert table.dialect_options["mysql"]["engine"] == "InnoDB"
        assert table.dialect_options["mysql"]["charset"] == "utf8mb4"
        assert table.dialect_options["mysql"]["collate"] == "utf8mb4_0900_ai_ci"

        actual_indexes = {
            index.name: tuple(column.name for column in index.columns)
            for index in table.indexes
        }
        assert actual_indexes == expected_indexes[table.name]


def test_electronic_seal_model_datetime_columns_use_mysql_microsecond_precision():
    datetime_columns = {
        ElectronicSealConfigMeta: {"created_at", "updated_at"},
        ElectronicSealBaselineMeta: {"captured_at"},
        ElectronicSealStateMeta: {"last_run_at", "bad_since", "last_good_at", "updated_at"},
        ElectronicSealEventMeta: {"started_at", "ended_at", "created_at", "updated_at"},
        ElectronicSealUploadJobMeta: {
            "next_retry_at",
            "claimed_at",
            "lease_expires_at",
            "created_at",
            "updated_at",
        },
    }

    for model, column_names in datetime_columns.items():
        for column_name in column_names:
            column = model.__table__.c[column_name]
            assert str(column.type.compile(dialect=mysql.dialect())) == "DATETIME(6)"


def test_electronic_seal_migration_declares_all_datetime_columns_with_microseconds():
    migration = Path(__file__).parents[1] / "migrations" / "20260721_electronic_seal.sql"
    lower_text = migration.read_text(encoding="utf-8").lower()
    datetime_columns = {
        "electronic_seal_configs": {"created_at", "updated_at"},
        "electronic_seal_baselines": {"captured_at"},
        "electronic_seal_states": {"last_run_at", "bad_since", "last_good_at", "updated_at"},
        "electronic_seal_events": {"started_at", "ended_at", "created_at", "updated_at"},
        "electronic_seal_upload_jobs": {
            "next_retry_at",
            "claimed_at",
            "lease_expires_at",
            "created_at",
            "updated_at",
        },
    }

    for table_name, column_names in datetime_columns.items():
        table_start = lower_text.index(f"create table if not exists {table_name}")
        table_end = lower_text.find("create table if not exists ", table_start + 1)
        table_text = lower_text[table_start:] if table_end == -1 else lower_text[table_start:table_end]
        for column_name in column_names:
            assert f"{column_name} datetime(6)" in table_text


def test_electronic_seal_config_migration_exposes_optional_model_id():
    migration = Path(__file__).parents[1] / "migrations" / "20260721_electronic_seal.sql"
    lower_text = migration.read_text(encoding="utf-8").lower()
    table_start = lower_text.index("create table if not exists electronic_seal_configs")
    table_end = lower_text.find("create table if not exists ", table_start + 1)
    table_text = lower_text[table_start:] if table_end == -1 else lower_text[table_start:table_end]
    assert "model_id varchar(64) null" in table_text


def test_electronic_seal_model_id_patch_is_rerunnable_and_guarded():
    migration = Path(__file__).parents[1] / "migrations" / "20260722_electronic_seal_model_id.sql"
    lower_text = migration.read_text(encoding="utf-8").lower()
    assert "information_schema.columns" in lower_text
    assert "add column model_id varchar(64) null" in lower_text
    assert "add key ix_electronic_seal_configs_model_id" in lower_text


def test_electronic_seal_schema_helper_creates_five_tables_without_metadata_sync():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine, tables=[ChannelMeta.__table__])

    ensure_electronic_seal_schema(engine)

    assert {
        "electronic_seal_configs",
        "electronic_seal_models",
        "electronic_seal_baselines",
        "electronic_seal_states",
        "electronic_seal_events",
        "electronic_seal_upload_jobs",
    }.issubset(set(inspect(engine).get_table_names()))
    assert "tasks" not in set(inspect(engine).get_table_names())
    engine.dispose()


def test_schema_helper_adds_model_id_to_an_existing_legacy_config_table():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine, tables=[ChannelMeta.__table__])
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE electronic_seal_configs (
                id VARCHAR(64) NOT NULL PRIMARY KEY,
                channel_id VARCHAR(64) NOT NULL,
                algorithm_type VARCHAR(64) NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT 0,
                sample_interval_ms INTEGER NOT NULL DEFAULT 1000,
                camera_health_interval_seconds INTEGER NOT NULL DEFAULT 1,
                alarm_hold_seconds INTEGER NOT NULL DEFAULT 10,
                recovery_hold_seconds INTEGER NOT NULL DEFAULT 15,
                need_snapshot BOOLEAN NOT NULL DEFAULT 1,
                need_video_clip BOOLEAN NOT NULL DEFAULT 0,
                line_config_json JSON NOT NULL,
                region_config_json JSON NOT NULL,
                threshold_config_json JSON NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

    ensure_electronic_seal_schema(engine)

    columns = {item["name"] for item in inspect(engine).get_columns("electronic_seal_configs")}
    indexes = {item["name"] for item in inspect(engine).get_indexes("electronic_seal_configs")}
    assert "model_id" in columns
    assert "ix_electronic_seal_configs_model_id" in indexes
    engine.dispose()


def test_sqlite_schema_enforces_config_and_state_uniqueness():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine, tables=[ChannelMeta.__table__])
    ensure_electronic_seal_schema(engine)

    config_table = ElectronicSealConfigMeta.__table__
    state_table = ElectronicSealStateMeta.__table__
    assert any(constraint.name == "uk_electronic_seal_config_channel_algorithm" for constraint in config_table.constraints)
    assert any(constraint.name == "uk_electronic_seal_state_channel_algorithm" for constraint in state_table.constraints)
    engine.dispose()


def test_mysql_migration_is_repeatable_isolated_and_uses_channel_compatible_collation():
    migration = Path(__file__).parents[1] / "migrations" / "20260721_electronic_seal.sql"
    text = migration.read_text(encoding="utf-8")
    lower_text = text.lower()

    assert text.count("CREATE TABLE IF NOT EXISTS") == 5
    assert lower_text.count("engine=innodb") == 5
    assert lower_text.count("charset=utf8mb4") == 5
    assert lower_text.count("engine=innodb default charset=utf8mb4 collate=utf8mb4_0900_ai_ci") == 5
    assert "task_id" not in lower_text
    assert "algorithm_id" not in lower_text
    assert "model_id" in lower_text
    assert lower_text.count("foreign key (channel_id) references channels (id)") == 4
    assert "foreign key (event_id) references electronic_seal_events (id) on delete cascade" in lower_text
    assert "evidence_snapshot_path" in lower_text
    assert "evidence_video_path" in lower_text
    assert "claimed_at datetime(6) null" in lower_text
    assert "lease_expires_at datetime(6) null" in lower_text
    assert "claim_token varchar(128) null" in lower_text
    assert "payload_json json null" in lower_text
    assert "idx_electronic_seal_upload_claim_token" in lower_text
