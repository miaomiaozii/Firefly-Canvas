import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import firefly_json_io
import main
from app.routers import api_agent_tools as tools
from app.services import agent_ops_store
from app.services.agent_host import AgentHostManager
from app.services.agent_ops_context import agent_ops_scope
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class DeleteFlushTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProjectStore(Path(self.temp.name) / "projects", snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.old_store = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.addCleanup(setattr, main, "FIREFLY_PROJECT_STORE", self.old_store)
        self.host = AgentHostManager()
        self.host_patch = patch('app.services.agent_host.agent_host_manager', self.host)
        self.host_patch.start()
        self.addCleanup(self.host_patch.stop)
        self.pid = self.store.create_project("delete flush")['id']
        self.canvas_path = self.store.root / self.pid / "canvas.json"
        canvas = self.store.canvas(self.pid)
        self.store.apply_mutations(self.pid, [
            {"op": "upsert_node", "node": {"id": "victim", "type": "text"}},
            {"op": "upsert_node", "node": {"id": "other", "type": "text"}},
        ], base_revision=canvas["revision"])
        self.store.flush(self.pid)
        self.counts = {(self.pid, "victim"): 3, (self.pid, "other"): 4, ("other-project", "victim"): 5}
        self.count_patch = patch.object(tools, "_GENERATION_COUNTS", self.counts)
        self.count_patch.start()
        self.addCleanup(self.count_patch.stop)
        self.addCleanup(self.close_journal)

    def close_journal(self):
        with tools._JOURNALS_LOCK:
            journal = tools._JOURNALS.pop(self.store, None)
        if journal:
            journal.close()

    def persisted_ids(self):
        canvas = json.loads(self.canvas_path.read_text(encoding="utf-8"))
        return {node["id"] for node in canvas["nodes"]}

    def operation(self, op_id):
        return agent_ops_store.get_operation(OpsDb(self.store.root), self.pid, op_id)

    def scope(self, node_id="victim"):
        return agent_ops_scope(project_id=self.pid, agent_node_id="other", turn_id="turn",
                               tool="canvas.delete_node", args={"node_id": node_id})

    def assert_remaining_counts(self):
        self.assertEqual(self.counts, {(self.pid, "other"): 4, ("other-project", "victim"): 5})

    async def test_post_replace_cleanup_failure_cleans_and_preserves_error(self):
        await self.host.get_or_create_session(self.pid, "victim")
        staging = self.canvas_path.parent / ".canvas.delete-test.tmp"
        original_staging = firefly_json_io._staging_path
        original_unlink = Path.unlink

        def staging_path(target):
            return staging if target == self.canvas_path else original_staging(target)

        def unlink(path, *args, **kwargs):
            if path == staging:
                raise OSError("postreplace cleanup")
            return original_unlink(path, *args, **kwargs)

        async def drop(pid, node):
            self.assertFalse(self.store._lock._is_owned())
            self.assertEqual((pid, node), (self.pid, "victim"))
            raise RuntimeError("session cleanup failure")

        with self.scope() as context, \
             patch.object(firefly_json_io, "_staging_path", side_effect=staging_path), \
             patch.object(Path, "unlink", unlink), \
             patch.object(self.host, "drop_node_session", new=AsyncMock(side_effect=drop)) as session:
            with self.assertRaises(HTTPException) as raised:
                await tools.delete_node_core(self.pid, "victim")
        self.assertEqual(raised.exception.status_code, 500)
        self.assertIn("postreplace cleanup", str(raised.exception.detail))
        self.assertIn("journal settled", str(raised.exception.detail))
        self.assertEqual(self.persisted_ids(), {"other"})
        self.assertEqual(self.operation(context.op_id)["tool"], "canvas.delete_node")
        session.assert_awaited_once_with(self.pid, "victim")
        self.assert_remaining_counts()
        self.assertEqual(len(self.store._persisted_canvas_listeners), 1)

    async def test_before_replace_failure_does_not_clean(self):
        await self.host.get_or_create_session(self.pid, "victim")
        directory = self.store.root / self.pid / "agent_nodes" / "victim"
        directory.mkdir(parents=True)
        (directory / "retained").write_text("important", encoding="utf-8")
        original = firefly_json_io._replace_staged

        def replace(staging, target):
            if target == self.canvas_path:
                raise OSError("before replace")
            return original(staging, target)

        with self.scope() as context, patch.object(firefly_json_io, "_replace_staged", side_effect=replace), \
             patch.object(self.host, "drop_node_session", new_callable=AsyncMock) as session:
            with self.assertRaises(HTTPException) as raised:
                await tools.delete_node_core(self.pid, "victim")
        self.assertEqual(raised.exception.status_code, 500)
        self.assertIn("before replace", str(raised.exception.detail))
        self.assertEqual(self.persisted_ids(), {"victim", "other"})
        self.assertIsNone(self.operation(context.op_id))
        session.assert_not_awaited()
        self.assertIn(f"{self.pid}:victim", self.host.sessions)
        self.assertEqual((directory / "retained").read_text(encoding="utf-8"), "important")
        self.assertEqual(self.counts[(self.pid, "victim")], 3)
        self.assertEqual(len(self.store._persisted_canvas_listeners), 1)

    async def test_noop_without_session_skips_destructive_cleanup(self):
        directory = self.store.root / self.pid / "agent_nodes" / "missing"
        directory.mkdir(parents=True)
        (directory / "retained").write_text("important", encoding="utf-8")
        with self.scope("missing") as context, \
             patch.object(self.host, "drop_node_session", new_callable=AsyncMock) as session:
            result = await tools.delete_node_core(self.pid, "missing")
        self.assertEqual(result, {"success": True, "node_id": "missing"})
        self.assertIsNone(self.operation(context.op_id))
        session.assert_not_awaited()
        self.assertEqual((directory / "retained").read_text(encoding="utf-8"), "important")
        self.assertEqual(self.counts[(self.pid, "victim")], 3)

    async def test_unrelated_delta_and_post_replace_error_do_not_clean_missing_node(self):
        canvas = self.store.canvas(self.pid)
        self.store.apply_mutations(self.pid, [{"op": "upsert_edge", "edge": {
            "id": "orphan", "from": "missing", "to": "other"}}], base_revision=canvas["revision"])
        self.store.flush(self.pid)
        staging = self.canvas_path.parent / ".canvas.delete-test.tmp"
        original_staging = firefly_json_io._staging_path
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == staging:
                raise OSError("postreplace cleanup")
            return original_unlink(path, *args, **kwargs)

        with self.scope("missing") as context, \
             patch.object(firefly_json_io, "_staging_path", side_effect=lambda target: staging if target == self.canvas_path else original_staging(target)), \
             patch.object(Path, "unlink", unlink), \
             patch.object(self.host, "drop_node_session", new_callable=AsyncMock) as session:
            with self.assertRaises(HTTPException) as raised:
                await tools.delete_node_core(self.pid, "missing")
        self.assertIn("postreplace cleanup", str(raised.exception.detail))
        self.assertIsNotNone(self.operation(context.op_id))
        self.assertEqual(self.persisted_ids(), {"victim", "other"})
        session.assert_not_awaited()
        self.assertEqual(self.counts[(self.pid, "victim")], 3)

    async def test_sidecar_error_after_replacement_skips_cleanup(self):
        await self.host.get_or_create_session(self.pid, "victim")
        directory = self.store.root / self.pid / "agent_nodes" / "victim"
        directory.mkdir(parents=True)
        (directory / "retained").write_text("important", encoding="utf-8")
        with self.scope() as context, patch.object(agent_ops_store, "append_operation", side_effect=OSError("sidecar")), \
             patch.object(self.host, "drop_node_session", new_callable=AsyncMock) as session:
            with self.assertRaises(HTTPException) as raised:
                await tools.delete_node_core(self.pid, "victim")
        self.assertEqual(raised.exception.detail["details"]["status"], "partial-unknown")
        self.assertIn("session cleanup skipped", raised.exception.detail["message"])
        self.assertEqual(self.persisted_ids(), {"other"})
        self.assertIsNone(self.operation(context.op_id))
        session.assert_not_awaited()
        self.assertIn(f"{self.pid}:victim", self.host.sessions)
        self.assertEqual((directory / "retained").read_text(encoding="utf-8"), "important")
        self.assertEqual(self.counts[(self.pid, "victim")], 3)

    async def test_cleanup_failure_does_not_mask_flush_error(self):
        await self.host.get_or_create_session(self.pid, "victim")
        original = firefly_json_io._replace_staged

        def replace(staging, target):
            if target == self.canvas_path.parent / "history.json":
                raise OSError("history flush failure")
            return original(staging, target)

        with self.scope(), patch.object(firefly_json_io, "_replace_staged", side_effect=replace), \
             patch.object(self.host, "drop_node_session", new_callable=AsyncMock) as session, \
             patch.object(tools, "reset_generation_counts", side_effect=RuntimeError("quota failure")):
            with self.assertRaises(HTTPException) as raised:
                await tools.delete_node_core(self.pid, "victim")
        self.assertIn("history flush failure", str(raised.exception.detail))
        self.assertNotIn("quota failure", str(raised.exception.detail))
        session.assert_awaited_once_with(self.pid, "victim")
        self.assertEqual(self.persisted_ids(), {"other"})

    async def test_non_journaled_success_keeps_existing_cleanup(self):
        with patch.object(self.host, "drop_node_session", new_callable=AsyncMock) as session:
            result = await tools.delete_node_core(self.pid, "victim")
        self.assertEqual(result, {"success": True, "node_id": "victim"})
        session.assert_awaited_once_with(self.pid, "victim")
        self.assert_remaining_counts()

    async def test_success_still_cleans_only_target(self):
        await self.host.get_or_create_session(self.pid, "victim")
        await self.host.get_or_create_session(self.pid, "other")
        victim_dir = self.store.root / self.pid / "agent_nodes" / "victim"
        other_dir = self.store.root / self.pid / "agent_nodes" / "other"
        for directory in (victim_dir, other_dir):
            directory.mkdir(parents=True)
            (directory / "session").write_text("important", encoding="utf-8")
        with self.scope() as context, \
             patch.object(self.host, "drop_node_session", wraps=self.host.drop_node_session) as session:
            result = await tools.delete_node_core(self.pid, "victim")
        self.assertEqual(result["op_id"], context.op_id)
        session.assert_awaited_once_with(self.pid, "victim")
        self.assertFalse(victim_dir.exists())
        self.assertTrue((other_dir / "session").exists())
        self.assertNotIn(f"{self.pid}:victim", self.host.sessions)
        self.assertIn(f"{self.pid}:other", self.host.sessions)
        self.assertEqual(self.persisted_ids(), {"other"})
        self.assertEqual(self.operation(context.op_id)["irreversible_effects"][0]["type"], "agent_session_cleanup")
        self.assert_remaining_counts()


if __name__ == "__main__":
    unittest.main()
