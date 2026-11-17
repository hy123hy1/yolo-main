# 后处理参数标准实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把算法后处理收敛为“场景模板 + 任务实例 + 运行时执行”三层结构，让模型推荐、任务配置、事件触发和前端展示都按统一参数标准工作。

**Architecture:** 后端维护一份场景目录，模型上传时生成推荐类别和默认场景，任务保存时把用户配置固化到 `postprocess_config` JSON 并兼容旧数据，分析服务按 `scene_key` 路由到对应后处理器。前端只展示当前场景需要的参数，避免把通用字段和算法私有字段混在同一个表单里。

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, SQLite, JSON, Vue 3, TypeScript

---

## File Structure

**Backend catalog and persistence**

- Create: `backend/src/services/postprocess_catalog.py`
  - Central scene registry, default parameters, and label rules.
- Modify: `backend/src/services/model_service.py`
  - Generate `scene_key`, `recommended_labels`, and default postprocess hints from model metadata.
- Modify: `backend/src/services/task_service.py`
  - Normalize task algorithm payloads into the unified parameter shape.
- Modify: `backend/src/services/analysis_service.py`
  - Dispatch analysis by `scene_key` and respect `detect_all_classes` / `requires_region`.
- Modify: `backend/src/services/metadata_db_sync.py`
  - Sync the new unified task algorithm config into the metadata DB.
- Modify: `backend/src/services/event_service.py`
  - Include `scene_key`, `labels`, and `postprocess_config` when creating events and clips.
- Modify: `backend/src/db/models.py`
  - Add or extend the fields that store task algorithm config JSON and runtime metadata.
- Modify: `backend/src/api/routes/models.py`
  - Return recommendation fields to the model management page.
- Modify: `backend/src/api/routes/tasks.py`
  - Accept the normalized algorithm config payload.
- Test: `backend/tests/test_postprocess_catalog.py`
- Test: `backend/tests/test_model_service.py`
- Test: `backend/tests/test_task_service.py`
- Test: `backend/tests/test_analysis_service.py`

**Frontend task and model UX**

- Modify: `frontend/src/api/models.ts`
  - Add types for recommendation fields and scene metadata.
- Modify: `frontend/src/api/tasks.ts`
  - Add unified task algorithm config types.
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`
  - Render scene-driven controls and the compact parameter layout.
- Modify: `frontend/src/pages/Models.vue`
  - Show model recommendation strategy, scene candidates, and recommended labels.
- Modify: `frontend/src/pages/Tasks.vue`
  - Show persisted algorithm scene info in task details.

**Docs**

- Modify: `docs/superpowers/specs/2026-06-09-postprocess-parameter-standard-design.md`
  - Mark implemented decisions after code lands.
- Modify: `README.md`
  - Briefly document the new parameter layers and compatibility rules.

---

### Task 1: Create the Scene Catalog and Default Parameter Rules

**Files:**
- Create: `backend/src/services/postprocess_catalog.py`
- Test: `backend/tests/test_postprocess_catalog.py`

- [ ] **Step 1: Write the failing tests**

```python
from src.services.postprocess_catalog import (
    get_scene_definition,
    normalize_postprocess_config,
)


def test_person_intrusion_defaults_need_region_and_snapshot():
    scene = get_scene_definition("person_intrusion")
    assert scene["requires_region"] is True
    assert scene["need_snapshot"] is True
    assert scene["need_video_clip"] is True


def test_labels_empty_means_detect_all_classes():
    config = normalize_postprocess_config(
        {
            "scene_key": "detection",
            "labels": [],
            "detect_all_classes": None,
        }
    )
    assert config["detect_all_classes"] is True
    assert config["labels"] == []


def test_small_class_scene_defaults_to_all():
    scene = get_scene_definition("helmet_detection")
    assert scene["default_label_strategy"] == "all"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_postprocess_catalog.py -q`

Expected: fail because the catalog module and helpers do not exist yet.

- [ ] **Step 3: Implement the catalog**

Add a scene registry with these responsibilities:

- `scene_key`
- `scene_name`
- `rule_type`
- `default_labels`
- `default_label_strategy`
- `requires_region`
- `need_overlay`
- `need_snapshot`
- `need_video_clip`
- `default_postprocess_config`

Use JSON-friendly defaults so the runtime can merge task overrides without special-case code.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest backend/tests/test_postprocess_catalog.py -q`

Expected: PASS.

---

### Task 2: Normalize Task Algorithms Into One Unified Payload

**Files:**
- Modify: `backend/src/services/task_service.py`
- Modify: `backend/src/api/routes/tasks.py`
- Modify: `backend/src/db/models.py`
- Modify: `backend/src/services/metadata_db_sync.py`
- Test: `backend/tests/test_task_service.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_task_algorithm_persists_scene_and_postprocess_config():
    payload = {
        "model_id": "model_001",
        "scene_key": "person_intrusion",
        "labels": ["person"],
        "detect_all_classes": False,
        "requires_region": True,
        "postprocess_config": {"hold_seconds": 10, "cooldown_seconds": 30},
    }
    saved = normalize_task_algorithm_payload(payload)
    assert saved["scene_key"] == "person_intrusion"
    assert saved["postprocess_config"]["hold_seconds"] == 10


def test_legacy_empty_labels_still_means_all_classes():
    payload = {"model_id": "model_001", "labels": []}
    saved = normalize_task_algorithm_payload(payload)
    assert saved["detect_all_classes"] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_task_service.py -q`

Expected: fail because the unified normalization logic is not wired yet.

- [ ] **Step 3: Implement the normalization and persistence**

Make task save/update do all of the following:

- merge scene defaults from `postprocess_catalog`
- preserve user overrides in `postprocess_config`
- treat `labels == []` as `detect_all_classes = true`
- keep old tasks compatible when `scene_key` is missing
- write the unified config into the DB and sync layer

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest backend/tests/test_task_service.py -q`

Expected: PASS.

---

### Task 3: Route Analysis, Events, and Clips by Scene Key

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Modify: `backend/src/services/event_service.py`
- Modify: `backend/src/services/recording_service.py`
- Test: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_scene_routing_uses_scene_key():
    handler = resolve_scene_handler("vehicle_congestion")
    assert handler is not None


def test_detect_all_classes_skips_label_filter():
    result = should_filter_labels(
        detect_all_classes=True,
        labels=["person"],
        detected_label="car",
    )
    assert result is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_analysis_service.py -q`

Expected: fail because scene routing and label filtering rules are not implemented yet.

- [ ] **Step 3: Implement runtime dispatch**

Add these behaviors:

- route by `scene_key`
- use `postprocess_config` for thresholds and timing
- allow `requires_region = false` to run full-screen detection
- include `scene_key` and final `labels` in events
- keep recordings and snapshots linked to the same algorithm instance

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest backend/tests/test_analysis_service.py -q`

Expected: PASS.

---

### Task 4: Update the Frontend to Show Scene-Driven Fields

**Files:**
- Modify: `frontend/src/api/models.ts`
- Modify: `frontend/src/api/tasks.ts`
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`
- Modify: `frontend/src/pages/Models.vue`
- Modify: `frontend/src/pages/Tasks.vue`

- [ ] **Step 1: Write the UI expectations first**

The task algorithm editor must show only the fields that matter for the selected scene:

- 模型
- 置信度
- 帧数
- 类别
- 保存抓拍图
- 保存告警视频
- 画框开关
- 区域配置入口
- 告警间隔
- 保存并应用

The model page must show:

- 推荐类别策略
- 推荐类别列表
- 场景候选

- [ ] **Step 2: Implement the form wiring**

Use scene metadata to:

- auto-fill recommended labels
- default small-class models to full selection
- hide irrelevant controls for `requires_region = false`
- keep the layout compact and readable

- [ ] **Step 3: Verify the front end builds**

Run: `npm.cmd run build`

Expected: build succeeds and the updated task editor compiles.

---

### Task 5: Compatibility, Docs, and Release Notes

**Files:**
- Modify: `docs/superpowers/specs/2026-06-09-postprocess-parameter-standard-design.md`
- Modify: `README.md`

- [ ] **Step 1: Add compatibility notes**

Document these rules:

- `labels` empty means all classes
- missing `scene_key` falls back to `detection`
- old tasks continue to run with default scene params
- `postprocess_config` is the only place for scene-specific overrides

- [ ] **Step 2: Add a short README note**

Explain how model upload, task creation, and analysis runtime now share one unified parameter standard.

- [ ] **Step 3: Re-run the focused test set**

Run:

```powershell
pytest backend/tests/test_postprocess_catalog.py -q
pytest backend/tests/test_model_service.py -q
pytest backend/tests/test_task_service.py -q
pytest backend/tests/test_analysis_service.py -q
```

Expected: all pass.

---

## Self-Review

**1. Spec coverage:**  
The plan covers scene templates, task-instance storage, runtime dispatch, front-end field display, compatibility, and docs.

**2. Placeholder scan:**  
No TBD / TODO / later placeholders remain in the plan.

**3. Type consistency:**  
`scene_key`, `detect_all_classes`, `labels`, `requires_region`, and `postprocess_config` are used consistently across backend and frontend tasks.

