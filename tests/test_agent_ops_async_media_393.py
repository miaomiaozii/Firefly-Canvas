import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from app.routers import api_agent_tools as routes
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_store import list_operations
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class AgentGenerationRegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / "projects", snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.project = self.store.create_project("test")['id']
        self.main_patch = patch.object(main, "FIREFLY_PROJECT_STORE", self.store)
        self.main_patch.start()
        self.addCleanup(self.main_patch.stop)

    async def test_unchanged_update_is_not_an_operation(self):
        self.store.apply_mutations(self.project, [{'op': 'upsert_node', 'node':
            {'id': 'actor', 'type': 'smart-agent'}}], base_revision=self.store.canvas(self.project)['revision'])
        with agent_ops_scope(project_id=self.project, agent_node_id="actor", tool="canvas.create_node", args={}):
            node = await routes.create_node_core(self.project, {"type": "prompt", "text": "same"}, agent_node_id="actor")
        before = self.store.canvas(self.project)
        with agent_ops_scope(project_id=self.project, agent_node_id="actor", tool="canvas.update_node", args={}):
            result = await routes.update_node_core(self.project, node["node_id"], {"text": "same"})
        self.assertNotIn("op_id", result)
        self.assertEqual(before, self.store.canvas(self.project))
        self.assertEqual(len(list_operations(OpsDb(self.store.root), self.project)), 1)
        with agent_ops_scope(project_id=self.project, agent_node_id="actor", tool="canvas.update_node", args={}):
            changed = await routes.update_node_core(self.project, node["node_id"], {"text": "different"})
        self.assertIn("op_id", changed)
        self.assertEqual(len(list_operations(OpsDb(self.store.root), self.project)), 2)

    async def test_registry_guards_undo_and_tracks_all_tasks(self):
        with agent_ops_scope(project_id=self.project, agent_node_id="actor", tool="canvas.create_node", args={}):
            result = await routes.create_node_core(self.project, {"type": "prompt"})
        op_id = result["op_id"]
        for task in ("one", "two"):
            routes.set_agent_generation_running(self.store, self.project, op_id, task, True)
        is_running = lambda project_id, operation_id: routes.agent_generation_is_running(self.store, project_id, operation_id)
        result = undo_ops(self.store, self.project, op_id=op_id, is_running=is_running)
        self.assertEqual(result["status"], "generating")
        routes.set_agent_generation_running(self.store, self.project, op_id, "one", False)
        self.assertTrue(is_running(self.project, op_id))
        self.assertFalse(is_running("other-project", op_id))
        routes.set_agent_generation_running(self.store, self.project, op_id, "two", False)
        self.assertEqual(undo_ops(self.store, self.project, op_id=op_id, is_running=is_running)["status"], "reverted")

    async def test_registry_uses_store_lock(self):
        started, completed = threading.Event(), threading.Event()

        def register():
            started.set()
            routes.set_agent_generation_running(self.store, self.project, "op", "task", True)
            completed.set()

        with self.store._lock:
            thread = threading.Thread(target=register)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(completed.wait(.05))
            self.assertFalse(routes.agent_generation_is_running(self.store, self.project, "op"))
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(routes.agent_generation_is_running(self.store, self.project, "op"))
