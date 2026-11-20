import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock

import main
from app.routers import api_agent_tools as routes, api_mcp, api_project_library
from tests.agent_image_task_fakes import image_candidate, route_stub
from app.services.agent_ops_journal import PendingOperation
from app.services.agent_ops_store import get_operation
from tests.test_agent_ops_media_e2e_393 import env, png, scope, setup_image


@pytest.fixture
def events(monkeypatch):
    recorded = []
    async def broadcast(project, timestamp, **kwargs):
        if kwargs.get('phase'):
            recorded.append(kwargs.copy())
    monkeypatch.setattr(main.manager, 'broadcast_canvas_updated', broadcast)
    return recorded


async def eventually(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(.05)
    pytest.fail('recovery did not settle')


def test_media_registration_reserves_before_mutation(env, events, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    def fail(*args, **kwargs):
        raise RuntimeError('register must not be called after write')
    monkeypatch.setattr(PendingOperation, 'register', fail)
    async def run():
        with scope(project, 'canvas.edit_rotate') as context:
            result = await routes.rotate_node_image_core(project, 'image', 90)
            assert result['op_id'] == context.op_id
            assert get_operation(db, project, context.op_id)
            assert [(e['phase'], e['touched_node_ids']) for e in events] == [
                ('start', ['image']), ('end', ['image'])]
        await asyncio.sleep(.7)
        assert len(events) == 2
    asyncio.run(run())


def test_media_registration_failure_delays_end_and_recovers(env, events, monkeypatch):
    store, project, db = env
    setup_image(store, project)
    store.snapshot_delay = .5
    register = routes._batch_register_reserved
    allow = False
    def fail_once(*args):
        if not allow:
            raise RuntimeError('injected registration failure')
        return register(*args)
    monkeypatch.setattr(routes, '_batch_register_reserved', fail_once)
    async def run():
        nonlocal allow
        with scope(project, 'canvas.edit_rotate') as context:
            with pytest.raises(HTTPException):
                await routes.rotate_node_image_core(project, 'image', 90)
            assert [e['phase'] for e in events] == ['start']
            assert routes.agent_generation_is_running(store, project, context.op_id)
            await asyncio.sleep(.7)
            assert [e['phase'] for e in events] == ['start']
            allow = True
            await eventually(lambda: len(events) == 2)
            assert events[-1]['touched_node_ids'] == ['image']
            await eventually(lambda: not routes.agent_generation_is_running(store, project, context.op_id))
            assert get_operation(db, project, context.op_id)
            await asyncio.sleep(.7)
            assert len(events) == 2
    asyncio.run(run())


def test_media_reservation_error_prevents_mutation_under_store_lock(env, events, monkeypatch):
    from app.services import agent_ops_store
    store, project, db = env
    setup_image(store, project)
    original = store.canvas(project)
    def fail(*args):
        assert store._lock._is_owned()
        raise OSError('reservation unavailable')
    monkeypatch.setattr(agent_ops_store, '_reserve', fail)
    async def run():
        with scope(project, 'canvas.edit_rotate') as context:
            with pytest.raises(HTTPException):
                await routes.rotate_node_image_core(project, 'image', 90)
            assert store.canvas(project) == original
            assert [(e['phase'], e['touched_node_ids']) for e in events] == [
                ('start', ['image']), ('end', [])]
            assert not routes.agent_generation_is_running(store, project, context.op_id)
    asyncio.run(run())


def test_media_sidecar_failure_finishes_without_backfilling(env, events, monkeypatch):
    from app.services import agent_ops_store
    from app.services.agent_ops_undo import undo_ops
    store, project, db = env
    setup_image(store, project)
    append = agent_ops_store.append_operation
    def failing(db, pid, record):
        if record['before']:
            raise OSError('sidecar unavailable')
        return append(db, pid, record)
    monkeypatch.setattr(agent_ops_store, 'append_operation', failing)
    async def run():
        with scope(project, 'canvas.edit_rotate') as context:
            result = await routes.rotate_node_image_core(project, 'image', 90)
            assert result['op_id'] == context.op_id
            assert [e['phase'] for e in events] == ['start', 'end']
            assert events[-1]['touched_node_ids'] == ['image']
            assert not routes.agent_generation_is_running(store, project, context.op_id)
            outcome = undo_ops(store, project, op_id=context.op_id)
            assert outcome['status'] == 'conflict'
            assert 'sidecar append failed' in outcome['error']
    asyncio.run(run())


def test_batch_cancel_during_start_has_empty_end(env, events, monkeypatch):
    store, project, db = env
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {'id': 'a', 'type': 'prompt', 'prompt': 'old'}}],
                          base_revision=store.canvas(project)['revision'])
    store.flush(project)
    original = store.canvas(project)
    broadcast = routes._broadcast_canvas_updated
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(pid, **kwargs):
        await broadcast(pid, **kwargs)
        if kwargs.get('phase') == 'start':
            entered.set()
            await release.wait()
    monkeypatch.setattr(routes, '_broadcast_canvas_updated', blocked)
    async def run():
        with scope(project, 'canvas.batch_edit') as context:
            task = asyncio.create_task(routes.batch_edit_core(project, ['a'], 'new', 'actor'))
            await entered.wait()
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert store.canvas(project) == original
            assert [(e['phase'], e['touched_node_ids']) for e in events] == [
                ('start', ['a']), ('end', [])]
            assert get_operation(db, project, context.op_id) is None
    asyncio.run(run())


def test_upload_rechecks_under_lock_after_start(env, events, monkeypatch):
    store, project, db = env
    source = store.root / project / 'race.png'
    source.write_bytes(png('green'))
    start = routes._broadcast_canvas_updated
    async def racing(project_id, **kwargs):
        await start(project_id, **kwargs)
        if kwargs.get('phase') == 'start':
            await asyncio.to_thread(store.import_media_file, project, str(source), source.name, '',
                                    source_info={'locator': str(source.resolve()), 'name': source.name, 'type': 'local_output'})
    monkeypatch.setattr(routes, '_broadcast_canvas_updated', racing)
    async def run():
        with scope(project, 'media.upload') as context:
            result = await routes.upload_media_core(project, str(source))
            assert 'op_id' not in result
            assert get_operation(db, project, context.op_id) is None
            assert [e['phase'] for e in events] == ['start', 'end']
    asyncio.run(run())


@pytest.mark.parametrize('node_id,prompt', [('missing', 'new'), ('a', 'old')])
def test_batch_noop_or_missing_source_reports_no_touched(env, events, monkeypatch, node_id, prompt):
    store, project, db = env
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {'id': 'a', 'type': 'prompt', 'prompt': 'old'}}],
                          base_revision=store.canvas(project)['revision'])
    store.flush(project)
    def fail(*args, **kwargs):
        raise RuntimeError('no generation')
    monkeypatch.setattr(main, 'route_image_generation', fail)
    async def run():
        with scope(project, 'canvas.batch_edit') as context:
            with pytest.raises((HTTPException, RuntimeError)):
                await routes.batch_edit_core(project, [node_id], prompt, 'actor')
            expected = [] if node_id == 'missing' else [('start', [node_id]), ('end', [])]
            assert [(e['phase'], e['touched_node_ids']) for e in events] == expected
            assert get_operation(db, project, context.op_id) is None
    asyncio.run(run())


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_batch_source_lifecycle_http(env, events, monkeypatch, transport):
    store, project, db = env
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {'id': 'a', 'type': 'prompt', 'prompt': 'old'}}],
                          base_revision=store.canvas(project)['revision'])
    store.flush(project)
    monkeypatch.setattr(main, 'route_image_generation', lambda *a, **k: route_stub())
    monkeypatch.setattr(main, 'load_api_providers', lambda: [])
    monkeypatch.setattr(routes, 'confirm_batch_prompt', AsyncMock())
    monkeypatch.setattr(api_mcp, 'resolve_project', lambda p: p)
    async def provider(_):
        raise RuntimeError('provider failure')
    monkeypatch.setattr(api_project_library, 'run_routed_image_candidate', image_candidate(provider))
    async def run():
        from app.services import agent_host
        host = agent_host.AgentHostManager()
        session = await host.get_or_create_session(project, 'actor')
        session._current_turn_id = 'turn'
        monkeypatch.setattr(agent_host, 'agent_host_manager', host)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://127.0.0.1') as client:
            args = {'node_ids': ['a'], 'prompt': 'new'}
            if transport == 'rest':
                response = await client.post(f'/api/projects/{project}/canvas/batch_edit?agent_node_id=actor',
                                             headers={'X-Agent-Project': project}, json=args)
                assert response.status_code == 200, response.text
                result = response.json()
            else:
                import json
                response = await client.post(f'/api/mcp?project={project}&node=actor&caller=hosted',
                    json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                          'params': {'name': 'canvas.batch_edit', 'arguments': args}})
                payload = response.json()['result']
                assert not payload.get('isError'), payload
                result = json.loads(payload['content'][0]['text'])
            root = [e for e in events if e['op_id'] == result['op_id']]
            assert [(e['phase'], e['touched_node_ids']) for e in root] == [
                ('start', ['a']), ('end', ['a'])]
            for item in result['tasks']:
                await eventually(lambda: main.CANVAS_TASKS[item['task_id']]['status'] in ('failed', 'succeeded'))
    asyncio.run(run())


def test_batch_source_uses_one_root_lifecycle(env, events, monkeypatch):
    store, project, db = env
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {'id': n, 'type': 'prompt', 'prompt': 'old'}}
                                    for n in ('a', 'b')], base_revision=store.canvas(project)['revision'])
    store.flush(project)
    monkeypatch.setattr(main, 'route_image_generation', lambda *a, **k: route_stub())
    monkeypatch.setattr(main, 'load_api_providers', lambda: [])
    async def provider(_):
        raise RuntimeError('provider failure')
    monkeypatch.setattr(api_project_library, 'run_routed_image_candidate', image_candidate(provider))
    async def run():
        with scope(project, 'canvas.batch_edit') as context:
            result = await routes.batch_edit_core(project, ['a', 'b'], 'new', 'actor')
            roots = [e for e in events if e['op_id'] == context.op_id]
            assert [(e['phase'], e['touched_node_ids']) for e in roots] == [
                ('start', ['a', 'b']), ('end', ['a', 'b'])]
            assert not [e for e in events if e['touched_node_ids'] in (['a'], ['b'])]
            assert get_operation(db, project, context.op_id)['touched_node_ids'] == ['a', 'b']
            for task in result['tasks']:
                await eventually(lambda: main.CANVAS_TASKS[task['task_id']]['status'] in ('failed', 'succeeded'))
    asyncio.run(run())
