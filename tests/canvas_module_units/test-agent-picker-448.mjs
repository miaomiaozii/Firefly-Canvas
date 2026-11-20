import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createCanvasContext, loadModules, evaluate, globalFunction } from './harness.mjs';

const flush = () => new Promise(resolve => setImmediate(resolve));
const reply = data => ({ ok: true, json: async () => data });
const cap = (model = 'native/a', efforts = ['low', 'high']) => ({ host: 'pi', status: 'available',
    models: [{ id: 'native/a', name: 'A' }, { id: 'native/b', name: 'B' }], defaults: { model: '', effort: null },
    selection: { model, efforts, source: 'test' } });
function setup() {
    const { ctx, harness } = createCanvasContext();
    evaluate(ctx, readFileSync(new URL('../../static/js/canvas-agent-navigation.js', import.meta.url), 'utf8'));
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js', 'canvas-agent-tui.js']);
    ctx.canvasId = 'project';
    const node = { id: 'a', type: 'smart-agent', agentCli: 'pi', model: 'native/a',
        agentHostSettings: { pi: { model: 'native/a', effort: 'high' } } };
    ctx.canvasState.nodes = [node];
    ctx.renderAgentNode = () => {};
    ctx.receiveAgentSettingsCanvas = canvas => { ctx.canvasState.nodes = canvas.nodes; return true; };
    const calls = [];
    ctx.fetch = (url, opts = {}) => new Promise((resolve, reject) => calls.push({ url, opts, resolve, reject }));
    const originalCreate = ctx.document.createElement;
    ctx.document.createElement = tag => {
        const el = originalCreate(tag);
        if (tag !== 'div') return el;
        let html = '', buttons = [];
        Object.defineProperty(el, 'innerHTML', { get: () => html, set: value => {
            html = value; buttons = [];
            for (const match of value.matchAll(/<button\b([^>]*)>/g)) {
                const button = harness.makeElement('button');
                for (const attr of match[1].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) button.setAttribute(attr[1], attr[2] ?? '');
                button.focus = () => { ctx.document.activeElement = button; };
                buttons.push(button);
            }
        }});
        el.querySelectorAll = selector => selector === 'button' ? buttons : buttons.filter(b => b.hasAttribute('data-agent-picker-option'));
        el.querySelector = selector => selector.includes('retry') ? buttons.find(b => b.hasAttribute('data-agent-picker-retry')) || null : buttons.find(b => b.getAttribute('aria-checked') === 'true') || null;
        el.focus = () => { ctx.document.activeElement = el; };
        el.contains = target => target === el || buttons.includes(target);
        return el;
    };
    const anchor = harness.makeElement('button');
    return { ctx, harness, node, calls, anchor, fn: name => globalFunction(ctx, name), state: globalFunction(ctx, 'getAgentTuiState')('a') };
}

test('picker reads on open, applies model with a supported fallback effort and closes', async () => {
    const h = setup();
    const opening = h.fn('openAgentModelPicker')('a', 'project', 'model', h.anchor);
    assert.match(h.state.modelPicker.menu.innerHTML, /正在读取/);
    h.calls[0].resolve(reply(cap())); await opening;
    const picker = h.state.modelPicker;
    assert.match(picker.menu.innerHTML, /A/);
    const applying = h.fn('applyAgentModelPicker')('a', 'project', picker, 'native/b');
    assert.equal(h.state.modelPicker, null);
    h.calls[1].resolve(reply(cap('native/b', ['low']))); await flush();
    assert.deepEqual(JSON.parse(h.calls[2].opts.body), { host: 'pi', model: 'native/b', effort: 'low', user_edit: true });
    const updated = { ...h.node, model: 'native/b', agentHostSettings: { pi: { model: 'native/b', effort: 'low' } } };
    h.calls[2].resolve(reply({ canvas: { nodes: [updated] } })); await applying;
    assert.equal(h.state.effortNotice, '已改为 low');
});

for (const [name, current, supported, expected] of [
    ['falls back downward even when the higher level is closer', 'high', ['low', 'xhigh'], 'low'],
    ['uses the lowest supported level when all levels are higher', 'medium', ['xhigh', 'high'], 'high'],
]) {
    test(`model switch ${name}`, async () => {
        const h = setup(); h.node.agentHostSettings.pi.effort = current;
        const opening = h.fn('openAgentModelPicker')('a', 'project', 'model', h.anchor);
        h.calls[0].resolve(reply(cap())); await opening;
        const applying = h.fn('applyAgentModelPicker')('a', 'project', h.state.modelPicker, 'native/b');
        h.calls[1].resolve(reply(cap('native/b', supported))); await flush();
        assert.deepEqual(JSON.parse(h.calls[2].opts.body), { host: 'pi', model: 'native/b', effort: expected, user_edit: true });
        const updated = { ...h.node, model: 'native/b', agentHostSettings: { pi: { model: 'native/b', effort: expected } } };
        h.calls[2].resolve(reply({ canvas: { nodes: [updated] } })); await applying;
        assert.equal(h.state.effortNotice, `已改为 ${expected}`);
    });
}

test('model switch preserves null effort without looking up or inventing a level', async () => {
    const h = setup(); h.node.agentHostSettings.pi.effort = null;
    const opening = h.fn('openAgentModelPicker')('a', 'project', 'model', h.anchor);
    h.calls[0].resolve(reply(cap())); await opening;
    const applying = h.fn('applyAgentModelPicker')('a', 'project', h.state.modelPicker, 'native/b');
    assert.deepEqual(JSON.parse(h.calls[1].opts.body), { host: 'pi', model: 'native/b', effort: null, user_edit: true });
    h.calls[1].resolve(reply({ canvas: { nodes: [{ ...h.node, model: 'native/b', agentHostSettings: { pi: { model: 'native/b', effort: null } } }] } }));
    await applying; assert.equal(h.calls.length, 2); assert.equal(h.state.effortNotice, undefined);
});

test('model switch retains a supported explicit effort', async () => {
    const h = setup();
    const opening = h.fn('openAgentModelPicker')('a', 'project', 'model', h.anchor);
    h.calls[0].resolve(reply(cap())); await opening;
    const applying = h.fn('applyAgentModelPicker')('a', 'project', h.state.modelPicker, 'native/b');
    h.calls[1].resolve(reply(cap('native/b', ['low', 'high']))); await flush();
    assert.deepEqual(JSON.parse(h.calls[2].opts.body), { host: 'pi', model: 'native/b', effort: 'high', user_edit: true });
    h.calls[2].resolve(reply({ canvas: { nodes: [{ ...h.node, model: 'native/b', agentHostSettings: { pi: { model: 'native/b', effort: 'high' } } }] } }));
    await applying; assert.equal(h.state.effortNotice, undefined);
});

test('empty supported effort is never used as a model-switch fallback', async () => {
    const h = setup();
    const opening = h.fn('openAgentModelPicker')('a', 'project', 'model', h.anchor);
    h.calls[0].resolve(reply(cap())); await opening;
    const applying = h.fn('applyAgentModelPicker')('a', 'project', h.state.modelPicker, 'native/b');
    h.calls[1].resolve(reply(cap('native/b', ['']))); await applying;
    assert.equal(h.calls.length, 2); assert.match(h.state.modelPicker.menu.innerHTML, /没有可用的思考档位/);
    h.fn('closeAgentModelPicker')(h.state);
});

test('failure offers retry and escape dismisses while loading', async () => {
    const h = setup();
    const opening = h.fn('openAgentModelPicker')('a', 'project', 'model', h.anchor);
    h.calls[0].reject(new Error('offline')); await opening;
    assert.match(h.state.modelPicker.menu.innerHTML, /offline/);
    h.state.modelPicker.menu.querySelector('[data-agent-picker-retry]').click();
    assert.equal(h.calls.length, 2);
    const picker = h.state.modelPicker;
    picker.keydown({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
    assert.equal(h.state.modelPicker, null);
    h.calls[1].resolve(reply(cap())); await flush();
    assert.equal(h.state.modelPicker, null);
});

test('arrow keys move focus and saved unlisted model is plain', async () => {
    const h = setup(); h.node.agentHostSettings.pi.model = 'native/saved';
    const opening = h.fn('openAgentModelPicker')('a', 'project', 'model', h.anchor);
    h.calls[0].resolve(reply(cap('native/saved'))); await opening;
    const picker = h.state.modelPicker;
    assert.match(picker.menu.innerHTML, /native\/saved/); assert.doesNotMatch(picker.menu.innerHTML, /未验证/);
    picker.keydown({ key: 'ArrowUp', preventDefault() {}, stopPropagation() {} });
    assert.equal(h.ctx.document.activeElement.getAttribute('data-agent-picker-option'), '2');
    picker.keydown({ key: 'ArrowDown', preventDefault() {}, stopPropagation() {} });
    assert.equal(h.ctx.document.activeElement.getAttribute('data-agent-picker-option'), '3');
    assert.equal(h.fn('agentFloorEffort')('medium', ['low', 'high']), 'low');
    picker.outside({ target: {} });
    assert.equal(h.state.modelPicker, null);
});

test('effort option applies directly; target capability failure returns to retryable menu', async () => {
    const h = setup();
    let opening = h.fn('openAgentModelPicker')('a', 'project', 'effort', h.anchor);
    h.calls[0].resolve(reply(cap())); await opening;
    let picker = h.state.modelPicker;
    picker.keydown({ key: 'ArrowUp', preventDefault() {}, stopPropagation() {} });
    assert.equal(h.ctx.document.activeElement.getAttribute('data-agent-picker-option'), '1');
    picker.keydown({ key: 'Enter', preventDefault() {}, stopPropagation() {} });
    assert.equal(h.state.modelPicker, null);
    assert.deepEqual(JSON.parse(h.calls[1].opts.body), { host: 'pi', model: 'native/a', effort: 'low', user_edit: true });
    const updated = { ...h.node, agentHostSettings: { pi: { model: 'native/a', effort: 'low' } } };
    h.calls[1].resolve(reply({ canvas: { nodes: [updated] } })); await flush();
    opening = h.fn('openAgentModelPicker')('a', 'project', 'model', h.anchor);
    h.calls[2].resolve(reply(cap())); await opening;
    picker = h.state.modelPicker;
    const applying = h.fn('applyAgentModelPicker')('a', 'project', picker, 'native/b');
    h.calls[3].reject(new Error('offline')); await applying;
    assert.equal(h.state.modelPicker, picker);
    assert.match(picker.menu.innerHTML, /offline/);
    picker.menu.querySelector('[data-agent-picker-retry]').click();
    h.calls[4].resolve(reply(cap())); await flush();
    assert.match(picker.menu.innerHTML, /B/);
    h.fn('closeAgentModelPicker')(h.state);
});
