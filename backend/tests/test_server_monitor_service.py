import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.server_monitor_service import ServerMonitorService


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


class ServerMonitorServiceTest(unittest.TestCase):
    def test_uses_host_agent_when_configured(self):
        host_payload = {
            "msg": "操作成功",
            "code": 200,
            "data": [
                {
                    "name": "智能分析服务器",
                    "online": True,
                    "os": {"name": "Windows 10", "version": "10.0", "arch": "amd64"},
                    "networkCards": [],
                    "cpu": {
                        "model": "host-cpu",
                        "quantity": 16,
                        "totalCores": 8,
                        "totalThreads": 16,
                        "usage": 5.5,
                    },
                    "gpu": {"name": "host-gpu", "memoryTotal": "8192 MB", "usage": 10},
                    "memory": {
                        "total": "32.00 GB",
                        "free": "20.00 GB",
                        "used": "12.00 GB",
                        "usage": 37.5,
                    },
                    "disk": {
                        "total": "1000.00 GB",
                        "free": "600.00 GB",
                        "used": "400.00 GB",
                        "usage": 40.0,
                    },
                }
            ],
        }

        service = ServerMonitorService(
            host_agent_url="http://host.docker.internal:8765/resources",
            host_agent_timeout_seconds=1,
        )

        with patch(
            "src.services.server_monitor_service.request.urlopen",
            return_value=_FakeHttpResponse(host_payload),
        ) as mocked_urlopen:
            result = service.get_server_resources()

        mocked_urlopen.assert_called_once()
        self.assertEqual(result["os"]["name"], "Windows 10")
        self.assertEqual(result["cpu"]["model"], "host-cpu")
        self.assertEqual(result["gpu"]["name"], "host-gpu")

    def test_uses_host_metrics_file_before_container_metrics(self):
        host_payload = {
            "msg": "操作成功",
            "code": 200,
            "data": [
                {
                    "name": "智能分析服务器",
                    "online": True,
                    "os": {"name": "Windows 10", "version": "10.0", "arch": "amd64"},
                    "networkCards": [],
                    "cpu": {
                        "model": "file-cpu",
                        "quantity": 16,
                        "totalCores": 8,
                        "totalThreads": 16,
                        "usage": 5.5,
                    },
                    "gpu": {"name": "file-gpu", "memoryTotal": "8192 MB", "usage": 10},
                    "memory": {
                        "total": "32.00 GB",
                        "free": "20.00 GB",
                        "used": "12.00 GB",
                        "usage": 37.5,
                    },
                    "disk": {
                        "total": "1000.00 GB",
                        "free": "600.00 GB",
                        "used": "400.00 GB",
                        "usage": 40.0,
                    },
                }
            ],
        }

        with TemporaryDirectory() as temp_dir:
            metrics_path = Path(temp_dir) / "host_monitor_resources.json"
            metrics_path.write_text(json.dumps(host_payload, ensure_ascii=False), encoding="utf-8")
            service = ServerMonitorService(
                host_agent_url="http://host.docker.internal:8765/resources",
                host_metrics_file=metrics_path,
                host_metrics_max_age_seconds=10,
            )

            with patch.object(service, "_get_host_agent_resources", side_effect=AssertionError("should not call agent")):
                result = service.get_server_resources()

        self.assertEqual(result["os"]["name"], "Windows 10")
        self.assertEqual(result["cpu"]["model"], "file-cpu")
        self.assertEqual(result["gpu"]["name"], "file-gpu")

    def test_stale_host_metrics_file_returns_offline_host(self):
        host_payload = {
            "msg": "操作成功",
            "code": 200,
            "data": [
                {
                    "name": "智能分析服务器",
                    "online": True,
                    "os": {"name": "Windows 10", "version": "10.0", "arch": "amd64"},
                    "networkCards": [],
                    "cpu": {
                        "model": "stale-cpu",
                        "quantity": 16,
                        "totalCores": 8,
                        "totalThreads": 16,
                        "usage": 5.5,
                    },
                    "gpu": {"name": "stale-gpu", "memoryTotal": "8192 MB", "usage": 10},
                    "memory": {"total": "32.00 GB", "free": "20.00 GB", "used": "12.00 GB", "usage": 37.5},
                    "disk": {"total": "1000.00 GB", "free": "600.00 GB", "used": "400.00 GB", "usage": 40.0},
                }
            ],
        }

        with TemporaryDirectory() as temp_dir:
            metrics_path = Path(temp_dir) / "host_monitor_resources.json"
            metrics_path.write_text(json.dumps(host_payload, ensure_ascii=False), encoding="utf-8")
            old_timestamp = 1000
            os.utime(metrics_path, (old_timestamp, old_timestamp))
            service = ServerMonitorService(
                host_agent_url="",
                host_metrics_file=metrics_path,
                host_metrics_max_age_seconds=1,
            )

            result = service.get_server_resources()

        self.assertFalse(result["online"])
        self.assertEqual(result["os"]["name"], "未知")
        self.assertEqual(result["cpu"]["model"], "未知CPU")


    def test_windows_cpu_model_prefers_processor_name(self):
        service = ServerMonitorService()
        fake_registry_result = type(
            "Result",
            (),
            {
                "returncode": 1,
                "stdout": "",
            },
        )()
        fake_wmi_result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": '[{"Name":"Intel(R) Core(TM) i7-12700"}]',
            },
        )()

        with (
            patch("src.services.server_monitor_service.platform.system", return_value="Windows"),
            patch("src.services.server_monitor_service.platform.processor", return_value="Intel64 Family 6 Model 151 Stepping 2, GenuineIntel"),
            patch("src.services.server_monitor_service.subprocess.run", side_effect=[fake_registry_result, fake_wmi_result]),
        ):
            result = service._get_cpu_model()

        self.assertEqual(result, "Intel Core i7-12700")

    def test_windows_cpu_model_prefers_registry_name(self):
        service = ServerMonitorService()
        fake_result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": (
                    "\nHKEY_LOCAL_MACHINE\\HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0\n"
                    "    ProcessorNameString    REG_SZ    12th Gen Intel(R) Core(TM) i7-12700KF\n"
                ),
            },
        )()

        with (
            patch("src.services.server_monitor_service.platform.system", return_value="Windows"),
            patch("src.services.server_monitor_service.subprocess.run", return_value=fake_result),
        ):
            result = service._get_cpu_model()

        self.assertEqual(result, "12th Gen Intel Core i7-12700KF")


if __name__ == "__main__":
    unittest.main()
