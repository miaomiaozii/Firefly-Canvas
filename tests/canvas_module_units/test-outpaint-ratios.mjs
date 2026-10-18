// #194 outpaint-ratios.js 行为单元测试。
// 防的回归：外扩（outpaint）画布的尺寸计算。parsePreset 负责把比例档位归一化
// （约分、非法输入一律回退 free），fitContaining 负责把源图「含住」到 16px 整数
// 单位的画布上并居中——这里任何一个边界算错，外扩请求就会带着错误的 canvas 尺寸
// 发给 API，或画布中心偏移。纯逻辑模块，无 DOM 依赖，用 fake core 单独加载。
// 注意：vm context 是独立 V8 realm，被测函数返回的对象/数组原型与宿主侧不同，
// assert/strict 的 deepEqual 原型敏感——所有值断言先 fromContext 往返（见 harness.mjs）。
import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, fromContext } from './harness.mjs';

function freshApi() {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['outpaint-ratios.js']);
    const api = ctx.OutpaintRatios;
    assert.ok(api && typeof api === 'object', 'OutpaintRatios must be exposed on globalThis by the UMD wrapper');
    return api;
}

test('UMD wrapper exposes OutpaintRatios on globalThis with the 3 public members', () => {
    const { ctx } = createCanvasContext();
    loadModules(ctx, ['outpaint-ratios.js']);
    assert.ok(ctx.OutpaintRatios, 'classic script must attach the api to globalThis');
    for (const name of ['PRESETS', 'parsePreset', 'fitContaining']) {
        assert.ok(name in ctx.OutpaintRatios, `OutpaintRatios.${name} missing`);
    }
});

test('PRESETS is exactly the 11 supported ratio slots (order included)', () => {
    assert.deepEqual(
        fromContext(freshApi().PRESETS),
        ['free', 'source', '1:1', '4:3', '3:4', '16:9', '9:16', '3:2', '2:3', '21:9', '9:21'],
    );
});

test('parsePreset: pass-through and invalid inputs all resolve to null (free)', () => {
    const { parsePreset } = freshApi();
    assert.equal(parsePreset('free'), null);
    assert.equal(parsePreset('source'), null);
    assert.equal(parsePreset('FREE'), null, 'the free/source check is case-sensitive');
    assert.equal(parsePreset(''), null);
    assert.equal(parsePreset(null), null);
    assert.equal(parsePreset(undefined), null);
    assert.equal(parsePreset('abc'), null);
    assert.equal(parsePreset('16:9:1'), null);
    assert.equal(parsePreset('1.5:1'), null, 'decimal ratios are not a supported slot');
    assert.equal(parsePreset('-4:3'), null, 'negative width rejected by the strict ^d+:d$ shape');
    assert.equal(parsePreset('4:-3'), null);
    assert.equal(parsePreset('0:3'), null, 'zero side is not a ratio');
    assert.equal(parsePreset('3:0'), null);
    assert.equal(parsePreset('0:0'), null);
});

test('parsePreset: trims whitespace around the colon and normalizes by gcd', () => {
    const { parsePreset } = freshApi();
    assert.deepEqual(fromContext(parsePreset('1:1')), { width: 1, height: 1, ratio: 1 });
    assert.deepEqual(fromContext(parsePreset(' 4 : 3 ')), { width: 4, height: 3, ratio: 4 / 3 });
    assert.deepEqual(fromContext(parsePreset('8:6')), { width: 4, height: 3, ratio: 4 / 3 }, '8:6 must reduce to 4:3');
    assert.deepEqual(fromContext(parsePreset('12:8')), { width: 3, height: 2, ratio: 3 / 2 });
    assert.deepEqual(fromContext(parsePreset('2:4')), { width: 1, height: 2, ratio: 0.5 });
    assert.deepEqual(fromContext(parsePreset('16:9')), { width: 16, height: 9, ratio: 16 / 9 });
    // 模块永远用 gcd 约分：21:9 -> 7:3（ratio 字段保留原始值 21/9）。
    assert.deepEqual(fromContext(parsePreset('21:9')), { width: 7, height: 3, ratio: 21 / 9 });
});

test('fitContaining: free/source/unknown preset passes the source size through with zero offset', () => {
    const { fitContaining } = freshApi();
    assert.deepEqual(fromContext(fitContaining(100, 80, 'free')), { width: 100, height: 80, offsetX: 0, offsetY: 0, preset: 'free' });
    assert.deepEqual(fromContext(fitContaining(100, 80, 'source')), { width: 100, height: 80, offsetX: 0, offsetY: 0, preset: 'source' });
    assert.deepEqual(fromContext(fitContaining(100, 80, 'bogus')), { width: 100, height: 80, offsetX: 0, offsetY: 0, preset: 'bogus' });
    assert.deepEqual(fromContext(fitContaining(100, 80, undefined)), { width: 100, height: 80, offsetX: 0, offsetY: 0, preset: 'free' });
});

test('fitContaining: non-numeric and non-positive source sizes clamp to a 16x16 canvas', () => {
    const { fitContaining } = freshApi();
    // 非法/非正源尺寸被 max(1, ...) 收敛为 1x1 最小源，再居中放进 16x16 画布：
    // offsetX/offsetY = floor((16-1)/2) = 7（源不是整块画布，是最小 1px 源）。
    const expected = { width: 16, height: 16, offsetX: 7, offsetY: 7, preset: '1:1' };
    assert.deepEqual(fromContext(fitContaining(null, null, '1:1')), expected);
    assert.deepEqual(fromContext(fitContaining(0, 0, '1:1')), expected, '0 is falsy in `Number(x) || 1`, then clamped by max(1, ...)');
    assert.deepEqual(fromContext(fitContaining(-5, -5, '1:1')), expected);
    // 'abc' -> 1px 宽；高 7px 保留：offsetX=floor(15/2)=7, offsetY=floor(9/2)=4。
    assert.deepEqual(fromContext(fitContaining('abc', 7, '1:1')), { width: 16, height: 16, offsetX: 7, offsetY: 4, preset: '1:1' });
});

test('fitContaining: target snaps UP to 16px units and never shrinks the source', () => {
    const { fitContaining } = freshApi();
    // 100x100 @ 4:3 -> rawUnits = ceil(max(25, 33.33)) = 34 -> units = 48 -> 192x144
    assert.deepEqual(fromContext(fitContaining(100, 100, '4:3')), { width: 192, height: 144, offsetX: 46, offsetY: 22, preset: '4:3' });
    // Source already exactly 4:3: rawUnits = 4, still rounded up to 16 units -> 64x48.
    // Containing-only semantics: a pixel-perfect source still gets a >=16-unit canvas.
    assert.deepEqual(fromContext(fitContaining(16, 12, '4:3')), { width: 64, height: 48, offsetX: 24, offsetY: 18, preset: '4:3' });
    // Tiny source: rawUnits floors at 1, then unit-alignment lifts it to 16.
    assert.deepEqual(fromContext(fitContaining(1, 1, '16:9')), { width: 256, height: 144, offsetX: 127, offsetY: 71, preset: '16:9' });
});

test('fitContaining: extreme aspects and large sources pick the covering unit count', () => {
    const { fitContaining } = freshApi();
    // 1x1 @ 21:9 (reduces to 7:3) -> rawUnits = ceil(1/3) = 1 -> 16 units -> 112x48
    assert.deepEqual(fromContext(fitContaining(1, 1, '21:9')), { width: 112, height: 48, offsetX: 55, offsetY: 23, preset: '21:9' });
    // 1000x1000 @ 9:16 -> rawUnits = ceil(max(111.11, 62.5)) = 112 -> 1008x1792
    assert.deepEqual(fromContext(fitContaining(1000, 1000, '9:16')), { width: 1008, height: 1792, offsetX: 4, offsetY: 396, preset: '9:16' });
    // 1000x1000 @ 21:9 (reduces to 7:3): height axis dominates, rawUnits = ceil(1000/3) = 334
    // -> units = 336 -> 2352x1008
    assert.deepEqual(fromContext(fitContaining(1000, 1000, '21:9')), { width: 2352, height: 1008, offsetX: 676, offsetY: 4, preset: '21:9' });
});

test('fitContaining: fractional source sizes round first, offsets floor toward top-left', () => {
    const { fitContaining } = freshApi();
    // 100.6 -> 101, 99.4 -> 99 @ 1:1 -> 112x112; floor((112-101)/2)=5, floor((112-99)/2)=6
    assert.deepEqual(fromContext(fitContaining(100.6, 99.4, '1:1')), { width: 112, height: 112, offsetX: 5, offsetY: 6, preset: '1:1' });
    assert.deepEqual(fromContext(fitContaining(101, 99, '1:1')), { width: 112, height: 112, offsetX: 5, offsetY: 6, preset: '1:1' });
});
