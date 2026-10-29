"""Tests for ADR 0006 compliance: incompatible aspect ratios must stop generation."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.core as core


class AspectRatioValidationTests(unittest.TestCase):
    """Verify that unsupported aspect ratios raise IncompatibleGenerationParameterError."""

    def test_supported_aspect_ratio_passes_strict(self):
        """A supported aspect ratio should pass strict validation."""
        # 16:9 is supported
        result = core.gemini_supported_aspect_ratio("16:9", strict=True)
        self.assertEqual(result, "16:9")

    def test_unsupported_aspect_ratio_raises_in_strict_mode(self):
        """An unsupported aspect ratio should raise in strict mode (ADR 0006)."""
        # 5:3 is NOT in GEMINI_IMAGE_ASPECT_RATIOS
        with self.assertRaises(core.IncompatibleGenerationParameterError) as ctx:
            core.gemini_supported_aspect_ratio("5:3", strict=True)

        error = ctx.exception
        self.assertEqual(error.parameter, "aspect_ratio")
        self.assertEqual(error.requested_value, "5:3")
        self.assertIsNotNone(error.supported_values)
        self.assertIn("aspect_ratio", str(error))
        self.assertIn("5:3", str(error))
        self.assertIn("not supported", str(error))

    def test_unsupported_aspect_ratio_snaps_in_non_strict_mode(self):
        """Non-strict mode (default) should still snap to nearest for backward compat."""
        # 5:3 is unsupported but should snap silently
        result = core.gemini_supported_aspect_ratio("5:3", strict=False)
        # Should snap to one of the supported ratios (likely 16:9 or 3:2)
        supported = [item[2] for item in core.GEMINI_IMAGE_ASPECT_RATIOS]
        self.assertIn(result, supported)

    def test_strict_mode_with_pixel_dimensions(self):
        """Strict validation should work with pixel dimensions too."""
        # 1600x1200 = 4:3, which is supported
        result = core.gemini_supported_aspect_ratio("1600x1200", strict=True)
        self.assertEqual(result, "4:3")

    def test_strict_mode_with_unsupported_pixel_dimensions(self):
        """Unsupported pixel dimensions should raise in strict mode."""
        # 1000x600 = 5:3, which is unsupported
        with self.assertRaises(core.IncompatibleGenerationParameterError) as ctx:
            core.gemini_supported_aspect_ratio("1000x600", strict=True)

        error = ctx.exception
        self.assertEqual(error.parameter, "aspect_ratio")

    def test_banana_image_request_params_uses_strict(self):
        """banana_image_request_params should use strict validation."""
        # 16:9 is supported
        params = core.banana_image_request_params("16:9")
        self.assertEqual(params["aspect_ratio"], "16:9")

        # 5:3 is unsupported
        with self.assertRaises(core.IncompatibleGenerationParameterError):
            core.banana_image_request_params("5:3")

    def test_route_openai_image_request_banana_family_strict(self):
        """Routing for banana family should validate aspect ratio strictly."""
        provider = {
            "image_models": ["nano-banana", "nano-banana-hd"],
        }
        # 16:9 is supported
        route = core.route_openai_image_request(provider, "nano-banana", "16:9")
        self.assertEqual(route["params"]["aspect_ratio"], "16:9")

        # 5:3 is unsupported
        with self.assertRaises(core.IncompatibleGenerationParameterError) as ctx:
            core.route_openai_image_request(provider, "nano-banana", "5:3")

        error = ctx.exception
        self.assertEqual(error.parameter, "aspect_ratio")

    def test_route_openai_image_request_gemini_model_strict(self):
        """Routing for gemini image models should validate aspect ratio strictly."""
        provider = {
            "image_models": ["gemini-3.1-flash-image-preview"],
        }
        # 1:1 is supported
        route = core.route_openai_image_request(provider, "gemini-3.1-flash-image-preview", "1:1")
        self.assertEqual(route["params"]["aspect_ratio"], "1:1")

        # 5:3 is unsupported
        with self.assertRaises(core.IncompatibleGenerationParameterError) as ctx:
            core.route_openai_image_request(provider, "gemini-3.1-flash-image-preview", "5:3")

        error = ctx.exception
        self.assertEqual(error.parameter, "aspect_ratio")

    def test_non_banana_model_not_affected(self):
        """Models that don't use aspect ratio routing should not be affected."""
        # flux-kontext-pro doesn't use banana-style routing
        route = core.route_openai_image_request({}, "flux-kontext-pro", "5:3")
        # Should not raise, and should not have aspect_ratio in params
        self.assertNotIn("aspect_ratio", route["params"])

    def test_error_message_is_actionable(self):
        """The error message should tell the user what went wrong and what's supported."""
        with self.assertRaises(core.IncompatibleGenerationParameterError) as ctx:
            core.gemini_supported_aspect_ratio("5:3", strict=True)

        error = ctx.exception
        msg = str(error)
        # Should mention the parameter
        self.assertIn("aspect_ratio", msg)
        # Should mention the requested value
        self.assertIn("5:3", msg)
        # Should mention supported values
        self.assertIn("Supported values:", msg)
        # Should mention a few known supported values
        self.assertIn("1:1", msg)
        self.assertIn("16:9", msg)


class IncompatibleParameterApiTests(unittest.TestCase):
    """Verify that IncompatibleGenerationParameterError is converted to a 400 response."""

    def setUp(self):
        from fastapi.testclient import TestClient
        from main import app
        self.client = TestClient(app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

    def test_exception_handler_returns_400_with_details(self):
        """The exception handler should return 400 with structured error details."""
        exc = core.IncompatibleGenerationParameterError(
            parameter='aspect_ratio',
            requested_value='5:3',
            supported_values=['1:1', '16:9', '9:16'],
            model_id='nano-banana',
            detail='Test detail',
        )

        # Verify the exception has all the fields the handler needs
        self.assertEqual(exc.parameter, 'aspect_ratio')
        self.assertEqual(exc.requested_value, '5:3')
        self.assertEqual(exc.supported_values, ['1:1', '16:9', '9:16'])
        self.assertEqual(exc.model_id, 'nano-banana')
        self.assertIn('aspect_ratio', str(exc))
        self.assertIn('5:3', str(exc))
        self.assertIn('not supported', str(exc))

    def test_error_message_includes_supported_values(self):
        """The error message should list the supported values for user guidance."""
        exc = core.IncompatibleGenerationParameterError(
            parameter='aspect_ratio',
            requested_value='5:3',
            supported_values=['1:1', '4:3', '3:4', '16:9', '9:16'],
            model_id='nano-banana',
        )
        msg = str(exc)
        # Should list some supported values
        self.assertIn('1:1', msg)
        self.assertIn('16:9', msg)
        self.assertIn('Supported values:', msg)

    def test_exception_handler_registered(self):
        """Verify the exception handler is registered for IncompatibleGenerationParameterError."""
        from main import app
        # Check that the handler is registered in the app's exception handlers
        self.assertIn(
            core.IncompatibleGenerationParameterError,
            app.exception_handlers,
            "IncompatibleGenerationParameterError handler not registered in app"
        )

    def test_exception_handler_returns_400_response(self):
        """Verify the exception handler returns a 400 response with structured details."""
        from main import app
        # Get the registered handler
        handler = app.exception_handlers[core.IncompatibleGenerationParameterError]

        # Create a fake request and exception
        from starlette.requests import Request
        scope = {'type': 'http', 'method': 'POST', 'path': '/api/test'}
        request = Request(scope)

        exc = core.IncompatibleGenerationParameterError(
            parameter='aspect_ratio',
            requested_value='5:3',
            supported_values=['1:1', '16:9', '9:16'],
            model_id='nano-banana',
        )

        # Call the handler (it's async, so we need to run it)
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            response = loop.run_until_complete(handler(request, exc))
        finally:
            loop.close()

        # Verify the response
        self.assertEqual(response.status_code, 400)
        # Parse the JSON body
        import json
        body = json.loads(response.body.decode('utf-8'))
        self.assertIn('detail', body)
        self.assertIn('parameter', body)
        self.assertEqual(body['parameter'], 'aspect_ratio')
        self.assertIn('requested_value', body)
        self.assertEqual(body['requested_value'], '5:3')
        self.assertIn('supported_values', body)
        self.assertEqual(body['supported_values'], ['1:1', '16:9', '9:16'])
        self.assertIn('model_id', body)
        self.assertEqual(body['model_id'], 'nano-banana')
        # Error message should mention the unsupported ratio
        self.assertIn('aspect_ratio', body['detail'])
        self.assertIn('5:3', body['detail'])
        self.assertIn('not supported', body['detail'])


class Banana2DocumentedRatioTests(unittest.TestCase):
    """Nano Banana 2 家族必须支持全部 15 种宽高比（含 21:9 的镜像 9:21）。

    依据：服务商文档 api-420646217 / api-481108294 / api-512112774 / api-512273870；
    nano-banana 与 nano-banana-pro 为基础集（api-341817446 / api-420361512）+ 镜像比例。
    """

    DOCUMENTED_RATIOS = [
        "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "9:21",
        "1:4", "4:1", "8:1", "1:8",
    ]
    # 1k 档位像素尺寸，与前端 SIZE_MAP / firefly_generation.COMMON_IMAGE_SIZES 一一对应
    RATIO_SIZES_1K = {
        "1:1": "1024x1024", "2:3": "1024x1536", "3:2": "1536x1024", "3:4": "1008x1344",
        "4:3": "1344x1008", "4:5": "1152x1440", "5:4": "1440x1152", "9:16": "720x1280",
        "16:9": "1280x720", "21:9": "1280x544", "9:21": "544x1280", "1:4": "384x1536", "4:1": "1536x384",
        "8:1": "1536x192", "1:8": "192x1536",
    }

    def test_every_documented_ratio_is_accepted_in_strict_mode(self):
        """文档里的 14 种比例（比例字符串与像素尺寸两种写法）都必须通过严格校验。"""
        for ratio, size in self.RATIO_SIZES_1K.items():
            self.assertEqual(core.gemini_supported_aspect_ratio(ratio, strict=True), ratio, ratio)
            self.assertEqual(core.gemini_supported_aspect_ratio(size, strict=True), ratio, size)

    def test_banana_image_request_params_accept_extreme_ratios(self):
        """之前缺失的 6 种比例必须能生成 banana 请求参数。"""
        for ratio in ("4:5", "5:4", "1:4", "4:1", "8:1", "1:8"):
            self.assertEqual(core.banana_image_request_params(ratio)["aspect_ratio"], ratio, ratio)

    def test_mirror_ratio_9_21_is_supported(self):
        """有 21:9 就有它的镜像 9:21（服务商档位 1280x544 / 544x1280 成对存在）。"""
        self.assertEqual(core.gemini_supported_aspect_ratio("9:21", strict=True), "9:21")
        self.assertEqual(core.gemini_supported_aspect_ratio("544x1280", strict=True), "9:21")
        self.assertEqual(core.banana_image_request_params("9:21")["aspect_ratio"], "9:21")

    def test_model_specs_list_all_documented_banana2_ratios(self):
        """model_specs.json 是能力单一来源，必须覆盖 14 种比例。"""
        from app.services.model_specs import get_aspect_ratios

        for model in ("nano-banana-2", "gemini-nano-banana-2.1"):
            self.assertEqual(sorted(get_aspect_ratios(model)), sorted(self.DOCUMENTED_RATIOS), model)

    def test_firefly_generation_ratio_pools_match_docs(self):
        """能力推断：Banana 2 家族 14 种，nano-banana / nano-banana-pro 保持 10 种。"""
        import firefly_generation as fg

        self.assertEqual(sorted(fg.GEMINI_FLASH_IMAGE_ASPECT_RATIOS), sorted(self.DOCUMENTED_RATIOS))
        for model in ("gemini-3.1-flash-image-preview", "gemini-3.1-flash-lite-image", "nano-banana-2", "gemini-nano-banana-2.1"):
            self.assertEqual(sorted(fg._banana_aspect_ratios(model)), sorted(self.DOCUMENTED_RATIOS), model)
        for model in ("nano-banana", "nano-banana-hd", "nano-banana-pro", "gemini-3-pro-image-preview"):
            self.assertEqual(sorted(fg._banana_aspect_ratios(model)), sorted(fg.GEMINI_ASPECT_RATIOS), model)

    def test_frontend_ratio_presets_match_backend_pools(self):
        """前端 SIZE_MAP / IMAGE_RATIO_PRESETS 不能落后于后端能力（曾缺失 6 种比例）。"""
        source = (ROOT / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        size_map = re.search(r"const SIZE_MAP = \{(.*?)\n\};", source, re.S)
        presets = re.search(r"const IMAGE_RATIO_PRESETS = \[(.*?)\n\];", source, re.S)
        self.assertIsNotNone(size_map, "SIZE_MAP not found in smart-canvas.js")
        self.assertIsNotNone(presets, "IMAGE_RATIO_PRESETS not found in smart-canvas.js")
        for ratio in self.DOCUMENTED_RATIOS:
            self.assertIn(f"'{ratio}'", presets.group(1), ratio)
            self.assertIn(self.RATIO_SIZES_1K[ratio], size_map.group(1), ratio)
        for key in re.findall(r"\['(\w+)'", presets.group(1)):
            self.assertIn(f"{key}: {{", size_map.group(1), key)


if __name__ == "__main__":
    unittest.main()
