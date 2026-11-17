import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import host_monitor_agent


class HostMonitorAgentTest(unittest.TestCase):
    def test_windows_cpu_model_prefers_processor_name(self):
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
            patch.object(host_monitor_agent.platform, "system", return_value="Windows"),
            patch.object(host_monitor_agent.platform, "processor", return_value="Intel64 Family 6 Model 151 Stepping 2, GenuineIntel"),
            patch.object(host_monitor_agent.subprocess, "run", side_effect=[fake_registry_result, fake_wmi_result]),
        ):
            result = host_monitor_agent.get_cpu_model()

        self.assertEqual(result, "Intel Core i7-12700")

    def test_windows_cpu_model_prefers_registry_name(self):
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
            patch.object(host_monitor_agent.platform, "system", return_value="Windows"),
            patch.object(host_monitor_agent.subprocess, "run", return_value=fake_result),
        ):
            result = host_monitor_agent.get_cpu_model()

        self.assertEqual(result, "12th Gen Intel Core i7-12700KF")


if __name__ == "__main__":
    unittest.main()
