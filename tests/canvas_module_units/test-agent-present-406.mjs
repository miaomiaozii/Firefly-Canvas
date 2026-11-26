import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createCanvasContext, loadModules, evaluate } from './harness.mjs';

// Only browser/network boundaries are substituted; event reduction, rendering and binding are real.
function setup() {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, ['i18n-core.js', 'canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-agent-navigation.js', 'canvas-agent-tui.js']);
    vm.runInContext(readFileSync(new URL('../../static/js/i18n/smart-canvas.js', import.meta.url), 'utf8'), ctx);
    const node = { id: 'agent/1', type: 'smart-agent', agentCli: 'pi' };
    ctx.canvasState.nodes.push(node);
    evaluate(ctx, "canvasId='project/1'; canvas={project_id:'project/1'}");
    const state = ctx.getAgentTuiState(node.id);
    state.historyLoaded = true;
    state.reconnectStopped = true;
    const calls = [];
    ctx.fetch = (url, options) => new Promise((resolve, reject) => calls.push({ url, options, resolve, reject }));
    return { ctx, harness, node, state, calls,
        event: event => ctx.handleAgentEvent(node.id, { host: 'pi', ...event }),
        html: () => ctx.agentNodeBodyHtml(node) };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
function bindAnswer(h) {
    const match = /data-agent-question-answer="([^"]+)"/.exec(h.html());
    assert.ok(match, 'the visible recommendation button must exist');
    const button = h.harness.makeElement('button');
    button.setAttribute('data-agent-question-answer', match[1]);
    const el = h.harness.makeElement('div');
    el.querySelectorAll = selector => selector === '[data-agent-question-answer]' ? [button] : [];
    h.ctx.bindAgentNodeControls(el, h.node);
    return () => button.dispatchEvent({ type: 'click', stopPropagation() {} });
}
function respond(call, presentation) { call.resolve({ ok: true, status: 200, json: async () => ({ presentation }) }); }
const question = { schema: 1, id: 'question/1', kind: 'question', title: 'Choose direction', state: 'pending', data: { questions: [
    { id: 'mood', text: 'Which mood <b>now</b>?', options: [{ id: 'warm', text: 'Warm <img src=x>' }, { id: 'cold', text: 'Cold' }], recommended: 'warm' },
    { id: 'size', text: 'Size?', options: [{ id: 'large', text: 'Large' }, { id: 'small', text: 'Small' }], recommended: 'large' },
] } };
const plan = { schema: 1, id: 'p1', kind: 'plan', title: 'Proposal <img src=x onerror=alert(1)>', state: 'presented', data: { steps: [{ id: 's1', text: 'Read <script>bad()</script>' }, { id: 's2', text: 'Then generate' }] } };

test('real structured plan appears once in conversation order, not as executable tasks or parsed prose', () => {
    const h = setup();
    h.event({ type: 'message_update', seq: 1, assistantMessageEvent: { type: 'text_delta', delta: 'Before the proposal' } });
    h.event({ type: 'plan', seq: 2, presentation: plan });
    h.event({ type: 'plan', seq: 2, presentation: plan });
    h.event({ type: 'plan', seq: 3, presentation: plan });
    h.event({ type: 'message_update', seq: 4, assistantMessageEvent: { type: 'text_delta', delta: 'After the proposal' } });
    const html = h.html();
    assert.match(html, /Read &lt;script&gt;bad\(\)&lt;\/script&gt;/);
    assert.match(html, /Proposal &lt;img src=x onerror=alert\(1\)&gt;/);
    assert.equal(html.split('Then generate').length - 1, 1);
    assert.ok(html.indexOf('Before the proposal') < html.indexOf('Then generate'));
    assert.ok(html.indexOf('Then generate') < html.indexOf('After the proposal'));
    assert.doesNotMatch(html, /<script>|<img src=x|type="checkbox"|data-agent-question-answer/);
    assert.equal(h.calls.length, 0);
});

test('empty presentation content is explicitly empty and cannot offer an answer action', () => {
    const h = setup();
    h.event({ type: 'plan', seq: 1, presentation: { ...plan, title: '', data: { steps: [] } } });
    h.event({ type: 'question', seq: 2, presentation: { ...question, title: '', data: { questions: [] } } });
    h.event({ type: 'diff', seq: 3, presentation: { schema: 1, id: 'empty-diff', kind: 'diff', title: '', state: 'presented', data: {} } });
    const html = h.html();
    assert.match(html, /暂无内容/);
    assert.match(html, /提议，尚未执行/);
    assert.doesNotMatch(html, /data-agent-question-answer|undefined|\[object Object\]/);
});

test('a previously live question is not actionable when its host is only being viewed as history', () => {
    const h = setup();
    h.event({ type: 'question', seq: 1, presentation: question });
    assert.match(h.html(), /data-agent-question-answer/);
    h.node.agentCli = 'codex';
    h.state.activeHost = 'codex';
    assert.match(h.html(), /Choose direction/);
    assert.doesNotMatch(h.html(), /data-agent-question-answer/);
});

test('paged history remains read-only and an older pending copy cannot duplicate or overwrite an answered receipt', async () => {
    const h = setup();
    h.state.historyLoaded = false;
    h.ctx.bindAgentNodeControls(h.harness.makeElement('div'), h.node);
    h.calls[0].resolve({ ok: true, json: async () => ({ events: [{ seq: 3, event: { type: 'turn_end', message: { content: [{ type: 'text', text: 'Between question and receipt' }] } } }, { seq: 4, host: 'pi', event: { type: 'question', presentation: { ...question, state: 'answered', answers: { mood: 'cold', size: 'small' } } } }], last_seq: 4, has_more: true, cursor: 'older' }) });
    await flush();
    assert.match(h.html(), /已回答/);
    const older = h.harness.makeElement('button'), el = h.harness.makeElement('div');
    el.querySelector = selector => selector === '[data-agent-events-older]' ? older : null;
    h.ctx.bindAgentNodeControls(el, h.node);
    older.dispatchEvent({ type: 'click', stopPropagation() {} });
    h.calls[1].resolve({ ok: true, json: async () => ({ events: [{ seq: 1, event: { type: 'plan', presentation: plan } }, { seq: 2, event: { type: 'question', presentation: question } }], has_more: false }) });
    await flush();
    const html = h.html();
    assert.equal(html.split('Choose direction').length - 1, 1);
    assert.match(html, /已回答/);
    assert.doesNotMatch(html, /data-agent-question-answer/);
    assert.ok(html.indexOf('Then generate') < html.indexOf('Choose direction'));
    assert.ok(html.indexOf('Choose direction') < html.indexOf('Between question and receipt'));
    assert.equal(h.calls.length, 2, 'history reads never submit answers');
});

test('resolved diff data is read-only escaped text alongside existing rich tool events', () => {
    const h = setup();
    h.event({ type: 'tool_execution_start', seq: 1, toolName: 'canvas.get_node', toolCallId: 'tool-1', args: { node_id: 'other' } });
    h.event({ type: 'diff', seq: 2, presentation: { schema: 1, id: 'd1', kind: 'diff', title: 'Resolved change', state: 'presented', data: { op_id: 'op-actual', changes: [{ before: '<svg onload=alert(1)>', after: 'New prompt' }], applied: false } } });
    h.event({ type: 'tool_execution_end', seq: 3, toolCallId: 'tool-1', result: 'Original result details', isError: false });
    const html = h.html();
    assert.match(html, /Resolved change/);
    assert.match(html, /op-actual/);
    assert.match(html, /&lt;svg onload=alert\(1\)&gt;/);
    assert.match(html, /New prompt/);
    assert.match(html, /Original result details/);
    assert.doesNotMatch(html, /<svg onload|data-agent-question-answer/);
    assert.equal(h.calls.length, 0);
});

test('large diff receipts distinguish complete changes from read-only count summaries', () => {
    const h = setup();
    h.event({ type: 'diff', seq: 1, presentation: { schema: 1, id: 'full', kind: 'diff', title: '15 rows', state: 'presented', data: {
        source: 'operation', op_id: 'full-op', detail: 'merged', changes: [{ id: '<svg onload=alert(1)>', before: { present: false }, after: { present: true, value: '商品 15' } }],
    } } });
    h.event({ type: 'diff', seq: 2, presentation: { schema: 1, id: 'summary', kind: 'diff', title: 'Larger diff', state: 'presented', data: {
        source: 'operation', op_id: 'summary-op', detail: 'summary', counts: { changes: 47, nodes: 32, connections: 15, media: 0 }, undone: true,
    } } });
    h.event({ type: 'diff', seq: 3, presentation: { schema: 1, id: 'preview', kind: 'diff', title: 'Prompt preview', state: 'presented', data: {
        source: 'preview', diff_id: 'preview-diff', detail: 'summary', counts: { edits: 2 }, applied: false,
    } } });
    const html = h.html();
    assert.match(html, /全部变更/);
    assert.match(html, /商品 15/);
    assert.match(html, /&lt;svg onload=alert\(1\)&gt;/);
    assert.match(html, /仅显示摘要/);
    assert.match(html, /47 项变更，涉及 32 个节点、15 条连线、0 个媒体条目/);
    assert.match(html, /summary-op/);
    assert.match(html, /操作已撤销/);
    assert.match(html, /2 项提示词编辑，尚未应用/);
    assert.doesNotMatch(html, /<svg onload|data-agent-question-answer|data-agent-plan-apply/);
    assert.equal(h.calls.length, 0);
});

test('recommendation click checks freshness, submits only recommended IDs and waits for an authoritative receipt', async () => {
    const h = setup();
    h.event({ type: 'question', seq: 1, presentation: question });
    const click = bindAnswer(h);
    click(); click();
    assert.equal(h.calls.length, 1);
    assert.equal(h.calls[0].url, '/api/projects/project%2F1/agent-nodes/agent%2F1/questions/question%2F1');
    assert.match(h.html(), /正在确认回答/);
    assert.doesNotMatch(h.html(), /已回答/);
    respond(h.calls[0], question); await flush();
    assert.equal(h.calls.length, 2);
    assert.equal(h.calls[1].url, '/api/projects/project%2F1/agent-nodes/agent%2F1/questions/question%2F1/answers');
    assert.equal(h.calls[1].options.method, 'POST');
    assert.equal(h.calls[1].options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(h.calls[1].options.body), { answers: { mood: 'warm', size: 'large' } });
    assert.doesNotMatch(h.html(), /已回答/);
    respond(h.calls[1], { ...question, state: 'answered', answers: { mood: 'warm', size: 'large' } }); await flush();
    assert.match(h.html(), /已回答/);
    assert.doesNotMatch(h.html(), /data-agent-question-answer/);
    assert.equal(h.calls.length, 2, 'no canvas/settings/prompt write or native launch endpoint');
});

test('freshness expiration suppresses POST and shows expired, not answered', async () => {
    const h = setup();
    h.event({ type: 'question', seq: 1, presentation: question });
    bindAnswer(h)();
    respond(h.calls[0], { ...question, state: 'expired' }); await flush();
    assert.match(h.html(), /已过期，不能继续回答/);
    assert.doesNotMatch(h.html(), /data-agent-question-answer|已回答/);
    assert.equal(h.calls.length, 1);
});

test('rejected answer is visibly unconfirmed with escaped error and a retry can recover the receipt', async () => {
    const h = setup();
    h.event({ type: 'question', seq: 1, presentation: question });
    bindAnswer(h)(); respond(h.calls[0], question); await flush();
    h.calls[1].resolve({ ok: false, status: 409, json: async () => ({ detail: { message: '<img src=x> busy' } }) }); await flush();
    assert.match(h.html(), /回答未获确认/);
    assert.match(h.html(), /&lt;img src=x&gt; busy/);
    assert.doesNotMatch(h.html(), /已回答|<img src=x>/);
    bindAnswer(h)();
    respond(h.calls[2], { ...question, state: 'answered', answers: { mood: 'warm', size: 'large' } }); await flush();
    assert.match(h.html(), /已回答/);
    assert.doesNotMatch(h.html(), /回答未获确认|data-agent-question-answer/);
    assert.equal(h.calls.length, 3, 'retry reads current receipt instead of blindly resubmitting');
});

test('host and project changes invalidate detached buttons and late freshness/answer responses', async () => {
    for (const stage of ['freshness', 'answer']) for (const change of ['host', 'project', 'view']) {
        const h = setup();
        h.event({ type: 'question', seq: 1, presentation: question });
        const click = bindAnswer(h); click();
        if (stage === 'answer') { respond(h.calls[0], question); await flush(); }
        if (change === 'host') { h.node.agentCli = 'codex'; h.html(); }
        else if (change === 'view') evaluate(h.ctx, "canvasId='different'");
        else evaluate(h.ctx, "canvasId='different'; canvas.project_id='different'");
        const prior = h.calls.length;
        respond(h.calls.at(-1), stage === 'answer' ? { ...question, state: 'answered', answers: { mood: 'warm', size: 'large' } } : question);
        await flush(); click(); await flush();
        assert.equal(h.calls.length, prior, `${stage}/${change} must not submit into the changed context`);
        assert.doesNotMatch(h.html(), /已回答/);
    }
});

test('malformed freshness receipts leave the question intact and visibly unconfirmed', async () => {
    const h = setup();
    h.event({ type: 'question', seq: 1, presentation: question });
    bindAnswer(h)();
    h.calls[0].resolve({ ok: true, json: async () => ({ presentation: { schema: 1, id: question.id, kind: 'question', state: 'answered' } }) }); await flush();
    assert.match(h.html(), /Choose direction/);
    assert.match(h.html(), /回答未获确认/);
    assert.doesNotMatch(h.html(), /已回答/);
    assert.equal(h.calls.length, 1);
});

test('only schema-1 presentation envelopes become structured UI; long text remains escaped literal text', () => {
    const h = setup();
    h.event({ type: 'plan', seq: 1, steps: [{ text: 'Native plan must stay unknown' }] });
    h.event({ type: 'question', seq: 2, presentation: { ...question, schema: 2, title: 'Future schema must stay unknown' } });
    h.event({ type: 'message_update', seq: 3, assistantMessageEvent: { type: 'text_delta', delta: '{"presentation":{"kind":"question","title":"Prose only"}}' } });
    const longText = 'x'.repeat(12000) + '<script>tail</script>';
    h.event({ type: 'plan', seq: 4, presentation: { ...plan, title: 'Long plan', data: { steps: [{ id: 'long', text: longText }] } } });
    const html = h.html();
    assert.doesNotMatch(html, /Native plan must stay unknown|Future schema must stay unknown|data-agent-question-answer|<script>tail/);
    assert.match(html, /Prose only/);
    assert.ok(html.includes('x'.repeat(12000) + '&lt;script&gt;tail&lt;/script&gt;'));
    assert.equal((html.match(/data-agent-presentation=/g) || []).length, 1);
    const dictionaries = h.ctx.StudioI18n.entries();
    for (const key of Object.keys(dictionaries.zh).filter(key => key.startsWith('smart.agentPresent'))) {
        assert.ok(dictionaries.en[key], `${key} needs a checked English source too`);
    }
});

test('a real answer event overrides a racing or earlier HTTP failure without contradictory error status', async () => {
    for (const eventFirst of [false, true]) {
        const h = setup();
        h.event({ type: 'question', seq: 1, presentation: question });
        bindAnswer(h)(); respond(h.calls[0], question); await flush();
        const receiptEvent = () => h.event({ type: 'question', seq: 2, presentation: { ...question, state: 'answered', answers: { mood: 'warm', size: 'large' } } });
        if (eventFirst) {
            receiptEvent();
            assert.doesNotMatch(h.html(), /正在确认回答/);
        }
        h.calls[1].reject(new Error('Connection interrupted')); await flush();
        if (!eventFirst) receiptEvent();
        const html = h.html();
        assert.match(html, /已回答/);
        assert.doesNotMatch(html, /回答未获确认|Connection interrupted|data-agent-question-answer/);
        assert.equal(html.split('Choose direction').length - 1, 1);
    }
});

test('known HTTP rejection and status delivery errors are not hidden by an answered decision receipt', async () => {
    const h = setup();
    h.event({ type: 'question', seq: 1, presentation: question });
    bindAnswer(h)(); respond(h.calls[0], question); await flush();
    const answered = { ...question, state: 'answered', answers: { mood: 'warm', size: 'large' } };
    h.event({ type: 'question', seq: 2, presentation: answered });
    h.calls[1].resolve({ ok: false, status: 502, json: async () => ({ detail: { message: 'Known <pipe> failure' } }) });
    await flush();
    assert.match(h.html(), /Known &lt;pipe&gt; failure/);
    assert.match(h.html(), /已回答/);
    assert.doesNotMatch(h.html(), /data-agent-question-answer/);

    const fresh = setup();
    fresh.event({ type: 'question', seq: 1, presentation: question });
    bindAnswer(fresh)();
    fresh.calls[0].resolve({ ok: true, status: 200, json: async () => ({ presentation: answered,
        answerable: false, delivery_error: { message: 'Stored delivery <error>' } }) });
    await flush();
    assert.match(fresh.html(), /Stored delivery &lt;error&gt;/);
    assert.equal(fresh.calls.length, 1, 'checking a retained failure must not resend the answer');
});

test('recording a human answer is not proof that the host received it; real delivery failure remains visible in live and history', () => {
    for (const history of [false, true]) {
        const h = setup();
        if (history) h.state._applyingReplay = true;
        const answered = { ...question, state: 'answered', answers: { mood: 'warm', size: 'large' } };
        h.event({ type: 'question', seq: 1, presentation: question });
        h.event({ type: 'question', seq: 2, presentation: answered });
        h.event({ type: 'question', seq: 3, presentation: answered,
            delivery_error: { code: 'UPSTREAM_MODEL_ERROR', message: 'Native pipe <closed>', hint: 'Do not resubmit' } });
        assert.match(h.html(), /已回答/);
        assert.match(h.html(), /Native pipe &lt;closed&gt;/);
        assert.doesNotMatch(h.html(), /data-agent-question-answer/);
        assert.equal(h.html().split('Choose direction').length - 1, 1);
    }
});

test('question choices show recommendations and the same-id answer receipt updates in place', () => {
    const h = setup();
    h.event({ type: 'question', seq: 1, presentation: question });
    let html = h.html();
    assert.match(html, /Which mood &lt;b&gt;now&lt;\/b&gt;\?/);
    assert.match(html, /Warm &lt;img src=x&gt;.*推荐/s);
    assert.match(html, /Large.*推荐/s);
    assert.match(html, /等待回答/);
    assert.match(html, /data-agent-question-answer="question\/1"[^>]*>按推荐开始/);
    assert.doesNotMatch(html, /type="checkbox"|type="radio"|contenteditable/);
    h.event({ type: 'question', seq: 2, presentation: { ...question, state: 'answered', answers: { mood: 'cold', size: 'small' } } });
    html = h.html();
    assert.equal(html.split('Choose direction').length - 1, 1);
    assert.match(html, /已回答/);
    assert.match(html, /Cold.*已选择/s);
    assert.doesNotMatch(html, /data-agent-question-answer/);
    h.event({ type: 'question', seq: 1, presentation: question });
    assert.match(h.html(), /已回答/);
    assert.equal(h.calls.length, 0);
});
