"""#465: real HTTP/Agent admission and dispatch with offline providers."""
import copy
import json
import time
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from app.project_sessions import register_project_session, unregister_project_session
from app.routers import api_agent_tools, api_mcp, api_project_library
from app.services import generation
from tests.test_project_image_generation import tiny_png_base64
from firefly_projects import ProjectStore


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'projects', snapshot_delay=0)
    monkeypatch.setattr(main, 'FIREFLY_PROJECT_STORE', store)
    providers = [
        {'id': 'other', 'name': 'Other Studio', 'image_models': ['other-wide', 'stale']},
        {'id': 'chosen', 'name': 'Chosen Studio', 'image_models': ['square', 'wide'],
         'image_model_capabilities': {'square': {'arbitrary_size': True, 'aspect_ratios': ['1:1']}},
         'available_models': {'image_models': ['discovered-only', 'never-checked']},
         'model_names': {'wide': 'Wide Canvas', 'discovered-only': 'Discovery Alias'}},
        {'id': 'primary', 'name': 'Primary Studio', 'primary': True, 'image_models': ['primary-wide', 'stale']},
        {'id': 'off', 'name': 'Disabled Studio', 'enabled': False, 'image_models': ['stale', 'never-checked']},
    ]
    for provider in providers:
        provider.update(base_url=f"https://{provider['id']}.example.invalid", protocol='openai')
    monkeypatch.setattr(main, 'load_api_providers', lambda: providers)
    upstream = AsyncMock(return_value=({'type': 'b64', 'value': tiny_png_base64(), 'mime_type': 'image/png'}, {}))
    monkeypatch.setattr(main, 'generate_ai_image', upstream)
    gate = AsyncMock(return_value={'approved': True})
    monkeypatch.setattr(api_agent_tools, 'request_confirmation_core', gate)
    monkeypatch.setattr(api_mcp, 'request_confirmation_core', gate)
    reservation = Mock(wraps=api_project_library.reserve_project_image_task)
    monkeypatch.setattr(api_project_library, 'reserve_project_image_task', reservation)
    pid = store.create_project('Issue 465')['id']
    store.apply_mutations(pid, [{'op': 'upsert_node', 'node': {'id': 'actor', 'type': 'smart-agent'}},
                                {'op': 'upsert_node', 'node': {'id': 'source', 'type': 'smart-image', 'prompt': 'Draw'}}],
                          base_revision=0)
    with main.CANVAS_TASK_LOCK:
        initial_tasks = set(main.CANVAS_TASKS)
    token = object()
    register_project_session(token, pid)
    try:
        with TestClient(main.app, base_url='http://localhost', headers={'origin': 'http://localhost'}) as client:
            yield client, store, pid, providers, upstream, reservation, gate
    finally:
        unregister_project_session(token)
        api_agent_tools.reset_generation_counts(pid)
        with main.CANVAS_TASK_LOCK:
            for task_id in set(main.CANVAS_TASKS) - initial_tasks:
                main.CANVAS_TASKS.pop(task_id)
        store.close()


def submit(client, store, pid, **patch):
    return client.post(f'/api/project-library/{pid}/image-generation-tasks', json={
        'prompt': 'Draw', 'result_node_id': 'source', 'base_revision': store.canvas(pid)['revision'],
        'size': '', 'aspect_ratio': '16:9', 'resolution': '', 'provider_id': 'chosen', **patch,
    })


def finish(client, pid, task_id):
    path = f'/api/project-library/{pid}/image-generation-tasks/{task_id}'
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.is_success, response.text
        task = response.json()
        if task['status'] in {'succeeded', 'failed'}:
            assert task['status'] == 'succeeded', task
            return task
        time.sleep(.02)
    pytest.fail(f'{task_id} did not finish')


def verify_no_work(workspace, before):
    _, store, pid, _, upstream, reservation, gate = workspace
    upstream.assert_not_called()
    reservation.assert_not_called()
    gate.assert_not_called()
    assert store.canvas(pid) == before
    with main.CANVAS_TASK_LOCK:
        assert not any(task.get('project_id') == pid for task in main.CANVAS_TASKS.values())
        assert not any(key[0] == pid for key in api_agent_tools._GENERATION_COUNTS)


def test_auto_chooses_compatible_checked_model_without_crossing_or_pinning(workspace):
    client, store, pid, providers, upstream, reserve, _ = workspace
    before = store.canvas(pid)
    providers[1]['image_models'] = ['square']
    rejected = submit(client, store, pid, model='square')
    assert rejected.status_code == 400, rejected.text
    detail = rejected.json()['detail']
    assert detail['incompatible_parameters'] == ['aspect_ratio']
    assert 'Chosen Studio' in detail['message'] and '全局设置' in detail['message']
    verify_no_work(workspace, before)
    providers[1]['image_models'].append('wide')
    accepted = submit(client, store, pid, model='square', idempotency_key='browser:465')
    assert accepted.status_code == 202, accepted.text
    result = accepted.json()
    assert result['route']['provider_id'] == 'chosen' and result['route']['model'] == 'wide'
    finish(client, pid, result['task_id'])
    assert upstream.await_args.args[3:6:2] == ('wide', 'chosen')
    providers[1]['image_models'].clear()
    # A now-stale submission key still resolves to the original task, not new work.
    with main.CANVAS_TASK_LOCK:
        main.CANVAS_TASKS.pop(result['task_id'], None)
    replay = submit(client, store, pid, model='square', idempotency_key='browser:465')
    assert replay.status_code == 202 and replay.json()['task_id'] == result['task_id']
    assert reserve.call_count == upstream.await_count == 1


def test_retry_admits_new_work_again_but_replays_existing_retry(workspace):
    client, store, pid, providers, upstream, reserve, _ = workspace
    upstream.side_effect = RuntimeError('mock provider failure')
    original = submit(client, store, pid, model='wide')
    assert original.status_code == 202, original.text
    original_id = original.json()['task_id']
    url = f'/api/project-library/{pid}/image-generation-tasks/{original_id}'
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and client.get(url).json()['status'] != 'failed':
        time.sleep(.02)
    assert client.get(url).json()['status'] == 'failed'
    providers[1]['image_models'].remove('wide')
    before = store.canvas(pid)
    rejected = client.post(f'{url}/retry')
    assert rejected.status_code == 400 and rejected.json()['detail']['code'] == 'image_model_not_enabled'
    assert store.canvas(pid) == before
    assert reserve.call_count == upstream.await_count == 1
    upstream.side_effect = None
    providers[1]['image_models'].append('wide')
    retry = client.post(f'{url}/retry')
    assert retry.status_code == 202, retry.text
    retry_id = retry.json()['task_id']
    assert retry_id != original_id and retry.json()['retry_of_task_id'] == original_id
    finish(client, pid, retry_id)
    providers[1]['image_models'].clear()
    replay = client.post(f'{url}/retry')
    assert replay.status_code == 202 and replay.json()['task_id'] == retry_id
    assert reserve.call_count == upstream.await_count == 2


@pytest.mark.parametrize(('mode', 'selection'), [
    ('auto', {}), ('auto', {'provider_id': ''}), ('manual', {'provider_id': '  '}),
])
def test_project_requires_provider_even_with_enabled_primary(workspace, mode, selection):
    client, store, pid, *_ = workspace
    before = store.canvas(pid)
    rejected = client.post(f'/api/project-library/{pid}/image-generation-tasks', json={
        'prompt': 'Draw', 'result_node_id': 'source', 'base_revision': before['revision'],
        'model': 'primary-wide', 'routing_mode': mode, **selection,
    })
    assert rejected.status_code == 400, rejected.text
    detail = rejected.json()['detail']
    assert detail['code'] == 'image_provider_required' and detail['message'] == '请先选择服务商'
    verify_no_work(workspace, before)


def test_retry_without_provider_does_not_recover_or_enqueue_legacy_snapshot(workspace):
    client, store, pid, *_ = workspace
    task_id = 'project_img_legacy_no_provider'
    before = store.begin_image_generation(pid, 'source', {
        'taskId': task_id, 'kind': 'image', 'count': 1,
        'providerId': 'primary', 'model': 'primary-wide',
        'requestPayload': {'prompt': 'Draw', 'result_node_id': 'source', 'provider_id': ''},
    }, base_revision=store.canvas(pid)['revision'])
    response = client.post(f'/api/project-library/{pid}/image-generation-tasks/{task_id}/retry')
    assert response.status_code == 400, response.text
    assert response.json()['detail']['message'] == '请先选择服务商'
    verify_no_work(workspace, before)


def test_no_checked_model_even_with_discovery(workspace):
    client, store, pid, providers, *_ = workspace
    providers[1]['image_models'] = []
    before = store.canvas(pid)
    rejected = submit(client, store, pid)
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()['detail']['code'] == 'no_enabled_image_model'
    assert 'Chosen Studio' in rejected.text and '全局设置' in rejected.text
    verify_no_work(workspace, before)


@pytest.mark.parametrize(('model', 'names'), [
    ('stale', ['Other Studio', 'Primary Studio']), ('other-wide', ['Other Studio']),
    ('never-checked', []), ('Discovery Alias', []),
])
def test_stale_model_diagnostics_only_count_enabled_checked_models(workspace, model, names):
    client, store, pid, *_ = workspace
    before = store.canvas(pid)
    response = submit(client, store, pid, model=model, routing_mode='manual' if model == 'Discovery Alias' else 'auto')
    assert response.status_code == 400, response.text
    detail = response.json()['detail']
    assert detail['code'] == 'image_model_not_enabled'
    message = detail['message']
    assert model in message and 'Chosen Studio' in message and 'Disabled Studio' not in message
    assert ('Other Studio' in message) == ('Other Studio' in names)
    assert ('Primary Studio' in message) == ('Primary Studio' in names)
    assert ('切换服务商到' in message) if names else ('全局设置' in message and '换一个模型' in message)
    verify_no_work(workspace, before)


@pytest.mark.parametrize('transport', ['rest', 'mcp'])
def test_agent_cannot_bypass_preflight_and_uses_valid_auto_selection(workspace, transport):
    client, store, pid, providers, upstream, reserve, gate = workspace

    def run_agent():
        args = {'node_id': 'source'}
        if transport == 'rest':
            res = client.post(f'/api/projects/{pid}/generate/trigger?agent_node_id=actor',
                              headers={'X-Agent-Project': pid}, json=args)
            return res.status_code == 400, res.json().get('detail', res.json())
        res = client.post(f'/api/mcp?project={pid}&node=actor', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                          'params': {'name': 'generate.trigger', 'arguments': args}})
        tool = res.json()['result']
        return tool['isError'], tool['structuredContent'] if tool['isError'] else json.loads(tool['content'][0]['text'])

    source = store.find_canvas_node(pid, 'source')
    source['runSettings'] = {'provider_id': 'chosen', 'model': 'stale', 'routingMode': 'auto',
                             'ratio': 'custom', 'customRatio': '16:9'}

    def save():
        store.apply_mutations(pid, [{'op': 'upsert_node', 'node': source}], base_revision=store.canvas(pid)['revision'])
        return store.canvas(pid)

    selected = source['runSettings'].pop('provider_id')
    prompt = source['prompt']
    source['prompt'] = '  '
    before = save()
    error, detail = run_agent()
    assert error and detail['code'] == 'NODE_NOT_GENERATABLE', detail
    verify_no_work(workspace, before)

    source['prompt'] = prompt
    for provider in (None, '', '  '):
        if provider is not None:
            source['runSettings']['provider_id'] = provider
        before = save()
        error, detail = run_agent()
        assert error and detail['code'] == 'INVALID_ARGUMENT', detail
        assert detail['details']['routing']['code'] == 'image_provider_required', detail
        assert detail['message'] == '请先选择服务商'
        verify_no_work(workspace, before)
    source['runSettings']['provider_id'] = selected
    before = save()
    error, detail = run_agent()
    assert error and detail['details']['routing']['code'] == 'image_model_not_enabled', detail
    assert 'Other Studio' in detail['message'] and 'Primary Studio' in detail['message']
    verify_no_work(workspace, before)
    source['runSettings']['model'] = 'square'
    before = save()
    providers[1]['image_models'] = ['square']
    error, detail = run_agent()
    assert error and detail['details']['routing']['incompatible_parameters'] == ['aspect_ratio'], detail
    verify_no_work(workspace, before)
    providers[1]['image_models'].append('wide')
    error, started = run_agent()
    assert not error, started
    task = finish(client, pid, started['task_id'])
    assert task['request_payload']['provider_id'] == 'chosen' and task['request_payload']['model'] == 'square'
    assert upstream.await_args.args[3:6:2] == ('wide', 'chosen')
    assert reserve.call_count == gate.await_count == 1
    assert store.find_canvas_node(pid, 'source')['runSettings'] == source['runSettings']


def test_manual_adapter_does_not_substitute_unchecked_resolution_variant(workspace, monkeypatch):
    client, store, pid, providers, _, _, _ = workspace
    model = 'gemini-3.1-flash-image-preview'
    variant = model + '-4k'
    providers[1].update(image_models=[model], available_models={'image_models': [variant]},
                        model_names={model: 'Friendly Flash'})
    calls = []

    def transport(request):
        calls.append((request.url.host, json.loads(request.content)))
        return httpx.Response(200, json={'data': [{'b64_json': tiny_png_base64()}]})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(generation.httpx, 'AsyncClient', lambda **kw: async_client(transport=httpx.MockTransport(transport), **kw))
    monkeypatch.setattr(main, 'generate_ai_image', generation.generate_ai_image)
    monkeypatch.setattr(main, 'api_headers', lambda **_kwargs: {})
    payload = {'routing_mode': 'manual', 'size': '4096x4096', 'aspect_ratio': '1:1', 'resolution': '4k'}
    before = copy.deepcopy(store.canvas(pid))
    rejected = submit(client, store, pid, model=variant, **payload)
    assert rejected.status_code == 400 and rejected.json()['detail']['code'] == 'image_model_not_enabled'
    assert not calls and store.canvas(pid) == before
    result = submit(client, store, pid, model='Friendly Flash', **payload)
    assert result.status_code == 202, result.text
    assert result.json()['route']['model'] == model
    finish(client, pid, result.json()['task_id'])
    assert len(calls) == 1 and calls[0][0] == 'chosen.example.invalid'
    assert calls[0][1]['model'] == model and calls[0][1]['image_size'] == '4K'
    providers[1]['image_models'].append(variant)
    result = submit(client, store, pid, model='Friendly Flash', **payload)
    assert result.status_code == 202, result.text
    finish(client, pid, result.json()['task_id'])
    assert result.json()['route']['model'] == calls[1][1]['model'] == variant


def test_legacy_canvas_http_checks_before_queue_but_leaves_online_image_contract(workspace):
    client, _, _, _, upstream, _, _ = workspace
    with main.CANVAS_TASK_LOCK:
        before = set(main.CANVAS_TASKS)
    for selection in ({}, {'provider_id': ''}, {'provider_id': '  '}):
        missing = client.post('/api/canvas-image-tasks', json={'prompt': 'Draw', 'model': 'primary-wide', **selection})
        assert missing.status_code == 400 and missing.json()['detail']['message'] == '请先选择服务商'
    payload = {'prompt': 'Draw', 'provider_id': 'chosen', 'model': 'discovered-only'}
    rejected = client.post('/api/canvas-image-tasks', json=payload)
    assert rejected.status_code == 400 and rejected.json()['detail']['code'] == 'image_model_not_enabled'
    upstream.assert_not_called()
    with main.CANVAS_TASK_LOCK:
        assert set(main.CANVAS_TASKS) == before
    ok = client.post('/api/canvas-image-tasks', json={**payload, 'model': 'Wide Canvas'})
    assert ok.status_code == 200, ok.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = client.get(f"/api/canvas-image-tasks/{ok.json()['task_id']}").json()
        if task['status'] in {'succeeded', 'failed'}:
            assert task['status'] == 'succeeded', task
            break
        time.sleep(.02)
    else:
        pytest.fail('legacy canvas task did not finish')
    assert upstream.await_args.args[3:6:2] == ('wide', 'chosen')
    online = client.post('/api/online-image', json={'prompt': 'Draw', 'model': 'unchecked-online'})
    assert online.status_code == 200, online.text
    assert upstream.await_args.args[3:6:2] == ('unchecked-online', 'primary')
