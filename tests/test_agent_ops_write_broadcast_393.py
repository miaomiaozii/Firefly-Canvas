import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

import main
from app.routers import api_agent_tools as tools, api_mcp
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_store import get_operation
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class WriteBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project('isolated')['id']
        self.db = OpsDb(self.store.root)
        p = patch.object(main, 'FIREFLY_PROJECT_STORE', self.store)
        p.start()
        self.addCleanup(p.stop)
        self.broadcast = AsyncMock()
        p = patch.object(main.manager, 'broadcast_canvas_updated', self.broadcast)
        p.start()
        self.addCleanup(p.stop)

    def check_pair(self, op_id, touched):
        calls = self.broadcast.await_args_list
        self.assertEqual(len(calls), 2)
        for call, phase in zip(calls, ('start', 'end')):
            self.assertEqual(call.args[0], self.pid)
            self.assertIsInstance(call.args[1], int)
            self.assertEqual(call.kwargs, {'source': 'agent', 'op_id': op_id,
                                           'touched_node_ids': touched, 'phase': phase})

    async def test_rest_and_mcp_real_store(self):
        body = json.dumps({'agent_node_id': 'actor', 'node': {'type': 'prompt'}}).encode()
        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}
        req = Request({'type': 'http', 'method': 'POST', 'path': '/', 'query_string': b'',
                       'headers': [(b'x-agent-project', self.pid.encode())]}, receive)
        result = await tools.create_node(self.pid, req)
        record = get_operation(self.db, self.pid, result['op_id'])
        self.assertEqual(result['touched_node_ids'], record['touched_node_ids'])
        self.check_pair(result['op_id'], record['touched_node_ids'])
        self.broadcast.reset_mock()
        req = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                       'query_string': b'node=actor&turn_id=turn', 'headers': []})
        payload = {'params': {'name': 'canvas.update_node', 'arguments': {
            'node_id': result['node_id'], 'updates': {'text': 'changed'}}}}
        with patch.object(api_mcp, 'resolve_project', return_value=self.pid):
            response = await api_mcp._tools_call(payload, 1, 'session', req)
        content = json.loads(response.body)['result']
        self.assertFalse(content['isError'], content)
        data = json.loads(content['content'][0]['text'])
        record = get_operation(self.db, self.pid, data['op_id'])
        self.assertEqual(data['touched_node_ids'], record['touched_node_ids'])
        self.check_pair(data['op_id'], record['touched_node_ids'])

    async def test_noop_and_validation_never_broadcast(self):
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='canvas.create_node', args={}):
            created = await tools.create_node_core(self.pid, {'type': 'prompt', 'text': 'same'})
        self.broadcast.reset_mock()
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='canvas.update_node', args={}):
            await tools.update_node_core(self.pid, created['node_id'], {'text': 'same'})
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='canvas.update_node', args={}):
            with self.assertRaises(Exception):
                await tools.update_node_core(self.pid, created['node_id'], {'not_allowed': 1})
        self.broadcast.assert_not_awaited()

    async def test_sidecar_failure_still_broadcasts_persisted_delta(self):
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='canvas.create_node', args={}) as ctx:
            with patch('app.services.agent_ops_store.append_operation', side_effect=OSError('sidecar')):
                result = await tools.create_node_core(self.pid, {'type': 'prompt'})
        self.assertEqual(result['op_id'], ctx.op_id)
        canvas = json.loads((self.store.root / self.pid / 'canvas.json').read_text(encoding='utf-8'))
        node_id = canvas['nodes'][0]['id']
        self.check_pair(ctx.op_id, [node_id])

    async def test_edges_and_delete_use_exact_delta(self):
        nodes = []
        for _ in range(2):
            with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='canvas.create_node', args={}):
                nodes.append((await tools.create_node_core(self.pid, {'type': 'prompt'}))['node_id'])
        for tool, invoke in (
            ('canvas.connect', lambda: tools.create_edge_core(self.pid, {'from': nodes[0], 'to': nodes[1]})),
            ('canvas.disconnect', lambda: tools.delete_edge_core(self.pid, *nodes)),
            ('canvas.delete_node', lambda: tools.delete_node_core(self.pid, nodes[0])),
        ):
            self.broadcast.reset_mock()
            with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool=tool, args={}):
                result = await invoke()
            record = get_operation(self.db, self.pid, result['op_id'])
            expected = sorted(nodes) if tool != 'canvas.delete_node' else [nodes[0]]
            self.assertEqual(record['touched_node_ids'], expected)
            self.assertEqual(result['touched_node_ids'], expected)
            self.check_pair(result['op_id'], expected)

    async def test_precommit_failure_closes_started_operation(self):
        import firefly_json_io
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='canvas.create_node', args={}) as ctx:
            with patch.object(firefly_json_io, '_replace_staged', side_effect=OSError('precommit')):
                with self.assertRaises(Exception):
                    await tools.create_node_core(self.pid, {'type': 'prompt'})
        start, end = self.broadcast.await_args_list
        self.assertEqual(start.kwargs['phase'], 'start')
        self.assertEqual(len(start.kwargs['touched_node_ids']), 1)
        self.assertEqual(end.kwargs, {'source': 'agent', 'op_id': ctx.op_id,
                                      'touched_node_ids': [], 'phase': 'end'})

    async def test_start_is_before_mutation_and_end_after_persistence(self):
        observed = []
        async def capture(*args, **kwargs):
            disk = json.loads((self.store.root / self.pid / 'canvas.json').read_text(encoding='utf-8'))
            observed.append((kwargs['phase'], self.store.canvas(self.pid)['nodes'], disk['nodes']))
        self.broadcast.side_effect = capture
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='canvas.create_node', args={}):
            result = await tools.create_node_core(self.pid, {'type': 'prompt'})
        self.assertEqual(observed[0], ('start', [], []))
        self.assertEqual(observed[1][0], 'end')
        self.assertEqual(observed[1][1][0]['id'], result['node_id'])
        self.assertEqual(observed[1][2][0]['id'], result['node_id'])

    async def test_cancel_during_start_closes_without_mutation(self):
        started = asyncio.Event()
        async def capture(*args, **kwargs):
            if kwargs['phase'] == 'start':
                started.set()
                await asyncio.Event().wait()
        self.broadcast.side_effect = capture
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='canvas.create_node', args={}) as ctx:
            task = asyncio.create_task(tools.create_node_core(self.pid, {'type': 'prompt'}))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.broadcast.await_args_list[-1].kwargs,
                         {'source': 'agent', 'op_id': ctx.op_id,
                          'touched_node_ids': [], 'phase': 'end'})
        self.assertEqual(self.store.canvas(self.pid)['nodes'], [])

    async def test_concurrent_write_after_start_rejects_stale_preview(self):
        async def capture(*args, **kwargs):
            if kwargs['phase'] == 'start':
                self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
                    'id': 'concurrent', 'type': 'prompt'}}], base_revision=0)
                self.store.flush(self.pid)
        self.broadcast.side_effect = capture
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='canvas.create_node', args={}):
            with self.assertRaises(Exception):
                await tools.create_node_core(self.pid, {'type': 'prompt'})
        self.assertEqual([n['id'] for n in self.store.canvas(self.pid)['nodes']], ['concurrent'])
        self.assertEqual([c.kwargs['phase'] for c in self.broadcast.await_args_list], ['start', 'end'])
        self.assertEqual(self.broadcast.await_args_list[-1].kwargs['touched_node_ids'], [])

    async def test_generation_response_includes_root_and_child_touched_nodes(self):
        from app.services import agent_ops_context
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
            'id': 'source', 'type': 'prompt', 'text': 'draw',
            'runSettings': {'provider_id': 'mock'}}}], base_revision=0)
        self.store.flush(self.pid)
        workers = []
        create = agent_ops_context.create_agent_ops_task
        def track(coro, **kwargs):
            task = create(coro, **kwargs)
            workers.append(task)
            return task
        async def fail(_):
            raise RuntimeError('provider failure')
        from tests.agent_image_task_fakes import patch_candidate, route_stub, stub_providers
        with patch.object(main, 'route_image_generation', return_value=route_stub()), \
             patch.object(main, 'load_api_providers', return_value=stub_providers()), patch_candidate(fail), \
             patch.object(agent_ops_context, 'create_agent_ops_task', side_effect=track):
            with agent_ops_scope(project_id=self.pid, agent_node_id='actor', tool='generate.trigger', args={}):
                result = await tools.trigger_generation_core(self.pid, 'source', agent_node_id='actor')
            self.assertEqual(result['touched_node_ids'], [result['result_node_id']])
            await asyncio.wait_for(workers[-1], 5)

    async def test_browser_retains_legacy_format(self):
        result = await tools.create_node_core(self.pid, {'type': 'prompt'})
        self.assertNotIn('op_id', result)
        self.assertEqual(self.broadcast.await_count, 1)
        self.assertEqual(self.broadcast.await_args.kwargs, {'source': 'agent'})
