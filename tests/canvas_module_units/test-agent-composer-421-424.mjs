// #421/#424: real TUI/settings/picker behavior; only DOM, transport and dialogs are boundaries.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

const fn = (ctx, name) => globalFunction(ctx, name);
const flush = () => new Promise(resolve => setImmediate(resolve));
const reply = data => ({ ok: true, status: 200, json: async () => data });
const native = (host = 'pi', model = 'native/a', efforts = ['low', 'high']) => ({
    host, status: 'available', models: [{ id: 'native/a', name: 'Native A' }, { id: 'native/b', name: 'Native B' }],
    defaults: { model: '', effort: null }, selection: { model, efforts, source: { models: 'native:model/list', efforts: 'native:levels' } }, error: null,
});
function setup() {
    const { ctx, harness } = createCanvasContext();
    // Explicit real navigation dependency, not a default/fallback supplied by the common harness.
    evaluate(ctx, readFileSync(new URL('../../static/js/canvas-agent-navigation.js', import.meta.url), 'utf8'));
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-agent-tui.js']);
    const node = { id: 'a', type: 'smart-agent', title: 'Work', agentCli: 'pi', model: 'native/a', autonomy: 'standard',
        agentHostSettings: { pi: { model: 'native/a', effort: 'high' }, codex: { model: 'native/b', effort: 'low' } },
        agentHostSessions: { pi: { extSessionId: 'pi-original', branches: [], current_branch: '' } } };
    ctx.canvasState.nodes = [node]; ctx.canvasId = 'project';
    const calls = [], received = [], renders = [], asks = [], sockets = [];
    ctx.console = { error() {}, warn() {} };
    ctx.renderAgentNode = id => renders.push(id);
    ctx.receiveAgentSettingsCanvas = (canvas, options) => {
        received.push({ canvas, options }); ctx.canvasState.nodes = canvas.nodes; return true;
    };
    ctx.ffDialog = { ask: options => new Promise(resolve => asks.push({ options, resolve })) };
    ctx.fetch = (url, opts = {}) => new Promise((resolve, reject) => calls.push({ url, opts, resolve, reject }));
    ctx.WebSocket = class {
        static OPEN = 1;
        constructor(url) { this.url = url; this.readyState = 1; this.sent = []; sockets.push(this); }
        send(raw) { this.sent.push(JSON.parse(raw)); }
        close() { this.readyState = 3; this.onclose?.(); }
    };
    const create = ctx.document.createElement;
    ctx.document.createElement = tag => {
        const element = create(tag);
        if (tag === 'dialog') {
            element.controls = new Map();
            let markup = '';
            Object.defineProperty(element, 'innerHTML', { get: () => markup, set: value => { markup = value; element.controls.clear(); } });
            element.querySelector = selector => {
                if (!element.controls.has(selector)) element.controls.set(selector, harness.makeElement(selector.includes('model]') || selector.includes('effort]') ? 'select' : 'button'));
                return element.controls.get(selector);
            };
        }
        return element;
    };
    const state = fn(ctx, 'getAgentTuiState')('a');
    return { ctx, harness, node, state, calls, received, renders, asks, sockets };
}
const event = (type, extra = {}) => ({ type, stopPropagation() {}, preventDefault() {}, ...extra });
const changedCanvas = (m, host, model, effort) => ({ nodes: [{ ...m.ctx.canvasState.nodes[0], agentCli: host, model,
    agentHostSettings: { ...m.ctx.canvasState.nodes[0].agentHostSettings, [host]: { model, effort } } }] });

test('r3 composer has no duplicate internal header; real parameters and more menu retain D1/ref/frame actions', () => {
    const m = setup(), html = fn(m.ctx, 'agentNodeBodyHtml')(m.node);
    assert.doesNotMatch(html, /agent-tui-header|agent-tui-title|设计预览/);
    assert.match(html, /agent-tui-composer/); assert.match(html, /min-height:96px/);
    for (const attr of ['host-picker', 'host-choice', 'model-picker', 'effort-picker', 'autonomy-choice']) assert.match(html, new RegExp(`data-agent-${attr}(?:[= ])`));
    assert.doesNotMatch(html, /<select\b/);
    assert.equal((html.match(/class="agent-tui-setting-dot"/g) || []).length, 2);
    assert.match(html, /data-agent-autonomy-choice="standard" aria-pressed="true">每批/);
    for (const text of ['每张', '每批', '无需', '生效未确认']) assert.ok(html.includes(text));
    const head = fn(m.ctx, 'agentHeadActionButtonsHtml')(m.node);
    assert.match(head, /^<details class="agent-tui-more"><summary aria-label="更多会话操作">.*<\/summary><div data-agent-more-content>/);
    assert.equal((head.match(/<summary /g) || []).length, 1);
    const menu = head.slice(head.indexOf('<div data-agent-more-content>'));
    for (const attr of ['chat-frame', 'ref-frame', 'dock-set', 'more-fork', 'more-cli', 'more-delete']) assert.ok(menu.includes(`data-agent-${attr}`));
    assert.equal(fn(m.ctx, 'agentDockNodeWidth')({}), 480);
    assert.equal(fn(m.ctx, 'agentDockNodeWidth')({ w: 382 }), 382);
    assert.equal(m.calls.length, 1, 'render may only prefetch the initial history page, not metadata');
    assert.equal(m.calls[0].url, '/api/projects/project/agent-nodes/a/events/page?host=pi&turns=5');
    assert.equal(m.calls[0].opts.method, undefined, 'history prefetch is a GET');
});

test('capabilities are fetched only during the shared model+effort picker lifecycle, with native IDs/source', async () => {
    const m = setup(), el = m.harness.makeElement('div'), effort = m.harness.makeElement('button');
    effort.setAttribute('data-agent-effort-picker', 'a');
    el.querySelectorAll = selector => selector === '[data-agent-model-picker],[data-agent-effort-picker]' ? [effort] : [];
    m.state.socket = { readyState: 1 };
    fn(m.ctx, 'bindAgentNodeControls')(el, m.node);
    assert.equal(m.calls.length, 1, 'bind may request only history, not metadata');
    assert.equal(m.calls[0].url, '/api/projects/project/agent-nodes/a/events/page?host=pi&turns=5');
    assert.equal(m.calls[0].opts.method, undefined, 'history replay is a GET');
    m.calls[0].resolve(reply({ events: [], has_more: false, cursor: null })); await flush();
    assert.equal(m.state.historyLoaded, true);
    effort.dispatchEvent(event('click'));
    assert.equal(m.calls.length, 2); assert.match(m.calls[1].url, /capabilities\?host=pi&model=native%2Fa$/);
    m.calls[1].resolve(reply(native('pi', 'native/a', ['low', 'high', 'ultra']))); await flush();
    const picker = m.state.modelPicker;
    assert.match(picker.dialog.innerHTML, /Native A/); assert.match(picker.dialog.innerHTML, /native:levels/);
    assert.doesNotMatch(picker.dialog.innerHTML, /value="ultra"/);
    picker.dialog.querySelector('[data-agent-picker-model]').dispatchEvent(event('change', { target: { value: 'native/b' } }));
    assert.match(m.calls[2].url, /model=native%2Fb$/);
    fn(m.ctx, 'closeAgentModelPicker')(m.state);
    m.calls[2].resolve(reply(native('pi', 'native/b', ['low']))); await flush();
    assert.equal(m.state.modelPicker, null, 'closed picker cannot be reopened by late capability response');
    assert.equal(m.calls.length, 3);
});

test('unsupported previous effort requires explicit confirmation and submits model+null effort atomically; 409 is visible', async () => {
    const m = setup();
    const opening = fn(m.ctx, 'openAgentModelPicker')('a', 'project');
    m.calls[0].resolve(reply(native())); await opening;
    const picker = m.state.modelPicker;
    picker.model = 'native/b'; picker.capabilities = native('pi', 'native/b', ['low']);
    await fn(m.ctx, 'applyAgentModelPicker')('a', 'project', picker);
    assert.match(picker.dialog.innerHTML, /新模型不支持当前思考档位/);
    assert.equal(m.asks.length, 0, 'a backdrop outside the native picker would be inert');
    assert.equal(m.calls.length, 1, 'the confirmation view does not write settings');
    picker.dialog.querySelector('[data-agent-picker-mismatch-cancel]').dispatchEvent(event('click'));
    assert.match(picker.dialog.innerHTML, /节点模型与思考/);
    assert.equal(m.calls.length, 1); assert.equal(m.state.modelPicker, picker);
    await fn(m.ctx, 'applyAgentModelPicker')('a', 'project', picker);
    const applying = fn(m.ctx, 'applyAgentModelPicker')('a', 'project', picker, true);
    await flush();
    assert.deepEqual(JSON.parse(m.calls[1].opts.body), { host: 'pi', model: 'native/b', effort: null, user_edit: true });
    m.calls[1].resolve({ ok: false, status: 409, json: async () => ({ detail: { message: 'Production default unverified' } }) });
    assert.equal(await applying, false);
    assert.equal(m.node.model, 'native/a'); assert.equal(m.node.agentHostSettings.pi.effort, 'high');
    assert.match(fn(m.ctx, 'agentNodeBodyHtml')(m.node), /Production default unverified/);
    assert.equal(m.received.length, 0);
});

test('first host switch can cancel, then saves the backend-restored per-host choice only through the canvas receiver', async () => {
    const m = setup(), before = JSON.stringify(m.node);
    const cancel = fn(m.ctx, 'agentSelectHost')('a', 'project', 'codex');
    assert.match(m.asks[0].options.title, /新开独立会话/); m.asks[0].resolve(false); await cancel;
    assert.equal(m.calls.length, 0); assert.equal(JSON.stringify(m.node), before);
    const switching = fn(m.ctx, 'agentSelectHost')('a', 'project', 'codex');
    m.asks[1].resolve(true); await flush();
    assert.deepEqual(JSON.parse(m.calls[0].opts.body), { host: 'codex', model: 'native/b', effort: 'low', user_edit: true });
    assert.equal(JSON.stringify(m.node), before, 'pending request cannot leak into autosave');
    const canvas = changedCanvas(m, 'codex', 'native/b', 'low');
    m.calls[0].resolve(reply({ canvas, effort_state: { requested: 'low', submitted: null, effective: null, source: 'codex:configured-only' } }));
    assert.equal(await switching, true);
    assert.deepEqual(fromContext(m.received[0].options.fields), ['agentCli', 'agentHostSettings', 'model']);
    assert.equal(m.state.activeHost, 'codex');
    const html = fn(m.ctx, 'agentNodeBodyHtml')(m.ctx.canvasState.nodes[0]);
    assert.match(html, /已设置/); assert.match(html, /生效未确认/); assert.doesNotMatch(html, /思考已生效/);
    assert.equal(m.node.agentHostSessions.pi.extSessionId, 'pi-original', 'TUI never mutates bindings');
});

test('returning host uses its saved native binding and parameters; stale settings cannot adopt a canvas', async () => {
    const m = setup();
    m.ctx.canvasState.nodes[0] = { ...m.node, agentCli: 'codex', model: 'native/b' };
    fn(m.ctx, 'agentSyncHostView')(m.ctx.canvasState.nodes[0], m.state);
    const returning = fn(m.ctx, 'agentSelectHost')('a', 'project', 'pi');
    assert.match(m.asks[0].options.title, /切回/); m.asks[0].resolve(true); await flush();
    assert.deepEqual(JSON.parse(m.calls[0].opts.body), { host: 'pi', model: 'native/a', effort: 'high', user_edit: true });
    fn(m.ctx, 'cleanupAgentTuiState')('a');
    m.calls[0].resolve(reply({ canvas: changedCanvas(m, 'pi', 'native/a', 'high') }));
    assert.equal(await returning, false); assert.equal(m.received.length, 0);
    assert.equal(evaluate(m.ctx, 'agentTuiState.has("a")'), false);
});

test('per-host transient views preserve messages, draft/focus/selection/reading position, attachments and references', () => {
    const m = setup();
    m.state.messages.push({ role: 'user', text: 'Pi only' }); m.state.draft = 'Pi draft';
    m.state.inputFocus = { focused: true, start: 1, end: 3, direction: 'backward', scrollTop: 4 };
    m.state.scrollState = { scrollTop: 127, atBottom: false, anchor: { key: 'entry:1', offset: -12 } };
    m.state.pendingImages.push({ name: 'pi.png', dataUrl: 'data:image/png;base64,AA' });
    m.state.nodeRefsDraft.manual.push('ref-pi');
    const piRefs = m.state.nodeRefsDraft;
    fn(m.ctx, 'agentChangeHostView')('a', m.state, 'codex');
    assert.equal(m.state.messages.length, 0); assert.equal(m.state.draft, ''); assert.equal(m.state.pendingImages.length, 0);
    m.state.messages.push({ role: 'user', text: 'Codex only' }); m.state.draft = 'Codex draft'; m.state.nodeRefsDraft.manual.push('ref-codex');
    fn(m.ctx, 'agentChangeHostView')('a', m.state, 'pi');
    assert.equal(m.state.messages[0].text, 'Pi only'); assert.equal(m.state.draft, 'Pi draft');
    assert.equal(m.state.nodeRefsDraft, piRefs); assert.equal(m.state.scrollState.scrollTop, 127);
    assert.deepEqual(fromContext(m.state.inputFocus), { focused: true, start: 1, end: 3, direction: 'backward', scrollTop: 4 });
    assert.equal(m.state.pendingImages[0].name, 'pi.png');
    fn(m.ctx, 'agentChangeHostView')('a', m.state, 'codex');
    assert.equal(m.state.draft, 'Codex draft'); assert.equal(m.state.nodeRefsDraft.manual[0], 'ref-codex');
    assert.ok(!/draft|inputFocus|scrollState|pendingImages|nodeRefsDraft/.test(JSON.stringify(m.node)));
});

test('readonly history uses only host-filtered GET events and rejects late frames/replays and all send routes', async () => {
    const m = setup(), socket = fn(m.ctx, 'openAgentWebSocket')('a', 'project');
    m.state.status = 'idle';
    await fn(m.ctx, 'agentViewHost')('a', 'project', 'codex');
    assert.equal(m.sockets.length, 1); assert.equal(m.calls[0].url, '/api/projects/project/agent-nodes/a/events/page?host=codex&turns=5'); assert.equal(m.calls[0].opts.method, undefined);
    socket.onmessage({ data: JSON.stringify({ host: 'pi', type: 'user_prompt', text: 'late old frame', seq: 7 }) });
    for (const sender of ['sendAgentPrompt', 'sendAgentSteer', 'sendAgentFollowUp']) fn(m.ctx, sender)('a', 'project', 'must not send');
    fn(m.ctx, 'sendAgentAbort')('a', 'project');
    assert.equal(m.calls.length, 1); assert.equal(m.state.messages.length, 0);
    m.calls[0].resolve(reply({ events: [ { seq: 1, host: 'pi', event: { type: 'user_prompt', text: 'wrong host' } },
        { seq: 2, host: 'codex', event: { host: 'codex', type: 'user_prompt', text: 'saved Codex' } } ], has_more: false, cursor: null }));
    await flush(); assert.equal(m.state.messages.length, 1); assert.equal(m.state.messages[0].text, 'saved Codex');
    assert.match(fn(m.ctx, 'agentNodeBodyHtml')(m.node), /agent-tui-readonly-banner/);
    assert.equal(fn(m.ctx, 'connectAgentWebSocket')('a', 'project'), null);
    await fn(m.ctx, 'agentViewHost')('a', 'project', 'pi');
    const piReplay = m.calls[1];
    assert.equal(piReplay.url, '/api/projects/project/agent-nodes/a/events/page?host=pi&turns=5');
    assert.equal(piReplay.opts.method, undefined);
    await fn(m.ctx, 'agentViewHost')('a', 'project', 'codex');
    assert.equal(m.calls[2].url, '/api/projects/project/agent-nodes/a/events?since=2&host=codex');
    assert.equal(m.calls[2].opts.method, undefined);
    piReplay.resolve(reply({ events: [{ seq: 10, host: 'pi', event: { type: 'user_prompt', text: 'late Pi replay' } }], has_more: false, cursor: null }));
    await flush(); assert.equal(m.state.messages[0].text, 'saved Codex');
    m.calls[2].resolve(reply({ events: [] })); await flush();
});

test('busy/starting/retrying/confirmation/queue and uncompleted stop lock every parameter path, but steer still queues', async () => {
    for (const status of ['busy', 'starting', 'retrying', 'waiting_confirm', 'stopping']) {
        const m = setup(); m.state.status = status;
        assert.equal(await fn(m.ctx, 'setAgentHostSettings')('a', 'project', { host: 'codex', model: '', effort: null }), false);
        await fn(m.ctx, 'agentSelectHost')('a', 'project', 'codex');
        fn(m.ctx, 'openAgentModelPicker')('a', 'project');
        assert.equal(m.calls.length, 0); assert.equal(m.asks.length, 0);
    }
    const m = setup(), socket = fn(m.ctx, 'openAgentWebSocket')('a', 'project'); m.state.status = 'busy';
    fn(m.ctx, 'sendAgentSteer')('a', 'project', 'steer running');
    assert.equal(socket.sent[0].type, 'steer'); assert.equal(m.state.queuedInputs.size, 1);
    fn(m.ctx, 'sendAgentAbort')('a', 'project');
    assert.equal(m.state.status, 'stopping'); assert.equal(fn(m.ctx, 'agentParametersLocked')(m.state), true);
    m.calls[0].resolve(reply({ host: 'pi', status: 'busy' })); await flush();
    fn(m.ctx, 'handleAgentEvent')('a', { type: 'turn_end' });
    assert.equal(m.state.stopPending, true); assert.equal(fn(m.ctx, 'agentParametersLocked')(m.state), true);
    fn(m.ctx, 'handleAgentEvent')('a', { type: 'agent_end' });
    assert.equal(m.state.stopPending, false); assert.equal(m.state.queuedInputs.size, 0);
    const pending = socket.sent[0];
    fn(m.ctx, 'handleAgentEvent')('a', { type: 'input_accepted', input_type: 'steer', request_id: pending.request_id, node_refs: [] });
    assert.equal(fn(m.ctx, 'agentParametersLocked')(m.state), false);
});

test('input receipts received during readonly browsing settle the originating host only after return', async () => {
    const m = setup();
    const sending = fn(m.ctx, 'sendAgentPrompt')('a', 'project', 'Pi pending');
    const submission = JSON.parse(m.calls[0].opts.body);
    fn(m.ctx, 'agentChangeHostView')('a', m.state, 'codex');
    m.state.draft = 'Codex untouched';
    m.calls[0].resolve(reply({ request_id: submission.request_id, input_type: 'prompt', node_refs: [] }));
    await sending;
    assert.equal(m.state.messages.length, 0); assert.equal(m.state.draft, 'Codex untouched');
    fn(m.ctx, 'agentChangeHostView')('a', m.state, 'pi'); await flush();
    assert.equal(m.state.messages[0].delivery, 'accepted'); assert.equal(m.state.nodeRefsDraft.pending.size, 0);
});

test('new-host defaults are submitted as empty model/null effort, never directory-first or invented levels', async () => {
    const m = setup(); delete m.node.agentHostSettings.codex;
    const switching = fn(m.ctx, 'agentSelectHost')('a', 'project', 'codex');
    m.asks[0].resolve(true); await flush();
    assert.deepEqual(JSON.parse(m.calls[0].opts.body), { host: 'codex', model: '', effort: null, user_edit: true });
    m.calls[0].resolve(reply({ canvas: changedCanvas(m, 'codex', '', null), effort_state: { requested: null, submitted: null, effective: null, source: 'codex:unconfirmed' } }));
    assert.equal(await switching, true);
    const body = fn(m.ctx, 'agentNodeBodyHtml')(m.ctx.canvasState.nodes[0]);
    assert.match(body, /data-agent-model-picker="a"[^>]*aria-label="模型 跟随宿主"/);
});

test('host confirmation cannot overwrite an externally switched host; capability requests cannot supersede a newer picker model', async () => {
    const m = setup();
    const switching = fn(m.ctx, 'agentSelectHost')('a', 'project', 'codex');
    m.ctx.canvasState.nodes[0] = { ...m.node, agentCli: 'codex' };
    m.asks[0].resolve(true); await switching;
    assert.equal(m.calls.length, 0);
    const p = setup(), first = fn(p.ctx, 'openAgentModelPicker')('a', 'project'), picker = p.state.modelPicker;
    picker.model = 'native/b';
    const second = fn(p.ctx, 'loadAgentPickerCapabilities')('a', 'project', picker);
    p.calls[1].resolve(reply(native('pi', 'native/b', ['low']))); await second;
    p.calls[0].resolve(reply(native('pi', 'native/a', ['high']))); await first;
    assert.equal(picker.capabilities.selection.model, 'native/b');
    assert.deepEqual(Array.from(picker.capabilities.selection.efforts), ['low']);
});

test('a network or invalid JSON settings failure remains visible and never reaches the authority receiver', async () => {
    for (const nonJson of [false, true]) {
        const m = setup(), saving = fn(m.ctx, 'setAgentHostSettings')('a', 'project', { host: 'pi', model: 'native/b', effort: 'low' });
        if (nonJson) m.calls[0].resolve({ ok: false, status: 502, json: async () => { throw new Error('Invalid JSON receipt'); } });
        else m.calls[0].reject(new Error('Network offline'));
        assert.equal(await saving, false); assert.equal(m.state.settingsPending, false); assert.equal(m.received.length, 0);
        assert.match(m.state.settingsError, nonJson ? /Invalid JSON/ : /Network offline/);
        assert.equal(m.node.model, 'native/a');
    }
});

test('native running means idle process, but turn boundaries and willRetry never unlock an unfinished agent', async () => {
    const m = setup();
    fn(m.ctx, 'handleAgentEvent')('a', { type: 'session', host: 'pi', status: 'running' });
    assert.equal(m.state.status, 'idle');
    fn(m.ctx, 'handleAgentEvent')('a', { type: 'agent_start' });
    fn(m.ctx, 'handleAgentEvent')('a', { type: 'turn_end' });
    assert.equal(m.state.status, 'busy'); assert.equal(fn(m.ctx, 'agentParametersLocked')(m.state), true);
    fn(m.ctx, 'handleAgentEvent')('a', { type: 'agent_end', willRetry: true });
    assert.equal(m.state.status, 'retrying'); assert.equal(fn(m.ctx, 'agentParametersLocked')(m.state), true);
    fn(m.ctx, 'handleAgentEvent')('a', { type: 'agent_end' });
    assert.equal(fn(m.ctx, 'agentParametersLocked')(m.state), false);
    m.state.status = 'stopping'; m.state.stopPending = true;
    const poll = fn(m.ctx, 'pollAgentStopStatus')('a', 'project', m.state, m.state.viewEpoch);
    m.calls[0].resolve(reply({ host: 'pi', status: 'running' })); await poll;
    assert.equal(m.state.stopPending, false); assert.equal(m.state.status, 'idle');
});

test('malformed/unavailable capability responses surface errors rather than inventing effort choices', async () => {
    for (const data of [{ ...native(), selection: null }, { ...native(), status: 'unavailable', error: { message: 'CLI offline' } }]) {
        const m = setup(), opening = fn(m.ctx, 'openAgentModelPicker')('a', 'project');
        m.calls[0].resolve(reply(data)); await opening;
        assert.match(m.state.modelPicker.dialog.innerHTML, /role="alert"/);
        assert.match(m.state.modelPicker.dialog.innerHTML, /data-agent-picker-apply disabled/);
        assert.doesNotMatch(m.state.modelPicker.dialog.innerHTML, /value="(?:medium|ultra)"/);
    }
});

test('navigation adapter receives only real user DOM entries with stable timeline keys; scroll never rebuilds timeline', () => {
    const m = setup(), state = m.state;
    fn(m.ctx, 'agentAppendMessage')(state, { role: 'user', text: 'first user' });
    fn(m.ctx, 'agentAppendMessage')(state, { role: 'assistant', text: 'assistant is not a tick' });
    fn(m.ctx, 'agentAppendMessage')(state, { role: 'user', text: 'second user' });
    const timeline = fn(m.ctx, 'agentTimelineSync')(state), root = m.harness.makeElement('div'), body = m.harness.makeElement('div');
    body.clientHeight = 100; body.scrollHeight = 500; body.scrollTop = 0;
    body.getBoundingClientRect = () => ({ top: 0, bottom: 100, height: 100 });
    const rows = timeline.map((entry, index) => {
        const row = m.harness.makeElement('article'); row.setAttribute('data-agent-entry-key', entry.key);
        row.getBoundingClientRect = () => ({ top: index * 150 - body.scrollTop, bottom: index * 150 + 80 - body.scrollTop }); return row;
    });
    body.querySelectorAll = () => rows;
    const mount = m.harness.makeElement('nav');
    root.querySelector = selector => ({ '[data-agent-tui-body]': body, '.agent-message-navigation': mount }[selector] || null);
    const records = []; let updates = 0, destroyed = 0;
    m.ctx.createAgentMessageNavigation = options => { records.push(options); return { refresh() {}, update() { updates++; }, destroy() { destroyed++; } }; };
    fn(m.ctx, 'bindAgentScroll')(root, state);
    assert.equal(records[0].body, body); assert.equal(records[0].mount, mount);
    assert.deepEqual(Array.from(records[0].entries, e => e.id), [timeline[0].key, timeline[2].key]);
    assert.equal(records[0].entries[1].element, rows[2]);
    const oldTimeline = state.timeline;
    body.dispatchEvent(event('scroll')); body.dispatchEvent(event('scroll'));
    assert.equal(records.length, 1); assert.equal(state.timeline, oldTimeline); assert.ok(updates > 0);
    records[0].onJump(records[0].entries[0]); assert.equal(body.scrollTop, 0); assert.equal(state.draft, '');
    fn(m.ctx, 'disposeAgentScroll')(state); assert.equal(destroyed, 1);
});
