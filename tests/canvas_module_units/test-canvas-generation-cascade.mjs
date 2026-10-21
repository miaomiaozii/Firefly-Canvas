// #199 轨道 4：canvas-generation-cascade.js 独立单测 —— 级联/循环生成调度的纯计算与状态机切片。
//
// 实测规模：1294 行 / 60 个顶层 function；其中 48 个函数的自身函数体不碰 DOM/fetch/alert。
// 本文件覆盖其中 45 个（窄而实）；4 个高桩成本编排函数与 runSmartCascade 家族判为
// 「单测盖不住」（见下方盖不住清单）。
//
// 加载面实测（harness.mjs HTML_SCRIPT_ORDER 前 13 个模块全部加载成功，hub smart-canvas.js
// 在 vm 里必炸 `Cannot read properties of null (reading 'addEventListener')`，故 hub 名字
// 一律用零逻辑桩）：
//   i18n-core.js / outpaint-ratios.js / canvas-node-types.js / canvas-core.js /
//   canvas-state.js / project-canvas-mutations.js / canvas-layout-utils.js /
//   canvas-connections.js / canvas-media-history.js / canvas-undo.js /
//   canvas-node-ops.js / canvas-image-editor.js / canvas-generation-cascade.js
//
// 由真实模块提供（加载即得，不桩）：canvasState / canvas / settings / tr / toast /
// uid / nowMs / escapeHtml / isSmartImageNode / isHistoryGroupNode / NODE_TYPE_* /
// addConnection / connectInputNode / outgoingConnectionsFor / cleanHistoryImages /
// ensureHistoryGroupForNode / historyGroupForNode / positionHistoryGroupForNode /
// replaceOutputsToNodeWithHistory（canvas-media-history.js:2358，真实实现进本文件断言面）/
// MEDIA_GROUP_DEFAULT_SCALE / scheduleConnectionLayerRefresh 等 46 个自由名。
//
// 桩清单（全部是 hub smart-canvas.js 的名字；ADR 0020 判据「桩要开始重新实现产品
// 逻辑就停」：未命中——22 个桩里 0 个复制产品算法，只有数据驱动注册表、记录器与值常量）：
//   值常量：MEDIA_NODE_DEFAULT_SCALE = 1（断言读 ctx 现值，不写死）。
//   值回显记录器（返回值 = 被代入值，断言「传了什么」）：
//     trf → 'T(key:values)'；mediaKindForUrls → 'K:kind'（产品传入的 kind 分支可观察）。
//   透传记录器：liveSmartNode / stripImageGenerationMeta / stripRunInputMeta → 原样返回；
//     markSmartNodeComplete / attachRunMeta / render → 只记调用。
//   数据驱动注册表（测试按用例喂数据，桩本身零判定）：
//     nodeRect → 按 node.id 查注册表（未注册回退全 0 矩形，纯常量）；
//     pendingBoxSize → 返回 D.pendingBox 常量；
//     cloneSmartNode → {...node} 浅拷贝（记录调用，不复制任何布局算法）；
//     outputImagesForNode / promptInputNodesFor / upstreamNodesForKinds /
//     workflowInputNodesFor / inputNodesFor(canvasCore 惰性 Proxy 解析到同一裸名) → 按 id 查表；
//     smartImageUsesWorkflowInput → node.__workflowInput 标志；
//     isVideoMediaItem → img.__isVideo 标志；
//     smartLoopCount → loop.__count 数据字段；
//     isApiLikeEngine → D.apiLike 集合成员判定（集合由测试喂）。
//   注意 canvasCore 是 canvas-core.js:202 的惰性 Proxy：get 走 `new Function("return name")`
//   按裸名解析全局，set 写回全局——所以 hub 桩挂在 ctx 上即被 canvasCore.X 命中，
//   不需要（也不能）替换 ctx.window（harness.mjs:180，canvas-state.js:3 依赖它）。
//
// 跨 realm 约定（vm 独立 V8 realm）：
//   - 被测函数返回的 vm 创建对象（数组/graph/children Map）先 fromContext(...)（JSON
//     往返）或 evaluate(ctx, '...') 回查；
//   - 宿主创建的对象（canvasState.nodes 里 push 的节点、ctx.roundOutputs Map、
//     runState）直接在宿主侧断言引用/内容；
//   - 可能为 undefined 的结果用裸 assert（fromContext 对 undefined 抛
//     `SyntaxError: "undefined" is not valid JSON`）；
//   - vm Map/Set（children / loopAppendInitialized / smartNodeRunTokens）一律
//     evaluate(ctx, 'Array.from(...)') 回查，不把 Map 往返出来比。
//
// 明确判为「单测盖不住」的行为（不写假测试，由浏览器 runner 兜底）：
//   - generateUrlsForCurrentSettings（引擎路由 runninghub/api/modelscope +
//     runApiGeneration/pollGenerationTaskResultForNode 等 14 个 hub 名，全网络面）
//     与 finalizePendingNode（copyMediaSizeFields/mediaNodeDefaultScale/
//     clearSourceBusyStateIfDownstreamDone/smartGroupContainingNode 等 21 个 hub 名）：
//     由门步骤 generation-lifecycle-evidence（tests/generation_lifecycle_browser.py）与
//     complete-generation-lifecycle-evidence（tests/generation_task_native_browser.py）兜。
//   - runCascadeStepIntoNode（30 个 hub 名：buildPromptRequestForNode/smartRunSnapshot/
//     rememberRecentSmartSettings/handleJimengPendingSignal/…）与
//     runLoopRoundIntoSlot（189 行/34 个 hub 名：runApiGeneration/
//     acceptProjectGenerationRegistration/resumeSmartPendingNode/…）：同上两个
//     generation 证据步骤 + node-flow-cascade-evidence（tests/node_flow_browser.py）兜。
//   - runSmartCascade / runSmartCascadeFromLoop / runSmartCascadeRoundsWithLimit /
//     requestSmartCascadeStop：顶层绑定 composer/runBtn DOM（document.getElementById，
//     真实渲染面）+ promptInput.innerHTML 读写 + smartCascadeRuns/savePromptDraftForCurrent/
//     pushUndo/syncRunButtonState 等编排名，属端到端运行链，由
//     node-flow-cascade-evidence（tests/node_flow_browser.py）兜。
//
// 有效性证据：5 处互相独立的变异（worktree 内改产品行为，红绿原文见 #199 评论）。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

const MODULES = [
    'i18n-core.js',
    'outpaint-ratios.js',
    'canvas-node-types.js',
    'canvas-core.js',
    'canvas-state.js',
    'project-canvas-mutations.js',
    'canvas-layout-utils.js',
    'canvas-connections.js',
    'canvas-media-history.js',
    'canvas-undo.js',
    'canvas-node-ops.js',
    'canvas-image-editor.js',
    'canvas-generation-cascade.js',
];

// 加载全部 13 个真实模块，再挂 22 个 hub 零逻辑桩（见文件头桩清单）。
// D = 数据驱动注册表（测试喂数据）；calls = 调用记录（断言「调了谁、传了什么」）。
function loadCascadeModule() {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, MODULES);
    const D = {
        rects: new Map(),
        pendingBox: { w: 260, h: 180 },
        outputImages: new Map(),
        promptInputs: new Map(),
        upstreams: new Map(),
        workflowInputs: new Map(),
        directInputs: new Map(),
        apiLike: new Set(),
        promptHtml: '',
    };
    const calls = {
        trf: [], nodeRect: [], pendingBoxSize: [], cloneSmartNode: [], liveSmartNode: [],
        markSmartNodeComplete: [], attachRunMeta: [], stripRunInputMeta: [],
        stripImageGenerationMeta: [], mediaKindForUrls: [], outputImagesForNode: [],
        promptInputNodesFor: [], upstreamNodesForKinds: [], workflowInputNodesFor: [],
        inputNodesFor: [], promptHtmlWithMentionTokens: [], render: [],
    };
    ctx.trf = (key, values = {}) => {
        calls.trf.push([key, values]);
        return 'T(' + key + (Object.keys(values).length ? ':' + JSON.stringify(Object.values(values)) : '') + ')';
    };
    ctx.nodeRect = (node) => {
        calls.nodeRect.push(node?.id);
        return D.rects.get(node?.id) || { x: 0, y: 0, width: 0, height: 0 };
    };
    ctx.pendingBoxSize = (count, opts) => {
        calls.pendingBoxSize.push([count, opts]);
        return { ...D.pendingBox };
    };
    ctx.cloneSmartNode = (node) => {
        calls.cloneSmartNode.push(node?.id);
        return { ...node };
    };
    ctx.liveSmartNode = (node) => {
        calls.liveSmartNode.push(node?.id);
        return node;
    };
    ctx.markSmartNodeComplete = (node, meta, complete) => {
        calls.markSmartNodeComplete.push([node?.id, meta, complete]);
        return node;
    };
    ctx.attachRunMeta = (node, meta) => {
        calls.attachRunMeta.push([node?.id, meta]);
        return meta;
    };
    ctx.stripRunInputMeta = (meta) => {
        calls.stripRunInputMeta.push(meta);
        return meta;
    };
    ctx.stripImageGenerationMeta = (img) => {
        calls.stripImageGenerationMeta.push(img?.url);
        return img;
    };
    ctx.mediaKindForUrls = (urls, kind) => {
        calls.mediaKindForUrls.push([urls, kind]);
        return 'K:' + String(kind);
    };
    ctx.isVideoMediaItem = (img) => Boolean(img && img.__isVideo);
    ctx.MEDIA_NODE_DEFAULT_SCALE = 1;
    ctx.outputImagesForNode = (node, consume, ctxArg) => {
        calls.outputImagesForNode.push([node?.id, consume, ctxArg]);
        return D.outputImages.get(node?.id) || [];
    };
    ctx.promptInputNodesFor = (node) => {
        calls.promptInputNodesFor.push(node?.id);
        return D.promptInputs.get(node?.id) || [];
    };
    ctx.upstreamNodesForKinds = (node, kinds) => {
        calls.upstreamNodesForKinds.push([node?.id, kinds]);
        return D.upstreams.get(node?.id) || [];
    };
    ctx.smartImageUsesWorkflowInput = (node) => Boolean(node && node.__workflowInput);
    ctx.workflowInputNodesFor = (node) => {
        calls.workflowInputNodesFor.push(node?.id);
        return D.workflowInputs.get(node?.id) || [];
    };
    ctx.inputNodesFor = (node) => {
        calls.inputNodesFor.push(node?.id);
        return D.directInputs.get(node?.id) || [];
    };
    ctx.smartLoopCount = (loop) => Number(loop && loop.__count) || 0;
    ctx.isApiLikeEngine = (engine) => D.apiLike.has(String(engine));
    ctx.promptHtmlWithMentionTokens = (text, refs) => {
        calls.promptHtmlWithMentionTokens.push([text, refs]);
        return D.promptHtml;
    };
    ctx.render = () => { calls.render.push('render'); };
    return { ctx, harness, D, calls, fn: (name) => globalFunction(ctx, name) };
}

// 常用夹具
const img = (n, extra = {}) => ({ url: `http://img.example/${n}`, kind: 'image', name: n, ...extra });
// 被测函数返回的数组是 vm realm 的 Array（prototype 不同，deepStrictEqual 会误报），
// 一律 JSON 往返回宿主。
function nodeIds(list) { return fromContext((list || []).map(n => n.id)); }
// evaluate 返回原始值（非字符串），同样 JSON 往返回宿主。
function evalJson(ctx, expression) { return fromContext(evaluate(ctx, expression)); }
// 给 canvas（canvas-core 的 let）赋连接表，返回 evaluate 的往返值（断言用 JSON 回查）。
function setCanvas(ctx, connections) {
    evaluate(ctx, `canvas = ${JSON.stringify({ connections })}`);
    return connections;
}
function connectionsOf(ctx) {
    return fromContext(evaluate(ctx, 'canvas && canvas.connections || []'));
}
function trOf(ctx, key) { return fromContext(evaluate(ctx, `tr(${JSON.stringify(key)})`)); }

// ── 纯函数：标题 / 过滤 / 停止请求 / 并行上限 / runPath ──────────────────

test('cascadeOutputTitle: 单数/复数/缺省 kind 矩阵', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('cascadeOutputTitle');
    assert.equal(f('image', 1), 'Image');
    assert.equal(f('video', 1), 'Video');
    assert.equal(f('audio', 1), 'Audio');
    assert.equal(f('text', 1), 'Text');
    assert.equal(f('file', 1), 'File');
    assert.equal(f('other-kind', 1), 'Image');
    assert.equal(f('image', 2), 'Group');
    assert.equal(f('video', 2), 'Videos');
    assert.equal(f('audio', 2), 'Audios');
    assert.equal(f('text', 2), 'Texts');
    assert.equal(f('file', 2), 'Group');
    assert.equal(f('image', 0), 'Image');
    assert.equal(f('image', NaN), 'Image');
});

test('nonPreviewOutputImages: 只留「有 url 且非预览」的图', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('nonPreviewOutputImages');
    const keep = img('a');
    const preview = { ...img('b'), loopInputPreview: true };
    const out = f([keep, preview, { kind: 'image' }, null, undefined]);
    assert.deepEqual(fromContext(out), [keep]);
    assert.deepEqual(fromContext(f()), []);
    assert.deepEqual(fromContext(f(null)), []);
});

test('smartCascadeParallelLimit: 恒 6（与 chain 无关）', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('smartCascadeParallelLimit');
    assert.equal(f([]), 6);
    assert.equal(f([{ id: 'a' }, { id: 'b' }, { id: 'c' }]), 6);
    assert.equal(f(null), 6);
});

test('smartCascadePathForCtx: runState.runPath 优先，其次 runPath，否则 null', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('smartCascadePathForCtx');
    const P1 = { states: {} };
    const P2 = { states: {} };
    const r1 = f({ runState: { runPath: P1 }, runPath: P2 });
    assert.equal(r1, P1, 'runState.runPath 必须优先');
    assert.equal(f({ runPath: P2 }), P2);
    assert.equal(f({ runState: {} }), null);
    assert.equal(f({}), null);
    assert.equal(f(null), null);
    assert.equal(f(), null);
});

test('smartCascadeStopText: stopping 选不同 tr key', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('smartCascadeStopText');
    assert.equal(f(), trOf(ctx, 'smart.stopRun'));
    assert.equal(f(false), trOf(ctx, 'smart.stopRun'));
    assert.equal(f(true), trOf(ctx, 'smart.stopping'));
    assert.notEqual(f(true), f(false));
});

test('smartCascadeAbortError: Error + smartCascadeStopped 标志', t => {
    const { ctx, fn } = loadCascadeModule();
    const err = fn('smartCascadeAbortError')();
    assert.equal(err.message, trOf(ctx, 'smart.oneClickStopped'));
    assert.equal(err.smartCascadeStopped, true);
    assert.equal(fromContext(evaluate(ctx, 'smartCascadeAbortError() instanceof Error')), true);
});

test('throwIfSmartCascadeStopRequested: stopRequested 才抛（且抛的是 smartCascadeStopped）', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('throwIfSmartCascadeStopRequested');
    assert.throws(() => f({ stopRequested: true }), e => e.smartCascadeStopped === true);
    assert.doesNotThrow(() => f(null));
    assert.doesNotThrow(() => f());
    assert.doesNotThrow(() => f({}));
    assert.doesNotThrow(() => f({ stopRequested: false }));
});

test('isGeneratedOutputForNode: target.sourceNodeId === source.id', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('isGeneratedOutputForNode');
    assert.equal(f({ id: 's' }, { sourceNodeId: 's' }), true);
    assert.equal(f({ id: 's' }, { sourceNodeId: 'x' }), false);
    assert.equal(f({ id: 's' }, {}), false);
    assert.equal(f(null, { sourceNodeId: 's' }), false);
    assert.equal(f({ id: 's' }, undefined), false);
});

test('tagLoopOutputSlot: 写全 5 个循环标记，NaN/负数归零，falsy 原样返回', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('tagLoopOutputSlot');
    const out = { id: 'o' };
    const r = f(out, { id: 'r' }, { id: 'l' }, 2, 3);
    assert.equal(r, out, '必须返回同一对象');
    assert.equal(out.sourceNodeId, 'r');
    assert.equal(out.loopSourceId, 'l');
    assert.equal(out.loopRootId, 'r');
    assert.equal(out.loopRoundIndex, 2);
    assert.equal(out.loopSlotIndex, 3);
    // NaN / 负数 / null 归零
    assert.equal(f({}, { id: 'r' }, null, NaN, -2).loopRoundIndex, 0);
    assert.equal(f({}, { id: 'r' }, null, 1, null).loopSlotIndex, 0);
    // root/loop 缺失时保留已有值
    const keep = { sourceNodeId: 'old', loopSourceId: 'oldL' };
    f(keep, null, null, 1, 1);
    assert.equal(keep.sourceNodeId, 'old');
    assert.equal(keep.loopSourceId, 'oldL');
    // falsy output 原样返回
    assert.equal(f(undefined, { id: 'r' }, null, 1, 1), undefined);
    assert.equal(f(null, { id: 'r' }, null, 1, 1), null);
});

test('rememberRoundOutputs: 过滤 url + 浅拷贝进 roundOutputs，返回原数组', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('rememberRoundOutputs');
    // 宿主 Map：直接读
    const ctxA = { roundOutputs: new Map() };
    const outputs = [img('a'), { kind: 'image' }];
    const ret = f(ctxA, { id: 'n' }, outputs);
    assert.equal(ret, outputs, '必须返回原数组（不是拷贝）');
    assert.deepEqual(fromContext(Array.from(ctxA.roundOutputs.entries())), [['n', [img('a')]]]);
    // 无 roundOutputs：函数自建 vm Map，用 evaluate 回查
    const ctxB = {};
    ctx._remCtx = ctxB;
    f(ctxB, { id: 'n2' }, [img('x')]);
    assert.equal(fromContext(evaluate(ctx, '_remCtx.roundOutputs.size')), 1);
    assert.deepEqual(evalJson(ctx, 'Array.from(_remCtx.roundOutputs.entries())'),
        [['n2', [img('x')]]]);
    // 守卫：node 无 id / outputs 非数组
    const noId = [img('z')];
    assert.equal(f({}, null, noId), noId);
    assert.equal(f({}, { id: 'n' }, 'not-array'), 'not-array');
    assert.deepEqual(fromContext(f({}, { id: 'n' }, null)), [], 'outputs 为 null → 返回新建空数组');
});

test('clearNodeRunningState: 清 token 且置 running=false', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('clearNodeRunningState');
    evaluate(ctx, 'smartNodeRunTokens.set("n", 99)');
    const node = { id: 'n', running: true };
    f(node);
    assert.equal(node.running, false);
    assert.equal(fromContext(evaluate(ctx, 'smartNodeRunTokens.has("n")')), false);
    assert.doesNotThrow(() => f(null));
});

// ── 下游图遍历（真实 canvas 连接表驱动）─────────────────────────────────

test('downstreamNodesForId: 前序 DFS + 环守卫 + kind 过滤', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('downstreamNodesForId');
    const A = { id: 'A', x: 1 }; const C = { id: 'C', x: 2 };
    const B = { id: 'B', x: 3 };
    ctx.canvasState.nodes.push({ id: 'R', x: 0 }, A, C, B, { id: 'G', x: 9 });
    setCanvas(ctx, [
        { from: 'R', to: 'A', kind: 'input' },
        { from: 'R', to: 'B', kind: 'flow' },
        { from: 'A', to: 'C' },
        { from: 'C', to: 'R', kind: 'flow' },   // 环：守卫必须拦住
        { from: 'R', to: 'G', kind: 'other' },  // 非 input/flow：忽略
        { from: 'R', to: 'ghost', kind: 'flow' }, // 目标不存在：过滤
    ]);
    assert.deepEqual(nodeIds(f('R')), ['A', 'C', 'B']);
    assert.deepEqual(nodeIds(f('A')), ['C', 'R', 'B'], '环守卫防死递归，但 R 子树仍在结果里');
    assert.deepEqual(nodeIds(f('B')), []);
    assert.deepEqual(nodeIds(f('missing-id')), []);
});

test('downstreamImageTargetsFor: 只留直接下游图片（非 history 组），按 x/y 排序', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('downstreamImageTargetsFor');
    const I1 = { id: 'I1', type: 'smart-image', x: 100, y: 10 };
    const I2 = { id: 'I2', type: 'smart-image', x: 100, y: 5 };
    const I3 = { id: 'I3', type: 'smart-image', x: 0, isHistoryGroup: true, historyFor: 'R' };
    const P = { id: 'P', type: 'prompt', x: 0 };
    ctx.canvasState.nodes.push({ id: 'R', x: 0 }, I1, I2, I3, P);
    setCanvas(ctx, [
        { from: 'R', to: 'I1' }, { from: 'R', to: 'I2' },
        { from: 'R', to: 'I3', kind: 'input' }, { from: 'R', to: 'P', kind: 'input' },
    ]);
    assert.deepEqual(nodeIds(f({ id: 'R' })), ['I2', 'I1']);
    assert.deepEqual(nodeIds(f({ id: 'I1' })), []);
    assert.deepEqual(nodeIds(f({})), []);
    assert.deepEqual(nodeIds(f(null)), []);
});

test('downstreamCascadeTargetsFor: 图片与 loop 都算，prompt/history 不算', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('downstreamCascadeTargetsFor');
    const T = fromContext(evaluate(ctx, 'NODE_TYPE_LOOP'));
    const I = { id: 'I', type: 'smart-image', x: 100, y: 0 };
    const L = { id: 'L', type: T, x: 50, y: 0 };
    const P = { id: 'P', type: 'prompt', x: 0 };
    const H = { id: 'H', type: 'smart-image', x: 0, isHistoryGroup: true };
    ctx.canvasState.nodes.push({ id: 'R', x: 0 }, I, L, P, H);
    setCanvas(ctx, [
        { from: 'R', to: 'I' }, { from: 'R', to: 'L', kind: 'input' },
        { from: 'R', to: 'P', kind: 'input' }, { from: 'R', to: 'H', kind: 'input' },
    ]);
    assert.deepEqual(nodeIds(f({ id: 'R' })), ['L', 'I']);
    assert.deepEqual(nodeIds(f({})), []);
});

test('downstreamWorkflowImageTargetsFor / hasDownstreamWorkflowImageNode: 排除自己生成的输出', t => {
    const { ctx, fn } = loadCascadeModule();
    const targets = fn('downstreamWorkflowImageTargetsFor');
    const has = fn('hasDownstreamWorkflowImageNode');
    const G1 = { id: 'G1', type: 'smart-image', x: 1, sourceNodeId: 'R' };  // R 生成的 → 排除
    const G2 = { id: 'G2', type: 'smart-image', x: 2, sourceNodeId: 'other' }; // 工作流目标
    const G3 = { id: 'G3', type: 'smart-image', x: 3, sourceNodeId: 'R2' };
    ctx.canvasState.nodes.push(
        { id: 'R', x: 0, type: 'smart-image' }, { id: 'R2', x: 0, type: 'smart-image' }, G1, G2, G3,
    );
    setCanvas(ctx, [
        { from: 'R', to: 'G1' }, { from: 'R', to: 'G2' },
        { from: 'R2', to: 'G3', kind: 'input' },
    ]);
    assert.deepEqual(nodeIds(targets({ id: 'R' })), ['G2']);
    assert.equal(has({ id: 'R' }), true);
    assert.equal(has({ id: 'R2' }), false, 'R2 只有自己生成的输出 → 无工作流目标');
    assert.equal(has(null), false);
});

test('directLoopRunTargets: 排除「还有下游工作流图」的直接目标', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('directLoopRunTargets');
    const T1 = { id: 'T1', type: 'smart-image', x: 1 };
    const T1b = { id: 'T1b', type: 'smart-image', x: 2, sourceNodeId: 'other' };
    const T2 = { id: 'T2', type: 'smart-image', x: 3 };
    const L = { id: 'L', type: 'smart-loop' };
    ctx.canvasState.nodes.push(L, T1, T1b, T2);
    setCanvas(ctx, [
        { from: 'L', to: 'T1', kind: 'flow' },
        { from: 'L', to: 'T2', kind: 'flow' },
        { from: 'T1', to: 'T1b', kind: 'flow' },
    ]);
    assert.deepEqual(nodeIds(f(L)), ['T2']);
    assert.deepEqual(nodeIds(f({})), []);
    assert.deepEqual(nodeIds(f(null)), []);
});

test('upstreamNodesForId: 后序遍历 + 环守卫（上游走注册表）', t => {
    const { ctx, fn, D, calls } = loadCascadeModule();
    const f = fn('upstreamNodesForId');
    ctx.canvasState.nodes.push({ id: 'D', x: 3 }, { id: 'B', x: 2 }, { id: 'A', x: 1 });
    D.upstreams.set('D', [{ id: 'B' }]);
    D.upstreams.set('B', [{ id: 'A' }]);
    D.upstreams.set('A', [{ id: 'D' }]); // 环：A 的上游是 D 自己
    assert.deepEqual(nodeIds(f('D')), ['A', 'B']);
    assert.deepEqual(nodeIds(f('B')), ['D', 'A']);
    assert.deepEqual(nodeIds(f('missing')), []);
    // kinds 透传（默认 ['input']）；找不到节点时 node 参数是 undefined（JSON 后为 null）
    assert.deepEqual(fromContext(calls.upstreamNodesForKinds.at(-1)), [null, ['input']]);
});

test('isDirectLoopTargetRun: root 对齐 + 无下游工作流 + 在 loop 直接目标里', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('isDirectLoopTargetRun');
    const L = { id: 'L', type: 'smart-loop' };
    const T = { id: 'T', type: 'smart-image', x: 1 };
    const X = { id: 'X', type: 'smart-image', x: 2 };
    const T1b = { id: 'T1b', type: 'smart-image', x: 3, sourceNodeId: 'other' };
    ctx.canvasState.nodes.push(L, T, X, T1b);
    const loop = { node: L, count: 2, mode: 'serial' };
    setCanvas(ctx, [
        { from: 'L', to: 'T', kind: 'flow' },
        { from: 'L', to: 'X', kind: 'flow' },
        { from: 'T', to: 'T1b', kind: 'flow' },
    ]);
    const graphOk = { root: T };
    assert.equal(f(loop, T, graphOk), false, 'T 有下游工作流图 → false');
    setCanvas(ctx, [
        { from: 'L', to: 'T', kind: 'flow' },
        { from: 'L', to: 'X', kind: 'flow' },
    ]);
    assert.equal(f(loop, T, { root: T }), true);
    assert.equal(f(loop, X, { root: X }), true);
    assert.equal(f(loop, T, { root: X }), false, 'graph.root 不是 tail → false');
    setCanvas(ctx, [{ from: 'L', to: 'X', kind: 'flow' }]);
    assert.equal(f(loop, T, { root: T }), false, 'tail 不在 loop 直接目标里 → false');
    assert.equal(f(null, T, { root: T }), false);
    assert.equal(f(loop, null, { root: T }), false);
    assert.equal(f(loop, T, null), false);
});

test('loopOutputSlotsForRoot: 下游图片按 x/y 排序，排除 history 组与非图片', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('loopOutputSlotsForRoot');
    const A = { id: 'A', type: 'smart-image', x: 100, y: 0 };
    const B = { id: 'B', type: 'smart-image', x: 50, y: 0 };
    const C = { id: 'C', type: 'smart-image', x: 0, y: 0 };
    const H = { id: 'H', type: 'smart-image', x: 0, isHistoryGroup: true, historyFor: 'R' };
    const P = { id: 'P', type: 'prompt', x: 0 };
    ctx.canvasState.nodes.push({ id: 'R', x: -10 }, A, B, C, H, P);
    setCanvas(ctx, [
        { from: 'R', to: 'A', kind: 'flow' }, { from: 'R', to: 'B', kind: 'flow' },
        { from: 'R', to: 'H', kind: 'flow' }, { from: 'R', to: 'P', kind: 'input' },
        { from: 'A', to: 'C', kind: 'flow' },
    ]);
    assert.deepEqual(nodeIds(f({ id: 'R' })), ['C', 'B', 'A']);
    assert.deepEqual(fromContext(f(null)), []);
    assert.deepEqual(fromContext(f({})), []);
});

test('loopOutputSlotForRound: round 优先，其次 slot，再次未标记位序，最后 null', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('loopOutputSlotForRound');
    const N1 = { id: 'N1', type: 'smart-image', sourceNodeId: 'R', loopRoundIndex: 2, loopSlotIndex: 7 };
    const N2 = { id: 'N2', type: 'smart-image', sourceNodeId: 'R', loopSlotIndex: 3 };
    const N3 = { id: 'N3', type: 'smart-image', sourceNodeId: 'R' };
    const N3a = { id: 'N3a', type: 'smart-image', sourceNodeId: 'R' };
    const N4 = { id: 'N4', type: 'smart-image', sourceNodeId: 'other' };
    const N5 = { id: 'N5', type: 'smart-image', sourceNodeId: 'R', loopSourceId: 'otherLoop', loopRoundIndex: 2 };
    ctx.canvasState.nodes.push({ id: 'R', x: 0 }, { id: 'L', type: 'smart-loop' }, N1, N2, N3, N3a, N4, N5);
    setCanvas(ctx, [
        { from: 'R', to: 'N1', kind: 'flow' }, { from: 'R', to: 'N2', kind: 'flow' },
        { from: 'R', to: 'N3', kind: 'flow' }, { from: 'R', to: 'N3a', kind: 'flow' },
        { from: 'R', to: 'N4', kind: 'flow' }, { from: 'R', to: 'N5', kind: 'flow' },
    ]);
    const R = { id: 'R' };
    assert.equal(f(R, null, 2, 0).id, 'N1', 'loopRoundIndex 命中优先');
    assert.equal(f(R, null, 9, 3).id, 'N2', '其次 loopSlotIndex 命中');
    assert.equal(f(R, null, 0, 0).id, 'N3', 'round/slot 都未命中 → untagged[0]（untagged=[N3,N3a]）');
    assert.equal(f(R, null, 9, 5), null, '越界 → null');
    assert.equal(f({ id: 'R' }, { id: 'L' }, 2, 0).id, 'N1', 'loopNode 过滤掉别的 loop 的槽');
    assert.equal(f(null, null, 2, 0), null);
    assert.equal(f({}, null, 2, 0), null);
});

// ── 上游解析（注册表驱动）──────────────────────────────────────────────

test('directImageInputsFor: workflow 分支走 workflowInputNodesFor，否则 inputNodesFor', t => {
    const { ctx, fn, D, calls } = loadCascadeModule();
    const f = fn('directImageInputsFor');
    const N1 = { id: 'N1', type: 'smart-image', x: 200, y: 10, images: [img('a')] };
    const N2 = { id: 'N2', type: 'smart-image', x: 200, y: 5, images: [img('b')] };
    const N3 = { id: 'N3', type: 'smart-image', x: 100, images: [] };          // 无 url → 排除
    const N4 = { id: 'N4', type: 'smart-image', x: 90, isHistoryGroup: true, images: [img('d')] };
    const W1 = { id: 'W1', type: 'smart-image', x: 300, images: [img('w')] };
    const P = { id: 'P', type: 'prompt', x: 400 };
    ctx.canvasState.nodes.push(N1, N2, N3, N4, W1, P);
    // 常规分支：inputNodesFor 注册表；x 同值按 y 升序
    D.directInputs.set('T', [N1, N2, N3, N4, P]);
    assert.deepEqual(nodeIds(f({ id: 'T' })), ['N2', 'N1']);
    assert.deepEqual(fromContext(calls.inputNodesFor), ['T']);
    // workflow 分支：__workflowInput 标志
    D.workflowInputs.set('W', [W1, P]);
    assert.deepEqual(nodeIds(f({ id: 'W', __workflowInput: true })), ['W1']);
    assert.deepEqual(fromContext(calls.workflowInputNodesFor), ['W']);
    assert.deepEqual(fromContext(f({ id: 'empty' })), [], '无上游 → []');
});

test('directImageInputsForKinds: kinds 原样透传给 upstreamNodesForKinds', t => {
    const { ctx, fn, D, calls } = loadCascadeModule();
    const f = fn('directImageInputsForKinds');
    const K1 = { id: 'K1', type: 'smart-image', x: 50, images: [img('k')] };
    const K2 = { id: 'K2', type: 'smart-image', x: 50, y: 3, images: [img('l')] };
    D.upstreams.set('T', [K1, K2]);
    assert.deepEqual(nodeIds(f({ id: 'T' }, ['input', 'flow'])), ['K1', 'K2'], 'x 同值按 y 升序');
    assert.deepEqual(fromContext(calls.upstreamNodesForKinds.at(-1)), ['T', ['input', 'flow']]);
    assert.deepEqual(nodeIds(f({ id: 'T' })), ['K1', 'K2'], '默认 kinds=[input]');
    assert.deepEqual(fromContext(calls.upstreamNodesForKinds.at(-1)), ['T', ['input']]);
});

test('primaryImageInputFor: 直接图片优先；否则上游 loop.imageInput 的图片上游；否则 null', t => {
    const { ctx, fn, D, calls } = loadCascadeModule();
    const f = fn('primaryImageInputFor');
    const I1 = { id: 'I1', type: 'smart-image', x: 100, images: [img('a')] };
    const I0 = { id: 'I0', type: 'smart-image', x: 0, images: [img('b')] };
    const L = { id: 'L', type: 'smart-loop', imageInput: true };
    const L2 = { id: 'L2', type: 'smart-loop' }; // imageInput 未开
    const P = { id: 'P', type: 'prompt' };
    ctx.canvasState.nodes.push(I1, I0, L, L2, P, { id: 'T', x: 200 });
    // 直接图片输入命中
    D.directInputs.set('T', [I1]);
    assert.equal(f({ id: 'T' }).id, 'I1');
    // 无直接图片 → 上游 loop（imageInput）的图片上游（非 workflow 分支走 canvasCore.inputNodesFor）
    D.directInputs.set('T2', [P, L]);
    D.upstreams.set('L', [I0]);
    assert.equal(f({ id: 'T2' }).id, 'I0');
    assert.deepEqual(fromContext(calls.upstreamNodesForKinds.at(-1)), ['L', ['input']], 'loop 图片上游按 [input] 找');
    // imageInput 未开的 loop 不触发查找
    D.upstreams.set('T3', [L2]);
    D.directInputs.set('T3', []);
    assert.equal(f({ id: 'T3' }), null);
    // 什么都没有
    D.upstreams.set('T4', [P]);
    assert.equal(f({ id: 'T4' }), null);
    // includeFlow：直接图片也走 kinds 注册表
    D.upstreams.set('T5', [I1]);
    assert.equal(f({ id: 'T5' }, { includeFlow: true }).id, 'I1');
    assert.deepEqual(fromContext(calls.upstreamNodesForKinds.at(-1)), ['T5', ['input', 'flow']]);
});

test('smartImageChainTo: 沿 primaryImageInputFor 回溯成链，环守卫，非图片 tail 空链', t => {
    const { ctx, fn, D } = loadCascadeModule();
    const f = fn('smartImageChainTo');
    const I0 = { id: 'I0', type: 'smart-image', x: 0, images: [img('a')] };
    const I1 = { id: 'I1', type: 'smart-image', x: 100, images: [img('b')] };
    const T = { id: 'T', type: 'smart-image', x: 200, images: [img('c')] };
    const X = { id: 'X', type: 'smart-image', x: 150, images: [img('x')] };
    const P = { id: 'P', type: 'prompt', x: 300 };
    ctx.canvasState.nodes.push(I0, I1, T, X, P);
    // 常规链 I0 ← I1 ← T（input 直连注册表）
    D.directInputs.set('T', [I1]);
    D.directInputs.set('I1', [I0]);
    assert.deepEqual(nodeIds(f('T')), ['I0', 'I1', 'T']);
    assert.deepEqual(nodeIds(f('I1')), ['I0', 'I1']);
    // includeFlow：flow 上游 X 因 x 更大（降序）成为 T 的直接输入
    D.upstreams.set('T', [X, I1]);
    assert.deepEqual(nodeIds(f('T', { includeFlow: true })), ['X', 'T']);
    // 环守卫：T ↔ I1 互相为上游
    D.directInputs.set('T', [I1]);
    D.directInputs.set('I1', [T]);
    assert.deepEqual(nodeIds(f('T')), ['I1', 'T']);
    // 非图片 / 不存在 tail
    D.directInputs.set('P', []);
    assert.deepEqual(fromContext(f('P')), []);
    assert.deepEqual(fromContext(f('no-such-id')), []);
});

test('upstreamLoopPromptNodesFor: 只留 loop 且 showPrompt', t => {
    const { ctx, fn, D, calls } = loadCascadeModule();
    const f = fn('upstreamLoopPromptNodesFor');
    const L1 = { id: 'L1', type: 'smart-loop', showPrompt: true };
    const L2 = { id: 'L2', type: 'smart-loop', showPrompt: false };
    const P = { id: 'P', type: 'prompt', showPrompt: true };
    D.promptInputs.set('T', [L1, L2, P]);
    assert.deepEqual(nodeIds(f({ id: 'T' })), ['L1']);
    assert.deepEqual(nodeIds(f({ id: 'empty' })), []);
    assert.deepEqual(fromContext(calls.promptInputNodesFor), ['T', 'empty']);
});

test('relayLoopPromptNodesForTarget: canvasCore.inputNodesFor 里留 loop+showPrompt', t => {
    const { ctx, fn, D } = loadCascadeModule();
    const f = fn('relayLoopPromptNodesForTarget');
    const L1 = { id: 'L1', type: 'smart-loop', showPrompt: true };
    const L2 = { id: 'L2', type: 'smart-loop' };
    const I = { id: 'I', type: 'smart-image' };
    D.directInputs.set('T', [L1, L2, I]);
    assert.deepEqual(nodeIds(f({ id: 'T' })), ['L1']);
    assert.deepEqual(nodeIds(f(null)), []);
    assert.deepEqual(nodeIds(f({})), []);
});

test('relayLoopPromptNodesForEdge: 排除 target 直连 loop，留 source 上游里的其余 relay loop', t => {
    const { ctx, fn, D } = loadCascadeModule();
    const f = fn('relayLoopPromptNodesForEdge');
    const L1 = { id: 'L1', type: 'smart-loop', showPrompt: true }; // target 直连
    const L2 = { id: 'L2', type: 'smart-loop', showPrompt: true }; // 应保留
    const L3 = { id: 'L3', type: 'smart-loop' };                    // 无 showPrompt
    const P = { id: 'P', type: 'prompt' };
    D.promptInputs.set('TARGET', [L1]);
    D.directInputs.set('SRC', [L1, L2, L3, P]);
    assert.deepEqual(nodeIds(f({ id: 'SRC' }, { id: 'TARGET' })), ['L2']);
    assert.deepEqual(nodeIds(f(null, { id: 'TARGET' })), []);
    assert.deepEqual(nodeIds(f({ id: 'SRC' }, null)), []);
});

// ── 恢复 / 视觉状态 ─────────────────────────────────────────────────────

test('restoreFromExtraction: 图片/尺寸还原，extracted 从节点表与连线移除', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('restoreFromExtraction');
    const node = { id: 'N', images: [], inputNodeIds: ['ext', 'keep'] };
    const extracted = { id: 'ext', images: [img('e')], w: 100, h: 50 };
    ctx.canvasState.nodes.push(node, extracted, { id: 'other' });
    setCanvas(ctx, [
        { from: 'ext', to: 'N' },
        { from: 'N', to: 'ext' },
        { from: 'other', to: 'N' },
        { from: 'ext', to: 'other' },
    ]);
    f(node, extracted);
    assert.deepEqual(fromContext(node.images), [img('e')]);
    assert.notEqual(node.images, extracted.images, 'images 必须是 slice 拷贝');
    assert.equal(node.w, 100);
    assert.equal(node.h, 50);
    assert.deepEqual(nodeIds(ctx.canvasState.nodes), ['N', 'other']);
    assert.equal(evaluate(ctx, 'canvas.nodes === canvasState.nodes'), true, 'canvas.nodes 同步');
    assert.deepEqual(fromContext(connectionsOf(ctx)), [
        { from: 'N', to: 'ext' },
        { from: 'other', to: 'N' },
        { from: 'ext', to: 'other' },
    ]);
    assert.deepEqual(node.inputNodeIds, ['keep']);
    // w/h 非有限值不写
    const node2 = { id: 'N2', images: [] };
    ctx.canvasState.nodes.push(node2);
    f(node2, { id: 'ext2', images: [img('f')], w: NaN, h: Infinity });
    assert.equal(node2.w, undefined);
    assert.equal(node2.h, undefined);
    // inputNodeIds 非数组：不崩
    const node3 = { id: 'N3', images: [] };
    ctx.canvasState.nodes.push(node3);
    assert.doesNotThrow(() => f(node3, { id: 'ext3', images: [] }));
    // falsy 守卫
    assert.doesNotThrow(() => f(null, extracted));
    assert.doesNotThrow(() => f(node, null));
});

test('restoreSourceVisualState: 深拷贝 images，按 state 恢复/删除字段，title 回退', t => {
    const { ctx, fn } = loadCascadeModule();
    const f = fn('restoreSourceVisualState');
    const node = { id: 'N', images: [img('old')], w: 99, h: 99, title: 'Old' };
    const state = { images: [img('s')], title: 'My', w: 10, h: 20, scale: 0.5, outputKind: 'video' };
    f(node, state);
    assert.deepEqual(fromContext(node.images), [img('s')]);
    assert.notEqual(node.images, state.images, 'images 必须深拷贝（数组与元素）');
    assert.notEqual(node.images[0], state.images[0]);
    assert.equal(node.title, 'My');
    assert.equal(node.w, 10);
    assert.equal(node.h, 20);
    assert.equal(node.scale, 0.5);
    assert.equal(node.outputKind, 'video');
    // state 缺 title → 按图片数回退；缺 w/h → 删除
    const node2 = { id: 'N2', w: 1, h: 2, images: [] };
    f(node2, { images: [img('a'), img('b')] });
    assert.equal(node2.title, 'Group');
    assert.equal(node2.w, undefined);
    assert.equal(node2.h, undefined);
    f(node2, { images: [img('a')] });
    assert.equal(node2.title, 'Image');
    // falsy 守卫
    assert.doesNotThrow(() => f(null, state));
    assert.doesNotThrow(() => f(node, null));
});
