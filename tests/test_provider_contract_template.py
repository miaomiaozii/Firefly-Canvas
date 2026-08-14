import asyncio
import tempfile
import unittest
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image

import main as APP
from firefly_generation import ImageGenerationRequest, ModelRoutingError, route_image_generation
from firefly_projects import ProjectStore
from tests.provider_contract import ProviderContractCapture, load_provider_contract, normalize_captured_request


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "provider_contracts" / "openai-comfly-banana-image.json"
AUTHORIZATION = "Bearer provider-contract-secret"


class ProviderContractTemplateTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_provider_contract(FIXTURE_PATH)
        self.capture = ProviderContractCapture(self.contract["response_fixture"])

    def provider(self):
        return deepcopy(self.contract["provider_fixture"])

    def test_final_request_normalization_preserves_payload_and_redacts_header_values(self):
        normalized = normalize_captured_request({
            "method": "POST",
            "url": "https://provider.invalid/v1/images/edits",
            "headers": {"Authorization": "Bearer must-not-appear"},
            "json": None,
            "data": {"model": "image-model"},
            "files": [("image", ("reference.png", b"abc", "image/png"))],
        })

        self.assertEqual(normalized, {
            "method": "POST",
            "url": "https://provider.invalid/v1/images/edits",
            "header_names": ["authorization"],
            "json": None,
            "data": {"model": "image-model"},
            "files": [{
                "field": "image",
                "filename": "reference.png",
                "mime_type": "image/png",
                "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            }],
        })
        self.assertNotIn("must-not-appear", str(normalized))

    def route(self, case, *, reference_ids=()):
        request = ImageGenerationRequest(
            prompt="provider contract prompt",
            size=case["size"],
            width=case["width"],
            height=case["height"],
            aspect_ratio=case["aspect_ratio"],
            resolution=case["resolution"],
            reference_asset_ids=tuple(reference_ids),
            media_type="image",
            quality="high",
            count=1,
        )
        route = route_image_generation(
            request,
            [self.provider()],
            provider_id=self.contract["provider_fixture"]["id"],
            model=self.contract["requested_model"],
        )
        expected_route = (
            self.contract["expected_edit_route"]
            if reference_ids
            else case["expected_route"]
        )
        self.assertEqual(route.to_dict(), expected_route)
        return route

    def generate(self, case, route, references):
        provider = self.provider()
        with (
            patch.object(APP, "get_api_provider_exact", return_value=provider),
            patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
            patch.object(APP.httpx, "AsyncClient", self.capture.async_client),
        ):
            return asyncio.run(APP.generate_ai_image(
                "provider contract prompt",
                case["size"],
                "high",
                route.model,
                references,
                provider["id"],
                preserve_model=True,
                image_parameters={
                    "width": case["width"],
                    "height": case["height"],
                    "aspect_ratio": case["aspect_ratio"],
                    "resolution": case["resolution"],
                    "media_type": "image",
                },
            ))

    def test_fixture_is_traceable_and_declares_the_transport_shapes(self):
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["family"], "openai-compatible-comfly-banana-image")
        self.assertEqual(self.contract["protocol"], "openai")
        self.assertEqual(self.contract["operations"], ["generation", "edit"])
        self.assertEqual(self.contract["request_shapes"]["generation"], "application/json")
        self.assertEqual(self.contract["request_shapes"]["edit"], "multipart/form-data")
        self.assertEqual(self.contract["response_shape"], {"data": [{"url": "string"}]})
        self.assertEqual(self.contract["retrieved_at"], "2026-08-09")
        self.assertEqual(
            {source["authority"] for source in self.contract["sources"]},
            {"primary-documentation", "external-reference-implementation", "local-implementation"},
        )
        for source in self.contract["sources"]:
            self.assertEqual(source["retrieved_at"], self.contract["retrieved_at"])
            self.assertTrue(source["supports"])
            if source["authority"] == "local-implementation":
                self.assertTrue((ROOT / source["path"]).is_file())
                self.assertTrue(source["symbols"])
            else:
                self.assertTrue(source["url"].startswith("https://"))
        self.assertNotIn(AUTHORIZATION, FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_zero_reference_matrix_reaches_one_final_json_request_without_rewriting_user_parameters(self):
        for case in self.contract["matrix"]:
            with self.subTest(resolution=case["resolution"], aspect_ratio=case["aspect_ratio"]):
                self.capture = ProviderContractCapture(self.contract["response_fixture"])
                route = self.route(case)
                image, raw = self.generate(case, route, [])

                request = self.capture.only_request()
                self.assertEqual(route.model, case["expected_model"])
                self.assertEqual(normalize_captured_request(request), case["expected_request"])
                self.assertEqual(raw, self.contract["response_fixture"])
                self.assertEqual(image["value"], self.contract["response_fixture"]["data"][0]["url"])

    def test_multipart_edit_preserves_reference_order_and_duplicate_occurrences(self):
        case = self.contract["matrix"][-1]
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.png"
            second = Path(temp_dir) / "second.png"
            Image.new("RGB", (16, 16), "red").save(first)
            Image.new("RGB", (16, 16), "blue").save(second)
            paths = {"/refs/first.png": str(first), "/refs/second.png": str(second)}
            references = [
                {"url": "/refs/first.png", "name": "first.png", "role": "source"},
                {"url": "/refs/second.png", "name": "second.png", "role": "source"},
                {"url": "/refs/first.png", "name": "first-again.png", "role": "source"},
            ]
            route = self.route(case, reference_ids=("first", "second", "first"))
            provider = self.provider()
            with (
                patch.object(APP, "get_api_provider_exact", return_value=provider),
                patch.object(APP, "output_file_from_url", side_effect=lambda url: paths[url]),
                patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
                patch.object(APP.httpx, "AsyncClient", self.capture.async_client),
            ):
                asyncio.run(APP.generate_ai_image(
                    "provider contract prompt", case["size"], "high", route.model, references,
                    provider["id"], preserve_model=True,
                    image_parameters={
                        "width": case["width"], "height": case["height"],
                        "aspect_ratio": case["aspect_ratio"], "resolution": case["resolution"],
                        "media_type": "image",
                    },
                ))

        request = self.capture.only_request()
        contract = next(item for item in self.contract["contracts"] if item["key"] == "openai-provider:comfly")
        self.assertEqual(normalize_captured_request(request), contract["final_request_golden"])
        self.assertNotIn(AUTHORIZATION, str(normalize_captured_request(request)))
        colors = []
        for _field, (_filename, payload, _mime_type) in request["files"]:
            with Image.open(BytesIO(payload)) as image:
                colors.append(image.convert("RGB").getpixel((0, 0)))
        self.assertEqual(colors, [(255, 0, 0), (0, 0, 255), (255, 0, 0)])

    def test_incompatible_request_fails_before_the_provider_executor(self):
        case = self.contract["matrix"][-1]
        provider = self.provider()
        provider["image_models"] = [self.contract["requested_model"]]
        provider["available_models"] = {"image_models": [self.contract["requested_model"]]}
        provider["image_model_capabilities"] = {
            self.contract["requested_model"]: {
                "media_types": ["image"],
                "sizes": ["1024x1024"],
                "aspect_ratios": ["1:1"],
                "resolutions": ["1k"],
                "max_reference_images": 0,
            }
        }
        request = ImageGenerationRequest(
            prompt="incompatible contract prompt",
            size=case["size"],
            width=case["width"],
            height=case["height"],
            aspect_ratio=case["aspect_ratio"],
            resolution=case["resolution"],
            reference_asset_ids=("first",),
        )

        with self.assertRaises(ModelRoutingError) as raised:
            route_image_generation(
                request,
                [provider],
                provider_id=provider["id"],
                model=self.contract["requested_model"],
            )

        self.assertIn("resolution", raised.exception.incompatible_parameters)
        self.assertIn("reference_count", raised.exception.incompatible_parameters)
        self.assertEqual(self.capture.requests, [])

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProjectStore(Path(temp_dir) / "projects", snapshot_delay=0)
            previous_store = APP.FIREFLY_PROJECT_STORE
            client = None
            try:
                APP.FIREFLY_PROJECT_STORE = store
                client = TestClient(
                    APP.app,
                    base_url="http://127.0.0.1",
                    headers={"origin": "http://127.0.0.1"},
                )
                executor = AsyncMock()
                project = store.create_project("provider contract incompatible")
                payload = {
                    "prompt": request.prompt,
                    "routing_mode": "manual",
                    "provider_id": provider["id"],
                    "model": self.contract["requested_model"],
                    "size": request.size,
                    "width": request.width,
                    "height": request.height,
                    "aspect_ratio": request.aspect_ratio,
                    "resolution": request.resolution,
                    "reference_asset_ids": [],
                    "media_type": "image",
                    "result_node_id": "new-result",
                    "base_revision": 0,
                }
                with (
                    patch.object(APP, "load_api_providers", return_value=[provider]),
                    patch.object(APP, "generate_ai_image", executor),
                ):
                    response = client.post(
                        f"/api/project-library/{project['id']}/image-generation-tasks",
                        json=payload,
                    )

                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"]["code"], "no_compatible_image_model")
                executor.assert_not_awaited()
                self.assertEqual(store.open_project(project["id"])["canvas"]["nodes"], [])
            finally:
                if client is not None:
                    client.close()
                APP.FIREFLY_PROJECT_STORE = previous_store
                store.close()


if __name__ == "__main__":
    unittest.main()
