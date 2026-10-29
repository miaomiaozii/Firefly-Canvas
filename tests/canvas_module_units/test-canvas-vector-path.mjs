// #255 canvas-vector-path.js 单元测试：ComfyArt src/vectorPath.ts 的 MIT 移植。
//
// 移植范围（源：ComfyArt v1.1.0@13396a8，MIT，Copyright (c) 2026 Terry Jia，见 NOTICE.md）：
//   - scalePath(d, w, h)：SVG path 字符串坐标缩放到 w×h（0..1 归一化路径 → 画布像素）；
//   - nodesToPath(nodes, closed)：贝塞尔节点（x/y + 手柄 hx/hy）→ 归一化 0..1 SVG path。
// 未移植：loadPaper / normalized（依赖 paper.js 运行时，超出本票范围，NOTICE.md 已注明）。
//
// 防的回归（断言移植保真，逐式对照源码）：
//   - PAIRED 指令（MLCSQTmlcsqt）参数按 x,y 交替分配轴；H/h 恒 x 轴（×w）；
//     其余单参数指令（V/v）恒 y 轴（×h）——指令切换时 index 归零；
//   - 数值 round 到 1e-5（0.30000000000000004 → 0.3），科学计数法可解析；
//   - nodesToPath 的 bbox 取 x/hx 三点（含手柄外扩），w/h 下限 1（退化不除零），
//     输出归一化 0..1，closed 追加「末→首」段 + Z。
//   - scalePath(nodesToPath(...).d, w, h) 往返：归一化路径还原到像素。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, fromContext } from './harness.mjs';

function loadVectorPath() {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-vector-path.js']);
    return ctx.CanvasVectorPath;
}

test('scalePath: M/L 成对指令按 x×w / y×h 缩放', () => {
    const vp = loadVectorPath();
    assert.equal(vp.scalePath('M 0 0 L 1 1', 100, 200), 'M 0 0 L 100 200');
    assert.equal(vp.scalePath('M 0.5 0.25 L 1 1', 100, 200), 'M 50 50 L 100 200');
    // 指令切换：index 归零（L 之后接 C，C 的第一个参数重新从 x 轴开始）
    assert.equal(vp.scalePath('M 0 0 L 0.5 0.5 C 0.25 0.25 0.75 0.75 1 1', 100, 100),
        'M 0 0 L 50 50 C 25 25 75 75 100 100');
});

test('scalePath: H/h 恒 x 轴，V 恒 y 轴，Z 透传', () => {
    const vp = loadVectorPath();
    assert.equal(vp.scalePath('M 0 0 H 0.5 V 0.25 Z', 200, 100), 'M 0 0 H 100 V 25 Z');
    assert.equal(vp.scalePath('M 0 0 h 0.5 v 0.25 z', 200, 100), 'M 0 0 h 100 v 25 z');
});

test('scalePath: round 到 1e-5 + 科学计数法（忠实原版怪癖）+ 空串', () => {
    const vp = loadVectorPath();
    // 1/3 在二进制的噪声（0.30000000000000004 × 100）被 round 收掉；round 作用在缩放后的值上
    assert.equal(vp.scalePath('M 0.30000000000000004 0 L 0.10000000000000000555 1', 100, 100),
        'M 30 0 L 10 100');
    // 忠实原版行为（源 vectorPath.ts 同款）：正则虽能抽出 '1e-3' 整体，但指令判断 /…/[a-zA-Z]/.test(tok)
    // 对含 'e' 的数字 token 判真 → 被当作指令原样透传（ComfyArt 既有怪癖，非移植引入）。
    assert.equal(vp.scalePath('M 1e-3 0 L 2e-3 1', 1000, 1000), 'M 1e-3 0 L 2e-3 1000');
    assert.equal(vp.scalePath('', 10, 10), '');
    assert.equal(vp.scalePath(null, 10, 10), '');
});

test('nodesToPath: 两点开放路径（无手柄 → 直线段 C，控制点=b±0）', () => {
    const vp = loadVectorPath();
    const out = fromContext(vp.nodesToPath(
        [{ x: 0, y: 0, hx: 0, hy: 0 }, { x: 10, y: 10, hx: 0, hy: 0 }], false
    ));
    // seg = C nx(a.x+a.hx) ny(a.y+a.hy) nx(b.x-b.hx) ny(b.y-b.hy) nx(b.x) ny(b.y)
    assert.equal(out.d, 'M 0 0 C 0 0 1 1 1 1');
    assert.deepEqual({ x: out.x, y: out.y, w: out.w, h: out.h }, { x: 0, y: 0, w: 10, h: 10 });
});

test('nodesToPath: 三点带手柄（bbox 含 hx/hy 外扩，逐式对照 seg 公式）', () => {
    const vp = loadVectorPath();
    const out = fromContext(vp.nodesToPath([
        { x: 0, y: 0, hx: 0, hy: 0 },
        { x: 10, y: 5, hx: 1, hy: 0 },
        { x: 20, y: 0, hx: 0, hy: 0 }
    ], false));
    // bbox: x∈[0,20]（含 10±1），y∈[0,5]
    assert.deepEqual({ x: out.x, y: out.y, w: out.w, h: out.h }, { x: 0, y: 0, w: 20, h: 5 });
    assert.equal(out.d, 'M 0 0 C 0 0 0.45 1 0.5 1 C 0.55 1 1 0 1 0');
});

test('nodesToPath: closed 追加「末→首」段 + Z', () => {
    const vp = loadVectorPath();
    const nodes = [
        { x: 0, y: 0, hx: 0, hy: 0 },
        { x: 10, y: 5, hx: 1, hy: 0 },
        { x: 20, y: 0, hx: 0, hy: 0 }
    ];
    const out = fromContext(vp.nodesToPath(nodes, true));
    assert.equal(out.d, 'M 0 0 C 0 0 0.45 1 0.5 1 C 0.55 1 1 0 1 0 C 1 0 0 0 0 0 Z');
});

test('nodesToPath: 退化输入（单点 / 空 / null）不抛错', () => {
    const vp = loadVectorPath();
    // 单点：w/h 下限 1（源码 Math.max(1, ·)），不除零
    const single = fromContext(vp.nodesToPath([{ x: 5, y: 7, hx: 0, hy: 0 }], false));
    assert.equal(single.d, 'M 0 0');
    assert.deepEqual({ x: single.x, y: single.y, w: single.w, h: single.h }, { x: 5, y: 7, w: 1, h: 1 });
    // 空 / null：防御性空路径（原 TS 对空数组会产生 Infinity——移植加固点）
    assert.deepEqual(fromContext(vp.nodesToPath([], false)), { x: 0, y: 0, w: 1, h: 1, d: '' });
    assert.deepEqual(fromContext(vp.nodesToPath(null, false)), { x: 0, y: 0, w: 1, h: 1, d: '' });
});

test('往返：nodesToPath → scalePath 还原到像素（归一化 0..1 是公共坐标系）', () => {
    const vp = loadVectorPath();
    // 开放两点：bbox 50×25 → 放大 10 倍（500×250）应还原
    const open = fromContext(vp.nodesToPath(
        [{ x: 0, y: 0, hx: 0, hy: 0 }, { x: 50, y: 25, hx: 0, hy: 0 }], false
    ));
    assert.equal(open.d, 'M 0 0 C 0 0 1 1 1 1');
    assert.equal(vp.scalePath(open.d, 500, 250), 'M 0 0 C 0 0 500 250 500 250');
    // 闭合三角：d 是 bbox 相对的 0..1 坐标，像素偏移（x/y）由调用方叠加
    const tri = fromContext(vp.nodesToPath(
        [{ x: 100, y: 50, hx: 0, hy: 0 }, { x: 150, y: 50, hx: 0, hy: 0 }, { x: 125, y: 75, hx: 0, hy: 0 }], true
    ));
    assert.deepEqual({ x: tri.x, y: tri.y, w: tri.w, h: tri.h }, { x: 100, y: 50, w: 50, h: 25 });
    assert.equal(tri.d, 'M 0 0 C 0 0 1 0 1 0 C 1 0 0.5 1 0.5 1 C 0.5 1 0 0 0 0 Z');
    assert.equal(vp.scalePath(tri.d, 500, 250),
        'M 0 0 C 0 0 500 0 500 0 C 500 0 250 250 250 250 C 250 250 0 0 0 0 Z');
});
