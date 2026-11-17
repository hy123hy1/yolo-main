from api.routes import create_app
from baseline.baseline_manager import BaselineManager
from config import settings
from storage.repository import CameraHealthRepository
from worker.channel_worker import WVPChannelClient, WVPChannelSyncService
from worker.manager import WorkerManager
from worker.person_count_coordinator import PersonCountCoordinator
from detectors.person_count import PersonCountDetector
from person_count.snapshot_manager import PersonCountSnapshotManager


repository = CameraHealthRepository(settings.database_url)
baseline_manager = BaselineManager(snapshot_dir=settings.snapshot_dir)
wvp_client = WVPChannelClient(
    api_base=settings.wvp_api_base,
    channel_list_path=settings.wvp_channel_list_path,
    channel_play_path=settings.wvp_channel_play_path,
)
sync_service = WVPChannelSyncService(
    repository=repository,
    client=wvp_client,
    baseline_manager=baseline_manager,
)
person_count_detector = PersonCountDetector(
    model_path=settings.person_count_model_path,
    confidence=settings.person_count_confidence,
    image_size=settings.person_count_image_size,
    device=settings.person_count_device,
)
person_count_snapshot_manager = PersonCountSnapshotManager(
    snapshot_dir=settings.person_count_snapshot_dir,
)
person_count_coordinator = PersonCountCoordinator(
    repository=repository,
    detector=person_count_detector,
    snapshot_manager=person_count_snapshot_manager,
)
worker_manager = WorkerManager(
    repository=repository,
    baseline_manager=baseline_manager,
    person_count_coordinator=person_count_coordinator,
)
sync_service.worker_manager = worker_manager
app = create_app(
    repository=repository,
    sync_service=sync_service,
    baseline_manager=baseline_manager,
    worker_manager=worker_manager,
    person_count_coordinator=person_count_coordinator,
    person_count_snapshot_dir=settings.person_count_snapshot_dir,
)
