import asyncio
import base64
import json
import io
import sys
import tempfile
import threading
import time
import unittest
import httpx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore


def box(kind, payload):
    return (len(payload) + 8).to_bytes(4, "big") + kind.encode("ascii") + payload


def small_structural_mp4():
    """A tiny ISO-BMFF video container used only by the fake online provider.

    It has an ftyp and a declared vide handler, which is enough to exercise the
    project archiver's container validation without making any network or paid
    model request.
    """
    ftyp = box("ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")
    hdlr = box("hdlr", b"\x00\x00\x00\x00\x00\x00\x00\x00vide\x00\x00\x00\x00")
    return ftyp + box("moov", box("trak", box("mdia", hdlr))) + box("mdat", b"\x00")


def tiny_png_bytes():
    image = Image.new("RGB", (5, 3), (38, 190, 133))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class ProjectVideoGenerationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.provider_key_patch = patch.object(main, "provider_env_key_value", return_value="test-api-key")
        self.ai_base_url_patch = patch.object(main, "AI_BASE_URL", "https://video.example.invalid")
        self.provider_key_patch.start()
        self.ai_base_url_patch.start()
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.providers = [{
            "id": "fake-video-http",
            "name": "假在线视频服务",
            "enabled": True,
            "protocol": "openai",
            "video_models": ["text-video", "image-video", "multimodal-video"],
            "video_model_capabilities": {
                "text-video": {
                    "sizes": ["1280x720"], "aspect_ratios": ["16:9"], "resolutions": ["720p"],
                    "min_duration": 5, "max_duration": 5,
                    "max_image_references": 0, "max_video_references": 0, "max_audio_references": 0,
                },
                "image-video": {
                    "sizes": ["1280x720"], "aspect_ratios": ["16:9"], "resolutions": ["720p"],
                    "min_duration": 5, "max_duration": 5,
                    "max_image_references": 2, "max_video_references": 0, "max_audio_references": 0,
                },
                "multimodal-video": {
                    "sizes": ["1280x720"], "aspect_ratios": ["16:9"], "resolutions": ["720p"],
                    "min_duration": 5, "max_duration": 5,
                    "max_image_references": 3, "max_video_references": 2, "max_audio_references": 2,
                },
            },
        }]

    def tearDown(self):
        self.client.close()
        self.ai_base_url_patch.stop()
        self.provider_key_patch.stop()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.clear()
        getattr(main, "PROJECT_VIDEO_GENERATION_ORPHANS", {}).clear()
        self.temp_dir.cleanup()

    def wait_for_task(self, task_id):
        # #204：这个截止是测试自身的负载容忍度，不是产品 SLO——它测的是
        # 任务 API 生命周期（202 -> 后台适配 -> succeeded），供应商调用全部
        # mock（in-process TestClient + 本地 ThreadingHTTPServer，亚秒返回）。
        # 原来是 4s：门里 8 个 unit 分片并行时机器饱和，后台执行器/轮询偶发
        # 超 4s，测试就红（实测 2/27）。放宽到 15s（名义完成时间 <1s 的 15 倍）；
        # 真回归（任务卡死/异常被吞）仍然会在 15s 确定性地 fail，不会漏。
        deadline = time.monotonic() + 15
        task = None
        while time.monotonic() < deadline:
            task = self.client.get(f"/api/canvas-image-tasks/{task_id}").json()
            if task["status"] in {"succeeded", "failed"}:
                return task
            time.sleep(0.02)
        self.fail(f"video task did not finish: {task}")

    def add_result_node(self, project, node_id="video-result"):
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": node_id, "type": "smart-image", "x": 120, "y": 80, "images": []}}],
            base_revision=0,
        )

    def payload(self, node_id="video-result", **overrides):
        data = {
            "prompt": "一只萤火虫飞过湖面", "routing_mode": "auto", "size": "1280x720",
            "width": 1280, "height": 720, "aspect_ratio": "16:9", "resolution": "720p",
            "duration": 5, "quality": "high", "n": 1, "seed": 42,
            "enhance_prompt": False, "enable_upsample": False, "watermark": False,
            "camera_fixed": True, "generate_audio": False,
            "image_reference_asset_ids": [], "video_reference_asset_ids": [], "audio_reference_asset_ids": [],
            "media_type": "video", "result_node_id": node_id, "base_revision": 1,
        }
        data.update(overrides)
        return data

    def submit(self, project, payload, fake_generate):
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(
            main, "generate_project_video", fake_generate, create=True
        ):
            response = self.client.post(f"/api/project-library/{project['id']}/video-generation-tasks", json=payload)
            self.assertEqual(response.status_code, 202, response.text)
            submitted = response.json()
            return submitted, self.wait_for_task(submitted["task_id"])

    def test_project_entry_uses_adapter_executability_before_registering_auto_or_manual_task(self):
        project = self.store.create_project("适配器同步路由")
        self.add_result_node(project)
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
                "id": "jimeng",
                "name": "即梦",
                "enabled": True,
                "protocol": "jimeng",
                "video_models": ["seedance2.0_vip"],
                "video_model_capabilities": {"seedance2.0_vip": capability},
            },
            {
                "id": "generic-http",
                "name": "通用 HTTP",
                "enabled": True,
                "protocol": "openai",
                "video_models": ["generic-video"],
                "video_model_capabilities": {"generic-video": capability},
            },
        ]
        fake_generate = AsyncMock(return_value=(
            [{"type": "b64", "value": base64.b64encode(small_structural_mp4()).decode("ascii"), "mime_type": "video/mp4"}],
            {"id": "fake-task"},
        ))
        endpoint = f"/api/project-library/{project['id']}/video-generation-tasks"
        canvas_before = self.store.open_project(project["id"])["canvas"]

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ):
            manual = self.client.post(endpoint, json=self.payload(
                routing_mode="manual", provider_id="jimeng", model="seedance2.0_vip",
            ))

        self.assertEqual(manual.status_code, 400, manual.text)
        self.assertEqual(
            set(manual.json()["detail"]["incompatible_parameters"]),
            {"width", "height", "size", "quality", "seed", "camera_fixed"},
        )
        self.assertEqual(self.store.open_project(project["id"])["canvas"], canvas_before)
        with main.CANVAS_TASK_LOCK:
            self.assertFalse(any(task.get("project_id") == project["id"] for task in main.CANVAS_TASKS.values()))
        fake_generate.assert_not_awaited()

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ):
            automatic = self.client.post(endpoint, json=self.payload())
            self.assertEqual(automatic.status_code, 202, automatic.text)
            submitted = automatic.json()
            task = self.wait_for_task(submitted["task_id"])

        self.assertEqual((submitted["route"]["provider_id"], submitted["route"]["model"]), ("generic-http", "generic-video"))
        self.assertEqual(task["status"], "succeeded", task)
        fake_generate.assert_awaited_once()

    def test_project_entry_preflights_runninghub_schema_with_real_references(self):
        project = self.store.create_project("RunningHub schema 预检")
        self.add_result_node(project)
        images = [
            self.store.import_media(project["id"], tiny_png_bytes(), f"reference-{index}.png", "image/png")
            for index in range(2)
        ]
        capability = {
            "arbitrary_size": True,
            "arbitrary_aspect_ratio": True,
            "arbitrary_resolution": True,
            "arbitrary_quality": True,
            "min_duration": 1,
            "max_duration": 60,
            "max_image_references": 2,
            "max_video_references": 0,
            "max_audio_references": 0,
        }
        runninghub_model = "schema-limited-video"
        providers = [
            {
                "id": "runninghub",
                "name": "RunningHub",
                "enabled": True,
                "protocol": "runninghub",
                "wallet_api_key": "test-wallet-key",
                "video_models": [runninghub_model],
                "video_model_capabilities": {
                    runninghub_model: {
                        **capability,
                        "adapter_schema": {
                            "endpoint": "schema-limited/video",
                            "params": [
                                {"fieldKey": "aspectRatio", "options": [{"value": "16:9"}]},
                                {"fieldKey": "duration", "options": [{"value": "5"}]},
                                {"fieldKey": "resolution", "options": [{"value": "720p"}]},
                                {"fieldKey": "imageUrl", "multipleInputs": False},
                            ],
                        },
                    },
                },
            },
            {
                "id": "generic-http",
                "name": "通用 HTTP",
                "enabled": True,
                "protocol": "openai",
                "video_models": ["generic-video"],
                "video_model_capabilities": {"generic-video": capability},
            },
        ]
        request_payload = self.payload(
            size="", width=None, height=None, aspect_ratio="9:16", resolution="1080p",
            duration=7, quality="auto", seed=None, camera_fixed=False,
            image_reference_asset_ids=[item["asset_id"] for item in images],
        )
        endpoint = f"/api/project-library/{project['id']}/video-generation-tasks"
        fake_generate = AsyncMock(return_value=(
            [{"type": "b64", "value": base64.b64encode(small_structural_mp4()).decode("ascii"), "mime_type": "video/mp4"}],
            {"id": "fake-task"},
        ))
        canvas_before = self.store.open_project(project["id"])["canvas"]

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ):
            manual = self.client.post(endpoint, json={
                **request_payload,
                "routing_mode": "manual",
                "provider_id": "runninghub",
                "model": runninghub_model,
            })

        self.assertEqual(manual.status_code, 400, manual.text)
        self.assertEqual(
            set(manual.json()["detail"]["incompatible_parameters"]),
            {"aspect_ratio", "duration", "resolution", "image_reference_count"},
        )
        self.assertEqual(self.store.open_project(project["id"])["canvas"], canvas_before)
        fake_generate.assert_not_awaited()

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ):
            automatic = self.client.post(endpoint, json=request_payload)
            self.assertEqual(automatic.status_code, 202, automatic.text)
            submitted = automatic.json()
            task = self.wait_for_task(submitted["task_id"])

        self.assertEqual(submitted["route"]["provider_id"], "generic-http")
        self.assertEqual(task["status"], "succeeded", task)

    def test_project_entry_preflights_runninghub_models_that_require_a_first_frame(self):
        project = self.store.create_project("RunningHub 首帧预检")
        self.add_result_node(project)
        capability = {
            "arbitrary_size": True,
            "arbitrary_aspect_ratio": True,
            "arbitrary_resolution": True,
            "arbitrary_quality": True,
            "min_duration": 1,
            "max_duration": 60,
            "max_image_references": 1,
            "max_video_references": 0,
            "max_audio_references": 0,
        }
        models = {
            "vendor-image-to-video": {
                "endpoint": "vendor/image-to-video",
                "params": [{"fieldKey": "resolution", "options": [{"value": "720p"}]}],
            },
            "schema-required-first-frame": {
                "endpoint": "vendor/text-to-video",
                "params": [
                    {"fieldKey": "resolution", "options": [{"value": "720p"}]},
                    {"fieldKey": "firstFrameUrl", "required": True},
                ],
            },
        }
        providers = [
            {
                "id": "runninghub",
                "name": "RunningHub",
                "enabled": True,
                "protocol": "runninghub",
                "wallet_api_key": "test-wallet-key",
                "video_models": list(models),
                "video_model_capabilities": {
                    model: {**capability, "adapter_schema": schema}
                    for model, schema in models.items()
                },
            },
            {
                "id": "generic-http",
                "name": "通用 HTTP",
                "enabled": True,
                "protocol": "openai",
                "video_models": ["generic-video"],
                "video_model_capabilities": {"generic-video": capability},
            },
        ]
        request_payload = self.payload(
            size="", width=None, height=None, quality="auto", seed=None, camera_fixed=False,
        )
        endpoint = f"/api/project-library/{project['id']}/video-generation-tasks"
        fake_generate = AsyncMock(return_value=(
            [{"type": "b64", "value": base64.b64encode(small_structural_mp4()).decode("ascii"), "mime_type": "video/mp4"}],
            {"id": "fake-task"},
        ))
        canvas_before = self.store.open_project(project["id"])["canvas"]

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ):
            for model in models:
                with self.subTest(routing_mode="manual", model=model):
                    manual = self.client.post(endpoint, json={
                        **request_payload,
                        "routing_mode": "manual",
                        "provider_id": "runninghub",
                        "model": model,
                    })
                    self.assertEqual(manual.status_code, 400, manual.text)
                    self.assertEqual(
                        manual.json()["detail"]["incompatible_parameters"],
                        ["image_reference_count"],
                    )

            self.assertEqual(self.store.open_project(project["id"])["canvas"], canvas_before)
            with main.CANVAS_TASK_LOCK:
                self.assertFalse(any(
                    task.get("project_id") == project["id"] for task in main.CANVAS_TASKS.values()
                ))
            fake_generate.assert_not_awaited()

            automatic = self.client.post(endpoint, json=request_payload)
            self.assertEqual(automatic.status_code, 202, automatic.text)
            submitted = automatic.json()
            task = self.wait_for_task(submitted["task_id"])

        self.assertEqual((submitted["route"]["provider_id"], submitted["route"]["model"]), ("generic-http", "generic-video"))
        self.assertEqual(task["status"], "succeeded", task)
        with main.CANVAS_TASK_LOCK:
            project_tasks = [task for task in main.CANVAS_TASKS.values() if task.get("project_id") == project["id"]]
        self.assertEqual(len(project_tasks), 1)
        fake_generate.assert_awaited_once()

    def test_project_entry_isolates_a_later_runninghub_candidate_with_no_wallet_key(self):
        project = self.store.create_project("RunningHub 候选隔离")
        self.add_result_node(project)
        capability = {
            "arbitrary_size": True, "arbitrary_aspect_ratio": True,
            "arbitrary_resolution": True, "arbitrary_quality": True,
            "min_duration": 1, "max_duration": 60,
            "max_image_references": 0, "max_video_references": 0, "max_audio_references": 0,
        }
        providers = [
            {
                "id": "generic-http", "name": "通用 HTTP", "enabled": True, "protocol": "openai",
                "base_url": "https://video.example.invalid", "video_models": ["generic-video"],
                "video_model_capabilities": {"generic-video": capability},
            },
            {
                "id": "runninghub", "name": "RunningHub", "enabled": True, "protocol": "runninghub",
                "video_models": ["remote-video"],
                "video_model_capabilities": {"remote-video": capability},
            },
        ]
        endpoint = f"/api/project-library/{project['id']}/video-generation-tasks"
        fake_generate = AsyncMock(return_value=(
            [{"type": "b64", "value": base64.b64encode(small_structural_mp4()).decode("ascii"), "mime_type": "video/mp4"}],
            {"id": "fake-task"},
        ))
        canvas_before = self.store.open_project(project["id"])["canvas"]
        request_payload = self.payload(
            size="", width=None, height=None, quality="auto", seed=None, camera_fixed=False,
        )

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ), patch.object(main, "runninghub_wallet_key_value", return_value="") as wallet_key, patch.object(
            main, "fetch_runninghub_model_registry", new_callable=AsyncMock
        ) as fetch_registry:
            manual = self.client.post(endpoint, json={
                **request_payload,
                "routing_mode": "manual",
                "provider_id": "runninghub",
                "model": "remote-video",
            })
            self.assertEqual(manual.status_code, 400, manual.text)
            self.assertIn("api_key", manual.json()["detail"]["incompatible_parameters"])
            self.assertEqual(self.store.open_project(project["id"])["canvas"], canvas_before)

            automatic = self.client.post(endpoint, json=request_payload)
            self.assertEqual(automatic.status_code, 202, automatic.text)
            submitted = automatic.json()
            task = self.wait_for_task(submitted["task_id"])

        fetch_registry.assert_not_awaited()
        wallet_key.assert_called_once_with()
        self.assertEqual(submitted["route"]["provider_id"], "generic-http")
        self.assertEqual(task["status"], "succeeded", task)
        fake_generate.assert_awaited_once()

    def test_project_entry_fetches_one_runninghub_registry_for_multiple_models(self):
        project = self.store.create_project("RunningHub registry 缓存")
        self.add_result_node(project)
        capability = {
            "arbitrary_size": True, "arbitrary_aspect_ratio": True,
            "arbitrary_resolution": True, "arbitrary_quality": True,
            "min_duration": 1, "max_duration": 60,
            "max_image_references": 1, "max_video_references": 0, "max_audio_references": 0,
        }
        model_names = ["remote-video-a", "remote-video-b"]
        providers = [
            {
                "id": "runninghub", "name": "RunningHub", "enabled": True, "protocol": "runninghub",
                "wallet_api_key": "test-wallet-key", "video_models": model_names,
                "video_model_capabilities": {model: capability for model in model_names},
            },
            {
                "id": "generic-http", "name": "通用 HTTP", "enabled": True, "protocol": "openai",
                "base_url": "https://video.example.invalid", "video_models": ["generic-video"],
                "video_model_capabilities": {"generic-video": capability},
            },
        ]
        registry = [
            {
                "name_en": model,
                "endpoint": f"vendor/{model}",
                "output_type": "video",
                "params": [
                    {"fieldKey": "resolution", "options": [{"value": "720p"}]},
                    {"fieldKey": "firstFrameUrl", "required": True},
                ],
            }
            for model in model_names
        ]
        request_payload = self.payload(
            size="", width=None, height=None, quality="auto", seed=None, camera_fixed=False,
        )
        fake_generate = AsyncMock(return_value=(
            [{"type": "b64", "value": base64.b64encode(small_structural_mp4()).decode("ascii"), "mime_type": "video/mp4"}],
            {"id": "fake-task"},
        ))

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ), patch.object(
            main, "fetch_runninghub_model_registry", new_callable=AsyncMock, return_value=registry
        ) as fetch_registry:
            response = self.client.post(
                f"/api/project-library/{project['id']}/video-generation-tasks", json=request_payload,
            )
            self.assertEqual(response.status_code, 202, response.text)
            submitted = response.json()
            task = self.wait_for_task(submitted["task_id"])

        fetch_registry.assert_awaited_once()
        self.assertEqual(submitted["route"]["provider_id"], "generic-http")
        self.assertEqual(task["status"], "succeeded", task)

    def test_project_entry_turns_runninghub_schema_failures_into_candidate_failures(self):
        project = self.store.create_project("RunningHub schema 失败隔离")
        self.add_result_node(project)
        capability = {
            "arbitrary_size": True, "arbitrary_aspect_ratio": True,
            "arbitrary_resolution": True, "arbitrary_quality": True,
            "min_duration": 1, "max_duration": 60,
            "max_image_references": 0, "max_video_references": 0, "max_audio_references": 0,
        }
        providers = [
            {
                "id": "runninghub", "name": "RunningHub", "enabled": True, "protocol": "runninghub",
                "wallet_api_key": "test-wallet-key", "video_models": ["remote-video"],
                "video_model_capabilities": {"remote-video": capability},
            },
            {
                "id": "generic-http", "name": "通用 HTTP", "enabled": True, "protocol": "openai",
                "base_url": "https://video.example.invalid", "video_models": ["generic-video"],
                "video_model_capabilities": {"generic-video": capability},
            },
        ]
        endpoint = f"/api/project-library/{project['id']}/video-generation-tasks"
        fake_generate = AsyncMock(return_value=(
            [{"type": "b64", "value": base64.b64encode(small_structural_mp4()).decode("ascii"), "mime_type": "video/mp4"}],
            {"id": "fake-task"},
        ))
        canvas_before = self.store.open_project(project["id"])["canvas"]
        request_payload = self.payload(
            size="", width=None, height=None, quality="auto", seed=None, camera_fixed=False,
        )

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ), patch.object(
            main, "fetch_runninghub_model_registry", new_callable=AsyncMock,
            side_effect=main.HTTPException(status_code=502, detail="registry unavailable"),
        ):
            manual = self.client.post(endpoint, json={
                **request_payload,
                "routing_mode": "manual",
                "provider_id": "runninghub",
                "model": "remote-video",
            })
            self.assertEqual(manual.status_code, 400, manual.text)
            self.assertIn("adapter_schema", manual.json()["detail"]["incompatible_parameters"])
            self.assertEqual(self.store.open_project(project["id"])["canvas"], canvas_before)

            automatic = self.client.post(endpoint, json=request_payload)
            self.assertEqual(automatic.status_code, 202, automatic.text)
            submitted = automatic.json()
            task = self.wait_for_task(submitted["task_id"])

        self.assertEqual(submitted["route"]["provider_id"], "generic-http")
        self.assertEqual(task["status"], "succeeded", task)
        fake_generate.assert_awaited_once()

    def test_project_entry_preflights_non_runninghub_base_url_and_api_key(self):
        project = self.store.create_project("HTTP 配置预检")
        self.add_result_node(project)
        capability = {
            "sizes": ["1280x720"], "aspect_ratios": ["16:9"], "resolutions": ["720p"],
            "qualities": ["high"], "min_duration": 5, "max_duration": 5,
            "max_image_references": 0, "max_video_references": 0, "max_audio_references": 0,
        }
        providers = [
            {
                "id": "broken-http", "name": "未配置 HTTP", "enabled": True, "protocol": "openai",
                "base_url": "", "video_models": ["broken-video"],
                "video_model_capabilities": {"broken-video": capability},
            },
            {
                "id": "valid-http", "name": "可执行 HTTP", "enabled": True, "protocol": "openai",
                "base_url": "https://video.example.invalid", "video_models": ["valid-video"],
                "video_model_capabilities": {"valid-video": capability},
            },
        ]
        endpoint = f"/api/project-library/{project['id']}/video-generation-tasks"
        fake_generate = AsyncMock(return_value=(
            [{"type": "b64", "value": base64.b64encode(small_structural_mp4()).decode("ascii"), "mime_type": "video/mp4"}],
            {"id": "fake-task"},
        ))
        canvas_before = self.store.open_project(project["id"])["canvas"]

        def provider_key(provider_id):
            return "" if provider_id == "broken-http" else "test-api-key"

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ), patch.object(main, "AI_BASE_URL", ""), patch.object(
            main, "provider_env_key_value", side_effect=provider_key
        ):
            manual = self.client.post(endpoint, json=self.payload(
                routing_mode="manual", provider_id="broken-http", model="broken-video",
            ))
            self.assertEqual(manual.status_code, 400, manual.text)
            self.assertEqual(
                set(manual.json()["detail"]["incompatible_parameters"]), {"base_url", "api_key"},
            )
            self.assertEqual(self.store.open_project(project["id"])["canvas"], canvas_before)

            automatic = self.client.post(endpoint, json=self.payload())
            self.assertEqual(automatic.status_code, 202, automatic.text)
            submitted = automatic.json()
            task = self.wait_for_task(submitted["task_id"])

        self.assertEqual((submitted["route"]["provider_id"], submitted["route"]["model"]), ("valid-http", "valid-video"))
        self.assertEqual(task["status"], "succeeded", task)
        fake_generate.assert_awaited_once()

    def test_project_entry_preflights_a_manual_model_only_pin_on_a_later_provider(self):
        project = self.store.create_project("仅固定视频模型预检")
        self.add_result_node(project)
        capability = {
            "sizes": ["1280x720"], "aspect_ratios": ["16:9"], "resolutions": ["720p"],
            "qualities": ["high"], "min_duration": 5, "max_duration": 5,
            "max_image_references": 0, "max_video_references": 0, "max_audio_references": 0,
        }
        providers = [
            {
                "id": "earlier-http", "name": "前置可用服务商", "enabled": True, "protocol": "openai",
                "base_url": "https://video.example.invalid", "video_models": ["other-video"],
                "video_model_capabilities": {"other-video": capability},
            },
            {
                "id": "target-http", "name": "缺少 Key 的目标服务商", "enabled": True, "protocol": "openai",
                "base_url": "https://target.example.invalid", "video_models": ["target-video"],
                "video_model_capabilities": {"target-video": capability},
            },
        ]
        endpoint = f"/api/project-library/{project['id']}/video-generation-tasks"
        fake_generate = AsyncMock(return_value=(
            [{"type": "b64", "value": base64.b64encode(small_structural_mp4()).decode("ascii"), "mime_type": "video/mp4"}],
            {"id": "fake-task"},
        ))
        canvas_before = self.store.open_project(project["id"])["canvas"]

        def provider_key(provider_id):
            return "" if provider_id == "target-http" else "test-api-key"

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ), patch.object(main, "provider_env_key_value", side_effect=provider_key):
            response = self.client.post(endpoint, json=self.payload(
                routing_mode="manual", provider_id="", model="target-video",
            ))

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["incompatible_parameters"], ["api_key"])
        self.assertEqual(self.store.open_project(project["id"])["canvas"], canvas_before)
        with main.CANVAS_TASK_LOCK:
            self.assertFalse(any(
                task.get("project_id") == project["id"] for task in main.CANVAS_TASKS.values()
            ))
        fake_generate.assert_not_awaited()

    def test_project_entry_preflights_actual_inline_media_size_per_candidate(self):
        project = self.store.create_project("内联素材预检")
        self.add_result_node(project)
        video = self.store.import_media(project["id"], small_structural_mp4(), "reference.mp4", "video/mp4")
        capability = {
            "arbitrary_size": True, "arbitrary_aspect_ratio": True,
            "arbitrary_resolution": True, "arbitrary_quality": True,
            "min_duration": 1, "max_duration": 60,
            "max_image_references": 0, "max_video_references": 1, "max_audio_references": 0,
        }
        providers = [
            {
                "id": "inline-http", "name": "内联 HTTP", "enabled": True, "protocol": "openai",
                "base_url": "https://video.example.invalid", "video_models": ["inline-video"],
                "video_model_capabilities": {"inline-video": capability},
            },
            {
                "id": "apimart", "name": "APIMart", "enabled": True, "protocol": "apimart",
                "base_url": "https://api.apimart.ai", "video_models": ["doubao-seedance-2.0"],
                "video_model_capabilities": {"doubao-seedance-2.0": capability},
            },
        ]
        request_payload = self.payload(
            size="", width=None, height=None, quality="auto", seed=None, camera_fixed=False,
            video_reference_asset_ids=[video["asset_id"]],
        )
        endpoint = f"/api/project-library/{project['id']}/video-generation-tasks"
        fake_generate = AsyncMock(return_value=(
            [{"type": "b64", "value": base64.b64encode(small_structural_mp4()).decode("ascii"), "mime_type": "video/mp4"}],
            {"id": "fake-task"},
        ))
        canvas_before = self.store.open_project(project["id"])["canvas"]

        with patch.object(main, "load_api_providers", return_value=providers), patch.object(
            main, "generate_project_video", fake_generate
        ), patch.object(main, "PROJECT_VIDEO_REFERENCE_INLINE_MAX_BYTES", 1):
            manual = self.client.post(endpoint, json={
                **request_payload,
                "routing_mode": "manual",
                "provider_id": "inline-http",
                "model": "inline-video",
            })
            self.assertEqual(manual.status_code, 400, manual.text)
            self.assertIn("reference_size", manual.json()["detail"]["incompatible_parameters"])
            self.assertEqual(self.store.open_project(project["id"])["canvas"], canvas_before)

            automatic = self.client.post(endpoint, json=request_payload)
            self.assertEqual(automatic.status_code, 202, automatic.text)
            submitted = automatic.json()
            task = self.wait_for_task(submitted["task_id"])

        self.assertEqual((submitted["route"]["provider_id"], submitted["route"]["model"]), ("apimart", "doubao-seedance-2.0"))
        self.assertEqual(task["status"], "succeeded", task)
        fake_generate.assert_awaited_once()

    def test_text_image_and_multimodal_video_are_project_owned_and_previewable(self):
        project = self.store.create_project("在线视频")
        self.add_result_node(project)
        image = self.store.import_media(project["id"], tiny_png_bytes(), "reference.png", "image/png")
        video = self.store.import_media(project["id"], small_structural_mp4(), "reference.mp4", "video/mp4")
        audio = self.store.import_media(project["id"], b"ID3\x04\x00\x00\x00\x00\x00\x00", "reference.mp3", "audio/mpeg")
        generated = base64.b64encode(small_structural_mp4()).decode("ascii")
        fake_generate = AsyncMock(return_value=([{"type": "b64", "value": generated, "mime_type": "video/mp4"}], {"id": "fake-task"}))

        submitted, task = self.submit(project, self.payload(
            return_last_frame=True,
            image_reference_asset_ids=[image["asset_id"]],
            video_reference_asset_ids=[video["asset_id"]],
            audio_reference_asset_ids=[audio["asset_id"]],
        ), fake_generate)

        self.assertEqual(submitted["route"]["model"], "multimodal-video")
        self.assertEqual(task["status"], "succeeded", task)
        requested, route, references = fake_generate.await_args.args
        self.assertEqual(requested.to_dict()["duration"], 5)
        self.assertEqual(requested.to_dict()["seed"], 42)
        self.assertEqual(requested.to_dict()["aspect_ratio"], "16:9")
        self.assertIs(requested.to_dict()["return_last_frame"], True)
        self.assertEqual(route.adapter, "http")
        self.assertEqual({item["kind"] for item in references}, {"image", "video", "audio"})

        opened = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in opened["nodes"] if item["id"] == "video-result")
        self.assertEqual(node["outputKind"], "video")
        self.assertEqual(node["images"][0]["kind"], "video")
        generated_asset = node["images"][0]["asset_id"]
        record = self.store.media_record(project["id"], generated_asset)
        self.assertEqual((record["kind"], record["mime_type"]), ("video", "video/mp4"))
        self.assertTrue(record["filename"].endswith(".mp4"))
        self.assertEqual(self.client.get(f"/api/project-library/{project['id']}/media/{generated_asset}").content, small_structural_mp4())
        self.assertNotIn("url", node["images"][0])
        history_output = opened["history"][-1]["outputs"][0]
        self.assertEqual(
            {key: value for key, value in history_output.items() if key not in {"output_id", "output_order"}},
            {"asset_id": generated_asset, "kind": "video"},
        )
        self.assertRegex(history_output["output_id"], r"^output-[a-f0-9]{32}$")
        self.assertEqual(history_output["output_id"], node["images"][0]["output_id"])
        self.assertEqual((history_output["output_order"], node["images"][0]["output_order"]), (0, 0))
        self.assertEqual(opened["history"][-1]["params"]["audio_reference_count"], 1)
        self.assertIs(opened["history"][-1]["params"]["return_last_frame"], True)
        self.assertEqual(opened["history"][-1]["createdAt"], opened["history"][-1]["created_at"])
        self.assertGreater(opened["history"][-1]["createdAt"], 0)

        with patch.object(main, "generate_video_preview_image", return_value=Image.new("RGB", (8, 6), "red")):
            preview = self.client.get(f"/api/project-library/{project['id']}/media/{generated_asset}/preview?w=128")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "image/webp")

    def test_video_adapter_preserves_requested_batch_count(self):
        request = main.VideoGenerationRequest(
            prompt="批量视频",
            size="1280x720",
            width=1280,
            height=720,
            aspect_ratio="16:9",
            resolution="720p",
            duration=5,
            count=2,
        )
        route = SimpleNamespace(provider_id="fake-video-http", model="text-video")
        fake_canvas_video = AsyncMock(side_effect=[
            {"videos": ["/output/first.mp4"], "raw": {"id": "first"}},
            {"videos": ["/output/second.mp4"], "raw": {"id": "second"}},
        ])
        with patch.object(main, "canvas_video", fake_canvas_video):
            candidates, raw = asyncio.run(main.generate_project_video(request, route, []))
        self.assertEqual([item["value"] for item in candidates], ["/output/first.mp4", "/output/second.mp4"])
        self.assertEqual(raw, {"results": [{"id": "first"}, {"id": "second"}]})
        self.assertEqual(fake_canvas_video.await_count, 2)

    def test_video_reference_kind_and_cross_project_reference_are_rejected_before_provider_call(self):
        project = self.store.create_project("视频引用校验")
        other = self.store.create_project("其他项目")
        self.add_result_node(project)
        audio = self.store.import_media(project["id"], b"ID3\x04\x00\x00\x00\x00\x00\x00", "audio.mp3", "audio/mpeg")
        foreign = self.store.import_media(other["id"], b"foreign-project-image", "foreign.png", "image/png")
        fake_generate = AsyncMock()
        for payload in (
            self.payload(image_reference_asset_ids=[audio["asset_id"]]),
            self.payload(image_reference_asset_ids=[foreign["asset_id"]]),
        ):
            with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(
                main, "generate_project_video", fake_generate, create=True
            ):
                response = self.client.post(f"/api/project-library/{project['id']}/video-generation-tasks", json=payload)
            self.assertEqual(response.status_code, 400, response.text)
        fake_generate.assert_not_awaited()
        self.assertEqual(self.store.open_project(project["id"])["canvas"]["history"], [])

    def test_project_video_references_use_restricted_adapter_handles_and_keep_project_isolation(self):
        project = self.store.create_project("适配边界")
        other = self.store.create_project("另一个适配边界")
        image = self.store.import_media(project["id"], tiny_png_bytes(), "reference.png", "image/png")
        video = self.store.import_media(project["id"], small_structural_mp4(), "reference.mp4", "video/mp4")
        audio = self.store.import_media(project["id"], b"ID3\x04\x00\x00\x00\x00\x00\x00", "reference.mp3", "audio/mpeg")
        foreign = self.store.import_media(other["id"], b"foreign-project-image", "foreign.png", "image/png")
        payload = main.ProjectVideoGenerationRequest(**self.payload(
            image_reference_asset_ids=[image["asset_id"]],
            video_reference_asset_ids=[video["asset_id"]],
            audio_reference_asset_ids=[audio["asset_id"]],
        ))

        references = main.project_video_all_references(project["id"], payload)
        self.assertEqual({item["kind"] for item in references}, {"image", "video", "audio"})
        for item in references:
            self.assertTrue(item["path"] and Path(item["path"]).is_file())
            self.assertTrue(item["adapter_url"].startswith("project-media://"))
            self.assertNotIn("/api/project-library/", item["adapter_url"])
            with self.assertRaises(main.ProjectStoreError):
                main.project_media_adapter_path(item["adapter_url"], item["kind"])

        legacy_image_url = f"/api/project-library/{project['id']}/media/{image['asset_id']}"
        legacy_video_url = f"/api/project-library/{project['id']}/media/{video['asset_id']}"
        with self.assertRaises(main.ProjectStoreError):
            main.volcengine_media_reference_url(legacy_image_url)
        with self.assertRaises(main.ProjectStoreError):
            main.volcengine_media_reference_url(legacy_video_url)

        foreign_handle = main.project_media_adapter_reference(project["id"], foreign["asset_id"])
        with self.assertRaises(main.ProjectStoreError):
            main.project_media_adapter_path(foreign_handle, "image")
        with self.assertRaises(main.ProjectStoreError):
            main.media_adapter_data_url("/api/project-library/not-a-media-reference", "video")

    def test_internal_project_video_executor_allows_only_own_handles_and_resets_scope(self):
        project = self.store.create_project("任务执行上下文")
        other = self.store.create_project("跨项目句柄")
        own = self.store.import_media(project["id"], tiny_png_bytes(), "own.png", "image/png")
        foreign = self.store.import_media(other["id"], tiny_png_bytes(), "foreign.png", "image/png")
        request = main.VideoGenerationRequest(
            prompt="安全读取", size="1280x720", width=1280, height=720,
            aspect_ratio="16:9", resolution="720p", duration=5,
        )
        route = SimpleNamespace(provider_id="fake-video-http", model="image-video")
        own_references = main.project_video_references(project["id"], [own["asset_id"]], "image")
        foreign_references = main.project_video_references(other["id"], [foreign["asset_id"]], "image")
        fake_generate = AsyncMock(return_value=([{"type": "url", "value": "/output/result.mp4"}], {}))

        with patch.object(main, "generate_project_video", fake_generate):
            candidates, _ = asyncio.run(main.execute_project_video_generation(
                project["id"], request, route, own_references,
            ))
        self.assertEqual(candidates[0]["value"], "/output/result.mp4")
        fake_generate.assert_awaited_once_with(request, route, own_references, on_provider_task=None)

        fake_generate.reset_mock()
        with patch.object(main, "generate_project_video", fake_generate), self.assertRaises(main.ProjectStoreError):
            asyncio.run(main.execute_project_video_generation(
                project["id"], request, route, foreign_references,
            ))
        fake_generate.assert_not_awaited()
        with self.assertRaises(main.ProjectStoreError):
            main.project_media_adapter_path(own_references[0]["adapter_url"], "image")

    def test_public_canvas_video_rejects_project_media_handle_before_adapter(self):
        project = self.store.create_project("公开接口隔离")
        image = self.store.import_media(project["id"], tiny_png_bytes(), "reference.png", "image/png")
        forged_handle = main.project_media_adapter_reference(project["id"], image["asset_id"])
        provider = {"id": "jimeng", "name": "即梦", "protocol": "jimeng"}
        adapter = AsyncMock(return_value={"videos": ["/output/should-not-run.mp4"]})

        with patch.object(main, "get_api_provider", return_value=provider), patch.object(
            main, "generate_jimeng_video", adapter
        ):
            response = self.client.post("/api/canvas-video", json={
                "prompt": "不应读取项目媒体",
                "provider_id": "jimeng",
                "model": "seedance2.0",
                "project_id": project["id"],
                "images": [{"url": forged_handle, "kind": "image"}],
            })

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["code"], "project_media_handle_requires_project_task")
        adapter.assert_not_awaited()

        with patch.object(main, "get_api_provider", return_value=provider), patch.object(
            main, "generate_jimeng_video", adapter
        ):
            ordinary = self.client.post("/api/canvas-video", json={
                "prompt": "普通本地路径仍走既有适配器",
                "provider_id": "jimeng",
                "model": "seedance2.0",
                "images": [{"url": "/output/local-reference.png", "kind": "image"}],
            })
        self.assertEqual(ordinary.status_code, 200, ordinary.text)
        adapter.assert_awaited_once()

    def test_project_references_reach_a_real_http_video_adapter_as_encoded_media(self):
        project = self.store.create_project("HTTP 适配")
        images = [self.store.import_media(project["id"], tiny_png_bytes(), f"reference-{index}.png", "image/png") for index in range(5)]
        videos = [self.store.import_media(project["id"], small_structural_mp4() + bytes([index]), f"reference-{index}.mp4", "video/mp4") for index in range(4)]
        audios = [self.store.import_media(project["id"], b"ID3\x04\x00\x00\x00\x00\x00" + bytes([index]), f"reference-{index}.mp3", "audio/mpeg") for index in range(4)]
        payload = main.ProjectVideoGenerationRequest(**self.payload(
            size="1280x720",
            width=1280,
            height=720,
            aspect_ratio="16:9",
            resolution="1080p",
            duration=7,
            quality="high",
            return_last_frame=True,
            image_reference_asset_ids=[item["asset_id"] for item in images],
            video_reference_asset_ids=[item["asset_id"] for item in videos],
            audio_reference_asset_ids=[item["asset_id"] for item in audios],
        ))
        references = main.project_video_all_references(project["id"], payload)
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                captured.append(json.loads(self.rfile.read(length).decode("utf-8")))
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                body = json.dumps({"videos": [f"{base}/generated.mp4"]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                body = small_structural_mp4()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        provider = {
            "id": "real-http-video",
            "name": "真实 HTTP 测试视频服务",
            "enabled": True,
            "protocol": "openai",
            "base_url": f"http://127.0.0.1:{server.server_address[1]}",
            "video_models": ["video-model"],
        }
        route = SimpleNamespace(provider_id=provider["id"], model="video-model")
        request = main.project_video_request(payload)
        try:
            with patch.object(main, "get_api_provider", return_value=provider), patch.object(
                main, "provider_env_key_value", return_value="test-key"
            ):
                asyncio.run(main.execute_project_video_generation(project["id"], request, route, references))
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

        self.assertEqual(len(captured), 1)
        body = captured[0]
        self.assertEqual(
            {key: body[key] for key in ("width", "height", "size", "aspect_ratio", "resolution", "duration", "quality", "media_type")},
            {
                "width": 1280, "height": 720, "size": "1280x720", "aspect_ratio": "16:9",
                "resolution": "1080p", "duration": 7, "quality": "high", "media_type": "video",
            },
        )
        self.assertEqual(len(body["images"]), 5)
        self.assertEqual(len(body["videos"]), 4)
        self.assertEqual(len(body["audios"]), 4)
        self.assertIs(body["return_last_frame"], True)
        self.assertTrue(all(value.startswith("data:image/") for value in body["images"]))
        self.assertTrue(all(value.startswith("data:video/") for value in body["videos"]))
        self.assertTrue(all(value.startswith("data:audio/") for value in body["audios"]))
        self.assertFalse(any("/api/project-library/" in value for value in body["videos"] + body["audios"]))

    def test_queued_project_video_uses_validated_provider_snapshot_after_config_drift(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.server.captured.append(json.loads(self.rfile.read(length).decode("utf-8")))
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                body = json.dumps({"videos": [f"{base}/generated.mp4"]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                body = small_structural_mp4()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        original_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        changed_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        original_server.captured = []
        changed_server.captured = []
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (original_server, changed_server)
        ]
        for thread in threads:
            thread.start()
        capability = {
            "sizes": ["1280x720"], "aspect_ratios": ["16:9"], "resolutions": ["720p"],
            "qualities": ["high"], "min_duration": 5, "max_duration": 5,
            "max_image_references": 0, "max_video_references": 0, "max_audio_references": 0,
        }
        original = {
            "id": "queued-provider", "name": "排队时服务商", "enabled": True, "protocol": "openai",
            "base_url": f"http://127.0.0.1:{original_server.server_address[1]}",
            "video_models": ["queued-video"],
            "video_model_capabilities": {"queued-video": capability},
        }
        changed_configs = [
            {
                "id": "fallback-provider", "name": "删除后的回退服务商", "enabled": True, "protocol": "openai",
                "base_url": f"http://127.0.0.1:{changed_server.server_address[1]}",
                "video_models": ["queued-video"],
                "video_model_capabilities": {"queued-video": capability},
            },
            {
                **original,
                "name": "同 ID 已修改服务商",
                "base_url": f"http://127.0.0.1:{changed_server.server_address[1]}",
            },
        ]
        try:
            for index, changed in enumerate(changed_configs):
                with self.subTest(config_drift=index):
                    original_server.captured.clear()
                    changed_server.captured.clear()
                    project = self.store.create_project(f"配置漂移 {index}")
                    self.add_result_node(project)
                    calls = 0

                    def providers_after_queue():
                        nonlocal calls
                        calls += 1
                        return [original] if calls == 1 else [changed]

                    with patch.object(main, "load_api_providers", side_effect=providers_after_queue), patch.object(
                        main, "provider_env_key_value", return_value="test-key"
                    ):
                        response = self.client.post(
                            f"/api/project-library/{project['id']}/video-generation-tasks",
                            json=self.payload(routing_mode="manual", provider_id=original["id"], model="queued-video"),
                        )
                        self.assertEqual(response.status_code, 202, response.text)
                        task = self.wait_for_task(response.json()["task_id"])

                    self.assertEqual(task["status"], "succeeded", task)
                    self.assertEqual(len(original_server.captured), 1)
                    self.assertEqual(changed_server.captured, [])
                    self.assertEqual(task["result"]["history_record"]["actual_provider_id"], original["id"])
        finally:
            for server in (original_server, changed_server):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)

    def test_specialized_video_adapter_rejects_parameters_it_would_drop_or_clamp(self):
        provider = {
            "id": "apimart",
            "name": "APIMart",
            "protocol": "apimart",
            "base_url": "http://127.0.0.1:1",
        }
        payload = main.CanvasVideoRequest(
            prompt="需要严格透传",
            provider_id="apimart",
            model="doubao-seedance-2.0",
            duration=20,
            width=1280,
            height=720,
            size="1280x720",
            aspect_ratio="16:9",
            resolution="720p",
            quality="high",
            media_type="video",
            videos=["video-1", "video-2", "video-3", "video-4"],
            audios=["audio-1", "audio-2", "audio-3", "audio-4"],
        )
        incompatible = main.video_adapter_incompatible_parameters(payload, provider)
        self.assertIn("duration", incompatible)
        self.assertIn("quality", incompatible)
        self.assertIn("video_reference_count", incompatible)
        self.assertIn("audio_reference_count", incompatible)

    def test_apimart_seedance_transmits_return_last_frame(self):
        provider = {
            "id": "apimart",
            "name": "APIMart",
            "protocol": "apimart",
            "base_url": "https://api.apimart.ai",
        }
        payload = main.CanvasVideoRequest(
            prompt="返回尾帧",
            provider_id="apimart",
            model="doubao-seedance-2.0",
            return_last_frame=True,
        )
        captured = []

        async def respond(request):
            captured.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"videos": ["https://cdn.example.invalid/result.mp4"]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(main, "provider_env_key_value", return_value="test-key"), patch.object(
            main.httpx, "AsyncClient", return_value=client
        ), patch.object(
            main, "save_remote_video_to_output", AsyncMock(return_value="/output/result.mp4")
        ):
            result = asyncio.run(main.execute_canvas_video(payload, provider))

        self.assertEqual(result["videos"], ["/output/result.mp4"])
        self.assertEqual(len(captured), 1)
        self.assertIs(captured[0]["return_last_frame"], True)

    def test_specialized_video_adapter_nondefault_parameter_matrix(self):
        image_refs = [
            main.AIReference(url=f"https://example.invalid/image-{index}.png", kind="image")
            for index in range(2)
        ]
        common = {
            "prompt": "严格参数矩阵",
            "duration": 7,
            "width": 1280,
            "height": 720,
            "size": "1280x720",
            "aspect_ratio": "9:16",
            "resolution": "1080p",
            "quality": "high",
            "media_type": "video",
            "images": image_refs,
            "videos": ["https://example.invalid/reference.mp4"],
            "audios": ["https://example.invalid/reference.mp3"],
            "seed": 37,
            "enhance_prompt": True,
            "enable_upsample": True,
            "watermark": True,
            "camera_fixed": True,
            "return_last_frame": True,
            "generate_audio": True,
            "multimodal": True,
        }
        cases = [
            (
                "jimeng", {"id": "jimeng", "protocol": "jimeng"}, "seedance2.0_vip",
                {"width", "height", "size", "quality", "seed", "enhance_prompt", "enable_upsample", "watermark", "camera_fixed", "return_last_frame", "generate_audio"},
            ),
            (
                "runninghub", {"id": "runninghub", "protocol": "runninghub"}, "video-app",
                {"width", "height", "size", "quality", "video_reference_count", "audio_reference_count", "seed", "enhance_prompt", "enable_upsample", "camera_fixed", "return_last_frame"},
            ),
            (
                "apimart-seedance", {"id": "apimart", "protocol": "apimart", "base_url": "https://api.apimart.ai"}, "doubao-seedance-2.0",
                {"width", "height", "size", "quality", "enhance_prompt", "enable_upsample", "watermark", "camera_fixed"},
            ),
            (
                "apimart-veo", {"id": "apimart", "protocol": "apimart", "base_url": "https://api.apimart.ai"}, "veo3.1-fast",
                {"width", "height", "size", "quality", "video_reference_count", "audio_reference_count", "seed", "enhance_prompt", "enable_upsample", "watermark", "camera_fixed", "return_last_frame", "generate_audio"},
            ),
            (
                "volcengine-direct", {"id": "volcengine", "protocol": "volcengine", "base_url": "https://ark.example.invalid"}, "doubao-seedance-2-0-fast-260128",
                {"width", "height", "size", "quality", "video_reference_count", "enhance_prompt", "enable_upsample", "return_last_frame"},
            ),
            (
                "yuli-openai", {"id": "yuli", "protocol": "openai", "base_url": "https://api.yuli.host"}, "veo3.1-fast",
                {"width", "height", "size", "resolution", "quality", "image_reference_count", "video_reference_count", "audio_reference_count", "seed", "enhance_prompt", "enable_upsample", "camera_fixed", "return_last_frame", "generate_audio"},
            ),
            (
                "yuli-native", {"id": "yuli", "protocol": "openai", "base_url": "https://api.yuli.host"}, "doubao-video",
                {"width", "height", "size", "resolution", "duration", "quality", "video_reference_count", "audio_reference_count", "seed", "watermark", "camera_fixed", "return_last_frame", "generate_audio"},
            ),
            (
                "lingjing", {"id": "lingjing", "protocol": "openai", "base_url": "https://api.apistudio.vip"}, "veo3.1-fast",
                {"width", "height", "size", "resolution", "quality", "video_reference_count", "audio_reference_count", "seed", "enhance_prompt", "enable_upsample", "camera_fixed", "return_last_frame", "generate_audio"},
            ),
            (
                "agnes", {"id": "agnes", "protocol": "openai", "base_url": "https://apihub.agnes-ai.com"}, "agnes-video-v2.0",
                {"size", "aspect_ratio", "resolution", "duration", "quality", "video_reference_count", "audio_reference_count", "enhance_prompt", "enable_upsample", "watermark", "camera_fixed", "return_last_frame", "generate_audio"},
            ),
        ]
        for name, provider, model, expected in cases:
            with self.subTest(adapter=name):
                payload = main.CanvasVideoRequest(provider_id=provider["id"], model=model, **common)
                self.assertEqual(set(main.video_adapter_incompatible_parameters(payload, provider)), expected)

        non_video = main.CanvasVideoRequest(prompt="错误媒体类型", media_type="image")
        for name, provider, model, _ in cases:
            with self.subTest(adapter=name, parameter="media_type"):
                non_video.provider_id = provider["id"]
                non_video.model = model
                self.assertIn("media_type", main.video_adapter_incompatible_parameters(non_video, provider))

    def test_runninghub_schema_matrix_rejects_any_nondefault_value_not_represented_upstream(self):
        payload = main.CanvasVideoRequest(
            prompt="RunningHub schema",
            model="video-app",
            duration=7,
            aspect_ratio="9:16",
            resolution="1080p",
            images=[
                main.AIReference(url="https://example.invalid/one.png", kind="image"),
                main.AIReference(url="https://example.invalid/two.png", kind="image"),
            ],
            watermark=True,
            generate_audio=True,
        )
        supported_schema = [
            {"fieldKey": "aspectRatio"},
            {"fieldKey": "duration"},
            {"fieldKey": "resolution"},
            {"fieldKey": "imageUrls", "multipleInputs": True},
            {"fieldKey": "watermark"},
            {"fieldKey": "generateAudio"},
        ]
        self.assertEqual(
            main.runninghub_video_incompatible_parameters(payload, supported_schema, image_to_video=False),
            [],
        )
        self.assertEqual(
            set(main.runninghub_video_incompatible_parameters(payload, [], image_to_video=False)),
            {"aspect_ratio", "resolution", "duration", "image_reference_count", "watermark", "generate_audio"},
        )
        single_image_schema = [{"fieldKey": "imageUrl", "multipleInputs": False}]
        self.assertIn(
            "image_reference_count",
            main.runninghub_video_incompatible_parameters(payload, single_image_schema, image_to_video=True),
        )
        no_image_payload = main.CanvasVideoRequest(
            prompt="RunningHub zero-image schema", model="video-app", resolution="",
        )
        self.assertEqual(
            main.runninghub_video_incompatible_parameters(no_image_payload, [], image_to_video=True),
            ["image_reference_count"],
        )
        self.assertEqual(
            main.runninghub_video_incompatible_parameters(
                no_image_payload,
                [{"fieldKey": "firstFrameUrl", "required": True}],
                image_to_video=False,
            ),
            ["image_reference_count"],
        )
        self.assertEqual(
            main.runninghub_video_incompatible_parameters(
                no_image_payload,
                [{"fieldKey": "firstFrameUrl", "required": False}],
                image_to_video=False,
            ),
            [],
        )

    def test_specialized_adapters_reject_reference_shapes_they_would_partially_drop(self):
        ref = lambda index, role="": main.AIReference(
            url=f"https://example.invalid/{index}.png", kind="image", role=role,
        )
        cases = (
            (
                {"id": "jimeng", "protocol": "jimeng"},
                main.CanvasVideoRequest(
                    prompt="即梦首尾帧不能吞第三张", model="seedance2.0",
                    images=[ref(1, "first_frame"), ref(2, "last_frame"), ref(3)],
                ),
                "image_reference_count",
            ),
            (
                {"id": "apimart", "protocol": "apimart", "base_url": "https://api.apimart.ai"},
                main.CanvasVideoRequest(
                    prompt="角色图片不能吞普通图片", model="doubao-seedance-2.0",
                    images=[ref(1, "first_frame"), ref(2)],
                ),
                "image_reference_count",
            ),
            (
                {"id": "apimart", "protocol": "apimart", "base_url": "https://api.apimart.ai"},
                main.CanvasVideoRequest(
                    prompt="首帧与视频不能冲突到晚期", model="doubao-seedance-2.0",
                    images=[ref(1, "first_frame")], videos=["https://example.invalid/reference.mp4"],
                ),
                "video_reference_count",
            ),
            (
                {"id": "volcengine", "protocol": "volcengine", "base_url": "https://ark.example.invalid"},
                main.CanvasVideoRequest(
                    prompt="重复首帧不能被吞", model="doubao-seedance-2-0-fast-260128",
                    images=[ref(1, "first_frame"), ref(2, "first_frame")],
                ),
                "image_reference_count",
            ),
        )
        for provider, payload, parameter in cases:
            with self.subTest(provider=provider["id"], parameter=parameter):
                self.assertIn(parameter, main.video_adapter_incompatible_parameters(payload, provider))

    def test_video_execution_rejects_a_model_protocol_override_without_a_real_adapter(self):
        provider = {
            "id": "mixed-protocol",
            "name": "Mixed Protocol",
            "enabled": True,
            "protocol": "openai",
            "base_url": "https://gateway.example.invalid",
            "model_protocols": {"video-gemini": "gemini"},
            "video_models": ["video-gemini"],
            "video_model_capabilities": {
                "video-gemini": {
                    "sizes": ["1280x720"], "aspect_ratios": ["16:9"], "resolutions": ["720p"],
                    "min_duration": 5, "max_duration": 5,
                    "max_image_references": 0, "max_video_references": 0, "max_audio_references": 0,
                },
            },
        }
        captured = []

        async def respond(request):
            captured.append(request)
            return httpx.Response(200, json={"videos": ["https://cdn.example.invalid/result.mp4"]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        request = main.VideoGenerationRequest(
            prompt="协议覆盖", size="1280x720", width=1280, height=720,
            aspect_ratio="16:9", resolution="720p", duration=5,
        )
        with self.assertRaises(main.ModelRoutingError) as routed:
            main.route_video_generation(request, [provider], provider_id=provider["id"], model="video-gemini")
        self.assertIn("protocol", routed.exception.incompatible_parameters)

        payload = main.CanvasVideoRequest(prompt="协议覆盖", provider_id=provider["id"], model="video-gemini")
        save_output = AsyncMock(return_value="/output/result.mp4")
        with patch.object(main, "get_api_provider", return_value=provider), patch.object(
            main, "provider_env_key_value", return_value="override-key"
        ), patch.object(main.httpx, "AsyncClient", return_value=client), patch.object(
            main, "save_remote_video_to_output", save_output
        ), self.assertRaises(main.HTTPException) as executed:
            asyncio.run(main.canvas_video(payload))

        self.assertEqual(executed.exception.status_code, 400)
        self.assertEqual(executed.exception.detail.get("code"), "unsupported_video_adapter_parameters")
        self.assertIn("不支持请求的参数", executed.exception.detail.get("message", ""))
        self.assertEqual(captured, [])
        save_output.assert_not_awaited()

    def test_failed_video_result_cleans_partial_media_and_a_retry_recovers(self):
        project = self.store.create_project("视频失败恢复")
        self.add_result_node(project)
        good = base64.b64encode(small_structural_mp4()).decode("ascii")
        bad = base64.b64encode(b"<html>not a video</html>").decode("ascii")
        fail_provider = AsyncMock(return_value=([
            {"type": "b64", "value": good, "mime_type": "video/mp4"},
            {"type": "b64", "value": bad, "mime_type": "video/mp4"},
        ], {}))
        submitted, failed = self.submit(project, self.payload(), fail_provider)
        self.assertEqual(failed["status"], "failed", failed)
        opened = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in opened["nodes"] if item["id"] == "video-result")
        self.assertEqual(node["pending"], 0)
        self.assertEqual(opened["history"][-1]["status"], "failed")
        self.assertEqual(opened["history"][-1]["createdAt"], opened["history"][-1]["created_at"])
        self.assertGreater(opened["history"][-1]["createdAt"], 0)
        # #83: 失败任务保留已下载成功的视频（被失败记录引用，重试可取回），不再静默硬删
        partial_items = self.store.list_media(project["id"])
        self.assertEqual(len(partial_items), 1)
        self.assertEqual([output["asset_id"] for output in opened["history"][-1]["outputs"]], [partial_items[0]["asset_id"]])

        recover_provider = AsyncMock(return_value=([{"type": "b64", "value": good, "mime_type": "video/mp4"}], {}))
        retry = self.payload(base_revision=opened["revision"])
        _retry_submitted, recovered = self.submit(project, retry, recover_provider)
        self.assertEqual(recovered["status"], "succeeded", recovered)
        reopened = self.store.open_project(project["id"])["canvas"]
        self.assertEqual([item["status"] for item in reopened["history"]], ["failed", "succeeded"])
        # 重试产生的视频与半成品字节相同（同 sha256 → 同 asset_id），媒体库仍为 1 条；
        # 节点展示重试结果，失败任务保留的半成品由失败记录引用。
        self.assertEqual(len(self.store.list_media(project["id"])), 1)
        self.assertEqual(self.store.list_media(project["id"])[0]["asset_id"], partial_items[0]["asset_id"])

    def test_two_video_tasks_complete_concurrently_without_losing_project_results(self):
        project = self.store.create_project("视频并发")
        self.add_result_node(project, "first")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "second", "type": "smart-image", "x": 420, "y": 80, "images": []}}],
            base_revision=1,
        )
        release = threading.Event()
        encoded = base64.b64encode(small_structural_mp4()).decode("ascii")

        async def delayed_provider(*_args, **_kwargs):
            await asyncio.to_thread(release.wait)
            return ([{"type": "b64", "value": encoded, "mime_type": "video/mp4"}], {})

        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(
            main, "generate_project_video", side_effect=delayed_provider, create=True
        ):
            first = self.client.post(f"/api/project-library/{project['id']}/video-generation-tasks", json=self.payload("first", base_revision=2))
            self.assertEqual(first.status_code, 202, first.text)
            second = self.client.post(f"/api/project-library/{project['id']}/video-generation-tasks", json=self.payload("second", base_revision=3))
            self.assertEqual(second.status_code, 202, second.text)
            release.set()
            first_task = self.wait_for_task(first.json()["task_id"])
            second_task = self.wait_for_task(second.json()["task_id"])
        self.assertEqual((first_task["status"], second_task["status"]), ("succeeded", "succeeded"))
        opened = self.store.open_project(project["id"])["canvas"]
        self.assertTrue(all(next(item for item in opened["nodes"] if item["id"] == node_id)["images"] for node_id in ("first", "second")))
        self.assertEqual(len([log for log in opened["history"] if log["type"] == "online-video"]), 2)

    def test_video_archiver_rejects_non_video_or_oversized_bytes_and_uses_verified_extension(self):
        project = self.store.create_project("视频归档安全")
        with self.assertRaisesRegex(Exception, "有效视频|不支持的视频在线格式"):
            asyncio.run(main.archive_project_generated_video(
                project["id"], {"type": "b64", "value": base64.b64encode(b"<html>bad</html>").decode("ascii"), "mime_type": "video/mp4"}, 0,
            ))
        self.assertEqual(self.store.list_media(project["id"]), [])
        encoded = base64.b64encode(small_structural_mp4()).decode("ascii")
        with patch.object(main, "PROJECT_GENERATED_VIDEO_MAX_BYTES", len(small_structural_mp4()) - 1, create=True):
            with self.assertRaisesRegex(Exception, "超过"):
                asyncio.run(main.archive_project_generated_video(project["id"], {"type": "b64", "value": encoded, "mime_type": "video/mp4"}, 0))
        item = asyncio.run(main.archive_project_generated_video(project["id"], {"type": "b64", "value": encoded, "mime_type": "text/plain"}, 0))
        record = self.store.media_record(project["id"], item["asset_id"])
        self.assertEqual((item["kind"], record["mime_type"]), ("video", "video/mp4"))
        self.assertTrue(record["filename"].endswith(".mp4"))

    def test_browser_frame_import_validates_a_real_png_before_it_becomes_project_media(self):
        project = self.store.create_project("视频抽帧")
        source = Path(self.temp_dir.name) / "frame.png"
        source.write_bytes(tiny_png_bytes())
        with patch.object(main, "project_media_source_path", return_value=str(source)):
            response = self.client.post(f"/api/project-library/{project['id']}/media/import", json={
                "source_url": "/assets/frame.png", "name": "frame.png", "mime_type": "image/png", "expected_kind": "image",
            })
            self.assertEqual(response.status_code, 202, response.text)
            task_id = response.json()["task"]["id"]
            deadline = time.monotonic() + 3
            status = {}
            while time.monotonic() < deadline:
                status = self.client.get(f"/api/project-library/{project['id']}/media/import/{task_id}").json()["task"]
                if status["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
        self.assertEqual(status["status"], "completed", status)
        self.assertEqual(status["media"]["kind"], "image")
        self.assertEqual(self.store.media_record(project["id"], status["media"]["asset_id"])["mime_type"], "image/png")

        invalid = Path(self.temp_dir.name) / "not-a-frame.png"
        invalid.write_bytes(b"not a PNG")
        with patch.object(main, "project_media_source_path", return_value=str(invalid)):
            response = self.client.post(f"/api/project-library/{project['id']}/media/import", json={
                "source_url": "/assets/not-a-frame.png", "name": "not-a-frame.png", "mime_type": "image/png", "expected_kind": "image",
            })
            self.assertEqual(response.status_code, 202, response.text)
            task_id = response.json()["task"]["id"]
            deadline = time.monotonic() + 3
            status = {}
            while time.monotonic() < deadline:
                status = self.client.get(f"/api/project-library/{project['id']}/media/import/{task_id}").json()["task"]
                if status["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
        self.assertEqual(status["status"], "failed", status)
        self.assertEqual(len(self.store.list_media(project["id"])), 1)


if __name__ == "__main__":
    unittest.main()
