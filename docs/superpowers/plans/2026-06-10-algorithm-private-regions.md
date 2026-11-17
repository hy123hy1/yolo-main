# Algorithm Private Regions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace task-shared regions with algorithm-private regions and make the region editor load/save regions only through algorithm-scoped APIs.

**Architecture:** Move region ownership from the task-level shared pool to dedicated algorithm-owned records. Keep task runtime and zone-preview logic working by aggregating enabled overlay regions from each algorithm's own region list. Add a dedicated `GET /tasks/{task_id}/algorithms/{algorithm_id}/regions` read API and make the frontend editor use only algorithm-scoped CRUD operations.

**Tech Stack:** FastAPI, SQLAlchemy, JSON file storage, Vue 3, TypeScript, Vite

---

### Task 1: Lock Backend Behavior With Failing Tests

**Files:**
- Modify: `backend/tests/test_task_service.py`
- Modify: `backend/tests/test_metadata_db_sync.py`

- [ ] Add tests that describe algorithm-private region behavior:
  - algorithm region CRUD should not depend on `task.regions`
  - the same task can have two algorithms with regions that have the same coordinates but are stored as different regions
  - `list_algorithm_regions(task_id, algorithm_id)` returns only the current algorithm's regions
  - deleting one algorithm region must not affect another algorithm
  - zone preview aggregation must collect from `algorithm.regions`

- [ ] Run the focused backend tests and confirm they fail before implementation.

### Task 2: Add Algorithm Region Persistence Model

**Files:**
- Modify: `backend/src/db/models.py`
- Modify: `backend/src/services/metadata_db_sync.py`

- [ ] Add a dedicated `AlgorithmRegionMeta` table with `id`, `task_id`, `algorithm_id`, `name`, `type`, `color`, `coordinates_json`, `enabled`, `created_at`, `updated_at`, `raw_json`.
- [ ] Update metadata DB migration/bootstrap logic so the table is created and synced.
- [ ] Keep legacy task region tables untouched for compatibility during transition, but stop using them as the primary source for fresh writes.

### Task 3: Refactor Task Service To Algorithm-Owned Regions

**Files:**
- Modify: `backend/src/services/task_service.py`

- [ ] Add algorithm-scoped helpers:
  - `list_algorithm_regions`
  - `add_algorithm_region`
  - `update_algorithm_region`
  - `delete_algorithm_region`
- [ ] Refactor normalization so algorithms carry their own `regions` list and `region_ids` is derived from that list.
- [ ] Stop creating or relying on shared task region pools for new writes.
- [ ] Update zone-preview aggregation to collect overlay regions from `algorithm.regions`.
- [ ] Keep task read compatibility by deriving any legacy-compatible task view from algorithm-owned regions where needed.

### Task 4: Expose Algorithm Region Detail API

**Files:**
- Modify: `backend/src/api/routes/tasks.py`
- Modify: `frontend/src/api/tasks.ts`

- [ ] Add `GET /tasks/{task_id}/algorithms/{algorithm_id}/regions`.
- [ ] Make existing algorithm region `POST/PUT/DELETE` endpoints operate on algorithm-owned region storage.
- [ ] Mark old task region and region-link endpoints as legacy in code comments if still retained for compatibility.

### Task 5: Migrate Region Editor Frontend

**Files:**
- Modify: `frontend/src/components/channels/TaskRegionEditorModal.vue`
- Modify: `frontend/src/pages/Channels.vue`

- [ ] Change the modal to fetch region details from the new algorithm region list API when opened.
- [ ] Remove shared-pool UI and binding checkbox logic.
- [ ] Make new region creation go straight to the current algorithm.
- [ ] Make save/delete operate only on the current algorithm's regions.
- [ ] Keep the existing snapshot capture flow intact.

### Task 6: Keep Zone Preview Refresh Correct

**Files:**
- Modify: `frontend/src/pages/Channels.vue`
- Modify: `backend/src/api/routes/channels.py`
- Modify: `backend/src/services/channel_service.py`

- [ ] Ensure "save and refresh preview" still restarts zone preview using the selected task's algorithm-owned regions.
- [ ] Preserve current task-aware zone preview restart behavior.

### Task 7: Verify End-to-End

**Files:**
- No code changes required if green

- [ ] Run focused backend tests for task service and metadata sync.
- [ ] Run frontend build: `npm run build`.
- [ ] Smoke-check that opening region config, saving a region, and refreshing zone preview still compiles and follows the new API path.
