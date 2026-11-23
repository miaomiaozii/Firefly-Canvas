// #489: actual command handlers against private Store generation/CAS/deletion transactions, not HTTP/browser evidence.
import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {mkdtempSync} from 'node:fs';
import path from 'node:path';
import {tmpdir} from 'node:os';
import {createRequire} from 'node:module';
import {ROOT, evaluate} from './harness.mjs';
import {editor, declarations} from './smart-human-handlers-harness-392.mjs';

const {originalDeletionReadSet}=createRequire(import.meta.url)('../../static/js/project-canvas-mutations.js');

function fixture(mode, injection, count=1, duringConflict=null){
    const root=mkdtempSync(path.join(tmpdir(), 'original-489-'));
    const store=(action, data={})=>{
        const run=spawnSync('python', ['tests/original_delete_rebase_489_fixture.py',root,action],
            {cwd:ROOT,encoding:'utf8',input:JSON.stringify(data)});
        assert.equal(run.status,0,run.stderr);
        return JSON.parse(run.stdout);
    };
    const seed=store('seed');
    const e=editor([], harness=>{
        harness.element('smartHistoryModal').classList.add('open');
        Object.assign(harness.element('smartHistoryList'),{contains:()=>false,scrollLeft:0,scrollWidth:1000,clientWidth:1000});
    });
    Object.assign(e.ctx,{smartNodeRunTokens:new Map(),smartRunTokens:new Map(),smartGenerationNodeRevisions:new Map(),
        smartHistoryDeletingBusy:false,smartTitleText:null,activeAssetLibraryId:'local',PROJECT_ASSET_LIBRARY_ID:'project',
        resumeSmartPendingTasks(){},ffToast:{info(){},error(message){e.harness.toasts.push(String(message));}}});
    const controlledTimeout=e.ctx.setTimeout;
    e.ctx.setTimeout=(fn,ms,...args)=>ms===100?setTimeout(fn,ms,...args):controlledTimeout(fn,ms,...args);
    evaluate(e.ctx,declarations('canvas-agent-tui.js'));
    e.ctx.applyMergedServerCanvas(seed.canvas);
    e.ctx.renderSmartCanvasHistory();
    const calls=[];
    e.ctx.fetch=async(url, options={})=>{
        assert.match(url,new RegExp(`/original/delete${mode==='wide'?'-project-wide':''}$`), 'no full canvas save or unexpected endpoint');
        const payload=JSON.parse(options.body);
        const response=store('delete',{mode,payload,injection:calls.length<count?injection:null});
        calls.push({payload,...response});
        if(response.status===409 && duringConflict) duringConflict(e,response,store);
        return {ok:response.status===200,status:response.status,json:async()=>structuredClone(response.data)};
    };
    const invoke=()=>mode==='wide'?e.ctx.deleteProjectOriginalWide(seed.asset_id):e.ctx.deleteSmartProjectOriginal('history-1',0);
    return {...e,seed,calls,store,invoke};
}
const node=(canvas,id)=>canvas.nodes.find(n=>n.id===id);
const record=(canvas,id)=>canvas.history.find(r=>r.id===id);

for(const mode of ['single','wide']){
    for(const count of [1,2]) test(`${mode}: unrelated real generation writes (${count}) rebase without deleting generated data`,async()=>{
        const e=fixture(mode,'generation',count);
        await e.invoke();
        assert.equal(e.calls.length,count+1,JSON.stringify(e.harness.toasts));
        assert.deepEqual(e.calls.map(c=>c.status),[...Array(count).fill(409),200]);
        for(const call of e.calls){
            assert.deepEqual(node(call.before_delete,'target'),node(e.seed.canvas,'target'));
            assert.deepEqual(record(call.before_delete,'history-1'),record(e.seed.canvas,'history-1'));
        }
        for(let i=1;i<e.calls.length;i++) assert.equal(e.calls[i].payload.base_revision,e.calls[i-1].after_delete.revision);
        const state=e.store('inspect');
        const before=e.calls.at(-1).before_delete;
        assert.deepEqual(node(state.canvas,'background'),node(before,'background'));
        for(let i=2;i<count+2;i++){
            const history=record(state.canvas,`history-${i}`);
            assert.deepEqual(history,record(before,`history-${i}`));
            const id=history.outputs[0].asset_id;
            assert.ok(state.media.some(item=>item.asset_id===id));
            assert.ok(Object.keys(state.files).some(file=>file.startsWith(`media/${id}.`)));
            assert.equal(Object.entries(state.files).find(([file])=>file.startsWith(`media/${id}.`))[1],id,'generated PNG bytes are unchanged');
        }
        assert.deepEqual(record(state.canvas,'history-1').outputs,[]);
        assert.deepEqual(node(state.canvas,'target').images,[]);
        assert.equal(evaluate(e.ctx,'undoStack.length'),1);
        assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),false);
        if(mode==='single'){
            assert.equal(node(state.canvas,'copy').images[0].asset_id,e.seed.asset_id);
            assert.ok(state.media.some(item=>item.asset_id===e.seed.asset_id));
            assert.ok(e.calls.at(-1).data.deleted.deletion_id);
        }else{
            assert.deepEqual(node(state.canvas,'copy').images,[]);
            assert.ok(!state.media.some(item=>item.asset_id===e.seed.asset_id));
            assert.ok(Object.keys(state.files).some(file=>file.startsWith(`trash/media/${e.seed.asset_id}.`)));
        }
        assert.equal(state.trash.length,1);
        assert.ok(!e.harness.toasts.some(message=>message.includes('其他窗口')));
    });
    for(const injection of ['target-node','target-history','new-node-reference','new-history-reference','new-mask-reference','already-deleted']){
        test(`${mode}: ${injection} rejects safely rather than retrying a changed deletion read-set`,async()=>{
            const e=fixture(mode,injection);
            await e.invoke();
            assert.equal(e.calls.length,1,'a real target/reference conflict must not be retried');
            assert.equal(e.calls[0].status,409);
            const state=e.store('inspect');
            assert.deepEqual(state.canvas,e.calls[0].before_delete,'rejected deletion must leave the entire authoritative canvas unchanged');
            assert.ok(state.media.some(item=>item.asset_id===e.seed.asset_id),'the existing copied reference must retain media');
            assert.equal(evaluate(e.ctx,'undoStack.length'),0);
            assert.ok(e.harness.toasts.includes('canvas.originalDeleteTargetChanged'),JSON.stringify(e.harness.toasts));
            assert.ok(!e.harness.toasts.some(message=>message.includes('其他窗口')));
        });
    }
    test(`${mode}: repeated unrelated writes stop at the fixed budget with neutral wording and no fake success`,async()=>{
        const e=fixture(mode,'generation',10);
        await e.invoke();
        assert.equal(e.calls.length,4);
        assert.ok(e.calls.every(c=>c.status===409));
        const state=e.store('inspect');
        assert.deepEqual(state.canvas,e.calls.at(-1).before_delete);
        assert.equal(evaluate(e.ctx,'undoStack.length'),0);
        assert.ok(e.harness.toasts.includes('canvas.originalDeleteBusy'),JSON.stringify(e.harness.toasts));
    });
    test(`${mode}: a newer broadcast cannot smuggle a changed target into the retry revision`,async()=>{
        const e=fixture(mode,'generation',1,(editor,response,store)=>{
            const newer=store('edit-target').canvas;
            assert.ok(newer.revision>response.data.detail.canvas.revision);
            editor.ctx.applyMergedServerCanvas(newer);
        });
        await e.invoke();
        assert.equal(e.calls.length,1);
        assert.ok(e.harness.toasts.includes('canvas.originalDeleteTargetChanged'),JSON.stringify(e.harness.toasts));
        assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    });
    test(`${mode}: a new local reference during conflict is retained and blocks retry`,async()=>{
        const e=fixture(mode,'generation',1,editor=>{
            editor.node('copy').newReference={asset_id:e.seed.asset_id};
            editor.ctx.observeSmartCanvasPending();
        });
        await e.invoke();
        assert.equal(e.calls.length,1);
        assert.ok(e.harness.toasts.includes('canvas.originalDeleteTargetChanged'),JSON.stringify(e.harness.toasts));
        assert.equal(e.node('copy').newReference.asset_id,e.seed.asset_id);
        assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),true);
    });
    test(`${mode}: unrelated local intent survives the successful conflict rebase`,async()=>{
        const e=fixture(mode,'generation',1,editor=>{
            editor.node('background').x=765;
            editor.ctx.observeSmartCanvasPending();
        });
        await e.invoke();
        assert.deepEqual(e.calls.map(call=>call.status),[409,200],JSON.stringify(e.harness.toasts));
        assert.equal(e.node('background').x,765);
        assert.ok(e.node('background').images.length>0);
        assert.equal(evaluate(e.ctx,'smartCanvasPendingState().journal.hasPending()'),true);
    });
    test(`${mode}: project switch during a real conflict cannot adopt or retry into the next project`,async()=>{
        const e=fixture(mode,'generation',1,(editor)=>evaluate(editor.ctx,"canvas.project_id='different-project'"));
        await e.invoke();
        assert.equal(e.calls.length,1);
        assert.ok(e.harness.toasts.includes('smart.canvasConflict'),JSON.stringify(e.harness.toasts));
        assert.equal(evaluate(e.ctx,'undoStack.length'),0);
    });
}

test('deletion read-set covers nested reference aliases and rejects missing or shifted occurrence identity',()=>{
    const output={asset_id:'original',output_id:'output-1'};
    const canvas={nodes:[{id:'owner',images:[output]},{id:'other',images:[]}],connections:[],
        history:[{id:'history',resultNodeId:'owner',outputs:[output]}]};
    const identity={assetId:'original',historyId:'history',outputId:'output-1',outputIndex:0};
    const baseline=originalDeletionReadSet(canvas,identity);
    assert.notEqual(baseline,null);
    for(const key of ['asset_id','original_asset_id','originalAssetId','input_asset_id','inputAssetId',
        'output_asset_id','outputAssetId','model_input_asset_id','modelInputAssetId','model_result_asset_id','modelResultAssetId',
        'selection_asset_id','selectionAssetId','mask_asset_id','maskAssetId','future_reference']){
        const next=structuredClone(canvas);next.nodes[1].nested={[key]:' original '};
        assert.notEqual(originalDeletionReadSet(next,identity),baseline,key);
    }
    for(const key of ['asset_ids','assetIds','reference_asset_ids','referenceAssetIds','image_reference_asset_ids',
        'imageReferenceAssetIds','video_reference_asset_ids','videoReferenceAssetIds','audio_reference_asset_ids','audioReferenceAssetIds']){
        const next=structuredClone(canvas);next.nodes[1].nested={[key]:['original']};
        assert.notEqual(originalDeletionReadSet(next,identity),baseline,key);
    }
    for(const change of [next=>next.history[0].outputs.unshift({asset_id:'peer',output_id:'output-2'}),
        next=>next.history[0].outputs[0].output_id='replacement',next=>next.nodes.shift(),next=>next.history=[]]){
        const next=structuredClone(canvas);change(next);
        assert.equal(originalDeletionReadSet(next,identity),null);
    }
    const reordered={history:canvas.history,connections:canvas.connections,nodes:canvas.nodes,revision:99,updated_at:999};
    assert.equal(originalDeletionReadSet(reordered,identity),baseline,'object key order and global revision are not target changes');
    const unrelated=structuredClone(canvas);unrelated.nodes[1].prompt='other edit';
    unrelated.history.push({id:'other-history',outputs:[{asset_id:'other'}]});
    assert.equal(originalDeletionReadSet(unrelated,identity),baseline);
});

test('project-wide read-set permits a missing old producer but detects its later recreation',()=>{
    const canvas={nodes:[{id:'copy',images:[{asset_id:'original'}]}],
        history:[{id:'history',result_node_id:'removed-owner',outputs:[{asset_id:'original',output_id:'output-1'}]}]};
    const identity={assetId:'original'};
    const baseline=originalDeletionReadSet(canvas,identity);
    assert.notEqual(baseline,null,'D4 can still remove references after the producer was deleted');
    canvas.nodes.push({id:'removed-owner',images:[]});
    assert.notEqual(originalDeletionReadSet(canvas,identity),baseline);
});
