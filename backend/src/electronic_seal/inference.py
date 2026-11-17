from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np


@dataclass(frozen=True)
class YoloDetection:
    label: str
    confidence: float
    bbox: list[float]
    class_id: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "bbox": list(self.bbox),
            "class_id": self.class_id,
        }


def _default_metadata_loader(model_id: str, registry_dir: Path) -> dict[str, Any]:
    model_file = registry_dir / model_id / "model.json"
    if not model_file.exists():
        raise ValueError(f"model not found: {model_id}")
    try:
        return json.loads(model_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to read model metadata: {model_id}") from exc


class YoloInferenceAdapter:
    """Load registered YOLO weights and expose a stable detection contract.

    This adapter is intentionally independent from ``AnalysisService``. The
    electronic-seal worker can therefore share model files without sharing AI
    task workers, metrics, or task state.
    """

    def __init__(
        self,
        *,
        registry_dir: Path | str,
        device: str = "cpu",
        model_loader: Callable[[str], Any] | None = None,
        metadata_loader: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.registry_dir = Path(registry_dir)
        self.device = device
        self._model_loader = model_loader or self._load_yolo_model
        self._metadata_loader = metadata_loader or (
            lambda model_id: _default_metadata_loader(model_id, self.registry_dir)
        )
        self._model_cache: dict[str, tuple[Any, dict[str, Any]]] = {}

    def detect(
        self,
        model_id: str,
        frame: np.ndarray,
        *,
        conf_threshold: float = 0.25,
    ) -> list[dict[str, Any]]:
        model_id = str(model_id or "").strip()
        if not model_id:
            raise ValueError("model_id must not be empty")
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("frame must be a non-empty numpy image")
        try:
            conf_threshold = float(conf_threshold)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("conf_threshold must be between 0 and 1") from exc
        if not 0.0 <= conf_threshold <= 1.0:
            raise ValueError("conf_threshold must be between 0 and 1")

        model, metadata = self._get_model(model_id)
        call_kwargs = {"verbose": False, "conf": conf_threshold}
        if self.device:
            call_kwargs["device"] = self.device
        results = model(frame, **call_kwargs)
        detections: list[dict[str, Any]] = []
        for result in results or ():
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            names = (
                metadata.get("classes")
                or getattr(result, "names", None)
                or getattr(model, "names", None)
                or getattr(getattr(model, "model", None), "names", None)
                or {}
            )
            for index in range(len(boxes)):
                box = boxes[index]
                class_id = int(self._scalar(getattr(box, "cls", [0])))
                confidence = float(self._scalar(getattr(box, "conf", [0.0])))
                xyxy = self._values(getattr(box, "xyxy", []))
                if len(xyxy) == 1 and isinstance(xyxy[0], (list, tuple)):
                    xyxy = xyxy[0]
                if len(xyxy) < 4:
                    continue
                label = self._class_name(names, class_id)
                detections.append(
                    YoloDetection(
                        label=label,
                        confidence=confidence,
                        bbox=[float(value) for value in xyxy[:4]],
                        class_id=class_id,
                    ).to_dict()
                )
        return detections

    def _get_model(self, model_id: str) -> tuple[Any, dict[str, Any]]:
        cached = self._model_cache.get(model_id)
        if cached is not None:
            return cached
        metadata = dict(self._metadata_loader(model_id) or {})
        weights_value = str(metadata.get("weights_path") or "").strip()
        if not weights_value:
            raise ValueError(f"model weights_path not found: {model_id}")
        weights_path = Path(weights_value)
        if not weights_path.is_absolute():
            weights_path = self.registry_dir / model_id / weights_path
        weights_path = weights_path.resolve()
        if not weights_path.exists():
            fallback = self.registry_dir / model_id / "weights" / weights_path.name
            if fallback.exists():
                weights_path = fallback.resolve()
            else:
                raise ValueError(f"model weights not found: {weights_path}")
        model = self._model_loader(str(weights_path))
        self._model_cache[model_id] = (model, metadata)
        return model, metadata

    @staticmethod
    def _load_yolo_model(weights_path: str) -> Any:
        from ultralytics import YOLO

        return YOLO(weights_path)

    @staticmethod
    def _values(value: Any) -> list[Any]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, (tuple, list)):
            return list(value)
        return [value]

    @classmethod
    def _scalar(cls, value: Any) -> Any:
        values = cls._values(value)
        if len(values) == 1 and isinstance(values[0], (tuple, list)):
            return cls._scalar(values[0])
        return values[0] if values else 0

    @staticmethod
    def _class_name(names: Any, class_id: int) -> str:
        if isinstance(names, dict):
            value = names.get(class_id, names.get(str(class_id), f"class_{class_id}"))
        elif isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
            value = names[class_id]
        else:
            value = f"class_{class_id}"
        return str(value).strip().lower() or f"class_{class_id}"


__all__ = ["YoloDetection", "YoloInferenceAdapter"]
