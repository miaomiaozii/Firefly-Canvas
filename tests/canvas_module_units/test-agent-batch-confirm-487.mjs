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
    ctx.AbortController = AbortController;
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

test('another Agent replacing the modal does not cancel or close an independent gate', async () => {
    const h = setup();
    evaluate(h.ctx, readFileSync(new URL('../../static/js/ui-dialog.js', import.meta.url), 'utf8'));
    h.event(gate('first'));
    const second = { ...h.node, id: 'other-agent' };
    h.ctx.canvasState.nodes.push(second);
    h.ctx.getAgentTuiState(second.id).projectId = 'project/487';
    h.ctx.handleAgentEvent(second.id, { ...gate('second'), host: 'pi' });
    await Promise.resolve();
    assert.equal(h.calls.length, 0, 'replacing the shared modal must not submit a decision');
    assert.match(h.html(), /Approval first/);
    let removed = 0;
    h.ctx.document.body.children.at(-1).remove = () => { removed++; };
    h.event({ type: 'confirm_resolved', confirm_id: 'batch-confirm-first', approved: false });
    assert.equal(removed, 0, 'the other Agent still owns the visible modal');
});

const HASH = 'a'.repeat(64);
const settle = () => new Promise(resolve => setImmediate(resolve));
const reply = (call, body, status = 200) => call.resolve({ ok: status < 400, status, json: async () => body });
const batchEvent = (batch, fields = {}) => ({ batch_id: batch, snapshot_sha256: HASH,
    project_id: 'project/487', agent_node_id: 'actor/487', ...fields });
const approvedBatch = (batch, source) => batchEvent(batch, { type: 'confirm_resolved', confirm_id: `batch-confirm-${batch}`,
    tool: 'generate.request_batch', approved: true, source });
const quota = { code: 'QUOTA_EXHAUSTED', message: 'Provider quota' };
const statusOf = (batch, status, items, fields = {}) => ({ batch_id: batch, snapshot_sha256: HASH, status, items, ...fields });
const actionFor = (h, track, action, item) => h.ctx.agentBatchRecoveryIO(h.node.id, h.state, track, action, item, track.status);

function checkScoped(call, batch, action, body) {
    assert.equal(call.url, `/api/projects/project%2F487/agent-nodes/actor%2F487/batches/${encodeURIComponent(batch)}/${action}`);
    assert.equal(call.options.method, body === undefined ? 'GET' : 'POST');
    assert.equal(call.options.credentials, 'same-origin');
    assert.equal(call.options.mode, 'same-origin');
    assert.deepEqual({ ...call.options.headers }, body === undefined
        ? { 'X-Agent-Project': 'project/487' } : { 'Content-Type': 'application/json', 'X-Agent-Project': 'project/487' });
    if (body === undefined) assert.equal('body' in call.options, false);
    else assert.deepEqual(JSON.parse(call.options.body), body);
}

test('authenticated replay watches approved REST and MCP batches; quota copy follows batch state', async () => {
    const h = setup();
    h.state.historyLoaded = false;
    const pending = { ...gate('unapproved'), params: { batch_id: 'unapproved', approval_stage: 'pay_batch',
        preflight: { snapshot_sha256: HASH, preview_revision: 1, items: [{ item_id: 'item-1', count: 1 }] } } };
    const replay = h.ctx.replayAgentEvents(h.node.id, h.state);
    assert.match(h.calls[0].url, /\/events\/page\?host=pi&turns=5$/);
    const events = [approvedBatch('rest/batch', 'rest'), approvedBatch('mcp-batch', 'mcp'), pending];
    reply(h.calls[0], { has_more: false, last_seq: 3, events: events.map((event, i) => ({ seq: i + 1, host: 'pi', event })) });
    await replay;
    assert.equal(h.calls.length, 3);
    checkScoped(h.calls[1], 'rest/batch', 'status');
    checkScoped(h.calls[2], 'mcp-batch', 'status');
    reply(h.calls[1], statusOf('rest/batch', 'done', [
        { item_id: 'rest-quota', node_id: 'quota-rest', status: 'failed', failure: quota, can_skip: 'true' },
        { item_id: 'recovered', status: 'succeeded', attempts: 2, last_failure: { message: '模型暂不可用' } },
    ]));
    reply(h.calls[2], statusOf('mcp-batch', 'paused', [{ item_id: 'mcp-quota', status: 'paused', failure: quota }],
        { reason: 'QUOTA_EXHAUSTED', can_resume: true }));
    await settle();
    const html = h.html();
    assert.match(html, /data-agent-batch-status="rest\/batch"/);
    assert.match(html, /data-agent-batch-notice="rest-quota"[^]*quota-rest：遇到服务商额度限制/);
    assert.match(html, /模型暂不可用；自动重试后已生成[^]*data-agent-batch-status="mcp-batch"[^]*整个批次已暂停/);
    assert.equal(html.match(/整个批次已暂停/g)?.length, 1);
    assert.match(html, /data-agent-recovery-action="resume"/);
    assert.doesNotMatch(html, /data-agent-recovery-action="(?:skip|retry)"|已继续后续/);
});

test('skip and resume use scoped flags and receipts; stale and forbidden operations do not duplicate', async () => {
    const h = setup();
    h.event(approvedBatch('batch'));
    const paused = statusOf('batch', 'paused', [{ item_id: 'row', status: 'paused', failure: quota, can_skip: true }],
        { reason: 'QUOTA_EXHAUSTED', can_resume: true });
    const track = h.state.batchRecovery.get('batch');
    reply(h.calls[0], paused);
    await settle();
    const staleRead = h.ctx.agentRefreshBatchRecovery(h.node.id, h.state, track);
    const skip = actionFor(h, track, 'skip', 'row');
    await actionFor(h, track, 'skip', 'row');
    assert.equal(h.calls.length, 3);
    assert.equal(h.calls[1].options.signal.aborted, true);
    checkScoped(h.calls[2], 'batch', 'skip', { item_id: 'row', snapshot_sha256: HASH });
    reply(h.calls[2], { ...paused, items: [{ ...paused.items[0], can_skip: false, user_skipped: true }] });
    await skip;
    reply(h.calls[1], paused);
    await staleRead;
    assert.match(h.html(), /已手动跳过/);
    assert.match(h.html(), /整个批次已暂停/);
    assert.doesNotMatch(h.html(), /data-agent-recovery-action="(?:retry|skip)"/);
    const resume = actionFor(h, track, 'resume');
    checkScoped(h.calls[3], 'batch', 'resume', { snapshot_sha256: HASH });
    reply(h.calls[3], { detail: { code: 'PREFLIGHT_STALE' } }, 409);
    await resume;
    checkScoped(h.calls[4], 'batch', 'status');
    assert.match(h.html(), /操作已过期或条件已变化/);
    assert.doesNotMatch(h.html(), /data-agent-recovery-action="resume"/);
    reply(h.calls[4], { detail: { code: 'PROJECT_MISMATCH' } }, 403);
    await settle();
    assert.match(h.html(), /无权访问此批次/);
    assert.equal(h.calls.length, 5);
});

test('batch retry replay drives scoped HTTP decision even with a connected socket', async () => {
    const h = setup();
    const socketWrites = [];
    h.ctx.WebSocket = { OPEN: 1 };
    h.state.socket = { readyState: 1, send: data => socketWrites.push(data) };
    const dialogs = [];
    h.ctx.ffDialog = { ask: options => new Promise(resolve => dialogs.push({ options, resolve })), close() {} };
    const batch = 'needs-retry';
    const confirmId = 'confirm-batch-retry-501';
    h.event(approvedBatch(batch, 'mcp'));
    const failed = statusOf(batch, 'done', [{ item_id: 'row', status: 'failed', failure: quota, can_retry: true }]);
    reply(h.calls[0], failed);
    await settle();
    const receipt = actionFor(h, h.state.batchRecovery.get(batch), 'retry', 'row');
    checkScoped(h.calls[1], batch, 'retry', { item_id: 'row', snapshot_sha256: HASH });
    reply(h.calls[1], { status: 'awaiting_confirmation', confirm_id: confirmId });
    await receipt;
    assert.equal(dialogs.length, 0);
    assert.doesNotMatch(h.html(), /data-agent-recovery-action="retry"/);
    assert.match(h.calls[2].url, /\/events\?since=0&host=pi$/);
    checkScoped(h.calls[3], batch, 'status');
    const event = batchEvent(batch, { type: 'confirm', confirm_id: confirmId, item_id: 'row', tool: 'generate.retry',
        message: '另行确认本次付费重试', params: { preflight: { opaque: true }, batch_id: batch } });
    reply(h.calls[2], { events: [{ seq: 1, host: 'pi', event }], last_seq: 1 });
    await h.state._replayPromise;
    assert.equal(dialogs.length, 1);
    assert.equal(dialogs[0].options.message, '另行确认本次付费重试');
    assert.match(h.html(), /data-agent-confirm="actor\/487"/);
    checkScoped(h.calls[4], batch, 'status');
    dialogs[0].resolve(false);
    await settle();
    assert.deepEqual(socketWrites, [], 'batch retry decisions do not use the connected socket');
    const decision = h.calls[5];
    assert.match(decision.url, /\/agent-nodes\/actor%2F487\/confirm$/);
    assert.equal(decision.options.headers['X-Agent-Project'], 'project/487');
    assert.deepEqual(JSON.parse(decision.options.body), { confirm_id: confirmId, approved: false });
    reply(decision, { ok: true, confirm_id: confirmId, approved: false });
    await settle();
    assert.match(h.html(), /data-agent-confirm="actor\/487"/);
    reply(h.calls[6], { events: [{ seq: 2, host: 'pi', event: { ...event, type: 'confirm_resolved', approved: false } }], last_seq: 2 });
    await h.state._replayPromise;
    assert.equal(h.calls[4].options.signal.aborted, true);
    checkScoped(h.calls[7], batch, 'status');
    reply(h.calls[7], failed);
    await settle();
    assert.doesNotMatch(h.html(), /data-agent-confirm="|已批准|已生成/);
    assert.match(h.html(), /data-agent-recovery-action="retry"/);
});

test('live batch retry resolution does not abort the scoped HTTP receipt', async () => {
    const h = setup();
    const confirmId = 'confirm-batch-retry-live';
    const gate = { confirm_id: confirmId, tool: 'generate.retry' };
    h.event({ type: 'confirm', ...gate });
    const sent = h.ctx.sendAgentConfirm(h.node.id, 'project/487', true);
    const decision = h.calls[0];
    h.event({ type: 'confirm_resolved', ...gate, approved: true });
    assert.equal(decision.options.signal.aborted, false);
    h.event({ type: 'confirm', ...gate, confirm_id: 'confirm-independent' });
    reply(decision, { ok: true, confirm_id: confirmId, approved: true });
    await sent;
    assert.equal(h.state.confirm.confirm_id, 'confirm-independent');
    assert.equal(h.calls.length, 1);
});

test('an early generic retry resolution does not leave a late receipt stuck waiting', async () => {
    const h = setup();
    const batch = 'fast-decision';
    h.event(approvedBatch(batch));
    const failed = statusOf(batch, 'done', [{ item_id: 'row', status: 'failed', can_retry: true }]);
    reply(h.calls[0], failed);
    await settle();
    const receipt = actionFor(h, h.state.batchRecovery.get(batch), 'retry', 'row');
    const retry = { confirm_id: 'confirm-fast', tool: 'generate.retry' };
    h.event({ type: 'confirm', ...retry });
    h.event({ type: 'confirm_resolved', ...retry, approved: false });
    reply(h.calls[1], { status: 'awaiting_confirmation', confirm_id: retry.confirm_id });
    await receipt;
    assert.equal(h.calls.length, 3);
    checkScoped(h.calls[2], batch, 'status');
    reply(h.calls[2], failed);
    await settle();
    assert.match(h.html(), /data-agent-recovery-action="retry"/);
    assert.doesNotMatch(h.html(), /重试处理中/);
});
