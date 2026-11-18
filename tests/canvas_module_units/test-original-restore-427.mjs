// #427: manual restore uses the real handlers, receiver, undo module and PUT seam.
import assert from 'node:assert/strict';
import test from 'node:test';
import {editor, declarations} from './smart-human-handlers-harness-392.mjs';
import {evaluate, fromContext} from './harness.mjs';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
const token=(deletionId='deleted-a',projectId='unit-392')=>({assetId:'asset-a',deletionId,projectId});
const outputs=[1,2,3].map(i=>({asset_id:'asset-a',output_id:`output-${i}`,url:`/fixture-${i}.png`}));
function fixture(){
    const e=editor([{id:'photo',type:'smart-image',images:[]}]);
    Object.assign(e.ctx,{smartNodeRunTokens:new Map(),smartRunTokens:new Map(),smartGenerationNodeRevisions:new Map(),smartHistoryModal:null});
    evaluate(e.ctx,declarations('canvas-agent-tui.js'));
    let server=fromContext(e.ctx.canvasForStorage());server.revision=1;
    e.ctx.applyMergedServerCanvas(server);
    let persisted={undo:[],redo:[]};
    const puts=[];
    e.ctx.fetch=async(url,options={})=>{
        if(url.endsWith('/undo-stack')){
            if(options.method==='PUT'){persisted=JSON.parse(options.body);puts.push(structuredClone(persisted));}
            return {ok:true,status:200,json:async()=>structuredClone(persisted)};
        }
        if(url.endsWith('/original/restore')){
            server.nodes[0].images=structuredClone(outputs);server.revision++;
            return {ok:true,status:200,json:async()=>({canvas:structuredClone(server)})};
        }
        return {ok:true,status:200,json:async()=>({items:[],saved:true,revision:server.revision})};
    };
    return {...e,puts,persisted:()=>persisted};
}

test('#427: history restore retires an already persisted deletion token and keeps all three output identities',async()=>{
    const e=fixture();
    e.ctx.pushUndo({projectOriginalDelete:token()});
    e.harness.advanceTimers();await tick();
    assert.equal(e.persisted().undo.length,1,'the old PUT must finish before restore: no pending timer may mask the bug');
    const putsBefore=e.puts.length;
    await e.ctx.restoreSmartProjectOriginal('asset-a','deleted-a','output-1','');
    assert.deepEqual(fromContext(e.node('photo').images).map(image=>image.output_id),['output-1','output-2','output-3']);
    assert.equal(evaluate(e.ctx,'undoStack.length'),0,'manual restore consumes the matching local token');
    e.harness.advanceTimers();await tick();
    assert.equal(e.puts.length,putsBefore+1,'manual restore must schedule its own stack cleanup PUT');
    assert.deepEqual(e.persisted(),{undo:[],redo:[]},'refresh must not reload the consumed deletion token');
});

test('#427: manual restore cleans the exact occurrence in both stacks, preserving other deletion and project tokens',async()=>{
    const e=fixture();
    for(const identity of [token(),token('other-deletion'),token('deleted-a','other-project')])
        e.ctx.pushUndo({projectOriginalDelete:identity});
    e.ctx.redoEntries=[{projectOriginalDelete:token()},{projectOriginalDelete:token('other-redo')}];
    evaluate(e.ctx,'redoStack.push(...redoEntries)');
    e.harness.advanceTimers();await tick();
    await e.ctx.restoreSmartProjectOriginal('asset-a','deleted-a');
    e.harness.advanceTimers();await tick();
    assert.deepEqual(e.persisted().undo.map(entry=>entry.projectOriginalDelete),[
        token('other-deletion'),token('deleted-a','other-project')
    ]);
    assert.deepEqual(e.persisted().redo.map(entry=>entry.projectOriginalDelete),[token('other-redo')]);
});

test('#427: a rejected manual restore retains its token in memory and persistence',async()=>{
    const e=fixture();
    e.ctx.pushUndo({projectOriginalDelete:token()});
    e.harness.advanceTimers();await tick();
    const fetch=e.ctx.fetch;
    e.ctx.fetch=(url,options)=>url.endsWith('/original/restore')
        ? Promise.resolve({ok:false,status:409,json:async()=>({detail:'restore conflict'})})
        : fetch(url,options);
    await e.ctx.restoreSmartProjectOriginal('asset-a','deleted-a');
    e.harness.advanceTimers();await tick();
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);
    assert.deepEqual(e.persisted().undo[0].projectOriginalDelete,token());
    assert.equal(e.puts.length,1,'failure does not schedule a cleanup PUT');
    assert.ok(e.harness.toasts.includes('restore conflict'));
});
