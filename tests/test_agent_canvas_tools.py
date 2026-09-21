"""Tests for agent canvas tools API endpoints (issue #119)."""
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore


class AgentCanvasToolsTests(unittest.TestCase):
    """Test the canvas tools API endpoints used by the Pi agent."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-canvas-tools-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

        # Create a test project with some nodes
        self.project = self.store.create_project("Canvas Tools Test")
        self.project_id = self.project["id"]
        self.store.apply_mutations(
            self.project_id,
            [
                {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt", "title": "Test Prompt", "text": "A glowing firefly", "x": 100, "y": 100}},
                {"op": "upsert_node", "node": {"id": "image-1", "type": "smart-image", "title": "Test Image", "x": 300, "y": 100}},
                {"op": "upsert_edge", "edge": {"id": "edge-1", "from": "prompt-1", "to": "image-1", "kind": "flow"}},
            ],
            base_revision=0,
        )

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_list_nodes(self):
        """canvas.list_nodes returns node summaries."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("nodes", data)
        self.assertEqual(data["count"], 2)

        # Check that each node has the required summary fields
        node_ids = {node["id"] for node in data["nodes"]}
        self.assertIn("prompt-1", node_ids)
        self.assertIn("image-1", node_ids)

        for node in data["nodes"]:
            self.assertIn("id", node)
            self.assertIn("type", node)
            self.assertIn("title", node)
            self.assertIn("x", node)
            self.assertIn("y", node)
            self.assertIn("incoming", node)
            self.assertIn("outgoing", node)
            self.assertIn("status", node)

    def test_list_nodes_includes_connections(self):
        """canvas.list_nodes includes connection info."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes")
        data = response.json()

        prompt_node = next(n for n in data["nodes"] if n["id"] == "prompt-1")
        image_node = next(n for n in data["nodes"] if n["id"] == "image-1")

        self.assertEqual(prompt_node["outgoing"], ["image-1"])
        self.assertEqual(image_node["incoming"], ["prompt-1"])

    def test_get_node(self):
        """canvas.get_node returns detailed node info."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes/prompt-1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], "prompt-1")
        self.assertEqual(data["type"], "smart-prompt")
        self.assertEqual(data["title"], "Test Prompt")
        self.assertEqual(data["text"], "A glowing firefly")

    def test_get_node_not_found(self):
        """canvas.get_node returns 404 for missing node."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes/nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_get_selection(self):
        """canvas.get_selection returns selected nodes (empty for agent)."""
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/selection")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("selected", data)
        self.assertEqual(data["selected"], [])

    def test_create_node(self):
        """canvas.create_node creates a new node."""
        new_node = {
            "id": "video-1",
            "type": "smart-video",
            "title": "Test Video",
            "x": 500,
            "y": 100,
        }
        response = self.client.post(
            f"/api/projects/{self.project_id}/canvas/nodes",
            json={"node": new_node},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["node_id"], "video-1")

        # Verify the node was created
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes/video-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "smart-video")

    def test_create_node_generates_id(self):
        """canvas.create_node generates an ID if not provided."""
        new_node = {
            "type": "smart-prompt",
            "title": "Auto ID",
        }
        response = self.client.post(
            f"/api/projects/{self.project_id}/canvas/nodes",
            json={"node": new_node},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["node_id"].startswith("node-"))

    def test_update_node(self):
        """canvas.update_node updates node properties."""
        response = self.client.patch(
            f"/api/projects/{self.project_id}/canvas/nodes/prompt-1",
            json={"title": "Updated Title", "text": "Updated text"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Verify the update
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes/prompt-1")
        self.assertEqual(response.json()["title"], "Updated Title")
        self.assertEqual(response.json()["text"], "Updated text")

    def test_delete_node(self):
        """canvas.delete_node deletes a node."""
        response = self.client.delete(f"/api/projects/{self.project_id}/canvas/nodes/prompt-1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Verify the node was deleted
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes/prompt-1")
        self.assertEqual(response.status_code, 404)

    def test_create_edge(self):
        """canvas.connect creates a connection."""
        # Create a new node to connect to
        self.client.post(
            f"/api/projects/{self.project_id}/canvas/nodes",
            json={"node": {"id": "agent-1", "type": "smart-agent", "title": "Agent"}},
        )

        edge = {
            "id": "edge-2",
            "from": "prompt-1",
            "to": "agent-1",
            "kind": "input",
        }
        response = self.client.post(
            f"/api/projects/{self.project_id}/canvas/edges",
            json={"edge": edge},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["edge_id"], "edge-2")

        # Verify the edge was created by checking list_nodes
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes")
        nodes = {n["id"]: n for n in response.json()["nodes"]}
        self.assertIn("agent-1", nodes["prompt-1"]["outgoing"])

    def test_delete_edge(self):
        """canvas.disconnect removes a connection."""
        response = self.client.delete(
            f"/api/projects/{self.project_id}/canvas/edges/prompt-1/image-1"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

        # Verify the edge was removed
        response = self.client.get(f"/api/projects/{self.project_id}/canvas/nodes")
        nodes = {n["id"]: n for n in response.json()["nodes"]}
        self.assertNotIn("image-1", nodes["prompt-1"]["outgoing"])

    def test_delete_edge_not_found(self):
        """canvas.disconnect returns 404 for missing edge."""
        response = self.client.delete(
            f"/api/projects/{self.project_id}/canvas/edges/nonexistent/nonexistent"
        )
        self.assertEqual(response.status_code, 404)

    def test_project_not_found(self):
        """Canvas tools return 404 for missing project."""
        response = self.client.get("/api/projects/nonexistent/canvas/nodes")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
