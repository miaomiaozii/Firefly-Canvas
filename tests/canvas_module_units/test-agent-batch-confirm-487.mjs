import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createCanvasContext, loadModules, evaluate } from './harness.mjs';

function setup() {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['i18n-core.js', 'canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-agent-navigation.js', 'canvas-agent-tui.js']);
    const node = { id: 'actor/487', type: 'smart-agent', agentCli: 'pi' };
    ctx.canvasState.nodes.push(node);
    evaluate(ctx, "canvasId='project/487'; canvas={project_id:'project/487'}");
    const state = ctx.getAgentTuiState(node.id);
    state.historyLoaded = true;
    state.reconnectStopped = true;
    state.projectId = 'project/487';
    const calls = [];
    ctx.fetch = (url, options) => new Promise((resolve, reject) => calls.push({ url, options, resolve, reject }));
    const event = value => ctx.handleAgentEvent(node.id, { host: 'pi', ...value });
    return { ctx, state, node, calls, event, html: () => ctx.agentNodeBodyHtml(node) };
}
function gate(id, batch = id) {
    return { type: 'confirm', confirm_id: `batch-confirm-${id}`, tool: 'generate.request_batch',
        args: { batch_id: batch }, message: `Approval ${id}` };
}

test('resolving one independent batch leaves the other pending gate visible and actionable', async () => {
    const h = setup();
    h.event(gate('A'));
    h.event(gate('B'));
    assert.match(h.html(), /Approval B/);
    h.event({ type: 'confirm_resolved', confirm_id: 'batch-confirm-B', approved: false });
    assert.match(h.html(), /Approval A/);
    assert.match(h.html(), /data-agent-confirm="actor\/487"/);
    const decision = h.ctx.sendAgentConfirm(h.node.id, 'project/487', false);
    assert.equal(h.calls.length, 1);
    assert.deepEqual(JSON.parse(h.calls[0].options.body), { confirm_id: 'batch-confirm-A', approved: false });
    assert.equal(h.calls[0].options.headers['X-Agent-Project'], 'project/487');
    h.calls[0].resolve({ ok: true, status: 200, json: async () => ({ ok: true, confirm_id: 'batch-confirm-A', approved: false }) });
    await decision;
    assert.doesNotMatch(h.html(), /data-agent-confirm="/);
});

test('an accepted late HTTP decision is not restored after another batch resolves', async () => {
    const h = setup();
    h.event(gate('A'));
    const decision = h.ctx.sendAgentConfirm(h.node.id, 'project/487', true);
    h.event(gate('B'));
    h.calls[0].resolve({ ok: true, status: 200, json: async () => ({ ok: true, confirm_id: 'batch-confirm-A', approved: true }) });
    await decision;
    assert.match(h.html(), /Approval B/);
    h.event({ type: 'confirm_resolved', confirm_id: 'batch-confirm-B', approved: false });
    assert.doesNotMatch(h.html(), /Approval A|data-agent-confirm="/);
});

test('preempted real-dialog callbacks cannot reject a gate restored within the same drain', async () => {
    const h = setup();
    evaluate(h.ctx, readFileSync(new URL('../../static/js/ui-dialog.js', import.meta.url), 'utf8'));
    h.event(gate('A'));
    h.event(gate('B'));
    h.event({ type: 'confirm_resolved', confirm_id: 'batch-confirm-B', approved: false });
    await Promise.resolve();
    assert.equal(h.calls.length, 0, 'Dialog preemption is not a human rejection of the restored gate');
    assert.match(h.html(), /Approval A/);
    const decision = h.ctx.sendAgentConfirm(h.node.id, 'project/487', false);
    assert.equal(h.calls.length, 1);
    assert.equal(JSON.parse(h.calls[0].options.body).confirm_id, 'batch-confirm-A');
    h.calls[0].resolve({ ok: true, status: 200, json: async () => ({ ok: true, confirm_id: 'batch-confirm-A', approved: false }) });
    await decision;
    assert.doesNotMatch(h.html(), /data-agent-confirm="/);
});

test('server resolution retires the last confirmation without sending a decision', async () => {
    const h = setup();
    evaluate(h.ctx, readFileSync(new URL('../../static/js/ui-dialog.js', import.meta.url), 'utf8'));
    h.event(gate('last'));
    assert.match(h.html(), /Approval last/);
    assert.match(h.html(), /data-agent-confirm="/);
    const backdrop = h.ctx.document.body.children.at(-1);
    assert.equal(backdrop.className, 'ff-dialog-backdrop');
    // The VM has no DOM removal; observe that IO boundary, not a confirmation helper.
    let removed = 0;
    backdrop.remove = () => { removed++; };
    h.event({ type: 'confirm_resolved', confirm_id: 'batch-confirm-last', approved: false });
    await Promise.resolve();
    assert.doesNotMatch(h.html(), /data-agent-confirm="/);
    assert.equal(removed, 1, 'The real dialog must close silently when its gate is resolved');
    assert.equal(h.calls.length, 0, 'A server resolution is not a human rejection');
});

test('replacement resolution retires only the old gate and never resurrects it', () => {
    const h = setup();
    h.event(gate('old', 'same-batch'));
    h.event({ type: 'confirm_resolved', confirm_id: 'batch-confirm-old', approved: false, reason: 'replaced' });
    h.event(gate('new', 'same-batch'));
    h.event(gate('other'));
    h.event({ type: 'confirm_resolved', confirm_id: 'batch-confirm-other', approved: false });
    assert.match(h.html(), /Approval new/);
    assert.doesNotMatch(h.html(), /Approval old/);
    h.event({ type: 'confirm_resolved', confirm_id: 'batch-confirm-new', approved: false });
    assert.doesNotMatch(h.html(), /data-agent-confirm="/);
});
