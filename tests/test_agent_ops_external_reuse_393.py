import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import main
from starlette.requests import Request
from app.routers import api_mcp
from app.services import agent_ops_store as ops
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class ExternalTurnReuseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project('reuse')['id']
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node':
            {'id': 'n', 'type': 'prompt', 'text': 'original'}}], base_revision=0)
        self.store.flush(self.pid)
        self.db = OpsDb(self.store.root)
        self.enterContext(patch.object(main, 'FIREFLY_PROJECT_STORE', self.store))
        self.enterContext(patch.object(main.manager, 'broadcast_canvas_updated', new=AsyncMock()))
        self.enterContext(patch.object(api_mcp, 'resolve_project', side_effect=lambda p: p))

    async def call(self, turn, text):
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                           'query_string': f'project={self.pid}&node=actor&turn_id={turn}'.encode(), 'headers': []})
        response = await api_mcp._tools_call({'params': {'name': 'canvas.update_node',
            'arguments': {'node_id': 'n', 'text': text}}}, 1, 'same-session', request)
        result = json.loads(response.body)['result']
        self.assertFalse(result['isError'], result)
        return json.loads(result['content'][0]['text'])

    async def test_failed_turn_lookup_keeps_attribution_and_rejects_the_whole_undo(self):
        before = self.store.canvas(self.pid)
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node':
            {'id': 'n2', 'type': 'prompt', 'title': 'original'}}], base_revision=before['revision'])
        self.store.flush(self.pid)
        first = await self.call('a', 'changed text')
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                           'query_string': f'project={self.pid}&node=actor&turn_id=a'.encode(), 'headers': []})
        with patch.object(ops, 'begin_turn', side_effect=OSError('turn lookup offline')):
            response = await api_mcp._tools_call({'params': {'name': 'canvas.update_node',
                'arguments': {'node_id': 'n2', 'title': 'changed title'}}}, 2, 'same-session', request)
        result = json.loads(response.body)['result']
        self.assertFalse(result['isError'], result)
        second = json.loads(result['content'][0]['text'])
        self.assertEqual(ops.get_operation(self.db, self.pid, second['op_id'])['turn_id'], 'a')
        self.assertEqual(self.store.find_canvas_node(self.pid, 'n2')['title'], 'changed title')
        after = self.store.canvas(self.pid)
        for target in ({'turn_id': 'a'}, {'op_id': first['op_id']}, {'op_id': second['op_id']}):
            outcome = undo_ops(self.store, self.pid, **target)
            self.assertEqual(outcome['status'], 'conflict', outcome)
            self.assertIn('turn lookup failed', outcome['error'])
        self.assertEqual(self.store.canvas(self.pid), after)

    async def test_pruned_turn_reuse_is_individually_undoable(self):
        with patch.object(ops, 'MAX_OPERATIONS', 2):
            a = await self.call('a', 'a')
            await self.call('b', 'b')
            await self.call('c', 'c')
            self.assertIsNone(ops.get_operation(self.db, self.pid, a['op_id']))
            with self.assertLogs(api_mcp.logger, 'WARNING') as logs:
                pruned = await self.call('a', 'pruned-reuse')
            self.assertIn('was pruned', logs.output[0])
            self.assertNotIn('turn_reuse', pruned)
            self.assertNotIn('turn_reuse_explanation', pruned)
            self.assertIsNone(ops.get_operation(self.db, self.pid, pruned['op_id'])['turn_id'])
            self.assertEqual(undo_ops(self.store, self.pid, turn_id='a')['status'], 'not_found')
            self.assertEqual(undo_ops(self.store, self.pid, op_id=pruned['op_id'])['status'], 'reverted')
