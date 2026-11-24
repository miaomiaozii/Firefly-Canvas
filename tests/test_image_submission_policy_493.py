"""Paid-POST policy on real function ASTs and installed HTTPX/httpcore, offline only."""
import ast
import asyncio
import importlib.util
import json
import logging
from pathlib import Path
import socket
import time
import urllib.parse

import httpcore
import httpx
import pytest
from fastapi import HTTPException

BASE = Path(__file__).parents[1]
def load_submission_policy_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

previous = load_submission_policy_module('_policy_pipeline493', BASE / 'tests/test_image_generation_pipeline_493.py')
sdk = load_submission_policy_module('_policy_sdk493', BASE / 'tests/test_image_transport_493.py')
transport = previous.transport
pipeline = previous.pipeline
no_outbound = previous.no_outbound


def core_namespace():
    ns = dict(httpx=httpx, asyncio=asyncio, json=json, logger=logging.getLogger('policy493'),
              time=time, urllib=urllib, HTTPException=HTTPException,
              RESPONSES_REJECT_STATUSES={400,404,405,415,422},
              RESPONSES_POLL_MAX_SECONDS=60, RESPONSES_POLL_INTERVAL=0,
              extract_images=lambda raw: [], image_payload_from_string=lambda *a, **k: None)
    return previous.bodies('app/core.py', {'_responses_image_submission', '_responses_wrap',
        'post_openai_responses', 'post_openai_responses_stream'}, ns)


def response(payload, status=200):
    return sdk.response_bytes(json.dumps(payload).encode(), status)


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('failure', [400,429,500,524,'read','write','unknown','cancel'])
def test_image_responses_never_fallback_after_paid_post(streaming, failure):
    ns = core_namespace()
    exc = {'read':httpcore.ReadError(), 'write':httpcore.WriteError(),
           'unknown':RuntimeError(), 'cancel':asyncio.CancelledError()}.get(failure)
    wire = sdk.Stream(reads=[exc] if exc else [response({'id':'accepted-task'}, failure)],
                      fail_write=(2,exc) if failure == 'write' else None)
    async def run():
        async with transport.ImageTransportClient(trust_env=False, max_upload_retries=2) as client:
            fallback = sdk.Stream(reads=[response({},400),response({'output':[]})])
            if failure == 400:
                wire.reads.extend(fallback.reads)
            backend = sdk.backend_for(client, [wire,fallback])
            function = ns['post_openai_responses_stream' if streaming else 'post_openai_responses']
            try:
                result = await function(client, 'http://fixture.invalid/responses', {},
                                        {'tools':[{'type':'image_generation'}], 'input':'exact'})
                assert not exc
                assert result.status_code == (502 if failure == 524 and not streaming else failure)
            except BaseException as caught:
                assert exc is not None
                assert isinstance(caught, (httpx.HTTPError, RuntimeError, asyncio.CancelledError))
            assert backend.calls == 1
            assert sum(part.startswith(b'POST ') for part in wire.writes) == 1
    asyncio.run(run())


@pytest.mark.parametrize('poll_failure', [False, True])
def test_accepted_responses_only_get_original_task(poll_failure):
    ns = core_namespace()
    wire = sdk.Stream(reads=[response({'id':'accepted-task','status':'queued'})] +
        ([response({'error':'unavailable'},503)]*6 if poll_failure else [response({'id':'accepted-task','status':'completed'})]))
    async def run():
        async with transport.ImageTransportClient(trust_env=False,max_upload_retries=2) as client:
            sdk.backend_for(client,[wire])
            result = await ns['post_openai_responses'](client,'http://fixture.invalid/responses',{},
                                                      {'tools':[{'type':'image_generation'}]})
            assert result.status_code == (502 if poll_failure else 200)
            posts = [part for part in wire.writes if part.startswith(b'POST ')]
            gets = [part for part in wire.writes if part.startswith(b'GET ')]
            assert len(posts) == 1
            assert len(gets) == (6 if poll_failure else 1)
            assert all(b'/responses/accepted-task ' in part for part in gets)
    asyncio.run(run())


@pytest.mark.parametrize('initial', [400,'read'])
def test_text_responses_preserves_background_stream_direct_compatibility(initial):
    ns = core_namespace()
    first = sdk.Stream(reads=[httpcore.ReadError()] if initial == 'read' else [response({},400)])
    second = sdk.Stream(reads=[response({},400),response({'output_text':'chat unchanged'})])
    async def run():
        async with transport.ImageTransportClient(trust_env=False) as client:
            sdk.backend_for(client,[first,second])
            # A successful 400 exchange keeps the original connection alive.
            if initial == 400:
                first.reads.extend(second.reads)
            result = await ns['post_openai_responses'](client,'http://fixture.invalid/responses',{}, {'input':'text only'})
            assert result.json() == {'output_text':'chat unchanged'}
            writes = first.writes+second.writes
            assert sum(part.startswith(b'POST ') for part in writes) == 3
    asyncio.run(run())


@pytest.mark.parametrize('cancel', [False,True])
def test_native_real_factory_incomplete_budget_never_restarts(pipeline, monkeypatch, cancel):
    network = previous.installed_client(monkeypatch,httpcore.ConnectError('positive SDK connect evidence'))
    delays=[]
    async def sleep(delay):
        delays.append(delay)
        if cancel:
            raise asyncio.CancelledError()
    monkeypatch.setattr(asyncio,'sleep',sleep)
    async def run():
        with pytest.raises(asyncio.CancelledError if cancel else httpx.ConnectError):
            await pipeline['run_provider_generation_with_backoff']('fixture',pipeline['generate_gemini_provider_image'],
                'p','1024x1024','gemini-test',provider={'id':'fixture'},
                image_parameters={'aspect_ratio':'1:1','resolution':'1k'})
    asyncio.run(run())
    assert network.calls == (1 if cancel else 3)
    assert delays == ([1] if cancel else [1,3])


def test_header_write_failure_without_body_count_is_not_zero_evidence(monkeypatch):
    wire=sdk.Stream(reads=[httpcore.ReadError()],fail_write=(1,httpcore.WriteError()))
    async def run():
        async with transport.ImageTransportClient(trust_env=False,max_upload_retries=2) as client:
            backend=sdk.backend_for(client,[wire])
            with pytest.raises(httpx.HTTPError):
                await client.post('http://fixture.invalid',content=b'body')
            assert backend.calls == 1
            assert client.telemetry_records[-1]['request_body_yielded_bytes'] is None
            assert client.telemetry_records[-1]['retry_scheduled'] is False
    asyncio.run(run())


def test_outer_semaphore_still_bounds_concurrency(pipeline):
    async def run():
        semaphore=asyncio.Semaphore(2)
        pipeline['_provider_generation_semaphore']=lambda p:semaphore
        active=0
        peak=0
        calls=0
        async def generate():
            nonlocal active,peak,calls
            calls+=1; active+=1; peak=max(peak,active)
            await asyncio.sleep(0)
            active-=1
        await asyncio.gather(*(pipeline['run_provider_generation_with_backoff']('fixture',generate) for _ in range(9)))
        assert calls == 9
        assert peak == 2
    asyncio.run(run())


def test_dedicated_factory_budgets_and_nonimage_boundaries():
    tree=ast.parse((BASE/'app/services/generation.py').read_text(encoding='utf-8'))
    names={'generate_modelscope_provider_image','generate_runninghub_entry_image','generate_runninghub_provider_image',
           'generate_gemini_provider_image','generate_volcengine_provider_image','_generate_ai_image_impl'}
    for function in tree.body:
        if isinstance(function,ast.AsyncFunctionDef) and function.name in names:
            calls=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                   and n.func.id=='_create_image_transport_client']
            assert len(calls)==1,function.name
            assert next(k.value.value for k in calls[0].keywords if k.arg=='max_upload_retries')==2
    assert len(names)==6
    # MiniMax's stricter no-replay contract, CLI/native hosts and video stay outside opt-in.
    for name in ('generate_minimax_provider_image','generate_codex_provider_image_via_gpt_image_2_skill',
                 'generate_gemini_cli_provider_image','generate_comfyui_provider_image','generate_runninghub_video'):
        current=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                       and n.func.id=='_create_image_transport_client' for n in ast.walk(current)),name


@pytest.mark.parametrize('adapter', ['modelscope','volcengine','runninghub','runninghub_entry'])
@pytest.mark.parametrize('failure', [400,429,503,'read','unknown','cancel'])
def test_dedicated_image_adapters_single_submission(pipeline, monkeypatch, adapter, failure):
    names={'generate_modelscope_provider_image','generate_volcengine_provider_image',
           'generate_runninghub_provider_image','generate_runninghub_entry_image'}
    previous.bodies('app/services/generation.py',names,pipeline)
    svc=pipeline['_svc']
    async def definition(*args): return {'params':[]}
    original_main=svc._main
    svc._main=lambda name: definition if name=='runninghub_model_definition' else original_main(name)
    svc.modelscope_api_key=lambda:'synthetic'
    svc.volcengine_endpoint_url=lambda p:'http://fixture.invalid/generate'
    svc.volcengine_image_payload=lambda r:None
    svc.runninghub_json_headers=lambda p:{}
    svc.runninghub_app_headers=lambda *a:{}
    svc.runninghub_api_key=lambda *a,**kw:'synthetic'
    pipeline.update(parse_size_pair=lambda s:(1024,1024),selected_model=lambda m,d:m,
        MODELSCOPE_DEFAULT_IMAGE_MODEL='model',AI_REQUEST_TIMEOUT=30,
        modelscope_image_api_root=lambda:'http://fixture.invalid',
        runninghub_entry_config_from_model=lambda *a:None,
        runninghub_task_endpoint=lambda *a:'http://fixture.invalid/generate',
        runninghub_schema_field=lambda *a:None,runninghub_apply_schema_defaults=lambda *a:None,
        runninghub_aspect_from_size=lambda *a:'1:1',runninghub_resolution_from_size=lambda *a:'1k',
        rh_sort_fields=lambda f:f,rh_field_indexes=lambda f:{},
        runninghub_endpoint_url=lambda *a:'http://fixture.invalid/generate')
    exc={'read':httpcore.ReadError(),'unknown':RuntimeError(),'cancel':asyncio.CancelledError()}.get(failure)
    wire=sdk.Stream(reads=[exc] if exc else [response({'task_id':'accepted','data':{'taskId':'accepted'}},failure)])
    network=previous.installed_client(monkeypatch,wire)
    provider={'id':'fixture','base_url':'http://fixture.invalid'}
    async def run():
        function=pipeline['generate_'+('runninghub_entry' if adapter=='runninghub_entry' else adapter+'_provider')+'_image']
        with pytest.raises((httpx.HTTPError,HTTPException,RuntimeError,asyncio.CancelledError)):
            if adapter=='runninghub_entry':
                await function('exact prompt','1024x1024','model',[],provider,{'id':'entry','fields':[]})
            elif adapter=='volcengine':
                await function('exact prompt','1024x1024','model',provider=provider,preserve_size=True)
            else:
                await function('exact prompt','1024x1024','model',provider=provider)
    asyncio.run(run())
    assert network.calls==1
    assert sum(part.startswith(b'POST ') for part in wire.writes)==1


@pytest.mark.parametrize('mode', ['openai-json','openai-responses'])
@pytest.mark.parametrize('failure', [400,429,'incomplete'])
def test_generic_real_dispatcher_uses_one_shared_transport_budget(pipeline,monkeypatch,mode,failure):
    pipeline.update(core_namespace())
    pipeline.update(require_project_media_values_scope=lambda v:None,effective_protocol=lambda *a:'openai',
        effective_image_request_mode=lambda *a:mode,route_openai_image_request=lambda p,m,s,**kw:{'model':m,'params':{}},
        is_gpt_image_2_model=lambda m:False,model_spec_for=lambda m:{},AI_REQUEST_TIMEOUT=30,
        responses_proxy_tool_size=lambda s:s,responses_image_size_instruction=lambda s:'',
        locked_recommended_provider_rule=lambda *a:False)
    for name in ('is_codex_provider','is_gemini_cli_provider','is_comfyui_provider','is_runninghub_provider',
                 'is_volcengine_provider','is_minimax_provider','is_apimart_provider'):
        pipeline[name]=lambda p:False
    pipeline['_svc'].provider_endpoint_url=lambda p,k,d:'http://fixture.invalid'+d
    wire=sdk.Stream(reads=[response({'id':'accepted-task'},failure)]) if failure!='incomplete' else None
    network=previous.installed_client(monkeypatch,wire if wire else httpcore.ConnectError())
    delays=[]
    async def sleep(delay):delays.append(delay)
    monkeypatch.setattr(asyncio,'sleep',sleep)
    provider={'id':'fixture','base_url':'http://fixture.invalid'}
    async def run():
        with pytest.raises((httpx.HTTPError,HTTPException)):
            await pipeline['run_provider_generation_with_backoff']('fixture',pipeline['_generate_ai_image_impl'],
                'exact','1024x1024','','model',approved_provider=provider)
    asyncio.run(run())
    assert network.calls==(3 if failure=='incomplete' else 1)
    assert delays==([1,3] if failure=='incomplete' else [])
    if wire:
        assert sum(part.startswith(b'POST ') for part in wire.writes)==1
