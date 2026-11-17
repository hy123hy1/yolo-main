# 2026-07-01 List Read Path Fast Response Design

## Background

Current user-facing list pages must remain fast even when the system has running tasks, active WVP channels, or zone preview workers:

- Channel list
- Task list
- Model list

The current bottleneck is not only the base list API itself. The bigger issue is that list-page initialization still triggers additional per-channel reads and, in some cases, read paths indirectly cause expensive runtime work such as:

- WVP source refresh
- zone preview bootstrap
- browser play URL refresh
- snapshot capture
- file-size refresh on every model row

This creates two bad outcomes:

1. List-page response time becomes unstable when tasks exist.
2. A read-heavy screen can accidentally trigger write/runtime side effects.

## Goals

1. Channel list, task list, and model list must respond quickly and consistently.
2. List-page performance must not degrade simply because tasks exist.
3. `GET` requests must be side-effect free.
4. Runtime actions must only happen through explicit action endpoints or background jobs.
5. Existing UI behavior should be preserved as much as possible while moving expensive work to on-demand paths.

## Non-Goals

1. This phase does not redesign task start/stop semantics.
2. This phase does not redesign zone preview generation itself.
3. This phase does not change core event-detection logic.
4. This phase does not attempt to eliminate every follow-up request, only the unnecessary eager ones.

## Approaches Considered

### Approach A: Strict Read/Write Separation

Keep list endpoints as pure DB summary reads, and move all expensive runtime work to explicit action flows or on-demand detail calls.

Pros:

- Most predictable performance
- Clear architecture boundary
- Easiest to reason about and test
- Prevents future “page open makes system busy” regressions

Cons:

- Requires front-end loading strategy adjustments
- Some detail data must be fetched later instead of eagerly

### Approach B: Keep Existing Behavior but Add Caching/Rate Limits

Retain current eager reads and auto-bootstrap behavior, but reduce pain with caching, throttling, and concurrency guards.

Pros:

- Smaller surface change
- Less front-end restructuring

Cons:

- Read requests still trigger write/runtime behavior
- Performance remains workload-sensitive
- Harder to guarantee stable list latency

### Recommendation

Use Approach A.

The core requirement is stable list responsiveness. That is easiest to guarantee only if list reads are pure and cheap.

## Design

### 1. Hard Rule: GET Endpoints Must Be Side-Effect Free

The following categories must never start runtime work, mutate DB state, refresh upstream sources, or touch media pipelines:

- channel list reads
- task list reads
- model list reads
- zone preview status reads
- browser play URL reads

Allowed behavior for `GET`:

- read DB rows
- assemble response DTOs
- optionally read already-cached values already persisted in DB

Disallowed behavior for `GET`:

- WVP `/play` refresh
- ZLM media probing
- FFmpeg/zone preview bootstrap
- snapshot capture
- DB state correction writes

### 2. Channel List Path

`GET /channels` remains the existing route, but its service path must stay summary-only:

- read `ChannelMeta`
- map DB fields to response
- return persisted cached values such as `webrtc_api`, `hls`, `snapshot_url`, `snapshot_path`, `ext.task_preview`, `ext.zone_preview`

It must not:

- refresh WVP source URL
- inspect live media state
- start channel proxy
- start zone preview

### 3. Task List Path

`GET /tasks` should remain DB-first, but become lighter:

- read `TaskMeta`
- join or batch-load only the minimum summary fields needed for list display
- avoid expanding every task into full algorithm-region detail during list rendering

List payload should be summary-oriented:

- task id
- name
- channel_id
- source_url
- frame_interval
- status
- created_at / updated_at
- algorithm_count
- region_count
- runtime summary

Detailed algorithm and region content should remain on dedicated detail APIs:

- `GET /tasks/{task_id}/algorithms`
- `GET /tasks/{task_id}/algorithms/{algorithm_id}/regions`

### 4. Model List Path

`GET /models` should read `ModelMetaRecord` only and return cached metadata.

It must not recalculate file size on every row for list rendering. File size should be:

- persisted at upload/import/edit time, or
- refreshed only in model detail view / explicit maintenance flow

Model detail APIs may still enrich data on demand, but list APIs must stay lightweight.

### 5. Zone Preview Status and Browser Play URL

These two read paths currently risk becoming hidden bootstrap entry points. They must become read-only views over already-prepared state:

- `GET /channels/{channel_id}/zone-preview/status`
- `GET /channels/{channel_id}/fence/browser-play-url`

They should only:

- read persisted `task_preview` / `zone_preview` state
- read cached `webrtc_api` / `hls`
- return the best-known status

They must not:

- call task-preview bootstrap
- call `play`
- call `start_zone_preview`
- refresh browser URLs by spinning runtime work

Zone preview startup remains explicit:

- task start warmup path
- explicit play/start action path

### 6. Frontend Channel Page Loading Strategy

Current channel-page initialization is too eager. Initial load must be narrowed to:

- `GET /channels`
- `GET /models`
- `GET /media-nodes`

Do not eagerly fan out per-channel requests for all channels on page open.

The following become on-demand:

- zones
- AI status
- zone preview status
- browser play URL
- snapshot refresh

Suggested triggers:

- when a channel card is selected
- when a detail panel is opened
- when the user clicks play
- when the user opens region/AI configuration

### 7. Task Start Path

`POST /tasks/{task_id}/start` remains the only automatic runtime warmup trigger in this phase.

It may:

- validate task
- start task analysis worker
- schedule preview warmup
- schedule background source refresh if needed

But list-page reads must not re-trigger those same actions.

### 8. Cached State as the Contract

To keep read paths fast, action paths and background jobs must persist enough state for UI rendering:

- channel cached play URLs
- task preview mode/status
- zone preview status/owner/zone count
- snapshot path/url
- task runtime summary
- model summary metadata including file size if needed

This means write paths become responsible for cache freshness, and read paths simply display cached state.

## API Compatibility Strategy

To minimize frontend breakage:

1. Keep existing endpoint URLs where possible.
2. Preserve current top-level response shapes unless there is a strong reason not to.
3. Reduce backend work behind the same route before introducing new routes.

If a lighter summary-only contract is needed later, add an optional compatibility flag such as:

- `?summary=true`

But phase 1 should prefer reducing hidden work behind existing endpoints first.

## Implementation Plan Scope

Phase 1 scope:

1. Make `GET /channels` side-effect free.
2. Make `GET /tasks` list path summary-oriented.
3. Make `GET /models` list path DB-summary only.
4. Remove auto-bootstrap behavior from read-only zone preview endpoints.
5. Change channel-page initialization to stop eager per-channel fan-out.

Phase 2 scope:

1. Add optional summary/detail split flags if needed.
2. Add background cache refresh policy for stale read models.
3. Add latency instrumentation for each list endpoint and channel-page load stages.

## Risks

1. Frontend may currently rely on eager-loaded detail data.
2. Some UI states may temporarily show cached values instead of freshly refreshed runtime values.
3. Removing auto-bootstrap from read endpoints may require explicit front-end trigger updates.

## Mitigations

1. Keep endpoint shapes stable where possible.
2. Move expensive calls to explicit user actions rather than removing capability.
3. Add targeted regressions for:
   - list reads do not trigger runtime side effects
   - tasks can still start and warm previews
   - manual play still works

## Validation

Success criteria:

1. `GET /channels`, `GET /tasks`, and `GET /models` remain fast with running tasks present.
2. Opening the channel page no longer triggers per-channel zone preview startup.
3. Task existence alone does not slow channel-page initial render.
4. Manual preview/play flows still work correctly.
