import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main  # Initialize router bindings before importing them directly.
from app.routers import api_agent_tools as routes, api_mcp
from app.services.agent_ops_context import agent_ops_scope
from starlette.requests import Request
from tests import test_agent_ops_generation_route_393 as generation_tests


class GenerationBroadcastTests(generation_tests.GenerationJournalTests):
    async def test_blocked_provider_lifecycle_and_guard(self):
        entered, release = asyncio.Event(), asyncio.Event()
        events = []
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs.copy())
                self.assertTrue(routes.agent_generation_is_running(self.store, pid, kwargs['op_id']))
                self.assertIsNotNone(self.store.find_canvas_node(pid, kwargs['touched_node_ids'][0]))
        async def provider(_):
            self.assertEqual([e['phase'] for e in events], ['start'])
            entered.set()
            await release.wait()
            raise RuntimeError('provider failed')
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
             patch.object(routes, 'build_online_image_result', side_effect=provider):
            result = await self.start()
            await asyncio.wait_for(entered.wait(), 5)
            self.assertEqual(events[0]['op_id'], result['op_id'])
            self.assertEqual(events[0]['touched_node_ids'], [result['result_node_id']])
            release.set()
            await asyncio.wait_for(self.workers[-1], 5)
        self.assertEqual([e['phase'] for e in events], ['start', 'end'])
        self.assertEqual(events[0]['op_id'], events[1]['op_id'])
        self.assertEqual(events[0]['touched_node_ids'], events[1]['touched_node_ids'])
        self.assertFalse(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))
        self.assertFalse(any(n.get('pendingTasks') for n in self.store.canvas(self.pid)['nodes']))

    async def test_success_and_cancel_after_write(self):
        for cancel in (False, True):
            events = []
            entered, release = threading.Event(), threading.Event()
            media = self.store.import_media(self.pid, b'image', 'test.png', 'image/png')
            complete = self.store.complete_image_generation
            def blocked(*args, **kwargs):
                result = complete(*args, **kwargs)
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('test timeout')
                return result
            async def provider(_):
                return {'image_items': [{'url': 'mock://image'}]}
            async def archive(*_):
                return {'asset_id': media['asset_id'], 'kind': 'image'}
            async def broadcast(pid, timestamp, **kwargs):
                if kwargs.get('phase'):
                    events.append(kwargs.copy())
            with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
                 patch.object(routes, 'build_online_image_result', side_effect=provider), \
                 patch('app.services.archive_project_generated_image', side_effect=archive), \
                 patch.object(self.store, 'complete_image_generation', side_effect=blocked):
                result = await self.start()
                worker = self.workers[-1]
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                if cancel:
                    worker.cancel()
                    await asyncio.sleep(.05)
                    self.assertFalse(worker.done())
                release.set()
                if cancel:
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(worker, 5)
                else:
                    await asyncio.wait_for(worker, 5)
            self.assertEqual([(e['phase'], e['op_id'], e['touched_node_ids']) for e in events],
                             [('start', result['op_id'], [result['result_node_id']]),
                              ('end', result['op_id'], [result['result_node_id']])])
            self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'succeeded')

    async def test_start_after_write_error_resyncs_and_validation_does_not_call_provider(self):
        events = []
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs.copy())
        flush = self.store.flush
        calls = 0
        def injected(pid):
            nonlocal calls
            calls += 1
            result = flush(pid)
            if calls == 1:
                raise RuntimeError('after write')
            return result
        provider = AsyncMock()
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
             patch.object(routes, 'build_online_image_result', provider), \
             patch.object(self.store, 'flush', side_effect=injected):
            with self.assertRaisesRegex(RuntimeError, 'after write'):
                await self.start()
        provider.assert_not_awaited()
        self.assertEqual([e['phase'] for e in events], ['start', 'end'])
        self.assertEqual(events[0]['op_id'], events[1]['op_id'])
        self.assertEqual(events[0]['touched_node_ids'], events[1]['touched_node_ids'])
        events.clear()
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
             patch.object(routes, 'build_online_image_result', provider):
            with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='turn',
                                 tool='generate.trigger', args={}):
                with self.assertRaises(Exception):
                    await routes.trigger_generation_core(self.pid, 'missing', agent_node_id='actor')
        self.assertEqual(events, [])
        provider.assert_not_awaited()

    async def test_unwritten_root_has_no_lifecycle(self):
        events = []
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs)
        provider = AsyncMock()
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
             patch.object(routes, 'build_online_image_result', provider), \
             patch.object(self.store, 'apply_mutations', side_effect=RuntimeError('before write')):
            with self.assertRaisesRegex(RuntimeError, 'before write'):
                await self.start()
        self.assertEqual(events, [])
        provider.assert_not_awaited()
        self.assertEqual([n['id'] for n in self.store.canvas(self.pid)['nodes']], ['source'])

    async def test_guard_is_held_until_end_delivery_finishes(self):
        end_entered, end_release = asyncio.Event(), asyncio.Event()
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase') == 'end':
                end_entered.set()
                await end_release.wait()
        async def provider(_):
            raise RuntimeError('failure')
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
             patch.object(routes, 'build_online_image_result', side_effect=provider):
            result = await self.start()
            await asyncio.wait_for(end_entered.wait(), 5)
            self.assertTrue(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))
            end_release.set()
            await asyncio.wait_for(self.workers[-1], 5)
        self.assertFalse(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))

    async def test_terminal_write_then_journal_error_still_resyncs(self):
        from app.services import agent_ops_store
        events = []
        append = agent_ops_store.append_operation
        calls = 0
        def injected(*args, **kwargs):
            nonlocal calls
            calls += 1
            record = args[2]
            if any(side.get('path') and side['path'][0] == 'pendingTasks'
                   for side in record['after']) and calls > 2:
                raise OSError('terminal journal error')
            return append(*args, **kwargs)
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs.copy())
        async def provider(_):
            raise RuntimeError('provider failure')
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
             patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch.object(agent_ops_store, 'append_operation', side_effect=injected):
            result = await self.start()
            await asyncio.wait_for(self.workers[-1], 5)
            self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'failed')
        self.assertEqual(self.undo(result['op_id'])['status'], 'conflict')
        self.assertEqual([e['phase'] for e in events], ['start', 'end'])
        self.assertTrue(all(e['op_id'] == result['op_id'] and
                            e['touched_node_ids'] == [result['result_node_id']] for e in events))
        self.assertFalse(any(n.get('pendingTasks') for n in self.store.open_project(self.pid)['canvas']['nodes']))
        self.assertFalse(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))

    async def test_rest_and_mcp_publish_matching_lifecycles(self):
        events = []
        release = asyncio.Event()
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs.copy())
        async def provider(_):
            await release.wait()
            raise RuntimeError('failed')
        request = SimpleNamespace(headers={'X-Agent-Project': self.pid},
                                  query_params={'agent_node_id': 'actor'},
                                  json=AsyncMock(return_value={'node_id': 'source'}))
        mcp = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                       'query_string': f'project={self.pid}&node=actor&turn_id=turn'.encode(), 'headers': []})
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
             patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch.object(routes, '_server_confirm_gate', new=AsyncMock()), \
             patch.object(api_mcp, '_confirm_gate', new=AsyncMock()), \
             patch.object(api_mcp, 'resolve_project', side_effect=lambda p: p):
            rest = await routes.trigger_generation(self.pid, request)
            response = await api_mcp._tools_call({'params': {'name': 'generate.trigger',
                'arguments': {'node_id': 'source'}}}, 1, 'session', mcp)
            payload = json.loads(response.body)['result']
            self.assertFalse(payload['isError'], payload)
            mcp_result = json.loads(payload['content'][0]['text'])
            release.set()
            await asyncio.gather(*self.workers)
        for result in (rest, mcp_result):
            lifecycle = [e for e in events if e['op_id'] == result['op_id']]
            self.assertEqual([e['phase'] for e in lifecycle], ['start', 'end'])
            self.assertTrue(all(e['touched_node_ids'] == [result['result_node_id']] for e in lifecycle))

    async def test_batch_generation_publishes_parent_and_creation_child(self):
        events = []
        release = asyncio.Event()
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs.copy())
        async def provider(_):
            await release.wait()
            raise RuntimeError('failed')
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
             patch.object(routes, 'build_online_image_result', side_effect=provider):
            with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='turn',
                                 tool='canvas.batch_edit', args={}):
                result = await routes.batch_edit_core(self.pid, ['source'], 'changed', 'actor')
            root = next(r for r in self.records() if r['op_id'] == result['op_id'])
            task = result['tasks'][0]
            result_node_id = main.CANVAS_TASKS[task['task_id']]['result_node_id']
            child = next(r for r in self.records() if r['op_id'] == events[-1]['op_id'])
            self.assertEqual(child['parent_op_id'], root['op_id'])
            self.assertEqual(child['touched_node_ids'], [result_node_id])
            self.assertTrue(any(side['id'] == result_node_id and side['path'] == [] and not side['before']['present']
                                for side in child['before']))
            self.assertEqual(root['touched_node_ids'], ['source'])
            expected = [('start', root['op_id'], ['source']),
                        ('end', root['op_id'], ['source']),
                        ('start', child['op_id'], [result_node_id])]
            self.assertEqual([(e['phase'], e['op_id'], e['touched_node_ids']) for e in events], expected)
            release.set()
            await asyncio.gather(*self.workers)
        self.assertEqual([(e['phase'], e['op_id'], e['touched_node_ids']) for e in events],
                         expected + [('end', child['op_id'], [result_node_id])])
