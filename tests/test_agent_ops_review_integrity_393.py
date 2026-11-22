import asyncio
import io
import json
from copy import deepcopy
from urllib.parse import urlencode
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from starlette.requests import Request

import main
from app.constants import NODE_TYPE_AGENT
from app.routers import api_agent_tools as routes, api_mcp
from app.services.agent_ops_store import get_operation, list_operations
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


def png(color):
    output = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(output, format='PNG')
    return output.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    project = store.create_project('review')['id']
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    asset = store.import_media(project, png('red'), 'source.png', 'image/png')
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {
        'id': 'image', 'type': 'smart-image',
        'images': [{'asset_id': asset['asset_id'], 'kind': 'image', 'name': 'source.png'}],
    }}], base_revision=0)
    store.flush(project)
    yield store, project, OpsDb(store.root)
    store.close()


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_erase_provider_wait_does_not_overwrite_human_image(env, monkeypatch, transport):
    store, project, db = env
    monkeypatch.setattr(routes, '_server_confirm_gate', AsyncMock())
    monkeypatch.setattr(api_mcp, '_confirm_gate', AsyncMock())
    monkeypatch.setattr(api_mcp, 'resolve_project', lambda p: p)
    from app.services.agent_host import agent_host_manager
    monkeypatch.setattr(agent_host_manager, 'get_current_turn_id', lambda p, n: 'turn')
    args = {'node_id': 'image', 'rect': {'x': 0, 'y': 0, 'width': 4, 'height': 4}, 'prompt': 'erase'}

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def adapter(_):
            entered.set()
            await release.wait()
            return png('blue')
        monkeypatch.setattr(main, 'local_edit_adapter', adapter)
        if transport == 'rest':
            body = json.dumps(args).encode()
            async def receive():
                return {'type': 'http.request', 'body': body, 'more_body': False}
            request = Request({'type': 'http', 'method': 'POST', 'path': '/',
                               'query_string': b'agent_node_id=actor',
                               'headers': [(b'x-agent-project', project.encode())]}, receive)
            task = asyncio.create_task(routes.canvas_edit_erase(project, request, 'actor'))
        else:
            query = urlencode({'project': project, 'node': 'actor', 'caller': 'hosted'})
            request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                               'query_string': query.encode(), 'headers': []})
            payload = {'params': {'name': 'canvas.edit_erase', 'arguments': args}}
            task = asyncio.create_task(api_mcp._tools_call(payload, 1, 'session', request))
        try:
            done, _ = await asyncio.wait({task, asyncio.create_task(entered.wait())}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            assert entered.is_set(), task.result().body if task.done() else 'provider was not called'
            canvas = store.canvas(project)
            node = deepcopy(canvas['nodes'][0])
            node['images'][0]['name'] = 'human.png'
            store.apply_mutations(project, [{'op': 'upsert_node', 'node': node}], base_revision=canvas['revision'])
            store.flush(project)
            release.set()
            if transport == 'rest':
                from fastapi import HTTPException
                with pytest.raises(HTTPException) as error:
                    await task
                assert error.value.status_code == 409
                detail = error.value.detail['details']
            else:
                response = await task
                result = json.loads(response.body)['result']
                assert result['isError']
                detail = result['structuredContent']['details']
            op_id = detail['op_id']
            assert detail['status'] == 'conflict' and detail['irreversible_effects']
            assert store.canvas(project)['nodes'][0]['images'][0]['name'] == 'human.png'
            assert get_operation(db, project, op_id)['irreversible_effects']
            assert len(list_operations(db, project)) >= 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['sidecar', 'before_replace'])
def test_failed_journal_broadcasts_only_proven_canvas_replace(env, monkeypatch, failure):
    from fastapi import HTTPException
    from app.services import agent_ops_store
    from app.services.agent_ops_context import agent_ops_scope

    store, project, db = env
    # #486: reach the persistence fault with an owned target; persist the baseline before injecting it.
    canvas = store.canvas(project)
    image = next(node for node in canvas['nodes'] if node['id'] == 'image')
    store.apply_mutations(project, [
        {'op': 'upsert_node', 'node': {
            **image, 'createdBy': 'agent', 'agentNodeId': 'actor',
            'userTouchedAt': None, 'promptEditedByUser': False, 'locked': False,
        }},
        {'op': 'upsert_node', 'node': {'id': 'actor', 'type': NODE_TYPE_AGENT}},
    ], base_revision=canvas['revision'])
    store.flush(project)
    manager = type('Manager', (), {})()
    manager.broadcast_canvas_updated = AsyncMock()
    monkeypatch.setattr(main, 'manager', manager)
    if failure == 'sidecar':
        monkeypatch.setattr(agent_ops_store, 'append_operation', lambda *a, **k: (_ for _ in ()).throw(OSError('sidecar')))
    else:
        monkeypatch.setattr(store, 'flush', lambda *a, **k: (_ for _ in ()).throw(OSError('before replace')))

    async def run():
        with agent_ops_scope(project_id=project, agent_node_id='actor', turn_id='turn',
                             tool='canvas.update_node', args={}) as context:
            if failure == 'sidecar':
                result = await routes.update_node_core(project, 'image', {'title': 'changed'})
                assert result['op_id'] == context.op_id
                assert result['touched_node_ids'] == ['image']
            else:
                with pytest.raises(HTTPException):
                    await routes.update_node_core(project, 'image', {'title': 'changed'})
            assert [call.kwargs for call in manager.broadcast_canvas_updated.await_args_list] == [
                {'source': 'agent', 'op_id': context.op_id, 'touched_node_ids': ['image'], 'phase': 'start'},
                {'source': 'agent', 'op_id': context.op_id,
                 'touched_node_ids': ['image'] if failure == 'sidecar' else [], 'phase': 'end'},
            ]
            persisted = json.loads((store.root / project / 'canvas.json').read_text(encoding='utf-8'))['nodes'][0]
            if failure == 'sidecar':
                assert persisted['title'] == 'changed'
            else:
                assert 'title' not in persisted
    asyncio.run(run())
