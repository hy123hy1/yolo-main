from __future__ import annotations

from datetime import datetime

import numpy as np

from detectors.person_count import PersonCountDetector, PersonCountResult
from storage.repository import CameraHealthRepository
from stream.frame_buffer import FrameBuffer
from worker.person_count_coordinator import PersonCountCoordinator
from worker.manager import WorkerManager


class FakeDetector:
    def __init__(self, counts):
        self.counts = list(counts)
        self.calls = 0

    def analyze(self, frame):
        count = self.counts[min(self.calls, len(self.counts) - 1)]
        self.calls += 1
        return PersonCountDetector.result_from_detections(
            frame,
            [((1, 1, 10, 10), 0.9)] * count,
        )


class FakeSnapshotManager:
    def __init__(self):
        self.calls = []

    def save(self, channel, result, *, created_at=None):
        self.calls.append((channel["id"], result.person_count, created_at))
        return {
            "channel_id": channel["id"],
            "analysis_type": "01",
            "analysis_case": f"{result.person_count:04d}",
            "snapshot_path": f"/snapshots/{channel['id']}_{result.person_count}.jpg",
        }


def _channel(repo, gb_id="gb-count", enabled=True):
    return repo.upsert_channel(
        {
            "gb_id": gb_id,
            "name": "人员计数通道",
            "camera_code": "12345678901234567890",
            "person_count_enabled": enabled,
        }
    )


def test_coordinator_persists_positive_samples_and_only_changed_snapshots(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = _channel(repo)
    frame_buffer = FrameBuffer()
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    detector = FakeDetector([1, 1, 2, 0, 1])
    snapshots = FakeSnapshotManager()
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=detector,
        snapshot_manager=snapshots,
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)

    for sample_time in range(100, 105):
        frame_buffer.put(frame, captured_at=sample_time - 1)
        coordinator.process_once(channel["id"], now=sample_time)

    records = repo.list_person_count_records(channel["id"], page=1, page_size=20)
    assert [item["analysis_case"] for item in reversed(records["items"])] == [
        "0001",
        "0001",
        "0002",
        "0001",
    ]
    assert [item["snapshot_path"] for item in reversed(records["items"])] == [
        "/snapshots/0001_1.jpg",
        None,
        "/snapshots/0001_2.jpg",
        "/snapshots/0001_1.jpg",
    ]
    assert len(snapshots.calls) == 3


def test_coordinator_does_not_write_for_stale_frame_or_detector_error(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = _channel(repo, gb_id="gb-stale")
    frame_buffer = FrameBuffer()

    class ErrorDetector:
        def analyze(self, frame):
            raise RuntimeError("model failed")

    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=ErrorDetector(),
        snapshot_manager=FakeSnapshotManager(),
        frame_timeout_seconds=0.1,
        interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)

    assert coordinator.process_once(channel["id"], now=100.0) is False
    frame_buffer.put(np.zeros((20, 20, 3), dtype=np.uint8), captured_at=1.0)
    assert coordinator.process_once(channel["id"], now=2.0) is False
    assert repo.list_person_count_records(channel["id"])["total"] == 0
    status = coordinator.get_status(channel["id"])
    assert status["current_count"] is None
    assert status["last_error"]


def test_coordinator_status_distinguishes_zero_from_no_success(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = _channel(repo, gb_id="gb-zero")
    frame_buffer = FrameBuffer()
    frame_buffer.put(np.zeros((20, 20, 3), dtype=np.uint8))
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=FakeDetector([0]),
        snapshot_manager=FakeSnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)

    assert coordinator.process_once(channel["id"], now=time_now(frame_buffer)) is True
    assert coordinator.get_status(channel["id"])["current_count"] == 0
    assert repo.list_person_count_records(channel["id"])["total"] == 0


def time_now(frame_buffer):
    return float(frame_buffer.snapshot()["captured_at"]) + 1


class FakeManagedWorker:
    def __init__(self, channel, **kwargs):
        self.channel = dict(channel)
        self.frame_buffer = FrameBuffer()
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def join(self, timeout=None):
        return None

    def status(self):
        return {"channel_id": self.channel["id"], "running": self.started > self.stopped}


class FakePersonCountCoordinator:
    def __init__(self):
        self.registered = []
        self.unregistered = []

    def register(self, channel, frame_buffer):
        self.registered.append((channel["id"], frame_buffer))
        return {"channel_id": channel["id"]}

    def unregister(self, channel_id):
        self.unregistered.append(str(channel_id))
        return True


def test_worker_manager_reuses_health_reader_for_person_count(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-manager",
            "name": "人员计数通道",
            "source_url": "rtsp://camera/live",
            "person_count_enabled": True,
        }
    )
    coordinator = FakePersonCountCoordinator()
    workers = []

    def factory(channel, **kwargs):
        worker = FakeManagedWorker(channel, **kwargs)
        workers.append(worker)
        return worker

    manager = WorkerManager(
        repository=repo,
        worker_factory=factory,
        person_count_coordinator=coordinator,
    )

    manager.reconcile(channel)
    manager.reconcile(channel)

    assert len(workers) == 1
    assert workers[0].started == 1
    assert len(coordinator.registered) == 2
    assert coordinator.registered[0][1] is workers[0].frame_buffer

    disabled = dict(channel, person_count_enabled=False)
    manager.reconcile(disabled)
    assert coordinator.unregistered == [channel["id"]]
