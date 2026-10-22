import asyncio
import hashlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

import app.services as SERVICES
import app.services.conversation as CONVERSATION
import app.services.generation as GENERATION
import app.services.misc as MISC
import main as APP
from app.core import OnlineImageRequest, SUPPORTED_PROVIDER_PROTOCOLS
from fastapi.testclient import TestClient
from firefly_generation import ImageGenerationRequest, route_image_generation
from firefly_projects import ProjectStore
from PIL import Image
from tests.provider_contract import ProviderContractCapture, load_provider_contract, normalize_captured_request


AUTHORIZATION = "Bearer provider-family-contract-secret"
PROMPT = "provider family contract prompt"
SIZE = "2048x1152"
PARAMETERS = {
    "width": 2048,
    "height": 1152,
    "aspect_ratio": "16:9",
    "resolution": "2k",
    "media_type": "image",
}
REFERENCES = [
    {"url": "https://refs.invalid/a.png"},
    {"url": "https://refs.invalid/b.png"},
    {"url": "https://refs.invalid/a.png"},
]
MATRIX_CASES = {
    "1k": {
        "1:1": ("1024x1024", 1024, 1024),
        "16:9": ("1024x576", 1024, 576),
        "2:3": ("683x1024", 683, 1024),
    },
    "2k": {
        "1:1": ("2048x2048", 2048, 2048),
        "16:9": ("2048x1152", 2048, 1152),
        "2:3": ("1365x2048", 1365, 2048),
    },
    "4k": {
        "1:1": ("4096x4096", 4096, 4096),
        "16:9": ("4096x2304", 4096, 2304),
        "2:3": ("2731x4096", 2731, 4096),
    },
}
SUPPORTED_FAMILY_CONTRACT = load_provider_contract(
    Path(__file__).parent / "fixtures" / "provider_contracts" / "supported-image-request-families.json"
)
COMFLY_FAMILY_CONTRACT = load_provider_contract(
    Path(__file__).parent / "fixtures" / "provider_contracts" / "openai-comfly-banana-image.json"
)
FAMILY_CONTRACTS = [
    *SUPPORTED_FAMILY_CONTRACT["contracts"],
    *COMFLY_FAMILY_CONTRACT.get("contracts", []),
]
FAMILY_CONTRACTS_BY_KEY = {contract["key"]: contract for contract in FAMILY_CONTRACTS}
FINAL_REQUEST_GOLDENS = {
    contract["key"]: contract["final_request_golden"]
    for contract in FAMILY_CONTRACTS
}


def provider(protocol="openai", *, request_mode="openai", model="contract-image-model"):
    return {
        "id": f"contract-{protocol}-{request_mode}",
        "name": f"{protocol} contract fixture",
        "enabled": True,
        "protocol": protocol,
        "base_url": "https://provider.invalid/v1",
        "image_request_mode": request_mode,
        "image_models": [model],
        "available_models": {"image_models": [model]},
        "image_model_capabilities": {
            model: {
                "media_types": ["image"],
                "sizes": [SIZE],
                "aspect_ratios": ["16:9"],
                "resolutions": ["2k"],
                "max_reference_images": 20,
            }
        },
    }


def provider_for_family(key):
    protocol = key.removeprefix("protocol:") if key.startswith("protocol:") else "openai"
    request_mode = key.removeprefix("openai-mode:") if key.startswith("openai-mode:") else "openai"
    model = {
        "openai-model:gpt-image-2": "gpt-image-2",
        "openai-model:gemini-image": "gemini-2.5-flash-image",
        "openai-model:nano-banana": "nano-banana",
        "openai-provider:comfly": "nano-banana-pro",
    }.get(key, "contract-image-model")
    fixture = provider(protocol, request_mode=request_mode, model=model)
    if key == "openai-provider:comfly":
        fixture["id"] = "comfly"
    if key == "runninghub-mode:ai-app":
        fixture["mode"] = "ai-app"
    return fixture, model


class ProviderFamilyContractTests(unittest.TestCase):
    def test_capture_returns_scripted_responses_in_order_and_fails_when_exhausted(self):
        capture = ProviderContractCapture([{"step": 1}, {"step": 2}])

        async def exercise():
            async with capture.async_client() as client:
                first = await client.post("https://provider.invalid/first")
                second = await client.get("https://provider.invalid/second")
                self.assertEqual(first.json(), {"step": 1})
                self.assertEqual(second.json(), {"step": 2})
                with self.assertRaisesRegex(AssertionError, "response script exhausted"):
                    await client.post("https://provider.invalid/third")

        asyncio.run(exercise())

    def test_every_supported_protocol_routes_without_rewriting_user_parameters(self):
        request = ImageGenerationRequest(
            prompt=PROMPT,
            size=SIZE,
            width=2048,
            height=1152,
            aspect_ratio="16:9",
            resolution="2k",
            reference_asset_ids=("asset-a", "asset-b", "asset-a"),
            media_type="image",
            quality="high",
            count=1,
        )
        for protocol in sorted(SUPPORTED_PROVIDER_PROTOCOLS):
            with self.subTest(protocol=protocol):
                fixture = provider(protocol)
                route = route_image_generation(
                    request,
                    [fixture],
                    provider_id=fixture["id"],
                    model="contract-image-model",
                )
                self.assertEqual(route.request, request.to_dict())
                self.assertEqual(route.provider_id, fixture["id"])
                self.assertEqual(route.protocol, protocol)

    def test_every_family_routes_each_declared_resolution_without_rewriting_parameters(self):
        for contract in FAMILY_CONTRACTS:
            key = contract["key"]
            fixture, model = provider_for_family(key)
            fixture["image_model_capabilities"][model] = {
                "media_types": ["image"],
                "sizes": [case[0] for cases in MATRIX_CASES.values() for case in cases.values()],
                "aspect_ratios": list(contract["capability_matrix"]["aspect_ratios"]),
                "resolutions": list(contract["capability_matrix"]["resolutions"]),
                "max_reference_images": 20,
            }
            for resolution in contract["capability_matrix"]["resolutions"]:
                for aspect_ratio in contract["capability_matrix"]["aspect_ratios"]:
                    size, width, height = MATRIX_CASES[resolution][aspect_ratio]
                    request = ImageGenerationRequest(
                        prompt=PROMPT,
                        size=size,
                        width=width,
                        height=height,
                        aspect_ratio=aspect_ratio,
                        resolution=resolution,
                        reference_asset_ids=("asset-a", "asset-b", "asset-a"),
                        media_type="image",
                        quality="high",
                        count=1,
                    )
                    with self.subTest(family=key, resolution=resolution, aspect_ratio=aspect_ratio):
                        route = route_image_generation(
                            request,
                            [fixture],
                            provider_id=fixture["id"],
                            model=model,
                        )
                        self.assertEqual(route.request, request.to_dict())

    def test_every_registered_family_rejects_incompatible_requests_before_an_executor(self):
        family_keys = {
            key for contract in FAMILY_CONTRACTS for key in [contract["key"]]
        } | {"openai-provider:comfly"}
        request = ImageGenerationRequest(
            prompt=PROMPT,
            size="2352x3520",
            width=2352,
            height=3520,
            aspect_ratio="2:3",
            resolution="4k",
            reference_asset_ids=("asset-a",),
            media_type="image",
            quality="high",
            count=1,
        )
        with tempfile.TemporaryDirectory(prefix="firefly-family-incompatible-") as temp_dir:
            store = ProjectStore(Path(temp_dir) / "projects", snapshot_delay=0)
            previous_store = APP.FIREFLY_PROJECT_STORE
            client = None
            try:
                APP.FIREFLY_PROJECT_STORE = store
                client = TestClient(APP.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
                for key in sorted(family_keys):
                    fixture, model = provider_for_family(key)
                    fixture["image_model_capabilities"] = {
                        model: {
                            "media_types": ["image"],
                            "sizes": ["1024x1024"],
                            "aspect_ratios": ["1:1"],
                            "resolutions": ["1k"],
                            "max_reference_images": 0,
                        }
                    }
                    executor = AsyncMock()
                    project = store.create_project(f"incompatible {key}")
                    payload = {
                        "prompt": request.prompt,
                        "routing_mode": "manual",
                        "provider_id": fixture["id"],
                        "model": model,
                        "size": request.size,
                        "width": request.width,
                        "height": request.height,
                        "aspect_ratio": request.aspect_ratio,
                        "resolution": request.resolution,
                        "reference_asset_ids": [],
                        "media_type": "image",
                        "result_node_id": f"result-{len(project['id'])}",
                        "base_revision": 0,
                    }
                    with (
                        self.subTest(family=key),
                        patch.object(APP, "load_api_providers", return_value=[fixture]),
                        patch.object(APP, "generate_ai_image", executor),
                    ):
                        response = client.post(
                            f"/api/project-library/{project['id']}/image-generation-tasks",
                            json=payload,
                        )
                        self.assertEqual(response.status_code, 400, response.text)
                        detail = response.json()["detail"]
                        self.assertEqual(detail["code"], "no_compatible_image_model")
                        # 契约钉的是结构化字段和参数清单，不是整条文案。
                        # 6656f70 往 message 里加了「该模型支持什么、本次请求是什么」
                        # 的能力提示，整串相等断言当场过期（16 个 family 全红）。
                        message = detail["message"]
                        self.assertTrue(
                            message.startswith("没有手动固定的模型完整支持本次请求"),
                            message,
                        )
                        self.assertIn(
                            "（不兼容参数：size, width, height, aspect_ratio, resolution）",
                            message,
                        )
                        self.assertEqual(
                            detail["incompatible_parameters"],
                            ["size", "width", "height", "aspect_ratio", "resolution"],
                        )
                        executor.assert_not_awaited()
                        self.assertEqual(store.open_project(project["id"])["canvas"]["nodes"], [])
            finally:
                if client is not None:
                    client.close()
                APP.FIREFLY_PROJECT_STORE = previous_store
                store.close()

    def assert_openai_request(self, fixture, family_key, *, model="contract-image-model", references=REFERENCES):
        capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/fake.png"}]})
        with (
            patch.object(APP, "get_api_provider_exact", return_value=deepcopy(fixture)),
            patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
            patch.object(APP, "reference_to_data_url", side_effect=lambda ref, **_: ref["url"]),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
        ):
            asyncio.run(APP.generate_ai_image(
                PROMPT,
                SIZE,
                "high",
                model,
                references,
                fixture["id"],
                preserve_model=True,
                image_parameters=PARAMETERS,
            ))
        normalized = normalize_captured_request(capture.only_request())
        self.assertEqual(normalized, FINAL_REQUEST_GOLDENS[family_key])
        self.assertNotIn(AUTHORIZATION, str(normalized))

    def test_generic_openai_json_generation_reaches_the_final_request(self):
        fixture = provider()
        self.assert_openai_request(fixture, "protocol:openai", references=[])

    def test_openai_json_wrapper_preserves_ordered_duplicate_references(self):
        fixture = provider(request_mode="openai-json")
        self.assert_openai_request(fixture, "openai-mode:openai-json")

    def test_apimart_preserves_resolution_ratio_and_ordered_duplicate_references(self):
        fixture = provider("apimart")
        self.assert_openai_request(fixture, "protocol:apimart")

    def test_gpt_image_2_special_case_omits_generic_images_fields(self):
        fixture = provider(model="gpt-image-2")
        self.assert_openai_request(fixture, "openai-model:gpt-image-2", model="gpt-image-2", references=[])

    def test_gemini_image_model_routes_to_the_openai_images_boundary(self):
        fixture = provider(model="gemini-2.5-flash-image")
        self.assert_openai_request(
            fixture,
            "openai-model:gemini-image",
            model="gemini-2.5-flash-image",
            references=[],
        )

    def test_nano_banana_model_reaches_its_final_json_request(self):
        fixture, model = provider_for_family("openai-model:nano-banana")
        self.assert_openai_request(
            fixture,
            "openai-model:nano-banana",
            model=model,
            references=[],
        )

    def test_openai_image_edit_families_preserve_ordered_duplicate_references(self):
        self.maxDiff = None
        family_keys = (
            "protocol:openai",
            "openai-model:gpt-image-2",
            "openai-model:gemini-image",
            "openai-model:nano-banana",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.png"
            second = Path(temp_dir) / "second.png"
            Image.new("RGB", (16, 16), "red").save(first)
            Image.new("RGB", (16, 16), "blue").save(second)
            paths = {"/refs/first.png": str(first), "/refs/second.png": str(second)}
            references = [
                {"url": "/refs/first.png"},
                {"url": "/refs/second.png"},
                {"url": "/refs/first.png"},
            ]
            for key in family_keys:
                with self.subTest(family=key):
                    fixture, model = provider_for_family(key)
                    capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/output.png"}]})
                    with (
                        patch.object(APP, "get_api_provider_exact", return_value=fixture),
                        patch.object(APP, "output_file_from_url", side_effect=lambda url: paths[url]),
                        patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
                        patch.object(APP.httpx, "AsyncClient", capture.async_client),
                    ):
                        asyncio.run(APP.generate_ai_image(
                            PROMPT, SIZE, "high", model, references, fixture["id"],
                            preserve_model=True, image_parameters=PARAMETERS,
                        ))
                    self.assertEqual(
                        normalize_captured_request(capture.only_request()),
                        FAMILY_CONTRACTS_BY_KEY[key]["reference_edit_golden"],
                    )

    def test_gemini_image_model_uses_tier_aliases_for_every_declared_ratio(self):
        base_model = "gemini-2.5-flash-image"
        aliases = {"1k": base_model, "2k": f"{base_model}-2k", "4k": f"{base_model}-4k"}
        fixture = provider(model=base_model)
        fixture["image_models"] = list(aliases.values())
        fixture["available_models"] = {"image_models": list(aliases.values())}
        fixture["image_model_capabilities"] = {
            model: {
                "media_types": ["image"],
                "sizes": [case[0] for cases in MATRIX_CASES.values() for case in cases.values()],
                "aspect_ratios": ["1:1", "16:9", "2:3"],
                "resolutions": ["1k", "2k", "4k"],
                "max_reference_images": 20,
            }
            for model in aliases.values()
        }
        for resolution, aspect_cases in MATRIX_CASES.items():
            for aspect_ratio, (size, width, height) in aspect_cases.items():
                capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/fake.png"}]})
                parameters = {
                    "width": width,
                    "height": height,
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                    "media_type": "image",
                }
                with (
                    self.subTest(resolution=resolution, aspect_ratio=aspect_ratio),
                    patch.object(APP, "get_api_provider_exact", return_value=deepcopy(fixture)),
                    patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
                    patch.object(APP.httpx, "AsyncClient", capture.async_client),
                ):
                    asyncio.run(APP.generate_ai_image(
                        PROMPT,
                        size,
                        "high",
                        base_model,
                        [],
                        fixture["id"],
                        preserve_model=True,
                        image_parameters=parameters,
                    ))
                    body = capture.only_request()["json"]
                    self.assertEqual(body["model"], aliases[resolution])
                    self.assertEqual(body["resolution"], resolution)
                    self.assertEqual(body["image_size"], resolution.upper())
                    self.assertEqual(body["aspect_ratio"], aspect_ratio)

    def test_gemini_rest_preserves_image_config_and_ordered_duplicate_parts(self):
        fixture = provider("gemini", model="gemini-2.5-flash-image")
        capture = ProviderContractCapture({
            "candidates": [{
                "content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "YWJj"}}]}
            }]
        })
        with (
            patch.object(APP, "get_api_provider_exact", return_value=fixture),
            patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
            patch.object(SERVICES, "gemini_endpoint_url", return_value="https://provider.invalid/v1/models/gemini-2.5-flash-image:generateContent"),
            patch.object(SERVICES, "gemini_reference_part", side_effect=lambda ref: {"fileData": {"fileUri": ref["url"]}}),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
        ):
            asyncio.run(APP.generate_ai_image(
                PROMPT, SIZE, "high", "gemini-2.5-flash-image", REFERENCES,
                fixture["id"], preserve_model=True, image_parameters=PARAMETERS,
            ))
        self.assertEqual(
            normalize_captured_request(capture.only_request()),
            FINAL_REQUEST_GOLDENS["protocol:gemini"],
        )

    def test_volcengine_preserves_size_and_ordered_duplicate_images(self):
        fixture = provider("volcengine", model="doubao-seedream-4-0-250828")
        capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/fake.png"}]})
        with (
            patch.object(APP, "get_api_provider_exact", return_value=fixture),
            patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
            patch.object(SERVICES, "volcengine_endpoint_url", return_value="https://provider.invalid/v1/images/generations"),
            patch.object(SERVICES, "volcengine_image_payload", side_effect=lambda ref: ref["url"]),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
        ):
            asyncio.run(APP.generate_ai_image(
                PROMPT, SIZE, "high", "doubao-seedream-4-0-250828", REFERENCES,
                fixture["id"], preserve_model=True, image_parameters=PARAMETERS,
            ))
        self.assertEqual(
            normalize_captured_request(capture.only_request()),
            FINAL_REQUEST_GOLDENS["protocol:volcengine"],
        )

    def test_openai_responses_preserves_ordered_duplicate_input_images(self):
        fixture = provider(request_mode="openai-responses")
        capture = ProviderContractCapture({"output": [{"type": "image_generation_call", "result": "YWJj"}]})

        with (
            patch.object(APP, "get_api_provider_exact", return_value=fixture),
            patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
            patch.object(SERVICES, "provider_endpoint_url", return_value="https://provider.invalid/v1/responses"),
            patch.object(SERVICES, "responses_input_image_url", new=AsyncMock(side_effect=lambda ref, **_: ref["url"])),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
        ):
            asyncio.run(APP.generate_ai_image(
                PROMPT, SIZE, "high", "contract-image-model", REFERENCES,
                fixture["id"], preserve_model=True, image_parameters=PARAMETERS,
            ))
        self.assertEqual(
            normalize_captured_request(capture.only_request()),
            FINAL_REQUEST_GOLDENS["openai-mode:openai-responses"],
        )

    def test_openai_video_proxy_preserves_resolution_and_ordered_duplicate_images(self):
        fixture = provider(request_mode="openai-video-proxy")
        capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/fake.png"}]})

        with (
            patch.object(APP, "get_api_provider_exact", return_value=fixture),
            patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
            patch.object(SERVICES, "openai_video_proxy_local_image_path", return_value=""),
            patch.object(SERVICES, "openai_video_proxy_public_reference_url", new=AsyncMock(side_effect=lambda ref: ref["url"])),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
        ):
            asyncio.run(APP.generate_ai_image(
                PROMPT, SIZE, "high", "contract-image-model", REFERENCES,
                fixture["id"], preserve_model=True, image_parameters=PARAMETERS,
            ))
        normalized = normalize_captured_request(capture.only_request())
        self.assertEqual(normalized, FINAL_REQUEST_GOLDENS["openai-mode:openai-video-proxy"])

    def test_runninghub_schema_endpoint_preserves_parameters_and_reference_order(self):
        fixture = provider("runninghub", model="contract/schema-image")
        capture = ProviderContractCapture({"data": {"results": [{"url": "http://127.0.0.1/fake.png"}]}})
        model_definition = {
            "endpoint": "/openapi/contract/schema-image",
            "params": [
                {"fieldKey": "aspectRatio"},
                {"fieldKey": "resolution"},
                {"fieldKey": "imageUrls", "multipleInputs": True},
            ],
        }
        with (
            patch.object(APP, "get_api_provider_exact", return_value=fixture),
            patch.object(APP, "runninghub_model_definition", new=AsyncMock(return_value=model_definition)),
            patch.object(SERVICES, "runninghub_upload_reference", new=AsyncMock(side_effect=lambda _client, _provider, ref: ref["url"])),
            patch.object(SERVICES, "runninghub_json_headers", return_value={"Authorization": AUTHORIZATION}),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
        ):
            asyncio.run(APP.generate_ai_image(
                PROMPT, SIZE, "high", "contract/schema-image", REFERENCES,
                fixture["id"], preserve_model=True, image_parameters=PARAMETERS,
            ))
        self.assertEqual(
            normalize_captured_request(capture.only_request()),
            FINAL_REQUEST_GOLDENS["protocol:runninghub"],
        )

    def test_runninghub_ai_app_preserves_prompt_and_ordered_duplicate_references(self):
        fixture = provider("runninghub", model="contract-app")
        fixture.update({"mode": "ai-app", "api_key": "contract-api-key"})
        capture = ProviderContractCapture([
            {"code": 0, "data": {"taskId": "contract-task"}},
            {"code": 0, "data": [{"fileUrl": "http://127.0.0.1/fake.png"}]},
        ])
        entry = {
            "id": "contract-app",
            "fields": [
                {"enabled": True, "nodeId": "1", "fieldName": "imageA", "fieldType": "IMAGE", "imageOrder": 1},
                {"enabled": True, "nodeId": "2", "fieldName": "imageB", "fieldType": "IMAGE", "imageOrder": 2},
                {"enabled": True, "nodeId": "3", "fieldName": "imageC", "fieldType": "IMAGE", "imageOrder": 3},
                {"enabled": True, "nodeId": "4", "fieldName": "prompt", "fieldType": "TEXT", "label": "Prompt"},
            ],
        }

        async def upload_reference(_client, _provider, reference_url, _use_wallet):
            return {REFERENCES[0]["url"]: "A.png", REFERENCES[1]["url"]: "B.png"}[reference_url]

        with (
            patch.object(APP, "runninghub_upload_local_to_filename", side_effect=upload_reference),
            patch.object(SERVICES, "runninghub_app_headers", return_value={"Authorization": AUTHORIZATION}),
            patch.object(SERVICES, "runninghub_api_key", return_value="contract-api-key"),
            patch.object(APP.httpx, "AsyncClient", capture.async_client),
            patch.object(GENERATION.asyncio, "sleep", new=AsyncMock()),
        ):
            output, _raw = asyncio.run(GENERATION.generate_runninghub_entry_image(
                PROMPT,
                SIZE,
                "contract-app",
                REFERENCES,
                fixture,
                entry,
                PARAMETERS,
            ))

        self.assertEqual(output, {"type": "url", "value": "http://127.0.0.1/fake.png"})
        self.assertEqual(
            [normalize_captured_request(request) for request in capture.requests],
            FINAL_REQUEST_GOLDENS["runninghub-mode:ai-app"],
        )

    def test_jimeng_cli_preserves_ordered_duplicate_reference_arguments(self):
        fixture = provider("jimeng", model="jimeng-4.5")
        observed = []

        class Process:
            returncode = 0

            async def communicate(self):
                return (b'{"outputs":["http://127.0.0.1/fake.png"]}', b"")

        async def prepare(url, _kind):
            return ({REFERENCES[0]["url"]: "A.png", REFERENCES[1]["url"]: "B.png"}[url], [])

        async def create_process(*args, **kwargs):
            observed.append({"args": list(args), "kwargs": kwargs})
            return Process()

        with (
            patch.object(APP, "get_api_provider_exact", return_value=fixture),
            patch.object(APP, "jimeng_prepare_local_media", side_effect=prepare),
            patch.object(APP, "jimeng_store_outputs", new=AsyncMock(return_value=["http://127.0.0.1/fake.png"])),
            patch.object(SERVICES, "jimeng_cli_executable", return_value="dreamina-contract"),
            patch.object(SERVICES, "jimeng_use_wsl", return_value=False),
            patch.object(CONVERSATION.asyncio, "create_subprocess_exec", side_effect=create_process),
        ):
            asyncio.run(APP.generate_ai_image(
                PROMPT, SIZE, "high", "jimeng-4.5", REFERENCES,
                fixture["id"], preserve_model=True, image_parameters=PARAMETERS,
            ))
        self.assertEqual(len(observed), 1)
        golden = FINAL_REQUEST_GOLDENS["protocol:jimeng"]
        self.assertEqual(observed[0]["args"], golden["argv"])

    def test_codex_skill_preserves_ordered_duplicate_ref_image_arguments(self):
        fixture = provider("codex", model="gpt-image-2")
        observed = []

        class Process:
            returncode = 0

            async def communicate(self):
                return (b"{}", b"")

        with tempfile.TemporaryDirectory(prefix="firefly-provider-codex-") as tmp:
            root = Path(tmp)
            first = root / "A.png"
            second = root / "B.png"
            first.write_bytes(b"A")
            second.write_bytes(b"B")

            async def create_process(*args, **kwargs):
                observed.append({"args": list(args), "kwargs": kwargs})
                output = Path(args[args.index("--out") + 1])
                output.write_bytes(b"fake image")
                return Process()

            with (
                patch.object(APP, "get_api_provider_exact", return_value=fixture),
                patch.object(APP, "OUTPUT_OUTPUT_DIR", str(root)),
                patch.object(APP, "gpt_image_2_skill_executable", return_value="gpt-image-2-contract"),
                patch.object(SERVICES, "codex_reference_paths", new=AsyncMock(return_value=([str(first), str(second), str(first)], []))),
                patch.object(SERVICES, "gpt_image_2_skill_auth_file", return_value=""),
                patch.object(SERVICES, "gpt_image_2_skill_provider_args", return_value=(["--provider", "codex"], "codex")),
                patch.object(SERVICES, "gpt_image_2_skill_api_key", return_value=""),
                patch.object(SERVICES, "codex_output_url_from_path", return_value="http://127.0.0.1/fake.png"),
                patch.object(GENERATION, "gpt_image_2_skill_auth_json", return_value={}),
                patch.object(GENERATION, "codex_postprocess_image_to_requested_size", side_effect=lambda path, *_: path),
                patch.object(GENERATION.asyncio, "create_subprocess_exec", side_effect=create_process),
            ):
                asyncio.run(APP.generate_ai_image(
                    PROMPT, SIZE, "high", "gpt-image-2", REFERENCES,
                    fixture["id"], preserve_model=True, image_parameters=PARAMETERS,
                ))
        self.assertEqual(len(observed), 1)
        args = observed[0]["args"]
        prompt_arg = args[args.index("--prompt") + 1]
        normalized = [
            "<output>" if index and args[index - 1] == "--out" else
            "<prompt>" if index and args[index - 1] == "--prompt" else
            "A.png" if value == str(first) else
            "B.png" if value == str(second) else value
            for index, value in enumerate(args)
        ]
        golden = FINAL_REQUEST_GOLDENS["protocol:codex"]
        self.assertEqual(normalized, golden["argv"])
        self.assertEqual(prompt_arg, golden["prompt"])
        self.assertEqual(hashlib.sha256(prompt_arg.encode("utf-8")).hexdigest(), golden["prompt_sha256"])

    def test_gemini_cli_freezes_the_full_prompt_and_duplicate_reference_order(self):
        fixture = provider("gemini-cli", model="gemini-2.5-flash-image")
        observed = []

        class Process:
            returncode = 0

            async def communicate(self):
                return (b'{"response":"created"}', b"")

        async def create_process(*args, **kwargs):
            observed.append({"args": list(args), "kwargs": kwargs})
            return Process()

        with tempfile.TemporaryDirectory(prefix="firefly-provider-gemini-cli-") as tmp:
            root = Path(tmp)
            first = root / "A.png"
            second = root / "B.png"
            output = root / "result.png"
            with (
                patch.object(APP, "get_api_provider_exact", return_value=fixture),
                patch.object(APP, "OUTPUT_OUTPUT_DIR", str(root)),
                patch.object(APP, "gemini_cli_executable", return_value="agy-contract.exe"),
                patch.object(APP, "gemini_cli_image_timeout", return_value=300),
                patch.object(SERVICES, "gemini_cli_reference_paths", new=AsyncMock(return_value=([str(first), str(second), str(first)], []))),
                patch.object(SERVICES, "codex_output_image_files", return_value=[str(output)]),
                patch.object(SERVICES, "codex_output_url_from_path", return_value="http://127.0.0.1/fake.png"),
                patch.object(GENERATION, "codex_postprocess_image_to_requested_size", side_effect=lambda path, *_: path),
                patch.object(CONVERSATION.asyncio, "create_subprocess_exec", side_effect=create_process),
            ):
                asyncio.run(APP.generate_ai_image(
                    PROMPT, SIZE, "high", "gemini-2.5-flash-image", REFERENCES,
                    fixture["id"], preserve_model=True, image_parameters=PARAMETERS,
                ))
        self.assertEqual(len(observed), 1)
        args = observed[0]["args"]
        prompt = args[-1].replace(str(root), "<root>")
        normalized_args = [prompt if index == len(args) - 1 else value for index, value in enumerate(args)]
        golden = FINAL_REQUEST_GOLDENS["protocol:gemini-cli"]
        self.assertEqual(normalized_args, golden["argv"])
        self.assertEqual(prompt, golden["prompt"])
        cursor = -1
        for reference in golden["ordered_reference_tokens"]:
            cursor = prompt.find(reference, cursor + 1)
            self.assertGreaterEqual(cursor, 0, reference)
        self.assertEqual(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), golden["prompt_sha256"])

    def test_route_discovers_tier_aliases_from_declared_model_lists_and_falls_back_to_the_pinned_model(self):
        cases = [
            ("nano-banana", "2k", ["nano-banana", "nano-banana-hd"], "nano-banana-hd"),
            ("gemini-2.5-flash-image", "2k", ["gemini-2.5-flash-image", "gemini-2.5-flash-image-2k"], "gemini-2.5-flash-image-2k"),
            ("gemini-2.5-flash-image", "4k", ["gemini-2.5-flash-image", "gemini-2.5-flash-image-4k"], "gemini-2.5-flash-image-4k"),
            ("nano-banana", "1k", ["nano-banana", "nano-banana-hd"], "nano-banana"),
            ("gemini-2.5-flash-image", "1k", ["gemini-2.5-flash-image", "gemini-2.5-flash-image-2k"], "gemini-2.5-flash-image"),
            # The variant is only used when the provider declares it; otherwise the pinned model is kept.
            ("gemini-2.5-flash-image", "2k", ["gemini-2.5-flash-image"], "gemini-2.5-flash-image"),
        ]
        discovery_sizes = {
            "1k": ("1024x1024", 1024, 1024, "1:1"),
            "2k": ("2048x1152", 2048, 1152, "16:9"),
            "4k": ("4096x4096", 4096, 4096, "1:1"),
        }
        for pinned_model, resolution, declared, expected in cases:
            size, width, height, aspect_ratio = discovery_sizes[resolution]
            request = ImageGenerationRequest(
                prompt=PROMPT,
                size=size,
                width=width,
                height=height,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                reference_asset_ids=("asset-a", "asset-b", "asset-a"),
                media_type="image",
                quality="high",
                count=1,
            )
            fixture = provider(model=declared[0])
            fixture["image_models"] = declared
            fixture["available_models"] = {"image_models": declared}
            fixture["image_model_capabilities"] = {}
            with self.subTest(model=pinned_model, resolution=resolution, declared=declared):
                route = route_image_generation(
                    request,
                    [fixture],
                    provider_id=fixture["id"],
                    model=pinned_model,
                )
                self.assertEqual(route.model, expected)
                self.assertEqual(route.request, request.to_dict())

    def test_nano_banana_tier_alias_used_and_preserves_user_ratio_and_resolution(self):
        base_model = "nano-banana"
        aliases = {"1k": base_model, "2k": "nano-banana-hd", "4k": "nano-banana-hd"}
        fixture = provider(model=base_model)
        fixture["image_models"] = [base_model, "nano-banana-hd"]
        fixture["available_models"] = {"image_models": [base_model, "nano-banana-hd"]}
        fixture["image_model_capabilities"] = {
            model: {
                "media_types": ["image"],
                "sizes": [case[0] for cases in MATRIX_CASES.values() for case in cases.values()],
                "aspect_ratios": ["1:1", "16:9", "2:3"],
                "resolutions": ["1k", "2k", "4k"],
                "max_reference_images": 20,
            }
            for model in (base_model, "nano-banana-hd")
        }
        for resolution, aspect_cases in MATRIX_CASES.items():
            for aspect_ratio, (size, width, height) in aspect_cases.items():
                capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/fake.png"}]})
                parameters = {
                    "width": width,
                    "height": height,
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                    "media_type": "image",
                }
                with (
                    self.subTest(resolution=resolution, aspect_ratio=aspect_ratio),
                    patch.object(APP, "get_api_provider_exact", return_value=deepcopy(fixture)),
                    patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
                    patch.object(APP.httpx, "AsyncClient", capture.async_client),
                ):
                    asyncio.run(APP.generate_ai_image(
                        PROMPT,
                        size,
                        "high",
                        base_model,
                        [],
                        fixture["id"],
                        preserve_model=True,
                        image_parameters=parameters,
                    ))
                    body = capture.only_request()["json"]
                    self.assertEqual(body["model"], aliases[resolution])
                    self.assertEqual(body["aspect_ratio"], aspect_ratio)
                    self.assertEqual(body["image_size"], resolution.upper())
                    self.assertEqual(body["resolution"], resolution)

    def test_gemini_tier_alias_preserves_ordered_duplicate_references(self):
        base_model = "gemini-2.5-flash-image"
        alias = f"{base_model}-2k"
        fixture = provider(model=base_model)
        fixture["image_models"] = [alias]
        fixture["available_models"] = {"image_models": [alias]}
        fixture["image_model_capabilities"] = {
            alias: {
                "media_types": ["image"],
                "sizes": [case[0] for cases in MATRIX_CASES.values() for case in cases.values()],
                "aspect_ratios": ["1:1", "16:9", "2:3"],
                "resolutions": ["1k", "2k", "4k"],
                "max_reference_images": 20,
            }
        }
        size, width, height = MATRIX_CASES["2k"]["16:9"]
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.png"
            second = Path(temp_dir) / "second.png"
            Image.new("RGB", (16, 16), "red").save(first)
            Image.new("RGB", (16, 16), "blue").save(second)
            paths = {"/refs/first.png": str(first), "/refs/second.png": str(second)}
            references = [
                {"url": "/refs/first.png"},
                {"url": "/refs/second.png"},
                {"url": "/refs/first.png"},
            ]
            capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/fake.png"}]})
            parameters = {
                "width": width,
                "height": height,
                "aspect_ratio": "16:9",
                "resolution": "2k",
                "media_type": "image",
            }
            with (
                patch.object(APP, "get_api_provider_exact", return_value=deepcopy(fixture)),
                patch.object(APP, "output_file_from_url", side_effect=lambda url: paths[url]),
                patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
                patch.object(APP.httpx, "AsyncClient", capture.async_client),
            ):
                asyncio.run(APP.generate_ai_image(
                    PROMPT,
                    size,
                    "high",
                    base_model,
                    references,
                    fixture["id"],
                    preserve_model=True,
                    image_parameters=parameters,
                ))
            normalized = normalize_captured_request(capture.only_request())
            self.assertEqual(normalized["data"]["model"], alias)
            self.assertEqual(normalized["data"]["aspect_ratio"], "16:9")
            self.assertEqual(normalized["data"]["resolution"], "2k")
            self.assertEqual(
                [entry["filename"] for entry in normalized["files"]],
                ["first.png", "second.png", "first.png"],
            )

    def test_legacy_online_image_path_and_project_library_path_share_the_same_accepted_contract(self):
        cases = [
            {
                "name": "plain openai family without user ratio",
                "model": "contract-image-model",
                "image_models": ["contract-image-model"],
                "parameters": {"width": 2048, "height": 1152, "aspect_ratio": "", "resolution": "", "media_type": "image"},
            },
            {
                "name": "gemini family discovers tier alias from size on both paths",
                "model": "gemini-2.5-flash-image",
                "image_models": ["gemini-2.5-flash-image", "gemini-2.5-flash-image-2k"],
                "parameters": {"width": 2048, "height": 1152, "aspect_ratio": "16:9", "resolution": "", "media_type": "image"},
            },
        ]
        for case in cases:
            with self.subTest(case=case["name"]):
                fixture = provider(model=case["model"])
                fixture["image_models"] = case["image_models"]
                fixture["available_models"] = {"image_models": case["image_models"]}
                fixture["image_model_capabilities"] = {
                    model: {
                        "media_types": ["image"],
                        "sizes": [size for sizes in MATRIX_CASES.values() for size, _w, _h in sizes.values()],
                        "aspect_ratios": ["1:1", "16:9", "2:3"],
                        "resolutions": ["1k", "2k", "4k"],
                        "max_reference_images": 20,
                    }
                    for model in case["image_models"]
                }
                legacy_capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/legacy.png"}]})
                with (
                    patch.object(APP, "get_api_provider", return_value=deepcopy(fixture)),
                    patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
                    patch.object(APP.httpx, "AsyncClient", legacy_capture.async_client),
                    patch.object(SERVICES, "save_ai_image_to_output", new=AsyncMock(return_value="http://127.0.0.1/legacy.png")),
                    patch.object(SERVICES, "image_output_meta", return_value={"url": "http://127.0.0.1/legacy.png"}),
                    patch.object(SERVICES, "save_to_history", return_value=None),
                    patch.object(MISC, "GLOBAL_LOOP", None),
                ):
                    asyncio.run(MISC.build_online_image_result(OnlineImageRequest(
                        prompt=PROMPT,
                        size=SIZE,
                        quality="high",
                        model="",
                        provider_id="comfly",
                        n=1,
                        reference_images=[],
                    )))
                new_path_capture = ProviderContractCapture({"data": [{"url": "http://127.0.0.1/new.png"}]})
                with (
                    patch.object(APP, "get_api_provider_exact", return_value=deepcopy(fixture)),
                    patch.object(APP, "api_headers", return_value={"Authorization": AUTHORIZATION}),
                    patch.object(APP.httpx, "AsyncClient", new_path_capture.async_client),
                ):
                    asyncio.run(APP.generate_ai_image(
                        PROMPT,
                        SIZE,
                        "high",
                        case["model"],
                        [],
                        fixture["id"],
                        preserve_model=True,
                        image_parameters=case["parameters"],
                    ))
                self.assertEqual(
                    normalize_captured_request(new_path_capture.only_request()),
                    normalize_captured_request(legacy_capture.only_request()),
                )


if __name__ == "__main__":
    unittest.main()
