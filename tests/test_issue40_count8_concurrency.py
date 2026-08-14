# -*- coding: utf-8 -*-
"""#40 单节点 count=8 生图并发 bug 的 API 级确定性复现与回归（Step A）。

用户症状：8 次并发时 provider 只收到 1 张，回传失败，应用完全不可用。

覆盖两条触发路径：
- v1 项目画布路径：1 个 POST /api/project-library/{pid}/image-generation-tasks（n=8）
  -> 服务端 run_project_image_task（app/routers/api_project_library.py）受控并发 8 次出站。
- legacy 画布路径：POST /api/canvas-image-tasks。n=8 时
  build_online_image_result（app/services/misc.py）内部 asyncio.gather 8 个并发出站；
  或前端 8 个并发 POST 各 n=1（static/js/smart-canvas.js 的非 v1 分支）。

BusyProviderFake 模拟并发敏感 provider：同一时刻只允许 1 个在飞请求，其余立即 429。
用于确定性复现「provider 只收到一张、其余失败、任务不可用」。#41 修复（每 provider 受控
并发信号量 + 429 有限退避重试，见 app/services/generation.py 的
run_provider_generation_with_backoff）后转绿：v1 与 legacy 均连发（非串行）、429 退避后
全部成功；重试预算/退避经 env 调大以保证忙 provider 下确定性收敛。
"""
import asyncio
import base64
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
import app.services as services  # noqa: E402
import app.services.misc as misc  # noqa: E402
from firefly_projects import ProjectStore  # noqa: E402

PROVIDER_INVALID_URL = "https://provider.invalid/v1/images/generations"


def tiny_png_base64():
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), (38, 190, 133)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _fake_payload():
    png = tiny_png_base64()
    return (
        {"type": "b64", "value": png, "mime_type": "image/png"},
        {"data": [{"b64_json": png}], "id": f"upstream-{time.monotonic_ns()}"},
    )


class PlainFakeProvider:
    """朴素 fake：每次调用都成功，用于验证代码表面确实出站 N 次。"""

    def __init__(self):
        self.attempts = 0
        self.successes = 0

    async def __call__(self, *args, **kwargs):
        self.attempts += 1
        await asyncio.sleep(0.005)
        self.successes += 1
        return _fake_payload()


class BusyProviderFake:
    """并发敏感 fake：同一时刻只允许 1 个在飞请求，其余立即 429。

    模拟用户观察到的 provider 行为（8 次并发只收到/生成 1 张）。
    """

    def __init__(self, hold=0.05):
        self.hold = hold
        self.attempts = 0
        self.successes = 0
        self.in_flight = 0
        self.max_concurrent = 0
        self._lock = asyncio.Lock()

    async def __call__(self, *args, **kwargs):
        async with self._lock:
            self.attempts += 1
            if self.in_flight >= 1:
                request = httpx.Request("POST", PROVIDER_INVALID_URL)
                response = httpx.Response(429, request=request, text="rate limited: provider busy")
                raise httpx.HTTPStatusError("provider busy", request=request, response=response)
            self.in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self.in_flight)
        try:
            await asyncio.sleep(self.hold)
            self.successes += 1
            return _fake_payload()
        finally:
            async with self._lock:
                self.in_flight -= 1


FAKE_PROVIDER = {
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


class V1PathCount8Tests(unittest.TestCase):
    """v1 项目画布路径：单 POST n=8，服务端顺序循环。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.store_root, snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        main.PROJECT_IMAGE_GENERATION_ORPHANS.clear()
        self.temp_dir.cleanup()

    def wait_for_task(self, task_id, deadline=8):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            task = self.client.get(f"/api/canvas-image-tasks/{task_id}").json()
            if task["status"] in {"succeeded", "failed", "cancelled"}:
                return task
            time.sleep(0.02)
        self.fail(f"image task did not finish: {task}")

    def _submit_n8(self):
        project = self.store.create_project("v1 n8 顺序")
        self.store.apply_mutations(
            project["id"],
            [{"op": "upsert_node", "node": {"id": "result-node", "type": "smart-image", "x": 420, "y": 80, "images": [], "pending": 1}}],
            base_revision=0,
        )
        request = {
            "prompt": "八张并发生成测试",
            "routing_mode": "auto",
            "size": "1024x1024",
            "width": 1024,
            "height": 1024,
            "aspect_ratio": "1:1",
            "resolution": "1k",
            "quality": "auto",
            "n": 8,
            "reference_asset_ids": [],
            "media_type": "image",
            "result_node_id": "result-node",
            "base_revision": 1,
        }
        return project, request

    def test_v1_single_post_n8_concurrency_sensitive_provider_still_all_succeed(self):
        """v1 路径受控并发 + 429 退避：即使 provider 只允许 1 个在飞请求，8 张也应全部成功。"""
        project, request = self._submit_n8()
        fake = BusyProviderFake(hold=0.02)
        env = {"FIREFLY_PROVIDER_429_RETRY_ATTEMPTS": "20", "FIREFLY_PROVIDER_429_BACKOFF_SECONDS": "0.02"}
        with patch.dict(os.environ, env), patch.object(main, "load_api_providers", return_value=[FAKE_PROVIDER]), patch.object(main, "generate_ai_image", new=fake):
            response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=request)
            self.assertEqual(response.status_code, 202, response.text)
            task = self.wait_for_task(response.json()["task_id"])
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(fake.successes, 8, f"attempts={fake.attempts}")
        self.assertGreaterEqual(fake.attempts, 8)
        node = next(n for n in self.store.open_project(project["id"])["canvas"]["nodes"] if n["id"] == "result-node")
        self.assertEqual(len(node["images"]), 8)


class LegacyPathCount8Tests(unittest.IsolatedAsyncioTestCase):
    """legacy 画布路径：/api/canvas-image-tasks。n=8 内部 gather 并发；或 8 个并发 POST 各 n=1。"""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_root = Path(self.temp_dir.name) / "projects"
        self.store = ProjectStore(self.store_root, snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://127.0.0.1",
            headers={"origin": "http://127.0.0.1"},
        )
        self._patches = [
            patch.object(main, "get_api_provider", return_value=FAKE_PROVIDER),
            patch.object(main.manager, "broadcast_new_image", new=AsyncMock()),
            patch.object(misc, "GLOBAL_LOOP", None),
            patch.object(services, "save_ai_image_to_output", new=AsyncMock(return_value="/output/fake-online.png")),
            patch.object(services, "image_output_meta", return_value={"url": "/output/fake-online.png", "width": 3, "height": 2, "name": "fake.png"}),
            patch.object(services, "save_to_history", new=Mock()),
        ]
        for p in self._patches:
            p.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        main.PROJECT_IMAGE_GENERATION_ORPHANS.clear()
        for p in self._patches:
            p.stop()
        self.temp_dir.cleanup()

    def _payload(self, n):
        return {
            "prompt": "并发生成测试",
            "provider_id": "fake-http",
            "model": "fake-square",
            "size": "1024x1024",
            "quality": "auto",
            "n": n,
            "reference_images": [],
        }

    async def wait_task(self, task_id, deadline=8):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            response = await self.client.get(f"/api/canvas-image-tasks/{task_id}")
            if response.status_code == 200:
                task = response.json()
                if task["status"] in {"succeeded", "failed", "cancelled"}:
                    return task
            await asyncio.sleep(0.01)
        self.fail(f"legacy task did not finish: {task_id}")

    async def test_legacy_single_post_n8_plain_provider(self):
        """朴素 fake 基线：代码表面 8 次出站全部成功。"""
        fake = PlainFakeProvider()
        with patch.object(main, "generate_ai_image", new=fake):
            response = await self.client.post("/api/canvas-image-tasks", json=self._payload(8))
            self.assertEqual(response.status_code, 200, response.text)
            task = await self.wait_task(response.json()["task_id"])
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(fake.attempts, 8)
        self.assertEqual(fake.successes, 8)
        self.assertEqual(len(task["result"]["images"]), 8)

    async def test_legacy_single_post_n8_concurrency_sensitive_provider(self):
        """红->绿：并发敏感 provider 下 n=8 连发，受控并发 + 429 退避后 8 次出站全部成功。"""
        fake = BusyProviderFake(hold=0.05)
        env = {"FIREFLY_PROVIDER_429_RETRY_ATTEMPTS": "20", "FIREFLY_PROVIDER_429_BACKOFF_SECONDS": "0.02"}
        with patch.dict(os.environ, env), patch.object(main, "generate_ai_image", new=fake):
            response = await self.client.post("/api/canvas-image-tasks", json=self._payload(8))
            self.assertEqual(response.status_code, 200, response.text)
            task = await self.wait_task(response.json()["task_id"])
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(fake.successes, 8, task)
        self.assertGreaterEqual(fake.attempts, 8, task)
        self.assertEqual(len(task["result"]["images"]), 8)

    async def test_legacy_8_concurrent_posts_n1_plain_provider(self):
        """朴素 fake 基线：8 个并发 POST 各 n=1，全部成功。"""
        fake = PlainFakeProvider()
        with patch.object(main, "generate_ai_image", new=fake):
            responses = await asyncio.gather(*[self.client.post("/api/canvas-image-tasks", json=self._payload(1)) for _ in range(8)])
            task_ids = [r.json()["task_id"] for r in responses]
            tasks = await asyncio.gather(*[self.wait_task(tid) for tid in task_ids])
        self.assertEqual([t["status"] for t in tasks], ["succeeded"] * 8)
        self.assertEqual(fake.attempts, 8)
        self.assertEqual(fake.successes, 8)

    async def test_legacy_8_concurrent_posts_concurrency_sensitive_provider(self):
        """红->绿：8 个并发任务同时出站，并发敏感 provider 下 429 退避后 8 个任务全部 succeeded。"""
        fake = BusyProviderFake(hold=0.05)
        env = {"FIREFLY_PROVIDER_429_RETRY_ATTEMPTS": "20", "FIREFLY_PROVIDER_429_BACKOFF_SECONDS": "0.02"}
        with patch.dict(os.environ, env), patch.object(main, "generate_ai_image", new=fake):
            responses = await asyncio.gather(*[self.client.post("/api/canvas-image-tasks", json=self._payload(1)) for _ in range(8)])
            task_ids = [r.json()["task_id"] for r in responses]
            tasks = await asyncio.gather(*[self.wait_task(tid) for tid in task_ids])
        statuses = [t["status"] for t in tasks]
        self.assertEqual(statuses, ["succeeded"] * 8, f"fake attempts={fake.attempts} successes={fake.successes} statuses={statuses}")
        self.assertEqual(fake.successes, 8)
