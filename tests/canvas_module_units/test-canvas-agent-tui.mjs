// #199 轨道 4：canvas-agent-tui.js 行为单元测试（ADR 0020：按依赖面从小到大补测）。
//
// 合并说明：本文件合并同一任务的两个并行交付——commit 0ebab4c 的 41 用例（含 #220/#221/#222
// 三处产品缺陷的发现）+ 后续补组。原 41 用例与其 loader 桩预算原样保留，补组覆盖原头部
// 误判为「盖不住」的两项（bindAgentNodeControls 的 DOM 绑定与 Enter/Shift+Enter 分流：
// harness.makeElement 支持按实例覆写 querySelector/querySelectorAll，test-canvas-connections.mjs
// 的 wireWorldQueries 已证同法可行），以及 connect 回调、fork/switch-branch/分叉选择器、
// renderAgentNode 重渲染等原版本未覆盖的真实路径。
//
// canvas-agent-tui.js（898 行，#154 从 smart-canvas.js 抽离）是画布 Agent TUI 会话节点：
// WebSocket 连接与事件状态机、prompt/steer/follow_up/abort/confirm 发送（WS 与 HTTP 双路径）、
// model/autonomy/fork/branch 会话操作、卡片与节点体 HTML 渲染、agent-task 只读卡、Markdown 渲染。
// 本文件顶层只有声明（`const agentTuiState = new Map()` 是字面量），加载期不读其他模块状态。
//
// 依赖面实测（tools/canvas_coupling_report.py:coupling()，口径=引用 hub 声明的名字）：
// **9 个外部自由名 / 12 处引用**（12 total / 9 distinct / free_refs 12 / interface 0），
// 是所有未补测模块里最小的（对比已补好的 canvas-node-ops.js 17 处/14 名、
// canvas-connections.js 12 处/9 名）。其中：
//   - 由**加载真实模块**满足（不桩）：
//       canvas-core.js  → tr / nodeScale / escapeHtml / escapeAttr / canvas(let) / canvasCore(Proxy)
//       canvas-node-types.js → NODE_TYPE_AGENT / NODE_TYPE_TASK
//       canvas-state.js → canvasState
//   - 9 个手写桩（全部来自 hub smart-canvas.js，按真实契约最小化）：
//       trf → 值传递记录器（返回值 = 真实 tr(key) + 被代入值；无 StudioI18n 时 smart-canvas.js:
//             314-316 的逐字 trf 返回 key 本身、代入值被丢弃，值传递路径不可观察，故刻意不用
//             逐字镜像——也不重新实现模板替换，零产品逻辑）；
//       chatApiProviders / providerChatModels → 数据驱动（测试直接喂数组）；
//       canvasId / sourceProjectId → 数据字段；
//       imageLayout / nodeBodyHtml / render / centerViewportOnWorldPoint → 零逻辑记录器
//       （只记录「被调了几次、参数是什么」，不返回布局结构，也不参与任何断言的计算）。
//   - 浏览器表面积（harness 不提供、按实例补，零产品逻辑）：
//       WebSocket → 记录器类 + OPEN 常量（真实契约只有 readyState/send/close/onmessage…）；
//       fetch → 记录器（harness.mjs:209 默认 fetch 直接抛，测试内按实例覆盖）；
//       prompt → 返回预置答案的记录器。
// ADR 0020 判据「桩要开始重新实现产品逻辑就停」：**未命中**——9 个桩里 0 个复制产品逻辑。
//
// 防的回归（全部断言可观察输出/副作用，不是「不抛错就算过」）：
//   - 谓词走真 NODE_TYPE_* 常量（不是字符串字面量）；
//   - handleAgentEvent 的完整状态机：status 迁移矩阵、turn_end 的 content 过滤/拼接、
//     message_update 的 streaming 续写 vs 新起、thinking_delta 独立于 messages、
//     tool_execution_* 的 running→done/error 与 result/isError、auto_retry_end 失败时
//     推入系统消息、process_exited→crashed、confirm→waiting_confirm、未知 type 不动状态；
//   - 每次事件都调 renderAgentNode（渲染不漏）；
//   - sendAgentPrompt / sendAgentConfirm 的 WS 与 HTTP 双路径载荷形状；
//   - steer/follow_up/abort 的 wsPayload 与系统消息、abort 把 status 复位 idle；
//   - setAgentModel / setAgentAutonomy 的乐观更新（state + node 两处都要写）；
//   - connectAgentWebSocket 回调面：onmessage 派发到真实 handleAgentEvent、畸形 JSON 走
//     console.error 不外抛、onerror→error、onclose→disconnected+socket=null、构造器抛错
//     →null、https location 切 wss；
//   - sendAgentSteer / sendAgentFollowUp 的 HTTP 回退（WS 未连：URL/body 精确；onSuccess 不
//     执行——#221 缺口，断言钉现状）；
//   - forkAgentSession / switchAgentBranch：成功写回 node.branches/current_branch 且清空
//     messages/toolActivities、失败零副作用；showForkPicker 的 prompt 选择→fork(entry_id,
//     name)、空列表/越界/空白选择不分叉；
//   - renderAgentNode：nodeBodyHtml 输出进 .node-body innerHTML、卡片状态进 .node-head-meta、
//     重绑定在旧 socket 非 OPEN 时新建；el/node 缺失与 layout=null → no-op；
//   - bindAgentNodeControls：Enter 发送（trim/清空、busy→follow_up、Shift+Enter/纯空白
//     no-op）、thinking 切换、steer/follow-up/abort、confirm 批准/取消、model/autonomy/
//     branch change、fork 按钮、工具行展开；
//   - bindAgentTaskNodeControls：结果链接点击选中结果节点 + centerViewportOnWorldPoint
//     （结果节点 + (120,100)）+ render、结果缺失不改选中；
//   - cleanupAgentTuiState 关 socket 且删条目（#217 同族：节点删除后的清理路径；删节点联动
//     已由 test-canvas-node-ops.mjs 的「deleteNode: agent 节点经真实 cleanupAgentTuiState
//     清 TUI 状态」覆盖）；
//   - agentCardStatusHtml 的预览截断（40 字符 + 省略号）与 toolActivities 回退；
//   - agentNodeBodyHtml 的 retrying/crashed 映射、HTML 转义、按钮 disabled 门、
//     model/autonomy/branch select 的 selected 判定；
//   - renderMarkdown 的 XSS 门：非 https/http/mailto///#/./ 协议必须降级为 '#'。
//
// 明确判为「单测盖不住」的行为（不写假测试）：
//   - WebSocket 真实服务端行为：握手/断线重连/服务端事件顺序（onmessage JSON.parse 异常
//     分支只覆盖到「解析失败不外抛」，真实网络时序靠浏览器 runner）；
//   - showForkPicker 的完整 UI 选择器：真实实现用 window.prompt（源码注释明示 "In a full
//     implementation, this would be a UI picker"）；prompt 选择流程本文件已覆盖，真实 UI
//     交互靠浏览器 runner；
//   - fork/switch-branch/model/autonomy/prompt 等 HTTP 端点的真实网络往返（fetch 是
//     数据驱动桩，只验证请求形态与本地状态变化）；
//   - 渲染出的 HTML 在浏览器里的实际布局/CSS（只断言字符串包含关系）。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

// WebSocket 记录器：真实契约只有 readyState / send / close 和几个 handler 槽位。
// OPEN 必须是 1（模块里比较的是 WebSocket.OPEN，不是数字字面量）。
function makeWebSocketHarness(ctx, { open = true } = {}) {
    const sent = [];
    const closed = [];
    class FakeSocket {
        constructor(url) {
            this.url = url;
            this.readyState = open ? 1 : 3;
            this.sent = [];
        }
        send(text) {
            this.sent.push(text);
            sent.push(text);
        }
        close() {
            closed.push(this.url);
        }
    }
    FakeSocket.OPEN = 1;
    ctx.WebSocket = FakeSocket;
    return { FakeSocket, sent, closed };
}

// fetch 记录器：harness.mjs:209 默认 fetch 会抛，测试按实例覆盖。
// response 三种形态：单个响应对象（所有调用同一响应）/ 数组（响应队列，shift，
// 空队列回退 ok/{}）/ 函数 (url, opts) => 响应（按 URL 分支）。
function makeFetchHarness(ctx, response) {
    const calls = [];
    const DEFAULT = { ok: true, json: async () => ({}) };
    const next = (url, opts) => {
        if (typeof response === 'function') return response(url, opts);
        if (Array.isArray(response)) return response.length > 0 ? response.shift() : DEFAULT;
        return response || DEFAULT;
    };
    ctx.fetch = async (url, opts = {}) => {
        calls.push({ url, opts });
        return next(url, opts);
    };
    return calls;
}

function loadAgentTuiModule({
    nodes = [],
    open = true,
    canvasData = null,
    chatProviders = [],
    modelsByProvider = {},
    location = { protocol: 'http:', host: 'example.test' },
    fetchResponse = undefined,
    layout = { x: 0, y: 0, width: 10, height: 10 },
} = {}) {
    const { ctx, harness } = createCanvasContext();
    // 注意：harness.mjs:180 设 `ctx.window = ctx`（浏览器里 window === 全局对象）。
    // 绝不能替换 ctx.window——canvas-state.js 的 `window.canvasState = {...}` 会写到
    // 另一个对象上，vm 全局里就拿不到 canvasState。
    ctx.location.protocol = location.protocol;
    ctx.location.host = location.host;
    // 不设 StudioI18n：走 canvas-core.js:64 的真实回退分支（tr 返回 key 本身）。
    const ws = makeWebSocketHarness(ctx, { open });
    const fetchCalls = makeFetchHarness(ctx, fetchResponse);
    const renders = [];
    const bodyHtmlCalls = [];
    const promptReturns = [];
    const promptLabels = [];
    const consoleErrors = [];

    // 9 个 hub 自由名（见文件头桩预算）。
    // trf 是零逻辑记录器：把翻译 key 和被代入的值都拼进返回值，让测试能断言
    // 「调用方确实把 kind 传进去了」，不复制 smart-canvas.js:314-316 的模板替换逻辑。
    ctx.trf = (key, values = {}) =>
        ctx.tr(key) + (Object.keys(values).length ? `\u00b7${Object.values(values).join('\u00b7')}` : '');
    ctx.chatApiProviders = () => chatProviders;
    ctx.providerChatModels = providerId => modelsByProvider[providerId] || [];
    ctx.canvasId = 'canvas-id-1';
    ctx.sourceProjectId = 'source-project-1';
    ctx.imageLayout = (imgs, scale, node) => {
        renders.push({ kind: 'imageLayout', nodeId: node?.id, images: imgs.length, scale });
        return layout;
    };
    ctx.nodeBodyHtml = (node, layout) => {
        bodyHtmlCalls.push({ nodeId: node?.id, hasLayout: Boolean(layout) });
        return '<div class="node-body-stub"/>';
    };
    ctx.render = () => renders.push({ kind: 'render' });
    ctx.centerViewportOnWorldPoint = (point) => renders.push({ kind: 'centerViewport', point });
    // prompt 记录器：返回值按队列弹出，队列空回退 null（与浏览器取消选择一致）。
    ctx.prompt = (label) => {
        promptLabels.push(label);
        return promptReturns.length > 0 ? promptReturns.shift() : null;
    };
    // console 记录器：让 console.error 分支可断言，同时避免测试输出噪音。
    ctx.console = {
        error: (...args) => { consoleErrors.push(args.map(String).join(' ')); },
        log: () => {},
    };

    loadModules(ctx, [
        'canvas-node-types.js',
        'canvas-state.js',
        'canvas-core.js',
        'canvas-agent-tui.js',
    ]);
    fromContext(evaluate(ctx, `canvas = ${JSON.stringify(canvasData ?? { project_id: 'proj-9' })}`));
    for (const node of nodes) {
        ctx.canvasState.nodes.push(node);
    }
    return { ctx, harness, ws, fetchCalls, renders, bodyHtmlCalls, promptReturns, promptLabels, consoleErrors };
}

const fn = (ctx, name) => globalFunction(ctx, name);

// ── 谓词：必须走真 NODE_TYPE_* 常量 ────────────────────────────────────
test('谓词按 NODE_TYPE_* 常量判定，不是字符串字面量', t => {
    const { ctx } = loadAgentTuiModule();
    const agentType = fromContext(evaluate(ctx, 'NODE_TYPE_AGENT'));
    const taskType = fromContext(evaluate(ctx, 'NODE_TYPE_TASK'));
    assert.notEqual(agentType, taskType);

    const isAgent = fn(ctx, 'isSmartAgentNode');
    const isTask = fn(ctx, 'isSmartAgentTaskNode');
    assert.equal(isAgent({ type: agentType }), true);
    assert.equal(isAgent({ type: taskType }), false);
    assert.equal(isTask({ type: taskType }), true);
    assert.equal(isTask({ type: agentType }), false);
    // 缺失/非对象输入不能抛。
    assert.equal(isAgent(undefined), false);
    assert.equal(isTask(null), false);
});

// ── 状态容器 ──────────────────────────────────────────────────────────
test('getAgentTuiState 首次建默认形状，二次返回同一对象', t => {
    const { ctx } = loadAgentTuiModule();
    const get = fn(ctx, 'getAgentTuiState');
    const first = get('n1');
    assert.deepEqual(fromContext({
        status: first.status,
        model: first.model,
        autonomy: first.autonomy,
        messages: first.messages,
        socket: first.socket,
    }), { status: 'idle', model: '', autonomy: 'cautious', messages: [], socket: null });
    first.status = 'busy';
    const second = get('n1');
    assert.equal(fromContext(second.status), 'busy', '必须复用同一 Map 条目，不能重建');
    assert.equal(fromContext(evaluate(ctx, 'agentTuiState.size')), 1);
});

// ── handleAgentEvent 状态机 ───────────────────────────────────────────
function eventCase(event, expect) {
    test(`handleAgentEvent: ${event.type}`, () => {
        const { ctx } = loadAgentTuiModule();
        const handle = fn(ctx, 'handleAgentEvent');
        handle('n1', event);
        const state = fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
        assert.equal(state.status, expect.status);
        if (expect.messages) assert.deepEqual(state.messages, expect.messages);
    });
}

for (const [type, status] of [
    ['session', 'idle'],
    ['agent_start', 'busy'],
    ['agent_end', 'idle'],
    ['turn_start', 'busy'],
    ['auto_retry_start', 'retrying'],
    ['process_exited', 'crashed'],
    ['response', 'idle'],
    ['totally-unknown-type', 'idle'],
]) {
    eventCase({ type }, { status });
}

test('handleAgentEvent: turn_end 只取 content 里的 text 段并按序拼接，空文本不推消息', () => {
    const { ctx } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    handle('n1', {
        type: 'turn_end',
        message: { content: [
            { type: 'text', text: 'A' },
            { type: 'tool_use', text: 'ignored' },
            { type: 'text', text: 'B' },
            null,
        ] },
    });
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages')), [
        { role: 'assistant', text: 'AB' },
    ]);

    handle('n2', { type: 'turn_end', message: { content: [{ type: 'text', text: '' }] } });
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n2").messages')), []);
    handle('n3', { type: 'turn_end' });
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n3").messages')), []);
});

test('handleAgentEvent: message_update 文本增量续写 streaming 消息，非 streaming 时新起一条', () => {
    const { ctx } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'He' } });
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'llo' } });
    let messages = fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages'));
    assert.deepEqual(messages, [{ role: 'assistant', text: 'Hello', streaming: true }]);

    // 实测行为：turn_end **不会**把之前 streaming 的消息收尾（见 #222），
    // 所以空 content 的 turn_end 后紧接的增量仍追到同一条。这里钉当前行为，
    // 不把它当“应然”——收尾缺失已单独开 issue。
    handle('n1', { type: 'turn_end', message: { content: [] } });
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'Next' } });
    messages = fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages'));
    assert.equal(messages.length, 1);
    assert.deepEqual(messages[0], { role: 'assistant', text: 'HelloNext', streaming: true });

    // 非 streaming 末尾时必须是新起一条（用 turn_end 带 content 做对照）。
    handle('n2', { type: 'turn_end', message: { content: [{ type: 'text', text: 'done' }] } });
    handle('n2', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'New' } });
    messages = fromContext(evaluate(ctx, 'getAgentTuiState("n2").messages'));
    assert.deepEqual(messages, [
        { role: 'assistant', text: 'done' },
        { role: 'assistant', text: 'New', streaming: true },
    ]);
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'other' } });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages').length), 1,
        '不认识的 assistantMessageEvent.type 不能改动消息列表');
});

test('handleAgentEvent: thinking_delta 存进 state.thinking，不污染 messages', () => {
    const { ctx } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'thinking_delta', delta: 'th' } });
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'thinking_delta', delta: 'ink' } });
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").thinking')), [
        { text: 'think', streaming: true },
    ]);
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages')), []);
});

test('handleAgentEvent: 工具活动 running→done/error，update 只挂到已存在的 call id', () => {
    const { ctx } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    handle('n1', { type: 'tool_execution_start', toolName: 'write', toolCallId: 'c1', args: { p: 1 } });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'busy');
    handle('n1', { type: 'tool_execution_update', toolCallId: 'c1', update: { pct: 50 } });
    handle('n1', { type: 'tool_execution_update', toolCallId: 'missing', update: { pct: 1 } });
    handle('n1', { type: 'tool_execution_end', toolCallId: 'c1', result: 'ok', isError: false });
    assert.deepEqual(fromContext(evaluate(ctx, 'Array.from(agentTuiState.get("n1").toolActivities.values())')), [
        { name: 'write', status: 'done', args: { p: 1 }, result: 'ok', isError: false, update: { pct: 50 } },
    ]);
    assert.equal(fromContext(evaluate(ctx, 'agentTuiState.get("n1").toolActivities.size')), 1,
        '未知 toolCallId 的 update 不能凭空建条目');

    handle('n2', { type: 'tool_execution_start', toolName: 'rm', toolCallId: 'c2' });
    handle('n2', { type: 'tool_execution_end', toolCallId: 'c2', result: 'boom', isError: true });
    assert.deepEqual(fromContext(evaluate(ctx, 'agentTuiState.get("n2").toolActivities.get("c2")')),
        { name: 'rm', status: 'error', isError: true, result: 'boom' });
});

test('handleAgentEvent: auto_retry_end 失败时置 error 并推系统消息，成功时回 busy', () => {
    const { ctx } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    handle('n1', { type: 'auto_retry_end', success: true });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'busy');
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages')), []);

    handle('n2', { type: 'auto_retry_end', success: false });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n2").status')), 'error');
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n2").messages').length), 1);
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n2").messages[0].role')), 'system');
});

test('handleAgentEvent: confirm 事件保存载荷并置 waiting_confirm；每次事件都走 renderAgentNode', () => {
    const { ctx } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    const calls = [];
    ctx.renderAgentNode = nodeId => calls.push(nodeId);
    handle('n1', { type: 'confirm', confirm_id: 'k1', message: '要删吗' });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'waiting_confirm');
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").confirm')), {
        type: 'confirm', confirm_id: 'k1', message: '要删吗',
    });
    handle('n1', { type: 'agent_end' });
    assert.deepEqual(calls, ['n1', 'n1'], '事件处理必须触发节点重渲染');
});

// ── 发送：WS / HTTP 双路径 ────────────────────────────────────────────
test('connectAgentWebSocket 拼出项目+节点 id 编码后的 ws URL，并置 connecting', () => {
    const { ctx, ws } = loadAgentTuiModule({ open: false });
    const connect = fn(ctx, 'connectAgentWebSocket');
    const socket = connect('node/1', 'proj/2');
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("node/1").status')), 'connecting');
    assert.equal(fromContext(socket.url),
        'ws://example.test/api/projects/proj%2F2/agent-nodes/node%2F1/stream');
    // 已连接时复用，不再新建。
    const openWs = makeWebSocketHarness(ctx, { open: true });
    evaluate(ctx, 'getAgentTuiState("node/1").socket = new WebSocket("already")');
    const reused = connect('node/1', 'proj/2');
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("node/1").socket.url')), 'already');
});

test('connectAgentWebSocket: 已 OPEN 的 socket 直接复用，不新建连接', () => {
    const { ctx, ws } = loadAgentTuiModule({ open: true });
    const connect = fn(ctx, 'connectAgentWebSocket');
    const first = connect('n1', 'p1');
    const second = connect('n1', 'p1');
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").socket.url')), fromContext(first.url));
    assert.equal(fromContext(second.url), fromContext(first.url), '同一次连接不能建两条 socket');
});

// ── connectAgentWebSocket：回调面与协议 ────────────────────────────────
test('connectAgentWebSocket: https location 切 wss://', () => {
    const { ctx } = loadAgentTuiModule({ open: false, location: { protocol: 'https:', host: 'canvas.example' } });
    const socket = fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    assert.equal(socket.url, 'wss://canvas.example/api/projects/p1/agent-nodes/n1/stream');
});

test('connectAgentWebSocket: onmessage 派发到真实 handleAgentEvent；畸形 JSON 不外抛且走 console.error', () => {
    const { ctx, consoleErrors } = loadAgentTuiModule({ open: true });
    const socket = fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    socket.onmessage({ data: JSON.stringify({ type: 'agent_start' }) });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'busy',
        'onmessage → 真实 handleAgentEvent 状态迁移');
    socket.onmessage({ data: 'not-json' });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'busy', '畸形 JSON 不改状态');
    assert.ok(consoleErrors.some(m => m.startsWith('Failed to parse agent event:')),
        '解析失败走 console.error，不是未捕获异常');
});

test('connectAgentWebSocket: onerror → error；onclose → disconnected 且 socket 复位 null', () => {
    const { ctx } = loadAgentTuiModule({ open: true });
    const socket = fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    socket.onerror();
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'error');
    socket.onclose();
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'disconnected');
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").socket')), null,
        '断开后 socket 必须复位 null，否则下次 connect 被陈旧实例挡住');
});

test('connectAgentWebSocket: 构造器抛错 → 返回 null 且状态无残留', () => {
    const { ctx, consoleErrors } = loadAgentTuiModule({ open: false });
    class Boom { constructor() { throw new Error('boom'); } }
    ctx.WebSocket = Boom;
    const r = fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    assert.equal(r, null);
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").socket')), null);
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'idle',
        '构造失败路径不能置成 connecting');
    assert.ok(consoleErrors.some(m => m.startsWith('Failed to connect agent WebSocket:')));
});

test('sendAgentPrompt: WS 打开走 socket.send（载荷含 images），关闭走 HTTP POST，两条路径都推 user 消息并置 busy', () => {
    const open = loadAgentTuiModule({ open: true });
    const sendPrompt = fn(open.ctx, 'sendAgentPrompt');
    fn(open.ctx, 'connectAgentWebSocket')('n1', 'p1');
    sendPrompt('n1', 'p1', '  画一只猫  ', ['img/1']);
    assert.deepEqual(JSON.parse(fromContext(open.ws.sent[0])), {
        type: 'prompt', message: '  画一只猫  ', images: ['img/1'],
    });
    assert.equal(open.fetchCalls.length, 0, 'WS 可用时不该再走 HTTP');
    assert.deepEqual(fromContext(evaluate(open.ctx, 'getAgentTuiState("n1").messages')), [
        { role: 'user', text: '  画一只猫  ' },
    ]);
    assert.equal(fromContext(evaluate(open.ctx, 'getAgentTuiState("n1").status')), 'busy');

    const closed = loadAgentTuiModule({ open: false });
    fn(closed.ctx, 'sendAgentPrompt')('n1', 'proj/2', '文本', []);
    assert.equal(closed.fetchCalls.length, 1);
    assert.equal(fromContext(closed.fetchCalls[0].url),
        '/api/projects/proj%2F2/agent-nodes/n1/prompt');
    assert.equal(fromContext(closed.fetchCalls[0].opts.method), 'POST');
    assert.deepEqual(JSON.parse(fromContext(closed.fetchCalls[0].opts.body)), { message: '文本', images: [] });
});

test('sendAgentSteer / FollowUp / Abort: wsPayload 形状、系统消息、abort 复位 idle', () => {
    const { ctx, ws } = loadAgentTuiModule({ open: true });
    fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    evaluate(ctx, 'getAgentTuiState("n1").status = "busy"');
    fn(ctx, 'sendAgentSteer')('n1', 'p1', '转向');
    fn(ctx, 'sendAgentFollowUp')('n1', 'p1', '排队');
    fn(ctx, 'sendAgentAbort')('n1', 'p1');
    assert.deepEqual(ws.sent.map(raw => JSON.parse(fromContext(raw))), [
        { type: 'steer', message: '转向' },
        { type: 'follow_up', message: '排队' },
        { type: 'abort' },
    ]);
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages')), [
        { role: 'system', text: '↪ steer: 转向' },
        { role: 'system', text: '⏳ follow_up queued: 排队' },
        { role: 'system', text: '⏹ aborted' },
    ]);
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'idle',
        'abort 必须把 status 复位，否则按钮永远停在运行中');
});

test('sendAgentCommand: WS 未连接时只发 HTTP 请求（不碰本地状态——缺口见 #221）', () => {
    const { ctx, ws, fetchCalls } = loadAgentTuiModule({ open: false });
    evaluate(ctx, 'getAgentTuiState("n1").status = "busy"');
    fn(ctx, 'sendAgentCommand')('n1', 'proj/2', 'steer', {
        body: { message: 'm' },
        wsPayload: { type: 'steer', message: 'm' },
    });
    assert.equal(ws.sent.length, 0);
    assert.equal(fetchCalls.length, 1);
    assert.equal(fromContext(fetchCalls[0].url), '/api/projects/proj%2F2/agent-nodes/n1/steer');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls[0].opts.body)), { message: 'm' });
    // body 未提供时不带 body 字段（abort 走这条）。
    fn(ctx, 'sendAgentCommand')('n1', 'p1', 'abort', { wsPayload: { type: 'abort' } });
    // 不能用 fromContext：harness.mjs:287 是 JSON 往返，对 undefined 会抛。
    assert.equal(fetchCalls[1].opts.body, undefined);
});

test('sendAgentSteer / sendAgentFollowUp: WS 未连接回退 HTTP（URL 与 body），onSuccess 不执行', () => {
    const { ctx, ws, fetchCalls } = loadAgentTuiModule({ open: false });
    evaluate(ctx, 'getAgentTuiState("n1").status = "busy"');
    fn(ctx, 'sendAgentSteer')('n1', 'proj/2', '往左');
    fn(ctx, 'sendAgentFollowUp')('n1', 'p1', '下一步');
    assert.equal(ws.sent.length, 0, 'ws 不可用，什么都不走 socket');
    assert.equal(fetchCalls.length, 2);
    assert.equal(fromContext(fetchCalls[0].url), '/api/projects/proj%2F2/agent-nodes/n1/steer');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls[0].opts.body)), { message: '往左' });
    assert.equal(fromContext(fetchCalls[1].url), '/api/projects/p1/agent-nodes/n1/follow_up');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls[1].opts.body)), { message: '下一步' });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages.length')), 0,
        '#221：HTTP 路径不执行 onSuccess（系统消息不回写本地，钉现状，待修）');
});

test('sendAgentConfirm: 无 confirm 时完全不动作；有 confirm 时清标记、置 busy、带 confirm_id', () => {
    const { ctx, ws, fetchCalls } = loadAgentTuiModule({ open: true });
    const confirm = fn(ctx, 'sendAgentConfirm');
    confirm('n1', 'p1', true);
    assert.equal(ws.sent.length, 0);
    assert.equal(fetchCalls.length, 0);
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'idle');

    fn(ctx, 'connectAgentWebSocket')('n2', 'p1');
    evaluate(ctx, 'getAgentTuiState("n2").confirm = { confirm_id: "k9", message: "删?" }');
    confirm('n2', 'p1', true);
    assert.deepEqual(JSON.parse(fromContext(ws.sent[0])), { type: 'confirm', confirm_id: 'k9', approved: true });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n2").confirm')), null);
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n2").status')), 'busy');
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n2").messages')), [
        { role: 'system', text: '✅ 已批准' },
    ]);

    evaluate(ctx, 'getAgentTuiState("n3").confirm = { confirm_id: "k8" }');
    confirm('n3', 'p1', false);
    assert.equal(fetchCalls.length, 1, '未连接的 n3 必须回退到 HTTP');
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n3").messages')), [
        { role: 'system', text: '❌ 已取消' },
    ]);
});

test('sendAgentConfirm: WS 断开时走 HTTP，载荷仍带 confirm_id/approved', () => {
    const { ctx, fetchCalls } = loadAgentTuiModule({ open: false });
    evaluate(ctx, 'getAgentTuiState("n1").confirm = { confirm_id: "k1" }');
    fn(ctx, 'sendAgentConfirm')('n1', 'proj/2', false);
    assert.equal(fetchCalls.length, 1);
    assert.equal(fromContext(fetchCalls[0].url), '/api/projects/proj%2F2/agent-nodes/n1/confirm');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls[0].opts.body)), { confirm_id: 'k1', approved: false });
});

// ── 乐观更新：state 与 node 两处都要写 ───────────────────────────────
test('setAgentModel / setAgentAutonomy 同时更新 state 和节点（渲染以节点为准）', () => {
    const { ctx, fetchCalls } = loadAgentTuiModule({ nodes: [{ id: 'n1', type: 'agent' }] });
    fn(ctx, 'setAgentModel')('n1', 'proj/2', 'gpt-x');
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").model')), 'gpt-x');
    assert.equal(fromContext(evaluate(ctx, 'canvasState.nodes[0].model')), 'gpt-x',
        '节点是渲染的真值来源，漏写会导致下拉框回弹');
    // 实测：模型/自治端点**不做** encodeURIComponent（与 prompt/confirm/abort 端点不一致）。
    assert.equal(fromContext(fetchCalls[0].url), '/api/projects/proj/2/agent-nodes/n1/model');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls[0].opts.body)), { model: 'gpt-x' });

    fn(ctx, 'setAgentAutonomy')('n1', 'p1', 'autonomous');
    assert.equal(fromContext(evaluate(ctx, 'canvasState.nodes[0].autonomy')), 'autonomous');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls[1].opts.body)), { autonomy: 'autonomous' });

    // 节点不存在时不能抛（state 仍更新）。
    fn(ctx, 'setAgentModel')('ghost', 'p1', 'gpt-y');
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("ghost").model')), 'gpt-y');
});

// ── 清理路径（#217 同族：节点删除后必须清干净）─────────────────────────
test('cleanupAgentTuiState 关闭 socket 并删除条目；不存在的 id 不抛', () => {
    const { ctx, ws } = loadAgentTuiModule({ open: true });
    fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    assert.equal(fromContext(evaluate(ctx, 'agentTuiState.size')), 1);
    fn(ctx, 'cleanupAgentTuiState')('n1');
    assert.deepEqual(fromContext(ws.closed), ['ws://example.test/api/projects/p1/agent-nodes/n1/stream']);
    assert.equal(fromContext(evaluate(ctx, 'agentTuiState.size')), 0,
        '条目必须删除，否则重新添加同 id 节点会复用已关闭的会话');
    fn(ctx, 'cleanupAgentTuiState')('nope');
    assert.equal(fromContext(evaluate(ctx, 'agentTuiState.size')), 0);
});

test('cleanupAgentTuiState: socket.close() 抛错也不能中断删除', () => {
    const { ctx } = loadAgentTuiModule({ open: true });
    fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    evaluate(ctx, 'getAgentTuiState("n1").socket.close = () => { throw new Error("boom") }');
    fn(ctx, 'cleanupAgentTuiState')('n1');
    assert.equal(fromContext(evaluate(ctx, 'agentTuiState.size')), 0);
});

// ── 卡片状态行 ───────────────────────────────────────────────────────
test('agentCardStatusHtml: 无会话时给空闲；四种状态映射到各自 class/文案', () => {
    const { ctx } = loadAgentTuiModule();
    const card = fn(ctx, 'agentCardStatusHtml');
    assert.equal(fromContext(card('missing')),
        '<span class="agent-card-status agent-tui-status-idle">空闲</span>');
    for (const [status, cls, text] of [
        ['busy', 'agent-tui-status-busy', '运行中'],
        ['connecting', 'agent-tui-status-connecting', '连接中'],
        ['error', 'agent-tui-status-error', '错误'],
        ['disconnected', 'agent-tui-status-disconnected', '已断开'],
    ]) {
        evaluate(ctx, `getAgentTuiState("n-${status}").status = ${JSON.stringify(status)}`);
        const html = fromContext(card(`n-${status}`));
        assert.ok(html.includes(`class="agent-card-status ${cls}"`), html);
        assert.ok(html.includes(`>${text}<`), html);
    }
});

test('agentCardStatusHtml: 预览取最后一条消息前 40 字符加省略号，并做 HTML 转义', () => {
    const { ctx } = loadAgentTuiModule();
    const card = fn(ctx, 'agentCardStatusHtml');
    evaluate(ctx, 'getAgentTuiState("n1").messages.push({role:"user", text:"<b>" + "x".repeat(45) + "</b>"})');
    const html = fromContext(card('n1'));
    assert.ok(html.includes('agent-card-preview'), html);
    assert.ok(!html.includes('<b>'), '预览必须转义，不能把用户文本当 HTML 插入');
    assert.ok(html.includes('…'), html);
    assert.equal((fromContext(evaluate(ctx, '(getAgentTuiState("n1").messages[0].text.slice(0,40))'))).length, 40);
});

test('agentCardStatusHtml: 没有消息时回退到最后一条工具活动 name(status)', () => {
    const { ctx } = loadAgentTuiModule();
    const card = fn(ctx, 'agentCardStatusHtml');
    evaluate(ctx, `getAgentTuiState("n1").toolActivities.set("c1", {name:"write", status:"running"})`);
    const html = fromContext(card('n1'));
    assert.ok(html.includes('write(running)'), html);
});

test('agentCardStatusHtml: confirm 挂起时覆盖成「等待确认」', () => {
    const { ctx } = loadAgentTuiModule();
    evaluate(ctx, 'getAgentTuiState("n1").status = "idle"; getAgentTuiState("n1").confirm = {confirm_id:"k"}');
    const html = fromContext(fn(ctx, 'agentCardStatusHtml')('n1'));
    assert.ok(html.includes('>等待确认<'), html);
    assert.ok(html.includes('agent-tui-status-busy'), html);
});

// ── 节点体渲染 ───────────────────────────────────────────────────────
test('agentNodeBodyHtml: retrying→重试中、crashed→已崩溃（卡片行没有这两个映射，见 #220）', () => {
    const { ctx } = loadAgentTuiModule();
    const body = fn(ctx, 'agentNodeBodyHtml');
    for (const [status, text] of [['retrying', '重试中'], ['crashed', '已崩溃']]) {
        evaluate(ctx, `getAgentTuiState("n-${status}").status = ${JSON.stringify(status)}`);
        const html = fromContext(body({ id: `n-${status}`, type: 'agent' }));
        assert.ok(html.includes(`>${text}<`), `${status} → ${text} 未出现: ${html}`);
    }
});

test('agentNodeBodyHtml: 用户/系统消息走转义、assistant 走 renderMarkdown', () => {
    const { ctx } = loadAgentTuiModule();
    evaluate(ctx, `
        const s = getAgentTuiState("n1");
        s.messages.push({role:"user", text:"<script>alert(1)</script>"});
        s.messages.push({role:"system", text:"**bold-system**"});
        s.messages.push({role:"assistant", text:"**bold**"});
    `);
    const html = fromContext(fn(ctx, 'agentNodeBodyHtml')({ id: 'n1', type: 'agent' }));
    assert.ok(html.includes('&lt;script&gt;'), '用户文本必须转义');
    assert.ok(!html.includes('<script>'), html);
    assert.ok(html.includes('**bold-system**'), '系统消息不该过 Markdown');
    assert.ok(html.includes('<strong>bold</strong>'), 'assistant 必须走 renderMarkdown');
    assert.ok(html.includes('agent-tui-msg-assistant'), html);
});

test('agentNodeBodyHtml: steer/followup/abort 三个按钮只在 busy 时可用', () => {
    const { ctx } = loadAgentTuiModule();
    const body = fn(ctx, 'agentNodeBodyHtml');
    const idleHtml = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.equal((idleHtml.match(/disabled/g) || []).length, 3, idleHtml);
    evaluate(ctx, 'getAgentTuiState("n2").status = "busy"');
    const busyHtml = fromContext(body({ id: 'n2', type: 'agent' }));
    assert.ok(!/data-agent-steer[^>]*disabled/.test(busyHtml), busyHtml);
    assert.ok(!/data-agent-abort[^>]*disabled/.test(busyHtml), busyHtml);
});

test('agentNodeBodyHtml: 模型/自治/分支下拉按节点真值选中最优项，无分支时不渲染分支下拉', () => {
    const { ctx } = loadAgentTuiModule({
        chatProviders: [{ id: 'openai' }, { id: 'deepseek' }],
        modelsByProvider: { openai: ['gpt-a', 'gpt-b'], deepseek: [] },
    });
    const body = fn(ctx, 'agentNodeBodyHtml');
    const html = fromContext(body({
        id: 'n1', type: 'agent', model: 'gpt-b', autonomy: 'standard',
        branches: [{ id: 'b1', name: '主线' }, { id: 'b2', name: '支线' }],
        current_branch: 'b2', title: '我的 Agent',
    }));
    assert.ok(html.includes('<option value="gpt-b" selected>'), html);
    assert.ok(html.includes('<option value="gpt-a" >'), html);
    assert.ok(html.includes('<option value="standard" selected>'), html);
    assert.ok(html.includes('<option value="b2" selected>'), html);
    assert.ok(html.includes('agent-tui-title">我的 Agent<'), html);

    const plain = fromContext(body({ id: 'n2', type: 'agent' }));
    assert.ok(!plain.includes('agent-tui-branch-select'), '无分支不该渲染分支下拉');
    assert.ok(plain.includes('<option value="cautious" selected>'), '默认自治级别必须是谨慎');
    assert.ok(plain.includes('agent-tui-empty'), '空会话要有占位文案');
});

test('agentNodeBodyHtml: thinking 默认隐藏，thinkingVisible 打开后才渲染且转义', () => {
    const { ctx } = loadAgentTuiModule();
    const body = fn(ctx, 'agentNodeBodyHtml');
    evaluate(ctx, 'const s = getAgentTuiState("n1"); s.thinking = [{text:"<i>私密</i>"}]');
    assert.ok(!fromContext(body({ id: 'n1', type: 'agent' })).includes('agent-tui-thinking'));
    evaluate(ctx, 'getAgentTuiState("n1").thinkingVisible = true');
    const html = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.ok(html.includes('agent-tui-thinking'), html);
    assert.ok(html.includes('&lt;i&gt;私密&lt;/i&gt;'), html);
});

// ── agent-task 只读卡 ───────────────────────────────────────────────
test('agentTaskStatusText: 五种已知状态走 i18n，未知状态原样回显', () => {
    const { ctx } = loadAgentTuiModule();
    const text = fn(ctx, 'agentTaskStatusText');
    assert.deepEqual(
        ['queued', 'running', 'succeeded', 'failed', 'uncertain'].map(s => fromContext(text(s))),
        ['smart.stQueued', 'smart.stRunning', 'smart.stSucceeded', 'smart.failed', 'smart.stUncertain'],
    );
    // 未映射但已知的工作台状态走 tr；完全空的状态回显原值（agentTaskStatusText 末尾
    // `status || tr(...)`，所以传 'weird' 会原样返回，必须传空才能进 tr 分支）。
    assert.equal(fromContext(text('weird-state')), 'weird-state');
});

test('agentTaskNodeBodyHtml: 结果节点链接/等待占位、错误块按有无 error 出现、文本全转义', () => {
    const { ctx } = loadAgentTuiModule();
    const card = fn(ctx, 'agentTaskNodeBodyHtml');
    const withResult = fromContext(card({
        id: 'n1', task_id: 't1', status: 'succeeded', kind: 'video',
        result_node_id: 'n9', prompt: '<img src=x>', params: { n: 2 }, model_type: 'online',
    }));
    assert.ok(withResult.includes('data-agent-result-link="n9"'), withResult);
    assert.ok(withResult.includes('smart.viewResultNode'), withResult);
    assert.ok(withResult.includes('data-agent-task-card="t1"'), withResult);
    assert.ok(withResult.includes('&lt;img src=x&gt;'), 'prompt 必须转义');
    assert.ok(!withResult.includes('data-agent-task-error'), '无 error 不该渲染错误块');
    // trf 桩是记录器（见文件头）：把被代入的 kind 拼回返回值，所以这里能断言
    // 调用方确实把本地化后的 kind 传进了标题。
    assert.ok(withResult.includes('smart.agentTaskTitle\u00b7smart.agentKindVideo'), withResult);

    const failed = fromContext(card({ id: 'n2', task_id: 't2', status: 'failed', error: '配额不足' }));
    assert.ok(failed.includes('data-agent-task-error'), failed);
    assert.ok(failed.includes('配额不足'), failed);
    assert.ok(failed.includes('agent-task-result-link muted'), failed);
    assert.ok(failed.includes('smart.waitingResultNode'), failed);
});

// ── 项目 id 解析 ────────────────────────────────────────────────────
test('agentProjectId: canvas.project_id → canvas.project → sourceProjectId → 空串', () => {
    const a = loadAgentTuiModule({ canvasData: { project_id: 'p1', project: 'p0' } });
    assert.equal(fromContext(fn(a.ctx, 'agentProjectId')()), 'p1');
    const b = loadAgentTuiModule({ canvasData: { project: 'p0' } });
    assert.equal(fromContext(fn(b.ctx, 'agentProjectId')()), 'p0');
    const c = loadAgentTuiModule({ canvasData: {} });
    assert.equal(fromContext(fn(c.ctx, 'agentProjectId')()), 'source-project-1');
    const d = loadAgentTuiModule({ canvasData: {} });
    fromContext(evaluate(d.ctx, 'sourceProjectId = ""'));
    assert.equal(fromContext(fn(d.ctx, 'agentProjectId')()), '');
});

// ── getChatModels ───────────────────────────────────────────────────
test('getChatModels 把所有 provider 的模型拉平成 {id,name}，空 provider 不产条目', () => {
    const { ctx } = loadAgentTuiModule({
        chatProviders: [{ id: 'openai' }, { id: 'deepseek' }, { id: 'empty' }],
        modelsByProvider: { openai: ['gpt-a', 'gpt-b'], deepseek: ['ds-1'], empty: [] },
    });
    assert.deepEqual(fromContext(evaluate(ctx, 'getChatModels()')), [
        { id: 'gpt-a', name: 'gpt-a' },
        { id: 'gpt-b', name: 'gpt-b' },
        { id: 'ds-1', name: 'ds-1' },
    ]);
});

// ── renderMarkdown：安全边界 ─────────────────────────────────────────
test('renderMarkdown: 非白名单协议链接降级为 #，白名单协议保留', () => {
    const { ctx } = loadAgentTuiModule();
    const md = fn(ctx, 'renderMarkdown');
    const evil = fromContext(md('[点我](javascript:alert(1))'));
    assert.ok(evil.includes('href="#"'), evil);
    assert.ok(!evil.includes('javascript:'), evil);
    for (const url of ['https://a.test/x', 'http://a.test/x', 'mailto:a@b.c', '/abs/path', '#frag', './rel', '../up']) {
        const html = fromContext(md(`[t](${url})`));
        assert.ok(html.includes(`href="${url}"`), `${url} 应保留: ${html}`);
        assert.ok(html.includes('rel="noopener noreferrer"'), html);
    }
});

test('renderMarkdown: 先转义再套 Markdown 结构，代码块优先于行内码', () => {
    const { ctx } = loadAgentTuiModule();
    const md = fn(ctx, 'renderMarkdown');
    const block = fromContext(md('```\n<script>x</script>\n```'));
    assert.ok(block.includes('agent-md-code-block'), block);
    assert.ok(block.includes('&lt;script&gt;'), block);

    const inline = fromContext(md('a `b` c'));
    assert.ok(inline.includes('<code class="agent-md-inline-code">b</code>'), inline);

    // 粗体必须先于斜体，否则 ** 会被拆成 *。
    const bold = fromContext(md('**b** and *i*'));
    assert.ok(bold.includes('<strong>b</strong>'), bold);
    assert.ok(bold.includes('<em>i</em>'), bold);

    const list = fromContext(md('- a\n- b'));
    assert.ok(list.includes('<ul class="agent-md-list">'), list);
    assert.equal(fromContext(md(null)), '');
});

// ── fork / switch-branch / 分叉选择器（fetch 数据驱动）──────────────────────
test('forkAgentSession: 成功写回 node.branches/current_branch 且清空 messages/toolActivities；失败零副作用', async () => {
    const seed = (fetchResponse) => {
        const m = loadAgentTuiModule({ nodes: [{ id: 'n1', type: 'agent' }], fetchResponse });
        fn(m.ctx, 'handleAgentEvent')('n1', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'old' } });
        fn(m.ctx, 'handleAgentEvent')('n1', { type: 'tool_execution_start', toolName: 't', toolCallId: 'c1' });
        return m;
    };

    // 成功。
    const ok = seed({ ok: true, json: async () => ({ branches: [{ id: 'b1', name: '分叉' }], current_branch: 'b1' }) });
    await fn(ok.ctx, 'forkAgentSession')('n1', 'proj/9', 'entry-7', '新分支');
    assert.equal(fromContext(ok.fetchCalls.at(-1).url), '/api/projects/proj/9/agent-nodes/n1/fork');
    assert.deepEqual(JSON.parse(fromContext(ok.fetchCalls.at(-1).opts.body)), { entry_id: 'entry-7', name: '新分支' });
    assert.deepEqual(fromContext(evaluate(ok.ctx, 'canvasState.nodes[0]')),
        { id: 'n1', type: 'agent', branches: [{ id: 'b1', name: '分叉' }], current_branch: 'b1' },
        'node 是渲染真值，必须写回');
    assert.deepEqual(fromContext(evaluate(ok.ctx, 'getAgentTuiState("n1").messages')), [], '新分支从头开始，旧消息清空');
    assert.equal(fromContext(evaluate(ok.ctx, 'getAgentTuiState("n1").toolActivities.size')), 0, '工具活动一并清空');

    // 失败（非 2xx）：本地全不动。
    const bad = seed({ ok: false, json: async () => ({}) });
    await fn(bad.ctx, 'forkAgentSession')('n1', 'p1', 'e', 'n');
    assert.equal(fromContext(evaluate(bad.ctx, 'getAgentTuiState("n1").messages.length')), 1, '失败保留旧消息');
    assert.equal(fromContext(evaluate(bad.ctx, 'getAgentTuiState("n1").toolActivities.size')), 1);
    assert.equal(evaluate(bad.ctx, 'canvasState.nodes[0].branches'), undefined, '失败不写 branches（fromContext 不支持 undefined，直接读）');
    assert.ok(bad.consoleErrors.some(m => m.startsWith('Failed to fork agent session:')));
});

test('switchAgentBranch: 成功更新 current_branch 并清空会话；失败零副作用', async () => {
    const seed = (fetchResponse) => {
        const m = loadAgentTuiModule({
            nodes: [{ id: 'n1', type: 'agent', branches: [{ id: 'b1', name: '主' }, { id: 'b2', name: '侧' }], current_branch: 'b1' }],
            fetchResponse,
        });
        fn(m.ctx, 'handleAgentEvent')('n1', { type: 'turn_end', message: { content: [{ type: 'text', text: 'x' }] } });
        return m;
    };
    const ok = seed({ ok: true, json: async () => ({ current_branch: 'b2' }) });
    await fn(ok.ctx, 'switchAgentBranch')('n1', 'p1', 'b2');
    assert.equal(fromContext(ok.fetchCalls.at(-1).url), '/api/projects/p1/agent-nodes/n1/switch-branch');
    assert.deepEqual(JSON.parse(fromContext(ok.fetchCalls.at(-1).opts.body)), { branch_id: 'b2' });
    assert.equal(fromContext(evaluate(ok.ctx, 'canvasState.nodes[0].current_branch')), 'b2');
    assert.deepEqual(fromContext(evaluate(ok.ctx, 'getAgentTuiState("n1").messages')), [], '切分支清空旧会话');

    const bad = seed({ ok: false, json: async () => ({}) });
    await fn(bad.ctx, 'switchAgentBranch')('n1', 'p1', 'b2');
    assert.equal(fromContext(evaluate(bad.ctx, 'canvasState.nodes[0].current_branch')), 'b1', '失败保留旧分支');
    assert.equal(fromContext(evaluate(bad.ctx, 'getAgentTuiState("n1").messages.length')), 1, '失败不清会话');
});

test('showForkPicker: prompt 选轮次与分支名后分叉（entry_id + name）；空列表/越界/空白选择不分叉', async () => {
    const listResponse = { ok: true, json: async () => ({ messages: [{ text: '第一轮', entryId: 'e1' }, { text: '第二轮', entryId: 'e2' }] }) };
    const ok = loadAgentTuiModule({
        nodes: [{ id: 'n1', type: 'agent' }],
        fetchResponse: (url) => url.endsWith('/fork-messages') ? listResponse : { ok: true, json: async () => ({}) },
    });
    ok.promptReturns.push('2', '分叉线');
    await fn(ok.ctx, 'showForkPicker')('n1', 'p1');
    assert.equal(ok.promptLabels.length, 2, '两轮 prompt：选轮次 + 分支名');
    assert.ok(ok.promptLabels[0].includes('1. 第一轮') && ok.promptLabels[0].includes('2. 第二轮'), 'prompt 列出编号消息列表');
    assert.ok(ok.promptLabels[1].startsWith('分支名称：'), '第二问是分支名');
    const forkCall = ok.fetchCalls.at(-1);
    assert.ok(forkCall.url.endsWith('/agent-nodes/n1/fork'), forkCall.url);
    assert.deepEqual(JSON.parse(fromContext(forkCall.opts.body)), { entry_id: 'e2', name: '分叉线' });

    // 空消息列表：直接返回，不 prompt 不分叉。
    const empty = loadAgentTuiModule({ nodes: [{ id: 'n1' }], fetchResponse: { ok: true, json: async () => ({ messages: [] }) } });
    await fn(empty.ctx, 'showForkPicker')('n1', 'p1');
    assert.equal(empty.promptLabels.length, 0);
    assert.equal(empty.fetchCalls.length, 1, '只有 fork-messages 一次请求');

    // 越界选择（'99'）与空白选择（prompt 返回 null）都不分叉。
    const one = { ok: true, json: async () => ({ messages: [{ text: '只有一轮', entryId: 'e9' }] }) };
    const oob = loadAgentTuiModule({ nodes: [{ id: 'n1' }], fetchResponse: one });
    oob.promptReturns.push('99');
    await fn(oob.ctx, 'showForkPicker')('n1', 'p1');
    assert.equal(oob.fetchCalls.length, 1, '越界 → 不分叉');
    const blank = loadAgentTuiModule({ nodes: [{ id: 'n1' }], fetchResponse: one });
    await fn(blank.ctx, 'showForkPicker')('n1', 'p1');
    assert.equal(blank.fetchCalls.length, 1, '空白选择 → 不分叉');
});

// ── renderAgentNode：重渲染与重绑定 ───────────────────────────────
test('renderAgentNode: 重渲染主体 + 卡片状态 + 重绑定；el/node 缺失与 layout=null → no-op', () => {
    const { ctx, harness, bodyHtmlCalls } = loadAgentTuiModule({
        nodes: [{ id: 'n1', type: 'agent' }],
        open: false,
    });
    // 注册 document.querySelector('[data-id="n1"]') 能找到的元素（浏览器 DOM 的替身）。
    const el = harness.makeElement('div');
    const body = harness.makeElement('div');
    const headMeta = harness.makeElement('div');
    const messagesEl = harness.makeElement('div');
    body.querySelector = (sel) => (sel === '[data-agent-tui-messages]' ? messagesEl : null);
    el.querySelector = (sel) => (sel === '.node-body' ? body : sel === '.node-head-meta' ? headMeta : null);
    harness.queryResults['[data-id="n1"]'] = [el];

    fn(ctx, 'getAgentTuiState')('n1').status = 'busy';
    // 计新创建的 socket：重绑定会调 connectAgentWebSocket，旧 socket 非 OPEN 时新建。
    let constructed = 0;
    const Base = ctx.WebSocket;
    ctx.WebSocket = class extends Base {
        constructor(url) { constructed++; super(url); }
    };
    fn(ctx, 'renderAgentNode')('n1');
    assert.equal(bodyHtmlCalls.length, 1, '渲染一次主体');
    assert.equal(body.innerHTML, '<div class="node-body-stub"/>', 'nodeBodyHtml 输出进 .node-body innerHTML');
    assert.ok(headMeta.innerHTML.includes('agent-tui-status-connecting'), headMeta.innerHTML);
    assert.ok(headMeta.innerHTML.includes('连接中'),
        '重绑定触发 connectAgentWebSocket，旧 socket 非 OPEN 时 status 置 connecting（真实行为）');
    assert.equal(constructed, 1, 'bindAgentNodeControls 重绑定并连接（旧 socket 未 OPEN → 新建）');
    fn(ctx, 'renderAgentNode')('n1');
    assert.equal(constructed, 2, '重绑定后 socket 仍未 OPEN → 再新建（当前行为）');
    assert.equal(bodyHtmlCalls.length, 2);

    // el 缺失 → 不渲染。
    fn(ctx, 'renderAgentNode')('missing');
    assert.equal(bodyHtmlCalls.length, 2);
    // node 缺失（el 注册了但 canvasState.nodes 里没有）→ 不渲染。
    harness.queryResults['[data-id="ghost"]'] = [harness.makeElement('div')];
    fn(ctx, 'renderAgentNode')('ghost');
    assert.equal(bodyHtmlCalls.length, 2);

    // layout=null → 在调 nodeBodyHtml 之前早退。
    const m2 = loadAgentTuiModule({ nodes: [{ id: 'n1', type: 'agent' }], open: false, layout: null });
    m2.harness.queryResults['[data-id="n1"]'] = [m2.harness.makeElement('div')];
    fn(m2.ctx, 'renderAgentNode')('n1');
    assert.equal(m2.bodyHtmlCalls.length, 0, 'layout 为 null 时早退，不渲染');
});
