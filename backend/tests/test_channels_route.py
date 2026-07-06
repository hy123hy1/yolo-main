import sys
import unittest
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
            result = await channels_route.sync_wvp_channels(page=1, count=20, query=None)

        self.assertEqual(result, sync_result)
        self.assertEqual(mocked_schedule_snapshot.call_count, 2)
        mocked_schedule_snapshot.assert_any_call("ch_sync_1")
        mocked_schedule_snapshot.assert_any_call("ch_sync_2")


if __name__ == "__main__":
    unittest.main()
