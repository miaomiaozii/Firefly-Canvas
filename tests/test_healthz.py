"""Healthz runtime projection tests: stable shape + faithful session/agent reporting."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main
from app.project_sessions import (
    open_project_ids,
    register_project_session,
    unregister_project_session,
)
from app.services.agent_host import AGENT_STATUSES, AgentSession, agent_host_manager
from firefly_projects import ProjectStore


class HealthzTests(unittest.TestCase):
    """/api/healthz must expose a stable read-only runtime projection."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = ProjectStore(
            Path(self.temp_dir.name) / "projects", snapshot_delay=0
        )
        self.client = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            headers={"origin": "http://127.0.0.1"},
        )
        self._tokens = []
        self.previous_agent_sessions = dict(agent_host_manager.sessions)

    def tearDown(self):
        for token in self._tokens:
            unregister_project_session(token)
        self.client.close()
        agent_host_manager.sessions.clear()
        agent_host_manager.sessions.update(self.previous_agent_sessions)
        main.FIREFLY_PROJECT_STORE.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def _open_session_token(self, project_id):
        token = object()
        self._tokens.append(token)
        register_project_session(token, project_id)
        return token

    def _healthz(self):
        response = self.client.get("/api/healthz")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_healthz_returns_stable_shape(self):
        body = self._healthz()
        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body["open_project_count"], int)
        self.assertIsInstance(body["open_project_ids"], list)
        self.assertEqual(body["open_project_count"], len(body["open_project_ids"]))
        agents = body["agents"]
        self.assertIsInstance(agents["total"], int)
        self.assertIsInstance(agents["by_status"], dict)
        self.assertIsInstance(agents["sessions"], list)
        self.assertEqual(agents["total"], len(agents["sessions"]))
        for status in AGENT_STATUSES:
            self.assertIn(status, agents["by_status"])
        self.assertEqual(sum(agents["by_status"].values()), agents["total"])
        for entry in agents["sessions"]:
            self.assertEqual(
                set(entry),
                {"project_id", "node_id", "status", "model", "autonomy", "alive"},
            )

    def test_healthz_reports_open_project_sessions(self):
        self._open_session_token("proj-a")
        self._open_session_token("proj-b")
        # A second transport on the same project must not double-count.
        self._open_session_token("proj-a")
        body = self._healthz()
        self.assertEqual(body["open_project_count"], 2)
        self.assertEqual(body["open_project_ids"], ["proj-a", "proj-b"])
        for token in self._tokens:
            unregister_project_session(token)
        self._tokens.clear()
        body = self._healthz()
        self.assertEqual(body["open_project_count"], 0)
        self.assertEqual(body["open_project_ids"], [])

    def test_healthz_reports_agent_session_summary(self):
        session = AgentSession(
            project_id="proj-a",
            node_id="node-1",
            status="running",
            model="provider/model-x",
            autonomy="standard",
        )
        agent_host_manager.sessions["proj-a:node-1"] = session
        body = self._healthz()
        agents = body["agents"]
        self.assertEqual(agents["total"], 1)
        self.assertEqual(agents["by_status"]["running"], 1)
        self.assertEqual(agents["by_status"]["crashed"], 0)
        self.assertEqual(agents["sessions"], [
            {
                "project_id": "proj-a",
                "node_id": "node-1",
                "status": "running",
                "model": "provider/model-x",
                "autonomy": "standard",
                "alive": False,
            }
        ])

    def test_open_project_ids_is_a_sorted_unique_projection(self):
        self._open_session_token("proj-b")
        self._open_session_token("proj-a")
        self._open_session_token("proj-a")
        self.assertEqual(open_project_ids(), ["proj-a", "proj-b"])


if __name__ == "__main__":
    unittest.main()
