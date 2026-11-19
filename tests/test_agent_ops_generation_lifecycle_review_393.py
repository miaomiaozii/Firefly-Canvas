import asyncio
from unittest.mock import patch

import main  # Initialize router bindings before importing them directly.
from app.routers import api_agent_tools as routes
from app.services import agent_ops_context
from app.services.agent_ops_context import agent_ops_scope
from tests import test_agent_ops_generation_route_393 as generation_tests


class GenerationLifecycleReviewTests(generation_tests.GenerationJournalTests):
    async def test_cancel_before_worker_first_step_recovers(self):
        events = []
        original = agent_ops_context.create_agent_ops_task
        cancelled = False
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs['phase'])
        def cancel_first(coro, **kwargs):
            nonlocal cancelled
            task = original(coro, **kwargs)
            if not cancelled:
                cancelled = True
                task.cancel()
            return task
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast), \
             patch.object(agent_ops_context, 'create_agent_ops_task', side_effect=cancel_first):
            result = await self.start()
            for _ in range(100):
                status = await routes.get_task_status(self.pid, result['task_id'])
                if status['status'] == 'cancelled' and events == ['start', 'end']:
                    break
                await asyncio.sleep(.02)
            self.assertEqual(status['status'], 'cancelled')
            self.assertEqual(events, ['start', 'end'])
            self.assertFalse(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))
            self.assertFalse(any(n.get('pendingTasks') for n in self.store.open_project(self.pid)['canvas']['nodes']))

    async def test_repeated_cancel_before_worker_first_step_recovers(self):
        original = agent_ops_context.create_agent_ops_task
        cancelled = 0
        def cancel_first_three(coro, **kwargs):
            nonlocal cancelled
            task = original(coro, **kwargs)
            if cancelled < 3:
                cancelled += 1
                task.cancel()
            return task
        with patch.object(agent_ops_context, 'create_agent_ops_task', side_effect=cancel_first_three):
            result = await self.start()
            for _ in range(200):
                status = await routes.get_task_status(self.pid, result['task_id'])
                if status['status'] == 'cancelled':
                    break
                await asyncio.sleep(.02)
        self.assertEqual(cancelled, 3)
        self.assertEqual(status['status'], 'cancelled')
        self.assertFalse(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))
        self.assertFalse(any(n.get('pendingTasks') for n in self.store.open_project(self.pid)['canvas']['nodes']))

    async def test_start_error_persisted_by_recovery_sends_start_before_end(self):
        events = []
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs['phase'])
        with patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast):
            with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='turn',
                                 tool='generate.trigger', args={}) as context:
                with patch.object(self.store, 'flush', side_effect=OSError('disk unavailable')):
                    with self.assertRaises(OSError) as caught:
                        await routes.trigger_generation_core(self.pid, 'source', agent_node_id='actor')
                self.assertIn(context.op_id, ' '.join(caught.exception.__notes__))
                self.assertTrue(routes.agent_generation_is_running(self.store, self.pid, context.op_id))
                self.assertEqual(events, [])
                await asyncio.wait_for(self.workers[-1], 5)
        self.assertEqual(events, ['start', 'end'])
        self.assertFalse(routes.agent_generation_is_running(self.store, self.pid, context.op_id))

    async def test_transient_cleanup_failure_retries_before_end(self):
        events = []
        original = self.store.fail_image_generation
        calls = 0
        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('transient')
            return original(*args, **kwargs)
        async def provider(_):
            raise RuntimeError('provider failed')
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs['phase'])
                if kwargs['phase'] == 'end':
                    self.assertFalse(any(n.get('pendingTasks') for n in self.store.open_project(pid)['canvas']['nodes']))
                    for record in self.records():
                        self.assertEqual(self.undo(record['op_id'])['status'], 'generating')
        with patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch.object(self.store, 'fail_image_generation', side_effect=fail_once), \
             patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast):
            result = await self.start()
            await asyncio.wait_for(self.workers[-1], 5)
        self.assertEqual(calls, 2)
        self.assertEqual(events, ['start', 'end'])
        self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'failed')
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_persistent_cleanup_failure_stays_visible_then_recovers(self):
        events = []
        blocked = True
        original = self.store.fail_image_generation
        def fail(*args, **kwargs):
            if blocked:
                raise OSError('persistent')
            return original(*args, **kwargs)
        async def provider(_):
            raise RuntimeError('provider failed')
        async def broadcast(pid, timestamp, **kwargs):
            if kwargs.get('phase'):
                events.append(kwargs['phase'])
        with patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch.object(self.store, 'fail_image_generation', side_effect=fail), \
             patch.object(routes._app_main.manager, 'broadcast_canvas_updated', side_effect=broadcast):
            result = await self.start()
            await asyncio.wait_for(self.workers[-1], 5)
            status = await routes.get_task_status(self.pid, result['task_id'])
            self.assertEqual(status['status'], 'cleanup_pending')
            self.assertEqual(status['op_id'], result['op_id'])
            self.assertIn(result['op_id'], status['error'])
            self.assertEqual(events, ['start'])
            self.assertTrue(any(n.get('pendingTasks') for n in self.store.open_project(self.pid)['canvas']['nodes']))
            for record in self.records():
                self.assertEqual(self.undo(record['op_id'])['status'], 'generating')
            blocked = False
            await asyncio.wait_for(self.workers[-1], 5)
        self.assertEqual(events, ['start', 'end'])
        self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'failed')
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_external_edit_survives_cleanup_retry(self):
        original = self.store.fail_image_generation
        blocked = True
        def fail_once(*args, **kwargs):
            if blocked:
                raise OSError('transient')
            return original(*args, **kwargs)
        async def provider(_):
            raise RuntimeError('provider failed')
        with patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch.object(self.store, 'fail_image_generation', side_effect=fail_once):
            result = await self.start()
            await asyncio.wait_for(self.workers[-1], 5)
            canvas = self.store.canvas(self.pid)
            node = next(n for n in canvas['nodes'] if n['id'] == result['result_node_id'])
            node['title'] = 'external edit'
            self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': node}], base_revision=canvas['revision'])
            self.store.flush(self.pid)
            blocked = False
            await asyncio.wait_for(self.workers[-1], 5)
        node = self.store.find_canvas_node(self.pid, result['result_node_id'])
        self.assertEqual(node['title'], 'external edit')
        self.assertFalse(node.get('pendingTasks'))
        self.assertEqual(self.undo(result['op_id'])['status'], 'conflict')
