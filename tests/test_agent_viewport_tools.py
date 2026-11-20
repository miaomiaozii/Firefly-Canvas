"""Tests for agent viewport tools API endpoints (issue #124)."""
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from app.routers import api_agent_tools
from firefly_projects import ProjectStore


class AgentViewportToolsTests(unittest.TestCase):
    """Test the viewport tools API endpoints used by the Pi agent."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-viewport-tools-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

        self.project_id = self.store.create_project("Viewport Tools Test")["id"]
        self.store.apply_mutations(
            self.project_id,
            [
                {"op": "upsert_node", "node": {"id": "node-1", "type": "smart-image", "title": "Test Node", "x": 100, "y": 200}},
            ],
            base_revision=0,
        )

    def tearDown(self):
        api_agent_tools.VIEWPORT_STATE.pop(self.project_id, None)
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def _post_viewport(self, action, payload):
        return self.client.post(
            f"/api/projects/{self.project_id}/viewport/{action}",
            headers={"X-Agent-Project": self.project_id},
            json=payload,
        )

    def test_focus_node(self):
        """viewport.focus scrolls to a node."""
        response = self._post_viewport("focus", {"node_id": "node-1"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["success"], True)
        self.assertEqual(data["node_id"], "node-1")

    def test_focus_node_not_found(self):
        """viewport.focus returns 404 for missing node."""
        response = self._post_viewport("focus", {"node_id": "nonexistent"})
        self.assertEqual(response.status_code, 404, response.text)

    def test_focus_node_missing_node_id(self):
        """viewport.focus returns 400 when node_id is missing."""
        response = self._post_viewport("focus", {})
        self.assertEqual(response.status_code, 400, response.text)
        # #391：detail 统一形状；code=INVALID_ARGUMENT，参数名进 message/hint
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "INVALID_ARGUMENT")
        self.assertIn("node_id", detail["message"] + detail["hint"])

    def test_set_zoom(self):
        """viewport.zoom sets the zoom level."""
        response = self._post_viewport("zoom", {"level": 1.5})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["success"], True)
        self.assertEqual(data["level"], 1.5)

    def test_set_zoom_missing_level(self):
        """viewport.zoom returns 400 when level is missing."""
        response = self._post_viewport("zoom", {})
        self.assertEqual(response.status_code, 400, response.text)
        # #391：detail 统一形状
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "INVALID_ARGUMENT")
        self.assertIn("level", detail["message"] + detail["hint"])

    def test_get_viewport_state(self):
        """viewport.state returns the current viewport state."""
        focus = self._post_viewport("focus", {"node_id": "node-1"})
        self.assertEqual(focus.status_code, 200, focus.text)
        zoom = self._post_viewport("zoom", {"level": 2.0})
        self.assertEqual(zoom.status_code, 200, zoom.text)

        response = self.client.get(
            f"/api/projects/{self.project_id}/viewport/state"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["focus_node_id"], "node-1")
        self.assertEqual(data["scale"], 2.0)

    def test_project_not_found(self):
        """Viewport tools return 404 for missing project."""
        response = self.client.post(
            "/api/projects/nonexistent/viewport/focus",
            headers={"X-Agent-Project": "nonexistent"},
            json={"node_id": "node-1"},
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"]["code"], "PROJECT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
