import json
import shutil
import csv
import os
import asyncio
import tempfile
import zipfile
import io
from pathlib import Path
from fastapi import UploadFile
from src.core.settings import settings
from src.db.database import SessionLocal
from src.db.models import ModelMetaRecord, TaskAlgorithmMeta
from src.services.metadata_db_sync import delete_json_document
from src.services.platform_storage import format_datetime, generate_id, normalize_datetime_fields, now_iso, save_json as persist_json


SCENE_MAPPINGS = [
    {
        "scene_key": "person_intrusion",
        "scene_name": "人员闯入",
        "keywords": ["人员检测", "人员闯入", "闯入", "intrusion", "person"],
        "labels": ["person"],
        "rule_type": "detection",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": True,
        "priority": 100,
    },
    {
        "scene_key": "person_gathering",
        "scene_name": "人员聚集",
        "keywords": ["人员聚集", "聚集", "gather", "crowd"],
        "labels": ["person"],
        "rule_type": "gathering",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": True,
        "priority": 95,
    },
    {
        "scene_key": "person_absence",
        "scene_name": "人员离岗",
        "keywords": ["人员离岗", "离岗", "absence", "sleep on duty"],
        "labels": ["person"],
        "rule_type": "absence",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": True,
        "priority": 94,
    },
    {
        "scene_key": "person_count",
        "scene_name": "人员计数",
        "keywords": ["人员计数", "人数统计", "person count"],
        "labels": ["person"],
        "rule_type": "count",
        "default_need_overlay": False,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 92,
    },
    {
        "scene_key": "person_fall",
        "scene_name": "人员倒地",
        "keywords": ["人员倒地", "倒地", "fall"],
        "labels": ["person"],
        "rule_type": "fall_like",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 90,
    },
    {
        "scene_key": "vehicle_count",
        "scene_name": "车辆计数",
        "keywords": ["车辆检测", "车辆计数", "vehicle count", "car count"],
        "labels": ["car", "bus", "truck", "motorcycle", "bicycle"],
        "rule_type": "count",
        "default_need_overlay": False,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 88,
    },
    {
        "scene_key": "vehicle_congestion",
        "scene_name": "车辆拥堵识别",
        "keywords": ["车辆拥堵", "拥堵", "congestion", "traffic jam"],
        "labels": ["car", "bus", "truck"],
        "rule_type": "congestion",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": True,
        "priority": 87,
    },
    {
        "scene_key": "vehicle_loitering",
        "scene_name": "车辆滞留识别",
        "keywords": ["车辆滞留", "滞留", "loiter", "parking"],
        "labels": ["car", "bus", "truck"],
        "rule_type": "loitering",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": True,
        "priority": 86,
    },
    {
        "scene_key": "mining_truck_detect",
        "scene_name": "矿车识别",
        "keywords": ["矿车", "矿卡", "mining truck"],
        "labels": ["truck"],
        "rule_type": "detection",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 85,
    },
    {
        "scene_key": "helmet_detection",
        "scene_name": "安全帽检测",
        "keywords": ["安全帽", "helmet"],
        "labels": ["no_helmet"],
        "rule_type": "detection",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 100,
    },
    {
        "scene_key": "sleep_on_duty",
        "scene_name": "睡岗检测",
        "keywords": ["睡岗", "睡岗检测", "sleep"],
        "labels": ["sleep"],
        "rule_type": "detection",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": True,
        "priority": 96,
    },
    {
        "scene_key": "smoke_alarm",
        "scene_name": "烟雾报警",
        "keywords": ["烟雾", "smoke"],
        "labels": ["smoke"],
        "rule_type": "detection",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 97,
    },
    {
        "scene_key": "fire_alarm",
        "scene_name": "火情检测",
        "keywords": ["火情", "火焰", "火灾", "明火", "fire", "flame"],
        "labels": ["fire"],
        "rule_type": "detection",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 98,
    },
    {
        "scene_key": "puddle_detection",
        "scene_name": "路面积水识别",
        "keywords": ["路面积水", "积水", "puddle", "waterlogging"],
        "labels": ["puddle"],
        "rule_type": "detection",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 93,
    },
    {
        "scene_key": "reflective_vest_detection",
        "scene_name": "反光背心识别",
        "keywords": ["反光背心", "背心", "vest", "reflective vest"],
        "labels": ["no_vest"],
        "rule_type": "detection",
        "default_need_overlay": True,
        "default_need_snapshot": True,
        "default_need_video_clip": True,
        "requires_region": False,
        "priority": 93,
    },
]


def _load_json(path: Path):
    """同步加载 JSON 文件"""
    with open(path, "r", encoding="utf-8") as f:
        return normalize_datetime_fields(json.load(f))


def _save_json(path: Path, data: dict):
    """同步保存 JSON 文件"""
    persist_json(path, normalize_datetime_fields(data))


def _delete_directory(path: Path):
    """同步删除目录"""
    shutil.rmtree(path)


class ModelService:
    def __init__(self):
        self.registry_dir = settings.REGISTRY_DIR
        self.jobs_dir = settings.JOBS_DIR

    @staticmethod
    def _default_model_confidence() -> float:
        return 0.25

    @staticmethod
    def _default_model_alert_interval() -> int:
        return 60

    def _apply_default_runtime_thresholds(self, model_meta: dict) -> dict:
        normalized = dict(model_meta or {})
        normalized["confidence"] = float(
            normalized.get("confidence", self._default_model_confidence())
            or self._default_model_confidence()
        )
        normalized["alert_interval"] = int(
            normalized.get("alert_interval", self._default_model_alert_interval())
            or self._default_model_alert_interval()
        )
        return normalized

    def _normalize_labels(self, labels: list[str], classes: list[str]) -> list[str]:
        class_lookup = {str(item).strip() for item in classes if str(item).strip()}
        result: list[str] = []
        for label in labels:
            normalized = str(label).strip()
            if normalized and normalized in class_lookup and normalized not in result:
                result.append(normalized)
        return result

    def _build_scene_candidates(self, model_name: str, classes: list[str]) -> list[dict]:
        normalized_name = str(model_name or "").strip().lower()
        candidates: list[dict] = []
        seen_keys: set[str] = set()
        for mapping in SCENE_MAPPINGS:
            keywords = [str(keyword).strip().lower() for keyword in mapping.get("keywords", [])]
            if not any(keyword and keyword in normalized_name for keyword in keywords):
                continue
            labels = self._normalize_labels(mapping.get("labels", []), classes)
            if not labels:
                continue
            scene_key = mapping["scene_key"]
            if scene_key in seen_keys:
                continue
            seen_keys.add(scene_key)
            candidates.append(
                {
                    "scene_key": scene_key,
                    "scene_name": mapping["scene_name"],
                    "recommended_labels": labels,
                    "rule_type": mapping["rule_type"],
                    "default_need_overlay": bool(mapping["default_need_overlay"]),
                    "default_need_snapshot": bool(mapping["default_need_snapshot"]),
                    "default_need_video_clip": bool(mapping["default_need_video_clip"]),
                    "requires_region": bool(mapping["requires_region"]),
                    "priority": int(mapping["priority"]),
                    "source": "model_name_mapping",
                }
            )
        candidates.sort(key=lambda item: item["priority"], reverse=True)
        return candidates

    def _apply_label_recommendation(self, model_meta: dict) -> dict:
        normalized = dict(model_meta or {})
        classes = [str(item).strip() for item in list(normalized.get("classes") or []) if str(item).strip()]
        normalized["classes"] = classes
        candidates = self._build_scene_candidates(str(normalized.get("name") or ""), classes)

        if candidates:
            normalized["auto_label_strategy"] = "recommended"
            normalized["recommended_labels"] = list(candidates[0]["recommended_labels"])
            normalized["scene_candidates"] = candidates
            normalized["scene_key"] = candidates[0]["scene_key"]
            normalized["scene_name"] = candidates[0]["scene_name"]
            normalized["rule_type"] = candidates[0]["rule_type"]
            return normalized

        if len(classes) <= 5:
            normalized["auto_label_strategy"] = "all"
            normalized["recommended_labels"] = classes
            normalized["scene_candidates"] = []
        else:
            normalized["auto_label_strategy"] = "manual"
            normalized["recommended_labels"] = []
            normalized["scene_candidates"] = []
        return normalized

    def _extract_classes_from_yolo_weights(self, weights_path: str) -> list[str]:
        if Path(weights_path).suffix.lower() == ".om":
            return []
        try:
            from ultralytics import YOLO

            model = YOLO(weights_path)
            names = getattr(model.model, "names", None) or getattr(model, "names", None)
            if isinstance(names, dict):
                return [str(names[key]) for key in sorted(names.keys(), key=lambda x: int(x))]
            if isinstance(names, list):
                return [str(name) for name in names]
        except Exception as e:
            print(f"Error extracting classes from weights: {e}")
        return []

    def _build_model_from_metadata_row(self, row: ModelMetaRecord) -> dict:
        payload = dict(row.raw_json or {})
        payload["model_id"] = row.id
        payload["name"] = row.name
        payload["version"] = row.version
        payload["source"] = row.source
        payload["description"] = row.description
        payload["tags"] = list(row.tags_json or [])
        payload["classes"] = list(row.classes_json or [])
        payload["weights_path"] = row.weights_path
        payload["file_size"] = row.file_size
        payload["file_size_mb"] = row.file_size_mb
        payload["job_id"] = row.job_id
        payload["confidence"] = float(row.confidence or self._default_model_confidence())
        payload["alert_interval"] = int(row.alert_interval or self._default_model_alert_interval())
        payload["created_at"] = format_datetime(row.created_at)
        payload["updated_at"] = format_datetime(row.updated_at)
        return payload

    def _refresh_runtime_file_size(self, model_meta: dict) -> dict:
        weights_path = model_meta.get("weights_path")
        if weights_path and Path(weights_path).exists():
            model_meta["file_size"] = os.path.getsize(weights_path)
            model_meta["file_size_mb"] = round(model_meta["file_size"] / (1024 * 1024), 2)
        return model_meta

    def _update_model_row_from_payload(self, row: ModelMetaRecord, payload: dict) -> None:
        row.name = payload.get("name")
        row.version = payload.get("version")
        row.source = payload.get("source")
        row.description = payload.get("description")
        row.tags_json = list(payload.get("tags") or [])
        row.classes_json = list(payload.get("classes") or [])
        row.weights_path = payload.get("weights_path")
        row.file_size = payload.get("file_size")
        row.file_size_mb = payload.get("file_size_mb")
        row.job_id = payload.get("job_id")
        row.confidence = float(payload.get("confidence", self._default_model_confidence()) or self._default_model_confidence())
        row.alert_interval = int(payload.get("alert_interval", self._default_model_alert_interval()) or self._default_model_alert_interval())
        row.raw_json = dict(payload)
    
    async def list_models(self):
        """列出所有模型"""
        with SessionLocal() as session:
            rows = (
                session.query(ModelMetaRecord)
                .order_by(ModelMetaRecord.created_at.desc(), ModelMetaRecord.id.desc())
                .all()
            )

        if settings.METADATA_DB_ENABLED or rows:
            metadata_models = []
            for row in rows:
                model_meta = self._apply_label_recommendation(
                    self._apply_default_runtime_thresholds(self._build_model_from_metadata_row(row))
                )
                metadata_models.append(model_meta)
            return {"models": metadata_models}

        def _list_models_sync():
            models = []
            
            if not self.registry_dir.exists():
                return models
            
            for model_dir in self.registry_dir.iterdir():
                if model_dir.is_dir():
                    model_file = model_dir / "model.json"
                    if model_file.exists():
                        try:
                            model_meta = _load_json(model_file)
                            model_meta = self._apply_default_runtime_thresholds(model_meta)
                            model_meta = self._apply_label_recommendation(model_meta)
                            # 添加模型文件大小
                            weights_path = model_meta.get("weights_path")
                            if weights_path and Path(weights_path).exists():
                                model_meta["file_size"] = os.path.getsize(weights_path)
                                model_meta["file_size_mb"] = round(model_meta["file_size"] / (1024 * 1024), 2)
                            models.append(model_meta)
                        except Exception:
                            continue
            
            return models
        
        models = await asyncio.to_thread(_list_models_sync)
        return {"models": sorted(models, key=lambda x: x.get("created_at", ""), reverse=True)}

    async def get_model_summary(self, model_id: str):
        """鑾峰彇妯″瀷鐨勮交閲忔憳瑕侊紝涓嶅姞杞借缁冩寚鏍囧拰 YOLO 鍙傛暟淇℃伅"""
        model_dir = self.registry_dir / model_id
        model_file = model_dir / "model.json"

        with SessionLocal() as session:
            row = session.query(ModelMetaRecord).filter(ModelMetaRecord.id == model_id).one_or_none()

        if row:
            return self._apply_label_recommendation(
                self._apply_default_runtime_thresholds(self._build_model_from_metadata_row(row))
            )

        if settings.METADATA_DB_ENABLED:
            return None

        if not await asyncio.to_thread(lambda: model_file.exists()):
            return None

        model_meta = await asyncio.to_thread(_load_json, model_file)
        model_meta = self._apply_default_runtime_thresholds(model_meta)
        return self._apply_label_recommendation(model_meta)

    async def get_model(self, model_id: str):
        """获取模型详情（包含训练指标）"""
        model_dir = self.registry_dir / model_id
        model_file = model_dir / "model.json"
        
        model_meta = None
        with SessionLocal() as session:
            row = session.query(ModelMetaRecord).filter(ModelMetaRecord.id == model_id).one_or_none()
        if row:
            model_meta = self._apply_label_recommendation(
                self._apply_default_runtime_thresholds(self._build_model_from_metadata_row(row))
            )

        if model_meta is None:
            if settings.METADATA_DB_ENABLED:
                return None
            if not await asyncio.to_thread(lambda: model_file.exists()):
                return None
            model_meta = await asyncio.to_thread(_load_json, model_file)
            model_meta = self._apply_default_runtime_thresholds(model_meta)
            model_meta = self._apply_label_recommendation(model_meta)
        
        # 添加模型文件大小
        model_meta = await asyncio.to_thread(self._refresh_runtime_file_size, model_meta)
        
        # 尝试加载训练指标
        job_id = model_meta.get("job_id")
        if job_id:
            training_metrics = await self._load_training_metrics(job_id)
            if training_metrics:
                model_meta["training_metrics"] = training_metrics
        
        # 尝试获取模型参数信息
        model_info = await self._get_model_info(model_meta.get("weights_path"))
        if model_info:
            model_meta["model_info"] = model_info
        
        return model_meta
    
    async def _load_training_metrics(self, job_id: str):
        """从训练任务加载训练指标"""
        job_dir = self.jobs_dir / job_id
        
        if not await asyncio.to_thread(lambda: job_dir.exists()):
            return None
        
        def _load_metrics_sync():
            metrics = {}
            
            # 尝试读取 results.csv（YOLO 训练输出）
            # 查找 train 目录下的 results.csv
            results_files = list(job_dir.rglob("results.csv"))
            if results_files:
                results_csv = results_files[0]
                try:
                    metrics["training_history"] = self._parse_results_csv(results_csv)
                except Exception as e:
                    print(f"Error parsing results.csv: {e}")
            
            # 读取训练任务配置
            job_file = job_dir / "job.json"
            if job_file.exists():
                try:
                    job_meta = _load_json(job_file)
                    metrics["job_config"] = {
                        "dataset_id": job_meta.get("dataset_id"),
                        "epochs": job_meta.get("epochs"),
                        "imgsz": job_meta.get("imgsz"),
                        "batch": job_meta.get("batch"),
                        "model_name": job_meta.get("model_name"),
                        "status": job_meta.get("status"),
                        "created_at": job_meta.get("created_at"),
                        "completed_at": job_meta.get("completed_at")
                    }
                except Exception as e:
                    print(f"Error reading job.json: {e}")
            
            return metrics if metrics else None
        
        return await asyncio.to_thread(_load_metrics_sync)
    
    def _parse_results_csv(self, csv_path: Path):
        """解析 YOLO 训练输出的 results.csv（同步方法，在线程中调用）"""
        history = {
            "epochs": [],
            "train_box_loss": [],
            "train_cls_loss": [],
            "train_dfl_loss": [],
            "val_box_loss": [],
            "val_cls_loss": [],
            "val_dfl_loss": [],
            "metrics_precision": [],
            "metrics_recall": [],
            "metrics_mAP50": [],
            "metrics_mAP50_95": []
        }
        
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 清理列名（去除空格）
                    row = {k.strip(): v.strip() for k, v in row.items()}
                    
                    epoch = int(row.get("epoch", len(history["epochs"]) + 1))
                    history["epochs"].append(epoch)
                    
                    # 训练损失
                    if "train/box_loss" in row:
                        history["train_box_loss"].append(float(row["train/box_loss"]))
                    if "train/cls_loss" in row:
                        history["train_cls_loss"].append(float(row["train/cls_loss"]))
                    if "train/dfl_loss" in row:
                        history["train_dfl_loss"].append(float(row["train/dfl_loss"]))
                    
                    # 验证损失
                    if "val/box_loss" in row:
                        history["val_box_loss"].append(float(row["val/box_loss"]))
                    if "val/cls_loss" in row:
                        history["val_cls_loss"].append(float(row["val/cls_loss"]))
                    if "val/dfl_loss" in row:
                        history["val_dfl_loss"].append(float(row["val/dfl_loss"]))
                    
                    # 指标
                    if "metrics/precision(B)" in row:
                        history["metrics_precision"].append(float(row["metrics/precision(B)"]))
                    if "metrics/recall(B)" in row:
                        history["metrics_recall"].append(float(row["metrics/recall(B)"]))
                    if "metrics/mAP50(B)" in row:
                        history["metrics_mAP50"].append(float(row["metrics/mAP50(B)"]))
                    if "metrics/mAP50-95(B)" in row:
                        history["metrics_mAP50_95"].append(float(row["metrics/mAP50-95(B)"]))
            
            # 清理空列表
            history = {k: v for k, v in history.items() if v}
            
            # 添加最终指标摘要
            if history.get("metrics_mAP50"):
                history["final_metrics"] = {
                    "mAP50": history["metrics_mAP50"][-1] if history["metrics_mAP50"] else None,
                    "mAP50_95": history["metrics_mAP50_95"][-1] if history.get("metrics_mAP50_95") else None,
                    "precision": history["metrics_precision"][-1] if history.get("metrics_precision") else None,
                    "recall": history["metrics_recall"][-1] if history.get("metrics_recall") else None
                }
            
            return history
            
        except Exception as e:
            print(f"Error parsing CSV: {e}")
            return None
    
    async def _get_model_info(self, weights_path: str):
        """获取模型参数信息"""
        if not weights_path:
            return None
        
        if not await asyncio.to_thread(lambda: Path(weights_path).exists()):
            return None
        if Path(weights_path).suffix.lower() == ".om":
            return {"task": "detect", "model_type": "ascend_om", "hardware_validated": False}
        
        def _get_model_info_sync():
            try:
                from ultralytics import YOLO
                model = YOLO(weights_path)
                
                # 获取模型信息
                info = {
                    "task": model.task,
                    "model_type": model.model.yaml.get("yaml_file", "unknown") if hasattr(model.model, "yaml") else "unknown"
                }
                
                # 尝试获取参数数量
                if hasattr(model.model, "model"):
                    total_params = sum(p.numel() for p in model.model.model.parameters())
                    trainable_params = sum(p.numel() for p in model.model.model.parameters() if p.requires_grad)
                    info["total_params"] = total_params
                    info["trainable_params"] = trainable_params
                    info["total_params_m"] = round(total_params / 1e6, 2)  # 百万参数
                
                return info
                
            except Exception as e:
                print(f"Error getting model info: {e}")
                return None
        
        return await asyncio.to_thread(_get_model_info_sync)
    
    async def update_model(self, model_id: str, request):
        """更新模型信息"""
        model_dir = self.registry_dir / model_id
        model_file = model_dir / "model.json"
        
        def _update_model_sync():
            with SessionLocal() as session:
                row = session.query(ModelMetaRecord).filter(ModelMetaRecord.id == model_id).one_or_none()
                if row:
                    model_meta = self._apply_default_runtime_thresholds(self._build_model_from_metadata_row(row))
                    if request.name is not None:
                        model_meta["name"] = request.name
                    if getattr(request, "confidence", None) is not None:
                        model_meta["confidence"] = float(request.confidence)
                    if getattr(request, "alert_interval", None) is not None:
                        model_meta["alert_interval"] = int(request.alert_interval)
                    model_meta["updated_at"] = now_iso()
                    model_meta = self._apply_label_recommendation(model_meta)
                    self._update_model_row_from_payload(row, model_meta)
                    session.commit()
                    session.refresh(row)
                    updated = self._apply_label_recommendation(
                        self._apply_default_runtime_thresholds(self._build_model_from_metadata_row(row))
                    )
                    if model_file.exists():
                        _save_json(model_file, updated)
                    return updated

            if settings.METADATA_DB_ENABLED or not model_file.exists():
                return None

            model_meta = _load_json(model_file)
            model_meta = self._apply_default_runtime_thresholds(model_meta)
            
            # 更新字段
            if request.name is not None:
                model_meta["name"] = request.name
            if getattr(request, "confidence", None) is not None:
                model_meta["confidence"] = float(request.confidence)
            if getattr(request, "alert_interval", None) is not None:
                model_meta["alert_interval"] = int(request.alert_interval)
            model_meta["updated_at"] = now_iso()
            model_meta = self._apply_label_recommendation(model_meta)
            
            _save_json(model_file, model_meta)
            
            return model_meta
        
        return await asyncio.to_thread(_update_model_sync)
    
    async def delete_model(self, model_id: str):
        """删除模型"""
        model_dir = self.registry_dir / model_id
        model_file = model_dir / "model.json"
        
        def _delete_model_metadata_sync() -> bool | None:
            with SessionLocal() as session:
                row = session.query(ModelMetaRecord).filter(ModelMetaRecord.id == model_id).one_or_none()
                if not row:
                    return None
                used_count = session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.model_id == model_id).count()
                if used_count:
                    raise ValueError("Model is used by task algorithms")
                session.delete(row)
                session.commit()
                return True

        deleted_from_db = await asyncio.to_thread(_delete_model_metadata_sync)
        if deleted_from_db:
            if await asyncio.to_thread(lambda: model_dir.exists()):
                await asyncio.to_thread(_delete_directory, model_dir)
            return {"ok": True, "message": f"Model {model_id} deleted"}

        if settings.METADATA_DB_ENABLED:
            return None

        if not await asyncio.to_thread(lambda: model_dir.exists()):
            return None

        if await asyncio.to_thread(lambda: model_file.exists()):
            delete_json_document(model_file, model_id)
        
        # 删除模型目录
        await asyncio.to_thread(_delete_directory, model_dir)
        
        return {"ok": True, "message": f"Model {model_id} deleted"}
    
    async def upload_model(self, file: UploadFile):
        """上传已有模型（ZIP格式）"""
        # 验证文件格式
        if not file.filename.endswith('.zip'):
            raise ValueError("Only .zip files are allowed")
        
        # 读取ZIP内容
        content = await file.read()
        filename = file.filename or "model.zip"
        uploaded_name = Path(filename).stem
        
        # 验证ZIP文件大小（防止解压炸弹，限制10GB）
        if len(content) > 10 * 1024 * 1024 * 1024:
            raise ValueError("ZIP file too large (max 100MB)")
        
        def _process_zip(zip_filename: str):
            """处理ZIP文件的同步函数"""
            
            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # 验证ZIP文件
                try:
                    zip_file = zipfile.ZipFile(io.BytesIO(content), 'r')
                    zip_file.testzip()  # 验证ZIP完整性
                except zipfile.BadZipFile:
                    raise ValueError("Invalid or corrupted ZIP file")
                
                # 解压ZIP文件
                zip_file.extractall(temp_path)
                zip_file.close()
                
                # 查找model.json和weights目录
                model_json_path = None
                weights_dir_path = None
                
                # 查找model.json（可能在根目录或子目录中）
                for json_file in temp_path.rglob("model.json"):
                    model_json_path = json_file
                    break
                
                # 查找weights目录
                for weights_dir in temp_path.rglob("weights"):
                    if weights_dir.is_dir():
                        weights_dir_path = weights_dir
                        break
                
                # 验证必需的文件和目录
                if not weights_dir_path:
                    raise ValueError("ZIP file must contain a 'weights' directory")
                
                weight_files = sorted(item for item in weights_dir_path.iterdir()
                                      if item.is_file() and item.suffix.lower() in {".pt", ".om"})
                if not weight_files:
                    raise ValueError("weights directory must contain at least one .pt or .om file")
                if any(item.suffix.lower() == ".om" for item in weight_files) and len(weight_files) != 1:
                    raise ValueError("OM model package must contain exactly one .om weight and no .pt weights")
                
                # 读取或创建model.json
                if model_json_path and model_json_path.exists():
                    model_meta = _load_json(model_json_path)
                    # 如果ZIP中有model_id，检查是否已存在
                    original_model_id = model_meta.get("model_id")
                    if original_model_id:
                        # 检查model_id是否已存在
                        existing_model_dir = self.registry_dir / original_model_id
                        if existing_model_dir.exists():
                            # 生成新的model_id
                            model_id = generate_id("model")
                        else:
                            model_id = original_model_id
                    else:
                        model_id = generate_id("model")
                else:
                    # 创建新的model.json
                    model_id = generate_id("model")
                    model_meta = {
                        "model_id": model_id,
                        "name": zip_filename.replace('.zip', ''),
                        "created_at": now_iso(),
                        "source": "uploaded",
                        "classes": [],
                        "description": f"Uploaded model from {zip_filename}",
                        "tags": ["uploaded"]
                    }

                model_meta["name"] = uploaded_name
                if weight_files[0].suffix.lower() == ".om":
                    from src.inference.ascend_om import validate_om_metadata

                    model_meta["inference"] = validate_om_metadata(model_meta)
                    model_meta["model_format"] = "om"
                
                # 创建目标目录
                model_dir = self.registry_dir / model_id
                target_weights_dir = model_dir / "weights"
                model_dir.mkdir(parents=True, exist_ok=True)
                target_weights_dir.mkdir(parents=True, exist_ok=True)
                
                # 复制权重文件
                total_size = 0
                for weight_file in weight_files:
                    target_weight = target_weights_dir / weight_file.name
                    shutil.copy2(weight_file, target_weight)
                    total_size += weight_file.stat().st_size
                
                # 更新model.json
                model_meta["model_id"] = model_id
                model_meta["weights_path"] = str((target_weights_dir / weight_files[0].name).resolve())
                model_meta["file_size"] = total_size
                model_meta["file_size_mb"] = round(total_size / (1024 * 1024), 2)
                model_meta["confidence"] = float(
                    model_meta.get("confidence", self._default_model_confidence())
                    or self._default_model_confidence()
                )
                model_meta["alert_interval"] = int(
                    model_meta.get("alert_interval", self._default_model_alert_interval())
                    or self._default_model_alert_interval()
                )
                model_meta["created_at"] = now_iso()
                model_meta["updated_at"] = now_iso()

                if not model_meta.get("classes"):
                    extracted_classes = self._extract_classes_from_yolo_weights(model_meta["weights_path"])
                    if extracted_classes:
                        model_meta["classes"] = extracted_classes
                model_meta = self._apply_default_runtime_thresholds(model_meta)
                model_meta = self._apply_label_recommendation(model_meta)
                
                # 如果ZIP中有其他文件，也复制（除了model.json和weights）
                for item in temp_path.rglob("*"):
                    if item.is_file():
                        relative_path = item.relative_to(temp_path)
                        # 跳过model.json和weights目录中的文件（已处理）
                        if relative_path.name == "model.json" or "weights" in relative_path.parts:
                            continue
                        # 复制其他文件到model目录
                        target_file = model_dir / relative_path.name
                        if not target_file.exists():
                            shutil.copy2(item, target_file)
                
                # 保存model.json
                model_file = model_dir / "model.json"
                _save_json(model_file, model_meta)
                
                return model_meta
        
        model_meta = await asyncio.to_thread(_process_zip, filename)
        
        # 尝试获取模型信息
        weights_path = model_meta.get("weights_path")
        if weights_path:
            model_info = await self._get_model_info(weights_path)
            if model_info:
                model_meta["model_info"] = model_info
                # 更新model.json
                model_dir = self.registry_dir / model_meta["model_id"]
                model_file = model_dir / "model.json"
                await asyncio.to_thread(_save_json, model_file, model_meta)
        
        return model_meta
    
    async def export_model(self, model_id: str):
        """导出模型为ZIP文件"""
        model_dir = self.registry_dir / model_id
        model_file = model_dir / "model.json"
        
        if not await asyncio.to_thread(lambda: model_file.exists()):
            return None
        
        # 创建临时ZIP文件
        def _create_zip():
            # 创建临时文件
            temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            temp_zip_path = temp_zip.name
            temp_zip.close()
            
            with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # 添加model.json
                if model_file.exists():
                    zipf.write(model_file, f"{model_id}/model.json")
                
                # 添加weights目录
                weights_dir = model_dir / "weights"
                if weights_dir.exists():
                    for weight_file in weights_dir.iterdir():
                        if weight_file.is_file():
                            zipf.write(weight_file, f"{model_id}/weights/{weight_file.name}")
                
                # 添加其他可能的文件
                for item in model_dir.iterdir():
                    if item.is_file() and item.name != "model.json":
                        zipf.write(item, f"{model_id}/{item.name}")
            
            return temp_zip_path
        
        zip_path = await asyncio.to_thread(_create_zip)
        return zip_path
    
    async def generate_training_charts(self, model_id: str, chart_type: str = "all"):
        """生成训练图表
        
        Args:
            model_id: 模型ID
            chart_type: 图表类型 - "loss", "metrics", "all"
        
        Returns:
            图表文件路径
        """
        model_dir = self.registry_dir / model_id
        model_file = model_dir / "model.json"
        
        if not await asyncio.to_thread(lambda: model_file.exists()):
            return None
        
        model_meta = await asyncio.to_thread(_load_json, model_file)
        job_id = model_meta.get("job_id")
        
        if not job_id:
            raise ValueError("Model has no associated training job")
        
        # 查找results.csv
        job_dir = self.jobs_dir / job_id
        
        def _find_results_csv():
            results_files = list(job_dir.rglob("results.csv"))
            if results_files:
                return results_files[0]
            return None
        
        results_csv = await asyncio.to_thread(_find_results_csv)
        
        if not results_csv:
            raise ValueError("Training results.csv not found")
        
        # 解析CSV数据
        training_history = await asyncio.to_thread(self._parse_results_csv, results_csv)
        
        if not training_history:
            raise ValueError("Failed to parse training results")
        
        # 生成图表
        def _generate_charts():
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            # 设置中文字体支持（如果需要）
            plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False
            
            if chart_type == "loss" or chart_type == "all":
                # 生成损失曲线图
                fig, axes = plt.subplots(1, 3, figsize=(18, 5))
                
                epochs = training_history.get("epochs", [])
                
                # Box Loss
                if "train_box_loss" in training_history:
                    axes[0].plot(epochs, training_history["train_box_loss"], label='Train Box Loss', marker='o')
                if "val_box_loss" in training_history:
                    axes[0].plot(epochs, training_history["val_box_loss"], label='Val Box Loss', marker='s')
                axes[0].set_xlabel('Epoch')
                axes[0].set_ylabel('Loss')
                axes[0].set_title('Box Loss')
                axes[0].legend()
                axes[0].grid(True)
                
                # Class Loss
                if "train_cls_loss" in training_history:
                    axes[1].plot(epochs, training_history["train_cls_loss"], label='Train Cls Loss', marker='o')
                if "val_cls_loss" in training_history:
                    axes[1].plot(epochs, training_history["val_cls_loss"], label='Val Cls Loss', marker='s')
                axes[1].set_xlabel('Epoch')
                axes[1].set_ylabel('Loss')
                axes[1].set_title('Classification Loss')
                axes[1].legend()
                axes[1].grid(True)
                
                # DFL Loss
                if "train_dfl_loss" in training_history:
                    axes[2].plot(epochs, training_history["train_dfl_loss"], label='Train DFL Loss', marker='o')
                if "val_dfl_loss" in training_history:
                    axes[2].plot(epochs, training_history["val_dfl_loss"], label='Val DFL Loss', marker='s')
                axes[2].set_xlabel('Epoch')
                axes[2].set_ylabel('Loss')
                axes[2].set_title('DFL Loss')
                axes[2].legend()
                axes[2].grid(True)
                
                plt.tight_layout()
                
                loss_chart_path = tempfile.mktemp(suffix='_loss.png')
                plt.savefig(loss_chart_path, dpi=150, bbox_inches='tight')
                plt.close()
            
            if chart_type == "metrics" or chart_type == "all":
                # 生成指标曲线图
                fig, axes = plt.subplots(2, 2, figsize=(14, 10))
                
                epochs = training_history.get("epochs", [])
                
                # Precision
                if "metrics_precision" in training_history:
                    axes[0, 0].plot(epochs, training_history["metrics_precision"], label='Precision', marker='o', color='blue')
                    axes[0, 0].set_xlabel('Epoch')
                    axes[0, 0].set_ylabel('Precision')
                    axes[0, 0].set_title('Precision')
                    axes[0, 0].legend()
                    axes[0, 0].grid(True)
                
                # Recall
                if "metrics_recall" in training_history:
                    axes[0, 1].plot(epochs, training_history["metrics_recall"], label='Recall', marker='s', color='green')
                    axes[0, 1].set_xlabel('Epoch')
                    axes[0, 1].set_ylabel('Recall')
                    axes[0, 1].set_title('Recall')
                    axes[0, 1].legend()
                    axes[0, 1].grid(True)
                
                # mAP50
                if "metrics_mAP50" in training_history:
                    axes[1, 0].plot(epochs, training_history["metrics_mAP50"], label='mAP@0.5', marker='^', color='orange')
                    axes[1, 0].set_xlabel('Epoch')
                    axes[1, 0].set_ylabel('mAP@0.5')
                    axes[1, 0].set_title('mAP@0.5')
                    axes[1, 0].legend()
                    axes[1, 0].grid(True)
                
                # mAP50-95
                if "metrics_mAP50_95" in training_history:
                    axes[1, 1].plot(epochs, training_history["metrics_mAP50_95"], label='mAP@0.5:0.95', marker='d', color='red')
                    axes[1, 1].set_xlabel('Epoch')
                    axes[1, 1].set_ylabel('mAP@0.5:0.95')
                    axes[1, 1].set_title('mAP@0.5:0.95')
                    axes[1, 1].legend()
                    axes[1, 1].grid(True)
                
                plt.tight_layout()
                
                metrics_chart_path = tempfile.mktemp(suffix='_metrics.png')
                plt.savefig(metrics_chart_path, dpi=150, bbox_inches='tight')
                plt.close()
            
            if chart_type == "all":
                return {"loss_chart": loss_chart_path, "metrics_chart": metrics_chart_path}
            elif chart_type == "loss":
                return {"loss_chart": loss_chart_path}
            elif chart_type == "metrics":
                return {"metrics_chart": metrics_chart_path}
        
        chart_paths = await asyncio.to_thread(_generate_charts)
        return chart_paths
