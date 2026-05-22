import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import cv2

from src.core.settings import settings
from src.services.event_service import EventService
from src.services.analysis_service import analysis_service
from src.services.media_node_service import MediaNodeService
from src.services.metadata_db_sync import delete_json_document
from src.services.model_service import ModelService
from src.services.platform_storage import generate_id, list_json_items, load_json, now_iso, save_json
from src.services.zlm_adapter import ZLMAdapter
from src.services.zone_preview_service import zone_preview_service


class ChannelService:
    def __init__(self):
        self.channels_dir: Path = settings.CHANNELS_DIR
        self.snapshots_dir: Path = settings.SNAPSHOTS_DIR
        self.transcode_logs_dir: Path = settings.TRANSCODE_LOGS_DIR
        self.media_node_service = MediaNodeService()
        self.event_service = EventService()
        self.model_service = ModelService()

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

    def _build_browser_transcode_target(self, node: dict | None, app: str, stream: str) -> tuple[str, str]:
        browser_stream = f"{stream}_browser"
        rtmp_port = node.get("rtmp_port", settings.ZLM_RTMP_PORT) if node else settings.ZLM_RTMP_PORT
        target_host = self._pick_transcode_publish_host(node)
        target = f"rtmp://{target_host}:{rtmp_port}/{app}/{browser_stream}"
        return browser_stream, target

    def _build_zone_preview_target(self, node: dict | None, app: str, stream: str) -> tuple[str, str]:
        zone_stream = f"{stream}_zone"
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

        browser_stream, target = self._build_browser_transcode_target(node, item["app"], item["stream"])
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

    async def restore_channel_proxies(self) -> dict:
        restored = 0
        failed: list[dict] = []
        items = await asyncio.to_thread(list_json_items, self.channels_dir)
        for item in items:
            channel_id = item.get("id")
            if not channel_id:
                continue
            try:
                await self.play(channel_id)
                restored += 1
            except Exception as exc:
                failed.append({
                    "channel_id": channel_id,
                    "error": str(exc),
                })
        return {
            "restored": restored,
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

    async def list_channels(self, device_id: str | None = None, type_filter: str | None = None, key: str | None = None):
        node_cache: dict[str, dict | None] = {}

        async def _resolve_node(node_id: str | None):
            if not node_id:
                return None
            if node_id not in node_cache:
                node_cache[node_id] = await self.media_node_service.get_node(node_id)
            return node_cache[node_id]

        def _list(items: list[dict]):
            return {"items": sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)}

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
                refreshed["is_online"] = is_online
                refreshed.setdefault("ext", {})["last_media_probe"] = inspect_result

                if not is_online and refreshed.get("is_playing"):
                    refreshed["is_playing"] = False
                if refreshed.get("is_online") != item.get("is_online") or refreshed.get("is_playing") != item.get("is_playing"):
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

        return await asyncio.to_thread(_list, refreshed_items)

    async def get_channel(self, channel_id: str):
        path = self._get_path(channel_id)
        if not await asyncio.to_thread(path.exists):
            return None
        return await asyncio.to_thread(load_json, path)

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
            save_json(self._get_path(channel_id), item)
            return channel_id, item

        channel_id, item = await asyncio.to_thread(_create)
        try:
            await self.play(channel_id)
            return await self.get_channel(channel_id)
        except Exception as exc:
            path = self._get_path(channel_id)

            def _mark_error():
                latest = load_json(path)
                latest.setdefault("ext", {})["auto_play_error"] = str(exc)
                latest["updated_at"] = now_iso()
                save_json(path, latest)
                return latest

            return await asyncio.to_thread(_mark_error)

    async def update_channel(self, channel_id: str, request):
        path = self._get_path(channel_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _update():
            item = load_json(path)
            for field in ["device_id", "name", "type", "source_url", "app", "stream", "media_node_id", "record_mode"]:
                value = getattr(request, field, None)
                if value is not None:
                    item[field] = value
            item["updated_at"] = now_iso()
            save_json(path, item)
            return item

        return await asyncio.to_thread(_update)

    async def delete_channel(self, channel_id: str):
        path = self._get_path(channel_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _delete():
            item = load_json(path)
            path.unlink()
            delete_json_document(path, item.get("id"))
            return item

        return await asyncio.to_thread(_delete)

    async def play(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None

        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)

        def _play():
            path = self._get_path(channel_id)
            item = load_json(path)
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
                    "stream": f"{item['stream']}_zone",
                    "target": None,
                    "urls": {},
                    "last_error": None,
                    "started_at": None,
                    "stopped_at": None,
                })
                item["updated_at"] = now_iso()
                save_json(path, item)
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
                save_json(path, item)
                raise ValueError(f"Failed to start stream proxy: {e}")

        return await asyncio.to_thread(_play)

    async def stop(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None

        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)

        def _stop():
            path = self._get_path(channel_id)
            item = load_json(path)
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
            save_json(path, item)
            return {
                "channel_id": channel_id,
                "status": "stopped",
                "zlm_result": zlm_result,
            }

        return await asyncio.to_thread(_stop)

    async def inspect_stream(self, channel_id: str):
        channel = await self.get_channel(channel_id)
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
        path = self._get_path(channel_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _update():
            item = load_json(path)
            item["is_playing"] = is_playing
            item["updated_at"] = now_iso()
            save_json(path, item)
            return item

        return await asyncio.to_thread(_update)

    async def play_urls(self, channel_id: str):
        channel = await self.get_channel(channel_id)
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
        if zone_stream:
            zone_preview_urls = adapter.build_play_urls(
                channel["app"],
                zone_stream,
                channel.get("media_node_id"),
            )
        return {
            "channel_id": channel_id,
            "status": "online" if channel.get("is_online") else "offline",
            "urls": urls,
            "browser_urls": browser_urls,
            "zone_preview_urls": zone_preview_urls,
        }

    async def latest_snapshot(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        return {"channel_id": channel_id, "snapshot_url": channel.get("snapshot_url")}

    async def snapshot(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None

        def _capture():
            cap = cv2.VideoCapture(channel["source_url"])
            if not cap.isOpened():
                raise ValueError("Failed to open channel source for snapshot")

            ok, frame = cap.read()
            cap.release()
            if not ok:
                raise ValueError("Failed to read frame for snapshot")

            filename = f"{channel_id}_{generate_id('snap')}.jpg"
            output_path = self.snapshots_dir / filename
            cv2.imwrite(str(output_path), frame)

            path = self._get_path(channel_id)
            item = load_json(path)
            item["snapshot_url"] = f"/static/snapshots/{filename}"
            item["updated_at"] = now_iso()
            save_json(path, item)
            return item["snapshot_url"]

        snapshot_url = await asyncio.to_thread(_capture)
        return {"channel_id": channel_id, "snapshot_url": snapshot_url, "captured_at": now_iso()}

    async def get_zones(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        return {"items": channel.get("ext", {}).get("zones", [])}

    async def add_zone(self, channel_id: str, request):
        path = self._get_path(channel_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _add():
            item = load_json(path)
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
            save_json(path, item)
            return zone

        return await asyncio.to_thread(_add)

    async def update_zone(self, channel_id: str, zone_id: str, request):
        path = self._get_path(channel_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _update():
            item = load_json(path)
            for zone in item.setdefault("ext", {}).setdefault("zones", []):
                if zone.get("id") == zone_id:
                    for field in ["name", "color", "coordinates"]:
                        value = getattr(request, field, None)
                        if value is not None:
                            zone[field] = value
                    zone["labels"] = []
                    item["updated_at"] = now_iso()
                    save_json(path, item)
                    return zone
            return None

        return await asyncio.to_thread(_update)

    async def delete_zone(self, channel_id: str, zone_id: str):
        path = self._get_path(channel_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _delete():
            item = load_json(path)
            zones = item.setdefault("ext", {}).setdefault("zones", [])
            remaining = [zone for zone in zones if zone.get("id") != zone_id]
            if len(remaining) == len(zones):
                return None
            item["ext"]["zones"] = remaining
            item["updated_at"] = now_iso()
            save_json(path, item)
            return {"ok": True}

        return await asyncio.to_thread(_delete)

    async def get_ai_config(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        config = channel.get("ext", {}).get("ai_config", {})
        self._sync_legacy_ai_config(config)
        return config

    async def update_ai_config(self, channel_id: str, request):
        path = self._get_path(channel_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _update():
            item = load_json(path)
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
            save_json(path, item)
            return config

        return await asyncio.to_thread(_update)

    async def start_ai(self, channel_id: str):
        config = await self.get_ai_config(channel_id)
        if config is None:
            return None
        request = type("Req", (), {**config, "enabled": True})()
        await self.update_ai_config(channel_id, request)
        return await analysis_service.start_channel_analysis(channel_id)

    async def stop_ai(self, channel_id: str):
        config = await self.get_ai_config(channel_id)
        if config is None:
            return None
        request = type("Req", (), {**config, "enabled": False})()
        await self.update_ai_config(channel_id, request)
        return await analysis_service.stop_channel_analysis(channel_id)

    async def get_ai_status(self, channel_id: str):
        return await analysis_service.get_channel_analysis_status(channel_id)

    async def start_zone_preview(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        if not (channel.get("ext", {}).get("zones") or []):
            raise ValueError("当前通道还没有配置区域")

        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)
        source_url = self._build_zone_preview_source(node, channel["app"], channel["stream"])
        zone_stream, target = self._build_zone_preview_target(node, channel["app"], channel["stream"])
        urls = adapter.build_play_urls(channel["app"], zone_stream, channel.get("media_node_id"))
        return await zone_preview_service.start(channel_id, source_url, zone_stream, target, urls)

    async def stop_zone_preview(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        return await zone_preview_service.stop(channel_id)

    async def get_zone_preview_status(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        return zone_preview_service.status(channel_id)

    async def get_zone_preview_play_urls(self, channel_id: str):
        channel = await self.get_channel(channel_id)
        if not channel:
            return None
        node = await self.media_node_service.get_node(channel.get("media_node_id")) if channel.get("media_node_id") else None
        adapter = self._build_adapter(node)
        zone_preview = channel.get("ext", {}).get("zone_preview", {}) or {}
        zone_stream = zone_preview.get("stream") or f"{channel['stream']}_zone"
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
        snapshot_path = None
        if snapshot_url and snapshot_url.startswith("/static/"):
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
        results = model(image_path, verbose=False, conf=conf_threshold)
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
