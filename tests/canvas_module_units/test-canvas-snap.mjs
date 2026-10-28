// #253 canvas-snap.js 单元测试：拖拽对齐吸附纯函数（center/edge 阈值吸附 + 参考线）。
//
// 该模块是纯函数 IIFE（window.FireflySnap）+ 独立 DOM overlay 层（window.FireflySnapUI），
// 不依赖其他画布模块（反向引用棘轮 0），测试单独加载它。
//
// 防的回归（全部断言可观察输出）：
//   - 阈值内 left/right/centerX 与 top/bottom/centerY 吸附，取每轴最小位移；
//   - 阈值外不吸附（dx/dy=0 且无参考线）；
//   - enableCentering=false 时中心线失效，边线仍生效；
//   - 缺失 cx/cy 时按 x+width/2 推导（nodeRect 不带 cx/cy）；
//   - 非法输入（null/非有限数/空 others）不抛错、返回零位移；
//   - snapDelta 叠加语义；guidesToPixels 参考线延伸到画布边界。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, fromContext } from './harness.mjs';

function loadSnap() {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-snap.js']);
    return ctx.FireflySnap;
}

const box = (x, y, w, h) => ({ x, y, width: w, height: h });

test('left-edge alignment inside threshold snaps and emits a vertical guide', () => {
    const snap = loadSnap();
    const target = box(103, 400, 50, 40);
    const others = [box(100, 200, 50, 40)];
    const r = fromContext(snap.computeSnap(target, others, { threshold: 8 }));
    assert.equal(r.dx, -3);
    assert.equal(r.dy, 0);
    assert.deepEqual(r.guides, [{ axis: 'x', value: 100 }]);
});

test('right-edge alignment snaps with guide at the sibling right edge', () => {
    const snap = loadSnap();
    const target = box(182, 400, 50, 40);   // right edge 232
    const others = [box(150, 200, 80, 40)]; // right edge 230 (left edge 150 far away)
    const r = fromContext(snap.computeSnap(target, others, { threshold: 8 }));
    assert.equal(r.dx, -2);
    assert.deepEqual(r.guides, [{ axis: 'x', value: 230 }]);
});

test('no snap beyond threshold (no guides, zero delta)', () => {
    const snap = loadSnap();
    const target = box(110, 400, 50, 40);   // 10px from sibling left edge
    const others = [box(100, 200, 50, 40)];
    assert.deepEqual(fromContext(snap.computeSnap(target, others, { threshold: 8 })), { dx: 0, dy: 0, guides: [] });
});

test('smallest delta wins when multiple candidates are inside threshold', () => {
    const snap = loadSnap();
    const target = box(103, 400, 50, 40);
    // sibling A left edge 105 (delta +2); sibling B left edge 102 (delta -1) -> B wins.
    const others = [box(105, 200, 50, 40), box(102, 300, 50, 40)];
    const r = fromContext(snap.computeSnap(target, others, { threshold: 8 }));
    assert.equal(r.dx, -1);
    assert.deepEqual(r.guides, [{ axis: 'x', value: 102 }]);
});

test('center-x alignment works when edges are far apart (cx/cy derived when missing)', () => {
    const snap = loadSnap();
    // target cx 50 == sibling cx 50, but edges are 25px apart (beyond threshold).
    const target = box(25, 400, 50, 40);
    const others = [box(0, 200, 100, 40)];
    const r = fromContext(snap.computeSnap(target, others, { threshold: 8 }));
    assert.equal(r.dx, 0);
    assert.deepEqual(r.guides, [{ axis: 'x', value: 50 }]);
});

test('enableCentering=false disables center guides but keeps edge snapping', () => {
    const snap = loadSnap();
    // center-only alignment scenario: edges 25px apart, centers equal.
    const r = fromContext(snap.computeSnap(box(25, 400, 50, 40), [box(0, 200, 100, 40)], { threshold: 8, enableCentering: false }));
    assert.deepEqual(r, { dx: 0, dy: 0, guides: [] });
    // edge snap still works with centering off.
    const r2 = fromContext(snap.computeSnap(box(103, 400, 50, 40), [box(100, 200, 50, 40)], { threshold: 8, enableCentering: false }));
    assert.equal(r2.dx, -3);
});

test('y-axis snaps independently (top/bottom/centerY) and combines with x guides', () => {
    const snap = loadSnap();
    const target = box(100, 397, 50, 40); // top 397
    const others = [box(100, 400, 50, 40)];
    const r = fromContext(snap.computeSnap(target, others, { threshold: 8 }));
    assert.equal(r.dx, 0);
    assert.equal(r.dy, 3);
    assert.deepEqual(r.guides, [{ axis: 'x', value: 100 }, { axis: 'y', value: 400 }]);
});

test('custom threshold is respected', () => {
    const snap = loadSnap();
    const target = box(112, 400, 50, 40);
    const others = [box(100, 200, 50, 40)];
    assert.equal(fromContext(snap.computeSnap(target, others, { threshold: 12 })).dx, -12);
    assert.equal(fromContext(snap.computeSnap(target, others, { threshold: 11 })).dx, 0);
});

test('invalid inputs never throw and return zero deltas', () => {
    const snap = loadSnap();
    assert.deepEqual(fromContext(snap.computeSnap(null, [box(0, 0, 1, 1)])), { dx: 0, dy: 0, guides: [] });
    assert.deepEqual(fromContext(snap.computeSnap(box(NaN, 10, 50, 40), [box(0, 0, 1, 1)])), { dx: 0, dy: 0, guides: [] });
    assert.deepEqual(fromContext(snap.computeSnap(box(10, 10, 50, 40), [null, { x: undefined, y: undefined }])), { dx: 0, dy: 0, guides: [] });
    assert.deepEqual(fromContext(snap.computeSnap(box(10, 10, 50, 40), [])), { dx: 0, dy: 0, guides: [] });
});

test('snapDelta adds the snap adjustment onto the drag delta (null-safe)', () => {
    const snap = loadSnap();
    assert.deepEqual(fromContext(snap.snapDelta({ dx: 5, dy: -2 }, { dx: -1, dy: 3 })), { dx: 4, dy: 1 });
    assert.deepEqual(fromContext(snap.snapDelta({ dx: 5, dy: -2 }, { dx: 0, dy: 0 })), { dx: 5, dy: -2 });
    assert.deepEqual(fromContext(snap.snapDelta(null, null)), { dx: 0, dy: 0 });
    assert.deepEqual(fromContext(snap.snapDelta({ dx: 1, dy: 2 }, null)), { dx: 1, dy: 2 });
});

test('guidesToPixels extends guides to the canvas bounds', () => {
    const snap = loadSnap();
    const canvas = { width: 800, height: 600 };
    assert.deepEqual(
        fromContext(snap.guidesToPixels([{ axis: 'x', value: 100 }, { axis: 'y', value: 200 }], canvas)),
        [
            { axis: 'x', value: 100, from: 0, to: 600 },
            { axis: 'y', value: 200, from: 0, to: 800 },
        ],
    );
    assert.deepEqual(fromContext(snap.guidesToPixels([], canvas)), []);
    assert.deepEqual(fromContext(snap.guidesToPixels([null, {}], canvas)), []);
});
