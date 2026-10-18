// #194：canvas-layout-utils.js 的行为单测。
//
// 防的回归：这个文件是纯几何工具，但它的输出直接决定分组卡片和单图节点的
// 宽高。#165（分组框拖动回弹）和 #186（1 像素抖动）都说明布局数值一旦漂移，
// 只有像素级证据能发现，而像素证据慢且间歇。这里把「列数选择、高度反算、
// 上下限钳制」的边界直接钉住。
//
// 注意：个别断言钉的是**当前实际行为**（含可疑行为），文件里明确标注。
// 那不是规格声明，只是特征化测试（characterization test）——真要改行为时，
// 这条断言必须连同注释一起改，不能只改代码。

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadCanvasScripts } from './harness.mjs';

const { take } = loadCanvasScripts(['canvas-layout-utils.js']);
const {
    BATCH_THUMB,
    SMART_GROUP_LAYOUT,
    SINGLE_IMAGE_LAYOUT,
    getBatchThumbGridSize,
    getSingleImageLayoutSize,
    getUngroupedImageSize,
    smartGroupLayoutSize,
    smartGroupZoom,
} = take([
    'BATCH_THUMB',
    'SMART_GROUP_LAYOUT',
    'SINGLE_IMAGE_LAYOUT',
    'getBatchThumbGridSize',
    'getSingleImageLayoutSize',
    'getUngroupedImageSize',
    'smartGroupLayoutSize',
    'smartGroupZoom',
]);

test('批量缩略图网格：优先选能整除且行数落在 2..4 的列数', () => {
    assert.equal(getBatchThumbGridSize(4).cols, 2);
    assert.equal(getBatchThumbGridSize(4).rows, 2);
    assert.equal(getBatchThumbGridSize(6).cols, 3);
    assert.equal(getBatchThumbGridSize(6).rows, 2);
    assert.equal(getBatchThumbGridSize(8).cols, 4);
    assert.equal(getBatchThumbGridSize(8).rows, 2);
});

test('批量缩略图网格：4 张不会选成 4x1（行数必须 >= 2）', () => {
    const grid = getBatchThumbGridSize(4);
    assert.notEqual(grid.cols, 4, '4 张排成一行会让分组卡片变成细长条');
});

test('批量缩略图网格：count 被钳到 2..8，越界不产生空位或超大网格', () => {
    for (const count of [0, 1, 2]) {
        const grid = getBatchThumbGridSize(count);
        assert.equal(grid.cols, 2, `count=${count}`);
        assert.equal(grid.rows, 1, `count=${count}`);
    }
    for (const count of [9, 99]) {
        const grid = getBatchThumbGridSize(count);
        assert.deepEqual(
            { cols: grid.cols, rows: grid.rows },
            { cols: 4, rows: 2 },
            `count=${count} 应当按 8 张处理`,
        );
    }
});

test('批量缩略图网格：不能整除时退回 sqrt 列数（5 张 = 3x2，3 张 = 2x2）', () => {
    assert.deepEqual(
        { cols: getBatchThumbGridSize(5).cols, rows: getBatchThumbGridSize(5).rows },
        { cols: 3, rows: 2 },
    );
    assert.deepEqual(
        { cols: getBatchThumbGridSize(3).cols, rows: getBatchThumbGridSize(3).rows },
        { cols: 2, rows: 2 },
    );
});

test('批量缩略图网格：盒子尺寸 = 网格 + 内边距，且含 summary 行高度', () => {
    const grid = getBatchThumbGridSize(4);
    const expectedGridW = 2 * BATCH_THUMB.WIDTH + 1 * BATCH_THUMB.GAP;
    const expectedGridH = 2 * BATCH_THUMB.HEIGHT + 1 * BATCH_THUMB.GAP;
    assert.equal(grid.gridW, expectedGridW);
    assert.equal(grid.gridH, expectedGridH);
    assert.equal(grid.boxW, expectedGridW + SMART_GROUP_LAYOUT.PADDING * 2);
    assert.equal(
        grid.boxH,
        SMART_GROUP_LAYOUT.SUMMARY_H + SMART_GROUP_LAYOUT.SUMMARY_GAP + expectedGridH + SMART_GROUP_LAYOUT.PADDING * 2,
    );
});

test('单图布局：宽不超限时按宽算高', () => {
    const box = getSingleImageLayoutSize({ w: 4, h: 3 });
    assert.equal(box.imgW, SINGLE_IMAGE_LAYOUT.MAX_WIDTH);
    assert.equal(box.imgH, 195);
    assert.equal(box.boxW, 260 + 32);
    assert.equal(box.boxH, 195 + 32);
    assert.equal(box.groupBoxH, 14 + 8 + 195 + 32);
});

test('单图布局：高度超限必须按高反算宽（否则竖图撑破卡片）', () => {
    const box = getSingleImageLayoutSize({ w: 1, h: 10 });
    assert.equal(box.imgH, SINGLE_IMAGE_LAYOUT.MAX_HEIGHT);
    assert.equal(box.imgW, 38);
    assert.ok(box.imgH <= SINGLE_IMAGE_LAYOUT.MAX_HEIGHT);
});

test('单图布局：aspect.w 为 0 时不得除零产生 Infinity', () => {
    const box = getSingleImageLayoutSize({ w: 0, h: 3 });
    assert.ok(Number.isFinite(box.imgH));
    assert.ok(Number.isFinite(box.boxH));
});

test('单图布局：maxWidth 覆盖默认值', () => {
    const box = getSingleImageLayoutSize({ w: 16, h: 9 }, 640);
    assert.equal(box.imgW, 640);
    assert.equal(box.imgH, 360);
});

test('解组图片：常规比例保持 136x102', () => {
    assert.deepEqual(getUngroupedImageSize({ w: 4, h: 3 }), { w: 136, h: 102 });
});

test('解组图片：极竖比例的下限钳制会重新撑破高度上限（当前行为，见注释）', () => {
    // 当前实现按 上限→下限 顺序钳制，最后一步把宽抬到 80 后没有再检查高度，
    // 1:10 的图会得到 h=800。这是**当前实际行为**，不是期望行为；
    // 若要修，改这条断言的同时要在 issue 里记录行为变更。
    assert.deepEqual(getUngroupedImageSize({ w: 1, h: 10 }), { w: 80, h: 800 });
});

test('分组卡片：缺尺寸用默认值，legacy 高度 220 一律升回 286', () => {
    assert.deepEqual(smartGroupLayoutSize({}), { width: 340, height: 286 });
    assert.deepEqual(smartGroupLayoutSize({ w: 400, h: 220 }), { width: 400, height: 286 });
});

test('分组卡片：小于最小宽度的 w 被忽略，小于最小高度的 h 被抬到最小值', () => {
    assert.equal(smartGroupLayoutSize({ w: 100 }).width, 340);
    assert.equal(smartGroupLayoutSize({ w: 400, h: 100 }).height, 130);
    assert.deepEqual(smartGroupLayoutSize({ w: 400, h: 500 }), { width: 400, height: 500 });
});

test('成员缩放：非正数与非有限数一律退回 1', () => {
    assert.equal(smartGroupZoom({}), 1);
    assert.equal(smartGroupZoom({ _memberZoom: 2.5 }), 2.5);
    for (const bad of [0, -1, Number.NaN, 'abc', null, undefined]) {
        assert.equal(smartGroupZoom({ _memberZoom: bad }), 1, `_memberZoom=${String(bad)}`);
    }
});
