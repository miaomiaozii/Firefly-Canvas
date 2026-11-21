import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services import agent_host
from app.services.agent_host import AgentHostManager


@pytest.fixture(autouse=True)
def isolated_host(monkeypatch, tmp_path):
    # No application bootstrap (or journal) is needed for queue unit tests.
    monkeypatch.setattr(agent_host, '_app_main', SimpleNamespace(FIREFLY_PROJECT_STORE=None))
    monkeypatch.setattr(AgentHostManager, '_get_project_dir', lambda self, project_id: str(tmp_path / project_id))


@pytest.fixture(autouse=True)
def isolated_event_loop_policy():
    # IsolatedAsyncioTestCase clears the current loop; preserve the caller's policy and loop.
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


class LogicalTurnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = AgentHostManager()
        self.session = await self.manager.get_or_create_session('project', 'node')
        self.session.status = 'running'
        self.sent = []
        self.events = []
        self.manager._collect_reference_images = lambda session: ([], [])
        self.manager._persist_event = lambda session, event, **kw: self.events.append(dict(event))

        async def send(session, command):
            self.sent.append(command)
            await asyncio.sleep(0)

        self.manager.send_command = send

    async def end(self, **fields):
        await self.manager._dispatch_event(self.session, {'type': 'agent_end', **fields})

    async def test_turn_lookup_and_steering(self):
        self.assertIsNone(self.manager.get_current_turn_id('project', None))
        self.assertIsNone(self.manager.get_current_turn_id('project', 'other'))
        await self.manager.send_prompt(self.session, 'hello')
        turn = self.manager.get_current_turn_id('project', 'node')
        UUID(turn)
        await self.manager.send_steer(self.session, 'change', request_id='steer')
        self.assertEqual(turn, self.manager.get_current_turn_id('project', 'node'))
        self.assertEqual([c['type'] for c in self.sent], ['prompt', 'steer'])
        await self.end()
        self.assertIsNone(self.manager.get_current_turn_id('project', 'node'))
        await self.manager.send_prompt(self.session, 'next')
        self.assertNotEqual(turn, self.manager.get_current_turn_id('project', 'node'))

    async def test_fifo_duplicates_events_and_snapshot(self):
        live = []
        self.session.event_handlers.append(lambda session, event: live.append(dict(event)))
        await self.manager.send_prompt(self.session, 'first')
        first = self.session._current_turn_id
        refs = [{'node_id': 'ref', 'name': 'original'}]
        images = [{'data': 'original'}]
        await self.manager.send_follow_up(self.session, 'second', images=images, node_refs=refs,
                                          node_ref_context='snapshot', request_id='req')
        await self.manager.send_follow_up(self.session, 'second', request_id='req')
        await self.manager.send_follow_up(self.session, 'third', request_id='req2')
        refs[0]['name'] = 'changed'
        images[0]['data'] = 'changed'
        self.assertEqual(len(self.sent), 1)
        self.assertEqual([e['type'] for e in live], ['queued_input', 'queued_input'])
        self.assertEqual(live[0]['node_refs'][0]['name'], 'original')
        await self.end()
        second = self.session._current_turn_id
        self.assertEqual([e['type'] for e in live], ['queued_input', 'queued_input', 'agent_end', 'input_delivery_attempt', 'user_prompt'])
        self.assertEqual(live[-1]['request_id'], 'req')
        self.assertNotEqual(first, second)
        self.assertEqual(self.sent[1], {'type': 'prompt', 'message': 'second\n\nsnapshot',
                                        'images': [{'data': 'original'}]})
        prompt = [e for e in self.events if e['type'] == 'user_prompt'][-1]
        self.assertEqual(prompt['request_id'], 'req')
        self.assertEqual(prompt['node_refs'][0]['name'], 'original')
        await self.end()
        self.assertEqual(self.sent[2]['message'], 'third')
        self.assertEqual(live[-1]['type'], 'user_prompt')
        self.assertEqual(live[-1]['request_id'], 'req2')
        self.assertNotEqual(second, self.session._current_turn_id)
        await self.end()
        self.assertIsNone(self.session._current_turn_id)
        await self.manager.send_follow_up(self.session, 'duplicate', request_id='req')
        self.assertEqual(len(self.sent), 3)

    async def test_retry_keeps_turn_and_queue_until_terminal_end(self):
        await self.manager.send_prompt(self.session, 'first')
        turn = self.session._current_turn_id
        await self.manager.send_follow_up(self.session, 'second')
        await self.end(willRetry=True)
        self.assertEqual(self.session._current_turn_id, turn)
        self.assertEqual(len(self.sent), 1)
        await self.manager._dispatch_event(self.session, {'type': 'auto_retry_start'})
        await self.manager._dispatch_event(self.session, {'type': 'auto_retry_end', 'success': True})
        self.assertEqual(self.session._current_turn_id, turn)
        await self.end()
        self.assertNotEqual(self.session._current_turn_id, turn)
        self.assertEqual(len(self.sent), 2)

    async def test_concurrent_prompts_are_serialized(self):
        await asyncio.gather(*(self.manager.send_prompt(self.session, str(n), request_id=str(n))
                               for n in range(8)))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.session._follow_ups), 7)
        turns = {self.session._current_turn_id}
        for _ in range(7):
            await self.end()
            turns.add(self.session._current_turn_id)
        self.assertEqual(len(turns), 8)
        self.assertEqual([c['message'] for c in self.sent], [str(n) for n in range(8)])

    async def test_follow_up_and_steer_do_not_wait_for_inflight_start(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(session, command):
            self.sent.append(command)
            if command['type'] == 'prompt' and command['message'] == 'first':
                entered.set()
                await release.wait()
        self.manager.send_command = send
        starting = asyncio.create_task(self.manager.send_prompt(self.session, 'first'))
        await entered.wait()
        turn = self.session._current_turn_id
        await asyncio.wait_for(self.manager.send_follow_up(self.session, 'second', request_id='second'), 1)
        await asyncio.wait_for(self.manager.send_steer(self.session, 'steer'), 1)
        self.assertEqual(self.session._current_turn_id, turn)
        release.set()
        await starting
        await self.end()
        self.assertEqual([c['message'] for c in self.sent], ['first', 'steer', 'second'])
        self.assertNotEqual(self.session._current_turn_id, turn)

    async def test_follow_up_is_unsent_after_inflight_initial_send_failure(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def broken(session, command):
            entered.set()
            await release.wait()
            raise RuntimeError('broken')
        send = self.manager.send_command
        self.manager.send_command = broken
        starting = asyncio.create_task(self.manager.send_prompt(self.session, 'first'))
        await entered.wait()
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        release.set()
        with self.assertRaises(RuntimeError):
            await starting
        self.assertFalse(self.session._follow_ups)
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'], [('second', 'unsent')])
        self.manager.send_command = send
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        self.assertEqual(self.sent, [])
        await self.manager.send_follow_up(self.session, 'second', request_id='confirmed')
        self.assertEqual([c['message'] for c in self.sent], ['second'])
        self.assertIsNotNone(self.session._current_turn_id)

    async def test_follow_up_races_end_handler(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(session, event):
            if event['type'] == 'agent_end':
                entered.set()
                await release.wait()
        self.session.event_handlers.append(handler)
        await self.manager.send_prompt(self.session, 'first')
        first = self.session._current_turn_id
        finishing = asyncio.create_task(self.end())
        await entered.wait()
        await self.manager.send_follow_up(self.session, 'second')
        self.assertEqual(len(self.sent), 1)
        release.set()
        await finishing
        self.assertNotEqual(first, self.session._current_turn_id)
        self.assertEqual(self.sent[-1]['message'], 'second')

    async def test_no_request_id_does_not_merge_distinct_inputs(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'same')
        await self.manager.send_follow_up(self.session, 'same')
        await self.end()
        await self.end()
        self.assertEqual([c['message'] for c in self.sent], ['first', 'same', 'same'])

    async def test_lookup_is_scoped_to_project_and_node(self):
        other = await self.manager.get_or_create_session('other', 'node')
        other.status = 'running'
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_prompt(other, 'other')
        self.assertNotEqual(self.manager.get_current_turn_id('project', 'node'),
                            self.manager.get_current_turn_id('other', 'node'))
        await self.end()
        self.assertIsNone(self.manager.get_current_turn_id('project', 'node'))
        self.assertIsNotNone(self.manager.get_current_turn_id('other', 'node'))

    async def test_failed_delivery_classifies_queue_before_new_input(self):
        live = []
        self.session.event_handlers.append(lambda session, event: live.append(dict(event)))
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        await self.manager.send_follow_up(self.session, 'third', request_id='third')
        send = self.manager.send_command
        self.manager.send_command = AsyncMock(side_effect=RuntimeError('broken'))
        await self.end()
        self.assertIsNone(self.session._current_turn_id)
        self.assertFalse(self.session._follow_ups)
        self.assertEqual([(e['request_id'], e['delivery']) for e in live if e['type'] == 'input_delivery_terminal'],
                         [('second', 'uncertain'), ('third', 'unsent')])
        self.assertFalse(any(e['type'] == 'user_prompt' for e in live))
        self.manager.send_command = send
        await self.manager.send_follow_up(self.session, 'fourth', request_id='fourth')
        self.assertEqual([c['message'] for c in self.sent], ['first', 'fourth'])
        await self.end()
        self.assertEqual([c['message'] for c in self.sent], ['first', 'fourth'])

    async def test_new_id_confirms_repeat_with_original_metadata(self):
        for method in ('send_prompt', 'send_follow_up'):
            with self.subTest(method=method):
                session = await self.manager.get_or_create_session('project', method)
                session.status = 'running'
                await self.manager.send_prompt(session, 'first')
                refs = [{'node_id': 'ref', 'name': 'saved'}]
                await self.manager.send_follow_up(session, 'repeat', images=[{'data': 'image'}],
                                                  node_refs=refs, node_ref_context='context', request_id='old')
                send = self.manager.send_command
                self.manager.send_command = AsyncMock(side_effect=RuntimeError('broken'))
                await self.manager._dispatch_event(session, {'type': 'agent_end'})
                self.manager.send_command = send
                queued = [e for e in self.events if e['type'] == 'queued_input' and e.get('request_id') == 'old'][-1]
                self.assertEqual(queued['node_refs'], refs)
                self.assertEqual(queued['node_ref_context'], 'context')
                self.assertEqual(queued['attachments_missing_on_restart'], 1)
                self.assertEqual([e['delivery'] for e in self.events if e['type'] == 'input_delivery_terminal' and e.get('request_id') == 'old'][-1], 'uncertain')
                await getattr(self.manager, method)(session, 'repeat', images=[{'data': 'image'}],
                                                    node_refs=refs, node_ref_context='context', request_id='new')
                self.assertFalse(session._follow_ups)
                self.assertEqual(self.sent[-1]['message'], 'repeat\n\ncontext')
                self.assertEqual(self.sent[-1]['images'], [{'data': 'image'}])
                delivered = [e for e in self.events if e['type'] == 'user_prompt' and e.get('request_id') == 'new']
                self.assertEqual(delivered[-1]['node_refs'], refs)
                await self.manager._dispatch_event(session, {'type': 'agent_end'})
                self.assertIsNone(session._current_turn_id)

    async def test_repeated_fresh_failure_does_not_retry_classified_fifo(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'old', request_id='old')
        await self.manager.send_follow_up(self.session, 'later', request_id='later')
        send = self.manager.send_command
        self.manager.send_command = AsyncMock(side_effect=RuntimeError('broken'))
        await self.end()
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'],
                         [('old', 'uncertain'), ('later', 'unsent')])
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'broken'):
                await self.manager.send_prompt(self.session, 'distinct', request_id='new')
            self.assertFalse(self.session._follow_ups)
        self.manager.send_command = send
        await self.manager.send_prompt(self.session, 'distinct', request_id='new')
        await self.end()
        self.assertEqual([c['message'] for c in self.sent], ['first', 'distinct'])
        self.assertEqual(len([e for e in self.events if e['type'] == 'input_delivery_terminal']), 2)

    async def test_router_returns_explicit_rejection_without_accepting_input(self):
        from fastapi import HTTPException
        from app.routers import api_agent_nodes

        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'old', request_id='old')
        send = self.manager.send_command
        self.manager.send_command = AsyncMock(side_effect=RuntimeError('broken'))
        await self.end()
        self.session.process = MagicMock()
        self.session.process.poll.return_value = None
        with patch.object(api_agent_nodes, 'agent_host_manager', self.manager), \
             patch.object(api_agent_nodes, '_prepare_input', return_value={'node_refs': [], 'context': ''}):
            with self.assertRaises(HTTPException) as rejected:
                await api_agent_nodes._send_input_admitted('project', 'node', 'follow_up',
                    {'message': 'fresh', 'request_id': 'new'}, self.session)
            self.assertEqual(rejected.exception.status_code, 502)
            self.assertIn('broken', rejected.exception.detail['message'])
            self.manager.send_command = send
            accepted = await api_agent_nodes._send_input_admitted('project', 'node', 'follow_up',
                    {'message': 'fresh', 'request_id': 'new'}, self.session)
        self.assertTrue(accepted['success'])
        self.assertEqual(accepted['request_id'], 'new')
        self.assertEqual([c['message'] for c in self.sent], ['first', 'fresh'])

    async def test_concurrent_new_input_after_failure_preserves_fifo(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'old', request_id='old')
        send = self.manager.send_command
        self.manager.send_command = AsyncMock(side_effect=RuntimeError('broken'))
        await self.end()
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(session, command):
            entered.set()
            await release.wait()
            await send(session, command)
        self.manager.send_command = slow
        recovery = asyncio.create_task(self.manager.send_prompt(self.session, 'distinct', request_id='new'))
        await entered.wait()
        await self.manager.send_follow_up(self.session, 'confirmed', request_id='another')
        await self.manager.send_follow_up(self.session, 'old', request_id='old')
        self.assertEqual([item['message'] for item in self.session._follow_ups], ['confirmed'])
        release.set()
        await recovery
        await self.end()
        self.assertEqual([c['message'] for c in self.sent], ['first', 'distinct', 'confirmed'])
        self.assertFalse(self.session._follow_ups)

    async def test_failed_delivery_retry_same_id_is_not_duplicated(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        send = self.manager.send_command
        attempts = []
        async def partial_write(session, command):
            attempts.append(command['message'])
            raise BrokenPipeError('after partial write')
        self.manager.send_command = partial_write
        await self.end()
        self.assertEqual(attempts, ['second'])
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'], [('second', 'uncertain')])
        self.manager.send_command = send
        await self.manager.send_follow_up(self.session, 'ignored', request_id='second')
        await self.manager.send_follow_up(self.session, 'ignored', request_id='second')
        self.assertEqual([c['message'] for c in self.sent], ['first'])
        self.assertFalse(self.session._follow_ups)
        await self.manager.send_follow_up(self.session, 'second', request_id='confirmed')
        self.assertEqual([c['message'] for c in self.sent], ['first', 'second'])

    async def test_queued_prompt_same_id_does_not_retry_uncertain_delivery(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_prompt(self.session, 'second', request_id='second')
        send = self.manager.send_command
        self.manager.send_command = AsyncMock(side_effect=RuntimeError('broken'))
        await self.end()
        self.assertFalse(self.session._follow_ups)
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'], [('second', 'uncertain')])
        self.manager.send_command = send
        await self.manager.send_prompt(self.session, 'ignored', request_id='second')
        await self.manager.send_prompt(self.session, 'ignored again', request_id='second')
        self.assertEqual([c['message'] for c in self.sent], ['first'])
        self.assertIsNone(self.session._current_turn_id)
        self.assertFalse(any(e['type'] == 'user_prompt' and e.get('request_id') == 'second' for e in self.events))
        await self.manager.send_prompt(self.session, 'second', request_id='confirmed')
        self.assertEqual([c['message'] for c in self.sent], ['first', 'second'])

    async def test_stop_then_restart_classifies_in_memory_queue_unsent(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        await self.manager.stop_session(self.session)
        self.session.status = 'running'
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        self.assertEqual([c['message'] for c in self.sent], ['first'])
        self.assertIsNone(self.session._current_turn_id)
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'], [('second', 'unsent')])
        await self.manager.send_follow_up(self.session, 'second', request_id='confirmed')
        self.assertEqual([c['message'] for c in self.sent], ['first', 'second'])

    async def test_pi_restart_accepts_fresh_input_without_retrying_queue(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'old', request_id='old')
        await self.manager.stop_session(self.session)
        self.session.status = 'running'
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'], [('old', 'unsent')])
        await self.manager.send_prompt(self.session, 'new', request_id='new')
        await self.end()
        self.assertEqual([c['message'] for c in self.sent], ['first', 'new'])

    async def test_codex_exit_accepts_fresh_input_without_retrying_queue(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'old', request_id='old')
        self.session.agent_cli = 'codex'
        process = MagicMock()
        process.stdout.readline.return_value = ''
        process.poll.return_value = 1
        process.returncode = 1
        self.session.process = process
        with patch('app.services.agent_host.maybe_warm_codex'):
            await self.manager._read_codex_turn(self.session, process, False, 'first', False)
        self.manager._codex_turn = AsyncMock()
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'], [('old', 'unsent')])
        await self.manager.send_prompt(self.session, 'new', request_id='new')
        self.manager._codex_turn.assert_awaited_once()
        self.assertEqual(self.manager._codex_turn.await_args.args[1], 'new')
        self.assertFalse(self.session._follow_ups)
        await self.manager._dispatch_event(self.session, {'type': 'agent_end'})
        self.assertEqual(self.manager._codex_turn.await_count, 1)

    async def test_codex_broken_stdin_marks_delivery_uncertain(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        self.session.agent_cli = 'codex'
        process = MagicMock()
        process.stderr = []
        process.stdin.write.side_effect = BrokenPipeError('closed')
        with tempfile.TemporaryDirectory() as directory, \
             patch('app.services.agent_host.subprocess.Popen', return_value=process), \
             patch('app.services.agent_host.shutil.which', return_value='codex'), \
             patch('app.services.agent_adapters.codex_adapter.write_home_mcp_config'), \
             patch.object(self.manager._adapters['codex'], 'build_argv', return_value=['codex']):
            self.manager._get_project_dir = lambda project_id: directory
            self.manager._kill_process_tree = AsyncMock()
            await self.end()
            self.manager._kill_process_tree.assert_awaited_once()
        self.assertIsNone(self.session._current_turn_id)
        self.assertFalse(self.session._turn_open)
        self.assertIsNone(self.session.process)
        self.assertFalse(self.session._follow_ups)
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'], [('second', 'uncertain')])
        self.assertEqual(process.stdin.write.call_count, 1)
        self.assertFalse(any(e['type'] == 'user_prompt' and e.get('request_id') == 'second' for e in self.events))
        self.manager._codex_turn = AsyncMock()
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        self.manager._codex_turn.assert_not_awaited()

    async def test_codex_exit_without_end_marks_queue_unsent(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        self.session.agent_cli = 'codex'
        process = MagicMock()
        process.stdout.readline.return_value = ''
        process.poll.return_value = 1
        process.returncode = 1
        self.session.process = process
        with patch('app.services.agent_host.maybe_warm_codex'):
            await self.manager._read_codex_turn(self.session, process, False, 'first', False)
        self.assertIsNone(self.session._current_turn_id)
        self.assertFalse(self.session._follow_ups)
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'], [('second', 'unsent')])
        self.manager._codex_turn = AsyncMock()
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        self.manager._codex_turn.assert_not_awaited()
        await self.manager.send_follow_up(self.session, 'second', request_id='confirmed')
        self.manager._codex_turn.assert_awaited_once()
        self.assertFalse(self.session._follow_ups)

    async def test_codex_failed_resume_retry_marks_follow_up_unsent(self):
        await self.manager.send_prompt(self.session, 'first')
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        self.session.agent_cli = 'codex'
        process = MagicMock()
        process.stdout.readline.return_value = ''
        process.poll.return_value = 1
        process.returncode = 1
        self.session.process = process
        self.manager._build_replay_context = lambda session: ('', 0)
        self.manager._codex_turn = AsyncMock(side_effect=RuntimeError('spawn failed'))
        await self.manager._read_codex_turn(self.session, process, True, 'first', True)
        self.assertIsNone(self.session._current_turn_id)
        self.assertEqual(self.session.status, 'running')
        self.assertFalse(self.session._follow_up_delivery_failed)
        self.assertFalse(self.session._follow_ups)
        self.assertEqual([(e['request_id'], e['delivery']) for e in self.events if e['type'] == 'input_delivery_terminal'], [('second', 'unsent')])
        self.manager._codex_turn = AsyncMock()
        await self.manager.send_follow_up(self.session, 'second', request_id='second')
        self.manager._codex_turn.assert_not_awaited()
        await self.manager.send_follow_up(self.session, 'second', request_id='confirmed')
        self.manager._codex_turn.assert_awaited_once()
        self.assertFalse(self.session._follow_ups)

    async def test_live_delivery_is_single_persisted_record_and_restarts_do_not_recover_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'project.json').write_text('{}', encoding='utf-8')
            self.manager._get_project_dir = lambda project_id: directory
            self.manager._persist_event = AgentHostManager._persist_event.__get__(self.manager)
            live = []
            self.session.event_handlers.append(lambda session, event: live.append(dict(event)))
            await self.manager.send_prompt(self.session, 'first', request_id='first')
            first_turn = self.session._current_turn_id
            await self.manager.send_follow_up(self.session, 'second', request_id='second')
            await self.manager.send_follow_up(self.session, 'third', request_id='third')
            await self.end()
            records = self.manager.load_events('project', 'node')['events']
            events = [record['event'] for record in records]
            ended = next(event for event in events if event['type'] == 'agent_end')
            self.assertEqual(ended['turn_id'], first_turn)
            self.assertNotEqual(ended['turn_id'], self.session._current_turn_id)
            self.assertEqual(ended, next(event for event in live if event['type'] == 'agent_end'))
            self.assertEqual([e['type'] for e in live],
                             ['queued_input', 'queued_input', 'agent_end', 'input_delivery_attempt', 'user_prompt'])
            delivered = [e for e in events if e['type'] == 'user_prompt' and e.get('request_id') == 'second']
            self.assertEqual(delivered, [live[-1]])
            self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
            page = self.manager.load_events_page('project', 'node', 'pi', turns=5)
            self.assertEqual([r['event'] for r in page['events']], events)
            restarted = AgentHostManager()
            restarted._get_project_dir = lambda project_id: directory
            session = await restarted.get_or_create_session('project', 'node')
            self.assertFalse(session._follow_ups)
            self.assertTrue(any(e['type'] == 'queued_input' and e.get('request_id') == 'third' for e in events))
            recovered = restarted.load_events('project', 'node')['events']
            self.assertEqual(recovered[-1]['event']['delivery'], 'unsent')
            self.assertEqual(recovered[-1]['event']['request_id'], 'third')
            restarted._persist_event(session, {'type': 'restart_test'})
            self.assertEqual(session._event_seq, len(events) + 2)

    async def test_restart_classifies_attempt_uncertain_and_remaining_unsent(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'project.json').write_text('{}', encoding='utf-8')
            self.manager._get_project_dir = lambda project_id: directory
            self.manager._persist_event = AgentHostManager._persist_event.__get__(self.manager)
            await self.manager.send_prompt(self.session, 'first')
            refs = [{'node_id': 'ref', 'name': 'snapshot'}]
            await self.manager.send_follow_up(self.session, 'second', images=[{'data': 'bytes'}],
                                              node_refs=refs, node_ref_context='context', request_id='second')
            await self.manager.send_follow_up(self.session, 'third', request_id='third')
            self.manager._persist_event(self.session, {'type': 'input_delivery_attempt', 'request_id': 'second'})
            restarted = AgentHostManager()
            restarted._get_project_dir = lambda project_id: directory
            session = await restarted.get_or_create_session('project', 'node')
            self.assertFalse(session._follow_ups)
            events = [record['event'] for record in restarted.load_events('project', 'node')['events']]
            queued = next(e for e in events if e['type'] == 'queued_input' and e.get('request_id') == 'second')
            self.assertEqual(queued['node_refs'], refs)
            self.assertEqual(queued['node_ref_context'], 'context')
            self.assertEqual(queued['attachments_missing_on_restart'], 1)
            self.assertEqual([(e['request_id'], e['delivery']) for e in events if e['type'] == 'input_delivery_terminal'],
                             [('second', 'uncertain'), ('third', 'unsent')])
            await restarted.get_or_create_session('project', 'node')
            self.assertEqual(len([r for r in restarted.load_events('project', 'node')['events']
                                  if r['event']['type'] == 'input_delivery_terminal']), 2)

    async def test_send_failure_does_not_leave_turn(self):
        self.manager.send_command = AsyncMock(side_effect=RuntimeError('broken'))
        with self.assertRaises(RuntimeError):
            await self.manager.send_prompt(self.session, 'first')
        self.assertIsNone(self.session._current_turn_id)
        self.assertFalse(self.events)


if __name__ == '__main__':
    unittest.main()
