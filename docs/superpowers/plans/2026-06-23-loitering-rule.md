# Loitering Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real time-threshold-based `loitering` rule for task algorithms and expose its threshold in the existing algorithm editor.

**Architecture:** Reuse the existing per-algorithm `postprocess_config` storage and extend `AnalysisService._build_rule_events()` with a region occupancy duration rule. Keep the frontend change isolated to the existing Vue algorithm modal by conditionally rendering one threshold input for `loitering`.

**Tech Stack:** Python, Vue 3, TypeScript, unittest.

---

### Task 1: Add failing tests for loitering behavior

**Files:**
- Modify: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Write failing tests**

Add tests covering:

```python
def test_apply_rule_type_loitering_emits_after_linger_threshold(self):
    ...
```

```python
def test_apply_rule_type_loitering_resets_after_region_clears(self):
    ...
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
python -m unittest backend.tests.test_analysis_service.AnalysisServiceTest.test_apply_rule_type_loitering_emits_after_linger_threshold backend.tests.test_analysis_service.AnalysisServiceTest.test_apply_rule_type_loitering_resets_after_region_clears
```

Expected:
- FAIL because `loitering` still falls through to generic behavior

### Task 2: Implement loitering rule in AnalysisService

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Test: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Add config helper**

Read `linger_seconds` from `postprocess_config`, defaulting to `10`.

- [ ] **Step 2: Add rule-specific state logic**

Implement region-by-region occupancy duration tracking and emit only after the region stays occupied for `linger_seconds`.

- [ ] **Step 3: Return business event payload**

Emit payload fields including:

- `loiter_seconds`
- `zone_id`
- `zone_name`
- `annotation_label`

- [ ] **Step 4: Run the backend test file**

Run:

```bash
python -m unittest backend.tests.test_analysis_service
```

Expected:
- PASS

### Task 3: Expose loitering threshold in the modal

**Files:**
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`

- [ ] **Step 1: Add conditional input**

Show `滞留秒数` only for `rule_type=loitering`.

- [ ] **Step 2: Wire it into `postprocess_config`**

Persist:

- `postprocess_config.linger_seconds`

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
