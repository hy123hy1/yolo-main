# Metadata Database Phase 1

Phase 1 goal:

- keep the current file-based storage path working
- add a structured metadata database index
- allow SQLite by default, with optional MySQL/PostgreSQL through DSN

Current synced scope:

- `devices`
- `media_nodes`
- `channels`
- `channel_zones`
- `channel_ai_configs`
- `models`
- `events`
- `recordings`

Design principles:

- files remain the source-compatible storage format
- database stores structured fields plus `raw_json`
- structured fields support filtering, indexing, and analytics
- `raw_json` preserves compatibility with old and future fields

Current mode:

- default: SQLite file database
- optional: external MySQL / PostgreSQL
- backend auto-creates tables and syncs metadata on startup

Phase 2 suggestions:

- switch list queries to database-first reads
- move event filtering, aggregation, and pagination fully into SQL
- continue syncing training jobs, datasets, and annotation metadata
- add future tables for AI event rules, GB28181, ONVIF, and related capabilities
