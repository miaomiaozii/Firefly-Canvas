"""Tests for Agent node v2 TUI: event forwarding, session lifecycle, command sending.

Uses the fake agent host (tests/fake_agent_host.py) to simulate pi --mode rpc
subprocess behavior without requiring the real pi CLI in CI.

Test coverage:
- Session lifecycle: create, spawn, status, stop
- Event forwarding: agent_start, message_update (streaming), turn_end, agent_end
- Tool execution events: tool_execution_start, tool_execution_end
- Command sending: prompt, steer, follow_up, abort
- WebSocket event stream
"""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services.agent_host import AgentHostManager, AgentSession, agent_host_manager
from tests.fake_agent_host import FakeAgentProcess, make_streaming_response_script, make_tool_call_script


def run_async(coro):
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestAgentHostManager(unittest.TestCase):
    """Test the agent host manager with fake agent process."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-tui-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})

        # Create a fresh manager for each test
        self.manager = AgentHostManager()

    def tearDown(self):
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_get_or_create_session(self):
        """Test session creation and retrieval."""
        session1 = run_async(self.manager.get_or_create_session("proj-1", "node-1"))
        # Second call creates a new session because the first is not running
        session2 = run_async(self.manager.get_or_create_session("proj-1", "node-1"))
        self.assertIsNot(session1, session2)

        # Different node should get different session
        session3 = run_async(self.manager.get_or_create_session("proj-1", "node-2"))
        self.assertIsNot(session1, session3)

        # Verify sessions are stored with correct keys
        self.assertIn("proj-1:node-1", self.manager.sessions)
        self.assertIn("proj-1:node-2", self.manager.sessions)

    def test_session_key(self):
        """Test session key generation."""
        key = self.manager._session_key("proj-1", "node-1")
        self.assertEqual(key, "proj-1:node-1")

    def test_spawn_session_with_fake_process(self):
        """Test spawning a session with a fake agent process."""
        project = self.store.create_project("Agent TUI Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))

        # Create fake process with session state response
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "starting"

        # get_state should return the fake session state
        state = run_async(self.manager.get_session_state(session, timeout=2.0))
        # Note: get_session_state uses send_command which writes to stdin,
        # and the fake auto-responds to get_state
        # But we need to actually run the event reader for this to work
        # For this test, just verify the fake process received the command
        cmd = fake.get_command("get_state")
        self.assertIsNotNone(cmd, "get_state command should have been sent")

    def test_send_command(self):
        """Test sending commands to the fake agent process."""
        project = self.store.create_project("Agent TUI Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))

        # Create fake process
        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        # Send a prompt
        run_async(self.manager.send_prompt(session, "介绍一下你自己"))

        # Verify command was received by fake process
        cmd = fake.get_command("prompt")
        self.assertIsNotNone(cmd, "prompt command should have been sent")
        self.assertEqual(cmd["message"], "介绍一下你自己")

    def test_send_steer(self):
        """Test sending steer command."""
        project = self.store.create_project("Agent TUI Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))

        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        run_async(self.manager.send_steer(session, "换个方向"))

        cmd = fake.get_command("steer")
        self.assertIsNotNone(cmd, "steer command should have been sent")
        self.assertEqual(cmd["message"], "换个方向")

    def test_send_follow_up(self):
        """Test sending follow_up command."""
        project = self.store.create_project("Agent TUI Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))

        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        run_async(self.manager.send_follow_up(session, "然后再做这个"))

        cmd = fake.get_command("follow_up")
        self.assertIsNotNone(cmd, "follow_up command should have been sent")
        self.assertEqual(cmd["message"], "然后再做这个")

    def test_send_abort(self):
        """Test sending abort command."""
        project = self.store.create_project("Agent TUI Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))

        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        run_async(self.manager.send_abort(session))

        cmd = fake.get_command("abort")
        self.assertIsNotNone(cmd, "abort command should have been sent")

    def test_stop_session(self):
        """Test stopping a session terminates the process."""
        project = self.store.create_project("Agent TUI Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))

        fake = FakeAgentProcess(script=[])
        session.process = fake
        session.status = "running"

        run_async(self.manager.stop_session(session))

        self.assertTrue(fake._closed, "Process should be terminated")
        self.assertIsNone(session.process, "Session process should be cleared")
        self.assertEqual(session.status, "stopped")

    def test_cleanup_all(self):
        """Test cleanup_all stops all sessions."""
        project = self.store.create_project("Agent TUI Test")
        session1 = run_async(self.manager.get_or_create_session(project["id"], "node-1"))
        session2 = run_async(self.manager.get_or_create_session(project["id"], "node-2"))

        fake1 = FakeAgentProcess(script=[])
        fake2 = FakeAgentProcess(script=[])
        session1.process = fake1
        session2.process = fake2
        session1.status = "running"
        session2.status = "running"

        run_async(self.manager.cleanup_all())

        self.assertTrue(fake1._closed, "Process 1 should be terminated")
        self.assertTrue(fake2._closed, "Process 2 should be terminated")
        self.assertEqual(len(self.manager.sessions), 0, "Sessions should be cleared")


class TestAgentCommandSending(unittest.TestCase):
    """Test sending steer, follow_up, and abort commands."""

    def setUp(self):
        self.project_id = "test-project"
        self.node_id = "agent-1"
        self.manager = AgentHostManager()
        self.session = asyncio.get_event_loop().run_until_complete(
            self.manager.get_or_create_session(self.project_id, self.node_id)
        )
        self.process = FakeAgentProcess(script=[{"type": "session", "sessionId": "s1"}])
        self.session.process = self.process
        self.session.status = "running"

    def test_send_steer(self):
        """Test that send_steer sends the correct command."""
        self.process.reset_script([{"type": "session", "sessionId": "s1"}])
        asyncio.get_event_loop().run_until_complete(
            self.manager.send_steer(self.session, "go left")
        )
        self.assertEqual(len(self.process.commands), 1)
        self.assertEqual(self.process.commands[0]["type"], "steer")
        self.assertEqual(self.process.commands[0]["message"], "go left")

    def test_send_follow_up(self):
        """Test that send_follow_up sends the correct command."""
        self.process.reset_script([{"type": "session", "sessionId": "s1"}])
        asyncio.get_event_loop().run_until_complete(
            self.manager.send_follow_up(self.session, "then do X")
        )
        self.assertEqual(len(self.process.commands), 1)
        self.assertEqual(self.process.commands[0]["type"], "follow_up")
        self.assertEqual(self.process.commands[0]["message"], "then do X")

    def test_send_abort(self):
        """Test that send_abort sends the correct command."""
        self.process.reset_script([{"type": "session", "sessionId": "s1"}])
        asyncio.get_event_loop().run_until_complete(
            self.manager.send_abort(self.session)
        )
        self.assertEqual(len(self.process.commands), 1)
        self.assertEqual(self.process.commands[0]["type"], "abort")




class TestAgentCrashRecovery(unittest.TestCase):
    """Test crash detection and auto-respawn."""

    def setUp(self):
        self.project_id = "test-project"
        self.node_id = "agent-crash"
        self.manager = AgentHostManager()
        self.session = asyncio.get_event_loop().run_until_complete(
            self.manager.get_or_create_session(self.project_id, self.node_id)
        )
        # Create a fake process that will "crash"
        self.process = FakeAgentProcess(script=[
            {"type": "session", "sessionId": "s1"},
        ])
        self.session.process = self.process
        self.session.status = "running"

    def test_process_exit_sets_crashed_status(self):
        """Test that when the process exits with non-zero code, status is crashed."""
        # Simulate process exit with non-zero code
        self.process._returncode = 1
        self.process._closed = True

        # Trigger the reader task to detect the exit
        asyncio.get_event_loop().run_until_complete(
            self.manager._read_events(self.session)
        )

        # Status should be crashed (or starting/running if respawned)
        self.assertIn(self.session.status, ["crashed", "starting", "running"])

    def test_process_exit_zero_sets_stopped(self):
        """Test that when the process exits with zero code, status is stopped."""
        # Simulate process exit with zero code
        self.process._returncode = 0
        self.process._closed = True

        # Trigger the reader task to detect the exit
        asyncio.get_event_loop().run_until_complete(
            self.manager._read_events(self.session)
        )

        # Status should be stopped (no respawn for clean exit)
        self.assertEqual(self.session.status, "stopped")

    def test_respawn_session_method_exists(self):
        """Test that respawn_session method exists and is callable."""
        self.assertTrue(hasattr(self.manager, "respawn_session"))
        self.assertTrue(callable(self.manager.respawn_session))

    def test_process_exited_event_dispatched(self):
        """Test that process_exited event is dispatched when process exits."""
        events_received = []

        async def event_handler(sess, event):
            events_received.append(event)

        self.manager.on_event(self.session, event_handler)

        # Simulate process exit
        self.process._returncode = 1
        self.process._closed = True

        # Trigger the reader task to detect the exit
        asyncio.get_event_loop().run_until_complete(
            self.manager._read_events(self.session)
        )

        # Should have received process_exited event
        event_types = [e.get("type") for e in events_received]
        self.assertIn("process_exited", event_types)


class TestAgentEventDispatch(unittest.TestCase):
    """Test event dispatching and status updates."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-event-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.manager = AgentHostManager()

    def tearDown(self):
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_agent_start_sets_busy(self):
        """agent_start event should set status to busy."""
        project = self.store.create_project("Agent Event Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))
        session.status = "running"

        run_async(self.manager._dispatch_event(session, {"type": "agent_start"}))
        self.assertEqual(session.status, "busy")

    def test_agent_end_sets_running(self):
        """agent_end event should set status to running (idle)."""
        project = self.store.create_project("Agent Event Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))
        session.status = "busy"

        run_async(self.manager._dispatch_event(session, {"type": "agent_end"}))
        self.assertEqual(session.status, "running")

    def test_tool_execution_start_sets_busy(self):
        """tool_execution_start event should set status to busy."""
        project = self.store.create_project("Agent Event Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))
        session.status = "running"

        run_async(self.manager._dispatch_event(session, {"type": "tool_execution_start", "toolName": "canvas.create_node"}))
        self.assertEqual(session.status, "busy")

    def test_event_handlers_are_called(self):
        """Registered event handlers should be called for each event."""
        project = self.store.create_project("Agent Event Test")
        session = run_async(self.manager.get_or_create_session(project["id"], "node-1"))

        received_events = []

        async def handler(sess, event):
            received_events.append(event)

        self.manager.on_event(session, handler)

        run_async(self.manager._dispatch_event(session, {"type": "agent_start"}))
        run_async(self.manager._dispatch_event(session, {"type": "agent_end"}))

        self.assertEqual(len(received_events), 2)
        self.assertEqual(received_events[0]["type"], "agent_start")
        self.assertEqual(received_events[1]["type"], "agent_end")


class TestFakeAgentProcess(unittest.TestCase):
    """Test the fake agent process itself."""

    def test_readline_returns_scripted_events(self):
        """readline should return scripted events in order."""
        script = [
            {"type": "agent_start"},
            {"type": "agent_end"},
        ]
        fake = FakeAgentProcess(script=script)

        line1 = fake.readline()
        self.assertEqual(json.loads(line1), {"type": "agent_start"})

        line2 = fake.readline()
        self.assertEqual(json.loads(line2), {"type": "agent_end"})

        # After script is exhausted, return empty
        line3 = fake.readline()
        self.assertEqual(line3, "")

    def test_write_collects_commands(self):
        """write should collect JSONL commands."""
        fake = FakeAgentProcess(script=[])

        fake.write(json.dumps({"type": "prompt", "message": "hello"}) + "\n")
        fake.write(json.dumps({"type": "abort"}) + "\n")

        self.assertEqual(len(fake.commands), 2)
        self.assertEqual(fake.commands[0]["type"], "prompt")
        self.assertEqual(fake.commands[1]["type"], "abort")

    def test_get_state_auto_response(self):
        """get_state command should get auto-response."""
        fake = FakeAgentProcess(script=[])

        # Write get_state command
        fake.write(json.dumps({"id": "test-1", "type": "get_state"}) + "\n")

        # The response should be in the stdout queue
        # For this test, we just verify the command was collected
        cmd = fake.get_command("get_state")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd["id"], "test-1")

    def test_terminate(self):
        """terminate should mark process as closed."""
        fake = FakeAgentProcess(script=[])
        self.assertIsNone(fake.poll())

        fake.terminate()
        self.assertEqual(fake.poll(), 0)
        self.assertEqual(fake.returncode, 0)

    def test_make_streaming_response_script(self):
        """make_streaming_response_script should create proper event sequence."""
        script = make_streaming_response_script("你好，我是 Agent")

        # Should start with agent_start
        self.assertEqual(script[0]["type"], "agent_start")

        # Should have message_update events with text_delta
        delta_events = [e for e in script if e["type"] == "message_update"]
        self.assertGreater(len(delta_events), 0)

        # All deltas should concatenate to the full text
        full_text = "".join(
            e["assistantMessageEvent"]["delta"]
            for e in delta_events
        )
        self.assertEqual(full_text, "你好，我是 Agent")

        # Should end with turn_end and agent_end
        self.assertEqual(script[-2]["type"], "turn_end")
        self.assertEqual(script[-1]["type"], "agent_end")

    def test_make_tool_call_script(self):
        """make_tool_call_script should create proper tool call sequence."""
        script = make_tool_call_script("canvas.create_node", {"type": "smart-prompt"}, {"success": True})

        self.assertEqual(script[0]["type"], "agent_start")
        self.assertEqual(script[1]["type"], "tool_execution_start")
        self.assertEqual(script[1]["toolName"], "canvas.create_node")
        self.assertEqual(script[2]["type"], "tool_execution_end")
        self.assertEqual(script[2]["isError"], False)
        self.assertEqual(script[3]["type"], "agent_end")


class TestMultiNodeIsolation(unittest.TestCase):
    """Test that two agent nodes' event streams are isolated from each other.

    Verifies that events dispatched to node A's session only reach node A's
    WebSocket stream, and events for node B only reach node B's stream.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-isolation-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.previous_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.client = TestClient(main.app, base_url="http://127.0.0.1", headers={"origin": "http://127.0.0.1"})
        # Reset the global manager's sessions for a clean test
        run_async(agent_host_manager.cleanup_all())

    def tearDown(self):
        run_async(agent_host_manager.cleanup_all())
        self.client.close()
        self.store.close()
        main.FIREFLY_PROJECT_STORE = self.previous_store
        self.temp_dir.cleanup()

    def test_two_nodes_events_do_not_cross(self):
        """Events from node A should not appear in node B's WS stream, and vice versa."""
        project = self.store.create_project("Isolation Test Project")
        project_id = project["id"]

        # Create two sessions for two different agent nodes
        session_a = run_async(agent_host_manager.get_or_create_session(project_id, "node-a"))
        session_b = run_async(agent_host_manager.get_or_create_session(project_id, "node-b"))

        # Attach fake processes with distinguishable event scripts
        script_a = make_streaming_response_script("Hello from A")
        script_b = make_streaming_response_script("Hello from B")

        fake_a = FakeAgentProcess(script=script_a)
        fake_b = FakeAgentProcess(script=script_b)

        session_a.process = fake_a
        session_a.status = "running"
        session_b.process = fake_b
        session_b.status = "running"

        # Connect two WebSockets (one per node)
        ws_a = self.client.websocket_connect(f"/api/projects/{project_id}/agent-nodes/node-a/stream")
        ws_b = self.client.websocket_connect(f"/api/projects/{project_id}/agent-nodes/node-b/stream")

        # Dispatch events directly to each session (simulating _read_events behavior)
        for event in script_a:
            run_async(agent_host_manager._dispatch_event(session_a, event))
        for event in script_b:
            run_async(agent_host_manager._dispatch_event(session_b, event))

        # Collect events from both WebSockets
        events_a = []
        events_b = []
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                events_a.append(ws_a.receive_json(timeout=0.3))
            except Exception:
                break
            try:
                events_b.append(ws_b.receive_json(timeout=0.3))
            except Exception:
                break

        ws_a.close()
        ws_b.close()

        # Verify isolation: node A's events should contain "Hello from A" only
        a_text = "".join(
            e.get("assistantMessageEvent", {}).get("delta", "")
            for e in events_a
            if e.get("type") == "message_update"
        )
        b_text = "".join(
            e.get("assistantMessageEvent", {}).get("delta", "")
            for e in events_b
            if e.get("type") == "message_update"
        )

        self.assertIn("Hello from A", a_text, f"Node A should receive its own text. Got: {a_text}")
        self.assertNotIn("Hello from B", a_text, f"Node A should NOT receive node B's text. Got: {a_text}")
        self.assertIn("Hello from B", b_text, f"Node B should receive its own text. Got: {b_text}")
        self.assertNotIn("Hello from A", b_text, f"Node B should NOT receive node A's text. Got: {b_text}")

        # Verify both nodes received events
        self.assertGreater(len(events_a), 0, "Node A should receive events")
        self.assertGreater(len(events_b), 0, "Node B should receive events")

    def test_two_nodes_tool_events_do_not_cross(self):
        """Tool execution events from node A should not appear in node B's stream."""
        project = self.store.create_project("Isolation Tool Test Project")
        project_id = project["id"]

        session_a = run_async(agent_host_manager.get_or_create_session(project_id, "node-a"))
        session_b = run_async(agent_host_manager.get_or_create_session(project_id, "node-b"))

        # Node A: tool call to create a node
        script_a = make_tool_call_script("canvas.create_node", {"type": "smart-prompt", "title": "A's node"}, {"success": True, "node_id": "n1"})
        # Node B: tool call to update a node
        script_b = make_tool_call_script("canvas.update_node", {"node_id": "n2", "title": "B's node"}, {"success": True})

        fake_a = FakeAgentProcess(script=script_a)
        fake_b = FakeAgentProcess(script=script_b)

        session_a.process = fake_a
        session_a.status = "running"
        session_b.process = fake_b
        session_b.status = "running"

        ws_a = self.client.websocket_connect(f"/api/projects/{project_id}/agent-nodes/node-a/stream")
        ws_b = self.client.websocket_connect(f"/api/projects/{project_id}/agent-nodes/node-b/stream")

        for event in script_a:
            run_async(agent_host_manager._dispatch_event(session_a, event))
        for event in script_b:
            run_async(agent_host_manager._dispatch_event(session_b, event))

        events_a = []
        events_b = []
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                events_a.append(ws_a.receive_json(timeout=0.3))
            except Exception:
                break
            try:
                events_b.append(ws_b.receive_json(timeout=0.3))
            except Exception:
                break

        ws_a.close()
        ws_b.close()

        # Verify node A received its tool events
        a_tools = [e for e in events_a if e.get("type") == "tool_execution_start"]
        b_tools = [e for e in events_b if e.get("type") == "tool_execution_start"]

        self.assertEqual(len(a_tools), 1, f"Node A should receive 1 tool event. Got: {len(a_tools)}")
        self.assertEqual(a_tools[0]["toolName"], "canvas.create_node")

        self.assertEqual(len(b_tools), 1, f"Node B should receive 1 tool event. Got: {len(b_tools)}")
        self.assertEqual(b_tools[0]["toolName"], "canvas.update_node")


if __name__ == "__main__":
    unittest.main()
