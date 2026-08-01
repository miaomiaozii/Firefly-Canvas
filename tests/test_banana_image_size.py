import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.core import banana_image_request_params, route_openai_image_request


class BananaImageSizeTests(unittest.TestCase):
    def test_banana_params_carry_ratio_and_resolution_tier(self):
        params = banana_image_request_params("1024x1024")
        self.assertEqual(params["aspect_ratio"], "1:1")
        self.assertEqual(params["image_size"], "1K")

    def test_banana_params_16_9_4k(self):
        params = banana_image_request_params("3840x2160")
        self.assertEqual(params["aspect_ratio"], "16:9")
        self.assertEqual(params["image_size"], "4K")

    def test_banana_route_keeps_base_model_and_passes_tier_params(self):
        provider = {
            "id": "comfly", "enabled": True, "protocol": "openai",
            "image_models": ["nano-banana-pro", "gemini-3.1-flash-image-preview", "gpt-image-2"],
        }
        route = route_openai_image_request(provider, "nano-banana-pro", "3840x2160")
        self.assertEqual(route["model"], "nano-banana-pro")
        self.assertEqual(route["params"]["aspect_ratio"], "16:9")
        self.assertEqual(route["params"]["image_size"], "4K")

    def test_banana_route_prefers_variant_when_base_absent(self):
        provider = {
            "id": "p", "enabled": True, "protocol": "openai",
            "image_models": ["nano-banana-pro-2k", "nano-banana-pro-4k"],
        }
        route = route_openai_image_request(provider, "nano-banana-pro", "3840x2160")
        self.assertEqual(route["model"], "nano-banana-pro-4k")
        self.assertEqual(route["params"]["image_size"], "4K")

    def test_gpt_image_keeps_no_banana_params(self):
        provider = {
            "id": "comfly", "enabled": True, "protocol": "openai",
            "image_models": ["gpt-image-2"],
        }
        route = route_openai_image_request(provider, "gpt-image-2", "2048x2048")
        self.assertEqual(route["model"], "gpt-image-2")
        self.assertEqual(route["params"], {})

    def test_route_uses_image_size_even_without_variant_models(self):
        provider = {
            "id": "comfly", "enabled": True, "protocol": "openai",
            "image_models": ["nano-banana-pro"],
        }
        route = route_openai_image_request(provider, "nano-banana-pro", "1024x1024")
        self.assertEqual(route["model"], "nano-banana-pro")
        self.assertEqual(route["params"]["image_size"], "1K")


if __name__ == "__main__":
    unittest.main()
