import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/js/canvas-agent-tui.js', import.meta.url), 'utf8');
function fixture() {
    const events = new EventTarget(), sent = [], timers = new Map();
    class CustomEvent extends Event {
        constructor(type, options = {}) { super(type); this.detail = options.detail; }
    }
    const canvasState = { nodes: [{ id: 'a', type: 'smart-agent' }], selectedIds: [], selectedId: '' };
    const document = { querySelector: () => null, querySelectorAll: () => [], getElementById: () => null, activeElement: null, title: 'Test' };
    const context = vm.createContext({ console, document, canvasState, canvasId: 'project',
        NODE_TYPE_AGENT: 'smart-agent', NODE_TYPE_TASK: 'agent-task', NODE_TYPE_IMAGE: 'smart-image',
        NODE_TYPE_PROMPT: 'smart-prompt', NODE_TYPE_GROUP: 'smart-group', NODE_TYPE_CONVERSATION: 'smart-conversation',
        WebSocket: { OPEN: 1 }, window: events, Event, CustomEvent, tr: key => key,
        setTimeout: fn => { const id = timers.size + 1; timers.set(id, fn); return id; },
        clearTimeout: id => timers.delete(id), wire: payload => sent.push(JSON.parse(payload)),
    });
    context.window.canvasState = canvasState;
    vm.runInContext(source, context, { filename: 'canvas-agent-tui.js' });
    const run = code => vm.runInContext(code, context);
    run("getAgentTuiState('a').socket={readyState:1,send:wire}");
    const state = run("getAgentTuiState('a')");
    const apply = (event, timestamp) => { context.event = event; context.recordedAt = timestamp; run("applyAgentEventBody('a',event,recordedAt)"); };
    return { context, run, state, sent, apply };
}

test('multiple follow-ups survive ends and reconcile one by one without replacing rows', () => {
    const f = fixture();
    f.run("sendAgentFollowUp('a','project','first');sendAgentFollowUp('a','project','second');sendAgentSteer('a','project','redirect')");
    const [first, second, steer] = f.sent;
    const [firstRow, secondRow, steerRow] = f.state.messages;
    for (const item of f.sent) f.apply({ type: 'queued_input', input_type: item.type, request_id: item.request_id, text: item.message, node_refs: [] });
    f.apply({ type: 'agent_end', willRetry: true });
    assert.equal(f.state.queuedInputs.size, 3);
    f.apply({ type: 'agent_end' });
    assert.deepEqual([...f.state.queuedInputs], [first.request_id, second.request_id]);
    assert.equal(steerRow.text, '↪ steer: redirect');
    assert.equal(firstRow.text, '⏳ follow_up queued: first');
    f.apply({ type: 'user_prompt', request_id: first.request_id, text: 'first canonical', node_refs: [{ node_id: 'ref' }] }, 1234);
    assert.equal(f.state.messages[0], firstRow);
    assert.equal(firstRow.role, 'user'); assert.equal(firstRow.text, 'first canonical');
    assert.equal(firstRow.timestamp, 1234); assert.equal(firstRow.node_refs[0].node_id, 'ref');
    assert.deepEqual([...f.state.queuedInputs], [second.request_id]);
    f.apply({ type: 'agent_start' }); f.apply({ type: 'agent_end' });
    assert.equal(secondRow.role, 'system'); assert.equal(f.state.queuedInputs.has(second.request_id), true);
    f.apply({ type: 'user_prompt', request_id: second.request_id, text: 'second', node_refs: [] });
    assert.equal(secondRow.role, 'user'); assert.equal(secondRow.text, 'second');
    assert.equal(f.state.queuedInputs.size, 0); assert.equal(f.state.messages.length, 3);
    f.apply({ type: 'user_prompt', request_id: second.request_id, text: 'second', node_refs: [] });
    assert.equal(f.state.messages.length, 3);
    assert.equal(steer.type, 'steer');
});

test('rejection removes only the rejected input, while a delivery error retains the queue', () => {
    const f = fixture();
    f.run("sendAgentFollowUp('a','project','bad');sendAgentFollowUp('a','project','waiting')");
    const [bad, waiting] = f.sent;
    f.apply({ type: 'input_rejected', input_type: 'follow_up', request_id: bad.request_id, message: 'rejected' });
    assert.equal(f.state.queuedInputs.has(bad.request_id), false);
    assert.equal(f.state.queuedInputs.has(waiting.request_id), true);
    assert.equal(f.state.messages[0].delivery, 'unconfirmed');
    f.apply({ type: 'error', request_id: waiting.request_id, message: 'Queued input delivery failed' });
    f.apply({ type: 'agent_end' });
    assert.equal(f.state.queuedInputs.has(waiting.request_id), true);
    assert.equal(f.state.messages[1].text, '⏳ follow_up queued: waiting');
});

test('history gap replay consumes the persisted unbroadcast prompt and keeps later pending input', async () => {
    const f = fixture(), calls = [];
    f.state.projectId = 'project'; f.state.historyLoaded = true; f.state.lastSeq = 3;
    f.apply({ type: 'queued_input', input_type: 'follow_up', request_id: 'one', text: 'one' });
    f.apply({ type: 'queued_input', input_type: 'follow_up', request_id: 'two', text: 'two' });
    f.context.fetch = async url => {
        calls.push(url);
        return { ok: true, json: async () => ({ events: [
            { seq: 4, ts: 10, event: { type: 'user_prompt', request_id: 'one', text: 'one' } },
            { seq: 5, event: { type: 'agent_start' } },
        ] }) };
    };
    f.context.live = { type: 'agent_start', seq: 5 };
    f.run("handleAgentEvent('a',live)");
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.length, 1); assert.match(calls[0], /since=3/);
    assert.equal(f.state.messages.length, 2); assert.equal(f.state.messages[0].role, 'user');
    assert.equal(f.state.messages[0].text, 'one'); assert.equal(f.state.messages[0].timestamp, 10000);
    assert.deepEqual([...f.state.queuedInputs], ['two']); assert.equal(f.state.lastSeq, 5);
});

test('fresh replay rebuilds queue and matches sent history without duplicate rows', () => {
    const f = fixture();
    f.apply({ type: 'queued_input', input_type: 'follow_up', request_id: 'one', text: 'one' });
    f.apply({ type: 'queued_input', input_type: 'follow_up', request_id: 'two', text: 'two' });
    f.apply({ type: 'queued_input', input_type: 'steer', request_id: 'steer', text: 'redirect' });
    f.apply({ type: 'agent_end' });
    f.apply({ type: 'user_prompt', request_id: 'one', text: 'one' }, 77);
    assert.equal(f.state.messages.length, 3);
    assert.equal(f.state.messages[0].role, 'user'); assert.equal(f.state.messages[0].timestamp, 77);
    assert.equal(f.state.messages[1].text, '⏳ follow_up queued: two');
    assert.equal(f.state.messages[2].text, '↪ steer: redirect');
    assert.deepEqual([...f.state.queuedInputs], ['two']);
});

test('restart replay marks lost in-memory follow-up unsent', async () => {
    const f = fixture();
    f.state.projectId = 'project';
    f.context.fetch = async () => ({ ok: true, json: async () => ({
        events: [{ seq: 1, ts: 10, event: { type: 'queued_input', host: 'pi', input_type: 'follow_up', request_id: 'lost', text: 'retain me' } },
            { seq: 2, ts: 11, event: { type: 'input_delivery_terminal', host: 'pi', request_id: 'lost', delivery: 'unsent' } }],
        last_seq: 2, has_more: false, cursor: null,
    }) });
    f.apply({ type: 'session', status: 'stopped' });
    await new Promise(resolve => setImmediate(resolve));
    assert.match(f.state.messages[0].text, /未发送/);
    assert.equal(f.state.queuedInputs.has('lost'), false);
});

test('loading older page renders enriched terminal outcomes for identical and legacy rows', async () => {
    const f = fixture();
    f.state.projectId = 'project';
    f.context.fetch = async url => ({ ok: true, json: async () => url.includes('cursor=') ? {
        events: [
            { seq: 1, event: { type: 'queued_input', input_type: 'follow_up', request_id: 'one', text: 'same', replay_delivery: 'uncertain' } },
            { seq: 2, event: { type: 'queued_input', input_type: 'follow_up', request_id: 'two', text: 'same', replay_delivery: 'unsent' } },
            { seq: 3, event: { type: 'queued_input', input_type: 'follow_up', text: 'legacy', replay_delivery: 'uncertain' } },
        ], has_more: false, cursor: null,
    } : {
        events: [
            { seq: 4, event: { type: 'input_delivery_terminal', request_id: 'one', delivery: 'uncertain' } },
            { seq: 5, event: { type: 'input_delivery_terminal', request_id: 'two', delivery: 'unsent' } },
        ], last_seq: 5, has_more: true, cursor: 'older',
    } });
    f.run("replayAgentEvents('a', getAgentTuiState('a'))");
    await new Promise(resolve => setImmediate(resolve));
    f.run("loadOlderAgentEvents('a', getAgentTuiState('a'))");
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual([...f.state.messages].map(row => row.text), ['状态不确定: same', '未发送: same', '状态不确定: legacy']);
    assert.equal(f.state.queuedInputs.size, 0);
    assert.equal(f.sent.length, 0);
});

test('stopped composer uses prompt even if replay has a pending row', () => {
    const f = fixture();
    f.apply({ type: 'session', status: 'stopped' });
    f.state.queuedInputs.add('lost');
    f.context.input = { value: 'fresh prompt', style: {} };
    f.run("agentTuiSendInput(input, 'a', 'project')");
    assert.equal(f.sent[0].type, 'prompt');
    assert.notEqual(f.sent[0].request_id, 'lost');
});

test('legacy steering replay clears only steering and composer sends the next prompt', () => {
    const f = fixture();
    f.apply({ type: 'queued_input', input_type: 'steer', seq: 10, text: 'same' });
    f.apply({ type: 'queued_input', input_type: 'follow_up', seq: 11, text: 'same' });
    f.apply({ type: 'agent_end' });
    assert.deepEqual([...f.state.queuedInputs], ['queued:11']);
    f.apply({ type: 'session', status: 'stopped' });
    f.state.queuedInputs.add('queued:11');
    f.context.input = { value: 'same', style: {} };
    f.run("agentTuiSendInput(input, 'a', 'project')");
    assert.equal(f.sent[0].type, 'prompt');
    f.apply({ type: 'agent_start' });
    assert.equal(f.state.queueStopped, false);
});

test('uncertain delivery is not queued and explicitly reports missing attachments', () => {
    const f = fixture();
    f.apply({ type: 'queued_input', input_type: 'follow_up', request_id: 'old', text: 'text', attachments_missing_on_restart: 2 });
    f.apply({ type: 'input_delivery_terminal', request_id: 'old', delivery: 'uncertain' });
    assert.match(f.state.messages[0].text, /状态不确定/);
    assert.match(f.state.messages[0].text, /2 个附件不可恢复/);
    assert.equal(f.state.queuedInputs.size, 0);
    assert.equal(f.sent.length, 0);
});
