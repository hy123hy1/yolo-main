from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260812_electronic_seal_event_severity.sql"
)


def test_severity_migration_updates_event_indexes_and_all_detail_tables():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "UPDATE `electronic_seal_event_index`" in sql
    assert "UPDATE `electronic_seal_events`" in sql
    assert "UPDATE `electronic_seal_truck_departure_events`" in sql
    assert "UPDATE `electronic_seal_room_empty_events`" in sql
    assert "UPDATE `electronic_seal_camera_occlusion_events`" in sql
    assert "UPDATE `electronic_seal_camera_view_shift_events`" in sql
    assert "WHEN 'truck_departure' THEN 'yellow'" in sql
    assert "WHEN 'room_empty' THEN 'orange'" in sql
    assert "WHEN 'camera_occlusion' THEN 'red'" in sql
    assert "WHEN 'camera_view_shift' THEN 'red'" in sql
    assert "handling_status" not in sql
    assert "`status`" not in sql
