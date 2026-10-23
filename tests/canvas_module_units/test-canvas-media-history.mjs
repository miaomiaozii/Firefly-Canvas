// #224 星标面板显示名单元测试。
//
// 现象（GitHub #224）：星标素材列表里显示的是 64 位十六进制 asset ID，不是可读名字。
//
// 根因（本文件把它钉死）：markedImageItems() 先扫 canvas.history[].outputs 再扫
// 节点 images，并且**先到先得**。生成历史输出只有 {asset_id, url, kind}（后端
// api_project_library.py:808 的 history_record['outputs'] 丢掉了 name），而节点图片
// 带可读名（archive_project_generated_image 返回 name=归档文件名，
// 见 canvas-generation-tasks.js 的 output-N.png 兜底）。于是无名的历史输出把
// 有名的节点图片**遮蔽**了，markedItemDisplayName 只能退回裸 asset_id。
//
// 断言全部基于可观察返回值 / 真实 DOM 文本，不是「调用不抛错」。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate } from './harness.mjs';

const HEX_ID = '3f2a9c8b7d6e5f4a3b2c1d0e9f8a7b6c3f2a9c8b7d6e5f4a3b2c1d0e9f8a7b6c';

function loadMediaHistory({ history = [], nodes = [], marked = [] } = {}) {
    const { ctx, harness } = createCanvasContext();
    // 不设 StudioI18n：走 canvas-core.js 的真实回退分支（tr 返回 key 本身）。
    loadModules(ctx, [
        'canvas-node-types.js',
        'canvas-core.js',
        'canvas-state.js',
        'canvas-media-history.js',
    ]);
    evaluate(ctx, `canvas = ${JSON.stringify({ project_id: 'proj-1', markedAssetIds: marked, history })}`);
    for (const node of nodes) ctx.canvasState.nodes.push(node);
    return { ctx, harness };
}

const fn = (ctx, name) => globalFunction(ctx, name);

// --- 显示名优先级 ------------------------------------------------------
test('markedItemDisplayName: 有 name 用 name，中文/空格名原样保留', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    assert.equal(name({ name: '海报主视觉 终稿.png', url: '/x/y.png', asset_id: HEX_ID }), '海报主视觉 终稿.png');
});

test('markedItemDisplayName: 无 name 时用 URL 里的文件名', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    assert.equal(name({ url: '/output/gen/generated-1.png', asset_id: HEX_ID }), 'generated-1.png');
});

test('markedItemDisplayName: 确实没有任何名字时才退回 asset_id', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    // URL 末段就是裸 asset_id（projectMediaUrl 的形态）→ 不能把 hex 当文件名显示。
    assert.equal(name({ url: `/api/project-library/p1/media/${HEX_ID}`, asset_id: HEX_ID }), HEX_ID);
});

// --- 核心回归：历史输出不得遮蔽带名字的图片条目 -------------------------
test('markedImageItems: 生成历史输出（无名）不得遮蔽带可读名的节点图片', () => {
    const url = `/api/project-library/p1/media/${HEX_ID}`;
    const { ctx } = loadMediaHistory({
        marked: [HEX_ID],
        // 后端落库的生成历史输出：只有 asset_id/kind/url，没有 name。
        history: [{ id: 'gen-1', outputs: [{ asset_id: HEX_ID, kind: 'image', url }] }],
        // 同一个素材在节点上带归档名。
        nodes: [{ id: 'n1', images: [{ asset_id: HEX_ID, url, name: '生成结果-01.png', kind: 'image' }] }],
    });
    const items = fn(ctx, 'markedImageItems')();
    assert.equal(items.length, 1);
    assert.equal(items[0].name, '生成结果-01.png',
        '无名历史输出先到先得，会把带名的节点图片遮蔽掉 → 面板只能显示裸 hex');
    assert.equal(fn(ctx, 'markedItemDisplayName')(items[0]), '生成结果-01.png');
});

test('markedImageItems: 只有无名历史输出时仍然返回该项（不能整项丢掉）', () => {
    const url = `/api/project-library/p1/media/${HEX_ID}`;
    const { ctx } = loadMediaHistory({
        marked: [HEX_ID],
        history: [{ id: 'gen-1', outputs: [{ asset_id: HEX_ID, kind: 'image', url }] }],
        nodes: [],
    });
    const items = fn(ctx, 'markedImageItems')();
    assert.equal(items.length, 1, '没有任何名字不等于素材不可用');
    assert.equal(items[0].url, url);
});

// --- 悬停必须能看到完整名字 --------------------------------------------
test('renderSmartMarkedPanel: title 带完整名字，不被截断', () => {
    const url = `/api/project-library/p1/media/${HEX_ID}`;
    const { ctx, harness } = loadMediaHistory({
        marked: [HEX_ID],
        history: [],
        nodes: [{ id: 'n1', images: [{ asset_id: HEX_ID, url, name: '一张非常长的中文素材名字-最终确认稿-v12.png', kind: 'image' }] }],
    });
    const list = harness.element('smartMarkedList');
    harness.element('smartMarkedCount');
    fn(ctx, 'renderSmartMarkedPanel')();
    const html = String(list.innerHTML || '');
    assert.ok(html.includes('title="一张非常长的中文素材名字-最终确认稿-v12.png"'),
        `title 必须是完整名字，实际渲染：${html}`);
    // data-asset-id / img src 里出现 hex 是正常的；只能断言**显示名文本**不能是裸 asset_id。
    const shown = /<span class="smart-marked-name"[^>]*>([^<]*)</.exec(html);
    assert.ok(shown, `没有渲染出 .smart-marked-name：${html}`);
    assert.equal(shown[1], '一张非常长的中文素材名字-最终确认稿-v12.png');
});
