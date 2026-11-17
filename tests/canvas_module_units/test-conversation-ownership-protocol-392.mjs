// #392 A2 2b-2c: real send/save/receive/queue/rebuilder seams in the existing VM.
// Only browser rendering and HTTP/storage I/O are replaced; no protocol implementation is mocked.
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {createCanvasContext, loadModules, evaluate, fromContext, JS_DIR} from './harness.mjs';

const copy = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
async function drain(){ for(let i=0;i<12;i++) await tick(); }
const source = readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
function declarations(text){
    const lines=text.split(/\r?\n/), result=[];
    for(let i=0;i<lines.length;i++){
        if(!/^(?:async )?function \w+\(/.test(lines[i])) continue;
        const start=i;
        if(!/}\s*$/.test(lines[i])) while(++i<lines.length && !/^}/.test(lines[i])){}
        assert.ok(i<lines.length,`unterminated ${lines[start]}`);
        result.push(lines.slice(start,i+1).join('\n'));
    }
    return result.join('\n');
}
const seed = () => ({id:'protocol-392',project_id:'protocol-392',format:'firefly-canvas-v1',revision:4,updated_at:400,title:'Conversation',icon:'sparkles',history:[],connections:[],nodes:[
    {id:'conversation',type:'smart-conversation',messages:[],createdBy:'agent',agentNodeId:'creator',userTouchedAt:null,promptEditedByUser:false},
    {id:'other',type:'smart-prompt',text:'before',userTouchedAt:100,promptEditedByUser:false},
]});
function editor(initial=seed()){
    const {ctx,harness}=createCanvasContext();
    loadModules(ctx,['canvas-node-types.js','canvas-core.js','canvas-state.js','project-canvas-mutations.js','canvas-connections.js','canvas-media-history.js','canvas-undo.js','canvas-generation-tasks.js','canvas-agent-tui.js']);
    vm.runInContext(source.match(/^const SMART_ONLINE_ENGINES = .+;$/m)[0],ctx);
    vm.runInContext(declarations(source),ctx);
    Object.assign(ctx,{
        AbortController,canvasId:initial.id,sourceProjectId:initial.project_id,canvasUsesConnections:true,canvasLoadReady:true,
        persistedCoreSnapshot:null,persistedDurableSnapshot:null,smartClientId:'conversation-unit',contentDirty:false,
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
    vm.runInContext(source.slice(source.indexOf('registerSmartPendingActionPersister(async'),source.indexOf('// #377：项目在另一个标签页')),ctx);
    let server=copy(initial);
    const requests=[];
    const response=(data, status=200)=>({ok:status>=200&&status<300,status,json:async()=>copy(data),text:async()=>JSON.stringify(data)});
    const e={ctx,harness,requests,response,
        live:()=>evaluate(ctx,'canvas'),node:(id='conversation')=>evaluate(ctx,`canvasState.nodes.find(n=>n.id===${JSON.stringify(id)})`),
        server:()=>copy(server),setServer(next){server=copy(next);},
        receive(next){return ctx.applyConversationCanvasResponse(copy(next));},
        async send(human=true,text='hello'){const input={value:text},button={disabled:false};await ctx.sendConversationMessage(e.node(),input,button,{human});await drain();return {input,button};},
        async replay(){await ctx.resumeSmartPendingActionResumes(e.live().nodes);await drain();},
        posts:()=>requests.filter(r=>r.options.method==='POST'&&r.url.endsWith('/messages')),
        onPost:null,onPatch:null,onGet:null,
    };
    ctx.fetch=async(url,options={})=>{
        const body=options.body?JSON.parse(options.body):null;
        requests.push({url,options,body});
        if(options.method==='PATCH'){
            if(e.onPatch) return e.onPatch(body);
            for(const mutation of body.mutations){
                if(mutation.op==='upsert_node'){
                    const index=server.nodes.findIndex(n=>n.id===mutation.node.id);
                    if(index<0) server.nodes.push(copy(mutation.node));else server.nodes[index]=copy(mutation.node);
                }
            }
            server.revision++;server.updated_at++;
            return response({saved:true,canvas:server,revision:server.revision,updated_at:server.updated_at});
        }
        if(options.method==='POST'&&url.endsWith('/messages')){
            if(e.onPost) return e.onPost(body);
            const node=server.nodes.find(n=>n.id==='conversation');
            node.messages=[{id:'server-user',role:'user',request_id:body.request_id,text:body.text,status:'completed',created_at:500,user_edit:body.user_edit}];
            node.pendingActionResumes=node.pendingActionResumes.filter(r=>r.kind!=='conversation'||r.payload.requestId!==body.request_id);
            if(body.user_edit===true) node.userTouchedAt=500;
            server.revision++;server.updated_at++;
            return response({saved:true,canvas:server,message:node.messages[0],status:'completed'});
        }
        if(options.method==='POST'&&url.endsWith('/flush')) return response({saved:true,revision:server.revision});
        if(e.onGet) return e.onGet(url);
        // This is the real GET route; /canvas only supports PATCH in production.
        if(url==='/api/project-library/protocol-392') return response({canvas:server});
        const task=url.match(/^\/api\/project-library\/protocol-392\/conversation\/([^/]+)\/messages\/([^/]+)$/);
        if(!task) return response({detail:'unsupported fixture route'},405);
        const requestId=decodeURIComponent(task[2]);
        const message=server.nodes.find(n=>n.id===decodeURIComponent(task[1]))?.messages?.find(m=>m.request_id===requestId&&!String(m.id).startsWith('local-'));
        return message?response({canvas:server,message,status:message.status}):response({detail:'not registered'},404);
    };
    return e;
}

test('internal live-state and enqueue contracts fail loudly instead of fabricating an identity',()=>{
    const e=editor();
    assert.throws(()=>e.ctx.queueSmartPendingActionResume('conversation','conversation',null),/projectId/);
    e.ctx.canvasState.nodes=undefined;
    assert.throws(()=>e.ctx.conversationRequestId(),/iterable/);
});

test('malformed boundary JSON is surfaced instead of silently becoming an empty receipt',async()=>{
    for(const phase of ['status','project','post']){
        const e=editor();
        const invalid={ok:true,status:200,json:async()=>{throw new SyntaxError(`${phase}-json-invalid`);}};
        if(phase==='post') e.onPost=()=>invalid;
        else e.onGet=url=>url==='/api/project-library/protocol-392'
            ? (phase==='project'?invalid:e.response({canvas:e.server()}))
            : (phase==='status'?invalid:e.response({detail:'not registered'},404));
        await e.send();
        assert.equal(e.posts().length,phase==='post'?1:0,phase);
        assert.ok(e.harness.toasts.some(message=>message.includes(`${phase}-json-invalid`)),JSON.stringify({phase,toasts:e.harness.toasts}));
        assert.equal(e.node().userTouchedAt,null);
        assert.equal(e.node().pendingActionResumes.length,1,'invalid JSON cannot acknowledge or erase the original intent');
    }
});

test('unsupported bare-canvas project response is rejected instead of silently selecting a compatibility path',async()=>{
    const e=editor();
    e.onGet=url=>url==='/api/project-library/protocol-392'
        ? e.response(e.server()):e.response({detail:'not registered'},404);
    await e.send();
    assert.equal(e.posts().length,0);
    assert.equal(e.node().userTouchedAt,null);
    assert.equal(e.node().pendingActionResumes.length,1);
    assert.ok(e.harness.toasts.length>0);
});

test('new human send saves its original protocol outbox before POST without a frontend ownership stamp',async()=>{
    const e=editor();
    let observed;
    e.onPost=body=>{
        observed={saved:e.server().nodes[0],body,claims:e.ctx.__fcPendingActionResumes.size};
        return e.response({detail:'pending verification'},503);
    };
    await e.send();
    assert.equal(e.posts().length,1,JSON.stringify({toasts:e.harness.toasts,requests:e.requests.map(r=>r.url)}));
    assert.equal(observed.saved.userTouchedAt,null,'saving the local intent must not stamp T');
    assert.equal(observed.saved.promptEditedByUser,false);
    assert.equal(observed.saved.pendingActionResumes.length,1,'normal first send also needs a durable outbox');
    assert.deepEqual(observed.saved.pendingActionResumes[0].payload,{
        conversationProtocol:1,projectId:'protocol-392',nodeId:'conversation',requestId:observed.body.request_id,text:'hello',humanIntent:true,
    });
    assert.equal(observed.body.user_edit,true);
    assert.equal(observed.claims,1,'claim stays alive until formal acknowledgement');
    assert.equal(e.node().userTouchedAt,null);
});

test('lost POST acknowledgement queries the original key and never writes back a consumed outbox',async()=>{
    const e=editor();
    let original;
    e.onPost=body=>{
        original=copy(body);
        const registered=e.server();registered.revision=6;registered.updated_at=600;
        registered.nodes[0].messages=[{id:'receipt',role:'user',request_id:body.request_id,text:'hello',status:'completed',created_at:550,user_edit:true}];
        registered.nodes[0].pendingActionResumes=[];registered.nodes[0].userTouchedAt=550;
        e.setServer(registered);
        throw new Error('acknowledgement lost');
    };
    await e.send();
    assert.equal(e.posts().length,1);
    assert.equal(e.ctx.__fcPendingActionResumes.size,0,'formal GET receipt confirms the claimed entry');
    assert.equal(e.node().pendingActionResumes.length,0);
    assert.equal(e.node().messages[0].id,'receipt');
    assert.equal(e.node().userTouchedAt,550);
    assert.equal(e.harness.dispatchedEvents.some(event=>event.type==='pagehide'),false);
    assert.ok(e.requests.some(r=>!r.options.method&&r.url.endsWith('/'+original.request_id)));
    await e.replay();assert.equal(e.posts().length,1);
});

test('failed initial save retains the original outbox and resumes once a later real save settles',async()=>{
    const e=editor();e.onPatch=()=>{throw new Error('storage unavailable');};
    await e.send();assert.equal(e.posts().length,0);
    const queued=fromContext(e.node().pendingActionResumes[0]);
    assert.equal(queued.payload.humanIntent,true);assert.equal(e.node().userTouchedAt,null);
    e.onPatch=null;assert.equal(await e.ctx.saveCanvas(),true,JSON.stringify(e.harness.toasts));await drain();
    assert.equal(e.posts().length,1);assert.equal(e.posts()[0].body.request_id,queued.payload.requestId);
    assert.equal(e.posts()[0].body.user_edit,true);assert.equal(e.ctx.__fcPendingActionResumes.size,0);
});

test('reload consults a formal legacy receipt ahead of its local bubble and does not POST again',async()=>{
    const initial=seed();
    const payload={conversationProtocol:1,projectId:'protocol-392',nodeId:'conversation',requestId:'reload-key',text:'hello',humanIntent:true};
    initial.nodes[0].pendingActionResumes=[{actionId:'persisted-action',kind:'conversation',payload,enqueuedAt:100}];
    initial.nodes[0].messages=[
        {id:'local-reload-key',role:'user',request_id:'reload-key',text:'hello',status:'pending',created_at:100},
        {id:'old-formal',role:'user',request_id:'reload-key',text:'hello',status:'completed',created_at:200},
    ];
    const e=editor(initial);
    const server=copy(initial);server.revision=5;server.nodes[0].pendingActionResumes=[];server.nodes[0].messages=server.nodes[0].messages.slice(1);e.setServer(server);
    await e.replay();
    assert.equal(e.posts().length,0,'the server already registered this old message without user_edit');
    assert.equal(e.ctx.__fcPendingActionResumes.size,0);
    assert.equal(e.node().pendingActionResumes.length,0);
    assert.deepEqual(e.node().messages.map(m=>m.id),['old-formal']);
    assert.equal(e.node().userTouchedAt,null,'legacy receipts never get a frontend T backfill');
});

test('canonical legacy records without id or created_at are acknowledged without migration, POST or T',async()=>{
    for(const fields of [{},{id:'old-formal'}]){
        const initial=seed();
        const payload={requestId:'legacy-receipt',text:'hello',humanIntent:true};
        initial.nodes[0].pendingActionResumes=[{actionId:'old-action',kind:'conversation',payload,enqueuedAt:100}];
        initial.nodes[0].messages=[{id:'local-legacy-receipt',role:'user',request_id:'legacy-receipt',text:'hello',status:'pending',created_at:100}];
        const e=editor(initial),server=copy(initial);
        const message={role:'user',request_id:'legacy-receipt',text:'hello',status:'completed',...fields};
        server.revision=5;server.nodes[0].messages=[message];server.nodes[0].pendingActionResumes=[];e.setServer(server);
        await e.replay();
        assert.equal(e.ctx.__fcPendingActionResumes.size,0,'canonical legacy history is already registered');
        assert.equal(e.posts().length,0);assert.equal(e.requests.some(r=>r.options.method==='PATCH'),false);
        assert.deepEqual(fromContext(e.node().messages),[message]);
        assert.equal(e.node().pendingActionResumes.length,0);assert.equal(e.node().userTouchedAt,null);
    }
});

test('legacy journal is queried then durably upgraded, preserving only an explicit original true',async()=>{
    for(const flag of [undefined,false,true]){
        const initial=seed();
        const payload={requestId:'legacy-key',text:'hello',...(flag===undefined?{}:{humanIntent:flag})};
        initial.nodes[0].pendingActionResumes=[{actionId:'legacy-action',kind:'conversation',payload,enqueuedAt:100}];
        initial.nodes[0].messages=[{id:'local-legacy-key',role:'user',request_id:'legacy-key',text:'hello',status:'pending',created_at:100}];
        const e=editor(initial);let atPost;
        e.onPost=body=>{atPost={body,saved:e.server().nodes[0],requests:e.requests.map(r=>({url:r.url,method:r.options.method}))};return e.response({},503);};
        await e.replay();
        assert.equal(e.posts().length,1,JSON.stringify(e.harness.toasts));
        assert.deepEqual(atPost.body,{text:'hello',request_id:'legacy-key',user_edit:flag===true});
        assert.deepEqual(atPost.saved.pendingActionResumes[0].payload,{
            conversationProtocol:1,projectId:'protocol-392',nodeId:'conversation',requestId:'legacy-key',text:'hello',humanIntent:flag===true,
        });
        assert.equal(atPost.saved.userTouchedAt,null);
        assert.ok(atPost.requests.findIndex(r=>!r.method&&r.url.endsWith('/legacy-key'))<atPost.requests.findIndex(r=>r.method==='PATCH'));
        assert.equal(e.harness.dispatchedEvents.length,0);
    }
});

test('a reloaded session cannot reuse a saved request key when clock and randomness repeat',async()=>{
    const e=editor();
    evaluate(e.ctx,'Math.random=()=>0.25;Date.now=()=>1000');
    const previous=e.ctx.conversationRequestId();
    const initial=seed();initial.nodes[0].messages=[{id:'previous-formal',role:'user',request_id:previous,text:'earlier',status:'completed',created_at:900}];
    const reloaded=editor(initial);
    evaluate(reloaded.ctx,'Math.random=()=>0.25;Date.now=()=>1000');
    await reloaded.send();
    assert.equal(reloaded.posts().length,1);
    assert.notEqual(reloaded.posts()[0].body.request_id,previous,'a new click must not inherit the previous receipt identity');
});

test('a stale formal ACK confirms only its own entry while latest shadow and unrelated dirty edits survive',async()=>{
    const e=editor();let release,receipt;
    e.onPost=async body=>{
        receipt=e.server();receipt.revision=6;receipt.updated_at=600;
        receipt.nodes[0].pendingActionResumes=[];receipt.nodes[0].userTouchedAt=500;
        receipt.nodes[0].messages=[{id:'late-receipt',role:'user',request_id:body.request_id,text:'hello',status:'completed',created_at:500,user_edit:true}];
        await new Promise(resolve=>{release=resolve;});
        return e.response({canvas:receipt,message:receipt.nodes[0].messages[0],status:'completed'});
    };
    await e.send();assert.equal(typeof release,'function');
    e.node('other').text='unsaved sibling';e.node('other').userTouchedAt=777;
    e.node().title='unsaved conversation title';e.ctx.scheduleSave();
    const latest=copy(receipt);latest.revision=9;latest.updated_at=900;latest.nodes[0].userTouchedAt=900;
    latest.nodes.push({id:'remote-addition',type:'smart-prompt',text:'keep remote'});
    e.setServer(latest);e.receive(latest);release();await drain();
    assert.equal(e.ctx.__fcPendingActionResumes.size,0);
    assert.equal(e.live().revision,9);
    assert.equal(e.node().userTouchedAt,900);
    assert.equal(e.node('other').text,'unsaved sibling');assert.equal(e.node('other').userTouchedAt,777);
    assert.equal(e.node().title,'unsaved conversation title');
    assert.equal(e.node().pendingActionResumes.length,0);
    assert.equal(e.node().messages.some(m=>String(m.id).startsWith('local-')),false);
    assert.equal(await e.ctx.saveCanvas(),true,JSON.stringify(e.harness.toasts));
    const saved=e.server();assert.equal(saved.nodes[0].title,'unsaved conversation title');
    assert.equal(saved.nodes.find(n=>n.id==='other').text,'unsaved sibling');
    assert.equal(saved.nodes[0].pendingActionResumes.length,0,'later autosave cannot revive the consumed intent');
    assert.ok(saved.nodes.some(n=>n.id==='remote-addition'));
});

test('local-only saved bubbles without an outbox are not registered tasks and are not polled or sent',async()=>{
    const initial=seed();initial.nodes[0].messages=[{id:'local-orphan',role:'user',request_id:'orphan',text:'unknown intent',status:'pending',created_at:100}];
    const e=editor(initial);e.ctx.resumeConversationTasks();await e.replay();
    assert.equal(e.requests.length,0,'role=user and a request_id on a local bubble are not registration proof');
    assert.equal(e.ctx.conversationTaskPolls.size,0);
    assert.equal(e.node().userTouchedAt,null);
});

test('local-only recovery does not deadlock a later explicit human send or guess the old intent',async()=>{
    const initial=seed();
    initial.nodes[0].messages=[{id:'local-orphan',role:'user',request_id:'orphan',text:'unknown old intent',status:'pending',created_at:100}];
    const e=editor(initial);e.ctx.resumeConversationTasks();await e.replay();
    const html=e.ctx.conversationNodeBodyHtml(e.node());
    const sendButton=html.match(/<button[^>]*data-conversation-send[^>]*>/);
    assert.ok(sendButton,'conversation renderer must provide its send button');
    assert.doesNotMatch(sendButton[0],/ disabled(?:[=\s>])/,'an unrecoverable old bubble is not an active request');
    assert.equal(e.requests.length,0);assert.equal(e.node().userTouchedAt,null);
    let saved;
    e.onPost=()=>{saved=e.server().nodes[0];return e.response({},503);};
    await e.send(true,'new explicit intent');
    assert.equal(e.posts().length,1);
    assert.equal(e.posts()[0].body.text,'new explicit intent');
    assert.notEqual(e.posts()[0].body.request_id,'orphan');
    assert.equal(e.posts()[0].body.user_edit,true);
    assert.deepEqual(fromContext(e.node().messages.find(message=>message.request_id==='orphan')),initial.nodes[0].messages[0],'old bubble remains data, not inferred or registered');
    assert.equal(saved.pendingActionResumes.length,1);
    assert.equal(saved.pendingActionResumes[0].payload.requestId,e.posts()[0].body.request_id);
    assert.equal(e.node().userTouchedAt,null,'a saved new intent without registration still has no T');
});

test('formal pending requests and durable old or new outboxes still gate both UI and direct sends',async()=>{
    const local={id:'local-old',role:'user',request_id:'old',text:'original',status:'pending'};
    const marker=payload=>({actionId:'original-action',kind:'conversation',payload,enqueuedAt:100});
    for(const state of [
        {messages:[{...local,id:'formal-old'}]},
        {messages:[local],pendingActionResumes:[marker({requestId:'old',text:'original'})]},
        ...[false,true].map(humanIntent=>({messages:[local],pendingActionResumes:[marker({conversationProtocol:1,projectId:'protocol-392',nodeId:'conversation',requestId:'old',text:'original',humanIntent})]})),
        {messages:[],pendingActionResumes:[marker({requestId:'old',text:'original'})]},
    ]){
        const initial=seed();Object.assign(initial.nodes[0],copy(state));const e=editor(initial);
        const html=e.ctx.conversationNodeBodyHtml(e.node());
        const sendButton=html.match(/<button[^>]*data-conversation-send[^>]*>/);
        assert.ok(sendButton,'conversation renderer must provide its send button');
        assert.match(sendButton[0],/ disabled(?:[=\s>])/);
        await e.send();assert.equal(e.requests.length,0,'existing recoverable work must not acquire another identity');
        assert.equal(e.node().userTouchedAt,null);
    }
});

test('a save acknowledgement and merged local marker alone cannot authorize POST without a pure server outbox',async()=>{
    const e=editor();
    e.onPatch=()=>e.response({saved:true,revision:5,updated_at:500});
    const fresh=seed();fresh.revision=6;fresh.updated_at=600;e.setServer(fresh);
    await e.send();
    assert.equal(e.posts().length,0,'GET server state has neither the original outbox nor a formal receipt');
    assert.equal(e.ctx.__fcPendingActionResumes.size,1,'uncertain intent remains visible, never silently dropped');
    assert.ok(e.harness.toasts.some(message=>message.includes('Conflict')||message.includes('conflict')));
    assert.equal(e.node().userTouchedAt,null);
});

test('reload preserves the original true or automatic false and the same request key after a saved-but-unsent intent',async()=>{
    for(const human of [true,false]){
        const first=editor();first.onGet=()=>first.response({},503);
        await first.send(human);
        assert.equal(first.posts().length,0);
        const saved=first.server();const mark=saved.nodes[0].pendingActionResumes[0];
        assert.equal(saved.nodes[0].userTouchedAt,null);
        assert.equal(mark.payload.humanIntent,human);
        const reload=editor(saved);await reload.replay();
        assert.equal(reload.posts().length,1,JSON.stringify(reload.harness.toasts));
        assert.deepEqual(reload.posts()[0].body,{text:'hello',request_id:mark.payload.requestId,user_edit:human});
        assert.equal(reload.node().userTouchedAt,human?500:null);
        assert.equal(reload.ctx.__fcPendingActionResumes.size,0);
    }
});

test('deleting the node while preflight awaits server evidence prevents the conversation POST',async()=>{
    const e=editor();let removed=false;
    e.onGet=url=>{
        if(url!=='/api/project-library/protocol-392'){
            evaluate(e.ctx,'canvasState.nodes=canvasState.nodes.filter(n=>n.id!=="conversation")');e.ctx.scheduleSave();removed=true;
            return e.response({},404);
        }
        return e.response({canvas:e.server()});
    };
    await e.send();
    assert.equal(removed,true);
    assert.equal(e.posts().length,0,'liveness is rechecked after all preparation awaits');
    assert.equal(e.node(),undefined);
});

test('empty input, stale deleted-node handles and duplicate clicks never acquire another send; concurrent replays share one claim',async()=>{
    const e=editor();await e.send(true,'  ');assert.equal(e.requests.length,0);
    const gone={id:'gone',type:'smart-conversation'};
    await e.ctx.sendConversationMessage(gone,{value:'hello'},{disabled:false},{human:true});assert.equal(e.requests.length,0);
    let release;
    e.onPost=async()=>{await new Promise(resolve=>{release=resolve;});return e.response({},503);};
    await Promise.all([e.send(),e.send()]);
    assert.equal(e.posts().length,1);
    await Promise.all([e.replay(),e.replay(),e.replay()]);
    await e.send();assert.equal(e.posts().length,1);
    assert.equal(e.ctx.__fcPendingActionResumes.size,1);
    assert.equal(e.server().nodes[0].pendingActionResumes.length,1);
    release();await drain();assert.equal(e.posts().length,1);
});

test('conversation retry exhaustion retains the only durable intent and retries only the original key without pagehide write-back',async()=>{
    const initial=seed();initial.nodes[0].pendingActionResumes=[{
        actionId:'exhausted-legacy-budget',kind:'conversation',enqueuedAt:100,replayAttempts:5,
        payload:{conversationProtocol:1,projectId:'protocol-392',nodeId:'conversation',requestId:'retained-key',text:'hello',humanIntent:true},
    }];
    const e=editor(initial);evaluate(e.ctx,'Date.now=()=>1000');e.onPost=()=>e.response({},503);
    await e.replay();
    assert.equal(e.posts().length,1,'the generic budget cannot discard a conversation outbox');
    assert.equal(e.ctx.__fcPendingActionResumes.size,1);
    assert.deepEqual(e.server().nodes[0].pendingActionResumes,initial.nodes[0].pendingActionResumes);
    evaluate(e.ctx,'Date.now=()=>1000000');e.harness.advanceTimers();await drain();
    assert.equal(e.posts().length,2);
    assert.deepEqual(e.posts()[0].body,e.posts()[1].body);
    assert.equal(e.requests.filter(r=>r.options.method==='PATCH').length,0,'retry state is runtime-only');
    assert.equal(e.harness.dispatchedEvents.length,0);
    assert.equal(e.node().userTouchedAt,null);
});

test('reload keeps independent conversation claims even when old sessions reused an actionId on different nodes',async()=>{
    const initial=seed();initial.nodes[1]={id:'other',type:'smart-conversation',messages:[],userTouchedAt:null};
    initial.nodes.forEach((node,index)=>{node.pendingActionResumes=[{actionId:'action-conversation-1',kind:'conversation',enqueuedAt:100,
        payload:{conversationProtocol:1,projectId:'protocol-392',nodeId:node.id,requestId:`key-${index}`,text:'hello',humanIntent:true}}];});
    const e=editor(initial);e.onPost=()=>e.response({},503);
    e.onGet=url=>url==='/api/project-library/protocol-392'?e.response({canvas:e.server()}):e.response({},404);
    await e.replay();
    assert.deepEqual(e.posts().map(r=>r.body.request_id).sort(),['key-0','key-1']);
    assert.equal(e.ctx.__fcPendingActionResumes.size,2);
    assert.equal(e.server().nodes.every(n=>n.pendingActionResumes.length===1),true);
});

test('a contradictory response message cannot acknowledge a claim while canonical GET is unavailable',async()=>{
    const e=editor();let release,latest;
    e.onPost=async body=>{
        const stale=e.server();latest=copy(stale);latest.revision=9;latest.updated_at=900;
        const message={id:'explicit-formal',role:'user',request_id:body.request_id,text:'hello',status:'completed',created_at:500,user_edit:true};
        latest.nodes[0].messages=[message];latest.nodes[0].pendingActionResumes=[];latest.nodes[0].userTouchedAt=900;
        await new Promise(resolve=>{release=resolve;});
        return e.response({canvas:stale,message,status:'completed'});
    };
    await e.send();e.receive(latest);e.setServer(latest);
    e.onGet=()=>e.response({},503);release();await drain();
    assert.equal(e.ctx.__fcPendingActionResumes.size,1,'a message beside a stale canvas is not a canonical receipt; retain only the runtime claim');
    assert.equal(e.node().userTouchedAt,900);assert.equal(e.live().revision,9);
    assert.equal(e.node().pendingActionResumes.length,0);
    assert.equal(e.node().messages[0].id,'explicit-formal');
});

test('a user-shaped response without the existing server message identity is not a formal registration receipt',async()=>{
    const e=editor();e.onPost=body=>e.response({message:{role:'user',request_id:body.request_id,text:'hello',status:'completed'}});
    await e.send();
    assert.equal(e.ctx.__fcPendingActionResumes.size,1,'missing id/created_at cannot acknowledge an outbox');
    assert.equal(e.server().nodes[0].pendingActionResumes.length,1);
    assert.equal(e.node().userTouchedAt,null);
});

test('post-registration 429 confirms by the original key and attaches polling without another POST or T',async()=>{
    const e=editor();let key;
    e.onPost=body=>{
        key=body.request_id;const registered=e.server();registered.revision=6;registered.updated_at=600;
        registered.nodes[0].messages=[{id:'registered-before-429',role:'user',request_id:key,text:'hello',status:'pending',created_at:500,user_edit:true}];
        registered.nodes[0].pendingActionResumes=[];registered.nodes[0].userTouchedAt=500;e.setServer(registered);
        return e.response({detail:'admission failed after registration'},429);
    };
    await e.send();
    assert.equal(e.ctx.__fcPendingActionResumes.size,0);assert.equal(e.ctx.conversationTaskPolls.size,1);
    assert.equal(e.node().userTouchedAt,500);assert.equal(e.node().pendingActionResumes.length,0);
    const completed=e.server();completed.revision=7;completed.nodes[0].messages[0].status='completed';e.setServer(completed);
    e.harness.advanceTimers();await drain();
    assert.equal(e.ctx.conversationTaskPolls.size,0);
    assert.equal(e.posts().length,1);
    assert.equal(e.node().userTouchedAt,500);
    assert.ok(e.requests.filter(r=>!r.options.method&&r.url.endsWith('/'+key)).length>=2);
});

test('v1 locator, version and boolean mismatches are visible conflicts, never migrated into the current project',async()=>{
    for(const changed of [{projectId:'different-project'},{nodeId:'different-node'},{conversationProtocol:2},{humanIntent:'true'}]){
        const initial=seed();initial.nodes[0].pendingActionResumes=[{actionId:'invalid-v1',kind:'conversation',enqueuedAt:100,
            payload:{conversationProtocol:1,projectId:'protocol-392',nodeId:'conversation',requestId:'invalid-key',text:'hello',humanIntent:true,...changed}}];
        const e=editor(initial);await e.replay();
        assert.equal(e.requests.length,0,'invalid original identity is rejected before any transport or save');
        assert.deepEqual(fromContext(e.node().pendingActionResumes),initial.nodes[0].pendingActionResumes);
        assert.equal(e.ctx.__fcPendingActionResumes.size,1);
        assert.ok(e.harness.toasts.some(message=>message.includes('Conflict')||message.includes('conflict')));
        assert.equal(e.node().userTouchedAt,null);
    }
});

test('video, local-edit and photoshop keep the real clear-on-send save and failure write-back/pagehide behavior',async()=>{
    for(const kind of ['video','local-edit','photoshop']){
        const e=editor();let atSend;
        e.ctx.queueSmartPendingActionResume(kind,'other',async()=>{
            atSend={saved:e.server().nodes[1].pendingActionResumes,claims:e.ctx.__fcPendingActionResumes.size};
            throw new Error('external request failed');
        },{fixture:kind});
        await e.replay();
        assert.deepEqual(atSend,{saved:[],claims:0},'non-conversation still clears and saves before sending');
        assert.equal(e.ctx.__fcPendingActionResumes.size,1);
        assert.equal(e.node('other').pendingActionResumes[0].replayAttempts,1);
        assert.equal(e.harness.dispatchedEvents.filter(event=>event.type==='pagehide').length,1);
    }
});

test('registered task polling is scoped to project and node, not just a reused request_id',async()=>{
    const initial=seed();initial.nodes[1]={id:'other',type:'smart-conversation',messages:[]};
    initial.nodes.forEach((node,index)=>{node.messages=[{id:`formal-${index}`,role:'user',request_id:'old-shared-key',text:'hello',status:'pending',created_at:100}];});
    const e=editor(initial);e.onGet=()=>e.response({status:'pending'});
    e.ctx.resumeConversationTasks();await drain();
    assert.equal(e.requests.filter(r=>r.url.includes('/conversation/')).length,2);
    assert.equal(e.ctx.conversationTaskPolls.size,2);
    assert.equal(e.posts().length,0);
});

test('a message-only response is not accepted until a canonical GET confirms its original key',async()=>{
    const e=editor();
    e.onPost=body=>{
        const registered=e.server();registered.revision=6;registered.updated_at=600;
        const message={id:'message-only-receipt',role:'user',request_id:body.request_id,text:'hello',status:'completed',created_at:500,user_edit:true};
        registered.nodes[0].messages=[message];registered.nodes[0].pendingActionResumes=[];registered.nodes[0].userTouchedAt=500;e.setServer(registered);
        return e.response({message,status:'completed'});
    };
    await e.send();
    assert.equal(e.ctx.__fcPendingActionResumes.size,0);
    assert.equal(e.node().pendingActionResumes.length,0);
    assert.equal(e.node().messages[0].id,'message-only-receipt');
    assert.equal(e.node().userTouchedAt,500);
    await e.replay();assert.equal(e.posts().length,1);
});
