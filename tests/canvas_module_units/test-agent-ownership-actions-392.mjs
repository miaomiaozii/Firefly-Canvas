// #392 phase 2b-1: approved caller seams only. Production core and constructors
// run in the existing classic-script harness; no manual handlers are wired here.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, evaluate, fromContext } from './harness.mjs';

function core() {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, ['canvas-core.js']);
    return { ctx, harness, api: ctx.canvasCore };
}

// Explicit projector injection at the pure-core seam; the production storage
// adapter itself is exercised with the real serializer in the lifecycle suite.
const beginHuman = (api, nodes, options={}) => api.beginSmartHumanEdit(nodes, {project:api.smartHumanEditContent, ...options});

const agent = { createdBy: 'agent', agentNodeId: ' opaque agent ', userTouchedAt: null, promptEditedByUser: false };
const user = { createdBy: 'user', agentNodeId: null, userTouchedAt: null, promptEditedByUser: false };

function nodeOps() {
    const { ctx, harness } = createCanvasContext();
    const effects = [];
    // Host-only services are recorders/data fixtures. Ownership, node creation,
    // state and undo snapshots are all production modules, not copied logic.
    ctx.render = () => effects.push('render');
    ctx.scheduleSave = () => effects.push('save');
    ctx.inheritNodeMetaFromImage = () => effects.push('inherit');
    ctx.attachRunMeta = () => effects.push('runMeta');
    ctx.mediaNodeDefaultScale = () => 2;
    ctx.resolveChatProviderId = () => 'fixture-provider';
    ctx.resolveChatModel = () => 'fixture-model';
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-layout-utils.js', 'canvas-undo.js', 'canvas-node-ops.js']);
    evaluate(ctx, 'canvas = { nodes: canvasState.nodes, connections: [] }; Date.now = () => 900');
    return { ctx, harness, effects };
}

function constructorContract(name) {
    test(`${name} gives a fresh identity from explicit creation context before any side effects`, () => {
        for(const options of [{}, { skipUndo: true, select: false }, { creation: { createdBy: 'agent', agentNodeId: ' opaque creator ' }, skipUndo: true, select: false }]) {
            const { ctx, effects } = nodeOps();
            const source = { id: 'source', ...agent, userTouchedAt: 8, promptEditedByUser: true, created_at: 1 };
            ctx.canvasState.nodes.push(source);
            ctx.canvasState.selectedId = 'source';
            const image = { url: 'fixture-image', ...source };
            const opts = { ...source, text: 'Initial OCR / extracted prompt body', ...options };
            const created = name === 'createNode' ? ctx.createNode(10, 20, [image], opts) : ctx[name](10, 20, opts);
            const expected = options.creation ? { createdBy: 'agent', agentNodeId: ' opaque creator ', userTouchedAt: null, promptEditedByUser: false } : user;
            assert.deepEqual({ createdBy: created.createdBy, agentNodeId: created.agentNodeId, userTouchedAt: created.userTouchedAt, promptEditedByUser: created.promptEditedByUser }, expected);
            assert.equal(created.created_at, 900, 'new epoch timestamp, not supplied metadata');
            assert.notEqual(created.id, 'source');
            assert.equal(created.x, 10);
            assert.equal(created.y, 20);
            assert.equal(ctx.canvasState.nodes[1], created, 'constructor publishes the actual returned node');
            assert.equal(ctx.canvasState.selectedId, options.select === false ? 'source' : created.id);
            assert.equal(evaluate(ctx, 'undoStack.length'), options.skipUndo ? 0 : 1);
            if(!options.skipUndo) assert.deepEqual(fromContext(evaluate(ctx, 'undoStack[0].nodes')), [source], 'before snapshot precedes insertion');
            assert.deepEqual(source, { id: 'source', ...agent, userTouchedAt: 8, promptEditedByUser: true, created_at: 1 });
            assert.deepEqual(image, { url: 'fixture-image', ...source }, 'supplied image is unchanged');
            if(name === 'createNode') {
                assert.equal(Object.hasOwn(created, 'w'), false, 'image layout still has no explicit w');
                assert.equal(Object.hasOwn(created, 'h'), false, 'image layout still has no explicit h');
            }
            if(name === 'createPromptNode') assert.equal(created.text, 'Initial OCR / extracted prompt body', 'initial content is not a human edit');
            assert.deepEqual(effects.slice(-2), ['render', 'save']);
        }
        for(const creation of [null, { createdBy: 'automatic' }, { createdBy: 'agent' }, { createdBy: 'agent', agentNodeId: ' ' }, { createdBy: 'agent', agentNodeId: 3 }]) {
            const { ctx, harness, effects } = nodeOps();
            ctx.canvasState.selectedId = 'unchanged';
            const invoke = () => name === 'createNode'
                ? ctx.createNode(1, 2, [], { creation, runMeta: { prompt: 'p' } })
                : ctx[name](1, 2, { creation });
            assert.throws(invoke, { name: 'TypeError' });
            assert.deepEqual(fromContext(ctx.canvasState.nodes), [], 'invalid creation inserts nothing');
            assert.equal(ctx.canvasState.selectedId, 'unchanged');
            assert.equal(evaluate(ctx, 'undoStack.length'), 0, 'validate before undo');
            assert.deepEqual(effects, [], 'validate before metadata/render/save services');
            assert.equal(harness.timers.length, 0);
        }
    });
}

test('explicit human action snapshots only its targets and stamps live changes in place without host side effects', () => {
    const { ctx, harness, api } = core();
    const effects = [];
    ctx.pushUndo = ctx.render = ctx.scheduleSave = () => effects.push('unexpected');
    const target = { id: 'target', ...agent, text: 'before' };
    const other = { id: 'other', ...agent, text: 'remote before' };
    const action = beginHuman(api, [target], { prompt: true, at: 123 });
    assert.deepEqual(target, { id: 'target', ...agent, text: 'before' }, 'begin is read-only');
    target.text = '';
    other.text = 'remote after';
    const live = [other, target];
    assert.equal(api.finishSmartHumanEdit(action, live), true);
    assert.equal(live[1], target, 'no replacement object');
    assert.deepEqual(target, { id: 'target', ...agent, text: '', userTouchedAt: 123, promptEditedByUser: true });
    assert.deepEqual(other, { id: 'other', ...agent, text: 'remote after' });
    assert.deepEqual(effects, [], 'the caller owns pending/save/undo/render');
    assert.equal(harness.timers.length, 0);
});

test('asynchronous field scope ignores remote prompt changes but counts false/null/removals and preserves live adoption', () => {
    const { api } = core();
    const target = { id: 'n', ...agent, enabled: true, reset: 9, obsolete: false, text: 'old' };
    const fields = ['enabled', 'reset', 'obsolete'];
    const action = beginHuman(api, [target], { fields, prompt: true, at: 200 });
    fields.push('text'); // the action owns its scope, not the caller's mutable list
    const live = { ...target, enabled: false, reset: null, text: 'remote prompt', createdBy: 'user', agentNodeId: null, promptEditedByUser: true, userTouchedAt: 150 };
    delete live.obsolete;
    assert.equal(api.finishSmartHumanEdit(action, [live]), true);
    assert.deepEqual(live, { id: 'n', createdBy: 'user', agentNodeId: null, userTouchedAt: 200, promptEditedByUser: true, enabled: false, reset: null, text: 'remote prompt' });
    const noPrompt = { ...target, promptEditedByUser: false };
    const nonPromptAction = beginHuman(api, [noPrompt], { fields: ['enabled'], prompt: true, at: 201 });
    noPrompt.enabled = false;
    noPrompt.text = 'unrelated remote prompt';
    assert.equal(api.finishSmartHumanEdit(nonPromptAction, [noPrompt]), true);
    assert.equal(noPrompt.promptEditedByUser, false, 'remote prompt field is outside this action');
    const noop = beginHuman(api, [noPrompt], { fields: ['enabled'], prompt: true, at: 202 });
    noPrompt.text = 'another remote update';
    assert.equal(api.finishSmartHumanEdit(noop, [noPrompt]), false);
    assert.equal(noPrompt.userTouchedAt, 201);
});

test('injected durable projection compares stored prompt structure, not raw/runtime-only changes', () => {
    const { api } = core();
    const node = { id: 'n', ...agent, text: 'before', scratch: 'preview', variablePrompts: [''] };
    // Caller-supplied storage boundary: a preview-only field and leading/trailing
    // prompt whitespace are not persisted. No production projection is replicated.
    const project = value => ({ id: value.id, text: value.text.trim(), variablePrompts: value.variablePrompts, ...agent });
    const noop = beginHuman(api, [node], { project, prompt: true, at: 300 });
    node.text = ' before ';
    node.scratch = 'different preview';
    assert.equal(api.finishSmartHumanEdit(noop, [node]), false);
    assert.equal(node.userTouchedAt, null);
    assert.equal(node.promptEditedByUser, false);
    const changed = beginHuman(api, [node], { project, prompt: true, at: 301 });
    node.variablePrompts.push('');
    assert.equal(api.finishSmartHumanEdit(changed, [node]), true);
    assert.equal(node.userTouchedAt, 301);
    assert.equal(node.promptEditedByUser, true, 'empty persisted prompt field adds structure');
    assert.deepEqual(node.variablePrompts, ['', '']);
    const scoped = beginHuman(api, [node], { project, fields: ['scratch'], at: 302 });
    node.scratch = 'not stored';
    node.text = 'remote persisted prompt';
    assert.equal(api.finishSmartHumanEdit(scoped, [node]), false, 'fields cannot revive data excluded by projection');
    assert.equal(node.userTouchedAt, 301);
    assert.throws(() => api.beginSmartHumanEdit([node], { project: true }), { name: 'TypeError' });
});

test('manual prompt structure changes protect the prompt, but ordinary settings and same values do not', () => {
    const { api } = core();
    for(const [field, before, after] of [['promptSeparator', ';', '\n'], ['promptSplitEnabled', false, true], ['llmSystemEnabled', true, false]]) {
        const node = { id: 'structure', ...agent, [field]: before };
        const action = beginHuman(api, [node], { fields: [field], prompt: true, at: 710 });
        node[field] = after;
        assert.equal(api.finishSmartHumanEdit(action, [node]), true);
        assert.equal(node.userTouchedAt, 710);
        assert.equal(node.promptEditedByUser, true, `${field} is persisted prompt structure`);
        assert.equal(node.createdBy, 'agent');
        assert.equal(node.agentNodeId, ' opaque agent ');
        const unchanged = { id: 'same', ...agent, [field]: after };
        const noop = beginHuman(api, [unchanged], { fields: [field], prompt: true, at: 711 });
        unchanged[field] = after;
        assert.equal(api.finishSmartHumanEdit(noop, [unchanged]), false);
        assert.equal(unchanged.userTouchedAt, null);
        assert.equal(unchanged.promptEditedByUser, false);
    }
    const node = { id: 'settings', ...agent, llmEnabled: false, llmProvider: 'old', llmModel: 'old', promptPresetId: 'old', refFrameId: 'old' };
    const action = beginHuman(api, [node], { fields: ['llmEnabled', 'llmProvider', 'llmModel', 'promptPresetId', 'refFrameId'], prompt: true, at: 712 });
    Object.assign(node, { llmEnabled: true, llmProvider: 'new', llmModel: 'new', promptPresetId: 'new', refFrameId: 'new' });
    assert.equal(api.finishSmartHumanEdit(action, [node]), true);
    assert.equal(node.userTouchedAt, 712);
    assert.equal(node.promptEditedByUser, false, 'settings and bindings are not prompt structure');
});

test('only explicit prompt=true promotes persisted prompt edits; live identity and protection are never restored from stale snapshots', () => {
    const { api } = core();
    for(const prompt of [false, undefined, 'true', 1]) {
        const node = { id: 'n', ...agent, text: 'before' };
        const action = beginHuman(api, [node], { prompt, at: 400 });
        node.text = '';
        node.createdBy = 'user';
        node.agentNodeId = null;
        assert.equal(api.finishSmartHumanEdit(action, [node]), true);
        assert.deepEqual(node, { id: 'n', createdBy: 'user', agentNodeId: null, userTouchedAt: 400, promptEditedByUser: false, text: '' });
    }
    const node = { id: 'live', ...agent, x: 0, text: 'same' };
    const action = beginHuman(api, [node], { at: 401 });
    node.x = 1;
    node.promptEditedByUser = true; // concurrent adoption since begin
    assert.equal(api.finishSmartHumanEdit(action, [node]), true);
    assert.equal(node.promptEditedByUser, true);
    assert.equal(node.agentNodeId, ' opaque agent ');
});

test('cancelled, same-value, deleted, runtime-only and automatic changes do not touch ownership', () => {
    const { api } = core();
    const node = { id: 'n', ...agent, text: 'same', userTouchedAt: 7, images: [{ id: 'a', layout_w: 10 }], inputImage: { id: 'ref', layout_h: 20 } };
    beginHuman(api, [node], { prompt: true, at: 500 }); // cancelled: never finished
    node.text = 'automatic response without an action seam';
    assert.equal(node.userTouchedAt, 7);
    assert.equal(node.promptEditedByUser, false);
    const noop = beginHuman(api, [node], { prompt: true, at: 501 });
    node.text = String(node.text);
    assert.equal(api.finishSmartHumanEdit(noop, [node]), false);
    node.running = true;
    node.pendingTasks = 2;
    node.conversationSending = true;
    node.images[0].layout_w = 100;
    node.images[0].layout_h = 80;
    node.images[0]._naturalSizeLoading = true;
    node.inputImage.layout_h = 30;
    assert.equal(api.finishSmartHumanEdit(noop, [node]), false);
    assert.equal(node.userTouchedAt, 7);
    assert.equal(node.promptEditedByUser, false);
    const legacy = { id: 'legacy', x: 0 };
    const gone = beginHuman(api, [legacy], { at: 502 });
    legacy.x = 1;
    const live = [{ id: 'other', text: 'remote' }];
    assert.equal(api.finishSmartHumanEdit(gone, live), false);
    assert.deepEqual(live, [{ id: 'other', text: 'remote' }], 'a missing target is never resurrected');
    assert.deepEqual(legacy, { id: 'legacy', x: 1 }, 'missing target is not stamped through the old reference');
    const sameLegacy = beginHuman(api, [legacy]);
    assert.equal(api.finishSmartHumanEdit(sameLegacy, [legacy]), false);
    assert.equal(Object.hasOwn(legacy, 'userTouchedAt'), false, 'no legacy migration on no-op');
});

test('finish obtains one timestamp for all changed live targets and invalid time cannot partially stamp', () => {
    const { ctx, api } = core();
    let clockReads = 0;
    ctx.actionClock = () => { clockReads++; return 600; };
    evaluate(ctx, 'Date.now = actionClock');
    const nodes = [{ id: 'a', ...agent, text: 'old' }, { id: 'b', ...agent, text: 'old' }];
    const action = beginHuman(api, nodes, { prompt: true });
    assert.equal(clockReads, 0, 'begin does not allocate edit time');
    nodes.forEach(node => { node.text = 'new'; });
    assert.equal(api.finishSmartHumanEdit(action, nodes), true);
    assert.equal(clockReads, 1);
    assert.deepEqual(nodes.map(node => node.userTouchedAt), [600, 600]);
    for(const at of [null, false, '700', -1, NaN, Infinity]) {
        const invalid = beginHuman(api, nodes, { prompt: true, at });
        nodes.forEach(node => { node.text += '!'; });
        const beforeFinish = structuredClone(nodes);
        assert.throws(() => api.finishSmartHumanEdit(invalid, nodes), { name: 'TypeError' });
        assert.deepEqual(nodes, beforeFinish, 'no partial metadata application');
    }
    const zero = beginHuman(api, nodes, { at: 0 });
    nodes[0].x = 0;
    assert.equal(api.finishSmartHumanEdit(zero, nodes), true);
    assert.equal(nodes[0].userTouchedAt, 0, 'zero is a valid explicit timestamp');
    assert.equal(nodes[1].userTouchedAt, 600, 'only changed target stamped');
    assert.equal(clockReads, 1, 'explicit at does not read the clock');
});

test('human action requires an explicit durable projector instead of guessing runtime persistence', () => {
    const { api } = core();
    assert.throws(() => api.beginSmartHumanEdit([{id:'local', type:'local-edit', _leBusy:false}]), {name:'TypeError'});
});

constructorContract('createNode');
constructorContract('createPromptNode');
constructorContract('createConversationNode');
constructorContract('createAgentNode');
constructorContract('createLocalEditNode');
constructorContract('createLoopNode');
constructorContract('createSmartGroupNode');

test('constructors reject non-object creation context rather than silently treating it as user authorship', () => {
    for(const name of ['createNode', 'createPromptNode', 'createConversationNode', 'createAgentNode', 'createLocalEditNode', 'createLoopNode', 'createSmartGroupNode']) {
        for(const creation of [false, true, 0, 1, '', 'agent', []]) {
            const { ctx, effects } = nodeOps();
            const invoke = () => name === 'createNode' ? ctx.createNode(0, 0, [], { creation }) : ctx[name](0, 0, { creation });
            assert.throws(invoke, { name: 'TypeError' }, `${name}: malformed author context`);
            assert.equal(ctx.canvasState.nodes.length, 0);
            assert.equal(evaluate(ctx, 'undoStack.length'), 0);
            assert.deepEqual(effects, []);
        }
    }
});
