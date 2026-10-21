"""gpt-image 家族（gpt-image-2 / gpt-image-2.5-flare / gpt-image-2.5-sunburst）的比例与分辨率契约。

服务商文档：api-515692647（2.5-flare 文生图）/ api-515693438（2.5-flare 图像编辑）/
api-515693868、api-515694152（2.5-sunburst）与 api-447258891 / api-447261009（gpt-image-2）。
逆向分组只接受 model、prompt、size、image、quality；size 的硬约束是
最大边 ≤3840px、两边均为 16 的倍数、长边:短边 ≤3:1、总像素 655360-8294400。
因此画布的「比例 + 档位」必须在本地换算成合规像素，不能把 aspect_ratio/resolution 透传。
"""
import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import app.services as SERVICES
import main as APP
from app.core import (GPT_IMAGE2_MAX_EDGE, GPT_IMAGE2_MAX_PIXELS, GPT_IMAGE2_MIN_PIXELS,
                      gpt_image_2_request_size, is_gpt_image_2_model)
from tests.provider_contract import ProviderContractCapture

PROMPT = "gpt image 2.5 contract prompt"
AUTHORIZATION = "Bearer gpt-image-2-5-contract-secret"
# 服务商文档给出的可用比例里，满足 3:1 长宽比限制的全部比例
DOCUMENTED_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "9:21"]
TIERS = ["1k", "2k", "4k"]
PROVIDER_VARIANTS = [
    "gpt-image-2.5-flare", "gpt-image-2.5-flare-2k", "gpt-image-2.5-flare-4k",
    "gpt-image-2.5-sunburst", "gpt-image-2.5-sunburst-2k", "gpt-image-2.5-sunburst-4k",
]


def provider_fixture(model):
    return {
        "id": "contract-gpt-image-2-5",
        "name": "gpt-image 2.5 contract fixture",
        "enabled": True,
        "protocol": "openai",
        "base_url": "https://provider.invalid/v1",
        "image_request_mode": "openai",
        "image_models": [model],
        "available_models": {"image_models": [model]},
        "image_model_capabilities": {
            model: {
                "media_types": ["image"],
                "sizes": ["1024x1024", "2048x1152", "3840x2160", "3840x1648"],
                "aspect_ratios": DOCUMENTED_RATIOS,
                "resolutions": TIERS,
                "max_reference_images": 20,
            }
        },
    }


def posted_json(model, size, aspect_ratio, resolution):
    """跑一次真实生成路径，返回服务商实际收到的 JSON body。"""
    fixture = provider_fixture(model)
    capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/fake.png"}]})
    parameters = {
        "width": None, "height": None,
        "aspect_ratio": aspect_ratio, "resolution": resolution, "media_type": "image",
    }
    with (
        patch.object(APP, "get_api_provider_exact", return_value=fixture),
        patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
        patch.object(SERVICES, "provider_endpoint_url", return_value="https://provider.invalid/v1/images/generations"),
        patch.object(APP.httpx, "AsyncClient", capture.async_client),
    ):
        asyncio.run(APP.generate_ai_image(
            PROMPT, size, "high", model, [], fixture["id"],
            preserve_model=True, image_parameters=parameters,
        ))
    return capture.only_request()["json"]


class GptImage2SizeConversionTests(unittest.TestCase):
    """比例 + 档位 必须换算成符合服务商约束的像素。"""

    def test_every_documented_ratio_and_tier_yields_a_valid_size(self):
        for ratio in DOCUMENTED_RATIOS:
            for tier in TIERS:
                size = gpt_image_2_request_size("1024x1024", ratio, tier)
                width, height = (int(part) for part in size.split("x"))
                requested = (lambda a, b: a / b)(*(int(part) for part in ratio.split(":")))
                with self.subTest(ratio=ratio, tier=tier, size=size):
                    self.assertLessEqual(max(width, height), GPT_IMAGE2_MAX_EDGE)
                    self.assertGreaterEqual(width * height, GPT_IMAGE2_MIN_PIXELS)
                    self.assertLessEqual(width * height, GPT_IMAGE2_MAX_PIXELS)
                    self.assertEqual(width % 16, 0)
                    self.assertEqual(height % 16, 0)
                    self.assertLessEqual(max(width, height) / min(width, height), 3.0001)
                    self.assertLess(abs(width / height - requested) / requested, 0.03)

    def test_documented_popular_sizes_are_reproduced_exactly(self):
        # 文档「常用尺码」必须原样命中，不能被取整改掉
        self.assertEqual(gpt_image_2_request_size("1024x1024", "1:1", "1k"), "1024x1024")
        self.assertEqual(gpt_image_2_request_size("1024x1024", "1:1", "2k"), "2048x2048")
        self.assertEqual(gpt_image_2_request_size("1024x1024", "16:9", "2k"), "2048x1152")
        self.assertEqual(gpt_image_2_request_size("1024x1024", "16:9", "4k"), "3840x2160")
        self.assertEqual(gpt_image_2_request_size("1024x1024", "9:16", "4k"), "2160x3840")

    def test_square_tier_above_pixel_budget_is_pulled_back_not_rejected(self):
        # 文档没有 4K 正方形：4K 档的 1:1 会被总像素上限压到 2880x2880
        size = gpt_image_2_request_size("1024x1024", "1:1", "4k")
        width, height = (int(part) for part in size.split("x"))
        self.assertEqual(width, height)
        self.assertLessEqual(width * height, GPT_IMAGE2_MAX_PIXELS)
        self.assertLess(width, GPT_IMAGE2_MAX_EDGE)

    def test_missing_or_broken_ratio_keeps_the_requested_pixels(self):
        self.assertEqual(gpt_image_2_request_size("1280x720", "", "auto"), "1280x720")
        self.assertEqual(gpt_image_2_request_size("1280x720", "not-a-ratio", ""), "1280x720")

    def test_provider_naming_variants_all_belong_to_the_gpt_image_2_family(self):
        for model in PROVIDER_VARIANTS:
            self.assertTrue(is_gpt_image_2_model(model), model)


class GptImage2OutgoingRequestTests(unittest.TestCase):
    """发往服务商的 body：只有 size，没有 aspect_ratio / resolution。"""

    def test_ratio_and_resolution_travel_as_size(self):
        body = posted_json("gpt-image-2.5-flare", "2048x1152", "16:9", "2k")
        self.assertEqual(body.get("model"), "gpt-image-2.5-flare")
        self.assertEqual(body.get("size"), "2048x1152")
        self.assertNotIn("aspect_ratio", body)
        self.assertNotIn("resolution", body)

    def test_extreme_ratio_is_converted_instead_of_dropped(self):
        body = posted_json("gpt-image-2.5-sunburst", "1024x1024", "21:9", "4k")
        self.assertEqual(body.get("size"), "3840x1648")
        self.assertNotIn("aspect_ratio", body)

    def test_oversized_pixel_size_is_pulled_back_even_without_ratio_parameters(self):
        # 设面板只传 size时，也不能把服务商一定会拒的 4096x4096 直接发出去
        body = posted_json("gpt-image-2", "4096x4096", "", "")
        self.assertEqual(body.get("size"), "2880x2880")

    def test_auto_size_is_left_alone(self):
        body = posted_json("gpt-image-2.5-flare", "auto", "", "auto")
        self.assertEqual(body.get("size"), "auto")

    def test_tier_variant_naming_does_not_change_the_size_contract(self):
        # -2k / -4k 只是服务商的档位命名，size 仍按画布的比例 + 档位换算
        self.assertEqual(
            posted_json("gpt-image-2.5-flare-4k", "1024x1024", "1:1", "1k").get("size"),
            posted_json("gpt-image-2.5-flare", "1024x1024", "1:1", "1k").get("size"),
        )


class GptImage2RegistryTests(unittest.TestCase):
    """model_specs.json 必须登记服务商文档里的参数形态（size 而不是 aspect_ratio）。"""

    def test_specs_register_size_based_parameters_for_every_variant(self):
        specs = json.loads((Path(__file__).resolve().parents[1] / "app" / "data" / "model_specs.json").read_text(encoding="utf-8"))
        image_models = specs.get("image_models") or {}
        for model in PROVIDER_VARIANTS:
            with self.subTest(model=model):
                spec = image_models.get(model)
                self.assertIsInstance(spec, dict)
                self.assertEqual(spec.get("api_type"), "images_generations")
                parameters = spec.get("parameters") or {}
                self.assertIn("size", parameters)
                self.assertIn("quality", parameters)
                self.assertNotIn("aspect_ratio", parameters)
                self.assertNotIn("image_size", parameters)


if __name__ == "__main__":
    unittest.main()
