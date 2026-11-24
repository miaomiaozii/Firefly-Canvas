import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createCanvasContext,loadModules,HTML_SCRIPT_ORDER,evaluate} from './harness.mjs';
// Frozen owned product case 10; public canvas/history only, no queue URLs or credentials.
const fixture=JSON.parse(readFileSync(new URL('./fixtures/retrieval-canonical-case10-493.json',import.meta.url),'utf8'));
const html=readFileSync(new URL('../../static/smart-canvas.html',import.meta.url),'utf8');
async function classic(groupChange={}){
    const {ctx,harness}=createCanvasContext({locationSearch:`?id=${fixture.id}`});
    for(const match of html.matchAll(/\bid="([^"]+)"/g)){
        const el=harness.element(match[1]);el.style.setProperty=(key,value)=>el.style[key]=value;el.style.removeProperty=key=>delete el.style[key];
    }
    const world=harness.element('world');world.childNodes=[];world.insertAdjacentHTML=()=>{};
    const create=ctx.document.createElement;ctx.document.createElement=tag=>{const el=create(tag);if(tag==='template')el.content={querySelector:()=>null};return el;};
    ctx.getComputedStyle=()=>({display:'none',getPropertyValue:()=>''});
    ctx.ResizeObserver=class{observe(){} disconnect(){}};ctx.MutationObserver=class{observe(){} disconnect(){}};
    ctx.Image=class{};ctx.CSS={escape:x=>x,supports:()=>false};ctx.BroadcastChannel=class{};
    const calls=[],canvas=structuredClone(fixture),state={group:{...structuredClone(fixture.history[0].download),...groupChange}};
    ctx.fetch=async(url,options={})=>{calls.push({url,options});assert.ok(!options.method,'read-only lifecycle');return {ok:true,json:async()=>url===`/api/canvases/${fixture.id}`?{canvas}:url.includes('image-result-downloads')?{groups:state.group?[state.group]:[]}:{groups:[],snapshots:[],annotations:[]}};};
    loadModules(ctx,HTML_SCRIPT_ORDER);
    // Use production durable projection, then the actual classic loadCanvas hydration pipeline.
    const durable=ctx.FireflyProjectCanvasMutations.durableSnapshotFromCanvas(canvas);
    canvas.nodes=Object.values(durable.nodes).map(encoded=>JSON.parse(encoded));
    assert.equal(typeof ctx.onload,'function');assert.equal(await ctx.loadCanvas(),true);
    await ctx.canvasResultRetrieval.refresh();
    return {ctx,harness,calls,state,node:evaluate(ctx,'canvasState.nodes[0]'),api:ctx.canvasResultRetrieval};
}
test('real classic load and canonical media hydration retain two durable outputs plus two normal missing slots',async()=>{
    const {ctx,node,api,calls}=await classic();
    assert.equal(node.type,'smart-image');assert.equal(node.pendingTasks,undefined);
    assert.equal(node.images.length,2);assert.equal(node._batchTotal,4);
    assert.deepEqual(Array.from(node.images,x=>[x.output_id,x.output_order,x.asset_id]),fixture.nodes[0].images.map(x=>[x.output_id,x.output_order,x.asset_id]));
    assert.deepEqual([node.x,node.y],[80,80]);
    const before=JSON.stringify(node),layout=ctx.imageLayout(node.images,ctx.nodeScale(node),node),body=ctx.nodeBodyHtml(node,layout);
    assert.equal((body.match(/data-retrieval-slot=/g)||[]).length,2);
    assert.equal((body.match(/class="thumb-item /g)||[]).length,2);
    for(const image of node.images){assert.equal(body.split(`data-output-id="${image.output_id}"`).length-1,1);assert.ok(image.url.includes(image.asset_id));}
    assert.equal((body.match(/width:192px;height:144px/g)||[]).length,4);
    assert.equal(api.slots(api.nodeGroup(node),node.images).length,4);
    assert.equal(JSON.stringify(node),before);assert.ok(calls.every(x=>!x.options.method));
});
test('canonical ownership rejects missing or foreign history/task/node/project without ephemeral tasks',async()=>{
    const {ctx,node,api,state}=await classic();assert.ok(api.nodeGroup(node));
    const history=evaluate(ctx,'canvas.history'),original=history[0];
    for(const change of [{id:'other'},{task_id:'other'},{result_node_id:'other'}]){history[0]={...original,...change};assert.equal(api.nodeGroup(node),null);}
    history.length=0;assert.equal(api.nodeGroup(node),null);history.push(original);
    assert.equal(api.nodeGroup({...node,id:'other'}),null);
    assert.equal(api.nodeGroup({...node,pendingTasks:[{taskId:'other',download:{history_id:original.id}}]}),null);
    for(const change of [{project_id:'other'},{history_id:'other'},{task_id:'other'},{node_id:'other'},{node_exists:false}]){
        const foreign=await classic(change);assert.equal(foreign.api.nodeGroup(foreign.node),null);
    }
    state.group={...state.group,project_id:'other'};await api.refresh();assert.equal(api.historyGroup({...original,id:'other'}),null);
});
test('visible toolbar badge changes 2 to 1 to 0, preserves icon/accessibility and project hidden semantics',async()=>{
    const {ctx,harness,api,state}=await classic(),badge=harness.element('smartRetrievalToggle');
    const label=()=>harness.element('smartRetrievalCount');
    assert.equal(badge.hidden,false);assert.equal(label()?.textContent,'2 张待取回');
    // Keep the real list polling and canonical merge bridge; do not manually configure module services.
    Object.assign(state.group.items[2],{status:'unavailable',can_retrieve:false});await api.refresh();assert.equal(label()?.textContent,'1 张待取回');
    Object.assign(state.group.items[3],{status:'unavailable',can_retrieve:false});await api.refresh();assert.equal(label()?.textContent,'0 张待取回');
    assert.equal(badge.hidden,false);assert.equal(badge.getAttribute('aria-label'),'0 张待取回');assert.equal(badge.getAttribute('aria-expanded'),'false');
    assert.match(html,/id="smartRetrievalToggle"[^>]*><svg[^>]*aria-hidden="true"/);
    state.group=null;await api.refresh();assert.equal(badge.hidden,true);
    assert.equal(typeof ctx.canvasCore.refreshIcons,'function');
});
test('retrieval-only toolbar label overrides icon-only span hiding without resizing thumbnails/fonts',()=>{
    const css=readFileSync(new URL('../../static/css/canvas-result-retrieval.css',import.meta.url),'utf8');
    assert.match(css,/\.smart-top-left > \.retrieval-toggle \.retrieval-count\s*\{[^}]*display:inline/);
    assert.match(css,/\.smart-top-left > \.retrieval-toggle\s*\{[^}]*width:auto/);
    assert.doesNotMatch(css,/font-size:|font-family:/);
});
