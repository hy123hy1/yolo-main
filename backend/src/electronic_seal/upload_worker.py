from __future__ import annotations

from datetime import datetime
import logging
import threading
from typing import Any, Callable


logger = logging.getLogger("uvicorn.error")


class ElectronicSealUploadWorker:
    """Drain persisted supervision upload jobs without touching frame workers."""

    def __init__(
        self,
        *,
        store: Any,
        uploader: Any,
        poll_seconds: float = 1.0,
        max_jobs: int = 100,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.store = store
        self.uploader = uploader
        self.poll_seconds = max(float(poll_seconds), 0.01)
        self.max_jobs = min(max(int(max_jobs), 1), 1000)
        self.now = now
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self.run,
            name="ElectronicSealUploadWorker",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=max(self.poll_seconds * 2, 1.0))
        self.thread = None

    def run_once(self) -> list[dict[str, Any]]:
        # Include abandoned leases so a worker restart can recover uploads after
        # the previous process died while the request was in flight.
        jobs = self.store.list_upload_jobs({"pending", "retry", "uploading"}, limit=self.max_jobs)
        results: list[dict[str, Any]] = []
        for job in jobs:
            result = self.uploader.process(str(job["id"]))
            if result is not None:
                results.append(result)
        return results

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("[ElectronicSeal] upload worker iteration failed")
            self.stop_event.wait(self.poll_seconds)


__all__ = ["ElectronicSealUploadWorker"]
