import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.services import agent_ops_delta, agent_ops_store
from app.services.agent_ops_journal import AgentOpsJournal, record_sides
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.previous = main.FIREFLY_PROJECT_STORE
        main.FIREFLY_PROJECT_STORE = self.store
        self.addCleanup(setattr, main, 'FIREFLY_PROJECT_STORE', self.previous)
        self.pid = self.store.create_project('surface')['id']
        self.other = self.store.create_project('other')['id']
        for pid in (self.pid, self.other):
            path = OpsDb(self.store.root).db_path(pid)
            if path.exists():
                path.unlink()
        self.tab = object()
        register_project_session(self.tab, self.pid)
        self.addCleanup(unregister_project_session, self.tab)
        self.client = TestClient(main.app, base_url='http://127.0.0.1:8124')
        self.base = f'/api/projects/{self.pid}/ops'
        self.journal = AgentOpsJournal(self.store, OpsDb(self.store.root))
        self.addCleanup(self.journal.close)

    def mcp(self, name, args):
        response = self.client.post(f'/api/mcp?project={self.pid}', json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': args}})
        return response.json()['result']

    def create_op(self):
        with self.journal.mutation(self.pid) as operation:
            before = self.store.open_project(self.pid)['canvas']
            self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
                'id': 'n', 'type': 'text'}}], base_revision=before['revision'])
            after = self.store.open_project(self.pid)['canvas']
            delta = agent_ops_delta.build_delta(before, after)
            old, new = record_sides(delta)
            operation.register({'op_id': 'op', 'turn_id': 'turn', 'agent_node_id': 'agent',
                                'tool': 'test', 'args': {}, 'before': old, 'after': new,
                                'touched_node_ids': ['n'], 'created_at': '2026-01-01',
                                'irreversible_effects': [], 'undone': False}, delta)
        self.store.flush(self.pid)
        self.assertEqual(operation.result()['status'], 'settled')

    def test_list_is_lazy_and_project_scoped(self):
        self.assertEqual(self.client.get(self.base).json(), {'operations': []})
        self.assertEqual(json.loads(self.mcp('ops.list', {})['content'][0]['text']), {'operations': []})
        self.assertFalse(OpsDb(self.store.root).has_db(self.pid))
        self.create_op()
        self.assertEqual(self.client.get(f'/api/projects/{self.other}/ops').json(), {'operations': []})
        self.assertEqual(self.client.get(self.base, params={'turn_id': 'turn'}).json()['operations'][0]['op_id'], 'op')
        self.assertEqual(self.client.get(self.base, params={'turn_id': 'wrong'}).json(), {'operations': []})
        self.assertFalse(OpsDb(self.store.root).has_db(self.other))

    def test_cold_project_read_does_not_create_database(self):
        self.store.close()
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        main.FIREFLY_PROJECT_STORE = self.store
        self.assertEqual(self.client.get(self.base).json(), {'operations': []})
        self.assertFalse(OpsDb(self.store.root).has_db(self.pid))

    def test_selectors_and_conflict_are_failures_on_both_transports(self):
        for args in ({}, {'op_id': 'a', 'turn_id': 'b'}, {'op_id': ''}, {'op_id': None}, {'other': 'x'}):
            self.assertEqual(self.client.post(self.base + '/undo', json=args).status_code, 400)
            self.assertTrue(self.mcp('ops.undo', args)['isError'])
        unsupported = self.mcp('ops.undo', {'turn_id': None})
        self.assertIn('不支持', unsupported['structuredContent']['message'])
        for limit in (0, -1, 1001, True, 'abc'):
            self.assertEqual(self.client.get(self.base, params={'limit': limit}).status_code, 400)
            self.assertTrue(self.mcp('ops.list', {'limit': limit})['isError'])
        self.create_op()
        canvas = self.store.open_project(self.pid)['canvas']
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
            'id': 'n', 'type': 'text', 'title': 'human'}}], base_revision=canvas['revision'])
        rest = self.client.post(self.base + '/undo', json={'op_id': 'op'})
        self.assertEqual(rest.status_code, 409)
        mcp = self.mcp('ops.undo', {'op_id': 'op'})
        self.assertTrue(mcp['isError'])
        self.assertEqual(rest.json()['detail'], mcp['structuredContent'])
        self.assertEqual(rest.json()['detail']['code'], 'UNDO_CONFLICT')

    def test_success_undo_broadcast_and_repeat(self):
        self.create_op()
        with patch.object(main.manager, 'broadcast_canvas_updated', new_callable=AsyncMock) as broadcast:
            result = self.client.post(self.base + '/undo', json={'op_id': 'op'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['reverted'], ['op'])
            args, kwargs = broadcast.await_args
            self.assertEqual(args[0], self.pid)
            self.assertEqual(kwargs, {'source': 'agent', 'op_id': 'op', 'touched_node_ids': ['n'], 'phase': 'undo'})
            repeat = self.mcp('ops.undo', {'op_id': 'op'})
            self.assertFalse(repeat['isError'])
            self.assertEqual(json.loads(repeat['content'][0]['text'])['already_reverted'], ['op'])
            self.assertEqual(broadcast.await_count, 1)


if __name__ == '__main__':
    unittest.main()
