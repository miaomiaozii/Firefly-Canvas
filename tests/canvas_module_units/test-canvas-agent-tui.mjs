// #199 轨道 4：canvas-agent-tui.js 行为单元测试（耦合报告里所有未单测模块中自由面最小的一个：
// 12 处 / 9 独立名 / 自由 12 / 9 名 / 可注入 3；在 canvas-connections.js（自由 12/9）与
// canvas-node-ops.js（自由 17/14）之后的下一个模块）。
//
// canvas-agent-tui.js 是画布 Agent TUI 会话节点模块（#154 从 smart-canvas.js 抽离；
// renderMarkdown 由 #200 从核心搬回）：isSmartAgentNode/isSmartAgentTaskNode 谓词、
// agentTuiState 状态容器、WebSocket 连接与事件状态机（handleAgentEvent）、命令发送
// （prompt/steer/follow_up/abort/confirm）、模型/自治/分支/分叉（fetch）、卡片与 TUI 主体
// HTML 渲染（agentCardStatusHtml/agentNodeBodyHtml/agentTaskNodeBodyHtml/renderMarkdown）、
// 控件绑定（bindAgentNodeControls/bindAgentTaskNodeControls）、cleanupAgentTuiState。
// 本文件顶层只有函数声明 + `const agentTuiState = new Map()`——加载期不触碰任何外部名。
//
// 依赖面实测（口径 = 耦合报告自由侧 9 个独立名 / 12 处引用，全部是 smart-canvas.js 顶层声明）：
//   - 单行契约镜像 1 个：trf（smart-canvas.js:314-316 逐字单表达式，内部调真实 tr
//     canvas-core.js:64；loadModules 之后经 evaluate 定义，与 node-ops 测试同法）；
//   - 状态 2 个：canvasId（smart-canvas.js:2，URL 参数 const）、sourceProjectId（smart-canvas.js:3，同）；
//   - 零逻辑记录器 3 个：nodeBodyHtml（smart-canvas.js:8686，记录调用+返回固定标记串）、
//     render（smart-canvas.js）、centerViewportOnWorldPoint（smart-canvas.js:2050）；
//   - 数据驱动 3 个：imageLayout（smart-canvas.js:1575，测试喂 harness.layout）、
//     chatApiProviders（smart-canvas.js:2329，测试喂 harness.providers）、
//     providerChatModels（smart-canvas.js:2359，测试喂 harness.modelsByProvider）；
//   - 0 个复制产品逻辑（ADR 0020 判据：没有任何一个桩需要「第二套实现」）。
// 报告口径外（不是 smart-canvas.js 顶层声明）的外部名：
//   - 由**加载真实模块**满足（不桩，harness 按 HTML_SCRIPT_ORDER 排序加载）：
//       canvas-node-types.js → NODE_TYPE_AGENT('smart-agent') / NODE_TYPE_TASK('agent-task')
//       canvas-core.js       → canvas(let) / tr（真实实现，含 window.StudioI18n 分支）/
//                              escapeHtml / escapeAttr / nodeScale
//       canvas-state.js      → canvasState
//   - 浏览器/harness 面（非产品逻辑）：
//       WebSocket → 手写可控假对象（class + 实例：url/readyState/sent/onmessage/onclose/
//                   onerror/close），纯浏览器 API 表面积，零产品逻辑；
//       fetch / prompt → harness 默认（禁用/返回 null），loader 替换为数据驱动记录器；
//       console → loader 替换为调用记录器（让 console.error 路径可断言）；
//       window / location / document → harness.mjs 提供。
//
// 防的回归（全部断言可观察输出/副作用，不是「不抛错就算过」）：
//   - 事件状态机：session/agent_start/agent_end/turn_start/turn_end（content[].type==='text'
//     过滤拼接，无文本不追加）、auto_retry_start/end（失败→error+系统消息）、
//     process_exited→crashed、confirm→waiting_confirm+confirm 对象、response/未知类型不变；
//   - 流式：text_delta 追加最近 streaming assistant 消息/非流式消息之后新开，
//     thinking_delta 进独立 thinking 缓冲（默认隐藏）；
//   - tool_execution start/update/end：running→done/error、isError 透传、未知 toolCallId
//     不崩不改状态；
//   - connectAgentWebSocket：ws:/wss: 协议选择、encodeURIComponent 拼 URL、OPEN 时返回同一
//     实例、onmessage 派发到真实 handleAgentEvent、畸形 JSON 安全（console.error 路径）、
//     onclose→disconnected+socket=null、onerror→error、构造器抛错→返回 null；
//   - 发送路径：OPEN socket 走 ws.send（prompt/steer/follow_up/abort/confirm 载荷），否则
//     fetch POST 回退（URL/body 精确断言，abort 无 body）、follow-up/abort 的 onSuccess
//     系统消息与状态、sendAgentConfirm 无 confirm 时早退零副作用；
//   - setAgentModel/setAgentAutonomy 乐观更新（state+node）+ fetch；forkAgentSession/
//     switchAgentBranch 成功更新 node.branches/current_branch 且清空 messages/toolActivities、
//     失败零副作用；showForkPicker 的 prompt 选择→fork(entryId, branchName)、
//     空/越界选择安全；
//   - agentProjectId 优先级 canvas.project_id > canvas.project > sourceProjectId > ''；
//   - agentCardStatusHtml 五态映射 + confirm 覆盖（等待确认）+ 末行 40 字预览与工具活动
//     回退 + 无 state 不创建；agentTaskStatusText 走真实 tr（含 StudioI18n 分支）；
//     agentTaskNodeBodyHtml 的 data-status/error/结果链接（anchor vs muted）；
//     agentNodeBodyHtml 的状态/thinking/消息角色/streaming class/工具图标/confirm 卡片/
//     模型-自治-分支下拉/空态占位/busy 按钮 disabled 门；
//   - renderMarkdown（#200 搬回本模块）：转义、代码块/行内代码、粗斜体、链接协议门
//     （javascript: → href="#"）、列表、段落；
//   - renderAgentNode：主体重渲染（nodeBodyHtml 输出进 .node-body innerHTML）+ 卡片状态进
//     .node-head-meta + 重绑定（WS 实例 +1）、el/node 缺失与 layout=null → 全 no-op；
//   - bindAgentNodeControls：Enter 发送（trim/空串/纯空白 no-op、busy→follow_up、
//     Shift+Enter no-op）、thinking 切换、steer/follow-up/abort、批准/取消、模型/自治/
//     分支 change、fork、工具行展开；bindAgentTaskNodeControls：结果链接点击→选中结果
//     节点 + centerViewportOnWorldPoint(x+120, y+100) + render；
//   - cleanupAgentTuiState：socket.close + 状态移除（删节点联动已由 test-canvas-node-ops.mjs
//     的「deleteNode: agent 节点经真实 cleanupAgentTuiState 清 TUI 状态」覆盖，本文件测
//     清理函数本体）。
//
// 判为**本轮单测不覆盖**的行为（不是「永远绿的假断言」，是预算/边界外的真实路径）：
//   - showForkPicker 的完整 UI 选择器：真实实现用 window.prompt（源码注释明示 "In a full
//     implementation, this would be a UI picker"）；prompt 选择流程本文件已覆盖，真实
//     UI 交互由浏览器 runner 兜底。
//   - 真实 WebSocket 服务端行为（帧序/重连/服务端推送时序）：单测只验证 URL 构造、事件
//     派发与状态迁移；真实网络由浏览器/手动测试兜底。
//   - fork/switch-branch/model/autonomy/prompt 等 HTTP 端点的真实网络往返：fetch 是
//     数据驱动桩，只验证请求形态（URL/method/body）与本地状态变化。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

// 加载真实底层模块 + 被测模块（按 HTML 相对顺序，harness 会排序）：
// canvas-node-types.js → canvas-core.js → canvas-state.js → canvas-agent-tui.js。
// 本模块加载期不触碰外部名（顶层只有函数声明与一个 Map），外部名在 loadModules 之前
// 用全局属性桩好即可；trf 契约镜像必须 loadModules 之后用 evaluate 定义（要引用
// context 内真实 tr）。
function loadAgentTuiModule({
    nodes = [],
    canvasProjectId = '',
    canvasProject = '',
    sourceProjectId = '',
} = {}) {
    const { ctx, harness } = createCanvasContext();
    const calls = [];
    // --- 数据驱动注册表：测试显式喂数据，桩零逻辑（防「第二套假核心」） ---
    harness.layout = { width: 180, height: 110 };    // imageLayout 返回值
    harness.providers = [];                          // chatApiProviders 返回值
    harness.modelsByProvider = new Map();            // providerId -> 聊天模型 ID[]
    harness.bodyHtmlCalls = [];                      // nodeBodyHtml 调用记录
    harness.bodyHtmlOut = '<div class="node-body">__body_stub__</div>';
    harness.viewportPointCalls = [];                 // centerViewportOnWorldPoint 调用记录
    harness.fetchCalls = [];                         // fetch 调用记录 {url, method, body}
    harness.fetchResponses = [];                     // fetch 响应队列（shift；空队列默认 ok:true/{}）
    harness.wsInstances = [];                        // FakeWebSocket 实例
    harness.wsCloseRecords = [];                     // FakeWebSocket.close 调用记录
    harness.consoleErrors = [];                      // console.error 调用记录
    harness.promptReturns = [];                      // ctx.prompt 返回值队列
    harness.promptLabels = [];                       // ctx.prompt 入参记录

    // 可控 WebSocket 假对象（浏览器 API 表面积，零产品逻辑）。
    class FakeWebSocket {
        static get OPEN() { return 1; }
        constructor(url) {
            this.url = url;
            this.readyState = 0;   // 0=未打开（真实浏览器用 CONNECTING；测试按需置 1 模拟 OPEN）
            this.sent = [];
            this.closed = false;
            harness.wsInstances.push(this);
        }
        send(data) { this.sent.push(data); }
        close() { this.closed = true; harness.wsCloseRecords.push(this.url); }
    }
    harness.FakeWebSocket = FakeWebSocket;
    ctx.WebSocket = FakeWebSocket;

    ctx.console = {
        error: (...args) => { harness.consoleErrors.push(args.map(String).join(' ')); },
        log: () => {},
    };

    // --- 宿主自由名桩（全部来自不可独立加载的宿主脚本 smart-canvas.js；预算清单见文件头） ---
    ctx.canvasId = 'cv1';                            // 状态（smart-canvas.js:2）
    ctx.sourceProjectId = sourceProjectId;           // 状态（smart-canvas.js:3）
    ctx.render = () => { calls.push('render'); };    // 零逻辑记录器
    ctx.centerViewportOnWorldPoint = (point) => { harness.viewportPointCalls.push(point); };
    ctx.nodeBodyHtml = (node, layout) => {
        harness.bodyHtmlCalls.push({ id: node?.id || '', layout });
        return harness.bodyHtmlOut;
    };
    ctx.imageLayout = () => harness.layout;          // 数据驱动
    ctx.chatApiProviders = () => harness.providers;  // 数据驱动
    ctx.providerChatModels = (providerId) => harness.modelsByProvider.get(providerId) || [];
    ctx.fetch = (url, opts) => {
        harness.fetchCalls.push({ url, method: opts?.method || '', body: opts && opts.body != null ? opts.body : null });
        const next = harness.fetchResponses.length > 0
            ? harness.fetchResponses.shift()
            : { ok: true, json: async () => ({}) };
        return Promise.resolve(next);
    };
    ctx.prompt = (label) => {
        harness.promptLabels.push(label);
        return harness.promptReturns.length > 0 ? harness.promptReturns.shift() : null;
    };

    loadModules(ctx, [
        'canvas-node-types.js',
        'canvas-core.js',
        'canvas-state.js',
        'canvas-agent-tui.js',
    ]);

    // 单行契约镜像（smart-canvas.js:314-316 逐字单表达式；tr 运行期解析到真实
    // canvas-core.js:64）。必须在 loadModules 之后定义。
    evaluate(ctx, 'trf = (key, values = {}) => Object.entries(values).reduce((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), tr(key))');

    // 运行期状态：canvas 是 canvas-core.js 的词法 let 绑定（不是全局对象属性），必须用
    // evaluate 赋值；canvasState 是 window.canvasState 对象，宿主侧直接改属性即可。
    ctx.canvasState.nodes = nodes;
    ctx.__canvasPayload = { id: 'cv', project_id: canvasProjectId, project: canvasProject, nodes, connections: [] };
    evaluate(ctx, 'canvas = __canvasPayload');
    return { ctx, harness, calls };
}

// ---------- 模块加载 ----------

test('loads canvas-agent-tui.js standalone: all top-level functions exist, real modules in place', () => {
    const { ctx } = loadAgentTuiModule();
    // 顶层 function 都在（模块加载成功且无 SyntaxError 的前置证据）。
    for (const name of [
        'isSmartAgentNode', 'isSmartAgentTaskNode', 'getAgentTuiState',
        'connectAgentWebSocket', 'handleAgentEvent', 'sendAgentPrompt', 'sendAgentCommand',
        'sendAgentSteer', 'sendAgentFollowUp', 'sendAgentAbort', 'setAgentModel', 'setAgentAutonomy',
        'forkAgentSession', 'switchAgentBranch', 'showForkPicker', 'sendAgentConfirm',
        'agentCardStatusHtml', 'agentNodeBodyHtml', 'agentTaskStatusText', 'agentTaskNodeBodyHtml',
        'agentProjectId', 'bindAgentNodeControls', 'renderAgentNode', 'bindAgentTaskNodeControls',
        'cleanupAgentTuiState', 'renderMarkdown', 'getChatModels',
    ]) globalFunction(ctx, name);
    // 真实底层模块就位。
    assert.equal(evaluate(ctx, 'NODE_TYPE_AGENT'), 'smart-agent', '真实 canvas-node-types.js 常量');
    assert.equal(evaluate(ctx, 'NODE_TYPE_TASK'), 'agent-task', '真实 canvas-node-types.js 常量（#184 agent-task 节点类型）');
    assert.equal(evaluate(ctx, 'agentTuiState instanceof Map'), true, 'agentTuiState 是模块内 Map');
    assert.equal(evaluate(ctx, 'agentTuiState.size'), 0, '初始状态容器为空');
    assert.equal(evaluate(ctx, "escapeHtml('<x>')"), '&lt;x&gt;', '真实 canvas-core.js escapeHtml');
    assert.equal(evaluate(ctx, 'tr("smart.stQueued")'), 'smart.stQueued', '无 StudioI18n 时 tr 回退 key');
    assert.equal(evaluate(ctx, "trf('x{a}{b}', {a: 1, b: 'c'})"), 'x1c', 'trf 契约镜像（smart-canvas.js:314）');
    assert.equal(evaluate(ctx, 'nodeScale({scale: 2})'), 2, '真实 canvas-core.js nodeScale');
    assert.equal(evaluate(ctx, 'nodeScale({})'), 1, 'nodeScale 缺省 1');
    assert.equal(evaluate(ctx, 'canvas && canvas.id'), 'cv', 'canvas let 绑定已被 evaluate 赋值');
    assert.equal(evaluate(ctx, 'canvasState.nodes.length'), 0, 'canvasState 来自真实 canvas-state.js');
});

// ---------- 状态容器 ----------

test('getAgentTuiState: 默认形状（idle/无 socket/cautious/空 messages）+ 幂等（同引用）', () => {
    const { ctx } = loadAgentTuiModule();
    const get = globalFunction(ctx, 'getAgentTuiState');

    const s = get('N');
    assert.equal(s.socket, null);
    assert.deepEqual(fromContext(s.messages), [], 'messages 初始空数组');
    assert.equal(s.status, 'idle');
    assert.equal(s.model, '');
    assert.equal(s.autonomy, 'cautious');
    assert.equal(s.thinkingVisible, false);
    assert.equal('thinking' in s, false, 'thinking 缓冲首次 thinking_delta 才建');
    assert.equal(evaluate(ctx, 'agentTuiState.get("N").toolActivities instanceof Map'), true, 'toolActivities 是 Map');
    assert.equal(evaluate(ctx, 'agentTuiState.get("N").toolActivities.size'), 0);

    assert.equal(evaluate(ctx, 'getAgentTuiState("N") === agentTuiState.get("N")'), true, '二次调用返回同一对象');
    assert.equal(evaluate(ctx, 'agentTuiState.size'), 1, '不重复建条目');
});

test('isSmartAgentNode / isSmartAgentTaskNode: 真实 NODE_TYPE_* 谓词', () => {
    const { ctx } = loadAgentTuiModule();
    const isAgent = globalFunction(ctx, 'isSmartAgentNode');
    const isTask = globalFunction(ctx, 'isSmartAgentTaskNode');

    assert.equal(isAgent({ type: 'smart-agent' }), true, 'NODE_TYPE_AGENT = "smart-agent"');
    assert.equal(isAgent({ type: 'agent-task' }), false);
    assert.equal(isAgent({}), false);
    assert.equal(isAgent(null), false);
    assert.equal(isAgent(undefined), false);

    assert.equal(isTask({ type: 'agent-task' }), true, 'NODE_TYPE_TASK = "agent-task"（#184）');
    assert.equal(isTask({ type: 'smart-agent' }), false);
    assert.equal(isTask(null), false);
});
