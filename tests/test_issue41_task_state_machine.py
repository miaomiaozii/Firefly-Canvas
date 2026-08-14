# -*- coding: utf-8 -*-
"""#41 生图任务状态机 bug 的 API 级确定性红复现与回归（diagnosing-bugs Phase 1 反馈环）。

用户症状（#41）：
- 生图节点卡'生成中'，计时器无限增长；历史记录已有完成图片但节点仍显示生成中。
- 后端：provider 只收到 1 条请求、页面无回传（#40 续报）。
- 用户明确要求：并发连发（不是一张一张慢吞吞传）；最大执行时长硬超时、超时标记 timeout。

红->绿 目标（修复前预期 RED，修复后 GREEN）：
1. v1 单 POST n=8 必须以受控并发连发（当前顺序循环 max_concurrent=1 -> RED）。
2. v1 n=8 遇到 provider 429 需退避重试后全部成功（当前无重试，第 1 个 429 即任务失败 -> RED）。
3. v1 任务硬超时后必须到达终态 timeout（当前无 deadline，任务永久 running -> RED）。
4. legacy 单 POST n=8 恢复受控并发连发（#40 的 Semaphore(1) 全串行 -> RED）。
5. legacy 任务硬超时后必须到达终态 timeout + 超时说明（当前无 deadline -> RED）。
6. 8 任务并发隔离性：1 个失败/超时，其余 7 个正常完成（超时场景当前 RED）。

环境变量（修复引入）：
- FIREFLY_PROVIDER_GENERATION_MAX_CONCURRENCY（默认 3）
- FIREFLY_PROVIDER_429_RETRY_ATTEMPTS（默认 3）
- FIREFLY_PROVIDER_429_BACKOFF_SECONDS（默认 0.5）
- FIREFLY_IMAGE_GENERATION_TASK_TIMEOUT_SECONDS（默认 1800）
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


def _rate_limited_error(text="rate limited"):
    request = httpx.Request("POST", PROVIDER_INVALID_URL)
    response = httpx.Response(429, request=request, text=text)
    return httpx.HTTPStatusError(text, request=request, response=response)


class ConcurrencyFake:
    """全成功并发计测 fake：记录最大在飞数 max_concurrent。"""

    def __init__(self, hold=0.03):
        self.hold = hold
        self.attempts = 0
        self.successes = 0
        self.in_flight = 0
        self.max_concurrent = 0

    async def __call__(self, *args, **kwargs):
        self.attempts += 1
        self.in_flight += 1
        self.max_concurrent = max(self.max_concurrent, self.in_flight)
        try:
            await asyncio.sleep(self.hold)
            self.successes += 1
            return _fake_payload()
        finally:
            self.in_flight -= 1


class Flaky429Fake:
    """前 fail_first 次调用返回 429，之后成功。用于断言 429 退避重试。"""

    def __init__(self, fail_first=2, hold=0.02):
        self.fail_first = fail_first
        self.hold = hold
        self.attempts = 0
        self.successes = 0

    async def __call__(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts <= self.fail_first:
            raise _rate_limited_error()
        await asyncio.sleep(self.hold)
        self.successes += 1
        return _fake_payload()


class SlowFake:
    """慢 fake（模拟 provider 挂起），用于硬超时终态。

    sleep 时长取 8.0s：必须超过 wait 轮询 deadline（6s）保证红态稳定失败，
    但又足够短——v1 任务跑在非 daemon 的 firefly-generation executor 线程上，
    3600s 会卡死进程退出；8s 让红态测试失败后进程能在几秒内自然退出。
    """

    def __init__(self, hold=8.0):
        self.hold = hold
        self.attempts = 0

    async def __call__(self, *args, **kwargs):
        self.attempts += 1
        await asyncio.sleep(self.hold)
        return _fake_payload()


class MixedFake:
    """prompt 含 'fail-me' -> 503；含 'slow-me' -> 挂起；其余成功。用于 8 任务隔离性。"""

    def __init__(self, hold=0.02, slow_hold=8.0):
        self.hold = hold
        self.slow_hold = slow_hold
        self.attempts = 0
        self.successes = 0

    async def __call__(self, *args, **kwargs):
        self.attempts += 1
        prompt = str(args[0] if args else kwargs.get("prompt", ""))
        if "fail-me" in prompt:
            request = httpx.Request("POST", PROVIDER_INVALID_URL)
            response = httpx.Response(503, request=request, text="upstream down")
            raise httpx.HTTPStatusError("upstream down", request=request, response=response)
        if "slow-me" in prompt:
            await asyncio.sleep(self.slow_hold)
        else:
            await asyncio.sleep(self.hold)
        self.successes += 1
        return _fake_payload()


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

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "timeout"}


class V1BurstStateMachineTests(unittest.TestCase):
    """v1 项目画布路径：POST /api/project-library/{pid}/image-generation-tasks。"""

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

    def wait_for_task(self, task_id, deadline=6):
        end = time.monotonic() + deadline
        task = None
        while time.monotonic() < end:
            task = self.client.get(f"/api/canvas-image-tasks/{task_id}").json()
            if task["status"] in TERMINAL_STATUSES:
                return task
            time.sleep(0.02)
        self.fail(f"image task did not finish within {deadline}s: {task}")

    def _submit_n8(self):
        project = self.store.create_project("v1 n8 burst")
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

    def _submit_n_tasks(self, prompts, fake):
        """同一项目内提交 len(prompts) 个 n=1 任务（独立节点），返回 {prompt: task}。

        任务在 executor 线程上后台完成会持续推进 canvas revision，提交窗口内可能撞
        RevisionConflict(409)；与真实前端一致地重读 revision 重试提交。
        """
        project = self.store.create_project("v1 multi-task isolation")
        nodes = [
            {"op": "upsert_node", "node": {"id": f"node-{i}", "type": "smart-image", "x": 40 + i * 60, "y": 80, "images": [], "pending": 1}}
            for i in range(len(prompts))
        ]
        self.store.apply_mutations(project["id"], nodes, base_revision=0)
        submitted = []
        with patch.object(main, "load_api_providers", return_value=[FAKE_PROVIDER]), patch.object(main, "generate_ai_image", new=fake):
            for i, prompt in enumerate(prompts):
                request = {
                    "prompt": prompt,
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
                    "result_node_id": f"node-{i}",
                }
                response = None
                for _attempt in range(40):
                    request["base_revision"] = self.store.open_project(project["id"])["canvas"]["revision"]
                    response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=request)
                    if response.status_code != 409:
                        break
                    time.sleep(0.02)
                self.assertEqual(response.status_code, 202, response.text)
                submitted.append((response.json()["task_id"], prompt))
            tasks = {prompt: self.wait_for_task(task_id) for task_id, prompt in submitted}
        return tasks

    def test_v1_single_post_n8_fires_concurrently(self):
        """红->绿：n=8 必须以受控并发连发（当前顺序循环 max_concurrent=1 -> RED）。"""
        project, request = self._submit_n8()
        fake = ConcurrencyFake(hold=0.03)
        with patch.object(main, "load_api_providers", return_value=[FAKE_PROVIDER]), patch.object(main, "generate_ai_image", new=fake):
            response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=request)
            self.assertEqual(response.status_code, 202, response.text)
            task = self.wait_for_task(response.json()["task_id"])
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(fake.successes, 8)
        self.assertGreaterEqual(fake.max_concurrent, 2, f"用户要求并发连发；max_concurrent={fake.max_concurrent}")
        node = next(n for n in self.store.open_project(project["id"])["canvas"]["nodes"] if n["id"] == "result-node")
        self.assertEqual(len(node["images"]), 8)

    def test_v1_n8_provider_429_is_retried_with_backoff(self):
        """红->绿：provider 429 需退避重试后 8 张全部成功（当前无重试 -> 任务 failed -> RED）。"""
        project, request = self._submit_n8()
        fake = Flaky429Fake(fail_first=2, hold=0.02)
        with patch.dict(os.environ, {"FIREFLY_PROVIDER_429_BACKOFF_SECONDS": "0.02"}), patch.object(main, "load_api_providers", return_value=[FAKE_PROVIDER]), patch.object(main, "generate_ai_image", new=fake):
            response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=request)
            self.assertEqual(response.status_code, 202, response.text)
            task = self.wait_for_task(response.json()["task_id"])
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(fake.successes, 8)
        self.assertGreaterEqual(fake.attempts, 9, f"429 应被重试；attempts={fake.attempts}")

    def test_v1_task_hard_timeout_reaches_timeout_terminal(self):
        """红->绿：任务硬超时后必须到达终态 timeout（当前无 deadline -> 永久 running -> RED）。"""
        project, request = self._submit_n8()
        fake = SlowFake()
        with patch.dict(os.environ, {"FIREFLY_IMAGE_GENERATION_TASK_TIMEOUT_SECONDS": "0.5"}), patch.object(main, "load_api_providers", return_value=[FAKE_PROVIDER]), patch.object(main, "generate_ai_image", new=fake):
            response = self.client.post(f"/api/project-library/{project['id']}/image-generation-tasks", json=request)
            self.assertEqual(response.status_code, 202, response.text)
            task = self.wait_for_task(response.json()["task_id"])
        self.assertEqual(task["status"], "timeout", task)

    def test_v1_8_tasks_one_failure_others_succeed(self):
        """回归：8 任务并发，1 个 503 失败，其余 7 个正常 succeeded。"""
        fake = MixedFake()
        prompts = [f"ok-{i}" for i in range(8)]
        prompts[3] = "fail-me"
        tasks = self._submit_n_tasks(prompts, fake)
        statuses = sorted(t["status"] for t in tasks.values())
        self.assertEqual(statuses, sorted(["succeeded"] * 7 + ["failed"]), tasks)

    def test_v1_8_tasks_one_timeout_others_succeed(self):
        """红->绿：8 任务并发，1 个挂起超时 -> timeout 终态，其余 7 个正常 succeeded。"""
        fake = MixedFake()
        prompts = [f"ok-{i}" for i in range(8)]
        prompts[5] = "slow-me"
        with patch.dict(os.environ, {"FIREFLY_IMAGE_GENERATION_TASK_TIMEOUT_SECONDS": "0.5"}):
            tasks = self._submit_n_tasks(prompts, fake)
        statuses = sorted(t["status"] for t in tasks.values())
        self.assertEqual(statuses, sorted(["succeeded"] * 7 + ["timeout"]), tasks)


class LegacyBurstStateMachineTests(unittest.IsolatedAsyncioTestCase):
    """legacy 画布路径：POST /api/canvas-image-tasks。"""

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

    async def wait_task(self, task_id, deadline=6):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            response = await self.client.get(f"/api/canvas-image-tasks/{task_id}")
            if response.status_code == 200:
                task = response.json()
                if task["status"] in TERMINAL_STATUSES:
                    return task
            await asyncio.sleep(0.01)
        self.fail(f"legacy task did not finish within {deadline}s: {task_id}")

    async def test_legacy_single_post_n8_fires_concurrently(self):
        """红->绿：legacy n=8 必须恢复受控并发连发（#40 的 Semaphore(1) 全串行 -> RED）。"""
        fake = ConcurrencyFake(hold=0.03)
        with patch.object(main, "generate_ai_image", new=fake):
            response = await self.client.post("/api/canvas-image-tasks", json=self._payload(8))
            self.assertEqual(response.status_code, 200, response.text)
            task = await self.wait_task(response.json()["task_id"])
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(fake.successes, 8)
        self.assertGreaterEqual(fake.max_concurrent, 2, f"用户要求并发连发；max_concurrent={fake.max_concurrent}")
        self.assertEqual(len(task["result"]["images"]), 8)

    async def test_legacy_task_hard_timeout_marks_failed(self):
        """红->绿：legacy 任务硬超时后必须到达终态 timeout + 超时说明（当前无 deadline -> RED）。"""
        fake = SlowFake()
        with patch.dict(os.environ, {"FIREFLY_IMAGE_GENERATION_TASK_TIMEOUT_SECONDS": "0.5"}), patch.object(main, "generate_ai_image", new=fake):
            response = await self.client.post("/api/canvas-image-tasks", json=self._payload(8))
            self.assertEqual(response.status_code, 200, response.text)
            task = await self.wait_task(response.json()["task_id"])
        self.assertEqual(task["status"], "timeout", task)
        self.assertIn("超时", str(task.get("error") or ""), task)
