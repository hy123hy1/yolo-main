from pathlib import Path

import pytest

from src.electronic_seal.severity import event_severity, load_event_severities


def test_default_event_severities_cover_all_electronic_seal_algorithms():
    assert load_event_severities() == {
        "truck_departure": "none",
        "room_empty": "yellow",
        "camera_occlusion": "red",
        "camera_view_shift": "red",
    }
    assert event_severity("truck_departure") is None
    assert event_severity("room_empty") == "yellow"


def test_event_severity_rejects_unknown_algorithm():
    with pytest.raises(ValueError, match="unsupported electronic-seal event algorithm"):
        event_severity("unknown")


def test_event_severity_rejects_invalid_configured_level(tmp_path: Path):
    path = tmp_path / "severity.toml"
    path.write_text('[events]\ntruck_departure = "critical"\n', encoding="ascii")

    with pytest.raises(ValueError, match="red, orange, yellow, or none"):
        event_severity("truck_departure", path)
