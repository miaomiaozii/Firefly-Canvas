"""Tests for ADR 0006 compliance: incompatible aspect ratios must stop generation."""
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


if __name__ == "__main__":
    unittest.main()
