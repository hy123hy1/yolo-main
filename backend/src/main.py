import asyncio
import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.core.settings import settings
from src.api.routes import datasets, annotations, train, logs, models, infer
from src.api.routes import media_nodes, devices, channels, events, recordings, proxy_sms, tasks, server_monitor
from src.services.analysis_service import analysis_service
from src.services.channel_service import ChannelService
from src.services.metadata_db_sync import initialize_metadata_database
from src.services.task_service import TaskService
from src.services.zone_preview_service import zone_preview_service

app = FastAPI(title="YOLO Training Platform API")
channel_service = ChannelService()
task_service = TaskService()
logger = logging.getLogger("uvicorn.error")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（用于访问图片等）
app.mount("/static", StaticFiles(directory=str(settings.DATA_DIR)), name="static")

# 路由
app.include_router(datasets.router)
app.include_router(annotations.router)
if settings.ENABLE_TRAINING:
    app.include_router(train.router)
app.include_router(logs.router)
app.include_router(models.router)
app.include_router(infer.router)
app.include_router(media_nodes.router)
app.include_router(devices.router)
app.include_router(channels.router)
app.include_router(tasks.router)
app.include_router(events.router)
app.include_router(recordings.router)
app.include_router(proxy_sms.router)
app.include_router(server_monitor.router)


async def _restore_runtime_state_after_startup():
    startup_task_restore_delay = max(float(settings.STARTUP_TASK_RESTORE_INITIAL_DELAY_SECONDS or 0), 0.0)

    async def _wait_before_restoring_tasks():
        if startup_task_restore_delay <= 0:
            return
        await asyncio.sleep(startup_task_restore_delay)

    steps = [
        ("initialize_metadata_database", lambda: asyncio.to_thread(initialize_metadata_database, sync_files=False)),
        ("restore_channel_proxies", channel_service.restore_channel_proxies),
        ("startup_task_restore_delay", _wait_before_restoring_tasks),
        ("restore_running_tasks", task_service.restore_running_tasks),
        ("restore_enabled_channels", analysis_service.restore_enabled_channels),
    ]

    for step_name, step_runner in steps:
        started_at = time.perf_counter()
        try:
            await step_runner()
            logger.info(
                "[StartupRestore] step=%s status=ok elapsed_ms=%s",
                step_name,
                round((time.perf_counter() - started_at) * 1000, 2),
            )
        except Exception as exc:
            logger.exception(
                "[StartupRestore] step=%s status=error elapsed_ms=%s error=%s",
                step_name,
                round((time.perf_counter() - started_at) * 1000, 2),
                exc,
            )


@app.on_event("startup")
async def restore_ai_analysis_tasks():
    existing_task = getattr(app.state, "startup_restore_task", None)
    if existing_task and not existing_task.done():
        return
    app.state.startup_restore_task = asyncio.create_task(_restore_runtime_state_after_startup())


@app.on_event("shutdown")
async def shutdown_runtime_workers():
    try:
        result = await analysis_service.stop_all()
        logger.info("[ShutdownCleanup] step=stop_all_analysis status=ok result=%s", result)
    except Exception as exc:
        logger.exception("[ShutdownCleanup] step=stop_all_analysis status=error error=%s", exc)

    try:
        await zone_preview_service.stop_all()
    except Exception as exc:
        logger.exception("[ShutdownCleanup] step=stop_all_zone_previews status=error error=%s", exc)

@app.get("/")
async def root():
    return {"message": "YOLO Training Platform API", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"status": "ok"}
