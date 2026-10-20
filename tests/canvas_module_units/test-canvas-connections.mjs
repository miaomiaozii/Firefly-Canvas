// #203 canvas-connections.js 行为单元测试（#199 轨道 4，ADR 0020：按依赖面从小到大补测，
// canvas-undo.js / canvas-core.js 之后的下一个模块）。
//
// canvas-connections.js 是画布节点连线模块（#158 P2a 从 smart-canvas.js 抽离）：连线渲染
// （SVG 几何/合并/状态 class）、刷新与事件绑定、拖拽连线目标判定、连线断开、擦除状态机
// 与轨迹、reroute、级联键等。本文件只含函数定义（无顶层执行代码）。
//
// 依赖面实测（acorn 作用域分析，口径=独立外部自由名，与 #203 canvas-core 同一口径）：
// **28 个外部自由名**，其中：
//   - 13 个由**加载真实模块**满足（不桩）：
//       canvas-core.js    → canvas(let 绑定) / canvasCore(Proxy 接口) / toast / tr /
//                          isSmartImageNode / isSmartGroupNode
//       canvas-state.js   → canvasState
//       canvas-node-types.js → NODE_TYPE_PROMPT/IMAGE/CONVERSATION/TASK/LOOP/GROUP/LOCAL_EDIT
//       （其中 6 个 NODE_TYPE_* 常量 + 其余 7 个，见上）
//   - 15 个手写函数桩（全部来自不可独立加载的宿主脚本：smart-canvas.js /
//     canvas-media-history.js / canvas-generation-cascade.js，按真实契约最小化）：
//       pushUndo selectedNodeIds syncLocalEditInputRoles clearDetachedRunInputRefs
//       isHistoryGroupNode demoteHistoryGroupNode imagesForNode promptTextItemsForNode
//       fitSmartLoopNode cascadeTailForLoop smartCascadeGraphForTail downstreamNodesForId
//       rectOverlapNode smartArrangeAtomicIds translateSmartNodeWithMembers
//   - 另有 9 个 canvasCore 接口成员（canvas-connections.js 里经 canvasCore 代理访问，
//     不是它自己的自由名，但必须提供）：nodeRect / world / shell / render / scheduleSave /
//     smartCascadeRuns / smartGroupContainingNode / inputNodesFor / dragState；
//     其中 4 个是纯数据/DOM（world、shell、smartCascadeRuns、dragState），无产品逻辑。
//   - 浏览器全局 document/requestAnimationFrame/setTimeout 由 harness.mjs 提供；
//     5 个 harness makeElement 缺失的 DOM API（elementFromPoint / createElementNS /
//     template / closest / replaceWith）在测试内按实例补齐——只补 DOM 表面积，不含产品逻辑。
// ADR 0020 判据「桩要开始重新实现产品逻辑就停」：15 个手写桩里 9 个是记录器/数据驱动
// （零逻辑），6 个是单行契约镜像（isHistoryGroupNode 逐字对应 canvas-media-history.js:203-205，
// rectOverlapNode 对应 smart-canvas.js:10976-10985 的中心点命中，smartArrangeAtomicIds 用
// 「过滤存在节点」的最小子集——测试只用非分组 id，不触碰分组成员折叠路径）。
//
// 防的回归（全部断言可观察输出/副作用，不是「不抛错就算过」）：
//   - renderConnections 的曲线几何（dx/dy 控制点、端点锚位）、kind 颜色/线宽、
//     pending/cascade/selected 的 class 与颜色、分组成员连线合并（data-conn-index 多索引）、
//     组内连线隐藏、history 连线不渲染、reduce-motion 门；
//   - 断开/擦除的真实副作用：canvas.connections 变更、inputNodeIds 清理、pushUndo/render/
//     scheduleSave 调用序列、擦除状态机（indices 集合/started/count/lastX/lastY 步进采样）;
//   - 拖拽连线目标判定（prompt/loop 用光标点探测 vs 其余用节点矩形）、
//     canAutoConnectDraggedNode 的类型合法性矩阵；
//   - reroute 的端点改写 + 自环/重复过滤 + inputNodeIds 重映射（含自引用剔除）；
//   - insertionConnectionForNode 的中点距离选择（96px 门）、connectInputNode 的分支
//     （agent-task 只读 toast、prompt→conversation 忽略 toast、local-edit 角色路由去重、
//     loop 的 imageInput/showPrompt 推导）。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

// 加载真实底层模块 + 被测模块（按 HTML 相对顺序，harness 会排序）：
// canvas-node-types.js → canvas-core.js → canvas-state.js → canvas-connections.js。
// canvas-connections.js 顶层无执行代码（只有函数/let 声明），加载期不触碰任何外部名，
// 所以外部名在 loadModules 之前用全局属性桩好即可（canvasCore Proxy 是惰性解析，
// 运行期才按名解析，同样成立）。
function loadConnectionsModule({ nodes = [], connections = [], selectedId = '', selectedIds = [] } = {}) {
    const { ctx, harness } = createCanvasContext();
    const calls = [];
    // 数据驱动注册表：测试显式喂数据，桩零逻辑（防「第二套假核心」）。
    harness.rectOverlapCalls = [];
    harness.groupRegistry = new Map();       // 成员 nodeId -> 分组节点（canvasCore.smartGroupContainingNode）
    harness.mediaByNode = new Map();         // nodeId -> images[]（imagesForNode）
    harness.promptsByNode = new Map();       // nodeId -> prompt 文本[]（promptTextItemsForNode）
    harness.inputNodesFor = new Map();       // nodeId -> 上游节点[]（canvasCore.inputNodesFor）
    harness.cascadeTails = new Map();        // loopId -> tail（cascadeTailForLoop）
    harness.cascadeGraphs = new Map();       // tailId -> {root,path,edges}（smartCascadeGraphForTail）
    harness.downstreamByNode = new Map();    // nodeId -> 下游节点[]（downstreamNodesForId）
    harness.selectedIdsResult = [];          // selectedNodeIds() 返回值

    // DOM 元素（canvasCore 接口成员 world/shell 是页面真实节点的替身）。
    const world = harness.makeElement('div');
    const shell = harness.makeElement('div');
    world.replaceWith = (next) => { world.__replacedWith = next; };
    harness.world = world;
    harness.shell = shell;

    // --- canvasCore 接口成员（真实 canvasCore Proxy 运行期按名解析到这些全局属性） ---
    // nodeRect：契约镜像（smart-canvas.js:1772-1775：x/y 取节点，宽/高取布局）；
    // 测试需要精确几何时在节点上显式给 width/height，缺省 180x110（真实 imageLayout 的常用默认）。
    ctx.nodeRect = (node) => ({
        x: Number(node?.x) || 0,
        y: Number(node?.y) || 0,
        width: node?.width || 180,
        height: node?.height || 110,
    });
    ctx.world = world;
    ctx.shell = shell;
    ctx.render = () => calls.push('render');
    ctx.scheduleSave = () => calls.push('scheduleSave');
    ctx.smartCascadeRuns = new Map();        // 纯数据结构（真实是 Map）
    ctx.dragState = { group: [], groupIds: [] };  // 纯数据
    ctx.smartGroupContainingNode = (id) => harness.groupRegistry.get(id) || null;
    ctx.inputNodesFor = (node) => harness.inputNodesFor.get(node?.id) || [];

    // --- 15 个手写函数桩 ---
    ctx.pushUndo = () => calls.push('pushUndo');
    ctx.selectedNodeIds = () => harness.selectedIdsResult;
    ctx.syncLocalEditInputRoles = (n) => calls.push('syncLocalEditInputRoles:' + (n?.id || ''));
    ctx.clearDetachedRunInputRefs = (n) => calls.push('clearDetachedRunInputRefs:' + (n?.id || ''));
    // 契约镜像（canvas-media-history.js:203-205 逐字一致），用真实的 isSmartImageNode。
    evaluate(ctx, 'isHistoryGroupNode = (node) => Boolean(isSmartImageNode(node) && (node.isHistoryGroup || node.historyFor))');
    ctx.demoteHistoryGroupNode = (g) => calls.push('demoteHistoryGroupNode:' + (g?.id || ''));
    ctx.imagesForNode = (n) => harness.mediaByNode.get(n?.id) || [];
    ctx.promptTextItemsForNode = (n) => harness.promptsByNode.get(n?.id) || [];
    ctx.fitSmartLoopNode = (n) => calls.push('fitSmartLoopNode:' + (n?.id || ''));
    ctx.cascadeTailForLoop = (id) => harness.cascadeTails.get(id) || null;
    ctx.smartCascadeGraphForTail = (tail) => harness.cascadeGraphs.get(tail?.id) || { root: null, path: [], edges: [] };
    ctx.downstreamNodesForId = (id) => harness.downstreamByNode.get(id) || [];
    ctx.translateSmartNodeWithMembers = (n, dx, dy) => calls.push('translate:' + (n?.id || '') + ':' + dx + ':' + dy);
    // 契约镜像（smart-canvas.js:10976-10985 的中心点命中测试）；本模块的调用方
    // dragConnectTargetFor 只关心「选中了谁/探测坐标是什么」，这里同时记录调用参数。
    ctx.rectOverlapNode = (draggedId, x, y, w, h, excludeIds = []) => {
        harness.rectOverlapCalls.push([draggedId, x, y, w, h, excludeIds]);
        const cx = x + w / 2, cy = y + h / 2;
        const excluded = new Set([draggedId, ...(excludeIds || [])]);
        for (const n of ctx.canvasState.nodes) {
            if (excluded.has(n.id)) continue;
            const r = ctx.nodeRect(n);
            if (cx >= r.x && cx <= r.x + r.width && cy >= r.y && cy <= r.y + r.height) return n;
        }
        return null;
    };
    // 最小契约（真实实现 smart-canvas.js:1776 起还有分组成员折叠）：测试只用非分组 id，
    // 不触碰折叠路径——折叠语义属于 smart-canvas.js 的行为，不在本模块测试范围。
    ctx.smartArrangeAtomicIds = (ids) => (ids || []).filter((id) => ctx.canvasState.nodes.some((n) => n.id === id));

    // --- harness 缺失的 5 个 DOM API（实例级补齐，仅 DOM 表面积） ---
    ctx.document.elementFromPoint = (x, y) => harness.hitForPoint ? harness.hitForPoint(x, y) : null;
    const origCreateElement = ctx.document.createElement;
    ctx.document.createElement = (tag) => {
        if (tag !== 'template') return origCreateElement(tag);
        const tpl = { __html: '' };
        Object.defineProperty(tpl, 'innerHTML', {
            get() { return tpl.__html; },
            set(v) { tpl.__html = String(v); },
        });
        Object.defineProperty(tpl, 'content', {
            get() { return { firstElementChild: tpl.__html ? { __svgHtml: tpl.__html } : null }; },
        });
        return tpl;
    };
    harness.nsElements = [];
    ctx.document.createElementNS = (ns, tag) => {
        const el = origCreateElement(tag);
        const paths = [origCreateElement('path'), origCreateElement('path')];
        el.querySelectorAll = (sel) => (sel === 'path' ? paths : []);
        el.__removed = false;
        el.remove = () => { el.__removed = true; };
        harness.nsElements.push(el);
        return el;
    };

    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-connections.js']);

    // 运行期状态：canvas 是 canvas-core.js 的词法 let 绑定（不是全局对象属性），必须用
    // evaluate 赋值；canvasState 是 window.canvasState 对象，宿主侧直接改属性即可。
    ctx.canvasState.nodes = nodes;
    ctx.canvasState.selectedId = selectedId;
    ctx.canvasState.selectedIds = selectedIds;
    ctx.__canvasPayload = { id: 'cv', nodes, connections };
    evaluate(ctx, 'canvas = __canvasPayload');
    return { ctx, harness, calls };
}

// world.querySelectorAll / shell.querySelector 的测试级覆盖辅助（makeElement 默认返回空）。
function wireWorldQueries(harness, { oldSvg = null, connEls = [] } = {}) {
    harness.world.querySelector = (sel) => (sel === 'svg.connection-layer' ? oldSvg : null);
    harness.world.querySelectorAll = (sel) => (sel === '[data-conn-index]' ? connEls : []);
}
function wireShellQueries(harness) {
    harness.shell.querySelector = (sel) => (sel.startsWith(':scope')
        ? (harness.shell.children.find((c) => c.tagName === 'SVG' && c.classList.contains('connection-erase-trail')) || null)
        : null);
}

test('loads canvas-connections.js standalone: all top-level functions exist, proxy resolves interface members', () => {
    const { ctx, harness } = loadConnectionsModule();
    // 顶层 function 都在（模块加载成功且无 SyntaxError 的前置证据）。
    for (const name of [
        'rerouteSmartConnections', 'connectedSmartClusterIds', 'arrangeSmartIdsByConnections',
        'mergeSmartConnections', 'renderConnections', 'refreshConnectionLayer',
        'scheduleConnectionLayerRefresh', 'bindConnectionEvents', 'dragConnectTargetFor',
        'canAutoConnectDraggedNode', 'disconnectConnections', 'connectionIndexSpecFromPoint',
        'eraseConnectionsAtClientPoint', 'finishConnectionErase', 'eraseConnectionsAtPoint',
        'eraseConnectionsAlongPointer', 'ensureConnectionEraseTrail', 'updateConnectionEraseTrail',
        'clearConnectionEraseTrail', 'connectionMidpoint', 'insertionConnectionForNode',
        'addConnection', 'connectInputNode', 'lineConnectionsFor', 'outgoingConnectionsFor',
        'cascadeConnectionKeys', 'smartCascadeEdgeState', 'smartGroupScopeId',
        'moveSmartNodeAtom', 'shellPoint',
    ]) globalFunction(ctx, name);
    // 词法 let 绑定可读写（模块私有状态）。
    assert.equal(evaluate(ctx, 'connectionEraseState'), null, 'connectionEraseState 初始 null');
    assert.equal(evaluate(ctx, 'loopInsertPreview'), null, 'loopInsertPreview 初始 null');
    // 真实 canvasCore Proxy 惰性解析到本测试提供的全局成员。
    assert.equal(evaluate(ctx, 'typeof canvasCore.nodeRect'), 'function', 'canvasCore.nodeRect 解析成功');
    assert.equal(evaluate(ctx, 'canvasCore.world === world'), true, 'canvasCore.world 是同一个元素');
    assert.equal(evaluate(ctx, 'canvasCore.shell === shell'), true, 'canvasCore.shell 是同一个元素');
    assert.equal(evaluate(ctx, 'canvasCore.render === render'), true, 'canvasCore.render 解析成功');
    // 真实底层模块就位。
    assert.equal(evaluate(ctx, 'NODE_TYPE_LOOP'), 'smart-loop', 'NODE_TYPE_LOOP 来自真实 canvas-node-types.js');
    assert.equal(evaluate(ctx, "canvasState.nodes.length"), 0, 'canvasState 来自真实 canvas-state.js');
    assert.equal(evaluate(ctx, 'canvas && canvas.id'), 'cv', 'canvas let 绑定已被 evaluate 赋值');
    assert.ok(harness, 'harness 可用');
});

test('mergeSmartConnections: local-first dedupe by from->to:kind, filtered to nodeIds, nulls dropped', () => {
    const { ctx } = loadConnectionsModule();
    const merge = globalFunction(ctx, 'mergeSmartConnections');
    const ids = new Set(['A', 'B', 'C']);
    const local = [{ from: 'A', to: 'B', kind: 'input' }, { from: 'B', to: 'C' }, null];
    const remote = [{ from: 'A', to: 'B', kind: 'input' }, { from: 'B', to: 'C' }, { from: 'C', to: 'D' }];

    const out = fromContext(merge(local, remote, ids));

    assert.equal(out.length, 2, '重复项与 nodeIds 外的项都被丢弃');
    assert.deepEqual(out[0], { from: 'A', to: 'B', kind: 'input' }, 'local 优先保留');
    assert.equal(out[1].from, 'B');
    assert.equal(out[1].to, 'C', '缺省 kind 按 flow 参与去重');
    assert.ok(!out.some((c) => c.to === 'D'), 'nodeIds 之外的端点被过滤');
    assert.deepEqual(fromContext(merge(null, null, ids)), [], 'null 输入 → 空');
    // 同端点不同 kind 不互相去重。
    const mixed = fromContext(merge([{ from: 'A', to: 'B' }], [{ from: 'A', to: 'B', kind: 'input' }], ids));
    assert.equal(mixed.length, 2, 'flow 与 input 是两个不同的连接');
});

test('addConnection: pushes {from,to,kind}, dedupes by from/to/kind, guards self and empty ids', () => {
    const { ctx } = loadConnectionsModule({ connections: [] });
    const add = globalFunction(ctx, 'addConnection');
    const conns = () => fromContext(evaluate(ctx, 'canvas.connections'));

    add('A', 'B');
    assert.deepEqual(conns(), [{ from: 'A', to: 'B', kind: 'flow' }], '缺省 kind=flow');
    add('A', 'B');
    assert.equal(conns().length, 1, '完全相同的连接不重复加入');
    add('A', 'B', 'input');
    assert.equal(conns().length, 2, '不同 kind 是不同连接');
    add('A', 'A');
    add('', 'B');
    add('A', '');
    assert.equal(conns().length, 2, '自连/空 id 不加入');

    evaluate(ctx, 'canvas = null');
    add('X', 'Y');
    assert.equal(evaluate(ctx, 'canvas'), null, 'canvas 为 null 时安全无操作');
});

test('smartCascadeEdgeState: first non-empty state across runs, empty when no run covers the edge', () => {
    const { ctx } = loadConnectionsModule();
    const edgeState = globalFunction(ctx, 'smartCascadeEdgeState');

    assert.equal(edgeState('A->B'), '', '无 run → 空串');
    ctx.smartCascadeRuns = new Map([
        ['R1', {}],
        ['R2', { runPath: { states: { 'A->B': 'active', 'C->D': 'done' } } }],
    ]);
    assert.equal(edgeState('A->B'), 'active');
    assert.equal(edgeState('C->D'), 'done');
    assert.equal(edgeState('X->Y'), '', '未覆盖的边 → 空串');
    assert.equal(edgeState(null), '', 'run 缺 runPath 时安全');
});
