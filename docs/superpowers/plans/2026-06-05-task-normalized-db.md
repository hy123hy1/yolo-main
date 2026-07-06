# 任务管理数据库规范化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce normalized task metadata tables for task algorithms, task regions, and task-to-region links while preserving the current JSON task format and runtime behavior.

**Architecture:** Keep `data/tasks/*.json` as the phase-1 source of truth, but expand metadata sync so each task JSON document is mirrored into `tasks`, `task_algorithms`, `task_regions`, and `task_algorithm_regions`. Extend the SQLAlchemy models first, then add failing tests for normalized sync behavior, then implement deterministic “delete and rebuild per task” synchronization in `metadata_db_sync`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, SQLite metadata database, JSON document storage, Python unittest.

---

### Task 1: Add failing tests for normalized task metadata sync

**Files:**
- Modify: `backend/tests/test_task_service.py`
- Test: `backend/tests/test_task_service.py`

- [ ] **Step 1: Write the failing test**

Add imports for the normalized task metadata models and add a new test that creates a task with multiple algorithms, shared task regions, and algorithm-region links, then queries the metadata database to assert the normalized rows exist.

```python
from src.db.models import (
    Base,
    TaskAlgorithmMeta,
    TaskAlgorithmRegionMeta,
    TaskMeta,
    TaskRegionMeta,
)

    async def test_sync_task_metadata_into_normalized_tables(self):
        task = await self.service.create_task(
            {
                "name": "normalized-sync-task",
                "source_url": "rtsp://example/live/10",
                "channel_id": "ch_normalized",
                "frame_interval": 6,
                "regions": [
                    {
                        "id": "region_gate",
                        "name": "gate-zone",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    },
                    {
                        "id": "region_yard",
                        "name": "yard-zone",
                        "coordinates": [[120, 0], [240, 0], [240, 120], [120, 120]],
                    },
                ],
                "algorithms": [
                    {
                        "id": "algo_person",
                        "model_id": "model_person",
                        "labels": ["person"],
                        "confidence": 0.4,
                        "need_overlay": True,
                        "region_ids": ["region_gate", "region_yard"],
                    },
                    {
                        "id": "algo_helmet",
                        "model_id": "model_helmet",
                        "labels": ["helmet"],
                        "confidence": 0.55,
                        "need_overlay": True,
                        "region_ids": ["region_gate"],
                    },
                ],
            }
        )

        with self.session_local() as session:
            task_row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one()
            algorithm_rows = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.task_id == task["id"])
            ).scalars().all()
            region_rows = session.execute(
                select(TaskRegionMeta).where(TaskRegionMeta.task_id == task["id"])
            ).scalars().all()
            link_rows = session.execute(
                select(TaskAlgorithmRegionMeta).where(TaskAlgorithmRegionMeta.task_id == task["id"])
            ).scalars().all()

        self.assertEqual(task_row.algorithm_count, 2)
        self.assertEqual(task_row.region_count, 2)
        self.assertEqual(len(algorithm_rows), 2)
        self.assertEqual(len(region_rows), 2)
        self.assertEqual(len(link_rows), 3)
        self.assertEqual(
            sorted((row.algorithm_id, row.region_id) for row in link_rows),
            [
                ("algo_helmet", "region_gate"),
                ("algo_person", "region_gate"),
                ("algo_person", "region_yard"),
            ],
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec yolo-backend python -m py_compile /app/src/db/models.py /app/src/services/metadata_db_sync.py /app/src/services/task_service.py`
Expected: PASS for syntax only, because the container does not contain the test tree.

Run locally in a Python environment with dependencies:
`python -m unittest backend.tests.test_task_service.TaskServiceTest.test_sync_task_metadata_into_normalized_tables -v`
Expected: FAIL because `TaskAlgorithmMeta`, `TaskRegionMeta`, and `TaskAlgorithmRegionMeta` do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Do not implement logic yet. Only prepare the test and model import targets so the failure is specific to missing normalized models.

- [ ] **Step 4: Run test to verify it fails for the expected reason**

Run: `python -m unittest backend.tests.test_task_service.TaskServiceTest.test_sync_task_metadata_into_normalized_tables -v`
Expected: FAIL with import or model lookup failure referencing the missing normalized task metadata models.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_task_service.py
git commit -m "test: define normalized task metadata sync expectations"
```

### Task 2: Add normalized SQLAlchemy models for task algorithms and regions

**Files:**
- Modify: `backend/src/db/models.py`
- Test: `backend/tests/test_task_service.py`

- [ ] **Step 1: Write the failing schema expectation**

Extend the normalized sync test assertions to verify structured columns for algorithms and regions, not just row counts.

```python
        person_row = next(row for row in algorithm_rows if row.id == "algo_person")
        helmet_row = next(row for row in algorithm_rows if row.id == "algo_helmet")
        gate_row = next(row for row in region_rows if row.id == "region_gate")

        self.assertEqual(person_row.model_id, "model_person")
        self.assertEqual(person_row.labels_json, ["person"])
        self.assertAlmostEqual(person_row.confidence, 0.4)
        self.assertTrue(person_row.need_overlay)
        self.assertEqual(helmet_row.model_id, "model_helmet")
        self.assertEqual(gate_row.name, "gate-zone")
        self.assertEqual(gate_row.coordinates_json[0], [0, 0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest backend.tests.test_task_service.TaskServiceTest.test_sync_task_metadata_into_normalized_tables -v`
Expected: FAIL because the new models and columns do not exist.

- [ ] **Step 3: Write minimal implementation**

Add the new SQLAlchemy models and extend `TaskMeta`.

```python
class TaskMeta(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(64), index=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), index=True)
    frame_interval: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(64), index=True)
    algorithm_count: Mapped[int | None] = mapped_column(Integer)
    region_count: Mapped[int | None] = mapped_column(Integer)
    is_running: Mapped[bool | None] = mapped_column(Boolean, index=True)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    stopped_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    last_analysis_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    last_event_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class TaskAlgorithmMeta(Base):
    __tablename__ = "task_algorithms"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    algorithm_type: Mapped[str | None] = mapped_column(String(64), index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), index=True)
    enabled: Mapped[bool | None] = mapped_column(Boolean, index=True)
    labels_json: Mapped[list | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    alert_interval: Mapped[int | None] = mapped_column(Integer)
    need_snapshot: Mapped[bool | None] = mapped_column(Boolean)
    need_video_clip: Mapped[bool | None] = mapped_column(Boolean)
    video_before_seconds: Mapped[int | None] = mapped_column(Integer)
    video_after_seconds: Mapped[int | None] = mapped_column(Integer)
    need_overlay: Mapped[bool | None] = mapped_column(Boolean, index=True)
    overlay_color: Mapped[str | None] = mapped_column(String(32))
    sort_order: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class TaskRegionMeta(Base):
    __tablename__ = "task_regions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(64), index=True)
    color: Mapped[str | None] = mapped_column(String(32))
    coordinates_json: Mapped[list | None] = mapped_column(JSON)
    enabled: Mapped[bool | None] = mapped_column(Boolean, index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)


class TaskAlgorithmRegionMeta(Base):
    __tablename__ = "task_algorithm_regions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    algorithm_id: Mapped[str] = mapped_column(String(64), ForeignKey("task_algorithms.id", ondelete="CASCADE"), index=True)
    region_id: Mapped[str] = mapped_column(String(64), ForeignKey("task_regions.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
```

- [ ] **Step 4: Run test to verify it still fails later in sync logic**

Run: `python -m unittest backend.tests.test_task_service.TaskServiceTest.test_sync_task_metadata_into_normalized_tables -v`
Expected: FAIL because the sync layer still only writes `tasks`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/tests/test_task_service.py
git commit -m "feat: add normalized task metadata models"
```

### Task 3: Implement normalized task metadata sync

**Files:**
- Modify: `backend/src/services/metadata_db_sync.py`
- Test: `backend/tests/test_task_service.py`

- [ ] **Step 1: Write the failing rebuild test**

Add a second test that updates the same task and verifies removed algorithms, removed regions, and removed links disappear from the normalized tables after sync.

```python
    async def test_task_sync_rebuilds_normalized_children_after_update(self):
        task = await self.service.create_task(
            {
                "name": "rebuild-sync-task",
                "source_url": "rtsp://example/live/11",
                "regions": [
                    {
                        "id": "region_old",
                        "name": "old-zone",
                        "coordinates": [[0, 0], [10, 0], [10, 10], [0, 10]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_old",
                        "model_id": "model_old",
                        "region_ids": ["region_old"],
                    }
                ],
            }
        )

        await self.service.update_task(
            task["id"],
            {
                "name": "rebuild-sync-task-v2",
                "source_url": "rtsp://example/live/11",
                "regions": [
                    {
                        "id": "region_new",
                        "name": "new-zone",
                        "coordinates": [[20, 20], [40, 20], [40, 40], [20, 40]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_new",
                        "model_id": "model_new",
                        "region_ids": ["region_new"],
                    }
                ],
            },
        )

        with self.session_local() as session:
            algorithm_ids = session.execute(
                select(TaskAlgorithmMeta.id).where(TaskAlgorithmMeta.task_id == task["id"])
            ).scalars().all()
            region_ids = session.execute(
                select(TaskRegionMeta.id).where(TaskRegionMeta.task_id == task["id"])
            ).scalars().all()
            link_pairs = session.execute(
                select(TaskAlgorithmRegionMeta.algorithm_id, TaskAlgorithmRegionMeta.region_id).where(
                    TaskAlgorithmRegionMeta.task_id == task["id"]
                )
            ).all()

        self.assertEqual(algorithm_ids, ["algo_new"])
        self.assertEqual(region_ids, ["region_new"])
        self.assertEqual(link_pairs, [("algo_new", "region_new")])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest backend.tests.test_task_service.TaskServiceTest.test_task_sync_rebuilds_normalized_children_after_update -v`
Expected: FAIL because the sync layer does not delete and rebuild normalized child rows yet.

- [ ] **Step 3: Write minimal implementation**

Import the new models and replace `_sync_task()` with a deterministic full rebuild.

```python
from src.db.models import (
    Base,
    ChannelAIConfigMeta,
    ChannelMeta,
    ChannelZoneMeta,
    DeviceMeta,
    EventMeta,
    MediaNodeMeta,
    ModelMetaRecord,
    RecordingMeta,
    TaskAlgorithmMeta,
    TaskAlgorithmRegionMeta,
    TaskMeta,
    TaskRegionMeta,
)


def _sync_task(session: Session, payload: dict) -> None:
    runtime = payload.get("runtime") or {}
    algorithms = list(payload.get("algorithms") or [])
    regions = list(payload.get("regions") or [])

    _upsert(
        session,
        TaskMeta(
            id=payload["id"],
            name=payload.get("name"),
            source_url=payload.get("source_url"),
            source_type=payload.get("source_type"),
            channel_id=payload.get("channel_id"),
            frame_interval=payload.get("frame_interval"),
            status=payload.get("status"),
            algorithm_count=len(algorithms),
            region_count=len(regions),
            is_running=runtime.get("is_running"),
            started_at=_parse_dt(runtime.get("started_at")),
            stopped_at=_parse_dt(runtime.get("stopped_at")),
            last_analysis_at=_parse_dt(runtime.get("last_analysis_at")),
            last_event_at=_parse_dt(runtime.get("last_event_at")),
            last_error=runtime.get("last_error"),
            created_at=_parse_dt(payload.get("created_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
            raw_json=copy.deepcopy(payload),
        ),
    )

    session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == payload["id"]).delete()
    session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == payload["id"]).delete()
    session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == payload["id"]).delete()

    for index, algorithm in enumerate(algorithms):
        algorithm_id = algorithm.get("id")
        if not algorithm_id:
            continue
        session.add(
            TaskAlgorithmMeta(
                id=algorithm_id,
                task_id=payload["id"],
                algorithm_type=algorithm.get("algorithm_type"),
                model_id=algorithm.get("model_id"),
                enabled=algorithm.get("enabled"),
                labels_json=copy.deepcopy(algorithm.get("labels") or []),
                confidence=algorithm.get("confidence"),
                alert_interval=algorithm.get("alert_interval"),
                need_snapshot=algorithm.get("need_snapshot"),
                need_video_clip=algorithm.get("need_video_clip"),
                video_before_seconds=algorithm.get("video_before_seconds"),
                video_after_seconds=algorithm.get("video_after_seconds"),
                need_overlay=algorithm.get("need_overlay"),
                overlay_color=algorithm.get("overlay_color"),
                sort_order=index,
                created_at=_parse_dt(algorithm.get("created_at")) or _parse_dt(payload.get("created_at")),
                updated_at=_parse_dt(payload.get("updated_at")),
                raw_json=copy.deepcopy(algorithm),
            )
        )

    for region in regions:
        region_id = region.get("id")
        if not region_id:
            continue
        session.add(
            TaskRegionMeta(
                id=region_id,
                task_id=payload["id"],
                name=region.get("name"),
                type=region.get("type"),
                color=region.get("color"),
                coordinates_json=copy.deepcopy(region.get("coordinates") or []),
                enabled=region.get("enabled"),
                created_at=_parse_dt(region.get("created_at")) or _parse_dt(payload.get("created_at")),
                updated_at=_parse_dt(payload.get("updated_at")),
                raw_json=copy.deepcopy(region),
            )
        )

    link_index = 0
    valid_region_ids = {region.get("id") for region in regions if region.get("id")}
    for algorithm in algorithms:
        algorithm_id = algorithm.get("id")
        if not algorithm_id:
            continue
        for region_id in list(algorithm.get("region_ids") or []):
            if region_id not in valid_region_ids:
                continue
            link_index += 1
            session.add(
                TaskAlgorithmRegionMeta(
                    id=f"{payload['id']}_{algorithm_id}_{region_id}_{link_index}",
                    task_id=payload["id"],
                    algorithm_id=algorithm_id,
                    region_id=region_id,
                    created_at=_parse_dt(payload.get("updated_at")) or _parse_dt(payload.get("created_at")),
                )
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest backend.tests.test_task_service.TaskServiceTest.test_sync_task_metadata_into_normalized_tables backend.tests.test_task_service.TaskServiceTest.test_task_sync_rebuilds_normalized_children_after_update -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/metadata_db_sync.py backend/tests/test_task_service.py
git commit -m "feat: sync normalized task metadata tables"
```

### Task 4: Ensure task deletion cleans normalized tables

**Files:**
- Modify: `backend/src/services/metadata_db_sync.py`
- Modify: `backend/tests/test_task_service.py`
- Test: `backend/tests/test_task_service.py`

- [ ] **Step 1: Write the failing delete cleanup test**

Add a test that creates a task, deletes it through `TaskService.delete_task()`, and verifies all four normalized tables are empty for that task.

```python
    async def test_delete_task_cleans_normalized_tables(self):
        task = await self.service.create_task(
            {
                "name": "delete-cleanup-task",
                "source_url": "rtsp://example/live/12",
                "regions": [
                    {
                        "id": "region_cleanup",
                        "name": "cleanup-zone",
                        "coordinates": [[0, 0], [50, 0], [50, 50], [0, 50]],
                    }
                ],
                "algorithms": [
                    {
                        "id": "algo_cleanup",
                        "model_id": "model_cleanup",
                        "region_ids": ["region_cleanup"],
                    }
                ],
            }
        )

        await self.service.delete_task(task["id"])

        with self.session_local() as session:
            task_row = session.execute(select(TaskMeta).where(TaskMeta.id == task["id"])).scalar_one_or_none()
            algorithm_rows = session.execute(
                select(TaskAlgorithmMeta).where(TaskAlgorithmMeta.task_id == task["id"])
            ).scalars().all()
            region_rows = session.execute(
                select(TaskRegionMeta).where(TaskRegionMeta.task_id == task["id"])
            ).scalars().all()
            link_rows = session.execute(
                select(TaskAlgorithmRegionMeta).where(TaskAlgorithmRegionMeta.task_id == task["id"])
            ).scalars().all()

        self.assertIsNone(task_row)
        self.assertEqual(algorithm_rows, [])
        self.assertEqual(region_rows, [])
        self.assertEqual(link_rows, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest backend.tests.test_task_service.TaskServiceTest.test_delete_task_cleans_normalized_tables -v`
Expected: FAIL because `delete_json_document()` only deletes `TaskMeta`.

- [ ] **Step 3: Write minimal implementation**

Extend the task branch in `delete_json_document()`.

```python
        elif path.parent == settings.TASKS_DIR:
            session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == item_id).delete()
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == item_id).delete()
            session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == item_id).delete()
            session.query(TaskMeta).filter(TaskMeta.id == item_id).delete()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest backend.tests.test_task_service.TaskServiceTest.test_delete_task_cleans_normalized_tables -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/metadata_db_sync.py backend/tests/test_task_service.py
git commit -m "fix: clean normalized task metadata on delete"
```

### Task 5: Final verification and documentation handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-06-05-task-normalized-db.md`

- [ ] **Step 1: Run focused backend syntax verification**

Run: `docker exec yolo-backend python -m py_compile /app/src/db/models.py /app/src/services/metadata_db_sync.py /app/src/services/task_service.py`
Expected: command completes without syntax errors

- [ ] **Step 2: Run front-end build to ensure no unrelated regression**

Run: `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command npm.cmd run build`
Working directory: `frontend`
Expected: build completes successfully

- [ ] **Step 3: Run task metadata smoke import**

Run:
`docker exec yolo-backend sh -lc "cd /app && python - <<'PY'\nfrom src.db.models import TaskMeta, TaskAlgorithmMeta, TaskRegionMeta, TaskAlgorithmRegionMeta\nprint('normalized-task-models-ok')\nPY"`
Expected: prints `normalized-task-models-ok`

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-06-05-task-normalized-db.md
git commit -m "docs: add normalized task metadata plan"
```

