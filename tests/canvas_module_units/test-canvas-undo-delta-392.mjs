// #392 2a: real core + collection merger + undo entry points. Only browser/IO seams
// are substituted; no delta builder/applier or collection merger is mocked.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, evaluate, fromContext } from './harness.mjs';

function loadUndo(nodes) {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'project-canvas-mutations.js']);
    ctx.initialNodes = structuredClone(nodes);
    evaluate(ctx, `canvas = {id:'cv', project_id:'proj', format:'firefly-canvas-v1', nodes:initialNodes, connections:[]};
        canvasState.nodes = canvas.nodes; canvasState.annotations = [];`);
    ctx.sourceProjectId = 'proj';
    ctx.canvasId = 'cv';
    const effects = { saves: [], archives: [], toasts: [] };
    ctx.render = () => {};
    ctx.toast = value => effects.toasts.push(value);
    ctx.cancelPendingMediaArchives = () => {};
    ctx.scheduleMediaArchive = images => effects.archives.push(fromContext(images));
    ctx.scheduleSave = () => effects.saves.push(fromContext(evaluate(ctx, 'canvas')));
    ctx.fetch = async () => ({ok:true, json:async () => ({items:[]})});
    harness.element('smartUndoButton');
    harness.element('smartRedoButton');
    loadModules(ctx, ['canvas-undo.js']);
    // Before the implementation exists, reach the actual old undo overwrite rather
    // than making a missing helper the only red evidence. Never replace that helper.
    const seal = () => ctx.finalizePendingCanvasUndo?.();
    return { ctx, harness, effects, seal,
        nodes: () => fromContext(evaluate(ctx, 'canvasState.nodes')) };
}

const prompt = {id:'p', type:'smart-prompt', text:'before', createdBy:'agent', agentNodeId:null,
    userTouchedAt:null, promptEditedByUser:false, created_at:17};

test('undo during an in-flight save restores prompt and exact metadata without erasing a later remote node', async () => {
    const h = loadUndo([prompt, {id:'legacy', type:'smart-prompt', text:'old'}]);
    const mutations = h.ctx.FireflyProjectCanvasMutations;
    const baseline = mutations.snapshotFromCanvas(evaluate(h.ctx, 'canvas'));
    h.ctx.pushUndo();
    evaluate(h.ctx, `Object.assign(canvasState.nodes[0], {text:'edited', agentNodeId:'agent-a', userTouchedAt:123, promptEditedByUser:true});
        Object.assign(canvasState.nodes[1], {text:'new', createdBy:'user', agentNodeId:null, userTouchedAt:124, promptEditedByUser:true});`);
    h.seal(); // save starts: freeze this action before any remote receipt
    const saving = structuredClone(h.nodes());
    const frozenCanvas = fromContext(evaluate(h.ctx, 'canvas'));
    const batch = mutations.mutationsFromCanvas(frozenCanvas, baseline);
    let acknowledge;
    const inFlightSave = mutations.flushProjectCanvasMutations({canvas:frozenCanvas, baseline, mutations:batch.mutations,
        send:() => new Promise(resolve => { acknowledge = resolve; })});
    assert.equal(typeof acknowledge, 'function', 'the real mutation flush has reached its delayed network boundary');
    evaluate(h.ctx, `canvasState.nodes.push({id:'remote', type:'smart-image', images:[{output_id:'remote-output', asset_id:'remote-asset'}]});`);
    const beforeUndo = fromContext(evaluate(h.ctx, 'canvas'));
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [prompt, {id:'legacy', type:'smart-prompt', text:'old'},
        {id:'remote', type:'smart-image', images:[{output_id:'remote-output', asset_id:'remote-asset'}]}]);
    const journal = h.ctx.createSmartCanvasPendingJournal({mergeValue:mutations.mergeCanvasValue});
    journal.record(beforeUndo, evaluate(h.ctx, 'canvas'));
    acknowledge({status:200, saved:true, revision:2});
    const acknowledged = await inFlightSave;
    assert.equal(acknowledged.saved, true);
    assert.equal(acknowledged.canvas.nodes[0].text, 'edited', 'the already-sent save acknowledges the old edit');
    assert.deepEqual(fromContext(journal.replay(acknowledged.canvas).canvas.nodes),
        [prompt, {id:'legacy', type:'smart-prompt', text:'old'}], 'an old save ack cannot swallow the later compensating restore');
    await h.ctx.performRedo();
    assert.deepEqual(h.nodes(), [...saving,
        {id:'remote', type:'smart-image', images:[{output_id:'remote-output', asset_id:'remote-asset'}]}]);
    assert.equal(h.nodes()[0].created_at, 17, 'redo restores identity/time, not a newly-created node');
});

test('a new-format no-op does not invalidate a valid redo branch', async () => {
    const h = loadUndo([prompt]);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'edited';`);
    h.seal();
    await h.ctx.performUndo();
    h.ctx.pushUndo(); // same-value or cancelled edit
    h.seal();
    await h.ctx.performRedo();
    assert.deepEqual(h.nodes(), [{...prompt, text:'edited'}]);
});

test('a capture spanning remote receipt rebases its open action before the asynchronous continuation', async () => {
    for(const earlyEdit of [false, true]) {
        const h = loadUndo([prompt]);
        h.ctx.capturePendingUndo();
        if(earlyEdit) evaluate(h.ctx, `canvasState.nodes[0].text = 'early local edit';`);
        h.seal(); // immediately before remote application
        evaluate(h.ctx, `canvasState.nodes.push({id:'remote', type:'smart-prompt', text:'remote during capture'});`);
        h.ctx.finalizePendingCanvasUndo({afterRemote:true});
        await Promise.resolve();
        evaluate(h.ctx, `canvasState.nodes[0].text = 'completed local edit';`);
        h.ctx.commitPendingUndo();
        await h.ctx.performUndo();
        assert.deepEqual(h.nodes(), [prompt, {id:'remote', type:'smart-prompt', text:'remote during capture'}]);
        await h.ctx.performRedo();
        assert.deepEqual(h.nodes(), [{...prompt, text:'completed local edit'}, {id:'remote', type:'smart-prompt', text:'remote during capture'}]);
    }
});

test('save finalization leaves an open capture able to include later local field continuation', async () => {
    const h = loadUndo([{...prompt, x:0}]);
    h.ctx.capturePendingUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'local prefix';`);
    h.seal(); // save begins, not the end of the capture
    evaluate(h.ctx, `canvasState.nodes[0].x = 99;`);
    h.ctx.commitPendingUndo();
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [{...prompt, x:0}]);
    await h.ctx.performRedo();
    assert.deepEqual(h.nodes(), [{...prompt, text:'local prefix', x:99}]);
});

test('repeated afterRemote finalization without changes cannot erase an open capture prefix', async () => {
    const h = loadUndo([prompt]);
    h.ctx.capturePendingUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'local capture edit';`);
    h.seal();
    evaluate(h.ctx, `canvasState.nodes.push({id:'remote', type:'smart-prompt', text:'remote'});`);
    h.ctx.finalizePendingCanvasUndo({afterRemote:true});
    h.ctx.finalizePendingCanvasUndo({afterRemote:true});
    h.ctx.commitPendingUndo();
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [prompt, {id:'remote', type:'smart-prompt', text:'remote'}]);
    await h.ctx.performRedo();
    assert.deepEqual(h.nodes(), [{...prompt, text:'local capture edit'}, {id:'remote', type:'smart-prompt', text:'remote'}]);
});

test('#146 redo archives only removed assets while selection and annotations round-trip', async () => {
    const original = {id:'image', type:'smart-image', images:[{output_id:'o1',asset_id:'a'}]};
    const survivor = {id:'survivor', type:'smart-image', images:[{output_id:'o2',asset_id:'b'}]};
    const h = loadUndo([original, survivor]);
    evaluate(h.ctx, `canvasState.selectedId = 'image'; canvasState.selectedIds = ['image'];
        canvasState.selectedImage = {nodeId:'image',index:0}; canvasState.annotations = [{id:'note',text:'before'}];`);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes.splice(0,1); canvasState.selectedId = 'survivor'; canvasState.selectedIds = ['survivor'];
        canvasState.selectedImage = {nodeId:'survivor',index:0}; canvasState.annotations[0].text = 'after';`);
    h.seal();
    evaluate(h.ctx, `canvasState.annotations.push({id:'remote-note',text:'remote'});`);
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes().find(n => n.id === 'image'), original);
    assert.deepEqual(fromContext(evaluate(h.ctx, '[canvasState.selectedId,canvasState.selectedIds,canvasState.selectedImage,canvasState.annotations]')),
        ['image',['image'],{nodeId:'image',index:0},[{id:'note',text:'before'},{id:'remote-note',text:'remote'}]]);
    await h.ctx.performRedo();
    assert.deepEqual(h.nodes(), [survivor]);
    assert.deepEqual(h.effects.archives, [[{output_id:'o1',asset_id:'a'}]]);
    assert.deepEqual(fromContext(evaluate(h.ctx, '[canvasState.selectedId,canvasState.selectedIds,canvasState.selectedImage,canvasState.annotations]')),
        ['survivor',['survivor'],{nodeId:'survivor',index:0},[{id:'note',text:'after'},{id:'remote-note',text:'remote'}]]);
});

test('ambiguous media identity keeps both the live canvas and the undo/redo entry available', async () => {
    const h = loadUndo([{id:'image', type:'smart-image', images:[{asset_id:'a', name:'before'}]}]);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].images[0].name = 'edited';`);
    h.seal();
    evaluate(h.ctx, `canvasState.nodes[0].images.push({asset_id:'a', name:'ambiguous remote output'});`);
    const conflicting = h.nodes();
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), conflicting);
    assert.equal(evaluate(h.ctx, 'undoStack.length'), 1);
    assert.equal(evaluate(h.ctx, 'redoStack.length'), 0);
    assert.equal(h.effects.saves.length, 0, 'a partial conflicting result must never be saved');
    evaluate(h.ctx, `canvasState.nodes[0].images.pop();`); // external conflict resolution, not a merger stub
    await h.ctx.performUndo();
    assert.equal(h.nodes()[0].images[0].name, 'before', 'the retained entry is retryable');
    evaluate(h.ctx, `canvasState.nodes[0].images.push({asset_id:'a', name:'another ambiguous output'});`);
    const conflictingRedo = h.nodes();
    await h.ctx.performRedo();
    assert.deepEqual(h.nodes(), conflictingRedo);
    assert.equal(evaluate(h.ctx, 'undoStack.length'), 0);
    assert.equal(evaluate(h.ctx, 'redoStack.length'), 1);
    assert.equal(h.effects.saves.length, 1, 'failed redo must not send another save');
});

test('#251 undo-stack persistence seals a new action before serializing and preserves its scope after reload', async () => {
    const h = loadUndo([prompt]);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'saved edit';`);
    let payload;
    h.ctx.fetch = async (_url, options) => { payload = JSON.parse(options.body); return {ok:true}; };
    await h.ctx.persistUndoStackNow('proj');
    const reloaded = loadUndo([{...prompt, text:'saved edit'}, {id:'remote', type:'smart-prompt', text:'after persistence'}]);
    reloaded.ctx.fetch = async () => ({ok:true, json:async () => structuredClone(payload)});
    await reloaded.ctx.restorePersistedUndoStack();
    await reloaded.ctx.performUndo();
    assert.deepEqual(reloaded.nodes(), [prompt, {id:'remote', type:'smart-prompt', text:'after persistence'}]);
    await reloaded.ctx.performRedo();
    assert.deepEqual(reloaded.nodes(), [{...prompt, text:'saved edit'}, {id:'remote', type:'smart-prompt', text:'after persistence'}]);
});

test('#375 merge undo emits an Agent-node tombstone; only explicit redo restores its original identity', async () => {
    const h = loadUndo([prompt]);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'user edit inside debounce';`);
    h.seal();
    h.ctx.preMerge = h.ctx.snapshotForUndo(); // the real #375 before-merge snapshot API
    const agent = {id:'agent-output', type:'smart-image', createdBy:'agent', agentNodeId:'agent-a',
        userTouchedAt:null, promptEditedByUser:false, created_at:9, images:[]};
    h.ctx.agentOutput = agent;
    evaluate(h.ctx, `canvasState.nodes.push(agentOutput); undoStack.push(preMerge);`);
    h.seal(); // caller closes the #375 step immediately after the accepted merge
    const originalDelta = fromContext(evaluate(h.ctx, 'undoStack[undoStack.length - 1].actionDelta'));
    const server = fromContext(evaluate(h.ctx, 'canvas'));
    const mutations = h.ctx.FireflyProjectCanvasMutations;
    const baseline = mutations.snapshotFromCanvas(server);
    evaluate(h.ctx, `canvasState.nodes.push({id:'later', type:'smart-prompt', text:'not part of Agent action'});`);
    const beforeUndo = fromContext(evaluate(h.ctx, 'canvas'));
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes().map(n => n.id), ['p','later']);
    assert.equal(h.nodes()[0].text, 'user edit inside debounce', 'Agent undo cannot roll back the preceding user edit');
    const writes = fromContext(mutations.mutationsFromCanvas(evaluate(h.ctx, 'canvas'), baseline).mutations);
    assert.ok(writes.some(m => m.op === 'remove_node' && m.node_id === 'agent-output'));
    const journal = h.ctx.createSmartCanvasPendingJournal({mergeValue:mutations.mergeCanvasValue});
    journal.record(beforeUndo, evaluate(h.ctx, 'canvas'));
    assert.deepEqual(fromContext(journal.replay(beforeUndo).canvas.nodes).map(n => n.id), ['p','later'],
        'an old broadcast/terminal result cannot revive the removed Agent node');
    await h.ctx.performRedo();
    assert.deepEqual(h.nodes().find(n => n.id === 'agent-output'), agent);
    assert.deepEqual(fromContext(evaluate(h.ctx, 'undoStack[undoStack.length - 1].actionDelta')), originalDelta,
        'redo pushes the same sealed action, not a newly measured restore');
});

test('persisted legacy entries stay snapshot-based after undo/redo even when the new core is loaded', async () => {
    const h = loadUndo([{...prompt, text:'edited'}]);
    const legacy = {nodes:[prompt], connections:[], selectedId:'p', selectedIds:['p'], selectedImage:{}, annotations:[]};
    h.ctx.fetch = async () => ({ok:true, json:async () => ({undo:[structuredClone(legacy)], redo:[]})});
    await h.ctx.restorePersistedUndoStack();
    await h.ctx.performUndo();
    await h.ctx.performRedo();
    h.seal();
    evaluate(h.ctx, `canvasState.nodes.push({id:'later', type:'smart-prompt', text:'legacy limitation'});`);
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [prompt], 'old entries are not silently migrated to inferred action ranges');
});

test('#62 media refresh during undo cannot discard a remote node received while awaiting the library', async () => {
    const h = loadUndo([{id:'image', type:'smart-image', images:[{output_id:'o', asset_id:'a'}]}]);
    evaluate(h.ctx, `canvasState.projectMediaLibrary = {items:[{asset_id:'unrelated'}]};`);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].images = [];`);
    h.seal();
    let finishRefresh;
    h.ctx.fetch = () => new Promise(resolve => { finishRefresh = resolve; });
    const undo = h.ctx.performUndo();
    assert.equal(typeof finishRefresh, 'function', 'undo has reached the real media-library refresh IO boundary');
    evaluate(h.ctx, `canvasState.nodes.push({id:'remote', type:'smart-prompt', text:'received while undo awaited'});`);
    finishRefresh({ok:true, json:async () => ({items:[{asset_id:'a'}]})});
    await undo;
    assert.deepEqual(h.nodes(), [{id:'image', type:'smart-image', images:[{output_id:'o',asset_id:'a'}]},
        {id:'remote', type:'smart-prompt', text:'received while undo awaited'}]);
});

test('undo waiting for media refresh aborts safely when a newer local action becomes undoable', async () => {
    const original = {id:'image', type:'smart-image', text:'before', images:[{output_id:'o', asset_id:'a'}]};
    const h = loadUndo([original]);
    evaluate(h.ctx, `canvasState.projectMediaLibrary = {items:[{asset_id:'unrelated'}]};`);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].images = [];`);
    h.seal();
    let finishRefresh;
    h.ctx.fetch = () => new Promise(resolve => { finishRefresh = resolve; });
    const undo = h.ctx.performUndo();
    assert.equal(typeof finishRefresh, 'function');
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'newer local edit';`);
    h.seal();
    finishRefresh({ok:true, json:async () => ({items:[{asset_id:'a'}]})});
    await undo;
    assert.deepEqual(h.nodes(), [{...original, text:'newer local edit', images:[]}], 'outdated undo must not consume a different action');
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [{...original, images:[]}], 'newer edit is still the next undo step');
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [original], 'the deferred original action is still undoable');
    await h.ctx.performRedo();
    await h.ctx.performRedo();
    assert.deepEqual(h.nodes(), [{...original, text:'newer local edit', images:[]}], 'both actions retain coherent redo order');
});

test('capture committed before thumbnail detachment seals after the actual mutation, not as an empty action', async () => {
    const h = loadUndo([{id:'image', type:'smart-image', images:[{output_id:'o', asset_id:'a'}]}]);
    h.ctx.capturePendingUndo();
    h.ctx.commitPendingUndo(); // actual thumb-detach entry commits before creating the detached node
    evaluate(h.ctx, `canvasState.nodes[0].images = []; canvasState.nodes.push({id:'detached', type:'smart-image', images:[{output_id:'o',asset_id:'a'}]});`);
    h.seal();
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [{id:'image', type:'smart-image', images:[{output_id:'o',asset_id:'a'}]}]);
});

test('#62 hard deletion strips an undo whose media reference exists only in the action after-value', () => {
    const h = loadUndo([{id:'image', type:'smart-image', images:[]}]);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].images.push({output_id:'output', asset_id:'deleted'});`);
    h.seal();
    assert.equal(h.ctx.stripDeletedMediaFromUndoStacks('deleted'), 1,
        'the forward delta must not retain a reference which could be redone after hard deletion');
    assert.equal(evaluate(h.ctx, 'undoStack.length'), 0);
});

test('an asynchronously committed capture keeps its sealed scope and invalidates an obsolete redo branch', async () => {
    const h = loadUndo([prompt]);
    h.ctx.capturePendingUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'dragged';`);
    h.seal();
    evaluate(h.ctx, `canvasState.nodes.push({id:'remote', type:'smart-prompt', text:'remote'});`);
    await Promise.resolve();
    h.ctx.commitPendingUndo(); // must not recompute the sealed delta with the remote node
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [prompt, {id:'remote', type:'smart-prompt', text:'remote'}]);
    h.ctx.capturePendingUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'different action';`);
    await Promise.resolve();
    h.ctx.commitPendingUndo();
    await h.ctx.performRedo();
    assert.equal(h.nodes()[0].text, 'different action', 'redo from the old branch is no longer available');
    assert.equal(evaluate(h.ctx, 'redoStack.length'), 0);
});

test('a sealed no-op does not consume the preceding real undo step', async () => {
    const h = loadUndo([prompt]);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'edited';`);
    h.seal();
    h.ctx.pushUndo(); // a cancelled/same-value action, not a user-visible step
    h.seal();
    evaluate(h.ctx, `canvasState.nodes.push({id:'remote', type:'smart-prompt', text:'remote'});`);
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [prompt, {id:'remote', type:'smart-prompt', text:'remote'}]);
    assert.equal(evaluate(h.ctx, 'redoStack.length'), 1);
});

test('forty cancelled or no-op actions cannot evict real history or consume its redo branch', async () => {
    const h = loadUndo([prompt]);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'real edit';`);
    h.seal();
    const noops = () => {
        for(let i = 0; i < 40; i++) {
            h.ctx.pushUndo();
            h.seal();
            h.ctx.capturePendingUndo();
            h.ctx.commitPendingUndo();
            h.seal();
        }
    };
    noops();
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [prompt], 'only confirmed actions count toward the history limit');
    noops();
    h.ctx.capturePendingUndo();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'cancelled prefix';`);
    h.seal();
    evaluate(h.ctx, `canvasState.nodes[0].text = 'before';`);
    h.ctx.commitPendingUndo();
    h.seal();
    await h.ctx.performRedo();
    assert.deepEqual(h.nodes(), [{...prompt, text:'real edit'}], 'cancellation cannot invalidate the old redo branch');
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes(), [prompt]);
});

test('undo and redo preserve later remote output identities and group members', async () => {
    const h = loadUndo([{id:'g', type:'smart-group', items:['p'], images:[
        {output_id:'o1', asset_id:'shared', name:'before'}, {output_id:'o2', asset_id:'shared', name:'untouched'}]}]);
    h.ctx.pushUndo();
    evaluate(h.ctx, `canvasState.nodes[0].items = ['p','local']; canvasState.nodes[0].images[0].name = 'edited';`);
    h.seal();
    evaluate(h.ctx, `canvasState.nodes[0].items.push('remote');
        canvasState.nodes[0].images.push({output_id:'o3', asset_id:'shared', name:'remote'});`);
    await h.ctx.performUndo();
    assert.deepEqual(h.nodes()[0], {id:'g', type:'smart-group', items:['p','remote'], images:[
        {output_id:'o1', asset_id:'shared', name:'before'}, {output_id:'o2', asset_id:'shared', name:'untouched'},
        {output_id:'o3', asset_id:'shared', name:'remote'}]});
    await h.ctx.performRedo();
    // Concurrent insertions have no required relative order; membership is the undo contract.
    const redone = h.nodes()[0];
    assert.deepEqual({...redone, items:[...redone.items].sort()}, {id:'g', type:'smart-group', items:['local','p','remote'], images:[
        {output_id:'o1', asset_id:'shared', name:'edited'}, {output_id:'o2', asset_id:'shared', name:'untouched'},
        {output_id:'o3', asset_id:'shared', name:'remote'}]});
});
