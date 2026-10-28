// #238 Perf(W1)：缩放自适应缩略图档位 + 视频预览 poster/preload 单元测试。
//
// 处方（抄 ComfyArt 95ab202 a/b）：节点长边 × zoom × dpr 向上取档 [256,512,1024]，
// 预览 URL 带 w=；视频预览 preload="none" + poster=对应档缩略图，hover 才播。
// 全部断言基于可观察返回值/属性字符串，不是「调用不抛错」。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate } from './harness.mjs';

function loadMedia() {
    const { ctx, harness } = createCanvasContext();
    loadModules(ctx, [
        'canvas-node-types.js',
        'canvas-core.js',
        'canvas-state.js',
        'canvas-media-history.js',
    ]);
    evaluate(ctx, `canvas = { project_id: 'proj-1' }`);
    // displayMediaUrl 是 smart-canvas.js（宿主）提供的；模块单测环境按真实契约最小桩。
    evaluate(ctx, `var displayMediaUrl = (item) => (typeof item === 'string' ? item : (item && item.url) || '');`);
    return { ctx, harness };
}

const fn = (ctx, name) => globalFunction(ctx, name);

// --- 档位选择（tier 表 + 边界） ---------------------------------------
test('mediaPreviewTierFor: 小尺寸取最小档 256', () => {
    const { ctx } = loadMedia();
    assert.equal(fn(ctx, 'mediaPreviewTierFor')(100, 1, 1), 256);
});

test('mediaPreviewTierFor: 恰等于档位时保持该档（<=），略超则升档', () => {
    const { ctx } = loadMedia();
    const tier = fn(ctx, 'mediaPreviewTierFor');
    assert.equal(tier(256, 1, 1), 256);
    assert.equal(tier(257, 1, 1), 512);
    assert.equal(tier(512, 1, 1), 512);
    assert.equal(tier(513, 1, 1), 1024);
});

test('mediaPreviewTierFor: zoom 与 dpr 相乘决定档位', () => {
    const { ctx } = loadMedia();
    const tier = fn(ctx, 'mediaPreviewTierFor');
    // 300 CSS px × 2 zoom × 1 dpr = 600 → 1024 档
    assert.equal(tier(300, 2, 1), 1024);
    // 300 × 0.5 zoom × 2 dpr = 300 → 512 档
    assert.equal(tier(300, 0.5, 2), 512);
});

test('mediaPreviewTierFor: 超出最大档钳到 1024；非法输入不炸', () => {
    const { ctx } = loadMedia();
    const tier = fn(ctx, 'mediaPreviewTierFor');
    assert.equal(tier(4000, 3, 2), 1024);
    assert.equal(tier(undefined, undefined, undefined), 256);
    assert.equal(tier('abc', 'x', 'y'), 256);
});

// --- URL 档位重写（LOD 刷新用） ---------------------------------------
test('setPreviewUrlWidth: 重写既有 w 参数（?w= 与 &w= 两种位置）', () => {
    const { ctx } = loadMedia();
    const rw = fn(ctx, 'setPreviewUrlWidth');
    assert.equal(rw('/api/media-preview?w=256&url=%2Fa.png', 1024), '/api/media-preview?w=1024&url=%2Fa.png');
    assert.equal(rw('/api/project-library/p1/media/a1/preview?w=512', 256), '/api/project-library/p1/media/a1/preview?w=256');
});

test('setPreviewUrlWidth: 无 w 参数的 URL 原样返回（不越权造参数）', () => {
    const { ctx } = loadMedia();
    assert.equal(fn(ctx, 'setPreviewUrlWidth')('/output/x.png', 512), '/output/x.png');
});

// --- 视频预览 HTML：poster + preload="none" ----------------------------
test('smartVideoPreviewHtml: 可预览源产出 poster=档位缩略图 且 preload="none"', () => {
    const { ctx } = loadMedia();
    const html = fn(ctx, 'smartVideoPreviewHtml')({ url: '/assets/clip.mp4' }, 512, 'alt=""');
    assert.match(html, /preload="none"/);
    assert.doesNotMatch(html, /preload="metadata"/);
    assert.match(html, /poster="\/api\/media-preview\?w=512&amp;url=%2Fassets%2Fclip\.mp4"/);
    assert.match(html, /data-video-preview="1"/);
    assert.match(html, /muted/);
});

test('smartVideoPreviewHtml: 不可预览源（data:/blob:）不造 poster，仍 preload="none"', () => {
    const { ctx } = loadMedia();
    const html = fn(ctx, 'smartVideoPreviewHtml')('blob:http://x/y', 512, '');
    assert.doesNotMatch(html, /poster="/);
    assert.match(html, /preload="none"/);
});

test('smartMediaPreviewUrl: 项目媒体路径带 w 档位', () => {
    const { ctx } = loadMedia();
    const url = fn(ctx, 'smartMediaPreviewUrl')('/api/project-library/p1/media/a1', 1024);
    assert.equal(url, '/api/project-library/p1/media/a1/preview?w=1024');
});
