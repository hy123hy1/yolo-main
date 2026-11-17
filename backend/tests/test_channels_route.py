import sys
import unittest
from fastapi import BackgroundTasks
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.routes import channels as channels_route


class ChannelsRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_route_uses_quick_refresh_request(self):
        quick_result = {
            "channel_id": "ch_snapshot",
            "status": "pending",
            "snapshot_url": "/static/snapshots/old.jpg",
            "snapshot_path": "/app/data/snapshots/old.jpg",
        }

        with (
            patch.object(channels_route.channel_service, "request_snapshot_refresh", AsyncMock(return_value=quick_result)) as mocked_request,
            patch.object(channels_route.channel_service, "snapshot", AsyncMock(side_effect=AssertionError("should not call sync snapshot"))),
        ):
            result = await channels_route.snapshot_channel("ch_snapshot")

        mocked_request.assert_awaited_once_with("ch_snapshot")
        self.assertEqual(result, quick_result)

    async def test_sync_wvp_route_schedules_snapshot_for_synced_channels(self):
        sync_result = {
            "enabled": True,
            "synced": 2,
            "total": 2,
            "items": [
                {"id": "ch_sync_1"},
                {"id": "ch_sync_2"},
            ],
        }

        with (
            patch.object(channels_route.wvp_service, "sync_channels", return_value=sync_result),
            patch.object(channels_route.channel_service, "schedule_channel_snapshot") as mocked_schedule_snapshot,
        ):
            result = await channels_route.sync_wvp_channels(
                background_tasks=BackgroundTasks(),
                page=1,
                count=20,
                query=None,
            )

        self.assertEqual(result, sync_result)
        self.assertEqual(mocked_schedule_snapshot.call_count, 2)
        mocked_schedule_snapshot.assert_any_call("ch_sync_1")
        mocked_schedule_snapshot.assert_any_call("ch_sync_2")

    async def test_sync_wvp_route_queues_all_play_probes_outside_main_request(self):
        sync_result = {
            "enabled": True,
            "synced": 2,
            "total": 2,
            "items": [
                {
                    "id": "ch_video_ai",
                    "_sync_owner": "video_ai",
                    "ext": {"upstream": {"online": True}},
                },
                {
                    "id": "ch_electronic_seal",
                    "_sync_owner": "electronic_seal",
                    "ext": {"upstream": {"online": True}},
                },
            ],
            "_play_info_refresh_requests": [
                {
                    "channel_id": "ch_video_ai",
                    "sync_owner": "video_ai",
                    "upstream_channel_id": "upstream-video-ai",
                    "remote_item": {"gbId": "upstream-video-ai", "gbStatus": "ON"},
                },
                {
                    "channel_id": "ch_electronic_seal",
                    "sync_owner": "electronic_seal",
                    "upstream_channel_id": "upstream-electronic-seal",
                    "remote_item": {"gbId": "upstream-electronic-seal", "gbStatus": "ON"},
                },
                {
                    "channel_id": None,
                    "sync_owner": "unresolved",
                    "upstream_channel_id": "upstream-unresolved",
                    "remote_item": {"gbId": "upstream-unresolved", "gbStatus": "ON"},
                },
            ],
        }
        background_tasks = BackgroundTasks()

        with (
            patch.object(channels_route.wvp_service, "sync_channels", return_value=sync_result),
            patch.object(channels_route.channel_service, "schedule_channel_snapshot") as mocked_schedule_snapshot,
        ):
            result = await channels_route.sync_wvp_channels(
                background_tasks=background_tasks,
                page=1,
                count=20,
                query=None,
            )

        mocked_schedule_snapshot.assert_called_once_with("ch_video_ai")
        self.assertEqual(result["play_info_refresh"]["queued"], 3)
        self.assertEqual(len(background_tasks.tasks), 1)
        task = background_tasks.tasks[0]
        self.assertIs(
            task.func.__func__,
            channels_route.wvp_service.refresh_play_info_requests.__func__,
        )
        self.assertEqual(len(task.args[0]), 3)
        self.assertNotIn("_play_info_refresh_requests", result)
        self.assertNotIn("_sync_owner", result["items"][0])
        self.assertNotIn("_sync_owner", result["items"][1])


if __name__ == "__main__":
    unittest.main()
