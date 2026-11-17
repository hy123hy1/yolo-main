# Server Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new top-level "服务器监控" page that shows the current host summary, real-time load cards, and time-window history charts for CPU, memory, GPU, disk, and network metrics.

**Architecture:** Add a dedicated backend `system-monitor` service that samples host metrics into an in-memory ring buffer and exposes summary/current/history APIs. Add a frontend page and API client that periodically refreshes the summary and charts, reusing the existing Vue + ECharts stack and integrating a new top-level route/navigation entry.

**Tech Stack:** FastAPI, Python services, `psutil`, optional `pynvml`, pytest, Vue 3, TypeScript, axios, ECharts, vue-echarts.

---

### Task 1: Add backend tests for system monitor sampling and APIs

**Files:**
- Create: `backend/tests/test_system_monitor_service.py`
- Modify: `backend/requirements-runtime.txt`
- Test: `backend/tests/test_system_monitor_service.py`

- [ ] **Step 1: Write the failing tests**

```python
from collections import deque

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import system_monitor
from src.services.system_monitor_service import SystemMonitorService


def test_history_window_filters_points():
    service = SystemMonitorService()
    service._history = deque(
        [
            {
                "sampled_at": "2026-06-15 12:00:00",
                "timestamp": 100.0,
                "cpu_percent": 10.0,
                "memory_percent": 20.0,
                "disk_percent": 30.0,
                "network_upload_bps": 1000.0,
                "network_download_bps": 2000.0,
                "gpu_util_percent": None,
                "gpu_memory_percent": None,
            },
            {
                "sampled_at": "2026-06-15 12:10:00",
                "timestamp": 700.0,
                "cpu_percent": 40.0,
                "memory_percent": 50.0,
                "disk_percent": 60.0,
                "network_upload_bps": 3000.0,
                "network_download_bps": 4000.0,
                "gpu_util_percent": None,
                "gpu_memory_percent": None,
            },
        ],
        maxlen=17280,
    )

    points = service.get_history("5m", now_ts=700.0)["points"]

    assert len(points) == 1
    assert points[0]["cpu_percent"] == 40.0


def test_current_returns_last_sample():
    service = SystemMonitorService()
    service._current_sample = {
        "sampled_at": "2026-06-15 13:20:00",
        "cpu_percent": 36.4,
        "memory_percent": 58.2,
        "disk_percent": 71.1,
        "network_upload_bps": 128000.0,
        "network_download_bps": 640000.0,
        "gpu": {
            "available": False,
            "util_percent": None,
            "memory_percent": None,
        },
    }

    current = service.get_current()

    assert current["cpu_percent"] == 36.4
    assert current["gpu"]["available"] is False


def test_summary_endpoint_returns_payload(monkeypatch):
    app = FastAPI()
    app.include_router(system_monitor.router)
    client = TestClient(app)

    fake_summary = {
        "hostname": "server-01",
        "status": "online",
        "os_name": "Linux",
        "os_version": "6.0",
        "boot_time": "2026-06-15 08:00:00",
        "uptime_seconds": 1800,
        "cpu": {"model": "Fake CPU", "physical_cores": 4, "logical_cores": 8},
        "gpu": {"available": False, "name": None, "count": 0, "memory_total_mb": None},
        "memory": {"total_bytes": 1024},
        "disk": {"total_bytes": 2048},
        "network": {"primary_interface": "eth0", "primary_ip": "192.168.1.61"},
    }

    monkeypatch.setattr(system_monitor.system_monitor_service, "get_summary", lambda: fake_summary)

    response = client.get("/system-monitor/summary")

    assert response.status_code == 200
    assert response.json()["hostname"] == "server-01"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_system_monitor_service.py -v`
Expected: FAIL with import errors because `src.api.routes.system_monitor` and `src.services.system_monitor_service` do not exist yet.

- [ ] **Step 3: Add runtime dependency placeholders**

```text
psutil>=6.0.0
pynvml>=12.0.0
```

Add them to `backend/requirements-runtime.txt` in the runtime dependency section so the new service can collect metrics.

- [ ] **Step 4: Run test to verify it still fails for missing implementation**

Run: `pytest backend/tests/test_system_monitor_service.py -v`
Expected: FAIL because the new backend files still do not exist, confirming the dependency change did not accidentally satisfy the feature.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_system_monitor_service.py backend/requirements-runtime.txt
git commit -m "test: add failing system monitor backend tests"
```

### Task 2: Implement backend system monitor service and routes

**Files:**
- Create: `backend/src/services/system_monitor_service.py`
- Create: `backend/src/api/routes/system_monitor.py`
- Modify: `backend/src/main.py`
- Test: `backend/tests/test_system_monitor_service.py`

- [ ] **Step 1: Write the minimal service implementation**

```python
from __future__ import annotations

import platform
import socket
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

import psutil

try:
    from pynvml import (
        nvmlDeviceGetCount,
        nvmlDeviceGetHandleByIndex,
        nvmlDeviceGetMemoryInfo,
        nvmlDeviceGetName,
        nvmlDeviceGetUtilizationRates,
        nvmlInit,
        nvmlShutdown,
    )
except Exception:  # pragma: no cover
    nvmlInit = None


WINDOW_SECONDS = {
    "5m": 300,
    "30m": 1800,
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
}


class SystemMonitorService:
    def __init__(self, sample_interval_seconds: int = 5, history_window_seconds: int = 86400) -> None:
        self.sample_interval_seconds = sample_interval_seconds
        self.history_window_seconds = history_window_seconds
        self._history = deque(maxlen=max(1, history_window_seconds // sample_interval_seconds))
        self._current_sample: dict[str, Any] | None = None
        self._net_last_bytes: tuple[int, int] | None = None
        self._net_last_ts: float | None = None
        self._summary_cache: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.sample_once()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="system-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.wait(self.sample_interval_seconds):
            self.sample_once()

    def sample_once(self) -> dict[str, Any]:
        now_ts = time.time()
        counters = psutil.net_io_counters()
        upload_bps = 0.0
        download_bps = 0.0
        if self._net_last_bytes is not None and self._net_last_ts is not None:
            elapsed = max(now_ts - self._net_last_ts, 1e-6)
            upload_bps = max((counters.bytes_sent - self._net_last_bytes[0]) / elapsed, 0.0)
            download_bps = max((counters.bytes_recv - self._net_last_bytes[1]) / elapsed, 0.0)
        self._net_last_bytes = (counters.bytes_sent, counters.bytes_recv)
        self._net_last_ts = now_ts

        gpu_current, gpu_history = self._read_gpu()

        sample = {
            "sampled_at": datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": now_ts,
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage("/").percent,
            "network_upload_bps": upload_bps,
            "network_download_bps": download_bps,
            "gpu_util_percent": gpu_history["gpu_util_percent"],
            "gpu_memory_percent": gpu_history["gpu_memory_percent"],
        }
        current = {
            "sampled_at": sample["sampled_at"],
            "cpu_percent": sample["cpu_percent"],
            "memory_percent": sample["memory_percent"],
            "disk_percent": sample["disk_percent"],
            "network_upload_bps": sample["network_upload_bps"],
            "network_download_bps": sample["network_download_bps"],
            "gpu": gpu_current,
        }
        with self._lock:
            self._history.append(sample)
            self._current_sample = current
        return current

    def get_summary(self) -> dict[str, Any]:
        if self._summary_cache is None:
            self._summary_cache = self._build_summary()
        return self._summary_cache

    def get_current(self) -> dict[str, Any]:
        if self._current_sample is None:
            return self.sample_once()
        return self._current_sample

    def get_history(self, window: str, now_ts: float | None = None) -> dict[str, Any]:
        if window not in WINDOW_SECONDS:
            raise ValueError(f"Unsupported window: {window}")
        cutoff = (now_ts or time.time()) - WINDOW_SECONDS[window]
        with self._lock:
            points = [
                {
                    "sampled_at": item["sampled_at"],
                    "cpu_percent": item["cpu_percent"],
                    "memory_percent": item["memory_percent"],
                    "disk_percent": item["disk_percent"],
                    "network_upload_bps": item["network_upload_bps"],
                    "network_download_bps": item["network_download_bps"],
                    "gpu_util_percent": item["gpu_util_percent"],
                    "gpu_memory_percent": item["gpu_memory_percent"],
                }
                for item in self._history
                if item["timestamp"] >= cutoff
            ]
        return {
            "window": window,
            "interval_seconds": self.sample_interval_seconds,
            "points": points,
        }
```

- [ ] **Step 2: Add the helper methods and API route**

```python
    def _build_summary(self) -> dict[str, Any]:
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        gpu_summary, _ = self._read_gpu()
        hostname = socket.gethostname()
        primary_interface, primary_ip = self._resolve_primary_network()
        return {
            "hostname": hostname,
            "status": "online",
            "os_name": platform.system(),
            "os_version": platform.version(),
            "boot_time": boot_time.strftime("%Y-%m-%d %H:%M:%S"),
            "uptime_seconds": int(time.time() - psutil.boot_time()),
            "cpu": {
                "model": platform.processor() or platform.machine(),
                "physical_cores": psutil.cpu_count(logical=False) or 0,
                "logical_cores": psutil.cpu_count(logical=True) or 0,
            },
            "gpu": {
                "available": gpu_summary["available"],
                "name": gpu_summary["name"],
                "count": gpu_summary["count"],
                "memory_total_mb": gpu_summary["memory_total_mb"],
            },
            "memory": {"total_bytes": memory.total},
            "disk": {"total_bytes": disk.total},
            "network": {
                "primary_interface": primary_interface,
                "primary_ip": primary_ip,
            },
        }

    def _resolve_primary_network(self) -> tuple[str | None, str | None]:
        interfaces = psutil.net_if_addrs()
        for name, addresses in interfaces.items():
            for address in addresses:
                if getattr(address, "family", None) == socket.AF_INET and not address.address.startswith("127."):
                    return name, address.address
        return None, None

    def _read_gpu(self) -> tuple[dict[str, Any], dict[str, Any]]:
        unavailable_current = {
            "available": False,
            "name": None,
            "count": 0,
            "memory_total_mb": None,
            "util_percent": None,
            "memory_percent": None,
        }
        unavailable_history = {
            "gpu_util_percent": None,
            "gpu_memory_percent": None,
        }
        if nvmlInit is None:
            return unavailable_current, unavailable_history
        try:
            nvmlInit()
            count = nvmlDeviceGetCount()
            if count < 1:
                return unavailable_current, unavailable_history
            handle = nvmlDeviceGetHandleByIndex(0)
            memory = nvmlDeviceGetMemoryInfo(handle)
            util = nvmlDeviceGetUtilizationRates(handle)
            current = {
                "available": True,
                "name": nvmlDeviceGetName(handle).decode() if isinstance(nvmlDeviceGetName(handle), bytes) else nvmlDeviceGetName(handle),
                "count": count,
                "memory_total_mb": int(memory.total / 1024 / 1024),
                "util_percent": float(util.gpu),
                "memory_percent": round(memory.used / memory.total * 100, 2) if memory.total else None,
            }
            history = {
                "gpu_util_percent": current["util_percent"],
                "gpu_memory_percent": current["memory_percent"],
            }
            return current, history
        except Exception:
            return unavailable_current, unavailable_history
        finally:
            try:
                nvmlShutdown()
            except Exception:
                pass
```

```python
from fastapi import APIRouter, HTTPException, Query

from src.services.system_monitor_service import SystemMonitorService

router = APIRouter(prefix="/system-monitor", tags=["system-monitor"])
system_monitor_service = SystemMonitorService()


@router.get("/summary")
async def get_system_monitor_summary():
    return system_monitor_service.get_summary()


@router.get("/current")
async def get_system_monitor_current():
    return system_monitor_service.get_current()


@router.get("/history")
async def get_system_monitor_history(window: str = Query("5m")):
    try:
        return system_monitor_service.get_history(window)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

Register the router in `backend/src/main.py` and start the sampler during startup:

```python
from src.api.routes import system_monitor
from src.api.routes import datasets, annotations, train, logs, models, infer

app.include_router(system_monitor.router)


@app.on_event("startup")
async def restore_ai_analysis_tasks():
    initialize_metadata_database()
    system_monitor.system_monitor_service.start()
    await channel_service.restore_channel_proxies()
```

- [ ] **Step 3: Run backend tests to verify they pass**

Run: `pytest backend/tests/test_system_monitor_service.py -v`
Expected: PASS for the new backend tests.

- [ ] **Step 4: Run related regression tests**

Run: `pytest backend/tests/test_task_service.py backend/tests/test_model_service.py -v`
Expected: PASS, proving the new route/service wiring did not break nearby backend behavior.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/system_monitor_service.py backend/src/api/routes/system_monitor.py backend/src/main.py backend/tests/test_system_monitor_service.py backend/requirements-runtime.txt
git commit -m "feat: add backend system monitor service"
```

### Task 3: Add frontend API client and route tests-by-build setup

**Files:**
- Create: `frontend/src/api/systemMonitor.ts`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/App.vue`
- Test: `frontend/src/api/systemMonitor.ts`

- [ ] **Step 1: Write the API client and route wiring**

```ts
import api from './axios'

export type MonitorWindow = '5m' | '30m' | '1h' | '6h' | '24h'

export const getSystemMonitorSummary = async () => {
  const { data } = await api.get('/system-monitor/summary')
  return data
}

export const getSystemMonitorCurrent = async () => {
  const { data } = await api.get('/system-monitor/current')
  return data
}

export const getSystemMonitorHistory = async (window: MonitorWindow) => {
  const { data } = await api.get('/system-monitor/history', {
    params: { window },
  })
  return data
}
```

Add the route:

```ts
{
  path: '/system-monitor',
  name: 'SystemMonitor',
  component: () => import('../pages/SystemMonitor.vue')
}
```

Add the nav entry in `frontend/src/App.vue`:

```vue
<router-link to="/system-monitor">服务器监控</router-link>
```

- [ ] **Step 2: Run frontend build to verify it fails for the missing page**

Run: `npm.cmd run build`
Expected: FAIL because `frontend/src/pages/SystemMonitor.vue` does not exist yet.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/systemMonitor.ts frontend/src/router/index.ts frontend/src/App.vue
git commit -m "feat: wire frontend system monitor route"
```

### Task 4: Implement the SystemMonitor page

**Files:**
- Create: `frontend/src/pages/SystemMonitor.vue`
- Modify: `frontend/src/App.vue`
- Test: `frontend/src/pages/SystemMonitor.vue`

- [ ] **Step 1: Implement the page script and template**

```vue
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import {
  getSystemMonitorCurrent,
  getSystemMonitorHistory,
  getSystemMonitorSummary,
  type MonitorWindow,
} from '@/api/systemMonitor'

const WINDOWS: Array<{ value: MonitorWindow; label: string }> = [
  { value: '5m', label: '5分钟' },
  { value: '30m', label: '30分钟' },
  { value: '1h', label: '1小时' },
  { value: '6h', label: '6小时' },
  { value: '24h', label: '24小时' },
]

const loading = ref(false)
const refreshing = ref(false)
const errorMessage = ref('')
const summary = ref<any | null>(null)
const current = ref<any | null>(null)
const history = ref<any[]>([])
const selectedWindow = ref<MonitorWindow>('5m')
let timer: ReturnType<typeof window.setInterval> | null = null

const loadSummary = async () => {
  summary.value = await getSystemMonitorSummary()
}

const loadCurrent = async () => {
  current.value = await getSystemMonitorCurrent()
}

const loadHistory = async () => {
  const result = await getSystemMonitorHistory(selectedWindow.value)
  history.value = result.points || []
}

const loadAll = async () => {
  loading.value = true
  errorMessage.value = ''
  try {
    await Promise.all([loadSummary(), loadCurrent(), loadHistory()])
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || error?.message || '监控数据加载失败'
  } finally {
    loading.value = false
  }
}

const refreshRealtime = async () => {
  refreshing.value = true
  try {
    await Promise.all([loadCurrent(), loadHistory()])
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || error?.message || '监控数据获取失败'
  } finally {
    refreshing.value = false
  }
}

const selectWindow = async (windowValue: MonitorWindow) => {
  selectedWindow.value = windowValue
  await loadHistory()
}

const percentChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { top: 0 },
  grid: { left: 40, right: 20, top: 40, bottom: 30 },
  xAxis: {
    type: 'category',
    data: history.value.map((item) => item.sampled_at?.slice(11) || ''),
  },
  yAxis: { type: 'value', max: 100 },
  series: [
    { name: 'CPU', type: 'line', smooth: true, data: history.value.map((item) => item.cpu_percent ?? null) },
    { name: '内存', type: 'line', smooth: true, data: history.value.map((item) => item.memory_percent ?? null) },
    { name: 'GPU', type: 'line', smooth: true, data: history.value.map((item) => item.gpu_util_percent ?? null) },
  ],
}))

const networkChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { top: 0 },
  grid: { left: 40, right: 20, top: 40, bottom: 30 },
  xAxis: {
    type: 'category',
    data: history.value.map((item) => item.sampled_at?.slice(11) || ''),
  },
  yAxis: { type: 'value' },
  series: [
    { name: '上行', type: 'line', smooth: true, data: history.value.map((item) => item.network_upload_bps ?? 0) },
    { name: '下行', type: 'line', smooth: true, data: history.value.map((item) => item.network_download_bps ?? 0) },
  ],
}))

onMounted(async () => {
  await loadAll()
  timer = window.setInterval(refreshRealtime, 5000)
})

onBeforeUnmount(() => {
  if (timer) {
    window.clearInterval(timer)
    timer = null
  }
})
</script>
```

```vue
<template>
  <div class="system-monitor-page">
    <section class="hero-card">
      <div>
        <h2>服务器监控</h2>
        <p>查看当前部署服务器的基础信息、实时负载和历史趋势。</p>
      </div>
      <button class="ghost-btn" @click="refreshRealtime" :disabled="refreshing">
        {{ refreshing ? '刷新中...' : '刷新状态' }}
      </button>
    </section>

    <div v-if="errorMessage" class="message error">{{ errorMessage }}</div>
    <div v-if="loading" class="empty-state">监控数据加载中...</div>

    <template v-else>
      <section class="summary-grid">
        <article class="panel summary-main">
          <div class="summary-top">
            <div>
              <h3>{{ summary?.hostname || '当前服务器' }}</h3>
              <p>{{ summary?.os_name || '-' }} {{ summary?.os_version || '' }}</p>
            </div>
            <span class="status-pill ok">在线</span>
          </div>
          <div class="summary-list">
            <div><strong>启动时间</strong>{{ summary?.boot_time || '-' }}</div>
            <div><strong>CPU</strong>{{ summary?.cpu?.model || '-' }}</div>
            <div><strong>核心 / 线程</strong>{{ summary?.cpu?.physical_cores || 0 }} / {{ summary?.cpu?.logical_cores || 0 }}</div>
            <div><strong>GPU</strong>{{ summary?.gpu?.available ? summary?.gpu?.name : '未检测到 GPU' }}</div>
          </div>
        </article>

        <article class="panel summary-side">
          <div class="summary-list">
            <div><strong>总内存</strong>{{ summary?.memory?.total_bytes || '-' }}</div>
            <div><strong>总磁盘</strong>{{ summary?.disk?.total_bytes || '-' }}</div>
            <div><strong>主网卡</strong>{{ summary?.network?.primary_interface || '-' }}</div>
            <div><strong>主 IP</strong>{{ summary?.network?.primary_ip || '-' }}</div>
          </div>
        </article>
      </section>

      <section class="metrics-grid">
        <article class="metric-card">
          <span>CPU</span>
          <strong>{{ current?.cpu_percent ?? '-' }}%</strong>
        </article>
        <article class="metric-card">
          <span>内存</span>
          <strong>{{ current?.memory_percent ?? '-' }}%</strong>
        </article>
        <article class="metric-card">
          <span>磁盘</span>
          <strong>{{ current?.disk_percent ?? '-' }}%</strong>
        </article>
        <article class="metric-card">
          <span>GPU</span>
          <strong>{{ current?.gpu?.available ? `${current?.gpu?.util_percent ?? '-'}%` : '未检测到 GPU' }}</strong>
        </article>
        <article class="metric-card">
          <span>上行</span>
          <strong>{{ current?.network_upload_bps ?? 0 }}</strong>
        </article>
        <article class="metric-card">
          <span>下行</span>
          <strong>{{ current?.network_download_bps ?? 0 }}</strong>
        </article>
      </section>

      <section class="panel chart-panel">
        <div class="chart-header">
          <h3>负载趋势</h3>
          <div class="window-tabs">
            <button
              v-for="item in WINDOWS"
              :key="item.value"
              :class="['tab-btn', selectedWindow === item.value ? 'active' : '']"
              @click="selectWindow(item.value)"
            >
              {{ item.label }}
            </button>
          </div>
        </div>

        <div class="chart-grid">
          <v-chart class="chart-box" :option="percentChartOption" autoresize />
          <v-chart class="chart-box" :option="networkChartOption" autoresize />
        </div>
      </section>
    </template>
  </div>
</template>
```

- [ ] **Step 2: Add the page styles**

```vue
<style scoped>
.system-monitor-page {
  display: grid;
  gap: 1rem;
}

.hero-card,
.panel,
.metric-card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.04);
}

.hero-card,
.chart-header,
.summary-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.hero-card,
.panel,
.metric-card {
  padding: 1.25rem;
}

.summary-grid,
.metrics-grid,
.chart-grid {
  display: grid;
  gap: 1rem;
}

.summary-grid {
  grid-template-columns: 1.4fr 1fr;
}

.metrics-grid {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}

.metric-card strong {
  display: block;
  margin-top: 0.5rem;
  font-size: 1.75rem;
  color: #0f172a;
}

.summary-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.75rem;
  margin-top: 1rem;
}

.window-tabs {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.tab-btn,
.ghost-btn {
  min-height: 40px;
  padding: 0.6rem 1rem;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  background: #fff;
  cursor: pointer;
}

.tab-btn.active {
  background: #1d4ed8;
  border-color: #1d4ed8;
  color: #fff;
}

.status-pill.ok {
  padding: 0.3rem 0.7rem;
  border-radius: 999px;
  background: #dcfce7;
  color: #166534;
  font-weight: 600;
}

.chart-grid {
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  margin-top: 1rem;
}

.chart-box {
  height: 320px;
}

.message.error,
.empty-state {
  border-radius: 12px;
  padding: 0.9rem 1rem;
}

.message.error {
  background: #fef3f2;
  color: #b42318;
  border: 1px solid #fecdca;
}

.empty-state {
  background: #f8fafc;
  color: #475569;
  border: 1px solid #e2e8f0;
}

@media (max-width: 960px) {
  .summary-grid,
  .chart-grid {
    grid-template-columns: 1fr;
  }

  .hero-card,
  .chart-header,
  .summary-top {
    flex-direction: column;
  }
}
</style>
```

- [ ] **Step 3: Run frontend build to verify it passes**

Run: `npm.cmd run build`
Expected: PASS, producing the Vite build output without route import errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SystemMonitor.vue frontend/src/api/systemMonitor.ts frontend/src/router/index.ts frontend/src/App.vue
git commit -m "feat: add frontend system monitor page"
```

### Task 5: End-to-end verification

**Files:**
- Modify: `docs/superpowers/plans/2026-06-15-system-monitor.md`
- Test: `backend/tests/test_system_monitor_service.py`

- [ ] **Step 1: Run backend tests**

Run: `pytest backend/tests/test_system_monitor_service.py backend/tests/test_task_service.py backend/tests/test_model_service.py -v`
Expected: PASS for all selected tests.

- [ ] **Step 2: Run frontend build**

Run: `npm.cmd run build`
Expected: PASS with no TypeScript or Vite compile errors.

- [ ] **Step 3: Manually verify the page**

Run the local stack, open the app, and verify:

```text
1. 顶部菜单出现“服务器监控”
2. 页面能展示主机名、系统信息、CPU/GPU/内存/磁盘/网络信息
3. 时间窗口切换能刷新曲线
4. 页面停留时数据每 5 秒刷新一次
5. 页面离开后不继续刷新
```

- [ ] **Step 4: Commit final verification notes if plan tracking was updated**

```bash
git add docs/superpowers/plans/2026-06-15-system-monitor.md
git commit -m "docs: record system monitor verification"
```
