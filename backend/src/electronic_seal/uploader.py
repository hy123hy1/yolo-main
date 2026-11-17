from __future__ import annotations

from datetime import datetime, timedelta
import time
from typing import Any, Callable


class ElectronicSealUploader:
    """Process one leased upload job and persist retryable failures."""

    def __init__(
        self,
        *,
        store: Any,
        client: Any,
        now: Callable[[], datetime] = datetime.now,
        lease_seconds: int = 300,
        max_attempts: int = 5,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 300.0,
        deadline_seconds: float = 30.0,
    ) -> None:
        self.store = store
        self.client = client
        self.now = now
        self.lease_seconds = int(lease_seconds)
        self.max_attempts = int(max_attempts)
        self.retry_base_seconds = max(float(retry_base_seconds), 0.0)
        self.retry_max_seconds = max(float(retry_max_seconds), self.retry_base_seconds)
        self.deadline_seconds = max(float(deadline_seconds), 0.1)

    def process(self, job_id: str) -> dict[str, Any] | None:
        timestamp = self.now()
        job = self.store.claim_upload_job(
            job_id,
            now=timestamp,
            lease_seconds=self.lease_seconds,
            max_attempts=self.max_attempts,
        )
        if job is None:
            return None

        event_id = str(job["event_id"])
        claim_token = job["claim_token"]
        payload = dict(job.get("payload_json") or {})
        idempotency_key = event_id
        deadline = time.monotonic() + self.deadline_seconds
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError("upload deadline exceeded before request")
            event_payload = payload.get("event_payload")
            if not event_payload:
                event_payload = self.store.get_event(event_id)
            event_payload = event_payload or payload
            response = self.client.send_event(
                event_payload,
                request_id=str(claim_token),
                idempotency_key=idempotency_key,
            )
            if time.monotonic() >= deadline:
                raise TimeoutError("upload deadline exceeded after event request")
            remote_id = response.get("id") or response.get("remote_id")
            for evidence_path in payload.get("evidence_paths") or ():
                if time.monotonic() >= deadline:
                    raise TimeoutError("upload deadline exceeded before evidence request")
                self.client.upload_evidence(
                    str(remote_id or event_id),
                    str(evidence_path),
                    request_id=str(claim_token),
                    idempotency_key=idempotency_key,
                )
            completed_at = self.now()
            return self.store.finish_upload_job(
                job_id,
                claim_token=claim_token,
                success=True,
                now=completed_at,
                remote_id=remote_id,
                max_attempts=self.max_attempts,
            )
        except Exception as exc:
            attempts = max(int(job.get("attempts") or 1), 1)
            delay = min(
                self.retry_base_seconds * (2 ** (attempts - 1)),
                self.retry_max_seconds,
            )
            failed_at = self.now()
            return self.store.finish_upload_job(
                job_id,
                claim_token=claim_token,
                success=False,
                now=failed_at,
                error=str(exc),
                next_retry_at=failed_at + timedelta(seconds=delay),
                max_attempts=self.max_attempts,
            )

    def process_many(self, job_ids: list[str]) -> list[dict[str, Any]]:
        results = []
        for job_id in job_ids:
            result = self.process(job_id)
            if result is not None:
                results.append(result)
        return results


__all__ = ["ElectronicSealUploader"]
