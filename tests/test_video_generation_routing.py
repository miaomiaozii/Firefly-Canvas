import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_generation import GenerationRoute, ModelRoutingError, VideoGenerationRequest, route_video_generation


class VideoGenerationRoutingTests(unittest.TestCase):
    def setUp(self):
        capability = {
            "sizes": ["1280x720"],
            "aspect_ratios": ["16:9"],
            "resolutions": ["720p"],
            "min_duration": 5,
            "max_duration": 5,
        }
        self.providers = [{
            "id": "video-provider",
            "name": "Video Provider",
            "enabled": True,
            "protocol": "openai",
            "video_models": ["text-to-video", "image-to-video", "multimodal-video"],
            "video_model_capabilities": {
                "text-to-video": {**capability, "max_image_references": 0, "max_video_references": 0, "max_audio_references": 0},
                "image-to-video": {**capability, "max_image_references": 1, "max_video_references": 0, "max_audio_references": 0},
                "multimodal-video": {**capability, "max_image_references": 3, "max_video_references": 2, "max_audio_references": 2},
            },
        }]

    def request(self, **changes):
        values = {
            "prompt": "萤火虫飞过湖面",
            "size": "1280x720",
            "width": 1280,
            "height": 720,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "duration": 5,
            "media_type": "video",
        }
        values.update(changes)
        return VideoGenerationRequest(**values)

    def test_auto_routing_distinguishes_text_image_and_multimodal_inputs(self):
        text_route = route_video_generation(self.request(), self.providers)
        self.assertEqual(text_route.model, "text-to-video")

        image_route = route_video_generation(
            self.request(image_reference_asset_ids=("image-1",)), self.providers,
        )
        self.assertEqual(image_route.model, "image-to-video")

        multimodal_route = route_video_generation(
            self.request(
                image_reference_asset_ids=("image-1",),
                video_reference_asset_ids=("video-1",),
                audio_reference_asset_ids=("audio-1",),
            ),
            self.providers,
        )
        self.assertEqual(multimodal_route.model, "multimodal-video")

    def test_manual_model_rejects_unsupported_duration_or_reference_kind(self):
        with self.assertRaises(ModelRoutingError) as raised:
            route_video_generation(
                self.request(duration=8, image_reference_asset_ids=("image-1",)),
                self.providers,
                provider_id="video-provider",
                model="text-to-video",
            )
        self.assertEqual(raised.exception.code, "no_compatible_video_model")
        self.assertIn("duration", raised.exception.incompatible_parameters)
        self.assertIn("image_reference_count", raised.exception.incompatible_parameters)

    def test_adapter_executability_skips_auto_candidate_but_rejects_manual_pin(self):
        capability = {
            "sizes": ["1280x720"],
            "aspect_ratios": ["16:9"],
            "resolutions": ["720p"],
            "qualities": ["high"],
            "min_duration": 5,
            "max_duration": 5,
            "max_image_references": 0,
            "max_video_references": 0,
            "max_audio_references": 0,
        }
        providers = [
            {
                "id": "apimart",
                "name": "APIMart",
                "enabled": True,
                "protocol": "apimart",
                "video_models": ["seedance-specialized"],
                "video_model_capabilities": {"seedance-specialized": capability},
            },
            {
                "id": "generic-http",
                "name": "Generic HTTP",
                "enabled": True,
                "protocol": "openai",
                "video_models": ["generic-video"],
                "video_model_capabilities": {"generic-video": capability},
            },
        ]

        def adapter_incompatibilities(_request, provider, _model):
            if provider["id"] == "apimart":
                return ["width", "height", "size", "quality"]
            return []

        route = route_video_generation(
            self.request(quality="high"),
            providers,
            adapter_incompatible_parameters=adapter_incompatibilities,
        )
        self.assertEqual((route.provider_id, route.model), ("generic-http", "generic-video"))

        with self.assertRaises(ModelRoutingError) as raised:
            route_video_generation(
                self.request(quality="high"),
                providers,
                provider_id="apimart",
                model="seedance-specialized",
                adapter_incompatible_parameters=adapter_incompatibilities,
            )
        self.assertEqual(
            set(raised.exception.incompatible_parameters),
            {"width", "height", "size", "quality"},
        )

    def test_routing_preserves_the_user_request_and_provider_metadata(self):
        request = self.request(
            image_reference_asset_ids=("image-1",),
            video_reference_asset_ids=("video-1",),
            audio_reference_asset_ids=("audio-1",),
        )
        request_before = copy.deepcopy(request.to_dict())
        providers_before = copy.deepcopy(self.providers)
        route = route_video_generation(request, self.providers)
        self.assertEqual(request.to_dict(), request_before)
        self.assertEqual(self.providers, providers_before)
        self.assertEqual(route.request, request_before)
        self.assertEqual(route.request["duration"], 5)
        self.assertEqual(route.request["image_reference_count"], 1)
        self.assertEqual(route.request["video_reference_count"], 1)
        self.assertEqual(route.request["audio_reference_count"], 1)

    def test_unannotated_unknown_models_reject_reference_inputs(self):
        providers = [{
            "id": "unknown-provider",
            "enabled": True,
            "protocol": "openai",
            "video_models": ["mystery-video-model"],
        }]
        with self.assertRaises(ModelRoutingError) as raised:
            route_video_generation(
                self.request(image_reference_asset_ids=("image-1",)), providers,
            )
        self.assertIn("image_reference_count", raised.exception.incompatible_parameters)

    def test_video_routing_rejects_cli_protocols_including_retired_jimeng(self):
        # #236：即梦已全量退役 —— VIDEO_CLI_PROTOCOLS 已清零，CLI 视频协议（含 jimeng）全部不再可执行。
        capability = {
            "sizes": ["1280x720"],
            "aspect_ratios": ["16:9"],
            "resolutions": ["720p"],
            "min_duration": 5,
            "max_duration": 5,
            "max_image_references": 0,
            "max_video_references": 0,
            "max_audio_references": 0,
        }
        providers = [
            {"id": "codex-video", "enabled": True, "protocol": "codex", "video_models": ["text-to-video"], "video_model_capabilities": {"text-to-video": capability}},
            {"id": "gemini-cli-video", "enabled": True, "protocol": "gemini-cli", "video_models": ["text-to-video"], "video_model_capabilities": {"text-to-video": capability}},
            {"id": "jimeng-video", "enabled": True, "protocol": "jimeng", "video_models": ["text-to-video"], "video_model_capabilities": {"text-to-video": capability}},
        ]
        with self.assertRaises(ModelRoutingError) as raised:
            route_video_generation(self.request(), providers)
        self.assertEqual(raised.exception.code, "no_compatible_video_model")
        self.assertIn("protocol", raised.exception.incompatible_parameters)
        for provider_id, marker in (("codex-video", "CLI"), ("gemini-cli-video", "CLI"), ("jimeng-video", "jimeng")):
            with self.subTest(provider=provider_id):
                with self.assertRaises(ModelRoutingError) as raised:
                    route_video_generation(self.request(), providers, provider_id=provider_id, model="text-to-video")
                self.assertEqual(raised.exception.code, "no_compatible_video_model")
                self.assertIn("protocol", raised.exception.incompatible_parameters)
                self.assertIn(marker, str(raised.exception))

    def test_image_and_video_routes_use_one_neutral_route_type(self):
        self.assertIs(route_video_generation(self.request(), self.providers).__class__, GenerationRoute)


if __name__ == "__main__":
    unittest.main()
