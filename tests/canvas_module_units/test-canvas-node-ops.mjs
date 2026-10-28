// #199 轨道 4：canvas-node-ops.js 行为单元测试（canvas-connections.js / canvas-generation-tasks.js
// 之后的下一个模块；耦合报告：60 处 / 27 独立名 / 自由 17 处 / 14 名 / 可注入 11）。
//
// canvas-node-ops.js 是画布节点操作模块（#160 P2c 从 smart-canvas.js 抽离）：节点创建
// （image/prompt/conversation/agent/local-edit/loop/智能分组）、deleteNode（#147 分组解散委托、
// #188 历史候选池降级、#74/#146 延迟归档）、clearNodeMediaBeforeDelete、deleteNodeFromButton
// （#65 生成中禁删）、renameSmartNodeImage、createNodeFromMenu、rAF 交互层刷新调度。
// 本文件只含函数定义与少量模块级 let/const，加载期不触碰外部名。
//
// 依赖面实测（口径=独立外部自由名，与耦合报告一致；与 #203 canvas-connections 同方法）：
// **21 个外部自由名**，其中：
//   - 7 个模块由**加载真实模块**满足（不桩；harness 按 HTML_SCRIPT_ORDER 排序加载）：
//       canvas-node-types.js → NODE_TYPE_IMAGE/PROMPT/CONVERSATION/AGENT/LOCAL_EDIT/LOOP/GROUP
//       canvas-core.js       → canvas(let) / canvasCore(Proxy 接口) / tr / toast / uid /
//                              selectedNode / nodeScale / isSmartGroupNode / isSmartImageNode /
//                              MEDIA_GROUP_DEFAULT_SCALE
//       canvas-state.js      → canvasState
//       canvas-layout-utils.js → SMART_GROUP_DEFAULT_WIDTH / SMART_GROUP_DEFAULT_HEIGHT
//       canvas-undo.js       → pushUndo（真实深快照撤销栈：deleteNode 的 undo 断言直接读 undoStack）
//       canvas-media-history.js → isHistoryGroupNode / demoteHistoryGroupNode / historyGroupForNode /
//                              scheduleMediaArchive（#217 真实实现：只登记 pendingMediaArchives、
//                              无计时器——本测试直接断言 harness.timers 为空）
//       canvas-agent-tui.js  → isSmartAgentNode / cleanupAgentTuiState（真实 agentTuiState Map 清理）
//   - 20 个手写桩（全部来自不可独立加载的宿主脚本 smart-canvas.js / canvas-connections.js，
//     按真实契约最小化；ADR 0020 判据「桩要开始重新实现产品逻辑就停」：0 个复制产品逻辑）：
//       零逻辑记录器 9 个：render / scheduleSave / attachRunMeta / arrangeSmartGroupMembers /
//       inheritNodeMetaFromImage / ungroupNode / closeCreateMenu / refreshConnectionLayer /
//       renderMinimap（前两个替换真实 canvasCore 成员为调用记录器，与 canvas-connections 测试同法）
//       数据驱动 8 个（测试显式喂数据，桩零逻辑）：imageLayout / resolveChatProviderId /
//       resolveChatModel / smartNodeInFlight / openAssetNameDialog / viewportCenter /
//       addNodeToSmartGroup / connectInputNode（connectInputNode 属 canvas-connections.js——
//       不加载整模块，因为那会额外引入它自己的 11 个自由名桩；连线判定行为已由
//       test-canvas-connections.mjs 覆盖）
//       单行契约镜像 3 个：mediaNodeDefaultScale（smart-canvas.js:1079-1082 逐字，MEDIA_NODE_DEFAULT_SCALE=2
//       取 smart-canvas.js:1030 的值）、nodeRect（smart-canvas.js:1772-1775 逐字，nodeScale 用真实
//       canvas-core.js:88）、imageNameLabel（smart-canvas.js:7207-7209 测试触达分支；fileNameFromUrl
//       回退分支不在测试路径——测试图片都带 name）
//       状态 1 个：interactionLayerRaf（smart-canvas.js 的 let；测试上下文无该词法声明，初始化为 0，
//       非函数）
//   - 浏览器全局 document/localStorage/requestAnimationFrame/可控定时器由 harness.mjs 提供；
//     document.getElementById('toast') 显式注册元素（供真实 canvas-core toast 运行）。
//
// 防的回归（全部断言可观察输出/副作用，不是「不抛错就算过」）：
//   - 节点创建：真实 uid 前缀/真实 NODE_TYPE_* 常量/真实 MEDIA_GROUP_DEFAULT_SCALE 与
//     SMART_GROUP_DEFAULT_WIDTH/HEIGHT、images 浅拷贝隔离、options 透传（title String 化、
//     runMeta、historyId String 化、needsRegeneration Boolean、generationState）、
//     select:false 不抢选中、skipUndo 不压栈（真实 undoStack 长度）；
//   - deleteNode（#147/#188/#74/#146）：分组节点委托 ungroupNode 且不重复 pushUndo；
//     关联边过滤（from/to 两端）、inputNodeIds/分组成员 items 清理、selectedId/selectedIds/
//     selectedImage 复位、canvas.nodes 同引用同步、真实 pushUndo 深快照（删前状态可读回）、
//     历史候选池经真实 demoteHistoryGroupNode 解绑降级且池内图片全保留、agent 节点经真实
//     cleanupAgentTuiState 清 TUI 状态（含 socket.close）、#217 归档只登记
//     pendingMediaArchives 且**零计时器**（旧实现 setTimeout(ARCHIVE_DELAY_MS) 比保存防抖早，
//     归档 409 被吞——本断言是单测侧的直接证据）、canvas=null 安全；
//   - clearNodeMediaBeforeDelete：仅图片节点、清图后历史池经真实 historyGroupForNode 找到并
//     移除（含其连线）、标题/尺寸复位、清出的图片进 pendingMediaArchives；
//   - deleteNodeFromButton（#65）：生成中节点禁止删除（toast + 零副作用）；
//   - createLocalEditNodeFromImage：节点缺失/无素材 toast 且零副作用、正常路径位置公式
//     （源节点右缘 +140、y 下限 40）、角色 original、connectInputNode 调用与 toast 分支；
//   - createNodeFromMenu：七种类型的偏移矩阵、closeCreateMenu、createMenuGroupId 读-还原-清零、
//     分组入组触发 arrangeSmartGroupMembers(skipUndo)、createMenuPoint=null 回退 viewportCenter；
//   - scheduleInteractionLayerRefresh：rAF 合并、flush 后双刷新、token 清零可重排。
//
// 判为**本轮单测不覆盖**的行为（不是「永远绿的假断言」，是预算/边界外的真实路径）：
//   - moveNodeElementsDuringDrag / updateNodeElementDuringResize：拖拽/缩放的 DOM 样式同步。
//     覆盖它们需再引入 6 个桩（positionComposerForNode / mediaGroupGridHeight /
//     applyThumbDisplaySizeToElement / smartGroupImageRefs / smartGroupCompactMembers / CSS.escape），
//     总桩数将突破 26 条既有上限（canvas-generation-tasks.js 26），且核心输入 imageLayout 本身
//     是数据驱动桩——断言大半会落在「自己桩的输出」上，自我断言风险高。其核心布局数学
//     （getBatchThumbGridSize/BATCH_THUMB）属 canvas-layout-utils.js，由 test-canvas-layout-utils.mjs
//     覆盖；真实时序由浏览器 runner 兜底。
//   - 节点复制/粘贴/Alt 拖拽复制（cloneSmartNode/copySelectedNodes/pasteNodes/duplicateForAltDrag）：
//     属剪贴板操作，仍在 smart-canvas.js（本模块头部注释明示「留主脚本」）——不在本模块测试范围，
//     其身份与去重行为由浏览器 runner 兜底。
//   - createLocalEditNodeFromImage 里 connectInputNode 的连线判定内部（角色路由/去重）：
//     属 canvas-connections.js 行为，已由 test-canvas-connections.mjs 的 connectInputNode 用例覆盖；
//     本文件只断言 node-ops 侧的调用参数与 toast 分支。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

// 加载真实底层模块 + 被测模块（按 HTML 相对顺序，harness 会排序）：
// canvas-node-types.js → canvas-core.js → canvas-state.js → canvas-layout-utils.js →
// canvas-undo.js → canvas-media-history.js → canvas-agent-tui.js → canvas-node-ops.js。
// 外部名在 loadModules 之前用全局属性桩好即可（canvasCore Proxy 是惰性解析，运行期才按名
// 解析）；3 个契约镜像必须 loadModules 之后用 evaluate 定义（要引用 context 内真实常量/函数）。
function loadNodeOpsModule({ nodes = [], connections = [] } = {}) {
    const { ctx, harness } = createCanvasContext();
    const calls = [];
    // --- 数据驱动注册表：测试显式喂数据，桩零逻辑（防「第二套假核心」） ---
    harness.layout = { width: 180, height: 110 };      // imageLayout 返回值（canvasCore 成员）
    harness.providerId = 'comfly';                     // resolveChatProviderId 返回值
    harness.model = 'gpt-5.4';                         // resolveChatModel 返回值
    harness.inFlight = new Set();                      // smartNodeInFlight：哪些节点生成中
    harness.assetName = undefined;                     // openAssetNameDialog 返回值
    harness.assetDialogCalls = [];                     // openAssetNameDialog 入参记录
    harness.viewportPoint = { x: 0, y: 0 };            // viewportCenter 返回值
    harness.connectResult = true;                      // connectInputNode 返回值
    harness.connectCalls = [];                         // connectInputNode 调用记录
    harness.addNodeToGroupResult = false;              // addNodeToSmartGroup 返回值
    harness.addNodeToGroupCalls = [];                  // addNodeToSmartGroup 调用记录
    harness.runMetaCalls = [];                         // canvasCore.attachRunMeta 调用记录
    harness.inheritCalls = [];                         // inheritNodeMetaFromImage 调用记录
    harness.ungroupCalls = [];                         // ungroupNode 调用记录
    harness.closeMenuCalls = 0;                         // closeCreateMenu 调用计数
    harness.arrangeCalls = [];                         // canvasCore.arrangeSmartGroupMembers 调用记录
    harness.refreshLayerCalls = 0;                      // refreshConnectionLayer 调用计数
    harness.minimapCalls = 0;                           // renderMinimap 调用计数

    harness.element('toast');   // 真实 canvas-core toast（canvas-core.js:154）需要 #toast 元素

    // --- canvasCore 接口成员（真实 canvasCore Proxy 运行期按名解析到这些全局属性） ---
    ctx.render = () => calls.push('render');
    ctx.scheduleSave = () => calls.push('scheduleSave');
    ctx.imageLayout = () => harness.layout;
    ctx.attachRunMeta = (node, meta) => { harness.runMetaCalls.push({ node: node?.id || '', meta }); };
    ctx.resolveChatProviderId = () => harness.providerId;
    ctx.resolveChatModel = () => harness.model;
    ctx.arrangeSmartGroupMembers = (group, options) => { harness.arrangeCalls.push({ group: group?.id ?? '', options }); };
    // --- 宿主自由名桩（smart-canvas.js / canvas-connections.js） ---
    ctx.inheritNodeMetaFromImage = (node) => { harness.inheritCalls.push(node?.id || ''); };
    ctx.ungroupNode = (id) => { harness.ungroupCalls.push(id); };
    ctx.smartNodeInFlight = (node) => harness.inFlight.has(node?.id);
    ctx.openAssetNameDialog = async (opts) => { harness.assetDialogCalls.push(opts); return harness.assetName; };
    ctx.viewportCenter = () => harness.viewportPoint;
    ctx.closeCreateMenu = () => { harness.closeMenuCalls++; };
    ctx.addNodeToSmartGroup = (group, node) => {
        harness.addNodeToGroupCalls.push({ group: group?.id ?? null, node: node?.id || '' });
        return harness.addNodeToGroupResult;
    };
    ctx.connectInputNode = (fromId, toId) => {
        harness.connectCalls.push([fromId, toId]);
        return harness.connectResult;
    };
    ctx.refreshConnectionLayer = () => { harness.refreshLayerCalls++; };
    ctx.renderMinimap = () => { harness.minimapCalls++; };
    // 状态：interactionLayerRaf（smart-canvas.js 的 let；测试上下文无词法声明，用全局属性顶替）
    ctx.interactionLayerRaf = 0;

    loadModules(ctx, [
        'canvas-node-types.js',
        'canvas-core.js',
        'canvas-state.js',
        'canvas-layout-utils.js',
        'canvas-placement.js',
        'canvas-undo.js',
        'canvas-media-history.js',
        'canvas-agent-tui.js',
        'canvas-node-ops.js',
    ]);

    // --- 3 个单行契约镜像（loadModules 之后定义：引用 context 内的真实常量/函数） ---
    // 契约镜像（smart-canvas.js:7207-7209 测试触达分支：name 存在 → trim；测试图片都带 name，
    // fileNameFromUrl 回退分支不在测试路径）。
    evaluate(ctx, 'imageNameLabel = (img, fallback = "image") => { const raw = String(img?.name || fallback || "image").trim(); return raw || "image"; }');
    // 契约镜像（smart-canvas.js:1079-1082 逐字；MEDIA_NODE_DEFAULT_SCALE=2 的值取自
    // smart-canvas.js:1030；MEDIA_GROUP_DEFAULT_SCALE 用真实 canvas-core.js:86 的 0.8）。
    evaluate(ctx, 'mediaNodeDefaultScale = (node) => { if((node?.images || []).length > 1 && !Number.isFinite(Number(node?.scale))) return MEDIA_GROUP_DEFAULT_SCALE; return Number.isFinite(Number(node?.scale)) && Number(node.scale) > 0 ? Number(node.scale) : 2; }');
    // 契约镜像（smart-canvas.js:1772-1775 逐字：x/y 取节点，宽/高取 imageLayout；
    // nodeScale 用真实 canvas-core.js:88）。
    evaluate(ctx, 'nodeRect = (node) => { const layout = imageLayout(node.images || [], nodeScale(node), node); return { x: node.x || 0, y: node.y || 0, width: layout.width, height: layout.height }; }');

    // 运行期状态：canvas 是 canvas-core.js 的词法 let 绑定（不是全局对象属性），必须用
    // evaluate 赋值；canvasState 是 window.canvasState 对象，宿主侧直接改属性即可。
    ctx.canvasState.nodes = nodes;
    ctx.__canvasPayload = { id: 'cv', nodes, connections };
    evaluate(ctx, 'canvas = __canvasPayload');
    return { ctx, harness, calls };
}

// ---------- 模块加载 ----------

test('loads canvas-node-ops.js standalone: all top-level functions exist, real modules in place', () => {
    const { ctx } = loadNodeOpsModule();
    // 顶层 function 都在（模块加载成功且无 SyntaxError 的前置证据）。
    for (const name of [
        'createImageNodeAt', 'createNode', 'createPromptNode', 'createConversationNode',
        'createAgentNode', 'createLocalEditNode', 'createLocalEditNodeFromImage',
        'createLoopNode', 'createSmartGroupNode', 'moveNodeElementsDuringDrag',
        'updateNodeElementDuringResize', 'deleteNode', 'clearNodeMediaBeforeDelete',
        'deleteNodeFromButton', 'renameSmartNodeImage', 'createNodeFromMenu',
        'scheduleInteractionLayerRefresh', 'addCreatedNodeToMenuGroup',
    ]) globalFunction(ctx, name);
    // 真实底层模块就位。
    assert.equal(evaluate(ctx, 'NODE_TYPE_IMAGE'), 'smart-image', 'NODE_TYPE_IMAGE 来自真实 canvas-node-types.js');
    assert.equal(evaluate(ctx, 'NODE_TYPE_LOCAL_EDIT'), 'local-edit', 'NODE_TYPE_LOCAL_EDIT 来自真实 canvas-node-types.js');
    assert.equal(evaluate(ctx, 'MEDIA_GROUP_DEFAULT_SCALE'), 0.8, '真实 canvas-core.js 常量');
    assert.equal(evaluate(ctx, 'SMART_GROUP_DEFAULT_WIDTH'), 340, '真实 canvas-layout-utils.js 常量');
    assert.equal(evaluate(ctx, 'SMART_GROUP_DEFAULT_HEIGHT'), 286, '真实 canvas-layout-utils.js 常量');
    assert.equal(evaluate(ctx, 'typeof pushUndo'), 'function', 'pushUndo 来自真实 canvas-undo.js');
    assert.equal(evaluate(ctx, 'undoStack.length'), 0, '真实撤销栈初始为空');
    assert.equal(evaluate(ctx, 'typeof scheduleMediaArchive'), 'function', 'scheduleMediaArchive 来自真实 canvas-media-history.js');
    assert.equal(evaluate(ctx, 'typeof isHistoryGroupNode'), 'function', 'isHistoryGroupNode 来自真实 canvas-media-history.js');
    assert.equal(evaluate(ctx, 'pendingMediaArchives.length'), 0, '真实待归档队列初始为空');
    assert.equal(evaluate(ctx, 'typeof cleanupAgentTuiState'), 'function', 'cleanupAgentTuiState 来自真实 canvas-agent-tui.js');
    assert.equal(evaluate(ctx, 'canvas && canvas.id'), 'cv', 'canvas let 绑定已被 evaluate 赋值');
    assert.equal(evaluate(ctx, 'canvasState.nodes.length'), 0, 'canvasState 来自真实 canvas-state.js');
    // 真实 canvasCore Proxy 惰性解析到本测试提供的全局成员。
    assert.equal(evaluate(ctx, 'typeof canvasCore.imageLayout'), 'function', 'canvasCore.imageLayout 解析成功');
    assert.equal(evaluate(ctx, 'typeof canvasCore.nodeRect'), 'function', 'canvasCore.nodeRect 解析成功');
    assert.equal(evaluate(ctx, 'typeof canvasCore.mediaNodeDefaultScale'), 'function', 'canvasCore.mediaNodeDefaultScale 解析成功');
    assert.equal(evaluate(ctx, 'canvasCore.render === render'), true, 'canvasCore.render 是记录器');
});

// ---------- 节点创建 ----------

test('createNode: default shape — real uid prefix/type/scale constants, title branches, shallow-copied images', () => {
    const { ctx } = loadNodeOpsModule();
    const create = globalFunction(ctx, 'createNode');

    const single = fromContext(create(10, 20, [{ url: 'u1', w: 100, h: 50 }]));
    assert.ok(single.id.startsWith('smart_'), '真实 uid 前缀 smart_');
    assert.equal(single.type, 'smart-image', 'NODE_TYPE_IMAGE 真实值');
    assert.equal(single.x, 10);
    assert.equal(single.y, 20);
    assert.equal(single.title, 'Image', '单图标题 Image');
    assert.equal(single.scale, 2, '单图缺省 scale = MEDIA_NODE_DEFAULT_SCALE（smart-canvas.js:1030 的 2）');
    assert.equal(typeof single.created_at, 'number');
    assert.deepEqual(single.images, [{ url: 'u1', w: 100, h: 50 }]);

    const group = fromContext(create(30, 40, [{ url: 'a' }, { url: 'b' }]));
    assert.equal(group.title, 'Group', '多图标题 Group');
    assert.equal(group.scale, 0.8, '多图 scale = 真实 MEDIA_GROUP_DEFAULT_SCALE（canvas-core.js:86）');

    const empty = fromContext(create(0, 0));
    assert.equal(empty.title, 'smart.createImportNode', '无图标题走真实 tr（无 StudioI18n 返回 key）');
    assert.equal(empty.scale, 2);
    assert.deepEqual(empty.images, []);

    // images 浅拷贝隔离：改原数组/原对象不影响节点。
    const source = [{ url: 'u1', w: 100 }];
    create(50, 60, source);
    source[0].url = 'changed';
    source.splice(1, 0, { url: 'extra' });
    const n = fromContext(evaluate(ctx, 'canvasState.nodes.find(x => x.x === 50)'));
    assert.equal(n.images.length, 1, '原数组增删不影响节点');
    assert.equal(n.images[0].url, 'u1', '原对象改字段不影响节点（浅拷贝）');
});

test('createNode: undo push / skipUndo / select:false / render+scheduleSave 序列', () => {
    const { ctx, calls } = loadNodeOpsModule();
    const create = globalFunction(ctx, 'createNode');

    create(0, 0);
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, '默认压一次真实撤销栈');
    assert.deepEqual(calls, ['render', 'scheduleSave'], '副作用序列（pushUndo 是真实函数，不在记录器里）');

    create(0, 0, [], { skipUndo: true });
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, 'skipUndo 不压栈');
    assert.equal(calls.length, 4, '但 render/scheduleSave 仍发生');

    evaluate(ctx, 'canvasState.selectedId = "preset"');
    create(0, 0, [], { select: false });
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), 'preset', 'select:false 不抢选中');
});

test('createNode: options 透传 — title String 化 / runMeta / historyId String 化 / needsRegeneration Boolean / generationState', () => {
    const { ctx, harness } = loadNodeOpsModule();
    const create = globalFunction(ctx, 'createNode');

    const node = fromContext(create(0, 0, [], {
        title: 42,
        runMeta: { prompt: 'p', inputRefs: [{ url: 'u', kind: 'image' }] },
        historyId: 123,
        needsRegeneration: 0,
        generationState: 'running',
    }));
    assert.equal(node.title, '42', 'title 经 String() 转换');
    assert.equal(node.historyId, '123', 'historyId 经 String() 转换');
    assert.equal(node.needsRegeneration, false, 'needsRegeneration 经 Boolean() 转换（0 → false）');
    assert.equal(node.generationState, 'running');
    assert.deepEqual(harness.runMetaCalls, [
        { node: node.id, meta: { prompt: 'p', inputRefs: [{ url: 'u', kind: 'image' }] } },
    ], 'runMeta 原样交给 attachRunMeta');
    assert.deepEqual(harness.inheritCalls, [node.id], 'inheritNodeMetaFromImage 以节点为参调用');

    const noMeta = fromContext(create(0, 0, [], { needsRegeneration: null }));
    assert.equal('needsRegeneration' in noMeta, false, 'needsRegeneration=null 不设置字段（!= null 门）');
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), noMeta.id, '默认选中新建节点');
});

test('createImageNodeAt: 按布局尺寸把节点中心对准光标点（数据驱动 layout 180x110）', () => {
    const { ctx } = loadNodeOpsModule();
    const createAt = globalFunction(ctx, 'createImageNodeAt');

    const node = fromContext(createAt({ x: 1000, y: 500 }, [{ url: 'u' }]));
    assert.equal(node.x, 1000 - Math.round(180 / 2), 'x = 光标 x - 宽/2');
    assert.equal(node.y, 500 - Math.round(110 / 2), 'y = 光标 y - 高/2');
    assert.equal(node.type, 'smart-image');

    const atOrigin = fromContext(createAt(null, []));
    assert.equal(atOrigin.x, -90, 'null 点 → (0,0) 中心对齐');
    assert.equal(atOrigin.y, -55);
});

test('createPromptNode: 形状 + 聊天 provider/model 经 canvasCore 接口 + source id String 化', () => {
    const { ctx } = loadNodeOpsModule();
    const createPrompt = globalFunction(ctx, 'createPromptNode');

    const node = fromContext(createPrompt(1, 2, { text: 'hello', sourceNodeId: 7, sourceMessageId: 'm1' }));
    assert.ok(node.id.startsWith('prompt_'), '真实 uid 前缀 prompt_');
    assert.equal(node.type, 'smart-prompt');
    assert.equal(node.w, 316);
    assert.equal(node.h, 240);
    assert.equal(node.title, 'Prompt');
    assert.equal(node.text, 'hello');
    assert.equal(node.promptSeparator, ';');
    assert.equal(node.promptSplitEnabled, false);
    assert.equal(node.llmEnabled, false);
    assert.equal(node.llmProvider, 'comfly', 'llmProvider 经 canvasCore.resolveChatProviderId');
    assert.equal(node.llmModel, 'gpt-5.4', 'llmModel 经 canvasCore.resolveChatModel');
    assert.equal(node.llmSystemEnabled, false);
    assert.equal(node.llmSystemPrompt, 'You are a helpful prompt assistant.');
    assert.equal(node.llmInstruction, '');
    assert.equal(node.source_node_id, '7', 'sourceNodeId String 化');
    assert.equal(node.source_message_id, 'm1');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, '压一次真实撤销栈');
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), node.id, '默认选中');
});

test('createConversationNode / createAgentNode: 形状与选中', () => {
    const { ctx } = loadNodeOpsModule();
    const conv = fromContext(globalFunction(ctx, 'createConversationNode')(10, 20));
    assert.ok(conv.id.startsWith('conversation_'));
    assert.equal(conv.type, 'smart-conversation');
    assert.equal(conv.w, 390);
    assert.equal(conv.h, 430);
    assert.equal(conv.title, 'Conversation');
    assert.deepEqual(conv.messages, []);
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), conv.id);

    const agent = fromContext(globalFunction(ctx, 'createAgentNode')(30, 40));
    assert.ok(agent.id.startsWith('agent_'));
    assert.equal(agent.type, 'smart-agent');
    assert.equal(agent.w, 382);
    assert.equal(agent.h, 360);
    assert.equal(agent.title, 'Agent');
    assert.equal(agent.agentGoal, '');
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), agent.id);
});

test('createLocalEditNode: 模块级尺寸常量 400x520 与默认角色结构', () => {
    const { ctx } = loadNodeOpsModule();
    const node = fromContext(globalFunction(ctx, 'createLocalEditNode')(0, 0));
    assert.ok(node.id.startsWith('local-edit_'));
    assert.equal(node.type, 'local-edit', 'NODE_TYPE_LOCAL_EDIT 真实值');
    assert.equal(node.w, 400, 'LOCAL_EDIT_NODE_W');
    assert.equal(node.h, 520, 'LOCAL_EDIT_NODE_H');
    assert.equal(node.title, '');
    assert.equal(node.mode, 'modify');
    assert.equal(node.original_asset_id, '');
    assert.deepEqual(node.reference_asset_ids, []);
    assert.equal(node.prompt, '');
    assert.deepEqual(node.localEditInputRoles, { originalNode: '', referenceNodes: [], promptNode: '', promptNodes: [] });
    assert.equal(node.localEditActiveRole, 'original');
    assert.equal(node.aspect_ratio, '1:1');
    assert.equal(node.generation_size, '1024x1024');
    assert.equal(node.generation_resolution, '1k');
    assert.equal(node.selectionType, 'polygon');
    assert.deepEqual(node.selection, []);
    assert.equal(node.feather_radius, 8);
    assert.deepEqual(node.images, []);
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), node.id);
});

test('createLoopNode: 形状（count/mode/imageBatchSize 默认）', () => {
    const { ctx } = loadNodeOpsModule();
    const node = fromContext(globalFunction(ctx, 'createLoopNode')(0, 0));
    assert.ok(node.id.startsWith('loop_'));
    assert.equal(node.type, 'smart-loop');
    assert.equal(node.w, 340);
    assert.equal(node.h, 168);
    assert.equal(node.title, 'Loop');
    assert.equal(node.count, 1);
    assert.equal(node.mode, 'serial');
    assert.equal(node.showPrompt, false);
    assert.equal(node.imageInput, false);
    assert.equal(node.loopStart, 1);
    assert.equal(node.imageBatchSize, 1);
    assert.equal(node.variablePrompt, '');
});

test('createSmartGroupNode: 真实 SMART_GROUP_DEFAULT_WIDTH/HEIGHT（canvas-layout-utils.js）', () => {
    const { ctx } = loadNodeOpsModule();
    const node = fromContext(globalFunction(ctx, 'createSmartGroupNode')(0, 0));
    assert.ok(node.id.startsWith('group_'));
    assert.equal(node.type, 'smart-group');
    assert.equal(node.w, evaluate(ctx, 'SMART_GROUP_DEFAULT_WIDTH'), '宽 = 真实常量 340');
    assert.equal(node.h, evaluate(ctx, 'SMART_GROUP_DEFAULT_HEIGHT'), '高 = 真实常量 286');
    assert.equal(node.title, '智能分组');
    assert.deepEqual(node.items, []);
});

// ---------- 删除节点 ----------

test('deleteNode: 节点移除 + 关联边两端过滤 + inputNodeIds/分组成员清理 + 选中复位 + canvas 同步 + 真实 undo 深快照', () => {
    const nodes = [
        { id: 'N', images: [{ url: 'u1' }, { url: 'u2' }] },
        { id: 'P', inputNodeIds: ['N', 'X'] },
        { id: 'X' },
        { id: 'G', type: 'smart-group', items: ['N', 'X'] },
    ];
    const { ctx, harness, calls } = loadNodeOpsModule({
        nodes,
        connections: [
            { from: 'N', to: 'P' },
            { from: 'X', to: 'N' },
            { from: 'P', to: 'X' },
            { from: 'X', to: 'Q' },
        ],
    });
    ctx.canvasState.selectedId = 'N';
    ctx.canvasState.selectedIds = ['N', 'X'];
    ctx.canvasState.selectedImage = { nodeId: 'N', index: 0 };
    const deleteNode = globalFunction(ctx, 'deleteNode');

    deleteNode('N');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes.map(n => n.id)')), ['P', 'X', 'G'], 'N 被移除');
    assert.equal(evaluate(ctx, 'canvas.nodes === canvasState.nodes'), true, 'canvas.nodes 指向同一数组引用');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvas.connections')), [
        { from: 'P', to: 'X' },
        { from: 'X', to: 'Q' },
    ], 'N 作端点的两条边被过滤，其余保留');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes.find(n => n.id === "P").inputNodeIds')), ['X'], 'P.inputNodeIds 去掉 N');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes.find(n => n.id === "G").items')), ['X'], '分组成员 items 去掉 N');
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), '', '删除的是选中节点 → 清空 selectedId');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.selectedIds')), ['X'], 'selectedIds 过滤 N');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.selectedImage')), { nodeId: '', index: -1 }, 'selectedImage 指向被删节点 → 复位');
    assert.deepEqual(calls, ['render', 'scheduleSave'], '副作用序列');
    // 真实 pushUndo 的深快照：删前状态可读回（N 带 2 张图、4 条边都在快照里）。
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, '压一次真实撤销栈');
    const snap = fromContext(evaluate(ctx, 'undoStack[0]'));
    assert.deepEqual(snap.nodes.map((n) => n.id), ['N', 'P', 'X', 'G'], '快照含删前全部节点');
    assert.equal(snap.nodes[0].images.length, 2, '快照里 N 的图片完整');
    assert.equal(snap.connections.length, 4, '快照含删前全部边');

    // 无图节点：再次删除不留归档条目（真实 scheduleMediaArchive 的 !images.length 早退）。
    deleteNode('X');
    assert.deepEqual(fromContext(evaluate(ctx, 'pendingMediaArchives.map(e => e.images.map(i => i.url))')),
        [['u1', 'u2']], '只有 N 的图片进待归档队列');
    assert.equal(harness.timers.length, 0, '#217：归档只登记、零计时器（旧 setTimeout(ARCHIVE_DELAY_MS) 实现必红）');
    assert.equal(evaluate(ctx, 'undoStack.length'), 2, '无图节点也压 undo');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvas.connections')), [], 'X 作端点的两条边（P→X、X→Q）都被过滤');
});

test('deleteNode: 分组节点委托 ungroupNode 且不重复 pushUndo/render（#147 解散=不丢数据）', () => {
    const { ctx, harness, calls } = loadNodeOpsModule({
        nodes: [
            { id: 'G', type: 'smart-group', items: ['A'] },
            { id: 'A' },
        ],
    });
    const deleteNode = globalFunction(ctx, 'deleteNode');

    deleteNode('G');
    assert.deepEqual(harness.ungroupCalls, ['G'], '委托 ungroupNode(G)');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes.map(n => n.id)')), ['G', 'A'], 'deleteNode 自身不改节点（解散由 ungroupNode 负责）');
    assert.equal(evaluate(ctx, 'undoStack.length'), 0, 'ungroupNode 内部已 pushUndo，这里不重复');
    assert.deepEqual(calls, [], '不 render/save（提前 return）');
});

test('deleteNode: 历史候选池经真实 demoteHistoryGroupNode 解绑降级，池内图片全保留（#188）', () => {
    const { ctx, harness } = loadNodeOpsModule({
        nodes: [
            { id: 'N', images: [{ url: 'u1' }] },
            { id: 'H', type: 'smart-image', isHistoryGroup: true, historyFor: 'N', title: 'Group', images: [{ url: 'h1' }, { url: 'h2' }] },
        ],
    });
    const deleteNode = globalFunction(ctx, 'deleteNode');

    deleteNode('N');
    const h = fromContext(evaluate(ctx, 'canvasState.nodes.find(n => n.id === "H")'));
    assert.ok(h, '候选池节点保留在画布上');
    assert.equal(h.isHistoryGroup, undefined, '解绑 isHistoryGroup');
    assert.equal(h.historyFor, undefined, '解绑 historyFor');
    assert.equal(h.title, 'Group', '真实 demote 的标题重置（2 图 → Group，titleTouched 缺失）');
    assert.deepEqual(h.images, [{ url: 'h1' }, { url: 'h2' }], '池内图片全部保留');
    assert.deepEqual(fromContext(evaluate(ctx, 'pendingMediaArchives.map(e => e.images.map(i => i.url))')),
        [['u1']], '只有被删节点的图进归档，池内图不归档');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1);
});

test('deleteNode: agent 节点经真实 cleanupAgentTuiState 清 TUI 状态（含 socket.close），非 agent 不清', () => {
    const { ctx } = loadNodeOpsModule({
        nodes: [
            { id: 'A', type: 'smart-agent' },
            { id: 'B', type: 'smart-agent' },
            { id: 'C' },
        ],
    });
    evaluate(ctx, 'agentTuiState.set("A", { socket: null })');
    ctx.__closeRecord = [];
    evaluate(ctx, 'agentTuiState.set("B", { socket: { close(){ __closeRecord.push("B"); } } })');
    evaluate(ctx, 'agentTuiState.set("C", { socket: null })');
    const deleteNode = globalFunction(ctx, 'deleteNode');

    deleteNode('A');
    deleteNode('B');
    deleteNode('C');
    assert.equal(evaluate(ctx, 'agentTuiState.has("A")'), false, 'agent A 的 TUI 状态被清');
    assert.equal(evaluate(ctx, 'agentTuiState.has("B")'), false, 'agent B 的 TUI 状态被清');
    assert.deepEqual(ctx.__closeRecord, ['B'], 'B 的 socket.close() 被真实 cleanup 调用');
    assert.equal(evaluate(ctx, 'agentTuiState.has("C")'), true, '非 agent 节点不触发 TUI 清理');
});

test('deleteNode: canvas=null 安全（真实 pushUndo 早退，归档与渲染仍发生）', () => {
    const { ctx, calls } = loadNodeOpsModule({
        nodes: [{ id: 'N', images: [{ url: 'u1' }] }],
    });
    evaluate(ctx, 'canvas = null');
    const deleteNode = globalFunction(ctx, 'deleteNode');

    deleteNode('N');
    assert.equal(evaluate(ctx, 'canvas'), null, 'canvas 保持 null');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes')), [], '节点仍被移除');
    assert.equal(evaluate(ctx, 'undoStack.length'), 0, '真实 pushUndo 的 if(!canvas) return → 不压栈');
    assert.deepEqual(fromContext(evaluate(ctx, 'pendingMediaArchives.map(e => e.images.map(i => i.url))')),
        [['u1']], '归档登记与 canvas 无关，照常');
    assert.deepEqual(calls, ['render', 'scheduleSave']);
});

test('clearNodeMediaBeforeDelete: 图片节点清图 + 历史池移除（含连线）+ 选中复位 + 清出图片进归档', () => {
    const { ctx, calls } = loadNodeOpsModule({
        nodes: [
            { id: 'N', type: 'smart-image', images: [{ url: 'i1' }, { url: 'i2' }], pending: 2, running: true, w: 300, h: 200, title: 'Image' },
            { id: 'H', type: 'smart-image', isHistoryGroup: true, historyFor: 'N', images: [{ url: 'h1' }] },
            { id: 'P' },
        ],
        connections: [
            { from: 'H', to: 'N', kind: 'history' },
            { from: 'N', to: 'P' },
            { from: 'P', to: 'N' },
        ],
    });
    ctx.canvasState.selectedId = 'P';
    ctx.canvasState.selectedIds = ['P'];
    ctx.canvasState.selectedImage = { nodeId: 'N', index: 0 };
    const clear = globalFunction(ctx, 'clearNodeMediaBeforeDelete');

    assert.equal(clear('N'), true, '有媒体 → true');
    const n = fromContext(evaluate(ctx, 'canvasState.nodes.find(x => x.id === "N")'));
    assert.deepEqual(n.images, [], 'images 清空');
    assert.equal(n.pending, 0);
    assert.equal(n.running, false);
    assert.equal(n.title, 'smart.createImportNode', '真实 tr 回退标题');
    assert.equal('w' in n, false, 'delete w');
    assert.equal('h' in n, false, 'delete h');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes.map(x => x.id)')), ['N', 'P'], '历史池 H 被移除，N 保留');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvas.connections')), [
        { from: 'N', to: 'P' },
        { from: 'P', to: 'N' },
    ], '历史池的边被过滤，N↔P 保留');
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), 'N', '清图后选中保持该节点');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.selectedIds')), [], 'selectedIds 清空');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.selectedImage')), { nodeId: '', index: -1 });
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, '真实 pushUndo');
    assert.deepEqual(fromContext(evaluate(ctx, 'pendingMediaArchives.map(e => e.images.map(i => i.url))')),
        [['i1', 'i2']], '清出的图片进归档队列');
    assert.deepEqual(calls, ['render', 'scheduleSave']);
});

test('clearNodeMediaBeforeDelete: 非图片节点/无媒体 → false 零副作用；仅 pending → true 但无归档条目', () => {
    const { ctx, calls } = loadNodeOpsModule({
        nodes: [
            { id: 'P', type: 'smart-prompt' },
            { id: 'I', type: 'smart-image', images: [] },
            { id: 'R', type: 'smart-image', images: [], pending: 1, title: 'Image' },
        ],
    });
    const clear = globalFunction(ctx, 'clearNodeMediaBeforeDelete');

    assert.equal(clear('P'), false, '非图片节点 → false');
    assert.equal(clear('I'), false, '无 images 且无 pending → false');
    assert.deepEqual(calls, [], '两种 false 都零副作用');
    assert.equal(evaluate(ctx, 'undoStack.length'), 0);

    assert.equal(clear('R'), true, '仅 pending 也算有媒体 → true');
    const r = fromContext(evaluate(ctx, 'canvasState.nodes.find(x => x.id === "R")'));
    assert.equal(r.pending, 0);
    assert.equal(r.running, false);
    assert.equal(evaluate(ctx, 'undoStack.length'), 1);
    assert.equal(evaluate(ctx, 'pendingMediaArchives.length'), 0, 'clearedImages 为空 → 真实 scheduleMediaArchive 早退，无归档条目');
});

test('deleteNodeFromButton: 生成中节点禁止删除（#65：toast + 零副作用）', () => {
    const { ctx, harness, calls } = loadNodeOpsModule({
        nodes: [{ id: 'N', images: [{ url: 'u1' }] }],
    });
    harness.inFlight.add('N');
    const del = globalFunction(ctx, 'deleteNodeFromButton');

    del('N');
    assert.equal(harness.element('toast').textContent, 'smart.nodeDeleteBusy', '真实 toast 文案（tr key）');
    assert.ok(harness.element('toast').classList.contains('show'), 'toast 已显示');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes.map(n => n.id)')), ['N'], '节点仍在');
    assert.equal(evaluate(ctx, 'undoStack.length'), 0, '不压 undo');
    assert.equal(evaluate(ctx, 'pendingMediaArchives.length'), 0, '不归档');
    assert.deepEqual(calls, [], '不 render/save');
});

test('deleteNodeFromButton: 空闲节点一步删除（#149 B5）；ghost id 走 deleteNode 仍压一次真实 undo', () => {
    const { ctx, harness } = loadNodeOpsModule({
        nodes: [{ id: 'N', images: [{ url: 'u1' }] }],
    });
    const del = globalFunction(ctx, 'deleteNodeFromButton');

    del('N');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes')), [], '节点被删');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1);
    assert.equal(harness.element('toast').textContent, '', '空闲删除无 toast');
    assert.ok(!harness.element('toast').classList.contains('show'));

    del('ghost');
    assert.equal(evaluate(ctx, 'undoStack.length'), 2, 'ghost id：deleteNode 照常 pushUndo/render（真实行为，非 bug 断言）');
    assert.deepEqual(fromContext(evaluate(ctx, 'pendingMediaArchives.map(e => e.images.map(i => i.url))')),
        [['u1']], 'ghost 无被删节点 → 不新增归档条目（队列里只有 N 的 u1）');
});

// ---------- 工具排「局部编辑」 ----------

test('createLocalEditNodeFromImage: 节点缺失/无素材/索引越界 → toast noExportableMedia 且零副作用', () => {
    const { ctx, harness, calls } = loadNodeOpsModule({ nodes: [] });
    const f = globalFunction(ctx, 'createLocalEditNodeFromImage');

    assert.equal(f('ghost'), undefined, '节点不存在 → undefined');
    assert.deepEqual(calls, []);
    assert.deepEqual(harness.connectCalls, []);
    assert.equal(harness.element('toast').textContent, '', '无 toast');

    evaluate(ctx, 'canvasState.nodes.push({ id: "N" })');
    f('N');
    assert.equal(harness.element('toast').textContent, 'smart.noExportableMedia', '无素材 toast（真实 tr key）');
    assert.equal(fromContext(evaluate(ctx, 'canvasState.nodes.length')), 1, '不创建节点');
    assert.deepEqual(harness.connectCalls, [], '不连线');
    assert.deepEqual(calls, [], '不 render/save');

    evaluate(ctx, 'canvasState.nodes[0].images = [{ url: "i0" }]');
    f('N', 5);
    assert.equal(fromContext(evaluate(ctx, 'canvasState.nodes.length')), 1, '索引越界 → 仍是 noExportableMedia，不创建节点');
    assert.deepEqual(harness.connectCalls, []);
});

test('createLocalEditNodeFromImage: 位置公式（源右缘+140、y 下限 40）+ original 角色 + connectInputNode 调用 + toast 分支', () => {
    const { ctx, harness, calls } = loadNodeOpsModule({
        nodes: [{ id: 'N', x: 0, y: 0, images: [{ url: 'i0' }, { url: 'i1' }] }],
    });
    const f = globalFunction(ctx, 'createLocalEditNodeFromImage');

    f('N');
    const created = fromContext(evaluate(ctx, 'canvasState.nodes.find(n => n.type === "local-edit")'));
    assert.ok(created, '创建了 local-edit 节点');
    assert.equal(created.x, 0 + 180 + 140, 'x = 源节点右缘 + 140（nodeRect 宽 180）');
    assert.equal(created.y, 40, 'y = max(40, 0 + max(0, 55 - 260)) = 40（下限门）');
    assert.equal(created.localEditActiveRole, 'original', '自动连好原图角色');
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), created.id, 'select:true → 选中新节点');
    assert.deepEqual(harness.connectCalls, [['N', created.id]], 'connectInputNode(源, 新) 恰好一次');
    assert.equal(harness.element('toast').textContent, 'smart.localEditCreated', '连线成功 toast 分支');
    assert.deepEqual(calls, ['render', 'scheduleSave', 'render', 'scheduleSave'], 'createLocalEditNode 自身 + 外层各一次 render/save');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, '仅内层 createLocalEditNode 压一次真实 undo');

    // 高节点：y 不再触下限（55 → 300-260=40 正值加到 500 上）。
    harness.layout = { width: 200, height: 600 };
    evaluate(ctx, 'canvasState.nodes.push({ id: "N2", x: 100, y: 500, images: [{ url: "a" }] })');
    f('N2', 0);
    const created2 = fromContext(evaluate(ctx, `canvasState.nodes.find(n => n.id !== '${created.id}' && n.type === "local-edit")`));
    assert.equal(created2.x, 100 + 200 + 140, 'x = 100+200+140');
    assert.equal(created2.y, 500 + Math.max(0, 300 - 260), 'y = 500 + (300-260) = 540');

    // 连线失败分支。
    harness.connectResult = false;
    evaluate(ctx, 'canvasState.nodes.push({ id: "N3", x: 0, y: 0, images: [{ url: "b" }] })');
    f('N3');
    assert.equal(harness.element('toast').textContent, 'smart.localEditConnectHint', '连线失败 toast 分支');
});

// ---------- 新建节点菜单 ----------

test('createNodeFromMenu: 七种类型偏移矩阵（逐类型独立 harness：无碰撞 → 固定偏移不变）+ closeCreateMenu + groupId 清零', () => {
    // #246 后每次创建都会避让既有节点（含刚建的），故偏移矩阵用逐类型独立 harness
    // （空画布 → 无碰撞 → 锚点 = 原固定偏移位）验证；碰撞扫描行为见下方 freeSpot 用例。
    const expected = {
        group: { x: 500 - 170, y: 400 - 110, type: 'smart-group' },
        prompt: { x: 500 - 158, y: 400 - 97, type: 'smart-prompt' },
        conversation: { x: 500 - 195, y: 400 - 215, type: 'smart-conversation' },
        agent: { x: 500 - 191, y: 400 - 180, type: 'smart-agent' },
        'local-edit': { x: 500 - 180, y: 400 - 165, type: 'local-edit' },
        loop: { x: 500 - 135, y: 400 - 95, type: 'smart-loop' },
        image: { x: 500 - Math.round(180 / 2), y: 400 - Math.round(110 / 2), type: 'smart-image' },
    };
    for (const [type, exp] of Object.entries(expected)) {
        const { ctx, harness } = loadNodeOpsModule();
        evaluate(ctx, 'createMenuPoint = { x: 500, y: 400 }');
        const node = fromContext(globalFunction(ctx, 'createNodeFromMenu')(type));
        assert.equal(node.type, exp.type, `${type}: 真实类型常量`);
        assert.equal(node.x, exp.x, `${type}: 无碰撞 → x = 原固定偏移`);
        assert.equal(node.y, exp.y, `${type}: 无碰撞 → y = 原固定偏移`);
        if (type === 'group') {
            assert.deepEqual(harness.addNodeToGroupCalls, [], 'group 类型在入组逻辑前早退 return');
        } else {
            assert.deepEqual(harness.addNodeToGroupCalls, [{ group: null, node: node.id }], `${type}: createMenuGroupId 空 → group null 也走 addNodeToSmartGroup`);
        }
        assert.equal(harness.closeMenuCalls, 1, `${type}: closeCreateMenu`);
        assert.equal(evaluate(ctx, 'canvasState.createMenuGroupId'), '', `${type}: 调用后清零`);
    }
});

test('createNodeFromMenu: 分组入组 → arrange(skipUndo) + groupId 还原-清零', () => {
    const { ctx, harness, calls } = loadNodeOpsModule({
        nodes: [{ id: 'G', type: 'smart-group', x: -1000, y: -1000, items: [] }],
    });
    evaluate(ctx, 'createMenuPoint = { x: 500, y: 400 }');
    const menu = globalFunction(ctx, 'createNodeFromMenu');
    // 分组入组：createMenuGroupId 指向真实分组节点（远离锚点 → 不触发扫描）+
    // addNodeToSmartGroup 返回 true → arrangeSmartGroupMembers(group, {skipUndo:true}) + render/save。
    ctx.canvasState.createMenuGroupId = 'G';
    harness.addNodeToGroupResult = true;
    const callsBefore = calls.length;
    const inGroup = fromContext(menu('agent'));
    assert.equal(inGroup.x, 500 - 191, '无碰撞 → 偏移不变');
    assert.deepEqual(harness.addNodeToGroupCalls.at(-1), { group: 'G', node: inGroup.id }, '分组节点对象传入');
    assert.deepEqual(fromContext(harness.arrangeCalls), [{ group: 'G', options: { skipUndo: true } }], '入组后自动整理且 skipUndo（创建已压过 undo）');
    assert.deepEqual(calls.slice(callsBefore), ['render', 'scheduleSave', 'render', 'scheduleSave'], 'createAgentNode 自身 + 入组整理各一次 render/save');
    assert.equal(evaluate(ctx, 'canvasState.createMenuGroupId'), '', '还原-入组-清零后仍为空');
});

test('createNodeFromMenu: createMenuPoint=null 回退 viewportCenter', () => {
    const { ctx, harness } = loadNodeOpsModule();
    evaluate(ctx, 'createMenuPoint = null');
    harness.viewportPoint = { x: 700, y: 300 };
    const node = fromContext(globalFunction(ctx, 'createNodeFromMenu')('image'));
    assert.equal(node.x, 700 - Math.round(180 / 2), 'viewportCenter 作为中心点');
    assert.equal(node.y, 300 - Math.round(110 / 2));
});

// ---------- #246 freeSpot 落点扫描（createNodeFromMenu 接线） ----------

test('createNodeFromMenu: #246 freeSpot — 锚点碰撞 → 向右扫描首个空位（x = 碰撞盒 maxX + 40，y 不变）', () => {
    const { ctx } = loadNodeOpsModule({
        nodes: [{ id: 'B1', x: 300, y: 300, images: [{ url: 'u' }] }],
    });
    // nodeRect 契约镜像：B1 → {x:300, y:300, width:180, height:110}（harness.layout）
    evaluate(ctx, 'createMenuPoint = { x: 500, y: 400 }');
    const menu = globalFunction(ctx, 'createNodeFromMenu');

    const prompt = fromContext(menu('prompt'));
    // 锚点 (342,303) 316×240：342 < 300+180+40=520 ✓ 且 342+316+40=698 > 300 ✓，
    // y: 303 < 300+110+40=450 ✓ 且 303+240+40=583 > 300 ✓ → 碰撞
    // → x = 300+180+40 = 520；复检 520 < 520 为假 → 无碰撞。
    assert.equal(prompt.x, 520, '碰撞 → x = 碰撞盒 maxX + GAP(40)');
    assert.equal(prompt.y, 400 - 97, 'y 不变（仅 x 向右扫描）');
});

test('createNodeFromMenu: #246 freeSpot — 图片节点同走扫描（尺寸经 imageLayout 同参重算）', () => {
    const { ctx } = loadNodeOpsModule({
        nodes: [{ id: 'B1', x: 300, y: 300, images: [{ url: 'u' }] }],
    });
    evaluate(ctx, 'createMenuPoint = { x: 500, y: 400 }');
    const image = fromContext(globalFunction(ctx, 'createNodeFromMenu')('image'));
    // 锚点 (410,345) 180×110：410 < 520 ✓ 且 410+180+40=630 > 300 ✓，y 同向相交 → 碰撞
    // → x = 520 → 中心 (610,400) → node.x = 610 - 90 = 520
    assert.equal(image.x, 520);
    assert.equal(image.y, 400 - 55, 'y = 锚点 y（345）');
});

test('createNodeFromMenu: #246 freeSpot — 多挡块链式右移逐箱推进到首个空位', () => {
    const { ctx } = loadNodeOpsModule({
        nodes: [
            { id: 'B1', x: 300, y: 300, images: [{ url: 'u' }] },
            { id: 'B2', x: 560, y: 350, images: [{ url: 'u' }] },
        ],
    });
    // B1 → maxX=480；B2 → {x:560, y:350, w:180, h:110} maxX=740
    evaluate(ctx, 'createMenuPoint = { x: 500, y: 400 }');
    const loop = fromContext(globalFunction(ctx, 'createNodeFromMenu')('loop'));
    // 锚点 (365,305) 340×168：
    // 步 1：撞 B1（365<520 且 365+340+40=745>300；y: 305<450 且 305+208=513>300）→ x = 520
    // 步 2：B1 不再撞；撞 B2（520<780 且 520+380=900>560；y: 305<500 且 513>350）→ x = 780
    // 步 3：B2: 780 < 780? 否 → 无碰撞 → 停在 (780, 305)
    assert.equal(loop.x, 780, '步 1 → 520（B1 maxX+40）；步 2 → 780（B2 maxX+40）');
    assert.equal(loop.y, 400 - 95, 'y 不变');
});

// ---------- rAF 交互层刷新调度 ----------

test('scheduleInteractionLayerRefresh: rAF 合并 / flush 后双刷新 / token 清零可重排', () => {
    const { ctx, harness } = loadNodeOpsModule();
    const schedule = globalFunction(ctx, 'scheduleInteractionLayerRefresh');

    schedule();
    schedule();
    schedule();
    assert.equal(harness.rafQueue.length, 1, '重复调度合并为一次 rAF');
    assert.equal(ctx.interactionLayerRaf, 1, 'token = 首个 rAF id（harness rafSeq 从 1 起）');

    assert.equal(harness.flushRafs(), 1, 'flush 执行一次');
    assert.equal(harness.refreshLayerCalls, 1, 'refreshConnectionLayer 被调用');
    assert.equal(harness.minimapCalls, 1, 'renderMinimap 被调用');
    assert.equal(ctx.interactionLayerRaf, 0, '回调内 token 清零');

    schedule();
    assert.equal(harness.rafQueue.length, 1, '清零后可以重新排队');
    assert.equal(harness.refreshLayerCalls, 1, '未 flush 前不重复执行');
});

// ---------- 重命名节点图片 ----------

test('renameSmartNodeImage: 取消/同名/纯空白 → 零副作用；新名 → 改名 + 真实 undo + 选中更新', async () => {
    const { ctx, harness, calls } = loadNodeOpsModule({
        nodes: [{ id: 'N', images: [{ url: 'u1', name: 'old.png' }] }],
    });
    const rename = globalFunction(ctx, 'renameSmartNodeImage');
    const name = () => fromContext(evaluate(ctx, 'canvasState.nodes[0].images[0].name'));

    // 取消（null）→ 不改名不压栈。
    harness.assetName = null;
    await rename('N', 0);
    assert.equal(name(), 'old.png');
    assert.equal(evaluate(ctx, 'undoStack.length'), 0);
    assert.deepEqual(fromContext(harness.assetDialogCalls.at(-1)),
        { title: 'smart.renameImage', value: 'old.png', placeholder: 'smart.imageName', cancelValue: null },
        '对话框入参（真实 tr key + imageNameLabel 当前值；fromContext 抹平跨 realm 原型）');

    // 同名 → 不改名不压栈。
    harness.assetName = 'old.png';
    await rename('N', 0);
    assert.equal(name(), 'old.png');
    assert.equal(evaluate(ctx, 'undoStack.length'), 0);

    // 纯空白 → trim 后空串 → 不改名。
    harness.assetName = '   ';
    await rename('N', 0);
    assert.equal(name(), 'old.png');

    // 新名（带空白）→ trim 后生效 + 真实 undo + 选中更新。
    harness.assetName = '  new name  ';
    await rename('N', 0);
    assert.equal(name(), 'new name', 'trim 后写入');
    assert.equal(evaluate(ctx, 'undoStack.length'), 1, '真实 pushUndo');
    assert.equal(evaluate(ctx, 'canvasState.selectedId'), 'N');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.selectedIds')), []);
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.selectedImage')), { nodeId: 'N', index: 0 });
    assert.deepEqual(calls, ['render', 'scheduleSave']);

    // 节点缺失/索引越界 → 连对话框都不开。
    const dialogs = harness.assetDialogCalls.length;
    await rename('ghost', 0);
    await rename('N', 5);
    assert.equal(harness.assetDialogCalls.length, dialogs, '无节点/无图片不打开对话框');
    assert.equal(name(), 'new name', '无副作用');

    // 索引字符串 '0' → Number 化后命中。
    harness.assetName = 'renamed2';
    await rename('N', '0');
    assert.equal(name(), 'renamed2');
});
