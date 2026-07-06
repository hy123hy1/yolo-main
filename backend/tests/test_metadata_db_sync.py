import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.db.models import (
    AlgorithmRegionMeta,
    Base,
    ChannelAIConfigMeta,
    ChannelMeta,
    ModelMetaRecord,
    TaskAlgorithmMeta,
    TaskMeta,
)
from src.services import metadata_db_sync


class MetadataDbSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.channels_dir = Path(self.temp_dir.name) / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path = Path(self.temp_dir.name) / "metadata.db"

        self.original_channels_dir = settings.CHANNELS_DIR
        settings.CHANNELS_DIR = self.channels_dir

        self.original_engine = metadata_db_sync.engine
        self.original_session_local = metadata_db_sync.SessionLocal

        self.engine = create_engine(
            f"sqlite:///{self.sqlite_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(self.engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.session_local = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        metadata_db_sync.engine = self.engine
        metadata_db_sync.SessionLocal = self.session_local
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()
        metadata_db_sync.engine = self.original_engine
        metadata_db_sync.SessionLocal = self.original_session_local
        settings.CHANNELS_DIR = self.original_channels_dir
        self.temp_dir.cleanup()

    def test_sync_channel_persists_parent_before_ai_config_children(self):
        channel_payload = {
            "id": "ch_sync_parent_first",
            "name": "sync-parent-first",
            "type": "rtsp",
            "source_url": "rtsp://example/live/channel",
            "app": "live",
            "stream": "channel",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": True,
            "record_mode": "none",
            "snapshot_url": None,
            "snapshot_path": "G:/data/snapshots/ch_sync_parent_first.jpg",
            "created_at": "2026-06-06T10:00:00",
            "updated_at": "2026-06-06T10:00:00",
            "ext": {
                "zones": [
                    {
                        "id": "zone_sync_parent_first",
                        "name": "entry",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
                "ai_config": {
                    "enabled": True,
                    "model_id": "model_person",
                    "labels": ["person"],
                    "conf": 0.3,
                    "frame_interval": 5,
                    "zone_ids": ["zone_sync_parent_first"],
                    "save_snapshot": True,
                },
            },
        }

        channel_path = self.channels_dir / f"{channel_payload['id']}.json"

        metadata_db_sync.sync_json_document(channel_path, channel_payload)

        with self.session_local() as session:
            channel_row = session.execute(
                select(ChannelMeta).where(ChannelMeta.id == channel_payload["id"])
            ).scalar_one()
            config_row = session.execute(
                select(ChannelAIConfigMeta).where(ChannelAIConfigMeta.channel_id == channel_payload["id"])
            ).scalar_one()

        self.assertEqual(channel_row.name, "sync-parent-first")
        self.assertEqual(channel_row.snapshot_path, "G:/data/snapshots/ch_sync_parent_first.jpg")
        self.assertEqual(config_row.model_id, "model_person")
        self.assertEqual(config_row.labels_json, ["person"])

    def test_sync_all_metadata_from_files_does_not_resurrect_channels_when_db_has_rows(self):
        stale_payload = {
            "id": "ch_stale_file_only",
            "name": "stale-file-only",
            "type": "rtsp",
            "source_url": "rtsp://example/live/stale-file-only",
            "app": "live",
            "stream": "stale_file_only",
            "is_online": True,
            "is_playing": True,
            "ai_enabled": False,
            "record_mode": "none",
            "created_at": "2026-06-16T10:00:00",
            "updated_at": "2026-06-16T10:00:00",
            "ext": {},
        }
        (self.channels_dir / f"{stale_payload['id']}.json").write_text(
            json.dumps(stale_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id="ch_existing_db",
                    name="existing-db",
                    type="rtsp",
                    source_url="rtsp://example/live/existing-db",
                    app="live",
                    stream="existing_db",
                    is_online=False,
                    is_playing=False,
                    ai_enabled=False,
                    record_mode="none",
                    raw_json={
                        "id": "ch_existing_db",
                        "name": "existing-db",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/existing-db",
                        "app": "live",
                        "stream": "existing_db",
                        "is_online": False,
                        "is_playing": False,
                        "ai_enabled": False,
                        "record_mode": "none",
                        "ext": {},
                    },
                )
            )
            session.commit()

        metadata_db_sync.sync_all_metadata_from_files()

        with self.session_local() as session:
            stale_row = session.execute(
                select(ChannelMeta).where(ChannelMeta.id == stale_payload["id"])
            ).scalar_one_or_none()
            existing_row = session.execute(
                select(ChannelMeta).where(ChannelMeta.id == "ch_existing_db")
            ).scalar_one_or_none()

        self.assertIsNone(stale_row)
        self.assertIsNotNone(existing_row)

    def test_sync_task_json_document_is_ignored_in_db_first_mode(self):
        tasks_dir = Path(self.temp_dir.name) / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        original_tasks_dir = settings.TASKS_DIR
        settings.TASKS_DIR = tasks_dir

        try:
            task_payload = {
                "id": "task_sync_parent_first",
                "name": "task-sync-parent-first",
                "source_url": "rtsp://example/live/task",
                "source_type": "rtsp",
                "channel_id": "ch_sync_parent_first",
                "frame_interval": 5,
                "status": "stopped",
                "created_at": "2026-06-06T10:00:00",
                "updated_at": "2026-06-06T10:00:00",
                "runtime": {
                    "status": "stopped",
                    "is_running": False,
                },
                "algorithms": [
                    {
                        "id": "algo_sync_parent_first",
                        "algorithm_type": "detect",
                        "model_id": "model_person",
                        "enabled": True,
                        "labels": ["person"],
                        "need_overlay": True,
                        "regions": [
                            {
                                "id": "region_sync_parent_first",
                                "name": "entry-region",
                                "type": "polygon",
                                "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                                "enabled": True,
                            }
                        ],
                    }
                ],
            }

            task_path = tasks_dir / f"{task_payload['id']}.json"
            metadata_db_sync.sync_json_document(task_path, task_payload)

            with self.session_local() as session:
                task_row = session.execute(
                    select(TaskMeta).where(TaskMeta.id == task_payload["id"])
                ).scalar_one_or_none()
                algorithm_row = session.execute(
                    select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.task_id == task_payload["id"])
                ).scalar_one_or_none()
                algorithm_region_row = session.execute(
                    select(AlgorithmRegionMeta).where(AlgorithmRegionMeta.task_id == task_payload["id"])
                ).scalar_one_or_none()

            self.assertIsNone(task_row)
            self.assertIsNone(algorithm_row)
            self.assertIsNone(algorithm_region_row)
        finally:
            settings.TASKS_DIR = original_tasks_dir

    def test_migrate_metadata_database_schema_removes_orphan_tasks(self):
        tasks_dir = Path(self.temp_dir.name) / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        original_tasks_dir = settings.TASKS_DIR
        settings.TASKS_DIR = tasks_dir

        try:
            with self.session_local() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO tasks (
                            id, name, source_url, source_type, channel_id, frame_interval, status,
                            algorithm_count, region_count, is_running, created_at, updated_at, raw_json
                        ) VALUES (
                            'task_orphan_cleanup', 'orphan task', 'rtsp://example/live/orphan', 'rtsp',
                            'missing_channel', 10, 'stopped', 0, 0, 0, NULL, NULL, '{}'
                        )
                        """
                    )
                )
                session.execute(
                    text(
                        """
                        INSERT INTO task_algorithms (
                            id, task_id, algorithm_type, model_id, enabled, created_at, updated_at, raw_json
                        ) VALUES (
                            'algo_orphan_cleanup', 'task_orphan_cleanup', 'detection', 'model_person', 1, NULL, NULL, '{}'
                        )
                        """
                    )
                )
                session.commit()

            metadata_db_sync.migrate_metadata_database_schema()

            with self.session_local() as session:
                task_row = session.execute(
                    select(TaskMeta).where(TaskMeta.id == "task_orphan_cleanup")
                ).scalar_one_or_none()
                algorithm_row = session.execute(
                    select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.id == "algo_orphan_cleanup")
                ).scalar_one_or_none()

            self.assertIsNone(task_row)
            self.assertIsNone(algorithm_row)
        finally:
            settings.TASKS_DIR = original_tasks_dir

    def test_task_channel_foreign_key_cascades_in_sqlite_schema(self):
        with self.session_local() as session:
            session.add(
                ChannelMeta(
                    id="ch_fk_cascade",
                    name="cascade-channel",
                    type="rtsp",
                    source_url="rtsp://example/live/cascade",
                    app="live",
                    stream="cascade",
                    is_online=True,
                    is_playing=False,
                    ai_enabled=False,
                    record_mode="none",
                    raw_json={},
                )
            )
            session.add(
                TaskMeta(
                    id="task_fk_cascade",
                    name="cascade-task",
                    source_url="rtsp://example/live/cascade",
                    source_type="rtsp",
                    channel_id="ch_fk_cascade",
                    frame_interval=10,
                    status="stopped",
                    raw_json={},
                )
            )
            session.commit()

            session.query(ChannelMeta).filter(ChannelMeta.id == "ch_fk_cascade").delete()
            session.commit()

            task_row = session.execute(
                select(TaskMeta).where(TaskMeta.id == "task_fk_cascade")
            ).scalar_one_or_none()

        self.assertIsNone(task_row)

    def test_task_algorithm_model_foreign_key_targets_models_table(self):
        foreign_keys = list(TaskAlgorithmMeta.__table__.c.model_id.foreign_keys)

        self.assertEqual(len(foreign_keys), 1)
        self.assertEqual(foreign_keys[0].target_fullname, "models.id")
        self.assertEqual(foreign_keys[0].ondelete, "RESTRICT")

    def test_task_algorithm_model_foreign_key_restricts_model_delete_in_sqlite_schema(self):
        with self.session_local() as session:
            session.add(
                ModelMetaRecord(
                    id="model_fk_restrict",
                    name="fk-restrict-model",
                    raw_json={},
                )
            )
            session.add(
                TaskMeta(
                    id="task_fk_model_restrict",
                    name="fk-model-task",
                    source_url="rtsp://example/live/model-fk",
                    source_type="rtsp",
                    channel_id=None,
                    frame_interval=10,
                    status="stopped",
                    raw_json={},
                )
            )
            session.add(
                TaskAlgorithmMeta(
                    id="algo_fk_model_restrict",
                    task_id="task_fk_model_restrict",
                    algorithm_type="detection",
                    model_id="model_fk_restrict",
                    enabled=True,
                    raw_json={},
                )
            )
            session.commit()

            with self.assertRaises(Exception):
                session.query(ModelMetaRecord).filter(ModelMetaRecord.id == "model_fk_restrict").delete()
                session.commit()

    def test_migrate_metric_tables_to_numeric_ids_and_scope_key(self):
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS task_algorithm_metric_history"))
            connection.execute(text("DROP TABLE IF EXISTS task_algorithm_metrics"))
            connection.execute(
                text(
                    """
                    INSERT INTO tasks (
                        id, name, source_url, source_type, channel_id, frame_interval, status,
                        algorithm_count, region_count, is_running, created_at, updated_at, raw_json
                    ) VALUES (
                        'task_metric', 'metric task', 'rtsp://example/live/metric', 'rtsp',
                        NULL, 5, 'stopped', 1, 0, 0, NULL, NULL, '{}'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO task_algorithms (
                        id, task_id, algorithm_type, scene_key, scene_name, rule_type,
                        model_id, enabled, labels_json, detect_all_classes, label_source,
                        confidence, alert_interval, need_snapshot, need_video_clip,
                        video_before_seconds, video_after_seconds, need_overlay, requires_region,
                        overlay_color, postprocess_config_json, sort_order, created_at, updated_at, raw_json
                    ) VALUES (
                        'algo_metric', 'task_metric', 'count', NULL, 'person count', 'count',
                        NULL, 1, '[]', 1, 'all',
                        0.25, 60, 1, 1,
                        3, 3, 0, 0,
                        '#ff4d4f', '{}', 0, NULL, NULL, '{}'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE task_algorithm_metrics (
                        id VARCHAR(128) PRIMARY KEY,
                        task_id VARCHAR(64),
                        algorithm_id VARCHAR(64),
                        channel_id VARCHAR(64),
                        model_id VARCHAR(64),
                        metric_type VARCHAR(64),
                        metric_value FLOAT,
                        region_id VARCHAR(64),
                        region_name VARCHAR(255),
                        updated_at DATETIME,
                        raw_json JSON
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE task_algorithm_metric_history (
                        id VARCHAR(128) PRIMARY KEY,
                        task_id VARCHAR(64),
                        algorithm_id VARCHAR(64),
                        channel_id VARCHAR(64),
                        model_id VARCHAR(64),
                        metric_type VARCHAR(64),
                        metric_value FLOAT,
                        region_id VARCHAR(64),
                        region_name VARCHAR(255),
                        sampled_at DATETIME,
                        created_at DATETIME,
                        raw_json JSON
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO task_algorithm_metrics (
                        id, task_id, algorithm_id, channel_id, model_id, metric_type,
                        metric_value, region_id, region_name, updated_at, raw_json
                    ) VALUES (
                        'task_metric_algo_metric_global',
                        'task_metric',
                        'algo_metric',
                        'channel_metric',
                        'model_metric',
                        'person_count',
                        2,
                        NULL,
                        NULL,
                        '2026-06-24 12:00:00',
                        '{}'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO task_algorithm_metric_history (
                        id, task_id, algorithm_id, channel_id, model_id, metric_type,
                        metric_value, region_id, region_name, sampled_at, created_at, raw_json
                    ) VALUES (
                        'task_metric_algo_metric_global_20260624120000',
                        'task_metric',
                        'algo_metric',
                        'channel_metric',
                        'model_metric',
                        'person_count',
                        2,
                        NULL,
                        NULL,
                        '2026-06-24 12:00:00',
                        '2026-06-24 12:00:00',
                        '{}'
                    )
                    """
                )
            )

        metadata_db_sync.migrate_metadata_database_schema()

        inspector = inspect(self.engine)
        metric_columns = {
            column["name"]: column
            for column in inspector.get_columns("task_algorithm_metrics")
        }
        history_columns = {
            column["name"]: column
            for column in inspector.get_columns("task_algorithm_metric_history")
        }

        self.assertIn("scope_key", metric_columns)
        self.assertIn("int", str(metric_columns["id"]["type"]).lower())
        self.assertIn("int", str(history_columns["id"]["type"]).lower())

        with self.session_local() as session:
            metric_row = session.execute(
                text(
                    """
                    SELECT id, task_id, algorithm_id, metric_type, metric_value, scope_key
                    FROM task_algorithm_metrics
                    """
                )
            ).mappings().one()
            history_row = session.execute(
                text(
                    """
                    SELECT id, task_id, algorithm_id, metric_type, metric_value
                    FROM task_algorithm_metric_history
                    """
                )
            ).mappings().one()

        self.assertIsInstance(metric_row["id"], int)
        self.assertEqual(metric_row["scope_key"], "global")
        self.assertEqual(metric_row["metric_value"], 2.0)
        self.assertIsInstance(history_row["id"], int)
        self.assertEqual(history_row["metric_value"], 2.0)


if __name__ == "__main__":
    unittest.main()
