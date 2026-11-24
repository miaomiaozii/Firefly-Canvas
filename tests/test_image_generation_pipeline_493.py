"""Actual function bodies with isolated dependencies; no app/main bootstrap or outbound I/O."""
import ast
import asyncio
import base64
from contextvars import ContextVar
import importlib.util
import io
import json
import logging
import mimetypes
import os
from pathlib import Path
import random
import socket
import struct
import sys
import tempfile
import time
import types
import urllib.parse
import uuid
import warnings

from fastapi import HTTPException
import httpcore
import httpx
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import pytest

from app.services.generation_failures import UpstreamImageFailure, provider_failure, response_failure, mark_accepted

BASE = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('_pipeline_transport493', BASE / 'app/services/image_transport.py')
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


def bodies(path, names, ns):
    tree = ast.parse((BASE / path).read_text(encoding='utf-8'))
    chosen = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=chosen, type_ignores=[]), path, 'exec'), ns)
    return ns


@pytest.fixture(autouse=True)
def no_outbound(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('outbound I/O forbidden')
    original_connect, original_pair = socket.socket.connect, socket.socketpair
    def self_pipe(*args, **kwargs):
        # Windows asyncio's self-pipe is a stdlib loopback socketpair, not HTTP I/O.
        with monkeypatch.context() as local:
            local.setattr(socket.socket, 'connect', original_connect)
            return original_pair(*args, **kwargs)
    monkeypatch.setattr(socket, 'socketpair', self_pipe)
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket, 'getaddrinfo', refuse)


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    pkg = types.ModuleType('_pipeline493')
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, pkg.__name__, pkg)
    media = types.ModuleType('_pipeline493.media')
    monkeypatch.setitem(sys.modules, media.__name__, media)
    svc = types.SimpleNamespace()
    def main(name):
        return {'local_media_adapter_path': lambda value, kind: str(tmp_path / value.split('/')[-1]) if value.startswith('/output/') else None,
                'api_headers': lambda **kw: {}, 'reference_to_data_url': lambda ref, **kw: 'legacy',
                'AI_BASE_URL': ''}[name]
    svc._main = main
    svc.gemini_reference_part = lambda ref: {'legacy': True}
    svc.gemini_endpoint_url = lambda *a: 'http://fixture.invalid/generate'
    svc.output_path_for = lambda filename, category: str(tmp_path / filename)
    svc.output_url_for = lambda filename, category: '/output/' + filename
    ns = dict(__package__='_pipeline493', _svc=svc, HTTPException=HTTPException,
              Optional=__import__('typing').Optional, Dict=dict, Any=object, Tuple=tuple, Mapping=__import__('collections.abc', fromlist=['Mapping']).Mapping,
              os=os, base64=base64, BytesIO=io.BytesIO, Image=Image, mimetypes=mimetypes,
              _warnings=warnings, _struct=struct, _GenerationPngInfo=PngInfo,
              content_type_for_path=lambda p: mimetypes.guess_type(p)[0] or 'application/octet-stream')
    media.__dict__.update(ns)
    bodies('app/services/media.py', {'_generation_reference_value', 'generation_reference_bytes', 'generation_reference_to_data_url'}, media.__dict__)
    bodies('app/services/conversation.py', {'gemini_generation_reference_part'}, ns)
    ns.update(httpx=httpx, asyncio=asyncio, time=time, random=random, json=json, uuid=uuid, Path=Path,
              tempfile=tempfile, urllib=urllib, logger=logging.getLogger('pipeline493'),
              ProjectStoreError=RuntimeError, ONLINE_IMAGE_REFERENCE_MAX=16,
              gemini_model_name=lambda m: m, soon_gemini_native_model=lambda p,m: m,
              AGENT_IMAGE_ATTEMPT=ContextVar('attempt493', default=False),
              _provider_generation_no_retry=lambda p: False,
              _provider_generation_semaphore=lambda p: asyncio.Semaphore(1),
              _retry_after_seconds=lambda r: None, log_net_error=lambda *a: None,
              rewrite_runninghub_file_url=lambda v: v,
              extract_image=lambda raw: raw['image'],
              _gemini_generation_reference_part=ns['gemini_generation_reference_part'],
              _create_image_transport_client=transport.create_image_transport_client,
              _image_response_json=transport.image_response_json,
              _record_image_decode=transport.record_image_decode,
              _record_current_image_decode=transport._record_current_image_decode,
              _generation_reference_to_data_url=media.generation_reference_to_data_url)
    bodies('app/core.py', {'gemini_image_config', 'validate_generated_image'}, ns)
    bodies('app/services/providers.py', {'_soon_url_verified_target', '_soon_url_auto_provider'}, ns)
    bodies('app/services/generation.py', {'generate_gemini_provider_image', 'run_provider_generation_with_backoff',
           '_extract_generation_image', '_generate_ai_image_impl', '_generate_soon_url_candidate', 'save_ai_image_to_output', '_download_generation_asset', '_parallel_range_download'}, ns)
    ns.update(_GENERATION_DOWNLOAD_MAX_BYTES=1024**3, _GENERATION_DOWNLOAD_MIN_PARALLEL=2*1024**2,
              _GENERATION_DOWNLOAD_CHUNK_BYTES=1024**2, _GENERATION_DOWNLOAD_MAX_WORKERS=6,
              _IMAGE_SUBMISSION_STARTED=ContextVar('submitted493', default=False),
              _image_upload_retry_scope=transport._image_upload_retry_scope,
              UpstreamImageFailure=UpstreamImageFailure, provider_failure=provider_failure,
              response_failure=response_failure, mark_accepted=mark_accepted)
    return ns


class StubClient:
    calls = []
    outcome = None
    def __init__(self, **kwargs):
        self.kwargs = kwargs
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def post(self, url, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return httpx.Response(self.outcome or 200, json={'task_id': 'accepted', 'image': {'type': 'url', 'value': 'http://fixture.invalid/result'}}, request=httpx.Request('POST', url))


@pytest.mark.parametrize('fmt,mode,edge', [('PNG','RGB',4096), ('JPEG','RGB',4096), ('PNG','RGBA',4096), ('PNG','RGBA',4097)])
def test_real_native_reference_policy(pipeline, monkeypatch, tmp_path, fmt, mode, edge):
    path = tmp_path / 'reference.png'
    image = Image.new(mode, (edge, 10), (20,40,60,73) if mode == 'RGBA' else (20,40,60))
    image.save(path, format=fmt)
    original = path.read_bytes()
    StubClient.calls, StubClient.outcome = [], None
    monkeypatch.setattr(httpx, 'AsyncClient', StubClient)
    asyncio.run(pipeline['generate_gemini_provider_image']('prompt', '2048x2048', 'gemini-test', [{'url':'/output/reference.png'}], {'id':'fixture'}, {'aspect_ratio':'16:9','resolution':'4k'}))
    body = StubClient.calls[0]['json']
    part = body['contents'][0]['parts'][1]['inlineData']
    raw = base64.b64decode(part['data'])
    with Image.open(io.BytesIO(raw)) as result:
        assert result.size == (min(edge,4096),10)
        assert part['mimeType'] == Image.MIME[fmt]
        if mode == 'RGBA':
            assert result.getchannel('A').getextrema() == (73,73)
    if edge <= 4096:
        assert raw == original
    assert path.read_bytes() == original
    assert body['generationConfig']['imageConfig'] == {'aspectRatio':'16:9','imageSize':'4K'}


@pytest.mark.parametrize('outcome', [429, httpx.ReadError('full-write/read failure'), httpx.WriteError('unknown'), asyncio.CancelledError()])
def test_native_outer_backoff_never_resubmits(pipeline, monkeypatch, outcome):
    StubClient.calls, StubClient.outcome = [], outcome
    monkeypatch.setattr(httpx, 'AsyncClient', StubClient)
    async def sleep(delay):
        pass
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    async def run():
        with pytest.raises(type(outcome) if isinstance(outcome, BaseException) else httpx.HTTPStatusError):
            await pipeline['run_provider_generation_with_backoff']('fixture', pipeline['generate_gemini_provider_image'], 'p','1024x1024','gemini-test', provider={'id':'fixture'}, image_parameters={'aspect_ratio':'16:9','resolution':'4k'})
    asyncio.run(run())
    assert len(StubClient.calls) == 1


def test_remote_unknown_mime_is_explicit_not_fabricated_png(pipeline):
    with pytest.raises(HTTPException) as caught:
        pipeline['gemini_generation_reference_part']({'url':'https://fixture.invalid/photo.jpg', 'mime_type':'image/jpeg'})
    assert caught.value.detail['code'] == 'image_generation_reference_mime_unverified'


def test_legacy_save_uses_pinned_archive_and_exact_bytes(pipeline, monkeypatch, tmp_path):
    image = Image.new('RGBA', (19,13), (2,3,4,73))
    data = io.BytesIO()
    image.save(data, format='PNG')
    original = data.getvalue()
    calls = []
    async def download(value, path, timeout):
        calls.append(value)
        path.write_bytes(original)
    pipeline['_download_generation_asset'] = download
    result = asyncio.run(pipeline['save_ai_image_to_output']({'type':'url','value':'https://fixture.invalid/image'}))
    assert calls == ['https://fixture.invalid/image']
    assert (tmp_path / result.split('/')[-1]).read_bytes() == original


def test_legacy_save_never_reports_remote_success(pipeline):
    async def download(*args):
        raise RuntimeError('private redirect rejected')
    pipeline['_download_generation_asset'] = download
    with pytest.raises(RuntimeError, match='private redirect rejected'):
        asyncio.run(pipeline['save_ai_image_to_output']({'type':'url','value':'https://fixture.invalid/image'}))


class Wire(httpcore.AsyncNetworkStream):
    def __init__(self, body, status=200):
        self.reads = [b'HTTP/1.1 ' + str(status).encode() + b' Test\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body]
        self.writes = []
    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)
    async def read(self, max_bytes, timeout=None):
        if self.reads:
            value = self.reads.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return b''
    async def start_tls(self, *args, **kwargs):
        return self
    async def aclose(self):
        pass
    def get_extra_info(self, info):
        return False if info == 'is_readable' else None


class Network(httpcore.AsyncNetworkBackend):
    def __init__(self, wire):
        self.wire, self.calls = wire, 0
    async def connect_tcp(self, *args, **kwargs):
        self.calls += 1
        if isinstance(self.wire, BaseException):
            raise self.wire
        return self.wire


def installed_client(monkeypatch, wire):
    network = Network(wire)
    original = transport.ImageTransportClient.__init__
    def init(self, **kwargs):
        original(self, **kwargs)
        self._transport._pool._network_backend = network
    monkeypatch.setattr(transport.ImageTransportClient, '__init__', init)
    return network


@pytest.mark.parametrize('generic', [False, True])
def test_actual_callers_capture_sdk_segments_and_original_params(pipeline, monkeypatch, tmp_path, generic):
    image = Image.new('RGBA', (19,13), (2,3,4,73))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    original = buffer.getvalue()
    encoded = base64.b64encode(original).decode('ascii')
    wire = Wire(json.dumps({'image':{'type':'b64','value':encoded,'mime_type':'image/png'}}).encode())
    network = installed_client(monkeypatch, wire)
    provider = {'id':'fixture','base_url':'http://fixture.invalid','image_bypass_system_proxy':True}
    receipts = []
    if generic:
        pipeline.update(require_project_media_values_scope=lambda values: None,
                        effective_protocol=lambda *a: 'openai', effective_image_request_mode=lambda *a: 'openai-json',
                        route_openai_image_request=lambda p,m,s,**kw: {'model':m,'params':{}},
                        banana_model_family=lambda m: True, looks_like_gemini_image_model=lambda m: True,
                        is_gpt_image_2_model=lambda m: False, model_spec_for=lambda m: {}, AI_REQUEST_TIMEOUT=1800)
        for name in ('is_codex_provider','is_gemini_cli_provider','is_comfyui_provider','is_runninghub_provider',
                     'is_volcengine_provider','is_minimax_provider','is_apimart_provider'):
            pipeline[name] = lambda p: False
        pipeline['_svc'].provider_endpoint_url = lambda p,k,default: 'http://fixture.invalid' + default
    async def run():
        with transport._capture_image_transport(receipts.append):
            if generic:
                candidate, raw = await pipeline['_generate_soon_url_candidate']('exact prompt','2048x2048','', 'gemini-test',
                    [{'url':'data:image/jpeg;base64,'+encoded}], provider=provider,
                    image_parameters={'aspect_ratio':'16:9','resolution':'4k'})
            else:
                candidate, raw = await pipeline['generate_gemini_provider_image']('exact prompt','2048x2048','gemini-test',
                    provider=provider, image_parameters={'aspect_ratio':'16:9','resolution':'4k'})
            saved = await pipeline['save_ai_image_to_output'](candidate)
            assert (tmp_path / saved.split('/')[-1]).read_bytes() == original
        assert transport._IMAGE_OBSERVATION_SINK.get() is None
        assert transport._IMAGE_OBSERVATION_RESPONSE.get() is None
    asyncio.run(run())
    assert network.calls == 1
    events = {r['event']:r for r in receipts}
    assert {'transport','json_decode','extract_decode','base64_decode','image_decode'} <= events.keys()
    record = events['image_decode']
    assert record['request_write_complete_ms'] is not None
    assert record['response_headers_complete_ms'] is not None
    assert record['first_raw_body_yield_ms'] is not None
    assert record['physical_ttfb_ms'] is None
    assert record['base64_output_bytes'] == record['image_output_bytes'] == len(original)
    assert record['json_decode_ms'] >= 0
    assert 'exact prompt' not in json.dumps(receipts)
    assert 'fixture.invalid' not in json.dumps(receipts)
    body = json.loads(b''.join(wire.writes).split(b'\r\n\r\n',1)[1])
    if generic:
        assert body['model'] == 'gemini-test'
        assert body['prompt'] == 'exact prompt'
        assert body['aspect_ratio'] == '16:9'
        assert body['image_size'] == '4K'
        assert body['extra_body']['response_format'] == 'url'
        assert base64.b64decode(body['extra_body']['image'][0].split(',',1)[1]) == original
    else:
        assert body['generationConfig']['imageConfig'] == {'aspectRatio':'16:9','imageSize':'4K'}


@pytest.mark.parametrize('failure', ['read', 'unknown', 'incomplete'])
def test_actual_installed_native_outer_does_not_restart_transport_budget(pipeline, monkeypatch, failure):
    delays = []
    async def sleep(delay):
        delays.append(delay)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    wire = Wire(b'')
    if failure == 'read':
        wire.reads = [httpcore.ReadError('after full write')]
    network = installed_client(monkeypatch, wire if failure == 'read' else httpcore.ConnectError('positive incomplete'))
    if failure == 'unknown':
        async def unknown(*args, **kwargs):
            network.calls += 1
            raise httpx.WriteError('no trace evidence')
        monkeypatch.setattr(transport.ImageTransportClient, '_send_single_request', unknown)
    async def run():
        with pytest.raises(httpx.HTTPError):
            await pipeline['run_provider_generation_with_backoff']('fixture', pipeline['generate_gemini_provider_image'],
                'p','2048x2048','gemini-test', provider={'image_bypass_system_proxy':True},
                image_parameters={'aspect_ratio':'16:9','resolution':'4k'})
    asyncio.run(run())
    assert network.calls == (3 if failure == 'incomplete' else 1)
    assert delays == ([1, 3] if failure == 'incomplete' else [])


def test_download_rejects_private_redirect_before_second_get(pipeline, monkeypatch, tmp_path):
    seen, gets = [], []
    async def resolve(value):
        seen.append(value)
        if '127.0.0.1' in value:
            raise RuntimeError('private redirect rejected')
        return 'https://93.184.216.34/result', {'Host':'fixture.invalid'}, {'sni_hostname':'fixture.invalid'}
    class RedirectClient(StubClient):
        def stream(self, method, url, **kwargs):
            gets.append((url,kwargs))
            class Context:
                async def __aenter__(self):
                    return httpx.Response(302, headers={'Location':'https://127.0.0.1/private'})
                async def __aexit__(self,*args):
                    pass
            return Context()
    monkeypatch.setattr(httpx,'AsyncClient',RedirectClient)
    pipeline['ensure_public_generation_target'] = resolve
    with pytest.raises(RuntimeError, match='private redirect rejected'):
        asyncio.run(pipeline['_download_generation_asset']('https://fixture.invalid/result',tmp_path/'result',httpx.Timeout(1)))
    assert len(gets) == 1
    assert seen == ['https://fixture.invalid/result','https://127.0.0.1/private']
    assert gets[0][1]['headers'] == {'Host':'fixture.invalid'}
    assert gets[0][1]['extensions'] == {'sni_hostname':'fixture.invalid'}


@pytest.mark.parametrize('cancel', [False, True])
def test_transport_budget_exhaustion_or_cancel_cannot_restart_outer(pipeline, monkeypatch, cancel):
    network = installed_client(monkeypatch, httpcore.ConnectError('proved before origin upload'))
    factory = pipeline['_create_image_transport_client']
    def opt_in(*args, **kwargs):
        kwargs['max_upload_retries'] = 2
        return factory(*args, **kwargs)
    pipeline['_create_image_transport_client'] = opt_in
    delays = []
    async def sleep(delay):
        delays.append(delay)
        if cancel:
            raise asyncio.CancelledError()
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    async def run():
        with pytest.raises(asyncio.CancelledError if cancel else httpx.ConnectError):
            await pipeline['run_provider_generation_with_backoff']('fixture', pipeline['generate_gemini_provider_image'],
                'p','2048x2048','gemini-test', provider={'image_bypass_system_proxy':True},
                image_parameters={'aspect_ratio':'16:9','resolution':'4k'})
    asyncio.run(run())
    assert network.calls == (1 if cancel else 3)
    assert delays == ([1] if cancel else [1,3])


def test_candidate_does_not_flip_native_default(pipeline):
    pipeline.update(effective_protocol=lambda *a:'gemini', effective_image_request_mode=lambda *a:'openai-json')
    with pytest.raises(HTTPException):
        asyncio.run(pipeline['_generate_soon_url_candidate']('p','2048x2048','','gemini-test',provider={'id':'fixture'}))


def test_private_imports_legacy_adapter_and_d_receipt_remain():
    tree = ast.parse((BASE / 'app/services/generation.py').read_text(encoding='utf-8'))
    imports = {(n.module,a.name,a.asname) for n in tree.body if isinstance(n,ast.ImportFrom) for a in n.names}
    assert ('conversation','gemini_generation_reference_part','_gemini_generation_reference_part') in imports
    conversation = (BASE / 'app/services/conversation.py').read_text(encoding='utf-8')
    legacy = next(n for n in ast.parse(conversation).body if isinstance(n,ast.FunctionDef) and n.name=='gemini_reference_part')
    assert "_svc._main('reference_to_data_url')(ref, max_size=1536)" in ast.get_source_segment(conversation,legacy)
    source = (BASE / 'app/services/generation.py').read_text(encoding='utf-8')
    assert "if isinstance(task_result, dict) and not extract_task_id(task_result):\n                task_result = {**task_result, 'task_id': task_id}" in source
