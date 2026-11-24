import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/js/global-settings.js', import.meta.url), 'utf8');
const translations = readFileSync(new URL('../static/js/i18n/studio.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../static/global-settings.html', import.meta.url), 'utf8');
const bypass = '[data-field="image_bypass_system_proxy"]';

// VM/DOM 接缝守住设置序列化边界，不冒充浏览器布局或原生输入验收。
class Element {
    constructor(tag = 'div') {
        this.tagName = tag;
        this.children = [];
        this.dataset = {};
        this.listeners = {};
        this.value = '';
        this.className = '';
        this.checked = this.disabled = this.hidden = false;
        this._text = '';
        this.classList = {toggle() {}};
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set innerHTML(value) {
        this.children = [];
        this._text = '';
        const stack = [this];
        for (const token of value.match(/<[^>]+>|[^<]+/g) || []) {
            if (token.startsWith('</')) { stack.pop(); continue; }
            if (!token.startsWith('<')) { stack.at(-1)._text += token; continue; }
            const tag = token.match(/^<([\w-]+)/)?.[1];
            if (!tag) continue;
            const node = new Element(tag);
            for (const match of token.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
                const [, name, raw = ''] = match;
                if (name === tag) continue;
                if (name.startsWith('data-')) node.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = raw;
                else if (name === 'class') node.className = raw;
                else node[name] = ['hidden', 'checked', 'disabled'].includes(name) ? true : raw;
            }
            stack.at(-1).appendChild(node);
            if (!['input', 'br', 'hr', 'img'].includes(tag)) stack.push(node);
        }
    }
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
    async fire(name) { for (const fn of this.listeners[name] || []) await fn({target: this, preventDefault() {}}); }
    remove() { this.parentNode.children = this.parentNode.children.filter(child => child !== this); }
    matches(selector) {
        if (selector.startsWith('.')) return this.className.split(' ').includes(selector.slice(1));
        const attribute = selector.match(/^\[data-([\w-]+)(?:="([^"]*)")?\](:checked)?$/);
        if (attribute) {
            const name = attribute[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase());
            return name in this.dataset && (attribute[2] === undefined || this.dataset[name] === attribute[2]) && (!attribute[3] || this.checked);
        }
        return this.tagName === selector;
    }
    querySelectorAll(selector) {
        return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

async function boot(language = 'zh') {
    const ids = ['pageStatus', 'providerList', 'cliGrid', 'libraryPath', 'moveExisting', 'themeSelect',
        'librarySummary', 'photoshopStatus', 'backButton', 'refreshAll', 'addProvider', 'saveProviders',
        'savePreferences', 'libraryForm'];
    const elements = Object.fromEntries(ids.map(id => {
        assert.ok(html.includes(`id="${id}"`), `real page includes ${id}`);
        return [id, new Element()];
    }));
    const calls = [];
    let providers = [
        {id: 'direct', protocol: 'openai', image_bypass_system_proxy: true},
        {id: 'old', protocol: 'openai'},
        {id: 'off', protocol: 'gemini', image_bypass_system_proxy: false},
        {id: 'bad-string', protocol: 'openai', image_bypass_system_proxy: 'false'},
        {id: 'cli', protocol: 'codex'},
        {id: 'cli-gemini', protocol: 'gemini-cli'},
    ].map(item => ({name: item.id, base_url: 'https://private-fake.invalid/v1', image_models: ['image-one'],
        chat_models: ['chat-one'], video_models: ['video-one'], key_preview: '****6789', has_key: true, ...item}));
    const dictionary = {};
    const context = vm.createContext({
        document: {getElementById: id => elements[id] || null, createElement: tag => new Element(tag)},
        StudioI18n: {register: value => Object.assign(dictionary, value), t: key => dictionary[key]?.[language] || key, lang: () => language},
        location: {origin: 'http://private-qa.invalid'},
        addEventListener() {},
        BroadcastChannel: class { postMessage() {} },
        async fetch(url, options = {}) {
            const body = options.body ? JSON.parse(options.body) : null;
            calls.push({url, method: options.method || 'GET', body});
            let data;
            if (url === '/api/global-settings') data = {providers: structuredClone(providers), cli: {}, preferences: {}, project_library: {}};
            else if (url === '/api/config/data-diagnostics') data = {items: []};
            else if (url === '/api/providers' && options.method === 'PUT') {
                providers = structuredClone(body);
                data = {providers: structuredClone(providers)};
            } else if (['/api/providers/test-connection', '/api/providers/fetch-models'].includes(url)) data = {ok: true, image_models: ['image-one']};
            else throw new Error(`Unexpected fake-backend endpoint: ${url}`);
            return {ok: true, json: async () => data};
        },
    });
    context.window = context;
    context.self = context.top = context;
    vm.runInContext(translations, context);
    vm.runInContext(source, context);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(elements.pageStatus.dataset.kind, 'success', elements.pageStatus.textContent);
    return {elements, calls, card: id => elements.providerList.children.find(card => card.dataset.providerId === id)};
}

for (const language of ['zh', 'en']) {
    test(`${language}: deferred switch has no control or translation, even for stale true/false values`, async () => {
        const {card} = await boot(language);
        assert.ok(!translations.includes('globalSettings.imageBypassSystemProxy'));
        for (const id of ['direct', 'old', 'off', 'bad-string', 'cli', 'cli-gemini']) {
            assert.equal(card(id).querySelector(bypass), null);
            assert.equal(card(id).querySelector('.provider-image-network'), null);
        }
    });
}

test('stale experimental booleans cannot reach save/test/fetch serialization or reappear after reload', async () => {
    const envBefore = {...process.env};
    const {card, elements, calls} = await boot();
    card('old').querySelector('[data-field="name"]').value = 'Renamed';
    await card('old').querySelector('[data-provider-action="test"]').fire('click');
    await card('direct').querySelector('[data-provider-action="fetch"]').fire('click');
    await new Promise(resolve => setImmediate(resolve));
    const probe = calls.find(call => call.url === '/api/providers/test-connection');
    assert.ok(!Object.hasOwn(probe.body, 'image_bypass_system_proxy'));
    const fetch = calls.find(call => call.url === '/api/providers/fetch-models');
    assert.ok(!Object.hasOwn(fetch.body, 'image_bypass_system_proxy'));
    card('old').querySelector('[data-models="image_models"]').value = 'manual-image';
    await elements.saveProviders.fire('click');
    const saved = calls.find(call => call.method === 'PUT').body;
    assert.ok(saved.every(item => !Object.hasOwn(item, 'image_bypass_system_proxy')));
    assert.equal(saved.find(item => item.id === 'old').name, 'Renamed');
    assert.deepEqual(saved.find(item => item.id === 'old').image_models, ['manual-image']);
    assert.ok(saved.every(item => !('api_key' in item) && !('key_preview' in item) && !('has_key' in item)));
    await elements.refreshAll.fire('click');
    assert.equal(card('old').querySelector('[data-field="name"]').value, 'Renamed');
    assert.equal(card('direct').querySelector(bypass), null);
    assert.deepEqual({...process.env}, envBefore);
});

test('protocol changes and new providers never acquire the deferred switch', async () => {
    const {card, elements, calls} = await boot();
    const protocol = card('direct').querySelector('[data-field="protocol"]');
    protocol.value = 'codex';
    await protocol.fire('change');
    assert.equal(card('direct').querySelector(bypass), null);
    await elements.saveProviders.fire('click');
    assert.ok(!Object.hasOwn(calls.find(call => call.method === 'PUT').body[0], 'image_bypass_system_proxy'));
    const restored = card('direct').querySelector('[data-field="protocol"]');
    restored.value = 'openai';
    await restored.fire('change');
    assert.equal(card('direct').querySelector(bypass), null);
    await elements.addProvider.fire('click');
    assert.equal(elements.providerList.children.at(-1).querySelector(bypass), null);
    await elements.saveProviders.fire('click');
    assert.ok(calls.filter(call => call.method === 'PUT').every(call => call.body.every(item => !Object.hasOwn(item, 'image_bypass_system_proxy'))));
});
