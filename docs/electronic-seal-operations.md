# Electronic Seal Operations

## Scope

The electronic-seal worker is isolated from AI task analysis. It reads `electronic_seal_channels.source_url`, owns one frame broker and one Worker per `channel_id`, and writes only the `electronic_seal_*` tables. Video AI continues to use `channels`; its public channel and task APIs are unchanged.

The ownership unit is a channel record, not a physical camera or a stream URL. Two channel records may intentionally point to the same `source_url`; they are still started and configured independently. For example, the vehicle-entry channel owns `truck_departure`, while the dispatch-room channel owns `room_empty`. A channel is not merged with another channel merely because both records expose the same RTSP address.

## Start and Health

The compose service uses the local `docker-backend` image. Start it from the project root:

```powershell
docker compose -f docker/docker-compose.yml up -d electronic-seal-worker
docker compose -f docker/docker-compose.yml ps electronic-seal-worker
```

The worker API listens on `9001`; its health endpoint is `GET /health`. The frontend reaches it through `/electronic-seal-api/`.

## Logs

```powershell
docker compose logs -f electronic-seal-worker
```

Useful prefixes are `[ElectronicSeal]` for channel workers and `electronic-seal frame` for frame-broker lifecycle errors.

## Resource Settings

The compose service requests `gpus: all` and defaults inference to `cuda:0` through `ELECTRONIC_SEAL_DEVICE`. Its current resource limit is 2 CPUs and 2 GB memory. It does not depend on the AI backend process. Evidence is stored under `backend/data/electronic_seal`.

## Configuration and Recovery

The worker automatically starts every channel profile with `bound=true` when the container starts. The initial role bindings are defined in `backend/configs/electronic_seal_defaults.toml`; the current deployment maps `40bcbc84` to `vehicle_entry` and `8224` to `dispatch_room`. The binding and role are stored in `electronic_seal_channel_profiles`, so a restart does not require manually enabling four algorithm rows.

Algorithm rows are seeded from the channel role and are runtime-active by default. The `enabled` field is retained for database/API compatibility, but it is not a per-algorithm startup switch. Change virtual-line or polygon geometry through the electronic-seal configuration API/frontend; updates are applied to the existing channel Worker without restarting its FFmpeg reader. A changed `electronic_seal_channels.source_url` causes only that channel Worker to restart.

The vehicle-entry line and dispatch-room polygon must be valid before the corresponding algorithm can alarm. Missing or invalid geometry is exposed as `waiting_config`, and produces no event. Camera-health algorithms can establish their baseline from the first valid frame. Camera-health checks persist `last_run_at` so a restart does not run every channel repeatedly in a tight loop.

The main AI task service refuses to start a task on a channel persisted in `electronic_seal_channels` or bound by an electronic-seal profile. Worker stop and lease expiry do not release ownership. Existing task records remain stored and can still be stopped.

## Channel Ownership and WVP Synchronization

Apply `backend/migrations/20260810_electronic_seal_channels.sql` only after a database backup. The migration preserves channel IDs, rejects channels still referenced by video-AI tasks or zones, copies electronic-seal channels into `electronic_seal_channels`, switches electronic-seal foreign keys, verifies the copy, and only then removes the source rows from `channels`.

Apply `backend/migrations/20260811_channel_gb_id.sql` after the channel split migration. It adds the normalized `gb_id` field to both channel tables, indexes it, and backfills it from `raw_json.ext.upstream.channel_db_id`. The field is nullable because manually created or historical non-WVP channels may not have a WVP ID.

`POST /channels/sync-wvp` keeps its existing path and response contract. It resolves WVP `data.stream`; a stream already present in `electronic_seal_channels` updates only that table, while other streams continue through the existing video-AI `channels` path. A new WVP item with no resolvable stream is not assigned to either owner. Offline electronic-seal rows retain their stream because it is the persistent WVP identity.

Electronic-seal clients use `GET /electronic-seal/channels`, `GET /electronic-seal/channels/{channel_id}`, and `POST /electronic-seal/channels/{channel_id}/play`. The play endpoint consumes the electronic-seal channel payload and does not write a compatibility row back into `channels`.

Channel ownership and scene roles are separate operations. `POST /electronic-seal/channels/bind` accepts a non-empty `streams` array (one to 100 WVP stream identifiers), moves each matching row from `channels` to `electronic_seal_channels` in an independent transaction, and does not create a profile or start a Worker. Assign `vehicle_entry` or `dispatch_room` later through `PUT /electronic-seal/channels/{channel_id}/profile`. The older `POST /electronic-seal/channels/claim` endpoint remains available for clients that intentionally want to perform both operations in one request.

After migration, verify ownership and referential integrity:

```sql
SELECT stream, COUNT(*)
FROM electronic_seal_channels
GROUP BY stream
HAVING stream IS NULL OR TRIM(stream) = '' OR COUNT(*) > 1;

SELECT COUNT(*) AS orphan_profiles
FROM electronic_seal_channel_profiles p
LEFT JOIN electronic_seal_channels c ON c.id = p.channel_id
WHERE c.id IS NULL;
```

Upload jobs use an expiring claim lease, a claim token, persisted attempts, and exponential backoff. A timed-out upload is retried without blocking frame capture or AI analysis.

## Event Storage and Counts

The runtime event source is `electronic_seal_event_index` plus the matching
algorithm detail table. New installations do not create
`electronic_seal_events`, and the Worker does not read or write that legacy
table.

Before upgrading an installation that still has legacy rows, back up MySQL and
record these counts:

```sql
SELECT COUNT(*) AS legacy_events FROM electronic_seal_events;
SELECT algorithm_type, COUNT(*) AS event_count
FROM electronic_seal_events
GROUP BY algorithm_type;
SELECT COUNT(*) AS indexed_events FROM electronic_seal_event_index;
SELECT COUNT(*) AS truck_events FROM electronic_seal_truck_departure_events;
SELECT COUNT(*) AS room_empty_events FROM electronic_seal_room_empty_events;
SELECT COUNT(*) AS occlusion_events FROM electronic_seal_camera_occlusion_events;
SELECT COUNT(*) AS view_shift_events FROM electronic_seal_camera_view_shift_events;
```

Run `backend/migrations/20260817_electronic_seal_event_index_backfill.sql`
manually. It is idempotent, fills only missing index/detail rows, does not drop
the legacy table, and finishes with an audit query for unsupported algorithm
types. That audit result must be empty before deploying the cleaned runtime.
Retain `electronic_seal_events` read-only for rollback and manual audit until
the normalized counts have been accepted; dropping it is a separate operator
decision.

After the backfill, verify that every supported index row has its detail row:

```sql
SELECT i.id, i.algorithm_type, i.detail_table
FROM electronic_seal_event_index i
LEFT JOIN electronic_seal_truck_departure_events t
    ON i.algorithm_type = 'truck_departure' AND t.id = i.id
LEFT JOIN electronic_seal_room_empty_events r
    ON i.algorithm_type = 'room_empty' AND r.id = i.id
LEFT JOIN electronic_seal_camera_occlusion_events o
    ON i.algorithm_type = 'camera_occlusion' AND o.id = i.id
LEFT JOIN electronic_seal_camera_view_shift_events v
    ON i.algorithm_type = 'camera_view_shift' AND v.id = i.id
WHERE (i.algorithm_type = 'truck_departure' AND t.id IS NULL)
   OR (i.algorithm_type = 'room_empty' AND r.id IS NULL)
   OR (i.algorithm_type = 'camera_occlusion' AND o.id IS NULL)
   OR (i.algorithm_type = 'camera_view_shift' AND v.id IS NULL);
```

Only a confirmed `inside_to_outside` crossing creates a truck departure event
and increments `out_count`. The count is cumulative and is not reset when the
worker restarts. Rebuilding it from detail events is safe with:

```sql
INSERT INTO electronic_seal_truck_counts
    (channel_id, out_count, last_event_id, last_event_at, updated_at)
SELECT channel_id, COUNT(*), MAX(id), MAX(crossing_timestamp), CURRENT_TIMESTAMP(6)
FROM electronic_seal_truck_departure_events
WHERE direction = 'inside_to_outside'
GROUP BY channel_id
ON DUPLICATE KEY UPDATE
    out_count = VALUES(out_count),
    last_event_id = VALUES(last_event_id),
    last_event_at = VALUES(last_event_at),
    updated_at = VALUES(updated_at);
```

The Java-facing endpoints are `GET /electronic-seal/events`,
`PUT /electronic-seal/events/{event_id}/handling`,
`GET /electronic-seal/channels/{channel_id}/counts/truck_departure`, and
`GET /electronic-seal/channels/{channel_id}/counts/truck_departure/history`.
Event responses expose `snapshot_url` and `video_url` in the form
`/electronic-seal/events/{event_id}/{filename}`; container paths such as
`/app/data/...` are internal and are never integration fields. The legacy
`/electronic-seal/events/{event_id}/evidence?kind=snapshot|video` routes remain
available for compatibility. Event queries
support channel, algorithm, time range, page, and stable time-plus-ID cursor
filters.

## Supervision Upload

Set `ELECTRONIC_SEAL_SUPERVISION_BASE_URL` to enable the background uploader.
Without this setting, departure events and upload jobs are still persisted for
later configuration or manual retry. The default request timeout is 10 seconds
and the total event upload deadline is 30 seconds. A successful upload records
`uploaded_at` and `upload_status=succeeded`; a timeout records `retry` or
`failed` and leaves the event queryable. The idempotency key is
`{event_id}:{target}`, so retrying a job cannot create a second remote event.

## Rollback

Stop only the electronic-seal service to roll back this module:

```powershell
docker compose stop electronic-seal-worker
```

The existing `backend` and `frontend` services can continue running. To restore the module, run `docker compose up -d electronic-seal-worker`.
