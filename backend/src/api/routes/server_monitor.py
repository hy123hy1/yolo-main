import asyncio

from fastapi import APIRouter

from src.services.server_monitor_service import server_monitor_service


router = APIRouter(prefix="/server", tags=["server-monitor"])


@router.get("/resources")
async def get_server_resources():
    data = await asyncio.to_thread(server_monitor_service.get_server_resources)
    return {
        "msg": "操作成功",
        "code": 200,
        "data": [data],
    }
