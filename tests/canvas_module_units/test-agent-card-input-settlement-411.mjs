import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { createCanvasContext, loadModules, evaluate } from './harness.mjs';

const project = 'e2e-tabbit-411-pending-fixture';
const presentation = { schema: 1, id: 'card/pending', kind: 'plan', title: '方案', state: 'presented', data: { source: 'plan_preview', plan_id: 'plan/pending', generation: 1 } };
const preview = { plan_id: 'plan/pending', generation: 1, preview: { tasks: [{ task_id: 'task', name: '主图', section: '主图', selected: true, source_row: 3, source_rows: [3] }], warnings: [], excluded: [] } };
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup(host = 'pi') {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, ['i18n-core.js', 'canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-agent-navigation.js', 'canvas-agent-tui.js']);
    vm.runInContext(readFileSync(new URL('../../static/js/i18n/smart-canvas.js', import.meta.url), 'utf8'), ctx);
    const node = { id: 'agent/pending', type: 'smart-agent', agentCli: host, extSessionId: 'original-thread' };
    ctx.canvasState.nodes.push(node);
    evaluate(ctx, `canvasId=${JSON.stringify(project)}; canvas={project_id:${JSON.stringify(project)}}`);
    const state = ctx.getAgentTuiState(node.id);
    state.historyLoaded = true; state.reconnectStopped = true;
    ctx.handleAgentEvent(node.id, { host, type: 'plan', seq: 1, presentation });
    const calls = [];
    ctx.fetch = (url, options) => new Promise(resolve => calls.push({ url, options, resolve }));
    const message = state.messages[0], view = ctx.agentPlanView(state, message);
    view.receipt = structuredClone(preview);
    return { ctx, harness, node, state, calls, message, view, html: () => ctx.agentNodeBodyHtml(node) };
}
function submitAdmittedCard(h, kind) {
    const current = h.ctx.agentPresentationOwner(h.node.id, project, h.state, h.state.viewEpoch, h.message);
    h.state.draft = '原输入框未发送草稿'; h.state.pendingImages = [{ name: '原附件' }];
    h.state.nodeRefsDraft.manual = ['原引用'];
    if (kind === 'follow_up') h.state.status = 'busy';
    const options = { preserveComposer: true, current, onPrepared: snapshot => { h.view.applyRow = snapshot.row; h.view.applyGeneration = 1; } };
    const send = kind === 'prompt' ? h.ctx.sendAgentPrompt : h.ctx.sendAgentFollowUp;
    send(h.node.id, project, '已发出的卡片输入', [], options);
    assert.equal(h.calls.length, 1);
    const sent = JSON.parse(h.calls[0].options.body);
    const refs = h.state.nodeRefsDraft, snapshot = refs.pending.get(sent.request_id);
    assert.ok(snapshot); assert.equal(snapshot.row.delivery, 'pending');
    return { sent, refs, snapshot };
}
function receipt(h, sent, accepted) {
    h.calls[0].resolve({ ok: accepted, status: accepted ? 200 : 409, json: async () => accepted
        ? { request_id: sent.request_id, input_type: h.calls[0].url.split('/').at(-1), node_refs: [] }
        : { detail: { code: 'INPUT_CONFLICT', message: '原宿主拒绝 <409>' } } });
}

for (const kind of ['prompt', 'follow_up']) for (const accepted of [false, true]) for (const visits of [1, 2]) {
    test(`admitted ${kind} ${accepted ? 'acceptance' : '409 rejection'} settles only in its original view after ${visits} read-only visits`, async () => {
        const h = setup(), original = submitAdmittedCard(h, kind);
        for (let visit = 1; visit < visits; visit++) {
            h.ctx.agentChangeHostView(h.node.id, h.state, 'codex');
            h.ctx.agentChangeHostView(h.node.id, h.state, 'pi');
            await flush();
        }
        h.ctx.agentChangeHostView(h.node.id, h.state, 'codex');
        const otherRefs = h.state.nodeRefsDraft;
        h.state.draft = 'Codex 草稿'; otherRefs.manual = ['Codex 引用'];
        receipt(h, original.sent, accepted); await flush();
        assert.equal(original.refs.pending.size, 1, 'receipt correlation must survive the inactive host');
        assert.equal(original.snapshot.row.delivery, 'pending');
        assert.equal(otherRefs.pending.size, 0); assert.equal(otherRefs.error, '');
        assert.equal(h.state.draft, 'Codex 草稿'); assert.deepEqual(Array.from(otherRefs.manual), ['Codex 引用']);
        assert.ok(!h.state.messages.includes(original.snapshot.row));
        h.ctx.agentChangeHostView(h.node.id, h.state, 'pi'); await flush();
        assert.equal(original.refs.pending.size, 0);
        assert.equal(original.snapshot.row.delivery, accepted ? 'accepted' : 'unconfirmed');
        assert.equal(h.state.draft, '原输入框未发送草稿'); assert.equal(h.state.pendingImages[0].name, '原附件');
        assert.deepEqual(Array.from(h.state.nodeRefsDraft.manual), ['原引用']);
        assert.equal(h.state.queuedInputs.size, accepted && kind === 'follow_up' ? 1 : 0);
        if (accepted && kind === 'follow_up') {
            assert.ok(h.state.queuedInputs.has(original.sent.request_id), 'admission is not delivery');
            h.ctx.handleAgentEvent(h.node.id, { host: 'pi', seq: 2, type: 'user_prompt', request_id: original.sent.request_id, input_type: kind, message: '已发出的卡片输入', node_refs: [] });
            assert.equal(h.state.queuedInputs.size, 0, 'the actual delivery event releases the queue');
        }
        if (!accepted) {
            assert.equal(original.snapshot.row.delivery_error, '原宿主拒绝 <409>');
            assert.match(h.html(), /原宿主拒绝 &lt;409&gt;/);
            if (kind === 'prompt') assert.equal(h.state.status, 'idle');
        }
        assert.doesNotMatch(h.html(), /应用请求正在核验准入/);
        assert.equal(h.calls.length, 1, 'returning to a view never resends input');
    });
}

test('a timeout while viewing another host retains correlation for the later real rejection', async () => {
    const h = setup(), original = submitAdmittedCard(h, 'prompt');
    h.ctx.agentChangeHostView(h.node.id, h.state, 'codex');
    h.harness.advanceTimers();
    assert.equal(original.refs.pending.size, 1);
    h.ctx.agentChangeHostView(h.node.id, h.state, 'pi'); await flush();
    assert.equal(original.snapshot.row.delivery, 'unconfirmed');
    assert.equal(original.refs.pending.size, 1, 'an uncertain timeout is not a rejection');
    receipt(h, original.sent, false); await flush();
    assert.equal(original.refs.pending.size, 0);
    assert.equal(original.snapshot.row.delivery_error, '原宿主拒绝 <409>');
    assert.equal(h.state.status, 'idle'); assert.equal(h.calls.length, 1);
});

test('a host visit does not permit input receipts to cross project, thread, presentation or state ownership', async () => {
    for (const change of ['project', 'thread', 'presentation', 'state', 'node', 'closing']) {
        const h = setup(), original = submitAdmittedCard(h, 'prompt');
        h.ctx.agentChangeHostView(h.node.id, h.state, 'codex'); receipt(h, original.sent, true); await flush();
        if (change === 'project') evaluate(h.ctx, "canvasId='e2e-tabbit-other-project'");
        if (change === 'thread') h.node.extSessionId = 'replacement-thread';
        if (change === 'presentation') h.state.hostViews.get('pi').messages.splice(0, 1);
        if (change === 'state') { evaluate(h.ctx, `agentTuiState.delete(${JSON.stringify(h.node.id)})`); h.ctx.getAgentTuiState(h.node.id); }
        if (change === 'node') h.ctx.canvasState.nodes.length = 0;
        if (change === 'closing') h.state.closing = true;
        h.ctx.agentChangeHostView(h.node.id, h.state, 'pi'); await flush();
        assert.equal(original.snapshot.row.delivery, 'pending', change);
        assert.doesNotMatch(h.html(), /应用请求已准入/, change);
    }
    for (const previousVisit of [false, true]) {
        const h = setup();
        if (previousVisit) { h.ctx.agentChangeHostView(h.node.id, h.state, 'codex'); h.ctx.agentChangeHostView(h.node.id, h.state, 'pi'); await flush(); }
        const original = submitAdmittedCard(h, 'prompt');
        h.state.viewEpoch++;
        receipt(h, original.sent, true); await flush();
        assert.equal(original.snapshot.row.delivery, 'pending', 'an unrelated epoch invalidation is not a host visit');
    }
    const h = setup(), original = submitAdmittedCard(h, 'prompt');
    h.ctx.agentChangeHostView(h.node.id, h.state, 'codex'); receipt(h, original.sent, true); await flush();
    h.ctx.agentChangeHostView(h.node.id, h.state, 'pi'); h.state.viewEpoch++; await flush();
    assert.equal(original.snapshot.row.delivery, 'pending', 'a later unrelated epoch invalidation must not reuse an old visit');
});

test('before #484 no plan renders an apply action or admits input from a stale apply control', async () => {
    for (const host of ['pi', 'codex']) for (const mcpSession of [null, 'external-session']) {
        const h = setup(host); h.node.mcp_session = mcpSession;
        assert.doesNotMatch(h.html(), /data-agent-plan-apply=/);
        const button = h.harness.makeElement('button'); button.setAttribute('data-agent-plan-apply', presentation.id);
        const el = h.harness.makeElement('div'); el.querySelectorAll = selector => selector === '[data-agent-plan-apply]' ? [button] : [];
        h.ctx.bindAgentNodeControls(el, h.node);
        button.dispatchEvent({ type: 'click', stopPropagation() {}, preventDefault() {} }); await flush();
        assert.equal(h.calls.length, 0, 'an unclassified preview cannot select an input transport');
    }
});
