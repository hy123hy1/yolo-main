import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psutil


SERVER_NAME = "智能分析服务器2"


def format_gb(bytes_value: float) -> str:
    return f"{bytes_value / 1024 / 1024 / 1024:.2f} GB"


def round_percent(value: float) -> float:
    return round(float(value), 2)


def normalize_arch(arch: str) -> str:
    normalized = (arch or "").lower()
    if normalized in {"amd64", "x86_64"}:
        return "amd64"
    if normalized in {"aarch64", "arm64"}:
        return "arm64"
    if normalized in {"i386", "i686", "x86"}:
        return "x86"
    return normalized or "unknown"


def normalize_mac(mac: str) -> str | None:
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


def get_os_info() -> dict[str, str]:
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
        "arch": normalize_arch(platform.machine()),
    }


def get_network_cards() -> list[dict[str, str]]:
    display_names = get_windows_network_display_names() if platform.system() == "Windows" else {}
    cards: list[dict[str, str]] = []

    for name, addresses in psutil.net_if_addrs().items():
        mac = None
        for address in addresses:
            family = getattr(address, "family", None)
            if family not in (getattr(psutil, "AF_LINK", object()), getattr(socket, "AF_PACKET", object())):
                continue
            mac = normalize_mac(getattr(address, "address", ""))
            if mac:
                break
        if not mac:
            continue
        cards.append(
            {
                "name": name,
                "displayName": display_names.get(name, name),
                "mac": mac,
            }
        )

    return cards


def get_windows_network_display_names() -> dict[str, str]:
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        (
            "Get-NetAdapter | Select-Object Name,InterfaceDescription "
            "| ConvertTo-Json -Compress"
        ),
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
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    rows = payload if isinstance(payload, list) else [payload]
    return {
        str(row.get("Name")): str(row.get("InterfaceDescription"))
        for row in rows
        if isinstance(row, dict) and row.get("Name") and row.get("InterfaceDescription")
    }


def get_cpu_model() -> str:
    windows_model = get_windows_cpu_model()
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


def get_windows_cpu_model() -> str | None:
    if platform.system() != "Windows":
        return None

    registry_model = get_windows_cpu_model_from_registry()
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
    return extract_windows_cpu_model_from_stdout(result.stdout)


def get_windows_cpu_model_from_registry() -> str | None:
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
            return normalize_cpu_model_name(value)
    return None


def extract_windows_cpu_model_from_stdout(stdout: str) -> str | None:
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
            return normalize_cpu_model_name(value)
    return None


def normalize_cpu_model_name(value: str) -> str:
    normalized = (value or "").strip()
    for marker in ["(R)", "(TM)", "(C)"]:
        normalized = normalized.replace(marker, "")
    normalized = normalized.replace(" CPU ", " ")
    if " @" in normalized:
        normalized = normalized.split(" @", 1)[0]
    return " ".join(normalized.split())


def get_cpu_info() -> dict[str, Any]:
    logical_count = psutil.cpu_count(logical=True) or os.cpu_count() or 0
    physical_count = psutil.cpu_count(logical=False) or logical_count

    return {
        "model": get_cpu_model(),
        "quantity": logical_count,
        "totalCores": physical_count,
        "totalThreads": logical_count,
        "usage": round_percent(psutil.cpu_percent(interval=0.1)),
    }


def empty_gpu_info() -> dict[str, Any]:
    return {"name": "未检测到GPU", "memoryTotal": "0 MB", "usage": 0}


def get_gpu_info() -> dict[str, Any]:
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
        return empty_gpu_info()

    if result.returncode != 0:
        return empty_gpu_info()

    first_line = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
    parts = [part.strip() for part in first_line.split(",")]
    if len(parts) < 3:
        return empty_gpu_info()

    memory_total = parts[1]
    if memory_total and not memory_total.lower().endswith("mb"):
        memory_total = f"{memory_total} MB"

    try:
        usage = round_percent(float(parts[2]))
    except ValueError:
        usage = 0

    return {
        "name": parts[0] or "未检测到GPU",
        "memoryTotal": memory_total or "0 MB",
        "usage": usage,
    }


def get_memory_info() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    total = float(memory.total)
    free = float(memory.available)
    used = max(total - free, 0.0)

    return {
        "total": format_gb(total),
        "free": format_gb(free),
        "used": format_gb(used),
        "usage": round_percent((used / total * 100) if total else 0),
    }


def should_count_partition(partition: Any, mountpoint: Path) -> bool:
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


def get_disk_info() -> dict[str, Any]:
    total = 0
    free = 0
    used = 0
    seen_devices: set[int] = set()

    for partition in psutil.disk_partitions(all=False):
        mountpoint = Path(partition.mountpoint)
        if not should_count_partition(partition, mountpoint):
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
        "total": format_gb(float(total)),
        "free": format_gb(float(free)),
        "used": format_gb(float(used)),
        "usage": round_percent((used / total * 100) if total else 0),
    }


def get_server_resources() -> dict[str, Any]:
    return {
        "name": SERVER_NAME,
        "online": True,
        "os": get_os_info(),
        "networkCards": get_network_cards(),
        "cpu": get_cpu_info(),
        "gpu": get_gpu_info(),
        "memory": get_memory_info(),
        "disk": get_disk_info(),
    }


def make_response() -> dict[str, Any]:
    return {
        "msg": "操作成功",
        "code": 200,
        "data": [get_server_resources()],
    }


def write_metrics_snapshot_loop() -> None:
    metrics_file = os.environ.get(
        "HOST_MONITOR_METRICS_FILE",
        str(Path(__file__).resolve().parents[1] / "backend" / "data" / "host_monitor_resources.json"),
    )
    interval_seconds = float(os.environ.get("HOST_MONITOR_METRICS_INTERVAL_SECONDS", "2"))
    metrics_path = Path(metrics_file)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            temp_path = metrics_path.with_suffix(metrics_path.suffix + ".tmp")
            temp_path.write_text(json.dumps(make_response(), ensure_ascii=False), encoding="utf-8")
            temp_path.replace(metrics_path)
        except Exception as exc:
            print(f"[HostMonitorAgent] failed to write metrics file: {exc}")
        time.sleep(interval_seconds)


class HostMonitorHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json({"status": "ok"})
            return
        if path == "/resources":
            self._send_json(make_response())
            return
        self.send_error(404, "Not Found")

    def log_message(self, format: str, *args: Any) -> None:
        print("[HostMonitorAgent] " + format % args)

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = os.environ.get("HOST_MONITOR_AGENT_HOST", "0.0.0.0")
    port = int(os.environ.get("HOST_MONITOR_AGENT_PORT", "8765"))
    writer = threading.Thread(target=write_metrics_snapshot_loop, daemon=True)
    writer.start()
    server = ThreadingHTTPServer((host, port), HostMonitorHandler)
    print(f"[HostMonitorAgent] listening on http://{host}:{port}/resources")
    server.serve_forever()


if __name__ == "__main__":
    main()
