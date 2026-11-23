"""#389：服务端确认门发出的 confirm 事件必须写入 agent 事件回放日志（events.jsonl）。

票面：确认挂起期间刷新页面，确认卡能重建并可批准/拒绝；确认结束后不再重放。
口径：
- request_confirmation_core 发 confirm 事件前先 _persist_event（就地注入 seq，
  live WS 与回放同一 payload——与 #318 票 C 单一 fan-out 口径一致）。
- 门结束（批准/拒绝/超时）写 confirm_resolved 记录并 live 广播——回放时前端据此
  关闭已决确认卡（「结束后不再重放」）。
- autonomy=autonomous 自动放行不发卡也不写回放（没有待决卡可重建）。
- 前端：confirm_resolved 只移除匹配的门；最后一张撤卡并静默关模态，
  独立未决门仍可操作。服务端终态不是用户拒绝，不得触发新的决议（#389、#487）。
"""
import json
import shutil
import subprocess
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
from app.constants import NODE_TYPE_AGENT
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
        self.client.__enter__()
        project = self.store.create_project("Confirm Replay 389")
        self.pid = project["id"]
        self.node_id = "agent-389"
        # 未指定 host 的回放按持久画布节点选择活跃宿主；夹具只创建节点，
        # 不伪造客户端无权写入的原生会话绑定，也不跳过服务端的宿主选择。
        self.store.apply_mutations(
            self.pid,
            [{"op": "upsert_node", "node": {
                "id": self.node_id, "type": NODE_TYPE_AGENT, "kind": "agent",
                "title": "Confirm Replay Agent", "x": 0, "y": 0,
            }}],
            base_revision=0,
        )
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
        self.client.__exit__(None, None, None)
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

    def test_wait_window_keeps_pending_replay_until_late_resolution(self):
        r = self._gate(timeout_seconds=0.4)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body.get("approved"))
        self.assertEqual(body.get("reason"), "pending")
        self.assertTrue(body.get('confirm_id'))
        records = self._replay()
        self.assertIn('confirm', self._types(records))
        self.assertNotIn('confirm_resolved', self._types(records))
        self.assertTrue(self.session.confirm_handlers)
        response = self.client.post(f'/api/projects/{self.pid}/agent-nodes/{self.node_id}/confirm',
                                    json={'confirm_id': body['confirm_id'], 'approved': False})
        self.assertEqual(response.status_code, 200)
        import time
        deadline = time.monotonic() + 2
        while self.session.confirm_handlers and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.session.confirm_handlers)
        resolved = [r['event'] for r in self._replay() if r['event']['type'] == 'confirm_resolved']
        self.assertEqual(resolved[-1]['reason'], 'rejected')

    # ── ③ autonomous 自动放行：不发卡、不写回放 ─────────────────────────
    def test_autonomous_autoapprove_writes_nothing(self):
        r = self._gate(tool="canvas.edit_crop", autonomy="autonomous")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("approved"))
        self.assertEqual(self._replay(), [], "自动放行不得留下待决卡记录")

    def test_frontend_consumes_resolution(self):
        # A blanket-null source assertion loses independent gates; require the public event behavior.
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for the production TUI regression")
        result = subprocess.run(
            [node, "--test", "--test-reporter=tap",
             "tests/canvas_module_units/test-agent-batch-confirm-487.mjs"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("server resolution retires the last confirmation without sending a decision",
                      result.stdout)


if __name__ == "__main__":
    unittest.main()
