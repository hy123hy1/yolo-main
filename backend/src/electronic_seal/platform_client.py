from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


Requester = Callable[..., dict[str, Any]]


class SupervisionPlatformClient:
    """Small HTTP adapter with injectable transport for the supervision platform."""

    def __init__(
        self,
        *,
        base_url: str,
        event_path: str = "/events",
        evidence_path: str = "/events/{event_id}/evidence",
        timeout_seconds: float = 10.0,
        requester: Requester | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.event_path = event_path
        self.evidence_path = evidence_path
        self.timeout_seconds = float(timeout_seconds)
        self.requester = requester or self._request

    def send_event(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = self._headers(request_id, idempotency_key)
        return self.requester(
            "POST",
            f"{self.base_url}{self.event_path}",
            headers=headers,
            payload=payload,
            timeout=self.timeout_seconds,
        )

    def upload_evidence(
        self,
        event_id: str,
        evidence_path: str,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = self._headers(request_id, idempotency_key)
        return self.requester(
            "POST",
            f"{self.base_url}{self.evidence_path.format(event_id=event_id)}",
            headers=headers,
            payload={"event_id": event_id, "evidence_path": evidence_path},
            timeout=self.timeout_seconds,
        )

    @staticmethod
    def _headers(request_id: str | None, idempotency_key: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "X-Request-ID": request_id or uuid4().hex}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _request(method: str, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float):
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"supervision platform request failed: {exc}") from exc
        if not raw:
            return {}
        result = json.loads(raw)
        return result if isinstance(result, dict) else {"data": result}


__all__ = ["SupervisionPlatformClient"]
