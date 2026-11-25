"""Real admission/readers and offline native pipes; not native/model behavior (#475)."""
from copy import deepcopy
import json
import time
from uuid import uuid4

import pytest

# Import the application before routers bind their main module.
import main
from app.routers import api_agent_tools as tools
from tests.test_agent_plan_399 import upload, workbook
from tests.test_agent_plan_apply_484 import drain, headers, track_lifetime
from tests.test_agent_plan_present_binding_482 import binding_http


@pytest.fixture
def live_http(binding_http, tmp_path, monkeypatch):
    home = tmp_path / 'owned-codex-home'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    return binding_http


def hosted_imported_475(http, transport, actor):
    source = upload(http.client, http.project, 'pending-hosted.xlsx', workbook(''), actor=actor)
    session = http.prime(actor=actor, source=source)
    if actor == 'codex-actor':
        session.process.emit({'type': 'thread.started', 'thread_id': str(uuid4())})
        http.wait_event(actor, 'input_branch_bound')
    ok, draft = http.call('plan.import_excel', {'attachment_id': source['id']},
                          transport=transport, actor=actor)
    assert ok, draft
    return source, http.human_merge(draft, actor=actor), session


def apply(http, draft, transport, actor):
    return http.call('plan.apply', {'plan_id': draft['plan_id'], 'generation': draft['generation']},
                     transport=transport, actor=actor)


def hosted_confirm_475(http, actor, event, approved=True):
    response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/confirm',
        headers=headers(http.project), json={'confirm_id': event['confirm_id'], 'approved': approved})
    assert response.status_code == 200, response.text


def read(http, draft, actor):
    return http.human_read(draft, actor=actor)['application']


def operations(http):
    response = http.client.get(f'/api/projects/{http.project}/ops', headers=headers(http.project))
    assert response.status_code == 200, response.text
    return response.json()['operations']


def inputs(http, actor):
    return [r for r in http.records(http.project, actor) if r['event']['type'] in
            ('user_prompt', 'queued_input', 'system_continuation')]


def wait_record(http, actor, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        found = [record for record in http.records(http.project, actor) if predicate(record['event'])]
        if found:
            return found[-1]['event']
        time.sleep(.01)
    pytest.fail('Expected public delivery event was not persisted')


def hosted_effects_475(http):
    result = {}
    for key, path in (
        ('canvas', f'/api/project-library/{http.project}'),
        ('media', f'/api/project-library/{http.project}/media'),
        ('tasks', '/api/global-tasks'),
    ):
        response = http.client.get(path, headers=headers(http.project))
        assert response.status_code == 200, response.text
        data = response.json()
        result[key] = data['canvas'] if key == 'canvas' else data['tasks'] if key == 'tasks' else data
    # The counter is an observation, never a replacement for generation or admission.
    result['quota'] = deepcopy(tools._GENERATION_COUNTS)
    return result


def hosted_committed_475(http, draft, actor, before, receipt):
    after = hosted_effects_475(http)
    assert all(after[key] == before[key] for key in ('media', 'tasks', 'quota'))
    canvas, old_canvas = after['canvas'], before['canvas']
    assert canvas['revision'] == old_canvas['revision'] + 1
    created = {node['id']: node for node in canvas['nodes']
               if node['id'] not in {old['id'] for old in old_canvas['nodes']}}
    assert len(created) == 3 and set(created) == set(receipt['touched_node_ids'])
    assert all(node['createdBy'] == 'agent' and node['agentNodeId'] == actor for node in created.values())
    task, = receipt['tasks']
    prompt, slot = created[task['prompt_node']], created[task['gen_node']]
    assert prompt['type'] == 'smart-prompt' and prompt['text'].splitlines() == ['白底拍摄', '场景共4张图']
    assert prompt['excel_task_id'] == slot['excel_task_id'] == task['task_id']
    assert slot['type'] == 'smart-image' and slot['runSettings']['prompt'] == ''
    assert slot['runSettings']['count'] == 1 and slot['running'] is False and not slot.get('images')
    assert task['ref_nodes'] == [] and receipt['already_applied'] == []
    assert len(receipt['groups']) == 1 and created[receipt['groups'][0]['group_id']]['type'] == 'smart-group'
    assert len(canvas['connections']) == len(old_canvas['connections']) + 1
    assert any(edge['from'] == prompt['id'] and edge['to'] == slot['id'] and edge['kind'] == 'input'
               for edge in canvas['connections'])


@pytest.mark.parametrize(('transport', 'actor'), [
    pytest.param('rest', 'actor', id='REST-Pi'),
    pytest.param('external', 'actor', id='external-Pi'),
    pytest.param('external', 'codex-actor', id='external-Codex'),
    pytest.param('hosted', 'actor', id='hosted-Pi'),
    pytest.param('hosted', 'codex-actor', id='hosted-Codex'),
])
@pytest.mark.parametrize('autonomy', ['cautious', 'standard', 'autonomous'])
@pytest.mark.parametrize('approved', [True, False], ids=['late-approve', 'late-reject'])
@pytest.mark.parametrize('decision', ['http', 'ws'])
@pytest.mark.parametrize('window', [0, .01], ids=['zero', 'positive'])
def test_R03_R04_R06_R31_R36_real_late_decision_and_host_delivery(
    live_http, monkeypatch, transport, actor, autonomy, approved, decision, window,
):
    http = live_http
    _, draft, session = hosted_imported_475(http, transport, actor)
    response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/autonomy',
        headers=headers(http.project), json={'autonomy': autonomy})
    assert response.status_code == 200, response.text
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', window)
    old_inputs = deepcopy(inputs(http, actor))
    old_pipes, old_processes = [p.stdin.getvalue() for p in http.processes], list(http.processes)
    before = hosted_effects_475(http)
    original = next(r['event']['turn_id'] for r in reversed(old_inputs) if r['event']['type'] == 'user_prompt')
    ok, pending = apply(http, draft, transport, actor)
    event = http.wait_event(actor, 'confirm')['event']
    try:
        assert ok and pending['code'] == 'CONFIRM_PENDING', pending
        assert pending['confirm_id'] == event['confirm_id']
        assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
        assert operations(http) == [] and hosted_effects_475(http) == before
        selected = draft['preview']['tasks'][0]
        assert event['params']['tasks'] == [{key: selected[key] for key in ('task_id', 'display_no', 'section', 'name', 'refs')}]
        assert event['params']['generation'] == draft['generation']
        if decision == 'http':
            hosted_confirm_475(http, actor, event, approved)
        else:
            with http.client.websocket_connect(
                f'ws://127.0.0.1:3198/api/projects/{http.project}/agent-nodes/{actor}/stream',
            ) as ws:
                assert ws.receive_json()['type'] == 'session'
                ws.send_json({'type': 'confirm', 'confirm_id': event['confirm_id'], 'approved': approved})
                while True:
                    resolved = ws.receive_json()
                    if resolved['type'] == 'confirm_resolved' and resolved['confirm_id'] == event['confirm_id']:
                        assert resolved['approved'] is approved
                        break
        drain(http.client, tasks)
        application = read(http, draft, actor)
        if approved:
            assert application['state'] == 'applied', application
            receipt = application['receipt']['result']
            ops = operations(http)
            assert len(ops) == 1 and ops[0]['op_id'] == receipt['op_id']
            assert ops[0]['turn_id'] == (None if transport == 'external' else original)
            assert len(receipt['tasks']) == 1 and receipt['tasks'][0]['task_id'] == draft['preview']['tasks'][0]['task_id']
            assert application['receipt']['applied_by'] == 'agent'
            assert application['receipt']['generation'] == draft['generation']
            hosted_committed_475(http, draft, actor, before, receipt)
        else:
            assert application['state'] == 'ready' and application['receipt'] is None, application
            assert application['error']['code'] == 'PLAN_NOT_CONFIRMED'
            assert operations(http) == [] and hosted_effects_475(http) == before
        assert not session.confirm_handlers
        if transport == 'external':
            assert list(http.processes) == old_processes
            assert [p.stdin.getvalue() for p in http.processes] == old_pipes
            assert inputs(http, actor) == old_inputs
        else:
            queued = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'queued')
            assert queued['turn_id'] == original
            payload = json.loads(queued['text'].split('：', 1)[1])
            if approved:
                assert payload['op_id'] == receipt['op_id']
            else:
                assert payload['error']['code'] == 'PLAN_NOT_CONFIRMED'
            if actor == 'codex-actor':
                http.finish(session)
            else:
                session.process.emit({'type': 'agent_end'})
            sent = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'sent')
            assert sent['request_id'] == queued['request_id'] and sent['turn_id'] == original
            if actor == 'codex-actor':
                assert len(http.processes) == len(old_processes) + 1
                assert http.processes[-1].stdin.getvalue() == queued['text']
            else:
                commands = [json.loads(line) for line in session.process.stdin.getvalue().splitlines()]
                assert [c['message'] for c in commands if c['type'] == 'follow_up'] == [queued['text']]
            current = inputs(http, actor)
            assert [r for r in current if r['event']['type'] != 'system_continuation'] == old_inputs
            assert len([r for r in current if r['event']['type'] == 'system_continuation']) == 2
    finally:
        hosted_confirm_475(http, actor, event, False)
        drain(http.client, tasks)


def end_response(http, session, actor):
    if actor == 'codex-actor':
        http.finish(session)
    else:
        last = max(r['event']['seq'] for r in http.records(http.project, actor))
        session.process.emit({'type': 'agent_end'})
        wait_record(http, actor, lambda e: e['type'] == 'agent_end' and e['seq'] > last)


@pytest.mark.parametrize('actor', ['actor', 'codex-actor'], ids=['Pi', 'Codex'])
def test_R08_natural_response_end_preserves_pending_original_turn(live_http, monkeypatch, actor):
    http = live_http
    _, draft, session = hosted_imported_475(http, 'hosted', actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    original = session._current_turn_id
    before = deepcopy(inputs(http, actor))
    ok, pending = apply(http, draft, 'hosted', actor)
    event = http.wait_event(actor, 'confirm')['event']
    try:
        assert ok and pending['code'] == 'CONFIRM_PENDING', pending
        end_response(http, session, actor)
        assert read(http, draft, actor)['state'] == 'awaiting_confirmation'
        assert operations(http) == []
        hosted_confirm_475(http, actor, event)
        drain(http.client, tasks)
        application = read(http, draft, actor)
        assert application['state'] == 'applied', application
        receipt = application['receipt']['result']
        assert operations(http)[0]['op_id'] == receipt['op_id']
        assert operations(http)[0]['turn_id'] == original
        sent = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'sent')
        assert sent['turn_id'] == original and json.loads(sent['text'].split('：', 1)[1])['op_id'] == receipt['op_id']
        assert [r for r in inputs(http, actor) if r['event']['type'] != 'system_continuation'] == before
        if actor == 'codex-actor':
            assert http.processes[-1].stdin.getvalue() == sent['text']
        else:
            commands = [json.loads(line) for line in session.process.stdin.getvalue().splitlines()]
            assert any(c['type'] == 'follow_up' and c['message'] == sent['text'] for c in commands)
    finally:
        hosted_confirm_475(http, actor, event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('actor', ['actor', 'codex-actor'], ids=['Pi', 'Codex'])
@pytest.mark.parametrize('order', ['system-first', 'user-first'])
def test_R09_real_fifo_preserves_order_and_never_delivers_system_result_to_new_user_turn(
    live_http, monkeypatch, actor, order,
):
    http = live_http
    _, draft, session = hosted_imported_475(http, 'hosted', actor)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    original = session._current_turn_id
    old_process_count = len(http.processes)
    old_pipe = session.process.stdin.getvalue()
    ok, pending = apply(http, draft, 'hosted', actor)
    event = http.wait_event(actor, 'confirm')['event']
    request_id = 'user-after-475-' + str(uuid4())

    def queue_user():
        response = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/prompt',
            headers=headers(http.project), json={'message': 'A distinct next user turn', 'request_id': request_id})
        assert response.status_code == 200, response.text
        wait_record(http, actor, lambda e: e['type'] == 'queued_input' and e['request_id'] == request_id)
        assert session._current_turn_id == original

    try:
        assert ok and pending['code'] == 'CONFIRM_PENDING', pending
        if order == 'user-first':
            queue_user()
        hosted_confirm_475(http, actor, event)
        drain(http.client, tasks)
        receipt = read(http, draft, actor)['receipt']['result']
        queued = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'queued')
        assert queued['turn_id'] == original
        if order == 'system-first':
            queue_user()
        end_response(http, session, actor)
        if order == 'system-first':
            sent = wait_record(http, actor, lambda e: e['type'] == 'system_continuation' and e['delivery'] == 'sent')
            assert sent['turn_id'] == original
            if actor == 'codex-actor':
                assert len(http.processes) == old_process_count + 1
                assert http.processes[-1].stdin.getvalue() == queued['text']
            else:
                commands = [json.loads(line) for line in session.process.stdin.getvalue()[len(old_pipe):].splitlines()]
                assert [(command['type'], command.get('message')) for command in commands
                        if command['type'] in ('prompt', 'follow_up')] == [('follow_up', queued['text'])]
            assert not any(r['event']['type'] == 'user_prompt' and r['event'].get('request_id') == request_id
                           for r in http.records(http.project, actor))
            end_response(http, session, actor)
        user = wait_record(http, actor, lambda e: e['type'] == 'user_prompt' and e.get('request_id') == request_id)
        assert user['turn_id'] != original
        if order == 'user-first':
            end_response(http, session, actor)
            wait_record(http, actor, lambda e: e['type'] == 'input_delivery_terminal'
                        and e['request_id'] == queued['request_id'] and e['delivery'] == 'unsent')
            assert not any(r['event']['type'] == 'system_continuation' and r['event']['delivery'] == 'sent'
                           for r in http.records(http.project, actor))
        if actor == 'codex-actor':
            native = [process.stdin.getvalue() for process in http.processes[old_process_count:]]
            assert native == ([queued['text'], 'A distinct next user turn'] if order == 'system-first'
                              else ['A distinct next user turn'])
        else:
            commands = [json.loads(line) for line in session.process.stdin.getvalue()[len(old_pipe):].splitlines()]
            native = [(command['type'], command.get('message')) for command in commands
                      if command['type'] in ('prompt', 'follow_up')]
            assert native == ([('follow_up', queued['text']), ('prompt', 'A distinct next user turn')]
                              if order == 'system-first' else [('prompt', 'A distinct next user turn')])
        assert operations(http)[0]['op_id'] == receipt['op_id']
        assert operations(http)[0]['turn_id'] == original
    finally:
        hosted_confirm_475(http, actor, event, False)
        drain(http.client, tasks)


def test_R30_external_omitted_turn_does_not_borrow_active_hosted_turn(live_http, monkeypatch):
    http = live_http
    _, draft, session = hosted_imported_475(http, 'external', 'actor')
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before_pipes = [process.stdin.getvalue() for process in http.processes]
    before_processes, before_inputs = list(http.processes), deepcopy(inputs(http, 'actor'))
    assert session.is_running and session._current_turn_id
    ok, pending = apply(http, draft, 'external', 'actor')
    event = http.wait_event('actor', 'confirm')['event']
    try:
        assert ok and pending['code'] == 'CONFIRM_PENDING', pending
        assert event['turn_id'] is None, event
        hosted_confirm_475(http, 'actor', event)
        drain(http.client, tasks)
        application = read(http, draft, 'actor')
        assert application['state'] == 'applied', application
        receipt = application['receipt']['result']
        ops = operations(http)
        assert len(ops) == 1 and ops[0]['op_id'] == receipt['op_id'] and ops[0]['turn_id'] is None
        assert list(http.processes) == before_processes
        assert [process.stdin.getvalue() for process in http.processes] == before_pipes
        assert inputs(http, 'actor') == before_inputs
        assert not session.confirm_handlers
    finally:
        hosted_confirm_475(http, 'actor', event, False)
        drain(http.client, tasks)
