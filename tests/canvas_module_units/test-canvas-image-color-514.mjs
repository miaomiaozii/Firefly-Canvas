import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { createCanvasContext, fromContext, JS_DIR } from './harness.mjs';

const filename = path.join(JS_DIR, 'canvas-image-color.js');
const color = createRequire(import.meta.url)(filename);
const source = readFileSync(filename, 'utf8');
const pixel = (...bytes) => new Uint8ClampedArray(bytes);
const deferred = () => Promise.withResolvers();
const advance = () => new Promise(resolve => setImmediate(resolve));
const base = { temperature: 0, tint: 0, exposure: 0, contrast: 0, saturation: 0 };

function fixture(options = {}) {
    const { ctx, harness } = createCanvasContext();
    const create = ctx.document.createElement;
    const loads = [], pendingClose = [], renders = [];
    const initialPixel = [160, 110, 88, 255];
    const image = { naturalWidth: 1, naturalHeight: 1 };
    ctx.innerWidth = 800;
    ctx.innerHeight = 600;
    ctx.document.createElement = tag => {
        const el = create(tag);
        el.remove = () => el.parentNode?.removeChild(el);
        el.contains = element => el === element || el.children.some(child => child.contains(element));
        if (tag === 'canvas') {
            // Browser boundary: return fixture pixels and record presented buffers, not a fake color algorithm.
            el.getContext = () => ({
                drawImage() {},
                getImageData: () => ({ data: pixel(...initialPixel), width: 1, height: 1 }),
                createImageData: () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 }),
                putImageData: ({ data }) => renders.push([...data]),
            });
            el.toBlob = (callback, type) => callback(new Blob(['fixture'], { type }));
            el.setPointerCapture = () => {};
        }
        return el;
    };
    const anchor = create('button');
    const targets = [{ nodeId: 'p', source_asset_id: 'x', url: '/original', current: { exposure: 28 }, occurrence: 3 }];
    const calls = { encode: [], commit: [], close: [], format: [] };
    const escapeStack = { push(fn) { pendingClose.push(fn); return () => pendingClose.pop(); } };
    vm.runInContext(source, ctx, { filename });
    const callbacks = {
        anchor, targets,
        translate: key => `:${key}`,
        format: (key, values) => { calls.format.push([key, values.count]); return `${key}(${values.count})`; },
        async loadImage(url, target) { loads.push([url, target.nodeId]); return image; },
        async encode(target, blob) { calls.encode.push([target, blob]); return { url: `/output/${target.nodeId}` }; },
        async commit(changes) { calls.commit.push(changes); },
        onClose(reason) { calls.close.push(reason); }, escapeStack,
        ...options,
    };
    const open = extra => ctx.CanvasImageColor.open({ ...callbacks, ...extra });
    const all = function* (root = ctx.document.body) {
        yield root;
        for (const child of root.children) yield* all(child);
    };
    const find = pred => {
        const match = [...all()].find(pred);
        assert.ok(match, 'missing panel control');
        return match;
    };
    const button = key => find(el => el.tagName === 'BUTTON' && el.textContent === `:imageColor.${key}`);
    const input = (key, type = 'range') => find(el => el.dataset.colorParam === key && el.type === type);
    const dispatch = (el, type, event) => el.dispatchEvent({ type, preventDefault() {}, stopPropagation() {}, ...event });
    const click = key => dispatch(button(key), 'click');
    const params = () => Object.fromEntries(Object.keys(base).map(key => [key, Number(input(key).value)]));
    return { ctx, open, loads, calls, targets, pendingClose, renders, initialPixel, harness, find, input, button, dispatch, click, params };
}

test('exposes the same normalized pixel API as a CommonJS module and a classic script', () => {
    const { ctx } = fixture();
    const supplied = { temperature: '102', contrast: -220, tint: '-0.1', exposure: 19.6, saturation: Infinity, extra: 19 };
    assert.deepEqual(color.normalizeParams(supplied), { temperature: 100, tint: 0, exposure: 20, contrast: -100, saturation: 0 });
    assert.deepEqual(fromContext(ctx.CanvasImageColor.normalizeParams(supplied)), color.normalizeParams(supplied));
});

test('zero parameters are byte-exact, clipping is bounded and alpha/fully transparent RGB survive', () => {
    const original = pixel(0, 8, 255, 127, 40, 50, 60, 0, 240, 240, 240, 255);
    const unchanged = original.slice();
    assert.equal(color.apply(unchanged, {}), unchanged);
    assert.deepEqual(unchanged, original);
    const adjusted = color.apply(original.slice(), { exposure: 100 });
    assert.deepEqual([...adjusted], [0, 25, 255, 127, 40, 50, 60, 0, 255, 255, 255, 255]);
    assert.deepEqual([...color.apply(pixel(128, 128, 128, 255), { temperature: 80 })], [165, 128, 99, 255]);
    const magenta = color.apply(pixel(128, 128, 128, 255), { tint: 80 });
    assert.equal(magenta[0], magenta[2]);
    assert.ok(magenta[0] > magenta[1]);
});

test('saturation −100 uses relative luminance, and contrast pushes light and dark apart', () => {
    const primary = pixel(255, 0, 0, 255, 0, 255, 0, 112);
    assert.deepEqual([...color.apply(primary, { saturation: -100 })], [127, 127, 127, 255, 220, 220, 220, 112]);
    assert.deepEqual([...color.apply(pixel(255, 0, 0, 137), { saturation: 100 })], [255, 0, 0, 137]);
    const grey = color.apply(pixel(70, 70, 70, 255, 180, 180, 180, 255), { contrast: 50 });
    assert.ok(grey[0] < 70 && grey[4] > 180);
});

test('gray world ignores transparent, near-black and clipped samples and limits correction', () => {
    const neutral = color.analyzeGrayWorld(pixel(130, 130, 130, 255, 10, 20, 200, 0, 2, 50, 60, 255, 70, 80, 254, 255));
    assert.deepEqual(neutral, { temperature: 0, tint: 0 });
    assert.equal(color.analyzeGrayWorld(pixel(0, 0, 0, 255, 120, 90, 80, 0)), null);
    const cast = pixel(160, 110, 88, 255);
    const result = color.analyzeGrayWorld(cast);
    assert.ok(result.temperature < 0 && Math.abs(result.temperature) < 40);
    const corrected = color.apply(cast.slice(), result);
    assert.ok(Math.max(...corrected.slice(0, 3)) - Math.min(...corrected.slice(0, 3)) < 72);
});

test('neutral sampler selects a centered 5×5, clips at edges and solves the actual temperature/tint model', () => {
    const pixels = new Uint8ClampedArray(9 * 9 * 4);
    for (let y = 0; y < 9; y++) for (let x = 0; x < 9; x++) {
        pixels.set(x >= 2 && x <= 6 && y >= 2 && y <= 6 ? [150, 120, 105, 255] : [15, 55, 220, 255], (y * 9 + x) * 4);
    }
    const patch = pixel(150, 120, 105, 255);
    const atCenter = color.sampleNeutral(pixels, 9, 9, 4, 4);
    assert.deepEqual(atCenter, color.sampleNeutral(patch, 1, 1, 0, 0));
    const corrected = [...color.apply(patch, atCenter)].slice(0, 3);
    assert.ok(Math.max(...corrected) - Math.min(...corrected) <= 1);
    assert.notDeepEqual(color.sampleNeutral(pixels, 9, 9, 0, 0), atCenter);
    assert.equal(color.sampleNeutral(pixel(180, 93, 95, 0), 1, 1, 0, 0), null);
});

test('reopen restores captured values; arrows, numeric entry, double-click and reset affect the right controls', async () => {
    const f = fixture();
    const session = f.open();
    f.targets[0].current.exposure = 99;
    assert.equal(await session.ready, true);
    assert.deepEqual(f.loads, [['/original', 'p']]);
    assert.deepEqual(f.params(), { ...base, exposure: 28 });
    const exposure = f.input('exposure');
    f.dispatch(exposure, 'keydown', { key: 'ArrowRight', shiftKey: false });
    f.dispatch(exposure, 'keydown', { key: 'ArrowDown', shiftKey: true });
    assert.equal(f.input('exposure', 'number').value, '19');
    const tint = f.input('tint', 'number');
    tint.value = '-135';
    f.dispatch(tint, 'input');
    f.dispatch(tint, 'change');
    assert.equal(tint.value, '-100');
    f.dispatch(exposure, 'dblclick');
    assert.deepEqual(f.params(), { ...base, tint: -100 });
    f.click('reset');
    assert.deepEqual(f.params(), base);
    f.pendingClose.at(-1)();
    assert.deepEqual(f.calls.close, ['cancel']);
    assert.equal(f.pendingClose.length, 0);
    await f.open({ targets: [{ ...f.targets[0], current: { tint: -27, saturation: 39 } }] }).ready;
    assert.deepEqual(f.params(), { ...base, tint: -27, saturation: 39 });
});

test('auto writes white-balance controls only; strength scales the suggestion and manual edit detaches it', async () => {
    const f = fixture();
    await f.open().ready;
    assert.equal(f.input('strength').disabled, true);
    f.click('auto');
    const full = f.params();
    assert.notEqual(full.temperature, 0);
    assert.equal(full.exposure, 28);
    const strength = f.input('strength');
    strength.value = '50';
    f.dispatch(strength, 'input');
    assert.equal(f.params().temperature, Math.round(full.temperature / 2));
    const temperature = f.input('temperature');
    f.dispatch(temperature, 'keydown', { key: 'ArrowUp', shiftKey: true });
    assert.equal(f.input('strength').disabled, true);
    assert.equal(f.params().temperature, Math.round(full.temperature / 2) + 10);
});

test('preview renders adjusted pixels from the original, holds comparison and resets exactly', async () => {
    const f = fixture();
    await f.open().ready;
    const render = () => { f.harness.flushRafs(); return f.renders.at(-1); };
    const key = (type, event) => {
        for (const listener of f.harness.windowListeners.get(type)) listener({
            key: '\\', preventDefault() {}, stopPropagation() {}, ...event,
        });
    };
    assert.deepEqual(render(), [...color.apply(pixel(...f.initialPixel), { exposure: 28 })]);
    key('keydown');
    assert.deepEqual(render(), f.initialPixel);
    key('keyup');
    assert.deepEqual(render(), [...color.apply(pixel(...f.initialPixel), { exposure: 28 })]);
    f.dispatch(f.input('exposure'), 'keydown', { key: 'ArrowUp' });
    assert.deepEqual(render(), [...color.apply(pixel(...f.initialPixel), { exposure: 29 })]);
    f.click('reset');
    assert.deepEqual(render(), f.initialPixel);
});

test('batch applies exactly the same captured settings and commits once, only after every file', async () => {
    const f = fixture();
    const last = deferred();
    const second = { nodeId: 'q', source_asset_id: 'y', url: '/another', current: { tint: 42 } };
    const encode = (target, blob) => {
        f.calls.encode.push([target, blob]);
        return target.nodeId === 'q' ? last.promise : { url: '/out-primary' };
    };
    await f.open({ targets: [...f.targets, second], encode }).ready;
    assert.deepEqual(f.calls.format, [['imageColor.scopeSelection', 2]]);
    const scope = f.find(el => el.tagName === 'SELECT');
    assert.equal(scope.value, 'primary');
    scope.value = 'selection';
    second.source_asset_id = 'not-captured';
    f.click('apply');
    await advance();
    f.click('apply');
    assert.deepEqual(f.loads, [['/original', 'p'], ['/another', 'q']]);
    assert.equal(f.calls.commit.length, 0);
    assert.equal(f.button('apply').disabled, true);
    assert.equal(f.calls.encode.length, 2);
    for (const [, blob] of f.calls.encode) assert.equal(blob.type, 'image/png');
    last.resolve({ url: '/out-second' });
    await advance();
    assert.equal(f.calls.commit.length, 1);
    const changes = fromContext(f.calls.commit[0]);
    assert.deepEqual(changes.map(({ file }) => file.url), ['/out-primary', '/out-second']);
    assert.equal(changes[0].target.occurrence, 3);
    assert.deepEqual(changes.map(({ metadata }) => metadata), ['x', 'y'].map(source_asset_id => ({
        source_asset_id, op: 'color', params: { ...base, exposure: 28 }, algo_version: color.ALGO_VERSION,
    })));
    assert.deepEqual(f.calls.close, ['applied']);
});

test('primary-only apply after reset encodes the original, and failed encodes do not close or commit', async () => {
    const f = fixture();
    let fail = true;
    const encode = (target, blob) => {
        f.calls.encode.push([target, blob]);
        return fail ? null : { url: '/out' };
    };
    await f.open({ targets: [...f.targets, { nodeId: 'q', source_asset_id: 'y', url: '/another' }], encode }).ready;
    f.click('reset');
    f.click('apply');
    await advance();
    assert.equal(f.calls.commit.length, 0);
    assert.deepEqual(f.calls.close, []);
    assert.equal(f.button('apply').disabled, false);
    assert.equal(f.find(el => el.getAttribute('role') === 'alert').textContent, ':imageColor.applyError');
    fail = false;
    f.click('apply');
    await advance();
    assert.deepEqual(f.loads, [['/original', 'p']]);
    assert.deepEqual(fromContext(f.calls.commit[0][0].metadata.params), base);
});

test('a second batch encode failure leaves the whole selection unapplied; a commit rejection permits retry', async () => {
    const f = fixture();
    let rejectsEncode = true, rejectsCommit = true;
    await f.open({
        targets: [...f.targets, { nodeId: 'q', source_asset_id: 'y', url: '/second' }],
        encode(target) {
            f.calls.encode.push(target.nodeId);
            if (target.nodeId === 'q' && rejectsEncode) return Promise.reject(new Error('encode'));
            return { url: `/output/${target.nodeId}` };
        },
        commit(changes) {
            f.calls.commit.push(changes);
            if (rejectsCommit) return Promise.reject(new Error('stale target'));
        },
    }).ready;
    f.find(el => el.tagName === 'SELECT').value = 'selection';
    f.click('apply');
    await advance();
    assert.deepEqual(f.calls.encode, ['p', 'q']);
    assert.deepEqual(f.calls.commit, []);
    rejectsEncode = false;
    f.click('apply');
    await advance();
    assert.equal(f.calls.commit.length, 1);
    assert.equal(f.calls.commit[0].length, 2);
    assert.deepEqual(f.calls.close, []);
    assert.equal(f.find(el => el.getAttribute('role') === 'alert').textContent, ':imageColor.applyError');
    rejectsCommit = false;
    f.click('apply');
    await advance();
    assert.equal(f.calls.commit.length, 2);
    assert.deepEqual(f.calls.close, ['applied']);
});

test('cancel during encoding and a superseded load cannot commit or close the replacement session', async () => {
    const f = fixture();
    const loading = deferred();
    const encoding = deferred();
    const stale = f.open({ loadImage: () => loading.promise });
    const second = f.open({ encode: () => encoding.promise });
    assert.equal(await stale.ready, false);
    loading.resolve({ naturalWidth: 1, naturalHeight: 1 });
    assert.equal(await second.ready, true);
    f.click('apply');
    await advance();
    f.click('cancel');
    await f.open().ready;
    encoding.resolve({ url: '/no-commit' });
    await advance();
    assert.deepEqual(f.calls.close, ['replaced', 'cancel']);
    assert.equal(f.calls.commit.length, 0);
});

test('load failure exposes a translated error and leaves apply disabled', async () => {
    const f = fixture();
    assert.equal(await f.open({ loadImage: () => { throw new Error('decode'); } }).ready, false);
    assert.equal(f.button('apply').disabled, true);
    assert.equal(f.find(el => el.getAttribute('role') === 'alert').textContent, ':imageColor.loadError');
});
