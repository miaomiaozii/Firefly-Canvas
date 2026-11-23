"""The Agent REST tool entry points require project context, not a browser Origin."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from app.routers import api_agent_tools
from firefly_projects import ProjectStore
from tests.test_canvas_viewport_485 import frame, open_socket, quiet_lifetime, ready


WRITES = (
    ('POST', '/canvas/nodes'),
    ('PATCH', '/canvas/nodes/target'),
    ('DELETE', '/canvas/nodes/target'),
    ('POST', '/canvas/edges'),
    ('DELETE', '/canvas/edges/target/actor'),
    ('POST', '/canvas/place_media'),
    ('POST', '/canvas/attach_refs'),
    ('POST', '/canvas/set_ref_order'),
    ('POST', '/canvas/group_create'),
    ('POST', '/canvas/group_arrange'),
    ('POST', '/canvas/arrange_groups'),
    ('POST', '/canvas/align'),
    ('POST', '/canvas/distribute'),
    ('POST', '/canvas/edit_crop'),
    ('POST', '/canvas/edit_rotate'),
    ('POST', '/canvas/edit_flip'),
    ('POST', '/canvas/edit_outpaint'),
    ('POST', '/canvas/edit_erase'),
    ('POST', '/canvas/batch_edit'),
    ('POST', '/generate/trigger'),
    ('POST', '/media/upload'),
    ('DELETE', '/media/example'),
    ('POST', '/viewport/focus'),
    ('POST', '/viewport/zoom'),
    ('POST', '/ops/undo'),
)


@pytest.fixture
def canvas(tmp_path: Path):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    project = store.create_project('Agent REST contract')['id']
    store.apply_mutations(project, [
        {'op': 'upsert_node', 'node': {'id': 'target', 'type': 'smart-prompt', 'text': 'apple'}},
        {'op': 'upsert_node', 'node': {'id': 'actor', 'type': 'smart-agent'}},
    ], base_revision=0)
    try:
        with patch.object(main, 'FIREFLY_PROJECT_STORE', store), \
             patch.object(main.app.router, 'lifespan_context', quiet_lifetime), \
             patch('subprocess.Popen', side_effect=AssertionError('native/provider startup forbidden')), \
             TestClient(main.app, base_url='http://127.0.0.1:3198') as client:
            yield client, store, project, f'/api/projects/{project}'
    finally:
        store.close()


def assert_missing_header(response):
    assert response.status_code == 400, response.text
    error = response.json()['detail']
    assert error['code'] == 'INVALID_ARGUMENT'
    assert 'X-Agent-Project' in error['message'] and 'X-Agent-Project' in error['hint']
    assert error['retryable'] is False


@pytest.mark.parametrize('method,route', WRITES)
@pytest.mark.parametrize('headers', [{}, {'Origin': 'http://127.0.0.1:3198'}, {'X-Agent-Project': ' '}])
def test_rejects_missing_project_before_tool_handler(canvas, method, route, headers):
    client, store, project, url = canvas
    before = store.canvas(project)
    with open_socket(client, project) as ws:
        ready(ws, project)
        body = {'node_id': 'target'} if route == '/viewport/focus' else {'level': 2} if route == '/viewport/zoom' else None
        with patch.object(api_agent_tools, 'request_confirmation_core', new_callable=AsyncMock) as confirm:
            response = client.request(method, f'{url}{route}?agent_node_id=actor', headers=headers, json=body)
        assert_missing_header(response)
        confirm.assert_not_awaited()
        assert store.canvas(project) == before
        # A ready public peer makes accidental viewport dispatch observable, without a fake cache.
        ws.send_text('ping')
        assert frame(ws) == {'type': 'pong'}


def test_preview_remains_read_only_and_apply_requires_project(canvas):
    client, store, project, url = canvas
    before = store.canvas(project)
    route = f'{url}/canvas/batch_prompt_edit?agent_node_id=actor'
    preview = client.post(route, json={'apply': False, 'edits': [{'node_id': 'target', 'append': ' pie'}]})
    assert preview.status_code == 200, preview.text
    payload = {'apply': True, 'diff_id': preview.json()['diff_id']}
    with patch.object(api_agent_tools, 'request_confirmation_core', new_callable=AsyncMock) as confirm:
        assert_missing_header(client.post(route, json=payload))
    confirm.assert_not_awaited()
    assert store.canvas(project) == before


def test_read_routes_dont_require_project_context(canvas):
    client, _, _, url = canvas
    for route in ('/canvas/nodes', '/canvas/nodes/target', '/media/list', '/ops'):
        response = client.get(url + route)
        assert response.status_code == 200, response.text
    response = client.get(url + '/viewport/state')
    assert response.status_code == 503, response.text
    error = response.json()['detail']
    assert error['code'] == 'SERVER_NOT_READY'
    assert error['details']['reason'] == 'VIEWPORT_NOT_READY'
    assert error['details']['dispatched'] is False
