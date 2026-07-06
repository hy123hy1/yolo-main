# Gathering / Congestion Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real region-count-based alarm logic for `gathering` and `congestion`, and expose threshold configuration in the algorithm editor.

**Architecture:** Keep the implementation inside the existing task algorithm pipeline by storing per-algorithm thresholds in `postprocess_config`. Extend `AnalysisService._build_rule_events()` with grouped region-count state handling, and expose the config in the existing Vue algorithm modal only for the relevant rule types.

**Tech Stack:** Python, FastAPI service layer, Vue 3, TypeScript, unittest.

---

### Task 1: Add failing backend tests for gathering/congestion rules

**Files:**
- Modify: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

```python
def test_apply_rule_type_gathering_emits_after_zone_count_reaches_threshold_for_hold_seconds(self):
    ...
```

```python
def test_apply_rule_type_congestion_resets_after_count_drops_below_threshold(self):
    ...
```

- [ ] **Step 2: Run focused tests to confirm red state**

Run:

```bash
python -m unittest backend.tests.test_analysis_service.AnalysisServiceTestCase.test_apply_rule_type_gathering_emits_after_zone_count_reaches_threshold_for_hold_seconds backend.tests.test_analysis_service.AnalysisServiceTestCase.test_apply_rule_type_congestion_resets_after_count_drops_below_threshold
```

Expected:
- FAIL because the rule-specific behavior does not exist yet

### Task 2: Implement gathering/congestion rule handling in AnalysisService

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Test: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Add helper(s) to read per-rule threshold config**

Read `count_threshold` and `hold_seconds` from `postprocess_config`, with defaults:

- gathering: threshold 3
- congestion: threshold 5
- hold seconds: 2

- [ ] **Step 2: Add grouped rule event logic**

Inside `_build_rule_events()`:

- group detections by `zone_id` or `global`
- count detections in each group
- record `active_since`
- emit only after continuous threshold hit for `hold_seconds`
- reset state when count falls below threshold

- [ ] **Step 3: Return business-friendly event payload**

Emit payload fields including:

- `object_count`
- `zone_id`
- `zone_name`
- `annotation_label`
- `label_anchor`

- [ ] **Step 4: Run focused backend tests**

Run:

```bash
python -m unittest backend.tests.test_analysis_service
```

Expected:
- PASS

### Task 3: Expose threshold config in the algorithm editor modal

**Files:**
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`

- [ ] **Step 1: Add conditional form fields**

Show `数量阈值` and `持续秒数` only when rule type is `gathering` or `congestion`.

- [ ] **Step 2: Wire values into `draft.postprocess_config`**

Persist:

- `postprocess_config.count_threshold`
- `postprocess_config.hold_seconds`

- [ ] **Step 3: Apply defaults when recommendation/model mapping selects these rules**

Ensure the form initializes with the expected defaults for the relevant rule types.

### Task 4: Verify syntax and regression safety

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Modify: `backend/tests/test_analysis_service.py`
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`

- [ ] **Step 1: Run backend syntax validation**

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

- [ ] **Step 3: Spot-check behavior summary**

Confirm:

- 聚集类参数只在相关规则下展示
- 阈值保存后会进入 `postprocess_config`
- 后端事件逻辑按区域数量阈值触发
