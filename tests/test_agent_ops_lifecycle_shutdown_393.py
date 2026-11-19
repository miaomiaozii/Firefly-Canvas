import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import main
from app.routers import api_agent_tools as routes
from app.services.agent_host import agent_host_manager
from tests.test_agent_ops_generation_route_393 import GenerationJournalTests


class GenerationShutdownTests(GenerationJournalTests):
    async def test_cleanup_when_active_turn_fills_retention_cap(self):
        from app.services import agent_ops_store
        async def provider(_):
            raise RuntimeError('provider failed')
        try:
            with patch.object(agent_ops_store, 'MAX_OPERATIONS', 3), \
                 patch.object(routes, 'build_online_image_result', side_effect=provider):
                result = await self.start()
                await asyncio.wait_for(self.workers[-1], 5)
                status = await routes.get_task_status(self.pid, result['task_id'])
                await routes.shutdown_agent_ops_lifetime()
                self.assertEqual(status['status'], 'failed')
                self.assertFalse(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))
        finally:
            routes.start_agent_ops_lifetime()

    async def test_shutdown_stops_retry_before_store_close_and_keeps_uncertain_guard(self):
        calls = []
        closed = False
        def fail(*args, **kwargs):
            calls.append(closed)
            if closed:
                raise AssertionError('store accessed after close')
            raise OSError('disk unavailable')
        async def provider(_):
            raise RuntimeError('provider failed')
        def close_store(method, *args):
            nonlocal closed
            self.assertEqual(method, 'close')
            closed = True
            self.store.close()
        try:
            with patch.object(routes, 'build_online_image_result', side_effect=provider), \
                 patch.object(self.store, 'fail_image_generation', side_effect=fail), \
                 patch.object(main, 'PROJECT_CONVERSATION_RUNNER', None), \
                 patch.object(main._r_api_project_library, 'shutdown_project_generation_tasks', new=AsyncMock()), \
                 patch.object(agent_host_manager, 'cleanup_all', new=AsyncMock()), \
                 patch.object(main, 'project_store_call', side_effect=close_store):
                result = await self.start()
                await asyncio.wait_for(self.workers[-1], 5)
                self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'cleanup_pending')
                self.assertTrue(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))
                await asyncio.wait_for(main.shutdown_event(), 5)
                before = len(calls)
                await asyncio.sleep(1.2)
                self.assertEqual(len(calls), before)
                self.assertTrue(closed)
                self.assertTrue(routes.agent_generation_is_running(self.store, self.pid, result['op_id']))
                self.assertEqual((await routes.get_task_status(self.pid, result['task_id']))['status'], 'cleanup_pending')
        finally:
            routes.start_agent_ops_lifetime()
