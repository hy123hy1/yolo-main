import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.db.models import Base, ChannelMeta, TaskMeta
from src.services import metadata_db_sync
from src.services import wvp_service as wvp_service_module
from src.services.platform_storage import save_json


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class WVPServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.channels_dir = Path(self.temp_dir.name) / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = Path(self.temp_dir.name) / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

        self.original_channels_dir = settings.CHANNELS_DIR
        self.original_tasks_dir = settings.TASKS_DIR
        self.original_wvp_enabled = getattr(settings, "WVP_ENABLED", False)
        self.original_wvp_api_base = getattr(settings, "WVP_API_BASE", "")
        self.original_wvp_channel_list_path = getattr(settings, "WVP_CHANNEL_LIST_PATH", "")
        self.original_wvp_channel_play_path = getattr(settings, "WVP_CHANNEL_PLAY_PATH", "")
        self.original_metadata_db_enabled = settings.METADATA_DB_ENABLED
        settings.CHANNELS_DIR = self.channels_dir
        settings.TASKS_DIR = self.tasks_dir
        settings.WVP_ENABLED = True
        settings.WVP_API_BASE = "http://127.0.0.1:18080"
        settings.WVP_CHANNEL_LIST_PATH = "/api/common/channel/list"
        settings.WVP_CHANNEL_PLAY_PATH = "/api/common/channel/play"
        settings.METADATA_DB_ENABLED = False

    def tearDown(self):
        settings.CHANNELS_DIR = self.original_channels_dir
        settings.TASKS_DIR = self.original_tasks_dir
        settings.WVP_ENABLED = self.original_wvp_enabled
        settings.WVP_API_BASE = self.original_wvp_api_base
        settings.WVP_CHANNEL_LIST_PATH = self.original_wvp_channel_list_path
        settings.WVP_CHANNEL_PLAY_PATH = self.original_wvp_channel_play_path
        settings.METADATA_DB_ENABLED = self.original_metadata_db_enabled
        self.temp_dir.cleanup()

    def test_remote_online_accepts_wvp_status_and_media_online(self):
        from src.services.wvp_service import WVPService

        service = WVPService()

        self.assertTrue(
            service._is_remote_online(
                {"gbStatus": "ON"},
                {"mediaInfo": {"online": True}},
            )
        )

    @patch("src.services.wvp_service.urlopen")
    def test_sync_channels_uses_upstream_id_and_does_not_fetch_play_info(self, mock_urlopen):
        from src.services.wvp_service import WVPService

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if "channel/list" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "total": 1,
                            "list": [
                                {
                                    "gbId": 1,
                                    "gbName": "Gate",
                                    "gbDeviceId": "34020000001320000002",
                                    "gbStatus": "ON",
                                }
                            ],
                        },
                    }
                )
            if "channel/play" in url:
                raise AssertionError("sync_channels should not fetch play info")
            raise AssertionError(f"Unexpected URL: {url}")

        mock_urlopen.side_effect = fake_urlopen

        service = WVPService()
        result = service.sync_channels(page=1, count=20)

        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["items"][0]["id"], "1")
        self.assertIsNone(result["items"][0]["source_url"])
        self.assertEqual(result["items"][0]["stream"], "")

        channel_file = self.channels_dir / "1.json"
        self.assertTrue(channel_file.exists())

        payload = json.loads(channel_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["id"], "1")
        self.assertEqual(payload["stream"], "")
        self.assertEqual(payload["name"], "Gate")
        self.assertEqual(payload["type"], "wvp")
        self.assertEqual(payload["ext"]["upstream"]["channel_db_id"], "1")
        self.assertEqual(payload["ext"]["upstream"]["play_info"], {})

    @patch("src.services.wvp_service.urlopen")
    def test_sync_channels_reuses_existing_channel_with_same_stream_id(self, mock_urlopen):
        from src.services.wvp_service import WVPService

        stream_id = "34020000001110000121_34020000001320000002"
        existing_payload = {
            "id": stream_id,
            "device_id": "34020000001320000002",
            "name": "Old Gate",
            "type": "wvp",
            "source_url": f"rtsp://192.168.1.36:554/rtp/{stream_id}",
            "app": "rtp",
            "stream": stream_id,
            "media_node_id": "default-node",
            "is_online": True,
            "is_playing": True,
            "ai_enabled": True,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-06-06T10:00:00",
            "updated_at": "2026-06-06T10:00:00",
            "ext": {
                "zones": [{"id": "zone_1", "name": "zone-1", "coordinates": [[0, 0], [1, 0], [1, 1]]}],
                "ai_config": {"enabled": True, "models": []},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "999",
                    "source_type": "rtsp",
                    "online": True,
                    "name": "Old Gate",
                },
            },
        }
        (self.channels_dir / f"{stream_id}.json").write_text(
            json.dumps(existing_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if "channel/list" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "total": 1,
                            "list": [
                                {
                                    "gbId": 1,
                                    "gbName": "Gate",
                                    "gbDeviceId": "34020000001320000002",
                                    "gbStatus": "ON",
                                }
                            ],
                        },
                    }
                )
            if "channel/play" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "app": "rtp",
                            "stream": stream_id,
                            "rtsp": f"rtsp://192.168.1.36:554/rtp/{stream_id}",
                            "mediaInfo": {
                                "online": True,
                            },
                        },
                    }
                )
            raise AssertionError(f"Unexpected URL: {url}")

        mock_urlopen.side_effect = fake_urlopen

        service = WVPService()
        result = service.sync_channels(page=1, count=20)

        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["items"][0]["id"], stream_id)

        channel_files = list(self.channels_dir.glob("*.json"))
        self.assertEqual(len(channel_files), 1)

        payload = json.loads(channel_files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["id"], stream_id)
        self.assertEqual(payload["media_node_id"], "default-node")
        self.assertTrue(payload["is_playing"])
        self.assertTrue(payload["ai_enabled"])
        self.assertEqual(payload["ext"]["zones"][0]["id"], "zone_1")
        self.assertEqual(payload["ext"]["upstream"]["channel_db_id"], "1")

    @patch("src.services.wvp_service.urlopen")
    def test_sync_channels_reuses_existing_channel_when_stream_id_is_null(self, mock_urlopen):
        from src.services.wvp_service import WVPService

        existing_channel_id = "existing_ball_cam"
        existing_payload = {
            "id": existing_channel_id,
            "device_id": "34020000001320009999",
            "name": "office-ball-2",
            "type": "wvp",
            "source_url": "rtsp://192.168.1.36:554/rtp/office_ball_cam_2",
            "app": "rtp",
            "stream": "",
            "media_node_id": "default-node",
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-07-03T10:00:00",
            "updated_at": "2026-07-03T10:00:00",
            "ext": {
                "zones": [],
                "ai_config": {"enabled": False, "models": []},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "2",
                    "source_type": "rtsp",
                    "online": True,
                    "name": "office-ball-2",
                },
            },
        }
        (self.channels_dir / f"{existing_channel_id}.json").write_text(
            json.dumps(existing_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if "channel/list" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "total": 1,
                            "list": [
                                {
                                    "gbId": 2,
                                    "gbName": "office-ball-2",
                                    "gbDeviceId": "34020000001320009999",
                                    "gbStatus": "ON",
                                }
                            ],
                        },
                    }
                )
            if "channel/play" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "app": "rtp",
                            "stream": None,
                            "rtsp": "rtsp://192.168.1.36:554/rtp/office_ball_cam_2",
                            "mediaInfo": {
                                "stream": None,
                                "online": True,
                            },
                        },
                    }
                )
            raise AssertionError(f"Unexpected URL: {url}")

        mock_urlopen.side_effect = fake_urlopen

        service = WVPService()
        result = service.sync_channels(page=1, count=20)

        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["items"][0]["id"], existing_channel_id)
        channel_files = sorted(path.name for path in self.channels_dir.glob("*.json"))
        self.assertEqual(channel_files, [f"{existing_channel_id}.json"])

    @patch("src.services.wvp_service.urlopen")
    def test_sync_channels_cleans_unbound_duplicate_with_same_upstream_channel(self, mock_urlopen):
        from src.services.wvp_service import WVPService

        old_random_id = "legacy_dining_1"
        stream_id = "34020000001110000001_34020000001320000021"

        old_payload = {
            "id": old_random_id,
            "device_id": "34020000001320000021",
            "name": "dining-1",
            "type": "wvp",
            "source_url": None,
            "app": "rtp",
            "stream": "",
            "media_node_id": None,
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-07-01T09:00:00",
            "updated_at": "2026-07-01T09:00:00",
            "ext": {
                "zones": [],
                "ai_config": {"enabled": False, "models": []},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "8192",
                    "source_type": "unknown",
                    "online": True,
                    "name": "dining-1",
                },
            },
        }
        stream_payload = {
            "id": stream_id,
            "device_id": "34020000001320000021",
            "name": "dining-1",
            "type": "wvp",
            "source_url": f"rtsp://192.168.60.160:554/rtp/{stream_id}",
            "app": "rtp",
            "stream": stream_id,
            "media_node_id": None,
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-07-02T09:00:00",
            "updated_at": "2026-07-02T09:00:00",
            "ext": {
                "zones": [],
                "ai_config": {"enabled": False, "models": []},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "8192",
                    "source_type": "rtsp",
                    "online": True,
                    "name": "dining-1",
                },
            },
        }
        (self.channels_dir / f"{old_random_id}.json").write_text(
            json.dumps(old_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.channels_dir / f"{stream_id}.json").write_text(
            json.dumps(stream_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if "channel/list" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "total": 1,
                            "list": [
                                {
                                    "gbId": 8192,
                                    "gbName": "dining-1",
                                    "gbDeviceId": "34020000001320000021",
                                    "gbStatus": "ON",
                                }
                            ],
                        },
                    }
                )
            if "channel/play" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "app": "rtp",
                            "stream": stream_id,
                            "rtsp": f"rtsp://192.168.60.160:554/rtp/{stream_id}",
                            "mediaInfo": {
                                "stream": stream_id,
                                "online": True,
                            },
                        },
                    }
                )
            raise AssertionError(f"Unexpected URL: {url}")

        mock_urlopen.side_effect = fake_urlopen

        service = WVPService()
        result = service.sync_channels(page=1, count=20)

        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["items"][0]["id"], stream_id)
        channel_files = sorted(path.name for path in self.channels_dir.glob("*.json"))
        self.assertEqual(channel_files, [f"{stream_id}.json"])

    @patch("src.services.wvp_service.urlopen")
    def test_sync_channels_prefers_task_bound_duplicate_when_same_upstream_channel_exists(self, mock_urlopen):
        from src.services.wvp_service import WVPService

        task_bound_id = "legacy_office_ball_2"
        stream_id = "34020000001110000001_34020000001320000022"

        task_bound_payload = {
            "id": task_bound_id,
            "device_id": "34020000001320000022",
            "name": "office-ball-2",
            "type": "wvp",
            "source_url": None,
            "app": "rtp",
            "stream": "",
            "media_node_id": None,
            "is_online": False,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-07-01T09:00:00",
            "updated_at": "2026-07-01T09:00:00",
            "ext": {
                "zones": [],
                "ai_config": {"enabled": False, "models": []},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "8191",
                    "source_type": "unknown",
                    "online": False,
                    "name": "office-ball-2",
                },
            },
        }
        duplicate_stream_payload = {
            "id": stream_id,
            "device_id": "34020000001320000022",
            "name": "office-ball-2",
            "type": "wvp",
            "source_url": f"rtsp://192.168.60.160:554/rtp/{stream_id}",
            "app": "rtp",
            "stream": stream_id,
            "media_node_id": None,
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-07-02T09:00:00",
            "updated_at": "2026-07-02T09:00:00",
            "ext": {
                "zones": [],
                "ai_config": {"enabled": False, "models": []},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "8191",
                    "source_type": "rtsp",
                    "online": True,
                    "name": "office-ball-2",
                },
            },
        }
        (self.channels_dir / f"{task_bound_id}.json").write_text(
            json.dumps(task_bound_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.channels_dir / f"{stream_id}.json").write_text(
            json.dumps(duplicate_stream_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.tasks_dir / "task_office_ball_2.json").write_text(
            json.dumps(
                {
                    "id": "task_office_ball_2",
                    "name": "office-ball-2-task",
                    "channel_id": task_bound_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if "channel/list" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "total": 1,
                            "list": [
                                {
                                    "gbId": 8191,
                                    "gbName": "office-ball-2",
                                    "gbDeviceId": "34020000001320000022",
                                    "gbStatus": "ON",
                                }
                            ],
                        },
                    }
                )
            if "channel/play" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "app": "rtp",
                            "stream": stream_id,
                            "rtsp": f"rtsp://192.168.60.160:554/rtp/{stream_id}",
                            "mediaInfo": {
                                "stream": stream_id,
                                "online": True,
                            },
                        },
                    }
                )
            raise AssertionError(f"Unexpected URL: {url}")

        mock_urlopen.side_effect = fake_urlopen

        service = WVPService()
        result = service.sync_channels(page=1, count=20)

        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["items"][0]["id"], task_bound_id)
        channel_files = sorted(path.name for path in self.channels_dir.glob("*.json"))
        self.assertEqual(channel_files, [f"{task_bound_id}.json"])
        payload = json.loads((self.channels_dir / f"{task_bound_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["stream"], stream_id)
        self.assertEqual(payload["source_url"], f"rtsp://192.168.60.160:554/rtp/{stream_id}")

    @patch("src.services.wvp_service.urlopen")
    def test_sync_channels_reuses_existing_channel_when_upstream_id_changes_but_device_matches(self, mock_urlopen):
        from src.services.wvp_service import WVPService

        existing_channel_id = "34020000001110000001_34020000001320000022"
        existing_payload = {
            "id": existing_channel_id,
            "device_id": "34020000001320000022",
            "name": "office-ball-2",
            "type": "wvp",
            "source_url": f"rtsp://192.168.60.160:554/rtp/{existing_channel_id}",
            "app": "rtp",
            "stream": existing_channel_id,
            "media_node_id": None,
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-07-01T09:00:00",
            "updated_at": "2026-07-02T09:00:00",
            "ext": {
                "zones": [],
                "ai_config": {"enabled": False, "models": []},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "7931",
                    "source_type": "rtsp",
                    "online": True,
                    "name": "office-ball-2",
                    "device_id": "34020000001320000022",
                    "play_info": {
                        "app": "rtp",
                        "stream": existing_channel_id,
                        "rtsp": f"rtsp://192.168.60.160:554/rtp/{existing_channel_id}",
                    },
                },
            },
        }
        (self.channels_dir / f"{existing_channel_id}.json").write_text(
            json.dumps(existing_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if "channel/list" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "total": 1,
                            "list": [
                                {
                                    "gbId": 8191,
                                    "gbName": "office-ball-2",
                                    "gbDeviceId": "34020000001320000022",
                                    "gbStatus": "OFF",
                                }
                            ],
                        },
                    }
                )
            if "channel/play" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {},
                    }
                )
            raise AssertionError(f"Unexpected URL: {url}")

        mock_urlopen.side_effect = fake_urlopen

        service = WVPService()
        result = service.sync_channels(page=1, count=20)

        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["items"][0]["id"], existing_channel_id)
        channel_files = sorted(path.name for path in self.channels_dir.glob("*.json"))
        self.assertEqual(channel_files, [f"{existing_channel_id}.json"])
        payload = json.loads((self.channels_dir / f"{existing_channel_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["ext"]["upstream"]["channel_db_id"], "8191")
        self.assertFalse(payload["is_online"])
        self.assertEqual(payload["stream"], "")
        self.assertIsNone(payload["source_url"])

    @patch("src.services.wvp_service.urlopen")
    def test_sync_channels_merges_historical_stream_into_task_bound_channel_when_upstream_id_changes(self, mock_urlopen):
        from src.services.wvp_service import WVPService

        task_bound_id = "legacy_office_ball_2"
        historical_stream_id = "34020000001110000001_34020000001320000022"

        task_bound_payload = {
            "id": task_bound_id,
            "device_id": "34020000001320000022",
            "name": "office-ball-2",
            "type": "wvp",
            "source_url": None,
            "app": "rtp",
            "stream": "",
            "media_node_id": None,
            "is_online": False,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-07-01T09:00:00",
            "updated_at": "2026-07-01T09:00:00",
            "ext": {
                "zones": [],
                "ai_config": {"enabled": False, "models": []},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "8191",
                    "source_type": "unknown",
                    "online": False,
                    "name": "office-ball-2",
                    "device_id": "34020000001320000022",
                },
            },
        }
        historical_payload = {
            "id": historical_stream_id,
            "device_id": "34020000001320000022",
            "name": "office-ball-2",
            "type": "wvp",
            "source_url": f"rtsp://192.168.60.160:554/rtp/{historical_stream_id}",
            "app": "rtp",
            "stream": historical_stream_id,
            "media_node_id": None,
            "is_online": True,
            "is_playing": False,
            "ai_enabled": False,
            "record_mode": "none",
            "snapshot_url": None,
            "created_at": "2026-07-02T09:00:00",
            "updated_at": "2026-07-02T09:00:00",
            "ext": {
                "zones": [],
                "ai_config": {"enabled": False, "models": []},
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": "7931",
                    "source_type": "rtsp",
                    "online": True,
                    "name": "office-ball-2",
                    "device_id": "34020000001320000022",
                    "play_info": {
                        "app": "rtp",
                        "stream": historical_stream_id,
                        "rtsp": f"rtsp://192.168.60.160:554/rtp/{historical_stream_id}",
                    },
                },
            },
        }
        (self.channels_dir / f"{task_bound_id}.json").write_text(
            json.dumps(task_bound_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.channels_dir / f"{historical_stream_id}.json").write_text(
            json.dumps(historical_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.tasks_dir / "task_office_ball_2.json").write_text(
            json.dumps(
                {
                    "id": "task_office_ball_2",
                    "name": "office-ball-2-task",
                    "channel_id": task_bound_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if "channel/list" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "total": 1,
                            "list": [
                                {
                                    "gbId": 8191,
                                    "gbName": "office-ball-2",
                                    "gbDeviceId": "34020000001320000022",
                                    "gbStatus": "OFF",
                                }
                            ],
                        },
                    }
                )
            if "channel/play" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {},
                    }
                )
            raise AssertionError(f"Unexpected URL: {url}")

        mock_urlopen.side_effect = fake_urlopen

        service = WVPService()
        result = service.sync_channels(page=1, count=20)

        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["items"][0]["id"], task_bound_id)
        channel_files = sorted(path.name for path in self.channels_dir.glob("*.json"))
        self.assertEqual(channel_files, [f"{task_bound_id}.json"])
        payload = json.loads((self.channels_dir / f"{task_bound_id}.json").read_text(encoding="utf-8"))
        self.assertFalse(payload["is_online"])
        self.assertEqual(payload["ext"]["upstream"]["channel_db_id"], "8191")
        self.assertEqual(payload["stream"], "")
        self.assertIsNone(payload["source_url"])


class WVPServiceMetadataDBTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.channels_dir = Path(self.temp_dir.name) / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = Path(self.temp_dir.name) / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path = Path(self.temp_dir.name) / "metadata.db"

        self.original_channels_dir = settings.CHANNELS_DIR
        self.original_tasks_dir = settings.TASKS_DIR
        self.original_wvp_enabled = getattr(settings, "WVP_ENABLED", False)
        self.original_wvp_api_base = getattr(settings, "WVP_API_BASE", "")
        self.original_wvp_channel_list_path = getattr(settings, "WVP_CHANNEL_LIST_PATH", "")
        self.original_wvp_channel_play_path = getattr(settings, "WVP_CHANNEL_PLAY_PATH", "")
        self.original_metadata_db_enabled = settings.METADATA_DB_ENABLED
        self.original_engine = metadata_db_sync.engine
        self.original_session_local = metadata_db_sync.SessionLocal
        self.original_wvp_service_session_local = wvp_service_module.SessionLocal

        settings.CHANNELS_DIR = self.channels_dir
        settings.TASKS_DIR = self.tasks_dir
        settings.WVP_ENABLED = True
        settings.WVP_API_BASE = "http://127.0.0.1:18080"
        settings.WVP_CHANNEL_LIST_PATH = "/api/common/channel/list"
        settings.WVP_CHANNEL_PLAY_PATH = "/api/common/channel/play"
        settings.METADATA_DB_ENABLED = True

        self.engine = create_engine(
            f"sqlite:///{self.sqlite_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.session_local = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        metadata_db_sync.engine = self.engine
        metadata_db_sync.SessionLocal = self.session_local
        wvp_service_module.SessionLocal = self.session_local
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self):
        settings.CHANNELS_DIR = self.original_channels_dir
        settings.TASKS_DIR = self.original_tasks_dir
        settings.WVP_ENABLED = self.original_wvp_enabled
        settings.WVP_API_BASE = self.original_wvp_api_base
        settings.WVP_CHANNEL_LIST_PATH = self.original_wvp_channel_list_path
        settings.WVP_CHANNEL_PLAY_PATH = self.original_wvp_channel_play_path
        settings.METADATA_DB_ENABLED = self.original_metadata_db_enabled
        self.engine.dispose()
        metadata_db_sync.engine = self.original_engine
        metadata_db_sync.SessionLocal = self.original_session_local
        wvp_service_module.SessionLocal = self.original_wvp_service_session_local
        self.temp_dir.cleanup()

    def _insert_task(self, task_id: str, channel_id: str, channel_name: str) -> None:
        task_payload = {
            "id": task_id,
            "name": f"task-{task_id}",
            "source_url": "",
            "source_type": "wvp",
            "channel_id": channel_id,
            "channel_name": channel_name,
            "frame_interval": 10,
            "status": "stopped",
            "algorithms": [],
            "regions": [],
            "runtime": {
                "status": "stopped",
                "is_running": False,
            },
            "created_at": "2026-07-03 10:00:00",
            "updated_at": "2026-07-03 10:00:00",
        }
        (self.tasks_dir / f"{task_id}.json").write_text(
            json.dumps(task_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with self.session_local() as session:
            session.merge(
                TaskMeta(
                    id=task_id,
                    name=task_payload["name"],
                    source_url=task_payload.get("source_url"),
                    source_type=task_payload.get("source_type"),
                    channel_id=channel_id,
                    channel_name=channel_name,
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
                    raw_json=task_payload,
                )
            )
            session.commit()

    @patch("src.services.wvp_service.urlopen")
    def test_sync_channels_rebinds_metadata_bound_duplicate_tasks_and_removes_duplicate_channel(self, mock_urlopen):
        from src.services.wvp_service import WVPService

        canonical_id = "34020000001110000001_34020000001320000022"
        duplicate_id = "0f095626"
        channel_name = "office-ball-2"
        device_id = "34020000001320000022"

        save_json(
            self.channels_dir / f"{canonical_id}.json",
            {
                "id": canonical_id,
                "device_id": device_id,
                "name": channel_name,
                "type": "wvp",
                "source_url": f"rtsp://192.168.60.160:554/rtp/{canonical_id}",
                "app": "rtp",
                "stream": canonical_id,
                "media_node_id": None,
                "is_online": True,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "snapshot_url": None,
                "snapshot_path": None,
                "created_at": "2026-07-03 09:00:00",
                "updated_at": "2026-07-03 09:00:00",
                "ext": {
                    "zones": [],
                    "ai_config": {"enabled": False, "models": []},
                    "upstream": {
                        "platform": "wvp",
                        "channel_db_id": "7931",
                        "source_type": "rtsp",
                        "online": True,
                        "name": channel_name,
                        "device_id": device_id,
                        "play_info": {
                            "app": "rtp",
                            "stream": canonical_id,
                            "rtsp": f"rtsp://192.168.60.160:554/rtp/{canonical_id}",
                        },
                    },
                },
            },
        )
        save_json(
            self.channels_dir / f"{duplicate_id}.json",
            {
                "id": duplicate_id,
                "device_id": device_id,
                "name": channel_name,
                "type": "wvp",
                "source_url": None,
                "app": "rtp",
                "stream": "",
                "media_node_id": None,
                "is_online": False,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": "none",
                "snapshot_url": None,
                "snapshot_path": None,
                "created_at": "2026-07-03 09:10:00",
                "updated_at": "2026-07-03 09:10:00",
                "ext": {
                    "zones": [],
                    "ai_config": {"enabled": False, "models": []},
                    "upstream": {
                        "platform": "wvp",
                        "channel_db_id": "8191",
                        "source_type": "unknown",
                        "online": False,
                        "name": channel_name,
                        "device_id": device_id,
                    },
                },
            },
        )
        self._insert_task("task_a", duplicate_id, channel_name)
        self._insert_task("task_b", canonical_id, channel_name)

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if "channel/list" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {
                            "total": 1,
                            "list": [
                                {
                                    "gbId": 8191,
                                    "gbName": channel_name,
                                    "gbDeviceId": device_id,
                                    "gbStatus": "OFF",
                                }
                            ],
                        },
                    }
                )
            if "channel/play" in url:
                return _FakeHTTPResponse(
                    {
                        "code": 0,
                        "msg": "success",
                        "data": {},
                    }
                )
            raise AssertionError(f"Unexpected URL: {url}")

        mock_urlopen.side_effect = fake_urlopen

        service = WVPService()
        result = service.sync_channels(page=1, count=20)

        self.assertEqual(result["synced"], 1)
        self.assertEqual(result["items"][0]["id"], canonical_id)
        self.assertFalse((self.channels_dir / f"{duplicate_id}.json").exists())

        with self.session_local() as session:
            channels = session.query(ChannelMeta).filter(ChannelMeta.device_id == device_id).all()
            tasks = session.query(TaskMeta).order_by(TaskMeta.id.asc()).all()

        self.assertEqual([row.id for row in channels], [canonical_id])
        self.assertEqual([row.channel_id for row in tasks], [canonical_id, canonical_id])

        task_payload = json.loads((self.tasks_dir / "task_a.json").read_text(encoding="utf-8"))
        self.assertEqual(task_payload["channel_id"], canonical_id)
