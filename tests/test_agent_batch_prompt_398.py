"""Batch prompt approval and undo through both public tool transports."""
import json
import os
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.services.agent_host import AgentHostManager
from firefly_projects import ProjectStore


@pytest.fixture
def canvas():
    with TemporaryDirectory(dir=os.environ.get('FIREFLY_PROJECTS_ROOT')) as path:
        store = ProjectStore(Path(path), snapshot_delay=0)
        project = store.create_project('synthetic batch prompts')['id']
        tab = object()
        register_project_session(tab, project)
        store.apply_mutations(project, [
            {'op': 'upsert_node', 'node': node} for node in (
                {'id': 'actor', 'type': 'smart-agent', 'autonomy': 'autonomous'},
                {'id': 'a', 'type': 'smart-prompt', 'text': 'red apple', 'promptEditedByUser': True,
                 'runSettings': {'provider_id': 'fake'}},
                {'id': 'b', 'type': 'smart-prompt', 'text': 'blue berry',
                 'runSettings': {'provider_id': 'fake'}},
            )
        ], base_revision=0)
        host = AgentHostManager()
        try:
            with patch.object(main, 'FIREFLY_PROJECT_STORE', store), \
                 patch('app.services.agent_host.agent_host_manager', host), \
                 TestClient(main.app, base_url='http://127.0.0.1:3198') as client:
                yield client, store, project, host
        finally:
            unregister_project_session(tab)
            store.close()


def tool(client, project, args, *, mcp, name='batch_prompt_edit', actor='actor'):
    if mcp:
        response = client.post(f'/api/mcp?project={project}&node={actor}', json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': f'canvas.{name}', 'arguments': args},
        })
        result = response.json()['result']
        return result['structuredContent'] if result.get('isError') else json.loads(result['content'][0]['text'])
    response = client.post(f'/api/projects/{project}/canvas/{name}?agent_node_id={actor}',
                           headers={'X-Agent-Project': project}, json=args)
    return response.json()['detail'] if response.is_error else response.json()


def confirmation(client, host, project, approved, before_decision=None):
    seen = []

    async def observe(session, event):
        if event['type'] == 'confirm':
            seen.append(event)
            if before_decision:
                before_decision()
            for handler in list(session.confirm_handlers):
                await handler(event['confirm_id'], approved)

    async def subscribe():
        session = await host.get_or_create_session(project, 'actor')
        session.autonomy = 'autonomous'
        session.event_handlers.append(observe)

    client.portal.call(subscribe)
    return seen


@pytest.mark.parametrize('mcp', [False, True])
@pytest.mark.parametrize('approved', [False, True])
def test_batch_confirm_cancel_and_undo(canvas, mcp, approved):
    client, store, project, host = canvas
    before = store.canvas(project)
    seen = confirmation(client, host, project, approved)
    draft = tool(client, project, {'apply': False, 'edits': [
        {'node_id': 'a', 'find': 'red', 'replace': 'green'}, {'node_id': 'b', 'append': ' jam'},
    ]}, mcp=mcp)
    assert draft['items'] == [
        {'node_id': 'a', 'before': 'red apple', 'after': 'green apple', 'protected': True},
        {'node_id': 'b', 'before': 'blue berry', 'after': 'blue berry jam', 'protected': False},
    ]
    assert not seen and store.canvas(project) == before
    args = {'apply': True, 'diff_id': draft['diff_id']}
    result = tool(client, project, args, mcp=mcp)
    assert len(seen) == 1
    assert seen[0]['params'] == {'node_count': 2, 'user_edited_count': 1}
    assert seen[0]['target_node_ids'] == ['a', 'b']
    assert '2' in seen[0]['message'] and '1' in seen[0]['message']
    assert 'apple' not in seen[0]['message']
    assert tool(client, project, args, mcp=mcp)['code'] == 'DIFF_STALE'
    if not approved:
        assert result['code'] == 'CONFIRM_REJECTED' and '用户已取消' in result['message']
        assert store.canvas(project) == before
        return
    after = {node['id']: node for node in store.canvas(project)['nodes']}
    assert result['applied'] == ['a', 'b']
    assert after['a']['text'] == 'green apple' and after['a']['promptEditedByUser'] is True
    assert after['b']['text'] == 'blue berry jam'
    response = client.post(f'/api/projects/{project}/ops/undo', json={'op_id': result['op_id']},
                           headers={'X-Agent-Project': project})
    assert response.status_code == 200, response.text
    undone = response.json()
    assert undone['reverted'] == [result['op_id']], undone
    assert store.canvas(project)['nodes'] == before['nodes']


@pytest.mark.parametrize('during_confirmation', [False, True])
def test_stale_preview_never_partially_applies(canvas, during_confirmation):
    client, store, project, host = canvas
    edits = {'apply': False, 'edits': [{'node_id': 'b', 'append': ' jam'}, {'node_id': 'a', 'prompt': 'replacement'}]}
    diff = tool(client, project, edits, mcp=False)['diff_id']

    def user_edit():
        current = store.canvas(project)
        a = next(node for node in current['nodes'] if node['id'] == 'a')
        store.apply_mutations(project, [{'op': 'upsert_node', 'node': {**a, 'text': 'manual'}}],
                              base_revision=current['revision'])

    seen = confirmation(client, host, project, True, user_edit if during_confirmation else None)
    if not during_confirmation:
        user_edit()
    args = {'apply': True, 'diff_id': diff}
    assert tool(client, project, args, mcp=False, actor='wrong')['code'] == 'DIFF_STALE'
    expected = 'CONFIRM_STALE' if during_confirmation else 'DIFF_STALE'
    assert tool(client, project, args, mcp=False)['code'] == expected
    assert len(seen) == int(during_confirmation)
    nodes = {node['id']: node for node in store.canvas(project)['nodes']}
    assert nodes['a']['text'] == 'manual'
    assert nodes['b']['text'] == 'blue berry'


def test_pending_prompt_replay_targets_exclude_unchanged_nodes(canvas, monkeypatch):
    from app.routers import api_agent_tools
    client, store, project, host = canvas
    monkeypatch.setattr(api_agent_tools, 'AGENT_WRITE_CONFIRM_TIMEOUT_SECONDS', .01)
    draft = tool(client, project, {'apply': False, 'edits': [
        {'node_id': 'b', 'prompt': 'blue berry'}, {'node_id': 'a', 'append': ' detail'},
    ]}, mcp=False)
    receipt = tool(client, project, {'apply': True, 'diff_id': draft['diff_id']}, mcp=False)
    assert receipt['code'] == 'CONFIRM_PENDING'
    response = client.get(f'/api/projects/{project}/agent-nodes/actor/events')
    events = [record['event'] for record in response.json()['events']]
    pending = next(event for event in events if event['type'] == 'confirm')
    assert pending['confirm_id'] == receipt['confirm_id']
    assert pending['target_node_ids'] == ['a']
    assert not any(event['type'] == 'confirm_resolved' for event in events)
    assert store.find_canvas_node(project, 'a')['text'] == 'red apple'
    response = client.post(f'/api/projects/{project}/agent-nodes/actor/confirm',
                           json={'confirm_id': receipt['confirm_id'], 'approved': False})
    assert response.status_code == 200


def test_legacy_rest_requires_agent_project_header(canvas):
    client, store, project, host = canvas
    before = store.canvas(project)
    seen = confirmation(client, host, project, True)
    response = client.post(f'/api/projects/{project}/canvas/batch_edit?agent_node_id=actor',
                           json={'node_ids': ['a', 'b'], 'prompt': 'replacement'})
    assert response.status_code == 400, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'INVALID_ARGUMENT'
    assert 'X-Agent-Project' in detail['message']
    assert not seen and store.canvas(project) == before


@pytest.mark.parametrize('mcp', [False, True])
@pytest.mark.parametrize('approved', [False, True])
def test_legacy_batch_uses_same_confirmation_before_any_write_or_task(canvas, mcp, approved, monkeypatch):
    client, store, project, host = canvas
    from app.services.agent_confirmation import Origin
    from app.routers import api_agent_tools as routes
    completed = Queue()

    async def finished(origin, result):
        completed.put(result)

    monkeypatch.setattr(Origin, 'continue_with', finished)
    from tests.agent_image_task_fakes import stub_providers
    before = store.canvas(project)
    seen = confirmation(client, host, project, approved)
    fake_task = AsyncMock(return_value={'task_id': 'synthetic'})
    with patch.object(main, 'load_api_providers', return_value=stub_providers('fake', 'fake')), \
         patch.object(main, 'route_image_generation', return_value=SimpleNamespace(provider_id='fake', model='fake')), \
         patch.object(routes, 'trigger_generation_core', new=fake_task):
        result = tool(client, project, {'node_ids': ['a', 'b'], 'prompt': 'replacement'}, mcp=mcp, name='batch_edit')
    assert len(seen) == 1
    params = seen[0]['params']
    assert {key: params[key] for key in ('node_count', 'user_edited_count', 'n')} == {
        'node_count': 2, 'user_edited_count': 1, 'n': 2}
    assert [item['node_id'] for item in params['preflight']] == ['a', 'b']
    assert seen[0]['target_node_ids'] == ['a', 'b']
    assert all(item['final_prompt'] == 'replacement' for item in params['preflight'])
    assert 'replacement' not in seen[0]['message']
    if result.get('code') == 'CONFIRM_PENDING':
        assert result['status'] == 'awaiting_confirmation' and result['confirm_id'] == seen[0]['confirm_id']
        result = completed.get(timeout=5)
        result = result.get('error', result)
    if not approved:
        assert result['code'] == 'CONFIRM_REJECTED' and '用户已取消' in result['message']
        assert store.canvas(project) == before
        fake_task.assert_not_awaited()
        return
    assert fake_task.await_count == 2
    after = {node['id']: node for node in store.canvas(project)['nodes']}
    assert after['a']['text'] == after['b']['text'] == 'replacement'
    assert after['a']['promptEditedByUser'] is True
    assert set(result['touched_node_ids']) == {'a', 'b'}
    response = client.post(f'/api/projects/{project}/ops/undo', json={'op_id': result['op_id']},
                           headers={'X-Agent-Project': project})
    assert response.status_code == 200, response.text
    undone = response.json()
    assert undone['status'] == 'reverted' and result['op_id'] in undone['reverted'], undone
    assert store.canvas(project)['nodes'] == before['nodes']
