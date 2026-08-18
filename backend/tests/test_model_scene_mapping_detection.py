import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.model_service import ModelService, SCENE_MAPPINGS


class DetectionSceneMappingTest(unittest.TestCase):
    def setUp(self):
        self.service = ModelService()

    def test_all_scene_mappings_default_to_alarm_snapshot_and_video(self):
        disabled_defaults = [
            mapping["scene_key"]
            for mapping in SCENE_MAPPINGS
            if not mapping.get("default_need_snapshot") or not mapping.get("default_need_video_clip")
        ]

        self.assertEqual(disabled_defaults, [])

    def test_puddle_model_maps_to_detection_scene(self):
        result = self.service._apply_label_recommendation(
            {
                "name": "路面积水识别",
                "classes": ["puddle", "person", "car", "truck", "bus", "bicycle"],
            }
        )

        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["puddle"])
        self.assertEqual(result["scene_key"], "puddle_detection")
        self.assertEqual(result["scene_name"], "路面积水识别")
        self.assertEqual(result["rule_type"], "detection")
        self.assertEqual(result["scene_candidates"][0]["scene_key"], "puddle_detection")
        self.assertEqual(result["scene_candidates"][0]["rule_type"], "detection")

    def test_reflective_vest_model_maps_to_detection_scene(self):
        result = self.service._apply_label_recommendation(
            {
                "name": "反光背心识别",
                "classes": ["vest", "no_vest"],
            }
        )

        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["no_vest"])
        self.assertEqual(result["scene_key"], "reflective_vest_detection")
        self.assertEqual(result["scene_name"], "反光背心识别")
        self.assertEqual(result["rule_type"], "detection")
        self.assertEqual(
            result["scene_candidates"][0]["scene_key"],
            "reflective_vest_detection",
        )
        self.assertEqual(result["scene_candidates"][0]["rule_type"], "detection")

    def test_sleep_model_only_alarms_on_sleep(self):
        result = self.service._apply_label_recommendation(
            {
                "name": "睡岗检测",
                "classes": ["normal", "sleep"],
            }
        )

        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["sleep"])

    def test_helmet_model_only_alarms_on_no_helmet(self):
        result = self.service._apply_label_recommendation(
            {
                "name": "安全帽检测",
                "classes": ["helmet", "no_helmet"],
            }
        )

        self.assertEqual(result["auto_label_strategy"], "recommended")
        self.assertEqual(result["recommended_labels"], ["no_helmet"])

    def test_unmapped_small_model_still_uses_all_classes(self):
        result = self.service._apply_label_recommendation(
            {
                "name": "通用二分类模型",
                "classes": ["left", "right"],
            }
        )

        self.assertEqual(result["auto_label_strategy"], "all")
        self.assertEqual(result["recommended_labels"], ["left", "right"])


if __name__ == "__main__":
    unittest.main()
