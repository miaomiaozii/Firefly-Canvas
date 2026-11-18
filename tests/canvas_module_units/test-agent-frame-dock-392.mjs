// #392 2b-2: real head/dock/drag/IME handlers, storage, delta undo and pending journal.
// Only browser/clock/network boundaries are fixtures; no ownership or undo replacements.
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {createCanvasContext, loadModules, evaluate, fromContext, JS_DIR} from './harness.mjs';

function declarations(file){
    const lines = readFileSync(path.join(JS_DIR, file), 'utf8').split(/\r?\n/), functions = [];
    for(let index = 0; index < lines.length; index++){
        if(!/^(?:async )?function \w+\(/.test(lines[index])) continue;
        const start = index;
        if(!/}\s*$/.test(lines[index])){
            while(++index < lines.length && !/^}/.test(lines[index])){}
            if(index === lines.length) throw new Error(`Unterminated declaration: ${lines[start]}`);
        }
        functions.push(lines.slice(start, index + 1).join('\n'));
    }
    return functions.join('\n');
}
const ownership = {createdBy:'agent', agentNodeId:' opaque creator ', userTouchedAt:7, promptEditedByUser:false};
const agent = (id, extra={}) => ({id, type:'smart-agent', x:10, y:20, w:382, title:'Assistant', ...ownership, ...extra});
const event = (type, extra={}) => ({type, preventDefault(){}, stopPropagation(){}, ...extra});
function editor(nodes){
    const {ctx, harness} = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'project-canvas-mutations.js',
        'canvas-layout-utils.js', 'canvas-connections.js', 'canvas-media-history.js', 'canvas-undo.js',
        'canvas-node-ops.js', 'canvas-agent-tui.js']);
    vm.runInContext(readFileSync(path.join(JS_DIR, 'smart-canvas.js'), 'utf8').match(/^const SMART_ONLINE_ENGINES = .+;$/m)[0], ctx);
    vm.runInContext(declarations('canvas-generation-cascade.js')+'\n'+declarations('canvas-generation-tasks.js')+'\n'+declarations('smart-canvas.js'), ctx);
    const initial = {id:'fixture-392', project_id:'fixture-392', format:'firefly-canvas-v1', revision:4,
        updated_at:400, title:'Agent frame and dock', nodes, connections:[], history:[]};
    const effects = [];
    Object.assign(ctx, {
        canvasId:initial.id, sourceProjectId:initial.id, canvasUsesConnections:true, canvasLoadReady:true,
        persistedCoreSnapshot:null, persistedDurableSnapshot:null, smartClientId:'frame-dock-test',
        contentDirty:false, debouncedSaveTimer:null, canvasSyncInFlight:false, canvasProjectGone:false,
        pendingRemoteUpdatedAt:0, pendingAgentMergeFingerprint:'', pendingAgentMergeUndo:false,
        conversationCanvasLastAcceptedRevision:0, smartGenerationNodeRevisions:new Map(), smartNodeRunTokens:new Map(), smartRunTokens:new Map(),
        projectMediaImportCache:new Map(), activeProjectMediaImports:0, projectMediaImportWaiters:[],
        SMART_LOG_PREVIEW_NODE_ID:'__preview__', DEBOUNCED_SAVE_MS:3000, viewport:{x:0,y:0,scale:1}, canvasDefaultSmartSettings:{}, initialSmartSettings:{},
        smartSaveButton:null, smartHistoryModal:null, promptInput:null, assetLibraryOpen:false, actionTime:900,
        // Rendering records what the real serializer sees at the first publication boundary.
        render(){effects.push({kind:'render', canvas:fromContext(ctx.canvasForStorage())});},
        centerViewportOnWorldPoint(point){effects.push({kind:'center', point});},
        applyViewport(){}, setSmartTitleText(){}, scheduleConnectionLayerRefresh(){}, scheduleSmartHistoryWindowRender(){},
        resumeSmartPendingTasks(){}, resumeSmartPersistencePendingTasks(){}, resumeSmartPendingActionResumes(){},
        toast(message){harness.toasts.push(String(message));},
        CSS:{escape:String}, innerWidth:1200,
        WebSocket:class {static OPEN=1; constructor(){this.readyState=1;} send(){} close(){}},
    });
    // Actual DOM event removal, needed to distinguish separate drags/cancellation.
    ctx.removeEventListener = (type, fn) => harness.windowListeners.set(type, (harness.windowListeners.get(type) || []).filter(item => item !== fn));
    evaluate(ctx, `canvas=${JSON.stringify(initial)}; canvasState.nodes=canvas.nodes; Date.now=()=>actionTime;`);
    ctx.persistedCoreSnapshot = ctx.FireflyProjectCanvasMutations.snapshotFromCanvas(initial);
    ctx.persistedDurableSnapshot = ctx.FireflyProjectCanvasMutations.durableSnapshotFromCanvas(initial);
    const e = {
        ctx, harness, effects,
        node:id => ctx.canvasState.nodes.find(node => node.id === id),
        stored:() => fromContext(ctx.canvasForStorage()),
        undo:() => fromContext(evaluate(ctx, 'undoStack')),
        pending:() => ctx.smartCanvasPendingState().journal.hasPending(),
        head(id, attribute, side){
            const button = harness.makeElement('button');
            button.dataset[attribute] = id;
            if(side !== undefined) button.dataset.agentDockSide = side;
            const root = harness.makeElement();
            const selector = `[data-${attribute.replace(/[A-Z]/g, letter => '-'+letter.toLowerCase())}]`;
            button.setAttribute(selector.slice(1, -1), id);
            const menu = harness.makeElement('details');
            button.closest = query => query === '.agent-tui-more' ? menu : null;
            root.querySelectorAll = query => query.split(',').includes(selector) ? [button] : [];
            ctx.bindAgentHeadControls(root, e.node(id) || {id});
            button.dispatchEvent(event('click'));
            return button;
        },
        input(id){
            const input = harness.makeElement('textarea'), root = harness.makeElement();
            root.querySelector = query => query === '[data-agent-tui-input]' ? input : null;
            ctx.bindAgentNodeControls(root, e.node(id));
            return input;
        },
        drag(id){
            const root = harness.makeElement(), head = harness.makeElement();
            root.querySelector = query => query === '.node-head' ? head : null;
            root.getBoundingClientRect = () => ({left:20, top:50});
            head.closest = () => null;
            ctx.shell = harness.element('shell');
            ctx.shell.getBoundingClientRect = () => ({left:100, top:50});
            harness.element('world').getBoundingClientRect = () => ({left:200, right:1000, top:100, bottom:800});
            ctx.viewport = {x:20, y:30, scale:2};
            ctx.bindAgentDockDragOut(root, id);
            return {root, start(clientX=50, clientY=80){head.dispatchEvent(event('mousedown', {button:0, target:head, clientX, clientY}));}};
        },
        fireWindow(type, extra={}){(harness.windowListeners.get(type) || []).slice().forEach(fn => fn(event(type, extra)));},
    };
    return e;
}

test('reference head cycles and unsets only Agent bindings with one delta each; no frames and missing targets are negative', async()=>{
    const e = editor([agent('A', {createdBy:'user', agentNodeId:null}),
        {id:'G1', type:'smart-group', refFrame:true, ...ownership},
        {id:'G2', type:'smart-group', refFrame:true, ...ownership}]);
    for(const [time, expected] of [[901, 'G1'], [902, 'G2'], [903, undefined]]){
        e.ctx.actionTime = time;
        e.head('A', 'agentRefFrame');
        assert.equal(e.node('A').refFrameId, expected);
        assert.equal(e.node('A').userTouchedAt, time, 'user-origin nodes also receive T');
        assert.deepEqual([e.node('A').createdBy, e.node('A').agentNodeId, e.node('A').promptEditedByUser], ['user', null, false]);
        assert.equal(e.effects.at(-1).canvas.nodes.find(node => node.id === 'A').userTouchedAt, time);
    }
    assert.equal(e.undo().length, 3);
    assert.equal(e.undo().every(snapshot => snapshot.actionDelta.changes.every(change => change.path[1].id === 'A')), true);
    assert.deepEqual(e.stored().nodes.slice(1).map(node => node.userTouchedAt), [7, 7]);
    await e.ctx.performUndo();
    assert.equal(e.node('A').refFrameId, 'G2'); assert.equal(e.node('A').userTouchedAt, 902);
    await e.ctx.performRedo();
    assert.equal(e.node('A').refFrameId, undefined); assert.equal(e.node('A').userTouchedAt, 903);
    e.node('G1').refFrame = e.node('G2').refFrame = false;
    const before = e.stored(), history = e.undo(); e.effects.length = 0;
    const button = e.head('A', 'agentRefFrame');
    assert.match(button.title, /暂无参考框/);
    e.head('missing', 'agentRefFrame'); e.head('G1', 'agentRefFrame');
    assert.deepEqual(e.stored(), before);
    assert.deepEqual(e.undo(), history);
    assert.equal(e.effects.length, 0);
});

test('dock head replaces only changed targets with one shared time and delta undo; same values and automatic calls never stamp', async()=>{
    const e = editor([agent('A', {dock:null, createdBy:'user', agentNodeId:null, promptEditedByUser:true}), agent('B', {dock:'left'}), agent('C', {dock:null})]);
    e.ctx.actionTime = 899; evaluate(e.ctx, 'Date.now=()=>++actionTime');
    e.head('A', 'agentDockSet', 'left');
    evaluate(e.ctx, 'Date.now=()=>actionTime');
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [['left', 900], [null, 900], [null, 7]]);
    assert.deepEqual(e.stored().nodes.map(node => [node.x, node.y]), [[10, 20], [10, 20], [10, 20]]);
    assert.deepEqual([e.node('A').createdBy, e.node('A').agentNodeId, e.node('A').promptEditedByUser], ['user', null, true]);
    assert.equal(e.undo().length, 1);
    assert.equal(e.undo()[0].actionDelta.changes.length, 4, 'only two dock fields and their two T fields');
    assert.deepEqual(e.effects[0].canvas.nodes.map(node => node.userTouchedAt), [900, 900, 7]);
    const before = e.stored(), history = e.undo(); e.ctx.actionTime = 901; e.effects.length = 0;
    e.head('A', 'agentDockSet', 'left');
    e.head('missing', 'agentDockSet', 'right');
    assert.deepEqual(e.stored(), before); assert.deepEqual(e.undo(), history); assert.equal(e.effects.length, 0);
    e.node('C').title = 'unrelated remote title';
    await e.ctx.performUndo();
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [[null, 7], ['left', 7], [null, 7]]);
    assert.equal(e.node('C').title, 'unrelated remote title');
    await e.ctx.performRedo();
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [['left', 900], [null, 900], [null, 7]]);
    e.ctx.actionTime = 902; e.head('A', 'agentDockUndock');
    assert.equal(e.node('A').dock, null); assert.equal(e.node('A').userTouchedAt, 902);
    assert.equal(e.undo().length, 2);
    e.head('A', 'agentDockUndock');
    assert.equal(e.undo().length, 2, 'already floating is no action');
    const auto = editor([agent('A', {dock:null})]);
    auto.ctx.agentDockSet('A', 'right');
    assert.equal(auto.node('A').dock, 'right'); assert.equal(auto.node('A').userTouchedAt, 7);
    assert.equal(auto.undo().length, 0, 'no implicit actor from the setter');
});

test('IME head queues intent only and the real compositionend seals replacement with execution-time T and undo baseline', async()=>{
    const e = editor([agent('A', {dock:'left'}), agent('B', {dock:null})]);
    const input = e.input('A'); input.value = '中文草稿';
    input.dispatchEvent(event('compositionstart'));
    const before = e.stored();
    e.head('B', 'agentDockSet', 'right');
    assert.deepEqual(e.stored(), before); assert.equal(e.undo().length, 0); assert.equal(e.pending(), false);
    assert.deepEqual(fromContext(e.ctx.getAgentTuiState('A').pendingDock), {nodeId:'B', dockSide:'right'});
    e.node('A').userTouchedAt = 80; e.node('B').userTouchedAt = 81;
    e.ctx.actionTime = 950;
    input.dispatchEvent(event('compositionend'));
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [[null, 950], ['right', 950]]);
    assert.equal(e.ctx.getAgentTuiState('A').pendingDock, undefined);
    assert.equal(e.ctx.getAgentTuiState('A').draft, '中文草稿');
    assert.equal(e.undo().length, 1); assert.equal(e.pending(), true);
    await e.ctx.performUndo();
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [['left', 80], [null, 81]]);
    await e.ctx.performRedo();
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [[null, 950], ['right', 950]]);
    e.ctx.agentDockSet('A', 'left');
    assert.deepEqual(e.stored().nodes.map(node => node.userTouchedAt), [950, 950], 'consumed actor cannot leak into an automatic setter');
    assert.equal(e.undo().length, 1);
});

test('dragging dock head commits actual dock and world coordinates once, with T in the same delta undo', async()=>{
    const e = editor([agent('A', {dock:'left', promptEditedByUser:true})]);
    const drag = e.drag('A'); drag.start();
    e.fireWindow('mousemove', {clientX:300, clientY:300});
    e.fireWindow('mousemove', {clientX:500, clientY:450});
    assert.deepEqual([e.node('A').dock, e.node('A').x, e.node('A').y, e.node('A').userTouchedAt], ['left', 10, 20, 7]);
    assert.equal(e.undo().length, 0); assert.equal(e.pending(), false);
    e.ctx.actionTime = 980;
    e.fireWindow('mouseup', {clientX:600, clientY:500});
    assert.deepEqual([e.node('A').dock, e.node('A').x, e.node('A').y, e.node('A').userTouchedAt], [null, 220, 190, 980]);
    assert.equal(e.effects[0].canvas.nodes[0].userTouchedAt, 980);
    assert.deepEqual([e.node('A').createdBy, e.node('A').agentNodeId, e.node('A').promptEditedByUser], ['agent', ' opaque creator ', true]);
    assert.equal(e.undo().length, 1); assert.equal(e.undo()[0].actionDelta.changes.length, 4);
    await e.ctx.performUndo();
    assert.deepEqual([e.node('A').dock, e.node('A').x, e.node('A').y, e.node('A').userTouchedAt], ['left', 10, 20, 7]);
    await e.ctx.performRedo();
    assert.deepEqual([e.node('A').dock, e.node('A').x, e.node('A').y, e.node('A').userTouchedAt], [null, 220, 190, 980]);
});

test('cancelled, unmoved, return-to-start and outside-world dock drags have no durable edit or history', ()=>{
    for(const mode of ['unmoved', 'below-threshold', 'return-to-start', 'outside', 'pointercancel', 'blur', 'missing', 'no-field-delta']){
        const e = editor([agent('A', {dock:'left'})]);
        const drag = e.drag('A'); drag.start(350, 200);
        if(mode === 'no-field-delta') Object.assign(e.node('A'), {dock:null, x:220, y:190});
        if(mode === 'missing') e.ctx.canvasState.nodes.splice(0, 1);
        const before = e.stored();
        if(mode !== 'unmoved' && mode !== 'below-threshold') e.fireWindow('mousemove', {clientX:600, clientY:500});
        if(mode === 'below-threshold') e.fireWindow('mousemove', {clientX:353, clientY:202});
        if(mode === 'pointercancel' || mode === 'blur') e.fireWindow(mode);
        const point = mode === 'outside' ? {clientX:1100, clientY:900}
            : ['unmoved', 'below-threshold', 'return-to-start'].includes(mode) ? {clientX:350, clientY:200} : {clientX:600, clientY:500};
        e.fireWindow('mouseup', point);
        assert.deepEqual(e.stored(), before, mode);
        assert.equal(e.undo().length, 0, mode); assert.equal(e.pending(), false, mode);
        assert.equal(drag.root.classList.contains('drag-out'), false, mode);
        assert.equal((e.harness.windowListeners.get('mousemove') || []).length, 0, mode+' removes listeners');
    }
});

test('stale, deleted, no-op and automatic IME continuations cannot stamp or leak a manual actor', ()=>{
    for(const mode of ['cancelled-by-new-noop', 'deleted', 'became-noop', 'automatic']){
        const e = editor([agent('A', {dock:'left'}), agent('B', {dock:null})]);
        const input = e.input('A'); input.dispatchEvent(event('compositionstart'));
        if(mode === 'automatic') e.ctx.agentDockSet('B', 'right');
        else e.head('B', 'agentDockSet', 'right');
        if(mode === 'cancelled-by-new-noop') e.head('A', 'agentDockSet', 'left');
        if(mode === 'deleted') e.ctx.canvasState.nodes.splice(1, 1);
        if(mode === 'became-noop') {e.node('A').dock = null; e.node('B').dock = 'right';}
        const before = e.stored(); e.ctx.actionTime = 1100;
        input.dispatchEvent(event('compositionend'));
        if(mode !== 'automatic') assert.deepEqual(e.stored(), before, mode);
        else assert.deepEqual(e.stored().nodes.map(node => node.dock), [null, 'right']);
        assert.equal(e.undo().length, 0, mode);
        assert.equal(e.ctx.getAgentTuiState('A').pendingDock, undefined);
        assert.deepEqual(e.stored().nodes.map(node => node.userTouchedAt), mode === 'deleted' ? [7] : [7, 7], mode);
        e.ctx.agentDockSet('A', 'right');
        assert.equal(e.undo().length, 0, mode+' does not leak history');
        assert.deepEqual(e.stored().nodes.map(node => node.userTouchedAt), mode === 'deleted' ? [7] : [7, 7], mode+' does not leak actor');
    }
});

test('IME manual intent survives another composing blocker without an early snapshot or timestamp', ()=>{
    const e = editor([agent('A', {dock:'left'}), agent('B', {dock:null})]);
    const aInput = e.input('A'), bInput = e.input('B');
    aInput.dispatchEvent(event('compositionstart')); bInput.dispatchEvent(event('compositionstart'));
    e.head('B', 'agentDockSet', 'left');
    e.ctx.actionTime = 1200; bInput.dispatchEvent(event('compositionend'));
    assert.deepEqual(e.stored().nodes.map(node => node.userTouchedAt), [7, 7]); assert.equal(e.undo().length, 0);
    assert.deepEqual(fromContext(e.ctx.getAgentTuiState('A').pendingDock), {nodeId:'B', dockSide:'left'});
    e.ctx.actionTime = 1300; aInput.dispatchEvent(event('compositionend'));
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [[null, 1300], ['left', 1300]]);
    assert.equal(e.undo().length, 1);
});

test('dock normalization, sync and collapse remain automatic or memory-only, never manual edits', ()=>{
    const e = editor([agent('A', {dock:'left', dockCollapsed:true}), agent('B', {dock:'right'}),
        {id:'G', type:'smart-group', dock:'left', ...ownership}]);
    const slot = e.harness.element('agentDock'), panel = e.harness.makeElement(); panel.dataset.id = 'A';
    slot.querySelectorAll = query => query === '.agent-dock-node' ? [panel] : [];
    e.harness.querySelectorAll('#agentDock .agent-dock-node[data-id="A"]', [panel]);
    e.harness.element('shell').style.setProperty = () => {};
    e.ctx.innerWidth = 800;
    e.ctx.syncAgentDock();
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [['left', 7], [null, 7], [null, 7]]);
    assert.equal(e.node('A').dockCollapsed, undefined);
    assert.equal(e.ctx.agentDockIsCollapsed('A'), true);
    const before = e.stored();
    e.head('A', 'agentDockCollapse');
    assert.equal(e.ctx.agentDockIsCollapsed('A'), false);
    e.ctx.syncAgentDock();
    assert.equal(e.ctx.agentDockIsCollapsed('A'), false, 'manual expansion persists only in memory');
    e.head('A', 'agentDockCollapse');
    assert.equal(e.ctx.agentDockIsCollapsed('A'), true);
    e.ctx.normalizeAgentDockNodes(e.ctx.canvasState.nodes);
    assert.deepEqual(e.stored(), before);
    assert.equal(e.undo().length, 0); assert.equal(e.effects.length, 0); assert.equal(e.ctx.contentDirty, false);
});

test('an in-flight dock save followed by undo retains compensating dock and T through the old ACK', async()=>{
    const e = editor([agent('A', {dock:null})]);
    e.head('A', 'agentDockSet', 'left');
    let release, started;
    const ready = new Promise(resolve => {started = resolve;}), wait = new Promise(resolve => {release = resolve;}), sent = [];
    e.ctx.fetch = async(url, options) => {
        assert.equal(options.method, 'PATCH', 'no Agent host/session RPC');
        assert.equal(url.endsWith('/canvas'), true);
        sent.push(JSON.parse(options.body));
        if(sent.length === 1) {started(); await wait;}
        return {ok:true, status:200, json:async()=>({saved:true, revision:4+sent.length, updated_at:400+100*sent.length})};
    };
    const saving = e.ctx.saveCanvas();
    await Promise.race([ready, saving.then(() => assert.fail('save ended before sending: '+JSON.stringify(e.harness.toasts)))]);
    assert.deepEqual([sent[0].mutations.find(item => item.node?.id === 'A').node.dock, sent[0].mutations.find(item => item.node?.id === 'A').node.userTouchedAt], ['left', 900]);
    await e.ctx.performUndo();
    assert.deepEqual([e.node('A').dock, e.node('A').userTouchedAt], [null, 7]);
    release(); assert.equal(await saving, true, JSON.stringify(e.harness.toasts));
    assert.deepEqual([e.node('A').dock, e.node('A').userTouchedAt], [null, 7]); assert.equal(e.pending(), true);
    assert.equal(await e.ctx.saveCanvas(), true, JSON.stringify(e.harness.toasts));
    const compensation = sent[1].mutations.find(item => item.node?.id === 'A').node;
    assert.deepEqual([compensation.dock, compensation.userTouchedAt, compensation.promptEditedByUser], [null, 7, false]);
    assert.equal(e.pending(), false);
    await e.ctx.performRedo();
    assert.deepEqual([e.node('A').dock, e.node('A').userTouchedAt], ['left', 900]);
});

test('an already-correct dock target is not stamped when only another dock needs displacement', async()=>{
    const e = editor([agent('A', {dock:'left'}), agent('B', {dock:'right', promptEditedByUser:true})]);
    e.head('A', 'agentDockSet', 'left');
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [['left', 7], [null, 900]]);
    assert.equal(e.node('B').promptEditedByUser, true);
    assert.equal(e.undo().length, 1);
    assert.equal(e.undo()[0].actionDelta.changes.every(change => change.path[1].id === 'B'), true);
    await e.ctx.performUndo();
    assert.deepEqual(e.stored().nodes.map(node => [node.dock, node.userTouchedAt]), [['left', 7], ['right', 7]]);
});

test('stale frame binding on a legacy user node is one real creation edit without normalizing its identity', ()=>{
    const e = editor([{id:'A', type:'smart-agent', x:0, y:0, chatFrameId:'gone'}, {id:'G', type:'smart-group'}]);
    e.head('A', 'agentChatFrame');
    assert.equal(e.node('A').userTouchedAt, 900);
    assert.equal(Object.hasOwn(e.node('A'), 'createdBy'), false);
    assert.equal(Object.hasOwn(e.node('A'), 'agentNodeId'), false);
    assert.equal(Object.hasOwn(e.node('A'), 'promptEditedByUser'), false);
    const before = e.stored(), history = e.undo(); e.effects.length = 0;
    e.head('G', 'agentChatFrame'); e.head('missing', 'agentChatFrame');
    e.head('G', 'agentDockSet', 'right');
    assert.deepEqual(e.stored(), before); assert.deepEqual(e.undo(), history); assert.equal(e.effects.length, 0);
});

// Slice 1: constructor default + creation seal + existing-frame negative.
test('head creates one initialized user frame and seals the existing Agent binding and T before publication', async()=>{
    const e = editor([agent('A', {promptEditedByUser:true})]);
    const ordinary = e.ctx.createSmartGroupNode(1, 2);
    assert.deepEqual([ordinary.createdBy, ordinary.agentNodeId, ordinary.userTouchedAt, ordinary.promptEditedByUser], ['user', null, null, false]);
    assert.equal(ordinary.title, '智能分组');
    assert.equal(ordinary.chatFrame, undefined);
    assert.equal(e.effects.length, 1, 'default constructor still renders once');
    assert.equal(e.undo().length, 1);
    e.effects.length = 0;
    e.head('A', 'agentChatFrame');
    const frame = e.stored().nodes.find(node => node.chatFrameFor === 'A');
    assert.ok(frame);
    assert.deepEqual([frame.createdBy, frame.agentNodeId, frame.userTouchedAt, frame.promptEditedByUser], ['user', null, null, false]);
    assert.deepEqual([frame.x, frame.y, frame.title, frame.chatFrame], [432, 20, 'Assistant · 会话框', true]);
    assert.equal(e.node('A').chatFrameId, frame.id);
    assert.equal(e.node('A').userTouchedAt, 900);
    assert.deepEqual([e.node('A').createdBy, e.node('A').agentNodeId, e.node('A').promptEditedByUser], ['agent', ' opaque creator ', true]);
    assert.equal(e.effects.length, 1, 'one creation publication, not a partially initialized frame');
    assert.equal(e.effects[0].canvas.nodes.find(node => node.id === 'A').userTouchedAt, 900);
    assert.equal(e.effects[0].canvas.nodes.find(node => node.id === frame.id).chatFrameFor, 'A');
    assert.equal(e.undo().length, 2, 'only one additional creation undo');
    assert.equal(e.undo()[1].actionDelta.changes.some(change => change.path.at(-1) === 'userTouchedAt'), true);
    assert.equal(e.pending(), true);
    await e.ctx.performUndo();
    assert.equal(e.node(frame.id), undefined);
    assert.equal(e.node('A').chatFrameId, undefined);
    assert.equal(e.node('A').userTouchedAt, 7);
    assert.ok(e.node(ordinary.id));
    await e.ctx.performRedo();
    assert.equal(e.node('A').chatFrameId, frame.id);
    assert.equal(e.node('A').userTouchedAt, 900);
    assert.equal(e.node(frame.id).userTouchedAt, null);
    const before = e.stored(), history = e.undo();
    e.effects.length = 0; e.ctx.actionTime = 1000;
    e.head('A', 'agentChatFrame');
    assert.deepEqual(e.stored(), before, 'opening the frame only moves the viewport');
    assert.deepEqual(e.undo(), history);
    assert.equal(e.effects[0].kind, 'center');
});
