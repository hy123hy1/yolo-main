# Task Center Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-phase task center backend that introduces task-based AI configuration, persistence, and metadata sync without replacing the current channel-based runtime.

**Architecture:** Add a new `tasks` resource alongside existing `channels`, persisted as JSON documents under `backend/data/tasks`. Expose CRUD/start/stop/status APIs through a dedicated task service and route module, then mirror task metadata into SQLite using the same sync pipeline already used by channels, events, and models.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2, JSON document storage, SQLite metadata database, Python unittest.

---

### Task 1: Add failing tests for task persistence and metadata sync

**Files:**
- Create: `backend/tests/test_task_service.py`

- [ ] **Step 1: Write the failing test**

```python
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from src.core.settings import settings
from src.services.task_service import TaskService


class TaskServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.temp_dir.name) / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.original_tasks_dir = settings.TASKS_DIR
        settings.TASKS_DIR = self.tasks_dir
        self.service = TaskService()

    async def asyncTearDown(self):
        settings.TASKS_DIR = self.original_tasks_dir
        self.temp_dir.cleanup()

    async def test_create_task_persists_default_runtime_and_algorithms(self):
        task = await self.service.create_task(
            {
                "name": "人员检测任务",
                "source_url": "rtsp://example/live/1",
                "frame_interval": 5,
                "algorithms": [
                    {
                        "model_id": "model_demo",
                        "labels": ["person"],
                    }
                ],
            }
        )

        self.assertEqual(task["name"], "人员检测任务")
        self.assertEqual(task["status"], "draft")
        self.assertEqual(task["runtime"]["status"], "stopped")
        self.assertEqual(task["algorithms"][0]["model_id"], "model_demo")
        self.assertTrue(task["algorithms"][0]["need_snapshot"])
        self.assertTrue((self.tasks_dir / f"{task['id']}.json").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest backend.tests.test_task_service -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` because `task_service` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create a new task service with:
- `create_task`
- `get_task`
- `list_tasks`
- `update_task`
- `delete_task`
- `start_task`
- `stop_task`
- `get_task_status`

Use JSON storage and generate default algorithm/runtime structure.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest backend.tests.test_task_service -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_task_service.py backend/src/services/task_service.py
git commit -m "feat: add task service persistence"
```

### Task 2: Add task API routes and app registration

**Files:**
- Create: `backend/src/api/routes/tasks.py`
- Modify: `backend/src/main.py`

- [ ] **Step 1: Write the failing route test**

Add a route-level test that imports the FastAPI app and verifies `GET /tasks` returns a JSON object with an `items` array.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest backend.tests.test_task_service -v`
Expected: FAIL because `/tasks` router is not registered yet.

- [ ] **Step 3: Write minimal implementation**

Create route models and endpoints:
- `GET /tasks`
- `POST /tasks`
- `GET /tasks/{task_id}`
- `PUT /tasks/{task_id}`
- `DELETE /tasks/{task_id}`
- `POST /tasks/{task_id}/start`
- `POST /tasks/{task_id}/stop`
- `GET /tasks/{task_id}/status`

Register the router in `main.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest backend.tests.test_task_service -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/tasks.py backend/src/main.py backend/tests/test_task_service.py
git commit -m "feat: expose task center api routes"
```

### Task 3: Sync task metadata into SQLite

**Files:**
- Modify: `backend/src/core/settings.py`
- Modify: `backend/src/db/models.py`
- Modify: `backend/src/services/metadata_db_sync.py`

- [ ] **Step 1: Write the failing sync test**

Extend the test module to call `sync_json_document(...)` with a task JSON file and verify the task table row can be queried afterward.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest backend.tests.test_task_service -v`
Expected: FAIL because `TaskMeta` and task sync handling do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add:
- `TASKS_DIR` setting and directory initialization
- `TaskMeta` SQLAlchemy model
- task branch in `_sync_by_path`, `delete_json_document`, and `sync_all_metadata_from_files`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest backend.tests.test_task_service -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/core/settings.py backend/src/db/models.py backend/src/services/metadata_db_sync.py backend/tests/test_task_service.py
git commit -m "feat: sync task metadata into sqlite"
```

### Task 4: Final verification

**Files:**
- No additional files

- [ ] **Step 1: Run the focused backend tests**

Run: `python -m unittest backend.tests.test_task_service -v`
Expected: PASS

- [ ] **Step 2: Run a syntax/import check**

Run: `python -m compileall backend/src`
Expected: completes without syntax errors

- [ ] **Step 3: Smoke-check created routes**

Run: `python -c "from backend.src.main import app; print([r.path for r in app.routes if 'tasks' in getattr(r, 'path', '')])"`
Expected: prints the task routes list

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-06-04-task-center-phase1.md
git commit -m "docs: add task center phase 1 implementation plan"
```
