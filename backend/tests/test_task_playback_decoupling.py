from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, ChannelMeta, TaskMeta
from src.services.task_service import TaskService


class NoElectronicSealOwner:
    def get_channel(self, _channel_id):
        return None

    def get_channel_profile(self, _channel_id):
        return None


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def service():
    return TaskService(electronic_seal_store=NoElectronicSealOwner())


def _task(source_url: str = "rtsp://camera/live/detect", *, with_regions: bool = True):
    algorithm = {
        "id": "algorithm-1",
        "model_id": "model-1",
        "enabled": True,
    }
    if with_regions:
        algorithm["regions"] = [
            {
                "id": "region-1",
                "name": "detect-zone",
                "coordinates": [[0, 0], [100, 0], [100, 100]],
                "enabled": True,
            }
        ]
    return {
        "id": "task-1",
        "name": "detection-only",
        "channel_id": "channel-1",
        "source_url": source_url,
        "source_type": "rtsp",
        "algorithms": [algorithm],
        "runtime": {"status": "stopped", "is_running": False},
    }


def _channel(source_url: str | None = "rtsp://camera/live/detect"):
    return {
        "id": "channel-1",
        "name": "camera-1",
        "type": "wvp",
        "source_url": source_url,
        "source_type": "rtsp" if str(source_url or "").lower().startswith("rtsp://") else "http",
        "app": "rtp",
        "stream": "camera-stream",
        "ext": {
            "upstream": {
                "platform": "wvp",
                "channel_db_id": "8234",
            }
        },
    }


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_task_configuration_reads_stored_channel_without_wvp_refresh(service, operation):
    task = _task()
    with (
        patch.object(service, "_find_any_task_on_channel", AsyncMock(return_value=None)),
        patch.object(service, "_persist_task", AsyncMock(side_effect=lambda value: value)),
        patch.object(service, "_load_task_for_write", AsyncMock(return_value=(task, None))),
        patch("src.services.task_service.channel_service.get_channel", AsyncMock(return_value=_channel())),
        patch("src.services.wvp_service.WVPService.refresh_channel_source") as refresh_wvp,
    ):
        if operation == "create":
            result = await service.create_task(task)
        else:
            result = await service.update_task("task-1", {"name": "updated"})

    assert result is not None
    refresh_wvp.assert_not_called()


@pytest.mark.anyio
async def test_start_task_uses_stored_rtsp_without_playback_side_effects(service):
    task = _task()
    with (
        patch.object(service, "get_task", AsyncMock(return_value=task)),
        patch.object(service, "_find_running_task_on_channel", AsyncMock(return_value=None)),
        patch.object(service, "_persist_task", AsyncMock(side_effect=lambda value: value)),
        patch.object(service, "_has_runnable_algorithm", return_value=True),
        patch("src.services.task_service.channel_service.get_channel", AsyncMock(return_value=_channel())),
        patch("src.services.wvp_service.WVPService.refresh_channel_source") as refresh_wvp,
        patch.object(service, "_warmup_task_preview", AsyncMock()) as warmup,
        patch.object(service, "_schedule_task_preview_warmup") as schedule_warmup,
        patch("src.services.task_service.channel_service.play", AsyncMock()) as play,
        patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock(return_value={"task_id": "task-1", "status": "starting"})) as start_analysis,
    ):
        result = await service.start_task("task-1")

    assert result == {"task_id": "task-1", "status": "starting"}
    start_analysis.assert_awaited_once_with("task-1")
    refresh_wvp.assert_not_called()
    warmup.assert_not_awaited()
    schedule_warmup.assert_not_called()
    play.assert_not_awaited()


@pytest.mark.anyio
async def test_restart_task_uses_stored_rtsp_without_playback_side_effects(service):
    task = _task()
    with (
        patch.object(service, "get_task", AsyncMock(return_value=task)),
        patch.object(service, "_persist_task", AsyncMock(side_effect=lambda value: value)),
        patch.object(service, "_has_runnable_algorithm", return_value=True),
        patch("src.services.task_service.channel_service.get_channel", AsyncMock(return_value=_channel())),
        patch("src.services.wvp_service.WVPService.refresh_channel_source") as refresh_wvp,
        patch.object(service, "_warmup_task_preview", AsyncMock()) as warmup,
        patch.object(service, "_schedule_task_preview_warmup") as schedule_warmup,
        patch("src.services.task_service.channel_service.play", AsyncMock()) as play,
        patch("src.services.task_service.analysis_service.restart_task_analysis", AsyncMock(return_value={"task_id": "task-1", "status": "starting"})) as restart_analysis,
    ):
        result = await service.restart_task("task-1")

    assert result == {"task_id": "task-1", "status": "starting"}
    restart_analysis.assert_awaited_once_with("task-1")
    refresh_wvp.assert_not_called()
    warmup.assert_not_awaited()
    schedule_warmup.assert_not_called()
    play.assert_not_awaited()


@pytest.mark.anyio
async def test_start_task_replaces_stale_task_source_with_stored_channel_rtsp(service):
    task = _task("rtsp://stale/task/source")
    stored_channel = _channel("rtsp://camera/live/current")
    persisted_payloads = []

    async def persist(value):
        persisted_payloads.append(value)
        return value

    with (
        patch.object(service, "get_task", AsyncMock(return_value=task)),
        patch.object(service, "_find_running_task_on_channel", AsyncMock(return_value=None)),
        patch.object(service, "_persist_task", AsyncMock(side_effect=persist)),
        patch.object(service, "_has_runnable_algorithm", return_value=True),
        patch("src.services.task_service.channel_service.get_channel", AsyncMock(return_value=stored_channel)),
        patch("src.services.wvp_service.WVPService.refresh_channel_source") as refresh_wvp,
        patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock(return_value={"status": "starting"})),
    ):
        await service.start_task("task-1")

    assert persisted_payloads[-1]["source_url"] == "rtsp://camera/live/current"
    assert persisted_payloads[-1]["source_type"] == "rtsp"
    refresh_wvp.assert_not_called()


@pytest.mark.anyio
async def test_task_source_type_follows_stored_rtsp_url_when_channel_metadata_is_stale(service):
    stored_channel = _channel("rtsp://camera/live/current")
    stored_channel["source_type"] = "http"
    with patch(
        "src.services.task_service.channel_service.get_channel",
        AsyncMock(return_value=stored_channel),
    ):
        hydrated = await service._hydrate_task_source_from_channel(_task())

    assert hydrated["source_url"] == "rtsp://camera/live/current"
    assert hydrated["source_type"] == "rtsp"


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["start", "restart"])
@pytest.mark.parametrize("source_url", [None, "", "http://camera/live", "rtmp://camera/live"])
async def test_task_lifecycle_rejects_non_rtsp_channel_before_worker(service, operation, source_url):
    task = _task("rtsp://stale/task/source")
    worker_method = "start_task_analysis" if operation == "start" else "restart_task_analysis"
    service_method = service.start_task if operation == "start" else service.restart_task

    with (
        patch.object(service, "get_task", AsyncMock(return_value=task)),
        patch.object(service, "_find_running_task_on_channel", AsyncMock(return_value=None)),
        patch.object(service, "_persist_task", AsyncMock(side_effect=lambda value: value)),
        patch.object(service, "_has_runnable_algorithm", return_value=True),
        patch("src.services.task_service.channel_service.get_channel", AsyncMock(return_value=_channel(source_url))),
        patch(f"src.services.task_service.analysis_service.{worker_method}", AsyncMock()) as worker,
    ):
        with pytest.raises(ValueError, match="RTSP"):
            await service_method("task-1")

    worker.assert_not_awaited()


@pytest.mark.anyio
async def test_stop_task_stops_analysis_only(service):
    task = _task()
    with (
        patch.object(service, "get_task", AsyncMock(return_value=task)),
        patch("src.services.task_service.analysis_service.stop_task_analysis", AsyncMock(return_value={"task_id": "task-1", "status": "stopping"})) as stop_analysis,
        patch("src.services.task_service.channel_service.stop_zone_preview", AsyncMock()) as stop_zone,
        patch("src.services.task_service.channel_service.clear_task_preview_state", AsyncMock()) as clear_preview,
        patch("src.services.task_service.channel_service.stop", AsyncMock()) as stop_channel,
    ):
        result = await service.stop_task("task-1")

    assert result == {"task_id": "task-1", "status": "stopping"}
    stop_analysis.assert_awaited_once_with("task-1")
    stop_zone.assert_not_awaited()
    clear_preview.assert_not_awaited()
    stop_channel.assert_not_awaited()


@pytest.mark.anyio
async def test_running_region_update_persists_detection_config_without_preview(service):
    task = _task(with_regions=False)
    task["runtime"] = {"status": "running", "is_running": True}
    persisted = {**task, "runtime": dict(task["runtime"])}
    with (
        patch.object(service, "get_task", AsyncMock(return_value=task)),
        patch.object(service, "_persist_task", AsyncMock(return_value=persisted)),
        patch.object(service, "_schedule_task_preview_warmup") as schedule_warmup,
    ):
        result = await service.replace_regions(
            "task-1",
            "algorithm-1",
            {
                "regions": [
                    {
                        "id": "region-new",
                        "name": "new-zone",
                        "coordinates": [[0, 0], [50, 0], [50, 50]],
                    }
                ]
            },
        )

    assert len(result["items"]) == 1
    assert result["items"][0]["id"].endswith("region-new")
    assert "playback_ready" not in result
    assert "preview_refresh_scheduled" not in result
    schedule_warmup.assert_not_called()


@pytest.mark.anyio
async def test_restore_running_tasks_does_not_schedule_source_or_preview(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'metadata.db').as_posix()}", future=True)
    sessions = sessionmaker(bind=engine, future=True)
    Base.metadata.create_all(engine)
    now = datetime.now()
    task_payload = _task()
    task_payload["runtime"] = {"status": "running", "is_running": True}
    with sessions() as session:
        session.add(
            ChannelMeta(
                id="channel-1",
                name="camera-1",
                type="wvp",
                source_url="rtsp://camera/live/detect",
                app="rtp",
                stream="camera-stream",
                is_online=True,
                is_playing=False,
                created_at=now,
                updated_at=now,
                raw_json=_channel(),
            )
        )
        session.add(
            TaskMeta(
                id="task-1",
                name="detection-only",
                source_url="rtsp://camera/live/detect",
                source_type="rtsp",
                channel_id="channel-1",
                channel_name="camera-1",
                frame_interval=10,
                status="running",
                is_running=True,
                algorithm_count=1,
                region_count=1,
                created_at=now,
                updated_at=now,
                raw_json=task_payload,
            )
        )
        session.commit()

    monkeypatch.setattr("src.services.task_service.SessionLocal", sessions)
    service = TaskService(electronic_seal_store=NoElectronicSealOwner())
    with (
        patch.object(service, "get_task_from_metadata", AsyncMock(return_value=task_payload)),
        patch.object(service, "_has_runnable_algorithm", return_value=True),
        patch.object(service, "_schedule_task_preview_warmup") as preview_warmup,
        patch("src.services.task_service.channel_service.get_channel", AsyncMock(return_value=_channel())),
        patch("src.services.wvp_service.WVPService.refresh_channel_source") as refresh_wvp,
        patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock(return_value={"status": "starting"})) as start_analysis,
    ):
        result = await service.restore_running_tasks()

    assert result["restored"] == 1
    start_analysis.assert_awaited_once_with("task-1")
    refresh_wvp.assert_not_called()
    preview_warmup.assert_not_called()
    engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("source_url", [None, "http://camera/live", "rtmp://camera/live"])
async def test_restore_running_task_records_invalid_channel_rtsp_without_worker(
    tmp_path,
    monkeypatch,
    source_url,
):
    engine = create_engine(f"sqlite:///{(tmp_path / 'metadata.db').as_posix()}", future=True)
    sessions = sessionmaker(bind=engine, future=True)
    Base.metadata.create_all(engine)
    now = datetime.now()
    task_payload = _task("rtsp://stale/task/source")
    task_payload["runtime"] = {"status": "running", "is_running": True}
    with sessions() as session:
        session.add(
            TaskMeta(
                id="task-1",
                name="detection-only",
                source_url="rtsp://stale/task/source",
                source_type="rtsp",
                channel_id="channel-1",
                channel_name="camera-1",
                frame_interval=10,
                status="running",
                is_running=True,
                algorithm_count=1,
                region_count=1,
                created_at=now,
                updated_at=now,
                raw_json=task_payload,
            )
        )
        session.commit()

    monkeypatch.setattr("src.services.task_service.SessionLocal", sessions)
    service = TaskService(electronic_seal_store=NoElectronicSealOwner())
    with (
        patch.object(service, "get_task_from_metadata", AsyncMock(return_value=task_payload)),
        patch("src.services.task_service.channel_service.get_channel", AsyncMock(return_value=_channel(source_url))),
        patch("src.services.task_service.analysis_service.start_task_analysis", AsyncMock()) as start_analysis,
    ):
        result = await service.restore_running_tasks()

    assert result["restored"] == 0
    assert result["failed"] == [
        {
            "task_id": "task-1",
            "error": "Task bound channel requires a valid RTSP source_url",
        }
    ]
    start_analysis.assert_not_awaited()
    engine.dispose()
