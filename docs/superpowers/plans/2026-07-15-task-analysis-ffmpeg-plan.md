# Task Analysis FFmpeg Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make task analysis use FFmpeg for stream probing and frame decoding, then verify the missing-alarm regression against the existing task debug logs.

**Architecture:** Keep the existing `FFmpegCapture` frame interface and make task analysis obtain dimensions through FFmpeg tooling. The task worker, alarm-video after-frame capture, and reconnect path will all use the same capture builder; OpenCV remains available only for image transforms and encoding.

**Tech Stack:** Python, FastAPI service layer, FFmpeg/ffprobe, NumPy, OpenCV, unittest/pytest.

---

### Task 1: Add the failing capture-path regression test

**Files:**
- Modify: `backend/tests/test_analysis_service.py`
- Test: `backend/tests/test_analysis_service.py`

- [ ] Add a test that patches `cv2.VideoCapture` to raise, patches the FFmpeg probe helper to return `(1920, 1080)`, patches `FFmpegCapture`, and asserts `_build_task_capture("rtsp://example")` constructs the FFmpeg capture with those dimensions.
- [ ] Run `python -m pytest backend/tests/test_analysis_service.py -k task_capture -q` and confirm the test fails because `_probe_task_stream` currently calls OpenCV.

### Task 2: Implement the FFmpeg-only task capture path

**Files:**
- Modify: `backend/src/services/analysis_service.py`

- [ ] Add an FFmpeg probe helper that returns the first video stream width and height and raises a clear `ValueError` for probe failure or missing dimensions.
- [ ] Replace `_probe_task_stream` with the FFmpeg probe helper.
- [ ] Make `_build_task_capture` use `FFmpegCapture` for RTSP, HTTP, and file inputs, while preserving the existing capture interface and release behavior.
- [ ] Add backend and decoded dimensions to task debug logging without changing detection or event decisions.
- [ ] Run the focused test and confirm it passes.

### Task 3: Verify existing behavior and audit runtime output

**Files:**
- No additional production files.

- [ ] Run `python -m py_compile backend/src/services/analysis_service.py`.
- [ ] Run the focused analysis and task-event tests.
- [ ] Restart the backend, start task `8a89a58b`, and inspect logs for FFmpeg initialization, frame dimensions, `raw`, `filtered`, `rule_events`, and `event_created`.
- [ ] Report the historical changes since the last channel `8224` alarm, separating committed history from uncommitted worktree changes.
