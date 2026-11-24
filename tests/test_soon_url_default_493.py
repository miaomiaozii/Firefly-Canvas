"""Evidence-bounded runtime routing; real function ASTs + installed HTTPX/httpcore, no main."""
import ast
import asyncio
import base64
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import re
import struct
import sys
import urllib.parse
import zlib
from unittest.mock import patch

import httpcore
import httpx
from fastapi import HTTPException
from PIL import Image
import pytest

BASE = Path(__file__).parents[1]
FIXTURES = BASE / 'tests/fixtures/soon-url-493'
MODEL = 'gemini-3.1-flash-image-preview'
PROMPT = ('Use the reference layout exactly: red square on the left, blue circle in the center, '
          'green triangle on the right, all on white. Keep square red and triangle green; '
          'change only the center circle from blue to purple. No text. Create one image.')
SPEC = importlib.util.spec_from_file_location('_soon493_pipeline', BASE / 'tests/test_image_generation_pipeline_493.py')
previous = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(previous)
pipeline, no_outbound = previous.pipeline, previous.no_outbound
transport = previous.transport


def load_soon_runtime_ast(path, names, ns):
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    chosen = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            chosen.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            chosen.append(node)
    found = {n.name for n in chosen if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    found.update(t.id for n in chosen if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name))
    assert names <= found, names - found
    exec(compile(ast.Module(body=chosen, type_ignores=[]), str(path), 'exec'), ns)
    return ns


def soon_default_provider(**overrides):
    return {'id': 'synthetic-soon-alpha', 'name': 'Soon', 'enabled': True,
            'base_url': 'https://api.soonstudio.ai', 'protocol': 'openai',
            'image_request_mode': 'openai', 'model_protocols': {}, 'model_request_modes': {},
            'image_models': [MODEL, MODEL + '-4k'], **overrides}


@pytest.fixture
def runtime(pipeline, tmp_path):
    ns = pipeline
    ns.update(re=re, math=math, urllib=urllib, sys=sys, IMAGE_MODEL='not-the-selected-model')
    load_soon_runtime_ast(BASE / 'firefly_generation.py', {'is_soon_provider', 'SOON_GEMINI_NATIVE_ALIASES',
         'soon_gemini_native_model', 'is_soon_gemini_native_image_model'}, ns)
    load_soon_runtime_ast(BASE / 'app/core.py', {'SUPPORTED_IMAGE_REQUEST_MODES', 'PER_MODEL_REQUEST_MODE_OPTIONS',
         'PER_MODEL_PROTOCOL_OPTIONS', 'FIXED_PROTOCOL_PROVIDER_IDS', 'GEMINI_IMAGE_ASPECT_RATIOS',
         'provider_protocol', 'normalize_image_request_mode', 'effective_image_request_mode',
         'effective_protocol', 'detect_image_request_mode', 'parse_size_pair', 'selected_model',
         'banana_model_family', 'looks_like_gemini_image_model', 'gemini_supported_aspect_ratio',
         'apimart_size_resolution', 'banana_image_request_params', 'image_provider_model_ids',
         'route_openai_image_request', 'gemini_model_name', 'is_gpt_image_2_model',
         'is_codex_provider', 'is_gemini_cli_provider', 'is_runninghub_provider',
         'is_volcengine_provider', 'is_minimax_provider', 'is_apimart_provider', 'is_fhl_provider'}, ns)
    load_soon_runtime_ast(BASE / 'app/services/providers.py', {'_soon_url_verified_target', '_soon_url_auto_provider',
         'provider_endpoint_url', 'gemini_endpoint_url'}, ns)
    load_soon_runtime_ast(BASE / 'app/services/generation.py', {'generate_ai_image', 'is_comfyui_provider'}, ns)
    ns['_generation_reference_bytes'] = sys.modules['_pipeline493.media'].generation_reference_bytes
    ns.update(model_spec_for=lambda m: {},
              require_project_media_values_scope=lambda values: None,
              _diag_emit=lambda *a, **kw: None,
              _provider_diagnostic_sensitive_values=lambda *a, **kw: [],
              _provider_generation_semaphore=lambda p: asyncio.Semaphore(1),
              mark_accepted=lambda: None, extract_task_id=lambda raw: raw.get('task_id'),
              AI_REQUEST_TIMEOUT=1800)
    ns['_svc'].provider_endpoint_url = ns['provider_endpoint_url']
    ns['_svc'].gemini_endpoint_url = ns['gemini_endpoint_url']
    ns['_provider'] = soon_default_provider()
    ns['_scope_calls'] = []
    ns['require_project_media_values_scope'] = lambda values: ns['_scope_calls'].append(values)
    def main(name):
        return {'get_api_provider_exact': lambda pid: ns['_provider'],
                'get_api_provider_required': lambda pid: ns['_provider'],
                'api_headers': lambda **kw: ns['_api_headers'](**kw), 'AI_BASE_URL': '',
                'provider_env_key_value': lambda pid: 'offline-synthetic-key',
                'fetch_image_task_payload': lambda *a, **kw: ns['fetch_image_task_payload'](*a, **kw),
                'local_media_adapter_path': lambda value, kind: str(tmp_path / value.split('/')[-1]) if value.startswith('/output/') else None}[name]
    ns['_svc']._main = main
    ns['_api_headers'] = lambda **kw: {}
    return ns


class MemoryWire(httpcore.AsyncNetworkStream):
    def __init__(self, status, payload):
        body = json.dumps(payload).encode()
        self.response = (f'HTTP/1.1 {status} Test\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body)
        self.writes = []
    async def read(self, max_bytes, timeout=None):
        value, self.response = self.response, b''
        return value
    async def write(self, buffer, timeout=None):
        self.writes.append(bytes(buffer))
    async def start_tls(self, *args, **kwargs):
        return self
    async def aclose(self):
        pass
    def get_extra_info(self, info):
        return False if info == 'is_readable' else None


class MemoryNetwork(httpcore.AsyncNetworkBackend):
    def __init__(self, replies):
        self.replies, self.wires = list(replies), []
    async def connect_tcp(self, *args, **kwargs):
        assert self.replies, 'unexpected retry or extra endpoint'
        wire = MemoryWire(*self.replies.pop(0))
        self.wires.append(wire)
        return wire
    def requests(self):
        result = []
        for wire in self.wires:
            head, body = b''.join(wire.writes).split(b'\r\n\r\n', 1)
            result.append((head.split(b'\r\n', 1)[0].decode(), json.loads(body) if body else None))
        return result

    def raw_bodies(self):
        # #493：预算契约是对 HTTPX 实际序列化字节的断言，不是 dict 估算。
        return [b''.join(wire.writes).split(b'\r\n\r\n', 1)[1] for wire in self.wires]


def install_soon_memory_network(monkeypatch, replies=None):
    network = MemoryNetwork(replies or [(200, {'image': {'type': 'url', 'value': 'https://result.invalid/image.png'}})])
    original = transport.ImageTransportClient.__init__
    def init(self, **kw):
        # Windows system proxy discovery is not part of this offline body/routing proof.
        original(self, **{**kw, 'trust_env': False})
        self._transport._pool._network_backend = network
        # HTTPX also creates environment-proxy pools; none may escape the in-memory backend.
        for mounted in self._mounts.values():
            if mounted is not None:
                mounted._pool._network_backend = network
    monkeypatch.setattr(transport.ImageTransportClient, '__init__', init)
    return network


def run_soon_default_generation(runtime, refs=None, model=MODEL, **kwargs):
    return asyncio.run(runtime['generate_ai_image'](PROMPT, '4096x3072', 'high', model,
        refs or [], runtime['_provider']['id'], **kwargs))


def soon_reference_png(size=(1024, 768), mode='RGBA'):
    output = io.BytesIO()
    Image.new(mode, size, (10, 20, 30, 73) if mode == 'RGBA' else (10, 20, 30)).save(output, format='PNG')
    return output.getvalue()


def ref(raw):
    return {'url': 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')}


def test_ordinary_default_entry_is_candidate_semantically_without_mutating_provider(runtime, monkeypatch):
    original = copy.deepcopy(runtime['_provider'])
    raw = soon_reference_png()
    network = install_soon_memory_network(monkeypatch, [(200, {'image': {}})] * 2)
    params = {'aspect_ratio': '4:3', 'resolution': '4k'}
    run_soon_default_generation(runtime, [ref(raw)], image_parameters=params)
    explicit = copy.deepcopy(original)
    explicit['model_protocols'][MODEL] = 'openai'
    explicit['model_request_modes'][MODEL] = 'openai-json'
    asyncio.run(runtime['_generate_soon_url_candidate'](PROMPT, '4096x3072', 'high', MODEL,
        [ref(raw)], provider=explicit, image_parameters=params))
    ordinary, candidate = network.requests()
    assert ordinary == candidate
    assert ordinary[0].startswith('POST /v1/images/generations ')
    assert ordinary[1] == {'model': MODEL, 'prompt': PROMPT,
        'aspect_ratio': '4:3', 'resolution': '4k', 'image_size': '4K',
        'extra_body': {'response_format': 'url', 'image': [ref(raw)['url']]}}
    assert runtime['_provider'] == original
    assert params == {'aspect_ratio': '4:3', 'resolution': '4k'}
    assert len(runtime['_scope_calls']) == 2


def test_ordinary_body_equals_frozen_paid_candidate_and_receipt(runtime, monkeypatch):
    provenance = json.loads((FIXTURES / 'provenance.json').read_text(encoding='utf-8'))
    frozen_body = gzip.decompress(base64.b64decode((FIXTURES / provenance['body']['path']).read_bytes()))
    recorded = json.loads(frozen_body)
    receipt = provenance['receipt']['request_proof']
    # The original receipt anchors prompt/reference bytes; the body hash comes from offline frozen-source reconstruction.
    assert provenance['source']['sha256'] == '01692128a43aaad571ce5b3e54f7b6a6aea3b65a709b1c51128d08b7f9725e62'
    assert provenance['receipt']['sha256'] == '8d843a1c209ed10c47e81bd5da533528519f5419e32a7a0abbcb8e204f3aee1d'
    assert hashlib.sha256(frozen_body).hexdigest() == provenance['body']['sha256'] == '4571273b3ae885cec3227383af92e45423553fcd4cb1dac35a97d1926d5d2a0c'
    assert len(frozen_body) == provenance['body']['bytes'] == receipt['actual_json_body_bytes'] == 9182
    assert provenance['paid_cohort_accounts'] == provenance['receipt']['observed_post_requests'] == 1
    original = base64.b64decode(recorded['extra_body']['image'][0].split(',', 1)[1], validate=True)
    assert len(original) == receipt['original_file_bytes'] == receipt['prepared_file_bytes'] == 6565
    assert hashlib.sha256(original).hexdigest() == receipt['original_reference_sha256'] == receipt['prepared_reference_sha256'] == '1af00dc6751ec03031a507176068667a54eedc870f908a9c0b755f1db8fad9f4'
    assert hashlib.sha256(PROMPT.encode()).hexdigest() == receipt['prompt_sha256']
    with Image.open(io.BytesIO(original)) as image:
        image.verify()
    with Image.open(io.BytesIO(original)) as image:
        assert image.size == (receipt['prepared_width'], receipt['prepared_height']) == (1024, 768)
        assert image.mode == 'RGBA'
    network = install_soon_memory_network(monkeypatch, [(200, {'image': {}})] * 2)
    with patch.object(Image.Image, 'save', side_effect=AssertionError('frozen reference must not be re-encoded')):
        run_soon_default_generation(runtime, [ref(original)], image_parameters={'aspect_ratio': '4:3', 'resolution': '4k'})
        explicit = soon_default_provider(model_protocols={MODEL: 'openai'}, model_request_modes={MODEL: 'openai-json'})
        asyncio.run(runtime['_generate_soon_url_candidate'](PROMPT, '4096x3072', '', MODEL,
            [ref(original)], provider=explicit, image_parameters={'aspect_ratio': '4:3', 'resolution': '4k'}))
    first, candidate = network.requests()
    assert first == candidate == ('POST /v1/images/generations HTTP/1.1', recorded)
    for wire in network.wires:
        actual = b''.join(wire.writes).split(b'\r\n\r\n', 1)[1]
        assert actual == frozen_body
        assert hashlib.sha256(actual).hexdigest() == provenance['body']['sha256']
        sent = base64.b64decode(json.loads(actual)['extra_body']['image'][0].split(',', 1)[1], validate=True)
        assert sent == original


def test_explicit_candidate_wire_body_matches_portable_frozen_record(runtime, monkeypatch):
    provenance = json.loads((FIXTURES / 'provenance.json').read_text(encoding='utf-8'))
    frozen_body = gzip.decompress(base64.b64decode((FIXTURES / provenance['body']['path']).read_bytes()))
    recorded = json.loads(frozen_body)
    original = base64.b64decode(recorded['extra_body']['image'][0].split(',', 1)[1], validate=True)
    network = install_soon_memory_network(monkeypatch)
    explicit = soon_default_provider(model_protocols={MODEL: 'openai'}, model_request_modes={MODEL: 'openai-json'})
    with patch.object(Image.Image, 'save', side_effect=AssertionError('frozen reference must not be re-encoded')):
        asyncio.run(runtime['_generate_soon_url_candidate'](PROMPT, '4096x3072', '', MODEL,
            [ref(original)], provider=explicit, image_parameters={'aspect_ratio': '4:3', 'resolution': '4k'}))
    assert network.requests() == [('POST /v1/images/generations HTTP/1.1', recorded)]
    actual = b''.join(network.wires[0].writes).split(b'\r\n\r\n', 1)[1]
    assert actual == frozen_body
    assert len(actual) == provenance['receipt']['request_proof']['actual_json_body_bytes'] == 9182
    assert hashlib.sha256(actual).hexdigest() == provenance['body']['sha256'] == '4571273b3ae885cec3227383af92e45423553fcd4cb1dac35a97d1926d5d2a0c'
    assert hashlib.sha256(original).hexdigest() == provenance['receipt']['request_proof']['prepared_reference_sha256'] == '1af00dc6751ec03031a507176068667a54eedc870f908a9c0b755f1db8fad9f4'


@pytest.mark.parametrize('provider_id', ['synthetic-soon-alpha', 'synthetic-soon-beta'])
@pytest.mark.parametrize('base_url', ['https://api.soonstudio.ai', 'https://api.soonstudio.ai/',
    'https://api.soonstudio.ai/v1', 'https://api.soonstudio.ai/v1/'])
def test_endpoint_model_profile_is_independent_of_local_id(runtime, monkeypatch, provider_id, base_url):
    saved = soon_default_provider(id=provider_id, base_url=base_url)
    original = copy.deepcopy(saved)
    profile = runtime['_soon_url_auto_provider'](saved, MODEL)
    assert runtime['_soon_url_verified_target'](saved, MODEL) is True
    assert profile is not saved
    assert profile == {**saved, 'model_protocols': {MODEL: 'openai'}, 'model_request_modes': {MODEL: 'openai-json'}}
    assert saved == original
    runtime['_provider'] = saved
    network = install_soon_memory_network(monkeypatch)
    run_soon_default_generation(runtime, image_parameters={'aspect_ratio': '4:3', 'resolution': '4k'})
    assert network.requests() == [('POST /v1/images/generations HTTP/1.1', {
        'model': MODEL, 'prompt': PROMPT, 'extra_body': {'response_format': 'url'},
        'aspect_ratio': '4:3', 'image_size': '4K', 'resolution': '4k'})]
    assert saved == original


@pytest.mark.parametrize('base_url', ['http://api.soonstudio.ai', 'https://api-cn.soonstudio.ai',
    'https://soonstudio.ai', 'https://api.soonstudio.ai.evil.invalid', 'https://api.soonstudio.ai:443',
    'https://user@api.soonstudio.ai', 'https://api.soonstudio.ai/v2',
    'https://api.soonstudio.ai/v1/images/generations', 'https://api.soonstudio.ai?mode=url',
    'https://api.soonstudio.ai/#fragment', 'https://[invalid'])
def test_endpoint_profile_does_not_broaden_url_boundary(runtime, base_url):
    saved = soon_default_provider(base_url=base_url)
    assert runtime['_soon_url_verified_target'](saved, MODEL) is False
    assert runtime['_soon_url_auto_provider'](saved, MODEL) is saved


def test_missing_provider_does_not_match_endpoint_profile(runtime):
    assert runtime['_soon_url_verified_target'](None, MODEL) is False


@pytest.mark.parametrize('field,value', [('model_protocols', 'gemini'), ('model_protocols', 'openai'),
    ('model_request_modes', 'openai'), ('model_request_modes', 'openai-json'), ('model_request_modes', 'fal')])
def test_explicit_mapping_is_never_overwritten(runtime, field, value):
    saved = soon_default_provider(**{field: {MODEL: value}})
    assert runtime['_soon_url_auto_provider'](saved, MODEL) is saved


@pytest.mark.parametrize('changes,model', [({}, 'gemini-3-pro-image'), ({}, 'gemini-nano-banana-2.1'),
    ({}, 'nano-banana-pro'), ({}, MODEL + '-4k'), ({'base_url': 'https://soonstudio.ai'}, MODEL),
    ({'base_url': 'https://api-cn.soonstudio.ai'}, MODEL),
    ({'base_url': 'https://soonstudio.ai.evil.invalid', 'protocol': 'gemini'}, MODEL),
    ({'image_generation_endpoint': '/custom/generate'}, MODEL),
    ({'protocol': 'gemini'}, MODEL), ({'model_protocols': {MODEL: 'gemini'}}, MODEL),
    ({'model_request_modes': {MODEL: 'openai-json'}}, MODEL)])
def test_unverified_or_explicit_native_uses_actual_native_entry(runtime, monkeypatch, changes, model):
    runtime['_provider'] = soon_default_provider(**changes)
    network = install_soon_memory_network(monkeypatch)
    run_soon_default_generation(runtime, model=model, image_parameters={'aspect_ratio': '4:3', 'resolution': '4k'})
    request, body = network.requests()[0]
    assert request.startswith('POST ')
    assert 'extra_body' not in body
    assert body['contents'] == [{'role': 'user', 'parts': [{'text': PROMPT}]}]
    assert body['generationConfig']['imageConfig'] == {'aspectRatio': '4:3', 'imageSize': '4K'}
    assert len(network.requests()) == 1


def test_manual_json_stays_json_and_budgeted(runtime, monkeypatch):
    runtime['_provider'] = soon_default_provider(model_protocols={MODEL: 'openai'}, model_request_modes={MODEL: 'openai-json'})
    network = install_soon_memory_network(monkeypatch)
    run_soon_default_generation(runtime, [ref(soon_reference_png())], preserve_model=True, lock_model=True,
        image_parameters={'aspect_ratio': '4:3', 'resolution': '4k'})
    assert network.requests()[0][1]['extra_body']['response_format'] == 'url'


# #493 用户裁定：旧「准备字节求和 > 8,000,000 即拒」策略已被替换——
# 已知上限按 HTTPX 实际序列化请求体判定（本地目标 16,500,000 / 硬限 16,777,216），超限只压发送副本。
def test_over_local_limit_ladder_engages_and_still_posts(runtime, monkeypatch):
    import random
    pixels = random.Random(493).randbytes(2000 * 1200 * 4)
    buffer = io.BytesIO()
    Image.frombytes('RGBA', (2000, 1200), pixels).save(buffer, format='PNG')
    raw = buffer.getvalue()
    assert 9_000_000 < len(raw) < 11_500_000  # 两张实际序列化必超 16.5M 本地目标；旧策略下也必拒
    network = install_soon_memory_network(monkeypatch)
    run_soon_default_generation(runtime, [ref(raw), ref(raw)])
    bodies = network.raw_bodies()
    assert len(bodies) == 1  # 不预先拒绝，也不反复重试：一次达标即提交
    assert len(bodies[0]) <= 16_500_000
    body = json.loads(bodies[0])
    sent = [base64.b64decode(value.split(',', 1)[1]) for value in body['extra_body']['image']]
    assert any(value != raw for value in sent)  # 阶梯每轮只推进当前最大的一张，首个达标即停
    for value in sent:
        with Image.open(io.BytesIO(value)) as image:
            assert max(image.size) <= 4096


def test_within_local_limit_baseline_byte_identical_no_reencode(runtime, monkeypatch):
    import random
    pixels = random.Random(493).randbytes(2000 * 1200 * 4)
    buffer = io.BytesIO()
    Image.frombytes('RGBA', (2000, 1200), pixels).save(buffer, format='PNG')
    raw = buffer.getvalue()
    assert 9_000_000 < len(raw) < 11_500_000  # 单张实际序列化 ≈ 13M：旧 8M 求和会拒，新策略直通
    network = install_soon_memory_network(monkeypatch)
    with patch.object(Image.Image, 'save', side_effect=AssertionError('预算内不得重编码')):
        run_soon_default_generation(runtime, [ref(raw)])
    body = network.requests()[0][1]
    assert len(network.raw_bodies()[0]) > 8_000_000  # 旧求和规则已废除：体积按实际序列化计
    assert base64.b64decode(body['extra_body']['image'][0].split(',', 1)[1]) == raw


@pytest.mark.parametrize('scheme', ['https://', 'HTTPS://', 'http://'])
@pytest.mark.parametrize('manual', [False, True])
def test_remote_reference_passthrough_counted_and_posts(runtime, monkeypatch, manual, scheme):
    # 裁定：全请求体可测后，远程引用不再「unmeasurable」拒绝；字符串诚实计入，永不压缩。
    if manual:
        runtime['_provider'] = soon_default_provider(model_protocols={MODEL: 'openai'}, model_request_modes={MODEL: 'openai-json'})
    network = install_soon_memory_network(monkeypatch)
    run_soon_default_generation(runtime, [{'url': scheme + 'remote.invalid/ref.png', 'bytes': 1, 'mime_type': 'image/png'}],
        preserve_model=manual, lock_model=manual)
    body = network.requests()[0][1]
    assert body['extra_body']['image'] == [scheme + 'remote.invalid/ref.png']


@pytest.mark.parametrize('size', [(4096, 9), (5000, 9)])
def test_budget_after_mandatory_4096_and_no_extra_encoding(runtime, monkeypatch, tmp_path, size):
    raw = soon_reference_png(size)
    path = tmp_path / 'alpha.png'
    path.write_bytes(raw)
    network = install_soon_memory_network(monkeypatch)
    saves = []
    save = Image.Image.save
    def record_save(image, *a, **kw):
        saves.append((image.size, kw.get('format')))
        return save(image, *a, **kw)
    with patch.object(Image.Image, 'save', record_save):
        run_soon_default_generation(runtime, [{'url': '/output/alpha.png'}])
    sent = base64.b64decode(network.requests()[0][1]['extra_body']['image'][0].split(',', 1)[1])
    # #500 废除了 #493 的强制 4096 预缩放：预算内逐字节发原图（两参都成立）；函数名保留以免打乱基线测试 ID 清单。
    with Image.open(io.BytesIO(sent)) as image:
        assert image.size == size
        assert image.mode == 'RGBA' and image.getchannel('A').getextrema() == (73, 73)
    assert saves == []
    assert sent == raw
    assert path.read_bytes() == raw


@pytest.mark.parametrize('count', [1, 2])
def test_actual_valid_png_multi_reference_budget_not_source_or_json_size(runtime, monkeypatch, count):
    # 同名接缝保留：高熵 RGBA 不超 max_edge；预算判定看实际序列化体积，不看源文件求和。
    import random
    pixels = random.Random(493).randbytes(1500 * 1000 * 4)
    buffer = io.BytesIO()
    Image.frombytes('RGBA', (1500, 1000), pixels).save(buffer, format='PNG')
    raw = buffer.getvalue()
    assert 5_900_000 < len(raw) < 8_000_000
    network = install_soon_memory_network(monkeypatch)
    if count == 1:
        with patch.object(Image.Image, 'save', side_effect=AssertionError('budget must not re-encode')):
            run_soon_default_generation(runtime, [ref(raw)])
        body = network.requests()[0][1]
        assert len(json.dumps(body)) > 8_000_000
        assert base64.b64decode(body['extra_body']['image'][0].split(',', 1)[1]) == raw
    else:
        run_soon_default_generation(runtime, [ref(raw)] * count)
        assert len(network.raw_bodies()[0]) <= 16_500_000  # 阶梯介入后提交，不再预先拒


@pytest.mark.parametrize('height,expected', [(500, 'baseline'), (1000, 'ladder')])
def test_real_oversized_source_is_measured_only_after_mandatory_resize(runtime, monkeypatch, tmp_path, height, expected):
    # #500：基线 = 原图字节，序列化级判定；超限才压发送副本。参数名/测试 ID 保留以免打乱基线清单。
    import random
    image = Image.frombytes('RGBA', (5000, height), random.Random(493).randbytes(5000 * height * 4))
    path = tmp_path / 'large.png'
    image.save(path, format='PNG', compress_level=0)
    original = path.read_bytes()
    assert len(original) > 8_000_000
    network = install_soon_memory_network(monkeypatch)
    saves = []
    save = Image.Image.save
    def record_save(img, *a, **kw):
        saves.append(img.size)
        return save(img, *a, **kw)
    with patch.object(Image.Image, 'save', record_save):
        run_soon_default_generation(runtime, [{'url': '/output/large.png'}])
    bodies = network.raw_bodies()
    assert len(bodies) == 1
    assert len(bodies[0]) <= 16_500_000
    baseline = original  # #500 基线不再 4096 预缩放
    sent = base64.b64decode(json.loads(bodies[0])['extra_body']['image'][0].split(',', 1)[1])
    if expected == 'baseline':
        assert sent == baseline
        assert saves == []
    else:
        assert len(baseline) * 4 // 3 > 16_500_000  # 原图序列化仍超本地目标：阶梯必须介入
        assert sent != baseline
        with Image.open(io.BytesIO(sent)) as result:
            # RGBA 噪声源：#500 透明图走 WebP 质量档，先于尺寸档——尺寸保持原图。
            assert result.format == 'WEBP' and result.size == (5000, height)
    assert path.read_bytes() == original


@pytest.mark.parametrize('changes', [
    {'model_protocols': {MODEL: 'openai'}},
    {'base_url': 'https://another-provider.invalid', 'id': 'other-provider'},
    {'image_request_mode': 'openai-json', 'model_protocols': {MODEL: 'openai'}},
])
def test_manual_and_other_provider_modes_keep_existing_body(runtime, monkeypatch, changes):
    saved = soon_default_provider(**changes)
    runtime['_provider'] = saved
    assert runtime['_soon_url_auto_provider'](saved, MODEL) is saved
    network = install_soon_memory_network(monkeypatch)
    run_soon_default_generation(runtime, preserve_model=True, lock_model=True,
        image_parameters={'aspect_ratio': '4:3', 'resolution': '4k'})
    body = network.requests()[0][1]
    if changes.get('image_request_mode') == 'openai-json':
        assert body['extra_body'] == {'response_format': 'url'}
    else:
        assert 'extra_body' not in body and body['n'] == 1
    assert saved == soon_default_provider(**changes)


def test_apimart_reference_baseline_is_original_bytes_no_4096_prescale(runtime, monkeypatch):
    # #500 review 裁定（废 :786 预缩行）：基线=原图字节；长边 4200>4096 也必须原样直发。
    runtime['_provider'] = soon_default_provider(base_url='https://api.apimart.ai/v1')
    import random
    buffer = io.BytesIO()
    Image.frombytes('RGBA', (4200, 40), random.Random(500).randbytes(4200 * 40 * 4)).save(buffer, format='PNG')
    raw = buffer.getvalue()
    network = install_soon_memory_network(monkeypatch)
    run_soon_default_generation(runtime, refs=[ref(raw)])
    body = network.requests()[0][1]
    assert body['image_urls'] == ['data:image/png;base64,' + base64.b64encode(raw).decode('ascii')]


def test_apimart_oversize_413_engages_unified_ladder(runtime, monkeypatch):
    # #500：apimart 未登记上限 → 与 soon 同走 UNKNOWN 政策的 413 驱动阶梯，压不动才败。
    runtime['_provider'] = soon_default_provider(base_url='https://api.apimart.ai/v1')
    import random
    buffer = io.BytesIO()
    Image.frombytes('RGBA', (2600, 1600), random.Random(7).randbytes(2600 * 1600 * 4)).save(buffer, format='PNG')
    raw = buffer.getvalue()
    network = install_soon_memory_network(monkeypatch, [(413, {'error': 'too large'})] * 5)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(runtime['run_provider_generation_with_backoff'](runtime['_provider']['id'],
            runtime['generate_ai_image'], PROMPT, '1024x1024', '', MODEL, [ref(raw)], runtime['_provider']['id']))
    assert caught.value.status_code == 413
    bodies = network.raw_bodies()
    assert len(bodies) == 5  # 首发 + 最多 4 次重发，与 soon 同构
    for previous, current in zip(bodies, bodies[1:]):
        assert len(current) < len(previous)


def test_upstream_413_known_cap_conversion_then_final_fail(runtime, monkeypatch):
    # 裁定 A：已知上限仍被 413 → 阶梯转换一步后至多重提一次；再拒即最终失败。
    network = install_soon_memory_network(monkeypatch, [(413, {'error': 'request too large'}), (413, {'error': 'request too large'})])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(runtime['run_provider_generation_with_backoff'](runtime['_provider']['id'],
            runtime['generate_ai_image'], PROMPT, '4096x3072', '', MODEL, [ref(soon_reference_png((800, 600)))], runtime['_provider']['id']))
    assert caught.value.status_code == 413
    requests = network.requests()
    assert len(requests) == 2
    assert requests[0][0].startswith('POST /v1/images/generations ')
    assert requests[1][0] == requests[0][0]  # 不换端点不换模型
    assert requests[1][1]['extra_body']['image'] != requests[0][1]['extra_body']['image']


def test_upstream_413_known_cap_no_images_no_blind_resubmit(runtime, monkeypatch):
    network = install_soon_memory_network(monkeypatch, [(413, {'error': 'request too large'})])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(runtime['run_provider_generation_with_backoff'](runtime['_provider']['id'],
            runtime['generate_ai_image'], PROMPT, '4096x3072', '', MODEL, [], runtime['_provider']['id']))
    assert caught.value.status_code == 413
    assert len(network.requests()) == 1  # 无参考图可压：不盲目重发


def test_unknown_cap_413_ladder_max_four_resubmits_no_writeback(runtime, monkeypatch):
    import random
    runtime['_provider'] = soon_default_provider(id='other-provider', base_url='https://soon-alt.soonstudio.ai',
        image_request_mode='openai-json', model_protocols={MODEL: 'openai'})
    import random
    buffer = io.BytesIO()
    # 高熵噪声 + alpha：各档体积阶梯真实递减；长边 2600 提供 2560/2048/1536 缩小档 + 1536@80 底线，
    # 保证 4 次重发不会提前耗光阶梯（单调性由 prepare 契约保证：每轮 body ≤ floor(上轮×0.85)-1）。
    Image.frombytes('RGBA', (2600, 1600), random.Random(7).randbytes(2600 * 1600 * 4)).save(buffer, format='PNG')
    raw = buffer.getvalue()
    network = install_soon_memory_network(monkeypatch, [(413, {'error': 'too large'})] * 5)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(runtime['run_provider_generation_with_backoff'](runtime['_provider']['id'],
            runtime['generate_ai_image'], PROMPT, '1024x1024', '', MODEL, [ref(raw)], runtime['_provider']['id']))
    assert caught.value.status_code == 413
    print('F5DETAIL', getattr(caught.value,'detail',None))
    bodies = network.raw_bodies()
    print('F5BODIES', [len(b) for b in bodies])
    assert caught.value.status_code == 413
    bodies = network.raw_bodies()
    assert len(bodies) == 5  # 首发 + 最多 4 次重发
    for previous, current in zip(bodies, bodies[1:]):
        # 目标临时上限 floor(被拒正文×0.85)；阶梯压到底线时允许未达目标，但重发体积必须严格推进。
        assert len(current) < len(previous)
    # 不写回登记：同一 URL 仍是「上限未知」
    spec = importlib.util.spec_from_file_location('_budget493mod', BASE / 'app/services/image_request_budget.py')
    budget_mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, '_budget493mod', budget_mod)  # dataclass 注解后处理需要模块先在 sys.modules
    spec.loader.exec_module(budget_mod)
    assert budget_mod.lookup_known_body_limit('https://soon-alt.soonstudio.ai/v1/images/generations') is None


def test_ordinary_accepted_task_keeps_runtime_profile_for_get_only_poll(runtime, monkeypatch):
    load_soon_runtime_ast(BASE / 'app/services/providers.py', {'api_headers'}, runtime)
    load_soon_runtime_ast(BASE / 'app/services/generation.py', {'image_task_url_for_provider', 'fetch_image_task_payload',
         'wait_for_image_task'}, runtime)
    load_soon_runtime_ast(BASE / 'app/core.py', {'image_task_data', 'image_task_status', 'httpx_request_with_transient_retries',
         'IMAGE_TASK_SUCCESS_STATUSES', 'IMAGE_TASK_FAILED_STATUSES', 'IMAGE_TASK_UNKNOWN_STATUSES',
         'IMAGE_TASK_UNKNOWN_TERMINAL_AFTER'}, runtime)
    runtime.update(_api_headers=runtime['api_headers'],
        bearer_auth_value=lambda value: 'Bearer ' + value,
        IMAGE_TASK_TIMEOUT=30, IMAGE_POLL_INTERVAL=0)
    def extract(raw):
        if 'image' in raw:
            return raw['image']
        raise HTTPException(status_code=502, detail='no image yet')
    runtime['extract_image'] = extract
    network = install_soon_memory_network(monkeypatch, [(200, {'task_id': 'paid-original-task', 'status': 'pending'}),
        (200, {'status': 'completed', 'image': {'type': 'url', 'value': 'https://result.invalid/image.png'}})])
    result, raw = run_soon_default_generation(runtime)
    requests = network.requests()
    assert [request.split(' ', 2)[:2] for request, _body in requests] == [
        ['POST', '/v1/images/generations'], ['GET', '/v1/images/tasks/paid-original-task']]
    assert raw['task_id'] == 'paid-original-task'
    for wire in network.wires:
        head = b''.join(wire.writes).split(b'\r\n\r\n', 1)[0].lower()
        assert b'authorization: bearer offline-synthetic-key' in head
        assert b'x-goog-api-key' not in head
    assert runtime['_provider']['model_protocols'] == {}


@pytest.mark.parametrize('manual', [False, True])
def test_observed_accepted_8007313_does_not_raise_conservative_policy(runtime, monkeypatch, manual):
    observed = json.loads((FIXTURES / 'provenance.json').read_text(encoding='utf-8'))['observed_accepted_size']
    assert observed['receipt_sha256'] == 'fb38d663d4552d2530098dd0ff719dbab6e66177046589dec6494db52c85b490'
    assert observed['original_file_bytes'] == observed['prepared_file_bytes'] == 8_007_313
    assert observed['original_reference_sha256'] == observed['prepared_reference_sha256'] == '00b2a306ef5194540a69defd2b7f1a570089b139542a65a1f6dee935cb568da0'
    assert observed['actual_json_body_bytes'] == 10_676_846
    assert observed['state'] == 'archived-not-visual-acceptance' and observed['observed_post_requests'] == 1
    original = soon_reference_png((observed['prepared_width'], observed['prepared_height']))
    # Unknown private ancillary padding has a valid CRC; it is not the paid sample or an encoder-size assumption.
    kind = b'ffQa'
    padding = bytes(8_007_313 - len(original) - 12)
    chunk = struct.pack('>I', len(padding)) + kind + padding + struct.pack('>I', zlib.crc32(kind + padding))
    original = original[:-12] + chunk + original[-12:]
    assert len(original) == 8_007_313
    assert hashlib.sha256(original).hexdigest() != observed['prepared_reference_sha256']
    with Image.open(io.BytesIO(original)) as image:
        image.verify()
    with Image.open(io.BytesIO(original)) as image:
        image.load()
        assert image.size == (2048, 977) and image.mode == 'RGBA'
        assert image.getchannel('A').getextrema() == (73, 73)
    if manual:
        runtime['_provider'] = soon_default_provider(model_protocols={MODEL: 'openai'}, model_request_modes={MODEL: 'openai-json'})
    network = install_soon_memory_network(monkeypatch)
    with patch.object(Image.Image, 'save', side_effect=AssertionError('budget must not re-encode')):
        prepared, mime, _filename = runtime['_generation_reference_bytes'](ref(original), max_edge=4096)
        assert prepared == original and mime == 'image/png'
        run_soon_default_generation(runtime, [ref(original)], preserve_model=manual, lock_model=manual)
    # 实测证据：该 10,676,846 B 实际 JSON 请求体历史上已被接受；新策略下它在 16.5M 本地目标内，
    # 必须原字节提交，不触发任何阶梯。
    bodies = network.raw_bodies()
    assert len(bodies) == 1
    assert network.requests()[0][1]['extra_body']['image'][0] == 'data:image/png;base64,' + base64.b64encode(original).decode('ascii')


def test_generation_scope_rejection_precedes_preparation_and_post(runtime, monkeypatch):
    network = install_soon_memory_network(monkeypatch)
    def deny(values):
        raise HTTPException(status_code=403, detail='project scope denied')
    runtime['require_project_media_values_scope'] = deny
    with pytest.raises(HTTPException) as caught:
        run_soon_default_generation(runtime, [ref(soon_reference_png())])
    assert caught.value.status_code == 403
    assert network.wires == []


def test_reference_preparation_report_survives_generation_and_is_scalar_safe(runtime, monkeypatch):
    import random
    pixels = random.Random(493).randbytes(1650 * 1650 * 4)  # 交接会话的 fixture 尺寸基于旧 8M 求和策略；新实测基线（16.02M ≤ 16.5M）不会触发阶梯，尺寸调大使 known_cap 必然介入且两张都变。
    buffer = io.BytesIO()
    Image.frombytes('RGBA', (1650, 1650), pixels).save(buffer, format='PNG')
    raw = buffer.getvalue()
    assert 10_500_000 < len(raw) < 11_500_000
    network = install_soon_memory_network(monkeypatch)

    async def call():
        await runtime['run_provider_generation_with_backoff'](runtime['_provider']['id'],
            runtime['generate_ai_image'], PROMPT, '4096x3072', '', MODEL, [ref(raw)] * 2, runtime['_provider']['id'])
        return runtime['snapshot_reference_preparation']()

    report = asyncio.run(call())
    assert report and report['changed'] is True and report['policy'] == 'known_cap'
    changed = [item for item in report['references'] if item['changed']]
    assert len(changed) == 2 and all(item['final_format'] in ('WEBP', 'JPEG') for item in changed)
    allowed = {'index', 'source_format', 'final_format', 'quality', 'lossless', 'from_width', 'from_height',
               'width', 'height', 'byte_count', 'stage', 'changed'}
    assert all(set(item) == allowed for item in report['references'])


def test_attach_reference_preparation_only_when_changed():
    from app.services.misc import attach_reference_preparation
    items = [{'url': '/output/a.png', 'kind': 'image'}]
    attach_reference_preparation(items, None)
    attach_reference_preparation(items, {'changed': False, 'references': []})
    assert 'budget' not in items[0]
    report = {'policy': 'known_cap', 'changed': True, 'body_bytes': 1, 'resubmits': 0, 'rejected_body_bytes': [],
              'references': [{'index': 0, 'source_format': 'PNG', 'final_format': 'WEBP', 'lossless': False,
                              'quality': 90, 'from_width': 4096, 'from_height': 4096, 'width': 3072, 'height': 3072,
                              'byte_count': 1, 'stage': 2, 'changed': True}]}
    attach_reference_preparation(items, report)
    assert items[0]['budget']['references'][0]['final_format'] == 'WEBP'
    report['references'][0]['quality'] = 'mutated'
    assert items[0]['budget']['references'][0]['quality'] == 90  # 深拷贝：报告快照不被后续重试篡改
