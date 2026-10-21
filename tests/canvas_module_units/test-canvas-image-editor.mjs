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

// ---------- 裁剪 / 扩图 / 缩放数学 ----------

test('clampCrop: 无尺寸用 clientWidth/Height；x/y/w/h 双侧钳制（先收 w/h 再收 x/y）', () => {
    const { ctx, harness } = loadImageEditorModule();
    const img = harness.elementsById.get('cropImage');
    img.clientWidth = 300; img.clientHeight = 200;
    const f = globalFunction(ctx, 'clampCrop');
    const read = () => fromContext(evaluate(ctx, 'cropState'));

    evaluate(ctx, 'cropState = null');
    f();
    assert.equal(evaluate(ctx, 'cropState'), null, 'cropState null → no-op');

    // 无 cropImageSize：越界左上角 → 收进 0。
    evaluate(ctx, 'cropState = {x:-5, y:-5, w:100, h:50}');
    f();
    assert.deepEqual(read(), { x: 0, y: 0, w: 100, h: 50 });

    // 有 cropImageSize：右下越界 → x/y 收到 300-w / 200-h。
    evaluate(ctx, 'cropImageSize = {w:300, h:200}; cropState = {x:250, y:150, w:100, h:50}');
    f();
    assert.deepEqual(read(), { x: 200, y: 150, w: 100, h: 50 });

    // 框比画布大 → w/h 先被收小，x/y 再按新 w/h 收。
    evaluate(ctx, 'cropState = {x:250, y:150, w:400, h:300}');
    f();
    assert.deepEqual(read(), { x: 0, y: 0, w: 300, h: 200 });

    // 最小 24（钉现状：不是 0）。
    evaluate(ctx, 'cropState = {x:0, y:0, w:5, h:5}');
    f();
    assert.deepEqual(read(), { x: 0, y: 0, w: 24, h: 24 });

    // outpaint 模式 → 委托 clampOutpaint（框必须包住原图）。
    evaluate(ctx, 'imageEditMode = "outpaint"; cropState = {x:0, y:0, w:100, h:50}');
    f();
    assert.deepEqual(read(), { x: 0, y: 0, w: 300, h: 200 }, '委托 clampOutpaint：扩到包住 300×200 原图');
});

test('clampOutpaint: 框必须包住原图（w≥源宽, h≥源高, x∈[0,w-源宽], y∈[0,h-源高]）', () => {
    const { ctx, harness } = loadImageEditorModule();
    const img = harness.elementsById.get('cropImage');
    img.clientWidth = 300; img.clientHeight = 200;
    const f = globalFunction(ctx, 'clampOutpaint');
    const read = () => fromContext(evaluate(ctx, 'cropState'));

    evaluate(ctx, 'cropState = null');
    f();
    assert.equal(evaluate(ctx, 'cropState'), null, 'cropState null → no-op');

    // 框小于原图 → 扩到原图尺寸，x/y 归 0。
    evaluate(ctx, 'cropState = {x:-100, y:-50, w:80, h:30}');
    f();
    assert.deepEqual(read(), { x: 0, y: 0, w: 300, h: 200 });

    // 框大于原图且位置合法 → 原样。
    evaluate(ctx, 'cropState = {x:100, y:50, w:500, h:400}');
    f();
    assert.deepEqual(read(), { x: 100, y: 50, w: 500, h: 400 });

    // x/y 越出「包住原图」允许区 → 收到边界。
    evaluate(ctx, 'cropState = {x:400, y:300, w:500, h:400}');
    f();
    assert.deepEqual(read(), { x: 200, y: 200, w: 500, h: 400 });

    // 恰好等于原图 → 原样。
    evaluate(ctx, 'cropState = {x:0, y:0, w:300, h:200}');
    f();
    assert.deepEqual(read(), { x: 0, y: 0, w: 300, h: 200 });
});

test('fitCropRectToAspect(ratio, sourceRect): min 24 + ratio fit + bounds + 中心取整 + clampCrop', () => {
    const { ctx, harness } = loadImageEditorModule();
    const img = harness.elementsById.get('cropImage');
    img.clientWidth = 300; img.clientHeight = 200;
    const f = globalFunction(ctx, 'fitCropRectToAspect');
    const read = () => fromContext(evaluate(ctx, 'cropState'));

    evaluate(ctx, 'cropState = {x:0, y:0, w:1, h:1}');

    // rect 100×50, ratio 4/3：2 > 4/3 → w=50*4/3=66.67；中心用原 rect.w/h (100,65) → round → {67,40,67,50}。
    f(4 / 3, { x: 50, y: 40, w: 100, h: 50 });
    assert.deepEqual(read(), { x: 67, y: 40, w: 67, h: 50 }, '中心 (100,65) 保持；x=round(100-33.5)=67');

    // rect 100×150：0.667 ≤ 4/3 → h=100*3/4=75；中心 (50,75) → y=round(75-37.5)=38。
    f(4 / 3, { x: 0, y: 0, w: 100, h: 150 });
    assert.deepEqual(read(), { x: 0, y: 38, w: 100, h: 75 });

    // 越界：rect {200,150,200,100} ratio 1 → 中心 (300,200) → x=250,y=150 → clampCrop 收边。
    f(1, { x: 200, y: 150, w: 200, h: 100 });
    assert.deepEqual(read(), { x: 200, y: 100, w: 100, h: 100 }, 'clampCrop 收回界内');

    // ratio 0（free）：w/h 各收到 bounds；中心 (250,250) → clamp 后 {0,0,300,200}。
    f(0, { x: 0, y: 0, w: 500, h: 500 });
    assert.deepEqual(read(), { x: 0, y: 0, w: 300, h: 200 });

    // min 24：rect 5×5 free → 24×24；x/y=round(2.5-12)=-9 → 0。
    f(0, { x: 0, y: 0, w: 5, h: 5 });
    assert.deepEqual(read(), { x: 0, y: 0, w: 24, h: 24 });

    // sourceRect null → 用 cropState（同第一例 rect）。
    evaluate(ctx, 'cropState = {x:50, y:40, w:100, h:50}');
    f(4 / 3);
    assert.deepEqual(read(), { x: 67, y: 40, w: 67, h: 50 });
});

test('cropRatioFromPreset: "w:h" 解析；free/source/非法 → null（source = cropBounds 比例）', () => {
    const { ctx, harness } = loadImageEditorModule();
    const img = harness.elementsById.get('cropImage');
    img.clientWidth = 300; img.clientHeight = 200;
    const f = globalFunction(ctx, 'cropRatioFromPreset');
    assert.equal(f('16:9'), 16 / 9);
    assert.equal(f('1:1'), 1);
    assert.equal(f('3:4'), 0.75);
    assert.equal(f('source'), 1.5, 'cropBounds 300×200 → 300/200');
    assert.equal(f('free'), null);
    assert.equal(f(''), null);
    assert.equal(f(null), null);
    assert.equal(f('junk'), null, 'Number("junk")=NaN → null（钉现状）');
    assert.equal(f('0:5'), null, 'parts[0]>0 门');
});

test('clampAspectCropToBounds(anchor, moving, ratio, handle): ratio fit + 按 handle 方向收边（纯函数，不写 cropState）', () => {
    const { ctx, harness } = loadImageEditorModule();
    const img = harness.elementsById.get('cropImage');
    img.clientWidth = 300; img.clientHeight = 200;
    const f = globalFunction(ctx, 'clampAspectCropToBounds');
    evaluate(ctx, 'cropState = {x:9, y:9, w:9, h:9}');

    // se: anchor (100,100) → moving (250,180), 4:3：150×80 → 1.875>4/3 → w=80*4/3；bounds 不生效。
    assert.deepEqual(fromContext(f(100, 100, 250, 180, 4 / 3, 'se')),
        { x: 100, y: 100, w: 80 * (4 / 3), h: 80 });

    // nw: anchor (150,120) → moving (50,30), 1:1：100×90 → w=90 → x=150-90=60, y=120-90=30。
    assert.deepEqual(fromContext(f(150, 120, 50, 30, 1, 'nw')), { x: 60, y: 30, w: 90, h: 90 });

    // e: 宽被画布限制（250→300 只剩 50）→ 重 fit 4:3 → h=37.5。
    assert.deepEqual(fromContext(f(250, 50, 350, 200, 4 / 3, 'e')), { x: 250, y: 50, w: 50, h: 37.5 });

    // min 24：1px 拖拽 → 24×24。
    assert.deepEqual(fromContext(f(100, 100, 101, 101, 1, 'se')), { x: 100, y: 100, w: 24, h: 24 });

    // 纯函数：cropState 未被修改。
    assert.deepEqual(fromContext(evaluate(ctx, 'cropState')), { x: 9, y: 9, w: 9, h: 9 });
});

test('resizeCropFromDrag(dx,dy): 读 cropDrag.start/mode——free（24 下限）/ e-w / n-s / corner→clampAspectCropToBounds', () => {
    const { ctx, harness } = loadImageEditorModule();
    const img = harness.elementsById.get('cropImage');
    img.clientWidth = 300; img.clientHeight = 200;
    const f = globalFunction(ctx, 'resizeCropFromDrag');
    const read = () => fromContext(evaluate(ctx, 'cropState'));
    const drag = (mode, x, y, w, h) => evaluate(ctx,
        `cropState = {x:${x}, y:${y}, w:${w}, h:${h}}; cropDrag = {mode:"${mode}", sx:0, sy:0, start:{...cropState}}`);

    // 无 cropDrag → no-op。
    evaluate(ctx, 'cropState = {x:1, y:2, w:3, h:4}; cropDrag = null');
    f(10, 10);
    assert.deepEqual(read(), { x: 1, y: 2, w: 3, h: 4 });

    // free（ratio 0）：handle "resize" 移右/下边；24 下限。
    evaluate(ctx, 'cropAspectRatio = 0');
    drag('resize', 50, 40, 100, 80);
    f(50, 30);
    assert.deepEqual(read(), { x: 50, y: 40, w: 150, h: 110 }, 'right=200, bottom=150 → w=150, h=110');
    drag('resize', 50, 40, 100, 80);
    f(-80, 0);
    assert.deepEqual(read(), { x: 46, y: 40, w: 24, h: 80 }, 'left(50) > right-24(46) → x 钉 46, w=24');
    drag('crop-w', 50, 40, 100, 80);
    f(-30, 0);
    assert.deepEqual(read(), { x: 20, y: 40, w: 130, h: 80 }, 'w handle：left += dx');

    // ratio 4/3，e handle：width 150 被 maxW/maxH*ratio 钳制；x 钉左、y 中心（round）。
    evaluate(ctx, 'cropAspectRatio = 4/3');
    drag('e', 50, 40, 100, 75);
    f(50, 0);
    assert.deepEqual(read(), { x: 50, y: 21, w: 150, h: 113 }, 'y=round(77.5-56.25)=21, h=round(112.5)=113');
    drag('w', 50, 40, 100, 75);
    f(-50, 0);
    assert.deepEqual(read(), { x: 0, y: 21, w: 150, h: 113 }, 'x = start.x+start.w-width = 0');
    drag('s', 50, 40, 100, 75);
    f(0, 25);
    assert.deepEqual(read(), { x: 33, y: 40, w: 133, h: 100 }, 'h=100 → w=133.33→133；x=round(100-66.67)');

    // corner se → clampAspectCropToBounds 路径。
    drag('se', 50, 40, 100, 75);
    f(50, 25);
    assert.deepEqual(read(), { x: 50, y: 40, w: 133, h: 100 });
});

test('resizeOutpaintFromDrag(dx,dy): 读 cropDrag.start/mode——free ×2 居中回写 / 边拖 / 预设比例走真实 OutpaintRatios.parsePreset', () => {
    const { ctx, harness } = loadImageEditorModule();
    const img = harness.elementsById.get('cropImage');
    img.clientWidth = 300; img.clientHeight = 200;
    const f = globalFunction(ctx, 'resizeOutpaintFromDrag');
    const read = () => fromContext(evaluate(ctx, 'cropState'));
    const drag = (mode, x, y, w, h) => evaluate(ctx,
        `cropState = {x:${x}, y:${y}, w:${w}, h:${h}}; cropDrag = {mode:"${mode}", sx:0, sy:0, start:{...cropState}}`);
    evaluate(ctx, 'imageEditMode = "outpaint"; outpaintAspectPreset = "free"');

    // 无 cropDrag → no-op。
    evaluate(ctx, 'cropState = {x:1, y:2, w:3, h:4}; cropDrag = null');
    f(10, 10);
    assert.deepEqual(read(), { x: 1, y: 2, w: 3, h: 4 });

    // corner free：w/h 各增 2×dx/2×dy，中心平移 (dx,dy)（钉现状），不小于原图。
    drag('outpaint-corner', 0, 0, 300, 200);
    f(50, 30);
    assert.deepEqual(read(), { x: 50, y: 30, w: 400, h: 260 }, '中心 (150,100)→(250,160)；x=start.x+round(Δw/2)');

    // right 边拖：同样 ×2 增长（growX=dx）。
    drag('outpaint-right', 0, 0, 300, 200);
    f(50, 0);
    assert.deepEqual(read(), { x: 50, y: 0, w: 400, h: 200 });

    // bottom 边拖：仅 h 增 2×dy。
    drag('outpaint-bottom', 0, 0, 300, 200);
    f(0, 30);
    assert.deepEqual(read(), { x: 0, y: 30, w: 300, h: 260 });

    // 预设 16:9（真实 OutpaintRatios.parsePreset）：保持中心后按 16:9 重算，clampOutpaint 可能破中心（钉现状）。
    evaluate(ctx, 'outpaintAspectPreset = "16:9"');
    drag('outpaint-corner', 0, 0, 300, 200);
    f(50, 30);
    const r = read();
    assert.equal(r.h, 260);
    assert.equal(r.w, 260 * 16 / 9, '真实 parsePreset("16:9") → w=h*16/9');
    assert.equal(r.x, 0, '中心保持产生 x=-81.11，被 clampOutpaint max(0,x) 钳到 0（钉现状）');
    assert.equal(r.y, 0, 'y 中心 -30 被 clampOutpaint 钳到 0（钉现状）');
});

test('clampImageResizeScale: 0.05–1 + 两位小数 round + 非有限 → 0.5（纯函数，不回写模块变量）', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'clampImageResizeScale');
    assert.equal(f(0.5), 0.5);
    assert.equal(f(0.2), 0.2);
    assert.equal(f(0.05), 0.05, '下限 0.05');
    assert.equal(f(0), 0.05, '0 → 下限');
    assert.equal(f(-5), 0.05, '负数 → 下限');
    assert.equal(f(2), 1, '上限 1');
    assert.equal(f(300), 1, '超大 → 上限');
    assert.equal(f(0.123), 0.12, '两位小数 round(12.3)=12');
    assert.equal(f(0.125), 0.13, 'round(12.5)=13');
    assert.equal(f('2.5'), 1, '字符串数字 → 2.5 → 上限 1');
    assert.equal(f('abc'), 0.5, 'NaN → 0.5（钉现状）');
    assert.equal(f(NaN), 0.5);
    assert.equal(f(Infinity), 0.5, '+Inf → 0.5（钉现状）');

    // 不回写模块变量。
    evaluate(ctx, 'imageResizeScale = 0.3');
    f(0.8);
    assert.equal(evaluate(ctx, 'imageResizeScale'), 0.3);
});

test('imageResizeDimensions: naturalWidth/Height × clampImageResizeScale(scale) + 缺图源 1×1 兜底', () => {
    const { ctx, harness } = loadImageEditorModule();
    const img = harness.elementsById.get('cropImage');
    img.naturalWidth = 800; img.naturalHeight = 600;
    const f = globalFunction(ctx, 'imageResizeDimensions');
    const read = () => fromContext(f());

    evaluate(ctx, 'imageResizeScale = 0.25');
    assert.deepEqual(read(), { sourceW: 800, sourceH: 600, scale: 0.25, targetW: 200, targetH: 150 });
    evaluate(ctx, 'imageResizeScale = 1');
    assert.deepEqual(read(), { sourceW: 800, sourceH: 600, scale: 1, targetW: 800, targetH: 600 });
    img.naturalWidth = 900; img.naturalHeight = 900;
    evaluate(ctx, 'imageResizeScale = 6');
    assert.deepEqual(read(), { sourceW: 900, sourceH: 900, scale: 1, targetW: 900, targetH: 900 },
        'scale 被 clampImageResizeScale 钳 1（0.05–1，钉现状：无 4096 路径）');

    // 无图（naturalWidth=0）→ sourceW/H 兜底 1（钉现状）。
    img.naturalWidth = 0; img.naturalHeight = 0;
    evaluate(ctx, 'imageResizeScale = 2');
    assert.deepEqual(read(), { sourceW: 1, sourceH: 1, scale: 1, targetW: 1, targetH: 1 });
});

test('gridJoinCanvasSize: null 布局 → 1×1；格尺寸（cols×cellW+gap）与 items 包围盒取大；ceil 取整', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridJoinCanvasSize');

    assert.deepEqual(fromContext(f(null)), { w: 1, h: 1 }, 'null 布局 → 1×1（钉现状）');
    evaluate(ctx, 'gridJoinLayout = null');
    assert.deepEqual(fromContext(f()), { w: 1, h: 1 }, 'let 默认值（null）→ 1×1');

    assert.deepEqual(fromContext(f({ cols: 2, rows: 2, cellW: 64, cellH: 64, gap: 4 })),
        { w: 132, h: 132 }, 'w=2*64+1*4');

    assert.deepEqual(fromContext(f({ cols: 2, rows: 2, cellW: 64, cellH: 64, gap: 4,
        items: [{ x: 100, y: 100, w: 50, h: 50 }] })),
        { w: 150, h: 150 }, 'items 包围盒（150）大于格尺寸（132）→ 取 items');

    assert.deepEqual(fromContext(f({ cellW: 63.5, cellH: 20.25, gap: 0 })),
        { w: 64, h: 21 }, 'ceil 取整（无 cols/rows → 默认 1×1）');

    assert.deepEqual(fromContext(f({ items: [{ x: 0.5, y: 0, w: 10, h: 5 }] })),
        { w: 11, h: 5 }, '无 cols/cellW → 格 1×1，items 包围盒优先');
});

// ---------- 宫格布局数学 ----------

test('groupImageGridLayout: 完美网格优先（源码注释→断言）+ maxThumb 上限 + 小画布 fallback', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'groupImageGridLayout');
    // maxVisibleRows 默认经 canvasCore 代理 = 3（桩值，smart-canvas.js:1034）
    const pick = (count, W, H, maxThumb, maxVisibleRows) => {
        const args = [count, W, H, maxThumb];
        if (maxVisibleRows !== undefined) args.push(32, 8, maxVisibleRows); // pad/gap 默认
        const r = f(...args);
        return { cols: r.cols, rows: r.rows, visibleRows: r.visibleRows, thumb: r.thumb,
            perfect: r.score ? r.score[0] : undefined, fits: r.score ? r.score[1] : undefined };
    };
    const P = (r, cols, rows, visibleRows, thumb, perfect, fits) =>
        assert.deepEqual(r, { cols, rows, visibleRows, thumb, perfect, fits });

    // 源码注释的三个标准例（pad=32, gap=8, maxThumb=480, 1200×800）：
    P(pick(8, 1200, 800, 480), 4, 2, 2, 286, 1, 1, '8 → 4×2（perfect 压过 cols=1/2/3 的非完美布局）');
    P(pick(6, 1200, 800, 480), 3, 2, 2, 380, 1, 1, '6 → 3×2');
    P(pick(9, 1200, 800, 480), 3, 3, 3, 250, 1, 1, '9 → 3×3');

    // 决定性用例：count=5 时 perfect(cols=5) 必须压过 cols=3（3×2 缺角，非整除）。
    // 若评分向量去掉 perfect 项，这里会变成 cols=3（变异红）。
    P(pick(5, 1200, 800, 480), 5, 1, 1, 227, 1, 1, '5 → 5×1 完美优先（防 3×2 缺角）');

    // maxThumb 上限：raw 284 → 钳 100；cols=4 完美且 spare 最小。
    P(pick(4, 1200, 800, 100), 4, 1, 1, 100, 1, 1);

    // maxVisibleRows=1：只有 1 行布局可 perfect（rows<=1）。
    P(pick(8, 1200, 800, 480, 1), 8, 1, 1, 139, 1, 1);

    // 无可行 cols（画布比 pad 还小）→ fallback {cols:1, rows:1, thumb:28}，无 score（钉现状）。
    const fb = f(1, 10, 10, 480);
    assert.deepEqual({ cols: fb.cols, rows: fb.rows, visibleRows: fb.visibleRows, thumb: fb.thumb },
        { cols: 1, rows: 1, visibleRows: 1, thumb: 28 });
    assert.equal(fb.score, undefined);

    // count=0 → fallbackCols=0, rows=NaN（钉现状：无校验）。
    assert.equal(evaluate(ctx, 'groupImageGridLayout(0, 100, 100, 480).cols'), 0);
    assert.equal(evaluate(ctx, 'Number.isNaN(groupImageGridLayout(0, 100, 100, 480).rows)'), true);
});

// ---------- 宫格拼接数学（join/split 的纯几何核心） ----------

test('gridJoinAutoDims: cols=ceil(√count)，rows=ceil(count/cols)（正方形化）', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridJoinAutoDims');
    assert.deepEqual(fromContext(f(0)), { rows: 1, cols: 1 }, '0 → max(1,0) → 1×1（钉现状）');
    assert.deepEqual(fromContext(f(1)), { rows: 1, cols: 1 });
    assert.deepEqual(fromContext(f(2)), { rows: 1, cols: 2 }, 'cols=ceil(√2)=2');
    assert.deepEqual(fromContext(f(3)), { rows: 2, cols: 2 }, 'rows=ceil(3/2)=2');
    assert.deepEqual(fromContext(f(4)), { rows: 2, cols: 2 });
    assert.deepEqual(fromContext(f(5)), { rows: 2, cols: 3 }, 'cols=ceil(√5)=3, rows=ceil(5/3)=2');
    assert.deepEqual(fromContext(f(8)), { rows: 3, cols: 3 }, 'rows=ceil(8/3)=3');
    assert.deepEqual(fromContext(f(9)), { rows: 3, cols: 3 });
    assert.deepEqual(fromContext(f(10)), { rows: 3, cols: 4 }, 'cols=ceil(√10)=4, rows=ceil(10/4)=3');
});

test('gridJoinNaturalSize: natural_w/width/cache 优先级 + 512 兜底 + min 1', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridJoinNaturalSize');
    assert.deepEqual(fromContext(f({ item: { natural_w: 800, natural_h: 600 }, index: 0 })), { w: 800, h: 600 });
    assert.deepEqual(fromContext(f({ item: { width: 400, height: 300 }, index: 1 })), { w: 400, h: 300 }, 'natural_w 缺失 → width');
    assert.deepEqual(fromContext(f({ item: { natural_w: 100, natural_h: 50, width: 999, height: 999 } })), { w: 100, h: 50 }, 'natural_* 优先');
    evaluate(ctx, 'gridJoinImageCache.set(7, {naturalWidth: 640, naturalHeight: 480})');
    assert.deepEqual(fromContext(f({ item: {}, index: 7 })), { w: 640, h: 480 }, 'image 字段缺失 → 缓存');
    assert.deepEqual(fromContext(f({ item: {} })), { w: 512, h: 512 }, '全缺 → 512 兜底');
    assert.deepEqual(fromContext(f()), { w: 512, h: 512 }, 'entry undefined → 512');
    assert.deepEqual(fromContext(f({ item: { natural_w: 0.4, natural_h: 0.2 } })), { w: 1, h: 1 }, 'min 1');
    assert.deepEqual(fromContext(f({ item: { natural_w: 0, natural_h: 200 } })), { w: 512, h: 200 }, '0 视为缺失 → 512（钉现状）');
});

test('gridJoinBaseCellSize: 最大边 420 上限等比缩放 + 小图 scale=1 + 空列表 1×1', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridJoinBaseCellSize');
    assert.deepEqual(fromContext(f([{ item: { natural_w: 800, natural_h: 600 } }, { item: { natural_w: 400, natural_h: 400 } }])),
        { w: 420, h: 315, scale: 0.525 }, 'scale=420/800=0.525；h=round(600*0.525)');
    assert.deepEqual(fromContext(f([{ item: { natural_w: 100, natural_h: 50 } }])),
        { w: 100, h: 50, scale: 1 }, '小图不放大');
    assert.deepEqual(fromContext(f([])), { w: 1, h: 1, scale: 1 }, '空列表 → max(1, ...) = 1（钉现状）');
});

test('gridJoinItemDisplaySize: 纯 round + min 1（entry 参数不参与）', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridJoinItemDisplaySize');
    assert.deepEqual(fromContext(f({}, { w: 63.5, h: 20.25 })), { w: 64, h: 20 }, 'round(63.5)=64, round(20.25)=20');
    assert.deepEqual(fromContext(f({}, { w: 0.4, h: 0.2 })), { w: 1, h: 1 }, 'round(0.4)=0 → min 1');
    assert.deepEqual(fromContext(f({}, { w: 100, h: 50 })), { w: 100, h: 50 });
});

test('gridJoinVisualOrder: items 按 y 再 x 排序 → Number(index) 序列；layout 缺省 → []', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridJoinVisualOrder');
    assert.deepEqual(fromContext(f({ items: [
        { index: 2, x: 10, y: 30 }, { index: 1, x: 0, y: 30 }, { index: 0, x: 0, y: 0 },
    ]})), [0, 1, 2], 'y 优先；同 y 按 x');
    assert.deepEqual(fromContext(f({ items: [
        { index: 0, x: 0, y: 5 }, { index: 1, x: 9, y: 2 }, { index: 2, x: 1, y: 2 },
    ]})), [2, 1, 0]);
    assert.deepEqual(fromContext(f({ items: [{ index: '3', x: 0, y: 0 }] })), [3], 'index 字符串 → Number');
    assert.deepEqual(fromContext(f({})), [], 'items 缺失 → []');
    assert.deepEqual(fromContext(f(null)), [], 'layout null → []');
    assert.deepEqual(fromContext(f()), [], '缺省参数 = gridJoinLayout（null）→ []');
    // 不修改入参（slice）。
    const items = [{ index: 1, x: 0, y: 5 }, { index: 0, x: 0, y: 0 }];
    const before = items.map(i => i.index);
    f({ items });
    assert.deepEqual(items.map(i => i.index), before, '入参顺序不变');
});

test('gridJoinDragTarget: 排除自己 + inside 优先于 score + score < max(双方最大边)×0.55 门', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridJoinDragTarget');
    const setLayout = () => evaluate(ctx, 'gridJoinLayout = {rows: 1, cols: 3, cellW: 100, cellH: 100, gap: 10, items: [' +
        '{index:0, x:0, y:0, w:200, h:200}, {index:1, x:0, y:60, w:300, h:300}, {index:2, x:310, y:130, w:100, h:100}]}');
    const idxOf = () => {
        const r = f();
        return r === null ? null : fromContext(r).index;
    };

    // 无拖拽/无布局 → null。
    setLayout();
    evaluate(ctx, 'gridJoinDrag = null');
    assert.equal(idxOf(), null);
    evaluate(ctx, 'gridJoinDrag = {index:0, dx:0, dy:0}; gridJoinLayout = null');
    assert.equal(idxOf(), null);
    setLayout();

    // 排除自己：拖 index 1（自己中心 150,210，若不排除自己会 score=0 胜出）→ 落到 index 0（score≈120.8 < 165）。
    evaluate(ctx, 'gridJoinDrag = {index:1, dx:0, dy:0}');
    assert.equal(idxOf(), 0, '自己格子含拖拽点但被排除；index 2 score≈212 > 165 出局');

    // inside 优先于更近的 score：拖 0 → 点 (260,160)：index 1 inside（score≈120.8），
    // index 2 非 inside 但 score≈102 更小（< 阈值 110）——若排序按 score 会错选 2。
    evaluate(ctx, 'gridJoinDrag = {index:0, dx:160, dy:60}');
    assert.equal(idxOf(), 1, 'inside 优先（钉 0.55 阈值内的非 inside 候选被压过）');

    // 纯 score 路径：拖 2 → 点 (100,-5)：index 0 非 inside（cy<0），score=105 < 110 → 中选。
    evaluate(ctx, 'gridJoinDrag = {index:2, dx:-260, dy:-185}');
    assert.equal(idxOf(), 0);

    // 阈值门：拖 0 → 点 (260,430)：两者 score 均超阈值 → null。
    evaluate(ctx, 'gridJoinDrag = {index:0, dx:160, dy:330}');
    assert.equal(idxOf(), null, 'score ≈246/≈269 均 > 110');

    // 被拖 index 不在布局里 → null。
    evaluate(ctx, 'gridJoinDrag = {index:99, dx:0, dy:0}');
    assert.equal(idxOf(), null);
});

test('gridSplitRects（标准模式）: rows=hLines+1 / cols=vLines+1，首尾贴边、中间留 gap/2，越界格丢弃', () => {
    const { ctx, harness } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridSplitRects');
    const set = (h, v, gap) => {
        harness.elementsById.get('gridHorizontalLines').value = h;
        harness.elementsById.get('gridVerticalLines').value = v;
        harness.elementsById.get('gridGapSize').value = gap;
    };
    const rects = (w, h) => fromContext(f(w, h));

    set('1', '1', '8');
    assert.deepEqual(rects(300, 200), [
        { row: 0, col: 0, x: 0, y: 0, w: 146, h: 96 },
        { row: 0, col: 1, x: 154, y: 0, w: 146, h: 96 },
        { row: 1, col: 0, x: 0, y: 104, w: 146, h: 96 },
        { row: 1, col: 1, x: 154, y: 104, w: 146, h: 96 },
    ], '2×2：中间线 ±gap/2，首尾贴边');

    set('0', '0', '8');
    assert.deepEqual(rects(300, 200), [{ row: 0, col: 0, x: 0, y: 0, w: 300, h: 200 }], '0 线 → 单格整幅');

    set('1', '1', '0');
    assert.deepEqual(rects(300, 200), [
        { row: 0, col: 0, x: 0, y: 0, w: 150, h: 100 },
        { row: 0, col: 1, x: 150, y: 0, w: 150, h: 100 },
        { row: 1, col: 0, x: 0, y: 100, w: 150, h: 100 },
        { row: 1, col: 1, x: 150, y: 100, w: 150, h: 100 },
    ], 'gap 0 → 无缝');

    set('1', '1', '8');
    assert.deepEqual(rects(5, 5), [], '画布比 gap 还小 → 全部格被丢弃');

    set('25', '25', '0');
    assert.equal(rects(300, 200).length, 441, '线数钳 20 → 21×21');
});

test('gridSplitRectsCustom: gridCustomLines 切分线（去重排序）+ gap 钳 240；mode 关时走标准路径', () => {
    const { ctx, harness } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridSplitRects');
    const gap = harness.elementsById.get('gridGapSize');
    const rects = (w, h) => fromContext(f(w, h));
    const lines = (ls) => evaluate(ctx, 'gridCustomLines = ' + JSON.stringify(ls));
    const mode = (on) => evaluate(ctx, 'gridCustomMode = ' + (on ? 'true' : 'false'));

    gap.value = '8';
    mode(true);
    lines([{ type: 'h', pos: 0.5 }, { type: 'v', pos: 0.5 }]);
    assert.deepEqual(rects(300, 200), [
        { row: 0, col: 0, x: 0, y: 0, w: 146, h: 96 },
        { row: 0, col: 1, x: 154, y: 0, w: 146, h: 96 },
        { row: 1, col: 0, x: 0, y: 104, w: 146, h: 96 },
        { row: 1, col: 1, x: 154, y: 104, w: 146, h: 96 },
    ], 'h/v 各 1 线 → 2×2，与标准同形');

    lines([{ type: 'v', pos: 0.25 }, { type: 'v', pos: 0.5 }, { type: 'v', pos: 0.75 }]);
    assert.deepEqual(rects(300, 200), [
        { row: 0, col: 0, x: 0, y: 0, w: 71, h: 200 },
        { row: 0, col: 1, x: 79, y: 0, w: 67, h: 200 },
        { row: 0, col: 2, x: 154, y: 0, w: 67, h: 200 },
        { row: 0, col: 3, x: 229, y: 0, w: 71, h: 200 },
    ], '3 条竖线 → 4 列，首末列含边距补偿差');

    lines([{ type: 'h', pos: 0.5 }, { type: 'h', pos: 0.5 }, { type: 'h', pos: 0.5 }]);
    assert.deepEqual(rects(300, 200), [
        { row: 0, col: 0, x: 0, y: 0, w: 300, h: 96 },
        { row: 1, col: 0, x: 0, y: 104, w: 300, h: 96 },
    ], '重复线去重');

    gap.value = '999';
    lines([{ type: 'h', pos: 0.5 }]);
    assert.deepEqual(rects(300, 200), [], 'gap 钳 240 → 半 gap 120 > 行高 → 全丢弃');
    gap.value = '8';

    // mode 关 → 走标准路径（自定义线被忽略）。
    mode(false);
    lines([{ type: 'v', pos: 0.5 }]);
    harness.elementsById.get('gridHorizontalLines').value = '0';
    harness.elementsById.get('gridVerticalLines').value = '0';
    assert.deepEqual(rects(300, 200), [{ row: 0, col: 0, x: 0, y: 0, w: 300, h: 200 }]);
});

test('gridLayoutFromRects: type/grid-split + rows/cols = max(row|col)+1；空列表 1×1', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'gridLayoutFromRects');

    const empty = fromContext(f([]));
    assert.equal(empty.type, 'grid-split');
    assert.equal(empty.rows, 1);
    assert.equal(empty.cols, 1);
    assert.match(empty.groupId, /^grid_/);

    const r = fromContext(f([{ row: 0, col: 0 }, { row: 1, col: 2 }, { row: 0, col: 1 }]));
    assert.equal(r.type, 'grid-split');
    assert.equal(r.rows, 2);
    assert.equal(r.cols, 3);
    assert.match(r.groupId, /^grid_/);

    // uid 每次不同（随机）。
    assert.notEqual(empty.groupId, fromContext(f([])).groupId);
});


// ===== 组 D：文本项与历史栈（模块内状态；canvas 2D 用零逻辑记录器桩） =====

test('editTextHasContent: 空白/缺省全 false，任一非空白 true（读模块级 editTextItems）', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'editTextHasContent');
    evaluate(ctx, 'editTextItems = []');
    assert.equal(f(), false);
    evaluate(ctx, 'editTextItems = [{text: "   "}, {text: ""}, {}]');
    assert.equal(f(), false, '空白/空串/无 text → false');
    evaluate(ctx, 'editTextItems = [{text: "a b"}]');
    assert.equal(f(), true);
    evaluate(ctx, 'editTextItems = [{text: ""}, {text: "x"}]');
    assert.equal(f(), true, '任一有内容 → true');
    evaluate(ctx, 'editTextItems = [null]');
    assert.equal(f(), false, 'null 项安全（item?.text）');
});

test('selectedEditTextItem: 按 editTextSelectedId 查 editTextItems；空/未知 → null', () => {
    const { ctx } = loadImageEditorModule();
    const f = globalFunction(ctx, 'selectedEditTextItem');
    const create = globalFunction(ctx, 'createEditTextItem');
    const itemA = fromContext(create('A', { x: 0, y: 0 }));
    const itemB = fromContext(create('B', { x: 1, y: 1 }));
    evaluate(ctx, `editTextItems = [${JSON.stringify(itemA)}, ${JSON.stringify(itemB)}];`);

    evaluate(ctx, 'editTextSelectedId = ""');
    assert.equal(f(), null, '空 id → null');
    evaluate(ctx, `editTextSelectedId = "${itemA.id}"`);
    assert.deepEqual(fromContext(f()), itemA);
    evaluate(ctx, `editTextSelectedId = "${itemB.id}"`);
    assert.equal(fromContext(f()).text, 'B');
    evaluate(ctx, 'editTextSelectedId = "nope"');
    assert.equal(f(), null, '未知 id → null');
});

test('createEditTextItem: size=max(10,min(120,Number(preset.size)||brush*2))；默认文本/颜色', () => {
    const { ctx, harness } = loadImageEditorModule();
    const f = globalFunction(ctx, 'createEditTextItem');
    harness.element('paintBrushSize', 'input').value = '20'; // → editTextSizeFromBrush()=40

    const base = fromContext(f('Hello', { x: 5, y: 6 }));
    assert.match(base.id, /^txt_/);
    assert.equal(base.text, 'Hello');
    assert.equal(base.x, 5);
    assert.equal(base.y, 6);
    assert.equal(base.color, '#33ff99', 'loader 注册的 paintBrushColor 值（brushColor() 读取）');
    assert.equal(base.size, 40, 'brush 20 → 40');

    assert.equal(fromContext(f('A', { x: 0, y: 0 }, { size: 130 })).size, 120, '130 → 钳 120（钉现状：有 120 上限）');
    assert.equal(fromContext(f('A', { x: 0, y: 0 }, { size: '0.2' })).size, 10, 'truthy 0.2 → max(10, 0.2)');
    assert.equal(fromContext(f('A', { x: 0, y: 0 }, { size: -5 })).size, 10, '负数 → max(10, -5)');
    assert.equal(fromContext(f('A', { x: 0, y: 0 }, { size: 7 })).size, 10, '低于下限 → max(10, 7) = 10');
    assert.equal(fromContext(f('A', { x: 0, y: 0 }, { size: 50 })).size, 50, '区间内不钳');
    assert.equal(fromContext(f('A', { x: 0, y: 0 }, { size: '0' })).size, 40, '0 falsy → 回退 brush 值');

    assert.equal(fromContext(f('', { x: 1, y: 2 })).text, '双击编辑', '空文本 → defaultEditTextText（zh，StudioI18n 未注册）');
    const num = fromContext(f('T', { x: '7', y: '8' }));
    assert.equal(num.x, 7);
    assert.equal(num.y, 8, '坐标 Number() 转换');
    assert.equal(fromContext(f('T', {})).x, 0, 'point 缺字段 → 0');
});

test('setBrushTool: 白名单+缺省 free；primary/secondary 同步；text 保留 inline editor', () => {
    const { ctx, harness } = loadImageEditorModule();
    const f = globalFunction(ctx, 'setBrushTool');
    const btns = ['free', 'rect', 'ellipse'].map((t) => {
        const b = harness.makeElement('button');
        b.dataset.brushTool = t;
        return b;
    });
    harness.querySelectorAll('[data-brush-tool]', btns);
    const primary = () => fromContext(btns.filter((b) => b.classList.contains('primary')).map((b) => b.dataset.brushTool));

    f('rect');
    assert.equal(evaluate(ctx, 'brushTool'), 'rect');
    assert.deepEqual(primary(), ['rect'], 'rect primary，其余 secondary');

    f('ellipse');
    assert.deepEqual(primary(), ['ellipse']);

    f('text');
    assert.equal(evaluate(ctx, 'brushTool'), 'text');
    assert.deepEqual(primary(), [], 'text 按钮未注册 → 无 primary（钉注册面）');

    f('magic');
    assert.equal(evaluate(ctx, 'brushTool'), 'free', '白名单外 → free（钉现状）');
    assert.deepEqual(primary(), ['free']);

    // inline editor：非 text 工具清除（含 el.remove()），text 工具保留（钉现状）。
    const item = fromContext(globalFunction(ctx, 'createEditTextItem')('A', { x: 0, y: 0 }));
    evaluate(ctx, `editTextItems = [${JSON.stringify(item)}];`);
    evaluate(ctx, 'window.__editorStub = { removed: false, remove(){ this.removed = true; }, textContent: "A" };');
    evaluate(ctx, `editTextInlineEditor = { itemId: "${item.id}", before: "A", el: window.__editorStub };`);

    f('rect');
    assert.equal(evaluate(ctx, 'editTextInlineEditor'), null, '非 text 工具清除 inline editor');
    assert.equal(evaluate(ctx, 'window.__editorStub.removed'), true, 'editor.el.remove() 被调用');

    evaluate(ctx, `editTextInlineEditor = { itemId: "${item.id}", before: "A", el: window.__editorStub };`);
    f('text');
    assert.notEqual(evaluate(ctx, 'editTextInlineEditor'), null, 'text 工具不清除 inline editor（钉现状）');
});

test('pushEditDrawHistory: MAX=40 截断（shift 丢最旧）+ 每次清 redo + 快照为拷贝', () => {
    const { ctx, harness } = loadImageEditorModule();
    const f = globalFunction(ctx, 'pushEditDrawHistory');
    const canvas = harness.element('editDrawCanvas', 'canvas');
    canvas.width = 10; canvas.height = 10;
    let n = 0;
    canvas.getContext = () => ({ getImageData: () => ({ n: n++ }), putImageData: () => {} });

    for (let i = 0; i < 45; i++) f();
    assert.equal(evaluate(ctx, 'editDrawUndoStack.length'), 40, '45 次 → 截 40（EDIT_DRAW_HISTORY_MAX）');
    assert.equal(evaluate(ctx, 'editDrawRedoStack.length'), 0);
    assert.equal(fromContext(evaluate(ctx, 'editDrawUndoStack[0]')).imageData.n, 5, '最旧 5 条被 shift 掉');
    assert.equal(fromContext(evaluate(ctx, 'editDrawUndoStack[39]')).imageData.n, 44);

    // 快照 textItems 是浅拷贝（{...item}）：之后改活值不影响快照。
    const item = fromContext(globalFunction(ctx, 'createEditTextItem')('V', { x: 1, y: 1 }));
    evaluate(ctx, `editTextItems = [${JSON.stringify(item)}];`);
    f();
    evaluate(ctx, 'editTextItems[0].text = "MUTATED"');
    const snap = fromContext(evaluate(ctx, 'editDrawUndoStack[editDrawUndoStack.length - 1]'));
    assert.equal(snap.textItems[0].text, 'V', '快照与活值隔离');
    assert.equal(snap.textSelectedId, '');

    // redo 清除语义：undo 产生 redo 后，新 push 清空 redo。
    globalFunction(ctx, 'undoEditDrawing')();
    assert.equal(evaluate(ctx, 'editDrawRedoStack.length'), 1);
    f();
    assert.equal(evaluate(ctx, 'editDrawRedoStack.length'), 0, '新 push 清空 redo');
});

test('undo/redoEditDrawing: 栈转移语义 + restore 回滚 labelCounter（0 不恢复，钉现状）', () => {
    const { ctx, harness } = loadImageEditorModule();
    const push = globalFunction(ctx, 'pushEditDrawHistory');
    const undo = globalFunction(ctx, 'undoEditDrawing');
    const redo = globalFunction(ctx, 'redoEditDrawing');
    const canvas = harness.element('editDrawCanvas', 'canvas');
    canvas.width = 10; canvas.height = 10;
    let n = 0;
    let lastPut = null;
    canvas.getContext = () => ({
        getImageData: () => ({ n: n++ }),
        putImageData: (img) => { lastPut = img; },
    });
    const undoLen = () => evaluate(ctx, 'editDrawUndoStack.length');
    const redoLen = () => evaluate(ctx, 'editDrawRedoStack.length');

    undo();
    assert.equal(undoLen(), 0, '空栈 undo no-op');
    redo();
    assert.equal(redoLen(), 0, '空栈 redo no-op');

    push(); push(); push();
    assert.equal(undoLen(), 3);
    assert.equal(redoLen(), 0);

    undo();
    assert.equal(undoLen(), 2);
    assert.equal(redoLen(), 1, 'undo：当前快照入 redo，弹 undo 顶');
    assert.equal(lastPut.n, 2, 'restore undo 栈顶（第 3 条快照，imageData.n=2）');

    undo();
    assert.equal(undoLen(), 1);
    assert.equal(redoLen(), 2);
    assert.equal(lastPut.n, 1, 'undo 栈第 2 条（imageData.n=1）');

    undo();
    assert.equal(undoLen(), 0);
    assert.equal(redoLen(), 3);
    assert.equal(lastPut.n, 0, '恢复第 1 条快照（n=0）');

    undo();
    assert.equal(undoLen(), 0);
    assert.equal(redoLen(), 3, 'undo 空栈 no-op（redo 不变）');
    assert.equal(lastPut.n, 0, 'no-op 不 putImageData');

    redo();
    assert.equal(undoLen(), 1);
    assert.equal(redoLen(), 2);
    assert.equal(lastPut.n, 5, 'redo：当前快照入 undo，弹 redo 顶（n=5）');
    redo();
    assert.equal(undoLen(), 2);
    assert.equal(redoLen(), 1);
    assert.equal(lastPut.n, 4, '恢复第 4 条快照（n=4）');
    redo();
    assert.equal(undoLen(), 3);
    assert.equal(redoLen(), 0);
    assert.equal(lastPut.n, 3, '恢复第 3 条快照（n=3）');
    redo();
    assert.equal(undoLen(), 3, 'redo 空栈 no-op');
    assert.equal(lastPut.n, 3, 'no-op 不 putImageData');

    // labelCounter：快照非 0 时恢复；0 时不恢复（if(snapshot.labelCounter) 钉现状）。
    evaluate(ctx, 'brushLabelCounter = 7');
    push();
    evaluate(ctx, 'brushLabelCounter = 1');
    undo();
    assert.equal(evaluate(ctx, 'brushLabelCounter'), 7, 'labelCounter 从快照恢复');

    evaluate(ctx, 'brushLabelCounter = 0');
    push();
    evaluate(ctx, 'brushLabelCounter = 5');
    undo();
    assert.equal(evaluate(ctx, 'brushLabelCounter'), 5, '快照 0 为 falsy 不恢复（钉现状）');
});

test('resetEditDrawingHistory: 清栈/清文本/计数器归 1 + 历史按钮 disabled 同步', () => {
    const { ctx, harness } = loadImageEditorModule();
    const push = globalFunction(ctx, 'pushEditDrawHistory');
    const undo = globalFunction(ctx, 'undoEditDrawing');
    const reset = globalFunction(ctx, 'resetEditDrawingHistory');
    const canvas = harness.element('editDrawCanvas', 'canvas');
    canvas.width = 10; canvas.height = 10;
    let n = 0;
    canvas.getContext = () => ({ getImageData: () => ({ n: n++ }), putImageData: () => {} });

    for (const id of ['maskUndoBtn', 'brushUndoBtn', 'maskRedoBtn', 'brushRedoBtn']) harness.element(id, 'button');
    const btn = (id) => harness.element(id);

    reset();
    assert.equal(evaluate(ctx, 'editDrawUndoStack.length'), 0);
    assert.equal(evaluate(ctx, 'editDrawRedoStack.length'), 0);
    for (const id of ['maskUndoBtn', 'brushUndoBtn', 'maskRedoBtn', 'brushRedoBtn']) {
        assert.equal(btn(id).disabled, true, `${id} 空栈 sync → disabled`);
    }

    push();
    assert.equal(btn('maskUndoBtn').disabled, false, 'undo 有栈 → enabled');
    assert.equal(btn('brushUndoBtn').disabled, false);
    assert.equal(btn('brushRedoBtn').disabled, true, 'redo 仍空');

    undo();
    assert.equal(btn('maskRedoBtn').disabled, false, 'undo 后 redo 有栈 → enabled');

    const item = fromContext(globalFunction(ctx, 'createEditTextItem')('Z', { x: 0, y: 0 }));
    evaluate(ctx, `editTextItems = [${JSON.stringify(item)}];`);
    evaluate(ctx, `editTextSelectedId = "${item.id}"`);
    evaluate(ctx, 'brushLabelCounter = 9');

    reset();
    assert.equal(evaluate(ctx, 'editDrawUndoStack.length'), 0);
    assert.equal(evaluate(ctx, 'editDrawRedoStack.length'), 0);
    assert.equal(evaluate(ctx, 'editTextItems.length'), 0);
    assert.equal(evaluate(ctx, 'editTextSelectedId'), '');
    assert.equal(evaluate(ctx, 'brushLabelCounter'), 1);
    for (const id of ['maskUndoBtn', 'brushUndoBtn', 'maskRedoBtn', 'brushRedoBtn']) {
        assert.equal(btn(id).disabled, true, `${id} reset 后 disabled`);
    }
});

