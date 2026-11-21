import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {createCanvasContext, loadModules, evaluate, globalFunction, fromContext, ROOT} from './harness.mjs';
import {editor, declarations} from './smart-human-handlers-harness-392.mjs';

const actor = () => ({id:'agent', type:'smart-agent', agentCli:'pi'});
const target = id => ({id, type:'smart-prompt', text:'draft', x:10, y:20, w:200, h:100});
const click = () => ({type:'click', preventDefault(){}, stopPropagation(){}});
function surface(ctx, harness, id) {
    const element = harness.makeElement();
    element.dataset.id = id;
    element.appendChild(harness.makeElement('textarea'));
    return element;
}
function setup({nodes = [actor(), target('a'), target('b')], tui = false} = {}) {
    const {ctx, harness} = createCanvasContext();
    ctx.document.createElementNS = (_namespace, tag) => harness.makeElement(tag);
    loadModules(ctx, tui
        ? ['canvas-node-types.js','canvas-core.js','canvas-state.js','canvas-agent-activity.js','canvas-agent-tui.js']
        : ['canvas-agent-activity.js']);
    let project = 'project';
    let currentNodes = nodes;
    let elements = nodes.map(node => surface(ctx,harness,node.id));
    const pans = [], renders = [];
    const button = harness.makeElement('button');
    const api = ctx.canvasAgentActivity;
    const callbacks = {getProjectId:() => project, getNodes:() => currentNodes, getElements:() => elements,
        isTaskNode:node => node.type==='agent-task', focusTargets: ids => pans.push(fromContext(ids)), followButton:button};
    api.configure(callbacks);
    if (tui) {
        ctx.canvasState.nodes = nodes;
        ctx.renderAgentNode = id => renders.push(id);
        ctx.canvasId = 'project';
        const state = globalFunction(ctx,'getAgentTuiState')('agent');
        state.projectId = 'project';
    }
    const phase = (phase, op_id = 'op1', touched_node_ids = ['a'], extra = {}, options = {}) => api.consumeCanvasUpdate({
        type:'canvas_updated', source:'agent', canvas_id:project, phase, op_id, touched_node_ids, ...extra,
    }, options);
    const confirm = (type, confirm_id, target_node_ids = ['a'], extra = {}) => api.consumeConfirmation({
        project_id:project, node_id:'agent', host:'pi', event:{type,confirm_id,target_node_ids,...extra},
    });
    const parts = id => {
        const el = elements.find(element => element.dataset.id === id);
        const root = el.children.find(child => child.className === 'canvas-agent-activity');
        return root && {root, label:root.children[1], text:root.children[1].children[1], info:root.children[1].children[0], mark:root.children[2], element:el};
    };
    return {ctx,harness,api,phase,confirm,parts,button,pans,renders,
        nodes:()=>currentNodes, elements:()=>elements,
        project:value => {project=value;},
        replaceNodes:value => {currentNodes=value; if(tui) ctx.canvasState.nodes=value;},
        replaceElements:value => {elements=value;},
        callbacks};
}
function fire(timer) { timer.fired = true; timer.fn(...timer.args); }
const activeTimers = harness => harness.timers.filter(timer => !timer.fired && !timer.cancelled && timer.ms===1200);

function canvasSetup() {
    const m = editor([actor(),target('a'),target('b')]);
    const {ctx,harness} = m;
    ctx.document.createElementNS = (_namespace, tag) => harness.makeElement(tag);
    loadModules(ctx,['canvas-agent-activity.js','canvas-agent-tui.js']);
    const elements = ['agent','a','b'].map(id => surface(ctx,harness,id));
    harness.querySelectorAll('.image-node[data-id]', elements);
    harness.element('smartAgentFollowToggle','button');
    ctx.pans = [];
    ctx.centerViewportOnWorldPoint = point => ctx.pans.push(fromContext(point));
    ctx.nodeRect = node => ({x:node.x,y:node.y,width:200,height:100});
    ctx.smartCanvasAgentActivityBridge();
    evaluate(ctx,'canvas.updated_at=100');
    ctx.canvasSyncTimer=null;
    const phase = (phase, op_id='op1', touched_node_ids=['a'], extra={}) => ctx.handleCanvasUpdatedMessage({
        type:'canvas_updated', source:'agent', canvas_id:'unit-392', phase, op_id, touched_node_ids,
        updated_at:1, ...extra,
    });
    const root = id => elements.find(el => el.dataset.id===id).children.find(child=>child.className==='canvas-agent-activity');
    return {...m,elements,phase,root};
}

test('common receiver ignores cross-project phases before activity or legacy undo mutation', () => {
    const m=canvasSetup();
    m.ctx.canvasAgentActivity.setFollowing(true);
    const before=evaluate(m.ctx,'JSON.stringify(canvasState.nodes)');
    m.phase('start','foreign',['a'],{canvas_id:'other',updated_at:900});
    assert.equal(m.root('a'),undefined);
    assert.equal(m.ctx.pendingAgentMergeUndo,false);
    assert.equal(m.ctx.pendingAgentMergeFingerprint,'');
    assert.equal(m.harness.timers.length,0);
    assert.deepEqual(m.ctx.pans,[]);
    assert.equal(evaluate(m.ctx,'JSON.stringify(canvasState.nodes)'),before);
});

test('old-timestamp, self-client and in-flight-save starts paint before fetch suppression; undo coalescing stays armed', () => {
    for(const guard of ['timestamp','client','save']) {
        const m=canvasSetup();
        evaluate(m.ctx,'canvas.updated_at=100');
        if(guard==='save') m.ctx.canvasSyncInFlight=true;
        const before=evaluate(m.ctx,'JSON.stringify(canvasState.nodes)');
        m.phase('start','old',['a'],guard==='client'?{client_id:'unit-392'}:{});
        assert.equal(m.root('a').classList.contains('is-modifying'),true,guard);
        assert.equal(m.ctx.pendingAgentMergeUndo,true);
        assert.ok(m.ctx.pendingAgentMergeFingerprint.includes('draft'));
        assert.equal(m.renders.length,0,'phase paint never calls render');
        assert.equal(m.harness.timers.length,0,'old/self/saving start never fetches');
        assert.equal(evaluate(m.ctx,'JSON.stringify(canvasState.nodes)'),before);
    }
});

test('real merge reload coalesces multiple Agent phase broadcasts into one legacy undo entry', async () => {
    const m=canvasSetup();
    m.ctx.dragState=null;
    m.ctx.selectionState=null;
    const server=evaluate(m.ctx,'canvasForStorage()');
    server.updated_at=120;
    server.nodes[1].text='authoritative change';
    const fetches=[];
    m.ctx.fetch=async url=>{fetches.push(url); return {ok:true,json:async()=>({canvas:server})};};
    m.phase('start','one',['a']);
    m.phase('end','one',[],{updated_at:110});
    m.phase('start','two',['b'],{updated_at:110});
    m.phase('end','two',[],{updated_at:120});
    assert.equal(fetches.length,0);
    const reloads=m.harness.timers.filter(timer=>timer.ms===200 && !timer.cancelled && !timer.fired);
    assert.equal(reloads.length,1);
    fire(reloads[0]);
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(fetches.length,1);
    assert.equal(m.renders.length,1);
    assert.equal(evaluate(m.ctx,'undoStack.length'),1);
    assert.equal(m.ctx.pendingAgentMergeUndo,false);
    assert.equal(evaluate(m.ctx,'canvasState.nodes.find(node=>node.id==="a").text'),'authoritative change');
});

test('empty end uses original targets, fades for 1.2s and never invents failure/success/diff', () => {
    const m=setup();
    m.phase('start');
    const initial=m.parts('a');
    const originalBody=initial.element.children[0];
    const before=JSON.stringify(m.nodes());
    m.phase('end','op1',[]);
    assert.equal(m.parts('a').root,initial.root);
    assert.equal(m.parts('a').root.classList.contains('is-ending'),true);
    assert.equal(m.parts('a').text.textContent,'修改中');
    assert.equal(m.parts('a').mark.hidden,true);
    assert.equal(m.parts('a').root.classList.contains('is-failed'),false);
    assert.equal(activeTimers(m.harness).length,1);
    m.phase('end','op1',[]);
    assert.equal(activeTimers(m.harness).length,1,'duplicate end does not extend fade');
    fire(activeTimers(m.harness)[0]);
    assert.equal(m.parts('a').root.hidden,true);
    assert.equal(m.parts('a').element.children[0],originalBody,'body/IME/anchors retain identity');
    assert.equal(JSON.stringify(m.nodes()),before,'no persistent node fields');
});

test('overlapping operations on the same node cannot clear each other or be cleared by unrelated undo', () => {
    const m=setup();
    m.phase('start','one',['a']);
    m.phase('start','two',['a','b']);
    m.phase('end','one',[]);
    assert.equal(m.parts('a').root.classList.contains('is-ending'),false);
    fire(activeTimers(m.harness)[0]);
    assert.equal(m.parts('a').root.classList.contains('is-modifying'),true);
    m.phase('undo','one',['a']);
    assert.equal(m.parts('a').root.classList.contains('is-modifying'),true);
    m.phase('end','two',[]);
    assert.equal(m.parts('a').root.classList.contains('is-ending'),true);
    assert.equal(m.parts('b').root.classList.contains('is-ending'),true);
    fire(activeTimers(m.harness)[0]);
    assert.equal(m.parts('a').root.hidden,true);
    assert.equal(m.parts('b').root.hidden,true);
});

test('undo clears its own active operation and matching authoritative marks, without a completed badge', () => {
    const m=setup();
    m.phase('start','one',['a']);
    m.phase('start','two',['b']);
    m.api.setDiffMarks('project',[{node_id:'a',kind:'added',op_id:'one'}, {node_id:'b',kind:'modified',op_id:'two'}]);
    m.phase('undo','one',['a']);
    assert.equal(m.parts('a').root.hidden,true);
    assert.equal(m.parts('b').root.classList.contains('is-modifying'),true);
    m.phase('undo','',['b']);
    assert.equal(m.parts('b').mark.hidden,true);
    assert.equal(m.parts('b').root.classList.contains('is-modifying'),true,'turn undo is not a cancellation of an unrelated live op');
});

test('start targets that arrive only after authority merge are painted on world and existing dock shells', () => {
    const m=canvasSetup();
    const dock=m.elements[0];
    dock.appendChild(m.harness.makeElement('input'));
    const original=dock.children[0];
    m.phase('start','new',['new-node','agent']);
    const fresh=surface(m.ctx,m.harness,'new-node');
    m.elements.push(fresh);
    m.harness.querySelectorAll('.image-node[data-id]',m.elements);
    const accepted=evaluate(m.ctx,'canvasForStorage()');
    accepted.updated_at=120;
    accepted.nodes.push(target('new-node'));
    m.ctx.render=()=>{m.ctx.smartCanvasAgentActivityBridge()?.sync();};
    m.ctx.canvasAgentActivity.setFollowing(true);
    assert.equal(m.ctx.applyMergedServerCanvas(accepted),true);
    assert.equal(fresh.children[1].classList.contains('is-modifying'),true);
    assert.equal(m.root('agent').classList.contains('is-modifying'),true);
    assert.equal(dock.children[0],original,'dock composer is not replaced');
    assert.deepEqual(m.ctx.pans,[],'authority merge never pans even for newly arrived nodes');
});

test('real world render re-applies activity after dock sync without rebuilding a surviving dock body', () => {
    const m=canvasSetup();
    m.phase('start','dock',['agent']);
    const dock=m.elements[0], original=dock.children[0];
    // Real render with no world nodes isolates the dock-skipping branch; geometry/bodies are not emulated.
    vm.runInContext(declarations('smart-canvas.js'),m.ctx);
    const realRender=m.ctx.render;
    const calls=[];
    m.ctx.canvasState.nodes=[];
    m.ctx.world.childNodes=[];
    m.ctx.world.insertAdjacentHTML=()=>{};
    m.ctx.document.activeElement=null;
    m.ctx.document.createElement=tag=>{
        const el=m.harness.makeElement(tag);
        if(tag==='template') el.content={querySelector:()=>null};
        return el;
    };
    m.ctx.composer=null;
    m.ctx.canvasEmptyState=null;
    m.ctx.SMART_LOG_PREVIEW_NODE_ID='preview';
    m.ctx.normalizeAgentDockNodes=()=>{};
    m.ctx.selectedNodeIds=()=>[];
    m.ctx.captureMediaPlaybackStates=()=>[];
    m.ctx.restoreMediaPlaybackStates=()=>{};
    m.ctx.renderConnections=()=>'';
    for(const name of ['bindNodeEvents','bindConnectionEvents','updateComposer','renderMinimap','refreshIcons','bindSmartPreviewImageFallbacks',
        'syncSmartSelectedImageResolution','measureSmartNodeImages','scheduleSmartNodeFloatingMenuPosition','refreshRunTimerPills','renderAnnotationLayer']) m.ctx[name]=()=>{};
    m.ctx.syncAgentDock=()=>{
        calls.push('dock');
        m.ctx.canvasState.nodes=[actor()];
    };
    const originalSync=m.ctx.canvasAgentActivity.sync;
    m.ctx.canvasAgentActivity.sync=()=>{calls.push('paint'); originalSync();};
    realRender();
    assert.deepEqual(calls,['dock','paint']);
    assert.equal(m.root('agent').classList.contains('is-modifying'),true);
    assert.equal(dock.children[0],original);
});

test('follow is OFF by default; toggle alone, replay, ordinary updates, sync and history never pan', () => {
    const m=setup();
    assert.equal(m.button.getAttribute('aria-pressed'),'false');
    assert.match(m.button.getAttribute('aria-label'),/关/);
    m.phase('start','off');
    m.button.dispatchEvent(click());
    assert.equal(m.button.getAttribute('aria-pressed'),'true');
    assert.match(m.button.getAttribute('aria-label'),/开/);
    assert.deepEqual(m.pans,[]);
    m.phase('start','replayed',['a'],{}, {replay:true});
    m.phase(undefined,'ordinary',['b']);
    m.api.sync();
    m.confirm('confirm','history',['b']);
    assert.deepEqual(m.pans,[]);
    m.phase('start','live',['a','b']);
    m.phase('start','live',['a','b']);
    assert.deepEqual(m.pans,[['a','b']],'one focus request for a real new start only');
    m.phase('end','live',[]);
    m.project('other');
    m.api.sync();
    assert.equal(m.button.getAttribute('aria-pressed'),'false');
    assert.equal(activeTimers(m.harness).length,0,'project switch cancels owned fade timers');
    m.phase('start','other-start');
    assert.equal(m.pans.length,1);
});

test('an opted-in creation waits for its authoritative target; disabling follow cancels pending focus', () => {
    const m=setup();
    m.api.setFollowing(true);
    m.phase('start','new-node',['new']);
    m.phase('end','new-node',[]);
    assert.deepEqual(m.pans,[], 'a missing target cannot be focused');
    m.replaceNodes([...m.nodes(),target('new')]);
    m.api.sync();
    m.api.sync();
    assert.deepEqual(m.pans,[['new']], 'only the preceding live start permits this merge-time focus');
    m.phase('start','cancelled',['later']);
    m.api.setFollowing(false);
    m.replaceNodes([...m.nodes(),target('later')]);
    m.api.setFollowing(true);
    m.api.sync();
    assert.deepEqual(m.pans,[['new']], 're-enabling never follows an older operation');
});

test('opt-in SC focus uses current geometry without selection/render; dock-only targets never pan', () => {
    const m=canvasSetup();
    const before=evaluate(m.ctx,'JSON.stringify([canvasState.selectedId,canvasState.selectedIds])');
    m.ctx.canvasAgentActivity.setFollowing(true);
    m.phase('start','one',['a']);
    assert.deepEqual(m.ctx.pans,[{x:110,y:70}]);
    assert.equal(evaluate(m.ctx,'JSON.stringify([canvasState.selectedId,canvasState.selectedIds])'),before);
    assert.equal(m.renders.length,0);
    evaluate(m.ctx,'canvasState.nodes[0].dock="right"');
    m.phase('start','two',['agent']);
    assert.equal(m.ctx.pans.length,1);
    m.ctx.smartCanvasAgentActivityBridge().sync();
    assert.equal(m.ctx.pans.length,1);
});

test('all pending confirmation IDs highlight real targets, not Agent; a terminal clears only its matching ID', () => {
    const m=setup();
    m.confirm('confirm','first',['a','a']);
    m.confirm('confirm','second',['a','b']);
    assert.equal(m.parts('agent'),undefined);
    assert.equal(m.parts('a').root.classList.contains('is-confirming'),true);
    assert.equal(m.parts('b').text.textContent,'待确认');
    assert.equal(m.parts('b').info.style.display,'');
    assert.equal(m.parts('b').info.children[0].getAttribute('href'),'#i-info');
    m.confirm('confirm_resolved','unrelated',['a']);
    assert.equal(m.parts('a').root.classList.contains('is-confirming'),true);
    m.confirm('confirm_resolved','first',[]);
    assert.equal(m.parts('a').root.classList.contains('is-confirming'),true);
    m.confirm('confirm_resolved','second',[]);
    assert.equal(m.parts('a').root.hidden,true);
    assert.equal(m.parts('b').root.hidden,true);
    m.confirm('confirm','first',['a']);
    assert.equal(m.parts('a').root.hidden,true,'old replay cannot resurrect a terminal ID');
});

test('real live/latest replay confirmation bridge retains every ID and ignores older/read-only/wrong-project transcripts', () => {
    const m=setup({tui:true});
    const state=globalFunction(m.ctx,'getAgentTuiState')('agent');
    const apply=globalFunction(m.ctx,'applyAgentEventBody');
    const live=globalFunction(m.ctx,'handleAgentEvent');
    live('agent',{type:'confirm',confirm_id:'first',target_node_ids:['a'],host:'pi'});
    state._applyingReplay=true;
    apply('agent',{type:'confirm',confirm_id:'second',target_node_ids:['b'],host:'pi'});
    state._applyingReplay=false;
    live('agent',{type:'confirm_resolved',confirm_id:'first',target_node_ids:[],host:'pi'});
    assert.equal(m.parts('a').root.hidden,true);
    assert.equal(m.parts('b').root.classList.contains('is-confirming'),true);
    state.viewHost='codex';
    apply('agent',{type:'confirm',confirm_id:'readonly',target_node_ids:['a'],host:'codex'});
    state.viewHost='pi';
    const project=state.projectId;
    delete state.projectId;
    state._applyingReplay=true;
    apply('agent',{type:'confirm',confirm_id:'older',target_node_ids:['a'],host:'pi'});
    state.projectId='foreign';
    apply('agent',{type:'confirm',confirm_id:'foreign',target_node_ids:['a'],host:'pi'});
    state.projectId=project;
    state._applyingReplay=false;
    assert.equal(m.parts('a').root.hidden,true);
    assert.deepEqual(m.pans,[]);
    live('agent',{type:'confirm_resolved',confirm_id:'second',target_node_ids:[],host:'pi'});
    assert.equal(m.parts('b').root.hidden,true);
});

test('confirmation authority removes deleted/changed-host actors; same ID from another actor stays pending', () => {
    const m=setup({nodes:[actor(),{...actor(),id:'other-agent'},target('a'),target('b')]});
    m.confirm('confirm','shared',['a']);
    m.api.consumeConfirmation({project_id:'project',node_id:'other-agent',host:'pi',event:{type:'confirm',confirm_id:'shared',target_node_ids:['b']}});
    m.confirm('confirm_resolved','shared',[]);
    assert.equal(m.parts('b').root.classList.contains('is-confirming'),true);
    m.replaceNodes([actor(),{...actor(),id:'other-agent',agentCli:'codex'},target('a'),target('b')]);
    m.api.sync();
    assert.equal(m.parts('b').root.hidden,true);
});

test('authoritative generation/failure status updates in place, shows safe reason and completed has no badge', () => {
    const m=setup();
    const node=m.nodes()[1];
    node.agentStatus='generating';
    m.api.sync();
    const parts=m.parts('a');
    assert.equal(parts.root.classList.contains('is-generating'),true);
    assert.equal(parts.label.hidden,true,'generation edge needs no extra badge');
    node.agentStatus='failed';
    node.lastFailure={message:'<img onerror=evil()> service unavailable'};
    m.api.sync();
    assert.equal(m.parts('a').root,parts.root);
    assert.equal(parts.root.classList.contains('is-generating'),false);
    assert.equal(parts.root.classList.contains('is-failed'),true);
    assert.equal(parts.text.textContent,'失败');
    assert.equal(parts.info.style.display,'none');
    assert.equal(parts.label.getAttribute('title'),node.lastFailure.message);
    assert.equal(parts.text.children.length,0,'untrusted reason is never HTML');
    node.agentStatus='succeeded';
    delete node.lastFailure;
    m.api.sync();
    assert.equal(parts.root.hidden,true);
    assert.equal(parts.label.hidden,true);
});

test('existing task status uses only authoritative running/failed and its reason, never uncertain or completed', () => {
    const task={id:'task',type:'agent-task',status:'running'};
    const m=setup({nodes:[actor(),task]});
    m.api.sync();
    const parts=m.parts('task');
    assert.equal(parts.root.classList.contains('is-generating'),true);
    task.status='failed'; task.error='Provider rejected input';
    m.api.sync();
    assert.equal(parts.root.classList.contains('is-generating'),false);
    assert.equal(parts.root.classList.contains('is-failed'),true);
    assert.equal(parts.label.getAttribute('title'),task.error);
    for(const status of ['uncertain','succeeded']) {
        task.status=status;
        m.api.sync();
        assert.equal(parts.root.hidden,true,status);
    }
});

test('D7 public diff API uses authoritative op IDs only and emits reveal ONLY from a mark click', () => {
    const m=setup();
    m.phase('start'); m.phase('end','op1',[]);
    assert.equal(m.parts('a').mark.hidden,true,'end is not proof of a successful edit');
    assert.equal(m.api.setDiffMarks('foreign',[{node_id:'a',kind:'added',op_id:'x'}]),false);
    const count=m.harness.dispatchedEvents.length;
    const mark={node_id:'a',kind:'modified',op_id:'real-op',turn_id:null};
    assert.equal(m.api.setDiffMarks('project',[mark,{node_id:'b',kind:'added',op_id:'real-create',turn_id:'real-turn'},
        {node_id:'agent',kind:'added'}, {node_id:'unknown',kind:'success',op_id:'x'}]),true);
    assert.equal(m.parts('a').mark.textContent,'~');
    assert.equal(m.parts('b').mark.textContent,'+');
    assert.equal(m.parts('agent'),undefined);
    assert.equal(m.harness.dispatchedEvents.length,count);
    m.parts('a').mark.dispatchEvent(click());
    const event=m.harness.dispatchedEvents.at(-1);
    assert.equal(event.type,'studio-canvas-agent-diff-reveal');
    assert.deepEqual(fromContext(event.detail),{project_id:'project',...mark});
    assert.equal(m.harness.timers.some(timer=>timer.ms!==1200),false,'no fetch/undo work is launched by mark reveal');
    m.api.setDiffMarks('project',[]);
    assert.equal(m.parts('a').mark.hidden,true);
    assert.equal(m.parts('b').root.hidden,true);
    m.project('other'); m.api.sync();
    assert.equal(m.api.setDiffMarks('project',[mark]),false);
});

test('static CSS preserves selection, has a 2.4s flow, 1.5px dashed ink, 2px danger and visible reduced-motion edges', () => {
    const css=readFileSync(path.join(ROOT,'static/css/smart-canvas.css'),'utf8');
    const activity=css.slice(css.indexOf('/* Independent edges'));
    assert.match(activity,/animation:canvas-agent-flow 2\.4s linear infinite/);
    assert.match(activity,/is-modifying[^}]+stroke:var\(--ink, var\(--text\)\); stroke-width:1\.5px; stroke-dasharray:5 4/);
    assert.match(activity,/activity-edge rect \{[^}]*stroke-width:2px/);
    assert.match(activity,/is-failed \.canvas-agent-activity-status \{ stroke:var\(--danger\)/);
    assert.match(activity,/font-size:max\(12px,/);
    assert.match(activity,/canvas-agent-overview[^}]+stroke-width:6px/s);
    const reduced=activity.slice(activity.indexOf('@media (prefers-reduced-motion:reduce)'));
    assert.match(reduced,/is-generating[^}]+animation:none; stroke-dasharray:none; stroke-width:2px/);
    assert.match(reduced,/is-ending[^}]+animation:none; opacity:1/s);
    assert.doesNotMatch(activity.replace(/\/\*[\s\S]*?\*\//g,''),/\.selected|innerHTML|#[0-9a-f]{3,8}\b/i);
});
