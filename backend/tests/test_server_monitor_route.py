import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.routes import server_monitor


class ServerMonitorRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_server_resources_returns_expected_envelope(self):
        monitor_payload = {
            "name": "智能分析服务器",
            "online": True,
            "os": {"name": "Linux", "version": "1.0", "arch": "amd64"},
            "networkCards": [],
            "cpu": {
                "model": "test-cpu",
                "quantity": 8,
                "totalCores": 4,
                "totalThreads": 8,
                "usage": 12.34,
            },
            "gpu": {"name": "未检测到GPU", "memoryTotal": "0 MB", "usage": 0},
            "memory": {
                "total": "16.00 GB",
                "free": "8.00 GB",
                "used": "8.00 GB",
                "usage": 50.0,
            },
            "disk": {
                "total": "100.00 GB",
                "free": "60.00 GB",
                "used": "40.00 GB",
                "usage": 40.0,
            },
        }

        with patch.object(
            server_monitor.server_monitor_service,
            "get_server_resources",
            return_value=monitor_payload,
        ):
            result = await server_monitor.get_server_resources()

        self.assertEqual(result["msg"], "操作成功")
        self.assertEqual(result["code"], 200)
        self.assertEqual(result["data"], [monitor_payload])


if __name__ == "__main__":
    unittest.main()
