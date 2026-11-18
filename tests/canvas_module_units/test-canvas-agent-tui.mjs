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
//   - 11 个手写桩（全部来自 hub smart-canvas.js，按真实契约最小化）：
//       trf → 值传递记录器（返回值 = 真实 tr(key) + 被代入值；无 StudioI18n 时 smart-canvas.js:
//             314-316 的逐字 trf 返回 key 本身、代入值被丢弃，值传递路径不可观察，故刻意不用
//             逐字镜像——也不重新实现模板替换，零产品逻辑）；
//       chatApiProviders / providerChatModels → 数据驱动（测试直接喂数组）；
//       canvasId / sourceProjectId → 数据字段；
//       imageLayout / nodeBodyHtml / render / centerViewportOnWorldPoint → 零逻辑记录器
//       （只记录「被调了几次、参数是什么」，不返回布局结构，也不参与任何断言的计算）；
//       scheduleSave → #323 票 E 新增的零逻辑记录器（只有票 E 函数引用）。
//       注：toast 不是 hub 自由名（canvas-core.js 真函数，桩会被加载盖住）——
//       改为注册真 #toast 元素，断言走 element.textContent。
//   - 浏览器表面积（harness 不提供、按实例补，零产品逻辑）：
//       WebSocket → 记录器类 + OPEN 常量（真实契约只有 readyState/send/close/onmessage…）；
//       fetch → 记录器（harness.mjs:209 默认 fetch 直接抛，测试内按实例覆盖）；
//       #317：fork 选择器不再是 prompt（canvas-agent-tui.js:676-704 换成画布自有 <dialog>）——
//       harness 已补 dialog 表面（open/returnValue/showModal/close→close 事件 + harness.dialogs
//       句柄），测试按实例给 dialog 补 input 替身（见本文件 fillForkDialog），零产品逻辑。
//   - #323 票 E（会话框/参考框）：loader 选项 withNodeOps=true 额外加载真实
//       canvas-layout-utils.js（纯常量）/ canvas-undo.js（真 pushUndo）/ canvas-node-ops.js
//       （真 createSmartGroupNode/toggleGroupRefFrame）；新增记录器只有 scheduleSave/alert（toast 走真元素）。
// ADR 0020 判据「桩要开始重新实现产品逻辑就停」：**未命中**——11 个桩里 0 个复制产品逻辑。
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
//   - setAgentModel / setAgentAutonomy：严格人工载荷、拒绝乐观持久写入、权威画布接收 seam；
//   - connectAgentWebSocket 回调面：onmessage 派发到真实 handleAgentEvent、畸形 JSON 走
//     console.error 不外抛、onerror→error、onclose→socket 复位 null 并进入 #280 退避
//     （重连中/启动失败/重连停止）、构造器抛错→null 且计入失败、https location 切 wss；
//   - sendAgentSteer / sendAgentFollowUp 的 HTTP 回退（WS 未连：URL/body 精确；onSuccess 不
//     执行——#221 缺口，断言钉现状）；
//   - forkAgentSession / switchAgentBranch：成功经权威画布接收 seam 后清空
//     messages/toolActivities、失败零副作用；showForkPicker 的 <dialog> 选择→fork(entry_id,
//     name)（#317：两次 prompt() 换成一个 dialog）、空列表/取消/无选中轮次不分叉、
//     分支名留空仍分叉（服务端自动命名）；
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
//   - #280 重连退避与失败可见：agentReconnectDelayMs 纯退避序列（1s 起步翻倍、30s 封顶）、
//     onclose/构造器失败计入连续失败、退避在途/触顶后重复 connect 拒绝建连、连续失败 8 次
//     停止自动重连、force 重试清上限立即建连、后端 session 事件清退避态、后端 error 事件
//     （此前 default 分支静默吞掉→节点一直显示空闲）置 failed 态+原因且不重复堆叠、
//     失败横幅（agentNodeBodyHtml）与手动「重试连接」按钮；
//   - #323 票 E 会话框/参考框：agentChatFrameAction 无框 → 真 createSmartGroupNode
//     （位置 = agent.x + (w||240) + 40、标题 = (agent.title||'会话') + ' · 会话框'、写 chatFrameId、
//     第二次点击不重复建只 centerViewportOnWorldPoint、stale id 按无自愈）；agentRefFrameCycle
//     循环（未绑定 → 第一个 → 下一个 → 未绑定，×N 素材计数，解绑 = 清字段），无参考框只更新
//     按钮 title（禁 alert、绑定不动、不 render/不保存）；toggleGroupRefFrame true↔false
//     （render+scheduleSave+toast），非 group id no-op；head 按钮 HTML（会话框激活 class+✓、
//     参考解绑「参考」/绑定「参考 ×N」）。
//
// 明确判为「单测盖不住」的行为（不写假测试）：
//   - WebSocket 真实服务端行为：握手/断线重连/服务端事件顺序（onmessage JSON.parse 异常
//     分支只覆盖到「解析失败不外抛」，真实网络时序靠浏览器 runner）；
//   - showForkPicker 弹层的真实模态语义：<dialog> 的 showModal/close/returnValue 与取值本文件
//     已覆盖（harness 补 dialog 桩），但焦点陷阱、Escape 关闭、CSS 布局靠浏览器 runner；
//   - fork/switch-branch/model/autonomy/prompt 等 HTTP 端点的真实网络往返（fetch 是
//     数据驱动桩，只验证请求形态与本地状态变化）；
//   - 渲染出的 HTML 在浏览器里的实际布局/CSS（只断言字符串包含关系）。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';
import { editor as humanEditor } from './smart-human-handlers-harness-392.mjs';

// #409：旧的无引用用例仍严格验证原载荷；关联字段另验，不把新增 request_id 当成回归。
function inputPayloadForLegacyAssertion(raw) {
    const payload = JSON.parse(fromContext(raw));
    if (payload.type === 'abort') return payload;
    const { request_id, ...rest } = payload;
    assert.match(request_id, /^input-\d+-\d+$/);
    return rest;
}
function inputMessageForLegacyAssertion(message) {
    if (!message.request_id) return message;
    const { request_id, input_type, delivery, node_refs, attachments, ...rest } = message;
    assert.match(request_id, /^input-\d+-\d+$/);
    assert.ok(['prompt', 'steer', 'follow_up'].includes(input_type));
    assert.equal(delivery, 'pending', 'socket.send 不是服务端接受回执');
    assert.deepEqual(node_refs, [], '旧无引用用例不得继承引用');
    if (rest.role === 'user') rest.attachments = attachments;
    else assert.deepEqual(attachments, []);
    return rest;
}

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
    // #323 票 E：加载真实 canvas-node-ops.js（createSmartGroupNode/toggleGroupRefFrame）及其依赖。
    withNodeOps = false,
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
    const consoleErrors = [];
    // #392：只记录权威接收边界，不在 TUI 夹具复制 merge/revision 算法。
    const receivedCanvases = [];
    ctx.receiveAgentSettingsCanvas = canvas => { receivedCanvases.push(canvas); return true; };
    const alerts = [];   // #323 票 E：alert 必须为 0（票面禁止 alert），记录器兼作断言
    // #323 票 E：toast 是 canvas-core.js 的真实函数（不是 hub 自由名，无法用 ctx.toast 桩盖住）——
    // 注册真 #toast 元素让真 toast 跑通，断言走 element.textContent。

    // hub 自由名（见文件头桩预算）。
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
    // #323 票 E：保存记录器（零逻辑；hub 自由名，只有票 E 的新函数引用它）。
    ctx.scheduleSave = () => renders.push({ kind: 'scheduleSave' });
    ctx.alert = () => alerts.push(1);
    harness.element('toast');
    // #317 起本模块不再调 prompt（fork 走 <dialog>），故不再有 prompt 记录器；
    // 需要 prompt 的用例可直接按实例覆盖 ctx.prompt（harness.mjs 默认返回 null）。
    // console 记录器：让 console.error 分支可断言，同时避免测试输出噪音。
    ctx.console = {
        error: (...args) => { consoleErrors.push(args.map(String).join(' ')); },
        log: () => {},
        warn: (...args) => consoleErrors.push(args.map(String).join(' ')),
    };

    loadModules(ctx, [
        'canvas-node-types.js',
        'canvas-state.js',
        'canvas-core.js',
        // #323 票 E：真实节点操作链（createSmartGroupNode 的 pushUndo/布局常量走真模块，
        // 不是桩——ADR 0020 判据「桩要开始重新实现产品逻辑就停」）。
        ...(withNodeOps ? ['canvas-layout-utils.js', 'canvas-undo.js', 'canvas-node-ops.js'] : []),
        'canvas-agent-navigation.js',
        'canvas-agent-tui.js',
    ]);
    // D1/D2 fixtures isolate their scroll/IME behavior at the explicit navigation API.
    // The real module is loaded above; composer/navigation integration has its own tests.
    ctx.createAgentMessageNavigation = () => ({ refresh() {}, update() {}, destroy() {} });
    fromContext(evaluate(ctx, `canvas = ${JSON.stringify(canvasData ?? { project_id: 'proj-9' })}`));
    for (const node of nodes) {
        ctx.canvasState.nodes.push(node);
    }
    return { ctx, harness, ws, fetchCalls, renders, bodyHtmlCalls, consoleErrors, alerts, receivedCanvases };
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

    // #222：turn_end 必须把本轮 streaming 消息收尾。之前它从不收尾，
    // 下一轮的 text_delta 会直接拼到上一轮末尾，两轮回复显示成一条。
    handle('n1', { type: 'turn_end', message: { content: [] } });
    messages = fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages'));
    assert.deepEqual(messages, [{ role: 'assistant', text: 'Hello' }],
        'turn_end 要把 streaming 标记清掉，把本轮回复定型');
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'Next' } });
    messages = fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages'));
    assert.equal(messages.length, 2, '收尾后的新一轮增量必须新起一条，不能拼到上一轮');
    assert.deepEqual(messages[1], { role: 'assistant', text: 'Next', streaming: true });

    // 非 streaming 末尾时必须是新起一条（用 turn_end 带 content 做对照）。
    handle('n2', { type: 'turn_end', message: { content: [{ type: 'text', text: 'done' }] } });
    handle('n2', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'New' } });
    messages = fromContext(evaluate(ctx, 'getAgentTuiState("n2").messages'));
    assert.deepEqual(messages, [
        { role: 'assistant', text: 'done' },
        { role: 'assistant', text: 'New', streaming: true },
    ]);
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'other' } });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages').length), 2,
        '不认识的 assistantMessageEvent.type 不能改动消息列表');
});

test('handleAgentEvent: turn_end 带着流式已输出过的同一段文本时不能重复推入（#222）', () => {
    const { ctx } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'Hel' } });
    handle('n1', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'lo' } });
    // 服务商同时在 turn_end 里给完整 content：流式增量已经写过这段，
    // 再推一条就是历史区出现重复回复。
    handle('n1', { type: 'turn_end', message: { content: [{ type: 'text', text: 'Hello' }] } });
    const messages = fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages'));
    assert.deepEqual(messages, [{ role: 'assistant', text: 'Hello' }]);
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
    handle('n1', { type: 'tool_execution_end', toolCallId: 'c1', result: 'ok', isError: false, duration_ms: 1500 });
    assert.deepEqual(fromContext(evaluate(ctx, 'Array.from(agentTuiState.get("n1").toolActivities.values())')), [
        // #322 票 D：start 新增 turn/duration_ms 字段；end 携带 duration_ms 则落盘
        { name: 'write', status: 'done', args: { p: 1 }, result: 'ok', isError: false, update: { pct: 50 }, turn: 0, duration_ms: 1500 },
    ]);
    assert.equal(fromContext(evaluate(ctx, 'agentTuiState.get("n1").toolActivities.size')), 1,
        '未知 toolCallId 的 update 不能凭空建条目');

    handle('n2', { type: 'tool_execution_start', toolName: 'rm', toolCallId: 'c2' });
    handle('n2', { type: 'tool_execution_end', toolCallId: 'c2', result: 'boom', isError: true });
    assert.deepEqual(fromContext(evaluate(ctx, 'agentTuiState.get("n2").toolActivities.get("c2")')),
        { name: 'rm', status: 'error', result: 'boom', isError: true, turn: 0, duration_ms: null });
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

test('connectAgentWebSocket: onerror → error；onclose → socket 复位 null 并排 1s 退避重连（#280）', () => {
    const { ctx, harness } = loadAgentTuiModule({ open: true });
    const sockets = [];
    const Base = ctx.WebSocket;
    ctx.WebSocket = class extends Base { constructor(url) { super(url); sockets.push(this); } };
    const socket = fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    socket.onerror();
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'error');
    socket.onclose();
    const state = fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    assert.equal(state.socket, null,
        '断开后 socket 必须复位 null，否则下次 connect 被陈旧实例挡住');
    assert.equal(state.status, 'reconnecting', '后端没报原因时是纯断线：显示重连中');
    assert.equal(state.retryCount, 1);
    const pending = harness.timers.filter(t => !t.fired && !t.cancelled);
    assert.equal(pending.length, 1, '断开必须排一个退避定时器（旧实现无退避→重连风暴）');
    assert.equal(pending[0].ms, 1000, '首次失败 1s 起步');
    harness.advanceTimers();
    assert.equal(sockets.length, 2, '退避到期必须真的发起下一次连接');
});

test('connectAgentWebSocket: 构造器抛错 → 返回 null、计入一次失败并排 1s 退避（#280）', () => {
    const { ctx, harness, consoleErrors } = loadAgentTuiModule({ open: false });
    class Boom { constructor() { throw new Error('boom'); } }
    ctx.WebSocket = Boom;
    const r = fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    assert.equal(r, null);
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").socket')), null);
    const state = fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    assert.equal(state.status, 'reconnecting', '构造失败拿不到 close 事件，也要进入退避（旧实现卡 idle 且重试无门）');
    assert.equal(state.retryCount, 1);
    const pending = harness.timers.filter(t => !t.fired && !t.cancelled);
    assert.equal(pending.length, 1);
    assert.equal(pending[0].ms, 1000);
    assert.ok(consoleErrors.some(m => m.startsWith('Failed to connect agent WebSocket:')));
});

// ── #280：重连退避与失败可见（重连风暴 + 启动失败静默）────────────────
test('agentReconnectDelayMs: 指数退避序列——1s 起步、翻倍、30s 封顶（#280）', () => {
    const { ctx } = loadAgentTuiModule();
    const delay = fn(ctx, 'agentReconnectDelayMs');
    const seq = [1, 2, 3, 4, 5, 6, 7, 8, 9, 20].map(n => fromContext(delay(n)));
    assert.deepEqual(seq, [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000, 30000, 30000],
        '1s 起步、每次翻倍、30s 封顶');
    assert.equal(fromContext(delay(0)), 1000, '无效输入回退起步值');
    assert.equal(fromContext(delay(NaN)), 1000);
    assert.equal(fromContext(delay(3, 500, 10000)), 2000, '自定义 base/cap 生效');
});

test('#280 onclose 指数退避：1→2→4→8→16→30s 逐次翻倍，退避在途拒绝重复建连，session 事件清退避态', () => {
    const { ctx, harness } = loadAgentTuiModule({ open: false });
    const state = () => fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    const sockets = [];
    const Base = ctx.WebSocket;
    ctx.WebSocket = class extends Base { constructor(url) { super(url); sockets.push(this); } };

    fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    assert.equal(sockets.length, 1);
    for (let attempt = 2; attempt <= 7; attempt++) {
        sockets.at(-1).onclose();                       // 第 attempt-1 条连接失败
        const st = state();
        assert.equal(st.retryCount, attempt - 1);
        assert.equal(st.status, 'reconnecting');
        const pending = harness.timers.filter(t => !t.fired && !t.cancelled);
        assert.equal(pending.length, 1, '同一时刻只能有一个退避定时器');
        assert.equal(pending[0].ms, [1000, 2000, 4000, 8000, 16000, 30000][attempt - 2],
            `连续失败 ${attempt - 1} 次的退避时长`);
        // 旧重连风暴的根源：失败后的每次渲染重绑定都立即 new WebSocket——现在必须拒绝。
        assert.equal(fn(ctx, 'connectAgentWebSocket')('n1', 'p1'), null, '退避在途不重复建连');
        harness.advanceTimers();
        assert.equal(sockets.length, attempt, '退避到期必须真的发起下一次连接');
    }
    // 重连成功（后端 session 事件）：清退避态。
    sockets.at(-1).onmessage({ data: JSON.stringify({ type: 'session' }) });
    const after = state();
    assert.equal(after.status, 'idle');
    assert.equal(after.retryCount, 0);
    assert.equal(after.reconnectTimer, null);
    assert.equal(harness.timers.filter(t => !t.fired && !t.cancelled).length, 0);
});

test('#280 连续失败 8 次触顶：停止自动重连，force 重试清上限并立即连接', () => {
    const { ctx, harness } = loadAgentTuiModule({ open: false });
    const state = () => fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    const sockets = [];
    const Base = ctx.WebSocket;
    ctx.WebSocket = class extends Base { constructor(url) { super(url); sockets.push(this); } };

    fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    for (let i = 1; i <= 8; i++) {
        sockets.at(-1).onclose();
        const st = state();
        assert.equal(st.retryCount, i);
        if (i < 8) {
            assert.equal(st.status, 'reconnecting');
            assert.notEqual(st.reconnectTimer, null, `第 ${i} 次失败应排退避`);
            harness.advanceTimers();
            assert.equal(sockets.length, i + 1);
        }
    }
    const st = state();
    assert.equal(st.status, 'reconnect_stopped', '触顶显示「重连停止」，不再假装在连');
    assert.equal(st.reconnectStopped, true);
    assert.equal(st.reconnectTimer, null, '触顶后不得再有在途定时器');
    assert.equal(harness.timers.filter(t => !t.fired && !t.cancelled).length, 0);
    assert.equal(fn(ctx, 'connectAgentWebSocket')('n1', 'p1'), null, '触顶后普通路径不再建连');

    // 手动「重试连接」（force）：清上限、立即建连。
    const socket = fn(ctx, 'connectAgentWebSocket')('n1', 'p1', { force: true });
    assert.ok(socket, 'force 必须立即建连，不等退避');
    assert.equal(sockets.length, 9);
    assert.equal(state().retryCount, 0);
    assert.equal(state().reconnectStopped, false);
});

test('#280 后端 error 事件（spawn 失败）→「启动失败」态+原因，重发不堆叠，close 后保持失败态', () => {
    const { ctx, harness } = loadAgentTuiModule({ open: false });
    const state = () => fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    const socket = fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    // 真实后端行为（app/routers/api_agent_nodes.py agent_event_stream）：
    // 握手成功后 spawn 失败，发 {"type":"error","message":...} 然后立刻断开。
    socket.onmessage({ data: JSON.stringify({ type: 'error', message: 'Failed to start agent session' }) });
    let st = state();
    assert.equal(st.status, 'failed', '失败必须可见（此前 default 分支静默吞掉，节点一直显示空闲）');
    assert.equal(st.lastError, 'Failed to start agent session');
    assert.deepEqual(st.messages, [{ role: 'system', text: '⚠️ Failed to start agent session' }]);
    // 每次重连失败后端都会重发同一原因——历史区不得重复堆叠。
    socket.onmessage({ data: JSON.stringify({ type: 'error', message: 'Failed to start agent session' }) });
    assert.equal(state().messages.length, 1, '同一失败原因不重复堆叠');
    // 服务端断开：徽标保持「启动失败」（不被「重连中」盖掉），后台 1s 退避重试。
    socket.onclose();
    st = state();
    assert.equal(st.status, 'failed');
    assert.equal(st.retryCount, 1);
    const pending = harness.timers.filter(t => !t.fired && !t.cancelled);
    assert.equal(pending.length, 1);
    assert.equal(pending[0].ms, 1000);
});

test('#280 失败横幅：failed/reconnect_stopped 渲染原因+可操作提示+「重试连接」，正常态无横幅', () => {
    const { ctx } = loadAgentTuiModule({ nodes: [{ id: 'n1', type: 'agent' }] });
    const body = fn(ctx, 'agentNodeBodyHtml');
    assert.ok(!fromContext(body({ id: 'n1', type: 'agent' })).includes('agent-tui-error-banner'),
        '正常态不能出现失败横幅');

    evaluate(ctx, `(() => {
        const s = getAgentTuiState("n1");
        s.status = "failed";
        s.lastError = "Failed to start agent session";
        s.retryCount = 3;
    })()`);
    let html = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.ok(html.includes('agent-tui-error-banner'), html);
    assert.ok(html.includes('启动失败'), '徽标文案「启动失败」');
    assert.ok(html.includes('Failed to start agent session'), '展示后端原因');
    assert.ok(html.includes('FIREFLY_PI_CLI'), '可操作提示');
    assert.ok(html.includes('重试连接'), '手动重试入口');
    assert.ok(html.includes('已连续失败 3 次'), '退避进度可见');

    evaluate(ctx, `(() => {
        const s = getAgentTuiState("n1");
        s.status = "reconnect_stopped";
        s.retryCount = 8;
    })()`);
    html = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.ok(html.includes('自动重连已停止'), html);
    assert.ok(html.includes('重试连接'), html);
});

test('#280 卡片状态映射：failed→启动失败 / reconnecting→重连中 / reconnect_stopped→重连停止', () => {
    const { ctx } = loadAgentTuiModule();
    const card = fn(ctx, 'agentCardStatusHtml');
    for (const [status, cls, text] of [
        ['failed', 'agent-tui-status-error', '启动失败'],
        ['reconnecting', 'agent-tui-status-disconnected', '重连中'],
        ['reconnect_stopped', 'agent-tui-status-error', '重连停止'],
    ]) {
        evaluate(ctx, `getAgentTuiState("n-${status}").status = ${JSON.stringify(status)}`);
        const html = fromContext(card(`n-${status}`));
        assert.ok(html.includes(`class="agent-card-status ${cls}"`), html);
        assert.ok(html.includes(`>${text}<`), html);
    }
});

test('#280 点「重试连接」→ force 重连（清上限）并重渲染；触顶后重绑定不建连', () => {
    const { ctx, harness } = loadAgentTuiModule({ open: false, nodes: [{ id: 'n1', type: 'agent' }] });
    const el = harness.makeElement('div');
    const retryBtn = harness.makeElement('button');
    const renders = [];
    ctx.renderAgentNode = nodeId => renders.push(nodeId);
    el.querySelector = (sel) => (sel === '[data-agent-retry]' ? retryBtn : null);
    // 触顶态：普通路径不会建连，只有点按钮才会。
    evaluate(ctx, `(() => {
        const s = getAgentTuiState("n1");
        s.reconnectStopped = true;
        s.retryCount = 8;
        s.lastError = "Failed to start agent session";
    })()`);
    const sockets = [];
    const Base = ctx.WebSocket;
    ctx.WebSocket = class extends Base { constructor(url) { super(url); sockets.push(this); } };
    fn(ctx, 'bindAgentNodeControls')(el, { id: 'n1', type: 'agent' });
    assert.equal(sockets.length, 0, '触顶后重绑定不得建连（防风暴）');
    retryBtn.dispatchEvent(ev('click'));
    assert.equal(sockets.length, 1, '点「重试连接」必须立即建连');
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").retryCount')), 0, '手动重试清退避上限');
    assert.deepEqual(renders, ['n1'], '重连后要重渲染');
});

test('sendAgentPrompt: WS 打开走 socket.send（载荷含 images），关闭走 HTTP POST，两条路径都推 user 消息并置 busy', () => {
    const open = loadAgentTuiModule({ open: true });
    const sendPrompt = fn(open.ctx, 'sendAgentPrompt');
    fn(open.ctx, 'connectAgentWebSocket')('n1', 'p1');
    sendPrompt('n1', 'p1', '  画一只猫  ', ['img/1']);
    assert.deepEqual(inputPayloadForLegacyAssertion(open.ws.sent[0]), {
        type: 'prompt', message: '  画一只猫  ', images: ['img/1'],
    });
    assert.equal(open.fetchCalls.length, 0, 'WS 可用时不该再走 HTTP');
    assert.deepEqual(fromContext(evaluate(open.ctx, 'getAgentTuiState("n1").messages')).map(inputMessageForLegacyAssertion), [
        { role: 'user', text: '  画一只猫  ', attachments: [] }, // #268：user 消息记录附件名
    ]);
    assert.equal(fromContext(evaluate(open.ctx, 'getAgentTuiState("n1").status')), 'busy');

    // #268：images 传 {dataUrl,name,caption} 对象时，载荷归一化为 dataURL 字符串，附件名留在消息上
    sendPrompt('n1', 'p1', '看附件', [
        { dataUrl: 'data:image/png;base64,AA', name: 'sheet.png', caption: '视频 → contact sheet' },
    ]);
    assert.deepEqual(inputPayloadForLegacyAssertion(open.ws.sent[1]), {
        type: 'prompt', message: '看附件', images: ['data:image/png;base64,AA'],
    });
    assert.deepEqual(fromContext(evaluate(open.ctx, 'getAgentTuiState("n1").messages')).map(inputMessageForLegacyAssertion)[1],
        { role: 'user', text: '看附件', attachments: ['sheet.png'] },
        '对象附件的名字记在消息上，payload 只带 dataURL');

    const closed = loadAgentTuiModule({ open: false });
    fn(closed.ctx, 'sendAgentPrompt')('n1', 'proj/2', '文本', []);
    assert.equal(closed.fetchCalls.length, 1);
    assert.equal(fromContext(closed.fetchCalls[0].url),
        '/api/projects/proj%2F2/agent-nodes/n1/prompt');
    assert.equal(fromContext(closed.fetchCalls[0].opts.method), 'POST');
    assert.deepEqual(inputPayloadForLegacyAssertion(closed.fetchCalls[0].opts.body), { message: '文本', images: [] });
});

test('sendAgentSteer / FollowUp / Abort: wsPayload 形状、系统消息、abort 等待权威结束', () => {
    const { ctx, ws } = loadAgentTuiModule({ open: true });
    fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    evaluate(ctx, 'getAgentTuiState("n1").status = "busy"');
    fn(ctx, 'sendAgentSteer')('n1', 'p1', '转向');
    fn(ctx, 'sendAgentFollowUp')('n1', 'p1', '排队');
    fn(ctx, 'sendAgentAbort')('n1', 'p1');
    assert.deepEqual(ws.sent.map(inputPayloadForLegacyAssertion), [
        { type: 'steer', message: '转向' },
        { type: 'follow_up', message: '排队' },
        { type: 'abort' },
    ]);
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages')).map(inputMessageForLegacyAssertion), [
        { role: 'system', text: '↪ steer: 转向' },
        { role: 'system', text: '⏳ follow_up queued: 排队' },
        { role: 'system', text: '⏹ 停止请求已发送，等待宿主结束' },
    ]);
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'stopping',
        '请求送达不等于宿主已停止，参数必须继续锁定');
});

test('sendAgentCommand: WS 未连接走 HTTP，但 onSuccess 必须照样执行（#221）', async () => {
    const { ctx, ws, fetchCalls } = loadAgentTuiModule({ open: false });
    evaluate(ctx, 'getAgentTuiState("n1").status = "busy"');
    fn(ctx, 'sendAgentCommand')('n1', 'proj/2', 'steer', {
        body: { message: 'm' },
        wsPayload: { type: 'steer', message: 'm' },
        optimistic: (state) => state.messages.push({ role: 'system', text: 'optimistic' }),
        onSuccess: (state) => state.messages.push({ role: 'system', text: 'steered' }),
    });
    assert.equal(ws.sent.length, 0);
    assert.equal(fetchCalls.length, 1);
    assert.equal(fromContext(fetchCalls[0].url), '/api/projects/proj%2F2/agent-nodes/n1/steer');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls[0].opts.body)), { message: 'm' });
    // 同步可见的是 optimistic（它负贵让按钮不卡住），onSuccess 要等请求送达。
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages')),
        [{ role: 'system', text: 'optimistic' }], 'optimistic 必须同步跑');
    await new Promise(resolve => setTimeout(resolve, 0));
    // 本地状态表的是用户意图，跟走哪条通道无关。之前 HTTP 路径直接 return，
    // onSuccess（abort 唯一复位 idle 的地方）被整个跳过。
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages')),
        [{ role: 'system', text: 'optimistic' }, { role: 'system', text: 'steered' }],
        'onSuccess 必须在 HTTP 路径也执行');
    // body 未提供时不带 body 字段（abort 走这条）。
    fn(ctx, 'sendAgentCommand')('n1', 'p1', 'abort', { wsPayload: { type: 'abort' } });
    // 不能用 fromContext：harness.mjs:287 是 JSON 往返，对 undefined 会抛。
    assert.equal(fetchCalls[1].opts.body, undefined);
});

test('sendAgentAbort: WS 已断开时走 HTTP，状态等待宿主确认停止（#221/#421）', () => {
    const { ctx, ws, fetchCalls } = loadAgentTuiModule({ open: false });
    evaluate(ctx, 'getAgentTuiState("n1").status = "busy"');
    fn(ctx, 'sendAgentAbort')('n1', 'proj/2');
    assert.equal(ws.sent.length, 0, 'ws 不可用，什么都不走 socket');
    assert.equal(fetchCalls.length, 1);
    assert.equal(fromContext(fetchCalls[0].url), '/api/projects/proj%2F2/agent-nodes/n1/abort');
    // 用户可见症状全在这里：复位 idle 是 onSuccess 里做的，跳过它按钮就永远 disabled。
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'stopping',
        'HTTP 发送后也要等权威结束反馈');
});

test('sendAgentAbort: HTTP 请求失败时不能写“⏹ aborted”，要记「未送达」警告（#221 备注）', async () => {
    // issue 备注原文：「别修成『失败也显示已停止』」。上一轮修复只补了“按钮不卡住”，
    // 但 fetch 是 fire-and-forget：.catch 只记 console，onSuccess 无条件跑，
    // 请求根本没送达也会写出一条「⏹ aborted」。
    const { ctx, fetchCalls } = loadAgentTuiModule({
        open: false,
        fetchResponse: () => Promise.reject(new Error('offline')),
    });
    evaluate(ctx, 'getAgentTuiState("n1").status = "busy"');
    fn(ctx, 'sendAgentAbort')('n1', 'p1');
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(fetchCalls.length, 1);
    const state = fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    // 两个要求同时成立：按钮不能卡住（#221 主症状），但也不能谎报已停止。
    assert.equal(state.status, 'stopping');
    const texts = state.messages.map(item => item.text);
    assert.ok(!texts.some(text => /aborted|已停止/.test(text)), `失败却写成已停止：${JSON.stringify(texts)}`);
    assert.deepEqual(texts, ['⚠️ 停止请求未送达，agent 可能仍在生成']);
});

test('process_exited / socket.onclose 也要给末条流式消息定型（#222 同症状残留）', () => {
    // turn_end 不来了（进程崩溃 / WS 断开），流式标记还挂在末条上，
    // 下一轮的 delta 会直接续写进上一轮末尾——跟 #222 同一个用户可见症状。
    const { ctx } = loadAgentTuiModule({ open: true });
    const socket = fn(ctx, 'connectAgentWebSocket')('n1', 'p1');
    evaluate(ctx, `(() => {
        const state = getAgentTuiState("n1");
        state.messages.push({ role: "assistant", text: "上一轮", streaming: true });
        state.thinking = [{ text: "在想", streaming: true }];
    })()`);
    socket.onclose();
    let state = fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    assert.equal('streaming' in state.messages[0], false, '断开后 messages 末条的流式标记必须清掉');
    // #322 票 D：finalize 现在把本轮 thinking 归档成 role=thinking 消息（inline 折叠），
    // 不再保留 state.thinking 条目——两条旧症状（流式续写/历史堆积）一并防。
    assert.deepEqual(state.thinking, [], '归档后 state.thinking 清空');
    const archived = state.messages.find(m => m.role === 'thinking');
    assert.ok(archived && archived.text === '在想', 'thinking 必须归档进消息流');

    evaluate(ctx, `(() => {
        const state = getAgentTuiState("n1");
        state.messages.push({ role: "assistant", text: "出错前", streaming: true });
    })()`);
    const newSocket = fn(ctx, 'connectAgentWebSocket')('n1', 'p1', { force: true });
    newSocket.onerror();
    state = fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    assert.equal('streaming' in state.messages[state.messages.length - 1], false, '当前连接的 onerror 要收尾；已关闭旧连接不可污染新视图');

    fn(ctx, 'handleAgentEvent')('n1', { type: 'process_exited' });
    evaluate(ctx, `(() => {
        const state = getAgentTuiState("n1");
        state.messages.push({ role: "assistant", text: "崩溃前", streaming: true });
    })()`);
    fn(ctx, 'handleAgentEvent')('n1', { type: 'process_exited' });
    state = fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    assert.equal('streaming' in state.messages[state.messages.length - 1], false,
        'process_exited 同样要收尾流式消息');
});

test('turn_end：末条流式消息是空串时，正文要从 turn_end 补进来（#222 边界）', () => {
    // 去重按「本轮是否有流式末条」判断，但如果 delta 全是空串，末条 text 为空，
    // 无条件跳过 turn_end 会让本轮一个字都不显示。
    const { ctx } = loadAgentTuiModule();
    evaluate(ctx, `(() => {
        getAgentTuiState("n1").messages.push({ role: "assistant", text: "", streaming: true });
    })()`);
    fn(ctx, 'handleAgentEvent')('n1', { type: 'turn_end', message: { content: [{ type: 'text', text: '完整回复' }] } });
    const state = fromContext(evaluate(ctx, 'getAgentTuiState("n1")'));
    assert.deepEqual(state.messages, [{ role: 'assistant', text: '完整回复' }]);
});

test('sendAgentSteer / sendAgentFollowUp: WS 未连接回退 HTTP（URL 与 body），系统消息照样回写', () => {
    const { ctx, ws, fetchCalls } = loadAgentTuiModule({ open: false });
    evaluate(ctx, 'getAgentTuiState("n1").status = "busy"');
    fn(ctx, 'sendAgentSteer')('n1', 'proj/2', '往左');
    fn(ctx, 'sendAgentFollowUp')('n1', 'p1', '下一步');
    assert.equal(ws.sent.length, 0, 'ws 不可用，什么都不走 socket');
    assert.equal(fetchCalls.length, 2);
    assert.equal(fromContext(fetchCalls[0].url), '/api/projects/proj%2F2/agent-nodes/n1/steer');
    assert.deepEqual(inputPayloadForLegacyAssertion(fetchCalls[0].opts.body), { message: '往左' });
    assert.equal(fromContext(fetchCalls[1].url), '/api/projects/p1/agent-nodes/n1/follow_up');
    assert.deepEqual(inputPayloadForLegacyAssertion(fetchCalls[1].opts.body), { message: '下一步' });
    assert.deepEqual(fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages')).map(inputMessageForLegacyAssertion),
        [{ role: 'system', text: '↪ steer: 往左' }, { role: 'system', text: '⏳ follow_up queued: 下一步' }],
        '#221：HTTP 路径也要回写系统提示，不然历史区少一条记录');
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

// ── #392：请求与权威接收（不再把未确认设置发布到持久节点）──────────────
test('setAgentModel / setAgentAutonomy 通过权威接收器，不做乐观节点写；纯会话保留运行态', async () => {
    const modelCanvas = { revision: 1, nodes: [{ id: 'n1', type: 'agent', model: 'gpt-x' }] };
    const autonomyCanvas = { revision: 2, nodes: [{ id: 'n1', type: 'agent', model: 'gpt-x', autonomy: 'autonomous' }] };
    const { ctx, fetchCalls, receivedCanvases } = loadAgentTuiModule({
        nodes: [{ id: 'n1', type: 'agent', model: 'old', autonomy: 'cautious' }],
        fetchResponse: [
            { ok: true, json: async () => ({ success: true, model: 'gpt-x', persisted: true, canvas: modelCanvas }) },
            { ok: true, json: async () => ({ success: true, autonomy: 'autonomous', canvas: autonomyCanvas }) },
            { ok: true, json: async () => ({ success: true, model: 'gpt-y', persisted: false }) },
        ],
    });
    const pendingModel = fn(ctx, 'setAgentModel')('n1', 'proj/2', 'gpt-x');
    assert.equal(evaluate(ctx, 'getAgentTuiState("n1").model'), '');
    assert.equal(evaluate(ctx, 'canvasState.nodes[0].model'), 'old', 'autosave 不能偷存未确认的模型');
    // 保留模型/自治端点现有 encodeURI 行为。
    assert.equal(fromContext(fetchCalls[0].url), '/api/projects/proj/2/agent-nodes/n1/model');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls[0].opts.body)), { model: 'gpt-x', user_edit: true });
    await pendingModel;
    assert.deepEqual(receivedCanvases, [modelCanvas]);

    const pendingAutonomy = fn(ctx, 'setAgentAutonomy')('n1', 'p1', 'autonomous');
    assert.equal(evaluate(ctx, 'canvasState.nodes[0].autonomy'), 'cautious');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls[1].opts.body)), { autonomy: 'autonomous', user_edit: true });
    await pendingAutonomy;
    assert.deepEqual(receivedCanvases, [modelCanvas, autonomyCanvas]);
    assert.equal(evaluate(ctx, 'canvasState.nodes[0].autonomy'), 'cautious', '记录器不实现 merge；TUI 也不得直写');

    await fn(ctx, 'setAgentModel')('ghost', 'p1', 'gpt-y');
    assert.equal(evaluate(ctx, 'getAgentTuiState("ghost").model'), 'gpt-y');
    assert.equal(ctx.canvasState.nodes.length, 1, '纯会话不制造画布节点');
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
test('agentCardStatusHtml: 无会话时显示就绪；各状态映射到各自 class/文案', () => {
    const { ctx } = loadAgentTuiModule();
    const card = fn(ctx, 'agentCardStatusHtml');
    // #317 无障碍：状态 span 带 role="status"（canvas-agent-tui.js:898,924）。
    assert.equal(fromContext(card('missing')),
        '<span class="agent-card-status agent-tui-status-idle" role="status">就绪</span>');
    for (const [status, cls, text] of [
        ['idle', 'agent-tui-status-idle', '就绪'],
        ['busy', 'agent-tui-status-busy', '运行中'],
        ['connecting', 'agent-tui-status-connecting', '连接中'],
        // #220：这两个以前在卡片侧没有映射，掉进默认分支→显示「空闲」。
        ['retrying', 'agent-tui-status-busy', '重试中'],
        ['error', 'agent-tui-status-error', '错误'],
        ['crashed', 'agent-tui-status-error', '已崩溃'],
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
test('agentCardStatusHtml 使用统一状态表，r3 正文不重复渲染头部（#220/#421）', () => {
    // 两处各自一遍 switch 就是这次不一致的根因，所以钉「两处一致」而不只钉文案。
    const { ctx } = loadAgentTuiModule();
    const card = fn(ctx, 'agentCardStatusHtml');
    const body = fn(ctx, 'agentNodeBodyHtml');
    const statusText = (html, scope) => {
        // 状态 class 后可能还有别的属性（#317：卡片侧带 role="status"）。
        const match = new RegExp(`class="agent-${scope}-status agent-tui-status-[a-z]+"[^>]*>([^<]*)<`).exec(html);
        assert.ok(match, `没找到 ${scope} 状态文案: ${html}`);
        return match[1];
    };
    for (const status of ['busy', 'connecting', 'retrying', 'error', 'crashed', 'disconnected', 'idle']) {
        const nodeId = `n-${status}`;
        evaluate(ctx, `getAgentTuiState("${nodeId}").status = ${JSON.stringify(status)}`);
        const fromCard = statusText(fromContext(card(nodeId)), 'card');
        const presentation = fn(ctx, 'agentStatusPresentation')(fn(ctx, 'getAgentTuiState')(nodeId));
        assert.equal(fromCard, presentation.statusText, `${status}：外层唯一状态使用同一表`);
        assert.doesNotMatch(fromContext(body({ id: nodeId, type: 'agent' })), /class="agent-tui-header"/, '正文不重复渲染头部');
    }
});

test('agentNodeBodyHtml: retrying→重试中、crashed→已崩溃（卡片行没有这两个映射，见 #220）', () => {
    const { ctx } = loadAgentTuiModule();
    const body = fn(ctx, 'agentNodeBodyHtml');
    for (const [status, text] of [['retrying', '重试中'], ['crashed', '已崩溃']]) {
        evaluate(ctx, `getAgentTuiState("n-${status}").status = ${JSON.stringify(status)}`);
        const html = fromContext(fn(ctx, 'agentCardStatusHtml')(`n-${status}`));
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

test('agentNodeBodyHtml: runtime controls are available only during the corresponding operations', () => {
    const { ctx } = loadAgentTuiModule();
    const node = { id: 'n1', type: 'agent' }, body = () => fn(ctx, 'agentNodeBodyHtml')(node);
    const state = fn(ctx, 'getAgentTuiState')('n1');
    for (const status of ['idle', 'busy', 'retrying', 'waiting_confirm', 'stopping']) {
        state.status = status;
        const html = body();
        for (const hook of ['steer', 'followup', 'abort']) {
            const visible = ['busy', 'retrying'].includes(status) || hook === 'abort' && status === 'waiting_confirm';
            const button = new RegExp(`<button[^>]*data-agent-${hook}=[^>]*>`).exec(html);
            assert.equal(Boolean(button), visible, `${status}/${hook}`);
            if (button) assert.doesNotMatch(button[0], / disabled/);
        }
    }
    state.status = 'busy'; state.stopPending = true;
    assert.doesNotMatch(body(), /data-agent-(steer|followup|abort)=/);
});

test('agentNodeBodyHtml: 模型触发器、自治分段、更多菜单分支按节点真值显示', () => {
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
    assert.match(html, /data-agent-model-picker="n1"[^>]*title="gpt-b"[^>]*><span[^>]*>模型<\/span><b>gpt-b<\/b>/);
    assert.ok(!html.includes('gpt-a'), '未打开选择器不拉取或伪造模型池');
    assert.match(html, /data-agent-autonomy-choice="standard" aria-pressed="true">每批/);
    const more = fn(ctx, 'agentHeadActionButtonsHtml')({ id: 'n1', branches: [{ id: 'b2', name: '支线' }], current_branch: 'b2' });
    assert.ok(more.includes('<option value="b2" selected>'), more);
    assert.ok(!html.includes('agent-tui-title'), '标题归外层 header');

    const plain = fromContext(body({ id: 'n2', type: 'agent' }));
    assert.doesNotMatch(plain, /data-agent-more-branch/);
    assert.doesNotMatch(fn(ctx, 'agentHeadActionButtonsHtml')({ id:'n2' }), /data-agent-more-branch/, '无分支时更多菜单也无分支下拉');
    assert.match(plain, /data-agent-autonomy-choice="cautious" aria-pressed="true">每张/, '默认自治级别必须是谨慎');
    assert.ok(plain.includes('agent-tui-empty'), '空会话要有占位文案');
});

test('agentNodeBodyHtml: #322 思考 inline 折叠——默认收起不展开，thinkingVisible=全展开，全转义', () => {
    const { ctx } = loadAgentTuiModule();
    const body = fn(ctx, 'agentNodeBodyHtml');
    evaluate(ctx, 'const s = getAgentTuiState("n1"); s.thinking = [{text:"<i>私密</i>"}]');
    const collapsed = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.ok(collapsed.includes('agent-tui-thought'), 'live thinking 必须渲染为 inline details（默认收起）');
    assert.doesNotMatch(collapsed, /<details[^>]*agent-tui-thought[^>]*\sopen(?:\s|>)/, '默认不得带 open');
    assert.ok(collapsed.includes('&lt;i&gt;私密&lt;/i&gt;'), collapsed);
    evaluate(ctx, 'getAgentTuiState("n1").thinkingVisible = true');
    const opened = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.match(opened, /<details[^>]*agent-tui-thought-live[^>]*\sopen>/);
    // 已归档思考（role=thinking 消息）同样 inline details，默认收起
    evaluate(ctx, 'getAgentTuiState("n1").thinking = []; getAgentTuiState("n1").thinkingVisible = false; getAgentTuiState("n1").messages.push({role:"thinking", text:"旧思考<b>"})');
    const arc = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.match(arc, /<details class="agent-tui-thought"[^>]*>/);
    assert.ok(!arc.includes('思考中'), '清空 live thinking 后不得残留旧条目');
    assert.ok(arc.includes('旧思考&lt;b&gt;'), arc);
});

test('agentNodeBodyHtml: #322/D2 工具段分轮、首调用稳定 key、默认折叠 + 耗时展示', () => {
    const { ctx } = loadAgentTuiModule();
    const body = fn(ctx, 'agentNodeBodyHtml');
    evaluate(ctx, `(() => {
        const s = getAgentTuiState("n1"); s._turnSeq = 1;
        s.toolActivities.set("a1", {name:"shell",status:"done",args:{command:"ls"},result:"ok",isError:false,turn:1,duration_ms:1200});
        s.toolActivities.set("a2", {name:"read",status:"done",args:{path:"x"},result:"f",isError:false,turn:1,duration_ms:300});
        s.toolActivities.set("b1", {name:"shell",status:"done",args:null,result:"y",isError:false,turn:2,duration_ms:500});
    })()`);
    const html = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.ok(html.includes('data-tool-group="tools:a1"'), html);
    assert.ok(html.includes('执行了 2 个步骤 · 总 1.5s'), html);
    assert.ok(html.includes('data-tool-group="tools:b1"'), html);
    assert.ok(html.includes('执行了 1 个步骤 · 总 0.5s'), html);
    assert.ok(html.includes('agent-tui-tool-duration">1.2s'), html);
    // D2：运行中也默认折叠，不能丢失步骤数。
    evaluate(ctx, 'getAgentTuiState("n1").toolActivities.set("c9", {name:"shell",status:"running",args:null,result:null,isError:false,turn:3,duration_ms:null})');
    const running = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.match(running, /<details class="agent-tui-tool-group agent-tui-tool-group-running" data-tool-group="tools:c9">/);
    assert.ok(running.includes('1 个步骤执行中…'), running);
    assert.ok(!running.includes('执行了 1 个步骤 · 执行中…'), '进行中的调用不能计入已执行数量');
});

test('agentNodeBodyHtml: #322 todo 工具卡渲染 checklist（☐/▶/☑），无列表数据不渲染', () => {
    const { ctx } = loadAgentTuiModule();
    const body = fn(ctx, 'agentNodeBodyHtml');
    evaluate(ctx, `(() => {
        const s = getAgentTuiState("n1"); s._turnSeq = 1;
        s.toolActivities.set("t1", {name:"todo_list",status:"done",args:{todos:[{content:"甲",status:"completed"},{content:"乙",status:"in_progress"},{content:"丙",status:"pending"}]},result:null,isError:false,turn:1,duration_ms:10});
    })()`);
    const html = fromContext(body({ id: 'n1', type: 'agent' }));
    // #322 票 D：codex 宿主实发名 todo_list（adapter 直传 item type）也要渲染 checklist
    assert.ok(html.includes('agent-tui-checklist-item'), html);
    assert.ok(html.includes('☑') && html.includes('▶') && html.includes('☐'), html);
    assert.ok(html.includes('甲') && html.includes('乙'), html);
    evaluate(ctx, 'getAgentTuiState("n1").toolActivities.set("t1", {name:"todo_list",status:"done",args:{},result:null,isError:false,turn:1,duration_ms:10})');
    const empty = fromContext(body({ id: 'n1', type: 'agent' }));
    assert.ok(!empty.includes('agent-tui-checklist-item'), '无来源不显示（不造假）');
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
test('forkAgentSession: 成功接收权威 canvas 后清空 messages/toolActivities；失败零副作用', async () => {
    const seed = (fetchResponse) => {
        const m = loadAgentTuiModule({ nodes: [{ id: 'n1', type: 'agent' }], fetchResponse });
        fn(m.ctx, 'handleAgentEvent')('n1', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta: 'old' } });
        fn(m.ctx, 'handleAgentEvent')('n1', { type: 'tool_execution_start', toolName: 't', toolCallId: 'c1' });
        return m;
    };

    // 成功。
    const canvas = { revision: 1, nodes: [{ id: 'n1', type: 'agent', branches: [{ id: 'b1', name: '分叉' }], current_branch: 'b1' }] };
    const ok = seed({ ok: true, json: async () => ({ success: true, branches: [{ id: 'b1', name: '分叉' }], current_branch: 'b1', canvas }) });
    await fn(ok.ctx, 'forkAgentSession')('n1', 'proj/9', 'entry-7', '新分支');
    assert.equal(fromContext(ok.fetchCalls.at(-1).url), '/api/projects/proj/9/agent-nodes/n1/fork');
    assert.deepEqual(JSON.parse(fromContext(ok.fetchCalls.at(-1).opts.body)), { entry_id: 'entry-7', name: '新分支', user_edit: true });
    assert.deepEqual(ok.receivedCanvases, [canvas]);
    assert.deepEqual(fromContext(evaluate(ok.ctx, 'canvasState.nodes[0]')),
        { id: 'n1', type: 'agent' }, 'TUI 不复制权威接收器的节点合并');
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

test('switchAgentBranch: 成功接收 canvas 并清空会话；失败零副作用', async () => {
    const seed = (fetchResponse) => {
        const m = loadAgentTuiModule({
            nodes: [{ id: 'n1', type: 'agent', branches: [{ id: 'b1', name: '主' }, { id: 'b2', name: '侧' }], current_branch: 'b1' }],
            fetchResponse,
        });
        fn(m.ctx, 'handleAgentEvent')('n1', { type: 'turn_end', message: { content: [{ type: 'text', text: 'x' }] } });
        return m;
    };
    const canvas = { revision: 2, nodes: [{ id: 'n1', type: 'agent', current_branch: 'b2' }] };
    const ok = seed({ ok: true, json: async () => ({ success: true, current_branch: 'b2', canvas }) });
    await fn(ok.ctx, 'switchAgentBranch')('n1', 'p1', 'b2');
    assert.equal(fromContext(ok.fetchCalls.at(-1).url), '/api/projects/p1/agent-nodes/n1/switch-branch');
    assert.deepEqual(JSON.parse(fromContext(ok.fetchCalls.at(-1).opts.body)), { branch_id: 'b2', user_edit: true });
    assert.deepEqual(ok.receivedCanvases, [canvas]);
    assert.equal(fromContext(evaluate(ok.ctx, 'canvasState.nodes[0].current_branch')), 'b1', '仅接收器可更新持久分支');
    assert.deepEqual(fromContext(evaluate(ok.ctx, 'getAgentTuiState("n1").messages')), [], '切分支清空旧会话');

    const bad = seed({ ok: false, json: async () => ({}) });
    await fn(bad.ctx, 'switchAgentBranch')('n1', 'p1', 'b2');
    assert.equal(fromContext(evaluate(bad.ctx, 'canvasState.nodes[0].current_branch')), 'b1', '失败保留旧分支');
    assert.equal(fromContext(evaluate(bad.ctx, 'getAgentTuiState("n1").messages.length')), 1, '失败不清会话');
});

// #317：fork 选择器从两次 prompt() 换成画布自有 <dialog>（canvas-agent-tui.js:676-704
// agentForkPickerDialog，:705-723 showForkPicker）。harness.mjs 补了 dialog 表面
// （open/returnValue/showModal/close→close 事件 + harness.dialogs 句柄），但它不解析 innerHTML，
// 所以这里按产品 close 回调真正读取的两个选择器补 input 替身，再按「点分叉/点取消」的语义
// close(returnValue)：弹层构建、returnValue 判定、取值 trim、轮次→entryId 映射、fork POST
// 全跑的是产品代码。
function fillForkDialog(harness, dlg, { index, name, confirm = true }) {
    const radio = index === null ? null : harness.makeElement('input');
    if (radio) radio.value = String(index);
    const nameInput = harness.makeElement('input');
    nameInput.value = name;
    dlg.querySelector = (sel) => (
        sel === 'input[name="forkidx"]:checked' ? radio
            : sel === 'input[name="branchname"]' ? nameInput : null);
    dlg.close(confirm ? 'ok' : 'cancel');
}

test('showForkPicker: <dialog> 选轮次与分支名后分叉（entry_id + name）；空列表/取消/无选中不分叉', async () => {
    const listResponse = { ok: true, json: async () => ({ messages: [{ text: '第一轮', entryId: 'e1' }, { text: '第二轮', entryId: 'e2' }] }) };
    const ok = loadAgentTuiModule({
        nodes: [{ id: 'n1', type: 'agent' }],
        fetchResponse: (url) => url.endsWith('/fork-messages') ? listResponse : { ok: true, json: async () => ({}) },
    });
    const picking = fn(ok.ctx, 'showForkPicker')('n1', 'p1');
    await new Promise(r => setImmediate(r));
    assert.equal(ok.harness.dialogs.length, 1, '#317：fork 选择器是画布自有 <dialog>');
    const dlg = ok.harness.dialogs[0];
    assert.equal(dlg.className, 'agent-fork-dialog');
    assert.equal(dlg.open, true, 'showModal 必须被调用（不 open 用户看不到选择器）');
    assert.equal(dlg.parentNode === ok.ctx.document.body, true, '弹层挂到 document.body');
    // 原两次 prompt 的文案映射逐字搬到弹层：标题=「从哪轮分叉？」、编号消息列表、分支名输入位。
    assert.ok(dlg.innerHTML.includes('从哪轮分叉？'), dlg.innerHTML);
    assert.ok(dlg.innerHTML.includes('agent-fork-idx">1.</span><span>第一轮')
        && dlg.innerHTML.includes('agent-fork-idx">2.</span><span>第二轮'), '弹层列出编号消息列表');
    assert.ok(dlg.innerHTML.includes('分支名称（留空自动）'), '分支名输入位（原第二问）');
    assert.ok(dlg.innerHTML.includes('name="forkidx" value="1" checked'), '默认选中最后一轮');
    assert.ok(dlg.innerHTML.includes('value="第二轮"'), '分支名默认值 = 最后一条消息前 30 字');
    assert.ok(dlg.innerHTML.includes('>取消</button>') && dlg.innerHTML.includes('>分叉</button>'), '取消/分叉两个动作按钮');

    fillForkDialog(ok.harness, dlg, { index: 1, name: '分叉线' });
    await picking;
    const forkCall = ok.fetchCalls.at(-1);
    assert.ok(forkCall.url.endsWith('/agent-nodes/n1/fork'), forkCall.url);
    assert.deepEqual(JSON.parse(fromContext(forkCall.opts.body)), { entry_id: 'e2', name: '分叉线', user_edit: true });
    assert.equal(dlg.open, false, 'close 后弹层收起');

    // 空消息列表：直接返回，不建弹层不分叉。
    const empty = loadAgentTuiModule({ nodes: [{ id: 'n1' }], fetchResponse: { ok: true, json: async () => ({ messages: [] }) } });
    await fn(empty.ctx, 'showForkPicker')('n1', 'p1');
    assert.equal(empty.harness.dialogs.length, 0, '空列表不弹选择器');
    assert.equal(empty.fetchCalls.length, 1, '只有 fork-messages 一次请求');

    const one = { ok: true, json: async () => ({ messages: [{ text: '只有一轮', entryId: 'e9' }] }) };

    // 点「取消」（returnValue='cancel'）不分叉。
    const cancel = loadAgentTuiModule({ nodes: [{ id: 'n1' }], fetchResponse: one });
    const cancelling = fn(cancel.ctx, 'showForkPicker')('n1', 'p1');
    await new Promise(r => setImmediate(r));
    fillForkDialog(cancel.harness, cancel.harness.dialogs[0], { index: 0, name: 'x', confirm: false });
    await cancelling;
    assert.equal(cancel.fetchCalls.length, 1, '取消 → 不分叉');

    // 没有选中轮次（input[name="forkidx"]:checked 查不到）即使点分叉也不分叉。
    const none = loadAgentTuiModule({ nodes: [{ id: 'n1' }], fetchResponse: one });
    const nonPicking = fn(none.ctx, 'showForkPicker')('n1', 'p1');
    await new Promise(r => setImmediate(r));
    fillForkDialog(none.harness, none.harness.dialogs[0], { index: null, name: 'x' });
    await nonPicking;
    assert.equal(none.fetchCalls.length, 1, '无选中轮次 → 不分叉');

    // 越界序号只能来自不一致的 DOM；显式提示且不发 fork。
    const oob = loadAgentTuiModule({ nodes: [{ id: 'n1' }], fetchResponse: one });
    const oobPicking = fn(oob.ctx, 'showForkPicker')('n1', 'p1');
    await new Promise(r => setImmediate(r));
    fillForkDialog(oob.harness, oob.harness.dialogs[0], { index: 99, name: '' });
    await oobPicking;
    assert.equal(oob.fetchCalls.length, 1, '越界序号 → 不分叉');
    assert.match(oob.harness.toasts.at(-1).message, /所选分叉点无效/);

    // 分支名留空（trim 后空串）：#317 起仍分叉，交服务端自动命名（旧 prompt 的「空白不选」语义作废）。
    const blank = loadAgentTuiModule({ nodes: [{ id: 'n1' }], fetchResponse: one });
    const blankPicking = fn(blank.ctx, 'showForkPicker')('n1', 'p1');
    await new Promise(r => setImmediate(r));
    fillForkDialog(blank.harness, blank.harness.dialogs[0], { index: 0, name: '   ' });
    await blankPicking;
    assert.equal(blank.fetchCalls.length, 2, '留空分支名仍发 fork');
    assert.deepEqual(JSON.parse(fromContext(blank.fetchCalls.at(-1).opts.body)), { entry_id: 'e9', name: '', user_edit: true });
});

// ── #325 票 G：外部 CLI 接管（snippet 端点消费 + 头部按钮 + 弹层内容）──────
test('fetchAgentCliSnippet: GET external-cli-snippet?node= 返回数据；非 2xx → null+console.error 零副作用', async () => {
    const ok = loadAgentTuiModule({
        nodes: [{ id: 'n1', type: 'agent' }],
        fetchResponse: { ok: true, json: async () => ({ mcp_url: 'http://127.0.0.1:3000/api/mcp?project=p1&node=n1', codex_cmd: 'CODEX_CMD', claude_cmd: 'CLAUDE_CMD' }) },
    });
    const data = await fn(ok.ctx, 'fetchAgentCliSnippet')('n1', 'p1');
    assert.equal(fromContext(ok.fetchCalls.at(-1).url), '/api/projects/p1/agent-nodes/external-cli-snippet?node=n1');
    assert.equal(data.codex_cmd, 'CODEX_CMD');
    assert.equal(data.mcp_url, 'http://127.0.0.1:3000/api/mcp?project=p1&node=n1');

    const bad = loadAgentTuiModule({ nodes: [{ id: 'n1', type: 'agent' }], fetchResponse: { ok: false, json: async () => ({}) } });
    const none = await fn(bad.ctx, 'fetchAgentCliSnippet')('n1', 'p1');
    assert.equal(none, null, '失败返回 null（fromContext 把 null 原样带回）');
    assert.ok(bad.consoleErrors.some(m => m.startsWith('Failed to get CLI snippet:')), bad.consoleErrors.join('|'));
});

test('agentCliSnippetDialogHtml: 弹层含两条命令与 mcp_url（转义），复制按钮带 data 属性', () => {
    const { ctx } = loadAgentTuiModule({ nodes: [{ id: 'n1', type: 'agent' }] });
    const html = fn(ctx, 'agentCliSnippetDialogHtml')({
        mcp_url: 'http://127.0.0.1:3000/api/mcp?project=p&node=n1',
        codex_cmd: `codex exec -c 'mcp_servers.firefly.url="u<1>"' "列出画布节点"`,
        claude_cmd: 'claude mcp add --transport http firefly "u"',
    });
    assert.ok(html.includes('agent-cli-codex-cmd'), 'codex 命令行存在');
    assert.ok(html.includes('agent-cli-claude-cmd'), 'claude 命令行存在');
    assert.ok(html.includes('data-agent-cli-copy="codex"') && html.includes('data-agent-cli-copy="claude"'), '两条各有复制按钮');
    assert.ok(html.includes('u&lt;1&gt;'), '命令文本必须 HTML 转义');
    assert.ok(html.includes('mcp_url') === false || html.includes('api/mcp'), 'url 展示存在');
});

test('#325/#421: agent 外层头部更多菜单保留「由外部 CLI 接管」入口', () => {
    const { ctx } = loadAgentTuiModule({ nodes: [{ id: 'n1', type: 'agent' }] });
    const html = fn(ctx, 'agentHeadActionButtonsHtml')({ id: 'n1', type: 'agent', title: 'A' });
    assert.ok(html.includes('data-agent-more-cli'), '接管入口移至外层更多菜单');
    assert.ok(html.includes('agent-tui-more'), '菜单保留真实 CLI 接线');
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

// ── 控件绑定（makeElement 按实例覆写 querySelector/querySelectorAll，
//    test-canvas-connections.mjs 的 wireWorldQueries 已证同法可行）──────────
const ev = (type, extra = {}) => ({ type, preventDefault() {}, stopPropagation() {}, ...extra });

function buildAgentControlTree(harness) {
    const el = harness.makeElement('div');
    const input = harness.makeElement('textarea');
    const thinking = harness.makeElement('button');
    const steer = harness.makeElement('button');
    const followup = harness.makeElement('button');
    const abort = harness.makeElement('button');
    const approve = harness.makeElement('button');
    const reject = harness.makeElement('button');
    const model = harness.makeElement('button');
    const autonomy = harness.makeElement('button');
    autonomy.setAttribute('data-agent-autonomy-choice', 'autonomous');
    const branchSel = harness.makeElement('select');
    branchSel.setAttribute('data-agent-more-branch', '');
    thinking.setAttribute('data-agent-thinking', 'n1');
    const menu = harness.makeElement('details');
    const summary = harness.makeElement('summary');
    menu.querySelector = selector => selector === 'summary' ? summary : null;
    thinking.closest = branchSel.closest = selector => selector === '.agent-tui-more' ? menu : null;
    const forkBtn = harness.makeElement('button');
    const bodyEl = harness.makeElement('div');
    const toolRow = harness.makeElement('div');
    toolRow.setAttribute('data-tool-call-id', 'c1');
    el.querySelectorAll = (sel) => ({ '[data-tool-call-id]': [toolRow], '[data-agent-abort]': [abort],
        '[data-agent-model-picker],[data-agent-effort-picker]': [model], '[data-agent-autonomy-choice]': [autonomy],
        '.agent-tui-more': [menu], '[data-agent-more-fork],[data-agent-more-cli],[data-agent-more-view],[data-agent-more-branch],[data-agent-thinking]': [thinking, branchSel],
    }[sel] || []);
    el.querySelector = (sel) => ({
        '[data-agent-tui-input]': input,
        '[data-agent-steer]': steer,
        '[data-agent-followup]': followup,
        '[data-agent-abort]': abort,
        '[data-agent-confirm-approve]': approve,
        '[data-agent-confirm-reject]': reject,
        '[data-agent-fork]': forkBtn,
        '[data-agent-tui-body]': bodyEl,
    }[sel] ?? null);
    return { el, input, menu, thinking, steer, followup, abort, approve, reject, model, autonomy, branchSel, forkBtn, toolRow };
}

test('bindAgentNodeControls: Enter 发送 prompt（trim/清空）；busy → follow_up；Shift+Enter/纯空白 no-op', () => {
    const { ctx, harness, ws } = loadAgentTuiModule({ open: true, nodes: [{ id: 'n1', type: 'agent' }] });
    const tree = buildAgentControlTree(harness);
    fn(ctx, 'bindAgentNodeControls')(tree.el, { id: 'n1', type: 'agent' });

    tree.input.value = '  画一只猫  ';
    tree.input.dispatchEvent(ev('keydown', { key: 'Enter', shiftKey: false }));
    assert.equal(tree.input.value, '', '发送后清空输入框');
    assert.deepEqual(inputPayloadForLegacyAssertion(ws.sent.at(-1)), { type: 'prompt', message: '画一只猫', images: [] }, 'trim 后发送');
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'busy');

    // busy 时 Enter 进后续队列。
    tree.input.value = '再来一张';
    tree.input.dispatchEvent(ev('keydown', { key: 'Enter', shiftKey: false }));
    assert.deepEqual(inputPayloadForLegacyAssertion(ws.sent.at(-1)), { type: 'follow_up', message: '再来一张' });
    const msgs = fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages'));
    assert.equal(msgs.at(-1).text, '⏳ follow_up queued: 再来一张');

    // Shift+Enter 换行不发送；纯空白不发送也不清空。
    const sentBefore = ws.sent.length;
    tree.input.value = '换行';
    tree.input.dispatchEvent(ev('keydown', { key: 'Enter', shiftKey: true }));
    assert.equal(ws.sent.length, sentBefore, 'Shift+Enter 不发送');
    assert.equal(tree.input.value, '换行', 'Shift+Enter 内容保留');
    tree.input.value = '   ';
    tree.input.dispatchEvent(ev('keydown', { key: 'Enter', shiftKey: false }));
    assert.equal(ws.sent.length, sentBefore, '纯空白不发送');
    assert.equal(tree.input.value, '   ', '未发送则不清空');
});

test('bindAgentNodeControls: thinking 切换 / steer / follow-up / abort 按钮', () => {
    const { ctx, harness, ws } = loadAgentTuiModule({ open: true, nodes: [{ id: 'n1', type: 'agent' }] });
    const tree = buildAgentControlTree(harness);
    fn(ctx, 'bindAgentNodeControls')(tree.el, { id: 'n1', type: 'agent' });
    fn(ctx, 'handleAgentEvent')('n1', { type: 'agent_start' });

    // thinking 切换：false → true → false。
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").thinkingVisible')), false);
    tree.thinking.dispatchEvent(ev('click'));
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").thinkingVisible')), true);
    tree.thinking.dispatchEvent(ev('click'));
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").thinkingVisible')), false);

    // steer：读输入内容，发完清空；空输入不发。
    tree.input.value = '往左';
    tree.steer.dispatchEvent(ev('click'));
    assert.equal(tree.input.value, '');
    assert.deepEqual(inputPayloadForLegacyAssertion(ws.sent.at(-1)), { type: 'steer', message: '往左' });
    const sentBefore = ws.sent.length;
    tree.steer.dispatchEvent(ev('click'));
    assert.equal(ws.sent.length, sentBefore, '空输入 steer 不发送');

    // follow-up。
    tree.input.value = '下一步';
    tree.followup.dispatchEvent(ev('click'));
    assert.deepEqual(inputPayloadForLegacyAssertion(ws.sent.at(-1)), { type: 'follow_up', message: '下一步' });

    // Receipt is not termination: keep parameters locked until authoritative feedback.
    tree.abort.dispatchEvent(ev('click'));
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'stopping');
    const msgs = fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages'));
    assert.equal(msgs.at(-1).role, 'system');
    assert.equal(msgs.at(-1).text, '⏹ 停止请求已发送，等待宿主结束');
});

test('bindAgentNodeControls: confirm 批准/取消、model/autonomy/branch change、fork、工具行展开', async () => {
    const { ctx, harness, fetchCalls } = loadAgentTuiModule({
        open: false,
        nodes: [{ id: 'n1', type: 'agent', model: 'm0', autonomy: 'cautious', current_branch: 'b1' }],
        fetchResponse: (url) => {
            if (url.endsWith('/fork-messages')) return { ok: true, json: async () => ({ messages: [{ text: '第一轮', entryId: 'e1' }] }) };
            if (url.includes('/capabilities?')) return { ok: true, json: async () => ({ host:'pi', status:'available',
                models:[{id:'m0', name:'m0'}, {id:'m2', name:'m2'}], defaults:{model:null, effort:null},
                selection:{ model:new URL(url, 'http://example.test').searchParams.get('model'), efforts:[], source:'pi' } }) };
            return { ok: true, json: async () => ({}) };
        },
    });
    const create = ctx.document.createElement;
    ctx.document.createElement = tag => {
        const element = create(tag), fields = new Map();
        element.querySelector = selector => {
            if (!fields.has(selector)) fields.set(selector, harness.makeElement('button'));
            return fields.get(selector);
        };
        return element;
    };
    const tree = buildAgentControlTree(harness);
    fn(ctx, 'bindAgentNodeControls')(tree.el, { id: 'n1', type: 'agent' });
    // open:false → 命令全走 HTTP，便于断言 URL/body。

    // 无 confirm 挂起 → 点批准不发送。
    tree.approve.dispatchEvent(ev('click'));
    assert.equal(fetchCalls.length, 0);

    fn(ctx, 'handleAgentEvent')('n1', { type: 'confirm', confirm_id: 'k1', message: '删吗' });
    tree.approve.dispatchEvent(ev('click'));
    assert.equal(fromContext(fetchCalls.at(-1).url), '/api/projects/canvas-id-1/agent-nodes/n1/confirm');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls.at(-1).opts.body)), { confirm_id: 'k1', approved: true });
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").status')), 'busy');
    const msgs1 = fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages'));
    assert.equal(msgs1.at(-1).text, '✅ 已批准');

    fn(ctx, 'handleAgentEvent')('n1', { type: 'confirm', confirm_id: 'k2', message: '删吗' });
    tree.reject.dispatchEvent(ev('click'));
    assert.deepEqual(JSON.parse(fromContext(fetchCalls.at(-1).opts.body)), { confirm_id: 'k2', approved: false });
    const msgs2 = fromContext(evaluate(ctx, 'getAgentTuiState("n1").messages'));
    assert.equal(msgs2.at(-1).text, '❌ 已取消');

    // Settings are unlocked only after the confirmed operation actually ends.
    fn(ctx, 'handleAgentEvent')('n1', { type: 'agent_end' });
    // Native picker + segment declare human intent without optimistic persistence.
    tree.model.dispatchEvent(ev('click'));
    await d2Drain();
    const picker = harness.dialogs[0];
    picker.querySelector('[data-agent-picker-model]').dispatchEvent(ev('change', { target:{value:'m2'} }));
    await d2Drain();
    picker.querySelector('[data-agent-picker-apply]').dispatchEvent(ev('click'));
    assert.equal(fromContext(fetchCalls.at(-1).url), '/api/projects/canvas-id-1/agent-nodes/n1/settings');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls.at(-1).opts.body)), { host:'pi', model:'m2', effort:null, user_edit:true });
    assert.equal(fromContext(evaluate(ctx, 'canvasState.nodes[0].model')), 'm0');
    await d2Drain();

    tree.autonomy.dispatchEvent(ev('click'));
    assert.equal(fromContext(fetchCalls.at(-1).url), '/api/projects/canvas-id-1/agent-nodes/n1/autonomy');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls.at(-1).opts.body)), { autonomy: 'autonomous', user_edit: true });
    assert.equal(fromContext(evaluate(ctx, 'canvasState.nodes[0].autonomy')), 'cautious');

    // branch：空值不发，非空 → switch-branch。
    const beforeBranch = fetchCalls.length;
    for (const value of ['', 'b1']) {
        tree.branchSel.value = value;
        tree.branchSel.dispatchEvent(ev('change'));
    }
    assert.equal(fetchCalls.length, beforeBranch, '空分支或当前分支不发送');
    tree.branchSel.value = 'b2';
    tree.branchSel.dispatchEvent(ev('change'));
    assert.equal(fetchCalls.length, beforeBranch + 1);
    assert.equal(tree.branchSel.value, 'b1');
    assert.equal(fromContext(fetchCalls.at(-1).url), '/api/projects/canvas-id-1/agent-nodes/n1/switch-branch');
    assert.deepEqual(JSON.parse(fromContext(fetchCalls.at(-1).opts.body)), { branch_id: 'b2', user_edit: true });

    // fork 按钮 → showForkPicker（异步 fire-and-forget）：#317 起走画布自有 <dialog>。
    tree.forkBtn.dispatchEvent(ev('click'));
    await new Promise(r => setImmediate(r));
    assert.equal(harness.dialogs.length, 2, 'fork 按钮打开独立 <dialog> 选择器');
    assert.ok(harness.dialogs[1].innerHTML.includes('agent-fork-idx">1.</span><span>第一轮'), harness.dialogs[1].innerHTML);
    fillForkDialog(harness, harness.dialogs[1], { index: 0, name: '线' });
    await new Promise(r => setImmediate(r));
    const forkCall = fetchCalls.at(-1);
    assert.ok(forkCall.url.endsWith('/agent-nodes/n1/fork'), forkCall.url);
    assert.deepEqual(JSON.parse(fromContext(forkCall.opts.body)), { entry_id: 'e1', name: '线', user_edit: true });

    // 工具行：点击切换 expanded。
    fn(ctx, 'handleAgentEvent')('n1', { type: 'tool_execution_start', toolName: 'search', toolCallId: 'c1', args: { q: 1 } });
    assert.equal(evaluate(ctx, 'getAgentTuiState("n1").toolActivities.get("c1").expanded'), undefined);
    tree.toolRow.dispatchEvent(ev('click'));
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").toolActivities.get("c1").expanded')), true, '首次点击展开');
    tree.toolRow.dispatchEvent(ev('click'));
    assert.equal(fromContext(evaluate(ctx, 'getAgentTuiState("n1").toolActivities.get("c1").expanded')), false, '再次点击收起');
});

test('bindAgentTaskNodeControls: 结果链接点击选中结果节点 + 视口居中 + render；结果缺失 no-op', () => {
    const { ctx, harness, renders } = loadAgentTuiModule({ nodes: [{ id: 'A', type: 'agent' }, { id: 'R1', x: 100, y: 50 }] });
    const link = harness.makeElement('a');
    link.dataset.agentResultLink = 'R1';
    const el = harness.makeElement('div');
    el.querySelectorAll = (sel) => (sel === '[data-agent-result-link]' ? [link] : []);
    fn(ctx, 'bindAgentTaskNodeControls')(el, { id: 'A' });

    link.dispatchEvent(ev('click'));
    assert.equal(fromContext(evaluate(ctx, 'canvasState.selectedId')), 'R1');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.selectedIds')), []);
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.selectedImage')), { nodeId: '', index: -1 });
    assert.deepEqual(fromContext(renders.filter(r => r.kind === 'centerViewport').at(-1).point),
        { x: 220, y: 150 }, '视口中心 = 结果节点 + (120, 100)');
    assert.equal(renders.filter(r => r.kind === 'render').length, 1, '触发一次全量 render');

    // 结果缺失 → 不改选中不 render。
    link.dataset.agentResultLink = 'ghost';
    link.dispatchEvent(ev('click'));
    assert.equal(fromContext(evaluate(ctx, 'canvasState.selectedId')), 'R1', '缺失结果保持原选中');
    assert.equal(renders.filter(r => r.kind === 'render').length, 1, '缺失结果不触发 render');
});

// ── #284 MEDIA: 行渲染 + URL 归一化 ─────────────────────────────────────────
test('#284 normalizeAgentMediaUrl：/media/<f> 与裸文件名归一到 /assets/output/<f>，已服务前缀原样', () => {
    const { ctx } = loadAgentTuiModule();
    const norm = fn(ctx, 'normalizeAgentMediaUrl');
    assert.equal(fromContext(norm('/media/generated_image_20260304_012846_9490.png')),
        '/assets/output/generated_image_20260304_012846_9490.png');
    assert.equal(fromContext(norm('generated_image_a.png')), '/assets/output/generated_image_a.png');
    assert.equal(fromContext(norm('/assets/output/x.png')), '/assets/output/x.png');
    assert.equal(fromContext(norm('/output/x.png')), '/output/x.png');
    assert.equal(fromContext(norm('https://cdn.example/a.png')), 'https://cdn.example/a.png');
    assert.equal(fromContext(norm('D:/FF/assets/output/y.png')), '/assets/output/y.png');
    assert.equal(fromContext(norm('')), '');
});

test('#284 splitAgentMediaLines + renderAgentAssistantHtml：MEDIA 行拆出并渲染缩略图，正文仍走 markdown', () => {
    const { ctx } = loadAgentTuiModule();
    const split = fn(ctx, 'splitAgentMediaLines');
    const segs = fromContext(split('已生成。\nMEDIA:/media/gen_a.png MEDIA:/media/gen_b.png\n完成'));
    assert.equal(segs.length, 3);
    assert.deepEqual(segs[1].media, ['/media/gen_a.png', '/media/gen_b.png']);
    assert.equal(segs[0].text, '已生成。');
    const render = fn(ctx, 'renderAgentAssistantHtml');
    const html = String(fromContext(render('看图\nMEDIA:/media/gen_a.png')));
    assert.ok(html.includes('<img src="/assets/output/gen_a.png"'), `缩略图缺归一化 src：${html}`);
    assert.ok(html.includes('agent-tui-media-thumb'), '缩略图容器缺失');
    assert.ok(!html.includes('MEDIA:'), '原始 MEDIA: 文本不应再出现');
});

test('#421 assistant fences preserve multiline content, omit empty fences and do not expand literal MEDIA:', () => {
    const { ctx } = loadAgentTuiModule();
    const render = fn(ctx, 'renderAgentAssistantHtml');
    const fence = '```';
    const html = render(['before', '', fence + 'text', 'QA row: preserve canvas coordinates', 'MEDIA:/media/literal.png', fence,
        '', fence, '   ', fence, '', 'MEDIA:/media/actual.png', 'after'].join('\n'));
    assert.match(html, /<p>before<\/p><pre class="agent-md-code-block" data-lang="text"><code>QA row: preserve canvas coordinates\nMEDIA:\/media\/literal.png<\/code><\/pre>/);
    assert.equal((html.match(/<pre /g) || []).length, 1);
    assert.match(html, /<img src="\/assets\/output\/actual\.png"/);
    assert.doesNotMatch(html, /src="[^"]*literal\.png"/);
    assert.match(html, /<p>after<\/p>$/);
    assert.doesNotMatch(render(fence + 'text\n'), /<pre /, 'empty live fence never draws a gray block');
    assert.match(render(fence + 'text\n&<tag>'), /<code>&amp;&lt;tag&gt;<\/code>/, 'nonempty live fence remains readable and escaped');
});

// ── #285 会话模型池 ─────────────────────────────────────────────────────────
test('#285 applyAgentChatModelPool：provider/modelId 池生效并覆盖 getChatModels 回退池', () => {
    const { ctx } = loadAgentTuiModule({
        chatProviders: [{ id: 'custom', name: '自定义', models: ['qwen-image-2.1-pro'] }],
        modelsByProvider: { custom: ['qwen-image-2.1-pro'] },
    });
    const getModels = fn(ctx, 'getChatModels');
    assert.deepEqual(fromContext(getModels()).map(m => m.id), ['qwen-image-2.1-pro'],
        '未加载池时回退旧生成模型池（回归基线）');
    const apply = fn(ctx, 'applyAgentChatModelPool');
    const mapped = fromContext(apply({ models: [
        { id: 'bailian/qwen-plus', name: 'Qwen Plus', provider: 'bailian', model: 'qwen-plus' },
        { id: 'vllm-remote/qwen3', name: 'qwen3', provider: 'vllm-remote', model: 'qwen3' },
    ] }));
    assert.equal(mapped[0].name, 'bailian/Qwen Plus', '人可读名=provider/name');
    assert.equal(mapped[1].name, 'vllm-remote/qwen3', 'name==model 时不重复');
    assert.deepEqual(fromContext(getModels()).map(m => m.id), ['bailian/qwen-plus', 'vllm-remote/qwen3'],
        '池到位后 getChatModels 返回会话模型池');
});

test('#285 fetchAgentChatModels：走 /agent-nodes/chat-models 端点（# 号编码正确）', async () => {
    const { ctx, fetchCalls } = loadAgentTuiModule();
    const fetchModels = fn(ctx, 'fetchAgentChatModels');
    await fetchModels('proj#1');
    assert.equal(fetchCalls.length, 1, '恰一次拉取');
    const url = String(fromContext(fetchCalls[0].url));
    assert.ok(url.includes('/api/projects/proj%231/agent-nodes/chat-models'), `请求 URL 异常：${url}`);
});

// ── #323 票 E：会话框（chatFrameId）+ 参考框（refFrame / refFrameId）───────────────────────────────────
// 服务端只用节点字段（create_node_core 按 chatFrameId 落位；refFrame 由服务端从 canvas store 读），
// 这里钉的是前端字段写入 + UI 行为。withNodeOps 加载真 createSmartGroupNode（不桩产品逻辑）。

test('#323 E1 agentChatFrameAction：无框 → agent 右侧 +40 建 smart-group、写 chatFrameId；再点不重复建只居中；stale id 自愈', () => {
    const { ctx, renders } = loadAgentTuiModule({
        withNodeOps: true,
        open: false,
        nodes: [{ id: 'a1', type: 'smart-agent', title: '创作', x: 100, y: 200, w: 382 }],
    });
    const action = fn(ctx, 'agentChatFrameAction');
    const groups = () => fromContext(evaluate(ctx, 'canvasState.nodes.filter(n => n.type === NODE_TYPE_GROUP)'));
    const chatFrameId = () => fromContext(evaluate(ctx, "canvasState.nodes.find(n => n.id === 'a1').chatFrameId"));

    assert.equal(action('ghost'), null, '不存在的 agent 是 no-op');

    const r1 = action('a1');
    assert.equal(r1.action, 'created');
    assert.equal(groups().length, 1, '真实 createSmartGroupNode 建了一个 smart-group');
    assert.equal(groups()[0].x, 100 + 382 + 40, '位置 = agent.x + agent.w + 40');
    assert.equal(groups()[0].y, 200);
    assert.equal(groups()[0].title, '创作 · 会话框', '标题 = (agent.title||会话) + " · 会话框"');
    assert.deepEqual(groups()[0].items, []);
    assert.equal(r1.groupId, groups()[0].id);
    assert.equal(chatFrameId(), groups()[0].id, 'agent.chatFrameId 必须指向新框');
    assert.ok(renders.some(r => r.kind === 'render'), '创建必须 render');
    assert.ok(renders.some(r => r.kind === 'scheduleSave'), '创建必须保存');

    // 第二次点击：不重复建，只视口居中到该 frame（centerViewportOnWorldPoint 记录器）。
    const r2 = action('a1');
    assert.equal(r2.action, 'center');
    assert.equal(r2.groupId, groups()[0].id);
    assert.equal(groups().length, 1, '再点不得建第二个框');
    assert.deepEqual(fromContext(renders.filter(r => r.kind === 'centerViewport').at(-1).point),
        { x: 100 + 382 + 40 + 170, y: 200 + 143 }, '居中点 = 框真中心（x + w/2, y + h/2；w340/h286 默认，监理钉B2）');

    // stale chatFrameId（指向不存在节点）→ 按无处理（自愈降级），重建并覆盖旧 id。
    evaluate(ctx, "canvasState.nodes.find(n => n.id === 'a1').chatFrameId = 'ghost'");
    const r3 = action('a1');
    assert.equal(r3.action, 'created', 'stale id 不得当成已有框去居中');
    assert.notEqual(r3.groupId, 'ghost');
    assert.equal(groups().length, 2);
    assert.equal(chatFrameId(), r3.groupId, '新 chatFrameId 覆盖 stale id');
});

test('#323 E1 agentChatFrameAction：agent 无 title/无 w → 默认标题「会话 · 会话框」、位置按 w=240 回退', () => {
    const { ctx } = loadAgentTuiModule({
        withNodeOps: true,
        open: false,
        nodes: [{ id: 'a2', type: 'smart-agent', x: 10, y: 20 }],
    });
    const r = fn(ctx, 'agentChatFrameAction')('a2');
    assert.equal(r.action, 'created');
    const g = fromContext(evaluate(ctx, 'canvasState.nodes.filter(n => n.type === NODE_TYPE_GROUP)'));
    assert.equal(g[0].x, 10 + 240 + 40, 'w 缺失回退 240');
    assert.equal(g[0].y, 20);
    assert.equal(g[0].title, '会话 · 会话框', 'title 缺失回退「会话」');
});

test('#323 E1 agentHeadActionButtonsHtml：会话框按钮注入 .node-actions；激活态（class+✓）按框真实存在（stale id 自愈）', () => {
    const { ctx } = loadAgentTuiModule({ nodes: [{ id: 'a1', type: 'smart-agent', title: '创作' }] });
    const html = fn(ctx, 'agentHeadActionButtonsHtml');
    const liveNode = () => fromContext(evaluate(ctx, "canvasState.nodes.find(n => n.id === 'a1')"));

    let out = fromContext(html(liveNode()));
    assert.ok(out.includes('data-agent-chat-frame="a1"'), out);
    assert.ok(out.includes('data-agent-ref-frame="a1"'), out);
    assert.ok(!out.includes('agent-chat-frame-btn agent-tui-btn-active'), '无框 → 非激活态');
    assert.ok(out.includes('>创建会话框</button>'), '无框 → 文字钮「创建会话框」（监理钉B2）');

    // stale chatFrameId（指向不存在节点）→ 按无处理，按钮不得显示激活态。
    evaluate(ctx, "canvasState.nodes[0].chatFrameId = 'ghost'");
    out = fromContext(html(liveNode()));
    assert.ok(!out.includes('agent-chat-frame-btn agent-tui-btn-active'), out);
    assert.ok(out.includes('>创建会话框</button>'), 'stale → 仍显示「创建会话框」');

    // 框真实存在 → 激活 class + 文字钮变「打开会话框」（一键可达语义直接写在文案上）。
    evaluate(ctx, 'canvasState.nodes.push({ id: "g1", type: NODE_TYPE_GROUP, x: 0, y: 0, items: [] })');
    evaluate(ctx, "canvasState.nodes[0].chatFrameId = 'g1'");
    out = fromContext(html(liveNode()));
    assert.ok(out.includes('agent-chat-frame-btn agent-tui-btn-active'), out);
    assert.match(out, /data-agent-chat-frame="a1">打开会话框 <span[^>]+is-bound/, '菜单中保留「打开会话框」文字操作和绑定状态（监理钉B2）');
});

test('#323 监理钉B1/B3：新建会话框带 chatFrame/chatFrameFor 机器标记；已有框点击=居中到框真中心；卡面状态段两态可见', () => {
    const { ctx, renders } = loadAgentTuiModule({ withNodeOps: true, open: false,
        nodes: [{ id: 'a1', type: 'smart-agent', title: '创作', x: 100, y: 50, w: 480 }] });
    const chat = fn(ctx, 'agentChatFrameAction');
    const r1 = fromContext(chat('a1'));
    assert.equal(r1.action, 'created');
    const g = fromContext(evaluate(ctx, 'canvasState.nodes.filter(n => n.type === NODE_TYPE_GROUP)'));
    assert.equal(g[0].chatFrame, true, '监理钉B1：框节点带 chatFrame 机器标记');
    assert.equal(g[0].chatFrameFor, 'a1', '监理钉B1：chatFrameFor 指向归属 agent（其他 agent 可读）');
    assert.equal(g[0].title, '创作 · 会话框', '监理钉B1：框面可见归属标记（title 渲染在 group 卡 head）');
    // 已有框 → 居中到框真中心（x + w/2, y + h/2）。
    evaluate(ctx, "canvasState.nodes.find(n => n.type === NODE_TYPE_GROUP).w = 800; canvasState.nodes.find(n => n.type === NODE_TYPE_GROUP).h = 360");
    const r2 = fromContext(chat('a1'));
    assert.equal(r2.action, 'center', '绑定态点击=跳转不重建');
    assert.ok(renders.some(r => r.kind === 'centerViewport'), '绑定态点击必须触发居中');
    // 卡面状态段（钉B3）：绑定/未绑定两态。
    const status = fn(ctx, 'agentChatFrameStatusHtml');
    const agent = () => fromContext(evaluate(ctx, "canvasState.nodes.find(n => n.id === 'a1')"));
    let s = fromContext(status(agent()));
    assert.ok(s.includes('is-bound') && s.includes('会话框 ✓') && s.includes('创作 · 会话框'), s);
    evaluate(ctx, "delete canvasState.nodes.find(n => n.id === 'a1').chatFrameId");
    s = fromContext(status(agent()));
    assert.ok(s.includes('is-unbound') && s.includes('会话框 —'), s);
});

test('#323 E1 bindAgentHeadControls：点「会话框」按钮 → 真实创建；再点 → 居中不重复建', () => {
    const { ctx, harness, renders } = loadAgentTuiModule({
        withNodeOps: true,
        open: false,
        nodes: [{ id: 'a1', type: 'smart-agent', x: 0, y: 0 }],
    });
    const chatBtn = harness.makeElement('button');
    chatBtn.setAttribute('data-agent-chat-frame', 'a1');
    const menu = harness.makeElement('details');
    const summary = harness.makeElement('summary');
    summary.focus = options => assert.equal(options.preventScroll, true);
    menu.querySelector = selector => selector === 'summary' ? summary : null;
    chatBtn.closest = selector => selector === '.agent-tui-more' ? menu : null;
    menu.open = true;
    const el = harness.makeElement('div');
    el.querySelectorAll = sel => sel.split(',').includes('[data-agent-chat-frame]') ? [chatBtn] : [];
    fn(ctx, 'bindAgentHeadControls')(el, { id: 'a1', type: 'smart-agent' });
    const groups = () => fromContext(evaluate(ctx, 'canvasState.nodes.filter(n => n.type === NODE_TYPE_GROUP)'));

    chatBtn.dispatchEvent(ev('click'));
    assert.equal(menu.open, false);
    assert.equal(groups().length, 1, '首次点击创建 smart-group');
    assert.equal(groups()[0].x, 0 + 240 + 40, '无 w 回退 240');
    assert.equal(fromContext(evaluate(ctx, "canvasState.nodes.find(n => n.id === 'a1').chatFrameId")), groups()[0].id);

    chatBtn.dispatchEvent(ev('click'));
    assert.equal(groups().length, 1, '第二次点击不得重复建');
    assert.ok(renders.some(r => r.kind === 'centerViewport'), '第二次点击 = 视口居中');
});

test('#323 E2 toggleGroupRefFrame：refFrame true↔false 切换（写字段 + render + 保存 + toast）；非 group id 返回 false', () => {
    // #392: this node-ops action now needs the real durable projector/journal,
    // not the isolated TUI loader's intentionally absent hub dependencies.
    const { ctx, harness, renders } = humanEditor([
        { id: 'g1', type: 'smart-group', items: [] }, { id: 'i1', type: 'smart-image', images: [] },
    ]);
    loadModules(ctx, ['canvas-node-ops.js']);
    const toggle = fn(ctx, 'toggleGroupRefFrame');
    const refFrame = () => evaluate(ctx, "canvasState.nodes.find(n => n.id === 'g1').refFrame");
    const toastText = () => harness.toasts.at(-1);

    assert.equal(toggle('g1'), true);
    assert.equal(refFrame(), true, '第一次切换置 true');
    assert.equal(toastText(), '已设为参考框');
    assert.ok(renders.length > 0, '切换必须 render');
    assert.equal(evaluate(ctx, 'smartCanvasPendingState().journal.hasPending()'), true, '真实保存 journal 必须包含变化');
    assert.equal(evaluate(ctx, "canvasState.nodes.find(n => n.id === 'g1').userTouchedAt"), 1234);
    assert.equal(evaluate(ctx, "canvasState.nodes.find(n => n.id === 'g1').promptEditedByUser"), undefined);

    assert.equal(toggle('g1'), true);
    assert.equal(refFrame(), false, '第二次切换回 false（菜单文案随之变「设为参考框」）');
    assert.equal(toastText(), '已取消参考框');

    assert.equal(toggle('i1'), false, '非分组节点 no-op');
    assert.equal(toggle('ghost'), false, '不存在 id no-op');
});

test('#323 E2 agentRefFrameCycle：未绑定 → 第一个 → 下一个 → 未绑定 → 第一个；×N 计数（一个框 2 素材、一个 0）', () => {
    const { ctx, renders } = loadAgentTuiModule({
        open: false,
        nodes: [
            { id: 'a1', type: 'smart-agent' },
            { id: 'g1', type: 'smart-group', refFrame: true, items: ['i1', 'i2'] },
            { id: 'g2', type: 'smart-group', refFrame: true, items: [] },
            { id: 'i1', type: 'smart-image', images: [{ url: '/x/a.png' }] },
            { id: 'i2', type: 'smart-image', images: [{ url: '/x/b.png' }] },
            { id: 'i3', type: 'smart-image', images: [] }, // 不在 items 里且 images 空，双不计数
        ],
    });
    const cycle = fn(ctx, 'agentRefFrameCycle');
    const label = fn(ctx, 'agentRefFrameButtonLabel');
    const agent = () => evaluate(ctx, "canvasState.nodes.find(n => n.id === 'a1')");

    // ×N 素材数：g1 = 2（i1/i2 images 非空各计 1），g2 = 0。
    const g1 = fromContext(evaluate(ctx, "canvasState.nodes.find(n => n.id === 'g1')"));
    const g2 = fromContext(evaluate(ctx, "canvasState.nodes.find(n => n.id === 'g2')"));
    assert.equal(fn(ctx, 'agentRefFrameAssetCount')(g1), 2);
    assert.equal(fn(ctx, 'agentRefFrameAssetCount')(g2), 0);
    assert.equal(label(agent()), '参考', '解绑态显示「参考」');

    // 未绑定 → 第一个参考框（g1，×2）。
    let r = cycle('a1');
    assert.equal(r.action, 'cycled');
    assert.equal(r.refFrameId, 'g1');
    assert.equal(fromContext(agent().refFrameId), 'g1', 'agent.refFrameId 写入');
    assert.equal(label(agent()), '参考 ×2', '绑定态显示「参考 ×N」');

    // → 下一个（g2，×0）。
    r = cycle('a1');
    assert.equal(r.refFrameId, 'g2');
    assert.equal(label(agent()), '参考 ×0', '空框也要如实显示 ×0');

    // 最后一个 → 回未绑定（字段清掉，不是留空串）。
    r = cycle('a1');
    assert.equal(r.refFrameId, '');
    assert.equal(evaluate(ctx, "'refFrameId' in canvasState.nodes.find(n => n.id === 'a1')"), false, '解绑 = 清字段');
    assert.equal(label(agent()), '参考');

    // 循环回到第一个。
    r = cycle('a1');
    assert.equal(r.refFrameId, 'g1');
    assert.equal(r.frames, 2);
    assert.ok(renders.filter(x => x.kind === 'render').length >= 4, '每次循环都 render');
    assert.ok(renders.filter(x => x.kind === 'scheduleSave').length >= 4, '每次循环都保存');
});

test('#323 E2 agentRefFrameCycle：无参考框只更新 title 提示（禁 alert、绑定不动、不 render/不保存）；stale 绑定 + 有参考框 → 按未绑定', () => {
    const { ctx, alerts, renders } = loadAgentTuiModule({
        open: false,
        nodes: [
            { id: 'a1', type: 'smart-agent', refFrameId: 'gone' },
            { id: 'g9', type: 'smart-group', refFrame: false, items: [] },
        ],
    });
    const cycle = fn(ctx, 'agentRefFrameCycle');
    const btn = { title: '旧提示' };

    const r = cycle('a1', btn);
    assert.equal(r.action, 'none');
    assert.equal(r.refFrameId, 'gone', '无参考框 → 原绑定不动');
    assert.ok(String(btn.title).includes('设为参考框'), `title 要给出可操作提示：${btn.title}`);
    assert.equal(alerts.length, 0, '禁止 alert');
    assert.equal(renders.filter(x => x.kind === 'render').length, 0, '无参考框 → 不 render');
    assert.equal(renders.filter(x => x.kind === 'scheduleSave').length, 0, '无参考框 → 不保存');

    // stale 绑定（'gone' 不存在）且参考框存在 → 按未绑定处理，从第一个开始。
    evaluate(ctx, 'canvasState.nodes.push({ id: "g1", type: NODE_TYPE_GROUP, refFrame: true, items: [] })');
    const r2 = cycle('a1');
    assert.equal(r2.action, 'cycled');
    assert.equal(r2.refFrameId, 'g1');
});

test('#323 E2 agentHeadActionButtonsHtml：参考按钮解绑「参考」/绑定「参考 ×N」；绑定态带激活 class + title 带框名', () => {
    const { ctx } = loadAgentTuiModule({
        nodes: [
            { id: 'a1', type: 'smart-agent' },
            { id: 'g1', type: 'smart-group', refFrame: true, items: ['i1'], title: '参考框A' },
            { id: 'i1', type: 'smart-image', images: [{ url: '/a.png' }] },
        ],
    });
    const html = fn(ctx, 'agentHeadActionButtonsHtml');
    const liveNode = () => fromContext(evaluate(ctx, "canvasState.nodes.find(n => n.id === 'a1')"));

    let out = fromContext(html(liveNode()));
    assert.ok(out.includes('agent-ref-frame-btn'), out);
    assert.ok(!out.includes('agent-ref-frame-btn agent-tui-btn-active'), '解绑 → 无激活 class');
    assert.ok(out.includes('>参考</button>'), `解绑显示「参考」：${out}`);

    evaluate(ctx, "canvasState.nodes.find(n => n.id === 'a1').refFrameId = 'g1'");
    out = fromContext(html(liveNode()));
    assert.ok(out.includes('agent-ref-frame-btn agent-tui-btn-active'), '绑定 → 激活 class');
    assert.ok(out.includes('参考 ×1'), '绑定显示「参考 ×N」');
    assert.ok(out.includes('参考框：参考框A'), 'title 带绑定框名');
});

// ── #407 D1-v2：唯一停靠、会话级收起态、IME 延迟切换 ─────────────────────
// 仍用 loadAgentTuiModule 加载完整真实模块，render/scheduleSave 保持零逻辑记录器，
// 不给 render 补 sync/normalize 等产品行为。因此 render 前的宿主接线留给浏览器回归，
// 本组直接调用真实 syncAgentDock 验证同步入口的归一化与自动收起。
// 新增表面积：innerWidth/viewport 是数据；CSS.escape 仅对本组安全 ASCII id 透传，
// 不模拟特殊字符转义；isNodeSelected 固定返回未选中，不复制选择算法、不覆盖选择行为。
// harness 不解析 innerHTML，querySelector 返回 null，wrap.firstElementChild 为空而不建
// node DOM；真实模板和后续 collapse 循环仍执行。布局/CSS/焦点/真实 IME 由浏览器兜底；
// compositionstart/end 在此派发给真实 bindAgentNodeControls 注册的 handler。
function loadAgentDockModule({ width = 1440, ...options } = {}) {
    const m = loadAgentTuiModule(options);
    m.ctx.innerWidth = width;
    m.ctx.viewport = { x: -217, y: 113, scale: 0.65 };
    m.ctx.CSS = { escape: String };
    m.ctx.isNodeSelected = () => false;
    const dock = m.harness.element('agentDock');
    return { ...m, dock };
}

const dockMemory = ctx => fromContext(evaluate(ctx,
    '({ collapsed: [...agentDockCollapsed], expanded: [...agentDockUserExpanded] })'));

const dockEffects = renders => renders.filter(r =>
    ['render', 'scheduleSave', 'centerViewport'].includes(r.kind));

test('#407 D1-v2 agentDockState / ShouldAutoCollapse：左右状态、窄视口与中央留白边界不变', () => {
    const { ctx } = loadAgentDockModule();
    const state = fn(ctx, 'agentDockState');
    assert.equal(state(null), null);
    assert.equal(state({ dock: 'left' }), 'left');
    assert.equal(state({ dock: 'right' }), 'right');
    assert.equal(state({ dock: 'bottom' }), null);
    assert.equal(state({}), null);
    for (const [width, w, expected] of [
        [1440, 382, false], [899, 382, true], [900, 382, false],
        [1099, 620, true], [1100, 620, false],
    ]) {
        ctx.innerWidth = width;
        assert.equal(fn(ctx, 'agentDockShouldAutoCollapse')({ w }), expected, `视口 ${width} / 面板 ${w}`);
    }
});

test('#407 D1-v2 sync：宽默认展开、窄自动收起、变宽不自动展开；只写内存且不保存', () => {
    const node = { id: 'A', type: 'smart-agent', dock: 'left', x: 12, y: 34 };
    const { ctx, dock, renders, bodyHtmlCalls } = loadAgentDockModule({ nodes: [node] });
    const sync = fn(ctx, 'syncAgentDock');
    const collapsed = fn(ctx, 'agentDockIsCollapsed');
    assert.equal(evaluate(ctx, 'agentDockCollapsed instanceof Set && agentDockUserExpanded instanceof Set'), true);
    assert.equal(collapsed('A'), false);
    sync();
    assert.equal(collapsed('A'), false, '宽视口默认展开');
    assert.equal(dock.hidden, false, '真实 sync 识别到停靠节点');
    assert.equal(ctx.document.querySelector('#agentDock .agent-dock-node[data-id="A"]'), null);
    assert.equal(dock.children.length, 0, '不解析 HTML、不伪造节点 DOM');
    assert.ok(bodyHtmlCalls.length > 0, 'querySelector=null 时真实停靠模板仍执行');

    ctx.innerWidth = 800;
    sync();
    assert.equal(collapsed('A'), true, '窄视口真实自动收起');
    assert.deepEqual(dockMemory(ctx), { collapsed: ['A'], expanded: [] });
    ctx.innerWidth = 1440;
    sync();
    assert.equal(collapsed('A'), true, '同一 VM 只自动收、不自动展');
    assert.equal(Object.hasOwn(node, 'dockCollapsed'), false, '不得写回节点持久化字段');
    assert.deepEqual(node, { id: 'A', type: 'smart-agent', dock: 'left', x: 12, y: 34 });
    assert.deepEqual(dockEffects(renders), [], 'sync 自动收起/重同步均不 scheduleSave 或全量 render');
});

test('#407 D1-v2 手动展开抑制窄视口自动收起；再次收起清展开标记，全程不保存', () => {
    const node = { id: 'A', type: 'smart-agent', dock: 'right' };
    const { ctx, renders } = loadAgentDockModule({ width: 800, nodes: [node] });
    const sync = fn(ctx, 'syncAgentDock');
    const toggle = fn(ctx, 'agentDockCollapseToggle');
    sync();
    toggle('A');
    assert.equal(fn(ctx, 'agentDockIsCollapsed')('A'), false);
    assert.deepEqual(dockMemory(ctx), { collapsed: [], expanded: ['A'] });
    ctx.innerWidth = 600;
    sync();
    sync();
    assert.equal(fn(ctx, 'agentDockIsCollapsed')('A'), false, '手动展开不能被后续 sync 覆盖');
    toggle('A');
    sync();
    assert.equal(fn(ctx, 'agentDockIsCollapsed')('A'), true);
    assert.deepEqual(dockMemory(ctx), { collapsed: ['A'], expanded: [] });
    assert.deepEqual(node, { id: 'A', type: 'smart-agent', dock: 'right' });
    assert.deepEqual(dockEffects(renders), [], '手动收/展也不保存');
});

test('#407 D1-v2 新 VM 不继承收起/手动展开，按当前视口重新决定', () => {
    const first = loadAgentDockModule({ width: 800, nodes: [{ id: 'A', type: 'smart-agent', dock: 'left' }] });
    fn(first.ctx, 'syncAgentDock')();
    assert.equal(fn(first.ctx, 'agentDockIsCollapsed')('A'), true);
    const afterCollapse = fromContext(first.ctx.canvasState.nodes);
    fn(first.ctx, 'agentDockCollapseToggle')('A');
    assert.deepEqual(dockMemory(first.ctx), { collapsed: [], expanded: ['A'] });
    const afterExpand = fromContext(first.ctx.canvasState.nodes);
    assert.deepEqual(afterCollapse, afterExpand, '收起与展开不能改变可持久化节点');

    const wide = loadAgentDockModule({ width: 1440, nodes: afterCollapse });
    assert.deepEqual(dockMemory(wide.ctx), { collapsed: [], expanded: [] });
    fn(wide.ctx, 'syncAgentDock')();
    assert.equal(fn(wide.ctx, 'agentDockIsCollapsed')('A'), false, '旧 VM 收起不影响新宽视口');
    const narrow = loadAgentDockModule({ width: 800, nodes: afterExpand });
    fn(narrow.ctx, 'syncAgentDock')();
    assert.equal(fn(narrow.ctx, 'agentDockIsCollapsed')('A'), true, '旧 VM 手动展开不影响新窄视口');
    assert.deepEqual(dockMemory(narrow.ctx), { collapsed: ['A'], expanded: [] });
    for (const m of [first, wide, narrow]) {
        assert.equal(m.ctx.canvasState.nodes.some(n => Object.hasOwn(n, 'dockCollapsed')), false);
        assert.deepEqual(dockEffects(m.renders), []);
    }
});

test('#407 D1-v2 agentDockSet：非 Agent 与缺失 id 不得停靠、替换或保存', () => {
    const nodes = [
        { id: 'A', type: 'smart-agent', dock: 'left' },
        { id: 'image', type: 'smart-image', x: 1, y: 2 },
        { id: 'task', type: 'agent-task' },
    ];
    const { ctx, renders } = loadAgentDockModule({ nodes });
    const before = fromContext(nodes);
    for (const id of ['image', 'task', 'ghost']) {
        fn(ctx, 'agentDockSet')(id, 'right');
        fn(ctx, 'agentDockSet')(id, null);
    }
    assert.deepEqual(nodes, before);
    assert.deepEqual(dockMemory(ctx), { collapsed: [], expanded: [] });
    assert.deepEqual(renders, [], '非法目标不能触发 render/scheduleSave');
});

test('#407 D1-v2 唯一停靠：B 停任一侧都替换 A，原坐标/同一草稿状态/viewport 不动', () => {
    for (const side of ['left', 'right']) {
        const a = { id: 'A', type: 'smart-agent', dock: null, x: -850.25, y: 1700 };
        const b = { id: 'B', type: 'smart-agent', dock: null, x: 345, y: -44.5 };
        const { ctx, renders } = loadAgentDockModule({ nodes: [a, b] });
        const viewport = ctx.viewport;
        const beforeViewport = fromContext(viewport);
        const aState = fn(ctx, 'getAgentTuiState')('A');
        const bState = fn(ctx, 'getAgentTuiState')('B');
        aState.draft = 'A 未发送 <草稿>';
        bState.draft = 'B 未发送 & 草稿';
        fn(ctx, 'agentDockSet')('A', 'left');
        fn(ctx, 'agentDockCollapseToggle')('A');
        if (side === 'right') fn(ctx, 'agentDockCollapseToggle')('A');
        assert.ok(evaluate(ctx, 'agentDockCollapsed.has("A") || agentDockUserExpanded.has("A")'));

        fn(ctx, 'agentDockSet')('B', side);
        assert.equal(a.dock, null, `停 ${side} 的 B 必须取回 A，不是每侧各留一个`);
        assert.equal(b.dock, side);
        assert.deepEqual([a.x, a.y, b.x, b.y], [-850.25, 1700, 345, -44.5]);
        assert.equal(fn(ctx, 'getAgentTuiState')('A'), aState);
        assert.equal(fn(ctx, 'getAgentTuiState')('B'), bState);
        assert.equal(aState.draft, 'A 未发送 <草稿>');
        assert.equal(bState.draft, 'B 未发送 & 草稿');
        assert.ok(fn(ctx, 'agentNodeBodyHtml')(a).includes('A 未发送 &lt;草稿&gt;'));
        assert.ok(fn(ctx, 'agentNodeBodyHtml')(b).includes('B 未发送 &amp; 草稿'));
        assert.deepEqual(dockMemory(ctx), { collapsed: [], expanded: [] }, '替换时遗忘 A 的两种 UI 标记');
        assert.equal(ctx.viewport, viewport);
        assert.deepEqual(ctx.viewport, beforeViewport);
        assert.equal(renders.some(r => r.kind === 'centerViewport'), false);
        assert.equal([a, b].some(n => Object.hasOwn(n, 'dockCollapsed')), false);
    }
});

test('#407 D1-v2 取回按钮：落回原坐标，草稿/viewport 不动，清收起或手动展开标记', () => {
    for (const expanded of [false, true]) {
        const node = { id: 'A', type: 'smart-agent', dock: 'right', x: -310, y: 2048.5 };
        const { ctx, harness, renders } = loadAgentDockModule({ width: 800, nodes: [node] });
        const viewport = fromContext(ctx.viewport);
        const state = fn(ctx, 'getAgentTuiState')('A');
        state.draft = '取回仍保留的草稿';
        fn(ctx, 'syncAgentDock')();
        if (expanded) fn(ctx, 'agentDockCollapseToggle')('A');
        assert.equal(fn(ctx, 'agentDockIsCollapsed')('A'), !expanded);
        const btn = harness.makeElement('button');
        btn.setAttribute('data-agent-dock-undock', 'A');
        const menu = harness.makeElement('details');
        btn.closest = selector => selector === '.agent-tui-more' ? menu : null;
        const el = harness.makeElement('div');
        el.querySelectorAll = sel => sel.split(',').includes('[data-agent-dock-undock]') ? [btn] : [];
        fn(ctx, 'bindAgentHeadControls')(el, node);
        renders.length = 0;
        btn.dispatchEvent(ev('click'));
        assert.deepEqual(node, { id: 'A', type: 'smart-agent', dock: null, x: -310, y: 2048.5 });
        assert.equal(fn(ctx, 'getAgentTuiState')('A'), state);
        assert.equal(state.draft, '取回仍保留的草稿');
        assert.deepEqual(ctx.viewport, viewport);
        assert.equal(renders.some(r => r.kind === 'centerViewport'), false);
        assert.ok(renders.some(r => r.kind === 'render'), '真实按钮进入停靠切换路径');
        assert.deepEqual(dockMemory(ctx), { collapsed: [], expanded: [] });
    }
});

test('#407 D1-v2 normalizeAgentDockNodes：按数组首个 Agent 保留唯一 dock，删除全部旧收起字段且幂等', () => {
    const nodes = [
        { id: 'image', type: 'smart-image', dock: 'left', dockCollapsed: true, x: 1, y: 2 },
        { id: 'Z', type: 'smart-agent', dock: 'right', dockCollapsed: true, x: -3, y: 4 },
        { id: 'A', type: 'smart-agent', dock: 'left', dockCollapsed: false, x: 5, y: -6 },
        { id: 'B', type: 'smart-agent', dock: 'right', dockCollapsed: true },
        { id: 'floating', type: 'smart-agent', dock: null, dockCollapsed: false },
        { id: 'task', type: 'agent-task', dockCollapsed: true },
    ];
    const { ctx, renders } = loadAgentDockModule({ nodes });
    const normalize = fn(ctx, 'normalizeAgentDockNodes');
    const refs = [...nodes];
    normalize(nodes);
    assert.deepEqual(nodes.map(n => n.dock ?? null), [null, 'right', null, null, null, null],
        '忽略非 Agent 的旧 dock，Z 在 A 前，不得按 id 排序或左右各保留一个');
    assert.equal(nodes.some(n => Object.hasOwn(n, 'dockCollapsed')), false, '包括浮动/非 Agent 的 false 字段也要删');
    assert.deepEqual(nodes.slice(0, 3).map(n => [n.x, n.y]), [[1, 2], [-3, 4], [5, -6]]);
    nodes.forEach((node, index) => assert.equal(node, refs[index], '原地规范化，不替换节点实体'));
    const normalized = fromContext(nodes);
    normalize(nodes);
    assert.deepEqual(nodes, normalized, '重复归一化不继续变更文档');
    assert.deepEqual(dockMemory(ctx), { collapsed: [], expanded: [] }, '不能把旧收起字段迁移到内存');
    assert.deepEqual(renders, []);
});

test('#407 D1-v2 sync 自身归一化旧多 dock 与旧字段，宽视口不继承旧收起且不保存', () => {
    const nodes = [
        { id: 'B', type: 'smart-agent', dock: 'right', dockCollapsed: true },
        { id: 'A', type: 'smart-agent', dock: 'left', dockCollapsed: false },
        { id: 'image', type: 'smart-image', dock: 'left', dockCollapsed: true },
    ];
    const { ctx, renders } = loadAgentDockModule({ nodes });
    fn(ctx, 'syncAgentDock')();
    assert.deepEqual(nodes, [
        { id: 'B', type: 'smart-agent', dock: 'right' },
        { id: 'A', type: 'smart-agent', dock: null },
        { id: 'image', type: 'smart-image', dock: null },
    ]);
    assert.equal(fn(ctx, 'agentDockIsCollapsed')('B'), false);
    assert.deepEqual(dockMemory(ctx), { collapsed: [], expanded: [] });
    assert.deepEqual(dockEffects(renders), [], '兼容数据的 sync 不能触发保存');
});

for (const { label, dock, dockSide } of [
    { label: '自身停靠', dock: null, dockSide: 'right' },
    { label: '自身取回', dock: 'left', dockSide: null },
]) {
    test(`#407 D1-v2 IME ${label}：pendingDock 为请求对象，真实 compositionend 后才执行`, () => {
        const node = { id: 'A', type: 'smart-agent', dock, x: -90, y: 420 };
        // 目标 A composing 的停靠请求也不能提前取回未 composing 的 incumbent B。
        const other = { id: 'B', type: 'smart-agent', dock: dock === null ? 'left' : null, x: 50, y: -60 };
        const { ctx, harness, renders } = loadAgentDockModule({ nodes: [node, other] });
        const tree = buildAgentControlTree(harness);
        fn(ctx, 'bindAgentNodeControls')(tree.el, node);
        const state = fn(ctx, 'getAgentTuiState')('A');
        tree.input.value = '未完成的中文';
        tree.input.dispatchEvent(ev('input'));
        tree.input.dispatchEvent(ev('compositionstart'));
        assert.equal(state.composing, true);
        const before = fromContext([node, other]);
        const memory = dockMemory(ctx);
        const viewport = fromContext(ctx.viewport);
        fn(ctx, 'agentDockSet')('A', dockSide);
        assert.deepEqual(fromContext(state.pendingDock), { nodeId: 'A', dockSide });
        assert.deepEqual([node, other], before, '组合结束前目标与被替换者的 dock/坐标都不得变更');
        assert.deepEqual(dockMemory(ctx), memory, '组合结束前不清 UI 标记');
        assert.deepEqual(renders, [], '组合期间不得保存或重渲染');
        tree.input.value = '完成的中文草稿';
        tree.input.dispatchEvent(ev('compositionend'));
        assert.equal(state.composing, false);
        assert.equal(state.pendingDock, undefined, '请求消费后清槽');
        assert.equal(node.dock, dockSide);
        assert.equal(other.dock, null, '组合结束后才取回被替换的 B');
        assert.deepEqual([node.x, node.y, other.x, other.y], [-90, 420, 50, -60]);
        assert.equal(fn(ctx, 'getAgentTuiState')('A'), state);
        assert.equal(state.draft, '完成的中文草稿');
        assert.deepEqual(ctx.viewport, viewport);
        assert.ok(renders.some(r => r.kind === 'render'), '组合结束后才真正切换形态');
        assert.equal(Object.hasOwn(node, 'dockCollapsed'), false);
    });
}

test('#407 D1-v2 IME 替换：A composing 时 B 请求挂 A，compositionend 必须执行 B 而非 A', () => {
    const a = { id: 'A', type: 'smart-agent', dock: 'left', x: -200, y: 300 };
    const b = { id: 'B', type: 'smart-agent', dock: null, x: 800, y: -900 };
    const { ctx, harness, renders } = loadAgentDockModule({ nodes: [a, b] });
    fn(ctx, 'agentDockCollapseToggle')('A');
    const tree = buildAgentControlTree(harness);
    fn(ctx, 'bindAgentNodeControls')(tree.el, a);
    const aState = fn(ctx, 'getAgentTuiState')('A');
    const bState = fn(ctx, 'getAgentTuiState')('B');
    bState.draft = 'B 的待发送草稿';
    tree.input.value = 'A 正在输入';
    tree.input.dispatchEvent(ev('input'));
    tree.input.dispatchEvent(ev('compositionstart'));
    const before = fromContext([a, b]);
    const memory = dockMemory(ctx);
    const viewport = fromContext(ctx.viewport);
    renders.length = 0;
    fn(ctx, 'agentDockSet')('B', 'right');
    assert.deepEqual(fromContext(aState.pendingDock), { nodeId: 'B', dockSide: 'right' });
    assert.equal(bState.pendingDock, undefined, '请求必须挂阻塞者 A，而非未 composing 的 B');
    assert.deepEqual([a, b], before, 'A/B 都不能在 A 组合结束前改变 dock');
    assert.deepEqual(dockMemory(ctx), memory, '延迟请求不能提前遗忘 A 的收起态');
    assert.deepEqual(renders, [], '不能提前 render 杀掉 A 的输入法');
    tree.input.value = 'A 输入完成的草稿';
    tree.input.dispatchEvent(ev('compositionend'));
    assert.equal(aState.pendingDock, undefined);
    assert.equal(aState.composing, false);
    assert.equal(a.dock, null);
    assert.equal(b.dock, 'right', 'compositionend 必须用 request.nodeId，不是事件所属 A');
    assert.deepEqual([a.x, a.y, b.x, b.y], [-200, 300, 800, -900]);
    assert.equal(fn(ctx, 'getAgentTuiState')('A'), aState);
    assert.equal(fn(ctx, 'getAgentTuiState')('B'), bState);
    assert.equal(aState.draft, 'A 输入完成的草稿');
    assert.equal(bState.draft, 'B 的待发送草稿');
    assert.deepEqual(dockMemory(ctx), { collapsed: [], expanded: [] });
    assert.deepEqual(ctx.viewport, viewport);
    assert.ok(renders.some(r => r.kind === 'render'));
    assert.equal([a, b].some(n => Object.hasOwn(n, 'dockCollapsed')), false);
});

test('#407 D1-v2 agentDockForget：只清目标两个 Set，保留节点/会话草稿，重复调用无副作用', () => {
    const node = { id: 'A', type: 'smart-agent', dock: 'left' };
    const { ctx, renders } = loadAgentDockModule({ nodes: [node] });
    const state = fn(ctx, 'getAgentTuiState')('A');
    state.draft = '保留草稿';
    // 故意同时播两个标记，确认 forget 独立删除两者，不依赖互斥状态。
    evaluate(ctx, 'agentDockCollapsed.add("A"); agentDockCollapsed.add("B"); agentDockUserExpanded.add("A"); agentDockUserExpanded.add("B")');
    fn(ctx, 'agentDockForget')('A');
    fn(ctx, 'agentDockForget')('A');
    fn(ctx, 'agentDockForget')('ghost');
    assert.deepEqual(dockMemory(ctx), { collapsed: ['B'], expanded: ['B'] });
    assert.equal(fn(ctx, 'agentDockIsCollapsed')('A'), false);
    assert.equal(fn(ctx, 'agentDockIsCollapsed')('B'), true);
    assert.deepEqual(node, { id: 'A', type: 'smart-agent', dock: 'left' });
    assert.equal(fn(ctx, 'getAgentTuiState')('A'), state);
    assert.equal(state.draft, '保留草稿');
    assert.deepEqual(renders, []);
});

test('#407 D1-v2 cleanupAgentTuiState：有/无 TUI 条目都清两个停靠 Set，不影响其他节点', () => {
    const { ctx, ws, renders } = loadAgentDockModule();
    fn(ctx, 'connectAgentWebSocket')('A', 'p1');
    evaluate(ctx, 'agentDockCollapsed.add("A"); agentDockCollapsed.add("B"); agentDockUserExpanded.add("A"); agentDockUserExpanded.add("B")');
    fn(ctx, 'cleanupAgentTuiState')('A');
    assert.deepEqual(dockMemory(ctx), { collapsed: ['B'], expanded: ['B'] });
    assert.equal(evaluate(ctx, 'agentTuiState.has("A")'), false);
    assert.equal(evaluate(ctx, 'agentTuiState.has("B")'), false, 'B 从未建立 TUI 状态');
    assert.equal(ws.closed.length, 1);
    fn(ctx, 'cleanupAgentTuiState')('B');
    assert.deepEqual(dockMemory(ctx), { collapsed: [], expanded: [] });
    assert.equal(evaluate(ctx, 'agentTuiState.size'), 0);
    assert.deepEqual(renders, []);
});

test('#407 D1-v2 按钮 HTML：展开/收起文案读内存 Set，不读矛盾的旧 node.dockCollapsed', () => {
    const { ctx } = loadAgentDockModule();
    // 脱离 canvasState 的旧节点入参：故意让旧字段与内存相反，隔离 HTML 的读来源。
    const node = { id: 'A', type: 'smart-agent', dock: 'left', dockCollapsed: false };
    evaluate(ctx, 'agentDockCollapsed.add("A")');
    assert.equal(fn(ctx, 'agentDockIsCollapsed')('A'), true);
    for (const name of ['agentDockButtonsHtml', 'agentHeadActionButtonsHtml']) {
        const html = fn(ctx, name)(node);
        assert.match(html, /data-agent-dock-collapse="A"[^>]*aria-label="展开"[^>]*>展开<\/button>/);
        assert.ok(html.includes('data-agent-dock-undock="A"'));
        assert.ok(!html.includes('>收起</button>'));
    }
    node.dockCollapsed = true;
    evaluate(ctx, 'agentDockCollapsed.delete("A"); agentDockUserExpanded.add("A")');
    assert.equal(fn(ctx, 'agentDockIsCollapsed')('A'), false);
    const expanded = fn(ctx, 'agentDockButtonsHtml')(node);
    assert.match(expanded, /data-agent-dock-collapse="A"[^>]*aria-label="收起"[^>]*>收起<\/button>/);
    assert.ok(!expanded.includes('>展开</button>'));
    node.dock = null;
    const floating = fn(ctx, 'agentDockButtonsHtml')(node);
    assert.ok(floating.includes('data-agent-dock-side="left"') && floating.includes('data-agent-dock-side="right"'));
    assert.ok(!floating.includes('data-agent-dock-collapse') && !floating.includes('data-agent-dock-undock'));
});

// D2 fixtures prove the consumer/reload boundary only: there is no agentRun producer.
const d2Node = () => ({ id: 'n1', type: 'smart-agent', title: 'Agent' });
const d2Drain = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
const d2Text = (text, thinking = false) => ({ type: 'message_update', assistantMessageEvent: { type: thinking ? 'thinking_delta' : 'text_delta', delta: text } });
const d2Tool = id => ({ type: 'tool_execution_start', toolCallId: id, toolName: `tool-${id}`, args: {} });

// r3: ts is on the real /events envelope; Pi assistant timestamps live on
// message, in ms. All dates here are fixed mock inputs, not original host evidence.
const r3List = [
    '可以。保留自然的光感，把构图和材质拉开差异：', '',
    '1. **晨光纸影**  ', '   米白底色、纸张肌理，让留白成为画面主角。',
    '2. **新绿片刻**  ', '   用少量嫩绿呼应季节，产品保持克制。',
    '3. **窗边静物**  ', '   柔和侧光和长投影，营造真实的生活感。', '',
    '我建议先看第一种。你更想强调产品质感，还是春日的轻松氛围？',
].join('\n');

test('r3 history: replay envelope and Pi native time, calendar boundaries, safe ordered list, no added timeline entries', async () => {
    const node = { ...d2Node(), title: '视觉探索 <草稿>' };
    const first = new Date(2020, 4, 12, 10, 24);
    const next = new Date(2020, 4, 13, 11, 25);
    const rec = (seq, ts, event) => ({ seq, ts, event, generation: 0, host: 'pi' });
    const records = [
        rec(1, first.getTime() / 1000, { type: 'user_prompt',
            text: '参考画布里的这张图，试试更轻盈的春日方向。先给我三个视觉方案，不要生成图片。', request_id: 'r3-1',
            node_refs: [{ node_id: 'ref-1', type: 'smart-image', title: '参考图 · KV 方向' }] }),
        rec(2, next.getTime() / 1000, { type: 'turn_end', message: { role: 'assistant',
            timestamp: first.getTime(), content: [{ type: 'text', text: r3List }] } }),
        rec(3, undefined, { type: 'user_prompt', text: '没有日志时间的旧消息' }),
        rec(4, next.getTime() / 1000, { type: 'turn_end', message: { role: 'assistant', content: [{ type: 'text', text: '日志时间' }] } }),
    ];
    const { ctx } = loadAgentTuiModule({ nodes: [node], fetchResponse: { ok: true, json: async () => ({ events: records }) } });
    const state = fn(ctx, 'getAgentTuiState')(node.id);
    state.projectId = 'p1';
    fn(ctx, 'replayAgentEvents')(node.id, state);
    await d2Drain();
    const html = fn(ctx, 'agentNodeBodyHtml')(node);
    assert.match(html, /agent-tui-msg-byline"><span>你<\/span><time/);
    assert.match(html, /agent-tui-msg-mark" aria-hidden="true">✳<\/span> 萤火<\/span><time/);
    assert.equal(html.split(`<time datetime="${first.toISOString()}">10:24</time>`).length - 1, 2);
    assert.ok(html.includes(`<time datetime="${next.toISOString()}">11:25</time>`));
    assert.match(html, /<div class="agent-tui-msg-byline"><span>你<\/span><\/div><span class="agent-tui-msg-text">没有日志时间的旧消息/);
    assert.match(html, /agent-tui-conversation-date">2020年5月12日 · 视觉探索 &lt;草稿&gt;/);
    assert.match(html, /agent-tui-conversation-date">2020年5月13日 · 视觉探索 &lt;草稿&gt;/);
    assert.equal((html.match(/agent-tui-conversation-date/g) || []).length, 2);
    assert.match(html, /<\/p><ol class="agent-md-list"><li><strong>晨光纸影<\/strong><span>米白底色、纸张肌理，让留白成为画面主角。<\/span><\/li><li><strong>新绿片刻<\/strong>.*?<li><strong>窗边静物<\/strong>.*?<\/ol><p>我建议/);
    assert.equal((html.match(/data-agent-entry-key=/g) || []).length, 4);
    assert.equal((html.match(/agent-tui-msg-actions/g) || []).length, 2);
    assert.ok(html.includes('参考图 · KV 方向'));
    const markdown = fn(ctx, 'renderMarkdown');
    assert.equal(markdown('3. A\n4. `<ok>`\n- B\n- C'), '<ol class="agent-md-list" start="3"><li>A</li><li><code class="agent-md-inline-code">&lt;ok&gt;</code></li></ol><ul class="agent-md-list"><li>B</li><li>C</li></ul>');
    assert.match(markdown('```text\n1. not a list\n```'), /<code>1\. not a list<\/code>/);
});

test('r3 history: live receipt stays untimed; canonical replay enriches the same optimistic request and native stream retains one reply', async () => {
    const node = d2Node();
    const first = new Date(2020, 4, 12, 10, 24);
    let events;
    const { ctx } = loadAgentTuiModule({ nodes: [node], fetchResponse: { ok: true, json: async () => ({ events }) } });
    const state = fn(ctx, 'getAgentTuiState')(node.id);
    state.projectId = 'p1';
    state.lastSeq = 1;
    const optimistic = fn(ctx, 'agentNodeRefsPrepare')(node.id, 'prompt', '本地草稿');
    const handle = fn(ctx, 'handleAgentEvent');
    handle(node.id, { type: 'input_accepted', input_type: 'prompt', request_id: optimistic.id, node_refs: [] });
    assert.equal(optimistic.row.timestamp, undefined);
    assert.doesNotMatch(fn(ctx, 'agentNodeBodyHtml')(node), /<time|agent-tui-conversation-date/);
    events = [{ seq: 2, ts: first.getTime() / 1000, event: { type: 'user_prompt', text: '本地草稿', request_id: optimistic.id,
        node_refs: [{ node_id: 'ref-1', type: 'smart-image', title: '服务端引用名' }] } }];
    fn(ctx, 'replayAgentEvents')(node.id, state);
    await d2Drain();
    assert.equal(state.messages.length, 1);
    assert.equal(state.messages[0], optimistic.row);
    assert.equal(optimistic.row.node_refs[0].title, '服务端引用名');
    assert.equal(optimistic.row.timestamp, first.getTime());
    const nativeTime = first.getTime() + 1000;
    handle(node.id, { ...d2Text('部分'), message: { role: 'assistant', timestamp: nativeTime } });
    handle(node.id, d2Text('回复'));
    handle(node.id, { type: 'turn_end', message: { role: 'assistant', timestamp: nativeTime, content: [{ type: 'text', text: '部分回复' }] } });
    assert.equal(state.messages.length, 2);
    assert.equal(state.messages[1].text, '部分回复');
    assert.equal(state.messages[1].timestamp, nativeTime);
    assert.doesNotMatch(fn(ctx, 'agentNodeBodyHtml')(node), /2020年5月12日 · Agent/);
});

function r3ActionHarness(fetchResponse) {
    const node = d2Node();
    const m = loadAgentTuiModule({ nodes: [node], fetchResponse });
    const { ctx, harness } = m;
    const state = fn(ctx, 'getAgentTuiState')(node.id);
    const append = fn(ctx, 'agentAppendMessage');
    append(state, { role: 'user', text: '原始提示' });
    const reply = append(state, { role: 'assistant', text: '**回复** <安全>' });
    const nextUser = append(state, { role: 'user', text: '下一条 <提示>', delivery: 'accepted' });
    const key = state.timelineMessages.get(reply).key;
    const controls = ['copy', 'fork'].map(kind => {
        const button = harness.makeElement('button');
        button.setAttribute(`data-agent-msg-${kind}`, key);
        return button;
    });
    const el = harness.makeElement('div');
    el.querySelectorAll = selector => selector === '[data-agent-msg-copy], [data-agent-msg-fork]' ? controls : [];
    fn(ctx, 'bindAgentNodeControls')(el, node);
    state.status = 'idle'; // These button cases start after the mocked connection has settled.
    return { ...m, state, node, reply, nextUser, copy: controls[0], fork: controls[1], toastText: () => harness.toasts.at(-1).message };
}

test('r3 message copy: raw text, permission rejection, unavailable API, streaming and stale guards', async () => {
    const { ctx, node, state, reply, copy, toastText } = r3ActionHarness();
    const writes = [];
    ctx.navigator.clipboard = { writeText: async text => { writes.push(text); } };
    const html = () => fn(ctx, 'agentNodeBodyHtml')(node);
    assert.match(html(), /data-agent-msg-copy="entry:2" title="复制回复原文">复制/);
    copy.dispatchEvent(ev('click'));
    await d2Drain();
    assert.deepEqual(writes, [reply.text]);
    assert.equal(toastText(), '已复制回复');
    reply.streaming = true;
    assert.match(html(), /title="请等待这条回复完成后再复制" disabled/);
    copy.dispatchEvent(ev('click'));
    await d2Drain();
    assert.equal(writes.length, 1);
    reply.streaming = false;
    ctx.navigator.clipboard.writeText = async () => { throw new Error('permission denied'); };
    copy.dispatchEvent(ev('click'));
    await d2Drain();
    assert.match(toastText(), /复制失败：.*permission denied.*手动复制/);
    delete ctx.navigator.clipboard;
    assert.match(html(), /title="当前页面无法访问系统剪贴板，请选中文本手动复制" disabled/);
    copy.dispatchEvent(ev('click'));
    assert.match(toastText(), /无法访问系统剪贴板/);
    state.viewEpoch++;
    copy.dispatchEvent(ev('click'));
    assert.match(toastText(), /不在当前会话/);
});

test('r3 message fork: Pi picker provides context but never guesses a native ID, rechecks before submission', async () => {
    const nativeList = { ok: true, json: async () => ({ messages: [
        { text: '下一条 <提示>', entryId: 'pi-choice-a' }, { text: '下一条 <提示>', entryId: 'pi-choice-b' },
    ] }) };
    const m = r3ActionHarness(url => url.endsWith('/fork-messages') ? nativeList : { ok: true, json: async () => ({ canvas: { revision: 2 } }) });
    const { ctx, node, state, fork, harness, fetchCalls, toastText } = m;
    const html = () => fn(ctx, 'agentNodeBodyHtml')(node);
    assert.match(html(), /data-agent-msg-fork="entry:2" title="打开 Pi 选择器[^"]*">从此分叉/);
    fork.dispatchEvent(ev('click'));
    await d2Drain();
    assert.equal(fetchCalls.length, 1);
    const dialog = harness.dialogs[0];
    assert.match(dialog.innerHTML, /在 Pi 中选择分叉点/);
    assert.match(dialog.innerHTML, /萤火回复：\*\*回复\*\* &lt;安全&gt;<br>画布中的下一条用户消息：下一条 &lt;提示&gt;/);
    assert.match(dialog.innerHTML, /在选中的用户消息之前分叉/);
    assert.doesNotMatch(dialog.innerHTML, /\bchecked\b/);
    state.status = 'busy';
    fillForkDialog(harness, dialog, { index: 0, name: '' });
    await d2Drain();
    assert.equal(fetchCalls.length, 1);
    assert.match(toastText(), /请等待当前操作结束/);
    state.status = 'idle';
    fork.dispatchEvent(ev('click'));
    await d2Drain();
    fillForkDialog(harness, harness.dialogs[1], { index: 1, name: '取第二条' });
    await d2Drain();
    assert.deepEqual(JSON.parse(fetchCalls[2].opts.body), { entry_id: 'pi-choice-b', name: '取第二条', user_edit: true });
});

test('r3 message fork: disabled reasons and lookup/submit failures are visible', async () => {
    const lookupFailure = { ok: false, status: 400, json: async () => ({ detail: 'Agent is not running' }) };
    const m = r3ActionHarness(lookupFailure);
    const { ctx, node, state, nextUser, fork, fetchCalls, toastText } = m;
    const html = () => fn(ctx, 'agentNodeBodyHtml')(node);
    nextUser.delivery = 'unconfirmed';
    assert.match(html(), /title="缺少可确认的前后用户消息，无法从这条回复分叉" disabled/);
    fork.dispatchEvent(ev('click'));
    assert.equal(fetchCalls.length, 0);
    nextUser.delivery = 'accepted';
    state.viewHost = 'codex';
    assert.match(html(), /title="只读会话不能分叉" disabled/);
    state.activeHost = node.agentCli = 'codex';
    assert.match(html(), /title="仅 Pi 宿主支持分叉" disabled/);
    fork.dispatchEvent(ev('click'));
    assert.equal(fetchCalls.length, 0);
    state.viewHost = state.activeHost = node.agentCli = 'pi';
    fork.dispatchEvent(ev('click'));
    await d2Drain();
    assert.match(toastText(), /打开分叉选择器失败：.*Agent is not running/);
    const rejected = r3ActionHarness(url => url.endsWith('/fork-messages')
        ? { ok: true, json: async () => ({ messages: [{ entryId: 'native-id', text: 'next' }] }) }
        : { ok: false, status: 409, json: async () => ({ detail: { message: 'Pi cancelled' } }) });
    rejected.fork.dispatchEvent(ev('click'));
    await d2Drain();
    fillForkDialog(rejected.harness, rejected.harness.dialogs[0], { index: 0, name: '' });
    await d2Drain();
    assert.match(rejected.toastText(), /分叉失败：.*Pi cancelled/);
    assert.equal(rejected.state.messages.length, 3);
});

for (const state of ['空闲', '澄清', '计划预览', '执行中', '批量确认', '失败自处理', '等待用户', '已停止', '完成']) {
    test(`#408 D2 agentRun 消费/JSON reload fixture: ${state}`, () => {
        const { ctx } = loadAgentTuiModule();
        const node = { ...d2Node(), agentRun: { state, plan_id: 'p1', batch_id: 'b1', step: 2, total: 8 } };
        const before = JSON.stringify(node);
        const render = fn(ctx, 'agentProgressHtml');
        const html = render(node);
        assert.equal(render(JSON.parse(before)), html, '仅证明 JSON 快照刷新消费');
        assert.equal(JSON.stringify(node), before, '消费端不得写业务快照');
        if (state === '空闲') assert.equal(html, '');
        else {
            assert.ok(html.includes(`${state} · 2/8`));
            assert.ok(html.includes('width:25%'));
            assert.ok(html.includes('data-agent-progress="n1"'));
            assert.ok(!html.includes('主图') && !html.includes('A+') && !html.includes('failure_count'));
        }
    });
}

test('#408 D2 缺失/非法快照隐藏，非法计数只显示真实状态、不推算比例', () => {
    const { ctx } = loadAgentTuiModule();
    const render = fn(ctx, 'agentProgressHtml');
    for (const agentRun of [undefined, null, [], '执行中', {}, { state: 'busy', step: 1, total: 2 }, { state: '执行中<script>' }]) {
        assert.equal(render({ ...d2Node(), agentRun }), '');
    }
    for (const [step, total] of [[null, 2], [undefined, 2], [NaN, 2], [Infinity, 2], ['1', 2], [1.1, 2], [-1, 2], [3, 2], [0, 0], [0, -1], [0, Infinity], [0, '2']]) {
        const html = render({ ...d2Node(), agentRun: { state: '执行中', step, total } });
        assert.match(html, /agent-tui-progress-label">执行中<\/span>/);
        assert.ok(!html.includes('agent-tui-progress-fill'));
        assert.doesNotMatch(html, /\d+\/\d+/);
    }
    const st = fn(ctx, 'getAgentTuiState')('n1');
    st.status = 'busy'; st.retryCount = 5; st._turnSeq = 9;
    st.toolActivities.set('x', { status: 'running' });
    assert.equal(render(d2Node()), '', '工具/重连/回合数不是计划进度');
});

test('#408 D2 时间线按消息/工具/思考交错，连续组、原位更新、回合和系统边界', () => {
    const { ctx } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const events = [
        { type: 'agent_start' }, { type: 'user_prompt', text: '用户甲' }, d2Text('助手甲'),
        d2Tool('a'), d2Tool('b'), d2Text('思考甲', true), d2Text('思考乙', true),
        d2Tool('c'), d2Text('助手乙'), d2Tool('d'),
    ];
    events.forEach(event => handle('n1', event));
    const timeline = () => fromContext(fn(ctx, 'agentTimelineSync')(state).map(entry => entry.kind === 'tools' ? entry.items.map(item => item.toolCallId).join(',') : entry.message.text));
    assert.deepEqual(timeline(), ['用户甲', '助手甲', 'a,b', '思考甲思考乙', 'c', '助手乙', 'd']);
    const keys = fromContext(state.timeline.map(entry => entry.key));
    handle('n1', { type: 'tool_execution_update', toolCallId: 'a', update: 'later' });
    handle('n1', { type: 'tool_execution_end', toolCallId: 'a', result: 'done', duration_ms: 0 });
    handle('n1', d2Tool('a')); // repeated start must not erase completion/expansion
    assert.equal(state.toolActivities.get('a').status, 'done');
    assert.deepEqual(fromContext(state.timeline.map(entry => entry.key)), keys);
    handle('n1', { type: 'turn_end', message: { content: [{ type: 'text', text: '助手乙' }] } });
    assert.equal(state.messages.filter(message => message.text === '助手乙').length, 1, '工具结束不能导致 turn_end 重复正文');
    fn(ctx, 'sendAgentSteer')('n1', 'p', '系统边界');
    handle('n1', d2Tool('e'));
    handle('n1', { type: 'agent_start' });
    handle('n1', d2Tool('f'));
    assert.deepEqual(fromContext(state.timeline.filter(entry => entry.kind === 'tools').map(entry => entry.key)), ['tools:a', 'tools:c', 'tools:d', 'tools:e', 'tools:f']);
    const html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
    const positions = ['用户甲', '助手甲', 'data-tool-call-id="a"', '思考甲思考乙', 'data-tool-call-id="c"', '助手乙'].map(text => html.indexOf(text));
    assert.ok(positions.every((position, i) => position >= 0 && (!i || position > positions[i - 1])));
    assert.doesNotMatch(html, /<details[^>]*agent-tui-tool-group[^>]*\sopen>/);
});

test('#408 D2 思考归档位置/手动展开跨流式刷新保留，全局 brain 开关仍可重置', () => {
    const { ctx, harness } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    const state = fn(ctx, 'getAgentTuiState')('n1');
    handle('n1', d2Text('<think>', true));
    let html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.ok(html.includes('思考中') && html.includes('&lt;think&gt;'));
    assert.doesNotMatch(html, /思考(?:中|了)\s*\d/);
    const key = state.timeline[0].key;
    const tree = buildAgentControlTree(harness);
    const thought = harness.makeElement('details');
    thought.setAttribute('data-agent-thought', key);
    thought.isConnected = true;
    const originalQuery = tree.el.querySelectorAll;
    tree.el.querySelectorAll = selector => selector === '[data-agent-thought]' ? [thought] : originalQuery(selector);
    fn(ctx, 'bindAgentNodeControls')(tree.el, d2Node());
    thought.open = true; thought.dispatchEvent(ev('toggle'));
    handle('n1', d2Text(' more', true));
    html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.match(html, /data-agent-thought="entry:1" open>/);
    handle('n1', d2Tool('a'));
    handle('n1', { type: 'turn_end' });
    html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.equal((html.match(/data-agent-thought=/g) || []).length, 1);
    assert.ok(html.includes('思考了') && !html.includes('思考中'));
    assert.match(html, /data-agent-thought="entry:1" open>/);
    assert.ok(html.indexOf('data-agent-thought') < html.indexOf('data-tool-group'));
    tree.menu.open = true;
    tree.thinking.dispatchEvent(ev('click'));
    assert.equal(tree.menu.open, false, 'more menu closes when the toggle is used');
    tree.thinking.dispatchEvent(ev('click'));
    thought.open = false; thought.dispatchEvent(ev('toggle'));
    thought.open = true; thought.dispatchEvent(ev('toggle'));
    assert.deepEqual(fromContext(state.thoughtExpanded), {}, 'an old queued native toggle cannot restore the cleared override');
    html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.doesNotMatch(html, /<details[^>]*agent-tui-thought[^>]*\sopen>/);
});

for (const duration of [undefined, null, NaN, Infinity, -1, '10', {}, 0, 1500]) {
    test(`#408 D2 工具 duration 不把 ${String(duration)} 伪装成 0`, () => {
        const { ctx } = loadAgentTuiModule();
        const handle = fn(ctx, 'handleAgentEvent');
        handle('n1', d2Tool('a'));
        handle('n1', { type: 'tool_execution_end', toolCallId: 'a', duration_ms: duration });
        const html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
        const known = typeof duration === 'number' && Number.isFinite(duration) && duration >= 0;
        assert.equal(html.includes('agent-tui-tool-duration'), known);
        assert.equal(html.includes(' · 总 '), known);
        if (duration === 0) assert.ok(html.includes('总 0.0s'));
        handle('n1', d2Tool('b'));
        handle('n1', { type: 'tool_execution_end', toolCallId: 'b', isError: true });
        const partial = fn(ctx, 'agentNodeBodyHtml')(d2Node());
        assert.ok(!partial.includes(' · 总 '), '部分计时不得显示总时长');
        assert.ok(partial.includes('agent-tui-tool-error') && partial.includes('1 失败'));
    });
}

test('#408 D2 组/工具展开、长输出与 todo 原内容保留；跳转仅现存 args ID', () => {
    const { ctx, harness } = loadAgentTuiModule({ nodes: [d2Node(), { id: 'target' }] });
    const handle = fn(ctx, 'handleAgentEvent');
    const state = fn(ctx, 'getAgentTuiState')('n1');
    handle('n1', { ...d2Tool('a'), args: { node_id: 'target', node_ids: ['missing', 'target', null] } });
    handle('n1', { type: 'tool_execution_end', toolCallId: 'a', result: '<result>' + 'x'.repeat(601), duration_ms: 1 });
    let html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.equal((html.match(/data-agent-step-jump=/g) || []).length, 1);
    assert.ok(html.includes('data-agent-step-jump="target"'));
    assert.ok(html.includes('agent-tui-tool-result-clamped') && html.includes('&lt;result&gt;'));
    assert.deepEqual(fromContext(fn(ctx, 'agentToolNodeIds')(null)), []);
    const el = harness.makeElement('div');
    const group = harness.makeElement('details'); group.setAttribute('data-tool-group', 'tools:a');
    const output = harness.makeElement('button'); output.setAttribute('data-expand-output', 'a');
    const jump = harness.makeElement('button'); jump.setAttribute('data-agent-step-jump', 'target');
    el.querySelectorAll = selector => ({ '[data-tool-group]': [group], '[data-expand-output]': [output], '[data-agent-step-jump]': [jump] }[selector] || []);
    const focused = []; ctx.focusRevealNode = id => focused.push(id);
    fn(ctx, 'bindAgentNodeControls')(el, d2Node());
    group.open = true; group.dispatchEvent(ev('toggle'));
    output.dispatchEvent(ev('click'));
    handle('n1', { type: 'tool_execution_update', toolCallId: 'a', update: 'stream' });
    html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.match(html, /open data-tool-group="tools:a"/);
    assert.ok(!html.includes('agent-tui-tool-result-clamped'));
    assert.equal(state.toolActivities.get('a').resultExpanded, true);
    jump.dispatchEvent(ev('click'));
    ctx.canvasState.nodes.pop();
    jump.dispatchEvent(ev('click'));
    assert.deepEqual(focused, ['target']);
    assert.ok(!fn(ctx, 'agentNodeBodyHtml')(d2Node()).includes('data-agent-step-jump='));
});

test('#408 D2 进度停止和 footer 停止均绑定，失败保留真实快照/提示，不写已停止', async () => {
    const node = { ...d2Node(), agentRun: { state: '执行中', step: 1, total: 5 } };
    const { ctx, harness, fetchCalls } = loadAgentTuiModule({ nodes: [node], fetchResponse: { ok: false, status: 503, json: async () => ({}) } });
    const state = fn(ctx, 'getAgentTuiState')('n1');
    state.reconnectStopped = true;
    const buttons = [harness.makeElement('button'), harness.makeElement('button')];
    const el = harness.makeElement('div');
    el.querySelectorAll = selector => selector === '[data-agent-abort]' ? buttons : [];
    fn(ctx, 'bindAgentNodeControls')(el, node);
    for (const button of buttons) { button.dispatchEvent(ev('click')); await d2Drain(); }
    assert.equal(fetchCalls.filter(call => call.url.endsWith('/abort')).length, 2);
    assert.equal(state.messages.filter(message => message.text.includes('停止请求未送达')).length, 2);
    assert.ok(!state.messages.some(message => message.text.includes('aborted')));
    assert.deepEqual(node.agentRun, { state: '执行中', step: 1, total: 5 });
    assert.ok(fn(ctx, 'agentNodeBodyHtml')(node).includes('执行中 · 1/5'));
});

test('#408 D2 seq 去重、刷新全回放重建与增量回放不重复/不重排', async () => {
    const records = [{ type: 'agent_start' }, { type: 'user_prompt', text: 'history' }, d2Text('think', true), d2Tool('a'), d2Tool('b'), { type: 'tool_execution_end', toolCallId: 'a', duration_ms: 0 }, { type: 'agent_end' }].map((event, i) => ({ seq: i + 1, event }));
    const { ctx } = loadAgentTuiModule({ fetchResponse: { ok: true, json: async () => ({ events: records }) } });
    ctx.document.hidden = true; ctx.document.title = 'Canvas';
    const state = fn(ctx, 'getAgentTuiState')('n1'); state.projectId = 'p1';
    fn(ctx, 'agentAppendMessage')(state, { role: 'system', text: 'stale' });
    fn(ctx, 'replayAgentEvents')('n1', state);
    fn(ctx, 'handleAgentEvent')('n1', { ...d2Tool('b'), seq: 5 }); // buffered duplicate
    await d2Drain();
    assert.equal(state.lastSeq, 7);
    assert.equal(ctx.document.title, 'Canvas', '历史 agent_end 不是后台新完成');
    const html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.ok(!html.includes('stale'));
    assert.equal((html.match(/data-tool-call-id=/g) || []).length, 2);
    assert.equal((html.match(/data-agent-thought=/g) || []).length, 1);
    state.toolGroupExpanded['tools:a'] = true;
    fn(ctx, 'replayAgentEvents')('n1', state);
    await d2Drain();
    assert.equal(state.timeline.length, 3);
    assert.equal(state.toolGroupExpanded['tools:a'], true, '增量补拉不清手动展开');
    fn(ctx, 'handleAgentEvent')('n1', { ...d2Tool('a'), seq: 4 });
    assert.equal(state.toolActivities.get('a').status, 'done');
});

test('#408 D2 历史替换/branch/fork 重置元数据，迟到的旧回放不能污染新分支', async () => {
    let resolveReplay;
    const { ctx } = loadAgentTuiModule({ nodes: [d2Node()], fetchResponse: url => url.includes('/events?')
        ? new Promise(resolve => { resolveReplay = resolve; })
        : { ok: true, json: async () => ({ current_branch: 'new', branches: [],
            canvas: { revision: 2, nodes: [{ ...d2Node(), current_branch: 'new', branches: [] }] } }) } });
    const state = fn(ctx, 'getAgentTuiState')('n1');
    fn(ctx, 'handleAgentEvent')('n1', d2Tool('old'));
    state.toolGroupExpanded = { 'tools:old': true }; state.scrollState = { atBottom: false, scrollTop: 10 };
    state.messages = [{ role: 'assistant', text: 'restored' }]; state.toolActivities.clear();
    const html = fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.ok(html.includes('restored') && !html.includes('tools:old'));
    assert.deepEqual(fromContext(state.toolGroupExpanded), {});
    state.projectId = 'p';
    fn(ctx, 'replayAgentEvents')('n1', state);
    await fn(ctx, 'switchAgentBranch')('n1', 'p', 'new');
    resolveReplay({ ok: true, json: async () => ({ events: [{ seq: 99, event: d2Tool('late') }] }) });
    await d2Drain();
    assert.equal(state.timeline.length, 0); assert.equal(state.toolActivities.size, 0); assert.equal(state.lastSeq, 0);
    fn(ctx, 'handleAgentEvent')('n1', d2Text('new branch', true));
    await fn(ctx, 'forkAgentSession')('n1', 'p', 'entry', 'fork');
    assert.equal(state.timeline.length, 0); assert.equal(state.thinking.length, 0); assert.equal(state.scrollState, null);
});

// Minimal DOM/event geometry surface. No scrolling/follow product logic is duplicated here.
function d2ScrollDom(harness, { top = 700, height = 1000, client = 200, rows = [['entry:1', 600, 100], ['entry:2', 700, 100], ['entry:3', 800, 100]] } = {}) {
    const el = harness.makeElement('div');
    const body = harness.makeElement('div');
    const messages = harness.makeElement('div');
    const latest = harness.makeElement('button');
    const listeners = new Map();
    body.scrollTop = top; body.scrollHeight = height; body.clientHeight = client; body.isConnected = true;
    body.getBoundingClientRect = () => ({ top: 0, bottom: body.clientHeight, height: body.clientHeight });
    const rowElements = rows.map(([key, y, h]) => {
        const row = harness.makeElement('div'); row.y = y; row.h = h;
        row.setAttribute('data-agent-entry-key', key);
        row.getBoundingClientRect = () => {
            const shift = parseFloat((messages.style.translate || '').split(' ')[1]) || 0;
            return { top: row.y - body.scrollTop + shift, bottom: row.y + row.h - body.scrollTop + shift, height: row.h };
        };
        return row;
    });
    body.querySelectorAll = selector => selector === '[data-agent-entry-key]' ? rowElements : [];
    body.querySelector = selector => selector === '[data-agent-tui-messages]' ? messages : null;
    body.addEventListener = (type, listener) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(listener); };
    body.removeEventListener = (type, listener) => listeners.get(type)?.delete(listener);
    body.dispatchEvent = event => [...(listeners.get(event.type) || [])].forEach(listener => listener(event));
    el.querySelector = selector => ({ '[data-agent-tui-body]': body, '[data-agent-tui-messages]': messages, '[data-agent-back-latest]': latest }[selector] || null);
    return { el, body, messages, latest, rows: rowElements, listeners };
}

function d2Observers(ctx) {
    const sizes = [], mutations = [];
    class Observer {
        constructor(callback) { this.callback = callback; this.targets = []; this.disconnected = false; }
        observe(target) { this.targets.push(target); }
        disconnect() { this.disconnected = true; }
        fire() { if (!this.disconnected) this.callback([]); }
    }
    ctx.ResizeObserver = class extends Observer { constructor(callback) { super(callback); sizes.push(this); } };
    ctx.MutationObserver = class extends Observer { constructor(callback) { super(callback); mutations.push(this); } };
    return { sizes, mutations };
}

test('#422 已挂载的流式文字更新原位渲染，不重建整棵历史', () => {
    const { ctx, harness } = loadAgentTuiModule();
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const tree = d2ScrollDom(harness, { top: 500 });
    fn(ctx, 'bindAgentScroll')(tree.el, state);
    const msg = { role: 'assistant', text: 'hello', streaming: true };
    fn(ctx, 'agentAppendMessage')(state, msg);
    const row = harness.makeElement('div');
    const text = harness.makeElement('div');
    row.querySelector = selector => selector === '.agent-tui-msg-streaming .agent-tui-msg-text' ? text : null;
    tree.body.querySelector = selector => selector === '[data-agent-entry-key="entry:1"]' ? row : null;
    let renders = 0;
    ctx.renderAgentNode = () => { renders++; };
    tree.body.dispatchEvent(ev('wheel'));
    tree.body.scrollTop = 400;
    tree.body.dispatchEvent(ev('scroll'));
    for (const delta of [' world', ' & <safe>']) {
        fn(ctx, 'handleAgentEvent')('n1', { type: 'message_update', assistantMessageEvent: { type: 'text_delta', delta } });
    }
    harness.flushRafs();
    assert.equal(msg.text, 'hello world & <safe>');
    assert.match(text.innerHTML, /hello world/);
    assert.ok(!text.innerHTML.includes('<safe>'), '流式增量仍须安全转义');
    assert.equal(renders, 0, '增量不能重建整个对话栏');
    assert.equal(tree.body.scrollTop, 400, '用户上滚后输出不能拉回底部');
    assert.equal(tree.latest.hidden, false);
});

test('#422 用户滚轮意图在 ResizeObserver 通知后仍能脱离底部', () => {
    const { ctx, harness } = loadAgentTuiModule();
    const { sizes } = d2Observers(ctx);
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const tree = d2ScrollDom(harness, { top: 800 });
    fn(ctx, 'bindAgentScroll')(tree.el, state);
    harness.flushRafs();
    tree.body.dispatchEvent(ev('wheel'));
    sizes[0].fire();
    tree.body.scrollTop = 500;
    tree.body.dispatchEvent(ev('scroll'));
    assert.equal(state.scrollState.atBottom, false, '布局通知不能吞掉正在发生的用户滚动');
});

test('#426 分页插入小数高度后整数 scrollTop 仍须严格保持原锚点', () => {
    const { ctx, harness } = loadAgentTuiModule();
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const old = d2ScrollDom(harness, { top: 0, height: 1200, rows: [['entry:1', 81.984375, 100]] });
    fn(ctx, 'captureAgentScroll')(state, old.body, true);
    const before = old.rows[0].getBoundingClientRect().top - old.body.getBoundingClientRect().top;
    const next = d2ScrollDom(harness, { top: 0, height: 3200, rows: [['entry:1', 1678.703125, 100]] });
    let top = 0;
    Object.defineProperty(next.body, 'scrollTop', { get: () => top, set: value => { top = Math.round(value); } });
    fn(ctx, 'restoreAgentScroll')(state, next.body);
    const after = next.rows[0].getBoundingClientRect().top - next.body.getBoundingClientRect().top;
    assert.equal(after, before, '分页后同一行几何偏移必须严格不变');
});

for (const zoom of [1, 0.75]) {
    test(`#426 小数视口高度下缩放 ${zoom} 的重复恢复不漂移`, () => {
        const { ctx, harness } = loadAgentTuiModule();
        const state = fn(ctx, 'getAgentTuiState')('n1');
        const tree = d2ScrollDom(harness, { top: 10000, height: 20000, client: 726,
            rows: [['entry:63', 9958.84375, 100]] });
        let top = 10000;
        Object.defineProperty(tree.body, 'scrollTop', { get: () => top, set: value => { top = Math.round(value); } });
        tree.body.getBoundingClientRect = () => ({ top: 0, bottom: 726.421875 * zoom, height: 726.421875 * zoom });
        tree.rows[0].getBoundingClientRect = () => ({ top: (tree.rows[0].y - top) * zoom, bottom: (tree.rows[0].y + 100 - top) * zoom });
        ctx.getComputedStyle = () => ({ height: '726.421875px', boxSizing: 'border-box' });
        fn(ctx, 'captureAgentScroll')(state, tree.body, true);
        const initial = tree.rows[0].getBoundingClientRect().top;
        for (let i = 0; i < 4; i++) {
            tree.body.scrollTop = 0; // a newly rendered scrolling element
            fn(ctx, 'restoreAgentScroll')(state, tree.body);
            assert.equal(tree.rows[0].getBoundingClientRect().top, initial, `第 ${i + 1} 次恢复偏移`);
            fn(ctx, 'captureAgentScroll')(state, tree.body);
        }
    });
}

for (const distance of [47, 48, 49]) {
    test(`#408 D2 OLD layout ${distance}px 边界；追加增高不能反推 follow`, () => {
        const { ctx, harness } = loadAgentTuiModule();
        const state = fn(ctx, 'getAgentTuiState')('n1');
        const old = d2ScrollDom(harness, { top: 800 - distance });
        fn(ctx, 'captureAgentScroll')(state, old.body);
        assert.equal(state.scrollState.atBottom, distance <= 48);
        const next = d2ScrollDom(harness, { top: 0, height: 1400 });
        fn(ctx, 'bindAgentScroll')(next.el, state);
        harness.flushRafs();
        assert.equal(next.body.scrollTop, distance <= 48 ? 1200 : 751);
        assert.equal(state.scrollState.atBottom, distance <= 48);
    });
}

test('#408 D2 detached 首可见项+偏移锚点跨重建/展开/图片加载/resize，回到最新恢复 follow', () => {
    const { ctx, harness } = loadAgentTuiModule();
    const observers = d2Observers(ctx);
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const old = d2ScrollDom(harness, { top: 720 });
    fn(ctx, 'captureAgentScroll')(state, old.body);
    assert.deepEqual(fromContext(state.scrollState.anchor), { key: 'entry:2', offset: -20 });
    const next = d2ScrollDom(harness, { top: 0, height: 1600, rows: [['entry:1', 800, 100], ['entry:2', 900, 100], ['entry:3', 1000, 100]] });
    state.scrollState.unread = true;
    fn(ctx, 'bindAgentScroll')(next.el, state);
    harness.flushRafs();
    assert.equal(next.body.scrollTop, 920); assert.equal(next.latest.hidden, false);
    next.body.dispatchEvent(ev('mousedown')); // before expansion
    next.rows.forEach(row => { row.y += 80; });
    next.body.dispatchEvent(ev('toggle'));
    harness.flushRafs();
    assert.equal(next.body.scrollTop, 1000);
    next.rows.forEach(row => { row.y += 30; });
    next.body.dispatchEvent(ev('load'));
    observers.sizes[0].fire(); observers.sizes[0].fire();
    assert.equal(harness.rafQueue.length, 1, '布局事件合并，不能触发 render/rAF 自循环');
    harness.flushRafs();
    assert.equal(next.body.scrollTop, 1030);
    assert.equal(harness.rafQueue.length, 0);
    next.body.clientHeight = 150; observers.sizes[0].fire(); harness.flushRafs();
    assert.equal(next.body.scrollTop, 1030);
    next.latest.dispatchEvent(ev('click'));
    assert.equal(next.body.scrollTop, 1450); assert.equal(state.scrollState.atBottom, true); assert.equal(next.latest.hidden, true);
    next.body.scrollHeight += 500; observers.sizes[0].fire(); harness.flushRafs();
    assert.equal(next.body.scrollTop, 1950, '回最新后继续随内容尺寸跟随');
});

test('#408 D2 用户滚动与程序/原生布局 scroll 不混淆；detached 新流式内容才显示回最新', () => {
    const { ctx, harness } = loadAgentTuiModule();
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const tree = d2ScrollDom(harness, { top: 800 });
    fn(ctx, 'bindAgentScroll')(tree.el, state); harness.flushRafs();
    tree.body.scrollHeight = 1400;
    tree.body.dispatchEvent(ev('scroll')); // append/native anchoring, not user intent
    assert.equal(state.scrollState.atBottom, true);
    tree.body.dispatchEvent(ev('wheel'));
    tree.body.scrollTop = 650; tree.body.dispatchEvent(ev('scroll'));
    assert.equal(state.scrollState.atBottom, false); assert.equal(tree.latest.hidden, true);
    fn(ctx, 'agentNodeBodyHtml')(d2Node());
    fn(ctx, 'handleAgentEvent')('n1', d2Text('new content'));
    fn(ctx, 'agentNodeBodyHtml')(d2Node());
    fn(ctx, 'updateAgentBackLatest')(state);
    assert.equal(tree.latest.hidden, false);
    fn(ctx, 'restoreAgentScroll')(state, tree.body);
    tree.body.dispatchEvent(ev('scroll'));
    assert.equal(state.scrollState.atBottom, false);
    tree.body.dispatchEvent(ev('wheel'));
    tree.body.scrollTop = 1152; tree.body.dispatchEvent(ev('scroll'));
    assert.equal(state.scrollState.atBottom, true); assert.equal(tree.latest.hidden, true);
    harness.advanceTimers(); // user-intent lease expires; later focus/native scroll is not user intent.
    tree.body.scrollTop = 700; tree.body.dispatchEvent(ev('scroll'));
    assert.equal(state.scrollState.atBottom, true);
});

test('#408 D2 只展开 UI 不算新内容，detached 回最新不应误亮', () => {
    const { ctx } = loadAgentTuiModule();
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const handle = fn(ctx, 'handleAgentEvent');
    handle('n1', d2Tool('a'));
    handle('n1', { type: 'tool_execution_end', toolCallId: 'a', result: 'x'.repeat(700) });
    state.scrollState = { scrollTop: 120, atBottom: false, anchor: null, unread: false };
    fn(ctx, 'agentNodeBodyHtml')(d2Node());
    state.toolGroupExpanded = { 'tools:a': true };
    state.toolActivities.get('a').expanded = true; state.toolActivities.get('a').resultExpanded = true;
    fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.equal(state.scrollState.unread, false);
    handle('n1', { type: 'tool_execution_update', toolCallId: 'a', update: 'new update' });
    fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.equal(state.scrollState.unread, true);
});

test('#408 D2 找不到锚点回退 old scrollTop；重绑定/DOM detach/cleanup 清理观察器 rAF 和监听', () => {
    const { ctx, harness } = loadAgentTuiModule();
    const { sizes, mutations } = d2Observers(ctx);
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const old = d2ScrollDom(harness);
    fn(ctx, 'captureAgentScroll')(state, old.body);
    const next = d2ScrollDom(harness, { top: 0, rows: [] });
    fn(ctx, 'bindAgentScroll')(next.el, state);
    assert.equal(next.body.scrollTop, 700);
    const third = d2ScrollDom(harness);
    fn(ctx, 'bindAgentScroll')(third.el, state);
    assert.equal(sizes[0].disconnected, true); assert.equal(mutations[0].disconnected, true);
    assert.equal([...next.listeners.values()].reduce((sum, set) => sum + set.size, 0), 0);
    third.body.isConnected = false; mutations[1].fire();
    assert.equal(state.scrollBinding, null); assert.equal(harness.rafQueue.length, 0);
    const last = d2ScrollDom(harness);
    fn(ctx, 'bindAgentScroll')(last.el, state);
    fn(ctx, 'cleanupAgentTuiState')('n1');
    assert.equal(sizes[2].disconnected, true); assert.equal(mutations[2].disconnected, true);
    assert.equal(harness.rafQueue.length, 0);
    assert.equal([...last.listeners.values()].reduce((sum, set) => sum + set.size, 0), 0);
});

test('#408 D2 IME 延迟重建，草稿/焦点/选区以及 detached 阅读位置保留', () => {
    const node = d2Node();
    const { ctx, harness } = loadAgentTuiModule({ nodes: [node] });
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const tree = buildAgentControlTree(harness);
    const scroll = d2ScrollDom(harness, { top: 720 });
    const query = tree.el.querySelector;
    tree.el.querySelector = selector => scroll.el.querySelector(selector) || query(selector);
    const outer = harness.makeElement('div');
    const container = tree.el;
    outer.querySelector = selector => selector === '.node-body' ? container : container.querySelector(selector);
    harness.querySelectorAll('[data-id="n1"]', [outer]);
    tree.input.selectionStart = 1; tree.input.selectionEnd = 3;
    tree.input.focus = () => { ctx.document.activeElement = tree.input; };
    const selections = [];
    tree.input.setSelectionRange = (start, end) => { selections.push([start, end]); tree.input.selectionStart = start; tree.input.selectionEnd = end; };
    tree.input.focus();
    fn(ctx, 'bindAgentNodeControls')(container, node);
    tree.input.value = '草稿输入'; tree.input.dispatchEvent(ev('input'));
    tree.input.dispatchEvent(ev('compositionstart'));
    fn(ctx, 'handleAgentEvent')('n1', d2Text('stream'));
    assert.equal(state.pendingRerender, true); assert.equal(container.innerHTML, '');
    ctx.nodeBodyHtml = liveNode => fn(ctx, 'agentNodeBodyHtml')(liveNode);
    tree.input.dispatchEvent(ev('compositionend'));
    assert.equal(state.pendingRerender, false); assert.equal(state.draft, '草稿输入');
    assert.ok(container.innerHTML.includes('草稿输入'));
    assert.deepEqual(selections, [[1, 3]], '不能强制把 IME 选区移到末尾');
    // The initial bind follows by default; explicitly test detached rendering capture too.
    scroll.body.dispatchEvent(ev('wheel')); scroll.body.scrollTop = 720; scroll.body.dispatchEvent(ev('scroll'));
    fn(ctx, 'renderAgentNode')('n1');
    assert.equal(scroll.body.scrollTop, 720);
    assert.equal(ctx.document.activeElement, tree.input);
    assert.ok(!Object.keys(node).some(key => /scroll|expanded|timeline/i.test(key)));
});

test('#408 D2 真实重建表面替换 input/body 后，恢复 draft/selection 和偏移锚点', () => {
    const node = d2Node();
    const { ctx, harness } = loadAgentTuiModule({ nodes: [node] });
    const state = fn(ctx, 'getAgentTuiState')('n1');
    let current = d2ScrollDom(harness, { top: 720 });
    let input = harness.makeElement('textarea');
    input.value = '未发送草稿'; input.selectionStart = 2; input.selectionEnd = 4;
    ctx.document.activeElement = input;
    const oldInput = input;
    const oldBody = current.body;
    const selections = [];
    const container = harness.makeElement('div');
    container.querySelector = selector => selector === '[data-agent-tui-input]' ? input : current.el.querySelector(selector);
    let markup = '';
    Object.defineProperty(container, 'innerHTML', { get: () => markup, set: value => {
        markup = value;
        oldBody.isConnected = false;
        current = d2ScrollDom(harness, { top: 0, height: 1600, rows: [['entry:1', 800, 100], ['entry:2', 900, 100]] });
        input = harness.makeElement('textarea'); input.value = state.draft;
        input.focus = () => { ctx.document.activeElement = input; };
        input.setSelectionRange = (start, end) => selections.push([start, end]);
    } });
    const outer = harness.makeElement('div');
    outer.querySelector = selector => selector === '.node-body' ? container : container.querySelector(selector);
    harness.querySelectorAll('[data-id="n1"]', [outer]);
    fn(ctx, 'captureAgentScroll')(state, oldBody);
    fn(ctx, 'bindAgentScroll')(container, state); harness.flushRafs();
    ctx.nodeBodyHtml = liveNode => fn(ctx, 'agentNodeBodyHtml')(liveNode);
    fn(ctx, 'renderAgentNode')('n1'); harness.flushRafs();
    assert.notEqual(input, oldInput); assert.notEqual(current.body, oldBody);
    assert.equal(input.value, '未发送草稿'); assert.equal(ctx.document.activeElement, input);
    assert.ok(markup.includes('未发送草稿'));
    assert.deepEqual(selections, [[2, 4]]);
    assert.equal(current.body.scrollTop, 920);
    assert.equal(state.scrollBinding.body, current.body);
    fn(ctx, 'agentTuiSendInput')(input, 'n1', 'p');
    assert.equal(input.value, '', 'sender 重建的新输入框不能复活已发送草稿');
    assert.equal(state.draft, '');
    assert.ok(state.messages.some(message => message.role === 'user' && message.text === '未发送草稿'));
});

test('#408 D2 D1 收起期间 streaming/resize 不清掉 detached 阅读锚点', () => {
    const { ctx, harness } = loadAgentTuiModule();
    const observers = d2Observers(ctx);
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const tree = d2ScrollDom(harness, { top: 720 });
    fn(ctx, 'captureAgentScroll')(state, tree.body);
    fn(ctx, 'bindAgentScroll')(tree.el, state); harness.flushRafs();
    const before = fromContext(state.scrollState);
    tree.body.clientHeight = 0; tree.body.scrollHeight = 0; tree.body.scrollTop = 0;
    observers.sizes[0].fire(); harness.flushRafs();
    fn(ctx, 'agentNodeBodyHtml')(d2Node());
    fn(ctx, 'handleAgentEvent')('n1', d2Text('collapsed stream'));
    fn(ctx, 'agentNodeBodyHtml')(d2Node());
    assert.equal(state.scrollState.scrollTop, before.scrollTop);
    assert.deepEqual(fromContext(state.scrollState.anchor), before.anchor);
    tree.body.clientHeight = 200; tree.body.scrollHeight = 1400;
    observers.sizes[0].fire(); harness.flushRafs();
    assert.equal(tree.body.scrollTop, 720);
});

test('#408 D2 新 turn_start 切断工具组，stopReason aborted 不报完成', () => {
    const { ctx } = loadAgentTuiModule();
    ctx.document.hidden = true; ctx.document.title = 'Canvas';
    const handle = fn(ctx, 'handleAgentEvent');
    handle('n1', { type: 'agent_start' }); handle('n1', d2Tool('a'));
    handle('n1', { type: 'turn_start' }); handle('n1', d2Tool('b'));
    const state = fn(ctx, 'getAgentTuiState')('n1');
    assert.equal(state.timeline.filter(entry => entry.kind === 'tools').length, 2);
    handle('n1', { type: 'turn_end', message: { stopReason: 'aborted' } });
    handle('n1', { type: 'agent_end' });
    assert.equal(ctx.document.title, 'Canvas');
});

test('#408 D2 WS stop 送达抛错不能写成功，断线后 agent_end 也不报完成', () => {
    const { ctx } = loadAgentTuiModule();
    const state = fn(ctx, 'getAgentTuiState')('n1');
    const socket = fn(ctx, 'connectAgentWebSocket')('n1', 'p');
    socket.send = () => { throw new Error('closed meanwhile'); };
    assert.doesNotThrow(() => fn(ctx, 'sendAgentAbort')('n1', 'p'));
    assert.ok(state.messages.some(message => message.text.includes('停止请求未送达')));
    assert.ok(!state.messages.some(message => message.text.includes('aborted')));
    ctx.document.hidden = true; ctx.document.title = 'Canvas';
    fn(ctx, 'handleAgentEvent')('n1', { type: 'agent_start' });
    socket.onclose();
    fn(ctx, 'handleAgentEvent')('n1', { type: 'agent_end' });
    assert.equal(ctx.document.title, 'Canvas');
});

test('#408 D2 混合工具组只把结束项计入执行数量，进行项单独列出', () => {
    const { ctx } = loadAgentTuiModule();
    evaluate(ctx, `(() => {
        const state = getAgentTuiState('n1');
        state.toolActivities.set('ended', {name:'read', status:'done', turn:1, duration_ms:120});
        state.toolActivities.set('live', {name:'write', status:'running', turn:1, duration_ms:null});
    })()`);
    const html = fromContext(fn(ctx, 'agentNodeBodyHtml')({id:'n1', type:'smart-agent'}));
    assert.ok(html.includes('执行了 1 个步骤 · 1 个步骤执行中…'), html);
    assert.ok(!html.includes('执行了 2 个步骤'), html);
    assert.ok(!html.includes(' · 总 '), '部分计时不得写成完整总耗时');
});

test('#408 D2 已核实工具名做有限语义映射，未知工具不造动作对象', () => {
    const { ctx } = loadAgentTuiModule();
    evaluate(ctx, `canvasState.nodes = [{id:'target', title:'主图3 <产品>'}]`);
    const label = fn(ctx, 'agentToolSummary');
    assert.equal(fromContext(label({name:'canvas.get_node',args:{node_id:'target'}})), '读取节点 · 主图3 <产品>');
    assert.equal(fromContext(label({name:'canvas.create_node',args:{title:'新卡'}})), '新建节点 · 新卡');
    assert.equal(fromContext(label({name:'generate.status',args:{}})), '查看生成状态');
    assert.equal(fromContext(label({name:'unregistered.tool',args:{node_id:'target'}})), 'unregistered.tool');
    assert.equal(fromContext(label({name:'canvas.get_node',args:{node_id:'deleted'}})), '读取节点');
    evaluate(ctx, `getAgentTuiState('n1').toolActivities.set('known',{name:'canvas.get_node',args:{node_id:'target'},status:'done',turn:1})`);
    const html = fromContext(fn(ctx, 'agentNodeBodyHtml')({id:'n1',type:'smart-agent'}));
    assert.ok(html.includes('读取节点 · 主图3 &lt;产品&gt;'), html);
    assert.ok(html.includes('title="canvas.get_node"'), html);
});

test('#408 D2 后台仅新 agent_end 完成加自有前缀，前台清除；失败/中止/历史/缺 API 不误报', () => {
    const { ctx, harness } = loadAgentTuiModule();
    const handle = fn(ctx, 'handleAgentEvent');
    ctx.document.hidden = true; ctx.document.title = '画布';
    handle('n1', { type: 'agent_start' }); handle('n1', { type: 'agent_end' }); handle('n1', { type: 'agent_end' });
    assert.equal(ctx.document.title, '(✓) 画布');
    ctx.document.hidden = false; harness.fireDocumentEvent('visibilitychange');
    assert.equal(ctx.document.title, '画布');
    ctx.document.hidden = true; ctx.document.title = '(✓) external';
    handle('n1', { type: 'agent_start' }); handle('n1', { type: 'agent_end' });
    ctx.document.hidden = false; harness.fireDocumentEvent('visibilitychange');
    assert.equal(ctx.document.title, '(✓) external', '非自有前缀不能删除');
    ctx.document.title = '画布'; ctx.document.hidden = true;
    for (const failure of [{ type: 'process_exited' }, { type: 'error', message: 'bad' }, { type: 'turn_end', message: { stopReason: 'error' } }]) {
        handle('n1', { type: 'agent_start' }); handle('n1', failure); handle('n1', { type: 'agent_end' });
        assert.equal(ctx.document.title, '画布');
    }
    handle('n1', { type: 'agent_start' });
    fn(ctx, 'getAgentTuiState')('n1').socket = new ctx.WebSocket('ws:test');
    fn(ctx, 'sendAgentAbort')('n1', 'p'); handle('n1', { type: 'agent_end' });
    assert.equal(ctx.document.title, '画布');
    delete ctx.document.title; delete ctx.document.addEventListener;
    assert.doesNotThrow(() => { handle('n1', { type: 'agent_start' }); handle('n1', { type: 'agent_end' }); });
});
