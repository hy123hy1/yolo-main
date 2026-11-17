"""Hardware-specific execution; business rules consume ordinary detection dictionaries."""

from .runtime import load_detection_model, predict_detections

__all__ = ["load_detection_model", "predict_detections"]
