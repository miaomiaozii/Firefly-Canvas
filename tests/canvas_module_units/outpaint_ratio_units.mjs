// #194：outpaint-ratios.js 的行为单测。
//
// 防的回归：扩图的目标矩形由这里算。#187 刚暴露过「扩图的分辨率参数被丢弃」，
// 而 Python 侧只有逐字钉源码文本的契约测试（`tests/test_outpaint_ratios.py`），
// 改一行源码就会红、但改错数值不会红。这里直接算数值。

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadCanvasScripts, normalizeApi } from './harness.mjs';

const { OutpaintRatios } = (() => {
    // 该文件是 UMD：harness 里走 root 挂载。跨 realm 对象与函数返回值都必须
    // 归一化，否则 deepStrictEqual 会因原型不同而失败。
    const { context } = loadCanvasScripts(['outpaint-ratios.js']);
    return { OutpaintRatios: normalizeApi(context.OutpaintRatios) };
})();

const { PRESETS, parsePreset, fitContaining } = OutpaintRatios;

test('预设清单是固定契约，顺序与内容都不能漂', () => {
    assert.deepEqual(PRESETS, [
        'free', 'source', '1:1', '4:3', '3:4', '16:9', '9:16', '3:2', '2:3', '21:9', '9:21',
    ]);
});

test('parsePreset：比例约分到最简', () => {
    assert.deepEqual(parsePreset('4:2'), { width: 2, height: 1, ratio: 2 });
    assert.deepEqual(parsePreset('16:9'), { width: 16, height: 9, ratio: 16 / 9 });
    assert.deepEqual(parsePreset('8:6'), { width: 4, height: 3, ratio: 4 / 3 });
});

test('parsePreset：free / source 表示不套比例，返回 null', () => {
    assert.equal(parsePreset('free'), null);
    assert.equal(parsePreset('source'), null);
});

test('parsePreset：非法输入一律 null，不得抛异常', () => {
    for (const bad of ['', '   ', 'abc', '16', '16:', ':9', '16:9:9', 'w:h']) {
        assert.equal(parsePreset(bad), null, `parsePreset(${JSON.stringify(bad)})`);
    }
});

test('parsePreset：0 或负数边长必须拒绝（否则除零得到 Infinity）', () => {
    for (const bad of ['0:9', '16:0', '0:0', '-1:2', '16:-9']) {
        assert.equal(parsePreset(bad), null, `parsePreset(${JSON.stringify(bad)})`);
    }
});

test('parsePreset：比例两侧允许空格', () => {
    assert.deepEqual(parsePreset(' 16 : 9 '), { width: 16, height: 9, ratio: 16 / 9 });
});

test('fitContaining：目标边长必须向上取到 16 的倍数（服务商按 16 对齐）', () => {
    const box = fitContaining(100, 100, '1:1');
    assert.equal(box.width, 112);
    assert.equal(box.height, 112);
    assert.equal(box.width % 16, 0);
    assert.equal(box.height % 16, 0);
});

test('fitContaining：目标矩形必须完整包住原图且居中', () => {
    const box = fitContaining(100, 80, '4:3');
    assert.equal(box.width, 128);
    assert.equal(box.height, 96);
    assert.ok(box.width >= 100 && box.height >= 80, '目标必须包住原图');
    assert.equal(box.offsetX, 14);
    assert.equal(box.offsetY, 8);
    assert.equal(box.offsetX * 2, box.width - 100);
    assert.equal(box.offsetY * 2, box.height - 80);
});

test('fitContaining：free / source / 非法预设时原样返回，不改变尺寸', () => {
    for (const preset of ['free', 'source', 'bogus']) {
        const box = fitContaining(100, 80, preset);
        assert.equal(box.width, 100, `preset=${preset}`);
        assert.equal(box.height, 80, `preset=${preset}`);
        assert.equal(box.offsetX, 0, `preset=${preset}`);
        assert.equal(box.offsetY, 0, `preset=${preset}`);
    }
    assert.equal(fitContaining(100, 80, 'bogus').preset, 'bogus');
    assert.equal(fitContaining(100, 80).preset, 'free');
});

test('fitContaining：0 / 负数 / NaN 边长不得产生 0 或 NaN 目标', () => {
    for (const w of [0, -50, Number.NaN, undefined]) {
        for (const h of [0, -50, Number.NaN, undefined]) {
            const box = fitContaining(w, h, '1:1');
            assert.ok(Number.isFinite(box.width) && box.width >= 1, `w=${String(w)} h=${String(h)}`);
            assert.ok(Number.isFinite(box.height) && box.height >= 1, `w=${String(w)} h=${String(h)}`);
        }
    }
});

test('fitContaining：极端细长比例不得退化成 0 宽', () => {
    const box = fitContaining(1000, 10, '21:9');
    assert.ok(box.width >= 1000);
    assert.ok(box.height >= 10);
    assert.equal(box.width % 16, 0);
    assert.equal(box.height % 16, 0);
});
