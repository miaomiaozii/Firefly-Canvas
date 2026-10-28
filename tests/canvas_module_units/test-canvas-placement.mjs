// #246 canvas-placement.js 单元测试：freeSpot 落点扫描（右移碰撞 + 200 步上限）+ 批量行排。
//
// 该模块是纯函数 IIFE（window.CanvasPlacement），不依赖其他画布模块，测试单独加载它。
// 算法是 ComfyArt src/jobs.ts:231-243 freeSpot / :115-119 rowSpots 的直译
// （参考 docs/reference-comfyart-delta-202610.md §W3），碰撞语义 = 四边各留 GAP(40) 空隙，
// 碰撞则 x = 碰撞盒 maxX + GAP，y 不变；200 步上限兜底（x 单调递增，不会振荡）。
//
// 防的回归（全部断言可观察输出）：
//   - 无碰撞 → 原样返回锚点；
//   - 碰撞链：单挡块 / 阶梯链 / 宽重叠链，逐跳 x = hit.maxX + GAP，y 不变；
//   - GAP 边界：四边空隙恰好 = GAP 算无碰撞，< GAP 算碰撞（ComfyArt 原文条件）；
//   - 200 步上限：链长 = 上限 → 走完不 exhausted；链长 = 上限+1 → exhausted=true
//     且位置 = 第 200 跳后的坐标（位置照返，回退策略由调用方决定）；
//   - 批量行排：单行横向、游标 = 上一落点右缘 + GAP、每件各自避让静态碰撞盒；
//   - 退化输入（空 boxes / null anchor / 零尺寸 box / 负 gap 选项）不抛错。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, fromContext } from './harness.mjs';

function loadPlacement() {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['canvas-placement.js']);
    return ctx.CanvasPlacement;
}

// 碰撞盒简写：b(x, y, w, h)
const b = (x, y, w, h) => ({ x, y, w, h });

test('常量: GAP=40 / MAX_STEPS=200（ComfyArt jobs.ts 同值）', () => {
    const P = loadPlacement();
    assert.equal(P.GAP, 40);
    assert.equal(P.MAX_STEPS, 200);
});

test('findFreeSpot: 无碰撞 → 原样返回锚点（exhausted=false）', () => {
    const P = loadPlacement();
    // 空 boxes
    assert.deepEqual(fromContext(P.findFreeSpot({ x: 100, y: 50 }, { w: 80, h: 60 }, [])),
        { x: 100, y: 50, exhausted: false });
    // 不重叠的盒（水平错开）
    const out = fromContext(P.findFreeSpot({ x: 500, y: 50 }, { w: 80, h: 60 }, [b(0, 0, 100, 50)]));
    assert.deepEqual(out, { x: 500, y: 50, exhausted: false });
    // 垂直错开（y 区间 +GAP 不交）
    const out2 = fromContext(P.findFreeSpot({ x: 50, y: 200 }, { w: 80, h: 60 }, [b(0, 0, 100, 50)]));
    assert.equal(out2.exhausted, false, 'y 间距 > h + 2*GAP 不碰撞');
});

test('findFreeSpot: 单挡块碰撞 → x = 碰撞盒 maxX + GAP，y 不变', () => {
    const P = loadPlacement();
    // 挡块 (100,0,200,50)：锚点 (150,10) 碰撞（四边 GAP 内）→ x = 300+40 = 340
    const out = fromContext(P.findFreeSpot({ x: 150, y: 10 }, { w: 80, h: 60 }, [b(100, 0, 200, 50)]));
    assert.deepEqual(out, { x: 340, y: 10, exhausted: false });
    // 340 处不再碰撞：340 >= 300+40（左缘出 GAP）
});

test('findFreeSpot: 碰撞链（阶梯）逐跳右移，y 恒不变', () => {
    const P = loadPlacement();
    // 三个阶梯挡块：每块右缘比下一块左缘近（链式可达）
    const boxes = [
        b(100, 0, 100, 50),   // maxX=200
        b(180, 0, 150, 50),   // maxX=330
        b(300, 0, 200, 50),   // maxX=500
    ];
    // 锚点 (50,10)：撞 box0 → x=240；240 撞 box1（240<370 且 240+80+40=360>180）→ x=370；
    // 370 撞 box2（370<540 且 370+120=490>300）→ x=540；540 不撞任何块（540>=500+40）。
    const out = fromContext(P.findFreeSpot({ x: 50, y: 10 }, { w: 80, h: 60 }, boxes));
    assert.deepEqual(out, { x: 540, y: 10, exhausted: false }, '三跳链后落在第 3 块右缘 +GAP');
});

test('findFreeSpot: GAP 边界——空隙恰好 40 无碰撞，39 碰撞', () => {
    const P = loadPlacement();
    // 挡块 (0,0,100,50)：锚点 x=140 → 140 < 100+40? 否（140=140 不满足 <）→ 无碰撞
    const exact = fromContext(P.findFreeSpot({ x: 140, y: 10 }, { w: 10, h: 10 }, [b(0, 0, 100, 50)]));
    assert.equal(exact.exhausted, false, '左缘距挡块右缘恰好 GAP → 无碰撞');
    assert.equal(exact.x, 140);
    // 锚点 x=139 → 139 < 140 → 碰撞 → 右移到 140
    const near = fromContext(P.findFreeSpot({ x: 139, y: 10 }, { w: 10, h: 10 }, [b(0, 0, 100, 50)]));
    assert.deepEqual(near, { x: 140, y: 10, exhausted: false}, '< GAP 碰撞，一步出 GAP');
    // y 侧 GAP：挡块 (0,0,100,50)，新盒 h=10，y=90 → y < 50+40? 否 → 无碰撞
    const yExact = fromContext(P.findFreeSpot({ x: 50, y: 90 }, { w: 10, h: 10 }, [b(0, 0, 100, 50)]));
    assert.equal(yExact.exhausted, false, 'y 下缘距挡块下缘恰好 GAP → 无碰撞');
    const yNear = fromContext(P.findFreeSpot({ x: 50, y: 89 }, { w: 10, h: 10 }, [b(0, 0, 100, 50)]));
    assert.equal(yNear.exhausted, false, 'y 侧 <GAP 碰撞后一步出 GAP');
    assert.equal(yNear.y, 89, 'y 恒不变');
    assert.deepEqual([yNear.x, yNear.y], [140, 89], 'y 侧碰撞 → x = 挡块 maxX + GAP');
});

test('findFreeSpot: 宽重叠链——同 y 带内 x 严格单调前进直到出 GAP', () => {
    const P = loadPlacement();
    // 两块同 y 带、水平大幅重叠的宽块：逐跳 x = maxX + GAP
    const boxes = [b(0, 0, 1000, 50), b(2, 0, 900, 50)];
    const out = fromContext(P.findFreeSpot({ x: 10, y: 10 }, { w: 10, h: 10 }, boxes));
    // 撞 box0（maxX=1000）→ x=1040；1040 < 902+40=942? 否 → 不撞 box1。
    assert.deepEqual(out, { x: 1040, y: 10, exhausted: false });
});

test('findFreeSpot: 200 步上限——链长 200 走完不 exhausted，链长 201 则 exhausted=true 且位置照返', () => {
    const P = loadPlacement();
    // 阶梯链：box i = (100+2i, 0, 10000, 50) → maxX = 10100+2i（严格递增、水平可达）
    // 锚点 (190, 20) 起每跳 x = 10140+2i；N 块走完需 N 跳。
    const chain = n => Array.from({ length: n }, (_, i) => b(100 + 2 * i, 0, 10000, 50));
    // 链长 200 = MAX_STEPS：第 200 跳后 x = 10140 + 2*199 = 10538，无后续块 → 不 exhausted
    const justFit = fromContext(P.findFreeSpot({ x: 190, y: 20 }, { w: 80, h: 60 }, chain(200)));
    assert.equal(justFit.exhausted, false, '恰好 200 跳走完链');
    assert.equal(justFit.x, 10538, 'x = 10140 + 2*199');
    assert.equal(justFit.y, 20, 'y 恒不变');
    // 链长 201：200 跳用尽，停在 x=10538 仍撞第 201 块（maxX=10500，10538 < 10540）
    const over = fromContext(P.findFreeSpot({ x: 190, y: 20 }, { w: 80, h: 60 }, chain(201)));
    assert.equal(over.exhausted, true, '200 步用尽仍碰撞 → exhausted=true（位置照返）');
    assert.equal(over.x, 10538, '位置 = 第 200 跳后的坐标（回退由调用方决定）');
    assert.equal(over.y, 20);
});

test('findFreeSpot: 自定义 maxSteps/gap 选项生效', () => {
    const P = loadPlacement();
    const boxes = [b(100, 0, 10000, 50)];
    // maxSteps=0：一步不扫，锚点碰撞 → exhausted=true、位置=锚点
    const zero = fromContext(P.findFreeSpot({ x: 150, y: 10 }, { w: 10, h: 10 }, boxes, { maxSteps: 0 }));
    assert.deepEqual(zero, { x: 150, y: 10, exhausted: true });
    // gap=0：碰撞判定不留空隙，落点贴边（x = maxX）
    const tight = fromContext(P.findFreeSpot({ x: 150, y: 10 }, { w: 10, h: 10 }, [b(100, 0, 100, 50)], { gap: 0 }));
    assert.deepEqual(tight, { x: 200, y: 10, exhausted: false });
    // 自定义 gap 进入移动公式：gap=10 → x = maxX + 10
    const g10 = fromContext(P.findFreeSpot({ x: 150, y: 10 }, { w: 10, h: 10 }, [b(100, 0, 100, 50)], { gap: 10 }));
    assert.deepEqual(g10, { x: 210, y: 10, exhausted: false });
});

test('findFreeSpot: 退化输入不抛错', () => {
    const P = loadPlacement();
    // null anchor → (0,0) 起扫
    assert.deepEqual(fromContext(P.findFreeSpot(null, { w: 10, h: 10 }, [])),
        { x: 0, y: 0, exhausted: false });
    // null/非数组 boxes → 视为空
    assert.deepEqual(fromContext(P.findFreeSpot({ x: 5, y: 6 }, { w: 10, h: 10 }, null)),
        { x: 5, y: 6, exhausted: false });
    // 零尺寸 box 不参与碰撞
    assert.deepEqual(fromContext(P.findFreeSpot({ x: 0, y: 0 }, { w: 10, h: 10 }, [b(0, 0, 0, 0)])),
        { x: 0, y: 0, exhausted: false });
    // 负尺寸 size 按 0 处理（零宽盒仍可被 GAP 判定碰撞：x+0+GAP > box.x）
    const neg = fromContext(P.findFreeSpot({ x: 5, y: 5 }, { w: -10, h: 10 }, [b(0, 0, 100, 50)]));
    assert.deepEqual(neg, { x: 140, y: 5, exhausted: false}, '零宽盒碰撞 → x = maxX + GAP');
    // 坏选项（NaN gap / 负 maxSteps）回落到默认/0 步，不抛错
    assert.deepEqual(fromContext(P.findFreeSpot({ x: 5, y: 5 }, { w: 10, h: 10 }, [], { gap: NaN, maxSteps: -3 })),
        { x: 5, y: 5, exhausted: false });
});

test('rowSpots: 无碰撞 → 单行横排，游标 = 上一落点右缘 + GAP', () => {
    const P = loadPlacement();
    // 三个 100×80 的件从 (10, 20) 起排：x = 10 / 150 / 290（10+100+40 / 150+100+40）
    const spots = fromContext(P.rowSpots({ x: 10, y: 20 }, [{ w: 100, h: 80 }, { w: 100, h: 80 }, { w: 100, h: 80 }], []));
    assert.deepEqual(spots, [
        { x: 10, y: 20, exhausted: false },
        { x: 150, y: 20, exhausted: false },
        { x: 290, y: 20, exhausted: false },
    ]);
    // 不同尺寸：游标按各自 w 推进
    const mixed = fromContext(P.rowSpots({ x: 0, y: 0 }, [{ w: 50, h: 80 }, { w: 120, h: 60 }], []));
    assert.deepEqual(mixed, [
        { x: 0, y: 0, exhausted: false },
        { x: 90, y: 0, exhausted: false },
    ]);
});

test('rowSpots: 每件各自避让静态碰撞盒（ComfyArt rowSpots 语义）', () => {
    const P = loadPlacement();
    // 挡块 (100,0,200,50)（maxX=300）：第 1 件锚点 (0,10,100×80)：0 < 340 且 0+100+40=140 > 100
    // 且 y 区间交 → 撞 → 第 1 件 x = 300+40 = 340；游标 340+100+40=480；
    // 第 2 件 (480,10)：480 < 340? 否 → 不撞 → 停在 480。
    const spots = fromContext(P.rowSpots({ x: 0, y: 10 }, [{ w: 100, h: 80 }, { w: 100, h: 80 }], [b(100, 0, 200, 50)]));
    assert.deepEqual(spots, [
        { x: 340, y: 10, exhausted: false },
        { x: 480, y: 10, exhausted: false },
    ]);
    // 第 1 件被推到挡块右边后，第 2 件不再被该挡块影响（游标已出 GAP）。
});

test('rowSpots: 步数上限逐件独立生效；空 sizes → 空行', () => {
    const P = loadPlacement();
    // maxSteps=0：每件都停在游标处，exhausted = 该位置的碰撞判定
    const spots = fromContext(
        P.rowSpots({ x: 0, y: 10 }, [{ w: 100, h: 80 }, { w: 100, h: 80 }], [b(100, 0, 200, 50)], { maxSteps: 0 }));
    assert.equal(spots.length, 2);
    assert.equal(spots[0].x, 0, 'maxSteps=0 → 一步不扫，位置=游标');
    assert.equal(spots[0].exhausted, true, '游标处碰撞 → exhausted=true');
    assert.equal(spots[1].x, 140, '游标仍按 位置+宽+GAP 推进（0+100+40）');
    assert.equal(spots[1].exhausted, true, '140 处仍在挡块 GAP 内（140<340 且 140+140=280>100）→ 碰撞');
    // 空 sizes
    assert.deepEqual(fromContext(P.rowSpots({ x: 1, y: 2 }, [], [b(0, 0, 10, 10)])), []);
    assert.deepEqual(fromContext(P.rowSpots({ x: 1, y: 2 }, null, [])), []);
});
