import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createCanvasContext, loadModules} from './harness.mjs';

// These are VM/markup/CSS contracts, not browser layout or native acceptance.
const read = file => readFileSync(new URL(`../../${file}`, import.meta.url), 'utf8');
const css = read('static/css/canvas-result-retrieval.css');
const shared = read('static/css/smart-canvas.css');
const foundation = read('static/css/studio-foundation.css');
const fixture = JSON.parse(readFileSync(new URL('./fixtures/retrieval-canonical-case10-493.json', import.meta.url), 'utf8'));
const footer = '.retrieval-node > .retrieval-summary';
function declarations(source, selector) {
    const blocks = [...source.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)];
    const matching = blocks.filter(block => block[1].trim().split(/\s*,\s*/).includes(selector));
    assert.equal(matching.length, 1, `one unambiguous rule for ${selector}`);
    return Object.fromEntries(matching[0][2].split(';').filter(text => text.trim()).map(text => {
        const colon = text.indexOf(':');
        return [text.slice(0, colon).trim(), text.slice(colon + 1).trim()];
    }));
}
const rule = selector => declarations(css, selector);
const px = value => { assert.match(value, /^\d+px$/); return Number.parseInt(value); };
function tree(html) {
    const root = {tag: 'root', attrs: {}, children: []}, stack = [root];
    for (const token of html.match(/<[^>]+>|[^<]+/g) || []) {
        if (token.startsWith('</')) {
            assert.equal(stack.pop().tag, token.slice(2, -1));
        } else if (token.startsWith('<')) {
            const tag = token.match(/^<([\w-]+)/)[1];
            const attrs = Object.fromEntries([...token.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].slice(1).map(match => [match[1], match[2] ?? '']));
            const node = {tag, attrs, children: []};
            stack.at(-1).children.push(node);
            if (!token.endsWith('/>') && !['img', 'br', 'input'].includes(tag)) stack.push(node);
        } else stack.at(-1).children.push(token);
    }
    assert.equal(stack.length, 1);
    return root;
}
const all = (node, predicate) => typeof node === 'string' ? [] : [...(predicate(node) ? [node] : []), ...node.children.flatMap(child => all(child, predicate))];
const classIs = name => node => (node.attrs.class || '').split(/\s+/).includes(name);
const text = node => typeof node === 'string' ? node : node.children.map(text).join('');
async function setup(lang = 'zh', change = () => {}) {
    const {ctx, harness} = createCanvasContext();
    loadModules(ctx, ['canvas-layout-utils.js', 'canvas-result-retrieval.js']);
    ctx.StudioI18n = {lang: () => lang};
    const group = structuredClone(fixture.history[0].download);
    change(group);
    const calls = [];
    const api = ctx.canvasResultRetrieval;
    api.configure({getProjectId: () => fixture.id, getHistory: () => fixture.history, fetch: async (url, options = {}) => {
        calls.push({url, options});
        assert.equal(options.method, undefined, 'initial fixture listing is read-only');
        return {ok: true, json: async () => ({groups: [group]})};
    }});
    harness.element('smartRetrievalToggle');
    harness.element('smartRetrievalCount');
    await api.refresh();
    const node = structuredClone(fixture.nodes[0]);
    delete node.pendingTasks;
    const grid = ctx.getBatchThumbGridSize(4);
    const layout = {cols: grid.cols, rows: grid.rows, thumb: 192, gridHeight: grid.gridH};
    const render = () => api.nodeHtml(node, layout, (image, index, style) => `<div class="thumb-item" data-output-id="${image.output_id}" style="${style}"></div>`);
    return {ctx, harness, api, group, node, layout, render, calls};
}

test('retrieval footer alone adopts existing batch caption tokens, weight and tracking', () => {
    const caption = declarations(shared, '.batch-statusbar-label'), actual = rule(footer);
    assert.equal(caption['font-size'], '11px');
    assert.equal(caption['font-weight'], '700');
    assert.match(foundation, /--font-caption:\s*11px/);
    assert.match(actual.font, /^700 var\(--font-caption, 11px\)\/var\(--line-compact, 1\.3\) var\(--font-sans,/);
    assert.equal(actual['letter-spacing'], caption['letter-spacing']);
    assert.equal(rule(`${footer} strong`).font, 'inherit');
    const fontRules = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*\bfont\s*:[^{}]*)\}/g)];
    assert.ok(fontRules.filter(block => !/font\s*:\s*inherit\s*;/.test(block[2])).every(block => block[1].trim() === footer));
    assert.doesNotMatch(css, /--[\w-]+\s*:\s*[^;}]+[;}]/, 'no shared token overrides');
});

test('32px action plus focus ring fits the existing 48px status budget without footer scrolling or clipping', () => {
    const actual = rule(footer), stack = declarations(shared, '.pending-batch-stack');
    const source = read('static/js/canvas-layout-utils.js');
    const budget = Number(source.match(/const BATCH_STATUSBAR_H = (\d+);/)[1]);
    assert.equal(budget, 48);
    assert.equal(px(actual.height) + px(stack.gap), budget);
    assert.equal(actual.padding, '4px 8px 4px 10px');
    const action = rule('.retrieval-summary button');
    assert.equal(px(actual.height) - 8, px(action['min-height']));
    assert.equal(action['min-height'], '32px');
    assert.equal(action['flex-shrink'], '0');
    assert.equal(action['white-space'], 'nowrap');
    assert.equal(actual.overflow, 'visible');
    assert.equal(actual.flex, 'none');
    assert.equal(actual['flex-wrap'], 'nowrap', 'only the caption text wraps; the action remains alongside');
    const caption = rule(`${footer} strong`);
    assert.equal(caption.flex, '1 1 0');
    assert.equal(caption['min-width'], '0');
    assert.equal(caption['white-space'], 'normal');
    assert.equal(caption['overflow-wrap'], 'anywhere');
    assert.equal(caption.overflow, undefined);
    assert.equal(caption['text-overflow'], undefined);
});

for (const lang of ['zh', 'en']) test(`case10 ${lang} retains complete summary, batch count, badge2 and exact 192x144 slots`, async () => {
    const {render, node, harness, calls} = await setup(lang), before = JSON.stringify(node), markup = tree(render());
    const summary = all(markup, classIs('retrieval-summary'))[0];
    assert.equal(summary.attrs.role, 'status');
    assert.ok(classIs('batch-statusbar')(summary));
    assert.equal(all(summary, node => node.tag === 'strong').length, 1);
    assert.match(text(summary.children[0]), lang === 'zh' ? /原图已生成 · 2 张待取回 · 2 张已保存/ : /Original generated · 2 awaiting retrieval · 2 saved/);
    const batch = all(summary, node => node.attrs['data-retrieval-all'] === '1');
    assert.equal(batch.length, 1);
    assert.equal(text(batch[0]), lang === 'zh' ? '全部取回（2）' : 'Retrieve all（2）');
    assert.equal('disabled' in batch[0].attrs, false);
    assert.equal(all(summary, node => node.tag === 'small').length, 1, 'existing safety copy remains in markup');
    const saved = all(markup, classIs('thumb-item')), missing = all(markup, classIs('retrieval-slot'));
    assert.equal(saved.length, 2); assert.equal(missing.length, 2);
    for (const slot of [...saved, ...missing]) assert.match(slot.attrs.style, /width:192px;height:144px$/);
    assert.deepEqual(saved.map(slot => slot.attrs['data-output-id']), node.images.map(image => image.output_id));
    assert.match(all(markup, classIs('thumb-grid'))[0].attrs.style, /--thumb-max-height:300px;gap:12px$/);
    assert.equal(harness.element('smartRetrievalCount').textContent, lang === 'zh' ? '2 张待取回' : '2 awaiting retrieval');
    assert.equal(JSON.stringify(node), before);
    assert.ok(calls.every(call => !call.options.method));
});

test('in-flight batch remains disabled with its count, while every mixed outcome remains present', async () => {
    const {render} = await setup('en', group => group.items.slice(2).forEach(item => Object.assign(item, {status: 'downloading', can_retrieve: false})));
    const markup = tree(render()), batch = all(markup, node => node.attrs['data-retrieval-all'] === '1')[0];
    assert.equal(text(batch), 'Retrieve all（2）');
    assert.ok('disabled' in batch.attrs);
    assert.equal(all(markup, node => node.attrs.role === 'status' && text(node) === 'Downloading').length, 2);
    const mixed = await setup('en', group => {
        Object.assign(group.items[0], {status: 'failed', can_retrieve: true});
        Object.assign(group.items[1], {status: 'unavailable', can_retrieve: false});
        Object.assign(group.items[2], {status: 'unknown', can_retrieve: false});
    });
    const summary = text(all(tree(mixed.render()), classIs('retrieval-summary'))[0]);
    assert.match(summary, /2 awaiting retrieval · 1 unavailable · 1 awaiting status/);
});

test('long failures remain escaped, wrap, and use a bounded readable notice rather than stretching the footer', async () => {
    const message = '<unsafe> ' + '很长的失败说明LongUnbrokenError'.repeat(50);
    const {api, group} = await setup('zh', group => {group.items[2].error = message;});
    const slot = api.slotHtml(group, group.items[2]);
    assert.match(slot, /<details><summary>失败说明<\/summary><span>&lt;unsafe&gt;/);
    assert.ok(slot.includes('LongUnbrokenError'.repeat(1)));
    assert.equal(rule('.retrieval-slot details span')['overflow-wrap'], 'anywhere');
    assert.equal(rule('.retrieval-slot').overflow, 'auto', 'expanded slot errors remain reachable');
    // Failure is injected at the VM service boundary; no HTTP request leaves this test.
    api.configure({fetch: async () => ({ok: false, status: 409, json: async () => ({detail: message})})});
    await api.retrieve(group.history_id, [group.items[2].id]);
    const html = api.summaryHtml(group), alert = all(tree(html), node => node.attrs.role === 'alert')[0];
    assert.ok(alert); assert.ok(text(alert).includes('&lt;unsafe&gt;'));
    assert.match(html, /很长的失败说明LongUnbrokenError/);
    const notice = rule(`${footer} [role="alert"]`);
    assert.equal(notice.position, 'absolute');
    assert.equal(notice.bottom, '48px');
    assert.equal(notice['max-height'], 'calc(100% - 48px)');
    assert.equal(notice['box-sizing'], 'border-box');
    assert.equal(notice.overflow, 'auto');
    assert.equal(notice['white-space'], 'normal');
    assert.equal(notice['scrollbar-width'], 'thin');
    assert.equal(rule('.retrieval-summary [role="alert"]')['overflow-wrap'], 'anywhere');
    assert.ok(!notice.display && !notice['text-overflow'] && !notice['-webkit-line-clamp']);
});

test('disabled/focus affordances and thumbnail geometry are not overridden by footer rules', () => {
    assert.equal(rule('.retrieval-summary button:disabled').opacity, '.6');
    assert.equal(rule('.retrieval-summary button:focus-visible').outline, '2px solid var(--designer-accent,var(--text))');
    assert.equal(rule('.retrieval-summary button:focus-visible')['outline-offset'], '2px');
    assert.equal(rule('.retrieval-summary button').font, 'inherit');
    assert.equal(rule('.retrieval-node > .thumb-grid').flex, '1 1 auto');
    assert.equal(rule('.retrieval-node > .thumb-grid')['min-height'], '0');
    assert.doesNotMatch(css, /\.retrieval-node[^{}]*(?:\.thumb-item|\bimg\b)\s*\{/);
    assert.doesNotMatch(css, /zoom:|transform:\s*scale|filter:|!important/);
    const alert = rule(`${footer} [role="alert"]`);
    assert.equal(alert.background, 'var(--panel)');
    assert.equal(alert.border, '1px solid var(--line)');
    assert.equal(rule(footer).background, undefined, 'existing batch/live colors stay authoritative');
});
