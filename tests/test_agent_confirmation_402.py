"""A12 confirmation receipts, late admission and same-turn system continuation."""
import asyncio
from copy import deepcopy
import time
from types import SimpleNamespace

import pytest

import main
from app.routers import api_agent_tools as agent
from app.services import agent_batch
from app.services.agent_host import AgentHostManager, AgentSession, DEFAULT_SYSTEM_PROMPT, AUTONOMY_SYSTEM_PROMPT_SUFFIXES, agent_host_manager
from app.services.agent_ops_store import list_operations
from firefly_opsdb import OpsDb
from tests.test_agent_batch_400 import fx, rest, mcp, confirm, status_until, patch_node, tree
from tests.test_agent_generation_failures_401 import operation_results


def replay(fx):
    response = fx.client.get(f'/api/projects/{fx.pid}/agent-nodes/actor/events')
    assert response.is_success, response.text
    return [r['event'] for r in response.json()['events']]


def delete(fx, kind, target):
    path = f'canvas/{kind}/{target}' if kind != 'media' else f'media/{target}'
    return fx.client.delete(f'/api/projects/{fx.pid}/{path}?agent_node_id=actor', headers={'X-Agent-Project': fx.pid})


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    pytest.fail('Operation did not settle')


def test_standard_fifteen_images_one_prepared_confirmation_and_original_turn(fx):
    settings = deepcopy(fx.store.find_canvas_node(fx.pid, 'b')['runSettings'])
    ids = [f'gen-{i}' for i in range(15)]
    fx.store.apply_mutations(fx.pid, [{'op': 'upsert_node', 'node': {
        'id': ident, 'type': 'smart-image', 'prompt': ident, 'runSettings': settings}} for ident in ids],
        base_revision=fx.store.canvas(fx.pid)['revision'])
    pre = rest(fx, 'preflight', {'node_ids': ids})[1]
    receipt = rest(fx, 'request_batch', {'preflight_id': pre['preflight_id']})[1]
    event = fx.events.get(timeout=2)
    assert event['params']['preflight']['items'] == pre['items']
    assert event['target_node_ids'] == ids
    assert event['params']['total_images'] == 15
    fx.session._current_turn_id = None
    confirm(fx, event)
    result = status_until(fx, receipt['batch_id'], lambda s: s['status'] == 'done', timeout=20)
    assert result['spent'] == result['approved_count'] == 15 and fx.calls.qsize() == 15
    assert len([e for e in replay(fx) if e['type'] == 'confirm']) == 1
    ops = list_operations(OpsDb(fx.store.root), fx.pid, limit=100)
    assert ops and {op['turn_id'] for op in ops} == {'initiating-turn'}
    confirm(fx, event, expected=409)
    assert fx.calls.qsize() == 15


def test_cautious_batch_confirms_each_paid_item(fx):
    fx.session.autonomy = 'cautious'
    pre = rest(fx, 'preflight', {'node_ids': ['a', 'b']})[1]
    receipt = rest(fx, 'request_batch', {'preflight_id': pre['preflight_id']})[1]
    selection = fx.events.get(timeout=2)
    assert selection['params']['total_images'] == 2 and selection['target_node_ids'] == ['a', 'b']
    assert fx.calls.empty()
    confirm(fx, selection)
    first = None
    while first is None:
        event = fx.events.get(timeout=3)
        if event['type'] == 'confirm':
            first = event
    assert first['params']['total_images'] == 1
    assert first['target_node_ids'] == ['a']
    confirm(fx, first)
    wait_for(lambda: fx.calls.qsize() == 1)
    second = None
    while second is None:
        event = fx.events.get(timeout=3)
        if event['type'] == 'confirm':
            second = event
    assert second['confirm_id'] != first['confirm_id']
    assert second['params']['preflight']['items'][0]['node_id'] == 'b'
    assert second['target_node_ids'] == ['b']
    confirm(fx, second)
    final = status_until(fx, receipt['batch_id'], lambda s: s['status'] == 'done')
    assert final['approved_count'] == final['spent'] == fx.calls.qsize() == 2


def test_autonomous_paid_calls_remain_automatic_but_batch_requires_selection(fx):
    fx.session.autonomy = 'autonomous'
    response, result = rest(fx, 'trigger', {'node_id': 'b'})
    assert response == 200 and result.get('task_id'), result
    wait_for(lambda: fx.calls.qsize() == 1)
    pre = rest(fx, 'preflight', {'node_ids': ['a']})[1]
    receipt = rest(fx, 'request_batch', {'preflight_id': pre['preflight_id']})[1]
    assert receipt['status'] == 'awaiting_confirmation'
    event = fx.events.get(timeout=2)
    assert event['type'] == 'confirm' and fx.calls.qsize() == 1
    confirm(fx, event)
    final = status_until(fx, receipt['batch_id'], lambda s: s['status'] == 'done')
    assert final['spent'] == 1 and fx.calls.qsize() == 2
    resolved = fx.events.get(timeout=2)
    assert resolved['type'] == 'confirm_resolved' and resolved['confirm_id'] == event['confirm_id']
    assert fx.events.empty()


def test_paid_returns_pending_immediately_then_approval_executes_once(fx):
    start = time.monotonic()
    status, receipt = rest(fx, 'trigger', {'node_id': 'b'})
    assert status == 200 and receipt['code'] == 'CONFIRM_PENDING'
    assert time.monotonic() - start < 2 and fx.calls.empty()
    event = fx.events.get(timeout=2)
    assert event['confirm_id'] == receipt['confirm_id']
    assert event['params']['preflight']['final_prompt'] == 'Minimalist art'
    assert event['target_node_ids'] == ['b']
    saved = next(e for e in replay(fx) if e['type'] == 'confirm')
    assert saved == event
    confirm(fx, event)
    wait_for(lambda: fx.calls.qsize() == 1)
    confirm(fx, event)
    assert fx.calls.qsize() == 1


@pytest.mark.parametrize('change', ['generation', 'turn', 'provider'])
def test_already_started_paid_generation_settles_after_origin_changes(fx, change):
    fx.release.clear()
    generation, turn = fx.session.generation, fx.session._last_user_turn_id
    provider_url = fx.providers[0]['base_url']
    try:
        status, receipt = rest(fx, 'trigger', {'node_id': 'b'})
        assert status == 200 and receipt['code'] == 'CONFIRM_PENDING'
        confirm(fx, fx.events.get(timeout=2))
        wait_for(lambda: fx.calls.qsize() == 1)
        task_id, = [ident for ident, task in main.CANVAS_TASKS.items() if task.get('project_id') == fx.pid]
        if change == 'generation':
            fx.session._generation += 1
        elif change == 'turn':
            fx.session._last_user_turn_id = 'next-user-turn'
        else:
            fx.providers[0]['base_url'] = 'https://changed.invalid'
        fx.release.set()
        wait_for(lambda: main.CANVAS_TASKS[task_id].get('agent_settled'))
        task = main.CANVAS_TASKS[task_id]
        assert task['status'] == 'succeeded', task
        assert task['result']['history_record']['outputs']
        operations = list_operations(OpsDb(fx.store.root), fx.pid, limit=100)
        assert operations and {operation['turn_id'] for operation in operations} == {'initiating-turn'}
        assert fx.calls.qsize() == 1
    finally:
        fx.release.set()
        fx.session._generation = generation
        fx.session._last_user_turn_id = turn
        fx.providers[0]['base_url'] = provider_url


@pytest.mark.parametrize('ending', ['reject', 'stop', 'prompt', 'provider', 'generation', 'host', 'turn', 'actor'])
def test_pending_paid_reject_stop_or_stale_never_writes(fx, ending, operation_results):
    status, receipt = rest(fx, 'trigger', {'node_id': 'b'})
    assert status == 200 and receipt['code'] == 'CONFIRM_PENDING'
    event = fx.events.get(timeout=2)
    if ending == 'prompt':
        patch_node(fx, 'b', prompt='Changed')
    elif ending == 'provider':
        fx.providers[0]['base_url'] = 'https://changed.invalid'
    elif ending == 'generation':
        fx.session._generation += 1
    elif ending == 'host':
        fx.session.agent_cli = 'codex'
    elif ending == 'turn':
        fx.session._last_user_turn_id = 'another-user-turn'
    elif ending == 'actor':
        fx.store.apply_mutations(fx.pid, [{'op': 'remove_node', 'node_id': 'actor'}],
                                 base_revision=fx.store.canvas(fx.pid)['revision'])
    elif ending == 'stop':
        fx.client.portal.call(agent_host_manager.stop_session, fx.session)
    before = tree(fx)
    confirm(fx, event, ending != 'reject')
    outcome = operation_results.get(timeout=5)
    assert outcome['error']['code'] in ('CONFIRM_REJECTED', 'CONFIRM_PENDING', 'CONFIRM_STALE'), outcome
    assert not fx.session.confirm_handlers
    assert fx.calls.empty() and tree(fx) == before
    assert agent._GENERATION_COUNTS.get((fx.pid, 'actor'), 0) == 0


def test_over_ninety_seconds_batch_remains_live_replayable_and_late_approval_runs_once(fx, monkeypatch):
    pre = rest(fx, 'preflight', {'node_ids': ['b']})[1]
    receipt = rest(fx, 'request_batch', {'preflight_id': pre['preflight_id']})[1]
    event = fx.events.get(timeout=2)
    clock = time.monotonic
    monkeypatch.setattr(agent_batch, 'time', SimpleNamespace(monotonic=lambda: clock() + 901))
    status = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})[1]
    assert status['status'] == 'awaiting_confirmation'
    assert fx.session.confirm_handlers and fx.calls.empty()
    events = replay(fx)
    assert any(e.get('confirm_id') == event['confirm_id'] for e in events)
    assert not any(e['type'] == 'confirm_resolved' for e in events)
    assert next(e for e in events if e['type'] == 'confirm')['target_node_ids'] == ['b']
    confirm(fx, event)
    final = status_until(fx, receipt['batch_id'], lambda s: s['status'] == 'done')
    assert final['spent'] == fx.calls.qsize() == 1
    confirm(fx, event, expected=409)
    assert fx.calls.qsize() == 1


@pytest.mark.parametrize('kind,target', [('nodes', 'b'), ('edges', 'ref/a'), ('media', None)])
def test_autonomous_safety_floor_pending_survives_sync_window(fx, monkeypatch, kind, target):
    fx.session.autonomy = 'autonomous'
    monkeypatch.setattr(agent, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .02)
    target = target or fx.ids[0]
    before = tree(fx)
    response = delete(fx, kind, target)
    assert response.status_code == 200 and response.json()['code'] == 'CONFIRM_PENDING'
    event = fx.events.get(timeout=2)
    assert event['confirm_id'] == response.json()['confirm_id'] and fx.session.confirm_handlers
    assert tree(fx) == before
    assert not any(e['type'] == 'confirm_resolved' for e in replay(fx))
    confirm(fx, event, False)
    wait_for(lambda: not fx.session.confirm_handlers)
    assert tree(fx) == before


def test_late_delete_approval_runs_once_and_forged_ownership_never_bypasses(fx, monkeypatch):
    fx.session.autonomy = 'autonomous'
    monkeypatch.setattr(agent, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .02)
    patch_node(fx, 'b', createdBy='agent', agentNodeId='actor', turn_id='initiating-turn')
    response = delete(fx, 'nodes', 'b')
    assert response.status_code == 200 and response.json()['code'] == 'CONFIRM_PENDING'
    event = fx.events.get(timeout=2)
    confirm(fx, event)
    wait_for(lambda: fx.store.find_canvas_node(fx.pid, 'b') is None)
    count = len(list_operations(OpsDb(fx.store.root), fx.pid))
    confirm(fx, event)
    assert len(list_operations(OpsDb(fx.store.root), fx.pid)) == count


def test_same_turn_server_journal_allows_own_delete_but_user_touch_and_old_turn_gate(fx, monkeypatch):
    fx.session.autonomy = 'autonomous'
    monkeypatch.setattr(agent, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .02)
    def create(ident):
        response = fx.client.post(f'/api/projects/{fx.pid}/canvas/nodes?agent_node_id=actor',
            headers={'X-Agent-Project': fx.pid}, json={'agent_node_id': 'actor', 'node': {'title': ident, 'type': 'smart-image'}})
        assert response.is_success, response.text
        return response.json()['node_id']
    owned = create('own-new')
    assert delete(fx, 'nodes', owned).json()['success'] is True
    assert fx.events.empty()
    for reason in ('touch', 'old-turn'):
        owned = create('new-' + reason)
        if reason == 'touch':
            patch_node(fx, owned, userTouchedAt=123)
        else:
            fx.session._current_turn_id = 'next-turn'
        response = delete(fx, 'nodes', owned)
        assert response.json()['code'] == 'CONFIRM_PENDING'
        confirm(fx, fx.events.get(timeout=2), False)
        wait_for(lambda: not fx.session.confirm_handlers)
        while not fx.events.empty():
            fx.events.get_nowait()


@pytest.mark.parametrize('host_name', ['pi', 'codex'])
def test_system_continuation_same_turn_host_and_busy_fifo_without_user_inputs(monkeypatch, host_name):
    manager = AgentHostManager()
    session = AgentSession('project', 'actor', agent_cli=host_name, status='busy', _current_turn_id='original')
    manager.sessions['project:actor'] = session
    sent, events = [], []
    monkeypatch.setattr(manager, '_persist_event', lambda session, event, **kw: events.append(deepcopy(event)))
    async def command(s, payload):
        sent.append((s._current_turn_id, payload['message'], payload['type']))
    async def codex(s, message):
        sent.append((s._current_turn_id, message, 'codex'))
    monkeypatch.setattr(manager, 'send_command', command)
    monkeypatch.setattr(manager, '_codex_turn', codex)
    async def scenario():
        for text in ('approved-first', 'approved-second'):
            await manager.send_system_continuation(session, text, turn_id='original', host=host_name,
                generation=session.generation, epoch=session.input_epoch)
        assert not sent and len(session._follow_ups) == 2
        for _ in range(2):
            session.status = 'running'
            session._current_turn_id = None
            await manager._deliver_follow_up(session)
        assert [x[:2] for x in sent] == [('original', 'approved-first'), ('original', 'approved-second')]
        assert session._current_turn_id == 'original' and session.agent_cli == host_name
        assert not any(e['type'] in ('user_prompt', 'queued_input') for e in events)
        before = list(sent)
        await manager.send_system_continuation(session, 'stale', turn_id='original', host=host_name,
            generation=session.generation + 1, epoch=session.input_epoch)
        assert sent == before
    asyncio.run(scenario())


@pytest.mark.parametrize('actor', ['ghost', 'b'])
def test_missing_or_non_agent_actor_cannot_open_paid_confirmation(fx, actor):
    before = tree(fx)
    status, result = rest(fx, 'trigger', {'node_id': 'b'}, actor=actor)
    assert status == 404 and result['detail']['code'] == 'NODE_NOT_FOUND'
    assert fx.events.empty() and fx.calls.empty() and tree(fx) == before
    assert agent._GENERATION_COUNTS.get((fx.pid, actor), 0) == 0


@pytest.mark.parametrize('change', ['host', 'generation'])
def test_invalidated_card_is_resolved_in_durable_replay(fx, change):
    rest(fx, 'trigger', {'node_id': 'b'})
    event = fx.events.get(timeout=2)
    if change == 'host':
        fx.session.agent_cli = 'codex'
    else:
        fx.session._generation += 1
    wait_for(lambda: not fx.session.confirm_handlers)
    resolved = [e for e in replay(fx) if e['type'] == 'confirm_resolved' and e['confirm_id'] == event['confirm_id']]
    assert len(resolved) == 1 and not resolved[0]['approved']
    assert resolved[0]['host'] == 'pi' and fx.calls.empty()


def test_external_mcp_legacy_batch_never_continues_internal_carrier(fx, monkeypatch):
    fx.session.status = 'busy'
    fx.session._last_user_turn_id = 'initiating-turn'
    delivered = []
    async def continuation(*args, **kwargs):
        delivered.append((args, kwargs))
    monkeypatch.setattr(agent_host_manager, 'send_system_continuation', continuation)
    response = fx.client.post(f'/api/mcp?project={fx.pid}&node=actor', headers={'Mcp-Session-Id': 'external-a12'},
        json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
              'params': {'name': 'canvas.batch_edit', 'arguments': {'node_ids': ['b'], 'prompt': 'External task'}}})
    result = response.json()['result']
    assert not result['isError'], result
    event = fx.events.get(timeout=2)
    confirm(fx, event)
    wait_for(lambda: fx.calls.qsize() == 1)
    wait_for(lambda: not fx.session.confirm_handlers)
    assert not delivered and not fx.session._follow_ups


def test_default_standard_and_confirmation_instructions_are_unified():
    session = AgentSession('project', 'actor')
    assert session.autonomy == 'standard'
    assert len(set(AUTONOMY_SYSTEM_PROMPT_SUFFIXES.values())) == 1
    assert 'CONFIRM_PENDING' in DEFAULT_SYSTEM_PROMPT
    assert '必须先问用户确认' not in DEFAULT_SYSTEM_PROMPT
