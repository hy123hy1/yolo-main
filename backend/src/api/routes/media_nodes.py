from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from src.services.media_node_service import MediaNodeService

router = APIRouter(prefix="/media-nodes", tags=["media-nodes"])
media_node_service = MediaNodeService()


class MediaNodeRequest(BaseModel):
    name: str
    type: str = "zlm"
    api_base: str
    play_base: str
    webrtc_base: Optional[str] = None
    transcode_rtmp_host: Optional[str] = None
    public_rtsp_host: Optional[str] = None
    public_rtmp_host: Optional[str] = None
    public_http_host: Optional[str] = None
    rtsp_port: int = 554
    rtmp_port: int = 1935
    http_port: int = 8010
    https_port: int = 443
    hls_file: str = "hls.fmp4.m3u8"
    httpflv_suffix: str = ".live.flv"
    webrtc_path: str = "/index/api/webrtc"
    secret: Optional[str] = ""
    is_default: bool = False


class UpdateMediaNodeRequest(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    api_base: Optional[str] = None
    play_base: Optional[str] = None
    webrtc_base: Optional[str] = None
    transcode_rtmp_host: Optional[str] = None
    public_rtsp_host: Optional[str] = None
    public_rtmp_host: Optional[str] = None
    public_http_host: Optional[str] = None
    rtsp_port: Optional[int] = None
    rtmp_port: Optional[int] = None
    http_port: Optional[int] = None
    https_port: Optional[int] = None
    hls_file: Optional[str] = None
    httpflv_suffix: Optional[str] = None
    webrtc_path: Optional[str] = None
    secret: Optional[str] = None
    is_default: Optional[bool] = None
    status: Optional[str] = None


@router.get("")
async def list_media_nodes():
    return await media_node_service.list_nodes()


@router.post("")
async def create_media_node(request: MediaNodeRequest):
    return await media_node_service.create_node(request)


@router.get("/{node_id}")
async def get_media_node(node_id: str):
    result = await media_node_service.get_node(node_id)
    if not result:
        raise HTTPException(404, "Media node not found")
    return result


@router.put("/{node_id}")
async def update_media_node(node_id: str, request: UpdateMediaNodeRequest):
    result = await media_node_service.update_node(node_id, request)
    if not result:
        raise HTTPException(404, "Media node not found")
    return result


@router.delete("/{node_id}")
async def delete_media_node(node_id: str):
    result = await media_node_service.delete_node(node_id)
    if not result:
        raise HTTPException(404, "Media node not found")
    return result


@router.get("/{node_id}/health")
async def media_node_health(node_id: str):
    result = await media_node_service.health(node_id)
    if not result:
        raise HTTPException(404, "Media node not found")
    return result


@router.get("/{node_id}/server-config")
async def media_node_server_config(node_id: str):
    try:
        result = await media_node_service.server_config(node_id)
        if not result:
            raise HTTPException(404, "Media node not found")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
