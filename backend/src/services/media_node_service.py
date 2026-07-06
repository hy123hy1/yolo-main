import asyncio
from pathlib import Path

from src.core.settings import settings
from src.services.metadata_db_sync import delete_json_document
from src.services.platform_storage import generate_id, list_json_items, load_json, now_iso, save_json
from src.services.zlm_adapter import ZLMAdapter


class MediaNodeService:
    def __init__(self):
        self.media_nodes_dir: Path = settings.MEDIA_NODES_DIR
        self._ensure_default_node()

    def _get_path(self, node_id: str) -> Path:
        return self.media_nodes_dir / f"{node_id}.json"

    def _ensure_default_node(self):
        existing = list_json_items(self.media_nodes_dir)
        if existing:
            default_path = self._get_path("mn_default")
            if default_path.exists():
                item = load_json(default_path)
                changed = self._patch_node_defaults(item)
                if changed:
                    item["updated_at"] = now_iso()
                    save_json(default_path, item)
            return

        node_id = "mn_default"
        item = self._build_node_payload(
            node_id=node_id,
            request=type(
                "DefaultNodeRequest",
                (),
                {
                    "name": "Default ZLM",
                    "type": "zlm",
                    "api_base": settings.ZLM_API_BASE,
                    "play_base": settings.ZLM_PLAY_BASE,
                    "webrtc_base": settings.ZLM_WEBRTC_BASE,
                    "public_rtsp_host": settings.ZLM_PUBLIC_RTSP_HOST,
                    "public_rtmp_host": settings.ZLM_PUBLIC_RTMP_HOST,
                    "public_http_host": settings.ZLM_PUBLIC_HTTP_HOST,
                    "rtsp_port": settings.ZLM_RTSP_PORT,
                    "rtmp_port": settings.ZLM_RTMP_PORT,
                    "http_port": settings.ZLM_HTTP_PORT,
                    "https_port": settings.ZLM_HTTPS_PORT,
                    "hls_file": settings.ZLM_HLS_FILE,
                    "httpflv_suffix": settings.ZLM_HTTPFLV_SUFFIX,
                    "webrtc_path": settings.ZLM_WEBRTC_PATH,
                    "secret": settings.ZLM_SECRET,
                    "is_default": True,
                },
            )(),
        )
        save_json(self._get_path(node_id), item)

    def _clean_url(self, value: str | None) -> str | None:
        if value is None:
            return None
        return value.rstrip("/")

    def _build_node_payload(self, node_id: str, request):
        now = now_iso()
        return {
            "id": node_id,
            "name": request.name,
            "type": request.type,
            "api_base": self._clean_url(request.api_base),
            "play_base": self._clean_url(request.play_base),
            "webrtc_base": self._clean_url(getattr(request, "webrtc_base", None) or request.play_base),
            "transcode_rtmp_host": getattr(request, "transcode_rtmp_host", None),
            "public_rtsp_host": getattr(request, "public_rtsp_host", None) or settings.ZLM_PUBLIC_RTSP_HOST,
            "public_rtmp_host": getattr(request, "public_rtmp_host", None) or settings.ZLM_PUBLIC_RTMP_HOST,
            "public_http_host": getattr(request, "public_http_host", None) or settings.ZLM_PUBLIC_HTTP_HOST,
            "rtsp_port": getattr(request, "rtsp_port", settings.ZLM_RTSP_PORT),
            "rtmp_port": getattr(request, "rtmp_port", settings.ZLM_RTMP_PORT),
            "http_port": getattr(request, "http_port", settings.ZLM_HTTP_PORT),
            "https_port": getattr(request, "https_port", settings.ZLM_HTTPS_PORT),
            "hls_file": getattr(request, "hls_file", settings.ZLM_HLS_FILE),
            "httpflv_suffix": getattr(request, "httpflv_suffix", settings.ZLM_HTTPFLV_SUFFIX),
            "webrtc_path": getattr(request, "webrtc_path", settings.ZLM_WEBRTC_PATH),
            "secret": getattr(request, "secret", "") or "",
            "is_default": getattr(request, "is_default", False),
            "status": "unknown",
            "created_at": now,
            "updated_at": now,
        }

    def _patch_node_defaults(self, item: dict) -> bool:
        changed = False

        legacy_api_values = {"http://localhost:8080", "http://host.docker.internal:8080", "http://localhost:8010"}
        if item.get("api_base") in legacy_api_values and settings.ZLM_API_BASE != item.get("api_base"):
            item["api_base"] = settings.ZLM_API_BASE.rstrip("/")
            changed = True

        legacy_play_values = {
            "http://localhost:8080",
            "http://host.docker.internal:8080",
            "http://localhost:8010",
            "http://host.docker.internal:8010",
        }
        if item.get("play_base") in legacy_play_values and settings.ZLM_PLAY_BASE != item.get("play_base"):
            item["play_base"] = settings.ZLM_PLAY_BASE.rstrip("/")
            changed = True

        legacy_webrtc_values = {
            "webrtc://host.docker.internal:8080",
            "webrtc://host.docker.internal:8010",
            "webrtc://localhost:8080",
            "webrtc://localhost:8010",
        }
        if item.get("webrtc_base") in legacy_webrtc_values and settings.ZLM_WEBRTC_BASE != item.get("webrtc_base"):
            item["webrtc_base"] = settings.ZLM_WEBRTC_BASE.rstrip("/")
            changed = True

        # Keep the bundled default node aligned with the current external play IP/host config.
        if str(item.get("id") or "").strip() == "mn_default":
            forced_defaults = {
                "play_base": settings.ZLM_PLAY_BASE.rstrip("/"),
                "webrtc_base": settings.ZLM_WEBRTC_BASE.rstrip("/"),
                "public_rtsp_host": settings.ZLM_PUBLIC_RTSP_HOST,
                "public_rtmp_host": settings.ZLM_PUBLIC_RTMP_HOST,
                "public_http_host": settings.ZLM_PUBLIC_HTTP_HOST,
                "rtsp_port": settings.ZLM_RTSP_PORT,
                "rtmp_port": settings.ZLM_RTMP_PORT,
                "http_port": settings.ZLM_HTTP_PORT,
                "https_port": settings.ZLM_HTTPS_PORT,
                "hls_file": settings.ZLM_HLS_FILE,
                "httpflv_suffix": settings.ZLM_HTTPFLV_SUFFIX,
                "webrtc_path": settings.ZLM_WEBRTC_PATH,
            }
            for field, default_value in forced_defaults.items():
                if item.get(field) != default_value:
                    item[field] = default_value
                    changed = True

        defaults = {
            "webrtc_base": settings.ZLM_WEBRTC_BASE.rstrip("/"),
            "public_rtsp_host": settings.ZLM_PUBLIC_RTSP_HOST,
            "public_rtmp_host": settings.ZLM_PUBLIC_RTMP_HOST,
            "public_http_host": settings.ZLM_PUBLIC_HTTP_HOST,
            "rtsp_port": settings.ZLM_RTSP_PORT,
            "rtmp_port": settings.ZLM_RTMP_PORT,
            "http_port": settings.ZLM_HTTP_PORT,
            "https_port": settings.ZLM_HTTPS_PORT,
            "hls_file": settings.ZLM_HLS_FILE,
            "httpflv_suffix": settings.ZLM_HTTPFLV_SUFFIX,
            "webrtc_path": settings.ZLM_WEBRTC_PATH,
        }

        if not item.get("webrtc_base") and item.get("play_base"):
            item["webrtc_base"] = item["play_base"].replace("http://", "webrtc://", 1).replace("https://", "webrtcs://", 1)
            changed = True

        for field, default_value in defaults.items():
            if item.get(field) in (None, ""):
                item[field] = default_value
                changed = True

        if item.get("hls_file") == "hls.m3u8":
            item["hls_file"] = settings.ZLM_HLS_FILE
            changed = True

        return changed

    async def list_nodes(self):
        def _list():
            items = list_json_items(self.media_nodes_dir)
            return {"items": sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)}

        return await asyncio.to_thread(_list)

    async def get_node(self, node_id: str):
        path = self._get_path(node_id)
        if not await asyncio.to_thread(path.exists):
            return None
        return await asyncio.to_thread(load_json, path)

    async def create_node(self, request):
        def _create():
            node_id = generate_id("mn")
            item = self._build_node_payload(node_id=node_id, request=request)
            save_json(self._get_path(node_id), item)
            return item

        return await asyncio.to_thread(_create)

    async def update_node(self, node_id: str, request):
        path = self._get_path(node_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _update():
            item = load_json(path)
            url_fields = {"api_base", "play_base", "webrtc_base"}
            for field in [
                "name",
                "type",
                "api_base",
                "play_base",
                "webrtc_base",
                "transcode_rtmp_host",
                "public_rtsp_host",
                "public_rtmp_host",
                "public_http_host",
                "rtsp_port",
                "rtmp_port",
                "http_port",
                "https_port",
                "hls_file",
                "httpflv_suffix",
                "webrtc_path",
                "secret",
                "is_default",
                "status",
            ]:
                value = getattr(request, field, None)
                if value is not None:
                    item[field] = value.rstrip("/") if field in url_fields and isinstance(value, str) else value
            self._patch_node_defaults(item)
            item["updated_at"] = now_iso()
            save_json(path, item)
            return item

        return await asyncio.to_thread(_update)

    async def delete_node(self, node_id: str):
        path = self._get_path(node_id)
        if not await asyncio.to_thread(path.exists):
            return None

        def _delete():
            item = load_json(path)
            path.unlink()
            delete_json_document(path, item.get("id"))
            return item

        return await asyncio.to_thread(_delete)

    async def health(self, node_id: str):
        item = await self.get_node(node_id)
        if not item:
            return None

        return {
            "id": item["id"],
            "name": item["name"],
            "status": "online" if item.get("api_base") else "offline",
            "checked_at": now_iso(),
            "api_base": item.get("api_base"),
            "play_base": item.get("play_base"),
            "webrtc_base": item.get("webrtc_base"),
            "transcode_rtmp_host": item.get("transcode_rtmp_host"),
        }

    async def server_config(self, node_id: str):
        item = await self.get_node(node_id)
        if not item:
            return None

        adapter = ZLMAdapter(
            play_base=item.get("play_base") or settings.ZLM_PLAY_BASE,
            api_base=item.get("api_base") or settings.ZLM_API_BASE,
            webrtc_base=item.get("webrtc_base") or settings.ZLM_WEBRTC_BASE,
            secret=item.get("secret", "") or settings.ZLM_SECRET,
            public_rtsp_host=item.get("public_rtsp_host") or settings.ZLM_PUBLIC_RTSP_HOST,
            public_rtmp_host=item.get("public_rtmp_host") or settings.ZLM_PUBLIC_RTMP_HOST,
            public_http_host=item.get("public_http_host") or settings.ZLM_PUBLIC_HTTP_HOST,
            rtsp_port=item.get("rtsp_port", settings.ZLM_RTSP_PORT),
            rtmp_port=item.get("rtmp_port", settings.ZLM_RTMP_PORT),
            http_port=item.get("http_port", settings.ZLM_HTTP_PORT),
            https_port=item.get("https_port", settings.ZLM_HTTPS_PORT),
            hls_file=item.get("hls_file", settings.ZLM_HLS_FILE),
            httpflv_suffix=item.get("httpflv_suffix", settings.ZLM_HTTPFLV_SUFFIX),
            webrtc_path=item.get("webrtc_path", settings.ZLM_WEBRTC_PATH),
        )

        def _get():
            config = adapter.get_server_config()
            data = config.get("data")
            if isinstance(data, list) and data:
                data = data[0]
            return {
                "node": item,
                "zlm": data or config,
                "expected": {
                    "hls_file": item.get("hls_file", settings.ZLM_HLS_FILE),
                    "webrtc_path": item.get("webrtc_path", settings.ZLM_WEBRTC_PATH),
                    "api_base": item.get("api_base") or settings.ZLM_API_BASE,
                    "play_base": item.get("play_base") or settings.ZLM_PLAY_BASE,
                    "webrtc_base": item.get("webrtc_base") or settings.ZLM_WEBRTC_BASE,
                },
            }

        return await asyncio.to_thread(_get)
