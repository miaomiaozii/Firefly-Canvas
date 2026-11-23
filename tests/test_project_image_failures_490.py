"""#490: classify at execution boundaries; archiving cannot authorize a paid replay.

Source: review-batch-ui/MESSAGES.md:3721,3732 (reviewer 01a1ef85).
These tests run the actual async worker with a real isolated ProjectStore.
"""
import asyncio
import base64
import io
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from PIL import Image
from fastapi import HTTPException
import pytest

import main
from app.routers import api_project_library as library
from firefly_projects import ProjectStore, ProjectStoreError


def http_failure():
    request = httpx.Request('POST', 'https://provider.invalid/images/generations')
    response = httpx.Response(503, text='upstream unavailable', request=request)
    return httpx.HTTPStatusError('upstream unavailable', request=request, response=response)


def wrapped_failure(cause):
    error = HTTPException(status_code=502, detail='adapter reported failure')
    error.__cause__ = cause
    return error


@pytest.fixture
def worker(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Network/native CLI forbidden in #490 regression')

    # Windows asyncio needs a self-pipe; create only that pair before denying connects.
    self_pipe = socket.socketpair()
    monkeypatch.setattr(socket, 'socketpair', lambda *args, **kwargs: self_pipe)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(httpx.AsyncClient, 'send', denied)
    monkeypatch.setattr(httpx.Client, 'send', denied)
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    monkeypatch.setattr(library.manager, 'broadcast_canvas_updated', AsyncMock())
    monkeypatch.setattr(main, 'load_api_providers', lambda **kwargs: [])
    project_id = store.create_project('e2e-tabbit-490-classification')['id']
    store.apply_mutations(project_id, [{'op': 'upsert_node', 'node': {
        'id': 'result', 'type': 'smart-image', 'images': [], 'x': 120, 'y': 80,
    }}], base_revision=0)
    png = io.BytesIO()
    Image.new('RGB', (3, 2), (40, 100, 180)).save(png, format='PNG')
    image = {'type': 'b64', 'value': base64.b64encode(png.getvalue()).decode('ascii')}
    route = SimpleNamespace(provider_id='offline-490', provider_name='Offline', model='fixture',
                            reason='fixture', to_dict=lambda: {'provider_id': 'offline-490', 'model': 'fixture'})
    task_id = f'project_img_490_{project_id}'
    state = SimpleNamespace(store=store, pid=project_id, image=image, task_id=task_id,
                            calls=0, archives=[], complete_attempts=0)
    real_archive = library.archive_project_generated_image
    original_complete = store.complete_image_generation
    original_import = store.import_media

    async def run(outcomes, *, archive_failures=(), final_failures=0, partial_failure=False, prompt='offline fixture'):
        payload = main.ProjectImageGenerationRequest(
            prompt=prompt, provider_id=route.provider_id, model=route.model,
            n=len(outcomes), result_node_id='result', base_revision=1,
        )
        request = library.project_image_request(payload)
        registered = store.begin_image_generation(project_id, 'result', {
            'taskId': task_id, 'kind': 'image', 'count': len(outcomes),
            'requestPayload': payload.model_dump(), 'route': route.to_dict(),
        }, base_revision=1)
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS[task_id] = {'id': task_id, 'project_id': project_id, 'status': 'queued'}

        archive_failed_contents = set()

        async def provider(*args, **kwargs):
            index = state.calls
            state.calls += 1
            outcome = outcomes[index] if index < len(outcomes) else 'unexpected-paid-replay'
            if isinstance(outcome, BaseException):
                raise outcome
            content = io.BytesIO()
            Image.new('RGB', (3, 2), (40 + index, 100, 180)).save(content, format='PNG')
            if index in archive_failures:
                archive_failed_contents.add(content.getvalue())
            return {**image, 'value': base64.b64encode(content.getvalue()).decode('ascii')}, {}

        def import_media(pid, content, *args, **kwargs):
            if content in archive_failed_contents:
                raise ProjectStoreError('archive disk unavailable')
            return original_import(pid, content, *args, **kwargs)

        async def archive(pid, candidate, index):
            item = await real_archive(pid, candidate, index)
            state.archives.append(item)
            return item

        def complete(*args, **kwargs):
            if kwargs.get('partial'):
                if partial_failure:
                    raise OSError('partial store unavailable')
            else:
                state.complete_attempts += 1
                if state.complete_attempts <= final_failures:
                    raise OSError('final store unavailable')
            return original_complete(*args, **kwargs)

        monkeypatch.setattr(library, 'run_routed_image_candidate', provider)
        monkeypatch.setattr(library, 'archive_project_generated_image', archive)
        monkeypatch.setattr(store, 'complete_image_generation', complete)
        monkeypatch.setattr(store, 'import_media', import_media)
        await library.run_project_image_task(task_id, project_id, payload, request, route, registered['revision'])
        state.task = main.CANVAS_TASKS[task_id]
        state.canvas = store.open_project(project_id)['canvas']
        state.node = next(node for node in state.canvas['nodes'] if node['id'] == 'result')
        state.history = state.canvas['history'][-1]
        return state

    state.run = run
    try:
        yield state
    finally:
        with main.CANVAS_TASK_LOCK:
            main.CANVAS_TASKS.pop(task_id, None)
            main.PROJECT_IMAGE_GENERATION_ORPHANS.pop(project_id, None)
        store.close()
        for sock in self_pipe:
            sock.close()


def assert_failed_with_saved_output(state, expected_error, count=1):
    assert state.task['status'] == state.history['status'] == 'failed'
    assert state.task['error'] == state.history['error'] == expected_error
    assert len(state.archives) == count
    expected_assets = [item['asset_id'] for item in state.archives]
    assert [item['asset_id'] for item in state.node['images']] == expected_assets
    assert [item['asset_id'] for item in state.history['outputs']] == expected_assets
    assert [item['asset_id'] for item in state.task['result']['final_node']['images']] == expected_assets
    assert not state.node.get('pendingTasks') and state.node['pending'] == 0
    for item in state.history['outputs']:
        assert item['output_id'] in {image['output_id'] for image in state.node['images']}
        assert state.store.media_record(state.pid, item['asset_id'])['kind'] == 'image'


@pytest.mark.parametrize('failure,label,reason', [
    (http_failure(), '服务商请求失败', 'HTTP 503：服务商暂时无法完成请求；upstream unavailable'),
    (httpx.ReadError('TLS bad record mac'), '服务商连接或响应接收失败', 'ReadError：TLS bad record mac'),
    (httpx.ReadError('response interrupted'), '服务商连接或响应接收失败', 'ReadError：response interrupted'),
    (httpx.ConnectError('TLS handshake failed'), '服务商连接或响应接收失败', 'ConnectError：TLS handshake failed'),
    (httpx.RemoteProtocolError('peer disconnected'), '服务商连接或响应接收失败', 'RemoteProtocolError：peer disconnected'),
    (RuntimeError('Connection error: untyped'), '服务商生成失败', 'Connection error: untyped'),
    (wrapped_failure(http_failure()), '服务商请求失败', 'HTTP 503：服务商暂时无法完成请求；upstream unavailable'),
    (wrapped_failure(httpx.ReadError('TLS bad record mac')), '服务商连接或响应接收失败', 'ReadError：TLS bad record mac'),
])
def test_partial_provider_failures_are_not_local_writeback(worker, failure, label, reason):
    state = asyncio.run(worker.run(['success', failure]))
    assert state.calls == 2
    assert_failed_with_saved_output(state, f'1 张图片{label}（1/2 已写回画布）：{reason}')


@pytest.mark.parametrize('count', [1, 2, 8])
def test_archive_failure_never_replays_paid_candidate(worker, count):
    """#490 replaces archive-compensation with exactly n provider submissions, not n+1."""
    state = asyncio.run(worker.run(['success'] * count, archive_failures=(count - 1,)))
    assert state.calls == count
    if count == 1:
        assert state.task['status'] == 'failed'
        assert state.task['error'] == state.history['error'] == '图片下载或归档失败：archive disk unavailable'
        assert state.history['outputs'] == state.node['images'] == []
        assert not state.node.get('pendingTasks')
    else:
        assert_failed_with_saved_output(state,
            f'1 张图片下载或归档失败（{count - 1}/{count} 已写回画布）：archive disk unavailable', count=count - 1)


def test_mixed_http_and_archive_failures_keep_both_causes_and_real_success(worker):
    state = asyncio.run(worker.run(['success', http_failure(), 'success'], archive_failures=(2,)))
    assert state.calls == 3
    assert_failed_with_saved_output(state,
        '1 张图片服务商请求失败；1 张图片下载或归档失败（1/3 已写回画布）：'
        'HTTP 503：服务商暂时无法完成请求；upstream unavailable；archive disk unavailable')


def test_final_store_retry_exhaustion_is_writeback_not_provider_failure(worker):
    state = asyncio.run(worker.run(['success', 'success'], final_failures=3))
    assert state.calls == 2 and state.complete_attempts == 3
    assert_failed_with_saved_output(state, '图片写回失败：final store unavailable', count=2)


def test_local_partial_and_final_store_retries_do_not_replay_provider(worker):
    state = asyncio.run(worker.run(['success', 'success'], final_failures=2, partial_failure=True))
    assert state.calls == 2 and state.complete_attempts == 3
    assert state.task['status'] == state.history['status'] == 'succeeded'
    assert state.task['error'] == ''
    assert len(state.node['images']) == len(state.history['outputs']) == 2
    assert not state.node.get('pendingTasks')


def test_provider_reason_and_worker_logs_redact_sensitive_values(worker, caplog, capsys):
    prompt = 'fixture prompt that must not appear in errors'
    failure = httpx.ReadError(
        f'{prompt}; TLS bad record mac; token=fixture-secret; '
        'https://cdn.invalid/result?signature=fixture-signature; ' + worker.image['value'])
    state = asyncio.run(worker.run(['success', failure], prompt=prompt))
    assert state.calls == 2
    assert '服务商连接或响应接收失败' in state.task['error']
    assert 'TLS bad record mac' in state.task['error']
    printed = capsys.readouterr()
    exposed = state.task['error'] + caplog.text + printed.out + printed.err
    for secret in (prompt, 'fixture-secret', 'https://cdn.invalid', 'fixture-signature', worker.image['value']):
        assert secret not in exposed
