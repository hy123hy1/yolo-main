# Fall Like Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight `fall_like` post-processing rule that turns `person` detections into "人员倒地" alarms based on box shape and dwell time, with optional region scoping.

**Architecture:** Reuse the current task algorithm pipeline: detections first pass confidence, label, and region filters, then `AnalysisService._build_rule_events()` applies a `fall_like` heuristic. Store rule parameters in each task algorithm's `postprocess_config`, and expose them only when the algorithm rule type is `fall_like` in the existing Vue editor modal.

**Tech Stack:** Python, Vue 3, TypeScript, unittest.

---

### Task 1: Add failing backend tests for `fall_like`

**Files:**
- Modify: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

```python
def test_apply_rule_type_fall_like_emits_after_ratio_and_hold_threshold(self):
    ...
```

```python
def test_apply_rule_type_fall_like_ignores_boxes_below_ratio_or_min_height(self):
    ...
```

```python
def test_apply_rule_type_fall_like_resets_after_suspicious_target_disappears(self):
    ...
```

- [ ] **Step 2: Run the focused tests to confirm red state**

Run:

```bash
python -m unittest backend.tests.test_analysis_service.AnalysisServiceTest.test_apply_rule_type_fall_like_emits_after_ratio_and_hold_threshold backend.tests.test_analysis_service.AnalysisServiceTest.test_apply_rule_type_fall_like_ignores_boxes_below_ratio_or_min_height backend.tests.test_analysis_service.AnalysisServiceTest.test_apply_rule_type_fall_like_resets_after_suspicious_target_disappears
```

Expected:
- FAIL because `fall_like` still falls through to generic detection behavior

### Task 2: Implement `fall_like` heuristic in `AnalysisService`

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Test: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Add config helpers**

Read these values from `postprocess_config`:

- `fall_ratio_threshold`, default `1.2`
- `hold_seconds`, default `2`
- `min_box_height`, default `40`

- [ ] **Step 2: Add suspicious-candidate filtering**

Keep only detections that:

- belong to the effective person labels
- have `height >= min_box_height`
- have `width / height >= fall_ratio_threshold`

- [ ] **Step 3: Add region/global state tracking**

Track `active_since` and `emitted` per algorithm and per region/global scope. Emit only after suspicious fall-like presence lasts `hold_seconds`, and reset when no suspicious candidate remains.

- [ ] **Step 4: Return business event payload**

Include:

- `fall_ratio`
- `fall_seconds`
- `zone_id`
- `zone_name`
- `annotation_label`
- `snapshot_detections`
- `snapshot_regions`

- [ ] **Step 5: Run the backend tests**

Run:

```bash
python -m unittest backend.tests.test_analysis_service
```

Expected:
- PASS

### Task 3: Expose `fall_like` parameters in the algorithm editor modal

**Files:**
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`

- [ ] **Step 1: Add conditional fields**

Show these only when `rule_type=fall_like`:

- `倒地宽高比`
- `持续秒数`
- `最小框高`

- [ ] **Step 2: Persist values into `postprocess_config`**

Wire:

- `postprocess_config.fall_ratio_threshold`
- `postprocess_config.hold_seconds`
- `postprocess_config.min_box_height`

- [ ] **Step 3: Apply defaults on rule switch/model recommendation**

Make sure `fall_like` opens with default values even for a newly added algorithm.

### Task 4: Verify syntax and build

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Modify: `backend/tests/test_analysis_service.py`
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`

- [ ] **Step 1: Run Python syntax verification**

Run:

```bash
python -m py_compile backend/src/services/analysis_service.py backend/tests/test_analysis_service.py
```

Expected:
- no output

- [ ] **Step 2: Build frontend**

Run:

```bash
npm.cmd run build
```

Expected:
- build succeeds
