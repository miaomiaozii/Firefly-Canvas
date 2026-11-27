import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { createCanvasContext, JS_DIR, ROOT } from './harness.mjs';

const libraryUrl = '/api/project-library';
const html = readFileSync(join(ROOT, 'static/project-list.html'), 'utf8');
const script = readFileSync(join(JS_DIR, 'project-list.js'), 'utf8');
const project = (id, cover_thumbnail_url) => ({ id, name: id, cover_thumbnail_url });

async function renderCards(live, trashed) {
    const { ctx, harness } = createCanvasContext();
    for (const [, id] of html.matchAll(/\bid="([^"]+)"/g)) harness.element(id);
    ctx.document.activeElement = { closest: () => null };
    const trashGrid = harness.element('trashGrid');
    trashGrid.replaceChildren = (...cards) => { trashGrid.children = cards; };

    // The fake DOM only resolves the selectors emitted by the real card templates.
    ctx.document.createElement = tag => {
        const el = harness.makeElement(tag);
        el.remove = () => el.parentNode?.removeChild(el);
        if (tag !== 'article') return el;
        const matches = new Map();
        el.insertAdjacentHTML = (_, html) => { el.innerHTML += html; };
        el.querySelector = selector => {
            if (matches.has(selector)) return matches.get(selector);
            const match = selector.startsWith('.')
                ? el.innerHTML.match(new RegExp(`<([a-z0-9]+)\\b[^>]*class="[^"]*\\b${selector.slice(1)}\\b[^"]*"[^>]*>`))
                : el.innerHTML.match(new RegExp(`<(${selector})\\b[^>]*>`));
            if (!match) return null;
            const child = harness.makeElement(match[1]);
            for (const [, key, value] of match[0].matchAll(/([\w-]+)="([^"]*)"/g)) child.setAttribute(key, value);
            matches.set(selector, child);
            return child;
        };
        return el;
    };
    ctx.fetch = async url => ({ ok: true, json: async () => ({ projects: url === libraryUrl ? live : trashed }) });
    vm.runInContext(script, ctx, { filename: 'static/js/project-list.js' });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.element('status').textContent, '');
    return [harness.element('projectGrid').children, trashGrid.children];
}

function thumbnail(card) {
    assert.match(card.innerHTML, /<svg class="project-thumb-mark"/);
    const thumb = card.querySelector('.project-thumb');
    assert.equal(thumb.getAttribute('aria-hidden'), 'true');
    return thumb;
}

test('both card views load decorative cover images and fall back on image error', async () => {
    const live = `${libraryUrl}/live/cover/asset_1`;
    const trashed = `${libraryUrl}/trash/gone/cover/legacy.image-2`;
    const [[liveCard], [trashCard]] = await renderCards([project('live', live)], [project('gone', trashed)]);
    for (const [card, url] of [[liveCard, live], [trashCard, trashed]]) {
        const thumb = thumbnail(card);
        const [cover] = thumb.children;
        assert.equal(cover.tagName, 'IMG');
        assert.equal(cover.src, url);
        assert.equal(cover.alt, '');
        assert.equal(cover.loading, 'lazy');
        assert.equal(cover.decoding, 'async');
        assert.equal(cover.classList.contains('is-loaded'), false);
        cover.dispatchEvent(new Event('load'));
        assert.equal(cover.classList.contains('is-loaded'), true);
        cover.dispatchEvent(new Event('error'));
        assert.equal(thumbnail(card).children.length, 0);
    }
});

test('missing and unexpected cover URLs leave the decorative placeholder', async () => {
    const prefix = `${libraryUrl}/demo/cover/`;
    const invalid = [
        undefined, '', 42, `https://example.test${prefix}asset`, `//example.test${prefix}asset`,
        `${libraryUrl}/other/cover/asset`, `${libraryUrl}/trash/demo/cover/asset`,
        `${prefix}..`, `${prefix}%2e%2e`, `${prefix}asset/extra`, `${prefix}asset?raw=1`,
    ];
    const [liveCards, [trashCard]] = await renderCards(invalid.map(url => project('demo', url)), [project('demo', `${prefix}asset`)]);
    assert.equal(liveCards.length, invalid.length);
    for (const card of [...liveCards, trashCard]) assert.equal(thumbnail(card).children.length, 0);
});
