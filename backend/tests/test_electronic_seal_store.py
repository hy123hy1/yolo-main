import gc
import threading
import weakref
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session, sessionmaker
from sqlalchemy.orm.exc import NoResultFound

from src.db.models import (
    Base,
    ChannelMeta,
    ElectronicSealEventIndexMeta,
    ElectronicSealBaselineMeta,
    ElectronicSealConfigMeta,
    ElectronicSealModelMetaRecord,
    ElectronicSealUploadJobMeta,
    ElectronicSealStateMeta,
    ElectronicSealTruckCountMeta,
    ElectronicSealTruckDepartureEventMeta,
)
import src.electronic_seal.store as store_module
from src.electronic_seal.store import ElectronicSealStore, ensure_electronic_seal_schema


def _store():
    engine = _sqlite_engine()
    Base.metadata.create_all(engine, tables=[ChannelMeta.__table__])
    ensure_electronic_seal_schema(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as session:
        session.add(ChannelMeta(id="seal-channel", name="gate", type="rtsp"))
        session.commit()
    return engine, ElectronicSealStore(session_factory=sessions)


def _file_store(tmp_path):
    engine = _sqlite_engine(
        f"sqlite:///{(tmp_path / 'electronic-seal.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine, tables=[ChannelMeta.__table__])
    ensure_electronic_seal_schema(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as session:
        session.add(ChannelMeta(id="seal-channel", name="gate", type="rtsp"))
        session.commit()
    return engine, ElectronicSealStore(session_factory=sessions)


def _sqlite_engine(url="sqlite://", **kwargs):
    engine = create_engine(url, future=True, **kwargs)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


def test_store_reads_only_electronic_seal_models():
    engine, store = _store()
    try:
        with store.session_factory() as session:
            session.add(
                ElectronicSealModelMetaRecord(
                    id="seal-model-1",
                    name="电子封条",
                    version="v1",
                    classes_json=["person", "truck"],
                )
            )
            session.commit()

        assert store.list_models()[0]["model_id"] == "seal-model-1"
        assert store.get_model("seal-model-1")["name"] == "电子封条"
        assert store.find_model_by_name("电子封条")["model_id"] == "seal-model-1"
        assert store.model_exists("seal-model-1") is True
        assert store.model_exists("missing") is False
    finally:
        engine.dispose()


def test_store_rejects_unknown_electronic_seal_model():
    engine, store = _store()
    try:
        with pytest.raises(ValueError, match="electronic_seal_models"):
            store.upsert_config(
                "seal-channel",
                "room_empty",
                {"model_id": "missing"},
            )
    finally:
        engine.dispose()


def test_truck_departure_is_counted_once_and_queued_for_upload():
    engine, store = _store()
    try:
        first = store.create_event(
            {
                "id": "departure-1",
                "channel_id": "seal-channel",
                "algorithm_type": "truck_departure",
                "event_type": "truck_departure",
                "direction": "inside_to_outside",
                "track_id": "truck-1",
                "label": "truck",
                "confidence": 0.91,
                "line_id": "exit-line",
                "crossing_timestamp": "2026-07-24T10:00:00",
                "enqueue_upload": True,
            }
        )
        duplicate = store.create_event(
            {
                "id": "departure-1",
                "channel_id": "seal-channel",
                "algorithm_type": "truck_departure",
                "direction": "inside_to_outside",
            }
        )
        second = store.create_event(
            {
                "id": "departure-2",
                "channel_id": "seal-channel",
                "algorithm_type": "truck_departure",
                "direction": "inside_to_outside",
                "track_id": "truck-2",
                "label": "truck",
                "crossing_timestamp": "2026-07-24T10:01:00",
                "enqueue_upload": True,
            }
        )

        assert duplicate["id"] == first["id"]
        assert first["upload_job_id"]
        assert first["upload_target"] == "supervision"
        assert second["upload_job_id"]
        assert store.get_truck_count("seal-channel")["out_count"] == 2
        with store.session_factory() as session:
            assert session.query(ElectronicSealEventIndexMeta).count() == 2
            assert session.query(ElectronicSealTruckDepartureEventMeta).count() == 2
            assert session.query(ElectronicSealTruckCountMeta).one().out_count == 2
            jobs = session.query(ElectronicSealUploadJobMeta).all()
            assert len(jobs) == 2
            assert {job.event_id for job in jobs} == {first["id"], second["id"]}
    finally:
        engine.dispose()


def test_truck_departure_rejects_inbound_direction_before_writing():
    engine, store = _store()
    try:
        with pytest.raises(ValueError, match="inside_to_outside"):
            store.create_event(
                {
                    "channel_id": "seal-channel",
                    "algorithm_type": "truck_departure",
                    "direction": "outside_to_inside",
                }
            )
        with store.session_factory() as session:
            assert session.query(ElectronicSealEventIndexMeta).count() == 0
            assert session.query(ElectronicSealTruckCountMeta).count() == 0
    finally:
        engine.dispose()


def _synchronize_initial_unique_reads(engine, table_name):
    barrier = threading.Barrier(2)
    state = {"count": 0}
    state_lock = threading.Lock()

    def wait_for_concurrent_reads(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = statement.lower()
        if "select" not in normalized or f"from {table_name}" not in normalized:
            return
        if "event_id" not in normalized or "target" not in normalized:
            if "channel_id" not in normalized or "algorithm_type" not in normalized:
                return
        with state_lock:
            if state["count"] >= 2:
                return
            state["count"] += 1
        barrier.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", wait_for_concurrent_reads)
    return wait_for_concurrent_reads


def _synchronize_initial_upload_job_reads(engine):
    return _synchronize_initial_unique_reads(engine, "electronic_seal_upload_jobs")


def test_upload_jobs_can_be_loaded_after_restart_and_requeued_explicitly():
    engine, store = _store()
    try:
        event_row = store.create_event(
            {
                "channel_id": "seal-channel",
                "algorithm_type": "truck_departure",
                "event_type": "truck_departure",
            }
        )
        job = store.create_upload_job(event_row["id"], "supervision", {"event_type": "truck_departure"})
        claimed = store.claim_upload_job(job["id"], now=datetime(2026, 7, 22, 10))
        store.finish_upload_job(
            job["id"],
            claim_token=claimed["claim_token"],
            success=False,
            now=datetime(2026, 7, 22, 10),
            error="timeout",
            next_retry_at=datetime(2026, 7, 22, 10, 0, 5),
        )

        pending = store.list_upload_jobs({"retry", "pending", "uploading"})
        assert [item["id"] for item in pending] == [job["id"]]
        assert pending[0]["status"] == "retry"

        requeued = store.requeue_upload_job(job["id"])
        assert requeued["status"] == "pending"
        assert requeued["next_retry_at"] is None
        assert requeued["last_error"] is None
    finally:
        engine.dispose()


def test_config_upsert_applies_defaults_updates_whitelist_and_returns_json_copy():
    engine, store = _store()
    try:
        with store.session_factory() as session:
            session.add(ElectronicSealModelMetaRecord(id="shared-model", name="电子封条"))
            session.commit()
        first = store.upsert_config(
            "seal-channel",
            "truck_departure",
            {
                "enabled": True,
                "model_id": "shared-model",
                "line_config_json": {"line": [1, 2]},
                "region_config_json": {"region": []},
                "threshold_config_json": {"min_confidence": 0.5},
                "ignored": "must not persist",
            },
        )
        assert first["enabled"] is True
        assert first["model_id"] == "shared-model"
        assert first["sample_interval_ms"] == 1000
        assert first["camera_health_interval_seconds"] == 1
        assert first["alarm_hold_seconds"] == 10
        assert first["recovery_hold_seconds"] == 15
        assert first["need_snapshot"] is True
        assert first["need_video_clip"] is False
        assert first["line_config_json"] == {"line": [1, 2]}
        assert "ignored" not in first

        first["line_config_json"]["line"].append(3)
        assert store.get_config("seal-channel", "truck_departure")["line_config_json"] == {"line": [1, 2]}

        second = store.upsert_config(
            "seal-channel",
            "truck_departure",
            {"enabled": False, "sample_interval_ms": 250, "need_video_clip": True},
        )
        assert second["id"] == first["id"]
        assert second["enabled"] is False
        assert second["sample_interval_ms"] == 250
        assert second["need_video_clip"] is True
        assert len(store.list_configs("seal-channel")) == 1
    finally:
        engine.dispose()


def test_concurrent_initial_config_upserts_return_the_same_row_and_persist_final_payload(tmp_path):
    engine, store = _file_store(tmp_path)
    try:
        start = threading.Barrier(2)
        results = [None, None]
        errors = []

        def upsert_config(index):
            try:
                start.wait(timeout=10)
                results[index] = store.upsert_config(
                    "seal-channel",
                    "truck_departure",
                    {
                        "enabled": index == 0,
                        "sample_interval_ms": 100 + index,
                        "line_config_json": {"writer": index},
                    },
                )
            except BaseException as exc:  # pragma: no cover - assertion below reports unexpected thread failures
                errors.append(exc)

        threads = [threading.Thread(target=upsert_config, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert results[0] is not None
        assert results[1] is not None
        assert results[0]["id"] == results[1]["id"]
        assert len(store.list_configs("seal-channel")) == 1
        persisted = store.get_config("seal-channel", "truck_departure")
        assert persisted["enabled"] in {True, False}
        assert persisted["sample_interval_ms"] in {100, 101}
        assert persisted["line_config_json"] in ({"writer": 0}, {"writer": 1})
        assert persisted in results
    finally:
        engine.dispose()


def test_concurrent_initial_state_saves_return_the_same_row_and_persist_final_payload(tmp_path):
    engine, store = _file_store(tmp_path)
    try:
        start = threading.Barrier(2)
        results = [None, None]
        errors = []

        def save_state(index):
            try:
                start.wait(timeout=10)
                results[index] = store.save_state(
                    {
                        "channel_id": "seal-channel",
                        "algorithm_type": "truck_departure",
                        "status": "good",
                        "current_score": 0.8 + index / 10,
                        "runtime_json": {"writer": index},
                    }
                )
            except BaseException as exc:  # pragma: no cover - assertion below reports unexpected thread failures
                errors.append(exc)

        threads = [threading.Thread(target=save_state, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert results[0] is not None
        assert results[1] is not None
        assert results[0]["id"] == results[1]["id"]
        with store.session_factory() as session:
            assert session.query(ElectronicSealStateMeta).count() == 1
        persisted = store.get_state("seal-channel", "truck_departure")
        assert persisted["current_score"] in {0.8, 0.9}
        assert persisted["runtime_json"] in ({"writer": 0}, {"writer": 1})
        assert persisted in results
    finally:
        engine.dispose()


def test_concurrent_baseline_replacements_leave_only_one_active_row(tmp_path):
    engine, store = _file_store(tmp_path)
    try:
        start = threading.Barrier(2)
        results = [None, None]
        errors = []

        def replace_baseline(index):
            try:
                start.wait(timeout=10)
                results[index] = store.replace_baseline(
                    "seal-channel",
                    "camera_occlusion",
                    {
                        "image_path": f"/tmp/baseline-{index}.jpg",
                        "width": 1920,
                        "height": 1080,
                        "calibration_params_json": {"writer": index},
                    },
                )
            except BaseException as exc:  # pragma: no cover - assertion below reports unexpected thread failures
                errors.append(exc)

        threads = [threading.Thread(target=replace_baseline, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert all(result is not None for result in results)
        with store.session_factory() as session:
            active_rows = (
                session.query(ElectronicSealBaselineMeta)
                .filter_by(channel_id="seal-channel", algorithm_type="camera_occlusion", status="active")
                .all()
            )
        assert len(active_rows) == 1
        assert active_rows[0].id in {result["id"] for result in results}
    finally:
        engine.dispose()


def test_config_integrity_error_recheck_applies_current_payload(monkeypatch):
    engine, store = _store()
    original_commit = Session.commit
    triggered = {"value": False}

    def commit_with_competing_config(session):
        new_row = next((row for row in session.new if isinstance(row, ElectronicSealConfigMeta)), None)
        if new_row is not None and not triggered["value"]:
            triggered["value"] = True
            with store.session_factory() as winner_session:
                winner_session.add(
                    ElectronicSealConfigMeta(
                        id="winner-config",
                        channel_id="seal-channel",
                        algorithm_type="truck_departure",
                        enabled=False,
                        sample_interval_ms=1000,
                        camera_health_interval_seconds=3600,
                        alarm_hold_seconds=10,
                        recovery_hold_seconds=15,
                        need_snapshot=True,
                        need_video_clip=False,
                        line_config_json={},
                        region_config_json={},
                        threshold_config_json={},
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                    )
                )
                original_commit(winner_session)
            raise IntegrityError("forced concurrent insert", {}, RuntimeError("forced"))
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", commit_with_competing_config)
    try:
        result = store.upsert_config(
            "seal-channel",
            "truck_departure",
            {"enabled": True, "sample_interval_ms": 250, "line_config_json": {"winner": "caller"}},
        )
        assert result["id"] == "winner-config"
        assert result["enabled"] is True
        assert result["sample_interval_ms"] == 250
        assert result["line_config_json"] == {"winner": "caller"}
        assert store.get_config("seal-channel", "truck_departure")["line_config_json"] == {"winner": "caller"}
    finally:
        engine.dispose()


def test_state_integrity_error_recheck_applies_current_payload(monkeypatch):
    engine, store = _store()
    original_commit = Session.commit
    triggered = {"value": False}

    def commit_with_competing_state(session):
        new_row = next((row for row in session.new if isinstance(row, ElectronicSealStateMeta)), None)
        if new_row is not None and not triggered["value"]:
            triggered["value"] = True
            with store.session_factory() as winner_session:
                winner_session.add(
                    ElectronicSealStateMeta(
                        id="winner-state",
                        channel_id="seal-channel",
                        algorithm_type="truck_departure",
                        status="calibrating",
                        last_metrics_json={},
                        runtime_json={},
                        updated_at=datetime.now(),
                    )
                )
                original_commit(winner_session)
            raise IntegrityError("forced concurrent insert", {}, RuntimeError("forced"))
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", commit_with_competing_state)
    try:
        result = store.save_state(
            {
                "channel_id": "seal-channel",
                "algorithm_type": "truck_departure",
                "status": "good",
                "current_score": 0.95,
                "runtime_json": {"winner": "caller"},
            }
        )
        assert result["id"] == "winner-state"
        assert result["status"] == "good"
        assert result["current_score"] == 0.95
        assert result["runtime_json"] == {"winner": "caller"}
        assert store.get_state("seal-channel", "truck_departure")["runtime_json"] == {"winner": "caller"}
    finally:
        engine.dispose()


@pytest.mark.parametrize("operation", ["config", "state", "baseline", "event", "upload_job"])
def test_channel_writes_reject_missing_channel(operation):
    engine, store = _store()
    try:
        expected_exception = IntegrityError if operation == "event" else NoResultFound
        with pytest.raises(expected_exception):
            if operation == "config":
                store.upsert_config("missing-channel", "truck_departure", {"enabled": True})
            elif operation == "state":
                store.save_state(
                    {
                    "channel_id": "missing-channel",
                    "algorithm_type": "truck_departure",
                    "status": "good",
                }
            )
            elif operation == "baseline":
                store.replace_baseline(
                    "missing-channel",
                    "camera_occlusion",
                    {"image_path": "/tmp/missing.jpg", "width": 1, "height": 1},
                )
            elif operation == "event":
                store.create_event(
                    {
                        "channel_id": "missing-channel",
                        "algorithm_type": "room_empty",
                        "event_type": "room_empty",
                    }
                )
            else:
                store.create_upload_job("missing-event", "platform")
    finally:
        engine.dispose()


def test_baseline_and_state_lifecycle_preserves_json_and_formatted_times():
    engine, store = _store()
    try:
        captured_at = datetime(2026, 7, 21, 10, 0, 0)
        baseline = store.replace_baseline(
            "seal-channel",
            "camera_occlusion",
            {
                "image_path": "/tmp/baseline.jpg",
                "width": 1920,
                "height": 1080,
                "quality_score": 0.95,
                "calibration_params_json": {"mean": [1, 2]},
                "captured_at": captured_at,
                "version": 2,
            },
        )
        assert baseline["captured_at"] == "2026-07-21 10:00:00"
        baseline["calibration_params_json"]["mean"].append(3)
        assert store.get_active_baseline("seal-channel", "camera_occlusion")["calibration_params_json"] == {
            "mean": [1, 2]
        }

        replacement = store.replace_baseline(
            "seal-channel",
            "camera_occlusion",
            {"image_path": "/tmp/new.jpg", "width": 640, "height": 480},
        )
        assert replacement["status"] == "active"
        assert store.get_active_baseline("seal-channel", "camera_occlusion")["id"] == replacement["id"]
        invalidated = store.invalidate_baseline("seal-channel", "camera_occlusion")
        assert invalidated == {
            "channel_id": "seal-channel",
            "algorithm_type": "camera_occlusion",
            "status": "superseded",
        }
        assert store.get_active_baseline("seal-channel", "camera_occlusion") is None

        state = store.save_state(
            {
                "channel_id": "seal-channel",
                "algorithm_type": "camera_occlusion",
                "status": "alarming",
                "current_score": 0.8,
                "last_metrics_json": {"brightness": 1},
                "runtime_json": {"bad_count": 2},
                "last_run_at": captured_at,
                "bad_since": captured_at,
            }
        )
        assert state["last_run_at"] == "2026-07-21 10:00:00"
        assert state["runtime_json"] == {"bad_count": 2}
        state["runtime_json"]["bad_count"] = 9
        assert store.get_state("seal-channel", "camera_occlusion")["runtime_json"] == {"bad_count": 2}
        updated = store.save_state(
            {
                "channel_id": "seal-channel",
                "algorithm_type": "camera_occlusion",
                "status": "good",
                "last_good_at": captured_at,
                "last_event_id": "event-1",
            }
        )
        assert updated["id"] == state["id"]
        assert updated["status"] == "good"
        assert updated["last_event_id"] == "event-1"
        assert updated["runtime_json"] == {"bad_count": 2}
    finally:
        engine.dispose()


def test_event_lifecycle_uses_evidence_paths_and_supports_filters():
    engine, store = _store()
    try:
        started_at = datetime(2026, 7, 21, 10, 0, 0)
        event = store.create_event(
            {
                "channel_id": "seal-channel",
                "algorithm_type": "room_empty",
                "event_type": "room_empty",
                "severity": "critical",
                "started_at": started_at,
                "score": 0.9,
                "metrics_json": {"empty_seconds": 600},
                "evidence_snapshot_path": "/evidence/snapshot.jpg",
                "evidence_video_path": "/evidence/video.mp4",
            }
        )
        assert event["started_at"] == "2026-07-21 10:00:00"
        assert event["evidence_snapshot_path"] == "/evidence/snapshot.jpg"
        assert event["evidence_video_path"] == "/evidence/video.mp4"

        closed = store.close_event(event["id"], started_at + timedelta(seconds=12), 12)
        assert closed["status"] == "closed"
        assert closed["ended_at"] == "2026-07-21 10:00:12"
        assert closed["duration_seconds"] == 12.0
        updated = store.update_event(event["id"], {"handling_status": 1, "status": "handled", "ignored": True})
        assert updated["handling_status"] == 1
        assert updated["status"] == "handled"
        assert "ignored" not in updated
        assert store.get_event(event["id"])["id"] == event["id"]
        result = store.list_events("seal-channel", "room_empty", 1, 10)
        assert result["total"] == 1
        assert result["items"][0]["id"] == event["id"]
    finally:
        engine.dispose()


def test_upload_jobs_are_idempotent_and_claim_increments_attempts():
    engine, store = _store()
    try:
        event = store.create_event(
            {
                "channel_id": "seal-channel",
                "algorithm_type": "truck_departure",
                "event_type": "truck_departure",
            }
        )
        first = store.create_upload_job(
            event["id"],
            "supervision",
            {"remote_path": "/events/1", "snapshot_path": "/snapshots/1.jpg"},
        )
        second = store.create_upload_job(event["id"], "supervision", {"remote_path": "/events/changed"})
        assert first["id"] == second["id"]
        assert first["idempotency_key"] == f"{event['id']}:supervision"
        assert second["payload_json"] == {
            "remote_path": "/events/changed",
            "snapshot_path": "/snapshots/1.jpg",
        }
        assert second["status"] == "pending"
        assert second["attempts"] == 0

        now = datetime(2026, 7, 21, 10, 0, 0)
        claimed = store.claim_upload_job(first["id"], now=now)
        assert claimed["status"] == "uploading"
        assert claimed["attempts"] == 1
        assert claimed["claim_token"]
        assert claimed["claimed_at"] == "2026-07-21 10:00:00"
        assert claimed["lease_expires_at"] == "2026-07-21 10:05:00"
        assert store.claim_upload_job(first["id"], now=now) is None
    finally:
        engine.dispose()


def test_upload_job_payload_is_immutable_while_uploading_or_succeeded():
    engine, store = _store()
    try:
        event = store.create_event(
            {
                "channel_id": "seal-channel",
                "algorithm_type": "truck_departure",
                "event_type": "truck_departure",
            }
        )
        job = store.create_upload_job(
            event["id"],
            "supervision",
            {
                "video_path": "/original.mp4",
                "snapshot_path": "/original.jpg",
                "idempotency_key": "caller-controlled-key",
            },
        )
        assert job["idempotency_key"] == f"{event['id']}:supervision"
        assert job["idempotency_key"] != "caller-controlled-key"
        claimed = store.claim_upload_job(job["id"], now=datetime(2026, 7, 21, 10, 0, 0))

        uploading = store.create_upload_job(
            event["id"],
            "supervision",
            {"video_path": "/replacement.mp4", "remote_id": "replacement", "last_error": "replacement"},
        )
        assert uploading["status"] == "uploading"
        assert uploading["payload_json"] == job["payload_json"]
        assert uploading["remote_id"] is None
        assert uploading["last_error"] is None

        succeeded = store.finish_upload_job(
            job["id"],
            claim_token=claimed["claim_token"],
            success=True,
            remote_id="uploaded",
            now=datetime(2026, 7, 21, 10, 1, 0),
        )
        terminal = store.create_upload_job(
            event["id"],
            "supervision",
            {"video_path": "/second-replacement.mp4", "remote_id": "replacement-again"},
        )
        assert succeeded["status"] == "succeeded"
        assert terminal["status"] == "succeeded"
        assert terminal["payload_json"] == job["payload_json"]
        assert terminal["remote_id"] == "uploaded"
    finally:
        engine.dispose()


def test_channel_locks_are_weakly_held_and_reused_while_live():
    first = store_module._channel_lock("temporary-channel")
    second = store_module._channel_lock("temporary-channel")
    assert first is second
    lock_ref = weakref.ref(first)

    del second
    del first
    gc.collect()

    assert lock_ref() is None
    assert "temporary-channel" not in store_module._CHANNEL_LOCKS


def test_electronic_seal_datetime_formatter_preserves_fractional_seconds():
    exact_second = datetime(2026, 7, 21, 10, 0, 0)
    fractional = datetime(2026, 7, 21, 10, 0, 0, 123456)

    assert store_module._format_electronic_seal_datetime(exact_second) == "2026-07-21 10:00:00"
    assert (
        store_module._format_electronic_seal_datetime(fractional)
        == "2026-07-21 10:00:00.123456"
    )
    assert (
        store_module._format_electronic_seal_datetime("2026-07-21T10:00:00.120000+00:00")
        == "2026-07-21 10:00:00.120000"
    )


def test_claim_upload_job_uses_a_row_lock_before_checking_claim_state(monkeypatch):
    engine, store = _store()
    try:
        event_row = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "truck_departure", "event_type": "truck_departure"}
        )
        job = store.create_upload_job(event_row["id"], "supervision")
        lock_calls = []
        original_with_for_update = Query.with_for_update

        def track_with_for_update(query, *args, **kwargs):
            lock_calls.append(True)
            return original_with_for_update(query, *args, **kwargs)

        monkeypatch.setattr(Query, "with_for_update", track_with_for_update)

        assert store.claim_upload_job(job["id"], now=datetime(2026, 7, 21, 10, 0, 0))["attempts"] == 1
        assert lock_calls == [True]
    finally:
        engine.dispose()


def test_concurrent_create_upload_job_calls_return_the_same_job(tmp_path):
    engine, store = _file_store(tmp_path)
    try:
        event_row = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "truck_departure", "event_type": "truck_departure"}
        )
        start = threading.Barrier(2)
        results = [None, None]
        errors = []

        def create_job(index):
            try:
                start.wait(timeout=10)
                results[index] = store.create_upload_job(event_row["id"], "supervision")
            except BaseException as exc:  # pragma: no cover - assertion below reports unexpected thread failures
                errors.append(exc)

        threads = [threading.Thread(target=create_job, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert results[0] is not None
        assert results[1] is not None
        assert results[0]["id"] == results[1]["id"]
    finally:
        engine.dispose()


def test_concurrent_upload_job_unique_race_merges_payload_into_existing_job(monkeypatch):
    engine, store = _store()
    original_commit = Session.commit
    triggered = {"value": False}

    def commit_with_competing_upload_job(session):
        new_row = next((row for row in session.new if isinstance(row, ElectronicSealUploadJobMeta)), None)
        if new_row is not None and not triggered["value"]:
            triggered["value"] = True
            with store.session_factory() as winner_session:
                winner_session.add(
                    ElectronicSealUploadJobMeta(
                        id="winner-upload",
                        event_id=new_row.event_id,
                        target=new_row.target,
                        status="pending",
                        attempts=0,
                        payload_json={"snapshot_path": "/snapshots/winner.jpg"},
                        idempotency_key=new_row.idempotency_key,
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                    )
                )
                original_commit(winner_session)
            raise IntegrityError("forced concurrent insert", {}, RuntimeError("forced"))
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", commit_with_competing_upload_job)
    try:
        event = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "truck_departure", "event_type": "truck_departure"}
        )
        result = store.create_upload_job(
            event["id"],
            "supervision",
            {"video_path": "/videos/caller.mp4"},
        )
        assert result["id"] == "winner-upload"
        assert result["payload_json"] == {
            "snapshot_path": "/snapshots/winner.jpg",
            "video_path": "/videos/caller.mp4",
        }
    finally:
        engine.dispose()


def test_concurrent_upload_job_unique_race_preserves_terminal_upload(monkeypatch):
    engine, store = _store()
    original_commit = Session.commit
    triggered = {"value": False}
    winner_updated_at = datetime(2026, 7, 21, 10, 0, 0, 123456)

    def commit_with_competing_upload_job(session):
        new_row = next((row for row in session.new if isinstance(row, ElectronicSealUploadJobMeta)), None)
        if new_row is not None and not triggered["value"]:
            triggered["value"] = True
            with store.session_factory() as winner_session:
                winner_session.add(
                    ElectronicSealUploadJobMeta(
                        id="winner-uploading",
                        event_id=new_row.event_id,
                        target=new_row.target,
                        status="uploading",
                        attempts=2,
                        payload_json={"video_path": "/videos/winner.mp4"},
                        claim_token="winner-token",
                        remote_id="winner-remote",
                        last_error="previous error",
                        idempotency_key=new_row.idempotency_key,
                        claimed_at=datetime(2026, 7, 21, 9, 59, 0),
                        lease_expires_at=datetime(2026, 7, 21, 10, 4, 0),
                        created_at=datetime(2026, 7, 21, 9, 58, 0),
                        updated_at=winner_updated_at,
                    )
                )
                original_commit(winner_session)
            raise IntegrityError("forced concurrent insert", {}, RuntimeError("forced"))
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", commit_with_competing_upload_job)
    try:
        event = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "truck_departure", "event_type": "truck_departure"}
        )
        result = store.create_upload_job(
            event["id"],
            "supervision",
            {"video_path": "/videos/caller.mp4", "remote_id": "caller-remote"},
        )
        assert result == {
            "id": "winner-uploading",
            "event_id": event["id"],
            "target": "supervision",
            "status": "uploading",
            "attempts": 2,
            "payload_json": {"video_path": "/videos/winner.mp4"},
            "next_retry_at": None,
            "claimed_at": "2026-07-21 09:59:00",
            "lease_expires_at": "2026-07-21 10:04:00",
            "claim_token": "winner-token",
            "remote_id": "winner-remote",
            "last_error": "previous error",
            "idempotency_key": f"{event['id']}:supervision",
            "created_at": "2026-07-21 09:58:00",
            "updated_at": "2026-07-21 10:00:00.123456",
        }
    finally:
        engine.dispose()


def test_invalidate_baseline_locks_channel_row(monkeypatch):
    engine, store = _store()
    try:
        store.replace_baseline(
            "seal-channel",
            "camera_occlusion",
            {"image_path": "/tmp/baseline.jpg", "width": 1, "height": 1},
        )
        lock_calls = []
        original_with_for_update = Query.with_for_update

        def track_with_for_update(query, *args, **kwargs):
            lock_calls.append(True)
            return original_with_for_update(query, *args, **kwargs)

        monkeypatch.setattr(Query, "with_for_update", track_with_for_update)
        store.invalidate_baseline("seal-channel", "camera_occlusion")
        assert lock_calls == [True]
    finally:
        engine.dispose()


def test_concurrent_baseline_replace_and_invalidate_are_serialized_per_channel(tmp_path):
    engine, store = _file_store(tmp_path)
    try:
        store.replace_baseline(
            "seal-channel",
            "camera_occlusion",
            {"image_path": "/tmp/initial.jpg", "width": 1, "height": 1},
        )
        start = threading.Barrier(2)
        errors = []

        def replace():
            try:
                start.wait(timeout=10)
                store.replace_baseline(
                    "seal-channel",
                    "camera_occlusion",
                    {"image_path": "/tmp/replacement.jpg", "width": 2, "height": 2},
                )
            except BaseException as exc:  # pragma: no cover - assertion below reports unexpected thread failures
                errors.append(exc)

        def invalidate():
            try:
                start.wait(timeout=10)
                store.invalidate_baseline("seal-channel", "camera_occlusion")
            except BaseException as exc:  # pragma: no cover - assertion below reports unexpected thread failures
                errors.append(exc)

        threads = [threading.Thread(target=replace), threading.Thread(target=invalidate)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        with store.session_factory() as session:
            active_rows = (
                session.query(ElectronicSealBaselineMeta)
                .filter_by(channel_id="seal-channel", algorithm_type="camera_occlusion", status="active")
                .all()
            )
        assert len(active_rows) <= 1
    finally:
        engine.dispose()


def test_concurrent_claim_upload_job_calls_only_allow_one_success(tmp_path):
    engine, store = _file_store(tmp_path)
    try:
        event_row = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "truck_departure", "event_type": "truck_departure"}
        )
        job = store.create_upload_job(event_row["id"], "supervision")
        start = threading.Barrier(2)
        results = [None, None]
        errors = []
        now = datetime(2026, 7, 21, 10, 0, 0)

        def claim_job(index):
            try:
                start.wait(timeout=10)
                results[index] = store.claim_upload_job(job["id"], now=now)
            except BaseException as exc:  # pragma: no cover - assertion below reports unexpected thread failures
                errors.append(exc)

        threads = [threading.Thread(target=claim_job, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert sum(result is not None for result in results) == 1
        assert results[0] is None or results[1] is None
    finally:
        engine.dispose()


def test_create_upload_job_reraises_an_unrelated_integrity_error(monkeypatch):
    engine, store = _store()
    try:
        event_row = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "truck_departure", "event_type": "truck_departure"}
        )
        existing = store.create_upload_job(event_row["id"], "supervision")
        monkeypatch.setattr(store_module, "generate_id", lambda _prefix: existing["id"])

        with pytest.raises(IntegrityError):
            store.create_upload_job(event_row["id"], "archive")
    finally:
        engine.dispose()


def test_upload_jobs_retry_then_succeed_or_fail_at_max_attempts():
    engine, store = _store()
    try:
        event = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "room_empty", "event_type": "room_empty"}
        )
        retry_job = store.create_upload_job(event["id"], "platform")
        now = datetime(2026, 7, 21, 10, 0, 0)
        first_claim = store.claim_upload_job(retry_job["id"], now=now)
        assert first_claim["attempts"] == 1
        retry_at = now + timedelta(seconds=30)
        retried = store.finish_upload_job(
            retry_job["id"],
            claim_token=first_claim["claim_token"],
            success=False,
            now=now,
            error="timeout",
            next_retry_at=retry_at,
        )
        assert retried["status"] == "retry"
        assert retried["attempts"] == 1
        assert retried["last_error"] == "timeout"
        assert retried["next_retry_at"] == "2026-07-21 10:00:30"
        assert store.claim_upload_job(retry_job["id"], now=now) is None
        second_claim = store.claim_upload_job(retry_job["id"], now=retry_at)
        assert second_claim["attempts"] == 2
        succeeded = store.finish_upload_job(
            retry_job["id"],
            claim_token=second_claim["claim_token"],
            success=True,
            now=retry_at,
            remote_id="remote-1",
        )
        assert succeeded["status"] == "succeeded"
        assert succeeded["remote_id"] == "remote-1"

        failed_job = store.create_upload_job(event["id"], "archive")
        failed_claim = store.claim_upload_job(failed_job["id"], now=now)
        assert failed_claim["attempts"] == 1
        failed = store.finish_upload_job(
            failed_job["id"],
            claim_token=failed_claim["claim_token"],
            success=False,
            now=now,
            error="bad request",
            max_attempts=1,
            next_retry_at=retry_at,
        )
        assert failed["status"] == "failed"
        assert failed["last_error"] == "bad request"
        assert failed["next_retry_at"] == "2026-07-21 10:00:30"
    finally:
        engine.dispose()


def test_expired_upload_lease_can_be_reclaimed_with_a_new_token():
    engine, store = _store()
    try:
        event = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "room_empty", "event_type": "room_empty"}
        )
        job = store.create_upload_job(event["id"], "platform")
        claimed_at = datetime(2026, 7, 21, 10, 0, 0)

        first_claim = store.claim_upload_job(job["id"], now=claimed_at, lease_seconds=60)
        assert first_claim["lease_expires_at"] == "2026-07-21 10:01:00"
        assert store.claim_upload_job(job["id"], now=claimed_at + timedelta(seconds=59)) is None

        recovered = store.claim_upload_job(job["id"], now=claimed_at + timedelta(seconds=60), lease_seconds=120)
        assert recovered["status"] == "uploading"
        assert recovered["attempts"] == 2
        assert recovered["claim_token"] != first_claim["claim_token"]
        assert recovered["claimed_at"] == "2026-07-21 10:01:00"
        assert recovered["lease_expires_at"] == "2026-07-21 10:03:00"
    finally:
        engine.dispose()


def test_expired_upload_lease_recovery_marks_job_failed_at_max_attempts():
    engine, store = _store()
    try:
        event_row = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "room_empty", "event_type": "room_empty"}
        )
        job = store.create_upload_job(event_row["id"], "platform")
        claimed_at = datetime(2026, 7, 21, 10, 0, 0)
        claim = store.claim_upload_job(job["id"], now=claimed_at, lease_seconds=60, max_attempts=5)
        assert claim["attempts"] == 1

        for expected_attempt in range(2, 6):
            claim = store.claim_upload_job(
                job["id"],
                now=claimed_at + timedelta(seconds=(expected_attempt - 1) * 60),
                lease_seconds=60,
                max_attempts=5,
            )
            assert claim["attempts"] == expected_attempt

        terminal = store.claim_upload_job(
            job["id"],
            now=claimed_at + timedelta(seconds=5 * 60),
            max_attempts=5,
        )
        assert terminal is None
        with store.session_factory() as session:
            persisted = session.query(ElectronicSealUploadJobMeta).filter_by(id=job["id"]).one()
            persisted = {
                "status": persisted.status,
                "attempts": persisted.attempts,
                "claim_token": persisted.claim_token,
                "lease_expires_at": persisted.lease_expires_at,
                "last_error": persisted.last_error,
            }
        assert persisted["status"] == "failed"
        assert persisted["attempts"] == 5
        assert persisted["claim_token"] is None
        assert persisted["lease_expires_at"] is None
        assert persisted["last_error"] == "max attempts reached"
    finally:
        engine.dispose()


def test_expired_upload_callback_is_ignored_until_the_job_is_reclaimed():
    engine, store = _store()
    try:
        event = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "room_empty", "event_type": "room_empty"}
        )
        job = store.create_upload_job(event["id"], "platform")
        claimed_at = datetime(2026, 7, 21, 10, 0, 0)
        claim = store.claim_upload_job(job["id"], now=claimed_at, lease_seconds=60)
        lease_expiry = claimed_at + timedelta(seconds=60)

        assert (
            store.finish_upload_job(
                job["id"],
                claim_token=claim["claim_token"],
                success=True,
                remote_id="late-remote",
                now=lease_expiry,
            )
            is None
        )
        with store.session_factory() as session:
            persisted = session.query(ElectronicSealUploadJobMeta).filter_by(id=job["id"]).one()
            assert persisted.status == "uploading"
            assert persisted.claim_token == claim["claim_token"]
    finally:
        engine.dispose()


def test_stale_upload_callback_cannot_overwrite_a_recovered_claim_or_terminal_state():
    engine, store = _store()
    try:
        event = store.create_event(
            {"channel_id": "seal-channel", "algorithm_type": "room_empty", "event_type": "room_empty"}
        )
        job = store.create_upload_job(event["id"], "platform")
        claimed_at = datetime(2026, 7, 21, 10, 0, 0)
        first_claim = store.claim_upload_job(job["id"], now=claimed_at, lease_seconds=60)
        recovered = store.claim_upload_job(job["id"], now=claimed_at + timedelta(seconds=60))

        assert (
            store.finish_upload_job(
                job["id"], claim_token=first_claim["claim_token"], success=True, remote_id="stale-remote"
            )
            is None
        )
        assert store.get_event(job["event_id"]) is not None

        finished = store.finish_upload_job(
            job["id"],
            claim_token=recovered["claim_token"],
            success=True,
            now=claimed_at + timedelta(seconds=60),
            remote_id="remote-1",
        )
        assert finished["status"] == "succeeded"
        assert finished["claim_token"] is None
        assert finished["lease_expires_at"] is None
        assert (
            store.finish_upload_job(
                job["id"], claim_token=recovered["claim_token"], success=False, error="late failure"
            )
            is None
        )
    finally:
        engine.dispose()
