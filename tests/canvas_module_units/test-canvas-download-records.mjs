// #229 完整性信号 + #228 本地下载记录（tests/canvas_module_units 自动发现）。
//
// #229：后端改成流式打包之后，Content-Length 未必总是有（某张图尺寸读不出来时，后端
// 宁可不发也不发一个错的）。前端的「有没有被截断」判据必须在两种模式下都成立：
//   有 Content-Length → 精确对账；
//   没有 → 用回执里的 expected_bytes 做下界（STORED zip 必然 ≥ 素材字节合计）。
// #228：打包回执只闪一次就没了，用户回头想查「那次到底进了几张」无处可查。
// 这里把每次下载结果落进本地记录（localStorage，有上限），并给可读的一行摘要。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction } from './harness.mjs';

function load({ response = null, storageItems = {} } = {}) {
    const { ctx, harness } = createCanvasContext();
    for (const [key, value] of Object.entries(storageItems)) ctx.localStorage.setItem(key, value);
    if (response) ctx.fetch = async () => response;
    // i18n 资源不在 harness 里，tr() 会退回 key 本身（smart.downloadTruncated → 含 Truncated），
    // 所以下面对文案的断言按 key 的英文词形来写。
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-download.js']);
    return { ctx, harness };
}

function fakeResponse({ ok = true, status = 200, size = 1024, contentLength = null, report = null } = {}) {
    const bag = {};
    if (contentLength !== null) bag['content-length'] = String(contentLength);
    if (report !== null) bag['x-download-report'] = encodeURIComponent(JSON.stringify(report));
    return {
        ok,
        status,
        headers: { get: (name) => (bag[String(name).toLowerCase()] ?? null) },
        blob: async () => ({ size }),
        text: async () => '',
    };
}

const fn = (ctx, name) => globalFunction(ctx, name);

// ---- #229：没有 Content-Length 时仍要能判截断 ------------------------------
test('缺少 Content-Length 时用回执体积做下界，收到明显偏少就报截断', async () => {
    const { ctx } = load({
        response: fakeResponse({
            size: 400,
            report: { total: 2, packed: 2, skipped: [], expected_bytes: 1000, size_known: true },
        }),
    });
    const response = fakeResponse({
        size: 400,
        report: { total: 2, packed: 2, skipped: [], expected_bytes: 1000, size_known: true },
    });
    await assert.rejects(
        () => fn(ctx, 'canvasReceiveBlob')(response),
        (err) => err.reason === 'truncated' && /400/.test(err.message),
        '没有 Content-Length 就不判截断 = 流式打包后 #223 的防线失效',
    );
});

test('没有 Content-Length 但体积够（含 zip 头部开销）时正常放行', async () => {
    const { ctx } = load();
    const response = fakeResponse({
        size: 1180,
        report: { total: 1, packed: 1, skipped: [], expected_bytes: 1000, size_known: true },
    });
    const out = await fn(ctx, 'canvasReceiveBlob')(response);
    assert.equal(out.bytes, 1180);
    assert.equal(out.report.expectedBytes, 1000);
    assert.equal(out.report.sizeKnown, true);
});

test('没有回执也没有 Content-Length 时不做无根据的截断判定', async () => {
    const { ctx } = load();
    const out = await fn(ctx, 'canvasReceiveBlob')(fakeResponse({ size: 900 }));
    assert.equal(out.bytes, 900);
});

test('有 Content-Length 时仍然精确对账（流式改造不能削弱旧防线）', async () => {
    const { ctx } = load();
    const response = fakeResponse({ size: 47_000_000, contentLength: 48 * 1024 * 1024 });
    await assert.rejects(() => fn(ctx, 'canvasReceiveBlob')(response), /截断|truncated/i);
});

// ---- #228：本地下载记录 ---------------------------------------------------
test('每次下载结果落进本地记录，可回查张数与状态', () => {
    const { ctx } = load();
    fn(ctx, 'canvasRecordDownload')({
        filename: 'marked-images.zip',
        bytes: 48_000_000,
        report: { total: 5, packed: 5, skipped: [], expected_bytes: 47_900_000, size_known: true },
        status: 'ok',
    });
    const list = fn(ctx, 'canvasDownloadRecords')();
    assert.equal(list.length, 1);
    assert.equal(list[0].filename, 'marked-images.zip');
    assert.equal(list[0].packed, 5);
    assert.equal(list[0].total, 5);
    assert.equal(list[0].status, 'ok');
});

test('部分失败的记录要留住跳过原因，不只显示成功', () => {
    const { ctx } = load();
    fn(ctx, 'canvasRecordDownload')({
        filename: 'a.zip',
        bytes: 1000,
        report: { total: 21, packed: 20, skipped: [{ name: '海报.png', reason: 'trashed' }], expected_bytes: 900, size_known: true },
        status: 'partial',
    });
    const [rec] = fn(ctx, 'canvasDownloadRecords')();
    assert.equal(rec.packed, 20);
    assert.equal(rec.total, 21);
    // vm context 里的对象与宿主原型不同，strict deepEqual 会判「结构同但非同一引用」；
    // 比 JSON 文本，避开 realm 差异。
    assert.equal(JSON.stringify(rec.skipped), JSON.stringify([{ name: '海报.png', reason: 'trashed' }]));
    const line = fn(ctx, 'canvasFormatDownloadRecord')(rec);
    assert.match(line, /20\/21/, '要能看出进了几张/一共几张');
    assert.match(line, /海报\.png/, '要说清是哪一张');
    assert.match(line, /Trashed/, '跳过原因要走 i18n key，不能被丢掉');
});

test('记录有上限，旧的会被挤掉（不能无限涨）', () => {
    const { ctx } = load();
    for (let i = 0; i < 60; i += 1) {
        fn(ctx, 'canvasRecordDownload')({ filename: `f${i}.zip`, bytes: i, report: null, status: 'ok' });
    }
    const list = fn(ctx, 'canvasDownloadRecords')();
    assert.ok(list.length <= 50, `记录数 ${list.length} 超过上限`);
    assert.equal(list[0].filename, 'f59.zip', '最新一条必须排在最前');
});

test('本地记录坏了也不能抛错', () => {
    const { ctx } = load({ storageItems: { canvasDownloadRecords: '{not json' } });
    assert.equal(JSON.stringify(fn(ctx, 'canvasDownloadRecords')()), '[]');
    fn(ctx, 'canvasRecordDownload')({ filename: 'ok.zip', bytes: 1, report: null, status: 'ok' });
    assert.equal(fn(ctx, 'canvasDownloadRecords')().length, 1);
});

test('失败下载也要记一笔，用户能回查那次到底怎么了', () => {
    const { ctx } = load();
    fn(ctx, 'canvasRecordDownload')({ filename: 'broken.zip', bytes: 0, report: null, status: 'failed', reason: 'truncated' });
    const [rec] = fn(ctx, 'canvasDownloadRecords')();
    assert.equal(rec.status, 'failed');
    assert.equal(rec.reason, 'truncated');
});

test('下载主入口成功与失败都要落记录（否则 #228 的回查是空的）', async () => {
    const { ctx } = load({
        response: fakeResponse({
            size: 2048,
            contentLength: 2048,
            report: { total: 3, packed: 2, skipped: [{ name: 'x.png', reason: 'missing_file' }], expected_bytes: 2000, size_known: true },
        }),
    });
    const hrefs = [];
    ctx.URL = { createObjectURL: () => { const u = 'blob:x/1'; hrefs.push(u); return u; }, revokeObjectURL: () => {} };
    await fn(ctx, 'downloadCanvasArchive')({ filename: 'star.zip', urls: ['/a', '/b', '/c'] });
    const [rec] = fn(ctx, 'canvasDownloadRecords')();
    assert.equal(rec.filename, 'star.zip');
    assert.equal(rec.packed, 2);
    assert.equal(rec.total, 3);
    assert.equal(rec.status, 'partial');
});
