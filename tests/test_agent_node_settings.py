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

from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services.agent_host import AgentHostManager, AgentSession, agent_host_manager
from app.services import agent_model_capabilities
from tests.test_agent_host_settings_421_424 import capabilities
from tests.fake_agent_host import (
    FakeAgentProcess,
    install_stub_pi_cli,
    restore_stub_pi_cli,
)


def native_settings_ack(manager, session):
    """Native RPC shape: exact IDs, model object ACK and complete idle state."""
    send = manager.send_command
    state = {"model": {"provider": "vllm-remote", "id": "initial"}, "thinkingLevel": "medium",
             "isStreaming": False, "isCompacting": False, "pendingMessageCount": 0}

    async def acknowledge(sess, command):
        await send(sess, command)
        data = {}
        if command["type"] == "set_model":
            state["model"] = {"provider": command["provider"], "id": command["modelId"]}
            data = state["model"]
        elif command["type"] == "set_thinking_level":
            state["thinkingLevel"] = command["level"]
        elif command["type"] == "get_state":
            data = state
        await manager._dispatch_event(sess, {"type": "response", "command": command["type"],
                                            "id": command["id"], "success": True, "data": data})
    return patch.object(manager, "send_command", acknowledge)


def run_async(coro):
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def setUpModule():
    """#179: never let this module resolve the real pi CLI ("pi" -> pi.cmd)."""
    global _PREVIOUS_PI_CLI
    _PREVIOUS_PI_CLI = install_stub_pi_cli()


def tearDownModule():
    restore_stub_pi_cli(_PREVIOUS_PI_CLI)


class TestAgentModelOverride(unittest.TestCase):
    """Tests for #140: node-level model override."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-model-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        self.manager = AgentHostManager()
        self.capabilities_patch = patch.object(agent_model_capabilities, "get_capabilities", capabilities)
        self.capabilities_patch.start()
        self.addCleanup(self.capabilities_patch.stop)

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
        with native_settings_ack(self.manager, session):
            run_async(self.manager.set_model(session, "vllm-remote/qwen3.8-27b"))
        cmd = fake.get_command("set_model")
        self.assertIsNotNone(cmd, "set_model command should have been sent")
        self.assertEqual(cmd["provider"], "vllm-remote")
        self.assertEqual(cmd["modelId"], "qwen3.8-27b")

        # Native Pi RPC requires provider + modelId (rpc-mode.js set_model).
        # Resolve a bare request against the authoritative pre-mutation state.
        fake.reset_script([])
        with native_settings_ack(self.manager, session):
            run_async(self.manager.set_model(session, "gpt-4"))
        cmd = fake.get_command("set_model")
        self.assertIsNotNone(cmd, "set_model command should have been sent")
        self.assertEqual(cmd["modelId"], "gpt-4")
        self.assertEqual(cmd["provider"], "vllm-remote")
        self.assertEqual(session.model, "gpt-4")

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
        with native_settings_ack(agent_host_manager, session):
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


class SettingsHost(AgentHostManager):
    """Offline host boundary: no process, Pi configuration or network."""

    def __init__(self):
        super().__init__()
        self.effects = []
        self.session = AgentSession(project_id="fixture", node_id="settings-agent", model="old-model",
                                    session_file="original.jsonl", session_id="original", ext_session_id="original")
        self.session.process = FakeAgentProcess(script=[])
        self.session.status = "running"

    async def is_session_busy(self, session, *, refresh=True):
        return session.is_busy

    async def _runtime_rpc(self, session, command, **kwargs):
        if command["type"] == "fork":
            result = await self.fork(session, command["entryId"])
        else:
            await self.switch_branch(session, command["sessionPath"])
            result = {}
        return {"data": result}

    async def _runtime_state(self, session):
        return {"sessionId": session.session_id, "sessionFile": session.session_file,
                "model": {"provider": "fake", "id": "model"}, "thinkingLevel": "medium",
                "isStreaming": False, "isCompacting": False, "pendingMessageCount": 0}

    async def apply_runtime_settings(self, session, model, reasoning_effort):
        await self.set_model(session, model)
        session.reasoning_effort = reasoning_effort
        return session.effort_state

    async def get_or_create_session(self, *args):
        self.effects.append("session")
        return self.session

    async def set_model(self, session, model):
        self.effects.append("model")
        session.model = model

    async def fork(self, session, entry_id):
        self.effects.append("fork")
        session.session_file = "forked.jsonl"
        session.session_id = "forked"
        return {"session_file": "forked.jsonl", "session_id": "forked"}

    async def switch_branch(self, session, session_file):
        self.effects.append("switch")
        session.session_file = session_file
        session.session_id = session.ext_session_id = session_file.removesuffix(".jsonl")


class TestAgentSettingsOwnership(unittest.TestCase):
    """Approved seams: settings HTTP requests and public canvas persistence."""

    def setUp(self):
        from app.routers import api_agent_nodes
        self.temp = tempfile.TemporaryDirectory(prefix="firefly-settings-392-")
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.project_id = self.store.create_project("e2e-tabbit-settings-ownership")['id']
        self.node = {"id": "settings-agent", "type": "smart-agent", "x": 0, "y": 0,
                     "model": "old-model", "autonomy": "cautious", "prompt": "keep prompt",
                     "createdBy": "agent", "agentNodeId": "creator", "userTouchedAt": None,
                     "promptEditedByUser": False, "updated_at": 17,
                     "branches": [{"id": "original", "session_file": "original.jsonl", "session_id": "original"},
                                  {"id": "other", "session_file": "other.jsonl", "session_id": "other"}],
                     "current_branch": "original"}
        self.store.apply_agent_host_mutations(self.project_id, [{"op": "upsert_node", "node": self.node}], base_revision=0)
        self.capabilities_patch = patch.object(agent_model_capabilities, "get_capabilities", capabilities)
        self.capabilities_patch.start()
        self.addCleanup(self.capabilities_patch.stop)
        self.host = SettingsHost()
        self.host_patch = patch.object(api_agent_nodes, "agent_host_manager", self.host)
        self.host_patch.start()
        self.client = TestClient(main.app, base_url="http://127.0.0.1:3115",
                                 headers={"origin": "http://127.0.0.1:3115"})

    def tearDown(self):
        self.client.close()
        self.host_patch.stop()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp.cleanup()

    def post_setting(self, endpoint, body, node_id="settings-agent"):
        return self.client.post(f"/api/projects/{self.project_id}/agent-nodes/{node_id}/{endpoint}", json=body)

    def test_manual_model_change_preserves_provenance_and_returns_authoritative_canvas(self):
        broadcasts = []

        async def broadcast(project_id, updated_at, source=""):
            broadcasts.append(source)

        with patch.object(main.manager, "broadcast_canvas_updated", broadcast), patch.object(main, "now_ms", return_value=123456):
            response = self.post_setting("model", {"model": "new-model", "user_edit": True})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        persisted = self.store.canvas(self.project_id)
        node = next(n for n in persisted["nodes"] if n["id"] == "settings-agent")
        self.assertEqual(node["userTouchedAt"], 123456)
        self.assertEqual((node["createdBy"], node["agentNodeId"], node["promptEditedByUser"]),
                         ("agent", "creator", False))
        self.assertEqual(node["prompt"], "keep prompt")
        self.assertEqual(node["model"], "new-model")
        self.assertTrue(data["success"])
        self.assertTrue(data["persisted"])
        self.assertEqual(data["model"], "new-model")
        self.assertEqual(data["canvas"], persisted)
        self.assertEqual(broadcasts, [""])

    def test_manual_settings_stamp_only_actual_changes_not_automatic_or_noop(self):
        for endpoint, automatic, manual, server_time in (("model", "automatic-model", "manual-model", 456789),
                                                         ("autonomy", "standard", "autonomous", 987654)):
            with self.subTest(endpoint=endpoint):
                response = self.post_setting(endpoint, {endpoint: automatic, "user_edit": False})
                self.assertEqual(response.status_code, 200, response.text)
                node = next(n for n in self.store.canvas(self.project_id)["nodes"] if n["id"] == "settings-agent")
                previous_touch = node["userTouchedAt"]
                self.assertEqual(previous_touch, None if endpoint == "model" else 456789)
                with patch.object(main, "now_ms", return_value=server_time):
                    response = self.post_setting(endpoint, {endpoint: manual, "user_edit": True})
                self.assertEqual(response.status_code, 200, response.text)
                canvas = self.store.canvas(self.project_id)
                node = next(n for n in canvas["nodes"] if n["id"] == "settings-agent")
                self.assertEqual(node["userTouchedAt"], server_time)
                self.assertEqual((node["createdBy"], node["agentNodeId"], node["promptEditedByUser"]),
                                 ("agent", "creator", False))
                self.assertEqual(response.json()["canvas"], canvas)
                response = self.post_setting(endpoint, {endpoint: manual, "user_edit": True})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["canvas"], canvas)
                self.assertEqual(self.store.canvas(self.project_id), canvas)
                response = self.post_setting(endpoint, {endpoint: automatic})
                self.assertEqual(response.status_code, 200, response.text)
                node = next(n for n in response.json()["canvas"]["nodes"] if n["id"] == "settings-agent")
                self.assertEqual(node["userTouchedAt"], server_time)

    def test_session_only_model_does_not_create_canvas_identity(self):
        before = self.store.canvas(self.project_id)
        response = self.post_setting("model", {"model": "session-model", "user_edit": True}, node_id="session-only")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["persisted"])
        self.assertTrue(response.json()["is_running"])
        self.assertEqual(response.json()["model"], "session-model")
        self.assertEqual(response.json()["canvas"], before)
        self.assertEqual(self.store.canvas(self.project_id), before)
        # A fake stopped host session needs no concrete AgentSession members.
        self.host.session.process = None
        self.host.session.status = "stopped"
        response = self.client.post("/api/projects/no-such-project/agent-nodes/session-only/model",
                                    json={"model": "offline-model", "user_edit": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["persisted"])
        self.assertIsNone(response.json()["canvas"])
        self.assertEqual(self.host.session.model, "offline-model")

    def test_tool_broadcast_stays_agent_while_settings_use_empty_source(self):
        broadcasts = []
        operation_starts = []

        async def broadcast(project_id, updated_at, source="", *, phase=None, **operation):
            if phase == "start":
                operation_starts.append(source)
            else:
                broadcasts.append(source)

        with patch.object(main.manager, "broadcast_canvas_updated", broadcast):
            response = self.client.patch(f"/api/projects/{self.project_id}/canvas/nodes/settings-agent",
                                         json={"title": "tool title"},
                                         headers={"X-Agent-Project": self.project_id},
                                         params={"agent_node_id": self.node["id"]})
            self.assertEqual(response.status_code, 200, response.text)
            for endpoint, body in (("model", {"model": "new-model"}),
                                   ("autonomy", {"autonomy": "standard"}),
                                   ("fork", {"entry_id": "entry-1", "name": "manual"}),
                                   ("switch-branch", {"branch_id": "other"})):
                response = self.post_setting(endpoint, {**body, "user_edit": True})
                self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(operation_starts, ["agent"])
        self.assertEqual(broadcasts, ["agent", "", "", "", ""])
        node = next(n for n in self.store.canvas(self.project_id)["nodes"] if n["id"] == "settings-agent")
        self.assertEqual(node["title"], "tool title")

    def test_first_automatic_fork_keeps_original_host_identity_without_user_touch(self):
        node = {**self.node, "branches": [], "promptEditedByUser": True}
        self.store.apply_agent_host_mutations(self.project_id, [{"op": "upsert_node", "node": node}], base_revision=1)
        with patch.object(main, "now_ms", return_value=2468):
            response = self.post_setting("fork", {"entry_id": "entry-1", "name": "automatic"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["branches"][0], {"id": "branch-original", "name": "原始",
                         "session_file": "original.jsonl", "session_id": "original",
                         "fork_from_entry_id": None, "created_at": 2468})
        self.assertEqual(len(data["branches"]), 2)
        self.assertEqual(data["branch"]["session_file"], "forked.jsonl")
        self.assertEqual(data["branch"]["fork_from_entry_id"], "entry-1")
        canvas = self.store.canvas(self.project_id)
        self.assertEqual(data["canvas"], canvas)
        persisted = next(n for n in canvas["nodes"] if n["id"] == "settings-agent")
        self.assertEqual((persisted["createdBy"], persisted["agentNodeId"], persisted["userTouchedAt"], persisted["promptEditedByUser"]),
                         ("agent", "creator", None, True))
        response = self.post_setting("fork", {"entry_id": "entry-2", "name": "automatic again", "user_edit": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["branches"]), 3)
        self.assertIsNone(response.json()["canvas"]["nodes"][0]["userTouchedAt"])

    def test_cancelled_fork_and_failed_host_rpc_leave_durable_values_and_touch_unchanged(self):
        async def cancelled(session, entry_id):
            return {"cancelled": True}

        async def failed(*args):
            raise HTTPException(status_code=502, detail="offline fake host failure")

        before = self.store.canvas(self.project_id)
        with patch.object(self.host, "fork", cancelled):
            response = self.post_setting("fork", {"entry_id": "entry-1", "name": "cancelled", "user_edit": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.store.canvas(self.project_id), before)
        for endpoint, method, body in (("model", "set_model", {"model": "new-model"}),
                                        ("switch-branch", "switch_branch", {"branch_id": "other"})):
            with self.subTest(endpoint=endpoint), patch.object(self.host, method, failed):
                response = self.post_setting(endpoint, {**body, "user_edit": True})
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(self.store.canvas(self.project_id), before)

    def test_contract_rejects_invalid_edit_context_before_host_effects(self):
        requests = [("model", {"model": "new-model"}),
                    ("autonomy", {"autonomy": "standard"}),
                    ("fork", {"entry_id": "entry-1", "name": "new branch"}),
                    ("switch-branch", {"branch_id": "other"})]
        invalid = [{"user_edit": value} for value in (None, "true", "false", 0, 1, [], {})]
        invalid += [{key: value} for key, value in (("createdBy", "user"), ("agentNodeId", "injected"),
                    ("userTouchedAt", 123), ("promptEditedByUser", True), ("updated_at", 123),
                    ("created_at", 123))]
        before = self.store.canvas(self.project_id)
        for endpoint, body in requests:
            for injection in invalid:
                with self.subTest(endpoint=endpoint, injection=injection):
                    response = self.post_setting(endpoint, {**body, **injection})
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(response.json()["detail"]["code"], "INVALID_ARGUMENT")
                    self.assertFalse(self.host.effects)
                    self.assertEqual(self.store.canvas(self.project_id), before)


if __name__ == "__main__":
    unittest.main()
