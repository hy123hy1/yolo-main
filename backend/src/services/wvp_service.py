from __future__ import annotations

import json
import logging
import copy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from src.core.settings import settings
from src.db.database import SessionLocal
from src.db.models import (
    ChannelMeta,
    ElectronicSealChannelMeta,
    EventMeta,
    RecordingMeta,
    TaskAlgorithmMetricHistoryMeta,
    TaskAlgorithmMetricMeta,
    TaskMeta,
)
from src.services.metadata_db_sync import delete_json_document
from src.services.platform_storage import generate_id, list_json_items, load_json, now_iso, save_json


logger = logging.getLogger(__name__)


class WVPService:
    def __init__(self) -> None:
        self.channels_dir: Path = settings.CHANNELS_DIR
        self.tasks_dir: Path = settings.TASKS_DIR

    def enabled(self) -> bool:
        return bool(settings.WVP_ENABLED and settings.WVP_API_BASE)

    def _build_url(self, path: str, params: dict[str, Any] | None = None) -> str:
        base = settings.WVP_API_BASE.rstrip("/")
        route = path if path.startswith("/") else f"/{path}"
        if not params:
            return f"{base}{route}"
        query = urlencode({key: value for key, value in params.items() if value is not None})
        return f"{base}{route}?{query}"

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self._build_url(path, params)
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=20) as response:
            payload = response.read()
        if not payload:
            return {}
        return json.loads(payload.decode("utf-8"))

    def list_remote_channels(self, page: int = 1, count: int = 100, query: str | None = None) -> dict[str, Any]:
        if not self.enabled():
            return {"items": [], "total": 0, "enabled": False}

        payload = self._request_json(
            settings.WVP_CHANNEL_LIST_PATH,
            {
                "page": page,
                "count": count,
                "query": query,
            },
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        items = data.get("list") or data.get("items") or payload.get("list") or payload.get("items") or []
        total = data.get("total") or payload.get("total") or len(items)
        return {
            "items": items,
            "total": total,
            "enabled": True,
        }

    def get_play_info(self, channel_db_id: str | int) -> dict[str, Any]:
        if not self.enabled():
            return {}
        payload = self._request_json(
            settings.WVP_CHANNEL_PLAY_PATH,
            {
                "channelId": channel_db_id,
            },
        )
        return payload.get("data") if isinstance(payload.get("data"), dict) else {}

    def _build_channel_item_from_row(self, row: ChannelMeta) -> dict[str, Any]:
        item = dict(row.raw_json or {})
        item.setdefault("id", row.id)
        item.pop("ai_enabled", None)
        if row.gb_id is not None:
            item["gb_id"] = row.gb_id
        item["stream"] = row.stream
        item["source_url"] = row.source_url
        item["flv"] = getattr(row, "flv", None)
        item["name"] = row.name
        item["type"] = row.type
        item["is_online"] = row.is_online
        item["is_playing"] = row.is_playing
        item["record_mode"] = row.record_mode
        item["snapshot_url"] = row.snapshot_url
        item["snapshot_path"] = row.snapshot_path
        item["media_node_id"] = row.media_node_id
        if isinstance(row, ElectronicSealChannelMeta):
            item["flv"] = row.flv
        item.setdefault("ext", {})
        item["ext"].pop("ai_config", None)
        return item

    def _list_existing_wvp_channels(self) -> list[dict[str, Any]]:
        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                rows = session.query(ChannelMeta).filter(ChannelMeta.type == "wvp").all()
            return [self._build_channel_item_from_row(row) for row in rows]

        return [item for item in list_json_items(self.channels_dir) if item.get("type") == "wvp"]

    def _list_existing_electronic_seal_channels(self) -> list[dict[str, Any]]:
        if not settings.METADATA_DB_ENABLED:
            return []
        with SessionLocal() as session:
            rows = session.query(ElectronicSealChannelMeta).all()
        return [self._build_channel_item_from_row(row) for row in rows]

    def _update_electronic_seal_channel(
        self,
        channel_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not settings.METADATA_DB_ENABLED:
            return None
        with SessionLocal() as session:
            row = session.get(ElectronicSealChannelMeta, str(channel_id).strip())
            if row is None:
                return None
            for field in [
                "device_id",
                "gb_id",
                "media_node_id",
                "name",
                "type",
                "source_url",
                "app",
                "stream",
                "is_online",
                "is_playing",
                "record_mode",
                "webrtc_api",
                "flv",
                "hls",
            ]:
                if field in payload:
                    if field == "flv" and row.flv:
                        continue
                    setattr(row, field, copy.deepcopy(payload[field]))
            row.updated_at = datetime.now()
            raw_payload = copy.deepcopy(payload)
            raw_payload["snapshot_url"] = row.snapshot_url
            raw_payload["snapshot_path"] = row.snapshot_path
            row.raw_json = raw_payload
            session.commit()
            session.refresh(row)
            return self._build_channel_item_from_row(row)

    def _normalize_match_text(self, value: Any) -> str:
        return str(value or "").strip().casefold()

    def _find_existing_wvp_candidates(
        self,
        stream_id: str,
        upstream_channel_id: str,
        device_id: str,
        channel_name: str,
        existing_items: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        normalized_stream_id = str(stream_id or "").strip()
        normalized_upstream_channel_id = str(upstream_channel_id or "").strip()
        normalized_device_id = str(device_id or "").strip()
        normalized_channel_name = self._normalize_match_text(channel_name)

        for item in existing_items if existing_items is not None else self._list_existing_wvp_channels():
            channel_id = str(item.get("id") or "").strip()
            if not channel_id:
                continue
            upstream = ((item.get("ext") or {}).get("upstream") or {})
            item_stream = str(item.get("stream") or "").strip()
            item_device_id = str(item.get("device_id") or upstream.get("device_id") or "").strip()
            item_channel_name = self._normalize_match_text(item.get("name") or upstream.get("name"))
            matches_stream = bool(normalized_stream_id) and (
                item_stream == normalized_stream_id or channel_id == normalized_stream_id
            )
            matches_upstream = (
                upstream.get("platform") == "wvp"
                and bool(normalized_upstream_channel_id)
                and str(upstream.get("channel_db_id") or "").strip() == normalized_upstream_channel_id
            )
            matches_device = bool(normalized_device_id) and item_device_id == normalized_device_id
            matches_name = not normalized_device_id and bool(normalized_channel_name) and (
                item_channel_name == normalized_channel_name
            )
            if matches_stream or matches_upstream or matches_device or matches_name:
                candidates[channel_id] = item

        return list(candidates.values())

    def _merge_existing_candidate_data(
        self,
        primary: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if primary is None:
            return None

        merged = dict(primary)
        merged_ext = dict(merged.get("ext") or {})
        merged_upstream = dict(merged_ext.get("upstream") or {})
        primary_id = str(primary.get("id") or "").strip()

        def _richness_rank(item: dict[str, Any]) -> tuple[int, int, int, int, str, str]:
            upstream = ((item.get("ext") or {}).get("upstream") or {})
            item_stream = str(item.get("stream") or "").strip()
            play_info = upstream.get("play_info") or {}
            updated_at = str(item.get("updated_at") or item.get("created_at") or "")
            return (
                0 if str(item.get("source_url") or "").strip() else 1,
                0 if item_stream else 1,
                0 if play_info else 1,
                0 if str(item.get("snapshot_url") or "").strip() else 1,
                updated_at,
                str(item.get("id") or "").strip(),
            )

        for item in sorted(candidates, key=_richness_rank):
            channel_id = str(item.get("id") or "").strip()
            if not channel_id or channel_id == primary_id:
                continue

            for field in [
                "source_url",
                "app",
                "stream",
                "media_node_id",
                "snapshot_url",
                "snapshot_path",
                "webrtc_api",
                "hls",
            ]:
                merged_value = merged.get(field)
                item_value = item.get(field)
                if merged_value in (None, "", []) and item_value not in (None, "", []):
                    merged[field] = item_value

            item_upstream = ((item.get("ext") or {}).get("upstream") or {})
            for field in ["source_type", "play_info", "channel_info", "device_id", "name"]:
                merged_value = merged_upstream.get(field)
                item_value = item_upstream.get(field)
                if merged_value in (None, "", []) and item_value not in (None, "", []):
                    merged_upstream[field] = item_value

        merged_ext["upstream"] = merged_upstream
        merged["ext"] = merged_ext
        return merged

    def _find_bound_channel_ids(self, channel_ids: set[str]) -> set[str]:
        normalized_ids = {str(channel_id or "").strip() for channel_id in channel_ids if str(channel_id or "").strip()}
        if not normalized_ids:
            return set()

        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                rows = (
                    session.query(TaskMeta.channel_id)
                    .filter(TaskMeta.channel_id.in_(sorted(normalized_ids)))
                    .all()
                )
            return {
                str(row[0]).strip()
                for row in rows
                if row and str(row[0] or "").strip() in normalized_ids
            }

        bound_ids: set[str] = set()
        for task in list_json_items(settings.TASKS_DIR):
            channel_id = str(task.get("channel_id") or "").strip()
            if channel_id in normalized_ids:
                bound_ids.add(channel_id)
        return bound_ids

    def _select_canonical_existing_channel(
        self,
        candidates: list[dict[str, Any]],
        stream_id: str,
        bound_channel_ids: set[str],
    ) -> dict[str, Any] | None:
        if not candidates:
            return None

        normalized_stream_id = str(stream_id or "").strip()

        def _rank(item: dict[str, Any]) -> tuple[int, int, int, int, str, str]:
            channel_id = str(item.get("id") or "").strip()
            item_stream = str(item.get("stream") or "").strip()
            is_bound = channel_id in bound_channel_ids
            stream_match = bool(normalized_stream_id) and (
                channel_id == normalized_stream_id or item_stream == normalized_stream_id
            )
            has_source = bool(str(item.get("source_url") or "").strip())
            has_stream = bool(item_stream)
            updated_at = str(item.get("updated_at") or item.get("created_at") or "")
            return (
                0 if is_bound else 1,
                0 if stream_match else 1,
                0 if has_source else 1,
                0 if has_stream else 1,
                updated_at,
                channel_id,
            )

        return sorted(candidates, key=_rank)[0]

    def _delete_duplicate_channels(
        self,
        candidates: list[dict[str, Any]],
        keep_channel_id: str,
        protected_channel_ids: set[str],
    ) -> None:
        normalized_keep_id = str(keep_channel_id or "").strip()
        normalized_protected_ids = {
            str(channel_id or "").strip()
            for channel_id in protected_channel_ids
            if str(channel_id or "").strip()
        }

        for item in candidates:
            channel_id = str(item.get("id") or "").strip()
            if not channel_id or channel_id == normalized_keep_id or channel_id in normalized_protected_ids:
                continue
            path = self.channels_dir / f"{channel_id}.json"
            path.unlink(missing_ok=True)
            delete_json_document(path, channel_id)

    def _rebind_duplicate_channel_references(
        self,
        keep_channel_id: str,
        keep_channel_name: str | None,
        duplicate_channel_ids: set[str],
    ) -> set[str]:
        normalized_keep_id = str(keep_channel_id or "").strip()
        normalized_duplicate_ids = {
            str(channel_id or "").strip()
            for channel_id in duplicate_channel_ids
            if str(channel_id or "").strip() and str(channel_id or "").strip() != normalized_keep_id
        }
        if not normalized_keep_id or not normalized_duplicate_ids:
            return set()

        rebound_channel_ids: set[str] = set()
        current_time = now_iso()

        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                task_rows = (
                    session.query(TaskMeta)
                    .filter(TaskMeta.channel_id.in_(sorted(normalized_duplicate_ids)))
                    .all()
                )

                for row in task_rows:
                    old_channel_id = str(row.channel_id or "").strip()
                    if not old_channel_id or old_channel_id == normalized_keep_id:
                        continue
                    payload = dict(row.raw_json or {})
                    payload["channel_id"] = normalized_keep_id
                    if keep_channel_name:
                        payload["channel_name"] = keep_channel_name
                    payload["updated_at"] = current_time
                    row.channel_id = normalized_keep_id
                    if keep_channel_name:
                        row.channel_name = keep_channel_name
                    row.updated_at = datetime.now()
                    row.raw_json = payload
                    rebound_channel_ids.add(old_channel_id)

                if rebound_channel_ids:
                    session.query(EventMeta).filter(
                        EventMeta.channel_id.in_(sorted(rebound_channel_ids))
                    ).update(
                        {
                            EventMeta.channel_id: normalized_keep_id,
                        },
                        synchronize_session=False,
                    )
                    session.query(RecordingMeta).filter(
                        RecordingMeta.channel_id.in_(sorted(rebound_channel_ids))
                    ).update(
                        {
                            RecordingMeta.channel_id: normalized_keep_id,
                        },
                        synchronize_session=False,
                    )
                    session.query(TaskAlgorithmMetricMeta).filter(
                        TaskAlgorithmMetricMeta.channel_id.in_(sorted(rebound_channel_ids))
                    ).update(
                        {
                            TaskAlgorithmMetricMeta.channel_id: normalized_keep_id,
                        },
                        synchronize_session=False,
                    )
                    session.query(TaskAlgorithmMetricHistoryMeta).filter(
                        TaskAlgorithmMetricHistoryMeta.channel_id.in_(sorted(rebound_channel_ids))
                    ).update(
                        {
                            TaskAlgorithmMetricHistoryMeta.channel_id: normalized_keep_id,
                        },
                        synchronize_session=False,
                    )
                session.commit()

            for task_path in self.tasks_dir.glob("*.json"):
                try:
                    payload = load_json(task_path)
                except Exception:
                    continue
                old_channel_id = str(payload.get("channel_id") or "").strip()
                if old_channel_id not in rebound_channel_ids:
                    continue
                payload["channel_id"] = normalized_keep_id
                if keep_channel_name:
                    payload["channel_name"] = keep_channel_name
                payload["updated_at"] = current_time
                save_json(task_path, payload, sync_metadata=False)
            return rebound_channel_ids

        for task_path in self.tasks_dir.glob("*.json"):
            try:
                payload = load_json(task_path)
            except Exception:
                continue
            old_channel_id = str(payload.get("channel_id") or "").strip()
            if old_channel_id not in normalized_duplicate_ids:
                continue
            payload["channel_id"] = normalized_keep_id
            if keep_channel_name:
                payload["channel_name"] = keep_channel_name
            payload["updated_at"] = current_time
            save_json(task_path, payload, sync_metadata=False)
            rebound_channel_ids.add(old_channel_id)
        return rebound_channel_ids

    def _extract_upstream_channel_id(self, remote_item: dict[str, Any]) -> str:
        return str(
            remote_item.get("id")
            or remote_item.get("channelId")
            or remote_item.get("channel_id")
            or remote_item.get("gbId")
            or remote_item.get("gb_id")
            or ""
        )

    def _pick_source_url(self, play_info: dict[str, Any]) -> tuple[str, str]:
        for key, source_type in [
            ("rtsp", "rtsp"),
            ("rtmp", "rtmp"),
            ("hls", "hls"),
            ("flv", "flv"),
            ("fmp4", "fmp4"),
            ("rtc", "rtc"),
        ]:
            value = play_info.get(key)
            if value:
                return str(value), source_type
        return "", "unknown"

    def _has_playable_source(self, play_info: dict[str, Any]) -> bool:
        source_url, _ = self._pick_source_url(play_info)
        return bool(source_url)

    def _clear_offline_playback_fields(
        self,
        payload: dict[str, Any],
        *,
        preserve_stream: bool = False,
    ) -> dict[str, Any]:
        payload["source_url"] = None
        payload["source_type"] = "unknown"
        if not preserve_stream:
            payload["stream"] = ""
        payload["is_online"] = False
        payload["is_playing"] = False
        payload["webrtc_api"] = None
        payload["hls"] = None
        upstream = payload.setdefault("ext", {}).setdefault("upstream", {})
        upstream["online"] = False
        upstream["source_type"] = "unknown"
        upstream["play_info"] = {}
        return payload

    def _is_remote_status_off(
        self,
        remote_item: dict[str, Any],
        play_info: dict[str, Any] | None = None,
    ) -> bool:
        falsey_text = {"off", "offline", "false", "0", "no"}
        media_info = (play_info or {}).get("mediaInfo") or {}
        for source in [remote_item, play_info or {}, media_info]:
            for key in ["gbStatus", "status", "online", "isOnline", "gbOnline"]:
                value = source.get(key)
                if value is False:
                    return True
                if isinstance(value, (int, float)) and value == 0:
                    return True
                if isinstance(value, str) and value.strip().lower() in falsey_text:
                    return True
        return False

    def _extract_stream_id(self, play_info: dict[str, Any], existing: dict[str, Any] | None = None) -> str:
        media_info = play_info.get("mediaInfo") or {}
        return str(
            play_info.get("stream")
            or play_info.get("streamId")
            or play_info.get("stream_id")
            or media_info.get("stream")
            or media_info.get("streamId")
            or media_info.get("stream_id")
            or (existing or {}).get("stream")
            or ""
        ).strip()

    def _is_remote_online(self, remote_item: dict[str, Any], play_info: dict[str, Any]) -> bool:
        if self._is_remote_status_off(remote_item, play_info):
            return False
        media_info = play_info.get("mediaInfo") or {}
        truthy_text = {"on", "online", "true", "1", "yes"}
        status_values = [
            media_info.get("online"),
            media_info.get("status"),
            play_info.get("online"),
            play_info.get("status"),
            remote_item.get("online"),
            remote_item.get("isOnline"),
            remote_item.get("gbOnline"),
            remote_item.get("status"),
            remote_item.get("gbStatus"),
        ]
        for value in status_values:
            if value is True:
                return True
            if isinstance(value, (int, float)) and value == 1:
                return True
            if isinstance(value, str) and value.strip().lower() in truthy_text:
                return True
        return False

    def _build_channel_payload(
        self,
        remote_item: dict[str, Any],
        play_info: dict[str, Any],
        existing: dict[str, Any] | None,
        *,
        preserve_stream_on_offline: bool = False,
        stream_hint: str = "",
    ) -> dict[str, Any]:
        upstream_channel_id = self._extract_upstream_channel_id(remote_item)
        media_info = play_info.get("mediaInfo") or {}
        explicit_offline = self._is_remote_status_off(remote_item, play_info)
        online = False if explicit_offline else (
            self._is_remote_online(remote_item, play_info) or self._has_playable_source(play_info)
        )
        effective_play_info = play_info if online else {}
        source_url, source_type = self._pick_source_url(effective_play_info)
        stream = self._extract_stream_id(
            effective_play_info,
            existing if (online or preserve_stream_on_offline) else None,
        ) or str(stream_hint or "").strip()
        existing_channel_id = str((existing or {}).get("id") or "").strip()
        channel_id = existing_channel_id or stream or upstream_channel_id or generate_id("ch")
        current_time = now_iso()
        channel_name = (
            remote_item.get("gbName")
            or remote_item.get("name")
            or remote_item.get("channelName")
            or f"WVP Channel {upstream_channel_id}"
        )
        app = effective_play_info.get("app") or media_info.get("app") or (existing or {}).get("app") or "rtp"
        resolved_source_url = source_url or ((existing or {}).get("source_url") if online else None)

        payload = {
            "id": channel_id,
            "device_id": str(remote_item.get("deviceId") or remote_item.get("gbDeviceId") or remote_item.get("device_id") or "") or None,
            "gb_id": upstream_channel_id or None,
            "name": channel_name,
            "type": "wvp",
            "source_url": resolved_source_url,
            "app": app,
            "stream": stream,
            "media_node_id": (existing or {}).get("media_node_id"),
            "is_online": online,
            "is_playing": bool((existing or {}).get("is_playing", False)) if online else False,
            "record_mode": (existing or {}).get("record_mode", "none"),
            "snapshot_url": (existing or {}).get("snapshot_url"),
            "snapshot_path": (existing or {}).get("snapshot_path"),
            "flv": effective_play_info.get("flv") or (existing or {}).get("flv"),
            "created_at": (existing or {}).get("created_at") or current_time,
            "updated_at": current_time,
            "ext": {
                "zones": (((existing or {}).get("ext") or {}).get("zones") or []),
                "upstream": {
                    "platform": "wvp",
                    "channel_db_id": upstream_channel_id,
                    "source_type": source_type,
                    "online": online,
                    "name": channel_name,
                    "device_id": remote_item.get("deviceId") or remote_item.get("gbDeviceId") or remote_item.get("device_id"),
                    "play_info": effective_play_info,
                    "channel_info": remote_item,
                    "missing_from_wvp": False,
                    "synced_at": current_time,
                },
            },
        }
        if not online:
            payload = self._clear_offline_playback_fields(
                payload,
                preserve_stream=preserve_stream_on_offline,
            )
        return payload

    def _sync_result_can_cleanup_missing_channels(self, remote: dict[str, Any], page: int, query: str | None) -> bool:
        if not remote.get("enabled", False):
            return False
        if query or page != 1:
            return False
        items = list(remote.get("items") or [])
        try:
            total = int(remote.get("total", len(items)) or 0)
        except (TypeError, ValueError):
            total = len(items)
        return len(items) >= total

    def _mark_wvp_channel_missing_offline(self, item: dict[str, Any]) -> dict[str, Any]:
        payload = copy.deepcopy(item)
        current_time = now_iso()
        payload = self._clear_offline_playback_fields(payload)
        payload["updated_at"] = current_time
        upstream = payload.setdefault("ext", {}).setdefault("upstream", {})
        upstream["missing_from_wvp"] = True
        upstream["synced_at"] = current_time
        save_json(self.channels_dir / f"{payload['id']}.json", payload)
        return payload

    def _cleanup_missing_wvp_channels(
        self,
        existing_items: list[dict[str, Any]],
        remote_upstream_channel_ids: set[str],
        synced_channel_ids: set[str],
    ) -> dict[str, int]:
        existing_ids = {
            str(item.get("id") or "").strip()
            for item in existing_items
            if str(item.get("id") or "").strip()
        }
        bound_channel_ids = self._find_bound_channel_ids(existing_ids)
        deleted = 0
        marked_offline = 0

        for item in existing_items:
            channel_id = str(item.get("id") or "").strip()
            if not channel_id or channel_id in synced_channel_ids:
                continue

            upstream = ((item.get("ext") or {}).get("upstream") or {})
            if upstream.get("platform") != "wvp":
                continue

            upstream_channel_id = str(upstream.get("channel_db_id") or "").strip()
            if upstream_channel_id and upstream_channel_id in remote_upstream_channel_ids:
                continue

            channel_path = self.channels_dir / f"{channel_id}.json"
            if channel_id in bound_channel_ids:
                self._mark_wvp_channel_missing_offline(item)
                marked_offline += 1
                continue

            channel_path.unlink(missing_ok=True)
            delete_json_document(channel_path, channel_id)
            deleted += 1

        return {
            "deleted": deleted,
            "marked_offline": marked_offline,
        }

    def sync_channels(self, page: int = 1, count: int = 100, query: str | None = None) -> dict[str, Any]:
        remote = self.list_remote_channels(page=page, count=count, query=query)
        synced_items: list[dict[str, Any]] = []
        play_info_refresh_requests: list[dict[str, Any]] = []
        existing_wvp_channels = self._list_existing_wvp_channels()
        original_existing_wvp_channels = list(existing_wvp_channels)
        existing_electronic_seal_channels = self._list_existing_electronic_seal_channels()
        electronic_seal_by_stream = {
            str(item.get("stream") or "").strip(): item
            for item in existing_electronic_seal_channels
            if str(item.get("stream") or "").strip()
        }
        electronic_seal_by_upstream_id: dict[str, dict[str, Any]] = {}
        for item in existing_electronic_seal_channels:
            upstream = ((item.get("ext") or {}).get("upstream") or {})
            upstream_id = str(upstream.get("channel_db_id") or "").strip()
            if upstream_id:
                electronic_seal_by_upstream_id.setdefault(upstream_id, item)
        remote_items = list(remote.get("items") or [])
        remote_upstream_channel_ids: set[str] = set()
        synced_channel_ids: set[str] = set()
        bound_existing_channel_ids = self._find_bound_channel_ids(
            {str(item.get("id") or "").strip() for item in existing_wvp_channels}
        )

        for remote_item in remote_items:
            upstream_channel_id = self._extract_upstream_channel_id(remote_item)
            if not upstream_channel_id:
                continue
            remote_upstream_channel_ids.add(str(upstream_channel_id).strip())
            remote_stream_id = self._extract_stream_id(remote_item)
            play_info: dict[str, Any] = {}
            stream_id = remote_stream_id
            device_id = str(
                remote_item.get("deviceId")
                or remote_item.get("gbDeviceId")
                or remote_item.get("device_id")
                or ""
            ).strip()
            channel_name = str(
                remote_item.get("gbName")
                or remote_item.get("name")
                or remote_item.get("channelName")
                or ""
            ).strip()
            candidates = self._find_existing_wvp_candidates(
                stream_id,
                upstream_channel_id,
                device_id,
                channel_name,
                existing_wvp_channels,
            )
            bound_channel_ids = {
                str(item.get("id") or "").strip()
                for item in candidates
                if str(item.get("id") or "").strip() in bound_existing_channel_ids
            }
            existing = self._select_canonical_existing_channel(candidates, stream_id, bound_channel_ids)
            existing = self._merge_existing_candidate_data(existing, candidates)
            seal_existing = electronic_seal_by_stream.get(stream_id) if stream_id else None
            if seal_existing is None and self._is_remote_status_off(remote_item):
                seal_existing = electronic_seal_by_upstream_id.get(
                    str(upstream_channel_id).strip()
                )

            resolved_stream_id = (
                stream_id
                or str((existing or {}).get("stream") or "").strip()
                or str((seal_existing or {}).get("stream") or "").strip()
            )
            resolved_seal = electronic_seal_by_stream.get(resolved_stream_id) if resolved_stream_id else None

            if resolved_seal is not None:
                payload = self._build_channel_payload(
                    remote_item,
                    play_info,
                    resolved_seal,
                    preserve_stream_on_offline=True,
                    stream_hint=resolved_stream_id,
                )
                updated_seal = self._update_electronic_seal_channel(
                    str(resolved_seal.get("id") or "").strip(),
                    payload,
                )
                if updated_seal is None:
                    logger.warning(
                        "[WVPSync] electronic-seal owner disappeared channel_id=%s stream=%s",
                        resolved_seal.get("id"),
                        resolved_stream_id,
                    )
                    continue
                electronic_seal_by_stream[resolved_stream_id] = updated_seal
                synced_items.append({**updated_seal, "_sync_owner": "electronic_seal"})
                if not self._is_remote_status_off(remote_item):
                    play_info_refresh_requests.append({
                        "channel_id": str(updated_seal.get("id") or "").strip(),
                        "sync_owner": "electronic_seal",
                        "upstream_channel_id": str(upstream_channel_id).strip(),
                        "remote_item": copy.deepcopy(remote_item),
                    })
                continue

            if settings.METADATA_DB_ENABLED and not resolved_stream_id and existing is None:
                # Without a stable stream identity, do not invent a channel ID or
                # let this unresolved WVP item trigger missing-channel cleanup.
                synced_channel_ids.update(
                    str(item.get("id") or "").strip()
                    for item in candidates
                    if str(item.get("id") or "").strip()
                )
                logger.warning(
                    "[WVPSync] defer channel without resolved stream upstream_channel_id=%s",
                    upstream_channel_id,
                )
                if not self._is_remote_status_off(remote_item):
                    play_info_refresh_requests.append({
                        "channel_id": None,
                        "sync_owner": "unresolved",
                        "upstream_channel_id": str(upstream_channel_id).strip(),
                        "remote_item": copy.deepcopy(remote_item),
                    })
                continue

            payload = self._build_channel_payload(
                remote_item,
                play_info,
                existing,
                stream_hint=resolved_stream_id,
            )
            rebound_channel_ids = self._rebind_duplicate_channel_references(
                payload["id"],
                payload.get("name"),
                {str(item.get("id") or "").strip() for item in candidates},
            )
            channel_path = self.channels_dir / f"{payload['id']}.json"
            save_json(channel_path, payload)
            protected_channel_ids = bound_channel_ids - rebound_channel_ids
            self._delete_duplicate_channels(candidates, payload["id"], protected_channel_ids)
            synced_items.append({**payload, "_sync_owner": "video_ai"})
            if not self._is_remote_status_off(remote_item):
                play_info_refresh_requests.append({
                    "channel_id": str(payload.get("id") or "").strip(),
                    "sync_owner": "video_ai",
                    "upstream_channel_id": str(upstream_channel_id).strip(),
                    "remote_item": copy.deepcopy(remote_item),
                })
            synced_channel_ids.add(str(payload["id"]).strip())
            existing_wvp_channels = [
                item
                for item in existing_wvp_channels
                if str(item.get("id") or "").strip() not in {str(candidate.get("id") or "").strip() for candidate in candidates}
            ]
            existing_wvp_channels.append(payload)

        missing_cleanup = {"deleted": 0, "marked_offline": 0}
        if self._sync_result_can_cleanup_missing_channels(remote, page, query):
            missing_cleanup = self._cleanup_missing_wvp_channels(
                original_existing_wvp_channels,
                remote_upstream_channel_ids,
                synced_channel_ids,
            )

        return {
            "enabled": remote.get("enabled", False),
            "synced": len(synced_items),
            "total": remote.get("total", len(synced_items)),
            "items": synced_items,
            "missing_cleanup": missing_cleanup,
            "_play_info_refresh_requests": play_info_refresh_requests,
        }

    def _load_electronic_seal_channel_by_id(self, channel_id: str) -> dict[str, Any] | None:
        normalized_id = str(channel_id or "").strip()
        if not normalized_id or not settings.METADATA_DB_ENABLED:
            return None
        with SessionLocal() as session:
            row = session.get(ElectronicSealChannelMeta, normalized_id)
        return self._build_channel_item_from_row(row) if row else None

    def _save_play_info_payload(
        self,
        owner: str,
        channel: dict[str, Any],
        remote_item: dict[str, Any],
        play_info: dict[str, Any],
    ) -> dict[str, Any] | None:
        effective_remote_item = copy.deepcopy(remote_item)
        if not play_info:
            effective_remote_item["online"] = False
            effective_remote_item["status"] = "OFF"

        stream_hint = self._extract_stream_id(play_info, channel)
        payload = self._build_channel_payload(
            effective_remote_item,
            play_info,
            channel,
            preserve_stream_on_offline=owner == "electronic_seal",
            stream_hint=stream_hint,
        )
        if payload.get("is_online") and self.is_configured_recorder_rtsp(channel):
            payload["source_url"] = channel.get("source_url")
            payload["source_type"] = "rtsp"

        if owner == "electronic_seal":
            return self._update_electronic_seal_channel(
                str(channel.get("id") or "").strip(),
                payload,
            )

        save_json(self.channels_dir / f"{payload['id']}.json", payload)
        return payload

    def _refresh_play_info_request(self, refresh_request: dict[str, Any]) -> bool:
        remote_item = copy.deepcopy(refresh_request.get("remote_item") or {})
        upstream_channel_id = str(
            refresh_request.get("upstream_channel_id")
            or self._extract_upstream_channel_id(remote_item)
            or ""
        ).strip()
        if not upstream_channel_id:
            return False

        play_info = self.get_play_info(upstream_channel_id)
        owner = str(refresh_request.get("sync_owner") or "unresolved").strip()
        channel_id = str(refresh_request.get("channel_id") or "").strip()

        if owner == "electronic_seal" and channel_id:
            channel = self._load_electronic_seal_channel_by_id(channel_id)
            if channel:
                return self._save_play_info_payload(
                    "electronic_seal",
                    channel,
                    remote_item,
                    play_info,
                ) is not None

        if owner == "video_ai" and channel_id:
            channel = self._load_channel_by_id(channel_id)
            if channel:
                return self._save_play_info_payload(
                    "video_ai",
                    channel,
                    remote_item,
                    play_info,
                ) is not None

        stream_id = self._extract_stream_id(play_info)
        if not stream_id:
            return False

        electronic_seal_by_stream = {
            str(item.get("stream") or "").strip(): item
            for item in self._list_existing_electronic_seal_channels()
            if str(item.get("stream") or "").strip()
        }
        seal_channel = electronic_seal_by_stream.get(stream_id)
        if seal_channel:
            return self._save_play_info_payload(
                "electronic_seal",
                seal_channel,
                remote_item,
                play_info,
            ) is not None

        existing_wvp_channels = self._list_existing_wvp_channels()
        device_id = str(
            remote_item.get("deviceId")
            or remote_item.get("gbDeviceId")
            or remote_item.get("device_id")
            or ""
        ).strip()
        channel_name = str(
            remote_item.get("gbName")
            or remote_item.get("name")
            or remote_item.get("channelName")
            or ""
        ).strip()
        candidates = self._find_existing_wvp_candidates(
            stream_id,
            upstream_channel_id,
            device_id,
            channel_name,
            existing_wvp_channels,
        )
        bound_channel_ids = self._find_bound_channel_ids({
            str(item.get("id") or "").strip()
            for item in candidates
            if str(item.get("id") or "").strip()
        })
        existing = self._select_canonical_existing_channel(
            candidates,
            stream_id,
            bound_channel_ids,
        )
        existing = self._merge_existing_candidate_data(existing, candidates)
        payload = self._build_channel_payload(
            remote_item,
            play_info,
            existing,
            stream_hint=stream_id,
        )
        rebound_channel_ids = self._rebind_duplicate_channel_references(
            payload["id"],
            payload.get("name"),
            {str(item.get("id") or "").strip() for item in candidates},
        )
        save_json(self.channels_dir / f"{payload['id']}.json", payload)
        self._delete_duplicate_channels(
            candidates,
            payload["id"],
            bound_channel_ids - rebound_channel_ids,
        )
        return True

    def refresh_play_info_requests(
        self,
        refresh_requests: list[dict[str, Any]],
    ) -> dict[str, int]:
        refreshed = 0
        failed = 0
        skipped = 0
        for refresh_request in refresh_requests:
            upstream_channel_id = str(
                refresh_request.get("upstream_channel_id") or ""
            ).strip()
            try:
                if self._refresh_play_info_request(refresh_request):
                    refreshed += 1
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "[WVPSync] background play info refresh failed upstream_channel_id=%s error=%s",
                    upstream_channel_id,
                    exc,
                )
        return {
            "refreshed": refreshed,
            "failed": failed,
            "skipped": skipped,
        }

    def refresh_channel_source(self, channel: dict[str, Any]) -> dict[str, Any]:
        upstream = ((channel.get("ext") or {}).get("upstream") or {})
        if upstream.get("platform") != "wvp":
            return channel

        upstream_channel_id = upstream.get("channel_db_id")
        if not upstream_channel_id:
            return channel

        play_info = self.get_play_info(upstream_channel_id)
        explicit_offline = (
            not play_info
            or self._is_remote_status_off(upstream.get("channel_info") or {}, play_info)
        )

        effective_play_info = {} if explicit_offline else play_info
        source_url, source_type = self._pick_source_url(effective_play_info)
        media_info = play_info.get("mediaInfo") or {}
        current_time = now_iso()
        refreshed = dict(channel)
        preserve_configured_source = self.is_configured_recorder_rtsp(channel)
        refreshed["source_url"] = (
            refreshed.get("source_url")
            if preserve_configured_source
            else source_url or (refreshed.get("source_url") if not explicit_offline else None)
        )
        refreshed["source_type"] = source_type
        refreshed["flv"] = effective_play_info.get("flv") or refreshed.get("flv")
        refreshed["app"] = effective_play_info.get("app") or media_info.get("app") or refreshed.get("app")
        refreshed["stream"] = effective_play_info.get("stream") or media_info.get("stream") or (refreshed.get("stream") if not explicit_offline else "")
        refreshed["is_online"] = False if explicit_offline else (
            self._is_remote_online(upstream.get("channel_info") or {}, play_info) or self._has_playable_source(play_info)
        )
        if not refreshed["is_online"]:
            refreshed = self._clear_offline_playback_fields(refreshed)
        refreshed["updated_at"] = current_time
        refreshed.setdefault("ext", {}).setdefault("upstream", {}).update({
            "source_type": source_type,
            "online": refreshed.get("is_online"),
            "play_info": effective_play_info if refreshed.get("is_online") else {},
            "synced_at": current_time,
        })
        save_json(self.channels_dir / f"{refreshed['id']}.json", refreshed)
        return refreshed

    @staticmethod
    def is_configured_recorder_rtsp(channel: dict[str, Any]) -> bool:
        source_url = str(channel.get("source_url") or "").strip()
        if not source_url.lower().startswith("rtsp://"):
            return False
        parsed = urlparse(source_url)
        return not parsed.path.casefold().startswith("/rtp/")

    def _load_channel_by_id(self, channel_id: str) -> dict[str, Any] | None:
        normalized_id = str(channel_id or "").strip()
        if not normalized_id:
            return None

        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                row = session.query(ChannelMeta).filter(ChannelMeta.id == normalized_id).one_or_none()
            if row:
                return self._build_channel_item_from_row(row)

        path = self.channels_dir / f"{normalized_id}.json"
        if not path.exists():
            return None
        return load_json(path)

    def refresh_channel_sources(self, channel_ids: list[str]) -> dict[str, Any]:
        refreshed = 0
        failed = 0
        skipped = 0
        for channel_id in channel_ids:
            channel = self._load_channel_by_id(channel_id)
            if not channel:
                skipped += 1
                continue
            upstream = ((channel.get("ext") or {}).get("upstream") or {})
            if upstream.get("platform") != "wvp" or not upstream.get("online"):
                skipped += 1
                continue
            try:
                self.refresh_channel_source(channel)
                refreshed += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "[WVPSync] background play info refresh failed channel_id=%s error=%s",
                    channel_id,
                    exc,
                )
        return {
            "refreshed": refreshed,
            "failed": failed,
            "skipped": skipped,
        }
