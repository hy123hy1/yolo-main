# Model-Driven Label Recommendation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build model-driven scene and label recommendation so small-class models default to full selection while large-class YOLO models auto-suggest scene-based labels from the model name.

**Architecture:** Extend model metadata generation in the backend so each model persists `recommended_labels`, `auto_label_strategy`, and `scene_candidates`. Extend task algorithm normalization/storage to carry `scene_key`, `rule_type`, `detect_all_classes`, and related fields. Update the algorithm editor modal to consume model recommendation metadata and auto-fill scene/labels when the user selects a model.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, JSON metadata files, Vue 3, TypeScript

---

## File Structure

**Backend model recommendation**

- Modify: `backend/src/services/model_service.py`
  - Add scene mapping and recommendation generation.
- Modify: `backend/src/api/routes/models.py`
  - Ensure model update payload can round-trip recommendation fields if needed.
- Test: `backend/tests/test_model_service.py`
  - Add focused tests for small-class full selection and large-class name-based recommendation.

**Backend task algorithm structure**

- Modify: `backend/src/api/routes/tasks.py`
  - Extend request models for new task algorithm fields.
- Modify: `backend/src/services/task_service.py`
  - Normalize/persist task algorithm scene and label strategy fields.
- Modify: `backend/src/services/analysis_service.py`
  - Respect `detect_all_classes`.
- Modify: `backend/src/services/metadata_db_sync.py`
  - Sync new task algorithm fields into metadata DB.
- Modify: `backend/src/db/models.py`
  - Add task algorithm metadata columns.
- Test: `backend/tests/test_task_service.py`
  - Add task algorithm normalization and recommendation persistence coverage.

**Frontend model + task UX**

- Modify: `frontend/src/api/models.ts`
  - Extend model type definitions.
- Modify: `frontend/src/api/tasks.ts`
  - Extend task algorithm payload typing.
- Modify: `frontend/src/pages/Models.vue`
  - Show recommendation strategy, labels, and scene candidates.
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`
  - Auto-fill scene/labels from model metadata and small-model defaults.
- Optionally modify: `frontend/src/components/channels/TaskDetailPanel.vue`
  - Show scene display fields instead of raw label count where helpful.

**Docs**

- Modify: `docs/superpowers/specs/2026-06-08-model-driven-label-recommendation-design.md`
  - Mark implemented decisions if needed after code lands.

---

### Task 1: Add Backend Model Recommendation Engine

**Files:**
- Modify: `backend/src/services/model_service.py`
- Test: `backend/tests/test_model_service.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_model_service.py` with these cases:

```python
import tempfile
import unittest
from pathlib import Path

from src.services.model_service import ModelService


class ModelRecommendationTest(unittest.TestCase):
    def setUp(self):
        self.service = ModelService()

    def test_small_class_model_defaults_to_all(self):
        model_meta = {
            "name": "安全帽检测",
            "classes": ["helmet", "person"],
        }
        result = self.service._apply_label_recommendation(model_meta)
        self.assertEqual(result["auto_label_strategy"], "all")
        self.assertEqual(result["recommended_labels"], ["helmet", "person"])
        self.assertTrue(any(item["scene_key"] == "helmet_detection" for item in result["scene_candidates"]))

    def test_large_class_model_uses_name_mapping(self):
        model_meta = {
            "name": "人员检测",
            "classes": ["person", "car", "truck", "bus", "bicycle", "motorcycle"],
        }
        result = self.service._apply_label_recommendation(model_meta)
        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["person"])
        self.assertEqual(result["scene_candidates"][0]["scene_key"], "person_intrusion")

    def test_large_class_model_with_no_match_falls_back_to_manual(self):
        model_meta = {
            "name": "场景一",
            "classes": ["person", "car", "truck", "bus", "bicycle", "motorcycle"],
        }
        result = self.service._apply_label_recommendation(model_meta)
        self.assertEqual(result["auto_label_strategy"], "manual")
        self.assertEqual(result["recommended_labels"], [])
        self.assertEqual(result["scene_candidates"], [])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
docker exec yolo-backend sh -lc "cd /app && python -m unittest backend.tests.test_model_service -v"
```

Expected: FAIL with missing `_apply_label_recommendation` or wrong recommendation fields.

- [ ] **Step 3: Write minimal implementation**

Add these focused helpers inside `backend/src/services/model_service.py`:

```python
SCENE_MAPPINGS = [
    {
        "scene_key": "person_intrusion",
        "scene_name": "人员闯入",
        "keywords": ["人员检测", "人员闯入", "闯入", "person"],
        "labels": ["person"],
        "rule_type": "intrusion",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": True,
        "priority": 100,
    },
    {
        "scene_key": "vehicle_count",
        "scene_name": "车辆计数",
        "keywords": ["车辆检测", "车辆计数", "vehicle"],
        "labels": ["car", "bus", "truck", "motorcycle", "bicycle"],
        "rule_type": "count",
        "default_need_overlay": False,
        "default_need_snapshot": True,
        "default_need_video_clip": False,
        "requires_region": False,
        "priority": 80,
    },
    {
        "scene_key": "helmet_detection",
        "scene_name": "安全帽检测",
        "keywords": ["安全帽", "helmet"],
        "labels": ["helmet", "person"],
        "rule_type": "detection",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 100,
    },
]

def _normalize_labels(self, labels: list[str], classes: list[str]) -> list[str]:
    class_set = {str(item) for item in classes}
    return [label for label in labels if label in class_set]

def _build_scene_candidates(self, model_name: str, classes: list[str]) -> list[dict]:
    name = str(model_name or "").lower()
    candidates = []
    for mapping in SCENE_MAPPINGS:
        if not any(keyword.lower() in name for keyword in mapping["keywords"]):
            continue
        labels = self._normalize_labels(mapping["labels"], classes)
        if not labels:
            continue
        candidates.append({
            "scene_key": mapping["scene_key"],
            "scene_name": mapping["scene_name"],
            "recommended_labels": labels,
            "rule_type": mapping["rule_type"],
            "default_need_overlay": mapping["default_need_overlay"],
            "default_need_snapshot": mapping["default_need_snapshot"],
            "default_need_video_clip": mapping["default_need_video_clip"],
            "requires_region": mapping["requires_region"],
            "priority": mapping["priority"],
            "source": "model_name_mapping",
        })
    candidates.sort(key=lambda item: item["priority"], reverse=True)
    return candidates

def _apply_label_recommendation(self, model_meta: dict) -> dict:
    model_meta = dict(model_meta)
    classes = [str(item) for item in list(model_meta.get("classes") or [])]
    if len(classes) <= 5:
        model_meta["auto_label_strategy"] = "all"
        model_meta["recommended_labels"] = classes
        model_meta["scene_candidates"] = []
        if "安全帽" in str(model_meta.get("name") or "") and classes:
            model_meta["scene_candidates"] = [{
                "scene_key": "helmet_detection",
                "scene_name": "安全帽检测",
                "recommended_labels": classes,
                "rule_type": "detection",
                "default_need_overlay": True,
                "default_need_snapshot": True,
                "default_need_video_clip": True,
                "requires_region": False,
                "priority": 100,
                "source": "small_class_auto",
            }]
        return model_meta

    candidates = self._build_scene_candidates(str(model_meta.get("name") or ""), classes)
    if candidates:
        model_meta["auto_label_strategy"] = "recommended"
        model_meta["recommended_labels"] = list(candidates[0]["recommended_labels"])
        model_meta["scene_candidates"] = candidates
    else:
        model_meta["auto_label_strategy"] = "manual"
        model_meta["recommended_labels"] = []
        model_meta["scene_candidates"] = []
    return model_meta
```

Then call `_apply_label_recommendation(model_meta)` in:

- ZIP upload flow after classes are extracted
- `update_model(...)` before saving edited model metadata
- model list/get fallback path if recommendation fields are missing

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
docker exec yolo-backend sh -lc "cd /app && python -m unittest backend.tests.test_model_service -v"
```

Expected: PASS for all three recommendation scenarios.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/model_service.py backend/tests/test_model_service.py
git commit -m "feat: add model-driven label recommendation metadata"
```

### Task 2: Persist Task Algorithm Scene and Label Strategy Fields

**Files:**
- Modify: `backend/src/api/routes/tasks.py`
- Modify: `backend/src/services/task_service.py`
- Modify: `backend/src/services/analysis_service.py`
- Modify: `backend/src/services/metadata_db_sync.py`
- Modify: `backend/src/db/models.py`
- Test: `backend/tests/test_task_service.py`

- [ ] **Step 1: Write the failing tests**

Add these cases to `backend/tests/test_task_service.py`:

```python
    async def test_create_task_preserves_scene_and_label_strategy_fields(self):
        task = await self.service.create_task(
            {
                "name": "scene-aware-task",
                "source_url": "rtsp://example/live/scene",
                "algorithms": [
                    {
                        "id": "algo_scene",
                        "model_id": "model_person",
                        "scene_key": "person_intrusion",
                        "scene_name": "人员闯入",
                        "rule_type": "intrusion",
                        "labels": ["person"],
                        "detect_all_classes": False,
                        "label_source": "scene_recommended",
                        "requires_region": True,
                    }
                ],
            }
        )
        algorithm = task["algorithms"][0]
        self.assertEqual(algorithm["scene_key"], "person_intrusion")
        self.assertEqual(algorithm["rule_type"], "intrusion")
        self.assertFalse(algorithm["detect_all_classes"])
        self.assertEqual(algorithm["label_source"], "scene_recommended")
        self.assertTrue(algorithm["requires_region"])

    async def test_analysis_service_skips_label_filter_when_detect_all_classes_enabled(self):
        from src.services.analysis_service import AnalysisService

        service = AnalysisService()
        detections = [
            {"class_name": "person", "x1": 0, "y1": 0, "x2": 10, "y2": 10},
            {"class_name": "truck", "x1": 10, "y1": 10, "x2": 20, "y2": 20},
        ]
        task = {
            "regions": [],
            "ext": {},
        }
        algorithm = {
            "labels": [],
            "detect_all_classes": True,
            "region_ids": [],
            "regions": [],
        }
        result = service._apply_algorithm_scoped_filters(detections, task, algorithm)
        self.assertEqual(len(result), 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
docker exec yolo-backend sh -lc "cd /app && python -m unittest backend.tests.test_task_service -v"
```

Expected: FAIL because task normalization drops new fields and analysis still interprets empty labels as “all” implicitly.

- [ ] **Step 3: Write minimal implementation**

Update `backend/src/api/routes/tasks.py` request models:

```python
class AlgorithmRequest(BaseModel):
    id: Optional[str] = None
    algorithm_type: Optional[str] = "detection"
    model_id: str
    scene_key: Optional[str] = None
    scene_name: Optional[str] = None
    rule_type: Optional[str] = None
    enabled: bool = True
    labels: list[str] = Field(default_factory=list)
    detect_all_classes: bool = False
    label_source: Optional[str] = None
    confidence: float = 0.25
    alert_interval: int = 60
    need_snapshot: bool = True
    need_video_clip: bool = True
    video_before_seconds: int = 3
    video_after_seconds: int = 3
    need_overlay: bool = False
    overlay_color: str = "#ff4d4f"
    requires_region: bool = False
    postprocess_config: dict = Field(default_factory=dict)
    region_ids: list[str] = Field(default_factory=list)
    regions: list[RegionRequest] = Field(default_factory=list)
```

Update `backend/src/services/task_service.py` normalization:

```python
        return {
            "id": algorithm.get("id") or generate_id("algo"),
            "algorithm_type": algorithm.get("algorithm_type") or "detection",
            "model_id": algorithm.get("model_id"),
            "scene_key": algorithm.get("scene_key"),
            "scene_name": algorithm.get("scene_name"),
            "rule_type": algorithm.get("rule_type") or "detection",
            "enabled": bool(algorithm.get("enabled", True)),
            "labels": list(algorithm.get("labels") or []),
            "detect_all_classes": bool(algorithm.get("detect_all_classes", False)),
            "label_source": algorithm.get("label_source") or ("manual" if algorithm.get("labels") else None),
            "confidence": float(algorithm.get("confidence", algorithm.get("conf", 0.25)) or 0.25),
            "alert_interval": max(int(algorithm.get("alert_interval", 60) or 60), 0),
            "need_snapshot": bool(algorithm.get("need_snapshot", True)),
            "need_video_clip": need_video_clip,
            "video_before_seconds": 3 if need_video_clip else 0,
            "video_after_seconds": 3 if need_video_clip else 0,
            "need_overlay": bool(algorithm.get("need_overlay", False)),
            "overlay_color": algorithm.get("overlay_color") or "#ff4d4f",
            "requires_region": bool(algorithm.get("requires_region", False)),
            "postprocess_config": dict(algorithm.get("postprocess_config") or {}),
            "region_ids": region_ids,
            "regions": [
                self._normalize_region(region)
                for region in list(algorithm.get("regions") or [])
                if isinstance(region, dict)
            ],
        }
```

Update `backend/src/services/analysis_service.py` scoped label filtering:

```python
    def _filter_detections_by_labels(self, detections: list[dict], channel: dict) -> list[dict]:
        config = channel.get("ext", {}).get("ai_config", {})
        if config.get("detect_all_classes"):
            return detections
        labels = config.get("labels") or []
        if not labels:
            return detections
        label_set = {str(label) for label in labels}
        return [detection for detection in detections if str(detection.get("class_name", "")) in label_set]
```

Update `_build_scoped_task(...)`:

```python
                "detect_all_classes": bool(algorithm.get("detect_all_classes", False)),
                "labels": algorithm.get("labels") or [],
```

Update `backend/src/db/models.py` task algorithm metadata model:

```python
    scene_key: Mapped[str | None] = mapped_column(String(64), index=True)
    scene_name: Mapped[str | None] = mapped_column(String(255))
    rule_type: Mapped[str | None] = mapped_column(String(64), index=True)
    detect_all_classes: Mapped[bool | None] = mapped_column(Boolean, index=True)
    label_source: Mapped[str | None] = mapped_column(String(64), index=True)
    requires_region: Mapped[bool | None] = mapped_column(Boolean, index=True)
    postprocess_config_json: Mapped[dict | None] = mapped_column(JSON)
```

Update `backend/src/services/metadata_db_sync.py` task algorithm sync:

```python
                scene_key=algorithm.get("scene_key"),
                scene_name=algorithm.get("scene_name"),
                rule_type=algorithm.get("rule_type"),
                detect_all_classes=algorithm.get("detect_all_classes"),
                label_source=algorithm.get("label_source"),
                requires_region=algorithm.get("requires_region"),
                postprocess_config_json=copy.deepcopy(algorithm.get("postprocess_config") or {}),
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
docker exec yolo-backend sh -lc "cd /app && python -m unittest backend.tests.test_task_service -v"
```

Expected: PASS for new scene-aware normalization and detect-all handling.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/routes/tasks.py backend/src/services/task_service.py backend/src/services/analysis_service.py backend/src/services/metadata_db_sync.py backend/src/db/models.py backend/tests/test_task_service.py
git commit -m "feat: store scene-aware task algorithm metadata"
```

### Task 3: Surface Recommendation Metadata in Model Management UI

**Files:**
- Modify: `frontend/src/api/models.ts`
- Modify: `frontend/src/pages/Models.vue`

- [ ] **Step 1: Write the failing UI expectation as a dev note test target**

Document the expected frontend shape in `frontend/src/api/models.ts`:

```ts
export interface ModelSceneCandidate {
  scene_key: string
  scene_name: string
  recommended_labels: string[]
  rule_type: string
  default_need_overlay: boolean
  default_need_snapshot: boolean
  default_need_video_clip: boolean
  requires_region: boolean
  priority: number
  source: string
}

export interface ModelItem {
  model_id: string
  name?: string
  classes?: string[]
  recommended_labels?: string[]
  auto_label_strategy?: 'all' | 'recommended' | 'manual'
  scene_candidates?: ModelSceneCandidate[]
}
```

- [ ] **Step 2: Run frontend type-check or build to verify it currently fails or lacks display**

Run:

```bash
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command "npm.cmd run build"
```

Expected: Either current code ignores the fields or type additions require template updates.

- [ ] **Step 3: Write minimal implementation**

Update `frontend/src/pages/Models.vue` to show recommendation info in each model item:

```vue
<p v-if="model.auto_label_strategy">
  默认策略:
  {{
    model.auto_label_strategy === 'all'
      ? '默认全选'
      : model.auto_label_strategy === 'recommended'
        ? '自动推荐'
        : '手动选择'
  }}
</p>
<p v-if="model.recommended_labels?.length">
  推荐类别: {{ model.recommended_labels.join(', ') }}
</p>
<p v-if="model.scene_candidates?.length">
  推荐场景: {{ model.scene_candidates.map(item => item.scene_name).join('、') }}
</p>
```

Add the same fields in the details modal table:

```vue
<tr><td>默认类别策略</td><td>{{ detailModel?.auto_label_strategy || '-' }}</td></tr>
<tr><td>推荐类别</td><td>{{ detailModel?.recommended_labels?.join(', ') || '-' }}</td></tr>
<tr><td>推荐场景</td><td>{{ detailModel?.scene_candidates?.map(item => item.scene_name).join('、') || '-' }}</td></tr>
```

- [ ] **Step 4: Run frontend build to verify it passes**

Run:

```bash
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command "npm.cmd run build"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/models.ts frontend/src/pages/Models.vue
git commit -m "feat: show model label recommendation metadata"
```

### Task 4: Auto-Fill Task Algorithm Modal from Model Recommendations

**Files:**
- Modify: `frontend/src/api/tasks.ts`
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`
- Optionally modify: `frontend/src/components/channels/TaskDetailPanel.vue`

- [ ] **Step 1: Write the failing behavior target**

Define the new payload fields in `frontend/src/api/tasks.ts`:

```ts
export interface TaskAlgorithmPayload {
  id?: string
  algorithm_type?: string
  model_id: string
  scene_key?: string
  scene_name?: string
  rule_type?: string
  enabled?: boolean
  labels?: string[]
  detect_all_classes?: boolean
  label_source?: 'scene_recommended' | 'model_default_all' | 'manual'
  confidence?: number
  alert_interval?: number
  need_snapshot?: boolean
  need_video_clip?: boolean
  video_before_seconds?: number
  video_after_seconds?: number
  need_overlay?: boolean
  overlay_color?: string
  requires_region?: boolean
  postprocess_config?: Record<string, any>
  region_ids?: string[]
  regions?: TaskRegionPayload[]
}
```

Expected UX target:

- Small model (`auto_label_strategy=all`) auto-fills all labels
- Large model (`auto_label_strategy=recommended`) shows scene candidates and fills after click
- Manual model shows no default labels

- [ ] **Step 2: Run frontend build to verify current modal does not satisfy the new props/logic**

Run:

```bash
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command "npm.cmd run build"
```

Expected: Current modal has no scene card UI and no new payload fields.

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/components/channels/AlgorithmEditorModal.vue`, extend model option typing:

```ts
type ModelSceneCandidate = {
  scene_key: string
  scene_name: string
  recommended_labels: string[]
  rule_type: string
  default_need_overlay: boolean
  default_need_snapshot: boolean
  default_need_video_clip: boolean
  requires_region: boolean
  priority: number
  source: string
}
```

Update props:

```ts
const props = defineProps<{
  visible: boolean
  algorithm?: any | null
  modelOptions: Array<{
    model_id?: string
    id?: string
    name?: string
    classes?: string[]
    recommended_labels?: string[]
    auto_label_strategy?: 'all' | 'recommended' | 'manual'
    scene_candidates?: ModelSceneCandidate[]
  }>
}>()
```

Add draft defaults:

```ts
  scene_key: '',
  scene_name: '',
  rule_type: 'detection',
  detect_all_classes: false,
  label_source: 'manual',
  requires_region: false,
  postprocess_config: {},
```

Add helpers:

```ts
const selectedModel = computed(() =>
  props.modelOptions.find((model) => (model.model_id || model.id) === draft.value.model_id) || null
)

const applySceneCandidate = (candidate: ModelSceneCandidate) => {
  draft.value.scene_key = candidate.scene_key
  draft.value.scene_name = candidate.scene_name
  draft.value.rule_type = candidate.rule_type
  draft.value.labels = [...candidate.recommended_labels]
  draft.value.detect_all_classes = false
  draft.value.label_source = 'scene_recommended'
  draft.value.need_overlay = candidate.default_need_overlay
  draft.value.need_snapshot = candidate.default_need_snapshot
  draft.value.need_video_clip = candidate.default_need_video_clip
  draft.value.requires_region = candidate.requires_region
  labelsText.value = candidate.recommended_labels.join(',')
}

const applyModelDefaults = (model: any) => {
  if (!model) return
  if (model.auto_label_strategy === 'all') {
    const labels = Array.isArray(model.recommended_labels) ? model.recommended_labels : []
    draft.value.scene_key = ''
    draft.value.scene_name = ''
    draft.value.rule_type = 'detection'
    draft.value.labels = [...labels]
    draft.value.detect_all_classes = false
    draft.value.label_source = 'model_default_all'
    labelsText.value = labels.join(',')
    return
  }
  draft.value.labels = []
  draft.value.detect_all_classes = false
  draft.value.label_source = 'manual'
  labelsText.value = ''
}
```

Add model watch:

```ts
watch(
  () => draft.value.model_id,
  () => {
    if (isEdit.value) return
    applyModelDefaults(selectedModel.value)
  }
)
```

Add scene candidate UI above labels:

```vue
<div v-if="selectedModel?.scene_candidates?.length" class="field span-2">
  <span>推荐场景</span>
  <div class="scene-candidate-row">
    <button
      v-for="candidate in selectedModel.scene_candidates"
      :key="candidate.scene_key"
      type="button"
      class="scene-chip"
      @click="applySceneCandidate(candidate)"
    >
      {{ candidate.scene_name }}
    </button>
  </div>
</div>
```

Update submit:

```ts
  emit('submit', {
    ...draft.value,
    labels,
    detect_all_classes: Boolean(draft.value.detect_all_classes),
    video_before_seconds: draft.value.need_video_clip ? 3 : 0,
    video_after_seconds: draft.value.need_video_clip ? 3 : 0,
  })
```

Optionally show scene name in `TaskDetailPanel.vue`:

```vue
<div>场景：{{ algorithm.scene_name || algorithm.scene_key || '未指定' }}</div>
```

- [ ] **Step 4: Run frontend build to verify it passes**

Run:

```bash
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command "npm.cmd run build"
```

Expected: PASS with new scene candidate UI and payload fields.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/tasks.ts frontend/src/components/channels/AlgorithmEditorModal.vue frontend/src/components/channels/TaskDetailPanel.vue
git commit -m "feat: auto-fill task algorithms from model recommendations"
```

### Task 5: Final Verification and Metadata Resync

**Files:**
- Modify: `docs/superpowers/specs/2026-06-08-model-driven-label-recommendation-design.md` if implementation notes are needed

- [ ] **Step 1: Resync metadata after schema changes**

Run:

```bash
docker compose -f docker\docker-compose.yml restart backend
docker exec yolo-backend sh -lc "python - <<'PY'
from src.services.metadata_db_sync import initialize_metadata_database
initialize_metadata_database()
print('INIT_DONE')
PY"
```

Expected: `INIT_DONE`

- [ ] **Step 2: Run backend task tests**

Run:

```bash
docker exec yolo-backend sh -lc "cd /app && python -m unittest backend.tests.test_model_service backend.tests.test_task_service -v"
```

Expected: PASS.

- [ ] **Step 3: Run frontend production build**

Run:

```bash
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -Command "npm.cmd run build"
```

Expected: PASS.

- [ ] **Step 4: Manual smoke test**

Verify this sequence:

```text
1. Upload or rename a large-class model to “人员检测”
2. Open model details and confirm:
   - 默认类别策略 = 自动推荐
   - 推荐类别 = person
   - 推荐场景 contains 人员闯入
3. Create task -> add algorithm -> choose that model
4. Confirm labels auto-fill to person after selecting scene
5. Upload or use a 2-class model (e.g. 安全帽检测)
6. Add algorithm -> choose model
7. Confirm labels auto-fill to all model classes
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-06-08-model-driven-label-recommendation-design.md
git commit -m "docs: finalize model-driven label recommendation rollout"
```

## Self-Review

**Spec coverage:** This plan covers model metadata recommendation, task algorithm schema extension, UI auto-fill, metadata sync, compatibility behavior, and verification. The only intentionally deferred area is the second-stage rule engine.

**Placeholder scan:** No `TODO`/`TBD` placeholders remain. Every code-changing step names exact files and includes the concrete snippets to add.

**Type consistency:** The plan uses consistent field names across backend and frontend:

- `recommended_labels`
- `auto_label_strategy`
- `scene_candidates`
- `scene_key`
- `scene_name`
- `rule_type`
- `detect_all_classes`
- `label_source`
- `requires_region`
- `postprocess_config`

