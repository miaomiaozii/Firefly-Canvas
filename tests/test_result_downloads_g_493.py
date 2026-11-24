"""#493 approved deleted-node contract; canonical store, ASGI, original Agent journal."""
import asyncio
import copy
from types import SimpleNamespace
import httpx
import pytest
import main
from app.routers import api_agent_tools as agent
from app.services import agent_ops_store
from firefly_opsdb import OpsDb
from firefly_projects import ProjectStore
from tests.test_result_downloads_493 import fixture, network, png, Body, PROVIDER, URL
from tests.test_result_downloads_d2_493 import four, client


def remove_result(f):
    canvas=f.store.canvas(f.project)
    f.store.apply_mutations(f.project,[{'op':'remove_node','node_id':'result'}],base_revision=canvas['revision'],force_empty=True)


def test_deleted_node_history_only_reload_preserves_saved_assets(fixture):
    f=fixture
    network(f,lambda req:httpx.Response(200,stream=Body(png())))
    async def run():
        q=await four(f,2)
        saved=copy.deepcopy(f.store.canvas(f.project)['history'][0]['outputs'])
        remove_result(f)
        async with client() as c:
            base=f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}'
            state=await c.get(base)
            assert state.status_code==200,state.text
            assert state.json()['node_exists'] is False
            r=await c.post(base+'/redownload')
            assert r.status_code==200 and r.json()['status']=='succeeded',r.text
        f.store.flush(f.project);f.store.close()
        restarted=ProjectStore(f.store.root,snapshot_delay=0)
        try:
            canvas=restarted.canvas(f.project)
            assert not canvas['nodes']
            record=canvas['history'][0]
            assert record['outputs'][:2]==saved
            assert len(record['outputs'])==4
            for item in record['outputs']:
                assert restarted.media_record(f.project,item['asset_id'])['path']
            assert len(f.requests)==4
        finally: restarted.close()
    asyncio.run(run())


@pytest.mark.parametrize('forged',['history-deleted','generation','slot','output-id','cross-project','cancelled'])
def test_invalid_owner_before_get_or_media_writes(fixture,forged):
    f=fixture
    network(f,lambda req:pytest.fail('Invalid owner reached network'))
    async def run():
        q=await four(f)
        if forged in {'history-deleted','cancelled'}:
            records=f.store._histories[f.project]
            if forged=='history-deleted': records.clear()
            else: records[0]['status']='cancelled'
            f.store._persist_history(f.project)
        elif forged=='generation': q['generation']='forged';f.queue.save(q)
        elif forged=='slot': q['items'][0]['index']=42;f.queue.save(q)
        elif forged=='output-id': q['items'][0]['output_id']='output-'+'f'*32;f.queue.save(q)
        elif forged=='cross-project':
            from firefly_projects import _write_json_atomic as write_json_atomic
            path=f.store.image_result_download_queue_path(f.project,f.history['id'])
            q['project_id']=f.store.create_project('foreign')['id']
            write_json_atomic(path,q,keep_backup=False)
        async with client() as c:
            r=await c.post(f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload')
            assert r.status_code==404,r.text
        assert not f.requests
        assert not f.store.list_media(f.project)
    asyncio.run(run())


def admit_agent(f):
    canvas=f.store.canvas(f.project)
    result=copy.deepcopy(canvas['nodes'][0]);result.pop('pendingTasks',None);result['pending']=0
    f.store.apply_mutations(f.project,[{'op':'upsert_node','node':result},
        {'op':'upsert_node','node':{'id':'agent','type':'smart-agent'}},
        {'op':'upsert_node','node':{'id':'source','type':'smart-image','images':[]}}],base_revision=canvas['revision'])
    task='agent_img_493_g_original'
    context=SimpleNamespace(op_id='g-original-op',turn_id='g-original-turn',agent_node_id='agent',tool='generate.trigger',args={'node_id':'source'})
    guarded=set()
    def begin():
        return f.store.begin_image_generation(f.project,'result',{'taskId':task,'count':4,
            'source_node_id':'source','op_id':context.op_id,'providerId':PROVIDER['id']},base_revision=f.store.canvas(f.project)['revision'])
    agent._generation_mutation(f.store,f.project,context,task,guarded,begin)
    agent._generation_release(f.store,f.project,task,guarded)
    f.history.update(task_id=task,source_node_id='source',op_id=context.op_id,
                     retry_index=2,retry_root_id='original-paid-attempt')
    return task,context


@pytest.mark.parametrize('forged',['queue-provenance','history-provenance','unknown-operation','missing-operation'])
def test_agent_provenance_fail_closed_before_network(fixture,forged):
    f=fixture
    network(f,lambda req:pytest.fail('Forged Agent provenance reached network'))
    async def run():
        task,context=admit_agent(f)
        await four(f)
        q=f.queue.load(f.history['id'])
        if forged=='queue-provenance': q['history']['source_node_id']='forged';f.queue.save(q)
        else:
            value={'history-provenance':'forged','unknown-operation':'unknown','missing-operation':None}[forged]
            key='source_node_id' if forged=='history-provenance' else 'op_id'
            f.store._histories[f.project][0][key]=value
            q['history'][key]=value;f.queue.save(q);f.store._persist_history(f.project)
        async with client() as c:
            r=await c.post(f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload')
            assert r.status_code==404,r.text
        assert not f.requests and not f.store.list_media(f.project)
    asyncio.run(run())


def test_original_agent_writer_typed_result_and_real_undo(fixture):
    f=fixture
    network(f,lambda req:httpx.Response(200,stream=Body(png())))
    async def run():
        task,context=admit_agent(f)
        await four(f)
        await f.queue.failed(f.history['id'],RuntimeError('synthetic writeback failure'))
        other=f.store.import_media(f.project,png(True),'unrelated.png','image/png')
        canvas=f.store.canvas(f.project)
        f.store.apply_mutations(f.project,[{'op':'upsert_node','node':{'id':'source','type':'smart-image',
            'images':[{'asset_id':other['asset_id'],'output_id':'output-'+'a'*32}]}},
            {'op':'upsert_node','node':{'id':'source-group','type':'smart-group','children':['source','result'],'title':'Keep source group'}}],base_revision=canvas['revision'])
        original_group=copy.deepcopy(next(n for n in f.store.canvas(f.project)['nodes'] if n['id']=='source-group'))
        result=await agent.get_task_status(f.project,task)
        assert result['status']=='download_failed'
        assert result['failure']['code']=='RESULT_DOWNLOAD_FAILED'
        trusted=result['retrieval']
        assert trusted['type']=='image_result_retrieval'
        assert trusted['op_id']==context.op_id and trusted['turn_id']==context.turn_id
        assert trusted['generates'] is False and trusted['additional_cost'] is False
        assert 'PRIVATE-URL' not in str(trusted)
        assert (await agent.wait_for_task_core(f.project,task,1))['status']=='download_failed'
        before_source=copy.deepcopy(next(n for n in f.store.canvas(f.project)['nodes'] if n['id']=='source'))
        async with client() as c:
            r=await c.post(f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload')
            assert r.status_code==200 and r.json()['status']=='succeeded',r.text
            operations=agent_ops_store.list_operations(OpsDb(f.store.root),f.project)
            assert operations and {o['turn_id'] for o in operations}=={context.turn_id}
            assert all(o['op_id']==context.op_id or o.get('parent_op_id')==context.op_id for o in operations)
            undo=await c.post(f'/api/projects/{f.project}/ops/undo',json={'op_id':context.op_id},headers={'X-Agent-Project':f.project})
            assert undo.status_code==200,undo.text
            assert undo.json()['status']=='reverted',undo.text
        canvas=f.store.canvas(f.project)
        assert next(n for n in canvas['nodes'] if n['id']=='source')==before_source
        assert next(n for n in canvas['nodes'] if n['id']=='source-group')==original_group
        assert f.store.media_record(f.project,other['asset_id'])['path']
        assert not next(n for n in canvas['nodes'] if n['id']=='result').get('images')
        assert len(f.requests)==4
        history=canvas['history'][0]
        assert history['retry_index']==2 and history['retry_root_id']=='original-paid-attempt'
        durable={'id':task,'op_id':context.op_id,'source_node_id':'source','result_node_id':'result'}
        assert agent.recovered_generation_writer(f.project,durable) is None
    asyncio.run(run())


def test_original_agent_deleted_result_recovers_history_and_undo_fails_safely(fixture):
    f=fixture
    network(f,lambda req:httpx.Response(200,stream=Body(png())))
    async def run():
        task,context=admit_agent(f)
        await four(f)
        await f.queue.failed(f.history['id'],RuntimeError('synthetic failure'))
        remove_result(f)
        before=copy.deepcopy(f.store.canvas(f.project)['nodes'])
        async with client() as c:
            r=await c.post(f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload')
            assert r.status_code==200 and r.json()['status']=='succeeded',r.text
            assert f.store.canvas(f.project)['nodes']==before
            undo=await c.post(f'/api/projects/{f.project}/ops/undo',json={'op_id':context.op_id},headers={'X-Agent-Project':f.project})
            assert undo.status_code==409,undo.text
            assert undo.json()['detail']['code']=='UNDO_CONFLICT',undo.text
        assert f.store.canvas(f.project)['nodes']==before
        assert len(f.store.canvas(f.project)['history'][0]['outputs'])==4
        assert len(f.requests)==4
    asyncio.run(run())


def test_history_only_concurrent_single_and_batch_selection(fixture):
    f=fixture
    network(f,lambda req:httpx.Response(200,stream=Body(png())))
    async def run():
        q=await four(f);remove_result(f)
        ids=[i['id'] for i in q['items']]
        async with client() as c:
            url=f'/api/project-library/{f.project}/image-result-downloads/{f.history["id"]}/redownload'
            results=await asyncio.gather(c.post(url,json={'item_ids':[ids[0]]}),c.post(url,json={'item_ids':ids[:3]}))
            assert all(r.status_code==200 for r in results)
        canvas=f.store.canvas(f.project)
        assert not canvas['nodes'] and len(canvas['history'][0]['outputs'])==3
        assert len(f.requests)==3
    asyncio.run(run())
