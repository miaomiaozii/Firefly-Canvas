"""Tests for agent viewport tools API endpoints (issue #124)."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore


class AgentViewportToolsTests(unittest.TestCase):
    """Test the viewport tools API endpoints used by the Pi agent."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-viewport-tools-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

        # Create a test project with a node
        self.project = self.store.create_project("Viewport Tools Test")
        self.project_id = self.project["id"]
        self.store.apply_mutations(
            self.project_id,
            [
                {"op": "upsert_node", "node": {"id": "node-1", "type": "smart-image", "title": "Test Node", "x": 100, "y": 200}},
            ],
            base_revision=0,
        )

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_focus_node(self):
        """viewport.focus scrolls to a node."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/viewport/focus",
            json={"node_id": "node-1"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["success"], True)
        self.assertEqual(data["node_id"], "node-1")

    def test_focus_node_not_found(self):
        """viewport.focus returns 404 for missing node."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/viewport/focus",
            json={"node_id": "nonexistent"},
        )
        self.assertEqual(response.status_code, 404)

    def test_focus_node_missing_node_id(self):
        """viewport.focus returns 400 when node_id is missing."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/viewport/focus",
            json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("node_id", response.json()["detail"].lower())

    def test_set_zoom(self):
        """viewport.zoom sets the zoom level."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/viewport/zoom",
            json={"level": 1.5},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["success"], True)
        self.assertEqual(data["level"], 1.5)

    def test_set_zoom_missing_level(self):
        """viewport.zoom returns 400 when level is missing."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/viewport/zoom",
            json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("level", response.json()["detail"].lower())

    def test_get_viewport_state(self):
        """viewport.state returns the current viewport state."""
        # Set some state
        self.client.post(
            f"/api/projects/{self.project_id}/viewport/focus",
            json={"node_id": "node-1"},
        )
        self.client.post(
            f"/api/projects/{self.project_id}/viewport/zoom",
            json={"level": 2.0},
        )

        # Get the state
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
            json={"node_id": "node-1"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
