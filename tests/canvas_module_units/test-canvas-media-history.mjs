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

// --- 生成器默认名去歧义（#224 存量回填后暴露，用户定的方案：带生成时间）----
// 回填补回来的名字全是生成器默认名 generated-N.png/.jpg/.webp/.gif
// （名字由 app/core.py 的 generated_image_filename 产出）。实测 1053 个输出项
// 只有 14 个不同名字，generated-1.jpg 一种就占 479 条 —— 光看名字根本分不出是哪张。
// 用户否决了「用提示词当名字」（提示词高度模板化），要求引入时间日期。
// 时间来源：history 记录的 created_at/createdAt（firefly_projects.py:1232 会把
// history 整份挂到 canvas.history 上给前端，毫秒字符串）。outputs 本身没有时间字段，
// 所以 markedImageItems 必须把记录时间带到素材上。
// 时间标签不断言具体值（依赖本机时区），只钉格式与「不同时间必须不同文本」。

const ID_A = '0f02737013cc4b4c9af179c5b8459b84';   // #223/#224 复现里出现过的真实 ID
const ID_B = 'a1b2c3d4e5f60718293a4b5c6d7e8f90';
const mediaUrl = id => `/api/project-library/p1/media/${id}`;
// 真实历史记录里的 created_at（毫秒字符串）。
const CREATED_A = '1792383388944';
const CREATED_B = '1792470420000';
const MOMENT_LABEL = /\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/;

test('markedImageItems: 必须把历史记录的 created_at 带到素材上（名字里没有别的时间来源）', () => {
    const { ctx } = loadMediaHistory({
        marked: [ID_A],
        history: [{ created_at: CREATED_A, outputs: [{ asset_id: ID_A, url: mediaUrl(ID_A), kind: 'image', name: 'generated-1.jpg' }] }],
        nodes: [],
    });
    const [item] = fn(ctx, 'markedImageItems')();
    assert.ok(item, '素材项丢了');
    assert.ok(Number(item.createdAt) > 0, `素材上没有生成时间：${JSON.stringify(item)}`);
});

test('markedItemDisplayName: 生成器默认名要带生成时间去歧义', () => {
    const { ctx } = loadMediaHistory();
    const shown = fn(ctx, 'markedItemDisplayName')({
        name: 'generated-1.jpg', url: mediaUrl(ID_A), asset_id: ID_A, createdAt: CREATED_A,
    });
    assert.match(shown, /^generated-1\.jpg · \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/, `时间后缀不对：${shown}`);
});

// --- 占位名去重（#224 第二轮，真实数据实测）--------------------------------
// 上面的时间后缀只到分钟，而一批图是同一分钟连发的：实测一个项目里
// 「generated-1.jpg · 2026-10-19 12:16」这一个文本就吃了 8 张。
// 更硬的根因：media/index.json 里的 name 是**供应商给的**占位名（source.name），
// 这家供应商把每张图都叫 generated-1.png——实测四个不同 asset 的 index name
// 分别是 generated-1.png / generated-1.png / generated-1.png / generated-4.png。
// 所以下面钉三条：同批要能分、占位名不能独挡、真实名不受影响。

test('markedItemDisplayName: 同一分钟的占位名要用批次序号分开', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    const first = name({ name: 'generated-1.png', url: mediaUrl(ID_A), asset_id: ID_A, createdAt: CREATED_A, output_order: 0 });
    const second = name({ name: 'generated-1.png', url: mediaUrl(ID_B), asset_id: ID_B, createdAt: CREATED_A, output_order: 1 });
    assert.notEqual(first, second, `同批两张显示成了一样：${first} / ${second}`);
});

test('markedImageItems + 显示名：真实形状（4 个同名输出同一记录）必须两两不同', () => {
    const moment = '1792395041104';
    const ids = ['a942c052e0a1bd5d7cdf915feb31b94991b75e54099067c15c822d42801447f8',
                 '20d9bc966f73ca422b92b3e2b45e4865f8faf7a0cbfe4bae2d9b3e41f389a0eb',
                 '2d96f9a02def7547c573edc8404cd7ee717dfbb7d537928ab337c492c9028d43',
                 'd44dfffd0f66860adcbedf8fc858caf21e5062b244484514bcbcd85df80f47cb'];
    const { ctx } = loadMediaHistory({
        marked: ids,
        history: [{
            created_at: moment,
            outputs: ids.map((id, i) => ({
                asset_id: id, url: mediaUrl(id), kind: 'image',
                name: i === 3 ? 'generated-4.png' : 'generated-1.png', output_order: i,
            })),
        }],
        nodes: [],
    });
    const shown = fn(ctx, 'markedImageItems')().map(item => fn(ctx, 'markedItemDisplayName')(item));
    assert.equal(shown.length, 4, `丢了项：${JSON.stringify(shown)}`);
    assert.equal(new Set(shown).size, 4, `显示名重复：${JSON.stringify(shown)}`);
});

test('markedItemDisplayName: 真实文件名不加任何后缀（占位名才去歧义）', () => {
    const { ctx } = loadMediaHistory();
    const shown = fn(ctx, 'markedItemDisplayName')({
        name: 'DSC08791.jpg', url: '/output/gen/DSC08791.jpg', asset_id: ID_A, createdAt: CREATED_A, output_order: 2,
    });
    assert.equal(shown, 'DSC08791.jpg', `真实名被加了后缀：${shown}`);
});

test('markedItemDisplayName: 同名默认名但生成时间不同 → 文本必须不同', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    const first = name({ name: 'generated-1.jpg', url: mediaUrl(ID_A), asset_id: ID_A, createdAt: CREATED_A });
    const second = name({ name: 'generated-1.jpg', url: mediaUrl(ID_B), asset_id: ID_B, createdAt: CREATED_B });
    assert.notEqual(first, second, `不同时间显示成了一样：${first}`);
});

test('markedItemDisplayName: generated-N 的各扩展名都算默认名', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    for (const ext of ['png', 'jpg', 'jpeg', 'webp', 'gif']) {
        const shown = name({ name: `generated-12.${ext}`, url: mediaUrl(ID_A), asset_id: ID_A, createdAt: CREATED_A });
        assert.match(shown, new RegExp(`^generated-12\.${ext} · `), `generated-12.${ext} 没加时间：${shown}`);
        assert.match(shown, MOMENT_LABEL, `时间标签格式不对：${shown}`);
    }
});

test('markedItemDisplayName: 没有时间信息时才退回 asset_id 短前缀（不能退回裸名字）', () => {
    const { ctx } = loadMediaHistory();
    const shown = fn(ctx, 'markedItemDisplayName')({ name: 'generated-1.jpg', url: mediaUrl(ID_A), asset_id: ID_A });
    assert.equal(shown, 'generated-1.jpg · 0f0273', `没有时间就该用短前缀兜底：${shown}`);
});

test('markedItemDisplayName: 真实文件名不加任何后缀', () => {
    const { ctx } = loadMediaHistory();
    const name = fn(ctx, 'markedItemDisplayName');
    for (const real of ['DSC08791.jpg', 'generated-1-final.jpg', 'generated_image-1.jpg', '海报 generated-1.jpg']) {
        assert.equal(name({ name: real, url: mediaUrl(ID_A), asset_id: ID_A, createdAt: CREATED_A }), real,
            `${real} 不是生成器默认名，不该被改`);
    }
});

test('markedItemDisplayName: 没有任何名字时仍只给 asset_id，不要拼成「id · 时间」', () => {
    const { ctx } = loadMediaHistory();
    assert.equal(fn(ctx, 'markedItemDisplayName')({ url: mediaUrl(ID_A), asset_id: ID_A, createdAt: CREATED_A }), ID_A);
});

test('markedItemDisplayName: 名字来自 URL 末段（generated-N.png）时同样要带时间', () => {
    const { ctx } = loadMediaHistory();
    const shown = fn(ctx, 'markedItemDisplayName')({ url: '/media/outputs/generated-3.png', asset_id: ID_A, createdAt: CREATED_A });
    assert.match(shown, /^generated-3\.png · \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/, `没带时间：${shown}`);
});

test('renderSmartMarkedPanel: 同名的默认名必须在列表里显示成不同文本', () => {
    const { ctx, harness } = loadMediaHistory({
        marked: [ID_A, ID_B],
        history: [
            { created_at: CREATED_A, outputs: [{ asset_id: ID_A, url: mediaUrl(ID_A), kind: 'image', name: 'generated-1.jpg' }] },
            { created_at: CREATED_B, outputs: [{ asset_id: ID_B, url: mediaUrl(ID_B), kind: 'image', name: 'generated-1.jpg' }] },
        ],
        nodes: [],
    });
    const list = harness.element('smartMarkedList');
    harness.element('smartMarkedCount');
    fn(ctx, 'renderSmartMarkedPanel')();
    const shown = [...String(list.innerHTML || '').matchAll(/<span class="smart-marked-name"[^>]*>([^<]*)</g)]
        .map(match => match[1]);
    assert.equal(shown.length, 2, `两行都没渲染出来：${list.innerHTML}`);
    assert.notEqual(shown[0], shown[1], `同名默认名显示成一样了：${shown.join(' | ')}`);
});

test('renderSmartMarkedPanel: 同一分钟生成的同名默认名要再退回短前缀区分', () => {
    const sameMinuteB = String(Number(CREATED_A) + 25000);   // 同一分钟内的第二次生成（本分钟第 28.9s → 53.9s）
    const { ctx, harness } = loadMediaHistory({
        marked: [ID_A, ID_B],
        history: [
            { created_at: CREATED_A, outputs: [{ asset_id: ID_A, url: mediaUrl(ID_A), kind: 'image', name: 'generated-1.jpg' }] },
            { created_at: sameMinuteB, outputs: [{ asset_id: ID_B, url: mediaUrl(ID_B), kind: 'image', name: 'generated-1.jpg' }] },
        ],
        nodes: [],
    });
    const list = harness.element('smartMarkedList');
    harness.element('smartMarkedCount');
    fn(ctx, 'renderSmartMarkedPanel')();
    const shown = [...String(list.innerHTML || '').matchAll(/<span class="smart-marked-name"[^>]*>([^<]*)</g)]
        .map(match => match[1]);
    assert.equal(shown.length, 2);
    assert.notEqual(shown[0], shown[1], `同一分钟的同名默认名没区分开：${shown.join(' | ')}`);
    assert.ok(shown.every(text => /(?:0f0273|a1b2c3)$/.test(text)), `没退回短前缀：${shown.join(' | ')}`);
});

test('markedImageItems: 节点图片自己没有更新时间时，要向同 asset_id 的生成记录借时间', () => {
    // 真实形态：磁盘上的 history outputs 和节点图片都**没有 url**（url 由前端
    // hydrateProjectMediaReferences 补，smart-canvas.js:5682），而补 url 不会带时间。
    // 于是「只有节点图片能进列表」的素材拿不到 createdAt，日期显示会退化成短前缀。
    const { ctx } = loadMediaHistory({
        marked: [ID_A],
        history: [{ created_at: CREATED_A, outputs: [{ asset_id: ID_A, kind: 'image', name: 'generated-1.jpg' }] }],
        nodes: [{ id: 'n1', images: [{ asset_id: ID_A, url: mediaUrl(ID_A), kind: 'image', name: 'generated-1.jpg' }] }],
    });
    const [item] = fn(ctx, 'markedImageItems')();
    assert.ok(item, '素材项丢了');
    assert.ok(Number(item.createdAt) > 0, `没从生成记录借到时间：${JSON.stringify(item)}`);
});
