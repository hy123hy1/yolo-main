import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.models import Base, TaskAlgorithmMetricHistoryMeta, TaskAlgorithmMetricMeta
from src.services import metric_service as metric_service_module


class MetricServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sqlite_path = Path(self.temp_dir.name) / "metric-service.db"

        self.original_session_local = metric_service_module.SessionLocal
        self.engine = create_engine(
            f"sqlite:///{self.sqlite_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.session_local = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        metric_service_module.SessionLocal = self.session_local
        Base.metadata.create_all(bind=self.engine)
        self.service = metric_service_module.MetricService()

    def tearDown(self):
        self.engine.dispose()
        metric_service_module.SessionLocal = self.original_session_local
        self.temp_dir.cleanup()

    def test_upsert_realtime_metric_uses_numeric_id_and_scope_key(self):
        now = datetime(2026, 6, 24, 12, 0, 0)

        result = self.service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_numeric",
            algorithm_id="algo_metric_numeric",
            channel_id="ch_metric_numeric",
            model_id="model_metric_numeric",
            metric_type="person_count",
            metric_value=2,
            region_id=None,
            region_name=None,
            raw_json={"class_counts": {"person": 2}},
            now=now,
        )

        with self.session_local() as session:
            row = session.execute(
                select(TaskAlgorithmMetricMeta).where(
                    TaskAlgorithmMetricMeta.task_id == "task_metric_numeric"
                )
            ).scalar_one()

        self.assertIsInstance(result["id"], int)
        self.assertEqual(getattr(row, "scope_key", None), "global")
        self.assertEqual(row.metric_value, 2.0)

    def test_realtime_metric_upsert_reuses_same_row_for_same_scope(self):
        first = self.service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_reuse",
            algorithm_id="algo_metric_reuse",
            channel_id="ch_metric_reuse",
            model_id="model_metric_reuse",
            metric_type="person_count",
            metric_value=1,
            region_id="region_a",
            region_name="Zone A",
            raw_json={"class_counts": {"person": 1}},
            now=datetime(2026, 6, 24, 12, 1, 0),
        )

        second = self.service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_reuse",
            algorithm_id="algo_metric_reuse",
            channel_id="ch_metric_reuse",
            model_id="model_metric_reuse",
            metric_type="person_count",
            metric_value=3,
            region_id="region_a",
            region_name="Zone A",
            raw_json={"class_counts": {"person": 3}},
            now=datetime(2026, 6, 24, 12, 1, 2),
        )

        with self.session_local() as session:
            rows = session.execute(
                select(TaskAlgorithmMetricMeta).where(
                    TaskAlgorithmMetricMeta.task_id == "task_metric_reuse"
                )
            ).scalars().all()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].metric_value, 3.0)

    def test_history_metric_uses_numeric_auto_increment_id(self):
        self.service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_history",
            algorithm_id="algo_metric_history",
            channel_id="ch_metric_history",
            model_id="model_metric_history",
            metric_type="person_count",
            metric_value=1,
            region_id=None,
            region_name=None,
            raw_json={"class_counts": {"person": 1}},
            now=datetime(2026, 6, 24, 12, 2, 0),
        )

        self.service.upsert_task_algorithm_metric_sync(
            task_id="task_metric_history",
            algorithm_id="algo_metric_history",
            channel_id="ch_metric_history",
            model_id="model_metric_history",
            metric_type="person_count",
            metric_value=2,
            region_id=None,
            region_name=None,
            raw_json={"class_counts": {"person": 2}},
            now=datetime(2026, 6, 24, 12, 3, 1),
        )

        with self.session_local() as session:
            rows = session.execute(
                select(TaskAlgorithmMetricHistoryMeta).where(
                    TaskAlgorithmMetricHistoryMeta.task_id == "task_metric_history"
                ).order_by(TaskAlgorithmMetricHistoryMeta.sampled_at.asc())
            ).scalars().all()

        self.assertGreaterEqual(len(rows), 2)
        self.assertTrue(all(isinstance(row.id, int) for row in rows))
        self.assertEqual(rows[-1].metric_value, 2.0)

