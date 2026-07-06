import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.services.zone_preview_service import FFmpegCapture, ZonePreviewService


class _FakeThread:
    def __init__(self, *args, **kwargs):
        self._alive = False

    def start(self):
        self._alive = True

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self._alive = False


class _JoinRecordingThread(_FakeThread):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.join_calls: list[float | None] = []

    def join(self, timeout=None):
        self.join_calls.append(timeout)
        super().join(timeout=timeout)


class _ImmediateRunningService(ZonePreviewService):
    def status(self, channel_id: str):
        result = super().status(channel_id)
        if result:
            result["status"] = "running"
        return result


class _ChunkedStdout:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    def read(self, _size: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _ChunkedProcess:
    def __init__(self, chunks: list[bytes]):
        self.stdout = _ChunkedStdout(chunks)

    def poll(self):
        return None


class ZonePreviewServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.channels_dir = Path(self.temp_dir.name) / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)

        self.original_channels_dir = settings.CHANNELS_DIR
        settings.CHANNELS_DIR = self.channels_dir

    async def asyncTearDown(self):
        settings.CHANNELS_DIR = self.original_channels_dir
        self.temp_dir.cleanup()

    async def test_ffmpeg_capture_read_accumulates_partial_frame_bytes(self):
        capture = object.__new__(FFmpegCapture)
        capture.source_url = "rtsp://example/live/partial-frame"
        capture.width = 2
        capture.height = 1
        capture.frame_size = 6
        capture.process = _ChunkedProcess([b"\x00\x01", b"\x02\x03\x04\x05"])

        ok, frame = capture.read(retry=2)

        self.assertTrue(ok)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (1, 2, 3))
        self.assertEqual(frame.flatten().tolist(), [0, 1, 2, 3, 4, 5])

    async def test_start_returns_without_fixed_two_second_wait(self):
        channel_id = "ch_zone_preview_fast_start"
        channel_payload = {
            "id": channel_id,
            "name": "zone-preview-fast-start",
            "type": "rtsp",
            "source_url": "rtsp://example/live/fast-start",
            "app": "live",
            "stream": "fast_start",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-15 10:00:00",
            "updated_at": "2026-06-15 10:00:00",
            "ext": {},
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        service = _ImmediateRunningService()

        with (
            patch.object(service, "stop", AsyncMock(return_value=None)),
            patch("src.services.zone_preview_service.threading.Thread", _FakeThread),
            patch("src.services.metadata_db_sync.sync_json_document"),
        ):
            started_at = time.perf_counter()
            result = await service.start(
                channel_id=channel_id,
                source_url="rtsp://example/live/fast-start",
                stream="fast_start_zone",
                target="rtmp://zlm-server:1935/live/fast_start_zone",
                urls={"hls": "http://localhost/live/fast_start_zone/hls.fmp4.m3u8"},
                zones=[],
                zone_source="task",
                owner_task_id="task_fast_start",
            )
            elapsed = time.perf_counter() - started_at

        self.assertLess(elapsed, 0.5)
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["enabled"])
        self.assertEqual(result["stream"], "fast_start_zone")

    async def test_start_reuses_running_worker_when_task_and_zones_match(self):
        channel_id = "ch_zone_preview_reuse"
        zones = [
            {
                "id": "zone_1",
                "name": "zone_1",
                "coordinates": [[10, 10], [100, 10], [100, 100], [10, 100]],
            }
        ]
        channel_payload = {
            "id": channel_id,
            "name": "zone-preview-reuse",
            "type": "rtsp",
            "source_url": "rtsp://example/live/reuse",
            "app": "live",
            "stream": "reuse",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-15 10:00:00",
            "updated_at": "2026-06-15 10:00:00",
            "ext": {
                "zone_preview": {
                    "enabled": True,
                    "status": "running",
                    "stream": "reuse_zone",
                    "source": "rtsp://example/live/reuse",
                    "target": "rtmp://zlm-server:1935/live/reuse_zone",
                    "urls": {"hls": "http://localhost/live/reuse_zone/hls.fmp4.m3u8"},
                    "zones": zones,
                    "zone_count": 1,
                    "zone_source": "task",
                    "owner_task_id": "task_reuse",
                    "started_at": "2026-06-15 10:00:00",
                    "stopped_at": None,
                    "last_error": None,
                }
            },
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        service = ZonePreviewService()
        fake_thread = _FakeThread()
        fake_thread.start()
        service._workers[channel_id] = {
            "thread": fake_thread,
            "stop_event": None,
            "stream": "reuse_zone",
            "source": "rtsp://example/live/reuse",
            "target": "rtmp://zlm-server:1935/live/reuse_zone",
            "zones": zones,
            "zone_source": "task",
            "owner_task_id": "task_reuse",
        }

        with (
            patch.object(service, "stop", AsyncMock(return_value=None)) as mocked_stop,
            patch("src.services.zone_preview_service.threading.Thread") as mocked_thread,
            patch("src.services.zone_preview_service.logger.info") as mocked_logger_info,
        ):
            result = await service.start(
                channel_id=channel_id,
                source_url="rtsp://example/live/reuse",
                stream="reuse_zone",
                target="rtmp://zlm-server:1935/live/reuse_zone",
                urls={"hls": "http://localhost/live/reuse_zone/hls.fmp4.m3u8"},
                zones=zones,
                zone_source="task",
                owner_task_id="task_reuse",
            )

        mocked_stop.assert_not_awaited()
        mocked_thread.assert_not_called()
        mocked_logger_info.assert_any_call(
            "[ZonePreviewReuse] channel_id=%s reuse_hit=%s reason=%s",
            channel_id,
            True,
            "matched",
        )
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["enabled"])
        self.assertEqual(result["zone_count"], 1)

    async def test_start_reuse_skips_channel_persist_when_runtime_unchanged(self):
        channel_id = "ch_zone_preview_reuse_no_persist"
        zones = [
            {
                "id": "zone_1",
                "name": "zone_1",
                "coordinates": [[10, 10], [100, 10], [100, 100], [10, 100]],
            }
        ]
        channel_payload = {
            "id": channel_id,
            "name": "zone-preview-reuse-no-persist",
            "type": "rtsp",
            "source_url": "rtsp://example/live/reuse-no-persist",
            "app": "live",
            "stream": "reuse_no_persist",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-15 10:00:00",
            "updated_at": "2026-06-15 10:00:00",
            "ext": {
                "zone_preview": {
                    "enabled": True,
                    "status": "running",
                    "stream": "reuse_no_persist_zone",
                    "source": "rtsp://example/live/reuse-no-persist",
                    "target": "rtmp://zlm-server:1935/live/reuse_no_persist_zone",
                    "urls": {"hls": "http://localhost/live/reuse_no_persist_zone/hls.fmp4.m3u8"},
                    "zones": zones,
                    "zone_count": 1,
                    "zone_source": "task",
                    "owner_task_id": "task_reuse_no_persist",
                    "started_at": "2026-06-15 10:00:00",
                    "stopped_at": None,
                    "last_error": None,
                }
            },
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        service = ZonePreviewService()
        fake_thread = _FakeThread()
        fake_thread.start()
        service._workers[channel_id] = {
            "thread": fake_thread,
            "stop_event": None,
            "stream": "reuse_no_persist_zone",
            "source": "rtsp://example/live/reuse-no-persist",
            "target": "rtmp://zlm-server:1935/live/reuse_no_persist_zone",
            "zones": zones,
            "zone_source": "task",
            "owner_task_id": "task_reuse_no_persist",
        }

        with patch.object(service, "_save_channel") as mocked_save_channel:
            result = await service.start(
                channel_id=channel_id,
                source_url="rtsp://example/live/reuse-no-persist",
                stream="reuse_no_persist_zone",
                target="rtmp://zlm-server:1935/live/reuse_no_persist_zone",
                urls={"hls": "http://localhost/live/reuse_no_persist_zone/hls.fmp4.m3u8"},
                zones=zones,
                zone_source="task",
                owner_task_id="task_reuse_no_persist",
            )

        mocked_save_channel.assert_not_called()
        self.assertEqual(result["status"], "running")

    async def test_stop_uses_short_join_timeout(self):
        channel_id = "ch_zone_preview_stop_timeout"
        channel_payload = {
            "id": channel_id,
            "name": "zone-preview-stop-timeout",
            "type": "rtsp",
            "source_url": "rtsp://example/live/stop-timeout",
            "app": "live",
            "stream": "stop_timeout",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-15 10:00:00",
            "updated_at": "2026-06-15 10:00:00",
            "ext": {
                "zone_preview": {
                    "enabled": True,
                    "status": "running",
                }
            },
        }
        (self.channels_dir / f"{channel_id}.json").write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        service = ZonePreviewService()
        fake_thread = _JoinRecordingThread()
        fake_thread.start()
        service._workers[channel_id] = {
            "thread": fake_thread,
            "stop_event": type("StopEvent", (), {"set": lambda self: None})(),
        }

        with patch("src.services.metadata_db_sync.sync_json_document"):
            result = await service.stop(channel_id)

        self.assertIsNotNone(result)
        self.assertEqual(fake_thread.join_calls, [0.5])

    async def test_status_repairs_orphaned_runtime_only_once(self):
        channel_id = "ch_zone_preview_orphaned"
        channel_payload = {
            "id": channel_id,
            "name": "zone-preview-orphaned",
            "type": "rtsp",
            "source_url": "rtsp://example/live/orphaned",
            "app": "live",
            "stream": "orphaned",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-23 10:00:00",
            "updated_at": "2026-06-23 10:00:00",
            "ext": {
                "zone_preview": {
                    "enabled": True,
                    "status": "running",
                    "stream": "orphaned_zone",
                    "source": "rtsp://example/live/orphaned",
                    "target": "rtmp://zlm-server:1935/live/orphaned_zone",
                    "owner_task_id": "task_orphaned",
                    "started_at": "2026-06-23 10:00:00",
                    "stopped_at": None,
                    "last_error": None,
                }
            },
        }
        channel_path = self.channels_dir / f"{channel_id}.json"
        channel_path.write_text(
            json.dumps(channel_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        service = ZonePreviewService()

        with (
            patch("src.services.zone_preview_service.logger.warning") as mocked_warning,
            patch("src.services.metadata_db_sync.sync_json_document"),
        ):
            first = service.status(channel_id)
            second = service.status(channel_id)

        self.assertEqual(first["status"], "stopped")
        self.assertFalse(first["enabled"])
        self.assertEqual(second["status"], "stopped")

        orphaned_calls = [
            call
            for call in mocked_warning.call_args_list
            if call.args
            and call.args[0]
            == "[ZonePreviewOrphaned] channel_id=%s reason=runtime_running_but_worker_missing owner_task_id=%s stream=%s"
        ]
        self.assertEqual(len(orphaned_calls), 1)

        reloaded = json.loads(channel_path.read_text(encoding="utf-8"))
        runtime = ((reloaded.get("ext") or {}).get("zone_preview") or {})
        self.assertEqual(runtime.get("status"), "stopped")
        self.assertFalse(runtime.get("enabled"))
        self.assertIsNotNone(runtime.get("stopped_at"))


if __name__ == "__main__":
    unittest.main()
