# Snapshot Refresh Fast Response Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make manual channel snapshot refresh return quickly while continuing to refresh the underlying image in the background.

**Architecture:** Keep synchronous snapshot capture logic for internal backend callers, but introduce a lightweight background refresh request path for the `/channels/{channel_id}/snapshot` API. Update the frontend snapshot refresh actions to call the fast API and then poll `snapshot/latest` until a new or first snapshot becomes available.

**Tech Stack:** Python, asyncio, FastAPI routes, Vue 3, existing channel snapshot metadata, unittest.

---

### Task 1: Add failing tests for fast snapshot refresh behavior

**Files:**
- Modify: `backend/tests/test_channel_service.py`
- Modify: `backend/tests/test_channels_route.py`

- [ ] **Step 1: Write the failing tests**

Add tests that assert:

- `ChannelService.request_snapshot_refresh()` returns a quick `pending` response and schedules background work
- `POST /channels/{channel_id}/snapshot` calls the quick refresh method instead of the synchronous capture method

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
python -m unittest backend.tests.test_channel_service backend.tests.test_channels_route
```

Expected:
- New tests fail because the request/scheduling method does not exist yet and the route still calls the synchronous snapshot capture

### Task 2: Implement backend background snapshot request flow

**Files:**
- Modify: `backend/src/services/channel_service.py`
- Modify: `backend/src/api/routes/channels.py`
- Test: `backend/tests/test_channel_service.py`
- Test: `backend/tests/test_channels_route.py`

- [ ] **Step 1: Add background refresh task bookkeeping**

Implement an in-memory per-channel task registry so repeated clicks do not start duplicate snapshot refresh jobs for the same channel.

- [ ] **Step 2: Add a quick request method**

Create a `request_snapshot_refresh(channel_id)` method that:

- validates the channel exists
- returns current `snapshot_url` / `snapshot_path`
- schedules a background `ensure_channel_snapshot(..., force=True)` job when no job is already running
- returns a response like `status=pending|running`

- [ ] **Step 3: Keep synchronous capture API for internal callers**

Do not change the semantics of `ChannelService.snapshot()` because internal backend flows such as debug capture still need the actual file immediately.

- [ ] **Step 4: Update the route to use the quick request method**

Change `POST /channels/{channel_id}/snapshot` to call the new quick request method.

- [ ] **Step 5: Run the focused backend tests**

Run:

```bash
python -m unittest backend.tests.test_channel_service backend.tests.test_channels_route
```

Expected:
- PASS

### Task 3: Update frontend refresh flow to poll latest snapshot

**Files:**
- Modify: `frontend/src/api/channels.ts`
- Modify: `frontend/src/components/channels/ZoneEditorModal.vue`
- Modify: `frontend/src/components/channels/TaskRegionEditorModal.vue`

- [ ] **Step 1: Add latest snapshot API helper**

Expose `GET /channels/{channel_id}/snapshot/latest` in the frontend API layer.

- [ ] **Step 2: Update both region editors**

When the user clicks refresh:

- remember the previous `snapshot_url`
- call the fast refresh endpoint
- poll `latest` for a short window until a first or changed snapshot appears
- then update the preview image

- [ ] **Step 3: Preserve user feedback**

Show success when the new snapshot is ready; show timeout/failure feedback if polling completes without a new image.

### Task 4: Verify regression behavior

**Files:**
- Modify: `backend/tests/test_channel_service.py`
- Modify: `backend/tests/test_channels_route.py`

- [ ] **Step 1: Run backend regression tests**

Run:

```bash
python -m unittest backend.tests.test_channel_service backend.tests.test_wvp_service backend.tests.test_channels_route
```

Expected:
- PASS

- [ ] **Step 2: Run syntax verification**

Run:

```bash
python -m py_compile backend/src/services/channel_service.py backend/src/api/routes/channels.py frontend/src/api/channels.ts
```

Expected:
- backend files compile cleanly; frontend TS file is syntax-checked later by build

- [ ] **Step 3: Build frontend and restart backend if needed**

Run:

```bash
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command "npm.cmd run build"
```

from `G:\docker\yolo-main\frontend`

Then run:

```bash
docker compose -f docker\docker-compose.yml restart backend
Invoke-RestMethod -Uri http://localhost:8000/health | ConvertTo-Json -Compress
```

Expected:
- frontend build passes
- backend health returns `{"status":"ok"}`
