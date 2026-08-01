import asyncio
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.services import generation
from app.core import is_kling_model, is_kling_provider, video_adapter_label


class KlingModelTests(unittest.TestCase):
    def test_kling_model_detection(self):
        self.assertTrue(is_kling_model("kling-video-v3"))
        self.assertTrue(is_kling_model("kling-v1-6"))
        self.assertTrue(is_kling_model("kling-v1-5"))
        self.assertTrue(is_kling_model("kling-video-v1"))
        self.assertTrue(is_kling_model("kling-video-v1-6"))
        self.assertFalse(is_kling_model("veo3.1"))
        self.assertFalse(is_kling_model("sora-2"))

    def test_kling_provider_detection(self):
        self.assertTrue(is_kling_provider({}, "kling-video-v1"))
        self.assertTrue(is_kling_provider({}, "kling-v1-6"))
        self.assertFalse(is_kling_provider({}, "veo3.1"))


class VideoAdapterLabelTests(unittest.TestCase):
    def test_official_kling(self):
        label, ready = video_adapter_label("kling-v1-6", {})
        self.assertTrue(ready)
        self.assertIn("可灵", label)

    def test_official_volcengine(self):
        provider = {"id": "volcengine", "protocol": "volcengine", "base_url": "https://ark.cn-beijing.volces.com/api/v3"}
        label, ready = video_adapter_label("doubao-seedance-2-0-260128", provider)
        self.assertTrue(ready)
        self.assertIn("火山", label)

    def test_transit_veo31(self):
        label, ready = video_adapter_label("veo3.1", {})
        self.assertTrue(ready)
        self.assertIn("veo3.1", label)

    def test_transit_wan(self):
        label, ready = video_adapter_label("wan2.6-t2v", {})
        self.assertTrue(ready)
        self.assertIn("wan", label)

    def test_kling_v3_is_official_kling(self):
        label, ready = video_adapter_label("kling-video-v3", {})
        self.assertTrue(ready)
        self.assertIn("可灵", label)

    def test_unadapted_sora(self):
        label, ready = video_adapter_label("sora-2", {})
        self.assertFalse(ready)
        self.assertIn("未适配", label)

    def test_unified_format_families(self):
        for model in ("doubao-seedance-2-0-260128", "grok-video-3", "MiniMax-Hailuo-2.3", "hailuo-video"):
            label, ready = video_adapter_label(model, {})
            self.assertTrue(ready, model)
            self.assertIn("统一格式", label)


class KlingFormatTests(unittest.TestCase):
    def test_kling_model_name_maps_v3_to_v1_6(self):
        self.assertEqual(generation.kling_model_name("kling-video-v3"), "kling-v1-6")
        self.assertEqual(generation.kling_model_name("kling-v1-6"), "kling-v1-6")
        self.assertEqual(generation.kling_model_name("kling-v1-5"), "kling-v1-6")

    def test_kling_aspect_ratio_restricts_to_supported(self):
        self.assertEqual(generation.kling_aspect_ratio("16:9"), "16:9")
        self.assertEqual(generation.kling_aspect_ratio("9:16"), "9:16")
        self.assertEqual(generation.kling_aspect_ratio("1:1"), "1:1")
        self.assertEqual(generation.kling_aspect_ratio("21:9"), "16:9")

    def test_kling_duration_maps(self):
        self.assertEqual(generation.kling_duration(5), "5")
        self.assertEqual(generation.kling_duration(10), "10")
        self.assertEqual(generation.kling_duration(3), "5")
        self.assertEqual(generation.kling_duration(15), "10")

    def test_kling_video_output_urls_extracts_task_result(self):
        raw = {
            "code": 0,
            "data": {
                "task_status": "succeed",
                "task_result": {
                    "videos": [{"id": "v1", "url": "https://example.com/a.mp4", "duration": "5"}],
                },
            },
        }
        urls = generation.kling_video_output_urls(raw)
        self.assertEqual(urls, ["https://example.com/a.mp4"])

    def test_kling_video_output_urls_falls_back_to_common(self):
        raw = {"data": {"task_status": "succeed"}, "output": "https://example.com/b.mp4"}
        urls = generation.kling_video_output_urls(raw)
        self.assertEqual(urls, ["https://example.com/b.mp4"])


if __name__ == "__main__":
    unittest.main()
