// #203 canvas-core.js 行为单元测试（#199 轨道 4，ADR 0020：按依赖面从小到大补测，canvas-core 先行）。
//
// canvas-core.js 是画布前端的共享底层（#173 核心下沉，classic 脚本，canvas 脚本中最先加载）：
//   - 跨模块共享的可变状态：settings（52 字段默认值）/ params / canvas；
//   - 纯工具：uid / escapeHtml(+escapeAttr 别名) / nowMs / nodeScale / cloneSmartSettings /
//     resultMediaUrls / smartGenerationSubmissions；
//   - 类型谓词：selectedNode / isSmartImageNode / isSmartGroupNode；
//   - toast（只碰 #toast 元素与定时器）；tr（StudioI18n 可选）；
//   - #197 pending-action 注册表（rebuilder/persister，供 canvas-generation-tasks.js 在
//     fetch 前落盘并重建 replay 闭包）。
//
// 依赖面（机械抽取实测，口径=守卫的解析函数 + 去字符串/注释/属性访问）：**18 个外部自由名**，
//   - 9 个 JS 内建（Array/Boolean/Date/JSON/Map/Math/Number/Set/String）——V8 自带，无需桩；
//   - 6 个浏览器全局（URLSearchParams/document/location/setTimeout/clearTimeout/window）——harness 已提供；
//   - 3 个其他模块名：canvasState（canvas-state.js）、NODE_TYPE_IMAGE / NODE_TYPE_GROUP
//     （canvas-node-types.js）——**不桩**：直接加载这两个真实小模块（纯声明，已各有单测），
//     所以本文件 0 个手写函数桩。ADR 0020 判据「桩要开始重新实现产品逻辑就停」远未触及。
// 另需注册 #toast DOM 元素（harness.element，页面真实节点，非函数桩）；
// window.StudioI18n 是 tr 的可选宿主属性（`?.` 守卫），按测试显式设置。
//
// 防的回归（全部断言可观察输出/副作用，不是「不抛错就算过」）：
//   - settings 字段集与默认值漂移（漏一个字段不会在任何单个模块里报错，而是运行时炸，
//     与 test-canvas-state.mjs 同一思路）；
//   - resultMediaUrls 的递归展开/字段带出/数字强转/去重——生成结果→画布图片的唯一入口，
//     键名顺序或去重逻辑一变，用户看到的就是丢图或重图；
//   - nodeScale 的遗留分组 1.6→0.8 迁移——老数据回迁路径，改错会让旧分组尺寸整体漂移；
//   - cloneSmartSettings 的深拷贝语义 + 非 JSON 源（循环引用）走 catch 浅拷贝不抛错；
//   - toast 的 1800ms 计时与连发时的 clearTimeout 行为；
//   - pending-action 注册表的 String(kind) 归一与 function 守卫（#197 clear-on-send 协议的地基）。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

// 按 HTML 相对顺序加载（harness 会按 HTML_SCRIPT_ORDER 排序）：
// canvas-node-types.js → canvas-core.js → canvas-state.js。
// canvas-core.js 加载期不触碰 canvasState（只有 selectedNode() 运行期读），
// 所以核心先于 canvas-state 加载是安全的——这正是浏览器里的真实顺序。
function loadCore({ locationSearch = '' } = {}) {
    const { ctx, harness } = createCanvasContext({ locationSearch });
    // #toast 是页面真实 DOM 节点：显式注册，让 toast() 写的是模块真正操作的元素状态。
    harness.element('toast');
    loadModules(ctx, ['canvas-node-types.js', 'canvas-core.js', 'canvas-state.js']);
    return { ctx, harness };
}

const EXPECTED_SETTINGS = {
    engine: 'api',
    apiKind: 'image',
    routingMode: 'auto',
    anglePreset: 'none',
    provider_id: '',
    model: '',
    ratio: 'square',
    resolution: '4k',
    customRatio: '',
    customRatioWidth: '',
    customRatioHeight: '',
    customSize: '',
    customWidth: '',
    customHeight: '',
    quality: 'auto',
    count: 1,
    videoRoutingMode: 'auto',
    videoProvider: '',
    videoModel: '',
    videoDuration: 5,
    videoAspect: '16:9',
    videoResolution: '',
    videoEnhancePrompt: false,
    videoEnableUpsample: false,
    videoWatermark: false,
    videoCameraFixed: false,
    videoReturnLastFrame: false,
    videoGenerateAudio: false,
    videoMultimodal: true,
    _videoMultimodalUserSet: false,
    videoUseFrameRoles: false,
    videoTrustedAsset: false,
    videoTrustedSource: 'library',
    videoTempShLinks: [],
    msgenModel: 'custom',
    msCustomModel: '',
    msRatio: 'square',
    msResolution: '1k',
    msCustomRatio: '',
    msCustomRatioWidth: '',
    msCustomRatioHeight: '',
    msCustomSize: '',
    msCustomWidth: '',
    msCustomHeight: '',
    rhConfigKey: '',
    rhPayment: 'free',
    rhInstanceType: '',
    rhParams: {},
    rhRandomActive: {},
    width: 1024,
    height: 1024,
    promptH: 84,
};

test('settings keeps the full contract field set with exact defaults', () => {
    const { ctx } = loadCore();
    const snap = fromContext(evaluate(ctx, 'settings'));
    assert.deepEqual(
        Object.keys(snap).sort(),
        Object.keys(EXPECTED_SETTINGS).sort(),
        'settings 字段集必须逐一对应（多/少一个字段都是契约漂移）',
    );
    assert.deepEqual(snap, EXPECTED_SETTINGS, 'settings 每个默认值都必须精确保持');
});

test('params is a real URLSearchParams bound to the page URL at load time', () => {
    const { ctx } = loadCore({ locationSearch: '?ratio=16:9&model=a%20b' });
    assert.equal(evaluate(ctx, 'params instanceof URLSearchParams'), true, 'params 必须是真 URLSearchParams');
    assert.equal(evaluate(ctx, "params.get('ratio')"), '16:9', '查询参数按 URL 解析');
    assert.equal(evaluate(ctx, "params.get('model')"), 'a b', '百分号编码要解码');
    assert.equal(evaluate(ctx, "params.get('absent')"), null, '不存在的键是 null');
});

test('canvas starts null until the owning module assigns it', () => {
    const { ctx } = loadCore();
    assert.equal(evaluate(ctx, 'canvas'), null, 'canvas 初始为 null（由宿主页面/其他模块赋值）');
});

test('uid builds a prefixed unique token', () => {
    const { ctx } = loadCore();
    const uid = globalFunction(ctx, 'uid');
    const a = uid('node');
    const b = uid('node');
    assert.equal(typeof a, 'string');
    assert.ok(a.startsWith('node_'), `uid 前缀要保留（${a}）`);
    assert.ok(a.length > 'node_'.length, '前缀后要有随机段与时间戳段');
    assert.notEqual(a, b, '连续两次调用必须不同（随机段生效）');
});

test('escapeHtml escapes the five HTML special characters; null/undefined become empty string', () => {
    const { ctx } = loadCore();
    const escapeHtml = globalFunction(ctx, 'escapeHtml');
    assert.equal(escapeHtml(null), '', 'null 按空串处理');
    assert.equal(escapeHtml(undefined), '', 'undefined 按空串处理');
    assert.equal(escapeHtml('plain text 中文'), 'plain text 中文', '无特殊字符原样通过');
    assert.equal(
        escapeHtml('<a href="x">&\'y\'</a>'),
        '&lt;a href=&quot;x&quot;&gt;&amp;&#39;y&#39;&lt;/a&gt;',
        '& < > " \' 五个字符各自映射',
    );
});

test('escapeAttr is the same function object as escapeHtml (alias, not a copy)', () => {
    const { ctx } = loadCore();
    assert.equal(evaluate(ctx, 'escapeAttr === escapeHtml'), true, '别名必须指向同一函数对象');
});

test('nowMs returns a finite number', () => {
    const { ctx } = loadCore();
    const nowMs = globalFunction(ctx, 'nowMs');
    const t = nowMs();
    assert.equal(typeof t, 'number');
    assert.ok(Number.isFinite(t), `nowMs 必须有限（${t}）`);
});

test('selectedNode reads shared canvasState: the node for selectedId, or null', () => {
    const { ctx } = loadCore();
    assert.equal(globalFunction(ctx, 'selectedNode')(), null, '无选中 → null');
    evaluate(ctx, "canvasState.nodes = [{id:'n1'},{id:'n2'}]; canvasState.selectedId = 'n2';");
    assert.equal(
        evaluate(ctx, 'selectedNode() === canvasState.nodes[1]'),
        true,
        '返回的是共享状态里那个节点引用本身',
    );
    evaluate(ctx, "canvasState.selectedId = 'missing';");
    assert.equal(globalFunction(ctx, 'selectedNode')(), null, '选中 id 无匹配 → null');
});

test('isSmartImageNode treats image-typed and legacy no-type nodes as image; others are not', () => {
    const { ctx } = loadCore();
    assert.equal(evaluate(ctx, 'isSmartImageNode({type:NODE_TYPE_IMAGE})'), true, 'image 类型');
    assert.equal(evaluate(ctx, 'isSmartImageNode({})'), true, '无 type 的遗留节点按 image 处理');
    assert.equal(evaluate(ctx, 'isSmartImageNode(null)'), false, 'null 不是 image 节点');
    assert.equal(evaluate(ctx, 'isSmartImageNode({type:NODE_TYPE_PROMPT})'), false, 'prompt 不是 image');
    assert.equal(evaluate(ctx, 'isSmartImageNode({type:NODE_TYPE_GROUP})'), false, 'group 不是 image');
});

test('isSmartGroupNode accepts only group-typed nodes', () => {
    const { ctx } = loadCore();
    assert.equal(evaluate(ctx, 'isSmartGroupNode({type:NODE_TYPE_GROUP})'), true, 'group 类型');
    assert.equal(evaluate(ctx, 'isSmartGroupNode({type:NODE_TYPE_IMAGE})'), false, 'image 不是 group');
    assert.equal(evaluate(ctx, 'isSmartGroupNode({})'), false, '无 type 不是 group（与 image 的遗留语义不同）');
    assert.equal(evaluate(ctx, 'isSmartGroupNode(null)'), false, 'null 不是 group');
});

test('nodeScale falls back to 1 for missing/invalid scale and migrates the legacy 1.6 group scale', () => {
    const { ctx } = loadCore();
    assert.equal(evaluate(ctx, 'NODE_TYPE_GROUP'), 'smart-group'); // 前置：常量已按契约加载
    assert.equal(evaluate(ctx, 'nodeScale(null)'), 1, '无节点 → 1');
    assert.equal(evaluate(ctx, 'nodeScale({})'), 1, '无 scale → 1');
    assert.equal(evaluate(ctx, 'nodeScale({scale:0})'), 1, 'scale=0 不合法 → 1');
    assert.equal(evaluate(ctx, 'nodeScale({scale:-2})'), 1, '负数不合法 → 1');
    assert.equal(evaluate(ctx, 'nodeScale({scale:"abc"})'), 1, 'NaN 不合法 → 1');
    assert.equal(evaluate(ctx, 'nodeScale({scale:2.5})'), 2.5, '合法 scale 原样保留');
    assert.equal(evaluate(ctx, 'nodeScale({scale:"3"})'), 3, '数字字符串强转');
    // 遗留分组迁移：多成员 + 旧默认 1.6 → 新默认 0.8
    assert.equal(evaluate(ctx, 'nodeScale({scale:1.6, images:[1,2]})'), 0.8, '多成员遗留 1.6 → 0.8');
    assert.equal(evaluate(ctx, 'nodeScale({scale:1.6, images:[1]})'), 1.6, '单成员不触发迁移');
    assert.equal(evaluate(ctx, 'nodeScale({scale:1.6})'), 1.6, '无 images 不触发迁移');
});

test('cloneSmartSettings deep-copies (default source = live settings) without leaking mutations', () => {
    const { ctx } = loadCore();
    assert.equal(
        evaluate(ctx, `(() => {
            const copy = cloneSmartSettings();
            copy.rhParams.mutated = true;
            copy.videoTempShLinks.push('leak');
            return Object.keys(settings.rhParams).length === 0
                && settings.videoTempShLinks.length === 0
                && copy.width === settings.width
                && copy.count === settings.count;
        })()`),
        true,
        '默认源=当前 settings；克隆是深拷贝，改克隆不污染活状态（且默认值本身保真）',
    );
    assert.equal(
        evaluate(ctx, `(() => {
            const src = { nested: { a: 1 } };
            const copy = cloneSmartSettings(src);
            copy.nested.a = 2;
            return copy !== src && src.nested.a === 1;
        })()`),
        true,
        '显式源也是深拷贝',
    );
    assert.equal(
        evaluate(ctx, 'Object.keys(cloneSmartSettings(null)).length === 0'),
        true,
        'null 源 → 空对象',
    );
});

test('cloneSmartSettings falls back to a shallow copy for non-JSON sources (circular) without throwing', () => {
    const { ctx } = loadCore();
    assert.equal(
        evaluate(ctx, `(() => {
            const circ = {}; circ.self = circ;
            const copy = cloneSmartSettings(circ); // JSON 路径抛 TypeError → catch
            return copy !== circ && copy.self === circ;
        })()`),
        true,
        '循环引用走 catch 分支：不抛错、返回新对象、self 仍是原引用（浅拷贝）',
    );
});

test('resultMediaUrls extracts urls from arbitrarily nested results, coercing sizes and deduping', () => {
    const { ctx } = loadCore();
    const resultMediaUrls = globalFunction(ctx, 'resultMediaUrls');

    assert.deepEqual(fromContext(resultMediaUrls(null)), [], 'null 结果 → 空');
    assert.deepEqual(fromContext(resultMediaUrls('a.png')), ['a.png'], '裸字符串直通');
    assert.deepEqual(
        fromContext(resultMediaUrls(['a.png', 'b.mp4'])),
        ['a.png', 'b.mp4'],
        '字符串数组直通',
    );

    // 对象：url/path/src/uri 任一 + 元数据带出 + 尺寸强转（字符串→数字，0/缺省不带）
    const [one] = fromContext(resultMediaUrls({
        url: 'a.png', kind: 'image', name: 'n', filename: 'f',
        mime_type: 'image/png', asset_id: 'A1',
        natural_w: '800', natural_h: 600, width: 0, w: 100,
    }));
    assert.equal(one.url, 'a.png');
    assert.equal(one.kind, 'image');
    assert.equal(one.name, 'n');
    assert.equal(one.mime_type, 'image/png');
    assert.equal(one.asset_id, 'A1');
    assert.equal(one.natural_w, 800, '字符串尺寸强转为数字');
    assert.equal(one.natural_h, 600);
    assert.equal(one.w, 100);
    assert.equal('width' in one, false, '0 值尺寸不带出');

    // kind 回退链 kind || type || mediaKind || ''
    assert.equal(fromContext(resultMediaUrls({ url: 'x', type: 'video' }))[0].kind, 'video');
    assert.equal(fromContext(resultMediaUrls({ url: 'x', mediaKind: 'audio' }))[0].kind, 'audio');
    assert.equal(fromContext(resultMediaUrls({ url: 'x' }))[0].kind, '');

    // 递归展开（outputs/data.result 等多层键）+ 重复遍历去重
    const nested = { outputs: [{ url: 'o1' }, { path: 'p1', name: 'x' }], data: { result: [{ uri: 'u1' }] } };
    assert.deepEqual(
        fromContext(resultMediaUrls(nested)).map((i) => i.url),
        ['o1', 'p1', 'u1'],
        '嵌套结果全部展开且去重',
    );

    // 同一 url 以不同形态出现 → 只留第一个（第一个形态被保留：对象留对象、裸串留裸串）
    const dup = { images: [{ url: 'a.png' }, { path: 'a.png', name: 'again' }], urls: ['a.png', 'b.png'] };
    assert.deepEqual(
        fromContext(resultMediaUrls(dup)).map((i) => (typeof i === 'string' ? i : i.url)),
        ['a.png', 'b.png'],
        '重复 url 去重',
    );

    // 无 url/path/src/uri 的条目被过滤，不产生空 url 图片
    const noUrl = { images: [{ name: 'no-url' }], videos: [{ url: 'v.mp4' }] };
    assert.deepEqual(
        fromContext(resultMediaUrls(noUrl)).map((i) => (typeof i === 'string' ? i : i.url)),
        ['v.mp4'],
        '缺 url 的条目被丢弃',
    );
});

test('smartGenerationSubmissions keeps only entries carrying a truthy idempotencyKey', () => {
    const { ctx } = loadCore();
    const fn = globalFunction(ctx, 'smartGenerationSubmissions');
    assert.deepEqual(fromContext(fn(null)), [], '无节点 → []');
    assert.deepEqual(fromContext(fn({})), [], '无字段 → []');
    assert.deepEqual(fromContext(fn({ generationSubmissions: 'nope' })), [], '非数组 → []');
    const node = {
        generationSubmissions: [
            { idempotencyKey: 'K1', round: 1 },
            null,
            { idempotencyKey: '' },
            { round: 2 },
            { idempotencyKey: 'K2' },
        ],
    };
    assert.deepEqual(
        fromContext(fn(node)),
        [{ idempotencyKey: 'K1', round: 1 }, { idempotencyKey: 'K2' }],
        'null / 空 key / 缺 key 的条目全部过滤',
    );
});

test('toast writes #toast, shows it, hides it after the 1800ms timer, and re-arming cancels the old timer', () => {
    const { ctx, harness } = loadCore();
    const toast = globalFunction(ctx, 'toast');
    const el = harness.elementsById.get('toast');

    toast('saved');
    assert.equal(el.textContent, 'saved', '文案写进真实 #toast 元素');
    assert.equal(el.classList.contains('show'), true, '加 show 类');
    let pending = harness.timers.filter((t) => !t.cancelled && !t.fired);
    assert.equal(pending.length, 1, '恰好一个待触发计时器');
    assert.equal(pending[0].ms, 1800, '延时精确 1800ms');

    toast('updated');
    assert.equal(el.textContent, 'updated', '连发覆盖文案');
    assert.equal(pending[0].cancelled, true, '第一次的计时器被 clearTimeout 取消');
    pending = harness.timers.filter((t) => !t.cancelled && !t.fired);
    assert.equal(pending.length, 1, '只留最新的计时器');

    harness.advanceTimers();
    assert.equal(el.classList.contains('show'), false, '计时到期后移除 show 类');
});

test('tr falls back to the raw key without StudioI18n and delegates when it is loaded', () => {
    const { ctx } = loadCore();
    const tr = globalFunction(ctx, 'tr');
    assert.equal(tr('missing.i18n.key'), 'missing.i18n.key', '未加载 StudioI18n → 原样返回 key');
    ctx.StudioI18n = { t: (k) => k.toUpperCase() };
    assert.equal(tr('hello'), 'HELLO', '加载后委托给 StudioI18n.t');
    ctx.StudioI18n = {};
    assert.equal(tr('again'), 'again', 'StudioI18n 存在但 t 缺失（falsy）→ 仍回退 key');
});

test('pending-action registry: string-keyed, function-guarded registration (#197 clear-on-send ground)', () => {
    const { ctx } = loadCore();
    const registerRebuilder = globalFunction(ctx, 'registerSmartPendingActionRebuilder');
    const rebuilderFor = globalFunction(ctx, 'smartPendingActionRebuilderFor');
    const registerPersister = globalFunction(ctx, 'registerSmartPendingActionPersister');
    const persister = globalFunction(ctx, 'smartPendingActionPersister');
    const payload = globalFunction(ctx, 'escapeHtml'); // 用 realm 内真函数做载荷

    assert.equal(persister(), null, '初始 persister 为 null');
    assert.equal(rebuilderFor('gen'), null, '初始无任何 rebuilder');

    registerRebuilder('gen', payload);
    assert.equal(rebuilderFor('gen'), payload, '注册后按 kind 取回同一函数');
    registerRebuilder(7, payload);
    assert.equal(rebuilderFor('7'), payload, 'kind 按 String(kind) 归一（7 ≡ "7"）');
    registerRebuilder(null, payload);
    registerRebuilder('bad', 'not-a-function');
    assert.equal(rebuilderFor('bad'), null, 'kind 为假值或 rebuild 非函数 → 不注册');
    assert.equal(rebuilderFor(''), null, '空 kind 查无');

    registerPersister(payload);
    assert.equal(persister(), payload, 'persister 注册后可取回');
    registerPersister('nope');
    assert.equal(persister(), payload, '非函数不覆盖已有 persister');
});
