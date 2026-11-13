"""#335: unknown agentCli must be rejected at create, never fall back to pi silently."""
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
from app.services.agent_host import agent_host_manager  # noqa: F401  (manager singleton shared with router)
from app.routers import api_agent_nodes


class TestAgentCliValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-cli-335-")
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.project_id = self.store.create_project("票335校验")["id"]
        self.store.apply_mutations(self.project_id, [
            {"op": "upsert_node", "node": {
                "id": "n-bogus", "type": "agent", "kind": "agent",
                "x": 0, "y": 0, "agentCli": "bogus-cli",
            }},
            {"op": "upsert_node", "node": {
                "id": "n-pi", "type": "agent", "kind": "agent",
                "x": 100, "y": 0, "agentCli": "pi",
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

    def _post(self, node_id):
        return self.client.post(
            f"/api/projects/{self.project_id}/agent-nodes",
            json={"node_id": node_id},
        )

    def test_unknown_agent_cli_rejected_400_before_spawn(self):
        with patch.object(api_agent_nodes.agent_host_manager, "get_or_create_session") as get_session:
            r = self._post("n-bogus")
        self.assertEqual(r.status_code, 400, r.text)
        body = r.json()
        detail = body.get("detail") or {}
        self.assertEqual(detail.get("error"), "unknown agent_cli")
        self.assertEqual(detail.get("agent_cli"), "bogus-cli")
        self.assertIn("pi", detail.get("supported", []))
        self.assertIn("codex", detail.get("supported", []))
        get_session.assert_not_called()

    def test_known_cli_still_creates(self):
        async def _fake_spawn(session, model="", autonomy=""):
            session.status = "running"
            return True

        with patch.object(api_agent_nodes.agent_host_manager, "spawn_session", staticmethod(_fake_spawn)):
            r = self._post("n-pi")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("success"))


if __name__ == "__main__":
    unittest.main()
