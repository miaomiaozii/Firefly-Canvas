import assert from 'node:assert/strict';
import test from 'node:test';
import {createCanvasContext, loadModules, evaluate, fromContext} from './harness.mjs';

const before = {host:'pi',model:'',effort:null};
const after = {host:'pi',model:'chosen',effort:'high'};
const copy = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));
const capabilities = host => ({host,status:'available',models:[{id:'chosen',name:'Chosen'}],defaults:{model:'',effort:null},selection:{model:'chosen',efforts:['high'],source:'test'}});
function assertPicker(h,host='pi'){
    const picker=h.ctx.getAgentTuiState('target').modelPicker;
    assert.ok(picker); assert.equal(picker.host,host); assert.equal(picker.projectId,'project');
    assert.equal(picker.kind,'model'); assert.equal(picker.menu.getAttribute('role'),'menu');
    assert.ok(h.ctx.document.body.children.includes(picker.menu));
    assert.equal(picker.menu.style.bottom,'272px');
    assert.match(h.calls.at(-1).url,new RegExp(`/api/projects/project/agent-nodes/capabilities\\?host=${host}&model=chosen`));
    assert.match(picker.menu.innerHTML,/正在读取宿主能力/);
    return picker;
}
async function finishPicker(h,picker,host='pi'){
    h.calls.at(-1).resolve({ok:true,json:async()=>capabilities(host)}); await flush();
    assert.match(picker.menu.innerHTML,/Chosen/);
}
function setup(stored){
    const {ctx,harness} = createCanvasContext();
    if(stored) for(const [key,value] of stored) harness.store.set(key,value);
    for(const id of ['agentUndoNotice','agentUndoNoticeText','agentUndoNoticeSettings','agentUndoNoticeClose']) harness.element(id);
    loadModules(ctx,['canvas-node-types.js','canvas-core.js','canvas-state.js','canvas-undo.js','canvas-agent-navigation.js','canvas-agent-tui.js']);
    const agent = {id:'target',type:'smart-agent',agentCli:'pi',model:'chosen',agentHostSettings:{pi:{model:'chosen',effort:'high'}}};
    ctx.initialNodes = [agent,{id:'other',type:'smart-agent',agentCli:'pi',model:'other'}];
    evaluate(ctx,"canvas={id:'cv',project_id:'project',nodes:initialNodes,connections:[]}; canvasState.nodes=canvas.nodes;");
    const calls=[], toasts=[];
    ctx.fetch=(url,options)=>new Promise((resolve,reject)=>calls.push({url,options,resolve,reject}));
    ctx.toast=value=>toasts.push(String(value)); ctx.tr=x=>x; ctx.cancelPendingMediaArchives=()=>{};
    ctx.renderAgentNode=()=>{};
    ctx.receiveAgentSettingsCanvas=data=>{ctx.canvasState.nodes=data.nodes; return true;};
    ctx.applyAgentHostSettingsAuthority=()=>{};
    ctx.innerWidth=1024; ctx.innerHeight=768;
    const anchor=harness.makeElement('button'); anchor.setAttribute('data-agent-model-picker','target');
    anchor.getBoundingClientRect=()=>({left:300,top:500});
    let focused=false, revealed=''; anchor.focus=()=>{focused=true;};
    const otherAnchor=harness.makeElement('button'); otherAnchor.setAttribute('data-agent-model-picker','other');
    otherAnchor.getBoundingClientRect=()=>({left:10,top:100});
    harness.querySelectorAll('[data-agent-model-picker]',[otherAnchor,anchor]);
    evaluate(ctx,`undoStack.push({agentHostSettingsEdit:{nodeId:'target',before:${JSON.stringify(before)},after:${JSON.stringify(after)}}});`);
    const selection = new ctx.CustomEvent('studio-canvas-selection-sync', {detail:{locate:id=>{revealed=id; return true;}}});
    harness.windowListeners.get(selection.type).forEach(listener=>listener(selection));
    return {ctx,harness,calls,toasts,anchor,focused:()=>focused,revealed:()=>revealed,bar:harness.element('agentUndoNotice'),text:harness.element('agentUndoNoticeText'),settings:harness.element('agentUndoNoticeSettings'),close:harness.element('agentUndoNoticeClose'),stacks:()=>fromContext(evaluate(ctx,'[undoStack,redoStack]'))};
}
function fail(call,code='agent_default_unverified'){
    call.resolve({ok:false,status:409,json:async()=>({detail:{code,message:'raw server error'}})});
}
for(const direction of ['undo','redo']){
    test(`${direction}: persistent notice retains exact state and stack, dismiss and repeat`,async()=>{
        const h=setup();
        if(direction==='redo'){
            const undo=h.ctx.performUndo();
            const nodes=copy(h.ctx.canvasState.nodes);
            nodes[0].model=''; nodes[0].agentHostSettings.pi={model:'',effort:null};
            h.calls[0].resolve({ok:true,status:200,json:async()=>({success:true,persisted:true,canvas:{nodes}})});
            await undo;
            assert.deepEqual(h.stacks().map(stack=>stack.length),[0,1]);
        }
        const stacks=h.stacks(), nodes=copy(h.ctx.canvasState.nodes);
        const run=()=>h.ctx[direction==='undo'?'performUndo':'performRedo']();
        let pending=run(); fail(h.calls.at(-1)); await pending;
        assert.deepEqual(h.stacks(),stacks); assert.deepEqual(copy(h.ctx.canvasState.nodes),nodes);
        assert.equal(h.bar.hidden,false);
        assert.equal(h.text.textContent,'上次尝试：原默认无法确认，当时设置未变，撤销/重做记录保留。手动设置将算作新编辑。');
        assert.doesNotMatch(h.text.textContent,/agent_default_unverified|raw server error/);
        h.ctx.syncAgentUndoNotice(); assert.equal(h.bar.hidden,false);
        const reload=setup(h.harness.store); assert.equal(reload.bar.hidden,false); assert.equal(reload.text.textContent,h.text.textContent);
        h.close.click(); assert.equal(h.bar.hidden,true);
        pending=run(); fail(h.calls.at(-1)); await pending;
        assert.equal(h.bar.hidden,false); assert.deepEqual(h.stacks(),stacks);
    });
}
test('model, effort, combined and cross-host history failures keep the request and stacks intact',async()=>{
    const cases=[
        [{host:'pi',model:'',effort:'high'},after],
        [{host:'pi',model:'chosen',effort:null},after],
        [before,after],
        [{host:'codex',model:'',effort:null},after],
    ];
    for(const [prior,current] of cases){
        const h=setup();
        h.ctx.prior=prior; h.ctx.current=current;
        evaluate(h.ctx,'undoStack[0].agentHostSettingsEdit.before=prior; undoStack[0].agentHostSettingsEdit.after=current');
        const stacks=h.stacks(), pending=h.ctx.performUndo();
        assert.deepEqual(JSON.parse(h.calls[0].options.body),{...prior,expected:current,user_edit:true});
        fail(h.calls[0]); await pending;
        assert.deepEqual(h.stacks(),stacks); assert.equal(h.bar.hidden,false);
    }
});
test('after a new edit clears redo the persisted notice only describes the failed attempt',async()=>{
    const h=setup();
    let pending=h.ctx.performUndo();
    const nodes=copy(h.ctx.canvasState.nodes);
    nodes[0].model=''; nodes[0].agentHostSettings.pi={model:'',effort:null};
    h.calls[0].resolve({ok:true,status:200,json:async()=>({success:true,persisted:true,canvas:{nodes}})});
    await pending;
    pending=h.ctx.performRedo(); fail(h.calls.at(-1)); await pending;
    assert.deepEqual(h.stacks().map(stack=>stack.length),[0,1]);
    const old=copy(h.ctx.canvasState.nodes[0]), changed=copy(old);
    changed.model='new'; changed.agentHostSettings.pi={model:'new',effort:'low'};
    h.ctx.canvasState.nodes[0]=changed;
    assert.equal(h.ctx.pushAgentHostSettingsUndo(old,changed),true);
    assert.deepEqual(h.stacks().map(stack=>stack.length),[1,0]);
    h.ctx.dispatchEvent(new h.ctx.CustomEvent('studio-canvas-selection-sync'));
    assert.match(h.text.textContent,/上次尝试：原默认无法确认，当时设置未变/);
    assert.doesNotMatch(h.text.textContent,/当前设置未改变|撤销和重做记录均已保留/);
    const reload=setup(h.harness.store);
    assert.equal(reload.text.textContent,h.text.textContent);
});
test('inactive failed target is named and cannot open the active host picker',async()=>{
    const h=setup();
    evaluate(h.ctx,"undoStack[0].agentHostSettingsEdit.before={host:'codex',model:'',effort:null}");
    const pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    assert.equal(h.settings.disabled,true);
    assert.match(h.settings.title,/目标宿主未激活/);
    h.settings.click(); assert.equal(h.harness.dialogs.length,0);
    const reload=setup(h.harness.store); assert.equal(reload.settings.disabled,true);
});
test('one click returns from history host, waits for replay, then opens the active host picker',async()=>{
    const h=setup(), pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    const state=h.ctx.getAgentTuiState('target'); state.viewHost='codex';
    h.settings.click();
    assert.equal(state.viewHost,'pi'); assert.equal(state._replayInFlight,true);
    assert.match(h.calls.at(-1).url,/project\/agent-nodes\/target\/events\/page\?host=pi/);
    assert.equal(state.modelPicker,undefined);
    h.calls.at(-1).resolve({ok:true,json:async()=>({events:[],has_more:false})}); await flush();
    assert.equal(h.revealed(),'target'); assert.equal(state._replayInFlight,false);
    const picker=assertPicker(h); await finishPicker(h,picker);
});
test('settings button opens the failed node picker, not another selected node',async()=>{
    const h=setup(), pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    h.ctx.canvasState.selectedId='other'; h.settings.click();
    assert.equal(h.revealed(),'target');
    assert.equal(h.ctx.getAgentTuiState('other').modelPicker,undefined);
    const picker=assertPicker(h); await finishPicker(h,picker);
    assert.equal(h.bar.hidden,false);
});
test('notice for active Codex opens its own model menu',async()=>{
    const h=setup();
    h.ctx.canvasState.nodes[0].agentCli='codex';
    h.ctx.canvasState.nodes[0].agentHostSettings.codex={model:'chosen',effort:'high'};
    evaluate(h.ctx,"undoStack[0].agentHostSettingsEdit.before.host='codex'");
    const pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    h.settings.click(); const picker=assertPicker(h,'codex'); await finishPicker(h,picker,'codex');
});
test('an existing replay finishes before opening; genuine busy state shows a toast',async()=>{
    const h=setup(), pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    const state=h.ctx.getAgentTuiState('target'); state.projectId='project';
    h.ctx.replayAgentEvents('target',state); h.settings.click();
    assert.equal(state.modelPicker,undefined);
    h.calls.at(-1).resolve({ok:true,json:async()=>({events:[],has_more:false})}); await flush();
    const picker=assertPicker(h); await finishPicker(h,picker);
    h.ctx.closeAgentModelPicker(state); state.status='busy'; h.settings.click();
    assert.equal(state.modelPicker,null); assert.match(h.toasts.at(-1),/忙碌/);
});
test('queued catch-up replay also finishes before the picker opens',async()=>{
    const h=setup(), pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    const state=h.ctx.getAgentTuiState('target'); state.projectId='project';
    h.ctx.replayAgentEvents('target',state); h.ctx.replayAgentEvents('target',state);
    h.settings.click();
    h.calls.at(-1).resolve({ok:true,json:async()=>({events:[],has_more:false})}); await flush();
    assert.equal(state._replayInFlight,true); assert.equal(state.modelPicker,undefined);
    assert.match(h.calls.at(-1).url,/events\?since=0&host=pi/);
    h.calls.at(-1).resolve({ok:true,json:async()=>({events:[]})}); await flush();
    const picker=assertPicker(h); await finishPicker(h,picker);
});
test('a project switch during replay cannot open a stale picker',async()=>{
    const h=setup(), pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    const state=h.ctx.getAgentTuiState('target'); state.viewHost='codex'; h.settings.click();
    evaluate(h.ctx,"canvas.project_id='other-project'");
    h.calls.at(-1).resolve({ok:true,json:async()=>({events:[],has_more:false})}); await flush();
    assert.equal(state.modelPicker,undefined); assert.equal(h.calls.length,2);
});
test('deleted or replaced target cannot open settings; project/canvas switch scopes the notice',async()=>{
    const h=setup(), pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    evaluate(h.ctx,"canvasState.nodes=canvas.nodes=canvas.nodes.filter(n=>n.id!=='target')");
    h.ctx.syncAgentUndoNotice(); assert.equal(h.settings.disabled,true); h.settings.click(); assert.equal(h.harness.dialogs.length,0);
    evaluate(h.ctx,"canvasState.nodes.push({id:'target',type:'smart-prompt'}); canvas.project_id='other-project'");
    h.ctx.syncAgentUndoNotice(); assert.equal(h.bar.hidden,true); h.settings.click(); assert.equal(h.harness.dialogs.length,0);
    evaluate(h.ctx,"canvas.project_id='project'; canvas.id='other-canvas'"); h.ctx.syncAgentUndoNotice(); assert.equal(h.bar.hidden,true);
    evaluate(h.ctx,"canvas.id='cv'"); h.ctx.syncAgentUndoNotice(); assert.equal(h.bar.hidden,false); assert.equal(h.settings.disabled,true);
});
test('settings reveals a collapsed docked target',async()=>{
    const h=setup(), pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    h.ctx.canvasState.nodes[0].dock='right';
    evaluate(h.ctx,"agentDockCollapsed.add('target')");
    let synced=0; h.ctx.syncAgentDock=()=>{synced++;};
    h.settings.click();
    assert.equal(synced,1); assert.equal(evaluate(h.ctx,"agentDockCollapsed.has('target')"),false); assert.equal(h.revealed(),'');
    const picker=assertPicker(h); await finishPicker(h,picker);
});
test('navigation does nothing after a project switch',async()=>{
    const h=setup(), pending=h.ctx.performUndo(); fail(h.calls[0]); await pending;
    evaluate(h.ctx,"canvas.project_id='other-project'");
    h.settings.click(); assert.equal(h.revealed(),''); assert.equal(h.focused(),false);
});
test('late failure after project switch cannot display or persist a notice in either context',async()=>{
    const h=setup(), pending=h.ctx.performUndo();
    evaluate(h.ctx,"canvas.project_id='other-project'"); fail(h.calls[0]); await pending;
    h.ctx.syncAgentUndoNotice(); assert.equal(h.bar.hidden,true);
    evaluate(h.ctx,"canvas.project_id='project'"); h.ctx.syncAgentUndoNotice(); assert.equal(h.bar.hidden,true);
});
test('other failures still use existing toast, without creating this notice',async()=>{
    for(const code of ['agent_busy','agent_settings_conflict','not_found']){
        const h=setup(), stacks=h.stacks(), pending=h.ctx.performUndo(); fail(h.calls[0],code); await pending;
        assert.match(h.toasts.at(-1),new RegExp(code)); assert.equal(h.bar.hidden,true); assert.deepEqual(h.stacks(),stacks);
    }
});
