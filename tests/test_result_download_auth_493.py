"""Real submission → private checkpoint → original-task recovery, without app bootstrap."""
import ast
import asyncio
import contextlib
import copy
from contextvars import ContextVar
from datetime import datetime, timezone
import functools
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import threading
import time
import types
import uuid
import sys

import pytest

BASE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_auth493_runtime', BASE / 'tests/test_soon_url_default_493.py')
previous = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(previous)
pipeline, runtime, no_outbound = previous.pipeline, previous.runtime, previous.no_outbound
MODEL = previous.MODEL
httpx = previous.httpx
PROVIDER = {'id': 'synthetic-auth-493', 'base_url': 'https://api.soonstudio.ai/v1',
            'protocol': 'openai', 'image_request_mode': 'openai', 'image_models': [MODEL]}
AMBIGUOUS = '无法核验原生成请求身份，不能安全取回；不会重新生成'


def bind(path, names, ns):
    tree = ast.parse((BASE / path).read_text(encoding='utf-8-sig'))
    chosen = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
              and node.name in names]
    assert {node.name for node in chosen} == names
    exec(compile(ast.Module(body=chosen, type_ignores=[]), str(BASE / path), 'exec',
                 dont_inherit=True, flags=__import__('__future__').annotations.compiler_flag), ns)


@pytest.fixture
def auth(runtime, monkeypatch, tmp_path):
    ns = runtime
    bind('app/core.py', {'strip_auth_scheme', 'bearer_auth_value'}, ns)
    bind('app/services/providers.py', {'api_headers'}, ns)
    bind('app/services/generation.py', {'image_task_url_for_provider', 'fetch_image_task_payload'}, ns)
    bind('app/services/misc.py', {'run_routed_image_candidate'}, ns)
    ns.update(_api_headers=ns['api_headers'], _image_upload_retry_scope=contextlib.nullcontext,
              UpstreamImageFailure=type('UpstreamImageFailure', (Exception,), {}), provider_failure=lambda e: None,
              functools=functools, threading=threading, copy=copy, hashlib=hashlib, datetime=datetime,
              timezone=timezone, math=math, MAX_BYTES=1024**3, EXPIRED='expired')
    class StoreError(Exception):
        pass
    class Captured(BaseException):
        pass
    ns.update(ProjectStoreError=StoreError, InvalidMutation=type('InvalidMutation', (StoreError,), {}),
              ResultLinkExpired=type('ResultLinkExpired', (StoreError,), {}),
              ResultRecoveryPending=type('ResultRecoveryPending', (StoreError,), {}))
    # Local module bindings let imports inside the actual bodies run without configuration/bootstrap.
    modules = {}
    for name in ('app', 'app.services', 'app.core', 'firefly_generation', 'app.services.generation',
                 'app.services.providers', 'app.services.image_transport', 'app.services.result_downloads'):
        module = types.ModuleType(name)
        module.__path__ = []
        modules[name] = module
        monkeypatch.setitem(sys.modules, name, module)
    modules['app.core'].__dict__.update(ns)
    modules['app.core'].extract_task_id = lambda raw: raw.get('task_id')
    modules['firefly_generation'].is_soon_provider = ns['is_soon_provider']
    modules['app.services.generation'].image_task_url_for_provider = ns['image_task_url_for_provider']
    modules['app.services.providers']._soon_url_auto_provider = ns['_soon_url_auto_provider']
    modules['app.services.result_downloads']._PRIVATE_DOWNLOAD = ContextVar('auth493_private', default=False)
    requests, memory, checkpoint_providers, store_calls = [], {}, [], []
    current, key = [copy.deepcopy(PROVIDER)], ['offline-fake-auth-493']
    def respond(request):
        requests.append(request)
        assert request.method in {'POST', 'GET'}
        return httpx.Response(200, json={'task_id': 'original-task-493', 'image': {
            'type': 'url', 'value': 'https://result.invalid/' + ('expired' if request.method == 'POST' else 'fresh') + '.png'}})
    def client(*args, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
    ns['_create_image_transport_client'] = client
    modules['app.services.image_transport'].create_image_transport_client = client
    modules['app.services.image_transport'].image_response_json = lambda response: response.json()
    async def resolve(url):
        return url, {}, {}
    modules['app.core'].ensure_public_generation_target = resolve
    def store_call(op, *args, **kwargs):
        store_calls.append(op)
        if op == 'open_project':
            return {'canvas': {'nodes': [], 'history': []}}
        if op == 'list_media':
            return []
        assert op in {'flush', 'checkpoint_image_result_download', 'image_result_download_path'}
        return str(tmp_path / 'projects/result.tmp')
    app = types.SimpleNamespace(api_headers=ns['api_headers'], get_api_provider_required=lambda *a, **k: current[0],
        project_store_call=store_call, now_ms=lambda: int(time.time()*1000),
        FIREFLY_PROJECT_STORE=types.SimpleNamespace(root=tmp_path / 'projects'))
    ns['_svc']._main = lambda name: {'provider_env_key_value': lambda pid: key[0],
        'get_api_provider_exact': lambda pid: current[0], 'get_api_provider_required': lambda pid: current[0],
        'api_headers': ns['api_headers'], 'run_provider_generation_with_backoff': ns['run_provider_generation_with_backoff'],
        'generate_ai_image': ns['generate_ai_image'], 'AI_BASE_URL': ''}[name]
    bind('app/services/result_downloads.py', {'provider_identity', 'result_metadata', '_AsyncClaim', 'ResultDownloads'}, ns)
    queue = ns['ResultDownloads'](tmp_path / 'runtime', app)
    def load(hid):
        if hid not in memory:
            raise FileNotFoundError(hid)
        return copy.deepcopy(memory[hid])
    queue.load = load
    queue.save = lambda q: memory.update({q['history_id']: copy.deepcopy(q)})
    real_checkpoint = queue.checkpoint
    async def checkpoint(*args, **kwargs):
        checkpoint_providers.append(copy.deepcopy(args[4]))
        await real_checkpoint(*args, **kwargs)
        # Stop the real project runner before archive/UI effects, not before its durable checkpoint.
        raise Captured()
    queue.checkpoint = checkpoint
    ns.update(_app_main=app, downloads=lambda a: queue, get_api_provider=lambda pid: current[0],
        CANVAS_TASKS={'local-task': {}}, CANVAS_TASK_LOCK=threading.RLock(),
        PROJECT_MEDIA_ADAPTER_PROJECT=ContextVar('auth493_project', default=None),
        AGENT_IMAGE_ACCEPTED=ContextVar('auth493_accepted', default=False), project_image_references=lambda *a: [],
        project_image_executed_prompt=lambda p: p.prompt, update_project_image_task_progress=lambda *a, **k: None,
        raise_if_project_image_task_cancelled=lambda *a: None, image_generation_task_timeout_seconds=lambda: 10,
        PROJECT_IMAGE_PROGRESS_PHASES={}, recover_project_image_task=lambda *a, **k: ({}, {'status': 'download_failed'}),
        extract_images=lambda raw: [raw['image']], _CandidateWriteFailure=type('_CandidateWriteFailure', (Exception,), {}))
    bind('app/routers/api_project_library.py', {'run_project_image_task', '_recover_project_image_result_link'}, ns)
    async def submit(provider=None):
        saved = copy.deepcopy(PROVIDER if provider is None else provider)
        current[0] = copy.deepcopy(saved)
        ns['_provider'] = current[0]
        payload = types.SimpleNamespace(prompt='offline regression', reference_asset_ids=[], output_format='png', n=1,
            size='1024x1024', quality='high', idempotency_key='', result_node_id='result', model_dump=lambda: {'model': MODEL})
        snapshot = types.SimpleNamespace(width=1024, height=1024, aspect_ratio='1:1', resolution='1k', media_type='image')
        route = types.SimpleNamespace(provider_id=saved['id'], model=MODEL,
            to_dict=lambda: {'provider_id': saved['id'], 'model': MODEL})
        with pytest.raises(Captured):
            await ns['run_project_image_task']('local-task', 'project', payload, snapshot, route, 1)
        assert checkpoint_providers == [saved]
        assert current[0] == saved, 'invocation profile must not mutate stored provider'
        assert [r.method for r in requests] == ['POST']
        return copy.deepcopy(next(iter(memory.values())))
    async def direct(provider):
        current[0] = copy.deepcopy(provider)
        history = {'id': 'generation-' + uuid.uuid4().hex, 'task_id': 'local-task',
                   'result_node_id': 'result', 'actual_model': MODEL}
        await real_checkpoint('project', history, [{'type': 'url', 'value': 'https://result.invalid/expired.png'}],
                              {'task_id': 'original-task-493'}, current[0])
        return copy.deepcopy(next(iter(memory.values())))
    async def recover(q):
        return await ns['_recover_project_image_result_link'](q, q['items'][0])
    return types.SimpleNamespace(ns=ns, submit=submit, direct=direct, recover=recover, current=current, key=key,
                                 requests=requests, store_calls=store_calls, modules=modules)


def auth_headers(request):
    return {k: v for k, v in request.headers.items() if k in {'authorization', 'x-goog-api-key'}}


def test_actual_auto_submission_checkpoint_and_recovery_match(auth):
    async def run():
        q = await auth.submit()
        identity = auth.ns['provider_identity']
        headers = auth.ns['api_headers']
        invocation = auth.ns['_soon_url_auto_provider'](auth.current[0], MODEL)
        assert q['provider_identity'] == identity(PROVIDER, headers(provider=PROVIDER))
        assert q['model_identity'] == identity(PROVIDER, headers(provider=PROVIDER, model=MODEL), model=MODEL)
        assert q['invocation_identity'] == identity(invocation, headers(provider=invocation, model=MODEL), model=MODEL)
        assert q['invocation_identity'] != q['model_identity']
        assert (await auth.recover(q))['url'] == 'https://result.invalid/fresh.png'
        post, get = auth.requests
        assert [r.method for r in auth.requests] == ['POST', 'GET']
        assert str(post.url) == 'https://api.soonstudio.ai/v1/images/generations'
        assert json.loads(post.content)['model'] == q['model'] == MODEL
        assert str(get.url) == 'https://api.soonstudio.ai/v1/images/tasks/original-task-493'
        assert auth_headers(post) == auth_headers(get) == {'authorization': 'Bearer offline-fake-auth-493'}
    asyncio.run(run())


@pytest.mark.parametrize('guard', ['provider_identity', 'model_identity', 'invocation_identity',
                                   'account', 'credential', 'protocol', 'mode', 'profile'])
def test_config_model_account_and_invocation_guards_precede_get(auth, guard):
    async def run():
        q = await auth.submit()
        if guard.endswith('_identity'):
            q[guard] = '0' * 64
        elif guard == 'account':
            auth.current[0]['group'] = 'different-account'
        elif guard == 'credential':
            auth.key[0] = 'changed-fake-key'
        elif guard == 'protocol':
            auth.current[0]['model_protocols'] = {MODEL: 'gemini'}
        elif guard == 'mode':
            auth.current[0]['model_request_modes'] = {MODEL: 'openai-video-proxy'}
        else:
            auth.modules['app.services.providers']._soon_url_auto_provider = lambda provider, model: provider
        with pytest.raises(auth.ns['ResultLinkExpired']):
            await auth.recover(q)
        assert [r.method for r in auth.requests] == ['POST'], 'guard failure must never query or regenerate'
        assert 'image_result_download_path' not in auth.store_calls
    asyncio.run(run())


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('mode', ['openai-json', 'openai-video-proxy', 'gemini', 'nonprofile'])
def test_explicit_modes_and_unambiguous_legacy_keep_original_auth(auth, mode, legacy):
    async def run():
        provider = {**PROVIDER, 'image_request_mode': 'openai-video-proxy',
                    'model_protocols': {MODEL: 'openai'}, 'model_request_modes': {MODEL: mode}}
        if mode == 'gemini':
            provider.update(model_protocols={MODEL: 'gemini'}, model_request_modes={MODEL: 'openai-json'})
        elif mode == 'nonprofile':
            provider = {**PROVIDER, 'base_url': 'https://api-cn.soonstudio.ai/v1'}
        assert auth.ns['_soon_url_auto_provider'](provider, MODEL) is provider
        # Native/video generation is outside this bounded regression; their real checkpoint/recovery is not.
        q = await (auth.submit(provider) if mode == 'openai-json' else auth.direct(provider))
        if legacy:
            q.pop('invocation_identity')
            q.pop('model')
            q.pop('model_identity')
        result = await auth.recover(q)
        assert result['url'] == 'https://result.invalid/fresh.png'
        get = auth.requests[-1]
        expected = {'x-goog-api-key': 'offline-fake-auth-493'} if mode in {'gemini', 'nonprofile'} else {'authorization': 'Bearer offline-fake-auth-493'}
        assert auth_headers(get) == expected
        suffix = '/videos/original-task-493' if mode == 'openai-video-proxy' else '/images/tasks/original-task-493'
        assert get.url.path == '/v1' + suffix
        if mode == 'openai-json':
            assert auth_headers(auth.requests[0]) == auth_headers(get)
            assert [r.method for r in auth.requests] == ['POST', 'GET']
    asyncio.run(run())


def test_legacy_ambiguous_fails_closed_with_clear_reason_and_zero_requests(auth):
    async def run():
        q = await auth.direct(PROVIDER)
        q.pop('invocation_identity')
        with pytest.raises(auth.ns['ResultLinkExpired'], match=AMBIGUOUS):
            await auth.recover(q)
        assert not auth.requests, 'ambiguous legacy identity must cause zero GET and zero POST'
        assert 'image_result_download_path' not in auth.store_calls
    asyncio.run(run())


@pytest.mark.parametrize('legacy', [False, True])
def test_unknown_original_model_is_not_guessed(auth, legacy):
    async def run():
        q = await auth.direct(PROVIDER)
        q.pop('model')
        q['history'] = {}
        if legacy:
            q.pop('invocation_identity')
        with pytest.raises(auth.ns['ResultLinkExpired']):
            await auth.recover(q)
        assert not auth.requests
    asyncio.run(run())
