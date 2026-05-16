from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.core.settings import settings
from src.api.routes import datasets, annotations, train, logs, models, infer
from src.api.routes import media_nodes, devices, channels, events, recordings, proxy_sms
from src.services.analysis_service import analysis_service
from src.services.channel_service import ChannelService
from src.services.metadata_db_sync import initialize_metadata_database

app = FastAPI(title="YOLO Training Platform API")
channel_service = ChannelService()

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
app.include_router(events.router)
app.include_router(recordings.router)
app.include_router(proxy_sms.router)


@app.on_event("startup")
async def restore_ai_analysis_tasks():
    initialize_metadata_database()
    await channel_service.restore_channel_proxies()
    await analysis_service.restore_enabled_channels()

@app.get("/")
async def root():
    return {"message": "YOLO Training Platform API", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"status": "ok"}
