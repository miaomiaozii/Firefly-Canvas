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
