"""#472: real provider routes, isolated configuration, HTTP-boundary doubles only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = 'https://comfy.example.test/deployment'
NODE = 'MiniMaxH3ImageConditioning'
STATS_PATH = '/deployment/system_stats'
NODES_PATH = f'/deployment/object_info/{NODE}'
# /system_stats wire format; versions and hardware details are informational.
SYSTEM_STATS = {
    'system': {'os': 'posix', 'ram_total': 68719476736, 'ram_free': 34359738368,
               'comfyui_version': '0.3.26', 'python_version': '3.12.9', 'embedded_python': False},
    'devices': [{'name': 'cuda:0 NVIDIA RTX 4090', 'type': 'cuda', 'index': 0,
                 'vram_total': 25757220864, 'vram_free': 21474836480}],
}
# The shipped H3 workflow/ADR 0025 supplies CLIP/VAE and consumes conditioning
# plus latent. Keep its real wire envelope without depending on display fields.
H3_METADATA = {
    'input': {'required': {'clip': ['CLIP'], 'vae': ['VAE'],
                           'prompt': ['STRING', {'multiline': True}],
                           'width': ['INT', {'default': 2048, 'step': 32}],
                           'height': ['INT', {'default': 2048, 'step': 32}]},
              'optional': {}},
    'output': ['CONDITIONING', 'LATENT'], 'output_is_list': [False, False],
    'output_name': ['CONDITIONING', 'LATENT'], 'name': NODE, 'display_name': NODE,
    'description': '', 'category': 'conditioning', 'output_node': False,
}
PROVIDER = {
    'id': 'comfyui', 'name': 'Private provider', 'protocol': 'comfyui',
    'base_url': BASE_URL, 'enabled': True, 'primary': True,
    'image_models': ['comfyui-h3-image'], 'chat_models': [], 'video_models': [],
}


def probe(**overrides):
    return {'method': 'POST', 'path': '/api/providers/test-connection', 'json': {
        'base_url': BASE_URL, 'protocol': 'comfyui', 'provider_id': 'comfyui', **overrides,
    }}


def exercise(tmp_path, responses, actions=None, environment=None):
    """Import the actual app only after assigning private runtime/config roots.

    A fresh process avoids mutating main's globals or sharing saved settings with
    other tests in ordinary suite runs. No internal collaborator is replaced.
    """
    keep = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'PATH'}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    for directory in ('home', 'temp', 'runtime', 'projects', 'home/roaming', 'home/local'):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    env.update({
        'HOME': str(tmp_path / 'home'), 'USERPROFILE': str(tmp_path / 'home'),
        'APPDATA': str(tmp_path / 'home/roaming'), 'LOCALAPPDATA': str(tmp_path / 'home/local'),
        'TEMP': str(tmp_path / 'temp'), 'TMP': str(tmp_path / 'temp'),
        'TMPDIR': str(tmp_path / 'temp'),
        'FIREFLY_RUNTIME_ROOT': str(tmp_path / 'runtime'),
        'FIREFLY_PROJECTS_ROOT': str(tmp_path / 'projects'),
        'FIREFLY_TEST_MODE': '1', 'FIREFLY_TEST_DISABLE_CLI_PROBES': '1',
        'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'NO_PROXY': '*',
        **(environment or {}),
    })
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).resolve()), '--route-worker'],
        input=json.dumps({'responses': responses, 'actions': actions or [probe()]}),
        text=True, encoding='utf-8', capture_output=True, env=env, cwd=ROOT, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = next(line for line in result.stdout.splitlines() if line.startswith('ROUTE_RESULT='))
    return json.loads(output.removeprefix('ROUTE_RESULT='))


def route_worker():
    import httpx
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    sys.path.insert(0, str(ROOT))
    scenario = json.load(sys.stdin)
    captured = []

    def respond(request):
        captured.append({'method': request.method, 'url': str(request.url),
                         'headers': dict(request.headers), 'timeout': request.extensions.get('timeout')})
        assert request.url.host == 'comfy.example.test', f'Unexpected outbound URL: {request.url}'
        spec = scenario['responses'][request.url.path]
        if 'exception' in spec:
            raise getattr(httpx, spec['exception'])(spec.get('detail', 'offline failure'), request=request)
        return httpx.Response(spec.get('status', 200), **{
            key: value for key, value in spec.items() if key in ('json', 'text', 'headers')
        })

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs['transport'] = httpx.MockTransport(respond)
            super().__init__(*args, **kwargs)

    with patch.object(httpx, 'AsyncClient', OfflineClient):
        import main

        client = TestClient(main.app, base_url='http://127.0.0.1:8472',
                            headers={'Origin': 'http://127.0.0.1:8472'})
        try:
            replies = []
            for action in scenario['actions']:
                response = client.request(action['method'], action['path'], **{
                    key: value for key, value in action.items() if key == 'json'
                })
                replies.append({'http_status': response.status_code, 'body': response.json()})
        finally:
            client.close()
    print('ROUTE_RESULT=' + json.dumps({'replies': replies, 'requests': captured}, ensure_ascii=True))


def test_missing_node_is_not_detected_and_provider_can_still_be_saved(tmp_path):
    result = exercise(tmp_path, {
        STATS_PATH: {'json': SYSTEM_STATS}, NODES_PATH: {'json': {}},
    }, actions=[probe(), {'method': 'PUT', 'path': '/api/providers', 'json': [PROVIDER]},
                {'method': 'GET', 'path': '/api/providers'}])
    failed, saved, retrieved = result['replies']
    assert failed['http_status'] == 200
    assert failed['body']['ok'] is False
    assert failed['body']['status'] == 200
    assert failed['body']['protocol'] == 'comfyui'
    assert failed['body']['model_count'] == 0
    assert '服务可达，但当前无法生图' in failed['body']['message']
    assert NODE in failed['body']['message']
    assert '已检测到' not in failed['body']['message']
    for reply in (saved, retrieved):
        assert reply['http_status'] == 200
        provider = next(item for item in reply['body']['providers'] if item['id'] == 'comfyui')
        assert {key: provider[key] for key in PROVIDER} == PROVIDER


@pytest.mark.parametrize(('node_data', 'detected'), [
    pytest.param({NODE: H3_METADATA}, True, id='official-envelope'),
    pytest.param({NODE: {'input': {}, 'output': []}}, True, id='without-optional-fields'),
    pytest.param({}, False, id='official-missing-node'),
    pytest.param([], False, id='array'),
    pytest.param({'other_node': H3_METADATA}, False, id='other-node'),
    pytest.param({NODE: {'name': NODE}}, False, id='name-only'),
    pytest.param({NODE: True}, False, id='boolean'),
    pytest.param({NODE: 'installed'}, False, id='string'),
    pytest.param({NODE: {'input': [], 'output': []}}, False, id='invalid-input'),
    pytest.param({NODE: {'input': {}, 'output': 'CONDITIONING'}}, False, id='invalid-output'),
    pytest.param({NODE: {'input': {}, 'output': [123]}}, False, id='invalid-output-types'),
])
def test_only_genuine_node_metadata_is_detected(tmp_path, node_data, detected):
    result = exercise(tmp_path, {
        STATS_PATH: {'json': SYSTEM_STATS}, NODES_PATH: {'json': node_data},
    })
    reply = result['replies'][0]
    assert reply['http_status'] == 200
    assert reply['body']['status'] == 200
    assert reply['body']['ok'] is detected
    assert ('已检测到' in reply['body']['message']) is detected
    if not detected:
        assert '服务可达，但当前无法生图' in reply['body']['message']
        assert NODE in reply['body']['message']


@pytest.mark.parametrize(('stats_response', 'valid'), [
    pytest.param({'json': SYSTEM_STATS}, True, id='official-stats'),
    pytest.param({'json': {'system': {}, 'devices': []}}, True, id='minimal-stats'),
    pytest.param({'json': {}}, False, id='empty-object'),
    pytest.param({'json': []}, False, id='array'),
    pytest.param({'json': {'status': 'ok'}}, False, id='unrelated-json'),
    pytest.param({'json': {'system': [], 'devices': []}}, False, id='invalid-system'),
    pytest.param({'json': {'system': {}, 'devices': {}}}, False, id='invalid-devices'),
    pytest.param({'text': '<!DOCTYPE html><html>Login</html>'}, False, id='html'),
    pytest.param({'text': ''}, False, id='empty-body'),
    pytest.param({'text': 'not JSON'}, False, id='plain-text'),
])
def test_only_comfyui_stats_json_identifies_the_service(tmp_path, stats_response, valid):
    result = exercise(tmp_path, {
        STATS_PATH: stats_response, NODES_PATH: {'json': {NODE: H3_METADATA}},
    })
    reply = result['replies'][0]
    assert reply['http_status'] == 200
    assert reply['body']['status'] == 200
    assert reply['body']['ok'] is valid
    assert ('已检测到' in reply['body']['message']) is valid
    if not valid:
        assert '服务商' in reply['body']['message']
        if stats_response.get('text', '').startswith('<!DOCTYPE'):
            assert 'HTML' in reply['body']['message']


@pytest.mark.parametrize('endpoint', [STATS_PATH, NODES_PATH], ids=['stats', 'nodes'])
@pytest.mark.parametrize(('spec', 'diagnostic'), [
    pytest.param({'status': 401, 'text': 'credential=sk-private-472'}, '认证失败', id='authentication'),
    pytest.param({'status': 403, 'text': 'credential=sk-private-472'}, '认证失败', id='forbidden'),
    pytest.param({'status': 503, 'text': 'credential=sk-private-472'}, '服务暂不可用', id='unavailable'),
    pytest.param({'status': 302, 'headers': {'Location': 'https://external.example.test/login?key=sk-private-472'}},
                 '跳转', id='redirect'),
    pytest.param({'text': '<html>credential=sk-private-472</html>'}, 'HTML', id='html'),
    pytest.param({'json': {NODE: H3_METADATA, **SYSTEM_STATS}, 'headers': {'Content-Type': 'text/html'}},
                 'HTML', id='declared-html'),
])
def test_probe_errors_are_distinguishable_and_redacted(tmp_path, endpoint, spec, diagnostic):
    responses = {STATS_PATH: {'json': SYSTEM_STATS}, NODES_PATH: {'json': {NODE: H3_METADATA}}}
    responses[endpoint] = spec
    result = exercise(tmp_path, responses, actions=[probe(api_key='sk-private-472')])
    reply = result['replies'][0]
    assert reply['http_status'] == 200
    assert reply['body']['ok'] is False
    assert reply['body']['status'] == spec.get('status', 200)
    assert diagnostic in reply['body']['message']
    assert 'sk-private-472' not in json.dumps(reply['body'])
    assert '已检测到' not in reply['body']['message']
    assert all(request['url'].startswith(BASE_URL + '/') for request in result['requests'])


@pytest.mark.parametrize('endpoint', [STATS_PATH, NODES_PATH], ids=['stats', 'nodes'])
@pytest.mark.parametrize(('exception', 'diagnostic'), [
    ('ConnectError', '连接失败'), ('ConnectTimeout', '连接超时'),
    ('ReadTimeout', '读取超时'), ('RemoteProtocolError', 'HTTP 请求失败'),
])
def test_transport_errors_distinguish_connection_from_read_timeout(tmp_path, endpoint, exception, diagnostic):
    responses = {STATS_PATH: {'json': SYSTEM_STATS}, NODES_PATH: {'json': {NODE: H3_METADATA}}}
    responses[endpoint] = {'exception': exception,
                           'detail': 'https://user:password-private-472@comfy.example.test/?key=sk-private-472 Bearer sk-private-472'}
    result = exercise(tmp_path, responses, actions=[probe(api_key='sk-private-472')])
    reply = result['replies'][0]
    assert reply['http_status'] == 200
    assert reply['body']['ok'] is False
    assert reply['body']['status'] == 0
    assert diagnostic in reply['body']['message']
    assert 'sk-private-472' not in json.dumps(reply['body'])
    assert 'password-private-472' not in json.dumps(reply['body'])
    if exception == 'ReadTimeout':
        assert endpoint.removeprefix('/deployment') in reply['body']['message']


@pytest.mark.parametrize('source', ['explicit', 'environment', 'saved'])
@pytest.mark.parametrize(('key', 'expected_auth'), [
    pytest.param('', None, id='no-key'),
    pytest.param('sk-probe-472', 'Bearer sk-probe-472', id='regular-key'),
    pytest.param('Bearer sk-probe-472', 'Bearer sk-probe-472', id='prefixed-key'),
    pytest.param('Bearer ', None, id='scheme-without-key'),
])
def test_probe_auth_is_optional_bearer_and_uses_saved_or_environment_key(tmp_path, source, key, expected_auth):
    responses = {STATS_PATH: {'json': SYSTEM_STATS}, NODES_PATH: {'json': {NODE: H3_METADATA}}}
    environment = {}
    action = probe()
    if source == 'saved':
        saved = exercise(tmp_path, responses, actions=[{
            'method': 'PUT', 'path': '/api/providers', 'json': [{**PROVIDER, 'api_key': key}],
        }])
        assert saved['replies'][0]['http_status'] == 200
    elif source == 'environment':
        environment['API_PROVIDER_COMFYUI_KEY'] = key
    else:
        action = probe(api_key=key)
        if key.strip():
            environment['API_PROVIDER_COMFYUI_KEY'] = 'wrong-environment-key'
    result = exercise(tmp_path, responses, actions=[action], environment=environment)
    assert result['replies'][0]['body']['ok'] is True
    assert {request['url'] for request in result['requests']} == {
        BASE_URL + '/system_stats', BASE_URL + '/object_info/' + NODE,
    }
    for request in result['requests']:
        assert request['method'] == 'GET'
        assert request['headers'].get('authorization') == expected_auth
        assert 'cookie' not in request['headers']
        assert request['timeout'] == {'connect': 15, 'read': 15, 'write': 15, 'pool': 15}


@pytest.mark.parametrize('userinfo', ['user:password-private-472', 'user'])
def test_probe_does_not_implicitly_use_basic_auth_from_url_credentials(tmp_path, userinfo):
    result = exercise(tmp_path, {
        STATS_PATH: {'json': SYSTEM_STATS}, NODES_PATH: {'json': {NODE: H3_METADATA}},
    }, actions=[probe(base_url=f'https://{userinfo}@comfy.example.test/deployment', api_key='sk-private-472')])
    reply = result['replies'][0]
    assert reply['http_status'] == 400
    assert 'Bearer' in reply['body']['detail']
    assert 'password-private-472' not in json.dumps(reply['body'])
    assert result['requests'] == []


@pytest.mark.parametrize('key', ['', 'sk-probe-472'], ids=['anonymous', 'bearer'])
def test_probe_does_not_reuse_login_cookies(tmp_path, key):
    result = exercise(tmp_path, {
        STATS_PATH: {'json': SYSTEM_STATS,
                     'headers': {'Set-Cookie': 'session=private-session-472; Path=/; Secure; HttpOnly'}},
        NODES_PATH: {'json': {NODE: H3_METADATA}},
    }, actions=[probe(api_key=key)])
    assert result['replies'][0]['body']['ok'] is True
    assert len(result['requests']) == 2
    for request in result['requests']:
        assert 'cookie' not in request['headers']
        assert request['headers'].get('authorization') == ('Bearer sk-probe-472' if key else None)


@pytest.mark.parametrize('group', ['required', 'optional'])
def test_node_input_groups_must_be_metadata_objects_when_present(tmp_path, group):
    result = exercise(tmp_path, {
        STATS_PATH: {'json': SYSTEM_STATS},
        NODES_PATH: {'json': {NODE: {'input': {group: ['not-a-field-map']}, 'output': ['CONDITIONING']}}},
    })
    reply = result['replies'][0]
    assert reply['http_status'] == 200
    assert reply['body']['ok'] is False
    assert reply['body']['status'] == 200
    assert '服务可达，但当前无法生图' in reply['body']['message']
    assert NODE in reply['body']['message']
    assert '已检测到' not in reply['body']['message']


if __name__ == '__main__':
    route_worker()
