"""Keep NVIDIA models unchanged; select OM execution without importing CUDA libraries."""
from pathlib import Path

import cv2
import numpy as np

from src.core.settings import settings
from .ascend_om import AscendOmDetector


def load_detection_model(weights_path, metadata):
    backend = settings.AI_INFERENCE_BACKEND
    if backend not in {"auto", "ultralytics", "ascend_om"}:
        raise ValueError("AI_INFERENCE_BACKEND must be auto, ultralytics or ascend_om")
    is_om = Path(weights_path).suffix.lower() == ".om"
    if backend == "ascend_om" and not is_om:
        raise ValueError("Ascend OM deployment requires .om weights; .pt weights cannot run on this backend")
    if is_om:
        if backend == "ultralytics":
            raise ValueError("OM weights require auto or ascend_om backend")
        return AscendOmDetector(weights_path, metadata, settings.ASCEND_DEVICE_ID)
    from ultralytics import YOLO

    return YOLO(str(weights_path))


def predict_detections(model, metadata, image, conf_threshold=0.25, device=None):
    if isinstance(model, AscendOmDetector):
        if isinstance(image, (str, Path)):
            image = cv2.imdecode(np.fromfile(str(image), dtype=np.uint8), cv2.IMREAD_COLOR)
        return model.detect(image, conf_threshold)
    kwargs = {"verbose": False, "conf": conf_threshold}
    if device:
        if isinstance(device, str) and device.startswith("cuda"):
            import torch

            # Avoid Ultralytics rewriting CUDA_VISIBLE_DEVICES and remapping to cuda:0.
            device = torch.device(device)
        kwargs["device"] = device
    detections = []
    for result in model(image, **kwargs) or ():
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        names = (metadata.get("classes") or getattr(result, "names", None)
                 or getattr(model, "names", None) or getattr(getattr(model, "model", None), "names", {}) or {})
        for index in range(len(boxes)):
            box = boxes[index]
            class_id = int(box.cls[0])
            name = names.get(class_id, names.get(str(class_id), f"class_{class_id}")) if isinstance(names, dict) else (
                names[class_id] if class_id < len(names) else f"class_{class_id}"
            )
            detections.append({"class_id": class_id, "class_name": str(name), "conf": float(box.conf[0]),
                               **dict(zip(("x1", "y1", "x2", "y2"), map(float, box.xyxy[0].tolist())))})
    return detections
