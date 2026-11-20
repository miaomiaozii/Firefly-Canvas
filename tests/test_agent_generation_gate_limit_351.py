"""#351 生图类工具：服务端确认门 + 会话生成限额 + 结果挂节点/项目历史。
门语义本身由 request_confirmation_core 既有测试覆盖；这里钉三件事：
1) generate.trigger / canvas.batch_edit 在 agent 入口过门（拒→409/McpToolError）；
2) 每会话限额（超限 429，MCP 侧 ValueError）；
3) 成功后结果节点（smart-image）带项目媒体 images、项目历史含 succeeded 记录；
   失败后 pendingTasks 清理 + failed 历史。
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.routers import api_agent_tools, api_mcp
from tests.agent_image_task_fakes import patch_candidate


def _png_bytes():
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 200, 30)).save(buf, format="PNG")
    return buf.getvalue()


class _Base(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-351-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self._prev_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        # 上下文管理的 TestClient：lifespan portal 常驻——asyncio.create_task 的
        # 后台生成任务才会在响应之后继续被事件循环调度（裸 TestClient 每次请求
        # 用完即弃 loop，后台任务会永远停在 queued——probe 实证）。
        self._ctx = TestClient(main.app, base_url="http://127.0.0.1:8125",
                               headers={"origin": "http://127.0.0.1:8125"})
        self.client = self._ctx.__enter__()
        pid = self.store.create_project("Gen351")["id"]
        self.pid = pid
        self.store.apply_mutations(pid, [
            {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt",
                                           "title": "P", "prompt": "一只萤火虫", "x": 0, "y": 0}},
            {"op": "upsert_node", "node": {"id": "agent-1", "type": "smart-agent",
                                           "title": "Agent", "x": -200, "y": 0}},
        ], base_revision=0)
        self._prev_core = api_agent_tools.request_confirmation_core
        self._prev_mcp_core = api_mcp.request_confirmation_core
        self._prev_limit = api_agent_tools.GENERATION_SESSION_LIMIT
        self._prev_outdir = None
        from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
        self._tasks = CANVAS_TASKS
        self._tasks_lock = CANVAS_TASK_LOCK
        self._task_keys = set(CANVAS_TASKS)

    def tearDown(self):
        api_agent_tools.request_confirmation_core = self._prev_core
        api_mcp.request_confirmation_core = self._prev_mcp_core
        api_agent_tools.GENERATION_SESSION_LIMIT = self._prev_limit
        # 防泄漏：本测试造的生成任务不得残留在全局任务表（曾污染 test_global_tasks 空控制台钉）
        with self._tasks_lock:
            for key in list(self._tasks):
                if key not in self._task_keys:
                    del self._tasks[key]
        main.FIREFLY_PROJECT_STORE = self._prev_store
        self._ctx.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def _approve(self, approved=True, reason=""):
        # 门语义由 request_confirmation_core 既有测试覆盖；这里直接替换 core，
        # 不碰 _CONFIRMATION_MANAGER（core 被替后 manager 不会被访问）。
        async def core(project_id, node_id, tool, params, message, timeout_seconds=60):
            return {"approved": approved} if approved else {"approved": False, "reason": reason}
        api_agent_tools.request_confirmation_core = core
        # MCP 侧 _confirm_gate 引用的是 api_mcp 命名空间的同名函数——两处都要钉。
        api_mcp.request_confirmation_core = core

    def _fake_build(self, ok=True):
        out_dir = Path(main.OUTPUT_OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"351-test-{os.urandom(4).hex()}.png"
        (out_dir / name).write_bytes(_png_bytes())

        async def build(payload):
            if not ok:
                raise RuntimeError("上游生图失败（测试注入）")
            return {"prompt": payload.prompt, "images": [f"/output/{name}"],
                    "image_items": [{"url": f"/output/{name}", "mime_type": "image/png"}],
                    "provider_id": "p1", "provider_name": "P1", "model": "m1"}
        self.enterContext(patch_candidate(build))

    def _headers(self):
        return {"X-Agent-Project": self.pid}

    def _await_task(self, task_id, timeout=15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with api_agent_tools.CANVAS_TASK_LOCK:
                task = dict(api_agent_tools.CANVAS_TASKS.get(task_id) or {})
            if task.get("status") in ("succeeded", "failed"):
                return task
            time.sleep(0.1)
        self.fail(f"task {task_id} 未在 {timeout}s 内到达终态: {task!r}")


class RestGeneration351Tests(_Base):
    def test_agent_trigger_gated_rejected_409(self):
        self._approve(approved=False, reason="rejected")
        r = self.client.post(f"/api/projects/{self.pid}/generate/trigger?agent_node_id=agent-1",
                             json={"node_id": "prompt-1"}, headers=self._headers())
        self.assertEqual(r.status_code, 409)
        # #391：统一形状，票号已剥，语义保留
        self.assertIn("拒绝", r.text)
        self.assertEqual(r.json()["detail"]["code"], "CONFIRM_REJECTED")

    def test_agent_trigger_requires_agent_node_id(self):
        r = self.client.post(f"/api/projects/{self.pid}/generate/trigger",
                             json={"node_id": "prompt-1"}, headers=self._headers())
        self.assertEqual(r.status_code, 400)
        # #357 语义保留（文案），#391：票号不再出现在响应里，code 显式
        self.assertIn("agent_node_id", r.text)
        self.assertEqual(r.json()["detail"]["code"], "CONFIRM_MISSING_NODE_ID")

    def test_agent_batch_edit_gated(self):
        self._approve(approved=False, reason="rejected")
        r = self.client.post(f"/api/projects/{self.pid}/canvas/batch_edit?agent_node_id=agent-1",
                             json={"node_ids": ["prompt-1"], "prompt": "x"},
                             headers=self._headers())
        self.assertEqual(r.status_code, 409)

    def test_session_limit_429(self):
        self._approve()
        self._fake_build()
        api_agent_tools.GENERATION_SESSION_LIMIT = 2
        ids = []
        for _ in range(2):
            r = self.client.post(f"/api/projects/{self.pid}/generate/trigger?agent_node_id=agent-1",
                                 json={"node_id": "prompt-1"}, headers=self._headers())
            self.assertEqual(r.status_code, 200, r.text)
            ids.append(r.json()["task_id"])
        r = self.client.post(f"/api/projects/{self.pid}/generate/trigger?agent_node_id=agent-1",
                             json={"node_id": "prompt-1"}, headers=self._headers())
        self.assertEqual(r.status_code, 429)
        self.assertIn("超限", r.text)
        for tid in ids:
            self._await_task(tid)

    def test_result_attached_to_node_and_history(self):
        self._approve()
        self._fake_build()
        r = self.client.post(f"/api/projects/{self.pid}/generate/trigger?agent_node_id=agent-1",
                             json={"node_id": "prompt-1"}, headers=self._headers())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        task = self._await_task(body["task_id"])
        self.assertEqual(task["status"], "succeeded", task.get("error"))
        result_node_id = body["result_node_id"]
        canvas = self.store.canvas(self.pid)
        node = next((n for n in canvas["nodes"] if n.get("id") == result_node_id), None)
        self.assertIsNotNone(node, "#351：结果节点必须存在（挂到节点）")
        self.assertEqual(node.get("type"), "smart-image")
        asset_ids = [str(i.get("asset_id")) for i in (node.get("images") or [])]
        self.assertTrue(asset_ids, "#351：结果节点应带项目媒体 images")
        record = self.store.media_record(self.pid, asset_ids[0])
        self.assertTrue(Path(str(record.get("path"))).is_file())
        history = self.store._histories.get(self.pid, [])
        rec = next((h for h in history if str(h.get("task_id") or h.get("taskId") or "")
                    == body["task_id"] or h.get("result_node_id") == result_node_id), None)
        self.assertIsNotNone(rec, "#351：项目历史必须记录本次生成")
        self.assertEqual(rec.get("status"), "succeeded")
        self.assertTrue((task.get("result") or {}).get("history_recorded"))

    def test_failure_records_history_and_clears_pending(self):
        self._approve()
        self._fake_build(ok=False)
        r = self.client.post(f"/api/projects/{self.pid}/generate/trigger?agent_node_id=agent-1",
                             json={"node_id": "prompt-1"}, headers=self._headers())
        body = r.json()
        task = self._await_task(body["task_id"])
        self.assertEqual(task["status"], "failed")
        canvas = self.store.canvas(self.pid)
        node = next((n for n in canvas["nodes"] if n.get("id") == body["result_node_id"]), None)
        self.assertTrue(node is None or not (node.get("pendingTasks") or []),
                        "#351：失败必须清掉结果节点 pendingTasks")
        history = self.store._histories.get(self.pid, [])
        self.assertTrue(any(h.get("status") == "failed" for h in history),
                        "#351：失败也要写项目历史")

    def test_same_origin_trigger_requires_agent_project_header(self):
        before = self.store.canvas(self.pid)
        with patch.object(api_agent_tools, "request_confirmation_core",
                          new=AsyncMock(return_value={"approved": True})) as confirm:
            r = self.client.post(f"/api/projects/{self.pid}/generate/trigger?agent_node_id=agent-1",
                                 json={"node_id": "prompt-1"})
        self.assertEqual(r.status_code, 400, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["code"], "INVALID_ARGUMENT")
        self.assertIn("X-Agent-Project", detail["message"])
        confirm.assert_not_awaited()
        self.assertEqual(self.store.canvas(self.pid), before)
        with self._tasks_lock:
            self.assertFalse(any(task.get("project_id") == self.pid for task in self._tasks.values()))


class McpGeneration351Tests(_Base):
    def _run_impl(self, name, args, node_id="agent-1"):
        import app.routers.api_mcp as _m
        return asyncio.run(_m.TOOL_IMPLS[name](self.pid, node_id, args))

    def test_mcp_trigger_rejected_raises(self):
        self._approve(approved=False, reason="rejected")
        with self.assertRaises(api_mcp.McpToolError):
            self._run_impl("generate.trigger", {"node_id": "prompt-1"})

    def test_mcp_trigger_over_limit_raises(self):
        self._approve()
        self._fake_build()
        api_agent_tools.GENERATION_SESSION_LIMIT = 1
        self._run_impl("generate.trigger", {"node_id": "prompt-1"})
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            self._run_impl("generate.trigger", {"node_id": "prompt-1"})
        self.assertIn("超限", str(cm.exception))

    def test_mcp_batch_edit_counts_by_nodes(self):
        self._approve()
        self._fake_build()
        api_agent_tools.GENERATION_SESSION_LIMIT = 3
        self.store.apply_mutations(self.pid, [
            {"op": "upsert_node", "node": {"id": "prompt-2", "type": "smart-prompt",
                                           "prompt": "y", "x": 0, "y": 100}}],
            base_revision=int(self.store.canvas(self.pid).get("revision") or 0))
        self._run_impl("canvas.batch_edit", {"node_ids": ["prompt-1", "prompt-2"], "prompt": "z"})
        self._run_impl("generate.trigger", {"node_id": "prompt-1"})  # 2+1=3 恰好达上限
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            self._run_impl("generate.trigger", {"node_id": "prompt-1"})  # 第 4 张超上限→拒

    def test_node_delete_resets_generation_count(self):
        self._approve()
        self._fake_build()
        api_agent_tools.GENERATION_SESSION_LIMIT = 1
        self._run_impl("generate.trigger", {"node_id": "prompt-1"})
        asyncio.run(api_agent_tools.delete_node_core(self.pid, "agent-1"))
        # 节点销毁 → 额度清零 → 再次触发不再被限额拦（仍受门控，已批准）
        self._run_impl("generate.trigger", {"node_id": "prompt-1"})

    def test_agent_node_delete_rebuild_resets_quota(self):
        # #351 review 裁定：DELETE /agent-nodes/{id} 后同 node_id 重建，额度从 0 开始。
        self._approve()
        self._fake_build()
        api_agent_tools.GENERATION_SESSION_LIMIT = 1
        url = f"/api/projects/{self.pid}/generate/trigger?agent_node_id=agent-1"
        r = self.client.post(url, headers=self._headers(), json={"node_id": "prompt-1"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(url, headers=self._headers(), json={"node_id": "prompt-1"})
        self.assertEqual(r.status_code, 429)
        # 单测不拉真 pi/codex 进程：桩掉宿主会话获取（DELETE 处理器内仅 stop）
        from app.services import agent_host as _ah

        class _NoSession:
            is_running = False

        async def _fake_goc(project_id, node_id):
            return _NoSession()

        _prev = _ah.agent_host_manager.get_or_create_session
        _ah.agent_host_manager.get_or_create_session = _fake_goc
        try:
            d = self.client.delete(f"/api/projects/{self.pid}/agent-nodes/agent-1",
                                   headers=self._headers())
            self.assertEqual(d.status_code, 200)
            r = self.client.post(url, headers=self._headers(), json={"node_id": "prompt-1"})
            self.assertEqual(r.status_code, 200, f"重建后应重新获得额度: {r.text}")
        finally:
            _ah.agent_host_manager.get_or_create_session = _prev


if __name__ == "__main__":
    unittest.main()
