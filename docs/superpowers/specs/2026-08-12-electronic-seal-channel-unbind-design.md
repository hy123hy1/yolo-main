# Electronic-Seal Channel Unbind Design

## Goal

Add an API that releases one channel from electronic-seal ownership and moves it
back to the video-AI `channels` table. The operation removes electronic-seal
runtime configuration while preserving historical events, evidence references,
upload jobs, and online-history samples.

## API Contract

```http
DELETE /electronic-seal/channels/{channel_id}/binding
```

Successful response:

```json
{
  "status": "unbound",
  "channel": {
    "id": "channel-1",
    "stream": "stream-1"
  },
  "preserved": {
    "events": true,
    "evidence": true,
    "online_history": true
  }
}
```

Error responses:

- `404`: no row exists in `electronic_seal_channels` for `channel_id`.
- `409`: `channels` already contains the same ID, or another row owns the same
  non-empty stream.
- `500`: an unexpected storage or runtime error prevents the operation.

The endpoint is not idempotent: calling it again after a successful unbind
returns `404`, because the channel is no longer owned by electronic seal.

## Data Ownership

### Moved back to video AI

The `electronic_seal_channels` row is copied into `channels` with the same ID,
GB ID, device and media-node IDs, name, type, source URL, app, stream, online and
playing flags, recording mode, snapshot fields, WebRTC/HLS fields, timestamps,
and raw JSON. The electronic-seal-only `flv` value is merged into `raw_json`
when it is not already represented there, so a later bind can recover it.

After the copy is flushed successfully, the source row is deleted in the same
database transaction.

### Deleted runtime data

Unbinding deletes data that controls or describes active electronic-seal
processing for the channel:

- `electronic_seal_channel_profiles`
- `electronic_seal_configs`
- `electronic_seal_baselines`
- `electronic_seal_states`
- `electronic_seal_truck_counts`

Any in-memory worker for the channel is stopped as part of manager-level
orchestration. Snapshot and evidence files are not removed from disk.

### Preserved historical data

The following records remain addressable by their immutable `channel_id` even
after the channel changes ownership:

- `electronic_seal_events`
- `electronic_seal_event_index`
- `electronic_seal_truck_departure_events`
- `electronic_seal_room_empty_events`
- `electronic_seal_camera_occlusion_events`
- `electronic_seal_camera_view_shift_events`
- `electronic_seal_upload_jobs`, through their event-index reference
- `electronic_seal_channel_online_history`

Evidence paths stored on event rows remain unchanged. Existing event list,
detail, evidence, handling-status, and upload-retry APIs continue to operate by
event ID or channel ID.

## Schema Change

Historical event rows cannot retain their current `ON DELETE CASCADE` foreign
keys to `electronic_seal_channels`, because deleting the channel would delete
the history. The event and event-detail `channel_id` columns therefore become
indexed logical references without a database foreign key:

- `electronic_seal_events.channel_id`
- `electronic_seal_event_index.channel_id`
- all four algorithm-specific event-detail `channel_id` columns

Runtime tables keep their cascading foreign keys and are deleted with the
electronic-seal channel. Online history already has no channel foreign key and
requires no schema change.

A rerunnable MySQL migration drops only the six named foreign-key constraints
that connect historical tables to `electronic_seal_channels`. The SQLAlchemy
model metadata is updated to match. The migration must discover or use the
actual constraint names used by the existing channel-split migration, and must
be safe when a constraint is already absent.

This design intentionally favors durable audit history over strict referential
integrity to a table that represents current ownership. `channel_id` remains
required and indexed, so historical queries retain their current shape and
performance characteristics.

## Transaction And Concurrency

`ElectronicSealStore.release_channel(channel_id)` performs the database work:

1. Normalize and validate the channel ID.
2. Acquire the existing process-level channel lock.
3. Lock the electronic-seal source row with `SELECT ... FOR UPDATE`.
4. Return not-found if the source row is absent.
5. Reject an existing `channels` row with the same ID.
6. Reject a different `channels` row with the same non-empty stream.
7. Insert and flush the `channels` row.
8. Explicitly delete runtime rows for deterministic SQLite and MySQL behavior.
9. Delete the `electronic_seal_channels` row and commit.
10. Return the serialized video-AI channel.

Insert and delete occur in one transaction. Any failure rolls back both sides,
so the channel cannot disappear from both ownership tables.

The manager removes and stops the in-memory worker before calling the store.
If the store operation fails, it runs `reconcile()` to restore processing from
the unchanged database state. After success, it also reconciles so schedulers
and pending state reflect the new ownership.

## Rebinding Behavior

The existing claim/bind API may later move the same channel back into
`electronic_seal_channels`. New profile, config, baseline, state, and count rows
are created normally. Preserved historical records retain the same channel ID
and appear together with any new events in chronological queries.

## Testing

Store integration tests using SQLite with foreign keys enabled cover:

- moving all channel fields back to `channels`;
- removal of profile, config, baseline, state, and truck-count rows;
- preservation of generic, indexed, and algorithm-specific event rows;
- preservation of upload jobs and online-history rows;
- rejection of missing channels, duplicate IDs, and duplicate streams;
- transaction rollback when a conflict or database error occurs.

Manager tests cover worker shutdown, successful reconciliation, and recovery
reconciliation after a store failure.

API tests cover the success response and `404`/`409` mappings. Schema tests
verify that historical model metadata no longer defines channel foreign keys
and that the rerunnable migration targets all six constraints.

## Out Of Scope

- Deleting physical evidence or snapshot files.
- Deleting historical events through this endpoint.
- Moving video-AI tasks or zones between ownership domains.
- A bulk-unbind endpoint.
- A separate archived-channel table.
