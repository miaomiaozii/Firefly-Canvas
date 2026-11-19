import asyncio
import io
import json
from pathlib import Path
from urllib.parse import urlencode

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


def png(color):
    out = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(out, format='PNG')
    return out.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    project = store.create_project('media')['id']
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    yield store, project, OpsDb(store.root)
    store.close()


def scope(project, tool):
    return agent_ops_scope(project_id=project, agent_node_id='actor', turn_id='turn', tool=tool, args={})


def setup_image(store, project):
    asset = store.import_media(project, png('red'), 'source.png', 'image/png')
    canvas = store.canvas(project)
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {
        'id': 'image', 'type': 'smart-image', 'images': [{'asset_id': asset['asset_id'], 'kind': 'image', 'name': 'source.png'}]}}], base_revision=canvas['revision'])
    store.flush(project)
    return asset['asset_id']


def test_upload_dedupe_delete_and_undo(env):
    store, project, db = env
    source = store.root / project / 'input.png'
    source.write_bytes(png('red'))
    async def run():
        with scope(project, 'media.upload'):
            result = await routes.upload_media_core(project, str(source))
        with scope(project, 'media.upload'):
            again = await routes.upload_media_core(project, str(source))
        assert 'op_id' not in again
        assert len(list_operations(db, project)) == 1
        second = store.root / project / 'second.png'
        second.write_bytes(source.read_bytes())
        with scope(project, 'media.upload'):
            merged = await routes.upload_media_core(project, str(second))
        assert merged['asset_id'] == result['asset_id'] and merged['op_id'] != result['op_id']
        asset = result['asset_id']
        path = Path(store.media_record(project, asset)['path'])
        with scope(project, 'media.delete'):
            deleted = await routes.delete_media_core(project, asset)
        assert not path.exists()
        undo = undo_ops(store, project, op_id=deleted['op_id'])
        assert undo['irreversible_effects'] and not path.exists()
        with scope(project, 'media.delete'):
            missing = await routes.delete_media_core(project, asset)
        assert 'op_id' not in missing
        assert get_operation(db, project, result['op_id'])['agent_node_id'] == 'actor'
    asyncio.run(run())


def test_erase_success_canvas_and_undo(env, monkeypatch):
    store, project, db = env
    original = setup_image(store, project)
    monkeypatch.setattr(main, 'local_edit_adapter', lambda _: png('blue'))
    async def run():
        with scope(project, 'canvas.edit_erase'):
            return await routes.erase_node_image_core(project, 'image', {'x': 0, 'y': 0, 'width': 4, 'height': 4}, 'erase')
    result = asyncio.run(run())
    assert result['asset_id'] != original
    tree = list_operation_tree(db, project, result['op_id'])
    assert len(tree) == 3
    assert tree[0]['parent_op_id'] == result['op_id'] and tree[0]['before']
    undo = undo_ops(store, project, op_id=result['op_id'])
    assert undo['status'] == 'reverted' and undo['irreversible_effects']
    assert store.canvas(project)['nodes'][0]['images'][0]['asset_id'] == original
    assert Path(store.media_record(project, result['asset_id'])['path']).is_file()


@pytest.mark.parametrize('kind', ['failure', 'timeout'])
def test_erase_attempt_is_retained_on_failure_or_timeout(env, monkeypatch, kind):
    store, project, db = env
    setup_image(store, project)
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def adapter(_):
            entered.set()
            if kind == 'timeout':
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            raise RuntimeError('provider failure')
        monkeypatch.setattr(main, 'local_edit_adapter', adapter)
        with scope(project, 'canvas.edit_erase') as context:
            task = asyncio.create_task(routes.erase_node_image_core(
                project, 'image', {'x': 0, 'y': 0, 'width': 4, 'height': 4}, 'erase'))
            try:
                if kind == 'timeout':
                    await asyncio.wait_for(entered.wait(), 10)
                with pytest.raises(HTTPException) as error:
                    if kind == 'timeout':
                        await asyncio.wait_for(task, .05)
                    else:
                        await task
                assert entered.is_set()
                if kind == 'timeout':
                    assert cancelled.is_set()
                assert error.value.detail['details']['op_id'] == context.op_id
                assert error.value.detail['details']['status'] == 'partial-unknown'
                assert get_operation(db, project, context.op_id)['irreversible_effects']
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_bad_input_no_write_and_partial_delete(env, monkeypatch):
    store, project, db = env
    asset = setup_image(store, project)
    async def run():
        with scope(project, 'canvas.edit_erase'):
            with pytest.raises(HTTPException):
                await routes.erase_node_image_core(project, 'image', {}, '')
        assert list_operations(db, project) == []
        def partial(project_id, asset_id):
            Path(store.media_record(project_id, asset_id)['path']).unlink()
            raise OSError('index failure')
        monkeypatch.setattr(store, 'delete_media', partial)
        with scope(project, 'media.delete') as context:
            with pytest.raises(HTTPException) as error:
                await routes.delete_media_core(project, asset)
            assert error.value.detail['details']['op_id'] == context.op_id
            assert get_operation(db, project, context.op_id)['irreversible_effects']
    asyncio.run(run())


def test_rest_agent_scope_identity(env):
    store, project, db = env
    source = store.root / project / 'input.png'
    source.write_bytes(png('green'))
    body = ('{"file_path": ' + __import__('json').dumps(str(source)) + '}').encode()
    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'query_string': b'agent_node_id=rest-actor',
                       'headers': [(b'x-agent-project', project.encode())]}, receive)
    result = asyncio.run(routes.upload_media(project, request))
    assert get_operation(db, project, result['op_id'])['agent_node_id'] == 'rest-actor'


def test_mcp_agent_identity(env, monkeypatch):
    store, project, db = env
    source = store.root / project / 'input.png'
    source.write_bytes(png('green'))
    monkeypatch.setattr(api_mcp, 'resolve_project', lambda p: p)
    query = urlencode({'project': project, 'node': 'mcp-actor', 'turn_id': 'mcp-turn'})
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
                       'query_string': query.encode(), 'headers': []})
    payload = {'params': {'name': 'media.upload', 'arguments': {'file_path': str(source)}}}
    response = asyncio.run(api_mcp._tools_call(payload, 1, 'session', request))
    result = json.loads(response.body)['result']
    assert not result['isError'], result
    data = json.loads(result['content'][0]['text'])
    record = get_operation(db, project, data['op_id'])
    assert (record['agent_node_id'], record['turn_id'], record['tool']) == ('mcp-actor', 'mcp-turn', 'media.upload')


def test_erase_undo_respects_later_user_changes(env, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    monkeypatch.setattr(main, 'local_edit_adapter', lambda _: png('blue'))
    async def run():
        with scope(project, 'canvas.edit_erase'):
            return await routes.erase_node_image_core(project, 'image', {'x': 0, 'y': 0, 'width': 4, 'height': 4}, 'erase')
    result = asyncio.run(run())
    user_asset = store.import_media(project, png('yellow'), 'user.png', 'image/png')['asset_id']
    canvas = store.canvas(project)
    node = dict(canvas['nodes'][0])
    node['images'] = [{'asset_id': user_asset, 'kind': 'image'}]
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': node}], base_revision=canvas['revision'])
    store.flush(project)
    assert undo_ops(store, project, op_id=result['op_id'])['status'] == 'conflict'
    assert store.canvas(project)['nodes'][0]['images'][0]['asset_id'] == user_asset


def test_canvas_flush_failure_reports_partial_unknown(env, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    monkeypatch.setattr(main, 'local_edit_adapter', lambda _: png('blue'))
    flush = store.flush
    calls = 0
    def fail_second(project_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('canvas flush failed before replacement')
        return flush(project_id)
    monkeypatch.setattr(store, 'flush', fail_second)
    async def run():
        with scope(project, 'canvas.edit_erase') as context:
            with pytest.raises(HTTPException) as error:
                await routes.erase_node_image_core(project, 'image', {'x': 0, 'y': 0, 'width': 4, 'height': 4}, 'erase')
            assert error.value.detail['details'] == {'op_id': context.op_id, 'status': 'partial-unknown'}
            assert get_operation(db, project, context.op_id)['irreversible_effects']
            assert undo_ops(store, project, op_id=context.op_id)['status'] == 'pending'
    asyncio.run(run())
    monkeypatch.setattr(store, 'flush', flush)
    store.flush(project)


def test_partial_import_retains_identity(env, monkeypatch):
    store, project, db = env
    source = store.root / project / 'input.png'
    source.write_bytes(png('green'))
    def partial(*args, **kwargs):
        (store.root / project / 'media' / 'orphan.png').write_bytes(b'partial')
        raise OSError('failed after file replacement')
    monkeypatch.setattr(store, 'import_media_file', partial)
    async def run():
        with scope(project, 'media.upload') as context:
            with pytest.raises(HTTPException) as error:
                await routes.upload_media_core(project, str(source))
            assert error.value.detail['details'] == {'op_id': context.op_id, 'status': 'partial-unknown'}
            assert get_operation(db, project, context.op_id)['irreversible_effects']
    asyncio.run(run())
