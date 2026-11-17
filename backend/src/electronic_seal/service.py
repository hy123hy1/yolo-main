from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from src.electronic_seal.settings import ElectronicSealSettings


class ServiceComponent(Protocol):
    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...


class ElectronicSealService:
    """Own the electronic-seal worker lifecycle independently from AI tasks."""

    def __init__(
        self,
        settings: ElectronicSealSettings | None = None,
        components: Iterable[ServiceComponent] | None = None,
    ) -> None:
        self.settings = settings if settings is not None else ElectronicSealSettings()
        self.components = tuple(components or ())
        self._started_components: list[ServiceComponent] = []
        self._running = False
        self._startup_incomplete = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_startup_incomplete(self) -> bool:
        return self._startup_incomplete

    def start(self) -> None:
        if self._startup_incomplete:
            raise RuntimeError("Cannot start while pending component cleanup remains")
        if not self.settings.enabled or self._running:
            return
        try:
            for component in self.components:
                self._started_components.append(component)
                component.start()
        except Exception as exc:
            rollback_error = self._stop_started_components()
            self._running = False
            self._startup_incomplete = bool(self._started_components)
            if rollback_error is not None:
                exc.add_note(f"Startup rollback failed: {rollback_error}")
            raise
        self._running = True
        self._startup_incomplete = False

    def stop(self) -> None:
        first_error = self._stop_started_components()
        if first_error is None:
            self._startup_incomplete = False
        if first_error is not None:
            raise first_error

    def _stop_started_components(self) -> Exception | None:
        failed_component_ids: set[int] = set()
        first_error: Exception | None = None

        for component in reversed(self._started_components):
            try:
                component.stop()
            except Exception as exc:
                failed_component_ids.add(id(component))
                if first_error is None:
                    first_error = exc

        self._started_components = [
            component
            for component in self._started_components
            if id(component) in failed_component_ids
        ]
        self._running = bool(self._started_components)
        return first_error

    def run(self) -> None:
        self.start()


__all__ = ["ElectronicSealService", "ServiceComponent"]
