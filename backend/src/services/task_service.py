from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.core.settings import settings
from src.db.database import SessionLocal
from src.db.models import AlgorithmRegionMeta, ModelMetaRecord, TaskAlgorithmMeta, TaskAlgorithmMetricMeta, TaskAlgorithmRegionMeta, TaskMeta, TaskRegionMeta
from src.services.analysis_service import analysis_service
from src.services.channel_service import ChannelService
from src.services import metadata_db_sync
from src.services.metadata_db_sync import delete_json_document
from src.services.metric_service import metric_service
from src.services.platform_storage import format_datetime, generate_id, list_json_items, load_json, now_iso, save_json
from src.services.model_service import ModelService
from src.services.wvp_service import WVPService

channel_service = ChannelService()
wvp_service = WVPService()
model_service = ModelService()
logger = logging.getLogger("uvicorn.error")


class TaskService:
    def __init__(self):
        self.tasks_dir: Path = settings.TASKS_DIR
        self._preview_warmup_jobs: dict[str, asyncio.Task] = {}
        self._preview_warmup_semaphore: asyncio.Semaphore | None = None
        self._preview_warmup_semaphore_limit: int | None = None
        self._source_refresh_jobs: dict[str, asyncio.Task] = {}

    @staticmethod
    def _extract_scalar_id(row: Any) -> str:
        if row is None:
            return ""
        if isinstance(row, str):
            return row.strip()
        try:
            value = row[0]
        except Exception:
            value = row
        return str(value or "").strip()

    def _get_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.json"

    def _save_task_cache(self, task: dict[str, Any]) -> None:
        save_json(self._get_path(task["id"]), task, sync_metadata=False)

    def _normalize_source_type(self, payload: dict[str, Any]) -> str:
        if payload.get("source_type"):
            return str(payload["source_type"]).lower()

        source_url = str(payload.get("source_url") or "")
        if source_url.startswith("rtsp://"):
            return "rtsp"
        if source_url.startswith("rtmp://"):
            return "rtmp"
        if source_url.startswith("http://") or source_url.startswith("https://"):
            return "http"
        return "unknown"

    async def _resolve_channel_for_task(
        self,
        channel_id: str | None,
        *,
        refresh_wvp_source: bool = True,
    ) -> dict[str, Any] | None:
        if not channel_id:
            return None
        try:
            channel = await channel_service.get_channel(channel_id)
        except FileNotFoundError:
            return None
        if not channel:
            return None
        upstream = ((channel.get("ext") or {}).get("upstream") or {})
        if refresh_wvp_source and upstream.get("platform") == "wvp":
            try:
                channel = await asyncio.to_thread(wvp_service.refresh_channel_source, channel)
            except Exception:
                pass
        return channel

    async def _hydrate_task_source_from_channel(
        self,
        payload: dict[str, Any],
        *,
        refresh_wvp_source: bool = True,
    ) -> dict[str, Any]:
        hydrated = dict(payload)
        channel_id = hydrated.get("channel_id")
        channel = await self._resolve_channel_for_task(
            channel_id,
            refresh_wvp_source=refresh_wvp_source,
        )
        if not channel:
            return hydrated

        channel_source_url = channel.get("source_url")
        if channel_source_url:
            hydrated["source_url"] = channel_source_url

        channel_source_type = self._normalize_source_type({
            "source_type": channel.get("source_type"),
            "source_url": channel_source_url,
        })
        if channel_source_type == "unknown":
            channel_source_type = channel.get("type") or "unknown"
        hydrated["source_type"] = channel_source_type
        hydrated["channel_name"] = channel.get("name") or hydrated.get("channel_name")
        return hydrated

    @staticmethod
    def _default_video_before_seconds() -> int:
        return max(int(getattr(settings, "ALARM_VIDEO_BEFORE_SECONDS", 10) or 10), 0)

    @staticmethod
    def _default_video_after_seconds() -> int:
        return max(int(getattr(settings, "ALARM_VIDEO_AFTER_SECONDS", 10) or 10), 0)

    @staticmethod
    def _default_true_bool(value: Any) -> bool:
        return True if value is None else bool(value)

    def _schedule_task_source_refresh(
        self,
        task_id: str,
        *,
        reason: str = "startup-restore",
        delay_seconds: float = 0.0,
    ) -> None:
        task_id = str(task_id or "").strip()
        if not task_id:
            return
        delay_seconds = max(float(delay_seconds or 0.0), 0.0)

        async def _runner() -> None:
            try:
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                task = await self.get_task(task_id, prefer_metadata=True)
                if not task or not task.get("channel_id"):
                    return

                refreshed_payload = await self._hydrate_task_source_from_channel(
                    task,
                    refresh_wvp_source=True,
                )
                if not refreshed_payload.get("source_url"):
                    return
                if (
                    refreshed_payload.get("source_url") == task.get("source_url")
                    and refreshed_payload.get("source_type") == task.get("source_type")
                ):
                    return

                normalized = self._normalize_task(refreshed_payload, current=task)
                normalized["id"] = task_id
                await self._persist_task(normalized)
                logger.info(
                    "[TaskSourceRefresh] task_id=%s reason=%s source_url_updated=true",
                    task_id,
                    reason,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[TaskSourceRefresh] task_id=%s reason=%s error=%s",
                    task_id,
                    reason,
                    exc,
                )
            finally:
                current = asyncio.current_task()
                if self._source_refresh_jobs.get(task_id) is current:
                    self._source_refresh_jobs.pop(task_id, None)

        try:
            existing = self._source_refresh_jobs.get(task_id)
            if existing and not existing.done():
                existing.cancel()
            self._source_refresh_jobs[task_id] = asyncio.create_task(_runner())
        except RuntimeError:
            logger.warning(
                "[TaskSourceRefresh] skipped task_id=%s reason=%s because no running loop",
                task_id,
                reason,
            )

    async def _resolve_browser_play_url_for_task(self, task: dict[str, Any], algorithms: list[dict[str, Any]] | None = None) -> dict[str, str]:
        channel_id = task.get("channel_id")
        if not channel_id:
            return {}

        try:
            fence_play = await channel_service.get_zone_preview_browser_play_url(channel_id)
            play_url = dict((fence_play or {}).get("play_url") or {})
            if play_url:
                return play_url
        except Exception:
            return {}

        return {}

    def _normalize_region(self, region: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": region.get("id") or generate_id("region"),
            "name": region.get("name") or "区域",
            "type": region.get("type") or "polygon",
            "color": region.get("color") or "#3b82f6",
            "coordinates": list(region.get("coordinates") or []),
            "enabled": bool(region.get("enabled", True)),
        }

    def _build_private_region_id(self, algorithm_id: str | None, region_id: str | None) -> str:
        if algorithm_id and region_id:
            return f"{algorithm_id}_{region_id}"[:64]
        return generate_id("region")

    def _normalize_algorithm_regions_with_private_ids(
        self,
        algorithm_id: str | None,
        regions: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        normalized_regions: list[dict[str, Any]] = []
        for region in list(regions or []):
            if not isinstance(region, dict):
                continue
            normalized = self._normalize_region(region)
            normalized["id"] = self._build_private_region_id(algorithm_id, normalized.get("id"))
            normalized_regions.append(normalized)
        return normalized_regions

    def _derive_task_regions_from_algorithms(self, algorithms: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        regions: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for algorithm in list(algorithms or []):
            if not isinstance(algorithm, dict):
                continue
            for region in list(algorithm.get("regions") or []):
                if not isinstance(region, dict):
                    continue
                normalized = self._normalize_region(region)
                region_id = normalized.get("id")
                if not region_id or region_id in seen_ids:
                    continue
                seen_ids.add(region_id)
                regions.append(normalized)
        return regions

    def _sync_runtime_models_with_algorithms(
        self,
        runtime: dict[str, Any] | None,
        algorithms: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        normalized_runtime = dict(runtime or {})
        runtime_models = normalized_runtime.get("models")
        if not isinstance(runtime_models, dict):
            if "models" in normalized_runtime:
                normalized_runtime["models"] = {}
            return normalized_runtime

        allowed_algorithm_ids = {
            str(algorithm.get("id") or "").strip()
            for algorithm in list(algorithms or [])
            if isinstance(algorithm, dict) and str(algorithm.get("id") or "").strip()
        }
        allowed_model_ids = {
            str(algorithm.get("model_id") or "").strip()
            for algorithm in list(algorithms or [])
            if isinstance(algorithm, dict) and str(algorithm.get("model_id") or "").strip()
        }

        filtered_models: dict[str, Any] = {}
        for key, value in runtime_models.items():
            runtime_key = str(key or "").strip()
            runtime_value = value if isinstance(value, dict) else {}
            runtime_algorithm_id = str(runtime_value.get("algorithm_id") or "").strip()
            runtime_model_id = str(runtime_value.get("model_id") or "").strip()
            if (
                runtime_key in allowed_algorithm_ids
                or runtime_key in allowed_model_ids
                or runtime_algorithm_id in allowed_algorithm_ids
                or runtime_model_id in allowed_model_ids
            ):
                filtered_models[runtime_key] = runtime_value

        normalized_runtime["models"] = filtered_models
        return normalized_runtime

    def _normalize_algorithm(self, algorithm: dict[str, Any], task_regions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        need_video_clip = self._default_true_bool(algorithm.get("need_video_clip"))
        need_snapshot = self._default_true_bool(algorithm.get("need_snapshot"))
        labels = list(algorithm.get("labels") or [])
        normalized_regions = self._normalize_algorithm_regions_with_private_ids(
            algorithm.get("id"),
            list(algorithm.get("regions") or []),
        )
        if not normalized_regions:
            region_ids = list(algorithm.get("region_ids") or [])
            if task_regions is not None:
                task_region_map = {
                    region.get("id"): self._normalize_region(region)
                    for region in task_regions
                    if isinstance(region, dict) and region.get("id")
                }
                normalized_regions = [
                    {
                        **task_region_map[region_id],
                        "id": self._build_private_region_id(algorithm.get("id"), region_id),
                    }
                    for region_id in region_ids
                    if region_id in task_region_map
                ]
            elif region_ids:
                normalized_regions = [
                    self._normalize_region({"id": region_id})
                    for region_id in region_ids
                    if region_id
                ]
        region_ids = [region.get("id") for region in normalized_regions if region.get("id")]

        detect_all_classes = algorithm.get("detect_all_classes")
        if detect_all_classes is None:
            detect_all_classes = len(labels) == 0
        scene_key = algorithm.get("scene_key") or None
        scene_name = algorithm.get("scene_name") or None
        rule_type = algorithm.get("rule_type") or algorithm.get("algorithm_type") or "detection"
        label_source = algorithm.get("label_source") or ("all" if detect_all_classes else ("recommended" if scene_key else "manual"))
        requires_region = bool(algorithm.get("requires_region", algorithm.get("need_overlay", False)))

        return {
            "id": algorithm.get("id") or generate_id("algo"),
            "algorithm_type": algorithm.get("algorithm_type") or "detection",
            "scene_key": scene_key,
            "scene_name": scene_name,
            "rule_type": rule_type,
            "model_id": algorithm.get("model_id"),
            "model_name": algorithm.get("model_name"),
            "enabled": bool(algorithm.get("enabled", True)),
            "labels": labels,
            "detect_all_classes": bool(detect_all_classes),
            "label_source": label_source,
            "confidence": float(algorithm.get("confidence", algorithm.get("conf", 0.25)) or 0.25),
            "alert_interval": max(int(algorithm.get("alert_interval", 60) or 60), 0),
            "need_snapshot": need_snapshot,
            "need_video_clip": need_video_clip,
            "video_before_seconds": self._default_video_before_seconds() if need_video_clip else 0,
            "video_after_seconds": self._default_video_after_seconds() if need_video_clip else 0,
            "need_overlay": bool(algorithm.get("need_overlay", False)),
            "requires_region": requires_region,
            "overlay_color": algorithm.get("overlay_color") or "#ff4d4f",
            "postprocess_config": dict(algorithm.get("postprocess_config") or {}),
            "region_ids": region_ids,
            "regions": normalized_regions,
        }

    async def _apply_model_scene_defaults(self, payload: dict[str, Any]) -> dict[str, Any]:
        model_id = str(payload.get("model_id") or "").strip()
        if not model_id:
            return payload

        try:
            model_meta = await model_service.get_model_summary(model_id)
        except Exception:
            logger.exception("[TaskAlgorithm] failed to load model defaults model_id=%s", model_id)
            return payload

        enriched = dict(payload)
        if model_meta and model_meta.get("name"):
            enriched.setdefault("model_name", model_meta.get("name"))
        if model_meta:
            enriched.setdefault("confidence", float(model_meta.get("confidence", 0.25) or 0.25))
            enriched.setdefault("alert_interval", int(model_meta.get("alert_interval", 60) or 60))
        if enriched.get("scene_name") and enriched.get("scene_key") and enriched.get("rule_type") and enriched.get("model_name"):
            return enriched

        scene_candidates = list((model_meta or {}).get("scene_candidates") or [])
        if not scene_candidates:
            return enriched

        scene = scene_candidates[0]
        enriched.setdefault("scene_key", scene.get("scene_key"))
        enriched.setdefault("scene_name", scene.get("scene_name"))
        enriched.setdefault("rule_type", scene.get("rule_type") or enriched.get("algorithm_type") or "detection")
        if not enriched.get("labels") and scene.get("recommended_labels"):
            enriched["labels"] = list(scene.get("recommended_labels") or [])
        enriched.setdefault("label_source", "recommended")
        enriched.setdefault("need_snapshot", self._default_true_bool(scene.get("default_need_snapshot")))
        enriched.setdefault("need_video_clip", self._default_true_bool(scene.get("default_need_video_clip")))
        enriched.setdefault("requires_region", bool(scene.get("requires_region", False)))
        return enriched

    def _normalize_regions(self, regions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in list(regions or []):
            if not isinstance(item, dict):
                continue
            region = self._normalize_region(item)
            region_id = region.get("id")
            if region_id in seen_ids:
                continue
            seen_ids.add(region_id)
            normalized.append(region)
        return normalized

    def _upgrade_legacy_task_regions(self, task: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        algorithms = list(task.get("algorithms") or [])
        task_regions = self._normalize_regions(task.get("regions") or [])
        upgraded_algorithms: list[dict[str, Any]] = []
        for algorithm in algorithms:
            if not isinstance(algorithm, dict):
                continue
            upgraded_algorithms.append(self._normalize_algorithm(algorithm, task_regions))
        return self._derive_task_regions_from_algorithms(upgraded_algorithms), upgraded_algorithms

    def _has_runnable_algorithm(self, task: dict[str, Any]) -> bool:
        for algorithm in list(task.get("algorithms") or []):
            if not isinstance(algorithm, dict):
                continue
            if algorithm.get("enabled") and algorithm.get("model_id"):
                return True
        return False

    def _has_overlay_algorithm(self, task: dict[str, Any]) -> bool:
        for algorithm in list(task.get("algorithms") or []):
            if not isinstance(algorithm, dict):
                continue
            if (
                algorithm.get("enabled")
                and algorithm.get("model_id")
                and len(list(algorithm.get("regions") or [])) > 0
            ):
                return True
        return False

    def _get_algorithms_missing_regions(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        missing: list[dict[str, Any]] = []
        for algorithm in list(task.get("algorithms") or []):
            if not isinstance(algorithm, dict):
                continue
            if not (algorithm.get("enabled") and algorithm.get("model_id")):
                continue
            if not (algorithm.get("need_overlay") or algorithm.get("requires_region")):
                continue
            region_ids = list(algorithm.get("region_ids") or [])
            if len(region_ids) == 0:
                missing.append(algorithm)
        return missing

    async def _find_running_task_on_channel(self, channel_id: str | None, exclude_task_id: str | None = None) -> dict[str, Any] | None:
        if not channel_id:
            return None

        with SessionLocal() as session:
            task_rows = (
                session.query(TaskMeta.id)
                .filter(TaskMeta.channel_id == channel_id)
                .order_by(TaskMeta.created_at.desc(), TaskMeta.id.desc())
                .all()
            )

        for row in task_rows:
            task_id = str(row[0] or "").strip() if row else ""
            if not task_id or task_id == exclude_task_id:
                continue
            metadata_task = await self.get_task_from_metadata(task_id)
            if metadata_task and self._is_task_running(metadata_task):
                return metadata_task

        if not settings.METADATA_DB_ENABLED:
            items = list_json_items(self.tasks_dir)
            for raw_task in items:
                if not isinstance(raw_task, dict):
                    continue
                task_id = raw_task.get("id")
                if not task_id or task_id == exclude_task_id:
                    continue
                if raw_task.get("channel_id") != channel_id:
                    continue
                runtime = raw_task.get("runtime") or {}
                status = str(runtime.get("status") or raw_task.get("status") or "").lower()
                is_running = bool(runtime.get("is_running")) or status in {"running", "starting", "restarting"}
                if is_running:
                    return raw_task
        return None

    def _is_task_running(self, task: dict[str, Any]) -> bool:
        runtime = task.get("runtime") or {}
        status = str(runtime.get("status") or task.get("status") or "").lower()
        return bool(runtime.get("is_running")) or status in {"running", "starting", "restarting"}

    async def _load_task_for_write(self, task_id: str) -> tuple[dict[str, Any] | None, Path]:
        path = self._get_path(task_id)
        metadata_task = await self.get_task_from_metadata(task_id)
        if not metadata_task:
            if not settings.METADATA_DB_ENABLED and path.exists():
                return load_json(path), path
            return None, path

        self._save_task_cache(metadata_task)
        return metadata_task, path

    def _build_task_meta_row(self, task: dict[str, Any]) -> TaskMeta:
        runtime = dict(task.get("runtime") or {})
        algorithms = list(task.get("algorithms") or [])
        regions = list(task.get("regions") or self._derive_task_regions_from_algorithms(algorithms))
        return TaskMeta(
            id=task["id"],
            name=task.get("name"),
            source_url=task.get("source_url"),
            source_type=task.get("source_type"),
            channel_id=task.get("channel_id"),
            channel_name=task.get("channel_name"),
            frame_interval=int(task.get("frame_interval", 10) or 10),
            status=runtime.get("status") or task.get("status") or "stopped",
            algorithm_count=len(algorithms),
            region_count=len(regions),
            is_running=bool(runtime.get("is_running")),
            started_at=metadata_db_sync._parse_dt(runtime.get("started_at")),
            stopped_at=metadata_db_sync._parse_dt(runtime.get("stopped_at")),
            last_analysis_at=metadata_db_sync._parse_dt(runtime.get("last_analysis_at")),
            last_event_at=metadata_db_sync._parse_dt(runtime.get("last_event_at")),
            last_error=runtime.get("last_error"),
            created_at=metadata_db_sync._parse_dt(task.get("created_at")),
            updated_at=metadata_db_sync._parse_dt(task.get("updated_at")),
            raw_json=dict(task),
        )

    def _save_task_to_metadata(self, task: dict[str, Any]) -> None:
        with SessionLocal() as session:
            session.merge(self._build_task_meta_row(task))
            session.query(TaskAlgorithmMetricMeta).filter(TaskAlgorithmMetricMeta.task_id == task["id"]).delete()
            session.query(AlgorithmRegionMeta).filter(AlgorithmRegionMeta.task_id == task["id"]).delete()
            session.query(TaskAlgorithmRegionMeta).filter(TaskAlgorithmRegionMeta.task_id == task["id"]).delete()
            session.query(TaskAlgorithmMeta).filter(TaskAlgorithmMeta.task_id == task["id"]).delete()
            session.query(TaskRegionMeta).filter(TaskRegionMeta.task_id == task["id"]).delete()

            algorithms = list(task.get("algorithms") or [])
            regions = list(task.get("regions") or self._derive_task_regions_from_algorithms(algorithms))

            for index, algorithm in enumerate(algorithms):
                session.add(
                    TaskAlgorithmMeta(
                        id=algorithm.get("id"),
                        task_id=task["id"],
                        algorithm_type=algorithm.get("algorithm_type"),
                        scene_key=algorithm.get("scene_key"),
                        scene_name=algorithm.get("scene_name"),
                        rule_type=algorithm.get("rule_type"),
                        model_id=algorithm.get("model_id"),
                        enabled=bool(algorithm.get("enabled", True)),
                        labels_json=list(algorithm.get("labels") or []),
                        detect_all_classes=bool(algorithm.get("detect_all_classes", False)),
                        label_source=algorithm.get("label_source"),
                        confidence=float(algorithm.get("confidence", 0.25) or 0.25),
                        alert_interval=int(algorithm.get("alert_interval", 60) or 60),
                        need_snapshot=bool(algorithm.get("need_snapshot", True)),
                        need_video_clip=bool(algorithm.get("need_video_clip", True)),
                        video_before_seconds=int(algorithm.get("video_before_seconds", 0) or 0),
                        video_after_seconds=int(algorithm.get("video_after_seconds", 0) or 0),
                        need_overlay=bool(algorithm.get("need_overlay", False)),
                        requires_region=bool(algorithm.get("requires_region", False)),
                        overlay_color=algorithm.get("overlay_color"),
                        postprocess_config_json=dict(algorithm.get("postprocess_config") or {}),
                        sort_order=index,
                        created_at=metadata_db_sync._parse_dt(algorithm.get("created_at")) or metadata_db_sync._parse_dt(task.get("created_at")),
                        updated_at=metadata_db_sync._parse_dt(task.get("updated_at")),
                        raw_json=dict(algorithm),
                    )
                )

            session.flush()

            for algorithm in algorithms:
                for region in list(algorithm.get("regions") or []):
                    session.add(
                        AlgorithmRegionMeta(
                            id=region.get("id"),
                            task_id=task["id"],
                            algorithm_id=algorithm.get("id"),
                            name=region.get("name"),
                            type=region.get("type"),
                            color=region.get("color"),
                            coordinates_json=list(region.get("coordinates") or []),
                            enabled=bool(region.get("enabled", True)),
                            created_at=metadata_db_sync._parse_dt(region.get("created_at")) or metadata_db_sync._parse_dt(task.get("created_at")),
                            updated_at=metadata_db_sync._parse_dt(task.get("updated_at")),
                            raw_json=dict(region),
                        )
                    )

            for region in regions:
                session.add(
                    TaskRegionMeta(
                        id=region.get("id"),
                        task_id=task["id"],
                        name=region.get("name"),
                        type=region.get("type"),
                        color=region.get("color"),
                        coordinates_json=list(region.get("coordinates") or []),
                        enabled=bool(region.get("enabled", True)),
                        created_at=metadata_db_sync._parse_dt(region.get("created_at")) or metadata_db_sync._parse_dt(task.get("created_at")),
                        updated_at=metadata_db_sync._parse_dt(task.get("updated_at")),
                        raw_json=dict(region),
                    )
                )

            session.flush()

            for algorithm in algorithms:
                for link_index, region in enumerate(list(algorithm.get("regions") or []), start=1):
                    region_id = region.get("id")
                    algorithm_id = algorithm.get("id")
                    if not region_id or not algorithm_id:
                        continue
                    session.add(
                        TaskAlgorithmRegionMeta(
                            id=f"{task['id']}_{algorithm_id}_{region_id}_{link_index}",
                            task_id=task["id"],
                            algorithm_id=algorithm_id,
                            region_id=region_id,
                            created_at=metadata_db_sync._parse_dt(task.get("updated_at")) or metadata_db_sync._parse_dt(task.get("created_at")),
                        )
                    )

            session.commit()

    def _attach_model_names_to_task(self, task: dict[str, Any]) -> dict[str, Any]:
        model_ids = sorted(
            {
                str(algorithm.get("model_id") or "").strip()
                for algorithm in list(task.get("algorithms") or [])
                if isinstance(algorithm, dict) and str(algorithm.get("model_id") or "").strip()
            }
        )
        if not model_ids:
            return task

        with SessionLocal() as session:
            rows = (
                session.query(ModelMetaRecord.id, ModelMetaRecord.name)
                .filter(ModelMetaRecord.id.in_(model_ids))
                .all()
            )

        model_names_by_id = {
            str(row[0]): row[1]
            for row in rows
            if row and row[0]
        }

        for algorithm in list(task.get("algorithms") or []):
            if not isinstance(algorithm, dict):
                continue
            model_id = str(algorithm.get("model_id") or "").strip()
            if model_id and model_id in model_names_by_id:
                algorithm["model_name"] = model_names_by_id[model_id]

        return task

    def _build_fast_task_response(self, task: dict[str, Any]) -> dict[str, Any]:
        response_task = deepcopy(task)
        return self._attach_model_names_to_task(response_task)

    async def _persist_task(self, task: dict[str, Any], *, reload_metadata: bool = True) -> dict[str, Any]:
        task["runtime"] = self._sync_runtime_models_with_algorithms(
            task.get("runtime"),
            task.get("algorithms"),
        )
        self._save_task_to_metadata(task)
        if not reload_metadata:
            fast_task = self._build_fast_task_response(task)
            self._save_task_cache(fast_task)
            return fast_task
        metadata_task = await self.get_task_from_metadata(task["id"])
        if metadata_task:
            self._save_task_cache(metadata_task)
            return metadata_task
        self._save_task_cache(task)
        return task

    async def _find_any_task_on_channel(self, channel_id: str | None, exclude_task_id: str | None = None) -> dict[str, Any] | None:
        if not channel_id:
            return None

        with SessionLocal() as session:
            task_rows = (
                session.query(TaskMeta.id)
                .filter(TaskMeta.channel_id == channel_id)
                .order_by(TaskMeta.created_at.desc(), TaskMeta.id.desc())
                .all()
            )

        for row in task_rows:
            task_id = str(row[0] or "").strip() if row else ""
            if not task_id or task_id == exclude_task_id:
                continue
            metadata_task = await self.get_task_from_metadata(task_id)
            if metadata_task:
                return metadata_task

        if not settings.METADATA_DB_ENABLED:
            items = list_json_items(self.tasks_dir)
            for raw_task in items:
                if not isinstance(raw_task, dict):
                    continue
                task_id = raw_task.get("id")
                if not task_id or task_id == exclude_task_id:
                    continue
                if raw_task.get("channel_id") == channel_id:
                    return raw_task
        return None

    def _collect_overlay_regions(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for algorithm in list(task.get("algorithms") or []):
            if not isinstance(algorithm, dict):
                continue
            if not (algorithm.get("enabled") and algorithm.get("model_id")):
                continue

            algorithm_regions = list(algorithm.get("regions") or [])
            if len(algorithm_regions) == 0:
                continue

            for region in algorithm_regions:
                if not isinstance(region, dict):
                    continue
                normalized = self._normalize_region(region)
                region_id = normalized.get("id")
                if not region_id or region_id in seen_ids:
                    continue
                collected.append(normalized)
                seen_ids.add(region_id)

        return collected

    def _should_block_on_preview_warmup(self, task: dict[str, Any]) -> bool:
        return len(self._collect_overlay_regions(task)) > 0

    async def _warmup_task_preview(self, task: dict[str, Any]) -> dict[str, Any] | None:
        channel_id = task.get("channel_id")
        if not channel_id:
            return None

        await channel_service.play(channel_id)

        overlay_regions = self._collect_overlay_regions(task)
        if overlay_regions:
            result = await channel_service.start_zone_preview(
                channel_id,
                zones=overlay_regions,
                zone_source="task",
                owner_task_id=task.get("id"),
            )
            play_url = await channel_service.refresh_zone_preview_cached_urls(channel_id) or {}
            await channel_service.set_task_preview_state(
                channel_id,
                mode="zone",
                owner_task_id=task.get("id"),
                play_url=play_url,
                status="running",
            )
            return {
                **(result or {}),
                "play_url": play_url,
                "resolved_mode": "zone",
            }

        await channel_service.stop_zone_preview(channel_id)
        cached_urls = await channel_service.refresh_channel_browser_cached_urls(channel_id)
        await channel_service.set_task_preview_state(
            channel_id,
            mode="channel",
            owner_task_id=task.get("id"),
            play_url=cached_urls or {},
            status="running",
        )
        return {
            "channel_id": channel_id,
            "status": "running",
            "play_url": cached_urls or {},
            "resolved_mode": "channel",
        }

    def _get_preview_warmup_semaphore(self) -> asyncio.Semaphore:
        limit = max(int(getattr(settings, "TASK_PREVIEW_WARMUP_MAX_CONCURRENCY", 1) or 1), 1)
        if self._preview_warmup_semaphore is None or self._preview_warmup_semaphore_limit != limit:
            self._preview_warmup_semaphore = asyncio.Semaphore(limit)
            self._preview_warmup_semaphore_limit = limit
        return self._preview_warmup_semaphore

    @staticmethod
    def _preview_warmup_key(task: dict[str, Any]) -> str:
        return str(task.get("channel_id") or task.get("id") or "")

    def _schedule_task_preview_warmup(
        self,
        task: dict[str, Any],
        *,
        delay_seconds: float = 0.0,
        reason: str = "task-start",
    ) -> None:
        task_id = str(task.get("id") or "").strip()
        channel_id = str(task.get("channel_id") or "").strip()
        warmup_key = self._preview_warmup_key(task)
        delay_seconds = max(float(delay_seconds or 0), 0.0)

        async def _runner() -> None:
            try:
                logger.info(
                    "[TaskPreviewWarmup] scheduled task_id=%s channel_id=%s reason=%s delay_seconds=%s",
                    task_id,
                    channel_id,
                    reason,
                    round(delay_seconds, 2),
                )
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                async with self._get_preview_warmup_semaphore():
                    logger.info(
                        "[TaskPreviewWarmup] start task_id=%s channel_id=%s reason=%s",
                        task_id,
                        channel_id,
                        reason,
                    )
                    await self._warmup_task_preview(task)
            except asyncio.CancelledError:
                logger.info(
                    "[TaskPreviewWarmup] cancelled task_id=%s channel_id=%s reason=%s",
                    task_id,
                    channel_id,
                    reason,
                )
                raise
            except Exception as exc:
                logger.warning(
                    "[TaskPreviewWarmup] task_id=%s channel_id=%s reason=%s error=%s",
                    task_id,
                    channel_id,
                    reason,
                    exc,
                )
            finally:
                current = asyncio.current_task()
                if self._preview_warmup_jobs.get(warmup_key) is current:
                    self._preview_warmup_jobs.pop(warmup_key, None)

        try:
            existing = self._preview_warmup_jobs.get(warmup_key)
            if existing and not existing.done():
                existing.cancel()
            scheduled = asyncio.create_task(_runner())
            if warmup_key:
                self._preview_warmup_jobs[warmup_key] = scheduled
        except RuntimeError:
            logger.warning(
                "[TaskPreviewWarmup] skipped task_id=%s channel_id=%s reason=%s because no running loop",
                task_id,
                channel_id,
                reason,
            )

    def _normalize_task(self, payload: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
        current = current or {}
        created_at = current.get("created_at") or now_iso()
        runtime = current.get("runtime") or {}

        regions_payload = payload.get("regions")
        if regions_payload is None:
            regions_payload = current.get("regions") or []

        algorithms_payload = payload.get("algorithms")
        if algorithms_payload is None:
            algorithms_payload = current.get("algorithms") or []

        task_regions = self._normalize_regions(regions_payload)
        normalized_algorithms = [
            self._normalize_algorithm(item, task_regions)
            for item in list(algorithms_payload or [])
            if isinstance(item, dict)
        ]
        derived_regions = self._derive_task_regions_from_algorithms(normalized_algorithms)

        task = {
            "id": current.get("id") or payload.get("id") or generate_id("task"),
            "name": payload.get("name", current.get("name") or "未命名任务"),
            "source_url": payload.get("source_url", current.get("source_url")),
            "source_type": self._normalize_source_type({
                "source_type": payload.get("source_type", current.get("source_type")),
                "source_url": payload.get("source_url", current.get("source_url")),
            }),
            "channel_id": payload.get("channel_id", current.get("channel_id")),
            "channel_name": payload.get("channel_name", current.get("channel_name")),
            "frame_interval": max(int(payload.get("frame_interval", current.get("frame_interval", 10)) or 10), 1),
            "status": payload.get("status", current.get("status") or "stopped"),
            "created_at": created_at,
            "updated_at": now_iso(),
            "regions": derived_regions,
            "algorithms": normalized_algorithms,
            "runtime": {
                "status": runtime.get("status", "stopped"),
                "is_running": bool(runtime.get("is_running", False)),
                "started_at": runtime.get("started_at"),
                "stopped_at": runtime.get("stopped_at"),
                "last_error": runtime.get("last_error"),
                "last_analysis_at": runtime.get("last_analysis_at"),
                "last_event_at": runtime.get("last_event_at"),
            },
        }
        return task

    async def list_tasks(self, prefer_metadata: bool = False) -> dict[str, Any]:
        started_at = time.perf_counter()
        with SessionLocal() as session:
            task_rows = (
                session.query(TaskMeta)
                .order_by(TaskMeta.created_at.desc(), TaskMeta.id.desc())
                .all()
            )

        if task_rows:
            result = {
                "items": [
                    self._build_task_list_item_from_metadata_row(row)
                    for row in task_rows
                ]
            }
            logger.info(
                "[TaskListTiming] source=metadata count=%s prefer_metadata=%s elapsed_ms=%s",
                len(result["items"]),
                prefer_metadata,
                round((time.perf_counter() - started_at) * 1000, 2),
            )
            return result

        if settings.METADATA_DB_ENABLED:
            logger.info(
                "[TaskListTiming] source=metadata-empty count=0 prefer_metadata=%s elapsed_ms=%s",
                prefer_metadata,
                round((time.perf_counter() - started_at) * 1000, 2),
            )
            return {"items": []}

        items = list_json_items(self.tasks_dir)
        items.sort(
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        normalized_items = [self._normalize_task(item, current=item) for item in items]
        result = {"items": normalized_items}
        logger.info(
            "[TaskListTiming] source=json count=%s prefer_metadata=%s elapsed_ms=%s",
            len(result["items"]),
            prefer_metadata,
            round((time.perf_counter() - started_at) * 1000, 2),
        )
        return result

    def _build_task_list_item_from_metadata_row(self, row: TaskMeta) -> dict[str, Any]:
        payload = dict(row.raw_json or {})
        runtime_payload = dict(payload.get("runtime") or {})
        runtime_status = runtime_payload.get("status") or row.status or "stopped"
        runtime = {
            "status": runtime_status,
            "is_running": bool(runtime_payload.get("is_running", row.is_running)),
            "started_at": runtime_payload.get("started_at") or format_datetime(row.started_at),
            "stopped_at": runtime_payload.get("stopped_at") or format_datetime(row.stopped_at),
            "last_error": runtime_payload.get("last_error", row.last_error),
            "last_analysis_at": runtime_payload.get("last_analysis_at") or format_datetime(row.last_analysis_at),
            "last_event_at": runtime_payload.get("last_event_at") or format_datetime(row.last_event_at),
        }
        return {
            "id": row.id,
            "name": row.name,
            "source_url": row.source_url,
            "source_type": row.source_type,
            "channel_id": row.channel_id,
            "channel_name": row.channel_name or payload.get("channel_name"),
            "frame_interval": int(row.frame_interval or 10),
            "status": row.status or runtime_status,
            "algorithm_count": int(row.algorithm_count or 0),
            "region_count": int(row.region_count or 0),
            "created_at": format_datetime(row.created_at),
            "updated_at": format_datetime(row.updated_at),
            "runtime": runtime,
        }

    async def get_task(self, task_id: str, prefer_metadata: bool = False) -> dict[str, Any] | None:
        metadata_task = await self.get_task_from_metadata(task_id)
        if metadata_task:
            self._save_task_cache(metadata_task)
            return metadata_task

        if settings.METADATA_DB_ENABLED or prefer_metadata:
            return None

        path = self._get_path(task_id)
        if path.exists():
            task = load_json(path)
            normalized = self._normalize_task(task, current=task)
            if normalized != task:
                save_json(path, normalized, sync_metadata=False)
            return normalized
        return None

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("channel_id"):
            raise ValueError("Task creation requires channel_id")
        normalized_payload = await self._hydrate_task_source_from_channel(payload)
        existing_task = await self._find_any_task_on_channel(normalized_payload.get("channel_id"))
        if existing_task:
            channel_id = normalized_payload.get("channel_id")
            raise ValueError(f"Channel already has a task: {channel_id}")
        task = self._normalize_task(normalized_payload)
        return await self._persist_task(task)

    async def update_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        current, path = await self._load_task_for_write(task_id)
        if not current:
            return None
        merged_payload = {**current, **payload}
        if payload.get("channel_id") and "source_url" not in payload:
            merged_payload["source_url"] = None
            merged_payload["source_type"] = None
        normalized_payload = await self._hydrate_task_source_from_channel(merged_payload)
        existing_task = await self._find_any_task_on_channel(normalized_payload.get("channel_id"), exclude_task_id=task_id)
        if existing_task:
            channel_id = normalized_payload.get("channel_id")
            raise ValueError(f"Channel already has a task: {channel_id}")
        task = self._normalize_task(normalized_payload, current=current)
        task["id"] = task_id
        return await self._persist_task(task)

    async def delete_task(self, task_id: str) -> dict[str, Any] | None:
        path = self._get_path(task_id)
        item = await self.get_task(task_id, prefer_metadata=True)
        if not item:
            return None
        if path.exists():
            path.unlink()
        with SessionLocal() as session:
            session.query(TaskAlgorithmMetricMeta).filter(TaskAlgorithmMetricMeta.task_id == task_id).delete()
            session.commit()
        delete_json_document(path, item.get("id"))
        return item

    async def add_algorithm(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        started_at = time.perf_counter()
        task = await self.get_task(task_id)
        logger.info("[TaskAlgorithmTiming] op=add task_id=%s step=get_task elapsed_ms=%s", task_id, round((time.perf_counter() - started_at) * 1000, 2))
        if not task:
            return None
        algorithms = list(task.get("algorithms") or [])
        model_id = payload.get("model_id")
        if model_id and any(str(item.get("model_id") or "") == str(model_id) for item in algorithms):
            raise ValueError(f"Algorithm model already exists in task: {model_id}")
        enriched_payload = await self._apply_model_scene_defaults(payload)
        logger.info("[TaskAlgorithmTiming] op=add task_id=%s step=apply_model_defaults elapsed_ms=%s", task_id, round((time.perf_counter() - started_at) * 1000, 2))
        algorithm = self._normalize_algorithm(enriched_payload, task.get("regions") or [])
        algorithms.append(algorithm)
        task["algorithms"] = algorithms
        task["regions"] = self._derive_task_regions_from_algorithms(algorithms)
        task["updated_at"] = now_iso()
        persisted = await self._persist_task(task, reload_metadata=False)
        logger.info(
            "[TaskAlgorithmTiming] op=add task_id=%s algorithm_id=%s step=persist elapsed_ms=%s",
            task_id,
            algorithm.get("id"),
            round((time.perf_counter() - started_at) * 1000, 2),
        )
        return next((item for item in persisted.get("algorithms") or [] if item.get("id") == algorithm["id"]), algorithm)

    async def update_algorithm(self, task_id: str, algorithm_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        started_at = time.perf_counter()
        task = await self.get_task(task_id)
        logger.info("[TaskAlgorithmTiming] op=update task_id=%s algorithm_id=%s step=get_task elapsed_ms=%s", task_id, algorithm_id, round((time.perf_counter() - started_at) * 1000, 2))
        if not task:
            return None
        algorithms = list(task.get("algorithms") or [])
        model_id = payload.get("model_id")
        if model_id and any(
            str(item.get("id") or "") != str(algorithm_id)
            and str(item.get("model_id") or "") == str(model_id)
            for item in algorithms
        ):
            raise ValueError(f"Algorithm model already exists in task: {model_id}")
        for index, algorithm in enumerate(algorithms):
            if algorithm.get("id") != algorithm_id:
                continue
            merged = {**algorithm, **payload, "id": algorithm_id}
            merged = await self._apply_model_scene_defaults(merged)
            logger.info("[TaskAlgorithmTiming] op=update task_id=%s algorithm_id=%s step=apply_model_defaults elapsed_ms=%s", task_id, algorithm_id, round((time.perf_counter() - started_at) * 1000, 2))
            normalized = self._normalize_algorithm(merged, task.get("regions") or [])
            algorithms[index] = normalized
            task["algorithms"] = algorithms
            task["regions"] = self._derive_task_regions_from_algorithms(algorithms)
            task["updated_at"] = now_iso()
            persisted = await self._persist_task(task, reload_metadata=False)
            logger.info("[TaskAlgorithmTiming] op=update task_id=%s algorithm_id=%s step=persist elapsed_ms=%s", task_id, algorithm_id, round((time.perf_counter() - started_at) * 1000, 2))
            return next((item for item in persisted.get("algorithms") or [] if item.get("id") == algorithm_id), normalized)
        return None

    async def delete_algorithm(self, task_id: str, algorithm_id: str) -> dict[str, Any] | None:
        started_at = time.perf_counter()
        task = await self.get_task(task_id)
        logger.info("[TaskAlgorithmTiming] op=delete task_id=%s algorithm_id=%s step=get_task elapsed_ms=%s", task_id, algorithm_id, round((time.perf_counter() - started_at) * 1000, 2))
        if not task:
            return None
        algorithms = list(task.get("algorithms") or [])
        remaining = [algorithm for algorithm in algorithms if algorithm.get("id") != algorithm_id]
        if len(remaining) == len(algorithms):
            return None
        task["algorithms"] = remaining
        task["regions"] = self._derive_task_regions_from_algorithms(remaining)
        task["updated_at"] = now_iso()
        with SessionLocal() as session:
            session.query(TaskAlgorithmMetricMeta).filter(TaskAlgorithmMetricMeta.algorithm_id == algorithm_id).delete()
            session.commit()
        logger.info("[TaskAlgorithmTiming] op=delete task_id=%s algorithm_id=%s step=delete_metrics elapsed_ms=%s", task_id, algorithm_id, round((time.perf_counter() - started_at) * 1000, 2))
        await self._persist_task(task, reload_metadata=False)
        logger.info("[TaskAlgorithmTiming] op=delete task_id=%s algorithm_id=%s step=persist elapsed_ms=%s", task_id, algorithm_id, round((time.perf_counter() - started_at) * 1000, 2))
        return {"ok": True}

    async def list_task_algorithms(self, task_id: str, prefer_metadata: bool = False) -> dict[str, Any] | None:
        task = await self.get_task_from_metadata(task_id)
        if task:
            return {"items": task.get("algorithms") or []}

        if settings.METADATA_DB_ENABLED or prefer_metadata:
            return None

        task = await self.get_task(task_id, prefer_metadata=False)
        if not task:
            return None
        return {"items": task.get("algorithms") or []}

    def _build_available_model_algorithm_candidate(self, model_meta: dict[str, Any]) -> dict[str, Any]:
        item = dict(model_meta or {})
        scene_candidates = list(item.get("scene_candidates") or [])
        primary_scene = scene_candidates[0] if scene_candidates else {}
        auto_label_strategy = item.get("auto_label_strategy") or "manual"

        detect_all_classes = auto_label_strategy == "all"
        if detect_all_classes:
            label_source = "all"
        elif primary_scene:
            label_source = "recommended"
        else:
            label_source = "manual"

        algorithm_type = str(item.get("algorithm_type") or item.get("task") or "detection")
        item.update(
            {
                "algorithm_type": algorithm_type,
                "scene_key": primary_scene.get("scene_key"),
                "scene_name": primary_scene.get("scene_name"),
                "rule_type": primary_scene.get("rule_type") or algorithm_type,
                "enabled": True,
                "detect_all_classes": detect_all_classes,
                "label_source": label_source,
                "confidence": float(item.get("confidence", 0.25) or 0.25),
                "alert_interval": int(item.get("alert_interval", 60) or 60),
                "need_snapshot": bool(primary_scene.get("default_need_snapshot", True)),
                "need_video_clip": bool(primary_scene.get("default_need_video_clip", True)),
                "need_overlay": False,
                "requires_region": False,
                "video_before_seconds": self._default_video_before_seconds() if bool(primary_scene.get("default_need_video_clip", True)) else 0,
                "video_after_seconds": self._default_video_after_seconds() if bool(primary_scene.get("default_need_video_clip", True)) else 0,
                "overlay_color": item.get("overlay_color") or "#ff4d4f",
                "postprocess_config": dict(item.get("postprocess_config") or {}),
            }
        )
        return item

    async def list_task_available_models(self, task_id: str, prefer_metadata: bool = False) -> dict[str, Any] | None:
        task = await self.get_task_from_metadata(task_id)
        if not task and not (settings.METADATA_DB_ENABLED or prefer_metadata):
            task = await self.get_task(task_id, prefer_metadata=False)
        if not task:
            return None

        bound_model_ids = {
            str(item.get("model_id") or "")
            for item in list(task.get("algorithms") or [])
            if isinstance(item, dict) and item.get("model_id")
        }
        models_result = await model_service.list_models()
        items = [
            self._build_available_model_algorithm_candidate(item)
            for item in list(models_result.get("models") or [])
            if str(item.get("model_id") or "") not in bound_model_ids
        ]
        return {"items": items}

    async def list_algorithm_regions(self, task_id: str, algorithm_id: str) -> dict[str, Any] | None:
        task = await self.get_task_from_metadata(task_id)
        if not task and not settings.METADATA_DB_ENABLED:
            task = await self.get_task(task_id)
        if not task:
            return None
        algorithm = next(
            (item for item in list(task.get("algorithms") or []) if item.get("id") == algorithm_id),
            None,
        )
        if not algorithm:
            return None
        return {"items": list(algorithm.get("regions") or [])}

    async def replace_regions(self, task_id: str, algorithm_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        task = await self.get_task(task_id)
        if not task:
            return None

        requested_regions = [
            self._normalize_region(region)
            for region in list(payload.get("regions") or [])
            if isinstance(region, dict)
        ]

        algorithms = list(task.get("algorithms") or [])
        for index, algorithm in enumerate(algorithms):
            if algorithm.get("id") != algorithm_id:
                continue

            normalized_algorithm = self._normalize_algorithm(
                {
                    **algorithm,
                    "region_ids": [],
                    "regions": requested_regions,
                },
                task.get("regions") or [],
            )
            algorithms[index] = normalized_algorithm
            task["algorithms"] = algorithms
            task["regions"] = self._derive_task_regions_from_algorithms(algorithms)
            task["updated_at"] = now_iso()
            persisted_task = await self._persist_task(task)
            result = {
                "items": list(normalized_algorithm.get("regions") or []),
                "playback_ready": False,
            }
            if not self._is_task_running(persisted_task):
                return result

            self._schedule_task_preview_warmup(persisted_task, reason="region-update")
            return {
                **result,
                "preview_refresh_scheduled": True,
            }

        return None

    async def start_task(self, task_id: str) -> dict[str, Any] | None:
        started_at = time.perf_counter()

        def _log_step(step: str) -> None:
            logger.info(
                "[TaskStartTiming] task_id=%s step=%s elapsed_ms=%s",
                task_id,
                step,
                round((time.perf_counter() - started_at) * 1000, 2),
            )

        task = await self.get_task(task_id, prefer_metadata=True)
        _log_step("get_task")
        if not task:
            return None
        running_task = await self._find_running_task_on_channel(task.get("channel_id"), exclude_task_id=task_id)
        _log_step("find_running_task")
        if running_task:
            running_name = running_task.get("name") or running_task.get("id") or "unknown"
            raise ValueError(f"当前通道已有运行中任务: {running_name}")
        refreshed_payload = await self._hydrate_task_source_from_channel(task)
        _log_step("hydrate_source")
        if task.get("channel_id") and not refreshed_payload.get("source_url"):
            raise ValueError(f"Task bound channel not found: {task.get('channel_id')}")
        if refreshed_payload.get("source_url") != task.get("source_url") or refreshed_payload.get("source_type") != task.get("source_type"):
            task = self._normalize_task(refreshed_payload, current=task)
            task["id"] = task_id
            task = await self._persist_task(task)
            _log_step("persist_refreshed_task")
        if not self._has_runnable_algorithm(task):
            raise ValueError("Task has no enabled algorithms")
        _log_step("validated_algorithms")
        result = await analysis_service.start_task_analysis(task_id)
        _log_step("start_analysis")
        if self._should_block_on_preview_warmup(task):
            await self._warmup_task_preview(task)
            _log_step("warmup_task_preview")
        else:
            self._schedule_task_preview_warmup(task, reason="task-start")
            _log_step("schedule_preview_warmup")
        return result

    async def stop_task(self, task_id: str) -> dict[str, Any] | None:
        task = await self.get_task(task_id, prefer_metadata=True)
        if not task:
            return None
        result = await analysis_service.stop_task_analysis(task_id)
        if task.get("channel_id") and self._has_overlay_algorithm(task):
            try:
                logger.info(
                    "[TaskZonePreviewStop] task_id=%s channel_id=%s stop_zone_preview=true",
                    task_id,
                    task.get("channel_id"),
                )
                await channel_service.stop_zone_preview(task["channel_id"])
            except Exception:
                pass
        if task.get("channel_id"):
            try:
                await channel_service.clear_task_preview_state(task["channel_id"], owner_task_id=task_id)
            except Exception:
                pass
            try:
                await channel_service.stop(task["channel_id"])
            except Exception:
                pass
        return result

    async def restart_task(self, task_id: str) -> dict[str, Any] | None:
        task = await self.get_task(task_id, prefer_metadata=True)
        if not task:
            return None
        refreshed_payload = await self._hydrate_task_source_from_channel(task)
        if task.get("channel_id") and not refreshed_payload.get("source_url"):
            raise ValueError(f"Task bound channel not found: {task.get('channel_id')}")
        if refreshed_payload.get("source_url") != task.get("source_url") or refreshed_payload.get("source_type") != task.get("source_type"):
            task = self._normalize_task(refreshed_payload, current=task)
            task["id"] = task_id
            task = await self._persist_task(task)
        if not self._has_runnable_algorithm(task):
            raise ValueError("Task has no enabled algorithms")
        result = await analysis_service.restart_task_analysis(task_id)
        if self._should_block_on_preview_warmup(task):
            await self._warmup_task_preview(task)
        else:
            self._schedule_task_preview_warmup(task, reason="task-restart")
        return result

    async def restore_running_tasks(self) -> dict[str, Any]:
        def _cleanup_stale_stopping_tasks() -> int:
            cleaned = 0
            with SessionLocal() as session:
                stale_rows = (
                    session.query(TaskMeta)
                    .filter(TaskMeta.status == "stopping")
                    .all()
                )

                for row in stale_rows:
                    payload = dict(row.raw_json or {})
                    runtime = dict(payload.get("runtime") or {})
                    runtime_status = str(runtime.get("status") or "").lower()
                    runtime_is_running = bool(runtime.get("is_running"))
                    if runtime_status != "stopping" and runtime_is_running:
                        continue

                    payload["status"] = "stopped"
                    payload["runtime"] = {
                        **runtime,
                        "status": "stopped",
                        "is_running": False,
                        "stopped_at": runtime.get("stopped_at") or format_datetime(row.updated_at) or now_iso(),
                    }
                    row.status = "stopped"
                    row.is_running = False
                    row.raw_json = payload
                    cleaned += 1

                if cleaned:
                    session.commit()
            return cleaned

        cleaned_stopping = await asyncio.to_thread(_cleanup_stale_stopping_tasks)
        if cleaned_stopping:
            logger.info(
                "[TaskRestoreCleanup] cleaned_stale_stopping=%s",
                cleaned_stopping,
            )

        with SessionLocal() as session:
            task_ids = [
                self._extract_scalar_id(item)
                for item in session.query(TaskMeta.id)
                .filter(
                    (TaskMeta.is_running.is_(True))
                    | (TaskMeta.status.in_(["running", "starting", "restarting"]))
                )
                .order_by(TaskMeta.created_at.desc(), TaskMeta.id.desc())
                .all()
            ]
        restored = 0
        skipped = 0
        failed: list[dict[str, str]] = []

        for row in task_ids:
            task_id = self._extract_scalar_id(row)
            if not task_id:
                continue

            task_started_at = time.perf_counter()

            def _log_restore_step(step: str) -> None:
                logger.info(
                    "[TaskRestoreTiming] task_id=%s step=%s elapsed_ms=%s",
                    task_id,
                    step,
                    round((time.perf_counter() - task_started_at) * 1000, 2),
                )

            task = await self.get_task_from_metadata(task_id)
            _log_restore_step("get_task")
            if not task:
                skipped += 1
                continue
            runtime = task.get("runtime") or {}
            should_restore = bool(runtime.get("is_running")) or str(runtime.get("status") or "").lower() in {
                "running",
                "starting",
                "restarting",
            }
            _log_restore_step("check_runtime")
            if not should_restore:
                skipped += 1
                continue

            try:
                refreshed_payload = await self._hydrate_task_source_from_channel(
                    task,
                    refresh_wvp_source=False,
                )
                _log_restore_step("hydrate_source")
                if refreshed_payload.get("source_url") != task.get("source_url") or refreshed_payload.get("source_type") != task.get("source_type"):
                    task = self._normalize_task(refreshed_payload, current=task)
                    task["id"] = task_id
                    task = await self._persist_task(task)
                    _log_restore_step("persist_refreshed_task")

                if not self._has_runnable_algorithm(task):
                    skipped += 1
                    continue
                _log_restore_step("validated_algorithms")

                await analysis_service.start_task_analysis(task_id)
                _log_restore_step("start_analysis")
                self._schedule_task_source_refresh(task_id, reason="startup-restore")
                self._schedule_task_preview_warmup(
                    task,
                    delay_seconds=float(getattr(settings, "TASK_PREVIEW_WARMUP_STARTUP_DELAY_SECONDS", 0.0) or 0.0),
                    reason="startup-restore",
                )
                _log_restore_step("schedule_preview_warmup")
                restored += 1
                stagger_seconds = max(float(getattr(settings, "TASK_RESTORE_STARTUP_STAGGER_SECONDS", 0.0) or 0.0), 0.0)
                if stagger_seconds > 0:
                    await asyncio.sleep(stagger_seconds)
            except Exception as exc:
                failed.append({
                    "task_id": task_id,
                    "error": str(exc),
                })

        return {
            "total": len(task_ids),
            "restored": restored,
            "skipped": skipped,
            "failed": failed,
        }

    async def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        with SessionLocal() as session:
            task_row = session.query(TaskMeta).filter(TaskMeta.id == task_id).one_or_none()
        if not task_row:
            return None

        payload = dict(task_row.raw_json or {})
        runtime_payload = dict(payload.get("runtime") or {})
        runtime = await analysis_service.get_task_analysis_status(task_id)
        metrics = await metric_service.list_task_metrics(task_id)
        merged_runtime = {
            "status": (runtime or {}).get("status") or runtime_payload.get("status") or task_row.status or "stopped",
            "is_running": bool((runtime or {}).get("is_running", runtime_payload.get("is_running", task_row.is_running))),
            "started_at": (runtime or {}).get("started_at") or runtime_payload.get("started_at") or format_datetime(task_row.started_at),
            "stopped_at": (runtime or {}).get("stopped_at") or runtime_payload.get("stopped_at") or format_datetime(task_row.stopped_at),
            "last_analysis_at": (runtime or {}).get("last_analysis_at") or runtime_payload.get("last_analysis_at") or format_datetime(task_row.last_analysis_at),
            "last_event_at": (runtime or {}).get("last_event_at") or runtime_payload.get("last_event_at") or format_datetime(task_row.last_event_at),
            "last_error": (runtime or {}).get("last_error", runtime_payload.get("last_error", task_row.last_error)),
            "frame_count": (runtime or {}).get("frame_count", runtime_payload.get("frame_count", 0)),
            "event_count": (runtime or {}).get("event_count", runtime_payload.get("event_count", 0)),
            "models": (runtime or {}).get("models", runtime_payload.get("models", {})),
        }
        return {
            "task_id": task_id,
            "name": task_row.name,
            "channel_id": task_row.channel_id,
            "channel_name": task_row.channel_name or payload.get("channel_name"),
            "status": merged_runtime["status"],
            "runtime": merged_runtime,
            "algorithm_count": int(task_row.algorithm_count or 0),
            "region_count": int(task_row.region_count or 0),
            "metrics": metrics.get("items", []),
        }

    async def list_task_metrics(self, task_id: str) -> dict[str, Any] | None:
        task = await self.get_task(task_id, prefer_metadata=True)
        if not task:
            return None
        return await metric_service.list_task_metrics(task_id)

    async def list_task_metric_history(self, task_id: str, filters: dict[str, Any]) -> dict[str, Any] | None:
        task = await self.get_task(task_id, prefer_metadata=True)
        if not task:
            return None
        return await metric_service.list_task_metric_history(
            task_id,
            algorithm_id=filters.get("algorithm_id"),
            metric_type=filters.get("metric_type"),
            region_id=filters.get("region_id"),
            start_time=metadata_db_sync._parse_dt(filters.get("start_time")),
            end_time=metadata_db_sync._parse_dt(filters.get("end_time")),
            page=int(filters.get("page", 1) or 1),
            page_size=int(filters.get("page_size", 100) or 100),
        )

    async def resync_task_metadata(self, task_id: str) -> dict[str, Any] | None:
        task = await self.get_task_from_metadata(task_id)
        if not task:
            return None
        self._save_task_cache(task)
        return {
            "ok": True,
            "task_id": task_id,
            "source": "metadata",
        }

    async def resync_all_task_metadata(self) -> dict[str, Any]:
        failed: list[dict[str, str]] = []
        synced = 0
        with SessionLocal() as session:
            task_ids = [
                self._extract_scalar_id(row)
                for row in session.query(TaskMeta.id).order_by(TaskMeta.created_at.desc(), TaskMeta.id.desc()).all()
            ]

        for task_id in task_ids:
            if not task_id:
                continue
            try:
                task = await self.get_task_from_metadata(task_id)
                if not task:
                    failed.append({
                        "task_id": task_id,
                        "error": "task metadata not found",
                    })
                    continue
                self._save_task_cache(task)
                synced += 1
            except Exception as exc:
                failed.append({
                    "task_id": task_id,
                    "error": str(exc),
                })

        return {
            "total": len([task_id for task_id in task_ids if task_id]),
            "synced": synced,
            "failed": failed,
        }

    async def get_task_metadata_snapshot(self, task_id: str) -> dict[str, Any] | None:
        with SessionLocal() as session:
            task_row = session.query(TaskMeta).filter(TaskMeta.id == task_id).one_or_none()
            if not task_row:
                return None

            algorithm_rows = (
                session.query(TaskAlgorithmMeta)
                .filter(TaskAlgorithmMeta.task_id == task_id)
                .order_by(TaskAlgorithmMeta.sort_order.asc(), TaskAlgorithmMeta.id.asc())
                .all()
            )
            algorithm_region_rows = (
                session.query(AlgorithmRegionMeta)
                .filter(AlgorithmRegionMeta.task_id == task_id)
                .order_by(AlgorithmRegionMeta.algorithm_id.asc(), AlgorithmRegionMeta.id.asc())
                .all()
            )
            region_rows = (
                session.query(TaskRegionMeta)
                .filter(TaskRegionMeta.task_id == task_id)
                .order_by(TaskRegionMeta.id.asc())
                .all()
            )
            link_rows = (
                session.query(TaskAlgorithmRegionMeta)
                .filter(TaskAlgorithmRegionMeta.task_id == task_id)
                .order_by(TaskAlgorithmRegionMeta.algorithm_id.asc(), TaskAlgorithmRegionMeta.region_id.asc())
                .all()
            )

        return {
            "task": {
                "id": task_row.id,
                "name": task_row.name,
                "source_url": task_row.source_url,
                "source_type": task_row.source_type,
                "channel_id": task_row.channel_id,
                "channel_name": task_row.channel_name,
                "frame_interval": task_row.frame_interval,
                "status": task_row.status,
                "algorithm_count": task_row.algorithm_count,
                "region_count": task_row.region_count,
                "is_running": task_row.is_running,
                "started_at": format_datetime(task_row.started_at),
                "stopped_at": format_datetime(task_row.stopped_at),
                "last_analysis_at": format_datetime(task_row.last_analysis_at),
                "last_event_at": format_datetime(task_row.last_event_at),
                "last_error": task_row.last_error,
            },
            "algorithms": [
                {
                    "id": row.id,
                    "task_id": row.task_id,
                    "algorithm_type": row.algorithm_type,
                    "scene_key": row.scene_key,
                    "scene_name": row.scene_name,
                    "rule_type": row.rule_type,
                    "model_id": row.model_id,
                    "enabled": row.enabled,
                    "labels_json": row.labels_json or [],
                    "detect_all_classes": bool(row.detect_all_classes),
                    "label_source": row.label_source,
                    "confidence": row.confidence,
                    "alert_interval": row.alert_interval,
                    "need_snapshot": row.need_snapshot,
                    "need_video_clip": row.need_video_clip,
                    "video_before_seconds": row.video_before_seconds,
                    "video_after_seconds": row.video_after_seconds,
                    "need_overlay": row.need_overlay,
                    "requires_region": bool(row.requires_region),
                    "overlay_color": row.overlay_color,
                    "postprocess_config_json": row.postprocess_config_json or {},
                    "sort_order": row.sort_order,
                }
                for row in algorithm_rows
            ],
            "algorithm_regions": [
                {
                    "id": row.id,
                    "task_id": row.task_id,
                    "algorithm_id": row.algorithm_id,
                    "name": row.name,
                    "type": row.type,
                    "color": row.color,
                    "coordinates_json": row.coordinates_json or [],
                    "enabled": row.enabled,
                }
                for row in algorithm_region_rows
            ],
            "regions": [
                {
                    "id": row.id,
                    "task_id": row.task_id,
                    "name": row.name,
                    "type": row.type,
                    "color": row.color,
                    "coordinates_json": row.coordinates_json or [],
                    "enabled": row.enabled,
                }
                for row in region_rows
            ],
            "links": [
                {
                    "algorithm_id": row.algorithm_id,
                    "region_id": row.region_id,
                }
                for row in link_rows
            ],
        }

    async def get_task_from_metadata(self, task_id: str) -> dict[str, Any] | None:
        with SessionLocal() as session:
            task_row = session.query(TaskMeta).filter(TaskMeta.id == task_id).one_or_none()
            if not task_row:
                return None

            algorithm_rows = (
                session.query(TaskAlgorithmMeta)
                .filter(TaskAlgorithmMeta.task_id == task_id)
                .order_by(TaskAlgorithmMeta.sort_order.asc(), TaskAlgorithmMeta.id.asc())
                .all()
            )
            algorithm_region_rows = (
                session.query(AlgorithmRegionMeta)
                .filter(AlgorithmRegionMeta.task_id == task_id)
                .order_by(AlgorithmRegionMeta.algorithm_id.asc(), AlgorithmRegionMeta.id.asc())
                .all()
            )
            region_rows = (
                session.query(TaskRegionMeta)
                .filter(TaskRegionMeta.task_id == task_id)
                .order_by(TaskRegionMeta.id.asc())
                .all()
            )
            link_rows = (
                session.query(TaskAlgorithmRegionMeta)
                .filter(TaskAlgorithmRegionMeta.task_id == task_id)
                .order_by(TaskAlgorithmRegionMeta.algorithm_id.asc(), TaskAlgorithmRegionMeta.region_id.asc())
                .all()
            )
            model_ids = [
                row.model_id
                for row in algorithm_rows
                if row.model_id
            ]
            model_rows = (
                session.query(ModelMetaRecord.id, ModelMetaRecord.name, ModelMetaRecord.alert_interval)
                .filter(ModelMetaRecord.id.in_(model_ids))
                .all()
                if model_ids
                else []
            )

        model_names_by_id = {
            str(row[0]): row[1]
            for row in model_rows
            if row and row[0]
        }
        model_alert_intervals_by_id = {
            str(row[0]): int(row[2])
            for row in model_rows
            if row and row[0] and row[2] is not None
        }

        algorithm_regions_by_algorithm: dict[str, list[dict[str, Any]]] = {}
        if algorithm_region_rows:
            for row in algorithm_region_rows:
                algorithm_regions_by_algorithm.setdefault(row.algorithm_id, []).append(
                    {
                        "id": row.id,
                        "name": row.name,
                        "type": row.type,
                        "color": row.color,
                        "coordinates": row.coordinates_json or [],
                        "enabled": bool(row.enabled),
                    }
                )
        else:
            regions = [
                {
                    "id": row.id,
                    "name": row.name,
                    "type": row.type,
                    "color": row.color,
                    "coordinates": row.coordinates_json or [],
                    "enabled": bool(row.enabled),
                }
                for row in region_rows
            ]
            region_map = {region["id"]: region for region in regions}
            region_ids_by_algorithm: dict[str, list[str]] = {}
            for row in link_rows:
                region_ids_by_algorithm.setdefault(row.algorithm_id, []).append(row.region_id)
            for algorithm_id, region_ids in region_ids_by_algorithm.items():
                algorithm_regions_by_algorithm[algorithm_id] = [
                    {
                        **region_map[region_id],
                        "id": self._build_private_region_id(algorithm_id, region_id),
                    }
                    for region_id in region_ids
                    if region_id in region_map
                ]

        algorithms = []
        for row in algorithm_rows:
            algorithm_regions = list(algorithm_regions_by_algorithm.get(row.id, []))
            region_ids = [region["id"] for region in algorithm_regions if region.get("id")]
            model_alert_interval = model_alert_intervals_by_id.get(str(row.model_id or ""))
            alert_interval = model_alert_interval if model_alert_interval is not None else int(row.alert_interval or 60)
            algorithms.append(
                {
                    "id": row.id,
                    "algorithm_type": row.algorithm_type or "detection",
                    "scene_key": row.scene_key,
                    "scene_name": row.scene_name,
                    "rule_type": row.rule_type or row.algorithm_type or "detection",
                    "model_id": row.model_id,
                    "model_name": model_names_by_id.get(str(row.model_id or "")),
                    "enabled": bool(row.enabled),
                    "labels": row.labels_json or [],
                    "detect_all_classes": bool(row.detect_all_classes),
                    "label_source": row.label_source or ("all" if row.detect_all_classes else "manual"),
                    "confidence": float(row.confidence or 0.25),
                    "alert_interval": alert_interval,
                    "need_snapshot": bool(row.need_snapshot),
                    "need_video_clip": bool(row.need_video_clip),
                    "video_before_seconds": int(row.video_before_seconds or 0),
                    "video_after_seconds": int(row.video_after_seconds or 0),
                    "need_overlay": bool(row.need_overlay),
                    "requires_region": bool(row.requires_region),
                    "overlay_color": row.overlay_color or "#ff4d4f",
                    "postprocess_config": row.postprocess_config_json or {},
                    "region_ids": region_ids,
                    "regions": algorithm_regions,
                }
            )

        regions = self._derive_task_regions_from_algorithms(algorithms)
        runtime_payload = self._sync_runtime_models_with_algorithms(
            ((task_row.raw_json or {}).get("runtime")) or {},
            algorithms,
        )
        runtime_status = runtime_payload.get("status") or task_row.status or "stopped"
        runtime_payload["status"] = runtime_status
        runtime_payload["is_running"] = bool(runtime_payload.get("is_running", task_row.is_running))
        runtime_payload["started_at"] = runtime_payload.get("started_at") or format_datetime(task_row.started_at)
        runtime_payload["stopped_at"] = runtime_payload.get("stopped_at") or format_datetime(task_row.stopped_at)
        runtime_payload["last_analysis_at"] = runtime_payload.get("last_analysis_at") or format_datetime(task_row.last_analysis_at)
        runtime_payload["last_event_at"] = runtime_payload.get("last_event_at") or format_datetime(task_row.last_event_at)
        runtime_payload["last_error"] = runtime_payload.get("last_error", task_row.last_error)
        runtime_payload.setdefault("frame_count", 0)
        runtime_payload.setdefault("event_count", 0)
        runtime_payload.setdefault("models", {})

        return {
            "id": task_row.id,
            "name": task_row.name,
            "source_url": task_row.source_url,
            "source_type": task_row.source_type,
            "channel_id": task_row.channel_id,
            "channel_name": task_row.channel_name or (task_row.raw_json or {}).get("channel_name"),
            "frame_interval": int(task_row.frame_interval or 10),
            "status": task_row.status or "stopped",
            "created_at": format_datetime(task_row.created_at),
            "updated_at": format_datetime(task_row.updated_at),
            "regions": regions,
            "algorithms": algorithms,
            "runtime": runtime_payload,
        }

    def _canonicalize_task_view(self, task: dict[str, Any]) -> dict[str, Any]:
        algorithms = []
        for algorithm in list(task.get("algorithms") or []):
            if not isinstance(algorithm, dict):
                continue
            regions = [
                {
                    "id": region.get("id"),
                    "name": region.get("name"),
                    "type": region.get("type") or "polygon",
                    "color": region.get("color"),
                    "coordinates": list(region.get("coordinates") or []),
                    "enabled": bool(region.get("enabled", True)),
                }
                for region in list(algorithm.get("regions") or [])
                if isinstance(region, dict)
            ]
            region_ids = [region.get("id") for region in regions if region.get("id")]
            algorithms.append(
                {
                    "id": algorithm.get("id"),
                    "algorithm_type": algorithm.get("algorithm_type") or "detection",
                    "scene_key": algorithm.get("scene_key"),
                    "scene_name": algorithm.get("scene_name"),
                    "rule_type": algorithm.get("rule_type") or algorithm.get("algorithm_type") or "detection",
                    "model_id": algorithm.get("model_id"),
                    "enabled": bool(algorithm.get("enabled", True)),
                    "labels": list(algorithm.get("labels") or []),
                    "detect_all_classes": bool(algorithm.get("detect_all_classes", not list(algorithm.get("labels") or []))),
                    "label_source": algorithm.get("label_source") or ("all" if algorithm.get("detect_all_classes", not list(algorithm.get("labels") or [])) else "manual"),
                    "confidence": float(algorithm.get("confidence", algorithm.get("conf", 0.25)) or 0.25),
                    "alert_interval": int(algorithm.get("alert_interval", 60) or 60),
                    "need_snapshot": bool(algorithm.get("need_snapshot", True)),
                    "need_video_clip": bool(algorithm.get("need_video_clip", True)),
                    "video_before_seconds": int(algorithm.get("video_before_seconds", 0) or 0),
                    "video_after_seconds": int(algorithm.get("video_after_seconds", 0) or 0),
                    "need_overlay": bool(algorithm.get("need_overlay", False)),
                    "requires_region": bool(algorithm.get("requires_region", algorithm.get("need_overlay", False))),
                    "overlay_color": algorithm.get("overlay_color") or "#ff4d4f",
                    "postprocess_config": dict(algorithm.get("postprocess_config") or {}),
                    "region_ids": region_ids,
                    "regions": regions,
                }
            )

        regions = self._derive_task_regions_from_algorithms(algorithms)

        return {
            "id": task.get("id"),
            "name": task.get("name"),
            "source_url": task.get("source_url"),
            "source_type": task.get("source_type"),
            "channel_id": task.get("channel_id"),
            "channel_name": task.get("channel_name"),
            "frame_interval": int(task.get("frame_interval", 10) or 10),
            "status": task.get("status") or "stopped",
            "regions": regions,
            "algorithms": algorithms,
        }

    async def compare_task_views(self, task_id: str) -> dict[str, Any] | None:
        json_view = await self.get_task(task_id)
        metadata_view = await self.get_task_from_metadata(task_id)
        if not json_view or not metadata_view:
            return None

        canonical_json = self._canonicalize_task_view(json_view)
        canonical_metadata = self._canonicalize_task_view(metadata_view)

        differences: list[str] = []
        if canonical_json != canonical_metadata:
            for key in ["id", "name", "source_url", "source_type", "channel_id", "channel_name", "frame_interval", "status", "regions", "algorithms"]:
                if canonical_json.get(key) != canonical_metadata.get(key):
                    differences.append(key)

        return {
            "task_id": task_id,
            "match": not differences,
            "differences": differences,
            "json_view": canonical_json,
            "metadata_view": canonical_metadata,
        }
