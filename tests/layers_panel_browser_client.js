// #269 图层面板全链浏览器旅程（#255 验收第 1 条的浏览器证据）。
//
// 链路：打开图片编辑器 -> 图层模式 -> 多层创建（笔刷像素导入 + 空白图层）
// -> 切换显隐 -> 拖拽重排 -> 导出合成 PNG 并按层序断言像素
// （红下蓝上 / 蓝下红上 / 全隐回退底色的遮挡关系采样点）。
//
// 像素获取方式：exportCompositePng() 走离屏 canvas.toBlob 生成导出 PNG 字节，
// 这里在 prototype 上包一层 toBlob 记录真实产物字节（同时放行原回调），
// 再 createImageBitmap 解码采样——断言的就是产品导出函数产出的像素，
// 不依赖 headless 下载落盘位置。
(function(){
    'use strict';

    const result = {
        result_id: 'layers-panel-evidence-269',
        issue: 269,
        trace: [],
        viewport: {width: innerWidth, height: innerHeight},
        assertions: [],
        errors: [],
        pass: false
    };
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const assertion = (name, pass, actual) => result.assertions.push({name, pass: Boolean(pass), actual: actual === undefined ? null : actual});
    const trace = (label) => result.trace.push(label + '@' + Math.round(performance.now()/1000));
    async function waitFor(predicate, timeout=12000){
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline){
            try {
                if(await predicate()) return true;
            } catch(_){}
            await sleep(40);
        }
        return false;
    }
    async function postResult(){
        result.pass = result.errors.length === 0 && result.assertions.length > 0
            && result.assertions.every(item => item.pass);
        try {
            const res = await fetch('/__conversation_browser_results', {
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify(result)
            });
            if(!res.ok) result.errors.push('result upload: HTTP ' + res.status);
        } catch(error){ result.errors.push('result upload: ' + error.message); }
    }
    function evalFn(fn){
        return Function('"use strict";return (' + fn + ')')();
    }

    // --- 场景常量（与 tests/layers_panel_browser.py 的 seed 图对应） ---
    const W = 640, H = 480;
    const BASE_COLOR = [60, 64, 96];   // 底图纯色
    const RED = [255, 0, 0];           // 第一个笔刷图层
    const BLUE = [0, 0, 255];          // 第二个笔刷图层（初始位于红之上）
    // 笔刷矩形（归一化坐标）：红 x 0.10..0.45，蓝 x 0.25..0.60，均 y 0.10..0.45，重叠区 x 0.25..0.45。
    const RECT_RED = [0.10, 0.10, 0.45, 0.45];
    const RECT_BLUE = [0.25, 0.10, 0.60, 0.45];
    // 采样点（归一化坐标）：各点深入对应区域内部，避开矩形边缘。
    const PT_RED_ONLY = [0.15, 0.27];
    const PT_OVERLAP = [0.32, 0.27];
    const PT_BLUE_ONLY = [0.55, 0.27];
    const PT_BASE_ONLY = [0.90, 0.90];

    function layerRows(){ return Array.from(document.querySelectorAll('#layerList .layer-row')); }
    function rowIds(){ return layerRows().map(row => row.dataset.layerId); }
    function rowOf(id){ return layerRows().find(row => row.dataset.layerId === id) || null; }

    function drawBrushRect(color, x0, y0, x1, y1){
        const canvas = document.getElementById('editDrawCanvas');
        if(!canvas || !canvas.width || !canvas.height) return 'canvas-missing';
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = 'rgb(' + color.join(',') + ')';
        ctx.fillRect(
            Math.floor(x0 * canvas.width),
            Math.floor(y0 * canvas.height),
            Math.ceil((x1 - x0) * canvas.width),
            Math.ceil((y1 - y0) * canvas.height)
        );
        return 'ok';
    }

    // 记录导出函数产出的真实 toBlob 字节（原型层插桩，原回调照常执行）。
    const exportCapture = {blob: null};
    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function(callback, type){
        return origToBlob.call(this, (blob) => {
            exportCapture.blob = blob;
            if(callback){
                try { callback(blob); } catch(_){}
            }
        }, type);
    };

    async function captureCompositeExport(timeout=10000){
        exportCapture.blob = null;
        const btn = document.getElementById('layerExportBtn');
        if(!btn) throw new Error('layerExportBtn missing');
        btn.click();
        const deadline = performance.now() + timeout;
        while(performance.now() < deadline && !exportCapture.blob) await sleep(40);
        if(!exportCapture.blob) throw new Error('export composite produced no blob');
        const bmp = await createImageBitmap(exportCapture.blob);
        const canvas = document.createElement('canvas');
        canvas.width = bmp.width; canvas.height = bmp.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(bmp, 0, 0);
        return {w: bmp.width, h: bmp.height, blobSize: exportCapture.blob.size, data: ctx.getImageData(0, 0, bmp.width, bmp.height).data};
    }

    function samplePx(img, fx, fy){
        const x = Math.max(0, Math.min(img.w - 1, Math.floor(fx * img.w)));
        const y = Math.max(0, Math.min(img.h - 1, Math.floor(fy * img.h)));
        const p = (y * img.w + x) * 4;
        return [img.data[p], img.data[p + 1], img.data[p + 2], img.data[p + 3]];
    }
    // 合成在采样点必须不透明；PNG 无损 + 纯色填充，容差只为防边缘抗锯齿误伤。
    function nearColor(actual, expected, tol=16){
        if(!actual || actual[3] < 200) return false;
        return actual.slice(0, 3).every((value, i) => Math.abs(value - expected[i]) <= tol);
    }
    function pixelAssertions(label, img, expectations){
        for(const [name, point, expected] of expectations){
            const actual = samplePx(img, point[0], point[1]);
            assertion(label + ': ' + name, nearColor(actual, expected), {point, expected, actual});
        }
    }

    // 拖拽重排：走产品绑定的 dragstart/dragover/dragend 处理器（HTML5 DnD 事件链）。
    // insertAfter=true 时把 clientY 落在目标行下半区（插入其后），false 落在上半区（插入其前）。
    function dragRow(fromId, toId, insertAfter){
        const from = rowOf(fromId), to = rowOf(toId);
        if(!from || !to) throw new Error('drag rows missing: ' + fromId + ' / ' + toId);
        const dataTransfer = new DataTransfer();
        const rect = to.getBoundingClientRect();
        from.dispatchEvent(new DragEvent('dragstart', {dataTransfer, bubbles: true}));
        to.dispatchEvent(new DragEvent('dragover', {dataTransfer, bubbles: true, clientY: rect.top + rect.height * (insertAfter ? 0.75 : 0.25)}));
        from.dispatchEvent(new DragEvent('dragend', {dataTransfer, bubbles: true}));
    }

    async function toggleHidden(id, wantHidden){
        return waitFor(() => {
            const row = rowOf(id);
            if(!row) return false;
            const hidden = row.classList.contains('layer-hidden');
            if(hidden !== wantHidden) row.querySelector('.layer-vis').click();
            return rowOf(id)?.classList.contains('layer-hidden') === wantHidden;
        }, 5000);
    }

    async function run(){
        trace('run-start');
        const bootOk = await waitFor(() => {
            try { return (0, eval)('canvasState.nodes').some(n => n && n.id === 'edit-node'); } catch(_){ return false; }
        }, 20000);
        assertion('canvas boot exposes edit-node', bootOk, bootOk ? 'ok' : 'nodes missing');
        if(!bootOk) return;

        // --- 打开编辑器，等原图（640x480 主图，非 2048 预览）加载完 ---
        trace('open-editor');
        const editorOk = await waitFor(async () => {
            try { evalFn('(id) => { openImageEditor(id, 0); return true; }')('edit-node'); } catch(_){}
            const el = document.getElementById('cropImage');
            return !!(document.getElementById('imageEditModal')?.classList.contains('open') && el && el.naturalWidth === W && el.naturalHeight === H);
        }, 30000);
        const cropImg = document.getElementById('cropImage');
        assertion('editor opens with full-size original image', editorOk, {
            open: document.getElementById('imageEditModal')?.classList.contains('open'),
            natural: cropImg ? [cropImg.naturalWidth, cropImg.naturalHeight] : null,
            src: cropImg?.getAttribute('src') || ''
        });
        if(!editorOk) return;

        // --- 图层模式：store 从底图建出 base 层 ---
        trace('layers-mode');
        const layersOk = await waitFor(() => {
            try { evalFn('() => { setImageEditMode("layers", true); return true; }')(); } catch(_){}
            const tools = document.getElementById('imageLayersTools');
            return !!(tools && tools.classList.contains('active') && rowIds().length === 1 && rowIds()[0] === 'layer-base');
        }, 5000);
        assertion('layers mode shows the base layer row', layersOk, rowIds());
        if(!layersOk) return;

        // --- 多层创建：红笔刷 -> 空白 -> 蓝笔刷（初始显示序：蓝、空白、红、底图） ---
        trace('create-layers');
        const drawCanvasOk = await waitFor(() => {
            const c = document.getElementById('editDrawCanvas');
            return !!(c && c.width >= W && c.height >= H);
        }, 5000);
        assertion('edit draw canvas sized to original image', drawCanvasOk, (() => { const c = document.getElementById('editDrawCanvas'); return c ? [c.width, c.height] : null; })());
        if(!drawCanvasOk) return;

        const redCreated = await waitFor(async () => {
            drawBrushRect(RED, ...RECT_RED);
            document.getElementById('layerImportBrushBtn').click();
            const ids = rowIds();
            return ids.length === 2 && ids[0] !== 'layer-base';
        }, 5000);
        const redId = rowIds()[0];
        assertion('importing brush pixels adds a red layer above base', redCreated, rowIds());

        const blankCreated = await waitFor(async () => {
            document.getElementById('layerAddBlankBtn').click();
            const ids = rowIds();
            return ids.length === 3 && ids[0] !== 'layer-base' && ids[0] !== redId;
        }, 5000);
        const blankId = rowIds()[0];
        assertion('adding a blank layer stacks three rows', blankCreated, rowIds());

        const blueCreated = await waitFor(async () => {
            drawBrushRect(BLUE, ...RECT_BLUE);
            document.getElementById('layerImportBrushBtn').click();
            const ids = rowIds();
            return ids.length === 4 && ids[0] !== 'layer-base' && ids[0] !== redId && ids[0] !== blankId;
        }, 5000);
        const blueId = rowIds()[0];
        assertion('second brush import stacks four rows', blueCreated, rowIds());
        assertion('initial display order is blue, blank, red, base',
            JSON.stringify(rowIds()) === JSON.stringify([blueId, blankId, redId, 'layer-base']), rowIds());
        if(!(redCreated && blankCreated && blueCreated)) return;

        // --- 导出 1：初始层序，蓝在红之上 ---
        trace('export-1-initial');
        let ex1;
        try {
            ex1 = await captureCompositeExport();
            assertion('export 1: PNG size matches the original image', ex1.w === W && ex1.h === H, {size: [ex1.w, ex1.h], blobBytes: ex1.blobSize});
            pixelAssertions('export 1 (blue above red)', ex1, [
                ['overlap pixel is blue (top layer wins)', PT_OVERLAP, BLUE],
                ['red-only pixel is red', PT_RED_ONLY, RED],
                ['blue-only pixel is blue', PT_BLUE_ONLY, BLUE],
                ['untouched pixel keeps base color', PT_BASE_ONLY, BASE_COLOR]
            ]);
        } catch(error){
            result.errors.push('export 1: ' + error.message);
            return;
        }

        // --- 切换显隐：隐藏蓝 -> 红升为顶层色；再隐藏红 -> 全部回退底色 ---
        trace('toggle-visibility');
        const blueHidden = await toggleHidden(blueId, true);
        assertion('toggling visibility hides the blue layer row', blueHidden, rowOf(blueId)?.className);
        let ex2;
        try {
            ex2 = await captureCompositeExport();
            pixelAssertions('export 2 (blue hidden)', ex2, [
                ['overlap pixel is red (red is now top)', PT_OVERLAP, RED],
                ['blue-only region falls back to base color', PT_BLUE_ONLY, BASE_COLOR],
                ['red-only pixel stays red', PT_RED_ONLY, RED]
            ]);
        } catch(error){
            result.errors.push('export 2: ' + error.message);
            return;
        }

        const redHidden = await toggleHidden(redId, true);
        assertion('toggling visibility hides the red layer row', redHidden, rowOf(redId)?.className);
        let ex3;
        try {
            ex3 = await captureCompositeExport();
            pixelAssertions('export 3 (both brushes hidden)', ex3, [
                ['overlap pixel is base color', PT_OVERLAP, BASE_COLOR],
                ['red-only pixel is base color', PT_RED_ONLY, BASE_COLOR],
                ['blue-only pixel is base color', PT_BLUE_ONLY, BASE_COLOR]
            ]);
            assertion('export button stays enabled while the base layer is visible',
                document.getElementById('layerExportBtn').disabled === false,
                document.getElementById('layerExportBtn').disabled);
        } catch(error){
            result.errors.push('export 3: ' + error.message);
            return;
        }

        // 产品逻辑：只要还有任何可见层（含空白层）导出按钮就保持启用，
        // 所以这里必须把四个层全部隐藏才应看到 disabled=true。
        const allHidden = await waitFor(async () => {
            await Promise.all([redId, blueId, blankId, 'layer-base'].map(id => toggleHidden(id, true)));
            return rowIds().every(id => rowOf(id)?.classList.contains('layer-hidden'));
        }, 5000);
        assertion('hiding every layer disables the export button',
            allHidden && document.getElementById('layerExportBtn').disabled === true,
            {hiddenRows: rowIds().map(id => [id, rowOf(id)?.classList.contains('layer-hidden')]), disabled: document.getElementById('layerExportBtn').disabled});

        const allRestored = await waitFor(async () => {
            await Promise.all(['layer-base', redId, blueId, blankId].map(id => toggleHidden(id, false)));
            return rowIds().every(id => !rowOf(id)?.classList.contains('layer-hidden'));
        }, 5000);
        assertion('re-showing all layers re-enables export',
            allRestored && document.getElementById('layerExportBtn').disabled === false,
            {hiddenRows: rowIds().map(id => [id, rowOf(id)?.classList.contains('layer-hidden')]), disabled: document.getElementById('layerExportBtn').disabled});
        if(!allRestored) return;

        // --- 拖拽重排：蓝行拖到红行之下（红升顶） -> 导出 4；再拖回顶部 -> 导出 5 ---
        trace('drag-reorder');
        dragRow(blueId, redId, true);
        const reordered = await waitFor(() => JSON.stringify(rowIds()) === JSON.stringify([blankId, redId, blueId, 'layer-base']), 3000);
        assertion('dragging blue below red reorders the panel', reordered, rowIds());

        let ex4;
        try {
            ex4 = await captureCompositeExport();
            pixelAssertions('export 4 (red above blue after drag)', ex4, [
                ['overlap pixel is red (red now on top)', PT_OVERLAP, RED],
                ['red-only pixel stays red', PT_RED_ONLY, RED],
                ['blue-only pixel stays blue', PT_BLUE_ONLY, BLUE],
                ['untouched pixel keeps base color', PT_BASE_ONLY, BASE_COLOR]
            ]);
        } catch(error){
            result.errors.push('export 4: ' + error.message);
            return;
        }

        dragRow(blueId, blankId, false);
        const restoredOrder = await waitFor(() => JSON.stringify(rowIds()) === JSON.stringify([blueId, blankId, redId, 'layer-base']), 3000);
        assertion('dragging blue back to top restores the initial order', restoredOrder, rowIds());

        let ex5;
        try {
            ex5 = await captureCompositeExport();
            pixelAssertions('export 5 (round trip, blue top again)', ex5, [
                ['overlap pixel is blue again', PT_OVERLAP, BLUE],
                ['red-only pixel is red', PT_RED_ONLY, RED],
                ['blue-only pixel is blue', PT_BLUE_ONLY, BLUE]
            ]);
        } catch(error){
            result.errors.push('export 5: ' + error.message);
            return;
        }

        trace('run-done');
    }

    run().catch(error => result.errors.push(error?.stack || String(error))).finally(postResult);
})();
