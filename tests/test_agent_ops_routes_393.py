import asyncio
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from starlette.requests import Request

import main
from app.routers import api_agent_tools as routes, api_mcp
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_store import list_operations
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class RouteJournalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.project = self.store.create_project('synthetic')['id']
        self.patch = patch.object(main, 'FIREFLY_PROJECT_STORE', self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.db = OpsDb(self.store.root)

    def records(self):
        return list_operations(self.db, self.project)

    async def test_mcp_actual_dispatch_and_undo(self):
        req = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                       'query_string': b'node=actor&turn_id=turn-1', 'headers': []})
        payload = {'params': {'name': 'canvas.create_node', 'arguments': {'type': 'prompt', 'text': 'hello'}}}
        with patch.object(api_mcp, 'resolve_project', return_value=self.project):
            response = await api_mcp._tools_call(payload, 1, 'session', req)
        result = json.loads(response.body)['result']
        self.assertFalse(result['isError'], result)
        data = json.loads(result['content'][0]['text'])
        record, = self.records()
        self.assertEqual((record['op_id'], record['turn_id'], record['agent_node_id'], record['tool']),
                         (data['op_id'], 'turn-1', 'actor', 'canvas.create_node'))
        self.assertIn(data['node_id'], record['touched_node_ids'])
        persisted = json.loads((self.store.root / self.project / 'canvas.json').read_text(encoding='utf-8'))
        self.assertTrue(any(n['id'] == data['node_id'] for n in persisted['nodes']))
        from app.services.agent_ops_undo import undo_ops
        undo = undo_ops(self.store, self.project, op_id=data['op_id'])
        self.assertEqual(undo['status'], 'reverted')
        self.assertFalse(any(n['id'] == data['node_id'] for n in self.store.canvas(self.project)['nodes']))

    async def test_rest_creation_turn_from_host(self):
        body = json.dumps({'agent_node_id': 'actor', 'node': {'type': 'prompt'}}).encode()
        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'query_string': b'',
                           'headers': [(b'x-agent-project', self.project.encode())]}, receive)
        with patch('app.services.agent_host.agent_host_manager.get_current_turn_id', return_value='real-turn') as turn:
            result = await routes.create_node(self.project, request)
        turn.assert_called_once_with(self.project, 'actor')
        record, = self.records()
        self.assertEqual((record['op_id'], record['turn_id'], record['agent_node_id']),
                         (result['op_id'], 'real-turn', 'actor'))

    async def test_rest_agent_without_identity_fails_before_mutation(self):
        body = b'{"text":"changed"}'
        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}
        request = Request({'type': 'http', 'method': 'PATCH', 'path': '/', 'query_string': b'',
                           'headers': [(b'x-agent-project', self.project.encode())]}, receive)
        with self.assertRaises(Exception) as error:
            await routes.update_node(self.project, 'missing', request)
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(error.exception.detail['code'], 'INVALID_ARGUMENT')
        self.assertEqual(self.records(), [])

    async def test_threaded_mutations_capture_only_their_own_changes(self):
        barrier = threading.Barrier(4)
        local = threading.local()
        broadcast = routes._broadcast_canvas_updated

        async def synchronize_first_previews(*args, **kwargs):
            # All four previews must precede any commit to exercise the supported CAS conflict.
            if kwargs.get('phase') == 'start' and not getattr(local, 'started', False):
                local.started = True
                barrier.wait(timeout=10)
            await broadcast(*args, **kwargs)

        def run(i):
            async def invoke():
                for attempt in range(16):
                    try:
                        with agent_ops_scope(project_id=self.project, agent_node_id=f'actor-{i}',
                                             turn_id=f'turn-{i}', tool='canvas.create_node', args={'index': i}):
                            result = await routes.create_node_core(self.project, {'type': 'prompt', 'text': str(i)})
                        return result, attempt
                    except HTTPException as exc:
                        if (exc.status_code != 409 or exc.detail.get('code') != 'CANVAS_CONFLICT'
                                or exc.detail.get('message') != 'Canvas changed after the operation was announced'):
                            raise
                raise AssertionError(f'conflicts exhausted retries for actor-{i}')
            return asyncio.run(invoke())

        with patch.object(routes, '_broadcast_canvas_updated', synchronize_first_previews):
            with ThreadPoolExecutor(max_workers=4) as pool:
                outcomes = list(pool.map(run, range(8)))
        results = [result for result, _ in outcomes]
        self.assertGreaterEqual(sum(attempts for _, attempts in outcomes), 3)
        records = self.records()
        self.assertEqual(len(records), 8)
        self.assertEqual({r['op_id'] for r in records}, {r['op_id'] for r in results})
        by_op = {r['op_id']: r for r in records}
        for i, result in enumerate(results):
            record = by_op[result['op_id']]
            self.assertEqual((record['agent_node_id'], record['turn_id'], record['args']),
                             (f'actor-{i}', f'turn-{i}', {'index': i}))
            self.assertEqual(record['touched_node_ids'], [result['node_id']])

    async def test_sidecar_failure_returns_canvas_success_but_disables_undo(self):
        from app.services.agent_ops_undo import undo_ops
        with agent_ops_scope(project_id=self.project, agent_node_id='actor', tool='canvas.create_node', args={}) as ctx:
            with patch('app.services.agent_ops_store.append_operation', side_effect=OSError('database unavailable')):
                result = await routes.create_node_core(self.project, {'type': 'prompt'})
        self.assertEqual(result['op_id'], ctx.op_id)
        self.assertEqual(result['touched_node_ids'], [result['node_id']])
        self.assertEqual(self.records(), [])
        undone = undo_ops(self.store, self.project, op_id=ctx.op_id)
        self.assertEqual(undone['status'], 'conflict')
        self.assertIn('sidecar append failed', undone['error'])
        self.assertEqual(len(self.store.canvas(self.project)['nodes']), 1)

    async def test_agent_media_delete_nonexistent_asset_is_noop(self):
        with agent_ops_scope(project_id=self.project, agent_node_id='actor', tool='media.delete', args={}):
            with patch.object(self.store, 'delete_media', wraps=self.store.delete_media) as delete:
                result = await routes.delete_media_core(self.project, 'missing')
        self.assertEqual(result, {'success': True, 'asset_id': 'missing'})
        delete.assert_not_called()
        self.assertEqual(self.records(), [])

    async def test_agent_media_delete_journal_failure_preserves_recycle_but_disables_undo(self):
        image = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(image, format='PNG')
        asset = self.store.import_media(self.project, image.getvalue(), 'source.png', 'image/png')
        asset_id = asset['asset_id']
        before = self.store.media_record(self.project, asset_id)
        path = Path(before['path'])
        content = path.read_bytes()
        from app.services.agent_ops_undo import undo_ops
        with agent_ops_scope(project_id=self.project, agent_node_id='actor', tool='media.delete', args={}) as context:
            with patch('app.services.agent_ops_store.append_operation', side_effect=OSError('sidecar unavailable')):
                with patch.object(self.store, 'recycle_agent_media', wraps=self.store.recycle_agent_media) as recycle:
                    result = await routes.delete_media_core(self.project, asset_id)
        self.assertEqual(result['op_id'], context.op_id)
        recycle.assert_called_once()
        self.assertFalse(path.exists())
        trash = self.store.list_deleted_media(self.project)[0]
        self.assertEqual((self.store.root / self.project / 'trash/media' / trash['media_filename']).read_bytes(), content)
        self.assertEqual(self.records(), [])
        self.assertEqual(undo_ops(self.store, self.project, op_id=context.op_id)['status'], 'conflict')
        self.store.restore_trash_media(self.project, asset_id, base_revision=self.store.canvas(self.project)['revision'])
        self.assertEqual(self.store.media_record(self.project, asset_id), before)
        self.assertEqual(path.read_bytes(), content)
