// #219 canvas-generation-tasks.js 行为单元测试（#199 轨道 4 续：轨道 1 接口化后自由外部名
// 从 42 降到 22，已低于 canvas-undo.js 29 / canvas-connections.js 28 的「可测」线，
// ADR 0020 判据解锁补测）。
//
// canvas-generation-tasks.js 是画布生成任务生命周期模块（#154 从 smart-canvas.js 抽离）：
// 生成提交（firefly-canvas-v1 项目 API 路径）、#189 预生成 flush 失败后的持久化续跑
// （idempotency_key 服务端去重）、#197 四道保存关卡的 pending-action 队列
// （queue→mirror→clear-on-send→seed 恢复）、#198 重放失败的有界重试写回、
// 失败任务取回/重试（retryFailedGeneration）与任务轮询。
// 本文件顶层只有声明（window.__fc* 可观测句柄除外），加载期不触碰外部名。
//
// 依赖面实测（tests/test_canvas_core_layer.py 棘轮口径：独立外部名，总数/自由/接口）：
// **42 个来自 smart-canvas.js 的名字**（自由 22 名 35 处 + 接口 20 名 78 处）
// + **13 个来自 canvas-media-history.js** + **1 个来自 canvas-image-editor.js**。
// 其中：
//   - 13 个由**加载真实模块**满足（不桩）：
//       canvas-core.js       → canvas(let 绑定) / settings(let) / tr / uid / nowMs / toast /
//                              smartGenerationSubmissions / resultMediaUrls /
//                              MEDIA_GROUP_DEFAULT_SCALE / MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE /
//                              smartPendingActionPersister / smartPendingActionRebuilderFor
//                              （register* 两个真实注册函数供测试侧接入 persister/rebuilder）
//       canvas-state.js      → canvasState
//       canvas-node-types.js → NODE_TYPE_PROMPT
//   - 26 个手写桩（全部来自不可独立加载的宿主脚本 smart-canvas.js /
//     canvas-media-history.js / canvas-image-editor.js，按真实契约最小化）：
//       smart-canvas.js 自由名 9 个（22 个里的已测路径子集；其余 13 个在
//       video/RunningHub/LLM/prompt-node 路径，本轮不桩、不测）：
//       - apiErrorMessage / trf / parseSizeAspect / parseSizePair / reducedAspectRatio /
//         copyMediaSizeFields —— 单行契约镜像（分别对应 smart-canvas.js:717-733 /
//         314-316 / 1550-1558 / 875-878 / 879-885 与 copyMediaSizeFields 的字段覆盖语义）
//       - apiImageUsesExplicitResolution / imageRefsOnly / sizeForRun /
//         flushProjectCanvasBeforeGeneration / requestSmartEdgeReveal
//         —— 零逻辑数据驱动/记录器
//       canvasCore 接口成员 12 个（真实 canvasCore Proxy 运行期按名解析到这些全局属性）：
//       - render / scheduleSave / saveCanvas / requestSmartEdgeReveal 之外：
//         acceptProjectGenerationRegistration / importProjectMediaReference /
//         mediaNodeDefaultScale / smartGroupContainingNode / canvasId —— 零逻辑
//         记录器/数据；trf / copyMediaSizeFields / stripImageGenerationMeta 见上
//       canvas-media-history.js 5 个（该模块 98 处反向引用，不可独立加载）：
//       - beginSmartPendingHistoryLog（记录器，返回固定 id）/ smartPendingHistoryId（'') /
//         ensureSmartPendingHistoryLogs（记录器）/ failSmartPendingHistoryLog（记录器）/
//         cleanHistoryImages（恒等，测试只喂空数组或已成形媒体项）
//       canvas-image-editor.js 1 个：
//       - validOutpaintSize（零逻辑返回 null；测试节点不设置 outpaintSize）
//   - 浏览器全局：fetch 换成测试内记录器（桩掉 harness 默认的抛错 fetch），
//     document.getElementById('toast') 显式注册元素（供真实 canvas-core toast 运行）。
// ADR 0020 判据「桩要开始重新实现产品逻辑就停」：26 个手写桩里 20 个零逻辑
// （记录器/数据驱动/恒等），6 个单行契约镜像（逐字对应上述真实实现的行为分支，
// 测试路径触达的分支全部一致）。
//
// 防的回归（全部断言可观察输出/副作用，不是「不抛错就算过」）：
//   - #197 pending-action 时序：queue 写内存 Map + 节点 pendingActionResumes 镜像 +
//     scheduleSave；clear-on-send（persister 返回 true 才发 fetch，且发前从 Map 删除
//     ——single-shot）；persister 未落盘（返回 false）时 entry 与节点标记都必须保留，
//     下一次 save settle 再放；节点被删 → dropped-node-gone；刷新后（Map 为空）从节点
//     持久化标记 seed + 经真实 registerSmartPendingActionRebuilder 重建 replay 闭包；
//     seed 时重试预算已耗尽（replayAttempts>=3）→ 标记从节点删除、不再发请求。
//   - #198 重放失败：fetch 前标记已从盘上清除，失败必须把标记**写回**节点（带
//     replayAttempts 与 nextRetryAt），backoff 60000/180000ms 内不重放，预算 3 次
//     耗尽后永久删除并落 dropped-retry-exhausted；每次写回 dispatch pagehide
//     （pagehide crash-recovery flush 立刻把写回标记存盘）。
//   - #189 队列/幂等：upsert/clearSmartGenerationSubmission 按 idempotencyKey 去重与
//     pending 计数；runApiGeneration 预生成 flush 失败 → 不取消请求，标记进
//     smartPendingPersistenceKeys，save settle 后 resumeSmartPersistencePendingTasks
//     用**同一个 idempotency_key** 重放（服务端按 key 去重）；重放 409 → 清标记，
//     500×2 unknown → 保留标记；queueSmartGenerationRegistration 按节点串行化、
//     前一个失败不影响后一个、完成后清队列 Map；postProjectImageTaskWithReplay 的
//     2 次重试语义（<500 立即拒绝不重试 / 5xx 重试一次 / 耗尽 → smartSubmissionUnknown
//     且携带 payload.idempotency_key）。
//   - 纯函数：aspectRatioForRun 预设/custom/source 三分支（含 reducedAspectRatio 链路）、
//     projectVideoSizeForRun 尺寸矩阵、extractUpstreamTaskId 正则、projectImageTaskUrl
//     路由矩阵、smartPendingActionReplaySucceeded 的 local-edit output_asset_id 判定。
//
// 判为**本轮单测不覆盖**的行为（不是「永远绿的假断言」，是预算/边界外的真实路径）：
//   - runPromptLLMNode / runRunningHubGeneration / runApiVideoGeneration：依赖
//     smart-canvas.js 13 个自由名（LLM 输入收集、RunningHub 节点构造、视频参考处理），
//     本轮 22 名预算只覆盖图像路径；这些路径由浏览器 runner 兜底。
//   - pollSmartCanvasTask（900×2s）的长轮询循环本身：
//     单测只覆盖单次状态迁移（succeeded/cancelled/failed/timeout），
//     分钟级真实轮询无法在单测层有意义地验证。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

// 宿主侧微任务排空：被测代码里的 fire-and-forget（void async）用宿主 tick 等它落定。
// 不触碰 ctx 的 harness 定时器——那些必须由测试显式 advanceTimers() 驱动。
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

// 加载真实底层模块 + 被测模块（按 HTML 相对顺序，harness 会排序）：
// canvas-node-types.js → canvas-core.js → canvas-state.js → canvas-generation-tasks.js。
// 外部名在 loadModules 之前用全局属性桩好即可（canvasCore Proxy 是惰性解析，
// 运行期才按名解析）。
function loadGenerationTasksModule({ nodes = [], canvasPayload = null } = {}) {
    const { ctx, harness } = createCanvasContext();
    const calls = [];
    // --- 数据驱动注册表：测试显式喂数据，桩零逻辑（防「第二套假核心」） ---
    harness.flushResult = true;                 // flushProjectCanvasBeforeGeneration 返回值
    harness.imageRefs = [];                     // imageRefsOnly(refs) 返回值
    harness.sizeForRunResult = '1024x768';      // sizeForRun(settings) 返回值
    harness.explicitResolution = false;         // apiImageUsesExplicitResolution 返回值
    harness.pendingHistoryLogs = [];            // beginSmartPendingHistoryLog 记录
    harness.ensureHistoryCalls = [];            // ensureSmartPendingHistoryLogs 记录
    harness.failHistoryCalls = [];              // failSmartPendingHistoryLog 记录
    harness.acceptedResults = [];               // canvasCore.acceptProjectGenerationRegistration 记录
    harness.alignCalls = [];                    // canvasCore.alignRatioToModel 记录（零逻辑桩，只记录入参）
    harness.rebuilderCalls = [];                // 测试注册的 rebuilder 记录
    harness.replayFired = [];                   // 测试 replay 闭包触发记录
    harness.persisterCalls = [];                // 测试 persister 记录
    harness.fetchLog = [];                      // fetch(url, options) 记录
    harness.fetchResponder = null;              // (url, options) => Response 替身

    const toastEl = harness.element('toast');   // 真实 canvas-core toast 需要 #toast 元素

    // --- canvasCore 接口成员（真实 canvasCore Proxy 运行期按名解析到这些全局属性） ---
    ctx.render = () => calls.push('render');
    ctx.scheduleSave = () => calls.push('scheduleSave');
    ctx.canvasId = 'cv-1';
    ctx.saveCanvas = async () => { calls.push('saveCanvas'); return true; };
    ctx.acceptProjectGenerationRegistration = async (result) => {
        harness.acceptedResults.push(result);
        return true;
    };
    ctx.importProjectMediaReference = async (ref) => ref;
    ctx.mediaNodeDefaultScale = () => 1;
    ctx.smartGroupContainingNode = () => null;
    ctx.stripImageGenerationMeta = (item) => item;  // 恒等：测试只验证 url/kind/name 成形
    // 单行契约镜像（copyMediaSizeFields 的字段覆盖语义：fields 覆盖 item 同名字段）。
    ctx.copyMediaSizeFields = (item, fields) => ({ ...item, ...fields });
    // #199 轨道 4：两条发图路径生成前都要把残留比例收敛到模型能力内。
    // 桩只记录入参（收敛算法本身由 tests/test_canvas_ratio_model_alignment.py 钉）。
    ctx.alignRatioToModel = (settings) => { harness.alignCalls.push(settings); };
    // --- smart-canvas.js 自由名桩（22 个里的 9 个：已测路径子集） ---
    // 单行契约镜像（smart-canvas.js:717-733 触达分支：null/string/detail 字符串/JSON 兜底；
    // 数组 detail 的 pydantic 校验错误分支不在测试路径里）。
    ctx.apiErrorMessage = (data, fallback = 'fallback') => {
        if (!data) return fallback;
        if (typeof data === 'string') return data || fallback;
        const detail = data.detail ?? data.error ?? data.message;
        if (typeof detail === 'string') return detail || fallback;
        try { return JSON.stringify(data); } catch (e) { return fallback; }
    };
    ctx.apiImageUsesExplicitResolution = () => harness.explicitResolution;
    ctx.imageRefsOnly = () => harness.imageRefs;
    ctx.sizeForRun = () => harness.sizeForRunResult;
    ctx.flushProjectCanvasBeforeGeneration = async () => harness.flushResult;
    ctx.requestSmartEdgeReveal = (node) => calls.push('edgeReveal:' + (node?.id || ''));
    // 单行契约镜像（smart-canvas.js:1550-1558）：尺寸串/比例串 → {w,h}。
    ctx.parseSizeAspect = (sizeText) => {
        const text = String(sizeText || '').trim();
        const dims = text.match(/(\d+)\s*[x×]\s*(\d+)/i);
        if (dims) return { w: Math.max(1, Number(dims[1])), h: Math.max(1, Number(dims[2])) };
        const ratio = text.match(/(\d+)\s*[:：]\s*(\d+)/);
        if (ratio) return { w: Math.max(1, Number(ratio[1])), h: Math.max(1, Number(ratio[2])) };
        return { w: 1, h: 1 };
    };
    // 单行契约镜像（smart-canvas.js:875-878）。
    ctx.parseSizePair = (value) => {
        const m = String(value || '').match(/(\d+)\s*x\s*(\d+)/i);
        return m ? { width: Number(m[1]), height: Number(m[2]) } : null;
    };
    // 单行契约镜像（smart-canvas.js:879-885）：gcd 约分比例。
    ctx.reducedAspectRatio = (width, height) => {
        let w = Math.max(1, Math.round(Number(width) || 1));
        let h = Math.max(1, Math.round(Number(height) || 1));
        let a = w, b = h;
        while (b) { const r = a % b; a = b; b = r; }
        return `${w / a}:${h / a}`;
    };

    // --- canvas-media-history.js 5 个桩（该模块不可独立加载） ---
    ctx.beginSmartPendingHistoryLog = (node, defaults) => {
        const id = `ph-${harness.pendingHistoryLogs.length + 1}`;
        harness.pendingHistoryLogs.push({ node: node?.id || '', defaults });
        return id;
    };
    ctx.smartPendingHistoryId = () => '';
    ctx.ensureSmartPendingHistoryLogs = (node, tasks, defaults) => {
        harness.ensureHistoryCalls.push({ node: node?.id || '', tasks, defaults });
    };
    ctx.failSmartPendingHistoryLog = (taskId, error) => {
        harness.failHistoryCalls.push({ taskId, error });
    };
    ctx.cleanHistoryImages = (images = []) => (images || []);

    // --- canvas-image-editor.js 1 个桩 ---
    ctx.validOutpaintSize = () => null;  // 测试节点不设置 outpaintSize

    // --- fetch 记录器（替换 harness 默认的抛错 fetch） ---
    ctx.fetch = async (url, options) => {
        harness.fetchLog.push({ url, options });
        if (harness.fetchResponder) return harness.fetchResponder(url, options);
        throw new Error(`harness: unrecorded fetch ${url}`);
    };

    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-generation-tasks.js']);

    // 单行契约镜像（smart-canvas.js:314-316），用真实 tr（canvas-core.js 加载后才存在；
    // 无 StudioI18n 时 tr 返回 key 本身，测试断言基于 key）。
    const trReal = globalFunction(ctx, 'tr');
    ctx.trf = (key, values = {}) =>
        Object.entries(values).reduce((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), trReal(key));

    // 运行期状态：canvas 是 canvas-core.js 的词法 let 绑定（不是全局对象属性），必须用
    // evaluate 赋值；canvasState 是 window.canvasState 对象，宿主侧直接改属性即可。
    const payload = canvasPayload || {
        id: 'cv-1',
        format: 'firefly-canvas-v1',
        project_id: 'proj-1',
        revision: 7,
        nodes,
        history: [],
    };
    ctx.__canvasPayload = payload;
    evaluate(ctx, 'canvas = __canvasPayload');
    ctx.canvasState.nodes = nodes;
    void toastEl;
    return { ctx, harness, calls };
}

// 构造一个 Response 替身（postProjectImageTaskWithReplay 只用 ok/status/json）。
function fakeResponse({ ok = true, status = 200, data = {} } = {}) {
    return { ok, status, json: async () => data, text: async () => (typeof data === 'string' ? data : JSON.stringify(data)) };
}

test('loads canvas-generation-tasks.js standalone: all lifecycle functions exist, observability handles published', () => {
    const { ctx } = loadGenerationTasksModule();
    // 顶层 function 都在（模块加载成功且无 SyntaxError 的前置证据）。
    for (const name of [
        'runPromptLLMNode', 'queueSmartGenerationRegistration', 'upsertSmartGenerationSubmission',
        'clearSmartGenerationSubmission', 'postProjectImageTaskWithReplay', 'runApiGeneration',
        'runRunningHubGeneration', 'projectVideoSizeForRun', 'runApiVideoGeneration',
        'smartPendingTasks', 'projectImageTaskUrl', 'extractUpstreamTaskId',
        'providerIdForSmartTask',
        'fetchImageTaskQuery', 'querySmartImageTaskNow',
        'pollSmartCanvasTask', 'absorbProjectGenerationResult', 'finalizeSmartPendingTask',
        'resumeSmartPendingNode', 'retryFailedGeneration', 'retryFailedGenerationInner',
        'resumeSmartGenerationSubmission', 'resumeSmartPendingTasks', 'resumeSmartPersistencePendingTasks',
        'queueSmartPendingActionResume', 'mirrorPendingActionToStorage', 'clearPendingActionFromStorage',
        'smartPendingActionReplaySucceeded', 'schedulePendingActionRetryTimer',
        'handlePendingActionReplayFailure', 'hasSmartPendingActionResume', 'resumeSmartPendingActionResumes',
        'promptNodeLLMInputText', 'videoProviderPlatform', 'rhMediaForRun', 'applyUploadedUrlsToSmartRefs',
        'manualSmartMediaLinks', 'applySmartTaskProgress', 'aspectRatioForRun', 'sleep',
    ]) globalFunction(ctx, name);
    // class 声明只落在全局词法环境（不在 window 上），用表达式探测。
    assert.equal(evaluate(ctx, 'typeof ImageTaskRecoverSignal'), 'function', 'ImageTaskRecoverSignal 类已声明');
    // 词法 let/const 绑定可读写（模块私有状态，#197/#198 的账本都在这里）。
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 0, 'pending-action 队列初始为空');
    assert.equal(evaluate(ctx, 'smartPendingPersistenceKeys.size'), 0, '#189 持久化键集初始为空');
    assert.equal(evaluate(ctx, 'SMART_PENDING_ACTION_MAX_REPLAY_ATTEMPTS'), 3, '#198 重试预算');
    assert.deepEqual(fromContext(evaluate(ctx, 'SMART_PENDING_ACTION_RETRY_BACKOFF_MS')), [60000, 180000], '#198 backoff 表');
    assert.equal(evaluate(ctx, 'SMART_REFERENCE_IMAGE_MAX'), 20);
    // #197 可观测句柄：只暴露本模块自己的状态（发布到 window，测试侧直接读）。
    assert.equal(evaluate(ctx, 'window.__fcPendingActionResumes === smartPendingActionResumes'), true, '__fcPendingActionResumes 指向真实 Map');
    assert.equal(evaluate(ctx, 'Array.isArray(window.__fcPendingActionResumeLog)'), true, '__fcPendingActionResumeLog 是数组');
    // 真实底层模块就位。
    assert.equal(evaluate(ctx, 'NODE_TYPE_PROMPT'), 'smart-prompt', 'NODE_TYPE_PROMPT 来自真实 canvas-node-types.js');
    assert.equal(evaluate(ctx, 'canvas && canvas.id'), 'cv-1', 'canvas let 绑定已被 evaluate 赋值');
    assert.equal(evaluate(ctx, 'typeof canvasCore.acceptProjectGenerationRegistration'), 'function', 'canvasCore Proxy 解析接口成员');
});

test('queueSmartGenerationRegistration: per-node serialization, failure isolation, queue cleanup', async () => {
    const { ctx } = loadGenerationTasksModule();
    const queue = globalFunction(ctx, 'queueSmartGenerationRegistration');
    const order = [];
    // 同节点两个操作必须串行（后一个等前一个完成），且返回各自的结果。
    const p1 = queue('n1', async () => { order.push('a-start'); await Promise.resolve(); order.push('a-end'); return 'result-a'; });
    const p2 = queue('n1', async () => { order.push('b-start'); return 'result-b'; });
    const [ra, rb] = await Promise.all([p1, p2]);
    assert.equal(ra, 'result-a');
    assert.equal(rb, 'result-b');
    assert.deepEqual(order, ['a-start', 'a-end', 'b-start'], '同节点操作串行，b 在 a 完全结束后才开始');
    // 完成后队列 Map 清空（不泄漏悬挂 Promise）。
    assert.equal(evaluate(ctx, 'smartGenerationRegistrationQueues.size'), 0, '完成后队列 Map 已清理');

    // 前一个失败不能污染后一个（previous.catch(() => {}) 隔断了链上的 rejection）。
    const q2 = async () => { throw new Error('boom'); };
    const p3 = queue('n2', q2);
    const p4 = queue('n2', async () => 'ok-after-failure');
    await assert.rejects(p3, /boom/, '前一个操作自身的 rejection 仍透传给调用方');
    assert.equal(await p4, 'ok-after-failure', '前一个失败后，后一个照常执行');
    assert.equal(evaluate(ctx, 'smartGenerationRegistrationQueues.size'), 0);

    // 不同节点的队列互不影响。
    const p5 = queue('n3', async () => 'n3-ok');
    assert.equal(await p5, 'n3-ok');
});

test('upsert/clearSmartGenerationSubmission: idempotencyKey dedupe, pending accounting, empty removal', () => {
    const { ctx } = loadGenerationTasksModule({ nodes: [{ id: 'img-1' }] });
    const node = ctx.canvasState.nodes[0];
    const upsert = globalFunction(ctx, 'upsertSmartGenerationSubmission');
    const clear = globalFunction(ctx, 'clearSmartGenerationSubmission');
    const submissions = globalFunction(ctx, 'smartGenerationSubmissions');  // 真实 canvas-core 函数

    // 无 idempotencyKey → 拒绝登记（返回 null，节点不变）。
    assert.equal(upsert(node, { requestPayload: {} }), null, '缺 idempotencyKey 不登记');
    assert.equal(node.generationSubmissions, undefined);

    // 首次登记：outcome 固化为 unknown，node.pending 跟随标记数。
    const saved = upsert(node, { idempotencyKey: 'k1', requestPayload: { prompt: 'p1' }, count: 2, prompt: 'p1', createdAt: 1 });
    assert.equal(saved.outcome, 'unknown', '新登记固化为 unknown');
    assert.equal(node.generationSubmissions.length, 1);
    assert.equal(node.pending, 1, 'pending = max(旧值, 标记数)');

    // 同 key 再登记 → 去重替换（不出现两条同 key 记录），pending 不涨。
    upsert(node, { idempotencyKey: 'k1', requestPayload: { prompt: 'p1-again' }, count: 2, prompt: 'p1-again', createdAt: 2 });
    assert.equal(node.generationSubmissions.length, 1, '同 idempotencyKey 去重（#189：服务端按 key 去重的前提）');
    assert.equal(node.generationSubmissions[0].requestPayload.prompt, 'p1-again', '去重是替换而不是追加');
    assert.equal(node.pending, 1);

    // 不同 key → 追加，pending 涨。
    upsert(node, { idempotencyKey: 'k2', requestPayload: { prompt: 'p2' }, count: 1, prompt: 'p2', createdAt: 3 });
    assert.equal(node.generationSubmissions.length, 2);
    assert.equal(node.pending, 2);

    // 真实 canvas-core 的 smartGenerationSubmissions 过滤无 key 项。
    node.generationSubmissions.push({ requestPayload: {} });
    assert.equal(submissions(node).length, 2, 'smartGenerationSubmissions 只认带 idempotencyKey 的标记');

    // clear：删一条留一条；删完删属性本身（不留空数组）。
    clear(node, 'k1');
    assert.deepEqual(fromContext(submissions(node).map(s => s.idempotencyKey)), ['k2']);
    clear(node, 'k2');
    assert.equal(node.generationSubmissions, undefined, '清空后删除 generationSubmissions 属性');
    clear(node, 'missing');  // 未知 key：no-op，不抛错
    assert.equal(node.generationSubmissions, undefined);
});

test('postProjectImageTaskWithReplay: 2xx pass-through, <500 rejected no-retry, 5xx one retry, unknown carries idempotency_key', async () => {
    const { ctx, harness } = loadGenerationTasksModule();
    const post = globalFunction(ctx, 'postProjectImageTaskWithReplay');
    // 定时编排：postProjectImageTaskWithReplay 的 5xx 路径不抛错而是落回循环尾的
    // `await sleep(250)`（受控 harness 定时器），所以每次 5xx 之后要
    // tick（让下一发 fetch 的微任务落定）+ advanceTimers（放行 sleep）再来一次 tick。

    // 200 → 直接透传 body，只发一次请求。
    harness.fetchResponder = (url) => {
        assert.equal(url, '/api/project-library/proj-1/image-generation-tasks');
        return fakeResponse({ data: { task_id: 'project_img_1' } });
    };
    harness.fetchLog.length = 0;
    const data = await post('proj-1', { prompt: 'p', idempotency_key: 'key-a' });
    assert.equal(data.task_id, 'project_img_1');
    assert.equal(harness.fetchLog.length, 1);

    // 409（<500，服务端明确拒绝）→ smartSubmissionRejected，绝不重试（重试 = 重复计费风险）。
    harness.fetchResponder = () => fakeResponse({ ok: false, status: 409, data: { detail: 'already registered' } });
    harness.fetchLog.length = 0;
    await assert.rejects(
        post('proj-1', { prompt: 'p', idempotency_key: 'key-b' }),
        (e) => e.smartSubmissionRejected === true && e.message === 'already registered',
    );
    assert.equal(harness.fetchLog.length, 1, '<500 拒绝不重试');

    // 503 然后 200 → 重试一次成功。
    let n = 0;
    harness.fetchResponder = () => fakeResponse(n++ === 0 ? { ok: false, status: 503, data: {} } : { data: { task_id: 'project_img_2' } });
    harness.fetchLog.length = 0;
    const pRetry = post('proj-1', { prompt: 'p', idempotency_key: 'key-c' });
    await tick();
    harness.advanceTimers();  // 放行 sleep(250)，第二发 fetch 的微任务在 await 中落定
    assert.deepEqual(await pRetry, { task_id: 'project_img_2' });
    assert.equal(harness.fetchLog.length, 2, '5xx 重试一次');
    assert.ok(harness.timers.some(t => t.ms === 250), '重试前 sleep(250) 走受控定时器');

    // 500 然后 500 → 耗尽 → smartSubmissionUnknown，且错误携带 payload.idempotency_key
    // （#189：unknown 结果必须保留标记，后续用同一个 key 续跑，服务端去重）。
    // 两次 500 各落回一次 sleep（最后一次尝试后的 sleep 是无效等待但真实存在）。
    harness.fetchResponder = () => fakeResponse({ ok: false, status: 500, data: { detail: 'down' } });
    harness.fetchLog.length = 0;
    const pUnknown = post('proj-1', { prompt: 'p', idempotency_key: 'key-d' });
    await tick();
    harness.advanceTimers();
    await tick();
    harness.advanceTimers();
    await assert.rejects(
        pUnknown,
        (e) => e.smartSubmissionUnknown === true && e.idempotencyKey === 'key-d',
        'unknown 错误必须携带 idempotency_key',
    );
    assert.equal(harness.fetchLog.length, 2);

    // AbortError → 立即中止（catch 里 break，无 sleep）。
    harness.fetchResponder = () => { const e = new Error('aborted'); e.name = 'AbortError'; throw e; };
    harness.fetchLog.length = 0;
    const pAbort = post('proj-1', { prompt: 'p', idempotency_key: 'key-e' });
    await assert.rejects(pAbort, (e) => e.smartSubmissionUnknown === true && e.idempotencyKey === 'key-e');
    assert.equal(harness.fetchLog.length, 1, 'AbortError 不重试');

    // 500 然后 400：第一次是服务端故障，第二次是明确拒绝 →
    // 抛 rejected 而不是 unknown（用户输入问题不该进 unknown 续跑路径）。
    let m = 0;
    harness.fetchResponder = () => fakeResponse(m++ === 0 ? { ok: false, status: 500, data: {} } : { ok: false, status: 400, data: { detail: 'bad prompt' } });
    harness.fetchLog.length = 0;
    const pMixed = post('proj-1', { prompt: 'p', idempotency_key: 'key-f' });
    // 创建时立即挂 handler：400 的 rejection 会在后续 tick 期间落定，
    // 晚挂 handler 会触发 unhandledRejection。
    const mixedSettled = assert.rejects(pMixed, (e) => e.smartSubmissionRejected === true && e.message === 'bad prompt');
    await tick();
    harness.advanceTimers();
    await tick();
    await mixedSettled;
    assert.equal(harness.fetchLog.length, 2);
});

// 5xx 双发节奏：postProjectImageTaskWithReplay 的 5xx 路径各落回一次 sleep(250)
// （受控定时器），「tick 让 fetch 微任务落定 + advanceTimers 放行 sleep」共两轮。
async function drainTwoAttempts(harness) {
    await tick();
    harness.advanceTimers();
    await tick();
    harness.advanceTimers();
    await tick();
}

test('runApiGeneration: flush failure keeps the marker, save-settle replay reuses the SAME idempotency_key (#189)', async () => {
    const node = { id: 'img-1', type: 'smart-image' };
    const nodes = [node];
    const { ctx, harness } = loadGenerationTasksModule({ nodes });
    const run = globalFunction(ctx, 'runApiGeneration');
    const settle = globalFunction(ctx, 'resumeSmartPersistencePendingTasks');

    // 预生成 flush 失败 → 不取消请求：标记持久化在节点上 + 抛 smartSavePendingResume，不发 POST。
    harness.flushResult = false;
    const pending = run('make a cat', [], { provider_id: 'p1', model: 'm1', quality: 'auto' }, node, {});
    await assert.rejects(pending, (e) => e.smartSavePendingResume === true && /canvasSavePendingResume/.test(e.message));
    assert.equal(harness.fetchLog.length, 0, 'flush 失败：不发任何请求');
    assert.equal(node.generationSubmissions.length, 1, '提交标记先于 flush 持久化（upsert 在 flush 之前）');
    const key = node.generationSubmissions[0].idempotencyKey;
    assert.match(key, /^browser:proj-1:ph-1$/, 'idempotency_key = browser:<projectId>:<pendingHistoryId>');
    assert.equal(node.generationSubmissions[0].outcome, 'unknown');
    const payload = node.generationSubmissions[0].requestPayload;
    assert.equal(payload.result_node_id, 'img-1');
    assert.equal(payload.base_revision, 7);
    assert.equal(payload.media_type, 'image');
    assert.equal(payload.n, 1);
    assert.equal(payload.aspect_ratio, '1:1');
    assert.equal(node.pending, 1);
    assert.equal(evaluate(ctx, `smartPendingPersistenceKeys.has(${JSON.stringify(key)})`), true, '#189 键集记录待重放 key');

    // 模拟 save settle（autosave 完成 → markAutosaveSaved → resumeSmartPersistencePendingTasks）：
    // 用同一个 idempotency_key 重新提交（服务端按 key 去重，一次点击只产生一个任务）。
    harness.fetchResponder = (url) => {
        if (url === '/api/project-library/proj-1/image-generation-tasks') {
            return fakeResponse({ data: { task_id: 'project_img_9', route: { provider_id: 'p1', model: 'm1' }, project_revision: 8, registered_node: { id: 'img-1' }, idempotency_key: key } });
        }
        if (url === '/api/project-library/proj-1/image-generation-tasks/project_img_9') {
            return fakeResponse({ data: { status: 'succeeded', result: {} } });
        }
        throw new Error('unexpected fetch ' + url);
    };
    settle(nodes);
    await tick();
    assert.equal(harness.fetchLog.length, 1, 'save settle 触发重放 POST');
    const postBody = JSON.parse(harness.fetchLog[0].options.body);
    assert.equal(postBody.idempotency_key, key, '重放必须使用同一个 idempotency_key');
    assert.equal(postBody.result_node_id, 'img-1');
    assert.equal(postBody.base_revision, 7);
    assert.equal(node.generationSubmissions, undefined, '注册成功清除提交标记');
    assert.equal(harness.acceptedResults.length, 1);
    assert.deepEqual(fromContext(harness.acceptedResults[0].taskIds), ['project_img_9']);
    assert.equal(harness.acceptedResults[0].idempotencyKey, key);
    assert.equal(node.pendingTasks.length, 1);
    assert.equal(node.pendingTasks[0].taskId, 'project_img_9');

    // 驱动轮询到终态（2000ms 是受控定时器）：succeeded → finalize 清 pending。
    harness.advanceTimers();
    await tick();
    assert.equal(harness.fetchLog.length, 2, '轮询拉取任务结果');
    assert.equal(harness.fetchLog[1].url, '/api/project-library/proj-1/image-generation-tasks/project_img_9');
    assert.equal(node.pending, 0, 'finalize 递减 pending');
    assert.equal(node.pendingTasks, undefined, '全部完成 → 删除 pendingTasks');
    assert.ok(typeof node.runFinishedAt === 'number', '终态写入 runFinishedAt');

    // 下一次 settle：标记已清 → 不再发请求，陈旧 key 从 #189 键集清理。
    settle(nodes);
    await tick();
    assert.equal(harness.fetchLog.length, 2, '二次 settle 不重放（标记已清）');
    assert.equal(evaluate(ctx, 'smartPendingPersistenceKeys.size'), 0, '陈旧 key 在下次 settle 清理');
});

test('runApiGeneration: flush success sends exactly one POST with the durable payload and resets run timers', async () => {
    const node = { id: 'img-2', type: 'smart-image', runStartedAt: 111, runFinishedAt: 222, runElapsedMs: 111, runTimerHidden: true };
    const nodes = [node];
    const { ctx, harness } = loadGenerationTasksModule({ nodes });
    const run = globalFunction(ctx, 'runApiGeneration');
    harness.flushResult = true;
    harness.fetchResponder = (url) => {
        if (url === '/api/project-library/proj-1/image-generation-tasks') {
            return fakeResponse({ data: { task_id: 'project_img_5', route: { provider_id: 'p1', model: 'm1' }, project_revision: 8, registered_node: { id: 'img-2' }, idempotency_key: 'browser:proj-1:ph-1' } });
        }
        throw new Error('unexpected fetch ' + url);
    };

    const result = await run('make a cat', [], { provider_id: 'p1', model: 'm1', quality: 'auto' }, node, {});

    // 外层调用经注册队列包装，透传内层的注册结果。
    assert.deepEqual(fromContext(result.taskIds), ['project_img_5']);
    assert.equal(result.idempotencyKey, 'browser:proj-1:ph-1');
    assert.equal(result.projectRevision, 8);
    assert.equal(result.pendingHistoryId, 'ph-1');
    assert.equal(harness.pendingHistoryLogs.length, 1, 'pendingHistoryId 只在外层调用生成一次（内层复用）');
    // 生成前必须先过比例收敛（否则节点上残留的模型外比例会直接进请求）。
    assert.equal(harness.alignCalls.length, 1, 'runApiGeneration 必须在构造载荷前调 canvasCore.alignRatioToModel');
    // 恰好一个 POST，载荷是持久化契约。
    assert.equal(harness.fetchLog.length, 1);
    const body = JSON.parse(harness.fetchLog[0].options.body);
    assert.equal(body.prompt, 'make a cat');
    assert.equal(body.idempotency_key, 'browser:proj-1:ph-1');
    assert.equal(body.result_node_id, 'img-2');
    assert.equal(body.base_revision, 7);
    assert.equal(body.media_type, 'image');
    assert.equal(body.aspect_ratio, '1:1');
    assert.equal(node.generationSubmissions, undefined, 'POST 成功清除标记');
    // 新运行重置计时器：持久化节点不得带着上次生成的旧 runStartedAt 无限涨。
    assert.ok(node.runStartedAt > 111, 'runStartedAt 重置为当前时间');
    assert.equal(node.runFinishedAt, undefined);
    assert.equal(node.runElapsedMs, undefined);
    assert.equal(node.runTimerHidden, false);
});

test('resumeSmartGenerationSubmission: rejected(409) clears the marker, unknown(500x2) keeps it, double-fire guard', async () => {
    // 409：服务端明确拒绝 → 标记失去意义，清除。
    {
        const node = { id: 'img-3', type: 'smart-image', generationSubmissions: [{ idempotencyKey: 'k-3', requestPayload: { prompt: 'p', n: 1 }, prompt: 'p', count: 1, pendingHistoryId: 'ph-3' }] };
        const nodes = [node];
        const { ctx, harness } = loadGenerationTasksModule({ nodes });
        const resume = globalFunction(ctx, 'resumeSmartGenerationSubmission');
        harness.fetchResponder = () => fakeResponse({ ok: false, status: 409, data: { detail: 'already registered' } });
        await resume(node, node.generationSubmissions[0]);
        assert.equal(harness.fetchLog.length, 1);
        assert.equal(node.generationSubmissions, undefined, '明确拒绝 → 清标记');
    }
    // 500x2：结果 unknown（请求可能已生效）→ 必须保留标记，后续用同一 key 重试（服务端去重）。
    {
        const node = { id: 'img-4', type: 'smart-image', generationSubmissions: [{ idempotencyKey: 'k-4', requestPayload: { prompt: 'p', n: 1 }, prompt: 'p', count: 1, pendingHistoryId: 'ph-4' }] };
        const nodes = [node];
        const { ctx, harness } = loadGenerationTasksModule({ nodes });
        const resume = globalFunction(ctx, 'resumeSmartGenerationSubmission');
        harness.fetchResponder = () => fakeResponse({ ok: false, status: 500, data: { detail: 'down' } });
        const p = resume(node, node.generationSubmissions[0]);  // 内部吞错（catch + toast），正常 resolve
        await drainTwoAttempts(harness);
        await p;
        assert.equal(harness.fetchLog.length, 2, 'unknown 走了两次尝试');
        assert.equal(node.generationSubmissions.length, 1, 'unknown 结果 → 保留标记');
        assert.equal(node.generationSubmissions[0].idempotencyKey, 'k-4');
    }
    // 防连发：重放在途时同一 node+key 的第二次触发立即返回（activeSmartSubmissionRecoveries）。
    {
        const node = { id: 'img-5', type: 'smart-image', generationSubmissions: [{ idempotencyKey: 'k-5', requestPayload: { prompt: 'p', n: 1 }, prompt: 'p', count: 1, pendingHistoryId: 'ph-5' }] };
        const nodes = [node];
        const { ctx, harness } = loadGenerationTasksModule({ nodes });
        const resume = globalFunction(ctx, 'resumeSmartGenerationSubmission');
        harness.fetchResponder = () => fakeResponse({ ok: false, status: 500, data: { detail: 'down' } });
        const sub = node.generationSubmissions[0];
        const [p1, p2] = [resume(node, sub), resume(node, sub)];
        await drainTwoAttempts(harness);
        await Promise.all([p1, p2]);
        assert.equal(harness.fetchLog.length, 2, '双重触发只发一轮（2 次尝试），不是两轮（4 次）');
        assert.equal(node.generationSubmissions.length, 1);
    }
});

test('aspectRatioForRun: preset mapping, custom ratio pass-through, source size fallback via reducedAspectRatio', () => {
    const { ctx, harness } = loadGenerationTasksModule();
    const aspectRatioForRun = globalFunction(ctx, 'aspectRatioForRun');
    // 预设表（#200 从 smart-canvas.js 搬回本模块的函数，必须保持同一映射）。
    assert.equal(aspectRatioForRun({}), '1:1', '缺省 ratio 走 square');
    assert.equal(aspectRatioForRun({ ratio: 'square' }), '1:1');
    assert.equal(aspectRatioForRun({ ratio: 'portrait' }), '2:3');
    assert.equal(aspectRatioForRun({ ratio: 'landscape' }), '3:2');
    assert.equal(aspectRatioForRun({ ratio: 'portrait43' }), '3:4');
    assert.equal(aspectRatioForRun({ ratio: 'landscape43' }), '4:3');
    assert.equal(aspectRatioForRun({ ratio: 'story' }), '9:16');
    assert.equal(aspectRatioForRun({ ratio: 'wide' }), '16:9');
    assert.equal(aspectRatioForRun({ ratio: 'ultrawide' }), '21:9');
    assert.equal(aspectRatioForRun({ ratio: 'ultratall' }), '9:21');
    assert.equal(aspectRatioForRun({ ratio: 'bogus' }), '1:1', '未知预设回退 1:1');
    // custom：显式 customRatio 直出。
    assert.equal(aspectRatioForRun({ ratio: 'custom', customRatio: '3:4' }), '3:4');
    // custom/source：无 customRatio 时从尺寸串推导比例（parseSizePair → reducedAspectRatio 链路）。
    harness.sizeForRunResult = '1024x768';
    assert.equal(aspectRatioForRun({ ratio: 'custom' }), '4:3', '1024x768 约分为 4:3');
    assert.equal(aspectRatioForRun({ ratio: 'custom', customRatio: '   ' }), '4:3', '空白 customRatio 等同缺失');
    assert.equal(aspectRatioForRun({ ratio: 'source' }), '4:3');
    harness.sizeForRunResult = '1000x1000';
    assert.equal(aspectRatioForRun({ ratio: 'custom' }), '1:1');
    // 尺寸串无法解析 → 空串（调用方决定兜底），不是 undefined/抛错。
    harness.sizeForRunResult = '';
    assert.equal(aspectRatioForRun({ ratio: 'source' }), '');
});

test('projectVideoSizeForRun: ratio x resolution size matrix', () => {
    const { ctx } = loadGenerationTasksModule();
    const sizeForVideo = globalFunction(ctx, 'projectVideoSizeForRun');
    assert.deepEqual(fromContext(sizeForVideo({})), { width: 1280, height: 720, size: '1280x720' }, '缺省 16:9/720p');
    assert.deepEqual(fromContext(sizeForVideo({ videoAspect: '9:16', videoResolution: '1080p' })), { width: 1080, height: 1920, size: '1080x1920' });
    assert.deepEqual(fromContext(sizeForVideo({ videoAspect: '1:1', videoResolution: '480p' })), { width: 480, height: 480, size: '480x480' });
    assert.deepEqual(fromContext(sizeForVideo({ videoAspect: 'bogus' })), { width: 1280, height: 720, size: '1280x720' }, '未知比例回退 16:9');
    assert.deepEqual(fromContext(sizeForVideo({ videoResolution: 'BOGUS' })), { width: 1280, height: 720, size: '1280x720' }, '未知档位按 720p 原尺寸');
});

test('extractUpstreamTaskId: regex extraction from error text (task_id / taskId / task id, = or : or full-width :)', () => {
    const { ctx } = loadGenerationTasksModule();
    const extract = globalFunction(ctx, 'extractUpstreamTaskId');
    assert.equal(extract('upstream task_id: abc123'), 'abc123');
    assert.equal(extract('provider says taskId=XYZ-1 done'), 'XYZ-1');
    assert.equal(extract('task id：abc_1.x'), 'abc_1.x');
    assert.equal(extract('TASK_ID: upper-case-key'), 'upper-case-key');
    assert.equal(extract('no id here'), '');
    assert.equal(extract(''), '');
    assert.equal(extract(null), '');
});

test('projectImageTaskUrl: v1 project task ids route to project-library endpoints, others to legacy', () => {
    const node = { id: 'img-1' };
    const { ctx } = loadGenerationTasksModule({ nodes: [node] });
    const url = globalFunction(ctx, 'projectImageTaskUrl');
    // firefly-canvas-v1 + project_img_/project_vid_ 前缀 → 项目库端点（projectId 走 encodeURIComponent）。
    assert.equal(url('project_img_A'), '/api/project-library/proj-1/image-generation-tasks/project_img_A');
    assert.equal(url('project_vid_B'), '/api/project-library/proj-1/video-generation-tasks/project_vid_B');
    // 无前缀 → 旧端点。
    assert.equal(url('img_x'), '/api/canvas-image-tasks/img_x');
    assert.equal(url(''), '/api/canvas-image-tasks/');
    // projectId 兜底：canvas 无 project_id/project 时用 canvasCore.canvasId。
    evaluate(ctx, 'canvas = { id: "cv-1", format: "firefly-canvas-v1", nodes: __canvasPayload.nodes, history: [] }');
    assert.equal(url('project_img_C'), '/api/project-library/cv-1/image-generation-tasks/project_img_C');
    // 旧 canvas（无 format）一律走旧端点。
    evaluate(ctx, 'canvas = { id: "cv-1", nodes: __canvasPayload.nodes, history: [] }');
    assert.equal(url('project_img_A'), '/api/canvas-image-tasks/project_img_A');
    // 特殊字符 projectId 必须编码。
    evaluate(ctx, 'canvas = { id: "cv-1", format: "firefly-canvas-v1", project_id: "proj 1", nodes: __canvasPayload.nodes, history: [] }');
    assert.equal(url('project_img_D'), '/api/project-library/proj%201/image-generation-tasks/project_img_D');
});

// ---------- #197：pending-action 队列（video / local-edit / photoshop / conversation 四道保存关卡） ----------

test('queueSmartPendingActionResume: in-memory entry + node mirror + scheduleSave; input guards', () => {
    const node = { id: 'n1', type: 'smart-video' };
    const nodes = [node];
    const { ctx, calls } = loadGenerationTasksModule({ nodes });
    const queue = globalFunction(ctx, 'queueSmartPendingActionResume');
    const mirror = globalFunction(ctx, 'mirrorPendingActionToStorage');
    const has = globalFunction(ctx, 'hasSmartPendingActionResume');
    const replay = async () => {};

    const actionId = queue('video', 'n1', replay, { prompt: 'p', refs: [] });
    assert.equal(actionId, 'action-video-1', '首个 entry 编号 action-video-1');
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 1);
    // 节点镜像是持久化账本（#197：增量保存只带节点字段，顶层 canvas 字段不进 mutation 快照）。
    const marks = fromContext(node.pendingActionResumes);
    assert.equal(marks.length, 1);
    assert.equal(marks[0].actionId, 'action-video-1');
    assert.equal(marks[0].kind, 'video');
    assert.deepEqual(marks[0].payload, { prompt: 'p', refs: [] });
    assert.ok(typeof marks[0].enqueuedAt === 'number');
    assert.ok(calls.includes('scheduleSave'), '入队即触发保存（失败的 flush 把画布弄脏了）');
    // 内存 entry 携带不可序列化的 replay 闭包（镜像里只有 payload）。
    const entry = ctx.__fcPendingActionResumes.get(actionId);
    assert.equal(typeof entry.replay, 'function');
    assert.equal(entry.kind, 'video');
    assert.equal(entry.nodeId, 'n1');
    // hasSmartPendingActionResume 按 kind+nodeId 精确判定。
    assert.equal(has('video', 'n1'), true);
    assert.equal(has('video', 'n2'), false);
    assert.equal(has('local-edit', 'n1'), false);

    // 第二个 entry：seq 递增；同 actionId 重复 mirror 不重复追加。
    assert.equal(queue('video', 'n1', replay, { prompt: 'p2' }), 'action-video-2');
    assert.equal(node.pendingActionResumes.length, 2);
    mirror(entry);
    assert.equal(node.pendingActionResumes.length, 2, '同 actionId 的 mirror 是 no-op（去重）');

    // 非法输入：无 nodeId / replay 非函数 → 不排队。
    assert.equal(queue('video', '', replay, {}), null);
    assert.equal(queue('video', 'n1', 'not-a-function', {}), null);
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 2);

    // 节点不存在：内存 entry 仍建（权威），mirror 尽力而为（best-effort）。
    assert.equal(queue('video', 'ghost-node', replay, {}), 'action-video-3');
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 3);

    assert.deepEqual(fromContext(ctx.__fcPendingActionResumeLog).map(e => e.event), ['queued', 'queued', 'queued'], '守卫调用不产生日志事件');
});

test('resumeSmartPendingActionResumes: clear-on-send fires exactly once (single-shot), persister lands before the fetch', async () => {
    const node = { id: 'n1', type: 'smart-video' };
    const nodes = [node];
    const { ctx, harness } = loadGenerationTasksModule({ nodes });
    const queue = globalFunction(ctx, 'queueSmartPendingActionResume');
    const resume = globalFunction(ctx, 'resumeSmartPendingActionResumes');
    let fired = 0;
    const actionId = queue('video', 'n1', async () => { fired++; }, { prompt: 'p' });
    // 真实 core 的 persister 注册口（smart-canvas.js 在运行期注册把「标记已清」状态写盘的 persister）。
    globalFunction(ctx, 'registerSmartPendingActionPersister')(async (entry) => {
        harness.persisterCalls.push(entry.actionId);
        return true;  // clear-on-send：「标记已清」落盘成功
    });

    await resume(nodes);
    await tick();
    assert.equal(fired, 1, '重放恰好发一次');
    assert.equal(harness.persisterCalls.length, 1);
    assert.equal(harness.persisterCalls[0], actionId, 'persister 在 fetch 前提交「标记已清」');
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 0, 'entry 在重放前从 Map 删除（single-shot：之后的 settle 不可能再发）');
    assert.deepEqual(fromContext(ctx.__fcPendingActionResumeLog).map(e => e.event), ['queued', 'replayed']);

    // 第二次 settle（persister 已把标记清盘，节点上不再有标记）：无事发生。
    node.pendingActionResumes = [];
    await resume(nodes);
    await tick();
    assert.equal(fired, 1, '标记清盘后绝不重放');
    assert.equal(harness.persisterCalls.length, 1);
});

test('resumeSmartPendingActionResumes: persister not landed keeps entry AND node mark until the next settle (#197 window)', async () => {
    const node = { id: 'n1', type: 'smart-video' };
    const nodes = [node];
    const { ctx, harness } = loadGenerationTasksModule({ nodes });
    const queue = globalFunction(ctx, 'queueSmartPendingActionResume');
    const resume = globalFunction(ctx, 'resumeSmartPendingActionResumes');
    let fired = 0;
    queue('video', 'n1', async () => { fired++; }, { prompt: 'p' });
    let persisterResult = false;
    globalFunction(ctx, 'registerSmartPendingActionPersister')(async () => {
        harness.persisterCalls.push(1);
        return persisterResult;
    });

    // persister 未落盘：entry 留在 Map、节点标记保留——这个窗口内刷新页面照样能重放。
    await resume(nodes);
    await tick();
    assert.equal(fired, 0, 'persister 未落盘 → 不发请求');
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 1, 'entry 留在内存 Map');
    assert.equal(node.pendingActionResumes.length, 1, '节点标记保留（刷新账本还在）');
    assert.deepEqual(fromContext(ctx.__fcPendingActionResumeLog).map(e => e.event), ['queued', 'clear-persist-pending']);

    // 下一次 save settle，persister 落盘 → 恰好发一次。
    persisterResult = true;
    await resume(nodes);
    await tick();
    assert.equal(fired, 1);
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 0);
    assert.ok(ctx.__fcPendingActionResumeLog.some(e => e.event === 'replayed'));
});

test('resumeSmartPendingActionResumes: node deleted → dropped-node-gone', async () => {
    const node = { id: 'n1', type: 'smart-video' };
    const nodes = [node];
    const { ctx } = loadGenerationTasksModule({ nodes });
    const queue = globalFunction(ctx, 'queueSmartPendingActionResume');
    const resume = globalFunction(ctx, 'resumeSmartPendingActionResumes');
    let fired = 0;
    queue('video', 'n1', async () => { fired++; }, { prompt: 'p' });
    assert.equal(node.pendingActionResumes.length, 1);
    // 节点被删除（用户删节点；刷新后画布里没有该节点）。
    ctx.canvasState.nodes.length = 0;
    await resume([]);
    await tick();
    assert.equal(fired, 0);
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 0, '节点没了 → entry 丢弃');
    assert.deepEqual(fromContext(ctx.__fcPendingActionResumeLog).map(e => e.event), ['queued', 'dropped-node-gone']);
});

test('resumeSmartPendingActionResumes: reload seeding from node marks, replay rebuilt via registered rebuilder', async () => {
    const node = {
        id: 'n1', type: 'smart-video',
        pendingActionResumes: [{ actionId: 'action-video-7', kind: 'video', payload: { prompt: 'p7' }, enqueuedAt: 111, replayAttempts: 0, nextRetryAt: 0 }],
    };
    const nodes = [node];
    const { ctx, harness } = loadGenerationTasksModule({ nodes });
    // 全新 context：内存 Map 为空（刷新后活闭包没了），节点上的持久化标记就是「未发送」账本。
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 0);
    // 经真实 core 的 registerSmartPendingActionRebuilder 重建 replay（从可序列化 payload）。
    globalFunction(ctx, 'registerSmartPendingActionRebuilder')('video', (entry, live) => {
        harness.rebuilderCalls.push([entry.actionId, live.id]);
        return async () => { harness.replayFired.push(entry.actionId); };
    });
    await globalFunction(ctx, 'resumeSmartPendingActionResumes')(nodes);
    await tick();
    assert.deepEqual(harness.rebuilderCalls, [['action-video-7', 'n1']], 'rebuilder 拿到 entry 与活节点');
    assert.deepEqual(harness.replayFired, ['action-video-7'], '重建的 replay 被触发');
    assert.deepEqual(fromContext(ctx.__fcPendingActionResumeLog).map(e => e.event), ['seeded', 'replayed']);
    assert.equal(ctx.__fcPendingActionResumeLog[0].attempts, 0, 'seed 事件记录预算消耗');
});

test('resumeSmartPendingActionResumes: seeded mark with exhausted budget is dropped; no rebuilder → entry waits', async () => {
    // 边界：replayAttempts=2（预算 3 未耗尽）→ seed 且照发。
    {
        const node = { id: 'n1', type: 'smart-video', pendingActionResumes: [{ actionId: 'action-video-1', kind: 'video', payload: { prompt: 'p' }, enqueuedAt: 1, replayAttempts: 2, nextRetryAt: 0 }] };
        const nodes = [node];
        const { ctx, harness } = loadGenerationTasksModule({ nodes });
        globalFunction(ctx, 'registerSmartPendingActionRebuilder')('video', (entry) => async () => { harness.replayFired.push(entry.actionId); });
        await globalFunction(ctx, 'resumeSmartPendingActionResumes')(nodes);
        await tick();
        assert.deepEqual(harness.replayFired, ['action-video-1'], '2 次尝试 < 预算 3 → 还有最后机会');
    }
    // replayAttempts=3 → 标记是死重：从节点删除（下次保存落盘移除），绝不发请求。
    {
        const node = { id: 'n1', type: 'smart-video', pendingActionResumes: [{ actionId: 'action-video-1', kind: 'video', payload: { prompt: 'p' }, enqueuedAt: 1, replayAttempts: 3, nextRetryAt: 0 }] };
        const nodes = [node];
        const { ctx, harness } = loadGenerationTasksModule({ nodes });
        globalFunction(ctx, 'registerSmartPendingActionRebuilder')('video', () => { throw new Error('rebuilder 不应被调用'); });
        await globalFunction(ctx, 'resumeSmartPendingActionResumes')(nodes);
        await tick();
        assert.equal(harness.replayFired.length, 0);
        assert.deepEqual(fromContext(node.pendingActionResumes), [], '耗尽的标记从节点删除');
        assert.deepEqual(fromContext(ctx.__fcPendingActionResumeLog).map(e => e.event), ['dropped-retry-exhausted']);
        assert.equal(ctx.__fcPendingActionResumeLog[0].attempts, 3);
        assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 0);
    }
    // 未注册 rebuilder 的 kind：single-shot 已从 Map 删除，rebuild 失败 → 记
    // replay-missing-rebuilder 后**不再回队列**（entry 丢弃；持久化标记由关卡自身的
    // 恢复路径处理——本模块不假装能恢复自己无法重建的请求）。
    {
        const node = { id: 'n1', type: 'smart-video', pendingActionResumes: [{ actionId: 'action-video-9', kind: 'conversation', payload: {}, enqueuedAt: 1, replayAttempts: 0, nextRetryAt: 0 }] };
        const nodes = [node];
        const { ctx, harness } = loadGenerationTasksModule({ nodes });
        await globalFunction(ctx, 'resumeSmartPendingActionResumes')(nodes);
        await tick();
        assert.equal(harness.replayFired.length, 0);
        assert.deepEqual(fromContext(ctx.__fcPendingActionResumeLog).map(e => e.event), ['seeded', 'replay-missing-rebuilder']);
        assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 0, '无 rebuilder → entry 不回队列（single-shot 删除已发生）');
        assert.equal(node.pendingActionResumes.length, 1, '节点标记未动（没有 persister 参与，清盘从未发生）');
    }
});

// ---------- #198：重放失败的有界重试写回（clear-on-send 之后失败，标记必须写回，不能永久丢失） ----------

test('handlePendingActionReplayFailure via dispatcher: mark written back with budget, backoff honored, exhaustion drops (#198)', async () => {
    const node = { id: 'n1', type: 'smart-video' };
    const nodes = [node];
    const { ctx, harness } = loadGenerationTasksModule({ nodes });
    const queue = globalFunction(ctx, 'queueSmartPendingActionResume');
    const resume = globalFunction(ctx, 'resumeSmartPendingActionResumes');
    let failures = 0;
    const actionId = queue('video', 'n1', async () => {
        failures++;
        throw new Error('net down ' + failures);
    }, { prompt: 'p' });
    globalFunction(ctx, 'registerSmartPendingActionPersister')(async () => { harness.persisterCalls.push(1); return true; });

    // ---- 第 1 次重放失败：标记写回节点（带 replayAttempts + nextRetryAt），backoff 60000 ----
    await resume(nodes);
    await tick();
    assert.equal(failures, 1);
    const entry = ctx.__fcPendingActionResumes.get(actionId);
    assert.equal(entry, undefined || ctx.__fcPendingActionResumes.get(actionId), 'entry 仍在 Map（重新入队）');
    assert.equal(ctx.__fcPendingActionResumes.get(actionId).replayAttempts, 1);
    assert.ok(ctx.__fcPendingActionResumes.get(actionId).nextRetryAt > Date.now() && ctx.__fcPendingActionResumes.get(actionId).nextRetryAt <= Date.now() + 61000, 'backoff = 60000ms');
    // 写回的节点标记（#198 核心：刷新恢复的账本必须带预算计数器与重试截止时间）。
    assert.equal(node.pendingActionResumes.length, 1, '标记写回节点');
    const mark = node.pendingActionResumes[0];
    assert.equal(mark.actionId, actionId);
    assert.equal(mark.replayAttempts, 1);
    assert.equal(mark.nextRetryAt, ctx.__fcPendingActionResumes.get(actionId).nextRetryAt, '写回标记与内存 entry 同一 backoff');
    let log = fromContext(ctx.__fcPendingActionResumeLog);
    assert.deepEqual(log.map(e => e.event), ['queued', 'replay-failed', 'replay-scheduled-retry']);
    assert.equal(log[1].error, 'net down 1', 'replay-failed 记录错误详情');
    assert.equal(log[2].attempt, 1);
    assert.ok(log[2].retryInMs >= 59000 && log[2].retryInMs <= 60000);
    // 写回后立即 dispatch pagehide（crash-recovery flush 马上把标记存盘）。
    assert.equal(harness.dispatchedEvents.filter(ev => ev.type === 'pagehide').length, 1, '写回触发 pagehide 持久化');
    // 重试定时器挂上（delay≈60000，harness 受控、不真等）。
    assert.ok(harness.timers.some(t => !t.fired && !t.cancelled && t.ms >= 59000 && t.ms <= 60000), 'backoff 定时器已挂');

    // ---- backoff 窗口内的 settle：绝不重放、也不消耗 persister ----
    await resume(nodes);
    await tick();
    assert.equal(failures, 1, 'backoff 窗口内不重放（写回触发的 autosave settle 不能二次触发）');
    assert.equal(harness.persisterCalls.length, 1, 'backoff 窗口内不走 persister（entry 在 Map 里但被 nextRetryAt 拦住）');

    // ---- 到点第 2 次失败：backoff 180000 ----
    ctx.__fcPendingActionResumes.get(actionId).nextRetryAt = 0;
    await resume(nodes);
    await tick();
    assert.equal(failures, 2);
    assert.equal(ctx.__fcPendingActionResumes.get(actionId).replayAttempts, 2);
    const next2 = ctx.__fcPendingActionResumes.get(actionId).nextRetryAt;
    assert.ok(next2 > Date.now() && next2 <= Date.now() + 181000, '第二次 backoff = 180000ms');
    log = fromContext(ctx.__fcPendingActionResumeLog);
    assert.equal(log.filter(e => e.event === 'replay-scheduled-retry').length, 2);
    assert.equal(log.filter(e => e.event === 'replay-failed').length, 2);
    assert.equal(harness.dispatchedEvents.filter(ev => ev.type === 'pagehide').length, 2, '每次写回都触发一次 pagehide');

    // ---- 第 3 次失败：预算耗尽 → 永久丢弃（Map + 节点标记都清），不再 pagehide ----
    ctx.__fcPendingActionResumes.get(actionId).nextRetryAt = 0;
    await resume(nodes);
    await tick();
    assert.equal(failures, 3, '预算 3 次全部用完');
    assert.equal(evaluate(ctx, 'smartPendingActionResumes.size'), 0, '预算耗尽 → entry 永久丢弃');
    assert.deepEqual(fromContext(node.pendingActionResumes), [], '节点标记清除（下次保存落盘移除）');
    log = fromContext(ctx.__fcPendingActionResumeLog);
    const dropped = log.filter(e => e.event === 'dropped-retry-exhausted');
    assert.equal(dropped.length, 1);
    assert.equal(dropped[0].attempts, 3, '丢弃日志记录耗尽预算');
    assert.equal(harness.dispatchedEvents.filter(ev => ev.type === 'pagehide').length, 2, '耗尽分支不 dispatch pagehide（没有要持久化的写回了）');
    assert.equal(harness.persisterCalls.length, 3, '每次实际重放前都先过 persister（clear-on-send）');

    // ---- 丢弃后的 settle：无事发生 ----
    await resume(nodes);
    await tick();
    assert.equal(failures, 3);
});

test('smartPendingActionReplaySucceeded: local-edit 以 output_asset_id 变化为成功判据，其余 kind 解析即成功', () => {
    const { ctx } = loadGenerationTasksModule();
    const fn = globalFunction(ctx, 'smartPendingActionReplaySucceeded');
    assert.equal(fn('local-edit', 'old-asset', { output_asset_id: 'new-asset' }), true, 'local-edit：output_asset_id 变了 = 成功');
    assert.equal(fn('local-edit', 'old-asset', { output_asset_id: 'old-asset' }), false, 'local-edit：未变 = 失败（提交吞了 fetch 错误，必须看可观察效果）');
    assert.equal(fn('local-edit', '', {}), false, 'local-edit：从无 asset 到无 asset = 失败');
    assert.equal(fn('local-edit', 'old-asset', null), false);
    assert.equal(fn('video', 'anything', {}), true, '非 local-edit：replay 闭包自己抛错表失败，解析即成功');
    assert.equal(fn('conversation', '', null), true);
});

test('retryFailedGeneration: 先取回已归档输出（无新计费），无归档则换新幂等键重新生成', async () => {
    // ---- 路径 1：服务端已归档成功输出 → 直接取回，零 POST ----
    {
        const node = { id: 'img-1', type: 'smart-image', failedGenerationTasks: [{ taskId: 'project_img_1', error: 'boom' }] };
        const nodes = [node];
        const { ctx, harness, calls } = loadGenerationTasksModule({ nodes });
        harness.fetchResponder = (url) => {
            assert.equal(url, '/api/project-library/proj-1/image-generation-tasks/project_img_1');
            return fakeResponse({ data: { status: 'failed', result: { history_record: { outputs: [{ asset_id: 'a1', kind: 'image' }] } } } });
        };
        await globalFunction(ctx, 'retryFailedGeneration')('img-1');
        assert.equal(harness.fetchLog.length, 1, '取回路径只发一次 GET，零 POST（不重复计费）');
        assert.deepEqual(fromContext(node.failedGenerationTasks), [], '取回成功后失败清单清空');
        assert.equal(node.images.length, 1);
        assert.equal(node.images[0].url, '/api/project-library/proj-1/media/a1', 'media url 按 projectId 构造');
        assert.equal(node.images[0].name, 'output-1.png', '归档输出无名时按序生成 output-N.png');
        assert.equal(node.images[0].kind, 'image');
        assert.equal(node.images[0].generatedResult, true);
        assert.equal(node.title, 'Image', '终态清理：单图节点标题');
        assert.equal(node.running, false);
        assert.equal(node.outputKind, 'image');
        assert.equal(node._retryInFlight, false, '防连点标志在 finally 清除');
        assert.ok(calls.some(c => c === 'edgeReveal:img-1'), '取回的图触发 edge reveal');
    }
    // ---- 路径 2：无归档 → 新幂等键重新提交（不换键会永远重放同一个已失败任务）----
    {
        const node = {
            id: 'img-2', type: 'smart-image',
            failedGenerationTasks: [{ taskId: 'project_img_1', error: 'boom' }],
            generationSubmissions: [{ idempotencyKey: 'k-old', requestPayload: { prompt: 'p', n: 1 }, count: 1, prompt: 'p', createdAt: 1 }],
        };
        const nodes = [node];
        const { ctx, harness } = loadGenerationTasksModule({ nodes });
        harness.fetchResponder = (url, options) => {
            if (url === '/api/project-library/proj-1/image-generation-tasks/project_img_1') {
                return fakeResponse({ data: { status: 'failed', result: {} } });
            }
            if (url === '/api/project-library/proj-1/image-generation-tasks') {
                const body = JSON.parse(options.body);
                return fakeResponse({ data: { task_id: 'project_img_2', idempotency_key: body.idempotency_key, route: { provider_id: 'p1', model: 'm1' } } });
            }
            if (url === '/api/project-library/proj-1/image-generation-tasks/project_img_2') {
                return fakeResponse({ data: { status: 'succeeded', result: {} } });
            }
            throw new Error('unexpected url ' + url);
        };
        const settled = globalFunction(ctx, 'retryFailedGeneration')('img-2');
        await tick();
        // 提交已发出：POST 带新幂等键。
        const postEntry = harness.fetchLog.find(f => f.url === '/api/project-library/proj-1/image-generation-tasks' && f.options.method === 'POST');
        assert.ok(postEntry, '重新生成发了 POST');
        const body = JSON.parse(postEntry.options.body);
        assert.ok(body.idempotency_key.startsWith('regen-'), '新幂等键以 regen- 开头（显式重试是新的尝试）');
        assert.notEqual(body.idempotency_key, 'k-old');
        assert.equal(body.result_node_id, 'img-2');
        assert.equal(body.prompt, 'p');
        // 此刻 pending 任务已进节点、轮询在等 2000ms 受控定时器。
        assert.equal(node.pendingTasks.length, 1);
        assert.equal(node.pendingTasks[0].taskId, 'project_img_2');
        assert.equal(node.pendingTasks[0].idempotencyKey, body.idempotency_key);
        // 放行轮询（2000ms）→ 成功终态；retryFailedGeneration 在轮询结束后才 settle。
        harness.advanceTimers();
        await tick();
        await tick();
        await settled;
        assert.equal(node.pendingTasks, undefined, '任务完成后删除 pendingTasks');
        assert.equal(node.running, false);
        // 旧的失败提交标记已被清除（resumeSmartGenerationSubmission 成功后 clear）。
        assert.equal(node.generationSubmissions, undefined, '提交成功后 generationSubmissions 清空');
        // 防连点：重试在途时第二次调用直接返回。
        node.failedGenerationTasks = [{ taskId: 'project_img_9', error: 'boom' }];
        node._retryInFlight = true;
        harness.fetchLog.length = 0;
        await globalFunction(ctx, 'retryFailedGeneration')('img-2');
        assert.equal(harness.fetchLog.length, 0, '_retryInFlight 时不重复提交');
    }
});

test('pollSmartCanvasTask: terminal state transitions (cancelled/failed+recover/failed-bare/timeout/succeeded)', async () => {
    const cases = [
        {
            name: 'cancelled（用户主动停止）→ generationCancelled + providerFailure + cancelledByUser=true（stop≠error 中性终态标记）',
            task: { status: 'cancelled', error: '任务已取消', cancelled_by_user: true, result: {} },
            expect: (e) => e.generationCancelled === true && e.providerFailure === true && e.cancelledByUser === true && e.message === '任务已取消',
        },
        {
            name: 'cancelled（服务端 shutdown，无用户标记）→ cancelledByUser=false（仍按服务商失败终态，不能当网络错误重试）',
            task: { status: 'cancelled', error: '应用关闭，生成任务已明确终止', result: {} },
            expect: (e) => e.generationCancelled === true && e.providerFailure === true && e.cancelledByUser === false && e.message === '应用关闭，生成任务已明确终止',
        },
        {
            name: 'failed + upstream_task_id → ImageTaskRecoverSignal（失败取回信息）',
            task: { status: 'failed', upstream_task_id: 'up-1', provider_id: 'p1', kind: 'image', error: 'gen failed', result: {} },
            expect: (e) => e.imageTaskRecover === true && e.recoverTaskId === 'up-1' && e.taskId === 'project_img_X' && e.providerId === 'p1' && e.providerFailure === true && e.message === 'gen failed',
            expectFailLog: { taskId: 'project_img_X', error: 'gen failed' },
        },
        {
            name: 'failed 无 upstream → 普通 Error + providerFailure，且 failSmartPendingHistoryLog 落账',
            task: { status: 'failed', error: 'gen failed', result: {} },
            expect: (e) => e.imageTaskRecover === undefined && e.providerFailure === true && e.message === 'gen failed',
            expectFailLog: { taskId: 'project_img_X', error: 'gen failed' },
        },
        {
            name: 'timeout → generationTimedOut + providerFailure（#41 任务级硬超时是终态）',
            task: { status: 'timeout', error: 'timed out', result: {} },
            expect: (e) => e.generationTimedOut === true && e.providerFailure === true && e.message === 'timed out',
            expectFailLog: { taskId: 'project_img_X', error: 'timed out' },
        },
    ];
    for (const c of cases) {
        test(c.name, async () => {
            const { ctx, harness } = loadGenerationTasksModule();
            harness.fetchResponder = (url) => {
                assert.equal(url, '/api/project-library/proj-1/image-generation-tasks/project_img_X');
                return fakeResponse({ data: c.task });
            };
            const poll = globalFunction(ctx, 'pollSmartCanvasTask');
            const p = poll('project_img_X');
            const settled = assert.rejects(p, (e) => c.expect(e));
            harness.advanceTimers();  // 放行 2000ms 轮询间隔
            await tick();
            await tick();
            await settled;
            assert.equal(harness.fetchLog.length, 1, '终态只查一次');
            if (c.expectFailLog) {
                assert.equal(harness.failHistoryCalls.length, 1);
                assert.deepEqual(harness.failHistoryCalls[0], c.expectFailLog);
            } else {
                assert.equal(harness.failHistoryCalls.length, 0, 'cancelled 不落 fail 账');
            }
            assert.equal(evaluate(ctx, 'activeSmartTaskPolls.size'), 0, 'settled 后轮询去重 Map 清空');
        });
    }
    // succeeded：解析到 result，absorb 无 history_recorded 时是 no-op。
    test('succeeded → 返回 result，轮询 Map 清空', async () => {
        const { ctx, harness } = loadGenerationTasksModule();
        harness.fetchResponder = () => fakeResponse({ data: { status: 'succeeded', result: {} } });
        const poll = globalFunction(ctx, 'pollSmartCanvasTask');
        const p = poll('project_img_X');
        harness.advanceTimers();
        await tick();
        await tick();
        const result = await p;  // succeeded 永不 reject，直接 await
        assert.deepEqual(result, {});
        assert.equal(evaluate(ctx, 'activeSmartTaskPolls.size'), 0);
    });
});

// #237 停止语义精修（stop≠error、即时响应、partial 成品保留）：
// 用户主动停止后，轮询拿到 cancelled 终态快照（final_node + 含 partial outputs 的
// 取消历史 + cancelled_by_user 标记）——resumeSmartPendingNode 必须走中性终态：
// 不走服务商失败「失败/重试」UI（failedGenerationTasks）、不落 fail 账、
// partial 成品经 final_node 保留在节点上、pending 全部清理。
test('resumeSmartPendingNode: 用户主动 stop 是中性终态（stop≠error）——无失败 UI、无 fail 账、partial 成品保留', async () => {
    const node = {
        id: 'img-cancel-1',
        type: 'smart-image',
        running: true,
        pendingTasks: [{ taskId: 'project_img_cancel_1', count: 2 }],
        pending: 2,
        images: [],
        w: 1024,
        h: 1024,
    };
    const nodes = [node];
    const partialItem = { url: '/api/project-library/proj-1/media/asset-1', kind: 'image', name: 'partial-1.png' };
    const cancelledRecord = {
        id: 'generation-cancel-1',
        task_id: 'project_img_cancel_1',
        status: 'cancelled',
        error: '任务已取消',
        result_node_id: 'img-cancel-1',
        outputs: [partialItem],
    };
    const { ctx, harness } = loadGenerationTasksModule({ nodes });
    harness.fetchResponder = (url) => {
        assert.equal(url, '/api/project-library/proj-1/image-generation-tasks/project_img_cancel_1');
        return fakeResponse({ data: {
            status: 'cancelled',
            error: '任务已取消',
            cancelled_by_user: true,
            result: {
                history_recorded: true,
                history_record: cancelledRecord,
                project_id: 'proj-1',
                project_revision: 8,
                registered_revision: 7,
                result_node_id: 'img-cancel-1',
                final_node: { id: 'img-cancel-1', images: [partialItem], pendingTasks: [], pending: 0, running: false },
            },
        }});
    };
    const resume = globalFunction(ctx, 'resumeSmartPendingNode');
    const p = resume(node);
    harness.advanceTimers();  // 放行 2000ms 轮询间隔
    await p;
    await tick();

    assert.equal(node.pendingTasks, undefined, '该任务已从 pendingTasks 移除（节点不再「生成中」）');
    assert.equal(node.pending, 0);
    assert.equal(node.running, false);
    assert.equal(node.failedGenerationTasks, undefined, 'stop≠error：用户主动停止不登记「失败/重试」UI');
    assert.equal(harness.failHistoryCalls.length, 0, 'cancelled 不落 fail 账');
    assert.deepEqual(node.images, [partialItem], 'partial 成品经 final_node 写回保留');
    const history = fromContext(evaluate(ctx, 'canvas.history'));
    const record = history.find(r => String(r?.task_id || '') === 'project_img_cancel_1');
    assert.ok(record, '取消终态记录已进画布历史');
    assert.equal(record.status, 'cancelled');
    assert.equal(record.error, '任务已取消', '取消文案中性（interrupted vs error）');
    assert.deepEqual(record.outputs, [partialItem], 'partial 成品不被覆盖清除');
});

test('resumeSmartPendingNode: 服务端 shutdown 取消（无用户标记）仍走服务商失败终态', async () => {
    const node = {
        id: 'img-cancel-2',
        type: 'smart-image',
        running: true,
        pendingTasks: [{ taskId: 'project_img_cancel_2', count: 1 }],
        pending: 1,
        images: [],
    };
    const nodes = [node];
    const { ctx, harness } = loadGenerationTasksModule({ nodes });
    harness.fetchResponder = () => fakeResponse({ data: {
        status: 'cancelled',
        error: '应用关闭，生成任务已明确终止',
        result: { history_recorded: false },
    }});
    const resume = globalFunction(ctx, 'resumeSmartPendingNode');
    const p = resume(node);
    harness.advanceTimers();
    await p;
    await tick();
    // 非用户停止：保留旧语义——登记 failedGenerationTasks 让节点出现「重试」入口，
    // pending 仍被清理（不能永远「生成中」）。
    assert.ok(Array.isArray(node.failedGenerationTasks) && node.failedGenerationTasks.length === 1,
        'shutdown 取消仍登记失败任务（可重试入口）');
    assert.equal(node.failedGenerationTasks[0].taskId, 'project_img_cancel_2');
    assert.equal(node.pendingTasks, undefined);
    assert.equal(node.running, false);
});
