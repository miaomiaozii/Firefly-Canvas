import asyncio
import io
import json
import threading
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from PIL import Image
from starlette.requests import Request

import main
from app.routers import api_agent_tools as routes, api_mcp
from app.services.agent_ops_context import agent_ops_scope
from app.services.agent_ops_store import get_operation, list_operation_tree, list_operations
from app.services.agent_ops_undo import undo_ops
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore


CASES = {
    'crop': {'rect': {'x': 0, 'y': 0, 'width': 4, 'height': 6}},
    'rotate': {'angle': 90},
    'flip': {'direction': 'horizontal'},
    'outpaint': {'outpaint_size': {'width': 10, 'height': 10}, 'offset': {'x': 1, 'y': 1}},
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    project = store.create_project('transforms')['id']
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    image = Image.new('RGB', (8, 6), 'red')
    image.putpixel((0, 0), (0, 0, 255))
    data = io.BytesIO()
    image.save(data, format='PNG')
    asset = store.import_media(project, data.getvalue(), 'source.png', 'image/png')
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {'id': 'image', 'type': 'smart-image',
        'images': [{'asset_id': asset['asset_id'], 'kind': 'image', 'name': 'source.png'}]}}], base_revision=0)
    store.flush(project)
    yield store, project, OpsDb(store.root)
    store.close()


def scope(project, kind):
    return agent_ops_scope(project_id=project, agent_node_id='actor', turn_id='turn',
                           tool='canvas.edit_' + kind, args={'node_id': 'image', **CASES[kind]})


def call(project, kind):
    return getattr(routes, kind + '_node_image_core')(project, 'image', **CASES[kind])


@pytest.mark.parametrize('kind', CASES)
def test_transform_records_real_delta_and_undo(env, kind):
    store, project, db = env
    before = deepcopy(store.canvas(project)['nodes'])
    async def run():
        with scope(project, kind):
            return await call(project, kind)
    result = asyncio.run(run())
    tree = list_operation_tree(db, project, result['op_id'])
    assert len(tree) == 2
    assert all((r['agent_node_id'], r['turn_id'], r['tool']) == ('actor', 'turn', 'canvas.edit_' + kind) for r in tree)
    assert any(r['before'] for r in tree)
    assert any(r['irreversible_effects'] for r in tree)
    assert Path(store.media_record(project, result['asset_id'])['path']).is_file()
    undone = undo_ops(store, project, op_id=result['op_id'])
    assert undone['status'] == 'reverted' and undone['irreversible_effects']
    assert store.canvas(project)['nodes'] == before
    assert Path(store.media_record(project, result['asset_id'])['path']).is_file()


def test_dedupe_still_records_canvas_metadata(env, monkeypatch):
    store, project, db = env
    async def run():
        with scope(project, 'flip'):
            first = await call(project, 'flip')
        assert undo_ops(store, project, op_id=first['op_id'])['status'] == 'reverted'
        with scope(project, 'flip'):
            return first, await call(project, 'flip')
    first, second = asyncio.run(run())
    assert first['asset_id'] == second['asset_id']
    tree = list_operation_tree(db, project, second['op_id'])
    assert any(r['before'] for r in tree)
    assert not any(r['irreversible_effects'] for r in tree)
    assert undo_ops(store, project, op_id=second['op_id'])['status'] == 'reverted'


def test_partial_import_reports_identity(env, monkeypatch):
    store, project, db = env
    def fail(*args, **kwargs):
        (store.root / project / 'media' / 'orphan.png').write_bytes(b'partial')
        raise OSError('archive failed')
    monkeypatch.setattr(store, 'import_media', fail)
    async def run():
        with scope(project, 'crop') as context:
            with pytest.raises(HTTPException) as error:
                await call(project, 'crop')
            assert error.value.detail['details'] == {'op_id': context.op_id, 'status': 'partial-unknown'}
            assert get_operation(db, project, context.op_id)['irreversible_effects']
    asyncio.run(run())


def test_canvas_flush_failure_reports_partial_unknown(env, monkeypatch):
    store, project, db = env
    flush = store.flush
    def fail(_project):
        raise OSError('canvas flush failed')
    monkeypatch.setattr(store, 'flush', fail)
    async def run():
        with scope(project, 'rotate') as context:
            with pytest.raises(HTTPException) as error:
                await call(project, 'rotate')
            assert error.value.detail['details'] == {'op_id': context.op_id, 'status': 'partial-unknown'}
            assert get_operation(db, project, context.op_id)['irreversible_effects']
            assert undo_ops(store, project, op_id=context.op_id)['status'] in ('pending', 'generating')
    asyncio.run(run())
    monkeypatch.setattr(store, 'flush', flush)
    store.flush(project)


def test_invalid_argument_does_not_record(env):
    store, project, db = env
    async def run():
        with scope(project, 'rotate'):
            with pytest.raises(HTTPException):
                await routes.rotate_node_image_core(project, 'image', 23)
    asyncio.run(run())
    assert list_operations(db, project) == []


def test_cancellation_waits_for_import_and_reports_root(env, monkeypatch):
    store, project, db = env
    entered, release = threading.Event(), threading.Event()
    original = store.import_media
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'import_media', blocked)
    async def run():
        with scope(project, 'crop') as context:
            task = asyncio.create_task(call(project, 'crop'))
            assert await asyncio.to_thread(entered.wait, 5)
            assert get_operation(db, project, context.op_id)['irreversible_effects']
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done()
            release.set()
            with pytest.raises(HTTPException) as error:
                await task
            assert error.value.detail['details'] == {'op_id': context.op_id, 'status': 'partial-unknown'}
            assert get_operation(db, project, context.op_id)['irreversible_effects']
    asyncio.run(run())


def test_user_image_change_during_archive_is_not_overwritten(env, monkeypatch):
    store, project, db = env
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        original = routes._load_node_image
        async def blocked(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result
        monkeypatch.setattr(routes, '_load_node_image', blocked)
        with scope(project, 'crop') as context:
            task = asyncio.create_task(call(project, 'crop'))
            await asyncio.wait_for(entered.wait(), 5)
            canvas = store.canvas(project)
            node = deepcopy(canvas['nodes'][0])
            node['images'][0]['name'] = 'human.png'
            store.apply_mutations(project, [{'op': 'upsert_node', 'node': node}], base_revision=canvas['revision'])
            store.flush(project)
            release.set()
            with pytest.raises(HTTPException) as error:
                await task
            assert error.value.detail['details']['op_id'] == context.op_id
            assert store.canvas(project)['nodes'][0]['images'][0]['name'] == 'human.png'
    asyncio.run(run())


def test_undo_conflicts_with_later_user_edit(env):
    store, project, db = env
    async def run():
        with scope(project, 'rotate'):
            return await call(project, 'rotate')
    result = asyncio.run(run())
    canvas = store.canvas(project)
    node = deepcopy(canvas['nodes'][0])
    node['images'][0]['name'] = 'human.png'
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': node}], base_revision=canvas['revision'])
    store.flush(project)
    assert undo_ops(store, project, op_id=result['op_id'])['status'] == 'conflict'


@pytest.mark.parametrize('kind', CASES)
def test_rest_gate_and_identity(env, monkeypatch, kind):
    store, project, db = env
    gate = AsyncMock()
    monkeypatch.setattr(routes, '_server_confirm_gate', gate)
    from app.services.agent_host import agent_host_manager
    monkeypatch.setattr(agent_host_manager, 'get_current_turn_id', lambda p, n: 'active-turn')
    body = json.dumps({'node_id': 'image', **CASES[kind]}).encode()
    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'query_string': b'agent_node_id=rest-actor',
                       'headers': [(b'x-agent-project', project.encode())]}, receive)
    result = asyncio.run(getattr(routes, 'canvas_edit_' + kind)(project, request, 'rest-actor'))
    gate.assert_awaited_once()
    record = get_operation(db, project, result['op_id'])
    assert (record['agent_node_id'], record['turn_id'], record['tool']) == ('rest-actor', 'active-turn', 'canvas.edit_' + kind)


@pytest.mark.parametrize('kind', CASES)
def test_mcp_gate_and_identity(env, monkeypatch, kind):
    store, project, db = env
    gate = AsyncMock()
    monkeypatch.setattr(api_mcp, '_confirm_gate', gate)
    monkeypatch.setattr(api_mcp, 'resolve_project', lambda p: p)
    from app.services.agent_host import agent_host_manager
    monkeypatch.setattr(agent_host_manager, 'get_current_turn_id', lambda p, n: 'mcp-turn')
    query = urlencode({'project': project, 'node': 'mcp-actor', 'caller': 'hosted'})
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp', 'query_string': query.encode(), 'headers': []})
    payload = {'params': {'name': 'canvas.edit_' + kind, 'arguments': {'node_id': 'image', **CASES[kind]}}}
    response = asyncio.run(api_mcp._tools_call(payload, 1, 'session', request))
    result = json.loads(response.body)['result']
    assert not result['isError'], result
    gate.assert_awaited_once()
    data = json.loads(result['content'][0]['text'])
    record = get_operation(db, project, data['op_id'])
    assert (record['agent_node_id'], record['turn_id'], record['tool']) == ('mcp-actor', 'mcp-turn', 'canvas.edit_' + kind)


def test_rejected_confirmation_has_no_effect(env, monkeypatch):
    store, project, db = env
    monkeypatch.setattr(routes, '_server_confirm_gate', AsyncMock(side_effect=HTTPException(403, 'denied')))
    body = json.dumps({'node_id': 'image', **CASES['crop']}).encode()
    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'query_string': b'agent_node_id=actor',
                       'headers': [(b'x-agent-project', project.encode())]}, receive)
    with pytest.raises(HTTPException):
        asyncio.run(routes.canvas_edit_crop(project, request))
    assert list_operations(db, project) == []
