from datetime import datetime, timedelta
from typing import Any

from src.alarm_push.service import enqueue_metric_history
from src.db.database import SessionLocal
from src.db.models import ModelMetaRecord, TaskAlgorithmMeta, TaskAlgorithmMetricHistoryMeta, TaskAlgorithmMetricMeta
from src.services.platform_storage import format_datetime

REALTIME_UPDATE_INTERVAL_SECONDS = 1
HISTORY_SAMPLE_INTERVAL_SECONDS = 60
CUMULATIVE_METRIC_TYPES = frozenset({"count", "person_count", "vehicle_count"})


class MetricService:
    def reset_person_flow_sync(self, task: dict, algorithm: dict, regions: list[dict], run_id: str):
        now = datetime.now()
        with SessionLocal() as session:
            session.query(TaskAlgorithmMetricMeta).filter(
                TaskAlgorithmMetricMeta.task_id == task["id"],
                TaskAlgorithmMetricMeta.algorithm_id == algorithm["id"],
                TaskAlgorithmMetricMeta.metric_type == "person_count",
            ).delete(synchronize_session=False)
            for region in regions:
                session.add(TaskAlgorithmMetricMeta(
                    task_id=task["id"], algorithm_id=algorithm["id"],
                    channel_id=task.get("channel_id"), model_id=algorithm.get("model_id"),
                    metric_type="person_count", metric_value=0,
                    region_id=region["id"], region_name=region.get("name"), scope_key=region["id"],
                    updated_at=now, raw_json={"counting_mode": "person_flow", "run_id": run_id},
                ))
            session.commit()

    def record_person_flow_sync(
        self, *, task: dict, algorithm: dict, crossings: list[dict], run_id: str,
        now: datetime | None = None,
        snapshot_path: str | None = None,
    ) -> list[dict]:
        """Persist each confirmed crossing, its balance and delivery job atomically."""
        if not crossings:
            return []
        now = now or datetime.now()
        histories = []
        with SessionLocal() as session:
            for crossing in crossings:
                region_id = crossing["region_id"]
                row = session.query(TaskAlgorithmMetricMeta).filter(
                    TaskAlgorithmMetricMeta.task_id == task["id"],
                    TaskAlgorithmMetricMeta.algorithm_id == algorithm["id"],
                    TaskAlgorithmMetricMeta.metric_type == "person_count",
                    TaskAlgorithmMetricMeta.scope_key == region_id,
                ).with_for_update().one_or_none()
                if row is None:
                    row = TaskAlgorithmMetricMeta(
                        task_id=task["id"], algorithm_id=algorithm["id"], metric_type="person_count",
                        scope_key=region_id,
                    )
                    session.add(row)
                previous = float(row.metric_value or 0) if (row.raw_json or {}).get("run_id") == run_id else 0
                delta = crossing["delta"]
                if delta not in (-1, 1):
                    raise ValueError("person flow delta must be +1 or -1")
                details = {
                    "counting_mode": "person_flow", "run_id": run_id,
                    "delta": delta, "balance_before": previous,
                    "direction": crossing["direction"], "track_id": crossing["track_id"],
                }
                row.channel_id, row.model_id = task.get("channel_id"), algorithm.get("model_id")
                row.region_id, row.region_name = region_id, crossing.get("region_name")
                row.metric_value, row.updated_at, row.raw_json = previous + delta, now, dict(details)
                history = TaskAlgorithmMetricHistoryMeta(
                    task_id=task["id"], algorithm_id=algorithm["id"], channel_id=task.get("channel_id"),
                    model_id=algorithm.get("model_id"), metric_type="person_count", metric_value=row.metric_value,
                    region_id=region_id, region_name=crossing.get("region_name"),
                    snapshot_path=snapshot_path,
                    sampled_at=now, created_at=now, raw_json=dict(details),
                )
                session.add(history)
                session.flush()
                payload = self._history_from_row(history)
                enqueue_metric_history(session, payload)
                histories.append(payload)
            session.commit()
        return histories

    @staticmethod
    def _is_cumulative_metric(metric_type: str) -> bool:
        return str(metric_type or "").strip().lower() in CUMULATIVE_METRIC_TYPES

    @staticmethod
    def _parse_history_window_start(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    def _advance_cumulative_metric_window(
        self,
        session,
        *,
        row: TaskAlgorithmMetricMeta | None,
        task_id: str,
        algorithm_id: str,
        metric_type: str,
        region_id: str | None,
        metric_value: float,
        now: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        previous_raw = dict(row.raw_json or {}) if row else {}
        window_start = self._parse_history_window_start(
            previous_raw.get("history_window_started_at")
        )
        if window_start is None or now < window_start:
            window_start = now
            accumulated_value = 0.0
            sample_count = 0
        else:
            accumulated_value = float(
                previous_raw.get("history_accumulated_value", 0) or 0
            )
            sample_count = max(
                int(previous_raw.get("history_sample_count", 0) or 0),
                0,
            )

        pending_history: dict[str, Any] | None = None
        window_elapsed = now - window_start
        can_sample = sample_count > 0 and window_elapsed >= timedelta(
            seconds=HISTORY_SAMPLE_INTERVAL_SECONDS
        )
        if can_sample and self._should_sample_history(
            session,
            task_id=task_id,
            algorithm_id=algorithm_id,
            metric_type=metric_type,
            region_id=region_id,
            now=now,
        ):
            pending_history = {
                "metric_value": accumulated_value,
                "window_started_at": window_start.isoformat(),
                "window_ended_at": now.isoformat(),
                "sample_count": sample_count,
            }
            window_start = now
            accumulated_value = 0.0
            sample_count = 0

        accumulated_value += float(metric_value)
        sample_count += 1
        state = {
            "history_aggregation": "sum_realtime_values",
            "history_window_seconds": HISTORY_SAMPLE_INTERVAL_SECONDS,
            "history_window_started_at": window_start.isoformat(),
            "history_accumulated_value": accumulated_value,
            "history_sample_count": sample_count,
        }
        return state, pending_history

    def _metric_from_row(self, row: TaskAlgorithmMetricMeta, algorithm_names: dict[str, str], model_names: dict[str, str]) -> dict[str, Any]:
        raw_json = dict(row.raw_json or {})
        return {
            "id": row.id,
            "task_id": row.task_id,
            "algorithm_id": row.algorithm_id,
            "algorithm_name": raw_json.get("algorithm_name") or algorithm_names.get(row.algorithm_id),
            "channel_id": row.channel_id,
            "model_id": row.model_id,
            "model_name": raw_json.get("model_name") or model_names.get(str(row.model_id or "")),
            "metric_type": row.metric_type,
            "metric_value": row.metric_value,
            "region_id": row.region_id,
            "region_name": row.region_name,
            "updated_at": format_datetime(row.updated_at),
            "raw_json": raw_json,
        }

    def list_task_metrics_sync(self, task_id: str) -> dict[str, Any]:
        with SessionLocal() as session:
            rows = (
                session.query(TaskAlgorithmMetricMeta)
                .filter(TaskAlgorithmMetricMeta.task_id == task_id)
                .order_by(TaskAlgorithmMetricMeta.updated_at.desc(), TaskAlgorithmMetricMeta.algorithm_id.asc())
                .all()
            )
            algorithm_ids = [row.algorithm_id for row in rows if row.algorithm_id]
            model_ids = [str(row.model_id) for row in rows if row.model_id]
            algorithm_rows = (
                session.query(TaskAlgorithmMeta.id, TaskAlgorithmMeta.scene_name, TaskAlgorithmMeta.rule_type)
                .filter(TaskAlgorithmMeta.id.in_(algorithm_ids))
                .all()
                if algorithm_ids else []
            )
            model_rows = (
                session.query(ModelMetaRecord.id, ModelMetaRecord.name)
                .filter(ModelMetaRecord.id.in_(model_ids))
                .all()
                if model_ids else []
            )

        algorithm_names = {
            str(row[0]): row[1] or row[2] or str(row[0])
            for row in algorithm_rows
            if row and row[0]
        }
        model_names = {
            str(row[0]): row[1]
            for row in model_rows
            if row and row[0]
        }
        return {
            "items": [self._metric_from_row(row, algorithm_names, model_names) for row in rows],
            "total": len(rows),
        }

    async def list_task_metrics(self, task_id: str) -> dict[str, Any]:
        return self.list_task_metrics_sync(task_id)

    def _history_from_row(self, row: TaskAlgorithmMetricHistoryMeta) -> dict[str, Any]:
        return {
            "id": row.id,
            "task_id": row.task_id,
            "algorithm_id": row.algorithm_id,
            "channel_id": row.channel_id,
            "model_id": row.model_id,
            "metric_type": row.metric_type,
            "metric_value": row.metric_value,
            "region_id": row.region_id,
            "region_name": row.region_name,
            "snapshot_path": row.snapshot_path,
            "sampled_at": format_datetime(row.sampled_at),
            "created_at": format_datetime(row.created_at),
            "raw_json": dict(row.raw_json or {}),
        }

    def list_task_metric_history_sync(
        self,
        task_id: str,
        *,
        algorithm_id: str | None = None,
        metric_type: str | None = None,
        region_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 100), 1), 1000)
        with SessionLocal() as session:
            query = session.query(TaskAlgorithmMetricHistoryMeta).filter(TaskAlgorithmMetricHistoryMeta.task_id == task_id)
            if algorithm_id:
                query = query.filter(TaskAlgorithmMetricHistoryMeta.algorithm_id == algorithm_id)
            if metric_type:
                query = query.filter(TaskAlgorithmMetricHistoryMeta.metric_type == metric_type)
            if region_id:
                query = query.filter(TaskAlgorithmMetricHistoryMeta.region_id == region_id)
            if start_time:
                query = query.filter(TaskAlgorithmMetricHistoryMeta.sampled_at >= start_time)
            if end_time:
                query = query.filter(TaskAlgorithmMetricHistoryMeta.sampled_at <= end_time)
            total = query.count()
            rows = (
                query.order_by(TaskAlgorithmMetricHistoryMeta.sampled_at.desc(), TaskAlgorithmMetricHistoryMeta.id.desc())
                .offset((safe_page - 1) * safe_page_size)
                .limit(safe_page_size)
                .all()
            )
        return {
            "items": [self._history_from_row(row) for row in rows],
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
        }

    async def list_task_metric_history(self, task_id: str, **kwargs) -> dict[str, Any]:
        return self.list_task_metric_history_sync(task_id, **kwargs)

    def _should_update_realtime(self, row: TaskAlgorithmMetricMeta | None, now: datetime) -> bool:
        if not row or not row.updated_at:
            return True
        return now - row.updated_at >= timedelta(seconds=REALTIME_UPDATE_INTERVAL_SECONDS)

    def _should_sample_history(
        self,
        session,
        *,
        task_id: str,
        algorithm_id: str,
        metric_type: str,
        region_id: str | None,
        now: datetime,
    ) -> bool:
        latest = (
            session.query(TaskAlgorithmMetricHistoryMeta)
            .filter(TaskAlgorithmMetricHistoryMeta.task_id == task_id)
            .filter(TaskAlgorithmMetricHistoryMeta.algorithm_id == algorithm_id)
            .filter(TaskAlgorithmMetricHistoryMeta.metric_type == metric_type)
            .filter(TaskAlgorithmMetricHistoryMeta.region_id == region_id)
            .order_by(TaskAlgorithmMetricHistoryMeta.sampled_at.desc())
            .first()
        )
        if not latest or not latest.sampled_at:
            return True
        return now - latest.sampled_at >= timedelta(seconds=HISTORY_SAMPLE_INTERVAL_SECONDS)

    def _add_history_sample(
        self,
        session,
        *,
        task_id: str,
        algorithm_id: str,
        channel_id: str | None,
        model_id: str | None,
        metric_type: str,
        metric_value: float,
        region_id: str | None,
        region_name: str | None,
        raw_json: dict[str, Any],
        now: datetime,
    ) -> bool:
        if not self._should_sample_history(
            session,
            task_id=task_id,
            algorithm_id=algorithm_id,
            metric_type=metric_type,
            region_id=region_id,
            now=now,
        ):
            return False
        session.add(
            TaskAlgorithmMetricHistoryMeta(
                task_id=task_id,
                algorithm_id=algorithm_id,
                channel_id=channel_id,
                model_id=model_id,
                metric_type=metric_type,
                metric_value=float(metric_value),
                region_id=region_id,
                region_name=region_name,
                sampled_at=now,
                created_at=now,
                raw_json=dict(raw_json),
            )
        )
        return True

    def upsert_task_algorithm_metric_sync(
        self,
        *,
        task_id: str,
        algorithm_id: str,
        channel_id: str | None,
        model_id: str | None,
        metric_type: str,
        metric_value: float,
        region_id: str | None = None,
        region_name: str | None = None,
        raw_json: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now()
        payload = dict(raw_json or {})
        scope_key = str(region_id or "global")
        updated = False
        history_sampled = False
        with SessionLocal() as session:
            row = (
                session.query(TaskAlgorithmMetricMeta)
                .filter(TaskAlgorithmMetricMeta.task_id == task_id)
                .filter(TaskAlgorithmMetricMeta.algorithm_id == algorithm_id)
                .filter(TaskAlgorithmMetricMeta.metric_type == metric_type)
                .filter(TaskAlgorithmMetricMeta.scope_key == scope_key)
                .one_or_none()
            )
            should_update = self._should_update_realtime(row, now)
            pending_history: dict[str, Any] | None = None
            if should_update:
                if not row:
                    row = TaskAlgorithmMetricMeta()
                    session.add(row)
                row.task_id = task_id
                row.algorithm_id = algorithm_id
                row.channel_id = channel_id
                row.model_id = model_id
                row.metric_type = metric_type
                row.metric_value = float(metric_value)
                row.region_id = region_id
                row.region_name = region_name
                row.scope_key = scope_key
                row.updated_at = now
                runtime_raw_json = dict(payload)
                if self._is_cumulative_metric(metric_type):
                    history_state, pending_history = self._advance_cumulative_metric_window(
                        session,
                        row=row,
                        task_id=task_id,
                        algorithm_id=algorithm_id,
                        metric_type=metric_type,
                        region_id=region_id,
                        metric_value=float(metric_value),
                        now=now,
                    )
                    runtime_raw_json.update(history_state)
                row.raw_json = runtime_raw_json
                updated = True

            if self._is_cumulative_metric(metric_type):
                if pending_history is not None:
                    history_raw_json = dict(payload)
                    history_raw_json.update(
                        {
                            "metric_value": pending_history["metric_value"],
                            "history_aggregation": "sum_realtime_values",
                            "history_window_seconds": HISTORY_SAMPLE_INTERVAL_SECONDS,
                            "history_window_started_at": pending_history["window_started_at"],
                            "history_window_ended_at": pending_history["window_ended_at"],
                            "history_sample_count": pending_history["sample_count"],
                        }
                    )
                    history_sampled = self._add_history_sample(
                        session,
                        task_id=task_id,
                        algorithm_id=algorithm_id,
                        channel_id=channel_id,
                        model_id=model_id,
                        metric_type=metric_type,
                        metric_value=float(pending_history["metric_value"]),
                        region_id=region_id,
                        region_name=region_name,
                        raw_json=history_raw_json,
                        now=now,
                    )
                else:
                    history_sampled = False
            else:
                history_sampled = self._add_history_sample(
                    session,
                    task_id=task_id,
                    algorithm_id=algorithm_id,
                    channel_id=channel_id,
                    model_id=model_id,
                    metric_type=metric_type,
                    metric_value=float(metric_value),
                    region_id=region_id,
                    region_name=region_name,
                    raw_json=payload,
                    now=now,
                )
            session.commit()
            current_id = int(row.id) if row and row.id is not None else None
            current_value = float(row.metric_value or 0) if row else float(metric_value)
            current_updated_at = row.updated_at if row else now
            current_raw_json = dict(row.raw_json or payload) if row else payload

        return {
            "id": current_id,
            "task_id": task_id,
            "algorithm_id": algorithm_id,
            "channel_id": channel_id,
            "model_id": model_id,
            "metric_type": metric_type,
            "metric_value": current_value,
            "region_id": region_id,
            "region_name": region_name,
            "updated_at": format_datetime(current_updated_at),
            "raw_json": current_raw_json,
            "updated": updated,
            "history_sampled": history_sampled,
        }


metric_service = MetricService()
