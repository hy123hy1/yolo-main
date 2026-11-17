from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(eq=False)
class RealtimeDetectionSubscription:
    task_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]
    _hub: "RealtimeDetectionHub"
    _active: bool = True

    def unsubscribe(self) -> None:
        self._hub.unsubscribe(self)

    def _enqueue_latest(self, payload: dict[str, Any]) -> None:
        if not self._active:
            return
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.debug("Realtime detection queue remained full: task_id=%s", self.task_id)


class RealtimeDetectionHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[RealtimeDetectionSubscription]] = {}

    def subscribe(
        self,
        task_id: str,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> RealtimeDetectionSubscription:
        event_loop = loop or asyncio.get_running_loop()
        subscription = RealtimeDetectionSubscription(
            task_id=str(task_id),
            loop=event_loop,
            queue=asyncio.Queue(maxsize=1),
            _hub=self,
        )
        with self._lock:
            self._subscribers.setdefault(subscription.task_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: RealtimeDetectionSubscription) -> None:
        with self._lock:
            subscription._active = False
            subscribers = self._subscribers.get(subscription.task_id)
            if not subscribers:
                return
            subscribers.discard(subscription)
            if not subscribers:
                self._subscribers.pop(subscription.task_id, None)

    def subscriber_count(self, task_id: str) -> int:
        with self._lock:
            return len(self._subscribers.get(str(task_id), set()))

    def publish(self, task_id: str, payload: dict[str, Any]) -> None:
        try:
            safe_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError):
            logger.warning("Skip non-JSON-safe realtime detection payload: task_id=%s", task_id)
            return

        with self._lock:
            subscribers = list(self._subscribers.get(str(task_id), set()))

        for subscription in subscribers:
            if not subscription._active:
                continue
            try:
                subscription.loop.call_soon_threadsafe(
                    subscription._enqueue_latest,
                    safe_payload,
                )
            except RuntimeError:
                self.unsubscribe(subscription)

    def close(self) -> None:
        with self._lock:
            subscribers = [
                subscription
                for task_subscribers in self._subscribers.values()
                for subscription in task_subscribers
            ]
            self._subscribers.clear()
            for subscription in subscribers:
                subscription._active = False


realtime_detection_hub = RealtimeDetectionHub()
