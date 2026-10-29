// #255 canvas-geometry.js 单元测试：分数坐标骨架（0..1 公共坐标）+ 点-多边形命中。
//
// 该模块是纯函数 IIFE（window.CanvasGeometry），不依赖其他画布模块，测试单独加载它。
// 它是 crop / mask / brush 三套工具共用的换算地基（canvas-image-editor.js 接线），
// 也是图层面板 / 路径命中的 0..1 坐标基础。
//
// 防的回归（全部断言可观察输出）：
//   - fractionFromPoint / pointFromFraction 往返同构（不钳制，行为与旧「直连缩放」一致，
//     含 Math.max(1, w) 之外的 box 偏移/缩放）；
//   - unitFractionFromPoint 钳制到 [0,1]（ComfyArt：uv 一律 clamp）；
//   - fractionRectFromRect / rectFromFractionRect 矩形往返；
//   - normalizeUnitRect clamp + 翻转（EditLayers norm 语义：[min, min, abs, abs]）；
//   - pointInPolygon even-odd 语义（方框内/外、凹形、环=洞）；
//   - 退化输入（空多边形 / 非有限数 / 零尺寸 box）不抛错、返回合理值。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, fromContext } from './harness.mjs';

function loadGeometry() {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-geometry.js']);
    return ctx.CanvasGeometry;
}

test('clamp/clampUnit: 区间钳制 + 非有限数回落到下限', () => {
    const geo = loadGeometry();
    assert.equal(geo.clamp(5, 0, 10), 5);
    assert.equal(geo.clamp(-1, 0, 10), 0);
    assert.equal(geo.clamp(11, 0, 10), 10);
    assert.equal(geo.clampUnit(0.2), 0.2);
    assert.equal(geo.clampUnit(-0.3), 0);
    assert.equal(geo.clampUnit(1.7), 1);
    assert.equal(geo.clamp(NaN, 0, 10), 0);
    assert.equal(geo.clamp(Infinity, 0, 10), 10);
    assert.equal(geo.clamp(-Infinity, 0, 10), 0);
});

test('fractionFromPoint: 像素 → 0..1，含 box 偏移，不钳制', () => {
    const geo = loadGeometry();
    // 与旧直连换算逐式同构：(p - box.x) / box.w
    assert.deepEqual(fromContext(geo.fractionFromPoint({ x: 25, y: 50 }, { x: 0, y: 0, w: 100, h: 200 })),
        { x: 0.25, y: 0.25 });
    // 带偏移的 box
    assert.deepEqual(fromContext(geo.fractionFromPoint({ x: 110, y: 40 }, { x: 10, y: 40, w: 100, h: 100 })),
        { x: 1, y: 0 });
    // 越界不钳制（编辑路径靠画布裁剪，不是靠坐标钳制）
    assert.deepEqual(fromContext(geo.fractionFromPoint({ x: -10, y: 300 }, { x: 0, y: 0, w: 100, h: 200 })),
        { x: -0.1, y: 1.5 });
    // 零尺寸 box 不除零
    assert.deepEqual(fromContext(geo.fractionFromPoint({ x: 5, y: 5 }, { x: 0, y: 0, w: 0, h: 0 })),
        { x: 0, y: 0 });
});

test('pointFromFraction: 0..1 → 像素，往返同构', () => {
    const geo = loadGeometry();
    const box = { x: 0, y: 0, w: 100, h: 200 };
    assert.deepEqual(fromContext(geo.pointFromFraction({ x: 0.25, y: 0.25 }, box)), { x: 25, y: 50 });
    // 往返：像素 → 分数 → 像素（box 内点）
    const p = { x: 33, y: 77 };
    assert.deepEqual(fromContext(geo.pointFromFraction(geo.fractionFromPoint(p, box), box)), p);
    // 带偏移 box 的往返
    const ob = { x: 120, y: 80, w: 320, h: 240 };
    const q = { x: 280, y: 190 };
    assert.deepEqual(fromContext(geo.pointFromFraction(geo.fractionFromPoint(q, ob), ob)), q);
});

test('unitFractionFromPoint: 钳制到 [0,1]（ComfyArt uv 语义）', () => {
    const geo = loadGeometry();
    const box = { x: 0, y: 0, w: 100, h: 100 };
    assert.deepEqual(fromContext(geo.unitFractionFromPoint({ x: -30, y: 160 }, box)), { x: 0, y: 1 });
    assert.deepEqual(fromContext(geo.unitFractionFromPoint({ x: 50, y: 25 }, box)), { x: 0.5, y: 0.25 });
});

test('fractionRectFromRect/rectFromFractionRect: 矩形往返', () => {
    const geo = loadGeometry();
    const display = { x: 0, y: 0, w: 500, h: 500 };
    const natural = { x: 0, y: 0, w: 2000, h: 2000 };
    // display 像素 → 分数 → natural 像素（applyImageCrop 的换算链）
    const frac = geo.fractionRectFromRect({ x: 100, y: 50, w: 200, h: 100 }, display);
    assert.deepEqual(fromContext(frac), { x: 0.2, y: 0.1, w: 0.4, h: 0.2 });
    assert.deepEqual(fromContext(geo.rectFromFractionRect(frac, natural)),
        { x: 400, y: 200, w: 800, h: 400 });
    // 往返
    const rect = { x: 10, y: 20, w: 30, h: 40 };
    assert.deepEqual(fromContext(geo.fractionRectFromRect(geo.rectFromFractionRect(rect, display), display)),
        fromContext(rect));
});

test('normalizeUnitRect: clamp + 翻转（EditLayers norm 语义）', () => {
    const geo = loadGeometry();
    const norm = coords => fromContext(geo.normalizeUnitRect(coords));
    // 正常方向（取二进制可精确表示的值，避免浮点噪声）
    assert.deepEqual(norm([0.25, 0.25, 0.75, 0.75]), [0.25, 0.25, 0.5, 0.5]);
    // 反向拖拽（右下角拖到左上角）
    assert.deepEqual(norm([0.75, 0.75, 0.25, 0.25]), [0.25, 0.25, 0.5, 0.5]);
    // 越界先 clamp 再归一
    assert.deepEqual(norm([-0.5, -0.5, 1.5, 1.5]), [0, 0, 1, 1]);
    // 缺项按 0
    assert.deepEqual(norm([0.5, 0.5]), [0, 0, 0.5, 0.5]);
    assert.deepEqual(norm(null), [0, 0, 0, 0]);
});

test('pointInPolygon: even-odd 命中（方框 / 凹形 / 环=洞）', () => {
    const geo = loadGeometry();
    const square = [
        { x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }
    ];
    assert.equal(geo.pointInPolygon({ x: 5, y: 5 }, square), true, '框内命中');
    assert.equal(geo.pointInPolygon({ x: -1, y: 5 }, square), false, '框外不命中');
    assert.equal(geo.pointInPolygon({ x: 5, y: 11 }, square), false, '下边外不命中');
    // 顶点/边上的边界行为随 even-odd 射线，不在产品契约内——只断言稳定返回布尔
    assert.equal(typeof geo.pointInPolygon({ x: 0, y: 5 }, square), 'boolean');

    // 凹形（L 形）：凹口内不命中
    const lShape = [
        { x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 5 },
        { x: 5, y: 5 }, { x: 5, y: 10 }, { x: 0, y: 10 }
    ];
    assert.equal(geo.pointInPolygon({ x: 2, y: 2 }, lShape), true, '实心臂命中');
    assert.equal(geo.pointInPolygon({ x: 8, y: 8 }, lShape), false, '凹口不命中');

    // even-odd：孔洞必须是独立子路径（SVG M...Z 语义）——环带命中、洞中不命中
    const outer = [
        { x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }
    ];
    const inner = [
        { x: 3, y: 3 }, { x: 7, y: 3 }, { x: 7, y: 7 }, { x: 3, y: 7 }
    ];
    assert.equal(geo.pointInPolygon({ x: 1.5, y: 5 }, [outer, inner]), true, '环带命中');
    assert.equal(geo.pointInPolygon({ x: 5, y: 5 }, [outer, inner]), false, '洞中不命中');
    assert.equal(geo.pointInPolygon({ x: 5, y: 1.5 }, [outer, inner]), true, '上环带命中');
    assert.equal(geo.pointInPolygon({ x: 5, y: 11 }, [outer, inner]), false, '外框外不命中');
});

test('pointInPolygon: 退化输入不抛错', () => {
    const geo = loadGeometry();
    assert.equal(geo.pointInPolygon({ x: 1, y: 1 }, []), false, '空多边形');
    assert.equal(geo.pointInPolygon({ x: 1, y: 1 }, null), false, 'null 多边形');
    assert.equal(geo.pointInPolygon(null, [{ x: 0, y: 0 }, { x: 2, y: 2 }]), false, 'null 点');
    assert.equal(geo.pointInPolygon({ x: NaN, y: 1 }, [{ x: 0, y: 0 }, { x: 2, y: 2 }]), false, 'NaN 点');
    // 单个非有限顶点 → 整体多边形无效（不命中、不抛错）
    assert.equal(
        geo.pointInPolygon({ x: 1, y: 1 }, [{ x: NaN, y: 1 }, { x: 0, y: 0 }, { x: 2, y: 0 }, { x: 2, y: 2 }]),
        false, '坏顶点 → 无效多边形'
    );
});

test('unitPolygonFromPoints: 像素多边形 → 0..1 多边形（越界钳制）', () => {
    const geo = loadGeometry();
    const box = { x: 0, y: 0, w: 100, h: 100 };
    const out = fromContext(geo.unitPolygonFromPoints(
        [{ x: -10, y: 50 }, { x: 50, y: 150 }, { x: 200, y: -50 }, { x: 50, y: 50 }], box
    ));
    assert.deepEqual(out, [
        { x: 0, y: 0.5 }, { x: 0.5, y: 1 }, { x: 1, y: 0 }, { x: 0.5, y: 0.5 }
    ]);
    assert.deepEqual(fromContext(geo.unitPolygonFromPoints(null, box)), []);
});
