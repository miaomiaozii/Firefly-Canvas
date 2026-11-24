import asyncio, base64, io, json
from copy import deepcopy
from unittest.mock import patch
import httpcore, httpx, pytest
from PIL import Image
import main
from app.services import generation as g, image_transport as t, generation_failures as f
from tests.test_image_transport_493 import Backend, Stream, response_bytes
from tests.test_agent_generation_failures_401 import start_batch
from tests.test_agent_batch_400 import fx, status_until

@pytest.mark.parametrize('status,body,safe',[
    (429,{'error':{'code':'rate_limit_exceeded'}},True),
    (503,{'error':{'code':'model_not_found','message':'无可用渠道'}},True),
    (400,{'error':{'message':'unsupported aspect ratio'}},True),
    (502,{'error':'gateway failed'},False),(408,{'error':'timeout'},False),
    (429,{'error':{'code':'rate_limit_exceeded'},'task_id':'accepted'},False),
    (429,{'error':{'code':'rate_limit_exceeded'},'data':[{'id':'accepted'}]},False),
    (429,{'error':{'code':'rate_limit_exceeded'},'data':{'result':{'outputs':[{'url':'https://result.invalid'}]}}},False),
    (429,{'error':{'code':'rate_limit_exceeded'},'status':'processing'},False),
    (429,{'error':{'code':'rate_limit_exceeded'},'data':{'accepted':True}},False),
    (200,{'error':{'status':429,'code':'rate_limit_exceeded'}},True),
    (200,{'error':{'message':'unsupported aspect ratio'}},False),
])
def test_rejection_is_not_transport_ambiguity(status,body,safe):
    r=httpx.Response(status,json=body,request=httpx.Request('POST','https://fixture.invalid'))
    desc=f.describe_failure(f.response_failure(r),'fixture')
    assert desc['replay_safe'] is safe
    assert desc['automatic_retry'] is safe

@pytest.mark.parametrize('error',[httpx.ReadTimeout('read'),httpx.WriteTimeout('write'),httpx.ReadError('read'),httpx.WriteError('write'),httpx.RemoteProtocolError('protocol'),RuntimeError('429 timeout')])
def test_unknown_full_body_failures_never_replay(error):
    assert not f.describe_failure(error,'fixture')['replay_safe']

def install(monkeypatch,outcomes):
    backend=Backend(outcomes)
    original=t.ImageTransportClient.__init__
    def init(self,**kw):
        original(self,**kw)
        if hasattr(self._transport,'_pool'):
            self._transport._pool._network_backend=backend
    monkeypatch.setattr(t.ImageTransportClient,'__init__',init)
    return backend

def partial():
    return Stream(reads=[httpcore.ReadError()],fail_write=(2,httpcore.WriteError()))
def rejection():
    return Stream(reads=[response_bytes(b'{"error":{"code":"rate_limit_exceeded"}}',429,b'Retry-After: 0\r\n')])
async def post():
    async with t.ImageTransportClient(trust_env=False,max_upload_retries=2) as client:
        response=await client.post('http://fixture.invalid/generate',files={'image':('a.png',b'original-bytes','image/png')})
        if response.status_code>=400:
            raise f.response_failure(response)
        return response

@pytest.mark.parametrize('mode',['legacy','agent-children'])
def test_mixed_rejections_and_partial_uploads_share_two_retries(monkeypatch,mode):
    backend=install(monkeypatch,[partial(),rejection(),partial(),rejection(),partial()])
    delays=[];calls=[]
    async def sleep(delay):delays.append(delay)
    monkeypatch.setattr(asyncio,'sleep',sleep)
    monkeypatch.setattr(g,'_provider_generation_no_retry',lambda p:False)
    async def generate(**kw):calls.append(1);return await post()
    async def run():
        if mode=='legacy':
            with pytest.raises(httpx.ReadError):
                await g.run_provider_generation_with_backoff('fixture',generate)
        else:
            with t._image_upload_retry_scope():
                for index in range(3):
                    try:
                        await asyncio.create_task(g.run_provider_generation_with_backoff('fixture',generate,approved_provider={}))
                    except f.UpstreamImageFailure as exc:
                        assert index<2 and not exc.uncertain
                    except httpx.ReadError:
                        assert index==2
    asyncio.run(run())
    assert len(calls)==3 and backend.calls==5
    assert [d for d in delays if d>=1]==[1,1]

@pytest.mark.parametrize('kind',['connect','unknown-transport'])
def test_finalized_connect_failure_cannot_restart_agent_budget(monkeypatch,kind):
    backend=install(monkeypatch,[httpcore.ConnectError()]*3)
    async def sleep(delay):pass
    monkeypatch.setattr(asyncio,'sleep',sleep)
    async def run():
        if kind=='connect':
            with pytest.raises(httpx.ConnectError) as caught:await post()
        else:
            async def fail(request):raise httpx.ConnectTimeout('connect')
            async with t.ImageTransportClient(transport=httpx.MockTransport(fail),trust_env=False,max_upload_retries=2) as c:
                with pytest.raises(httpx.ConnectTimeout) as caught:await c.post('http://fixture.invalid',content=b'full')
        assert not f.describe_failure(caught.value,'fixture')['replay_safe']
    asyncio.run(run())
    assert backend.calls==(3 if kind=='connect' else 0)

def test_replay_mutation_keeps_connect_error_final(monkeypatch):
    backend=install(monkeypatch,[httpcore.ConnectError()]*3)
    async def run():
        async with t.ImageTransportClient(trust_env=False,max_upload_retries=2) as c:
            request=c.build_request('POST','http://fixture.invalid',content=b'original')
            original=request.stream
            async def sleep(delay):original._stream=b'changed'
            monkeypatch.setattr(asyncio,'sleep',sleep)
            with pytest.raises(httpx.ConnectError) as caught:await c.send(request)
            assert not f.describe_failure(caught.value,'fixture')['replay_safe']
            assert c.telemetry_records[-1]['event']=='retry_refused'
    asyncio.run(run())
    assert backend.calls==1

@pytest.mark.parametrize('protocol',['gemini','openai'])
def test_real_adapter_explicit_429_retries_with_retry_after(monkeypatch,protocol):
    out=io.BytesIO();Image.new('RGB',(7,5),(1,2,3)).save(out,format='PNG')
    payload={'data':[{'b64_json':base64.b64encode(out.getvalue()).decode()}]}
    backend=install(monkeypatch,[rejection(),Stream(reads=[response_bytes(json.dumps(payload).encode())])])
    from tests.test_provider_contract_families import provider
    fixture=provider(protocol,request_mode='openai-json',model='fixture-model')
    fixture.update(base_url='http://fixture.invalid',image_bypass_system_proxy=True)
    monkeypatch.setattr(main,'get_api_provider_exact',lambda p:fixture)
    monkeypatch.setattr(main,'load_api_providers',lambda **kw:[fixture])
    monkeypatch.setattr(main,'api_headers',lambda **kw:{})
    delays=[]
    async def sleep(delay):delays.append(delay)
    monkeypatch.setattr(asyncio,'sleep',sleep)
    async def run():
        image,raw=await g.run_provider_generation_with_backoff(fixture['id'],g.generate_ai_image,'exact','1024x1024','high','fixture-model',None,fixture['id'],preserve_model=True,image_parameters={'aspect_ratio':'1:1','resolution':'1k'})
        assert raw==payload and image['value']==payload['data'][0]['b64_json']
    asyncio.run(run())
    assert backend.calls==2 and delays==[0.05]

def test_actual_batch_inherits_one_upload_budget_across_business_attempts(fx,monkeypatch):
    backend=install(monkeypatch,[partial(),rejection(),partial(),rejection(),partial()])
    actual_sleep=asyncio.sleep
    delays=[]
    async def sleep(delay):
        if delay in (1,3):delays.append(delay);return
        await actual_sleep(delay)
    monkeypatch.setattr(asyncio,'sleep',sleep)
    async def generate(*args,**kw):return await post()
    monkeypatch.setattr(main,'generate_ai_image',generate)
    batch=start_batch(fx,max_retries=2)
    row,=status_until(fx,batch,lambda d:d['status']=='done')['items']
    assert row['attempts']==3 and row['failure']['code']=='TRANSIENT'
    assert backend.calls==5 and delays==[1,1]
    histories=fx.store.canvas(fx.pid)['history']
    assert [h['automatic_retry'] for h in histories]==[True,True,False]

@pytest.mark.parametrize('kind',['read','cancel','accepted429','minimax'])
def test_outer_never_replays_ambiguous_accepted_or_strict_provider(monkeypatch,kind):
    calls=[]
    monkeypatch.setattr(g,'_provider_generation_no_retry',lambda p:kind=='minimax')
    async def generate():
        calls.append(1)
        if kind=='read':raise httpx.ReadTimeout('read')
        if kind=='cancel':raise asyncio.CancelledError()
        r=httpx.Response(429,json={'task_id':'accepted'} if kind=='accepted429' else {'error':'rate limited'},request=httpx.Request('POST','http://fixture.invalid'))
        r.raise_for_status()
    async def run():
        with pytest.raises((httpx.HTTPError,asyncio.CancelledError)):
            await g.run_provider_generation_with_backoff('fixture',generate)
    asyncio.run(run())
    assert calls==[1]

def test_real_gemini_helper_project_bytes_duplicate_order_and_config(tmp_path,monkeypatch):
    import app.services as svc
    from firefly_projects import ProjectStore
    from tests.test_provider_contract_families import provider,PROMPT,SIZE,PARAMETERS,AUTHORIZATION,FINAL_REQUEST_GOLDENS
    from tests.provider_contract import ProviderContractCapture,normalize_captured_request
    store=ProjectStore(tmp_path/'projects',snapshot_delay=0)
    monkeypatch.setattr(main,'FIREFLY_PROJECT_STORE',store)
    pid=store.create_project('synthetic-reference-contract')['id']
    originals=[];refs=[]
    for color in ((1,2,3),(4,5,6)):
        out=io.BytesIO();Image.new('RGB',(7,5),color).save(out,format='PNG');raw=out.getvalue();originals.append(raw)
        record=store.import_media(pid,raw,'owned.png','image/png')
        refs.append({'url':main.project_media_adapter_reference(pid,record['asset_id'])})
    refs.append(refs[0]);originals.append(originals[0])
    capture=ProviderContractCapture({'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'image/png','data':base64.b64encode(originals[0]).decode()}}]}}]})
    fixture=provider('gemini',model='gemini-2.5-flash-image')
    monkeypatch.setattr(main,'get_api_provider_exact',lambda p:fixture)
    monkeypatch.setattr(main,'api_headers',lambda **kw:{'Authorization':AUTHORIZATION})
    monkeypatch.setattr(svc,'gemini_endpoint_url',lambda *args:'https://provider.invalid/v1/models/gemini-2.5-flash-image:generateContent')
    monkeypatch.setattr(main.httpx,'AsyncClient',capture.async_client)
    token=main.PROJECT_MEDIA_ADAPTER_PROJECT.set(pid)
    try:
        asyncio.run(main.generate_ai_image(PROMPT,SIZE,'high','gemini-2.5-flash-image',refs,fixture['id'],preserve_model=True,image_parameters=PARAMETERS))
        actual=normalize_captured_request(capture.only_request())
        expected=deepcopy(FINAL_REQUEST_GOLDENS['protocol:gemini'])
        # Preserve the complete golden; only reference representation now uses verified inline bytes.
        expected['json']['contents'][0]['parts']=[{'text':PROMPT}]+[{'inlineData':{'mimeType':'image/png','data':base64.b64encode(raw).decode()}} for raw in originals]
        assert actual==expected
    finally:
        main.PROJECT_MEDIA_ADAPTER_PROJECT.reset(token);store.close()
