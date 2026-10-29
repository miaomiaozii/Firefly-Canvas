// #255 canvas-layers.js 单元测试：ComfyArt src/layers.ts 纯函数 MIT 移植 + 图层 store/合成。
//
// 移植对照（源：ComfyArt v1.1.0@13396a8 src/layers.ts 275 行，MIT，Copyright (c) 2026 Terry Jia，见 NOTICE.md）：
//   - measure(media,data,orig)        → measureLayer(data, orig)      去掉 media 依赖，cover/solid/diff 语义逐式保留
//   - overlap(a,b)                    → layerOverlap(a, b)            IoU（alpha>OPAQUE）逐式保留
//   - cropLayer 的 bbox 扫描部分       → layerAlphaBBox / cropLayerData（uploadImage 部分不迁，改返回像素）
//   - separateRegions 像素循环         → splitLayerByBoxes（分数坐标 box → 区域像素 + 剩余像素）
//   - regionSplit 掩膜合成像素循环     → composeWithMask（mask≥128 保留原像素，alpha 置 255，union 累积）
//   - 新增（非移植）：compositeLayers（alpha-over 按层序合成）、图层 store 纯操作
// 未移植：registerPlacer 系（依赖 tldraw editor / comfy 后端）、grow（canvas blur）、canvasOf/pixels（DOM）。
//
// 防的回归：
//   - OPAQUE=128 / SOLID=200 阈值边界（a=128 不算覆盖，a=200 才算 solid）；
//   - 无 solid 像素时 diff=999（源码哨兵值，注册筛选用）；
//   - bbox 阈值 alpha<=8 视为空；
//   - splitByBoxes 消费的像素在 rest 里清零（防重复合成）；
//   - 合成顺序：数组末位在最上（alpha-over），visible=false 跳过。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, fromContext } from './harness.mjs';

function makeLayer(w, h, fill) {
    const data = new Uint8ClampedArray(w * h * 4);
    for (let i = 0; i < w * h; i++) {
        const f = typeof fill === 'function' ? fill(i, Math.floor(i / w), i % w) : fill;
        data[i * 4] = f[0];
        data[i * 4 + 1] = f[1];
        data[i * 4 + 2] = f[2];
        data[i * 4 + 3] = f[3];
    }
    return { data, width: w, height: h };
}

function loadLayers() {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-layers.js']);
    return ctx.CanvasLayers;
}

test('measureLayer: cover/solid/diff 阈值与 999 哨兵', () => {
    const cl = loadLayers();
    // 4 像素：a=255×2（solid，与 orig 完全一致）+ a=128×1（覆盖不算 solid 的上界外）+ a=0
    const orig = new Uint8ClampedArray([10, 20, 30, 255, 10, 20, 30, 255, 10, 20, 30, 255, 0, 0, 0, 255]);
    const data = new Uint8ClampedArray([10, 20, 30, 255, 10, 20, 30, 255, 50, 20, 30, 128, 0, 0, 0, 0]);
    const m = cl.measureLayer(data, orig);
    assert.equal(m.cover, 2 / 4, 'a>128 的像素占 2/4（a=128 严格大于才算覆盖，128 不算）');
    assert.equal(m.solid, 2, 'a>200 的像素占 2');
    assert.equal(m.diff, 0, '两个 solid 像素与 orig 一致 → diff=0（a=128 像素不进 diff 统计，只算 cover）');
    // 单个 solid 像素有差异：(40+0+0)/3，其余像素 alpha=0 不参与
    const diffData = new Uint8ClampedArray([50, 20, 30, 255, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
    assert.ok(Math.abs(cl.measureLayer(diffData, orig).diff - 40 / 3) < 1e-9, `单 solid 像素差 (40+0+0)/3=${cl.measureLayer(diffData, orig).diff}`);
    // 与 orig 完全一致 → diff=0
    const same = cl.measureLayer(orig, orig);
    assert.equal(same.cover, 1);
    assert.equal(same.diff, 0);
    // 无 solid 像素 → diff=999（源码哨兵）
    const onlyCovered = new Uint8ClampedArray([1, 2, 3, 129, 1, 2, 3, 130]);
    assert.equal(cl.measureLayer(onlyCovered, orig.slice(0, 8)).diff, 999);
});

test('layerOverlap: IoU 边界（相同=1 / 不相交=0 / 半交=0.5 / 空=0）', () => {
    const cl = loadLayers();
    const solid = (mask) => {
        const d = new Uint8ClampedArray(mask.length * 4);
        mask.forEach((m, i) => { d[i * 4] = 255; d[i * 4 + 3] = m; });
        return d;
    };
    assert.equal(cl.layerOverlap(solid([255, 255, 255, 255]), solid([255, 255, 255, 255])), 1);
    assert.equal(cl.layerOverlap(solid([255, 255, 0, 0]), solid([0, 0, 255, 255])), 0);
    assert.equal(cl.layerOverlap(solid([255, 255, 0, 0]), solid([255, 255, 255, 255])), 0.5); // inter=2 / union=4
    assert.equal(cl.layerOverlap(solid([255, 255, 255, 0]), solid([0, 0, 255, 255])), 0.25); // inter=1 / union=4
    assert.equal(cl.layerOverlap(solid([0, 0]), solid([0, 0])), 0);
    // 阈值：a=128 不算覆盖
    const low = new Uint8ClampedArray([255, 255, 255, 128, 255, 255, 255, 0]);
    assert.equal(cl.layerOverlap(low, solid([255, 0])), 0);
});

test('layerAlphaBBox: 紧致 bbox + alpha<=8 视为空 + 全空返回 null', () => {
    const cl = loadLayers();
    const L = makeLayer(4, 2, (i) => [255, 255, 255, 0]);
    // 点亮 (1,0)、(3,1)：a=9（阈值内）与 a=8（阈值外）
    L.data[(0 * 4 + 1) * 4 + 3] = 9;
    L.data[(1 * 4 + 3) * 4 + 3] = 8; // 不算
    L.data[(1 * 4 + 2) * 4 + 3] = 255;
    const box = fromContext(cl.layerAlphaBBox(L.data, 4, 2)); // JSON 回传宿主 realm（VM realm 对象过不了 deepStrictEqual）
    assert.deepEqual(box, { x: 1, y: 0, w: 2, h: 2 }, 'bbox=(1,0)-(2,1) 含 8 阈值外排除');
    assert.equal(cl.layerAlphaBBox(new Uint8ClampedArray(4 * 2 * 4), 4, 2), null);
});

test('cropLayerData: 裁剪到 bbox 且像素搬运正确，空返回 null', () => {
    const cl = loadLayers();
    const L = makeLayer(3, 3, () => [0, 0, 0, 0]);
    L.data[(1 * 3 + 1) * 4] = 11; L.data[(1 * 3 + 1) * 4 + 3] = 200;   // (1,1)
    L.data[(2 * 3 + 2) * 4] = 22; L.data[(2 * 3 + 2) * 4 + 3] = 255;   // (2,2)
    const out = cl.cropLayerData(L.data, 3, 3);
    assert.equal(out.x, 1); assert.equal(out.y, 1);
    assert.equal(out.w, 2); assert.equal(out.h, 2);
    // out(0,0) ← in(1,1)；out(1,1) ← in(2,2)；其余为空
    assert.equal(out.data[0], 11);    assert.equal(out.data[3], 200);
    assert.equal(out.data[(0 * 2 + 1) * 4], 0);   assert.equal(out.data[(0 * 2 + 1) * 4 + 3], 0);
    assert.equal(out.data[(1 * 2 + 1) * 4], 22);  assert.equal(out.data[(1 * 2 + 1) * 4 + 3], 255);
    assert.equal(cl.cropLayerData(new Uint8ClampedArray(8 * 4), 2, 4), null);
});

test('splitLayerByBoxes: 分数坐标分区 + 消费像素清零 + 空区域过滤', () => {
    const cl = loadLayers();
    // 8×4 全不透明图
    const L = makeLayer(8, 4, () => [200, 100, 50, 255]);
    // 左半 [0,0,0.5,1] 与右上 [0.5,0,0.5,0.5]
    const { parts, rest } = cl.splitLayerByBoxes(L.data, 8, 4, [[0, 0, 0.5, 1], [0.5, 0, 0.5, 0.5]]);
    assert.equal(parts.length, 2);
    // VM realm 的 TypedArray 用 Uint8ClampedArray.from 回传宿主（fromContext 的 JSON 序列化会丢掉 TypedArray）
    const host = (o) => o && { data: Uint8ClampedArray.from(o.data), x: o.x, y: o.y, w: o.w, h: o.h };
    const p0 = host(parts[0]);
    assert.equal(p0.data.length, 4 * 4 * 4, 'box0 = 4×4 全高');
    assert.equal(p0.x, 0); assert.equal(p0.y, 0); assert.equal(p0.w, 4); assert.equal(p0.h, 4);
    assert.equal(p0.data[3], 255);
    const p1 = host(parts[1]);
    assert.equal(p1.x, 4); assert.equal(p1.y, 0); assert.equal(p1.w, 4); assert.equal(p1.h, 2);
    // 剩余 = 右下 4×2
    const r = host(rest);
    assert.equal(r.x, 4); assert.equal(r.y, 2); assert.equal(r.w, 4); assert.equal(r.h, 2);
    assert.equal(r.data[3], 255, '剩余区像素保留');
    // 已消费像素在 rest 全量缓冲中清零
    assert.equal(L.data[(0 * 8 + 0) * 4 + 3], 0, 'box0 内像素被消费（alpha 清零）');
    assert.equal(L.data[(1 * 8 + 6) * 4 + 3], 0, 'box1 内像素被消费');
    assert.equal(L.data[(3 * 8 + 6) * 4 + 3], 255, '剩余区未被消费');
    // 空区域（全透明 box）→ 该 part 为 null 被过滤
    const empty = makeLayer(4, 4, () => [0, 0, 0, 0]);
    const out2 = cl.splitLayerByBoxes(empty.data, 4, 4, [[0, 0, 1, 1]]);
    assert.equal(out2.parts.length, 0);
    assert.equal(out2.rest, null, '全空图剩余区也是 null');
});

test('composeWithMask: mask≥128 保留原像素 alpha 置 255 + union 累积', () => {
    const cl = loadLayers();
    // 2×1：原像素 (10,20,30) (40,50,60)；掩膜灰度 255 / 0
    // 忠实原版 regionSplit：掩膜是灰度图加载成的 RGBA 缓冲，判断用 m[p]（R 通道）而非 alpha。
    const orig = new Uint8ClampedArray([10, 20, 30, 255, 40, 50, 60, 255]);
    const mask = new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 0]);
    const crop = { x: 0, y: 0, w: 2, h: 1 };
    const { data, union } = cl.composeWithMask(orig, mask, crop);
    assert.equal(data[0], 10); assert.equal(data[1], 20); assert.equal(data[2], 30); assert.equal(data[3], 255);
    assert.equal(data[4], 0); assert.equal(data[7], 0, 'mask<128 的像素跳过');
    assert.equal(union[3], 255); assert.equal(union[7], 0);
});

test('compositeLayers: alpha-over 按层序（数组末位最上）+ 跳过隐藏 + 半透明混合', () => {
    const cl = loadLayers();
    // 底：红不透明；顶：绿 50% 透明
    const bottom = makeLayer(1, 1, [255, 0, 0, 255]);
    const top = makeLayer(1, 1, [0, 255, 0, 128]);
    const out = cl.compositeLayers([bottom, { ...top, visible: true }], 1, 1);
    // a=128/255≈0.502：R=255×0.498≈127, G=255×0.502≈128（Uint8ClampedArray 四舍五入语义 ±1）
    assert.ok(Math.abs(out.data[0] - 127) <= 1, `R 混合=${out.data[0]}`);
    assert.ok(Math.abs(out.data[1] - 128) <= 1, `G 混合=${out.data[1]}`);
    assert.ok(Math.abs(out.data[3] - 255) <= 1, `A=${out.data[3]}`);
    // 顺序：末位在最上 → 顶变红底变绿则结果偏红
    const out2 = cl.compositeLayers([{ ...top, visible: true }, bottom], 1, 1);
    assert.ok(out2.data[0] > out2.data[1], '末位层（底=红）在上时 R>G');
    // visible=false 跳过
    const out3 = cl.compositeLayers([{ ...top, visible: false }, bottom], 1, 1);
    assert.equal(out3.data[0], 255);
    assert.equal(out3.data[1], 0, '隐藏层不参与合成');
    // 空列表 → 全透明
    const out4 = cl.compositeLayers([], 2, 2);
    assert.equal(out4.data.length, 16);
    assert.equal(out4.data[3], 0);
});

test('图层 store 纯操作：add/remove/toggle/move/reorder 不可变', () => {
    const cl = loadLayers();
    const L1 = { id: 'a', name: 'a', ...makeLayer(1, 1, [1, 1, 1, 255]) };
    const L2 = { id: 'b', name: 'b', ...makeLayer(1, 1, [2, 2, 2, 255]) };
    const L3 = { id: 'c', name: 'c', ...makeLayer(1, 1, [3, 3, 3, 255]) };
    let s = cl.createLayerStore();
    let s2, s3;
    assert.deepEqual(fromContext(s.layers), []);
    s = cl.storeAddLayer(s, L1);
    s = cl.storeAddLayer(s, L2);
    s = cl.storeAddLayer(s, L3);
    assert.deepEqual(fromContext(s.layers.map(l => l.id)), ['a', 'b', 'c'], 'add 追加到末尾（末位=最上层）');
    const before = s.layers.map(l => l.id).join('');
    s = cl.storeRemoveLayer(s, 'b');
    assert.deepEqual(fromContext(s.layers.map(l => l.id)), ['a', 'c']);
    assert.equal(before, 'abc', 'remove 不修改原 store');
    s = cl.storeToggleLayer(s, 'a');
    assert.equal(s.layers[0].visible, false);
    s = cl.storeToggleLayer(s, 'a');
    assert.equal(s.layers[0].visible, true);
    // move：'c'（index 1）移到 index 0
    s = cl.storeMoveLayer(s, 'c', 0);
    assert.deepEqual(fromContext(s.layers.map(l => l.id)), ['c', 'a']);
    // 非法 id / 越界 → 原样返回
    assert.equal(cl.storeMoveLayer(s, 'zz', 0), s);
    assert.equal(cl.storeMoveLayer(s, 'a', 5), s);
    // reorder：给完整的新底→顶顺序
    s2 = cl.storeAddLayer(cl.createLayerStore(), L1);
    s2 = cl.storeAddLayer(s2, L2);
    s2 = cl.storeAddLayer(s2, L3);
    s2 = cl.storeReorder(s2, ['c', 'a', 'b']);
    assert.deepEqual(fromContext(s2.layers.map(l => l.id)), ['c', 'a', 'b']);
    // reorder 缺 id → 原样
    assert.equal(cl.storeReorder(s2, ['c', 'a']), s2);
    // visibleLayers
    s3 = cl.storeToggleLayer(s2, 'a');
    assert.deepEqual(fromContext(cl.visibleLayers(s3).map(l => l.id)), ['c', 'b']);
});
