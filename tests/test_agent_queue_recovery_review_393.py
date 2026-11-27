"""Executable regression specifications for the unresolved #393 recovery boundary."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import main  # Initialize the application before its routers resolve the main module.
from app.routers import api_agent_nodes
from app.services.agent_host import AgentHostManager
from tests.agent_loop_fixtures import isolated_event_loop_policy  # noqa: F401


def test_router_does_not_retry_a_partially_written_queued_input(tmp_path):
    async def scenario():
        manager = AgentHostManager()
        manager._get_project_dir = lambda _project: str(tmp_path)
        Path(tmp_path, 'project.json').write_text('{}', encoding='utf-8')
        manager._collect_reference_images = lambda _session: ([], [])
        session = await manager.get_or_create_session('project', 'node')
        session.status = 'running'
        session.process = MagicMock()
        session.process.poll.return_value = None
        sent = []

        def write(line):
            message = json.loads(line)['message']
            sent.append(message)
            if message == 'old':
                raise BrokenPipeError('partial stdin write')
            return len(line)

        session.process.stdin.write.side_effect = write
        await manager.send_prompt(session, 'first', request_id='first')
        await manager.send_follow_up(session, 'old', request_id='old')
        await manager._dispatch_event(session, {'type': 'agent_end'})
        with patch.object(api_agent_nodes, 'agent_host_manager', manager), \
             patch.object(api_agent_nodes, '_prepare_input', return_value={'node_refs': [], 'context': ''}), \
             patch.object(api_agent_nodes, '_apply_node_host'):
            try:
                await api_agent_nodes._send_input_admitted('project', 'node', 'prompt',
                    {'message': 'fresh', 'request_id': 'fresh'}, session)
            except HTTPException as exc:
                assert exc.status_code == 502
        assert sent.count('old') == 1, sent

    asyncio.run(scenario())


def test_router_fresh_prompt_after_codex_exit_does_not_send_old_queue(tmp_path):
    async def scenario():
        Path(tmp_path, 'project.json').write_text('{}', encoding='utf-8')
        manager = AgentHostManager()
        manager._get_project_dir = lambda _project: str(tmp_path)
        manager._collect_reference_images = lambda _session: ([], [])
        session = await manager.get_or_create_session('project', 'node')
        session.status = 'running'
        manager.send_command = AsyncMock()
        await manager.send_prompt(session, 'first', request_id='first')
        await manager.send_follow_up(session, 'old', request_id='old')
        session.agent_cli = 'codex'
        process = MagicMock()
        process.stdout.readline.return_value = ''
        process.poll.return_value = 1
        process.returncode = 1
        session.process = process
        with patch('app.services.agent_host.maybe_warm_codex'):
            await manager._read_codex_turn(session, process, False, 'first', False)
        manager._codex_turn = AsyncMock()
        async def spawn(current):
            current.status = 'running'
            return True
        manager.spawn_session = spawn
        with patch.object(api_agent_nodes, 'agent_host_manager', manager), \
             patch.object(api_agent_nodes, '_prepare_input', return_value={'node_refs': [], 'context': ''}), \
             patch.object(api_agent_nodes, '_apply_node_host'):
            try:
                await api_agent_nodes._send_input_admitted('project', 'node', 'prompt',
                    {'message': 'fresh', 'request_id': 'fresh'}, session)
            except HTTPException as exc:
                assert exc.status_code == 502
        assert [call.args[1] for call in manager._codex_turn.await_args_list] == ['fresh']

    asyncio.run(scenario())


@pytest.mark.parametrize('abnormal', [False, True])
def test_codex_reader_cleanup_drains_inputs_admitted_by_router(tmp_path, abnormal):
    import threading

    async def scenario():
        Path(tmp_path, 'project.json').write_text('{}', encoding='utf-8')
        manager = AgentHostManager()
        manager._get_project_dir = lambda _: str(tmp_path)
        manager._collect_reference_images = lambda _: ([], [])
        session = await manager.get_or_create_session('project', 'node')
        session.agent_cli = 'codex'
        session.status = 'busy'
        session._current_turn_id = 'old-turn'
        session._turn_open = True
        process = MagicMock()
        process.stdout.readline.side_effect = ([''] if abnormal else [json.dumps({'type': 'turn.completed'}) + '\n', ''])
        process.poll.return_value = 1 if abnormal else None
        process.returncode = 1 if abnormal else 0
        wait_release = threading.Event()
        process.wait.side_effect = lambda: wait_release.wait(5)
        session.process = process
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(_, event):
            if event['type'] == ('error' if abnormal else 'agent_end'):
                entered.set()
                await release.wait()
        session.event_handlers.append(handler)
        delivered = []
        async def start(_, message, **kwargs):
            delivered.append(message)
        manager._codex_turn = start
        with patch('app.services.agent_host.maybe_warm_codex'), \
             patch.object(manager, '_codex_validate', return_value=True), \
             patch.object(api_agent_nodes, 'agent_host_manager', manager), \
             patch.object(api_agent_nodes, '_prepare_input', return_value={'node_refs': [], 'context': ''}), \
             patch.object(api_agent_nodes, '_apply_node_host'):
            reader = asyncio.create_task(manager._read_codex_turn(session, process, False, 'old', False))
            session._reader_task = reader
            if abnormal:
                wait_release.set()
            await asyncio.wait_for(entered.wait(), 3)
            if not abnormal:
                release.set()
                await asyncio.sleep(.05)
            receipt = await api_agent_nodes._send_input_admitted('project', 'node', 'prompt',
                {'message': 'same', 'request_id': 'fresh'}, session)
            assert receipt['success']
            assert not delivered
            release.set()
            wait_release.set()
            await asyncio.wait_for(reader, 3)
        assert delivered == ['same']
        assert not session._follow_ups
    asyncio.run(scenario())


def test_restart_emits_unsent_status_for_each_unattempted_request(tmp_path):
    async def scenario():
        Path(tmp_path, 'project.json').write_text('{}', encoding='utf-8')
        manager = AgentHostManager()
        manager._get_project_dir = lambda _project: str(tmp_path)
        manager._collect_reference_images = lambda _session: ([], [])
        session = await manager.get_or_create_session('project', 'node')
        session.status = 'busy'
        await manager.send_follow_up(session, 'same', request_id='one')
        await manager.send_follow_up(session, 'same', request_id='two')
        restarted = AgentHostManager()
        restarted._get_project_dir = lambda _project: str(tmp_path)
        new_session = await restarted.get_or_create_session('project', 'node')
        assert not new_session._follow_ups
        events = [r['event'] for r in restarted.load_events('project', 'node')['events']]
        assert {e['request_id'] for e in events if e.get('delivery') == 'unsent'} == {'one', 'two'}

    asyncio.run(scenario())


def test_restart_distinguishes_attempted_delivered_and_unattempted(tmp_path):
    async def scenario():
        Path(tmp_path, 'project.json').write_text('{}', encoding='utf-8')
        manager = AgentHostManager()
        manager._get_project_dir = lambda _project: str(tmp_path)
        session = await manager.get_or_create_session('project', 'node')
        session.status = 'busy'
        await manager.send_follow_up(session, 'attempted', request_id='attempted')
        await manager.send_follow_up(session, 'delivered', request_id='delivered')
        await manager.send_follow_up(session, 'pending', request_id='pending', images=[{'data': 'secret'}])
        manager._persist_event(session, {'type': 'input_delivery_attempt', 'request_id': 'attempted'})
        manager._persist_event(session, {'type': 'user_prompt', 'request_id': 'delivered', 'text': 'delivered'})
        restarted = AgentHostManager()
        restarted._get_project_dir = lambda _project: str(tmp_path)
        fresh = await restarted.get_or_create_session('project', 'node')
        assert not fresh._follow_ups
        records = restarted.load_events('project', 'node')['events']
        terminals = {r['event']['request_id']: r['event']['delivery'] for r in records
                     if r['event'].get('type') == 'input_delivery_terminal'}
        assert terminals == {'attempted': 'uncertain', 'pending': 'unsent'}
        assert 'secret' not in str(records)
        assert next(r['event'] for r in records if r['event'].get('request_id') == 'pending')['attachments_missing_on_restart'] == 1
    asyncio.run(scenario())


def test_older_page_enriches_queue_from_newer_terminal_and_legacy_is_uncertain(tmp_path):
    async def scenario():
        Path(tmp_path, 'project.json').write_text('{}', encoding='utf-8')
        manager = AgentHostManager()
        manager._get_project_dir = lambda _: str(tmp_path)
        session = await manager.get_or_create_session('project', 'node')
        for event in [
            {'type': 'user_prompt', 'text': 'first'},
            {'type': 'queued_input', 'input_type': 'follow_up', 'request_id': 'one', 'text': 'same'},
            {'type': 'queued_input', 'input_type': 'follow_up', 'request_id': 'two', 'text': 'same'},
            {'type': 'queued_input', 'input_type': 'follow_up', 'text': 'legacy'},
            {'type': 'user_prompt', 'text': 'next'},
            {'type': 'input_delivery_terminal', 'request_id': 'one', 'delivery': 'uncertain'},
            {'type': 'input_delivery_terminal', 'request_id': 'two', 'delivery': 'unsent'},
        ]:
            manager._persist_event(session, event)
        latest = manager.load_events_page('project', 'node', 'pi', turns=1)
        assert latest['has_more']
        older = manager.load_events_page('project', 'node', 'pi', turns=1, cursor=latest['cursor'])
        rows = [r['event'] for r in older['events'] if r['event']['type'] == 'queued_input']
        assert [(e.get('request_id'), e['replay_delivery']) for e in rows] == [
            ('one', 'uncertain'), ('two', 'unsent'), (None, 'uncertain')]
    asyncio.run(scenario())


def test_stopped_queue_is_unsent_before_distinct_fresh_prompt(tmp_path):
    async def scenario():
        Path(tmp_path, 'project.json').write_text('{}', encoding='utf-8')
        manager = AgentHostManager()
        manager._get_project_dir = lambda _project: str(tmp_path)
        manager._collect_reference_images = lambda _session: ([], [])
        session = await manager.get_or_create_session('project', 'node')
        session.status = 'busy'
        await manager.send_follow_up(session, 'old', request_id='old')
        session.status = 'stopped'
        sent = []
        session.agent_cli = 'codex'
        async def send(current, message, **kwargs):
            sent.append(message)
        manager._codex_turn = send
        with patch.object(manager, '_codex_validate', return_value=True), \
             patch.object(api_agent_nodes, 'agent_host_manager', manager), \
             patch.object(api_agent_nodes, '_prepare_input', return_value={'node_refs': [], 'context': ''}), \
             patch.object(api_agent_nodes, '_apply_node_host'):
            await api_agent_nodes._send_input_admitted('project', 'node', 'prompt',
                {'message': 'fresh', 'request_id': 'fresh'}, session)
        assert sent == ['fresh']
        events = [r['event'] for r in manager.load_events('project', 'node')['events']]
        assert any(e.get('delivery') == 'unsent' and e.get('request_id') == 'old' for e in events)
        assert any(e.get('type') == 'user_prompt' and e.get('request_id') == 'fresh' for e in events)
    asyncio.run(scenario())
