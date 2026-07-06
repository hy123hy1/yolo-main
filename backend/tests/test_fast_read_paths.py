import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.db.models import Base, ChannelMeta, ModelMetaRecord, TaskMeta
from src.services import channel_service as channel_service_module
from src.services import metadata_db_sync
from src.services import model_service as model_service_module
from src.services import task_service as task_service_module
from src.services.channel_service import ChannelService
from src.services.model_service import ModelService
from src.services.task_service import TaskService


class FastReadPathsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sqlite_path = Path(self.temp_dir.name) / "metadata.db"
        self.tasks_dir = Path(self.temp_dir.name) / "tasks"
        self.channels_dir = Path(self.temp_dir.name) / "channels"
        self.registry_dir = Path(self.temp_dir.name) / "registry"
        self.jobs_dir = Path(self.temp_dir.name) / "jobs"
        self.snapshots_dir = Path(self.temp_dir.name) / "snapshots"
        for directory in (
            self.tasks_dir,
            self.channels_dir,
            self.registry_dir,
            self.jobs_dir,
            self.snapshots_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self.original_tasks_dir = settings.TASKS_DIR
        self.original_channels_dir = settings.CHANNELS_DIR
        self.original_registry_dir = settings.REGISTRY_DIR
        self.original_jobs_dir = settings.JOBS_DIR
        self.original_snapshots_dir = settings.SNAPSHOTS_DIR
        self.original_metadata_db_enabled = settings.METADATA_DB_ENABLED
        settings.TASKS_DIR = self.tasks_dir
        settings.CHANNELS_DIR = self.channels_dir
        settings.REGISTRY_DIR = self.registry_dir
        settings.JOBS_DIR = self.jobs_dir
        settings.SNAPSHOTS_DIR = self.snapshots_dir
        settings.METADATA_DB_ENABLED = True

        self.original_engine = metadata_db_sync.engine
        self.original_session_local = metadata_db_sync.SessionLocal
        self.original_task_service_session_local = task_service_module.SessionLocal
        self.original_model_service_session_local = model_service_module.SessionLocal
        self.original_channel_service_session_local = channel_service_module.SessionLocal

        self.engine = create_engine(
            f"sqlite:///{self.sqlite_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.session_local = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            future=True,
        )
        metadata_db_sync.engine = self.engine
        metadata_db_sync.SessionLocal = self.session_local
        task_service_module.SessionLocal = self.session_local
        model_service_module.SessionLocal = self.session_local
        channel_service_module.SessionLocal = self.session_local
        Base.metadata.create_all(bind=self.engine)

    async def asyncTearDown(self):
        settings.TASKS_DIR = self.original_tasks_dir
        settings.CHANNELS_DIR = self.original_channels_dir
        settings.REGISTRY_DIR = self.original_registry_dir
        settings.JOBS_DIR = self.original_jobs_dir
        settings.SNAPSHOTS_DIR = self.original_snapshots_dir
        settings.METADATA_DB_ENABLED = self.original_metadata_db_enabled

        metadata_db_sync.engine = self.original_engine
        metadata_db_sync.SessionLocal = self.original_session_local
        task_service_module.SessionLocal = self.original_task_service_session_local
        model_service_module.SessionLocal = self.original_model_service_session_local
        channel_service_module.SessionLocal = self.original_channel_service_session_local

        self.engine.dispose()
        self.temp_dir.cleanup()

    def _insert_channel_meta(self, payload: dict) -> None:
        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id=payload["id"],
                    device_id=payload.get("device_id"),
                    media_node_id=payload.get("media_node_id"),
                    name=payload.get("name"),
                    type=payload.get("type"),
                    source_url=payload.get("source_url"),
                    app=payload.get("app"),
                    stream=payload.get("stream"),
                    is_online=payload.get("is_online"),
                    is_playing=payload.get("is_playing"),
                    ai_enabled=payload.get("ai_enabled"),
                    record_mode=payload.get("record_mode"),
                    snapshot_url=payload.get("snapshot_url"),
                    snapshot_path=payload.get("snapshot_path"),
                    webrtc_api=payload.get("webrtc_api"),
                    hls=payload.get("hls"),
                    created_at=None,
                    updated_at=None,
                    raw_json=payload,
                )
            )
            session.commit()

    def _insert_model_row(self, model_id: str, name: str, classes: list[str] | None = None) -> None:
        classes = list(classes or ["person"])
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id=model_id,
                    name=name,
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=classes,
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": model_id,
                        "name": name,
                        "description": "from-db",
                        "tags": ["db"],
                        "classes": classes,
                    },
                )
            )
            session.commit()

    def _insert_task_row(self, task_id: str, channel_id: str) -> None:
        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id=task_id,
                    name=f"task-{task_id}",
                    source_url=f"rtsp://example/live/{task_id}",
                    source_type="rtsp",
                    channel_id=channel_id,
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
                        "id": task_id,
                        "name": f"task-{task_id}",
                        "source_url": f"rtsp://example/live/{task_id}",
                        "source_type": "rtsp",
                        "channel_id": channel_id,
                        "frame_interval": 10,
                        "status": "stopped",
                        "runtime": {
                            "status": "stopped",
                            "is_running": False,
                        },
                    },
                )
            )
            session.commit()

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

        service = TaskService()
        with patch.object(
            service,
            "get_task_from_metadata",
            AsyncMock(side_effect=AssertionError("list_tasks should not expand detail")),
        ):
            result = await service.list_tasks()

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["algorithm_count"], 2)
        self.assertEqual(result["items"][0]["region_count"], 1)
        self.assertEqual(result["items"][0]["runtime"]["status"], "running")

    async def test_get_task_status_does_not_expand_task_detail(self):
        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_status_cached",
                    name="task-status-cached",
                    source_url="rtsp://example/live/task-status",
                    source_type="rtsp",
                    channel_id="ch_task_status",
                    frame_interval=5,
                    status="running",
                    algorithm_count=3,
                    region_count=2,
                    is_running=True,
                    started_at=None,
                    stopped_at=None,
                    last_analysis_at=None,
                    last_event_at=None,
                    last_error=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "task_status_cached",
                        "runtime": {
                            "status": "running",
                            "is_running": True,
                        },
                    },
                )
            )
            session.commit()

        service = TaskService()
        with (
            patch.object(
                service,
                "get_task",
                AsyncMock(side_effect=AssertionError("get_task_status should not expand task detail")),
            ),
            patch("src.services.task_service.analysis_service.get_task_analysis_status", AsyncMock(return_value={"status": "running"})),
            patch("src.services.task_service.metric_service.list_task_metrics", AsyncMock(return_value={"items": []})),
        ):
            result = await service.get_task_status("task_status_cached")

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["algorithm_count"], 3)
        self.assertEqual(result["region_count"], 2)

    async def test_list_models_runtime_file_size_refresh_uses_cached_metadata_summary(self):
        self._insert_model_row("model_summary_1", "model-summary-1")

        service = ModelService()
        with patch.object(
            service,
            "_refresh_runtime_file_size",
            side_effect=AssertionError("list_models should not refresh runtime file size"),
        ):
            result = await service.list_models()

        self.assertEqual(result["models"][0]["model_id"], "model_summary_1")
        self.assertEqual(result["models"][0]["file_size"], 123)

    async def test_get_zone_preview_status_does_not_bootstrap_unprepared_channel(self):
        channel_id = "ch_unprepared_status"
        self._insert_channel_meta(
            {
                "id": channel_id,
                "name": "unprepared-status",
                "type": "rtsp",
                "source_url": "rtsp://example/live/unprepared-status",
                "app": "rtp",
                "stream": "unprepared_status",
                "webrtc_api": "http://127.0.0.1/proxy/webrtc",
                "hls": "http://127.0.0.1/proxy/hls.m3u8",
                "is_online": True,
                "is_playing": False,
                "record_mode": "none",
                "ext": {},
            }
        )

        service = ChannelService()
        with (
            patch.object(
                service,
                "_bootstrap_running_task_preview",
                AsyncMock(side_effect=AssertionError("GET status must not bootstrap preview")),
            ),
            patch("src.services.channel_service.zone_preview_service.status", return_value={"status": "stopped", "enabled": False}),
        ):
            result = await service.get_zone_preview_status(channel_id)

        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["enabled"], False)

    async def test_get_zone_preview_browser_play_url_does_not_bootstrap_unprepared_channel(self):
        channel_id = "ch_unprepared_browser"
        self._insert_channel_meta(
            {
                "id": channel_id,
                "name": "unprepared-browser",
                "type": "rtsp",
                "source_url": "rtsp://example/live/unprepared-browser",
                "app": "rtp",
                "stream": "unprepared_browser",
                "webrtc_api": "http://127.0.0.1/proxy/webrtc",
                "hls": "http://127.0.0.1/proxy/hls.m3u8",
                "is_online": True,
                "is_playing": False,
                "record_mode": "none",
                "ext": {},
            }
        )

        service = ChannelService()
        with patch.object(
            service,
            "_bootstrap_running_task_preview",
            AsyncMock(side_effect=AssertionError("GET browser play url must not bootstrap preview")),
        ):
            result = await service.get_zone_preview_browser_play_url(channel_id)

        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["play_url"]["webrtc_api"], "http://127.0.0.1/proxy/webrtc")
        self.assertEqual(result["play_url"]["hls"], "http://127.0.0.1/proxy/hls.m3u8")
        self.assertTrue(str(result["play_url"]["httpflv"]).startswith("http://"))
        self.assertIn("/proxy/sms/rtp/unprepared_browser.live.flv", str(result["play_url"]["httpflv"]))
        self.assertEqual(result["play_url"]["flv"], result["play_url"]["httpflv"])

    async def test_add_algorithm_loads_scene_defaults_without_heavy_model_detail(self):
        task_id = "task_add_algorithm_defaults"
        model_id = "model_fire_detection"
        self._insert_task_row(task_id, "ch_add_algorithm_defaults")
        self._insert_model_row(model_id, "火情检测", classes=["fire"])

        service = TaskService()
        with patch.object(
            task_service_module.model_service,
            "get_model",
            AsyncMock(side_effect=AssertionError("add_algorithm should not load heavy model detail")),
        ):
            result = await service.add_algorithm(task_id, {"model_id": model_id})

        self.assertEqual(result["scene_key"], "fire_alarm")
        self.assertEqual(result["rule_type"], "detection")

    async def test_add_algorithm_persists_without_metadata_reread(self):
        service = TaskService()
        task = {
            "id": "task_add_algorithm_fast",
            "name": "task-add-algorithm-fast",
            "source_url": "rtsp://example/live/task-add-algorithm-fast",
            "source_type": "rtsp",
            "channel_id": "ch_add_algorithm_fast",
            "frame_interval": 10,
            "status": "stopped",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "regions": [],
            "algorithms": [],
            "runtime": {
                "status": "stopped",
                "is_running": False,
            },
        }

        with (
            patch.object(service, "get_task", AsyncMock(return_value=task)),
            patch.object(service, "_apply_model_scene_defaults", AsyncMock(return_value={"model_id": "model_add_fast"})),
            patch.object(service, "_persist_task", AsyncMock(return_value={"algorithms": []})) as persist_mock,
        ):
            await service.add_algorithm(task["id"], {"model_id": "model_add_fast"})

        self.assertEqual(persist_mock.await_args.kwargs.get("reload_metadata"), False)

    async def test_delete_algorithm_persists_without_metadata_reread(self):
        service = TaskService()
        task = {
            "id": "task_delete_algorithm_fast",
            "name": "task-delete-algorithm-fast",
            "source_url": "rtsp://example/live/task-delete-algorithm-fast",
            "source_type": "rtsp",
            "channel_id": "ch_delete_algorithm_fast",
            "frame_interval": 10,
            "status": "stopped",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "regions": [],
            "algorithms": [
                {
                    "id": "algo_delete_fast",
                    "algorithm_type": "detection",
                    "scene_key": "fire_detection",
                    "scene_name": "火情检测",
                    "rule_type": "detection",
                    "model_id": "model_delete_fast",
                    "enabled": True,
                    "labels": ["fire"],
                    "detect_all_classes": False,
                    "label_source": "recommended",
                    "confidence": 0.25,
                    "alert_interval": 60,
                    "need_snapshot": True,
                    "need_video_clip": True,
                    "video_before_seconds": 3,
                    "video_after_seconds": 3,
                    "need_overlay": False,
                    "requires_region": False,
                    "overlay_color": "#ff4d4f",
                    "postprocess_config": {},
                    "region_ids": [],
                    "regions": [],
                }
            ],
            "runtime": {
                "status": "stopped",
                "is_running": False,
            },
        }

        with (
            patch.object(service, "get_task", AsyncMock(return_value=task)),
            patch.object(service, "_persist_task", AsyncMock(return_value={"ok": True})) as persist_mock,
        ):
            result = await service.delete_algorithm(task["id"], "algo_delete_fast")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(persist_mock.await_args.kwargs.get("reload_metadata"), False)


if __name__ == "__main__":
    unittest.main()
