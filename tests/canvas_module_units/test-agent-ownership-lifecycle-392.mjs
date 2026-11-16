// #392 2a: real receive/save/undo functions; only browser I/O is replaced.
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {createCanvasContext, loadModules, evaluate, fromContext, JS_DIR} from './harness.mjs';

const copy = value => JSON.parse(JSON.stringify(value));
function declarations(file){
    const source = readFileSync(path.join(JS_DIR, file), 'utf8');
    const lines=source.split(/\r?\n/), functions=[];
    for(let index=0;index<lines.length;index++){
        if(!/^(?:async )?function \w+\(/.test(lines[index])) continue;
        const start=index;
        if(!/}\s*$/.test(lines[index])){
            while(++index<lines.length && !/^}/.test(lines[index])){}
            if(index===lines.length) throw new Error(`Unterminated declaration: ${lines[start]}`);
        }
        functions.push(lines.slice(start,index+1).join('\n'));
    }
    return functions.join('\n');
}
const seed = () => ({id:'e2e-tabbit-a2',project_id:'e2e-tabbit-a2',format:'firefly-canvas-v1',revision:4,updated_at:400,title:'Pending test',icon:'sparkles',nodes:[{id:'prompt',type:'smart-prompt',text:'before',createdBy:'agent',agentNodeId:'agent',userTouchedAt:null,promptEditedByUser:false}],connections:[],history:[]});
function editor(initial=seed()){
    const {ctx,harness} = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js','canvas-core.js','project-canvas-mutations.js','canvas-connections.js','canvas-media-history.js','canvas-undo.js']);
    vm.runInContext(readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8').match(/^const SMART_ONLINE_ENGINES = .+;$/m)[0],ctx);
    vm.runInContext(declarations('canvas-generation-cascade.js')+'\n'+declarations('canvas-generation-tasks.js')+'\n'+declarations('canvas-agent-tui.js')+'\n'+declarations('smart-canvas.js'), ctx);
    const timers = harness.timers;
    Object.assign(ctx, {
        canvasState:{nodes:[],selectedId:'prompt',selectedIds:[],selectedImage:{},annotations:[],projectMediaLibrary:{items:[]}},
        canvasId:initial.id,sourceProjectId:initial.project_id,canvasUsesConnections:true,canvasLoadReady:true,
        persistedCoreSnapshot:null,persistedDurableSnapshot:null,smartClientId:'lifecycle-test',
        contentDirty:false,debouncedSaveTimer:null,canvasSyncInFlight:false,canvasProjectGone:false,
        pendingRemoteUpdatedAt:0,pendingAgentMergeFingerprint:'',pendingAgentMergeUndo:false,
        conversationCanvasLastAcceptedRevision:0,smartGenerationNodeRevisions:new Map(),smartNodeRunTokens:new Map(),smartRunTokens:new Map(),
        projectMediaImportCache:new Map(),activeProjectMediaImports:0,projectMediaImportWaiters:[],
        SMART_LOG_PREVIEW_NODE_ID:'__preview__',DEBOUNCED_SAVE_MS:3000,viewport:{x:0,y:0,scale:1},canvasDefaultSmartSettings:{},initialSmartSettings:{},
        smartSaveButton:null,smartHistoryModal:null,assetLibraryOpen:false,
        // Rendering and unrelated background polling are browser I/O, not the
        // receive/save/undo/pending algorithms exercised by this fixture.
        render(){},applyViewport(){},setSmartTitleText(){},scheduleConnectionLayerRefresh(){},
        scheduleSmartHistoryWindowRender(){},resumeSmartPendingTasks(){},resumeSmartPersistencePendingTasks(){},
        resumeSmartPendingActionResumes(){},rebuildMarkedAssetIdsFromNodes(){},updateSmartMarkedBadge(){},
        savePromptDraftForCurrent(){},toast(message){harness.toasts.push(String(message));},
    });
    evaluate(ctx,`canvas=${JSON.stringify(initial)}; canvasState.nodes=canvas.nodes;`);
    ctx.persistedCoreSnapshot=ctx.FireflyProjectCanvasMutations.snapshotFromCanvas(initial);
    ctx.persistedDurableSnapshot=ctx.FireflyProjectCanvasMutations.durableSnapshotFromCanvas(initial);
    return {
        ctx,harness,timers,
        live:()=>evaluate(ctx,'canvas'),
        node:()=>evaluate(ctx,'canvasState.nodes.find(n=>n.id==="prompt")'),
        state:()=>fromContext(evaluate(ctx,'canvas')),
        call:(name,...args)=>ctx[name](...args),
        edit(text,extra={}){Object.assign(evaluate(ctx,'canvasState.nodes.find(n=>n.id==="prompt")'),{text,...extra});ctx.scheduleSave();},
    };
}

test('R1: repeated old broadcasts keep unsaved body and metadata, and body is still sent', async()=>{
    const e=editor();
    e.edit('human body',{userTouchedAt:1234,promptEditedByUser:true});
    e.call('applyMergedServerCanvas',seed());
    e.call('applyMergedServerCanvas',seed());
    assert.equal(e.node().text,'human body');
    assert.equal(e.node().userTouchedAt,1234);
    assert.equal(e.node().promptEditedByUser,true);
    const sent=[];
    e.ctx.fetch=async (url,options)=>{
        sent.push(JSON.parse(options.body));
        return {ok:true,status:200,json:async()=>({saved:true,revision:5,updated_at:500})};
    };
    assert.equal(e.ctx.canvasId,'e2e-tabbit-a2');
    assert.equal(e.live().format,'firefly-canvas-v1');
    assert.equal(e.ctx.canvasSyncInFlight,false);
    assert.equal(await e.call('saveCanvas'),true,JSON.stringify(e.harness.toasts));
    const update=sent.flatMap(batch=>batch.mutations||[]).find(item=>item.node?.id==='prompt');
    assert.equal(update?.node.text,'human body');
});

test('R2: Agent merge advances only server shadow, never swallows local body from the next save',async()=>{
    const e=editor();
    e.edit('not yet saved',{userTouchedAt:2345,promptEditedByUser:true});
    const remote=seed(); remote.revision=5; remote.updated_at=500;
    remote.nodes.push({id:'agent-result',type:'smart-prompt',text:'remote addition'});
    e.ctx.pendingAgentMergeFingerprint=JSON.stringify({nodes:e.live().nodes,connections:e.live().connections});
    e.call('applyMergedServerCanvas',remote,{agentUndo:true});
    const sent=[];
    e.ctx.fetch=async(url,options)=>{sent.push(JSON.parse(options.body));return {ok:true,status:200,json:async()=>({saved:true,revision:6,updated_at:600})};};
    assert.equal(await e.call('saveCanvas'),true,JSON.stringify(e.harness.toasts));
    const update=sent.flatMap(batch=>batch.mutations||[]).find(item=>item.node?.id==='prompt');
    assert.equal(update?.node.text,'not yet saved');
    assert.equal(e.live().nodes.some(node=>node.id==='agent-result'),true);
});

test('R3: stale dedicated adoption never replaces a newer canvas or its pending body',async()=>{
    const latest=seed();latest.revision=6;latest.updated_at=600;latest.nodes[0].text='new server';
    const e=editor(latest);
    e.edit('pending later',{userTouchedAt:3456,promptEditedByUser:true});
    await e.call('adoptServerCanvas',seed());
    assert.equal(e.live().revision,6);
    assert.equal(e.node().text,'pending later');
    assert.equal(e.call('applyConversationCanvasResponse',seed()),false);
    assert.equal(e.node().userTouchedAt,3456);
});

test('confirmed human settings undo and redo only their own fields and metadata',async()=>{
    for(const [endpoint,handler,args,fields] of [
        ['model','setAgentModel',['new-model'],{model:'new-model'}],
        ['autonomy','setAgentAutonomy',['standard'],{autonomy:'standard'}],
        ['fork','forkAgentSession',['entry-1','named'],{branches:[{id:'original'},{id:'branch-2'}],current_branch:'branch-2'}],
        ['switch-branch','switchAgentBranch',['branch-2'],{current_branch:'branch-2'}],
    ]){
        const initial=seed();Object.assign(initial.nodes[0],{type:'smart-agent',model:'old-model',autonomy:'cautious',branches:[{id:'original'}],current_branch:'original'});
        const e=editor(initial);e.ctx.agentTuiState=new Map();e.ctx.renderAgentNode=()=>{};
        const remote=copy(initial);remote.revision=5;remote.updated_at=500;
        Object.assign(remote.nodes[0],fields,{userTouchedAt:5000});
        remote.nodes.push({id:'remote-sibling',type:'smart-prompt',text:'keep remote'});
        e.ctx.fetch=async()=>({ok:true,status:200,json:async()=>({success:true,persisted:true,canvas:remote,...fields,...(endpoint==='fork'?{branch:{id:'branch-2'}}:{})})});
        await e.call(handler,'prompt',initial.project_id,...args);
        assert.equal(evaluate(e.ctx,'undoStack.length'),1,endpoint+' must have a confirmed action');
        await e.call('performUndo');
        for(const key of Object.keys(fields)) assert.deepEqual(fromContext(e.node()[key]),initial.nodes[0][key],endpoint+' undo '+key);
        assert.equal(e.node().userTouchedAt,null);
        assert.equal(e.node().createdBy,'agent');assert.equal(e.node().agentNodeId,'agent');assert.equal(e.node().promptEditedByUser,false);
        assert.equal(e.live().nodes.some(n=>n.id==='remote-sibling'),true,'unrelated remote addition is not undone');
        await e.call('performRedo');
        for(const [key,value] of Object.entries(fields)) assert.deepEqual(fromContext(e.node()[key]),value,endpoint+' redo '+key);
        assert.equal(e.node().userTouchedAt,5000);
    }
    for(const ok of [true,false]){
        const initial=seed();Object.assign(initial.nodes[0],{type:'smart-agent',model:'same'});
        const e=editor(initial);e.ctx.agentTuiState=new Map();e.ctx.renderAgentNode=()=>{};
        e.ctx.fetch=async()=>({ok,status:ok?200:409,json:async()=>({success:ok,persisted:true,canvas:initial,model:'same'})});
        await e.call('setAgentModel','prompt',initial.project_id,'same');
        assert.equal(evaluate(e.ctx,'undoStack.length'),0,'failed and same-value settings have no undo step');
        assert.equal(e.node().userTouchedAt,null);
    }
});

test('already accepted branch transitions clear old history even when HTTP authority is now stale',async()=>{
    const initial=seed();Object.assign(initial.nodes[0],{type:'agent',current_branch:'original',branches:[{id:'original'},{id:'branch-2'}]});
    const e=editor(initial);e.ctx.agentTuiState=new Map();e.ctx.renderAgentNode=()=>{};
    const state=e.call('getAgentTuiState','prompt');state.messages.push({role:'assistant',text:'old branch'});state.toolActivities.set('old-tool',{status:'done'});
    let release;
    const remote=copy(initial);remote.revision=5;remote.updated_at=500;Object.assign(remote.nodes[0],{current_branch:'branch-2',userTouchedAt:5000});
    e.ctx.fetch=async()=>{await new Promise(resolve=>{release=resolve;});return {ok:true,status:200,json:async()=>({success:true,current_branch:'branch-2',canvas:remote})};};
    const switching=e.call('switchAgentBranch','prompt',initial.project_id,'branch-2');
    e.call('applyMergedServerCanvas',remote);
    const later=copy(remote);later.revision=6;later.updated_at=600;later.nodes.push({id:'unrelated',text:'new authority'});
    e.call('applyMergedServerCanvas',later);
    release();await switching;
    assert.equal(e.live().revision,6,'receipt must not adopt the stale canvas');
    assert.equal(e.node().current_branch,'branch-2');
    assert.equal(state.messages.length,0,'the accepted branch cannot keep the old branch transcript');
    assert.equal(state.toolActivities.size,0);
    assert.equal(evaluate(e.ctx,'undoStack.length'),1,'late receipt still completes only its own confirmed action');
    await e.call('performUndo');assert.equal(e.node().current_branch,'original');
    assert.equal(e.live().nodes.some(n=>n.id==='unrelated'),true);
});

test('fork receipt and undo preserve a concurrent branch append and its protection',async()=>{
    const initial=seed();Object.assign(initial.nodes[0],{type:'agent',current_branch:'original',branches:[{id:'original'}]});
    const e=editor(initial);e.ctx.agentTuiState=new Map();e.ctx.renderAgentNode=()=>{};
    const state=e.call('getAgentTuiState','prompt');state.messages.push({role:'assistant',text:'old branch'});
    const remote=copy(initial);remote.revision=5;remote.updated_at=500;Object.assign(remote.nodes[0],{branches:[{id:'original'},{id:'own-fork'}],current_branch:'own-fork',userTouchedAt:5000});
    let release;e.ctx.fetch=async()=>{await new Promise(resolve=>{release=resolve;});return {ok:true,status:200,json:async()=>({success:true,branch:{id:'own-fork'},canvas:remote})};};
    const forking=e.call('forkAgentSession','prompt',initial.project_id,'entry-1','own');
    e.call('applyMergedServerCanvas',remote);
    const later=copy(remote);later.revision=6;later.updated_at=600;later.nodes[0].branches.push({id:'concurrent'});later.nodes[0].userTouchedAt=6000;
    e.call('applyMergedServerCanvas',later);release();await forking;
    assert.equal(state.messages.length,0,'later branch append does not block the confirmed transition');
    assert.equal(evaluate(e.ctx,'undoStack.length'),1);
    await e.call('performUndo');
    assert.deepEqual(fromContext(e.node().branches).map(b=>b.id),['original','concurrent']);
    assert.equal(e.node().current_branch,'original');assert.equal(e.node().userTouchedAt,6000);
    await e.call('performRedo');
    assert.deepEqual(new Set(fromContext(e.node().branches).map(b=>b.id)),new Set(['original','own-fork','concurrent']));
    assert.equal(e.node().current_branch,'own-fork');assert.equal(e.node().userTouchedAt,6000);
});

test('human action projection uses the real durable storage boundary',()=>{
    const e=editor();Object.assign(e.node(),{type:'local-edit',_leBusy:false,dockCollapsed:false});
    const project=node=>JSON.parse(e.ctx.FireflyProjectCanvasMutations.durableSnapshotFromCanvas(e.call('canvasForStorage')).nodes[node.id]);
    const action=e.call('beginSmartHumanEdit',[e.node()],{project,prompt:true,at:1234});
    e.node()._leBusy=true;e.node().dockCollapsed=true;e.node().running=true;
    assert.equal(e.call('finishSmartHumanEdit',action,[e.node()]),false,'UI-only changes cannot take over a node');
    assert.equal(e.node().userTouchedAt,null);
    const changed=e.call('beginSmartHumanEdit',[e.node()],{project,prompt:true,at:1235});
    e.node().text='';
    assert.equal(e.call('finishSmartHumanEdit',changed,[e.node()]),true);
    assert.equal(e.node().userTouchedAt,1235);assert.equal(e.node().promptEditedByUser,true);
});

test('human Agent settings adopt authority through the real receiver without swallowing pending edits',async()=>{
    const initial=seed();
    initial.nodes.push({id:'agent-settings',type:'smart-agent',model:'old-model',createdBy:'agent',agentNodeId:'creator',userTouchedAt:null,promptEditedByUser:false});
    const e=editor(initial);
    e.ctx.agentTuiState=new Map();
    e.ctx.renderAgentNode=()=>{}; // DOM boundary only; actual request/receiver/pending logic is loaded.
    e.edit('unsaved prompt',{userTouchedAt:3456,promptEditedByUser:true});
    const remote=copy(initial);remote.revision=5;remote.updated_at=500;
    Object.assign(remote.nodes[1],{model:'new-model',userTouchedAt:5000});
    const sent=[];
    e.ctx.fetch=async(url,options)=>{
        sent.push({url,body:JSON.parse(options.body)});
        return {ok:true,status:200,json:async()=>url.endsWith('/model')
            ? {success:true,persisted:true,model:'new-model',canvas:remote}
            : {saved:true,revision:6,updated_at:600}};
    };
    await e.call('setAgentModel','agent-settings',initial.project_id,'new-model');
    assert.equal(e.live().nodes.find(n=>n.id==='agent-settings').model,'new-model');
    assert.equal(e.live().nodes.find(n=>n.id==='agent-settings').userTouchedAt,5000);
    assert.equal(e.node().text,'unsaved prompt');
    assert.equal(evaluate(e.ctx,'smartCanvasServerShadow').nodes.find(n=>n.id==='prompt').text,'before');
    assert.equal(e.call('receiveAgentSettingsCanvas',initial),false,'old authority cannot roll back the accepted revision');
    const wrongProject=copy(remote);wrongProject.project_id='another-project';wrongProject.revision=8;
    assert.equal(e.call('receiveAgentSettingsCanvas',wrongProject),false,'late response cannot enter another project');
    assert.equal(e.live().revision,5);
    assert.equal(await e.call('saveCanvas'),true);
    const mutation=sent[1].body.mutations.find(m=>m.node?.id==='prompt');
    assert.equal(mutation.node.text,'unsaved prompt');
    assert.equal(mutation.node.promptEditedByUser,true);
    assert.equal(sent[0].body.user_edit,true);
});

test('R4: old ack keeps compensating undo, later ack clears only accepted intent',async()=>{
    const e=editor();
    e.call('pushUndo');
    e.edit('sent body',{userTouchedAt:4567,promptEditedByUser:true});
    let release;const sent=[];
    e.ctx.fetch=async(url,options)=>{
        sent.push(JSON.parse(options.body));
        if(sent.length===1) await new Promise(resolve=>{release=resolve;});
        return {ok:true,status:200,json:async()=>({saved:true,revision:4+sent.length,updated_at:400+100*sent.length})};
    };
    const saving=e.call('saveCanvas');
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(typeof release,'function');
    await e.call('performUndo');
    assert.equal(e.node().text,'before');
    assert.equal(e.node().userTouchedAt,null);
    assert.equal(e.node().promptEditedByUser,false);
    release();assert.equal(await saving,true);
    const accepted=seed();accepted.revision=5;accepted.updated_at=500;
    Object.assign(accepted.nodes[0],{text:'sent body',userTouchedAt:4567,promptEditedByUser:true});
    e.call('applyMergedServerCanvas',accepted);
    assert.equal(e.node().text,'before');
    assert.equal(e.node().promptEditedByUser,false);
    assert.equal(await e.call('saveCanvas'),true,JSON.stringify(e.harness.toasts));
    const compensation=sent[1].mutations.find(item=>item.node?.id==='prompt').node;
    assert.equal(compensation.text,'before');assert.equal(compensation.userTouchedAt,null);assert.equal(compensation.promptEditedByUser,false);
    const newest=seed();newest.revision=7;newest.updated_at=700;
    Object.assign(newest.nodes[0],{text:'new authority',userTouchedAt:7000,promptEditedByUser:true});
    e.call('applyMergedServerCanvas',newest);
    assert.equal(e.node().text,'new authority');
});

test('R4b: stale ack clears accepted intent against the newest shadow, not the stale live overlay',async()=>{
    const e=editor();e.edit('accepted A',{userTouchedAt:4567,promptEditedByUser:true});
    let release;const sent=[];
    e.ctx.fetch=async(url,options)=>{sent.push(JSON.parse(options.body));await new Promise(resolve=>{release=resolve;});return {ok:true,status:200,json:async()=>({saved:true,revision:5,updated_at:500})};};
    const saving=e.call('saveCanvas');await new Promise(resolve=>setImmediate(resolve));
    const newer=seed();newer.revision=6;newer.updated_at=600;newer.nodes[0].text='new authority B';
    e.call('applyMergedServerCanvas',newer);assert.equal(e.node().text,'accepted A');
    release();assert.equal(await saving,true);
    assert.equal(e.node().text,'new authority B');
    assert.equal(e.live().revision,6);
    assert.equal(await e.call('saveCanvas'),true);
    assert.equal(sent.length,1);
});

test('R5: ambiguous duplicate assets keep pending and cannot be acknowledged by a no-write save',async()=>{
    const base=seed();base.nodes[0].type='smart-image';base.nodes[0].images=[{asset_id:'shared',name:'A'},{asset_id:'shared',name:'B'}];
    const e=editor(base);
    e.node().images[1].name='local B';e.call('scheduleSave');
    const remote=copy(base);remote.revision=5;remote.updated_at=500;remote.nodes[0].images.push({asset_id:'remote',name:'remote C'});
    e.call('applyMergedServerCanvas',remote);
    assert.ok(e.harness.toasts.some(message=>message.includes('冲突')));
    let requests=0;e.ctx.fetch=async()=>{requests++;throw new Error('ambiguous intent must not be sent');};
    assert.equal(await e.call('saveCanvas'),false);
    assert.equal(requests,0);
    assert.equal(e.call('smartCanvasPendingState').journal.hasPending(),true);
});

test('R1b: local title replays after server metadata and remains a real save mutation',async()=>{
    const e=editor();e.live().title='local title';e.call('scheduleSave');
    e.call('applyMergedServerCanvas',seed());assert.equal(e.live().title,'local title');
    const sent=[];e.ctx.fetch=async(url,options)=>{sent.push(JSON.parse(options.body));return {ok:true,status:200,json:async()=>({saved:true,revision:5,updated_at:500})};};
    assert.equal(await e.call('saveCanvas'),true);
    assert.equal(sent.flatMap(batch=>batch.mutations).find(item=>item.op==='set_canvas_meta')?.title,'local title');
});

test('R4c: explicit flush saved:false retains pending and reports failure',async()=>{
    const e=editor();e.edit('not durable');let requests=0;
    e.ctx.fetch=async()=>{requests++;return {ok:true,status:200,json:async()=>({saved:false,revision:5,updated_at:500})};};
    assert.equal(await e.call('saveCanvas'),false);
    assert.equal(requests,2);assert.equal(e.call('smartCanvasPendingState').journal.hasPending(),true);
});

test('R4d: media import changes reference identity without losing a later rename',async()=>{
    const base=seed();base.nodes[0].type='smart-image';base.nodes[0].images=[];
    const e=editor(base);e.node().images=[{url:'/test-image.png',name:'before import'}];e.call('scheduleSave');
    let release;const sent=[];
    e.ctx.fetch=async(url,options)=>{
        if(url.endsWith('/media/import')){await new Promise(resolve=>{release=resolve;});return {ok:true,status:200,json:async()=>({media:{asset_id:'imported',kind:'image',name:'before import'}})};}
        sent.push(JSON.parse(options.body));return {ok:true,status:200,json:async()=>({saved:true,revision:4+sent.length,updated_at:400+100*sent.length})};
    };
    const saving=e.call('saveCanvas');await new Promise(resolve=>setImmediate(resolve));assert.equal(typeof release,'function');
    e.node().images[0].name='late rename';e.call('scheduleSave');
    release();assert.equal(await saving,true,JSON.stringify(e.harness.toasts));
    assert.equal(e.node().images[0]?.name,'late rename');assert.equal(e.node().images[0]?.asset_id,'imported');
    assert.equal(await e.call('saveCanvas'),true);
    assert.equal(sent[1].mutations.find(item=>item.node?.id==='prompt').node.images[0].name,'late rename');
});

test('#375: redo of an Agent addition retains terminal result received while its undo is pending',async()=>{
    const e=editor();const remote=seed();remote.revision=5;remote.updated_at=500;
    remote.nodes.push({id:'task',type:'agent-task',status:'queued',result:null});
    e.ctx.pendingAgentMergeFingerprint=JSON.stringify({nodes:e.live().nodes,connections:e.live().connections});
    e.call('applyMergedServerCanvas',remote,{agentUndo:true});
    await e.call('performUndo');assert.equal(e.live().nodes.some(node=>node.id==='task'),false);
    const terminal=copy(remote);terminal.revision=6;terminal.updated_at=600;
    Object.assign(terminal.nodes[1],{status:'succeeded',result:{text:'completed output'},completed_at:600});
    e.call('applyMergedServerCanvas',terminal);assert.equal(e.live().nodes.some(node=>node.id==='task'),false);
    await e.call('performRedo');
    assert.equal(e.live().nodes.find(node=>node.id==='task')?.status,'succeeded');
    assert.equal(e.live().nodes.find(node=>node.id==='task')?.result?.text,'completed output');
    const sent=[];e.ctx.fetch=async(url,options)=>{sent.push(JSON.parse(options.body));return {ok:true,status:200,json:async()=>({saved:true,revision:7,updated_at:700})};};
    assert.equal(await e.call('saveCanvas'),true);
    for(const item of sent.flatMap(batch=>batch.mutations||[]).filter(item=>item.node?.id==='task')) assert.equal(item.node.result.text,'completed output');
});

test('R5b: receive preserves two existing media IDs even when they share an asset',async()=>{
    const base=seed();base.nodes[0].type='smart-image';base.nodes[0].images=[{id:'A',asset_id:'shared'},{id:'B',asset_id:'shared'}];
    const e=editor(base);const remote=copy(base);remote.revision=5;remote.updated_at=500;
    e.call('applyMergedServerCanvas',remote);
    assert.deepEqual(Array.from(e.node().images,item=>item.id),['A','B']);
    const sent=[];e.ctx.fetch=async(url,options)=>{sent.push(JSON.parse(options.body));return {ok:true,status:200,json:async()=>({saved:true,revision:6,updated_at:600})};};
    assert.equal(await e.call('saveCanvas'),true,JSON.stringify(e.harness.toasts));
    for(const item of sent.flatMap(batch=>batch.mutations||[]).filter(item=>item.node?.id==='prompt')) assert.deepEqual(item.node.images.map(image=>image.id),['A','B']);
    assert.deepEqual(Array.from(e.node().images,item=>item.id),['A','B']);
});

test('history stays newest-first through receive, adopt, PATCH success/conflict/exhaustion and classic PUT',async()=>{
    const old={id:'old-log',createdAt:100},recent={id:'new-log',createdAt:300},local={id:'local-log',createdAt:200};
    for(const boundary of ['broadcast','adopt','PATCH','PATCH-conflict','PATCH-exhaustion','PUT']){
        const initial=seed();if(boundary==='PUT'){delete initial.project_id;delete initial.format;}
        const e=editor(initial);e.live().history=[copy(local)];e.call('scheduleSave');
        const remote=copy(initial);remote.revision=5;remote.updated_at=500;remote.history=[copy(old),copy(recent)];
        if(boundary==='broadcast') e.call('applyMergedServerCanvas',remote);
        else if(boundary==='adopt') await e.call('adoptServerCanvas',remote);
        else {
            let requests=0;
            if(boundary==='PATCH'){
                e.live().history=[copy(old),copy(local),copy(recent)];e.call('scheduleSave');
            }
            e.ctx.fetch=async()=>{
                requests++;
                if(boundary==='PUT') return {ok:true,status:200,json:async()=>({canvas:remote})};
                if(boundary==='PATCH-exhaustion'||(boundary==='PATCH-conflict'&&requests===1)) return {ok:false,status:409,json:async()=>({detail:{canvas:remote}})};
                return {ok:true,status:200,json:async()=>({saved:true,revision:6,updated_at:600})};
            };
            assert.equal(await e.call('saveCanvas'),boundary!=='PATCH-exhaustion',boundary+': '+JSON.stringify(e.harness.toasts));
        }
        assert.deepEqual(Array.from(e.live().history,item=>item.id),['new-log','local-log','old-log'],boundary);
    }
});

test('classic PUT ack leaves a later compensating undo with explicit null and false pending',async()=>{
    const initial=seed();delete initial.project_id;delete initial.format;
    const e=editor(initial);e.call('pushUndo');e.edit('A',{userTouchedAt:1234,promptEditedByUser:true});
    let release,started;const ready=new Promise(resolve=>started=resolve),wait=new Promise(resolve=>release=resolve),sent=[];
    e.ctx.fetch=async(url,options)=>{
        const body=JSON.parse(options.body);sent.push(body);started();await wait;
        return {ok:true,status:200,json:async()=>({canvas:{...copy(initial),...body,updated_at:500}})};
    };
    const saving=e.call('saveCanvas');await ready;await e.call('performUndo');release();
    assert.equal(await saving,true);
    assert.equal(e.node().text,'before');assert.equal(e.node().userTouchedAt,null);assert.equal(e.node().promptEditedByUser,false);
    assert.equal(await e.call('saveCanvas'),true);
    assert.equal(sent[1].nodes.find(node=>node.id==='prompt').text,'before');
    assert.equal(sent[1].nodes.find(node=>node.id==='prompt').userTouchedAt,null);
    assert.equal(sent[1].nodes.find(node=>node.id==='prompt').promptEditedByUser,false);
});

test('R3b: invalid-media recovery drops only confirmed dangling references, never a late body or ownership',async()=>{
    const e=editor();e.node().images=[{asset_id:'gone'},{asset_id:'live'}];e.edit('A',{userTouchedAt:1234,promptEditedByUser:true});
    const remote=seed();remote.revision=5;remote.updated_at=500;
    e.ctx.fetch=async(url,options)=>{
        if(options?.method==='PATCH') return {ok:false,status:400,json:async()=>({detail:'项目媒体不存在：gone'})};
        if(url.endsWith('/media')) return {ok:true,status:200,json:async()=>({items:[{asset_id:'live'}]})};
        e.edit('B',{userTouchedAt:2345,promptEditedByUser:true});
        return {ok:true,status:200,json:async()=>remote};
    };
    assert.equal(await e.call('saveCanvas'),false);
    assert.equal(e.node().text,'B');assert.equal(e.node().userTouchedAt,2345);assert.equal(e.node().promptEditedByUser,true);
    assert.deepEqual(Array.from(e.node().images,item=>item.asset_id),['live']);
    const sent=[];e.ctx.fetch=async(url,options)=>{sent.push(JSON.parse(options.body));return {ok:true,status:200,json:async()=>({saved:true,revision:6,updated_at:600})};};
    assert.equal(await e.call('saveCanvas'),true);
    const node=sent[0].mutations.find(item=>item.node?.id==='prompt').node;
    assert.equal(node.text,'B');assert.equal(node.userTouchedAt,2345);assert.deepEqual(node.images.map(item=>item.asset_id),['live']);
});

test('R1c: acknowledged in-flight media content cannot mask a newer authoritative edit',async()=>{
    const initial=seed();Object.assign(initial.nodes[0],{type:'smart-image',images:[],generationSubmissions:[{idempotencyKey:'queued'}]});
    const e=editor(initial);e.edit('A',{userTouchedAt:1234,promptEditedByUser:true});
    let release,started;const ready=new Promise(resolve=>started=resolve),wait=new Promise(resolve=>release=resolve);
    e.ctx.fetch=async()=>{started();await wait;return {ok:true,status:200,json:async()=>({saved:true,revision:5,updated_at:500})};};
    const saving=e.call('saveCanvas');await ready;
    const remote=copy(initial);remote.revision=6;remote.updated_at=600;Object.assign(remote.nodes[0],{text:'B',userTouchedAt:2345,title:'remote title'});
    e.call('applyMergedServerCanvas',remote);release();assert.equal(await saving,true);
    assert.equal(e.node().text,'B');assert.equal(e.node().userTouchedAt,2345);assert.equal(e.node().title,'remote title');
});

test('R5c: ack retires local media rename so a newer remote rename or deletion remains authoritative',async()=>{
    for(const remoteImages of [[{output_id:'out',asset_id:'asset',name:'B'}],[]]){
        const initial=seed();Object.assign(initial.nodes[0],{type:'smart-image',images:[{output_id:'out',asset_id:'asset',name:'before'}]});
        const e=editor(initial);e.node().images[0].name='A';e.edit('A');
        let release,started;const ready=new Promise(resolve=>started=resolve),wait=new Promise(resolve=>release=resolve);
        e.ctx.fetch=async()=>{started();await wait;return {ok:true,status:200,json:async()=>({saved:true,revision:5,updated_at:500})};};
        const saving=e.call('saveCanvas');await ready;
        const remote=copy(initial);remote.revision=6;remote.updated_at=600;remote.nodes[0].images=copy(remoteImages);
        e.call('applyMergedServerCanvas',remote);release();assert.equal(await saving,true);
        assert.deepEqual(Array.from(e.node().images,item=>item.name),remoteImages.map(item=>item.name));
    }
});

test('R3c: confirmed empty media library removes the last dangling reference without discarding body',async()=>{
    const e=editor();e.node().images=[{asset_id:'gone'}];e.edit('keep body');
    e.ctx.fetch=async(url,options)=>{
        if(options?.method==='PATCH') return {ok:false,status:400,json:async()=>({detail:'project media not found: gone'})};
        if(url.endsWith('/media')) return {ok:true,status:200,json:async()=>({items:[]})};
        return {ok:true,status:200,json:async()=>seed()};
    };
    assert.equal(await e.call('saveCanvas'),false);assert.equal(e.node().text,'keep body');assert.equal(e.node().images.length,0);
    e.ctx.fetch=async()=>({ok:true,status:200,json:async()=>({saved:true,revision:5,updated_at:500})});
    assert.equal(await e.call('saveCanvas'),true);
});

test('R1d: first classic creation is journaled from the real loaded server anchor',async()=>{
    const initial=seed();delete initial.format;delete initial.project_id;
    const e=editor(initial);Object.assign(e.ctx,{
        persistedCoreSnapshot:null,persistedDurableSnapshot:null,canvasLoadReady:false,composer:null,
        loadRecentSmartSettings(){},applyRecentSmartSettingsForCurrentMode(){},updateProviderModels(){},
        frameSmartCanvasContentIfNeeded(){},resumeConversationTasks(){},startCanvasMetaPoll(){},
        restorePersistedAnnotations(){},openCanvasRecoveryWizard(){throw new Error('unexpected recovery wizard');},
    });
    e.ctx.fetch=async()=>({ok:true,status:200,json:async()=>({canvas:copy(initial)})});
    assert.equal(await e.call('loadCanvas'),true,JSON.stringify(e.harness.toasts));
    e.live().nodes.push({id:'first-new',type:'smart-prompt',text:'new after load'});e.call('scheduleSave');
    const remote=copy(initial);remote.updated_at=500;e.call('applyMergedServerCanvas',remote);
    assert.equal(e.live().nodes.find(node=>node.id==='first-new')?.text,'new after load');
});

test('R1e: pending media creation is hydrated after receive while storage and server shadow remain references',async()=>{
    const e=editor();e.live().nodes.push({id:'copied',type:'smart-image',images:[{
        output_id:'out',asset_id:'asset-copy',kind:'image',url:'/api/project-library/e2e-tabbit-a2/media/asset-copy',name:'copied',
    }]});e.call('scheduleSave');
    const remote=seed();remote.revision=5;remote.updated_at=500;e.call('applyMergedServerCanvas',remote);
    const added=e.live().nodes.find(node=>node.id==='copied');
    assert.equal(added.images[0].url,'/api/project-library/e2e-tabbit-a2/media/asset-copy');
    assert.equal(added.images[0].preview_url,'/api/project-library/e2e-tabbit-a2/media/asset-copy/preview');
    assert.equal(e.ctx.persistedCoreSnapshot.nodes.copied,undefined);
    let sent;e.ctx.fetch=async(_url,options)=>{sent=JSON.parse(options.body);return {ok:true,status:200,json:async()=>({saved:true,revision:6,updated_at:600})};};
    assert.equal(await e.call('saveCanvas'),true);
    const stored=sent.mutations.find(change=>change.node?.id==='copied').node.images[0];
    assert.equal(stored.asset_id,'asset-copy');assert.equal(stored.url,undefined);assert.equal(stored.preview_url,undefined);
});

test('R3d: malformed refreshed media list is not proof for pruning live references',async()=>{
    const e=editor();e.ctx.canvasState.projectMediaLibrary={items:[{asset_id:'stale-cache'}]};
    e.node().images=[{asset_id:'gone'},{asset_id:'just-uploaded'}];e.edit('keep both until confirmed');
    e.ctx.fetch=async(url,options)=>{
        if(options?.method==='PATCH') return {ok:false,status:400,json:async()=>({detail:'project media not found: gone'})};
        if(url.endsWith('/media')) return {ok:true,status:200,json:async()=>({})};
        return {ok:true,status:200,json:async()=>seed()};
    };
    assert.equal(await e.call('saveCanvas'),false);
    assert.deepEqual(Array.from(e.node().images,item=>item.asset_id),['gone','just-uploaded']);
    assert.equal(e.node().text,'keep both until confirmed');
});
