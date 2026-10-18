// #194 画布模块单元测试 harness。
//
// 这些模块是 classic `<script>` 文件（不是 ES module）：顶层 `const` 落在
// context 的**全局词法作用域**，后续 `runInContext` 的脚本能读到，但
// `context.NAME` 读不到。所以加载完必须再在 context 内执行一段取值的脚本。
//
// 加载顺序必须与 static/smart-canvas.html 里的 <script> 顺序一致：跨文件重复的
// 顶层 let/const 会让整个后加载的文件解析失败，且没有任何 Python 侧报错
// （实测症状是页面节点全空、pageErrors 为空）。

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const JS_DIR = path.resolve(HERE, '..', '..', 'static', 'js');

// 必须与 static/smart-canvas.html 里 <script> 的相对顺序一致；
// tests/test_canvas_module_units.py 会比对两者，不一致直接红。
export const CANVAS_SCRIPT_ORDER = [
    'outpaint-ratios.js',
    'canvas-node-types.js',
    'canvas-core.js',
    'canvas-state.js',
    'canvas-layout-utils.js',
];

/** 最小浏览器替身：只给被测纯工具真正会碰到的东西，缺什么就让它显式报错。 */
function browserStubs() {
    const storage = new Map();
    const localStorage = {
        getItem: (key) => (storage.has(key) ? storage.get(key) : null),
        setItem: (key, value) => { storage.set(key, String(value)); },
        removeItem: (key) => { storage.delete(key); },
    };
    const noop = () => {};
    const elementStub = () => ({
        style: {}, dataset: {}, classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
        setAttribute: noop, removeAttribute: noop, getAttribute: () => null,
        appendChild: noop, removeChild: noop, addEventListener: noop, removeEventListener: noop,
        querySelector: () => null, querySelectorAll: () => [], textContent: '', innerHTML: '',
    });
    const document = {
        documentElement: elementStub(),
        body: elementStub(),
        head: elementStub(),
        createElement: elementStub,
        createTextNode: () => ({}),
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener: noop,
        removeEventListener: noop,
        elementFromPoint: () => null,
    };
    const window = {
        document,
        localStorage,
        location: { search: '', href: 'http://localhost/static/smart-canvas.html', pathname: '/static/smart-canvas.html', hash: '' },
        devicePixelRatio: 1,
        innerWidth: 1024,
        innerHeight: 768,
        addEventListener: noop,
        removeEventListener: noop,
        requestAnimationFrame: (fn) => { fn(0); return 1; },
        cancelAnimationFrame: noop,
        matchMedia: () => ({ matches: false, addEventListener: noop, removeEventListener: noop }),
        getComputedStyle: () => ({ getPropertyValue: () => '' }),
        fetch: () => { throw new Error('harness: fetch 未替身，被测模块不应在加载期取网络'); },
    };
    // vm context 自带 ECMAScript 内置对象，但不带宿主 Web 全局。
    // canvas-core.js 顶层就读了 location / URLSearchParams，缺了会整文件解析失败。
    return {
        window,
        document,
        localStorage,
        location: window.location,
        URL,
        URLSearchParams,
        setTimeout,
        clearTimeout,
        setInterval,
        clearInterval,
        console,
    };
}

/** 把跨 realm 的值重建成 host realm 的普通对象/数组（deepStrictEqual 比原型）。 */
export function normalizeValue(value) {
    if (value === null || typeof value !== 'object') return value;
    if (typeof value === 'function') return value;
    // 注意：对跨 realm 数组调 .map() 会用该 realm 的 Array 构造新数组
    // （Symbol.species），原型仍是跨 realm 的。必须先用展开重建 host 数组。
    if (Array.isArray(value)) return [...value].map(normalizeValue);
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, normalizeValue(v)]));
}

/**
 * 用于 UMD 模块导出的对象：数据字段归一化，函数包一层让**返回值**也归一化。
 * 直接调跨 realm 函数会返回跨 realm 对象，deepStrictEqual 会红。
 */
export function normalizeApi(api) {
    const out = {};
    for (const [key, value] of Object.entries(api)) {
        out[key] = typeof value === 'function' ? (...args) => normalizeValue(value(...args)) : normalizeValue(value);
    }
    return out;
}

/**
 * 按 HTML 顺序把 classic 脚本装进同一个 context。
 * @param {string[]} files 相对 static/js 的文件名
 * @returns {{context: object, take: (names: string[]) => object}}
 */
export function loadCanvasScripts(files) {
    const sandbox = browserStubs();
    const context = vm.createContext(sandbox);
    for (const file of files) {
        const full = path.join(JS_DIR, file);
        const source = fs.readFileSync(full, 'utf8');
        // filename 传真实路径：解析错误里能直接看到是哪个文件。
        vm.runInContext(source, context, { filename: full });
    }
    // vm context 里创建的对象原型属于另一个 realm，host 侧的
    // assert.deepStrictEqual 会因原型不同而失败（值完全一样）。
    // 这里把跨界的值重建成 host realm 的普通对象/数组；函数则包一层，
    // 让返回值也自动归一化。
    const normalize = (value) => normalizeValue(value);
    const take = (names) => {
        const raw = vm.runInContext(
            `(() => { const out = {}; ${names
                .map((n) => `out[${JSON.stringify(n)}] = typeof ${n} === 'undefined' ? undefined : ${n};`)
                .join(' ')} return out; })()`,
            context,
        );
        const result = {};
        for (const name of names) {
            const value = raw[name];
            result[name] = typeof value === 'function'
                ? (...args) => normalize(value(...args))
                : normalize(value);
        }
        return result;
    };
    return { context, take };
}
