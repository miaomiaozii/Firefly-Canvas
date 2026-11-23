"""Human batch decisions keep their strict shape across public transports (#487)."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_agent_batch_400 import fx, patch_node, rest, status_until


def _transports_start_batch_487(fx, nodes=None):
    code, preview = rest(fx, 'preflight', {'node_ids': nodes or ['a', 'b']})
    assert code == 200 and not preview['blockers'], preview
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200, receipt
    while True:
        event = fx.events.get(timeout=3)
        if event['type'] == 'confirm':
            return preview, receipt, event


def preview_url(fx, receipt, actor='actor', project=None):
    return f'/api/projects/{project or fx.pid}/agent-nodes/{actor}/batches/{receipt["batch_id"]}/preview'


def gate_handler(fx, event):
    return next(handler for handler in fx.session.confirm_handlers
                if getattr(handler, 'batch_confirm_id', None) == event['confirm_id'])


def dispatch(fx, transport, body, *, handler=None, token=None, actor='actor', project=None):
    if transport == 'callback':
        try:
            fx.client.portal.call(handler, body['confirm_id'], body)
        except HTTPException as error:
            return error.status_code, error.detail
        return 200, {'ok': True}
    if transport == 'http':
        response = fx.client.post(f'/api/projects/{project or fx.pid}/agent-nodes/{actor}/confirm',
                                  headers={'X-Agent-Project': project or fx.pid}, json=body)
        return response.status_code, response.json().get('detail', response.json())
    with fx.client.websocket_connect(f'ws://localhost/api/projects/{project or fx.pid}/agent-nodes/{actor}/stream') as ws:
        assert ws.receive_json()['type'] == 'session'
        ws.send_json({'type': 'confirm', **body, **({'decision_token': token} if token is not None else {})})
        while True:
            result = ws.receive_json()
            if result['type'] in ('error', 'confirm_result'):
                return result.get('status', 200), result


def read_gate(fx, receipt):
    response = fx.client.get(preview_url(fx, receipt), headers={'X-Agent-Project': fx.pid})
    assert response.status_code == 200, response.text
    return response.json()


def test_published_batch_gate_has_registered_callback_before_notification(fx):
    observed = []

    async def approve_published_gate(session, event):
        if event['type'] != 'confirm':
            return
        handlers = [handler for handler in session.confirm_handlers
                    if getattr(handler, 'batch_confirm_id', None) == event['confirm_id']]
        observed.append(len(handlers))
        if handlers:
            await handlers[0](event['confirm_id'], True)

    fx.session.event_handlers.append(approve_published_gate)
    try:
        _, receipt, _ = _transports_start_batch_487(fx, ['a'])
        assert observed == [1]
        final = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
        assert final['status'] == 'done'
        assert final['spent'] == final['approved_count'] == 1
        assert fx.calls.qsize() == 1
    finally:
        fx.session.event_handlers.remove(approve_published_gate)


def test_legal_human_preview_reads_current_gate_without_side_effects(fx):
    preview, receipt, event = _transports_start_batch_487(fx)
    before = fx.store.canvas(fx.pid)
    session_identity = (fx.session.session_id, fx.session.generation, fx.session.input_epoch,
                        fx.session._current_turn_id, fx.session._reader_task, fx.session.is_running)
    responses = [fx.client.get(preview_url(fx, receipt), headers={'X-Agent-Project': fx.pid}) for _ in range(2)]
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    current = responses[0].json()
    assert responses[1].json() == current
    assert current['batch_id'] == receipt['batch_id'] and current['status'] == 'awaiting_confirmation'
    assert current['preflight'] == {key: value for key, value in preview.items() if key != 'preflight_id'}
    assert current['confirm_id'] == event['confirm_id'] and current['actionable'] is True
    assert isinstance(current['decision_token'], str) and len(current['decision_token']) >= 32
    assert fx.store.canvas(fx.pid) == before and fx.calls.empty() and fx.events.empty()
    assert (fx.session.session_id, fx.session.generation, fx.session.input_epoch,
            fx.session._current_turn_id, fx.session._reader_task, fx.session.is_running) == session_identity
    assert rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})[0] == 200


@pytest.mark.parametrize('transport', ['http', 'callback', 'ws'])
def test_http_ws_and_registered_callback_accept_identical_subset(fx, transport):
    for node, count in (('a', 1), ('b', 3), ('ref', 2)):
        patch_node(fx, node, prompt='Transport ' + node,
                   runSettings={'model': 'fixture', 'provider_id': 'offline', 'routingMode': 'manual',
                                'ratio': 'landscape169', 'count': count})
    preview, receipt, event = _transports_start_batch_487(fx, ['a', 'b', 'ref'])
    body = {'confirm_id': event['confirm_id'], 'approved': True,
            'approved_item_ids': [preview['items'][1]['item_id']], 'snapshot_sha256': preview['snapshot_sha256']}
    if transport == 'http':
        response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                                  headers={'X-Agent-Project': fx.pid}, json=body)
        assert response.status_code == 200, response.text
    elif transport == 'callback':
        handler = next(handler for handler in fx.session.confirm_handlers
                       if getattr(handler, 'batch_confirm_id', None) == event['confirm_id'])
        fx.client.portal.call(handler, event['confirm_id'], body)
    else:
        current = fx.client.get(preview_url(fx, receipt), headers={'X-Agent-Project': fx.pid})
        assert current.status_code == 200, current.text
        with fx.client.websocket_connect(f'ws://localhost/api/projects/{fx.pid}/agent-nodes/actor/stream') as ws:
            assert ws.receive_json()['type'] == 'session'
            ws.send_json({'type': 'confirm', **body, 'decision_token': current.json()['decision_token']})
            final = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
    assert final['status'] == 'done', final
    assert final['spent'] == final['approved_count'] == 3, final
    assert final['approved_item_ids'] == body['approved_item_ids']
    assert [row['status'] for row in final['items']] == ['skipped', 'succeeded', 'skipped']
    assert [row['item_id'] for row in final['items'] if row['task_id']] == body['approved_item_ids']
    task = fx.client.get(f'/api/projects/{fx.pid}/generate/tasks/{final["items"][1]["task_id"]}')
    assert task.status_code == 200, task.text
    assert task.json()['status'] == 'succeeded' and task.json()['result']['prompt'] == 'Transport b'
    assert len(task.json()['result']['project_asset_ids']) == 3
    assert fx.store.find_canvas_node(fx.pid, final['items'][1]['result_node_id'])['images']
    assert fx.calls.qsize() == 3
    assert [fx.calls.get_nowait()[0] for _ in range(3)] == ['Transport b'] * 3


@pytest.mark.parametrize('transport', ['http', 'callback', 'ws'])
@pytest.mark.parametrize('ending', ['reject', 'double', 'race'])
def test_reject_stale_and_double_decision_never_execute(fx, transport, ending):
    preview, receipt, event = _transports_start_batch_487(fx, ['b'])
    handler = gate_handler(fx, event)
    token = read_gate(fx, receipt)['decision_token']
    body = {'confirm_id': event['confirm_id'], 'approved': ending != 'reject',
            'snapshot_sha256': preview['snapshot_sha256']}
    before = fx.store.canvas(fx.pid)
    if ending == 'race':
        barrier = threading.Barrier(2)

        def decide(approved):
            barrier.wait(timeout=3)
            return approved, dispatch(fx, transport, {**body, 'approved': approved}, handler=handler, token=token)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, [True, False]))
        assert sorted(result[0] for _, result in results) == [200, 409], results
        winner = next(approved for approved, result in results if result[0] == 200)
    else:
        code, result = dispatch(fx, transport, body, handler=handler, token=token)
        assert code == 200, result
        winner = body['approved']
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
    assert final['status'] == ('done' if winner else 'rejected'), final
    assert final['spent'] == final['approved_count'] == int(winner)
    assert fx.calls.qsize() == int(winner)
    code, result = dispatch(fx, transport, {**body, 'approved': True}, handler=handler, token=token)
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert fx.calls.qsize() == int(winner)
    assert read_gate(fx, receipt)['decision_token'] is None
    if not winner:
        assert fx.store.canvas(fx.pid) == before


@pytest.mark.parametrize('boundary', ['get', 'confirm'])
@pytest.mark.parametrize('guard', ['query', 'missing-origin', 'foreign-origin', 'missing-project',
                                   'wrong-project', 'duplicate-project', 'actor', 'project'])
def test_human_preview_and_confirm_guards(fx, boundary, guard):
    preview, receipt, event = _transports_start_batch_487(fx, ['b'])
    assert read_gate(fx, receipt)['actionable'] is True
    before = fx.store.canvas(fx.pid)
    url = (preview_url(fx, receipt) if boundary == 'get'
           else f'/api/projects/{fx.pid}/agent-nodes/actor/confirm')
    body = {'confirm_id': event['confirm_id'], 'approved': True}
    headers = [('X-Agent-Project', fx.pid)]
    expected = 403
    if guard == 'query':
        url += '?source=human&source=mcp'
        expected = 400
    elif guard == 'missing-project':
        headers = []
    elif guard == 'wrong-project':
        headers = [('X-Agent-Project', fx.other)]
    elif guard == 'duplicate-project':
        headers *= 2
    elif guard == 'actor':
        url = url.replace('/agent-nodes/actor/', '/agent-nodes/second-actor/')
        expected = 409
    elif guard == 'project':
        url = url.replace(f'/projects/{fx.pid}/', f'/projects/{fx.other}/')
        headers = [('X-Agent-Project', fx.other)]
        expected = 409
    request = fx.client.build_request('GET' if boundary == 'get' else 'POST', url,
                                      headers=headers, **({'json': body} if boundary != 'get' else {}))
    if guard == 'missing-origin':
        del request.headers['Origin']
    elif guard == 'foreign-origin':
        request.headers['Origin'] = 'http://foreign.invalid'
    response = fx.client.send(request)
    assert response.status_code == expected, response.text
    assert fx.store.canvas(fx.pid) == before and fx.calls.empty()
    _, pending = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert pending['status'] == 'awaiting_confirmation' and pending['spent'] == pending['approved_count'] == 0
    assert read_gate(fx, receipt)['confirm_id'] == event['confirm_id']
    code, result = dispatch(fx, 'http', body)
    assert code == 200, result
    assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')['spent'] == 1


def test_ws_whole_uses_new_token_for_each_real_cautious_gate(fx):
    fx.session.autonomy = 'cautious'
    preview, receipt, event = _transports_start_batch_487(fx, ['b'])
    selection = read_gate(fx, receipt)
    code, result = dispatch(fx, 'ws', {'confirm_id': event['confirm_id'], 'approved': True},
                            token=selection['decision_token'])
    assert code == 200, result
    while True:
        item_gate = fx.events.get(timeout=3)
        if item_gate['type'] == 'confirm':
            break
    current = read_gate(fx, receipt)
    assert current['confirm_id'] == item_gate['confirm_id'] != selection['confirm_id']
    assert current['decision_token'] != selection['decision_token']
    assert read_gate(fx, receipt) == current
    assert current['preflight']['snapshot_sha256'] == preview['snapshot_sha256']
    code, result = dispatch(fx, 'ws', {'confirm_id': item_gate['confirm_id'], 'approved': True},
                            token=selection['decision_token'])
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    _, pending = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert pending['spent'] == pending['approved_count'] == 0 and fx.calls.empty()
    code, result = dispatch(fx, 'ws', {'confirm_id': item_gate['confirm_id'], 'approved': True},
                            token=current['decision_token'])
    assert code == 200, result
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == 1 and fx.calls.qsize() == 1


@pytest.mark.parametrize('guard', ['missing-whole', 'missing-subset', 'wrong-token', 'actor', 'project',
                                   'json-header', 'wrong-hash'])
def test_ws_requires_http_bound_current_decision_token(fx, guard):
    preview, receipt, event = _transports_start_batch_487(fx, ['b'])
    current = read_gate(fx, receipt)
    before = fx.store.canvas(fx.pid)
    body = {'confirm_id': event['confirm_id'], 'approved': True}
    if guard == 'missing-subset':
        body.update(approved_item_ids=[preview['items'][0]['item_id']], snapshot_sha256=preview['snapshot_sha256'])
    token = current['decision_token']
    actor, project, expected = 'actor', fx.pid, 409
    if guard.startswith('missing'):
        token = None
    elif guard == 'wrong-token':
        token = 'not-issued'
    elif guard == 'actor':
        actor = 'second-actor'
    elif guard == 'project':
        project = fx.other
    elif guard == 'json-header':
        token = None
        body['X-Agent-Project'] = fx.pid
        expected = 400
    elif guard == 'wrong-hash':
        body['snapshot_sha256'] = '0' * 64
    code, result = dispatch(fx, 'ws', body, token=token, actor=actor, project=project)
    assert code == expected, result
    assert result['code'] == ('INVALID_ARGUMENT' if expected == 400 else 'CONFIRM_STALE')
    assert read_gate(fx, receipt) == current
    assert fx.store.canvas(fx.pid) == before and fx.calls.empty()
    _, pending = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert pending['spent'] == pending['approved_count'] == 0
    code, result = dispatch(fx, 'ws', {'confirm_id': event['confirm_id'], 'approved': True},
                            token=current['decision_token'])
    assert code == 200, result
    assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')['spent'] == 1


def invalidate_fixture_origin(fx, change):
    from app.services.agent_host import agent_host_manager as host
    if change == 'stop':
        response = fx.client.delete(f'/api/projects/{fx.pid}/agent-nodes/actor')
        assert response.status_code == 200, response.text
    elif change == 'host':
        fx.session.agent_cli = 'codex'
    elif change == 'generation':
        fx.session._generation += 1
    elif change == 'turn':
        fx.session._last_user_turn_id = 'new-user-turn-487'
    elif change in ('actor', 'actor-unbound'):
        actor = fx.store.find_canvas_node(fx.pid, 'actor')
        binding = '' if change == 'actor-unbound' else 'rebound-487'
        fx.store.apply_agent_host_mutations(fx.pid, [{'op': 'upsert_node', 'node': {**actor, 'mcp_session': binding}}],
                                            base_revision=fx.store.canvas(fx.pid)['revision'])
    else:
        assert fx.client.portal.call(host.drop_node_session, fx.pid, 'actor')


@pytest.mark.parametrize('change', ['stop', 'host', 'generation', 'turn', 'actor', 'actor-unbound'])
@pytest.mark.parametrize('phase', ['pending', 'repreflight', 'replaced'])
def test_lifecycle_invalidates_gate_and_token_across_repreflight(fx, monkeypatch, phase, change):
    from tests.test_agent_batch_preview_edit_487 import _preview_edit_mutate_487, provider_io_barrier
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    if phase == 'replaced':
        old, current = revise_gate(fx, receipt, 'edit')
        event = {'confirm_id': current['confirm_id']}
    else:
        current = old
    handler = gate_handler(fx, event)
    if phase == 'repreflight':
        entered, release = provider_io_barrier(fx, monkeypatch, call_number=3)
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(_preview_edit_mutate_487, fx, receipt, current)
            try:
                assert entered.wait(3)
                invalidate_fixture_origin(fx, change)
            finally:
                release.set()
            response = job.result(5)
        assert response.status_code == 409, response.text
    else:
        invalidate_fixture_origin(fx, change)
    body = {'confirm_id': current['confirm_id'], 'approved': True,
            'snapshot_sha256': current['preflight']['snapshot_sha256']}
    for transport in ('callback', 'ws'):
        code, result = dispatch(fx, transport, body, handler=handler, token=current['decision_token'])
        assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert fx.calls.empty()
    assert fx.session._reader_task is None and not fx.session.is_running
    replay = fx.client.get(f'/api/projects/{fx.pid}/agent-nodes/actor/events', params={'host': 'pi'})
    assert replay.status_code == 200
    assert not any(row['event']['type'] in ('user_prompt', 'system_continuation') for row in replay.json()['events'])


def test_actor_rebind_resolves_old_gate_without_another_decision(fx):
    from queue import Empty
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    invalidate_fixture_origin(fx, 'actor')
    try:
        resolved = fx.events.get(timeout=1)
    except Empty:
        resolved = None
    assert resolved is not None and resolved['type'] == 'confirm_resolved', 'rebound actor must invalidate the real waiter'
    assert resolved['confirm_id'] == event['confirm_id'] and resolved['reason'] == 'session_ended'
    code, result = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert code == 404 and result['detail']['code'] == 'BATCH_NOT_FOUND' and fx.calls.empty()


def test_external_origin_session_release_never_wakes_another_host(fx):
    from app.services.agent_host import agent_host_manager as host
    sid = initialize_mcp(fx)
    ok, preview = mcp_call(fx, sid, 'preflight', {'node_ids': ['b']})
    assert ok
    ok, receipt = mcp_call(fx, sid, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert ok
    event = fx.events.get(timeout=3)
    old = read_gate(fx, receipt)
    handler = gate_handler(fx, event)
    invalidate_fixture_origin(fx, 'session-drop')
    replacement = fx.client.portal.call(host.get_or_create_session, fx.pid, 'actor')
    assert replacement is not fx.session and not replacement.is_running
    assert dispatch(fx, 'callback', {'confirm_id': event['confirm_id'], 'approved': True}, handler=handler)[0] == 409
    assert dispatch(fx, 'ws', {'confirm_id': event['confirm_id'], 'approved': True}, token=old['decision_token'])[0] == 409
    ok, result = mcp_call(fx, sid, 'batch_status', {'batch_id': receipt['batch_id']})
    assert not ok and result['code'] == 'BATCH_NOT_FOUND'
    assert not replacement.confirm_handlers and replacement._reader_task is None and fx.calls.empty()
    replay = fx.client.get(f'/api/projects/{fx.pid}/agent-nodes/actor/events', params={'host': 'pi'})
    assert not any(row['event']['type'] in ('user_prompt', 'system_continuation') for row in replay.json()['events'])


def test_multiple_pending_batches_receive_independent_audited_receipts(fx):
    from concurrent.futures import TimeoutError
    _, first, first_event = _transports_start_batch_487(fx, ['b'])
    code, preview = rest(fx, 'preflight', {'node_ids': ['a']})
    assert code == 200
    responsive = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(rest, fx, 'request_batch', {'preflight_id': preview['preflight_id']})
        try:
            code, second = job.result(1)
            responsive = code == 200
        except TimeoutError:
            pass
        finally:
            assert dispatch(fx, 'http', {'confirm_id': first_event['confirm_id'], 'approved': False})[0] == 200
        code, second = job.result(5)
    assert code == 200
    current = read_gate(fx, second)
    assert dispatch(fx, 'http', {'confirm_id': current['confirm_id'], 'approved': False})[0] == 200
    assert status_until(fx, second['batch_id'], lambda row: row['status'] == 'rejected')['spent'] == 0
    assert responsive, 'another pending batch must not hold this request behind a human decision'
    assert fx.calls.empty()


def test_ws_batch_decision_requires_real_handshake_origin(fx):
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    current = read_gate(fx, receipt)
    original = fx.client.headers.pop('Origin')
    try:
        code, result = dispatch(fx, 'ws', {'confirm_id': event['confirm_id'], 'approved': True},
                                token=current['decision_token'])
    finally:
        fx.client.headers['Origin'] = original
    assert code == 403, result
    assert read_gate(fx, receipt) == current and fx.calls.empty()
    assert rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})[0] == 200


@pytest.mark.parametrize('transport', ['http', 'callback', 'ws'])
def test_rejection_cannot_consume_gate_with_stale_snapshot(fx, transport):
    preview, receipt, event = _transports_start_batch_487(fx, ['b'])
    handler = gate_handler(fx, event)
    current = read_gate(fx, receipt)
    code, result = dispatch(fx, transport, {'confirm_id': event['confirm_id'], 'approved': False,
                                           'snapshot_sha256': '0' * 64},
                            handler=handler, token=current['decision_token'])
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert read_gate(fx, receipt) == current and fx.calls.empty()
    code, result = dispatch(fx, transport, {'confirm_id': event['confirm_id'], 'approved': False,
                                           'snapshot_sha256': preview['snapshot_sha256']},
                            handler=handler, token=current['decision_token'])
    assert code == 200, result
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')
    assert final['spent'] == final['approved_count'] == 0 and fx.calls.empty()


def initialize_mcp(fx):
    response = fx.client.post('/api/mcp', json={
        'jsonrpc': '2.0', 'id': 487, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'confirmation-transport-test', 'version': '1'}}})
    assert response.status_code == 200 and 'result' in response.json(), response.text
    return response.headers['Mcp-Session-Id']


def mcp_call(fx, sid, name, args):
    response = fx.client.post(f'/api/mcp?project={fx.pid}&node=actor', headers={'Mcp-Session-Id': sid},
                              json={'jsonrpc': '2.0', 'id': 487, 'method': 'tools/call',
                                    'params': {'name': 'generate.' + name, 'arguments': args}})
    assert response.status_code == 200 and response.headers['Mcp-Session-Id'] == sid, response.text
    result = response.json()['result']
    if result['isError']:
        return False, result['structuredContent']
    return True, json.loads(result['content'][0]['text'])


def test_external_sid_scope_and_human_decision_keep_original_identity(fx):
    sid, other_sid = initialize_mcp(fx), initialize_mcp(fx)
    assert sid != other_sid
    identity = (fx.session.session_id, fx.session.generation, fx.session.input_epoch,
                fx.session._current_turn_id, fx.session._reader_task, fx.session.is_running)
    ok, preview = mcp_call(fx, sid, 'preflight', {'node_ids': ['b']})
    assert ok and not preview['blockers'], preview
    ok, wrong = mcp_call(fx, other_sid, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert not ok and wrong['code'] == 'PREFLIGHT_NOT_FOUND', wrong
    ok, receipt = mcp_call(fx, sid, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert ok, receipt
    event = fx.events.get(timeout=3)
    ok, wrong = mcp_call(fx, other_sid, 'batch_status', {'batch_id': receipt['batch_id']})
    assert not ok and wrong['code'] == 'BATCH_NOT_FOUND', wrong
    ok, wrong = mcp_call(fx, other_sid, 'stop_batch', {'batch_id': receipt['batch_id']})
    assert not ok and wrong['code'] == 'BATCH_NOT_FOUND', wrong
    current = read_gate(fx, receipt)
    assert current['actionable'] is True
    code, result = dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True,
                                        'approved_item_ids': [preview['items'][0]['item_id']],
                                        'snapshot_sha256': preview['snapshot_sha256']})
    assert code == 200, result
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ok, final = mcp_call(fx, sid, 'batch_status', {'batch_id': receipt['batch_id'], 'wait_seconds': .1})
        assert ok, final
        if final['status'] == 'done':
            break
    assert final['status'] == 'done' and final['spent'] == final['approved_count'] == 1
    assert fx.calls.qsize() == 1
    assert (fx.session.session_id, fx.session.generation, fx.session.input_epoch,
            fx.session._current_turn_id, fx.session._reader_task, fx.session.is_running) == identity
    response = fx.client.get(f'/api/projects/{fx.pid}/agent-nodes/actor/events?host=pi')
    assert response.status_code == 200, response.text
    assert not any(row['event']['type'] == 'user_prompt' for row in response.json()['events'])


@pytest.mark.parametrize('transport', ['http', 'callback', 'ws'])
def test_confirmation_remains_bound_to_original_store(fx, monkeypatch, transport):
    import main
    from firefly_projects import ProjectStore

    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    current = read_gate(fx, receipt)
    handler = gate_handler(fx, event)
    fx.store.flush(fx.pid)
    alternate = ProjectStore(fx.store.root, snapshot_delay=0)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(main, 'FIREFLY_PROJECT_STORE', alternate)
            response = fx.client.get(preview_url(fx, receipt), headers={'X-Agent-Project': fx.pid})
            assert response.status_code == 409, response.text
            code, result = dispatch(fx, transport, {'confirm_id': event['confirm_id'], 'approved': True},
                                    handler=handler, token=current['decision_token'])
            assert code == 409 and result['code'] == 'CONFIRM_STALE', result
            assert fx.calls.empty()
    finally:
        alternate.close()
    assert read_gate(fx, receipt) == current
    code, result = dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': False})
    assert code == 200, result
    assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')['spent'] == 0


@pytest.mark.parametrize('mismatch', ['body', 'argument'])
def test_registered_callback_cannot_retarget_confirmation(fx, mismatch):
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    handler = gate_handler(fx, event)
    body = {'confirm_id': event['confirm_id'], 'approved': True}
    cid = event['confirm_id']
    if mismatch == 'body':
        body['confirm_id'] = 'batch-confirm-not-this-gate'
    else:
        cid = 'batch-confirm-not-this-gate'
    try:
        fx.client.portal.call(handler, cid, body)
    except HTTPException as error:
        code, result = error.status_code, error.detail
    else:
        code, result = 200, None
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert read_gate(fx, receipt)['actionable'] is True and fx.calls.empty()
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': False})[0] == 200
    assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')['spent'] == 0


def test_preview_get_neither_mints_token_nor_extends_lifetime(fx, monkeypatch):
    from app.services import agent_batch, agent_confirmation

    clock = [100.0]
    monkeypatch.setattr(agent_batch, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    _, receipt, event = _transports_start_batch_487(fx, ['b'])

    def unavailable_random(*args):
        raise AssertionError('A read must not mint a new decision capability')

    monkeypatch.setattr(agent_confirmation.secrets, 'token_urlsafe', unavailable_random)
    current = read_gate(fx, receipt)
    clock[0] = 3699.0
    assert read_gate(fx, receipt) == current
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': False})[0] == 200
    assert status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'rejected')['spent'] == 0
    clock[0] = 3701.0
    response = fx.client.get(preview_url(fx, receipt), headers={'X-Agent-Project': fx.pid})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'CONFIRM_STALE'
    assert fx.calls.empty()


@pytest.mark.parametrize('field', ['source', 'caller', 'Mcp-Session-Id', 'turn_id', 'host', 'generation'])
@pytest.mark.parametrize('boundary', ['human-get', 'human-confirm', 'mcp'])
def test_fixed_human_source_cannot_be_forged(fx, boundary, field):
    sid = initialize_mcp(fx)
    ok, preview = mcp_call(fx, sid, 'preflight', {'node_ids': ['b']})
    assert ok, preview
    ok, receipt = mcp_call(fx, sid, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert ok, receipt
    event = fx.events.get(timeout=3)
    current = read_gate(fx, receipt)
    if boundary == 'human-get':
        response = fx.client.get(preview_url(fx, receipt), params={field: 'human'},
                                 headers={'X-Agent-Project': fx.pid})
        code, result = response.status_code, response.json()['detail']
    elif boundary == 'human-confirm':
        code, result = dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': True, field: 'human'})
    else:
        ok, result = mcp_call(fx, sid, 'batch_status', {'batch_id': receipt['batch_id'], field: 'human'})
        code = 200 if ok else 400
    assert code == 400 and result['code'] == 'INVALID_ARGUMENT', result
    assert read_gate(fx, receipt) == current and fx.calls.empty()
    assert dispatch(fx, 'http', {'confirm_id': event['confirm_id'], 'approved': False})[0] == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ok, final = mcp_call(fx, sid, 'batch_status', {'batch_id': receipt['batch_id'], 'wait_seconds': .1})
        assert ok, final
        if final['status'] == 'rejected':
            break
    assert final['status'] == 'rejected' and final['spent'] == final['approved_count'] == 0


def test_stopped_gate_token_cannot_authorize_a_new_batch(fx):
    _, receipt, event = _transports_start_batch_487(fx, ['b'])
    old = read_gate(fx, receipt)
    assert rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})[0] == 200
    code, result = dispatch(fx, 'ws', {'confirm_id': event['confirm_id'], 'approved': True},
                            token=old['decision_token'])
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert read_gate(fx, receipt)['decision_token'] is None and fx.calls.empty()
    preview, next_receipt, next_event = _transports_start_batch_487(fx, ['b'])
    current = read_gate(fx, next_receipt)
    assert current['decision_token'] != old['decision_token']
    body = {'confirm_id': next_event['confirm_id'], 'approved': True,
            'approved_item_ids': [preview['items'][0]['item_id']], 'snapshot_sha256': preview['snapshot_sha256']}
    code, result = dispatch(fx, 'ws', body, token=old['decision_token'])
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert read_gate(fx, next_receipt) == current and fx.calls.empty()
    code, result = dispatch(fx, 'ws', body, token=current['decision_token'])
    assert code == 200, result
    final = status_until(fx, next_receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == 1 and fx.calls.qsize() == 1


def test_foreign_ws_handshake_is_rejected_before_batch_decision(fx):
    from starlette.testclient import WebSocketDenialResponse

    _, receipt, _ = _transports_start_batch_487(fx, ['b'])
    current = read_gate(fx, receipt)
    with pytest.raises(WebSocketDenialResponse) as failure:
        with fx.client.websocket_connect(f'ws://localhost/api/projects/{fx.pid}/agent-nodes/actor/stream',
                                         headers={'Origin': 'http://foreign.invalid'}):
            pytest.fail('A foreign browser must not connect')
    assert failure.value.status_code == 403
    assert read_gate(fx, receipt) == current and fx.calls.empty()
    assert rest(fx, 'stop_batch', {'batch_id': receipt['batch_id']})[0] == 200


def test_ws_token_cannot_follow_changed_actor_binding(fx):
    preview, receipt, event = _transports_start_batch_487(fx, ['b'])
    current = read_gate(fx, receipt)
    actor = fx.store.find_canvas_node(fx.pid, 'actor')
    fx.store.apply_agent_host_mutations(fx.pid, [{'op': 'upsert_node',
                                                'node': {**actor, 'mcp_session': 'another-real-binding'}}],
                                        base_revision=fx.store.canvas(fx.pid)['revision'])
    assert fx.store.find_canvas_node(fx.pid, 'actor')['mcp_session'] == 'another-real-binding'
    response = fx.client.get(preview_url(fx, receipt), headers={'X-Agent-Project': fx.pid})
    assert response.status_code == 409, response.text
    code, result = dispatch(fx, 'ws', {'confirm_id': event['confirm_id'], 'approved': False,
                                      'snapshot_sha256': preview['snapshot_sha256']},
                            token=current['decision_token'])
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert fx.calls.empty()


@pytest.mark.parametrize('transport, malformed', [
    (transport, malformed) for transport in ('http', 'callback') for malformed in (
        'unknown', 'hash-format', 'reject-subset', 'missing-hash', 'approved-type',
        'confirm-id-type', 'ids-type', 'id-type')
])
def test_confirmation_payload_is_strict_at_every_boundary(fx, transport, malformed):
    code, preview = rest(fx, 'preflight', {'node_ids': ['b']})
    assert code == 200, preview
    code, receipt = rest(fx, 'request_batch', {'preflight_id': preview['preflight_id']})
    assert code == 200, receipt
    event = fx.events.get(timeout=3)
    valid = {'confirm_id': event['confirm_id'], 'approved': True,
             'approved_item_ids': [preview['items'][0]['item_id']],
             'snapshot_sha256': preview['snapshot_sha256']}
    body = {**valid, **{'unknown': {'unexpected': True},
                        'hash-format': {'snapshot_sha256': 'not-a-sha256'},
                        'reject-subset': {'approved': False}, 'missing-hash': {},
                        'approved-type': {'approved': 'false'},
                        'confirm-id-type': {'confirm_id': 42},
                        'ids-type': {'approved_item_ids': 'all'},
                        'id-type': {'approved_item_ids': [42]}}[malformed]}
    if malformed == 'missing-hash':
        body.pop('snapshot_sha256')
    before = fx.store.canvas(fx.pid)
    if transport == 'callback':
        handler = next(handler for handler in fx.session.confirm_handlers
                       if getattr(handler, 'batch_confirm_id', None) == event['confirm_id'])
        try:
            fx.client.portal.call(handler, event['confirm_id'], body)
        except HTTPException as error:
            code, detail = error.status_code, error.detail
        else:
            code, detail = 200, None
    else:
        response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                                  headers={'X-Agent-Project': fx.pid}, json=body)
        code, detail = response.status_code, response.json().get('detail')
    assert code == 400, detail
    assert detail['code'] == 'INVALID_ARGUMENT'
    assert fx.store.canvas(fx.pid) == before and fx.calls.empty()
    code, pending = rest(fx, 'batch_status', {'batch_id': receipt['batch_id']})
    assert code == 200 and pending['status'] == 'awaiting_confirmation'
    assert pending['spent'] == pending['approved_count'] == 0
    response = fx.client.post(f'/api/projects/{fx.pid}/agent-nodes/actor/confirm',
                              headers={'X-Agent-Project': fx.pid}, json=valid)
    assert response.status_code == 200, response.text
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] in ('done', 'rejected'))
    assert final['status'] == 'done' and final['spent'] == final['approved_count'] == 1
    assert fx.calls.qsize() == 1


@pytest.mark.parametrize('malformed', ['unknown', 'ids-type', 'id-type', 'approved-type',
    'hash-format', 'reject-subset', 'missing-hash', 'confirm-id-type'])
def test_ws_rich_payload_is_strict_with_current_token(fx, malformed):
    preview, receipt, event = _transports_start_batch_487(fx, ['b'])
    current = read_gate(fx, receipt)
    valid = {'confirm_id': event['confirm_id'], 'approved': True,
             'approved_item_ids': [preview['items'][0]['item_id']],
             'snapshot_sha256': current['preflight']['snapshot_sha256']}
    body = {**valid, **{'unknown': {'extra': True}, 'ids-type': {'approved_item_ids': 'all'},
        'id-type': {'approved_item_ids': [42]}, 'approved-type': {'approved': 'false'},
        'hash-format': {'snapshot_sha256': 'invalid'}, 'reject-subset': {'approved': False},
        'missing-hash': {}, 'confirm-id-type': {'confirm_id': 42}}[malformed]}
    if malformed == 'missing-hash':
        body.pop('snapshot_sha256')
    before = fx.store.canvas(fx.pid)
    with fx.client.websocket_connect(f'ws://localhost/api/projects/{fx.pid}/agent-nodes/actor/stream') as ws:
        assert ws.receive_json()['type'] == 'session'
        ws.send_json({'type': 'confirm', **body, 'decision_token': current['decision_token']})
        while True:
            result = ws.receive_json()
            if result['type'] in ('error', 'confirm_result'):
                break
        assert result.get('status') == 400 and result['code'] == 'INVALID_ARGUMENT', result
        assert read_gate(fx, receipt) == current and fx.calls.empty() and fx.store.canvas(fx.pid) == before
        ws.send_json({'type': 'confirm', **valid, 'decision_token': current['decision_token']})
        while True:
            result = ws.receive_json()
            if result['type'] in ('error', 'confirm_result'):
                break
        assert result['type'] == 'confirm_result', result
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == 1 and fx.calls.qsize() == 1


def preview_mutation_body(current, action):
    prepared = current['preflight']
    return {'preview_revision': prepared['preview_revision'], 'snapshot_sha256': prepared['snapshot_sha256'],
            'item_id': prepared['items'][0]['item_id'],
            **({'patch': {'prompt': 'Human revised body'}} if action == 'edit' else {})}


def revise_gate(fx, receipt, action):
    old = read_gate(fx, receipt)
    response = fx.client.post(preview_url(fx, receipt) + '/' + action, headers={'X-Agent-Project': fx.pid},
                              json=preview_mutation_body(old, action))
    assert response.status_code == 200, response.text
    current = response.json()
    assert current['confirm_id'] != old['confirm_id'] and current['decision_token'] != old['decision_token']
    while True:
        event = fx.events.get(timeout=3)
        if event['type'] == 'confirm':
            assert event['confirm_id'] == current['confirm_id']
            break
    return old, current


@pytest.mark.parametrize('action', ['edit', 'remove'])
@pytest.mark.parametrize('guard', ['query', 'missing-origin', 'foreign-origin', 'missing-project',
    'wrong-project', 'duplicate-project', 'actor', 'project', 'batch', 'body-source'])
def test_human_edit_remove_guards_after_legal_revision(fx, action, guard):
    _, receipt, _ = _transports_start_batch_487(fx)
    _, current = revise_gate(fx, receipt, action)
    before = fx.store.canvas(fx.pid)
    url = preview_url(fx, receipt) + '/' + action
    body = preview_mutation_body(current, action)
    headers = [('X-Agent-Project', fx.pid)]
    expected = 403
    if guard == 'query':
        url += '?source=human&source=mcp'
        expected = 400
    elif guard == 'missing-project':
        headers = []
    elif guard == 'wrong-project':
        headers = [('X-Agent-Project', fx.other)]
    elif guard == 'duplicate-project':
        headers *= 2
    elif guard == 'actor':
        url = url.replace('/agent-nodes/actor/', '/agent-nodes/second-actor/')
        expected = 409
    elif guard == 'project':
        url = url.replace(f'/projects/{fx.pid}/', f'/projects/{fx.other}/')
        headers = [('X-Agent-Project', fx.other)]
        expected = 409
    elif guard == 'batch':
        url = url.replace(receipt['batch_id'], 'not-this-batch')
        expected = 409
    elif guard == 'body-source':
        body.update(source='human', caller='mcp', SID='forged', turn_id='new-turn', host='pi', generation=9)
        expected = 400
    request = fx.client.build_request('POST', url, headers=headers, json=body)
    if guard == 'missing-origin':
        del request.headers['Origin']
    elif guard == 'foreign-origin':
        request.headers['Origin'] = 'http://foreign.invalid'
    response = fx.client.send(request)
    assert response.status_code == expected, response.text
    assert read_gate(fx, receipt) == current and fx.events.empty() and fx.calls.empty()
    assert fx.store.canvas(fx.pid) == before
    code, result = dispatch(fx, 'http', {'confirm_id': current['confirm_id'], 'approved': True})
    assert code == 200, result
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == len(current['preflight']['items'])


@pytest.mark.parametrize('action', ['edit', 'remove'])
@pytest.mark.parametrize('stale', ['token', 'confirm-id', 'hash'])
def test_ws_revision_requires_new_gate_token_and_snapshot(fx, action, stale):
    _, receipt, _ = _transports_start_batch_487(fx)
    old, current = revise_gate(fx, receipt, action)
    body = {'confirm_id': current['confirm_id'], 'approved': True,
            'approved_item_ids': [current['preflight']['items'][0]['item_id']],
            'snapshot_sha256': current['preflight']['snapshot_sha256']}
    token = current['decision_token']
    if stale == 'token':
        token = old['decision_token']
    elif stale == 'confirm-id':
        body['confirm_id'] = old['confirm_id']
    else:
        body['snapshot_sha256'] = old['preflight']['snapshot_sha256']
    code, result = dispatch(fx, 'ws', body, token=token)
    assert code == 409 and result['code'] == 'CONFIRM_STALE', result
    assert read_gate(fx, receipt) == current and fx.calls.empty()
    body['confirm_id'] = current['confirm_id']
    body['snapshot_sha256'] = current['preflight']['snapshot_sha256']
    code, result = dispatch(fx, 'ws', body, token=current['decision_token'])
    assert code == 200, result
    final = status_until(fx, receipt['batch_id'], lambda row: row['status'] == 'done')
    assert final['spent'] == final['approved_count'] == 1 and fx.calls.qsize() == 1
    assert dispatch(fx, 'ws', body, token=current['decision_token'])[0] == 409
