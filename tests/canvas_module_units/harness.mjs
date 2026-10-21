// #194 测试侧假核心（harness）。
//
// static/js/ 下的画布脚本是 classic <script> 模块：它们共享同一个全局词法作用域
// （每个文件的顶层 let/const/function 落在同一作用域，跨文件重复声明会让整个
// 后加载文件解析失败，SyntaxError）。本文件用 node:vm 复刻同样的环境：
//   1. vm.createContext 建 context，window 指向 context 自身（浏览器里 window === 全局对象）；
//   2. 按 static/smart-canvas.html 的 <script> 标签顺序，把模块逐个 runInContext 进同一 context；
//   3. 预桩 vm context 里缺失的浏览器全局（document/location/localStorage/CustomEvent/
//      requestAnimationFrame/fetch/可控定时器等）。
//
// 用法（见各 test-*.mjs）：
//   import { createCanvasContext, loadModules, evaluate, globalFunction } from './harness.mjs';
//   const { ctx, harness } = createCanvasContext();
//   loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js']);
//   globalFunction(ctx, 'escapeHtml');   // 顶层 function/var：context 全局对象的属性
//   evaluate(ctx, 'settings.width');     // let/const：只活在词法环境，必须用表达式读
//
// 若 loadModules 抛 SyntaxError（"Identifier X has already been declared"），是加载顺序或
// HTML_SCRIPT_ORDER 表的问题（tests/test_canvas_core_layer.py 有文本侧守卫），不是被测代码问题。

import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(here, '..', '..');
export const JS_DIR = path.join(ROOT, 'static', 'js');

// 画布相关脚本的相对加载顺序（与 static/smart-canvas.html 的 <script src> 顺序一致）。
// i18n-core.js 不是 HTML 里的 <script src>：static/js/i18n.js 在运行时注入它
// （解析期 document.write、否则异步 append），等效运行于 outpaint-ratios.js 之前，
// 故本表排在最前。HTML 变更时本表必须同步更新——
// tests/test_canvas_module_units.py::test_harness_order_matches_html 会钉住。
export const HTML_SCRIPT_ORDER = [
    'i18n-core.js',
    'outpaint-ratios.js',
    'canvas-node-types.js',
    'canvas-core.js',
    'canvas-state.js',
    'project-canvas-mutations.js',
    'canvas-layout-utils.js',
    'canvas-connections.js',
    'canvas-media-history.js',
    'canvas-undo.js',
    'canvas-node-ops.js',
    'canvas-image-editor.js',
    'canvas-generation-cascade.js',
    'canvas-generation-tasks.js',
    'canvas-agent-tui.js',
    'smart-canvas.js',
];

function makeElement(tag = 'div') {
    const classes = new Set();
    const attrs = new Map();
    const listeners = new Map();
    const el = {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        style: {},
        dataset: {},
        value: '',
        checked: false,
        disabled: false,
        hidden: false,
        textContent: '',
        innerHTML: '',
        classList: {
            add: (...cs) => cs.forEach((c) => classes.add(String(c))),
            remove: (...cs) => cs.forEach((c) => classes.delete(String(c))),
            contains: (c) => classes.has(String(c)),
            toggle: (c, force) => {
                const s = String(c);
                // DOM 语义：toggle(c) 翻转；toggle(c, force) 强制设置。
                const on = force === undefined ? !classes.has(s) : Boolean(force);
                if (on) classes.add(s); else classes.delete(s);
                return on;
            },
            toString: () => [...classes].join(' '),
        },
        hasAttribute: (n) => attrs.has(String(n)),
        getAttribute: (n) => (attrs.has(String(n)) ? attrs.get(String(n)) : null),
        setAttribute: (n, v) => { attrs.set(String(n), String(v)); },
        removeAttribute: (n) => { attrs.delete(String(n)); },
        addEventListener: (t, fn) => {
            const arr = listeners.get(t) || [];
            arr.push(fn);
            listeners.set(t, arr);
        },
        removeEventListener: () => {},
        dispatchEvent: (ev) => {
            (listeners.get(ev.type) || []).slice().forEach((fn) => fn.call(el, ev));
            return true;
        },
        appendChild: (child) => { el.children.push(child); child.parentNode = el; return child; },
        removeChild: (child) => { el.children = el.children.filter((c) => c !== child); return child; },
        replaceChild: (newChild, oldChild) => {
            const i = el.children.indexOf(oldChild);
            if (i >= 0) el.children[i] = newChild;
            return oldChild;
        },
        remove: () => {},
        querySelector: () => null,
        querySelectorAll: () => [],
        getBoundingClientRect: () => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }),
        focus: () => {},
        blur: () => {},
        click: () => { (listeners.get('click') || []).slice().forEach((fn) => fn.call(el, { type: 'click' })); },
    };
    return el;
}

export function createCanvasContext({ locationSearch = '' } = {}) {
    const ctx = vm.createContext({});

    const harness = {
        ctx,
        makeElement,
        // 注册（或取回）一个 document.getElementById 能找到的元素。
        // 浏览器里 getElementById 找不到返回 null，所以测试必须显式注册被测元素，
        // 不能依赖桩自动造出来——那会把「页面里没有这个 DOM 节点」的缺陷盖掉。
        elementsById: new Map(),
        element(id, tag = 'div') {
            if (!harness.elementsById.has(id)) harness.elementsById.set(id, makeElement(tag));
            return harness.elementsById.get(id);
        },
        // document.querySelector(All) 的结果，按选择器注册。
        queryResults: Object.create(null),
        querySelectorAll(selector, els) { harness.queryResults[selector] = els; return els; },
        // document.addEventListener 的记录。
        documentListeners: new Map(),
        fireDocumentEvent(type) {
            (harness.documentListeners.get(type) || []).slice().forEach((fn) => fn.call(document, { type }));
        },
        // window.addEventListener / dispatchEvent 的记录。
        windowListeners: new Map(),
        dispatchedEvents: [],
        // 可控定时器：被测代码的 setTimeout 记录在这里，测试显式 advanceTimers() 才触发。
        timers: [],
        timerSeq: 1,
        advanceTimers() {
            const due = harness.timers.filter((t) => !t.fired && !t.cancelled);
            due.forEach((t) => { t.fired = true; t.fn(...t.args); });
            return due.length;
        },
        intervals: [],
        clearIntervals() { harness.intervals.length = 0; return true; },
        // requestAnimationFrame 队列，测试显式 flushRafs() 才跑。
        rafQueue: [],
        rafSeq: 1,
        flushRafs() {
            const due = harness.rafQueue.splice(0);
            due.forEach((t) => t.fn(0));
            return due.length;
        },
        // localStorage 底层存储。
        store: new Map(),
    };

    const document = {
        readyState: 'complete',
        documentElement: makeElement('html'),
        head: makeElement('head'),
        body: makeElement('body'),
        currentScript: null,
        createElement: (t) => makeElement(t),
        getElementById: (id) => harness.elementsById.get(id) || null,
        querySelector: (sel) => (harness.queryResults[sel] || [])[0] || null,
        querySelectorAll: (sel) => harness.queryResults[sel] || [],
        addEventListener: (type, fn) => {
            const arr = harness.documentListeners.get(type) || [];
            arr.push(fn);
            harness.documentListeners.set(type, arr);
        },
        removeEventListener: () => {},
        write: () => {},
    };

    // window === 全局对象（classic script 的共享作用域载体）。
    ctx.window = ctx;

    // 宿主透传：裸 vm context 没有这些 WHATWG/宿主全局，不注入 canvas-core 顶层的
    // `new URLSearchParams(location.search)` 直接 ReferenceError。
    ctx.URL = URL;
    ctx.URLSearchParams = URLSearchParams;
    ctx.console = console;
    ctx.queueMicrotask = (fn) => { Promise.resolve().then(fn); };
    ctx.setTimeout = (fn, ms, ...args) => {
        const id = harness.timerSeq++;
        harness.timers.push({ id, fn, ms: ms || 0, args, fired: false, cancelled: false });
        return id;
    };
    ctx.clearTimeout = (id) => {
        const t = harness.timers.find((x) => x.id === id);
        if (t) t.cancelled = true;
    };
    ctx.setInterval = (fn, ms, ...args) => {
        const id = harness.timerSeq++;
        harness.intervals.push({ id, fn, ms: ms || 0, args });
        return id;
    };
    ctx.clearInterval = (id) => { harness.intervals = harness.intervals.filter((t) => t.id !== id); };
    ctx.requestAnimationFrame = (fn) => {
        const id = harness.rafSeq++;
        harness.rafQueue.push({ id, fn });
        return id;
    };
    ctx.cancelAnimationFrame = (id) => { harness.rafQueue = harness.rafQueue.filter((t) => t.id !== id); };
    ctx.fetch = async () => { throw new Error('fetch is disabled in the unit harness'); };
    ctx.CustomEvent = class CustomEvent {
        constructor(type, options = {}) { this.type = type; this.detail = options && options.detail; }
    };
    ctx.Event = class Event {
        constructor(type) { this.type = type; }
    };
    ctx.addEventListener = (type, fn) => {
        const arr = harness.windowListeners.get(type) || [];
        arr.push(fn);
        harness.windowListeners.set(type, arr);
    };
    ctx.removeEventListener = () => {};
    ctx.dispatchEvent = (ev) => { harness.dispatchedEvents.push(ev); return true; };
    ctx.location = {
        search: locationSearch,
        hash: '',
        href: 'http://localhost/static/smart-canvas.html' + (locationSearch || ''),
        origin: 'http://localhost',
        protocol: 'http:',
        host: 'localhost',
        hostname: 'localhost',
        pathname: '/static/smart-canvas.html',
    };
    ctx.localStorage = {
        getItem: (k) => (harness.store.has(k) ? harness.store.get(k) : null),
        setItem: (k, v) => { harness.store.set(k, String(v)); },
        removeItem: (k) => { harness.store.delete(k); },
        clear: () => { harness.store.clear(); },
        key: (i) => [...harness.store.keys()][i] || null,
        get length() { return harness.store.size; },
    };
    ctx.navigator = { language: 'zh-CN', languages: ['zh-CN'], userAgent: 'node-unit-harness' };
    ctx.screen = { width: 1920, height: 1080 };
    ctx.performance = { now: () => 0 };
    ctx.alert = () => {};
    ctx.confirm = () => true;
    ctx.prompt = () => null;
    ctx.document = document;

    return { ctx, harness };
}

// 按 HTML_SCRIPT_ORDER 的相对顺序把 names 逐个加载进 ctx（共享全局词法作用域）。
export function loadModules(ctx, names) {
    const requested = [...new Set(names)];
    if (requested.length !== names.length) throw new Error(`harness: duplicate module requested: ${names.join(', ')}`);
    const unknown = requested.filter((n) => !HTML_SCRIPT_ORDER.includes(n));
    if (unknown.length > 0) throw new Error(`harness: module(s) not in HTML_SCRIPT_ORDER: ${unknown.join(', ')}`);
    const ordered = HTML_SCRIPT_ORDER.filter((n) => requested.includes(n));
    for (const name of ordered) {
        const source = readFileSync(path.join(JS_DIR, name), 'utf8');
        try {
            vm.runInContext(source, ctx, { filename: `static/js/${name}` });
        } catch (err) {
            throw new Error(`harness: failed to load static/js/${name}: ${err && err.message}`);
        }
    }
    return ctx;
}

// 读词法环境里的 let/const（顶层 let/const 不是全局对象的属性，只能这样取）。
export function evaluate(ctx, expression) {
    return vm.runInContext(expression, ctx, { filename: '<evaluate>' });
}

// 取顶层 function/var（是 context 全局对象的属性）。
export function globalFunction(ctx, name) {
    const value = ctx[name];
    if (typeof value !== 'function') {
        throw new Error(`harness: expected top-level function ${name}, got ${typeof value}`);
    }
    return value;
}

// vm context 是独立的 V8 realm：里面 new 出来的对象带 context 自己的 Object.prototype，
// 宿主侧 assert.deepEqual（prototype 敏感）会跨 realm 假红。
// 断言前把被测函数返回的纯数据 JSON 往返一次，转回宿主 realm（仅限可序列化结果）。
export function fromContext(value) {
    return JSON.parse(JSON.stringify(value));
}
