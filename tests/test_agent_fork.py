"""Tests for Agent session branching (fork) - #142.

Uses the fake agent host (tests/fake_agent_host.py) to simulate pi --mode rpc
subprocess behavior without requiring the real pi CLI in CI.

Test coverage:
- Host boundary: get_fork_messages, fork, switch_branch
- API endpoints: GET fork-messages, POST fork, POST switch-branch
- Branch persistence to node attributes
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


async def _wait_for_command_count(process, cmd_type: str, count: int, timeout: float = 5.0):
    """Wait until the fake process has received at least `count` commands of the given type."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    n = 0
    while loop.time() < deadline:
        n = sum(1 for c in process.commands if c.get("type") == cmd_type)
        if n >= count:
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Expected {count} '{cmd_type}' commands, got {n} within {timeout}s")


class TestAgentForkHostBoundary(unittest.TestCase):
    """Test the fork methods on AgentHostManager."""

    def setUp(self):
        self.project_id = "test-project-fork"
        self.node_id = "agent-fork-1"
        self.manager = AgentHostManager()
        self.session = None
        self.process = None

    def tearDown(self):
        if self.session:
            run_async(self.manager.stop_session(self.session))
        self.manager.sessions.clear()

    def _setup_session(self, script=None):
        """Create a session with a fake process."""
        self.session = run_async(self.manager.get_or_create_session(self.project_id, self.node_id))
        self.process = FakeAgentProcess(script=script or [])
        self.session.process = self.process
        self.session.status = "running"
        self.session.session_id = "test-session-1"
        self.session.session_file = "/tmp/test-session-1.jsonl"
        return self.session

    def test_get_fork_messages(self):
        """get_fork_messages should send RPC and return the list."""
        fork_messages = [
            {"entryId": "entry-1", "text": "你好"},
            {"entryId": "entry-2", "text": "介绍一下项目"},
        ]
        self._setup_session([])

        async def test_get_fork_messages():
            # Start the fork_messages call (it will wait for response)
            task = asyncio.create_task(self.manager.get_fork_messages(self.session))
            # Wait a bit for it to register its handler
            await asyncio.sleep(0.1)
            # Manually dispatch the response event
            event = {
                "type": "response",
                "command": "get_fork_messages",
                "success": True,
                "data": {"messages": fork_messages},
            }
            await self.manager._dispatch_event(self.session, event)
            # Wait for the result
            result = await task
            return result

        result = run_async(test_get_fork_messages())
        self.assertEqual(result, fork_messages)

        # Verify the RPC command was sent
        cmd = self.process.get_command("get_fork_messages")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd["type"], "get_fork_messages")

    def test_fork(self):
        """fork should send the fork RPC, then query get_state for the new session file."""
        self._setup_session([])

        async def test_fork():
            before = sum(1 for c in self.process.commands if c.get("type") == "get_state")
            # Start the fork call (it will wait for the fork response)
            task = asyncio.create_task(self.manager.fork(self.session, "entry-1"))
            # Wait for the fork command to be sent
            await _wait_for_command_count(self.process, "fork", 1)
            # Dispatch the fork response
            await self.manager._dispatch_event(self.session, {
                "type": "response",
                "command": "fork",
                "success": True,
                "data": {"text": "Forked to new session"},
            })
            # The fork method now calls get_session_state; wait for its get_state command
            await _wait_for_command_count(self.process, "get_state", before + 1)
            await asyncio.sleep(0.1)  # give the handler time to register
            # Dispatch the get_state response with the NEW session file
            await self.manager._dispatch_event(self.session, {
                "type": "response",
                "command": "get_state",
                "success": True,
                "data": {
                    "sessionId": "new-session-2",
                    "sessionFile": "/tmp/new-session-2.jsonl",
                    "model": "fake-model",
                },
            })
            # Wait for the result
            result = await asyncio.wait_for(task, timeout=5.0)
            return result

        result = run_async(test_fork())
        self.assertEqual(result["cancelled"], False)
        self.assertEqual(result["session_id"], "new-session-2")
        self.assertEqual(result["session_file"], "/tmp/new-session-2.jsonl")

        # Verify fork command was sent
        cmd = self.process.get_command("fork")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd["entryId"], "entry-1")

        # Verify session state was updated
        self.assertEqual(self.session.session_id, "new-session-2")
        self.assertEqual(self.session.session_file, "/tmp/new-session-2.jsonl")

    def test_switch_branch(self):
        """switch_branch should send switch_session RPC."""
        self._setup_session([])

        async def test_switch_branch():
            # Start the switch_branch call
            task = asyncio.create_task(self.manager.switch_branch(self.session, "/tmp/other-branch.jsonl"))
            # Wait a bit for it to register its handler
            await asyncio.sleep(0.1)
            # Manually dispatch the response event
            event = {
                "type": "response",
                "command": "switch_session",
                "success": True,
            }
            await self.manager._dispatch_event(self.session, event)
            # Wait for the result
            await task

        run_async(test_switch_branch())

        # Verify switch_session command was sent (uses sessionPath per pi RPC)
        cmd = self.process.get_command("switch_session")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd["sessionPath"], "/tmp/other-branch.jsonl")

        # Verify session state was updated
        self.assertEqual(self.session.session_file, "/tmp/other-branch.jsonl")


class TestAgentForkAPI(unittest.TestCase):
    """Test the fork API endpoints by calling the endpoint functions directly."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-fork-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.project = self.store.create_project("Fork Test")
        self.project_id = self.project["id"]

        # Create an agent node on the canvas
        node = {
            "id": "fork-agent-1",
            "type": "smart-agent",
            "x": 100,
            "y": 100,
            "w": 382,
            "h": 360,
            "title": "Fork Agent",
            "agentGoal": "",
        }
        self.store.apply_mutations(
            self.project_id,
            [{"op": "upsert_node", "node": node}],
            base_revision=0,
        )
        self.node_id = "fork-agent-1"

        # Set up the app with our store
        main.FIREFLY_PROJECT_STORE = self.store

        # Reset the global agent host manager
        run_async(agent_host_manager.cleanup_all())

    def tearDown(self):
        run_async(agent_host_manager.cleanup_all())
        self.temp_dir.cleanup()

    def test_fork_messages_endpoint(self):
        """GET /fork-messages should return the list of forkable messages."""
        from app.routers.api_agent_nodes import get_fork_messages as endpoint

        fork_messages = [
            {"entryId": "entry-1", "text": "你好"},
            {"entryId": "entry-2", "text": "介绍一下"},
        ]

        async def run_test():
            # Create a session with a fake process
            session = await agent_host_manager.get_or_create_session(self.project_id, self.node_id)
            fake = FakeAgentProcess(script=[])
            session.process = fake
            session.status = "running"
            session.session_id = "test-session-1"

            # Start the endpoint call (it will wait for the response)
            endpoint_task = asyncio.create_task(endpoint(self.project_id, self.node_id, x_agent_project=""))

            # Wait for the endpoint to register its handler
            await asyncio.sleep(0.2)

            # Manually dispatch the response event
            event = {
                "type": "response",
                "command": "get_fork_messages",
                "success": True,
                "data": {"messages": fork_messages},
            }
            await agent_host_manager._dispatch_event(session, event)

            # Wait for the endpoint to complete
            result = await endpoint_task
            return result

        result = run_async(run_test())
        self.assertTrue(result["success"])
        self.assertEqual(result["messages"], fork_messages)

    def test_fork_endpoint(self):
        """POST /fork should create a new branch and persist it."""
        from app.routers.api_agent_nodes import fork_session as endpoint

        async def run_test():
            # Create a session with a fake process
            session = await agent_host_manager.get_or_create_session(self.project_id, self.node_id)
            fake = FakeAgentProcess(script=[])
            session.process = fake
            session.status = "running"
            session.session_id = "test-session-1"
            session.session_file = "/tmp/test-session-1.jsonl"

            # Start the endpoint call (it will wait for the fork response)
            endpoint_task = asyncio.create_task(endpoint(
                self.project_id, self.node_id,
                entry_id="entry-1", name="我的分支",
                x_agent_project="",
            ))

            # Wait for the fork command to be sent
            before = sum(1 for c in fake.commands if c.get("type") == "get_state")
            await _wait_for_command_count(fake, "fork", 1)
            # Dispatch the fork response
            await agent_host_manager._dispatch_event(session, {
                "type": "response",
                "command": "fork",
                "success": True,
                "data": {"text": "Forked to new session"},
            })
            # The fork method now calls get_session_state; wait for its get_state command
            await _wait_for_command_count(fake, "get_state", before + 1)
            await asyncio.sleep(0.1)
            # Dispatch the get_state response with the NEW session file
            await agent_host_manager._dispatch_event(session, {
                "type": "response",
                "command": "get_state",
                "success": True,
                "data": {
                    "sessionId": "new-session-2",
                    "sessionFile": "/tmp/new-session-2.jsonl",
                    "model": "fake-model",
                },
            })

            # Wait for the endpoint to complete
            result = await asyncio.wait_for(endpoint_task, timeout=5.0)
            return result

        result = run_async(run_test())
        self.assertTrue(result["success"])
        # First fork creates the original branch + the new branch
        self.assertEqual(len(result["branches"]), 2)
        self.assertEqual(result["branches"][0]["name"], "原始")
        self.assertEqual(result["branches"][0]["session_file"], "/tmp/test-session-1.jsonl")
        self.assertEqual(result["branches"][1]["name"], "我的分支")
        self.assertEqual(result["branches"][1]["session_file"], "/tmp/new-session-2.jsonl")
        self.assertEqual(result["current_branch"], result["branches"][1]["id"])

        # Verify persistence: node should have branches attribute
        canvas = self.store.canvas(self.project_id)
        node = next(n for n in canvas["nodes"] if n["id"] == self.node_id)
        self.assertIn("branches", node)
        self.assertEqual(len(node["branches"]), 2)
        self.assertEqual(node["current_branch"], result["current_branch"])

    def test_switch_branch_endpoint(self):
        """POST /switch-branch should switch to the specified branch."""
        from app.routers.api_agent_nodes import fork_session as fork_endpoint
        from app.routers.api_agent_nodes import switch_branch as switch_endpoint

        async def run_test():
            # Create a session with a fake process
            session = await agent_host_manager.get_or_create_session(self.project_id, self.node_id)
            fake = FakeAgentProcess(script=[])
            session.process = fake
            session.status = "running"
            session.session_id = "test-session-1"
            session.session_file = "/tmp/test-session-1.jsonl"

            # Fork to create a branch
            before = sum(1 for c in fake.commands if c.get("type") == "get_state")
            fork_task = asyncio.create_task(fork_endpoint(
                self.project_id, self.node_id,
                entry_id="entry-1", name="",
                x_agent_project="",
            ))
            await _wait_for_command_count(fake, "fork", 1)
            # Dispatch fork response
            await agent_host_manager._dispatch_event(session, {
                "type": "response",
                "command": "fork",
                "success": True,
                "data": {"text": "Forked to new session"},
            })
            await _wait_for_command_count(fake, "get_state", before + 1)
            await asyncio.sleep(0.1)
            # Dispatch the get_state response with the NEW session file
            await agent_host_manager._dispatch_event(session, {
                "type": "response",
                "command": "get_state",
                "success": True,
                "data": {
                    "sessionId": "new-session-2",
                    "sessionFile": "/tmp/new-session-2.jsonl",
                    "model": "fake-model",
                },
            })
            fork_result = await asyncio.wait_for(fork_task, timeout=5.0)

            # Switch back to the ORIGINAL branch (current is the new branch after fork)
            switch_task = asyncio.create_task(switch_endpoint(
                self.project_id, self.node_id,
                branch_id="branch-original",
                x_agent_project="",
            ))
            await asyncio.sleep(0.2)
            # Dispatch switch response
            await agent_host_manager._dispatch_event(session, {
                "type": "response",
                "command": "switch_session",
                "success": True,
            })
            switch_result = await asyncio.wait_for(switch_task, timeout=5.0)

            return fork_result, switch_result, fake

        fork_result, switch_result, fake = run_async(run_test())

        self.assertTrue(fork_result["success"])

        self.assertTrue(switch_result["success"])
        self.assertEqual(switch_result["current_branch"], "branch-original")

        # Verify the switch_session command was sent (uses sessionPath per pi RPC)
        cmd = fake.get_command("switch_session")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd["sessionPath"], "/tmp/test-session-1.jsonl")

        # Verify persistence
        canvas = self.store.canvas(self.project_id)
        node = next(n for n in canvas["nodes"] if n["id"] == self.node_id)
        self.assertEqual(node["current_branch"], "branch-original")


if __name__ == "__main__":
    unittest.main()
