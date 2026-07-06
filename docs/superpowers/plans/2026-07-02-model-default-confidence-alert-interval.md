# Model Default Confidence Alert Interval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `confidence` and `alert_interval` as model-level default values, expose them through model APIs, and automatically hydrate them into `task_algorithms` when a task adds an algorithm.

**Architecture:** Keep `models` as the source of default values and `task_algorithms` as the source of runtime values. The service layer will normalize missing model defaults to `0.25` and `60`, surface those values in model/task candidate APIs, and only materialize them into task algorithms at creation/update time when the request does not explicitly override them.

**Tech Stack:** FastAPI, SQLAlchemy ORM, Pydantic, unittest, SQLite/MySQL-compatible metadata schema

---

## File Structure

- `backend/src/db/models.py`
  Add `confidence` and `alert_interval` columns to `ModelMetaRecord`.
- `backend/src/services/model_service.py`
  Normalize model default values when reading/writing metadata rows and expose them in `list_models`, `get_model_summary`, `get_model`, and `update_model`.
- `backend/src/api/routes/models.py`
  Extend the update request schema so model management can edit `confidence` and `alert_interval`.
- `backend/src/services/task_service.py`
  Fill missing algorithm `confidence` and `alert_interval` from the selected model defaults, and include those defaults in `available-models`.
- `backend/tests/test_model_service.py`
  Add regression tests for model default field persistence and API-facing read behavior.
- `backend/tests/test_task_service.py`
  Add regression tests covering algorithm creation with inherited defaults and explicit override behavior.

### Task 1: Add Model Default Columns to Metadata Model

**Files:**
- Modify: `backend/src/db/models.py`
- Test: `backend/tests/test_model_service.py`

- [ ] **Step 1: Write the failing test for model metadata defaults**

Add a test in `backend/tests/test_model_service.py` that inserts a `ModelMetaRecord` row with `confidence=None` and `alert_interval=None`, then asserts `ModelService.list_models()` returns `0.25` and `60`.

```python
    async def test_list_models_fills_missing_default_confidence_and_alert_interval(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="dbdefaults",
                    name="defaults-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=None,
                    alert_interval=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "dbdefaults",
                        "name": "defaults-model",
                        "classes": ["person"],
                    },
                )
            )
            session.commit()

        service = ModelService()
        result = await service.list_models()

        self.assertEqual(result["models"][0]["confidence"], 0.25)
        self.assertEqual(result["models"][0]["alert_interval"], 60)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest backend.tests.test_model_service.ModelMetadataSyncTest.test_list_models_fills_missing_default_confidence_and_alert_interval -v`

Expected: FAIL because `ModelMetaRecord` does not yet accept the two fields or the service does not return them.

- [ ] **Step 3: Add the columns to `ModelMetaRecord`**

Update `backend/src/db/models.py` in `ModelMetaRecord`:

```python
class ModelMetaRecord(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str | None] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    tags_json: Mapped[list | None] = mapped_column(JSON)
    classes_json: Mapped[list | None] = mapped_column(JSON)
    weights_path: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_size_mb: Mapped[float | None] = mapped_column(Float)
    job_id: Mapped[str | None] = mapped_column(String(64), index=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    alert_interval: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    updated_at: Mapped[DateTime | None] = mapped_column(DateTime(), index=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
```

- [ ] **Step 4: Run the test again to verify schema support is in place**

Run: `python -m unittest backend.tests.test_model_service.ModelMetadataSyncTest.test_list_models_fills_missing_default_confidence_and_alert_interval -v`

Expected: still FAIL, but now at service return assertions rather than model construction.

- [ ] **Step 5: Commit the schema step**

```bash
git add backend/src/db/models.py backend/tests/test_model_service.py
git commit -m "feat: add model default confidence metadata columns"
```

### Task 2: Surface Model Default Values Through Model Service and API

**Files:**
- Modify: `backend/src/services/model_service.py`
- Modify: `backend/src/api/routes/models.py`
- Test: `backend/tests/test_model_service.py`

- [ ] **Step 1: Write the failing tests for update and readback**

Add these tests to `backend/tests/test_model_service.py`:

```python
    async def test_update_model_persists_default_confidence_and_alert_interval(self):
        self._insert_model_row("dbdefaults02", "defaults-edit-model")

        service = ModelService()

        class UpdateDefaultsRequest:
            name = "defaults-edit-model"
            confidence = 0.6
            alert_interval = 15

        result = await service.update_model("dbdefaults02", UpdateDefaultsRequest())

        self.assertEqual(result["confidence"], 0.6)
        self.assertEqual(result["alert_interval"], 15)

        with self.session_local() as session:
            row = session.execute(
                select(ModelMetaRecord).where(ModelMetaRecord.id == "dbdefaults02")
            ).scalar_one()

        self.assertEqual(row.confidence, 0.6)
        self.assertEqual(row.alert_interval, 15)

    async def test_get_model_summary_returns_model_default_confidence_and_alert_interval(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="dbsummarydefaults",
                    name="summary-defaults-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=0.5,
                    alert_interval=45,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "dbsummarydefaults",
                        "name": "summary-defaults-model",
                        "classes": ["person"],
                    },
                )
            )
            session.commit()

        service = ModelService()
        result = await service.get_model_summary("dbsummarydefaults")

        self.assertEqual(result["confidence"], 0.5)
        self.assertEqual(result["alert_interval"], 45)
```

- [ ] **Step 2: Run the model service tests and verify failure**

Run: `python -m unittest backend.tests.test_model_service.ModelMetadataSyncTest.test_update_model_persists_default_confidence_and_alert_interval backend.tests.test_model_service.ModelMetadataSyncTest.test_get_model_summary_returns_model_default_confidence_and_alert_interval -v`

Expected: FAIL because service read/write paths do not yet include the new fields.

- [ ] **Step 3: Add centralized default normalization in `ModelService`**

In `backend/src/services/model_service.py`, add helpers near the top of `ModelService`:

```python
    @staticmethod
    def _default_model_confidence() -> float:
        return 0.25

    @staticmethod
    def _default_model_alert_interval() -> int:
        return 60

    def _apply_default_runtime_thresholds(self, model_meta: dict) -> dict:
        normalized = dict(model_meta or {})
        normalized["confidence"] = float(
            normalized.get("confidence", self._default_model_confidence())
            or self._default_model_confidence()
        )
        normalized["alert_interval"] = int(
            normalized.get("alert_interval", self._default_model_alert_interval())
            or self._default_model_alert_interval()
        )
        return normalized
```

- [ ] **Step 4: Update model row read/write methods**

Modify `_build_model_from_metadata_row` and `_update_model_row_from_payload` in `backend/src/services/model_service.py`:

```python
    def _build_model_from_metadata_row(self, row: ModelMetaRecord) -> dict:
        payload = dict(row.raw_json or {})
        payload["model_id"] = row.id
        payload["name"] = row.name
        payload["source"] = row.source
        payload["description"] = row.description
        payload["tags"] = list(row.tags_json or [])
        payload["classes"] = list(row.classes_json or [])
        payload["weights_path"] = row.weights_path
        payload["file_size"] = row.file_size
        payload["file_size_mb"] = row.file_size_mb
        payload["job_id"] = row.job_id
        payload["confidence"] = float(row.confidence or self._default_model_confidence())
        payload["alert_interval"] = int(row.alert_interval or self._default_model_alert_interval())
        payload["created_at"] = format_datetime(row.created_at)
        payload["updated_at"] = format_datetime(row.updated_at)
        return payload

    def _update_model_row_from_payload(self, row: ModelMetaRecord, payload: dict) -> None:
        row.name = payload.get("name")
        row.source = payload.get("source")
        row.description = payload.get("description")
        row.tags_json = list(payload.get("tags") or [])
        row.classes_json = list(payload.get("classes") or [])
        row.weights_path = payload.get("weights_path")
        row.file_size = payload.get("file_size")
        row.file_size_mb = payload.get("file_size_mb")
        row.job_id = payload.get("job_id")
        row.confidence = float(payload.get("confidence", self._default_model_confidence()) or self._default_model_confidence())
        row.alert_interval = int(payload.get("alert_interval", self._default_model_alert_interval()) or self._default_model_alert_interval())
        row.raw_json = dict(payload)
```

- [ ] **Step 5: Apply default normalization on service reads**

Update `list_models`, `get_model_summary`, and `get_model` so they call `_apply_default_runtime_thresholds(...)` before `_apply_label_recommendation(...)`, for example:

```python
model_meta = self._apply_label_recommendation(
    self._apply_default_runtime_thresholds(
        self._build_model_from_metadata_row(row)
    )
)
```

Use the same pattern for local file fallback models.

- [ ] **Step 6: Extend model update request schema**

Modify `backend/src/api/routes/models.py`:

```python
class UpdateModelRequest(BaseModel):
    name: Optional[str] = None
    confidence: Optional[float] = None
    alert_interval: Optional[int] = None
```

- [ ] **Step 7: Update `ModelService.update_model` to accept the two fields**

In `backend/src/services/model_service.py`, inside `update_model`, merge request values into the loaded payload:

```python
        if hasattr(request, "name") and request.name is not None:
            model_meta["name"] = request.name
        if hasattr(request, "confidence") and request.confidence is not None:
            model_meta["confidence"] = float(request.confidence)
        if hasattr(request, "alert_interval") and request.alert_interval is not None:
            model_meta["alert_interval"] = int(request.alert_interval)

        model_meta = self._apply_default_runtime_thresholds(model_meta)
```

- [ ] **Step 8: Run the model service tests and verify they pass**

Run: `python -m unittest backend.tests.test_model_service -v`

Expected: PASS for the newly added default/read/write tests and no regressions in existing model metadata tests.

- [ ] **Step 9: Commit the model service/API step**

```bash
git add backend/src/services/model_service.py backend/src/api/routes/models.py backend/tests/test_model_service.py
git commit -m "feat: expose model default confidence and alert interval"
```

### Task 3: Inherit Model Defaults When Adding Task Algorithms

**Files:**
- Modify: `backend/src/services/task_service.py`
- Test: `backend/tests/test_task_service.py`

- [ ] **Step 1: Write the failing tests for inherited defaults and explicit override**

Add these tests to `backend/tests/test_task_service.py`:

```python
    async def test_add_algorithm_inherits_model_default_confidence_and_alert_interval(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="model_defaults_task",
                    name="task-default-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=0.65,
                    alert_interval=25,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "model_defaults_task",
                        "name": "task-default-model",
                        "classes": ["person"],
                        "confidence": 0.65,
                        "alert_interval": 25,
                    },
                )
            )
            session.commit()

        task = await self.service.create_task(
            {
                "name": "inherit-default-task",
                "channel_id": "ch_inherit_defaults",
                "source_url": "rtsp://example/live/inherit-defaults",
            }
        )

        algorithm = await self.service.add_algorithm(
            task["id"],
            {
                "model_id": "model_defaults_task",
                "labels": ["person"],
            },
        )

        self.assertAlmostEqual(algorithm["confidence"], 0.65)
        self.assertEqual(algorithm["alert_interval"], 25)

    async def test_add_algorithm_keeps_explicit_confidence_and_alert_interval_override(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="model_override_task",
                    name="task-override-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=0.7,
                    alert_interval=40,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "model_override_task",
                        "name": "task-override-model",
                        "classes": ["person"],
                        "confidence": 0.7,
                        "alert_interval": 40,
                    },
                )
            )
            session.commit()

        task = await self.service.create_task(
            {
                "name": "override-default-task",
                "channel_id": "ch_override_defaults",
                "source_url": "rtsp://example/live/override-defaults",
            }
        )

        algorithm = await self.service.add_algorithm(
            task["id"],
            {
                "model_id": "model_override_task",
                "labels": ["person"],
                "confidence": 0.5,
                "alert_interval": 12,
            },
        )

        self.assertAlmostEqual(algorithm["confidence"], 0.5)
        self.assertEqual(algorithm["alert_interval"], 12)
```

- [ ] **Step 2: Run the task service tests and verify failure**

Run: `python -m unittest backend.tests.test_task_service.TaskServiceTest.test_add_algorithm_inherits_model_default_confidence_and_alert_interval backend.tests.test_task_service.TaskServiceTest.test_add_algorithm_keeps_explicit_confidence_and_alert_interval_override -v`

Expected: FAIL because `add_algorithm` currently falls back to hard-coded defaults.

- [ ] **Step 3: Apply model default values during algorithm enrichment**

Modify `_apply_model_scene_defaults` in `backend/src/services/task_service.py` so model defaults are merged before scene defaults:

```python
    async def _apply_model_scene_defaults(self, payload: dict[str, Any]) -> dict[str, Any]:
        model_id = str(payload.get("model_id") or "").strip()
        if not model_id:
            return payload

        try:
            model_meta = await model_service.get_model_summary(model_id)
        except Exception:
            logger.exception("[TaskAlgorithm] failed to load model defaults model_id=%s", model_id)
            return payload

        enriched = dict(payload)
        if model_meta and model_meta.get("name"):
            enriched.setdefault("model_name", model_meta.get("name"))
        if model_meta:
            enriched.setdefault("confidence", float(model_meta.get("confidence", 0.25) or 0.25))
            enriched.setdefault("alert_interval", int(model_meta.get("alert_interval", 60) or 60))

        scene_candidates = list((model_meta or {}).get("scene_candidates") or [])
        if not scene_candidates:
            return enriched
```

Keep the existing scene-name, rule-type, label, snapshot, and region defaults logic intact below that block.

- [ ] **Step 4: Ensure task available models returns model defaults**

No new API shape is needed if `ModelService.list_models()` already returns the fields. Verify `_build_available_model_algorithm_candidate` preserves them:

```python
        item.update(
            {
                "algorithm_type": algorithm_type,
                "scene_key": primary_scene.get("scene_key"),
                "scene_name": primary_scene.get("scene_name"),
                "rule_type": primary_scene.get("rule_type") or algorithm_type,
                "enabled": True,
                "detect_all_classes": detect_all_classes,
                "label_source": label_source,
                "confidence": float(item.get("confidence", 0.25) or 0.25),
                "alert_interval": int(item.get("alert_interval", 60) or 60),
```

If the fields are missing in some path, patch the builder so they always flow through.

- [ ] **Step 5: Run the task service tests and verify they pass**

Run: `python -m unittest backend.tests.test_task_service -v`

Expected: PASS for the new inheritance/override tests, with existing task algorithm behavior unchanged.

- [ ] **Step 6: Commit the task inheritance step**

```bash
git add backend/src/services/task_service.py backend/tests/test_task_service.py
git commit -m "feat: inherit model default thresholds for task algorithms"
```

### Task 4: Verify Runtime Still Uses Task Algorithm Values

**Files:**
- Modify: `backend/tests/test_task_service.py`
- Modify: `backend/tests/test_fast_read_paths.py` (only if needed for list payload expectations)

- [ ] **Step 1: Add a regression test that task algorithm values remain authoritative**

Add a task-service regression that persists a task algorithm row with values different from the model defaults and verifies the task readback still returns the task algorithm values:

```python
    async def test_get_task_from_metadata_prefers_task_algorithm_thresholds_over_model_defaults(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="model_runtime_authority",
                    name="runtime-authority-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=0.8,
                    alert_interval=99,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "model_runtime_authority",
                        "name": "runtime-authority-model",
                        "classes": ["person"],
                        "confidence": 0.8,
                        "alert_interval": 99,
                    },
                )
            )
            session.commit()

        task = await self.service.create_task(
            {
                "name": "runtime-authority-task",
                "channel_id": "ch_runtime_authority",
                "source_url": "rtsp://example/live/runtime-authority",
                "algorithms": [
                    {
                        "id": "algo_runtime_authority",
                        "model_id": "model_runtime_authority",
                        "labels": ["person"],
                        "confidence": 0.42,
                        "alert_interval": 18,
                    }
                ],
            }
        )

        metadata_task = await self.service.get_task_from_metadata(task["id"])
        algorithm = metadata_task["algorithms"][0]

        self.assertAlmostEqual(algorithm["confidence"], 0.42)
        self.assertEqual(algorithm["alert_interval"], 18)
```

- [ ] **Step 2: Run the targeted regression and verify failure if behavior drifted**

Run: `python -m unittest backend.tests.test_task_service.TaskServiceTest.test_get_task_from_metadata_prefers_task_algorithm_thresholds_over_model_defaults -v`

Expected: PASS if no regression was introduced; if it fails, fix the read path before continuing.

- [ ] **Step 3: Run a focused full verification**

Run:

```bash
python -m unittest backend.tests.test_model_service backend.tests.test_task_service -v
python -m py_compile backend/src/db/models.py backend/src/services/model_service.py backend/src/services/task_service.py backend/src/api/routes/models.py
```

Expected:

- All added tests pass
- No syntax errors from `py_compile`

- [ ] **Step 4: Commit the verification guardrail**

```bash
git add backend/tests/test_task_service.py backend/tests/test_model_service.py
git commit -m "test: cover model default threshold inheritance"
```

## Self-Review

- Spec coverage: this plan covers schema changes, model API exposure, task algorithm default hydration, and runtime-authority regression checks.
- Placeholder scan: no `TODO` or generic “add tests” placeholders remain.
- Type consistency: field names are consistently `confidence` and `alert_interval` across models, tasks, service payloads, and tests.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-02-model-default-confidence-alert-interval.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
