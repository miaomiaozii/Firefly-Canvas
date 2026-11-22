"""Human apply uses real HTTP approval, Store CAS, persisted journal and undo (#484)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from queue import Queue
from threading import Barrier
from unittest.mock import AsyncMock

import pytest

# Routers bind their application module at import time.
import main
from app.routers import api_agent_tools as tools
from app.services.agent_ops_store import get_operation, list_operations
from firefly_opsdb import OpsDb
from tests.test_agent_plan_399 import env, submit, tool, upload, workbook


ORIGIN = 'http://127.0.0.1:3198'
STALE_HINT = '方案已过期，请让 Agent 重新导入'


def headers(project):
    return {'Origin': ORIGIN, 'X-Agent-Project': project}


def assert_human_apply_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()['detail']
    assert error['code'] == code, response.text
    if code == 'PLAN_STALE':
        assert error['hint'] == STALE_HINT
    return error


class ApplyHTTP:
    def __init__(self, env, *, mcp=False):
        self.client, self.store, self.host, (self.project, self.foreign) = env
        self.mcp = mcp
        self.source = upload(self.client, self.project, 'human-apply.xlsx', workbook(''))
        submit(self.client, self.project, self.source)
        self.draft = tool(self.client, self.project, 'plan.import_excel',
                          {'attachment_id': self.source['id']}, mcp=mcp)
        self.path = f'/api/projects/{self.project}/plan/previews/{self.draft["plan_id"]}'
        self.session = self.host.conversation_session(self.project, 'actor')
        self.events = Queue()

        async def observe(_session, event):
            if event['type'] == 'confirm' and event['tool'] == 'plan.apply':
                self.events.put(deepcopy(event))

        self.session.event_handlers.append(observe)

    def read(self):
        response = self.client.get(self.path, params={'agent_node_id': 'actor'}, headers=headers(self.project))
        assert response.status_code == 200, response.text
        return response.json()

    def edit(self, *ops):
        response = self.client.post(self.path + '/edits', params={'agent_node_id': 'actor'},
            headers=headers(self.project), json={'generation': self.draft['generation'], 'ops': list(ops)})
        assert response.status_code == 200, response.text
        self.draft = response.json()
        return self.draft

    def select(self, *indexes):
        return self.edit(*[{'task_id': self.draft['preview']['tasks'][index]['task_id'], 'select': True}
                           for index in indexes])

    def apply(self, **kwargs):
        kwargs.setdefault('params', {'agent_node_id': 'actor'})
        kwargs.setdefault('headers', headers(self.project))
        if not {'json', 'content'} & kwargs.keys():
            kwargs['json'] = {'generation': self.draft['generation']}
        return self.client.post(self.path + '/apply', **kwargs)

    def confirm(self, event, approved=True):
        response = self.client.post(f'/api/projects/{self.project}/agent-nodes/actor/confirm',
            headers=headers(self.project), json={'confirm_id': event['confirm_id'], 'approved': approved})
        assert response.status_code == 200, response.text
        assert response.json()['approved'] is approved

    def state(self):
        return {project: {'canvas': self.store.canvas(project),
            'disk': (self.store.root / project / 'canvas.json').read_bytes(),
            'media': self.store.list_media(project),
            'ops': list_operations(OpsDb(self.store.root), project, limit=100)}
            for project in (self.project, self.foreign)}

    def decide(self, *, approved=True, before_confirm=None):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.apply)
            event = None
            try:
                event = self.events.get(timeout=10)
                if before_confirm:
                    before_confirm(event)
                self.confirm(event, approved)
                return future.result(timeout=10), event
            finally:
                if event is not None and not future.done():
                    self.confirm(event, False)


@pytest.fixture
def apply_http(env):
    return ApplyHTTP(env)


def track_lifetime(monkeypatch):
    """Observe real detached execution; awaiting tasks is the settlement barrier."""
    tasks = []
    normal = tools._track_lifetime_task

    def track(task):
        tasks.append(task)
        return normal(task)

    monkeypatch.setattr(tools, '_track_lifetime_task', track)
    return tasks


def drain(client, tasks):
    async def settled():
        await asyncio.wait_for(asyncio.gather(*list(tasks), return_exceptions=True), timeout=10)
    client.portal.call(settled)


def no_agent_delivery(monkeypatch, manager):
    spies = []
    for method in ('send_prompt', 'send_system_continuation', 'spawn_session', 'respawn_session'):
        spy = AsyncMock(wraps=getattr(manager, method))
        monkeypatch.setattr(manager, method, spy)
        spies.append(spy)
    return spies


@pytest.mark.parametrize('mcp', [False, True], ids=['REST', 'external-SID'])
def test_human_selected_apply_has_original_owner_one_revision_receipt_and_real_undo(env, monkeypatch, mcp):
    http = ApplyHTTP(env, mcp=mcp)
    initial = http.draft
    assert initial['apply_via'] == 'human_apply' and initial['agent_node_id'] == 'actor'
    assert initial['application'] == {'state': 'ready', 'generation': 1, 'receipt': None}
    http.select(0, -1)
    wanted = [task['task_id'] for task in http.draft['preview']['tasks'] if task['selected']]
    before = http.state()
    spies = no_agent_delivery(monkeypatch, http.host)
    inputs = deepcopy(http.host.load_events(http.project, 'actor', host='pi')['events'])

    def waiting(event):
        assert [task['task_id'] for task in event['params']['tasks']] == wanted
        assert event['params']['generation'] == http.draft['generation']
        assert http.state() == before
        application = http.read()['application']
        assert application['state'] == 'awaiting_confirmation' and application['receipt'] is None
        assert application['confirm_id'] == event['confirm_id']

    response, event = http.decide(before_confirm=waiting)
    assert response.status_code == 200, response.text
    applied = response.json()
    application = applied['application']
    receipt = application['receipt']
    assert application['state'] == 'applied' and application['generation'] == http.draft['generation']
    assert receipt['generation'] == http.draft['generation'] and receipt['applied_by'] == 'human'
    result = receipt['result']
    assert [task['task_id'] for task in result['tasks']] == wanted
    assert [group['section'] for group in result['groups']] == ['主图方案', '高级A+方案']
    assert result['already_applied'] == [] and result['op_id']
    canvas = http.store.canvas(http.project)
    disk = json.loads((http.store.root / http.project / 'canvas.json').read_text(encoding='utf-8'))
    assert canvas['revision'] == disk['revision'] == before[http.project]['canvas']['revision'] + 1
    assert canvas['nodes'] == disk['nodes'] and canvas['connections'] == disk['connections']
    created = [node for node in canvas['nodes'] if node['id'] not in {'actor', 'other'}]
    assert {node['id'] for node in created} == set(result['touched_node_ids'])
    assert all(node['createdBy'] == 'agent' and node['agentNodeId'] == 'actor' for node in created)
    assert {node['excel_task_id'] for node in created if node.get('planLineage')} == set(wanted)
    assert all(not task['ref_nodes'] for task in result['tasks'])
    assert http.store.list_media(http.project) == []
    records = list_operations(OpsDb(http.store.root), http.project)
    assert len(records) == 1
    record = get_operation(OpsDb(http.store.root), http.project, result['op_id'])
    assert (record['tool'], record['agent_node_id'], record['turn_id']) == ('plan.apply', 'actor', None)
    assert record['touched_node_ids'] == result['touched_node_ids'] and record['before'] and record['after']
    assert http.read() == applied
    assert tool(http.client, http.project, 'plan.import_excel',
                {'attachment_id': http.source['id']}, mcp=mcp) == applied
    denied = tool(http.client, http.project, 'plan.apply',
                  {'plan_id': applied['plan_id'], 'generation': applied['generation']}, mcp=mcp)
    assert denied['code'] == 'PLAN_ALREADY_APPLIED', denied
    duplicate = assert_human_apply_error(http.apply(), 409, 'PLAN_ALREADY_APPLIED')
    assert duplicate['retryable'] is False
    assert duplicate['details']['plan_id'] == applied['plan_id']
    assert duplicate['details']['generation'] == applied['generation']
    assert duplicate['details']['receipt'] == receipt
    assert http.events.empty() and not http.session.confirm_handlers
    for spy in spies:
        spy.assert_not_awaited()
    events = http.host.load_events(http.project, 'actor', host='pi')['events']
    assert [r for r in events if r['event']['type'] in ('user_prompt', 'queued_input', 'system_continuation')] == [
        r for r in inputs if r['event']['type'] in ('user_prompt', 'queued_input', 'system_continuation')]
    undone = http.client.post(f'/api/projects/{http.project}/ops/undo', headers=headers(http.project),
                             json={'op_id': result['op_id']})
    assert undone.status_code == 200 and undone.json()['reverted'] == [result['op_id']], undone.text
    assert http.store.canvas(http.project)['nodes'] == before[http.project]['canvas']['nodes']
    assert http.store.canvas(http.project)['connections'] == before[http.project]['canvas']['connections']
    after_undo = http.state()
    assert_human_apply_error(http.apply(), 409, 'PLAN_ALREADY_APPLIED')
    assert http.read()['application']['receipt'] == receipt and http.state() == after_undo
    assert event['confirm_id']


def test_empty_human_selection_is_invalid_but_agent_empty_noop_is_unchanged(apply_http):
    http = apply_http
    before = http.state()
    assert_human_apply_error(http.apply(), 400, 'INVALID_ARGUMENT')
    assert http.read()['application'] == {'state': 'ready', 'generation': 1, 'receipt': None}
    result = tool(http.client, http.project, 'plan.apply', {'plan_id': http.draft['plan_id'], 'generation': 1})
    assert result['tasks'] == result['groups'] == result['already_applied'] == [] and result['op_id'] is None
    assert http.state() == before and http.events.empty()


def test_new_generation_preserves_receipt_all_existing_is_not_a_fictitious_application(apply_http):
    http = apply_http
    http.select(0)
    response, _ = http.decide()
    assert response.status_code == 200, response.text
    applied = response.json()
    receipt = applied['application']['receipt']
    first, second = [task['task_id'] for task in applied['preview']['tasks'][:2]]
    http.edit({'task_id': first, 'rename': 'Explicit new generation'})
    assert http.draft['application'] == {'state': 'ready', 'generation': 3, 'receipt': receipt}
    before = http.state()
    assert_human_apply_error(http.apply(json={'generation': 2}), 409, 'PLAN_STALE')
    response = http.apply()
    assert response.status_code == 200, response.text
    assert response.json()['no_changes'] is True
    assert response.json()['result']['already_applied'] == [first]
    assert response.json()['result']['tasks'] == [] and response.json()['result']['op_id'] is None
    assert response.json()['application'] == http.draft['application']
    assert http.state() == before and http.events.empty()
    http.edit({'task_id': second, 'select': True})
    response, _ = http.decide()
    assert response.status_code == 200, response.text
    result = response.json()['application']['receipt']['result']
    assert [task['task_id'] for task in result['tasks']] == [second]
    assert result['already_applied'] == [first]
    assert response.json()['application']['receipt']['generation'] == 4
    assert receipt['generation'] == 2


def test_agent_commit_consumes_human_apply_even_after_created_nodes_are_deleted(apply_http):
    http = apply_http
    http.select(0)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(tool, http.client, http.project, 'plan.apply',
                                 {'plan_id': http.draft['plan_id'], 'generation': 2})
        event = http.events.get(timeout=10)
        http.confirm(event)
        result = future.result(timeout=10)
    assert result['op_id'], result
    receipt = http.read()['application']['receipt']
    assert receipt == {'generation': 2, 'applied_by': 'agent', 'result': result}
    http.store.apply_mutations(http.project, [{'op': 'remove_node', 'node_id': ident}
        for ident in result['touched_node_ids']], base_revision=http.store.canvas(http.project)['revision'])
    http.store.flush(http.project)
    before = http.state()
    assert_human_apply_error(http.apply(), 409, 'PLAN_ALREADY_APPLIED')
    assert http.state() == before and http.read()['application']['receipt'] == receipt


@pytest.mark.parametrize('approved', [True, False], ids=['late-approve', 'late-reject'])
def test_real_pending_window_competitors_and_late_decision_settle_once(apply_http, monkeypatch, approved):
    http = apply_http
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    spies = no_agent_delivery(monkeypatch, http.host)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', 0)
    before = http.state()
    pending = http.apply()
    event = http.events.get(timeout=10)
    try:
        assert pending.status_code == 200 and pending.json()['code'] == 'CONFIRM_PENDING', pending.text
        assert pending.json()['status'] == 'awaiting_confirmation'
        assert pending.json()['confirm_id'] == event['confirm_id']
        application = http.read()['application']
        assert application == {'state': 'awaiting_confirmation', 'generation': 2,
                               'receipt': None, 'confirm_id': event['confirm_id']}
        assert_human_apply_error(http.apply(), 409, 'CONFLICT')
        denied = tool(http.client, http.project, 'plan.apply', {'plan_id': http.draft['plan_id'], 'generation': 2})
        assert denied['code'] == 'CONFLICT', denied
        assert http.events.empty() and len(http.session.confirm_handlers) == 1
        assert http.state() == before
        http.confirm(event, approved)
        drain(http.client, tasks)
        final = http.read()['application']
        assert final['state'] == ('applied' if approved else 'ready')
        assert (final['receipt'] is not None) is approved
        assert not http.session.confirm_handlers
        settled = http.state()
        http.confirm(event, approved)
        drain(http.client, tasks)
        assert http.state() == settled
        if approved:
            assert len(settled[http.project]['ops']) == 1
            assert_human_apply_error(http.apply(), 409, 'PLAN_ALREADY_APPLIED')
        else:
            assert settled == before
        for spy in spies:
            spy.assert_not_awaited()
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


def test_simultaneous_human_requests_reserve_one_confirmation_and_commit_once(apply_http, monkeypatch):
    http = apply_http
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', 0)
    start = Barrier(3)
    before = http.state()

    def compete():
        start.wait(timeout=10)
        return http.apply()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(compete) for _ in range(2)]
        start.wait(timeout=10)
        responses = [future.result(timeout=10) for future in futures]
    event = http.events.get(timeout=10)
    try:
        assert sorted(response.status_code for response in responses) == [200, 409]
        pending = next(response for response in responses if response.status_code == 200)
        assert pending.json()['code'] == 'CONFIRM_PENDING' and pending.json()['confirm_id'] == event['confirm_id']
        assert_human_apply_error(next(response for response in responses if response.status_code == 409), 409, 'CONFLICT')
        assert http.events.empty() and len(http.session.confirm_handlers) == 1
        assert http.state() == before
        http.confirm(event)
        drain(http.client, tasks)
        assert http.read()['application']['state'] == 'applied'
        assert len(list_operations(OpsDb(http.store.root), http.project)) == 1
        assert http.store.canvas(http.project)['revision'] == before[http.project]['canvas']['revision'] + 1
    finally:
        http.confirm(event, False)
        drain(http.client, tasks)


@pytest.mark.parametrize('change', ['edit', 'revision', 'attachment'])
def test_changes_while_real_confirmation_waits_reject_old_cas_without_plan_writes(apply_http, change):
    http = apply_http
    http.select(0)
    before = http.state()

    def invalidate(_event):
        if change == 'edit':
            http.edit({'task_id': http.draft['preview']['tasks'][0]['task_id'], 'select': False})
        elif change == 'revision':
            http.store.apply_mutations(http.project, [{'op': 'upsert_node', 'node': {
                'id': 'human-edit', 'type': 'smart-prompt', 'text': 'Keep human content'}}],
                base_revision=http.store.canvas(http.project)['revision'])
            http.store.flush(http.project)
        else:
            Path(http.source['path']).write_bytes(b'x' * http.source['size'])

    response, _ = http.decide(before_confirm=invalidate)
    assert_human_apply_error(response, 409, 'PLAN_STALE')
    assert http.read()['application']['state'] == 'ready'
    assert http.read()['application']['receipt'] is None and not http.session.confirm_handlers
    assert list_operations(OpsDb(http.store.root), http.project) == []
    assert http.store.list_media(http.project) == []
    assert not any(node.get('planLineage') for node in http.store.canvas(http.project)['nodes'])
    if change != 'revision':
        assert http.state() == before
    else:
        assert http.store.find_canvas_node(http.project, 'human-edit')['text'] == 'Keep human content'
        assert http.store.canvas(http.project)['revision'] == before[http.project]['canvas']['revision'] + 1


@pytest.mark.parametrize('ending', ['stop', 'cancel'])
def test_pending_stop_or_cancellation_cleans_reservation_without_continuation(apply_http, monkeypatch, ending):
    http = apply_http
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    spies = no_agent_delivery(monkeypatch, http.host)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', 0)
    before = http.state()
    pending = http.apply()
    event = http.events.get(timeout=10)
    assert pending.status_code == 200 and pending.json()['code'] == 'CONFIRM_PENDING', pending.text
    if ending == 'stop':
        http.client.portal.call(http.host.stop_session, http.session)
    else:
        async def cancel():
            # Cancel the actual resumable execution, not a substitute mutation coroutine.
            execution = next(task for task in tasks if 'resumable.<locals>.wrapped.<locals>.execute' in
                             task.get_coro().__qualname__)
            execution.cancel()
        http.client.portal.call(cancel)
    drain(http.client, tasks)
    assert not http.session.confirm_handlers
    assert http.state() == before
    application = http.read()['application']
    assert application['state'] == 'ready' and application['receipt'] is None
    http.confirm(event)
    assert http.state() == before
    for spy in spies:
        spy.assert_not_awaited()
    if ending == 'cancel':
        response, retried = http.decide(approved=False)
        assert response.status_code == 200 and response.json()['code'] == 'CONFIRM_PENDING', response.text
        assert retried['confirm_id'] != event['confirm_id']
        drain(http.client, tasks)
        assert http.read()['application']['state'] == 'ready' and http.state() == before


@pytest.mark.parametrize('change', ['revision', 'attachment', 'branch'])
def test_pending_invalidation_cleans_waiter_without_a_user_decision(apply_http, monkeypatch, change):
    http = apply_http
    http.select(0)
    tasks = track_lifetime(monkeypatch)
    monkeypatch.setattr(tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', 0)
    pending = http.apply()
    event = http.events.get(timeout=10)
    assert pending.status_code == 200 and pending.json()['code'] == 'CONFIRM_PENDING', pending.text
    if change == 'revision':
        http.store.apply_mutations(http.project, [{'op': 'upsert_node', 'node': {
            'id': 'independent-human-change', 'type': 'smart-prompt', 'text': 'Keep this'}}],
            base_revision=http.store.canvas(http.project)['revision'])
        http.store.flush(http.project)
    elif change == 'attachment':
        Path(http.source['path']).write_bytes(b'x' * http.source['size'])
    else:
        http.session.session_file = 'invalidated-pending-484'
    before = http.state()
    drain(http.client, tasks)
    application = http.read()['application']
    assert application['state'] == 'ready' and application['receipt'] is None
    assert application['error']['code'] == 'PLAN_STALE' and application['error']['retryable'] is False
    assert not http.session.confirm_handlers and http.state() == before
    records = http.host.load_events(http.project, 'actor', host='pi')['events']
    assert any(record['event']['type'] == 'confirm_resolved' and
               record['event']['confirm_id'] == event['confirm_id'] and
               record['event']['approved'] is False for record in records)
    http.confirm(event)
    assert http.state() == before and not any(node.get('planLineage') for node in http.store.canvas(http.project)['nodes'])


def test_failure_before_canvas_replace_never_advertises_applied(apply_http, monkeypatch):
    import firefly_json_io

    http = apply_http
    http.select(0)
    before = http.state()
    target = http.store.root / http.project / 'canvas.json'
    normal = firefly_json_io._replace_staged

    def fail(staging, path):
        if path == target:
            raise OSError('synthetic pre-replace disk failure')
        return normal(staging, path)

    with monkeypatch.context() as patch:
        patch.setattr(firefly_json_io, '_replace_staged', fail)
        response, _ = http.decide()
        assert response.is_error, response.text
        assert http.read()['application']['state'] != 'applied'
        assert http.read()['application']['receipt'] is None
        assert target.read_bytes() == before[http.project]['disk']
        assert list_operations(OpsDb(http.store.root), http.project) == []


def test_late_flush_preserves_settled_error_before_listener_removal(apply_http, monkeypatch):
    from app.services import agent_ops_store

    http = apply_http
    http.select(0)
    remove = http.store.remove_persisted_canvas_listener
    flushed = []

    def broken_append(*args, **kwargs):
        raise OSError('synthetic sidecar append failure before late flush')

    def remove_after_flush(listener):
        if listener.__name__ == 'persisted':
            flushed.append(http.store.flush(http.project)['revision'])
        return remove(listener)

    with monkeypatch.context() as patch:
        patch.setattr(agent_ops_store, 'append_operation', broken_append)
        patch.setattr(http.store, 'remove_persisted_canvas_listener', remove_after_flush)
        response, _ = http.decide()
    assert response.is_error and flushed, response.text
    application = http.read()['application']
    assert application['state'] == 'applied'
    assert application['error'] == response.json()['detail']
    assert application['error']['details']['journal_status'] == 'append_failed'
    assert application['error']['retryable'] is False
    assert application['receipt']['result']['op_id']
    assert_human_apply_error(http.apply(), 409, 'PLAN_ALREADY_APPLIED')


def test_unobserved_replace_is_unknown_and_never_advertises_safe_retry(apply_http, monkeypatch):
    import firefly_json_io

    http = apply_http
    http.select(0)
    target = http.store.root / http.project / 'canvas.json'
    revision = http.store.canvas(http.project)['revision']
    normal = firefly_json_io._replace_staged

    def replace_without_ack(staging, path):
        normal(staging, path)
        if path == target:
            # The durable write happened, but neither persistence listener saw its acknowledgement.
            raise OSError('synthetic lost replacement acknowledgement')

    with monkeypatch.context() as patch:
        patch.setattr(firefly_json_io, '_replace_staged', replace_without_ack)
        response, _ = http.decide()
        assert response.is_error, response.text
        application = http.read()['application']
        assert application['state'] == 'unknown' and application['receipt'] is None
        assert response.json()['detail']['retryable'] is False
        assert json.loads(target.read_text(encoding='utf-8'))['revision'] == revision + 1
        before = http.state()
        error = assert_human_apply_error(http.apply(), 409, 'CONFLICT')
        assert error['retryable'] is False
        assert http.state() == before and http.events.empty()


@pytest.mark.parametrize('failure', ['end-stream', 'append-failed', 'append-and-end'])
def test_persisted_commit_retains_true_receipt_despite_delivery_or_sidecar_failure(apply_http, monkeypatch, failure):
    from app.services import agent_ops_store

    http = apply_http
    http.select(0)
    before = http.state()
    broadcast = tools._broadcast_canvas_updated

    async def broken_end(*args, **kwargs):
        if kwargs.get('phase') == 'end':
            raise OSError('synthetic end-stream failure')
        return await broadcast(*args, **kwargs)

    def broken_append(*args, **kwargs):
        raise OSError('synthetic sidecar append failure')

    with monkeypatch.context() as patch:
        if failure in ('end-stream', 'append-and-end'):
            patch.setattr(tools, '_broadcast_canvas_updated', broken_end)
        if failure in ('append-failed', 'append-and-end'):
            patch.setattr(agent_ops_store, 'append_operation', broken_append)
        response, _ = http.decide()
        assert response.is_error, response.text
        application = http.read()['application']
        assert application['state'] == 'applied'
        assert application['error'] == response.json()['detail']
        assert application['error']['retryable'] is False
        assert '不要重复' in application['error']['hint']
        if failure in ('append-failed', 'append-and-end'):
            assert application['error']['details']['journal_status'] == 'append_failed'
        receipt = application['receipt']
        assert receipt['generation'] == 2 and receipt['applied_by'] == 'human'
        result = receipt['result']
        assert result['op_id'] and result['tasks'] and result['touched_node_ids']
        disk = json.loads((http.store.root / http.project / 'canvas.json').read_text(encoding='utf-8'))
        assert disk['revision'] == before[http.project]['canvas']['revision'] + 1
        assert set(result['touched_node_ids']) <= {node['id'] for node in disk['nodes']}
    assert_human_apply_error(http.apply(), 409, 'PLAN_ALREADY_APPLIED')
    assert http.read()['application']['receipt'] == receipt
    if failure in ('append-failed', 'append-and-end'):
        assert get_operation(OpsDb(http.store.root), http.project, result['op_id']) is None
        journal = tools._journal_for(http.store)
        assert journal.result(http.project, result['op_id'])['status'] == 'append_failed'
        before_undo = http.state()
        undo = http.client.post(f'/api/projects/{http.project}/ops/undo', headers=headers(http.project),
                                json={'op_id': result['op_id']})
        assert_human_apply_error(undo, 409, 'UNDO_CONFLICT')
        assert http.state() == before_undo
    else:
        assert get_operation(OpsDb(http.store.root), http.project, result['op_id'])['tool'] == 'plan.apply'
        undo = http.client.post(f'/api/projects/{http.project}/ops/undo', headers=headers(http.project),
                                json={'op_id': result['op_id']})
        assert undo.status_code == 200 and undo.json()['reverted'] == [result['op_id']], undo.text
        assert http.store.canvas(http.project)['nodes'] == before[http.project]['canvas']['nodes']
        assert http.store.canvas(http.project)['connections'] == before[http.project]['canvas']['connections']
    assert http.read()['application']['receipt'] == receipt
    assert_human_apply_error(http.apply(), 409, 'PLAN_ALREADY_APPLIED')
