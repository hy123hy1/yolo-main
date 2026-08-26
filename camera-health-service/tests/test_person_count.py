from storage.repository import CameraHealthRepository

from datetime import datetime

import cv2
import numpy as np
import pytest

from detectors.person_count import (
    PersonCountDetector,
    encode_person_count,
)
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
                "analysis_case": "0000",
            }
        )
    except ValueError as exc:
        assert "analysis_type" in str(exc) or "analysis_case" in str(exc)
    else:
        raise AssertionError("invalid person count codes must be rejected")


class FakeBoxes:
    def __init__(self, classes, confidences, coordinates):
        self.cls = np.asarray(classes, dtype=np.float32)
        self.conf = np.asarray(confidences, dtype=np.float32)
        self.xyxy = np.asarray(coordinates, dtype=np.float32)


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


def test_person_count_encoding_preserves_four_digits():
    assert encode_person_count(1) == "0001"
    assert encode_person_count(12) == "0012"
    try:
        encode_person_count(0)
    except ValueError as exc:
        assert "greater than 0" in str(exc)
    else:
        raise AssertionError("zero must not be encoded as a positive count")


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
