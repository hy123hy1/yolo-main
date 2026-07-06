import asyncio
import json
import sys
import tempfile
import threading
import unittest
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.db.models import (
    Base,
    ChannelMeta,
    EventMeta,
    ModelMetaRecord,
    RecordingMeta,
    TaskAlgorithmMetricHistoryMeta,
    TaskAlgorithmMeta,
    TaskAlgorithmMetricMeta,
    TaskAlgorithmRegionMeta,
    TaskMeta,
    TaskRegionMeta,
)
from src.services import metadata_db_sync
from src.services.analysis_service import AnalysisService, analysis_service
from src.services.channel_service import ChannelService
from src.services.event_service import EventService
from src.services.metric_service import MetricService
from src.services.recording_service import RecordingService


class TaskServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.temp_dir.name) / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir = Path(self.temp_dir.name) / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir = Path(self.temp_dir.name) / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.channels_dir = Path(self.temp_dir.name) / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path = Path(self.temp_dir.name) / "metadata.db"

        self.original_tasks_dir = settings.TASKS_DIR
        self.original_events_dir = settings.EVENTS_DIR
        self.original_recordings_dir = settings.RECORDINGS_DIR
        self.original_channels_dir = settings.CHANNELS_DIR
        self.original_task_restore_startup_stagger_seconds = getattr(settings, "TASK_RESTORE_STARTUP_STAGGER_SECONDS", 0.0)
        self.original_task_preview_warmup_startup_delay_seconds = getattr(settings, "TASK_PREVIEW_WARMUP_STARTUP_DELAY_SECONDS", 0.0)
        self.original_task_preview_warmup_max_concurrency = getattr(settings, "TASK_PREVIEW_WARMUP_MAX_CONCURRENCY", 1)
        settings.TASKS_DIR = self.tasks_dir
        settings.EVENTS_DIR = self.events_dir
        settings.RECORDINGS_DIR = self.recordings_dir
        settings.CHANNELS_DIR = self.channels_dir
        settings.TASK_RESTORE_STARTUP_STAGGER_SECONDS = 0.0
        settings.TASK_PREVIEW_WARMUP_STARTUP_DELAY_SECONDS = 0.0
        settings.TASK_PREVIEW_WARMUP_MAX_CONCURRENCY = 1

        self.original_engine = metadata_db_sync.engine
        self.original_session_local = metadata_db_sync.SessionLocal

        self.engine = create_engine(
            f"sqlite:///{self.sqlite_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.session_local = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        metadata_db_sync.engine = self.engine
        metadata_db_sync.SessionLocal = self.session_local
        Base.metadata.create_all(bind=self.engine)

        from src.services import analysis_service as analysis_service_module
        from src.services import event_service as event_service_module
        from src.services import metric_service as metric_service_module
        from src.services import recording_service as recording_service_module
        from src.services import task_service as task_service_module
        from src.services.channel_service import ChannelService
        from src.services.wvp_service import WVPService

        self.original_task_service_session_local = task_service_module.SessionLocal
        self.original_analysis_service_session_local = analysis_service_module.SessionLocal
        self.original_event_service_session_local = event_service_module.SessionLocal
        self.original_metric_service_session_local = metric_service_module.SessionLocal
        self.original_recording_service_session_local = recording_service_module.SessionLocal
        task_service_module.SessionLocal = self.session_local
        analysis_service_module.SessionLocal = self.session_local
        event_service_module.SessionLocal = self.session_local
        metric_service_module.SessionLocal = self.session_local
        recording_service_module.SessionLocal = self.session_local
        task_service_module.channel_service = ChannelService()
        task_service_module.wvp_service = WVPService()

        from src.services.task_service import TaskService

        self.service = TaskService()

    async def asyncTearDown(self):
        self.engine.dispose()
        metadata_db_sync.engine = self.original_engine
        metadata_db_sync.SessionLocal = self.original_session_local
        from src.services import analysis_service as analysis_service_module
        from src.services import event_service as event_service_module
        from src.services import metric_service as metric_service_module
        from src.services import recording_service as recording_service_module
        from src.services import task_service as task_service_module
        task_service_module.SessionLocal = self.original_task_service_session_local
        analysis_service_module.SessionLocal = self.original_analysis_service_session_local
        event_service_module.SessionLocal = self.original_event_service_session_local
        metric_service_module.SessionLocal = self.original_metric_service_session_local
        recording_service_module.SessionLocal = self.original_recording_service_session_local
        settings.TASKS_DIR = self.original_tasks_dir
        settings.EVENTS_DIR = self.original_events_dir
        settings.RECORDINGS_DIR = self.original_recordings_dir
        settings.CHANNELS_DIR = self.original_channels_dir
        settings.TASK_RESTORE_STARTUP_STAGGER_SECONDS = self.original_task_restore_startup_stagger_seconds
        settings.TASK_PREVIEW_WARMUP_STARTUP_DELAY_SECONDS = self.original_task_preview_warmup_startup_delay_seconds
        settings.TASK_PREVIEW_WARMUP_MAX_CONCURRENCY = self.original_task_preview_warmup_max_concurrency
        self.temp_dir.cleanup()

    async def test_create_task_persists_default_runtime_and_algorithms(self):
        task = await self.service.create_task(
            {
                "name": "person-detect-task",
                "source_url": "rtsp://example/live/1",
                "frame_interval": 5,
                "algorithms": [
                    {
                        "model_id": "model_demo",
                        "labels": ["person"],
                    }
                ],
            }
        )

        self.assertEqual(task["name"], "person-detect-task")
        self.assertEqual(task["status"], "stopped")
        self.assertEqual(task["runtime"]["status"], "stopped")
        self.assertEqual(task["algorithms"][0]["model_id"], "model_demo")
        self.assertTrue(task["algorithms"][0]["need_snapshot"])
        self.assertRegex(task["id"], r"^[0-9a-f]{8}$")
        self.assertRegex(task["algorithms"][0]["id"], r"^[0-9a-f]{8}$")
        self.assertTrue((self.tasks_dir / f"{task['id']}.json").exists())

    async def test_task_status_includes_realtime_algorithm_metrics(self):
        task = await self.service.create_task(
            {
                "name": "count-metric-task",
                "source_url": "rtsp://example/live/count",
                "algorithms": [
                    {
                        "id": "algo_count_person",
                        "model_id": "model_person",
                        "rule_type": "count",
                        "scene_name": "人员计数",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            session.add(
                TaskAlgorithmMetricMeta(
                    task_id=task["id"],
                    algorithm_id="algo_count_person",
                    channel_id=task.get("channel_id"),
                    model_id="model_person",
                    metric_type="person_count",
                    metric_value=3,
                    region_id=None,
                    region_name=None,
                    scope_key="global",
                    updated_at=None,
                    raw_json={"class_counts": {"person": 3}},
                )
            )
            session.commit()

        status = await self.service.get_task_status(task["id"])

        self.assertEqual(status["metrics"][0]["algorithm_id"], "algo_count_person")
        self.assertEqual(status["metrics"][0]["metric_type"], "person_count")
        self.assertEqual(status["metrics"][0]["metric_value"], 3)
        self.assertEqual(status["metrics"][0]["raw_json"]["class_counts"], {"person": 3})

    async def test_task_status_reads_runtime_counters_from_metadata_raw_json(self):
        runtime_snapshot = {
            "status": "running",
            "is_running": True,
            "started_at": "2026-06-23 09:23:17",
            "stopped_at": None,
            "last_analysis_at": "2026-06-23 09:30:40",
            "last_event_at": "2026-06-23 09:29:10",
            "last_error": None,
            "frame_count": 4813,
            "event_count": 2,
            "models": {
                "algo_runtime_person": {
                    "algorithm_id": "algo_runtime_person",
                    "model_id": "model_runtime_person",
                    "last_analysis_at": "2026-06-23 09:30:40",
                    "detections": 1,
                }
            },
        }

        with self.session_local() as session:
            session.add(
                TaskMeta(
                    id="task_runtime_status",
                    name="runtime-status-task",
                    source_url="rtsp://example/live/runtime-status",
                    source_type="rtsp",
                    channel_id="ch_runtime_status",
                    frame_interval=10,
                    status="running",
                    algorithm_count=1,
                    region_count=0,
                    is_running=True,
                    started_at=datetime.fromisoformat("2026-06-23T09:23:17"),
                    stopped_at=None,
                    last_analysis_at=datetime.fromisoformat("2026-06-23T09:30:40"),
                    last_event_at=datetime.fromisoformat("2026-06-23T09:29:10"),
                    last_error=None,
                    created_at=datetime.fromisoformat("2026-06-23T09:20:00"),
                    updated_at=datetime.fromisoformat("2026-06-23T09:30:40"),
                    raw_json={
                        "id": "task_runtime_status",
                        "name": "runtime-status-task",
                        "source_url": "rtsp://example/live/runtime-status",
                        "source_type": "rtsp",
                        "channel_id": "ch_runtime_status",
                        "frame_interval": 10,
                        "status": "running",
                        "algorithms": [
                            {
                                "id": "algo_runtime_person",
                                "model_id": "model_runtime_person",
                                "rule_type": "detection",
                                "enabled": True,
                                "labels": ["person"],
                                "regions": [],
                                "region_ids": [],
                            }
                        ],
                        "regions": [],
                        "runtime": runtime_snapshot,
                    },
                )
            )
            session.add(
                TaskAlgorithmMeta(
                    id="algo_runtime_person",
                    task_id="task_runtime_status",
                    algorithm_type="detection",
                    scene_key=None,
                    scene_name="人员检测",
                    rule_type="detection",
                    model_id="model_runtime_person",
                    enabled=True,
                    labels_json=["person"],
                    detect_all_classes=False,
                    label_source="recommended",
                    confidence=0.25,
                    alert_interval=60,
                    need_snapshot=True,
                    need_video_clip=True,
                    video_before_seconds=3,
                    video_after_seconds=3,
                    need_overlay=False,
                    requires_region=False,
                    overlay_color="#ff4d4f",
                    postprocess_config_json={},
                    sort_order=0,
                    created_at=datetime.fromisoformat("2026-06-23T09:20:00"),
                    updated_at=datetime.fromisoformat("2026-06-23T09:30:40"),
                    raw_json={},
                )
            )
            session.commit()

        status = await self.service.get_task_status("task_runtime_status")

        self.assertEqual(status["runtime"]["frame_count"], 4813)
        self.assertEqual(status["runtime"]["event_count"], 2)
        self.assertEqual(
            status["runtime"]["models"]["algo_runtime_person"]["detections"],
            1,
        )

    async def test_metric_service_throttles_realtime_updates_to_one_second(self):
        service = MetricService()

        first = service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_rate",
            algorithm_id="algo_metric_rate",
            channel_id="ch_metric_rate",
            model_id="model_metric_rate",
            metric_type="person_count",
            metric_value=1,
            raw_json={"class_counts": {"person": 1}},
            now=datetime(2026, 6, 18, 10, 0, 0),
        )
        second = service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_rate",
            algorithm_id="algo_metric_rate",
            channel_id="ch_metric_rate",
            model_id="model_metric_rate",
            metric_type="person_count",
            metric_value=2,
            raw_json={"class_counts": {"person": 2}},
            now=datetime(2026, 6, 18, 10, 0, 0, 500000),
        )
        third = service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_rate",
            algorithm_id="algo_metric_rate",
            channel_id="ch_metric_rate",
            model_id="model_metric_rate",
            metric_type="person_count",
            metric_value=3,
            raw_json={"class_counts": {"person": 3}},
            now=datetime(2026, 6, 18, 10, 0, 1),
        )

        with self.session_local() as session:
            row = session.query(TaskAlgorithmMetricMeta).filter(TaskAlgorithmMetricMeta.id == first["id"]).one()

        self.assertFalse(second["updated"])
        self.assertTrue(third["updated"])
        self.assertEqual(row.metric_value, 3)
        self.assertEqual(row.raw_json["class_counts"], {"person": 3})

    async def test_metric_service_samples_history_every_sixty_seconds(self):
        service = MetricService()

        service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_history",
            algorithm_id="algo_metric_history",
            channel_id="ch_metric_history",
            model_id="model_metric_history",
            metric_type="vehicle_count",
            metric_value=1,
            raw_json={"class_counts": {"truck": 1}},
            now=datetime(2026, 6, 18, 10, 0, 0),
        )
        service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_history",
            algorithm_id="algo_metric_history",
            channel_id="ch_metric_history",
            model_id="model_metric_history",
            metric_type="vehicle_count",
            metric_value=2,
            raw_json={"class_counts": {"truck": 2}},
            now=datetime(2026, 6, 18, 10, 0, 30),
        )
        service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_history",
            algorithm_id="algo_metric_history",
            channel_id="ch_metric_history",
            model_id="model_metric_history",
            metric_type="vehicle_count",
            metric_value=3,
            raw_json={"class_counts": {"truck": 3}},
            now=datetime(2026, 6, 18, 10, 1, 0),
        )

        with self.session_local() as session:
            history_rows = (
                session.query(TaskAlgorithmMetricHistoryMeta)
                .filter(TaskAlgorithmMetricHistoryMeta.task_id == "task_metric_history")
                .order_by(TaskAlgorithmMetricHistoryMeta.sampled_at.asc())
                .all()
            )

        self.assertEqual(len(history_rows), 2)
        self.assertEqual([row.metric_value for row in history_rows], [1, 3])

    async def test_list_tasks_prefers_metadata_view_by_default(self):
        task = await self.service.create_task(
            {
                "name": "json-name",
                "channel_id": "ch_list_tasks_db_first",
                "source_url": "rtsp://example/live/db-first",
                "algorithms": [
                    {
                        "id": "algo_db_first",
                        "model_id": "model_db_first",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            row = session.execute(
                select(TaskMeta).where(TaskMeta.id == task["id"])
            ).scalar_one()
            row.name = "db-name"
            session.commit()

        result = await self.service.list_tasks()

        matched = next(item for item in result["items"] if item["id"] == task["id"])
        self.assertEqual(matched["name"], "db-name")

    async def test_list_tasks_summary_does_not_expand_task_detail(self):
        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_summary_cached",
                    name="summary-cached-task",
                    source_url="rtsp://example/live/task-summary",
                    source_type="rtsp",
                    channel_id="ch_task_summary",
                    frame_interval=10,
                    status="running",
                    algorithm_count=2,
                    region_count=1,
                    is_running=True,
                    started_at=None,
                    stopped_at=None,
                    last_analysis_at=None,
                    last_event_at=None,
                    last_error=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "task_summary_cached",
                        "name": "summary-cached-task",
                        "source_url": "rtsp://example/live/task-summary",
                        "source_type": "rtsp",
                        "channel_id": "ch_task_summary",
                        "frame_interval": 10,
                        "status": "running",
                        "runtime": {
                            "status": "running",
                            "is_running": True,
                        },
                    },
                )
            )
            session.commit()

        with patch.object(
            self.service,
            "get_task_from_metadata",
            AsyncMock(side_effect=AssertionError("list_tasks should not expand detail")),
        ):
            result = await self.service.list_tasks()

        item = next(entry for entry in result["items"] if entry["id"] == "task_summary_cached")
        self.assertEqual(item["algorithm_count"], 2)
        self.assertEqual(item["region_count"], 1)
        self.assertEqual(item["runtime"]["status"], "running")

    async def test_list_tasks_includes_metadata_only_task_without_runtime_json(self):
        runtime_task = await self.service.create_task(
            {
                "name": "runtime-task",
                "channel_id": "ch_runtime_task",
                "source_url": "rtsp://example/live/runtime-task",
                "algorithms": [
                    {
                        "id": "algo_runtime_task",
                        "model_id": "model_runtime_task",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_metadata_only_visible",
                    name="metadata-only-task",
                    source_url="rtsp://example/live/metadata-only-task",
                    source_type="rtsp",
                    channel_id="ch_metadata_only_task",
                    frame_interval=10,
                    status="stopped",
                    algorithm_count=0,
                    region_count=0,
                    is_running=False,
                    started_at=None,
                    stopped_at=None,
                    last_analysis_at=None,
                    last_event_at=None,
                    last_error=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "task_metadata_only_visible",
                        "name": "metadata-only-task",
                        "source_url": "rtsp://example/live/metadata-only-task",
                        "source_type": "rtsp",
                        "channel_id": "ch_metadata_only_task",
                        "frame_interval": 10,
                        "status": "stopped",
                        "regions": [],
                        "algorithms": [],
                        "runtime": {
                            "status": "stopped",
                            "is_running": False,
                            "started_at": None,
                            "stopped_at": None,
                            "last_error": None,
                            "last_analysis_at": None,
                            "last_event_at": None,
                        },
                    },
                )
            )
            session.commit()

        result = await self.service.list_tasks()

        item = next((entry for entry in result["items"] if entry["id"] == "task_metadata_only_visible"), None)
        self.assertIsNotNone(item)
        self.assertEqual(item["name"], "metadata-only-task")
        self.assertEqual(item["channel_id"], "ch_metadata_only_task")

        runtime_item = next((entry for entry in result["items"] if entry["id"] == runtime_task["id"]), None)
        self.assertIsNotNone(runtime_item)

    async def test_find_any_task_on_channel_includes_metadata_only_task_without_runtime_json(self):
        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_metadata_only_lookup",
                    name="metadata-only-lookup",
                    source_url="rtsp://example/live/metadata-only-lookup",
                    source_type="rtsp",
                    channel_id="ch_metadata_lookup",
                    frame_interval=10,
                    status="stopped",
                    algorithm_count=0,
                    region_count=0,
                    is_running=False,
                    started_at=None,
                    stopped_at=None,
                    last_analysis_at=None,
                    last_event_at=None,
                    last_error=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "task_metadata_only_lookup",
                        "name": "metadata-only-lookup",
                        "source_url": "rtsp://example/live/metadata-only-lookup",
                        "source_type": "rtsp",
                        "channel_id": "ch_metadata_lookup",
                        "frame_interval": 10,
                        "status": "stopped",
                        "regions": [],
                        "algorithms": [],
                        "runtime": {
                            "status": "stopped",
                            "is_running": False,
                        },
                    },
                )
            )
            session.commit()

        task = await self.service._find_any_task_on_channel("ch_metadata_lookup")

        self.assertIsNotNone(task)
        self.assertEqual(task["id"], "task_metadata_only_lookup")
        self.assertEqual(task["channel_id"], "ch_metadata_lookup")

    async def test_find_running_task_on_channel_includes_metadata_only_running_task_without_runtime_json(self):
        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_metadata_only_running",
                    name="metadata-only-running",
                    source_url="rtsp://example/live/metadata-only-running",
                    source_type="rtsp",
                    channel_id="ch_metadata_running",
                    frame_interval=10,
                    status="running",
                    algorithm_count=1,
                    region_count=0,
                    is_running=True,
                    started_at=None,
                    stopped_at=None,
                    last_analysis_at=None,
                    last_event_at=None,
                    last_error=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "task_metadata_only_running",
                        "name": "metadata-only-running",
                        "source_url": "rtsp://example/live/metadata-only-running",
                        "source_type": "rtsp",
                        "channel_id": "ch_metadata_running",
                        "frame_interval": 10,
                        "status": "running",
                        "regions": [],
                        "algorithms": [
                            {
                                "id": "algo_metadata_only_running",
                                "model_id": "model_metadata_only_running",
                            }
                        ],
                        "runtime": {
                            "status": "running",
                            "is_running": True,
                        },
                    },
                )
            )
            session.commit()

        task = await self.service._find_running_task_on_channel("ch_metadata_running")

        self.assertIsNotNone(task)
        self.assertEqual(task["id"], "task_metadata_only_running")
        self.assertEqual(task["runtime"]["status"], "running")
        self.assertTrue(task["runtime"]["is_running"])

    async def test_get_task_prefers_metadata_view_by_default(self):
        task = await self.service.create_task(
            {
                "name": "json-detail-name",
                "channel_id": "ch_get_task_db_first",
                "source_url": "rtsp://example/live/db-first-detail",
                "algorithms": [
                    {
                        "id": "algo_db_first_detail",
                        "model_id": "model_db_first_detail",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            row = session.execute(
                select(TaskMeta).where(TaskMeta.id == task["id"])
            ).scalar_one()
            row.name = "db-detail-name"
            session.commit()

        result = await self.service.get_task(task["id"])

        self.assertEqual(result["name"], "db-detail-name")

    async def test_list_task_algorithms_prefers_metadata_view_by_default(self):
        task = await self.service.create_task(
            {
                "name": "algo-list-db-first",
                "channel_id": "ch_algo_list_db_first",
                "source_url": "rtsp://example/live/db-first-algo",
                "algorithms": [
                    {
                        "id": "algo_meta_name",
                        "model_id": "model_meta_name",
                        "labels": ["person"],
                        "scene_name": "json-scene",
                    }
                ],
            }
        )

        with self.session_local() as session:
            row = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.id == "algo_meta_name")
            ).scalar_one()
            row.scene_name = "db-scene"
            session.commit()

        result = await self.service.list_task_algorithms(task["id"])

        self.assertEqual(result["items"][0]["scene_name"], "db-scene")

    async def test_create_task_rejects_second_task_on_same_channel(self):
        channel = {
            "id": "ch_unique_task",
            "name": "unique-task-channel",
            "type": "rtsp",
            "source_url": "rtsp://example/live/unique",
            "app": "live",
            "stream": "unique",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-09 10:00:00",
            "updated_at": "2026-06-09 10:00:00",
            "ext": {},
        }
        (self.channels_dir / "ch_unique_task.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        first = await self.service.create_task(
            {
                "name": "first-task",
                "channel_id": "ch_unique_task",
            }
        )
        self.assertEqual(first["channel_id"], "ch_unique_task")

        with self.assertRaisesRegex(ValueError, "already has a task"):
            await self.service.create_task(
                {
                    "name": "second-task",
                    "channel_id": "ch_unique_task",
                }
            )

    async def test_create_task_requires_channel_id(self):
        with self.assertRaisesRegex(ValueError, "channel_id"):
            await self.service.create_task(
                {
                    "name": "task-without-channel",
                    "source_url": "rtsp://example/live/no-channel",
                }
            )

    async def test_create_task_uses_selected_channel_source_when_source_missing(self):
        channel = {
            "id": "ch_wvp_demo",
            "name": "WVP测试通道",
            "type": "wvp",
            "source_url": "rtsp://192.168.1.10/live/test",
            "app": "rtp",
            "stream": "34020000001110000121_34020000001320000002",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-05T10:00:00",
            "updated_at": "2026-06-05T10:00:00",
            "ext": {
                "zones": [],
                "ai_config": {},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "1",
                    "online": True,
                },
            },
        }
        (self.channels_dir / "ch_wvp_demo.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "wvp-task",
                "channel_id": "ch_wvp_demo",
            }
        )

        self.assertEqual(task["channel_id"], "ch_wvp_demo")
        self.assertEqual(task["source_url"], "rtsp://192.168.1.10/live/test")
        self.assertEqual(task["source_type"], "rtsp")

    async def test_start_task_rejects_missing_bound_channel(self):
        task = await self.service.create_task(
            {
                "name": "missing-bound-channel-task",
                "channel_id": "ch_missing_bound",
                "source_url": None,
                "algorithms": [
                    {
                        "id": "algo_missing_bound",
                        "model_id": "model_missing_bound",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with (
            patch.object(self.service, "_find_running_task_on_channel", AsyncMock(return_value=None)),
            patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock(return_value={"status": "running"})) as mocked_start,
        ):
            with self.assertRaisesRegex(ValueError, "Task bound channel not found"):
                await self.service.start_task(task["id"])

        mocked_start.assert_not_awaited()

    async def test_start_task_refreshes_bound_channel_source_even_when_task_has_existing_source(self):
        channel = {
            "id": "ch_wvp_refresh",
            "name": "wvp-refresh-channel",
            "type": "wvp",
            "source_url": "rtsp://new-source/live",
            "app": "rtp",
            "stream": "34020000001110000121_34020000001320000099",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-11 15:00:00",
            "updated_at": "2026-06-11 15:00:00",
            "ext": {
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "1",
                    "online": True,
                },
            },
        }
        (self.channels_dir / "ch_wvp_refresh.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "wvp-refresh-task",
                "channel_id": "ch_wvp_refresh",
                "source_url": "rtsp://old-source/live",
                "algorithms": [
                    {
                        "id": "algo_refresh",
                        "model_id": "model_refresh",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with (
            patch.object(self.service, "_find_running_task_on_channel", AsyncMock(return_value=None)),
            patch.object(self.service, "_has_runnable_algorithm", return_value=True),
            patch.object(self.service, "_get_algorithms_missing_regions", return_value=[]),
            patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock(return_value={"status": "running"})),
        ):
            started = await self.service.start_task(task["id"])

        self.assertEqual(started["status"], "running")
        saved_task = json.loads((self.tasks_dir / f"{task['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_task["source_url"], "rtsp://new-source/live")
        self.assertEqual(saved_task["source_type"], "rtsp")

    async def test_start_task_does_not_require_bound_channel_proxy_before_analysis(self):
        channel = {
            "id": "ch_task_proxy",
            "name": "task-proxy-channel",
            "type": "wvp",
            "source_url": "rtsp://192.168.1.36:554/rtp/task_proxy",
            "app": "rtp",
            "stream": "task_proxy",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-11 16:10:00",
            "updated_at": "2026-06-11 16:10:00",
            "ext": {},
        }
        (self.channels_dir / "ch_task_proxy.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "task-proxy-start",
                "channel_id": "ch_task_proxy",
                "algorithms": [
                    {
                        "id": "algo_task_proxy",
                        "model_id": "model_task_proxy",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with (
            patch.object(self.service, "_find_running_task_on_channel", AsyncMock(return_value=None)),
            patch.object(self.service, "_has_runnable_algorithm", return_value=True),
            patch.object(self.service, "_get_algorithms_missing_regions", return_value=[]),
            patch("src.services.task_service.channel_service.play", AsyncMock(return_value={"status": "playing"})) as mocked_play,
            patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock(return_value={"status": "running"})) as mocked_start,
        ):
            started = await self.service.start_task(task["id"])

        self.assertEqual(started["status"], "running")
        mocked_play.assert_not_awaited()
        mocked_start.assert_awaited_once_with(task["id"])

    async def test_update_algorithm_can_disable_region_requirement_and_start_without_regions(self):
        channel = {
            "id": "ch_disable_region",
            "name": "disable-region-channel",
            "type": "rtsp",
            "source_url": "rtsp://example/live/disable-region",
            "app": "live",
            "stream": "disable-region",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-12 10:00:00",
            "updated_at": "2026-06-12 10:00:00",
            "ext": {},
        }
        (self.channels_dir / "ch_disable_region.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "disable-region-task",
                "channel_id": "ch_disable_region",
                "algorithms": [
                    {
                        "id": "algo_disable_region",
                        "model_id": "model_disable_region",
                        "labels": ["person"],
                        "need_overlay": True,
                        "requires_region": True,
                        "regions": [],
                    }
                ],
            }
        )

        with (
            patch.object(self.service, "_find_running_task_on_channel", AsyncMock(return_value=None)),
            patch("src.services.task_service.channel_service.play", AsyncMock(return_value={"status": "playing"})) as mocked_play_initial,
            patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock(return_value={"status": "running"})) as mocked_start_initial,
        ):
            started_initial = await self.service.start_task(task["id"])

        self.assertEqual(started_initial["status"], "running")
        mocked_play_initial.assert_awaited_once_with("ch_disable_region")
        mocked_start_initial.assert_awaited_once_with(task["id"])

        updated = await self.service.update_algorithm(
            task["id"],
            "algo_disable_region",
            {
                "need_overlay": False,
                "requires_region": False,
            },
        )

        self.assertIsNotNone(updated)
        self.assertFalse(updated["need_overlay"])
        self.assertFalse(updated["requires_region"])

        with (
            patch.object(self.service, "_find_running_task_on_channel", AsyncMock(return_value=None)),
            patch("src.services.task_service.channel_service.play", AsyncMock(return_value={"status": "playing"})) as mocked_play,
            patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock(return_value={"status": "running"})) as mocked_start,
        ):
            started = await self.service.start_task(task["id"])

        self.assertEqual(started["status"], "running")
        mocked_play.assert_awaited_once_with("ch_disable_region")
        mocked_start.assert_awaited_once_with(task["id"])

    async def test_create_task_persists_algorithm_scene_fields_and_metadata(self):
        task = await self.service.create_task(
            {
                "name": "scene-driven-task",
                "source_url": "rtsp://example/live/scene",
                "algorithms": [
                    {
                        "id": "algo_scene_01",
                        "model_id": "model_person",
                        "labels": ["person"],
                        "scene_key": "person_intrusion",
                        "scene_name": "人员闯入",
                        "rule_type": "intrusion",
                        "detect_all_classes": False,
                        "label_source": "recommended",
                        "requires_region": True,
                        "postprocess_config": {"linger_seconds": 3},
                    }
                ],
            }
        )

        algorithm = task["algorithms"][0]
        self.assertEqual(algorithm["scene_key"], "person_intrusion")
        self.assertEqual(algorithm["scene_name"], "人员闯入")
        self.assertEqual(algorithm["rule_type"], "intrusion")
        self.assertFalse(algorithm["detect_all_classes"])
        self.assertEqual(algorithm["label_source"], "recommended")
        self.assertTrue(algorithm["requires_region"])
        self.assertEqual(algorithm["postprocess_config"], {"linger_seconds": 3})

        metadata_task = await self.service.get_task_from_metadata(task["id"])
        self.assertIsNotNone(metadata_task)
        metadata_algorithm = metadata_task["algorithms"][0]
        self.assertEqual(metadata_algorithm["scene_key"], "person_intrusion")
        self.assertEqual(metadata_algorithm["scene_name"], "人员闯入")
        self.assertEqual(metadata_algorithm["rule_type"], "intrusion")
        self.assertFalse(metadata_algorithm["detect_all_classes"])
        self.assertEqual(metadata_algorithm["label_source"], "recommended")
        self.assertTrue(metadata_algorithm["requires_region"])
        self.assertEqual(metadata_algorithm["postprocess_config"], {"linger_seconds": 3})

        with self.session_local() as session:
            row = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.id == "algo_scene_01")
            ).scalar_one()
        self.assertEqual(row.scene_key, "person_intrusion")
        self.assertEqual(row.scene_name, "人员闯入")
        self.assertEqual(row.rule_type, "intrusion")
        self.assertFalse(row.detect_all_classes)
        self.assertEqual(row.label_source, "recommended")
        self.assertTrue(row.requires_region)
        self.assertEqual(row.postprocess_config_json, {"linger_seconds": 3})

    async def test_analysis_service_respects_detect_all_classes_flag(self):
        service = AnalysisService()
        detections = [
            {"class_name": "person", "x1": 0, "y1": 0, "x2": 10, "y2": 10},
            {"class_name": "car", "x1": 20, "y1": 20, "x2": 30, "y2": 30},
        ]
        task = {"regions": []}

        filtered = service._apply_algorithm_scoped_filters(
            detections,
            task,
            {
                "id": "algo_all",
                "labels": ["person"],
                "detect_all_classes": True,
                "region_ids": [],
                "regions": [],
            },
        )
        self.assertEqual([item["class_name"] for item in filtered], ["person", "car"])

        filtered = service._apply_algorithm_scoped_filters(
            detections,
            task,
            {
                "id": "algo_person_only",
                "labels": ["person"],
                "detect_all_classes": False,
                "region_ids": [],
                "regions": [],
            },
        )
        self.assertEqual([item["class_name"] for item in filtered], ["person"])

    async def test_restore_running_tasks_restarts_analysis_and_zone_preview(self):
        channel = {
            "id": "ch_restore_demo",
            "name": "恢复测试通道",
            "type": "wvp",
            "source_url": "rtsp://192.168.1.10/live/restore",
            "app": "rtp",
            "stream": "restore_stream",
            "is_online": True,
            "is_playing": True,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-05T10:00:00",
            "updated_at": "2026-06-05T10:00:00",
            "ext": {
                "zones": [],
                "ai_config": {},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "99",
                    "online": True,
                },
            },
        }
        (self.channels_dir / "ch_restore_demo.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "restore-running-task",
                "channel_id": "ch_restore_demo",
                "source_url": "rtsp://192.168.1.10/live/restore",
                "regions": [
                    {
                        "id": "region_restore",
                        "name": "restore-zone",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_restore",
                        "model_id": "model_restore",
                        "need_overlay": True,
                        "region_ids": ["region_restore"],
                    }
                ],
            }
        )

        task_path = self.tasks_dir / f"{task['id']}.json"
        task_payload = json.loads(task_path.read_text(encoding="utf-8"))
        task_payload["runtime"] = {
            "status": "running",
            "is_running": True,
            "started_at": "2026-06-05T15:00:00",
            "stopped_at": None,
            "last_error": None,
            "last_analysis_at": None,
            "last_event_at": None,
        }
        task_payload["status"] = "running"
        task_path.write_text(json.dumps(task_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as start_analysis_mock, \
             patch("src.services.task_service.channel_service.start_zone_preview", AsyncMock(return_value={"status": "running"})) as start_zone_preview_mock:
            result = await self.service.restore_running_tasks()

        self.assertEqual(result["restored"], 1)
        self.assertEqual(result["failed"], [])
        start_analysis_mock.assert_awaited_once_with(task["id"])
        start_zone_preview_mock.assert_awaited_once()
        _, kwargs = start_zone_preview_mock.await_args
        self.assertEqual(kwargs["zones"][0]["id"], "region_restore")
        self.assertEqual(kwargs["zone_source"], "task")
        self.assertEqual(kwargs["owner_task_id"], task["id"])

    async def test_restore_running_tasks_uses_cached_source_without_sync_wvp_play_info(self):
        channel = {
            "id": "ch_restore_cached_wvp",
            "name": "restore-cached-wvp",
            "type": "wvp",
            "source_url": "rtsp://192.168.1.10/live/cached",
            "app": "rtp",
            "stream": "restore_cached_stream",
            "is_online": True,
            "is_playing": True,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-05T10:00:00",
            "updated_at": "2026-06-05T10:00:00",
            "ext": {
                "zones": [],
                "ai_config": {},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "100",
                    "online": True,
                },
            },
        }
        (self.channels_dir / "ch_restore_cached_wvp.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id=channel["id"],
                    name=channel["name"],
                    type=channel["type"],
                    source_url=channel["source_url"],
                    app=channel["app"],
                    stream=channel["stream"],
                    is_online=channel["is_online"],
                    is_playing=channel["is_playing"],
                    ai_enabled=channel["ai_enabled"],
                    record_mode=channel["record_mode"],
                    snapshot_url=channel["snapshot_url"],
                    raw_json=channel,
                )
            )
            session.commit()

        task = await self.service.create_task(
            {
                "name": "restore-cached-wvp-task",
                "channel_id": "ch_restore_cached_wvp",
                "source_url": "rtsp://192.168.1.10/live/cached",
                "algorithms": [
                    {
                        "id": "algo_restore_cached_wvp",
                        "model_id": "model_restore_cached_wvp",
                        "enabled": True,
                    }
                ],
            }
        )

        with self.session_local() as session:
            row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one()
            payload = dict(row.raw_json or {})
            payload["runtime"] = {
                **dict(payload.get("runtime") or {}),
                "status": "running",
                "is_running": True,
            }
            row.status = "running"
            row.is_running = True
            row.raw_json = payload
            session.commit()

        with (
            patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start,
            patch.object(self.service, "_schedule_task_preview_warmup") as mocked_schedule,
            patch.object(self.service, "_schedule_task_source_refresh", create=True) as mocked_schedule_source_refresh,
            patch("src.services.task_service.channel_service.get_channel", AsyncMock(return_value=channel)),
            patch("src.services.task_service.wvp_service.refresh_channel_source", return_value=channel) as mocked_refresh,
        ):
            result = await self.service.restore_running_tasks()

        self.assertEqual(result["restored"], 1)
        mocked_start.assert_awaited_once_with(task["id"])
        mocked_schedule.assert_called_once()
        mocked_schedule_source_refresh.assert_called_once_with(task["id"], reason="startup-restore")
        mocked_refresh.assert_not_called()

    async def test_restore_running_tasks_includes_status_running_even_when_is_running_false(self):
        channel = {
            "id": "ch_restore_status_only",
            "name": "restore-status-only",
            "type": "rtsp",
            "source_url": "rtsp://example/live/restore-status-only",
            "app": "live",
            "stream": "restore_status_only",
            "is_online": True,
            "is_playing": True,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-05T10:00:00",
            "updated_at": "2026-06-05T10:00:00",
            "ext": {
                "zones": [],
                "ai_config": {},
            },
        }
        (self.channels_dir / "ch_restore_status_only.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "restore-status-only-task",
                "channel_id": "ch_restore_status_only",
                "source_url": "rtsp://example/live/restore-status-only",
                "algorithms": [
                    {
                        "id": "algo_restore_status_only",
                        "model_id": "model_restore_status_only",
                    }
                ],
            }
        )

        with self.session_local() as session:
            row = session.query(TaskMeta).filter(TaskMeta.id == task["id"]).one()
            payload = dict(row.raw_json or {})
            payload["status"] = "running"
            payload.setdefault("runtime", {})
            payload["runtime"]["status"] = "running"
            payload["runtime"]["is_running"] = False
            row.status = "running"
            row.is_running = False
            row.raw_json = payload
            session.commit()

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as start_analysis_mock, \
             patch.object(self.service, "_warmup_task_preview", AsyncMock(return_value={"status": "running"})) as warmup_mock:
            result = await self.service.restore_running_tasks()

        self.assertEqual(result["restored"], 1)
        self.assertEqual(result["failed"], [])
        start_analysis_mock.assert_awaited_once_with(task["id"])
        warmup_mock.assert_awaited_once()

    async def test_restore_running_tasks_cleans_stale_stopping_to_stopped(self):
        task = await self.service.create_task(
            {
                "name": "restore-stale-stopping-task",
                "channel_id": "ch_restore_stale_stopping",
                "source_url": "rtsp://example/live/restore-stale-stopping",
                "algorithms": [
                    {
                        "id": "algo_restore_stale_stopping",
                        "model_id": "model_restore_stale_stopping",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            row = session.query(TaskMeta).filter(TaskMeta.id == task["id"]).one()
            payload = dict(row.raw_json or {})
            payload["status"] = "stopping"
            payload["runtime"] = {
                **dict(payload.get("runtime") or {}),
                "status": "stopping",
                "is_running": True,
                "started_at": "2026-07-01 10:00:00",
                "stopped_at": "2026-07-01 10:05:00",
            }
            row.status = "stopping"
            row.is_running = True
            row.raw_json = payload
            session.commit()

        result = await self.service.restore_running_tasks()

        self.assertEqual(result["restored"], 0)
        self.assertEqual(result["failed"], [])

        with self.session_local() as session:
            row = session.query(TaskMeta).filter(TaskMeta.id == task["id"]).one()

        self.assertEqual(row.status, "stopped")
        self.assertFalse(row.is_running)
        self.assertEqual((row.raw_json or {}).get("status"), "stopped")
        self.assertEqual(((row.raw_json or {}).get("runtime") or {}).get("status"), "stopped")
        self.assertFalse(bool((((row.raw_json or {}).get("runtime") or {}).get("is_running"))))

    async def test_sync_task_metadata_to_sqlite(self):
        task = await self.service.create_task(
            {
                "name": "helmet-detect-task",
                "source_url": "rtsp://example/live/2",
                "algorithms": [
                    {
                        "model_id": "model_helmet",
                        "labels": ["helmet"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one()

        self.assertEqual(row.name, "helmet-detect-task")
        self.assertEqual(row.source_url, "rtsp://example/live/2")
        self.assertEqual(row.status, "stopped")
        self.assertEqual(row.raw_json["algorithms"][0]["model_id"], "model_helmet")

    async def test_sync_task_metadata_into_normalized_tables(self):
        task = await self.service.create_task(
            {
                "name": "normalized-sync-task",
                "source_url": "rtsp://example/live/10",
                "channel_id": "ch_normalized",
                "frame_interval": 6,
                "regions": [
                    {
                        "id": "region_gate",
                        "name": "gate-zone",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    },
                    {
                        "id": "region_yard",
                        "name": "yard-zone",
                        "coordinates": [[120, 0], [240, 0], [240, 120], [120, 120]],
                    },
                ],
                "algorithms": [
                    {
                        "id": "algo_person",
                        "model_id": "model_person",
                        "labels": ["person"],
                        "confidence": 0.4,
                        "need_overlay": True,
                        "region_ids": ["region_gate", "region_yard"],
                    },
                    {
                        "id": "algo_helmet",
                        "model_id": "model_helmet",
                        "labels": ["helmet"],
                        "confidence": 0.55,
                        "need_overlay": True,
                        "region_ids": ["region_gate"],
                    },
                ],
            }
        )

        with self.session_local() as session:
            task_row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one()
            algorithm_rows = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.task_id == task["id"])
            ).scalars().all()
            region_rows = session.execute(
                select(TaskRegionMeta).where(TaskRegionMeta.task_id == task["id"])
            ).scalars().all()
            link_rows = session.execute(
                select(TaskAlgorithmRegionMeta).where(TaskAlgorithmRegionMeta.task_id == task["id"])
            ).scalars().all()

        self.assertEqual(task_row.algorithm_count, 2)
        self.assertEqual(task_row.region_count, 2)
        self.assertEqual(len(algorithm_rows), 2)
        self.assertEqual(len(region_rows), 2)
        self.assertEqual(len(link_rows), 3)

        person_row = next(row for row in algorithm_rows if row.id == "algo_person")
        helmet_row = next(row for row in algorithm_rows if row.id == "algo_helmet")
        gate_row = next(row for row in region_rows if row.id == "region_gate")

        self.assertEqual(person_row.model_id, "model_person")
        self.assertEqual(person_row.labels_json, ["person"])
        self.assertAlmostEqual(person_row.confidence, 0.4)
        self.assertTrue(person_row.need_overlay)
        self.assertEqual(helmet_row.model_id, "model_helmet")
        self.assertEqual(gate_row.name, "gate-zone")
        self.assertEqual(gate_row.coordinates_json[0], [0, 0])
        self.assertEqual(
            sorted((row.algorithm_id, row.region_id) for row in link_rows),
            [
                ("algo_helmet", "region_gate"),
                ("algo_person", "region_gate"),
                ("algo_person", "region_yard"),
            ],
        )

    async def test_task_sync_rebuilds_normalized_children_after_update(self):
        task = await self.service.create_task(
            {
                "name": "rebuild-sync-task",
                "source_url": "rtsp://example/live/11",
                "regions": [
                    {
                        "id": "region_old",
                        "name": "old-zone",
                        "coordinates": [[0, 0], [10, 0], [10, 10], [0, 10]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_old",
                        "model_id": "model_old",
                        "region_ids": ["region_old"],
                    }
                ],
            }
        )

        await self.service.update_task(
            task["id"],
            {
                "name": "rebuild-sync-task-v2",
                "source_url": "rtsp://example/live/11",
                "regions": [
                    {
                        "id": "region_new",
                        "name": "new-zone",
                        "coordinates": [[20, 20], [40, 20], [40, 40], [20, 40]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_new",
                        "model_id": "model_new",
                        "region_ids": ["region_new"],
                    }
                ],
            },
        )

        with self.session_local() as session:
            algorithm_ids = session.execute(
                select(TaskAlgorithmMeta.id).where(TaskAlgorithmMeta.task_id == task["id"])
            ).scalars().all()
            region_ids = session.execute(
                select(TaskRegionMeta.id).where(TaskRegionMeta.task_id == task["id"])
            ).scalars().all()
            link_pairs = session.execute(
                select(TaskAlgorithmRegionMeta.algorithm_id, TaskAlgorithmRegionMeta.region_id).where(
                    TaskAlgorithmRegionMeta.task_id == task["id"]
                )
            ).all()

        self.assertEqual(algorithm_ids, ["algo_new"])
        self.assertEqual(region_ids, ["region_new"])
        self.assertEqual(link_pairs, [("algo_new", "region_new")])

    async def test_update_task_rehydrates_metadata_only_task_without_runtime_json(self):
        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_metadata_only_update",
                    name="metadata-only-update",
                    source_url="rtsp://example/live/metadata-only-update",
                    source_type="rtsp",
                    channel_id="ch_metadata_update",
                    frame_interval=10,
                    status="stopped",
                    algorithm_count=0,
                    region_count=0,
                    is_running=False,
                    started_at=None,
                    stopped_at=None,
                    last_analysis_at=None,
                    last_event_at=None,
                    last_error=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "task_metadata_only_update",
                        "name": "metadata-only-update",
                        "source_url": "rtsp://example/live/metadata-only-update",
                        "source_type": "rtsp",
                        "channel_id": "ch_metadata_update",
                        "frame_interval": 10,
                        "status": "stopped",
                        "regions": [],
                        "algorithms": [],
                        "runtime": {
                            "status": "stopped",
                            "is_running": False,
                        },
                    },
                )
            )
            session.commit()

        updated = await self.service.update_task(
            "task_metadata_only_update",
            {
                "name": "metadata-only-update-v2",
                "frame_interval": 5,
            },
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated["name"], "metadata-only-update-v2")
        self.assertEqual(updated["frame_interval"], 5)
        self.assertTrue((self.tasks_dir / "task_metadata_only_update.json").exists())

        with self.session_local() as session:
            row = session.execute(
                select(TaskMeta).where(TaskMeta.id == "task_metadata_only_update")
            ).scalar_one()

        self.assertEqual(row.name, "metadata-only-update-v2")
        self.assertEqual(row.frame_interval, 5)

    async def test_delete_task_cleans_normalized_tables(self):
        task = await self.service.create_task(
            {
                "name": "delete-cleanup-task",
                "source_url": "rtsp://example/live/12",
                "regions": [
                    {
                        "id": "region_cleanup",
                        "name": "cleanup-zone",
                        "coordinates": [[0, 0], [50, 0], [50, 50], [0, 50]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_cleanup",
                        "model_id": "model_cleanup",
                        "region_ids": ["region_cleanup"],
                    }
                ],
            }
        )

        await self.service.delete_task(task["id"])

        with self.session_local() as session:
            task_row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one_or_none()
            algorithm_rows = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.task_id == task["id"])
            ).scalars().all()
            region_rows = session.execute(
                select(TaskRegionMeta).where(TaskRegionMeta.task_id == task["id"])
            ).scalars().all()
            link_rows = session.execute(
                select(TaskAlgorithmRegionMeta).where(TaskAlgorithmRegionMeta.task_id == task["id"])
            ).scalars().all()

        self.assertIsNone(task_row)
        self.assertEqual(algorithm_rows, [])
        self.assertEqual(region_rows, [])
        self.assertEqual(link_rows, [])

    async def test_delete_task_removes_metadata_only_task_without_runtime_json(self):
        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_metadata_only_delete",
                    name="metadata-only-delete",
                    source_url="rtsp://example/live/metadata-only-delete",
                    source_type="rtsp",
                    channel_id="ch_metadata_delete",
                    frame_interval=10,
                    status="stopped",
                    algorithm_count=0,
                    region_count=0,
                    is_running=False,
                    started_at=None,
                    stopped_at=None,
                    last_analysis_at=None,
                    last_event_at=None,
                    last_error=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "task_metadata_only_delete",
                        "name": "metadata-only-delete",
                        "source_url": "rtsp://example/live/metadata-only-delete",
                        "source_type": "rtsp",
                        "channel_id": "ch_metadata_delete",
                        "frame_interval": 10,
                        "status": "stopped",
                        "regions": [],
                        "algorithms": [],
                        "runtime": {
                            "status": "stopped",
                            "is_running": False,
                        },
                    },
                )
            )
            session.commit()

        deleted = await self.service.delete_task("task_metadata_only_delete")

        self.assertIsNotNone(deleted)
        self.assertEqual(deleted["id"], "task_metadata_only_delete")
        self.assertFalse((self.tasks_dir / "task_metadata_only_delete.json").exists())

        with self.session_local() as session:
            row = session.execute(
                select(TaskMeta).where(TaskMeta.id == "task_metadata_only_delete")
            ).scalar_one_or_none()

        self.assertIsNone(row)

    async def test_get_task_metadata_snapshot_returns_normalized_rows(self):
        task = await self.service.create_task(
            {
                "name": "inspect-metadata-task",
                "source_url": "rtsp://example/live/13",
                "channel_id": "ch_inspect",
                "regions": [
                    {
                        "id": "region_inspect",
                        "name": "inspect-zone",
                        "coordinates": [[0, 0], [60, 0], [60, 60], [0, 60]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_inspect",
                        "model_id": "model_inspect",
                        "labels": ["person"],
                        "region_ids": ["region_inspect"],
                    }
                ],
            }
        )

        snapshot = await self.service.get_task_metadata_snapshot(task["id"])

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["task"]["id"], task["id"])
        self.assertEqual(snapshot["task"]["algorithm_count"], 1)
        self.assertEqual(snapshot["task"]["region_count"], 1)
        self.assertEqual(len(snapshot["algorithms"]), 1)
        self.assertEqual(snapshot["algorithms"][0]["id"], "algo_inspect")
        self.assertEqual(len(snapshot["regions"]), 1)
        self.assertEqual(snapshot["regions"][0]["id"], "region_inspect")
        self.assertEqual(snapshot["links"], [{"algorithm_id": "algo_inspect", "region_id": "region_inspect"}])

    async def test_resync_task_metadata_refreshes_cache_from_db_without_restoring_deleted_rows(self):
        task = await self.service.create_task(
            {
                "name": "resync-task",
                "source_url": "rtsp://example/live/14",
                "channel_id": "ch_resync",
                "regions": [
                    {
                        "id": "region_resync",
                        "name": "resync-zone",
                        "coordinates": [[0, 0], [80, 0], [80, 80], [0, 80]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_resync",
                        "model_id": "model_resync",
                        "region_ids": ["region_resync"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == task["id"]).delete()
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == task["id"]).delete()
            session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == task["id"]).delete()
            session.query(TaskMeta).filter(TaskMeta.id == task["id"]).update(
                {
                    "algorithm_count": 0,
                    "region_count": 0,
                }
            )
            session.commit()

        result = await self.service.resync_task_metadata(task["id"])

        self.assertTrue(result["ok"])
        self.assertEqual(result["task_id"], task["id"])
        self.assertEqual(result["source"], "metadata")

        with self.session_local() as session:
            task_row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one_or_none()
            algorithm_rows = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.task_id == task["id"])
            ).scalars().all()
            region_rows = session.execute(
                select(TaskRegionMeta).where(TaskRegionMeta.task_id == task["id"])
            ).scalars().all()
            link_rows = session.execute(
                select(TaskAlgorithmRegionMeta).where(TaskAlgorithmRegionMeta.task_id == task["id"])
            ).scalars().all()

        self.assertIsNotNone(task_row)
        self.assertEqual(len(algorithm_rows), 0)
        self.assertEqual(len(region_rows), 0)
        self.assertEqual(len(link_rows), 0)

        cached_task = json.loads((self.tasks_dir / f"{task['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(cached_task["algorithms"], [])
        self.assertEqual(cached_task["regions"], [])

    async def test_resync_all_task_metadata_refreshes_existing_db_tasks_only(self):
        first_task = await self.service.create_task(
            {
                "name": "resync-all-task-1",
                "source_url": "rtsp://example/live/15",
                "regions": [
                    {
                        "id": "region_all_1",
                        "name": "all-zone-1",
                        "coordinates": [[0, 0], [20, 0], [20, 20], [0, 20]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_all_1",
                        "model_id": "model_all_1",
                        "region_ids": ["region_all_1"],
                    }
                ],
            }
        )
        second_task = await self.service.create_task(
            {
                "name": "resync-all-task-2",
                "source_url": "rtsp://example/live/16",
                "regions": [
                    {
                        "id": "region_all_2",
                        "name": "all-zone-2",
                        "coordinates": [[0, 0], [30, 0], [30, 30], [0, 30]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_all_2",
                        "model_id": "model_all_2",
                        "region_ids": ["region_all_2"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == first_task["id"]).delete()
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == first_task["id"]).delete()
            session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == first_task["id"]).delete()
            session.query(TaskMeta).filter(TaskMeta.id == first_task["id"]).update(
                {
                    "algorithm_count": 0,
                    "region_count": 0,
                }
            )
            session.commit()

        result = await self.service.resync_all_task_metadata()

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["synced"], 2)
        self.assertEqual(result["failed"], [])

        with self.session_local() as session:
            task_ids = session.execute(select(TaskMeta.id)).scalars().all()
            algorithm_ids = session.execute(select(TaskAlgorithmMeta.id)).scalars().all()
            region_ids = session.execute(select(TaskRegionMeta.id)).scalars().all()
            link_pairs = session.execute(
                select(TaskAlgorithmRegionMeta.algorithm_id, TaskAlgorithmRegionMeta.region_id)
            ).all()

        self.assertEqual(sorted(task_ids), sorted([first_task["id"], second_task["id"]]))
        self.assertEqual(sorted(algorithm_ids), ["algo_all_2"])
        self.assertEqual(sorted(region_ids), ["region_all_2"])
        self.assertEqual(
            sorted(link_pairs),
            [("algo_all_2", "region_all_2")],
        )

        first_cached = json.loads((self.tasks_dir / f"{first_task['id']}.json").read_text(encoding="utf-8"))
        second_cached = json.loads((self.tasks_dir / f"{second_task['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(first_cached["algorithms"], [])
        self.assertEqual([item["id"] for item in second_cached["algorithms"]], ["algo_all_2"])

    async def test_get_task_from_metadata_reconstructs_task_shape(self):
        task = await self.service.create_task(
            {
                "name": "metadata-view-task",
                "source_url": "rtsp://example/live/17",
                "source_type": "rtsp",
                "channel_id": "ch_metadata_view",
                "frame_interval": 9,
                "regions": [
                    {
                        "id": "region_meta_1",
                        "name": "meta-zone-1",
                        "color": "#ff4d4f",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    },
                    {
                        "id": "region_meta_2",
                        "name": "meta-zone-2",
                        "coordinates": [[120, 0], [220, 0], [220, 100], [120, 100]],
                    },
                ],
                "algorithms": [
                    {
                        "id": "algo_meta_1",
                        "algorithm_type": "detection",
                        "model_id": "model_meta_1",
                        "enabled": True,
                        "labels": ["person"],
                        "confidence": 0.45,
                        "alert_interval": 30,
                        "need_snapshot": True,
                        "need_video_clip": True,
                        "need_overlay": True,
                        "overlay_color": "#22c55e",
                        "region_ids": ["region_meta_1", "region_meta_2"],
                    }
                ],
            }
        )

        reconstructed = await self.service.get_task_from_metadata(task["id"])

        self.assertIsNotNone(reconstructed)
        self.assertEqual(reconstructed["id"], task["id"])
        self.assertEqual(reconstructed["name"], "metadata-view-task")
        self.assertEqual(reconstructed["source_url"], "rtsp://example/live/17")
        self.assertEqual(reconstructed["channel_id"], "ch_metadata_view")
        self.assertEqual(len(reconstructed["regions"]), 2)
        self.assertEqual(len(reconstructed["algorithms"]), 1)
        self.assertEqual(reconstructed["algorithms"][0]["id"], "algo_meta_1")
        self.assertEqual(reconstructed["algorithms"][0]["model_id"], "model_meta_1")
        self.assertEqual(reconstructed["algorithms"][0]["labels"], ["person"])
        self.assertEqual(reconstructed["algorithms"][0]["region_ids"], ["region_meta_1", "region_meta_2"])
        self.assertEqual(reconstructed["algorithms"][0]["regions"][0]["id"], "region_meta_1")
        self.assertEqual(reconstructed["algorithms"][0]["regions"][1]["id"], "region_meta_2")

    async def test_compare_task_views_reports_json_and_metadata_alignment(self):
        task = await self.service.create_task(
            {
                "name": "compare-view-task",
                "source_url": "rtsp://example/live/18",
                "source_type": "rtsp",
                "channel_id": "ch_compare",
                "frame_interval": 7,
                "regions": [
                    {
                        "id": "region_compare",
                        "name": "compare-zone",
                        "coordinates": [[0, 0], [40, 0], [40, 40], [0, 40]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_compare",
                        "model_id": "model_compare",
                        "labels": ["helmet"],
                        "confidence": 0.5,
                        "region_ids": ["region_compare"],
                    }
                ],
            }
        )

        comparison = await self.service.compare_task_views(task["id"])

        self.assertIsNotNone(comparison)
        self.assertTrue(comparison["match"])
        self.assertEqual(comparison["json_view"]["id"], task["id"])
        self.assertEqual(comparison["metadata_view"]["id"], task["id"])
        self.assertEqual(comparison["differences"], [])

    async def test_get_task_can_prefer_metadata_view(self):
        task = await self.service.create_task(
            {
                "name": "prefer-metadata-task",
                "source_url": "rtsp://example/live/19",
                "source_type": "rtsp",
                "channel_id": "ch_prefer_metadata",
                "regions": [
                    {
                        "id": "region_prefer",
                        "name": "prefer-zone",
                        "coordinates": [[0, 0], [30, 0], [30, 30], [0, 30]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_prefer",
                        "model_id": "model_prefer",
                        "labels": ["person"],
                        "region_ids": ["region_prefer"],
                    }
                ],
            }
        )

        metadata_task = await self.service.get_task(task["id"], prefer_metadata=True)

        self.assertIsNotNone(metadata_task)
        self.assertEqual(metadata_task["id"], task["id"])
        self.assertEqual(metadata_task["algorithms"][0]["id"], "algo_prefer")
        self.assertEqual(metadata_task["algorithms"][0]["regions"][0]["id"], "region_prefer")

    async def test_get_task_prefer_metadata_falls_back_to_json_when_metadata_missing(self):
        task = await self.service.create_task(
            {
                "name": "prefer-metadata-fallback-task",
                "source_url": "rtsp://example/live/20",
                "algorithms": [
                    {
                        "id": "algo_fallback",
                        "model_id": "model_fallback",
                    }
                ],
            }
        )

        with self.session_local() as session:
            session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == task["id"]).delete()
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == task["id"]).delete()
            session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == task["id"]).delete()
            session.query(TaskMeta).filter(TaskMeta.id == task["id"]).delete()
            session.commit()

        fallback_task = await self.service.get_task(task["id"], prefer_metadata=True)

        self.assertIsNotNone(fallback_task)
        self.assertEqual(fallback_task["id"], task["id"])
        self.assertEqual(fallback_task["algorithms"][0]["id"], "algo_fallback")

    async def test_get_task_prefers_db_over_stale_json_when_algorithm_deleted_from_db(self):
        task = await self.service.create_task(
            {
                "name": "db-first-delete-algorithm-task",
                "source_url": "rtsp://example/live/db-delete",
                "channel_id": "ch_db_delete",
                "algorithms": [
                    {
                        "id": "algo_deleted_in_db",
                        "model_id": "model_deleted_in_db",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == task["id"]).delete()
            session.query(TaskMeta).filter(TaskMeta.id == task["id"]).update(
                {
                    "algorithm_count": 0,
                    "region_count": 0,
                }
            )
            session.commit()

        reloaded = await self.service.get_task(task["id"])

        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["id"], task["id"])
        self.assertEqual(reloaded["algorithms"], [])
        self.assertEqual(reloaded["regions"], [])

    async def test_list_tasks_can_prefer_metadata_view(self):
        first_task = await self.service.create_task(
            {
                "name": "metadata-list-task-1",
                "source_url": "rtsp://example/live/21",
                "channel_id": "ch_list_1",
                "algorithms": [
                    {
                        "id": "algo_list_1",
                        "model_id": "model_list_1",
                    }
                ],
            }
        )
        second_task = await self.service.create_task(
            {
                "name": "metadata-list-task-2",
                "source_url": "rtsp://example/live/22",
                "channel_id": "ch_list_2",
                "algorithms": [
                    {
                        "id": "algo_list_2",
                        "model_id": "model_list_2",
                    }
                ],
            }
        )

        result = await self.service.list_tasks(prefer_metadata=True)

        self.assertEqual(len(result["items"]), 2)
        returned_ids = [item["id"] for item in result["items"]]
        self.assertEqual(returned_ids, [second_task["id"], first_task["id"]])
        self.assertEqual(result["items"][0]["algorithms"][0]["id"], "algo_list_2")
        self.assertEqual(result["items"][1]["algorithms"][0]["id"], "algo_list_1")

    async def test_list_tasks_prefer_metadata_ignores_stale_rows_without_json(self):
        first_task = await self.service.create_task(
            {
                "name": "metadata-stale-task-1",
                "source_url": "rtsp://example/live/31",
                "channel_id": "ch_stale_1",
                "algorithms": [
                    {
                        "id": "algo_stale_1",
                        "model_id": "model_stale_1",
                    }
                ],
            }
        )
        second_task = await self.service.create_task(
            {
                "name": "metadata-stale-task-2",
                "source_url": "rtsp://example/live/32",
                "channel_id": "ch_stale_2",
                "algorithms": [
                    {
                        "id": "algo_stale_2",
                        "model_id": "model_stale_2",
                    }
                ],
            }
        )

        (self.tasks_dir / f"{second_task['id']}.json").unlink()

        result = await self.service.list_tasks(prefer_metadata=True)

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["id"], first_task["id"])

    async def test_analysis_service_can_load_task_from_metadata_when_json_missing(self):
        task = await self.service.create_task(
            {
                "name": "analysis-metadata-task",
                "source_url": "rtsp://example/live/23",
                "channel_id": "ch_analysis_metadata",
                "regions": [
                    {
                        "id": "region_analysis",
                        "name": "analysis-zone",
                        "coordinates": [[0, 0], [50, 0], [50, 50], [0, 50]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_analysis",
                        "model_id": "model_analysis",
                        "labels": ["person"],
                        "region_ids": ["region_analysis"],
                    }
                ],
            }
        )

        (self.tasks_dir / f"{task['id']}.json").unlink()

        service = AnalysisService()
        loaded = service._load_task(task["id"])

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["id"], task["id"])
        self.assertEqual(loaded["algorithms"][0]["id"], "algo_analysis")
        self.assertEqual(loaded["algorithms"][0]["region_ids"], ["region_analysis"])
        self.assertEqual(loaded["regions"][0]["id"], "region_analysis")

    async def test_analysis_service_does_not_fallback_to_stale_json_when_metadata_row_is_deleted(self):
        task = await self.service.create_task(
            {
                "name": "analysis-db-only-task",
                "source_url": "rtsp://example/live/23-db-only",
                "channel_id": "ch_analysis_db_only",
                "algorithms": [
                    {
                        "id": "algo_analysis_db_only",
                        "model_id": "model_analysis_db_only",
                        "labels": ["person"],
                    }
                ],
            }
        )

        service = AnalysisService()
        self.assertIsNotNone(service._load_task(task["id"]))

        with self.session_local() as session:
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == task["id"]).delete()
            session.query(TaskMeta).filter(TaskMeta.id == task["id"]).delete()
            session.commit()

        stale_loaded = service._load_task(task["id"])

        self.assertIsNone(stale_loaded)

    async def test_analysis_service_updates_channel_runtime_in_metadata_row(self):
        channel_id = "ch_analysis_runtime_db"
        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id=channel_id,
                    device_id=None,
                    media_node_id=None,
                    name="analysis-runtime-db",
                    type="rtsp",
                    source_url="rtsp://example/live/channel-runtime",
                    app="live",
                    stream="channel_runtime_db",
                    is_online=True,
                    is_playing=False,
                    ai_enabled=True,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api=None,
                    hls=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": channel_id,
                        "name": "analysis-runtime-db",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/channel-runtime",
                        "app": "live",
                        "stream": "channel_runtime_db",
                        "is_online": True,
                        "is_playing": False,
                        "ai_enabled": True,
                        "record_mode": "none",
                        "ext": {
                            "ai_config": {
                                "enabled": True,
                                "models": [],
                            }
                        },
                    },
                )
            )
            session.commit()

        service = AnalysisService()
        service._update_runtime(
            channel_id,
            status="running",
            last_analysis_at="2026-06-23 10:00:00",
            frame_count=12,
            event_count=3,
        )

        with self.session_local() as session:
            row = session.query(ChannelMeta).filter(ChannelMeta.id == channel_id).one()
            runtime = ((row.raw_json or {}).get("ext") or {}).get("ai_runtime") or {}

        self.assertEqual(runtime.get("status"), "running")
        self.assertEqual(runtime.get("last_analysis_at"), "2026-06-23 10:00:00")
        self.assertEqual(runtime.get("frame_count"), 12)
        self.assertEqual(runtime.get("event_count"), 3)

    async def test_start_task_can_use_metadata_when_json_missing(self):
        task = await self.service.create_task(
            {
                "name": "start-metadata-task",
                "source_url": "rtsp://example/live/24",
                "channel_id": "ch_start_metadata",
                "algorithms": [
                    {
                        "id": "algo_start_metadata",
                        "model_id": "model_start_metadata",
                        "labels": ["person"],
                    }
                ],
            }
        )

        (self.tasks_dir / f"{task['id']}.json").unlink()

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start:
            result = await self.service.start_task(task["id"])

        self.assertEqual(result["status"], "starting")
        mocked_start.assert_awaited_once_with(task["id"])

    async def test_task_analysis_status_converges_stopping_to_stopped_when_worker_is_gone(self):
        task = await self.service.create_task(
            {
                "name": "stopping-converges-task",
                "source_url": "rtsp://example/live/stop-converge",
                "channel_id": "ch_stop_converge",
                "algorithms": [
                    {
                        "id": "algo_stop_converge",
                        "model_id": "model_stop_converge",
                        "labels": ["person"],
                    }
                ],
            }
        )

        current = await self.service.get_task(task["id"])
        current["status"] = "stopping"
        current["runtime"]["status"] = "stopping"
        current["runtime"]["is_running"] = True
        current["runtime"]["started_at"] = "2026-06-12 13:00:00"
        current["runtime"]["stopped_at"] = "2026-06-12 13:05:00"

        from src.services.platform_storage import save_json
        save_json(self.tasks_dir / f"{task['id']}.json", current)

        service = AnalysisService()
        status = await service.get_task_analysis_status(task["id"])

        self.assertEqual(status["status"], "stopped")
        self.assertFalse(status["is_running"])

        reloaded = service._load_task(task["id"])
        self.assertEqual(reloaded["status"], "stopped")
        self.assertEqual(reloaded["runtime"]["status"], "stopped")
        self.assertFalse(reloaded["runtime"]["is_running"])

    async def test_task_analysis_clears_stale_stream_error_after_recovery(self):
        task = await self.service.create_task(
            {
                "name": "recover-stale-error-task",
                "source_url": "rtsp://example/live/recover-stale",
                "channel_id": "ch_recover_stale",
                "frame_interval": 1,
                "algorithms": [
                    {
                        "id": "algo_recover_stale",
                        "model_id": "model_recover_stale",
                        "labels": ["person"],
                        "enabled": True,
                    }
                ],
            }
        )

        service = AnalysisService()
        stop_event = threading.Event()
        first_capture = object()
        second_capture = object()

        class _FakeFrame:
            def copy(self):
                return self

        failure_reads = {"count": 0}
        recovered = {"done": False}

        def _fake_read(capture):
            if capture is first_capture and failure_reads["count"] < 10:
                failure_reads["count"] += 1
                return False, None
            if capture is second_capture and not recovered["done"]:
                recovered["done"] = True
                stop_event.set()
                return True, _FakeFrame()
            stop_event.set()
            return False, None

        with (
            patch.object(service, "_build_task_capture", side_effect=[first_capture, second_capture]),
            patch.object(service, "_read_task_frame", side_effect=_fake_read),
            patch.object(service, "_release_task_capture"),
            patch.object(service, "_load_model", return_value=(object(), {"classes": ["person"]})),
            patch.object(service, "_detect_frame", return_value=[]),
            patch.object(service, "_apply_algorithm_scoped_filters", return_value=[]),
            patch("src.services.analysis_service.time.sleep", lambda *_args, **_kwargs: None),
        ):
            service._run_task_worker(task["id"], stop_event)

        reloaded = service._load_task(task["id"])
        self.assertIsNotNone(reloaded)
        self.assertIsNone((reloaded.get("runtime") or {}).get("last_error"))

    async def test_event_service_can_list_events_from_metadata_with_task_and_algorithm_filters(self):
        service = EventService()
        first_event = await service.create_event(
            {
                "channel_id": "ch_events",
                "task_id": "task_alpha",
                "algorithm_id": "algo_person",
                "model_id": "model_person",
                "event_type": "detection",
                "class_name": "person",
                "conf": 0.81,
                "snapshot_path": "/app/data/snapshots/a.jpg",
                "handling_status": 0,
                "status": "new",
            }
        )
        await service.create_event(
            {
                "channel_id": "ch_events",
                "task_id": "task_beta",
                "algorithm_id": "algo_helmet",
                "model_id": "model_helmet",
                "event_type": "detection",
                "class_name": "helmet",
                "conf": 0.62,
                "snapshot_path": "/app/data/snapshots/b.jpg",
                "handling_status": 0,
                "status": "new",
            }
        )

        for path in self.events_dir.glob("*.json"):
            path.unlink()

        result = await service.list_events(task_id="task_alpha", algorithm_id="algo_person", page=1, page_size=10)

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 10)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["id"], first_event["id"])
        self.assertEqual(result["items"][0]["task_id"], "task_alpha")
        self.assertEqual(result["items"][0]["algorithm_id"], "algo_person")
        self.assertEqual(result["items"][0]["handling_status"], 0)

        with self.session_local() as session:
            rows = session.execute(select(EventMeta).where(EventMeta.task_id == "task_alpha")).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].handling_status, 0)

    async def test_recording_service_can_list_recordings_from_metadata_with_task_and_algorithm_filters(self):
        service = RecordingService()
        first = await service.create_recording(
            {
                "id": "rec_alpha",
                "channel_id": "ch_recordings",
                "task_id": "task_alpha",
                "algorithm_id": "algo_person",
                "started_at": "2026-06-05T10:00:00",
                "ended_at": "2026-06-05T10:00:06",
                "duration": 6,
                "file_path": "/app/data/recordings/alpha.mp4",
                "status": "ready",
                "raw_json": {
                    "event_id": "evt_alpha",
                    "event_type": "detection",
                    "event_type_label": "alarm_clip",
                    "class_name": "person",
                    "conf": 0.81,
                    "snapshot_path": "/app/data/snapshots/alpha.jpg",
                },
            }
        )
        await service.create_recording(
            {
                "id": "rec_beta",
                "channel_id": "ch_recordings",
                "task_id": "task_beta",
                "algorithm_id": "algo_helmet",
                "started_at": "2026-06-05T10:01:00",
                "ended_at": "2026-06-05T10:01:06",
                "duration": 6,
                "file_path": "/app/data/recordings/beta.mp4",
                "status": "ready",
                "raw_json": {
                    "event_id": "evt_beta",
                    "event_type": "alarm_clip",
                    "snapshot_path": "/app/data/snapshots/beta.jpg",
                },
            }
        )

        for path in self.recordings_dir.glob("*.json"):
            path.unlink()

        result = await service.list_recordings(
            channel_id="ch_recordings",
            task_id="task_alpha",
            algorithm_id="algo_person",
            page=1,
            page_size=10,
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 10)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["id"], first["id"])
        self.assertEqual(result["items"][0]["task_id"], "task_alpha")
        self.assertEqual(result["items"][0]["algorithm_id"], "algo_person")
        self.assertEqual(result["items"][0]["event_id"], "evt_alpha")
        self.assertEqual(result["items"][0]["event_type"], "detection")
        self.assertEqual(result["items"][0]["event_type_label"], "alarm_clip")
        self.assertEqual(result["items"][0]["class_name"], "person")
        self.assertEqual(result["items"][0]["conf"], 0.81)

        with self.session_local() as session:
            rows = session.execute(select(RecordingMeta).where(RecordingMeta.task_id == "task_alpha")).scalars().all()
        self.assertEqual(len(rows), 1)

    async def test_add_algorithm_and_region_to_task(self):
        task = await self.service.create_task(
            {
                "name": "region-detect-task",
                "source_url": "rtsp://example/live/3",
                "algorithms": [],
            }
        )

        algorithm = await self.service.add_algorithm(
            task["id"],
            {
                "model_id": "model_person",
                "labels": ["person"],
                "regions": [
                    {
                        "name": "entry-zone",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
            },
        )

        current = await self.service.get_task(task["id"])
        self.assertEqual(algorithm["model_id"], "model_person")
        self.assertEqual(len(current["algorithms"]), 1)
        self.assertEqual(current["algorithms"][0]["regions"][0]["name"], "entry-zone")

    async def test_add_algorithm_hydrates_sleep_scene_name_from_model(self):
        with patch.object(self.service, "_resolve_channel_for_task", AsyncMock(return_value=None)):
            task = await self.service.create_task(
                {
                    "name": "sleep-scene-task",
                    "channel_id": "ch_sleep_scene",
                    "source_url": "rtsp://example/live/sleep-scene",
                    "algorithms": [],
                }
            )

        with patch(
            "src.services.task_service.model_service.get_model",
            AsyncMock(
                return_value={
                    "model_id": "model_sleep",
                    "name": "睡岗检测",
                    "classes": ["normal", "sleep"],
                    "scene_candidates": [
                        {
                            "scene_key": "sleep_on_duty",
                            "scene_name": "睡岗检测",
                            "recommended_labels": ["sleep"],
                            "rule_type": "detection",
                            "default_need_snapshot": True,
                            "default_need_video_clip": True,
                            "requires_region": True,
                        }
                    ],
                }
            ),
        ):
            algorithm = await self.service.add_algorithm(task["id"], {"model_id": "model_sleep"})

        self.assertEqual(algorithm["scene_key"], "sleep_on_duty")
        self.assertEqual(algorithm["scene_name"], "睡岗检测")
        self.assertEqual(algorithm["rule_type"], "detection")
        self.assertEqual(algorithm["labels"], ["sleep"])
        self.assertEqual(algorithm["label_source"], "recommended")
        self.assertTrue(algorithm["requires_region"])

        with self.session_local() as session:
            row = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.id == algorithm["id"])
            ).scalar_one()
        self.assertEqual(row.scene_key, "sleep_on_duty")
        self.assertEqual(row.scene_name, "睡岗检测")
        self.assertEqual(row.rule_type, "detection")
        self.assertEqual(row.labels_json, ["sleep"])

    async def test_add_algorithm_rejects_duplicate_model_in_same_task(self):
        task = await self.service.create_task(
            {
                "name": "duplicate-model-task",
                "source_url": "rtsp://example/live/duplicate",
                "algorithms": [
                    {
                        "id": "algo_person",
                        "model_id": "model_person",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with self.assertRaisesRegex(ValueError, "already exists in task"):
            await self.service.add_algorithm(
                task["id"],
                {
                    "model_id": "model_person",
                    "labels": ["person"],
                },
            )

    async def test_list_task_algorithms_returns_only_task_algorithms(self):
        task = await self.service.create_task(
            {
                "name": "algorithm-list-task",
                "source_url": "rtsp://example/live/algorithms",
                "algorithms": [
                    {
                        "id": "algo_person",
                        "model_id": "model_person",
                        "labels": ["person"],
                    },
                    {
                        "id": "algo_helmet",
                        "model_id": "model_helmet",
                        "labels": ["helmet"],
                    },
                ],
            }
        )

        result = await self.service.list_task_algorithms(task["id"])

        self.assertIsNotNone(result)
        self.assertEqual([item["id"] for item in result["items"]], ["algo_person", "algo_helmet"])
        self.assertEqual([item["model_id"] for item in result["items"]], ["model_person", "model_helmet"])

    async def test_list_task_algorithms_does_not_restore_db_deleted_algorithm_from_json(self):
        task = await self.service.create_task(
            {
                "name": "algorithm-db-first-task",
                "source_url": "rtsp://example/live/algorithms-db-first",
                "channel_id": "ch_algorithms_db_first",
                "algorithms": [
                    {
                        "id": "algo_db_only",
                        "model_id": "model_db_only",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == task["id"]).delete()
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == task["id"]).delete()
            session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == task["id"]).delete()
            session.query(TaskMeta).filter(TaskMeta.id == task["id"]).update(
                {
                    "algorithm_count": 0,
                    "region_count": 0,
                }
            )
            session.commit()

        result = await self.service.list_task_algorithms(task["id"])

        self.assertIsNotNone(result)
        self.assertEqual(result["items"], [])

        with self.session_local() as session:
            rows = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.task_id == task["id"])
            ).scalars().all()

        self.assertEqual(rows, [])

    async def test_delete_algorithm_prunes_runtime_models_from_metadata_view(self):
        task = await self.service.create_task(
            {
                "name": "algorithm-runtime-prune-task",
                "source_url": "rtsp://example/live/algorithms-runtime-prune",
                "channel_id": "ch_algorithms_runtime_prune",
                "algorithms": [
                    {
                        "id": "algo_keep",
                        "model_id": "model_keep",
                        "labels": ["person"],
                    },
                    {
                        "id": "algo_drop",
                        "model_id": "model_drop",
                        "labels": ["helmet"],
                    },
                ],
            }
        )

        runtime_snapshot = {
            "status": "stopped",
            "is_running": False,
            "frame_count": 12,
            "event_count": 2,
            "models": {
                "algo_keep": {
                    "algorithm_id": "algo_keep",
                    "model_id": "model_keep",
                    "detections": 1,
                },
                "algo_drop": {
                    "algorithm_id": "algo_drop",
                    "model_id": "model_drop",
                    "detections": 3,
                },
            },
        }

        with self.session_local() as session:
            row = session.query(TaskMeta).filter(TaskMeta.id == task["id"]).one()
            raw_json = dict(row.raw_json or {})
            raw_json["runtime"] = runtime_snapshot
            row.raw_json = raw_json
            session.commit()

        await self.service.delete_algorithm(task["id"], "algo_drop")

        metadata_task = await self.service.get_task_from_metadata(task["id"])

        self.assertIsNotNone(metadata_task)
        self.assertEqual([item["id"] for item in metadata_task["algorithms"]], ["algo_keep"])
        self.assertIn("algo_keep", metadata_task["runtime"]["models"])
        self.assertNotIn("algo_drop", metadata_task["runtime"]["models"])

        with self.session_local() as session:
            row = session.query(TaskMeta).filter(TaskMeta.id == task["id"]).one()
            runtime_models = ((row.raw_json or {}).get("runtime") or {}).get("models") or {}

        self.assertIn("algo_keep", runtime_models)
        self.assertNotIn("algo_drop", runtime_models)

    async def test_task_json_sync_entrypoint_does_not_restore_db_deleted_algorithm(self):
        task = await self.service.create_task(
            {
                "name": "algorithm-sync-entrypoint-task",
                "source_url": "rtsp://example/live/algorithms-sync-entrypoint",
                "channel_id": "ch_algorithms_sync_entrypoint",
                "algorithms": [
                    {
                        "id": "algo_sync_entrypoint",
                        "model_id": "model_sync_entrypoint",
                        "labels": ["person"],
                    }
                ],
            }
        )

        task_path = self.tasks_dir / f"{task['id']}.json"
        with self.session_local() as session:
            session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == task["id"]).delete()
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == task["id"]).delete()
            session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == task["id"]).delete()
            session.query(TaskMeta).filter(TaskMeta.id == task["id"]).update(
                {
                    "algorithm_count": 0,
                    "region_count": 0,
                }
            )
            session.commit()

        synced = metadata_db_sync.sync_single_metadata_file(task_path)

        self.assertFalse(synced)
        with self.session_local() as session:
            rows = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.task_id == task["id"])
            ).scalars().all()

        self.assertEqual(rows, [])

    async def test_list_task_available_models_filters_bound_models(self):
        registry_dir = Path(settings.REGISTRY_DIR)
        original_registry_dir = settings.REGISTRY_DIR
        try:
            settings.REGISTRY_DIR = Path(self.temp_dir.name) / "models_registry"
            settings.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

            for model_id, model_name in [
                ("model_person", "人员检测"),
                ("model_helmet", "安全帽检测"),
                ("model_smoke", "烟雾检测"),
            ]:
                model_dir = settings.REGISTRY_DIR / model_id
                model_dir.mkdir(parents=True, exist_ok=True)
                (model_dir / "model.json").write_text(
                    json.dumps(
                        {
                            "model_id": model_id,
                            "name": model_name,
                            "classes": ["person"],
                            "created_at": "2026-06-09 10:00:00",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

            task = await self.service.create_task(
                {
                    "name": "available-models-task",
                    "source_url": "rtsp://example/live/models",
                    "algorithms": [
                        {
                            "id": "algo_person",
                            "model_id": "model_person",
                            "labels": ["person"],
                        }
                    ],
                }
            )

            result = await self.service.list_task_available_models(task["id"])

            self.assertIsNotNone(result)
            self.assertEqual([item["model_id"] for item in result["items"]], ["model_helmet", "model_smoke"])
            self.assertTrue(all(item["algorithm_type"] == "detection" for item in result["items"]))
            self.assertTrue(all("rule_type" in item for item in result["items"]))
            self.assertTrue(all("need_snapshot" in item for item in result["items"]))
            self.assertTrue(all("need_video_clip" in item for item in result["items"]))
            self.assertTrue(all("need_overlay" in item for item in result["items"]))
            self.assertTrue(all("requires_region" in item for item in result["items"]))
        finally:
            settings.REGISTRY_DIR = original_registry_dir

    async def test_add_algorithm_inherits_model_default_confidence_and_alert_interval(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="model_defaults_task",
                    name="task-default-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=0.65,
                    alert_interval=25,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "model_defaults_task",
                        "name": "task-default-model",
                        "classes": ["person"],
                        "confidence": 0.65,
                        "alert_interval": 25,
                    },
                )
            )
            session.commit()

        task = await self.service.create_task(
            {
                "name": "inherit-default-task",
                "channel_id": "ch_inherit_defaults",
                "source_url": "rtsp://example/live/inherit-defaults",
                "algorithms": [],
            }
        )

        algorithm = await self.service.add_algorithm(
            task["id"],
            {
                "model_id": "model_defaults_task",
                "labels": ["person"],
            },
        )

        self.assertAlmostEqual(algorithm["confidence"], 0.65)
        self.assertEqual(algorithm["alert_interval"], 25)

    async def test_add_algorithm_keeps_explicit_confidence_and_alert_interval_override(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="model_override_task",
                    name="task-override-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=0.7,
                    alert_interval=40,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "model_override_task",
                        "name": "task-override-model",
                        "classes": ["person"],
                        "confidence": 0.7,
                        "alert_interval": 40,
                    },
                )
            )
            session.commit()

        task = await self.service.create_task(
            {
                "name": "override-default-task",
                "channel_id": "ch_override_defaults",
                "source_url": "rtsp://example/live/override-defaults",
                "algorithms": [],
            }
        )

        algorithm = await self.service.add_algorithm(
            task["id"],
            {
                "model_id": "model_override_task",
                "labels": ["person"],
                "confidence": 0.5,
                "alert_interval": 12,
            },
        )

        self.assertAlmostEqual(algorithm["confidence"], 0.5)
        self.assertEqual(algorithm["alert_interval"], 12)

    async def test_get_task_from_metadata_prefers_task_algorithm_thresholds_over_model_defaults(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="model_runtime_authority",
                    name="runtime-authority-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=0.8,
                    alert_interval=99,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "model_runtime_authority",
                        "name": "runtime-authority-model",
                        "classes": ["person"],
                        "confidence": 0.8,
                        "alert_interval": 99,
                    },
                )
            )
            session.commit()

        task = await self.service.create_task(
            {
                "name": "runtime-authority-task",
                "channel_id": "ch_runtime_authority",
                "source_url": "rtsp://example/live/runtime-authority",
                "algorithms": [
                    {
                        "id": "algo_runtime_authority",
                        "model_id": "model_runtime_authority",
                        "labels": ["person"],
                        "confidence": 0.42,
                        "alert_interval": 18,
                    }
                ],
            }
        )

        metadata_task = await self.service.get_task_from_metadata(task["id"])
        algorithm = metadata_task["algorithms"][0]

        self.assertAlmostEqual(algorithm["confidence"], 0.42)
        self.assertEqual(algorithm["alert_interval"], 18)

    async def test_algorithm_regions_are_private_to_each_algorithm(self):
        task = await self.service.create_task(
            {
                "name": "private-algorithm-regions",
                "source_url": "rtsp://example/live/6",
                "algorithms": [
                    {"model_id": "model_person", "need_overlay": True},
                    {"model_id": "model_helmet", "need_overlay": True},
                ],
            }
        )

        first_algorithm = task["algorithms"][0]
        second_algorithm = task["algorithms"][1]

        first_result = await self.service.replace_regions(
            task["id"],
            first_algorithm["id"],
            {
                "regions": [
                    {
                        "name": "person-zone",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
            },
        )
        second_result = await self.service.replace_regions(
            task["id"],
            second_algorithm["id"],
            {
                "regions": [
                    {
                        "name": "helmet-zone",
                        "coordinates": [[20, 20], [120, 20], [120, 120], [20, 120]],
                    }
                ],
            },
        )

        self.assertIsNotNone(first_result)
        self.assertIsNotNone(second_result)
        first_region = first_result["items"][0]
        second_region = second_result["items"][0]
        self.assertNotEqual(first_region["id"], second_region["id"])

        first_items = await self.service.list_algorithm_regions(task["id"], first_algorithm["id"])
        second_items = await self.service.list_algorithm_regions(task["id"], second_algorithm["id"])

        self.assertEqual([item["id"] for item in first_items["items"]], [first_region["id"]])
        self.assertEqual([item["id"] for item in second_items["items"]], [second_region["id"]])

        current = await self.service.get_task(task["id"])
        self.assertEqual(current["algorithms"][0]["region_ids"], [first_region["id"]])
        self.assertEqual(current["algorithms"][1]["region_ids"], [second_region["id"]])
        self.assertEqual(len(current["algorithms"][0]["regions"]), 1)
        self.assertEqual(len(current["algorithms"][1]["regions"]), 1)

    async def test_algorithm_regions_with_same_public_id_are_isolated_per_algorithm(self):
        task = await self.service.create_task(
            {
                "name": "private-algorithm-regions-same-id",
                "source_url": "rtsp://example/live/6b",
                "channel_id": "ch_private_algorithm_regions_same_id",
                "algorithms": [
                    {"id": "algo_person_same_zone", "model_id": "model_person", "need_overlay": True},
                    {"id": "algo_helmet_same_zone", "model_id": "model_helmet", "need_overlay": True},
                ],
            }
        )

        first_result = await self.service.replace_regions(
            task["id"],
            "algo_person_same_zone",
            {
                "regions": [
                    {
                        "id": "zone_1",
                        "name": "person-zone",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
            },
        )
        second_result = await self.service.replace_regions(
            task["id"],
            "algo_helmet_same_zone",
            {
                "regions": [
                    {
                        "id": "zone_1",
                        "name": "helmet-zone",
                        "coordinates": [[10, 10], [110, 10], [110, 110], [10, 110]],
                    }
                ],
            },
        )

        self.assertIsNotNone(first_result)
        self.assertIsNotNone(second_result)
        self.assertEqual([item["id"] for item in first_result["items"]], ["algo_person_same_zone_zone_1"])
        self.assertEqual([item["id"] for item in second_result["items"]], ["algo_helmet_same_zone_zone_1"])

        first_items = await self.service.list_algorithm_regions(task["id"], "algo_person_same_zone")
        second_items = await self.service.list_algorithm_regions(task["id"], "algo_helmet_same_zone")
        self.assertEqual([item["id"] for item in first_items["items"]], ["algo_person_same_zone_zone_1"])
        self.assertEqual([item["id"] for item in second_items["items"]], ["algo_helmet_same_zone_zone_1"])

    async def test_delete_algorithm_region_only_affects_current_algorithm(self):
        task = await self.service.create_task(
            {
                "name": "delete-private-algorithm-region",
                "source_url": "rtsp://example/live/delete-private",
                "algorithms": [
                    {"id": "algo_person_private", "model_id": "model_person", "need_overlay": True},
                    {"id": "algo_helmet_private", "model_id": "model_helmet", "need_overlay": True},
                ],
            }
        )

        first_result = await self.service.replace_regions(
            task["id"],
            "algo_person_private",
            {
                "regions": [
                    {
                        "id": "region_person_private",
                        "name": "person-zone",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
            },
        )
        second_result = await self.service.replace_regions(
            task["id"],
            "algo_helmet_private",
            {
                "regions": [
                    {
                        "id": "region_helmet_private",
                        "name": "helmet-zone",
                        "coordinates": [[10, 10], [110, 10], [110, 110], [10, 110]],
                    }
                ],
            },
        )

        self.assertIsNotNone(first_result)
        self.assertIsNotNone(second_result)
        self.assertEqual([item["id"] for item in first_result["items"]], ["region_person_private"])
        self.assertEqual([item["id"] for item in second_result["items"]], ["region_helmet_private"])

        deleted = await self.service.replace_regions(task["id"], "algo_person_private", {"regions": []})
        self.assertEqual(deleted["items"], [])

        first_items = await self.service.list_algorithm_regions(task["id"], "algo_person_private")
        second_items = await self.service.list_algorithm_regions(task["id"], "algo_helmet_private")
        self.assertEqual(first_items["items"], [])
        self.assertEqual([item["id"] for item in second_items["items"]], ["region_helmet_private"])

    async def test_start_task_auto_starts_zone_preview_when_overlay_algorithm_exists(self):
        task = await self.service.create_task(
            {
                "name": "auto-zone-preview-task",
                "source_url": "rtsp://example/live/7",
                "channel_id": "ch_demo",
                "algorithms": [
                    {
                        "model_id": "model_person",
                        "need_overlay": True,
                        "regions": [
                            {
                                "id": "region_demo",
                                "name": "demo-zone",
                                "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                            }
                        ],
                    }
                ],
            }
        )

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start:
            with patch("src.services.task_service.channel_service.start_zone_preview", AsyncMock(return_value={"status": "running"})) as mocked_zone_start:
                with patch("src.services.task_service.channel_service.play", AsyncMock(return_value={"status": "playing"})) as mocked_play:
                    result = await self.service.start_task(task["id"])

        self.assertEqual(result["status"], "starting")
        mocked_start.assert_awaited_once_with(task["id"])
        mocked_play.assert_awaited_once_with("ch_demo")
        mocked_zone_start.assert_awaited_once()

    async def test_start_task_without_regions_only_warms_channel_play(self):
        task = await self.service.create_task(
            {
                "name": "missing-region-zone-preview-task",
                "source_url": "rtsp://example/live/missing-region",
                "channel_id": "ch_demo_missing_region",
                "algorithms": [
                    {
                        "id": "algo_missing_region",
                        "model_id": "model_person",
                        "need_overlay": True,
                        "enabled": True,
                    }
                ],
            }
        )

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start:
            with patch("src.services.task_service.channel_service.start_zone_preview", AsyncMock(return_value={"status": "running"})) as mocked_zone_start:
                with (
                    patch("src.services.task_service.channel_service.play", AsyncMock(return_value={"status": "playing"})) as mocked_play,
                    patch("src.services.task_service.channel_service.refresh_channel_browser_cached_urls", AsyncMock(return_value={"hls": "http://localhost/hls", "webrtc_api": "http://localhost/webrtc"})) as mocked_refresh,
                ):
                    result = await self.service.start_task(task["id"])

        self.assertEqual(result["status"], "starting")
        mocked_start.assert_awaited_once_with(task["id"])
        mocked_play.assert_awaited_once_with("ch_demo_missing_region")
        mocked_refresh.assert_awaited_once_with("ch_demo_missing_region")
        mocked_zone_start.assert_not_awaited()

    async def test_start_task_persists_task_preview_mode_for_channel_play(self):
        task = await self.service.create_task(
            {
                "name": "persist-channel-preview-mode",
                "source_url": "rtsp://example/live/persist-channel-preview",
                "channel_id": "ch_demo_missing_region",
                "algorithms": [
                    {
                        "id": "algo_channel_preview_mode",
                        "model_id": "model_person",
                        "need_overlay": False,
                        "enabled": True,
                    }
                ],
            }
        )

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})):
            with (
                patch("src.services.task_service.channel_service.play", AsyncMock(return_value={"status": "playing"})),
                patch("src.services.task_service.channel_service.refresh_channel_browser_cached_urls", AsyncMock(return_value={"hls": "http://localhost/hls", "webrtc_api": "http://localhost/webrtc"})),
            ):
                await self.service.start_task(task["id"])

        saved_channel = json.loads((self.channels_dir / "ch_demo_missing_region.json").read_text(encoding="utf-8"))
        task_preview = ((saved_channel.get("ext") or {}).get("task_preview") or {})
        self.assertTrue(task_preview.get("prepared"))
        self.assertEqual(task_preview.get("mode"), "channel")
        self.assertEqual(task_preview.get("owner_task_id"), task["id"])

    async def test_start_task_returns_without_waiting_for_preview_warmup(self):
        task = await self.service.create_task(
            {
                "name": "non-blocking-warmup-task",
                "source_url": "rtsp://example/live/non-blocking",
                "channel_id": "ch_non_blocking",
                "algorithms": [
                    {
                        "id": "algo_non_blocking",
                        "model_id": "model_person",
                        "enabled": True,
                    }
                ],
            }
        )

        async def _slow_warmup(_task):
            await asyncio.sleep(0.5)
            return {"status": "running"}

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start:
            with patch.object(self.service, "_warmup_task_preview", side_effect=_slow_warmup) as mocked_warmup:
                started_at = time.perf_counter()
                result = await self.service.start_task(task["id"])
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                await asyncio.sleep(0.6)

        self.assertEqual(result["status"], "starting")
        self.assertLess(elapsed_ms, 300)
        mocked_start.assert_awaited_once_with(task["id"])
        mocked_warmup.assert_awaited_once()

    async def test_start_task_with_regions_waits_for_preview_warmup_before_return(self):
        task = await self.service.create_task(
            {
                "name": "blocking-zone-warmup-task",
                "source_url": "rtsp://example/live/blocking-zone",
                "channel_id": "ch_blocking_zone",
                "algorithms": [
                    {
                        "id": "algo_blocking_zone",
                        "model_id": "model_person",
                        "enabled": True,
                        "regions": [
                            {
                                "id": "region_blocking_zone",
                                "name": "blocking-zone",
                                "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                            }
                        ],
                    }
                ],
            }
        )

        async def _slow_warmup(_task):
            await asyncio.sleep(0.3)
            return {"status": "running", "resolved_mode": "zone"}

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start:
            with patch.object(self.service, "_warmup_task_preview", side_effect=_slow_warmup) as mocked_warmup:
                started_at = time.perf_counter()
                result = await self.service.start_task(task["id"])
                elapsed_ms = (time.perf_counter() - started_at) * 1000

        self.assertEqual(result["status"], "starting")
        self.assertGreaterEqual(elapsed_ms, 250)
        mocked_start.assert_awaited_once_with(task["id"])
        mocked_warmup.assert_awaited_once()

    async def test_start_task_emits_step_timing_logs(self):
        task = await self.service.create_task(
            {
                "name": "start-timing-task",
                "source_url": "rtsp://example/live/start-timing",
                "channel_id": "ch_start_timing",
                "algorithms": [
                    {
                        "id": "algo_start_timing",
                        "model_id": "model_person",
                        "enabled": True,
                    }
                ],
            }
        )

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start:
            with patch.object(self.service, "_warmup_task_preview", AsyncMock(return_value={"status": "running"})):
                with patch("src.services.task_service.logger.info") as mocked_logger:
                    result = await self.service.start_task(task["id"])
                    await asyncio.sleep(0.05)

        timing_steps = [
            call.args[2]
            for call in mocked_logger.call_args_list
            if len(call.args) >= 3 and call.args[0] == "[TaskStartTiming] task_id=%s step=%s elapsed_ms=%s"
        ]

        self.assertEqual(result["status"], "starting")
        self.assertEqual(
            timing_steps,
            [
                "get_task",
                "find_running_task",
                "hydrate_source",
                "validated_algorithms",
                "start_analysis",
                "schedule_preview_warmup",
            ],
        )
        mocked_start.assert_awaited_once_with(task["id"])

    async def test_start_task_auto_starts_zone_preview_with_combined_regions(self):
        task = await self.service.create_task(
            {
                "name": "project-task-regions-to-zone-preview",
                "source_url": "rtsp://example/live/9",
                "channel_id": "ch_demo_projection",
                "algorithms": [
                    {
                        "id": "algo_person",
                        "model_id": "model_person",
                        "need_overlay": True,
                        "enabled": True,
                        "regions": [
                            {
                                "id": "region_a",
                                "name": "zone-a",
                                "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                            },
                            {
                                "id": "region_b",
                                "name": "zone-b",
                                "coordinates": [[120, 0], [220, 0], [220, 100], [120, 100]],
                            },
                        ],
                    },
                    {
                        "id": "algo_helmet",
                        "model_id": "model_helmet",
                        "need_overlay": True,
                        "enabled": True,
                        "regions": [
                            {
                                "id": "region_b",
                                "name": "zone-b",
                                "coordinates": [[120, 0], [220, 0], [220, 100], [120, 100]],
                            }
                        ],
                    },
                    {
                        "id": "algo_vehicle",
                        "model_id": "model_vehicle",
                        "need_overlay": False,
                        "enabled": True,
                        "regions": [
                            {
                                "id": "region_c",
                                "name": "zone-c",
                                "coordinates": [[240, 0], [340, 0], [340, 100], [240, 100]],
                            }
                        ],
                    },
                    {
                        "id": "algo_offline",
                        "model_id": "model_offline",
                        "need_overlay": True,
                        "enabled": False,
                        "regions": [
                            {
                                "id": "region_c",
                                "name": "zone-c",
                                "coordinates": [[240, 0], [340, 0], [340, 100], [240, 100]],
                            }
                        ],
                    },
                ],
            }
        )

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start:
            with patch("src.services.task_service.channel_service.start_zone_preview", AsyncMock(return_value={"status": "running"})) as mocked_zone_start:
                with patch("src.services.task_service.channel_service.play", AsyncMock(return_value={"status": "playing"})) as mocked_play:
                    result = await self.service.start_task(task["id"])

        self.assertEqual(result["status"], "starting")
        mocked_start.assert_awaited_once_with(task["id"])
        mocked_play.assert_awaited_once_with("ch_demo_projection")
        mocked_zone_start.assert_awaited_once()
        _, kwargs = mocked_zone_start.await_args
        self.assertEqual(kwargs.get("channel_id"), "ch_demo_projection")
        self.assertEqual(kwargs.get("zone_source"), "task")
        self.assertEqual(kwargs.get("owner_task_id"), task["id"])
        regions = kwargs.get("zones") or []
        self.assertEqual(sorted(region["id"] for region in regions), ["region_a", "region_b", "region_c"])

    async def test_start_task_uses_zone_preview_when_regions_exist_even_without_overlay_flag(self):
        task = await self.service.create_task(
            {
                "name": "regions-alone-trigger-zone-preview",
                "source_url": "rtsp://example/live/regions-only",
                "channel_id": "ch_demo_regions_only",
                "algorithms": [
                    {
                        "id": "algo_regions_only",
                        "model_id": "model_person",
                        "need_overlay": False,
                        "requires_region": False,
                        "enabled": True,
                        "regions": [
                            {
                                "id": "region_regions_only",
                                "name": "zone-regions-only",
                                "coordinates": [[0, 0], [80, 0], [80, 80], [0, 80]],
                            }
                        ],
                    }
                ],
            }
        )

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start:
            with patch("src.services.task_service.channel_service.start_zone_preview", AsyncMock(return_value={"status": "running"})) as mocked_zone_start:
                with patch("src.services.task_service.channel_service.play", AsyncMock(return_value={"status": "playing"})) as mocked_play:
                    result = await self.service.start_task(task["id"])

        self.assertEqual(result["status"], "starting")
        mocked_start.assert_awaited_once_with(task["id"])
        mocked_play.assert_awaited_once_with("ch_demo_regions_only")
        mocked_zone_start.assert_awaited_once()
        _, kwargs = mocked_zone_start.await_args
        self.assertEqual(kwargs.get("channel_id"), "ch_demo_regions_only")
        self.assertEqual(kwargs.get("zone_source"), "task")
        self.assertEqual(kwargs.get("owner_task_id"), task["id"])
        regions = kwargs.get("zones") or []
        self.assertEqual([region["id"] for region in regions], ["region_regions_only"])

    async def test_replace_regions_save_only_does_not_start_zone_preview(self):
        channel = {
            "id": "ch_replace_regions_preview",
            "name": "replace-regions-preview",
            "type": "rtsp",
            "source_url": "rtsp://example/live/replace-regions",
            "app": "live",
            "stream": "replace_regions",
            "media_node_id": "default",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-12 10:00:00",
            "updated_at": "2026-06-12 10:00:00",
            "ext": {},
        }
        (self.channels_dir / "ch_replace_regions_preview.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "replace-regions-preview-task",
                "channel_id": "ch_replace_regions_preview",
                "algorithms": [
                    {
                        "id": "algo_replace_regions_preview",
                        "model_id": "model_person",
                        "need_overlay": True,
                        "enabled": True,
                    }
                ],
            }
        )

        with patch("src.services.task_service.channel_service.start_zone_preview", AsyncMock(return_value={"status": "running", "stream": "replace_regions_zone"})) as mocked_zone_start:
            result = await self.service.replace_regions(
                task["id"],
                "algo_replace_regions_preview",
                {
                    "refresh_preview": True,
                    "regions": [
                        {
                            "id": "region_replace_regions_preview",
                            "name": "entry-zone",
                            "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                        }
                    ],
                },
            )

        self.assertIsNotNone(result)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["id"], "region_replace_regions_preview")
        self.assertNotIn("preview", result)
        self.assertNotIn("play_url", result)
        saved_channel = json.loads((self.channels_dir / "ch_replace_regions_preview.json").read_text(encoding="utf-8"))
        self.assertIsNone(saved_channel.get("hls"))
        self.assertIsNone(saved_channel.get("webrtc_api"))
        mocked_zone_start.assert_not_awaited()

    async def test_replace_regions_running_task_schedules_async_preview_refresh(self):
        channel = {
            "id": "ch_replace_regions_running",
            "name": "replace-regions-running",
            "type": "rtsp",
            "source_url": "rtsp://example/live/replace-regions-running",
            "app": "live",
            "stream": "replace_regions_running",
            "media_node_id": "default",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-12 10:00:00",
            "updated_at": "2026-06-12 10:00:00",
            "ext": {},
        }
        (self.channels_dir / "ch_replace_regions_running.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "replace-regions-running-task",
                "channel_id": "ch_replace_regions_running",
                "algorithms": [
                    {
                        "id": "algo_replace_regions_running",
                        "model_id": "model_person",
                        "need_overlay": True,
                        "enabled": True,
                    }
                ],
            }
        )
        task["status"] = "running"
        task["runtime"] = {
            **(task.get("runtime") or {}),
            "status": "running",
            "is_running": True,
        }
        task = await self.service._persist_task(task)

        with patch.object(self.service, "_schedule_task_preview_warmup") as mocked_schedule_warmup:
            result = await self.service.replace_regions(
                task["id"],
                "algo_replace_regions_running",
                {
                    "refresh_preview": True,
                    "regions": [
                        {
                            "id": "region_replace_regions_running",
                            "name": "entry-zone",
                            "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                        }
                    ],
                },
            )

        self.assertIsNotNone(result)
        self.assertFalse(result["playback_ready"])
        self.assertTrue(result["preview_refresh_scheduled"])
        self.assertNotIn("resolved_mode", result)
        self.assertNotIn("play_url", result)
        mocked_schedule_warmup.assert_called_once()
        scheduled_task = mocked_schedule_warmup.call_args.args[0]
        self.assertEqual(scheduled_task.get("id"), task["id"])
        self.assertEqual(
            [item["id"] for item in list((scheduled_task.get("algorithms") or [])[0].get("regions") or [])],
            ["region_replace_regions_running"],
        )

    async def test_replace_regions_without_overlay_regions_falls_back_to_channel_play_url(self):
        channel = {
            "id": "ch_replace_regions_no_overlay",
            "name": "replace-regions-no-overlay",
            "type": "rtsp",
            "source_url": "rtsp://example/live/no-overlay",
            "app": "live",
            "stream": "replace_regions_no_overlay",
            "media_node_id": "default",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-12 10:10:00",
            "updated_at": "2026-06-12 10:10:00",
            "ext": {},
        }
        (self.channels_dir / "ch_replace_regions_no_overlay.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "replace-regions-no-overlay-task",
                "channel_id": "ch_replace_regions_no_overlay",
                "algorithms": [
                    {
                        "id": "algo_replace_regions_no_overlay",
                        "model_id": "model_person",
                        "need_overlay": False,
                        "requires_region": False,
                        "enabled": True,
                    }
                ],
            }
        )

        result = await self.service.replace_regions(
            task["id"],
            "algo_replace_regions_no_overlay",
            {
                "regions": [],
            },
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["items"], [])
        self.assertFalse(result["playback_ready"])
        self.assertNotIn("play_url", result)
        saved_channel = json.loads((self.channels_dir / "ch_replace_regions_no_overlay.json").read_text(encoding="utf-8"))
        self.assertIsNone(saved_channel.get("hls"))
        self.assertIsNone(saved_channel.get("webrtc_api"))

    async def test_replace_regions_can_remove_existing_regions(self):
        channel = {
            "id": "ch_replace_regions_remove",
            "name": "replace-regions-remove",
            "type": "rtsp",
            "source_url": "rtsp://example/live/remove",
            "app": "live",
            "stream": "replace_regions_remove",
            "media_node_id": "default",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-12 10:20:00",
            "updated_at": "2026-06-12 10:20:00",
            "ext": {},
        }
        (self.channels_dir / "ch_replace_regions_remove.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "replace-regions-remove-task",
                "channel_id": "ch_replace_regions_remove",
                "algorithms": [
                    {
                        "id": "algo_replace_regions_remove",
                        "model_id": "model_person",
                        "need_overlay": True,
                        "enabled": True,
                        "regions": [
                            {
                                "id": "region_remove_1",
                                "name": "zone_1",
                                "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                            }
                        ],
                    }
                ],
            }
        )

        result = await self.service.replace_regions(
            task["id"],
            "algo_replace_regions_remove",
            {
                "regions": [],
            },
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["items"], [])
        refreshed = await self.service.list_algorithm_regions(task["id"], "algo_replace_regions_remove")
        self.assertEqual(refreshed["items"], [])

    async def test_replace_regions_running_task_without_regions_schedules_async_preview_refresh(self):
        channel = {
            "id": "ch_replace_regions_running_remove",
            "name": "replace-regions-running-remove",
            "type": "rtsp",
            "source_url": "rtsp://example/live/remove-running",
            "app": "live",
            "stream": "replace_regions_running_remove",
            "media_node_id": "default",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-12 10:21:00",
            "updated_at": "2026-06-12 10:21:00",
            "ext": {},
        }
        (self.channels_dir / "ch_replace_regions_running_remove.json").write_text(
            json.dumps(channel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        task = await self.service.create_task(
            {
                "name": "replace-regions-running-remove-task",
                "channel_id": "ch_replace_regions_running_remove",
                "algorithms": [
                    {
                        "id": "algo_replace_regions_running_remove",
                        "model_id": "model_person",
                        "need_overlay": True,
                        "enabled": True,
                        "regions": [
                            {
                                "id": "region_replace_regions_running_remove",
                                "name": "entry-zone",
                                "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                            }
                        ],
                    }
                ],
            }
        )
        task["status"] = "running"
        task["runtime"] = {
            **(task.get("runtime") or {}),
            "status": "running",
            "is_running": True,
        }
        task = await self.service._persist_task(task)

        with patch.object(self.service, "_schedule_task_preview_warmup") as mocked_schedule_warmup:
            result = await self.service.replace_regions(
                task["id"],
                "algo_replace_regions_running_remove",
                {
                    "regions": [],
                },
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["items"], [])
        self.assertFalse(result["playback_ready"])
        self.assertTrue(result["preview_refresh_scheduled"])
        self.assertNotIn("resolved_mode", result)
        self.assertNotIn("play_url", result)
        mocked_schedule_warmup.assert_called_once()
        scheduled_task = mocked_schedule_warmup.call_args.args[0]
        self.assertEqual(scheduled_task.get("id"), task["id"])
        refreshed_algorithm = next(
            item for item in list(scheduled_task.get("algorithms") or [])
            if item.get("id") == "algo_replace_regions_running_remove"
        )
        self.assertEqual(list(refreshed_algorithm.get("regions") or []), [])

    async def test_stop_task_auto_stops_zone_preview(self):
        task = {
            "id": "task_stop_zone_preview",
            "name": "stop-zone-preview-task",
            "source_url": "rtsp://example/live/8",
            "channel_id": "ch_demo_stop",
            "algorithms": [
                {
                    "id": "algo_stop_zone_preview",
                    "model_id": "model_person",
                    "enabled": True,
                    "need_overlay": True,
                    "regions": [
                        {
                            "id": "region_stop_zone_preview",
                            "name": "zone_1",
                            "type": "polygon",
                            "coordinates": [[0, 0], [1, 0], [1, 1], [0, 1]],
                            "enabled": True,
                        }
                    ],
                }
            ],
        }

        with patch.object(self.service, "get_task", AsyncMock(return_value=task)) as mocked_get_task:
            with patch.object(analysis_service, "stop_task_analysis", AsyncMock(return_value={"status": "stopping"})) as mocked_stop:
                with patch("src.services.task_service.channel_service.stop_zone_preview", AsyncMock(return_value={"status": "stopped"})) as mocked_zone_stop:
                    with patch("src.services.task_service.channel_service.stop", AsyncMock(return_value={"status": "stopped"})) as mocked_channel_stop:
                        with patch("src.services.task_service.channel_service.clear_task_preview_state", AsyncMock(return_value={"ok": True})) as mocked_clear_preview:
                            result = await self.service.stop_task(task["id"])

        self.assertEqual(result["status"], "stopping")
        mocked_get_task.assert_awaited_once_with(task["id"], prefer_metadata=True)
        mocked_stop.assert_awaited_once_with(task["id"])
        mocked_zone_stop.assert_awaited_once_with("ch_demo_stop")
        mocked_clear_preview.assert_awaited_once_with("ch_demo_stop", owner_task_id=task["id"])
        mocked_channel_stop.assert_awaited_once_with("ch_demo_stop")

    async def test_stop_task_also_stops_channel_stream(self):
        task = {
            "id": "task_stop_channel_stream",
            "name": "stop-channel-stream-task",
            "source_url": "rtsp://example/live/stop-channel-stream",
            "channel_id": "ch_demo_stop",
            "algorithms": [
                {
                    "id": "algo_stop_channel_stream",
                    "model_id": "model_person",
                    "enabled": True,
                    "need_overlay": False,
                    "regions": [],
                }
            ],
        }

        with patch.object(self.service, "get_task", AsyncMock(return_value=task)) as mocked_get_task:
            with patch.object(analysis_service, "stop_task_analysis", AsyncMock(return_value={"status": "stopping"})) as mocked_stop:
                with patch("src.services.task_service.channel_service.stop_zone_preview", AsyncMock(return_value={"status": "stopped"})) as mocked_zone_stop:
                    with patch("src.services.task_service.channel_service.stop", AsyncMock(return_value={"status": "stopped"})) as mocked_channel_stop:
                        with patch("src.services.task_service.channel_service.clear_task_preview_state", AsyncMock(return_value={"ok": True})) as mocked_clear_preview:
                            result = await self.service.stop_task(task["id"])

        self.assertEqual(result["status"], "stopping")
        mocked_get_task.assert_awaited_once_with(task["id"], prefer_metadata=True)
        mocked_stop.assert_awaited_once_with(task["id"])
        mocked_zone_stop.assert_not_awaited()
        mocked_clear_preview.assert_awaited_once_with("ch_demo_stop", owner_task_id=task["id"])
        mocked_channel_stop.assert_awaited_once_with("ch_demo_stop")

    async def test_restore_running_tasks_schedules_zone_preview_warmup(self):
        task = await self.service.create_task(
            {
                "name": "restore-zone-preview-task",
                "channel_id": "ch_restore_zone_preview",
                "source_url": "rtsp://example/live/restore-zone",
                "algorithms": [
                    {
                        "id": "algo_restore_zone",
                        "model_id": "model_restore_zone",
                        "need_overlay": True,
                        "enabled": True,
                        "regions": [
                            {
                                "id": "region_restore_zone",
                                "name": "restore-zone",
                                "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                            }
                        ],
                    }
                ],
            }
        )

        task_path = self.tasks_dir / f"{task['id']}.json"
        payload = json.loads(task_path.read_text(encoding="utf-8"))
        payload["status"] = "running"
        payload["runtime"] = {
            "status": "running",
            "is_running": True,
            "started_at": "2026-06-15 10:00:00",
            "stopped_at": None,
            "last_error": None,
            "last_analysis_at": None,
            "last_event_at": None,
        }
        task_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        with (
            patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start,
            patch.object(self.service, "_schedule_task_preview_warmup") as mocked_schedule,
        ):
            result = await self.service.restore_running_tasks()

        self.assertEqual(result["restored"], 1)
        mocked_start.assert_awaited_once_with(task["id"])
        mocked_schedule.assert_called_once()

    async def test_restore_running_tasks_schedules_preview_warmup_with_startup_delay(self):
        settings.TASK_PREVIEW_WARMUP_STARTUP_DELAY_SECONDS = 2.5

        task = await self.service.create_task(
            {
                "name": "restore-preview-delay-task",
                "channel_id": "ch_restore_preview_delay",
                "source_url": "rtsp://example/live/restore-delay",
                "algorithms": [
                    {
                        "id": "algo_restore_preview_delay",
                        "model_id": "model_restore_preview_delay",
                        "enabled": True,
                    }
                ],
            }
        )

        with self.session_local() as session:
            row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one()
            payload = dict(row.raw_json or {})
            payload["runtime"] = {
                **dict(payload.get("runtime") or {}),
                "status": "running",
                "is_running": True,
            }
            row.status = "running"
            row.is_running = True
            row.raw_json = payload
            session.commit()

        with (
            patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})),
            patch.object(self.service, "_schedule_task_preview_warmup") as mocked_schedule,
        ):
            result = await self.service.restore_running_tasks()

        self.assertEqual(result["restored"], 1)
        mocked_schedule.assert_called_once()
        scheduled_task = mocked_schedule.call_args.args[0]
        self.assertEqual(scheduled_task.get("id"), task["id"])
        self.assertEqual(mocked_schedule.call_args.kwargs.get("reason"), "startup-restore")
        self.assertEqual(mocked_schedule.call_args.kwargs.get("delay_seconds"), 2.5)

    async def test_restore_running_tasks_staggers_task_launches(self):
        settings.TASK_RESTORE_STARTUP_STAGGER_SECONDS = 0.75

        first_task = await self.service.create_task(
            {
                "name": "restore-stagger-first",
                "channel_id": "ch_restore_stagger_first",
                "source_url": "rtsp://example/live/restore-stagger-first",
                "algorithms": [
                    {
                        "id": "algo_restore_stagger_first",
                        "model_id": "model_restore_stagger_first",
                        "enabled": True,
                    }
                ],
            }
        )
        second_task = await self.service.create_task(
            {
                "name": "restore-stagger-second",
                "channel_id": "ch_restore_stagger_second",
                "source_url": "rtsp://example/live/restore-stagger-second",
                "algorithms": [
                    {
                        "id": "algo_restore_stagger_second",
                        "model_id": "model_restore_stagger_second",
                        "enabled": True,
                    }
                ],
            }
        )

        for task in [first_task, second_task]:
            with self.session_local() as session:
                row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one()
                payload = dict(row.raw_json or {})
                payload["runtime"] = {
                    **dict(payload.get("runtime") or {}),
                    "status": "running",
                    "is_running": True,
                }
                row.status = "running"
                row.is_running = True
                row.raw_json = payload
                session.commit()

        with (
            patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start,
            patch.object(self.service, "_schedule_task_preview_warmup"),
            patch("src.services.task_service.asyncio.sleep", AsyncMock()) as mocked_sleep,
        ):
            result = await self.service.restore_running_tasks()

        self.assertEqual(result["restored"], 2)
        self.assertEqual(mocked_start.await_count, 2)
        mocked_sleep.assert_any_awaited_with(0.75)

    async def test_restore_running_tasks_emits_step_timing_logs(self):
        task = await self.service.create_task(
            {
                "name": "restore-timing-task",
                "channel_id": "ch_restore_timing",
                "source_url": "rtsp://example/live/restore-timing",
                "algorithms": [
                    {
                        "id": "algo_restore_timing",
                        "model_id": "model_restore_timing",
                        "labels": ["person"],
                    }
                ],
            }
        )

        with self.session_local() as session:
            row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one()
            payload = dict(row.raw_json or {})
            payload["runtime"] = {
                **dict(payload.get("runtime") or {}),
                "status": "running",
                "is_running": True,
            }
            row.status = "running"
            row.is_running = True
            row.raw_json = payload
            session.commit()

        with patch.object(analysis_service, "start_task_analysis", AsyncMock(return_value={"status": "starting"})) as mocked_start:
            with patch.object(self.service, "_warmup_task_preview", AsyncMock(return_value={"status": "running"})):
                with patch("src.services.task_service.logger.info") as mocked_logger:
                    result = await self.service.restore_running_tasks()
                    await asyncio.sleep(0.05)

        timing_steps = [
            call.args[2]
            for call in mocked_logger.call_args_list
            if len(call.args) >= 3 and call.args[0] == "[TaskRestoreTiming] task_id=%s step=%s elapsed_ms=%s"
        ]

        self.assertEqual(result["restored"], 1)
        self.assertEqual(
            timing_steps,
            [
                "get_task",
                "check_runtime",
                "hydrate_source",
                "validated_algorithms",
                "start_analysis",
                "schedule_preview_warmup",
            ],
        )
        mocked_start.assert_awaited_once_with(task["id"])

    async def test_start_task_requires_enabled_algorithm_before_starting_worker(self):
        task = await self.service.create_task(
            {
                "name": "empty-algorithm-task",
                "source_url": "rtsp://example/live/4",
                "algorithms": [],
            }
        )

        with patch.object(analysis_service, "start_task_analysis", AsyncMock()) as mocked_start:
            with self.assertRaisesRegex(ValueError, "Task has no enabled algorithms"):
                await self.service.start_task(task["id"])

        mocked_start.assert_not_awaited()

        current = await self.service.get_task(task["id"])
        self.assertEqual(current["status"], "stopped")
        self.assertEqual(current["runtime"]["status"], "stopped")
        self.assertIsNone(current["runtime"]["last_error"])

    async def test_start_task_blocks_second_running_task_on_same_channel(self):
        first_task = await self.service.create_task(
            {
                "name": "channel-exclusive-task-1",
                "source_url": "rtsp://example/live/exclusive-1",
                "channel_id": "ch_exclusive",
                "algorithms": [
                    {
                        "id": "algo_exclusive_1",
                        "model_id": "model_person_1",
                        "labels": ["person"],
                    }
                ],
            }
        )
        second_task = await self.service.create_task(
            {
                "name": "channel-exclusive-task-2",
                "source_url": "rtsp://example/live/exclusive-2",
                "channel_id": "ch_exclusive",
                "algorithms": [
                    {
                        "id": "algo_exclusive_2",
                        "model_id": "model_person_2",
                        "labels": ["person"],
                    }
                ],
            }
        )

        first_path = self.tasks_dir / f"{first_task['id']}.json"
        first_payload = json.loads(first_path.read_text(encoding="utf-8"))
        first_payload["status"] = "running"
        first_payload["runtime"] = {
            "status": "running",
            "is_running": True,
            "started_at": "2026-06-09T10:00:00",
            "stopped_at": None,
            "last_error": None,
            "last_analysis_at": None,
            "last_event_at": None,
        }
        first_path.write_text(json.dumps(first_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        with patch.object(analysis_service, "start_task_analysis", AsyncMock()) as mocked_start:
            with self.assertRaisesRegex(ValueError, "已有运行中任务"):
                await self.service.start_task(second_task["id"])

        mocked_start.assert_not_awaited()

    async def test_available_for_task_channels_excludes_channel_with_existing_task(self):
        occupied_channel = {
            "id": "ch_busy",
            "name": "busy-channel",
            "type": "rtsp",
            "source_url": "rtsp://example/live/busy",
            "app": "live",
            "stream": "busy",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-09T10:00:00",
            "updated_at": "2026-06-09T10:00:00",
            "ext": {},
        }
        free_channel = {
            "id": "ch_free",
            "name": "free-channel",
            "type": "rtsp",
            "source_url": "rtsp://example/live/free",
            "app": "live",
            "stream": "free",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-09T10:00:01",
            "updated_at": "2026-06-09T10:00:01",
            "ext": {},
        }
        (self.channels_dir / "ch_busy.json").write_text(json.dumps(occupied_channel, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.channels_dir / "ch_free.json").write_text(json.dumps(free_channel, ensure_ascii=False, indent=2), encoding="utf-8")

        task = await self.service.create_task(
            {
                "name": "busy-task",
                "channel_id": "ch_busy",
                "source_url": "rtsp://example/live/busy",
                "algorithms": [
                    {
                        "id": "algo_busy",
                        "model_id": "model_busy",
                        "labels": ["person"],
                    }
                ],
            }
        )

        task_path = self.tasks_dir / f"{task['id']}.json"
        task_payload = json.loads(task_path.read_text(encoding="utf-8"))
        task_payload["status"] = "stopped"
        task_payload["runtime"] = {
            "status": "stopped",
            "is_running": False,
            "started_at": None,
            "stopped_at": "2026-06-09 10:01:00",
            "last_error": None,
            "last_analysis_at": None,
            "last_event_at": None,
        }
        task_path.write_text(json.dumps(task_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        channel_service = ChannelService()
        with patch.object(channel_service, "list_channels", AsyncMock(return_value={"items": [occupied_channel, free_channel]})):
            result = await channel_service.list_available_for_task_channels()

        self.assertEqual([item["id"] for item in result["items"]], ["ch_free"])

    async def test_algorithm_video_clip_window_is_fixed_to_ten_seconds_each_side(self):
        task = await self.service.create_task(
            {
                "name": "fixed-clip-duration-task",
                "source_url": "rtsp://example/live/5",
                "algorithms": [
                    {
                        "model_id": "model_person",
                        "video_before_seconds": 9,
                        "video_after_seconds": 1,
                    }
                ],
            }
        )

        algorithm = task["algorithms"][0]
        self.assertEqual(algorithm["video_before_seconds"], 10)
        self.assertEqual(algorithm["video_after_seconds"], 10)
        self.assertFalse(algorithm["need_overlay"])

    async def test_channel_play_urls_supports_auto_channel_and_zone_modes(self):
        channel_payload = {
            "id": "ch_play_mode_demo",
            "name": "play-mode-demo",
            "type": "rtsp",
            "source_url": "rtsp://example/live/play-mode",
            "app": "live",
            "stream": "play_mode",
            "media_node_id": None,
            "is_online": True,
            "is_playing": True,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-10 10:00:00",
            "updated_at": "2026-06-10 10:00:00",
            "ext": {
                "browser_transcode": {
                    "enabled": True,
                    "app": "live",
                    "stream": "play_mode_browser",
                    "inspect_rtmp": {"data": [{"stream": "play_mode_browser"}]},
                },
                "zone_preview": {
                    "enabled": True,
                    "status": "running",
                    "stream": "play_mode_zone",
                },
            },
        }
        (self.channels_dir / "ch_play_mode_demo.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        channel_service = ChannelService()

        channel_result = await channel_service.play_urls("ch_play_mode_demo", mode="channel")
        self.assertEqual(channel_result["requested_mode"], "channel")
        self.assertEqual(channel_result["resolved_mode"], "channel")
        self.assertEqual(channel_result["source"], "channel")
        self.assertEqual(channel_result["play_urls"]["rtsp"], channel_result["urls"]["rtsp"])
        self.assertEqual(channel_result["browser_play_urls"]["browser_hls"], channel_result["browser_urls"]["browser_hls"])
        self.assertEqual(channel_result["source_urls"]["rtsp"], channel_result["urls"]["rtsp"])
        self.assertEqual(channel_result["source_browser_urls"]["browser_hls"], channel_result["browser_urls"]["browser_hls"])
        self.assertEqual(channel_result["final_play_urls"]["rtsp"], channel_result["play_urls"]["rtsp"])
        self.assertEqual(channel_result["final_browser_play_urls"]["browser_hls"], channel_result["browser_play_urls"]["browser_hls"])
        self.assertTrue(channel_result["final_browser_play_urls"]["browser_hls_url"].startswith("http"))
        self.assertTrue(channel_result["final_browser_play_urls"]["browser_httpflv_url"].startswith("http"))
        self.assertTrue(channel_result["final_browser_play_urls"]["browser_webrtc_api_url"].startswith("http"))

        zone_result = await channel_service.play_urls("ch_play_mode_demo", mode="zone")
        self.assertEqual(zone_result["requested_mode"], "zone")
        self.assertEqual(zone_result["resolved_mode"], "zone")
        self.assertEqual(zone_result["source"], "zone")
        self.assertEqual(zone_result["play_urls"]["rtsp"], zone_result["zone_preview_urls"]["rtsp"])
        self.assertEqual(zone_result["browser_play_urls"]["browser_hls"], zone_result["zone_preview_urls"]["browser_hls"])
        self.assertEqual(zone_result["source_urls"]["rtsp"], zone_result["zone_preview_urls"]["rtsp"])
        self.assertEqual(zone_result["final_play_urls"]["rtsp"], zone_result["play_urls"]["rtsp"])
        self.assertEqual(zone_result["final_browser_play_urls"]["browser_hls"], zone_result["browser_play_urls"]["browser_hls"])

        auto_result = await channel_service.play_urls("ch_play_mode_demo")
        self.assertEqual(auto_result["requested_mode"], "auto")
        self.assertEqual(auto_result["resolved_mode"], "zone")
        self.assertEqual(auto_result["source"], "zone")
        self.assertEqual(auto_result["play_urls"]["rtsp"], auto_result["zone_preview_urls"]["rtsp"])
        self.assertEqual(auto_result["final_play_urls"]["rtsp"], auto_result["play_urls"]["rtsp"])
        self.assertEqual(auto_result["final_browser_play_urls"]["browser_hls"], auto_result["browser_play_urls"]["browser_hls"])

    async def test_channel_play_urls_auto_falls_back_to_channel_when_zone_missing(self):
        channel_payload = {
            "id": "ch_play_mode_fallback",
            "name": "play-mode-fallback",
            "type": "rtsp",
            "source_url": "rtsp://example/live/play-mode-fallback",
            "app": "live",
            "stream": "play_mode_fallback",
            "media_node_id": None,
            "is_online": True,
            "is_playing": True,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-10 10:01:00",
            "updated_at": "2026-06-10 10:01:00",
            "ext": {},
        }
        (self.channels_dir / "ch_play_mode_fallback.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        channel_service = ChannelService()

        auto_result = await channel_service.play_urls("ch_play_mode_fallback")
        self.assertEqual(auto_result["requested_mode"], "auto")
        self.assertEqual(auto_result["resolved_mode"], "channel")
        self.assertEqual(auto_result["source"], "channel")
        self.assertEqual(auto_result["play_urls"]["rtsp"], auto_result["urls"]["rtsp"])
        self.assertEqual(auto_result["source_urls"]["rtsp"], auto_result["urls"]["rtsp"])
        self.assertEqual(auto_result["final_play_urls"]["rtsp"], auto_result["play_urls"]["rtsp"])
        self.assertEqual(auto_result["final_browser_play_urls"]["browser_hls"], auto_result["browser_play_urls"]["browser_hls"])

    async def test_channel_play_urls_auto_falls_back_to_channel_when_zone_not_running(self):
        channel_payload = {
            "id": "ch_play_mode_zone_stopped",
            "name": "play-mode-zone-stopped",
            "type": "rtsp",
            "source_url": "rtsp://example/live/play-mode-zone-stopped",
            "app": "live",
            "stream": "play_mode_zone_stopped",
            "media_node_id": None,
            "is_online": True,
            "is_playing": True,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-12 18:00:00",
            "updated_at": "2026-06-12 18:00:00",
            "ext": {
                "zone_preview": {
                    "enabled": False,
                    "status": "stopped",
                    "stream": "play_mode_zone_stopped_zone",
                },
            },
        }
        (self.channels_dir / "ch_play_mode_zone_stopped.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        channel_service = ChannelService()

        auto_result = await channel_service.play_urls("ch_play_mode_zone_stopped")
        self.assertEqual(auto_result["requested_mode"], "auto")
        self.assertEqual(auto_result["resolved_mode"], "channel")
        self.assertEqual(auto_result["source"], "channel")

    async def test_zone_preview_browser_play_url_returns_minimal_browser_fields(self):
        channel_payload = {
            "id": "ch_zone_browser_minimal",
            "name": "zone-browser-minimal",
            "type": "rtsp",
            "source_url": "rtsp://example/live/zone-browser-minimal",
            "app": "live",
            "stream": "zone_browser_minimal",
            "media_node_id": None,
            "is_online": True,
            "is_playing": True,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-12 09:00:00",
            "updated_at": "2026-06-12 09:00:00",
            "ext": {
                "zone_preview": {
                    "enabled": True,
                    "status": "running",
                    "stream": "zone_browser_minimal_zone",
                },
            },
        }
        (self.channels_dir / "ch_zone_browser_minimal.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        channel_service = ChannelService()
        result = await channel_service.get_zone_preview_browser_play_url("ch_zone_browser_minimal")

        self.assertEqual(result["channel_id"], "ch_zone_browser_minimal")
        self.assertEqual(result["resolved_mode"], "zone")
        self.assertEqual(result["status"], "online")
        self.assertEqual(
            sorted(result["play_url"].keys()),
            ["flv", "hls", "httpflv", "webrtc_api"],
        )

    async def test_channel_unique_transcode_stream_names_use_channel_id(self):
        channel_service = ChannelService()

        browser_stream, browser_target = channel_service._build_browser_transcode_target(
            None,
            "live",
            "101",
            "e36c059e",
        )
        zone_stream, zone_target = channel_service._build_zone_preview_target(
            None,
            "live",
            "101",
            "e36c059e",
        )

        self.assertEqual(browser_stream, "e36c059e_browser")
        self.assertIn("/live/e36c059e_browser", browser_target)
        self.assertEqual(zone_stream, "e36c059e_zone")
        self.assertIn("/live/e36c059e_zone", zone_target)

    async def test_zone_preview_play_urls_fallback_uses_channel_id_unique_stream(self):
        channel_payload = {
            "id": "e36c059e",
            "name": "legacy-zone-stream",
            "type": "rtsp",
            "source_url": "rtsp://example/live/101",
            "app": "live",
            "stream": "101",
            "media_node_id": None,
            "is_online": True,
            "is_playing": True,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-15 10:00:00",
            "updated_at": "2026-06-15 10:00:00",
            "ext": {},
        }
        (self.channels_dir / "e36c059e.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        channel_service = ChannelService()
        result = await channel_service.get_zone_preview_play_urls("e36c059e")

        self.assertEqual(result["stream"], "e36c059e_zone")
        self.assertIn("/live/e36c059e_zone", result["urls"]["rtmp"])
        self.assertIn("stream=e36c059e_zone", result["urls"]["webrtc"])
        self.assertTrue(result["play_url"]["hls"].startswith("http://localhost:3000/proxy/sms/"))
        self.assertTrue(result["play_url"]["webrtc_api"].startswith("http://localhost:3000/proxy/sms/"))

    async def test_task_routes_are_registered_in_main_app(self):
        from src.main import app

        routes = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/tasks", routes)
        self.assertIn("/tasks/{task_id}", routes)
        self.assertIn("/tasks/{task_id}/status", routes)
        self.assertIn("/tasks/{task_id}/algorithms", routes)


if __name__ == "__main__":
    unittest.main()
