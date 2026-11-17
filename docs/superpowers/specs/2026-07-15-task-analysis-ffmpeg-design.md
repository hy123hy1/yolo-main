# Task Analysis FFmpeg Design

## Goal

Make task analysis use one FFmpeg-based input path for RTSP, HTTP, and file sources so stream probing and frame decoding cannot disagree about the input format or frame dimensions. Keep OpenCV for image processing and JPEG/video encoding only.

## Evidence

- The last alarm on channel `8224` was at `2026-07-11 10:34:16` from the old channel-level analysis path.
- The current task `8a89a58b` receives frames and runs inference, but at the screenshot time the raw result was `suitcase` at about `0.284`; label filtering then produced zero detections.
- The current task path already uses `FFmpegCapture` after `_probe_task_stream`, but `_probe_task_stream` still calls `cv2.VideoCapture` and non-RTSP sources fall back to OpenCV.
- The current worktree contains an uncommitted migration from channel-level analysis to task-level analysis. These existing changes must be preserved.

## Design

1. Add FFmpeg/ffprobe probing in the task analysis service. The probe returns width and height and fails with a useful error when the source cannot be opened or has no video stream.
2. Make `_build_task_capture` return `FFmpegCapture` for every supported source URL. The capture emits `bgr24` NumPy frames with the probed dimensions.
3. Keep OpenCV calls used for YOLO image operations, annotation, JPEG encoding, and alarm video encoding. Remove OpenCV stream capture from the task analysis path.
4. Add runtime capture metadata to the existing task debug logs so verification can identify the backend and decoded frame size.
5. Do not change label filtering, confidence semantics, region matching, cooldown behavior, or event payloads. A model result of `suitcase` must remain non-alarming for a `person` rule.

## Verification

- Unit tests prove the task capture builder does not call `cv2.VideoCapture` and uses FFmpeg probing/capture.
- Existing analysis and task event tests pass.
- After restart, task `8a89a58b` is started again and logs are checked for FFmpeg frame dimensions, `person` detections, and `event_created`.
- The historical audit is reported separately from the code change and does not rewrite unrelated worktree changes.
