"""Tests for agent generate/media/viewport tools API endpoints (issue #123)."""
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
from app.core import CANVAS_TASKS, CANVAS_TASK_LOCK
from firefly_projects import ProjectStore


class AgentGenerateToolsTests(unittest.TestCase):
    """Test the generate/media/viewport tools API endpoints used by the Pi agent."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-generate-tools-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

        # Create a test project with nodes
        self.project = self.store.create_project("Generate Tools Test")
        self.project_id = self.project["id"]
        self.store.apply_mutations(
            self.project_id,
            [
                {"op": "upsert_node", "node": {"id": "prompt-1", "type": "smart-prompt", "title": "Test Prompt", "text": "A glowing firefly", "x": 100, "y": 100}},
                {"op": "upsert_node", "node": {"id": "image-1", "type": "smart-image", "title": "Test Image", "prompt": "A beautiful landscape", "x": 300, "y": 100}},
                {"op": "upsert_edge", "edge": {"id": "edge-1", "from": "prompt-1", "to": "image-1", "kind": "flow"}},
            ],
            base_revision=0,
        )

    def tearDown(self):
        # Clean up any tasks created during tests
        with CANVAS_TASK_LOCK:
            for task_id in list(CANVAS_TASKS.keys()):
                if CANVAS_TASKS[task_id].get("project_id") == self.project_id:
                    del CANVAS_TASKS[task_id]
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_trigger_generation(self):
        """generate.trigger starts generation and returns task ID."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/generate/trigger",
            json={"node_id": "image-1"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("task_id", data)
        self.assertTrue(data["task_id"].startswith("agent_img_"))
        self.assertEqual(data["type"], "image")
        self.assertEqual(data["node_id"], "image-1")

    def test_trigger_generation_uses_model_routing(self):
        """#134: generate.trigger routes through model routing (ADR 0006), not a hardcoded provider/size."""
        from unittest import mock
        import main as main_mod
        from firefly_generation import GenerationRoute

        captured = {}

        def fake_route(request, providers, *, provider_id="", model=""):
            captured['request'] = request
            captured['provider_id'] = provider_id
            captured['model'] = model
            return GenerationRoute(
                provider_id='routed-provider',
                provider_name='Routed',
                model='routed-model',
                protocol='openai',
                adapter='http',
                reason='test',
                request=request.to_dict(),
            )

        # Node with non-default generation params to prove they are not hardcoded.
        revision = int(self.store.open_project(self.project_id)["canvas"].get("revision") or 0)
        self.store.apply_mutations(
            self.project_id,
            [{"op": "upsert_node", "node": {"id": "img-routed", "type": "smart-image", "prompt": "a test subject", "x": 0, "y": 0, "generation_size": "2048x2048", "aspect_ratio": "16:9", "generation_resolution": "2k"}}],
            base_revision=revision,
        )

        with mock.patch.object(main_mod, 'route_image_generation', side_effect=fake_route):
            response = self.client.post(
                f"/api/projects/{self.project_id}/generate/trigger",
                json={"node_id": "img-routed"},
            )
        self.assertEqual(response.status_code, 200)

        # Auto routing: no pinned provider/model.
        self.assertEqual(captured['provider_id'], "")
        self.assertEqual(captured['model'], "")
        # The node's generation parameters were passed to the router (not hardcoded).
        req = captured['request']
        self.assertEqual(req.size, "2048x2048")
        self.assertEqual(req.aspect_ratio, "16:9")
        self.assertEqual(req.resolution, "2k")
        self.assertEqual(req.prompt, "a test subject")

    def test_trigger_generation_no_prompt(self):
        """generate.trigger returns 400 if node has no prompt."""
        # Create a node with no prompt or text
        self.store.apply_mutations(
            self.project_id,
            [{"op": "upsert_node", "node": {"id": "empty-1", "type": "smart-image", "x": 0, "y": 0}}],
            base_revision=1,
        )
        response = self.client.post(
            f"/api/projects/{self.project_id}/generate/trigger",
            json={"node_id": "empty-1"},
        )
        self.assertEqual(response.status_code, 400)

    def test_trigger_generation_node_not_found(self):
        """generate.trigger returns 404 for missing node."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/generate/trigger",
            json={"node_id": "nonexistent"},
        )
        self.assertEqual(response.status_code, 404)

    def test_get_task_status(self):
        """generate.status returns task status."""
        # First trigger a generation
        response = self.client.post(
            f"/api/projects/{self.project_id}/generate/trigger",
            json={"node_id": "image-1"},
        )
        task_id = response.json()["task_id"]

        # Query the status
        response = self.client.get(f"/api/projects/{self.project_id}/generate/tasks/{task_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertIn("status", data)
        self.assertIn(data["status"], ["queued", "running", "succeeded", "failed"])

    def test_get_task_status_not_found(self):
        """generate.status returns 404 for missing task."""
        response = self.client.get(f"/api/projects/{self.project_id}/generate/tasks/nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_wait_for_task(self):
        """generate.wait waits for task completion or timeout."""
        # First trigger a generation
        response = self.client.post(
            f"/api/projects/{self.project_id}/generate/trigger",
            json={"node_id": "image-1"},
        )
        task_id = response.json()["task_id"]

        # Wait with a short timeout
        response = self.client.get(
            f"/api/projects/{self.project_id}/generate/tasks/{task_id}/wait?timeout=5"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertIn("status", data)

    def test_wait_for_task_timeout(self):
        """generate.wait returns timeout status if task doesn't complete."""
        # Create a task that will never complete
        task_id = "agent_img_test_timeout"
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id] = {
                "id": task_id,
                "type": "agent-image",
                "status": "running",
                "project_id": self.project_id,
                "node_id": "image-1",
                "created_at": time.time(),
                "updated_at": time.time(),
                "result": None,
                "error": "",
            }

        # Wait with a very short timeout
        response = self.client.get(
            f"/api/projects/{self.project_id}/generate/tasks/{task_id}/wait?timeout=2"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertEqual(data["status"], "timeout")

    def test_media_upload(self):
        """media.upload uploads a media file."""
        # Create a temporary image file
        import os
        temp_image = os.path.join(self.temp_dir.name, "test_image.png")
        with open(temp_image, "wb") as f:
            # Write a minimal valid PNG
            f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01')

        response = self.client.post(
            f"/api/projects/{self.project_id}/media/upload",
            json={"file_path": temp_image},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("asset_id", data)

    def test_media_upload_file_not_found(self):
        """media.upload returns 404 for missing file."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/media/upload",
            json={"file_path": "/nonexistent/file.png"},
        )
        self.assertEqual(response.status_code, 404)

    def test_media_list(self):
        """media.list lists project media."""
        response = self.client.get(f"/api/projects/{self.project_id}/media/list")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("items", data)

    def test_viewport_focus(self):
        """viewport.focus focuses on a node."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/viewport/focus",
            json={"node_id": "image-1"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["node_id"], "image-1")

    def test_viewport_focus_not_found(self):
        """viewport.focus returns 404 for missing node."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/viewport/focus",
            json={"node_id": "nonexistent"},
        )
        self.assertEqual(response.status_code, 404)

    def test_viewport_zoom(self):
        """viewport.zoom sets zoom level."""
        response = self.client.post(
            f"/api/projects/{self.project_id}/viewport/zoom",
            json={"level": 1.5},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["level"], 1.5)

    def test_viewport_state(self):
        """viewport.state returns current viewport state."""
        # First set some state
        self.client.post(
            f"/api/projects/{self.project_id}/viewport/focus",
            json={"node_id": "image-1"},
        )
        self.client.post(
            f"/api/projects/{self.project_id}/viewport/zoom",
            json={"level": 2.0},
        )

        # Get the state
        response = self.client.get(f"/api/projects/{self.project_id}/viewport/state")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["focus_node_id"], "image-1")
        self.assertEqual(data["scale"], 2.0)


if __name__ == "__main__":
    unittest.main()
