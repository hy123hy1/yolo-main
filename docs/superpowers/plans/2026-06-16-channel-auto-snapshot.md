# Channel Auto Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically capture and persist a channel snapshot shortly after channel creation or WVP sync, without blocking the main API flow.

**Architecture:** Keep snapshot capture logic centralized inside `ChannelService`, and add a lightweight async scheduling layer that is triggered from channel creation and WVP sync entrypoints. Reuse the existing snapshot persistence path so JSON metadata and the `channels` table stay in sync through `save_json` and `metadata_db_sync`.

**Tech Stack:** Python, asyncio, FastAPI route handlers, existing JSON metadata storage, SQLAlchemy-backed metadata sync, unittest with async mocks.

---

### Task 1: Add failing tests for auto-snapshot scheduling

**Files:**
- Modify: `backend/tests/test_channel_service.py`
- Modify: `backend/tests/test_wvp_service.py`
- Create: `backend/tests/test_channels_route.py`

- [ ] **Step 1: Write the failing tests**

Add tests that assert:

```python
async def test_create_channel_schedules_snapshot_capture(self):
    ...
```

```python
def test_sync_wvp_route_schedules_snapshot_for_synced_channels():
    ...
```

```python
async def test_ensure_channel_snapshot_skips_when_snapshot_file_exists(self):
    ...
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
python -m unittest backend.tests.test_channel_service backend.tests.test_wvp_service backend.tests.test_channels_route
```

Expected:
- At least the new tests fail because the scheduling/helper methods do not exist yet

- [ ] **Step 3: Do not change production code until the failures are confirmed**

The expected red-state failures should mention missing helper behavior, missing scheduling calls, or missing route-triggered snapshot dispatch.

### Task 2: Implement reusable auto-snapshot logic in ChannelService

**Files:**
- Modify: `backend/src/services/channel_service.py`
- Test: `backend/tests/test_channel_service.py`

- [ ] **Step 1: Add a reusable internal capture helper**

Refactor the existing `snapshot()` flow so the persistence work lives in a shared internal helper used by both manual snapshot and auto-snapshot flows.

- [ ] **Step 2: Add an idempotent ensure/schedule API**

Implement helper methods that:

- load the channel
- skip if no `source_url`
- skip if `snapshot_path` exists and points to a real file
- otherwise capture and persist a new snapshot
- expose a scheduling wrapper that uses `asyncio.create_task(...)`
- swallow/log errors in background execution

- [ ] **Step 3: Trigger scheduling from channel creation**

After the channel JSON is saved in `create_channel()`, schedule an auto snapshot immediately, without awaiting completion and without changing current create/play behavior.

- [ ] **Step 4: Run the focused ChannelService tests**

Run:

```bash
python -m unittest backend.tests.test_channel_service
```

Expected:
- PASS

### Task 3: Trigger auto-snapshot after WVP sync

**Files:**
- Modify: `backend/src/api/routes/channels.py`
- Test: `backend/tests/test_channels_route.py`

- [ ] **Step 1: Add route-level scheduling after sync-wvp completes**

Keep `WVPService.sync_channels()` focused on fetching and persisting data. After the thread returns in the FastAPI route, iterate through synced items and schedule auto snapshots through `channel_service`.

- [ ] **Step 2: Keep WVP sync non-blocking**

Do not await the background snapshot tasks. The route should return the original sync result even if snapshot scheduling later fails.

- [ ] **Step 3: Run the focused route tests**

Run:

```bash
python -m unittest backend.tests.test_channels_route
```

Expected:
- PASS

### Task 4: Verify end-to-end regression coverage

**Files:**
- Modify: `backend/tests/test_wvp_service.py`
- Test: `backend/tests/test_channel_service.py`
- Test: `backend/tests/test_channels_route.py`

- [ ] **Step 1: Add or tighten regression assertions**

Confirm:

- create channel still succeeds when scheduling is present
- existing snapshot files are not recaptured
- WVP sync still returns the same payload shape while also scheduling snapshots

- [ ] **Step 2: Run the combined tests**

Run:

```bash
python -m unittest backend.tests.test_channel_service backend.tests.test_wvp_service backend.tests.test_channels_route
```

Expected:
- PASS

- [ ] **Step 3: Run syntax verification**

Run:

```bash
python -m py_compile backend/src/services/channel_service.py backend/src/api/routes/channels.py backend/tests/test_channel_service.py backend/tests/test_wvp_service.py backend/tests/test_channels_route.py
```

Expected:
- no output

- [ ] **Step 4: Restart backend and verify health**

Run:

```bash
docker compose -f docker\docker-compose.yml restart backend
```

Then run:

```bash
Invoke-RestMethod -Uri http://localhost:8000/health | ConvertTo-Json -Compress
```

Expected:

```json
{"status":"ok"}
```
