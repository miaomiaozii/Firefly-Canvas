import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createCanvasContext,loadModules,evaluate} from './harness.mjs';
import {editor} from './smart-human-handlers-harness-392.mjs';
const path=process.env.FIREFLY_UI2_QA;
// The companion Python test exports the actual ASGI response and restarted ProjectStore, never a JS-made DTO.
const fixture=JSON.parse(readFileSync(`${path}/canonical-projection.json`,'utf8'));
const source=readFileSync(new URL('../../static/js/smart-canvas.js',import.meta.url),'utf8');

test('real restarted project/ASGI projection reaches exact node/history/Agent matchers without writes',async()=>{
    const {ctx}=createCanvasContext();loadModules(ctx,['canvas-node-types.js','canvas-core.js','canvas-state.js','canvas-result-retrieval.js','canvas-agent-tui.js']);
    const canvas=fixture.canvas,group=fixture.listing.groups[0],calls=[];
    ctx.canvasResultRetrieval.configure({getProjectId:()=>group.project_id,getHistory:()=>canvas.history,fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>fixture.listing};}});
    await ctx.canvasResultRetrieval.refresh();
    const history=canvas.history.find(h=>h.id===group.history_id),node=canvas.nodes.find(n=>n.id===group.node_id);
    assert.ok(history);assert.equal(history.task_id,group.task_id);assert.equal(history.result_node_id,group.node_id);
    assert.ok(ctx.canvasResultRetrieval.historyGroup(history));assert.ok(ctx.canvasResultRetrieval.nodeGroup(node));
    const before=JSON.stringify(canvas);evaluate(ctx,`canvas=${JSON.stringify(canvas)}`);
    const html=ctx.agentTaskNodeBodyHtml(fixture.agent);
    assert.match(html,/data-retrieval-history=/);assert.doesNotMatch(html,/data-image-task-retry|data-history-retry-task/);
    assert.equal(JSON.stringify(canvas),before);assert.ok(calls.every(c=>!c.options?.method));
    assert.equal(ctx.canvasResultRetrieval.historyGroup({...history,task_id:'previous-paid-task'}),null);
    assert.equal(ctx.canvasResultRetrieval.nodeGroup({...node,pendingTasks:[{taskId:'previous-paid-task',download:{history_id:group.history_id}}]}),null);
});

test('retrieval polling uses the real canonical merge without clobbering unsaved prompt, position, selection or viewport',async()=>{
    const e=editor([{id:'prompt',type:'smart-prompt',text:'before',x:1,y:2}]);
    Object.assign(e.ctx,{smartGenerationNodeRevisions:new Map(),conversationCanvasLastAcceptedRevision:0,smartHistoryModal:null,dragState:null,selectionState:null,pendingAgentMergeUndo:false,resumeSmartPendingTasks(){},resumeSmartPersistencePendingTasks(){},resumeSmartPendingActionResumes(){},setSmartTitleText(){}});
    loadModules(e.ctx,['canvas-result-retrieval.js','canvas-agent-tui.js']);
    const remote=JSON.parse(JSON.stringify(evaluate(e.ctx,'canvas')));remote.revision=2;remote.updated_at=500;
    Object.assign(e.node(),{text:'unsaved 中文',x:123,positionUpdatedAt:1000});e.ctx.scheduleSave();
    Object.assign(e.ctx.viewport,{x:55,y:66,scale:1.25});evaluate(e.ctx,'canvasState.selectedId="prompt"');
    let saved=false;const requests=[];const group={...fixture.listing.groups[0],project_id:'unit-392'};
    e.ctx.fetch=async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>url.includes('image-result-downloads')?{groups:[{...group,items:group.items.map(item=>saved?{...item,status:'succeeded',can_retrieve:false}:item)}]}:{canvas:remote}};};
    const api=e.ctx.canvasResultRetrieval,adoptions=[];api.configure({getProjectId:()=>e.ctx.canvasId,getHistory:()=>evaluate(e.ctx,'canvas.history'),adoptCanonical:async p=>{const accepted=await e.ctx.mergeReloadCanvasNow(p);adoptions.push(accepted);return accepted;}});
    await api.refresh();saved=true;await api.refresh();
    assert.deepEqual(adoptions,[true]);
    assert.ok(requests.some(request=>request.url.includes('/api/canvases/')));assert.ok(requests.every(request=>!request.options?.method));
    assert.equal(e.node().text,'unsaved 中文');assert.equal(e.node().x,123);
    assert.equal(evaluate(e.ctx,'canvasState.selectedId'),'prompt');assert.deepEqual({...e.ctx.viewport},{x:55,y:66,scale:1.25});
    assert.equal(e.ctx.smartCanvasPendingState().journal.hasPending(),true);
});

test('actual mergeReloadCanvasNow has a strict boolean result and does not replace another project',async()=>{
    const {ctx}=createCanvasContext();const start=source.indexOf('async function mergeReloadCanvasNow('),end=source.indexOf('\nwindow.refreshSmartCanvas',start);
    evaluate(ctx,source.slice(start,end));Object.assign(ctx,{canvasId:'p',dragState:null,selectionState:null,pendingAgentMergeUndo:false});
    const calls=[];ctx.applyMergedServerCanvas=canvas=>{calls.push(canvas);return true;};
    ctx.fetch=async()=>({ok:true,json:async()=>({canvas:{id:'p'}})});
    assert.equal(await ctx.mergeReloadCanvasNow('p'),true);assert.equal(await ctx.mergeReloadCanvasNow('q'),false);
    let release;ctx.fetch=async()=>new Promise(resolve=>release=resolve);
    const late=ctx.mergeReloadCanvasNow('p');ctx.canvasId='q';release({ok:true,json:async()=>({canvas:{id:'p'}})});
    assert.equal(await late,false);assert.equal(calls.length,1);
});
