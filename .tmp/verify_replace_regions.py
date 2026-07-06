import asyncio
from unittest.mock import AsyncMock, patch
from pathlib import Path
import json
import tempfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.core.settings import settings
from src.db.models import Base
from src.services import metadata_db_sync
from src.services.channel_service import ChannelService
from src.services.wvp_service import WVPService

async def main():
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    tasks_dir = root / 'tasks'; tasks_dir.mkdir(parents=True, exist_ok=True)
    events_dir = root / 'events'; events_dir.mkdir(parents=True, exist_ok=True)
    recordings_dir = root / 'recordings'; recordings_dir.mkdir(parents=True, exist_ok=True)
    channels_dir = root / 'channels'; channels_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = root / 'metadata.db'

    original = {
        'tasks_dir': settings.TASKS_DIR,
        'events_dir': settings.EVENTS_DIR,
        'recordings_dir': settings.RECORDINGS_DIR,
        'channels_dir': settings.CHANNELS_DIR,
        'engine': metadata_db_sync.engine,
        'session_local': metadata_db_sync.SessionLocal,
    }

    from src.services import analysis_service as analysis_service_module
    from src.services import event_service as event_service_module
    from src.services import metric_service as metric_service_module
    from src.services import recording_service as recording_service_module
    from src.services import task_service as task_service_module

    original['task_service_session_local'] = task_service_module.SessionLocal
    original['analysis_service_session_local'] = analysis_service_module.SessionLocal
    original['event_service_session_local'] = event_service_module.SessionLocal
    original['metric_service_session_local'] = metric_service_module.SessionLocal
    original['recording_service_session_local'] = recording_service_module.SessionLocal

    settings.TASKS_DIR = tasks_dir
    settings.EVENTS_DIR = events_dir
    settings.RECORDINGS_DIR = recordings_dir
    settings.CHANNELS_DIR = channels_dir
    engine = create_engine(f"sqlite:///{sqlite_path.as_posix()}", connect_args={'check_same_thread': False}, future=True)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    metadata_db_sync.engine = engine
    metadata_db_sync.SessionLocal = session_local
    Base.metadata.create_all(bind=engine)
    task_service_module.SessionLocal = session_local
    analysis_service_module.SessionLocal = session_local
    event_service_module.SessionLocal = session_local
    metric_service_module.SessionLocal = session_local
    recording_service_module.SessionLocal = session_local
    task_service_module.channel_service = ChannelService()
    task_service_module.wvp_service = WVPService()

    try:
        from src.services.task_service import TaskService
        service = TaskService()
        channel = {
            'id': 'ch_hot_reload', 'name': 'hot-reload', 'type': 'rtsp', 'source_url': 'rtsp://example/live/hot',
            'app': 'live', 'stream': 'hot_reload', 'media_node_id': 'default', 'is_online': True,
            'is_playing': False, 'ai_enabled': False, 'record_mode': 'none', 'snapshot_url': None,
            'created_at': '2026-06-12 10:00:00', 'updated_at': '2026-06-12 10:00:00', 'ext': {}
        }
        (channels_dir / 'ch_hot_reload.json').write_text(json.dumps(channel, ensure_ascii=False, indent=2), encoding='utf-8')
        task = await service.create_task({'name': 'hot-reload-task', 'channel_id': 'ch_hot_reload', 'algorithms': [{'id': 'algo_hot_reload', 'model_id': 'model_person', 'need_overlay': True, 'enabled': True}]})
        task['status'] = 'running'
        task['runtime'] = {**(task.get('runtime') or {}), 'status': 'running', 'is_running': True}
        task = await service._persist_task(task)

        with (
            patch('src.services.task_service.channel_service.play', AsyncMock(return_value={'status': 'online'})) as mocked_play,
            patch('src.services.task_service.channel_service.start_zone_preview', AsyncMock(return_value={'status': 'running'})) as mocked_zone_start,
            patch('src.services.task_service.channel_service.refresh_zone_preview_cached_urls', AsyncMock(return_value={'webrtc_api': 'w', 'hls': 'h'})) as mocked_refresh_zone,
            patch('src.services.task_service.channel_service.set_task_preview_state', AsyncMock(return_value={'status': 'running'})) as mocked_set_state,
        ):
            result = await service.replace_regions(task['id'], 'algo_hot_reload', {'regions': [{'id': 'zone_1', 'name': 'z1', 'coordinates': [[0,0],[1,0],[1,1],[0,1]]}]})
            print('WITH_ZONE_RESULT', result)
            print('WITH_ZONE_CALLS', mocked_play.await_count, mocked_zone_start.await_count, mocked_refresh_zone.await_count, mocked_set_state.await_count)

        task2 = await service.get_task(task['id'])
        task2['status'] = 'running'
        task2['runtime'] = {**(task2.get('runtime') or {}), 'status': 'running', 'is_running': True}
        task2 = await service._persist_task(task2)
        with (
            patch('src.services.task_service.channel_service.play', AsyncMock(return_value={'status': 'online'})) as mocked_play,
            patch('src.services.task_service.channel_service.stop_zone_preview', AsyncMock(return_value={'status': 'stopped'})) as mocked_stop_zone,
            patch('src.services.task_service.channel_service.refresh_channel_browser_cached_urls', AsyncMock(return_value={'webrtc_api': 'cw', 'hls': 'ch'})) as mocked_refresh_channel,
            patch('src.services.task_service.channel_service.set_task_preview_state', AsyncMock(return_value={'status': 'running'})) as mocked_set_state,
        ):
            result = await service.replace_regions(task2['id'], 'algo_hot_reload', {'regions': []})
            print('WITHOUT_ZONE_RESULT', result)
            print('WITHOUT_ZONE_CALLS', mocked_play.await_count, mocked_stop_zone.await_count, mocked_refresh_channel.await_count, mocked_set_state.await_count)
    finally:
        settings.TASKS_DIR = original['tasks_dir']
        settings.EVENTS_DIR = original['events_dir']
        settings.RECORDINGS_DIR = original['recordings_dir']
        settings.CHANNELS_DIR = original['channels_dir']
        metadata_db_sync.engine = original['engine']
        metadata_db_sync.SessionLocal = original['session_local']
        task_service_module.SessionLocal = original['task_service_session_local']
        analysis_service_module.SessionLocal = original['analysis_service_session_local']
        event_service_module.SessionLocal = original['event_service_session_local']
        metric_service_module.SessionLocal = original['metric_service_session_local']
        recording_service_module.SessionLocal = original['recording_service_session_local']
        temp_dir.cleanup()

asyncio.run(main())
