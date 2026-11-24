"""#493 per-item selections against the canonical store and real ASGI router."""
import asyncio
import copy
import json
import time
import httpx
import pytest
from tests.test_result_downloads_493 import isolated_app, fixture, retrieval_result_png, network, Body, PROVIDER, URL, _REAL_CLIENT
from firefly_projects import ProjectStore


async def four(f, saved=0):
    candidates = [{'type': 'url', 'value': URL + '&slot=' + str(i)} for i in range(4)]
    await f.queue.checkpoint(f.project, f.history, candidates, {'task_id': 'original-four'}, PROVIDER)
    q = f.queue.load(f.history['id'])
    for item in q['items'][:saved]:
        await f.queue.archive(f.history['id'], item['id'])
    if saved:
        q = f.queue.load(f.history['id'])
        outputs = [i['output'] for i in q['items'] if i.get('output')]
        f.store.complete_image_generation(f.project, 'result', outputs,
            {**f.history, 'outputs': outputs, 'status': 'running'}, task_id='task-local', require_pending=True, partial=True)
    return f.queue.load(f.history['id'])


def client():
    import main
    return _REAL_CLIENT(transport=httpx.ASGITransport(app=main.app), base_url='http://127.0.0.1:3197', headers={'origin':'http://127.0.0.1:3197'})


@pytest.mark.parametrize('saved', [0,2])
def test_four_selected_items_preserve_saved_and_restart(fixture, saved):
    f=fixture
    from app.services import result_downloads as rd
    network(f, lambda req:httpx.Response(200, stream=Body(retrieval_result_png())))
    async def run():
        q=await four(f,saved)
        original=copy.deepcopy(f.store.canvas(f.project)['history'][0].get('outputs',[]))
        ids=[i['id'] for i in q['items']]
        async with client() as c:
            base=f'/api/project-library/{f.project}/image-result-downloads'
            listing=await c.get(base)
            assert listing.status_code==200
            assert len(listing.json()['groups'][0]['items'])==4
            assert 'PRIVATE-URL' not in listing.text
            response=await c.post(base+'/'+f.history['id']+'/redownload',json={'item_ids':[ids[3]]})
            assert response.status_code==200,response.text
            assert response.json()['downloaded']==saved+1
            assert [i['index'] for i in response.json()['items']]==[0,1,2,3]
            assert len(f.requests)==saved+1
            record=f.store.canvas(f.project)['history'][0]
            assert record['outputs'][:saved]==original
            assert len(record['outputs'])==saved+1
        f.store.flush(f.project)
        f.store.close()
        restarted=ProjectStore(f.store.root,snapshot_delay=0)
        f.monkeypatch.setattr(f.queue.app,'FIREFLY_PROJECT_STORE',restarted)
        service=rd.ResultDownloads(restarted.root,f.queue.app)
        persisted=service.load(f.history['id'],f.project)
        assert persisted['items'][3]['status']=='succeeded'
        assert persisted['items'][3]['downloaded_bytes']==len(retrieval_result_png())
        result=await service.resume(f.project,f.history['id'])
        assert result['status']=='succeeded'
        record=restarted.canvas(f.project)['history'][0]
        assert len(record['outputs'])==4
        assert [o['output_order'] for o in record['outputs']]==[0,1,2,3]
        assert [o['output_order'] for o in restarted.canvas(f.project)['nodes'][0]['images']]==[0,1,2,3]
        assert record['outputs'][:saved]==original
        assert len(f.requests)==4
        restarted.close()
    asyncio.run(run())


def test_three_success_one_unavailable_does_not_abort_loop(fixture):
    f=fixture
    network(f,lambda req:httpx.Response(404) if req.url.params.get('slot')=='1' else httpx.Response(200,stream=Body(retrieval_result_png())))
    async def run():
        await four(f)
        result=await f.queue.resume(f.project,f.history['id'])
        assert result['downloaded']==3
        assert [i['status'] for i in result['items']]==['succeeded','unavailable','succeeded','succeeded']
        assert not result['items'][1]['can_retrieve']
        assert len(f.store.canvas(f.project)['history'][0]['outputs'])==3
        assert len(f.store.canvas(f.project)['nodes'][0]['images'])==3
        assert len(f.requests)==4
    asyncio.run(run())


def test_temporary_recovery_failure_keeps_expired_item_retrievable(fixture):
    f=fixture
    from app.routers import api_project_library as api
    network(f,lambda req:httpx.Response(503) if 'images/tasks/' in req.url.path else httpx.Response(200,stream=Body(retrieval_result_png())))
    async def run():
        q=await four(f)
        q['items'][0].update(expires_at=time.time()-1,expiry_estimated=False)
        f.queue.save(q)
        result=await f.queue.resume(f.project,f.history['id'],refresh=api._recover_project_image_result_link)
        assert result['downloaded']==3
        assert result['items'][0]['status']=='expired'
        assert result['items'][0]['can_retrieve'] is True
        assert len(f.requests)==4
    asyncio.run(run())


def test_overlapping_selections_are_not_dropped_or_downloaded_twice(fixture):
    f=fixture
    network(f,lambda req:httpx.Response(200,stream=Body(retrieval_result_png())))
    async def run():
        q=await four(f)
        ids=[i['id'] for i in q['items']]
        async with client() as c:
            url=f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload'
            results=await asyncio.gather(c.post(url,json={'item_ids':ids[:2]}),c.post(url,json={'item_ids':ids[1:3]}))
            assert all(r.status_code==200 for r in results)
        assert len(f.requests)==3
        assert f.queue.public(f.queue.load(f.history['id']))['downloaded']==3
    asyncio.run(run())


@pytest.mark.parametrize('body',[{'item_ids':[]},{'item_ids':None},{'item_ids':['forged']},{'item_ids':['x','x']},{'item_ids':[1]},{'url':URL},[],{'generation':'x'}])
def test_strict_body_and_forged_items_never_get(fixture,body):
    f=fixture
    network(f,lambda req:pytest.fail('Rejected selection must not GET'))
    async def run():
        await four(f)
        async with client() as c:
            response=await c.post(f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload',json=body)
            assert response.status_code==422,response.text
        assert not f.requests
    asyncio.run(run())


def test_mixed_inline_outputs_keep_original_identity_and_provenance(fixture):
    f=fixture
    network(f,lambda req:httpx.Response(200,stream=Body(retrieval_result_png())))
    async def run():
        candidates=[{'type':'b64','value':'not-persisted'}]*2+[{'type':'url','value':URL+'&slot=2'},{'type':'url','value':URL+'&slot=3'}]
        await f.queue.checkpoint(f.project,f.history,candidates,{'task_id':'mixed-original'},PROVIDER)
        outputs=[]
        for i in range(2):
            media=f.store.import_media(f.project,retrieval_result_png(),f'inline-{i}.png','image/png')
            outputs.append({'asset_id':media['asset_id'],'kind':'image','output_id':'output-'+str(i+1)*32,'output_order':i})
        f.store.complete_image_generation(f.project,'result',outputs,{**f.history,'outputs':outputs,'status':'running','source_node_id':'source','op_id':'retained-op'},task_id='task-local',require_pending=True,partial=True)
        before=copy.deepcopy(f.store.canvas(f.project)['history'][0]['outputs'])
        result=await f.queue.resume(f.project,f.history['id'])
        assert result['status']=='succeeded'
        record=f.store.canvas(f.project)['history'][0]
        assert record['outputs'][:2]==before
        assert record['source_node_id']=='source' and record['op_id']=='retained-op'
        assert len(record['outputs'])==4 and len(f.requests)==2
        assert [o['output_order'] for o in record['outputs']]==[0,1,2,3]
    asyncio.run(run())


def test_foreign_project_generation_and_same_origin_fail_closed(fixture):
    f=fixture
    network(f,lambda req:pytest.fail('Invalid identity must not GET'))
    async def run():
        q=await four(f)
        other=f.store.create_project('other')['id']
        async with client() as c:
            url=f'/api/project-library/{other}/image-result-downloads/{f.history["id"]}/redownload'
            assert (await c.post(url)).status_code==404
            url=f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload'
            assert (await c.post(url,headers={'origin':'https://evil.invalid'})).status_code==403
            assert (await c.get(f'/api/project-library/{other}/image-result-downloads')).json()=={'groups':[]}
        q['generation']='forged';f.queue.save(q)
        assert (await f.queue.resume(f.project,f.history['id']))['status']=='unavailable'
        assert not f.requests
    asyncio.run(run())
