"""Public-route scope/selection regressions for #475 (R15/16/19/21/22/24/33).

REST and external-original-SID are characterized independently; this file does
not claim hosted-reader/FIFO, browser, model, or the entire matrix as covered.
Only the approval response window and the preview's monotonic clock are seams.
"""
import json
import time

import pytest

# Routers resolve the application module at import time.
import main
from app.routers import api_agent_tools as tools
from app.services import agent_plan
from tests.test_agent_plan_apply_484 import (
    ORIGIN, assert_human_apply_error, drain, headers, track_lifetime,
)
from tests.test_agent_plan_resumable_475 import external_call, plan_http, public_state


TRANSPORTS = pytest.mark.parametrize(
    'plan_http', [False, True], indirect=True, ids=['REST', 'external-original-SID'])


def scope_call_475(http, operation, args, *, error=None, project=None, actor='actor', sid='plan-test', mcp=None):
    """Keep HTTP admission separate from the external MCP tool-success envelope."""
    project = http.project if project is None else project
    mcp = http.mcp if mcp is None else mcp
    if mcp:
        response = http.client.post('/api/mcp',
            params={'project': project, 'node': actor, 'caller': 'external'},
            headers={'Mcp-Session-Id': sid}, json={
                'jsonrpc': '2.0', 'id': 475, 'method': 'tools/call',
                'params': {'name': operation, 'arguments': args}})
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope['id'] == 475 and 'result' in envelope, envelope
        result = envelope['result']
        assert result['isError'] is (error is not None), result
        data = result['structuredContent'] if error else json.loads(result['content'][0]['text'])
    else:
        response = http.client.post(f'/api/projects/{project}/plan/{operation.split(".")[1]}',
            params={'agent_node_id': actor}, headers=headers(project), json=args)
        if error:
            assert response.is_error, response.text
            data = response.json()['detail']
        else:
            assert response.status_code == 200, response.text
            data = response.json()
    if error:
        assert data['code'] == error, data
    return data


def apply_args(http, *, generation=True, **kwargs):
    return {'plan_id': http.draft['plan_id'],
            **({'generation': http.draft['generation']} if generation else {}), **kwargs}


def scope_pending_475(http, args=None, *, human=False):
    if human:
        response = http.apply()
        assert response.status_code == 200, response.text
        result = response.json()
    else:
        result = scope_call_475(http, 'plan.apply', apply_args(http) if args is None else args)
    event = http.events.get(timeout=10)
    assert (result['code'], result['status'], result['confirm_id']) == (
        'CONFIRM_PENDING', 'awaiting_confirmation', event['confirm_id'])
    assert http.read()['application'] == {
        'state': 'awaiting_confirmation', 'generation': http.draft['generation'],
        'receipt': http.draft['application']['receipt'], 'confirm_id': event['confirm_id']}
    return event


def public_canvas(http, project=None):
    project = http.project if project is None else project
    response = http.client.get(f'/api/project-library/{project}')
    assert response.status_code == 200, response.text
    return response.json()['canvas']


def public_both_projects(http):
    result = {}
    for project in (http.project, http.foreign):
        media = http.client.get(f'/api/project-library/{project}/media')
        ops = http.client.get(f'/api/projects/{project}/ops', headers=headers(project))
        assert media.status_code == ops.status_code == 200, (media.text, ops.text)
        result[project] = {'canvas': public_canvas(http, project), 'media': media.json(), 'ops': ops.json()}
    return result


def resolved(http, confirm_id):
    """Poll a real persisted resolution, never use sleeping as a completion proof."""
    deadline = time.monotonic() + 3
    while True:
        response = http.client.get(f'/api/projects/{http.project}/agent-nodes/actor/events',
                                   params={'host': 'pi'})
        assert response.status_code == 200, response.text
        found = [record['event'] for record in response.json()['events']
                 if record['event']['type'] == 'confirm_resolved'
                 and record['event']['confirm_id'] == confirm_id]
        if found:
            return found[-1]
        assert time.monotonic() < deadline, f'No public confirm_resolved for {confirm_id}'
        time.sleep(.01)


def scope_committed_475(http, tasks, event, wanted, *, applied_by='agent', operation_count=1):
    http.confirm(event)
    drain(http.client, tasks)
    application = http.read()['application']
    assert application['state'] == 'applied', application
    receipt = application['receipt']
    assert (receipt['generation'], receipt['applied_by']) == (http.draft['generation'], applied_by)
    result = receipt['result']
    assert [task['task_id'] for task in result['tasks']] == wanted
    assert result['op_id'] and result['touched_node_ids']
    records = public_state(http)['ops']['operations']
    assert len(records) == operation_count
    operation = next(record for record in records if record['op_id'] == result['op_id'])
    assert (operation['agent_node_id'], operation['tool']) == ('actor', 'plan.apply')
    assert set(operation['touched_node_ids']) == set(result['touched_node_ids'])
    canvas = public_canvas(http)
    nodes = {node['id']: node for node in canvas['nodes']}
    assert set(result['touched_node_ids']) <= nodes.keys()
    for task in result['tasks']:
        prompt, slot = nodes[task['prompt_node']], nodes[task['gen_node']]
        assert prompt['excel_task_id'] == slot['excel_task_id'] == task['task_id']
        assert prompt['agentNodeId'] == slot['agentNodeId'] == 'actor'
        assert slot['runSettings']['prompt'] == '' and slot['runSettings']['count'] == 1
        assert any(edge['from'] == prompt['id'] and edge['to'] == slot['id']
                   and edge['kind'] == 'input' for edge in canvas['connections'])
    return receipt


def late_positive(http, monkeypatch):
    """Safety negatives have a real late-approval execution on the same scope."""
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    first = http.draft['preview']['tasks'][0]['task_id']
    http.select(0)
    event = scope_pending_475(http)
    try:
        return scope_committed_475(http, tasks, event, [first])
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


@TRANSPORTS
@pytest.mark.parametrize('explicit', [False, True], ids=['selected-default', 'explicit-unselected-ID'])
def test_R16_late_approval_applies_only_the_frozen_selected_or_explicit_tasks(plan_http, monkeypatch, explicit):
    http = plan_http
    http.select(0)
    first, second, _ = [task['task_id'] for task in http.draft['preview']['tasks']]
    wanted = [second] if explicit else [first]
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before = public_both_projects(http)
    args = apply_args(http, **({'task_ids': wanted} if explicit else {}))
    event = scope_pending_475(http, args)
    try:
        assert [task['task_id'] for task in event['params']['tasks']] == wanted
        assert event['params']['generation'] == http.draft['generation']
        scope_call_475(http, 'plan.apply', apply_args(http, task_ids=[first, second]), error='CONFLICT')
        assert http.events.empty() and public_both_projects(http) == before
        receipt = scope_committed_475(http, tasks, event, wanted)
        after = public_both_projects(http)
        assert after[http.foreign] == before[http.foreign]
        assert after[http.project]['media'] == before[http.project]['media']
        assert after[http.project]['canvas']['revision'] == before[http.project]['canvas']['revision'] + 1
        assert {node['excel_task_id'] for node in after[http.project]['canvas']['nodes']
                if node.get('planLineage')} == set(wanted)
        assert scope_call_475(http, 'plan.import_excel', {'attachment_id': http.source['id']})['application']['receipt'] == receipt
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


@TRANSPORTS
@pytest.mark.parametrize('editor', ['human', 'agent'])
@pytest.mark.parametrize('generation', [True, False], ids=['explicit-generation', 'omitted-generation'])
def test_R15_edit_retires_old_pending_without_old_cleanup_clearing_new_reservation(
        plan_http, monkeypatch, editor, generation):
    http = plan_http
    http.select(0)
    first, second, _ = [task['task_id'] for task in http.draft['preview']['tasks']]
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before = public_both_projects(http)
    old_args = apply_args(http, generation=generation)
    old = scope_pending_475(http, old_args)
    new = None
    try:
        edits = [{'task_id': first, 'select': False}, {'task_id': second, 'select': True},
                 {'task_id': second, 'rename': 'Human-approved replacement'}]
        if editor == 'human':
            http.edit(*edits)
        else:
            http.draft = scope_call_475(http, 'plan.edit_preview', {
                **apply_args(http, generation=generation), 'ops': edits})
        assert http.draft['generation'] == 3 and http.read()['application']['state'] == 'ready'
        assert public_both_projects(http) == before
        stale = http.client.get(http.path, params={'agent_node_id': 'actor', 'generation': 2},
                                headers=headers(http.project))
        assert_human_apply_error(stale, 409, 'PLAN_STALE')
        scope_call_475(http, 'plan.apply', {**old_args, 'generation': 2}, error='PLAN_STALE')
        new = scope_pending_475(http, apply_args(http, generation=generation))
        assert new['confirm_id'] != old['confirm_id']
        http.confirm(old)
        assert resolved(http, old['confirm_id'])['approved'] is False
        assert http.read()['application']['confirm_id'] == new['confirm_id']
        assert public_both_projects(http) == before
        assert [task['task_id'] for task in new['params']['tasks']] == [second]
        assert new['params']['tasks'][0]['name'] == 'Human-approved replacement'
        receipt = scope_committed_475(http, tasks, new, [second])
        assert receipt['generation'] == 3
    finally:
        http.confirm(old, False)
        if new:
            http.confirm(new, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('plan_http', [True], indirect=True, ids=['external-original-SID'])
@pytest.mark.parametrize('operation', ['plan.apply', 'plan.edit_preview'])
@pytest.mark.parametrize('scope', ['foreign-project', 'other-actor', 'wrong-SID', 'REST-binding'])
def test_R19_original_external_scope_cannot_be_borrowed_and_remains_usable(
        plan_http, monkeypatch, operation, scope):
    http = plan_http
    draft, before = http.read(), public_both_projects(http)
    args = apply_args(http)
    if operation.endswith('edit_preview'):
        args['ops'] = [{'task_id': draft['preview']['tasks'][0]['task_id'], 'select': True}]
    options = {'foreign-project': {'project': http.foreign}, 'other-actor': {'actor': 'other'},
               'wrong-SID': {'sid': 'unrelated-475'}, 'REST-binding': {'mcp': False}}[scope]
    scope_call_475(http, operation, args, error='PLAN_STALE', **options)
    assert http.read() == draft and public_both_projects(http) == before
    assert http.events.empty() and not http.session.confirm_handlers
    late_positive(http, monkeypatch)


@TRANSPORTS
@pytest.mark.parametrize('human_operation', ['GET', 'edit', 'apply'])
@pytest.mark.parametrize('target', ['foreign-project', 'other-actor'])
def test_R19_human_routes_do_not_leak_other_project_or_actor_preview(
        plan_http, monkeypatch, human_operation, target):
    http = plan_http
    draft, before = http.read(), public_both_projects(http)
    project = http.foreign if target == 'foreign-project' else http.project
    actor = 'other' if target == 'other-actor' else 'actor'
    path = f'/api/projects/{project}/plan/previews/{draft["plan_id"]}'
    body = {'generation': draft['generation']}
    if human_operation == 'edit':
        path += '/edits'
        body['ops'] = [{'task_id': draft['preview']['tasks'][0]['task_id'], 'select': True}]
    elif human_operation == 'apply':
        path += '/apply'
    response = http.client.request('GET' if human_operation == 'GET' else 'POST', path,
        params={'agent_node_id': actor}, headers=headers(project),
        **({} if human_operation == 'GET' else {'json': body}))
    assert_human_apply_error(response, 409, 'PLAN_STALE')
    assert http.read() == draft and public_both_projects(http) == before
    assert http.events.empty() and not http.session.confirm_handlers
    late_positive(http, monkeypatch)


@pytest.fixture
def preview_clock(monkeypatch):
    # Only the preview cache's clock moves; asyncio deadlines and confirmation
    # transport stay real. The public import below establishes the TTL origin.
    clock = [10000.0]
    monkeypatch.setattr(agent_plan, 'monotonic', lambda: clock[0])
    return clock


@pytest.fixture
def clocked_http(preview_clock, plan_http):
    return plan_http, preview_clock


@TRANSPORTS
def test_R21_preview_ttl_is_not_extended_by_get_edit_or_pending_apply(clocked_http, monkeypatch):
    http, clock = clocked_http
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before = public_both_projects(http)
    clock[0] = 11798.0
    assert http.read()['generation'] == 1
    clock[0] = 11799.0
    http.select(0)
    clock[0] = 11799.5
    old = scope_pending_475(http)
    try:
        clock[0] = 11800.0
        assert_human_apply_error(http.client.get(http.path, params={'agent_node_id': 'actor'},
                                                 headers=headers(http.project)), 409, 'PLAN_STALE')
        assert_human_apply_error(http.apply(), 409, 'PLAN_STALE')
        edit = http.client.post(http.path + '/edits', params={'agent_node_id': 'actor'},
            headers=headers(http.project), json={'generation': 2, 'ops': [
                {'task_id': http.draft['preview']['tasks'][0]['task_id'], 'rename': 'Expired edit'}]})
        assert_human_apply_error(edit, 409, 'PLAN_STALE')
        scope_call_475(http, 'plan.apply', apply_args(http), error='PLAN_STALE')
        http.confirm(old)
        drain(http.client, tasks)
        assert public_both_projects(http) == before
        old_id = http.draft['plan_id']
        http.draft = scope_call_475(http, 'plan.import_excel', {'attachment_id': http.source['id']})
        assert http.draft['plan_id'] != old_id and http.draft['generation'] == 1
        http.path = f'/api/projects/{http.project}/plan/previews/{http.draft["plan_id"]}'
        http.select(0)
        new = scope_pending_475(http)
        try:
            http.confirm(old)
            assert http.read()['application']['confirm_id'] == new['confirm_id']
            scope_committed_475(http, tasks, new, [http.draft['preview']['tasks'][0]['task_id']])
        finally:
            http.confirm(new, False)
    finally:
        http.confirm(old, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('plan_http', [True], indirect=True, ids=['external-original-SID'])
def test_R21_real_public_import_cache_eviction_retires_pending_but_not_reimport(plan_http, monkeypatch):
    http = plan_http
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    before = public_both_projects(http)
    old = scope_pending_475(http)
    try:
        # Distinct real MCP bindings fill the documented 64-preview Store cache;
        # no private cache insertion/deletion stands in for public eviction.
        for index in range(64):
            imported = external_call(http, 'plan.import_excel', {'attachment_id': http.source['id']},
                                     sid=f'eviction-475-{index}')
            assert imported['isError'] is False, imported
        assert_human_apply_error(http.client.get(http.path, params={'agent_node_id': 'actor'},
                                                 headers=headers(http.project)), 409, 'PLAN_STALE')
        http.confirm(old)
        drain(http.client, tasks)
        assert public_both_projects(http) == before
        old_id = http.draft['plan_id']
        http.draft = scope_call_475(http, 'plan.import_excel', {'attachment_id': http.source['id']})
        assert http.draft['plan_id'] != old_id
        http.path = f'/api/projects/{http.project}/plan/previews/{http.draft["plan_id"]}'
        late_positive(http, monkeypatch)
    finally:
        http.confirm(old, False)
        drain(http.client, tasks)


@TRANSPORTS
@pytest.mark.parametrize('winner', ['agent', 'human'])
def test_R22_live_reservation_excludes_agent_and_human_competitors_and_commits_once(
        plan_http, monkeypatch, winner):
    http = plan_http
    http.select(0)
    wanted = [http.draft['preview']['tasks'][0]['task_id']]
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', 0)
    before = public_both_projects(http)
    event = scope_pending_475(http, human=winner == 'human')
    try:
        scope_call_475(http, 'plan.apply', apply_args(http), error='CONFLICT')
        assert_human_apply_error(http.apply(), 409, 'CONFLICT')
        scope_call_475(http, 'plan.apply', apply_args(http, task_ids=[]), error='CONFLICT')
        assert http.events.empty() and len(http.session.confirm_handlers) == 1
        assert public_both_projects(http) == before
        receipt = scope_committed_475(http, tasks, event, wanted, applied_by=winner)
        settled = public_both_projects(http)
        assert settled[http.project]['canvas']['revision'] == before[http.project]['canvas']['revision'] + 1
        http.confirm(event)
        http.confirm(event, False)
        drain(http.client, tasks)
        scope_call_475(http, 'plan.apply', apply_args(http), error='PLAN_ALREADY_APPLIED')
        assert_human_apply_error(http.apply(), 409, 'PLAN_ALREADY_APPLIED')
        assert http.read()['application']['receipt'] == receipt
        assert public_both_projects(http) == settled and http.events.empty()
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


@TRANSPORTS
@pytest.mark.parametrize('selection', ['default-empty', 'explicit-empty'])
def test_R24_agent_empty_and_human_zero_selection_do_not_consume_generation(
        plan_http, monkeypatch, selection):
    http = plan_http
    if selection == 'explicit-empty':
        http.select(0)
    draft, before = http.read(), public_both_projects(http)
    args = apply_args(http, **({'task_ids': []} if selection == 'explicit-empty' else {}))
    result = scope_call_475(http, 'plan.apply', args)
    assert result['tasks'] == result['groups'] == result['already_applied'] == []
    assert result['op_id'] is None
    assert http.read() == draft and public_both_projects(http) == before and http.events.empty()
    if selection == 'explicit-empty':
        http.edit({'task_id': http.draft['preview']['tasks'][0]['task_id'], 'select': False})
    assert_human_apply_error(http.apply(), 400, 'INVALID_ARGUMENT')
    assert public_both_projects(http) == before and http.events.empty()
    assert http.read()['application']['state'] == 'ready'
    late_positive(http, monkeypatch)


@TRANSPORTS
def test_R24_all_existing_is_no_changes_not_a_new_receipt_or_operation(plan_http, monkeypatch):
    http = plan_http
    original = late_positive(http, monkeypatch)
    first, second, _ = [task['task_id'] for task in http.draft['preview']['tasks']]
    http.edit({'task_id': first, 'rename': 'Does not overwrite the surviving prompt'})
    draft, before = http.read(), public_both_projects(http)
    result = scope_call_475(http, 'plan.apply', apply_args(http))
    assert result['no_changes'] is True and result['already_applied'] == [first]
    assert result['tasks'] == result['groups'] == [] and result['op_id'] is None
    human = http.apply()
    assert human.status_code == 200 and human.json()['no_changes'] is True, human.text
    assert human.json()['result']['already_applied'] == [first]
    assert http.read() == draft and draft['application']['receipt'] == original
    assert draft['application']['state'] == 'ready' and http.events.empty()
    assert public_both_projects(http) == before
    http.edit({'task_id': second, 'select': True})
    tasks = track_lifetime(monkeypatch)
    event = scope_pending_475(http)
    try:
        receipt = scope_committed_475(http, tasks, event, [second], operation_count=2)
        assert receipt['result']['already_applied'] == [first]
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


@TRANSPORTS
@pytest.mark.parametrize('bad', ['unknown-field', 'bool-generation', 'null-task-ids', 'string-task-ids',
                                 'duplicate-task-ids', 'unknown-task', 'bad-layout'])
def test_R33_invalid_agent_schema_never_reserves_or_prevents_valid_late_apply(plan_http, monkeypatch, bad):
    http = plan_http
    draft, before = http.read(), public_both_projects(http)
    first = draft['preview']['tasks'][0]['task_id']
    overrides = {'unknown-field': {'approved': True}, 'bool-generation': {'generation': True},
                 'null-task-ids': {'task_ids': None}, 'string-task-ids': {'task_ids': first},
                 'duplicate-task-ids': {'task_ids': [first, first]},
                 'unknown-task': {'task_ids': ['missing-475']}, 'bad-layout': {'layout': 'anywhere'}}[bad]
    code = {'bool-generation': 'PLAN_STALE', 'unknown-task': 'TASK_NOT_FOUND'}.get(bad, 'INVALID_ARGUMENT')
    scope_call_475(http, 'plan.apply', {**apply_args(http), **overrides}, error=code)
    assert http.read() == draft and public_both_projects(http) == before
    assert http.events.empty() and not http.session.confirm_handlers
    late_positive(http, monkeypatch)


@TRANSPORTS
@pytest.mark.parametrize('surface', ['GET', 'edit', 'human-apply', 'agent-apply'])
@pytest.mark.parametrize('guard', ['foreign-origin', 'spoofed-host', 'wrong-project-header'])
def test_R33_transport_guard_precedes_pending_execution(plan_http, monkeypatch, surface, guard):
    http = plan_http
    draft, before = http.read(), public_both_projects(http)
    request_headers = headers(http.project)
    request_headers.update({'foreign-origin': {'Origin': 'http://untrusted.invalid'},
                            'spoofed-host': {'Host': 'untrusted.invalid:3198', 'Origin': 'http://untrusted.invalid:3198'},
                            'wrong-project-header': {'X-Agent-Project': http.foreign}}[guard])
    path = http.path
    body = {'generation': draft['generation']}
    if surface == 'edit':
        path += '/edits'
        body['ops'] = [{'task_id': draft['preview']['tasks'][0]['task_id'], 'select': True}]
    elif surface == 'human-apply':
        path += '/apply'
    elif surface == 'agent-apply':
        path = f'/api/projects/{http.project}/plan/apply'
        body = apply_args(http)
    response = http.client.request('GET' if surface == 'GET' else 'POST', path,
        params={'agent_node_id': 'actor'}, headers=request_headers,
        **({} if surface == 'GET' else {'json': body}))
    if guard == 'wrong-project-header':
        assert_human_apply_error(response, 409 if surface == 'agent-apply' else 403,
                                 'PLAN_STALE' if surface == 'agent-apply' else 'PROJECT_MISMATCH')
    else:
        assert response.status_code == 403, response.text
    assert http.read() == draft and public_both_projects(http) == before
    assert http.events.empty() and not http.session.confirm_handlers
    late_positive(http, monkeypatch)


@TRANSPORTS
@pytest.mark.parametrize('bad', ['repeated-actor', 'repeated-generation', 'repeated-project-header',
                                 'truncated-json', 'non-object', 'unknown-body-field', 'boolean-generation'])
def test_R33_human_apply_rejects_ambiguous_or_malformed_intent_before_reserving(plan_http, monkeypatch, bad):
    http = plan_http
    draft, before = http.read(), public_both_projects(http)
    options = {}
    if bad == 'repeated-actor':
        options['params'] = [('agent_node_id', 'actor'), ('agent_node_id', 'actor')]
    elif bad == 'repeated-generation':
        options['params'] = [('agent_node_id', 'actor'), ('generation', '1'), ('generation', '1')]
    elif bad == 'repeated-project-header':
        options['headers'] = [('Origin', ORIGIN), ('X-Agent-Project', http.project),
                              ('X-Agent-Project', http.project)]
    elif bad in ('truncated-json', 'non-object'):
        options['content'] = '{"generation":' if bad == 'truncated-json' else '[]'
    else:
        options['json'] = {'generation': True} if bad == 'boolean-generation' else {
            'generation': draft['generation'], 'task_ids': []}
    assert_human_apply_error(http.apply(**options), 403 if bad == 'repeated-project-header' else 400,
                             'PROJECT_MISMATCH' if bad == 'repeated-project-header' else 'INVALID_ARGUMENT')
    assert http.read() == draft and public_both_projects(http) == before
    assert http.events.empty() and not http.session.confirm_handlers
    late_positive(http, monkeypatch)
