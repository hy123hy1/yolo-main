import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import UploadFile
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.db.models import Base, ModelMetaRecord
from src.services import metadata_db_sync
from src.services.platform_storage import save_json as persist_json
from src.services.model_service import ModelService


class ModelRecommendationTest(unittest.TestCase):
    def setUp(self):
        self.service = ModelService()

    def test_mapped_small_class_model_uses_scene_mapping(self):
        model_meta = {
            "name": "安全帽检测",
            "classes": ["helmet", "no_helmet"],
        }

        result = self.service._apply_label_recommendation(model_meta)

        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["no_helmet"])
        self.assertTrue(
            any(item["scene_key"] == "helmet_detection" for item in result["scene_candidates"])
        )

    def test_large_class_model_uses_name_mapping(self):
        model_meta = {
            "name": "人员检测",
            "classes": ["person", "car", "truck", "bus", "bicycle", "motorcycle"],
        }

        result = self.service._apply_label_recommendation(model_meta)

        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["person"])
        self.assertEqual(result["scene_candidates"][0]["scene_key"], "person_intrusion")

    def test_fire_model_name_maps_to_detection_scene(self):
        model_meta = {
            "name": "fire detection",
            "classes": ["fire", "smoke", "person", "car", "truck", "bus"],
        }

        result = self.service._apply_label_recommendation(model_meta)

        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["fire"])
        self.assertEqual(result["scene_candidates"][0]["scene_key"], "fire_alarm")
        self.assertEqual(result["scene_candidates"][0]["rule_type"], "detection")

    def test_smoke_model_name_maps_to_smoke_only(self):
        model_meta = {
            "name": "烟雾检测",
            "classes": ["fire", "smoke"],
        }

        result = self.service._apply_label_recommendation(model_meta)

        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["smoke"])
        self.assertEqual(result["scene_candidates"][0]["scene_key"], "smoke_alarm")
        self.assertEqual(result["scene_candidates"][0]["rule_type"], "detection")

    def test_large_class_model_with_no_match_falls_back_to_manual(self):
        model_meta = {
            "name": "场景一",
            "classes": ["person", "car", "truck", "bus", "bicycle", "motorcycle"],
        }

        result = self.service._apply_label_recommendation(model_meta)

        self.assertEqual(result["auto_label_strategy"], "manual")
        self.assertEqual(result["recommended_labels"], [])
        self.assertEqual(result["scene_candidates"], [])


    def test_sleep_small_class_model_keeps_scene_candidate(self):
        model_meta = {
            "name": "睡岗检测",
            "classes": ["normal", "sleep"],
        }

        result = self.service._apply_label_recommendation(model_meta)

        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["sleep"])
        self.assertEqual(result["scene_candidates"][0]["scene_key"], "sleep_on_duty")
        self.assertEqual(result["scene_candidates"][0]["scene_name"], "睡岗检测")
        self.assertEqual(result["scene_candidates"][0]["rule_type"], "detection")


if __name__ == "__main__":
    unittest.main()


class ModelMetadataSyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_dir = Path(self.temp_dir.name) / "registry"
        self.jobs_dir = Path(self.temp_dir.name) / "jobs"
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path = Path(self.temp_dir.name) / "metadata.db"

        self.original_registry_dir = settings.REGISTRY_DIR
        self.original_jobs_dir = settings.JOBS_DIR
        settings.REGISTRY_DIR = self.registry_dir
        settings.JOBS_DIR = self.jobs_dir

        self.original_engine = metadata_db_sync.engine
        self.original_session_local = metadata_db_sync.SessionLocal
        from src.services import model_service as model_service_module

        self.original_model_service_session_local = model_service_module.SessionLocal
        self.engine = create_engine(
            f"sqlite:///{self.sqlite_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.session_local = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        metadata_db_sync.engine = self.engine
        metadata_db_sync.SessionLocal = self.session_local
        model_service_module.SessionLocal = self.session_local
        Base.metadata.create_all(bind=self.engine)

    async def asyncTearDown(self):
        self.engine.dispose()
        metadata_db_sync.engine = self.original_engine
        metadata_db_sync.SessionLocal = self.original_session_local
        from src.services import model_service as model_service_module

        model_service_module.SessionLocal = self.original_model_service_session_local
        settings.REGISTRY_DIR = self.original_registry_dir
        settings.JOBS_DIR = self.original_jobs_dir
        self.temp_dir.cleanup()

    def _insert_model_row(
        self,
        model_id: str = "dbonly01",
        name: str = "db-only-model",
        *,
        weights_path: str | None = None,
        version: str | None = None,
    ) -> None:
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id=model_id,
                    name=name,
                    version=version,
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=weights_path,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": model_id,
                        "name": name,
                        "version": version,
                        "description": "from-db",
                        "tags": ["db"],
                        "classes": ["person"],
                        "weights_path": weights_path,
                    },
                )
            )
            session.commit()

    async def test_upload_and_delete_model_sync_metadata_db(self):
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "model.json",
                json.dumps(
                    {
                        "name": "helmet-model",
                        "classes": ["helmet", "person"],
                        "description": "demo",
                        "tags": ["uploaded"],
                    },
                    ensure_ascii=False,
                ),
            )
            archive.writestr("weights/model.pt", b"fake-weights")
        memory_file.seek(0)

        upload = UploadFile(filename="helmet-model.zip", file=memory_file)
        service = ModelService()

        with patch.object(service, "_get_model_info", AsyncMock(return_value=None)):
            result = await service.upload_model(upload)

        with self.session_local() as session:
            row = session.execute(
                select(ModelMetaRecord).where(ModelMetaRecord.id == result["model_id"])
            ).scalar_one_or_none()
        self.assertIsNotNone(row)
        self.assertEqual(row.name, "helmet-model")

        await service.delete_model(result["model_id"])

        with self.session_local() as session:
            row = session.execute(
                select(ModelMetaRecord).where(ModelMetaRecord.id == result["model_id"])
            ).scalar_one_or_none()
        self.assertIsNone(row)

    async def test_update_model_only_changes_name(self):
        model_id = "abcd1234"
        self._insert_model_row(model_id, "old-name")

        service = ModelService()

        class RenameOnlyRequest:
            name = "new-name"

        result = await service.update_model(model_id, RenameOnlyRequest())

        self.assertEqual(result["name"], "new-name")
        self.assertEqual(result["description"], "from-db")
        self.assertEqual(result["tags"], ["db"])
        self.assertIn("updated_at", result)

    async def test_list_models_returns_db_rows_without_local_model_file(self):
        self._insert_model_row("dbonly01", "db-only-model", version="v1.2.3")

        service = ModelService()
        result = await service.list_models()

        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["model_id"], "dbonly01")
        self.assertEqual(result["models"][0]["name"], "db-only-model")
        self.assertEqual(result["models"][0]["version"], "v1.2.3")
        self.assertEqual(result["models"][0]["description"], "from-db")

    async def test_get_model_returns_db_row_without_local_model_file(self):
        self._insert_model_row("dbdetail", "db-detail-model", version="2026.07")

        service = ModelService()
        result = await service.get_model("dbdetail")

        self.assertIsNotNone(result)
        self.assertEqual(result["model_id"], "dbdetail")
        self.assertEqual(result["name"], "db-detail-model")
        self.assertEqual(result["version"], "2026.07")
        self.assertEqual(result["description"], "from-db")

    async def test_update_model_updates_db_row_without_local_model_file(self):
        self._insert_model_row("dbrename", "old-db-name")

        service = ModelService()

        class RenameOnlyRequest:
            name = "new-db-name"

        result = await service.update_model("dbrename", RenameOnlyRequest())

        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "new-db-name")
        self.assertEqual(result["description"], "from-db")
        with self.session_local() as session:
            row = session.execute(select(ModelMetaRecord).where(ModelMetaRecord.id == "dbrename")).scalar_one()
        self.assertEqual(row.name, "new-db-name")
        self.assertEqual(row.raw_json["name"], "new-db-name")

    async def test_list_models_fills_missing_default_confidence_and_alert_interval(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="dbdefaults",
                    name="defaults-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=None,
                    alert_interval=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "dbdefaults",
                        "name": "defaults-model",
                        "classes": ["person"],
                    },
                )
            )
            session.commit()

        service = ModelService()
        result = await service.list_models()

        self.assertEqual(result["models"][0]["model_id"], "dbdefaults")
        self.assertEqual(result["models"][0]["confidence"], 0.25)
        self.assertEqual(result["models"][0]["alert_interval"], 60)

    async def test_update_model_persists_default_confidence_and_alert_interval(self):
        self._insert_model_row("dbdefaults02", "defaults-edit-model")

        service = ModelService()

        class UpdateDefaultsRequest:
            name = "defaults-edit-model"
            confidence = 0.6
            alert_interval = 15

        result = await service.update_model("dbdefaults02", UpdateDefaultsRequest())

        self.assertEqual(result["confidence"], 0.6)
        self.assertEqual(result["alert_interval"], 15)

        with self.session_local() as session:
            row = session.execute(
                select(ModelMetaRecord).where(ModelMetaRecord.id == "dbdefaults02")
            ).scalar_one()

        self.assertEqual(row.confidence, 0.6)
        self.assertEqual(row.alert_interval, 15)

    async def test_get_model_summary_returns_model_default_confidence_and_alert_interval(self):
        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id="dbsummarydefaults",
                    name="summary-defaults-model",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=None,
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    confidence=0.5,
                    alert_interval=45,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": "dbsummarydefaults",
                        "name": "summary-defaults-model",
                        "classes": ["person"],
                        "confidence": 0.5,
                        "alert_interval": 45,
                    },
                )
            )
            session.commit()

        service = ModelService()
        result = await service.get_model_summary("dbsummarydefaults")

        self.assertEqual(result["confidence"], 0.5)
        self.assertEqual(result["alert_interval"], 45)

    async def test_delete_model_deletes_db_row_without_local_model_dir(self):
        self._insert_model_row("dbdelete", "delete-me")

        service = ModelService()
        result = await service.delete_model("dbdelete")

        self.assertEqual(result["ok"], True)
        with self.session_local() as session:
            row = session.execute(
                select(ModelMetaRecord).where(ModelMetaRecord.id == "dbdelete")
            ).scalar_one_or_none()
        self.assertIsNone(row)

    async def test_list_models_prefers_metadata_rows(self):
        model_id = "meta1234"
        model_dir = self.registry_dir / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        model_file = model_dir / "model.json"
        persist_json(
            model_file,
            {
                "model_id": model_id,
                "name": "json-name",
                "classes": ["person"],
                "weights_path": str((model_dir / "weights.pt").resolve()),
                "created_at": "2026-06-16 10:00:00",
                "updated_at": "2026-06-16 10:00:00",
            },
        )

        with self.session_local() as session:
            session.merge(
                ModelMetaRecord(
                    id=model_id,
                    name="db-name",
                    source="uploaded",
                    description="from-db",
                    tags_json=["db"],
                    classes_json=["person"],
                    weights_path=str((model_dir / "weights.pt").resolve()),
                    file_size=123,
                    file_size_mb=0.12,
                    job_id=None,
                    created_at=None,
                    updated_at=None,
                    raw_json={
                        "model_id": model_id,
                        "name": "db-name",
                        "description": "from-db",
                        "tags": ["db"],
                        "classes": ["person"],
                    },
                )
            )
            session.commit()

        service = ModelService()
        result = await service.list_models()

        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["model_id"], model_id)
        self.assertEqual(result["models"][0]["name"], "db-name")
        self.assertEqual(result["models"][0]["description"], "from-db")

    async def test_list_models_runtime_file_size_refresh_uses_cached_metadata_summary(self):
        self._insert_model_row("model_summary_1", "model-summary-1")

        service = ModelService()
        with patch.object(
            service,
            "_refresh_runtime_file_size",
            side_effect=AssertionError("list_models should not refresh runtime file size"),
        ):
            result = await service.list_models()

        self.assertEqual(result["models"][0]["model_id"], "model_summary_1")
        self.assertEqual(result["models"][0]["file_size"], 123)
