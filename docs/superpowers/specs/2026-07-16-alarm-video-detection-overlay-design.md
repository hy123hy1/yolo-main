# Alarm Video Detection Overlay Design

## Goal

Add detection overlays to generated alarm videos so the event frame shows the detected label, confidence, and bounding box.

## Scope

- Keep the existing 20-second alarm-video window and asynchronous encoding.
- Reuse `AnalysisService._annotate_frame()` for label, confidence, bounding-box, and existing region rendering behavior.
- Carry the event inference timestamp from the task worker into the alarm-video job.
- Annotate only the video frame whose timestamp matches the event inference frame.
- Leave all other video frames unchanged because the current alarm-video capture does not run inference on every frame.

## Data Flow

1. The task worker detects an event on `now_ts` and stores the event frame in `pre_event_buffer`.
2. The worker submits the alarm-video job with `event_timestamp=now_ts` and the event detection payload.
3. The asynchronous job collects post-event frames and builds the clip as before.
4. `_save_alarm_video()` compares each frame timestamp with `event_timestamp`; only the matching frame is passed through `_annotate_frame()`.
5. The resulting MP4 contains the original before/after frames plus a correctly annotated event frame.

## Compatibility

- Existing callers that do not pass an annotation timestamp continue to encode raw frames.
- Snapshot generation remains unchanged.
- Detection, region filtering, preview, and event persistence remain unchanged.

## Verification

- Unit test that the matching event frame is annotated with the red bbox and non-event frames remain unchanged.
- Unit test that the alarm-video job forwards the event timestamp.
- Run the focused analysis-service tests, then the full backend test suite.
