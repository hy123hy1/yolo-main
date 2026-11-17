"""Static YOLOv8/YOLO11 OM detection with host preprocessing and class-aware NMS."""
import logging
from pathlib import Path
import threading

import cv2
import numpy as np


logger = logging.getLogger("uvicorn.error")


def validate_om_metadata(metadata):
    classes = metadata.get("classes")
    if not isinstance(classes, list) or not classes or any(not isinstance(name, str) or not name.strip() for name in classes):
        raise ValueError("OM requires an ordered, non-empty classes list in model.json")
    config = metadata.get("inference", {})
    if not isinstance(config, dict):
        raise ValueError("model.json inference must be an object")
    layout = config.get("output_layout", "channels_first")
    if layout not in {"channels_first", "candidates_first"}:
        raise ValueError("OM supports raw YOLOv8/11 outputs only: channels_first or candidates_first; no embedded NMS")
    dtype = config.get("input_dtype", "float32")
    if dtype not in {"float32", "float16"}:
        raise ValueError("OM input_dtype must be float32 or float16; AIPP/quantized input is unsupported")
    iou = float(config.get("iou_threshold", 0.7))
    maximum = config.get("max_detections", 300)
    if not 0 < iou <= 1 or isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 3000:
        raise ValueError("OM requires 0 < iou_threshold <= 1 and integer max_detections in [1,3000]")
    return {"output_layout": layout, "input_dtype": dtype, "iou_threshold": iou, "max_detections": maximum}


class AscendOmDetector:
    def __init__(self, weights_path, metadata, device_id=0, session_factory=None):
        self.path = str(Path(weights_path).resolve())
        if not Path(self.path).is_file():
            raise ValueError(f"OM model not found: {self.path}")
        self.config = validate_om_metadata(metadata)
        self.names = list(metadata["classes"])
        self.device_id = int(device_id)
        if self.device_id < 0:
            raise ValueError("ASCEND_DEVICE_ID must be nonnegative")
        self._lock = threading.Lock()
        self._session = None
        if session_factory is None:
            try:
                from ais_bench.infer.interface import InferSession
            except ImportError as exc:
                raise RuntimeError("OM inference requires matching ais_bench, aclruntime and CANN on the Ascend host") from exc
            session_factory = InferSession
        try:
            self._session = session_factory(device_id=self.device_id, model_path=self.path)
            inputs, outputs = self._session.get_inputs(), self._session.get_outputs()
            if len(inputs) != 1 or len(outputs) != 1:
                raise ValueError("OM supports exactly one image input and one raw detection output")
            shape = tuple(int(value) for value in inputs[0].shape)
            if len(shape) != 4 or shape[:2] != (1, 3) or min(shape[2:]) <= 0:
                raise ValueError(f"OM requires static NCHW [1,3,H,W], got {shape}")
            self.height, self.width = shape[2:]
            self.dtype = np.dtype(self.config["input_dtype"])
            expected_bytes = int(np.prod(shape)) * self.dtype.itemsize
            if int(inputs[0].realsize) != expected_bytes:
                raise ValueError(f"OM input byte size does not match {self.dtype}; check input_dtype/AIPP")
            output_shape = tuple(int(value) for value in outputs[0].shape)
            feature_axis = 1 if self.config["output_layout"] == "channels_first" else 2
            if (len(output_shape) != 3 or output_shape[0] != 1 or min(output_shape) <= 0
                    or output_shape[feature_axis] != 4 + len(self.names)):
                raise ValueError(f"OM output {output_shape} is not raw YOLO xywh + {len(self.names)} class scores; check classes/output_layout/export")
            self.output_shape = output_shape
            logger.info("[AscendOM] loaded path=%s device=%s input=%s dtype=%s output=%s layout=%s",
                        self.path, self.device_id, shape, self.dtype, output_shape, self.config["output_layout"])
        except Exception as exc:
            self.close()
            raise RuntimeError(f"Failed to load OM {self.path} on NPU {self.device_id}: {exc}. Check target SoC, driver/CANN and runtime compatibility") from exc

    def _prepare(self, frame):
        if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            raise ValueError("OM expects a non-empty uint8 BGR image")
        height, width = frame.shape[:2]
        scale = min(self.width / width, self.height / height)
        resized_w, resized_h = max(1, round(width * scale)), max(1, round(height * scale))
        left = round((self.width - resized_w) / 2 - 0.1)
        top = round((self.height - resized_h) / 2 - 0.1)
        padded = np.full((self.height, self.width, 3), 114, dtype=np.uint8)
        padded[top:top + resized_h, left:left + resized_w] = cv2.resize(frame, (resized_w, resized_h))
        tensor = padded[:, :, ::-1].transpose(2, 0, 1)[None].astype(self.dtype) / self.dtype.type(255)
        return np.ascontiguousarray(tensor), scale, left, top

    def detect(self, frame, conf_threshold=0.25):
        threshold = float(conf_threshold)
        if not 0 <= threshold <= 1:
            raise ValueError("conf_threshold must be between 0 and 1")
        tensor, scale, left, top = self._prepare(frame)
        # Sessions are shared by camera threads. Bind the ACL context on the calling thread.
        with self._lock:
            if self._session is None:
                raise RuntimeError("OM session is closed")
            try:
                self._session.set_context()
                outputs = self._session.infer(feeds=[tensor], mode="static")
            except Exception as exc:
                raise RuntimeError(f"OM inference failed on NPU {self.device_id}: {exc}") from exc
        if len(outputs) != 1:
            raise RuntimeError("OM returned an unexpected number of outputs")
        raw = np.asarray(outputs[0])
        if raw.shape != self.output_shape or not np.isfinite(raw).all():
            raise RuntimeError(f"OM returned invalid output: shape={raw.shape}, expected={self.output_shape}")
        candidates = raw[0].T if self.config["output_layout"] == "channels_first" else raw[0]
        candidates = candidates.astype(np.float32, copy=False)
        scores = candidates[:, 4:]
        if np.any(scores < 0) or np.any(scores > 1):
            raise RuntimeError("OM class scores must be probabilities; logits/quantized outputs are unsupported")
        labels = scores.argmax(axis=1)
        confidences = scores[np.arange(len(scores)), labels]
        valid = (confidences >= threshold) & (candidates[:, 2] > 0) & (candidates[:, 3] > 0)
        candidates, labels, confidences = candidates[valid], labels[valid], confidences[valid]
        if not len(candidates):
            return []
        boxes = np.column_stack((candidates[:, :2] - candidates[:, 2:4] / 2,
                                 candidates[:, :2] + candidates[:, 2:4] / 2))
        # Suppress on model-space boxes, restore letterbox, then clip to the original image.
        kept = self._nms(boxes, confidences, labels)
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - left) / scale
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - top) / scale
        boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, frame.shape[1])
        boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, frame.shape[0])
        return [
            {"class_id": int(labels[i]), "class_name": self.names[int(labels[i])],
             "conf": float(confidences[i]), **dict(zip(("x1", "y1", "x2", "y2"), map(float, boxes[i])))}
            for i in kept if boxes[i, 2] > boxes[i, 0] and boxes[i, 3] > boxes[i, 1]
        ]

    def _nms(self, boxes, scores, labels):
        order = np.argsort(-scores, kind="stable")[:30000]
        kept = []
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        while order.size and len(kept) < self.config["max_detections"]:
            index, others = order[0], order[1:]
            kept.append(int(index))
            overlap = np.maximum(0, np.minimum(boxes[index, 2:], boxes[others, 2:]) - np.maximum(boxes[index, :2], boxes[others, :2]))
            intersection = overlap[:, 0] * overlap[:, 1]
            union = areas[index] + areas[others] - intersection
            iou = intersection / np.maximum(union, 1e-9)
            order = others[(labels[others] != labels[index]) | (iou <= self.config["iou_threshold"])]
        return kept

    def close(self):
        with self._lock:
            session, self._session = self._session, None
            if session is not None:
                try:
                    session.set_context()
                    session.free_resource()
                except Exception:
                    logger.warning("[AscendOM] failed to release model=%s", self.path, exc_info=True)

    def __del__(self):
        if hasattr(self, "_lock"):
            self.close()
