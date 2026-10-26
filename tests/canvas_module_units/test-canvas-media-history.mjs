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
    // 这里用真实文件名：URL 末段是 generated-N.png 时要走去歧义分支，
    // 那个行为在下面单独钉（「名字来自 URL 末段时同样要去歧义」）。
    assert.equal(name({ url: '/output/gen/DSC08791.png', asset_id: HEX_ID }), 'DSC08791.png');
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

// --- 生成器默认名去歧义（#224 存量回填后暴露的问题，用户选定方案 1）----
// 回填补回来的名字全是生成器默认名 generated-N.png/.jpg/.webp/.gif
// （名字由 app/core.py 的 generated_image_filename(index, mime_type) 生成）。
// 实测 1053 个输出项只有 14 个不同名字，generated-1.jpg 一个就占 479 条：
// 名字本身不再唯一，光看列表分不出是哪张。所以默认名要带 asset_id 短前缀。
// 真实文件名（DSC08791.jpg）不加后缀，保持干净。

const ID_A = '0f02737013cc4b4c9af179c5b8459b84';   // #223/#224 复现里出现过的真实 ID
const ID_B = 'a1b2c3d4e5f60718293a4b5c6d7e8f90';
const mediaUrl = id => `/api/project-library/p1/media/${id}`;

test('markedItemDisplayName: 生成器默认名要带 asset_id 短前缀去歧义', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    assert.equal(
        name({ name: 'generated-1.jpg', url: mediaUrl(ID_A), asset_id: ID_A }),
        'generated-1.jpg · 0f0273',
    );
});

test('markedItemDisplayName: 两个同名默认名必须显示成不同文本', () => {
    // 这是方案 1 的全部意义：光看名字分不出 479 张 generated-1.jpg 里的哪一张。
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    const first = name({ name: 'generated-1.jpg', url: mediaUrl(ID_A), asset_id: ID_A });
    const second = name({ name: 'generated-1.jpg', url: mediaUrl(ID_B), asset_id: ID_B });
    assert.notEqual(first, second, `同名默认名显示成了一样的：${first}`);
    assert.ok(first.includes('0f0273') && second.includes('a1b2c3'));
});

test('markedItemDisplayName: generated-N 的扩展名变体都算默认名', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    for (const ext of ['png', 'jpg', 'jpeg', 'webp', 'gif']) {
        const shown = name({ name: `generated-12.${ext}`, url: mediaUrl(ID_A), asset_id: ID_A });
        assert.ok(shown.endsWith(' · 0f0273'), `generated-12.${ext} 没加后缀：${shown}`);
    }
});

test('markedItemDisplayName: 真实文件名不加后缀，保持干净', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    for (const real of ['DSC08791.jpg', 'generated-1-final.jpg', 'generated_image-1.jpg', '海报 generated-1.jpg']) {
        assert.equal(name({ name: real, url: mediaUrl(ID_A), asset_id: ID_A }), real,
            `${real} 不是生成器默认名，不该被改`);
    }
});

test('markedItemDisplayName: 没有任何名字时仍只给 asset_id，不要拼成「id · id」', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    assert.equal(name({ url: mediaUrl(ID_A), asset_id: ID_A }), ID_A);
});

test('markedItemDisplayName: 名字来自 URL 末段（generated-N.png）时同样要去歧义', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    assert.equal(
        name({ url: '/media/outputs/generated-3.png', asset_id: ID_A }),
        'generated-3.png · 0f0273',
    );
});

test('renderSmartMarkedPanel: 同名默认名在列表里渲染成两行不同文本', () => {
    const { ctx, harness } = loadMediaHistory({
        marked: [ID_A, ID_B],
        history: [
            { outputs: [{ asset_id: ID_A, url: mediaUrl(ID_A), kind: 'image', name: 'generated-1.jpg' }] },
            { outputs: [{ asset_id: ID_B, url: mediaUrl(ID_B), kind: 'image', name: 'generated-1.jpg' }] },
        ],
        nodes: [],
    });
    const list = harness.element('smartMarkedList');
    harness.element('smartMarkedCount');
    fn(ctx, 'renderSmartMarkedPanel')();
    const shown = [...String(list.innerHTML || '').matchAll(/<span class="smart-marked-name"[^>]*>([^<]*)</g)]
        .map(match => match[1]);
    assert.equal(shown.length, 2, `两行都没渲染出来：${list.innerHTML}`);
    assert.notEqual(shown[0], shown[1], `两行同名默认名显示成一样了：${shown.join(' | ')}`);
    assert.ok(shown.every(text => text.startsWith('generated-1.jpg · ')), `缺后缀：${shown.join(' | ')}`);
});
