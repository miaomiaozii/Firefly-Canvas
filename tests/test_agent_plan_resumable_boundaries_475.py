"""Public direct/duplicate/human plan decisions; native pipes alone are offline (#475)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from queue import Queue
from threading import Event

from fastapi.testclient import TestClient
import httpx
import pytest

# Shared route fixtures must bind the real application before router imports.
import main
from app.routers import api_agent_tools as tools
from tests.test_agent_plan_apply_484 import drain, headers, track_lifetime
from tests.test_agent_plan_present_binding_482 import binding_http, ORIGINAL_SID
from tests.test_agent_plan_resumable_hosted_475 import (
    live_http, hosted_imported_475 as imported, hosted_confirm_475 as confirm, read, operations, inputs, end_response, wait_record,
)


CHANNELS = [pytest.param('rest', 'actor', id='REST-Pi'),
            pytest.param('hosted', 'actor', id='hosted-Pi'),
            pytest.param('hosted', 'codex-actor', id='hosted-Codex'),
            pytest.param('external', 'actor', id='external-original-SID')]
REJECTED = {'code': 'PLAN_NOT_CONFIRMED', 'message': '方案未获批准；没有修改画布或媒体。',
            'hint': '请先与用户核对任务与参考图，再请求批准。', 'retryable': True,
            'details': {'reason': 'rejected'}}


def boundary_canvas_475(http):
    response = http.client.get(f'/api/project-library/{http.project}')
    assert response.status_code == 200, response.text
    return response.json()['canvas']


def native_inputs(http, actor):
    return (deepcopy(inputs(http, actor)), list(http.processes),
            [process.stdin.getvalue() for process in http.processes])


def gates(http, session):
    observed = Queue()

    async def observe(_session, event):
        if event['type'] == 'confirm' and event['tool'] == 'plan.apply':
            observed.put(deepcopy(event))

    session.event_handlers.append(observe)
    return observed


def raw_apply(http, draft, transport, actor):
    args = {'plan_id': draft['plan_id'], 'generation': draft['generation']}
    if transport == 'rest':
        return http.client.post(f'/api/projects/{http.project}/plan/apply',
            params={'agent_node_id': actor}, headers=headers(http.project), json=args)
    return http.client.post('/api/mcp',
        params={'project': http.project, 'node': actor, 'caller': transport},
        headers={'Mcp-Session-Id': ORIGINAL_SID}, json={
            'jsonrpc': '2.0', 'id': 475, 'method': 'tools/call',
            'params': {'name': 'plan.apply', 'arguments': args}})


def boundary_terminal_475(response, transport, *, result=None, error=None):
    assert (result is None) != (error is None)
    if transport == 'rest':
        assert response.status_code == (409 if error else 200), response.text
        assert response.json() == ({'detail': error} if error else result)
        return
    assert response.status_code == 200, response.text
    assert response.headers['Mcp-Session-Id'] == ORIGINAL_SID
    if error:
        text = f"{error['code']}｜{error['message']}｜下一步：{error['hint']}"
        if error.get('details'):
            text += f"｜details={error['details']}"
        expected = {'isError': True, 'structuredContent': error,
                    'content': [{'type': 'text', 'text': text}]}
    else:
        expected = {'isError': False, 'content': [
            {'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}]}
    assert response.json() == {'jsonrpc': '2.0', 'id': 475, 'result': expected}


def successful_application(http, draft, actor, before, *, applied_by, turn):
    application = read(http, draft, actor)
    assert application['state'] == 'applied' and application['generation'] == draft['generation']
    assert set(application) == {'state', 'generation', 'receipt'}
    receipt = application['receipt']
    assert receipt['generation'] == draft['generation'] and receipt['applied_by'] == applied_by
    assert set(receipt) == {'generation', 'applied_by', 'result'}
    result = receipt['result']
    assert [task['task_id'] for task in result['tasks']] == [draft['preview']['tasks'][0]['task_id']]
    assert result['already_applied'] == [] and len(result['groups']) == 1
    after = boundary_canvas_475(http)
    assert after['revision'] == before['revision'] + 1
    created = [node for node in after['nodes'] if node['id'] not in {n['id'] for n in before['nodes']}]
    assert len(created) == 3 and {node['id'] for node in created} == set(result['touched_node_ids'])
    assert all(node['createdBy'] == 'agent' and node['agentNodeId'] == actor for node in created)
    task, = result['tasks']
    nodes = {node['id']: node for node in created}
    assert nodes[task['prompt_node']]['type'] == 'smart-prompt'
    assert nodes[task['prompt_node']]['text'].splitlines() == ['白底拍摄', '场景共4张图']
    slot = nodes[task['gen_node']]
    assert slot['type'] == 'smart-image' and slot['runSettings']['prompt'] == ''
    assert slot['runSettings']['count'] == 1 and slot['running'] is False
    assert task['ref_nodes'] == []
    assert len(after['connections']) == len(before['connections']) + 1
    assert next(edge for edge in after['connections'] if
                (edge['from'], edge['to']) == (task['prompt_node'], task['gen_node']))['kind'] == 'input'
    media = http.client.get(f'/api/project-library/{http.project}/media')
    assert media.status_code == 200 and media.json() == {'items': []}, media.text
    ops = operations(http)
    assert len(ops) == 1
    assert (ops[0]['op_id'], ops[0]['tool'], ops[0]['agent_node_id'], ops[0]['turn_id']) == (
        result['op_id'], 'plan.apply', actor, turn)
    assert set(ops[0]['touched_node_ids']) == set(result['touched_node_ids'])
    return deepcopy(application)


def direct_decision(http, draft, transport, actor, observed, approved):
    # A genuine outstanding request and a published gate, not timer manipulation, order the decision.
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(raw_apply, http, draft, transport, actor)
        event = observed.get(timeout=10)
        try:
            assert not future.done(), 'The bounded synchronous request ended before the public decision'
            assert read(http, draft, actor) == {'state': 'awaiting_confirmation',
                'generation': draft['generation'], 'receipt': None, 'confirm_id': event['confirm_id']}
            assert operations(http) == []
            confirm(http, actor, event, approved)
            return future.result(timeout=30), event
        finally:
            if not future.done():
                confirm(http, actor, event, False)


@pytest.fixture
def shutdown_http(tmp_path, monkeypatch):
    home = tmp_path / 'owned-shutdown-codex-home'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    fixture = binding_http.__wrapped__(tmp_path, monkeypatch)
    http = next(fixture)
    closed = False

    def close():
        nonlocal closed
        if not closed:
            closed = True
            with pytest.raises(StopIteration):
                next(fixture)

    try:
        yield http, close
    finally:
        close()


def asgi_apply(http, draft, transport, actor, finished, cancelled):
    async def request():
        # Cancellation owns the public ASGI request, never a private execution/confirmation task.
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                         base_url='http://127.0.0.1:3198') as client:
                args = {'plan_id': draft['plan_id'], 'generation': draft['generation']}
                if transport == 'rest':
                    return await client.post(f'/api/projects/{http.project}/plan/apply',
                        params={'agent_node_id': actor}, headers=headers(http.project), json=args)
                return await client.post('/api/mcp',
                    params={'project': http.project, 'node': actor, 'caller': transport},
                    headers={'Mcp-Session-Id': ORIGINAL_SID}, json={
                        'jsonrpc': '2.0', 'id': 475, 'method': 'tools/call',
                        'params': {'name': 'plan.apply', 'arguments': args}})
        except asyncio.CancelledError:
            cancelled.set()
            raise
        finally:
            finished.set()
    return http.client.portal.start_task_soon(request)


@pytest.mark.parametrize(('transport', 'actor'), CHANNELS)
@pytest.mark.parametrize('autonomy', ['cautious', 'standard', 'autonomous'])
@pytest.mark.parametrize('approved', [True, False], ids=['within-approve', 'within-reject'])
def test_R05_within_window_terminal_envelope_no_continuation_and_released_rejection(
        live_http, monkeypatch, transport, actor, autonomy, approved):
    http = live_http
    _, draft, session = imported(http, transport, actor)
    response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/autonomy',
        headers=headers(http.project), json={'autonomy': autonomy})
    assert response.status_code == 200, response.text
    tasks = track_lifetime(monkeypatch)
    assert tools.AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS == 90.0
    observed = gates(http, session)
    before, old_native = boundary_canvas_475(http), native_inputs(http, actor)
    turn = next(row['event']['turn_id'] for row in reversed(old_native[0]) if row['event']['type'] == 'user_prompt')
    response, event = direct_decision(http, draft, transport, actor, observed, approved)
    drain(http.client, tasks)
    try:
        if approved:
            saved = successful_application(http, draft, actor, before, applied_by='agent',
                turn=None if transport == 'external' else turn)
            boundary_terminal_475(response, transport, result=saved['receipt']['result'])
        else:
            boundary_terminal_475(response, transport, error=REJECTED)
            assert read(http, draft, actor) == {'state': 'ready', 'generation': draft['generation'],
                'receipt': None, 'error': REJECTED}
            assert boundary_canvas_475(http) == before and operations(http) == []
        assert native_inputs(http, actor) == old_native
        assert observed.empty() and not session.confirm_handlers
        if not approved:
            # A new public request succeeds in the same generation: the rejected reservation is released.
            sibling, new_gate = direct_decision(http, draft, transport, actor, observed, True)
            assert new_gate['confirm_id'] != event['confirm_id']
            drain(http.client, tasks)
            saved = successful_application(http, draft, actor, before, applied_by='agent',
                turn=None if transport == 'external' else turn)
            boundary_terminal_475(sibling, transport, result=saved['receipt']['result'])
        end_response(http, session, actor)
        assert native_inputs(http, actor) == old_native
        assert observed.empty() and not session.confirm_handlers
        assert read(http, draft, actor) == saved
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize(('transport', 'actor'), CHANNELS)
def test_R07_pending_competitor_and_repeated_decisions_preserve_one_real_late_application(
        live_http, monkeypatch, transport, actor):
    http = live_http
    _, draft, session = imported(http, transport, actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    observed = gates(http, session)
    before, old_native = boundary_canvas_475(http), native_inputs(http, actor)
    turn = next(row['event']['turn_id'] for row in reversed(old_native[0]) if row['event']['type'] == 'user_prompt')
    response = raw_apply(http, draft, transport, actor)
    event = observed.get(timeout=10)
    try:
        boundary_terminal_475(response, transport, result={'approved': False, 'reason': 'pending',
            'code': 'CONFIRM_PENDING', 'status': 'awaiting_confirmation', 'confirm_id': event['confirm_id']})
        assert read(http, draft, actor) == {'state': 'awaiting_confirmation',
            'generation': draft['generation'], 'receipt': None, 'confirm_id': event['confirm_id']}
        assert boundary_canvas_475(http) == before and operations(http) == []
        competitor = raw_apply(http, draft, transport, actor)
        boundary_terminal_475(competitor, transport, error={'code': 'CONFLICT',
            'message': '方案正在等待确认或提交结果尚不确定。',
            'hint': '请查询方案状态和操作日志，不要重复应用。', 'retryable': False,
            'details': {'plan_id': draft['plan_id'], 'generation': draft['generation'],
                        'state': 'awaiting_confirmation', 'receipt': None, 'confirm_id': event['confirm_id']}})
        assert observed.empty() and len(session.confirm_handlers) == 1
        assert boundary_canvas_475(http) == before and operations(http) == []
        confirm(http, actor, event)
        confirm(http, actor, event)
        confirm(http, actor, event, False)
        drain(http.client, tasks)
        saved = successful_application(http, draft, actor, before, applied_by='agent',
            turn=None if transport == 'external' else turn)
        saved_canvas, saved_ops = boundary_canvas_475(http), operations(http)
        for approved in (True, True, False):
            confirm(http, actor, event, approved)
            drain(http.client, tasks)
            assert read(http, draft, actor) == saved
            assert boundary_canvas_475(http) == saved_canvas and operations(http) == saved_ops
        duplicate = raw_apply(http, draft, transport, actor)
        boundary_terminal_475(duplicate, transport, error={'code': 'PLAN_ALREADY_APPLIED',
            'message': '此方案代次已经应用，不能重复执行。',
            'hint': '请先编辑方案产生新代次，再重新确认。', 'retryable': False,
            'details': {'plan_id': draft['plan_id'], 'generation': draft['generation'],
                'state': 'applied', 'receipt': saved['receipt'], 'applied_by': 'agent',
                'op_id': saved['receipt']['result']['op_id']}})
        records = [row['event'] for row in http.records(http.project, actor)]
        assert len([e for e in records if e['type'] == 'confirm' and e['tool'] == 'plan.apply']) == 1
        resolved = [e for e in records if e['type'] == 'confirm_resolved' and e['confirm_id'] == event['confirm_id']]
        assert len(resolved) == 1 and resolved[0]['approved'] is True
        assert observed.empty() and not session.confirm_handlers
        if transport == 'external':
            assert native_inputs(http, actor) == old_native
        else:
            queued = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'queued')
            assert json.loads(queued['text'].split('：', 1)[1]) == saved['receipt']['result']
            assert queued['turn_id'] == turn
            end_response(http, session, actor)
            sent = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'sent')
            assert (sent['text'], sent['request_id'], sent['turn_id']) == (queued['text'], queued['request_id'], turn)
            continuations = [row['event'] for row in inputs(http, actor) if row['event']['type'] == 'system_continuation']
            assert [e['delivery'] for e in continuations] == ['queued', 'sent']
            if actor == 'codex-actor':
                assert len(http.processes) == len(old_native[1]) + 1
                assert http.processes[-1].stdin.getvalue() == sent['text']
            else:
                commands = [json.loads(line) for line in session.process.stdin.getvalue().splitlines()]
                assert [c['message'] for c in commands if c['type'] == 'follow_up'] == [sent['text']]
        assert read(http, draft, actor) == saved and operations(http) == saved_ops
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)


def boundary_human_apply_475(http, draft, actor):
    return http.client.post(f'/api/projects/{http.project}/plan/previews/{draft["plan_id"]}/apply',
        params={'agent_node_id': actor}, headers=headers(http.project), json={'generation': draft['generation']})


@pytest.mark.parametrize(('transport', 'actor'), CHANNELS)
@pytest.mark.parametrize('approved', [True, False], ids=['late-approve', 'late-reject'])
def test_R32_human_apply_from_every_import_source_never_inputs_the_active_host(
        live_http, monkeypatch, transport, actor, approved):
    http = live_http
    source, draft, session = imported(http, transport, actor)
    assert draft['apply_via'] == 'human_apply'
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    observed = gates(http, session)
    before, old_native = boundary_canvas_475(http), native_inputs(http, actor)
    assert session.is_running and any(row['event']['type'] == 'user_prompt' for row in old_native[0])
    response = boundary_human_apply_475(http, draft, actor)
    event = observed.get(timeout=10)
    try:
        assert response.status_code == 200, response.text
        assert response.json() == {'approved': False, 'reason': 'pending', 'code': 'CONFIRM_PENDING',
            'status': 'awaiting_confirmation', 'confirm_id': event['confirm_id']}
        assert event['turn_id'] is None
        assert read(http, draft, actor) == {'state': 'awaiting_confirmation',
            'generation': draft['generation'], 'receipt': None, 'confirm_id': event['confirm_id']}
        assert boundary_canvas_475(http) == before and operations(http) == []
        confirm(http, actor, event, approved)
        drain(http.client, tasks)
        if not approved:
            assert read(http, draft, actor) == {'state': 'ready', 'generation': draft['generation'],
                'receipt': None, 'error': REJECTED}
            assert boundary_canvas_475(http) == before and operations(http) == []
            assert native_inputs(http, actor) == old_native
            assert not session.confirm_handlers
            # The rejected human operation has no lingering reservation; the same generation remains usable.
            sibling = boundary_human_apply_475(http, draft, actor)
            new_gate = observed.get(timeout=10)
            assert sibling.status_code == 200 and sibling.json()['code'] == 'CONFIRM_PENDING', sibling.text
            assert sibling.json()['confirm_id'] == new_gate['confirm_id'] != event['confirm_id']
            assert new_gate['turn_id'] is None
            confirm(http, actor, new_gate)
            drain(http.client, tasks)
        saved = successful_application(http, draft, actor, before, applied_by='human', turn=None)
        public = http.human_read(draft, actor=actor)
        assert public['apply_via'] == 'human_apply' and public['application'] == saved
        ok, replay = http.call('plan.import_excel', {'attachment_id': source['id']}, transport=transport, actor=actor)
        assert ok and replay == public
        assert native_inputs(http, actor) == old_native
        assert observed.empty() and not session.confirm_handlers
        undo = http.client.post(f'/api/projects/{http.project}/ops/undo', headers=headers(http.project),
            json={'op_id': saved['receipt']['result']['op_id']})
        assert undo.status_code == 200 and undo.json()['reverted'] == [saved['receipt']['result']['op_id']], undo.text
        assert boundary_canvas_475(http)['nodes'] == before['nodes'] and boundary_canvas_475(http)['connections'] == before['connections']
        assert read(http, draft, actor) == saved
        duplicate = boundary_human_apply_475(http, draft, actor)
        assert duplicate.status_code == 409 and duplicate.json()['detail']['code'] == 'PLAN_ALREADY_APPLIED', duplicate.text
        assert duplicate.json()['detail']['details']['receipt'] == saved['receipt']
        end_response(http, session, actor)
        assert native_inputs(http, actor) == old_native
        assert observed.empty() and not session.confirm_handlers
    finally:
        confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize(('transport', 'actor'), CHANNELS)
def test_R12_actual_asgi_request_cancellation_settles_gate_and_allows_fresh_public_apply(
        live_http, monkeypatch, transport, actor):
    http = live_http
    _, draft, session = imported(http, transport, actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', 20)
    observed = gates(http, session)
    before, old_native = boundary_canvas_475(http), native_inputs(http, actor)
    turn = next(row['event']['turn_id'] for row in reversed(old_native[0]) if row['event']['type'] == 'user_prompt')
    finished, cancelled = Event(), Event()
    request = asgi_apply(http, draft, transport, actor, finished, cancelled)
    event = observed.get(timeout=10)
    try:
        assert not request.done() and not finished.is_set()
        assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
        assert boundary_canvas_475(http) == before and operations(http) == []
        assert request.cancel()
        assert finished.wait(timeout=10) and cancelled.is_set()
        drain(http.client, tasks)
        application = read(http, draft, actor)
        assert application['state'] == 'ready' and application['receipt'] is None
        assert 'confirm_id' not in application
        assert not session.confirm_handlers
        resolved = [row['event'] for row in http.records(http.project, actor)
                    if row['event']['type'] == 'confirm_resolved' and row['event']['confirm_id'] == event['confirm_id']]
        assert len(resolved) == 1 and resolved[0]['approved'] is False and resolved[0]['reason'] == 'stopped'
        confirm(http, actor, event)
        drain(http.client, tasks)
        assert read(http, draft, actor) == application
        assert boundary_canvas_475(http) == before and operations(http) == []
        assert native_inputs(http, actor) == old_native
        response, fresh_gate = direct_decision(http, draft, transport, actor, observed, True)
        assert fresh_gate['confirm_id'] != event['confirm_id']
        drain(http.client, tasks)
        saved = successful_application(http, draft, actor, before, applied_by='agent',
            turn=None if transport == 'external' else turn)
        boundary_terminal_475(response, transport, result=saved['receipt']['result'])
        end_response(http, session, actor)
        assert native_inputs(http, actor) == old_native
        assert observed.empty() and not session.confirm_handlers
    finally:
        if not request.done():
            request.cancel()
        confirm(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize(('transport', 'actor'), CHANNELS)
@pytest.mark.parametrize('approved', [False, True], ids=['shutdown-pending', 'late-apply-before-shutdown-control'])
def test_R12_actual_testclient_lifespan_loop_shutdown_does_not_revive_old_gate(
        shutdown_http, monkeypatch, transport, actor, approved):
    http, close = shutdown_http
    _, draft, session = imported(http, transport, actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    observed = gates(http, session)
    before, old_native = boundary_canvas_475(http), native_inputs(http, actor)
    turn = next(row['event']['turn_id'] for row in reversed(old_native[0]) if row['event']['type'] == 'user_prompt')
    pending = raw_apply(http, draft, transport, actor)
    event = observed.get(timeout=10)
    boundary_terminal_475(pending, transport, result={'approved': False, 'reason': 'pending', 'code': 'CONFIRM_PENDING',
        'status': 'awaiting_confirmation', 'confirm_id': event['confirm_id']})
    assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
    assert boundary_canvas_475(http) == before and operations(http) == []
    saved = None
    if approved:
        confirm(http, actor, event)
        drain(http.client, tasks)
        saved = successful_application(http, draft, actor, before, applied_by='agent',
            turn=None if transport == 'external' else turn)
        if transport != 'external':
            end_response(http, session, actor)
            sent = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'sent')
            assert sent['turn_id'] == turn
            assert json.loads(sent['text'].split('：', 1)[1]) == saved['receipt']['result']
            if actor == 'codex-actor':
                assert len(http.processes) == len(old_native[1]) + 1
                assert http.processes[-1].stdin.getvalue() == sent['text']
            else:
                commands = [json.loads(line) for line in session.process.stdin.getvalue().splitlines()]
                assert [c['message'] for c in commands if c['type'] == 'follow_up'] == [sent['text']]
    before_close_native = native_inputs(http, actor)
    # This exits the real TestClient application lifespan and its execution loop, not an internal task.
    close()
    assert http.client.portal is None
    assert tasks and all(task.done() for task in tasks)
    assert not session.confirm_handlers
    assert all(task.get_loop().is_closed() for task in tasks)
    with TestClient(main.app, base_url='http://127.0.0.1:3198') as client:
        http.client = client
        application = read(http, draft, actor)
        if approved:
            assert application == saved
            assert len(operations(http)) == 1
            assert operations(http)[0]['op_id'] == saved['receipt']['result']['op_id']
        else:
            assert application['state'] == 'ready' and application['receipt'] is None
            assert 'confirm_id' not in application
            assert boundary_canvas_475(http) == before and operations(http) == []
            resolved = [row['event'] for row in http.records(http.project, actor)
                        if row['event']['type'] == 'confirm_resolved' and row['event']['confirm_id'] == event['confirm_id']]
            assert len(resolved) == 1 and resolved[0]['approved'] is False and resolved[0]['reason'] == 'stopped'
        settled_canvas, settled_ops, settled_native = boundary_canvas_475(http), operations(http), native_inputs(http, actor)
        for decision in (True, True, False):
            confirm(http, actor, event, decision)
            assert read(http, draft, actor) == application
            assert boundary_canvas_475(http) == settled_canvas and operations(http) == settled_ops
            assert native_inputs(http, actor) == settled_native
        assert native_inputs(http, actor) == before_close_native
        if not approved or transport == 'external':
            assert native_inputs(http, actor) == old_native
