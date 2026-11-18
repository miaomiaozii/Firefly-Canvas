// #392 2b-1: real settings requests/handlers, with a recording canvas receiver only.
// The parent's adapter lifecycle tests own merge/revision behavior; this fixture never merges.
import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

const fn = (ctx, name) => globalFunction(ctx, name);
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = data => ({ ok: true, json: async () => data });
const originalNode = () => ({
    id: 'n1', type: 'smart-agent', model: 'old-model', autonomy: 'cautious',
    branches: [{ id: 'b1', name: 'Main' }], current_branch: 'b1',
    createdBy: 'agent', agentNodeId: 'owner', userTouchedAt: null, promptEditedByUser: false,
});

function setup({ nodes = [originalNode()], accepted = true, bridge = true } = {}) {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-agent-tui.js']);
    ctx.canvasState.nodes = nodes;
    ctx.canvasId = 'project';
    ctx.chatApiProviders = () => [{ id: 'offline' }];
    ctx.providerChatModels = () => ['old-model', 'runtime-model'];
    ctx.receiveAgentSettingsCanvas = bridge ? canvas => { received.push(canvas); return accepted; } : undefined;
    const received = [], calls = [], renders = [], errors = [];
    ctx.renderAgentNode = id => renders.push(id);
    ctx.console = { error: (...args) => errors.push(args.map(String).join(' ')) };
    ctx.fetch = (url, opts = {}) => new Promise((resolve, reject) => calls.push({ url, opts, resolve, reject }));
    const state = fn(ctx, 'getAgentTuiState')('n1');
    state.messages.push({ role: 'assistant', text: 'old history' });
    state.toolActivities.set('old-tool', { name: 'read', status: 'done' });
    return { ctx, harness, state, calls, received, renders, errors };
}

const persisted = ctx => fromContext(ctx.canvasState.nodes);
const actions = [
    ['setAgentModel', ['first-model'], { model: 'first-model', persisted: true }],
    ['setAgentAutonomy', ['standard'], { autonomy: 'standard' }],
    ['forkAgentSession', ['entry', 'Fork'], { branches: [{ id: 'fork' }], current_branch: 'fork' }],
    ['switchAgentBranch', ['b2'], { current_branch: 'b2' }],
];

test('a still-pending or failed newer action is not replaced by an older successful completion', async () => {
    for (const newestFailsFirst of [false, true]) {
        const m = setup();
        const older = fn(m.ctx, 'forkAgentSession')('n1', 'project', 'entry', 'Older fork');
        const newer = fn(m.ctx, 'setAgentModel')('n1', 'project', 'runtime-model');
        if (newestFailsFirst) {
            m.calls[1].resolve({ ok: false, status: 409, json: async () => ({ detail: 'latest conflict' }) });
            await newer;
        }
        const renderCount = m.renders.length;
        m.calls[0].resolve(response({ success: true, canvas: { revision: 50, nodes: [originalNode()] } }));
        await older;
        assert.deepEqual(m.received, []);
        assert.equal(m.state.messages.length, 1);
        assert.equal(m.state.toolActivities.size, 1);
        assert.equal(m.renders.length, renderCount);
        if (!newestFailsFirst) {
            m.calls[1].resolve({ ok: false, status: 409, json: async () => ({ detail: 'latest conflict' }) });
            await newer;
        }
        assert.equal(m.harness.toasts.length, 1);
        assert.match(m.harness.toasts[0].message, /latest conflict/);
    }
});

test('branch acknowledgements preserve new D2 thinking, turns and buffered live events', async () => {
    for (const name of ['forkAgentSession', 'switchAgentBranch']) {
        for (const activity of ['thinking', 'turn', 'buffered']) {
            const m = setup();
            const args = name === 'forkAgentSession' ? ['entry', 'Fork'] : ['b2'];
            const pending = fn(m.ctx, name)('n1', 'project', ...args);
            if (activity === 'buffered') m.state._replayInFlight = true;
            fn(m.ctx, 'handleAgentEvent')('n1', activity === 'thinking'
                ? { type: 'message_update', assistantMessageEvent: { type: 'thinking_delta', delta: 'new D2 thought' } }
                : activity === 'turn' ? { type: 'agent_start' }
                : { type: 'user_prompt', text: 'buffered live prompt', seq: 8 });
            m.calls[0].resolve(response({ success: true, canvas: { revision: 50, nodes: [originalNode()] } }));
            await pending;
            assert.equal(m.state.messages.length, 1, `${name}/${activity}: new activity forbids history reset`);
            assert.equal(m.state.toolActivities.size, 1);
            if (activity === 'thinking') assert.equal(m.state.thinking[0].text, 'new D2 thought');
            if (activity === 'turn') assert.equal(m.state._turnSeq, 1);
            if (activity === 'buffered') {
                assert.equal(m.state._replayInFlight, true);
                assert.equal(m.state._replayBuffer[0].text, 'buffered live prompt');
            }
        }
    }
});

test('network and non-JSON HTTP failures remain visible without persisted or history changes', async () => {
    for (const [name, args] of actions) {
        for (const network of [false, true]) {
            const m = setup();
            const before = persisted(m.ctx);
            const pending = fn(m.ctx, name)('n1', 'project', ...args);
            if (network) m.calls[0].reject(new Error('offline'));
            else m.calls[0].resolve({ ok: false, status: 503, json: async () => { throw new Error('not JSON'); } });
            await pending;
            assert.deepEqual(persisted(m.ctx), before);
            assert.deepEqual(m.received, []);
            assert.equal(m.state.messages.length, 1);
            assert.equal(m.state.toolActivities.size, 1);
            assert.equal(m.harness.toasts.length, 1);
            assert.ok(m.errors.length > 0);
        }
    }
});

test('no canvas, missing receiver or receiver rejection grants no persistent authority or history reset', async () => {
    for (const [name, args, data] of actions) {
        for (const mode of ['no-canvas', 'missing-bridge', 'rejected-canvas']) {
            const m = setup({ bridge: mode !== 'missing-bridge', accepted: mode !== 'rejected-canvas' });
            const before = persisted(m.ctx);
            const renderBefore = m.renders.length;
            const pending = fn(m.ctx, name)('n1', 'project', ...args);
            const canvas = { revision: 40, nodes: [{ ...originalNode(), model: 'not-adopted' }] };
            m.calls[0].resolve(response({ success: true, ...data, ...(mode === 'no-canvas' ? {} : { canvas }) }));
            await pending;
            assert.deepEqual(persisted(m.ctx), before);
            assert.equal(m.state.model, '');
            assert.equal(m.state.autonomy, 'cautious');
            assert.equal(m.state.messages.length, 1);
            assert.equal(m.state.toolActivities.size, 1);
            assert.deepEqual(m.received, mode === 'rejected-canvas' ? [canvas] : []);
            assert.equal(m.renders.length, renderBefore + (name === 'setAgentModel' || name === 'setAgentAutonomy' ? 1 : 0), 'no history/body adoption without authority');
            if (name === 'forkAgentSession' || name === 'switchAgentBranch') assert.match(m.harness.toasts[0].message, /未收到可应用/);
        }
    }
});

test('cleanup during response JSON or inside the receiver leaves neither retained nor recreated state changed', async () => {
    for (const [name, args, data] of actions) {
        const m = setup();
        const pending = fn(m.ctx, name)('n1', 'project', ...args);
        let finishJson;
        m.calls[0].resolve({ ok: true, json: () => new Promise(resolve => { finishJson = resolve; }) });
        await flush();
        fn(m.ctx, 'cleanupAgentTuiState')('n1');
        finishJson({ success: true, ...data, canvas: { revision: 41, nodes: [originalNode()] } });
        await pending;
        assert.deepEqual(m.received, []);
        assert.equal(m.state.messages.length, 1);
        assert.equal(evaluate(m.ctx, 'agentTuiState.has("n1")'), false);

        const duringBridge = setup();
        duringBridge.ctx.receiveAgentSettingsCanvas = canvas => {
            duringBridge.received.push(canvas);
            fn(duringBridge.ctx, 'cleanupAgentTuiState')('n1');
            return true;
        };
        const adopting = fn(duringBridge.ctx, name)('n1', 'project', ...args);
        const renderCount = duringBridge.renders.length;
        duringBridge.calls[0].resolve(response({ success: true, ...data, canvas: { revision: 42, nodes: [originalNode()] } }));
        await adopting;
        assert.equal(duringBridge.received.length, 1);
        assert.equal(duringBridge.renders.length, renderCount);
        assert.equal(duringBridge.state.messages.length, 1);
        assert.equal(duringBridge.state.toolActivities.size, 1);
        assert.equal(evaluate(duringBridge.ctx, 'agentTuiState.has("n1")'), false);
    }
});

test('real bound settings handlers declare human intent without optimistic persisted changes', async () => {
    const m = setup();
    const before = persisted(m.ctx);
    // This settings-only fixture starts connected with its existing history already loaded.
    m.state.socket = { readyState: 1 };
    m.state.historyLoaded = true;
    m.state.chatModelsFetched = true;
    const el = m.harness.makeElement('div');
    const controls = Object.fromEntries(['model-picker', 'autonomy-choice', 'more-branch', 'fork'].map(key => {
        const control = m.harness.makeElement(key === 'more-branch' ? 'select' : 'button');
        control.setAttribute(`data-agent-${key}`, key === 'autonomy-choice' ? 'autonomous' : '');
        control.closest = () => null;
        return [key, control];
    }));
    const create = m.ctx.document.createElement;
    m.ctx.document.createElement = tag => {
        const element = create(tag), fields = new Map();
        // The picker replaces its controls on every render, as the browser does.
        let markup = '';
        Object.defineProperty(element, 'innerHTML', {
            get: () => markup,
            set: value => { markup = value; fields.clear(); },
        });
        element.querySelector = selector => {
            if (!fields.has(selector)) fields.set(selector, m.harness.makeElement('button'));
            return fields.get(selector);
        };
        return element;
    };
    el.querySelectorAll = selector => Object.entries(controls).filter(([key]) => selector.split(',').includes(`[data-agent-${key}]`)).map(([,control]) => control);
    el.querySelector = selector => selector === '[data-agent-fork]' ? controls.fork : null;
    fn(m.ctx, 'bindAgentNodeControls')(el, m.ctx.canvasState.nodes[0]);
    const event = (type, extra = {}) => ({ type, stopPropagation() {}, ...extra });
    controls['model-picker'].dispatchEvent(event('click'));
    const caps = model => ({ host:'pi', status:'available', models:[{id:'old-model', name:'Old'}, {id:'runtime-model', name:'New'}],
        defaults:{model:null, effort:null}, selection:{model, efforts:['low'], source:'pi'} });
    assert.match(m.calls[0].url, /capabilities\?host=pi&model=old-model$/);
    m.calls[0].resolve(response(caps('old-model'))); await flush();
    const picker = m.state.modelPicker.dialog;
    picker.querySelector('[data-agent-picker-model]').dispatchEvent(event('change', { target:{value:'runtime-model'} }));
    assert.match(m.calls[1].url, /capabilities\?host=pi&model=runtime-model$/);
    m.calls[1].resolve(response(caps('runtime-model'))); await flush();
    picker.querySelector('[data-agent-picker-apply]').dispatchEvent(event('click'));
    assert.deepEqual(JSON.parse(m.calls[2].opts.body), { host:'pi', model:'runtime-model', effort:null, user_edit:true });
    assert.deepEqual(persisted(m.ctx), before);
    m.calls[2].resolve({ ok:false, status:409, json:async () => ({detail:'model rejected'}) }); await flush();
    assert.match(m.harness.toasts.shift().message, /model rejected/);

    controls['autonomy-choice'].dispatchEvent(event('click'));
    controls['more-branch'].value = 'b2';
    controls['more-branch'].dispatchEvent(event('change'));
    assert.equal(controls['more-branch'].value, 'b1', 'native select stays on authoritative branch pending receipt');
    controls.fork.dispatchEvent(event('click'));
    assert.deepEqual(m.calls.slice(3, 5).map(call => JSON.parse(call.opts.body)), [
        { autonomy: 'autonomous', user_edit: true }, { branch_id: 'b2', user_edit: true },
    ]);
    assert.equal(m.calls[5].opts.body, undefined, 'picker GET does not claim a human persisted edit');
    assert.deepEqual(persisted(m.ctx), before);
    m.calls[5].resolve(response({ messages: [{ text: 'Fork here', entryId: 'e1' }] }));
    await flush();
    const dlg = m.harness.dialogs[1];
    const radio = m.harness.makeElement('input');
    radio.value = '0';
    const name = m.harness.makeElement('input');
    name.value = 'Branch';
    dlg.querySelector = selector => selector === 'input[name="forkidx"]:checked' ? radio : selector === 'input[name="branchname"]' ? name : null;
    dlg.close('ok');
    await flush();
    assert.deepEqual(JSON.parse(m.calls[6].opts.body), { entry_id: 'e1', name: 'Branch', user_edit: true });
    for (const call of [...m.calls.slice(3, 5), m.calls[6]]) {
        call.resolve({ ok: false, status: 409, json: async () => ({ detail: 'rejected' }) });
    }
    await flush();
    assert.deepEqual(persisted(m.ctx), before, 'rejected native handlers leave autosave projection unchanged');
    assert.equal(m.harness.toasts.length, 1, 'only latest setting may show its error');
    assert.match(m.harness.toasts[0].message, /分叉失败.*rejected/);
});

test('fork picker cannot reopen or send a fork after its node lifecycle was cleaned up', async () => {
    const beforeDialog = setup();
    const loading = fn(beforeDialog.ctx, 'showForkPicker')('n1', 'project');
    fn(beforeDialog.ctx, 'cleanupAgentTuiState')('n1');
    beforeDialog.calls[0].resolve(response({ messages: [{ text: 'Fork here', entryId: 'e1' }] }));
    await flush();
    assert.equal(beforeDialog.harness.dialogs.length, 0, 'late picker response must not reopen deleted UI');
    await loading;
    assert.equal(evaluate(beforeDialog.ctx, 'agentTuiState.has("n1")'), false);

    const inDialog = setup();
    const picking = fn(inDialog.ctx, 'showForkPicker')('n1', 'project');
    inDialog.calls[0].resolve(response({ messages: [{ text: 'Fork here', entryId: 'e1' }] }));
    await flush();
    const dlg = inDialog.harness.dialogs[0];
    const radio = inDialog.harness.makeElement('input');
    radio.value = '0';
    const branchName = inDialog.harness.makeElement('input');
    branchName.value = 'Stale branch';
    dlg.querySelector = selector => selector === 'input[name="forkidx"]:checked' ? radio : selector === 'input[name="branchname"]' ? branchName : null;
    fn(inDialog.ctx, 'cleanupAgentTuiState')('n1');
    dlg.close('ok');
    await flush();
    assert.equal(inDialog.calls.length, 1, 'closed stale picker must not send a fork or recreate state');
    await picking;
    assert.equal(evaluate(inDialog.ctx, 'agentTuiState.has("n1")'), false);
});

test('session-only model success updates runtime display without inventing persisted ownership or requiring a canvas', async () => {
    const m = setup({ nodes: [] });
    const pending = fn(m.ctx, 'setAgentModel')('n1', 'project', 'runtime-model');
    assert.equal(m.state.model, '');
    m.calls[0].resolve(response({ success: true, model: 'runtime-model', persisted: false }));
    await pending;
    assert.equal(m.state.model, 'runtime-model');
    assert.deepEqual(persisted(m.ctx), []);
    assert.deepEqual(m.received, []);
    // Rendering here exercises the model label, not history prefetch or replay.
    m.state.historyLoaded = true;
    const body = fn(m.ctx, 'agentNodeBodyHtml')({ id: 'n1', type: 'smart-agent' });
    assert.equal(m.calls.length, 1, 'rendering the loaded view must not add a history request');
    assert.match(body, /data-agent-model-picker="n1"[^>]*aria-label="模型 runtime-model"[^>]*><span[^>]*>模型<\/span><b>runtime-model<\/b>/);

    const withCanvas = setup();
    const runtimeOnly = fn(withCanvas.ctx, 'setAgentModel')('n1', 'project', 'runtime-model');
    const unchangedCanvas = { revision: 30, nodes: [originalNode()] };
    withCanvas.calls[0].resolve(response({ success: true, model: 'runtime-model', persisted: false, canvas: unchangedCanvas }));
    await runtimeOnly;
    assert.deepEqual(withCanvas.received, [unchangedCanvas], 'available authoritative canvas still goes through the receiver');
    assert.deepEqual(persisted(withCanvas.ctx), [originalNode()], 'runtime model does not stamp persisted ownership even if a node exists locally');
    assert.equal(withCanvas.state.model, 'runtime-model');
    withCanvas.state.historyLoaded = true;

    const stale = fn(m.ctx, 'setAgentModel')('n1', 'project', 'old-model');
    const current = fn(m.ctx, 'setAgentModel')('n1', 'project', 'runtime-model');
    m.calls[2].resolve(response({ success: true, model: 'runtime-model', persisted: false }));
    await current;
    m.calls[1].resolve(response({ success: true, model: 'old-model', persisted: false }));
    await stale;
    assert.equal(m.state.model, 'runtime-model');
    assert.deepEqual(persisted(m.ctx), []);

    // A later persistent model acceptance drops the session-only display override.
    m.ctx.canvasState.nodes = [originalNode()];
    const persistent = fn(m.ctx, 'setAgentModel')('n1', 'project', 'old-model');
    const canvas = { revision: 31, nodes: [originalNode()] };
    m.calls[3].resolve(response({ success: true, model: 'old-model', persisted: true, canvas }));
    await persistent;
    assert.deepEqual(m.received, [canvas]);
    assert.match(fn(m.ctx, 'agentNodeBodyHtml')(m.ctx.canvasState.nodes[0]), /data-agent-model-picker="n1"[^>]*aria-label="模型 old-model"[^>]*><span[^>]*>模型<\/span><b>old-model<\/b>/);
});

test('fork/switch success cannot erase messages or tool updates that arrived while the request was pending', async () => {
    for (const [name, args, data] of actions.slice(2)) {
        for (const event of [
            { type: 'user_prompt', text: 'new message' },
            { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'more' } },
            { type: 'tool_execution_start', toolCallId: 'new-tool', toolName: 'write' },
            { type: 'tool_execution_end', toolCallId: 'old-tool', result: 'new result', isError: true },
        ]) {
            const m = setup();
            // A streaming update can change an existing message without changing array length.
            m.state.messages[0].streaming = true;
            const pending = fn(m.ctx, name)('n1', 'project', ...args);
            fn(m.ctx, 'handleAgentEvent')('n1', event);
            const messages = fromContext(m.state.messages);
            const tools = fromContext([...m.state.toolActivities]);
            const canvas = { revision: 30, nodes: [originalNode()] };
            m.calls[0].resolve(response({ success: true, ...data, canvas }));
            await pending;
            assert.deepEqual(m.received, [canvas]);
            assert.deepEqual(fromContext(m.state.messages), messages);
            assert.deepEqual(fromContext([...m.state.toolActivities]), tools);
        }
    }
});

test('settings completions follow live replacement nodes, but cannot revive deletion or cleanup lifecycles', async () => {
    for (const [name, args, data] of actions) {
        const replaced = setup();
        const oldNode = replaced.ctx.canvasState.nodes[0];
        const pending = fn(replaced.ctx, name)('n1', 'project', ...args);
        const liveNode = { ...originalNode(), model: 'remote-model', title: 'remote replacement' };
        replaced.ctx.canvasState.nodes = [liveNode];
        const canvas = { revision: 20, nodes: [{ ...liveNode, model: 'authoritative-model' }] };
        replaced.calls[0].resolve(response({ success: true, ...data, canvas }));
        await pending;
        assert.deepEqual(replaced.received, [canvas]);
        assert.deepEqual(oldNode, originalNode(), 'no stale captured node may be changed');
        assert.equal(liveNode.model, 'remote-model', 'only the receiver owns replacement/adoption');

        for (const lifecycle of ['delete', 'cleanup', 'cleanup-and-recreate']) {
            for (const failure of [false, true]) {
                const m = setup();
                const request = fn(m.ctx, name)('n1', 'project', ...args);
                if (lifecycle === 'delete') m.ctx.canvasState.nodes = [];
                else fn(m.ctx, 'cleanupAgentTuiState')('n1');
                const fresh = lifecycle === 'cleanup-and-recreate' ? fn(m.ctx, 'getAgentTuiState')('n1') : null;
                const renderCount = m.renders.length;
                m.calls[0].resolve(failure ? { ok: false, status: 500, json: async () => ({}) }
                    : response({ success: true, ...data, canvas }));
                await request;
                assert.deepEqual(m.received, [], `${name}: ${lifecycle} must not adopt late canvas`);
                assert.equal(m.renders.length, renderCount);
                assert.equal(m.state.messages.length, 1, 'retained removed state is not updated');
                assert.equal(m.state.toolActivities.size, 1);
                assert.deepEqual(m.harness.toasts, []);
                if (fresh) assert.equal(fresh.messages.length, 0);
                if (lifecycle === 'cleanup') assert.equal(evaluate(m.ctx, 'agentTuiState.has("n1")'), false);
            }
        }
    }
});

test('late same/different setting completions cannot overwrite newer UI, errors, messages or tool activities', async () => {
    for (const [firstName, firstArgs, firstData] of actions) {
        for (const [nextName, nextArgs, nextData] of actions) {
            for (const lateFailure of [false, true]) {
                const m = setup();
                const first = fn(m.ctx, firstName)('n1', 'project', ...firstArgs);
                const next = fn(m.ctx, nextName)('n1', 'project', ...nextArgs);
                const newest = { revision: 12, nodes: [originalNode()] };
                m.calls[1].resolve(response({ success: true, ...nextData, canvas: newest }));
                await next;
                await flush();
                fn(m.ctx, 'handleAgentEvent')('n1', { type: 'user_prompt', text: 'new history' });
                fn(m.ctx, 'handleAgentEvent')('n1', { type: 'tool_execution_start', toolCallId: 'new-tool', toolName: 'write' });
                const messages = fromContext(m.state.messages);
                const tools = fromContext([...m.state.toolActivities]);
                const renderCount = m.renders.length;
                m.calls[0].resolve(lateFailure
                    ? { ok: false, status: 409, json: async () => ({ detail: 'obsolete failure' }) }
                    : response({ success: true, ...firstData, canvas: { revision: 11, nodes: [] } }));
                await first;
                await flush();
                assert.deepEqual(m.received, [newest], `${firstName} after ${nextName}: stale canvas must not reach receiver`);
                assert.deepEqual(fromContext(m.state.messages), messages);
                assert.deepEqual(fromContext([...m.state.toolActivities]), tools);
                assert.equal(m.renders.length, renderCount, 'late response must not rebuild newer UI');
                assert.deepEqual(m.harness.toasts, [], 'obsolete errors must not overwrite current feedback');
            }
        }
    }
});

test('branch switch cannot publish legacy branch fields; accepted canvas alone authorizes history reset', async () => {
    const m = setup();
    const before = persisted(m.ctx);
    const pending = fn(m.ctx, 'switchAgentBranch')('n1', 'project', 'b2');
    assert.deepEqual(JSON.parse(m.calls[0].opts.body), { branch_id: 'b2', user_edit: true });
    assert.deepEqual(persisted(m.ctx), before);
    const canvas = { revision: 10, nodes: [{ ...originalNode(), current_branch: 'b2' }] };
    m.calls[0].resolve(response({ success: true, current_branch: 'legacy-not-authority', canvas }));
    await pending;
    assert.deepEqual(m.received, [canvas]);
    assert.deepEqual(persisted(m.ctx), before);
    assert.deepEqual(fromContext(m.state.messages), []);
    assert.equal(m.state.toolActivities.size, 0);

    const bad = setup();
    const rejected = fn(bad.ctx, 'switchAgentBranch')('n1', 'project', 'b2');
    bad.calls[0].resolve({ ok: false, status: 500, json: async () => ({ detail: 'offline' }) });
    await rejected;
    assert.deepEqual(persisted(bad.ctx), before);
    assert.equal(bad.state.messages.length, 1);
    assert.equal(bad.state.toolActivities.size, 1);
    assert.match(bad.harness.toasts[0].message, /切换分支失败/);
});

test('fork accepts branches only through the canvas receiver and clears history only on accepted success', async () => {
    const m = setup();
    const before = persisted(m.ctx);
    const pending = fn(m.ctx, 'forkAgentSession')('n1', 'project', 'entry-7', 'New branch');
    assert.deepEqual(JSON.parse(m.calls[0].opts.body), { entry_id: 'entry-7', name: 'New branch', user_edit: true });
    assert.deepEqual(persisted(m.ctx), before);
    const canvas = { revision: 9, nodes: [{ ...originalNode(), current_branch: 'b2' }] };
    m.calls[0].resolve(response({ success: true, branches: [{ id: 'b2' }], current_branch: 'b2', canvas }));
    await pending;
    assert.deepEqual(m.received, [canvas]);
    assert.deepEqual(persisted(m.ctx), before);
    assert.deepEqual(fromContext(m.state.messages), []);
    assert.equal(m.state.toolActivities.size, 0);

    const bad = setup();
    const rejected = fn(bad.ctx, 'forkAgentSession')('n1', 'project', 'entry-7', 'No');
    bad.calls[0].resolve({ ok: false, status: 409, json: async () => ({ detail: 'fork conflict' }) });
    await rejected;
    assert.deepEqual(persisted(bad.ctx), before);
    assert.equal(bad.state.messages.length, 1);
    assert.equal(bad.state.toolActivities.size, 1);
    assert.deepEqual(bad.received, []);
    assert.match(bad.harness.toasts[0].message, /分叉失败.*fork conflict/);
});

test('autonomy request keeps persisted values until the server canvas is received, with visible rejection', async () => {
    const m = setup();
    const before = persisted(m.ctx);
    const pending = fn(m.ctx, 'setAgentAutonomy')('n1', 'project', 'autonomous');
    assert.deepEqual(JSON.parse(m.calls[0].opts.body), { autonomy: 'autonomous', user_edit: true });
    assert.deepEqual(persisted(m.ctx), before);
    assert.equal(m.state.autonomy, 'cautious');
    m.calls[0].resolve({ ok: false, status: 403, json: async () => ({ detail: 'not allowed' }) });
    await pending;
    await flush();
    assert.deepEqual(persisted(m.ctx), before);
    assert.match(m.harness.toasts[0].message, /自主级别切换失败.*not allowed/);
    assert.deepEqual(m.received, []);
    const accepted = fn(m.ctx, 'setAgentAutonomy')('n1', 'project', 'standard');
    const canvas = { revision: 8, nodes: [{ ...originalNode(), autonomy: 'standard' }] };
    m.calls[1].resolve(response({ success: true, autonomy: 'standard', canvas }));
    await accepted;
    await flush();
    assert.deepEqual(m.received, [canvas]);
    assert.deepEqual(persisted(m.ctx), before, 'receiver, not legacy fields, owns node updates');
});

test('model request declares human intent and never exposes an unconfirmed model to autosave', async () => {
    const m = setup();
    const before = persisted(m.ctx);
    const pending = fn(m.ctx, 'setAgentModel')('n1', 'proj/2', 'new-model');
    assert.deepEqual(JSON.parse(m.calls[0].opts.body), { model: 'new-model', user_edit: true });
    assert.equal(m.calls[0].url, '/api/projects/proj/2/agent-nodes/n1/model', 'preserve existing URI encoding');
    assert.deepEqual(persisted(m.ctx), before, 'another autosave sees only the old persisted projection');
    assert.equal(m.state.model, '', 'unconfirmed selection is not a runtime fact either');
    m.calls[0].resolve({ ok: false, status: 409, json: async () => ({ detail: 'conflicting update' }) });
    await pending;
    await flush();
    assert.deepEqual(persisted(m.ctx), before);
    assert.deepEqual(m.received, []);
    assert.match(m.harness.toasts[0].message, /模型切换失败.*conflicting update/);

    const accepted = fn(m.ctx, 'setAgentModel')('n1', 'project', 'confirmed-model');
    const canvas = { revision: 7, nodes: [{ ...originalNode(), model: 'confirmed-model', userTouchedAt: 'server-time' }] };
    m.calls[1].resolve(response({ success: true, model: 'confirmed-model', persisted: true, canvas }));
    await accepted;
    await flush();
    assert.deepEqual(m.received, [canvas], 'authoritative canvas must cross the agreed receiver seam');
    assert.deepEqual(persisted(m.ctx), before, 'TUI must not duplicate the receiver merge or stamp ownership');
});
