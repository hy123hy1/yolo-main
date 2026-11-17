# Backend Rule Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move algorithm-specific thresholds for `absence`, `gathering`, `congestion`, `loitering`, and `fall_like` from per-task frontend configuration to a backend-managed TOML file.

**Architecture:** Add a dedicated backend config file and a lightweight cached reader in `src/core`, then change `AnalysisService` to read rule thresholds exclusively from that source. Remove the corresponding frontend controls so the UI no longer suggests these values are task-editable.

**Tech Stack:** Python, TOML, Vue 3, TypeScript, unittest.

---

### Task 1: Write failing tests for backend-managed rule config

**Files:**
- Modify: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Add red tests**

Add tests showing that backend config should override and ignore old algorithm `postprocess_config` for:

- `absence`
- `gathering`
- `loitering`
- `fall_like`

- [ ] **Step 2: Run focused tests to confirm failure**

Run:

```bash
python -m unittest backend.tests.test_analysis_service.AnalysisServiceTest.test_absence_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config backend.tests.test_analysis_service.AnalysisServiceTest.test_gathering_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config backend.tests.test_analysis_service.AnalysisServiceTest.test_loitering_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config backend.tests.test_analysis_service.AnalysisServiceTest.test_fall_like_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config
```

Expected:
- FAIL because runtime still uses database/task values

### Task 2: Add backend rule config file and loader

**Files:**
- Create: `backend/configs/algorithm_rules.toml`
- Create: `backend/src/core/algorithm_rule_config.py`

- [ ] **Step 1: Add config file with defaults**

Create rule sections for:

- `absence`
- `gathering`
- `congestion`
- `loitering`
- `fall_like`

- [ ] **Step 2: Add cached loader**

Expose:

```python
def get_algorithm_rule_config(rule_type: str) -> dict[str, Any]:
    ...
```

### Task 3: Switch runtime rules to backend config only

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Test: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Replace database-based threshold reads**

Update these helpers:

- `_absence_threshold_seconds()`
- `_count_rule_threshold()`
- `_loitering_threshold_seconds()`
- `_fall_like_thresholds()`

to use backend config only.

- [ ] **Step 2: Run focused tests**

Run:

```bash
python -m unittest backend.tests.test_analysis_service.AnalysisServiceTest.test_absence_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config backend.tests.test_analysis_service.AnalysisServiceTest.test_gathering_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config backend.tests.test_analysis_service.AnalysisServiceTest.test_loitering_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config backend.tests.test_analysis_service.AnalysisServiceTest.test_fall_like_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config
```

Expected:
- PASS

### Task 4: Remove frontend rule-threshold editing for backend-managed rules

**Files:**
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`

- [ ] **Step 1: Remove UI fields**

Remove parameter controls for:

- `absence`
- `gathering`
- `congestion`
- `loitering`
- `fall_like`

- [ ] **Step 2: Submit empty `postprocess_config` for backend-managed rule types**

Keep old DB values from growing further and reduce operator confusion.

### Task 5: Verify regression safety

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Modify: `backend/tests/test_analysis_service.py`
- Modify: `frontend/src/components/channels/AlgorithmEditorModal.vue`

- [ ] **Step 1: Run full analysis service tests**

Run:

```bash
python -m unittest backend.tests.test_analysis_service
```

Expected:
- PASS

- [ ] **Step 2: Run Python syntax verification**

Run:

```bash
python -m py_compile backend/src/core/algorithm_rule_config.py backend/src/services/analysis_service.py backend/tests/test_analysis_service.py
```

Expected:
- no output

- [ ] **Step 3: Build frontend**

Run:

```bash
npm.cmd run build
```

Expected:
- build succeeds
