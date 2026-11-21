// #414: real composer -> binder -> request -> authority/merge/storage -> remount.
// Only DOM, layout and HTTP are boundaries; no receiver or persistence algorithm is copied.
import assert from 'node:assert/strict';
import test from 'node:test';
import { loadModules, evaluate, fromContext } from './harness.mjs';
import { editor } from './smart-human-handlers-harness-392.mjs';

const copy = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = data => ({ ok: true, status: 200, json: async () => data });
const event = type => ({ type, stopPropagation() {}, preventDefault() {} });
const levels = [['cautious', '每张'], ['standard', '每批'], ['autonomous', '无需']];
const seed = () => [
    { id: 'agent', type: 'smart-agent', title: 'Work', agentCli: 'pi', autonomy: 'standard',
        createdBy: 'agent', agentNodeId: 'creator', userTouchedAt: null, model: '',
        agentHostSettings: { pi: { model: '', effort: null } },
        agentHostSessions: { pi: { extSessionId: 'retained-binding', branches: [], current_branch: '' } } },
    { id: 'prompt', type: 'smart-prompt', text: 'server text' },
];

function setup(nodes = seed()) {
    const m = editor(copy(nodes));
    const { ctx, harness } = m;
    loadModules(ctx, ['canvas-agent-navigation.js', 'canvas-agent-tui.js']);
    evaluate(ctx, 'canvas.revision=4; canvas.updated_at=400;');
    const state = ctx.getAgentTuiState('agent');
    state.historyLoaded = true;
    state.socket = { readyState: 1, close() {} };
    ctx.WebSocket = class { static OPEN = 1; };
    const calls = [], errors = [];
    ctx.fetch = (url, options = {}) => new Promise((resolve, reject) => calls.push({ url, options, resolve, reject }));
    ctx.console = { error: (...args) => errors.push(args), warn() {} };
    ctx.imageLayout = () => ({ x: 0, y: 0, width: 480, height: 500 });
    ctx.nodeBodyHtml = node => ctx.agentNodeBodyHtml(node);
    const root = harness.makeElement('section'), body = harness.makeElement('div');
    let markup = '', controls = [];
    // The shared harness deliberately does not parse HTML. Project only real
    // rendered autonomy buttons into DOM handles, never infer product state here.
    Object.defineProperty(body, 'innerHTML', {
        get: () => markup,
        set: html => {
            markup = html;
            controls = [...html.matchAll(/<button\b([^>]*\bdata-agent-autonomy-choice="[^"]+"[^>]*)>([^<]*)<\/button>/g)].map(([, attrs, label]) => {
                const button = harness.makeElement('button');
                for (const [, name, value] of attrs.matchAll(/([\w-]+)="([^"]*)"/g)) button.setAttribute(name, value);
                button.disabled = /\sdisabled(?:\s|$)/.test(attrs);
                button.textContent = label;
                return button;
            });
        },
    });
    body.querySelectorAll = selector => selector === '[data-agent-autonomy-choice]' ? controls : [];
    root.querySelector = selector => selector === '.node-body' ? body : null;
    harness.querySelectorAll('[data-id="agent"]', [root]);
    const buttons = () => controls;
    const click = value => {
        const button = controls.find(item => item.getAttribute('data-agent-autonomy-choice') === value);
        assert.ok(button, `rendered ${value} button must exist`);
        // Dispatch even when disabled to also verify the binder's state guard.
        button.dispatchEvent(event('click'));
    };
    const selected = () => controls.filter(button => button.getAttribute('aria-pressed') === 'true')
        .map(button => button.getAttribute('data-agent-autonomy-choice'));
    const canvas = () => fromContext(evaluate(ctx, 'canvas'));
    const render = () => ctx.renderAgentNode('agent');
    const authority = (autonomy, revision = 5) => {
        const data = canvas();
        data.revision = revision; data.updated_at = revision * 100;
        Object.assign(data.nodes.find(node => node.id === 'agent'), { autonomy, userTouchedAt: revision * 100 });
        return data;
    };
    render();
    return { ...m, state, calls, errors, root, body, buttons, click, selected, canvas, render, authority };
}

function assertRequest(call, autonomy) {
    assert.equal(call.url, '/api/projects/unit-392/agent-nodes/agent/autonomy');
    assert.equal(call.options.method, 'POST');
    assert.deepEqual(fromContext(call.options.headers), { 'Content-Type': 'application/json' });
    assert.deepEqual(JSON.parse(call.options.body), { autonomy, user_edit: true });
}

for (const dock of [null, 'left', 'right']) {
    test(`three composer choices use real authority/storage and survive a fresh TUI (${dock || 'floating'})`, async () => {
        const nodes = seed(); nodes[0].dock = dock;
        const m = setup(nodes);
        m.ctx.canvasState.nodes.find(node => node.id === 'prompt').text = 'unsaved human text';
        m.ctx.scheduleSave();
        for (const value of ['cautious', 'autonomous', 'standard']) {
            assert.deepEqual(m.buttons().map(button => [button.getAttribute('data-agent-autonomy-choice'), button.textContent]), levels);
            const head = m.ctx.agentHeadActionButtonsHtml(m.node('agent'));
            assert.doesNotMatch(head, /data-agent-autonomy|agent-tui-autonomy|每张|每批|无需/);
            assert.doesNotMatch(m.body.innerHTML, /<select\b[^>]*data-agent-autonomy(?:[=\s>])|data-lucide/);
            assert.ok(m.body.innerHTML.indexOf('agent-tui-composer') < m.body.innerHTML.indexOf('data-agent-autonomy-choice'));
            const old = m.selected()[0], before = m.stored(), count = m.calls.length;
            m.click(old);
            assert.equal(m.calls.length, count, 'selected choice is a no-op');
            m.click(value);
            assert.equal(m.calls.length, count + 1);
            assertRequest(m.calls[count], value);
            assert.deepEqual(m.stored(), before, 'pending selection must not reach ordinary autosave');
            assert.deepEqual(m.selected(), [old], 'pending request cannot claim server authority');
            assert.equal(evaluate(m.ctx, 'undoStack.length'), count, 'pending selection creates no history step');
            const remote = m.authority(value, 5 + count);
            remote.nodes.find(node => node.id === 'prompt').text = 'server text';
            m.calls[count].resolve(response({ success: true, autonomy: old, canvas: remote }));
            await tick();
            assert.deepEqual(m.selected(), [value], 'canvas, not the legacy response scalar, selects the control');
            assert.equal(m.node('agent').autonomy, value);
            assert.equal(m.node('agent').userTouchedAt, (5 + count) * 100);
            assert.equal(m.node('prompt').text, 'unsaved human text', 'autonomy receive preserves unrelated pending edits');
            assert.equal(evaluate(m.ctx, 'smartCanvasServerShadow.nodes.find(node => node.id === "prompt").text'), 'server text');
            assert.equal(evaluate(m.ctx, 'undoStack.length'), count + 1);
            assert.equal(m.harness.toasts.length, 0);
            const stored = m.stored(), fresh = setup(stored);
            assert.deepEqual(fresh.selected(), [value], 'new VM must reconstruct selection from stored canvas fields');
            assert.equal(fresh.node('agent').dock, dock);
            assert.equal(fresh.node('agent').agentHostSessions.pi.extSessionId, 'retained-binding');
            assert.equal(fresh.calls.length, 0, 'remount never POSTs or probes a host');
            assert.equal(m.root, m.harness.queryResults['[data-id="agent"]'][0], 'in-place renderer retains the mounted node');
        }
    });
}

for (const mode of ['network', 'http', 'non-json', 'no-canvas', 'wrong-project', 'wrong-canvas', 'older-revision']) {
    test(`bound autonomy choice cannot persist or select an unsuccessful receipt: ${mode}`, async () => {
        const m = setup(), before = m.stored();
        m.click('autonomous');
        const call = m.calls[0];
        assertRequest(call, 'autonomous');
        if (mode === 'network') call.reject(new Error('offline'));
        else if (mode === 'http') call.resolve({ ok: false, status: 409, json: async () => ({ detail: 'conflict' }) });
        else if (mode === 'non-json') call.resolve({ ok: true, status: 200, json: async () => { throw new Error('invalid JSON'); } });
        else {
            const canvas = m.authority('autonomous', mode === 'older-revision' ? 3 : 5);
            if (mode === 'wrong-project') canvas.project_id = 'other-project';
            if (mode === 'wrong-canvas') canvas.id = 'other-canvas';
            call.resolve(response({ success: true, autonomy: 'autonomous', ...(mode === 'no-canvas' ? {} : { canvas }) }));
        }
        await tick();
        assert.deepEqual(m.stored(), before);
        assert.deepEqual(m.selected(), ['standard']);
        assert.equal(evaluate(m.ctx, 'undoStack.length'), 0);
        assert.equal(m.calls.length, 1);
        if (['network', 'http', 'non-json'].includes(mode)) {
            assert.equal(m.harness.toasts.length, 1);
            assert.match(m.harness.toasts[0].message, /自主级别切换失败.*(?:offline|conflict|invalid JSON)/);
        }
    });
}

for (const replaced of [false, true]) {
    test(`late HTTP receipt after newer broadcasts ${replaced ? 'cannot undo a superseding choice' : 'recognizes already accepted authority'}`, async () => {
        const m = setup();
        m.click('autonomous');
        const receipt = m.authority('autonomous', 5);
        m.ctx.applyMergedServerCanvas(copy(receipt)); m.render();
        assert.deepEqual(m.selected(), ['autonomous']);
        const latest = copy(receipt); latest.revision = 6; latest.updated_at = 600;
        latest.nodes.find(node => node.id === 'prompt').text = 'newer server edit';
        if (replaced) Object.assign(latest.nodes[0], { autonomy: 'cautious', userTouchedAt: 600 });
        m.ctx.applyMergedServerCanvas(latest); m.render();
        m.calls[0].resolve(response({ success: true, canvas: receipt }));
        await tick();
        assert.deepEqual(m.selected(), [replaced ? 'cautious' : 'autonomous']);
        assert.equal(m.canvas().revision, 6);
        assert.equal(m.node('prompt').text, 'newer server edit');
        assert.equal(evaluate(m.ctx, 'undoStack.length'), replaced ? 0 : 1);
        assert.equal(m.harness.toasts.length, 0);
    });
}

for (const phase of ['fetch', 'json']) {
    for (const lifecycle of ['project', 'canvas', 'deleted', 'recreated']) {
        test(`late ${phase} autonomy receipt is inert after ${lifecycle} context changes`, async () => {
            const m = setup();
            m.click('autonomous');
            const data = { success: true, canvas: m.authority('autonomous') };
            let finish;
            if (phase === 'json') {
                m.calls[0].resolve({ ok: true, json: () => new Promise(resolve => { finish = resolve; }) });
                await tick();
                assert.equal(typeof finish, 'function');
            }
            if (lifecycle === 'project') evaluate(m.ctx, 'canvas.project_id="another-project";');
            if (lifecycle === 'canvas') evaluate(m.ctx, 'canvas.id="another-canvas";');
            if (lifecycle === 'deleted') m.ctx.canvasState.nodes = m.ctx.canvasState.nodes.filter(node => node.id !== 'agent');
            if (lifecycle === 'recreated') {
                m.ctx.cleanupAgentTuiState('agent');
                const fresh = m.ctx.getAgentTuiState('agent');
                fresh.historyLoaded = true;
                fresh.socket = { readyState: 1, close() {} };
            }
            const before = m.stored(), html = m.body.innerHTML;
            if (phase === 'json') finish(data);
            else m.calls[0].resolve(response(data));
            await tick();
            assert.deepEqual(m.stored(), before);
            assert.equal(m.body.innerHTML, html, 'obsolete receipt cannot rebuild the current view');
            assert.equal(evaluate(m.ctx, 'undoStack.length'), 0);
            assert.equal(m.harness.toasts.length, 0);
        });
    }
}

const locks = {
    busy: s => { s.status = 'busy'; }, starting: s => { s.status = 'starting'; },
    confirmation: s => { s.confirm = { confirm_id: 'pending', tool: 'generate.trigger' }; },
    stopping: s => { s.stopPending = true; }, settings: s => { s.settingsPending = true; },
    'host-confirmation': s => { s.hostConfirmPending = true; }, ime: s => { s.composing = true; },
    attachments: s => { s.attachWorking = 'attachment.xlsx'; }, replay: s => { s._replayInFlight = true; },
    queue: s => { s.queuedInputs.add('queued-input'); },
    'input-receipt': s => { s.nodeRefsDraft.pending.set('input', {}); },
    readonly: s => { s.viewHost = 'codex'; },
};
for (const [name, lock] of Object.entries(locks)) {
    test(`composer autonomy renderer and already-bound controls respect ${name} lock`, () => {
        const m = setup(), before = m.stored();
        lock(m.state);
        m.click('autonomous');
        assert.equal(m.calls.length, 0, 'a stale enabled DOM handle must recheck current state');
        const html = m.ctx.agentNodeBodyHtml(m.node('agent'));
        const choices = [...html.matchAll(/<button\b[^>]*data-agent-autonomy-choice="([^"]+)"[^>]*>/g)];
        assert.equal(choices.length, 3);
        for (const [tag] of choices) assert.match(tag, /\sdisabled(?:\s|>)/);
        assert.deepEqual(m.stored(), before);
        assert.deepEqual(m.selected(), ['standard']);
    });
}
