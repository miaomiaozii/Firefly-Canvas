// #365：i18n-core 中文单源行为测试 harness。
//
// 在 vm 沙箱里执行 static/js/i18n-core.js 的真实代码（非副本），预置 localStorage
// 为老用户值 'en'，断言 #365 之后的行为：
//   1. lang() 恒返回 'zh'；
//   2. localStorage 里的旧值被原地改写成 'zh'；
//   3. toggle 入口已删除（全仓无调用者）；
//   4. t() 返回 zh 文案（即使沙箱初始是 en）；
//   5. set('en') 仍写 'zh'（老调用点兼容）；
//   6. document lang 属性是 zh-CN 而不是 en。
// 任一失败：打印原因并退出码 1（门/测试看到红）。
//
// 运行约定与 ui-toast-network-error-harness.mjs 相同：`node tests/i18n-core-zh-only-harness.mjs`
// （单参数，cwd 为仓库根）。
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { runInNewContext } from 'node:vm';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const src = readFileSync(join(root, 'static', 'js', 'i18n-core.js'), 'utf8');

// 模拟老用户：localStorage 里存着 'en'。
const storage = { studio_lang: 'en' };
const langAttrs = [];
const windowEvents = [];
const sandbox = {
    window: { dispatchEvent: (ev) => windowEvents.push(ev.type) },
    document: {
        // 立即触发 DOMContentLoaded 回调，让加载期的 apply() 也跑到（真实页面语义）。
        addEventListener: (type, fn) => { if (type === 'DOMContentLoaded') fn(); },
        documentElement: { setAttribute: (k, v) => langAttrs.push([k, v]) },
        querySelectorAll: () => [],
    },
    localStorage: {
        getItem: (k) => (k in storage ? storage[k] : null),
        setItem: (k, v) => { storage[k] = String(v); },
    },
    CustomEvent: function CustomEvent(type, init) { return { type, ...(init || {}) }; },
    console,
};

runInNewContext(src, sandbox, { filename: 'i18n-core.js' });

const I = sandbox.window.StudioI18n;
const failures = [];
function check(label, actual, expected) {
    if (actual !== expected) {
        failures.push(`${label}: 期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`);
    }
}

check('lang()（旧值 en）', I.lang(), 'zh');
check('localStorage 旧值被原地改写', storage.studio_lang, 'zh');
check('toggle 入口已删除', 'toggle' in I, false);
I.register({ sample: { zh: '示例', en: 'Sample' } });
check('t() 返回 zh 文案', I.t('sample'), '示例');
check('document lang 属性', langAttrs.length ? langAttrs[langAttrs.length - 1][1] : null, 'zh-CN');
I.set('en');
check('set("en") 后 lang()', I.lang(), 'zh');
check('set("en") 后 localStorage', storage.studio_lang, 'zh');
check('set("en") 后 t()', I.t('sample'), '示例');

if (failures.length) {
    console.error(`i18n-core zh-only: ${failures.length} 项断言失败\n` + failures.join('\n'));
    process.exit(1);
}
console.log('i18n-core zh-only: 8 项断言全过（旧 en → zh、toggle 已删、set("en") 无效、lang=zh-CN）');
