import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import UploadFile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.services.model_service import ModelService
from src.services.platform_storage import generate_id


class IdGenerationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_dir = Path(self.temp_dir.name) / "registry"
        self.jobs_dir = Path(self.temp_dir.name) / "jobs"
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

        self.original_registry_dir = settings.REGISTRY_DIR
        self.original_jobs_dir = settings.JOBS_DIR
        settings.REGISTRY_DIR = self.registry_dir
        settings.JOBS_DIR = self.jobs_dir

    async def asyncTearDown(self):
        settings.REGISTRY_DIR = self.original_registry_dir
        settings.JOBS_DIR = self.original_jobs_dir
        self.temp_dir.cleanup()

    def test_generate_id_returns_uuid_only(self):
        generated = generate_id("task")

        self.assertRegex(generated, r"^[0-9a-f]{8}$")

    async def test_upload_model_uses_uuid_only_model_id(self):
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "model.json",
                json.dumps(
                    {
                        "name": "demo-model",
                        "classes": ["person"],
                        "description": "demo",
                        "tags": ["uploaded"],
                    },
                    ensure_ascii=False,
                ),
            )
            archive.writestr("weights/model.pt", b"fake-weights")
        memory_file.seek(0)

        upload = UploadFile(filename="demo-model.zip", file=memory_file)
        service = ModelService()

        with patch.object(service, "_get_model_info", AsyncMock(return_value=None)):
            result = await service.upload_model(upload)

        self.assertRegex(result["model_id"], r"^[0-9a-f]{8}$")
        self.assertTrue((self.registry_dir / result["model_id"] / "model.json").exists())


if __name__ == "__main__":
    unittest.main()
