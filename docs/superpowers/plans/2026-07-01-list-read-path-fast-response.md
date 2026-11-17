# List Read Path Fast Response Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make channel, model, and task list pages stay fast and stable even when tasks exist, while keeping `GET` routes side-effect free.

**Architecture:** Keep list APIs as DB-summary reads only, and move heavy runtime work to explicit action flows or detail reads. The backend will return cached summary/runtime fields from metadata tables, while the frontend will stop eagerly fanning out per-channel and per-task follow-up requests during initial page load.

**Tech Stack:** FastAPI, SQLAlchemy, Vue 3, TypeScript, Vite, unittest, node:test

---

## File Map

- `backend/src/services/task_service.py`
  Responsible for task list/detail reads. This plan adds a summary-only list projection so `/tasks` no longer expands every task into full algorithm/region detail.
- `backend/src/services/model_service.py`
  Responsible for model list/detail reads. This plan keeps list reads DB-backed and stops runtime file-size refresh on every row.
- `backend/src/services/channel_service.py`
  Responsible for channel preview/status/play-url logic. This plan removes hidden bootstrap behavior from read-only preview endpoints.
- `frontend/src/pages/Channels.vue`
  Responsible for the main channel/task/fence page. This plan narrows initial load and switches expensive secondary reads to on-demand triggers.
- `frontend/src/components/channels/TaskListPanel.vue`
  Responsible for rendering task list cards. This plan switches list rendering from `task.algorithms.length` to summary fields such as `algorithm_count`.
- `frontend/src/utils/channels-load-strategy.js`
  Responsible for extracting page-load orchestration into a testable helper so the initial request set stays intentionally small.
- `backend/tests/test_task_service.py`
  Adds regression tests proving `/tasks` list path stays summary-only.
- `backend/tests/test_model_service.py`
  Adds regression tests proving `/models` list path does not touch runtime file inspection.
- `backend/tests/test_channel_service.py`
  Adds regression tests proving preview status/play-url `GET` paths do not bootstrap runtime work.
- `frontend/tests/channels-load-strategy.test.mjs`
  Adds a focused test for the new page-load orchestration helpers so initial load stays narrow.

---

### Task 1: Lock backend read-path behavior with failing tests

**Files:**
- Modify: `backend/tests/test_task_service.py`
- Modify: `backend/tests/test_model_service.py`
- Modify: `backend/tests/test_channel_service.py`

- [ ] **Step 1: Write the failing task-list summary test**

```python
    async def test_list_tasks_uses_metadata_summary_without_expanding_task_detail(self):
        with self.session_local() as session:
            session.add(
                TaskMeta(
                    id="task_summary_1",
                    name="summary-task",
                    source_url="rtsp://example/live/summary",
                    source_type="rtsp",
                    channel_id="channel_summary_1",
                    frame_interval=12,
                    status="running",
                    algorithm_count=2,
                    region_count=1,
                    is_running=True,
                    created_at=datetime.fromisoformat("2026-07-01T10:00:00"),
                    updated_at=datetime.fromisoformat("2026-07-01T10:05:00"),
                    raw_json={
                        "id": "task_summary_1",
                        "runtime": {
                            "status": "running",
                            "is_running": True,
                            "last_event_at": "2026-07-01 10:04:00",
                        },
                    },
                )
            )
            session.commit()

        with patch.object(
            self.service,
            "get_task_from_metadata",
            AsyncMock(side_effect=AssertionError("list_tasks should not expand detail")),
        ):
            result = await self.service.list_tasks()

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["algorithm_count"], 2)
        self.assertEqual(result["items"][0]["region_count"], 1)
        self.assertEqual(result["items"][0]["runtime"]["status"], "running")
```

- [ ] **Step 2: Write the failing model-list no-runtime-refresh test**

```python
    async def test_list_models_uses_cached_metadata_without_runtime_file_size_refresh(self):
        self._insert_model_row("model_summary_1", "summary-model", weights_path="G:/missing/model.pt")
        service = ModelService()

        with patch.object(
            service,
            "_refresh_runtime_file_size",
            side_effect=AssertionError("list_models should not refresh runtime file size"),
        ):
            result = await service.list_models()

        self.assertEqual(result["models"][0]["model_id"], "model_summary_1")
        self.assertEqual(result["models"][0]["file_size"], 123)
```

- [ ] **Step 3: Write the failing preview-read no-bootstrap tests**

```python
    async def test_get_zone_preview_status_returns_cached_state_without_bootstrap(self):
        payload = {
            "id": "ch_cached_zone",
            "name": "cached-zone",
            "type": "rtsp",
            "source_url": "rtsp://example/live/cached-zone",
            "app": "rtp",
            "stream": "cached-zone",
            "is_online": True,
            "is_playing": False,
            "webrtc_api": "http://127.0.0.1/proxy/webrtc",
            "hls": "http://127.0.0.1/proxy/hls.m3u8",
            "ext": {
                "task_preview": {
                    "resolved_mode": "zone",
                    "status": "running",
                    "owner_task_id": "task_cached_zone",
                    "play_url": {
                        "webrtc_api": "http://127.0.0.1/proxy/webrtc",
                        "hls": "http://127.0.0.1/proxy/hls.m3u8",
                    },
                },
                "zone_preview": {
                    "status": "running",
                    "zone_count": 1,
                },
            },
        }
        self._insert_channel_meta(payload)
        service = ChannelService()

        with patch.object(
            service,
            "_bootstrap_running_task_preview",
            AsyncMock(side_effect=AssertionError("GET status must not bootstrap preview")),
        ):
            result = await service.get_zone_preview_status("ch_cached_zone")

        self.assertEqual(result["resolved_mode"], "zone")
        self.assertEqual(result["status"], "running")
```

```python
    async def test_get_zone_preview_browser_play_url_returns_cached_play_url_without_bootstrap(self):
        service = ChannelService()
        with patch.object(
            service,
            "_bootstrap_running_task_preview",
            AsyncMock(side_effect=AssertionError("GET browser play url must not bootstrap preview")),
        ):
            result = await service.get_zone_preview_browser_play_url("ch_cached_zone")

        self.assertEqual(result["play_url"]["webrtc_api"], "http://127.0.0.1/proxy/webrtc")
```

- [ ] **Step 4: Run focused backend tests to verify they fail**

Run:

```powershell
python -m pytest backend/tests/test_task_service.py -k summary -v
python -m pytest backend/tests/test_model_service.py -k runtime_file_size_refresh -v
python -m pytest backend/tests/test_channel_service.py -k bootstrap -v
```

Expected: at least one test fails because `list_tasks()` still calls `get_task_from_metadata()`, `list_models()` still calls `_refresh_runtime_file_size()`, and preview `GET` paths still reach bootstrap code.

- [ ] **Step 5: Commit the failing tests**

```bash
git add backend/tests/test_task_service.py backend/tests/test_model_service.py backend/tests/test_channel_service.py
git commit -m "test: lock fast read-path regressions"
```

### Task 2: Make backend list reads summary-only and side-effect free

**Files:**
- Modify: `backend/src/services/task_service.py`
- Modify: `backend/src/services/model_service.py`
- Modify: `backend/src/services/channel_service.py`
- Test: `backend/tests/test_task_service.py`
- Test: `backend/tests/test_model_service.py`
- Test: `backend/tests/test_channel_service.py`

- [ ] **Step 1: Add a task-list summary builder in `task_service.py`**

```python
    def _build_task_list_item_from_metadata_row(self, row: TaskMeta) -> dict[str, Any]:
        payload = dict(row.raw_json or {})
        runtime_payload = dict(payload.get("runtime") or {})
        runtime_status = runtime_payload.get("status") or row.status or "stopped"
        runtime = {
            "status": runtime_status,
            "is_running": bool(runtime_payload.get("is_running", row.is_running)),
            "started_at": format_datetime(row.started_at),
            "stopped_at": format_datetime(row.stopped_at),
            "last_error": row.last_error,
            "last_analysis_at": format_datetime(row.last_analysis_at),
            "last_event_at": format_datetime(row.last_event_at),
        }
        return {
            "id": row.id,
            "name": row.name,
            "source_url": row.source_url,
            "source_type": row.source_type,
            "channel_id": row.channel_id,
            "frame_interval": row.frame_interval,
            "status": row.status or runtime_status,
            "algorithm_count": int(row.algorithm_count or 0),
            "region_count": int(row.region_count or 0),
            "created_at": format_datetime(row.created_at),
            "updated_at": format_datetime(row.updated_at),
            "runtime": runtime,
        }
```

- [ ] **Step 2: Change `list_tasks()` to use the summary builder instead of `get_task_from_metadata()`**

```python
    async def list_tasks(self, prefer_metadata: bool = False) -> dict[str, Any]:
        with SessionLocal() as session:
            task_rows = (
                session.query(TaskMeta)
                .order_by(TaskMeta.created_at.desc(), TaskMeta.id.desc())
                .all()
            )

        if task_rows:
            return {
                "items": [self._build_task_list_item_from_metadata_row(row) for row in task_rows]
            }
```

- [ ] **Step 3: Make `list_models()` return cached DB metadata only**

```python
    async def list_models(self):
        with SessionLocal() as session:
            rows = (
                session.query(ModelMetaRecord)
                .order_by(ModelMetaRecord.created_at.desc(), ModelMetaRecord.id.desc())
                .all()
            )

        if settings.METADATA_DB_ENABLED or rows:
            metadata_models = [
                self._apply_label_recommendation(self._build_model_from_metadata_row(row))
                for row in rows
            ]
            return {"models": metadata_models}
```

- [ ] **Step 4: Remove bootstrap calls from preview status/play-url `GET` paths**

```python
    async def get_zone_preview_status(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        prepared = self._build_prepared_task_preview_status(channel)
        if prepared:
            return prepared
        return zone_preview_service.status(channel_id)
```

```python
    async def get_zone_preview_browser_play_url(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None

        prepared = self._build_prepared_task_preview_status(channel)
        if not prepared:
            return {
                "channel_id": channel_id,
                "resolved_mode": "unknown",
                "source": "cache",
                "status": "stopped",
                "play_url": {
                    "webrtc_api": channel.get("webrtc_api"),
                    "hls": channel.get("hls"),
                },
            }

        return {
            "channel_id": channel_id,
            "resolved_mode": prepared.get("resolved_mode"),
            "source": "cache",
            "status": prepared.get("status"),
            "play_url": prepared.get("play_url") or {
                "webrtc_api": channel.get("webrtc_api"),
                "hls": channel.get("hls"),
            },
        }
```

- [ ] **Step 5: Run the focused backend tests and verify they pass**

Run:

```powershell
python -m pytest backend/tests/test_task_service.py -k summary -v
python -m pytest backend/tests/test_model_service.py -k runtime_file_size_refresh -v
python -m pytest backend/tests/test_channel_service.py -k bootstrap -v
```

Expected: PASS for all newly added regression tests.

- [ ] **Step 6: Commit the backend read-path refactor**

```bash
git add backend/src/services/task_service.py backend/src/services/model_service.py backend/src/services/channel_service.py backend/tests/test_task_service.py backend/tests/test_model_service.py backend/tests/test_channel_service.py
git commit -m "refactor: make list reads summary-only and side-effect free"
```

### Task 3: Narrow `Channels.vue` initial load and move heavy reads on-demand

**Files:**
- Modify: `frontend/src/pages/Channels.vue`
- Modify: `frontend/src/components/channels/TaskListPanel.vue`
- Add: `frontend/src/utils/channels-load-strategy.js`
- Add: `frontend/tests/channels-load-strategy.test.mjs`

- [ ] **Step 1: Extract small load orchestrators for each concern**

```ts
const loadBasePageData = async () => {
  const [channelResult, mediaNodeResult, modelResult] = await Promise.all([
    listChannels(),
    listMediaNodes(),
    refreshModels(),
  ])
  items.value = channelResult.items || []
  mediaNodes.value = mediaNodeResult.items || []
  models.value = modelResult || []
  syncAIConfigsFromItems()
}

const loadManageTabData = async () => {
  await Promise.all([loadAIStatuses(), loadChannelZonesForVisibleManageCards()])
}

const loadTasksTabData = async () => {
  await loadTasksForCurrentChannel()
}

const loadFenceTabData = async () => {
  await loadAllZonePreviewStatuses()
}
```

- [ ] **Step 2: Make initial `load()` call only base data**

```ts
const load = async () => {
  loading.value = true
  try {
    await loadBasePageData()
    if (activeTab.value === 'manage') {
      await loadManageTabData()
    }
    if (activeTab.value === 'tasks') {
      await loadTasksTabData()
    }
    if (activeTab.value === 'fence') {
      await loadFenceTabData()
    }
  } finally {
    loading.value = false
  }

  if (activeTab.value === 'preview') {
    void restoreBrowserPlaybackForActiveChannels()
  }
}
```

- [ ] **Step 3: Stop loading all task statuses during task-list fetch**

```ts
const loadTasksForCurrentChannel = async () => {
  taskLoading.value = true
  try {
    const result = await listTasks()
    tasks.value = result.items || []
    syncSelectedTask()
  } finally {
    taskLoading.value = false
  }
}
```

```ts
const hasRunnableTaskAlgorithm = (task: any) => {
  if (Array.isArray(task?.algorithms) && task.algorithms.length > 0) {
    return task.algorithms.some((algorithm: any) => algorithm?.enabled !== false && algorithm?.model_id)
  }
  return Number(task?.algorithm_count || 0) > 0
}
```

- [ ] **Step 4: Make task cards render summary counts instead of full algorithm arrays**

```vue
<span>算法 {{ Number(task.algorithm_count ?? task.algorithms?.length ?? 0) }}</span>
```

- [ ] **Step 5: Update `watch(activeTab, ...)` to load secondary data only when the tab is entered**

```ts
watch(activeTab, async (tab) => {
  if (shouldPollChannelAiStatus(tab)) {
    void loadAIStatuses()
    startAiStatusPolling()
  } else {
    stopAiStatusPolling()
  }

  if (tab === 'manage') {
    await loadManageTabData()
  }
  if (tab === 'tasks') {
    await loadTasksTabData()
  }
  if (tab === 'fence') {
    await loadFenceTabData()
  } else {
    await resetAllZonePreviewPlayers()
  }
  if (tab === 'preview') {
    void restoreBrowserPlaybackForActiveChannels()
  }
})
```

- [ ] **Step 6: Add a focused frontend orchestration test**

```ts
import test from 'node:test'
import assert from 'node:assert/strict'
import { createChannelsPageLoadStrategy } from '../src/utils/channels-load-strategy.js'

test('initial load only requests base data', async () => {
  const calls: string[] = []
  const strategy = createChannelsPageLoadStrategy({
    loadBase: async () => calls.push('base'),
    loadTasks: async () => calls.push('tasks'),
    loadFence: async () => calls.push('fence'),
    restorePreview: async () => calls.push('preview'),
  })

  await strategy.initialize('preview')

  assert.deepEqual(calls, ['base', 'preview'])
})
```

```js
export const createChannelsPageLoadStrategy = ({
  loadBase,
  loadManage,
  loadTasks,
  loadFence,
  restorePreview,
}) => ({
  async initialize(tab) {
    await loadBase()
    if (tab === 'manage') await loadManage?.()
    if (tab === 'tasks') await loadTasks?.()
    if (tab === 'fence') await loadFence?.()
    if (tab === 'preview') await restorePreview?.()
  },
})
```

- [ ] **Step 7: Run frontend test/build verification**

Run:

```powershell
node --test frontend/tests/channels-load-strategy.test.mjs
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command "npm.cmd run build"
```

Expected: the new node test passes, and the frontend build completes successfully.

- [ ] **Step 8: Commit the frontend load-strategy change**

```bash
git add frontend/src/pages/Channels.vue frontend/src/components/channels/TaskListPanel.vue frontend/src/utils/channels-load-strategy.js frontend/tests/channels-load-strategy.test.mjs
git commit -m "perf: defer heavy channel page reads"
```

### Task 4: Remove dead eager-read branches and keep route contracts stable

**Files:**
- Modify: `frontend/src/pages/Channels.vue`
- Modify: `backend/src/services/channel_service.py`
- Test: `backend/tests/test_channel_service.py`

- [ ] **Step 1: Delete read-path fallback branches that start runtime work**

```python
# delete this pattern from read-only GET paths
prepared = self._build_prepared_task_preview_status(channel) or await self._bootstrap_running_task_preview(channel)
```

```ts
// keep explicit action flow only
if (zonePreviewStatuses.value[channelId]?.status !== 'running') {
  await startChannelZonePreview(channelId)
  await loadZonePreviewStatus(channelId)
}
```

- [ ] **Step 2: Keep response shapes stable while using cached values**

```python
        return {
            "channel_id": channel_id,
            "resolved_mode": prepared.get("resolved_mode"),
            "source": "cache",
            "status": prepared.get("status"),
            "play_url": {
                "webrtc_api": channel.get("webrtc_api"),
                "hls": channel.get("hls"),
            },
        }
```

- [ ] **Step 3: Run the mixed regression set**

Run:

```powershell
python -m pytest backend/tests/test_channel_service.py -v
python -m pytest backend/tests/test_channels_route.py -v
node --test frontend/tests/channels-load-strategy.test.mjs
```

Expected: read-only preview routes remain compatible, and no test depends on hidden bootstrap from `GET`.

- [ ] **Step 4: Commit the cleanup**

```bash
git add backend/src/services/channel_service.py backend/tests/test_channel_service.py frontend/src/pages/Channels.vue
git commit -m "cleanup: remove eager preview bootstrap from read paths"
```

### Task 5: Final verification

**Files:**
- No code changes required if all checks are green

- [ ] **Step 1: Run the focused backend regression suite**

```powershell
python -m pytest backend/tests/test_task_service.py -v
python -m pytest backend/tests/test_model_service.py -v
python -m pytest backend/tests/test_channel_service.py -v
python -m pytest backend/tests/test_channels_route.py -v
```

Expected: PASS.

- [ ] **Step 2: Run syntax/build verification**

```powershell
python -m compileall backend/src
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command "npm.cmd run build"
```

Expected: backend compiles without syntax errors; frontend build succeeds.

- [ ] **Step 3: Manual smoke-check the page flow**

Run through this checklist:

```text
1. 打开“通道管理”页面，确认首屏先出通道列表，不再等待任务/围栏状态全量请求。
2. 切到“任务管理”页，再加载任务列表；点击某个任务时，才加载算法详情和任务状态。
3. 切到“电子围栏”页，再加载区域流状态；点击播放时，仍然可以显式启动并播放。
4. 确认仅调用 GET /channels 不会触发 zone-preview/start、play、WVP /play、snapshot 刷新。
```

- [ ] **Step 4: Commit verification-only notes if any documentation changed**

```bash
git status --short
```

Expected: no unexpected file drift remains.
