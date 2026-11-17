"""Manage debounced health states and alarm transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any


def _epoch_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return float((value - datetime(1970, 1, 1)).total_seconds())
        return float(value.astimezone(timezone.utc).timestamp())
    return float(value)


@dataclass(slots=True)
class HealthTransition:
    """The state change produced by one health observation."""

    status: str
    create_event: bool = False
    close_event: bool = False
    observed_at: float = 0.0
    bad_started_at: float | None = None
    recovery_started_at: float | None = None
    reason: str | None = None
    score: float | None = None
    metrics: dict[str, Any] | None = None


class HealthStateMachine:
    """Debounce one algorithm's observations into stable health states.

    A detector result is sampled repeatedly.  The machine does not create an
    event on the first bad frame and does not close an event on the first good
    frame.  This keeps transient stream noise out of the event table.
    """

    def __init__(
        self,
        *,
        alarm_hold_seconds: float = 10.0,
        recovery_hold_seconds: float = 5.0,
    ):
        self.alarm_hold_seconds = max(float(alarm_hold_seconds), 0.0)
        self.recovery_hold_seconds = max(float(recovery_hold_seconds), 0.0)
        self.status = "normal"
        self.bad_started_at: float | None = None
        self.recovery_started_at: float | None = None
        self.event_started_at: float | None = None

    def update(
        self,
        *,
        is_bad: bool = False,
        quality_status: str | None = None,
        now: float | None = None,
        reason: str | None = None,
        score: float | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> HealthTransition:
        observed_at = float(time.time() if now is None else now)

        if quality_status == "unknown":
            self.recovery_started_at = None
            if self.status in {"alarming", "recovering", "offline"}:
                return self._transition(
                    observed_at,
                    reason=reason,
                    score=score,
                    metrics=metrics,
                )
            self.status = "unknown"
            self.bad_started_at = None
            self.event_started_at = None
            return self._transition(
                observed_at,
                reason=reason,
                score=score,
                metrics=metrics,
            )

        if quality_status == "offline":
            self.recovery_started_at = None
            if self.bad_started_at is None:
                self.bad_started_at = observed_at
            if self.event_started_at is None:
                elapsed = observed_at - self.bad_started_at
                if elapsed >= self.alarm_hold_seconds:
                    self.status = "offline"
                    self.event_started_at = self.bad_started_at
                    return self._transition(
                        observed_at,
                        create_event=True,
                        reason=reason or "offline",
                        score=score,
                        metrics=metrics,
                    )
            self.status = "offline"
            return self._transition(
                observed_at,
                reason=reason or "offline",
                score=score,
                metrics=metrics,
            )

        if is_bad:
            return self._update_bad(
                observed_at,
                reason=reason,
                score=score,
                metrics=metrics,
            )
        return self._update_good(
            observed_at,
            reason=reason,
            score=score,
            metrics=metrics,
        )

    def restore(
        self,
        *,
        status: str | None = None,
        bad_started_at: float | None = None,
        recovery_started_at: float | None = None,
        event_started_at: float | None = None,
    ) -> None:
        """Restore persisted debounce state after a process restart."""
        self.status = str(status or "normal")
        self.bad_started_at = _epoch_or_none(bad_started_at)
        self.recovery_started_at = _epoch_or_none(recovery_started_at)
        self.event_started_at = _epoch_or_none(event_started_at)

    def _update_bad(
        self,
        observed_at: float,
        *,
        reason: str | None,
        score: float | None,
        metrics: dict[str, Any] | None,
    ) -> HealthTransition:
        self.recovery_started_at = None
        if self.bad_started_at is None:
            self.bad_started_at = observed_at

        if self.status != "alarming":
            elapsed = observed_at - self.bad_started_at
            if elapsed >= self.alarm_hold_seconds:
                self.status = "alarming"
                self.event_started_at = self.bad_started_at
                return self._transition(
                    observed_at,
                    create_event=True,
                    reason=reason,
                    score=score,
                    metrics=metrics,
                )
            self.status = "suspected"

        return self._transition(
            observed_at,
            reason=reason,
            score=score,
            metrics=metrics,
        )

    def _update_good(
        self,
        observed_at: float,
        *,
        reason: str | None,
        score: float | None,
        metrics: dict[str, Any] | None,
    ) -> HealthTransition:
        self.bad_started_at = None
        if self.status in {"alarming", "recovering", "offline"}:
            if self.recovery_started_at is None:
                self.recovery_started_at = observed_at
            elapsed = observed_at - self.recovery_started_at
            if elapsed >= self.recovery_hold_seconds:
                self.status = "normal"
                self.recovery_started_at = None
                self.event_started_at = None
                return self._transition(
                    observed_at,
                    close_event=True,
                    reason=reason,
                    score=score,
                    metrics=metrics,
                )
            self.status = "recovering"
        else:
            self.status = "normal"
            self.recovery_started_at = None

        return self._transition(
            observed_at,
            reason=reason,
            score=score,
            metrics=metrics,
        )

    def _transition(
        self,
        observed_at: float,
        *,
        create_event: bool = False,
        close_event: bool = False,
        reason: str | None,
        score: float | None,
        metrics: dict[str, Any] | None,
    ) -> HealthTransition:
        return HealthTransition(
            status=self.status,
            create_event=create_event,
            close_event=close_event,
            observed_at=observed_at,
            bad_started_at=self.bad_started_at,
            recovery_started_at=self.recovery_started_at,
            reason=reason,
            score=score,
            metrics=metrics or {},
        )
