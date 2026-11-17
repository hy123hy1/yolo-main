from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable, Iterable

import numpy as np

from config import settings


@dataclass(frozen=True, slots=True)
class PersonDetection:
    box: tuple[int, int, int, int]
    confidence: float
    track_id: str | None = None


@dataclass(frozen=True, slots=True)
class PersonCountResult:
    person_count: int
    detections: tuple[PersonDetection, ...]
    inference_ms: float
    source_frame: Any


def encode_person_count(person_count: int) -> str:
    value = int(person_count)
    if value < 0:
        raise ValueError("person count cannot be negative")
    if value > 9999:
        raise ValueError("person count cannot exceed 9999")
    return f"{value:04d}"


class PersonCountDetector:
    """Run one lazy-loaded YOLO model and keep only person detections."""

    def __init__(
        self,
        *,
        model_path: str | Path,
        confidence: float = 0.25,
        image_size: int = 640,
        device: str = "cuda:0",
        model_factory: Callable[[str | Path], Any] | None = None,
    ):
        if not 0 <= float(confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if int(image_size) <= 0:
            raise ValueError("image_size must be greater than 0")
        self.model_path = Path(model_path)
        self.confidence = float(confidence)
        self.image_size = int(image_size)
        self.device = str(device or "").strip()
        self.model_factory = model_factory
        self._model: Any | None = None

    def analyze(self, frame: Any, *, track: bool = False) -> PersonCountResult:
        if frame is None or not hasattr(frame, "size") or frame.size == 0:
            raise ValueError("frame is empty")
        model = self._ensure_model()
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "conf": self.confidence,
            "imgsz": self.image_size,
            "project": str(settings.person_count_run_dir),
            "save": False,
            "verbose": False,
        }
        if self.device:
            kwargs["device"] = self.device
        if track:
            kwargs["tracker"] = "bytetrack.yaml"
            kwargs["persist"] = True
            raw_results = model.track(frame, **kwargs)
        else:
            raw_results = model(frame, **kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000
        result = next(iter(raw_results), None) if _is_iterable(raw_results) else raw_results
        detections = self._extract_person_detections(result, model)
        return PersonCountResult(
            person_count=len(detections),
            detections=tuple(detections),
            inference_ms=elapsed_ms,
            source_frame=frame,
        )

    def new_context(self) -> "PersonCountDetector":
        """Create an independent detector context for one video stream.

        Ultralytics keeps ByteTrack state on the model/predictor when
        ``persist=True``.  A fresh detector therefore prevents track IDs from
        leaking between channels while retaining the same model settings.
        The model itself remains lazy-loaded in the new context.
        """
        return type(self)(
            model_path=self.model_path,
            confidence=self.confidence,
            image_size=self.image_size,
            device=self.device,
            model_factory=self.model_factory,
        )

    @staticmethod
    def result_from_detections(
        frame: Any,
        detections: Iterable[tuple[tuple[int, int, int, int], float]],
    ) -> PersonCountResult:
        parsed = tuple(
            PersonDetection(
                box=tuple(int(value) for value in box),
                confidence=float(confidence),
            )
            for box, confidence in detections
        )
        return PersonCountResult(
            person_count=len(parsed),
            detections=parsed,
            inference_ms=0.0,
            source_frame=frame,
        )

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self.model_factory is not None:
            self._model = self.model_factory(self.model_path)
            return self._model
        if not self.model_path.is_file():
            raise FileNotFoundError(f"person count model not found: {self.model_path}")
        from ultralytics import YOLO

        self._model = YOLO(str(self.model_path))
        return self._model

    def _extract_person_detections(
        self,
        result: Any,
        model: Any,
    ) -> list[PersonDetection]:
        if result is None or getattr(result, "boxes", None) is None:
            return []
        boxes = result.boxes
        classes = _to_numpy(getattr(boxes, "cls", []))
        confidences = _to_numpy(getattr(boxes, "conf", []))
        coordinates = _to_numpy(getattr(boxes, "xyxy", []))
        raw_track_ids = getattr(boxes, "id", None)
        track_ids = (
            _to_numpy(raw_track_ids).reshape(-1)
            if raw_track_ids is not None
            else np.empty((0,))
        )
        names = getattr(result, "names", None) or getattr(model, "names", None) or {}
        detections: list[PersonDetection] = []
        for index, (class_id, confidence, coordinate) in enumerate(
            zip(classes.tolist(), confidences.tolist(), coordinates.tolist())
        ):
            confidence_value = float(confidence)
            if confidence_value < self.confidence:
                continue
            if _class_name(names, int(class_id)).strip().lower() != "person":
                continue
            if len(coordinate) < 4:
                continue
            detections.append(
                PersonDetection(
                    box=tuple(int(round(value)) for value in coordinate[:4]),
                    confidence=confidence_value,
                    track_id=(
                        _track_id(track_ids[index])
                        if index < len(track_ids)
                        else None
                    ),
                )
            )
        return detections


def _is_iterable(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def _to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def _class_name(names: Any, class_id: int) -> str:
    if isinstance(names, dict):
        return str(names.get(class_id, names.get(str(class_id), "")))
    if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
        return str(names[class_id])
    return ""


def _track_id(value: Any) -> str | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        return text or None
    if not np.isfinite(numeric):
        return None
    return str(int(numeric)) if numeric.is_integer() else str(numeric)
