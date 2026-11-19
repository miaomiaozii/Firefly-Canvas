import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from starlette.requests import Request

import main
from app.routers import api_agent_nodes, api_mcp
from app.services.agent_host import AgentHostManager
from app.services.agent_ops_context import current_agent_ops_context
from app.services.agent_ops_store import list_operations
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


class McpCallerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name) / 'projects', snapshot_delay=0)
        self.addCleanup(self.store.close)
        self.project = self.store.create_project('first')['id']
        self.other = self.store.create_project('second')['id']
        self.db = OpsDb(self.store.root)
        self.host = AgentHostManager()
        for project, turn in ((self.project, 'turn-first'), (self.other, 'turn-second')):
            session = await self.host.get_or_create_session(project, 'actor')
            session._current_turn_id = turn
        for target in (patch.object(main, 'FIREFLY_PROJECT_STORE', self.store),
                       patch('app.services.agent_host.agent_host_manager', self.host),
                       patch.object(api_mcp, 'resolve_project', side_effect=lambda p: p)):
            target.start()
            self.addCleanup(target.stop)

    async def call(self, query, name='canvas.create_node', args=None):
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                           'query_string': urlencode(query).encode(), 'headers': []})
        response = await api_mcp._tools_call(
            {'params': {'name': name, 'arguments': args if args is not None else {'type': 'prompt'}}},
            1, 'session-not-turn', request)
        return json.loads(response.body)['result']

    def query(self, **kwargs):
        return {'project': self.project, 'node': 'actor', **kwargs}

    async def test_concurrent_actual_writes_and_spoofed_marker(self):
        queries = [self.query(caller='hosted'), self.query(), self.query(caller='external'),
                   self.query(turn_id='explicit'), self.query(caller='hosted'),
                   {'project': self.other, 'node': 'actor', 'caller': 'hosted'}]
        results = await asyncio.gather(*(self.call(q, args={'type': 'prompt', 'text': str(i)})
                                         for i, q in enumerate(queries)))
        for result in results:
            self.assertFalse(result['isError'], result)
        data = [json.loads(r['content'][0]['text']) for r in results]
        records = {r['op_id']: r for p in (self.project, self.other)
                   for r in list_operations(self.db, p)}
        self.assertEqual([records[d['op_id']]['turn_id'] for d in data],
                         ['turn-first', None, None, 'explicit', 'turn-first', 'turn-second'])
        self.assertEqual([records[d['op_id']]['agent_node_id'] for d in data], ['actor'] * 6)
        # The second hosted claim is deliberately indistinguishable from a spoof.
        self.assertEqual(records[data[4]['op_id']]['turn_id'], 'turn-first')

    async def test_external_never_queries_host_even_for_same_node(self):
        async def capture(project, node, args):
            return {'turn': current_agent_ops_context().turn_id}

        with patch.dict(api_mcp.TOOL_IMPLS, {'canvas.update_node': capture}), \
             patch.object(self.host, 'get_current_turn_id', side_effect=AssertionError('must not query host')):
            for query, expected in ((self.query(), None), (self.query(caller='external'), None),
                                    (self.query(turn_id='explicit'), 'explicit')):
                result = await self.call(query, 'canvas.update_node', {})
                self.assertFalse(result['isError'], result)
                self.assertEqual(json.loads(result['content'][0]['text'])['turn'], expected)

    async def test_invalid_inputs_and_no_turn_reject_before_write(self):
        bad = [self.query(caller='invalid'), self.query(caller=''),
               self.query(caller='hosted', turn_id='manual'), self.query(caller='hosted', turn_id=''),
               self.query(turn_id=' '), {'project': self.project, 'caller': 'hosted'},
               self.query(node=' ', caller='hosted'), self.query(node='missing', caller='hosted')]
        for query in bad:
            with self.subTest(query=query):
                result = await self.call(query)
                self.assertTrue(result['isError'], result)
                self.assertEqual(result['structuredContent']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(list_operations(self.db, self.project), [])
        self.assertEqual(self.store.canvas(self.project)['nodes'], [])

    async def test_reads_and_explicit_undo_without_active_turn(self):
        created = await self.call(self.query(turn_id='explicit'))
        op_id = json.loads(created['content'][0]['text'])['op_id']
        for name, args in (('canvas.list_nodes', {}), ('ops.list', {})):
            result = await self.call(self.query(node='missing', caller='hosted'), name, args)
            self.assertFalse(result['isError'], result)
        result = await self.call(self.query(node='missing', caller='hosted'), 'ops.undo', {'op_id': op_id})
        self.assertFalse(result['isError'], result)
        self.assertEqual(json.loads(result['content'][0]['text'])['status'], 'reverted')
        result = await self.call(self.query(node='missing', caller='hosted'), 'ops.undo', {})
        self.assertTrue(result['isError'])
        result = await self.call(self.query(node='missing', caller='hosted'), 'ops.undo', {'turn_id': None})
        self.assertTrue(result['isError'])
        created = await self.call(self.query(turn_id='second-explicit'))
        self.assertFalse(created['isError'], created)
        result = await self.call(self.query(node='missing', caller='hosted'), 'ops.undo', {'turn_id': 'second-explicit'})
        self.assertFalse(result['isError'], result)
        self.assertEqual(json.loads(result['content'][0]['text'])['status'], 'reverted')

    async def test_steering_retry_and_next_turn_attribution(self):
        session = await self.host.get_or_create_session(self.project, 'steerer')
        session.status = 'running'
        self.host._collect_reference_images = lambda session: ([], [])
        self.host._persist_event = lambda *args, **kwargs: None
        self.host.send_command = AsyncMock()
        await self.host.send_prompt(session, 'first')
        first = session._current_turn_id
        seen = []

        async def capture(project, node, args):
            seen.append(current_agent_ops_context().turn_id)
            return {}

        async def write():
            result = await self.call(self.query(node='steerer', caller='hosted'), 'canvas.update_node', {})
            self.assertFalse(result['isError'], result)

        with patch.dict(api_mcp.TOOL_IMPLS, {'canvas.update_node': capture}):
            await write()
            await self.host.send_steer(session, 'steer')
            await self.host._dispatch_event(session, {'type': 'agent_end', 'willRetry': True})
            await self.host._dispatch_event(session, {'type': 'auto_retry_start'})
            await self.host._dispatch_event(session, {'type': 'auto_retry_end', 'success': True})
            await write()
            await self.host._dispatch_event(session, {'type': 'agent_end'})
            await self.host.send_prompt(session, 'next')
            await write()
        self.assertEqual(seen[:2], [first, first])
        self.assertNotEqual(seen[2], first)


class McpUrlTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_spawn_and_resume_use_hosted_url_in_argv_and_config(self):
        manager = AgentHostManager()
        with tempfile.TemporaryDirectory() as directory:
            manager._get_project_dir = lambda project: directory
            session = await manager.get_or_create_session('project', 'actor')
            session.agent_cli = 'codex'
            expected = 'http://127.0.0.1:3123/api/mcp?project=project&node=actor&caller=hosted'
            home = str(Path(directory) / 'home')
            with patch.dict('os.environ', {'FIREFLY_AGENT_API_BASE': 'http://127.0.0.1:3123', 'CODEX_HOME': home}), \
                 patch('app.services.agent_host.shutil.which', return_value='codex'), \
                 patch('app.services.agent_host.finalize_spawn_argv', side_effect=lambda cmd: cmd), \
                 patch('app.services.agent_host.subprocess.Popen', side_effect=OSError('stop before spawn')) as popen:
                for binding in (None, 'thread-1'):
                    session.ext_session_id = binding
                    with self.assertRaisesRegex(RuntimeError, 'stop before spawn'):
                        await manager._codex_turn(session, 'hello')
                    argv = popen.call_args.args[0]
                    self.assertIn(f'mcp_servers.firefly.url="{expected}"', argv)
                    self.assertEqual('resume' in argv, binding is not None)
                    self.assertIn(f'url = "{expected}"', Path(home, 'config.toml').read_text(encoding='utf-8'))

    async def test_pi_spawn_and_external_snippet_remain_unmarked(self):
        manager = AgentHostManager()
        with tempfile.TemporaryDirectory() as directory:
            manager._get_project_dir = lambda project: directory
            session = await manager.get_or_create_session('project', 'actor')
            adapter = manager._adapters['pi']
            original = adapter.build_argv
            captured = []

            def capture(params):
                captured.append((params.mcp_url, original(params)))
                return None

            with patch.dict('os.environ', {'FIREFLY_AGENT_API_BASE': 'http://127.0.0.1:3123'}), \
                 patch('app.services.agent_host.resolve_pi_cli', return_value='pi'), \
                 patch('app.services.agent_host.build_pi_launch_command', side_effect=lambda cli, args: [cli, *args]), \
                 patch.object(adapter, 'build_argv', side_effect=capture), \
                 patch.object(api_agent_nodes, '_find_node', return_value={}):
                self.assertFalse(await manager.spawn_session(session))
                result = await api_agent_nodes.external_cli_snippet('project', 'actor')
            url = 'http://127.0.0.1:3123/api/mcp?project=project&node=actor'
            self.assertEqual(captured[0][0], url)
            self.assertTrue(all('caller=hosted' not in arg for arg in captured[0][1]))
            self.assertEqual(result['mcp_url'], url)
            self.assertIn(url, result['codex_cmd'])
            self.assertIn(url, result['claude_cmd'])
