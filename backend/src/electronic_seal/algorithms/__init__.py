"""Pure algorithm implementations used by the electronic-seal worker."""

from .truck_departure import TruckDepartureDetector, TruckDepartureEvent
from .room_empty import RoomEmptyDetector, RoomEmptyEvent, RoomEmptyResult, RoomEmptyUpdate
from .camera_health import (
    ElectronicSealCameraHealthAdapter,
    SUPPORTED_CAMERA_HEALTH_ALGORITHMS,
)

__all__ = [
    "ElectronicSealCameraHealthAdapter",
    "SUPPORTED_CAMERA_HEALTH_ALGORITHMS",
    "RoomEmptyDetector",
    "RoomEmptyEvent",
    "RoomEmptyResult",
    "RoomEmptyUpdate",
    "TruckDepartureDetector",
    "TruckDepartureEvent",
]
