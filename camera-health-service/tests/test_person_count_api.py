from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from api.routes import create_app
from storage.repository import CameraHealthRepository
from worker.channel_worker import WVPChannelSyncService


class FakeClient:
    def list_channels(self, page=1, count=100, query=None):
        return {"enabled": True, "total": 0, "items": []}


class FakeManager:
    def __init__(self):
        self.reconciled = []
        self.statuses = {}

    def reconcile(self, channel):
        self.reconciled.append(channel)
        return {"channel_id": channel["id"], "running": True}

    def get_person_count_status(self, channel_id):
        return self.statuses.get(str(channel_id))


def _app(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    service = WVPChannelSyncService(repository=repo, client=FakeClient())
    manager = FakeManager()
    app = create_app(
        repository=repo,
        sync_service=service,
        worker_manager=manager,
        person_count_snapshot_dir=tmp_path / "person-count",
    )
    return app, repo, manager


def test_camera_code_and_person_count_config_are_persisted_and_reconciled(tmp_path):
    app, repo, manager = _app(tmp_path)
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-api-1",
            "name": "接口测试",
            "source_url": "rtsp://camera/live",
        }
    )
    client = TestClient(app)

    code_response = client.put(
        f"/channels/{channel['id']}/camera-code",
        json={"camera_code": "12345678901234567890"},
    )
    config_response = client.put(
        f"/channels/{channel['id']}/person-count/config",
        json={"enabled": True},
    )

    assert code_response.status_code == 200
    assert code_response.json()["camera_code"] == "12345678901234567890"
    assert config_response.status_code == 200
    assert config_response.json()["person_count_enabled"] is True
    assert manager.reconciled[-1]["person_count_enabled"] is True


def test_person_count_status_distinguishes_no_result_from_zero(tmp_path):
    app, repo, manager = _app(tmp_path)
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-api-2",
            "name": "状态测试",
            "person_count_enabled": True,
        }
    )
    manager.statuses[channel["id"]] = {
        "channel_id": channel["id"],
        "enabled": True,
        "running": True,
        "current_count": None,
    }
    client = TestClient(app)

    first = client.get(f"/channels/{channel['id']}/person-count/status")
    assert first.status_code == 200
    assert first.json()["current_count"] is None

    manager.statuses[channel["id"]]["current_count"] = 0
    second = client.get(f"/channels/{channel['id']}/person-count/status")
    assert second.json()["current_count"] == 0


def test_person_count_records_return_pagination_and_snapshot_urls(tmp_path):
    app, repo, _ = _app(tmp_path)
    channel = repo.upsert_channel({"gb_id": "gb-api-3", "name": "记录测试"})
    record = repo.create_person_count_record(
        {
            "channel_id": channel["id"],
            "analysis_type": "01",
            "analysis_case": "0001",
            "created_at": datetime(2026, 8, 26, 10, 30, 1),
        }
    )
    client = TestClient(app)

    response = client.get(
        f"/channels/{channel['id']}/person-count/records?page=1&page_size=20"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["analysis_case"] == "0001"
    assert "snapshot_url" not in body["items"][0]
    assert record["id"] == body["items"][0]["id"]


def test_person_count_snapshot_is_served_only_from_configured_root(tmp_path):
    app, repo, _ = _app(tmp_path)
    channel = repo.upsert_channel({"gb_id": "gb-api-4", "name": "图片测试"})
    snapshot_root = tmp_path / "person-count"
    snapshot_root.mkdir()
    image_path = snapshot_root / "image.jpg"
    image_path.write_bytes(b"jpeg")
    record = repo.create_person_count_record(
        {
            "channel_id": channel["id"],
            "analysis_case": "0001",
            "snapshot_path": str(image_path),
        }
    )
    client = TestClient(app)

    response = client.get(f"/person-count/records/{record['id']}/snapshot")

    assert response.status_code == 200
    assert response.content == b"jpeg"

    repo.engine.dispose()


def test_person_count_rejects_invalid_camera_code_and_missing_snapshot(tmp_path):
    app, repo, _ = _app(tmp_path)
    channel = repo.upsert_channel({"gb_id": "gb-api-5", "name": "校验测试"})
    client = TestClient(app)

    invalid = client.put(
        f"/channels/{channel['id']}/camera-code",
        json={"camera_code": "123"},
    )
    assert invalid.status_code == 422

    record = repo.create_person_count_record(
        {
            "channel_id": channel["id"],
            "analysis_case": "0001",
            "snapshot_path": str(tmp_path / "person-count" / "missing.jpg"),
        }
    )
    missing = client.get(f"/person-count/records/{record['id']}/snapshot")
    assert missing.status_code == 404

    outside = repo.create_person_count_record(
        {
            "channel_id": channel["id"],
            "analysis_case": "0001",
            "snapshot_path": str(tmp_path / "outside.jpg"),
        }
    )
    assert (
        client.get(f"/person-count/records/{outside['id']}/snapshot").status_code
        == 500
    )
