import asyncio
import json
import threading
from urllib.parse import urlencode
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import main
from app.routers import api_agent_tools as routes, api_mcp
from app.services.agent_ops_store import get_operation
from tests.test_agent_ops_media_e2e_393 import env, png, scope, setup_image
from tests.test_agent_ops_transform_393 import CASES


@pytest.fixture
def events(monkeypatch):
    events = []
    async def broadcast(project, timestamp, **kw):
        if kw.get('phase'):
            events.append(kw.copy())
    monkeypatch.setattr(main.manager, 'broadcast_canvas_updated', broadcast)
    return events


def pair(events, op_id, start, end):
    assert [(e['phase'], e['op_id'], e['touched_node_ids'], e['source']) for e in events] == [
        ('start', op_id, start, 'agent'), ('end', op_id, end, 'agent')]


@pytest.mark.parametrize('kind', list(CASES))
def test_transforms(env, events, kind):
    store, project, db = env
    setup_image(store, project)
    async def run():
        with scope(project, 'canvas.edit_' + kind):
            return await getattr(routes, kind + '_node_image_core')(project, 'image', **CASES[kind])
    result = asyncio.run(run())
    pair(events, result['op_id'], ['image'], ['image'])
    assert get_operation(db, project, result['op_id'])


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel'])
def test_erase_blocked_provider(env, events, monkeypatch, outcome):
    store, project, db = env
    setup_image(store, project)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(_):
            assert [e['phase'] for e in events] == ['start']
            assert routes.agent_generation_is_running(store, project, events[0]['op_id'])
            entered.set()
            await release.wait()
            if outcome == 'failure':
                raise RuntimeError('failed')
            return png('blue')
        monkeypatch.setattr(main, 'local_edit_adapter', provider)
        with scope(project, 'canvas.edit_erase') as context:
            task = asyncio.create_task(routes.erase_node_image_core(project, 'image',
                {'x': 0, 'y': 0, 'width': 4, 'height': 4}, 'erase'))
            await asyncio.wait_for(entered.wait(), 5)
            if outcome == 'cancel':
                task.cancel()
            else:
                release.set()
            if outcome == 'success':
                result = await task
                assert result['op_id'] == context.op_id
            else:
                with pytest.raises(HTTPException):
                    await task
            pair(events, context.op_id, ['image'], ['image'] if outcome == 'success' else [])
            assert not routes.agent_generation_is_running(store, project, context.op_id)
    asyncio.run(run())


def test_upload_delete_and_noop(env, events):
    store, project, db = env
    source = store.root / project / 'input.png'
    source.write_bytes(png('red'))
    async def run():
        with scope(project, 'media.upload'):
            result = await routes.upload_media_core(project, str(source))
        pair(events, result['op_id'], [], [])
        events.clear()
        with scope(project, 'media.upload'):
            duplicate = await routes.upload_media_core(project, str(source))
        assert 'op_id' not in duplicate and not events
        with scope(project, 'media.delete'):
            deleted = await routes.delete_media_core(project, result['asset_id'])
        pair(events, deleted['op_id'], [], [])
        events.clear()
        with scope(project, 'media.delete'):
            missing = await routes.delete_media_core(project, result['asset_id'])
        assert 'op_id' not in missing and not events
    asyncio.run(run())


def test_failure_before_canvas_persist(env, events, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    before = json.loads((store.root / project / 'canvas.json').read_text(encoding='utf-8'))
    flush = store.flush
    def fail(*args, **kwargs):
        raise OSError('flush failed')
    monkeypatch.setattr(store, 'flush', fail)
    async def run():
        with scope(project, 'canvas.edit_rotate') as context:
            with pytest.raises(HTTPException):
                await routes.rotate_node_image_core(project, 'image', 90)
            assert [(e['phase'], e['op_id'], e['touched_node_ids']) for e in events] == [
                ('start', context.op_id, ['image'])]
            assert routes.agent_generation_is_running(store, project, context.op_id)
            monkeypatch.setattr(store, 'flush', flush)
            for _ in range(100):
                if len(events) == 2:
                    break
                await asyncio.sleep(.05)
            persisted = json.loads((store.root / project / 'canvas.json').read_text(encoding='utf-8'))
            from app.services.agent_ops_delta import build_delta, touched_node_ids
            persisted_ids = touched_node_ids(build_delta(before, persisted))
            assert persisted_ids == ['image']
            assert get_operation(db, project, context.op_id)
            pair(events, context.op_id, ['image'], persisted_ids)
            assert not routes.agent_generation_is_running(store, project, context.op_id)
    asyncio.run(run())


def test_rest_mcp_identity(env, events, monkeypatch):
    store, project, db = env
    first = store.root / project / 'first.png'
    second = store.root / project / 'second.png'
    first.write_bytes(png('red'))
    second.write_bytes(png('blue'))
    monkeypatch.setattr(api_mcp, 'resolve_project', lambda p: p)
    async def run():
        request = Request({'type': 'http', 'method': 'POST', 'path': '/',
            'query_string': b'agent_node_id=rest', 'headers': [(b'x-agent-project', project.encode())]},
            AsyncMock(return_value={'type': 'http.request', 'body': json.dumps({'file_path': str(first)}).encode(), 'more_body': False}))
        result = await routes.upload_media(project, request)
        pair(events, result['op_id'], [], [])
        assert get_operation(db, project, result['op_id'])['agent_node_id'] == 'rest'
        events.clear()
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
            'query_string': urlencode({'project': project, 'node': 'mcp', 'turn_id': 'turn'}).encode(), 'headers': []})
        response = await api_mcp._tools_call({'params': {'name': 'media.upload',
            'arguments': {'file_path': str(second)}}}, 1, 'session', request)
        payload = json.loads(response.body)['result']
        assert not payload['isError']
        result = json.loads(payload['content'][0]['text'])
        pair(events, result['op_id'], [], [])
        assert get_operation(db, project, result['op_id'])['agent_node_id'] == 'mcp'
    asyncio.run(run())


def test_cancel_waits_for_blocking_archive(env, events, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    entered, release = threading.Event(), threading.Event()
    original = store.import_media
    def blocked(*args, **kwargs):
        assert [e['phase'] for e in events] == ['start']
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'import_media', blocked)
    async def run():
        with scope(project, 'canvas.edit_crop') as context:
            task = asyncio.create_task(routes.crop_node_image_core(project, 'image', CASES['crop']['rect']))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done() and len(events) == 1
            release.set()
            with pytest.raises(HTTPException):
                await task
            pair(events, context.op_id, ['image'], [])
    asyncio.run(run())


def test_cancel_after_canvas_persistence_reports_actual_ids(env, events, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    entered, release = threading.Event(), threading.Event()
    flush = store.flush
    def blocked(pid):
        flush(pid)
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(store, 'flush', blocked)
    async def run():
        with scope(project, 'canvas.edit_rotate') as context:
            task = asyncio.create_task(routes.rotate_node_image_core(project, 'image', 90))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done() and len(events) == 1
            release.set()
            with pytest.raises(HTTPException):
                await task
            pair(events, context.op_id, ['image'], ['image'])
    asyncio.run(run())


def test_flush_after_persist_failure_reports_persisted_ids(env, events, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    flush = store.flush
    def failed_after_flush(pid):
        flush(pid)
        raise OSError('after persistence')
    monkeypatch.setattr(store, 'flush', failed_after_flush)
    async def run():
        with scope(project, 'canvas.edit_rotate') as context:
            with pytest.raises(HTTPException):
                await routes.rotate_node_image_core(project, 'image', 90)
            pair(events, context.op_id, ['image'], ['image'])
    asyncio.run(run())


def test_best_effort_broadcast_and_validation(env, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    broadcast = AsyncMock(side_effect=RuntimeError('disconnected'))
    monkeypatch.setattr(main.manager, 'broadcast_canvas_updated', broadcast)
    async def run():
        with scope(project, 'canvas.edit_rotate'):
            with pytest.raises(HTTPException):
                await routes.rotate_node_image_core(project, 'image', 1)
        broadcast.assert_not_awaited()
        with scope(project, 'canvas.edit_rotate'):
            result = await routes.rotate_node_image_core(project, 'image', 90)
        assert get_operation(db, project, result['op_id'])
        assert broadcast.await_count >= 2
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['upload', 'delete'])
def test_media_effect_waits_before_end(env, events, monkeypatch, operation):
    store, project, db = env
    source = store.root / project / 'input.png'
    source.write_bytes(png('green'))
    asset = store.import_media(project, png('red'), 'red.png', 'image/png')['asset_id']
    method = 'import_media_file' if operation == 'upload' else 'delete_media'
    original = getattr(store, method)
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        assert [e['phase'] for e in events] == ['start']
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, method, blocked)
    async def run():
        with scope(project, 'media.' + operation) as context:
            call = (routes.upload_media_core(project, str(source)) if operation == 'upload'
                    else routes.delete_media_core(project, asset))
            task = asyncio.create_task(call)
            assert await asyncio.to_thread(entered.wait, 5)
            assert routes.agent_generation_is_running(store, project, context.op_id)
            assert len(events) == 1
            release.set()
            await task
            pair(events, context.op_id, [], [])
    asyncio.run(run())


def test_transform_rest_mcp_identity(env, events, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    monkeypatch.setattr(routes, '_server_confirm_gate', AsyncMock())
    monkeypatch.setattr(api_mcp, '_confirm_gate', AsyncMock())
    monkeypatch.setattr(api_mcp, 'resolve_project', lambda p: p)
    async def run():
        body = {'node_id': 'image', 'direction': 'horizontal'}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/',
            'query_string': b'agent_node_id=rest', 'headers': [(b'x-agent-project', project.encode())]},
            AsyncMock(return_value={'type': 'http.request', 'body': json.dumps(body).encode(), 'more_body': False}))
        result = await routes.canvas_edit_flip(project, request, 'rest')
        pair(events, result['op_id'], ['image'], ['image'])
        assert get_operation(db, project, result['op_id'])['agent_node_id'] == 'rest'
        events.clear()
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/mcp',
            'query_string': urlencode({'project': project, 'node': 'mcp', 'turn_id': 'turn'}).encode(), 'headers': []})
        response = await api_mcp._tools_call({'params': {'name': 'canvas.edit_flip', 'arguments': body}}, 1, 'session', request)
        payload = json.loads(response.body)['result']
        assert not payload['isError'], payload
        result = json.loads(payload['content'][0]['text'])
        pair(events, result['op_id'], ['image'], ['image'])
        assert get_operation(db, project, result['op_id'])['agent_node_id'] == 'mcp'
    asyncio.run(run())


def test_guard_held_through_end_delivery(env, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def broadcast(pid, timestamp, **kw):
            if kw.get('phase') == 'end':
                assert routes.agent_generation_is_running(store, project, kw['op_id'])
                entered.set()
                await release.wait()
        monkeypatch.setattr(main.manager, 'broadcast_canvas_updated', broadcast)
        with scope(project, 'canvas.edit_flip') as context:
            task = asyncio.create_task(routes.flip_node_image_core(project, 'image', 'horizontal'))
            await asyncio.wait_for(entered.wait(), 5)
            assert routes.agent_generation_is_running(store, project, context.op_id)
            release.set()
            await task
            assert not routes.agent_generation_is_running(store, project, context.op_id)
    asyncio.run(run())
