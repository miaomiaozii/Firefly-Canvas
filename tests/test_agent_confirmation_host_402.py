"""A12 system continuation ownership through the real manager queue and response cleanup."""
import asyncio
from copy import deepcopy
from io import StringIO
from types import SimpleNamespace

import pytest

from app.services import agent_host
from app.services.agent_host import AgentHostManager, AgentSession


@pytest.fixture(params=['pi', 'codex'])
def host(request, monkeypatch):
    manager = AgentHostManager()
    session = AgentSession('project', 'actor', agent_cli=request.param, status='running')
    manager.sessions['project:actor'] = session
    sent, events, live, failures = [], [], [], {}
    monkeypatch.setattr(agent_host, '_app_main', SimpleNamespace(FIREFLY_PROJECT_STORE=None))
    monkeypatch.setattr(agent_host, 'maybe_warm_codex', lambda *args: False)
    monkeypatch.setattr(manager, '_collect_reference_images', lambda session: ([], []))
    monkeypatch.setattr(manager, '_persist_event',
                        lambda session, event, **kwargs: events.append(deepcopy(event)))
    session.event_handlers.append(lambda session, event: live.append(deepcopy(event)))

    def record(s, message, kind):
        sent.append({'message': message, 'kind': kind, 'turn_id': s._current_turn_id,
                     'generation': s.generation, 'request_id': s._current_request_id})
        if message in failures:
            raise failures[message]('uncertain transport write')

    async def command(s, payload):
        record(s, payload['message'], payload['type'])

    async def codex(s, message, **kwargs):
        assert not s._turn_open
        s._generation += 1
        record(s, message, 'codex')
        s._turn_open = True

    monkeypatch.setattr(manager, 'send_command', command)
    monkeypatch.setattr(manager, '_codex_turn', codex)

    async def finish():
        if session.agent_cli == 'pi':
            await manager._dispatch_event(session, {'type': 'agent_end'})
        else:
            # Exercise Codex reader cleanup too: its agent_end cannot drain before process exit.
            process = SimpleNamespace(stdout=StringIO('{"type":"turn.completed"}\n'),
                                      returncode=0, poll=lambda: 0, wait=lambda: 0)
            session.process = process
            task = asyncio.create_task(manager._read_codex_turn(
                session, process, False, '', False,
                host=session.agent_cli, generation=session.generation))
            session._reader_task = task
            await task

    async def continue_with(message, turn_id=None, **overrides):
        origin = dict(turn_id=turn_id or session._last_user_turn_id, host=session.agent_cli,
                      generation=session.generation, epoch=session.input_epoch)
        origin.update(overrides)
        await manager.send_system_continuation(session, message, **origin)

    return SimpleNamespace(manager=manager, session=session, sent=sent, events=events,
                           live=live, failures=failures, finish=finish, continue_with=continue_with)


def terminal(events):
    return [(event['request_id'], event['delivery']) for event in events
            if event['type'] == 'input_delivery_terminal']


@pytest.mark.parametrize('ordering', ['user-first', 'system-first'])
def test_mixed_queue_drops_old_system_results_without_blocking_later_user(host, ordering):
    async def scenario():
        m, s = host.manager, host.session
        await m.send_prompt(s, 'original', request_id='original')
        original = s._current_turn_id
        if ordering == 'system-first':
            await host.continue_with('valid-before-user')
        await m.send_follow_up(s, 'next-user', request_id='next')
        await host.continue_with('obsolete-after-user')
        obsolete = s._follow_ups[-1]
        obsolete_id, obsolete_generation = obsolete['request_id'], obsolete['generation']
        await m.send_follow_up(s, 'later-user', request_id='later')
        await m.send_follow_up(s, 'duplicate', request_id='next')
        if ordering == 'system-first':
            await host.finish()
            assert host.sent[-1]['message'] == 'valid-before-user'
            assert s._current_turn_id == original
            obsolete_generation = obsolete['generation']
        await host.finish()
        next_turn = s._current_turn_id
        assert next_turn != original and s._last_user_turn_id == next_turn
        assert host.sent[-1]['message'] == 'next-user'
        assert obsolete['generation'] == obsolete_generation
        await host.finish()
        assert host.sent[-1]['message'] == 'later-user'
        assert s._current_turn_id not in (original, next_turn, None)
        assert s._last_user_turn_id == s._current_turn_id
        expected = ['original'] + (['valid-before-user'] if ordering == 'system-first' else [])
        assert [item['message'] for item in host.sent] == expected + ['next-user', 'later-user']
        assert terminal(host.events) == terminal(host.live) == [(obsolete_id, 'unsent')]
        assert [event['text'] for event in host.events if event['type'] == 'user_prompt'] == [
            'original', 'next-user', 'later-user']
        assert [event['request_id'] for event in host.live if event['type'] == 'user_prompt'] == ['next', 'later']
        assert not any(event.get('delivery') == 'sent' and event.get('text') == 'obsolete-after-user'
                       for event in host.events)
        await host.finish()
        assert not s.is_busy and not s._follow_ups and s._current_turn_id is None
    asyncio.run(scenario())


@pytest.mark.parametrize('first_delivery', ['queued', 'idle'])
def test_same_user_turn_system_fifo_survives_response_cleanup_and_native_generations(host, first_delivery):
    async def scenario():
        m, s = host.manager, host.session
        await m.send_prompt(s, 'original', request_id='original')
        original, generation = s._current_turn_id, s.generation
        if first_delivery == 'idle':
            await host.finish()
            assert s._current_turn_id is None and s._last_user_turn_id == original
        messages = ['approved-first', 'approved-second', 'approved-third']
        for message in messages:
            await host.continue_with(message, turn_id=original)
        if first_delivery == 'queued':
            assert len(host.sent) == 1 and len(s._follow_ups) == 3
            await host.finish()
        for index, message in enumerate(messages):
            assert host.sent[-1]['message'] == message
            assert host.sent[-1]['turn_id'] == original
            assert host.sent[-1]['request_id'] is None
            assert s._current_turn_id == s._last_user_turn_id == original
            expected_generation = generation + index + 1 if s.agent_cli == 'codex' else generation
            assert s.generation == expected_generation
            assert all(item['generation'] == expected_generation for item in s._follow_ups)
            await host.finish()
        assert [item['message'] for item in host.sent] == ['original', *messages]
        assert [event['text'] for event in host.events if event['type'] == 'user_prompt'] == ['original']
        assert not any(event['type'] in ('user_prompt', 'queued_input') for event in host.live)
        assert [event['text'] for event in host.events
                if event['type'] == 'system_continuation' and event['delivery'] == 'sent'] == messages
        assert terminal(host.events) == []
        assert s._current_turn_id is None and s._last_user_turn_id == original and not s.is_busy
    asyncio.run(scenario())


@pytest.mark.parametrize('new_turn_state', ['active', 'finished'])
def test_old_system_admission_is_rejected_even_with_current_runtime_identity(host, new_turn_state):
    async def scenario():
        m, s = host.manager, host.session
        await m.send_prompt(s, 'original')
        original = s._current_turn_id
        await host.finish()
        await m.send_follow_up(s, 'next-user', request_id='next')
        next_turn = s._current_turn_id
        if new_turn_state == 'finished':
            await host.finish()
        before = (deepcopy(host.sent), deepcopy(host.events), s._current_turn_id,
                  s._current_request_id, s.generation, s.status)
        await host.continue_with('obsolete', turn_id=original)
        assert before == (host.sent, host.events, s._current_turn_id,
                          s._current_request_id, s.generation, s.status)
        assert s._last_user_turn_id == next_turn and not s._follow_ups
    asyncio.run(scenario())


def test_dequeued_system_rechecks_last_user_turn_without_resetting_active_turn(host):
    async def scenario():
        m, s = host.manager, host.session
        await m.send_prompt(s, 'original')
        await m.send_follow_up(s, 'next-user', request_id='next')
        await host.continue_with('obsolete')
        obsolete = s._follow_ups[-1]
        await host.finish()
        # Matching native generation is insufficient authority to resume a previous user turn.
        obsolete['generation'] = s.generation
        before = (s._current_turn_id, s._current_request_id, s._last_user_turn_id, s.generation, s.status)
        async with s._input_lock:
            await m._deliver_follow_up(s)
        assert before == (s._current_turn_id, s._current_request_id, s._last_user_turn_id, s.generation, s.status)
        assert [item['message'] for item in host.sent] == ['original', 'next-user']
        assert terminal(host.events) == terminal(host.live) == [(obsolete['request_id'], 'unsent')]
        assert not s._follow_ups
        await host.finish()
        assert not s.is_busy
    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', ['host', 'generation', 'epoch', 'stopping'])
@pytest.mark.parametrize('stage', ['admission', 'queued'])
def test_runtime_fences_still_reject_system_results(host, invalid, stage):
    async def scenario():
        m, s = host.manager, host.session
        await m.send_prompt(s, 'original')
        origin = dict(turn_id=s._current_turn_id, host=s.agent_cli,
                      generation=s.generation, epoch=s.input_epoch)
        if stage == 'queued':
            await host.continue_with('obsolete')
            item = s._follow_ups[0]
            if invalid == 'stopping':
                s._stopping = True
            elif invalid == 'generation':
                item['generation'] += 1
            elif invalid == 'epoch':
                item['epoch'] = 'expired'
            else:
                item['host'] = 'codex' if s.agent_cli == 'pi' else 'pi'
            before = (s._current_turn_id, s._current_request_id, s.generation, s.status)
            async with s._input_lock:
                await m._deliver_follow_up(s)
            assert before == (s._current_turn_id, s._current_request_id, s.generation, s.status)
            assert terminal(host.events) == terminal(host.live) == [(item['request_id'], 'unsent')]
        else:
            if invalid == 'stopping':
                s._stopping = True
            elif invalid == 'generation':
                origin['generation'] += 1
            elif invalid == 'epoch':
                origin['epoch'] = 'expired'
            else:
                origin['host'] = 'codex' if s.agent_cli == 'pi' else 'pi'
            before = deepcopy(host.events)
            await m.send_system_continuation(s, 'obsolete', **origin)
            assert host.events == before
        assert [item['message'] for item in host.sent] == ['original']
        assert not s._follow_ups
    asyncio.run(scenario())


@pytest.mark.parametrize('first', ['user', 'system'])
@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError], ids=['error', 'cancelled'])
def test_mixed_queue_uncertain_delivery_classification_is_preserved(host, first, failure):
    async def scenario():
        m, s = host.manager, host.session
        await m.send_prompt(s, 'original')
        if first == 'user':
            await m.send_follow_up(s, 'failing', request_id='failing-user')
            await host.continue_with('not-attempted')
        else:
            await host.continue_with('failing')
            await m.send_follow_up(s, 'not-attempted', request_id='later-user')
        first_id, later_id = [item['request_id'] for item in s._follow_ups]
        host.failures['failing'] = failure
        if failure is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await host.finish()
        else:
            await host.finish()
        assert terminal(host.events) == terminal(host.live) == [(first_id, 'uncertain'), (later_id, 'unsent')]
        assert [item['message'] for item in host.sent] == ['original', 'failing']
        assert [event['text'] for event in host.events if event['type'] == 'user_prompt'] == ['original']
        assert not any(event.get('delivery') == 'sent' for event in host.events)
        assert not s._follow_ups and s._current_turn_id is None
    asyncio.run(scenario())


def test_active_turn_fallback_keeps_same_turn_results_when_last_user_is_not_yet_recorded(host):
    async def scenario():
        s = host.session
        s._current_turn_id, s.status = 'original', 'busy'
        assert s._last_user_turn_id is None
        for text in ('approved-first', 'approved-second'):
            await host.continue_with(text, turn_id='original')
        await host.finish()
        await host.finish()
        assert [(item['turn_id'], item['message']) for item in host.sent] == [
            ('original', 'approved-first'), ('original', 'approved-second')]
        assert not any(event['type'] in ('user_prompt', 'queued_input') for event in host.events)
    asyncio.run(scenario())
