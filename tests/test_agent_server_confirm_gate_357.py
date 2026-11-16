"""#357 服务端确认门（统一执行入口）。

口径（REVIEW.md 11:0x / #357 票面）：
- 确认门挂在 pi REST 与 MCP 共用的执行入口（api_agent_tools._server_confirm_gate，
  复用 request_confirmation_core）；pi 扩展不再预确认（canvas-tools.ts 源码钉住）。
- REST DELETE /nodes、DELETE /edges（含 edit_* / media.delete）带 X-Agent-Project 时过门；
  浏览器用户路径（无该头）不进门——前端 #347 ffDialog 已在发起侧确认。
- 写操作确认等待 >60s。
门自身的阻塞/批准/autonomy 语义由 request_confirmation_core 既有测试与
test_tool_surface_consistency 的 gated()（含 #357 新增 disconnect）覆盖；这里钉接线。
"""
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.routers import api_agent_tools, api_mcp


class ServerConfirmGate357Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-357-gate-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self._prev_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8125",
                                 headers={"origin": "http://127.0.0.1:8125"})
        project = self.store.create_project("Gate 357")
        self.pid = project["id"]
        self.store.apply_mutations(self.pid, [
            {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt",
                                           "title": "P", "x": 0, "y": 0}},
            {"op": "upsert_node", "node": {"id": "image-1", "type": "smart-image",
                                           "title": "I", "x": 100, "y": 0}},
            {"op": "upsert_edge", "edge": {"id": "edge-1", "from": "prompt-1",
                                            "to": "image-1"}},
        ], base_revision=0)
        self._calls = []
        self._prev_core = api_agent_tools.request_confirmation_core

    def tearDown(self):
        api_agent_tools.request_confirmation_core = self._prev_core
        main.FIREFLY_PROJECT_STORE = self._prev_store
        self.client.close()
        self.temp_dir.cleanup()

    def _patch_decision(self, decision):
        calls = self._calls

        async def fake_core(project_id, node_id, tool, params, message, timeout_seconds=60):
            calls.append({"project_id": project_id, "node_id": node_id, "tool": tool,
                          "timeout_seconds": timeout_seconds})
            return dict(decision)

        api_agent_tools.request_confirmation_core = fake_core

    def _agent_headers(self):
        return {"X-Agent-Project": self.pid}

    # ── 浏览器路径不进门 ──────────────────────────────────────────────
    def test_browser_delete_not_gated(self):
        self._patch_decision({"approved": False, "reason": "rejected"})
        r = self.client.delete(f"/api/projects/{self.pid}/canvas/nodes/image-1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._calls, [], "浏览器（无 X-Agent-Project）不应触发服务端门")
        ids = {n.get("id") for n in self.store.canvas(self.pid)["nodes"]}
        self.assertNotIn("image-1", ids)

    # ── agent 路径必带归属节点 ───────────────────────────────────────
    def test_agent_delete_requires_agent_node_id(self):
        r = self.client.delete(f"/api/projects/{self.pid}/canvas/nodes/image-1",
                               headers=self._agent_headers())
        self.assertEqual(r.status_code, 400)
        self.assertIn("#357", r.text)
        ids = {n.get("id") for n in self.store.canvas(self.pid)["nodes"]}
        self.assertIn("image-1", ids, "400 拒绝时节点不得被删")

    # ── agent 路径过门：批准 ─────────────────────────────────────────
    def test_agent_delete_approved_deletes(self):
        self._patch_decision({"approved": True})
        r = self.client.delete(
            f"/api/projects/{self.pid}/canvas/nodes/image-1?agent_node_id=prompt-1",
            headers=self._agent_headers())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self._calls), 1)
        self.assertEqual(self._calls[0]["tool"], "canvas.delete_node")
        self.assertEqual(self._calls[0]["node_id"], "prompt-1")
        self.assertGreater(self._calls[0]["timeout_seconds"], 60,
                           "review 口径：pi 写操作超时要大于 60s")
        ids = {n.get("id") for n in self.store.canvas(self.pid)["nodes"]}
        self.assertNotIn("image-1", ids)

    # ── agent 路径过门：拒绝/超时 → 409 ─────────────────────────────
    def test_agent_delete_rejected_409(self):
        self._patch_decision({"approved": False, "reason": "rejected"})
        r = self.client.delete(
            f"/api/projects/{self.pid}/canvas/nodes/image-1?agent_node_id=prompt-1",
            headers=self._agent_headers())
        self.assertEqual(r.status_code, 409)
        self.assertIn("拒绝", r.text)
        ids = {n.get("id") for n in self.store.canvas(self.pid)["nodes"]}
        self.assertIn("image-1", ids, "被拒的删除不得落盘")

    def test_agent_delete_timeout_409(self):
        self._patch_decision({"approved": False, "reason": "timeout"})
        r = self.client.delete(
            f"/api/projects/{self.pid}/canvas/nodes/image-1?agent_node_id=prompt-1",
            headers=self._agent_headers())
        self.assertEqual(r.status_code, 409)
        self.assertIn("确认超时", r.text)

    # ── DELETE /edges 同样走门（#357 点名）──────────────────────────
    def test_edge_delete_gated(self):
        self._patch_decision({"approved": True})
        r = self.client.delete(
            f"/api/projects/{self.pid}/canvas/edges/prompt-1/image-1?agent_node_id=prompt-1",
            headers=self._agent_headers())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._calls[-1]["tool"], "canvas.delete_edge")
        conns = self.store.canvas(self.pid).get("connections", [])
        self.assertEqual([c for c in conns if c.get("id") == "edge-1"], [])

    def test_edge_delete_rejected_keeps_edge(self):
        self._patch_decision({"approved": False, "reason": "rejected"})
        r = self.client.delete(
            f"/api/projects/{self.pid}/canvas/edges/prompt-1/image-1?agent_node_id=prompt-1",
            headers=self._agent_headers())
        self.assertEqual(r.status_code, 409)
        conns = self.store.canvas(self.pid).get("connections", [])
        self.assertTrue([c for c in conns if c.get("id") == "edge-1"])

    # ── media.delete 走门 ───────────────────────────────────────────
    def test_media_delete_agent_requires_gate(self):
        r = self.client.delete(f"/api/projects/{self.pid}/media/whatever.png",
                               headers=self._agent_headers())
        self.assertEqual(r.status_code, 400)
        self.assertIn("#357", r.text)

    # ── 超时口径 >60s（两侧一致）────────────────────────────────────
    def test_confirm_timeouts_exceed_60s(self):
        self.assertGreater(api_agent_tools.AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS, 60)
        self.assertGreater(api_mcp.CONFIRM_TIMEOUT_SECONDS, 60)

    # ── 源码钉住：扩展不再预确认；前端确认走 ffDialog（#347 同机制）──
    def test_extension_no_client_side_preconfirm(self):
        src = (ROOT / "app" / "agent_extensions" / "canvas-tools.ts").read_text(encoding="utf-8")
        self.assertNotIn("confirmGate(", src, "#357：pi 扩展预确认必须删除（门在服务端）")
        self.assertIn("agent_node_id=", src, "写操作必须带 ?agent_node_id= 归属参数")

    def test_extension_gated_ops_all_carry_gate_query(self):
        """服务端 _server_confirm_gate 包裹的每个 REST 写端点，扩展侧 URL 必须带 gateQuery()。
        曾在 UI 实测发现 generate.trigger/batch_edit 漏带 → 服务端 400。"""
        src = (ROOT / "app" / "agent_extensions" / "canvas-tools.ts").read_text(encoding="utf-8")
        gated = ["generate/trigger", "canvas/batch_edit",
                 "canvas/edit_crop", "canvas/edit_rotate", "canvas/edit_flip",
                 "canvas/edit_outpaint", "canvas/edit_erase"]
        import re
        for path in gated:
            # 匹配扩展里对该端点的 callApi URL 模板（反引号串）
            hits = [m.group(0) for m in re.finditer(r"`[^`]*" + re.escape(path) + r"[^`]*`", src)]
            self.assertTrue(hits, f"扩展未调用 {path}")
            for url in hits:
                self.assertIn("gateQuery()", url,
                              f"#357：扩展对门控端点 {path} 的 URL 缺 gateQuery()：{url}")
        # 删除类：REST 门在 DELETE /nodes/{id}、/edges/…、/media/{id}
        del_hits = [m.group(0) for m in re.finditer(
            r"`[^`]*(?:/nodes/\$\{params\.node_id\}|/edges/\$\{params\.from_node\}|/media/\$\{params\.asset_id\})[^`]*`", src)]
        for url in del_hits:
            if url.endswith("${params.node_id}`"):
                continue  # get_node 的 GET URL（不带门）
            self.assertIn("gateQuery()", url,
                          f"#357：扩展对门控删除端点的 URL 缺 gateQuery()：{url}")

    def test_tui_confirm_uses_ffdialog(self):
        src = (ROOT / "static" / "js" / "canvas-agent-tui.js").read_text(encoding="utf-8")
        self.assertIn("ffDialog.ask", src,
                      "#357：确认卡必须走 ffDialog（与 #347 删除确认同一机制）")


if __name__ == "__main__":
    unittest.main()
