"""soon（soonstudio.ai）服务商：Gemini 图像模型自动走原生 generateContent，能力按原生协议。

来源：soon 返回 400 gemini_native_request_required（logs 2026-11-19）；
https://soonstudio.ai/docs 前端 bundle（/v1beta/models/{model}:generateContent，imageConfig.imageSize）；
2026-11-19 用真实 key 实测 gemini-nano-banana-2.1 imageSize=4K 出 5056x3392。
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import app.services as SERVICES
import main as APP
from firefly_generation import (
    ImageGenerationRequest,
    is_soon_gemini_native_image_model,
    is_soon_openrouter_video_model,
    normalize_image_model_capabilities,
    route_image_generation,
    soon_gemini_native_model,
)
from tests.provider_contract import ProviderContractCapture

SOON_MODELS = ["gemini-3-pro-image", "gemini-3.1-flash-image-preview", "gemini-nano-banana-2.1", "nano-banana-pro"]
# 用户现有配置里的快照：旧推断把 gemini-nano-banana-2.1 锁在 1k。
STALE_1K = {
    "media_types": ["image"],
    "aspect_ratios": ["1:1", "3:2", "2:3"],
    "resolutions": ["1k"],
    "sizes": ["1024x1024", "1536x1024"],
    "max_reference_images": 20,
}


def soon_provider(base_url="https://api.soonstudio.ai", **extra):
    return {
        "id": "provider-soon",
        "name": "soon",
        "enabled": True,
        "protocol": "openai",
        "base_url": base_url,
        "image_request_mode": "openai",
        "image_models": list(SOON_MODELS),
        "image_model_capabilities": {model: dict(STALE_1K) for model in SOON_MODELS},
        "model_protocols": {},
        **extra,
    }


class SoonProtocolRoutingTests(unittest.TestCase):
    def test_gemini_image_models_route_to_native_protocol(self):
        provider = soon_provider()
        for model in ("gemini-3-pro-image", "gemini-3.1-flash-image-preview", "gemini-nano-banana-2.1"):
            with self.subTest(model=model):
                self.assertEqual(APP.effective_protocol(provider, model), "gemini")

    def test_non_gemini_models_keep_provider_protocol(self):
        provider = soon_provider()
        for model in ("gpt-5.4", "openrouter/google/gemini-3-pro-image", "openrouter/google/veo-3.1"):
            with self.subTest(model=model):
                self.assertEqual(APP.effective_protocol(provider, model), "openai")

    def test_explicit_model_override_wins(self):
        provider = soon_provider(model_protocols={"gemini-3-pro-image": "openai"})
        self.assertEqual(APP.effective_protocol(provider, "gemini-3-pro-image"), "openai")

    def test_other_providers_are_unchanged(self):
        for base_url in ("https://ai.comfly.chat/v1", "https://notsoonstudio.ai", "https://soonstudio.ai.evil.example"):
            with self.subTest(base_url=base_url):
                provider = soon_provider(base_url=base_url)
                self.assertFalse(is_soon_gemini_native_image_model(provider, "gemini-3-pro-image"))
                self.assertEqual(APP.effective_protocol(provider, "gemini-3-pro-image"), "openai")

    def test_regional_hosts_are_soon(self):
        for host in ("api-cn", "api-hk", "api-us"):
            with self.subTest(host=host):
                self.assertTrue(is_soon_gemini_native_image_model(
                    soon_provider(base_url=f"https://{host}.soonstudio.ai/v1"), "gemini-nano-banana-2.1"))


class SoonGeminiEndpointTests(unittest.TestCase):
    def test_endpoint_joins_v1beta_without_duplicating_v1(self):
        for base_url in ("https://api.soonstudio.ai", "https://api.soonstudio.ai/", "https://api.soonstudio.ai/v1"):
            with self.subTest(base_url=base_url):
                self.assertEqual(
                    SERVICES.gemini_endpoint_url(soon_provider(base_url=base_url), "gemini-3-pro-image"),
                    "https://api.soonstudio.ai/v1beta/models/gemini-3-pro-image:generateContent",
                )

    def test_explicit_endpoint_override_is_kept(self):
        provider = soon_provider(base_url="https://api.soonstudio.ai/v1", image_generation_endpoint="/custom/gen")
        self.assertEqual(SERVICES.gemini_endpoint_url(provider, "gemini-3-pro-image"), "https://api.soonstudio.ai/custom/gen")


class SoonCapabilityTests(unittest.TestCase):
    def test_stale_1k_snapshot_is_replaced_for_native_models(self):
        provider = soon_provider()
        capabilities = normalize_image_model_capabilities(provider["image_model_capabilities"], SOON_MODELS, provider)
        nb21 = capabilities["gemini-nano-banana-2.1"]
        self.assertEqual(nb21["resolutions"], ["1k", "2k", "4k"])
        self.assertIn("3520x2352", nb21["sizes"])
        self.assertIn("8:1", nb21["aspect_ratios"])
        self.assertEqual(nb21["max_reference_images"], 20)
        self.assertNotIn("8:1", capabilities["gemini-3-pro-image"]["aspect_ratios"])
        # nano-banana-pro 后台转发 gemini-3-pro-image，能力随原生模型。
        self.assertEqual(capabilities["nano-banana-pro"]["resolutions"], ["1k", "2k", "4k"])
        self.assertEqual(capabilities["nano-banana-pro"]["aspect_ratios"], capabilities["gemini-3-pro-image"]["aspect_ratios"])

    def test_without_provider_context_explicit_capabilities_stay_authoritative(self):
        capabilities = normalize_image_model_capabilities({"gemini-nano-banana-2.1": STALE_1K}, ["gemini-nano-banana-2.1"])
        self.assertEqual(capabilities["gemini-nano-banana-2.1"]["resolutions"], ["1k"])

    def test_saved_provider_record_is_normalized_with_native_capabilities(self):
        normalized = APP.normalize_provider(soon_provider())
        self.assertEqual(normalized["image_model_capabilities"]["gemini-nano-banana-2.1"]["resolutions"], ["1k", "2k", "4k"])

    def test_4k_request_on_base_model_passes_precheck(self):
        route = route_image_generation(
            ImageGenerationRequest(
                prompt="t", size="3520x2352", width=3520, height=2352,
                aspect_ratio="3:2", resolution="4k",
            ),
            [soon_provider()],
            provider_id="provider-soon",
            model="gemini-nano-banana-2.1",
        )
        self.assertEqual(route.model, "gemini-nano-banana-2.1")


class SoonNativeRequestTests(unittest.TestCase):
    def test_generate_image_posts_native_body_to_v1beta(self):
        provider = soon_provider(base_url="https://api.soonstudio.ai/v1")
        capture = ProviderContractCapture({
            "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "YWJj"}}]}}]
        })
        with (
            patch.object(APP, "get_api_provider_exact", return_value=provider),
            patch.object(APP, "provider_env_key_value", return_value="sk-soon-test"),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
        ):
            asyncio.run(APP.generate_ai_image(
                "red apple", "3520x2352", "auto", "gemini-nano-banana-2.1", [],
                provider["id"], preserve_model=True,
                image_parameters={"aspect_ratio": "3:2", "resolution": "4k"},
            ))
        request = capture.only_request()
        self.assertEqual(request["url"], "https://api.soonstudio.ai/v1beta/models/gemini-nano-banana-2.1:generateContent")
        self.assertEqual(request["headers"].get("Authorization"), "Bearer sk-soon-test")
        body = request["json"]
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "red apple"}]}])
        self.assertEqual(body["generationConfig"]["imageConfig"], {"aspectRatio": "3:2", "imageSize": "4K"})
        self.assertNotIn("model", body)


class SoonNanoBananaProTests(unittest.TestCase):
    """nano-banana-pro 的 images 端点只出 1264x848；原生 gemini-3-pro-image 4K 实测 4096x4096 / 5504x3072。"""

    def test_routes_to_native_protocol_only_on_soon(self):
        self.assertEqual(APP.effective_protocol(soon_provider(), "nano-banana-pro"), "gemini")
        self.assertEqual(soon_gemini_native_model(soon_provider(), "nano-banana-pro"), "gemini-3-pro-image")
        other = soon_provider(base_url="https://ai.comfly.chat/v1")
        self.assertEqual(APP.effective_protocol(other, "nano-banana-pro"), "openai")
        self.assertEqual(soon_gemini_native_model(other, "nano-banana-pro"), "nano-banana-pro")

    def test_4k_request_passes_precheck_and_keeps_user_model(self):
        route = route_image_generation(
            ImageGenerationRequest(prompt="t", size="4096x4096", width=4096, height=4096, aspect_ratio="1:1", resolution="4k"),
            [soon_provider()],
            provider_id="provider-soon",
            model="nano-banana-pro",
        )
        self.assertEqual(route.model, "nano-banana-pro")

    def test_request_is_sent_as_gemini_3_pro_image(self):
        provider = soon_provider()
        capture = ProviderContractCapture({
            "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "YWJj"}}]}}]
        })
        with (
            patch.object(APP, "get_api_provider_exact", return_value=provider),
            patch.object(APP, "provider_env_key_value", return_value="sk-soon-test"),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
        ):
            asyncio.run(APP.generate_ai_image(
                "red apple", "4096x4096", "auto", "nano-banana-pro", [],
                provider["id"], preserve_model=True,
                image_parameters={"aspect_ratio": "1:1", "resolution": "4k"},
            ))
        request = capture.only_request()
        self.assertEqual(request["url"], "https://api.soonstudio.ai/v1beta/models/gemini-3-pro-image:generateContent")
        self.assertEqual(request["json"]["generationConfig"]["imageConfig"], {"aspectRatio": "1:1", "imageSize": "4K"})


VEO = "openrouter/google/veo-3.1"


class SoonOpenRouterVideoTests(unittest.TestCase):
    """来源：soon 模型页 bundle（ModelDetailPage，/openrouter/v1/videos + x-studio-channel-group: openrouter）。"""

    def test_detection_requires_soon_host_and_openrouter_prefix(self):
        self.assertTrue(is_soon_openrouter_video_model(soon_provider(), VEO))
        self.assertFalse(is_soon_openrouter_video_model(soon_provider(), "veo3.1-fast"))
        self.assertFalse(is_soon_openrouter_video_model(soon_provider(base_url="https://openrouter.ai/api"), VEO))

    def test_submit_poll_and_download_use_openrouter_group(self):
        provider = soon_provider(video_models=[VEO])
        capture = ProviderContractCapture([
            {"id": "gen-vid-1", "polling_url": "/openrouter/v1/videos/gen-vid-1", "status": "pending"},
            {"id": "gen-vid-1", "status": "in_progress"},
            {"id": "gen-vid-1", "status": "completed", "urls": ["https://cdn.example/v.mp4"]},
        ])
        payload = APP.CanvasVideoRequest(
            prompt="a cat", provider_id=provider["id"], model=VEO, duration=4,
            aspect_ratio="16:9", resolution="720p",
            images=[{"url": "https://cdn.example/first.png", "role": "first_frame"}],
        )
        with (
            patch.object(APP, "provider_env_key_value", return_value="sk-soon-test"),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
            patch.object(APP, "save_remote_video_to_output", AsyncMock(return_value="/output/v.mp4")),
            patch("app.services.generation.asyncio.sleep", AsyncMock()),
        ):
            result = asyncio.run(SERVICES.execute_canvas_video(payload, provider))
        self.assertEqual(result["videos"], ["/output/v.mp4"])
        submit, *polls = capture.requests
        self.assertEqual(submit["url"], "https://api.soonstudio.ai/openrouter/v1/videos")
        self.assertEqual(submit["json"]["model"], "google/veo-3.1")
        self.assertEqual(submit["json"]["duration"], 4)
        self.assertEqual(submit["json"]["resolution"], "720p")
        self.assertEqual(submit["json"]["frame_images"], [
            {"type": "image_url", "image_url": {"url": "https://cdn.example/first.png"}, "frame_type": "first_frame"}
        ])
        self.assertEqual([p["url"] for p in polls], ["https://api.soonstudio.ai/openrouter/v1/videos/gen-vid-1"] * 2)
        for request in capture.requests:
            self.assertEqual(request["headers"].get("x-studio-channel-group"), "openrouter")

    def test_other_soon_video_models_keep_default_route(self):
        self.assertEqual(SERVICES.video_submit_url_candidates(soon_provider(), "https://api.soonstudio.ai", "veo3.1-fast")[0],
                         "https://api.soonstudio.ai/v2/videos/generations")


if __name__ == "__main__":
    unittest.main()
