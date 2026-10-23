// #223 下载层单元测试：把服务端 zip 安全落成本地文件。
//
// 现象（GitHub #223）：批量下载星标原图，只得到一个 0 字节 zip，用户以为下载成功了。
// 单张 9–10MB、合计 48MB；服务端直接请求同一端点能返回 200 + 48MB 有效 zip
// → 故障在浏览器接收/落盘侧，不在服务端。
//
// 旧实现（smart-canvas.js:7575-7604）的三个缺陷：
//   1. `setTimeout(() => URL.revokeObjectURL(href), 1200)` —— 固定 1200ms 撤销 blob URL。
//      48MB 还没被浏览器读完就撤销，下载被截断成 0 字节。
//   2. 不校验 blob.size，也不与 Content-Length 对账 → 空/截断的 blob 照样落盘，静默「成功」。
//   3. 部分失败（#225 的服务端回执）完全不读，用户不知道少了哪几张。
//
// 断言全部基于可观察副作用：createObjectURL/revokeObjectURL 调用序列、注册的 <a> 元素、
// 定时器延迟、返回值 / 抛错原因。不是「调用不抛错就算过」。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction } from './harness.mjs';

function fakeResponse({ ok = true, status = 200, size = 1024, contentLength = null, body = '', headers = {} } = {}) {
    const bag = { ...headers };
    if (contentLength !== null) bag['content-length'] = String(contentLength);
    return {
        ok,
        status,
        headers: { get: (name) => (bag[String(name).toLowerCase()] ?? null) },
        blob: async () => ({ size }),
        text: async () => body,
    };
}

function loadDownloadModule(response) {
    const { ctx, harness } = createCanvasContext();
    const created = [];
    const revoked = [];
    // harness 把宿主 URL 注入 ctx（harness.mjs:205）；测试换成可观察的假实现，
    // 才能断言「什么时候撤销、撤销了几次」。
    ctx.URL = {
        createObjectURL: (blob) => {
            const url = `blob:stub/${created.length + 1}`;
            created.push({ url, blob });
            return url;
        },
        revokeObjectURL: (url) => { revoked.push(url); },
    };
    const fetchCalls = [];
    ctx.fetch = async (url, opts = {}) => { fetchCalls.push({ url, opts }); return response; };
    // canvas-core.js 提供 tr/toast/escapeHtml；toast 换成记录器以便断言文案。
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-download.js']);
    const toasts = [];
    ctx.toast = (message, ms) => toasts.push(String(message));
    const body = ctx.document.body;
    const appended = [];
    const origAppend = body.appendChild.bind(body);
    body.appendChild = (child) => { appended.push(child); return origAppend(child); };
    return { ctx, harness, created, revoked, fetchCalls, toasts, appended };
}

const fn = (ctx, name) => globalFunction(ctx, name);

const PAYLOAD = {
    filename: '星标原图.zip',
    urls: ['/api/project-library/p1/media/a1', '/api/project-library/p1/media/a2'],
    items: [{ url: '/api/project-library/p1/media/a1', name: '主视觉.png' }],
};

// --- 缺陷 2：空 blob / 截断 blob 必须报错，不能留 0 字节文件 ----------------
test('空响应体必须报错，且不能创建 <a> 落盘（否则用户得到 0 字节 zip）', async () => {
    const { ctx, appended } = loadDownloadModule(fakeResponse({ size: 0, contentLength: 0 }));
    await assert.rejects(() => fn(ctx, 'downloadCanvasArchive')(PAYLOAD), /空|empty/i);
    assert.equal(appended.length, 0, '空 blob 绝不能触发下载动作');
});

test('blob 大小与 Content-Length 不符 = 截断，必须报错并说明是截断', async () => {
    const { ctx, appended } = loadDownloadModule(
        fakeResponse({ size: 0, contentLength: 48 * 1024 * 1024 }),
    );
    await assert.rejects(() => fn(ctx, 'downloadCanvasArchive')(PAYLOAD), /截断|truncated/i);
    assert.equal(appended.length, 0, '截断的包绝不能当成成功落盘');
});

test('HTTP 失败要把服务端原因带出来，而不是笼统「下载失败」', async () => {
    const { ctx } = loadDownloadModule(fakeResponse({
        ok: false, status: 404, body: '没有可下载的本地图片',
    }));
    await assert.rejects(
        () => fn(ctx, 'downloadCanvasArchive')(PAYLOAD),
        /没有可下载的本地图片/,
    );
});

// --- 缺陷 1：revoke 时机 -----------------------------------------------
test('下载在飞期间绝不能撤销 object URL（旧的固定 1200ms 会截断大包）', async () => {
    const bytes = 48 * 1024 * 1024;
    const { ctx, created, revoked, appended, harness } = loadDownloadModule(
        fakeResponse({ size: bytes, contentLength: bytes }),
    );
    await fn(ctx, 'downloadCanvasArchive')(PAYLOAD);
    assert.equal(created.length, 1, '有效包必须创建一次 object URL');
    assert.equal(appended.length, 1, '必须挂上 <a> 触发下载');
    assert.equal(revoked.length, 0, '下载尚未结束前不能撤销；撤销只能排在定时器里');
    const revokeTimers = harness.timers.filter(t => !t.cancelled);
    assert.ok(revokeTimers.length >= 1, '必须安排一个延后撤销的定时器');
    const delay = Math.max(...revokeTimers.map(t => t.ms));
    assert.ok(delay > 1200, `撤销延迟必须远大于旧的 1200ms，实际 ${delay}ms`);
    harness.advanceTimers();
    assert.deepEqual(revoked, created.map(c => c.url), '定时器到点后才撤销');
});

test('撤销延迟必须随体积增长（48MB 要比 1MB 留更久）', async () => {
    const one = 1024 * 1024;
    const big = 48 * 1024 * 1024;
    const smallRun = loadDownloadModule(fakeResponse({ size: one, contentLength: one }));
    await fn(smallRun.ctx, 'downloadCanvasArchive')(PAYLOAD);
    const bigRun = loadDownloadModule(fakeResponse({ size: big, contentLength: big }));
    await fn(bigRun.ctx, 'downloadCanvasArchive')(PAYLOAD);
    const smallDelay = Math.max(...smallRun.harness.timers.map(t => t.ms));
    const bigDelay = Math.max(...bigRun.harness.timers.map(t => t.ms));
    assert.ok(bigDelay > smallDelay, `大包必须比小包留更长的撤销窗口：${bigDelay} <= ${smallDelay}`);
});

// --- 缺陷 3：部分失败回执必须回传给调用方 ---------------------------------
test('服务端回执（X-Download-Report）要解析出来，供上层提示「已打包 n/总数」', async () => {
    const report = { total: 5, packed: 3, skipped: [{ name: '海报主视觉.png', reason: 'trashed' }] };
    const { ctx } = loadDownloadModule(fakeResponse({
        size: 2048,
        contentLength: 2048,
        headers: { 'x-download-report': encodeURIComponent(JSON.stringify(report)) },
    }));
    const result = await fn(ctx, 'downloadCanvasArchive')(PAYLOAD);
    assert.equal(result.report.packed, 3);
    assert.equal(result.report.total, 5);
    assert.equal(result.report.skipped[0].name, '海报主视觉.png');
    assert.equal(result.report.skipped[0].reason, 'trashed');
});

test('没有回执头时 report 为 null，不能抛错（后端旧版本仍要能用）', async () => {
    const { ctx } = loadDownloadModule(fakeResponse({ size: 512, contentLength: 512 }));
    const result = await fn(ctx, 'downloadCanvasArchive')(PAYLOAD);
    assert.equal(result.report, null);
});

test('星标导出走 /api/excel/export-zip，同一类空包缺陷也必须报错（canvasReceiveBlob）', async () => {
    const { ctx } = loadDownloadModule(fakeResponse());
    await assert.rejects(
        () => fn(ctx, 'canvasReceiveBlob')(fakeResponse({ ok: true, status: 200, size: 0, contentLength: 0 })),
        /空|empty/i,
    );
});

test('canvasReceiveBlob 正常时回传 blob/bytes/report，供调用方落盘', async () => {
    const report = { total: 2, packed: 1, skipped: [{ name: 'a.png', reason: 'missing_file' }] };
    const { ctx } = loadDownloadModule(fakeResponse({
        size: 4096,
        contentLength: 4096,
        headers: { 'x-download-report': encodeURIComponent(JSON.stringify(report)) },
    }));
    const response = await ctx.fetch('/api/excel/export-zip');
    const got = await fn(ctx, 'canvasReceiveBlob')(response);
    assert.equal(got.bytes, 4096);
    assert.equal(got.report.packed, 1);
    assert.equal(got.report.skipped[0].reason, 'missing_file');
});

test('部分失败要能生成人可读的跳过说明（名字 + 原因，不能只说失败）', async () => {
    const { ctx } = loadDownloadModule(fakeResponse({ size: 1, contentLength: 1 }));
    const summary = fn(ctx, 'canvasDownloadSkipSummary')({
        total: 3, packed: 1,
        skipped: [{ name: '海报主视觉.png', reason: 'trashed' }, { name: 'x'.repeat(200), reason: 'bogus_reason' }],
    });
    assert.match(summary, /3/);
    assert.match(summary, /海报主视觉\.png/);
    assert.match(summary, /trashed/i);
});

test('没有跳过项时不制造噪声文案', async () => {
    const { ctx } = loadDownloadModule(fakeResponse({ size: 1, contentLength: 1 }));
    assert.equal(fn(ctx, 'canvasDownloadSkipSummary')(null), '');
    assert.equal(fn(ctx, 'canvasDownloadSkipSummary')({ total: 2, packed: 2, skipped: [] }), '');
});

test('请求体必须带 items（下载名）与 urls，端点不变', async () => {
    const { ctx, fetchCalls } = loadDownloadModule(fakeResponse({ size: 512, contentLength: 512 }));
    await fn(ctx, 'downloadCanvasArchive')(PAYLOAD);
    assert.equal(fetchCalls.length, 1);
    assert.equal(fetchCalls[0].url, '/api/canvas-assets/download');
    assert.equal(fetchCalls[0].opts.method, 'POST');
    const body = JSON.parse(fetchCalls[0].opts.body);
    assert.deepEqual(body.urls, PAYLOAD.urls);
    assert.deepEqual(body.items, PAYLOAD.items);
    assert.equal(body.filename, PAYLOAD.filename);
});

// --- 审查补测：失败原因透传、撤销窗口上限、无 Content-Length 的响应 ----------
test('服务端回 JSON 错误体（{"detail": ...}）时必须把 detail 透出来，不能只给笼统文案', async () => {
    // FastAPI 的错误体就是 {"detail": "..."}。旧写法先 text() 再 json()，
    // 真实 Response 上 json() 必抛（body 已被消费）→ 永远走到笼统文案。
    // 假对象根本没有 json() 方法，同样让旧写法红。
    // 断言不能只看子串（整坨 JSON 里也含该子串），必须确认甩出来的是剥好的文案。
    const { ctx } = loadDownloadModule(fakeResponse({
        ok: false, status: 409, body: '{"detail":"素材已被移入回收区"}',
    }));
    await assert.rejects(
        () => fn(ctx, 'downloadCanvasArchive')(PAYLOAD),
        (err) => {
            const message = String(err?.message || '');
            assert.ok(message.includes('素材已被移入回收区'), `detail 没透出来：${message}`);
            assert.ok(!message.includes('detail') && !message.includes('{'),
                      `不能把整坨 JSON 直接给用户：${message}`);
            return true;
        },
    );
});

test('撤销窗口必须有上限（10 分钟），不能随体积无限增长', () => {
    const { ctx } = loadDownloadModule(fakeResponse());
    const delay = fn(ctx, 'canvasDownloadRevokeDelayMs');
    assert.equal(delay(48 * 1024 * 1024), 20000 + 98304, '48MB ≈ 20s + 98s');
    assert.equal(delay(40 * 1024 * 1024 * 1024), 600000, '上限 10 分钟：再多说明下载已经死了');
    assert.equal(delay(0), 20000, '小包也留 20s 起下载窗口');
    assert.equal(delay('nonsense'), 20000, '非法体积退回下限，不能算出 NaN');
});

test('响应没有 Content-Length（chunked / gzip）时不能误报截断；0 字节仍然报错', async () => {
    const okRun = loadDownloadModule(fakeResponse({ size: 4096, contentLength: null }));
    const result = await fn(okRun.ctx, 'downloadCanvasArchive')(PAYLOAD);
    assert.equal(result.bytes, 4096);
    assert.equal(okRun.appended.length, 1, '无 Content-Length 但非空：正常落盘');
    assert.equal(okRun.created.length, 1);

    const emptyRun = loadDownloadModule(fakeResponse({ size: 0, contentLength: null }));
    await assert.rejects(() => fn(emptyRun.ctx, 'downloadCanvasArchive')(PAYLOAD), /空|empty/i);
    assert.equal(emptyRun.appended.length, 0);
});
