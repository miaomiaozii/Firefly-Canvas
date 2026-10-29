"""Tests for issue #266: canvas.batch_edit（§59.3：选中 N 节点 + 一句 prompt = N 次生成）。

票面语义 = 调用既有生成通路（每节点一次 trigger_generation_core：独立任务 + 后台运行），
**非本地 PIL 处理**；结果放各自旁边 = 各节点自身输出。
Spike 结论（.pipeline/batches/issue-266b-result.md）：trigger_generation_core 每次调用
独立建 CANVAS_TASKS 条目 + asyncio 后台任务，连续 N 次调用安全；生成主链路在
app/services/generation.py（会话B领地，禁改），本票只复用其 core。

Mock 边界：无网络 / 无 provider —— monkeypatch main.route_image_generation /
load_api_providers + api_agent_tools._run_agent_generation（后台运行体），
不触碰真实生成管线。错误文案带下一步建议（#266 票面规范）。
"""
import json
import sys
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: F401
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
from app.routers import api_agent_tools, api_mcp
from tests.test_mcp_endpoint import McpTestBase

PROMPT = "a glowing firefly in the forest"


class BatchEditSurfaceTests(McpTestBase):
    """声明表机制：新工具必须进 api_mcp.TOOLS 声明表（ADR 0022）+ 严格 schema。"""

    def test_batch_edit_declared_with_strict_schema(self):
        resp = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
        self.assertIn("canvas.batch_edit", tools)
        schema = tools["canvas.batch_edit"]["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema.get("additionalProperties"))
        self.assertEqual(schema["required"], ["node_ids", "prompt"])
        node_ids = schema["properties"]["node_ids"]
        self.assertEqual(node_ids["type"], "array")
        self.assertEqual(node_ids["items"]["type"], "string")
        self.assertEqual(node_ids["minItems"], 1)
        self.assertEqual(node_ids["maxItems"], 10)
        prompt = schema["properties"]["prompt"]
        self.assertEqual(prompt["type"], "string")
        self.assertEqual(prompt["minLength"], 1)
        # 票面语义：调用既有生成通路，描述必须声明消耗生成（区别于 edit_* 的"不消耗外部生成"）
        self.assertIn("生成", tools["canvas.batch_edit"]["description"])
        # 与 generate.trigger 一致：生成类操作不走确认门（#242 语义：确认门针对破坏性写操作）
        self.assertNotIn("canvas.batch_edit", api_mcp.CONFIRMATION_GATED_TOOLS)
        # 声明表必须与 impl 接线一致
        self.assertIn("canvas.batch_edit", api_mcp.TOOL_IMPLS)


class BatchEditBehaviorTests(McpTestBase):
    """canvas.batch_edit 行为：N 节点 → N 个独立生成任务 + batch prompt 写入各节点。"""

    def setUp(self):
        super().setUp()
        self.open_tab()
        canvas = self.store.canvas(self.project_id)
        self.store.apply_mutations(
            self.project_id,
            [
                {"op": "upsert_node", "node": {"id": "img-a", "type": "smart-image", "title": "A", "x": 0, "y": 0, "prompt": "old-a"}},
                {"op": "upsert_node", "node": {"id": "img-b", "type": "smart-image", "title": "B", "x": 0, "y": 20, "prompt": "old-b"}},
            ],
            base_revision=int(canvas.get("revision") or 0))
        # Mock 边界：无网络 / 无 provider —— 假路由 + 假后台运行体
        self.previous_route = main.route_image_generation
        self.previous_providers = main.load_api_providers
        self.previous_runner = api_agent_tools._run_agent_generation
        main.route_image_generation = lambda *a, **k: types.SimpleNamespace(
            provider_id="fake", model="fake")
        main.load_api_providers = lambda: []
        api_agent_tools._run_agent_generation = self._fake_runner

    async def _fake_runner(self, task_id, project_id, node_id, payload):
        # 后台运行体替身：直接把任务标记成功（trigger_generation_core 用 asyncio.create_task 调度，必须是协程）
        with CANVAS_TASK_LOCK:
            task = CANVAS_TASKS.get(task_id)
            if task is not None:
                task.update(status="succeeded", error="", updated_at=time.time())

    def tearDown(self):
        main.route_image_generation = self.previous_route
        main.load_api_providers = self.previous_providers
        api_agent_tools._run_agent_generation = self.previous_runner
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS.clear()
        super().tearDown()

    def call(self, args, project=""):
        result = self.tool_result(self.tool_call("canvas.batch_edit", args, project=project or self.project_id))
        if result.get("isError"):
            return result, None
        return result, json.loads(result["content"][0]["text"])

    def test_batch_edit_creates_n_tasks_and_writes_prompts(self):
        result, data = self.call({"node_ids": ["img-a", "img-b"], "prompt": PROMPT})
        self.assertIsInstance(data, dict, result)
        self.assertTrue(data.get("success"), data)
        self.assertEqual(data["count"], 2)
        by_node = {t["node_id"]: t["task_id"] for t in data["tasks"]}
        self.assertEqual(set(by_node), {"img-a", "img-b"})
        for task_id in by_node.values():
            self.assertTrue(task_id, "每个节点必须返回 task_id")
        # N 个独立任务，各自绑定对应节点
        with CANVAS_TASK_LOCK:
            for node_id, task_id in by_node.items():
                task = CANVAS_TASKS.get(task_id)
                self.assertIsNotNone(task, f"task {task_id} missing from CANVAS_TASKS")
                self.assertEqual(task["node_id"], node_id)
                self.assertEqual(task["project_id"], self.project_id)
        # batch prompt 写入各节点（trigger_generation_core 读节点自身 prompt）
        canvas = self.store.canvas(self.project_id)
        nodes = {n["id"]: n for n in canvas.get("nodes", [])}
        self.assertEqual(nodes["img-a"]["prompt"], PROMPT)
        self.assertEqual(nodes["img-b"]["prompt"], PROMPT)

    def test_batch_edit_missing_node_is_error_with_next_step(self):
        result, _data = self.call({"node_ids": ["ghost"], "prompt": PROMPT})
        self.assertTrue(result.get("isError"), result)
        text = result["content"][0]["text"]
        self.assertIn("ghost", text)
        self.assertIn("canvas.list_nodes", text)  # 下一步建议
        with CANVAS_TASK_LOCK:
            self.assertEqual(len(CANVAS_TASKS), 0, "失败不得留下任何生成任务")

    def test_batch_edit_empty_prompt_is_error_with_next_step(self):
        result, _data = self.call({"node_ids": ["img-a"], "prompt": "   "})
        self.assertTrue(result.get("isError"), result)
        self.assertIn("prompt", result["content"][0]["text"])
        with CANVAS_TASK_LOCK:
            self.assertEqual(len(CANVAS_TASKS), 0)

    def test_batch_edit_empty_node_ids_is_error(self):
        result, _data = self.call({"node_ids": [], "prompt": PROMPT})
        self.assertTrue(result.get("isError"), result)
        self.assertIn("node_ids", result["content"][0]["text"])
        with CANVAS_TASK_LOCK:
            self.assertEqual(len(CANVAS_TASKS), 0)

    def test_batch_edit_too_many_nodes_is_error(self):
        ids = [f"n-{i}" for i in range(11)]
        result, _data = self.call({"node_ids": ids, "prompt": PROMPT})
        self.assertTrue(result.get("isError"), result)
        text = result["content"][0]["text"]
        self.assertIn("10", text)  # 上限提示
        with CANVAS_TASK_LOCK:
            self.assertEqual(len(CANVAS_TASKS), 0)

    def test_batch_edit_project_not_found_is_error(self):
        result, _data = self.call({"node_ids": ["img-a"], "prompt": PROMPT}, project="missing-project")
        self.assertTrue(result.get("isError"), result)


if __name__ == "__main__":
    unittest.main()
