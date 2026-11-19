import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from fastapi import HTTPException
from starlette.requests import Request

import firefly_json_io
import main
from app.services import agent_ops_store
from app.routers import api_agent_tools as tools, api_mcp
from app.services.agent_host import AgentHostManager
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_store import get_operation, list_operations
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class DeleteSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.project = self.store.create_project('delete')['id']
        self.db = OpsDb(self.store.root)
        self.host = AgentHostManager()
        self.node('anchor')
        for p in (patch.object(main, 'FIREFLY_PROJECT_STORE', self.store),
                  patch('app.services.agent_host.agent_host_manager', self.host)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.close_journal)

    def close_journal(self):
        with tools._JOURNALS_LOCK:
            journal = tools._JOURNALS.pop(self.store, None)
        if journal:
            journal.close()

    def node(self, node_id='victim'):
        self.store.apply_mutations(self.project, [{'op': 'upsert_node', 'node': {
            'id': node_id, 'type': 'text'}}], base_revision=self.store.canvas(self.project)['revision'])
        self.store.flush(self.project)

    async def session(self, node_id='victim'):
        session = await self.host.get_or_create_session(self.project, node_id)
        directory = self.store.root / self.project / 'agent_nodes' / node_id / 'sessions'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'conversation.jsonl').write_text('important', encoding='utf-8')
        return session, directory.parent

    def scope(self, node_id='victim'):
        return agent_ops_scope(project_id=self.project, agent_node_id='actor', turn_id='turn',
                               tool='canvas.delete_node', args={'node_id': node_id})

    def check_effect(self, op_id):
        record = get_operation(self.db, self.project, op_id)
        effect = record['irreversible_effects'][0]
        self.assertEqual(effect['type'], 'agent_session_cleanup')
        self.assertIn('cannot restore', effect['impact'])
        result = undo_ops(self.store, self.project, op_id=op_id)
        self.assertEqual(result['status'], 'reverted')
        self.assertEqual(result['irreversible_effects'][0]['effect'], effect)
        return effect

    async def test_actual_session_and_files_undo_restores_canvas_only(self):
        self.node()
        _, directory = await self.session()
        with self.scope():
            result = await tools.delete_node_core(self.project, 'victim')
        self.assertFalse(directory.exists())
        effect = self.check_effect(result['op_id'])
        self.assertEqual(effect['directory_state'], 'absent after cleanup')
        self.assertEqual(effect['process_state'], 'no process before cleanup')
        self.assertEqual(effect['registration_state'], 'original session no longer registered')
        self.assertIn('victim', [node['id'] for node in self.store.canvas(self.project)['nodes']])
        self.assertFalse(directory.exists())

    async def test_absent_node_with_session_has_effect_only_operation(self):
        _, directory = await self.session()
        with self.scope():
            result = await tools.delete_node_core(self.project, 'victim')
        self.assertFalse(directory.exists())
        self.assertEqual(get_operation(self.db, self.project, result['op_id'])['before'], [])
        self.check_effect(result['op_id'])

    async def test_no_session_leaves_existing_directory_and_noop_unrecorded(self):
        directory = self.store.root / self.project / 'agent_nodes' / 'victim'
        directory.mkdir(parents=True)
        (directory / 'file').write_text('retained')
        with self.scope():
            result = await tools.delete_node_core(self.project, 'victim')
        self.assertNotIn('op_id', result)
        self.assertEqual(list_operations(self.db, self.project), [])
        self.assertTrue((directory / 'file').exists())
        self.node()
        with self.scope():
            result = await tools.delete_node_core(self.project, 'victim')
        self.assertEqual(get_operation(self.db, self.project, result['op_id'])['irreversible_effects'], [])

    async def test_running_process_is_observed_and_stopped(self):
        self.node()
        session, _ = await self.session()
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        session.process = process
        session.status = 'running'
        with self.scope():
            result = await tools.delete_node_core(self.project, 'victim')
        effect = self.check_effect(result['op_id'])
        self.assertEqual(effect['process_state_before'], 'running')
        self.assertEqual(effect['process_state'], 'exited')
        self.assertEqual(effect['session_status_before'], 'running')
        self.assertEqual(effect['session_status_after'], 'stopped')
        self.assertIsNotNone(process.poll())

    async def test_stop_failure_records_partial_state_not_removal(self):
        self.node()
        _, directory = await self.session()
        with self.scope(), patch.object(self.host, 'stop_session', new=AsyncMock(side_effect=RuntimeError('stop failed'))):
            result = await tools.delete_node_core(self.project, 'victim')
        effect = self.check_effect(result['op_id'])
        self.assertIn('partial outcome possible', effect['outcome'])
        self.assertIn('still exists', effect['directory_state'])
        self.assertTrue(directory.exists())

    async def test_ignored_rmtree_failure_is_not_reported_removed(self):
        self.node()
        _, directory = await self.session()
        with self.scope(), patch('app.services.agent_host.shutil.rmtree'):
            result = await tools.delete_node_core(self.project, 'victim')
        effect = self.check_effect(result['op_id'])
        self.assertIn('still exists', effect['directory_state'])
        self.assertTrue(directory.exists())

    async def test_rest_and_mcp_return_op_ids(self):
        self.node('rest')
        await self.session('rest')
        request = Request({'type': 'http', 'method': 'DELETE', 'path': '/',
                           'query_string': b'agent_node_id=actor',
                           'headers': [(b'x-agent-project', self.project.encode())]})
        with patch.object(tools, '_server_confirm_gate', new=AsyncMock()):
            result = await tools.delete_node(self.project, 'rest', request, agent_node_id='actor')
        self.check_effect(result['op_id'])
        self.node('mcp')
        await self.session('mcp')
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                           'query_string': urlencode({'project': self.project, 'node': 'actor'}).encode(),
                           'headers': []})
        with patch.object(api_mcp, 'resolve_project', return_value=self.project), \
             patch.object(api_mcp, '_confirm_gate', new=AsyncMock()):
            response = await api_mcp._tools_call({'params': {'name': 'canvas.delete_node',
                                                  'arguments': {'node_id': 'mcp'}}}, 1, 's', request)
        result = json.loads(response.body)['result']
        self.assertFalse(result['isError'], result)
        self.check_effect(json.loads(result['content'][0]['text'])['op_id'])

    async def test_undo_blocked_while_cleanup_is_running(self):
        self.node()
        await self.session()
        started, release = asyncio.Event(), asyncio.Event()
        original = self.host.drop_node_session

        async def paused(*args):
            started.set()
            await release.wait()
            return await original(*args)

        with self.scope() as context, patch.object(self.host, 'drop_node_session', side_effect=paused):
            task = asyncio.create_task(tools.delete_node_core(self.project, 'victim'))
            await started.wait()
            pending = get_operation(self.db, self.project, context.op_id)
            self.assertIn('unknown', pending['irreversible_effects'][0]['outcome'])
            self.assertEqual(undo_ops(self.store, self.project, op_id=context.op_id,
                                      is_running=lambda p, o: tools.agent_generation_is_running(self.store, p, o))['status'],
                             'generating')
            release.set()
            await task
        self.check_effect(context.op_id)

    async def test_sidecar_failure_after_canvas_replace_skips_destructive_cleanup(self):
        self.node()
        _, directory = await self.session()
        with self.scope(), patch.object(agent_ops_store, 'append_operation', side_effect=OSError('sidecar')):
            with self.assertRaises(HTTPException):
                await tools.delete_node_core(self.project, 'victim')
        self.assertTrue(directory.exists())
        self.assertIn(f'{self.project}:victim', self.host.sessions)
        self.assertNotIn('victim', [n['id'] for n in self.store.canvas(self.project)['nodes']])

    async def test_post_replace_flush_error_still_records_cleanup(self):
        self.node()
        _, directory = await self.session()
        original = firefly_json_io._replace_staged

        def replace(staging, target):
            if target == self.store.root / self.project / 'history.json':
                raise OSError('history failure')
            return original(staging, target)

        with self.scope() as context, patch.object(firefly_json_io, '_replace_staged', side_effect=replace):
            with self.assertRaises(HTTPException):
                await tools.delete_node_core(self.project, 'victim')
        self.assertFalse(directory.exists())
        self.check_effect(context.op_id)

    async def test_final_effect_write_failure_retains_uncertainty(self):
        self.node()
        _, directory = await self.session()
        original = tools._delete_session_effect

        def fail_final(*args, **kwargs):
            if not kwargs['create']:
                raise OSError('update failed')
            return original(*args, **kwargs)

        with self.scope() as context, patch.object(tools, '_delete_session_effect', side_effect=fail_final):
            with self.assertRaises(HTTPException) as error:
                await tools.delete_node_core(self.project, 'victim')
        self.assertEqual(error.exception.detail['details']['op_id'], context.op_id)
        self.assertFalse(directory.exists())
        self.assertIn('unknown', self.check_effect(context.op_id)['outcome'])

    async def test_preflight_failure_does_not_destroy_session(self):
        self.node()
        _, directory = await self.session()
        with self.scope(), patch.object(tools, '_delete_session_effect', side_effect=OSError('sidecar')):
            with self.assertRaises(HTTPException) as error:
                await tools.delete_node_core(self.project, 'victim')
            self.assertEqual(error.exception.detail['details']['status'], 'partial-unknown')
        self.assertTrue(directory.exists())
        self.assertIn(f'{self.project}:victim', self.host.sessions)
