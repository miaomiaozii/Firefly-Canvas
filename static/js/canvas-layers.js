// #255 图像分层 + 图层面板：ComfyArt src/layers.ts 纯函数 MIT 移植 + 图层 store / 合成 / 面板。
//
// Copyright (c) 2026 Terry Jia
// SPDX-License-Identifier: MIT
// 完整版权声明见 NOTICE.md（保留原 copyright 行）。
//
// 移植对照（源：ComfyArt v1.1.0 @ 13396a8 src/layers.ts，275 行；TS → JS 逐式对照，逻辑零改动）：
//   - measure(media,data,orig)          → measureLayer(data, orig)       去掉 media 依赖，cover/solid/diff 语义保留
//   - overlap(a,b)                      → layerOverlap(a, b)             IoU（alpha>OPAQUE）保留
//   - cropLayer 的 bbox 扫描             → layerAlphaBBox / cropLayerData  （uploadImage 上传部分不迁，改返回像素缓冲）
//   - separateRegions 像素循环           → splitLayerByBoxes               （分数坐标 box → 区域像素 + 剩余像素，原缓冲就地清零）
//   - regionSplit 掩膜合成像素循环       → composeWithMask                 （mask≥128 保留原像素、alpha 置 255、union 累积）
// 未移植（超出本票范围，见 NOTICE.md）：registerPlacer('replace'/'layers'/'regions')（依赖 tldraw
// editor + comfy 后端）、grow（canvas blur 膨胀）、canvasOf/pixels（纯 DOM 工具，我方直接走 2d context）。
// 新增（非移植）：compositeLayers（alpha-over 按层序合成，数组末位=最上层）、图层 store 纯操作、DOM 面板。
//
// 约束遵守：
//   - 不引用 smart-canvas.js 的任何顶层名（棘轮 0/0）；
//   - 顶层零声明（UMD IIFE），不污染全局作用域，唯一对外名字是 window.CanvasLayers。
(function (root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.CanvasLayers = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const SOLID = 200;      // 源码常量：alpha > 200 才算"实色"
    const OPAQUE = 128;     // 源码常量：alpha > 128 才算"覆盖"
    const EMPTY_ALPHA = 8;  // 源码 cropLayer：alpha <= 8 视为空像素

    // ===== 移植自 layers.ts 的纯函数（可 node 单测） =====

    // 源码 measure：cover = 覆盖占比；diff = 实色像素相对原图的 RGB 平均差；无实色像素时 diff=999（哨兵）。
    function measureLayer(data, orig) {
        let covered = 0, solid = 0, diff = 0;
        for (let i = 0; i < data.length; i += 4) {
            if (data[i + 3] > OPAQUE) covered++;
            if (data[i + 3] <= SOLID) continue;
            solid++;
            diff += (Math.abs(data[i] - orig[i]) + Math.abs(data[i + 1] - orig[i + 1]) + Math.abs(data[i + 2] - orig[i + 2])) / 3;
        }
        return { cover: covered / (data.length / 4), solid, diff: solid ? diff / solid : 999 };
    }

    // 源码 overlap：两层覆盖像素的 IoU。
    function layerOverlap(a, b) {
        let inter = 0, union = 0;
        for (let i = 3; i < a.length; i += 4) {
            const x = a[i] > OPAQUE;
            const y = b[i] > OPAQUE;
            if (x && y) inter++;
            if (x || y) union++;
        }
        return union ? inter / union : 0;
    }

    // 源码 cropLayer 的 bbox 扫描部分（alpha <= 8 视为空）。
    function layerAlphaBBox(data, w, h) {
        let l = w, t = h, r = -1, b = -1;
        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                if (data[(y * w + x) * 4 + 3] <= EMPTY_ALPHA) continue;
                if (x < l) l = x;
                if (x > r) r = x;
                if (y < t) t = y;
                if (y > b) b = y;
            }
        }
        if (r < 0) return null;
        return { x: l, y: t, w: r - l + 1, h: b - t + 1 };
    }

    // 源码 cropLayer 的裁剪部分：返回紧致 bbox 内的像素（不上传，返回纯缓冲）。
    function cropLayerData(data, w, h) {
        const box = layerAlphaBBox(data, w, h);
        if (!box) return null;
        const out = new Uint8ClampedArray(box.w * box.h * 4);
        for (let y = 0; y < box.h; y++) {
            const srcStart = ((box.y + y) * w + box.x) * 4;
            out.set(data.subarray(srcStart, srcStart + box.w * 4), y * box.w * 4);
        }
        return { data: out, x: box.x, y: box.y, w: box.w, h: box.h };
    }

    // 源码 separateRegions 的像素循环：分数坐标 box 逐个切区域，被消费的像素在原缓冲清零；
    // 返回 { parts: 各区域裁剪结果, rest: 剩余区裁剪结果（可能为 null） }。
    function splitLayerByBoxes(data, w, h, boxes) {
        const rest = data; // 源码就地修改语义
        const parts = [];
        for (const [bx, by, bw, bh] of boxes) {
            const buf = new Uint8ClampedArray(w * h * 4);
            for (let y = Math.round(by * h); y < Math.min(h, Math.round((by + bh) * h)); y++) {
                for (let x = Math.round(bx * w); x < Math.min(w, Math.round((bx + bw) * w)); x++) {
                    const p = (y * w + x) * 4;
                    if (rest[p + 3] <= EMPTY_ALPHA) continue;
                    buf.set(rest.subarray(p, p + 4), p);
                    rest[p + 3] = 0;
                }
            }
            const part = cropLayerData(buf, w, h);
            if (part) parts.push(part);
        }
        return { parts, rest: cropLayerData(rest, w, h) };
    }

    // 源码 regionSplit 的掩膜合成像素循环：mask≥128 处保留原图像素（alpha 置 255），union 累积掩膜。
    function composeWithMask(orig, mask, crop) {
        const w = orig.length / 4;
        const h = mask.length / 4;
        const data = new Uint8ClampedArray(w * h * 4);
        const union = new Uint8ClampedArray(w * h * 4);
        for (let y = crop.y; y < crop.y + crop.h && y < h; y++) {
            for (let x = crop.x; x < crop.x + crop.w && x < w; x++) {
                const p = (y * w + x) * 4;
                if (mask[p] < 128) continue;
                data.set(orig.subarray(p, p + 3), p);
                data[p + 3] = 255;
                union[p + 3] = 255;
            }
        }
        return { data, union };
    }

    // ===== 新增：按层序合成 + 图层 store（纯） =====

    // alpha-over 合成：layers 按底→顶顺序，数组末位在最上层；visible=false 跳过；尺寸不符跳过。
    function compositeLayers(layers, w, h) {
        const out = new Uint8ClampedArray(w * h * 4);
        for (const layer of layers) {
            if (!layer || layer.visible === false || !layer.data) continue;
            const data = layer.data;
            if (data.length !== out.length) continue;
            for (let p = 0; p < out.length; p += 4) {
                const a = data[p + 3] / 255;
                if (!a) continue;
                const ia = 1 - a;
                out[p] = data[p] * a + out[p] * ia;
                out[p + 1] = data[p + 1] * a + out[p + 1] * ia;
                out[p + 2] = data[p + 2] * a + out[p + 2] * ia;
                out[p + 3] = data[p + 3] + out[p + 3] * ia;
            }
        }
        return { width: w, height: h, data: out };
    }

    function createLayerStore() { return { layers: [] }; }
    function storeAddLayer(store, layer) { return { layers: [...store.layers, layer] }; }
    function storeRemoveLayer(store, id) { return { layers: store.layers.filter(l => l.id !== id) }; }
    // 缺失 visible 按可见处理（!l.visible 会把 undefined 误判为隐藏）
    function storeToggleLayer(store, id) { return { layers: store.layers.map(l => l.id === id ? { ...l, visible: l.visible === false } : l) }; }
    function storeMoveLayer(store, id, toIndex) {
        const from = store.layers.findIndex(l => l.id === id);
        if (from < 0 || toIndex < 0 || toIndex >= store.layers.length) return store;
        const layers = store.layers.slice();
        const [item] = layers.splice(from, 1);
        layers.splice(toIndex, 0, item);
        return { layers };
    }
    // orderIds 为完整的新底→顶顺序；缺 id / 数量不符 → 原样返回。
    function storeReorder(store, orderIds) {
        if (!Array.isArray(orderIds) || orderIds.length !== store.layers.length) return store;
        const byId = new Map(store.layers.map(l => [l.id, l]));
        const layers = orderIds.map(id => byId.get(id));
        if (layers.some(l => !l)) return store;
        return { layers };
    }
    function visibleLayers(store) { return store.layers.filter(l => l.visible !== false); }

    // ===== DOM 层（仅浏览器执行；纯函数以上已全部单测覆盖） =====

    let store = null;      // {layers: [...]} 底→顶
    let baseKey = '';      // 当前底图标识（src + 尺寸），变化时重建 store
    let layerSeq = 0;

    function t(key) {
        const i18n = typeof window !== 'undefined' ? window.StudioI18n : null;
        return i18n?.t?.(key) || key;
    }

    function refreshIcons() {
        const core = typeof window !== 'undefined' ? window.canvasCore : null;
        core?.refreshIcons?.();
    }

    // 进入 layers 模式时由 canvas-image-editor.js 的 setImageEditMode 调用。
    function onEditMode(mode) {
        if (typeof document === 'undefined') return;
        if (mode !== 'layers') return;
        ensureStore();
        renderList();
    }

    function baseCanvasFromCurrentImage() {
        const img = document.getElementById('cropImage');
        if (!img || !img.naturalWidth || !img.naturalHeight) return null;
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(img, 0, 0);
        return { data: ctx.getImageData(0, 0, canvas.width, canvas.height).data, width: canvas.width, height: canvas.height };
    }

    function ensureStore() {
        const base = baseCanvasFromCurrentImage();
        const key = base ? `${document.getElementById('cropImage').src}:${base.width}x${base.height}` : '';
        if (store && baseKey === key) return true;
        if (!base) return false;
        store = createLayerStore();
        store = storeAddLayer(store, {
            id: 'layer-base',
            name: t('canvas.layersBase'),
            data: base.data,
            width: base.width,
            height: base.height,
            visible: true,
            base: true,
        });
        baseKey = key;
        layerSeq = 0;
        return true;
    }

    function baseName() {
        const img = document.getElementById('cropImage');
        const editorImage = typeof currentEditImage === 'function' ? currentEditImage()?.image : null;
        const raw = (editorImage?.name || img?.getAttribute?.('alt') || 'image').replace(/\.[^.]+$/, '');
        return raw || 'image';
    }

    function addBlankLayer() {
        if (!ensureStore()) return;
        const b = store.layers[0];
        const n = store.layers.length;
        store = storeAddLayer(store, {
            id: `layer-${++layerSeq}`,
            name: `${t('canvas.layersBlank')} ${n}`,
            data: new Uint8ClampedArray(b.width * b.height * 4),
            width: b.width,
            height: b.height,
            visible: true,
        });
        renderList();
    }

    // 把当前画笔/遮罩画布的已绘制像素导入为顶层图层（显示尺寸 → 原图尺寸缩放）。
    function importBrushLayer() {
        if (!ensureStore()) return;
        const draw = document.getElementById('editDrawCanvas');
        if (!draw || !draw.width || !draw.height) return;
        const src = draw.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, draw.width, draw.height).data;
        let hasPixels = false;
        for (let i = 3; i < src.length; i += 4) { if (src[i] > EMPTY_ALPHA) { hasPixels = true; break; } }
        if (!hasPixels) return;
        const b = store.layers[0];
        const canvas = document.createElement('canvas');
        canvas.width = b.width; canvas.height = b.height;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(draw, 0, 0, b.width, b.height);
        store = storeAddLayer(store, {
            id: `layer-${++layerSeq}`,
            name: `${t('canvas.layersBrush')} ${store.layers.length}`,
            data: ctx.getImageData(0, 0, b.width, b.height).data,
            width: b.width,
            height: b.height,
            visible: true,
        });
        renderList();
    }

    function removeLayer(id) {
        if (!store || id === 'layer-base') return;
        store = storeRemoveLayer(store, id);
        renderList();
    }

    function toggleLayer(id) {
        if (!store) return;
        store = storeToggleLayer(store, id);
        renderList();
    }

    // 显示序（顶→底）上移/下移一步。
    function nudgeLayer(id, displayDelta) {
        if (!store) return;
        const n = store.layers.length;
        const a = store.layers.findIndex(l => l.id === id);
        if (a < 0) return;
        const target = a + displayDelta; // 显示上移 = 数组 +1（数组末位=顶）
        if (target < 0 || target >= n) return;
        store = storeMoveLayer(store, id, target);
        renderList();
    }

    // DnD：displayFrom 为被拖行的显示下标，displayTo 为插入点的显示下标（0..n）。
    function reorderDisplay(displayFrom, displayTo) {
        if (!store) return;
        const idsTopDown = store.layers.map(l => l.id).reverse();
        if (displayFrom < 0 || displayFrom >= idsTopDown.length) return;
        const [moved] = idsTopDown.splice(displayFrom, 1);
        let to = displayTo;
        if (displayFrom < displayTo) to -= 1; // 移入自身原位置之后时插入点前移
        to = Math.max(0, Math.min(idsTopDown.length, to));
        idsTopDown.splice(to, 0, moved);
        store = storeReorder(store, idsTopDown.reverse());
        renderList();
    }

    let dragLayerId = null;
    function onRowDragStart(event, id) {
        dragLayerId = id;
        event.dataTransfer?.setData('text/plain', id);
        if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
    }
    function onRowDragOver(event, id) {
        if (!dragLayerId || !store) return;
        event.preventDefault();
        const row = event.currentTarget;
        const rect = row.getBoundingClientRect();
        const before = (event.clientY - rect.top) < rect.height / 2;
        const idsTopDown = store.layers.map(l => l.id).reverse();
        const from = idsTopDown.indexOf(dragLayerId);
        const over = idsTopDown.indexOf(id);
        if (from < 0 || over < 0) return;
        const to = before ? over : over + 1;
        if (from === to || from + 1 === to) return;
        reorderDisplay(from, to);
    }
    function onListDragOver(event) {
        if (dragLayerId) event.preventDefault();
    }
    function onRowDragEnd() {
        dragLayerId = null;
    }

    function renderList() {
        if (typeof document === 'undefined') return;
        const list = document.getElementById('layerList');
        if (!list) return;
        list.textContent = '';
        if (!store || !store.layers.length) {
            const empty = document.createElement('span');
            empty.className = 'layer-empty image-edit-sub';
            empty.textContent = t('canvas.layersEmpty');
            list.appendChild(empty);
            syncExportButton();
            return;
        }
        // 显示序顶→底
        const rows = [...store.layers].reverse();
        rows.forEach((layer, displayIndex) => {
            const row = document.createElement('div');
            row.className = 'layer-row' + (layer.visible === false ? ' layer-hidden' : '');
            row.draggable = true;
            row.dataset.layerId = layer.id;
            row.addEventListener('dragstart', (e) => onRowDragStart(e, layer.id));
            row.addEventListener('dragover', (e) => onRowDragOver(e, layer.id));
            row.addEventListener('dragend', onRowDragEnd);

            const drag = document.createElement('span');
            drag.className = 'layer-drag';
            drag.innerHTML = '<i data-lucide="grip-vertical" class="w-3.5 h-3.5"></i>';

            const vis = document.createElement('button');
            vis.type = 'button';
            vis.className = 'image-edit-btn secondary layer-vis';
            vis.title = t('canvas.layersVisibility');
            vis.innerHTML = `<i data-lucide="${layer.visible === false ? 'eye-off' : 'eye'}" class="w-3.5 h-3.5"></i>`;
            vis.addEventListener('click', () => toggleLayer(layer.id));

            const name = document.createElement('span');
            name.className = 'layer-name';
            name.textContent = layer.name;

            const up = document.createElement('button');
            up.type = 'button';
            up.className = 'image-edit-btn secondary layer-nudge';
            up.title = t('canvas.layersMoveUp');
            up.innerHTML = '<i data-lucide="chevron-up" class="w-3.5 h-3.5"></i>';
            up.addEventListener('click', () => nudgeLayer(layer.id, 1));

            const down = document.createElement('button');
            down.type = 'button';
            down.className = 'image-edit-btn secondary layer-nudge';
            down.title = t('canvas.layersMoveDown');
            down.innerHTML = '<i data-lucide="chevron-down" class="w-3.5 h-3.5"></i>';
            down.addEventListener('click', () => nudgeLayer(layer.id, -1));

            const del = document.createElement('button');
            del.type = 'button';
            del.className = 'image-edit-btn secondary layer-del';
            del.title = t('canvas.layersRemove');
            del.innerHTML = '<i data-lucide="x" class="w-3.5 h-3.5"></i>';
            if (layer.base) del.disabled = true;
            del.addEventListener('click', () => removeLayer(layer.id));

            row.append(drag, vis, name, up, down, del);
            list.appendChild(row);
        });
        list.addEventListener('dragover', onListDragOver);
        syncExportButton();
        refreshIcons();
    }

    function syncExportButton() {
        const btn = document.getElementById('layerExportBtn');
        if (!btn || !store) return;
        btn.disabled = visibleLayers(store).length === 0;
    }

    // 离屏 canvas 按层序（底→顶）合成可见层 → 导出 PNG（浏览器下载）。
    function exportCompositePng() {
        if (typeof document === 'undefined' || !store) return;
        const b = store.layers[0];
        const comp = compositeLayers(visibleLayers(store), b.width, b.height);
        const canvas = document.createElement('canvas');
        canvas.width = b.width; canvas.height = b.height;
        canvas.getContext('2d').putImageData(new ImageData(comp.data, b.width, b.height), 0, 0);
        canvas.toBlob(async (blob) => {
            if (!blob) return;
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${baseName()}_layers.png`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 5000);
        }, 'image/png');
    }

    // 打开/关闭面板由编辑器按模式切换（#imageLayersTools.active）；这里只做状态重置钩子。
    function reset() {
        store = null;
        baseKey = '';
        layerSeq = 0;
        dragLayerId = null;
    }

    // ===== i18n（中英） =====
    if (typeof window !== 'undefined' && window.StudioI18n?.register) {
        window.StudioI18n.register({
            'canvas.layersMode': { zh: '图层', en: 'Layers' },
            'canvas.layersTitle': { zh: '图层编辑', en: 'Layer editing' },
            'canvas.layersHint': { zh: '调整图层显隐与顺序，导出合成 PNG', en: 'Toggle and reorder layers, then export a composited PNG' },
            'canvas.layersAddBlank': { zh: '空白图层', en: 'Add blank layer' },
            'canvas.layersImportBrush': { zh: '导入笔刷', en: 'Import brush layer' },
            'canvas.layersExport': { zh: '导出合成 PNG', en: 'Export PNG' },
            'canvas.layersBase': { zh: '底图', en: 'Base image' },
            'canvas.layersBlank': { zh: '空白图层', en: 'Blank layer' },
            'canvas.layersBrush': { zh: '笔刷图层', en: 'Brush layer' },
            'canvas.layersVisibility': { zh: '显示 / 隐藏', en: 'Toggle visibility' },
            'canvas.layersMoveUp': { zh: '上移', en: 'Move up' },
            'canvas.layersMoveDown': { zh: '下移', en: 'Move down' },
            'canvas.layersRemove': { zh: '删除图层', en: 'Remove layer' },
            'canvas.layersEmpty': { zh: '暂无图层：先切到画笔/遮罩模式绘制，再导入', en: 'No layers yet — draw with the brush, then import' },
        });
    }

    return {
        // 纯函数（移植 + 新增）
        SOLID, OPAQUE, EMPTY_ALPHA,
        measureLayer, layerOverlap, layerAlphaBBox, cropLayerData, splitLayerByBoxes, composeWithMask,
        compositeLayers,
        createLayerStore, storeAddLayer, storeRemoveLayer, storeToggleLayer, storeMoveLayer, storeReorder, visibleLayers,
        // DOM 面板
        onEditMode, addBlankLayer, importBrushLayer, exportCompositePng, reset, renderList,
    };
});
