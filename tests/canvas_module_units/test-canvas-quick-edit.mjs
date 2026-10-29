// #253 canvas-quick-edit.js 单元测试：右键操作卡可见性 + 重命名目标判定（纯函数）。
//
// 该模块是纯函数 IIFE（window.FireflyQuickEdit）+ 独立 DOM 层（openQuickEdit，回调式接线），
// 不依赖其他画布模块（反向引用棘轮 0），测试单独加载它。
//
// 防的回归（全部断言可观察输出）：
//   - operationCardVisibility：重命名卡仅右键落在节点上可见；整理排版卡仅节点数 >=2 可见
//     （arrangeSelectedSmartNodes 对 <2 个是 no-op，卡片不该出现）；
//   - quickEditRenameTarget：图片节点改首图名（无图不可改名）；其余节点类型改 node.title；
//     imageType 参数显式传入时的判定；非法输入不抛错。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, fromContext } from './harness.mjs';

function loadQuickEdit() {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-quick-edit.js']);
    return ctx.FireflyQuickEdit;
}

test('operationCardVisibility: rename card only on a node context', () => {
    const api = loadQuickEdit();
    assert.deepEqual(fromContext(api.operationCardVisibility({ nodeId: 'n1', nodeCount: 0 })), { rename: true, tidy: false });
    assert.deepEqual(fromContext(api.operationCardVisibility({ nodeId: '', nodeCount: 1 })), { rename: false, tidy: false });
    assert.deepEqual(fromContext(api.operationCardVisibility({})), { rename: false, tidy: false });
    assert.deepEqual(fromContext(api.operationCardVisibility(null)), { rename: false, tidy: false });
});

test('operationCardVisibility: tidy card only with >=2 nodes', () => {
    const api = loadQuickEdit();
    assert.equal(api.operationCardVisibility({ nodeId: '', nodeCount: 0 }).tidy, false);
    assert.equal(api.operationCardVisibility({ nodeId: '', nodeCount: 1 }).tidy, false);
    assert.equal(api.operationCardVisibility({ nodeId: '', nodeCount: 2 }).tidy, true);
    assert.equal(api.operationCardVisibility({ nodeId: 'g1', nodeCount: 3 }).tidy, true);
    // 非数字/缺失 nodeCount 按 0 处理。
    assert.equal(api.operationCardVisibility({ nodeId: '', nodeCount: '2' }).tidy, true);
    assert.equal(api.operationCardVisibility({ nodeId: '', nodeCount: null }).tidy, false);
});

test('quickEditRenameTarget: image nodes rename the first image name', () => {
    const api = loadQuickEdit();
    const IMAGE = 'smart-image';
    assert.deepEqual(fromContext(api.quickEditRenameTarget({ type: IMAGE, images: [{ url: 'x' }] }, IMAGE)), { kind: 'image' });
    assert.equal(api.quickEditRenameTarget({ type: IMAGE, images: [] }, IMAGE), null);
    assert.equal(api.quickEditRenameTarget({ type: IMAGE, images: null }, IMAGE), null);
    assert.equal(api.quickEditRenameTarget(null, IMAGE), null);
    assert.equal(api.quickEditRenameTarget('not-an-object', IMAGE), null);
});

test('quickEditRenameTarget: non-image node types rename node.title', () => {
    const api = loadQuickEdit();
    const IMAGE = 'smart-image';
    assert.deepEqual(fromContext(api.quickEditRenameTarget({ type: 'smart-prompt', title: 'p' }, IMAGE)), { kind: 'title' });
    assert.deepEqual(fromContext(api.quickEditRenameTarget({ type: 'smart-group', title: 'g', images: [{ url: 'x' }] }, IMAGE)), { kind: 'title' });
    assert.deepEqual(fromContext(api.quickEditRenameTarget({ type: 'smart-loop' }, IMAGE)), { kind: 'title' });
});

test('quickEditRenameTarget: default imageType accepts the real constants', () => {
    const api = loadQuickEdit();
    assert.deepEqual(fromContext(api.quickEditRenameTarget({ type: 'smart-image', images: [{ url: 'x' }] })), { kind: 'image' });
    assert.deepEqual(fromContext(api.quickEditRenameTarget({ type: undefined, images: [{ url: 'x' }] })), { kind: 'image' });
    assert.deepEqual(fromContext(api.quickEditRenameTarget({ type: 'smart-prompt', images: [{ url: 'x' }] })), { kind: 'title' });
});
