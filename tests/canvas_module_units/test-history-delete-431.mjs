// #431: exercise real deletion handlers, server receiver and history card renderer.
import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {ROOT} from './harness.mjs';
import {editor, declarations} from './smart-human-handlers-harness-392.mjs';
import {evaluate, fromContext} from './harness.mjs';

const copy = value => structuredClone(value);
const image = (id, output) => ({asset_id:id, output_id:output, kind:'image', url:`/media/${id}.png`});
const a=image('asset-a','output-a'), b=image('asset-b','output-b'), c=image('asset-c','output-c');
const record=(id, outputs) => ({id, status:'succeeded', result_node_id:id+'-node', outputs, prompt:id});
function fixture(){
    const e=editor([{id:'one-node',type:'smart-image',images:[a]}, {id:'multi-node',type:'smart-image',images:[a,b]}, {id:'other-node',type:'smart-image',images:[c]}], harness => {
        harness.element('smartHistoryModal').classList.add('open');
        Object.assign(harness.element('smartHistoryList'), {contains:()=>false, scrollLeft:0, scrollWidth:1000, clientWidth:1000});
    });
    Object.assign(e.ctx,{smartNodeRunTokens:new Map(),smartRunTokens:new Map(),smartGenerationNodeRevisions:new Map(),smartHistoryModal:null,smartHistoryDeletingBusy:false,activeAssetLibraryId:'local',PROJECT_ASSET_LIBRARY_ID:'project'});
    evaluate(e.ctx,declarations('canvas-agent-tui.js'));
    let server=fromContext(e.ctx.canvasForStorage());
    server.revision=1; server.history=[record('one',[a]),record('multi',[a,b]),record('other',[c])];
    e.ctx.applyMergedServerCanvas(server);
    e.ctx.ffDialog={ask:async()=>true};
    e.ctx.ffToast={info(){},error(message){e.harness.toasts.push(String(message));}};
    let fail=false;
    const before=copy(server);
    e.ctx.fetch=async(url, options={})=>{
        const response=(data,ok=true)=>({ok,status:ok?200:500,json:async()=>copy(data)});
        if(url.endsWith('/history/delete')){
            if(fail) return response({detail:'delete failed visibly'},false);
            const {history_id}=JSON.parse(options.body);
            server.history=server.history.filter(r=>r.id!==history_id); server.revision++;
            return response({canvas:server});
        }
        if(url.endsWith('/original/delete')){
            if(fail) return response({detail:'delete failed visibly'},false);
            const {history_id,output_id}=JSON.parse(options.body);
            const r=server.history.find(item=>item.id===history_id);
            r.outputs=r.outputs.filter(item=>item.output_id!==output_id);
            r.deletedOutputTombstones=[{deleted_asset_id:'asset-a',deleted_output_id:output_id,deletion_id:'deletion-a'}];
            if(!r.outputs.length){r.status='original_deleted';r.originalDeleted=true;}
            server.nodes.find(n=>n.id===r.result_node_id).images=[];
            server.revision++;return response({canvas:server,deleted:{deletion_id:'deletion-a'}});
        }
        if(url.endsWith('/original/delete-project-wide')){
            if(fail) return response({detail:'delete failed visibly'},false);
            for(const r of server.history){
                if(!r.outputs.some(o=>o.asset_id==='asset-a')) continue;
                r.outputs=r.outputs.filter(o=>o.asset_id!=='asset-a');
                r.deletedOutputTombstones=[{deleted_asset_id:'asset-a'}];
                if(!r.outputs.length){r.status='original_deleted';r.originalDeleted=true;}
            }
            for(const n of server.nodes) n.images=n.images.filter(o=>o.asset_id!=='asset-a');
            server.revision++; return response({canvas:server,deleted:{asset_id:'asset-a'}});
        }
        if(url.endsWith('/original/restore-project-wide')){
            server=copy(before);server.revision=3;return response({canvas:server});
        }
        if(url.endsWith('/original/restore')){
            if(!JSON.parse(options.body).deletion_id) return response({detail:'missing deletion identity'},false);
            server=copy(before);server.revision=3;return response({canvas:server});
        }
        return response({saved:true,items:[],revision:server.revision});
    };
    e.ctx.renderSmartCanvasHistory();
    const logs=()=>fromContext(evaluate(e.ctx,'canvas.history'));
    const html=()=>e.harness.element('smartHistoryList').innerHTML;
    const card=id=>e.ctx.smartHistoryLogCardHtml(evaluate(e.ctx,`canvas.history.find(r=>r.id==='${id}')`),0);
    return {...e,logs,card,html, fail:()=>{fail=true;}, server:()=>copy(server)};
}

test('real store responses refresh a mounted list across broadcast-before-response and undo',async()=>{
    const run=spawnSync('python',['tests/history_delete_431_fixture.py'],{cwd:ROOT,encoding:'utf8'});
    assert.equal(run.status,0,run.stderr);
    const data=JSON.parse(run.stdout);
    const e=editor([], harness=>{
        harness.element('smartHistoryModal').classList.add('open');
        Object.assign(harness.element('smartHistoryList'),{contains:()=>false,scrollLeft:0,scrollWidth:1000,clientWidth:1000});
    });
    Object.assign(e.ctx,{smartNodeRunTokens:new Map(),smartRunTokens:new Map(),smartGenerationNodeRevisions:new Map(),smartHistoryDeletingBusy:false,smartTitleText:null,ffDialog:{ask:async()=>true},resumeSmartPendingTasks(){}});
    evaluate(e.ctx,declarations('canvas-agent-tui.js'));
    e.ctx.applyMergedServerCanvas(data.before);
    e.ctx.renderSmartCanvasHistory();
    const active=e.node('other-node');
    active.pendingTasks=[{taskId:'task-inflight',kind:'image'}];
    e.ctx.ensureSmartPendingHistoryLogs(active,active.pendingTasks);
    const html=()=>e.harness.element('smartHistoryList').innerHTML;
    const activeCount=()=>fromContext(evaluate(e.ctx,'canvas.history')).filter(r=>r.request?.task_id==='task-inflight').length;
    assert.match(html(),/data-canvas-history-id="one"/);
    let stage=0;
    e.ctx.fetch=async(url)=>{
        if(url.endsWith('/canvas')) return {ok:true,status:200,json:async()=>({saved:true,revision:data.occurrence.canvas.revision})};
        const body=[data.occurrence,data.restored,data.wide,data.wide_restored,data.history_deleted][stage++];
        assert.match(url, [/original\/delete$/, /original\/restore$/, /original\/delete-project-wide$/, /original\/restore-project-wide$/, /history\/delete$/][stage-1]);
        e.ctx.applyMergedServerCanvas(body.canvas);
        e.harness.flushRafs();
        return {ok:true,status:200,json:async()=>copy(body)};
    };
    await e.ctx.deleteSmartProjectOriginal('one',0);
    assert.equal(stage,1,JSON.stringify(e.harness.toasts));
    assert.equal(evaluate(e.ctx,"canvas.history.find(r=>r.id==='one').status"),'original_deleted',JSON.stringify(e.harness.toasts));
    assert.ok(html().includes('log-thumb-deleted'),`mounted list should show deleted original: ${JSON.stringify(e.harness.toasts)}`);
    assert.equal(activeCount(),1);
    assert.match(html(),/data-canvas-history-id="multi"/);
    await e.ctx.performUndo();
    assert.equal(stage,2,JSON.stringify(e.harness.toasts));
    assert.ok(!html().includes('log-thumb-deleted'),JSON.stringify(e.harness.toasts));
    assert.equal(activeCount(),1);
    assert.match(html(),new RegExp(data.asset));
    await e.ctx.deleteProjectOriginalWide(data.asset);
    assert.equal(stage,3,JSON.stringify(e.harness.toasts));
    assert.ok(html().includes('log-thumb-deleted'));
    await e.ctx.performUndo();
    assert.equal(stage,4,JSON.stringify(e.harness.toasts));
    assert.ok(!html().includes('log-thumb-deleted'),JSON.stringify(e.harness.toasts));
    await e.ctx.deleteCanvasLogEntry('one');
    assert.equal(stage,5,JSON.stringify(e.harness.toasts));
    assert.ok(!html().includes('data-canvas-history-id="one"'),JSON.stringify(e.harness.toasts));
    assert.ok(html().includes('data-canvas-history-id="multi"'));
});

test('pending journal and task identity survive delete response after broadcast without duplicates',async()=>{
    const e=fixture();
    e.ctx.resumeSmartPendingTasks=()=>{};
    const node=e.node('other-node');
    node.x=321;
    node.pendingTasks=[{taskId:'task-live',kind:'image'},{taskId:'task-done',kind:'image'}];
    e.ctx.ensureSmartPendingHistoryLogs(node,node.pendingTasks);
    e.ctx.observeSmartCanvasPending();
    assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),true);
    const server=e.server();
    server.revision++;
    server.updated_at=Number(server.updated_at||0)+100;
    server.history=server.history.filter(r=>r.id!=='one');
    server.history.push({id:'finished-server',status:'succeeded',request:{task_id:'task-done'},outputs:[]});
    e.ctx.fetch=async(url)=>{
        assert.match(url,/history\/delete$/);
        const current=e.node('other-node');
        current.pendingTasks.push({taskId:'task-new',kind:'image'});
        e.ctx.ensureSmartPendingHistoryLogs(current,current.pendingTasks);
        e.ctx.applyMergedServerCanvas(server);
        e.harness.flushRafs();
        return {ok:true,status:200,json:async()=>({ok:true,canvas:copy(server)})};
    };
    await e.ctx.deleteCanvasLogEntry('one');
    const logs=e.logs();
    assert.equal(logs.filter(r=>r.request?.task_id==='task-live').length,1);
    assert.equal(logs.filter(r=>r.request?.task_id==='task-new').length,1);
    assert.deepEqual(logs.filter(r=>r.request?.task_id==='task-done').map(r=>r.status),['succeeded']);
    assert.equal(e.node('other-node').x,321);
    assert.equal(e.node('other-node').pendingTasks.some(t=>t.taskId==='task-live'),true);
    assert.equal(e.node('other-node').pendingTasks.some(t=>t.taskId==='task-new'),true);
    assert.equal(e.node('other-node').pendingTasks.some(t=>t.taskId==='task-done'),false);
    assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),true);
    assert.ok(!e.html().includes('data-canvas-history-id="one"'),JSON.stringify(e.harness.toasts));
    assert.ok(e.html().includes('data-canvas-history-id="finished-server"'),JSON.stringify(e.harness.toasts));
});

test('project-wide delete settles a pending copied asset before server deletion',async()=>{
    const e=fixture();
    let server=e.server();
    const calls=[];
    e.node('other-node').images.push(copy(a));
    e.ctx.observeSmartCanvasPending();
    assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),true);
    e.ctx.fetch=async(url,options={})=>{
        const response=data=>({ok:true,status:200,json:async()=>copy(data)});
        if(url.endsWith('/canvas')){
            calls.push('save');
            const body=JSON.parse(options.body);
            assert.equal(body.base_revision,server.revision);
            for(const mutation of body.mutations){
                if(mutation.op==='upsert_node'){
                    const index=server.nodes.findIndex(n=>n.id===mutation.node.id);
                    if(index<0) server.nodes.push(copy(mutation.node)); else server.nodes[index]=copy(mutation.node);
                }
            }
            server.revision++;
            return response({saved:true,revision:server.revision});
        }
        if(url.endsWith('/flush')){calls.push('flush');return response({saved:true,revision:server.revision});}
        assert.match(url,/original\/delete-project-wide$/);
        calls.push('delete');
        assert.equal(JSON.parse(options.body).base_revision,server.revision);
        for(const n of server.nodes) n.images=n.images.filter(o=>o.asset_id!=='asset-a');
        for(const r of server.history) r.outputs=r.outputs.filter(o=>o.asset_id!=='asset-a');
        server.revision++;
        return response({canvas:server,deleted:{asset_id:'asset-a'}});
    };
    await e.ctx.deleteProjectOriginalWide('asset-a');
    assert.equal(e.node('other-node').images.some(item=>item.asset_id==='asset-a'),false,'local copy must not survive the server deletion');
    assert.deepEqual(calls,['save','delete'],JSON.stringify(e.harness.toasts));
    assert.equal(server.nodes.find(n=>n.id==='other-node').images.some(item=>item.asset_id==='asset-a'),false);
    assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),false);
});

test('pending copied asset is saved and removed using real project-store delete snapshots',async()=>{
    const run=spawnSync('python',['tests/history_delete_pending_431_fixture.py'],{cwd:ROOT,encoding:'utf8'});
    assert.equal(run.status,0,run.stderr);
    const data=JSON.parse(run.stdout);
    const e=editor([],harness=>{
        harness.element('smartHistoryModal').classList.add('open');
        Object.assign(harness.element('smartHistoryList'),{contains:()=>false,scrollLeft:0,scrollWidth:1000,clientWidth:1000});
    });
    Object.assign(e.ctx,{smartNodeRunTokens:new Map(),smartRunTokens:new Map(),smartGenerationNodeRevisions:new Map(),smartHistoryDeletingBusy:false,smartTitleText:null,activeAssetLibraryId:'local',PROJECT_ASSET_LIBRARY_ID:'project',resumeSmartPendingTasks(){}});
    evaluate(e.ctx,declarations('canvas-agent-tui.js'));
    e.ctx.applyMergedServerCanvas(data.before);
    e.node('other-node').images.push(copy(data.copied));
    e.ctx.observeSmartCanvasPending();
    assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),true);
    const calls=[];
    e.ctx.fetch=async(url,options={})=>{
        if(url.endsWith('/canvas')){
            calls.push('save');
            const body=JSON.parse(options.body);
            assert.equal(body.base_revision,data.before.revision);
            const savedNode=body.mutations.find(m=>m.op==='upsert_node'&&m.node.id==='other-node').node;
            assert.deepEqual(savedNode.images.map(item=>item.asset_id),data.after_save.nodes.find(n=>n.id==='other-node').images.map(item=>item.asset_id));
            return {ok:true,status:200,json:async()=>({saved:true,revision:data.after_save.revision})};
        }
        assert.match(url,/original\/delete-project-wide$/);
        calls.push('delete');
        assert.equal(JSON.parse(options.body).base_revision,data.after_save.revision);
        return {ok:true,status:200,json:async()=>copy(data.deleted)};
    };
    await e.ctx.deleteProjectOriginalWide(data.asset);
    assert.equal(e.node('other-node').images.some(item=>item.asset_id===data.asset),false,JSON.stringify(e.harness.toasts));
    assert.deepEqual(calls,['save','delete']);
    assert.equal(data.deleted.canvas.nodes.find(n=>n.id==='other-node').images.some(item=>item.asset_id===data.asset),false);
    assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),false);
});

test('project-wide delete aborts on settle failure or project switch',async()=>{
    for(const mode of ['failed','switched']){
        const e=fixture();
        const button=e.harness.makeElement();
        e.node('other-node').images.push(copy(a));
        e.ctx.observeSmartCanvasPending();
        let requests=0;
        e.ctx.fetch=async()=>{requests++;throw Error('unexpected delete');};
        e.ctx.settleProjectCanvasBeforeOriginalMutation=async()=>{
            if(mode==='switched') evaluate(e.ctx,"canvas.project_id='another-project'");
            return mode!=='failed';
        };
        await e.ctx.deleteProjectOriginalWide('asset-a',button);
        assert.equal(requests,0,mode);
        assert.equal(button.disabled,false,mode);
        assert.ok(e.harness.toasts.includes(mode==='failed'?'smart.projectSaveFailed':'smart.canvasConflict'),JSON.stringify(e.harness.toasts));
        assert.equal(e.node('other-node').images.some(item=>item.asset_id==='asset-a'),true);
    }
});

test('project-wide delete retains pending copy when real save fails',async()=>{
    const e=fixture();
    e.node('other-node').images.push(copy(a));
    e.ctx.observeSmartCanvasPending();
    const requests=[];
    e.ctx.fetch=async url=>{
        requests.push(url);
        return {ok:false,status:500,json:async()=>({detail:'save failed'})};
    };
    await e.ctx.deleteProjectOriginalWide('asset-a');
    assert.ok(requests.length>0);
    assert.ok(requests.every(url=>url.endsWith('/canvas')),JSON.stringify(requests));
    assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),true);
    assert.equal(e.node('other-node').images.some(item=>item.asset_id==='asset-a'),true);
    assert.ok(e.harness.toasts.includes('smart.projectSaveFailed'),JSON.stringify(e.harness.toasts));
});

test('project-wide delete ignores success and conflict responses after switching projects',async()=>{
    for(const status of [200,409]){
        const e=fixture();
        e.ctx.settleProjectCanvasBeforeOriginalMutation=async()=>true;
        e.ctx.fetch=async()=>{
            evaluate(e.ctx,"canvas.project_id='another-project'");
            const server=e.server();server.revision++;
            server.nodes.find(n=>n.id==='one-node').images=[];
            return {ok:status===200,status,json:async()=>status===200?{canvas:server}:{detail:{canvas:server}}};
        };
        await e.ctx.deleteProjectOriginalWide('asset-a');
        assert.equal(e.node('one-node').images[0].asset_id,'asset-a',String(status));
        assert.equal(evaluate(e.ctx,'undoStack.length'),0,String(status));
        assert.ok(e.harness.toasts.includes('smart.canvasConflict'),JSON.stringify(e.harness.toasts));
    }
});

test('history delete removes its rendered card immediately and after refresh, preserving unrelated records',async()=>{
    const e=fixture();
    assert.match(e.html(), /data-canvas-history-id="one"/);
    await e.ctx.deleteCanvasLogEntry('one');
    assert.doesNotMatch(e.html(), /data-canvas-history-id="one"/);
    assert.match(e.html(), /data-canvas-history-id="multi"/);
    assert.deepEqual(e.logs().map(r=>r.id).sort(),['multi','other']);
    e.ctx.applyMergedServerCanvas(e.server());
    assert.deepEqual(e.logs().map(r=>r.id).sort(),['multi','other']);
    assert.match(e.card('multi'),/asset-b/);
});

test('project-wide deletion renders recoverable deleted state, preserves other outputs and restores it',async()=>{
    const e=fixture();
    await e.ctx.deleteProjectOriginalWide('asset-a');
    assert.match(e.html(), /log-thumb-deleted/);
    assert.equal(e.logs().find(r=>r.id==='one').status,'original_deleted');
    assert.match(e.card('one'),/log-thumb-deleted/);
    assert.match(e.card('one'),/data-history-restore-original="asset-a"/);
    assert.match(e.card('multi'),/asset-b/);
    assert.match(e.card('other'),/asset-c/);
    e.ctx.applyMergedServerCanvas(e.server());
    assert.match(e.card('one'),/log-thumb-deleted/);
    await e.ctx.performUndo();
    assert.doesNotMatch(e.html(), /log-thumb-deleted/);
    assert.match(e.html(), /asset-a/);
    assert.equal(e.logs().find(r=>r.id==='one').outputs[0].asset_id,'asset-a');
    assert.match(e.card('one'),/asset-a/);
});

test('history original deletion renders a tombstone, then undo restores the original',async()=>{
    const e=fixture();
    await e.ctx.deleteSmartProjectOriginal('one',0);
    assert.equal(e.logs().find(r=>r.id==='one').status,'original_deleted');
    assert.match(e.card('one'),/log-thumb-deleted/);
    assert.match(e.card('one'),/data-deletion-id="deletion-a"/);
    assert.match(e.card('multi'),/asset-b/);
    e.ctx.applyMergedServerCanvas(e.server());
    assert.match(e.card('one'),/log-thumb-deleted/);
    await e.ctx.performUndo();
    assert.equal(e.logs().find(r=>r.id==='one').outputs[0].asset_id,'asset-a');
});

test('manual project-wide restore after refresh uses the project-wide endpoint',async()=>{
    const e=fixture();
    await e.ctx.deleteProjectOriginalWide('asset-a');
    e.ctx.applyMergedServerCanvas(e.server());
    await e.ctx.restoreSmartProjectOriginal('asset-a','','','one');
    assert.equal(e.logs().find(r=>r.id==='one').outputs[0].asset_id,'asset-a');
});

test('project-wide trash restore restores history instead of creating an orphan node',async()=>{
    const e=fixture();
    await e.ctx.deleteProjectOriginalWide('asset-a');
    Object.assign(e.ctx,{smartTrashRestoring:false,smartTrashItems:[{asset_id:'asset-a',reason:'delete-generated-original'}],openSmartTrashPanel:async()=>{}});
    await e.ctx.smartTrashRestoreItem('asset-a','');
    assert.equal(e.logs().find(r=>r.id==='one').outputs[0].asset_id,'asset-a');
    assert.equal(e.node('one-node').images.length,1);
    assert.equal(evaluate(e.ctx,'undoStack.length'),0);
});

test('failed deletions retain cards and show a visible error',async()=>{
    for(const operation of ['history','wide','original']){
        const e=fixture();e.fail();
        const before=e.html();
        if(operation==='history') await e.ctx.deleteCanvasLogEntry('one');
        else if(operation==='wide') await e.ctx.deleteProjectOriginalWide('asset-a');
        else await e.ctx.deleteSmartProjectOriginal('one',0);
        assert.equal(e.logs().find(r=>r.id==='one').outputs[0].asset_id,'asset-a');
        assert.equal(e.html(),before);
        assert.ok(e.harness.toasts.includes('delete failed visibly'));
    }
});
