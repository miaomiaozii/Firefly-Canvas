// #194：canvas-node-types.js 与 canvas-state.js 的行为单测。
//
// 这两个文件是「所有模块都依赖、但没人测」的最底层。#173 的实测结论是
// 拆出的模块反向引用 smart-canvas.js 达 1697 处；把常量和共享状态沉到
// canvas-core.js 之后，这里就是那条依赖链的地基。地基上少一个键，症状是
// 远处某个模块读到 undefined，排查成本极高。

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadCanvasScripts, CANVAS_SCRIPT_ORDER, normalizeValue } from './harness.mjs';

const { context, take } = loadCanvasScripts(CANVAS_SCRIPT_ORDER);

test('节点类型常量的值必须与后端 app/constants.py 一致（此处钉字面量）', () => {
    const types = take([
        'NODE_TYPE_PROMPT', 'NODE_TYPE_IMAGE', 'NODE_TYPE_AGENT', 'NODE_TYPE_CONVERSATION',
        'NODE_TYPE_TASK', 'NODE_TYPE_LOOP', 'NODE_TYPE_GROUP', 'NODE_TYPE_LOCAL_EDIT',
    ]);
    assert.deepEqual(types, {
        NODE_TYPE_PROMPT: 'smart-prompt',
        NODE_TYPE_IMAGE: 'smart-image',
        NODE_TYPE_AGENT: 'smart-agent',
        NODE_TYPE_CONVERSATION: 'smart-conversation',
        NODE_TYPE_TASK: 'agent-task',
        NODE_TYPE_LOOP: 'smart-loop',
        NODE_TYPE_GROUP: 'smart-group',
        NODE_TYPE_LOCAL_EDIT: 'local-edit',
    });
});

test('FireflyNodeTypes 必须挂到 globalThis，且覆盖全部 8 个类型', () => {
    // 顶层 const 不是 globalThis 属性，模块靠文件末尾那段显式挂载共享。
    const shared = normalizeValue(context.FireflyNodeTypes);
    assert.ok(shared, 'globalThis.FireflyNodeTypes 未挂载');
    assert.deepEqual(
        Object.keys(shared).sort(),
        [
            'NODE_TYPE_AGENT', 'NODE_TYPE_CONVERSATION', 'NODE_TYPE_GROUP', 'NODE_TYPE_IMAGE',
            'NODE_TYPE_LOCAL_EDIT', 'NODE_TYPE_LOOP', 'NODE_TYPE_PROMPT', 'NODE_TYPE_TASK',
        ],
    );
});

test('共享可变状态容器的键集合不得增删（增删必须显式改这条断言）', () => {
    const state = normalizeValue(context.window.canvasState);
    assert.ok(state, 'window.canvasState 未初始化');
    assert.deepEqual(Object.keys(state).sort(), [
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
    ]);
});

test('共享状态初始值：不得把 null 变成 undefined，也不得共享同一个数组实例', () => {
    const rawState = context.window.canvasState;
    const state = normalizeValue(rawState);
    assert.equal(state.smartHistoryDrawerCollapsed, false);
    assert.equal(state.smartHistoryPointerState, null);
    assert.equal(state.smartHistoryTap, null);
    assert.equal(state.smartHistorySelectedImage, null);
    assert.equal(state.activeComposerSubject, null);
    assert.equal(state.selectedId, '');
    assert.equal(state.createMenuGroupId, '');
    assert.equal(state.connectionLayerRaf, 0);
    assert.deepEqual(state.nodes, []);
    assert.deepEqual(state.selectedIds, []);
    assert.deepEqual(state.selectedImage, { nodeId: '', index: -1 });
    assert.deepEqual(state.projectMediaLibrary, { items: [] });
    // 数组必须是各自独立的实例，否则一个模块 push 会污染另一个。
    // 必须在原始跨 realm 对象上比引用，归一化后永远是两个不同的 host 对象。
    assert.notEqual(rawState.nodes, rawState.selectedIds);
    assert.notEqual(rawState.nodes, rawState.projectMediaLibrary.items);
});

test('canvas-core.js 必须在 canvas-state.js 之前加载且可共存于同一作用域', () => {
    // 顺序守卫的另一半：如果 core 里重复定义了 state 的名字，整个后加载的
    // 文件会解析失败（实测症状是页面节点全空、pageErrors 为空）。
    assert.ok(context.window.canvasState, 'canvas-state.js 未生效，疑似解析失败');
    const coreNames = take(['smartGenerationSubmissions', 'escapeHtml', 'uid', 'nowMs']);
    for (const [name, value] of Object.entries(coreNames)) {
        assert.notEqual(value, undefined, `canvas-core.js 的 ${name} 读不到`);
    }
});
