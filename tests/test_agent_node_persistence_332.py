"""#332: POST agent-nodes must persist the node into canvas.json (no evaporation on restart)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.routers import api_agent_nodes


class TestAgentNodePersistence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-332-")
        self.root = Path(self.temp.name) / "projects"
        self.store = ProjectStore(self.root, snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.project_id = self.store.create_project("票332持久化")["id"]
        self.client = TestClient(main.app, base_url="http://127.0.0.1",
                                 headers={"origin": "http://127.0.0.1"})

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp.cleanup()

    async def _fake_spawn(self, session, model="", autonomy=""):
        session.status = "running"
        return True

    def _post(self, node_id, **extra):
        return self.client.post(
            f"/api/projects/{self.project_id}/agent-nodes",
            json={"node_id": node_id, **extra},
        )

    def test_phantom_node_persisted_and_survives_reload(self):
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("ghost-1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("success"))
        canvas = self.store.open_project(self.project_id)["canvas"]
        node = next((n for n in canvas["nodes"] if n.get("id") == "ghost-1"), None)
        self.assertIsNotNone(node, "agent-nodes create must upsert into canvas.json")
        self.assertEqual(node.get("type"), "smart-agent")
        # 磁盘级：flush+新建 store 重载后仍在（=进程重启语义）
        self.store.flush(self.project_id)
        reopened = ProjectStore(self.root, snapshot_delay=0).open_project(self.project_id)
        self.assertTrue(any(n.get("id") == "ghost-1" for n in reopened["canvas"]["nodes"]))

    def test_existing_node_untouched(self):
        self.store.apply_mutations(self.project_id, [{
            "op": "upsert_node",
            "node": {"id": "keep-1", "type": "smart-agent", "kind": "agent", "x": 42, "y": 7,
                     "title": "我的卡", "agentCli": "pi", "custom": "keepme"},
        }], base_revision=0)
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("keep-1")
        self.assertEqual(r.status_code, 200, r.text)
        canvas = self.store.open_project(self.project_id)["canvas"]
        node = next(n for n in canvas["nodes"] if n.get("id") == "keep-1")
        self.assertEqual(node.get("x"), 42)
        self.assertEqual(node.get("custom"), "keepme")
        self.assertEqual(node.get("title"), "我的卡")

    def test_response_echoes_agent_cli(self):
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("echo-1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("agentCli"), "pi")

    def test_body_agent_cli_conflicts_with_node_attr_400(self):
        # 评审修正：已有节点属性=pi，body 带 codex → 400，不得静默无视/谎回显。
        self.store.apply_mutations(self.project_id, [{
            "op": "upsert_node",
            "node": {"id": "cx-conf", "type": "smart-agent", "kind": "agent", "x": 0, "y": 0, "agentCli": "pi"},
        }], base_revision=0)
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("cx-conf", agentCli="codex")
        self.assertEqual(r.status_code, 400, r.text)
        detail = (r.json().get("detail") or {})
        self.assertEqual(detail.get("node_agent_cli"), "pi")
        self.assertEqual(detail.get("body_agent_cli"), "codex")

    def test_unknown_project_404_not_masked(self):
        # 评审修正：项目不存在必须 404 原样上报（不得被 upsert 通道吞成 409）。
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self.client.post("/api/projects/no-such-proj/agent-nodes", json={"node_id": "x-1"})
        self.assertIn(r.status_code, (404, 422), r.text)
        self.assertNotEqual(r.status_code, 409)

    def test_echo_reflects_actual_session_host(self):
        # 评审修正：回显=会话实际宿主，不是请求值。
        self.store.apply_mutations(self.project_id, [{
            "op": "upsert_node",
            "node": {"id": "echo-host", "type": "smart-agent", "kind": "agent", "x": 0, "y": 0, "agentCli": "pi"},
        }], base_revision=0)
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("echo-host")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("agentCli"), "pi")

    def test_phantom_nodes_grid_placement_not_stacked(self):
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            self._post("grid-1")
            self._post("grid-2")
        canvas = self.store.open_project(self.project_id)["canvas"]
        pos = [(n.get("x"), n.get("y")) for n in canvas["nodes"]]
        self.assertEqual(len(set(pos)), len(pos), "phantom 节点必须错位摆放")

    def test_body_agent_cli_codex_persisted_and_unknown_400(self):
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            r = self._post("cx-1", agentCli="codex")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("agentCli"), "codex")
        canvas = self.store.open_project(self.project_id)["canvas"]
        node = next(n for n in canvas["nodes"] if n.get("id") == "cx-1")
        self.assertEqual(node.get("agentCli"), "codex")
        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(self._fake_spawn)):
            bad = self._post("bad-1", agentCli="hacker")
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
