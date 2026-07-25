from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class StateTransition:
    status: str
    create_event: bool = False
    close_event: bool = False
    reason: str | None = None


class CameraHealthStateMachine:
    def __init__(self, alarm_hold_seconds: int = 10, recovery_hold_seconds: int = 15):
        self.alarm_hold_seconds = max(float(alarm_hold_seconds), 0.0)
        self.recovery_hold_seconds = max(float(recovery_hold_seconds), 0.0)
        self.status = "normal"
        self.bad_since: float | None = None
        self.recovery_since: float | None = None
        self._alarm_was_active = False

    def update(
        self,
        *,
        is_bad: bool | None = None,
        quality_status: str | None = None,
        now: float,
    ) -> StateTransition:
        if quality_status in {"offline", "degraded"}:
            alarm_was_active = self.status in {"alarming", "recovering"}
            if alarm_was_active:
                self._alarm_was_active = True
            self.status = quality_status
            if not alarm_was_active:
                self.bad_since = None
            self.recovery_since = None
            return StateTransition(self.status, reason=quality_status)

        if bool(is_bad):
            self.recovery_since = None
            if self.status == "alarming":
                return StateTransition("alarming", reason="alarm_continues")
            if self.status == "recovering":
                self.status = "alarming"
                return StateTransition("alarming", reason="alarm_reopened")
            if self.status != "suspected" or self.bad_since is None:
                self.status = "suspected"
                self.bad_since = now
                return StateTransition("suspected", reason="bad_hold_started")
            if now - self.bad_since >= self.alarm_hold_seconds:
                self.status = "alarming"
                create_event = not self._alarm_was_active
                self._alarm_was_active = True
                return StateTransition("alarming", create_event=create_event, reason="bad_hold_reached")
            return StateTransition("suspected", reason="bad_hold_in_progress")

        if self.status == "alarming":
            self.status = "recovering"
            self.recovery_since = now
            return StateTransition("recovering", reason="recovery_hold_started")
        if self.status == "recovering":
            if self.recovery_since is not None and now - self.recovery_since >= self.recovery_hold_seconds:
                self.status = "normal"
                self.recovery_since = None
                self.bad_since = None
                self._alarm_was_active = False
                return StateTransition("normal", close_event=True, reason="recovery_hold_reached")
            return StateTransition("recovering", reason="recovery_hold_in_progress")

        if self.status in {"offline", "degraded"} and self._alarm_was_active:
            self.status = "recovering"
            self.recovery_since = now
            return StateTransition("recovering", reason="recovery_hold_started_after_unavailable")

        self.bad_since = None
        self.status = "normal"
        self.recovery_since = None
        return StateTransition("normal", reason="normal_frame")
