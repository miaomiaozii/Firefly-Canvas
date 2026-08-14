import asyncio
import importlib.util
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("infinite_canvas_main", ROOT / "main.py")
APP = importlib.util.module_from_spec(SPEC)
sys.modules["infinite_canvas_main"] = APP
SPEC.loader.exec_module(APP)

# The exec'd module must not linger in sys.modules nor leave FIREFLY_MAIN_MODULE
# set: otherwise later `import main` (same process) would bind routers to this
# stale module. Routers already captured their _app_main reference at exec time.
for _m in ("infinite_canvas_main",):
    sys.modules.pop(_m, None)
    for _k in list(sys.modules):
        if _k.startswith(_m + "."):
            sys.modules.pop(_k, None)
import os as _os
_os.environ.pop("FIREFLY_MAIN_MODULE", None)
# restore services binding to the real main module if it was already imported
try:
    import app.services as _services
    _services._bind_main(sys.modules["main"])
except KeyError:
    pass


class FakeResponse:
    status_code = 200
    text = ""
    reason_phrase = "OK"
    headers = {"content-length": "2"}

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"url": "https://example.invalid/out.png"}]}

    async def aread(self):
        return b"{}"


class FakeAsyncClient:
    last_post = None
    last_request = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        FakeAsyncClient.last_post = {"url": url, **kwargs}
        return FakeResponse()

    def build_request(self, method, url, **kwargs):
        FakeAsyncClient.last_request = {"method": method, "url": url, **kwargs}
        return FakeAsyncClient.last_request

    async def send(self, request, stream=False):
        for _field, (_filename, content, _mime_type) in request.get("files", []):
            while content.read(64 * 1024):
                pass
        return FakeResponse()


class BananaOutpaintRequestTests(unittest.TestCase):
    def test_extract_image_accepts_nested_signed_url_without_file_extension(self):
        signed_url = "https://cdn.example.invalid/generated/object?token=signed"

        image = APP.extract_image({"data": {"images": [{"image_url": signed_url}]}})

        self.assertEqual(image, {"type": "url", "value": signed_url})

    def setUp(self):
        try:
            import app.services as _services
            self._services = _services
            self._services._bind_main(APP)
        except Exception:
            self._services = None

    def tearDown(self):
        if getattr(self, "_services", None) is not None:
            try:
                self._services._bind_main(sys.modules["main"])
            except KeyError:
                self._services._main_bound_module = None

    def test_openai_compatible_banana_edit_sends_supported_aspect_ratio(self):
        provider = {
            "id": "custom-api",
            "name": "comfly",
            "base_url": "https://ai.comfly.org",
            "protocol": "openai",
            "image_request_mode": "openai",
            "api_key": "test-key",
            "model_protocols": {},
        }
        reference = [{"url": "/assets/input/source.png", "name": "source.png", "role": "source"}]
        with (
            patch.object(APP, "get_api_provider", return_value=provider),
            patch.object(APP, "output_file_from_url", return_value=str(__file__)),
            patch.object(APP, "api_headers", return_value={}),
            patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
        ):
            asyncio.run(APP.generate_ai_image(
                "Remove white area and fill the scene",
                "3840x1648",
                "high",
                "nano-banana-pro-4k",
                reference,
                "custom-api",
            ))

        data = FakeAsyncClient.last_post["data"]
        self.assertEqual(data["model"], "nano-banana-pro-4k")
        self.assertNotIn("size", data)
        self.assertEqual(data["aspect_ratio"], "21:9")
        self.assertEqual(data["image_size"], "4K")
        self.assertEqual(data["response_format"], "url")

    def test_comfly_edit_upload_uses_smaller_pixel_exact_lossless_webp(self):
        provider = {
            "id": "provider-ms5fzxu7",
            "name": "comfly",
            "base_url": "https://ai.comfly.org/v1",
            "protocol": "openai",
            "image_request_mode": "openai",
            "api_key": "test-key",
            "model_protocols": {},
        }
        source = Image.effect_noise((512, 512), 20).convert("RGB")
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "reference.png"
            source.save(source_path, "PNG")
            reference = [{"url": "/assets/input/reference.png", "name": "reference.png", "role": "source"}]
            with (
                patch.object(APP, "get_api_provider", return_value=provider),
                patch.object(APP, "output_file_from_url", return_value=str(source_path)),
                patch.object(APP, "api_headers", return_value={}),
                patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
            ):
                asyncio.run(APP.generate_ai_image(
                    "Turn the cat white",
                    "3840x1648",
                    "high",
                    "gemini-3.1-flash-image-preview",
                    reference,
                    "provider-ms5fzxu7",
                ))

            field, upload = FakeAsyncClient.last_post["files"][0]
            filename, payload, mime_type = upload
            self.assertEqual(field, "image")
            self.assertEqual(Path(filename).suffix, ".webp")
            self.assertEqual(mime_type, "image/webp")
            self.assertIsInstance(payload, bytes)
            self.assertLess(len(payload), source_path.stat().st_size)
            with Image.open(BytesIO(payload)) as decoded:
                decoded.load()
                self.assertEqual(decoded.size, source.size)
                self.assertIsNone(ImageChops.difference(source, decoded.convert("RGB")).getbbox())

    def test_comfly_edit_reports_real_upload_and_receive_stages(self):
        provider = {
            "id": "provider-ms5fzxu7",
            "name": "comfly",
            "base_url": "https://ai.comfly.org/v1",
            "protocol": "openai",
            "image_request_mode": "openai",
            "api_key": "test-key",
            "model_protocols": {},
        }
        progress = []
        source = Image.effect_noise((512, 512), 20).convert("RGB")
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "reference.png"
            source.save(source_path, "PNG")
            reference = [{"url": "/assets/input/reference.png", "name": "reference.png", "role": "source"}]
            with (
                patch.object(APP, "get_api_provider", return_value=provider),
                patch.object(APP, "output_file_from_url", return_value=str(source_path)),
                patch.object(APP, "api_headers", return_value={}),
                patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
            ):
                asyncio.run(APP.generate_ai_image(
                    "Turn the cat white",
                    "3840x1648",
                    "high",
                    "gemini-3.1-flash-image-preview",
                    reference,
                    "provider-ms5fzxu7",
                    progress_callback=lambda phase, **details: progress.append((phase, details)),
                ))

        phases = [phase for phase, _details in progress]
        self.assertEqual(phases[0], "uploading")
        self.assertIn("provider_generating", phases)
        self.assertEqual(phases[-1], "receiving")
        upload_updates = [details for phase, details in progress if phase == "uploading"]
        self.assertGreater(upload_updates[-1]["bytes_done"], 0)
        self.assertEqual(upload_updates[-1]["bytes_done"], upload_updates[-1]["bytes_total"])
        receive_updates = [details for phase, details in progress if phase == "receiving"]
        self.assertEqual(receive_updates[-1], {"bytes_done": 2, "bytes_total": 2})

    def test_every_outpaint_preset_maps_to_a_supported_gemini_ratio(self):
        allowed = {item[2] for item in APP.GEMINI_IMAGE_ASPECT_RATIOS}
        for preset in ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9", "9:21"):
            with self.subTest(preset=preset):
                self.assertIn(APP.gemini_supported_aspect_ratio(preset), allowed)

    def test_route_uses_base_banana_family_and_size_parameters(self):
        provider = {
            "image_models": [
                "nano-banana-2", "nano-banana-2-2k", "nano-banana-2-4k",
                "nano-banana-pro", "nano-banana-pro-2k", "nano-banana-pro-4k",
            ]
        }
        cases = (
            ("nano-banana-pro-4k", "1024x1024", "nano-banana-pro-4k", "1:1", "1K"),
            ("nano-banana-pro-4k", "2048x2048", "nano-banana-pro-4k", "1:1", "2K"),
            ("nano-banana-pro-2k", "4096x2304", "nano-banana-pro-2k", "16:9", "4K"),
            ("nano-banana-2-2k", "3840x1648", "nano-banana-2-2k", "21:9", "4K"),
        )
        for requested, size, expected_model, expected_ratio, expected_image_size in cases:
            with self.subTest(requested=requested, size=size):
                route = APP.route_openai_image_request(provider, requested, size)
                self.assertEqual(route["model"], expected_model)
                self.assertEqual(route["params"]["aspect_ratio"], expected_ratio)
                self.assertEqual(route["params"]["image_size"], expected_image_size)

    def test_route_falls_back_to_base_when_resolution_alias_is_unavailable(self):
        provider = {"image_models": ["nano-banana-pro"]}
        route = APP.route_openai_image_request(provider, "nano-banana-pro-2k", "4096x2304")
        self.assertEqual(route["model"], "nano-banana-pro")
        self.assertEqual(route["params"]["image_size"], "4K")

    def test_openai_gateway_gemini_image_id_receives_image_parameters(self):
        route = APP.route_openai_image_request({}, "gemini-3-pro-image-preview", "1536x1024")
        self.assertEqual(route["model"], "gemini-3-pro-image-preview")
        self.assertEqual(route["params"], {"aspect_ratio": "3:2", "image_size": "1K"})

    def test_unrelated_image_model_is_not_routed(self):
        route = APP.route_openai_image_request({}, "flux-kontext-pro", "1536x1024")
        self.assertEqual(route["model"], "flux-kontext-pro")
        self.assertEqual(route["params"], {})

    def test_text_to_image_request_uses_routed_model_and_parameters(self):
        provider = {
            "id": "custom-api",
            "name": "comfly",
            "base_url": "https://ai.comfly.org",
            "protocol": "openai",
            "image_request_mode": "openai",
            "api_key": "test-key",
            "model_protocols": {},
            "image_models": ["nano-banana-2", "nano-banana-2-2k", "nano-banana-2-4k"],
        }
        with (
            patch.object(APP, "get_api_provider", return_value=provider),
            patch.object(APP, "api_headers", return_value={}),
            patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
        ):
            asyncio.run(APP.generate_ai_image(
                "A panoramic orchard",
                "4096x2304",
                "high",
                "nano-banana-2",
                [],
                "custom-api",
            ))

        body = FakeAsyncClient.last_post["json"]
        self.assertEqual(body["model"], "nano-banana-2-4k")
        self.assertEqual(body["aspect_ratio"], "16:9")
        self.assertNotIn("size", body)
        self.assertEqual(body["image_size"], "4K")
        self.assertEqual(body["response_format"], "url")

    def test_comfly_preserves_explicit_tier_model_and_selected_ratio(self):
        provider = {
            "id": "custom-api", "name": "comfly", "base_url": "https://ai.comfly.org",
            "protocol": "openai", "image_request_mode": "openai", "api_key": "test-key",
            "model_protocols": {},
            "image_models": ["nano-banana-pro", "nano-banana-pro-2k", "nano-banana-pro-4k"],
        }
        with (
            patch.object(APP, "get_api_provider_exact", return_value=provider),
            patch.object(APP, "api_headers", return_value={}),
            patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
        ):
            asyncio.run(APP.generate_ai_image(
                "A panoramic orchard", "3840x1648", "high", "nano-banana-pro-4k", [], "custom-api",
                preserve_model=True,
                image_parameters={"width": 3840, "height": 1648, "aspect_ratio": "21:9", "resolution": "4k", "media_type": "image"},
            ))

        body = FakeAsyncClient.last_post["json"]
        self.assertEqual(body["model"], "nano-banana-pro-4k")
        self.assertNotIn("size", body)
        self.assertEqual(body["aspect_ratio"], "21:9")
        self.assertNotIn("resolution", body)
        self.assertEqual(body["image_size"], "4K")
        self.assertEqual(body["response_format"], "url")
        self.assertEqual(body["response_format"], "url")

    def test_nano_banana_2_base_model_receives_selected_ratio_and_image_size(self):
        provider = {
            "id": "provider-ms5fzxu7", "name": "comfly", "base_url": "https://ai.comfly.org/v1",
            "protocol": "openai", "image_request_mode": "openai", "api_key": "test-key",
            "model_protocols": {}, "image_models": ["gemini-3.1-flash-image-preview"],
        }
        with (
            patch.object(APP, "get_api_provider_exact", return_value=provider),
            patch.object(APP, "api_headers", return_value={}),
            patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
        ):
            asyncio.run(APP.generate_ai_image(
                "生成一只白猫", "3840x1648", "high", "gemini-3.1-flash-image-preview", [],
                "provider-ms5fzxu7", preserve_model=True,
                image_parameters={"width": 3840, "height": 1648, "aspect_ratio": "21:9", "resolution": "4k", "media_type": "image"},
            ))

        body = FakeAsyncClient.last_post["json"]
        self.assertEqual(body["model"], "gemini-3.1-flash-image-preview")
        self.assertNotIn("size", body)
        self.assertEqual(body["aspect_ratio"], "21:9")
        self.assertNotIn("resolution", body)
        self.assertEqual(body["image_size"], "4K")
        self.assertEqual(body["response_format"], "url")

    def test_comfly_4k_banana_edit_matches_provider_model_and_parameter_contract(self):
        provider = {
            "id": "provider-ms5fzxu7", "name": "comfly", "base_url": "https://ai.comfly.org/v1",
            "protocol": "openai", "image_request_mode": "openai", "api_key": "test-key",
            "model_protocols": {}, "image_models": ["gemini-3.1-flash-image-preview"],
            "available_models": {"image_models": [
                "gemini-3.1-flash-image-preview", "gemini-3.1-flash-image-preview-4k",
            ]},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            first_path = Path(temp_dir) / "first.png"
            second_path = Path(temp_dir) / "second.png"
            Image.new("RGB", (16, 16), "red").save(first_path)
            Image.new("RGB", (16, 16), "blue").save(second_path)
            references = [
                {"url": "/assets/input/first.png", "name": "first.png", "role": "source"},
                {"url": "/assets/input/second.png", "name": "second.png", "role": "source"},
            ]
            paths = {
                "/assets/input/first.png": str(first_path),
                "/assets/input/second.png": str(second_path),
            }
            with (
                patch.object(APP, "get_api_provider_exact", return_value=provider),
                patch.object(APP, "output_file_from_url", side_effect=lambda url: paths[url]),
                patch.object(APP, "api_headers", return_value={}),
                patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
            ):
                asyncio.run(APP.generate_ai_image(
                    "精修途中的產品細節，高級感，商業攝影，白色背景。",
                    "4096x4096", "high", "gemini-3.1-flash-image-preview", references,
                    "provider-ms5fzxu7", preserve_model=True,
                    image_parameters={
                        "width": 4096, "height": 4096, "aspect_ratio": "1:1",
                        "resolution": "4k", "media_type": "image",
                    },
                ))

        body = FakeAsyncClient.last_post["data"]
        self.assertEqual(body["model"], "gemini-3.1-flash-image-preview-4k")
        self.assertEqual(body["aspect_ratio"], "1:1")
        self.assertEqual(body["image_size"], "4K")
        self.assertEqual(body["response_format"], "url")
        self.assertNotIn("size", body)
        self.assertNotIn("quality", body)
        files = FakeAsyncClient.last_post["files"]
        self.assertEqual([field for field, _upload in files], ["image", "image"])
        colors = []
        for _field, (_filename, payload, _mime_type) in files:
            with Image.open(BytesIO(payload)) as image:
                colors.append(image.convert("RGB").getpixel((0, 0)))
        self.assertEqual(colors, [(255, 0, 0), (0, 0, 255)])

    def test_comfly_banana_family_all_tiers_reach_final_request(self):
        cases = (
            ("nano-banana", ("nano-banana", "nano-banana-hd", "nano-banana-hd")),
            ("nano-banana-pro", ("nano-banana-pro", "nano-banana-pro-2k", "nano-banana-pro-4k")),
            ("nano-banana-2", ("nano-banana-2", "nano-banana-2-2k", "nano-banana-2-4k")),
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
        available = list(dict.fromkeys(expected for _model, models in cases for expected in models))
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "reference.png"
            Image.new("RGB", (16, 16), "green").save(image_path)
            for model, expected_models in cases:
                for (resolution, pixels), expected_model in zip(tiers, expected_models):
                    with self.subTest(model=model, resolution=resolution):
                        provider = {
                            "id": "comfly", "name": "comfly", "base_url": "https://ai.comfly.org/v1",
                            "protocol": "openai", "image_request_mode": "openai", "api_key": "test-key",
                            "model_protocols": {}, "image_models": [model],
                            "available_models": {"image_models": [model, *available]},
                        }
                        with (
                            patch.object(APP, "get_api_provider_exact", return_value=provider),
                            patch.object(APP, "output_file_from_url", return_value=str(image_path)),
                            patch.object(APP, "api_headers", return_value={}),
                            patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
                        ):
                            asyncio.run(APP.generate_ai_image(
                                "Keep the selected product details", f"{pixels}x{pixels}", "high", model,
                                [{"url": "/assets/input/reference.png", "name": "reference.png", "role": "source"}],
                                "comfly", preserve_model=True,
                                image_parameters={
                                    "width": pixels, "height": pixels, "aspect_ratio": "1:1",
                                    "resolution": resolution, "media_type": "image",
                                },
                            ))
                        body = FakeAsyncClient.last_post["data"]
                        self.assertEqual(body["model"], expected_model)
                        self.assertEqual(body["aspect_ratio"], "1:1")
                        self.assertEqual(body["image_size"], resolution.upper())
                        self.assertEqual(body["response_format"], "url")
                        self.assertNotIn("size", body)
                        self.assertNotIn("quality", body)
                        self.assertEqual([field for field, _upload in FakeAsyncClient.last_post["files"]], ["image"])

    def test_legacy_comfly_path_routes_selected_tier(self):
        provider = {
            "id": "comfly", "name": "comfly", "base_url": "https://ai.comfly.org/v1",
            "protocol": "openai", "image_request_mode": "openai", "api_key": "test-key",
            "model_protocols": {}, "image_models": ["nano-banana-pro"],
            "available_models": {"image_models": ["nano-banana-pro", "nano-banana-pro-4k"]},
        }
        with (
            patch.object(APP, "get_api_provider", return_value=provider),
            patch.object(APP, "api_headers", return_value={}),
            patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
        ):
            asyncio.run(APP.generate_ai_image(
                "A wide landscape", "3840x1648", "high", "nano-banana-pro", [], "comfly",
            ))

        body = FakeAsyncClient.last_post["json"]
        self.assertEqual(body["model"], "nano-banana-pro-4k")
        self.assertEqual(body["aspect_ratio"], "21:9")
        self.assertNotIn("resolution", body)
        self.assertEqual(body["image_size"], "4K")
        self.assertEqual(body["response_format"], "url")

    def test_image_2_request_does_not_receive_banana_only_fields(self):
        provider = {
            "id": "custom-api",
            "name": "comfly",
            "base_url": "https://ai.comfly.org",
            "protocol": "openai",
            "image_request_mode": "openai",
            "api_key": "test-key",
            "model_protocols": {},
        }
        reference = [{"url": "/assets/input/source.png", "name": "source.png", "role": "source"}]
        with (
            patch.object(APP, "get_api_provider", return_value=provider),
            patch.object(APP, "output_file_from_url", return_value=str(__file__)),
            patch.object(APP, "api_headers", return_value={}),
            patch.object(APP.httpx, "AsyncClient", FakeAsyncClient),
        ):
            asyncio.run(APP.generate_ai_image(
                "Expand the image",
                "1536x1024",
                "high",
                "gpt-image-2",
                reference,
                "custom-api",
            ))

        data = FakeAsyncClient.last_post["data"]
        self.assertNotIn("aspect_ratio", data)
        self.assertNotIn("image_size", data)


if __name__ == "__main__":
    unittest.main()
