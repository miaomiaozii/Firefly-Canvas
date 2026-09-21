"""Tests for agent generate tools API endpoints (issue #121)."""
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


class AgentGenerateToolsTests(unittest.TestCase):
    """Test the generate tools API endpoints used by the Pi agent."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-generate-tools-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

        # Create a test project with an image node
        self.project = self.store.create_project("Generate Tools Test")
        self.project_id = self.project["id"]
        self.store.apply_mutations(
            self.project_id,
            [
                {"op": "upsert_node", "node": {"id": "image-1", "type": "smart-image", "title": "Test Image", "prompt": "A glowing firefly", "x": 100, "y": 100}},
            ],
            base_revision=0,
        )

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_trigger_generation(self):
        """generate.trigger triggers generation on a node."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/generate/trigger",
            json={"node_id": "image-1"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("task_id", data)
        self.assertEqual(data["type"], "image")
        self.assertEqual(data["node_id"], "image-1")
        self.assertTrue(data["task_id"].startswith("agent_img_"))

    def test_trigger_generation_node_not_found(self):
        """generate.trigger returns 404 for missing node."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/generate/trigger",
            json={"node_id": "nonexistent"},
        )
        self.assertEqual(response.status_code, 404)

    def test_trigger_generation_no_prompt(self):
        """generate.trigger returns 400 for node without prompt."""
        # Create a node without a prompt
        self.store.apply_mutations(
            self.project_id,
            [{"op": "upsert_node", "node": {"id": "image-2", "type": "smart-image", "title": "No Prompt", "x": 200, "y": 200}}],
            base_revision=1,
        )
        response = self.client.post(
            f"/api/projects/{self.project_id}/generate/trigger",
            json={"node_id": "image-2"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("no prompt", response.json()["detail"].lower())

    def test_trigger_generation_missing_node_id(self):
        """generate.trigger returns 400 when node_id is missing."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/generate/trigger",
            json={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("node_id", response.json()["detail"].lower())

    def test_get_task_status_not_found(self):
        """generate.status returns 404 for unknown task."""
        response = self.client.get(
            f"/api/projects/{self.project_id}/generate/tasks/nonexistent"
        )
        self.assertEqual(response.status_code, 404)

    def test_wait_for_task_not_found(self):
        """generate.wait returns timeout for unknown task."""
        response = self.client.get(
            f"/api/projects/{self.project_id}/generate/tasks/nonexistent/wait?timeout=1"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "timeout")
        self.assertIn("not found", data["error"].lower())

    def test_project_not_found(self):
        """Generate tools return 404 for missing project."""
        response = self.client.post(
            "/api/projects/nonexistent/generate/trigger",
            json={"node_id": "image-1"},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
