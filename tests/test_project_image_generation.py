import base64
import asyncio
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.routers import api_project_library
from firefly_projects import ProjectStore


def tiny_png_base64(color=(38, 190, 133)):
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class ProjectImageGenerationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.store_root, snapshot_delay=0)
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
            if task["status"] in {"succeeded", "failed", "cancelled"}:
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

        observed_progress = []

        async def fake_generate_impl(*args, **_kwargs):
            resolved_references.append(main.output_file_from_url(args[4][0]["url"]))
            progress_callback = _kwargs.get("progress_callback")
            if progress_callback:
                progress_callback("uploading", bytes_done=4, bytes_total=8)
                with main.CANVAS_TASK_LOCK:
                    running = next(
                        task for task in main.CANVAS_TASKS.values()
                        if task.get("project_id") == project["id"]
                    )
                    observed_progress.append(dict(running.get("progress") or {}))
                progress_callback("provider_generating")
                with main.CANVAS_TASK_LOCK:
                    observed_progress.append(dict(running.get("progress") or {}))
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
        call = fake_generate.await_args
        self.assertTrue(callable(call.kwargs.get("progress_callback")))
        self.assertEqual([item.get("phase") for item in observed_progress], ["uploading", "provider_generating"])
        self.assertEqual(observed_progress[0].get("bytes_done"), 4)
        self.assertEqual(observed_progress[0].get("bytes_total"), 8)
        self.assertEqual(task.get("progress", {}).get("phase"), "completed")
        self.assertEqual(task.get("progress", {}).get("phase_index"), 6)
        self.assertEqual(task.get("progress", {}).get("phase_count"), 6)
        self.assertEqual(task["result"]["registered_revision"], submitted["project_revision"])
        self.assertEqual(task["result"]["final_node"]["id"], "result-node")
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
        generated_image = node["images"][0]
        self.assertEqual(
            set(generated_image) - {"output_id", "output_order"},
            {"asset_id", "kind", "name", "natural_w", "natural_h"},
        )
        self.assertRegex(generated_image["output_id"], r"^output-[a-f0-9]{32}$")
        self.assertEqual(generated_image["output_order"], 0)
        generated_asset = generated_image["asset_id"]
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
        self.assertEqual(
            [
                {key: value for key, value in item.items() if key not in {"output_id", "output_order"}}
                for item in history["outputs"]
            ],
            [{"asset_id": generated_asset, "kind": "image"}],
        )
        self.assertEqual(history["outputs"][0]["output_id"], generated_image["output_id"])
        self.assertEqual(history["outputs"][0]["output_order"], 0)
        self.assertEqual(history["createdAt"], history["created_at"])
        self.assertGreater(history["createdAt"], 0)

    def test_generated_original_download_preserves_exact_provider_bytes_and_asset_identity(self):
        project = self.store.create_project("原件字节下载")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        provider_bytes = base64.b64decode(tiny_png_base64())
        provider_base64 = base64.b64encode(provider_bytes).decode("ascii")
        adapter = AsyncMock(return_value=(
            {"type": "b64", "value": provider_base64, "mime_type": "image/png"},
            {"data": [{"b64_json": provider_base64}], "id": "upstream-exact-bytes"},
        ))
        payload = {
            "prompt": "preserve exact bytes",
            "routing_mode": "auto",
            "size": "1024x1024",
            "aspect_ratio": "1:1",
            "resolution": "1k",
            "quality": "auto",
            "n": 1,
            "reference_asset_ids": [],
            "media_type": "image",
            "result_node_id": "result-node",
            "base_revision": 1,
        }

        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter):
            submitted = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json=payload,
            )
            self.assertEqual(submitted.status_code, 202, submitted.text)
            task = self.wait_for_task(submitted.json()["task_id"])

        item = task["result"]["image_items"][0]
        asset_id = item["asset_id"]
        outputs = task["result"]["history_record"]["outputs"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["asset_id"], asset_id)
        self.assertEqual(outputs[0]["kind"], "image")
        # T4 修复：Store 分配的 canonical 生成输出身份（output_id/output_order）必须回写给调用方，
        # 否则前端日志输出无 output_id、在画布历史里无法删除。
        self.assertTrue(outputs[0].get("output_id"), "T4: outputs must carry canonical output_id")
        self.assertIsNotNone(outputs[0].get("output_order"), "T4: outputs must carry output_order")
        media = self.client.get(f"/api/project-library/{project['id']}/media/{asset_id}")
        self.assertEqual(media.status_code, 200, media.text)
        self.assertEqual(media.content, provider_bytes)
        downloaded = self.client.get("/api/download-output", params={"url": item["url"], "name": "original.png"})
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.content, provider_bytes)
        self.assertIn("original.png", downloaded.headers.get("content-disposition", ""))

    def test_running_task_exposes_current_stage_for_refresh_recovery(self):
        project = self.store.create_project("刷新恢复生成阶段")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "x": 80, "y": 60, "images": [], "pending": 1}}],
            base_revision=0,
        )
        reached_provider = threading.Event()
        release_provider = threading.Event()

        async def fake_generate_impl(*_args, **kwargs):
            progress_callback = kwargs["progress_callback"]
            progress_callback("provider_generating")
            reached_provider.set()
            await asyncio.to_thread(release_provider.wait, 2)
            return (
                {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
                {"data": [{"b64_json": tiny_png_base64()}], "id": "upstream-refresh"},
            )

        request = {
            "prompt": "刷新后继续显示阶段",
            "routing_mode": "auto",
            "size": "1024x1024",
            "width": 1024,
            "height": 1024,
            "aspect_ratio": "1:1",
            "resolution": "1k",
            "quality": "auto",
            "n": 1,
            "reference_asset_ids": [],
            "media_type": "image",
            "result_node_id": "result-node",
            "base_revision": 1,
        }

        fake_generate = AsyncMock(side_effect=fake_generate_impl)
        try:
            with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", fake_generate):
                response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=request)
                self.assertEqual(response.status_code, 202, response.text)
                task_id = response.json()["task_id"]
                self.assertTrue(reached_provider.wait(1), "generation never reached the provider stage")
                running = self.client.get(f"/api/canvas-image-tasks/{task_id}").json()
                self.assertEqual(running["status"], "running")
                self.assertEqual(running["progress"]["phase"], "provider_generating")
                self.assertEqual(running["progress"]["phase_index"], 3)
                self.assertEqual(running["progress"]["phase_count"], 6)
                self.assertGreaterEqual(running["progress"]["elapsed_ms"], 0)
                release_provider.set()
                completed = self.wait_for_task(task_id)
        finally:
            release_provider.set()

        self.assertEqual(completed["status"], "succeeded", completed)
        self.assertEqual(completed["progress"]["phase"], "completed")

    def test_cancelling_one_overlapping_project_task_leaves_the_other_running(self):
        project = self.store.create_project("并发取消隔离")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        reached = {"first": threading.Event(), "second": threading.Event()}
        released = {"first": threading.Event(), "second": threading.Event()}

        async def blocking_generate(prompt, *_args, **_kwargs):
            key = "first" if prompt == "first" else "second"
            reached[key].set()
            await asyncio.to_thread(released[key].wait, 2)
            return (
                {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
                {"data": [{"b64_json": tiny_png_base64()}], "id": f"upstream-{key}"},
            )

        def payload(prompt, revision):
            return {
                "prompt": prompt,
                "routing_mode": "auto",
                "size": "1024x1024",
                "aspect_ratio": "1:1",
                "resolution": "1k",
                "quality": "auto",
                "n": 1,
                "reference_asset_ids": [],
                "media_type": "image",
                "result_node_id": "result-node",
                "base_revision": revision,
            }

        adapter = AsyncMock(side_effect=blocking_generate)
        try:
            with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter):
                first = self.client.post(
                    f"/api/project-library/{project['id']}/image-generation-tasks",
                    json=payload("first", 1),
                )
                self.assertEqual(first.status_code, 202, first.text)
                second = self.client.post(
                    f"/api/project-library/{project['id']}/image-generation-tasks",
                    json=payload("second", first.json()["project_revision"]),
                )
                self.assertEqual(second.status_code, 202, second.text)
                first_id = first.json()["task_id"]
                second_id = second.json()["task_id"]
                self.assertNotEqual(first_id, second_id)
                self.assertTrue(reached["first"].wait(1), "first task never reached adapter")
                self.assertTrue(reached["second"].wait(1), "second task never reached adapter")

                cancelled = self.client.post(
                    f"/api/project-library/{project['id']}/image-generation-tasks/{first_id}/cancel"
                )
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                self.assertEqual(cancelled.json()["task_id"], first_id)
                self.assertEqual(cancelled.json()["status"], "cancelled")
                self.assertEqual(self.client.get(f"/api/canvas-image-tasks/{first_id}").json()["status"], "cancelled")
                self.assertEqual(self.client.get(f"/api/canvas-image-tasks/{second_id}").json()["status"], "running")

                node = next(
                    item for item in self.store.open_project(project["id"])["canvas"]["nodes"]
                    if item["id"] == "result-node"
                )
                self.assertEqual([item["taskId"] for item in node["pendingTasks"]], [second_id])

                released["second"].set()
                completed = self.wait_for_task(second_id)
                self.assertEqual(completed["status"], "succeeded", completed)
                self.assertEqual(adapter.await_count, 2)
        finally:
            released["first"].set()
            released["second"].set()

        first_terminal = self.wait_for_task(first_id)
        self.assertEqual(first_terminal["status"], "cancelled", first_terminal)
        history_by_task = {
            item.get("task_id"): item
            for item in self.store.open_project(project["id"])["canvas"].get("logs") or []
        }
        self.assertEqual(history_by_task[first_id]["status"], "cancelled")
        self.assertEqual(history_by_task[second_id]["status"], "succeeded")

    def test_cancel_flush_exception_reconciles_to_a_durable_terminal_state(self):
        project = self.store.create_project("取消落盘异常")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        reached = threading.Event()
        released = threading.Event()

        async def blocking_generate(*_args, **_kwargs):
            reached.set()
            await asyncio.to_thread(released.wait, 2)
            return (
                {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
                {"data": [{"b64_json": tiny_png_base64()}]},
            )

        adapter = AsyncMock(side_effect=blocking_generate)
        try:
            with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter):
                submitted = self.client.post(
                    f"/api/project-library/{project['id']}/image-generation-tasks",
                    json={
                        "prompt": "cancel despite flush failure",
                        "routing_mode": "auto",
                        "n": 1,
                        "media_type": "image",
                        "result_node_id": "result-node",
                        "base_revision": 1,
                    },
                )
                self.assertEqual(submitted.status_code, 202, submitted.text)
                task_id = submitted.json()["task_id"]
                self.assertTrue(reached.wait(1))
                original_store_call = main.project_store_call
                failed_flush = False

                def fail_flush_after_commit(method_name, *args, **kwargs):
                    nonlocal failed_flush
                    result = original_store_call(method_name, *args, **kwargs)
                    if method_name == "flush" and not failed_flush:
                        failed_flush = True
                        raise OSError("simulated cancel flush failure")
                    return result

                with patch.object(main, "project_store_call", side_effect=fail_flush_after_commit):
                    cancelled = self.client.post(
                        f"/api/project-library/{project['id']}/image-generation-tasks/{task_id}/cancel"
                    )
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                self.assertEqual(cancelled.json()["status"], "cancelled")
        finally:
            released.set()

        terminal = self.wait_for_task(task_id)
        self.assertEqual(terminal["status"], "cancelled", terminal)
        history = [item for item in self.store.open_project(project["id"])["canvas"]["logs"] if item.get("task_id") == task_id]
        self.assertEqual([item["status"] for item in history], ["cancelled"])

    def test_cancel_broadcast_failure_does_not_hide_the_durable_cancelled_terminal(self):
        project = self.store.create_project("取消广播异常")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        reached = threading.Event()
        released = threading.Event()

        async def blocking_generate(*_args, **_kwargs):
            reached.set()
            await asyncio.to_thread(released.wait, 2)
            return (
                {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
                {"data": [{"b64_json": tiny_png_base64()}]},
            )

        try:
            with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", AsyncMock(side_effect=blocking_generate)):
                submitted = self.client.post(
                    f"/api/project-library/{project['id']}/image-generation-tasks",
                    json={
                        "prompt": "cancel despite websocket failure",
                        "routing_mode": "auto",
                        "n": 1,
                        "media_type": "image",
                        "result_node_id": "result-node",
                        "base_revision": 1,
                    },
                )
                self.assertEqual(submitted.status_code, 202, submitted.text)
                task_id = submitted.json()["task_id"]
                self.assertTrue(reached.wait(1))
                with patch.object(
                    api_project_library.manager,
                    "broadcast_canvas_updated",
                    AsyncMock(side_effect=RuntimeError("websocket unavailable")),
                ):
                    cancelled = self.client.post(
                        f"/api/project-library/{project['id']}/image-generation-tasks/{task_id}/cancel"
                    )
        finally:
            released.set()

        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(self.wait_for_task(task_id)["status"], "cancelled")
        history = [item for item in self.store.open_project(project["id"])["canvas"]["logs"] if item.get("task_id") == task_id]
        self.assertEqual([item["status"] for item in history], ["cancelled"])

    def test_repeating_an_idempotent_submission_reuses_task_without_adapter_recall(self):
        project = self.store.create_project("重复提交不重复计费")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        payload = {
            "prompt": "one paid call",
            "routing_mode": "auto",
            "size": "1024x1024",
            "aspect_ratio": "1:1",
            "resolution": "1k",
            "quality": "auto",
            "n": 1,
            "reference_asset_ids": [],
            "media_type": "image",
            "result_node_id": "result-node",
            "base_revision": 1,
            "idempotency_key": "browser-run-1",
        }
        adapter = AsyncMock(return_value=(
            {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
            {"data": [{"b64_json": tiny_png_base64()}], "id": "upstream-idempotent"},
        ))
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter):
            first = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json=payload,
            )
            self.assertEqual(first.status_code, 202, first.text)
            first_id = first.json()["task_id"]
            self.assertEqual(self.wait_for_task(first_id)["status"], "succeeded")
            with main.CANVAS_TASK_LOCK:
                main.CANVAS_TASKS.pop(first_id, None)
                main.PROJECT_IMAGE_GENERATION_FUTURES.pop(first_id, None)
            repeated = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json=payload,
            )

        self.assertEqual(repeated.status_code, 202, repeated.text)
        self.assertEqual(repeated.json()["task_id"], first_id)
        self.assertEqual(repeated.json()["idempotency_key"], "browser-run-1")
        self.assertEqual(adapter.await_count, 1)

        # A replay resolves the durable identity before validating mutable
        # provider configuration, so disabling a provider cannot induce a
        # second paid call under a fresh key.
        with patch.object(main, "load_api_providers", return_value=[]), patch.object(main, "generate_ai_image", adapter):
            replayed_after_provider_change = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json=payload,
            )
        self.assertEqual(replayed_after_provider_change.status_code, 202, replayed_after_provider_change.text)
        self.assertEqual(replayed_after_provider_change.json()["task_id"], first_id)
        self.assertEqual(adapter.await_count, 1)

    def test_same_idempotency_key_rejects_a_different_generation_request(self):
        project = self.store.create_project("幂等冲突")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "stable-node", "type": "smart-image", "images": [], "pending": 1}}],
            base_revision=0,
        )
        fake_generate = AsyncMock(return_value=(
            {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
            {"data": [{"b64_json": tiny_png_base64()}]},
        ))
        payload = {
            "prompt": "第一次请求",
            "routing_mode": "auto",
            "n": 1,
            "media_type": "image",
            "result_node_id": "stable-node",
            "base_revision": 1,
            "idempotency_key": "one-action-one-payload",
        }
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", fake_generate):
            first = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=payload)
            self.assertEqual(first.status_code, 202, first.text)
            self.assertEqual(self.wait_for_task(first.json()["task_id"])["status"], "succeeded")
            payload.update(prompt="被错误复用的不同请求", base_revision=self.store.open_project(project["id"])["canvas"]["revision"])
            conflict = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=payload)

        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["detail"]["code"], "image_generation_idempotency_conflict")
        self.assertEqual(fake_generate.await_count, 1)

    def test_concurrent_reservations_with_the_same_key_keep_one_task_identity(self):
        project = self.store.create_project("并发幂等预留")
        payload = main.ProjectImageGenerationRequest(
            prompt="同一次点击",
            routing_mode="auto",
            n=1,
            media_type="image",
            result_node_id="same-node",
            base_revision=0,
            idempotency_key="concurrent-action",
        )
        route = SimpleNamespace(provider_id="fake-http", model="fake-square", to_dict=lambda: {"provider_id": "fake-http", "model": "fake-square"})
        task_ids = ["concurrent-task-a", "concurrent-task-b"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda task_id: main.reserve_project_image_task(project["id"], task_id, payload, route), task_ids))

        with main.CANVAS_TASK_LOCK:
            reserved = [
                task for task in main.CANVAS_TASKS.values()
                if task.get("project_id") == project["id"] and task.get("idempotency_key") == "concurrent-action"
            ]
        self.assertEqual(len(reserved), 1)
        self.assertEqual(sum(result is None for result in results), 1)
        self.assertEqual({result["id"] for result in results if result is not None}, {reserved[0]["id"]})
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.pop(reserved[0]["id"], None)

    def test_registration_flush_failure_terminates_the_reserved_task_without_a_ghost_pending(self):
        project = self.store.create_project("注册失败补偿")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        payload = {
            "prompt": "must not invoke provider",
            "routing_mode": "auto",
            "n": 1,
            "media_type": "image",
            "result_node_id": "result-node",
            "base_revision": 1,
            "idempotency_key": "registration-failure",
        }
        adapter = AsyncMock(side_effect=AssertionError("adapter must not run before durable registration"))
        original_store_call = main.project_store_call
        flush_calls = 0

        def fail_first_flush(method_name, *args, **kwargs):
            nonlocal flush_calls
            result = original_store_call(method_name, *args, **kwargs)
            if method_name == "flush":
                flush_calls += 1
                if flush_calls == 1:
                    raise OSError("simulated registration flush failure")
            return result

        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter), patch.object(main, "project_store_call", side_effect=fail_first_flush):
            with self.assertRaisesRegex(OSError, "registration flush failure"):
                self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=payload)

        self.assertEqual(adapter.await_count, 0)
        canvas = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in canvas["nodes"] if item["id"] == "result-node")
        self.assertNotIn("pendingTasks", node)
        records = [item for item in canvas["logs"] if item.get("idempotency_key") == "registration-failure"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(records[0]["termination_reason"], "registration_failed")

    def test_executor_submission_failure_terminates_registration_without_a_ghost_task(self):
        project = self.store.create_project("调度失败补偿")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        payload = {
            "prompt": "must not invoke provider when scheduling fails",
            "routing_mode": "auto",
            "n": 1,
            "media_type": "image",
            "result_node_id": "result-node",
            "base_revision": 1,
            "idempotency_key": "executor-failure",
        }
        adapter = AsyncMock(side_effect=AssertionError("adapter must not run when scheduling fails"))
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter), patch.object(
            api_project_library,
            "ensure_project_image_generation_executor",
            side_effect=RuntimeError("executor unavailable"),
        ):
            failed = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json=payload,
            )

        self.assertEqual(failed.status_code, 503, failed.text)
        task_id = failed.json()["detail"]["task_id"]
        self.assertEqual(failed.json()["detail"]["code"], "image_generation_scheduling_failed")
        self.assertEqual(adapter.await_count, 0)
        canvas = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in canvas["nodes"] if item["id"] == "result-node")
        self.assertNotIn("pendingTasks", node)
        records = [item for item in canvas["logs"] if item.get("task_id") == task_id]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(records[0]["termination_reason"], "scheduling_failed")

        # Replaying the same key returns the same explicit failed identity and
        # cannot turn the uncompensated reservation back into a queued ghost.
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter):
            replayed = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json=payload,
            )
        self.assertEqual(replayed.status_code, 202, replayed.text)
        self.assertEqual(replayed.json()["task_id"], task_id)
        self.assertEqual(replayed.json()["status"], "failed")
        self.assertEqual(adapter.await_count, 0)

    def test_failure_broadcast_exception_keeps_the_durable_failure_result_queryable(self):
        project = self.store.create_project("失败终态广播异常")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        adapter = AsyncMock(side_effect=RuntimeError("provider rejected request"))
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter), patch.object(
            api_project_library.manager,
            "broadcast_canvas_updated",
            AsyncMock(side_effect=RuntimeError("websocket unavailable")),
        ):
            submitted = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json={
                    "prompt": "failure survives broadcast outage",
                    "routing_mode": "auto",
                    "n": 1,
                    "media_type": "image",
                    "result_node_id": "result-node",
                    "base_revision": 1,
                },
            )
            self.assertEqual(submitted.status_code, 202, submitted.text)
            terminal = self.wait_for_task(submitted.json()["task_id"])

        self.assertEqual(terminal["status"], "failed", terminal)
        self.assertTrue(terminal["result"]["history_recorded"])
        self.assertEqual(terminal["result"]["history_record"]["status"], "failed")
        self.assertEqual(terminal["result"]["history_record"]["error"], "provider rejected request")
        self.assertEqual(terminal["result"]["history_record"]["progress"]["phase"], "provider_generating")
        self.assertTrue(terminal["result"]["history_record"]["progress"]["failed"])

    def test_failure_commit_exception_reconciles_the_durable_history_into_memory(self):
        project = self.store.create_project("失败提交结果未知")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        original_commit = self.store._commit_generation_canvas
        commit_count = 0

        def commit_then_raise(project_id, canvas):
            nonlocal commit_count
            committed = original_commit(project_id, canvas)
            commit_count += 1
            if commit_count == 2:
                raise OSError("simulated failure after failed history commit")
            return committed

        adapter = AsyncMock(side_effect=RuntimeError("provider rejected request"))
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter), patch.object(
            self.store,
            "_commit_generation_canvas",
            side_effect=commit_then_raise,
        ):
            submitted = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json={
                    "prompt": "reconcile failed commit",
                    "routing_mode": "auto",
                    "n": 1,
                    "media_type": "image",
                    "result_node_id": "result-node",
                    "base_revision": 1,
                },
            )
            self.assertEqual(submitted.status_code, 202, submitted.text)
            terminal = self.wait_for_task(submitted.json()["task_id"])

        self.assertEqual(terminal["status"], "failed", terminal)
        self.assertTrue(terminal["result"]["history_recorded"])
        self.assertEqual(terminal["result"]["history_record"]["status"], "failed")
        records = [
            item
            for item in self.store.open_project(project["id"])["canvas"]["logs"]
            if item.get("task_id") == submitted.json()["task_id"]
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["error"], "provider rejected request")

    def test_lifespan_shutdown_stops_active_generation_before_store_close(self):
        project = self.store.create_project("真实生命周期关闭")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        started = threading.Event()
        stopped = threading.Event()

        async def blocking_generate(*_args, **_kwargs):
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                stopped.set()

        adapter = AsyncMock(side_effect=blocking_generate)
        task_id = ""
        started_shutdown = 0.0
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter):
            with TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"}) as lifespan_client:
                submitted = lifespan_client.post(
                    f"/api/project-library/{project['id']}/image-generation-tasks",
                    json={
                        "prompt": "shutdown must cancel",
                        "routing_mode": "auto",
                        "n": 1,
                        "media_type": "image",
                        "result_node_id": "result-node",
                        "base_revision": 1,
                    },
                )
                self.assertEqual(submitted.status_code, 202, submitted.text)
                task_id = submitted.json()["task_id"]
                self.assertTrue(started.wait(2))
                started_shutdown = time.monotonic()

        self.assertTrue(stopped.wait(2), "shutdown must cancel and await the active adapter task")
        self.assertLess(time.monotonic() - started_shutdown, 5)
        self.store = ProjectStore(self.store_root, snapshot_delay=0)
        main.FIREFLY_PROJECT_STORE = self.store
        canvas = self.store.open_project(project["id"])["canvas"]
        records = [item for item in canvas["logs"] if item.get("task_id") == task_id]
        self.assertEqual([item["status"] for item in records], ["cancelled"])
        self.assertEqual(records[0]["termination_reason"], "service_shutdown")
        node = next(item for item in canvas["nodes"] if item["id"] == "result-node")
        self.assertNotIn("pendingTasks", node)
        time.sleep(0.1)
        self.assertFalse(any(item.get("status") == "succeeded" for item in self.store.open_project(project["id"])["canvas"]["logs"] if item.get("task_id") == task_id))

    def test_success_commit_exception_reconciles_to_succeeded_without_retry_or_orphan_cleanup(self):
        project = self.store.create_project("成功提交后异常恢复")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        original_store_call = main.project_store_call
        raised_after_success = False

        def raise_after_success_commit(method_name, *args, **kwargs):
            nonlocal raised_after_success
            result = original_store_call(method_name, *args, **kwargs)
            if method_name == "complete_image_generation" and not raised_after_success:
                raised_after_success = True
                raise OSError("simulated exception after success commit")
            return result

        adapter = AsyncMock(return_value=(
            {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
            {"data": [{"b64_json": tiny_png_base64()}]},
        ))
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter), patch.object(
            main,
            "project_store_call",
            side_effect=raise_after_success_commit,
        ):
            submitted = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json={
                    "prompt": "success remains success",
                    "routing_mode": "auto",
                    "n": 1,
                    "media_type": "image",
                    "result_node_id": "result-node",
                    "base_revision": 1,
                },
            )
            self.assertEqual(submitted.status_code, 202, submitted.text)
            terminal = self.wait_for_task(submitted.json()["task_id"])

        self.assertEqual(terminal["status"], "succeeded", terminal)
        self.assertEqual(terminal["error"], "")
        self.assertTrue(terminal["result"]["history_recorded"])
        self.assertEqual(terminal["result"]["history_record"]["status"], "succeeded")
        self.assertEqual(adapter.await_count, 1)
        canvas = self.store.open_project(project["id"])["canvas"]
        records = [item for item in canvas["logs"] if item.get("task_id") == submitted.json()["task_id"]]
        self.assertEqual([item["status"] for item in records], ["succeeded"])
        self.assertEqual(len(self.store.list_media(project["id"])), 1)
        with main.CANVAS_TASK_LOCK:
            self.assertFalse(main.PROJECT_IMAGE_GENERATION_ORPHANS.get(project["id"]))
        retry = self.client.post(
            f"/api/project-library/{project['id']}/image-generation-tasks/{submitted.json()['task_id']}/retry"
        )
        self.assertEqual(retry.status_code, 409, retry.text)

    def test_complete_race_reconciles_to_durable_cancelled_and_cannot_retry(self):
        project = self.store.create_project("取消终态赢得提交竞争")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        original_store_call = main.project_store_call
        cancelled_in_store = False

        def cancel_when_completion_commits(method_name, *args, **kwargs):
            nonlocal cancelled_in_store
            if method_name == "complete_image_generation" and not cancelled_in_store:
                cancelled_in_store = True
                project_id, result_node_id, _items, history_record = args[:4]
                task_id = kwargs["task_id"]
                original_store_call(
                    "cancel_image_generation",
                    project_id,
                    result_node_id,
                    task_id,
                    {
                        "id": f"cancel-{task_id}",
                        "task_id": task_id,
                        "idempotency_key": history_record.get("idempotency_key"),
                        "status": "cancelled",
                        "error": "cancel won the durable CAS",
                        "request_payload": history_record.get("request_payload"),
                    },
                )
                raise OSError("completion lost the durable terminal race")
            return original_store_call(method_name, *args, **kwargs)

        adapter = AsyncMock(return_value=(
            {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
            {"data": [{"b64_json": tiny_png_base64()}]},
        ))
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter), patch.object(
            main,
            "project_store_call",
            side_effect=cancel_when_completion_commits,
        ):
            submitted = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json={
                    "prompt": "cancelled remains cancelled",
                    "routing_mode": "auto",
                    "n": 1,
                    "media_type": "image",
                    "result_node_id": "result-node",
                    "base_revision": 1,
                },
            )
            self.assertEqual(submitted.status_code, 202, submitted.text)
            terminal = self.wait_for_task(submitted.json()["task_id"])

        self.assertEqual(terminal["status"], "cancelled", terminal)
        self.assertEqual(terminal["result"]["history_record"]["status"], "cancelled")
        records = [item for item in self.store.open_project(project["id"])["canvas"]["logs"] if item.get("task_id") == submitted.json()["task_id"]]
        self.assertEqual([item["status"] for item in records], ["cancelled"])
        self.assertEqual(self.store.list_media(project["id"]), [])
        retry = self.client.post(
            f"/api/project-library/{project['id']}/image-generation-tasks/{submitted.json()['task_id']}/retry"
        )
        self.assertEqual(retry.status_code, 409, retry.text)

    def test_post_commit_flush_failure_does_not_rewrite_success_as_failure(self):
        project = self.store.create_project("成功终态不被副作用改写")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        adapter = AsyncMock(return_value=(
            {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
            {"data": [{"b64_json": tiny_png_base64()}]},
        ))
        original_store_call = main.project_store_call
        flush_calls = 0

        def fail_completion_flush(method_name, *args, **kwargs):
            nonlocal flush_calls
            if method_name == "flush":
                flush_calls += 1
                if flush_calls == 2:
                    raise OSError("simulated post-commit flush failure")
            return original_store_call(method_name, *args, **kwargs)

        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter), patch.object(main, "project_store_call", side_effect=fail_completion_flush):
            submitted = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json={
                    "prompt": "paid success",
                    "routing_mode": "auto",
                    "n": 1,
                    "media_type": "image",
                    "result_node_id": "result-node",
                    "base_revision": 1,
                },
            )
            self.assertEqual(submitted.status_code, 202, submitted.text)
            terminal = self.wait_for_task(submitted.json()["task_id"])

        self.assertEqual(terminal["status"], "succeeded", terminal)
        records = [item for item in self.store.open_project(project["id"])["canvas"]["logs"] if item.get("task_id") == submitted.json()["task_id"]]
        self.assertEqual([item["status"] for item in records], ["succeeded"])

    def test_restart_explicitly_terminates_durable_pending_task_without_adapter_recall(self):
        project = self.store.create_project("重启终止未知任务")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        task_id = "project_img_restart_pending"
        request_payload = {
            "prompt": "must not recall provider",
            "routing_mode": "auto",
            "provider_id": "",
            "model": "",
            "size": "1024x1024",
            "width": None,
            "height": None,
            "aspect_ratio": "1:1",
            "resolution": "1k",
            "quality": "auto",
            "n": 1,
            "reference_asset_ids": [],
            "media_type": "image",
            "result_node_id": "result-node",
            "angle_preset": "none",
            "base_revision": 1,
            "idempotency_key": "restart-request-1",
        }
        self.store.begin_image_generation(
            project["id"],
            "result-node",
            {
                "taskId": task_id,
                "kind": "image",
                "count": 1,
                "providerId": "fake-http",
                "model": "fake-square",
                "idempotencyKey": "restart-request-1",
                "requestPayload": request_payload,
                "route": {"provider_id": "fake-http", "provider_name": "假在线服务", "model": "fake-square"},
            },
            base_revision=1,
        )
        self.store.flush(project["id"])
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.pop(task_id, None)
            main.PROJECT_IMAGE_GENERATION_FUTURES.pop(task_id, None)
        # Reconstruct the store from disk as a real process restart would; the
        # recovery path must not depend on the old store's in-memory project.
        self.store.close()
        self.store = ProjectStore(self.store_root, snapshot_delay=0)
        main.FIREFLY_PROJECT_STORE = self.store
        adapter = AsyncMock(side_effect=AssertionError("adapter must not be recalled after restart"))

        task_url = f"/api/project-library/{project['id']}/image-generation-tasks/{task_id}"
        with patch.object(main, "generate_ai_image", adapter):
            with ThreadPoolExecutor(max_workers=2) as executor:
                recovered_responses = list(executor.map(lambda _index: self.client.get(task_url), range(2)))

        for recovered in recovered_responses:
            self.assertEqual(recovered.status_code, 200, recovered.text)
            self.assertEqual(recovered.json()["task_id"], task_id)
            self.assertEqual(recovered.json()["status"], "failed")
            self.assertIn("避免重复计费", recovered.json()["error"])
        self.assertEqual(adapter.await_count, 0)
        canvas = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in canvas["nodes"] if item["id"] == "result-node")
        self.assertNotIn("pendingTasks", node)
        task_history = [item for item in canvas["logs"] if item.get("task_id") == task_id]
        self.assertEqual(len(task_history), 1)
        self.assertEqual(task_history[0]["status"], "failed")
        self.assertEqual(task_history[0]["termination_reason"], "service_restart_unknown")

    def test_terminal_history_repairs_a_stale_pending_canvas_after_commit_interruption(self):
        project = self.store.create_project("终态日志修复 pending")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        task_id = "project_img_interrupted_commit"
        request_payload = {
            "prompt": "paid result survives",
            "routing_mode": "auto",
            "n": 1,
            "media_type": "image",
            "result_node_id": "result-node",
            "base_revision": 1,
            "idempotency_key": "interrupted-commit",
        }
        self.store.begin_image_generation(
            project["id"],
            "result-node",
            {"taskId": task_id, "kind": "image", "count": 1, "idempotencyKey": "interrupted-commit", "requestPayload": request_payload},
            base_revision=1,
        )
        media = self.store.import_media(project["id"], base64.b64decode(tiny_png_base64()), "paid.png", "image/png")
        history = {
            "id": "generation-interrupted",
            "task_id": task_id,
            "idempotency_key": "interrupted-commit",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "paid result survives",
            "request_payload": request_payload,
            "outputs": [{"asset_id": media["asset_id"], "kind": "image"}],
            "result_node_id": "result-node",
        }
        with patch.object(self.store, "_commit_generation_canvas", side_effect=RuntimeError("simulated crash after history persistence")):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                self.store.complete_image_generation(
                    project["id"],
                    "result-node",
                    [{"asset_id": media["asset_id"], "kind": "image"}],
                    history,
                    task_id=task_id,
                    require_pending=True,
                )

        recovered = self.client.get(f"/api/project-library/{project['id']}/image-generation-tasks/{task_id}")
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["status"], "succeeded")
        canvas = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in canvas["nodes"] if item["id"] == "result-node")
        self.assertEqual(
            [
                {key: value for key, value in item.items() if key not in {"output_id", "output_order"}}
                for item in node["images"]
            ],
            [{"asset_id": media["asset_id"], "kind": "image"}],
        )
        self.assertRegex(node["images"][0]["output_id"], r"^output-[a-f0-9]{32}$")
        self.assertEqual(node["images"][0]["output_order"], 0)
        terminal_history = next(item for item in canvas["logs"] if item.get("task_id") == task_id)
        self.assertEqual(terminal_history["outputs"][0]["output_id"], node["images"][0]["output_id"])
        self.assertNotIn("pendingTasks", node)
        self.assertEqual(len([item for item in canvas["logs"] if item.get("task_id") == task_id]), 1)

    def test_partial_completion_stages_images_while_keeping_pending_placeholder_state(self):
        project = self.store.create_project("增量写回占位状态")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        task_id = "project_img_partial_stage"
        request_payload = {"prompt": "partial staging", "routing_mode": "auto", "n": 4, "media_type": "image", "result_node_id": "result-node", "base_revision": 1, "idempotency_key": "partial-stage"}
        self.store.begin_image_generation(
            project["id"],
            "result-node",
            {"taskId": task_id, "kind": "image", "count": 4, "idempotencyKey": "partial-stage", "requestPayload": request_payload},
            base_revision=1,
        )
        first = self.store.import_media(project["id"], base64.b64decode(tiny_png_base64()), "first.png", "image/png")
        second = self.store.import_media(project["id"], base64.b64decode(tiny_png_base64((216, 90, 58))), "second.png", "image/png")

        def progress_record(outputs):
            return {"id": "generation-partial", "task_id": task_id, "idempotency_key": "partial-stage", "type": "online-image", "status": "running", "prompt": "partial staging", "request_payload": request_payload, "outputs": outputs, "result_node_id": "result-node"}

        self.store.complete_image_generation(
            project["id"],
            "result-node",
            [{"asset_id": first["asset_id"], "kind": "image"}],
            progress_record([{"asset_id": first["asset_id"], "kind": "image"}]),
            task_id=task_id,
            partial=True,
        )
        canvas = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in canvas["nodes"] if item["id"] == "result-node")
        self.assertEqual([item["asset_id"] for item in node["images"]], [first["asset_id"]])
        self.assertFalse(node.get("running"))
        self.assertEqual([item.get("taskId") for item in node.get("pendingTasks", [])], [task_id])
        self.assertGreaterEqual(node.get("pending", 0), 3)
        running_logs = [item for item in canvas["logs"] if item.get("id") == "generation-partial"]
        self.assertEqual(len(running_logs), 1)
        self.assertEqual(running_logs[0]["status"], "running")

        self.store.complete_image_generation(
            project["id"],
            "result-node",
            [{"asset_id": second["asset_id"], "kind": "image"}],
            progress_record([{"asset_id": first["asset_id"], "kind": "image"}, {"asset_id": second["asset_id"], "kind": "image"}]),
            task_id=task_id,
            partial=True,
        )
        canvas = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in canvas["nodes"] if item["id"] == "result-node")
        self.assertEqual([item["asset_id"] for item in node["images"]], [first["asset_id"], second["asset_id"]])
        self.assertIn("pendingTasks", node)

        terminal = {"id": "generation-partial", "task_id": task_id, "idempotency_key": "partial-stage", "type": "online-image", "status": "succeeded", "prompt": "partial staging", "request_payload": request_payload, "outputs": [{"asset_id": first["asset_id"], "kind": "image"}, {"asset_id": second["asset_id"], "kind": "image"}], "result_node_id": "result-node"}
        self.store.complete_image_generation(
            project["id"],
            "result-node",
            [{"asset_id": first["asset_id"], "kind": "image"}, {"asset_id": second["asset_id"], "kind": "image"}],
            terminal,
            task_id=task_id,
        )
        canvas = self.store.open_project(project["id"])["canvas"]
        node = next(item for item in canvas["nodes"] if item["id"] == "result-node")
        self.assertNotIn("pendingTasks", node)
        self.assertEqual(node.get("pending", 0), 0)
        self.assertEqual(
            [item["asset_id"] for item in node["images"]],
            [first["asset_id"], second["asset_id"]],
        )
        self.assertNotIn("_generationBatchApplied", node)
        final_logs = [item for item in canvas["logs"] if item.get("id") == "generation-partial"]
        self.assertEqual(len(final_logs), 1)
        self.assertEqual(final_logs[0]["status"], "succeeded")
        self.assertEqual(len(final_logs[0]["outputs"]), 2)

    def test_partial_completion_requires_pending_guard_like_terminal_completion(self):
        project = self.store.create_project("增量写回需 pending 守卫")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        media = self.store.import_media(project["id"], base64.b64decode(tiny_png_base64()), "stray.png", "image/png")
        with self.assertRaisesRegex(Exception, "生成任务已结束"):
            self.store.complete_image_generation(
                project["id"],
                "result-node",
                [{"asset_id": media["asset_id"], "kind": "image"}],
                {"id": "generation-stray", "type": "online-image", "status": "running", "outputs": [], "result_node_id": "result-node"},
                task_id="project_img_stray_partial",
                partial=True,
                require_pending=True,
            )

    def test_retrying_a_failed_task_uses_one_new_identity_and_one_new_adapter_call(self):
        project = self.store.create_project("失败重试幂等")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "images": []}}],
            base_revision=0,
        )
        attempts = 0

        async def fail_then_succeed(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("fake first attempt failed")
            return (
                {"type": "b64", "value": tiny_png_base64(), "mime_type": "image/png"},
                {"data": [{"b64_json": tiny_png_base64()}], "id": "upstream-retry"},
            )

        payload = {
            "prompt": "retry once",
            "routing_mode": "auto",
            "size": "1024x1024",
            "aspect_ratio": "1:1",
            "resolution": "1k",
            "quality": "auto",
            "n": 1,
            "reference_asset_ids": [],
            "media_type": "image",
            "result_node_id": "result-node",
            "base_revision": 1,
        }
        adapter = AsyncMock(side_effect=fail_then_succeed)
        with patch.object(main, "load_api_providers", return_value=self.providers), patch.object(main, "generate_ai_image", adapter):
            submitted = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks",
                json=payload,
            )
            self.assertEqual(submitted.status_code, 202, submitted.text)
            failed_id = submitted.json()["task_id"]
            self.assertEqual(self.wait_for_task(failed_id)["status"], "failed")
            # Simulate process-memory loss: retry must resolve the durable
            # failed record rather than requiring the old registry entry.
            with main.CANVAS_TASK_LOCK:
                main.CANVAS_TASKS.pop(failed_id, None)
                main.PROJECT_IMAGE_GENERATION_FUTURES.pop(failed_id, None)

            first_retry = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks/{failed_id}/retry"
            )
            self.assertEqual(first_retry.status_code, 202, first_retry.text)
            second_retry = self.client.post(
                f"/api/project-library/{project['id']}/image-generation-tasks/{failed_id}/retry"
            )
            self.assertEqual(second_retry.status_code, 202, second_retry.text)
            retry_id = first_retry.json()["task_id"]
            self.assertNotEqual(retry_id, failed_id)
            self.assertEqual(second_retry.json()["task_id"], retry_id)
            self.assertEqual(first_retry.json()["retry_of_task_id"], failed_id)
            self.assertEqual(first_retry.json()["idempotency_key"], f"retry:{failed_id}")
            self.assertEqual(self.wait_for_task(retry_id)["status"], "succeeded")

        self.assertEqual(adapter.await_count, 2)
        history_by_task = {
            item.get("task_id"): item
            for item in self.store.open_project(project["id"])["canvas"].get("logs") or []
        }
        self.assertEqual(history_by_task[failed_id]["status"], "failed")
        self.assertEqual(history_by_task[retry_id]["status"], "succeeded")

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

    def test_archived_image_is_persisted_as_transient_before_project_commit(self):
        project = self.store.create_project("归档临时状态")
        encoded = base64.b64encode(base64.b64decode(tiny_png_base64())).decode("ascii")

        item = asyncio.run(main.archive_project_generated_image(
            project["id"],
            {"type": "b64", "value": encoded, "mime_type": "image/png"},
            0,
        ))

        record = self.store.media_record(project["id"], item["asset_id"])
        self.assertEqual(record["source"]["type"], "online_generation")
        self.assertEqual(record["source"]["lifecycle"], "transient")
        self.assertEqual(self.store.preview_project_cleanup(project["id"])["candidate_count"], 1)

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
        self.assertEqual(opened["logs"][-1]["createdAt"], opened["logs"][-1]["created_at"])
        self.assertGreater(opened["logs"][-1]["createdAt"], 0)
        self.assertEqual(self.store.list_media(project["id"]), [])


if __name__ == "__main__":
    unittest.main()
