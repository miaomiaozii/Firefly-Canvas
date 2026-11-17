// #392 D3: real core predicates and actual undo/redo/history entry points.
// Only DOM, clock, and network edges are substituted.
import assert from 'node:assert/strict';
import test from 'node:test';
import {createCanvasContext, loadModules, evaluate, fromContext} from './harness.mjs';
import {editor, identity} from './smart-human-handlers-harness-392.mjs';

const outbox = {actionId:'action-conversation-original', kind:'conversation', enqueuedAt:5,
    payload:{conversationProtocol:1, projectId:'proj', nodeId:'chat', requestId:'request-original', text:'original text', humanIntent:true}};
const message = {id:'server-message', role:'user', status:'pending', request_id:'request-original', text:'original text', references:[], user_edit:true, created_at:8};
const conversation = fields => ({id:'chat', type:'smart-conversation', ...fields});

function core(){
    const {ctx} = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js']);
    return ctx;
}

test('core exposes unresolved conversation outboxes with stable identity and original payload without mutating the node', () => {
    const ctx = core();
    const node = conversation({pendingActionResumes:[outbox, {kind:'video', actionId:'other', payload:{}}]});
    const before = structuredClone(node);
    assert.deepEqual(fromContext(ctx.canvasCore.smartConversationPendingIntents(node)), [
        {source:'outbox', actionId:'action-conversation-original', payload:outbox.payload},
    ]);
    assert.deepEqual(node, before);
    const projected = ctx.canvasCore.smartConversationPendingIntents(node);
    projected[0].payload.text = 'changed projection';
    assert.deepEqual(node, before, 'returned payloads do not alias live execution data');
});

test('formal pending user requests are intents, but local placeholders, terminal receipts and assistants are not', () => {
    const ctx = core();
    const node = conversation({messages:[message,
        {...message, id:'local-request-original'},
        {...message, id:'completed', status:'completed'},
        {...message, id:'failed', status:'failed'},
        {...message, id:'assistant', role:'assistant'},
        {id:'old-text', role:'user', status:'pending', text:'no request identity'},
    ]});
    assert.deepEqual(fromContext(ctx.canvasCore.smartConversationPendingIntents(node)), [
        {source:'message', requestId:'request-original', payload:message},
    ]);
    assert.deepEqual(fromContext(ctx.canvasCore.smartConversationPendingIntents({id:'legacy'})), []);
    const legacy = {...message}; delete legacy.id; delete legacy.user_edit;
    assert.deepEqual(fromContext(ctx.canvasCore.smartConversationPendingIntents(conversation({messages:[legacy]}))), [
        {source:'message', requestId:'request-original', payload:legacy},
    ], 'real legacy server receipts may omit id and user_edit');
});

test('pending user messages on a non-conversation node are not executable conversation requests', () => {
    const ctx = core();
    assert.deepEqual(fromContext(ctx.canvasCore.smartConversationPendingIntents({id:'agent', type:'smart-agent', messages:[message]})), []);
    assert.deepEqual(fromContext(ctx.canvasCore.smartConversationPendingIntents({id:'image', type:'smart-image', pendingActionResumes:[outbox]})), [
        {source:'outbox', actionId:outbox.actionId, payload:outbox.payload},
    ], 'explicit conversation outboxes remain protected on any anchored node');
});

test('present corrupted conversation arrays and records fail explicitly instead of permitting copy/history', () => {
    const ctx = core();
    for(const fields of [{messages:null}, {messages:[null]}, {pendingActionResumes:{}},
        {pendingActionResumes:[null]}, {pendingActionResumes:[{kind:'conversation', payload:null}]}]){
        assert.throws(() => ctx.canvasCore.smartConversationPendingIntents(conversation(fields)), {name:'TypeError'});
    }
});

const stacks = h => evaluate(h.ctx, 'JSON.stringify({undo:undoStack, redo:redoStack})');
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };

test('delta undo rejects resurrection of a consumed outbox atomically and preserves its original history entry', async () => {
    const h = editor([conversation({...identity, pendingActionResumes:[outbox]})]);
    h.ctx.pushUndo();
    delete h.node().pendingActionResumes;
    h.ctx.finalizePendingCanvasUndo();
    const beforeNodes = h.stored(), beforeStacks = stacks(h);
    await h.ctx.performUndo();
    assert.deepEqual(h.stored(), beforeNodes, 'consumed execution intent must not be restored');
    assert.equal(stacks(h), beforeStacks, 'conflict must leave both stacks byte-for-byte unchanged');
    assert.ok(h.harness.toasts.includes('smart.canvasConflict'), 'conflict is visible');
});

test('legacy snapshot undo and redo reject old formal requests without popping either stack', async () => {
    for(const direction of ['Undo', 'Redo']){
        const h = editor([conversation({...identity, messages:[]})]);
        const snapshot = fromContext(h.ctx.snapshotForUndo());
        delete snapshot.actionDeltaVersion;
        snapshot.nodes[0].messages = [message];
        const history = {undo:[], redo:[]};
        history[direction === 'Undo' ? 'undo' : 'redo'].push(snapshot);
        h.ctx.fetch = async () => ({ok:true, json:async () => history});
        await h.ctx.restorePersistedUndoStack();
        const beforeNodes = h.stored(), beforeStacks = stacks(h);
        await h.ctx['perform'+direction]();
        assert.deepEqual(h.stored(), beforeNodes, direction+' must not resurrect the formal request');
        assert.equal(stacks(h), beforeStacks, direction+' preserves original stacks');
        assert.ok(h.harness.toasts.includes('smart.canvasConflict'));
    }
});

test('delta undo cannot remove an accepted pending send as an implicit send cancellation', async () => {
    const h = editor([conversation({...identity, messages:[]})]);
    h.ctx.pushUndo(); h.node().messages = [message]; h.ctx.finalizePendingCanvasUndo();
    const before = h.stored(), beforeStacks = stacks(h);
    await h.ctx.performUndo();
    assert.deepEqual(h.stored(), before);
    assert.equal(stacks(h), beforeStacks);
    assert.ok(h.harness.toasts.includes('smart.canvasConflict'));
});

test('an undo cannot replace a current same-key outbox with a different text or human flag', async () => {
    for(const payload of [{...outbox.payload, text:'different text'}, {...outbox.payload, humanIntent:false}]){
        const h = editor([conversation({...identity, pendingActionResumes:[outbox]})]);
        h.ctx.pushUndo(); h.node().pendingActionResumes[0].payload = payload; h.ctx.finalizePendingCanvasUndo();
        const before = h.stored(), beforeStacks = stacks(h);
        await h.ctx.performUndo();
        assert.deepEqual(h.stored(), before);
        assert.equal(stacks(h), beforeStacks);
        assert.ok(h.harness.toasts.includes('smart.canvasConflict'));
    }
});

test('legacy history cannot silently clear a legitimate reload queue or its registered message', async () => {
    for(const removeNode of [false,true]){
        const h=editor([conversation({...identity,messages:[message],pendingActionResumes:[outbox]})]);
        const snapshot=fromContext(h.ctx.snapshotForUndo());delete snapshot.actionDeltaVersion;
        if(removeNode)snapshot.nodes=[];
        else {snapshot.nodes[0].messages=[];delete snapshot.nodes[0].pendingActionResumes;}
        h.ctx.fetch=async()=>({ok:true,json:async()=>({undo:[snapshot],redo:[]})});
        await h.ctx.restorePersistedUndoStack();
        const before=h.stored(),beforeStacks=stacks(h);
        await h.ctx.performUndo();
        assert.deepEqual(h.stored(),before);
        assert.equal(stacks(h),beforeStacks);
        assert.ok(h.harness.toasts.includes('smart.canvasConflict'));
    }
});

test('untouched live intents remain valid through content/ownership undo-redo and unrelated remote nodes survive', async () => {
    const original = conversation({...identity, text:'before', created_at:19, messages:[message], pendingActionResumes:[outbox]});
    const h = editor([original]);
    h.ctx.pushUndo();
    Object.assign(h.node(), {text:'after', userTouchedAt:80, promptEditedByUser:true, agentNodeId:null});
    h.ctx.finalizePendingCanvasUndo();
    evaluate(h.ctx, `canvasState.nodes.push({id:'remote', type:'smart-prompt', text:'remote'});`);
    await h.ctx.performUndo();
    assert.deepEqual(fromContext(h.node()), original);
    assert.equal(h.stored()[1].text, 'remote');
    await h.ctx.performRedo();
    assert.deepEqual(fromContext(h.node()), {...original, text:'after', userTouchedAt:80, promptEditedByUser:true, agentNodeId:null});
    assert.equal(h.stored()[1].text, 'remote');
});

test('legacy history rechecks live formal intents after media await rather than reviving a newly consumed request', async () => {
    const h = editor([conversation({...identity, messages:[message], images:[]})]);
    const snapshot = fromContext(h.ctx.snapshotForUndo()); delete snapshot.actionDeltaVersion;
    snapshot.nodes[0].images = [{asset_id:'original'}];
    h.ctx.fetch = async () => ({ok:true, json:async () => ({undo:[snapshot], redo:[]})});
    await h.ctx.restorePersistedUndoStack();
    evaluate(h.ctx, `canvasState.projectMediaLibrary = {items:[{asset_id:'unrelated'}]};`);
    const response = deferred(); h.ctx.fetch = () => response.promise;
    const undo = h.ctx.performUndo();
    h.node().messages = [];
    const before = h.stored(), beforeStacks = stacks(h);
    response.resolve({ok:true, json:async () => ({items:[{asset_id:'original'}]})});
    await undo;
    assert.deepEqual(h.stored(), before); assert.equal(stacks(h), beforeStacks);
    assert.ok(h.harness.toasts.includes('smart.canvasConflict'));
});

test('delta redo rechecks current intent after media IO even when its history entry did not move', async () => {
    const h = editor([conversation({...identity, messages:[message], images:[]})]);
    const snapshot = fromContext(h.ctx.snapshotForUndo());
    const before = {...fromContext(h.node()), messages:[], images:[]};
    const after = {...fromContext(h.node()), images:[{asset_id:'original'}]};
    snapshot.actionDelta = fromContext(h.ctx.canvasCore.smartCanvasActionDelta({nodes:[before]}, {nodes:[after]}));
    h.ctx.fetch = async () => ({ok:true, json:async () => ({undo:[], redo:[snapshot]})});
    await h.ctx.restorePersistedUndoStack();
    evaluate(h.ctx, `canvasState.projectMediaLibrary = {items:[{asset_id:'unrelated'}]};`);
    const response = deferred(); h.ctx.fetch = () => response.promise;
    const redo = h.ctx.performRedo();
    h.node().messages = [];
    const beforeNodes = h.stored(), beforeStacks = stacks(h);
    response.resolve({ok:true, json:async () => ({items:[{asset_id:'original'}]})});
    await redo;
    assert.deepEqual(h.stored(), beforeNodes); assert.equal(stacks(h), beforeStacks);
    assert.ok(h.harness.toasts.includes('smart.canvasConflict'));
});

test('media refresh cannot apply an undo from the previous project or overwrite the new project media library', async () => {
    const h = editor([{id:'image', type:'smart-image', images:[{asset_id:'original', output_id:'output'}]}]);
    evaluate(h.ctx, `canvasState.projectMediaLibrary = {items:[{asset_id:'unrelated'}]};`);
    h.ctx.pushUndo(); h.node().images = []; h.ctx.finalizePendingCanvasUndo();
    const response = deferred();
    h.ctx.fetch = () => response.promise;
    const undo = h.ctx.performUndo();
    evaluate(h.ctx, `canvas.id = 'other-canvas'; canvas.project_id = 'other-project';
        canvasState.nodes = [{id:'new-project-node', type:'smart-prompt', text:'keep'}]; canvas.nodes = canvasState.nodes;
        canvasState.projectMediaLibrary = {items:[{asset_id:'new-project-asset'}]};`);
    const before = h.stored(), beforeStacks = stacks(h);
    response.resolve({ok:true, json:async () => ({items:[{asset_id:'original'}]})});
    await undo;
    assert.deepEqual(h.stored(), before);
    assert.equal(stacks(h), beforeStacks);
    assert.deepEqual(fromContext(evaluate(h.ctx, 'canvasState.projectMediaLibrary.items')), [{asset_id:'new-project-asset'}]);
});

test('media-library refresh retains the existing sourceProjectId lookup for legacy canvases without project_id', async () => {
    const h = editor([{id:'p', type:'smart-prompt', text:'legacy'}]);
    evaluate(h.ctx, 'delete canvas.project_id');
    h.ctx.fetch = async url => {
        assert.equal(url, '/api/project-library/unit-392/media');
        return {ok:true, json:async () => ({items:[{asset_id:'valid'}]})};
    };
    assert.equal(await h.ctx.refreshProjectMediaLibraryItems(), true);
    assert.deepEqual(fromContext(evaluate(h.ctx, 'canvasState.projectMediaLibrary.items')), [{asset_id:'valid'}]);
});

test('legacy undo and redo awaiting media preserve a newer local action or switched project', async () => {
    for(const direction of ['Undo', 'Redo']) for(const change of ['local action', 'project switch']){
        const h = editor([{id:'p', type:'smart-prompt', text:'live'}]);
        const snapshot = fromContext(h.ctx.snapshotForUndo());
        delete snapshot.actionDeltaVersion;
        snapshot.nodes = [{id:'image', type:'smart-image', images:[{asset_id:'original'}]}];
        const history = {undo:[], redo:[]}; history[direction === 'Undo' ? 'undo' : 'redo'].push(snapshot);
        h.ctx.fetch = async () => ({ok:true, json:async () => history});
        await h.ctx.restorePersistedUndoStack();
        evaluate(h.ctx, `canvasState.projectMediaLibrary = {items:[{asset_id:'unrelated'}]};`);
        const response = deferred(); h.ctx.fetch = () => response.promise;
        const restoring = h.ctx['perform'+direction]();
        if(change === 'local action'){
            h.ctx.pushUndo(); h.node().text = 'new action'; h.ctx.finalizePendingCanvasUndo();
        } else {
            evaluate(h.ctx, `canvas.id = 'other-canvas'; canvas.project_id = 'other-project';`);
        }
        const before = h.stored(), beforeStacks = stacks(h);
        response.resolve({ok:true, json:async () => ({items:[{asset_id:'original'}]})});
        await restoring;
        assert.deepEqual(h.stored(), before, direction+' / '+change+' aborts stale snapshot application');
        assert.equal(stacks(h), beforeStacks);
    }
});

test('a rejected media-original restore keeps its token in the original stack position across a later action', async () => {
    const h = editor([{id:'p', type:'smart-prompt', text:'before'}]);
    loadModules(h.ctx, ['canvas-agent-tui.js']);
    h.ctx.smartTitleText = h.harness.makeElement();
    h.ctx.pushUndo({projectOriginalDelete:{assetId:'original', deletionId:'deleted-output', projectId:'unit-392'}});
    const response = deferred(); let requested = false;
    h.ctx.fetch = url => {
        assert.match(url, /\/original\/restore$/);
        requested = true; return response.promise;
    };
    const undo = h.ctx.performUndo();
    for(let i=0; i<30 && !requested; i++) await Promise.resolve();
    assert.equal(requested, true, 'real media restore reached its POST boundary: '+JSON.stringify(h.harness.toasts));
    h.ctx.pushUndo(); h.node().text = 'later action'; h.ctx.finalizePendingCanvasUndo();
    const before = stacks(h);
    response.resolve({ok:false, status:409, json:async () => ({detail:'restore conflict'})});
    await undo;
    assert.equal(stacks(h), before, 'rejected earlier restore must not jump above the later action');
    assert.ok(h.harness.toasts.includes('restore conflict'));
});

test('content and all ownership presence/value fields including created_at round-trip in one delta', async () => {
    const originals = [
        {id:'p', type:'smart-prompt', text:'before', createdBy:'agent', agentNodeId:'author', userTouchedAt:null, promptEditedByUser:false, created_at:9},
        {id:'legacy', type:'smart-prompt', text:'legacy'},
    ];
    const h = editor(originals); h.ctx.pushUndo();
    for(const node of evaluate(h.ctx, 'canvasState.nodes')) Object.assign(node, {text:'after', createdBy:'user', agentNodeId:null, userTouchedAt:10, promptEditedByUser:true, created_at:99});
    h.ctx.finalizePendingCanvasUndo(); const edited = h.stored();
    await h.ctx.performUndo(); assert.deepEqual(h.stored(), originals);
    await h.ctx.performRedo(); assert.deepEqual(h.stored(), edited);
});

test('persisted history rechecks after response JSON and does not import over a cancelled no-op transaction', async () => {
    const h = editor([{id:'p', type:'smart-prompt', text:'before'}]);
    const body = deferred(), reading = deferred();
    const snapshot = fromContext(h.ctx.snapshotForUndo());
    h.ctx.fetch = async () => ({ok:true, json:() => { reading.resolve(); return body.promise; }});
    const restoring = h.ctx.restorePersistedUndoStack();
    await reading.promise;
    h.ctx.pushUndo(); h.ctx.finalizePendingCanvasUndo();
    assert.equal(evaluate(h.ctx, 'undoStack.length'), 0, 'no-op action leaves empty history');
    body.resolve({undo:[snapshot], redo:[]}); await restoring;
    assert.equal(evaluate(h.ctx, 'undoStack.length'), 0, 'old history must not reappear after the no-op transaction');
});

test('a valid empty-history startup loads, but an active capture blocks history loading without clearing it', async () => {
    const h = editor([{id:'p', type:'smart-prompt', text:'before'}]);
    const snapshot = fromContext(h.ctx.snapshotForUndo()); delete snapshot.actionDeltaVersion;
    let requests = 0;
    h.ctx.fetch = async () => { requests++; return {ok:true, json:async () => ({undo:[snapshot], redo:[]})}; };
    h.ctx.capturePendingUndo(); await h.ctx.restorePersistedUndoStack();
    assert.equal(requests, 0); assert.equal(evaluate(h.ctx, 'pendingUndoSnapshot !== null'), true);
    h.ctx.discardPendingUndo(); await h.ctx.restorePersistedUndoStack();
    assert.equal(requests, 1); assert.equal(evaluate(h.ctx, 'undoStack.length'), 1);
});

test('delayed persisted history cannot overwrite local history, a cancelled action, or a switched project', async () => {
    for(const change of ['local history', 'cancelled capture', 'project switch']){
        const h = editor([{id:'p', type:'smart-prompt', text:'before'}]);
        const response = deferred();
        const old = fromContext(h.ctx.snapshotForUndo());
        delete old.actionDeltaVersion;
        old.nodes[0].text = 'old persisted step';
        h.ctx.fetch = () => response.promise;
        const restoring = h.ctx.restorePersistedUndoStack();
        if(change === 'local history'){
            h.ctx.pushUndo(); h.node().text = 'local action'; h.ctx.finalizePendingCanvasUndo();
        } else if(change === 'cancelled capture'){
            h.ctx.capturePendingUndo(); h.ctx.discardPendingUndo();
        } else {
            evaluate(h.ctx, `canvas.project_id = 'other-project'; canvas.id = 'other-canvas';`);
        }
        const before = stacks(h);
        response.resolve({ok:true, json:async () => ({undo:[old], redo:[]})});
        await restoring;
        assert.equal(stacks(h), before, change+' rejects the stale GET instead of importing old steps');
    }
});
