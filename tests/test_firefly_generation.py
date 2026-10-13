import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_generation import (
    ImageGenerationRequest,
    ModelRoutingError,
    VideoGenerationRequest,
    apply_angle_preset,
    route_image_generation,
    route_video_generation,
)


class ImageGenerationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.providers = [
            {
                "id": "http-provider",
                "name": "HTTP",
                "enabled": True,
                "protocol": "openai",
                "image_models": ["square-only", "flex-4k"],
                "image_model_capabilities": {
                    "square-only": {
                        "media_types": ["image"],
                        "sizes": ["1024x1024"],
                        "aspect_ratios": ["1:1"],
                        "resolutions": ["1k"],
                        "max_reference_images": 0,
                    },
                    "flex-4k": {
                        "media_types": ["image"],
                        "sizes": ["1024x1024", "3840x1648", "2048x1152"],
                        "aspect_ratios": ["1:1", "16:9", "21:9"],
                        "resolutions": ["1k", "2k", "4k"],
                        "max_reference_images": 12,
                    },
                },
            },
            {
                "id": "cli-provider",
                "name": "CLI",
                "enabled": True,
                "protocol": "codex",
                "image_models": ["cli-wide"],
                "image_model_capabilities": {
                    "cli-wide": {
                        "media_types": ["image"],
                        "sizes": ["2048x1152"],
                        "aspect_ratios": ["16:9"],
                        "resolutions": ["2k"],
                        "max_reference_images": 2,
                    }
                },
            },
        ]

    def request(self, **changes):
        values = {
            "prompt": "萤火虫飞过湖面",
            "size": "3840x1648",
            "width": 3840,
            "height": 1648,
            "aspect_ratio": "21:9",
            "resolution": "4k",
            "reference_asset_ids": ("asset-a", "asset-b"),
            "media_type": "image",
            "quality": "high",
            "count": 1,
        }
        values.update(changes)
        return ImageGenerationRequest(**values)

    def test_auto_routing_matches_all_capabilities_for_http_and_cli_adapters(self):
        route = route_image_generation(self.request(), self.providers)
        self.assertEqual((route.provider_id, route.model, route.adapter), ("http-provider", "flex-4k", "http"))

        cli_route = route_image_generation(
            self.request(
                size="2048x1152", width=2048, height=1152, aspect_ratio="16:9",
                resolution="2k", reference_asset_ids=("asset-a",),
            ),
            self.providers,
        )
        self.assertEqual((cli_route.provider_id, cli_route.model, cli_route.adapter), ("http-provider", "flex-4k", "http"))
        cli_pinned = route_image_generation(
            self.request(
                size="2048x1152", width=2048, height=1152, aspect_ratio="16:9",
                resolution="2k", reference_asset_ids=("asset-a",),
            ),
            self.providers,
            provider_id="cli-provider",
        )
        self.assertEqual((cli_pinned.provider_id, cli_pinned.model, cli_pinned.adapter), ("cli-provider", "cli-wide", "cli"))

    def test_manual_provider_or_exact_model_pin_uses_same_validation(self):
        request = self.request(
            size="1024x1024", width=1024, height=1024, aspect_ratio="1:1",
            resolution="1k", reference_asset_ids=(),
        )
        by_provider = route_image_generation(request, self.providers, provider_id="http-provider")
        self.assertEqual(by_provider.model, "square-only")
        by_model = route_image_generation(request, self.providers, model="flex-4k")
        self.assertEqual((by_model.provider_id, by_model.model), ("http-provider", "flex-4k"))
        exact = route_image_generation(request, self.providers, provider_id="http-provider", model="flex-4k")
        self.assertEqual((exact.provider_id, exact.model), ("http-provider", "flex-4k"))

        with self.assertRaises(ModelRoutingError) as raised:
            route_image_generation(self.request(), self.providers, model="square-only")
        self.assertIn("aspect_ratio", raised.exception.incompatible_parameters)
        self.assertIn("resolution", raised.exception.incompatible_parameters)
        self.assertIn("reference_count", raised.exception.incompatible_parameters)

    def test_pinned_model_upgrades_to_resolution_variant_when_platform_offers_it(self):
        providers = [
            {
                "id": "comfly",
                "name": "comfly",
                "enabled": True,
                "protocol": "openai",
                "image_models": [
                    "nano-banana-pro",
                    "nano-banana-pro-2k",
                    "nano-banana-pro-4k",
                ],
            }
        ]
        four_k = route_image_generation(
            ImageGenerationRequest(
                prompt="t", size="4096x4096", width=4096, height=4096,
                aspect_ratio="1:1", resolution="4k",
            ),
            providers,
            provider_id="comfly",
            model="nano-banana-pro",
        )
        self.assertEqual(four_k.model, "nano-banana-pro-4k")

        two_k = route_image_generation(
            ImageGenerationRequest(
                prompt="t", size="2048x2048", width=2048, height=2048,
                aspect_ratio="1:1", resolution="2k",
            ),
            providers,
            provider_id="comfly",
            model="nano-banana-pro",
        )
        self.assertEqual(two_k.model, "nano-banana-pro-2k")

        one_k = route_image_generation(
            ImageGenerationRequest(
                prompt="t", size="1024x1024", width=1024, height=1024,
                aspect_ratio="1:1", resolution="1k",
            ),
            providers,
            provider_id="comfly",
            model="nano-banana-pro",
        )
        self.assertEqual(one_k.model, "nano-banana-pro")

    def test_pinned_banana_models_route_all_selected_resolution_tiers(self):
        cases = (
            ("nano-banana", ("nano-banana", "nano-banana-hd", "nano-banana-hd")),
            ("nano-banana-pro", ("nano-banana-pro", "nano-banana-pro-2k", "nano-banana-pro-4k")),
            ("nano-banana-2", ("nano-banana-2", "nano-banana-2-2k", "nano-banana-2-4k")),
            ("gemini-nano-banana-2.1", (
                "gemini-nano-banana-2.1",
                "gemini-nano-banana-2.1-2k",
                "gemini-nano-banana-2.1-4k",
            )),
            ("gemini-3.1-flash-image-preview", (
                "gemini-3.1-flash-image-preview",
                "gemini-3.1-flash-image-preview-2k",
                "gemini-3.1-flash-image-preview-4k",
            )),
            ("gemini-3.1-flash-lite-image", (
                "gemini-3.1-flash-lite-image",
                "gemini-3.1-flash-lite-image",
                "gemini-3.1-flash-lite-image",
            )),
        )
        tiers = (("1k", 1024), ("2k", 2048), ("4k", 4096))
        for model, expected_models in cases:
            available_models = list(dict.fromkeys((model, *expected_models)))
            for (resolution, pixels), expected in zip(tiers, expected_models):
                with self.subTest(model=model, resolution=resolution):
                    providers = [{
                        "id": "comfly",
                        "name": "comfly",
                        "enabled": True,
                        "protocol": "openai",
                        "image_models": [model],
                        "image_model_capabilities": {model: {
                            "sizes": ["1024x1024", "2048x2048", "4096x4096"],
                            "aspect_ratios": ["1:1"],
                            "resolutions": ["1k", "2k", "4k"],
                            "max_reference_images": 20,
                        }},
                        "available_models": {"image_models": available_models},
                    }]
                    route = route_image_generation(
                        ImageGenerationRequest(
                            prompt="t", size=f"{pixels}x{pixels}", width=pixels, height=pixels,
                            aspect_ratio="1:1", resolution=resolution,
                        ),
                        providers,
                        provider_id="comfly",
                        model=model,
                    )
                    self.assertEqual(route.model, expected)

    def test_pinned_model_without_variant_still_reports_incompatible_resolution(self):
        providers = [
            {
                "id": "comfly",
                "name": "comfly",
                "enabled": True,
                "protocol": "openai",
                "image_models": ["nano-banana-pro"],
            }
        ]
        with self.assertRaises(ModelRoutingError) as raised:
            route_image_generation(
                ImageGenerationRequest(
                    prompt="t", size="4096x4096", width=4096, height=4096,
                    aspect_ratio="1:1", resolution="4k",
                ),
                providers,
                provider_id="comfly",
                model="nano-banana-pro",
            )
        self.assertIn("resolution", raised.exception.incompatible_parameters)

    def test_no_compatible_model_reports_concrete_incompatible_parameters(self):
        with self.assertRaises(ModelRoutingError) as raised:
            route_image_generation(
                self.request(reference_asset_ids=tuple(f"asset-{index}" for index in range(13))),
                self.providers,
            )
        error = raised.exception
        self.assertEqual(error.code, "no_compatible_image_model")
        self.assertEqual(error.incompatible_parameters, ["reference_count"])
        self.assertIn("reference_count", str(error))

    def test_routing_does_not_mutate_request_or_provider_configuration(self):
        request = self.request()
        request_before = copy.deepcopy(request.to_dict())
        providers_before = copy.deepcopy(self.providers)
        route = route_image_generation(request, self.providers)
        self.assertEqual(request.to_dict(), request_before)
        self.assertEqual(self.providers, providers_before)
        self.assertEqual(route.request, request_before)
        self.assertEqual(route.request["size"], "3840x1648")
        self.assertEqual(route.request["aspect_ratio"], "21:9")
        self.assertEqual(route.request["resolution"], "4k")
        self.assertEqual(route.request["reference_count"], 2)
        self.assertEqual(route.request["media_type"], "image")

    def test_angle_reference_is_only_a_prompt_preset(self):
        original = "产品放在木桌上"
        executed = apply_angle_preset(original, "rear-three-quarter")
        self.assertEqual(original, "产品放在木桌上")
        self.assertIn(original, executed)
        self.assertIn("后侧四分之三", executed)
        self.assertEqual(apply_angle_preset(original, "none"), original)

    def test_known_banana_resolution_aliases_are_routed_by_user_resolution(self):
        providers = [{
            "id": "banana", "enabled": True, "protocol": "openai",
            "image_models": ["nano-banana-pro", "nano-banana-pro-2k", "nano-banana-pro-4k"],
        }]
        route = route_image_generation(self.request(), providers)
        self.assertEqual(route.model, "nano-banana-pro-4k")
        pinned = route_image_generation(self.request(), providers, model="nano-banana-pro")
        self.assertEqual(pinned.model, "nano-banana-pro-4k")

    def test_banana_two_aliases_are_routed_by_user_resolution(self):
        providers = [{
            "id": "banana", "enabled": True, "protocol": "openai",
            "image_models": ["nano-banana-2", "nano-banana-2-2k", "nano-banana-2-4k"],
        }]
        route = route_image_generation(self.request(), providers)
        self.assertEqual(route.model, "nano-banana-2-4k")
        self.assertEqual(route.request["size"], "3840x1648")
        self.assertEqual(route.request["width"], 3840)
        self.assertEqual(route.request["height"], 1648)
        self.assertEqual(route.request["aspect_ratio"], "21:9")
        self.assertEqual(route.request["resolution"], "4k")

    def test_unknown_model_without_explicit_capabilities_is_permissive(self):
        """#135: 无显式能力的未知模型不再被误拒，size/宽高比/分辨率/参考图均可通过路由。"""
        providers = [{
            "id": "unknown", "enabled": True, "protocol": "openai",
            "image_models": ["mystery-image-model"],
        }]
        route = route_image_generation(self.request(), providers)
        self.assertEqual((route.provider_id, route.model), ("unknown", "mystery-image-model"))
        self.assertEqual(route.request["size"], "3840x1648")
        self.assertEqual(route.request["aspect_ratio"], "21:9")
        self.assertEqual(route.request["resolution"], "4k")
        self.assertEqual(route.request["reference_count"], 2)

    def test_issue_135_unknown_model_routes_size_aspect_ratio_resolution_and_references(self):
        """#135: 回归测试——provider 不声明 image_model_capabilities 的未知模型（如 z-image-turbo）
        带 size/aspect_ratio/resolution/参考图的合法请求必须能通过路由，不抛 no_compatible_image_model。"""
        providers = [{
            "id": "zimage", "enabled": True, "protocol": "openai",
            "image_models": ["z-image-turbo"],
        }]
        route = route_image_generation(
            self.request(
                size="1536x1024", width=1536, height=1024,
                aspect_ratio="3:2", resolution="2k",
                reference_asset_ids=("asset-a", "asset-b"),
            ),
            providers,
        )
        self.assertEqual((route.provider_id, route.model), ("zimage", "z-image-turbo"))
        self.assertEqual(route.request["size"], "1536x1024")
        self.assertEqual(route.request["width"], 1536)
        self.assertEqual(route.request["height"], 1024)
        self.assertEqual(route.request["aspect_ratio"], "3:2")
        self.assertEqual(route.request["resolution"], "2k")
        self.assertEqual(route.request["reference_count"], 2)


class VideoGenerationRoutingTests(unittest.TestCase):
    def request(self, **overrides):
        values = dict(
            prompt="a cat",
            size="1024x576",
            width=None,
            height=None,
            aspect_ratio="16:9",
            resolution="720p",
            duration=5,
        )
        values.update(overrides)
        return VideoGenerationRequest(**values)

    def test_video_cli_protocol_without_adapter_reports_online_adapter_missing(self):
        providers = [{
            "id": "cli-only", "enabled": True, "protocol": "gemini-cli",
            "video_models": ["veo2"],
        }]
        with self.assertRaises(ModelRoutingError) as raised:
            route_video_generation(self.request(), providers)
        self.assertEqual(raised.exception.code, "no_compatible_video_model")
        self.assertIn("CLI 协议没有在线视频适配器", raised.exception.args[0])

    def test_video_unknown_protocol_reports_protocol_without_online_adapter(self):
        providers = [{
            "id": "weird", "enabled": True, "protocol": "mystery-protocol",
            "video_models": ["veo2"],
        }]
        with self.assertRaises(ModelRoutingError) as raised:
            route_video_generation(self.request(), providers)
        self.assertEqual(raised.exception.code, "no_compatible_video_model")
        self.assertIn("协议 mystery-protocol 没有在线视频适配器", raised.exception.args[0])
        self.assertIn("protocol", raised.exception.incompatible_parameters)

    def test_video_pinned_model_not_found_lists_target(self):
        providers = [{
            "id": "p", "enabled": True, "protocol": "openai",
            "video_models": ["veo2"],
            "video_model_capabilities": {
                "veo2": {"media_types": ["video"], "resolutions": ["720p"], "durations": [5], "aspect_ratios": ["16:9"]},
            },
        }]
        with self.assertRaises(ModelRoutingError) as raised:
            route_video_generation(self.request(), providers, model="missing-model")
        self.assertEqual(raised.exception.code, "video_model_not_found")
        self.assertIn("模型 missing-model", raised.exception.args[0])

    def test_video_adapter_incompatible_parameters_are_merged(self):
        providers = [{
            "id": "p", "enabled": True, "protocol": "openai",
            "video_models": ["veo2"],
            "video_model_capabilities": {
                "veo2": {"media_types": ["video"], "resolutions": ["720p"], "durations": [5], "aspect_ratios": ["16:9"]},
            },
        }]
        with self.assertRaises(ModelRoutingError) as raised:
            route_video_generation(
                self.request(),
                providers,
                adapter_incompatible_parameters=lambda req, provider, model: ["watermark"],
            )
        self.assertIn("watermark", raised.exception.incompatible_parameters)


if __name__ == "__main__":
    unittest.main()
