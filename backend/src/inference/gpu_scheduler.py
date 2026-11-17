"""Best-effort, process-local placement for long-running NVIDIA tasks."""
from dataclasses import dataclass
import logging
import subprocess
import threading
from uuid import uuid4


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GpuSample:
    index: int
    uuid: str
    free_mb: int
    utilization: float | None = None


@dataclass(frozen=True)
class GpuAllocation:
    token: str
    device: str
    reserved_mb: int


def sample_gpus(candidates: str) -> list[GpuSample]:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("Adaptive GPU scheduling requires available NVIDIA CUDA devices")
    count = torch.cuda.device_count()
    indices = list(range(count))
    if candidates.strip():
        try:
            indices = sorted({int(value.strip()) for value in candidates.split(",")})
        except ValueError as exc:
            raise ValueError("AI_GPU_CANDIDATES must contain comma-separated CUDA indices") from exc
        if not indices or any(index < 0 or index >= count for index in indices):
            raise ValueError(f"AI_GPU_CANDIDATES must refer to visible CUDA devices 0..{count - 1}")

    utilization = {}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            gpu_uuid, value = (part.strip() for part in line.split(","))
            utilization[gpu_uuid] = float(value)
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.debug("GPU utilization unavailable; using free memory and task reservations", exc_info=True)

    samples = []
    for index in indices:
        # CUDA-local indices respect visibility/reordering; physical nvidia-smi indices do not.
        free_bytes, _ = torch.cuda.mem_get_info(index)
        gpu_uuid = str(getattr(torch.cuda.get_device_properties(index), "uuid", ""))
        samples.append(GpuSample(index, gpu_uuid, int(free_bytes // (1024 * 1024)), utilization.get(gpu_uuid)))
    return samples


class GpuScheduler:
    def __init__(self, sampler=None):
        self._sampler = sampler or sample_gpus
        self._lock = threading.Lock()
        self._allocations: dict[str, tuple[GpuAllocation, int]] = {}

    def reserve(self, *, candidates: str, min_free_mb: int, reserve_mb: int) -> GpuAllocation:
        if min_free_mb < 0 or reserve_mb <= 0:
            raise ValueError("GPU minimum free memory must be >= 0 and task reserve must be > 0")
        with self._lock:
            samples = self._sampler(candidates)
            use_utilization = bool(samples) and all(sample.utilization is not None for sample in samples)
            ranked = []
            for sample in samples:
                device = f"cuda:{sample.index}"
                assigned = [allocation for allocation, _ in self._allocations.values() if allocation.device == device]
                effective_free = sample.free_mb - sum(item.reserved_mb for item in assigned)
                if effective_free < min_free_mb + reserve_mb:
                    continue
                utilization = sample.utilization if use_utilization else 0.0
                score = effective_free * (1.0 - min(max(utilization, 0.0), 100.0) / 200.0) / (1 + len(assigned))
                ranked.append((score, -sample.index, sample, effective_free))
            if not ranked:
                raise RuntimeError(
                    f"No eligible NVIDIA GPU: require {min_free_mb} MiB headroom + {reserve_mb} MiB task reserve; "
                    "check GPU usage, candidate devices or scheduling thresholds"
                )
            _, _, sample, effective_free = max(ranked, key=lambda item: item[:2])
            allocation = GpuAllocation(uuid4().hex, f"cuda:{sample.index}", reserve_mb)
            self._allocations[allocation.token] = (allocation, 1)
            logger.info(
                "[GPUAllocation] device=%s uuid=%s free_mb=%s effective_free_mb=%s utilization=%s reserve_mb=%s",
                allocation.device, sample.uuid or "unavailable", sample.free_mb, effective_free,
                sample.utilization, reserve_mb,
            )
            return allocation

    def retain(self, allocation: GpuAllocation) -> None:
        with self._lock:
            current, references = self._allocations[allocation.token]
            self._allocations[allocation.token] = (current, references + 1)

    def release(self, allocation: GpuAllocation) -> None:
        with self._lock:
            current, references = self._allocations[allocation.token]
            if references == 1:
                del self._allocations[allocation.token]
            else:
                self._allocations[allocation.token] = (current, references - 1)
