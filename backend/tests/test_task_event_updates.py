import asyncio
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.db.models import Base, ChannelMeta, EventMeta, ModelMetaRecord, RecordingMeta, TaskAlgorithmMeta, TaskMeta
from src.services import metadata_db_sync
from src.services.analysis_service import AnalysisService
from src.services.channel_service import ChannelService
from src.services.event_service import EventService
from src.services.recording_service import RecordingService
from src.services.task_service import TaskService


class _FakeFrame:
    def copy(self):
        return self


class TaskEventUpdatesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.tasks_dir = self.data_dir / "tasks"
        self.events_dir = self.data_dir / "events"
        self.recordings_dir = self.data_dir / "recordings"
        self.channels_dir = self.data_dir / "channels"
        self.snapshots_dir = self.data_dir / "snapshots"
        self.sqlite_path = self.data_dir / "metadata.db"
        for directory in (
            self.tasks_dir,
            self.events_dir,
            self.recordings_dir,
            self.channels_dir,
            self.snapshots_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self.original_tasks_dir = settings.TASKS_DIR
        self.original_events_dir = settings.EVENTS_DIR
        self.original_recordings_dir = settings.RECORDINGS_DIR
        self.original_channels_dir = settings.CHANNELS_DIR
        self.original_snapshots_dir = settings.SNAPSHOTS_DIR
        self.original_metadata_db_enabled = settings.METADATA_DB_ENABLED
        self.original_metadata_db_strict = settings.METADATA_DB_STRICT
        self.original_task_analysis_startup_warmup_seconds = getattr(
            settings,
            "TASK_ANALYSIS_STARTUP_WARMUP_SECONDS",
            0.0,
        )
        self.original_alarm_video_before_seconds = getattr(settings, "ALARM_VIDEO_BEFORE_SECONDS", 3)
        self.original_alarm_video_after_seconds = getattr(settings, "ALARM_VIDEO_AFTER_SECONDS", 3)
        settings.TASKS_DIR = self.tasks_dir
        settings.EVENTS_DIR = self.events_dir
        settings.RECORDINGS_DIR = self.recordings_dir
        settings.CHANNELS_DIR = self.channels_dir
        settings.SNAPSHOTS_DIR = self.snapshots_dir
        settings.METADATA_DB_ENABLED = True
        settings.METADATA_DB_STRICT = False

        self.original_engine = metadata_db_sync.engine
        self.original_session_local = metadata_db_sync.SessionLocal

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
        Base.metadata.create_all(bind=self.engine)

        from src.services import analysis_service as analysis_service_module
        from src.services import channel_service as channel_service_module
        from src.services import event_service as event_service_module
        from src.services import recording_service as recording_service_module
        from src.services import task_service as task_service_module

        self.original_analysis_service_session_local = analysis_service_module.SessionLocal
        self.original_channel_service_session_local = channel_service_module.SessionLocal
        self.original_event_service_session_local = event_service_module.SessionLocal
        self.original_recording_service_session_local = recording_service_module.SessionLocal
        self.original_task_service_session_local = task_service_module.SessionLocal
        self.original_task_channel_service = task_service_module.channel_service

        analysis_service_module.SessionLocal = self.session_local
        channel_service_module.SessionLocal = self.session_local
        event_service_module.SessionLocal = self.session_local
        recording_service_module.SessionLocal = self.session_local
        task_service_module.SessionLocal = self.session_local
        task_service_module.channel_service = ChannelService()

        self.task_service = TaskService()
        self.event_service = EventService()
        self.recording_service = RecordingService()
        self.analysis_service = AnalysisService()

    async def asyncTearDown(self):
        metadata_db_sync.engine = self.original_engine
        metadata_db_sync.SessionLocal = self.original_session_local

        from src.services import analysis_service as analysis_service_module
        from src.services import channel_service as channel_service_module
        from src.services import event_service as event_service_module
        from src.services import recording_service as recording_service_module
        from src.services import task_service as task_service_module

        analysis_service_module.SessionLocal = self.original_analysis_service_session_local
        channel_service_module.SessionLocal = self.original_channel_service_session_local
        event_service_module.SessionLocal = self.original_event_service_session_local
        recording_service_module.SessionLocal = self.original_recording_service_session_local
        task_service_module.SessionLocal = self.original_task_service_session_local
        task_service_module.channel_service = self.original_task_channel_service

        settings.TASKS_DIR = self.original_tasks_dir
        settings.EVENTS_DIR = self.original_events_dir
        settings.RECORDINGS_DIR = self.original_recordings_dir
        settings.CHANNELS_DIR = self.original_channels_dir
        settings.SNAPSHOTS_DIR = self.original_snapshots_dir
        settings.METADATA_DB_ENABLED = self.original_metadata_db_enabled
        settings.METADATA_DB_STRICT = self.original_metadata_db_strict
        settings.TASK_ANALYSIS_STARTUP_WARMUP_SECONDS = self.original_task_analysis_startup_warmup_seconds
        settings.ALARM_VIDEO_BEFORE_SECONDS = self.original_alarm_video_before_seconds
        settings.ALARM_VIDEO_AFTER_SECONDS = self.original_alarm_video_after_seconds

        self.engine.dispose()
        self.temp_dir.cleanup()

    def _insert_channel(self, channel_id: str, channel_name: str) -> None:
        payload = {
            "id": channel_id,
            "name": channel_name,
            "type": "rtsp",
            "source_url": f"rtsp://example/live/{channel_id}",
            "source_type": "rtsp",
            "app": "live",
            "stream": channel_id,
            "is_online": True,
            "is_playing": False,
            "created_at": "2026-07-01 10:00:00",
            "updated_at": "2026-07-01 10:00:00",
            "ext": {},
        }
        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id=payload["id"],
                    device_id=None,
                    media_node_id=None,
                    name=payload["name"],
                    type=payload["type"],
                    source_url=payload["source_url"],
                    app=payload["app"],
                    stream=payload["stream"],
                    is_online=payload["is_online"],
                    is_playing=payload["is_playing"],
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api=None,
                    hls=None,
                    created_at=None,
                    updated_at=None,
                    raw_json=payload,
                )
            )
            session.commit()

    async def test_task_list_and_detail_include_channel_name(self):
        self._insert_channel("ch_gate", "大门口")

        task = await self.task_service.create_task(
            {
                "name": "人员任务",
                "channel_id": "ch_gate",
                "frame_interval": 10,
            }
        )

        listed = await self.task_service.list_tasks()
        detail = await self.task_service.get_task(task["id"])

        listed_item = next(item for item in listed["items"] if item["id"] == task["id"])
        self.assertEqual(task["channel_name"], "大门口")
        self.assertEqual(listed_item["channel_name"], "大门口")
        self.assertEqual(detail["channel_name"], "大门口")

        with self.session_local() as session:
            row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one()
        self.assertEqual(row.channel_name, "大门口")

    async def test_delete_event_media_removes_snapshot_video_and_alarm_recording(self):
        snapshot_path = self.snapshots_dir / "evt_snapshot.jpg"
        video_path = self.recordings_dir / "evt_alarm.mp4"
        snapshot_path.write_bytes(b"snapshot")
        video_path.write_bytes(b"video")

        event = await self.event_service.create_event(
            {
                "channel_id": "ch_gate",
                "task_id": "task_media",
                "algorithm_id": "algo_media",
                "model_id": "model_media",
                "event_type": "detection",
                "class_name": "person",
                "conf": 0.93,
                "snapshot_path": str(snapshot_path.resolve()),
                "video_path": str(video_path.resolve()),
                "status": "new",
            }
        )

        await self.recording_service.create_recording(
            {
                "id": event["id"],
                "channel_id": "ch_gate",
                "task_id": "task_media",
                "algorithm_id": "algo_media",
                "file_path": str(video_path.resolve()),
                "status": "ready",
                "raw_json": {
                    "event_id": event["id"],
                    "video_path": str(video_path.resolve()),
                },
            }
        )

        event_json_path = self.event_service._get_path(event["id"])
        if event_json_path.exists():
            event_json_path.unlink()

        result = await self.event_service.delete_event_media(event["id"])
        updated = await self.event_service.get_event(event["id"])

        self.assertTrue(result["deleted_snapshot"])
        self.assertTrue(result["deleted_video"])
        self.assertTrue(result["deleted_recording"])
        self.assertFalse(snapshot_path.exists())
        self.assertFalse(video_path.exists())
        self.assertIsNotNone(updated)
        self.assertIsNone(updated["snapshot_path"])
        self.assertIsNone(updated["video_path"])

        with self.session_local() as session:
            recording_row = session.execute(
                select(RecordingMeta).where(RecordingMeta.id == event["id"])
            ).scalar_one_or_none()
            event_row = session.execute(
                select(EventMeta).where(EventMeta.id == event["id"])
            ).scalar_one()
        self.assertIsNone(recording_row)
        self.assertIsNone(event_row.snapshot_path)
        self.assertIsNone(event_row.video_path)

    async def test_event_service_writes_event_updates_directly_to_metadata_db(self):
        event = await self.event_service.create_event(
            {
                "channel_id": "ch_db_event",
                "task_id": "task_db_event",
                "algorithm_id": "algo_db_event",
                "model_id": "model_db_event",
                "class_name": "person",
            }
        )

        event_json_path = self.event_service._get_path(event["id"])
        self.assertFalse(event_json_path.exists())

        request = type("Req", (), {
            "event_type": None,
            "class_name": None,
            "conf": None,
            "snapshot_path": None,
            "video_path": "/app/data/recordings/event_db_alarm.mp4",
            "handling_status": None,
            "remark": None,
            "status": None,
        })()
        updated = await self.event_service.update_event(event["id"], request)

        self.assertEqual(updated["video_path"], "/app/data/recordings/event_db_alarm.mp4")
        self.assertFalse(event_json_path.exists())
        with self.session_local() as session:
            row = session.execute(select(EventMeta).where(EventMeta.id == event["id"])).scalar_one()
        self.assertEqual(row.video_path, "/app/data/recordings/event_db_alarm.mp4")
        self.assertEqual((row.raw_json or {}).get("video_path"), "/app/data/recordings/event_db_alarm.mp4")

    async def test_event_service_update_event_sync_writes_metadata_db(self):
        event = await self.event_service.create_event(
            {
                "channel_id": "ch_sync_event",
                "task_id": "task_sync_event",
                "algorithm_id": "algo_sync_event",
                "model_id": "model_sync_event",
                "class_name": "person",
            }
        )

        request = type("Req", (), {
            "event_type": None,
            "class_name": None,
            "conf": None,
            "snapshot_path": None,
            "video_path": "/app/data/recordings/event_sync_alarm.mp4",
            "handling_status": None,
            "remark": None,
            "status": None,
        })()

        updated = self.event_service.update_event_sync(event["id"], request)

        self.assertIsNotNone(updated)
        self.assertEqual(updated["video_path"], "/app/data/recordings/event_sync_alarm.mp4")
        with self.session_local() as session:
            row = session.execute(select(EventMeta).where(EventMeta.id == event["id"])).scalar_one()
        self.assertEqual(row.video_path, "/app/data/recordings/event_sync_alarm.mp4")

    async def test_recording_service_create_recording_sync_writes_metadata_db(self):
        created = self.recording_service.create_recording_sync(
            {
                "id": "rec_sync_demo",
                "channel_id": "ch_sync_recording",
                "task_id": "task_sync_recording",
                "algorithm_id": "algo_sync_recording",
                "started_at": "2026-07-03T18:58:13",
                "ended_at": "2026-07-03T18:58:28",
                "duration": 15,
                "file_path": "/app/data/recordings/rec_sync_demo.mp4",
                "status": "ready",
                "raw_json": {
                    "event_id": "evt_sync_demo",
                    "video_path": "/app/data/recordings/rec_sync_demo.mp4",
                },
            }
        )

        self.assertEqual(created["id"], "rec_sync_demo")
        with self.session_local() as session:
            row = session.execute(select(RecordingMeta).where(RecordingMeta.id == "rec_sync_demo")).scalar_one()
        self.assertEqual(row.file_path, "/app/data/recordings/rec_sync_demo.mp4")

    async def test_event_service_ignores_legacy_event_json_when_metadata_db_enabled(self):
        legacy_event_path = self.events_dir / "legacy_event.json"
        legacy_event_path.write_text(
            json.dumps(
                {
                    "id": "legacy_event",
                    "channel_id": "ch_legacy",
                    "event_type": "detection",
                    "class_name": "person",
                    "created_at": "2026-07-03 10:00:00",
                    "updated_at": "2026-07-03 10:00:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        listed = await self.event_service.list_events(page=1, page_size=10)
        detail = await self.event_service.get_event("legacy_event")

        self.assertEqual(listed["items"], [])
        self.assertEqual(listed["total"], 0)
        self.assertIsNone(detail)

    async def test_task_worker_skips_alarm_generation_during_startup_warmup(self):
        self._insert_channel("ch_warmup", "入口")
        task = await self.task_service.create_task(
            {
                "name": "预热任务",
                "channel_id": "ch_warmup",
                "frame_interval": 1,
                "algorithms": [
                    {
                        "id": "algo_warmup",
                        "model_id": "model_warmup",
                        "labels": ["person"],
                        "enabled": True,
                    }
                ],
            }
        )

        stop_event = threading.Event()
        capture = object()
        detection = {
            "class_name": "person",
            "conf": 0.95,
            "x1": 1,
            "y1": 1,
            "x2": 10,
            "y2": 10,
        }

        def _fake_read(_capture):
            stop_event.set()
            return True, _FakeFrame()

        with (
            patch.object(self.analysis_service, "_build_task_capture", return_value=capture),
            patch.object(self.analysis_service, "_read_task_frame", side_effect=_fake_read),
            patch.object(self.analysis_service, "_release_task_capture"),
            patch.object(self.analysis_service, "_load_model", return_value=(object(), {"classes": ["person"]})),
            patch.object(self.analysis_service, "_detect_frame", return_value=[detection]),
            patch.object(self.analysis_service, "_apply_algorithm_scoped_filters", return_value=[detection]),
            patch.object(self.analysis_service, "_build_rule_events", return_value=[detection]),
            patch.object(self.analysis_service, "_save_event_snapshot", side_effect=AssertionError("warmup should suppress snapshot creation")),
            patch.object(self.analysis_service.events_service, "create_event", AsyncMock(return_value={"id": "evt_warmup"})) as create_event_mock,
            patch.object(self.analysis_service, "_task_analysis_startup_warmup_seconds", return_value=30.0, create=True),
            patch("src.services.analysis_service.time.sleep", lambda *_args, **_kwargs: None),
        ):
            self.analysis_service._run_task_worker(task["id"], stop_event)

        self.assertEqual(create_event_mock.await_count, 0)

    async def test_task_worker_publishes_filtered_realtime_detections(self):
        self._insert_channel("ch_realtime", "实时检测通道")
        task = await self.task_service.create_task(
            {
                "name": "实时检测任务",
                "channel_id": "ch_realtime",
                "frame_interval": 1,
                "algorithms": [
                    {
                        "id": "algo_person",
                        "model_id": "model_person",
                        "scene_name": "Person detection",
                        "overlay_color": "#22c55e",
                        "labels": ["person"],
                        "enabled": True,
                        "need_snapshot": False,
                        "need_video_clip": False,
                    },
                    {
                        "id": "algo_vehicle",
                        "model_id": "model_vehicle",
                        "scene_name": "Vehicle detection",
                        "labels": ["car"],
                        "enabled": True,
                        "need_snapshot": False,
                        "need_video_clip": False,
                    },
                ],
            }
        )

        stop_event = threading.Event()
        capture = object()
        detection = {
            "class_name": "person",
            "conf": 0.95,
            "x1": 1,
            "y1": 2,
            "x2": 10,
            "y2": 12,
            "zone_id": "zone_gate",
        }

        def _fake_read(_capture):
            if not stop_event.is_set():
                stop_event.set()
                return True, np.zeros((16, 32, 3), dtype=np.uint8)
            return False, None

        def _fake_load_model(model_id, device=None):
            return model_id, {"classes": ["person"]}

        def _fake_detect(model, _model_meta, _frame, _confidence):
            return [detection] if model == "model_person" else []

        with (
            patch.object(self.analysis_service, "_build_task_capture", return_value=capture),
            patch.object(self.analysis_service, "_read_task_frame", side_effect=_fake_read),
            patch.object(self.analysis_service, "_release_task_capture"),
            patch.object(self.analysis_service, "_load_model", side_effect=_fake_load_model),
            patch.object(self.analysis_service, "_detect_frame", side_effect=_fake_detect),
            patch.object(
                self.analysis_service,
                "_apply_algorithm_scoped_filters",
                side_effect=lambda detections, _task, _algorithm: detections,
            ),
            patch.object(self.analysis_service, "_build_rule_events", return_value=[]),
            patch("src.services.analysis_service.realtime_detection_hub.publish") as publish_mock,
            patch.object(self.analysis_service, "_task_analysis_startup_warmup_seconds", return_value=0.0, create=True),
            patch("src.services.analysis_service.time.sleep", lambda *_args, **_kwargs: None),
        ):
            await asyncio.to_thread(self.analysis_service._run_task_worker, task["id"], stop_event)

        self.assertEqual(publish_mock.call_count, 1)
        task_id, payload = publish_mock.call_args.args
        self.assertEqual(task_id, task["id"])
        self.assertEqual(payload["source_width"], 32)
        self.assertEqual(payload["source_height"], 16)
        self.assertEqual([item["algorithm_id"] for item in payload["algorithms"]], ["algo_person", "algo_vehicle"])
        self.assertEqual(payload["algorithms"][0]["detections"][0]["label"], "person")
        self.assertEqual(payload["algorithms"][1]["detections"], [])

    async def test_task_worker_submits_alarm_video_job_without_inline_video_encoding(self):
        self._insert_channel("ch_async_video", "卸料口")
        task = await self.task_service.create_task(
            {
                "name": "异步视频任务",
                "channel_id": "ch_async_video",
                "frame_interval": 1,
                "algorithms": [
                    {
                        "id": "algo_async_video",
                        "model_id": "model_async_video",
                        "labels": ["person"],
                        "enabled": True,
                        "need_video_clip": True,
                    }
                ],
            }
        )

        stop_event = threading.Event()
        capture = object()
        detection = {
            "class_name": "person",
            "conf": 0.95,
            "x1": 1,
            "y1": 1,
            "x2": 10,
            "y2": 10,
        }

        def _fake_read(_capture):
            if not stop_event.is_set():
                stop_event.set()
                return True, np.zeros((16, 16, 3), dtype=np.uint8)
            return False, None

        with (
            patch.object(self.analysis_service, "_build_task_capture", return_value=capture),
            patch.object(self.analysis_service, "_read_task_frame", side_effect=_fake_read),
            patch.object(self.analysis_service, "_release_task_capture"),
            patch.object(self.analysis_service, "_load_model", return_value=(object(), {"classes": ["person"]})),
            patch.object(self.analysis_service, "_detect_frame", return_value=[detection]),
            patch.object(self.analysis_service, "_apply_algorithm_scoped_filters", return_value=[detection]),
            patch.object(self.analysis_service, "_build_rule_events", return_value=[detection]),
            patch.object(self.analysis_service, "_save_event_snapshot", return_value="/tmp/async_snapshot.jpg"),
            patch.object(self.analysis_service, "_submit_task_alarm_video_job") as submit_video_job_mock,
            patch.object(self.analysis_service, "_save_alarm_video", side_effect=AssertionError("task worker should not encode video inline")),
            patch.object(self.analysis_service.events_service, "create_event", AsyncMock(return_value={"id": "evt_async_video", "event_type": "detection"})),
            patch.object(self.analysis_service, "_task_analysis_startup_warmup_seconds", return_value=0.0, create=True),
            patch("src.services.analysis_service.time.sleep", lambda *_args, **_kwargs: None),
        ):
            await asyncio.to_thread(self.analysis_service._run_task_worker, task["id"], stop_event)

        self.assertEqual(submit_video_job_mock.call_count, 1)

    async def test_task_worker_passes_consecutive_after_frames_to_alarm_video_job(self):
        self._insert_channel("ch_continuous_video", "连续报警视频通道")
        task = await self.task_service.create_task(
            {
                "name": "连续报警视频任务",
                "channel_id": "ch_continuous_video",
                "frame_interval": 1,
                "algorithms": [
                    {
                        "id": "algo_continuous_video",
                        "model_id": "model_continuous_video",
                        "labels": ["person"],
                        "enabled": True,
                        "need_video_clip": True,
                    }
                ],
            }
        )

        stop_event = threading.Event()
        capture = object()
        detection = {
            "class_name": "person",
            "conf": 0.95,
            "x1": 1,
            "y1": 1,
            "x2": 10,
            "y2": 10,
        }
        source_frames = [
            np.full((16, 16, 3), 10, dtype=np.uint8),
            np.full((16, 16, 3), 20, dtype=np.uint8),
            np.full((16, 16, 3), 30, dtype=np.uint8),
        ]
        frame_index = 0

        def _fake_read(_capture):
            nonlocal frame_index
            if frame_index >= len(source_frames):
                stop_event.set()
                return False, None
            frame = source_frames[frame_index]
            frame_index += 1
            if frame_index == len(source_frames):
                stop_event.set()
            return True, frame

        with (
            patch.object(self.analysis_service, "_build_task_capture", return_value=capture),
            patch.object(self.analysis_service, "_read_task_frame", side_effect=_fake_read),
            patch.object(self.analysis_service, "_release_task_capture"),
            patch.object(self.analysis_service, "_load_model", return_value=(object(), {"classes": ["person"]})),
            patch.object(self.analysis_service, "_detect_frame", return_value=[detection]),
            patch.object(self.analysis_service, "_apply_algorithm_scoped_filters", return_value=[detection]),
            patch.object(self.analysis_service, "_build_rule_events", return_value=[detection]),
            patch.object(self.analysis_service, "_save_event_snapshot", return_value="/tmp/continuous_snapshot.jpg"),
            patch.object(self.analysis_service, "_submit_task_alarm_video_job") as submit_video_job_mock,
            patch.object(self.analysis_service, "_save_alarm_video", side_effect=AssertionError("task worker should not encode video inline")),
            patch.object(self.analysis_service.events_service, "create_event", AsyncMock(return_value={"id": "evt_continuous_video", "event_type": "detection"})),
            patch.object(self.analysis_service, "_task_analysis_startup_warmup_seconds", return_value=0.0, create=True),
            patch.object(self.analysis_service, "_alarm_video_before_seconds", return_value=0),
            patch("src.services.analysis_service.time.sleep", lambda *_args, **_kwargs: None),
        ):
            await asyncio.to_thread(self.analysis_service._run_task_worker, task["id"], stop_event)

        self.assertEqual(submit_video_job_mock.call_count, 1)
        after_frames = submit_video_job_mock.call_args.kwargs["after_frames"]
        self.assertEqual(len(after_frames), 2)
        decoded_after = [self.analysis_service._decode_alarm_video_frame(frame) for _, frame in after_frames]
        self.assertLess(float(np.mean(decoded_after[0])), float(np.mean(decoded_after[1])))

    async def test_analysis_service_loads_alert_interval_from_model_metadata(self):
        self._insert_channel("ch_model_interval", "模型间隔通道")
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="model_interval_runtime",
                    name="人员闯入模型",
                    confidence=0.25,
                    alert_interval=300,
                    raw_json={
                        "id": "model_interval_runtime",
                        "name": "人员闯入模型",
                        "alert_interval": 300,
                    },
                )
            )
            session.commit()

        task = await self.task_service.create_task(
            {
                "name": "模型间隔任务",
                "channel_id": "ch_model_interval",
                "frame_interval": 1,
                "algorithms": [
                    {
                        "id": "algo_model_interval",
                        "model_id": "model_interval_runtime",
                        "labels": ["person"],
                        "enabled": True,
                        "alert_interval": 30,
                    }
                ],
            }
        )
        with self.session_local() as session:
            row = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.id == task["algorithms"][0]["id"])
            ).scalar_one()
            row.alert_interval = 30
            session.commit()

        loaded = self.analysis_service._load_task(task["id"])
        task_detail = await self.task_service.get_task(task["id"])

        self.assertEqual(loaded["algorithms"][0]["alert_interval"], 300)
        self.assertEqual(task_detail["algorithms"][0]["alert_interval"], 300)

    async def test_recording_service_get_recording_reads_metadata_db_first(self):
        with self.session_local() as session:
            session.merge(
                RecordingMeta(
                    id="rec_db_only",
                    channel_id="ch_db_recording",
                    task_id="task_db_recording",
                    algorithm_id="algo_db_recording",
                    file_path="/app/data/recordings/rec_db_only.mp4",
                    status="ready",
                    raw_json={
                        "id": "rec_db_only",
                        "event_id": "evt_db_only",
                        "file_path": "/app/data/recordings/rec_db_only.mp4",
                    },
                )
            )
            session.commit()

        recording = await self.recording_service.get_recording("rec_db_only")

        self.assertIsNotNone(recording)
        self.assertEqual(recording["file_path"], "/app/data/recordings/rec_db_only.mp4")

    async def test_event_service_falls_back_to_json_when_metadata_write_fails(self):
        class _BrokenSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def add(self, *_args, **_kwargs):
                return None

            def commit(self):
                raise RuntimeError("metadata unavailable")

        with patch("src.services.event_service.SessionLocal", return_value=_BrokenSession()):
            event = await self.event_service.create_event(
                {
                    "channel_id": "ch_fallback",
                    "task_id": "task_fallback",
                    "algorithm_id": "algo_fallback",
                    "model_id": "model_fallback",
                    "class_name": "person",
                    "conf": 0.9,
                }
            )

        event_path = self.events_dir / f"{event['id']}.json"
        self.assertTrue(event_path.exists())
        stored = json.loads(event_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["task_id"], "task_fallback")
        self.assertEqual(stored["class_name"], "person")
        with self.session_local() as session:
            self.assertEqual(session.query(EventMeta).count(), 0)

    async def test_analysis_service_runtime_update_falls_back_to_json_when_metadata_write_fails(self):
        self._insert_channel("ch_runtime_fallback", "运行态回退通道")
        task = await self.task_service.create_task(
            {
                "name": "运行态回退任务",
                "channel_id": "ch_runtime_fallback",
                "frame_interval": 5,
            }
        )

        class _BrokenSession:
            def __enter__(self):
                raise RuntimeError("metadata unavailable")

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch("src.services.analysis_service.SessionLocal", return_value=_BrokenSession()):
            self.analysis_service._update_task_runtime(
                task["id"],
                status="running",
                last_analysis_at="2026-07-15 10:50:00",
                frame_count=42,
            )

        cached_task = json.loads((self.tasks_dir / f"{task['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(cached_task["status"], "running")
        self.assertEqual(cached_task["runtime"]["status"], "running")
        self.assertEqual(cached_task["runtime"]["frame_count"], 42)
        self.assertEqual(cached_task["runtime"]["last_analysis_at"], "2026-07-15 10:50:00")

    async def test_task_service_normalizes_alarm_video_window_to_ten_seconds_each_side(self):
        self._insert_channel("ch_video_window", "皮带口")
        task = await self.task_service.create_task(
            {
                "name": "视频窗口任务",
                "channel_id": "ch_video_window",
                "algorithms": [
                    {
                        "model_id": "model_person",
                        "video_before_seconds": 1,
                        "video_after_seconds": 2,
                    }
                ],
            }
        )

        algorithm = task["algorithms"][0]
        self.assertEqual(algorithm["video_before_seconds"], 10)
        self.assertEqual(algorithm["video_after_seconds"], 10)


class AnalysisVideoWindowTest(unittest.TestCase):
    def test_alarm_video_frames_use_twenty_second_window(self):
        service = AnalysisService()
        frames = [(float(index), object()) for index in range(1000)]

        selected = service._build_alarm_video_frames(
            before_frames=frames[:500],
            after_frames=frames[500:],
            fps=25.0,
            before_seconds=10,
            after_seconds=10,
        )

        self.assertEqual(len(selected), 500)
        self.assertEqual(selected[0][0], 250.0)
        self.assertEqual(selected[-1][0], 749.0)

    def test_alarm_recording_payload_uses_twenty_second_duration(self):
        service = AnalysisService()
        payload = service._build_alarm_recording_payload(
            event={"id": "evt_demo", "event_type": "detection"},
            channel_id="ch_demo",
            task_id="task_demo",
            algorithm_id="algo_demo",
            model_id="model_demo",
            detection={"class_name": "person", "conf": 0.91},
            snapshot_path="/tmp/snapshot.jpg",
            video_path="/tmp/video.mp4",
            video_frames=[(100.0, object()), (120.0, object())],
            fps=25.0,
        )

        self.assertEqual(payload["duration"], 20)
