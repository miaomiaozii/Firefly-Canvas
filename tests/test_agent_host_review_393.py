import asyncio
import builtins
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main  # Initialize the application before importing host services.
from app.routers import api_agent_nodes
from fastapi import HTTPException
from app.services.agent_host import AgentHostManager


@pytest.fixture(autouse=True)
def isolated_event_loop_policy():
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(previous)


def setup_manager(tmp_path):
    (tmp_path / 'project.json').write_text('{}', encoding='utf-8')
    manager = AgentHostManager()
    manager._get_project_dir = lambda _: str(tmp_path)
    manager._collect_reference_images = lambda _: ([], [])
    return manager


def test_retry_exhaustion_terminal_end_drains_once(tmp_path):
    async def run():
        manager = setup_manager(tmp_path)
        session = await manager.get_or_create_session('p', 'n')
        session.status = 'running'
        manager.send_command = AsyncMock()
        await manager.send_prompt(session, 'first', request_id='first')
        await manager.send_follow_up(session, 'second', request_id='second')
        old = session._current_turn_id
        await manager._dispatch_event(session, {'type': 'agent_end', 'willRetry': True})
        await manager._dispatch_event(session, {'type': 'auto_retry_start'})
        await manager._dispatch_event(session, {'type': 'agent_end', 'willRetry': False})
        assert session._current_turn_id != old
        await manager._dispatch_event(session, {'type': 'auto_retry_end', 'success': False})
        assert [c.args[1]['message'] for c in manager.send_command.await_args_list] == ['first', 'second']
        assert not session._follow_ups
    asyncio.run(run())


def test_recovered_uncertain_id_deduplicates_both_entrypoints(tmp_path):
    async def run():
        manager = setup_manager(tmp_path)
        session = await manager.get_or_create_session('p', 'n')
        session.status = 'busy'
        await manager.send_follow_up(session, 'old', request_id='old')
        manager._persist_event(session, {'type': 'input_delivery_attempt', 'request_id': 'old'})
        restarted = setup_manager(tmp_path)
        fresh = await restarted.get_or_create_session('p', 'n')
        fresh.status = 'running'
        restarted.send_command = AsyncMock()
        with pytest.raises(ValueError, match='recovered history'):
            await restarted.send_prompt(fresh, 'old', request_id='old')
        with pytest.raises(ValueError, match='recovered history'):
            await restarted.send_follow_up(fresh, 'old', request_id='old')
        assert restarted.send_command.await_count == 0
        await restarted.send_prompt(fresh, 'confirmed', request_id='new')
        assert restarted.send_command.await_count == 1
        events = [r['event'] for r in restarted.load_events('p', 'n')['events']]
        assert any(e.get('request_id') == 'old' and e.get('delivery') == 'uncertain' for e in events)
    asyncio.run(run())


def test_rejected_queue_admission_and_partial_append_are_not_replayed(tmp_path):
    async def run():
        manager = setup_manager(tmp_path)
        session = await manager.get_or_create_session('p', 'n')
        session.status = 'busy'
        await manager.send_follow_up(session, 'good', request_id='good')
        real_open = builtins.open

        class PartialWriter:
            def __init__(self, file):
                self.file = file
            def __enter__(self):
                self.file.__enter__()
                return self
            def __exit__(self, *args):
                return self.file.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.file, name)
            def write(self, data):
                self.file.write(data[:12])
                raise OSError('partial write')

        def broken_open(path, mode='r', *args, **kwargs):
            f = real_open(path, mode, *args, **kwargs)
            return PartialWriter(f) if mode == 'a+b' else f
        with patch('builtins.open', side_effect=broken_open):
            with pytest.raises(RuntimeError, match='persist'):
                await manager.send_follow_up(session, 'bad', request_id='bad')
        assert [item['request_id'] for item in session._follow_ups] == ['good']
        assert 'bad' not in session._follow_up_ids
        await manager.send_follow_up(session, 'later', request_id='later')
        restarted = setup_manager(tmp_path)
        await restarted.get_or_create_session('p', 'n')
        events = [r['event'] for r in restarted.load_events('p', 'n')['events']]
        assert {e['request_id'] for e in events if e.get('delivery') == 'unsent'} == {'good', 'later'}
        assert not any(e.get('request_id') == 'bad' for e in events)
    asyncio.run(run())


def test_router_failed_persistence_rejects_and_lost_response_is_idempotent(tmp_path):
    async def run():
        manager = setup_manager(tmp_path)
        session = await manager.get_or_create_session('p', 'n')
        session.status = 'busy'
        session.process = MagicMock()
        session.process.poll.return_value = None
        manager.send_command = AsyncMock()
        with patch.object(api_agent_nodes, 'agent_host_manager', manager), \
             patch.object(api_agent_nodes, '_prepare_input', return_value={'node_refs': [], 'context': ''}), \
             patch.object(manager, '_persist_event', return_value=False):
            with pytest.raises(HTTPException) as exc:
                await api_agent_nodes._send_input_admitted('p', 'n', 'follow_up',
                    {'message': 'bad', 'request_id': 'bad'}, session)
            assert exc.value.status_code == 502
        assert not session._follow_ups
        with patch.object(api_agent_nodes, 'agent_host_manager', manager), \
             patch.object(api_agent_nodes, '_prepare_input', return_value={'node_refs': [], 'context': ''}):
            body = {'message': 'accepted', 'request_id': 'accepted'}
            assert (await api_agent_nodes._send_input_admitted('p', 'n', 'follow_up', body, session))['success']
            # Simulate a lost HTTP response and retry with the same ID.
            assert (await api_agent_nodes._send_input_admitted('p', 'n', 'follow_up', body, session))['success']
            await api_agent_nodes._send_input_admitted('p', 'n', 'follow_up',
                {'message': 'accepted', 'request_id': 'confirmed'}, session)
        assert [item['request_id'] for item in session._follow_ups] == ['accepted', 'confirmed']
    asyncio.run(run())


@pytest.mark.parametrize('lines', [
    [],
    [{'type': 'item.completed', 'item': {'id': 'i', 'type': 'command_execution', 'command': 'touch x', 'status': 'completed'}}],
    [{'type': 'turn.completed'}],
    ['not json'],
])
def test_resumed_codex_nonzero_never_replays_unknown_outcome(tmp_path, lines):
    async def run():
        manager = setup_manager(tmp_path)
        session = await manager.get_or_create_session('p', 'n')
        session.agent_cli = 'codex'
        session.status = 'busy'
        session._current_turn_id = 'turn'
        session._current_request_id = 'req'
        manager._persist_event(session, {'type': 'user_prompt', 'request_id': 'req', 'text': 'execute'})
        process = MagicMock()
        process.stdout.readline.side_effect = [json.dumps(x) + '\n' if isinstance(x, dict) else x + '\n' for x in lines] + ['']
        process.poll.return_value = 1
        process.returncode = 1
        session.process = process
        manager._codex_turn = AsyncMock()
        with patch('app.services.agent_host.maybe_warm_codex'):
            reader = asyncio.create_task(manager._read_codex_turn(session, process, True, 'execute', True))
            session._reader_task = reader
            await reader
        manager._codex_turn.assert_not_awaited()
        events = [r['event'] for r in manager.load_events('p', 'n')['events']]
        assert any(e.get('request_id') == 'req' and e.get('delivery') == 'uncertain' for e in events)
        assert any(e.get('type') == 'error' and e.get('delivery') == 'uncertain' for e in events)
        assert session._current_turn_id is None
    asyncio.run(run())
