"""HTTP/clock isolation keeps real generation helpers observable (#472)."""
import asyncio
import base64
from io import BytesIO
import json
import time
import traceback
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

import main
from app.services import generation
from app.services.generation import generate_comfyui_provider_image
from app.services.generation_failures import AGENT_IMAGE_ATTEMPT, UpstreamImageFailure


BASE = 'https://comfy.invalid:8188'
PROMPT_ID = 'prompt-472-actual'
MODEL = 'comfyui-h3-image'
_buffer = BytesIO()
Image.new('RGB', (32, 32), (210, 140, 90)).save(_buffer, format='PNG')
PNG = _buffer.getvalue()
REFERENCE = {'url': 'data:image/png;base64,' + base64.b64encode(PNG).decode('ascii')}


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class ComfyHTTP:
    def __init__(self):
        self.requests = []
        self.overrides = {}
        self.history_steps = ['done']
        self.history_calls = 0
        self.clock = Clock()

    def completed(self):
        return {PROMPT_ID: {
            'status': {'status_str': 'success', 'completed': True, 'messages': []},
            'outputs': {'9': {'images': [{'filename': 'result.png', 'subfolder': '', 'type': 'output'}]}},
        }}

    def respond(self, request):
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key in self.overrides:
            value = self.overrides[key]
            if isinstance(value, Exception):
                raise value
            return value(request) if callable(value) else value
        if key == ('POST', '/prompt'):
            return httpx.Response(200, json={'prompt_id': PROMPT_ID})
        if request.url.path.startswith('/history/'):
            step = self.history_steps[min(self.history_calls, len(self.history_steps) - 1)]
            self.history_calls += 1
            return httpx.Response(200, json=self.completed() if step == 'done' else step)
        if key == ('GET', '/view') or request.url.host == 'reference.invalid':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        if key == ('POST', '/upload/image'):
            return httpx.Response(200, json={'name': 'reference.png', 'subfolder': '', 'type': 'input'})
        if key == ('GET', '/queue'):
            return httpx.Response(200, json={'queue_running': [], 'queue_pending': []})
        if key in {('POST', '/queue'), ('POST', '/interrupt'), ('POST', '/free')}:
            return httpx.Response(200, json={})
        raise AssertionError(f'Unexpected external HTTP request: {key}')

    def to(self, path, method=None):
        return [r for r in self.requests if r.url.path == path and (method is None or r.method == method)]


@pytest.fixture
def server(monkeypatch, tmp_path):
    fake = ComfyHTTP()
    real_client = httpx.AsyncClient

    class Client(real_client):
        def __init__(self, *args, **kwargs):
            kwargs['transport'] = httpx.MockTransport(fake.respond)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    monkeypatch.setattr(generation, 'time', SimpleNamespace(monotonic=fake.clock.monotonic, strftime=time.strftime))
    monkeypatch.setattr(asyncio, 'sleep', fake.clock.sleep)
    # A test must never persist generated media into a developer's output directory.
    monkeypatch.setattr(main, 'OUTPUT_OUTPUT_DIR', str(tmp_path / 'output'))
    monkeypatch.setattr(main, 'API_ENV_FILE', str(tmp_path / 'empty-api.env'))
    monkeypatch.delenv(main.provider_key_env('comfy-472-test'), raising=False)
    return fake


def generate(*, provider=None, references=None, progress=None):
    return asyncio.run(generate_comfyui_provider_image(
        'a cat', '1024x1024', MODEL,
        reference_images=references,
        provider=provider or {'id': 'comfy-472-test', 'protocol': 'comfyui', 'base_url': BASE},
        image_parameters={'aspect_ratio': '1:1', 'resolution': '1K'},
        progress_callback=progress,
    ))


@pytest.mark.parametrize(('response', 'status', 'message'), [
    (httpx.Response(503, json={'error': 'unavailable'}), 503, '服务不可用'),
    (httpx.Response(200, text='<html>login</html>', headers={'content-type': 'text/html'}), 502, '不是 ComfyUI'),
    (httpx.Response(200, content=b'{broken'), 502, '不是 ComfyUI'),
    (httpx.Response(200, json=[]), 502, '不是 ComfyUI'),
    (httpx.Response(200, json={PROMPT_ID: []}), 502, '不是 ComfyUI'),
    (httpx.Response(200, json={PROMPT_ID: {'status': [], 'outputs': {}}}), 502, '不是 ComfyUI'),
    (httpx.Response(200, json={PROMPT_ID: {'status': {}, 'outputs': []}}), 502, '不是 ComfyUI'),
    (httpx.Response(200, json={'error': 'wrong endpoint'}), 502, '不是 ComfyUI'),
    (httpx.Response(200, json=None), 502, '不是 ComfyUI'),
    (httpx.Response(200, json={PROMPT_ID: {'status': {'status_str': 'error', 'messages': [['execution_error', []]]}}}), 502, '不是 ComfyUI'),
    (httpx.Response(200, json={PROMPT_ID: {'outputs': {'9': {'images': [None]}}}}), 502, '不是 ComfyUI'),
    (httpx.Response(200, json={PROMPT_ID: {'outputs': {'9': {'images': [{'filename': 'result.png', 'metadata': []}]}}}}), 502, '不是 ComfyUI'),
])
def test_invalid_history_does_not_wait_until_deadline(server, response, status, message):
    server.overrides['GET', f'/history/{PROMPT_ID}'] = response
    with pytest.raises(main.HTTPException) as error:
        generate()
    assert error.value.status_code == status
    assert message in error.value.detail
    assert PROMPT_ID in error.value.detail
    assert len(server.to(f'/history/{PROMPT_ID}')) == 1
    assert len(server.to('/prompt', 'POST')) == 1


def test_empty_history_is_pending_then_completes(server):
    server.history_steps = [{}, 'done']
    progress = []
    image, raw = generate(progress=lambda phase, **details: progress.append((phase, details)))
    assert image['type'] == 'url'
    assert raw['prompt_id'] == PROMPT_ID
    assert raw['bytes'] == len(PNG)
    assert len(server.to(f'/history/{PROMPT_ID}')) == 2
    assert len(server.to('/prompt', 'POST')) == 1
    assert progress[-1] == ('receiving', {'bytes_done': len(PNG), 'bytes_total': len(PNG)})


def test_view_timeout_is_readable_and_never_resubmits(server):
    server.overrides['GET', '/view'] = httpx.ReadTimeout('socket stalled')
    with pytest.raises(main.HTTPException) as error:
        generate()
    assert error.value.status_code == 504
    assert '取图超时' in error.value.detail
    assert PROMPT_ID in error.value.detail
    assert len(server.to('/prompt', 'POST')) == 1
    assert len(server.to('/view')) == 1


@pytest.mark.parametrize(('path', 'exception', 'message', 'status', 'references'), [
    ('/prompt', httpx.ConnectTimeout('connect stalled'), '无法建立连接', 503, None),
    ('/prompt', httpx.ConnectError('refused'), '无法建立连接', 503, None),
    ('/upload/image', httpx.WriteTimeout('upload stalled'), '参考图上传超时', 504, [REFERENCE]),
    ('/prompt', httpx.ReadTimeout('submit stalled'), '提交超时', 504, None),
    (f'/history/{PROMPT_ID}', httpx.ReadTimeout('history stalled'), '查询任务状态超时', 504, None),
    ('/view', httpx.ConnectError('view refused'), '取图连接失败', 503, None),
])
def test_stage_specific_transport_errors(server, path, exception, message, status, references):
    method = 'POST' if path in ('/prompt', '/upload/image') else 'GET'
    server.overrides[method, path] = exception
    with pytest.raises(main.HTTPException) as error:
        generate(references=references)
    assert error.value.status_code == status
    assert message in error.value.detail
    if path.startswith('/history/') or path == '/view':
        assert PROMPT_ID in error.value.detail
    if path == '/prompt' and isinstance(exception, httpx.ReadTimeout):
        assert '可能已入队' in error.value.detail
        assert '队列' in error.value.detail
    assert len(server.to('/prompt', 'POST')) <= 1


@pytest.mark.parametrize(('exception', 'status', 'uncertain'), [
    (httpx.ConnectTimeout('connect stalled'), 503, False),
    (httpx.ReadTimeout('submit stalled'), 504, True),
])
def test_agent_submit_errors_keep_readable_http_and_retry_guards(server, exception, status, uncertain):
    server.overrides['POST', '/prompt'] = exception
    token = AGENT_IMAGE_ATTEMPT.set(True)
    try:
        with pytest.raises(UpstreamImageFailure) as error:
            generate()
    finally:
        AGENT_IMAGE_ATTEMPT.reset(token)
    assert error.value.status_code == status
    assert error.value.uncertain is uncertain
    assert 'ComfyUI' in error.value.detail
    if uncertain:
        assert '可能已入队' in error.value.detail
    assert len(server.to('/prompt', 'POST')) == 1


def test_total_deadline_is_not_a_connection_timeout(server):
    def accepted(request):
        server.clock.now = 1801
        return httpx.Response(200, json={'prompt_id': PROMPT_ID})
    server.overrides['POST', '/prompt'] = accepted
    with pytest.raises(main.HTTPException) as error:
        generate()
    assert error.value.status_code == 504
    assert '总等待超时' in error.value.detail
    assert PROMPT_ID in error.value.detail
    assert 'output/MiniMax-H3-Image/' in error.value.detail
    assert len(server.to('/prompt', 'POST')) == 1


@pytest.mark.parametrize(('key', 'response', 'outcome'), [
    (('POST', '/queue'), httpx.Response(503, json={}), 'failed'),
    (('POST', '/queue'), httpx.ConnectError('queue unavailable'), 'failed'),
    (('GET', '/queue'), httpx.Response(503, json={}), 'unknown'),
    (('GET', '/queue'), httpx.ReadTimeout('queue stalled'), 'unknown'),
    (('GET', '/queue'), httpx.Response(200, content=b'{broken'), 'unknown'),
    (('GET', '/queue'), httpx.Response(200, json=[]), 'unknown'),
    (('GET', '/queue'), httpx.Response(200, json={}), 'unknown'),
])
def test_timeout_cleanup_failures_are_truthful_and_preserve_primary_error(server, key, response, outcome):
    server.history_steps = [{}]
    server.overrides[key] = response
    with pytest.raises(main.HTTPException) as error:
        generate()
    assert error.value.status_code == 504
    assert '总等待超时' in error.value.detail
    assert PROMPT_ID in error.value.detail
    assert '无法确认已从队列撤单' in error.value.detail
    assert '检查远端队列' in error.value.detail
    assert error.value.comfyui_cancellation_status == outcome
    assert len(server.to('/prompt', 'POST')) == 1
    assert json.loads(server.to('/queue', 'POST')[0].content) == {'delete': [PROMPT_ID]}
    assert not server.to('/interrupt')


@pytest.mark.parametrize(('running_id', 'interrupt_status', 'outcome'), [
    (PROMPT_ID, 503, 'failed'),
    (PROMPT_ID, 200, 'unknown'),
    (PROMPT_ID, httpx.ConnectError('interrupt unavailable'), 'failed'),
    ('another-users-prompt', 200, 'success'),
])
def test_cleanup_interrupts_only_our_running_prompt_without_claiming_stopped(server, running_id, interrupt_status, outcome):
    server.overrides['GET', f'/history/{PROMPT_ID}'] = httpx.Response(503, json={})
    server.overrides['GET', '/queue'] = httpx.Response(200, json={'queue_running': [[0, running_id, {}]], 'queue_pending': []})
    server.overrides['POST', '/interrupt'] = interrupt_status if isinstance(interrupt_status, Exception) else httpx.Response(interrupt_status, json={})
    with pytest.raises(main.HTTPException) as error:
        generate()
    assert error.value.status_code == 503
    assert '服务不可用' in error.value.detail
    assert error.value.comfyui_cancellation_status == outcome
    assert len(server.to('/interrupt')) == (1 if running_id == PROMPT_ID else 0)
    assert len(server.to('/prompt', 'POST')) == 1
    if running_id == PROMPT_ID:
        assert '无法确认已从队列撤单' in error.value.detail
        assert '检查远端队列' in error.value.detail
    else:
        assert '已从队列撤单' in error.value.detail


def test_task_cancellation_still_cleans_up_own_prompt(server):
    async def cancelled_request(request):
        asyncio.current_task().cancel()
        await asyncio.Future()
    server.overrides['GET', f'/history/{PROMPT_ID}'] = cancelled_request
    server.overrides['GET', '/queue'] = httpx.Response(200, json={'queue_running': [[0, PROMPT_ID]], 'queue_pending': []})
    with pytest.raises(asyncio.CancelledError) as error:
        generate()
    assert error.value.comfyui_cancellation_status == 'unknown'
    assert len(server.to('/queue', 'POST')) == 1
    assert len(server.to('/interrupt', 'POST')) == 1
    assert len(server.to('/prompt', 'POST')) == 1


@pytest.mark.parametrize('key', ['', 'fake-472-private-token', 'Bearer fake-472-private-token'])
def test_scoped_bearer_reaches_all_comfy_endpoints_but_never_external_reference(server, key):
    server.history_steps = [{PROMPT_ID: {
        'status': {'status_str': 'error', 'messages': [['execution_error', {'exception_message': 'CUDA out of memory'}]]},
        'outputs': {},
    }}, 'done']
    server.overrides['GET', '/queue'] = httpx.Response(200, json={'queue_running': [[0, PROMPT_ID]], 'queue_pending': []})
    provider = {'id': 'comfy-472-test', 'protocol': 'comfyui', 'base_url': BASE, 'api_key': key}
    image, raw = generate(provider=provider, references=[{'url': 'https://reference.invalid/image.png'}])
    assert image['type'] == 'url'
    assert raw['prompt_id'] == PROMPT_ID
    expected = 'Bearer fake-472-private-token' if key else None
    for path in ('/upload/image', '/prompt', f'/history/{PROMPT_ID}', '/view', '/queue', '/interrupt', '/free'):
        requests = server.to(path)
        assert requests, path
        assert all(r.headers.get('authorization') == expected for r in requests), path
    foreign = [r for r in server.requests if r.url.host == 'reference.invalid']
    assert len(foreign) == 1
    assert 'authorization' not in foreign[0].headers
    assert len(server.to('/prompt', 'POST')) == 2
    assert len(server.to('/free', 'POST')) == 1
    assert server.to('/prompt')[0].extensions['timeout'] == {'connect': 10.0, 'read': 1800.0, 'write': 120.0, 'pool': 10.0}


@pytest.mark.parametrize('key', ['Bearer', ' bearer ', 'Bearer \t'])
def test_empty_bearer_scheme_matches_probe_no_auth_behavior(server, key):
    generate(provider={'id': 'comfy-472-test', 'protocol': 'comfyui', 'base_url': BASE, 'api_key': key})
    assert all('authorization' not in request.headers for request in server.requests)


def test_reference_download_never_uses_auth_even_on_comfy_origin(server):
    server.overrides['GET', '/reference.png'] = httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    generate(provider={'id': 'comfy-472-test', 'protocol': 'comfyui', 'base_url': BASE, 'api_key': 'fake-472-private-token'},
             references=[{'url': f'{BASE}/reference.png'}])
    assert 'authorization' not in server.to('/reference.png')[0].headers
    assert server.to('/upload/image')[0].headers['authorization'] == 'Bearer fake-472-private-token'


def test_saved_provider_key_resolution_is_used(server, monkeypatch):
    monkeypatch.setenv(main.provider_key_env('comfy-472-test'), 'Bearer fake-472-saved-token')
    generate()
    assert all(r.headers.get('authorization') == 'Bearer fake-472-saved-token' for r in server.requests)


@pytest.mark.parametrize(('path', 'payload', 'agent'), [
    ('/upload/image', httpx.Response(400, text='fake-472-private-token'), False),
    ('/prompt', httpx.Response(400, json={'error': 'fake-472-private-token'}), False),
    ('/prompt', httpx.Response(503, json={'error': 'fake-472-private-token'}), True),
    (f'/history/{PROMPT_ID}', httpx.Response(200, json={PROMPT_ID: {
        'status': {'status_str': 'error', 'messages': [['execution_error', {'exception_message': 'fake-472-private-token'}]]},
        'outputs': {},
    }}), False),
])
def test_provider_secret_is_absent_from_public_errors_and_logs(server, caplog, path, payload, agent):
    server.overrides['POST' if path in ('/prompt', '/upload/image') else 'GET', path] = payload
    token = AGENT_IMAGE_ATTEMPT.set(agent)
    try:
        with pytest.raises(main.HTTPException) as error:
            generate(provider={'id': 'comfy-472-test', 'protocol': 'comfyui', 'base_url': BASE, 'api_key': 'Bearer fake-472-private-token'},
                     references=[REFERENCE] if path == '/upload/image' else None)
    finally:
        AGENT_IMAGE_ATTEMPT.reset(token)
    assert 'fake-472-private-token' not in error.value.detail
    assert 'fake-472-private-token' not in caplog.text


@pytest.mark.parametrize(('path', 'response', 'status', 'message', 'references'), [
    ('/upload/image', httpx.Response(401, json={}), 401, '鉴权失败', [REFERENCE]),
    ('/prompt', httpx.Response(503, json={}), 503, '服务不可用', None),
    ('/prompt', httpx.Response(401, json={}), 401, '鉴权失败', None),
    ('/view', httpx.Response(503, json={}), 503, '服务不可用', None),
    ('/view', httpx.Response(401, json={}), 401, '鉴权失败', None),
    ('/prompt', httpx.Response(200, text='<html>login</html>'), 502, '不是 ComfyUI', None),
    ('/prompt', httpx.Response(200, json=[]), 502, '不是 ComfyUI', None),
    ('/upload/image', httpx.Response(200, json=[]), 502, '不是 ComfyUI', [REFERENCE]),
])
def test_stage_http_failures_are_meaningful(server, path, response, status, message, references):
    server.overrides['POST' if path in ('/prompt', '/upload/image') else 'GET', path] = response
    with pytest.raises(main.HTTPException) as error:
        generate(references=references)
    assert error.value.status_code == status
    assert message in error.value.detail
    if path == '/view':
        assert PROMPT_ID in error.value.detail
    assert len(server.to('/prompt', 'POST')) <= 1


@pytest.mark.parametrize('path', [f'/history/{PROMPT_ID}', '/view'])
def test_agent_failure_after_acceptance_retains_task_identity_and_no_replay(server, path):
    server.overrides['GET', path] = httpx.Response(503, json={})
    token = AGENT_IMAGE_ATTEMPT.set(True)
    try:
        with pytest.raises(UpstreamImageFailure) as error:
            generate()
    finally:
        AGENT_IMAGE_ATTEMPT.reset(token)
    assert error.value.uncertain
    assert error.value.upstream_task_id == PROMPT_ID
    assert error.value.upstream_status == 503
    assert len(server.to('/prompt', 'POST')) == 1


@pytest.mark.parametrize(('status', 'body', 'message', 'uncertain'), [
    (403, {'error': {'message': 'insufficient balance'}}, 'insufficient balance', False),
    (429, {'error': {'message': 'rate limited', 'code': 'rate_limit'}}, 'rate limited', False),
    (503, {'error': 'temporarily unavailable', 'task_id': 'maybe-accepted'}, 'temporarily unavailable', True),
])
def test_agent_submit_http_errors_preserve_failure_taxonomy(server, status, body, message, uncertain):
    server.overrides['POST', '/prompt'] = httpx.Response(status, json=body, headers={'retry-after': '17'})
    token = AGENT_IMAGE_ATTEMPT.set(True)
    try:
        with pytest.raises(UpstreamImageFailure) as error:
            generate()
    finally:
        AGENT_IMAGE_ATTEMPT.reset(token)
    assert error.value.upstream_status == status
    assert error.value.retry_after == '17'
    assert error.value.uncertain is uncertain
    assert 'ComfyUI 提交' in error.value.detail
    assert message in error.value.detail
    assert len(server.to('/prompt', 'POST')) == 1


def test_repeated_oom_has_truthful_message_and_only_one_free_retry(server):
    server.history_steps = [{PROMPT_ID: {
        'status': {'status_str': 'error', 'messages': [['execution_error', {'exception_message': 'CUDA out of memory'}]]},
        'outputs': {},
    }}]
    server.overrides['POST', '/free'] = httpx.Response(503, json={})
    with pytest.raises(main.HTTPException) as error:
        generate()
    assert '显存' in error.value.detail
    assert '已让 ComfyUI 卸载模型' not in error.value.detail
    assert len(server.to('/prompt', 'POST')) == 2
    assert len(server.to('/free', 'POST')) == 1


@pytest.mark.parametrize('path', ['/prompt', '/upload/image', f'/history/{PROMPT_ID}', '/view'])
def test_transport_diagnostics_cannot_leak_provider_key_in_tracebacks(server, path):
    server.overrides['POST' if path in ('/prompt', '/upload/image') else 'GET', path] = httpx.ReadTimeout('fake-472-private-token')
    provider = {'id': 'comfy-472-test', 'protocol': 'comfyui', 'base_url': BASE, 'api_key': 'fake-472-private-token'}
    with pytest.raises(main.HTTPException) as error:
        generate(provider=provider, references=[REFERENCE] if path == '/upload/image' else None)
    rendered = ''.join(traceback.format_exception(error.value))
    assert 'fake-472-private-token' not in rendered


@pytest.mark.parametrize('path', ['/prompt', '/view'])
def test_api_redirects_are_not_followed_with_provider_credentials(server, path):
    server.overrides['POST' if path == '/prompt' else 'GET', path] = httpx.Response(307, headers={'location': 'https://foreign.invalid/steal'})
    with pytest.raises(main.HTTPException):
        generate(provider={'id': 'comfy-472-test', 'protocol': 'comfyui', 'base_url': BASE, 'api_key': 'fake-472-private-token'})
    assert all(r.url.host == 'comfy.invalid' for r in server.requests)
    assert len(server.to('/prompt', 'POST')) == 1


def test_pending_task_reports_queue_progress_without_resubmission(server):
    server.history_steps = [{}, {}, {}, {}, 'done']
    server.overrides['GET', '/queue'] = httpx.Response(200, json={'queue_running': [], 'queue_pending': [[0, 'other'], [1, PROMPT_ID]]})
    progress = []
    _, raw = generate(progress=lambda phase, **details: progress.append((phase, details)))
    assert any(phase == 'provider_generating' and details.get('queue_position') == 1 for phase, details in progress)
    assert raw['queue_position'] == 1
    assert len(server.to('/prompt', 'POST')) == 1


def test_agent_upload_failure_keeps_reference_taxonomy_and_never_submits(server):
    server.overrides['POST', '/upload/image'] = httpx.WriteTimeout('upload stalled')
    token = AGENT_IMAGE_ATTEMPT.set(True)
    try:
        with pytest.raises(UpstreamImageFailure) as error:
            generate(references=[REFERENCE])
    finally:
        AGENT_IMAGE_ATTEMPT.reset(token)
    assert error.value.error_code == 'ref_upload_failed'
    assert error.value.status_code == 504
    assert not error.value.uncertain
    assert '上传超时' in error.value.detail
    assert not server.to('/prompt')


def test_agent_oom_never_triggers_an_adapter_resubmission(server):
    server.history_steps = [{PROMPT_ID: {
        'status': {'status_str': 'error', 'messages': [['execution_error', {'exception_message': 'CUDA out of memory'}]]},
        'outputs': {},
    }}]
    token = AGENT_IMAGE_ATTEMPT.set(True)
    try:
        with pytest.raises(main.HTTPException) as error:
            generate()
    finally:
        AGENT_IMAGE_ATTEMPT.reset(token)
    assert error.value.upstream_task_id == PROMPT_ID
    assert '显存' in error.value.detail
    assert len(server.to('/prompt', 'POST')) == 1
    assert not server.to('/free')


def test_history_auth_failure_is_immediate_and_readable(server):
    server.overrides['GET', f'/history/{PROMPT_ID}'] = httpx.Response(401, json={'error': 'unauthorized'})
    with pytest.raises(main.HTTPException) as error:
        generate()
    assert error.value.status_code == 401
    assert '鉴权' in error.value.detail
    assert PROMPT_ID in error.value.detail
    assert len(server.to(f'/history/{PROMPT_ID}')) == 1
    assert len(server.to('/prompt', 'POST')) == 1
