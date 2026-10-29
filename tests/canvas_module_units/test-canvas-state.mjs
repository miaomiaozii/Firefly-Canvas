// #194 canvas-state.js 行为单元测试。
// 防的回归：画布共享可变状态容器的字段集与默认值漂移。canvas-state.js 只有一行
// `window.canvasState = {...}`，但它是 13 个跨模块可变状态的唯一归属地——漏一个
// 字段（或改默认值）不会在任何单个模块里报错，而是等 canvas-undo / canvas-node-ops
// 之类读它的模块在运行时炸。这里断言完整字段集 + 每个默认值，并验证它确实挂在
// 全局 window 上、且 canvas-core.js 的 selectedNode() 读到的是同一个对象引用。
import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

test('loads standalone and attaches canvasState to window with the 13 documented fields', () => {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-state.js']);
    const state = ctx.canvasState;
    assert.ok(state && typeof state === 'object', 'window.canvasState must exist after loading');
    // vm context 是独立 V8 realm：里面产出的对象原型与宿主侧不同，而
    // assert/strict 的 deepEqual 原型敏感——值断言前必须 fromContext 往返回宿主 realm。
    const snap = fromContext(state);
    assert.deepEqual(
        Object.keys(snap).sort(),
        [
            'activeComposerSubject',
            'connectionLayerRaf',
            'createMenuGroupId',
            'lastComposerNodeId',
            'nodes',
            'projectMediaLibrary',
            'selectedId',
            'selectedIds',
            'selectedImage',
            'smartHistoryDrawerCollapsed',
            'smartHistoryPointerState',
            'smartHistorySelectedImage',
            'smartHistoryTap',
        ],
        'field set must match the P2e contract exactly (no more, no less)',
    );
    assert.equal(snap.smartHistoryDrawerCollapsed, false);
    assert.equal(snap.smartHistoryPointerState, null);
    assert.equal(snap.smartHistoryTap, null);
    assert.equal(snap.smartHistorySelectedImage, null);
    assert.deepEqual(snap.nodes, []);
    assert.equal(snap.selectedId, '');
    assert.deepEqual(snap.selectedIds, []);
    assert.deepEqual(snap.selectedImage, { nodeId: '', index: -1 });
    assert.deepEqual(snap.projectMediaLibrary, { items: [] });
    assert.equal(snap.createMenuGroupId, '');
    assert.equal(snap.connectionLayerRaf, 0);
    assert.equal(snap.lastComposerNodeId, '');
    assert.equal(snap.activeComposerSubject, null);
    // 数组必须是各自独立的实例，否则一个模块 push 会污染另一个。
    // 必须在原始跨 realm 对象上比引用，归一化后永远是两个不同的 host 对象。
    // （自旧 canvas_state_units.mjs 移植：初始值 + 独立性是「共享状态容器」契约的一半。）
    assert.notEqual(state.nodes, state.selectedIds);
    assert.notEqual(state.nodes, state.projectMediaLibrary.items);
});

test('canvas-core.selectedNode() reads the SAME canvasState object, not a copy', () => {
    // 页面真实加载顺序（smart-canvas.html）：canvas-node-types -> canvas-core -> canvas-state。
    // selectedNode 在 canvas-core.js 里裸引用 canvasState（window 属性），
    // 必须读到 canvas-state.js 挂上去的同一个容器——引用相等是跨模块共享状态的硬契约。
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js']);
    const selectedNode = globalFunction(ctx, 'selectedNode');

    assert.equal(selectedNode(), null, 'fresh state: no selection');

    const node = { id: 'node-1', type: 'smart-image' };
    ctx.canvasState.nodes.push(node);
    ctx.canvasState.selectedId = 'node-1';
    assert.strictEqual(selectedNode(), node, 'must return the pushed object by reference');

    ctx.canvasState.selectedId = 'does-not-exist';
    assert.equal(selectedNode(), null, 'unknown selectedId falls back to null');

    ctx.canvasState.nodes.push({ id: 'node-2', type: 'smart-prompt' });
    ctx.canvasState.selectedId = 'node-2';
    assert.equal(selectedNode().type, 'smart-prompt');
    // canvas-core 的其他顶层名字也在同一共享词法作用域里可读（同域共存守卫，
    // 自旧 canvas_state_units.mjs 移植：core 若重复定义 state 的名字，后加载
    // 的文件会解析失败，页面节点全空而 pageErrors 为空）。
    for (const name of ['escapeHtml', 'uid', 'nowMs', 'smartGenerationSubmissions']) {
        assert.notEqual(evaluate(ctx, name), undefined, `canvas-core.js 的 ${name} 读不到`);
    }
});

test('container is mutable in place: nested objects keep identity across reads', () => {
    // 消费方（canvas-undo 的快照、canvas-node-ops 的 upsert）依赖的是引用稳定性：
    // 反复读 canvasState.selectedImage / projectMediaLibrary 必须拿到同一个对象。
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-state.js']);
    const firstImage = ctx.canvasState.selectedImage;
    const firstLibrary = ctx.canvasState.projectMediaLibrary;
    assert.strictEqual(ctx.canvasState.selectedImage, firstImage);
    assert.strictEqual(ctx.canvasState.projectMediaLibrary, firstLibrary);
    firstImage.nodeId = 'n9';
    firstImage.index = 3;
    // 引用断言（strictEqual）跨 realm 直接成立；值断言先 fromContext 往返。
    assert.deepEqual(fromContext(ctx.canvasState.selectedImage), { nodeId: 'n9', index: 3 });
    firstLibrary.items.push({ id: 'm1' });
    assert.deepEqual(fromContext(ctx.canvasState.projectMediaLibrary.items), [{ id: 'm1' }]);
});
