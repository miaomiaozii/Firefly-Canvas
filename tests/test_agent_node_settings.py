"""Tests for Agent node settings: model override (#140) and autonomy level (#141).

Uses the fake agent host (tests/fake_agent_host.py) to simulate pi --mode rpc
subprocess behavior without requiring the real pi CLI in CI.

Test coverage:
- Model override: node attribute persistence, spawn passthrough, runtime set_model
- Autonomy level: node attribute persistence, confirm gate scope per level
- Default behavior regression: nodes without model/autonomy attributes behave as before
"""
import asyncio
import json
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
from app.services.agent_host import AgentHostManager, agent_host_manager
from tests.fake_agent_host import FakeAgentProcess


def run_async(coro):
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestAgentModelOverride(unittest.TestCase):
    """Tests for #140: node-level model override."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-model-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.manager = AgentHostManager()

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_get_or_create_session_with_model(self):
        """get_or_create_session should accept and store model."""
        session = run_async(self.manager.get_or_create_session("proj-1", "node-1", model="vllm-remote/test-model"))
        self.assertEqual(session.model, "vllm-remote/test-model")

    def test_get_or_create_session_default_model(self):
        """get_or_create_session should default to empty model."""
        session = run_async(self.manager.get_or_create_session("proj-1", "node-1"))
        self.assertEqual(session.model, "")

    def test_spawn_session_with_model(self):
        """spawn_session should pass model to the fake process."""
        project = self.store.create_project("Model Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1", model="vllm-remote/test-model"))

        # Create fake process
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "starting"

        # Verify spawn_session would include model in command
        # (We can't easily test the actual command line, but we can verify the session has the model)
        self.assertEqual(session.model, "vllm-remote/test-model")

    def test_set_model_sends_rpc_command(self):
        """set_model should send the correct pi RPC set_model command."""
        project = self.store.create_project("Model Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))

        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Test with provider/model format
        run_async(self.manager.set_model(session, "vllm-remote/qwen3.8-27b"))
        cmd = fake.get_command("set_model")
        self.assertIsNotNone(cmd, "set_model command should have been sent")
        self.assertEqual(cmd["provider"], "vllm-remote")
        self.assertEqual(cmd["modelId"], "qwen3.8-27b")

        # Test with model-only format
        fake.reset_script([])
        run_async(self.manager.set_model(session, "gpt-4"))
        cmd = fake.get_command("set_model")
        self.assertIsNotNone(cmd, "set_model command should have been sent")
        self.assertEqual(cmd["modelId"], "gpt-4")
        self.assertNotIn("provider", cmd)

    def test_set_model_endpoint(self):
        """POST /model endpoint should update node attribute and send RPC command."""
        project = self.store.create_project("Model Endpoint Test")
        project_id = project["id"]
        node_id = "node-model-1"

        # Create a node on the canvas first
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Create a running session
        session = run_async(agent_host_manager.get_or_create_session(project_id, node_id))
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Call the /model endpoint
        response = self.client.post(
            f"/api/projects/{project_id}/agent-nodes/{node_id}/model",
            json={"model": "vllm-remote/test-model"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["model"], "vllm-remote/test-model")

        # Verify node attribute was updated
        opened = self.store.open_project(project_id)
        canvas = opened.get("canvas", {})
        node = next((n for n in canvas.get("nodes", []) if n.get("id") == node_id), None)
        self.assertIsNotNone(node)
        self.assertEqual(node.get("model"), "vllm-remote/test-model")

        # Verify RPC command was sent
        cmd = fake.get_command("set_model")
        self.assertIsNotNone(cmd, "set_model command should have been sent")
        self.assertEqual(cmd["provider"], "vllm-remote")
        self.assertEqual(cmd["modelId"], "test-model")

    def test_set_model_endpoint_not_running(self):
        """POST /model endpoint should update node attribute even if session is not running."""
        project = self.store.create_project("Model Endpoint Test 2")
        project_id = project["id"]
        node_id = "node-model-2"

        # Create a node on the canvas first
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Call the /model endpoint (no running session)
        response = self.client.post(
            f"/api/projects/{project_id}/agent-nodes/{node_id}/model",
            json={"model": "vllm-remote/test-model"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertFalse(data["is_running"])

        # Verify node attribute was updated
        opened = self.store.open_project(project_id)
        canvas = opened.get("canvas", {})
        node = next((n for n in canvas.get("nodes", []) if n.get("id") == node_id), None)
        self.assertIsNotNone(node)
        self.assertEqual(node.get("model"), "vllm-remote/test-model")

    def test_create_agent_node_reads_node_model(self):
        """create_agent_node should read model from node attributes if not provided."""
        project = self.store.create_project("Model Read Test")
        project_id = project["id"]
        node_id = "node-model-3"

        # Create a node with model attribute
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "model": "vllm-remote/node-model",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Create a running session (mock spawn to avoid real pi)
        session = run_async(agent_host_manager.get_or_create_session(project_id, node_id))
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Call create_agent_node without model (should read from node)
        response = self.client.post(
            f"/api/projects/{project_id}/agent-nodes/",
            json={"node_id": node_id},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["model"], "vllm-remote/node-model")


class TestAgentAutonomyLevel(unittest.TestCase):
    """Tests for #141: node-level autonomy level."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-autonomy-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.manager = AgentHostManager()

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_get_or_create_session_with_autonomy(self):
        """get_or_create_session should accept and store autonomy."""
        session = run_async(self.manager.get_or_create_session("proj-1", "node-1", autonomy="autonomous"))
        self.assertEqual(session.autonomy, "autonomous")

    def test_get_or_create_session_default_autonomy(self):
        """get_or_create_session should default to cautious."""
        session = run_async(self.manager.get_or_create_session("proj-1", "node-1"))
        self.assertEqual(session.autonomy, "cautious")

    def test_set_autonomy_endpoint(self):
        """POST /autonomy endpoint should update node attribute and session."""
        project = self.store.create_project("Autonomy Endpoint Test")
        project_id = project["id"]
        node_id = "node-auto-1"

        # Create a node on the canvas first
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Create a running session
        session = run_async(agent_host_manager.get_or_create_session(project_id, node_id))
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Call the /autonomy endpoint
        response = self.client.post(
            f"/api/projects/{project_id}/agent-nodes/{node_id}/autonomy",
            json={"autonomy": "autonomous"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["autonomy"], "autonomous")

        # Verify node attribute was updated
        opened = self.store.open_project(project_id)
        canvas = opened.get("canvas", {})
        node = next((n for n in canvas.get("nodes", []) if n.get("id") == node_id), None)
        self.assertIsNotNone(node)
        self.assertEqual(node.get("autonomy"), "autonomous")

        # Verify session autonomy was updated
        self.assertEqual(session.autonomy, "autonomous")

    def test_set_autonomy_invalid_level(self):
        """POST /autonomy endpoint should reject invalid levels."""
        project = self.store.create_project("Autonomy Invalid Test")
        project_id = project["id"]
        node_id = "node-auto-2"

        # Create a node on the canvas first
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        response = self.client.post(
            f"/api/projects/{project_id}/agent-nodes/{node_id}/autonomy",
            json={"autonomy": "invalid"},
        )
        self.assertEqual(response.status_code, 400)

    def test_confirm_autonomous_auto_approves(self):
        """In autonomous mode, destructive tools should be auto-approved."""
        project = self.store.create_project("Confirm Autonomous Test")
        project_id = project["id"]
        node_id = "node-auto-3"

        # Create a node on the canvas first
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "autonomy": "autonomous",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Create a running session with autonomous level
        session = run_async(agent_host_manager.get_or_create_session(project_id, node_id, autonomy="autonomous"))
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Call the confirm endpoint for canvas.delete_node
        response = self.client.post(
            f"/api/projects/{project_id}/agent/confirm",
            json={
                "node_id": node_id,
                "tool": "canvas.delete_node",
                "params": {"node_id": "some-node"},
                "message": "删除节点？",
                "timeout_seconds": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["approved"], "Autonomous mode should auto-approve")

    def test_confirm_cautious_waits_for_user(self):
        """In cautious mode, destructive tools should wait for user confirmation."""
        project = self.store.create_project("Confirm Cautious Test")
        project_id = project["id"]
        node_id = "node-auto-4"

        # Create a node on the canvas first
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Create a running session with cautious level (default)
        session = run_async(agent_host_manager.get_or_create_session(project_id, node_id, autonomy="cautious"))
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Call the confirm endpoint with a short timeout
        response = self.client.post(
            f"/api/projects/{project_id}/agent/confirm",
            json={
                "node_id": node_id,
                "tool": "canvas.delete_node",
                "params": {"node_id": "some-node"},
                "message": "删除节点？",
                "timeout_seconds": 0.5,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["approved"], "Cautious mode should wait for user")
        self.assertEqual(data["reason"], "timeout")

    def test_confirm_standard_waits_for_user(self):
        """In standard mode, destructive tools should wait for user confirmation."""
        project = self.store.create_project("Confirm Standard Test")
        project_id = project["id"]
        node_id = "node-auto-5"

        # Create a node on the canvas first
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Create a running session with standard level
        session = run_async(agent_host_manager.get_or_create_session(project_id, node_id, autonomy="standard"))
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Call the confirm endpoint with a short timeout
        response = self.client.post(
            f"/api/projects/{project_id}/agent/confirm",
            json={
                "node_id": node_id,
                "tool": "canvas.delete_node",
                "params": {"node_id": "some-node"},
                "message": "删除节点？",
                "timeout_seconds": 0.5,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["approved"], "Standard mode should wait for user")
        self.assertEqual(data["reason"], "timeout")

    def test_confirm_autonomous_media_delete(self):
        """In autonomous mode, media.delete should also be auto-approved."""
        project = self.store.create_project("Confirm Autonomous Media Test")
        project_id = project["id"]
        node_id = "node-auto-6"

        # Create a node on the canvas first
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "autonomy": "autonomous",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Create a running session with autonomous level
        session = run_async(agent_host_manager.get_or_create_session(project_id, node_id, autonomy="autonomous"))
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Call the confirm endpoint for media.delete
        response = self.client.post(
            f"/api/projects/{project_id}/agent/confirm",
            json={
                "node_id": node_id,
                "tool": "media.delete",
                "params": {"asset_id": "some-asset"},
                "message": "删除媒体？",
                "timeout_seconds": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["approved"], "Autonomous mode should auto-approve media.delete")

    def test_create_agent_node_reads_node_autonomy(self):
        """create_agent_node should read autonomy from node attributes if not provided."""
        project = self.store.create_project("Autonomy Read Test")
        project_id = project["id"]
        node_id = "node-auto-7"

        # Create a node with autonomy attribute
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "autonomy": "autonomous",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Create a running session (mock spawn to avoid real pi)
        session = run_async(agent_host_manager.get_or_create_session(project_id, node_id))
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Call create_agent_node without autonomy (should read from node)
        response = self.client.post(
            f"/api/projects/{project_id}/agent-nodes/",
            json={"node_id": node_id},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["autonomy"], "autonomous")


class TestDefaultBehaviorRegression(unittest.TestCase):
    """Tests that default behavior (no model/autonomy attributes) is unchanged."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-default-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.manager = AgentHostManager()

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_node_without_model_uses_default(self):
        """A node without a model attribute should use the default model."""
        session = run_async(self.manager.get_or_create_session("proj-1", "node-1"))
        self.assertEqual(session.model, "")

    def test_node_without_autonomy_uses_cautious(self):
        """A node without an autonomy attribute should default to cautious."""
        session = run_async(self.manager.get_or_create_session("proj-1", "node-1"))
        self.assertEqual(session.autonomy, "cautious")

    def test_confirm_without_autonomy_waits(self):
        """A node without autonomy attribute should wait for confirmation (cautious default)."""
        project = self.store.create_project("Default Confirm Test")
        project_id = project["id"]
        node_id = "node-default-1"

        # Create a node WITHOUT autonomy attribute
        self.store.apply_mutations(project_id, [{
            "op": "upsert_node",
            "node": {
                "id": node_id,
                "type": "smart-agent",
                "title": "Test Agent",
                "x": 0,
                "y": 0,
            }
        }], base_revision=0)

        # Create a running session
        session = run_async(agent_host_manager.get_or_create_session(project_id, node_id))
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Call the confirm endpoint with a short timeout
        response = self.client.post(
            f"/api/projects/{project_id}/agent/confirm",
            json={
                "node_id": node_id,
                "tool": "canvas.delete_node",
                "params": {"node_id": "some-node"},
                "message": "删除节点？",
                "timeout_seconds": 0.5,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["approved"], "Default (cautious) should wait for user")
        self.assertEqual(data["reason"], "timeout")


if __name__ == "__main__":
    unittest.main()
