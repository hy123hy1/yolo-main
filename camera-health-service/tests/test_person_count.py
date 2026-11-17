from storage.repository import CameraHealthRepository

from datetime import datetime

import cv2
import numpy as np
import pytest

from detectors.person_count import (
    PersonCountDetector,
    encode_person_count,
)
from person_count.entry_tracker import PersonEntryEvent
from person_count.snapshot_manager import PersonCountSnapshotManager


def test_channel_keeps_manual_person_count_fields(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")

    channel = repo.upsert_channel(
        {
            "gb_id": "gb-person-1",
            "name": "人员计数测试",
            "camera_code": "12345678901234567890",
            "person_count_enabled": True,
        }
    )

    assert channel["camera_code"] == "12345678901234567890"
    assert channel["person_count_enabled"] is True

    updated = repo.upsert_channel(
        {
            "gb_id": "gb-person-1",
            "name": "同步后的名称",
            "is_online": True,
        }
    )
    assert updated["camera_code"] == "12345678901234567890"
    assert updated["person_count_enabled"] is True


def test_person_count_record_keeps_analysis_codes_and_snapshot_path(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {"gb_id": "gb-person-2", "name": "人员计数测试"}
    )

    record = repo.create_person_count_record(
        {
            "channel_id": channel["id"],
            "analysis_type": "01",
            "analysis_case": "0001",
            "snapshot_path": None,
        }
    )

    assert record["channel_id"] == channel["id"]
    assert record["analysis_type"] == "01"
    assert record["analysis_case"] == "0001"
    assert record["snapshot_path"] is None
    assert record["created_at"] is not None


def test_person_count_record_accepts_exit_analysis_type(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {"gb_id": "gb-person-exit", "name": "出井计数测试"}
    )

    record = repo.create_person_count_record(
        {
            "channel_id": channel["id"],
            "analysis_type": "02",
            "analysis_case": "0001",
        }
    )

    assert record["analysis_type"] == "02"


def test_person_count_record_ids_are_auto_incrementing_integers(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {"gb_id": "gb-person-auto-id", "name": "人员计数自增ID测试"}
    )

    first = repo.create_person_count_record(
        {"channel_id": channel["id"], "analysis_case": "0001"}
    )
    second = repo.create_person_count_record(
        {"channel_id": channel["id"], "analysis_case": "0002"}
    )

    assert isinstance(first["id"], int)
    assert second["id"] == first["id"] + 1


def test_person_count_repository_rejects_invalid_codes(tmp_path):
    repo = CameraHealthRepository(database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}")
    channel = repo.upsert_channel(
        {"gb_id": "gb-person-3", "name": "人员计数测试"}
    )

    try:
        repo.set_camera_code(channel["id"], "123")
    except ValueError as exc:
        assert "20 digits" in str(exc)
    else:
        raise AssertionError("invalid camera code must be rejected")

    try:
        repo.create_person_count_record(
            {
                "channel_id": channel["id"],
                "analysis_type": "1",
                "analysis_case": "000",
            }
        )
    except ValueError as exc:
        assert "analysis_type" in str(exc) or "analysis_case" in str(exc)
    else:
        raise AssertionError("invalid person count codes must be rejected")


class FakeBoxes:
    def __init__(self, classes, confidences, coordinates, ids=None):
        self.cls = np.asarray(classes, dtype=np.float32)
        self.conf = np.asarray(confidences, dtype=np.float32)
        self.xyxy = np.asarray(coordinates, dtype=np.float32)
        self.id = None if ids is None else np.asarray(ids, dtype=np.float32)


class FakeInferenceResult:
    names = {0: "car", 1: "person"}

    def __init__(self, boxes):
        self.boxes = boxes


class FakeModel:
    names = {0: "car", 1: "person"}

    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, frame, **kwargs):
        self.calls.append((frame, kwargs))
        return self.results

    def track(self, frame, **kwargs):
        self.calls.append((frame, kwargs))
        return self.results


def test_person_count_detector_filters_person_by_name_and_confidence():
    boxes = FakeBoxes(
        classes=[0, 1, 1],
        confidences=[0.99, 0.80, 0.20],
        coordinates=[[1, 2, 10, 20], [11, 12, 30, 40], [50, 60, 70, 80]],
    )
    model = FakeModel([FakeInferenceResult(boxes)])
    detector = PersonCountDetector(
        model_path="unused.pt",
        confidence=0.25,
        model_factory=lambda _: model,
    )

    result = detector.analyze(np.zeros((100, 100, 3), dtype=np.uint8))

    assert result.person_count == 1
    assert result.detections[0].confidence == pytest.approx(0.80)
    assert result.detections[0].box == (11, 12, 30, 40)
    assert model.calls[0][1]["conf"] == 0.25
    assert model.calls[0][1]["save"] is False
    assert model.calls[0][1]["project"]


def test_person_count_detector_uses_bytetrack_ids_when_tracking_requested():
    boxes = FakeBoxes(
        classes=[1],
        confidences=[0.80],
        coordinates=[[11, 12, 30, 40]],
        ids=[17],
    )
    model = FakeModel([FakeInferenceResult(boxes)])
    detector = PersonCountDetector(
        model_path="unused.pt",
        confidence=0.25,
        model_factory=lambda _: model,
    )

    result = detector.analyze(
        np.zeros((100, 100, 3), dtype=np.uint8),
        track=True,
    )

    assert result.detections[0].track_id == "17"
    assert model.calls[0][1]["tracker"] == "bytetrack.yaml"
    assert model.calls[0][1]["persist"] is True


def test_person_count_detector_new_context_does_not_share_model_instance():
    models = []

    def factory(_):
        model = FakeModel([])
        models.append(model)
        return model

    detector = PersonCountDetector(
        model_path="unused.pt",
        model_factory=factory,
    )

    first = detector.new_context()
    second = detector.new_context()
    first.analyze(np.zeros((20, 20, 3), dtype=np.uint8), track=True)
    second.analyze(np.zeros((20, 20, 3), dtype=np.uint8), track=True)

    assert first is not second
    assert first._ensure_model() is not second._ensure_model()
    assert len(models) == 2


def test_person_count_detector_reports_missing_model_path(tmp_path):
    detector = PersonCountDetector(model_path=tmp_path / "missing-yolo.pt")

    with pytest.raises(FileNotFoundError):
        detector.analyze(np.zeros((20, 20, 3), dtype=np.uint8))


def test_person_count_encoding_preserves_four_digits():
    assert encode_person_count(1) == "0001"
    assert encode_person_count(12) == "0012"
    assert encode_person_count(0) == "0000"


def test_person_count_snapshot_uses_camera_code_and_analysis_type(tmp_path):
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    result = PersonCountDetector.result_from_detections(
        frame,
        [((10, 20, 60, 80), 0.91)],
    )
    manager = PersonCountSnapshotManager(snapshot_dir=tmp_path)

    saved = manager.save(
        {
            "id": "0001",
            "camera_code": "12345678901234567890",
        },
        result,
        created_at=datetime(2026, 8, 26, 10, 30, 1),
    )

    assert saved["snapshot_path"].endswith(
        "12345678901234567890_01_20260826103001.jpg"
    )
    image = cv2.imread(saved["snapshot_path"])
    assert image is not None
    assert not np.array_equal(image, frame)


@pytest.mark.parametrize("direction", ["outside_to_inside", "inside_to_outside"])
def test_person_entry_snapshot_uses_same_analysis_filename_for_both_directions(
    tmp_path,
    direction,
):
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    result = PersonCountDetector.result_from_detections(
        frame,
        [((10, 20, 60, 80), 0.91)],
    )
    event = PersonEntryEvent(
        track_id="track-1",
        confidence=0.91,
        line_id="entry-line",
        direction=direction,
        crossing_timestamp=0.0,
        bbox=[10, 20, 60, 80],
    )
    manager = PersonCountSnapshotManager(snapshot_dir=tmp_path)

    saved = manager.save_entry(
        {
            "id": "0001",
            "camera_code": "00000000000000000001",
        },
        result,
        event,
        created_at=datetime(2026, 9, 15, 12, 32, 15),
    )

    assert saved["snapshot_path"].endswith(
        "00000000000000000001_01_20260915123215.jpg"
    )
    assert cv2.imread(saved["snapshot_path"]) is not None


def test_person_count_snapshot_rejects_invalid_camera_code(tmp_path):
    manager = PersonCountSnapshotManager(snapshot_dir=tmp_path)
    result = PersonCountDetector.result_from_detections(
        np.zeros((20, 20, 3), dtype=np.uint8),
        [((1, 1, 10, 10), 0.9)],
    )

    try:
        manager.save({"id": "0001", "camera_code": "camera-1"}, result)
    except ValueError as exc:
        assert "20 digits" in str(exc)
    else:
        raise AssertionError("invalid camera code must be rejected")
