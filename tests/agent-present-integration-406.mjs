import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { setTimeout as delay } from 'node:timers/promises';
import { createCanvasContext, loadModules, evaluate } from './canvas_module_units/harness.mjs';

const [origin, project] = process.argv.slice(2);
const base = new URL(origin);
assert.equal(base.hostname, '127.0.0.1');
assert.ok([3146, 3147, 3148, 3149].includes(Number(base.port)));
const api = `/api/projects/${encodeURIComponent(project)}`;
const actor = `${api}/agent-nodes/actor`;
const trace = [];
const deadline = setTimeout(() => { console.error('Integration Node deadline exceeded'); process.exit(2); }, 35000);

// Relative URL resolution is a browser boundary; every response remains native Node fetch.
async function realFetch(path, options = {}) {
    const url = new URL(path, base);
    assert.equal(url.origin, origin, 'integration traffic must stay on its dedicated loopback listener');
    const call = { path: url.pathname + url.search, method: options.method || 'GET',
        ...(options.body ? { body: JSON.parse(options.body) } : {}) };
    trace.push(call);
    const response = await fetch(url, { ...options, redirect: 'error',
        headers: { Origin: origin, ...options.headers }, signal: AbortSignal.timeout(8000) });
    call.status = response.status;
    call.response = await response.clone().json();
    return response;
}
async function json(path, options) {
    const response = await realFetch(path, options);
    const body = await response.json();
    assert.equal(response.status, 200, JSON.stringify(body));
    return body;
}
const present = args => json(`${api}/agent/present?agent_node_id=actor`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Agent-Project': project },
    body: JSON.stringify(args),
});
const plain = value => JSON.parse(JSON.stringify(value));
const occurrences = (html, text) => html.split(text).length - 1;

function view(node, historyLoaded) {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, ['i18n-core.js', 'canvas-node-types.js', 'canvas-core.js', 'canvas-state.js',
        'canvas-agent-navigation.js', 'canvas-agent-tui.js']);
    vm.runInContext(readFileSync(new URL('../static/js/i18n/smart-canvas.js', import.meta.url), 'utf8'), ctx);
    ctx.fetch = realFetch;
    ctx.location = { ...ctx.location, origin, host: base.host, hostname: base.hostname,
        href: `${origin}/static/smart-canvas.html`, protocol: base.protocol };
    ctx.canvasState.nodes.push(node);
    evaluate(ctx, `canvasId=${JSON.stringify(project)}; canvas={project_id:${JSON.stringify(project)}}`);
    const state = ctx.getAgentTuiState(node.id);
    state.historyLoaded = historyLoaded;
    // This journey consumes HTTP journal records, not a simulated WebSocket/native host.
    state.reconnectStopped = true;
    const mounted = { html: '', button: null, renders: 0 };
    // Only DOM replacement/HTML parsing is substituted. The actual TUI renderer and controls run each time.
    ctx.renderAgentNode = nodeId => {
        assert.equal(nodeId, node.id);
        mounted.html = ctx.agentNodeBodyHtml(node);
        mounted.renders++;
        const el = harness.makeElement('div');
        el.innerHTML = mounted.html;
        const match = /<button\b[^>]*data-agent-question-answer="([^"]+)"[^>]*>/.exec(mounted.html);
        mounted.button = match ? harness.makeElement('button') : null;
        if (mounted.button) {
            mounted.button.setAttribute('data-agent-question-answer', match[1]);
            mounted.button.disabled = /\bdisabled\b/.test(match[0]);
        }
        el.querySelectorAll = selector => selector === '[data-agent-question-answer]' && mounted.button ? [mounted.button] : [];
        ctx.bindAgentNodeControls(el, node);
    };
    ctx.renderAgentNode(node.id);
    return { ctx, state, mounted };
}
function assertCards(html) {
    assert.match(html, /406 plan &lt;img src=x onerror=alert\(1\)&gt;/);
    assert.match(html, /Inspect &lt;script&gt;bad406\(\)&lt;\/script&gt; &amp; preserve &quot;quotes&quot;/);
    assert.match(html, /Which mood &lt;b&gt;now&lt;\/b&gt;\?/);
    assert.match(html, /Warm &lt;img src=x&gt; &amp; &quot;bright&quot;/);
    assert.doesNotMatch(html, /<script>|<img src=x|type="checkbox"|type="radio"/);
    assert.equal(occurrences(html, '<strong>406 plan '), 1);
    assert.equal(occurrences(html, '<strong>406 direction</strong>'), 1);
    assert.ok(html.indexOf('406 plan ') < html.indexOf('406 direction'));
}

try {
    const node = await json(`${api}/canvas/nodes/actor`);
    const plan = (await present({ action: 'show', kind: 'plan',
        title: '406 plan <img src=x onerror=alert(1)>',
        steps: [{ id: 'inspect', text: 'Inspect <script>bad406()</script> & preserve "quotes"' }],
    })).presentation;
    const pending = (await present({ action: 'show', kind: 'question', title: '406 direction', questions: [
        { id: 'mood/406', text: 'Which mood <b>now</b>?', recommended: 'warm/"<&', options: [
            { id: 'warm/"<&', text: 'Warm <img src=x> & "bright"' }, { id: 'cold', text: 'Cold' }] },
        { id: 'size&406', text: 'Size?', recommended: 'large/406', options: [
            { id: 'small', text: 'Small' }, { id: 'large/406', text: 'Large' }] },
    ] })).presentation;
    assert.equal(plan.state, 'presented');
    assert.equal(pending.state, 'pending');
    const before = await json(`${actor}/events/page?host=pi&turns=5`);
    const fullBefore = await json(`${actor}/events?host=pi`);
    assert.deepEqual(before.events, fullBefore.events);
    assert.deepEqual(before.events.filter(r => r.event.type === 'plan').map(r => r.event.presentation), [plan]);
    assert.deepEqual(before.events.filter(r => r.event.type === 'question').map(r => r.event.presentation), [pending]);

    const live = view(node, true);
    // Use the persisted server frames verbatim; neither event identities nor sequence numbers are invented.
    for (const record of before.events) live.ctx.handleAgentEvent(node.id, record.event);
    assertCards(live.mounted.html);
    assert.match(live.mounted.html, /等待回答/);
    const button = live.mounted.button;
    assert.ok(button && !button.disabled, 'the actual renderer must expose the recommendation control');
    assert.equal(button.getAttribute('data-agent-question-answer'), pending.id);
    const row = live.state.messages.find(m => m.presentation?.id === pending.id);
    const anchor = live.state.timelineMessages.get(row).key;
    const questionPath = `${actor}/questions/${encodeURIComponent(pending.id)}`;
    const clickAt = trace.length;
    button.dispatchEvent({ type: 'click', stopPropagation() {} });
    button.dispatchEvent({ type: 'click', stopPropagation() {} });
    assert.match(live.mounted.html, /正在确认回答/);
    assert.doesNotMatch(live.mounted.html, /已回答/);
    assert.equal(trace.length, clickAt + 1, 'a double click must not start a second freshness request');
    const until = Date.now() + 10000;
    while (row.presentationSending && Date.now() < until) await delay(10);
    assert.equal(row.presentationSending, false, 'the real recommendation request must settle');
    assert.equal(row.presentationError || '', '');
    const calls = trace.slice(clickAt);
    assert.deepEqual(calls.map(c => [c.method, c.path, c.status]), [
        ['GET', questionPath, 200], ['POST', `${questionPath}/answers`, 200],
    ]);
    assert.deepEqual(calls[0].response, { presentation: pending, answerable: true });
    const recommended = Object.fromEntries(pending.data.questions.map(q => [q.id, q.recommended]));
    assert.deepEqual(calls[1].body, { answers: recommended });
    const receipt = calls[1].response;
    assert.deepEqual(receipt.presentation, { ...pending, state: 'answered', answers: recommended });
    assert.equal(receipt.input.success, true);
    assert.equal(receipt.input.request_id, `presentation-${pending.id}`);
    assert.deepEqual(plain(row.presentation), receipt.presentation);

    const after = await json(`${actor}/events/page?host=pi&turns=5`);
    const fullAfter = await json(`${actor}/events?host=pi`);
    assert.deepEqual(after.events, fullAfter.events);
    assert.equal(after.has_more, false);
    const answered = after.events.filter(r => r.event.type === 'question' && r.event.presentation.state === 'answered');
    assert.equal(answered.length, 1);
    assert.deepEqual(answered[0].event.presentation, receipt.presentation);
    for (const record of after.events) live.ctx.handleAgentEvent(node.id, record.event);
    // Old pending frames must not undo the same-id authoritative receipt.
    for (const record of before.events) live.ctx.handleAgentEvent(node.id, record.event);
    assert.equal(live.state.messages.find(m => m.presentation?.id === pending.id), row);
    assert.equal(live.state.timelineMessages.get(row).key, anchor);
    assert.deepEqual(plain(row.presentation), receipt.presentation);
    assert.equal(live.state.messages.filter(m => m.presentation?.id === pending.id).length, 1);
    assertCards(live.mounted.html);
    assert.match(live.mounted.html, /已回答/);
    assert.doesNotMatch(live.mounted.html, /data-agent-question-answer|回答未获确认|正在确认回答/);
    const queued = after.events.filter(r => r.event.type === 'queued_input');
    assert.equal(queued.length, 1);
    assert.equal(queued[0].event.request_id, receipt.input.request_id);
    assert.equal(queued[0].event.input_type, 'follow_up');

    const historyAt = trace.length;
    const history = view(plain(node), false);
    await history.state._replayPromise;
    assert.equal(history.state.historyLoaded, true);
    assert.equal(history.state.eventsError, '');
    assertCards(history.mounted.html);
    assert.match(history.mounted.html, /已回答/);
    assert.doesNotMatch(history.mounted.html, /data-agent-question-answer/);
    assert.deepEqual(plain(history.state.messages.find(m => m.presentation?.id === pending.id).presentation), receipt.presentation);
    assert.deepEqual(trace.slice(historyAt).map(c => [c.method, c.path, c.status]), [
        ['GET', `${actor}/events/page?host=pi&turns=5`, 200],
    ], 'the actual page replay is read-only');
    console.log(JSON.stringify({ origin, plan_id: plan.id, question_id: pending.id, receipt,
        records: after.events, trace: trace.map(({ response, ...request }) => request),
        renderer: 'agentNodeBodyHtml', same_id_anchor_preserved: true, history_readonly: true,
        live_renders: live.mounted.renders, history_renders: history.mounted.renders }));
} finally {
    clearTimeout(deadline);
}
