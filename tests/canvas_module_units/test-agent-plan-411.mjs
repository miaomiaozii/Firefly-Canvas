import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { createCanvasContext, loadModules, evaluate } from './harness.mjs';

const reference = { schema: 1, id: 'presentation/411', kind: 'plan', title: '运营 <plan>', state: 'presented', data: { source: 'plan_preview', plan_id: 'plan/411', generation: 1 } };
const preview = (generation = 1) => ({ plan_id: 'plan/411', generation, agent_node_id: 'agent/411', apply_via: 'human_apply',
    application: { state: 'ready', generation, receipt: null }, preview: {
    tasks: [{ task_id: 'a', name: '主体 <img>', section: '主图', selected: false, source_row: 2, source_rows: [2], base_prompt: '照片', overlay_plan: '标题' },
        { task_id: 'b', name: '细节', section: 'A+', selected: true, source_row: 3, source_rows: [3] }],
    sections: { '主图': 1, 'A+': 1 }, warnings: ['请核对 <script>引用</script>'], excluded: [], source: { name: '方案.xlsx', sheet: 'Sheet1' },
} });
const question = { schema: 1, id: 'q411', kind: 'question', title: '开工前', state: 'pending', data: { questions: [
    { id: 'style', text: '风格', recommended: 'warm', options: [{ id: 'warm', text: '暖色' }, { id: 'cold', text: '冷色' }] },
    { id: 'size', text: '比例', recommended: 'square', options: [{ id: 'square', text: '方形' }, { id: 'wide', text: '横图' }] },
] } };
function setup(p = reference, history = false, host = 'pi') {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, ['i18n-core.js', 'canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-agent-navigation.js', 'canvas-agent-tui.js']);
    vm.runInContext(readFileSync(new URL('../../static/js/i18n/smart-canvas.js', import.meta.url), 'utf8'), ctx);
    const node = { id: 'agent/411', type: 'smart-agent', agentCli: host, extSessionId: 'original-thread' };
    ctx.canvasState.nodes.push(node);
    evaluate(ctx, "canvasId='project/411'; canvas={project_id:'project/411'}");
    const state = ctx.getAgentTuiState(node.id); state.historyLoaded = true; state.reconnectStopped = true;
    const calls = []; ctx.fetch = (url, options) => new Promise((resolve, reject) => calls.push({ url, options, resolve, reject }));
    const event = e => ctx.handleAgentEvent(node.id, { host, ...e });
    state._applyingReplay = history; event({ type: p.kind, seq: 1, generation: 17, presentation: p }); state._applyingReplay = false;
    return { ctx, harness, node, state, calls, event, html: () => ctx.agentNodeBodyHtml(node), message: state.messages[0] };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function reply(call, body, status = 200) { assert.ok(call, 'expected a real HTTP boundary'); call.resolve({ ok: status < 400, status, json: async () => body }); }
function bind(h, attr, value, extra = {}) {
    const control = h.harness.makeElement('button'); control.setAttribute(attr, value);
    for (const [key, val] of Object.entries(extra)) key.startsWith('data-') ? control.setAttribute(key, val) : control[key] = val;
    const el = h.harness.makeElement('div'); el.querySelectorAll = selector => selector === `[${attr}]` ? [control] : [];
    h.ctx.bindAgentNodeControls(el, h.node);
    return { control, click: () => control.dispatchEvent({ type: 'click', stopPropagation() {}, preventDefault() {} }), change: () => control.dispatchEvent({ type: 'change', stopPropagation() {} }), input: () => control.dispatchEvent({ type: 'input', stopPropagation() {} }) };
}
async function loaded(h) { const b = bind(h, 'data-agent-plan-refresh', reference.id); if (!h.calls.length) b.click(); reply(h.calls[0], preview()); await flush(); return b; }
const result = () => ({ groups: [{ group_id: 'real-frame', section: 'A+' }],
    tasks: [{ task_id: 'b', prompt_node: 'real-prompt', gen_node: 'real-empty-slot', ref_nodes: [] }],
    already_applied: ['existing-task'], op_id: 'real-op/411', touched_node_ids: ['real-frame', 'real-prompt', 'real-empty-slot'] });
function applied(generation = 1) {
    const value = preview(generation); value.application = { state: 'applied', generation,
        receipt: { generation, applied_by: 'human', result: result() } }; return value;
}
const pending = { approved: false, reason: 'pending', code: 'CONFIRM_PENDING', status: 'awaiting_confirmation', confirm_id: 'real-confirm/411' };
async function posted(h) {
    bind(h, 'data-agent-plan-apply', reference.id).click();
    assert.equal(h.calls.length, 2); reply(h.calls[1], preview()); await flush();
    assert.equal(h.calls.length, 3); return h.calls[2];
}
function visit(h, host) {
    h.ctx.agentChangeHostView(h.node.id, h.state, host);
    h.state.historyLoaded = true; h.state.reconnectStopped = true;
}
function assertManualOnly(h) {
    assert.ok(h.calls.every(call => !/\/(?:prompt|follow_up|steer|continuation|settings|canvas|start|bootstrap)(?:\?|$)/.test(call.url)));
    assert.equal(h.state.messages.filter(row => row.role === 'user' || row.delivery).length, 0);
}

test('literal workbook risks and exclusions are readable localized evidence, with unknown values escaped and preserved', async () => {
    const h = setup(); const b = bind(h, 'data-agent-plan-refresh', reference.id); if (!h.calls.length) b.click();
    const value = preview(); value.preview.warnings = [
        { code: 'CONTINUATION_OR_NON_TASK_ROW', source_row: '方案<行>!11', column: 4 },
        JSON.stringify({ code: 'ANCHOR_OUT_OF_RANGE', source_row: '方案!100', column: 2 }),
        { code: 'NO_WORKBOOK_IMAGE', source_row: '方案!5' },
        { code: 'PROMPT_NEEDS_REVIEW', source_row: '方案!6' },
        { code: 'FUTURE<CODE>', source_row: '未知<行>', details: '<script>原样</script>' },
        '非 JSON 的风险 <b>保留</b>',
    ]; value.preview.excluded = [
        { source_row: '方案!7', skip_reason: 'video' }, { source_row: '方案!8', skip_reason: 'mask' },
        { source_row: '方案!9', skip_reason: 'no_remake' }, { source_row: '方案!10', skip_reason: 'future<reason>' },
    ]; reply(h.calls[0], value); await flush(); const html = h.html();
    for (const text of ['图片位于续行或非任务行', '图片锚点不在工作表有效行中', '未识别到工作簿内的运营示例图', '提示词按文案主题推导', '视频制作内容', '遮罩或蒙版内容', '明确标记无需重做']) assert.ok(html.includes(text), text);
    for (const text of ['方案&lt;行&gt;!11', 'FUTURE&lt;CODE&gt;', '&lt;script&gt;原样&lt;/script&gt;', '非 JSON 的风险 &lt;b&gt;保留&lt;/b&gt;', 'future&lt;reason&gt;']) assert.ok(html.includes(text), text);
    assert.doesNotMatch(html, /<script>|<b>保留<\/b>/); assert.equal(h.message.presentation.data.generation, 1);
});

test('reference loads real human preview, groups actual selection and escapes risks without mutating the historical reference', async () => {
    const h = setup(); await loaded(h);
    assert.equal(h.calls[0].url, '/api/projects/project%2F411/plan/previews/plan%2F411?agent_node_id=agent%2F411');
    assert.equal(h.calls[0].options.headers['X-Agent-Project'], 'project/411');
    assert.equal(h.calls[0].options.credentials, 'same-origin'); assert.equal(h.calls[0].options.cache, 'no-store');
    const html = h.html(); assert.match(html, /主体 &lt;img&gt;/); assert.match(html, /主图/); assert.match(html, /A\+/);
    assert.match(html, /请核对 &lt;script&gt;引用&lt;\/script&gt;/); assert.match(html, /1 \/ 2/);
    assert.match(html, /data-agent-plan-select="a"[^>]*(?<!checked)>/);
    assert.match(html, /data-agent-plan-select="b"[^>]*checked/);
    assert.equal(h.message.presentation.data.generation, 1);
});

test('select save is serialized, exact human edits schema, and refresh shows authoritative saved state', async () => {
    const h = setup(); await loaded(h);
    const select = bind(h, 'data-agent-plan-select', 'a', { 'data-agent-plan-owner': reference.id, checked: true });
    select.change(); select.change();
    assert.equal(h.calls.length, 2); assert.match(h.calls[1].url, /\/edits\?agent_node_id=agent%2F411$/);
    assert.deepEqual(JSON.parse(h.calls[1].options.body), { generation: 1, ops: [{ task_id: 'a', select: true }] });
    const saved = preview(2); saved.preview.tasks[0].selected = true; reply(h.calls[1], saved); await flush();
    assert.match(h.html(), /2 \/ 2/); assert.equal(h.message.presentation.data.generation, 1);
    bind(h, 'data-agent-plan-refresh', reference.id).click(); reply(h.calls[2], saved); await flush(); assert.match(h.html(), /2 \/ 2/);
});

test('rename, section and merge are real explicit operations using draft values, never split or guessed task IDs', async () => {
    const h = setup(); await loaded(h);
    for (const [field, value] of [['rename', '改名 <b>'], ['set_section', '新分区'], ['merge_into', 'b']]) {
        const input = bind(h, 'data-agent-plan-draft', 'a', { 'data-agent-plan-owner': reference.id, 'data-agent-plan-field': field, value }); input.input(); input.change();
    }
    bind(h, 'data-agent-plan-save', 'a', { 'data-agent-plan-owner': reference.id }).click();
    assert.deepEqual(JSON.parse(h.calls[1].options.body), { generation: 1, ops: [
        { task_id: 'a', rename: '改名 <b>' }, { task_id: 'a', set_section: '新分区' }, { task_id: 'a', merge_into: 'b' },
    ] });
    reply(h.calls[1], preview(2)); await flush(); assert.doesNotMatch(h.html(), /data-agent-plan-split/);
});

test('409 conflict reads latest without generation, never replays old ops, and explicitly leaves the edit unsaved', async () => {
    const h = setup(); await loaded(h);
    bind(h, 'data-agent-plan-select', 'a', { 'data-agent-plan-owner': reference.id, checked: true }).change();
    reply(h.calls[1], { detail: { code: 'PLAN_STALE', message: 'stale' } }, 409); await flush();
    assert.equal(h.calls.length, 3); assert.doesNotMatch(h.calls[2].url, /[?&]generation=/);
    reply(h.calls[2], preview(3)); await flush(); assert.match(h.html(), /冲突/); assert.match(h.html(), /未保存/);
    assert.match(h.html(), /1 \/ 2/); assert.equal(h.calls.length, 3);
});

test('network and malformed save receipts are visible, do not advance authority or show successful edits', async () => {
    for (const malformed of [false, true]) {
        const h = setup(); await loaded(h);
        bind(h, 'data-agent-plan-select', 'a', { 'data-agent-plan-owner': reference.id, checked: true }).change();
        if (malformed) reply(h.calls[1], { plan_id: 'other', generation: 99, preview: {} }); else h.calls[1].reject(new Error('offline <network>'));
        await flush(); assert.match(h.html(), /未保存/); assert.match(h.html(), /1 \/ 2/); assert.equal(h.message.presentation.data.generation, 1);
    }
});

test('historical preview remains readable but never becomes editable or executable after a successful read', async () => {
    const h = setup(reference, true); await loaded(h);
    assert.match(h.html(), /主体 &lt;img&gt;/); assert.match(h.html(), /历史记录只读/);
    assert.doesNotMatch(h.html(), /data-agent-plan-(?:apply|select|save)=/);
    bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 1);
});

test('context, node-map, epoch, message, thread and detached controls cannot submit or accept late reads', async () => {
    for (const change of ['host', 'project', 'epoch', 'message', 'map', 'thread', 'detached']) {
        const h = setup(); await loaded(h); const b = bind(h, 'data-agent-plan-apply', reference.id);
        if (change === 'host') h.node.agentCli = 'codex';
        if (change === 'project') evaluate(h.ctx, "canvasId='other'");
        if (change === 'epoch') h.state.viewEpoch++;
        if (change === 'message') h.state.messages = [];
        if (change === 'map') evaluate(h.ctx, "agentTuiState.set('agent/411', {...agentTuiState.get('agent/411')})");
        if (change === 'thread') h.node.extSessionId = 'different-thread';
        if (change === 'detached') b.control.isConnected = false;
        b.click(); assert.equal(h.calls.length, 1, change);
    }
    const h = setup(); const b = bind(h, 'data-agent-plan-refresh', reference.id); if (!h.calls.length) b.click(); h.state.viewEpoch++;
    reply(h.calls[0], preview()); await flush(); assert.doesNotMatch(h.html(), /主体 &lt;img&gt;/);
});

test('every source rechecks human authority and POSTs exact generation once, preserving composer, refs, attachments and camera', async () => {
    for (const host of ['pi', 'codex']) for (const mcpSession of [null, 'hosted-session', 'external-session']) for (const busy of [false, true]) {
        const h = setup(reference, false, host); h.node.mcp_session = mcpSession; await loaded(h); h.state.status = busy ? 'busy' : 'idle';
        h.state.draft = 'unsent draft'; h.state.pendingImages = [{ attachmentId: 'do-not-send', name: 'pending.png' }];
        evaluate(h.ctx, 'viewport={x:123,y:456,scale:0.6}'); h.ctx.canvasState.selectedId = 'canvas-selection';
        h.state.nodeRefsDraft.manual = ['unrelated']; h.state.inputFocus = { start: 2, end: 4, focused: true };
        const b = bind(h, 'data-agent-plan-apply', reference.id); b.click(); b.click(); assert.equal(h.calls.length, 2);
        reply(h.calls[1], preview()); await flush(); assert.equal(h.calls.length, 3); b.click(); assert.equal(h.calls.length, 3);
        assert.equal(h.calls[2].url, '/api/projects/project%2F411/plan/previews/plan%2F411/apply?agent_node_id=agent%2F411');
        assert.equal(h.calls[2].options.method, 'POST'); assert.equal(h.calls[2].options.credentials, 'same-origin');
        assert.deepEqual(JSON.parse(JSON.stringify(h.calls[2].options.headers)), { 'X-Agent-Project': 'project/411', 'Content-Type': 'application/json' });
        assert.equal(h.calls[2].options.body, '{"generation":1}');
        assert.equal(h.state.draft, 'unsent draft'); assert.equal(h.state.pendingImages.length, 1); assert.deepEqual(Array.from(h.state.nodeRefsDraft.manual), ['unrelated']);
        assert.equal(h.state.inputFocus.start, 2); assert.equal(h.state.inputFocus.end, 4);
        assert.equal(h.ctx.canvasState.selectedId, 'canvas-selection');
        assert.equal(evaluate(h.ctx, 'JSON.stringify(viewport)'), '{"x":123,"y":456,"scale":0.6}'); assert.equal(h.node.agentCli, host);
        assert.match(h.html(), /正在请求应用/); assert.doesNotMatch(h.html(), /等待确认|方案已应用|已创建/);
        reply(h.calls[2], applied()); await flush(); assert.match(h.html(), /方案已应用/);
        for (const id of ['real-frame', 'real-prompt', 'real-empty-slot', 'real-op/411', 'existing-task']) assert.ok(h.html().includes(id), id);
        assert.doesNotMatch(h.html(), /已生成|generation.task_id/); b.click(); assert.equal(h.calls.length, 3);
        assert.equal(h.message.presentation.data.generation, 1); assertManualOnly(h);
    }
});

test('apply generation drift requires review; real declined application stays visible without changing an empty composer', async () => {
    const stale = setup(); await loaded(stale); bind(stale, 'data-agent-plan-apply', reference.id).click(); reply(stale.calls[1], preview(2)); await flush();
    assert.equal(stale.calls.length, 2); assert.match(stale.html(), /核对/);
    const h = setup(); await loaded(h); await posted(h);
    const error = { code: 'PLAN_NOT_CONFIRMED', message: 'approval <denied>', retryable: false, details: { reason: 'rejected' } };
    reply(h.calls[2], { detail: error }, 409); await flush(); assert.equal(h.calls.length, 4);
    const declined = preview(); declined.application.error = error; reply(h.calls[3], declined); await flush();
    assert.match(h.html(), /approval &lt;denied&gt;/); assert.match(h.html(), /未获批准/); assert.equal(h.state.draft, '');
    assert.doesNotMatch(h.html(), /方案已应用/); assertManualOnly(h);
});

test('per-question native choices default to recommendation but submit every actual chosen ID after freshness', async () => {
    const h = setup(question); assert.match(h.html(), /<select[^>]*data-agent-question-choice="style"/);
    bind(h, 'data-agent-question-choice', 'style', { 'data-agent-question-owner': question.id, value: 'cold' }).change();
    bind(h, 'data-agent-question-submit', question.id).click(); reply(h.calls[0], { presentation: question, answerable: true }); await flush();
    assert.deepEqual(JSON.parse(h.calls[1].options.body), { answers: { style: 'cold', size: 'square' } });
    reply(h.calls[1], { presentation: { ...question, state: 'answered', answers: { style: 'cold', size: 'square' } } }); await flush(); assert.match(h.html(), /已回答/);
});

test('lost or malformed POST result forbids replay until canonical GET proves a state', async () => {
    for (const malformed of [false, true]) {
        const h = setup(); await loaded(h); await posted(h);
        if (malformed) reply(h.calls[2], { code: 'CONFIRM_PENDING', status: 'awaiting_confirmation' });
        else h.calls[2].reject(new Error('lost response'));
        await flush(); assert.equal(h.calls.length, 4);
        h.calls[3].reject(new Error('GET offline')); await flush();
        bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 4);
        assert.match(h.html(), /结果未知/); assert.doesNotMatch(h.html(), /方案已应用|等待确认/); assertManualOnly(h);
    }
});

test('real CONFIRM_PENDING uses its actual confirm_id and reconciles via GET, never WebSocket or another POST', async () => {
    for (const accepted of [false, true]) {
        const h = setup(); await loaded(h); const frames = []; h.ctx.WebSocket = { OPEN: 1 };
        h.state.socket = { readyState: 1, send: raw => frames.push(JSON.parse(raw)) };
        await posted(h); reply(h.calls[2], pending); await flush();
        assert.match(h.html(), /等待确认/); assert.match(h.html(), /real-confirm\/411/); assert.doesNotMatch(h.html(), /方案已应用/);
        bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 3);
        bind(h, 'data-agent-plan-refresh', reference.id).click(); assert.equal(h.calls[3].options.method, undefined);
        const waiting = preview(); waiting.application = { state: 'awaiting_confirmation', generation: 1, receipt: null, confirm_id: pending.confirm_id };
        reply(h.calls[3], waiting); await flush(); assert.match(h.html(), /等待确认/);
        bind(h, 'data-agent-plan-refresh', reference.id).click();
        const final = accepted ? applied() : preview(); if (!accepted) final.application.error = { code: 'PLAN_NOT_CONFIRMED', message: '未获批准', retryable: false };
        reply(h.calls[4], final); await flush(); assert.match(h.html(), accepted ? /方案已应用/ : /未获批准/);
        assert.equal(frames.length, 0); assert.equal(h.calls.filter(c => c.options.method === 'POST').length, 1); assertManualOnly(h);
    }
});

test('saving selection preserves other unsaved task drafts and a pending save excludes racing refreshes', async () => {
    const h = setup(); await loaded(h);
    bind(h, 'data-agent-plan-draft', 'b', { 'data-agent-plan-owner': reference.id, 'data-agent-plan-field': 'rename', value: '未保存的名称' }).input();
    bind(h, 'data-agent-plan-select', 'a', { 'data-agent-plan-owner': reference.id, checked: true }).change();
    bind(h, 'data-agent-plan-refresh', reference.id).click(); assert.equal(h.calls.length, 2);
    const saved = preview(2); saved.preview.tasks[0].selected = true; reply(h.calls[1], saved); await flush();
    assert.match(h.html(), /value="未保存的名称"/);
    bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 2, 'unsaved names require an explicit save or reload');
});

test('late manual receipts cannot cross project, canvas, epoch, state, node, presentation or thread ownership', async () => {
    for (const change of ['project', 'canvas', 'epoch', 'state', 'node', 'presentation', 'replacement', 'map', 'thread', 'closing']) {
        const h = setup(); await loaded(h); await posted(h);
        if (change === 'project') evaluate(h.ctx, "canvas={project_id:'other'}");
        if (change === 'canvas') evaluate(h.ctx, "canvasId='other'");
        if (change === 'epoch') h.state.viewEpoch++;
        if (change === 'state') evaluate(h.ctx, "agentTuiState.set('agent/411', {...agentTuiState.get('agent/411')})");
        if (change === 'node') h.ctx.canvasState.nodes.length = 0;
        if (change === 'presentation') h.state.messages = [];
        if (change === 'replacement') h.message.presentation = structuredClone(reference);
        if (change === 'map') h.state.planPreviews = new Map();
        if (change === 'thread') h.node.extSessionId = 'replacement';
        if (change === 'closing') h.state.closing = true;
        reply(h.calls[2], applied()); await flush(); assert.doesNotMatch(h.html(), /方案已应用|real-op\/411/, change);
        assert.equal(h.calls.length, 3, change);
    }
});

test('same Codex thread can request application after a runtime generation change, but a thread replacement cannot', async () => {
    const h = setup(reference, false, 'codex'); await loaded(h);
    h.event({ type: 'agent_start', seq: 2, generation: 18 }); h.event({ type: 'agent_end', seq: 3, generation: 18 });
    bind(h, 'data-agent-plan-apply', reference.id).click(); reply(h.calls[1], preview()); await flush(); assert.equal(h.calls.length, 3);
    assert.match(h.calls[2].url, /\/apply\?agent_node_id=agent%2F411$/);
    assert.equal(h.calls[2].options.body, '{"generation":1}');
    reply(h.calls[2], applied()); await flush();
    h.node.extSessionId = 'different-codex-thread';
    bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 3);
});

test('missing or invalid capability, actor, application generation and empty selection never authorize a write', async () => {
    for (const invalid of ['absent', 'old-route', 'actor', 'generation', 'applied-proof', 'pending-id', 'unknown', 'empty']) {
        const h = setup(); await loaded(h); const value = preview();
        if (invalid === 'absent') delete value.apply_via;
        if (invalid === 'old-route') value.apply_via = 'hosted_input';
        if (invalid === 'actor') value.agent_node_id = 'another-agent';
        if (invalid === 'generation') value.application.generation = 2;
        if (invalid === 'applied-proof') value.application.state = 'applied';
        if (invalid === 'pending-id') value.application.state = 'awaiting_confirmation';
        if (invalid === 'unknown') value.application.state = 'unknown';
        if (invalid === 'empty') value.preview.tasks.forEach(task => { task.selected = false; });
        bind(h, 'data-agent-plan-apply', reference.id).click(); reply(h.calls[1], value); await flush();
        assert.equal(h.calls.length, 2, invalid); assert.doesNotMatch(h.html(), /方案已应用/, invalid); assertManualOnly(h);
    }
    const h = setup(); bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 0, 'presentation alone is not authority');
});

test('an unclassified or wrong-actor GET is readable but cannot authorize native editing or apply controls', async () => {
    for (const invalid of ['capability', 'actor', 'application']) {
        const h = setup(); const refresh = bind(h, 'data-agent-plan-refresh', reference.id); if (!h.calls.length) refresh.click();
        const value = preview();
        if (invalid === 'capability') delete value.apply_via;
        if (invalid === 'actor') value.agent_node_id = 'other';
        if (invalid === 'application') delete value.application;
        reply(h.calls[0], value); await flush(); assert.match(h.html(), /主体 &lt;img&gt;/);
        assert.doesNotMatch(h.html(), /data-agent-plan-(?:select|save|apply)=/, invalid);
        bind(h, 'data-agent-plan-select', 'a', { 'data-agent-plan-owner': reference.id, checked: true }).change();
        bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 1, invalid);
    }
});

test('409 already-applied reads canonical real receipt once, and old receipt never marks a new generation applied', async () => {
    for (const edited of [false, true]) {
        const h = setup(); await loaded(h); await posted(h);
        reply(h.calls[2], { detail: { code: 'PLAN_ALREADY_APPLIED', message: 'already', details: { receipt: applied().application.receipt } } }, 409); await flush();
        assert.equal(h.calls.length, 4); assert.doesNotMatch(h.calls[3].url, /generation=/);
        const canonical = edited ? preview(2) : applied(); if (edited) canonical.application.receipt = applied().application.receipt;
        reply(h.calls[3], canonical); await flush(); assert.match(h.html(), /real-op\/411/);
        assert.match(h.html(), edited ? /当前版本尚未应用/ : /方案已应用/);
        if (edited) assert.doesNotMatch(h.html(), /方案已应用/);
        assert.equal(h.message.presentation.data.generation, 1); assert.equal(h.calls.filter(c => c.options.method === 'POST').length, 1);
    }
});

test('apply PLAN_STALE is terminal even if a later manual GET returns readable ready data', async () => {
    const h = setup(); await loaded(h); await posted(h);
    reply(h.calls[2], { detail: { code: 'PLAN_STALE', message: 'stale', hint: 'server text' } }, 409); await flush();
    assert.equal(h.calls.length, 3); assert.match(h.html(), /方案已过期，请让 Agent 重新导入/);
    assert.equal(h.html().match(/方案已过期，请让 Agent 重新导入/g)?.length, 1);
    assert.doesNotMatch(h.html(), /data-agent-plan-(?:apply|select|save)=/);
    bind(h, 'data-agent-plan-refresh', reference.id).click(); reply(h.calls[3], preview(2)); await flush();
    bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 4);
    assert.match(h.html(), /方案已过期，请让 Agent 重新导入/); assert.doesNotMatch(h.html(), /data-agent-plan-(?:apply|select|save)=/);
});

test('an expired first manual read keeps exactly one expiry notice without an application payload', async () => {
    const h = setup(); const refresh = bind(h, 'data-agent-plan-refresh', reference.id); if (!h.calls.length) refresh.click();
    reply(h.calls[0], { detail: { code: 'PLAN_STALE', message: 'original preview expired' } }, 409); await flush();
    assert.equal(h.calls.length, 1);
    assert.equal(h.html().match(/方案已过期，请让 Agent 重新导入/g)?.length, 1);
    assert.doesNotMatch(h.html(), /data-agent-plan-(?:apply|select|save)=/);
});

test('committed application retains append_failed error and real receipt, without unqualified success or replay', async () => {
    const h = setup(); await loaded(h); await posted(h);
    const value = applied(); value.application.error = { code: 'INTERNAL_ERROR', message: '已写入但撤销不可用 <error>',
        retryable: false, hint: '不要重复提交', details: { journal_status: 'append_failed', error: 'journal <lost>' } };
    reply(h.calls[2], { detail: value.application.error }, 500); await flush(); reply(h.calls[3], value); await flush();
    for (const text of ['real-op/411', 'append_failed', '撤销不可用', '不要重复提交', '&lt;error&gt;']) assert.ok(h.html().includes(text), text);
    assert.doesNotMatch(h.html(), />方案已应用[。<]/); bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 4);
    bind(h, 'data-agent-plan-refresh', reference.id).click(); reply(h.calls[4], value); await flush(); assert.match(h.html(), /append_failed/);
});

test('all-existing no_changes exposes actual existing IDs but does not manufacture a consumed generation', async () => {
    const h = setup(); await loaded(h); await posted(h); const value = preview();
    value.no_changes = true; value.result = { groups: [], tasks: [], already_applied: ['existing-task'], op_id: null };
    reply(h.calls[2], value); await flush(); assert.match(h.html(), /existing-task/); assert.match(h.html(), /没有新增/);
    assert.doesNotMatch(h.html(), /方案已应用/);
});

test('editing an applied preview retains the old real receipt but applies only the new current generation', async () => {
    const h = setup(); await loaded(h); await posted(h); reply(h.calls[2], applied()); await flush();
    bind(h, 'data-agent-plan-select', 'a', { 'data-agent-plan-owner': reference.id, checked: true }).change();
    const edited = preview(2); edited.preview.tasks[0].selected = true; edited.application.receipt = applied().application.receipt;
    reply(h.calls[3], edited); await flush(); assert.match(h.html(), /当前版本尚未应用/); assert.match(h.html(), /实际应用回执 · 版本 1/);
    assert.doesNotMatch(h.html(), /方案已应用/); bind(h, 'data-agent-plan-apply', reference.id).click();
    reply(h.calls[4], edited); await flush(); assert.equal(h.calls[5].options.body, '{"generation":2}');
    reply(h.calls[5], applied(2)); await flush(); assert.match(h.html(), /实际应用回执 · 版本 2/);
    assert.equal(h.message.presentation.data.generation, 1); assertManualOnly(h);
});

test('unrelated epoch invalidation cannot be laundered through later read-only visits', async () => {
    const h = setup(); await loaded(h); await posted(h); const view = h.ctx.agentPlanView(h.state, h.message);
    h.state.viewEpoch++; visit(h, 'codex'); reply(h.calls[2], applied()); await flush(); visit(h, 'pi'); await flush();
    assert.equal(view.busy, ''); assert.notEqual(view.application.state, 'applied'); assert.doesNotMatch(h.html(), /real-op\/411/);
    assert.equal(h.calls.length, 3);
});

test('a lost POST remains unknown if reconciliation has no valid application capability or receipt', async () => {
    for (const invalid of ['application', 'actor', 'capability', 'generation']) {
        const h = setup(); await loaded(h); await posted(h); h.calls[2].reject(new Error('lost POST')); await flush();
        const value = applied();
        if (invalid === 'application') delete value.application;
        if (invalid === 'actor') value.agent_node_id = 'other';
        if (invalid === 'capability') delete value.apply_via;
        if (invalid === 'generation') value.application.receipt.generation = 2;
        reply(h.calls[3], value); await flush();
        assert.match(h.html(), /结果未知/, invalid); assert.doesNotMatch(h.html(), /方案已应用|real-op\/411/, invalid);
        bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 4, invalid);
    }
});

test('pending reconciliation rejects a changed thread even though ordinary historical reads are permitted', async () => {
    for (const visitOther of [false, true]) {
        const h = setup(); await loaded(h); await posted(h); reply(h.calls[2], pending); await flush();
        bind(h, 'data-agent-plan-refresh', reference.id).click();
        if (visitOther) visit(h, 'codex');
        h.node.extSessionId = 'new-thread'; reply(h.calls[3], applied()); await flush();
        if (visitOther) { visit(h, 'pi'); await flush(); }
        assert.doesNotMatch(h.html(), /方案已应用|real-op\/411/); assert.equal(h.calls.length, 4);
    }
});

test('late PLAN_ALREADY_APPLIED and competing CONFLICT reconcile canonical state after repeated read-only visits', async () => {
    for (const code of ['PLAN_ALREADY_APPLIED', 'CONFLICT']) {
        const h = setup(); await loaded(h); await posted(h);
        visit(h, 'codex'); visit(h, 'pi'); await flush(); visit(h, 'codex');
        reply(h.calls[2], { detail: { code, message: 'real 409', retryable: false } }, 409); await flush();
        assert.equal(h.calls.length, 3); assert.equal(h.state.planPreviews.size, 0);
        visit(h, 'pi'); await flush(); assert.equal(h.calls.length, 4);
        assert.equal(h.calls[3].options.credentials, 'same-origin'); assert.equal(h.calls[3].options.headers['X-Agent-Project'], 'project/411');
        const value = code === 'PLAN_ALREADY_APPLIED' ? applied() : preview();
        if (code === 'CONFLICT') value.application = { state: 'awaiting_confirmation', generation: 1, receipt: null, confirm_id: 'other-real-confirm' };
        visit(h, 'codex'); reply(h.calls[3], value); await flush(); assert.doesNotMatch(h.html(), /real-op\/411|other-real-confirm/);
        visit(h, 'pi'); await flush(); assert.equal(h.ctx.agentPlanView(h.state, h.message).busy, '');
        assert.match(h.html(), code === 'PLAN_ALREADY_APPLIED' ? /real-op\/411/ : /other-real-confirm/);
        bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.length, 4); assertManualOnly(h);
    }
});

for (const phase of ['preflight', 'post-success', 'post-pending', 'post-409', 'pending-get', 'pending-get-409']) {
    test(`manual ${phase} settles only the originating plan after read-only Pi→Codex→Pi, with one POST`, async () => {
        const h = setup(); await loaded(h); h.state.draft = 'Pi 草稿'; h.state.pendingImages = [{ name: 'Pi 附件' }];
        h.state.nodeRefsDraft.manual = ['Pi 引用']; h.state.inputFocus = { start: 1, end: 3 };
        if (phase === 'preflight') bind(h, 'data-agent-plan-apply', reference.id).click(); else await posted(h);
        if (phase.startsWith('pending-get')) {
            reply(h.calls[2], pending); await flush(); bind(h, 'data-agent-plan-refresh', reference.id).click();
        }
        const origin = h.ctx.agentPlanView(h.state, h.message), response = h.calls.at(-1);
        visit(h, 'codex'); h.state.draft = 'Codex 草稿'; h.state.nodeRefsDraft.manual = ['Codex 引用'];
        if (phase.endsWith('409')) reply(response, { detail: { code: 'PLAN_STALE', message: 'late stale' } }, 409);
        else reply(response, phase === 'preflight' ? preview() : phase === 'post-pending' ? pending : applied());
        await flush(); assert.equal(h.state.planPreviews.size, 0); assert.equal(h.state.draft, 'Codex 草稿');
        assert.deepEqual(Array.from(h.state.nodeRefsDraft.manual), ['Codex 引用']); assert.doesNotMatch(h.html(), /real-op\/411|late stale|real-confirm\/411/);
        visit(h, 'pi'); await flush();
        if (phase === 'preflight') { assert.equal(h.calls.length, 3); reply(h.calls[2], applied()); await flush(); }
        assert.equal(origin.busy, '');
        assert.match(h.html(), phase.endsWith('409') ? /方案已过期，请让 Agent 重新导入/ : phase === 'post-pending' ? /等待确认/ : /real-op\/411/);
        assert.equal(h.state.draft, 'Pi 草稿'); assert.equal(h.state.pendingImages[0].name, 'Pi 附件');
        assert.deepEqual(Array.from(h.state.nodeRefsDraft.manual), ['Pi 引用']); assert.equal(h.state.inputFocus.end, 3);
        bind(h, 'data-agent-plan-apply', reference.id).click(); assert.equal(h.calls.filter(c => c.options.method === 'POST').length, 1); assertManualOnly(h);
    });
}

test('deferred manual receipt cannot bypass changed owner or an unrelated epoch after a host return', async () => {
    for (const change of ['project', 'canvas', 'thread', 'presentation', 'state', 'node', 'closing', 'epoch']) {
        const h = setup(); await loaded(h); await posted(h); const view = h.ctx.agentPlanView(h.state, h.message);
        visit(h, 'codex'); reply(h.calls[2], applied()); await flush();
        if (change === 'project') evaluate(h.ctx, "canvas={project_id:'other'}");
        if (change === 'canvas') evaluate(h.ctx, "canvasId='other'");
        if (change === 'thread') h.node.extSessionId = 'replacement';
        if (change === 'presentation') h.state.hostViews.get('pi').messages.splice(0, 1);
        if (change === 'state') evaluate(h.ctx, "agentTuiState.set('agent/411', {...agentTuiState.get('agent/411')})");
        if (change === 'node') h.ctx.canvasState.nodes.length = 0;
        if (change === 'closing') h.state.closing = true;
        visit(h, 'pi'); if (change === 'epoch') h.state.viewEpoch++;
        await flush(); assert.notEqual(view.application?.state, 'applied', change); assert.equal(h.calls.length, 3, change);
    }
});

test('focused plan draft survives uncontrolled live updates without remounting the native field', async () => {
    const h = setup(); await loaded(h);
    const outer = h.harness.makeElement('div'), editor = h.harness.makeElement('details'), input = h.harness.makeElement('input');
    input.setAttribute('data-agent-plan-draft', 'a'); input.setAttribute('data-agent-plan-owner', reference.id); input.setAttribute('data-agent-plan-field', 'rename');
    input.value = '正在输入'; input.selectionStart = 1; input.selectionEnd = 3;
    input.closest = () => editor; outer.contains = target => target === input;
    h.harness.querySelectorAll('[data-id="agent/411"]', [outer]); h.ctx.document.activeElement = input;
    let layouts = 0; h.ctx.imageLayout = () => { layouts++; return null; };
    h.event({ type: 'message_update', seq: 2, assistantMessageEvent: { type: 'text_delta', delta: 'unrelated live text' } });
    assert.equal(layouts, 0); assert.equal(h.ctx.document.activeElement, input); assert.equal(input.selectionEnd, 3); assert.equal(h.state.planDraftRerender, true);
});

test('leaving a focused draft for another card action does not detach that action before its click', async () => {
    const h = setup(); await loaded(h); const el = h.harness.makeElement('div'), editor = h.harness.makeElement('details'), button = h.harness.makeElement('button');
    editor.setAttribute('data-agent-plan-editor', 'a'); editor.setAttribute('data-agent-plan-owner', reference.id);
    el.querySelectorAll = selector => selector === '[data-agent-plan-editor]' ? [editor] : [];
    el.contains = target => target === button;
    h.ctx.bindAgentNodeControls(el, h.node);
    h.state.planDraftRerender = true; h.ctx.document.activeElement = button;
    h.harness.querySelectorAll('[data-id="agent/411"]', [el]);
    let layouts = 0; h.ctx.imageLayout = () => { layouts++; return null; };
    editor.dispatchEvent({ type: 'focusout' }); await flush(); assert.equal(layouts, 0);
});

test('false answerability never POSTs, and late freshness receipt cannot replace a same-id authoritative answer', async () => {
    const h = setup(question); bind(h, 'data-agent-question-submit', question.id).click(); reply(h.calls[0], { presentation: question, answerable: false }); await flush();
    assert.equal(h.calls.length, 1); assert.match(h.html(), /回答未获确认/);
    const race = setup(question); bind(race, 'data-agent-question-submit', question.id).click();
    race.event({ type: 'question', seq: 2, presentation: { ...question, state: 'answered', answers: { style: 'cold', size: 'wide' } } });
    reply(race.calls[0], { presentation: question }); await flush(); assert.equal(race.calls.length, 1); assert.match(race.html(), /已回答/);
});

test('fresh question options must contain all selected IDs; authoritative same-id event wins racing HTTP receipt', async () => {
    const h = setup(question); bind(h, 'data-agent-question-choice', 'style', { 'data-agent-question-owner': question.id, value: 'cold' }).change();
    bind(h, 'data-agent-question-submit', question.id).click();
    const changed = structuredClone(question); changed.data.questions[0].options = [{ id: 'warm', text: '暖色' }, { id: 'new', text: '新' }];
    reply(h.calls[0], { presentation: changed }); await flush(); assert.equal(h.calls.length, 1); assert.match(h.html(), /回答未获确认/);
    assert.match(h.html(), /<option value="" selected disabled>请选择有效选项/);
    const race = setup(question); bind(race, 'data-agent-question-submit', question.id).click(); reply(race.calls[0], { presentation: question }); await flush();
    race.event({ type: 'question', seq: 2, presentation: { ...question, state: 'expired' } });
    reply(race.calls[1], { presentation: { ...question, state: 'answered', answers: { style: 'warm', size: 'square' } } }); await flush();
    assert.match(race.html(), /已过期/); assert.doesNotMatch(race.html(), /已回答/);
});
