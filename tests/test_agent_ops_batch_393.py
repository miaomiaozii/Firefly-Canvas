import asyncio
import json
import inspect
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import main
import httpx
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import api_agent_tools as routes, api_mcp
from app.services.agent_host import AgentHostManager
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_store import list_operations
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class BatchJournalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project('batch')['id']
        self.store.apply_mutations(self.pid, [
            {'op': 'upsert_node', 'node': {'id': n, 'type': 'prompt', 'prompt': 'old'}}
            for n in ('a', 'b')], base_revision=0)
        self.store.flush(self.pid)
        self.db = OpsDb(self.store.root)
        self.workers = []
        from app.services import agent_ops_context
        create = agent_ops_context.create_agent_ops_task
        def track(coro, **kwargs):
            task = create(coro, **kwargs)
            self.workers.append(task)
            return task
        self.host = AgentHostManager()
        session = await self.host.get_or_create_session(self.pid, 'actor')
        session._current_turn_id = 'actual-turn'
        patches = [patch.object(main, 'FIREFLY_PROJECT_STORE', self.store),
                   patch.object(main, 'route_image_generation', return_value=SimpleNamespace(provider_id='mock', model='mock')),
                   patch.object(main, 'load_api_providers', return_value=[]),
                   patch.object(main.manager, 'broadcast_canvas_updated', new=AsyncMock()),
                   patch.object(routes, '_server_confirm_gate', new=AsyncMock()),
                   patch.object(api_mcp, '_confirm_gate', new=AsyncMock()),
                   patch.object(api_mcp, 'resolve_project', side_effect=lambda p: p),
                   patch('app.services.agent_host.agent_host_manager', self.host),
                   patch.object(agent_ops_context, 'create_agent_ops_task', side_effect=track)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def records(self):
        return list_operations(self.db, self.pid)

    def undo(self, op):
        return undo_ops(self.store, self.pid, op_id=op,
                        is_running=lambda p, o: routes.agent_generation_is_running(self.store, p, o))

    async def call(self, ids=('a', 'b')):
        args = {'node_ids': list(ids), 'prompt': 'new'}
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='actual-turn',
                             tool='canvas.batch_edit', args=args):
            return await routes.batch_edit_core(self.pid, args['node_ids'], args['prompt'], 'actor')

    async def test_guard_all_children_and_undo_actual_root(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(_):
            entered.set()
            await release.wait()
            raise RuntimeError('provider failure')
        original = deepcopy(self.store.canvas(self.pid)['nodes'])
        with patch.object(routes, 'build_online_image_result', side_effect=provider):
            result = await self.call()
            await asyncio.wait_for(entered.wait(), 5)
            records = self.records()
            self.assertEqual(len(result['tasks']), 2)
            self.assertTrue(any(r['op_id'] == result['op_id'] for r in records))
            for record in records:
                self.assertEqual((record['turn_id'], record['agent_node_id'], record['tool']),
                                 ('actual-turn', 'actor', 'canvas.batch_edit'))
                if record['op_id'] != result['op_id']:
                    self.assertEqual(record['parent_op_id'], result['op_id'])
                self.assertEqual(self.undo(record['op_id'])['status'], 'generating')
            release.set()
            await asyncio.gather(*self.workers)
        self.assertTrue(any(r['irreversible_effects'] for r in self.records()))
        undone = self.undo(result['op_id'])
        self.assertEqual(undone['status'], 'reverted', undone)
        self.assertTrue(undone['irreversible_effects'])
        self.assertEqual(self.store.canvas(self.pid)['nodes'], original)

    async def test_partial_success_keeps_family_guarded_until_last_task(self):
        from PIL import Image
        image = Path(self.tmp.name) / 'generated.png'
        Image.new('RGB', (2, 2), 'red').save(image)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def provider(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {'image_items': [{'url': '/output/generated.png', 'mime_type': 'image/png'}]}
            entered.set()
            await release.wait()
            raise RuntimeError('second generation failed')
        with patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch.object(main, 'output_file_from_url', return_value=image):
            result = await self.call()
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(self.workers[0], 5)
            self.assertEqual((await routes.get_task_status(self.pid, result['tasks'][0]['task_id']))['status'], 'succeeded')
            for record in self.records():
                self.assertEqual(self.undo(record['op_id'])['status'], 'generating')
            release.set()
            await asyncio.gather(*self.workers)
        self.assertEqual((await routes.get_task_status(self.pid, result['tasks'][1]['task_id']))['status'], 'failed')
        undone = self.undo(result['op_id'])
        self.assertEqual(undone['status'], 'reverted', undone)
        self.assertTrue(undone['irreversible_effects'])
        self.assertEqual([n['id'] for n in self.store.canvas(self.pid)['nodes']], ['a', 'b'])

    async def test_unchanged_prompt_root_is_first_generated_node(self):
        async def provider(_):
            raise RuntimeError('failed')
        with patch.object(routes, 'build_online_image_result', side_effect=provider):
            with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='actual-turn',
                                 tool='canvas.batch_edit', args={'node_ids': ['a'], 'prompt': 'old'}):
                result = await routes.batch_edit_core(self.pid, ['a'], 'old', 'actor')
            await asyncio.gather(*self.workers)
        root = next(r for r in self.records() if r['op_id'] == result['op_id'])
        self.assertNotIn('a', root['touched_node_ids'])
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_conflict_preserves_whole_tree(self):
        async def provider(_):
            raise RuntimeError('failed')
        with patch.object(routes, 'build_online_image_result', side_effect=provider):
            result = await self.call()
            await asyncio.gather(*self.workers)
        canvas = self.store.canvas(self.pid)
        node = deepcopy(next(n for n in canvas['nodes'] if n['id'] == 'a'))
        node['prompt'] = 'human edit'
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': node}], base_revision=canvas['revision'])
        self.store.flush(self.pid)
        before, records = deepcopy(self.store.canvas(self.pid)), self.records()
        self.assertEqual(self.undo(result['op_id'])['status'], 'conflict')
        self.assertEqual(self.store.canvas(self.pid), before)
        self.assertEqual(self.records(), records)

    async def test_partial_failure_reports_root_and_started_task(self):
        async def provider(_):
            raise RuntimeError('failed')
        with patch.object(routes, 'build_online_image_result', side_effect=provider):
            with self.assertRaises(HTTPException) as caught:
                await self.call(('a', 'missing'))
            await asyncio.gather(*self.workers)
        details = caught.exception.detail['details']
        self.assertEqual(len(details['tasks']), 1)
        self.assertTrue(any(r['op_id'] == details['op_id'] for r in self.records()))
        self.assertEqual(self.undo(details['op_id'])['status'], 'reverted')

    async def test_noop_before_write_creates_no_operation(self):
        with self.assertRaises(HTTPException):
            await self.call(('missing',))
        self.assertEqual(self.records(), [])
        with patch.object(main, 'route_image_generation', side_effect=RuntimeError('route failed')):
            with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='actual-turn',
                                 tool='canvas.batch_edit', args={'node_ids': ['a'], 'prompt': 'old'}):
                with self.assertRaisesRegex(RuntimeError, 'route failed'):
                    await routes.batch_edit_core(self.pid, ['a'], 'old', 'actor')
        self.assertEqual(self.records(), [])

    async def test_flush_error_is_not_silent_and_reports_real_root(self):
        flush = self.store.flush
        def fail_after_flush(project_id):
            flush(project_id)
            raise RuntimeError('flush failed after replacement')
        with patch.object(self.store, 'flush', side_effect=fail_after_flush):
            with self.assertRaises(HTTPException) as caught:
                await self.call(('a',))
        details = caught.exception.detail['details']
        self.assertIn('flush failed after replacement', caught.exception.detail['message'])
        self.assertTrue(any(r['op_id'] == details['op_id'] for r in self.records()))
        self.assertEqual(self.undo(details['op_id'])['status'], 'reverted')

    async def test_reservation_failures_over_http_do_not_commit_unjournaled_sources(self):
        from app.services import agent_ops_store
        reserve = agent_ops_store._reserve
        for transport in ('rest', 'mcp'):
            for failure_at in (1, 2):
                with self.subTest(transport=transport, failure_at=failure_at):
                    # Each case starts from the same persisted canvas and a fresh turn.
                    calls = 0
                    release = asyncio.Event()
                    async def provider(_):
                        await release.wait()
                        raise RuntimeError('provider failed')
                    def fail_reservation(*args):
                        nonlocal calls
                        frame = inspect.currentframe().f_back
                        while frame is not None and frame.f_code.co_name != '_generation_mutation':
                            frame = frame.f_back
                        action = frame.f_locals['action'] if frame else None
                        if action is not None and 'build' in action.__code__.co_freevars:
                            calls += 1
                            if calls == failure_at:
                                raise HTTPException(status_code=500, detail='reserve failed')
                        return reserve(*args)
                    original = deepcopy(self.store.canvas(self.pid)['nodes'])
                    args = {'node_ids': ['a', 'b'], 'prompt': 'new'}
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                                 base_url='http://127.0.0.1:3000') as client:
                        with patch.object(agent_ops_store, '_reserve', side_effect=fail_reservation), \
                             patch.object(routes, 'build_online_image_result', side_effect=provider):
                            if transport == 'rest':
                                response = await client.post(f'/api/projects/{self.pid}/canvas/batch_edit?agent_node_id=actor',
                                                             headers={'X-Agent-Project': self.pid}, json=args)
                                self.assertEqual(response.status_code, 500, response.text)
                                detail = response.json()['detail']
                            else:
                                response = await client.post(f'/api/mcp?project={self.pid}&node=actor&caller=hosted',
                                    json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                          'params': {'name': 'canvas.batch_edit', 'arguments': args}})
                                payload = response.json()['result']
                                self.assertTrue(payload['isError'], payload)
                                detail = payload['structuredContent']
                            self.assertEqual(calls, failure_at)
                            nodes = {n['id']: n for n in self.store.canvas(self.pid)['nodes']}
                            self.assertEqual(nodes['b']['prompt'], 'old')
                            if failure_at == 1:
                                self.assertEqual(nodes['a']['prompt'], 'old')
                                self.assertNotIn('details', detail)
                            else:
                                partial = detail['details']
                                self.assertEqual(len(partial['tasks']), 1)
                                root = partial['op_id']
                                self.assertEqual(self.undo(root)['status'], 'generating')
                            release.set()
                            await asyncio.gather(*self.workers)
                            self.workers.clear()
                    if failure_at == 2:
                        self.assertEqual(self.undo(root)['status'], 'reverted')
                    self.assertEqual(self.store.canvas(self.pid)['nodes'], original)

    async def test_failed_sidecar_after_canvas_replacement_keeps_batch_running_but_blocks_undo(self):
        from app.services import agent_ops_store
        append = agent_ops_store.append_operation
        blocked = True
        release = asyncio.Event()
        async def provider(_):
            await release.wait()
            raise RuntimeError('provider failed')
        def fail_append(db, pid, record):
            if blocked and 'b' in record['touched_node_ids']:
                raise OSError('sidecar unavailable after canvas replacement')
            return append(db, pid, record)
        original = deepcopy(self.store.canvas(self.pid)['nodes'])
        with patch.object(agent_ops_store, 'append_operation', side_effect=fail_append), \
             patch.object(routes, 'build_online_image_result', side_effect=provider):
            try:
                result = await self.call()
                self.assertEqual(len(result['tasks']), 2)
                self.assertEqual(next(n for n in self.store.canvas(self.pid)['nodes'] if n['id'] == 'b')['prompt'], 'new')
                self.assertTrue(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))
                refused = self.undo(result['op_id'])
                self.assertEqual(refused['status'], 'conflict', refused)
                self.assertIn('sidecar append failed', refused['error'])
            finally:
                blocked = False
                release.set()
                await asyncio.wait_for(asyncio.gather(*self.workers), 10)
        after = self.store.canvas(self.pid)['nodes']
        outcome = self.undo(result['op_id'])
        self.assertEqual(outcome['status'], 'conflict', outcome)
        self.assertIn('sidecar append failed', outcome['error'])
        self.assertNotEqual(after, original)
        self.assertEqual(self.store.canvas(self.pid)['nodes'], after)

    async def test_registration_after_source_write_retries_before_parent_undo(self):
        register = routes._batch_register_reserved
        blocked = True
        release = asyncio.Event()
        async def provider(_):
            await release.wait()
            raise RuntimeError('provider failed')
        def fail_register(journal, pid, record, delta):
            if blocked and 'b' in record['touched_node_ids']:
                raise OSError('registration failed')
            return register(journal, pid, record, delta)
        original = deepcopy(self.store.canvas(self.pid)['nodes'])
        with patch.object(routes, '_batch_register_reserved', side_effect=fail_register), \
             patch.object(routes, 'build_online_image_result', side_effect=provider):
            with self.assertRaises(HTTPException) as caught:
                await self.call()
            root = caught.exception.detail['details']['op_id']
            self.assertEqual(next(n for n in self.store.canvas(self.pid)['nodes'] if n['id'] == 'b')['prompt'], 'new')
            self.assertEqual(self.undo(root)['status'], 'generating')
            blocked = False
            release.set()
            await asyncio.wait_for(asyncio.gather(*self.workers), 10)
        self.assertEqual(self.undo(root)['status'], 'reverted')
        self.assertEqual(self.store.canvas(self.pid)['nodes'], original)

    async def test_started_task_is_reported_when_trigger_broadcast_fails(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(_):
            entered.set()
            await release.wait()
            raise RuntimeError('provider failed')
        events = []
        failed = False
        async def broadcast(*args, **kwargs):
            nonlocal failed
            if kwargs.get('phase'):
                events.append(kwargs.copy())
                if kwargs['phase'] == 'start' and kwargs['touched_node_ids'] != ['a'] and not failed:
                    failed = True
                    raise RuntimeError('trigger broadcast failed')
        with patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch.object(routes, '_broadcast_canvas_updated', side_effect=broadcast):
            with self.assertRaises(HTTPException) as caught:
                await self.call(('a',))
            details = caught.exception.detail['details']
            self.assertEqual(len(details['tasks']), 1)
            task_id = details['tasks'][0]['task_id']
            await asyncio.wait_for(entered.wait(), 5)
            status = await routes.get_task_status(self.pid, task_id)
            self.assertIn(status['status'], ('queued', 'running'))
            result_node_id = main.CANVAS_TASKS[task_id]['result_node_id']
            root = next(r for r in self.records() if r['op_id'] == details['op_id'])
            child = next(r for r in self.records() if r['op_id'] == events[-1]['op_id'])
            self.assertEqual(child['parent_op_id'], root['op_id'])
            self.assertEqual(child['touched_node_ids'], [result_node_id])
            self.assertTrue(any(side['id'] == result_node_id and side['path'] == [] and not side['before']['present']
                                for side in child['before']))
            self.assertEqual(root['touched_node_ids'], ['a'])
            expected = [('start', root['op_id'], ['a']), ('end', root['op_id'], ['a']),
                        ('start', child['op_id'], [result_node_id])]
            self.assertEqual([(e['phase'], e['op_id'], e['touched_node_ids']) for e in events], expected)
            self.assertEqual(self.undo(details['op_id'])['status'], 'generating')
            release.set()
            await asyncio.gather(*self.workers)
        self.assertEqual([(e['phase'], e['op_id'], e['touched_node_ids']) for e in events],
                         expected + [('start', child['op_id'], [result_node_id]),
                                     ('end', child['op_id'], [result_node_id])])
        self.assertEqual(self.undo(details['op_id'])['status'], 'reverted')

    async def test_rest_and_hosted_mcp_use_real_turn_and_root_result(self):
        async def provider(_):
            raise RuntimeError('failed')
        args = {'node_ids': ['a'], 'prompt': 'new'}
        request = SimpleNamespace(headers={'X-Agent-Project': self.pid},
                                  query_params={'agent_node_id': 'actor'}, json=AsyncMock(return_value=args))
        mcp = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                       'query_string': urlencode({'project': self.pid, 'node': 'actor', 'caller': 'hosted'}).encode(),
                       'headers': []})
        with patch.object(routes, 'build_online_image_result', side_effect=provider):
            rest = await routes.canvas_batch_edit(self.pid, request)
            await asyncio.gather(*self.workers)
            response = await api_mcp._tools_call({'params': {'name': 'canvas.batch_edit', 'arguments': args}}, 1, 'sid', mcp)
            payload = json.loads(response.body)['result']
            self.assertFalse(payload['isError'], payload)
            result = json.loads(payload['content'][0]['text'])
            await asyncio.gather(*self.workers)
        records = {r['op_id']: r for r in self.records()}
        for response in (rest, result):
            root = records[response['op_id']]
            self.assertEqual((root['turn_id'], root['agent_node_id']), ('actual-turn', 'actor'))
            self.assertNotIn('parent_op_id', root)
