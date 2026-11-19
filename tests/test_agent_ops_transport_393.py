import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

import main  # Initialize the shared router's main-module binding first.
from app.routers import api_mcp
from app.services.agent_ops_context import current_agent_ops_context
from app.services.agent_ops_journal import AgentOpsJournal
from app.services import agent_ops_store
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class McpAttributionTests(unittest.IsolatedAsyncioTestCase):
    async def call_tool(self, query):
        request = Request({"type": "http", "method": "POST", "path": "/api/mcp",
                           "query_string": query.encode(), "headers": []})
        payload = {"params": {"name": "canvas.update_node", "arguments": {"node_id": "n"}}}
        return await api_mcp._tools_call(payload, 1, "session-not-a-turn", request)

    async def test_explicit_turn_and_absent_turn_are_request_scoped(self):
        seen = []
        async def impl(project, node, args):
            await asyncio.sleep(0)
            ctx = current_agent_ops_context()
            seen.append((ctx.project_id, ctx.agent_node_id, ctx.turn_id, ctx.tool, ctx.args, ctx.op_id))
            return {"ok": True}
        with patch.object(api_mcp, "resolve_project", return_value="project"), \
             patch.dict(api_mcp.TOOL_IMPLS, {"canvas.update_node": impl}):
            first, second = await asyncio.gather(
                self.call_tool("node=a&turn_id=logical-turn"),
                self.call_tool("node=b"),
            )
        self.assertFalse(json.loads(first.body)["result"]["isError"])
        self.assertFalse(json.loads(second.body)["result"]["isError"])
        by_node = {item[1]: item for item in seen}
        self.assertEqual(by_node["a"][:5], ("project", "a", "logical-turn", "canvas.update_node", {"node_id": "n"}))
        self.assertIsNone(by_node["b"][2])
        self.assertNotEqual(by_node["a"][5], by_node["b"][5])
        self.assertIsNone(current_agent_ops_context())

    async def test_empty_turn_rejected_before_materializing_node(self):
        async def impl(*args):
            self.fail("tool must not run")
        with patch.object(api_mcp, "resolve_project", return_value="project"), \
             patch.dict(api_mcp.TOOL_IMPLS, {"canvas.update_node": impl}), \
             patch.object(api_mcp, "ensure_external_agent_node") as materialize:
            response = await self.call_tool("turn_id=+")
        self.assertTrue(json.loads(response.body)["result"]["isError"])
        materialize.assert_not_called()


class EffectOnlyJournalTests(unittest.TestCase):
    def test_empty_delta_with_irreversible_effect_waits_for_flush(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectStore(Path(directory) / "projects", snapshot_delay=0)
            try:
                project = store.create_project("test")["id"]
                store.open_project(project)
                db = OpsDb(store.root)
                journal = AgentOpsJournal(store, db)
                try:
                    record = {"op_id": "effect", "turn_id": None, "agent_node_id": "a",
                              "tool": "media.delete", "args": {"asset_id": "x"},
                              "before": [], "after": [], "touched_node_ids": [],
                              "created_at": "2026-01-01T00:00:00Z",
                              "irreversible_effects": ["physical media deletion"], "undone": False}
                    with journal.mutation(project) as op:
                        op.register(record, [])
                    self.assertIsNone(agent_ops_store.get_operation(db, project, "effect"))
                    store.flush(project)
                    self.assertEqual(op.result()["status"], "settled")
                    self.assertEqual(agent_ops_store.get_operation(db, project, "effect"), record)
                    result = undo_ops(store, project, op_id="effect")
                    self.assertEqual(result["status"], "reverted")
                    self.assertEqual(result["irreversible_effects"], [
                        {"op_id": "effect", "effect": "physical media deletion"}])
                    record.update(op_id="noop", irreversible_effects=[])
                    with journal.mutation(project) as noop:
                        with self.assertRaises(ValueError):
                            noop.register(record, [])
                finally:
                    journal.close()
            finally:
                store.close()
