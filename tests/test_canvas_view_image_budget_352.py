"""#352：PNG 预算不能靠裁剪、放大小图或改写输入来满足。"""
import base64
import io
import random
import unittest
from unittest.mock import patch

from PIL import Image

from app.services import canvas_view


class CanvasViewImageBudgetTests(unittest.TestCase):
    def assert_preview(self, source, max_px=1024):
        original_size = source.size
        original_pixels = source.tobytes()
        png, size = canvas_view.encode_view_png(source, max_px)
        self.assertEqual(source.size, original_size)
        self.assertEqual(source.tobytes(), original_pixels)
        self.assertLessEqual(len(png), 1048576)
        self.assertLessEqual(len(base64.b64encode(png)), 1398104)
        with Image.open(io.BytesIO(png)) as preview:
            self.assertEqual(preview.format, "PNG")
            self.assertEqual(preview.size, size)
            self.assertGreaterEqual(min(size), 1)
            self.assertLessEqual(max(size), canvas_view.clamp_view_max_px(max_px))
            self.assertLessEqual(size[0], original_size[0])
            self.assertLessEqual(size[1], original_size[1])
            return preview.copy()

    def test_small_inputs_keep_all_pixels_without_upscaling(self):
        for size in ((1, 1), (1, 31), (31, 1), (31, 17), (64, 32)):
            with self.subTest(size=size):
                pixels = random.Random(352).randbytes(size[0] * size[1] * 3)
                source = Image.frombytes("RGB", size, pixels)
                preview = self.assert_preview(source, max_px=99999)
                self.assertEqual(preview.size, size)
                self.assertEqual(preview.tobytes(), pixels)

    def test_compressible_input_keeps_requested_resolution_and_aspect(self):
        source = Image.new("RGB", (2048, 1024), (230, 230, 230))
        for max_px, expected in ((None, (1024, 512)), (99999, (1568, 784)), (256, (256, 128))):
            with self.subTest(max_px=max_px):
                self.assertEqual(self.assert_preview(source, max_px).size, expected)

    def test_high_entropy_landscape_and_portrait_preserve_aspect(self):
        for size in ((1536, 768), (768, 1536)):
            with self.subTest(size=size):
                source = Image.frombytes("RGB", size, random.Random(352).randbytes(size[0] * size[1] * 3))
                preview = self.assert_preview(source, max_px=99999)
                self.assertLess(max(preview.size), 1536)
                self.assertGreaterEqual(max(preview.size), 768)
                self.assertLessEqual(abs(preview.width - preview.height * size[0] / size[1]), 1)

    def test_large_metadata_does_not_spend_preview_budget_or_mutate_source(self):
        source = Image.new("RGB", (8, 8), (230, 230, 230))
        profile = random.Random(352).randbytes(1048577)
        source.info["icc_profile"] = profile
        preview = self.assert_preview(source)
        self.assertEqual(preview.size, (8, 8))
        self.assertNotIn("icc_profile", preview.info)
        self.assertEqual(source.info, {"icc_profile": profile})

    def test_tiny_budget_can_terminate_at_one_pixel(self):
        source = Image.frombytes("RGB", (8, 8), random.Random(352).randbytes(8 * 8 * 3))
        with patch.object(canvas_view, "MAX_VIEW_PNG_BYTES", 70):
            png, size = canvas_view.encode_view_png(source)
        self.assertLessEqual(len(png), 70)
        self.assertEqual(size, (1, 1))
        with Image.open(io.BytesIO(png)) as preview:
            self.assertEqual(preview.size, size)
        self.assertEqual(source.size, (8, 8))

    def test_max_px_clamp_keeps_default_and_lower_bound(self):
        for value in (None, "invalid"):
            with self.subTest(value=value):
                self.assertEqual(canvas_view.clamp_view_max_px(value), 1024)
        self.assertEqual(canvas_view.clamp_view_max_px(1), 64)
        self.assertEqual(canvas_view.clamp_view_max_px(99999), 1568)


if __name__ == "__main__":
    unittest.main()
