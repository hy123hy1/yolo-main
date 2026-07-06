import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import cv2

from src.core.settings import settings
from src.db.database import SessionLocal
from src.db.models import ChannelMeta, TaskMeta
from src.services.event_service import EventService
from src.services.analysis_service import analysis_service
from src.services.media_node_service import MediaNodeService
from src.services.metadata_db_sync import delete_json_document
from src.services.model_service import ModelService
from src.services.platform_storage import format_datetime, generate_id, list_json_items, load_json, now_iso, save_json
from src.services.wvp_service import WVPService
from src.services.zlm_adapter import ZLMAdapter
from src.services.zone_preview_service import zone_preview_service

logger = logging.getLogger("uvicorn.error")


class ChannelService:
    def __init__(self):
        self.channels_dir: Path = settings.CHANNELS_DIR
        self.snapshots_dir: Path = settings.SNAPSHOTS_DIR
        self.transcode_logs_dir: Path = settings.TRANSCODE_LOGS_DIR
        self._snapshot_refresh_tasks: dict[str, asyncio.Task] = {}
        self.media_node_service = MediaNodeService()
        self.event_service = EventService()
        self.model_service = ModelService()
        self.wvp_service = WVPService()

    def _build_adapter(self, node: dict | None):
        api_base = node.get("api_base") if node else settings.ZLM_API_BASE
        play_base = node.get("play_base") if node else settings.ZLM_PLAY_BASE
        webrtc_base = node.get("webrtc_base") if node else settings.ZLM_WEBRTC_BASE
        secret = node.get("secret", "") if node else settings.ZLM_SECRET

        return ZLMAdapter(
            play_base=play_base,
            api_base=api_base,
            webrtc_base=webrtc_base,
            secret=secret,
            public_rtsp_host=node.get("public_rtsp_host") if node else settings.ZLM_PUBLIC_RTSP_HOST,
            public_rtmp_host=node.get("public_rtmp_host") if node else settings.ZLM_PUBLIC_RTMP_HOST,
            public_http_host=node.get("public_http_host") if node else settings.ZLM_PUBLIC_HTTP_HOST,
            rtsp_port=node.get("rtsp_port", settings.ZLM_RTSP_PORT) if node else settings.ZLM_RTSP_PORT,
            rtmp_port=node.get("rtmp_port", settings.ZLM_RTMP_PORT) if node else settings.ZLM_RTMP_PORT,
            http_port=node.get("http_port", settings.ZLM_HTTP_PORT) if node else settings.ZLM_HTTP_PORT,
            https_port=node.get("https_port", settings.ZLM_HTTPS_PORT) if node else settings.ZLM_HTTPS_PORT,
            hls_file=node.get("hls_file", settings.ZLM_HLS_FILE) if node else settings.ZLM_HLS_FILE,
            httpflv_suffix=node.get("httpflv_suffix", settings.ZLM_HTTPFLV_SUFFIX) if node else settings.ZLM_HTTPFLV_SUFFIX,
            webrtc_path=node.get("webrtc_path", settings.ZLM_WEBRTC_PATH) if node else settings.ZLM_WEBRTC_PATH,
        )

    def _build_public_web_url(self, path: str | None) -> str | None:
        if not path:
            return None
        base = (settings.WEB_PUBLIC_BASE or "").rstrip("/")
        if not base:
            return path
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{base}{path if path.startswith('/') else f'/{path}'}"

    def _normalize_public_browser_url(self, url: str | None) -> str | None:
        if not url:
            return None
        value = str(url).strip()
        if not value:
            return None

        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return self._build_public_web_url(value)

        if not parsed.path.startswith("/proxy/sms/"):
            return value

        base = (settings.WEB_PUBLIC_BASE or "").rstrip("/")
        if not base:
            return value

        suffix = parsed.path
        if parsed.query:
            suffix = f"{suffix}?{parsed.query}"
        if parsed.fragment:
            suffix = f"{suffix}#{parsed.fragment}"
        return f"{base}{suffix}"

    def _normalize_public_play_url(self, url: str | None) -> str | None:
        if not url:
            return None
        value = str(url).strip()
        if not value:
            return None

        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return self._build_public_web_url(value)
        if parsed.path.startswith("/proxy/sms/"):
            return self._normalize_public_browser_url(value)

        suffix = parsed.path or ""
        if parsed.query:
            suffix = f"{suffix}?{parsed.query}"
        if parsed.fragment:
            suffix = f"{suffix}#{parsed.fragment}"

        if parsed.scheme in {"http", "https"}:
            host = settings.ZLM_PUBLIC_HTTP_HOST or parsed.hostname or "localhost"
            port = settings.ZLM_HTTPS_PORT if parsed.scheme == "https" else settings.ZLM_HTTP_PORT
            return f"{parsed.scheme}://{host}:{port}{suffix}"
        if parsed.scheme in {"webrtc", "webrtcs"}:
            host = settings.ZLM_PUBLIC_HTTP_HOST or parsed.hostname or "localhost"
            port = settings.ZLM_HTTPS_PORT if parsed.scheme == "webrtcs" else settings.ZLM_HTTP_PORT
            return f"{parsed.scheme}://{host}:{port}{suffix}"
        if parsed.scheme == "rtsp":
            host = settings.ZLM_PUBLIC_RTSP_HOST or parsed.hostname or "localhost"
            return f"rtsp://{host}:{settings.ZLM_RTSP_PORT}{suffix}"
        if parsed.scheme == "rtmp":
            host = settings.ZLM_PUBLIC_RTMP_HOST or parsed.hostname or "localhost"
            return f"rtmp://{host}:{settings.ZLM_RTMP_PORT}{suffix}"
        return value

    def _normalize_browser_play_url_map(self, play_url: dict[str, str | None] | None) -> dict[str, str | None] | None:
        if not isinstance(play_url, dict):
            return play_url

        normalized = dict(play_url)
        for key in ("webrtc_api", "hls", "httpflv", "flv"):
            value = self._normalize_public_play_url(normalized.get(key))
            if value:
                normalized[key] = value
        return normalized

    def _build_zone_preview_browser_urls(self, channel: dict, node: dict | None = None) -> dict[str, str | None]:
        adapter = self._build_adapter(node)
        zone_preview = channel.get("ext", {}).get("zone_preview", {}) or {}
        zone_stream = zone_preview.get("stream") or self._build_zone_preview_stream_name(channel["id"], channel["stream"])
        urls = adapter.build_play_urls(channel["app"], zone_stream, channel.get("media_node_id"))
        return self._build_browser_play_url_map(urls)

    def _build_browser_play_url_map(self, urls: dict[str, str | None] | None) -> dict[str, str | None]:
        urls = urls or {}
        httpflv_url = (
            self._normalize_public_browser_url(urls.get("browser_httpflv"))
            or self._normalize_public_browser_url(urls.get("browser_httpflv_url"))
            or urls.get("httpflv")
        )
        return {
            "webrtc_api": (
                self._normalize_public_browser_url(urls.get("browser_webrtc_api"))
                or self._normalize_public_browser_url(urls.get("browser_webrtc_api_url"))
                or urls.get("webrtc")
            ),
            "hls": (
                self._normalize_public_browser_url(urls.get("browser_hls"))
                or self._normalize_public_browser_url(urls.get("browser_hls_url"))
                or urls.get("hls")
            ),
            "httpflv": httpflv_url,
            "flv": httpflv_url,
        }

    def _default_task_preview_runtime(self) -> dict[str, str | bool | None]:
        return {
            "prepared": False,
            "mode": None,
            "owner_task_id": None,
            "status": "stopped",
            "last_error": None,
            "play_url": None,
            "updated_at": None,
        }

    def _ensure_task_preview_runtime(self, item: dict) -> dict:
        ext = item.setdefault("ext", {})
        task_preview = ext.get("task_preview")
        if not isinstance(task_preview, dict):
            task_preview = {}
            ext["task_preview"] = task_preview
        defaults = self._default_task_preview_runtime()
        for key, value in defaults.items():
            task_preview.setdefault(key, value)
        return task_preview

    def _get_prepared_task_preview(self, channel: dict) -> dict | None:
        preview = ((channel.get("ext") or {}).get("task_preview") or {})
        if not isinstance(preview, dict):
            return None
        mode = str(preview.get("mode") or "").lower()
        if not preview.get("prepared") or mode not in {"channel", "zone"}:
            return None
        return preview

    def _build_task_preview_play_url(
        self,
        channel: dict,
        *,
        mode: str,
        preview_play_url: dict[str, str | None] | None = None,
    ) -> dict[str, str | None]:
        adapter = self._build_adapter(None)
        mode = str(mode or "channel").lower()
        stream = str(channel.get("stream") or "").strip()

        if mode == "zone":
            zone_preview = ((channel.get("ext") or {}).get("zone_preview") or {})
            stream = str(
                zone_preview.get("stream")
                or self._build_zone_preview_stream_name(channel["id"], channel.get("stream"))
            ).strip()

        dynamic_urls: dict[str, str | None] = {}
        app = str(channel.get("app") or "").strip()
        if app and stream:
            dynamic_urls = self._build_browser_play_url_map(
                adapter.build_play_urls(app, stream, channel.get("media_node_id"))
            )

        preview_play_url = self._normalize_browser_play_url_map(preview_play_url)
        resolved = {
            "webrtc_api": self._normalize_public_browser_url(channel.get("webrtc_api")) or dynamic_urls.get("webrtc_api"),
            "hls": self._normalize_public_browser_url(channel.get("hls")) or dynamic_urls.get("hls"),
            "httpflv": dynamic_urls.get("httpflv"),
            "flv": dynamic_urls.get("flv") or dynamic_urls.get("httpflv"),
        }

        if isinstance(preview_play_url, dict):
            resolved["webrtc_api"] = preview_play_url.get("webrtc_api") or resolved.get("webrtc_api")
            resolved["hls"] = preview_play_url.get("hls") or resolved.get("hls")
            resolved["httpflv"] = (
                preview_play_url.get("httpflv")
                or preview_play_url.get("flv")
                or resolved.get("httpflv")
            )
            resolved["flv"] = (
                preview_play_url.get("flv")
                or preview_play_url.get("httpflv")
                or resolved.get("flv")
                or resolved.get("httpflv")
            )

        if not resolved.get("httpflv") and resolved.get("flv"):
            resolved["httpflv"] = resolved["flv"]
        if not resolved.get("flv") and resolved.get("httpflv"):
            resolved["flv"] = resolved["httpflv"]

        return resolved

    def _is_browser_transcode_ready(self, channel: dict) -> bool:
        browser = ((channel.get("ext") or {}).get("browser_transcode") or {})
        return bool(
            browser.get("enabled")
            and browser.get("stream")
            and (browser.get("inspect_rtmp") or {}).get("data")
            and not browser.get("error")
        )

    @staticmethod
    def _play_url_targets_stream(play_url: dict[str, str | None] | None, stream: str | None) -> bool:
        stream = str(stream or "").strip()
        if not stream or not isinstance(play_url, dict):
            return False
        for key in ("webrtc_api", "hls", "httpflv", "flv"):
            value = str(play_url.get(key) or "").strip()
            if stream and stream in value:
                return True
        return False

    def _build_channel_browser_play_url(
        self,
        channel: dict,
        *,
        preview_play_url: dict[str, str | None] | None = None,
    ) -> dict[str, str | None]:
        app = str(channel.get("app") or "").strip()
        stream = str(channel.get("stream") or "").strip()
        browser = ((channel.get("ext") or {}).get("browser_transcode") or {})
        adapter = self._build_adapter(None)

        original_dynamic: dict[str, str | None] = {}
        if app and stream:
            original_dynamic = self._build_browser_play_url_map(
                adapter.build_play_urls(app, stream, channel.get("media_node_id"))
            )

        if self._is_browser_transcode_ready(channel):
            app = str(browser.get("app") or app).strip()
            stream = str(browser.get("stream") or stream).strip()
            if app and stream:
                return self._build_browser_play_url_map(
                    adapter.build_play_urls(app, stream, channel.get("media_node_id"))
                )

        resolved = self._build_task_preview_play_url(
            channel,
            mode="channel",
            preview_play_url=preview_play_url,
        )
        browser_stream = str(
            browser.get("stream")
            or self._build_browser_transcode_stream_name(channel.get("id"), channel.get("stream"))
        ).strip()
        if self._play_url_targets_stream(resolved, browser_stream):
            return original_dynamic or resolved

        merged = dict(original_dynamic)
        for key, value in resolved.items():
            if value:
                merged[key] = value
        if not merged.get("httpflv") and merged.get("flv"):
            merged["httpflv"] = merged["flv"]
        if not merged.get("flv") and merged.get("httpflv"):
            merged["flv"] = merged["httpflv"]
        return merged or resolved

    def _build_prepared_task_preview_status(self, channel: dict) -> dict | None:
        preview = self._get_prepared_task_preview(channel)
        if not preview:
            return None

        mode = str(preview.get("mode") or "").lower()
        if mode == "channel":
            play_url = self._build_channel_browser_play_url(
                channel,
                preview_play_url=preview.get("play_url"),
            )
        else:
            play_url = self._build_task_preview_play_url(
                channel,
                mode=mode,
                preview_play_url=preview.get("play_url"),
            )

        if mode == "channel":
            is_running = bool(channel.get("is_playing"))
            return {
                "channel_id": channel["id"],
                "enabled": is_running,
                "status": "running" if is_running else str(preview.get("status") or "stopped"),
                "stream": channel.get("stream"),
                "source": channel.get("source_url"),
                "target": None,
                "urls": {},
                "last_error": preview.get("last_error"),
                "started_at": None,
                "stopped_at": None,
                "process_alive": is_running,
                "zone_count": 0,
                "zone_source": "channel",
                "owner_task_id": preview.get("owner_task_id"),
                "resolved_mode": "channel",
                "play_url": play_url,
            }

        runtime = zone_preview_service.status(channel["id"]) or {}
        zone_preview = ((channel.get("ext") or {}).get("zone_preview") or {})
        status = str(
            runtime.get("status")
            or zone_preview.get("status")
            or preview.get("status")
            or "stopped"
        )
        return {
            "channel_id": channel["id"],
            "enabled": bool(runtime.get("enabled", status == "running")),
            "status": status,
            "stream": runtime.get("stream") or zone_preview.get("stream") or self._build_zone_preview_stream_name(channel["id"], channel["stream"]),
            "source": runtime.get("source") or channel.get("source_url"),
            "target": runtime.get("target") or zone_preview.get("target"),
            "urls": runtime.get("urls") or zone_preview.get("urls") or {},
            "last_error": runtime.get("last_error") or zone_preview.get("last_error") or preview.get("last_error"),
            "started_at": runtime.get("started_at") or zone_preview.get("started_at"),
            "stopped_at": runtime.get("stopped_at") or zone_preview.get("stopped_at"),
            "process_alive": bool(runtime.get("process_alive", status == "running")),
            "zone_count": int(runtime.get("zone_count") or zone_preview.get("zone_count") or len(zone_preview.get("zones") or [])),
            "zone_source": runtime.get("zone_source") or zone_preview.get("zone_source") or "task",
            "owner_task_id": preview.get("owner_task_id") or runtime.get("owner_task_id") or zone_preview.get("owner_task_id"),
            "resolved_mode": "zone",
            "play_url": play_url,
        }

    @staticmethod
    def _task_payload_is_running(task_payload: dict) -> bool:
        runtime = task_payload.get("runtime") or {}
        status = str(runtime.get("status") or task_payload.get("status") or "").lower()
        return bool(runtime.get("is_running")) or status in {"running", "starting", "restarting"}

    @staticmethod
    def _normalize_overlay_region_payload(region: dict) -> dict:
        return {
            "id": region.get("id") or generate_id("region"),
            "name": region.get("name") or "zone",
            "type": region.get("type") or "polygon",
            "color": region.get("color") or "#3b82f6",
            "coordinates": list(region.get("coordinates") or []),
            "enabled": bool(region.get("enabled", True)),
        }

    def _collect_overlay_regions_from_task_payload(self, task_payload: dict) -> list[dict]:
        collected: list[dict] = []
        seen_ids: set[str] = set()

        for algorithm in list(task_payload.get("algorithms") or []):
            if not isinstance(algorithm, dict):
                continue
            if not (algorithm.get("enabled") and algorithm.get("model_id")):
                continue
            for region in list(algorithm.get("regions") or []):
                if not isinstance(region, dict):
                    continue
                normalized = self._normalize_overlay_region_payload(region)
                region_id = str(normalized.get("id") or "").strip()
                if not region_id or region_id in seen_ids:
                    continue
                collected.append(normalized)
                seen_ids.add(region_id)

        return collected

    def _build_running_task_preview_request(self, task_payload: dict) -> dict | None:
        if not self._task_payload_is_running(task_payload):
            return None

        task_id = str(task_payload.get("id") or "").strip()
        if not task_id:
            return None

        overlay_regions = self._collect_overlay_regions_from_task_payload(task_payload)
        if overlay_regions:
            return {
                "task_id": task_id,
                "mode": "zone",
                "zones": overlay_regions,
            }

        has_runnable_algorithm = any(
            isinstance(algorithm, dict) and algorithm.get("enabled") and algorithm.get("model_id")
            for algorithm in list(task_payload.get("algorithms") or [])
        )
        if not has_runnable_algorithm:
            return None

        return {
            "task_id": task_id,
            "mode": "channel",
            "zones": [],
        }

    async def _find_running_task_preview_request(self, channel_id: str) -> dict | None:
        with SessionLocal() as session:
            task_rows = (
                session.query(TaskMeta)
                .filter(TaskMeta.channel_id == channel_id)
                .order_by(TaskMeta.created_at.desc(), TaskMeta.id.desc())
                .all()
            )

        for row in task_rows:
            payload = dict(row.raw_json or {})
            payload.setdefault("id", row.id)
            payload.setdefault("channel_id", row.channel_id)
            payload.setdefault("status", row.status)
            payload["runtime"] = {
                **dict(payload.get("runtime") or {}),
                "status": (payload.get("runtime") or {}).get("status") or row.status,
                "is_running": bool((payload.get("runtime") or {}).get("is_running", row.is_running)),
            }
            preview_request = self._build_running_task_preview_request(payload)
            if preview_request:
                return preview_request

        if settings.METADATA_DB_ENABLED:
            return None

        for raw_task in list_json_items(settings.TASKS_DIR):
            if not isinstance(raw_task, dict):
                continue
            if str(raw_task.get("channel_id") or "") != str(channel_id):
                continue
            preview_request = self._build_running_task_preview_request(raw_task)
            if preview_request:
                return preview_request

        return None

    async def _bootstrap_running_task_preview(self, channel: dict) -> dict | None:
        prepared = self._build_prepared_task_preview_status(channel)
        if prepared:
            return prepared

        preview_request = await self._find_running_task_preview_request(channel["id"])
        if not preview_request:
            return None

        await self.play(channel["id"])

        task_id = preview_request["task_id"]
        mode = preview_request["mode"]
        if mode == "zone":
            await self.start_zone_preview(
                channel["id"],
                zones=list(preview_request.get("zones") or []),
                zone_source="task",
                owner_task_id=task_id,
            )
            play_url = await self.refresh_zone_preview_cached_urls(channel["id"]) or {}
            await self.set_task_preview_state(
                channel["id"],
                mode="zone",
                owner_task_id=task_id,
                play_url=play_url,
                status="running",
            )
        else:
            play_url = await self.refresh_channel_browser_cached_urls(channel["id"]) or {}
            await self.set_task_preview_state(
                channel["id"],
                mode="channel",
                owner_task_id=task_id,
                play_url=play_url,
                status="running",
            )

        refreshed_channel = await self.get_channel(channel["id"])
        if not refreshed_channel:
            return None
        return self._build_prepared_task_preview_status(refreshed_channel)

    def _save_cached_browser_urls_if_changed(self, item: dict, urls: dict[str, str | None]) -> bool:
        current_webrtc_api = item.get("webrtc_api")
        current_hls = item.get("hls")
        next_webrtc_api = urls.get("webrtc_api")
        next_hls = urls.get("hls")

        if current_webrtc_api == next_webrtc_api and current_hls == next_hls:
            return False

        item["webrtc_api"] = next_webrtc_api
        item["hls"] = next_hls
        item["updated_at"] = now_iso()
        self._save_channel_item(item)
        return True

    async def refresh_zone_preview_cached_urls(self, channel_id: str) -> dict[str, str | None] | None:
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        node = None
        if channel.get("media_node_id"):
            node = await self.media_node_service.get_node(channel.get("media_node_id"))

        urls = self._build_zone_preview_browser_urls(channel, node)

        await asyncio.to_thread(self._save_cached_browser_urls_if_changed, channel, urls)
        return urls

    async def refresh_channel_browser_cached_urls(self, channel_id: str) -> dict[str, str | None] | None:
        channel = await self.get_channel(channel_id)
        if not channel:
            return None

        channel_play = await self.play_urls(channel_id, mode="channel", compact=True)
        if not channel_play:
            return None

        final_browser_stream = (channel_play or {}).get("final_browser_stream") or {}
        urls = self._build_browser_play_url_map(final_browser_stream)

        await asyncio.to_thread(self._save_cached_browser_urls_if_changed, channel, urls)
        return urls

    async def set_task_preview_state(
        self,
        channel_id: str,
        mode: str,
        owner_task_id: str | None,
        play_url: dict[str, str | None] | None = None,
        status: str = "running",
        last_error: str | None = None,
    ) -> dict | None:
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        normalized_mode = str(mode or "").lower()
        if normalized_mode not in {"channel", "zone"}:
            raise ValueError(f"Unsupported task preview mode: {mode}")

        def _update():
            item = dict(channel)
            task_preview = self._ensure_task_preview_runtime(item)
            task_preview["prepared"] = True
            task_preview["mode"] = normalized_mode
            task_preview["owner_task_id"] = owner_task_id
            task_preview["status"] = status
            task_preview["last_error"] = last_error
            task_preview["play_url"] = dict(play_url) if play_url is not None else None
            task_preview["updated_at"] = now_iso()
            if play_url is not None:
                item["webrtc_api"] = play_url.get("webrtc_api")
                item["hls"] = play_url.get("hls")
            item["updated_at"] = now_iso()
            self._save_channel_item(item)
            return item

        return await asyncio.to_thread(_update)

    async def clear_task_preview_state(self, channel_id: str, owner_task_id: str | None = None) -> dict | None:
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        def _update():
            item = dict(channel)
            task_preview = self._ensure_task_preview_runtime(item)
            current_owner_task_id = str(task_preview.get("owner_task_id") or "")
            if owner_task_id and current_owner_task_id and current_owner_task_id != str(owner_task_id):
                return item
            item["webrtc_api"] = None
            item["hls"] = None
            item["updated_at"] = now_iso()
            item.setdefault("ext", {})["task_preview"] = {
                **self._default_task_preview_runtime(),
                "updated_at": now_iso(),
            }
            self._save_channel_item(item)
            return item

        return await asyncio.to_thread(_update)

    def _normalize_ai_models(self, config: dict) -> list[dict]:
        models = config.get("models")
        normalized: list[dict] = []
        if isinstance(models, list) and models:
            for item in models:
                if not isinstance(item, dict) or not item.get("model_id"):
                    continue
                normalized.append({
                    "model_id": item.get("model_id"),
                    "enabled": bool(item.get("enabled", True)),
                    "labels": list(item.get("labels") or []),
                    "conf": float(item.get("conf", config.get("conf", 0.25)) or 0.25),
                    "frame_interval": max(int(item.get("frame_interval", config.get("frame_interval", 10)) or 10), 1),
                    "zone_ids": list(item.get("zone_ids") or []),
                    "save_snapshot": bool(item.get("save_snapshot", config.get("save_snapshot", True))),
                })
            return normalized

        if config.get("model_id"):
            normalized.append({
                "model_id": config.get("model_id"),
                "enabled": bool(config.get("enabled")),
                "labels": list(config.get("labels") or []),
                "conf": float(config.get("conf", 0.25) or 0.25),
                "frame_interval": max(int(config.get("frame_interval", 10) or 10), 1),
                "zone_ids": list(config.get("zone_ids") or []),
                "save_snapshot": bool(config.get("save_snapshot", True)),
            })
        return normalized

    def _sync_legacy_ai_config(self, config: dict) -> None:
        models = self._normalize_ai_models(config)
        config["models"] = models
        primary = models[0] if models else {}
        config["model_id"] = primary.get("model_id")
        config["labels"] = primary.get("labels", [])
        config["conf"] = primary.get("conf", config.get("conf", 0.25))
        config["frame_interval"] = primary.get("frame_interval", config.get("frame_interval", 10))
        config["zone_ids"] = primary.get("zone_ids", [])
        config["save_snapshot"] = primary.get("save_snapshot", config.get("save_snapshot", True))
        config["enabled"] = any(item.get("enabled") for item in models) if models else bool(config.get("enabled"))

    def _pick_transcode_publish_host(self, node: dict | None) -> str:
        candidate_urls = [
            (node or {}).get("api_base"),
            (node or {}).get("play_base"),
            settings.ZLM_API_BASE,
        ]
        for candidate_url in candidate_urls:
            if not candidate_url:
                continue
            parsed = urlparse(candidate_url)
            candidate = (parsed.hostname or "").strip()
            if candidate:
                return candidate

        preferred = [
            (node or {}).get("transcode_rtmp_host"),
            (node or {}).get("public_rtmp_host"),
            (node or {}).get("public_http_host"),
        ]
        for candidate in preferred:
            if candidate and candidate not in {"localhost", "127.0.0.1"}:
                return candidate

        try:
            adapter = self._build_adapter(node)
            server_config = adapter.get_server_config()
            config_rows = server_config.get("data") or []
            if config_rows:
                extern_ip = (config_rows[0].get("rtc.externIP") or "").strip()
                for candidate in [part.strip() for part in extern_ip.split(",")]:
                    if candidate and candidate not in {"localhost", "127.0.0.1"}:
                        return candidate
        except Exception:
            pass

        return (node or {}).get("public_rtmp_host") or "localhost"

    def _build_browser_transcode_stream_name(self, channel_id: str, stream: str) -> str:
        return f"{channel_id}_browser"

    def _build_zone_preview_stream_name(self, channel_id: str, stream: str) -> str:
        return f"{channel_id}_zone"

    def _build_browser_transcode_target(self, node: dict | None, app: str, stream: str, channel_id: str) -> tuple[str, str]:
        browser_stream = self._build_browser_transcode_stream_name(channel_id, stream)
        rtmp_port = node.get("rtmp_port", settings.ZLM_RTMP_PORT) if node else settings.ZLM_RTMP_PORT
        target_host = self._pick_transcode_publish_host(node)
        target = f"rtmp://{target_host}:{rtmp_port}/{app}/{browser_stream}"
        return browser_stream, target

    def _build_zone_preview_target(self, node: dict | None, app: str, stream: str, channel_id: str) -> tuple[str, str]:
        zone_stream = self._build_zone_preview_stream_name(channel_id, stream)
        rtmp_port = node.get("rtmp_port", settings.ZLM_RTMP_PORT) if node else settings.ZLM_RTMP_PORT
        target_host = "zlm-server"
        target = f"rtmp://{target_host}:{rtmp_port}/{app}/{zone_stream}"
        return zone_stream, target

    def _build_zone_preview_source(self, node: dict | None, app: str, stream: str) -> str:
        rtsp_port = node.get("rtsp_port", settings.ZLM_RTSP_PORT) if node else settings.ZLM_RTSP_PORT
        return f"rtsp://zlm-server:{rtsp_port}/{app}/{stream}"

    def _start_browser_transcode(self, node: dict | None, item: dict) -> dict:
        ext = item.setdefault("ext", {})
        browser = ext.setdefault("browser_transcode", {})
        for field in ["pid", "log_path", "log_tail", "process_alive", "inspect_rtmp", "stopped_at", "startup_exception"]:
            browser.pop(field, None)
        ffmpeg_key = browser.get("ffmpeg_key")
        if ffmpeg_key:
            adapter = self._build_adapter(node)
            try:
                browser["zlm_ffmpeg_stop_before_restart"] = adapter.delete_ffmpeg_source(ffmpeg_key)
            except Exception as exc:
                browser["zlm_ffmpeg_stop_before_restart_error"] = str(exc)
            browser.pop("ffmpeg_key", None)
            browser.pop("zlm_ffmpeg_result", None)
            browser.pop("error", None)

        browser_stream, target = self._build_browser_transcode_target(node, item["app"], item["stream"], item["id"])
        adapter = self._build_adapter(node)
        try:
            existing = adapter.get_media_list(item["app"], browser_stream, "rtmp")
            if existing.get("code") == 0 and existing.get("data"):
                browser.update({
                    "enabled": True,
                    "app": item["app"],
                    "stream": browser_stream,
                    "target": target,
                    "inspect_rtmp": existing,
                    "started_at": now_iso(),
                    "reused_existing": True,
                })
                browser.pop("error", None)
                return browser
        except Exception:
            pass
        try:
            result = adapter.add_ffmpeg_source(
                source_url=item["source_url"],
                target_url=target,
                timeout_ms=15000,
                enable_hls=True,
                enable_mp4=False,
            )
        except Exception as exc:
            browser.update({
                "enabled": False,
                "app": item["app"],
                "stream": browser_stream,
                "target": target,
                "startup_exception": str(exc),
                "started_at": now_iso(),
            })
            return browser
        if result.get("code") != 0:
            browser.update({
                "enabled": False,
                "app": item["app"],
                "stream": browser_stream,
                "target": target,
                "error": result.get("msg") or str(result),
                "started_at": now_iso(),
            })
            return browser

        ffmpeg_key = ((result.get("data") or {}).get("key")) if isinstance(result.get("data"), dict) else None
        browser.update({
            "enabled": True,
            "app": item["app"],
            "stream": browser_stream,
            "target": target,
            "ffmpeg_key": ffmpeg_key,
            "zlm_ffmpeg_result": result,
            "started_at": now_iso(),
        })
        time.sleep(5)
        try:
            inspect = adapter.get_media_list(item["app"], browser_stream, "rtmp")
            browser["inspect_rtmp"] = inspect
            if inspect.get("code") != 0 or not inspect.get("data"):
                browser["error"] = "Browser transcode stream not published to media server"
            else:
                browser.pop("error", None)
        except Exception as exc:
                browser["error"] = str(exc)

        return browser

    def _stop_browser_transcode(self, node: dict | None, item: dict) -> dict | None:
        browser = item.setdefault("ext", {}).get("browser_transcode")
        if not browser:
            return None
        ffmpeg_key = browser.get("ffmpeg_key")
        adapter = self._build_adapter(node)
        if ffmpeg_key:
            try:
                browser["zlm_ffmpeg_stop"] = adapter.delete_ffmpeg_source(ffmpeg_key)
            except Exception as exc:
                browser["zlm_ffmpeg_stop_error"] = str(exc)
        browser["stopped_at"] = now_iso()
        browser.pop("ffmpeg_key", None)
        return browser

    def _get_path(self, channel_id: str) -> Path:
        return self.channels_dir / f"{channel_id}.json"

    def _save_channel_item(self, item: dict) -> None:
        channel_id = str(item.get("id") or "").strip()
        if not channel_id:
            raise ValueError("Channel id is required")
        item.setdefault("ext", {})
        save_json(self._get_path(channel_id), item)

    def _clear_stale_preview_runtime(self, payload: dict) -> bool:
        changed = False
        ext = payload.setdefault("ext", {})
        zone_preview = ext.get("zone_preview")
        if isinstance(zone_preview, dict):
            if zone_preview.get("enabled") or str(zone_preview.get("status") or "").lower() in {"running", "starting", "restarting"}:
                zone_preview["enabled"] = False
                zone_preview["status"] = "stopped"
                zone_preview["last_error"] = None
                zone_preview["stopped_at"] = now_iso()
                changed = True

        task_preview = ext.get("task_preview")
        if isinstance(task_preview, dict):
            if task_preview.get("prepared") or str(task_preview.get("status") or "").lower() in {"running", "starting", "restarting"}:
                ext["task_preview"] = {
                    **self._default_task_preview_runtime(),
                    "updated_at": now_iso(),
                }
                payload["webrtc_api"] = None
                payload["hls"] = None
                changed = True
        return changed

    def _load_channel_from_db(self, channel_id: str) -> dict | None:
        with SessionLocal() as session:
            row = session.query(ChannelMeta).filter(ChannelMeta.id == channel_id).one_or_none()
            if not row:
                return None
            return self._build_channel_from_metadata_row(row)

    def _load_channel_from_legacy_json(self, channel_id: str) -> dict | None:
        path = self._get_path(channel_id)
        if not path.exists():
            return None
        return load_json(path)

    def _is_task_running(self, task: dict) -> bool:
        runtime = (task or {}).get("runtime") or {}
        status = str(runtime.get("status") or task.get("status") or "").lower()
        return bool(runtime.get("is_running")) or status in {"running", "starting", "restarting"}

    async def restore_channel_proxies(self) -> dict:
        restored = 0
        reset = 0
        failed: list[dict] = []
        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                rows = [
                    str(row[0])
                    for row in (
                        session.query(ChannelMeta.id)
                        .filter(ChannelMeta.is_playing.is_(True))
                        .order_by(ChannelMeta.updated_at.desc(), ChannelMeta.id.desc())
                        .all()
                    )
                    if row and row[0]
                ]
                for channel_id in rows:
                    row = session.query(ChannelMeta).filter(ChannelMeta.id == channel_id).one_or_none()
                    if not row:
                        continue
                    payload = dict(row.raw_json or {})
                    payload["is_playing"] = False
                    payload["is_online"] = False
                    payload["updated_at"] = now_iso()
                    row.is_playing = False
                    row.is_online = False
                    row.updated_at = datetime.now()
                    row.raw_json = payload
                    reset += 1
                session.commit()

            with SessionLocal() as session:
                all_rows = session.query(ChannelMeta).all()
                for row in all_rows:
                    payload = dict(row.raw_json or {})
                    if not self._clear_stale_preview_runtime(payload):
                        continue
                    payload["updated_at"] = now_iso()
                    row.webrtc_api = payload.get("webrtc_api")
                    row.hls = payload.get("hls")
                    row.updated_at = datetime.now()
                    row.raw_json = payload
                    reset += 1
                session.commit()
            return {
                "restored": restored,
                "reset": reset,
                "failed": failed,
            }

        items = await asyncio.to_thread(list_json_items, self.channels_dir)
        channel_ids = [
            str(item.get("id"))
            for item in items
            if item.get("id") and item.get("is_playing")
        ]

        for channel_id in channel_ids:
            if not channel_id:
                continue
            try:
                channel = await self.get_channel_runtime(channel_id)
                if not channel:
                    continue
                channel["is_playing"] = False
                channel["is_online"] = False
                self._clear_stale_preview_runtime(channel)
                channel["updated_at"] = now_iso()
                await asyncio.to_thread(self._save_channel_item, channel)
                reset += 1
            except Exception as exc:
                failed.append({
                    "channel_id": channel_id,
                    "error": str(exc),
                })
        return {
            "restored": restored,
            "reset": reset,
            "failed": failed,
        }

    def _inspect_channel_online_state(self, adapter: ZLMAdapter, item: dict) -> tuple[bool, dict]:
        inspect_result = {
            "rtsp": adapter.get_media_list(item["app"], item["stream"], "rtsp"),
            "rtmp": adapter.get_media_list(item["app"], item["stream"], "rtmp"),
            "hls": adapter.get_media_list(item["app"], item["stream"], "hls"),
        }
        is_online = any(
            isinstance(result, dict) and bool(result.get("data"))
            for result in inspect_result.values()
        )
        return is_online, inspect_result

    def _is_wvp_upstream_online(self, item: dict) -> bool:
        upstream = ((item.get("ext") or {}).get("upstream") or {})
        if upstream.get("platform") != "wvp":
            return False
        if upstream.get("online") is True:
            return True
        play_info = upstream.get("play_info") or {}
        media_info = play_info.get("mediaInfo") or {}
        channel_info = upstream.get("channel_info") or {}
        truthy_text = {"on", "online", "true", "1", "yes"}
        for value in [
            media_info.get("online"),
            media_info.get("status"),
            channel_info.get("online"),
            channel_info.get("isOnline"),
            channel_info.get("gbOnline"),
            channel_info.get("status"),
            channel_info.get("gbStatus"),
        ]:
            if value is True:
                return True
            if isinstance(value, (int, float)) and value == 1:
                return True
            if isinstance(value, str) and value.strip().lower() in truthy_text:
                return True
        return False

    def _build_channel_from_metadata_row(self, row: ChannelMeta) -> dict:
        payload = dict(row.raw_json or {})
        payload.setdefault("id", row.id)
        payload["device_id"] = row.device_id
        payload["media_node_id"] = row.media_node_id
        payload["name"] = row.name
        payload["type"] = row.type
        payload["source_url"] = row.source_url
        payload["app"] = row.app
        payload["stream"] = row.stream
        payload["is_online"] = bool(row.is_online) or self._is_wvp_upstream_online(payload)
        payload["is_playing"] = row.is_playing
        payload["ai_enabled"] = row.ai_enabled
        payload["record_mode"] = row.record_mode
        payload["snapshot_url"] = row.snapshot_url
        payload["snapshot_path"] = row.snapshot_path
        payload["webrtc_api"] = self._normalize_public_play_url(row.webrtc_api)
        payload["hls"] = self._normalize_public_play_url(row.hls)
        payload["created_at"] = format_datetime(row.created_at)
        payload["updated_at"] = format_datetime(row.updated_at)
        payload.setdefault("ext", {})
        task_preview = payload["ext"].get("task_preview")
        if isinstance(task_preview, dict) and isinstance(task_preview.get("play_url"), dict):
            task_preview["play_url"] = self._normalize_browser_play_url_map(task_preview.get("play_url"))
        zone_preview = payload["ext"].get("zone_preview")
        if isinstance(zone_preview, dict) and isinstance(zone_preview.get("urls"), dict):
            zone_urls = dict(zone_preview.get("urls") or {})
            for key in (
                "hls",
                "rtmp",
                "rtsp",
                "webrtc",
                "httpflv",
                "browser_hls_url",
                "browser_httpflv_url",
                "browser_webrtc_api_url",
            ):
                value = self._normalize_public_play_url(zone_urls.get(key))
                if value:
                    zone_urls[key] = value
            zone_preview["urls"] = zone_urls
        return payload

    async def list_channels(self, device_id: str | None = None, type_filter: str | None = None, key: str | None = None):
        started_at = time.perf_counter()
        if settings.METADATA_DB_ENABLED:
            with SessionLocal() as session:
                rows = session.query(ChannelMeta).order_by(ChannelMeta.created_at.desc(), ChannelMeta.id.desc()).all()
            items = [self._build_channel_from_metadata_row(row) for row in rows]
            if device_id:
                items = [item for item in items if item.get("device_id") == device_id]
            if type_filter:
                items = [item for item in items if item.get("type") == type_filter]
            if key:
                items = [item for item in items if key.lower() in str(item).lower()]
            result = {
                "items": sorted(
                    items,
                    key=lambda x: (
                        str(x.get("created_at") or ""),
                        str(x.get("id") or ""),
                    ),
                    reverse=True,
                )
            }
            logger.info(
                "[ChannelListTiming] source=metadata count=%s elapsed_ms=%s",
                len(result["items"]),
                round((time.perf_counter() - started_at) * 1000, 2),
            )
            return result

        node_cache: dict[str, dict | None] = {}

        async def _resolve_node(node_id: str | None):
            if not node_id:
                return None
            if node_id not in node_cache:
                node_cache[node_id] = await self.media_node_service.get_node(node_id)
            return node_cache[node_id]

        def _list(items: list[dict]):
            return {
                "items": sorted(
                    items,
                    key=lambda x: (
                        str(x.get("created_at") or ""),
                        str(x.get("id") or ""),
                    ),
                    reverse=True,
                )
            }

        with SessionLocal() as session:
            rows = session.query(ChannelMeta).order_by(ChannelMeta.created_at.desc(), ChannelMeta.id.desc()).all()

        if rows:
            items = [
                self._build_channel_from_metadata_row(row)
                for row in rows
            ]
        else:
            items = await asyncio.to_thread(list_json_items, self.channels_dir)
        if device_id:
            items = [item for item in items if item.get("device_id") == device_id]
        if type_filter:
            items = [item for item in items if item.get("type") == type_filter]
        if key:
            items = [item for item in items if key.lower() in str(item).lower()]

        changed_items: list[tuple[Path, dict]] = []
        refreshed_items: list[dict] = []
        for item in items:
            path = self._get_path(item.get("id"))
            refreshed = dict(item)
            try:
                node = await _resolve_node(refreshed.get("media_node_id"))
                adapter = self._build_adapter(node)
                is_online, inspect_result = await asyncio.to_thread(self._inspect_channel_online_state, adapter, refreshed)
                upstream = ((refreshed.get("ext") or {}).get("upstream") or {})
                if not is_online and upstream.get("platform") == "wvp" and upstream.get("online") is True:
                    # WVP 通道在未建立本地代理前，也应展示为上游在线，便于前端自动拉起浏览器播放。
                    is_online = True
                refreshed["is_online"] = is_online
                refreshed.setdefault("ext", {})["last_media_probe"] = inspect_result

                if not is_online and refreshed.get("is_playing"):
                    refreshed["is_playing"] = False
                runtime_exists = path.exists()
                if runtime_exists and (
                    refreshed.get("is_online") != item.get("is_online") or refreshed.get("is_playing") != item.get("is_playing")
                ):
                    refreshed["updated_at"] = now_iso()
                    changed_items.append((path, refreshed))
            except Exception as exc:
                refreshed.setdefault("ext", {})["last_media_probe_error"] = str(exc)
            refreshed_items.append(refreshed)

        if changed_items:
            items = list_json_items(self.channels_dir)
            latest_map = {entry.get("id"): entry for entry in items}
            for path, changed in changed_items:
                latest = latest_map.get(changed.get("id"), changed)
                latest.update({
                    "is_online": changed.get("is_online", latest.get("is_online")),
                    "is_playing": changed.get("is_playing", latest.get("is_playing")),
                    "updated_at": changed.get("updated_at", latest.get("updated_at")),
                })
                latest.setdefault("ext", {}).update(changed.get("ext", {}))
                await asyncio.to_thread(save_json, path, latest)

        result = await asyncio.to_thread(_list, refreshed_items)
        logger.info(
            "[ChannelListTiming] source=runtime count=%s elapsed_ms=%s",
            len(result["items"]),
            round((time.perf_counter() - started_at) * 1000, 2),
        )
        return result

    async def list_available_for_task_channels(self):
        channels_result = await self.list_channels()
        items = list(channels_result.get("items") or [])
        occupied_channel_ids: set[str] = set()
        with SessionLocal() as session:
            task_rows = session.query(TaskMeta.channel_id).filter(TaskMeta.channel_id.is_not(None)).all()

        if settings.METADATA_DB_ENABLED:
            occupied_channel_ids = {
                str(row[0])
                for row in task_rows
                if row and row[0]
            }
        elif task_rows:
            occupied_channel_ids = {
                str(row[0])
                for row in task_rows
                if row and row[0]
            }
        else:
            tasks_dir = settings.TASKS_DIR
            task_items = await asyncio.to_thread(list_json_items, tasks_dir)
            for task in task_items:
                if not isinstance(task, dict):
                    continue
                channel_id = task.get("channel_id")
                if not channel_id:
                    continue
                occupied_channel_ids.add(str(channel_id))

        available_items = [
            item for item in items
            if str(item.get("id") or "") not in occupied_channel_ids
        ]
        return {"items": available_items}

    async def get_channel(self, channel_id: str):
        if settings.METADATA_DB_ENABLED:
            return await asyncio.to_thread(self._load_channel_from_db, channel_id)

        path = self._get_path(channel_id)
        json_channel = None
        if await asyncio.to_thread(path.exists):
            json_channel = await asyncio.to_thread(load_json, path)

        with SessionLocal() as session:
            row = session.query(ChannelMeta).filter(ChannelMeta.id == channel_id).one_or_none()

        if row:
            return self._build_channel_from_metadata_row(row)

        return json_channel

    async def get_channel_runtime(self, channel_id: str):
        if settings.METADATA_DB_ENABLED:
            return await asyncio.to_thread(self._load_channel_from_db, channel_id)

        path = self._get_path(channel_id)
        if not await asyncio.to_thread(path.exists):
            return None
        return await asyncio.to_thread(load_json, path)

    def _has_valid_snapshot(self, channel: dict) -> bool:
        snapshot_path = str(channel.get("snapshot_path") or "").strip()
        return bool(snapshot_path and Path(snapshot_path).exists())

    def _capture_snapshot_for_channel(self, channel_id: str, channel: dict) -> dict:
        source_url = str(channel.get("source_url") or "").strip()
        if not source_url:
            raise ValueError("Channel source is not configured for snapshot")

        cap = cv2.VideoCapture(source_url)
        if not cap.isOpened():
            raise ValueError("Failed to open channel source for snapshot")

        ok, frame = cap.read()
        cap.release()
        if not ok:
            raise ValueError("Failed to read frame for snapshot")

        filename = f"{channel_id}_{generate_id('snap')}.jpg"
        output_path = self.snapshots_dir / filename
        cv2.imwrite(str(output_path), frame)

        item = self._load_channel_from_db(channel_id) if settings.METADATA_DB_ENABLED else self._load_channel_from_legacy_json(channel_id)
        if not item:
            raise ValueError("Channel not found")
        item["snapshot_url"] = f"/static/snapshots/{filename}"
        item["snapshot_path"] = str(output_path.resolve())
        item["updated_at"] = now_iso()
        self._save_channel_item(item)
        return {
            "channel_id": channel_id,
            "snapshot_url": item["snapshot_url"],
            "snapshot_path": item["snapshot_path"],
        }

    async def ensure_channel_snapshot(self, channel_id: str, force: bool = False):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        if not str(channel.get("source_url") or "").strip():
            return {"channel_id": channel_id, "status": "skipped", "reason": "missing_source_url"}

        if not force and self._has_valid_snapshot(channel):
            return {"channel_id": channel_id, "status": "skipped", "reason": "snapshot_exists"}

        snapshot = await asyncio.to_thread(self._capture_snapshot_for_channel, channel_id, channel)
        return {
            **snapshot,
            "status": "captured",
            "reason": "captured",
            "captured_at": now_iso(),
        }

    def schedule_channel_snapshot(self, channel_id: str, force: bool = False):
        async def _runner():
            try:
                await self.ensure_channel_snapshot(channel_id, force=force)
            except Exception:
                logger.exception("[ChannelAutoSnapshot] failed channel_id=%s force=%s", channel_id, force)

        try:
            return asyncio.create_task(_runner())
        except RuntimeError:
            logger.warning("[ChannelAutoSnapshot] skipped schedule because no running loop channel_id=%s", channel_id)
            return None

    async def request_snapshot_refresh(self, channel_id: str):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        running_task = self._snapshot_refresh_tasks.get(channel_id)
        if running_task and not running_task.done():
            return {
                "channel_id": channel_id,
                "status": "running",
                "snapshot_url": channel.get("snapshot_url"),
                "snapshot_path": channel.get("snapshot_path"),
            }

        task = self.schedule_channel_snapshot(channel_id, force=True)
        if task is not None:
            self._snapshot_refresh_tasks[channel_id] = task

            if hasattr(task, "add_done_callback"):
                def _cleanup(_task):
                    current_task = self._snapshot_refresh_tasks.get(channel_id)
                    if current_task is _task:
                        self._snapshot_refresh_tasks.pop(channel_id, None)

                task.add_done_callback(_cleanup)

        return {
            "channel_id": channel_id,
            "status": "pending",
            "snapshot_url": channel.get("snapshot_url"),
            "snapshot_path": channel.get("snapshot_path"),
        }

    async def create_channel(self, request):
        media_node_id = request.media_node_id
        if media_node_id:
            node = await self.media_node_service.get_node(media_node_id)
            if not node:
                raise ValueError(f"Media node not found: {media_node_id}")

        def _create():
            channel_id = generate_id("ch")
            parsed = urlparse(request.source_url)
            inferred_app = request.app or ("push" if request.type == "rtmp" else "live")
            inferred_stream = request.stream or Path(parsed.path).name or channel_id
            item = {
                "id": channel_id,
                "device_id": request.device_id,
                "name": request.name,
                "type": request.type,
                "source_url": request.source_url,
                "app": inferred_app,
                "stream": inferred_stream,
                "media_node_id": request.media_node_id,
                "is_online": False,
                "is_playing": False,
                "ai_enabled": False,
                "record_mode": request.record_mode or "none",
                "snapshot_url": None,
                "snapshot_path": None,
                "webrtc_api": None,
                "hls": None,
                "ext": {
                    "zones": [],
                    "ai_config": {
                        "enabled": False,
                        "model_id": None,
                        "labels": [],
                        "conf": 0.25,
                        "frame_interval": 10,
                        "zone_ids": [],
                        "save_snapshot": True,
                        "models": [],
                    },
                },
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
            self._save_channel_item(item)
            return channel_id, item

        channel_id, item = await asyncio.to_thread(_create)
        try:
            self.schedule_channel_snapshot(channel_id)
        except Exception:
            logger.exception("[ChannelAutoSnapshot] schedule failed after channel create channel_id=%s", channel_id)
        try:
            await self.play(channel_id)
            return await self.get_channel(channel_id)
        except Exception as exc:
            path = self._get_path(channel_id)

            def _mark_error():
                latest = self._load_channel_from_db(channel_id) if settings.METADATA_DB_ENABLED else self._load_channel_from_legacy_json(channel_id)
                if not latest:
                    latest = dict(item)
                latest.setdefault("ext", {})["auto_play_error"] = str(exc)
                latest["updated_at"] = now_iso()
                self._save_channel_item(latest)
                return latest

            await asyncio.to_thread(_mark_error)
            return await self.get_channel(channel_id)

    async def update_channel(self, channel_id: str, request):
        runtime_channel = await self.get_channel_runtime(channel_id)
        if not runtime_channel:
            return None

        def _update():
            item = dict(runtime_channel or {})
            for field in ["device_id", "name", "type", "source_url", "app", "stream", "media_node_id", "record_mode"]:
                value = getattr(request, field, None)
                if value is not None:
                    item[field] = value
            item.setdefault("ext", {})
            item["updated_at"] = now_iso()
            self._save_channel_item(item)
            return item

        await asyncio.to_thread(_update)
        return await self.get_channel(channel_id)

    async def delete_channel(self, channel_id: str):
        path = self._get_path(channel_id)
        channel_view = await self.get_channel(channel_id)
        if not channel_view:
            return None

        def _delete():
            item = channel_view
            task_ids_to_delete: set[str] = set()
            for task_path in settings.TASKS_DIR.glob("*.json"):
                try:
                    task_item = load_json(task_path)
                except Exception:
                    continue
                if str(task_item.get("channel_id") or "") != str(channel_id):
                    continue
                task_id = str(task_item.get("id") or "").strip()
                if task_id:
                    task_ids_to_delete.add(task_id)
                task_path.unlink(missing_ok=True)
                delete_json_document(task_path, task_item.get("id"))

            with SessionLocal() as session:
                task_rows = session.query(TaskMeta.id).filter(TaskMeta.channel_id == channel_id).all()
            for row in task_rows:
                task_id = str(row[0] or "").strip() if row else ""
                if task_id:
                    task_ids_to_delete.add(task_id)

            for task_id in task_ids_to_delete:
                delete_json_document(settings.TASKS_DIR / f"{task_id}.json", task_id)

            if path.exists():
                path.unlink()
            delete_json_document(path, item.get("id"))
            return item

        deleted = await asyncio.to_thread(_delete)
        return deleted

    async def play(self, channel_id: str):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None
        upstream = ((channel.get("ext") or {}).get("upstream") or {})
        if upstream.get("platform") == "wvp":
            try:
                channel = await asyncio.to_thread(self.wvp_service.refresh_channel_source, channel)
            except Exception:
                pass

        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)

        def _play():
            item = self._load_channel_from_db(channel_id) if settings.METADATA_DB_ENABLED else self._load_channel_from_legacy_json(channel_id)
            if not item:
                raise ValueError("Channel not found")
            try:
                zlm_result = adapter.add_stream_proxy(item["app"], item["stream"], item["source_url"])
                zlm_code = zlm_result.get("code")
                zlm_msg = str(zlm_result.get("msg") or zlm_result.get("message") or "")
                if zlm_code != 0:
                    if "already exists" not in zlm_msg.lower():
                        raise ValueError(zlm_msg or str(zlm_result))
                    zlm_result = {
                        **zlm_result,
                        "already_exists": True,
                    }

                item["is_playing"] = True
                item["is_online"] = True
                item.setdefault("ext", {})["zlm_proxy"] = zlm_result
                browser_transcode = self._start_browser_transcode(node, item)
                item.setdefault("ext", {})["browser_transcode"] = browser_transcode
                item.setdefault("ext", {}).setdefault("zone_preview", {
                    "enabled": False,
                    "status": "stopped",
                    "stream": self._build_zone_preview_stream_name(item["id"], item["stream"]),
                    "target": None,
                    "urls": {},
                    "last_error": None,
                    "started_at": None,
                    "stopped_at": None,
                })
                item["updated_at"] = now_iso()
                self._save_channel_item(item)
                browser_urls = None
                browser_ready = bool((browser_transcode.get("inspect_rtmp") or {}).get("data")) and not browser_transcode.get("error")
                if browser_transcode.get("enabled") and browser_transcode.get("stream") and browser_ready:
                    browser_urls = adapter.build_play_urls(
                        browser_transcode.get("app", item["app"]),
                        browser_transcode.get("stream"),
                        item.get("media_node_id"),
                    )
                return {
                    "channel_id": channel_id,
                    "status": "playing",
                    "zlm_result": zlm_result,
                    "urls": adapter.build_play_urls(item["app"], item["stream"], item.get("media_node_id")),
                    "browser_urls": browser_urls,
                }
            except Exception as e:
                item["is_playing"] = False
                item["is_online"] = False
                item.setdefault("ext", {})["zlm_error"] = str(e)
                item["updated_at"] = now_iso()
                self._save_channel_item(item)
                raise ValueError(f"Failed to start stream proxy: {e}")

        return await asyncio.to_thread(_play)

    async def stop(self, channel_id: str):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)

        def _stop():
            item = self._load_channel_from_db(channel_id) if settings.METADATA_DB_ENABLED else self._load_channel_from_legacy_json(channel_id)
            if not item:
                raise ValueError("Channel not found")
            try:
                zlm_result = adapter.delete_stream_proxy(item["app"], item["stream"])
                if isinstance(zlm_result, dict) and zlm_result.get("code") not in (None, 0):
                    item.setdefault("ext", {})["zlm_proxy_stop_error"] = zlm_result
            except Exception as e:
                zlm_result = {"error": str(e)}

            browser_stop = self._stop_browser_transcode(node, item)
            try:
                asyncio.run(zone_preview_service.stop(channel_id))
            except Exception:
                pass
            item["is_playing"] = False
            item["is_online"] = False
            item.setdefault("ext", {})["zlm_proxy_stop"] = zlm_result
            if browser_stop is not None:
                item.setdefault("ext", {})["browser_transcode"] = browser_stop
            item["updated_at"] = now_iso()
            self._save_channel_item(item)
            return {
                "channel_id": channel_id,
                "status": "stopped",
                "zlm_result": zlm_result,
            }

        return await asyncio.to_thread(_stop)

    async def inspect_stream(self, channel_id: str):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)
        browser = channel.get("ext", {}).get("browser_transcode", {})

        def _inspect():
            result = {
                "rtsp": adapter.get_media_list(channel["app"], channel["stream"], "rtsp"),
                "rtmp": adapter.get_media_list(channel["app"], channel["stream"], "rtmp"),
                "hls": adapter.get_media_list(channel["app"], channel["stream"], "hls"),
            }
            if browser.get("stream"):
                result["browser_stream"] = {
                    "stream": browser.get("stream"),
                    "process_alive": bool(browser.get("ffmpeg_key")),
                    "enabled": browser.get("enabled"),
                    "error": browser.get("error"),
                    "startup_exception": browser.get("startup_exception"),
                    "ffmpeg_key": browser.get("ffmpeg_key"),
                    "target": browser.get("target"),
                    "raw": browser,
                    "zlm_ffmpeg_result": browser.get("zlm_ffmpeg_result"),
                    "rtmp": adapter.get_media_list(browser.get("app", channel["app"]), browser["stream"], "rtmp"),
                    "hls": adapter.get_media_list(browser.get("app", channel["app"]), browser["stream"], "hls"),
                }
            zone_preview = channel.get("ext", {}).get("zone_preview", {})
            if zone_preview.get("stream"):
                result["zone_preview_stream"] = {
                    "stream": zone_preview.get("stream"),
                    "enabled": zone_preview.get("enabled"),
                    "status": zone_preview.get("status"),
                    "error": zone_preview.get("last_error"),
                    "target": zone_preview.get("target"),
                    "raw": zone_preview,
                    "rtmp": adapter.get_media_list(channel["app"], zone_preview["stream"], "rtmp"),
                    "hls": adapter.get_media_list(channel["app"], zone_preview["stream"], "hls"),
                }
            return result

        return await asyncio.to_thread(_inspect)

    async def _set_playing(self, channel_id: str, is_playing: bool):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        def _update():
            item = dict(channel)
            item["is_playing"] = is_playing
            item["updated_at"] = now_iso()
            self._save_channel_item(item)
            return item

        return await asyncio.to_thread(_update)

    async def play_urls(self, channel_id: str, mode: str = "auto", compact: bool = False):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)
        urls = adapter.build_play_urls(channel["app"], channel["stream"], channel.get("media_node_id"))
        browser = channel.get("ext", {}).get("browser_transcode", {})
        browser_urls = None
        browser_ready = bool((browser.get("inspect_rtmp") or {}).get("data")) and not browser.get("error")
        if browser.get("enabled") and browser.get("stream") and browser_ready:
            browser_urls = adapter.build_play_urls(
                browser.get("app", channel["app"]),
                browser.get("stream"),
                channel.get("media_node_id"),
            )
        zone_preview = channel.get("ext", {}).get("zone_preview", {}) or {}
        zone_preview_urls = None
        zone_stream = zone_preview.get("stream")
        zone_preview_running = bool(zone_preview.get("enabled")) or str(zone_preview.get("status") or "").lower() == "running"
        if zone_stream and zone_preview_running:
            zone_preview_urls = adapter.build_play_urls(
                channel["app"],
                zone_stream,
                channel.get("media_node_id"),
            )
        normalized_mode = str(mode or "auto").lower()
        if normalized_mode not in {"auto", "channel", "zone"}:
            normalized_mode = "auto"

        has_zone_preview = bool(zone_preview_urls)
        resolved_mode = normalized_mode
        if normalized_mode == "auto":
            resolved_mode = "zone" if has_zone_preview else "channel"
        elif normalized_mode == "zone" and not has_zone_preview:
            resolved_mode = "channel"

        if resolved_mode == "zone":
            selected_urls = zone_preview_urls or {}
            selected_browser_urls = zone_preview_urls or {}
            selected_source = "zone"
            source_urls = zone_preview_urls or {}
            source_browser_urls = zone_preview_urls or {}
        else:
            selected_urls = urls
            selected_browser_urls = browser_urls or urls
            selected_source = "channel"
            source_urls = urls
            source_browser_urls = browser_urls or urls

        response = {
            "channel_id": channel_id,
            "requested_mode": normalized_mode,
            "resolved_mode": resolved_mode,
            "source": selected_source,
            "status": "online" if channel.get("is_online") else "offline",
            "channel_stream": urls,
            "browser_stream": browser_urls,
            "zone_stream": zone_preview_urls,
            "final_stream": selected_urls,
            "final_browser_stream": selected_browser_urls,
            "source_urls": source_urls,
            "source_browser_urls": source_browser_urls,
            "final_play_urls": selected_urls,
            "final_browser_play_urls": selected_browser_urls,
            "urls": urls,
            "browser_urls": browser_urls,
            "zone_preview_urls": zone_preview_urls,
            "play_urls": selected_urls,
            "browser_play_urls": selected_browser_urls,
        }
        if compact:
            return {
                "channel_id": response["channel_id"],
                "requested_mode": response["requested_mode"],
                "resolved_mode": response["resolved_mode"],
                "source": response["source"],
                "status": response["status"],
                "channel_stream": response["channel_stream"],
                "browser_stream": response["browser_stream"],
                "zone_stream": response["zone_stream"],
                "final_stream": response["final_stream"],
                "final_browser_stream": response["final_browser_stream"],
            }
        return response

    async def latest_snapshot(self, channel_id: str):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None
        return {
            "channel_id": channel_id,
            "snapshot_url": channel.get("snapshot_url"),
            "snapshot_path": channel.get("snapshot_path"),
        }

    async def snapshot(self, channel_id: str):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None
        snapshot = await asyncio.to_thread(self._capture_snapshot_for_channel, channel_id, channel)
        return {
            "channel_id": channel_id,
            "snapshot_url": snapshot["snapshot_url"],
            "snapshot_path": snapshot["snapshot_path"],
            "captured_at": now_iso(),
        }

    async def get_zones(self, channel_id: str):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None
        return {"items": channel.get("ext", {}).get("zones", [])}

    async def add_zone(self, channel_id: str, request):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        def _add():
            item = dict(channel)
            zones = item.setdefault("ext", {}).setdefault("zones", [])
            zone = {
                "id": generate_id("zone"),
                "name": request.name,
                "color": request.color,
                "labels": [],
                "coordinates": request.coordinates,
            }
            zones.append(zone)
            item["updated_at"] = now_iso()
            self._save_channel_item(item)
            return zone

        return await asyncio.to_thread(_add)

    async def update_zone(self, channel_id: str, zone_id: str, request):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        def _update():
            item = dict(channel)
            for zone in item.setdefault("ext", {}).setdefault("zones", []):
                if zone.get("id") == zone_id:
                    for field in ["name", "color", "coordinates"]:
                        value = getattr(request, field, None)
                        if value is not None:
                            zone[field] = value
                    zone["labels"] = []
                    item["updated_at"] = now_iso()
                    self._save_channel_item(item)
                    return zone
            return None

        return await asyncio.to_thread(_update)

    async def delete_zone(self, channel_id: str, zone_id: str):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        def _delete():
            item = dict(channel)
            zones = item.setdefault("ext", {}).setdefault("zones", [])
            remaining = [zone for zone in zones if zone.get("id") != zone_id]
            if len(remaining) == len(zones):
                return None
            item["ext"]["zones"] = remaining
            item["updated_at"] = now_iso()
            self._save_channel_item(item)
            return {"ok": True}

        return await asyncio.to_thread(_delete)

    async def get_ai_config(self, channel_id: str):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None
        config = channel.get("ext", {}).get("ai_config", {})
        self._sync_legacy_ai_config(config)
        return config

    async def update_ai_config(self, channel_id: str, request):
        channel = await self.get_channel_runtime(channel_id)
        if not channel:
            return None

        def _update():
            item = dict(channel)
            config = item.setdefault("ext", {}).setdefault("ai_config", {})
            item.setdefault("ext", {}).setdefault("ai_runtime", {
                "status": "stopped",
                "last_error": None,
                "last_analysis_at": None,
                "last_event_at": None,
                "started_at": None,
                "stopped_at": None,
                "frame_count": 0,
                "event_count": 0,
            })
            for field in ["enabled", "model_id", "labels", "conf", "frame_interval", "zone_ids", "save_snapshot", "models"]:
                value = getattr(request, field, None)
                if value is not None:
                    config[field] = value
            self._sync_legacy_ai_config(config)
            item["ai_enabled"] = bool(config.get("enabled"))
            item["updated_at"] = now_iso()
            self._save_channel_item(item)
            return config

        return await asyncio.to_thread(_update)

    async def start_ai(self, channel_id: str):
        config = await self.get_ai_config(channel_id)
        if config is None:
            return None
        request = type("Req", (), {**config, "enabled": True})()
        await self.update_ai_config(channel_id, request)
        return await analysis_service.start_channel_analysis(channel_id)

    async def apply_ai(self, channel_id: str):
        config = await self.get_ai_config(channel_id)
        if config is None:
            return None
        request = type("Req", (), {**config, "enabled": True})()
        await self.update_ai_config(channel_id, request)
        return await analysis_service.restart_channel_analysis(channel_id)

    async def stop_ai(self, channel_id: str):
        config = await self.get_ai_config(channel_id)
        if config is None:
            return None
        request = type("Req", (), {**config, "enabled": False})()
        await self.update_ai_config(channel_id, request)
        return await analysis_service.stop_channel_analysis(channel_id)

    async def get_ai_status(self, channel_id: str):
        return await analysis_service.get_channel_analysis_status(channel_id)

    async def start_zone_preview(
        self,
        channel_id: str,
        zones: list[dict] | None = None,
        zone_source: str | None = None,
        owner_task_id: str | None = None,
    ):
        started_at = time.perf_counter()
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        explicit_zones = zones is not None
        effective_zones = list(zones or [])
        if not explicit_zones and not effective_zones:
            effective_zones = list(channel.get("ext", {}).get("zones", []) or [])

        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)
        source_url = self._build_zone_preview_source(node, channel["app"], channel["stream"])
        zone_stream, target = self._build_zone_preview_target(node, channel["app"], channel["stream"], channel["id"])
        urls = adapter.build_play_urls(channel["app"], zone_stream, channel.get("media_node_id"))
        result = await zone_preview_service.start(
            channel_id,
            source_url,
            zone_stream,
            target,
            urls,
            zones=effective_zones,
            zone_source=zone_source or ("task" if explicit_zones else "channel"),
            owner_task_id=owner_task_id,
        )
        logger.debug(
            "[ZonePreviewService] start_zone_preview channel_id=%s zone_count=%s zone_source=%s owner_task_id=%s elapsed_ms=%s status=%s",
            channel_id,
            len(effective_zones),
            zone_source or ("task" if explicit_zones else "channel"),
            owner_task_id,
            round((time.perf_counter() - started_at) * 1000, 2),
            (result or {}).get("status"),
        )
        return result

    async def start_task_zone_preview(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None

        prepared = self._build_prepared_task_preview_status(channel) or await self._bootstrap_running_task_preview(channel)
        if not prepared:
            raise ValueError("当前通道未准备电子围栏播放信息，请先启动任务")

        if prepared.get("resolved_mode") == "channel" and not channel.get("is_playing"):
            await self.play(channel_id)
            channel = await self.get_channel(channel_id)
            if not channel:
                return None
            prepared = self._build_prepared_task_preview_status(channel)

        return prepared

    async def stop_zone_preview(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        return await zone_preview_service.stop(channel_id)

    async def get_zone_preview_status(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        prepared = self._build_prepared_task_preview_status(channel)
        if prepared:
            return prepared
        return zone_preview_service.status(channel_id)

    async def get_zone_preview_play_urls(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)
        zone_preview = channel.get("ext", {}).get("zone_preview", {}) or {}
        zone_stream = zone_preview.get("stream") or self._build_zone_preview_stream_name(channel["id"], channel["stream"])
        urls = adapter.build_play_urls(channel["app"], zone_stream, channel.get("media_node_id"))
        current = zone_preview_service.status(channel_id) or {}
        return {
            "channel_id": channel_id,
            "stream": zone_stream,
            "status": current.get("status") or zone_preview.get("status") or "stopped",
            "enabled": bool(current.get("enabled")),
            "urls": urls,
            "target": current.get("target") or zone_preview.get("target"),
            "last_error": current.get("last_error") or zone_preview.get("last_error"),
        }

    async def get_zone_preview_browser_play_url(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None

        prepared = self._build_prepared_task_preview_status(channel)
        if prepared:
            return {
                "channel_id": channel_id,
                "resolved_mode": prepared.get("resolved_mode"),
                "source": prepared.get("resolved_mode"),
                "status": prepared.get("status"),
                "play_url": prepared.get("play_url")
                or self._build_task_preview_play_url(
                    channel,
                    mode=str(prepared.get("resolved_mode") or "channel"),
                ),
            }

        return {
            "channel_id": channel_id,
            "resolved_mode": "unknown",
            "source": "cache",
            "status": "stopped",
            "play_url": self._build_channel_browser_play_url(channel),
        }

    async def debug_ai_once(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None

        ai_config = channel.get("ext", {}).get("ai_config", {})
        self._sync_legacy_ai_config(ai_config)
        model_id = ai_config.get("model_id")
        if not model_id:
            raise ValueError("AI model_id is not configured for this channel")

        model_dir = settings.REGISTRY_DIR / model_id
        model_file = model_dir / "model.json"
        if not model_file.exists():
            raise ValueError(f"Model not found: {model_id}")

        model_meta = await asyncio.to_thread(load_json, model_file)
        weights_path = model_meta.get("weights_path")
        if not weights_path:
            raise ValueError("Model weights_path not found in model.json")

        snapshot = await self.snapshot(channel_id)
        snapshot_url = snapshot.get("snapshot_url")
        snapshot_path = snapshot.get("snapshot_path")
        if not snapshot_path and snapshot_url and snapshot_url.startswith("/static/"):
            relative_path = snapshot_url.replace("/static/", "", 1)
            snapshot_path = str((settings.DATA_DIR / relative_path).resolve())

        if not snapshot_path or not Path(snapshot_path).exists():
            raise ValueError("Snapshot file was not created")

        detections = await asyncio.to_thread(
            self._run_detection_once,
            weights_path,
            model_meta,
            snapshot_path,
            ai_config.get("conf", 0.25),
        )
        detections = analysis_service._filter_detections_by_labels(detections, channel)
        detections = analysis_service._filter_detections_by_zones(detections, channel)

        events = []
        if detections:
            for detection in detections:
                events.append(await self.event_service.create_event({
                    "channel_id": channel_id,
                    "model_id": model_id,
                    "event_type": "debug_detection",
                    "class_name": detection.get("class_name", "unknown"),
                    "conf": detection.get("conf", 0),
                    "snapshot_path": snapshot_path,
                    "remark": json.dumps(detection, ensure_ascii=False),
                    "status": "new",
                }))
        else:
            events.append(await self.event_service.create_event({
                "channel_id": channel_id,
                "model_id": model_id,
                "event_type": "debug_detection",
                "class_name": "no_detection",
                "conf": 0,
                "snapshot_path": snapshot_path,
                "remark": "No objects detected in debug AI once",
                "status": "new",
            }))

        return {
            "channel_id": channel_id,
            "detections": detections,
            "events": events,
        }

    def _run_detection_once(self, weights_path: str, model_meta: dict, image_path: str, conf_threshold: float):
        from ultralytics import YOLO

        model = YOLO(str(weights_path))
        results = model(
            image_path,
            verbose=False,
            conf=conf_threshold,
            device=analysis_service._resolve_inference_device(),
        )
        detections = []
        classes = model_meta.get("classes", [])
        for result in results:
            boxes = result.boxes
            for i in range(len(boxes)):
                box = boxes[i]
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                class_id = int(box.cls[0])
                class_name = classes[class_id] if class_id < len(classes) else f"class_{class_id}"
                detections.append({
                    "class_id": class_id,
                    "class_name": class_name,
                    "conf": conf,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                })
        return detections
