# Realtime Detection Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the existing ZLMediaKit WebRTC preview as the video layer and display live task detection boxes, labels, and confidence values on the existing electronic-fence canvas.

**Architecture:** Keep video and detection on separate channels. ZLMediaKit continues to provide the browser WebRTC video, while the task analysis worker publishes the latest filtered detections through a task-scoped WebSocket. The frontend merges those detections with the existing fence regions and draws both layers on the same canvas; detection publishing is latest-value only so it cannot block inference.

**Tech Stack:** FastAPI WebSocket, Python threading/asyncio bridge, existing `AnalysisService`, Vue 3, TypeScript, HTML canvas, existing ZLMediaKit WebRTC playback.

---

### Task 1: Define the detection metadata contract

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Test: `backend/tests/test_analysis_service.py`

- [ ] **Step 1: Write the failing payload test**

Add a unit test for a helper that converts one task frame's algorithm results into a JSON-safe payload containing `task_id`, `channel_id`, `seq`, `timestamp`, source dimensions, and per-algorithm detections with `label`, `confidence`, `bbox`, and `zone_id`.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```powershell
E:\anz\miniconda3\envs\dzt_coal\python.exe -m unittest backend.tests.test_analysis_service.AnalysisServiceTest.test_build_realtime_detection_payload -v
```

Expected: failure because the payload helper does not exist yet.

- [ ] **Step 3: Implement the payload helper**

Add a small pure helper that:

```python
{
    "type": "task_detection",
    "task_id": task_id,
    "channel_id": channel_id,
    "seq": frame_count,
    "timestamp": frame_timestamp,
    "source_width": int(frame.shape[1]),
    "source_height": int(frame.shape[0]),
    "algorithms": [
        {
            "algorithm_id": algorithm_id,
            "label": algorithm_label,
            "color": algorithm.get("overlay_color", "#ff4d4f"),
            "detections": [...],
        }
    ],
}
```

The helper must omit non-serializable model objects and include only filtered detections intended for display.

- [ ] **Step 4: Run the focused test and confirm it passes**

Run the same unittest command and expect `OK`.

### Task 2: Add a non-blocking task detection WebSocket hub

**Files:**
- Create: `backend/src/services/realtime_detection_service.py`
- Modify: `backend/src/api/routes/tasks.py`
- Modify: `backend/src/main.py` only if the router registration requires it
- Test: `backend/tests/test_realtime_detection_service.py`

- [ ] **Step 1: Write failing hub tests**

Cover these behaviors:

- subscribing creates a per-client queue;
- publishing from a worker thread enqueues a JSON-safe payload;
- publishing a newer payload replaces an older queued payload when the client is slow;
- unsubscribing removes the client and does not raise on later publishes.

- [ ] **Step 2: Run the focused hub tests and confirm they fail**

Run:

```powershell
E:\anz\miniconda3\envs\dzt_coal\python.exe -m unittest backend.tests.test_realtime_detection_service -v
```

Expected: failure because the hub module does not exist yet.

- [ ] **Step 3: Implement the hub**

Use a thread-safe registry keyed by `task_id`. Each subscriber owns an asyncio queue and event loop reference. The synchronous `publish(task_id, payload)` method must use `loop.call_soon_threadsafe` and a bounded/latest-value queue so a slow browser never blocks `_run_task_worker`.

- [ ] **Step 4: Add the WebSocket endpoint**

Add `@router.websocket("/{task_id}/detections/ws")` to the task router. Accept the socket, subscribe to the task hub, send a small `ready` message, then forward queued payloads until disconnect. Always unsubscribe in `finally`.

- [ ] **Step 5: Run the hub tests and the existing task tests**

Run:

```powershell
E:\anz\miniconda3\envs\dzt_coal\python.exe -m unittest backend.tests.test_realtime_detection_service backend.tests.test_analysis_service backend.tests.test_task_event_updates -v
```

Expected: all tests pass.

### Task 3: Publish live detections from the task worker

**Files:**
- Modify: `backend/src/services/analysis_service.py`
- Test: `backend/tests/test_analysis_service.py`
- Test: `backend/tests/test_task_event_updates.py`

- [ ] **Step 1: Write a failing worker publication test**

Patch the realtime hub and run a short worker with two algorithms. Assert that each processed frame publishes the filtered detections, source dimensions, algorithm IDs, and empty detection lists when no object is found.

- [ ] **Step 2: Run the focused worker test and confirm it fails**

Run the test by its fully qualified unittest name and expect no publication calls.

- [ ] **Step 3: Publish after inference without changing alarm behavior**

After `_collect_task_algorithm_inferences` returns, build and publish the metadata payload. Keep event creation, cooldown, snapshot creation, and alarm-video handling unchanged. Publish only JSON-safe detection data; never send raw frames or model objects. Use the worker's existing `frame_count` and `now_ts` so the frontend can identify stale results.

- [ ] **Step 4: Run the focused worker test and the full backend regression suites**

Run:

```powershell
E:\anz\miniconda3\envs\dzt_coal\python.exe -m unittest backend.tests.test_analysis_service backend.tests.test_task_event_updates -v
```

Expected: all tests pass and alarm event creation remains covered.

### Task 4: Subscribe and draw detections in the electronic-fence page

**Files:**
- Modify: `frontend/src/pages/Channels.vue`
- Modify: `frontend/src/api/tasks.ts` only if a shared WebSocket URL helper is needed

- [ ] **Step 1: Add frontend detection state and lifecycle helpers**

Add maps keyed by `task_id` or `channel_id` for `WebSocket`, latest algorithm detections, reconnect timer, and last message timestamp. Connect only when the electronic-fence preview has a task and the preview is playing; close and clear state when the task changes, preview stops, or the page unmounts.

- [ ] **Step 2: Add the WebSocket URL builder**

Derive the WebSocket origin from the existing API base URL, preserving `ws`/`wss` based on the page protocol. Use `/tasks/{taskId}/detections/ws` and do not hard-code the backend host.

- [ ] **Step 3: Extend canvas rendering**

Keep existing region rendering first. Then draw detections using the payload's source dimensions, scaling `bbox` coordinates into the canvas. Render the algorithm color, class label, and confidence. Clear detection state when an empty result arrives or the message age exceeds the configured short TTL, so stale boxes do not remain on screen.

- [ ] **Step 4: Add bounded reconnect behavior**

Reconnect only while the corresponding fence preview is active, with a short capped backoff. A WebSocket failure must not stop or restart video playback.

- [ ] **Step 5: Run the frontend type check/build**

Run:

```powershell
cd frontend
npm.cmd run build
```

Expected: `vue-tsc` and Vite complete successfully.

### Task 5: End-to-end verification

**Files:**
- Modify: none unless verification exposes a defect

- [ ] **Step 1: Restart backend and frontend services**

Restart the backend container and start the frontend dev/build service using the repository's existing compose/development commands.

- [ ] **Step 2: Start task `8a89a58b` and open the electronic-fence preview**

Verify that the WebRTC video continues to play, the fence polygons remain visible, and the browser establishes `/tasks/8a89a58b/detections/ws`.

- [ ] **Step 3: Verify live metadata and drawing**

Use backend logs and browser devtools to confirm increasing `seq` values. Place a person inside and outside the configured region and verify that the box appears only for the filtered detection and disappears after an empty/expired update.

- [ ] **Step 4: Verify failure isolation**

Close the WebSocket or stop the task and confirm the video remains playable, the overlay clears, and the task worker continues without a WebSocket exception.

