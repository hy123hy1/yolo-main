import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.routes.tasks import router, task_detection_websocket
from src.services.realtime_detection_service import RealtimeDetectionHub


class TaskRoutesTest(unittest.TestCase):
    def test_task_routes_only_expose_current_algorithm_region_flow(self):
        route_map = {
            (method, route.path)
            for route in router.routes
            if hasattr(route, "methods")
            for method in route.methods
        }

        kept_routes = {
            ("GET", "/tasks/{task_id}/algorithms/{algorithm_id}/regions"),
            ("PUT", "/tasks/{task_id}/algorithms/{algorithm_id}/regions"),
        }
        removed_routes = {
            ("GET", "/tasks/{task_id}/algorithms/{algorithm_id}/regions/{region_id}"),
            ("POST", "/tasks/{task_id}/algorithms/{algorithm_id}/regions"),
            ("PUT", "/tasks/{task_id}/algorithms/{algorithm_id}/regions/{region_id}"),
            ("DELETE", "/tasks/{task_id}/algorithms/{algorithm_id}/regions/{region_id}"),
            ("GET", "/tasks/{task_id}/regions"),
            ("POST", "/tasks/{task_id}/regions"),
            ("PUT", "/tasks/{task_id}/regions/{region_id}"),
            ("DELETE", "/tasks/{task_id}/regions/{region_id}"),
            ("POST", "/tasks/{task_id}/algorithms/{algorithm_id}/region-links"),
            ("DELETE", "/tasks/{task_id}/algorithms/{algorithm_id}/region-links/{region_id}"),
        }

        for route in kept_routes:
            self.assertIn(route, route_map)
        for route in removed_routes:
            self.assertNotIn(route, route_map)

    def test_detection_websocket_releases_idle_subscription_when_client_disconnects(self):
        class FakeWebSocket:
            def __init__(self):
                self.sent = []

            async def accept(self):
                return None

            async def send_json(self, payload):
                self.sent.append(payload)

            async def receive(self):
                return {"type": "websocket.disconnect"}

        async def run_test():
            hub = RealtimeDetectionHub()
            websocket = FakeWebSocket()
            try:
                with patch("src.api.routes.tasks.realtime_detection_hub", hub):
                    await asyncio.wait_for(
                        task_detection_websocket("task_idle", websocket),
                        timeout=0.2,
                    )
                self.assertEqual(websocket.sent, [{"type": "ready", "task_id": "task_idle"}])
                self.assertEqual(hub.subscriber_count("task_idle"), 0)
            finally:
                hub.close()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
