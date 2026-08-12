# Video AI Task Playback Decoupling Design

## Goal

Video AI tasks must focus on detection only. A task reads the RTSP URL already
stored in `channels.source_url`, decodes frames for inference, and applies its
algorithm and region configuration. Java owns WVP playback requests, stream
proxying, transcoding, pushing, and browser playback information.

Detection reading RTSP frames is explicitly in scope. Creating extra playback
pipelines for users is out of scope.

## Selected Approach

Fully decouple task lifecycle operations from playback while retaining the
existing manual channel playback APIs for compatibility.

The task service must not call WVP play, ZLM stream proxy, browser transcode,
channel playback, or zone-preview operations during task start, restart,
startup restoration, stop, or region updates.

## Task Data Flow

1. Load the task and its bound channel.
2. Copy `channels.source_url` and source type into the task execution snapshot.
3. Reject task start or restart when a bound channel has no non-empty RTSP URL.
4. Start the analysis worker with the task RTSP URL.
5. Decode frames, run enabled algorithms, apply regions, and persist detection
   state and events through the existing analysis pipeline.

Task execution must not refresh the source by calling the WVP
`/api/common/channel/play` endpoint. The Java/synchronization side is
responsible for maintaining a usable RTSP URL in `channels.source_url`.

## Lifecycle Changes

### Start and Restart

`POST /tasks/{task_id}/start` and task restart only validate the task, hydrate
the source from the channel row, and start or restart analysis. They do not
warm up channel playback or zone preview.

Their response remains the analysis status (`task_id`, `status`, and timing
fields). No HLS, WebRTC, HTTP-FLV, proxy, or preview URL is returned.

### Startup Restoration

Restored running tasks use the RTSP URL stored in `channels.source_url`. The
restore flow must not schedule WVP source refresh or preview warmup jobs.

### Region Updates

Updating task or algorithm regions persists detection configuration only. The
existing analysis configuration refresh mechanism applies new regions to the
running task. Region updates do not start or refresh a zone-preview stream.

### Stop

Stopping a task stops the analysis worker only. It does not stop a channel
proxy, browser transcode, or zone-preview process because the task no longer
owns those playback resources.

## Compatibility Surface

The following channel playback APIs remain available for manual use and Java
integration, but the task lifecycle does not call them:

- `POST /channels/{channel_id}/play`
- `POST /channels/{channel_id}/stop`
- `GET /channels/{channel_id}/play-url`
- Existing zone-preview start, stop, status, and browser-play-url APIs

Playback service implementations are not deleted in this change.

The current frontend task start and stop actions must stop requesting fence or
zone-preview state as a side effect. Any future playback UI will consume Java
playback APIs independently from task state.

## Error Handling

- A missing bound channel keeps the existing not-found behavior.
- An empty or non-RTSP `channels.source_url` rejects start and restart with a
  clear validation error before an analysis worker is created.
- RTSP open, decode, or reconnect failures remain analysis runtime errors and
  are reported through the existing task status path.
- Playback service failures cannot fail task lifecycle operations because the
  lifecycle no longer invokes playback services.

## Resource Model

Each running task owns one detection input/decode pipeline. It does not create
an additional original-stream proxy, browser transcode, or region-overlay
preview pipeline. Multiple tasks therefore consume resources for inference and
their required RTSP decoders only.

## Verification

Tests must prove that:

- start and restart read the stored channel RTSP without calling WVP refresh;
- start and restart never call task preview warmup or channel playback;
- startup restoration never schedules source refresh or preview warmup;
- region updates do not schedule preview warmup;
- stop never calls channel or zone-preview stop methods;
- missing, empty, and non-RTSP channel sources are rejected before worker start;
- existing manual playback API tests continue to pass;
- analysis still receives the RTSP URL plus algorithm and region configuration.
