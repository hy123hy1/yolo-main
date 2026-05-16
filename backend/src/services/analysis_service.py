import asyncio
import json
import threading
import time
from pathlib import Path

import cv2

from src.core.settings import settings
from src.services.event_service import EventService
from src.services.platform_storage import load_json, now_iso, save_json


class AnalysisService:
    def __init__(self):
        self.channels_dir: Path = settings.CHANNELS_DIR
        self.events_service = EventService()
        self._lock = threading.Lock()
        self._workers: dict[str, dict] = {}
        self._model_cache: dict[str, tuple[object, dict]] = {}

    def _get_channel_path(self, channel_id: str) -> Path:
        return self.channels_dir / f"{channel_id}.json"

    def _load_channel(self, channel_id: str) -> dict | None:
        path = self._get_channel_path(channel_id)
        if not path.exists():
            return None
        return load_json(path)

    def _save_channel(self, channel: dict) -> None:
        channel["updated_at"] = now_iso()
        save_json(self._get_channel_path(channel["id"]), channel)

    def _update_runtime(
        self,
        channel_id: str,
        *,
        status: str | None = None,
        last_error: str | None = None,
        last_event_at: str | None = None,
        last_analysis_at: str | None = None,
        started_at: str | None = None,
        stopped_at: str | None = None,
        frame_count: int | None = None,
        event_count: int | None = None,
        clear_error: bool = False,
    ) -> None:
        channel = self._load_channel(channel_id)
        if not channel:
            return

        runtime = channel.setdefault("ext", {}).setdefault("ai_runtime", {})
        if status is not None:
            runtime["status"] = status
        if clear_error:
            runtime["last_error"] = None
        elif last_error is not None:
            runtime["last_error"] = last_error
        if last_event_at is not None:
            runtime["last_event_at"] = last_event_at
        if last_analysis_at is not None:
            runtime["last_analysis_at"] = last_analysis_at
        if started_at is not None:
            runtime["started_at"] = started_at
        if stopped_at is not None:
            runtime["stopped_at"] = stopped_at
        if frame_count is not None:
            runtime["frame_count"] = frame_count
        if event_count is not None:
            runtime["event_count"] = event_count

        self._save_channel(channel)

    def _point_in_polygon(self, x: float, y: float, polygon: list[list[float]]) -> bool:
        if len(polygon) < 3:
            return False
        inside = False
        j = len(polygon) - 1
        for i in range(len(polygon)):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            intersects = ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
            )
            if intersects:
                inside = not inside
            j = i
        return inside

    def _filter_detections_by_zones(self, detections: list[dict], channel: dict) -> list[dict]:
        config = channel.get("ext", {}).get("ai_config", {})
        zone_ids = config.get("zone_ids") or []
        if not zone_ids:
            return detections

        zones = {
            zone.get("id"): zone
            for zone in channel.get("ext", {}).get("zones", [])
            if zone.get("id") in zone_ids
        }
        if not zones:
            return detections

        filtered: list[dict] = []
        for detection in detections:
            center_x = (detection["x1"] + detection["x2"]) / 2
            center_y = (detection["y1"] + detection["y2"]) / 2
            for zone_id, zone in zones.items():
                polygon = zone.get("coordinates") or []
                if self._point_in_polygon(center_x, center_y, polygon):
                    detection = {**detection, "zone_id": zone_id}
                    filtered.append(detection)
                    break
        return filtered

    def _filter_detections_by_labels(self, detections: list[dict], channel: dict) -> list[dict]:
        config = channel.get("ext", {}).get("ai_config", {})
        labels = config.get("labels") or []
        if not labels:
            return detections

        label_set = {str(label) for label in labels}
        return [
            detection
            for detection in detections
            if str(detection.get("class_name", "")) in label_set
        ]

    def _build_scoped_channel(self, channel: dict, model_config: dict) -> dict:
        return {
            **channel,
            "ext": {
                **channel.get("ext", {}),
                "ai_config": {
                    **channel.get("ext", {}).get("ai_config", {}),
                    "labels": model_config.get("labels") or [],
                    "zone_ids": model_config.get("zone_ids") or [],
                },
            },
        }

    # def _summarize_classes(self, detections: list[dict]) -> list[str]:
    #     return sorted({
    #         str(detection.get("class_name", ""))
    #         for detection in detections
    #         if detection.get("class_name") is not None
    #     })

    def _normalize_model_configs(self, channel: dict) -> list[dict]:
        ai_config = channel.get("ext", {}).get("ai_config", {}) or {}
        models = ai_config.get("models")
        normalized: list[dict] = []
        if isinstance(models, list) and models:
            for item in models:
                if not isinstance(item, dict):
                    continue
                model_id = item.get("model_id")
                if not model_id:
                    continue
                normalized.append({
                    "model_id": model_id,
                    "enabled": bool(item.get("enabled", True)),
                    "labels": list(item.get("labels") or []),
                    "conf": float(item.get("conf", ai_config.get("conf", 0.25)) or 0.25),
                    "frame_interval": max(int(item.get("frame_interval", ai_config.get("frame_interval", 10)) or 10), 1),
                    "zone_ids": list(item.get("zone_ids") or []),
                    "save_snapshot": bool(item.get("save_snapshot", ai_config.get("save_snapshot", True))),
                })
            return normalized

        model_id = ai_config.get("model_id")
        if model_id:
            normalized.append({
                "model_id": model_id,
                "enabled": bool(ai_config.get("enabled")),
                "labels": list(ai_config.get("labels") or []),
                "conf": float(ai_config.get("conf", 0.25) or 0.25),
                "frame_interval": max(int(ai_config.get("frame_interval", 10) or 10), 1),
                "zone_ids": list(ai_config.get("zone_ids") or []),
                "save_snapshot": bool(ai_config.get("save_snapshot", True)),
            })
        return normalized

    def _apply_model_scoped_filters(self, detections: list[dict], channel: dict, model_config: dict) -> list[dict]:
        scoped_channel = self._build_scoped_channel(channel, model_config)
        detections = self._filter_detections_by_labels(detections, scoped_channel)
        detections = self._filter_detections_by_zones(detections, scoped_channel)
        return detections

    def _load_model(self, model_id: str):
        cached = self._model_cache.get(model_id)
        if cached:
            return cached

        model_dir = settings.REGISTRY_DIR / model_id
        model_file = model_dir / "model.json"
        if not model_file.exists():
            raise ValueError(f"Model not found: {model_id}")

        model_meta = load_json(model_file)
        weights_path = model_meta.get("weights_path")
        if not weights_path:
            raise ValueError("Model weights_path not found in model.json")

        from ultralytics import YOLO

        model = YOLO(str(weights_path))
        self._model_cache[model_id] = (model, model_meta)
        return model, model_meta

    def _detect_frame(self, model, model_meta: dict, frame, conf_threshold: float) -> list[dict]:
        classes = model_meta.get("classes", [])
        results = model(frame, verbose=False, conf=conf_threshold)
        detections = []
        for result in results:
            boxes = result.boxes
            for i in range(len(boxes)):
                box = boxes[i]
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                class_id = int(box.cls[0])
                class_name = classes[class_id] if class_id < len(classes) else f"class_{class_id}"
                detections.append({
                    "class_id": class_id,
                    "class_name": class_name,
                    "conf": conf,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                })
        return detections

    def _save_event_snapshot(self, channel_id: str, frame) -> str:
        snapshots_dir = settings.SNAPSHOTS_DIR
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{channel_id}_evt_{int(time.time() * 1000)}.jpg"
        output_path = snapshots_dir / filename
        cv2.imwrite(str(output_path), frame)
        return str(output_path.resolve())

    def _event_cooldown_key(self, detection: dict) -> str:
        zone_id = detection.get("zone_id", "global")
        return f"{detection.get('class_name', 'unknown')}::{zone_id}"

    def _run_worker(self, channel_id: str, stop_event: threading.Event) -> None:
        frame_count = 0
        event_count = 0
        cooldown_cache: dict[str, float] = {}

        try:
            channel = self._load_channel(channel_id)
            if not channel:
                raise ValueError("Channel not found")

            ai_config = channel.get("ext", {}).get("ai_config", {})
            if not ai_config.get("enabled"):
                raise ValueError("AI is not enabled for this channel")

            model_configs = [
                item for item in self._normalize_model_configs(channel)
                if item.get("enabled") and item.get("model_id")
            ]
            if not model_configs:
                raise ValueError("No enabled AI model is configured for this channel")

            source_url = channel.get("source_url")
            if not source_url:
                raise ValueError("Channel source_url is empty")

            cap = cv2.VideoCapture(source_url)
            if not cap.isOpened():
                raise ValueError("Failed to open channel source for AI analysis")

            self._update_runtime(
                channel_id,
                status="running",
                started_at=now_iso(),
                clear_error=True,
                frame_count=0,
                event_count=0,
            )

            try:
                while not stop_event.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        time.sleep(0.2)
                        continue

                    frame_count += 1
                    self._update_runtime(
                        channel_id,
                        last_analysis_at=now_iso(),
                        frame_count=frame_count,
                        event_count=event_count,
                    )
                    runtime_by_model: dict[str, dict] = {}
                    for model_config in model_configs:
                        model_id = model_config["model_id"]
                        frame_interval = max(int(model_config.get("frame_interval", 10) or 10), 1)
                        if frame_count % frame_interval != 0:
                            continue

                        model, model_meta = self._load_model(model_id)
                        conf_threshold = float(model_config.get("conf", 0.25) or 0.25)
                        save_snapshot = bool(model_config.get("save_snapshot", True))
                        detections = self._detect_frame(model, model_meta, frame, conf_threshold)
                        detections = self._apply_model_scoped_filters(detections, channel, model_config)

                        runtime_by_model[model_id] = {
                            "last_analysis_at": now_iso(),
                            "detections": len(detections),
                        }

                        # 调试统计能力先保留实现痕迹，暂时停用
                        # scoped_channel = self._build_scoped_channel(channel, model_config)
                        # raw_detections = self._detect_frame(model, model_meta, frame, conf_threshold)
                        # label_filtered_detections = self._filter_detections_by_labels(raw_detections, scoped_channel)
                        # zone_filtered_detections = self._filter_detections_by_zones(label_filtered_detections, scoped_channel)
                        #
                        # model_runtime = {
                        #     "last_analysis_at": now_iso(),
                        #     "detections": len(zone_filtered_detections),
                        #     "raw_detections": len(raw_detections),
                        #     "label_filtered_detections": len(label_filtered_detections),
                        #     "zone_filtered_detections": len(zone_filtered_detections),
                        #     "cooldown_blocked_detections": 0,
                        #     "event_emitted_detections": 0,
                        #     "configured_labels": list(model_config.get("labels") or []),
                        #     "configured_zone_ids": list(model_config.get("zone_ids") or []),
                        #     "raw_classes": self._summarize_classes(raw_detections),
                        #     "label_filtered_classes": self._summarize_classes(label_filtered_detections),
                        #     "zone_filtered_classes": self._summarize_classes(zone_filtered_detections),
                        #     "debug_reason": "ready",
                        # }
                        #
                        # if not raw_detections:
                        #     model_runtime["debug_reason"] = "模型未检出目标"
                        # elif not label_filtered_detections:
                        #     model_runtime["debug_reason"] = "目标被类别过滤拦截"
                        # elif not zone_filtered_detections:
                        #     model_runtime["debug_reason"] = "目标被区域过滤拦截"
                        #
                        # runtime_by_model[model_id] = model_runtime

                        if not detections:
                            continue

                        for detection in detections:
                            cooldown_key = f"{model_id}::{self._event_cooldown_key(detection)}"
                            now_ts = time.time()
                            if now_ts - cooldown_cache.get(cooldown_key, 0) < 10:
                                # runtime_by_model[model_id]["cooldown_blocked_detections"] += 1
                                # runtime_by_model[model_id]["debug_reason"] = "命中冷却时间，事件被抑制"
                                continue
                            cooldown_cache[cooldown_key] = now_ts

                            snapshot_path = self._save_event_snapshot(channel_id, frame) if save_snapshot else None
                            payload = {
                                "channel_id": channel_id,
                                "model_id": model_id,
                                "event_type": "detection",
                                "class_name": detection.get("class_name", "unknown"),
                                "conf": detection.get("conf", 0),
                                "snapshot_path": snapshot_path,
                                "remark": json.dumps({
                                    **detection,
                                    "model_id": model_id,
                                }, ensure_ascii=False),
                                "status": "new",
                            }
                            asyncio.run(self.events_service.create_event(payload))
                            event_count += 1
                            # runtime_by_model[model_id]["event_emitted_detections"] += 1
                            # runtime_by_model[model_id]["debug_reason"] = "已触发事件"
                            runtime_by_model.setdefault(model_id, {})["last_event_at"] = now_iso()
                            self._update_runtime(
                                channel_id,
                                last_event_at=now_iso(),
                                frame_count=frame_count,
                                event_count=event_count,
                            )

                    if runtime_by_model:
                        channel = self._load_channel(channel_id)
                        if channel:
                            runtime = channel.setdefault("ext", {}).setdefault("ai_runtime", {})
                            runtime["models"] = {
                                **runtime.get("models", {}),
                                **runtime_by_model,
                            }
                            # previous_models = runtime.get("models", {})
                            # merged_models = {}
                            # for model_id, model_runtime in runtime_by_model.items():
                            #     previous = previous_models.get(model_id, {})
                            #     merged_models[model_id] = {
                            #         **previous,
                            #         **model_runtime,
                            #         "total_event_emitted_detections": int(previous.get("total_event_emitted_detections", 0))
                            #         + int(model_runtime.get("event_emitted_detections", 0)),
                            #         "total_cooldown_blocked_detections": int(previous.get("total_cooldown_blocked_detections", 0))
                            #         + int(model_runtime.get("cooldown_blocked_detections", 0)),
                            #     }
                            # runtime["models"] = {
                            #     **previous_models,
                            #     **merged_models,
                            # }
                            self._save_channel(channel)
            finally:
                cap.release()
        except Exception as exc:
            self._update_runtime(channel_id, status="error", last_error=str(exc), stopped_at=now_iso())
        finally:
            with self._lock:
                self._workers.pop(channel_id, None)
            channel = self._load_channel(channel_id)
            if channel and channel.get("ext", {}).get("ai_runtime", {}).get("status") != "error":
                self._update_runtime(
                    channel_id,
                    status="stopped",
                    stopped_at=now_iso(),
                    frame_count=frame_count,
                    event_count=event_count,
                )

    async def start_channel_analysis(self, channel_id: str) -> dict | None:
        channel = self._load_channel(channel_id)
        if not channel:
            return None

        with self._lock:
            existing = self._workers.get(channel_id)
            if existing and existing["thread"].is_alive():
                return {
                    "channel_id": channel_id,
                    "status": "running",
                    "message": "AI analysis is already running",
                }

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_worker,
                args=(channel_id, stop_event),
                daemon=True,
                name=f"analysis-{channel_id}",
            )
            self._workers[channel_id] = {
                "thread": thread,
                "stop_event": stop_event,
                "started_at": now_iso(),
            }
            thread.start()

        return {
            "channel_id": channel_id,
            "status": "starting",
            "started_at": self._workers[channel_id]["started_at"],
        }

    async def stop_channel_analysis(self, channel_id: str) -> dict | None:
        channel = self._load_channel(channel_id)
        if not channel:
            return None

        with self._lock:
            existing = self._workers.get(channel_id)
            if existing:
                existing["stop_event"].set()

        self._update_runtime(channel_id, status="stopping", stopped_at=now_iso())
        return {
            "channel_id": channel_id,
            "status": "stopping",
        }

    async def get_channel_analysis_status(self, channel_id: str) -> dict | None:
        channel = self._load_channel(channel_id)
        if not channel:
            return None

        runtime = channel.get("ext", {}).get("ai_runtime", {})
        with self._lock:
            worker = self._workers.get(channel_id)
            is_running = bool(worker and worker["thread"].is_alive())

        return {
            "channel_id": channel_id,
            "enabled": bool(channel.get("ext", {}).get("ai_config", {}).get("enabled")),
            "status": runtime.get("status", "running" if is_running else "stopped"),
            "is_running": is_running,
            "started_at": runtime.get("started_at"),
            "stopped_at": runtime.get("stopped_at"),
            "last_analysis_at": runtime.get("last_analysis_at"),
            "last_event_at": runtime.get("last_event_at"),
            "last_error": runtime.get("last_error"),
            "frame_count": runtime.get("frame_count", 0),
            "event_count": runtime.get("event_count", 0),
            "models": runtime.get("models", {}),
        }

    async def restore_enabled_channels(self) -> dict:
        restored = 0
        skipped = 0
        for path in self.channels_dir.glob("*.json"):
            try:
                channel = load_json(path)
            except Exception:
                skipped += 1
                continue

            ai_config = channel.get("ext", {}).get("ai_config", {})
            model_configs = [
                item for item in self._normalize_model_configs(channel)
                if item.get("enabled") and item.get("model_id")
            ]
            if not ai_config.get("enabled") or not model_configs:
                continue

            result = await self.start_channel_analysis(channel["id"])
            if result:
                restored += 1
            else:
                skipped += 1

        return {
            "restored": restored,
            "skipped": skipped,
        }


analysis_service = AnalysisService()
