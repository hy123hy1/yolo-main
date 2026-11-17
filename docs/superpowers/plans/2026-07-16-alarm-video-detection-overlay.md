# Alarm Video Detection Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Annotate the actual event inference frame in generated alarm videos with the detection label, confidence, and bounding box.

**Architecture:** Preserve the existing asynchronous alarm-video pipeline. Pass the event frame timestamp through `_submit_task_alarm_video_job()` and `_finalize_task_alarm_video_job()` into `_save_alarm_video()`, which invokes the existing `_annotate_frame()` only for the matching frame timestamp.

**Tech Stack:** Python, OpenCV, unittest, existing `AnalysisService` alarm-video pipeline.

---

### Task 1: Lock the annotation contract with tests

**Files:**
- Modify: `G:/docker/yolo-main/backend/tests/test_analysis_service.py`

- [x] **Step 1: Replace the raw-frame expectation with an event-frame expectation**

Add a test that encodes two frames with timestamps `1.0` and `2.0`, passes `annotation_timestamp=2.0` and one detection, then asserts the first frame remains black while the second frame contains the red bbox and nonzero label pixels.

- [x] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
python -m unittest backend.tests.test_analysis_service.AnalysisServiceTest.test_alarm_video_annotates_only_event_frame -v
```

Expected: failure because `_save_alarm_video()` does not accept or use `annotation_timestamp`.

- [x] **Step 3: Add a forwarding test for the asynchronous job**

Patch `_alarm_video_executor.submit`, call `_submit_task_alarm_video_job()` with `event_timestamp=12.5`, and assert the submitted keyword arguments contain `event_timestamp=12.5`.

- [x] **Step 4: Run the forwarding test and verify it fails**

Run:

```powershell
python -m unittest backend.tests.test_analysis_service.AnalysisServiceTest.test_submit_alarm_video_job_forwards_event_timestamp -v
```

Expected: failure because the current method signature has no event timestamp parameter.

### Task 2: Implement timestamp-scoped video annotation

**Files:**
- Modify: `G:/docker/yolo-main/backend/src/services/analysis_service.py`
- Test: `G:/docker/yolo-main/backend/tests/test_analysis_service.py`

- [x] **Step 1: Extend the alarm-video method signatures**

Add `event_timestamp: float | None = None` to `_save_alarm_video()`, `_finalize_task_alarm_video_job()`, and `_submit_task_alarm_video_job()`.

- [x] **Step 2: Annotate only the matching frame**

In `_save_alarm_video()`, compute a timestamp tolerance of `max(0.05, 0.5 / safe_fps)`. For each `(frame_timestamp, frame)`, call `_prepare_alarm_video_frame(frame, detections, draw_detection=...)`, where `draw_detection` is true only when `event_timestamp` is present and the timestamp difference is within tolerance.

Update `_prepare_alarm_video_frame()` to call `_annotate_frame()` only when `draw_detection` is true; otherwise return the decoded frame unchanged.

- [x] **Step 3: Forward the event timestamp from the task worker**

Pass the current `now_ts` into `_submit_task_alarm_video_job(event_timestamp=now_ts, ...)`. Forward it unchanged through the asynchronous job to `_save_alarm_video()`.

- [x] **Step 4: Run the focused tests and verify they pass**

Run:

```powershell
python -m unittest backend.tests.test_analysis_service.AnalysisServiceTest.test_alarm_video_annotates_only_event_frame backend.tests.test_analysis_service.AnalysisServiceTest.test_submit_alarm_video_job_forwards_event_timestamp -v
```

Expected: both tests pass.

### Task 3: Regression verification

**Files:**
- No additional files.

- [x] **Step 1: Run all analysis-service tests**

```powershell
python -m unittest backend.tests.test_analysis_service -v
```

- [x] **Step 2: Run the complete backend test suite**

```powershell
python -m unittest discover -s backend/tests -p 'test_*.py' -v
```

- [x] **Step 3: Inspect the diff and confirm scope**

```powershell
git diff -- backend/src/services/analysis_service.py backend/tests/test_analysis_service.py
```

Confirm that only the alarm-video annotation timestamp flow and its tests changed; detection, preview, and event rules remain untouched.
