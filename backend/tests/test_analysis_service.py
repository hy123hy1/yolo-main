import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import os

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.settings import settings
from src.services.analysis_service import AnalysisService


class AnalysisServiceTest(unittest.TestCase):
    def test_annotate_frame_draws_red_box_and_label(self):
        service = AnalysisService()
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        detections = [
            {
                "class_name": "person",
                "conf": 0.91,
                "x1": 20,
                "y1": 30,
                "x2": 100,
                "y2": 90,
            }
        ]

        annotated = service._annotate_frame(frame, detections)

        self.assertEqual(tuple(frame[30, 20]), (0, 0, 0))
        self.assertEqual(tuple(annotated[30, 20]), (0, 0, 255))
        self.assertGreater(int(annotated.sum()), 0)

    def test_build_task_event_payload_keeps_detection_class_events_as_detection(self):
        service = AnalysisService()
        payload = service._build_task_event_payload(
            task_id="task_demo",
            task={"channel_id": "channel_demo"},
            algorithm={
                "id": "algo_demo",
                "model_id": "model_demo",
                "algorithm_type": "detection",
                "rule_type": "intrusion",
            },
            detection={"class_name": "person", "conf": 0.9},
            snapshot_path="/tmp/demo.jpg",
        )

        self.assertEqual(payload["event_type"], "detection")
        self.assertEqual(payload["task_id"], "task_demo")
        self.assertEqual(payload["algorithm_id"], "algo_demo")
        self.assertIn('"rule_type": "intrusion"', payload["remark"])

    def test_build_task_event_payload_keeps_non_detection_rule_type(self):
        service = AnalysisService()
        payload = service._build_task_event_payload(
            task_id="task_demo",
            task={"channel_id": "channel_demo"},
            algorithm={
                "id": "algo_demo",
                "model_id": "model_demo",
                "algorithm_type": "detection",
                "rule_type": "count",
            },
            detection={"class_name": "person", "conf": 0.9, "count": 3},
            snapshot_path="/tmp/demo.jpg",
        )

        self.assertEqual(payload["event_type"], "count")

    def test_alarm_video_frames_are_not_annotated(self):
        service = AnalysisService()
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        detections = [
            {
                "class_name": "person",
                "conf": 0.91,
                "x1": 20,
                "y1": 30,
                "x2": 100,
                "y2": 90,
            }
        ]

        encoded = service._prepare_alarm_video_frame(frame, detections)

        self.assertEqual(tuple(encoded[30, 20]), (0, 0, 0))
        self.assertEqual(int(encoded.sum()), 0)

    def test_alarm_video_buffer_frame_is_compressed_and_decodable(self):
        service = AnalysisService()
        frame = np.full((120, 160, 3), 128, dtype=np.uint8)

        packed = service._pack_alarm_video_frame(frame)
        decoded = service._decode_alarm_video_frame(packed)

        self.assertIsInstance(packed, bytes)
        self.assertLess(len(packed), frame.nbytes)
        self.assertEqual(decoded.shape, frame.shape)

    def test_save_alarm_video_accepts_compressed_buffer_frames(self):
        service = AnalysisService()
        frame = np.full((16, 16, 3), 128, dtype=np.uint8)
        frames = [
            (float(index), service._pack_alarm_video_frame(frame))
            for index in range(5)
        ]
        original_recordings_dir = settings.RECORDINGS_DIR

        with tempfile.TemporaryDirectory() as temp_dir:
            settings.RECORDINGS_DIR = Path(temp_dir)
            try:
                video_path = service._save_alarm_video(
                    "ch_demo",
                    "evt_demo",
                    frames,
                    fps=5.0,
                )
            finally:
                settings.RECORDINGS_DIR = original_recordings_dir

            self.assertIsNotNone(video_path)
            cap = cv2.VideoCapture(str(video_path))
            try:
                self.assertTrue(cap.isOpened())
                self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 5)
            finally:
                cap.release()

    def test_clear_model_cache_removes_loaded_models(self):
        service = AnalysisService()
        service._model_cache["model_demo"] = (object(), {"id": "model_demo"})

        service.clear_model_cache()

        self.assertEqual(service._model_cache, {})

    def test_select_inference_device_prefers_cuda_when_auto_and_available(self):
        service = AnalysisService()

        device = service._select_inference_device("auto", cuda_available=True)

        self.assertEqual(device, "cuda:0")

    def test_select_inference_device_falls_back_to_cpu_when_cuda_unavailable(self):
        service = AnalysisService()

        device = service._select_inference_device("cuda:0", cuda_available=False)

        self.assertEqual(device, "cpu")

    def test_detect_frame_passes_selected_device_to_model(self):
        service = AnalysisService()
        calls = []

        def fake_model(_frame, **kwargs):
            calls.append(kwargs)
            return []

        with patch.object(service, "_resolve_inference_device", return_value="cuda:0"):
            detections = service._detect_frame(
                fake_model,
                {"classes": ["person"]},
                np.zeros((16, 16, 3), dtype=np.uint8),
                0.5,
            )

        self.assertEqual(detections, [])
        self.assertEqual(calls[0]["device"], "cuda:0")

    def test_alarm_video_clip_is_limited_by_frame_count(self):
        service = AnalysisService()
        frames = [(float(index), np.zeros((2, 2, 3), dtype=np.uint8)) for index in range(500)]

        selected = service._build_alarm_video_frames(
            before_frames=frames[:250],
            after_frames=frames[250:],
            fps=25.0,
            before_seconds=3,
            after_seconds=3,
        )

        self.assertEqual(len(selected), 150)
        self.assertEqual(selected[0][0], 175.0)
        self.assertEqual(selected[-1][0], 324.0)

    def test_save_alarm_video_clamps_output_to_twenty_seconds(self):
        service = AnalysisService()
        frames = [(float(index), np.zeros((16, 16, 3), dtype=np.uint8)) for index in range(500)]
        original_recordings_dir = settings.RECORDINGS_DIR

        with tempfile.TemporaryDirectory() as temp_dir:
            settings.RECORDINGS_DIR = Path(temp_dir)
            try:
                video_path = service._save_alarm_video(
                    "ch_demo",
                    "evt_demo",
                    frames,
                    fps=25.0,
                )
            finally:
                settings.RECORDINGS_DIR = original_recordings_dir

            self.assertIsNotNone(video_path)
            cap = cv2.VideoCapture(str(video_path))
            try:
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            finally:
                cap.release()

            self.assertEqual(round(fps), 25)
            self.assertEqual(int(frame_count), 500)

    def test_save_event_snapshot_compresses_image_below_100kb(self):
        service = AnalysisService()
        original_snapshots_dir = settings.SNAPSHOTS_DIR
        rng = np.random.default_rng(1234)
        frame = rng.integers(0, 256, size=(1080, 1920, 3), dtype=np.uint8)

        with tempfile.TemporaryDirectory() as temp_dir:
            settings.SNAPSHOTS_DIR = Path(temp_dir)
            try:
                snapshot_path = service._save_event_snapshot(
                    "ch_demo",
                    frame,
                    detections=[
                        {
                            "class_name": "person",
                            "conf": 0.91,
                            "x1": 50,
                            "y1": 60,
                            "x2": 1000,
                            "y2": 900,
                        }
                    ],
                )
            finally:
                settings.SNAPSHOTS_DIR = original_snapshots_dir

            self.assertTrue(Path(snapshot_path).exists())
            self.assertLessEqual(os.path.getsize(snapshot_path), 100 * 1024)

    def test_build_recording_payload_includes_alarm_class(self):
        service = AnalysisService()
        payload = service._build_alarm_recording_payload(
            event={"id": "evt_demo", "event_type": "detection"},
            channel_id="ch_demo",
            task_id="task_demo",
            algorithm_id="algo_demo",
            model_id="model_demo",
            detection={"class_name": "person", "conf": 0.91},
            snapshot_path="/tmp/snapshot.jpg",
            video_path="/tmp/video.mp4",
            video_frames=[(100.0, np.zeros((2, 2, 3), dtype=np.uint8)), (106.0, np.zeros((2, 2, 3), dtype=np.uint8))],
            fps=25.0,
        )

        self.assertEqual(payload["duration"], 20)
        self.assertEqual(payload["raw_json"]["class_name"], "person")
        self.assertEqual(payload["raw_json"]["conf"], 0.91)
        self.assertEqual(payload["raw_json"]["event_type"], "detection")
        self.assertEqual(payload["raw_json"]["event_type_label"], "alarm_clip")

    def test_frame_quality_check_rejects_uniform_gray_frame(self):
        service = AnalysisService()
        gray_frame = np.full((120, 160, 3), 128, dtype=np.uint8)
        scene_frame = np.zeros((120, 160, 3), dtype=np.uint8)
        cv2.rectangle(scene_frame, (20, 20), (100, 100), (255, 255, 255), -1)

        self.assertFalse(service._is_frame_usable(gray_frame))
        self.assertTrue(service._is_frame_usable(scene_frame))

    def test_sanitize_alarm_video_frames_replaces_bad_frames_with_previous_good_frame(self):
        service = AnalysisService()
        good_frame = np.zeros((32, 32, 3), dtype=np.uint8)
        cv2.rectangle(good_frame, (8, 8), (24, 24), (255, 255, 255), -1)
        bad_frame = np.full((32, 32, 3), 128, dtype=np.uint8)

        sanitized = service._sanitize_alarm_video_frames([
            (1.0, good_frame),
            (2.0, bad_frame),
        ])

        self.assertEqual(len(sanitized), 2)
        self.assertTrue(np.array_equal(sanitized[0][1], good_frame))
        self.assertTrue(np.array_equal(sanitized[1][1], good_frame))

    def test_collect_task_algorithm_inferences_runs_algorithms_in_parallel(self):
        service = AnalysisService()
        task = {"id": "task_parallel"}
        algorithms = [
            {"id": "algo_a", "model_id": "model_a", "frame_interval": 1, "confidence": 0.25},
            {"id": "algo_b", "model_id": "model_b", "frame_interval": 1, "confidence": 0.25},
        ]
        barrier = threading.Barrier(2, timeout=1.0)

        def fake_detect(_model, _meta, _frame, _conf):
            barrier.wait()
            return [{"class_name": "person", "conf": 0.9}]

        with (
            patch.object(service, "_load_model", return_value=(object(), {"classes": ["person"]})),
            patch.object(service, "_detect_frame", side_effect=fake_detect),
            patch.object(service, "_apply_algorithm_scoped_filters", side_effect=lambda detections, *_args: detections),
        ):
            results = service._collect_task_algorithm_inferences(
                task=task,
                algorithms=algorithms,
                frame=np.zeros((8, 8, 3), dtype=np.uint8),
                frame_count=1,
            )

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["error"] is None for result in results))
        self.assertTrue(all(len(result["detections"]) == 1 for result in results))

    def test_collect_task_alarm_after_frames_stops_after_fail_limit(self):
        service = AnalysisService()
        capture = object()

        with (
            patch.object(service, "_build_task_capture", return_value=capture),
            patch.object(service, "_release_task_capture") as release_capture_mock,
            patch.object(service, "_task_alarm_video_read_fail_limit", return_value=3),
            patch.object(service, "_task_alarm_video_capture_timeout_seconds", return_value=30.0),
            patch.object(service, "_read_task_frame", side_effect=[(False, None), (False, None), (False, None)]) as read_frame_mock,
            patch("src.services.analysis_service.time.sleep", lambda *_args, **_kwargs: None),
        ):
            frames = service._collect_task_alarm_after_frames(
                source_url="rtsp://example/live/test",
                after_frame_limit=10,
                after_seconds=10,
            )

        self.assertEqual(frames, [])
        self.assertEqual(read_frame_mock.call_count, 3)
        release_capture_mock.assert_called_once_with(capture)

    def test_channel_worker_submits_alarm_video_job_without_inline_video_encoding(self):
        service = AnalysisService()
        stop_event = threading.Event()
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        detection = {
            "class_name": "person",
            "conf": 0.91,
            "x1": 1,
            "y1": 1,
            "x2": 10,
            "y2": 10,
        }

        class FakeCapture:
            def isOpened(self):
                return True

            def read(self):
                stop_event.set()
                return True, frame

            def release(self):
                pass

        channel = {
            "id": "ch_async_worker",
            "source_url": "rtsp://example/live/ch_async_worker",
            "ext": {
                "ai_config": {
                    "enabled": True,
                    "models": [
                        {
                            "model_id": "model_async_worker",
                            "enabled": True,
                            "frame_interval": 1,
                            "save_snapshot": True,
                        }
                    ],
                }
            },
        }

        with (
            patch.object(service, "_load_channel", return_value=channel),
            patch.object(service, "_save_channel"),
            patch("src.services.analysis_service.cv2.VideoCapture", return_value=FakeCapture()),
            patch.object(service, "_load_model", return_value=(object(), {"classes": ["person"]})),
            patch.object(service, "_detect_frame", return_value=[detection]),
            patch.object(service, "_apply_model_scoped_filters", return_value=[detection]),
            patch.object(service, "_save_event_snapshot", return_value="/tmp/channel_snapshot.jpg"),
            patch.object(service.events_service, "create_event", AsyncMock(return_value={"id": "evt_channel_async", "event_type": "detection"})),
            patch.object(service, "_submit_channel_alarm_video_job") as submit_video_job_mock,
            patch.object(service, "_save_alarm_video", side_effect=AssertionError("channel worker should not encode video inline")),
            patch("src.services.analysis_service.time.sleep", lambda *_args, **_kwargs: None),
        ):
            service._run_worker("ch_async_worker", stop_event)

        self.assertEqual(submit_video_job_mock.call_count, 1)

    def test_apply_rule_type_detection_returns_every_detection(self):
        service = AnalysisService()
        detections = [
            {"class_name": "person", "conf": 0.8},
            {"class_name": "car", "conf": 0.7},
        ]

        events = service._build_rule_events(
            task={},
            algorithm={"rule_type": "detection"},
            detections=detections,
            now_ts=100.0,
            state={},
        )

        self.assertEqual(len(events), 2)

    def test_apply_rule_type_intrusion_returns_every_detection(self):
        service = AnalysisService()
        detections = [{"class_name": "person", "conf": 0.8, "zone_id": "z1"}]

        events = service._build_rule_events(
            task={},
            algorithm={"rule_type": "intrusion"},
            detections=detections,
            now_ts=100.0,
            state={},
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["class_name"], "person")

    def test_detection_algorithm_cooldown_key_is_one_event_per_algorithm(self):
        service = AnalysisService()
        algorithm = {
            "id": "algo_intrusion",
            "model_id": "model_person",
            "algorithm_type": "detection",
            "rule_type": "detection",
        }

        person_key = service._event_cooldown_key_for_algorithm(
            algorithm,
            {"class_name": "person", "zone_id": "zone_a"},
        )
        car_key = service._event_cooldown_key_for_algorithm(
            algorithm,
            {"class_name": "car", "zone_id": "zone_b"},
        )

        self.assertEqual(person_key, "detection")
        self.assertEqual(car_key, "detection")

    def test_non_detection_rule_cooldown_key_keeps_class_and_zone_scope(self):
        service = AnalysisService()
        key = service._event_cooldown_key_for_algorithm(
            {"id": "algo_gathering", "algorithm_type": "detection", "rule_type": "gathering"},
            {"class_name": "person", "zone_id": "zone_a"},
        )

        self.assertEqual(key, "person::zone_a")

    def test_zone_filter_keeps_detection_when_bbox_intersects_polygon_even_if_center_is_outside(self):
        service = AnalysisService()
        detections = [
            {"class_name": "person", "conf": 0.8, "x1": 80, "y1": 40, "x2": 140, "y2": 90}
        ]
        channel = {
            "ext": {
                "ai_config": {
                    "zone_ids": ["zone_1"],
                },
                "zones": [
                    {
                        "id": "zone_1",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
            }
        }

        filtered = service._filter_detections_by_zones(detections, channel)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["zone_id"], "zone_1")

    def test_zone_filter_drops_detection_when_bbox_does_not_intersect_polygon(self):
        service = AnalysisService()
        detections = [
            {"class_name": "person", "conf": 0.8, "x1": 120, "y1": 120, "x2": 180, "y2": 180}
        ]
        channel = {
            "ext": {
                "ai_config": {
                    "zone_ids": ["zone_1"],
                },
                "zones": [
                    {
                        "id": "zone_1",
                        "coordinates": [[0, 0], [100, 0], [100, 100], [0, 100]],
                    }
                ],
            }
        }

        filtered = service._filter_detections_by_zones(detections, channel)

        self.assertEqual(filtered, [])

    def test_count_metric_helpers_summarize_detections_for_realtime_dashboard(self):
        service = AnalysisService()
        detections = [
            {"class_name": "person", "conf": 0.8, "zone_id": "z1"},
            {"class_name": "person", "conf": 0.7, "zone_id": "z1"},
            {"class_name": "person", "conf": 0.9, "zone_id": "z1"},
        ]

        self.assertEqual(service._count_classes(detections), {"person": 3})
        self.assertEqual(service._metric_type_for_algorithm({"labels": ["person"]}), "person_count")
        self.assertEqual(service._metric_type_for_algorithm({"labels": ["car", "truck"]}), "vehicle_count")

    def test_apply_rule_type_count_does_not_emit_alarm_event(self):
        service = AnalysisService()
        detections = [
            {"class_name": "person", "conf": 0.8},
            {"class_name": "person", "conf": 0.7},
        ]

        events = service._build_rule_events(
            task={},
            algorithm={"rule_type": "count"},
            detections=detections,
            now_ts=100.0,
            state={},
        )

        self.assertEqual(events, [])

    def test_apply_rule_type_gathering_emits_after_zone_count_reaches_threshold_for_hold_seconds(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_gathering",
            "rule_type": "gathering",
            "scene_name": "人员聚集",
            "postprocess_config": {"count_threshold": 3, "hold_seconds": 2},
            "labels": ["person"],
            "regions": [
                {
                    "id": "zone_a",
                    "name": "区域A",
                    "coordinates": [[20, 20], [120, 20], [120, 120], [20, 120]],
                }
            ],
        }
        detections = [
            {"class_name": "person", "conf": 0.9, "zone_id": "zone_a", "x1": 30, "y1": 30, "x2": 50, "y2": 90},
            {"class_name": "person", "conf": 0.88, "zone_id": "zone_a", "x1": 55, "y1": 35, "x2": 75, "y2": 95},
            {"class_name": "person", "conf": 0.86, "zone_id": "zone_a", "x1": 80, "y1": 32, "x2": 100, "y2": 92},
        ]

        first = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=100.0, state=state)
        second = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=101.0, state=state)
        third = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=102.0, state=state)

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(len(third), 1)
        self.assertEqual(third[0]["zone_id"], "zone_a")
        self.assertEqual(third[0]["zone_name"], "区域A")
        self.assertEqual(third[0]["object_count"], 3)
        self.assertEqual(third[0]["annotation_label"], "人员聚集 3")

    def test_apply_rule_type_congestion_resets_after_count_drops_below_threshold(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_congestion",
            "rule_type": "congestion",
            "scene_name": "车辆拥堵识别",
            "postprocess_config": {"count_threshold": 2, "hold_seconds": 2},
            "labels": ["car", "truck"],
            "regions": [
                {
                    "id": "zone_road",
                    "name": "道路区域",
                    "coordinates": [[0, 0], [200, 0], [200, 120], [0, 120]],
                }
            ],
        }
        crowded = [
            {"class_name": "car", "conf": 0.9, "zone_id": "zone_road", "x1": 10, "y1": 20, "x2": 50, "y2": 60},
            {"class_name": "truck", "conf": 0.85, "zone_id": "zone_road", "x1": 70, "y1": 25, "x2": 140, "y2": 80},
        ]
        sparse = [
            {"class_name": "car", "conf": 0.9, "zone_id": "zone_road", "x1": 10, "y1": 20, "x2": 50, "y2": 60},
        ]

        with patch(
            "src.services.analysis_service.get_algorithm_rule_config",
            return_value={"count_threshold": 2, "hold_seconds": 2},
        ):
            service._build_rule_events(task={}, algorithm=algorithm, detections=crowded, now_ts=100.0, state=state)
            first_event = service._build_rule_events(task={}, algorithm=algorithm, detections=crowded, now_ts=102.0, state=state)
            reset = service._build_rule_events(task={}, algorithm=algorithm, detections=sparse, now_ts=103.0, state=state)
            service._build_rule_events(task={}, algorithm=algorithm, detections=crowded, now_ts=104.0, state=state)
            second_event = service._build_rule_events(task={}, algorithm=algorithm, detections=crowded, now_ts=106.0, state=state)

        self.assertEqual(len(first_event), 1)
        self.assertEqual(first_event[0]["object_count"], 2)
        self.assertEqual(first_event[0]["annotation_label"], "车辆拥堵识别 2")
        self.assertEqual(reset, [])
        self.assertEqual(len(second_event), 1)
        self.assertEqual(second_event[0]["zone_id"], "zone_road")

    def test_apply_rule_type_loitering_emits_after_linger_threshold(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_loitering",
            "rule_type": "loitering",
            "scene_name": "车辆滞留识别",
            "postprocess_config": {"linger_seconds": 10},
            "labels": ["car", "truck"],
            "regions": [
                {
                    "id": "zone_parking",
                    "name": "停车区域",
                    "coordinates": [[10, 10], [180, 10], [180, 120], [10, 120]],
                }
            ],
        }
        detections = [
            {"class_name": "car", "conf": 0.91, "zone_id": "zone_parking", "x1": 30, "y1": 30, "x2": 90, "y2": 90},
        ]

        first = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=100.0, state=state)
        second = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=108.0, state=state)
        third = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=110.0, state=state)

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(len(third), 1)
        self.assertEqual(third[0]["zone_id"], "zone_parking")
        self.assertEqual(third[0]["zone_name"], "停车区域")
        self.assertEqual(third[0]["loiter_seconds"], 10)
        self.assertEqual(third[0]["annotation_label"], "车辆滞留识别 10s")

    def test_apply_rule_type_loitering_resets_after_region_clears(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_loitering_reset",
            "rule_type": "loitering",
            "scene_name": "车辆滞留识别",
            "postprocess_config": {"linger_seconds": 5},
            "labels": ["car"],
            "regions": [
                {
                    "id": "zone_gate",
                    "name": "道闸区域",
                    "coordinates": [[0, 0], [160, 0], [160, 120], [0, 120]],
                }
            ],
        }
        occupied = [
            {"class_name": "car", "conf": 0.88, "zone_id": "zone_gate", "x1": 20, "y1": 20, "x2": 70, "y2": 80},
        ]

        with patch(
            "src.services.analysis_service.get_algorithm_rule_config",
            return_value={"linger_seconds": 5},
        ):
            service._build_rule_events(task={}, algorithm=algorithm, detections=occupied, now_ts=100.0, state=state)
            service._build_rule_events(task={}, algorithm=algorithm, detections=[], now_ts=102.0, state=state)
            service._build_rule_events(task={}, algorithm=algorithm, detections=occupied, now_ts=103.0, state=state)
            second = service._build_rule_events(task={}, algorithm=algorithm, detections=occupied, now_ts=108.0, state=state)

        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["zone_id"], "zone_gate")
        self.assertEqual(second[0]["loiter_seconds"], 5)

    def test_apply_rule_type_fall_like_emits_after_ratio_and_hold_threshold(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_fall_like",
            "rule_type": "fall_like",
            "scene_name": "人员倒地",
            "postprocess_config": {
                "fall_ratio_threshold": 1.2,
                "hold_seconds": 2,
                "min_box_height": 40,
            },
            "labels": ["person"],
            "regions": [
                {
                    "id": "zone_fall",
                    "name": "倒地区域",
                    "coordinates": [[0, 0], [200, 0], [200, 140], [0, 140]],
                }
            ],
        }
        suspicious = [
            {"class_name": "person", "conf": 0.92, "zone_id": "zone_fall", "x1": 20, "y1": 40, "x2": 120, "y2": 100},
        ]

        first = service._build_rule_events(task={}, algorithm=algorithm, detections=suspicious, now_ts=100.0, state=state)
        second = service._build_rule_events(task={}, algorithm=algorithm, detections=suspicious, now_ts=101.0, state=state)
        third = service._build_rule_events(task={}, algorithm=algorithm, detections=suspicious, now_ts=102.0, state=state)

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(len(third), 1)
        self.assertEqual(third[0]["zone_id"], "zone_fall")
        self.assertEqual(third[0]["zone_name"], "倒地区域")
        self.assertEqual(third[0]["fall_seconds"], 2)
        self.assertEqual(third[0]["annotation_label"], "人员倒地 2s")
        self.assertGreaterEqual(third[0]["fall_ratio"], 1.2)

    def test_apply_rule_type_fall_like_ignores_boxes_below_ratio_or_min_height(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_fall_like_filter",
            "rule_type": "fall_like",
            "scene_name": "人员倒地",
            "postprocess_config": {
                "fall_ratio_threshold": 1.2,
                "hold_seconds": 2,
                "min_box_height": 40,
            },
            "labels": ["person"],
        }
        invalid = [
            {"class_name": "person", "conf": 0.9, "x1": 10, "y1": 10, "x2": 60, "y2": 100},
            {"class_name": "person", "conf": 0.88, "x1": 80, "y1": 10, "x2": 120, "y2": 35},
        ]

        first = service._build_rule_events(task={}, algorithm=algorithm, detections=invalid, now_ts=100.0, state=state)
        second = service._build_rule_events(task={}, algorithm=algorithm, detections=invalid, now_ts=103.0, state=state)

        self.assertEqual(first, [])
        self.assertEqual(second, [])

    def test_apply_rule_type_fall_like_resets_after_suspicious_target_disappears(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_fall_like_reset",
            "rule_type": "fall_like",
            "scene_name": "人员倒地",
            "postprocess_config": {
                "fall_ratio_threshold": 1.2,
                "hold_seconds": 2,
                "min_box_height": 40,
            },
            "labels": ["person"],
            "regions": [
                {
                    "id": "zone_reset",
                    "name": "重置区域",
                    "coordinates": [[0, 0], [200, 0], [200, 140], [0, 140]],
                }
            ],
        }
        suspicious = [
            {"class_name": "person", "conf": 0.9, "zone_id": "zone_reset", "x1": 30, "y1": 40, "x2": 130, "y2": 100},
        ]

        service._build_rule_events(task={}, algorithm=algorithm, detections=suspicious, now_ts=100.0, state=state)
        service._build_rule_events(task={}, algorithm=algorithm, detections=[], now_ts=101.0, state=state)
        first_after_reset = service._build_rule_events(task={}, algorithm=algorithm, detections=suspicious, now_ts=102.0, state=state)
        second_after_reset = service._build_rule_events(task={}, algorithm=algorithm, detections=suspicious, now_ts=103.0, state=state)
        third_after_reset = service._build_rule_events(task={}, algorithm=algorithm, detections=suspicious, now_ts=104.0, state=state)

        self.assertEqual(first_after_reset, [])
        self.assertEqual(second_after_reset, [])
        self.assertEqual(len(third_after_reset), 1)
        self.assertEqual(third_after_reset[0]["zone_id"], "zone_reset")
        self.assertEqual(third_after_reset[0]["fall_seconds"], 2)

    def test_absence_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_absence_backend_cfg",
            "rule_type": "absence",
            "scene_name": "人员离岗",
            "postprocess_config": {"absence_seconds": 5},
            "labels": ["person"],
        }

        with patch(
            "src.services.analysis_service.get_algorithm_rule_config",
            return_value={"absence_seconds": 30},
            create=True,
        ):
            service._build_rule_events(task={}, algorithm=algorithm, detections=[], now_ts=100.0, state=state)
            before_backend_threshold = service._build_rule_events(
                task={}, algorithm=algorithm, detections=[], now_ts=106.0, state=state
            )
            after_backend_threshold = service._build_rule_events(
                task={}, algorithm=algorithm, detections=[], now_ts=131.0, state=state
            )

        self.assertEqual(before_backend_threshold, [])
        self.assertEqual(len(after_backend_threshold), 1)
        self.assertEqual(after_backend_threshold[0]["absence_seconds"], 31)

    def test_gathering_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_gathering_backend_cfg",
            "rule_type": "gathering",
            "scene_name": "人员聚集",
            "postprocess_config": {"count_threshold": 2, "hold_seconds": 1},
            "labels": ["person"],
            "regions": [
                {
                    "id": "zone_cfg",
                    "name": "配置区域",
                    "coordinates": [[0, 0], [200, 0], [200, 120], [0, 120]],
                }
            ],
        }
        detections = [
            {"class_name": "person", "conf": 0.9, "zone_id": "zone_cfg", "x1": 20, "y1": 20, "x2": 60, "y2": 80},
            {"class_name": "person", "conf": 0.88, "zone_id": "zone_cfg", "x1": 70, "y1": 20, "x2": 110, "y2": 80},
            {"class_name": "person", "conf": 0.86, "zone_id": "zone_cfg", "x1": 120, "y1": 20, "x2": 160, "y2": 80},
        ]

        with patch(
            "src.services.analysis_service.get_algorithm_rule_config",
            return_value={"count_threshold": 4, "hold_seconds": 3},
            create=True,
        ):
            first = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=100.0, state=state)
            second = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=103.0, state=state)

        self.assertEqual(first, [])
        self.assertEqual(second, [])

    def test_loitering_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_loitering_backend_cfg",
            "rule_type": "loitering",
            "scene_name": "车辆滞留识别",
            "postprocess_config": {"linger_seconds": 3},
            "labels": ["car"],
            "regions": [
                {
                    "id": "zone_car",
                    "name": "车辆区域",
                    "coordinates": [[0, 0], [200, 0], [200, 120], [0, 120]],
                }
            ],
        }
        detections = [
            {"class_name": "car", "conf": 0.9, "zone_id": "zone_car", "x1": 30, "y1": 30, "x2": 90, "y2": 90},
        ]

        with patch(
            "src.services.analysis_service.get_algorithm_rule_config",
            return_value={"linger_seconds": 10},
            create=True,
        ):
            service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=100.0, state=state)
            before_backend_threshold = service._build_rule_events(
                task={}, algorithm=algorithm, detections=detections, now_ts=105.0, state=state
            )
            after_backend_threshold = service._build_rule_events(
                task={}, algorithm=algorithm, detections=detections, now_ts=110.0, state=state
            )

        self.assertEqual(before_backend_threshold, [])
        self.assertEqual(len(after_backend_threshold), 1)
        self.assertEqual(after_backend_threshold[0]["loiter_seconds"], 10)

    def test_fall_like_rule_uses_backend_rule_config_instead_of_algorithm_postprocess_config(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_fall_backend_cfg",
            "rule_type": "fall_like",
            "scene_name": "人员倒地",
            "postprocess_config": {
                "fall_ratio_threshold": 1.0,
                "hold_seconds": 1,
                "min_box_height": 20,
            },
            "labels": ["person"],
            "regions": [
                {
                    "id": "zone_fall_cfg",
                    "name": "倒地区域",
                    "coordinates": [[0, 0], [220, 0], [220, 160], [0, 160]],
                }
            ],
        }
        detections = [
            {"class_name": "person", "conf": 0.92, "zone_id": "zone_fall_cfg", "x1": 20, "y1": 40, "x2": 110, "y2": 100},
        ]

        with patch(
            "src.services.analysis_service.get_algorithm_rule_config",
            return_value={"fall_ratio_threshold": 1.6, "hold_seconds": 3, "min_box_height": 40},
            create=True,
        ):
            first = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=100.0, state=state)
            second = service._build_rule_events(task={}, algorithm=algorithm, detections=detections, now_ts=103.0, state=state)

        self.assertEqual(first, [])
        self.assertEqual(second, [])

    def test_apply_rule_type_absence_emits_only_after_threshold(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "rule_type": "absence",
            "postprocess_config": {"absence_seconds": 5},
            "labels": ["person"],
        }

        first = service._build_rule_events(
            task={},
            algorithm=algorithm,
            detections=[],
            now_ts=100.0,
            state=state,
        )
        second = service._build_rule_events(
            task={},
            algorithm=algorithm,
            detections=[],
            now_ts=103.0,
            state=state,
        )
        third = service._build_rule_events(
            task={},
            algorithm=algorithm,
            detections=[],
            now_ts=106.0,
            state=state,
        )

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(len(third), 1)
        self.assertEqual(third[0]["class_name"], "person")
        self.assertEqual(third[0]["absence_seconds"], 6)

    def test_apply_rule_type_absence_tracks_each_region_independently(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "id": "algo_absence_regions",
            "rule_type": "absence",
            "scene_name": "离岗检测",
            "postprocess_config": {"absence_seconds": 5},
            "labels": ["person"],
            "regions": [
                {
                    "id": "zone_a",
                    "name": "A区",
                    "coordinates": [[0, 0], [40, 0], [40, 40], [0, 40]],
                },
                {
                    "id": "zone_b",
                    "name": "B区",
                    "coordinates": [[60, 0], [100, 0], [100, 40], [60, 40]],
                },
            ],
        }

        service._build_rule_events(
            task={},
            algorithm=algorithm,
            detections=[{"class_name": "person", "conf": 0.9, "zone_id": "zone_a"}],
            now_ts=100.0,
            state=state,
        )
        service._build_rule_events(
            task={},
            algorithm=algorithm,
            detections=[{"class_name": "person", "conf": 0.9, "zone_id": "zone_a"}],
            now_ts=103.0,
            state=state,
        )
        third = service._build_rule_events(
            task={},
            algorithm=algorithm,
            detections=[{"class_name": "person", "conf": 0.9, "zone_id": "zone_a"}],
            now_ts=106.0,
            state=state,
        )

        self.assertEqual(len(third), 1)
        self.assertEqual(third[0]["zone_id"], "zone_b")
        self.assertEqual(third[0]["zone_name"], "B区")
        self.assertEqual(third[0]["absence_seconds"], 6)
        self.assertEqual(third[0]["annotation_label"], "离岗检测 6s")

    def test_apply_rule_type_absence_resets_after_person_reappears(self):
        service = AnalysisService()
        state: dict = {}
        algorithm = {
            "rule_type": "absence",
            "postprocess_config": {"absence_seconds": 5},
            "labels": ["person"],
        }

        service._build_rule_events(task={}, algorithm=algorithm, detections=[], now_ts=100.0, state=state)
        service._build_rule_events(
            task={},
            algorithm=algorithm,
            detections=[{"class_name": "person", "conf": 0.9}],
            now_ts=102.0,
            state=state,
        )
        after_reset = service._build_rule_events(
            task={},
            algorithm=algorithm,
            detections=[],
            now_ts=104.0,
            state=state,
        )

        self.assertEqual(after_reset, [])

    def test_annotate_frame_for_absence_draws_zone_and_business_label_without_fake_bbox(self):
        service = AnalysisService()
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        detections = [
            {
                "class_name": "person",
                "conf": 1.0,
                "zone_id": "zone_absence",
                "zone_name": "离岗区",
                "absence_seconds": 30,
                "annotation_label": "离岗检测 30s",
                "label_anchor": [24, 24],
            }
        ]
        regions = [
            {
                "id": "zone_absence",
                "name": "离岗区",
                "color": "#ff4d4f",
                "coordinates": [[20, 20], [100, 20], [100, 90], [20, 90]],
            }
        ]

        annotated = service._annotate_frame(frame, detections, regions=regions)

        self.assertEqual(tuple(frame[20, 20]), (0, 0, 0))
        self.assertNotEqual(tuple(annotated[20, 20]), (0, 0, 0))
        self.assertGreater(int(annotated.sum()), 0)

class AnalysisServiceShutdownTest(unittest.IsolatedAsyncioTestCase):
    async def test_stop_all_waits_for_pending_alarm_video_jobs(self):
        service = AnalysisService()
        future = service._alarm_video_executor.submit(time.sleep, 0.1)
        service._track_alarm_video_future(future)

        result = await service.stop_all(timeout_seconds=1.0)

        self.assertTrue(future.done())
        self.assertEqual(len(service._alarm_video_futures), 0)
        self.assertEqual(result["pending_alarm_video_jobs"], 0)


if __name__ == "__main__":
    unittest.main()
