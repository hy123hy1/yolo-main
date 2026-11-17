# Task Metric History Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留 `task_algorithm_metric_history` 历史指标行及其原始 `task_id`，使其不因视频 AI 任务删除或孤儿任务清理而删除。

**Architecture:** 历史指标表改为独立历史表，`task_id` 保留为普通索引字段，不再依赖 `tasks` 外键；实时指标表继续通过任务外键和现有任务删除逻辑清理。现有 MySQL 数据库通过一次性 SQL 删除历史表外键，模型和完整建库 SQL 同步更新。

**Tech Stack:** Python 3.11, SQLAlchemy, pytest/unittest, MySQL 8/InnoDB, SQLite test database.

---

### Task 1: Add failing retention tests

**Files:**
- Modify: `backend/tests/test_metric_service.py`
- Modify: `backend/tests/test_metadata_db_sync.py`

- [ ] **Step 1: Add a database-level lifecycle test**

In `test_metric_service.py`, enable SQLite foreign keys in the test engine, import `event`, `TaskMeta`, and `TaskAlgorithmMeta`, then add a test that inserts one task, one algorithm, one realtime metric, and one history metric. Delete the task and assert the realtime row is gone while the history row remains with the original ID:

```python
def test_task_delete_keeps_history_metric_but_removes_realtime_metric(self):
    with self.session_local() as session:
        session.add(TaskMeta(id="task_delete_history", name="history task"))
        session.add(
            TaskAlgorithmMeta(
                id="algo_delete_history",
                task_id="task_delete_history",
                algorithm_type="detection",
            )
        )
        session.add(
            TaskAlgorithmMetricMeta(
                task_id="task_delete_history",
                algorithm_id="algo_delete_history",
                metric_type="person_count",
                metric_value=3,
                scope_key="global",
            )
        )
        session.add(
            TaskAlgorithmMetricHistoryMeta(
                task_id="task_delete_history",
                algorithm_id="algo_delete_history",
                metric_type="person_count",
                metric_value=3,
            )
        )
        session.commit()

        session.query(TaskMeta).filter(TaskMeta.id == "task_delete_history").delete()
        session.commit()

        assert session.query(TaskAlgorithmMetricMeta).filter_by(
            task_id="task_delete_history"
        ).count() == 0
        history = session.query(TaskAlgorithmMetricHistoryMeta).filter_by(
            task_id="task_delete_history"
        ).one()
        assert history.task_id == "task_delete_history"
```

- [ ] **Step 2: Add an orphan-cleanup retention test**

In `test_metadata_db_sync.py`, import `TaskAlgorithmMetricHistoryMeta`. Add a test that creates a task pointing to a missing channel, inserts history, calls `_cleanup_orphan_tasks`, and asserts the task is removed while history remains:

```python
def test_cleanup_orphan_task_keeps_metric_history(self):
    with self.session_local() as session:
        session.add(TaskMeta(id="orphan_history_task", channel_id="missing-channel"))
        session.add(
            TaskAlgorithmMetricHistoryMeta(
                task_id="orphan_history_task",
                algorithm_id="orphan-history-algorithm",
                metric_type="person_count",
                metric_value=7,
            )
        )
        session.commit()

        metadata_db_sync._cleanup_orphan_tasks(session)
        session.commit()

        assert session.get(TaskMeta, "orphan_history_task") is None
        history = session.query(TaskAlgorithmMetricHistoryMeta).filter_by(
            task_id="orphan_history_task"
        ).one()
        assert history.metric_value == 7
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest -q backend/tests/test_metric_service.py::MetricServiceTest::test_task_delete_keeps_history_metric_but_removes_realtime_metric backend/tests/test_metadata_db_sync.py::MetadataDbSyncTest::test_cleanup_orphan_task_keeps_metric_history
```

Expected: the tests fail because the model still creates a cascading history foreign key and orphan cleanup explicitly deletes history.

### Task 2: Decouple the history model and cleanup paths

**Files:**
- Modify: `backend/src/db/models.py:254-268`
- Modify: `backend/src/services/metadata_db_sync.py:321-328`
- Modify: `backend/src/services/metadata_db_sync.py:971-978`

- [ ] **Step 1: Remove the history foreign key**

Change only the history model field from:

```python
task_id: Mapped[str] = mapped_column(
    String(64), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
)
```

to:

```python
task_id: Mapped[str] = mapped_column(String(64), index=True)
```

Keep the field non-null and keep the index.

- [ ] **Step 2: Stop deleting history during orphan cleanup**

Remove only this statement from `_cleanup_orphan_tasks`:

```python
session.query(TaskAlgorithmMetricHistoryMeta).filter(
    TaskAlgorithmMetricHistoryMeta.task_id == task_id
).delete()
```

Leave realtime metric and task configuration cleanup unchanged.

- [ ] **Step 3: Stop deleting history during task JSON cleanup**

Remove only the equivalent history delete from the `settings.TASKS_DIR` branch of `delete_json_document`. Keep deletion of `TaskAlgorithmMetricMeta`, algorithm/region rows, and `TaskMeta` unchanged.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the two tests from Task 1. Expected: both pass, proving task deletion preserves history and realtime metrics still follow the old lifecycle.

### Task 3: Add the MySQL migration and update the full schema dump

**Files:**
- Create: `backend/migrations/20260810_task_metric_history_retention.sql`
- Modify: `backend/migrations/sp-ai-yolo.sql:3617`

- [ ] **Step 1: Add an idempotent MySQL migration**

Create a migration that finds and drops only the foreign key from `task_algorithm_metric_history.task_id` to `tasks.id`:

```sql
SET @history_fk = (
    SELECT CONSTRAINT_NAME
    FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'task_algorithm_metric_history'
      AND COLUMN_NAME = 'task_id'
      AND REFERENCED_TABLE_NAME = 'tasks'
    LIMIT 1
);
SET @drop_history_fk = IF(
    @history_fk IS NULL,
    'SELECT 1',
    CONCAT(
        'ALTER TABLE task_algorithm_metric_history DROP FOREIGN KEY `',
        @history_fk,
        '`'
    )
);
PREPARE stmt FROM @drop_history_fk;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
```

- [ ] **Step 2: Update the full schema dump**

Remove the `task_algorithm_metric_history_ibfk_1` constraint from `backend/migrations/sp-ai-yolo.sql`, leaving the table, `task_id` column, and its index intact.

- [ ] **Step 3: Verify schema metadata locally**

Run:

```powershell
python -m pytest -q backend/tests/test_metadata_db_sync.py backend/tests/test_metric_service.py
```

Expected: all tests pass; the SQL migration contains the conditional foreign-key removal and the dump no longer recreates the history-to-task cascade.

### Task 4: Regression verification

**Files:**
- No additional files.

- [ ] **Step 1: Run task and metadata regression tests**

```powershell
python -m pytest -q backend/tests/test_task_service.py backend/tests/test_metadata_db_sync.py backend/tests/test_metric_service.py
```

- [ ] **Step 2: Inspect the final diff**

```powershell
git diff -- backend/src/db/models.py backend/src/services/metadata_db_sync.py backend/tests/test_metric_service.py backend/tests/test_metadata_db_sync.py backend/migrations/20260810_task_metric_history_retention.sql backend/migrations/sp-ai-yolo.sql
```

Confirm no deletion remains for `TaskAlgorithmMetricHistoryMeta` in task deletion or orphan cleanup paths, and no unrelated dirty files are staged.

- [ ] **Step 3: Report deployment action**

Tell the deployer to execute `backend/migrations/20260810_task_metric_history_retention.sql` once against the active MySQL database before restarting the backend. Do not claim the production schema is changed unless that SQL is executed successfully.
