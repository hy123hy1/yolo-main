# Metric ID Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert `task_algorithm_metrics` and `task_algorithm_metric_history` to numeric auto-increment primary keys while preserving realtime upsert semantics through a new `scope_key`.

**Architecture:** Keep the current metric business meaning unchanged, but separate surrogate primary keys from business uniqueness. Realtime metrics will upsert by `(task_id, algorithm_id, metric_type, scope_key)`, and history rows will become append-only records with numeric IDs.

**Tech Stack:** Python, SQLAlchemy ORM, MySQL/SQLite compatibility logic, unittest, metadata DB self-healing startup path

---

### Task 1: Lock behavior with failing tests

**Files:**
- Modify: `backend/tests/test_task_service.py`
- Modify: `backend/tests/test_analysis_service.py`
- Modify: `backend/tests/test_metadata_db_sync.py`
- Create: `backend/tests/test_metric_service.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_upsert_task_algorithm_metric_uses_business_key_not_string_id():
    result1 = metric_service.upsert_task_algorithm_metric_sync(
        task_id="task_1",
        algorithm_id="algo_1",
        channel_id="ch_1",
        model_id="model_1",
        metric_type="person_count",
        metric_value=1,
        region_id=None,
        region_name=None,
        raw_json={"class_counts": {"person": 1}},
    )
    result2 = metric_service.upsert_task_algorithm_metric_sync(
        task_id="task_1",
        algorithm_id="algo_1",
        channel_id="ch_1",
        model_id="model_1",
        metric_type="person_count",
        metric_value=2,
        region_id=None,
        region_name=None,
        raw_json={"class_counts": {"person": 2}},
    )
    assert result1["id"] == result2["id"]
    assert result2["metric_value"] == 2


def test_history_rows_get_numeric_ids_and_append():
    first = metric_service.upsert_task_algorithm_metric_sync(
        task_id="task_1",
        algorithm_id="algo_1",
        channel_id="ch_1",
        model_id="model_1",
        metric_type="person_count",
        metric_value=1,
        region_id="region_1",
        region_name="Zone A",
        raw_json={"class_counts": {"person": 1}},
    )
    second = metric_service._add_history_sample(
        session,
        metric_id="legacy_unused",
        task_id="task_1",
        algorithm_id="algo_1",
        channel_id="ch_1",
        model_id="model_1",
        metric_type="person_count",
        metric_value=2,
        region_id="region_1",
        region_name="Zone A",
        raw_json={"class_counts": {"person": 2}},
        now=datetime.now() + timedelta(seconds=61),
    )
    assert first["id"] != ""
    assert second is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest backend.tests.test_metric_service`

Expected: FAIL because current schema and service still depend on string metric IDs.

- [ ] **Step 3: Add minimal test scaffolding**

```python
class MetricServiceTest(unittest.TestCase):
    def setUp(self):
        ...
```

- [ ] **Step 4: Run the focused tests again**

Run: `python -m unittest backend.tests.test_metric_service`

Expected: FAIL with assertion/schema mismatch rather than import/setup errors.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_metric_service.py
git commit -m "test: cover metric id redesign"
```

### Task 2: Change schema and migration path

**Files:**
- Modify: `backend/src/db/models.py`
- Modify: `backend/src/services/metadata_db_sync.py`
- Test: `backend/tests/test_metadata_db_sync.py`

- [ ] **Step 1: Write the failing migration test**

```python
def test_metric_tables_migrate_to_numeric_ids_and_scope_key():
    metadata_db_sync.initialize_metadata_database()
    columns = inspect(engine).get_columns("task_algorithm_metrics")
    assert any(column["name"] == "scope_key" for column in columns)
    assert any(column["name"] == "id" for column in columns)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest backend.tests.test_metadata_db_sync`

Expected: FAIL because `scope_key` and numeric metric IDs are not yet provisioned.

- [ ] **Step 3: Implement minimal schema updates**

```python
class TaskAlgorithmMetricMeta(Base):
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_key: Mapped[str] = mapped_column(String(64), index=True)


class TaskAlgorithmMetricHistoryMeta(Base):
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
```

- [ ] **Step 4: Implement migration/rebuild logic**

```python
def _ensure_metric_tables_v2(session: Session) -> None:
    # create temp table
    # copy existing rows with scope_key = region_id or "global"
    # swap tables
    # recreate indexes
```

- [ ] **Step 5: Run migration tests**

Run: `python -m unittest backend.tests.test_metadata_db_sync`

Expected: PASS

### Task 3: Switch MetricService to business-key upsert

**Files:**
- Modify: `backend/src/services/metric_service.py`
- Test: `backend/tests/test_metric_service.py`

- [ ] **Step 1: Write the failing behavior test for `scope_key`**

```python
def test_global_and_region_metrics_use_different_scope_keys():
    global_row = metric_service.upsert_task_algorithm_metric_sync(
        task_id="task_1",
        algorithm_id="algo_1",
        channel_id="ch_1",
        model_id="model_1",
        metric_type="person_count",
        metric_value=1,
        region_id=None,
        region_name=None,
        raw_json={},
    )
    region_row = metric_service.upsert_task_algorithm_metric_sync(
        task_id="task_1",
        algorithm_id="algo_1",
        channel_id="ch_1",
        model_id="model_1",
        metric_type="person_count",
        metric_value=1,
        region_id="region_1",
        region_name="Zone A",
        raw_json={},
    )
    assert global_row["id"] != region_row["id"]
```

- [ ] **Step 2: Run the focused metric tests**

Run: `python -m unittest backend.tests.test_metric_service`

Expected: FAIL until service uses `scope_key` queries and numeric IDs.

- [ ] **Step 3: Implement minimal service change**

```python
scope_key = region_id or "global"
row = (
    session.query(TaskAlgorithmMetricMeta)
    .filter(TaskAlgorithmMetricMeta.task_id == task_id)
    .filter(TaskAlgorithmMetricMeta.algorithm_id == algorithm_id)
    .filter(TaskAlgorithmMetricMeta.metric_type == metric_type)
    .filter(TaskAlgorithmMetricMeta.scope_key == scope_key)
    .one_or_none()
)
```

- [ ] **Step 4: Remove string history ID generation**

```python
session.add(
    TaskAlgorithmMetricHistoryMeta(
        task_id=task_id,
        algorithm_id=algorithm_id,
        ...
    )
)
```

- [ ] **Step 5: Run metric tests**

Run: `python -m unittest backend.tests.test_metric_service`

Expected: PASS

### Task 4: Verify integration paths

**Files:**
- Modify: `backend/tests/test_analysis_service.py`
- Modify: `backend/tests/test_task_service.py`
- Test: `backend/tests/test_metric_service.py`

- [ ] **Step 1: Add regression tests around task status / analysis integration**

```python
def test_task_status_reads_numeric_metric_ids_without_behavior_change():
    status = asyncio.run(task_service.get_task_status(task_id))
    assert status["metrics"][0]["metric_value"] == 3
    assert status["metrics"][0]["raw_json"]["class_counts"] == {"person": 3}
```

- [ ] **Step 2: Run the affected test suites**

Run: `python -m unittest backend.tests.test_metric_service backend.tests.test_task_service backend.tests.test_analysis_service`

Expected: FAIL only where metric ID assumptions remain.

- [ ] **Step 3: Make the minimal compatibility fixes**

```python
return {
    "id": row.id,
    "task_id": row.task_id,
    ...
}
```

- [ ] **Step 4: Run the targeted suites again**

Run: `python -m unittest backend.tests.test_metric_service backend.tests.test_task_service backend.tests.test_analysis_service`

Expected: PASS

- [ ] **Step 5: Run final verification**

Run: `python -m unittest backend.tests.test_metric_service backend.tests.test_metadata_db_sync backend.tests.test_task_service backend.tests.test_analysis_service`

Expected: PASS
