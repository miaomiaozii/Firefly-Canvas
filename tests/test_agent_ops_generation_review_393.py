import asyncio
import threading
from unittest.mock import patch

from tests import test_agent_ops_generation_route_393 as generation_tests
from tests.agent_image_task_fakes import patch_archive, patch_candidate
from tests.test_agent_ops_store import record
from app.routers import api_agent_tools as routes
from app.services import agent_ops_store
from app.services.agent_ops_context import agent_ops_scope


class GenerationReviewTests(generation_tests.GenerationJournalTests):
    async def test_active_family_survives_concurrent_retention(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(_):
            entered.set()
            await release.wait()
            raise RuntimeError('done')
        with patch_candidate(provider):
            result = await self.start()
            await asyncio.wait_for(entered.wait(), 5)
            count = len(self.records())
            with patch.object(agent_ops_store, 'MAX_OPERATIONS', count):
                await asyncio.to_thread(agent_ops_store.append_operation, self.db, self.pid,
                                        record('other', 'other-turn'))
                self.assertTrue({result['op_id'], 'other'} <= {r['op_id'] for r in self.records()})
            release.set()
            await asyncio.wait_for(self.workers[-1], 5)
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_begin_post_replace_error_cleans_pending_and_preserves_exception(self):
        flush = self.store.flush
        calls = 0
        def injected(pid):
            nonlocal calls
            calls += 1
            result = flush(pid)
            if calls == 2:
                raise RuntimeError('post-replacement failure')
            return result
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='turn',
                             tool='generate.trigger', args={'node_id': 'source'}) as context:
            with patch.object(self.store, 'flush', side_effect=injected):
                with self.assertRaisesRegex(RuntimeError, 'post-replacement failure'):
                    await routes.trigger_generation_core(self.pid, 'source', agent_node_id='actor')
        self.assertTrue(any(r['op_id'] == context.op_id for r in self.records()))
        nodes = self.store.canvas(self.pid)['nodes']
        self.assertFalse(any(n.get('pendingTasks') for n in nodes))
        self.assertEqual([h['status'] for h in self.store.open_project(self.pid)['canvas']['history']], ['failed'])
        self.assertTrue(any(r.get('parent_op_id') == context.op_id and
                            any(c['path'] and c['path'][0] == 'pendingTasks' for c in r['after'])
                            for r in self.records()))
        self.assertEqual(self.undo(context.op_id)['status'], 'reverted')

    async def test_begin_mutates_then_raises_is_journaled_and_cleaned(self):
        begin = self.store.begin_image_generation
        def injected(*args, **kwargs):
            begin(*args, **kwargs)
            raise RuntimeError('begin after mutation')
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='turn',
                             tool='generate.trigger', args={'node_id': 'source'}) as context:
            with patch.object(self.store, 'begin_image_generation', side_effect=injected):
                with self.assertRaisesRegex(RuntimeError, 'begin after mutation'):
                    await routes.trigger_generation_core(self.pid, 'source', agent_node_id='actor')
        self.assertFalse(any(n.get('pendingTasks') for n in self.store.canvas(self.pid)['nodes']))
        self.assertEqual([h['status'] for h in self.store.open_project(self.pid)['canvas']['history']], ['failed'])
        self.assertGreaterEqual(len(self.records()), 3)
        self.assertEqual(self.undo(context.op_id)['status'], 'reverted')

    async def test_cleanup_failure_retains_guard_and_original_exception(self):
        flush = self.store.flush
        calls = 0
        def injected(pid):
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise RuntimeError('persistent flush failure')
            return flush(pid)
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='turn',
                             tool='generate.trigger', args={'node_id': 'source'}) as context:
            with patch.object(self.store, 'flush', side_effect=injected):
                with self.assertRaisesRegex(RuntimeError, 'persistent flush failure') as caught:
                    await routes.trigger_generation_core(self.pid, 'source', agent_node_id='actor')
        self.assertIn(context.op_id, ' '.join(caught.exception.__notes__))
        self.assertTrue(routes.agent_generation_is_running(self.store, self.pid, context.op_id))
        self.assertIn(self.undo(context.op_id)['status'], ('pending', 'generating'))
        await asyncio.wait_for(self.workers[-1], 5)
        self.assertFalse(any(n.get('pendingTasks') for n in self.store.canvas(self.pid)['nodes']))
        self.assertEqual(self.undo(context.op_id)['status'], 'reverted')

    async def test_null_turn_family_survives_retention(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(_):
            entered.set()
            await release.wait()
            raise RuntimeError('done')
        with patch_candidate(provider):
            with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id=None,
                                 tool='generate.trigger', args={'node_id': 'source'}):
                result = await routes.trigger_generation_core(self.pid, 'source', agent_node_id='actor')
            await asyncio.wait_for(entered.wait(), 5)
            with patch.object(agent_ops_store, 'MAX_OPERATIONS', len(self.records())):
                await asyncio.to_thread(agent_ops_store.append_operation, self.db, self.pid,
                                        record('other', 'other-turn'))
                self.assertTrue({result['op_id'], 'other'} <= {r['op_id'] for r in self.records()})
            release.set()
            await asyncio.wait_for(self.workers[-1], 5)
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_complete_mutates_then_raises_keeps_one_success(self):
        media = self.store.import_media(self.pid, b'image', 'image.png', 'image/png')
        complete = self.store.complete_image_generation
        def injected(*args, **kwargs):
            complete(*args, **kwargs)
            raise RuntimeError('complete after mutation')
        async def provider(_):
            return {'image_items': [{'url': 'mock://image'}]}
        async def archive(*args):
            return {'asset_id': media['asset_id'], 'kind': 'image'}
        with patch_candidate(provider), patch_archive(archive), \
             patch.object(self.store, 'complete_image_generation', side_effect=injected):
            result = await self.start()
            await asyncio.wait_for(self.workers[-1], 5)
        history = self.store.open_project(self.pid)['canvas']['history']
        self.assertEqual([(h['task_id'], h['status']) for h in history], [(result['task_id'], 'succeeded')])
        self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'succeeded')
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_cancel_complete_has_one_terminal_history(self):
        entered, release = threading.Event(), threading.Event()
        media = self.store.import_media(self.pid, b'image', 'image.png', 'image/png')
        complete = self.store.complete_image_generation
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return complete(*args, **kwargs)
        async def provider(_):
            return {'image_items': [{'url': 'mock://image'}]}
        async def archive(*args):
            return {'asset_id': media['asset_id'], 'kind': 'image'}
        with patch_candidate(provider), patch_archive(archive), \
             patch.object(self.store, 'complete_image_generation', side_effect=blocked):
            result = await self.start()
            worker = self.workers[-1]
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            worker.cancel()
            await asyncio.sleep(.05)
            self.assertFalse(worker.done())
            self.assertIn((self.pid, result['op_id']), self.store._agent_generation_tasks)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(worker, 5)
        self.assertEqual([h['status'] for h in self.store.open_project(self.pid)['canvas']['history']], ['succeeded'])
        self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'succeeded')
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')
