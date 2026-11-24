"""Offline #493 retrieval contracts; run via an all-roots-isolated QA launcher."""
import asyncio
import gzip
import io
import json
import logging
import os
import random
import socket
import time
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

_REQUIRED_ROOTS = 'HOME USERPROFILE APPDATA LOCALAPPDATA TMP TEMP TMPDIR PI_CODING_AGENT_DIR PI_CODING_AGENT_SESSION_DIR CODEX_HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME XDG_STATE_HOME FIREFLY_RUNTIME_ROOT FIREFLY_PROJECTS_ROOT'.split()
if any(not os.environ.get(key) for key in _REQUIRED_ROOTS):
    raise RuntimeError('All roots must be isolated before importing main in retrieval tests')

import main
from app import core
from app.routers import api_project_library as api
from app.services import result_downloads as rd
from firefly_projects import ProjectStore, InvalidMutation, ProjectStoreError

_REAL_CLIENT = httpx.AsyncClient
PROVIDER = {'id': 'soon-fixture', 'name': 'Fixture', 'base_url': 'https://api.soonstudio.ai/v1', 'protocol': 'openai'}
URL = 'https://cdn.example.invalid/result.png?signature=PRIVATE-URL'


class Body(httpx.AsyncByteStream):
    def __init__(self, content, fail=False):
        self.content, self.fail = content, fail

    async def __aiter__(self):
        yield self.content
        if self.fail:
            raise httpx.ReadError('fixture disconnect, PRIVATE-URL')


def png(large=False):
    image = Image.frombytes('RGB', (320, 320), random.Random(493).randbytes(320 * 320 * 3)) if large else Image.new('RGB', (5, 4), 'green')
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    monkeypatch.setattr(main, 'DATA_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setattr(main.manager, 'broadcast_canvas_updated', AsyncMock())
    monkeypatch.setattr(main, 'get_api_provider_required', lambda *a, **k: dict(PROVIDER))
    monkeypatch.setattr(main, 'api_headers', lambda **k: {'Authorization': 'Bearer synthetic-fixture'})
    monkeypatch.setattr(api, 'get_api_provider', lambda *a: dict(PROVIDER))
    project = store.create_project('Own retrieval fixture')['id']
    store.apply_mutations(project, [{'op': 'upsert_node', 'node': {'id': 'result', 'type': 'smart-image', 'images': []}}], base_revision=0)
    store.begin_image_generation(project, 'result', {'taskId': 'task-local', 'count': 1, 'providerId': PROVIDER['id']}, base_revision=1)
    history = {'id': 'generation-' + uuid.uuid4().hex, 'task_id': 'task-local', 'result_node_id': 'result',
               'created_at': round(time.time() * 1000), 'actual_provider_id': PROVIDER['id'],
               'actual_model': 'fixture-model', 'outputs': [], 'prompt': 'fixture'}
    queue = rd.downloads(main)
    requests = []
    async def dns(hostname, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]
    monkeypatch.setattr(core, '_resolve_generation_host', dns)
    delays = []
    async def sleep(delay):
        delays.append(delay)
    monkeypatch.setattr(rd.asyncio, 'sleep', sleep)
    yield SimpleNamespace(store=store, project=project, history=history, queue=queue, requests=requests,
                          monkeypatch=monkeypatch, delays=delays, tmp=tmp_path)
    store.close()


def network(f, handler):
    def recorded(request):
        f.requests.append(request)
        assert request.method == 'GET', 'Retrieval must never submit generation'
        return handler(request)
    f.monkeypatch.setattr(rd.httpx, 'AsyncClient', lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(recorded), **kw))


async def checkpoint(f, data=None, **metadata):
    candidate = {'type': 'url', 'value': URL, **metadata}
    ids = await f.queue.checkpoint(f.project, f.history, [candidate], {'task_id': 'upstream-original', 'data': [{'url': URL, **metadata}]}, PROVIDER)
    return next(iter(ids.values()))


def test_checkpoint_before_get_private_url_and_exact_ownership(fixture, caplog):
    f = fixture
    caplog.set_level(logging.INFO, logger='httpx')
    content = png()
    def respond(request):
        canvas = f.store.canvas(f.project)
        record = canvas['history'][0]
        assert record['status'] == 'generated_pending_download'
        assert record['download']['upstream_task_id'] == 'upstream-original'
        assert 'PRIVATE-URL' not in json.dumps(canvas)
        persisted = f.queue.load(f.history['id'])
        assert persisted['items'][0]['expected_bytes'] == len(content)
        assert persisted['items'][0]['expires_at'] == persisted['items'][0]['received_at'] + 86400
        assert request.headers['host'] == 'cdn.example.invalid'
        assert request.extensions['sni_hostname'] == 'cdn.example.invalid'
        return httpx.Response(200, headers={'Content-Length': str(len(content)), 'ETag': '"v1"'}, stream=Body(content))
    network(f, respond)
    async def run():
        await checkpoint(f, expected_bytes=len(content))
        result = await f.queue.resume(f.project, f.history['id'])
        assert result['status'] == 'succeeded'
    asyncio.run(run())
    record = f.store.canvas(f.project)['history'][0]
    original = Path(f.store.media_record(f.project, record['outputs'][0]['asset_id'])['path'])
    assert original.read_bytes() == content
    assert 'PRIVATE-URL' not in json.dumps(f.store.list_media(f.project))
    assert len(f.requests) == 1
    assert 'PRIVATE-URL' not in caplog.text


@pytest.mark.parametrize('range_supported', [True, False])
def test_midstream_disconnect_resume_or_restart(fixture, range_supported):
    f = fixture
    content = png(True)
    def respond(request):
        if len(f.requests) == 1:
            return httpx.Response(200, headers={'Content-Length': str(len(content)), 'ETag': '"v1"'}, stream=Body(content[:131072], True))
        assert request.headers['Range'] == 'bytes=131072-'
        assert request.headers['If-Range'] == '"v1"'
        if range_supported:
            return httpx.Response(206, headers={'Content-Length': str(len(content) - 131072), 'Content-Range': f'bytes 131072-{len(content)-1}/{len(content)}', 'ETag': '"v1"'}, stream=Body(content[131072:]))
        return httpx.Response(200, headers={'Content-Length': str(len(content)), 'ETag': '"v2"'}, stream=Body(content))
    network(f, respond)
    async def run():
        await checkpoint(f)
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'succeeded'
    asyncio.run(run())
    assert f.delays == [1]
    record = f.store.canvas(f.project)['history'][0]
    assert Path(f.store.media_record(f.project, record['outputs'][0]['asset_id'])['path']).read_bytes() == content


def test_corrupt_image_never_committed_and_exact_retry_budget(fixture):
    f = fixture
    network(f, lambda request: httpx.Response(200, headers={'Content-Length': '9', 'ETag': '"bad"'}, stream=Body(b'not image')))
    async def run():
        await checkpoint(f)
        result = await f.queue.resume(f.project, f.history['id'])
        assert result['status'] == 'download_failed'
    asyncio.run(run())
    assert len(f.requests) == 4
    assert f.delays == [1, 3, 10]
    assert not f.store.list_media(f.project)
    assert not list((f.store.root / f.project / 'media').glob('*.png'))
    assert not list((f.store.root / f.project / 'media').glob('.result-*.part'))


def test_gzip_raw_length_separate_from_decoded_expected(fixture):
    f = fixture
    content = png(True)
    compressed = gzip.compress(content)
    network(f, lambda req: httpx.Response(200, headers={'Content-Length': str(len(compressed)), 'Content-Encoding': 'gzip'}, stream=Body(compressed)))
    async def run():
        await checkpoint(f, expected_bytes=len(content))
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'succeeded'
    asyncio.run(run())


@pytest.mark.parametrize('bad_range', ['wrong-etag', 'wrong-range', 'short-body'])
def test_range_validation_does_not_append_corrupt_representation(fixture, bad_range):
    f = fixture
    content = png(True)
    def respond(req):
        if len(f.requests) == 1:
            return httpx.Response(200, headers={'ETag': '"v1"', 'Content-Length': str(len(content))}, stream=Body(content[:131072], True))
        if len(f.requests) == 2:
            return httpx.Response(206, headers={'ETag': '"v2"' if bad_range == 'wrong-etag' else '"v1"',
                'Content-Range': f'bytes {0 if bad_range == "wrong-range" else 131072}-{len(content)-1}/{len(content)}',
                'Content-Length': str(len(content)-131072)}, stream=Body(content[131072:-1] if bad_range == 'short-body' else content[131072:]))
        return httpx.Response(200, headers={'ETag': '"v3"', 'Content-Length': str(len(content))}, stream=Body(content))
    network(f, respond)
    async def run():
        await checkpoint(f)
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'succeeded'
    asyncio.run(run())
    record = f.store.canvas(f.project)['history'][0]
    assert Path(f.store.media_record(f.project, record['outputs'][0]['asset_id'])['path']).read_bytes() == content


def test_restart_retains_partial_and_resumes_without_generation(fixture):
    f = fixture
    content = png(True)
    network(f, lambda req: httpx.Response(200, headers={'ETag': '"v1"', 'Content-Length': str(len(content))}, stream=Body(content[:131072], True)))
    async def fail():
        await checkpoint(f)
        await f.queue.resume(f.project, f.history['id'])
    asyncio.run(fail())
    assert list((f.store.root / f.project / 'media').glob('.result-*.part'))
    f.store.flush(f.project)
    f.store.close()
    restarted = ProjectStore(f.store.root, snapshot_delay=0)
    f.monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', restarted)
    f.queue = rd.ResultDownloads(f.queue.root, main)
    def respond(req):
        assert req.headers['Range'] == 'bytes=131072-'
        return httpx.Response(206, headers={'ETag': '"v1"', 'Content-Length': str(len(content)-131072), 'Content-Range': f'bytes 131072-{len(content)-1}/{len(content)}'}, stream=Body(content[131072:]))
    network(f, respond)
    asyncio.run(f.queue.startup())
    assert restarted.canvas(f.project)['history'][0]['status'] == 'succeeded'
    restarted.close()


def test_expired_recovers_original_id_only_and_provider_config_change_blocks(fixture):
    f = fixture
    content = png()
    def respond(req):
        if 'images/tasks/' in req.url.path:
            assert req.url.path.endswith('/upstream-original')
            return httpx.Response(200, json={'data': [{'url': URL, 'expires_in': 600}]})
        return httpx.Response(200, headers={'Content-Length': str(len(content))}, stream=Body(content))
    network(f, respond)
    async def run():
        await checkpoint(f, expires_at=time.time()-1)
        assert (await f.queue.resume(f.project, f.history['id'], refresh=api._recover_project_image_result_link))['status'] == 'succeeded'
    asyncio.run(run())
    assert len(f.requests) == 2


@pytest.mark.parametrize('reason', ['no-task', 'changed-provider', 'changed-credentials', 'not-supported'])
def test_expired_no_recovery_never_generates(fixture, reason):
    f = fixture
    network(f, lambda req: pytest.fail('Must not contact another endpoint'))
    async def run():
        await checkpoint(f, expires_at=time.time()-1)
        q = f.queue.load(f.history['id'])
        if reason == 'no-task':
            q['upstream_task_id'] = ''
            q['items'][0]['upstream_task_id'] = ''
        elif reason == 'changed-provider':
            f.monkeypatch.setattr(main, 'get_api_provider_required', lambda *a, **k: {**PROVIDER, 'base_url': 'https://other.invalid'})
        elif reason == 'changed-credentials':
            f.monkeypatch.setattr(main, 'api_headers', lambda **k: {'Authorization': 'Bearer another-account'})
        else:
            q['provider_identity'] = 'not-soon'
        f.queue.save(q)
        result = await f.queue.resume(f.project, f.history['id'], refresh=api._recover_project_image_result_link)
        assert result['error'] == rd.EXPIRED
    asyncio.run(run())
    assert not f.requests


def test_deleted_node_saves_only_history_and_client_cannot_inject_url(fixture):
    f = fixture
    content = png()
    network(f, lambda req: httpx.Response(200, headers={'Content-Length': str(len(content))}, stream=Body(content)))
    async def run():
        await checkpoint(f)
        canvas = f.store.canvas(f.project)
        f.store.apply_mutations(f.project, [{'op': 'remove_node', 'node_id': 'result'}], base_revision=canvas['revision'], force_empty=True)
        result = await f.queue.resume(f.project, f.history['id'])
        assert result['status'] == 'succeeded'
        saved = f.store.canvas(f.project)
        assert not saved['nodes']
        assert len(saved['history'][0]['outputs']) == 1
        assert len(f.requests) == 1
        async with _REAL_CLIENT(transport=httpx.ASGITransport(app=main.app), base_url='http://127.0.0.1:3197', headers={'origin': 'http://127.0.0.1:3197'}) as client:
            response = await client.post(f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload', json={'url': 'http://127.0.0.1'})
            assert response.status_code == 422
    asyncio.run(run())


def test_concurrent_click_exact_idempotency(fixture):
    f = fixture
    content = png()
    network(f, lambda req: httpx.Response(200, headers={'Content-Length': str(len(content))}, stream=Body(content)))
    async def run():
        await checkpoint(f)
        async with _REAL_CLIENT(transport=httpx.ASGITransport(app=main.app), base_url='http://127.0.0.1:3197', headers={'origin': 'http://127.0.0.1:3197'}) as client:
            url = f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload'
            results = await asyncio.gather(client.post(url), client.post(url))
            assert all(response.status_code == 200 for response in results)
            assert any(response.json()['status'] == 'succeeded' for response in results)
    asyncio.run(run())
    assert len(f.requests) == 1
    assert len(f.store.canvas(f.project)['history'][0]['outputs']) == 1


@pytest.mark.parametrize('status', [403, 404])
def test_forbidden_or_missing_url_refreshes_original_task(fixture, status):
    f = fixture
    content = png()
    def respond(req):
        if 'images/tasks/' in req.url.path:
            return httpx.Response(200, json={'data': [{'url': URL + '-new', 'expires_in': 60}]})
        if str(req.url).endswith('PRIVATE-URL'):
            return httpx.Response(status)
        return httpx.Response(200, headers={'Content-Length': str(len(content))}, stream=Body(content))
    network(f, respond)
    async def run():
        await checkpoint(f)
        assert (await f.queue.resume(f.project, f.history['id'], refresh=api._recover_project_image_result_link))['status'] == 'succeeded'
    asyncio.run(run())
    assert len(f.requests) == 3
    assert all(req.method == 'GET' for req in f.requests)


@pytest.mark.parametrize('metadata', [{'expected_bytes': 1}, {'sha256': '0' * 64}])
def test_expected_length_or_hash_failure_never_imports(fixture, metadata):
    f = fixture
    content = png()
    network(f, lambda req: httpx.Response(200, headers={'Content-Length': str(len(content))}, stream=Body(content)))
    async def run():
        await checkpoint(f, **metadata)
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'download_failed'
    asyncio.run(run())
    assert not f.store.list_media(f.project)
    assert len(f.requests) == 4


def test_cancelled_task_cannot_be_resurrected(fixture):
    f = fixture
    network(f, lambda req: pytest.fail('Cancelled task must never download'))
    async def run():
        await checkpoint(f)
        record = {**f.history, 'status': 'cancelled'}
        f.store.cancel_image_generation(f.project, 'result', 'task-local', record)
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'unavailable'
    asyncio.run(run())


def test_recreated_node_cannot_inject_original_pending_claim(fixture):
    f = fixture
    network(f, lambda req: pytest.fail('Recreated node must never download'))
    async def run():
        await checkpoint(f)
        canvas = f.store.canvas(f.project)
        old = canvas['nodes'][0]
        f.store.apply_mutations(f.project, [{'op': 'remove_node', 'node_id': 'result'}], base_revision=canvas['revision'], force_empty=True)
        canvas = f.store.canvas(f.project)
        f.store.apply_mutations(f.project, [{'op': 'upsert_node', 'node': old}], base_revision=canvas['revision'])
        assert not f.store.canvas(f.project)['nodes'][0].get('pendingTasks')
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'unavailable'
    asyncio.run(run())


def test_deleted_history_never_resurrects(fixture):
    f = fixture
    network(f, lambda req: pytest.fail('Deleted history must not download'))
    async def run():
        await checkpoint(f)
        f.store.delete_history_record(f.project, f.history['id'], base_revision=f.store.canvas(f.project)['revision'])
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'unavailable'
        assert not f.store.canvas(f.project)['history']
    asyncio.run(run())


def test_restart_repairs_history_before_canvas_checkpoint_window(fixture):
    f = fixture
    content = png()
    network(f, lambda req: httpx.Response(200, headers={'Content-Length': str(len(content))}, stream=Body(content)))
    async def run():
        await checkpoint(f)
        with f.store._lock:
            f.store._canvases[f.project]['nodes'][0]['pendingTasks'][0].pop('download_generation')
        f.store.flush(f.project)
        f.store.close()
        restarted_store = ProjectStore(f.store.root, snapshot_delay=0)
        f.monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', restarted_store)
        try:
            await rd.ResultDownloads(f.queue.root, main).startup()
            assert restarted_store.canvas(f.project)['history'][0]['status'] == 'succeeded'
        finally:
            restarted_store.close()
    asyncio.run(run())


def test_changed_project_library_cannot_adopt_original_queue(fixture):
    f = fixture
    network(f, lambda req: pytest.fail('Another project library must not download'))
    async def run():
        await checkpoint(f)
        other = ProjectStore(f.tmp / 'other-library', snapshot_delay=0)
        f.monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', other)
        try:
            with pytest.raises(InvalidMutation, match='原项目库'):
                await f.queue.resume(f.project, f.history['id'])
        finally:
            other.close()
    asyncio.run(run())


def test_restart_after_archive_before_canvas_commit_completes_without_network(fixture):
    f = fixture
    content = png()
    network(f, lambda req: httpx.Response(200, headers={'Content-Length': str(len(content))}, stream=Body(content)))
    async def initial():
        item_id = await checkpoint(f)
        await f.queue.archive(f.history['id'], item_id)
    asyncio.run(initial())
    network(f, lambda req: pytest.fail('Already archived output must not be downloaded again'))
    restarted = rd.ResultDownloads(f.queue.root, main)
    asyncio.run(restarted.startup())
    assert f.store.canvas(f.project)['history'][0]['status'] == 'succeeded'


def test_multiple_provider_tasks_recover_each_original_id_and_output_index(fixture):
    f = fixture
    content = png()
    def respond(req):
        if 'images/tasks/' in req.url.path:
            task_id = req.url.path.rsplit('/', 1)[1]
            return httpx.Response(200, json={'data': [{'url': URL + '-' + task_id}]})
        return httpx.Response(200, headers={'Content-Length': str(len(content))}, stream=Body(content))
    network(f, respond)
    async def run():
        for task_id in ('original-one', 'original-two'):
            candidate = {'type': 'url', 'value': URL + '-' + task_id}
            await f.queue.checkpoint(f.project, f.history, [candidate],
                {'task_id': task_id, 'data': [{'url': candidate['value'], 'expires_at': time.time()-1}]}, PROVIDER)
        result = await f.queue.resume(f.project, f.history['id'], refresh=api._recover_project_image_result_link)
        assert result['status'] == 'succeeded'
    asyncio.run(run())
    task_paths = [req.url.path for req in f.requests if 'images/tasks/' in req.url.path]
    assert task_paths == ['/v1/images/tasks/original-one', '/v1/images/tasks/original-two']
    outputs = f.store.canvas(f.project)['history'][0]['outputs']
    assert len(outputs) == 2 and outputs[0]['output_id'] != outputs[1]['output_id']


def test_generation_download_failure_and_redownload_endpoint_never_resubmits(fixture):
    f = fixture
    content = png()
    broken = [True]
    def respond(req):
        return httpx.Response(200, headers={'Content-Length': str(len(content))},
                              stream=Body(content[:10], True) if broken[0] else Body(content))
    network(f, respond)
    generate = AsyncMock(return_value=({'type': 'url', 'value': URL}, {'task_id': 'upstream-original', 'data': [{'url': URL}]}))
    f.monkeypatch.setattr(api, 'run_routed_image_candidate', generate)
    payload = core.ProjectImageGenerationRequest(prompt='fixture', result_node_id='result', provider_id=PROVIDER['id'], model='fixture-model', base_revision=2)
    route = SimpleNamespace(provider_id=PROVIDER['id'], provider_name='Fixture', model='fixture-model', reason='manual',
                            to_dict=lambda: {'provider_id': PROVIDER['id'], 'model': 'fixture-model'})
    core.CANVAS_TASKS['task-local'] = {'id': 'task-local', 'type': 'project-online-image', 'project_id': f.project,
        'status': 'queued', 'created_at': time.time(), 'result_node_id': 'result'}
    async def run():
        await api.run_project_image_task('task-local', f.project, payload, api.project_image_request(payload), route, 2)
        assert core.CANVAS_TASKS['task-local']['status'] == 'download_failed'
        record = f.store.canvas(f.project)['history'][0]
        assert record['status'] == 'download_failed'
        assert len(f.requests) == 4
        broken[0] = False
        async with _REAL_CLIENT(transport=httpx.ASGITransport(app=main.app), base_url='http://127.0.0.1:3197', headers={'origin': 'http://127.0.0.1:3197'}) as client:
            url = f'/api/project-library/{f.project}/image-result-downloads/{record["id"]}/redownload'
            response = await client.post(url)
            assert response.status_code == 200, response.text
            assert response.json()['status'] == 'succeeded'
            again = await client.post(url)
            assert again.json()['status'] == 'succeeded'
            task = await client.get(f'/api/project-library/{f.project}/image-generation-tasks/task-local')
            assert task.json()['status'] == 'succeeded'
    try:
        asyncio.run(run())
        assert generate.await_count == 1
        assert len(f.requests) == 5
    finally:
        core.CANVAS_TASKS.pop('task-local', None)


def test_agent_original_journal_writer_recovers_without_host_or_generation(fixture):
    f = fixture
    from app.routers import api_agent_tools as agents
    from app.services.agent_ops_context import agent_ops_scope
    content = png()
    broken = [True]
    network(f, lambda req: httpx.Response(200, headers={'Content-Length': str(len(content))},
        stream=Body(content[:10], True) if broken[0] else Body(content)))
    generate = AsyncMock(return_value=({'type': 'url', 'value': URL}, {'task_id': 'upstream-original', 'data': [{'url': URL}]}))
    f.monkeypatch.setattr(api, 'run_routed_image_candidate', generate)
    route = SimpleNamespace(provider_id=PROVIDER['id'], provider_name='Fixture', model='fixture-model', reason='manual',
                            to_dict=lambda: {'provider_id': PROVIDER['id'], 'model': 'fixture-model'})
    f.monkeypatch.setattr(main, 'route_image_generation', lambda *a, **k: route)
    f.monkeypatch.setattr(main, 'load_api_providers', lambda **k: [{**PROVIDER, 'image_models': ['fixture-model']}])
    canvas = f.store.canvas(f.project)
    f.store.apply_mutations(f.project, [{'op': 'upsert_node', 'node': {'id': 'source', 'type': 'prompt', 'text': 'fixture',
        'runSettings': {'provider_id': PROVIDER['id'], 'model': 'fixture-model'}}}], base_revision=canvas['revision'])
    async def run():
        with agent_ops_scope(project_id=f.project, agent_node_id='actor', turn_id='fixture-turn',
                             tool='generate.trigger', args={'node_id': 'source'}):
            started = await agents.trigger_generation_core(f.project, 'source', agent_node_id='actor')
        worker = core.PROJECT_IMAGE_GENERATION_FUTURES[started['task_id']]
        await asyncio.wait_for(worker, 10)
        task = core.CANVAS_TASKS[started['task_id']]
        assert task['status'] == 'download_failed' and task['agent_settled'] is True
        queue = f.queue.find_task(f.project, started['task_id'])
        assert queue is not None
        broken[0] = False
        result = await f.queue.resume(f.project, queue['history_id'])
        assert result['status'] == 'succeeded'
        assert core.CANVAS_TASKS[started['task_id']]['status'] == 'succeeded'
        assert f.store.find_canvas_node(f.project, started['result_node_id'])['images']
    asyncio.run(run())
    assert generate.await_count == 1
    assert len(f.requests) == 5


def test_deleted_partial_output_is_not_resurrected_from_private_queue(fixture):
    f = fixture
    content = png()
    network(f, lambda req: httpx.Response(200, stream=Body(content)))
    async def run():
        item_id = await checkpoint(f)
        output = await f.queue.archive(f.history['id'], item_id)
        f.store.complete_image_generation(f.project, 'result', [output],
            {**f.history, 'status': 'running', 'outputs': [output]},
            task_id='task-local', require_pending=True, partial=True)
        f.store.delete_generated_original(f.project, output['asset_id'],
            base_revision=f.store.canvas(f.project)['revision'], history_id=f.history['id'],
            output_id=output['output_id'], output_index=0)
        network(f, lambda req: pytest.fail('Deleted successful output must not be downloaded again'))
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'succeeded'
        canvas = f.store.canvas(f.project)
        assert not canvas['history'][0]['outputs']
        assert not canvas['nodes'][0]['images']
        assert not f.store.list_media(f.project)
    asyncio.run(run())


def test_successful_slot_not_redownloaded_when_sibling_retries(fixture):
    f = fixture
    content = png()
    broken = [True]
    def respond(req):
        if req.url.params.get('slot') == 'second' and broken[0]:
            return httpx.Response(200, stream=Body(b'not an image'))
        return httpx.Response(200, stream=Body(content))
    network(f, respond)
    async def run():
        await f.queue.checkpoint(f.project, f.history,
            [{'type': 'url', 'value': URL}, {'type': 'url', 'value': URL + '&slot=second'}],
            {'task_id': 'upstream-original'}, PROVIDER)
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'download_failed'
        first = f.queue.load(f.history['id'])['items'][0]['output'].copy()
        assert len(f.requests) == 5
        broken[0] = False
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'succeeded'
        outputs = f.store.canvas(f.project)['history'][0]['outputs']
        assert outputs[0]['output_id'] == first['output_id']
        assert outputs[0]['asset_id'] == first['asset_id']
        assert len(outputs) == 2 and len(f.requests) == 6
    asyncio.run(run())


def test_disk_full_never_commits_partial_and_remains_recoverable(fixture):
    f = fixture
    content = png()
    network(f, lambda req: httpx.Response(200, stream=Body(content)))
    original_open = Path.open
    def disk_full(path, mode='r', *args, **kwargs):
        if path.name.endswith('.part') and 'w' in mode:
            raise OSError('synthetic disk full')
        return original_open(path, mode, *args, **kwargs)
    async def run():
        await checkpoint(f)
        with f.monkeypatch.context() as patch:
            patch.setattr(Path, 'open', disk_full)
            assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'download_failed'
            assert not f.store.list_media(f.project)
            assert not f.store.canvas(f.project)['history'][0]['outputs']
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'succeeded'
    asyncio.run(run())
    assert len(f.requests) == 5


def test_project_contained_lazy_private_queue_excluded_from_fly(fixture):
    f = fixture
    folder = f.store.root / f.project / 'private-result-downloads'
    assert not folder.exists()
    asyncio.run(checkpoint(f))
    assert len(list(folder.glob('generation-*.json'))) == 1
    archive = f.tmp / 'fixture.fly'
    f.store.export_fly_to(f.project, archive)
    with zipfile.ZipFile(archive) as package:
        assert not any('private-result-downloads' in name or '.part' in name for name in package.namelist())
        assert all(b'PRIVATE-URL' not in package.read(name) for name in package.namelist())


def test_estimated_expiry_is_not_a_provider_guarantee_or_terminal_cutoff(fixture):
    f = fixture
    content = png()
    network(f, lambda req: httpx.Response(200, stream=Body(content)))
    async def run():
        await checkpoint(f)
        queue = f.queue.load(f.history['id'])
        assert f.queue.public(queue)['expiry_estimated'] is True
        queue['items'][0]['expires_at'] = time.time() - 1
        f.queue.save(queue)
        assert (await f.queue.resume(f.project, f.history['id']))['status'] == 'succeeded'
    asyncio.run(run())
    assert len(f.requests) == 1


def test_restart_expired_result_queries_pinned_original_task(fixture, caplog):
    f = fixture
    content = png()
    caplog.set_level(logging.DEBUG)
    def respond(req):
        if 'images/tasks/' in req.url.path:
            assert req.headers['host'] == 'api.soonstudio.ai'
            assert req.extensions['sni_hostname'] == 'api.soonstudio.ai'
            assert req.headers['authorization'] == 'Bearer synthetic-fixture'
            return httpx.Response(200, json={'data': [{'url': URL}]})
        assert 'authorization' not in req.headers
        return httpx.Response(200, stream=Body(content))
    network(f, respond)
    async def run():
        await checkpoint(f, expires_at=time.time()-1)
        await f.queue.startup(refresh=api._recover_project_image_result_link)
    asyncio.run(run())
    assert f.store.canvas(f.project)['history'][0]['status'] == 'succeeded'
    assert len(f.requests) == 2
    assert 'PRIVATE-URL' not in caplog.text and 'synthetic-fixture' not in caplog.text


def test_recovery_query_rejects_private_dns_without_http(fixture):
    f = fixture
    network(f, lambda req: pytest.fail('Private recovery query must not reach HTTP'))
    async def private_dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
    f.monkeypatch.setattr(core, '_resolve_generation_host', private_dns)
    async def run():
        await checkpoint(f, expires_at=time.time()-1)
        result = await f.queue.resume(f.project, f.history['id'], refresh=api._recover_project_image_result_link)
        assert result['error'] == rd.EXPIRED
    asyncio.run(run())
    assert not f.requests


def test_controlled_http_disconnect_then_verified_range(tmp_path):
    content = png(True)
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            seen.append(dict(self.headers))
            offset = 131072 if self.headers.get('Range') else 0
            self.send_response(206 if offset else 200)
            self.send_header('ETag', '"fixture-v1"')
            self.send_header('Content-Length', str(len(content)-offset))
            if offset:
                self.send_header('Content-Range', f'bytes {offset}-{len(content)-1}/{len(content)}')
            self.end_headers()
            self.wfile.write(content[offset:] if offset else content[:131072])
            self.wfile.flush()
            self.close_connection = True
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    path = tmp_path / 'controlled.part'
    state = {}
    async def resolve(url):
        return url, {}, {}
    async def run():
        url = f'http://127.0.0.1:{server.server_port}/synthetic.png'
        with pytest.raises(httpx.RemoteProtocolError):
            await rd.stream_result(url, path, state, lambda: None, resolve=resolve)
        assert path.stat().st_size == 131072
        await rd.stream_result(url, path, state, lambda: None, resolve=resolve)
    try:
        asyncio.run(run())
        assert path.read_bytes() == content
        assert seen[1]['Range'] == 'bytes=131072-'
        assert seen[1]['If-Range'] == '"fixture-v1"'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_ssrf_validation_still_rejects_private_dns(fixture):
    f = fixture
    async def private_dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
    f.monkeypatch.setattr(core, '_resolve_generation_host', private_dns)
    with pytest.raises(ProjectStoreError, match='私有网络'):
        asyncio.run(core.ensure_public_generation_target(URL))
