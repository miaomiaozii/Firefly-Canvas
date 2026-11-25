import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createCanvasContext,loadModules,evaluate} from './harness.mjs';

const source=readFileSync(new URL('../../static/js/smart-canvas.js',import.meta.url),'utf8');
const dto=(overrides={})=>({project_id:'p',history_id:'h',node_id:'n',task_id:'t',status:'download_failed',items:[0,1,2,3].map(index=>({id:`private-${index}`,index,output_id:index===0?'o0':null,status:index===0?'succeeded':'failed',can_retrieve:index!==0,remaining_seconds:90,expiry_estimated:false,error:''})),...overrides});
const log={id:'h',task_id:'t',result_node_id:'n',outputs:[]};
const response=data=>({ok:true,json:async()=>data});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function setup(group=dto()) {
    const {ctx,harness}=createCanvasContext();
    harness.element('smartHistoryList');
    loadModules(ctx,['canvas-node-types.js','canvas-core.js','canvas-state.js','canvas-result-retrieval.js','canvas-media-history.js']);
    const calls=[], state={project:'p',history:[structuredClone(log)],group,adoptions:[]};
    ctx.fetch=async(url,options={})=>{calls.push({url,options});return response(options.method ? state.group : {groups:[state.group]});};
    const api=ctx.canvasResultRetrieval;
    api.configure({getProjectId:()=>state.project,getHistory:()=>state.history,adoptCanonical:async p=>{state.adoptions.push(p);return true;},repaint:()=>{}});
    await api.refresh();
    return {ctx,harness,calls,state,api};
}

test('single slot sends only private queue ID, canonical adoption, no asset or node mutation',async()=>{
    const {api,calls,state}=await setup();
    const node={id:'n',pendingTasks:[{taskId:'t',download:{history_id:'h'}}],images:[{output_id:'o0',asset_id:'keep',output_order:0}]};
    const before=JSON.stringify(node);
    assert.ok(api.nodeHtml(node,{cols:2,thumb:192,gridHeight:300},()=>'<saved/>').includes('<saved/>'));
    await api.retrieve('h',['private-1']);
    const post=calls.filter(c=>c.options.method);
    assert.equal(post.length,1);
    assert.equal(post[0].url,'/api/project-library/p/image-result-downloads/h/redownload');
    assert.deepEqual(JSON.parse(post[0].options.body),{item_ids:['private-1']});
    assert.deepEqual(state.adoptions,['p']);
    assert.equal(JSON.stringify(node),before);
    assert.ok(calls.every(c=>!c.url.includes('generation')));
});

test('batch exact IDs, de-duplicates parallel views, queues disjoint single slots',async()=>{
    const {api,calls,ctx,state}=await setup();
    let release;
    ctx.fetch=async(url,options={})=>{calls.push({url,options});if(calls.filter(c=>c.options.method).length===1) await new Promise(r=>release=r);return response(state.group);};
    const one=api.retrieve('h',['private-1','private-1']); await tick();
    const duplicate=api.retrieve('h',['private-1']);
    const batch=api.retrieve('h',['private-2','private-3','foreign']);
    release(); await Promise.all([one,duplicate,batch]);
    assert.deepEqual(calls.filter(c=>c.options.method).map(c=>JSON.parse(c.options.body).item_ids),[['private-1'],['private-2','private-3']]);
});

test('history/task/node/project identity all required; node ID alone grants no action',async()=>{
    const {api,state,calls}=await setup();
    for(const altered of [{...log,id:'other'},{...log,task_id:'other'},{...log,result_node_id:'other'}]) assert.equal(api.historyGroup(altered),null);
    assert.equal(api.nodeGroup({id:'n',pendingTasks:[{taskId:'old',download:{history_id:'h'}}]}),null);
    state.history=[{...log,task_id:'foreign'}]; await api.retrieve('h',['private-1']);
    assert.equal(calls.filter(c=>c.options.method).length,0);
    state.project='other'; api.sync();
    assert.equal(api.historyGroup(log),null);
});

test('late project response cannot populate new project, including switch back',async()=>{
    const {api,ctx,state}=await setup(); let release;
    let first=true;
    ctx.fetch=async()=>{if(first){first=false;await new Promise(r=>release=r);return response({groups:[dto()]});}return response({groups:[]});};
    const old=api.refresh(); await tick(); state.project='q'; api.sync(); state.project='p'; api.sync(); release(); await old;
    assert.equal(api.historyGroup(log),null);
});

test('mixed expiry independently renders recoverable, confirmed unavailable, byte metrics, no invented percent',async()=>{
    const group=dto(); group.items[1]={...group.items[1],status:'expired',can_retrieve:true,remaining_seconds:0}; group.items[2]={...group.items[2],status:'unavailable',can_retrieve:false,error:'expired token'}; group.items[3]={...group.items[3],status:'downloading',can_retrieve:false,downloaded_bytes:20*1024*1024,total_bytes:null};
    const {api}=await setup(group);
    const a=api.slotHtml(group,group.items[1]),b=api.slotHtml(group,group.items[2]),c=api.slotHtml(group,group.items[3]);
    assert.match(a,/仍可查询原结果/); assert.doesNotMatch(a,/ disabled/);
    assert.match(b,/已确认无法取回/); assert.doesNotMatch(b,/<button|data-retrieval-item=/);
    assert.match(c,/20\.0 MB/); assert.doesNotMatch(c,/%/);
    assert.match(api.summaryHtml(group),/全部取回（2）/);
});

test('partial success updates batch count without covering unavailable sibling',async()=>{
    const {api,state}=await setup();
    assert.match(api.summaryHtml(state.group),/全部取回（3）/);
    state.group=structuredClone(state.group); state.group.items[1].status='succeeded'; state.group.items[1].can_retrieve=false; state.group.items[2].status='unavailable'; state.group.items[2].can_retrieve=false;
    await api.retrieve('h',['private-1','private-2','private-3']);
    const group=api.historyGroup(log);
    assert.doesNotMatch(api.summaryHtml(group),/全部取回/);
    assert.match(api.slotHtml(group,group.items[2]),/已确认无法取回/);
});

test('actual node body renderer prioritizes retrieval over failed generation actions and preserves 192×144 gap12',async()=>{
    const {ctx,api}=await setup(dto({items:dto().items.map(i=>({...i,status:'failed',can_retrieve:true,output_id:null}))}));
    for(const name of ['nodeBodyHtml']) {const start=source.indexOf(`function ${name}(`); evaluate(ctx,source.slice(start,source.indexOf('\n}',start)+2));}
    Object.assign(ctx,{isSmartAgentTaskNode:()=>false,isSmartConversationNode:()=>false,isSmartAgentNode:()=>false,imageForDisplay:x=>x});
    const node={id:'n',type:'smart-image',images:[],pendingTasks:[{taskId:'t',download:{history_id:'h'}}],failedGenerationTasks:[{error:'failed'}]};
    const before=JSON.stringify(node);
    const html=ctx.nodeBodyHtml(node,{cols:2,rows:2,thumb:192,gridHeight:300});
    assert.equal((html.match(/data-retrieval-slot=/g)||[]).length,4);
    assert.match(html,/width:192px;height:144px/); assert.match(html,/gap:12px/);
    assert.doesNotMatch(html,/data-image-task-retry|重新生成<\/button>/);
    assert.equal(JSON.stringify(node),before);
    const buttons=['private-0','private-1'].map(id=>({dataset:{retrievalHistory:'h',retrievalItem:id}}));
    api.bind({querySelectorAll:()=>buttons}); let stopped=0; await buttons[1].onclick({stopPropagation(){stopped++;}}); assert.equal(stopped,1);
});

test('actual history renderer retains existing thumbnail classes and binds retrieval event IDs',async()=>{
    const {ctx,api,harness,calls}=await setup(dto({items:dto().items.map(i=>({...i,status:'failed',can_retrieve:true,output_id:null}))}));
    Object.assign(ctx,{smartHistorySizeSummary:()=>'',smartHistoryResolutionTier:()=>'',historyLogModelMeta:()=>({}),formatRunDuration:()=>'',trf:x=>x,ico:()=>'',smartHistoryList:harness.makeElement()});
    evaluate(ctx,'canvas={history:[],format:"firefly-canvas-v1"}');
    const html=ctx.smartHistoryLogCardHtml({...log,outputs:[],status:'failed'},0);
    assert.match(html,/4 张待取回/); assert.equal((html.match(/retrieval-history-slot/g)||[]).length,4);
    assert.match(html,/class="log-thumb-square"/); assert.doesNotMatch(html,/data-history-retry-task/);
    const button={dataset:{retrievalHistory:'h',retrievalItem:'private-2'}};
    harness.element('smartHistoryList').querySelectorAll=selector=>selector==='[data-retrieval-history]'?[button]:[];
    // Real bindSmartHistoryWindow wires retrieval first; unrelated image/clipboard binders are inert fixtures.
    ctx.bindSmartPreviewImageFallbacks=()=>{};ctx.bindLongContentHistoryTriggers=()=>{};ctx.refreshIcons=()=>{};
    ctx.smartHistoryList.addEventListener=()=>{};
    ctx.smartHistoryList.querySelector=()=>null;
    ctx.bindSmartHistoryWindow();
    await button.onclick({stopPropagation(){}});
    assert.deepEqual(JSON.parse(calls.find(c=>c.options.method).options.body),{item_ids:['private-2']});
});

test('request and canonical adoption failures remain visible; no fallback generation request',async()=>{
    const {api,ctx,state,calls}=await setup();
    ctx.fetch=async(url,options)=>{calls.push({url,options});return {ok:false,status:409,json:async()=>({detail:'identity changed'})};};
    await api.retrieve('h',['private-1']); assert.match(api.summaryHtml(state.group),/identity changed/);
    ctx.fetch=async()=>response(state.group);api.configure({adoptCanonical:async()=>false});
    await api.retrieve('h',['private-1']);assert.match(api.summaryHtml(state.group),/画布同步未确认/);
});

test('four original slots with two saved keep exact order and normal output identities',async()=>{
    const group=dto();group.items[1]={...group.items[1],status:'succeeded',can_retrieve:false,output_id:'o1'};
    const {api}=await setup(group);
    const saved=[{output_id:'o1',asset_id:'asset1',output_order:1},{output_id:'o0',asset_id:'asset0',output_order:0}];
    const before=JSON.stringify(saved),rows=api.slots(group,saved);
    assert.deepEqual(Array.from(rows,row=>row.output?.asset_id || row.item.id),['asset0','asset1','private-2','private-3']);
    assert.match(api.summaryHtml(group),/全部取回（2）/); assert.equal(JSON.stringify(saved),before);
});

test('unproved Agent task card receives no actions; matching original task/result/history does',async()=>{
    const {ctx}=await setup();loadModules(ctx,['canvas-agent-tui.js']);
    Object.assign(ctx,{ico:()=>'',trf:x=>x,agentTaskStatusText:x=>x});evaluate(ctx,'canvas={history:[{id:"h",task_id:"t",result_node_id:"n"}]}');
    const node={id:'card',task_id:'other',result_node_id:'n',error:'download failed'};
    assert.doesNotMatch(ctx.agentTaskNodeBodyHtml(node),/data-retrieval-history/);
    const html=ctx.agentTaskNodeBodyHtml({...node,task_id:'t'});
    assert.match(html,/data-retrieval-history="h"/);assert.doesNotMatch(html,/data-image-task-retry/);
});

test('toolbar server group outside client history window posts only its own selected IDs',async()=>{
    const {api,state,harness,calls}=await setup(); state.history=[];
    const badge=harness.element('smartRetrievalToggle'),panel=harness.element('smartRetrievalPanel');
    const close=harness.makeElement(),button={dataset:{retrievalHistory:'h',retrievalAll:'1'}};
    panel.querySelector=selector=>selector==='[data-retrieval-close]'?close:null;
    panel.querySelectorAll=selector=>selector==='[data-retrieval-history]'?[button]:[];
    await api.refresh(); badge.onclick(); await tick();
    await button.onclick({stopPropagation(){}});
    assert.deepEqual(JSON.parse(calls.find(c=>c.options.method)?.options.body || 'null'),{item_ids:['private-1','private-2','private-3']});
    assert.equal(api.nodeGroup({id:'n',pendingTasks:[{taskId:'t',download:{history_id:'h'}}]}),null);
});

test('three saved plus confirmed unavailable is an outcome, not one awaiting retrieval',async()=>{
    const group=dto();group.items.forEach((item,index)=>Object.assign(item,{status:index===3?'unavailable':'succeeded',can_retrieve:false}));
    const {api}=await setup(group),html=api.summaryHtml(group);
    assert.doesNotMatch(html,/待取回|全部取回/); assert.match(html,/3.*已保存/); assert.match(html,/1.*无法取回/);
});

test('four in-flight originals retain disabled batch count; partial completion shows three remaining and each outcome',async()=>{
    const group=dto({items:dto().items.map(item=>({...item,status:'failed',can_retrieve:true}))});
    const {api,ctx,state}=await setup(group);let release;
    ctx.fetch=async()=>{await new Promise(r=>release=r);return response(state.group);};
    const run=api.retrieve('h',group.items.map(item=>item.id));await tick();
    let html=api.summaryHtml(group);assert.match(html,/全部取回（4）/);assert.match(html,/disabled/);
    state.group=structuredClone(group);state.group.items[0].status='succeeded';state.group.items[0].can_retrieve=false;
    release();await run;
    const next=api.historyGroup(log);html=api.summaryHtml(next);assert.match(html,/全部取回（3）/);assert.match(html,/1.*已保存/);
    assert.match(api.slotHtml(next,next.items[0]),/已保存/);assert.match(api.slotHtml(next,next.items[1]),/取回失败/);
});

test('real sync fetches next project immediately despite old scheduled timer and ignores stale response',async()=>{
    const {api,ctx,state,calls}=await setup();let release;
    ctx.fetch=async(url)=>{calls.push({url,options:{}});if(url.includes('/p/')) {await new Promise(r=>release=r);return response({groups:[dto()]});}return response({groups:[dto({project_id:'q',history_id:'hq'})]});};
    const old=api.refresh();await tick();state.project='q';api.sync();await tick();
    assert.ok(calls.some(call=>call.url.includes('/q/')));assert.ok(api.historyGroup({...log,id:'hq'}));
    release();await old;assert.equal(api.historyGroup(log),null);assert.ok(api.historyGroup({...log,id:'hq'}));
});

test('in-flight poll publishes saved/unavailable outcomes immediately and keeps exact remaining count',async()=>{
    const group=dto({items:dto().items.map(item=>({...item,status:'failed',can_retrieve:true}))});
    const {api,ctx,state,harness}=await setup(group);let release;
    ctx.fetch=async(url,options={})=>{if(options.method) await new Promise(r=>release=r);return response(options.method?state.group:{groups:[state.group]});};
    const run=api.retrieve('h',group.items.map(item=>item.id));await tick();
    assert.ok(harness.timers.some(timer=>!timer.cancelled && timer.ms===1000));
    state.group=structuredClone(group);Object.assign(state.group.items[0],{status:'succeeded',can_retrieve:false});
    await api.refresh();let next=api.historyGroup(log);
    assert.match(api.slotHtml(next,next.items[0]),/已保存/);assert.match(api.summaryHtml(next),/全部取回（3）/);
    Object.assign(state.group.items[1],{status:'unavailable',can_retrieve:false});await api.refresh();next=api.historyGroup(log);
    assert.match(api.slotHtml(next,next.items[1]),/已确认无法取回/);assert.match(api.summaryHtml(next),/全部取回（2）/);
    release();await run;
});

test('a fired poll timer cannot block the next project sync initial fetch',async()=>{
    const {api,ctx,state,harness,calls}=await setup();
    ctx.fetch=async(url,options={})=>{calls.push({url,options});return response({groups:[dto({project_id:state.project})]});};
    assert.equal(harness.advanceTimers(),1);await tick();state.project='q';api.sync();await tick();
    assert.ok(calls.some(call=>call.url.includes('/q/')));
    assert.equal(api.historyGroup(log).project_id,'q');
});

test('output order cannot substitute a different durable output ID',async()=>{
    const {api,state}=await setup();
    const rows=api.slots(state.group,[{output_id:'other-occurrence',output_order:0,asset_id:'foreign'}]);
    assert.equal(rows[0].item.id,'private-0');
    assert.equal(rows.filter(row=>row.output).length,1);
});

test('layout contains summary inside bounded node flow and uses existing toolbar icon styling',()=>{
    const css=readFileSync(new URL('../../static/css/canvas-result-retrieval.css',import.meta.url),'utf8');
    assert.doesNotMatch(css,/top:100%|left:648px/);
    const html=readFileSync(new URL('../../static/smart-canvas.html',import.meta.url),'utf8');
    assert.match(html,/id="smartRetrievalToggle"[^>]*>[\s\S]*?<svg/);
});

test('CSS keeps button hit height and does not resize history or fonts',()=>{
    const css=readFileSync(new URL('../../static/css/canvas-result-retrieval.css',import.meta.url),'utf8');
    assert.match(css,/min-height:32px/);assert.doesNotMatch(css,/font-size:|font-family:|\.log-thumb-square\s*\{/);
});
