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
        this.attributes = {};
        this.value = '';
        this.className = '';
        this.checked = this.disabled = this.hidden = false;
        this._text = '';
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
                else if (name.startsWith('aria-')) node.setAttribute(name, raw);
                else node[name] = ['hidden', 'checked', 'disabled'].includes(name) ? true : raw;
            }
            stack.at(-1).appendChild(node);
            if (!['input', 'br', 'hr', 'img', 'meta', 'link'].includes(tag)) stack.push(node);
        }
    }
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    replaceChildren(...children) { for (const child of [...this.children]) child.remove(); this.append(...children); }
    get options() { return this.children; }
    get classList() {
        return {toggle: (name, on) => {
            const classes = new Set(this.className.split(/\s+/).filter(Boolean));
            if (on) classes.add(name); else classes.delete(name);
            this.className = [...classes].join(' ');
        }};
    }
    get root() { return this.parentNode?.root || this; }
    get nextElementSibling() { return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1]; }
    get previousElementSibling() { return this.parentNode?.children[this.parentNode.children.indexOf(this) - 1]; }
    getElementById(id) { return this.querySelector(`#${id}`); }
    focus() { this.root.activeElement = this; }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector); }
    contains(el) { return this === el || this.children.some(child => child.contains(el)); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
    async fire(name, event = {}) {
        if (this.disabled && name === 'click') return;
        for (const fn of this.listeners[name] || []) await fn({target: this, preventDefault() {}, ...event});
    }
    remove() {
        this.parentNode.children = this.parentNode.children.filter(child => child !== this);
        this.parentNode = null;
    }
    matches(selector) {
        if (selector.startsWith('#')) return this.id === selector.slice(1);
        if (selector.startsWith('.')) return this.className.split(' ').includes(selector.slice(1));
        const attribute = selector.match(/^\[data-([\w-]+)(?:="([^"]*)")?\](:checked)?$/);
        if (attribute) {
            const name = attribute[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase());
            return name in this.dataset && (attribute[2] === undefined || this.dataset[name] === attribute[2]) && (!attribute[3] || this.checked);
        }
        return this.tagName === selector;
    }
    querySelectorAll(selector) {
        if (selector.startsWith('[data-settings-panel] :is(')) {
            return this.querySelectorAll('[data-settings-panel]').flatMap(panel => panel.querySelectorAll('button, input, select, textarea'));
        }
        if (selector.includes(' ') && !selector.includes(',')) {
            const [ancestor, target] = selector.split(' ');
            return this.querySelectorAll(ancestor).flatMap(item => item.querySelectorAll(target));
        }
        const selectors = selector.split(',').map(part => part.trim());
        return this.children.flatMap(child => [...(selectors.some(part => child.matches(part)) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

const settle = () => new Promise(resolve => setImmediate(resolve));

async function boot(language = 'zh', receipts = {}) {
    const document = new Element('document');
    document.innerHTML = html;
    document.body = document.querySelector('body');
    const elements = new Proxy({}, {get: (_, id) => document.getElementById(id)});
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
    const messages = [];
    const dockCalls = [], clipboard = [], preferenceReads = [], timers = new Map();
    let timerId = 0, now = 0;
    const addTimer = (fn, delay, repeating = false) => {
        timers.set(++timerId, {fn, delay, due: now + delay, repeating});
        return timerId;
    };
    let preferences = {theme: 'dark', upstream_direct: true, ...receipts.preferences};
    const preferenceReceipt = (fields = {}) => {
        const value = {...preferences, ...fields};
        if (Object.hasOwn(value, 'chat_dock_enabled')) {
            const legacy = value.chat_dock_enabled;
            Object.defineProperty(value, 'chat_dock_enabled', {enumerable: true, get() {
                preferenceReads.push('chat_dock_enabled');
                return legacy;
            }});
        }
        return value;
    };
    let library = {path: 'D:/fake-original', project_count: 1};
    document.createElement = tag => new Element(tag);
    const context = vm.createContext({
        document,
        URL, URLSearchParams,
        Date: class extends Date { static now() { return now; } },
        scrollTo() {},
        setInterval(fn, delay) { return addTimer(fn, delay, true); },
        setTimeout(fn, delay) { return addTimer(fn, delay); },
        clearInterval(id) { timers.delete(id); },
        clearTimeout(id) { timers.delete(id); },
        navigator: {userAgent: receipts.userAgent || 'Chrome/130.0', clipboard: {async writeText(text) {
            if (receipts.clipboardError) throw new Error('fake clipboard unavailable');
            clipboard.push(text);
        }}},
        StudioI18n: {register: value => Object.assign(dictionary, value), t: key => dictionary[key]?.[language] || key, lang: () => language},
        location: new URL(receipts.origin || 'http://private-qa.invalid'),
        FireflyChatDockClient: {
            async hello() { dockCalls.push('hello'); return receipts.dockHello ?? null; },
            async options() { dockCalls.push('options'); return receipts.optionsResult ?? true; },
            async reload() { dockCalls.push('reload'); return receipts.reloadResult ?? true; },
            async uninstall() {
                dockCalls.push('uninstall');
                if (receipts.uninstallConfirmed) receipts.dockHello = null;
                return receipts.uninstallConfirmed ?? false;
            },
        },
        addEventListener() {},
        BroadcastChannel: class { postMessage(message) { messages.push(message); } },
        async fetch(url, options = {}) {
            const body = options.body ? JSON.parse(options.body) : null;
            calls.push({url, method: options.method || 'GET', body, options});
            let data;
            if (url === '/api/global-settings/chat-dock') data = receipts.dockFolder || {available: false};
            else if (url === '/api/global-settings/chat-dock/open-folder' && options.method === 'POST') {
                if (receipts.openFolderError) return {ok: false, status: 403, json: async () => ({detail: 'fake open failure'})};
                data = {ok: true};
            } else if (url === '/api/global-settings') {
                data = {providers: structuredClone(providers), cli: receipts.cli, photoshop: receipts.photoshop,
                    preferences: preferenceReceipt(), project_library: structuredClone(library)};
                if (receipts.recovery) data = {storage_recovery: {message: receipts.recovery}};
            } else if (url === '/api/config/data-diagnostics') data = {items: []};
            else if (url.startsWith('/api/global-tasks?')) data = {tasks: structuredClone(receipts.tasks || []), projects: []};
            else if (url === '/api/project-library/p/image-generation-tasks/t/cancel' && options.method === 'POST') data = {ok: true};
            else if (url === '/api/global-settings/preferences' && options.method === 'PATCH') {
                if (receipts.preferenceError) return {ok: false, json: async () => ({detail: 'test save failure'})};
                preferences = {...preferences, ...body};
                data = {preferences: preferenceReceipt({language: 'zh', agent_file_access: 'read-only'})};
            } else if (url === '/api/global-settings/project-library' && options.method === 'POST') {
                library.path = body.path;
                data = {project_library: library, migration: {file_count: 2}};
            }
            else if (url === '/api/providers' && options.method === 'PUT') {
                providers = body.map(({api_key, ...item}) => api_key ? {...item, has_key: true, key_preview: '••••••••0000'} : item);
                data = {providers: structuredClone(providers)};
            } else if (url === '/api/providers/test-connection') data = receipts.testResult || {ok: true, image_models: ['image-one']};
            else if (url === '/api/providers/fetch-models') data = {ok: true, image_models: ['image-one']};
            else throw new Error(`Unexpected fake-backend endpoint: ${url}`);
            return {ok: true, json: async () => data};
        },
    });
    context.window = context;
    context.self = context;
    context.top = receipts.embedded ? {postMessage: message => messages.push(message)} : context;
    context.parent = context.top;
    vm.runInContext(translations, context);
    vm.runInContext(source, context);
    await settle();
    assert.equal(elements.pageStatus.dataset.kind, receipts.recovery ? 'error' : 'success', elements.pageStatus.textContent);
    const nav = panel => elements.settingsNav.querySelectorAll('button').find(button => button.getAttribute('aria-controls') === panel);
    const tick = async (ms = 2000) => {
        await settle();
        const until = now + ms;
        for (;;) {
            const next = [...timers].filter(([, timer]) => timer.due <= until).sort(([, a], [, b]) => a.due - b.due)[0];
            if (!next) break;
            const [id, timer] = next;
            now = timer.due;
            if (timer.repeating) timer.due += timer.delay; else timers.delete(id);
            timer.fn();
            await settle();
        }
        now = until;
    };
    return {document, elements, calls, messages, nav, tick, dockCalls, clipboard, preferenceReads,
        card: id => elements.providerList.children.find(card => card.dataset.providerId === id)};
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

test('#504: grouped navigation shows exactly one panel and keeps drafts across sections, save and refresh', async () => {
    const {elements, document, calls, card, nav} = await boot();
    const assertPanel = panel => {
        assert.deepEqual(document.querySelectorAll('[data-settings-panel]').filter(el => !el.hidden).map(el => el.id), [panel]);
        assert.deepEqual(elements.settingsNav.querySelectorAll('button').filter(el => el.getAttribute('aria-current') === 'true'), [nav(panel)]);
    };
    assert.deepEqual(elements.settingsNav.children.map(group => group.querySelectorAll('button').map(button => button.getAttribute('aria-controls'))), [
        ['preferencesPanel', 'providersPanel', 'libraryPanel'], ['agentFileAccessPanel', 'cliPanel'], ['photoshopPanel', 'chatDockPanel', 'tasksPanel'],
    ]);
    assertPanel('preferencesPanel');
    await nav('preferencesPanel').fire('keydown', {key: 'ArrowDown'});
    assert.equal(document.activeElement, nav('providersPanel'));
    await nav('providersPanel').fire('click');
    assertPanel('providersPanel');
    assert.equal(document.activeElement, elements.providersTitle);
    const draft = card('old');
    const toggle = draft.querySelector('.provider-toggle');
    const editor = draft.querySelector('.provider-editor');
    assert.equal(editor.hidden, true);
    await toggle.fire('click');
    assert.equal(editor.hidden, false);
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    const key = draft.querySelector('[data-field="api_key"]');
    assert.equal(key.value, '');
    assert.equal(key.placeholder, '****6789');
    key.value = 'test-draft-key';
    draft.querySelector('[data-models="image_models"]').value = 'draft-model';
    await nav('libraryPanel').fire('click');
    elements.libraryPath.value = 'D:/draft-library';
    elements.moveExisting.checked = true;
    await nav('preferencesPanel').fire('click');
    elements.themeSelect.value = 'light';
    elements.upstreamDirect.checked = false;
    await elements.refreshAll.fire('click');
    assert.equal(elements.themeSelect.value, 'light');
    assert.equal(elements.upstreamDirect.checked, false);
    await elements.savePreferences.fire('click');
    assert.deepEqual(calls.find(call => call.method === 'PATCH').body, {theme: 'light', upstream_direct: false});
    assert.equal(elements.libraryPath.value, 'D:/draft-library');
    assert.equal(elements.moveExisting.checked, true);
    await nav('providersPanel').fire('click');
    assertPanel('providersPanel');
    assert.equal(card('old'), draft);
    assert.equal(key.value, 'test-draft-key');
    assert.equal(editor.hidden, false);
    await elements.saveProviders.fire('click');
    const savedCard = calls.find(call => call.method === 'PUT').body.find(provider => provider.id === 'old');
    assert.deepEqual(savedCard.image_models, ['draft-model']);
    assert.equal(savedCard.api_key, 'test-draft-key');
    assert.equal(card('old').querySelector('[data-field="api_key"]').value, '');
    assert.ok(!elements.providerList.textContent.includes('test-draft-key'));
    assert.equal(elements.libraryPath.value, 'D:/draft-library');
});

test('#504: aggregate CTA and status dots distinguish known issues, unknown CLI, and task activity', async () => {
    const receipts = {
        cli: {codex: {installed: true, logged_in: false, raw: {login_status_checked: false}},
            gemini: {installed: false, logged_in: false, message: 'Antigravity 状态读取失败：probe failure'}},
        tasks: [{task_id: 't', project_id: 'p', status: 'running', cancellable: true}],
    };
    const {elements, document, nav, card, calls} = await boot('zh', receipts);
    const dot = id => nav(id).querySelector('.settings-nav-dot');
    const issuePanels = () => elements.settingsIssueActions.children.map(action => action.dataset.issuePanel);
    assert.equal(elements.settingsIssues.hidden, true);
    assert.equal(dot('tasksPanel').dataset.tone, 'activity');
    assert.equal(dot('cliPanel').hidden, true);
    assert.equal(dot('providersPanel').hidden, true);
    assert.equal(elements.photoshopStatus.textContent, '状态未知');
    assert.equal(calls.some(call => call.url === '/api/providers/test-connection'), false);
    receipts.photoshop = {connected: false};
    receipts.cli.codex.raw.login_status_checked = true;
    await elements.refreshAll.fire('click');
    assert.deepEqual(issuePanels(), ['cliPanel', 'photoshopPanel']);
    assert.equal(dot('cliPanel').dataset.tone, 'warning');
    assert.equal(dot('photoshopPanel').dataset.tone, 'problem');
    await elements.settingsIssueActions.children[1].fire('click');
    assert.equal(elements.photoshopPanel.hidden, false);
    assert.equal(document.activeElement, elements.psTitle);

    receipts.testResult = {ok: false, status: 503, message: 'fake-key-in-upstream-response'};
    const provider = card('direct');
    const key = provider.querySelector('[data-field="api_key"]');
    key.value = 'fake-key-in-upstream-response';
    await provider.querySelector('[data-provider-action="test"]').fire('click');
    await settle();
    assert.deepEqual(issuePanels(), ['providersPanel', 'cliPanel', 'photoshopPanel']);
    assert.equal(dot('providersPanel').dataset.tone, 'problem');
    assert.equal(provider.querySelector('.provider-fetch-status').textContent, '••••••••');
    assert.ok(!elements.settingsIssueSummary.textContent.includes('fake-key'));
    await key.fire('input');
    assert.equal(dot('providersPanel').hidden, true);
    receipts.photoshop.connected = true;
    receipts.cli.codex.logged_in = true;
    await elements.refreshAll.fire('click');
    assert.equal(elements.settingsIssues.hidden, true);
});

test('#504: task refresh keeps keyboard focus and cancellation updates the activity dot', async () => {
    const task = {task_id: 't', project_id: 'p', status: 'queued', cancellable: true, result_node_id: 'r'};
    const {elements, document, nav, calls} = await boot('zh', {tasks: [task]});
    await nav('tasksPanel').fire('click');
    const findRow = () => elements.taskConsoleList.querySelector('.task-console-row');
    assert.ok(findRow().querySelector('a').href.endsWith('&focus_node=r'));
    const cancel = findRow().querySelector('button');
    cancel.focus();
    await elements.consoleRefresh.fire('click');
    await settle();
    assert.notEqual(findRow().querySelector('button'), cancel);
    assert.equal(document.activeElement, findRow().querySelector('button'));
    await document.activeElement.fire('click');
    await settle();
    assert.ok(calls.some(call => call.url === '/api/project-library/p/image-generation-tasks/t/cancel' && call.method === 'POST'));
    assert.equal(nav('tasksPanel').querySelector('.settings-nav-dot').hidden, true);
    assert.equal(findRow().querySelector('button'), null);
});

test('#504: storage recovery leaves category navigation and embedded return usable', async () => {
    const receipts = {embedded: true, recovery: '恢复设置后再试'};
    const {document, elements, calls, messages, nav} = await boot('zh', receipts);
    assert.equal(elements.savePreferences.disabled, true);
    assert.equal(elements.themeSelect.disabled, true);
    assert.equal(elements.settingsIssues.hidden, true);
    assert.ok(!calls.some(call => call.url.startsWith('/api/global-tasks')));
    await nav('agentFileAccessPanel').fire('click');
    assert.equal(elements.agentFileAccessPanel.hidden, false);
    assert.equal(document.activeElement, elements.agentFileAccessTitle);
    await document.fire('keydown', {key: 'Escape'});
    await elements.backButton.fire('click');
    assert.deepEqual(messages.map(message => message.type), ['close-settings', 'close-settings']);
    delete receipts.recovery;
    await elements.refreshAll.fire('click');
    assert.equal(elements.savePreferences.disabled, false);
    assert.equal(elements.themeSelect.disabled, false);
});

const dockFolder = {available: true, version: '0.10.0', directory: 'D:/portable/env/chat-dock'};
const dockHello = (version, supported = true) => ({ok: true, version, supported});

test('#520: Chat Dock detection follows the four receipt states only while its panel is visible', async () => {
    const receipts = {dockFolder};
    const {elements, document, nav, tick, dockCalls, calls} = await boot('en', receipts);
    await tick();
    assert.deepEqual(dockCalls, []);
    await nav('chatDockPanel').fire('click');
    await settle();
    const assertState = (label, installed) => {
        assert.match(elements.chatDockState.textContent, label);
        assert.equal(elements.chatDockGuide.hidden, installed);
        assert.equal(elements.chatDockInstalled.hidden, !installed);
        assert.equal(elements.chatDockDetectionNote.hidden, installed);
    };
    assert.equal(document.activeElement, elements.chatDockTitle);
    assertState(/not detected/, false);
    assert.match(elements.chatDockDetectionNote.textContent, /Older versions may not respond/);
    receipts.dockHello = dockHello('0.9.12');
    await tick();
    assertState(/installed but outdated/, true);
    assert.match(elements.chatDockVersions.textContent, /0\.9\.12.*0\.10\.0/);
    receipts.dockHello = dockHello('0.9', false);
    await tick();
    assertState(/browser does not support/, true);
    for (const version of ['0.10', '0.11.0']) {
        receipts.dockHello = dockHello(version);
        await tick();
        assertState(/up to date/, true);
    }
    elements.chatDockOptions.focus();
    receipts.dockHello = null;
    await tick();
    assert.equal(document.activeElement, elements.chatDockTitle);
    assertState(/not detected/, false);
    await nav('preferencesPanel').fire('click');
    const count = dockCalls.length;
    await tick();
    assert.equal(dockCalls.length, count);
    assert.ok(!calls.some(call => call.url.startsWith('/api/global-settings/chat-dock/')));
});

test('#520: Chat Dock install guide copies vendor address and folder, and opens the folder via same-origin POST', async () => {
    const receipts = {dockFolder, userAgent: 'Chrome/130.0 Edg/130.0'};
    const {elements, nav, calls, clipboard} = await boot('en', receipts);
    await nav('chatDockPanel').fire('click');
    await settle();
    assert.equal(elements.chatDockManagerUrl.textContent, 'edge://extensions');
    assert.equal(elements.chatDockFolderPath.textContent, dockFolder.directory);
    await elements.chatDockCopyManager.fire('click');
    await elements.chatDockCopyFolder.fire('click');
    await settle();
    assert.deepEqual(clipboard, ['edge://extensions', dockFolder.directory]);
    assert.equal(elements.chatDockActionFeedback.textContent, 'Copied.');
    await elements.chatDockOpenFolder.fire('click');
    const [open] = calls.filter(call => call.url === '/api/global-settings/chat-dock/open-folder');
    assert.equal(open.method, 'POST');
    assert.equal(open.body, null);
    assert.deepEqual({...open.options}, {method: 'POST', mode: 'same-origin', credentials: 'same-origin', referrerPolicy: 'same-origin'});
    receipts.openFolderError = true;
    await elements.chatDockOpenFolder.fire('click');
    assert.equal(elements.chatDockActionFeedback.dataset.kind, 'error');
    assert.match(elements.chatDockActionFeedback.textContent, /Could not open the extension folder/);
    receipts.clipboardError = true;
    await elements.chatDockCopyManager.fire('click');
    assert.match(elements.chatDockActionFeedback.textContent, /Could not copy/);
    receipts.dockFolder = {available: false};
    await elements.refreshAll.fire('click');
    await settle();
    assert.match(elements.chatDockFolderStatus.textContent, /files are missing/);
    assert.equal(elements.chatDockFolderPath.hidden, true);
    assert.equal(elements.chatDockOpenFolder.disabled, true);
    assert.equal(elements.chatDockCopyFolder.disabled, true);
});

test('#520: Chat Dock options, reload and uninstall work through extension receipts', async () => {
    const receipts = {dockFolder, dockHello: dockHello('0.9.12')};
    const {elements, nav, tick, dockCalls, calls} = await boot('en', receipts);
    await nav('chatDockPanel').fire('click');
    await settle();
    assert.equal(elements.chatDockManagerUrl.textContent, 'chrome://extensions');
    await elements.chatDockOptions.fire('click');
    assert.equal(dockCalls.at(-1), 'options');
    receipts.optionsResult = false;
    await elements.chatDockOptions.fire('click');
    assert.match(elements.chatDockActionFeedback.textContent, /extension did not respond/);
    const reload = async () => {
        const done = elements.chatDockReload.fire('click');
        await settle();
        assert.match(elements.chatDockState.textContent, /Detecting/);
        assert.equal(elements.chatDockReload.disabled, true);
        await tick(600);
        await done;
        assert.equal(elements.chatDockReload.disabled, false);
        assert.equal(elements.chatDockActionFeedback.textContent, 'Extension reloaded.');
    };
    await reload();
    assert.equal(elements.chatDockReloadNote.hidden, false);
    assert.match(elements.chatDockReloadNote.textContent, /different folder/);
    receipts.dockHello = dockHello('0.10.0');
    await reload();
    assert.equal(elements.chatDockReloadNote.hidden, true);
    assert.match(elements.chatDockState.textContent, /up to date/);
    for (const confirmed of [false, true]) {
        receipts.uninstallConfirmed = confirmed;
        const done = elements.chatDockUninstall.fire('click');
        await tick(600);
        await done;
        assert.equal(elements.chatDockInstalled.hidden, confirmed);
        assert.equal(elements.chatDockGuide.hidden, !confirmed);
        assert.equal(elements.chatDockActionFeedback.dataset.kind, '');
    }
    assert.deepEqual(dockCalls.filter(call => call !== 'hello'), ['options', 'options', 'reload', 'reload', 'uninstall', 'uninstall']);
    assert.ok(!calls.some(call => call.method !== 'GET'), 'extension actions do not write settings');
});

test('#520: stored Chat Dock preferences are not read or edited by the settings UI', async () => {
    for (const value of [true, false, 'true']) {
        const receipts = {embedded: true, dockHello: dockHello('0.10.0'), preferences: {chat_dock_enabled: value, autosave_interval_minutes: 5}};
        const {elements, calls, messages, nav, preferenceReads} = await boot('en', receipts);
        elements.themeSelect.value = 'light';
        elements.upstreamDirect.checked = false;
        await nav('chatDockPanel').fire('click');
        await settle();
        assert.match(elements.chatDockState.textContent, /installed$/);
        assert.match(elements.chatDockFolderStatus.textContent, /files are missing/);
        assert.equal(elements.chatDockEnabled, null);
        assert.equal(elements.chatDockPreferenceFeedback, null);
        assert.deepEqual(elements.chatDockPanel.querySelectorAll('input, select, textarea'), []);
        assert.deepEqual(preferenceReads, []);
        assert.ok(calls.every(call => call.method === 'GET' && call.url !== '/api/global-settings/preferences'));
        assert.deepEqual(messages, []);
        await nav('preferencesPanel').fire('click');
        assert.equal(elements.themeSelect.value, 'light');
        assert.equal(elements.upstreamDirect.checked, false);
        await elements.savePreferences.fire('click');
        assert.deepEqual(calls.filter(call => call.method === 'PATCH').map(call => call.body), [{theme: 'light', upstream_direct: false}]);
        assert.equal(messages.length, 1);
        assert.equal(messages[0].type, 'preferences-changed');
        assert.equal(messages[0].source, 'global-settings');
        assert.equal(messages[0].preferences.autosave_interval_minutes, 5);
        receipts.preferenceError = true;
        elements.themeSelect.value = 'dark';
        await elements.savePreferences.fire('click');
        assert.equal(elements.pageStatus.dataset.kind, 'error');
        assert.equal(messages.length, 1);
        assert.deepEqual(preferenceReads, []);
    }
});
