import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {createCanvasContext, loadModules, evaluate, fromContext, JS_DIR} from './harness.mjs';
import {declarations} from './smart-human-handlers-harness-392.mjs';

const copy = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const source = readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
const projectUrl = '/api/project-library/protocol-392';
const taskUrl = (nodeId='conversation', requestId='old-key') => `${projectUrl}/conversation/${nodeId}/messages/${requestId}`;
const seed = () => ({id:'protocol-392',project_id:'protocol-392',format:'firefly-canvas-v1',revision:4,updated_at:400,title:'Conversation',icon:'sparkles',history:[],connections:[],nodes:[
    {id:'conversation',type:'smart-conversation',messages:[],createdBy:'agent',agentNodeId:'creator',userTouchedAt:null,promptEditedByUser:false},
    {id:'other',type:'smart-prompt',text:'before',userTouchedAt:100,promptEditedByUser:false},
]});
const v1 = (extra={}) => ({conversationProtocol:1,projectId:'protocol-392',nodeId:'conversation',requestId:'old-key',text:'hello',humanIntent:true,...extra});
const outbox = (payload=v1(), extra={}) => ({actionId:'old-action',kind:'conversation',payload,enqueuedAt:100,...extra});
const bubble = (requestId='old-key') => ({id:`local-${requestId}`,role:'user',request_id:requestId,text:'hello',status:'pending',created_at:100});
const formal = (extra={}) => ({id:'receipt',role:'user',request_id:'old-key',text:'hello',status:'completed',created_at:500,user_edit:true,...extra});
function journal(record=outbox()){
    const initial=seed();
    initial.nodes[0].pendingActionResumes=[record];
    initial.nodes[0].messages=[bubble(record.payload.requestId)];
    return initial;
}
function registered(initial, message=formal(), touch=500){
    const server=copy(initial);
    server.revision=6;server.updated_at=600;
    Object.assign(server.nodes[0],{messages:[message],pendingActionResumes:[],userTouchedAt:touch});
    return server;
}

// Keep the real persister/rebuilder/receiver; only replace transport and browser boundaries.
function editor(initial=seed()){
    const {ctx,harness}=createCanvasContext();
    loadModules(ctx,['canvas-node-types.js','canvas-core.js','canvas-state.js','project-canvas-mutations.js','canvas-connections.js','canvas-media-history.js','canvas-undo.js','canvas-generation-tasks.js','canvas-agent-tui.js']);
    vm.runInContext(source.match(/^const SMART_ONLINE_ENGINES = .+;$/m)[0],ctx);
    vm.runInContext(declarations('smart-canvas.js'),ctx);
    Object.assign(ctx,{
        AbortController,canvasId:initial.id,sourceProjectId:initial.project_id,canvasUsesConnections:true,canvasLoadReady:true,
        persistedCoreSnapshot:null,persistedDurableSnapshot:null,smartCanvasServerShadow:null,smartClientId:'conversation-unit',contentDirty:false,
        debouncedSaveTimer:null,canvasSyncInFlight:false,canvasProjectGone:false,pendingRemoteUpdatedAt:0,
        pendingAgentMergeFingerprint:'',pendingAgentMergeUndo:false,conversationCanvasLastAcceptedRevision:0,
        conversationTaskPolls:new Map(),CONVERSATION_MAX_NOT_FOUND_POLLS:30,smartGenerationNodeRevisions:new Map(),
        smartNodeRunTokens:new Map(),smartRunTokens:new Map(),projectMediaImportCache:new Map(),activeProjectMediaImports:0,
        projectMediaImportWaiters:[],SMART_LOG_PREVIEW_NODE_ID:'__preview__',DEBOUNCED_SAVE_MS:3000,
        viewport:{x:0,y:0,scale:1},canvasDefaultSmartSettings:{},initialSmartSettings:{},smartSaveButton:null,
        smartHistoryModal:null,assetLibraryOpen:false,
        render(){},applyViewport(){},setSmartTitleText(){},scheduleConnectionLayerRefresh(){},scheduleSmartHistoryWindowRender(){},
        rebuildMarkedAssetIdsFromNodes(){},updateSmartMarkedBadge(){},savePromptDraftForCurrent(){},
        toast(message){harness.toasts.push(String(message));},
    });
    evaluate(ctx,`canvas=${JSON.stringify(initial)};canvasState.nodes=canvas.nodes;`);
    ctx.persistedCoreSnapshot=ctx.FireflyProjectCanvasMutations.snapshotFromCanvas(initial);
    ctx.persistedDurableSnapshot=ctx.FireflyProjectCanvasMutations.durableSnapshotFromCanvas(initial);
    ctx.smartCanvasPendingState();
    vm.runInContext(source.slice(source.indexOf('registerSmartPendingActionPersister(async'),source.indexOf('// #377：项目在另一个标签页')),ctx);
    let server=copy(initial);
    const requests=[];
    const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>copy(data),text:async()=>JSON.stringify(data)});
    function get(url){
        if(url===projectUrl) return response({canvas:server});
        const task=url.match(/^\/api\/project-library\/protocol-392\/conversation\/([^/]+)\/messages\/([^/]+)$/);
        if(!task) throw new Error(`Unexpected GET ${url}`);
        const requestId=decodeURIComponent(task[2]);
        const message=server.nodes.find(n=>n.id===decodeURIComponent(task[1]))?.messages?.find(m=>m.request_id===requestId&&!String(m.id).startsWith('local-'));
        return message?response({canvas:server,message,status:message.status}):response({detail:'not registered'},404);
    }
    const e={ctx,harness,requests,response,get,onGet:null,
        live:()=>evaluate(ctx,'canvas'),node:(id='conversation')=>ctx.canvasState.nodes.find(n=>n.id===id),
        server:()=>copy(server),setServer(next){server=copy(next);},
        receive(next){return ctx.applyConversationCanvasResponse(copy(next));},
        async replay(){await ctx.resumeSmartPendingActionResumes(ctx.canvasState.nodes);await tick();},
    };
    ctx.fetch=async(url,options={})=>{
        const method=options.method || 'GET';
        const body=options.body?JSON.parse(options.body):null;
        requests.push({url,method,options,body});
        if(method==='GET') return e.onGet ? e.onGet(url) : get(url);
        if(method==='PATCH'&&url===`${projectUrl}/canvas`){
            for(const mutation of body.mutations){
                if(mutation.op==='set_canvas_meta'){
                    const {op,...meta}=mutation;
                    Object.assign(server,meta);
                    continue;
                }
                assert.equal(mutation.op,'upsert_node');
                const index=server.nodes.findIndex(n=>n.id===mutation.node.id);
                if(index<0) server.nodes.push(copy(mutation.node));else server.nodes[index]=copy(mutation.node);
            }
            server.revision++;server.updated_at++;
            return response({saved:true,canvas:server,revision:server.revision,updated_at:server.updated_at});
        }
        throw new Error(`Unexpected ${method} ${url}`);
    };
    return e;
}
function assertReads(e, urls){
    assert.ok(e.requests.every(request=>request.method==='GET'&&request.body===null));
    assert.equal(e.harness.dispatchedEvents.some(event=>event.type==='pagehide'),false);
    if(urls) assert.deepEqual(e.requests.map(request=>request.url),urls);
}
function assertRetained(e, initial){
    assert.deepEqual(fromContext(e.node().pendingActionResumes),initial.nodes[0].pendingActionResumes);
    assert.deepEqual(fromContext(e.node().messages),initial.nodes[0].messages);
    assert.equal(e.node().userTouchedAt,null);
    assert.equal(e.ctx.__fcPendingActionResumes.size,1);
}

test('reload GET reconciles a formal receipt ahead of its local bubble; old receipts do not backfill ownership',async()=>{
    const initial=journal();
    const oldReceipt={id:'old-formal',role:'user',request_id:'old-key',text:'hello',status:'completed',created_at:200};
    initial.nodes[0].messages.push(oldReceipt);
    const e=editor(initial);e.setServer(registered(initial,oldReceipt,null));
    await e.replay();await e.replay();
    assertReads(e,[taskUrl()]);
    assert.equal(e.requests[0].options.cache,'no-store');
    assert.equal(e.ctx.__fcPendingActionResumes.size,0);
    assert.deepEqual(fromContext(e.node().pendingActionResumes),[]);
    assert.deepEqual(fromContext(e.node().messages),[oldReceipt]);
    assert.equal(e.node().userTouchedAt,null);
});

test('legacy canonical project receipts without id or created_at are accepted without migration',async()=>{
    for(const fields of [{},{id:'old-formal'}]){
        const initial=journal(outbox({requestId:'old-key',text:'hello',humanIntent:true}));
        const message={role:'user',request_id:'old-key',text:'hello',status:'completed',...fields};
        const e=editor(initial);e.setServer(registered(initial,message,null));
        e.onGet=url=>url===taskUrl()?e.response({message,status:'completed'}):e.get(url);
        await e.replay();
        assertReads(e,[taskUrl(),projectUrl]);
        assert.ok(e.requests.every(request=>request.options.cache==='no-store'));
        assert.equal(e.ctx.__fcPendingActionResumes.size,0);
        assert.deepEqual(fromContext(e.node().messages),[message]);
        assert.deepEqual(fromContext(e.node().pendingActionResumes),[]);
        assert.equal(e.node().userTouchedAt,null);
    }
});

test('an unsent old or v1 journal remains unchanged on reload, including explicit original false',async()=>{
    const legacy=humanIntent=>({requestId:'old-key',text:'hello',...(humanIntent===undefined?{}:{humanIntent})});
    for(const payload of [legacy(),legacy(false),legacy(true),v1(),v1({humanIntent:false})]){
        const initial=journal(outbox(payload,{replayAttempts:5}));
        const e=editor(initial);
        await e.replay();await e.replay();
        assertReads(e,[taskUrl(),projectUrl,taskUrl(),projectUrl]);
        assertRetained(e,initial);
        assert.deepEqual(e.server(),initial);
        assert.deepEqual(fromContext([...e.ctx.__fcPendingActionResumes.values()][0].payload),payload);
        assert.deepEqual(e.harness.toasts,[]);
    }
});

test('invalid task or project JSON is visible, without acknowledging or rewriting the outbox',async()=>{
    for(const phase of ['task','project']){
        const initial=journal(),e=editor(initial);
        const broken={ok:true,status:200,json:async()=>{throw new SyntaxError(`${phase}-json-invalid`);}};
        e.onGet=url=>(phase==='task')===(url===taskUrl())?broken:e.get(url);
        await e.replay();
        assertReads(e,phase==='task'?[taskUrl()]:[taskUrl(),projectUrl]);
        assertRetained(e,initial);
        assert.ok(e.harness.toasts.some(message=>message.includes(`${phase}-json-invalid`)));
    }
});

test('flat project responses and standalone receipt messages cannot prove registration',async()=>{
    const initial=journal(),e=editor(initial);
    e.onGet=url=>url===taskUrl()?e.response({message:formal(),status:'completed'}):e.response(initial);
    await e.replay();
    assertReads(e,[taskUrl(),projectUrl]);
    assertRetained(e,initial);
    assert.ok(e.harness.toasts.length>0);
});

test('canonical absence of both a receipt and matching original outbox is a visible conflict',async()=>{
    const initial=journal(),e=editor(initial);
    const missing=seed();missing.revision=6;e.setServer(missing);
    await e.replay();
    assertReads(e,[taskUrl(),projectUrl]);
    assert.equal(e.ctx.__fcPendingActionResumes.size,1);
    assert.ok(e.harness.toasts.includes('smart.canvasConflict'));
    assert.deepEqual(e.server(),missing);
});

test('a stale formal GET confirms only its original intent and does not roll back the latest shadow or dirty edits',async()=>{
    const initial=journal(),e=editor(initial);
    const receipt=registered(initial);
    let release;
    e.onGet=async()=>{await new Promise(resolve=>{release=resolve;});return e.response({canvas:receipt,message:formal(),status:'completed'});};
    const replaying=e.replay();assert.equal(typeof release,'function');
    e.node('other').text='unsaved sibling';e.node('other').userTouchedAt=777;
    e.node().title='unsaved conversation title';e.ctx.scheduleSave();
    const latest=copy(receipt);latest.revision=9;latest.updated_at=900;latest.nodes[0].userTouchedAt=900;
    latest.nodes.push({id:'remote-addition',type:'smart-prompt',text:'keep remote'});
    e.setServer(latest);e.receive(latest);release();await replaying;
    assertReads(e,[taskUrl()]);
    assert.equal(e.ctx.__fcPendingActionResumes.size,0);
    assert.equal(e.live().revision,9);
    assert.equal(e.node().userTouchedAt,900);
    assert.equal(e.node('other').text,'unsaved sibling',JSON.stringify(e.harness.toasts));assert.equal(e.node('other').userTouchedAt,777);
    assert.equal(e.node().title,'unsaved conversation title');
    assert.deepEqual(fromContext(e.node().pendingActionResumes),[]);
    assert.deepEqual(fromContext(e.node().messages),[formal()]);
    assert.equal(await e.ctx.saveCanvas(),true,JSON.stringify(e.harness.toasts));
    assert.deepEqual(e.requests.map(r=>[r.method,r.url]),[['GET',taskUrl()],['PATCH',`${projectUrl}/canvas`]]);
    const saved=e.server();assert.equal(saved.nodes[0].title,'unsaved conversation title');
    assert.equal(saved.nodes.find(n=>n.id==='other').text,'unsaved sibling');
    assert.deepEqual(saved.nodes[0].pendingActionResumes,[],'later autosave cannot revive the consumed intent');
    assert.ok(saved.nodes.some(n=>n.id==='remote-addition'));
});

test('a conflicting message text or explicit user_edit does not acknowledge the old claim',async()=>{
    for(const mismatch of [{text:'other text'},{user_edit:false}]){
        const initial=journal(),e=editor(initial);
        const conflicting=formal(mismatch);
        e.setServer(registered(initial,conflicting));
        await e.replay();await e.replay();
        assertReads(e,[taskUrl()]);
        assert.equal(e.ctx.__fcPendingActionResumes.size,1);
        assert.ok(e.harness.toasts.includes('smart.canvasConflict'));
        assert.deepEqual(fromContext(e.node().messages),[conflicting],'display the canonical history, not a fabricated receipt');
    }
});

test('a formal pending receipt reconciles and polls to completion using only the original GET key',async()=>{
    const initial=journal(),e=editor(initial);
    e.setServer(registered(initial,formal({status:'pending'})));
    await e.replay();
    assertReads(e,[taskUrl(),taskUrl()]);
    assert.equal(e.ctx.__fcPendingActionResumes.size,0);assert.equal(e.ctx.conversationTaskPolls.size,1);
    assert.equal(e.node().userTouchedAt,500);assert.deepEqual(fromContext(e.node().pendingActionResumes),[]);
    const completed=e.server();completed.revision=7;completed.nodes[0].messages[0].status='completed';e.setServer(completed);
    e.harness.advanceTimers();await tick();
    assertReads(e,[taskUrl(),taskUrl(),taskUrl()]);
    assert.equal(e.ctx.conversationTaskPolls.size,0);
    assert.equal(e.node().messages[0].status,'completed');
    assert.equal(e.node().userTouchedAt,500);
});

test('local-only history without an outbox is not polled, submitted, or discarded',async()=>{
    const initial=seed();initial.nodes[0].messages=[bubble('orphan')];
    const e=editor(initial);e.ctx.resumeConversationTasks();await e.replay();
    assertReads(e,[]);
    assert.equal(e.ctx.conversationTaskPolls.size,0);
    assert.deepEqual(fromContext(e.node().messages),initial.nodes[0].messages);
    assert.equal(e.node().userTouchedAt,null);
});

test('concurrent recovery passes share one GET claim, and deletion invalidates the in-flight lookup',async()=>{
    const initial=journal(),e=editor(initial);
    let release;
    e.onGet=async url=>{await new Promise(resolve=>{release=resolve;});return e.get(url);};
    const replaying=e.replay();assert.equal(typeof release,'function');
    await Promise.all([e.replay(),e.replay()]);
    assertReads(e,[taskUrl()]);
    evaluate(e.ctx,'canvasState.nodes=canvasState.nodes.filter(n=>n.id!=="conversation")');
    e.setServer(registered(initial));release();await replaying;
    assertReads(e,[taskUrl()]);
    assert.equal(e.node(),undefined,'late receipt must not resurrect the deleted target');
    assert.ok(e.harness.toasts.includes('smart.canvasConflict'));
});

test('transient GET errors retry the original key without touching the durable retry budget',async()=>{
    const initial=journal(outbox(v1(),{replayAttempts:5})),e=editor(initial);
    evaluate(e.ctx,'Date.now=()=>1000');
    e.onGet=url=>e.response({detail:`temporarily unavailable: ${url}`},503);
    await e.replay();
    assertReads(e,[taskUrl(),projectUrl]);
    assertRetained(e,initial);
    evaluate(e.ctx,'Date.now=()=>1000000');
    e.harness.advanceTimers();await tick();
    assertReads(e,[taskUrl(),projectUrl,taskUrl(),projectUrl]);
    assertRetained(e,initial);
    assert.deepEqual(e.server(),initial);
});

test('reload retains independent claims for reused action IDs on different nodes',async()=>{
    const initial=journal();
    initial.nodes[1]={id:'other',type:'smart-conversation',messages:[bubble('other-key')],userTouchedAt:null,
        pendingActionResumes:[outbox(v1({nodeId:'other',requestId:'other-key'}))]};
    const e=editor(initial);
    const server=registered(initial);e.setServer(server);
    await e.replay();
    assertReads(e,[taskUrl(),taskUrl('other','other-key'),projectUrl]);
    assert.equal(e.ctx.__fcPendingActionResumes.size,1);
    assert.equal([...e.ctx.__fcPendingActionResumes.values()][0].nodeId,'other');
    assert.deepEqual(fromContext(e.node('other').pendingActionResumes),initial.nodes[1].pendingActionResumes);
    assert.deepEqual(fromContext(e.node().pendingActionResumes),[]);
});

test('v1 locator, version and boolean mismatches are visible conflicts before any transport',async()=>{
    for(const changed of [{projectId:'different-project'},{nodeId:'different-node'},{conversationProtocol:2},{humanIntent:'true'}]){
        const initial=journal(outbox(v1(changed))),e=editor(initial);
        await e.replay();
        assertReads(e,[]);
        assertRetained(e,initial);
        assert.ok(e.harness.toasts.includes('smart.canvasConflict'));
    }
});

test('video, local-edit and photoshop still clear and save before replay, then write back failures',async()=>{
    for(const kind of ['video','local-edit','photoshop']){
        const e=editor();let atSend;
        e.ctx.queueSmartPendingActionResume(kind,'other',async()=>{
            atSend={saved:e.server().nodes[1].pendingActionResumes,claims:e.ctx.__fcPendingActionResumes.size};
            throw new Error('external request failed');
        },{fixture:kind});
        await e.replay();
        assert.deepEqual(atSend,{saved:[],claims:0},JSON.stringify({kind,requests:e.requests,toasts:e.harness.toasts}));
        assert.equal(e.ctx.__fcPendingActionResumes.size,1);
        assert.equal(e.node('other').pendingActionResumes[0].replayAttempts,1);
        assert.equal(e.harness.dispatchedEvents.filter(event=>event.type==='pagehide').length,1);
    }
});

test('registered task polling is scoped to project and node, not just a reused request_id',async()=>{
    const initial=seed();initial.nodes[1]={id:'other',type:'smart-conversation',messages:[]};
    initial.nodes.forEach((node,index)=>{node.messages=[formal({id:`formal-${index}`,status:'pending'})];});
    const e=editor(initial);e.onGet=()=>e.response({status:'pending'});
    e.ctx.resumeConversationTasks();await tick();
    assertReads(e,[taskUrl(),taskUrl('other')]);
    assert.equal(e.ctx.conversationTaskPolls.size,2);
});

test('legacy card displays messages, statuses, direct media references and collapse, without any submission UI',()=>{
    const initial=journal();
    initial.nodes[0].messages.push({id:'assistant-1',role:'assistant',status:'failed',text:'<prior reply>'});
    initial.nodes.push({id:'media',type:'smart-image',images:[
        {asset_id:'image',kind:'image',name:'picture.png'},{asset_id:'video',kind:'video',name:'movie.mp4'},
        {asset_id:'audio',kind:'audio',name:'voice.mp3'},{asset_id:'image',kind:'image',name:'duplicate.png'},
        {asset_id:'text',kind:'text',name:'excluded.txt'},{kind:'image',name:'unregistered.png'},
    ]},{id:'indirect',type:'smart-image',images:[{asset_id:'indirect',name:'indirect.png'}]});
    initial.connections=[{from:'media',to:'conversation',kind:'input'},{from:'media',to:'conversation',kind:'flow'},
        {from:'indirect',to:'media',kind:'input'}];
    const e=editor(initial);
    const references=fromContext(e.ctx.conversationConnectedReferences(e.node()));
    assert.deepEqual(references,[{asset_id:'image',kind:'image',name:'picture.png'},
        {asset_id:'video',kind:'video',name:'movie.mp4'},{asset_id:'audio',kind:'audio',name:'voice.mp3'}]);
    for(const collapsed of [false,true]){
        e.node().conversation_collapsed=collapsed;
        const html=e.ctx.conversationNodeBodyHtml(e.node());
        assert.match(html,/smart\.conversationRetired/);
        assert.match(html,/data-conversation-dock[^>]*>smart\.openConversationDock/);
        assert.match(html,new RegExp(`data-conversation-collapse aria-expanded="${!collapsed}"`));
        assert.equal(/data-conversation-panel\s+hidden/.test(html),collapsed);
        assert.match(html,/data-role="user" data-status="pending"/);
        assert.match(html,/data-role="assistant" data-status="failed"/);
        assert.match(html,/data-message-id="assistant-1" tabindex="0" aria-haspopup="menu"[^>]*>&lt;prior reply&gt;/);
        for(const {kind} of references) assert.match(html,new RegExp(`data-reference-kind="${kind}"`));
        assert.doesNotMatch(html,/textarea|contenteditable|data-conversation-(?:input|send|retry)|duplicate\.png|indirect\.png/i);
    }
});
