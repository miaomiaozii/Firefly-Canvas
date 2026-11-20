// Real core + real undo stacks/entrypoints. Browser transport and the authority
// receiver are explicit seams; full route/transaction/Store persistence is tested
// in test_agent_host_settings_421_424.py, not simulated by this receiver.
import assert from 'node:assert/strict';
import test from 'node:test';
import {createCanvasContext, loadModules, evaluate, fromContext} from './harness.mjs';

const a = {host:'pi', model:'provider/old', effort:'low'};
const b = {host:'pi', model:'provider/new', effort:'high'};
const copy = value => JSON.parse(JSON.stringify(value));
const choice = node => ({host:node.agentCli, ...node.agentHostSettings[node.agentCli]});
const tick = () => new Promise(resolve => setImmediate(resolve));
function node(selection=b){
    return {id:'agent', type:'smart-agent', agentCli:selection.host, model:selection.model,
        agentHostSettings:{pi:{model:'provider/old', effort:'low'}, codex:{model:'gpt-other', effort:'high'},
            [selection.host]:{model:selection.model, effort:selection.effort}},
        agentHostSessions:{pi:{extSessionId:'pi-progress', branches:[{id:'pi-new-branch'}], current_branch:'pi-new-branch'},
            codex:{extSessionId:'codex-progress', branches:[], current_branch:''}},
        extSessionId:'active-progress', branches:[{id:'new-branch'}], current_branch:'new-branch', mcp_session:'mcp-progress',
        createdBy:'agent', agentNodeId:'creator', promptEditedByUser:true, userTouchedAt:100, updated_at:100,
        messages:[{role:'user', text:'retained'}], x:71};
}
function setup({before=node(a), after=node(b), record=true}={}){
    const {ctx, harness} = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js','canvas-core.js','canvas-state.js','project-canvas-mutations.js','canvas-media-history.js']);
    ctx.initialNodes = [copy(after), {id:'sibling', type:'smart-prompt', text:'keep'}];
    evaluate(ctx, `canvas={id:'cv',project_id:'project',format:'firefly-canvas-v1',revision:3,nodes:initialNodes,connections:[]};
        canvasState.nodes=canvas.nodes; canvasState.annotations=[];`);
    const calls=[], received=[], toasts=[], saves=[];
    ctx.render = () => {};
    ctx.toast = value => toasts.push(String(value));
    ctx.tr = key => key;
    ctx.scheduleSave = () => saves.push('ordinary-save');
    // Runtime/DOM adoption is covered by the real TUI integration tests.
    ctx.applyAgentHostSettingsAuthority = () => {};
    ctx.agentNodeHost = node => node.agentCli;
    ctx.cancelPendingMediaArchives = () => {};
    ctx.fetch = (url, options) => new Promise((resolve,reject) => calls.push({url, options, resolve, reject}));
    // Explicit authoritative receiver seam: adopts only this response, records no
    // history, returns a strict boolean. It does not mock the undo stack itself.
    ctx.receiveAgentSettingsCanvas = (data, edit) => {
        received.push({data:copy(data), edit:copy(edit)});
        ctx.incomingNodes = copy(data.nodes);
        evaluate(ctx, 'canvas.nodes=incomingNodes; canvasState.nodes=canvas.nodes;');
        return true;
    };
    harness.element('smartUndoButton'); harness.element('smartRedoButton');
    for(const id of ['agentUndoNotice','agentUndoNoticeText','agentUndoNoticeSettings','agentUndoNoticeClose']) harness.element(id);
    loadModules(ctx, ['canvas-undo.js']);
    if(record) ctx.pushAgentHostSettingsUndo(before, after);
    const state = () => fromContext(evaluate(ctx, 'canvasState.nodes'));
    const stacks = () => fromContext(evaluate(ctx, '[undoStack,redoStack]'));
    function success(index=0, selection=null, timestamp=200){
        const call = calls[index];
        const body = JSON.parse(call.options.body);
        const target = selection || body;
        const nodes = state();
        const current = nodes.find(n => n.id === 'agent');
        Object.assign(current, {agentCli:target.host, model:target.model, userTouchedAt:timestamp, updated_at:timestamp});
        current.agentHostSettings[target.host] = {model:target.model, effort:target.effort};
        call.resolve({ok:true,status:200,json:async()=>({success:true,persisted:true,canvas:{id:'cv',project_id:'project',revision:4+index,nodes}})});
    }
    return {ctx,harness,calls,received,toasts,saves,state,stacks,success};
}

const stackSizes = h => h.stacks().map(stack=>stack.length);
const failed = (call, code, status=409) => call.resolve({ok:false,status,json:async()=>({detail:{code,message:'transaction refused'}})});

test('classic global records only the selected manual pair, never a native binding/snapshot/delta', () => {
    const h=setup();
    assert.deepEqual(h.stacks(), [[{agentHostSettingsEdit:{nodeId:'agent',before:a,after:b}}],[]]);
    h.ctx.finalizePendingCanvasUndo();
    assert.deepEqual(stackSizes(h),[1,0], 'generic delta sealing must not remove the settings token');
    const latest=node(b); latest.agentHostSessions.pi.extSessionId='later';
    latest.agentHostSettings.codex.model='another-host-progress'; latest.userTouchedAt=1234;
    assert.equal(h.ctx.pushAgentHostSettingsUndo(node(b),latest),false);
    assert.deepEqual(stackSizes(h),[1,0], 'bindings/inactive host/timestamps alone are not a manual edit');
    const legacy=setup({record:false});
    const old={id:'agent',agentCli:' pi ',model:null,extSessionId:'never-record-me'};
    legacy.ctx.pushAgentHostSettingsUndo(old,node(b));
    assert.deepEqual(legacy.stacks()[0][0].agentHostSettingsEdit.before,{host:'pi',model:'',effort:null});
    const mapped=node(a); mapped.model='stale-top-projection';
    const canonical=setup({before:mapped});
    assert.deepEqual(canonical.stacks()[0][0].agentHostSettingsEdit.before,a);
    assert.throws(()=>h.ctx.pushAgentHostSettingsUndo({id:'different'},node(b)),/节点身份/);
});

test('undo/redo POST the inverse pair plus expected; authority alone updates timestamps and preserves progressed bindings', async () => {
    const h=setup();
    const token=evaluate(h.ctx,'undoStack[0]');
    const initial=h.state();
    const undo=h.ctx.performUndo();
    assert.deepEqual(JSON.parse(h.calls[0].options.body),{...a,expected:b,user_edit:true});
    assert.equal(h.calls[0].url,'/api/projects/project/agent-nodes/agent/settings');
    assert.equal(h.calls[0].options.method,'POST');
    assert.deepEqual(h.state(),initial,'no optimistic node mutation');
    assert.deepEqual(stackSizes(h),[1,0],'no optimistic history move');
    // Native/inactive-host progress while HTTP is pending is not an undo scope.
    evaluate(h.ctx, `canvasState.nodes[0].agentHostSessions.codex.extSessionId='codex-even-later';
        canvasState.nodes[0].agentHostSettings.codex.model='gpt-later';`);
    h.success(); await undo;
    assert.deepEqual(stackSizes(h),[0,1]);
    assert.equal(evaluate(h.ctx,'redoStack[0]'),token);
    assert.deepEqual(choice(h.state()[0]),a);
    assert.equal(h.state()[0].agentHostSessions.codex.extSessionId,'codex-even-later');
    assert.equal(h.state()[0].agentHostSettings.codex.model,'gpt-later');
    assert.equal(h.state()[0].userTouchedAt,200,'do not rewind the edit timestamp');
    for(const field of ['agentHostSessions','extSessionId','branches','current_branch','mcp_session','messages','createdBy','agentNodeId','promptEditedByUser','x']){
        if(field!=='agentHostSessions') assert.deepEqual(h.state()[0][field],initial[0][field]);
    }
    assert.equal(h.received[0].edit.before.agentHostSessions.codex.extSessionId,'codex-even-later','receiver before is the current node, not capture-time history');
    assert.deepEqual(h.received[0].edit.fields,['agentCli','agentHostSettings','model']);
    assert.equal(h.received[0].edit.recordUndo,false);
    const redo=h.ctx.performRedo();
    assert.deepEqual(JSON.parse(h.calls[1].options.body),{...b,expected:a,user_edit:true});
    h.success(1,null,300); await redo;
    assert.deepEqual(stackSizes(h),[1,0]);
    assert.equal(evaluate(h.ctx,'undoStack[0]'),token);
    assert.deepEqual(choice(h.state()[0]),b);
    assert.equal(h.state()[0].userTouchedAt,300);
    assert.deepEqual(h.saves,[],'neither direction may call ordinary scheduleSave');
    assert.deepEqual(h.toasts,['smart.toastUndone','smart.toastRedone']);
    assert.equal(h.harness.elementsById.get('smartUndoButton').disabled,false);
    assert.equal(h.harness.elementsById.get('smartRedoButton').disabled,true);
});

test('host switch undo/redo records neither the other host settings nor binding migration', async () => {
    const c={host:'codex',model:'gpt-other',effort:'high'};
    const h=setup({before:node(a),after:node(c)});
    const undo=h.ctx.performUndo(); h.success(); await undo;
    assert.deepEqual(JSON.parse(h.calls[0].options.body),{...a,expected:c,user_edit:true});
    const redo=h.ctx.performRedo(); h.success(1); await redo;
    assert.deepEqual(JSON.parse(h.calls[1].options.body),{...c,expected:a,user_edit:true});
    assert.deepEqual(h.state()[0].agentHostSessions,node(c).agentHostSessions);
    assert.deepEqual(h.stacks()[0][0],{agentHostSettingsEdit:{nodeId:'agent',before:a,after:c}});
});

for(const direction of ['undo','redo']){
    for(const mode of ['agent_settings_conflict','agent_busy','agent_default_unverified','deleted','network','bad-json','server-false','no-canvas','receiver-false','receiver-nonboolean','receiver-throws']){
        test(`${direction}: ${mode} visibly fails and retains the exact original history`, async () => {
            const h=setup();
            if(direction==='redo') { const pending=h.ctx.performUndo(); h.success(); await pending; }
            const stacks=h.stacks(), state=h.state(), received=h.received.length;
            const operation=h.ctx[direction==='undo'?'performUndo':'performRedo']();
            const call=h.calls.at(-1);
            if(mode==='network') call.reject(new Error('network offline'));
            else if(mode==='bad-json') call.resolve({ok:false,status:502,json:async()=>{throw new Error('invalid JSON');}});
            else if(mode==='server-false') call.resolve({ok:true,status:200,json:async()=>({success:false,canvas:{}})});
            else if(mode==='no-canvas') call.resolve({ok:true,status:200,json:async()=>({success:true})});
            else if(mode.startsWith('receiver-')){
                h.ctx.receiveAgentSettingsCanvas=()=>{
                    if(mode==='receiver-throws') throw new Error('receiver rejected');
                    return mode==='receiver-nonboolean' ? 1 : false;
                };
                h.success(h.calls.length-1);
            } else failed(call,mode==='deleted'?'not_found':mode,mode==='deleted'?404:409);
            await operation;
            assert.deepEqual(h.stacks(),stacks);
            assert.deepEqual(h.state(),state);
            assert.equal(h.received.length,received);
            if(mode === 'agent_default_unverified'){
                assert.equal(h.harness.element('agentUndoNotice').hidden, false);
                assert.match(h.harness.element('agentUndoNoticeText').textContent, /原默认无法确认/);
            } else {
                assert.match(h.toasts.at(-1),/Agent 设置.*失败/);
                if(mode.startsWith('agent_')) assert.ok(h.toasts.at(-1).includes(mode));
            }
            assert.deepEqual(h.saves,[]);
        });
    }
}

test('same in-flight undo or redo cannot POST twice or move a second entry', async () => {
    const h=setup();
    for(const [name,index] of [['performUndo',0],['performRedo',1]]){
        const pending=h.ctx[name]();
        const stacks=h.stacks();
        await h.ctx[name]();
        assert.equal(h.calls.length,index+1);
        assert.deepEqual(h.stacks(),stacks);
        assert.match(h.toasts.at(-1),/已有设置历史请求/);
        h.success(index); await pending;
    }
    assert.deepEqual(stackSizes(h),[1,0]);
    assert.equal(h.received.length,2);
});

for(const phase of ['fetch','json']){
    for(const context of ['project','canvas-id','epoch','pending','top','node-deleted']){
        test(`late ${phase} response with changed ${context} never reaches the receiver or moves history`, async () => {
            const h=setup();
            const pending=h.ctx.performUndo();
            let finish;
            if(phase==='json'){
                h.calls[0].resolve({ok:true,status:200,json:()=>new Promise(resolve=>{finish=resolve;})});
                await tick();
                assert.equal(typeof finish,'function');
            }
            const mutations={
                project:`canvas.project_id='another-project'`,
                'canvas-id':`canvas.id='another-canvas'`,
                epoch:`undoHistoryEpoch+=1`,
                pending:`pendingUndoSnapshot={nodes:[]}`,
                top:`undoStack.push({agentHostSettingsEdit:{nodeId:'another',before:${JSON.stringify(a)},after:${JSON.stringify(b)}}})`,
                'node-deleted':`canvasState.nodes=canvas.nodes=canvasState.nodes.filter(node=>node.id!=='agent')`,
            };
            evaluate(h.ctx,mutations[context]);
            const stacks=h.stacks(), state=h.state();
            const data={success:true,canvas:{id:'cv',project_id:'project',nodes:[node(a)]}};
            if(phase==='json') finish(data);
            else h.calls[0].resolve({ok:true,status:200,json:async()=>data});
            await pending;
            assert.deepEqual(h.stacks(),stacks);
            assert.deepEqual(h.state(),state);
            assert.deepEqual(h.received,[]);
            assert.match(h.toasts.at(-1),/失败/);
        });
    }
}

test('receiver-side context changes are checked again before any history move', async () => {
    for(const mutation of [`undoHistoryEpoch+=1`,`pendingUndoSnapshot={}`,`canvas.project_id='other'`]){
        const h=setup();
        h.ctx.receiveAgentSettingsCanvas=()=>{evaluate(h.ctx,mutation);return true;};
        const stacks=h.stacks();
        const pending=h.ctx.performUndo(); h.success(); await pending;
        assert.deepEqual(h.stacks(),stacks);
        assert.match(h.toasts.at(-1),/历史上下文已变化/);
    }
});

test('malformed persisted manual history never downgrades to an unconditional settings request', async () => {
    for(const replacement of [undefined, null, {host:'pi',model:''}, {...a,extra:'forbidden'}, {...a,effort:false}]){
        const h=setup();
        evaluate(h.ctx,'undoStack[0].agentHostSettingsEdit.after=null');
        evaluate(h.ctx,'undoStack[0].agentHostSettingsEdit').after=replacement;
        const stacks=h.stacks();
        await h.ctx.performUndo();
        assert.equal(h.calls.length,0);
        assert.deepEqual(h.stacks(),stacks);
        assert.match(h.toasts.at(-1),/历史格式无效/);
    }
    for(const malformed of [null,[],false]){
        const h=setup({record:false});
        const before=node(a); before.agentHostSettings=malformed;
        assert.throws(()=>h.ctx.pushAgentHostSettingsUndo(before,node(b)),/无效/);
        assert.deepEqual(stackSizes(h),[0,0]);
    }
});

test('a removed node fails before sending and a failed request releases the history in-flight gate for retry', async () => {
    const removed=setup();
    evaluate(removed.ctx,'canvasState.nodes=[]');
    await removed.ctx.performUndo();
    assert.deepEqual(stackSizes(removed),[1,0]);
    assert.equal(removed.calls.length,0);
    assert.match(removed.toasts.at(-1),/节点已不存在/);
    const h=setup();
    const first=h.ctx.performUndo(); failed(h.calls[0],'agent_busy'); await first;
    const retry=h.ctx.performUndo(); h.success(1); await retry;
    assert.deepEqual(stackSizes(h),[0,1]);
});

test('dedicated tokens persist/reload without bindings, respect FIFO, suppression and redo invalidation', async () => {
    const h=setup();
    let payload;
    h.ctx.fetch=async (_url,options)=>{payload=JSON.parse(options.body);return {ok:true};};
    await h.ctx.persistUndoStackNow('project');
    assert.deepEqual(payload,{undo:[{agentHostSettingsEdit:{nodeId:'agent',before:a,after:b}}],redo:[]});
    const reload=setup({record:false});
    reload.ctx.fetch=async()=>({ok:true,json:async()=>payload});
    await reload.ctx.restorePersistedUndoStack();
    reload.ctx.fetch=(url,options)=>new Promise((resolve,reject)=>reload.calls.push({url,options,resolve,reject}));
    const undone=reload.ctx.performUndo(); reload.success(); await undone;
    assert.deepEqual(stackSizes(reload),[0,1]);
    const noOp=copy(reload.state()[0]);
    assert.equal(reload.ctx.pushAgentHostSettingsUndo(noOp,noOp),false);
    assert.deepEqual(stackSizes(reload),[0,1]);
    evaluate(reload.ctx,'undoSuppressed=true');
    assert.equal(reload.ctx.pushAgentHostSettingsUndo(node(a),node(b)),false);
    assert.deepEqual(stackSizes(reload),[0,1]);
    evaluate(reload.ctx,'undoSuppressed=false');
    reload.ctx.pushAgentHostSettingsUndo(node(a),node(b));
    assert.deepEqual(stackSizes(reload),[1,0]);
    for(let index=0;index<41;index++){
        const after=node({...b,model:'new-'+index});
        reload.ctx.pushAgentHostSettingsUndo(node(a),after);
    }
    assert.equal(reload.stacks()[0].length,40);
    assert.equal(reload.stacks()[0][0].agentHostSettingsEdit.after.model,'new-1');
    assert.equal(reload.stacks()[0][39].agentHostSettingsEdit.after.model,'new-40');
});
