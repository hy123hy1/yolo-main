import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

import psutil


logger = logging.getLogger("uvicorn.error")


class ServerMonitorService:
    server_name = "智能分析服务器"

    def __init__(
        self,
        host_agent_url: str | None = None,
        host_agent_timeout_seconds: float | None = None,
        host_metrics_file: str | Path | None = None,
        host_metrics_max_age_seconds: float | None = None,
    ) -> None:
        self.host_agent_url = host_agent_url
        self.host_agent_timeout_seconds = host_agent_timeout_seconds
        self.host_metrics_file = Path(host_metrics_file) if host_metrics_file else None
        self.host_metrics_max_age_seconds = host_metrics_max_age_seconds

    def get_server_resources(self) -> dict[str, Any]:
        file_resources = self._get_host_metrics_file_resources()
        if file_resources:
            return file_resources

        host_resources = self._get_host_agent_resources()
        if host_resources:
            return host_resources

        if self.host_metrics_file or self.host_agent_url:
            return self._offline_host_resources()

        return {
            "name": self.server_name,
            "online": True,
            "os": self._get_os_info(),
            "networkCards": self._get_network_cards(),
            "cpu": self._get_cpu_info(),
            "gpu": self._get_gpu_info(),
            "memory": self._get_memory_info(),
            "disk": self._get_disk_info(),
        }

    def _get_host_metrics_file_resources(self) -> dict[str, Any] | None:
        if not self.host_metrics_file or not self.host_metrics_file.exists():
            return None

        try:
            if self._is_host_metrics_file_stale():
                logger.warning("[ServerMonitor] host metrics file is stale: %s", self.host_metrics_file)
                return None
            payload = json.loads(self.host_metrics_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[ServerMonitor] host metrics file unavailable: %s", exc)
            return None

        return self._extract_first_data_item(payload, source="host metrics file")

    def _is_host_metrics_file_stale(self) -> bool:
        if not self.host_metrics_file:
            return True
        max_age_seconds = self.host_metrics_max_age_seconds
        if max_age_seconds is None or max_age_seconds <= 0:
            return False
        return time.time() - self.host_metrics_file.stat().st_mtime > max_age_seconds

    def _get_host_agent_resources(self) -> dict[str, Any] | None:
        if not self.host_agent_url:
            return None

        try:
            with request.urlopen(
                self.host_agent_url,
                timeout=self.host_agent_timeout_seconds or 2,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.warning("[ServerMonitor] host agent unavailable: %s", exc)
            return None

        return self._extract_first_data_item(payload, source="host agent")

    def _extract_first_data_item(self, payload: Any, source: str) -> dict[str, Any] | None:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            logger.warning("[ServerMonitor] %s returned invalid payload", source)
            return None
        return data[0]

    def _get_os_info(self) -> dict[str, str]:
        system = platform.system() or "Unknown"
        release = platform.release() or ""
        version = release

        if system == "Windows":
            win_release, win_version, *_ = platform.win32_ver()
            release = win_release or release
            version = win_version or release
            name = f"Windows {release}".strip()
        else:
            name = system

        return {
            "name": name,
            "version": version,
            "arch": self._normalize_arch(platform.machine()),
        }

    def _get_network_cards(self) -> list[dict[str, str]]:
        cards: list[dict[str, str]] = []

        for name, addresses in psutil.net_if_addrs().items():
            mac = self._find_mac_address(addresses)
            if not mac:
                continue
            cards.append(
                {
                    "name": name,
                    "displayName": name,
                    "mac": mac,
                }
            )

        if cards:
            return cards

        fallback_mac = self._format_mac_from_int_uuid()
        if not fallback_mac:
            return []
        return [
            {
                "name": socket.gethostname() or "default",
                "displayName": socket.gethostname() or "default",
                "mac": fallback_mac,
            }
        ]

    def _get_cpu_info(self) -> dict[str, Any]:
        logical_count = psutil.cpu_count(logical=True) or os.cpu_count() or 0
        physical_count = psutil.cpu_count(logical=False) or logical_count

        return {
            "model": self._get_cpu_model(),
            "quantity": logical_count,
            "totalCores": physical_count,
            "totalThreads": logical_count,
            "usage": self._round_percent(psutil.cpu_percent(interval=0.1)),
        }

    def _get_gpu_info(self) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return self._empty_gpu_info()

        if result.returncode != 0:
            return self._empty_gpu_info()

        first_line = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
        parts = [part.strip() for part in first_line.split(",")]
        if len(parts) < 3:
            return self._empty_gpu_info()

        memory_total = parts[1]
        if memory_total and not memory_total.lower().endswith("mb"):
            memory_total = f"{memory_total} MB"

        try:
            usage = self._round_percent(float(parts[2]))
        except ValueError:
            usage = 0

        return {
            "name": parts[0] or "未检测到GPU",
            "memoryTotal": memory_total or "0 MB",
            "usage": usage,
        }

    def _get_memory_info(self) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        total = float(memory.total)
        free = float(memory.available)
        used = max(total - free, 0.0)

        return {
            "total": self._format_gb(total),
            "free": self._format_gb(free),
            "used": self._format_gb(used),
            "usage": self._round_percent((used / total * 100) if total else 0),
        }

    def _get_disk_info(self) -> dict[str, Any]:
        total = 0
        free = 0
        used = 0
        seen_devices: set[int] = set()

        for partition in psutil.disk_partitions(all=False):
            mountpoint = Path(partition.mountpoint)
            if not self._should_count_partition(partition, mountpoint):
                continue
            try:
                stat_device = mountpoint.stat().st_dev
                if stat_device in seen_devices:
                    continue
                usage = psutil.disk_usage(str(mountpoint))
            except (OSError, PermissionError):
                continue
            seen_devices.add(stat_device)
            total += usage.total
            free += usage.free
            used += usage.used

        if total == 0:
            usage = shutil.disk_usage(Path.cwd().anchor or "/")
            total = usage.total
            free = usage.free
            used = usage.used

        return {
            "total": self._format_gb(float(total)),
            "free": self._format_gb(float(free)),
            "used": self._format_gb(float(used)),
            "usage": self._round_percent((used / total * 100) if total else 0),
        }

    def _get_cpu_model(self) -> str:
        windows_model = self._get_windows_cpu_model()
        if windows_model:
            return windows_model

        processor = platform.processor()
        if processor:
            return processor

        cpuinfo_path = Path("/proc/cpuinfo")
        if cpuinfo_path.exists():
            try:
                for line in cpuinfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.lower().startswith("model name"):
                        _, value = line.split(":", 1)
                        return value.strip()
            except OSError:
                pass

        return "未知CPU"

    def _get_windows_cpu_model(self) -> str | None:
        if platform.system() != "Windows":
            return None

        registry_model = self._get_windows_cpu_model_from_registry()
        if registry_model:
            return registry_model

        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return None

        if result.returncode != 0:
            return None
        return self._extract_windows_cpu_model_from_stdout(result.stdout)

    def _get_windows_cpu_model_from_registry(self) -> str | None:
        command = [
            "reg",
            "query",
            r"HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            "/v",
            "ProcessorNameString",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return None

        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            if "ProcessorNameString" not in line:
                continue
            parts = line.split("REG_SZ", 1)
            value = parts[1].strip() if len(parts) > 1 else ""
            if value:
                return self._normalize_cpu_model_name(value)
        return None

    def _extract_windows_cpu_model_from_stdout(self, stdout: str) -> str | None:
        text = (stdout or "").strip()
        if not text:
            return None

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text.splitlines()[0].strip() or None

        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if isinstance(row, dict):
                value = str(row.get("Name") or "").strip()
            else:
                value = str(row or "").strip()
            if value:
                return self._normalize_cpu_model_name(value)
        return None

    def _normalize_cpu_model_name(self, value: str) -> str:
        normalized = (value or "").strip()
        for marker in ["(R)", "(TM)", "(C)"]:
            normalized = normalized.replace(marker, "")
        normalized = normalized.replace(" CPU ", " ")
        if " @" in normalized:
            normalized = normalized.split(" @", 1)[0]
        return " ".join(normalized.split())

    def _find_mac_address(self, addresses: list[Any]) -> str | None:
        for address in addresses:
            family = getattr(address, "family", None)
            if family not in (getattr(psutil, "AF_LINK", object()), getattr(socket, "AF_PACKET", object())):
                continue
            mac = self._normalize_mac(getattr(address, "address", ""))
            if mac:
                return mac
        return None

    def _should_count_partition(self, partition: Any, mountpoint: Path) -> bool:
        ignored_fstypes = {
            "proc",
            "sysfs",
            "devtmpfs",
            "devfs",
            "tmpfs",
            "cgroup",
            "cgroup2",
            "squashfs",
            "rpc_pipefs",
            "autofs",
        }
        if partition.fstype.lower() in ignored_fstypes:
            return False
        if "cdrom" in partition.opts.lower():
            return False
        return mountpoint.exists() and mountpoint.is_dir()

    def _normalize_arch(self, arch: str) -> str:
        normalized = (arch or "").lower()
        if normalized in {"amd64", "x86_64"}:
            return "amd64"
        if normalized in {"aarch64", "arm64"}:
            return "arm64"
        if normalized in {"i386", "i686", "x86"}:
            return "x86"
        return normalized or "unknown"

    def _normalize_mac(self, mac: str) -> str | None:
        value = (mac or "").strip()
        if not value:
            return None
        value = value.replace("-", ":").upper()
        if value in {"00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"}:
            return None
        parts = value.split(":")
        if len(parts) != 6 or any(len(part) != 2 for part in parts):
            return None
        return "-".join(parts)

    def _format_mac_from_int_uuid(self) -> str | None:
        import uuid

        node = uuid.getnode()
        if (node >> 40) % 2:
            return None
        return "-".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))

    def _empty_gpu_info(self) -> dict[str, Any]:
        return {"name": "未检测到GPU", "memoryTotal": "0 MB", "usage": 0}

    def _format_gb(self, bytes_value: float) -> str:
        return f"{bytes_value / 1024 / 1024 / 1024:.2f} GB"

    def _round_percent(self, value: float) -> float:
        return round(float(value), 2)

    def _offline_host_resources(self) -> dict[str, Any]:
        return {
            "name": self.server_name,
            "online": False,
            "os": {"name": "未知", "version": "", "arch": "unknown"},
            "networkCards": [],
            "cpu": {
                "model": "未知CPU",
                "quantity": 0,
                "totalCores": 0,
                "totalThreads": 0,
                "usage": 0,
            },
            "gpu": self._empty_gpu_info(),
            "memory": {
                "total": "0 GB",
                "free": "0 GB",
                "used": "0 GB",
                "usage": 0,
            },
            "disk": {
                "total": "0 GB",
                "free": "0 GB",
                "used": "0 GB",
                "usage": 0,
            },
        }


try:
    from src.core.settings import settings

    _host_agent_url = settings.HOST_MONITOR_AGENT_URL
    _host_agent_timeout = settings.HOST_MONITOR_AGENT_TIMEOUT_SECONDS
    _host_metrics_file = settings.resolved_host_monitor_metrics_file
    _host_metrics_max_age = settings.HOST_MONITOR_METRICS_MAX_AGE_SECONDS
except Exception:
    _host_agent_url = os.environ.get("HOST_MONITOR_AGENT_URL", "")
    _host_agent_timeout = float(os.environ.get("HOST_MONITOR_AGENT_TIMEOUT_SECONDS", "2"))
    _host_metrics_file = os.environ.get("HOST_MONITOR_METRICS_FILE", "")
    _host_metrics_max_age = float(os.environ.get("HOST_MONITOR_METRICS_MAX_AGE_SECONDS", "10"))


server_monitor_service = ServerMonitorService(
    host_agent_url=_host_agent_url,
    host_agent_timeout_seconds=_host_agent_timeout,
    host_metrics_file=_host_metrics_file,
    host_metrics_max_age_seconds=_host_metrics_max_age,
)
