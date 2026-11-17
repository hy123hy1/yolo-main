from __future__ import annotations

import numpy as np
import pytest

from detectors.person_count import PersonCountDetector
from person_count.entry_tracker import PersonEntryEvent
from storage.repository import CameraHealthRepository
from stream.frame_buffer import FrameBuffer
from worker.person_count_coordinator import PersonCountCoordinator
from worker.manager import WorkerManager


class FakeDetector:
    def __init__(self, counts):
        self.counts = list(counts)
        self.calls = 0

    def analyze(self, frame, *, track=False):
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


class TimestampEntryDetector:
    def __init__(self):
        self.timestamps = []

    def update(self, detections, timestamp):
        self.timestamps.append(timestamp)
        return [
            PersonEntryEvent(
                track_id="track-1",
                confidence=0.9,
                line_id="entry-line",
                direction="outside_to_inside",
                crossing_timestamp=timestamp,
                bbox=[1, 1, 10, 10],
            )
        ]


class TimestampEntrySnapshotManager(FakeSnapshotManager):
    def save_entry(self, channel, result, event, *, created_at=None):
        return {"snapshot_path": None}


class FakeEntrySnapshotManager(FakeSnapshotManager):
    def save_entry(self, channel, result, event, *, created_at=None):
        self.calls.append((channel["id"], "entry", event.track_id, created_at))
        return {"snapshot_path": f"/snapshots/{channel['id']}_{event.track_id}.jpg"}


class FakeEntryDetector:
    def __init__(self):
        self.calls = 0
        self.tracking_calls = []

    def analyze(self, frame, *, track=False):
        self.tracking_calls.append(track)
        self.calls += 1
        if self.calls == 1:
            detections = [((40, 20, 60, 40), 0.9)]
        else:
            detections = [((40, 40, 60, 60), 0.9)]
        return PersonCountDetector.result_from_detections(frame, detections)


class RegionSequenceDetector:
    def __init__(self, y_values):
        self.y_values = list(y_values)
        self.calls = 0

    def analyze(self, frame, *, track=False):
        y = self.y_values[min(self.calls, len(self.y_values) - 1)]
        self.calls += 1
        return PersonCountDetector.result_from_detections(
            frame,
            [((40, y - 20, 60, y), 0.9)],
        )


class ContextDetector:
    def __init__(self):
        self.contexts = []

    def new_context(self):
        context = ContextDetector()
        self.contexts.append(context)
        return context

    def analyze(self, frame, *, track=False):
        return PersonCountDetector.result_from_detections(frame, [])


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
        record_interval_seconds=10.0,
    )
    coordinator.register(channel, frame_buffer)

    for sample_time in range(100, 105):
        frame_buffer.put(frame, captured_at=sample_time - 1)
        coordinator.process_once(channel["id"], now=sample_time)

    records = repo.list_person_count_records(channel["id"], page=1, page_size=20)
    assert [item["analysis_case"] for item in reversed(records["items"])] == [
        "0001",
        "0002",
        "0000",
        "0001",
    ]
    assert [item["snapshot_path"] for item in reversed(records["items"])] == [
        "/snapshots/0001_1.jpg",
        "/snapshots/0001_2.jpg",
        None,
        "/snapshots/0001_1.jpg",
    ]
    assert len(snapshots.calls) == 3


def test_coordinator_preserves_behavior_detector_for_unchanged_channel_config(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    region = {
        "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    }
    channel = dict(
        _channel(repo, gb_id="gb-reconcile-preserve"),
        person_behavior_enabled=True,
        tailgating_config={"region_config": region},
    )
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=FakeDetector([1]),
        snapshot_manager=FakeSnapshotManager(),
    )
    frame_buffer = FrameBuffer()
    coordinator.register(channel, frame_buffer)
    state = coordinator._channels[channel["id"]]
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    first = coordinator._ensure_tailgating_detector(state, frame)

    coordinator.register(dict(channel), frame_buffer)

    assert coordinator._channels[channel["id"]].tailgating_detector is first


def test_coordinator_uses_one_detector_context_per_channel(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    first_channel = _channel(repo, gb_id="gb-detector-context-1")
    second_channel = _channel(repo, gb_id="gb-detector-context-2")
    template = ContextDetector()
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=template,
        snapshot_manager=FakeSnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
    )
    first_buffer = FrameBuffer()
    second_buffer = FrameBuffer()
    coordinator.register(first_channel, first_buffer)
    coordinator.register(second_channel, second_buffer)
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    first_buffer.put(frame, captured_at=100.0)
    second_buffer.put(frame, captured_at=100.0)

    assert coordinator.process_once(first_channel["id"], now=100.0) is True
    assert coordinator.process_once(second_channel["id"], now=100.0) is True

    first_detector = coordinator._channels[first_channel["id"]].detector
    second_detector = coordinator._channels[second_channel["id"]].detector
    assert first_detector is not second_detector
    assert template.contexts == [first_detector, second_detector]


def test_coordinator_uses_frame_capture_time_for_entry_events(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = dict(
        _channel(repo, gb_id="gb-frame-time"),
        person_count_enabled=False,
        person_entry_enabled=True,
        person_entry_region_config={
            "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        },
    )
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=FakeEntryDetector(),
        snapshot_manager=TimestampEntrySnapshotManager(),
        frame_timeout_seconds=2000.0,
        interval_seconds=10.0,
        entry_interval_seconds=0.0,
    )
    coordinator.register(channel, FrameBuffer())
    state = coordinator._channels[channel["id"]]
    entry_detector = TimestampEntryDetector()
    coordinator._ensure_entry_detector = lambda _state, _frame: entry_detector

    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    state.frame_buffer.put(frame, captured_at=1700000100.0)
    assert coordinator.process_once(channel["id"], now=1000.0) is True

    assert entry_detector.timestamps == [1700000100.0]
    event = repo.list_person_entry_events(channel["id"])["items"][0]
    assert event["crossing_timestamp"].timestamp() == pytest.approx(1700000100.0)


def test_coordinator_uses_per_channel_algorithm_interval(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = dict(
        _channel(repo, gb_id="gb-channel-interval"),
        person_count_interval_seconds=0.5,
    )
    frame_buffer = FrameBuffer()
    detector = FakeDetector([1, 2, 3])
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=detector,
        snapshot_manager=FakeSnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=10.0,
    )
    coordinator.register(channel, frame_buffer)

    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame_buffer.put(frame, captured_at=100.0)
    assert coordinator.process_once(channel["id"], now=100.0) is True
    frame_buffer.put(frame, captured_at=100.4)
    assert coordinator.process_once(channel["id"], now=100.4) is False
    frame_buffer.put(frame, captured_at=100.6)
    assert coordinator.process_once(channel["id"], now=100.6) is True
    assert detector.calls == 2


def test_coordinator_writes_heartbeat_only_after_record_interval(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = _channel(repo, gb_id="gb-heartbeat")
    frame_buffer = FrameBuffer()
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=FakeDetector([2, 2, 2]),
        snapshot_manager=FakeSnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
        record_interval_seconds=60.0,
    )
    coordinator.register(channel, frame_buffer)
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    for captured_at, now in ((100.0, 101.0), (102.0, 103.0), (160.0, 161.0)):
        frame_buffer.put(frame, captured_at=captured_at)
        assert coordinator.process_once(channel["id"], now=now) is True

    records = repo.list_person_count_records(channel["id"], page=1, page_size=20)
    assert records["total"] == 2
    assert [item["analysis_case"] for item in reversed(records["items"])] == [
        "0002",
        "0002",
    ]


def test_coordinator_does_not_write_for_stale_frame_or_detector_error(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = _channel(repo, gb_id="gb-stale")
    frame_buffer = FrameBuffer()

    class ErrorDetector:
        def analyze(self, frame, *, track=False):
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


def test_coordinator_keeps_last_successful_count_after_inference_failure(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = _channel(repo, gb_id="gb-retry")
    frame_buffer = FrameBuffer()

    class FlakyDetector:
        def __init__(self):
            self.calls = 0

        def analyze(self, frame, *, track=False):
            self.calls += 1
            if self.calls == 1:
                return PersonCountDetector.result_from_detections(
                    frame, [((1, 1, 10, 10), 0.9)]
                )
            raise RuntimeError("yolo inference failed")

    detector = FlakyDetector()
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=detector,
        snapshot_manager=FakeSnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)

    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame_buffer.put(frame, captured_at=100.0)
    assert coordinator.process_once(channel["id"], now=101.0) is True
    frame_buffer.put(frame, captured_at=102.0)
    assert coordinator.process_once(channel["id"], now=103.0) is False

    status = coordinator.get_status(channel["id"])
    assert status["current_count"] == 1
    assert status["last_error"] == "yolo inference failed"
    assert status["consecutive_failures"] == 1
    assert status["last_recorded_at"] == 101.0
    assert repo.list_person_count_records(channel["id"])["total"] == 1


def test_coordinator_uses_configured_exit_direction_for_person_count_type(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-count-exit",
            "name": "出井计数通道",
            "camera_code": "12345678901234567890",
            "person_count_enabled": True,
            "person_analysis_direction": "inside_to_outside",
        }
    )
    frame_buffer = FrameBuffer()
    frame_buffer.put(np.zeros((20, 20, 3), dtype=np.uint8), captured_at=100.0)
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=FakeDetector([1]),
        snapshot_manager=FakeSnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)

    assert coordinator.process_once(channel["id"], now=101.0) is True

    record = repo.list_person_count_records(channel["id"])["items"][0]
    assert record["analysis_type"] == "02"


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
    records = repo.list_person_count_records(channel["id"])
    assert records["total"] == 1
    assert records["items"][0]["analysis_case"] == "0000"


def test_coordinator_records_transition_to_zero(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = _channel(repo, gb_id="gb-zero-transition")
    frame_buffer = FrameBuffer()
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=FakeDetector([1, 0]),
        snapshot_manager=FakeSnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
        record_interval_seconds=300.0,
    )
    coordinator.register(channel, frame_buffer)
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    frame_buffer.put(frame, captured_at=100.0)
    assert coordinator.process_once(channel["id"], now=101.0) is True
    frame_buffer.put(frame, captured_at=102.0)
    assert coordinator.process_once(channel["id"], now=103.0) is True

    records = repo.list_person_count_records(channel["id"], page=1, page_size=20)
    assert [item["analysis_case"] for item in reversed(records["items"])] == [
        "0001",
        "0000",
    ]


def test_coordinator_shares_inference_for_person_entry_and_persists_crossing(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-entry",
            "name": "进入通道",
            "person_count_enabled": False,
            "person_entry_enabled": True,
            "person_entry_line_config": {
                "points": [[0.0, 0.5], [1.0, 0.5]],
                "inside_side": "right",
            },
        }
    )
    frame_buffer = FrameBuffer()
    detector = FakeEntryDetector()
    snapshots = FakeEntrySnapshotManager()
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=detector,
        snapshot_manager=snapshots,
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
        entry_interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    frame_buffer.put(frame, captured_at=100.0)
    assert coordinator.process_once(channel["id"], now=101.0) is True
    frame_buffer.put(frame, captured_at=102.0)
    assert coordinator.process_once(channel["id"], now=103.0) is True

    assert detector.calls == 2
    assert detector.tracking_calls == [True, True]
    events = repo.list_person_entry_events(channel["id"])
    assert events["total"] == 1
    assert events["items"][0]["direction"] == "outside_to_inside"
    assert events["items"][0]["snapshot_path"].endswith("track-1.jpg")


def test_coordinator_uses_region_state_machine_and_persists_once(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-entry-region",
            "name": "区域通行",
            "person_entry_enabled": True,
            "person_analysis_direction": "toward_first_edge",
            "person_entry_region_config": {
                "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            },
        }
    )
    frame_buffer = FrameBuffer()
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=RegionSequenceDetector([90, 90, 90, 60, 40, 30, 25, 20]),
        snapshot_manager=FakeEntrySnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
        entry_interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    for index in range(8):
        frame_buffer.put(frame, captured_at=100.0 + index)
        assert coordinator.process_once(channel["id"], now=101.0 + index) is True

    events = repo.list_person_entry_events(channel["id"])
    assert events["total"] == 1
    assert events["items"][0]["line_id"] == "region-line"
    assert events["items"][0]["direction"] == "toward_first_edge"


def test_coordinator_runs_entry_tracking_with_line_only_configuration(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-entry-line-only",
            "name": "仅虚拟线",
            "person_count_enabled": False,
            "person_entry_enabled": True,
            "person_entry_line_config": {
                "points": [[0.0, 0.5], [1.0, 0.5]],
                "inside_side": "right",
            },
        }
    )
    frame_buffer = FrameBuffer()
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=FakeEntryDetector(),
        snapshot_manager=FakeEntrySnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
        entry_interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    frame_buffer.put(frame, captured_at=100.0)
    assert coordinator.process_once(channel["id"], now=101.0) is True
    frame_buffer.put(frame, captured_at=102.0)
    assert coordinator.process_once(channel["id"], now=103.0) is True

    assert repo.list_person_entry_events(channel["id"])["total"] == 1


def test_coordinator_keeps_entry_event_when_snapshot_fails(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-entry-no-snapshot",
            "name": "进入无图",
            "person_entry_enabled": True,
            "person_entry_line_config": {
                "points": [[0.0, 0.5], [1.0, 0.5]],
                "inside_side": "right",
            },
        }
    )
    frame_buffer = FrameBuffer()

    class BrokenSnapshotManager(FakeEntrySnapshotManager):
        def save_entry(self, *args, **kwargs):
            raise RuntimeError("snapshot failed")

    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=FakeEntryDetector(),
        snapshot_manager=BrokenSnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
        entry_interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame_buffer.put(frame, captured_at=100.0)
    coordinator.process_once(channel["id"], now=101.0)
    frame_buffer.put(frame, captured_at=102.0)
    coordinator.process_once(channel["id"], now=103.0)

    events = repo.list_person_entry_events(channel["id"])
    assert events["total"] == 1
    assert events["items"][0]["snapshot_path"] is None


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
        self.channels = []
        self.unregistered = []

    def register(self, channel, frame_buffer):
        self.registered.append((channel["id"], frame_buffer))
        self.channels.append(dict(channel))
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

    disabled = dict(
        channel,
        person_analysis_enabled=False,
        person_count_enabled=False,
        person_entry_enabled=False,
    )
    manager.reconcile(disabled)
    assert coordinator.unregistered == [channel["id"]]


def test_worker_manager_registers_entry_only_channels(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-entry-manager",
            "name": "仅进入统计",
            "source_url": "rtsp://camera/live",
            "person_entry_enabled": True,
        }
    )
    coordinator = FakePersonCountCoordinator()
    manager = WorkerManager(
        repository=repo,
        worker_factory=FakeManagedWorker,
        person_count_coordinator=coordinator,
    )

    manager.reconcile(channel)

    assert len(coordinator.registered) == 1
    assert coordinator.registered[0][0] == channel["id"]


def test_worker_manager_normalizes_unified_count_mode(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-unified-count",
            "name": "统一人数",
            "source_url": "rtsp://camera/live",
        }
    )
    channel = dict(
        channel,
        person_analysis_enabled=True,
        person_analysis_mode="count",
        person_count_enabled=False,
        person_entry_enabled=False,
    )
    coordinator = FakePersonCountCoordinator()
    manager = WorkerManager(
        repository=repo,
        worker_factory=FakeManagedWorker,
        person_count_coordinator=coordinator,
    )

    manager.reconcile(channel)

    registered_channel = coordinator.registered[0][0]
    assert registered_channel == channel["id"]
    normalized = coordinator.channels[0]
    assert normalized["person_count_enabled"] is True
    assert normalized["person_entry_enabled"] is False
    assert normalized["person_analysis_mode"] == "count"


def test_worker_manager_prefers_enabled_person_count_algorithm_binding(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-algorithm-binding",
            "name": "算法绑定",
            "source_url": "rtsp://camera/live",
            "person_analysis_enabled": False,
        }
    )
    repo.upsert_channel_algorithm_config(
        channel["id"],
        "person_count",
        enabled=True,
        analysis_interval=0.5,
        config={"direction": "inside_to_outside"},
    )
    coordinator = FakePersonCountCoordinator()
    manager = WorkerManager(
        repository=repo,
        worker_factory=FakeManagedWorker,
        person_count_coordinator=coordinator,
    )

    manager.reconcile(channel)

    normalized = coordinator.channels[0]
    assert normalized["person_count_enabled"] is True
    assert normalized["person_count_interval_seconds"] == 0.5
    assert normalized["person_analysis_direction"] == "inside_to_outside"


def test_worker_manager_disabled_person_count_binding_unregisters(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-disabled-binding",
            "name": "禁用算法",
            "source_url": "rtsp://camera/live",
            "person_analysis_enabled": True,
            "person_count_enabled": True,
        }
    )
    repo.upsert_channel_algorithm_config(
        channel["id"],
        "person_count",
        enabled=False,
        analysis_interval=None,
        config={},
    )
    coordinator = FakePersonCountCoordinator()
    manager = WorkerManager(
        repository=repo,
        worker_factory=FakeManagedWorker,
        person_count_coordinator=coordinator,
    )

    manager.reconcile(channel)

    assert coordinator.registered == []
    assert coordinator.unregistered == [channel["id"]]


def test_worker_manager_normalizes_unified_entry_mode(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-unified-entry",
            "name": "统一进入",
            "source_url": "rtsp://camera/live",
        }
    )
    channel = dict(
        channel,
        person_analysis_enabled=True,
        person_analysis_mode="entry",
        person_analysis_line_config={
            "points": [[0.0, 0.5], [1.0, 0.5]],
            "inside_side": "right",
        },
        person_analysis_region_config={
            "points": [[0.0, 0.5], [1.0, 0.5], [1.0, 1.0], [0.0, 1.0]],
        },
        person_count_enabled=False,
        person_entry_enabled=False,
    )
    coordinator = FakePersonCountCoordinator()
    manager = WorkerManager(
        repository=repo,
        worker_factory=FakeManagedWorker,
        person_count_coordinator=coordinator,
    )

    manager.reconcile(channel)

    normalized = coordinator.channels[0]
    assert normalized["person_count_enabled"] is False
    assert normalized["person_entry_enabled"] is True
    assert normalized["person_analysis_mode"] == "entry"


def test_coordinator_disables_entry_branch_in_count_mode(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {
            "gb_id": "gb-unified-count-coordinator",
            "name": "统一人数协调器",
        }
    )
    channel = repo.update_person_analysis_config(
        channel["id"],
        enabled=True,
        mode="count",
        line_config={},
        region_config={},
    )
    frame_buffer = FrameBuffer()
    detector = FakeEntryDetector()
    coordinator = PersonCountCoordinator(
        repository=repo,
        detector=detector,
        snapshot_manager=FakeSnapshotManager(),
        frame_timeout_seconds=5.0,
        interval_seconds=0.0,
        entry_interval_seconds=0.0,
    )
    coordinator.register(channel, frame_buffer)
    frame_buffer.put(np.zeros((100, 100, 3), dtype=np.uint8), captured_at=100.0)

    assert coordinator.process_once(channel["id"], now=101.0) is True
    assert detector.tracking_calls == [False]
