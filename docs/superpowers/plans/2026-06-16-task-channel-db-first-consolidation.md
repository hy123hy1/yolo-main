# Task/Channel DB-First Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the WVP-channel-to-task flow onto one API path and make task/channel/model list reads DB-first by default.

**Architecture:** Keep JSON files as runtime persistence and metadata sync source, but make public list/detail APIs query metadata tables first and only fall back to JSON when metadata is unavailable. Remove frontend dependence on `prefer_metadata` and delete clearly unused compatibility endpoints.

**Tech Stack:** FastAPI, SQLAlchemy, JSON metadata sync, Vue 3, TypeScript, Vite

---

### Task 1: Lock Target Behavior With Tests

**Files:**
- Modify: `backend/tests/test_task_service.py`
- Modify: `backend/tests/test_channel_service.py`
- Modify: `backend/tests/test_model_service.py`

- [ ] Add failing tests for DB-first reads:
  - `TaskService.list_tasks()` should prefer DB rows over direct JSON enumeration.
  - `TaskService.get_task()` and `list_task_algorithms()` should read the metadata projection by default.
  - `ChannelService.list_available_for_task_channels()` should determine occupied channels from task metadata, not JSON scans.
  - `ModelService.list_models()` should return DB-backed models when metadata exists.

- [ ] Run focused backend tests and confirm failures before implementation.

### Task 2: Make Task Reads DB-First

**Files:**
- Modify: `backend/src/services/task_service.py`
- Modify: `backend/src/api/routes/tasks.py`

- [ ] Remove `prefer_metadata` from the main task list/detail/algorithm/available-models route contracts.
- [ ] Make `list_tasks`, `get_task`, `list_task_algorithms`, and `list_task_available_models` query metadata first.
- [ ] Keep JSON fallback only when metadata rows are missing.

### Task 3: Make Channel Lists DB-First

**Files:**
- Modify: `backend/src/services/channel_service.py`

- [ ] Change `list_channels()` to build the channel set from metadata rows first.
- [ ] Rehydrate from `raw_json` for extended runtime fields.
- [ ] Preserve online-state probing and persistence updates.
- [ ] Change `list_available_for_task_channels()` to compute occupied channels from task metadata.

### Task 4: Make Model Lists DB-First

**Files:**
- Modify: `backend/src/services/model_service.py`

- [ ] Change `list_models()` to query `ModelMetaRecord` first.
- [ ] Change `get_model()` to prefer the metadata row and only use the model directory for derived file information and training artifacts.
- [ ] Preserve label recommendation enrichment.

### Task 5: Remove Frontend Main-Flow Compatibility Branches

**Files:**
- Modify: `frontend/src/api/tasks.ts`
- Modify: `frontend/src/pages/Channels.vue`

- [ ] Remove `preferMetadata` parameters from task API wrappers used by the main UI.
- [ ] Remove fallback branches in `Channels.vue` that call the same endpoint twice with and without metadata preference.
- [ ] Keep task detail, algorithm list, available-model list, and region editor behavior intact.

### Task 6: Delete Confirmed Redundant Endpoint Usage

**Files:**
- Modify: `frontend/src/api/channels.ts`
- Modify: `backend/src/api/routes/channels.py`
- Modify: `backend/src/services/channel_service.py`
- Modify: `backend/tests/test_task_service.py`

- [ ] Delete the unused frontend wrapper for `GET /channels/{channel_id}/zone-preview/play-url`.
- [ ] Delete the matching route if it is no longer used by the application.
- [ ] Remove or update tests that only exist for the deleted route path.

### Task 7: Verify

**Files:**
- No code changes required if green

- [ ] Run focused backend tests for task/channel/model service behavior.
- [ ] Run backend syntax validation.
- [ ] Run frontend build: `npm run build`.
- [ ] Summarize any remaining compatibility/debug endpoints that were intentionally kept.
