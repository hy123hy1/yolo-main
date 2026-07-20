# WVP Source URL Task Rebinding Design

## Goal

When WVP channels are synchronized again, existing tasks must remain bound to the same camera without requiring task recreation. The binding key is the channel `source_url`, while the local `channel_id` may change as channel records are reconciled.

The synchronization result must also contain unique local channel IDs. Different WVP channels must not reuse one local ID merely because they belong to the same WVP device.

## Current Problem

Tasks store both `channel_id` and `source_url`. WVP synchronization currently matches existing channels by several fields, including `device_id`. Two different WVP channels under the same device can therefore select the same local channel record. The sync response can contain duplicate local IDs, while persistence keeps only one record because the local channel ID is a primary key.

The frontend then reloads persisted channels and displays fewer records than the `synced` count. Duplicate IDs also collide in Vue keys and per-channel playback/task state maps.

## Matching Contract

### Task binding

Task rebinding uses a canonical form of `source_url`:

- trim surrounding whitespace;
- normalize the URL scheme and hostname casing;
- normalize the URL path and trailing slash representation;
- sort query parameters deterministically;
- preserve query parameters that identify the stream variant unless they are explicitly classified as transport-only;
- do not use channel name, device name, or fuzzy text matching as a task binding fallback.

The canonical form is used only for comparison. The task and channel retain the latest effective URL returned by WVP.

### Channel identity

Channel reconciliation checks source URL identity before broad historical matching. A WVP upstream identity uses the upstream channel identifier together with the device identity when a source URL is unavailable.

Matching by `device_id` alone must not merge two WVP channels.

## Synchronization Flow

For each WVP channel:

1. Read the remote channel and obtain the latest play information when available.
2. Build the effective `source_url`.
3. Find an existing local channel by canonical source URL.
4. If no URL match exists, use the stable WVP upstream identity to find a historical record.
5. If neither match exists, create a new unique local channel ID.
6. Persist exactly one local channel record for the WVP channel.
7. Rebind tasks whose canonical `source_url` matches the incoming channel URL.
8. Update the task's `channel_id`, latest `source_url`, and `channel_name` while preserving task ID, algorithms, regions, runtime configuration, events, recordings, and metrics.

The sync response must be built from the unique persisted channel results. Its `synced` count must equal the number of unique channel records represented by `items`.

## Existing Task Rebinding

Before replacing a local channel identity, collect tasks associated with the old channel and their stored source URLs. After the incoming channel is assigned its canonical local ID:

- update matching task JSON and normalized metadata rows;
- update task source URL and channel name from the latest channel data;
- update channel references on events, recordings, and metric records when the local channel ID changes;
- keep the task runtime state unchanged;
- avoid rebinding a task to a channel with a different canonical source URL.

If multiple tasks match one source URL, do not overwrite one task with another. Keep the current associations and record a synchronization conflict for inspection.

If a channel has no source URL yet, task rebinding is deferred until the background play-info refresh produces one.

## Duplicate ID Handling

The synchronization process must not append multiple payloads with the same local ID. Before persistence and response construction:

- detect an ID collision between different upstream channel identities;
- keep the existing local ID only for the source URL or upstream identity it belongs to;
- allocate a new local ID for the other channel;
- ensure the response list, JSON cache, and normalized database all have one record per local ID.

The matching logic must preserve task-bound local IDs when their source URL matches, so existing task links remain stable where possible.

## Failure Handling

- A failed WVP play-info request must not delete an existing task.
- A missing source URL must leave the task intact and mark rebinding as pending.
- A source URL collision must be reported rather than silently choosing one task.
- Metadata and JSON persistence must use the existing synchronization path so both views converge.
- Repeated synchronization must be idempotent: the same WVP response should not create new channel IDs or duplicate task references.

## Testing

Add focused tests for:

- canonical source URL matching with equivalent URL formatting;
- source URL changes that should and should not match;
- two WVP channels sharing one device but receiving unique local IDs;
- an existing task rebinding from an old channel ID to the synchronized channel;
- preservation of task algorithms, regions, runtime, events, recordings, and metrics;
- deferred rebinding when play information has no source URL;
- ambiguous source URL matches producing a conflict without overwriting;
- repeated synchronization being idempotent;
- sync response count matching unique persisted channel records.

## Acceptance Criteria

1. A task created before WVP synchronization remains usable after synchronization without task recreation when its source URL matches.
2. Two WVP channels under the same device never appear with the same local channel ID.
3. `synced` equals the number of unique channel items returned and persisted.
4. Repeated synchronization does not increase channel or task counts unexpectedly.
5. A source URL mismatch does not silently rebind a task to another camera.
