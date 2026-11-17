"""#388: POST /agent-nodes/{id}/prompt 对不存在节点必须 404，不创建会话、不 spawn。

票面（GitHub #388）：期望 404 且前后进程计数不变；已存在节点行为不变。
单测层用 mock 证明 get_or_create_session / spawn_session 从未被调用（等价于
「不创建会话、不 spawn 进程」）；live 进程计数复测见交付 post。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.routers import api_agent_nodes
from app.services.agent_host import AgentSession


class TestPromptNode404_388(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-388-")
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.project_id = self.store.create_project("票388幽灵节点")["id"]
        self.store.apply_mutations(self.project_id, [
            {"op": "upsert_node", "node": {
                "id": "agent-real", "type": "agent", "kind": "agent",
                "x": 0, "y": 0, "agentCli": "pi",
            }},
        ], base_revision=0)
        self.store.flush(self.project_id)
        self.client = TestClient(main.app, base_url="http://127.0.0.1",
                                 headers={"origin": "http://127.0.0.1"})

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp.cleanup()

    def _prompt(self, node_id):
        return self.client.post(
            f"/api/projects/{self.project_id}/agent-nodes/{node_id}/prompt",
            json={"message": "hello"},
        )

    def test_prompt_missing_node_404_no_session_no_spawn(self):
        with patch.object(api_agent_nodes.agent_host_manager,
                          "get_or_create_session") as goc, \
             patch.object(api_agent_nodes.agent_host_manager,
                          "spawn_session") as spawn:
            r = self._prompt("agent-ghost")
        self.assertEqual(r.status_code, 404, r.text)
        goc.assert_not_called()
        spawn.assert_not_called()

    def test_prompt_existing_node_unchanged(self):
        session = AgentSession(
            project_id=self.project_id, node_id="agent-real", status="running",
            process=Mock(poll=Mock(return_value=None)),
        )

        async def _fake_goc(*a, **k):
            return session

        async def _fake_send(session, message, images):
            return None

        with patch.object(api_agent_nodes.agent_host_manager,
                          "get_or_create_session", _fake_goc), \
             patch.object(api_agent_nodes.agent_host_manager,
                          "spawn_session") as spawn, \
             patch.object(api_agent_nodes.agent_host_manager,
                          "send_prompt", _fake_send):
            r = self._prompt("agent-real")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("success"))
        spawn.assert_not_called()  # 已 running 不再 spawn（原语义保持）

    def test_ws_prompt_ghost_node_no_spawn(self):
        # #388 补充（review 裁定）：WS 兜底 prompt 路径同样不得为不存在节点 spawn。
        session = AgentSession(
            project_id=self.project_id, node_id="ghost-ws",
            session_id="s", ext_session_id="",
        )

        async def _fake_goc(*a, **k):
            return session

        with patch.object(api_agent_nodes.agent_host_manager,
                          "get_or_create_session", _fake_goc), \
             patch.object(api_agent_nodes.agent_host_manager,
                          "spawn_session") as spawn, \
             patch.object(api_agent_nodes.agent_host_manager,
                          "send_prompt") as send:
            with self.client.websocket_connect(
                    f"/api/projects/{self.project_id}/agent-nodes/ghost-ws/stream",
                    headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}) as ws:
                ws.receive_json()  # session 首帧
                ws.send_json({"type": "prompt", "message": "hello"})
                msg = ws.receive_json()
        self.assertEqual(msg.get("type"), "error", msg)
        spawn.assert_not_called()
        send.assert_not_called()

    def test_ws_prompt_existing_agent_node_still_spawns(self):
        # #317 语义保持：canvas 上有 agent 节点且会话未 running → WS 兜底仍可拉起。
        session = AgentSession(
            project_id=self.project_id, node_id="agent-real",
            session_id="s", ext_session_id="",
        )

        calls = []

        async def _fake_goc(*a, **k):
            return session

        async def _fake_spawn(session):
            calls.append("spawn")
            return True

        async def _fake_send(session, message, images):
            calls.append("send")

        with patch.object(api_agent_nodes.agent_host_manager,
                          "get_or_create_session", _fake_goc), \
             patch.object(api_agent_nodes.agent_host_manager,
                          "spawn_session", _fake_spawn), \
             patch.object(api_agent_nodes.agent_host_manager,
                          "send_prompt", _fake_send):
            with self.client.websocket_connect(
                    f"/api/projects/{self.project_id}/agent-nodes/agent-real/stream",
                    headers={"origin": "http://127.0.0.1", "host": "127.0.0.1"}) as ws:
                ws.receive_json()
                ws.send_json({"type": "prompt", "message": "hello"})
                ws.close()
        self.assertEqual(calls, ["spawn", "send"])


if __name__ == "__main__":
    unittest.main()
