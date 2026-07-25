from __future__ import annotations

import asyncio

from fastapi import FastAPI

from src.electronic_seal.api import router
from src.electronic_seal.manager import electronic_seal_manager
from src.electronic_seal.platform_client import SupervisionPlatformClient
from src.electronic_seal.store import ensure_electronic_seal_schema
from src.electronic_seal.uploader import ElectronicSealUploader
from src.electronic_seal.upload_worker import ElectronicSealUploadWorker


app = FastAPI(title="Electronic Seal Worker API")
app.include_router(router)


def _build_upload_worker() -> ElectronicSealUploadWorker | None:
    settings = electronic_seal_manager.settings
    if not str(settings.supervision_base_url or "").strip():
        return None
    client = SupervisionPlatformClient(
        base_url=settings.supervision_base_url,
        event_path=settings.supervision_event_path,
        evidence_path=settings.supervision_evidence_path,
        timeout_seconds=settings.supervision_request_timeout_seconds,
    )
    uploader = ElectronicSealUploader(
        store=electronic_seal_manager.store,
        client=client,
        lease_seconds=30,
        max_attempts=settings.upload_max_attempts,
        retry_base_seconds=settings.upload_retry_base_seconds,
        retry_max_seconds=settings.upload_retry_max_seconds,
        deadline_seconds=settings.upload_deadline_seconds,
    )
    return ElectronicSealUploadWorker(
        store=electronic_seal_manager.store,
        uploader=uploader,
        poll_seconds=settings.upload_poll_seconds,
    )


upload_worker = _build_upload_worker()


@app.on_event("startup")
async def startup_electronic_seal_worker():
    await asyncio.to_thread(ensure_electronic_seal_schema)
    await asyncio.to_thread(electronic_seal_manager.restore_enabled_channels)
    if upload_worker is not None:
        upload_worker.start()


@app.on_event("shutdown")
async def shutdown_electronic_seal_worker():
    if upload_worker is not None:
        upload_worker.stop()
    await asyncio.to_thread(electronic_seal_manager.stop_all)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "electronic-seal"}


__all__ = ["app"]
