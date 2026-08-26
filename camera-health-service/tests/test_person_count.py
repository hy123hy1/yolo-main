from storage.repository import CameraHealthRepository


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
