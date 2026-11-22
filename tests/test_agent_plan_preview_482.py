"""Human preview editing never changes the importing Agent's ownership or approval path."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.test_agent_plan_399 import env, submit, tool, upload, workbook


ORIGIN = 'http://127.0.0.1:3198'


def human_headers(project):
    return {'Origin': ORIGIN, 'X-Agent-Project': project}


def preview_path(project, plan_id):
    return f'/api/projects/{project}/plan/previews/{plan_id}'


@pytest.fixture
def live_pipe(env):
    attached = []

    def attach(session):
        attached.append((session, session.process))
        session.process = SimpleNamespace(poll=lambda: None)

    yield attach
    for session, process in reversed(attached):
        session.process = process


def import_preview(client, project, *, mcp=True):
    source = upload(client, project, 'plan.xlsx', workbook(''))
    submit(client, project, source)
    return tool(client, project, 'plan.import_excel', {'attachment_id': source['id']}, mcp=mcp)


@pytest.mark.parametrize('mcp', [False, True])
def test_plan_presentation_is_reference_only_and_replays_through_real_event_log(env, mcp):
    client, store, host, (project, _) = env
    draft = import_preview(client, project, mcp=mcp)
    session = host.conversation_session(project, 'actor')
    live = []
    session.event_handlers.append(lambda _session, event: live.append(deepcopy(event)))
    # Only process liveness is supplied; turn, scope, publication and replay stay real.
    session.process = SimpleNamespace(poll=lambda: None)
    before = store.canvas(project)
    try:
        result = tool(client, project, 'agent.present', {
            'action': 'show', 'kind': 'plan', 'title': 'Review imported plan',
            'plan_id': draft['plan_id'], 'generation': 1,
        }, mcp=mcp)
        assert 'presentation' in result, result
        presentation = result['presentation']
        assert presentation['kind'] == 'plan' and presentation['state'] == 'presented'
        assert presentation['data'] == {'source': 'plan_preview', 'plan_id': draft['plan_id'], 'generation': 1}
        published = [event for event in live if event['type'] == 'plan']
        assert len(published) == 1 and published[0]['presentation'] == presentation
        for suffix in ('events', 'events/page'):
            replay = client.get(f'/api/projects/{project}/agent-nodes/actor/{suffix}', params={'host': 'pi'})
            assert replay.status_code == 200, replay.text
            plans = [record['event'] for record in replay.json()['events'] if record['event']['type'] == 'plan']
            assert plans == published
        read = client.get(preview_path(project, draft['plan_id']),
                          params={'agent_node_id': 'actor'}, headers=human_headers(project))
        assert read.json() == draft
        assert store.canvas(project) == before and store.list_media(project) == []
    finally:
        session.process = None


@pytest.mark.parametrize('mcp', [False, True])
@pytest.mark.parametrize('decision', ['approve', 'reject', 'edit-during-confirmation'])
def test_original_agent_uses_human_selection_only_after_real_input_confirmation_and_cas(env, live_pipe, mcp, decision):
    client, store, host, (project, _) = env
    draft = import_preview(client, project, mcp=mcp)
    first = draft['preview']['tasks'][0]['task_id']
    path = preview_path(project, draft['plan_id'])
    before = store.canvas(project)
    edited = client.post(path + '/edits', params={'agent_node_id': 'actor'}, headers=human_headers(project),
                         json={'generation': 1, 'ops': [{'task_id': first, 'select': True}]})
    assert edited.status_code == 200, edited.text
    session = host.conversation_session(project, 'actor')
    # A persistent Pi process must not be mistaken for a stopped, unbound runtime.
    live_pipe(session)
    client.portal.call(host._dispatch_event, session, {'type': 'agent_end'})
    requested = client.post(f'/api/projects/{project}/agent-nodes/actor/prompt', json={
        'message': f"Apply current selection for {draft['plan_id']} generation 2",
        'request_id': 'human-apply-plan',
    })
    assert requested.status_code == 200, requested.text
    records = host.load_events(project, 'actor', host='pi')['events']
    assert any(record['event'].get('request_id') == 'human-apply-plan'
               and record['event']['type'] == 'user_prompt' for record in records)
    latest = tool(client, project, 'plan.import_excel', {
        'attachment_id': draft['preview']['source']['attachment_id'],
    }, mcp=mcp)
    assert latest == edited.json()
    assert store.canvas(project) == before and store.list_media(project) == []
    seen = []

    async def human_confirmation(_session, event):
        if event['type'] != 'confirm':
            return
        seen.append(deepcopy(event))
        if decision == 'edit-during-confirmation':
            changed = await asyncio.to_thread(client.post, path + '/edits',
                params={'agent_node_id': 'actor'}, headers=human_headers(project),
                json={'generation': 2, 'ops': [{'task_id': first, 'select': False}]})
            assert changed.status_code == 200, changed.text
        answered = await asyncio.to_thread(client.post,
            f'/api/projects/{project}/agent-nodes/actor/confirm', headers=human_headers(project),
            json={'confirm_id': event['confirm_id'], 'approved': decision != 'reject'})
        assert answered.status_code == 200, answered.text
        assert answered.json()['approved'] is (decision != 'reject')

    session.event_handlers.append(human_confirmation)
    applied = tool(client, project, 'plan.apply', {'plan_id': draft['plan_id'], 'generation': 2}, mcp=mcp)
    assert len(seen) == 1 and seen[0]['params']['generation'] == 2
    assert [task['task_id'] for task in seen[0]['params']['tasks']] == [first]
    if decision != 'approve':
        assert applied['code'] == ('PLAN_NOT_CONFIRMED' if decision == 'reject' else 'PLAN_STALE'), applied
        assert store.canvas(project) == before and store.list_media(project) == []
        return
    assert [task['task_id'] for task in applied['tasks']] == [first]
    assert store.canvas(project)['revision'] == before['revision'] + 1
    assert store.list_media(project) == []
    undone = client.post(f'/api/projects/{project}/ops/undo', json={'op_id': applied['op_id']},
                          headers={'X-Agent-Project': project})
    assert undone.status_code == 200, undone.text
    assert undone.json()['reverted'] == [applied['op_id']]
    assert store.canvas(project)['nodes'] == before['nodes']
    assert store.canvas(project)['connections'] == before['connections']


def test_human_read_fetches_external_mcp_preview_without_canvas_or_media_writes(env):
    client, store, _, (project, _) = env
    draft = import_preview(client, project)
    before = store.canvas(project)
    response = client.get(preview_path(project, draft['plan_id']),
                          params={'agent_node_id': 'actor'}, headers=human_headers(project))
    assert response.status_code == 200, response.text
    assert response.json() == draft
    assert store.canvas(project) == before
    assert store.list_media(project) == []


def test_human_select_returns_new_generation_and_leaves_original_draft_unchanged(env):
    client, store, _, (project, _) = env
    draft = import_preview(client, project)
    first = draft['preview']['tasks'][0]['task_id']
    before = store.canvas(project)
    path = preview_path(project, draft['plan_id'])
    response = client.post(path + '/edits', params={'agent_node_id': 'actor'},
                           headers=human_headers(project), json={
                               'generation': 1, 'ops': [{'task_id': first, 'select': True}],
                           })
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited['plan_id'] == draft['plan_id'] and edited['generation'] == 2
    assert edited['preview']['tasks'][0]['selected'] is True
    assert draft['preview']['tasks'][0]['selected'] is False
    latest = client.get(path, params={'agent_node_id': 'actor'}, headers=human_headers(project))
    assert latest.json() == edited
    assert store.canvas(project) == before and store.list_media(project) == []


def test_human_read_and_write_reject_stale_generation_without_changing_preview(env):
    client, _, _, (project, _) = env
    draft = import_preview(client, project)
    path = preview_path(project, draft['plan_id'])
    first = draft['preview']['tasks'][0]['task_id']
    edit = {'generation': 1, 'ops': [{'task_id': first, 'select': True}]}
    saved = client.post(path + '/edits', params={'agent_node_id': 'actor'},
                        headers=human_headers(project), json=edit)
    assert saved.status_code == 200, saved.text
    old = client.get(path, params={'agent_node_id': 'actor', 'generation': 1},
                     headers=human_headers(project))
    assert old.status_code == 409, old.text
    assert old.json()['detail']['code'] == 'PLAN_STALE'
    repeated = client.post(path + '/edits', params={'agent_node_id': 'actor'},
                           headers=human_headers(project), json=edit)
    assert repeated.status_code == 409 and repeated.json()['detail']['code'] == 'PLAN_STALE'
    current = client.get(path, params={'agent_node_id': 'actor', 'generation': 2},
                         headers=human_headers(project))
    assert current.status_code == 200 and current.json() == saved.json()


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('field', ['caller', 'binding', 'source'])
def test_human_interfaces_reject_caller_binding_or_source_in_query(env, method, field):
    client, _, _, (project, _) = env
    draft = import_preview(client, project)
    path = preview_path(project, draft['plan_id'])
    body = {'generation': 1, 'ops': [{'task_id': draft['preview']['tasks'][0]['task_id'], 'select': True}]}
    response = client.request(method, path + ('/edits' if method == 'POST' else ''),
                              params={'agent_node_id': 'actor', field: 'human'},
                              headers=human_headers(project), **({'json': body} if method == 'POST' else {}))
    assert response.status_code == 400, response.text
    assert response.json()['detail']['code'] == 'INVALID_ARGUMENT'
    current = client.get(path, params={'agent_node_id': 'actor'}, headers=human_headers(project))
    assert current.json() == draft
