import base64
import asyncio
import io
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore


def tiny_png_base64():
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), (38, 190, 133)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class ProjectImageGenerationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.providers = [
            {
                "id": "fake-http",
                "name": "假在线服务",
                "enabled": True,
                "protocol": "openai",
                "image_models": ["fake-square", "fake-wide-4k"],
                "image_model_capabilities": {
                    "fake-square": {"sizes": ["1024x1024"], "aspect_ratios": ["1:1"], "resolutions": ["1k"], "max_reference_images": 0},
                    "fake-wide-4k": {"sizes": ["3840x1648"], "aspect_ratios": ["21:9"], "resolutions": ["4k"], "max_reference_images": 4},
                },
            }
        ]

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        main.PROJECT_IMAGE_GENERATION_ORPHANS.clear()
        self.temp_dir.cleanup()

    def wait_for_task(self, task_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            task = self.client.get(f"/api/canvas-image-tasks/{task_id}").json()
            if task["status"] in {"succeeded", "failed"}:
                return task
            time.sleep(0.02)
        self.fail(f"image task did not finish: {task}")

    def test_provider_configuration_preserves_sanitized_image_capabilities(self):
        saved_payload = main.ApiProviderPayload(**{
            "id": "capable", "name": "能力平台", "base_url": "https://example.test/v1",
            "image_models": ["image-a"],
            "image_model_capabilities": {
                "image-a": {"aspect_ratios": ["1:1", "1:1", "21:9"], "resolutions": ["4k"], "max_reference_images": "8"},
                "not-configured": {"resolutions": ["1k"]},
            },
        }).model_dump()
        normalized = main.normalize_provider(saved_payload)
        self.assertEqual(normalized["image_model_capabilities"], {
            "image-a": {"aspect_ratios": ["1:1", "21:9"], "resolutions": ["4k"], "max_reference_images": 8, "media_types": ["image"]}
        })

    def test_project_generation_routes_without_mutation_and_persists_original_node_and_history(self):
        project = self.store.create_project("统一生图")
        reference = self.store.import_media(project["id"], b"reference", "reference.png", "image/png")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "x": 420, "y": 80, "images": [], "pending": 1}}],
            base_revision=0,
        )
        resolved_references = []

        async def fake_generate_impl(*args, **_kwargs):
            resolved_references.append(main.output_file_from_url(args[4][0]["url"]))
            return (
                {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
                {"data": [{"b64_json": tiny_png_base64()}], "id": "upstream-1"},
            )

        fake_generate = AsyncMock(side_effect=fake_generate_impl)
        request = {
            "prompt": "产品放在木桌上",
            "routing_mode": "auto",
            "size": "3840x1648",
            "width": 3840,
            "height": 1648,
            "aspect_ratio": "21:9",
            "resolution": "4k",
            "quality": "high",
            "n": 1,
            "reference_asset_ids": [reference["asset_id"]],
            "media_type": "image",
            "result_node_id": "result-node",
            "angle_preset": "rear-three-quarter",
            "base_revision": 1,
        }

        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", fake_generate):
            response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=request)
            self.assertEqual(response.status_code, 202, response.text)
            submitted = response.json()
            # Legacy project image tasks expose their task id as the stable
            # idempotency key.  Keep this response contract covered so an
            # Agent-only task variable cannot leak into the legacy endpoint.
            self.assertEqual(submitted["idempotency_key"], submitted["task_id"])
            self.assertEqual(submitted["route"]["model"], "fake-wide-4k")
            self.assertGreaterEqual(submitted["project_revision"], 2)
            self.assertEqual(submitted["registered_node"]["id"], "result-node")
            self.assertNotIn("registered_canvas", submitted)
            task = self.wait_for_task(submitted["task_id"])

        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["result"]["registered_revision"], submitted["project_revision"])
        self.assertEqual(task["result"]["final_node"]["id"], "result-node")
        call = fake_generate.await_args
        self.assertEqual(call.args[1], "3840x1648")
        self.assertEqual(call.args[3], "fake-wide-4k")
        self.assertEqual(call.args[4][0]["asset_id"], reference["asset_id"])
        self.assertEqual(resolved_references, [self.store.media_record(project["id"], reference["asset_id"])["path"]])
        self.assertIsNone(main.PROJECT_MEDIA_ADAPTER_PROJECT.get())
        self.assertEqual(call.kwargs["image_parameters"], {
            "width": 3840, "height": 1648, "aspect_ratio": "21:9", "resolution": "4k", "media_type": "image",
        })
        self.assertTrue(call.kwargs["preserve_model"])
        self.assertIn("后侧四分之三", call.args[0])

        opened = self.store.open_project(project["id"])["canvas"]
        node = next(node for node in opened["nodes"] if node["id"] == "result-node")
        self.assertEqual((node["x"], node["y"]), (420, 80))
        self.assertEqual(len(node["images"]), 1)
        self.assertEqual(set(node["images"][0]), {"asset_id", "kind", "name", "natural_w", "natural_h"})
        generated_asset = node["images"][0]["asset_id"]
        self.assertTrue(Path(self.store.media_record(project["id"], generated_asset)["path"]).is_file())
        self.assertNotEqual(generated_asset, reference["asset_id"])

        history = opened["logs"][-1]
        self.assertEqual(history["prompt"], request["prompt"])
        self.assertEqual(history["executed_prompt"], call.args[0])
        self.assertEqual(history["actual_provider_id"], "fake-http")
        self.assertEqual(history["actual_model"], "fake-wide-4k")
        self.assertEqual(history["params"]["size"], "3840x1648")
        self.assertEqual(history["params"]["width"], 3840)
        self.assertEqual(history["params"]["height"], 1648)
        self.assertEqual(history["params"]["aspect_ratio"], "21:9")
        self.assertEqual(history["params"]["resolution"], "4k")
        self.assertEqual(history["params"]["reference_count"], 1)
        self.assertEqual(history["params"]["media_type"], "image")
        self.assertEqual(history["angle_preset"], "rear-three-quarter")
        self.assertEqual(history["outputs"], [{"asset_id": generated_asset, "kind": "image"}])

    def test_public_online_image_rejects_project_media_and_project_task_rejects_foreign_assets(self):
        project = self.store.create_project("公开生图隔离")
        other = self.store.create_project("其他生图项目")
        own = self.store.import_media(project["id"], b"own", "own.png", "image/png")
        foreign = self.store.import_media(other["id"], b"foreign", "foreign.png", "image/png")
        adapter = AsyncMock()
        forged_values = (
            main.project_media_adapter_reference(project["id"], own["asset_id"]),
            f"/api/project-library/{project['id']}/media/{own['asset_id']}",
        )
        for endpoint in ("/api/online-image", "/api/canvas-image-tasks"):
            for forged in forged_values:
                with self.subTest(endpoint=endpoint, reference=forged), patch.object(main, "generate_ai_image", adapter):
                    response = self.client.post(endpoint, json={
                        "prompt": "不能公开读取项目图片",
                        "reference_images": [{"url": forged, "kind": "image"}],
                    })
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"]["code"], "project_media_handle_requires_project_task")
        adapter.assert_not_awaited()

        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        payload = {
            "prompt": "跨项目图片",
            "routing_mode": "auto",
            "size": "3840x1648",
            "width": 3840,
            "height": 1648,
            "aspect_ratio": "21:9",
            "resolution": "4k",
            "reference_asset_ids": [foreign["asset_id"]],
            "media_type": "image",
            "result_node_id": "result",
            "base_revision": 1,
        }
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(
            main, "generate_ai_image", adapter
        ):
            response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=payload)
        self.assertEqual(response.status_code, 404, response.text)
        adapter.assert_not_awaited()

    def test_manual_pin_and_no_compatible_route_fail_before_paid_call(self):
        project = self.store.create_project("路由错误")
        fake_generate = AsyncMock()
        payload = {
            "prompt": "宽幅画面", "routing_mode": "manual", "provider_id": "fake-http", "model": "fake-square",
            "size": "3840x1648", "width": 3840, "height": 1648, "aspect_ratio": "21:9", "resolution": "4k",
            "reference_asset_ids": [], "media_type": "image", "result_node_id": "new-result",
            "base_revision": 0,
        }
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", fake_generate):
            response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "no_compatible_image_model")
        self.assertEqual(response.json()["detail"]["incompatible_parameters"], ["size", "width", "height", "aspect_ratio", "resolution"])
        fake_generate.assert_not_awaited()
        self.assertEqual(self.store.open_project(project["id"])["canvas"]["nodes"], [])

    def test_invalid_generated_bytes_are_rejected_before_project_import(self):
        project = self.store.create_project("无效图片")
        with self.assertRaisesRegex(Exception, "有效图片"):
            asyncio.run(main.archive_project_generated_image(
                project["id"],
                {"type": "b64", "value": base64.b64encode(b"not-an-image").decode("ascii"), "mime_type": "image/png"},
                0,
            ))
        self.assertEqual(self.store.list_media(project["id"]), [])

    def test_verified_image_format_controls_archived_mime_and_extension(self):
        project = self.store.create_project("格式校验")
        misleading_path = Path(self.temp_dir.name) / "upstream-result.html"
        Image.new("RGB", (5, 4), (8, 16, 24)).save(misleading_path, format="PNG")
        with patch.object(main, "output_file_from_url", return_value=str(misleading_path)):
            item = asyncio.run(main.archive_project_generated_image(
                project["id"], {"type": "url", "value": "/output/upstream-result.html"}, 0,
            ))
        record = self.store.media_record(project["id"], item["asset_id"])
        self.assertEqual(record["mime_type"], "image/png")
        self.assertTrue(record["filename"].endswith(".png"))
        self.assertEqual((item["natural_w"], item["natural_h"]), (5, 4))

    def test_remote_image_target_is_ip_pinned_with_string_sni_and_rejects_private_ips(self):
        public_answer = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch.object(main.socket, "getaddrinfo", return_value=public_answer):
            url, headers, extensions = asyncio.run(main.ensure_public_generation_target("https://example.com/image.png"))
        self.assertEqual(url, "https://93.184.216.34/image.png")
        self.assertEqual(headers["host"], "example.com")
        self.assertEqual(extensions["sni_hostname"], "example.com")
        self.assertIsInstance(extensions["sni_hostname"], str)
        private_answer = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.object(main.socket, "getaddrinfo", return_value=private_answer):
            with self.assertRaisesRegex(Exception, "私有网络"):
                asyncio.run(main.ensure_public_generation_target("https://example.com/image.png"))

    def test_orphan_cleanup_waits_for_last_task_and_preserves_referenced_hashes(self):
        project = self.store.create_project("并发清理")
        orphan = self.store.import_media(project["id"], b"orphan", "orphan.png", "image/png")
        referenced = self.store.import_media(project["id"], b"shared", "shared.png", "image/png")
        self.store.apply_mutations(project["id"], [{
            "op": "upsert_node",
            "node": {"id": "result", "type": "smart-image", "images": [{"asset_id": referenced["asset_id"], "kind": "image"}]},
        }], base_revision=0)
        task_id = "cleanup-running"
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS[task_id] = {
                "id": task_id, "type": "project-online-image", "project_id": project["id"], "status": "running",
            }
            main.PROJECT_IMAGE_GENERATION_ORPHANS[project["id"]] = {orphan["asset_id"], referenced["asset_id"]}
        asyncio.run(main.cleanup_project_generation_orphans(project["id"]))
        self.assertEqual(len(self.store.list_media(project["id"])), 2)
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS[task_id]["status"] = "succeeded"
        asyncio.run(main.cleanup_project_generation_orphans(project["id"]))
        remaining = {item["asset_id"] for item in self.store.list_media(project["id"])}
        self.assertEqual(remaining, {referenced["asset_id"]})
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.pop(task_id, None)

    def test_completion_reports_non_contiguous_revision_after_intervening_edit(self):
        project = self.store.create_project("生成期间编辑")
        self.store.apply_mutations(project["id"], [{
            "op": "upsert_node", "node": {"id": "result", "type": "smart-image", "x": 0, "y": 0},
        }], base_revision=0)
        release = threading.Event()

        async def delayed_generate(*_args, **_kwargs):
            await asyncio.to_thread(release.wait)
            encoded = tiny_png_base64()
            return ({"type": "b64", "value": encoded, "mime_type": "image/png"}, {})

        payload = {
            "prompt": "等待期间移动其他节点", "routing_mode": "auto", "size": "3840x1648",
            "width": 3840, "height": 1648, "aspect_ratio": "21:9", "resolution": "4k",
            "reference_asset_ids": [], "media_type": "image", "result_node_id": "result", "base_revision": 1,
        }
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", side_effect=delayed_generate):
            submitted = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=payload).json()
            registered_revision = submitted["project_revision"]
            self.store.apply_mutations(project["id"], [{
                "op": "upsert_node", "node": {"id": "other", "type": "smart-prompt", "x": 900, "y": 700},
            }], base_revision=registered_revision)
            release.set()
            task = self.wait_for_task(submitted["task_id"])
        self.assertEqual(task["status"], "succeeded")
        self.assertGreater(task["result"]["project_revision"], registered_revision + 1)
        self.assertEqual(task["result"]["registered_revision"], registered_revision)
        opened = self.store.open_project(project["id"])["canvas"]
        self.assertTrue(any(node["id"] == "other" for node in opened["nodes"]))

    def test_failed_task_clears_server_pending_state_and_persists_failure_history(self):
        project = self.store.create_project("失败事务")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result", "type": "smart-image", "x": 7, "y": 9, "pending": 1}}],
            base_revision=0,
        )
        invalid_generate = AsyncMock(return_value=(
            {"type": "b64", "value": base64.b64encode(b"invalid").decode("ascii"), "mime_type": "image/png"},
            {},
        ))
        payload = {
            "prompt": "失败也要记录", "routing_mode": "auto", "size": "3840x1648",
            "width": 3840, "height": 1648, "aspect_ratio": "21:9", "resolution": "4k",
            "reference_asset_ids": [], "media_type": "image", "result_node_id": "result",
            "base_revision": 1,
        }
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", invalid_generate):
            response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=payload)
            self.assertEqual(response.status_code, 202, response.text)
            task = self.wait_for_task(response.json()["task_id"])

        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["result"]["final_node"]["id"], "result")
        self.assertGreater(task["result"]["project_revision"], task["result"]["registered_revision"])
        opened = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in opened["nodes"] if item["id"] == "result")
        self.assertEqual(node["pending"], 0)
        self.assertNotIn("pendingTasks", node)
        self.assertEqual(opened["logs"][-1]["status"], "failed")
        self.assertEqual(opened["logs"][-1]["actual_model"], "fake-wide-4k")
        self.assertEqual(self.store.list_media(project["id"]), [])


if __name__ == "__main__":
    unittest.main()
