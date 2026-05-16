from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from src.services.device_service import DeviceService

router = APIRouter(prefix="/devices", tags=["devices"])
device_service = DeviceService()


class DeviceRequest(BaseModel):
    name: str
    type: str
    host: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    remark: Optional[str] = None


class UpdateDeviceRequest(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    host: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    remark: Optional[str] = None
    status: Optional[str] = None


@router.get("")
async def list_devices(
    key: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
):
    return await device_service.list_devices(key=key, type_filter=type)


@router.post("")
async def create_device(request: DeviceRequest):
    return await device_service.create_device(request)


@router.get("/{device_id}")
async def get_device(device_id: str):
    result = await device_service.get_device(device_id)
    if not result:
        raise HTTPException(404, "Device not found")
    return result


@router.put("/{device_id}")
async def update_device(device_id: str, request: UpdateDeviceRequest):
    result = await device_service.update_device(device_id, request)
    if not result:
        raise HTTPException(404, "Device not found")
    return result


@router.delete("/{device_id}")
async def delete_device(device_id: str):
    result = await device_service.delete_device(device_id)
    if not result:
        raise HTTPException(404, "Device not found")
    return result
