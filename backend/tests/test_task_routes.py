import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.routes.tasks import router


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


if __name__ == "__main__":
    unittest.main()
