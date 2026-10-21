// #199 轨道 4：canvas-image-editor.js 独立单测 —— 只测纯计算核心。
//
// 实测规模：2301 行 / 139 个顶层 function；其中 65 个顶层函数的自身函数体不碰 DOM/canvas。
// 本文件覆盖这 65 个里的纯计算/参数整形切片（窄而实，不是全覆盖）：
//   - 扩图参数整形：validOutpaintSize / withOutpaintDisplaySettings / stripOutpaintDisplaySettings /
//     applyOutpaintSizeToSmartParams / exceedsFourKStandard / textItemFont
//   - 裁剪/扩图数学：clampCrop / clampOutpaint / fitCropRectToAspect / cropRatioFromPreset /
//     clampAspectCropToBounds / resizeCropFromDrag / resizeOutpaintFromDrag（×2 居中回写 +
//     预设比例保持中心）/ clampImageResizeScale / imageResizeDimensions
//   - 宫格布局数学：groupImageGridLayout（「完美网格优先」注释 → 断言）/ gridJoinAutoDims /
//     gridJoinNaturalSize / gridJoinBaseCellSize / gridJoinItemDisplaySize / gridJoinVisualOrder /
//     gridJoinDragTarget（inside 优先 / 0.55 score 阈值 / 排除自己）/ gridSplitRects /
//     gridLayoutFromRects / gridJoinCanvasSize
//   - 文本项与绘图历史栈（模块内状态）：editTextHasContent / selectedEditTextItem /
//     createEditTextItem / measureEditTextItem / setBrushTool / pushEditDrawHistory
//     （EDIT_DRAW_HISTORY_MAX = 40 截断）/ undoEditDrawing / redoEditDrawing 的栈转移语义 /
//     resetEditDrawingHistory
//
// ADR 0020 判据：只测纯计算/参数整形核心，不造假 canvas 2D context / ImageData 去覆盖渲染路径
// —— 那会变成「第二套假核心」，假核心与真核心行为不一致时测试会骗人。下面的假 2D context
// 只用作历史栈快照的像素容器（存/还原语义），不断言任何渲染输出。
//
// 真实模块（加载，不桩）：
//   i18n-core.js        → window.StudioI18n（默认 lang() === 'zh' → 默认文本「双击编辑」；
//                         tr(key) 无词条 → 返回 key）
//   outpaint-ratios.js  → window.OutpaintRatios（parsePreset/fitContaining 真实实现；
//                         resizeOutpaintFromDrag 的预设分支走真实 parsePreset('16:9') → {16,9}）
//   canvas-node-types.js → NODE_TYPE_*
//   canvas-core.js      → uid / tr / toast / settings(let) / isSmartImageNode / isSmartGroupNode /
//                         cloneSmartSettings（stripOutpaintDisplaySettings /
//                         applyOutpaintSizeToSmartParams 走真实 clone，不自己写 clone）
//   canvas-state.js     → canvasState
//
// 桩清单（全部是不可独立加载的宿主脚本 smart-canvas.js 里的名字）——预算 0 逻辑复制：
//   零逻辑/数据驱动记录器（断言「确实调了它 + 传了什么」，不复制它的产品逻辑；
//   上一轮 WIP 的 1 个红用例就是在桩里镜像实现了 gcd 化简——ADR 禁止的「第二套实现」）：
//     canvasCore.reducedAspectRatio → 返回 D.reducedRatio（桩值）；validOutpaintSize 白名单
//       不匹配时断言「调用了它且传的是取整后的 w/h」，而不是断言化简结果。
//       （白名单正则 /^\d+\s*:\s*\d+$/ 是模块自己的逻辑——直接断言，如 ' 4 : 5 ' → '4:5'。）
//     normalizeSmartOnlineEngine → D.engine；isApiLikeEngine → D.apiLike（测试喂布尔，
//       不抄 smart-canvas.js:575-577 的 ['api','volcengine'].includes 判定）。
//     nearestFourKSizeFor → D.fourK；currentEditImage → D.currentEdit；
//     activeSettingsSubject → D.subject；canvasCore.smartSettingsForNode → D.smartSettings；
//     renderDynamicParams → 只记 calls。
//   值常量：MEDIA_GROUP_MAX_VISIBLE_ROWS = 3（smart-canvas.js:1034 的值；groupImageGridLayout
//     默认参数经 canvasCore 惰性 Proxy 按裸名解析到这里）。
//
// 跨 realm 约定（vm 是独立 V8 realm）：
//   - 被测函数返回的对象先 fromContext(...)（JSON 往返）再 deepEqual——上一轮 WIP 的红直接
//     就是 deepStrictEqual 对跨 realm 对象报 "Values have same structure but are not
//     reference-equal (different realms)"；
//   - 模块内 let 状态（editTextItems/cropState/gridJoinLayout/…）用 evaluate(ctx, '…') 读写
//     （返回原始值，无跨 realm 问题）；
//   - 可能为 undefined 的结果用裸 assert.equal（fromContext 对 undefined 抛 SyntaxError）。
//
// 盖不住清单（不是「永远绿的假断言」，是预算/边界外的真实路径——由浏览器 runner 兜底）：
//   - applyImageCrop / applyImageOutpaint / applyImageMask / applyImageBrush /
//     applyImageResize / applyImageGridSplit / applyImageGridJoin：canvas 2D 合成 +
//     toBlob + fetch('/api/ai/upload') 上传链（harness 的 createElement 产物无 toBlob，
//     fetch 默认禁用）。
//   - 渲染/DOM/canvas 像素路径：renderGridJoinPreview / renderCropBox / renderEditTextCanvas /
//     normalizeMaskPreviewCanvas / strokeFreeDrawPoint / drawBrushShape 等——像素输出断言
//     需要真实 canvas 2D context，本文件不造假 context 去覆盖它们。
//   - 指针拖拽链：beginGridJoinDrag / moveGridJoinDrag / beginCropDrag / beginEditDraw 等
//     （setPointerCapture / host.querySelector + CSS.escape）；纯几何的 gridJoinDragTarget
//     已单独覆盖。
//   - 编辑器开/关全流程：openImageEditor / closeImageEditor（modal/快图/兜底链/定时器/
//     video 分支）。
//
// 有效性证据：4 处互相独立的变异（worktree 内改产品行为，红绿原文见 #199 评论与
// C:/Users/Administrator/fix199g-evidence/）。

import assert from 'node:assert/strict';
import test from 'node:test';
import { createCanvasContext, loadModules, globalFunction, evaluate, fromContext } from './harness.mjs';

// 假 2D 上下文：最小像素容器（历史栈快照需要真实可读写的 getImageData/putImageData）。
// 只用于状态存/还原，不用于断言渲染。measureText 只返回 {width}（不返回
// actualBoundingBox*）——默认走 measureEditTextItem 的回退分支（ascent=size*0.8）。
function makeFakeCtx2d() {
    const rec = [];
    let pixels = null;
    const syncPixels = (w, h) => {
        const len = Math.max(0, w * h * 4);
        if (!pixels || pixels.length !== len) pixels = new Uint8ClampedArray(len);
    };
    return {
        calls: rec,
        font: '', textAlign: 'left', textBaseline: 'alphabetic',
        fillStyle: '', strokeStyle: '', lineWidth: 1,
        globalCompositeOperation: 'source-over',
        save() { rec.push(['save']); },
        restore() { rec.push(['restore']); },
        beginPath() { rec.push(['beginPath']); },
        closePath() { rec.push(['closePath']); },
        moveTo() { rec.push(['moveTo']); },
        lineTo() { rec.push(['lineTo']); },
        arc() { rec.push(['arc']); },
        ellipse() { rec.push(['ellipse']); },
        fill() { rec.push(['fill']); },
        stroke() { rec.push(['stroke']); },
        fillText(t) { rec.push(['fillText', t]); },
        strokeText(t) { rec.push(['strokeText', t]); },
        strokeRect() { rec.push(['strokeRect']); },
        fillRect() { rec.push(['fillRect']); },
        clearRect() { rec.push(['clearRect']); pixels = null; },
        setLineDash() { rec.push(['setLineDash']); },
        measureText(text) { return { width: Math.max(1, String(text ?? '').length * 10) }; },
        getImageData(x, y, w, h) {
            syncPixels(Math.max(0, Math.round(Number(w) || 0)), Math.max(0, Math.round(Number(h) || 0)));
            rec.push(['getImageData', x, y]);
            return { data: pixels, width: w, height: h };
        },
        putImageData(data, x, y) {
            if (data && data.data) pixels = new Uint8ClampedArray(data.data);
            rec.push(['putImageData', x, y]);
        },
        createImageData(w, h) { return { data: new Uint8ClampedArray(Math.max(0, w * h * 4)), width: w, height: h }; },
        // 测试侧像素控制（纯容器操作，不是产品逻辑）。
        setPixels(arr) { pixels = new Uint8ClampedArray(arr); },
        getPixels() { return pixels; },
    };
}

// 加载真实底层模块 + 被测模块。imageEditModal 是本模块顶层 const（加载期绑定 DOM），
// 必须在 loadModules 前注册（注册后为真实元素，不是桩）。
function loadImageEditorModule() {
    const { ctx, harness } = createCanvasContext();
    const calls = [];

    // --- 值/浏览器 API（非产品逻辑） ---
    ctx.innerWidth = 1920;
    ctx.innerHeight = 1080;

    // --- DOM（harness：getElementById 找不到返回 null，被测路径用到的元素必须显式注册） ---
    const E = (id, tag = 'div') => harness.element(id, tag);
    E('imageEditModal');                       // 顶层 const，加载期绑定
    E('cropImage', 'img');                     // cropBounds/imageResizeDimensions 的输入
    E('cropCanvas');                           // syncTextToolState/syncBrushToolButtons
    const editDraw = E('editDrawCanvas', 'canvas');
    const editText = E('editTextCanvas', 'canvas');
    editDraw.width = 4; editDraw.height = 2;   // 像素容器 4×2 = 32 字节
    editText.width = 4; editText.height = 2;
    const editDrawCtx = makeFakeCtx2d();
    const editTextCtx = makeFakeCtx2d();
    editDraw.getContext = () => editDrawCtx;
    editText.getContext = () => editTextCtx;
    E('maskBrushSize', 'input').value = '20';
    E('paintBrushSize', 'input').value = '20';
    E('paintBrushColor', 'input').value = '#33ff99';
    for (const id of ['maskUndoBtn', 'brushUndoBtn', 'maskRedoBtn', 'brushRedoBtn']) E(id, 'button');
    E('gridGapSize', 'input').value = '8';
    E('gridGapValue');
    E('gridHorizontalLines', 'input').value = '1';
    E('gridVerticalLines', 'input').value = '1';
    const brushBtns = ['free', 'text', 'label'].map((t) => {
        const b = harness.makeElement('button');
        b.dataset.brushTool = t;
        return b;
    });
    harness.querySelectorAll('[data-brush-tool]', brushBtns);

    // classList.toggle 第二参 force（harness makeElement 不支持，逐实例覆写——
    // 同 test-canvas-connections 先例；syncBrushToolButtons/syncTextToolState 用 force 语义）。
    for (const el of [...harness.elementsById.values(), ...brushBtns]) {
        const classes = new Set();
        el.classList = {
            add: (...cs) => cs.forEach((c) => classes.add(String(c))),
            remove: (...cs) => cs.forEach((c) => classes.delete(String(c))),
            contains: (c) => classes.has(String(c)),
            toggle: (c, force) => {
                const s = String(c);
                const want = force === undefined ? !classes.has(s) : Boolean(force);
                if (want) classes.add(s); else classes.delete(s);
                return want;
            },
            toString: () => [...classes].join(' '),
        };
    }

    // --- 零逻辑/数据驱动记录器（smart-canvas.js 的名字；测试显式喂值） ---
    const D = (harness.data = {
        reducedRatio: '',        // canvasCore.reducedAspectRatio(w,h) 的桩返回值
        engine: 'api',           // normalizeSmartOnlineEngine(v) 的桩返回值
        apiLike: false,          // canvasCore.isApiLikeEngine(v) 的桩返回值（布尔）
        fourK: null,             // nearestFourKSizeFor(w,h) 的桩返回值
        currentEdit: { node: null, index: 0, image: null },  // currentEditImage()
        subject: null,           // activeSettingsSubject()
        smartSettings: null,     // canvasCore.smartSettingsForNode(node)
    });
    ctx.reducedAspectRatio = (w, h) => { calls.push(['reducedAspectRatio', w, h]); return D.reducedRatio; };
    ctx.normalizeSmartOnlineEngine = (v) => { calls.push(['normalizeSmartOnlineEngine', v]); return D.engine; };
    ctx.isApiLikeEngine = (v) => { calls.push(['isApiLikeEngine', v]); return D.apiLike; };
    ctx.nearestFourKSizeFor = (w, h) => { calls.push(['nearestFourKSizeFor', w, h]); return D.fourK; };
    ctx.currentEditImage = () => D.currentEdit;
    ctx.activeSettingsSubject = () => D.subject;
    ctx.smartSettingsForNode = () => D.smartSettings;
    ctx.renderDynamicParams = () => { calls.push(['renderDynamicParams']); };
    ctx.MEDIA_GROUP_MAX_VISIBLE_ROWS = 3; // smart-canvas.js:1034 的值（非逻辑）

    loadModules(ctx, [
        'i18n-core.js',
        'outpaint-ratios.js',
        'canvas-node-types.js',
        'canvas-core.js',
        'canvas-state.js',
        'canvas-image-editor.js',
    ]);

    // canvas 是 canvas-core.js 的词法 let 绑定，用 evaluate 赋值（本测试不用它，
    // 只为排除加载期/运行期的意外引用）。
    evaluate(ctx, 'canvas = {nodes: [], connections: [], markedAssetIds: []}');

    return { ctx, harness, calls, D, E, brushBtns, editDrawCtx, editTextCtx };
}

// ---------- 模块加载 ----------

test('loads canvas-image-editor.js standalone: target functions + real modules in place', () => {
    const { ctx } = loadImageEditorModule();
    for (const name of [
        // 被测目标函数
        'validOutpaintSize', 'withOutpaintDisplaySettings', 'stripOutpaintDisplaySettings',
        'applyOutpaintSizeToSmartParams', 'exceedsFourKStandard', 'textItemFont',
        'clampCrop', 'clampOutpaint', 'fitCropRectToAspect', 'cropRatioFromPreset',
        'clampAspectCropToBounds', 'resizeCropFromDrag', 'resizeOutpaintFromDrag',
        'clampImageResizeScale', 'imageResizeDimensions',
        'groupImageGridLayout', 'gridJoinAutoDims', 'gridJoinNaturalSize',
        'gridJoinBaseCellSize', 'gridJoinItemDisplaySize', 'gridJoinVisualOrder',
        'gridJoinDragTarget', 'gridSplitRects', 'gridLayoutFromRects', 'gridJoinCanvasSize',
        'editTextHasContent', 'selectedEditTextItem', 'createEditTextItem',
        'measureEditTextItem', 'setBrushTool', 'pushEditDrawHistory',
        'undoEditDrawing', 'redoEditDrawing', 'resetEditDrawingHistory',
        // 结构性锚点
        'smartImageEditorIsOpen', 'editDrawCanvas', 'editTextCanvas', 'editTextContext',
        'defaultEditTextText', 'beginTextEditChange', 'cropBounds', 'jimengImageEditMode',
    ]) globalFunction(ctx, name);
    // 真实底层模块就位。
    assert.equal(typeof evaluate(ctx, 'canvasState.nodes'), 'object', 'canvasState 来自真实 canvas-state.js');
    assert.equal(evaluate(ctx, "settings.provider_id"), '', 'settings 来自真实 canvas-core.js 默认值');
    assert.equal(evaluate(ctx, "typeof window.StudioI18n.lang"), 'function', 'StudioI18n 来自真实 i18n-core.js');
    assert.equal(evaluate(ctx, 'window.StudioI18n.lang()'), 'zh', '默认语言 zh（localStorage 空）');
    assert.equal(typeof evaluate(ctx, 'window.OutpaintRatios.fitContaining'), 'function', 'OutpaintRatios 来自真实 outpaint-ratios.js');
    assert.deepEqual(fromContext(evaluate(ctx, "window.OutpaintRatios.parsePreset('16:9')")),
        { width: 16, height: 9, ratio: 16 / 9 }, 'parsePreset 真实 gcd 化简（不是桩）');
    assert.equal(evaluate(ctx, "typeof isSmartImageNode"), 'function', 'isSmartImageNode 来自真实 canvas-core.js');
    assert.equal(evaluate(ctx, "typeof cloneSmartSettings"), 'function', 'cloneSmartSettings 来自真实 canvas-core.js');
    // tr 无词条返回 key（i18n-core 真实行为）——默认文本断言的基础。
    assert.equal(evaluate(ctx, "tr('smart.x')"), 'smart.x');
    assert.equal(evaluate(ctx, 'imageEditModal && imageEditModal.tagName'), 'DIV', '顶层 const imageEditModal 已绑定（加载前注册）');
});

// ---------- 扩图参数整形 ----------

test('validOutpaintSize: null 门 + 比例白名单（\d+:\d+ 去空格保留，否则走 canvasCore.reducedAspectRatio）', () => {
    const { ctx, D, calls } = loadImageEditorModule();
    const f = globalFunction(ctx, 'validOutpaintSize');

    assert.equal(f(null), null, '无节点 → null');
    assert.equal(f({}), null, '无 outpaintSize → null');
    assert.equal(f({ outpaintSize: { width: 0, height: 800 } }), null, 'width<=0 → null');
    assert.equal(f({ outpaintSize: { width: 800, height: -1 } }), null, 'height<=0 → null');

    // 字符串尺寸 Number 化；无 aspectRatio → 走 reducedAspectRatio（断言「调了它 + 取整后的 w/h」，不断言化简结果）。
    D.reducedRatio = '3:4';
    assert.deepEqual(fromContext(f({ outpaintSize: { width: '1200', height: '1600' } })),
        { width: 1200, height: 1600, aspectRatio: '3:4' });
    assert.ok(calls.some(c => c[0] === 'reducedAspectRatio' && c[1] === 1200 && c[2] === 1600),
        'reducedAspectRatio 收到取整后的 1200/1600（桩值 3:4 透传）');

    // 白名单是模块自己的逻辑：带空格的 ' 4 : 5 ' → '4:5'，不再调 reducedAspectRatio。
    D.reducedRatio = '9:9';
    const before = calls.filter(c => c[0] === 'reducedAspectRatio').length;
    assert.deepEqual(fromContext(f({ outpaintSize: { width: 1200, height: 1600, aspectRatio: ' 4 : 5 ' } })),
        { width: 1200, height: 1600, aspectRatio: '4:5' });
    assert.equal(calls.filter(c => c[0] === 'reducedAspectRatio').length, before, '白名单命中不调 reducedAspectRatio');

    // 非比例字符串 + 小数尺寸 → 取整后走桩（1600.6 → 1601）。
    D.reducedRatio = '3:4';
    assert.deepEqual(fromContext(f({ outpaintSize: { width: 1200.4, height: 1600.6, aspectRatio: 'square' } })),
        { width: 1200, height: 1601, aspectRatio: '3:4' });
    assert.ok(calls.some(c => c[0] === 'reducedAspectRatio' && c[1] === 1200 && c[2] === 1601));
});

test('withOutpaintDisplaySettings: 无扩图尺寸返回原引用；有则 ratio:custom + customRatio + outpaintResolutionLocked（不设 resolution）+ 引擎分支', () => {
    const { ctx, D } = loadImageEditorModule();
    const f = globalFunction(ctx, 'withOutpaintDisplaySettings');

    const base = { provider: 'p', resolution: '2k', model: 'm' };
    assert.equal(f({}, base), base, '无 outpaintSize → 原引用返回');

    // api 类引擎：apiKind='image'，resolution 预设保持 '2k'（源码注释：扩图不设 resolution:custom）。
    D.engine = 'api'; D.apiLike = true;
    const node = { outpaintSize: { width: 1200, height: 900, aspectRatio: '4:3' } };
    assert.deepEqual(fromContext(f(node, base)),
        { provider: 'p', resolution: '2k', model: 'm',
          ratio: 'custom', customRatio: '4:3', outpaintResolutionLocked: true, apiKind: 'image' });

    // modelscope：ms 双字段，无 apiKind（apiLike 桩为 false）。
    D.engine = 'modelscope'; D.apiLike = false;
    assert.deepEqual(fromContext(f(node, base)),
        { provider: 'p', resolution: '2k', model: 'm',
          ratio: 'custom', customRatio: '4:3', outpaintResolutionLocked: true,
          msRatio: 'custom', msCustomRatio: '4:3' });
});

test('stripOutpaintDisplaySettings: customSize 匹配重置 1k + w/h→1024 + ms 分支 + 真实 cloneSmartSettings 隔离', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'stripOutpaintDisplaySettings');
    const node = { outpaintSize: { width: 1200, height: 900, aspectRatio: '4:3' } };

    // 匹配重置 + outpaintResolutionLocked 删除 + 无关字段保留。
    const base = { resolution: 'custom', customSize: '1200x900', ratio: '16:9',
        width: 1200, height: 900, outpaintResolutionLocked: true, extra: 'keep' };
    assert.deepEqual(fromContext(f(base, node)),
        { resolution: '1k', ratio: '16:9', width: 1024, height: 1024,
          customSize: '', customWidth: '', customHeight: '', extra: 'keep' });

    // ms 分支。
    assert.deepEqual(fromContext(f({ msResolution: 'custom', msCustomSize: '1200x900', msRatio: '3:4' }, node)),
        { msResolution: '1k', msRatio: '3:4', msCustomSize: '', msCustomWidth: '', msCustomHeight: '' });

    // 不匹配 → 原样（除 outpaintResolutionLocked）。
    assert.deepEqual(fromContext(f({ resolution: 'custom', customSize: '800x600' }, node)),
        { resolution: 'custom', customSize: '800x600' });
    // w/h 只匹配一边 → 不重置。
    assert.deepEqual(fromContext(f({ width: 1200, height: 600 }, node)), { width: 1200, height: 600 });
    // node=null → 只删 outpaintResolutionLocked。
    assert.deepEqual(fromContext(f({ resolution: 'custom', outpaintResolutionLocked: true }, null)),
        { resolution: 'custom' });

    // 真实 cloneSmartSettings：入参对象不被原地修改。
    const orig = { resolution: 'custom', customSize: '1200x900', width: 1200, height: 900 };
    f(orig, node);
    assert.equal(orig.resolution, 'custom', '入参未被原地改');
    assert.equal(orig.width, 1200);
});

test('applyOutpaintSizeToSmartParams: isSmartImageNode 门 + 白名单/桩双路 + 真实 clone 隔离 + activeSettingsSubject 写回', () => {
    const { ctx, D, calls } = loadImageEditorModule();
    const f = globalFunction(ctx, 'applyOutpaintSizeToSmartParams');
    evaluate(ctx, "outpaintAspectPreset = '4:3'");
    D.apiLike = false;

    // 门：无节点 → no-op。
    const ghost = { id: 'g' };
    D.currentEdit = { node: null, index: 0, image: null };
    f(1200, 900);
    assert.equal(ghost.outpaintSize, undefined, '无节点不改任何对象');

    // 门：非图片节点 → no-op。
    const prompt = { type: 'smart-prompt', id: 'n3' };
    D.currentEdit = { node: prompt, index: 0, image: null };
    f(1200, 900);
    assert.equal(prompt.outpaintSize, undefined, '非图片节点不改 outpaintSize');

    // 正常路径：outpaintSize 白名单比例 + runSettings 走真实 clone + withOutpaint。
    const subject = { type: 'smart-image', id: 'n1' };
    const origRun = { provider: 'p', resolution: '1k', model: 'm' };
    subject.runSettings = origRun;
    D.currentEdit = { node: subject, index: 0, image: {} };
    D.subject = null;
    const reducedBefore = calls.filter(c => c[0] === 'reducedAspectRatio').length;
    f(1200, 900);
    assert.deepEqual(fromContext(subject.outpaintSize), { width: 1200, height: 900, aspectRatio: '4:3' });
    assert.deepEqual(fromContext(subject.runSettings),
        { provider: 'p', resolution: '1k', model: 'm',
          ratio: 'custom', customRatio: '4:3', outpaintResolutionLocked: true });
    assert.equal(calls.filter(c => c[0] === 'reducedAspectRatio').length, reducedBefore, '白名单命中不走桩');
    assert.equal(calls.filter(c => c[0] === 'renderDynamicParams').length, 0, '非活动 subject 不触发重渲染');
    assert.equal(evaluate(ctx, 'settings.provider_id'), '', 'settings 未被写回');
    assert.equal(origRun.resolution, '1k', '真实 cloneSmartSettings：原 runSettings 对象隔离');
    assert.equal('ratio' in origRun, false, '原对象无新增字段');

    // reducedAspectRatio 路径：预设 'free' + 小数入参 → 取整传桩。
    evaluate(ctx, "outpaintAspectPreset = 'free'");
    D.reducedRatio = '3:2';
    const subject2 = { type: 'smart-image', id: 'n2', runSettings: {} };
    D.currentEdit = { node: subject2, index: 0, image: {} };
    f(1200.4, 899.6);
    assert.deepEqual(fromContext(subject2.outpaintSize), { width: 1200, height: 900, aspectRatio: '3:2' });
    assert.ok(calls.some(c => c[0] === 'reducedAspectRatio' && c[1] === 1200 && c[2] === 900));

    // activeSettingsSubject 命中：settings 写回（桩值）+ renderDynamicParams。
    evaluate(ctx, "outpaintAspectPreset = '4:3'");
    const subject3 = { type: 'smart-image', id: 'n3', runSettings: {} };
    D.currentEdit = { node: subject3, index: 0, image: {} };
    D.subject = { id: 'n3' };
    D.smartSettings = { provider: 'pp', ratio: 'custom' };
    f(1024, 768);
    assert.deepEqual(fromContext(evaluate(ctx, 'settings')), { provider: 'pp', ratio: 'custom' }, 'settings 被写回为 smartSettingsForNode 桩值');
    assert.ok(calls.some(c => c[0] === 'renderDynamicParams'), '活动 subject → renderDynamicParams');

    // modelscope 分支透传（withOutpaint 的 ms 字段）。
    const subject4 = { type: 'smart-image', id: 'n4', runSettings: { res: '2k' } };
    D.currentEdit = { node: subject4, index: 0, image: {} };
    D.subject = null;
    D.engine = 'modelscope'; D.apiLike = false;
    f(1600, 1200);
    const rs = fromContext(subject4.runSettings);
    assert.equal(rs.msRatio, 'custom');
    assert.equal(rs.msCustomRatio, '4:3');
    assert.equal(rs.apiKind, undefined, '非 api 类引擎无 apiKind');
    assert.equal(rs.res, '2k', '原有 runSettings 字段保留');

    // 钉现状：width/height 缺失时 Math.max(1, …) 兜底 1（源码当前行为，非理想值）。
    const subject5 = { type: 'smart-image', id: 'n5', runSettings: {} };
    D.currentEdit = { node: subject5, index: 0, image: {} };
    f('abc', 'xyz');
    assert.deepEqual(fromContext(subject5.outpaintSize), { width: 1, height: 1, aspectRatio: '4:3' }, '钉现状：非法尺寸 → 1×1');
});

test('exceedsFourKStandard: nearestFourKSizeFor 桩值比较（null → false；任一边超出 → true）', () => {
    const { ctx, D, calls } = loadImageEditorModule();
    const f = globalFunction(ctx, 'exceedsFourKStandard');

    D.fourK = null;
    assert.equal(f(99999, 99999), false, '无 4K 标准 → false');

    D.fourK = { width: 3840, height: 2160 };
    assert.equal(f(3840, 2160), false, '恰好等于标准 → false');
    assert.equal(f(3841, 2160), true, '宽超出 → true');
    assert.equal(f(3840, 2161), true, '高超出 → true');
    assert.equal(f(100, 100), false);
    assert.ok(calls.some(c => c[0] === 'nearestFourKSizeFor' && c[1] === 3841 && c[2] === 2160), '传原始 w/h 给桩');
});

test('textItemFont: size 钳制 10–120 + 默认 28 + 字符串 size', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'textItemFont');
    assert.equal(f({ size: 28 }), '900 28px Arial, sans-serif');
    assert.equal(f({ size: 5 }), '900 10px Arial, sans-serif', '下限 10');
    assert.equal(f({ size: 500 }), '900 120px Arial, sans-serif', '上限 120');
    assert.equal(f(null), '900 28px Arial, sans-serif', '默认 28');
    assert.equal(f({ size: '32' }), '900 32px Arial, sans-serif', '字符串 size');
});
