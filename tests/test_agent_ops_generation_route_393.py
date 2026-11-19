import asyncio
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import main
from app.routers import api_agent_tools as routes, api_mcp
from starlette.requests import Request
from app.services.agent_ops_context import agent_ops_scope, current_agent_ops_context
from app.services.agent_ops_store import list_operations
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class GenerationJournalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.pid = self.store.create_project('generation')['id']
        self.store.apply_mutations(self.pid, [{'op': 'upsert_node', 'node': {
            'id': 'source', 'type': 'prompt', 'text': 'draw'}}], base_revision=0)
        self.store.flush(self.pid)
        self.enterContext(patch.object(main, 'FIREFLY_PROJECT_STORE', self.store))
        self.enterContext(patch.object(main, 'route_image_generation', return_value=SimpleNamespace(provider_id='mock', model='mock')))
        self.enterContext(patch.object(main, 'load_api_providers', return_value=[]))
        self.enterContext(patch.object(main.manager, 'broadcast_canvas_updated', new=AsyncMock()))
        self.db = OpsDb(self.store.root)
        self.workers = []
        from app.services import agent_ops_context
        create = agent_ops_context.create_agent_ops_task
        def track(coro, **kwargs):
            task = create(coro, **kwargs)
            self.workers.append(task)
            return task
        self.enterContext(patch.object(agent_ops_context, 'create_agent_ops_task', side_effect=track))

    def records(self):
        return list_operations(self.db, self.pid)

    def undo(self, op_id):
        return undo_ops(self.store, self.pid, op_id=op_id,
                        is_running=lambda p, o: routes.agent_generation_is_running(self.store, p, o))

    async def start(self):
        with agent_ops_scope(project_id=self.pid, agent_node_id='actor', turn_id='turn',
                             tool='generate.trigger', args={'node_id': 'source'}):
            return await routes.trigger_generation_core(self.pid, 'source', agent_node_id='actor')

    async def test_blocked_provider_guards_every_visible_child_and_root_undo(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(_):
            self.assertEqual(current_agent_ops_context().turn_id, 'turn')
            entered.set()
            await release.wait()
            return {'image_items': [{'url': 'mock://image'}], 'provider_id': 'mock', 'model': 'mock'}
        media = self.store.import_media(self.pid, b'image', 'image.png', 'image/png')
        async def archive(*args):
            return {'asset_id': media['asset_id'], 'kind': 'image'}
        with patch.object(routes, 'build_online_image_result', side_effect=provider), patch('app.services.archive_project_generated_image', side_effect=archive):
            result = await self.start()
            await asyncio.wait_for(entered.wait(), 5)
            records = self.records()
            self.assertGreaterEqual(len(records), 3)
            before = deepcopy(self.store.canvas(self.pid))
            for record in records:
                self.assertEqual(record['turn_id'], 'turn')
                if record['op_id'] != result['op_id']:
                    self.assertEqual(record['parent_op_id'], result['op_id'])
                self.assertEqual(self.undo(record['op_id'])['status'], 'generating')
                self.assertEqual(self.store.canvas(self.pid), before)
            release.set()
            await asyncio.wait_for(self.workers[-1], 5)
        self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'succeeded')
        self.assertTrue(any(r['irreversible_effects'] for r in self.records()))
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')
        self.assertEqual([n['id'] for n in self.store.canvas(self.pid)['nodes']], ['source'])

    async def test_real_archive_and_generation_root_undo(self):
        from PIL import Image
        image = Path(self.tmp.name) / 'generated.png'
        Image.new('RGB', (2, 2), 'red').save(image)
        async def provider(_):
            return {'image_items': [{'url': '/output/generated.png', 'mime_type': 'image/png'}],
                    'provider_id': 'mock', 'model': 'mock'}
        with patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch.object(main, 'output_file_from_url', return_value=image):
            result = await self.start()
            await asyncio.wait_for(self.workers[-1], 10)
        task = await routes.get_task_status(self.pid, result['task_id'])
        self.assertEqual(task['status'], 'succeeded', task)
        self.assertTrue(task['result']['project_asset_ids'])
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')
        self.assertEqual([n['id'] for n in self.store.canvas(self.pid)['nodes']], ['source'])

    async def test_sidecar_append_failure_does_not_block_provider_or_terminal_status(self):
        from app.services import agent_ops_store

        provider = AsyncMock(side_effect=RuntimeError('mock provider failed'))
        with patch.object(routes, 'build_online_image_result', provider), patch.object(
            agent_ops_store, 'append_operation', side_effect=OSError('Agent journal offline')
        ), self.assertLogs('app.services.agent_ops_journal', 'WARNING'):
            result = await self.start()
            await asyncio.wait_for(self.workers[0], 5)
            provider.assert_awaited_once()
            self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'failed')
        self.assertEqual(result['touched_node_ids'], [result['result_node_id']])
        self.assertFalse(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))
        self.assertEqual(self.undo(result['op_id'])['status'], 'conflict')
        self.assertEqual(self.records(), [])

    async def test_provider_failure_records_failure_and_releases_guards(self):
        async def provider(_):
            raise RuntimeError('provider failed')
        with patch.object(routes, 'build_online_image_result', side_effect=provider):
            result = await self.start()
            await asyncio.wait_for(self.workers[-1], 5)
        self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'failed')
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_cancellation_does_not_release_guard_before_thread_finishes(self):
        entered, release = threading.Event(), threading.Event()
        original = self.store.fail_image_generation
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return original(*args, **kwargs)
        async def provider(_):
            raise RuntimeError('provider failed')
        with patch.object(routes, 'build_online_image_result', side_effect=provider), patch.object(self.store, 'fail_image_generation', side_effect=blocked):
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
        self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'cancelled')
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_cancellation_during_completion_waits_for_journal_settlement(self):
        entered, release = threading.Event(), threading.Event()
        media = self.store.import_media(self.pid, b'image', 'image.png', 'image/png')
        original = self.store.complete_image_generation
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return original(*args, **kwargs)
        async def provider(_):
            return {'image_items': [{'url': 'mock://image'}]}
        async def archive(*args):
            return {'asset_id': media['asset_id'], 'kind': 'image'}
        with patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch('app.services.archive_project_generated_image', side_effect=archive), \
             patch.object(self.store, 'complete_image_generation', side_effect=blocked):
            result = await self.start()
            worker = self.workers[-1]
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            worker.cancel()
            await asyncio.sleep(.05)
            self.assertFalse(worker.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(worker, 5)
        self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'succeeded')
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')
        self.assertEqual([n['id'] for n in self.store.canvas(self.pid)['nodes']], ['source'])

    async def test_cancellation_waits_for_archive_before_failure_cleanup(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(_):
            return {'image_items': [{'url': 'mock://image'}]}
        async def archive(*args):
            entered.set()
            await release.wait()
            return {'asset_id': 'unused', 'kind': 'image'}
        with patch.object(routes, 'build_online_image_result', side_effect=provider), patch('app.services.archive_project_generated_image', side_effect=archive):
            result = await self.start()
            worker = self.workers[-1]
            await asyncio.wait_for(entered.wait(), 5)
            worker.cancel()
            await asyncio.sleep(.05)
            self.assertFalse(worker.done())
            self.assertEqual(self.undo(result['op_id'])['status'], 'generating')
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(worker, 5)
        self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'cancelled')
        self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_rest_and_mcp_propagate_caller_turn(self):
        async def provider(_):
            raise RuntimeError('provider failed')
        request = SimpleNamespace(headers={'X-Agent-Project': self.pid},
                                  query_params={'agent_node_id': 'actor'},
                                  json=AsyncMock(return_value={'node_id': 'source'}))
        mcp_request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                               'query_string': f'project={self.pid}&node=actor&turn_id=mcp-turn'.encode(),
                               'headers': []})
        with patch.object(routes, 'build_online_image_result', side_effect=provider), \
             patch.object(routes, '_server_confirm_gate', new=AsyncMock()), \
             patch.object(api_mcp, '_confirm_gate', new=AsyncMock()), \
             patch.object(api_mcp, 'resolve_project', side_effect=lambda p: p), \
             patch('app.services.agent_host.agent_host_manager.get_current_turn_id', return_value='rest-turn'):
            rest = await routes.trigger_generation(self.pid, request)
            response = await api_mcp._tools_call({'params': {'name': 'generate.trigger',
                'arguments': {'node_id': 'source'}}}, 1, 'session', mcp_request)
            mcp = json.loads(response.body)['result']
            self.assertFalse(mcp['isError'], mcp)
            mcp = json.loads(mcp['content'][0]['text'])
            await asyncio.gather(*self.workers)
        records = {r['op_id']: r for r in self.records()}
        for result, turn in ((rest, 'rest-turn'), (mcp, 'mcp-turn')):
            root = records[result['op_id']]
            self.assertEqual((root['turn_id'], root['agent_node_id']), (turn, 'actor'))
            children = [r for r in records.values() if r.get('parent_op_id') == result['op_id']]
            self.assertTrue(children)
            self.assertTrue(all(r['turn_id'] == turn for r in children))
            self.assertEqual(self.undo(result['op_id'])['status'], 'reverted')

    async def test_cancelled_start_keeps_worker_and_guards(self):
        entered, release = threading.Event(), threading.Event()
        provider_entered, provider_release = asyncio.Event(), asyncio.Event()
        begin = self.store.begin_image_generation
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return begin(*args, **kwargs)
        async def provider(_):
            provider_entered.set()
            await provider_release.wait()
            raise RuntimeError('done')
        with patch.object(self.store, 'begin_image_generation', side_effect=blocked), patch.object(routes, 'build_online_image_result', side_effect=provider):
            starter = asyncio.create_task(self.start())
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            starter.cancel()
            await asyncio.sleep(.05)
            self.assertFalse(starter.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(starter, 5)
            await asyncio.wait_for(provider_entered.wait(), 5)
            root = next(r for r in self.records() if 'parent_op_id' not in r)
            self.assertEqual(self.undo(root['op_id'])['status'], 'generating')
            provider_release.set()
            await asyncio.wait_for(self.workers[-1], 5)
        self.assertEqual(self.undo(root['op_id'])['status'], 'reverted')
