import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {createCanvasContext, loadModules, evaluate, fromContext, JS_DIR} from './harness.mjs';

const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const change = (id, p, before, after, entity = 'node') => ({entity, id, path: p, before, after});
const present = value => ({present: true, value});
const absent = {present: false};
function operation(op_id = 'op1', changes = [change('prompt1', ['text'], present('old'), present('new'))], extra = {}) {
    return {op_id, turn_id: 'logical1', agent_node_id: 'agent1', tool: 'canvas.batch_edit', args: {},
        before: changes.map(({after, ...entry}) => entry), after: changes.map(({before, ...entry}) => entry).reverse(),
        touched_node_ids: [...new Set(changes.map(c => c.id))], irreversible_effects: [], undone: false, ...extra};
}
function setup() {
    const {ctx, harness} = createCanvasContext();
    ctx.canvasId = 'project1';
    ctx.imageLayout = () => ({});
    ctx.centerViewportOnWorldPoint = () => { throw new Error('must not pan'); };
    ctx.render = () => {};
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-agent-activity.js', 'canvas-agent-completion.js', 'canvas-agent-tui.js']);
    evaluate(ctx, `canvasState.nodes = [{id:'agent1',type:'smart-agent',agentCli:'pi'}, {id:'user1',type:'smart-prompt',text:'user unrelated'}];`);
    const state = ctx.getAgentTuiState('agent1');
    state.projectId = 'project1';
    state.historyLoaded = true;
    const api = ctx.canvasAgentCompletion;
    api.configure({getProjectId: () => ctx.canvasId});
    ctx.agentCompletionBridge();
    const calls = [], dialogs = [], reloads = [];
    let operations = [operation()];
    let undoResult = response({status: 'reverted', reverted: ['op1'], already_reverted: [], conflicts: [], irreversible_effects: [], error: null});
    ctx.fetch = async (url, options) => {
        calls.push({url, options});
        return options?.method === 'POST' ? undoResult : response({operations});
    };
    ctx.ffDialog = {ask: async options => { dialogs.push(options); return true; }};
    ctx.waitForCanvasRefresh = async project => { reloads.push(project); return true; };
    const end = (turn_id = 'logical1', extra = {}) => ctx.applyAgentEventBody('agent1', {type:'agent_end',host:'pi',turn_id,...extra});
    const card = () => state.messages.find(m => m.completion)?.completion;
    return {ctx, harness, state, api, calls, dialogs, reloads, end, card,
        ops: value => { operations = value; }, result: value => { undoResult = value; }};
}
function sectionFixture(f, card = f.card()) {
    const {harness} = f;
    const root = harness.makeElement(), section = harness.makeElement('section'), body = harness.makeElement();
    section.setAttribute('data-agent-completion', card.turnId || '');
    const op = harness.makeElement('button'), turn = harness.makeElement('button'), refresh = harness.makeElement('button');
    op.setAttribute('data-completion-undo', card.operations[0]?.op_id || 'op1');
    turn.setAttribute('data-completion-turn', '');
    refresh.setAttribute('data-completion-refresh', '');
    const group = harness.makeElement('details'); group.setAttribute('data-completion-group', 'prompts');
    const row = harness.makeElement(); row.setAttribute('data-completion-op', card.operations[0]?.op_id || 'op1');
    row.getBoundingClientRect = () => ({top:150});
    body.scrollTop = 10; body.getBoundingClientRect = () => ({top:100});
    section.querySelectorAll = selector => selector === '[data-completion-group]' ? [group]
        : selector === '[data-completion-op]' ? [row] : [op, turn, refresh];
    root.querySelectorAll = () => [section];
    root.querySelector = selector => selector === '[data-agent-tui-body]' ? body : null;
    const click = el => el.dispatchEvent({type:'click', stopPropagation(){}, preventDefault(){}});
    return {root, section, body, op, turn, refresh, group, row, click};
}

test('presentation and completion share one operation identity without a second undo executor', async () => {
    const f = setup();
    const op = operation();
    const receipt = {schema: 1, id: 'receipt-not-op', kind: 'diff', state: 'presented', title: 'Recorded operation',
        data: {source: 'operation', op_id: op.op_id, turn_id: op.turn_id, tool: op.tool,
            before: op.before, after: op.after, undone: false}};
    const diff = () => f.ctx.applyAgentEventBody('agent1', {type: 'diff', host: 'pi', presentation: receipt});
    diff(); f.end(); await flush();
    f.state._applyingReplay = true;
    diff(); f.end();
    f.state._applyingReplay = false;
    await flush();
    assert.equal(f.state.messages.filter(message => message.presentation).length, 1);
    assert.equal(f.state.messages.filter(message => message.completion).length, 1);
    assert.equal(f.state.messages.find(message => message.presentation).presentation.data.op_id, f.card().operations[0].op_id);
    const html = f.ctx.agentNodeBodyHtml(f.ctx.canvasState.nodes[0]);
    const diffMarkup = html.match(/<div[^>]*data-agent-presentation="receipt-not-op"[^>]*>([\s\S]*?)<\/div>/)?.[1];
    assert.ok(diffMarkup);
    assert.match(diffMarkup, /op1/);
    assert.doesNotMatch(diffMarkup, /<button|data-completion-undo|data-completion-turn|data-agent-question-answer/);
    assert.equal((html.match(/data-completion-undo="op1"/g) || []).length, 1);
    assert.equal(f.calls.filter(call => call.options?.method === 'POST').length, 0);

    const approval = deferred();
    f.ctx.ffDialog.ask = options => { f.dialogs.push(options); return approval.promise; };
    const controls = sectionFixture(f);
    f.api.bind(controls.root, 'agent1', f.state);
    controls.click(controls.op); controls.click(controls.op); controls.click(controls.turn);
    assert.equal(f.dialogs.length, 1);
    assert.equal(f.calls.filter(call => call.options?.method === 'POST').length, 0);
    f.ops([{...op, undone: true}]);
    approval.resolve(true);
    await new Promise(resolve => setImmediate(resolve));
    const posts = f.calls.filter(call => call.options?.method === 'POST');
    assert.equal(posts.length, 1);
    assert.equal(posts[0].url, '/api/projects/project1/ops/undo');
    assert.equal(posts[0].options.headers['X-Agent-Project'], 'project1');
    assert.deepEqual(JSON.parse(posts[0].options.body), {op_id: receipt.data.op_id});
    assert.equal(f.card().operations[0].undone, true);
    assert.deepEqual(f.reloads, ['project1']);
    controls.click(controls.op); diff(); f.end(); await flush();
    assert.equal(f.calls.filter(call => call.options?.method === 'POST').length, 1);
});

test('unread content observes both presentation receipts and completion evidence after integration', async () => {
    const f = setup();
    const receipt = {schema: 1, id: 'receipt', kind: 'plan', state: 'presented', title: 'Proposal', data: {steps: [{id: 'a', text: 'before'}]}};
    f.ctx.applyAgentEventBody('agent1', {type: 'plan', host: 'pi', presentation: receipt});
    f.end(); await flush();
    const html = () => f.ctx.agentNodeBodyHtml(f.ctx.canvasState.nodes[0]);
    f.state.scrollState = {atBottom: false, unread: false};
    html(); f.state.scrollState.unread = false;
    f.ctx.applyAgentEventBody('agent1', {type: 'plan', host: 'pi', presentation: {...receipt, data: {steps: [{id: 'a', text: 'after'}]}}});
    html(); assert.equal(f.state.scrollState.unread, true, 'presentation updates remain content');
    f.state.scrollState.unread = false;
    f.card().expanded.add('prompts'); html();
    assert.equal(f.state.scrollState.unread, false, 'expansion remains local UI');
    f.ops([operation(), operation('op2')]);
    await f.api.load('agent1', f.state, f.card()); html();
    assert.equal(f.state.scrollState.unread, true, 'new completion evidence remains content');
});

test('pairs deltas by entity/id/path, groups actual nodes, and safely renders inline removal/addition', async () => {
    const f = setup();
    const changes = [change('new1', [], absent, present({id:'new1',type:'smart-prompt'})),
        change('frame1', [], absent, present({id:'frame1',type:'smart-group'})),
        change('prompt1', ['runSettings','prompt'], present('a<img onerror="x">z'), present('a<script>x</script>z')),
        change('prompt1', ['x'], present(1), present(3)), change('prompt1', ['y'], present(2), present(4)),
        change('prompt1', ['agentStatus'], present('running'), present('failed')),
        change('prompt1', ['lastFailure'], absent, present({code:'UNKNOWN',message:'<bad>'})),
        change('edge1', [], absent, present({id:'edge1'}), 'connection')];
    f.ops([operation('op"<1', changes)]); f.end(); await flush();
    const groups = f.api.aggregate(f.card().operations);
    assert.equal(groups.added.length, 1); assert.equal(groups.frames.length, 1);
    assert.equal(groups.moves.length, 2); assert.equal(new Set(groups.moves.map(r => r.change.id)).size, 1);
    assert.equal(groups.generation.length, 2); assert.equal(groups.other.length, 1);
    const html = f.api.html(f.card(), f.state);
    assert.match(html, /新增组框 1/); assert.match(html, /移动 1/);
    assert.ok(html.includes('a&lt;<del>img onerror=&quot;x&quot;</del><ins>script&gt;x&lt;/script</ins>&gt;z'));
    assert.match(html, /op&quot;&lt;1/); assert.doesNotMatch(html, /<script>|<img|<bad>/);
    assert.doesNotMatch(html, /生成张数|失败数|生成 \d+ 张|失败 \d+/);
    assert.match(html, /不是单节点 \/ 字段/);
    assert.equal(f.api.inlineDiff('😀旧尾', '😀新尾'), '😀<del>旧</del><ins>新</ins>尾');
    const addedSettings=f.api.aggregate([operation('settings',[change('prompt1',['runSettings'],absent,present({prompt:'new text',count:1}))])]);
    assert.equal(addedSettings.prompts.length,1,'a newly present runSettings object still contains factual prompt changes');
});

test('approved contract omits unprovable image totals and states generated media retention beside undo', async () => {
    const f = setup();
    f.ops([operation('admitted', [change('result1', [], absent, present({id:'result1',type:'smart-image',
        agentStatus:'running',pendingTasks:[{taskId:'agent_img_pending',count:12}],images:[]}))],
        {tool:'generate.trigger',args:{count:12},irreversible_effects:['external image generation attempted']})]);
    f.end(); await flush();
    const html = f.api.html(f.card(), f.state);
    assert.doesNotMatch(html, /生成张数|失败数|生成 \d+ 张|失败 \d+|不保证生成结果进入回收区/);
    assert.match(html, /响应结束.*不代表生成完成/);
    assert.match(html, /媒体保留在资源库和历史里，不进入 7 天回收区/);
    assert.match(html, /删除.*新建.*节点/);
    await f.api.undo('agent1',f.state,f.card(),'admitted');
    assert.match(f.dialogs[0].message, /媒体保留在资源库和历史里，不进入 7 天回收区/);
    assert.doesNotMatch(f.dialogs[0].message, /不保证生成结果进入回收区/);
    assert.equal(f.calls.filter(call=>call.options).length,1,'retention must not be implemented with a second delete/recycle call');
    assert.ok(f.calls.every(call=>call.url.includes('/ops')),'completion must not poll providers or introduce aggregation APIs');
    f.ops([]); f.end(); await flush();
    assert.doesNotMatch(f.api.html(f.card(), f.state), /生成张数|失败数|生成 \d+ 张|失败 \d+/);
});

test('real reducer coalesces repeated logical completion, never groups by native turn/display index or phase:end', async () => {
    const f = setup();
    f.ctx.applyAgentEventBody('agent1', {type:'agent_start'});
    f.end(); await flush();
    f.ctx.applyAgentEventBody('agent1', {type:'turn_end',message:{content:[]}});
    f.ctx.applyAgentEventBody('agent1', {type:'agent_start'});
    f.end(); await flush();
    assert.equal(f.state.messages.filter(m => m.completion).length, 1);
    assert.equal(f.calls.length, 2);
    f.end('logical2'); await flush();
    assert.equal(f.state.messages.filter(m => m.completion).length, 2);
    f.end('retry', {willRetry:true});
    f.ctx.applyAgentEventBody('agent1', {type:'sourcephase:end',turn_id:'invented'});
    assert.equal(f.state.messages.filter(m => m.completion).length, 2);
    assert.ok(f.calls.every(c => !c.options));
});

test('legacy/null completion never infers a turn from user_prompt, native turn, seq or queue', async () => {
    const f = setup();
    f.ctx.applyAgentEventBody('agent1', {type:'user_prompt',text:'goal',turn_id:'real-user'});
    f.state.queuedInputs.add('queue'); f.end(null, {seq:99,native_turn_id:'native'}); await flush();
    assert.equal(f.card().turnId, null); assert.equal(f.calls.length, 0);
    const html = f.api.html(f.card(), f.state);
    assert.match(html, /旧历史缺少真实逻辑轮/); assert.doesNotMatch(html, /data-completion-turn|data-completion-undo/);
});

test('empty, capped and foreign logs never assert no changes or full-turn undoability', async () => {
    const f = setup(); f.ops([]); f.end(); await flush();
    assert.match(f.api.html(f.card(), f.state), /不能证明没有改动/);
    await f.api.undo('agent1', f.state, f.card(), null); assert.equal(f.dialogs.length, 0);
    f.ops(Array.from({length:1000}, (_, i) => operation(`op${i}`, []))); f.end(); await flush();
    assert.equal(f.card().capped, true); assert.match(f.api.html(f.card(), f.state), /1000 条读取上限/);
    await f.api.undo('agent1', f.state, f.card(), null); assert.equal(f.dialogs.length, 0);
    f.ops([operation(), operation('foreign', [], {agent_node_id:'another'}), operation('wrongturn', [], {turn_id:null})]);
    f.end(); await flush(); assert.equal(f.card().operations.length, 1); assert.equal(f.card().foreign, true);
    await f.api.undo('agent1', f.state, f.card(), 'foreign');
    await f.api.undo('agent1', f.state, f.card(), null); assert.equal(f.dialogs.length, 0);
});

test('unmatched/duplicate delta identities and failing GET are unavailable, not empty success', async () => {
    const f = setup(); const op = operation(); op.after[0].path = ['prompt']; f.ops([op]); f.end(); await flush();
    assert.equal(f.card().phase, 'error'); assert.match(f.card().notice, /不完整/);
    assert.doesNotMatch(f.api.html(f.card(), f.state), /data-completion-turn|data-completion-undo/);
    f.ctx.fetch = async () => response({detail:'gone'},404); f.end(); await flush();
    assert.equal(f.card().phase, 'error'); assert.match(f.card().notice, /不能据此判断本轮无改动/);
});

for (const race of ['project', 'host', 'epoch', 'timeline', 'deleted', 'cleanup']) test(`late ops GET cannot leak after ${race} changes`, async () => {
    const f = setup(), pending = deferred(); f.ctx.fetch = () => pending.promise; f.end();
    const card = f.card();
    if (race === 'project') f.ctx.canvasId = 'different';
    if (race === 'host') f.state.viewHost = 'codex';
    if (race === 'epoch') f.state.viewEpoch++;
    if (race === 'timeline') f.ctx.resetAgentTimeline(f.state);
    if (race === 'deleted') evaluate(f.ctx, `canvasState.nodes = canvasState.nodes.filter(n => n.id !== 'agent1')`);
    if (race === 'cleanup') f.ctx.cleanupAgentTuiState('agent1');
    pending.resolve(response({operations:[operation()]})); await flush();
    assert.equal(card.operations.length, 0); assert.equal(card.phase, 'loading'); assert.equal(f.reloads.length, 0);
});

for (const race of ['project', 'host', 'timeline', 'deleted']) test(`late undo POST result cannot refresh or write into a changed ${race}`, async () => {
    const f = setup(); f.end(); await flush(); const pending = deferred();
    f.ctx.fetch = (url, options) => { f.calls.push({url,options}); return pending.promise; };
    const card = f.card(), action = f.api.undo('agent1',f.state,card,'op1'); await flush();
    assert.equal(f.calls.filter(c=>c.options).length,1);
    if (race === 'project') f.ctx.canvasId = 'other';
    if (race === 'host') f.ctx.agentChangeHostView('agent1',f.state,'codex');
    if (race === 'timeline') f.ctx.resetAgentTimeline(f.state);
    if (race === 'deleted') evaluate(f.ctx, `canvasState.nodes = canvasState.nodes.filter(n => n.id !== 'agent1')`);
    pending.resolve(response({status:'reverted',reverted:['op1'],already_reverted:[],conflicts:[],irreversible_effects:[],error:null}));
    await action; assert.equal(f.reloads.length,0); assert.equal(card.notice,'');
});

test('host restoration retries a discarded in-flight GET rather than leaving a loading card forever', async () => {
    const f = setup(), pending = deferred(); f.ctx.fetch = () => pending.promise; f.end(); const card = f.card();
    f.ctx.agentChangeHostView('agent1',f.state,'codex');
    pending.resolve(response({operations:[operation('stale')]})); await flush();
    f.ctx.agentChangeHostView('agent1',f.state,'pi');
    f.ctx.fetch = async () => response({operations:[operation('fresh')]});
    const ui = sectionFixture(f,card); f.api.bind(ui.root,'agent1',f.state); await flush();
    assert.equal(card.phase,'ready'); assert.equal(card.operations[0].op_id,'fresh');
});

test('real activity painter receives newest operation mark and clears it after accepted undo requery', async () => {
    const f = setup(), surface = f.harness.makeElement(); surface.dataset.id='prompt1';
    f.ctx.document.createElementNS = (_namespace, tag) => f.harness.makeElement(tag);
    evaluate(f.ctx, `canvasState.nodes.push({id:'prompt1',type:'smart-prompt',text:'new'})`);
    f.ctx.canvasAgentActivity.configure({getProjectId:()=>f.ctx.canvasId,getNodes:()=>f.ctx.canvasState.nodes,
        getElements:()=>[surface],focusTargets:()=>{throw new Error('must not follow diff');}});
    f.ops([operation('newest'),operation('older')]); f.end(); await flush();
    const marker=surface.children[0].children[2]; assert.equal(marker.hidden,false);
    marker.dispatchEvent({type:'click',preventDefault(){},stopPropagation(){}});
    const detail=f.harness.dispatchedEvents.at(-1).detail;
    assert.deepEqual(fromContext(detail),{project_id:'project1',node_id:'prompt1',kind:'modified',op_id:'newest',turn_id:'logical1'});
    f.ops([operation('newest',undefined,{undone:true}),operation('older',undefined,{undone:true})]);
    await f.api.undo('agent1',f.state,f.card(),'newest'); assert.equal(marker.hidden,true);
});

test('a newer same-turn reload wins out-of-order responses', async () => {
    const f = setup(), a = deferred(), b = deferred(); let n = 0;
    f.ctx.fetch = () => (++n === 1 ? a.promise : b.promise);
    f.end(); f.end(); b.resolve(response({operations:[operation('new')]})); await flush();
    a.resolve(response({operations:[operation('old')]})); await flush();
    assert.equal(f.card().operations[0].op_id, 'new');
});

for (const scope of ['operation', 'turn']) test(`binder ${scope} undo sends exactly one identity plus project header; authoritative refresh precedes re-GET`, async () => {
    const f = setup(); f.end(); await flush(); f.calls.length = 0;
    const order = [];
    f.ctx.waitForCanvasRefresh = async project => { order.push('merge'); f.reloads.push(project); return true; };
    f.ctx.fetch = async (url, options) => {
        f.calls.push({url,options}); order.push(options ? 'POST' : 'GET');
        return options ? response({status:'reverted', reverted:['op1'], already_reverted:[], conflicts:[], irreversible_effects:[{effect:'paid <call>'}],error:null})
            : response({operations:[operation('op1',undefined,{undone:true})]});
    };
    const ui = sectionFixture(f); f.api.bind(ui.root,'agent1',f.state);
    ui.click(scope === 'turn' ? ui.turn : ui.op); await flush();
    assert.deepEqual(order, ['POST','merge','GET']);
    assert.deepEqual(JSON.parse(f.calls[0].options.body), scope === 'turn' ? {turn_id:'logical1'} : {op_id:'op1'});
    assert.equal(f.calls[0].options.headers['X-Agent-Project'], 'project1');
    assert.match(f.dialogs[0].message, /子操作|全部保留操作/); assert.match(f.dialogs[0].message, /不.*退费|不可.*退费/);
    assert.match(f.card().notice, /不退费/); assert.match(f.card().notice, /权威画布/);
    assert.equal(evaluate(f.ctx, `canvasState.nodes.find(n => n.id === 'user1').text`), 'user unrelated');
    assert.match(f.api.html(f.card(),f.state), /paid &lt;call&gt;/);
});

test('operation undo delegates the entire descendant tree with one identity and trusts only refreshed undone flags', async () => {
    const f = setup();
    const family = [operation('grandchild',[],{parent_op_id:'child'}),
        operation('child',[],{parent_op_id:'root'}),operation('root')];
    f.ops(family); f.end(); await flush();
    f.result(response({status:'reverted',reverted:['grandchild','child','root'],already_reverted:[],
        conflicts:[],irreversible_effects:[],error:null}));
    f.ctx.waitForCanvasRefresh = async () => {
        assert.ok(f.card().operations.every(op=>!op.undone),'POST success cannot fabricate local undone flags');
        f.ops(family.map(op=>({...op,undone:true})));
        return true;
    };
    await f.api.undo('agent1',f.state,f.card(),'root');
    const posts=f.calls.filter(call=>call.options);
    assert.equal(posts.length,1);
    assert.deepEqual(JSON.parse(posts[0].options.body),{op_id:'root'});
    assert.match(f.dialogs[0].message,/全部后代/);
    assert.deepEqual(fromContext(f.card().operations.map(op=>[op.op_id,op.undone])),
        [['grandchild',true],['child',true],['root',true]]);
    assert.equal((f.api.html(f.card(),f.state).match(/data-completion-undo="[^"]+" disabled/g)||[]).length,3);
});

test('pending generation rejection stays un-undone and manual log refresh can observe later settlement evidence', async () => {
    const f=setup(); f.end(); await flush();
    f.result(response({detail:{code:'CONFLICT',message:'生成仍在进行，撤销未执行。',
        details:{status:'generating',running:['op1'],reverted:[],already_reverted:[]}}},409));
    await f.api.undo('agent1',f.state,f.card(),null);
    assert.match(f.card().notice,/生成仍在进行/);
    assert.equal(f.card().operations[0].undone,false);
    assert.equal(f.reloads.length,0);
    f.ops([operation('settlement',[change('result1',['agentStatus'],present('generating'),present('done'))],
        {parent_op_id:'op1'}),operation()]);
    await f.api.load('agent1',f.state,f.card());
    assert.equal(f.card().operations.length,2);
    assert.match(f.api.html(f.card(),f.state),/生成状态节点 1/);
    assert.doesNotMatch(f.api.html(f.card(),f.state),/生成张数|失败数|生成 \d+ 张|失败 \d+|· 已撤销/);
});

for (const status of [409,404,500]) test(`HTTP ${status} never reports undo success, never refreshes/deletes canvas`, async () => {
    const f = setup(); f.end(); await flush();
    f.result(response({detail:{code:status === 409 ? 'UNDO_CONFLICT':'NOT_FOUND', message:'<conflict>', details:{conflicts:[{id:'prompt1',reason:'human edit'}]}}}, status));
    f.state.draft = 'human 中文 draft';
    f.state.inputFocus = {focused:true,start:3,end:6,direction:'forward'};
    f.state.scrollState = {atBottom:false,anchor:{key:'entry:1',offset:12},unread:false};
    const before = evaluate(f.ctx, 'JSON.stringify(canvasState.nodes)');
    const viewBefore = JSON.stringify([f.state.draft,f.state.inputFocus,f.state.scrollState]);
    await f.api.undo('agent1',f.state,f.card(),'op1');
    assert.match(f.card().notice, status === 409 ? /撤销未执行/ : status === 404 ? /日志已不可用/ : /未确认/);
    assert.doesNotMatch(f.card().notice,/已接受|已接收/); assert.equal(f.reloads.length,0);
    assert.equal(evaluate(f.ctx,'JSON.stringify(canvasState.nodes)'), before);
    assert.equal(JSON.stringify([f.state.draft,f.state.inputFocus,f.state.scrollState]),viewBefore);
    assert.equal(f.card().operations[0].undone,false);
    assert.doesNotMatch(f.api.html(f.card(),f.state), /<conflict>|· 已撤销/);
});

test('already_reverted and unconfirmed merge are honest; body-level conflicts are not success', async () => {
    const f = setup(); f.end(); await flush(); f.ctx.waitForCanvasRefresh = async () => false;
    f.result(response({status:'already_reverted',reverted:[],already_reverted:['op1'],conflicts:[],irreversible_effects:[],error:null}));
    await f.api.undo('agent1',f.state,f.card(),null);
    assert.match(f.card().notice, /此前已撤销/); assert.match(f.card().notice,/刷新未确认/);
    f.result(response({status:'reverted',error:null,conflicts:[{id:'conflict'}]}));
    await f.api.undo('agent1',f.state,f.card(),null); assert.match(f.card().notice,/未报告成功/);
});

test('readonly, IME, busy, replay and pending modal locks cannot open mutation paths', async () => {
    const f = setup(); f.end(); await flush();
    for (const [key,value] of [['viewHost','codex'],['composing',true],['status','busy'],['_applyingReplay',true],['_batchHistory',true]]) {
        const old = f.state[key]; f.state[key] = value;
        await f.api.undo('agent1',f.state,f.card(),'op1'); assert.equal(f.dialogs.length,0);
        assert.match(f.api.html(f.card(),f.state), /data-completion-undo="op1" disabled/);
        f.state[key] = old;
    }
    evaluate(f.ctx, `canvasState.nodes[0].agentCli='codex'`);
    await f.api.undo('agent1',f.state,f.card(),'op1'); assert.equal(f.dialogs.length,0);
    evaluate(f.ctx, `canvasState.nodes[0].agentCli='pi'`);
    const pending = deferred(); f.ctx.ffDialog.ask = () => pending.promise;
    const first = f.api.undo('agent1',f.state,f.card(),'op1');
    const posts = f.calls.filter(c => c.options).length;
    await f.api.undo('agent1',f.state,f.card(),null);
    f.state.viewEpoch++; pending.resolve(true); await first;
    assert.equal(f.calls.filter(c => c.options).length,posts);
});

test('real older-page reducer is GET-only, preserves live status/draft/reading and coalesces same-turn cards', async () => {
    const f = setup(); f.end(); await flush();
    f.state.status = 'busy'; f.state.draft = 'draft 中文'; f.state.scrollState = {atBottom:false, anchor:'entry:1', unread:false};
    f.state.historyCursor = 'cursor'; f.state.historyHasMore = true;
    f.ctx.fetch = async (url, options) => {
        f.calls.push({url,options});
        return response({events:[{seq:1,event:{type:'agent_end',host:'pi',turn_id:'logical0'}}, {seq:2,event:{type:'agent_end',host:'pi',turn_id:'logical1'}}],has_more:false,cursor:null});
    };
    f.ctx.loadOlderAgentEvents('agent1',f.state); await flush();
    assert.equal(f.state.messages.filter(m => m.completion?.turnId === 'logical1').length,1);
    assert.equal(f.state.messages.filter(m => m.completion?.turnId === 'logical0').length,1);
    assert.equal(f.state.status,'busy'); assert.equal(f.state.draft,'draft 中文'); assert.equal(f.state.scrollState.anchor,'entry:1');
    assert.equal(f.calls.filter(c => c.url.includes('/ops')).length,1);
    assert.ok(f.calls.every(c => !c.options)); assert.equal(f.reloads.length,0);
});

test('latest replay uses real agent_end.turn_id, without native turn invention or mutation', async () => {
    const f = setup(); f.state.historyLoaded = false;
    f.ctx.fetch = async (url,options) => {
        f.calls.push({url,options});
        return url.includes('/events/page') ? response({events:[{seq:1,event:{type:'user_prompt',host:'pi',text:'x',turn_id:'logical1'}},
            {seq:2,event:{type:'agent_end',host:'pi',turn_id:'logical1'}}, {seq:3,event:{type:'agent_end',host:'pi',turn_id:'logical1'}}],last_seq:3,has_more:false,cursor:null})
            : response({operations:[operation()]});
    };
    await f.ctx.replayAgentEvents('agent1',f.state); await flush();
    assert.equal(f.state.messages.filter(m => m.completion).length,1); assert.equal(f.card().phase,'ready');
    assert.ok(f.calls.every(c => !c.options)); assert.equal(f.reloads.length,0);
});

test('marker reveal verifies project/op/turn/actual target, expands record and scrolls TUI only', async () => {
    const f = setup(); f.end(); await flush(); const ui = sectionFixture(f);
    const detail = {project_id:'project1',node_id:'prompt1',op_id:'op1',turn_id:'logical1'};
    f.api.reveal({...detail,project_id:'other'}); f.api.bind(ui.root,'agent1',f.state); assert.equal(ui.body.scrollTop,10);
    f.api.reveal({...detail,node_id:'user1'}); assert.equal(f.card().expanded.size,0);
    for (const listener of f.harness.windowListeners.get('studio-canvas-agent-diff-reveal')) listener({detail});
    assert.ok(f.card().expanded.has('prompts')); f.api.bind(ui.root,'agent1',f.state); assert.equal(ui.body.scrollTop,60);
    assert.equal(f.calls.length,1); assert.equal(f.reloads.length,0);
    ui.group.open = true; ui.group.dispatchEvent({type:'toggle'});
    const before = JSON.stringify(f.api.fingerprint(f.card()));
    ui.group.open = false; ui.group.dispatchEvent({type:'toggle'});
    assert.equal(JSON.stringify(f.api.fingerprint(f.card())),before);
    f.state.composing=true; f.api.reveal(detail); f.api.bind(ui.root,'agent1',f.state);
    assert.equal(ui.body.scrollTop,60);
    f.state.composing=false; f.state.viewEpoch++; f.api.bind(ui.root,'agent1',f.state);
    assert.equal(ui.body.scrollTop,60,'a deferred reveal cannot scroll another host/timeline epoch');
});

test('shared real TUI renderer includes one completion row in float/dock and expansion does not create unread content', async () => {
    const f = setup(); f.state.draft='retained draft'; f.end(); await flush();
    const node=evaluate(f.ctx, `canvasState.nodes.find(n=>n.id==='agent1')`);
    const floating=f.ctx.agentNodeBodyHtml(node);
    assert.equal((floating.match(/class="agent-completion"/g)||[]).length,1);
    assert.match(floating,/retained draft/);
    const key=f.state.timeline.find(entry=>entry.message.completion).key;
    assert.ok(floating.includes(`data-agent-entry-key="${key}"`));
    f.state.scrollState={atBottom:false,unread:false,anchor:{key,offset:0}};
    f.card().expanded.add('prompts');
    const docked=f.ctx.agentNodeBodyHtml({...node,dock:'left'});
    assert.match(docked,/data-completion-group="prompts" open/);
    assert.equal(f.state.scrollState.unread,false); assert.equal(f.state.draft,'retained draft');
});

test('real TUI binder wires completion actions without a new socket, host switch, or history mutation', async () => {
    const f=setup(); f.end(); await flush(); const ui=sectionFixture(f);
    f.state.socket={readyState:1}; f.ctx.WebSocket={OPEN:1,CONNECTING:0};
    ui.root.querySelector=()=>null;
    ui.root.querySelectorAll=selector=>selector==='[data-agent-completion]'?[ui.section]:[];
    f.ctx.bindAgentNodeControls(ui.root,evaluate(f.ctx,`canvasState.nodes[0]`));
    ui.click(ui.op); await flush();
    assert.deepEqual(JSON.parse(f.calls.find(call=>call.options).options.body),{op_id:'op1'});
    assert.equal(f.state.viewHost,'pi'); assert.equal(f.state.messages.filter(m=>m.completion).length,1);
});

test('completion rendering honors real IME deferral and does not replace composer/scroll bindings', async () => {
    const f = setup(); f.end(); await flush();
    const el = f.harness.makeElement(), body = f.harness.makeElement(); body.innerHTML = 'original';
    el.querySelector = () => body; f.harness.queryResults['[data-id="agent1"]'] = [el];
    f.state.composing = true; f.state.draft = '中文草稿'; f.state.scrollState = {atBottom:false,anchor:'stable'};
    f.ctx.renderAgentNode('agent1');
    assert.equal(body.innerHTML,'original'); assert.equal(f.state.pendingRerender,true);
    assert.equal(f.state.draft,'中文草稿'); assert.equal(f.state.scrollState.anchor,'stable');
});

test('foreign event node/project/host IDs cannot associate this Agent card or issue requests', async () => {
    const f = setup();
    f.end('foreign',{node_id:'other'}); f.end('foreign',{project_id:'other'}); f.end('foreign',{host:'codex'});
    await flush(); assert.equal(f.state.messages.filter(m => m.completion).length,0); assert.equal(f.calls.length,0);
});

test('real authoritative reload uses existing merge receiver, reports rejection and guards project changes', async () => {
    const {ctx} = createCanvasContext();
    const source = readFileSync(path.join(JS_DIR,'smart-canvas.js'),'utf8');
    const start = source.indexOf('async function mergeReloadCanvasNow('), end = source.indexOf('\nwindow.refreshSmartCanvas',start);
    evaluate(ctx, source.slice(start,end));
    ctx.canvasId = 'p'; ctx.dragState = null; ctx.selectionState = null; ctx.pendingAgentMergeUndo = false;
    const merged = [], scheduled = [];
    ctx.applyMergedServerCanvas = (...args) => { merged.push(args); return true; };
    ctx.scheduleCanvasMergeReload = delay => scheduled.push(delay);
    ctx.fetch = async () => response({canvas:{id:'p',nodes:[{id:'server'}]}});
    assert.equal(await ctx.mergeReloadCanvasNow('p'),true); assert.equal(merged.length,1);
    assert.equal(await ctx.mergeReloadCanvasNow('wrong'),false); assert.equal(merged.length,1);
    const pending = deferred(); ctx.fetch = () => pending.promise;
    const reload = ctx.mergeReloadCanvasNow('p'); ctx.canvasId = 'q'; pending.resolve(response({canvas:{id:'p'}}));
    assert.equal(await reload,false); assert.equal(merged.length,1);
    ctx.dragState = {}; assert.equal(await ctx.mergeReloadCanvasNow('q'),false); assert.deepEqual(scheduled,[600]);
});
