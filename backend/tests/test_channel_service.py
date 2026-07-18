import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.db.models import Base, ChannelMeta, TaskMeta
from src.services import channel_service as channel_service_module
from src.services import metadata_db_sync
from src.services.channel_service import ChannelService
from src.db.models import ChannelMeta
from src.services.platform_storage import save_json as persist_json


class ChannelServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_wvp_channel_uses_upstream_online_when_db_column_is_false(self):
        service = ChannelService()
        row = ChannelMeta(
            id="wvp_stream_1",
            name="大门口",
            type="wvp",
            source_url="rtsp://example.test/live",
            app="rtp",
            stream="wvp_stream_1",
            is_online=False,
            is_playing=False,
            raw_json={
                "id": "wvp_stream_1",
                "name": "大门口",
                "type": "wvp",
                "is_online": False,
                "ext": {
                    "upstream": {
                        "platform": "wvp",
                        "online": True,
                    }
                },
            },
        )

        channel = service._build_channel_from_metadata_row(row)

        self.assertTrue(channel["is_online"])

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.channels_dir = Path(self.temp_dir.name) / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = Path(self.temp_dir.name) / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = Path(self.temp_dir.name) / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path = Path(self.temp_dir.name) / "metadata.db"

        self.original_channels_dir = settings.CHANNELS_DIR
        self.original_tasks_dir = settings.TASKS_DIR
        self.original_snapshots_dir = settings.SNAPSHOTS_DIR
        self.original_metadata_db_enabled = settings.METADATA_DB_ENABLED
        self.original_engine = metadata_db_sync.engine
        self.original_session_local = metadata_db_sync.SessionLocal
        self.original_channel_service_session_local = channel_service_module.SessionLocal
        settings.CHANNELS_DIR = self.channels_dir
        settings.TASKS_DIR = self.tasks_dir
        settings.SNAPSHOTS_DIR = self.snapshots_dir
        settings.METADATA_DB_ENABLED = True

        self.engine = create_engine(
            f"sqlite:///{self.sqlite_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.session_local = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        metadata_db_sync.engine = self.engine
        metadata_db_sync.SessionLocal = self.session_local
        channel_service_module.SessionLocal = self.session_local
        Base.metadata.create_all(bind=self.engine)

    async def asyncTearDown(self):
        settings.CHANNELS_DIR = self.original_channels_dir
        settings.TASKS_DIR = self.original_tasks_dir
        settings.SNAPSHOTS_DIR = self.original_snapshots_dir
        settings.METADATA_DB_ENABLED = self.original_metadata_db_enabled
        self.engine.dispose()
        metadata_db_sync.engine = self.original_engine
        metadata_db_sync.SessionLocal = self.original_session_local
        channel_service_module.SessionLocal = self.original_channel_service_session_local
        self.temp_dir.cleanup()

    def _insert_channel_meta(self, payload: dict):
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

    def _insert_task_meta(self, payload: dict):
        runtime = dict(payload.get("runtime") or {})
        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id=payload["id"],
                    name=payload.get("name"),
                    source_url=payload.get("source_url"),
                    source_type=payload.get("source_type"),
                    channel_id=payload.get("channel_id"),
                    channel_name=payload.get("channel_name"),
                    frame_interval=int(payload.get("frame_interval", 10) or 10),
                    status=runtime.get("status") or payload.get("status") or "stopped",
                    algorithm_count=len(list(payload.get("algorithms") or [])),
                    region_count=len(list(payload.get("regions") or [])),
                    is_running=bool(runtime.get("is_running")),
                    started_at=None,
                    stopped_at=None,
                    last_analysis_at=None,
                    last_event_at=None,
                    last_error=runtime.get("last_error"),
                    created_at=None,
                    updated_at=None,
                    raw_json=payload,
                )
            )
            session.commit()

    def _build_cached_zone_preview_channel_payload(self, channel_id: str) -> dict:
        return {
            "id": channel_id,
            "name": "cached-zone-preview",
            "type": "rtsp",
            "source_url": "rtsp://example/live/cached-zone-preview",
            "app": "live",
            "stream": "cached_zone_preview",
            "webrtc_api": "http://192.168.60.61:3000/proxy/sms/index/api/webrtc?app=live&stream=cached_zone_preview_zone&type=play",
            "hls": "http://192.168.60.61:3000/proxy/sms/live/cached_zone_preview_zone/hls.fmp4.m3u8",
            "is_online": True,
            "is_playing": True,
            "created_at": "2026-07-01 09:50:00",
            "updated_at": "2026-07-01 09:50:00",
            "ext": {
                "task_preview": {
                    "prepared": True,
                    "mode": "zone",
                    "owner_task_id": "task_cached_zone",
                    "status": "running",
                },
                "zone_preview": {
                    "enabled": True,
                    "status": "running",
                    "stream": "cached_zone_preview_zone",
                    "zone_source": "task",
                    "owner_task_id": "task_cached_zone",
                    "zone_count": 1,
                },
            },
        }

    async def test_list_available_for_task_channels_uses_task_metadata(self):
        channel_id = "ch_meta_busy"
        persist_json(
            self.channels_dir / f"{channel_id}.json",
            {
                "id": channel_id,
                "name": "json-name",
                "type": "rtsp",
                "source_url": "rtsp://example/live/meta-busy",
                "app": "live",
                "stream": "meta_busy",
                "is_online": True,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "created_at": "2026-06-16 10:00:00",
                "updated_at": "2026-06-16 10:00:00",
                "ext": {},
            },
        )
        persist_json(
            self.channels_dir / "ch_meta_free.json",
            {
                "id": "ch_meta_free",
                "name": "meta-free",
                "type": "rtsp",
                "source_url": "rtsp://example/live/meta-free",
                "app": "live",
                "stream": "meta_free",
                "is_online": True,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "created_at": "2026-06-16 10:00:01",
                "updated_at": "2026-06-16 10:00:01",
                "ext": {},
            },
        )

        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id=channel_id,
                    device_id=None,
                    media_node_id=None,
                    name="db-name",
                    type="rtsp",
                    source_url="rtsp://example/live/meta-busy",
                    app="live",
                    stream="meta_busy",
                    is_online=True,
                    is_playing=False,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api=None,
                    hls=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": channel_id,
                        "name": "db-name",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/meta-busy",
                        "app": "live",
                        "stream": "meta_busy",
                        "is_online": True,
                        "is_playing": False,
                        "ai_enabled": False,
                        "record_mode": "none",
                        "ext": {},
                    },
                )
            )
            session.merge(
                ChannelMeta(
                    id="ch_meta_free",
                    device_id=None,
                    media_node_id=None,
                    name="meta-free",
                    type="rtsp",
                    source_url="rtsp://example/live/meta-free",
                    app="live",
                    stream="meta_free",
                    is_online=True,
                    is_playing=False,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api=None,
                    hls=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "ch_meta_free",
                        "name": "meta-free",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/meta-free",
                        "app": "live",
                        "stream": "meta_free",
                        "is_online": True,
                        "is_playing": False,
                        "ai_enabled": False,
                        "record_mode": "none",
                        "ext": {},
                    },
                )
            )
            session.merge(
                TaskMeta(
                    id="task_meta_busy",
                    name="busy-task",
                    source_url="rtsp://example/live/meta-busy",
                    source_type="rtsp",
                    channel_id=channel_id,
                    frame_interval=10,
                    status="stopped",
                    algorithm_count=1,
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
                        "id": "task_meta_busy",
                        "channel_id": channel_id,
                    },
                )
            )
            session.commit()

        service = ChannelService()
        with patch.object(service, "list_channels", AsyncMock(return_value={"items": [
            {
                "id": channel_id,
                "name": "db-name",
            },
            {
                "id": "ch_meta_free",
                "name": "meta-free",
            },
        ]})):
            result = await service.list_available_for_task_channels()

        self.assertEqual([item["id"] for item in result["items"]], ["ch_meta_free"])

    async def test_available_for_task_ignores_legacy_task_json_when_metadata_enabled(self):
        channel_id = "ch_legacy_task_json"
        persist_json(
            self.tasks_dir / "legacy_task.json",
            {
                "id": "legacy_task",
                "channel_id": channel_id,
            },
        )

        service = ChannelService()
        with patch.object(service, "list_channels", AsyncMock(return_value={"items": [
            {
                "id": channel_id,
                "name": "legacy-json-channel",
            },
        ]})):
            result = await service.list_available_for_task_channels()

        self.assertEqual([item["id"] for item in result["items"]], [channel_id])

    async def test_get_ai_status_uses_bound_task_runtime(self):
        channel_id = "ch_task_status"
        task_id = "task_status_demo"
        self._insert_channel_meta(
            {
                "id": channel_id,
                "name": "task-status-channel",
                "type": "rtsp",
                "source_url": "rtsp://example/live/task-status",
                "app": "live",
                "stream": "task_status_stream",
                "is_online": True,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "ext": {},
            }
        )
        self._insert_task_meta(
            {
                "id": task_id,
                "name": "test-task",
                "channel_id": channel_id,
                "source_url": "rtsp://example/live/task-status",
                "source_type": "rtsp",
                "status": "running",
                "runtime": {
                    "status": "running",
                    "is_running": True,
                },
                "algorithms": [
                    {"id": "algo_a", "enabled": True, "model_id": "model_a"},
                ],
                "regions": [],
            }
        )

        service = ChannelService()
        task_payload = {
            "id": task_id,
            "name": "test-task",
            "channel_id": channel_id,
            "status": "running",
            "runtime": {
                "status": "running",
                "is_running": True,
            },
            "algorithms": [
                {"id": "algo_a", "enabled": True, "model_id": "model_a"},
            ],
        }
        task_status = {
            "task_id": task_id,
            "status": "running",
            "runtime": {
                "status": "running",
                "is_running": True,
                "frame_count": 12,
                "event_count": 3,
                "models": {"algo_a": {"detections": 1}},
            },
        }

    async def test_get_channel_prefers_metadata_row(self):
        channel_id = "ch_detail_db_first"
        persist_json(
            self.channels_dir / f"{channel_id}.json",
            {
                "id": channel_id,
                "name": "json-channel-name",
                "type": "rtsp",
                "source_url": "rtsp://example/live/detail",
                "app": "live",
                "stream": "detail_db_first",
                "is_online": True,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "created_at": "2026-06-16 12:00:00",
                "updated_at": "2026-06-16 12:00:00",
                "ext": {
                    "upstream": {
                        "platform": "wvp",
                    }
                },
            },
        )

        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id=channel_id,
                    device_id=None,
                    media_node_id=None,
                    name="db-channel-name",
                    type="rtsp",
                    source_url="rtsp://example/live/detail",
                    app="live",
                    stream="detail_db_first",
                    is_online=True,
                    is_playing=False,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api="http://example.com/webrtc",
                    hls="http://example.com/hls.m3u8",
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": channel_id,
                        "name": "db-channel-name",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/detail",
                        "app": "live",
                        "stream": "detail_db_first",
                        "is_online": True,
                        "is_playing": False,
                        "ai_enabled": False,
                        "record_mode": "none",
                        "ext": {
                            "upstream": {
                                "platform": "wvp",
                            }
                        },
                    },
                )
            )
            session.commit()

        service = ChannelService()
        result = await service.get_channel(channel_id)

        self.assertEqual(result["name"], "db-channel-name")
        self.assertEqual(result["webrtc_api"], "http://example.com/webrtc")
        self.assertEqual(result["hls"], "http://example.com/hls.m3u8")

    async def test_get_channel_runtime_prefers_metadata_row_without_json(self):
        channel_id = "ch_runtime_metadata_only"
        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id=channel_id,
                    device_id="dev-runtime",
                    media_node_id=None,
                    name="runtime-metadata-only",
                    type="rtsp",
                    source_url="rtsp://example/live/runtime-metadata-only",
                    app="live",
                    stream="runtime_metadata_only",
                    is_online=True,
                    is_playing=True,
                    record_mode="none",
                    snapshot_url="/static/snapshots/runtime.jpg",
                    snapshot_path=str((self.snapshots_dir / "runtime.jpg").resolve()),
                    webrtc_api="http://example.com/runtime.webrtc",
                    hls="http://example.com/runtime.m3u8",
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": channel_id,
                        "device_id": "dev-runtime",
                        "name": "runtime-metadata-only",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/runtime-metadata-only",
                        "app": "live",
                        "stream": "runtime_metadata_only",
                        "is_online": True,
                        "is_playing": True,
                        "ai_enabled": False,
                        "record_mode": "none",
                        "ext": {
                            "task_preview": {
                                "prepared": True,
                                "mode": "channel",
                                "owner_task_id": "task-runtime",
                                "status": "running",
                            }
                        },
                    },
                )
            )
            session.commit()

        service = ChannelService()
        result = await service.get_channel_runtime(channel_id)

        self.assertEqual(result["id"], channel_id)
        self.assertEqual(result["name"], "runtime-metadata-only")
        self.assertEqual(result["webrtc_api"], "http://example.com/runtime.webrtc")
        self.assertEqual(result["hls"], "http://example.com/runtime.m3u8")

    async def test_restore_channel_proxies_resets_metadata_playing_state_without_autoplay(self):
        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id="ch_restore_playing",
                    device_id=None,
                    media_node_id=None,
                    name="restore-playing",
                    type="rtsp",
                    source_url="rtsp://example/live/restore-playing",
                    app="live",
                    stream="restore_playing",
                    is_online=True,
                    is_playing=True,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api=None,
                    hls=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "ch_restore_playing",
                        "name": "restore-playing",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/restore-playing",
                        "app": "live",
                        "stream": "restore_playing",
                        "is_online": True,
                        "is_playing": True,
                        "ext": {},
                    },
                )
            )
            session.merge(
                ChannelMeta(
                    id="ch_restore_stopped",
                    device_id=None,
                    media_node_id=None,
                    name="restore-stopped",
                    type="rtsp",
                    source_url="rtsp://example/live/restore-stopped",
                    app="live",
                    stream="restore_stopped",
                    is_online=False,
                    is_playing=False,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api=None,
                    hls=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "ch_restore_stopped",
                        "name": "restore-stopped",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/restore-stopped",
                        "app": "live",
                        "stream": "restore_stopped",
                        "is_online": False,
                        "is_playing": False,
                        "ext": {},
                    },
                )
            )
            session.commit()

        persist_json(
            self.channels_dir / "ch_stale_json_only.json",
            {
                "id": "ch_stale_json_only",
                "name": "stale-json-only",
                "type": "rtsp",
                "source_url": "rtsp://example/live/stale-json-only",
                "app": "live",
                "stream": "stale_json_only",
                "is_online": True,
                "is_playing": True,
                "ext": {},
            },
            sync_metadata=False,
        )

        service = ChannelService()
        with patch.object(service, "play", AsyncMock(return_value={"status": "playing"})) as mocked_play:
            result = await service.restore_channel_proxies()

        self.assertEqual(result["restored"], 0)
        self.assertEqual(result["reset"], 1)
        mocked_play.assert_not_awaited()

        with self.session_local() as session:
            playing_row = session.execute(
                select(ChannelMeta).where(ChannelMeta.id == "ch_restore_playing")
            ).scalar_one()
            stale_row = session.execute(
                select(ChannelMeta).where(ChannelMeta.id == "ch_stale_json_only")
            ).scalar_one_or_none()

        self.assertFalse(playing_row.is_playing)
        self.assertFalse(playing_row.is_online)
        self.assertIsNone(stale_row)

    async def test_list_channels_includes_metadata_only_channel_without_runtime_json(self):
        channel_id = "ch_metadata_only_visible"
        persist_json(
            self.channels_dir / "ch_runtime_existing.json",
            {
                "id": "ch_runtime_existing",
                "device_id": "dev-runtime",
                "name": "runtime-existing",
                "type": "rtsp",
                "source_url": "rtsp://example/live/runtime-existing",
                "app": "live",
                "stream": "runtime_existing",
                "is_online": False,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "created_at": "2026-06-16 15:00:00",
                "updated_at": "2026-06-16 15:00:00",
                "ext": {},
            },
        )
        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id="ch_runtime_existing",
                    device_id="dev-runtime",
                    media_node_id=None,
                    name="runtime-existing",
                    type="rtsp",
                    source_url="rtsp://example/live/runtime-existing",
                    app="live",
                    stream="runtime_existing",
                    is_online=False,
                    is_playing=False,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api=None,
                    hls=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": "ch_runtime_existing",
                        "device_id": "dev-runtime",
                        "name": "runtime-existing",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/runtime-existing",
                        "app": "live",
                        "stream": "runtime_existing",
                        "is_online": False,
                        "is_playing": False,
                        "ai_enabled": False,
                        "record_mode": "none",
                        "ext": {},
                    },
                )
            )
            session.merge(
                ChannelMeta(
                    id=channel_id,
                    device_id="dev-meta-only",
                    media_node_id=None,
                    name="metadata-only-visible",
                    type="rtsp",
                    source_url="rtsp://example/live/metadata-only-visible",
                    app="live",
                    stream="metadata_only_visible",
                    is_online=False,
                    is_playing=False,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api="http://example.com/meta-only.webrtc",
                    hls="http://example.com/meta-only.m3u8",
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": channel_id,
                        "device_id": "dev-meta-only",
                        "name": "metadata-only-visible",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/metadata-only-visible",
                        "app": "live",
                        "stream": "metadata_only_visible",
                        "is_online": False,
                        "is_playing": False,
                        "ai_enabled": False,
                        "record_mode": "none",
                        "webrtc_api": "http://example.com/meta-only.webrtc",
                        "hls": "http://example.com/meta-only.m3u8",
                        "ext": {},
                    },
                )
            )
            session.commit()

        service = ChannelService()
        with patch.object(service, "_inspect_channel_online_state", return_value=(False, {})):
            result = await service.list_channels()

        item = next((entry for entry in result["items"] if entry["id"] == channel_id), None)
        self.assertIsNotNone(item)
        self.assertEqual(item["name"], "metadata-only-visible")
        self.assertEqual(item["device_id"], "dev-meta-only")
        self.assertEqual(item["webrtc_api"], "http://example.com/meta-only.webrtc")
        self.assertEqual(item["hls"], "http://example.com/meta-only.m3u8")

    async def test_play_urls_uses_metadata_row_instead_of_stale_runtime_json(self):
        channel_id = "ch_runtime_zone_preview"
        persist_json(
            self.channels_dir / f"{channel_id}.json",
            {
                "id": channel_id,
                "name": "runtime-zone-preview",
                "type": "rtsp",
                "source_url": "rtsp://example/live/runtime-zone-preview",
                "app": "live",
                "stream": "runtime_zone_preview",
                "media_node_id": None,
                "is_online": True,
                "is_playing": True,
                "ai_enabled": False,
                "record_mode": "none",
                "created_at": "2026-06-16 12:30:00",
                "updated_at": "2026-06-16 12:30:00",
                "ext": {
                    "zone_preview": {
                        "enabled": True,
                        "status": "running",
                        "stream": "runtime_zone_preview_zone",
                    },
                },
            },
        )

        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id=channel_id,
                    device_id=None,
                    media_node_id=None,
                    name="runtime-zone-preview",
                    type="rtsp",
                    source_url="rtsp://example/live/runtime-zone-preview",
                    app="live",
                    stream="runtime_zone_preview",
                    is_online=True,
                    is_playing=True,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api=None,
                    hls=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": channel_id,
                        "name": "runtime-zone-preview",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/runtime-zone-preview",
                        "app": "live",
                        "stream": "runtime_zone_preview",
                        "is_online": True,
                        "is_playing": True,
                        "ai_enabled": False,
                        "record_mode": "none",
                        "ext": {},
                    },
                )
            )
            session.commit()

        service = ChannelService()
        result = await service.play_urls(channel_id, mode="auto")

        self.assertEqual(result["resolved_mode"], "channel")
        self.assertEqual(result["source"], "channel")
        self.assertIsNone(result["zone_preview_urls"])

    async def test_refresh_zone_preview_cached_urls_skips_save_when_urls_unchanged(self):
        channel_id = "ch_zone_cached_url_no_persist"
        channel_payload = {
            "id": channel_id,
            "name": "zone-cached-url-no-persist",
            "type": "rtsp",
            "source_url": "rtsp://example/live/channel",
            "app": "live",
            "stream": "channel",
            "webrtc_api": "http://192.168.60.61:3000/proxy/sms/index/api/webrtc?app=live&stream=channel_zone&type=play",
            "hls": "http://192.168.60.61:3000/proxy/sms/live/channel_zone/hls.fmp4.m3u8",
            "created_at": "2026-06-15 10:00:00",
            "updated_at": "2026-06-15 10:00:00",
            "ext": {
                "zone_preview": {
                    "stream": "channel_zone",
                }
            },
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        service = ChannelService()
        service.channels_dir = self.channels_dir

        unchanged_urls = {
            "webrtc_api": channel_payload["webrtc_api"],
            "hls": channel_payload["hls"],
        }

        with (
            patch.object(service, "_build_zone_preview_browser_urls", return_value=unchanged_urls),
            patch.object(service.media_node_service, "get_node", AsyncMock(return_value=None)),
            patch("src.services.channel_service.save_json") as mocked_save_json,
        ):
            result = await service.refresh_zone_preview_cached_urls(channel_id)

        mocked_save_json.assert_not_called()
        self.assertEqual(result, unchanged_urls)

    async def test_build_browser_play_url_map_prefers_web_public_proxy_urls(self):
        service = ChannelService()
        urls = service._build_browser_play_url_map({
            "browser_webrtc_api": "/proxy/sms/index/api/webrtc?app=rtp&stream=demo_zone&type=play",
            "browser_hls": "/proxy/sms/rtp/demo_zone/hls.fmp4.m3u8",
            "browser_httpflv": "/proxy/sms/rtp/demo_zone.live.flv",
            "browser_webrtc_api_url": "http://192.168.60.61:8010/proxy/sms/index/api/webrtc?app=rtp&stream=demo_zone&type=play",
            "browser_hls_url": "http://192.168.60.61:8010/proxy/sms/rtp/demo_zone/hls.fmp4.m3u8",
            "browser_httpflv_url": "http://192.168.60.61:8010/proxy/sms/rtp/demo_zone.live.flv",
        })

        self.assertTrue(urls["webrtc_api"].startswith("http://"))
        self.assertIn(":3000/proxy/sms/index/api/webrtc", urls["webrtc_api"])
        self.assertTrue(urls["hls"].startswith("http://"))
        self.assertIn(":3000/proxy/sms/rtp/demo_zone/hls.fmp4.m3u8", urls["hls"])
        self.assertTrue(urls["httpflv"].startswith("http://"))
        self.assertIn(":3000/proxy/sms/rtp/demo_zone.live.flv", urls["httpflv"])
        self.assertEqual(urls["flv"], urls["httpflv"])

    async def test_start_task_zone_preview_uses_prepared_channel_preview_without_task_scan(self):
        channel_id = "ch_prepared_task_preview"
        channel_payload = {
            "id": channel_id,
            "name": "prepared-task-preview",
            "type": "rtsp",
            "source_url": "rtsp://example/live/channel",
            "app": "live",
            "stream": "prepared_task_preview",
            "webrtc_api": "http://192.168.60.61:3000/proxy/sms/index/api/webrtc?app=live&stream=prepared_task_preview_zone&type=play",
            "hls": "http://192.168.60.61:3000/proxy/sms/live/prepared_task_preview_zone/hls.fmp4.m3u8",
            "is_online": True,
            "is_playing": True,
            "created_at": "2026-06-15 10:00:00",
            "updated_at": "2026-06-15 10:00:00",
            "ext": {
                "task_preview": {
                    "prepared": True,
                    "mode": "zone",
                    "owner_task_id": "task_demo",
                    "status": "running",
                },
                "zone_preview": {
                    "enabled": True,
                    "status": "running",
                    "stream": "prepared_task_preview_zone",
                    "zone_source": "task",
                    "owner_task_id": "task_demo",
                    "zone_count": 1,
                },
            },
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        service = ChannelService()
        service.channels_dir = self.channels_dir

        with patch("src.services.channel_service.list_json_items", side_effect=AssertionError("should not scan tasks")):
            result = await service.start_task_zone_preview(channel_id)

        self.assertEqual(result["channel_id"], channel_id)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["zone_source"], "task")
        self.assertEqual(result["owner_task_id"], "task_demo")

    async def test_get_zone_preview_status_returns_running_for_prepared_channel_mode(self):
        channel_id = "ch_prepared_channel_mode"
        channel_payload = {
            "id": channel_id,
            "name": "prepared-channel-mode",
            "type": "rtsp",
            "source_url": "rtsp://example/live/channel-mode",
            "app": "live",
            "stream": "prepared_channel_mode",
            "webrtc_api": "http://192.168.60.61:3000/proxy/sms/index/api/webrtc?app=live&stream=prepared_channel_mode_browser&type=play",
            "hls": "http://192.168.60.61:3000/proxy/sms/live/prepared_channel_mode_browser/hls.fmp4.m3u8",
            "is_online": True,
            "is_playing": True,
            "created_at": "2026-06-15 11:00:00",
            "updated_at": "2026-06-15 11:00:00",
            "ext": {
                "task_preview": {
                    "prepared": True,
                    "mode": "channel",
                    "owner_task_id": "task_channel_mode",
                    "status": "running",
                }
            },
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        service = ChannelService()
        service.channels_dir = self.channels_dir

        result = await service.get_zone_preview_status(channel_id)

        self.assertEqual(result["channel_id"], channel_id)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["zone_source"], "channel")
        self.assertEqual(result["owner_task_id"], "task_channel_mode")

    async def test_get_zone_preview_status_bootstraps_running_task_overlay_preview(self):
        channel_id = "ch_bootstrap_task_preview"
        channel_payload = {
            "id": channel_id,
            "name": "bootstrap-task-preview",
            "type": "rtsp",
            "source_url": "rtsp://example/live/bootstrap-task-preview",
            "app": "live",
            "stream": "bootstrap_task_preview",
            "is_online": True,
            "is_playing": False,
            "created_at": "2026-07-01 10:00:00",
            "updated_at": "2026-07-01 10:00:00",
            "ext": {},
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_bootstrap_task_preview",
                    name="bootstrap-task",
                    source_url="rtsp://example/live/bootstrap-task-preview",
                    source_type="rtsp",
                    channel_id=channel_id,
                    frame_interval=10,
                    status="running",
                    algorithm_count=1,
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
                        "id": "task_bootstrap_task_preview",
                        "channel_id": channel_id,
                        "status": "running",
                        "runtime": {
                            "status": "running",
                            "is_running": True,
                        },
                        "algorithms": [
                            {
                                "id": "algo_bootstrap_task_preview",
                                "model_id": "model_person",
                                "enabled": True,
                                "need_overlay": True,
                                "regions": [
                                    {
                                        "id": "algo_bootstrap_task_preview_zone_1",
                                        "name": "zone_1",
                                        "type": "polygon",
                                        "color": "#3b82f6",
                                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                                        "enabled": True,
                                    }
                                ],
                            }
                        ],
                    },
                )
            )
            session.commit()

        service = ChannelService()
        service.channels_dir = self.channels_dir

        with (
            patch.object(
                service,
                "_bootstrap_running_task_preview",
                AsyncMock(side_effect=AssertionError("get_zone_preview_status should not bootstrap unprepared preview")),
            ),
            patch("src.services.channel_service.zone_preview_service.status", return_value={
                "status": "running",
                "enabled": True,
                "stream": "bootstrap_task_preview_zone",
                "zone_count": 1,
                "zone_source": "task",
                "owner_task_id": "task_bootstrap_task_preview",
            }),
        ):
            result = await service.get_zone_preview_status(channel_id)

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["zone_source"], "task")
        self.assertEqual(result["stream"], "bootstrap_task_preview_zone")

    async def test_get_zone_preview_status_skips_bootstrap_for_cached_zone_preview(self):
        channel_id = "ch_cached_zone"
        channel_payload = self._build_cached_zone_preview_channel_payload(channel_id)
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        service = ChannelService()
        service.channels_dir = self.channels_dir

        with patch.object(
            service,
            "_bootstrap_running_task_preview",
            AsyncMock(side_effect=AssertionError("get_zone_preview_status should not bootstrap cached preview")),
        ):
            result = await service.get_zone_preview_status(channel_id)

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["resolved_mode"], "zone")
        self.assertEqual(result["owner_task_id"], "task_cached_zone")

    async def test_get_zone_preview_browser_play_url_bootstraps_running_task_overlay_preview(self):
        channel_id = "ch_bootstrap_browser_play_url"
        channel_payload = {
            "id": channel_id,
            "name": "bootstrap-browser-play-url",
            "type": "rtsp",
            "source_url": "rtsp://example/live/bootstrap-browser-play-url",
            "app": "live",
            "stream": "bootstrap_browser_play_url",
            "is_online": True,
            "is_playing": False,
            "created_at": "2026-07-01 10:05:00",
            "updated_at": "2026-07-01 10:05:00",
            "ext": {},
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_bootstrap_browser_play_url",
                    name="bootstrap-browser-play-url-task",
                    source_url="rtsp://example/live/bootstrap-browser-play-url",
                    source_type="rtsp",
                    channel_id=channel_id,
                    frame_interval=10,
                    status="running",
                    algorithm_count=1,
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
                        "id": "task_bootstrap_browser_play_url",
                        "channel_id": channel_id,
                        "status": "running",
                        "runtime": {
                            "status": "running",
                            "is_running": True,
                        },
                        "algorithms": [
                            {
                                "id": "algo_bootstrap_browser_play_url",
                                "model_id": "model_person",
                                "enabled": True,
                                "need_overlay": True,
                                "regions": [
                                    {
                                        "id": "algo_bootstrap_browser_play_url_zone_1",
                                        "name": "zone_1",
                                        "type": "polygon",
                                        "color": "#3b82f6",
                                        "coordinates": [[0, 0], [120, 0], [120, 120], [0, 120]],
                                        "enabled": True,
                                    }
                                ],
                            }
                        ],
                    },
                )
            )
            session.commit()

        service = ChannelService()
        service.channels_dir = self.channels_dir

        expected_play_url = {
            "webrtc_api": "http://192.168.60.61:3000/proxy/sms/index/api/webrtc?app=live&stream=bootstrap_browser_play_url&type=play",
            "hls": "http://192.168.60.61:3000/proxy/sms/live/bootstrap_browser_play_url/hls.fmp4.m3u8",
            "httpflv": "http://192.168.60.61:3000/proxy/sms/live/bootstrap_browser_play_url.live.flv",
            "flv": "http://192.168.60.61:3000/proxy/sms/live/bootstrap_browser_play_url.live.flv",
        }

        with (
            patch.object(
                service,
                "_bootstrap_running_task_preview",
                AsyncMock(side_effect=AssertionError("get_zone_preview_browser_play_url should not bootstrap unprepared preview")),
            ),
        ):
            result = await service.get_zone_preview_browser_play_url(channel_id)

        self.assertEqual(result["resolved_mode"], "unknown")
        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["play_url"], expected_play_url)

    async def test_get_zone_preview_browser_play_url_skips_bootstrap_for_cached_zone_preview(self):
        channel_id = "ch_cached_zone"
        channel_payload = self._build_cached_zone_preview_channel_payload(channel_id)
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        service = ChannelService()
        service.channels_dir = self.channels_dir

        with patch.object(
            service,
            "_bootstrap_running_task_preview",
            AsyncMock(side_effect=AssertionError("get_zone_preview_browser_play_url should not bootstrap cached preview")),
        ):
            result = await service.get_zone_preview_browser_play_url(channel_id)

        self.assertEqual(result["resolved_mode"], "zone")
        self.assertEqual(result["source"], "zone")
        self.assertEqual(
            result["play_url"],
            {
                "webrtc_api": channel_payload["webrtc_api"],
                "hls": channel_payload["hls"],
                "httpflv": "http://192.168.60.61:3000/proxy/sms/live/cached_zone_preview_zone.live.flv",
                "flv": "http://192.168.60.61:3000/proxy/sms/live/cached_zone_preview_zone.live.flv",
            },
        )

    async def test_get_zone_preview_browser_play_url_falls_back_when_cached_browser_stream_not_ready(self):
        channel_id = "ch_cached_browser_not_ready"
        channel_payload = {
            "id": channel_id,
            "name": "cached-browser-not-ready",
            "type": "rtsp",
            "source_url": "rtsp://example/live/cached-browser-not-ready",
            "app": "rtp",
            "stream": "34020000001110000001_34020000001320000003",
            "is_online": True,
            "is_playing": True,
            "webrtc_api": "http://192.168.60.61:3000/proxy/sms/index/api/webrtc?app=rtp&stream=34020000001110000001_34020000001320000003_browser&type=play",
            "hls": "http://192.168.60.61:3000/proxy/sms/rtp/34020000001110000001_34020000001320000003_browser/hls.fmp4.m3u8",
            "created_at": "2026-07-01 10:10:00",
            "updated_at": "2026-07-01 10:10:00",
            "ext": {
                "task_preview": {
                    "prepared": True,
                    "mode": "channel",
                    "owner_task_id": "task_cached_browser_not_ready",
                    "status": "running",
                    "play_url": {
                        "webrtc_api": "http://192.168.60.61:3000/proxy/sms/index/api/webrtc?app=rtp&stream=34020000001110000001_34020000001320000003_browser&type=play",
                        "hls": "http://192.168.60.61:3000/proxy/sms/rtp/34020000001110000001_34020000001320000003_browser/hls.fmp4.m3u8",
                        "httpflv": "http://192.168.60.61:3000/proxy/sms/rtp/34020000001110000001_34020000001320000003_browser.live.flv",
                        "flv": "http://192.168.60.61:3000/proxy/sms/rtp/34020000001110000001_34020000001320000003_browser.live.flv",
                    },
                },
                "browser_transcode": {
                    "enabled": True,
                    "app": "rtp",
                    "stream": "34020000001110000001_34020000001320000003_browser",
                    "inspect_rtmp": {"data": []},
                    "error": "Browser transcode stream not published to media server",
                },
            },
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        service = ChannelService()
        service.channels_dir = self.channels_dir

        result = await service.get_zone_preview_browser_play_url(channel_id)

        self.assertEqual(result["resolved_mode"], "channel")
        self.assertEqual(result["source"], "channel")
        self.assertEqual(result["status"], "running")
        self.assertEqual(
            result["play_url"],
            {
                "webrtc_api": "http://192.168.60.61:3000/proxy/sms/index/api/webrtc?app=rtp&stream=34020000001110000001_34020000001320000003&type=play",
                "hls": "http://192.168.60.61:3000/proxy/sms/rtp/34020000001110000001_34020000001320000003/hls.fmp4.m3u8",
                "httpflv": "http://192.168.60.61:3000/proxy/sms/rtp/34020000001110000001_34020000001320000003.live.flv",
                "flv": "http://192.168.60.61:3000/proxy/sms/rtp/34020000001110000001_34020000001320000003.live.flv",
            },
        )

    async def test_snapshot_persists_snapshot_url_and_snapshot_path(self):
        channel_id = "ch_snapshot_path"
        channel_payload = {
            "id": channel_id,
            "name": "snapshot-path",
            "type": "rtsp",
            "source_url": "rtsp://example/live/snapshot-path",
            "app": "live",
            "stream": "snapshot_path",
            "created_at": "2026-06-16 10:00:00",
            "updated_at": "2026-06-16 10:00:00",
            "ext": {},
        }
        channel_path = self.channels_dir / f"{channel_id}.json"
        channel_path.write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        service = ChannelService()
        service.channels_dir = self.channels_dir
        service.snapshots_dir = self.snapshots_dir

        class FakeCapture:
            def isOpened(self):
                return True

            def read(self):
                return True, object()

            def release(self):
                return None

        with (
            patch("src.services.channel_service.cv2.VideoCapture", return_value=FakeCapture()),
            patch("src.services.channel_service.cv2.imwrite", return_value=True),
        ):
            result = await service.snapshot(channel_id)

        saved_channel = json.loads(channel_path.read_text(encoding="utf-8"))
        snapshot_url = saved_channel.get("snapshot_url")
        snapshot_path = saved_channel.get("snapshot_path")

        self.assertEqual(result["snapshot_url"], snapshot_url)
        self.assertEqual(result["snapshot_path"], snapshot_path)
        self.assertTrue(str(snapshot_path).startswith(str(self.snapshots_dir.resolve())))
        self.assertTrue(str(snapshot_url).startswith("/static/snapshots/"))

    async def test_create_channel_schedules_auto_snapshot(self):
        request = type(
            "Req",
            (),
            {
                "device_id": None,
                "name": "auto-snapshot",
                "type": "rtsp",
                "source_url": "rtsp://example/live/auto",
                "app": None,
                "stream": None,
                "media_node_id": None,
                "record_mode": "none",
            },
        )()

        service = ChannelService()
        service.channels_dir = self.channels_dir
        service.snapshots_dir = self.snapshots_dir

        with (
            patch("src.services.channel_service.generate_id", return_value="ch_auto_snapshot"),
            patch.object(service, "schedule_channel_snapshot") as mocked_schedule_snapshot,
            patch.object(service, "play", AsyncMock(return_value={"ok": True})),
        ):
            result = await service.create_channel(request)

        self.assertEqual(result["id"], "ch_auto_snapshot")
        mocked_schedule_snapshot.assert_called_once_with("ch_auto_snapshot")

    async def test_create_channel_returns_metadata_view(self):
        request = type(
            "Req",
            (),
            {
                "device_id": "dev-create",
                "name": "create-db-view",
                "type": "rtsp",
                "source_url": "rtsp://example/live/create-db-view",
                "app": None,
                "stream": None,
                "media_node_id": None,
                "record_mode": "none",
            },
        )()

        service = ChannelService()
        service.channels_dir = self.channels_dir
        service.snapshots_dir = self.snapshots_dir

        with (
            patch("src.services.channel_service.generate_id", return_value="ch_create_db_view"),
            patch.object(service, "schedule_channel_snapshot"),
            patch.object(service, "play", AsyncMock(return_value={"ok": True})),
        ):
            result = await service.create_channel(request)

        self.assertEqual(result["id"], "ch_create_db_view")
        self.assertEqual(result["name"], "create-db-view")
        self.assertEqual(result["device_id"], "dev-create")

        with self.session_local() as session:
            row = session.execute(select(ChannelMeta).where(ChannelMeta.id == "ch_create_db_view")).scalar_one()
        self.assertEqual(row.name, "create-db-view")
        self.assertEqual(row.device_id, "dev-create")

    async def test_update_channel_returns_metadata_view_after_runtime_write(self):
        channel_id = "ch_update_db_first"
        persist_json(
            self.channels_dir / f"{channel_id}.json",
            {
                "id": channel_id,
                "device_id": "dev-1",
                "name": "old-name",
                "type": "rtsp",
                "source_url": "rtsp://example/live/update",
                "app": "live",
                "stream": "update_channel",
                "media_node_id": None,
                "is_online": False,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "snapshot_url": None,
                "snapshot_path": None,
                "webrtc_api": None,
                "hls": None,
                "created_at": "2026-06-16 13:00:00",
                "updated_at": "2026-06-16 13:00:00",
                "ext": {},
            },
        )

        service = ChannelService()

        request = type(
            "Req",
            (),
            {
                "device_id": "dev-2",
                "name": "new-name",
                "type": None,
                "source_url": None,
                "app": None,
                "stream": None,
                "media_node_id": None,
                "record_mode": "manual",
            },
        )()

        result = await service.update_channel(channel_id, request)

        self.assertEqual(result["name"], "new-name")
        self.assertEqual(result["device_id"], "dev-2")
        self.assertEqual(result["record_mode"], "manual")

        with self.session_local() as session:
            row = session.execute(select(ChannelMeta).where(ChannelMeta.id == channel_id)).scalar_one()
        self.assertEqual(row.name, "new-name")
        self.assertEqual(row.device_id, "dev-2")
        self.assertEqual(row.record_mode, "manual")

    async def test_update_channel_rehydrates_runtime_json_when_only_metadata_exists(self):
        channel_id = "ch_update_from_metadata_only"
        with self.session_local() as session:
            session.merge(
                ChannelMeta(
                    id=channel_id,
                    device_id="dev-origin",
                    media_node_id=None,
                    name="metadata-only-channel",
                    type="rtsp",
                    source_url="rtsp://example/live/metadata-only",
                    app="live",
                    stream="metadata_only",
                    is_online=False,
                    is_playing=False,
                    record_mode="none",
                    snapshot_url=None,
                    snapshot_path=None,
                    webrtc_api=None,
                    hls=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "id": channel_id,
                        "device_id": "dev-origin",
                        "name": "metadata-only-channel",
                        "type": "rtsp",
                        "source_url": "rtsp://example/live/metadata-only",
                        "app": "live",
                        "stream": "metadata_only",
                        "is_online": False,
                        "is_playing": False,
                        "ai_enabled": False,
                        "record_mode": "none",
                        "ext": {},
                    },
                )
            )
            session.commit()

        service = ChannelService()
        request = type(
            "Req",
            (),
            {
                "device_id": None,
                "name": "metadata-updated-name",
                "type": None,
                "source_url": None,
                "app": None,
                "stream": None,
                "media_node_id": None,
                "record_mode": None,
            },
        )()

        result = await service.update_channel(channel_id, request)

        self.assertEqual(result["id"], channel_id)
        self.assertEqual(result["name"], "metadata-updated-name")
        self.assertTrue((self.channels_dir / f"{channel_id}.json").exists())

        with self.session_local() as session:
            row = session.execute(select(ChannelMeta).where(ChannelMeta.id == channel_id)).scalar_one()
        self.assertEqual(row.name, "metadata-updated-name")

    async def test_delete_channel_returns_metadata_view_before_removal(self):
        channel_id = "ch_delete_db_first"
        persist_json(
            self.channels_dir / f"{channel_id}.json",
            {
                "id": channel_id,
                "name": "delete-channel",
                "type": "rtsp",
                "source_url": "rtsp://example/live/delete-db-first",
                "app": "live",
                "stream": "delete_db_first",
                "is_online": False,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "created_at": "2026-06-16 13:05:00",
                "updated_at": "2026-06-16 13:05:00",
                "ext": {},
            },
        )

        service = ChannelService()
        result = await service.delete_channel(channel_id)

        self.assertEqual(result["id"], channel_id)
        self.assertEqual(result["name"], "delete-channel")

        with self.session_local() as session:
            row = session.execute(select(ChannelMeta).where(ChannelMeta.id == channel_id)).scalar_one_or_none()
        self.assertIsNone(row)

    async def test_delete_channel_removes_metadata_bound_tasks_even_without_task_file(self):
        channel_id = "ch_delete_metadata_tasks"
        persist_json(
            self.channels_dir / f"{channel_id}.json",
            {
                "id": channel_id,
                "name": "delete-metadata-tasks",
                "type": "rtsp",
                "source_url": "rtsp://example/live/delete-metadata-tasks",
                "app": "live",
                "stream": "delete_metadata_tasks",
                "is_online": False,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "created_at": "2026-06-16 13:06:00",
                "updated_at": "2026-06-16 13:06:00",
                "ext": {},
            },
        )

        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id="task_metadata_only",
                    name="metadata-only-task",
                    source_url="rtsp://example/live/delete-metadata-tasks",
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
                        "id": "task_metadata_only",
                        "channel_id": channel_id,
                    },
                )
            )
            session.commit()

        service = ChannelService()
        result = await service.delete_channel(channel_id)

        self.assertEqual(result["id"], channel_id)
        with self.session_local() as session:
            task_row = session.execute(select(TaskMeta).where(TaskMeta.id == "task_metadata_only")).scalar_one_or_none()
        self.assertIsNone(task_row)

    async def test_create_channel_returns_metadata_view_when_auto_play_fails(self):
        request = type(
            "Req",
            (),
            {
                "device_id": "dev-create-error",
                "name": "create-db-view-error",
                "type": "rtsp",
                "source_url": "rtsp://example/live/create-db-view-error",
                "app": None,
                "stream": None,
                "media_node_id": None,
                "record_mode": "none",
            },
        )()

        service = ChannelService()
        service.channels_dir = self.channels_dir
        service.snapshots_dir = self.snapshots_dir

        with (
            patch("src.services.channel_service.generate_id", return_value="ch_create_db_view_error"),
            patch.object(service, "schedule_channel_snapshot"),
            patch.object(service, "play", AsyncMock(side_effect=ValueError("autoplay failed"))),
        ):
            result = await service.create_channel(request)

        self.assertEqual(result["id"], "ch_create_db_view_error")
        self.assertEqual(result["device_id"], "dev-create-error")
        self.assertEqual(result["name"], "create-db-view-error")
        self.assertEqual(result.get("ext", {}).get("auto_play_error"), "autoplay failed")

        with self.session_local() as session:
            row = session.execute(select(ChannelMeta).where(ChannelMeta.id == "ch_create_db_view_error")).scalar_one()
        self.assertEqual(row.name, "create-db-view-error")
        self.assertEqual(row.device_id, "dev-create-error")

    async def test_ensure_channel_snapshot_skips_when_snapshot_file_exists(self):
        channel_id = "ch_existing_snapshot"
        existing_snapshot = self.snapshots_dir / "existing.jpg"
        existing_snapshot.write_bytes(b"fake-image")
        channel_payload = {
            "id": channel_id,
            "name": "existing-snapshot",
            "type": "rtsp",
            "source_url": "rtsp://example/live/existing",
            "app": "live",
            "stream": "existing_snapshot",
            "snapshot_url": "/static/snapshots/existing.jpg",
            "snapshot_path": str(existing_snapshot.resolve()),
            "created_at": "2026-06-16 10:00:00",
            "updated_at": "2026-06-16 10:00:00",
            "ext": {},
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        service = ChannelService()
        service.channels_dir = self.channels_dir
        service.snapshots_dir = self.snapshots_dir

        with patch("src.services.channel_service.cv2.VideoCapture", side_effect=AssertionError("should skip capture")):
            result = await service.ensure_channel_snapshot(channel_id)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "snapshot_exists")

    async def test_request_snapshot_refresh_returns_pending_without_waiting_capture(self):
        channel_id = "ch_request_snapshot_refresh"
        channel_payload = {
            "id": channel_id,
            "name": "request-snapshot-refresh",
            "type": "rtsp",
            "source_url": "rtsp://example/live/request-refresh",
            "app": "live",
            "stream": "request_refresh",
            "snapshot_url": "/static/snapshots/old.jpg",
            "snapshot_path": str((self.snapshots_dir / "old.jpg").resolve()),
            "created_at": "2026-06-16 10:00:00",
            "updated_at": "2026-06-16 10:00:00",
            "ext": {},
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)

        service = ChannelService()
        service.channels_dir = self.channels_dir
        service.snapshots_dir = self.snapshots_dir

        fake_task = object()
        with (
            patch.object(service, "schedule_channel_snapshot", return_value=fake_task) as mocked_schedule,
        ):
            result = await service.request_snapshot_refresh(channel_id)

        mocked_schedule.assert_called_once_with(channel_id, force=True)
        self.assertEqual(result["channel_id"], channel_id)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["snapshot_url"], "/static/snapshots/old.jpg")

    async def test_delete_channel_removes_bound_task_files(self):
        channel_id = "ch_delete_with_tasks"
        channel_payload = {
            "id": channel_id,
            "name": "delete-with-tasks",
            "type": "rtsp",
            "source_url": "rtsp://example/live/delete",
            "app": "live",
            "stream": "delete_with_tasks",
            "created_at": "2026-06-16 10:00:00",
            "updated_at": "2026-06-16 10:00:00",
            "ext": {},
        }
        task_payload = {
            "id": "task_bound_to_channel",
            "name": "bound-task",
            "channel_id": channel_id,
            "source_url": "rtsp://example/live/delete",
            "source_type": "rtsp",
            "frame_interval": 10,
            "status": "stopped",
            "runtime": {
                "status": "stopped",
                "is_running": False,
            },
            "algorithms": [],
            "regions": [],
            "created_at": "2026-06-16 10:00:00",
            "updated_at": "2026-06-16 10:00:00",
        }
        unrelated_task_payload = {
            **task_payload,
            "id": "task_other_channel",
            "channel_id": "ch_other",
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._insert_channel_meta(channel_payload)
        bound_task_path = self.tasks_dir / "task_bound_to_channel.json"
        bound_task_path.write_text(json.dumps(task_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        unrelated_task_path = self.tasks_dir / "task_other_channel.json"
        unrelated_task_path.write_text(json.dumps(unrelated_task_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        service = ChannelService()
        service.channels_dir = self.channels_dir

        result = await service.delete_channel(channel_id)

        self.assertEqual(result["id"], channel_id)
        self.assertFalse((self.channels_dir / f"{channel_id}.json").exists())
        self.assertFalse(bound_task_path.exists())
        self.assertTrue(unrelated_task_path.exists())


if __name__ == "__main__":
    unittest.main()
