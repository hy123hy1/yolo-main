import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import main


class StartupRestoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for item in (
            patch.object(main, "ensure_alarm_push_schema"),
            patch.object(main.alarm_push_worker, "start"),
            patch.object(main.analysis_service.external_video_capture, "recover_interrupted"),
            patch.object(main, "ensure_electronic_seal_schema"),
            patch.object(main, "ensure_camera_health_schema"),
        ):
            item.start()
            self.addCleanup(item.stop)

    async def test_startup_schedules_restore_in_background(self):
        async def slow_restore():
            await asyncio.sleep(1)

        created_tasks = []

        def fake_create_task(coro):
            created_tasks.append(coro)
            coro.close()
            return object()

        with (
            patch.object(main, "initialize_metadata_database", side_effect=lambda **_: time.sleep(0.2)),
            patch.object(main, "ensure_camera_health_schema"),
            patch.object(main.channel_service, "restore_channel_proxies", AsyncMock(side_effect=slow_restore)),
            patch.object(main.task_service, "restore_running_tasks", AsyncMock(side_effect=slow_restore)),
            patch.object(main.analysis_service, "restore_enabled_channels", AsyncMock(side_effect=slow_restore)),
            patch.object(main.camera_health_manager, "restore_enabled_channels", side_effect=slow_restore),
            patch("asyncio.create_task", side_effect=fake_create_task),
        ):
            await asyncio.wait_for(main.restore_ai_analysis_tasks(), timeout=0.05)

        self.assertEqual(len(created_tasks), 1)

    async def test_startup_waits_before_restoring_running_tasks(self):
        with (
            patch.object(main.settings, "STARTUP_TASK_RESTORE_INITIAL_DELAY_SECONDS", 2.0, create=True),
            patch.object(main, "initialize_metadata_database", side_effect=lambda **_: None),
            patch.object(main.channel_service, "restore_channel_proxies", AsyncMock(return_value={"restored": 0})),
            patch.object(main.task_service, "restore_running_tasks", AsyncMock(return_value={"restored": 0})),
            patch.object(main.analysis_service, "restore_enabled_channels", AsyncMock(return_value={"restored": 0})),
            patch.object(main.camera_health_manager, "restore_enabled_channels", return_value={"restored": 0}),
            patch("src.main.asyncio.sleep", AsyncMock()) as mocked_sleep,
        ):
            await main._restore_runtime_state_after_startup()

        mocked_sleep.assert_awaited_once_with(2.0)

    async def test_startup_initializes_metadata_without_syncing_files(self):
        with (
            patch.object(main.settings, "STARTUP_TASK_RESTORE_INITIAL_DELAY_SECONDS", 0.0, create=True),
            patch.object(main, "initialize_metadata_database") as mocked_initialize,
            patch.object(main, "ensure_camera_health_schema"),
            patch.object(main.channel_service, "restore_channel_proxies", AsyncMock(return_value={"restored": 0})),
            patch.object(main.task_service, "restore_running_tasks", AsyncMock(return_value={"restored": 0})),
            patch.object(main.analysis_service, "restore_enabled_channels", AsyncMock(return_value={"restored": 0})),
            patch.object(main.camera_health_manager, "restore_enabled_channels", return_value={"restored": 0}),
        ):
            await main._restore_runtime_state_after_startup()

        mocked_initialize.assert_called_once_with(sync_files=False)
        if main.settings.METADATA_DB_ENABLED:
            main.analysis_service.external_video_capture.recover_interrupted.assert_called_once_with()

    async def test_startup_restores_enabled_camera_health_channels(self):
        with (
            patch.object(main, "initialize_metadata_database", side_effect=lambda **_: None),
            patch.object(main, "ensure_camera_health_schema"),
            patch.object(main.channel_service, "restore_channel_proxies", AsyncMock(return_value={"restored": 0})),
            patch.object(main.task_service, "restore_running_tasks", AsyncMock(return_value={"restored": 0})),
            patch.object(main.analysis_service, "restore_enabled_channels", AsyncMock(return_value={"restored": 0})),
            patch.object(main.camera_health_manager, "restore_enabled_channels") as mocked_restore,
        ):
            await main._restore_runtime_state_after_startup()

        mocked_restore.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
