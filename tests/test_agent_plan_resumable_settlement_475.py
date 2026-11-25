"""Public settlement characterization for #475: R18/R23/R25-R28 and retained R32.

First valid passes are CHARACTERIZATION, not historical RED evidence. These tests
use the offline #399/#484 fixture only at its existing native boundary; approval,
execution, persistence, operation bookkeeping, and undo remain production code.
Hosted delivery/FIFO, genuine lock-internal RevisionConflict, and hosted-import
human apply are NOT_RUN here, not inferred from transport failure injection.
"""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
from threading import Event

import pytest

# Routers must bind the real application rather than the guarded entry module.
import main
from app.routers import api_agent_tools as tools
from tests.test_agent_plan_399 import tool
from tests.test_agent_plan_apply_484 import drain, headers, track_lifetime
from tests.test_agent_plan_resumable_475 import agent_apply, plan_http, public_state


@pytest.fixture(autouse=True)
def characterization(record_property):
    record_property('classification', 'CHARACTERIZATION')


def settlement_canvas_475(http):
    response = http.client.get(f'/api/project-library/{http.project}', headers=headers(http.project))
    assert response.status_code == 200, response.text
    return response.json()['canvas']


def settlement_state_475(http):
    response = http.client.get(f'/api/project-library/{http.project}/media', headers=headers(http.project))
    assert response.status_code == 200, response.text
    return {**public_state(http), 'canvas': settlement_canvas_475(http), 'media': response.json()}


def settlement_mutate_475(http, mutations):
    response = http.client.patch(f'/api/project-library/{http.project}/canvas', headers=headers(http.project),
        json={'base_revision': settlement_canvas_475(http)['revision'], 'mutations': mutations})
    assert response.status_code == 200, response.text
    # The shared fixture disables background snapshots (snapshot_delay=0).
    # This is a real persistence completion barrier, not a replacement for the REST mutation.
    http.store.flush(http.project)
    saved = http.client.get(f'/api/project-library/{http.project}/save-status', headers=headers(http.project))
    assert saved.status_code == 200 and saved.json()['saved'] is True, saved.text
    disk = json.loads((http.store.root / http.project / 'canvas.json').read_text(encoding='utf-8'))
    assert disk['revision'] == response.json()['revision']
    assert disk['nodes'] == settlement_canvas_475(http)['nodes']
    return response.json()


def settlement_error_475(response, status, code):
    assert response.status_code == status, response.text
    detail = response.json()['detail']
    assert detail['code'] == code, response.text
    return detail


def original_preview(http):
    result = tool(http.client, http.project, 'plan.import_excel',
        {'attachment_id': http.source['id']}, mcp=http.mcp)
    assert result == http.read()
    return result


@contextmanager
def settlement_pending_475(http, tasks, *, human=False):
    before = settlement_state_475(http)
    response = http.apply() if human else agent_apply(http)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['code'], body['status']) == ('CONFIRM_PENDING', 'awaiting_confirmation'), body
    event = http.events.get(timeout=10)
    try:
        assert body['confirm_id'] == event['confirm_id']
        assert http.read()['application'] == {'state': 'awaiting_confirmation',
            'generation': http.draft['generation'], 'receipt': before_receipt(http),
            'confirm_id': event['confirm_id']}
        assert settlement_state_475(http) == before
        yield event, before
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


def before_receipt(http):
    return http.draft['application']['receipt']


def settlement_prepare_475(http, monkeypatch, *indexes):
    http.select(*indexes)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    return track_lifetime(monkeypatch)


def consumed(http, receipt):
    before = settlement_state_475(http)
    for response in (agent_apply(http), http.apply()):
        detail = settlement_error_475(response, 409, 'PLAN_ALREADY_APPLIED')
        assert detail['retryable'] is False and detail['details']['receipt'] == receipt
    assert original_preview(http)['application']['receipt'] == receipt
    assert settlement_state_475(http) == before and http.events.empty()


def undo(http, op_id):
    return http.client.post(f'/api/projects/{http.project}/ops/undo',
        headers=headers(http.project), json={'op_id': op_id})


def settlement_committed_475(http, before, *, applied_by='agent', expected_tasks=None, journal=True):
    application = original_preview(http)['application']
    assert application['state'] == 'applied', application
    receipt = application['receipt']
    assert (receipt['generation'], receipt['applied_by']) == (http.draft['generation'], applied_by)
    result = receipt['result']
    wanted = expected_tasks if expected_tasks is not None else [
        task['task_id'] for task in http.draft['preview']['tasks'] if task['selected']]
    assert [task['task_id'] for task in result['tasks']] == wanted
    assert result['op_id'] and result['touched_node_ids']
    after = settlement_state_475(http)
    disk = json.loads((http.store.root / http.project / 'canvas.json').read_text(encoding='utf-8'))
    assert after['canvas']['revision'] == disk['revision'] == before['canvas']['revision'] + 1
    assert after['canvas']['nodes'] == disk['nodes']
    assert after['canvas']['connections'] == disk['connections']
    old_ids = {node['id'] for node in before['canvas']['nodes']}
    new_nodes = {node['id']: node for node in after['canvas']['nodes'] if node['id'] not in old_ids}
    assert set(new_nodes) == set(result['touched_node_ids'])
    assert after['nodes']['count'] == before['nodes']['count'] + len(new_nodes)
    assert all(node['createdBy'] == 'agent' and node['agentNodeId'] == 'actor' for node in new_nodes.values())
    for task in result['tasks']:
        assert not task['ref_nodes']
        assert new_nodes[task['prompt_node']]['text']
        slot = new_nodes[task['gen_node']]
        assert not slot.get('images') and slot['runSettings']['prompt'] == ''
        assert any(edge.get('from') == task['prompt_node'] and edge.get('to') == task['gen_node']
            and edge.get('kind') == 'input' for edge in after['canvas']['connections'])
    for ident, node in new_nodes.items():
        response = http.client.get(f'/api/projects/{http.project}/canvas/nodes/{ident}',
            headers=headers(http.project))
        assert response.status_code == 200 and response.json() == node, response.text
    assert after['media'] == before['media']
    records = after['ops']['operations']
    if journal:
        assert len(records) == len(before['ops']['operations']) + 1
        operation = next(record for record in records if record['op_id'] == result['op_id'])
        assert (operation['tool'], operation['agent_node_id']) == ('plan.apply', 'actor')
        assert operation['before'] and operation['after']
        assert set(operation['touched_node_ids']) == set(result['touched_node_ids'])
    else:
        assert records == before['ops']['operations']
    assert not http.session.confirm_handlers
    return receipt


def assert_undone(http, receipt, before):
    result = undo(http, receipt['result']['op_id'])
    assert result.status_code == 200 and result.json()['reverted'] == [receipt['result']['op_id']], result.text
    restored = settlement_canvas_475(http)
    assert restored['nodes'] == before['canvas']['nodes']
    assert restored['connections'] == before['canvas']['connections']
    assert original_preview(http)['application']['receipt'] == receipt


def test_R18_R23_late_approval_positive_control_real_undo_does_not_revive_generation(plan_http, monkeypatch):
    http = plan_http
    tasks = settlement_prepare_475(http, monkeypatch, 0)
    with settlement_pending_475(http, tasks) as (event, before):
        http.confirm(event)
        drain(http.client, tasks)
        receipt = settlement_committed_475(http, before)
        consumed(http, receipt)
        http.confirm(event)
        drain(http.client, tasks)
        assert len(public_state(http)['ops']['operations']) == 1
        assert_undone(http, receipt, before)
        consumed(http, receipt)
        http.edit({'task_id': http.draft['preview']['tasks'][0]['task_id'], 'rename': 'Reapproved after undo'})
        assert http.draft['application'] == {'state': 'ready', 'generation': 3, 'receipt': receipt}
        with settlement_pending_475(http, tasks) as (next_event, next_before):
            assert next_event['confirm_id'] != event['confirm_id']
            http.confirm(next_event)
            drain(http.client, tasks)
            replacement = settlement_committed_475(http, next_before)
            assert replacement['result']['op_id'] != receipt['result']['op_id']
            assert_undone(http, replacement, next_before)


@pytest.mark.parametrize('race', ['before-approval', 'after-start-before-final-CAS'])
def test_R18_public_canvas_race_keeps_human_content_and_no_plan_commit(plan_http, monkeypatch, race):
    http = plan_http
    tasks = settlement_prepare_475(http, monkeypatch, 0)
    entered, release = Event(), Event()
    normal = tools._broadcast_canvas_updated

    async def hold_start(*args, **kwargs):
        await normal(*args, **kwargs)
        if kwargs.get('phase') == 'start':
            entered.set()
            assert await asyncio.to_thread(release.wait, 10), 'transport barrier not released'

    with settlement_pending_475(http, tasks) as (event, before):
        try:
            if race == 'after-start-before-final-CAS':
                monkeypatch.setattr(tools, '_broadcast_canvas_updated', hold_start)
                http.confirm(event)
                assert entered.wait(10), 'real start broadcast not reached'
            settlement_mutate_475(http, [{'op': 'upsert_node', 'node': {
                'id': 'human-475', 'type': 'smart-prompt', 'text': 'Keep the independent human edit'}}])
            changed = settlement_state_475(http)
            if race == 'before-approval':
                http.confirm(event)
        finally:
            release.set()
        drain(http.client, tasks)
        application = original_preview(http)['application']
        assert application['state'] == 'ready' and application['receipt'] is None, application
        assert application['error']['code'] == 'PLAN_STALE', application
        assert settlement_state_475(http) == changed
        assert changed['canvas']['revision'] == before['canvas']['revision'] + 1
        assert changed['ops'] == before['ops']
        assert not any(node.get('planLineage') for node in changed['canvas']['nodes'])
        assert not http.session.confirm_handlers


def test_R23_human_delete_does_not_revive_generation_new_approval_only_adds_missing_task(plan_http, monkeypatch):
    http = plan_http
    tasks = settlement_prepare_475(http, monkeypatch, 0, 1)
    with settlement_pending_475(http, tasks) as (event, before):
        http.confirm(event)
        drain(http.client, tasks)
        receipt = settlement_committed_475(http, before)
        first, second = receipt['result']['tasks']
        raw = {node['id']: node for node in settlement_canvas_475(http)['nodes']}
        survivor = {**raw[first['prompt_node']], 'text': 'Human keeps this exact prompt', 'userPinned': True}
        frame_id = receipt['result']['groups'][0]['group_id']
        frame = {**raw[frame_id], 'title': 'Human frame', 'w': 1777}
        settlement_mutate_475(http, [{'op': 'upsert_node', 'node': survivor}, {'op': 'upsert_node', 'node': frame},
            {'op': 'remove_node', 'node_id': second['prompt_node']},
            {'op': 'remove_node', 'node_id': second['gen_node']}])
        consumed(http, receipt)
        retained = {node['id']: node for node in settlement_canvas_475(http)['nodes']}
        http.edit({'task_id': first['task_id'], 'rename': 'Must not overwrite human prompt'})
        assert http.read()['application'] == {'state': 'ready', 'generation': 3, 'receipt': receipt}
        with settlement_pending_475(http, tasks) as (next_event, next_before):
            assert [task['task_id'] for task in next_event['params']['tasks']] == [second['task_id']]
            http.confirm(next_event)
            drain(http.client, tasks)
            appended = settlement_committed_475(http, next_before, expected_tasks=[second['task_id']])
            assert appended['result']['already_applied'] == [first['task_id']]
            current = {node['id']: node for node in settlement_canvas_475(http)['nodes']}
            assert current[first['prompt_node']] == retained[first['prompt_node']]
            assert current[frame_id] == retained[frame_id]
            consumed(http, appended)
            # The human-kept frame still references these stable task IDs. Undo
            # must not remove nodes underneath that surviving membership (#393).
            before_undo = settlement_state_475(http)
            denied = settlement_error_475(undo(http, appended['result']['op_id']), 409, 'UNDO_CONFLICT')
            assert denied['details']['reverted'] == []
            conflicts = denied['details']['conflicts']
            assert {item['node_id'] for item in conflicts} == {second['prompt_node'], second['gen_node']}
            assert all(item['reason'] == 'remaining smart-group membership' for item in conflicts)
            assert settlement_state_475(http) == before_undo
            consumed(http, appended)


def test_R25_precommit_start_transport_failure_releases_reservation_and_can_reapprove(plan_http, monkeypatch):
    http = plan_http
    tasks = settlement_prepare_475(http, monkeypatch, 0)
    normal = tools._broadcast_canvas_updated
    attempts = []

    async def fail_start(*args, **kwargs):
        if kwargs.get('phase') == 'start':
            attempts.append(kwargs['op_id'])
            raise OSError('475 injected precommit start transport failure')
        return await normal(*args, **kwargs)

    with settlement_pending_475(http, tasks) as (event, before):
        with monkeypatch.context() as patch:
            patch.setattr(tools, '_broadcast_canvas_updated', fail_start)
            http.confirm(event)
            drain(http.client, tasks)
            application = original_preview(http)['application']
            assert application['state'] == 'ready' and application['receipt'] is None, application
            assert application['error']['code'] == 'CANVAS_CONFLICT'
            assert '475 injected precommit start transport failure' in application['error']['message']
            assert len(attempts) == 1 and settlement_state_475(http) == before
            settlement_error_475(undo(http, attempts[0]), 404, 'NOT_FOUND')
            assert settlement_state_475(http) == before
        with settlement_pending_475(http, tasks) as (new_event, new_before):
            assert new_event['confirm_id'] != event['confirm_id']
            http.confirm(new_event)
            drain(http.client, tasks)
            receipt = settlement_committed_475(http, new_before)
            assert receipt['result']['op_id'] != attempts[0]
            assert_undone(http, receipt, new_before)


@pytest.mark.parametrize('failure', ['end-stream', 'append-failed', 'append-and-end'])
def test_R26_R27_durable_commit_keeps_original_receipt_error_and_true_undo_status(plan_http, monkeypatch, failure):
    from app.services import agent_ops_store

    http = plan_http
    tasks = settlement_prepare_475(http, monkeypatch, 0)
    normal = tools._broadcast_canvas_updated
    injected = []

    async def fail_end(*args, **kwargs):
        if kwargs.get('phase') == 'end':
            injected.append('end')
            raise OSError('475 injected end transport failure')
        return await normal(*args, **kwargs)

    def fail_append(*args, **kwargs):
        injected.append('append')
        raise OSError('475 injected sidecar append failure')

    with settlement_pending_475(http, tasks) as (event, before):
        with monkeypatch.context() as patch:
            if failure in ('end-stream', 'append-and-end'):
                patch.setattr(tools, '_broadcast_canvas_updated', fail_end)
            if failure in ('append-failed', 'append-and-end'):
                patch.setattr(agent_ops_store, 'append_operation', fail_append)
            http.confirm(event)
            drain(http.client, tasks)
            has_journal = failure == 'end-stream'
            receipt = settlement_committed_475(http, before, journal=has_journal)
            application = http.read()['application']
            detail = application['error']
            assert detail['retryable'] is False and '不要重复' in detail['hint']
            if not has_journal:
                assert 'append' in injected
                assert detail['details']['journal_status'] == 'append_failed'
                assert detail['details']['op_id'] == receipt['result']['op_id']
            if failure in ('end-stream', 'append-and-end'):
                assert 'end' in injected and '475 injected end transport failure' in detail['message']
            consumed(http, receipt)
            settled = settlement_state_475(http)
            http.confirm(event)
            drain(http.client, tasks)
            assert settlement_state_475(http) == settled
        http.store.flush(http.project)
        assert http.read()['application'] == application
        if has_journal:
            assert_undone(http, receipt, before)
        else:
            before_undo = settlement_state_475(http)
            result = undo(http, receipt['result']['op_id'])
            denied = settlement_error_475(result, 409, 'UNDO_CONFLICT')
            assert denied['retryable'] is False
            assert settlement_state_475(http) == before_undo
        consumed(http, receipt)


@pytest.mark.parametrize('failure', ['before-replace', 'lost-replace-ack'])
def test_R25_R28_unproved_disk_result_is_unknown_not_safe_retry_or_fictitious_undo(plan_http, monkeypatch, failure):
    import firefly_json_io

    http = plan_http
    tasks = settlement_prepare_475(http, monkeypatch, 0)
    target = http.store.root / http.project / 'canvas.json'
    original_disk = target.read_bytes()
    replace = firefly_json_io._replace_staged
    broadcast = tools._broadcast_canvas_updated
    replacements, operations = [], []

    def disk_boundary(staging, path):
        if path != target:
            return replace(staging, path)
        replacements.append(path)
        if failure == 'lost-replace-ack':
            replace(staging, path)
        raise OSError('475 injected ' + failure)

    async def observe_transport(*args, **kwargs):
        if kwargs.get('phase') == 'start':
            operations.append(kwargs['op_id'])
        return await broadcast(*args, **kwargs)

    with settlement_pending_475(http, tasks) as (event, before):
        with monkeypatch.context() as patch:
            patch.setattr(firefly_json_io, '_replace_staged', disk_boundary)
            patch.setattr(tools, '_broadcast_canvas_updated', observe_transport)
            http.confirm(event)
            drain(http.client, tasks)
            assert replacements and len(operations) == 1
            application = original_preview(http)['application']
            # Pre-replace failure after the in-memory CAS has no acknowledged proof
            # either: the existing #484 contract conservatively keeps it unknown.
            assert application['state'] == 'unknown' and application['receipt'] is None, application
            assert application['error']['retryable'] is False
            assert '475 injected ' + failure in application['error']['message']
            disk = json.loads(target.read_text(encoding='utf-8'))
            if failure == 'before-replace':
                assert target.read_bytes() == original_disk
            else:
                assert disk['revision'] == before['canvas']['revision'] + 1
                assert disk['nodes'] == settlement_canvas_475(http)['nodes']
                assert disk['connections'] == settlement_canvas_475(http)['connections']
            assert public_state(http)['ops'] == before['ops']
            settled = settlement_state_475(http)
            for response in (agent_apply(http), http.apply()):
                detail = settlement_error_475(response, 409, 'CONFLICT')
                assert detail['retryable'] is False and detail['details']['state'] == 'unknown'
            # A pending original operation uses the existing undo error layer,
            # unlike the append_failed conflict; neither may report a reversion.
            denied = settlement_error_475(undo(http, operations[0]), 500, 'INTERNAL_ERROR')
            assert denied['retryable'] is False
            assert denied['details']['status'] == 'pending'
            assert denied['details']['reverted'] == denied['details']['already_reverted'] == []
            assert denied['details']['conflicts'] == [{'op_id': operations[0], 'node_id': None,
                'reason': 'original operation is pending'}]
            http.confirm(event)
            drain(http.client, tasks)
            assert settlement_state_475(http) == settled and http.events.empty()
            assert http.read()['application'] == application
            assert not http.session.confirm_handlers


@pytest.mark.parametrize('plan_http', [False, True], indirect=True, ids=['REST-import', 'external-original-SID-import'])
@pytest.mark.parametrize('approved', [True, False], ids=['late-approval', 'late-rejection'])
def test_R32_retained_human_apply_is_turnless_has_real_settlement_and_no_input_events(plan_http, monkeypatch, approved):
    http = plan_http
    tasks = settlement_prepare_475(http, monkeypatch, 0)

    def input_events():
        response = http.client.get(f'/api/projects/{http.project}/agent-nodes/actor/events', params={'host': 'pi'})
        assert response.status_code == 200, response.text
        return [deepcopy(record) for record in response.json()['events'] if record['event']['type'] in (
            'user_prompt', 'queued_input', 'system_continuation')]

    original_inputs = input_events()
    assert http.read()['apply_via'] == 'human_apply'
    with settlement_pending_475(http, tasks, human=True) as (event, before):
        http.confirm(event, approved)
        drain(http.client, tasks)
        application = original_preview(http)['application']
        assert input_events() == original_inputs
        if approved:
            receipt = settlement_committed_475(http, before, applied_by='human')
            operation, = public_state(http)['ops']['operations']
            assert operation['turn_id'] is None
            assert_undone(http, receipt, before)
            settlement_error_475(http.apply(), 409, 'PLAN_ALREADY_APPLIED')
        else:
            assert application['state'] == 'ready' and application['receipt'] is None
            assert application['error']['code'] == 'PLAN_NOT_CONFIRMED'
            assert settlement_state_475(http) == before
        assert not http.session.confirm_handlers and input_events() == original_inputs
