"""Real REST/MCP admission with offline native pipes; no native/model claims (#484)."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from queue import Queue
from uuid import uuid4

import pytest

from tests.test_agent_plan_399 import upload, workbook
from tests.test_agent_plan_apply_484 import assert_human_apply_error, headers, no_agent_delivery
from tests.test_agent_plan_present_binding_482 import binding_http
from app.services.agent_ops_store import get_operation, list_operations
from firefly_opsdb import OpsDb


@pytest.fixture
def native_http(binding_http, tmp_path, monkeypatch):
    home = tmp_path / 'owned-codex-home'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    return binding_http


def bind_thread(http, session, monkeypatch, thread=None):
    """A real persisted input-binding event, not a polling deadline, is the barrier."""
    thread = thread or str(uuid4())
    bound = Queue()
    persist = http.manager._persist_event

    def observe(owner, event, **kwargs):
        result = persist(owner, event, **kwargs)
        if owner is session and event['type'] == 'input_branch_bound' and result:
            bound.put(deepcopy(event))
        return result

    with monkeypatch.context() as patch:
        patch.setattr(http.manager, '_persist_event', observe)
        session.process.emit({'type': 'thread.started', 'thread_id': thread})
        bound.get(timeout=10)
    assert session.input_branch == thread
    records = http.records(http.project, session.node_id)
    record = next(record for record in reversed(records) if record.get('input_binding'))
    assert record['input_branch'] == thread
    return record


def imported(http, transport, actor, monkeypatch):
    source = upload(http.client, http.project, 'binding-apply.xlsx', workbook(''), actor=actor)
    session = http.prime(actor=actor, source=source)
    if actor == 'codex-actor':
        bind_thread(http, session, monkeypatch)
    ok, draft = http.call('plan.import_excel', {'attachment_id': source['id']}, transport=transport, actor=actor)
    assert ok and draft['generation'] == 1, draft
    return source, draft, session


def human_apply(http, draft, actor, *, approved=True, before_confirm=None):
    events = Queue()
    session = http.manager.conversation_session(http.project, actor)

    async def observe(_session, event):
        if event['type'] == 'confirm' and event['tool'] == 'plan.apply':
            events.put(deepcopy(event))

    session.event_handlers.append(observe)
    path = f'/api/projects/{http.project}/plan/previews/{draft["plan_id"]}/apply'
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(http.client.post, path, params={'agent_node_id': actor},
                                 headers=headers(http.project), json={'generation': draft['generation']})
        event = None
        try:
            event = events.get(timeout=10)
            if before_confirm:
                before_confirm(event)
            confirm = http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/confirm',
                headers=headers(http.project), json={'confirm_id': event['confirm_id'], 'approved': approved})
            assert confirm.status_code == 200, confirm.text
            response = future.result(timeout=10)
            assert events.empty()
            return response, event
        finally:
            if event and not future.done():
                http.client.post(f'/api/projects/{http.project}/agent-nodes/{actor}/confirm',
                    headers=headers(http.project), json={'confirm_id': event['confirm_id'], 'approved': False})
            session.event_handlers.remove(observe)


def content_state(http):
    return {project: {'canvas': http.store.canvas(project), 'media': http.store.list_media(project),
        'disk': (http.store.root / project / 'canvas.json').read_bytes(),
        'ops': list_operations(OpsDb(http.store.root), project)} for project in (http.project, http.foreign)}


@pytest.mark.parametrize(('transport', 'actor'), [
    pytest.param('rest', 'actor', id='REST'),
    pytest.param('external', 'actor', id='external-original-SID'),
    pytest.param('hosted', 'actor', id='hosted-Pi'),
    pytest.param('hosted', 'codex-actor', id='hosted-Codex'),
])
def test_human_apply_never_sends_agent_input_or_continuation_and_original_channel_reads_receipt(
    native_http, monkeypatch, transport, actor,
):
    http = native_http
    source, draft, session = imported(http, transport, actor, monkeypatch)
    assert draft['apply_via'] == 'human_apply' and draft['agent_node_id'] == actor
    assert draft['application'] == {'state': 'ready', 'generation': 1, 'receipt': None}
    edited = http.human_merge(draft, actor=actor)
    wanted = edited['preview']['tasks'][0]['task_id']
    before = content_state(http)
    pipes = [process.stdin.getvalue() for process in http.processes]
    processes = list(http.processes)
    records = http.records(http.project, actor)
    spies = no_agent_delivery(monkeypatch, http.manager)

    def waiting(event):
        assert [task['task_id'] for task in event['params']['tasks']] == [wanted]
        pending = http.human_read(edited, actor=actor)['application']
        assert pending == {'state': 'awaiting_confirmation', 'generation': 2,
                           'receipt': None, 'confirm_id': event['confirm_id']}
        assert content_state(http) == before

    response, _ = human_apply(http, edited, actor, before_confirm=waiting)
    assert response.status_code == 200, response.text
    applied = response.json()
    receipt = applied['application']['receipt']
    assert applied['agent_node_id'] == actor and applied['apply_via'] == 'human_apply'
    assert applied['application']['state'] == 'applied' and applied['application']['generation'] == 2
    assert receipt['generation'] == 2 and receipt['applied_by'] == 'human'
    result = receipt['result']
    assert [task['task_id'] for task in result['tasks']] == [wanted]
    assert result['op_id'] and result['touched_node_ids'] and result['already_applied'] == []
    canvas = http.store.canvas(http.project)
    assert canvas['revision'] == before[http.project]['canvas']['revision'] + 1
    created = [node for node in canvas['nodes'] if node['id'] in result['touched_node_ids']]
    assert all(node['agentNodeId'] == actor and node['createdBy'] == 'agent' for node in created)
    prompt = http.store.find_canvas_node(http.project, result['tasks'][0]['prompt_node'])
    assert prompt['planLineage']['source_rows'] == ['图片方案!3', '图片方案!5']
    journal = list_operations(OpsDb(http.store.root), http.project)
    assert len(journal) == 1
    record = get_operation(OpsDb(http.store.root), http.project, result['op_id'])
    assert (record['tool'], record['agent_node_id'], record['turn_id']) == ('plan.apply', actor, None)
    assert http.store.list_media(http.project) == []
    assert list(http.processes) == processes and [process.stdin.getvalue() for process in http.processes] == pipes
    for spy in spies:
        spy.assert_not_awaited()
    after_records = http.records(http.project, actor)
    assert [record for record in after_records if record['event']['type'] in
            ('user_prompt', 'queued_input', 'system_continuation')] == [record for record in records
            if record['event']['type'] in ('user_prompt', 'queued_input', 'system_continuation')]
    assert not session.confirm_handlers
    ok, latest = http.call('plan.import_excel', {'attachment_id': source['id']}, transport=transport, actor=actor)
    assert ok and latest == applied, latest
    ok, error = http.call('plan.apply', {'plan_id': draft['plan_id'], 'generation': 2},
                         transport=transport, actor=actor)
    assert not ok and error['code'] == 'PLAN_ALREADY_APPLIED', error
    undo = http.client.post(f'/api/projects/{http.project}/ops/undo', headers=headers(http.project),
                            json={'op_id': result['op_id']})
    assert undo.status_code == 200 and undo.json()['reverted'] == [result['op_id']], undo.text
    assert http.store.canvas(http.project)['nodes'] == before[http.project]['canvas']['nodes']
    assert http.store.canvas(http.project)['connections'] == before[http.project]['canvas']['connections']


def test_cross_sid_apply_is_stale_not_authorized_by_a_human_receipt(native_http, monkeypatch):
    http = native_http
    _, draft, _ = imported(http, 'external', 'actor', monkeypatch)
    edited = http.human_merge(draft)
    response, _ = human_apply(http, edited, 'actor')
    assert response.status_code == 200, response.text
    before = content_state(http)
    pipes = [process.stdin.getvalue() for process in http.processes]
    ok, error = http.call('plan.apply', {'plan_id': draft['plan_id'], 'generation': 2},
                         transport='external', sid='another-SID-484')
    assert not ok and error['code'] == 'PLAN_STALE', error
    assert error['hint'] == '方案已过期，请让 Agent 重新导入'
    assert content_state(http) == before
    assert [process.stdin.getvalue() for process in http.processes] == pipes
    assert http.human_read(edited)['application']['receipt'] == response.json()['application']['receipt']


def test_codex_same_branch_resumed_turn_import_reads_the_human_receipt(native_http, monkeypatch):
    http = native_http
    source, draft, session = imported(http, 'hosted', 'codex-actor', monkeypatch)
    branch, epoch, generation = session.input_branch, session.input_epoch, session.generation
    edited = http.human_merge(draft, actor='codex-actor')
    response, _ = human_apply(http, edited, 'codex-actor')
    assert response.status_code == 200, response.text
    applied = response.json()
    http.finish(session)
    session = http.prime(actor='codex-actor')
    assert session.input_epoch == epoch and session.input_branch == branch and session.generation > generation
    process = session.process
    assert 'resume' in process.argv and process.argv[process.argv.index('resume') + 1] == branch
    ok, latest = http.call('plan.import_excel', {'attachment_id': source['id']},
                          transport='hosted', actor='codex-actor', sid='reconnected-hosted-484')
    assert ok and latest == applied, latest
    before = content_state(http)
    ok, error = http.call('plan.apply', {'plan_id': draft['plan_id'], 'generation': 2},
                         transport='hosted', actor='codex-actor')
    assert not ok and error['code'] == 'PLAN_ALREADY_APPLIED', error
    assert content_state(http) == before


@pytest.mark.parametrize('change', ['session-disappeared', 'host', 'branch', 'epoch'])
def test_human_does_not_revive_stale_original_session_binding(native_http, monkeypatch, change):
    http = native_http
    _, draft, session = imported(http, 'external', 'actor', monkeypatch)
    edited = http.human_merge(draft)
    key = http.manager._session_key(http.project, 'actor')
    if change == 'session-disappeared':
        assert http.manager.sessions.pop(key) is session
    elif change == 'host':
        session.agent_cli = 'codex'
    elif change == 'branch':
        session.session_file = 'changed-native-branch-484'
    else:
        session.input_epoch = 'changed-input-epoch-484'
    before = content_state(http)
    pipes = [process.stdin.getvalue() for process in http.processes]
    processes = list(http.processes)
    spies = no_agent_delivery(monkeypatch, http.manager)
    try:
        assert http.human_read(edited) == edited
        response = http.client.post(f'/api/projects/{http.project}/plan/previews/{draft["plan_id"]}/apply',
            params={'agent_node_id': 'actor'}, headers=headers(http.project), json={'generation': 2})
        assert_human_apply_error(response, 409, 'PLAN_STALE')
        assert content_state(http) == before
        assert list(http.processes) == processes and [process.stdin.getvalue() for process in http.processes] == pipes
        assert not session.confirm_handlers
        for spy in spies:
            spy.assert_not_awaited()
    finally:
        if change == 'session-disappeared':
            http.manager.sessions[key] = session
