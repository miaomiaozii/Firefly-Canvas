"""#493 audit P2: production AST bodies, installed SDK and MockTransport only."""
import ast
import asyncio
from contextvars import ContextVar
import importlib.util
import ipaddress
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import time
import types
import typing
import urllib.parse

from fastapi import HTTPException
import httpx
import pytest

BASE = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('_t493_transport', BASE / 'app/services/image_transport.py')
SDK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SDK)


def bind(path, names, ns):
    tree = ast.parse((BASE / path).read_text(encoding='utf-8'))
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in selected} == set(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), path, 'exec'), ns)


@pytest.fixture
def ns():
    scope = dict(httpx=httpx, asyncio=asyncio, Path=Path, Dict=typing.Dict, Any=typing.Any,
                 Tuple=typing.Tuple, re=re, urllib=urllib, ipaddress=ipaddress,
                 ProjectStoreError=RuntimeError, HTTPException=HTTPException, time=time, json=json,
                 _create_image_transport_client=SDK.create_image_transport_client,
                 _image_response_json=SDK.image_response_json,
                 _record_image_decode=SDK.record_image_decode,
                 AGENT_IMAGE_ATTEMPT=ContextVar('t493', default=False),
                 mark_accepted=lambda: None, logger=__import__('logging').getLogger('t493'),
                 _GENERATION_DOWNLOAD_MAX_BYTES=1024**3, _GENERATION_DOWNLOAD_MIN_PARALLEL=2*1024**2,
                 _GENERATION_DOWNLOAD_CHUNK_BYTES=1024**2, _GENERATION_DOWNLOAD_MAX_WORKERS=6)
    bind('app/core.py', {'ensure_public_generation_target', 'effective_protocol', 'effective_image_request_mode',
                        'normalize_image_request_mode', 'httpx_request_with_transient_retries', 'image_task_data', 'image_task_status'}, scope)
    scope.update(provider_protocol=lambda p: (p or {}).get('protocol', 'openai'), FIXED_PROTOCOL_PROVIDER_IDS=set(),
                 PER_MODEL_PROTOCOL_OPTIONS={'openai','gemini'}, PER_MODEL_REQUEST_MODE_OPTIONS={'openai-json','openai-video-proxy'},
                 SUPPORTED_IMAGE_REQUEST_MODES={'openai','openai-json','openai-video-proxy'},
                 detect_image_request_mode=lambda *a: '', is_soon_gemini_native_image_model=lambda *a: False)
    async def resolve(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1' if host == 'private.invalid' else '93.184.216.34', port))]
    scope['_resolve_generation_host'] = resolve
    bind('app/services/generation.py', {'_download_generation_asset', '_parallel_range_download', 'image_task_url_for_provider',
          'fetch_image_task_payload', 'wait_for_image_task', '_generate_soon_url_candidate', '_generate_ai_image_impl',
          '_extract_generation_image'}, scope)
    bind('app/services/providers.py', {'api_headers', '_soon_url_verified_target', '_soon_url_auto_provider'}, scope)
    scope.update(is_codex_provider=lambda p: False, is_gemini_cli_provider=lambda p: False,
                 is_apimart_provider=lambda p: (p or {}).get('protocol') == 'apimart',
                 bearer_auth_value=lambda key: 'Bearer '+key)
    svc = types.SimpleNamespace()
    def main(name):
        if name == 'api_headers': return scope['api_headers']
        if name == 'fetch_image_task_payload': return scope['fetch_image_task_payload']
        if name == 'provider_env_key_value': return lambda p: 'fake-empty-credential'
        if name == 'AI_BASE_URL': return ''
        raise AssertionError('unbound production namespace: '+name)
    svc._main = main
    svc.provider_endpoint_url = lambda p,k,d: p['base_url'].rstrip('/') + d
    scope['_svc'] = svc
    return scope


class Stream(httpx.AsyncByteStream):
    def __init__(self, length, *, cancel=False, truncated=False):
        self.length=length; self.cancel=cancel; self.truncated=truncated; self.closed=False
    async def __aiter__(self):
        remaining=self.length - int(self.truncated)
        while remaining:
            if self.cancel:
                raise asyncio.CancelledError()
            n=min(65536,remaining); remaining-=n
            yield b'Z'*n
    async def aclose(self): self.closed=True


def installed(ns, handler):
    kwargs=[]
    def create(*args, **kw):
        kwargs.append(dict(kw))
        return SDK.create_image_transport_client(*args, transport=httpx.MockTransport(handler), **kw)
    ns['_create_image_transport_client']=create
    return kwargs


@pytest.mark.parametrize('advertisement', ['none','weak','missing','strong','ignored','late-ignored'])
def test_initial_or_verified_range_exact_bytes(ns, tmp_path, advertisement):
    total=2*1024**2+17; calls=[]; streams=[]
    def serve(request):
        calls.append(request)
        assert request.headers['host']=='cdn.invalid'
        assert request.extensions['sni_hostname']=='cdn.invalid'
        assert request.url.host=='93.184.216.34'
        assert not {'authorization','x-goog-api-key'} & set(request.headers)
        headers={'Content-Length':str(total)}
        if advertisement!='none': headers.update({'Accept-Ranges':'bytes','ETag':'W/"v1"' if advertisement=='weak' else '"v1"'})
        if advertisement=='missing': headers.pop('ETag')
        status=200; length=total
        if 'range' in request.headers:
            assert request.headers['if-range']=='"v1"'
            start,end=map(int,request.headers['range'][6:].split('-'))
            if advertisement=='strong' or (advertisement=='late-ignored' and start==0):
                length=end-start+1;status=206
                headers.update({'Content-Length':str(length),'Content-Range':f'bytes {start}-{end}/{total}'})
        stream=Stream(length);streams.append(stream)
        return httpx.Response(status,headers=headers,stream=stream)
    kw=installed(ns,serve);path=tmp_path/'asset'
    asyncio.run(ns['_download_generation_asset']('https://cdn.invalid/result',path,httpx.Timeout(1)))
    assert path.read_bytes()==b'Z'*total
    assert len(calls)==(4 if advertisement=='strong' else 1 if advertisement in {'none','weak','missing'} else 5 if advertisement=='late-ignored' else 3)
    assert all(s.closed for s in streams)
    assert kw[0]['follow_redirects'] is False and kw[0]['trust_env'] is False
    assert kw[0]['max_upload_retries']==0


@pytest.mark.parametrize('fault', ['start','end','total','etag','missing-etag','weak-etag','encoding','length','truncated','cancel','ignored-changed','ignored-missing','redirect-private','redirect-range-private','late-ignored','late-etag','late-cancel','overflow'])
def test_range_integrity_and_cleanup(ns, tmp_path, fault):
    total=2*1024**2+17;calls=[]
    def serve(request):
        calls.append(request)
        if fault=='redirect-private' or (fault=='redirect-range-private' and 'range' in request.headers):
            return httpx.Response(302,headers={'Location':'https://private.invalid/secret'})
        headers={'Content-Length':str(total),'Accept-Ranges':'bytes','ETag':'"v1"'}
        status=200;length=total;truncated=False;cancel=False
        if 'range' in request.headers:
            start,end=map(int,request.headers['range'][6:].split('-'))
            status=206;length=end-start+1
            headers.update({'Content-Length':str(length),'Content-Range':f'bytes {start}-{end}/{total}'})
            if fault in {'start','end','total'}:
                headers['Content-Range']=f'bytes {start+int(fault=="start")}-{end+int(fault=="end")}/{total+int(fault=="total")}'
            if fault in {'etag','ignored-changed'} or (fault=='late-etag' and start>0):headers['ETag']='"v2"'
            if fault in {'missing-etag','ignored-missing'}:headers.pop('ETag')
            if fault=='weak-etag':headers['ETag']='W/"v1"'
            if fault=='encoding':headers['Content-Encoding']='gzip'
            if fault=='length':headers['Content-Length']=str(length+1)
            truncated=fault=='truncated';cancel=fault=='cancel' or (fault=='late-cancel' and start>0)
            if fault=='overflow':length+=1
            if fault.startswith('ignored-') or (fault=='late-ignored' and start>0):
                status=200;length=total;headers['Content-Length']=str(total);headers.pop('Content-Range')
        if fault=='late-ignored' and len(calls)>3 and 'range' not in request.headers:
            headers['ETag']='"v2"'
        return httpx.Response(status,headers=headers,stream=Stream(length,cancel=cancel,truncated=truncated))
    installed(ns,serve);path=tmp_path/'asset'
    with pytest.raises(asyncio.CancelledError if fault in {'cancel','late-cancel'} else (RuntimeError,httpx.HTTPError)):
        asyncio.run(ns['_download_generation_asset']('https://cdn.invalid/result',path,httpx.Timeout(1)))
    assert not path.exists()
    assert all(r.method=='GET' for r in calls)
    assert all(r.url.host!='127.0.0.1' for r in calls)


@pytest.mark.parametrize('fault', ['truncated','cancel','oversize'])
def test_initial_stream_fails_closed(ns,tmp_path,fault):
    total=2*1024**2+17
    if fault=='oversize': ns['_GENERATION_DOWNLOAD_MAX_BYTES']=total-1
    calls=[]
    def serve(request):
        calls.append(request)
        return httpx.Response(200,headers={'Content-Length':str(total)},stream=Stream(total,cancel=fault=='cancel',truncated=fault=='truncated'))
    installed(ns,serve);path=tmp_path/'asset'
    with pytest.raises(asyncio.CancelledError if fault=='cancel' else RuntimeError):
        asyncio.run(ns['_download_generation_asset']('https://cdn.invalid/result',path,httpx.Timeout(1)))
    assert len(calls)==1 and not path.exists()


def configure_generation(ns):
    ns.update(require_project_media_values_scope=lambda a: None, ONLINE_IMAGE_REFERENCE_MAX=16,
              route_openai_image_request=lambda p,m,s,**k:{'model':m,'params':{}},
              is_gpt_image_2_model=lambda m: False, model_spec_for=lambda m: {}, AI_REQUEST_TIMEOUT=30,
              APIMART_IMAGE_TASK_TIMEOUT=1, IMAGE_TASK_TIMEOUT=1, APIMART_IMAGE_POLL_INTERVAL=0,
              IMAGE_POLL_INTERVAL=0, APIMART_IMAGE_INITIAL_POLL_DELAY=0,
              IMAGE_TASK_SUCCESS_STATUSES={'SUCCESS'}, IMAGE_TASK_FAILED_STATUSES={'FAILED'},
              IMAGE_TASK_UNKNOWN_STATUSES={'NOT_FOUND','UNKNOWN'}, IMAGE_TASK_UNKNOWN_TERMINAL_AFTER=2,
              extract_task_id=lambda p:p.get('task_id',''))
    for name in ('is_comfyui_provider','is_runninghub_provider','is_volcengine_provider','is_minimax_provider'):
        ns[name]=lambda p: False
    def extract(raw):
        if 'image' not in raw:raise HTTPException(502,'no image')
        return raw['image']
    ns['extract_image']=extract


@pytest.mark.parametrize('global_protocol,global_mode', [('gemini','openai-json'),('openai','openai-video-proxy')])
@pytest.mark.parametrize('unknown', [False,True])
def test_candidate_selected_model_poll_no_regeneration(ns,global_protocol,global_mode,unknown):
    configure_generation(ns);calls=[]
    provider={'id':'selected','base_url':'https://api.invalid','protocol':global_protocol,'image_request_mode':global_mode,
              'model_protocols':{'chosen':'openai'},'model_request_modes':{'chosen':'openai-json'}}
    original=json.dumps(provider,sort_keys=True)
    def serve(request):
        calls.append(request)
        assert request.headers.get('authorization')=='Bearer fake-empty-credential'
        assert 'x-goog-api-key' not in request.headers
        if request.method=='POST':
            assert request.url.path=='/v1/images/generations'
            assert json.loads(request.content)['model']=='chosen'
            return httpx.Response(200,json={'task_id':'accepted'})
        assert request.url.path=='/v1/images/tasks/accepted'
        return httpx.Response(404 if unknown else 200,json={} if unknown else {'status':'SUCCESS','image':{'type':'url','value':'https://cdn.invalid/result'}})
    installed(ns,serve)
    async def run():
        result=await ns['_generate_soon_url_candidate']('p','1024x1024','','chosen',provider=provider)
        assert result[1]['task_id']=='accepted'
    if unknown:
        with pytest.raises(HTTPException) as exc:asyncio.run(run())
        assert exc.value.upstream_task_id=='accepted'
    else:asyncio.run(run())
    assert [r.method for r in calls] == (['POST','GET','GET'] if unknown else ['POST','GET'])
    assert json.dumps(provider,sort_keys=True)==original


def test_actual_video_submission_and_poll_unchanged(ns):
    configure_generation(ns)
    ns['runninghub_aspect_from_size']=lambda *a:'1:1'
    calls=[]
    provider={'id':'selected','base_url':'https://api.invalid','protocol':'openai','image_request_mode':'openai-video-proxy'}
    def serve(request):
        calls.append(request)
        assert request.headers['authorization']=='Bearer fake-empty-credential'
        assert request.url.path==('/v1/videos' if request.method=='POST' else '/v1/videos/accepted')
        return httpx.Response(200,json={'task_id':'accepted'} if request.method=='POST' else {'status':'SUCCESS','image':{'type':'url','value':'https://cdn.invalid/result'}})
    installed(ns,serve)
    asyncio.run(ns['_generate_ai_image_impl']('p','1024x1024','','video',approved_provider=provider))
    assert [r.method for r in calls]==['POST','GET']


def test_missing_length_still_enforces_stream_limit(ns,tmp_path):
    ns['_GENERATION_DOWNLOAD_MAX_BYTES']=3
    installed(ns,lambda request:httpx.Response(200,stream=Stream(4)))
    path=tmp_path/'asset'
    with pytest.raises(RuntimeError):
        asyncio.run(ns['_download_generation_asset']('https://cdn.invalid/result',path,httpx.Timeout(1)))
    assert not path.exists()


def test_legacy_video_and_optional_poll_signatures(ns):
    provider={'base_url':'https://api.invalid/v1','image_request_mode':'openai-video-proxy'}
    assert ns['image_task_url_for_provider'](provider,'t')=='https://api.invalid/v1/videos/t'
    assert ns['image_task_url_for_provider'](provider,'t',model='actual-video')=='https://api.invalid/v1/videos/t'


@pytest.mark.parametrize('global_protocol,global_mode', [('gemini','openai-json'),('openai','openai-video-proxy')])
def test_recovery_uses_queue_model_preserves_stored_identity(ns, monkeypatch, global_protocol, global_mode):
    ns.update(Mapping=typing.Mapping, hashlib=hashlib)
    bind('firefly_generation.py', {'is_soon_provider'}, ns)
    bind('app/services/result_downloads.py', {'provider_identity'}, ns)
    bind('app/routers/api_project_library.py', {'_recover_project_image_result_link'}, ns)
    provider={'id':'selected','base_url':'https://soonstudio.ai','protocol':global_protocol,'image_request_mode':global_mode,
              'model_protocols':{'chosen':'openai'},'model_request_modes':{'chosen':'openai-json'}}
    app=types.SimpleNamespace(api_headers=ns['api_headers'],get_api_provider_required=lambda *a,**k:provider,
                              project_store_call=lambda *a:None)
    ns.update(_app_main=app, recover_project_image_task=lambda *a,**k:({},{}),
              ResultLinkExpired=type('Expired',(RuntimeError,),{}), ResultRecoveryPending=type('Pending',(RuntimeError,),{}),
              EXPIRED='expired', extract_images=lambda raw:raw['images'],
              result_metadata=lambda candidate,raw,now: candidate)
    modules={'firefly_generation':{'is_soon_provider':ns['is_soon_provider']},
             'app.services.generation':{'image_task_url_for_provider':ns['image_task_url_for_provider']},
             'app.core':{'ensure_public_generation_target':ns['ensure_public_generation_target']},
             'app.services.result_downloads':{'_PRIVATE_DOWNLOAD':ContextVar('private-t493',default=False)}}
    for name,contents in modules.items():
        module=types.ModuleType(name);module.__dict__.update(contents);monkeypatch.setitem(sys.modules,name,module)
    calls=[]
    def serve(request):
        calls.append(request)
        assert request.method=='GET' and request.url.path=='/v1/images/tasks/accepted'
        assert request.url.host=='93.184.216.34' and request.headers['host']=='soonstudio.ai'
        assert request.extensions['sni_hostname']=='soonstudio.ai'
        assert request.headers['authorization']=='Bearer fake-empty-credential'
        assert 'x-goog-api-key' not in request.headers
        return httpx.Response(200,json={'images':[{'type':'url','value':'https://cdn.invalid/result'}]})
    sdk=types.ModuleType('app.services.image_transport')
    sdk.create_image_transport_client=lambda **kw: SDK.create_image_transport_client(transport=httpx.MockTransport(serve),**kw)
    sdk.image_response_json=SDK.image_response_json
    monkeypatch.setitem(sys.modules,'app.services.image_transport',sdk)
    queue=dict(project_id='p',task_id='t',provider_id='selected',model='chosen',node_id='n',history_id='h',generation=1,
               provider_identity=ns['provider_identity'](provider,ns['api_headers'](provider=provider)))
    result=asyncio.run(ns['_recover_project_image_result_link'](queue,{'upstream_task_id':'accepted','id':'i','upstream_output_index':0}))
    assert result['value']=='https://cdn.invalid/result' and len(calls)==1
