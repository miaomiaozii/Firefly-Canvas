// #284：canvas-tools.ts 工具返回值「可服务绝对 URL」归一化的行为单元测试。
//
// 背景：Agent 回复里的 MEDIA: 行此前是原始文本 `MEDIA:/media/generated_image_*.png`，
// 该 URL 404——真实挂载是 StaticFiles 挂 /output 与 /assets（main.py:1180-1182），
// 生成目录 OUTPUT_OUTPUT_DIR=ASSETS_DIR/output（main.py:1090）→ 可服务前缀 /assets/output/<file>。
// #284 前半（6bbb173）在前端 canvas-agent-tui.js 归一化 + 渲染缩略；本文件覆盖 #284 后半：
// app/agent_extensions/canvas-tools.ts 的媒体/生成工具返回值带「可服务绝对 URL」
// （normalizeMediaUrl + normalizeMediaPayload，递归改写 generate.status / generate.wait /
// media.upload / media.list 的返回 payload）。
//
// 为什么用 vm 抽取而不是 import 整个 .ts：canvas-tools.ts 依赖 pi-stable / typebox（pi 运行时
// 专有，测试环境不可解析）且顶层注册 19 个工具。归一化 helper 是**纯函数**（仅依赖模块级
// API_BASE，源码里刻意不写 TS 类型标注以便 Node 直接求值），测试按哨兵切片抽到 vm 上下文里
// 求值——测的是真实源码文本，不是重新实现。
//
// 防的回归（断言可观察返回值，非「不抛错」）：
//   - /media/<f>、裸媒体文件名、磁盘路径 output/<f> → 补 API_BASE 主机的 /assets/output/<f>；
//   - 已可服务的相对路径（/assets|/output|/api）→ 仅补主机；已绝对 URL（http(s)://）原样；
//   - 非媒体字符串（task_id / status / model / 提示词 / base64）一律原样，不被污染；
//   - normalizeMediaPayload 深遍历对象/数组，只改字符串、保留结构与标量。

import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(
    path.join(__dirname, '..', '..', 'app', 'agent_extensions', 'canvas-tools.ts'),
    'utf8',
);

const BASE = 'http://127.0.0.1:9999';

function loadHelpers(apiBase) {
    const start = SOURCE.indexOf('const MEDIA_NAME_RE');
    const end = SOURCE.indexOf('// ── end #284 媒体 URL 归一化 ──');
    assert.ok(start !== -1, 'canvas-tools.ts 缺少 MEDIA_NAME_RE（#284 归一化 helper 缺失）');
    assert.ok(end !== -1 && end > start, 'canvas-tools.ts 缺少 #284 helper 结束哨兵');
    const code = SOURCE.slice(start, end);
    // 用 new Function 在「当前 realm」求值（而非 vm 隔离 realm——vm 产出的数组/对象原型不同，
    // deepStrictEqual 会因原型差异误报）。helper 是纯函数，API_BASE 作参数注入即可。
    const factory = new Function('API_BASE', code + '\nreturn { normalizeMediaUrl, normalizeMediaPayload };');
    const helpers = factory(apiBase);
    assert.equal(typeof helpers.normalizeMediaUrl, 'function', 'helper 未导出 normalizeMediaUrl');
    assert.equal(typeof helpers.normalizeMediaPayload, 'function', 'helper 未导出 normalizeMediaPayload');
    return { norm: helpers.normalizeMediaUrl, payload: helpers.normalizeMediaPayload };
}

test('#284 源码契约：五个媒体/生成工具返回值都过 normalizeMediaPayload', () => {
    // 工具名 → callApi 的路径段（确保钉到真实工具，而不是随便一个 JSON.stringify）。
    const wired = [
        ['generate.status', '/generate/tasks/${params.task_id}`'],
        ['generate.wait', '/generate/tasks/${params.task_id}/wait?timeout=${timeout}`'],
        ['media.upload', '/media/upload`'],
        ['media.list', '/media/list`'],
        ['canvas.view_media', '/canvas/view_media`'],
    ];
    for (const [tool, pathSeg] of wired) {
        // 找到该工具的 registerTool 块，断言其返回体用了 normalizeMediaPayload(data)。
        const toolIdx = SOURCE.indexOf(`name: "${tool}"`);
        assert.notEqual(toolIdx, -1, `canvas-tools.ts 找不到工具 ${tool}`);
        // 块范围：从该工具名到下一个 registerTool（或文件尾）。
        const nextIdx = SOURCE.indexOf('pi.registerTool', toolIdx + 1);
        const block = SOURCE.slice(toolIdx, nextIdx === -1 ? SOURCE.length : nextIdx);
        assert.ok(block.includes(pathSeg), `${tool} 块里找不到其 callApi 路径段 ${pathSeg}`);
        assert.ok(
            block.includes('JSON.stringify(normalizeMediaPayload(data), null, 2)'),
            `${tool} 返回值未过 normalizeMediaPayload（#284 可服务绝对 URL 缺失）`,
        );
    }
});

test('#284 normalizeMediaUrl：/media/<f>、裸媒体名、磁盘 output 路径 → 绝对 /assets/output/<f>', () => {
    const { norm } = loadHelpers(BASE);
    assert.equal(norm('/media/generated_image_20260304_012846_9490.png'),
        `${BASE}/assets/output/generated_image_20260304_012846_9490.png`);
    assert.equal(norm('generated_image_a.png'), `${BASE}/assets/output/generated_image_a.png`);
    // 真实生产前缀（save_ai_image_to_output 用 online_；视频/音频同理）。
    assert.equal(norm('online_abc1234567.png'), `${BASE}/assets/output/online_abc1234567.png`);
    assert.equal(norm('video_0123456789.mp4'), `${BASE}/assets/output/video_0123456789.mp4`);
    // 磁盘绝对路径里的 output/<f>（含 Windows）。
    assert.equal(norm('D:/FF/assets/output/y.png'), `${BASE}/assets/output/y.png`);
    assert.equal(norm('/var/ff/assets/output/z.png'), `${BASE}/assets/output/z.png`);
});

test('#284 normalizeMediaUrl：已可服务相对路径仅补主机；已绝对 URL 原样', () => {
    const { norm } = loadHelpers(BASE);
    assert.equal(norm('/assets/output/online_x.png'), `${BASE}/assets/output/online_x.png`);
    assert.equal(norm('/output/v.mp4'), `${BASE}/output/v.mp4`);
    assert.equal(norm('/api/storage-files/generated/a.png'), `${BASE}/api/storage-files/generated/a.png`);
    assert.equal(norm('https://cdn.example.com/x.png'), 'https://cdn.example.com/x.png');
    assert.equal(norm('http://other.host:8080/y.png'), 'http://other.host:8080/y.png');
});

test('#284 normalizeMediaUrl：非媒体字符串一律原样（不污染 task_id/status/model/提示词/base64）', () => {
    const { norm } = loadHelpers(BASE);
    assert.equal(norm('agent_img_ab12cd34ef56'), 'agent_img_ab12cd34ef56');
    assert.equal(norm('succeeded'), 'succeeded');
    assert.equal(norm('qwen-plus'), 'qwen-plus');
    assert.equal(norm('a video of a cat'), 'a video of a cat');        // 提示词（含 video 词但非前缀）
    assert.equal(norm('data:image/png;base64,AAAA'), 'data:image/png;base64,AAAA');
    assert.equal(norm(''), '');
    assert.equal(norm(null), null);
    assert.equal(norm(undefined), undefined);
});

test('#284 normalizeMediaUrl：相对路径仅改写已知媒体前缀的末段', () => {
    const { norm } = loadHelpers(BASE);
    assert.equal(norm('some/dir/video_intro.mp4'), `${BASE}/assets/output/video_intro.mp4`);
    assert.equal(norm('some/dir/notes.txt'), 'some/dir/notes.txt');    // 非媒体末段原样
});

test('#284 normalizeMediaPayload：深遍历对象/数组，只改字符串、保留结构与标量', () => {
    const { payload } = loadHelpers(BASE);
    const generateStyle = {
        task_id: 'agent_img_x',
        status: 'succeeded',
        result: {
            images: ['/assets/output/online_a.png', '/media/legacy_b.png', 'https://cdn/x.png'],
            image_items: [{ url: 'online_c.png', name: 'online_c.png' }],
            prompt: 'a video of a cat',
            model: 'qwen-plus',
            timestamp: 1700000000,
        },
    };
    const out = payload(generateStyle);
    assert.equal(out.task_id, 'agent_img_x');                          // task_id 不改
    assert.equal(out.status, 'succeeded');
    assert.equal(out.result.images[0], `${BASE}/assets/output/online_a.png`);
    assert.equal(out.result.images[1], `${BASE}/assets/output/legacy_b.png`);
    assert.equal(out.result.images[2], 'https://cdn/x.png');           // 已绝对原样
    assert.equal(out.result.image_items[0].url, `${BASE}/assets/output/online_c.png`);
    assert.equal(out.result.image_items[0].name, `${BASE}/assets/output/online_c.png`); // name 也是媒体文件名 → 一并归一
    assert.equal(out.result.prompt, 'a video of a cat');               // 提示词不改
    assert.equal(out.result.model, 'qwen-plus');
    assert.equal(out.result.timestamp, 1700000000);
    // 原对象不被就地改写（返回新对象）。
    assert.equal(generateStyle.result.images[0], '/assets/output/online_a.png');
});

test('#284 normalizeMediaPayload：标量/数组/空值透传', () => {
    const { payload } = loadHelpers(BASE);
    assert.equal(payload('succeeded'), 'succeeded');
    assert.equal(payload(42), 42);
    assert.equal(payload(true), true);
    assert.equal(payload(null), null);
    assert.deepEqual(payload([1, 'online_a.png', null, { u: '/media/b.png' }]),
        [1, `${BASE}/assets/output/online_a.png`, null, { u: `${BASE}/assets/output/b.png` }]);
});
