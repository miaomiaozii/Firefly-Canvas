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
    harness.selectedIdsResult = selectedIds;          // selectedNodeIds() 返回值

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
        // makeElement.setAttribute 不同步 classList；模块用 setAttribute('class', ...) 建轨迹 svg，
        // 补齐 DOM 表面积（仅 class 同步，不含产品逻辑），让 querySelector 能按 class 找到它。
        const origSet = el.setAttribute.bind(el);
        el.setAttribute = (n, v) => {
            if (n === 'class') String(v).split(/\s+/).filter(Boolean).forEach((c) => el.classList.add(c));
            return origSet(n, v);
        };
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

// ---------- 测试内小组件 ----------
const ev = (type) => ({ type, preventDefault(){}, stopPropagation(){} });
function connElement(harness, index) {
    const el = harness.makeElement('path');
    el.dataset.connIndex = index;
    el.closest = () => el;
    return el;
}

// ---------- reroute / cluster / arrange ----------

test('rerouteSmartConnections: rewrites endpoints, drops self-loops and dupes, remaps inputNodeIds', () => {
    const nodes = [
        { id: 'A' },
        { id: 'B', inputNodeIds: ['A', 'B'] },
        { id: 'C', inputNodeIds: ['A', 'C'] },
        { id: 'X', inputNodeIds: ['A'] },
    ];
    const { ctx } = loadConnectionsModule({
        nodes,
        connections: [
            { from: 'A', to: 'B' },
            { from: 'A', to: 'C', kind: 'input' },
            { from: 'A', to: 'C' },
            { from: 'A', to: 'C' },
            { from: 'X', to: 'A' },
        ],
    });
    const reroute = globalFunction(ctx, 'rerouteSmartConnections');

    reroute('A', 'B');
    const conns = fromContext(evaluate(ctx, 'canvas.connections'));
    // A→B 变成 B→B（自环，被过滤）；两条 A→C(input) 去重成一条；A→C(flow) 与 input 不同 kind 保留；X→A 改写为 X→B。
    assert.deepEqual(conns, [
        { from: 'B', to: 'C', kind: 'input' },
        { from: 'B', to: 'C' },
        { from: 'X', to: 'B' },
    ], '端点改写 + 自环/重复过滤');
    // inputNodeIds 重映射：A→B，且剔除指向自己的 id。
    assert.deepEqual(nodesInputIds(ctx, 'B'), [], 'B.inputNodeIds 里 A→B 后全是自引用，被剔除');
    assert.deepEqual(nodesInputIds(ctx, 'C'), ['B'], 'C.inputNodeIds A→B；指向自己的 id 也被剔除（filter id !== n.id）');
    assert.deepEqual(nodesInputIds(ctx, 'X'), ['B'], 'X.inputNodeIds A→B');

    // canvas 为 null 时不改连线，但 inputNodeIds 仍重映射（守卫只包 connections 分支）。
    evaluate(ctx, 'canvas = null');
    nodes[2].inputNodeIds = ['X', 'C'];
    reroute('X', 'B');
    assert.equal(evaluate(ctx, 'canvas'), null, 'canvas 保持 null');
    assert.deepEqual(nodesInputIds(ctx, 'C'), ['B'], 'canvas=null 时 inputNodeIds 仍重映射（自引用 C 同样被剔除）');
});

test('connectedSmartClusterIds: BFS over connections from the seed, empty when seed is not a node', () => {
    const { ctx } = loadConnectionsModule({
        nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }, { id: 'D' }],
        connections: [
            { from: 'A', to: 'B' },
            { from: 'B', to: 'C' },
            { from: 'C', to: 'A' },
            { from: 'D', to: 'E' },
        ],
    });
    const cluster = globalFunction(ctx, 'connectedSmartClusterIds');

    assert.deepEqual(fromContext(cluster('A')), ['A', 'B', 'C'], 'A 的连通分量（含环），BFS 顺序');
    assert.deepEqual(fromContext(cluster('D')), ['D'], 'D 经不存在节点 E 的边探不到别人');
    assert.deepEqual(fromContext(cluster('Z')), [], 'seed 不在节点里 → 空');
    assert.deepEqual(fromContext(cluster(null)), [], 'null seed → 空');
});

test('arrangeSmartIdsByConnections: depth columns via moveSmartNodeAtom, false when fewer than 2 real nodes', () => {
    const { ctx, calls } = loadConnectionsModule({
        nodes: [
            { id: 'n1', x: 0, y: 0 },
            { id: 'n2', x: 500, y: 0 },
            { id: 'n3', x: 500, y: 400 },
        ],
        connections: [
            { from: 'n1', to: 'n2' },
            { from: 'n1', to: 'n3' },
        ],
    });
    const arrange = globalFunction(ctx, 'arrangeSmartIdsByConnections');

    assert.equal(arrange(['n1', 'n2', 'n3']), true, '正常返回 true');
    // 深度列：n1(depth 0) 在 (startX,startY)=(0,0)；n2/n3(depth 1) 排在 x=0+maxW(180)+180=360 列，
    // 按 y 排序后 n2 在 y=0、n3 在 y=0+max(110,110)+56=166；moveSmartNodeAtom 传的是相对位移。
    assert.deepEqual(calls, [
        'translate:n1:0:0',
        'translate:n2:-140:0',
        'translate:n3:-140:-234',
    ], '逐节点的真实位移（dx=目标x-当前x）');

    assert.equal(arrange(['n1']), false, '只有 1 个节点 → false（不动）');
    assert.equal(arrange(['n1', 'ghost']), false, '不存在的 id 被 smartArrangeAtomicIds 滤掉后不足 2 个');

    // 有向环（无入度 0 的根）：回退用第一个节点做种子，仍完成布局。
    const { ctx: ctx2, calls: calls2 } = loadConnectionsModule({
        nodes: [{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 100, y: 0 }],
        connections: [
            { from: 'a', to: 'b' },
            { from: 'b', to: 'a' },
        ],
    });
    assert.equal(globalFunction(ctx2, 'arrangeSmartIdsByConnections')(['a', 'b']), true);
    assert.deepEqual(calls2, ['translate:b:-100:0', 'translate:a:360:0'], '环回退：b 先于 a 排到第 0 列，a 进第 1 列');
});

// ---------- renderConnections ----------

test('renderConnections: flow geometry anchors, 45% control handles, colors, hit/end/cut decorations', () => {
    const { ctx } = loadConnectionsModule({
        nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 1000, y: 0 }],
        connections: [{ from: 'A', to: 'B' }],
    });
    const svg = globalFunction(ctx, 'renderConnections')();

    // 外层 6000x4000 SVG 容器。
    assert.ok(svg.startsWith('<svg class="connection-layer '), '无 reduce-motion 时的基础 class');
    assert.ok(svg.includes('width="6000" height="4000" viewBox="0 0 6000 4000"'), '固定画布尺寸');
    // 曲线锚点：flow 从源节点右缘中点 → 目标节点左缘中点。
    const m = svg.match(/d="M([\d.]+) ([\d.]+) C ([\d.]+) ([\d.]+), ([\d.]+) ([\d.]+), ([\d.]+) ([\d.]+)"/);
    assert.ok(m, '找到连线曲线');
    const [fx, fy, c1x, c1y, c2x, c2y, tx, ty] = m.slice(1).map(Number);
    assert.equal(fx, 180, '起点 x = 源节点右缘（x + 默认宽 180）');
    assert.equal(fy, 55, '起点 y = 源节点中点（110/2）');
    assert.equal(tx, 1000, '终点 x = 目标节点左缘');
    assert.equal(ty, 55, '终点 y = 目标节点中点');
    const dx = Math.max(50, Math.abs(tx - fx) * 0.45);
    assert.ok(Math.abs(c1x - (fx + dx)) < 1e-9, '控制柄 1 横向偏移 45% 跨度（最小 50）');
    assert.ok(Math.abs(c1y - fy) < 1e-9, '控制柄 1 与起点同高');
    assert.ok(Math.abs(c2x - (tx - dx)) < 1e-9, '控制柄 2 对称');
    assert.ok(Math.abs(c2y - ty) < 1e-9, '控制柄 2 与终点同高');
    // 视觉属性：flow 的默认色/线宽，命中层 14px 透明加宽，端点圆在 (tx,ty)，剪断手柄在中点。
    assert.ok(svg.includes('stroke="rgba(148,163,184,0.62)" stroke-width="1.6"'), 'flow 默认色与线宽');
    assert.ok(svg.includes('<path class="conn-hit" data-conn-index="0" d="') && svg.includes('stroke="transparent" stroke-width="14"'), '命中层');
    assert.ok(svg.includes('<circle class="conn-end" data-conn-index="0" cx="1000" cy="55" r="3.5"'), '端点圆');
    assert.ok(svg.includes('<g class="conn-cut" data-conn-index="0" transform="translate(590 55)">'), '剪断手柄在中点');
    assert.ok(svg.includes('class=" conn-line"'), '无任何状态时 class 前缀为空串');
});

test('renderConnections: input kind color/width, pending opacity, selection class', () => {
    const { ctx } = loadConnectionsModule({
        nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 1000, y: 0, pending: true }],
        connections: [{ from: 'A', to: 'B', kind: 'input' }],
        selectedIds: ['B'],
    });
    const svg = globalFunction(ctx, 'renderConnections')();

    assert.ok(svg.includes('class="conn-pending conn-selected conn-line"'), 'pending+selected 两个状态 class 同时生效');
    assert.ok(svg.includes('stroke="rgba(100,116,139,0.62)" stroke-width="1.9"'), 'input kind 的颜色与更粗线宽');
    assert.ok(svg.includes('opacity=".82"'), 'pending 线半透明');
    // 无选中时不出现 conn-selected。
    const { ctx: ctx2 } = loadConnectionsModule({
        nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 1000, y: 0 }],
        connections: [{ from: 'A', to: 'B', kind: 'input' }],
    });
    assert.ok(!globalFunction(ctx2, 'renderConnections')().includes('conn-selected'), '无选中 → 无 selected class');
});

test('renderConnections: cascade state classes (active/done) and green color from runPath states', () => {
    const load = (state) => {
        const { ctx } = loadConnectionsModule({
            nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 1000, y: 0 }],
            connections: [{ from: 'A', to: 'B' }],
        });
        ctx.smartCascadeRuns = new Map([['R1', { runPath: { states: { 'A->B': state } } }]]);
        return globalFunction(ctx, 'renderConnections')();
    };

    const activeSvg = load('active');
    assert.ok(activeSvg.includes('class="conn-cascade conn-cascade-wait conn-cascade-active conn-line"'), 'active 三件套 class');
    assert.ok(activeSvg.includes('stroke="#16a34a"'), '级联线绿色');

    const doneSvg = load('done');
    assert.ok(doneSvg.includes('class="conn-cascade conn-cascade-done conn-line"'), 'done 只有 cascade-done');
    assert.ok(doneSvg.includes('stroke="#16a34a"'), 'done 仍是级联绿色');
});

test('renderConnections: merges A->member lines into one A->group line; hides same-scope lines; skips history', () => {
    const { ctx, harness } = loadConnectionsModule({
        nodes: [
            { id: 'A', x: 0, y: 0 },
            { id: 'G', type: 'smart-group', x: 2000, y: 0 },
            { id: 'M1', x: 2100, y: 0 },
            { id: 'M2', x: 2200, y: 0 },
        ],
        connections: [
            { from: 'A', to: 'M1' },
            { from: 'A', to: 'M2' },
            { from: 'M1', to: 'M2' },
            { from: 'M1', to: 'G' },
        ],
    });
    // M1/M2 是 G 的成员。
    const G = { id: 'G', type: 'smart-group', x: 2000, y: 0 };
    harness.groupRegistry.set('M1', G);
    harness.groupRegistry.set('M2', G);

    const svg = globalFunction(ctx, 'renderConnections')();
    // 两条 A→成员 合并成一条 A→G，data-conn-index 携带两个原始索引；组内线（成员↔成员、成员↔组本体）不渲染。
    assert.equal(svg.split('data-conn-index="0,1"').length - 1, 4, '合并线 4 个元素（line/hit/end/cut）共享索引 0,1');
    assert.ok(!svg.includes('data-conn-index="0"'), '没有单独的 0 号线');
    assert.ok(!svg.includes('data-conn-index="1"'), '没有单独的 1 号线');
    assert.equal(svg.split('conn-line').length - 1, 1, '整层只有 1 条可见线');
});

test('renderConnections: history kind never drawn; >24 non-done cascade states switch on reduce-motion', () => {
    // history 不渲染。
    const { ctx } = loadConnectionsModule({
        nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 1000, y: 0 }],
        connections: [{ from: 'A', to: 'B', kind: 'history' }],
    });
    const svg = globalFunction(ctx, 'renderConnections')();
    assert.ok(!svg.includes('<path'), 'history 连线不产生任何 path');
    assert.ok(svg.includes('<svg class="connection-layer "'), '空层仍是 6000x4000 容器');

    // reduce-motion：25 个非 done 状态（>24 门）。
    const { ctx: ctx2 } = loadConnectionsModule({
        nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 1000, y: 0 }],
        connections: [{ from: 'A', to: 'B' }],
    });
    const states = {};
    for (let i = 0; i < 25; i++) states[`k${i}->x${i}`] = 'waiting';
    ctx2.smartCascadeRuns = new Map([['R1', { runPath: { states } }]]);
    const svg2 = globalFunction(ctx2, 'renderConnections')();
    assert.ok(svg2.includes('<svg class="connection-layer conn-reduce-motion"'), '25 个活动状态 → conn-reduce-motion');
    // 边界：正好 24 个不触发。
    delete states['k24->x24'];
    const svg3 = globalFunction(ctx2, 'renderConnections')();
    assert.ok(!svg3.includes('conn-reduce-motion'), '24 个活动状态不触发（门是 >24）');
});

// ---------- refresh / schedule / bind ----------

test('refreshConnectionLayer: replaces the svg, binds hit dblclick + others click to disconnectConnections', () => {
    const { ctx, harness, calls } = loadConnectionsModule({
        nodes: [{ id: 'A' }, { id: 'B', inputNodeIds: ['A'] }, { id: 'C' }, { id: 'D' }],
        connections: [
            { from: 'A', to: 'B' },
            { from: 'C', to: 'D' },
        ],
    });
    const oldSvg = harness.makeElement('svg');
    oldSvg.replaceWith = (next) => { harness.replaced = next; };
    const hitEl = connElement(harness, '0');
    hitEl.classList.add('conn-hit');
    const cutEl = connElement(harness, '1');
    wireWorldQueries(harness, { oldSvg, connEls: [hitEl, cutEl] });

    globalFunction(ctx, 'refreshConnectionLayer')();
    assert.ok(harness.replaced && typeof harness.replaced.__svgHtml === 'string', '旧 svg 被替换');
    assert.ok(harness.replaced.__svgHtml.startsWith('<svg class="connection-layer'), '新层是 renderConnections 的输出');
    assert.ok(harness.replaced.__svgHtml.includes('data-conn-index="0"'), '新层包含连接');

    // 绑定后：conn-hit 只响应 dblclick，其余元素只响应 click，都落到 disconnectConnections。
    hitEl.dispatchEvent(ev('dblclick'));
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 1, 'dblclick 断开了索引 0 的线');
    assert.deepEqual(nodesInputIds(ctx, 'B'), [], '断开后 B.inputNodeIds 清掉 A');
    assert.ok(calls[0] === 'pushUndo', '先 pushUndo');

    // 旧 DOM 元素带的是刷新前的索引：索引 1 已不存在 → 安全无操作（不 pushUndo、不改连线）。
    const callsAfterFirst = calls.length;
    cutEl.dispatchEvent(ev('click'));
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 1, '陈旧索引 click 是无操作');
    assert.equal(calls.length, callsAfterFirst, '无副作用');

    // 重新绑定后（模拟 render 重建层），当前索引 0 的 click 才真正断开。
    const cutEl2 = connElement(harness, '0');
    wireWorldQueries(harness, { oldSvg, connEls: [cutEl2] });
    globalFunction(ctx, 'bindConnectionEvents')();
    cutEl2.dispatchEvent(ev('click'));
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 0, 'click 断开了剩余那条线');
    assert.deepEqual(calls,
        ['pushUndo', 'clearDetachedRunInputRefs:B', 'render', 'scheduleSave',
         'pushUndo', 'clearDetachedRunInputRefs:D', 'render', 'scheduleSave'],
        '两次断开的完整副作用序列（flow 线都清 run 引用，无 inputNodeIds/local-edit/history 副作用）');

    // 无旧 svg → 提前返回，不替换。
    const { ctx: ctx2, harness: h2 } = loadConnectionsModule({
        nodes: [{ id: 'A' }, { id: 'B' }],
        connections: [{ from: 'A', to: 'B' }],
    });
    wireWorldQueries(h2, { oldSvg: null });
    globalFunction(ctx2, 'refreshConnectionLayer')();
    assert.equal(h2.replaced, undefined, '没有旧 svg 时不做替换');
});

function nodesInputIds(ctx, id) {
    return fromContext(evaluate(ctx, `canvasState.nodes.find(n => n.id === '${id}').inputNodeIds`));
}

test('scheduleConnectionLayerRefresh: coalesces repeated calls into one rAF, refresh resets the token', () => {
    const { ctx, harness } = loadConnectionsModule();
    const schedule = globalFunction(ctx, 'scheduleConnectionLayerRefresh');

    schedule();
    schedule();
    schedule();
    assert.equal(harness.rafQueue.length, 1, '重复调度合并为一次 rAF');
    assert.equal(ctx.canvasState.connectionLayerRaf, 1, '记录 rAF id 作去重 token');
    assert.equal(harness.flushRafs(), 1, 'flush 执行一次刷新');
    assert.equal(ctx.canvasState.connectionLayerRaf, 0, '刷新后 token 清零，可再次调度');
    schedule();
    assert.equal(harness.rafQueue.length, 1, '清零后可以重新排队');
});

// ---------- drag / auto-connect ----------

test('dragConnectTargetFor: prompt/loop probe the pointer point, others probe the node rect', () => {
    const { ctx, harness } = loadConnectionsModule({
        nodes: [
            { id: 'P', type: 'smart-prompt', x: 0, y: 0 },
            { id: 'I', x: 10, y: 200 },
            { id: 'T', x: 40, y: 50 },
        ],
    });
    const pick = globalFunction(ctx, 'dragConnectTargetFor');

    // 光标点 (60,140) 只落在 T 的矩形内（P: y≤110、I: y≥200 都不含）。
    const target = pick(ctx.canvasState.nodes[0], { x: 60, y: 140 });
    assert.equal(target && target.id, 'T', 'prompt 用光标点（60,140）探测到 T 的矩形');
    assert.deepEqual(harness.rectOverlapCalls.at(-1), ['P', 59, 139, 2, 2, []], 'prompt 探测框 = (point.x-1, point.y-1, 2, 2)');

    const before = harness.rectOverlapCalls.length;
    assert.equal(pick(ctx.canvasState.nodes[1]), null, '普通节点无命中 → null');
    assert.deepEqual(harness.rectOverlapCalls.at(-1), ['I', 10, 200, 180, 110, []], '普通节点用自己的完整矩形探测');
    assert.ok(harness.rectOverlapCalls.length === before + 1);

    // 排除组 id 透传。
    ctx.dragState = { group: [], groupIds: ['X'] };
    pick(ctx.canvasState.nodes[1]);
    assert.deepEqual(harness.rectOverlapCalls.at(-1), ['I', 10, 200, 180, 110, ['X']], 'dragState.groupIds 透传给探测');

    // 多节点组拖动 / 无源节点 → null 且不探测。
    ctx.dragState = { group: ['a', 'b'], groupIds: ['a', 'b'] };
    const probesBefore = harness.rectOverlapCalls.length;
    assert.equal(pick(ctx.canvasState.nodes[0], { x: 60, y: 140 }), null, '组拖动（>1）不连线');
    assert.equal(pick(null, { x: 1, y: 1 }), null, '无源节点 → null');
    assert.equal(harness.rectOverlapCalls.length, probesBefore, '两种情况都不发起探测');
});

test('canAutoConnectDraggedNode: type-legal matrix, history-group/group blocks, self/agent-task rules', () => {
    const { ctx } = loadConnectionsModule();
    const can = globalFunction(ctx, 'canAutoConnectDraggedNode');
    const node = (id, type, extra = {}) => ({ id, type, ...extra });
    const img = (id) => node(id, 'smart-image');

    const cases = [
        [[img('a'), img('b')], true, 'image → image'],
        [[img('a'), node('b', 'smart-loop')], true, 'image → loop'],
        [[img('a'), node('b', 'smart-prompt')], true, 'image → prompt'],
        [[img('a'), node('b', 'smart-conversation')], true, 'image → conversation'],
        [[img('a'), node('b', 'smart-group')], false, 'image → group 禁止（分组不接受输入）'],
        [[img('a'), node('b', 'agent-task')], false, 'image → agent-task 禁止'],
        [[node('a', 'smart-prompt'), img('b')], true, 'prompt → image'],
        [[node('a', 'smart-prompt'), node('b', 'smart-loop')], true, 'prompt → loop'],
        [[node('a', 'smart-prompt'), node('b', 'smart-prompt')], false, 'prompt → prompt 禁止'],
        [[node('a', 'smart-loop'), img('b')], true, 'loop → image'],
        [[node('a', 'smart-loop'), node('b', 'smart-prompt')], false, 'loop → prompt 禁止'],
        [[node('a', 'smart-group'), img('b')], true, 'group → image（展开组成员）'],
        [[node('a', 'smart-group'), node('b', 'smart-loop')], true, 'group → loop'],
        [[node('a', 'smart-group'), node('b', 'smart-prompt')], false, 'group → prompt 禁止'],
        [[node('a', 'local-edit'), img('b')], true, 'local-edit → image'],
        [[node('a', 'local-edit'), node('b', 'smart-loop')], false, 'local-edit → loop 禁止'],
        [[node('a', 'smart-conversation'), node('b', 'smart-prompt')], true, 'conversation → prompt'],
        [[node('a', 'agent'), node('b', 'smart-loop')], true, '字面 "agent" 源（实现查的是字面字符串）→ loop 允许'],
        [[node('a', 'smart-agent'), img('b')], false, 'NODE_TYPE_AGENT("smart-agent") 源在当前实现落到 false（字面 "agent" 分支疑似死分支，见报告）'],
        [[node('a', 'agent-task'), img('b')], false, 'agent-task 是只读结果卡，作源也禁止'],
    ];
    for (const [args, expected, why] of cases) {
        assert.equal(can(...args), expected, why);
    }
    const self = node('a', 'smart-image');
    assert.equal(can(self, self), false, '同节点自连禁止');
    assert.equal(can(null, img('b')), false, '无源节点');
    assert.equal(can(img('a'), null), false, '无目标节点');
    const hist = node('a', 'smart-image', { isHistoryGroup: true });
    assert.equal(can(hist, img('b')), false, 'history 分组节点作源禁止');
    assert.equal(can(img('a'), node('b', 'smart-image', { historyFor: 'x' })), false, 'history 分组节点作目标禁止');
});

// ---------- disconnect ----------

test('disconnectConnections: removes by index spec, cleans inputNodeIds, syncs local-edit, demotes history, records undo', () => {
    const nodes = [
        { id: 'A' },
        { id: 'B', inputNodeIds: ['A'] },
        { id: 'LE', type: 'local-edit', inputNodeIds: ['A'] },
        { id: 'HG', type: 'smart-image', isHistoryGroup: true, historyFor: 'A' },
    ];
    const { ctx, calls } = loadConnectionsModule({
        nodes,
        connections: [
            { from: 'A', to: 'B' },
            { from: 'A', to: 'LE', kind: 'input' },
            { from: 'A', to: 'HG', kind: 'history' },
        ],
    });
    const disconnect = globalFunction(ctx, 'disconnectConnections');

    disconnect('0,2');
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 1, '断开索引 0 和 2');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvas.connections[0].to')), 'LE', '剩下的中间那条');
    assert.deepEqual(nodesInputIds(ctx, 'B'), [], 'B 的 inputNodeIds 去掉 A');
    assert.deepEqual(calls, ['pushUndo', 'clearDetachedRunInputRefs:B', 'demoteHistoryGroupNode:HG', 'render', 'scheduleSave'],
        'flow 线清 run 引用；history 线命中 historyFor 时降级分组；顺序：pushUndo → 清理 → render → scheduleSave');

    disconnect([0]);
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 0, '数组 spec 断开最后一条');
    assert.deepEqual(nodesInputIds(ctx, 'LE'), [], 'local-edit 节点的 inputNodeIds 清理');
    assert.deepEqual(calls,
        ['pushUndo', 'clearDetachedRunInputRefs:B', 'demoteHistoryGroupNode:HG', 'render', 'scheduleSave',
         'pushUndo', 'syncLocalEditInputRoles:LE', 'clearDetachedRunInputRefs:LE', 'render', 'scheduleSave'],
        'input 线对 local-edit 目标追加 syncLocalEditInputRoles');

    const frozen = calls.length;
    disconnect('99,-1,1.5,abc');
    disconnect(null);
    assert.equal(calls.length, frozen, '全非法索引/null spec 无副作用');
    evaluate(ctx, 'canvas = null');
    disconnect('0');
    assert.equal(calls.length, frozen, 'canvas 为 null 时无操作');
});

test('connectionIndexSpecFromPoint: resolves the element under the cursor to its data-conn-index', () => {
    const { ctx, harness } = loadConnectionsModule();
    const spec = globalFunction(ctx, 'connectionIndexSpecFromPoint');

    const hit = connElement(harness, '1,2');
    const plain = harness.makeElement('div');
    plain.closest = () => null;
    harness.hitForPoint = (x, y) => ((x === 1 ? hit : x === 2 ? plain : null));

    assert.equal(spec(1, 0), '1,2', '命中连线元素 → 返回其 connIndex（可多索引）');
    assert.equal(spec(2, 0), '', '命中非连线元素 → 空串');
    assert.equal(spec(3, 0), '', '未命中任何元素 → 空串');
});

// ---------- erase state machine ----------

test('eraseConnectionsAtClientPoint + finishConnectionErase: accumulate marks, then commit with undo', () => {
    const nodes = [{ id: 'A' }, { id: 'B', inputNodeIds: ['A'] }, { id: 'C' }];
    const { ctx, harness, calls } = loadConnectionsModule({
        nodes,
        connections: [
            { from: 'A', to: 'B' },
            { from: 'A', to: 'C' },
        ],
    });
    const eraseAt = globalFunction(ctx, 'eraseConnectionsAtClientPoint');
    const finish = globalFunction(ctx, 'finishConnectionErase');

    assert.equal(eraseAt(0, 0), false, '未开启擦除状态 → false');
    assert.equal(finish(), false, '无状态时 finish → false');

    evaluate(ctx, 'connectionEraseState = { indices: new Set(), started: false, count: 0 }');
    const hit = connElement(harness, '0');
    harness.hitForPoint = () => hit;
    const mark0 = harness.makeElement('path');
    mark0.dataset.connIndex = '0';
    const mark1 = harness.makeElement('path');
    mark1.dataset.connIndex = '1';
    wireWorldQueries(harness, { connEls: [mark0, mark1] });

    assert.equal(eraseAt(5, 5), true, '命中索引 0 → true');
    assert.equal(evaluate(ctx, 'connectionEraseState.indices.size'), 1);
    assert.equal(evaluate(ctx, 'connectionEraseState.started'), true);
    assert.equal(evaluate(ctx, 'connectionEraseState.count'), 1);
    assert.ok(mark0.classList.contains('conn-erasing-mark'), '被擦的连线打上标记 class');
    assert.ok(!mark1.classList.contains('conn-erasing-mark'), '未擦的线不打标记');

    assert.equal(eraseAt(5, 5), false, '同一索引不重复计');
    // 越界索引被滤掉。
    harness.hitForPoint = () => connElement(harness, '5');
    assert.equal(eraseAt(5, 5), false, '索引越界 → false 且不计入');
    assert.equal(evaluate(ctx, 'connectionEraseState.count'), 1);

    assert.equal(finish(), true, 'finish 提交');
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 1, '索引 0 的线被删');
    assert.deepEqual(nodesInputIds(ctx, 'B'), [], '目标节点 inputNodeIds 清理');
    assert.deepEqual(calls, ['pushUndo', 'clearDetachedRunInputRefs:B', 'render'], 'finish 的副作用（无 scheduleSave）');
    // finish 本身不清空 indices 集合（宿主负责重置状态）：再 finish 会删当前索引 0 的线（A→C）。
    assert.equal(finish(), true, '索引集合未清空 → 第二次 finish 删新的索引 0');
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 0, 'A→C 也被删');
    evaluate(ctx, 'connectionEraseState.indices.clear()');
    assert.equal(finish(), false, '索引集合清空后 → false');
});

test('eraseConnectionsAlongPointer: samples the stroke into at most 12 steps, tracks lastX/lastY', () => {
    const { ctx, harness } = loadConnectionsModule({
        nodes: [{ id: 'A' }, { id: 'B' }],
        connections: [{ from: 'A', to: 'B' }],
    });
    const along = globalFunction(ctx, 'eraseConnectionsAlongPointer');
    const finish = globalFunction(ctx, 'finishConnectionErase');

    evaluate(ctx, 'connectionEraseState = { indices: new Set(), started: false, count: 0, lastX: 0, lastY: 0 }');
    let probes = 0;
    harness.hitForPoint = (x) => {
        probes++;
        return (x > 0 && x % 32 === 0) ? connElement(harness, '0') : null;
    };

    assert.equal(along({ clientX: 96, clientY: 0 }), true, '笔画采样命中');
    assert.equal(probes, 12, '96px / 8px = 12 步（上限 12）');
    assert.equal(evaluate(ctx, 'connectionEraseState.lastX'), 96, 'lastX 更新到终点');
    assert.equal(evaluate(ctx, 'connectionEraseState.lastY'), 0);

    // 没有 lastX/lastY 时从当前点起步（单步）。
    evaluate(ctx, 'connectionEraseState = { indices: new Set(), started: false, count: 0 }');
    probes = 0;
    harness.hitForPoint = (x, y) => { probes++; return (x === 10 && y === 20) ? connElement(harness, '0') : null; };
    assert.equal(along({ clientX: 10, clientY: 20 }), true, '无上次坐标 → 单步探测当前点');
    assert.equal(probes, 1);

    assert.equal(finish(), true, '采样命中的线最终被提交删除');
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 0);
    assert.equal(along({ clientX: 0, clientY: 0 }), false, '状态被 finish 消费后…');
    evaluate(ctx, 'connectionEraseState = null');
    assert.equal(along({ clientX: 0, clientY: 0 }), false, '状态为 null → false');
});

test('erase trail: ensure creates one NS svg, update caps the trail at 80 and writes path d, clear fades then removes', () => {
    const { ctx, harness } = loadConnectionsModule();
    const update = globalFunction(ctx, 'updateConnectionEraseTrail');
    const clear = globalFunction(ctx, 'clearConnectionEraseTrail');
    const ensure = globalFunction(ctx, 'ensureConnectionEraseTrail');

    ctx.shell.getBoundingClientRect = () => ({ left: 10, top: 20, width: 800, height: 600 });
    wireShellQueries(harness);
    evaluate(ctx, 'connectionEraseState = { trail: [] }');

    assert.equal(update({ clientX: 110, clientY: 120 }), undefined);
    assert.equal(harness.nsElements.length, 1, '首次 update 创建轨迹 svg');
    const svg = harness.nsElements[0];
    assert.equal(svg.getAttribute('viewBox'), '0 0 800 600', 'viewBox 跟随 shell 尺寸');
    assert.equal(svg.querySelectorAll('path')[0].getAttribute('d'), 'M100.0 100.0', '客户端坐标转 shell 坐标（-left,-top）');
    assert.ok(svg.parentNode === ctx.shell, 'svg 挂在 shell 下');

    assert.equal(ensure() === svg, true, 'ensure 复用已有 svg，不重复创建');
    assert.equal(harness.nsElements.length, 1);

    update({ clientX: 210, clientY: 130 });
    assert.equal(svg.querySelectorAll('path')[0].getAttribute('d'), 'M100.0 100.0 L200.0 110.0', '第二段用 L 续接');
    assert.equal(svg.querySelectorAll('path')[1].getAttribute('d'), 'M100.0 100.0 L200.0 110.0', 'glow 与 line 两条 path 同 d');

    // trail 上限 80。
    for (let i = 0; i < 100; i++) update({ clientX: 10 + i, clientY: 10 });
    assert.equal(evaluate(ctx, 'connectionEraseState.trail.length'), 80, '轨迹最多保留 80 点');

    clear();
    assert.ok(svg.classList.contains('fading'), 'clear 先加 fading class');
    assert.equal(harness.advanceTimers(), 1, '180ms 定时器已登记');
    assert.equal(svg.__removed, true, '定时器触发后 svg 移除');
});

// ---------- geometry helpers ----------

test('connectionMidpoint: flow vs history anchor formulas, null when an endpoint node is missing', () => {
    const { ctx } = loadConnectionsModule({
        nodes: [{ id: 'A', x: 0, y: 0 }, { id: 'B', x: 1000, y: 0, height: 220 }],
    });
    const mid = globalFunction(ctx, 'connectionMidpoint');

    assert.deepEqual(fromContext(mid({ from: 'A', to: 'B' })), { x: 590, y: 82.5 }, 'flow：右缘中点 → 左缘中点的中点');
    assert.deepEqual(fromContext(mid({ from: 'A', to: 'B', kind: 'history' })), { x: 590, y: 55 }, 'history：from 底边中点 → to 顶边中点的中点');
    assert.equal(mid({ from: 'A', to: 'missing' }), null, '目标节点不存在 → null');
    assert.equal(mid(null), null, 'conn 为 null → null');
});

test('insertionConnectionForNode: nearest input/flow midpoint within 96px, skipping own/history/missing', () => {
    const load = (connections) => loadConnectionsModule({
        nodes: [
            { id: 'L', type: 'smart-loop', x: 0, y: 0 },
            { id: 'A', x: 0, y: 0 },
            { id: 'B', x: 1000, y: 0 },
            { id: 'C', x: 0, y: 0 },
            { id: 'D', x: 160, y: 0 },
        ],
        connections,
    });

    const { ctx } = load([
        { from: 'A', to: 'B' },   // 中点 (590,55)，距 loop 中心 500px → 超 96 门
        { from: 'C', to: 'D' },   // 中点 (170,55)，距离 80px → 命中
    ]);
    const pick = globalFunction(ctx, 'insertionConnectionForNode');
    const best = fromContext(pick(ctx.canvasState.nodes.find(n => n.id === 'L')));
    assert.deepEqual(best, { conn: { from: 'C', to: 'D' }, index: 1, score: 80 }, '选 96px 内最近的线，带原始索引');

    assert.equal(pick(ctx.canvasState.nodes.find(n => n.id === 'A')), null, '非 loop 节点 → null');
    const { ctx: ctx2 } = load([{ from: 'L', to: 'C' }]);
    assert.equal(globalFunction(ctx2, 'insertionConnectionForNode')(ctx2.canvasState.nodes.find(n => n.id === 'L')), null, '自身参与的线不参与');
    const { ctx: ctx3 } = load([{ from: 'C', to: 'D', kind: 'history' }]);
    assert.equal(globalFunction(ctx3, 'insertionConnectionForNode')(ctx3.canvasState.nodes.find(n => n.id === 'L')), null, 'history 线不参与');
    const { ctx: ctx4 } = load([{ from: 'C', to: 'ghost' }]);
    assert.equal(globalFunction(ctx4, 'insertionConnectionForNode')(ctx4.canvasState.nodes.find(n => n.id === 'L')), null, '端点缺失的线不参与');
    const { ctx: ctx5 } = load([]);
    assert.equal(globalFunction(ctx5, 'insertionConnectionForNode')(ctx5.canvasState.nodes.find(n => n.id === 'L')), null, '无线可插 → null');
});

// ---------- connectInputNode ----------

test('connectInputNode: read-only agent-task and ignored prompt→conversation toast and return false', () => {
    const { ctx, harness } = loadConnectionsModule({
        nodes: [
            { id: 'F', type: 'smart-image' },
            { id: 'T', type: 'agent-task' },
            { id: 'P', type: 'smart-prompt' },
            { id: 'CV', type: 'smart-conversation' },
        ],
        connections: [],
    });
    const toastEl = harness.element('toast');
    const connect = globalFunction(ctx, 'connectInputNode');

    assert.equal(connect('F', 'T'), false, 'agent-task 只读');
    assert.equal(toastEl.textContent, 'smart.connectAgentTaskReadOnly', '提示 key 精确匹配');
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 0, '未加连线');

    assert.equal(connect('P', 'CV'), false, 'prompt→conversation 不生效');
    assert.equal(toastEl.textContent, 'smart.connectPromptToConversationIgnored', '第二条提示 key');
    assert.equal(fromContext(evaluate(ctx, 'canvas.connections.length')), 0);

    assert.equal(connect('F', 'F'), false, '自连');
    assert.equal(connect('ghost', 'T'), false, '源不存在');
    assert.equal(connect('F', 'ghost'), false, '目标不存在');
});

test('connectInputNode: local-edit routes into original/prompt/reference roles with dedupe', () => {
    const nodes = [
        { id: 'F1', type: 'smart-image' },
        { id: 'F2', type: 'smart-image' },
        { id: 'LE', type: 'local-edit', localEditActiveRole: 'original' },
        { id: 'LE2', type: 'local-edit', localEditActiveRole: 'prompt',
          localEditInputRoles: { originalNode: '', referenceNodes: [], promptNode: 'old', promptNodes: ['old'] } },
        { id: 'LE3', type: 'local-edit', localEditActiveRole: 'references' },
        { id: 'LE4', type: 'local-edit', localEditActiveRole: '' },
    ];
    const { ctx } = loadConnectionsModule({ nodes, connections: [] });
    const connect = globalFunction(ctx, 'connectInputNode');
    const conns = () => fromContext(evaluate(ctx, 'canvas.connections'));

    assert.equal(connect('F1', 'LE4'), false, 'role 未选 → 拒绝');
    assert.equal(connect('F1', 'LE'), true, 'original 角色');
    assert.equal(nodes[2].localEditInputRoles.originalNode, 'F1');
    assert.deepEqual(nodesInputIds(ctx, 'LE'), ['F1'], 'inputNodeIds 同步');
    assert.deepEqual(conns(), [{ from: 'F1', to: 'LE', kind: 'input', role: 'original' }]);

    assert.equal(connect('F2', 'LE'), true, '换 original 源 → 旧的被替换');
    assert.deepEqual(conns().filter(c => c.to === 'LE'), [{ from: 'F2', to: 'LE', kind: 'input', role: 'original' }], 'original 角色唯一（旧的先滤掉）');
    assert.equal(nodes[2].localEditInputRoles.originalNode, 'F2');

    assert.equal(connect('F1', 'LE2'), true, 'prompt 角色');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes.find(n => n.id === "LE2").localEditInputRoles.promptNodes')), ['old', 'F1'], 'promptNodes 去重追加');
    assert.equal(nodes[3].localEditInputRoles.promptNode, 'old', 'promptNode 保持第一个');

    assert.equal(connect('F2', 'LE3'), true, 'references 角色');
    assert.deepEqual(fromContext(evaluate(ctx, 'canvasState.nodes.find(n => n.id === "LE3").localEditInputRoles.referenceNodes')), ['F2'], 'referenceNodes 去重追加');
});

test('connectInputNode: plain target adds an input connection; loop derives imageInput/showPrompt from the source', () => {
    const nodes = [
        { id: 'F', type: 'smart-image' },
        { id: 'P', type: 'smart-prompt' },
        { id: 'G', type: 'smart-group' },
        { id: 'B', type: 'smart-image' },
        { id: 'L1', type: 'smart-loop' },
        { id: 'L2', type: 'smart-loop' },
        { id: 'L3', type: 'smart-loop' },
    ];
    const { ctx, calls } = loadConnectionsModule({ nodes, connections: [] });
    const connect = globalFunction(ctx, 'connectInputNode');
    const conns = () => fromContext(evaluate(ctx, 'canvas.connections'));

    assert.equal(connect('F', 'B'), true, '普通目标');
    assert.deepEqual(nodesInputIds(ctx, 'B'), ['F']);
    assert.deepEqual(conns(), [{ from: 'F', to: 'B', kind: 'input' }], '加 input 连线');
    assert.equal(connect('F', 'B'), true, '重复连接幂等');
    assert.equal(conns().length, 1, 'addConnection 去重');

    assert.equal(connect('F', 'L1'), true, 'image → loop');
    assert.equal(nodes[4].imageInput, true, '图片来源开启 imageInput');
    assert.equal(nodes[4].showPrompt, undefined, '图片来源不开 showPrompt');
    assert.ok(calls.includes('fitSmartLoopNode:L1'), '能力变化触发 fitSmartLoopNode');

    assert.equal(connect('P', 'L2'), true, 'prompt → loop');
    assert.equal(nodes[5].showPrompt, true, 'prompt 来源开启 showPrompt');
    assert.equal(nodes[5].imageInput, undefined);

    assert.equal(connect('G', 'L3'), false, '无图片也无 prompt 的 group → loop 被拒');
    assert.equal(nodes[6].imageInput, undefined, '不设置 imageInput');
    assert.equal(nodes[6].showPrompt, undefined, '不设置 showPrompt');
    assert.ok(!calls.includes('fitSmartLoopNode:L3'), '无能力变化不触发 fit');
});

// ---------- query helpers / cascade keys / misc ----------

test('lineConnectionsFor / outgoingConnectionsFor: kind and direction filters', () => {
    const { ctx } = loadConnectionsModule({
        nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }, { id: 'D' }],
        connections: [
            { from: 'A', to: 'B' },
            { from: 'A', to: 'C', kind: 'input' },
            { from: 'A', to: 'D', kind: 'history' },
            { from: 'A', to: 'A', kind: 'history' },
            { from: null, to: 'B' },
            { from: 'D', to: 'A', kind: 'input' },
        ],
    });
    const lines = globalFunction(ctx, 'lineConnectionsFor');
    const outgoing = globalFunction(ctx, 'outgoingConnectionsFor');
    const A = ctx.canvasState.nodes[0];

    assert.equal(fromContext(lines(A)).length, 3, 'lineConnectionsFor 返回全部 input/flow 且非自连/端点完整的线（A→B、A→C、D→A）');
    assert.equal(fromContext(lines(null)).length, 0, '无节点 → 空');

    assert.equal(fromContext(outgoing(A)).length, 1, '默认 kinds=[input]');
    assert.equal(fromContext(outgoing(A, ['flow'])).length, 1, '指定 flow');
    assert.equal(fromContext(outgoing(A, ['input', 'flow'])).length, 2, '两种都要');
    assert.equal(fromContext(outgoing(ctx.canvasState.nodes[3])).length, 1, 'D 的 outgoing 是 D→A');
    assert.equal(fromContext(outgoing(null)).length, 0, '无节点 → 空');
});

test('cascadeConnectionKeys: graph edges + loop-outgoing + prompt-loop-upstream, gated by active runs when present', () => {
    const { ctx, harness } = loadConnectionsModule({
        nodes: [
            { id: 'L', type: 'smart-loop' },
            { id: 'L2', type: 'smart-loop' },
            { id: 'LP', type: 'smart-loop', showPrompt: true },
            { id: 'A' }, { id: 'B' }, { id: 'C' }, { id: 'D' },
        ],
        connections: [
            { from: 'L', to: 'A', kind: 'flow' },
            { from: 'L', to: 'D', kind: 'flow' },
            { from: 'L', to: 'C', kind: 'history' },
            { from: 'LP', to: 'B', kind: 'input' },
        ],
    });
    const byId = (id) => ctx.canvasState.nodes.find((n) => n.id === id);
    harness.cascadeTails.set('L', { id: 'T' });
    harness.cascadeGraphs.set('T', {
        root: 'A',
        path: [{ id: 'A' }, { id: 'B' }, { id: 'C' }],
        edges: [{ source: { id: 'A' }, target: { id: 'B' } }, { source: { id: 'B' }, target: { id: 'C' } }],
    });
    harness.cascadeTails.set('L2', { id: 'T2' });
    harness.cascadeGraphs.set('T2', {
        root: 'D',
        path: [{ id: 'D' }],
        edges: [{ source: { id: 'D' }, target: { id: 'A' } }],
    });
    harness.inputNodesFor.set('B', [byId('LP')]);
    harness.downstreamByNode.set('D', [byId('C')]);
    const keys = () => fromContext(evaluate(ctx, 'Array.from(cascadeConnectionKeys()).sort()'));

    // 无活动 run → 所有 loop 都参与：图边（A->B、B->C、D->A）+ loop 出边到链上节点（L->A）、
    // 下游落在链上（L->D，D 经 downstream 到 C）+ 链上节点 B 的 showPrompt loop 上游（LP->B）。
    assert.deepEqual(keys(), ['A->B', 'B->C', 'D->A', 'L->A', 'L->D', 'LP->B'], '全量 loop 的边集合');

    // 有活动 run 时只看活动 loop：L2 的图边 D->A 被门控排除，其余保留。
    ctx.smartCascadeRuns = new Map([['L', {}]]);
    assert.deepEqual(keys(), ['A->B', 'B->C', 'L->A', 'L->D', 'LP->B'], '活动 run 门控：非活动 loop 的链不算');
});

test('smartGroupScopeId: member resolves to group id, group node to itself, plain/missing to empty', () => {
    const { ctx, harness } = loadConnectionsModule({
        nodes: [
            { id: 'G', type: 'smart-group', x: 0, y: 0 },
            { id: 'M1', x: 10, y: 10 },
            { id: 'A', x: 20, y: 20 },
        ],
    });
    harness.groupRegistry.set('M1', ctx.canvasState.nodes[0]);
    const scope = globalFunction(ctx, 'smartGroupScopeId');

    assert.equal(scope('M1'), 'G', '成员 → 所在分组 id');
    assert.equal(scope('G'), 'G', '分组本体 → 自身 id');
    assert.equal(scope('A'), '', '普通节点 → 空');
    assert.equal(scope('missing'), '', '不存在 → 空');
});

test('moveSmartNodeAtom + shellPoint: rounded delta translation and shell-relative pointer', () => {
    const { ctx, calls } = loadConnectionsModule({ nodes: [{ id: 'n1', x: 0.2, y: 0 }] });
    const move = globalFunction(ctx, 'moveSmartNodeAtom');
    const shellPoint = globalFunction(ctx, 'shellPoint');
    const n1 = ctx.canvasState.nodes[0];

    move(n1, 100.4, 50.2);
    assert.deepEqual(calls, ['translate:n1:100:50'], '位移四舍五入后委托 translateSmartNodeWithMembers');

    ctx.shell.getBoundingClientRect = () => ({ left: 10, top: 20, width: 800, height: 600 });
    assert.deepEqual(fromContext(shellPoint({ clientX: 110, clientY: 120 })), { x: 100, y: 100 }, '客户端坐标减 shell 偏移');
});
