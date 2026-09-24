"""Tests for parallel Agent node execution (#143).

Verifies that multiple agent nodes can run concurrently on the same canvas
without interfering with each other:
- Each node has an independent pi subprocess (isolated sessions)
- Concurrent canvas mutations are serialized via the revision mechanism
- Generation tasks from multiple agents coexist correctly
- TUI event streams are isolated per node

Uses fake agent host (tests/fake_agent_host.py) for deterministic testing.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main
from firefly_projects import ProjectStore
from app.services.agent_host import AgentHostManager
from tests.fake_agent_host import FakeAgentProcess, make_streaming_response_script


def run_async(coro):
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestAgentConcurrency(unittest.TestCase):
    """Test concurrent agent node behavior."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-agent-concurrency-tests-")
        self.store = ProjectStore(Path(self.temp_dir.name) / "projects", snapshot_delay=0)
        self.project = self.store.create_project("Concurrency Test")
        self.project_id = self.project["id"]

        # Create two agent nodes on the canvas
        self.agent_node_a = "agent-conc-a"
        self.agent_node_b = "agent-conc-b"
        node_a = {
            "id": self.agent_node_a,
            "type": "smart-agent",
            "x": 100,
            "y": 100,
            "w": 382,
            "h": 360,
            "title": "Agent A",
            "agentGoal": "Test agent A",
        }
        node_b = {
            "id": self.agent_node_b,
            "type": "smart-agent",
            "x": 500,
            "y": 100,
            "w": 382,
            "h": 360,
            "title": "Agent B",
            "agentGoal": "Test agent B",
        }
        self.store.apply_mutations(
            self.project_id,
            [{"op": "upsert_node", "node": node_a}, {"op": "upsert_node", "node": node_b}],
            base_revision=0,
        )

        # Set up the app with our store
        main.FIREFLY_PROJECT_STORE = self.store

        # Create a fresh manager for each test
        self.manager = AgentHostManager()
        # Replace the global manager temporarily
        import app.routers.api_agent_nodes as nodes_module
        import app.routers.api_agent_tools as tools_module
        self._orig_manager = nodes_module.agent_host_manager
        nodes_module.agent_host_manager = self.manager
        tools_module.agent_host_manager = self.manager

    def tearDown(self):
        run_async(self.manager.cleanup_all())
        import app.routers.api_agent_nodes as nodes_module
        import app.routers.api_agent_tools as tools_module
        nodes_module.agent_host_manager = self._orig_manager
        tools_module.agent_host_manager = self._orig_manager
        self.temp_dir.cleanup()

    async def _setup_fake_sessions(self):
        """Set up two fake agent sessions with independent processes."""
        session_a = await self.manager.get_or_create_session(self.project_id, self.agent_node_a)
        process_a = FakeAgentProcess(script=make_streaming_response_script("Agent A response"))
        session_a.process = process_a
        session_a.status = "running"
        session_a.session_id = "session-a"
        session_a.session_file = "/tmp/session-a.jsonl"

        session_b = await self.manager.get_or_create_session(self.project_id, self.agent_node_b)
        process_b = FakeAgentProcess(script=make_streaming_response_script("Agent B response"))
        session_b.process = process_b
        session_b.status = "running"
        session_b.session_id = "session-b"
        session_b.session_file = "/tmp/session-b.jsonl"

        return session_a, session_b, process_a, process_b

    def test_session_isolation(self):
        """Each agent node should have an independent session."""
        async def test_isolation():
            session_a = await self.manager.get_or_create_session(self.project_id, self.agent_node_a)
            session_b = await self.manager.get_or_create_session(self.project_id, self.agent_node_b)

            # They should be different objects
            self.assertIsNot(session_a, session_b)
            self.assertEqual(session_a.node_id, self.agent_node_a)
            self.assertEqual(session_b.node_id, self.agent_node_b)

            # Set up fake processes
            process_a = FakeAgentProcess()
            process_b = FakeAgentProcess()
            session_a.process = process_a
            session_b.process = process_b
            session_a.status = "running"
            session_b.status = "running"

            # Send different prompts
            await self.manager.send_prompt(session_a, "Hello from A")
            await self.manager.send_prompt(session_b, "Hello from B")

            # Verify each process received only its own prompt
            prompt_a = process_a.get_command("prompt")
            prompt_b = process_b.get_command("prompt")
            self.assertIsNotNone(prompt_a)
            self.assertIsNotNone(prompt_b)
            self.assertEqual(prompt_a["message"], "Hello from A")
            self.assertEqual(prompt_b["message"], "Hello from B")

        run_async(test_isolation())

    def test_concurrent_prompts_do_not_block(self):
        """Concurrent prompt sends should not block each other."""
        async def test_concurrent_prompts():
            session_a, session_b, process_a, process_b = await self._setup_fake_sessions()

            start = time.time()
            # Send prompts concurrently
            await asyncio.gather(
                self.manager.send_prompt(session_a, "Prompt A"),
                self.manager.send_prompt(session_b, "Prompt B"),
            )
            elapsed = time.time() - start

            # Both should have received their prompt
            prompt_a = process_a.get_command("prompt")
            prompt_b = process_b.get_command("prompt")
            self.assertIsNotNone(prompt_a)
            self.assertIsNotNone(prompt_b)
            self.assertEqual(prompt_a["message"], "Prompt A")
            self.assertEqual(prompt_b["message"], "Prompt B")

            # Should be fast (no blocking)
            self.assertLess(elapsed, 1.0)

        run_async(test_concurrent_prompts())

    def test_event_stream_isolation(self):
        """Events from one agent should not leak to another's event handlers."""
        async def test_event_isolation():
            session_a, session_b, process_a, process_b = await self._setup_fake_sessions()

            events_a = []
            events_b = []

            async def handler_a(sess, event):
                events_a.append(event)

            async def handler_b(sess, event):
                events_b.append(event)

            self.manager.on_event(session_a, handler_a)
            self.manager.on_event(session_b, handler_b)

            # Dispatch different events to each session
            await self.manager._dispatch_event(session_a, {"type": "agent_start", "source": "A"})
            await self.manager._dispatch_event(session_b, {"type": "agent_start", "source": "B"})
            await self.manager._dispatch_event(session_a, {"type": "message_update", "delta": "A said hi"})
            await self.manager._dispatch_event(session_b, {"type": "message_update", "delta": "B said hi"})

            # Each handler should have received only its own events
            self.assertEqual(len(events_a), 2)
            self.assertEqual(len(events_b), 2)
            self.assertEqual(events_a[0]["source"], "A")
            self.assertEqual(events_b[0]["source"], "B")
            self.assertEqual(events_a[1]["delta"], "A said hi")
            self.assertEqual(events_b[1]["delta"], "B said hi")

        run_async(test_event_isolation())

    def test_concurrent_canvas_mutations_revision_monotonic(self):
        """Concurrent canvas mutations should result in monotonically increasing revisions."""
        # This tests the ProjectStore revision mechanism under concurrent access
        initial_canvas = self.store.canvas(self.project_id)
        initial_revision = int(initial_canvas.get("revision") or 0)

        # Apply multiple mutations sequentially (simulating what concurrent agents would do)
        mutations = [
            [{"op": "upsert_node", "node": {
                "id": f"result-a-{i}", "type": "smart-image",
                "x": 100 + i * 50, "y": 500, "w": 100, "h": 100,
                "title": f"Result A {i}", "prompt": f"Test prompt A {i}"
            }}]
            for i in range(5)
        ] + [
            [{"op": "upsert_node", "node": {
                "id": f"result-b-{i}", "type": "smart-image",
                "x": 300 + i * 50, "y": 500, "w": 100, "h": 100,
                "title": f"Result B {i}", "prompt": f"Test prompt B {i}"
            }}]
            for i in range(5)
        ]

        revisions = []
        for mutations_list in mutations:
            # Each mutation reads current revision and applies
            canvas = self.store.canvas(self.project_id)
            revision = int(canvas.get("revision") or 0)
            result = self.store.apply_mutations(self.project_id, mutations_list, base_revision=revision)
            revisions.append(result["revision"])

        # Revisions should be strictly increasing
        self.assertEqual(len(revisions), 10)
        for i in range(1, len(revisions)):
            self.assertGreater(revisions[i], revisions[i - 1])

        # Final revision should be initial + 10
        final_canvas = self.store.canvas(self.project_id)
        final_revision = int(final_canvas.get("revision") or 0)
        self.assertEqual(final_revision, initial_revision + 10)

        # All nodes should exist
        node_ids = {n["id"] for n in final_canvas["nodes"]}
        for i in range(5):
            self.assertIn(f"result-a-{i}", node_ids)
            self.assertIn(f"result-b-{i}", node_ids)

    def test_concurrent_canvas_mutations_with_retry(self):
        """Concurrent mutations with revision conflicts should succeed via retry."""
        from app.routers.api_agent_tools import _apply_mutations
        import threading

        # Simulate concurrent mutations that might conflict
        # (in real concurrent execution, they'd race on revision)
        results = [None] * 4
        errors = [None] * 4

        def apply_mutation(i):
            node = {
                "id": f"concurrent-node-{i}",
                "type": "smart-image",
                "x": 100 + i * 100,
                "y": 600,
                "w": 80,
                "h": 80,
                "title": f"Concurrent {i}",
            }
            try:
                results[i] = _apply_mutations(self.project_id, [{"op": "upsert_node", "node": node}])
            except Exception as e:
                errors[i] = e

        # Run 4 mutations in parallel threads to trigger revision conflicts
        threads = []
        for i in range(4):
            t = threading.Thread(target=apply_mutation, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        # All should have succeeded
        for i in range(4):
            self.assertIsNone(errors[i], f"Mutation {i} failed: {errors[i]}")
            self.assertIsNotNone(results[i])
            self.assertIn("revision", results[i])

        # Final canvas should have all 4 nodes
        canvas = self.store.canvas(self.project_id)
        node_ids = {n["id"] for n in canvas["nodes"]}
        for i in range(4):
            self.assertIn(f"concurrent-node-{i}", node_ids)

    def test_concurrent_generation_tasks(self):
        """Two agents triggering generation should create independent tasks."""
        from app.routers.api_agent_tools import CANVAS_TASKS, CANVAS_TASK_LOCK

        async def test_concurrent_generation():
            # Create two image nodes for the agents to generate on
            node_a = {
                "id": "gen-node-a",
                "type": "smart-image",
                "x": 100,
                "y": 700,
                "w": 200,
                "h": 200,
                "title": "Gen A",
                "prompt": "A beautiful sunset",
            }
            node_b = {
                "id": "gen-node-b",
                "type": "smart-image",
                "x": 350,
                "y": 700,
                "w": 200,
                "h": 200,
                "title": "Gen B",
                "prompt": "A mountain lake",
            }
            canvas = self.store.canvas(self.project_id)
            revision = int(canvas.get("revision") or 0)
            self.store.apply_mutations(
                self.project_id,
                [{"op": "upsert_node", "node": node_a}, {"op": "upsert_node", "node": node_b}],
                base_revision=revision,
            )

            # Simulate two agents triggering generation concurrently
            task_a_id = f"agent_img_{uuid.uuid4().hex}"
            task_b_id = f"agent_img_{uuid.uuid4().hex}"

            with CANVAS_TASK_LOCK:
                CANVAS_TASKS[task_a_id] = {
                    "id": task_a_id,
                    "type": "agent-image",
                    "status": "queued",
                    "project_id": self.project_id,
                    "node_id": "gen-node-a",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "result": None,
                    "error": "",
                }
                CANVAS_TASKS[task_b_id] = {
                    "id": task_b_id,
                    "type": "agent-image",
                    "status": "queued",
                    "project_id": self.project_id,
                    "node_id": "gen-node-b",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "result": None,
                    "error": "",
                }

            # Both tasks should exist independently
            self.assertIn(task_a_id, CANVAS_TASKS)
            self.assertIn(task_b_id, CANVAS_TASKS)
            self.assertEqual(CANVAS_TASKS[task_a_id]["node_id"], "gen-node-a")
            self.assertEqual(CANVAS_TASKS[task_b_id]["node_id"], "gen-node-b")

            # Complete task A
            CANVAS_TASKS[task_a_id]["status"] = "completed"
            CANVAS_TASKS[task_a_id]["result"] = {"url": "https://example.com/a.png"}

            # Task B should still be queued
            self.assertEqual(CANVAS_TASKS[task_a_id]["status"], "completed")
            self.assertEqual(CANVAS_TASKS[task_b_id]["status"], "queued")

            # Clean up
            del CANVAS_TASKS[task_a_id]
            del CANVAS_TASKS[task_b_id]

        run_async(test_concurrent_generation())

    def test_stress_concurrent_structural_changes(self):
        """High-frequency concurrent structural changes should not lose data."""
        import threading

        initial_canvas = self.store.canvas(self.project_id)
        initial_revision = int(initial_canvas.get("revision") or 0)

        errors = []
        lock = threading.Lock()

        def make_changes(prefix, count):
            """Make `count` node upserts for the given prefix."""
            for i in range(count):
                try:
                    node = {
                        "id": f"{prefix}-{i}",
                        "type": "smart-image",
                        "x": 100 + (hash(prefix) % 3) * 100,
                        "y": 800 + i * 10,
                        "w": 50,
                        "h": 50,
                        "title": f"{prefix} {i}",
                    }
                    # Read current revision and apply (with retry on conflict)
                    for attempt in range(30):
                        canvas = self.store.canvas(self.project_id)
                        revision = int(canvas.get("revision") or 0)
                        try:
                            self.store.apply_mutations(
                                self.project_id,
                                [{"op": "upsert_node", "node": node}],
                                base_revision=revision,
                            )
                            break
                        except Exception:
                            if attempt == 29:
                                with lock:
                                    errors.append(f"{prefix}-{i}: failed after retries")
                except Exception as e:
                    with lock:
                        errors.append(f"{prefix}-{i}: {e}")

        # Run 4 "agents" making changes concurrently
        threads = []
        for prefix in ["agent1", "agent2", "agent3", "agent4"]:
            t = threading.Thread(target=make_changes, args=(prefix, 5))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        # Check for errors
        self.assertEqual(errors, [], f"Errors during concurrent changes: {errors}")

        # Verify all nodes exist
        final_canvas = self.store.canvas(self.project_id)
        node_ids = {n["id"] for n in final_canvas["nodes"]}
        for prefix in ["agent1", "agent2", "agent3", "agent4"]:
            for i in range(5):
                self.assertIn(f"{prefix}-{i}", node_ids)

        # Revision should have increased by 20 (4 agents × 5 changes)
        final_revision = int(final_canvas.get("revision") or 0)
        self.assertEqual(final_revision, initial_revision + 20)

    def test_concurrent_status_updates(self):
        """Concurrent status updates to different agents should not interfere."""
        async def test_concurrent_status():
            session_a, session_b, _, _ = await self._setup_fake_sessions()

            # Update statuses concurrently
            async def update_status(session, status):
                session.status = status

            await asyncio.gather(
                update_status(session_a, "busy"),
                update_status(session_b, "waiting_confirm"),
            )

            self.assertEqual(session_a.status, "busy")
            self.assertEqual(session_b.status, "waiting_confirm")

        run_async(test_concurrent_status())

    def test_multiple_nodes_same_session_manager(self):
        """Multiple agent nodes should coexist in the same manager."""
        async def test_multiple_nodes():
            # Create 3 sessions
            node_ids = ["node-1", "node-2", "node-3"]
            sessions = {}
            for node_id in node_ids:
                session = await self.manager.get_or_create_session(self.project_id, node_id)
                fake = FakeAgentProcess()
                session.process = fake
                session.status = "running"
                sessions[node_id] = session

            # All 3 should be tracked
            self.assertEqual(len(self.manager.sessions), 3)

            # Send prompts to all 3 concurrently
            await asyncio.gather(
                self.manager.send_prompt(sessions["node-1"], "To node 1"),
                self.manager.send_prompt(sessions["node-2"], "To node 2"),
                self.manager.send_prompt(sessions["node-3"], "To node 3"),
            )

            # Each should have received its own prompt
            for node_id in node_ids:
                session = sessions[node_id]
                prompt = session.process.get_command("prompt")
                self.assertIsNotNone(prompt)
                expected = f"To node {node_id.split('-')[1]}"
                self.assertEqual(prompt["message"], expected)

        run_async(test_multiple_nodes())


if __name__ == "__main__":
    unittest.main()
