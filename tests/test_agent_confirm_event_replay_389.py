"""#389：服务端确认门发出的 confirm 事件必须写入 agent 事件回放日志（events.jsonl）。

票面：确认挂起期间刷新页面，确认卡能重建并可批准/拒绝；确认结束后不再重放。
口径：
- request_confirmation_core 发 confirm 事件前先 _persist_event（就地注入 seq，
  live WS 与回放同一 payload——与 #318 票 C 单一 fan-out 口径一致）。
- 门结束（批准/拒绝/超时）写 confirm_resolved 记录并 live 广播——回放时前端据此
  关闭已决确认卡（「结束后不再重放」）。
- autonomy=autonomous 自动放行不发卡也不写回放（没有待决卡可重建）。
- 前端：applyAgentEventBody 消费 confirm_resolved（清 state.confirm + 静默关模态），
  ffDialog.close() 不 settle promise——调用方 state.confirm 守卫保证不误发决议。
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services.agent_host import agent_host_manager
from tests.fake_agent_host import FakeAgentProcess


class ConfirmEventReplay389Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-389-replay-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8125",
                                 headers={"origin": "http://127.0.0.1:8125"})
        project = self.store.create_project("Confirm Replay 389")
        self.pid = project["id"]
        self.node_id = "agent-389"
        # 真实（未 spawn）会话：/agent/confirm 端点走 get_or_create_session 复用。
        session = self._run(agent_host_manager.get_or_create_session(self.pid, self.node_id))
        session.process = FakeAgentProcess(script=[])
        session.status = "running"
        self.session = session
        self.captured = []

        async def capture(sess, event):
            self.captured.append(dict(event))

        agent_host_manager.on_event(session, capture)

    def tearDown(self):
        for handler in list(self.session.event_handlers):
            self.session.event_handlers.remove(handler)
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    @staticmethod
    def _run(coro):
        import asyncio
        return asyncio.run(coro)

    def _replay(self, since=0):
        r = self.client.get(
            f"/api/projects/{self.pid}/agent-nodes/{self.node_id}/events?since={since}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json().get("events", [])

    def _types(self, records):
        return [rec.get("event", {}).get("type") for rec in records]

    def _gate(self, tool="canvas.delete_node", timeout_seconds=5, autonomy="cautious"):
        self.session.autonomy = autonomy
        return self.client.post(
            f"/api/projects/{self.pid}/agent/confirm",
            json={"node_id": self.node_id, "tool": tool,
                  "params": {"node_id": "image-1"}, "message": "删除节点？",
                  "timeout_seconds": timeout_seconds},
        )

    # ── ① 确认挂起期间：confirm 事件已在回放日志里（刷新可重建）─────────
    def test_pending_confirm_is_persisted_and_replayable(self):
        resolved = {}

        async def capture_then_approve(sess, event):
            self.captured.append(dict(event))
            if event.get("type") == "confirm" and "approve" not in resolved:
                resolved["approve"] = True
                # 模拟「刷新后的新页面」：只靠回放日志+决议端点重建确认卡再批准。
                thread = threading.Thread(
                    target=lambda: self.client.post(
                        f"/api/projects/{self.pid}/agent-nodes/{self.node_id}/confirm",
                        json={"confirm_id": event["confirm_id"], "approved": True}))
                thread.start()
                self.addCleanup(thread.join, 5)

        self.session.event_handlers.clear()
        agent_host_manager.on_event(self.session, capture_then_approve)

        r = self._gate()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("approved"), f"批准应送达门: {body}")

        records = self._replay()
        types = self._types(records)
        self.assertIn("confirm", types, "confirm 事件必须写入 events.jsonl（票面核心）")
        self.assertIn("confirm_resolved", types, "确认结束必须落终态记录（不再重放）")
        self.assertLess(types.index("confirm"), types.index("confirm_resolved"))
        confirm_rec = records[types.index("confirm")]
        self.assertTrue(confirm_rec.get("seq"), "回放记录必须带 seq")
        self.assertTrue(confirm_rec["event"].get("seq"), "event payload 必须带 seq（live/回放同链）")
        resolved_ev = records[types.index("confirm_resolved")]["event"]
        self.assertEqual(resolved_ev.get("confirm_id"), confirm_rec["event"]["confirm_id"])
        self.assertTrue(resolved_ev.get("approved"))
        # live WS 也收到两条（confirm + confirm_resolved）
        self.assertIn("confirm_resolved", [e.get("type") for e in self.captured])

    # ── ② 超时：门返回 timeout 且 confirm_resolved(reason=timeout) 落日志 ──
    def test_timeout_persists_resolution(self):
        r = self._gate(timeout_seconds=0.4)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body.get("approved"))
        self.assertEqual(body.get("reason"), "timeout")
        records = self._replay()
        types = self._types(records)
        self.assertIn("confirm", types)
        idx = types.index("confirm_resolved")
        self.assertGreater(idx, -1, "超时也必须落终态记录")
        self.assertEqual(records[idx]["event"].get("reason"), "timeout")

    # ── ③ autonomous 自动放行：不发卡、不写回放 ─────────────────────────
    def test_autonomous_autoapprove_writes_nothing(self):
        r = self._gate(tool="canvas.edit_crop", autonomy="autonomous")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("approved"))
        self.assertEqual(self._replay(), [], "自动放行不得留下待决卡记录")

    # ── ④ 前端契约钉（源码级）：confirm_resolved 消费 + 静默关模态 ──────
    def test_frontend_consumes_resolution(self):
        tui = (ROOT / "static" / "js" / "canvas-agent-tui.js").read_text(encoding="utf-8")
        self.assertIn("case 'confirm_resolved'", tui,
                      "#389：回放/直播都必须消费终态记录以关闭已决确认卡")
        self.assertIn("state.confirm = null", tui)
        self.assertIn("ffDialog.close", tui, "已决确认卡必须能从模态上关掉（不触发决议）")
        dialog = (ROOT / "static" / "js" / "ui-dialog.js").read_text(encoding="utf-8")
        self.assertIn("close:", dialog, "#389：ffDialog 需提供不 settle 的 close()")


if __name__ == "__main__":
    unittest.main()
