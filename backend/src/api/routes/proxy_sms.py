import asyncio
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest
from fastapi.responses import Response, StreamingResponse

from src.core.settings import settings
from src.services.media_node_service import MediaNodeService

router = APIRouter(prefix="/proxy/sms", tags=["proxy-sms"])
media_node_service = MediaNodeService()


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    ignored = {"host", "connection", "content-length", "transfer-encoding", "accept-encoding"}
    return {k: v for k, v in headers.items() if k.lower() not in ignored}


async def _resolve_upstream_base(node_id: str | None) -> str:
    if node_id:
        node = await media_node_service.get_node(node_id)
        if not node:
            raise HTTPException(404, "Media node not found")
        return (node.get("api_base") or node.get("play_base") or settings.ZLM_API_BASE).rstrip("/")
    return settings.ZLM_API_BASE.rstrip("/")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
async def proxy_sms(path: str, request: FastAPIRequest):
    node_id = request.query_params.get("node_id")
    query_items = [(k, v) for k, v in request.query_params.multi_items() if k != "node_id"]
    base = await _resolve_upstream_base(node_id)
    target = f"{base}/{path.lstrip('/')}"
    if query_items:
        target = f"{target}?{urlencode(query_items, doseq=True)}"

    body = await request.body()
    upstream_request = Request(
        target,
        data=body if request.method in {"POST", "PUT", "DELETE"} else None,
        headers=_filter_headers(dict(request.headers)),
        method=request.method,
    )

    try:
        upstream = await asyncio.to_thread(urlopen, upstream_request, timeout=20)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(exc.code, detail or exc.reason)
    except URLError as exc:
        raise HTTPException(502, f"Proxy to media server failed: {exc.reason}")

    media_type = upstream.headers.get_content_type()
    response_headers = {}
    for key in ["Cache-Control", "Content-Length", "Content-Range", "Accept-Ranges"]:
        value = upstream.headers.get(key)
        if value:
            response_headers[key] = value

    if path.endswith(".m3u8") or path.endswith(".json") or path.endswith(".js") or path.endswith(".html"):
        try:
            data = await asyncio.to_thread(upstream.read)
        finally:
            await asyncio.to_thread(upstream.close)
        return Response(content=data, status_code=upstream.status, media_type=media_type, headers=response_headers)

    async def _iter():
        try:
            while True:
                chunk = await asyncio.to_thread(upstream.read, 64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(upstream.close)

    return StreamingResponse(_iter(), status_code=upstream.status, media_type=media_type, headers=response_headers)
