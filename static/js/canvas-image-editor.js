// 画布图片编辑器：裁剪、旋转、预览导航与对比（从 smart-canvas.js 抽离，#154）。
// invariant: classic script，按 HTML 顺序先于 smart-canvas.js 加载；本文件顶层只有声明，
// 初始化器只取 DOM 元素或空容器，不读其他模块的状态。函数声明整体提升，实际调用都发生在
// 主脚本初始化之后。跨模块可变状态一律走 canvasState（window.canvasState），不要引用裸 nodes/selectedId。

const imageEditModal = document.getElementById('imageEditModal');

let releaseImageEditorEscape = () => {};
let imageEditorSession = null;

let cropState = null;

let cropDrag = null;

let cropAspectPreset = 'free';

let cropAspectRatio = null;

let outpaintAspectPreset = 'free';

let imageEditMode = 'crop';

let imageResizeScale = 0.5;

let editDrawState = null;

let editTextItems = [];

let editTextSelectedId = '';

let editTextDrag = null;

let editTextDirty = false;

let editTextInlineEditor = null;

let editDrawUndoStack = [];

let editDrawRedoStack = [];

const EDIT_DRAW_HISTORY_MAX = 40;

let brushTool = 'free';

let brushLabelCounter = 1;

let gridCustomMode = false;

let gridCustomLines = [];

let gridCustomOrientation = 'h';

let gridCustomHistory = [];

let gridCustomDrag = null;

let gridOperationMode = 'split';

let gridJoinLayout = null;

let gridJoinDrag = null;

let gridJoinImageCache = new Map();

let gridJoinUserMoved = false;

let gridJoinOutputSize = 2048;

// 非空时表示当前宫格拼接的数据源是整个分组（聚合组内所有图片成员），而不是单个节点。
let gridJoinGroupId = '';

let imageEditZoom = 1.0;

let imageEditBaseW = 0;

let imageEditBaseH = 0;

let imageEditPanDrag = null;

function smartImageEditorIsOpen(){
    return Boolean(imageEditModal?.classList?.contains('open'));
}

function validOutpaintSize(node){
    const w = Math.round(Number(node?.outpaintSize?.width || 0));
    const h = Math.round(Number(node?.outpaintSize?.height || 0));
    if(w <= 0 || h <= 0) return null;
    const selected = String(node?.outpaintSize?.aspectRatio || '').trim();
    return {width:w, height:h, aspectRatio:/^\d+\s*:\s*\d+$/.test(selected) ? selected.replace(/\s/g, '') : canvasCore.reducedAspectRatio(w, h)};
}

function withOutpaintDisplaySettings(node, baseSettings){
    const size = validOutpaintSize(node);
    if(!size) return baseSettings;
    const engine = normalizeSmartOnlineEngine(baseSettings?.engine);
    // 扩图时不设置 resolution:'custom'，保留用户选择的分辨率预设（4K/2K/1K）
    // 只传递比例（如 4:3），让 API 根据比例 + 分辨率自动计算尺寸
    const next = {
        ...baseSettings,
        ratio:'custom',
        customRatio:size.aspectRatio,
        outpaintResolutionLocked:true
    };
    if(canvasCore.isApiLikeEngine(engine)) next.apiKind = 'image';
    if(engine === 'modelscope'){
        next.msRatio = 'custom';
        next.msCustomRatio = size.aspectRatio;
    }
    return next;
}

function stripOutpaintDisplaySettings(settingsObj, node=null){
    const clean = cloneSmartSettings(settingsObj);
    const size = validOutpaintSize(node);
    const matchesOutpaintSize = size && clean.resolution === 'custom' && String(clean.customSize || '') === `${size.width}x${size.height}`;
    if(matchesOutpaintSize){
        clean.resolution = '1k';
        clean.ratio = clean.ratio || 'square';
        clean.customWidth = '';
        clean.customHeight = '';
        clean.customSize = '';
    }
    const matchesMsOutpaintSize = size && clean.msResolution === 'custom' && String(clean.msCustomSize || '') === `${size.width}x${size.height}`;
    if(matchesMsOutpaintSize){
        clean.msResolution = '1k';
        clean.msRatio = clean.msRatio || 'square';
        clean.msCustomWidth = '';
        clean.msCustomHeight = '';
        clean.msCustomSize = '';
    }
    if(size && Number(clean.width) === size.width && Number(clean.height) === size.height){
        clean.width = 1024;
        clean.height = 1024;
    }
    delete clean.outpaintResolutionLocked;
    return clean;
}

function groupImageGridLayout(count, explicitW, explicitH, maxThumb, pad=32, gap=8, maxVisibleRows=canvasCore.MEDIA_GROUP_MAX_VISIBLE_ROWS){
    let best = null;
    for(let cols = 1; cols <= count; cols++){
        const rows = Math.ceil(count / cols);
        const visibleRows = Math.min(Math.max(1, maxVisibleRows), rows);
        const availableW = explicitW - pad - (cols - 1) * gap;
        const availableH = explicitH - pad - (visibleRows - 1) * gap;
        if(availableW <= 0 || availableH <= 0) continue;
        const rawThumb = Math.floor(Math.min(availableW / cols, availableH / visibleRows));
        const fittedThumb = Math.max(28, Math.min(maxThumb, rawThumb));
        const fits = rawThumb >= 28;
        const usedW = cols * fittedThumb + (cols - 1) * gap + pad;
        const usedH = visibleRows * fittedThumb + (visibleRows - 1) * gap + pad;
        const spareW = Math.max(0, explicitW - usedW);
        const spareH = Math.max(0, explicitH - usedH);
        const atMax = fittedThumb >= maxThumb;
        // 完美网格优先：整除且行数不超可见上限（8→4×2 / 2×4、6→3×2、9→3×3），
        // 避免 3×2+2 这类缺角布局。
        const perfect = (count % cols === 0) && (rows <= maxVisibleRows) ? 1 : 0;
        const score = [
            perfect,
            fits ? 1 : 0,
            fittedThumb,
            atMax ? cols : 0,
            -(spareW + spareH * 0.35),
            -rows
        ];
        let better = !best;
        if(best){
            for(let i = 0; i < score.length; i++){
                if(score[i] === best.score[i]) continue;
                better = score[i] > best.score[i];
                break;
            }
        }
        if(better){
            best = {cols, rows, visibleRows, thumb:fittedThumb, score};
        }
    }
    const fallbackCols = Math.min(count, 2);
    const fallbackRows = Math.ceil(count / fallbackCols);
    return best || {cols:fallbackCols, rows:fallbackRows, visibleRows:Math.min(canvasCore.MEDIA_GROUP_MAX_VISIBLE_ROWS, fallbackRows), thumb:28};
}

function cropImageDisplaySize(){
    const img = document.getElementById('cropImage');
    const clientW = Number(img?.clientWidth || 0);
    const clientH = Number(img?.clientHeight || 0);
    if(clientW > 2 && clientH > 2) return {w:clientW, h:clientH};
    ensureImageEditBaseSize();
    const fallbackW = Math.round((imageEditBaseW || Number(img?.naturalWidth || 0) || 1) * imageEditZoom);
    const fallbackH = Math.round((imageEditBaseH || Number(img?.naturalHeight || 0) || 1) * imageEditZoom);
    return {w:Math.max(1, fallbackW), h:Math.max(1, fallbackH)};
}

function cropBounds(){
    return cropImageDisplaySize();
}

function editDrawCanvas(){ return document.getElementById('editDrawCanvas'); }

function editTextCanvas(){ return document.getElementById('editTextCanvas'); }

function editTextContext(){ return editTextCanvas()?.getContext('2d') || null; }

function selectedEditTextItem(){ return editTextItems.find(item => item.id === editTextSelectedId) || null; }

function defaultEditTextText(){ return window.StudioI18n?.lang?.() === 'en' ? 'Double-click to edit' : '双击编辑'; }

function editTextSizeFromBrush(){ return Math.max(14, Math.min(120, Math.round(editBrushSize() * 2))); }

function createEditTextItem(text, point, preset={}){
    const size = Math.max(10, Math.min(120, Number(preset.size) || editTextSizeFromBrush()));
    return {id:uid('txt'), text:String(text || defaultEditTextText()).trim(), x:Number(point?.x || 0), y:Number(point?.y || 0), color:preset.color || brushColor(), size};
}

function measureEditTextItem(item, ctx=editTextContext()){
    if(!item || !ctx) return {x:0, y:0, w:0, h:0};
    const size = Math.max(10, Math.min(120, Number(item.size) || 28));
    ctx.save();
    ctx.font = textItemFont(item);
    const metrics = ctx.measureText(String(item.text || ''));
    ctx.restore();
    const width = Math.max(1, metrics.width || 1);
    const ascent = Number.isFinite(metrics.actualBoundingBoxAscent) ? metrics.actualBoundingBoxAscent : size * 0.8;
    const descent = Number.isFinite(metrics.actualBoundingBoxDescent) ? metrics.actualBoundingBoxDescent : size * 0.25;
    const pad = Math.max(4, Math.round(size * 0.18));
    return {x:item.x - width / 2 - pad, y:item.y - (ascent + descent) / 2 - pad, w:width + pad * 2, h:ascent + descent + pad * 2, textW:width, textH:ascent + descent, pad};
}

function hitEditTextItem(point){
    const ctx = editTextContext();
    if(!ctx) return null;
    for(let i = editTextItems.length - 1; i >= 0; i--){
        const item = editTextItems[i];
        const box = measureEditTextItem(item, ctx);
        if(point.x >= box.x && point.x <= box.x + box.w && point.y >= box.y && point.y <= box.y + box.h) return item;
    }
    return null;
}

function renderEditTextCanvas(){
    const canvasEl = editTextCanvas();
    const ctx = editTextContext();
    if(!canvasEl || !ctx) return;
    ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
    editTextItems.forEach(item => {
        if(!item?.text) return;
        const selected = item.id === editTextSelectedId;
        const box = measureEditTextItem(item, ctx);
        ctx.save();
        ctx.font = textItemFont(item);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = item.color || brushColor();
        ctx.strokeStyle = 'rgba(255,255,255,.92)';
        ctx.lineWidth = Math.max(2, (Number(item.size) || 28) / 8);
        ctx.strokeText(String(item.text || ''), item.x, item.y);
        ctx.fillText(String(item.text || ''), item.x, item.y);
        if(selected){
            ctx.setLineDash([7, 5]);
            ctx.lineWidth = 1.5;
            ctx.strokeStyle = getComputedStyle(canvasEl).getPropertyValue('--shadow-color').trim();
            ctx.globalAlpha = .72;
            ctx.strokeRect(box.x, box.y, box.w, box.h);
            ctx.setLineDash([]);
            ctx.fillStyle = getComputedStyle(canvasEl).getPropertyValue('--shadow-color').trim();
            ctx.globalAlpha = .92;
            ctx.beginPath();
            ctx.arc(item.x + box.w / 2 - box.pad, item.y - box.h / 2 + box.pad, 3.5, 0, Math.PI * 2);
            ctx.fill();
        }
        ctx.restore();
    });
    positionEditTextInlineEditor();
}

function syncTextToolState(force=false){
    const cropCanvasEl = document.getElementById('cropCanvas');
    cropCanvasEl?.classList.toggle('text-mode', imageEditMode === 'brush' && brushTool === 'text');
}

function syncSelectedEditTextStyleFromBrush(){
    if(imageEditMode !== 'brush' || brushTool !== 'text' || editTextInlineEditor) return;
    const item = selectedEditTextItem();
    if(!item) return;
    const nextSize = editTextSizeFromBrush();
    const nextColor = brushColor();
    if(item.size === nextSize && item.color === nextColor) return;
    beginTextEditChange();
    item.size = nextSize;
    item.color = nextColor;
    renderEditTextCanvas();
    syncTextToolState(true);
}

function setSelectedEditTextItem(id){
    editTextSelectedId = id || '';
    renderEditTextCanvas();
    syncTextToolState(true);
}

function confirmSelectedEditTextItem(){
    const selected = selectedEditTextItem();
    if(!selected) return false;
    if(!String(selected.text || '').trim()) editTextItems = editTextItems.filter(item => item.id !== selected.id);
    editTextSelectedId = '';
    editTextDrag = null;
    editTextDirty = false;
    renderEditTextCanvas();
    syncTextToolState(true);
    return true;
}

function editTextCanvasScale(){
    const canvasEl = editTextCanvas();
    const rect = canvasEl?.getBoundingClientRect?.();
    return {x:(rect?.width || canvasEl?.width || 1) / Math.max(1, canvasEl?.width || 1), y:(rect?.height || canvasEl?.height || 1) / Math.max(1, canvasEl?.height || 1), rect};
}

function positionEditTextInlineEditor(){
    const editor = editTextInlineEditor;
    if(!editor?.el) return;
    const item = editTextItems.find(x => x.id === editor.itemId);
    const canvasEl = editTextCanvas();
    const cropCanvasEl = document.getElementById('cropCanvas');
    if(!item || !canvasEl || !cropCanvasEl) return;
    const box = measureEditTextItem(item, editTextContext());
    const scale = editTextCanvasScale();
    const hostRect = cropCanvasEl.getBoundingClientRect();
    const canvasRect = scale.rect || canvasEl.getBoundingClientRect();
    const left = canvasRect.left - hostRect.left + box.x * scale.x;
    const top = canvasRect.top - hostRect.top + box.y * scale.y;
    const w = Math.max(48, box.w * scale.x);
    const h = Math.max(28, box.h * scale.y);
    editor.minW = w;
    editor.minH = h;
    editor.el.style.left = `${left}px`;
    editor.el.style.top = `${top}px`;
    editor.el.style.minWidth = `${w}px`;
    editor.el.style.minHeight = `${h}px`;
    editor.el.style.font = `900 ${Math.max(10, (Number(item.size) || 28) * scale.y)}px Arial, sans-serif`;
    editor.el.style.color = item.color || brushColor();
    autosizeEditTextInlineEditor();
}

function removeEditTextInlineEditor(commit=true){
    const editor = editTextInlineEditor;
    if(!editor) return;
    const item = editTextItems.find(x => x.id === editor.itemId);
    const next = inlineEditorText().trim();
    editTextInlineEditor = null;
    editor.el.remove();
    if(!item) return;
    if(commit){
        if(next !== String(editor.before || '')){
            beginTextEditChange();
            if(next) item.text = next;
            else {
                editTextItems = editTextItems.filter(x => x.id !== item.id);
                editTextSelectedId = '';
            }
        }
    } else {
        item.text = editor.before || item.text || defaultEditTextText();
    }
    editTextDirty = false;
    renderEditTextCanvas();
    syncTextToolState(true);
}

function beginEditTextInline(item){
    if(!item) return;
    removeEditTextInlineEditor(true);
    editTextSelectedId = item.id;
    const host = document.getElementById('cropCanvas');
    if(!host) return;
    const el = document.createElement('div');
    el.className = 'edit-text-inline';
    el.contentEditable = 'true';
    el.spellcheck = false;
    el.textContent = item.text || defaultEditTextText();
    host.appendChild(el);
    editTextInlineEditor = {el, itemId:item.id, before:item.text || ''};
    positionEditTextInlineEditor();
    el.addEventListener('input', autosizeEditTextInlineEditor);
    el.addEventListener('keydown', event => {
        if(event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229){ event.preventDefault(); removeEditTextInlineEditor(true); } // #252：IME 候选确认回车不提交行内编辑
        else if(event.key === 'Escape'){ event.preventDefault(); removeEditTextInlineEditor(false); }
    });
    el.addEventListener('blur', () => removeEditTextInlineEditor(true));
    requestAnimationFrame(() => { el.focus(); selectInlineEditorText(el); });
    renderEditTextCanvas();
    syncTextToolState(true);
}

function editTextPoint(event){ return editDrawPoint(event); }

function beginEditText(event){
    if(imageEditMode !== 'brush' || brushTool !== 'text') return;
    event.preventDefault(); event.stopPropagation();
    removeEditTextInlineEditor(true);
    const canvasEl = editTextCanvas();
    const point = editTextPoint(event);
    const hit = hitEditTextItem(point);
    if(hit){
        editTextSelectedId = hit.id;
        editTextDrag = {id:hit.id, pointerId:event.pointerId, startX:hit.x, startY:hit.y, sx:event.clientX, sy:event.clientY, moved:false, hasHistory:false};
        canvasEl.setPointerCapture?.(event.pointerId);
        canvasEl.style.cursor = 'grabbing';
        syncTextToolState(true);
        renderEditTextCanvas();
        return;
    }
    if(selectedEditTextItem()){
        confirmSelectedEditTextItem();
        return;
    }
    beginTextEditChange();
    const item = createEditTextItem(defaultEditTextText(), point, {color:brushColor(), size:editTextSizeFromBrush()});
    editTextItems.push(item);
    editTextSelectedId = item.id;
    canvasEl.style.cursor = 'text';
    renderEditTextCanvas();
    syncTextToolState(true);
}

function updateEditTextCursor(event){
    const canvasEl = editTextCanvas();
    if(!canvasEl || imageEditMode !== 'brush' || brushTool !== 'text') return;
    const hit = hitEditTextItem(editTextPoint(event));
    canvasEl.style.cursor = hit ? 'move' : 'text';
}

function moveEditText(event){
    if(!editTextDrag){
        updateEditTextCursor(event);
        return;
    }
    event.preventDefault(); event.stopPropagation();
    const item = editTextItems.find(x => x.id === editTextDrag.id);
    if(!item) return;
    const dx = event.clientX - editTextDrag.sx;
    const dy = event.clientY - editTextDrag.sy;
    if(!editTextDrag.moved && Math.abs(dx) + Math.abs(dy) < 2) return;
    editTextDrag.moved = true;
    if(!editTextDrag.hasHistory){
        beginTextEditChange();
        editTextDrag.hasHistory = true;
    }
    const canvasEl = editTextCanvas();
    const rect = canvasEl?.getBoundingClientRect?.();
    const scaleX = canvasEl ? canvasEl.width / Math.max(1, rect?.width || canvasEl.width) : 1;
    const scaleY = canvasEl ? canvasEl.height / Math.max(1, rect?.height || canvasEl.height) : 1;
    item.x = editTextDrag.startX + dx * scaleX;
    item.y = editTextDrag.startY + dy * scaleY;
    renderEditTextCanvas();
}

function endEditText(event){
    if(editTextDrag && event?.pointerId != null) editTextCanvas()?.releasePointerCapture?.(event.pointerId);
    editTextDrag = null;
    editTextDirty = false;
    renderEditTextCanvas();
    syncTextToolState(true);
    if(event) updateEditTextCursor(event);
}

function editTextHasContent(){ return editTextItems.some(item => String(item?.text || '').trim().length > 0); }

function resizeEditTextCanvas(){
    const img = document.getElementById('cropImage');
    const canvasEl = editTextCanvas();
    if(!img || !canvasEl) return;
    const display = cropImageDisplaySize();
    const w = Math.max(1, img.naturalWidth || img.clientWidth || 1);
    const h = Math.max(1, img.naturalHeight || img.clientHeight || 1);
    if(canvasEl.width !== w) canvasEl.width = w;
    if(canvasEl.height !== h) canvasEl.height = h;
    canvasEl.style.width = `${display.w}px`;
    canvasEl.style.height = `${display.h}px`;
    renderEditTextCanvas();
}

function resizeEditDrawCanvas(){
    const img = document.getElementById('cropImage');
    const canvasEl = editDrawCanvas();
    const display = cropImageDisplaySize();
    const w = Math.max(1, img.naturalWidth || img.clientWidth || 1);
    const h = Math.max(1, img.naturalHeight || img.clientHeight || 1);
    const changed = canvasEl.width !== w || canvasEl.height !== h;
    const snapshot = changed && isImageDrawingMode(imageEditMode) && (editDrawUndoStack.length || editTextItems.length)
        ? editDrawSnapshot() : null;
    if(changed){ canvasEl.width = w; canvasEl.height = h; }
    canvasEl.style.width = `${display.w}px`;
    canvasEl.style.height = `${display.h}px`;
    resizeEditTextCanvas();
    if(snapshot) restoreEditDrawSnapshot(snapshot);
    if(imageEditMode === 'grid') refreshGridSplitPreview();
}

function setImageEditMode(mode, _userTouched=false){
    const session = imageEditorSession;
    if(session?.applying || session?.discarding) return;
    const isVideoPreview = canvasCore.mediaKindForItem(currentEditImage().image || {}) === 'video';
    const next = !isVideoPreview && ['preview','crop','outpaint','mask','brush','resize','grid','layers'].includes(mode) ? mode : 'preview';
    const prev = imageEditMode;
    if(session && prev !== next){
        if(cropState.w && (prev === 'crop' || prev === 'outpaint')) session.boxes[prev] = imageEditorBoxFractions();
        if(isImageDrawingMode(prev)){
            removeEditTextInlineEditor(true);
            if(editDrawState) endEditDraw({pointerId:editDrawState.pointerId});
            if(editTextDrag) endEditText();
            if(editDrawUndoStack.length || editDrawRedoStack.length || editTextItems.length){
                const snapshot = editDrawSnapshot();
                session.drawings[prev] = {snapshot, dirty:editImageDataHasPixels(snapshot.imageData) || editTextHasContent(), undo:editDrawUndoStack, redo:editDrawRedoStack};
            }
        }
        if(isImageDrawingMode(prev) || prev === 'grid'){
            clearEditDrawing(true);
            resetEditDrawingHistory();
        }
        cropDrag = null;
        Object.assign(cropState, {x:0, y:0, w:0, h:0});
    }
    imageEditMode = next;
    if(prev !== next && prev === 'outpaint') renderCropBox();
    const cropCanvasEl = document.getElementById('cropCanvas');
    const previewStageEl = document.getElementById('previewStage');
    const editStageEl = document.getElementById('imageEditStage');
    const editPanelEl = document.querySelector('.image-edit-panel');
    const previewDownloadBtn = document.getElementById('previewDownloadBtn');
    const previewDownloadAllBtn = document.getElementById('previewDownloadAllBtn');
    const modeBar = document.querySelector('.image-edit-mode');
    const videoFrameTools = document.getElementById('videoFrameTools');
    const zoomLabel = document.getElementById('imageEditZoomLabel');
    const cancelBtn = document.getElementById('imageEditCancelBtn');
    const isPreview = imageEditMode === 'preview';
    cropCanvasEl.style.display = isPreview ? 'none' : '';
    previewStageEl.style.display = isPreview ? 'inline-flex' : 'none';
    editStageEl?.classList.toggle('preview-mode', isPreview);
    editPanelEl?.classList.toggle('video-preview-mode', isVideoPreview);
    if(previewDownloadBtn) previewDownloadBtn.style.display = isPreview ? 'inline-flex' : 'none';
    if(previewDownloadAllBtn) previewDownloadAllBtn.style.display = isPreview && !isVideoPreview && previewDownloadGroupItems().length > 1 ? 'inline-flex' : 'none';
    if(modeBar) modeBar.style.display = isVideoPreview ? 'none' : '';
    if(videoFrameTools) videoFrameTools.style.display = isVideoPreview && isPreview ? 'flex' : 'none';
    if(zoomLabel) zoomLabel.style.display = isVideoPreview ? 'none' : '';
    if(cancelBtn){
        cancelBtn.style.display = '';
        cancelBtn.textContent = isVideoPreview ? tr('common.close') : tr('common.cancel');
    }
    cropCanvasEl.classList.toggle('mask-mode', imageEditMode === 'mask');
    cropCanvasEl.classList.toggle('brush-mode', imageEditMode === 'brush');
    cropCanvasEl.classList.toggle('resize-mode', imageEditMode === 'resize');
    cropCanvasEl.classList.toggle('grid-mode', imageEditMode === 'grid');
    cropCanvasEl.classList.toggle('outpaint-mode', imageEditMode === 'outpaint');
    syncGridCustomCursor();
    document.querySelectorAll('[data-image-edit-mode]').forEach(btn => btn.classList.toggle('active', btn.dataset.imageEditMode === imageEditMode));
    document.getElementById('imagePreviewTools').classList.toggle('active', isPreview && !isVideoPreview);
    document.getElementById('imageCropTools')?.classList.toggle('active', imageEditMode === 'crop');
    document.getElementById('imageOutpaintTools')?.classList.toggle('active', imageEditMode === 'outpaint');
    document.getElementById('imageMaskTools').classList.toggle('active', imageEditMode === 'mask');
    document.getElementById('imageBrushTools').classList.toggle('active', imageEditMode === 'brush');
    document.getElementById('imageResizeTools')?.classList.toggle('active', imageEditMode === 'resize');
    document.getElementById('imageGridTools').classList.toggle('active', imageEditMode === 'grid');
    document.getElementById('imageLayersTools')?.classList.toggle('active', imageEditMode === 'layers');
    if(imageEditMode === 'grid' && gridOperationMode === 'join' && !canGridJoinCurrentNode()) gridOperationMode = 'split';
    syncGridOperationControls();
    updateImagePreviewMarkButton();
    syncGridGapValue();
    syncImageResizeControls();
    const applyBtn = document.getElementById('imageEditApplyBtn');
    document.getElementById('compareToggleBtn').style.display = isPreview && !isVideoPreview ? 'inline-flex' : 'none';
    document.getElementById('compareThumbs').style.display = 'none';
    if(isPreview){
        document.getElementById('imageEditTitle').textContent = isVideoPreview ? tr('smart.previewVideo') : tr('smart.previewImage');
        document.getElementById('imageEditSub').textContent = isVideoPreview ? '' : tr('smart.previewHint');
        applyBtn.style.display = 'none';
        refreshComparePanel();
    } else {
        if(session?.loaded){
            ensureImageEditBaseSize(true);
            applyImageEditZoom();
            if(prev !== next) initializeImageEditorBox();
        }
        applyBtn.style.display = '';
        if(imageEditMode === 'resize'){
            document.getElementById('imageEditTitle').textContent = tr('smart.zoomImage');
            document.getElementById('imageEditSub').textContent = tr('smart.zoomImageSub');
            applyBtn.innerHTML = `<svg class="ico w-4 h-4" aria-hidden="true"><use href="#i-minimize-2"/></svg><span>${escapeHtml(tr('smart.applyZoom'))}</span>`;
        } else if(imageEditMode === 'layers'){
            // #255 图层模式：导出走图层面板自己的按钮，隐藏主应用按钮。
            document.getElementById('imageEditTitle').textContent = tr('canvas.layersTitle');
            document.getElementById('imageEditSub').textContent = tr('canvas.layersHint');
            applyBtn.style.display = 'none';
        } else {
            const icon = imageEditMode === 'crop' ? 'crop' : imageEditMode === 'outpaint' ? 'expand' : imageEditMode === 'mask' ? 'inpaint' : imageEditMode === 'brush' ? 'paintbrush' : 'grid-3x3';
            const labelKey = imageEditMode === 'crop' ? 'canvas.applyCrop' : imageEditMode === 'outpaint' ? 'canvas.applyOutpaint' : imageEditMode === 'mask' ? 'canvas.applyMask' : imageEditMode === 'brush' ? 'canvas.applyBrush' : 'canvas.applyGrid';
            const titleKey = imageEditMode === 'crop' ? 'canvas.cropImage' : imageEditMode === 'outpaint' ? 'canvas.outpaintImage' : imageEditMode === 'mask' ? 'canvas.maskEdit' : imageEditMode === 'brush' ? 'canvas.brushEdit' : 'canvas.modeGrid';
            const subKey = imageEditMode === 'crop' ? 'canvas.cropHint' : imageEditMode === 'outpaint' ? 'canvas.outpaintHint' : imageEditMode === 'mask' ? 'canvas.maskHint2' : imageEditMode === 'brush' ? 'canvas.brushHint' : 'canvas.gridHint';
            document.getElementById('imageEditTitle').textContent = tr(titleKey);
            document.getElementById('imageEditSub').textContent = tr(subKey);
            const applyLabel = imageEditMode === 'grid' && gridOperationMode === 'join' ? tr('smart.outputGridJoin') : tr(labelKey);
            applyBtn.innerHTML = `${ico(icon, 'w-4 h-4')}<span>${applyLabel}</span>`;
        }
    }
    if(prev !== next && isImageDrawingMode(next)){
        const saved = session?.drawings[next];
        if(saved){
            restoreEditDrawSnapshot(saved.snapshot);
            editDrawUndoStack = saved.undo;
            editDrawRedoStack = saved.redo;
            delete session.drawings[next];
        }
    }
    syncImageEditApplyState();
    syncEditDrawingHistoryButtons();
    syncBrushToolButtons();
    syncTextToolState(true);
    updatePreviewNavButtons();
    canvasCore.refreshIcons();
    // #255 图层面板钩子：layers 模式时初始化 store + 渲染列表（纯接线，逻辑在 canvas-layers.js）。
    window.CanvasLayers?.onEditMode?.(imageEditMode);
}

function isImageDrawingMode(mode){ return mode === 'mask' || mode === 'brush'; }

function editDrawSnapshot(){
    const canvasEl = editDrawCanvas();
    return {
        imageData:canvasEl.getContext('2d').getImageData(0, 0, canvasEl.width, canvasEl.height),
        labelCounter:brushLabelCounter,
        textItems:editTextItems.map(item => ({...item})),
        textSelectedId:editTextSelectedId || ''
    };
}

function restoreEditDrawSnapshot(snapshot){
    if(!snapshot) return;
    removeEditTextInlineEditor(false);
    const canvasEl = editDrawCanvas();
    const ctx = canvasEl.getContext('2d');
    const pixels = snapshot.imageData || snapshot;
    const scaleX = canvasEl.width / pixels.width, scaleY = canvasEl.height / pixels.height;
    if(scaleX === 1 && scaleY === 1){
        ctx.putImageData(pixels, 0, 0);
    } else {
        // Undo/redo snapshots retain their original resolution until restored.
        const source = document.createElement('canvas');
        source.width = pixels.width; source.height = pixels.height;
        source.getContext('2d').putImageData(pixels, 0, 0);
        ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
        ctx.save();
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(source, 0, 0, canvasEl.width, canvasEl.height);
        ctx.restore();
    }
    if(snapshot.labelCounter) brushLabelCounter = snapshot.labelCounter;
    editTextItems = (snapshot.textItems || []).map(item => ({...item, x:item.x * scaleX, y:item.y * scaleY, size:item.size * scaleY}));
    editTextSelectedId = snapshot.textSelectedId || '';
    renderEditTextCanvas();
    syncTextToolState(true);
}

function pushEditDrawHistory(){
    editDrawUndoStack.push(editDrawSnapshot());
    if(editDrawUndoStack.length > EDIT_DRAW_HISTORY_MAX) editDrawUndoStack.shift();
    editDrawRedoStack = [];
    syncEditDrawingHistoryButtons();
}

function syncEditDrawingHistoryButtons(){
    ['maskUndoBtn','brushUndoBtn'].forEach(id => { const btn = document.getElementById(id); if(btn){ btn.disabled = !editDrawUndoStack.length; btn.style.opacity = editDrawUndoStack.length ? '1' : '.42'; } });
    ['maskRedoBtn','brushRedoBtn'].forEach(id => { const btn = document.getElementById(id); if(btn){ btn.disabled = !editDrawRedoStack.length; btn.style.opacity = editDrawRedoStack.length ? '1' : '.42'; } });
}

function undoEditDrawing(){
    if(!editDrawUndoStack.length) return;
    editDrawRedoStack.push(editDrawSnapshot());
    restoreEditDrawSnapshot(editDrawUndoStack.pop());
    syncEditDrawingHistoryButtons();
}

function redoEditDrawing(){
    if(!editDrawRedoStack.length) return;
    editDrawUndoStack.push(editDrawSnapshot());
    restoreEditDrawSnapshot(editDrawRedoStack.pop());
    syncEditDrawingHistoryButtons();
}

function clearEditDrawing(silent=false){
    removeEditTextInlineEditor(false);
    const canvasEl = editDrawCanvas();
    if(!silent && editCanvasHasPixels()) pushEditDrawHistory();
    canvasEl.getContext('2d').clearRect(0, 0, canvasEl.width, canvasEl.height);
    const textCanvasEl = editTextCanvas();
    textCanvasEl?.getContext('2d')?.clearRect(0, 0, textCanvasEl.width, textCanvasEl.height);
    editTextItems = [];
    editTextSelectedId = '';
    editTextDrag = null;
    editTextDirty = false;
    brushLabelCounter = 1;
    syncTextToolState(true);
    syncEditDrawingHistoryButtons();
}

function resetEditDrawingHistory(){
    removeEditTextInlineEditor(false);
    editDrawUndoStack = [];
    editDrawRedoStack = [];
    brushLabelCounter = 1;
    editTextItems = [];
    editTextSelectedId = '';
    editTextDrag = null;
    editTextDirty = false;
    renderEditTextCanvas();
    syncTextToolState(true);
    syncEditDrawingHistoryButtons();
}

function setBrushTool(tool){
    if(tool !== 'text') removeEditTextInlineEditor(true);
    brushTool = ['free','rect','ellipse','label','text'].includes(tool) ? tool : 'free';
    syncBrushToolButtons();
    syncTextToolState(true);
}

function syncBrushToolButtons(){
    document.querySelectorAll('[data-brush-tool]').forEach(btn => {
        const active = btn.dataset.brushTool === brushTool;
        btn.classList.toggle('primary', active);
        btn.classList.toggle('secondary', !active);
    });
    document.getElementById('cropCanvas')?.classList.toggle('text-mode', imageEditMode === 'brush' && brushTool === 'text');
}

function editDrawPoint(event){
    // #255：client 像素 → 画布像素统一走分数坐标骨架（0..1 公共坐标系）。
    // brush / mask / text 绘图共用此换算入口；与旧「直连缩放」逐式同构（不钳制，越界靠画布裁剪）。
    const canvasEl = editDrawCanvas();
    const rect = canvasEl.getBoundingClientRect();
    const geometry = window.CanvasGeometry || null;
    if(!geometry) return {x:(event.clientX - rect.left) * canvasEl.width / Math.max(1, rect.width), y:(event.clientY - rect.top) * canvasEl.height / Math.max(1, rect.height)};
    const fraction = geometry.fractionFromPoint({x:event.clientX - rect.left, y:event.clientY - rect.top}, {x:0, y:0, w:Math.max(1, rect.width), h:Math.max(1, rect.height)});
    return geometry.pointFromFraction(fraction, {x:0, y:0, w:canvasEl.width, h:canvasEl.height});
}

// #255：显示像素 → 原图像素统一走分数坐标骨架（显示 box → 0..1 分数 → 自然尺寸 box）。
// crop / outpaint 应用时共用；骨架模块缺失时回退到旧「直连缩放」。
function editScaleRectToNatural(rect, img){
    const geometry = window.CanvasGeometry || null;
    if(geometry){
        return geometry.rectFromFractionRect(
            geometry.fractionRectFromRect(rect, {x:0, y:0, w:img.clientWidth || 1, h:img.clientHeight || 1}),
            {x:0, y:0, w:img.naturalWidth, h:img.naturalHeight}
        );
    }
    const scaleX = img.naturalWidth / (img.clientWidth || 1), scaleY = img.naturalHeight / (img.clientHeight || 1);
    return {x:rect.x * scaleX, y:rect.y * scaleY, w:rect.w * scaleX, h:rect.h * scaleY};
}

function gridCustomLineHit(point){
    const canvasEl = editDrawCanvas();
    const threshold = Math.max(8, Math.min(canvasEl.width, canvasEl.height) / 80);
    let best = -1, bestDist = Infinity;
    gridCustomLines.forEach((line, index) => {
        const dist = line.type === 'h' ? Math.abs(point.y - line.pos * canvasEl.height) : Math.abs(point.x - line.pos * canvasEl.width);
        if(dist < bestDist && dist <= threshold){ best = index; bestDist = dist; }
    });
    return best;
}

function setGridCustomLinePos(index, point){
    const canvasEl = editDrawCanvas();
    const line = gridCustomLines[index];
    if(!line) return;
    line.pos = line.type === 'h'
        ? Math.max(0.001, Math.min(0.999, point.y / Math.max(1, canvasEl.height)))
        : Math.max(0.001, Math.min(0.999, point.x / Math.max(1, canvasEl.width)));
}

const MASK_BRUSH_ALPHA = 115;

const MASK_BRUSH_COLOR = `rgba(255,255,255,${MASK_BRUSH_ALPHA / 255})`;

function editBrushSize(){ return Number(document.getElementById(imageEditMode === 'mask' ? 'maskBrushSize' : 'paintBrushSize')?.value || 20); }

function brushColor(){ return document.getElementById('paintBrushColor')?.value || '#ff2d55'; }

function setupDrawStyle(ctx){
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.lineWidth = editBrushSize();
    ctx.strokeStyle = imageEditMode === 'mask' ? MASK_BRUSH_COLOR : brushColor();
    ctx.fillStyle = imageEditMode === 'mask' ? MASK_BRUSH_COLOR : brushColor();
    ctx.globalCompositeOperation = 'source-over';
}

function normalizeMaskPreviewCanvas(canvasEl=editDrawCanvas()){
    if(imageEditMode !== 'mask' || !canvasEl?.width || !canvasEl?.height) return;
    const ctx = canvasEl.getContext('2d');
    const imageData = ctx.getImageData(0, 0, canvasEl.width, canvasEl.height);
    const data = imageData.data;
    let changed = false;
    for(let i = 0; i < data.length; i += 4){
        if(data[i + 3] <= 0) continue;
        data[i] = 255;
        data[i + 1] = 255;
        data[i + 2] = 255;
        if(data[i + 3] > MASK_BRUSH_ALPHA) data[i + 3] = MASK_BRUSH_ALPHA;
        changed = true;
    }
    if(changed) ctx.putImageData(imageData, 0, 0);
}

function strokeFreeDrawPoint(point){
    if(!editDrawState) return;
    const ctx = editDrawCanvas().getContext('2d');
    setupDrawStyle(ctx);
    const dx = point.x - editDrawState.x;
    const dy = point.y - editDrawState.y;
    const dist = Math.hypot(dx, dy);
    const radius = Math.max(1, editBrushSize() / 2);
    if(dist > radius){
        const steps = Math.ceil(dist / Math.max(1, radius * 0.35));
        for(let i = 1; i <= steps; i++){
            const t = i / steps;
            const x = editDrawState.x + dx * t;
            const y = editDrawState.y + dy * t;
            ctx.beginPath();
            ctx.arc(x, y, radius, 0, Math.PI * 2);
            ctx.fill();
        }
    }
    ctx.beginPath();
    ctx.moveTo(editDrawState.x, editDrawState.y);
    ctx.lineTo(point.x, point.y);
    ctx.stroke();
    editDrawState.x = point.x;
    editDrawState.y = point.y;
}

function drawBrushShape(ctx, start, end){
    setupDrawStyle(ctx);
    const x = Math.min(start.x, end.x), y = Math.min(start.y, end.y), w = Math.abs(end.x - start.x), h = Math.abs(end.y - start.y);
    if(brushTool === 'rect') ctx.strokeRect(x, y, w, h);
    else if(brushTool === 'ellipse'){ ctx.beginPath(); ctx.ellipse(x + w / 2, y + h / 2, Math.max(1, w / 2), Math.max(1, h / 2), 0, 0, Math.PI * 2); ctx.stroke(); }
}

function beginEditDraw(event){
    if(imageEditMode === 'crop') return;
    event.preventDefault(); event.stopPropagation();
    const canvasEl = editDrawCanvas();
    canvasEl.setPointerCapture?.(event.pointerId);
    const p = editDrawPoint(event);
    if(imageEditMode === 'grid'){
        if(gridOperationMode === 'join') return;
        if(!gridCustomMode) return;
        const hit = gridCustomLineHit(p);
        gridCustomHistory.push([...gridCustomLines.map(line => ({...line}))]);
        if(hit >= 0){ gridCustomDrag = {index:hit, pointerId:event.pointerId}; setGridCustomLinePos(hit, p); }
        else { gridCustomLines.push({type:gridCustomOrientation, pos:gridCustomOrientation === 'h' ? p.y / canvasEl.height : p.x / canvasEl.width}); gridCustomDrag = {index:gridCustomLines.length - 1, pointerId:event.pointerId}; }
        syncGridCustomUndoBtn(); refreshGridSplitPreview(); return;
    }
    const ctx = canvasEl.getContext('2d');
    pushEditDrawHistory();
    if(imageEditMode === 'brush' && brushTool === 'label'){ drawNumberLabel(p); editDrawState = null; canvasEl.releasePointerCapture?.(event.pointerId); return; }
    editDrawState = {x:p.x, y:p.y, sx:p.x, sy:p.y, pointerId:event.pointerId, snapshot:(imageEditMode === 'brush' && brushTool !== 'free') ? editDrawSnapshot() : null};
    setupDrawStyle(ctx);
    ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x + .01, p.y + .01);
    if(imageEditMode === 'mask' || brushTool === 'free') ctx.stroke();
    normalizeMaskPreviewCanvas(canvasEl);
}

function moveEditDraw(event){
    if(imageEditMode === 'grid' && gridOperationMode === 'join') return;
    if(imageEditMode === 'grid' && gridCustomMode && gridCustomDrag){ event.preventDefault(); event.stopPropagation(); setGridCustomLinePos(gridCustomDrag.index, editDrawPoint(event)); refreshGridSplitPreview(); return; }
    if(!editDrawState || imageEditMode === 'crop' || imageEditMode === 'grid') return;
    event.preventDefault(); event.stopPropagation();
    const ctx = editDrawCanvas().getContext('2d');
    const p = editDrawPoint(event);
    if(imageEditMode === 'brush' && brushTool !== 'free'){ restoreEditDrawSnapshot(editDrawState.snapshot); drawBrushShape(ctx, {x:editDrawState.sx, y:editDrawState.sy}, p); return; }
    const events = typeof event.getCoalescedEvents === 'function' ? event.getCoalescedEvents() : [];
    if(events.length){
        events.forEach(ev => strokeFreeDrawPoint(editDrawPoint(ev)));
    } else {
        strokeFreeDrawPoint(p);
    }
    normalizeMaskPreviewCanvas();
}

function endEditDraw(event){
    if(editDrawState && event?.pointerId != null) editDrawCanvas().releasePointerCapture?.(event.pointerId);
    if(gridCustomDrag && event?.pointerId != null) editDrawCanvas().releasePointerCapture?.(event.pointerId);
    editDrawState = null; gridCustomDrag = null; syncEditDrawingHistoryButtons();
}

function beginGridJoinDrag(event){
    if(imageEditMode !== 'grid' || gridOperationMode !== 'join') return;
    const itemEl = event.target?.closest?.('.grid-join-item');
    if(!itemEl) return;
    event.preventDefault();
    event.stopPropagation();
    const index = Number(itemEl.dataset.gridJoinIndex);
    const item = gridJoinLayout?.items?.find(entry => Number(entry.index) === index);
    const host = document.getElementById('gridJoinCanvas');
    if(!item || !host) return;
    itemEl.setPointerCapture?.(event.pointerId);
    gridJoinDrag = {index, pointerId:event.pointerId, sx:event.clientX, sy:event.clientY, x:item.x, y:item.y};
    itemEl.classList.add('dragging');
}

function moveGridJoinDrag(event){
    if(!gridJoinDrag || imageEditMode !== 'grid' || gridOperationMode !== 'join') return;
    event.preventDefault();
    event.stopPropagation();
    const item = gridJoinLayout?.items?.find(entry => Number(entry.index) === Number(gridJoinDrag.index));
    if(!item) return;
    const host = document.getElementById('gridJoinCanvas');
    const rect = host?.getBoundingClientRect();
    const logical = gridJoinCanvasSize();
    const scale = rect ? Math.max(0.001, rect.width / Math.max(1, logical.w)) : Math.max(0.001, imageEditZoom || 1);
    const dx = (event.clientX - gridJoinDrag.sx) / scale;
    const dy = (event.clientY - gridJoinDrag.sy) / scale;
    gridJoinDrag.dx = dx;
    gridJoinDrag.dy = dy;
    const el = host?.querySelector(`[data-grid-join-index="${CSS.escape(String(gridJoinDrag.index))}"]`);
    if(el){
        el.style.transform = `translate(${Math.round(dx)}px, ${Math.round(dy)}px)`;
    }
}

function gridJoinDragTarget(){
    if(!gridJoinDrag || !gridJoinLayout) return null;
    const dragged = gridJoinLayout.items.find(entry => Number(entry.index) === Number(gridJoinDrag.index));
    if(!dragged) return null;
    const dx = gridJoinDrag.dx || 0;
    const dy = gridJoinDrag.dy || 0;
    const cx = dragged.x + dx + dragged.w / 2;
    const cy = dragged.y + dy + dragged.h / 2;
    return (gridJoinLayout.items || [])
        .filter(entry => Number(entry.index) !== Number(gridJoinDrag.index))
        .map(entry => {
            const inside = cx >= entry.x && cx <= entry.x + entry.w && cy >= entry.y && cy <= entry.y + entry.h;
            const score = Math.hypot(cx - (entry.x + entry.w / 2), cy - (entry.y + entry.h / 2));
            return {entry, inside, score};
        })
        .filter(item => item.inside || item.score < Math.max(dragged.w, dragged.h, item.entry.w, item.entry.h) * 0.55)
        .sort((a, b) => (b.inside - a.inside) || a.score - b.score)[0]?.entry || null;
}

function endGridJoinDrag(event){
    if(!gridJoinDrag) return;
    const host = document.getElementById('gridJoinCanvas');
    const draggedEl = host?.querySelector(`[data-grid-join-index="${CSS.escape(String(gridJoinDrag.index))}"]`);
    draggedEl?.classList.remove('dragging');
    if(draggedEl) draggedEl.style.transform = '';
    const dragged = gridJoinLayout?.items?.find(entry => Number(entry.index) === Number(gridJoinDrag.index));
    const target = gridJoinDragTarget();
    if(dragged && target){
        const order = gridJoinVisualOrder();
        const a = order.indexOf(Number(dragged.index));
        const b = order.indexOf(Number(target.index));
        if(a >= 0 && b >= 0) [order[a], order[b]] = [order[b], order[a]];
        setGridJoinLayoutOrder(order, gridJoinLayout.rows, gridJoinLayout.cols, gridJoinLayout.gap);
        gridJoinUserMoved = true;
        renderGridJoinPreview();
    }
    if(event?.pointerId != null) event.target?.releasePointerCapture?.(event.pointerId);
    gridJoinDrag = null;
}

function syncGridGapValue(){
    const input = document.getElementById('gridGapSize');
    const value = Math.max(0, Math.min(240, Number(input?.value || 0)));
    if(input) input.value = value;
    const label = document.getElementById('gridGapValue');
    if(label) label.textContent = String(value);
    if(gridJoinLayout && gridOperationMode === 'join'){
        const rows = gridJoinLayout.rows;
        const cols = gridJoinLayout.cols;
        const order = gridJoinVisualOrder();
        setGridJoinLayoutOrder(order, rows, cols, value);
    }
    return value;
}

function gridGapInputValue(){
    return Math.max(0, Math.min(240, Number(document.getElementById('gridGapSize')?.value || 0)));
}

function gridSplitSettings(){
    const hLines = Math.max(0, Math.min(20, Number(document.getElementById('gridHorizontalLines')?.value || 0)));
    const vLines = Math.max(0, Math.min(20, Number(document.getElementById('gridVerticalLines')?.value || 0)));
    return {rows:hLines + 1, cols:vLines + 1, gap:syncGridGapValue()};
}

function currentGridJoinItems(){
    // 分组拼接：聚合组内所有图片成员的图片，按阅读顺序给出连续索引（source 指向真实图片对象以写回自然尺寸）。
    if(gridJoinGroupId){
        const group = canvasState.nodes.find(n => n.id === gridJoinGroupId && isSmartGroupNode(n));
        if(group){
            return canvasCore.smartGroupImageRefs(group)
                .filter(r => canvasCore.mediaKindForItem(r.item) === 'image' && r.item?.url)
                .map((r, index) => ({item:r.item, source:r.source, index}));
        }
    }
    const node = currentEditImage().node;
    return (node?.images || [])
        .map((item, index) => ({item:canvasCore.imageForDisplay(item), source:item, index}))
        .filter(entry => canvasCore.mediaKindForItem(entry.item) === 'image' && entry.item?.url);
}

// 从分组小菜单打开“宫格拼接”：锚定在分组第一张图片（保证编辑器有真实底图，不出现破图/尺寸异常），
// 但把拼接数据源切换到整个分组（gridJoinGroupId）。
function canGridJoinCurrentNode(){
    return currentGridJoinItems().length > 1;
}

function gridJoinAutoDims(count){
    const cols = Math.max(1, Math.ceil(Math.sqrt(Math.max(1, count))));
    return {rows:Math.max(1, Math.ceil(count / cols)), cols};
}

function gridJoinNaturalSize(entry){
    const item = entry?.item || {};
    const cached = gridJoinImageCache.get(entry?.index);
    const w = Number(item.natural_w || item.width || cached?.naturalWidth || 0);
    const h = Number(item.natural_h || item.height || cached?.naturalHeight || 0);
    return {w:Math.max(1, w || 512), h:Math.max(1, h || 512)};
}

function gridJoinBaseCellSize(items){
    const sizes = items.map(gridJoinNaturalSize);
    const maxW = Math.max(1, ...sizes.map(size => size.w));
    const maxH = Math.max(1, ...sizes.map(size => size.h));
    const scale = Math.min(1, 420 / Math.max(maxW, maxH));
    return {w:Math.max(1, Math.round(maxW * scale)), h:Math.max(1, Math.round(maxH * scale)), scale};
}

function gridJoinItemDisplaySize(entry, cell){
    return {
        w:Math.max(1, Math.round(cell.w)),
        h:Math.max(1, Math.round(cell.h))
    };
}

function ensureGridJoinLayout(rows=null, cols=null){
    const items = currentGridJoinItems();
    if(!items.length){ gridJoinLayout = null; return null; }
    const auto = gridJoinAutoDims(items.length);
    const nextRows = Math.max(1, Number(rows || gridJoinLayout?.rows || auto.rows) || auto.rows);
    const nextCols = Math.max(1, Number(cols || gridJoinLayout?.cols || auto.cols) || auto.cols);
    const byIndex = new Map(items.map(entry => [entry.index, entry]));
    const previousOrder = gridJoinVisualOrder()
        .map(index => byIndex.get(Number(index)))
        .filter(Boolean);
    const ordered = [
        ...previousOrder,
        ...items.filter(entry => !previousOrder.some(prev => Number(prev.index) === Number(entry.index)))
    ];
    const cell = gridJoinBaseCellSize(ordered);
    const gap = gridGapInputValue();
    const layoutItems = ordered.map((entry, order) => {
        const row = Math.floor(order / nextCols);
        const col = order % nextCols;
        const {w, h} = gridJoinItemDisplaySize(entry, cell);
        return {
            index:entry.index,
            x:col * (cell.w + gap),
            y:row * (cell.h + gap),
            w,
            h
        };
    });
    gridJoinLayout = {rows:nextRows, cols:nextCols, cellW:cell.w, cellH:cell.h, gap, items:layoutItems};
    return gridJoinLayout;
}

function gridJoinVisualOrder(layout=gridJoinLayout){
    return (layout?.items || [])
        .slice()
        .sort((a, b) => (Number(a.y || 0) - Number(b.y || 0)) || (Number(a.x || 0) - Number(b.x || 0)))
        .map(item => Number(item.index));
}

function setGridJoinLayoutOrder(order, rows=null, cols=null, gapOverride=null){
    const entries = currentGridJoinItems();
    if(!entries.length){ gridJoinLayout = null; return null; }
    const byIndex = new Map(entries.map(entry => [entry.index, entry]));
    const ordered = [
        ...order.map(index => byIndex.get(Number(index))).filter(Boolean),
        ...entries.filter(entry => !order.includes(entry.index))
    ];
    const auto = gridJoinAutoDims(ordered.length);
    const nextRows = Math.max(1, Number(rows || gridJoinLayout?.rows || auto.rows) || auto.rows);
    const nextCols = Math.max(1, Number(cols || gridJoinLayout?.cols || auto.cols) || auto.cols);
    const cell = gridJoinBaseCellSize(ordered);
    const gap = Math.max(0, Math.min(240, Number(gapOverride ?? document.getElementById('gridGapSize')?.value ?? 0)));
    const layoutItems = ordered.map((entry, orderIndex) => {
        const row = Math.floor(orderIndex / nextCols);
        const col = orderIndex % nextCols;
        const {w, h} = gridJoinItemDisplaySize(entry, cell);
        return {
            index:entry.index,
            x:col * (cell.w + gap),
            y:row * (cell.h + gap),
            w,
            h
        };
    });
    gridJoinLayout = {rows:nextRows, cols:nextCols, cellW:cell.w, cellH:cell.h, gap, items:layoutItems};
    return gridJoinLayout;
}

function resetGridJoinLayout(){
    gridJoinUserMoved = false;
    gridJoinLayout = null;
    ensureGridJoinLayout();
    renderGridJoinPreview();
}

function applyGridJoinPreset(rows, cols){
    gridJoinUserMoved = false;
    const order = gridJoinVisualOrder();
    if(order.length) setGridJoinLayoutOrder(order, rows, cols);
    else {
        gridJoinLayout = null;
        ensureGridJoinLayout(rows, cols);
    }
    renderGridJoinPreview();
}

function setGridJoinOutputSize(size){
    gridJoinOutputSize = Math.max(256, Math.min(8192, Number(size) || 2048));
    syncGridJoinSizeControls();
    refreshGridSplitPreview();
}

function syncGridJoinSizeControls(){
    document.querySelectorAll('[data-grid-join-size]').forEach(btn => {
        const active = Number(btn.dataset.gridJoinSize || 0) === Number(gridJoinOutputSize);
        btn.classList.toggle('active', active);
    });
}

function setGridOperationMode(mode){
    gridOperationMode = mode === 'join' && canGridJoinCurrentNode() ? 'join' : 'split';
    if(mode === 'join' && gridOperationMode !== 'join') toast(tr('smart.gridJoinNeedsGroup'));
    syncGridOperationControls();
    refreshGridSplitPreview();
}

function syncGridOperationControls(){
    const join = gridOperationMode === 'join';
    document.getElementById('gridSplitModeBtn')?.classList.toggle('primary', !join);
    document.getElementById('gridSplitModeBtn')?.classList.toggle('secondary', join);
    const joinBtn = document.getElementById('gridJoinModeBtn');
    if(joinBtn){
        joinBtn.disabled = !canGridJoinCurrentNode();
        joinBtn.classList.toggle('primary', join);
        joinBtn.classList.toggle('secondary', !join);
    }
    document.querySelectorAll('.grid-split-control').forEach(el => { el.style.display = join ? 'none' : (el.id === 'gridRegularControls' ? 'contents' : ''); });
    document.querySelectorAll('.grid-join-control').forEach(el => { el.style.display = join ? 'flex' : 'none'; });
    syncGridJoinSizeControls();
    if(!join) syncGridCustomControls();
    document.getElementById('cropCanvas')?.classList.toggle('grid-join-mode', join);
    document.getElementById('cropImage')?.classList.toggle('grid-join-hidden', join);
    if(join) ensureGridJoinLayout();
    else gridJoinDrag = null;
}

function gridSplitRects(width, height){
    if(gridCustomMode) return gridSplitRectsCustom(width, height);
    const {rows, cols, gap} = gridSplitSettings();
    const halfGap = gap / 2, rects = [];
    for(let row = 0; row < rows; row++){
        const topLine = row * height / rows, bottomLine = (row + 1) * height / rows;
        const y1 = Math.round(row === 0 ? 0 : topLine + halfGap), y2 = Math.round(row === rows - 1 ? height : bottomLine - halfGap);
        for(let col = 0; col < cols; col++){
            const leftLine = col * width / cols, rightLine = (col + 1) * width / cols;
            const x1 = Math.round(col === 0 ? 0 : leftLine + halfGap), x2 = Math.round(col === cols - 1 ? width : rightLine - halfGap);
            if(x2 > x1 && y2 > y1) rects.push({row, col, x:x1, y:y1, w:x2 - x1, h:y2 - y1});
        }
    }
    return rects;
}

function gridSplitRectsCustom(width, height){
    const gap = Math.max(0, Math.min(240, Number(document.getElementById('gridGapSize')?.value || 0)));
    const halfGap = gap / 2;
    const rawH = [...new Set(gridCustomLines.filter(l => l.type === 'h').map(l => l.pos * height))].sort((a, b) => a - b);
    const rawV = [...new Set(gridCustomLines.filter(l => l.type === 'v').map(l => l.pos * width))].sort((a, b) => a - b);
    const hCuts = [0, ...rawH, height], vCuts = [0, ...rawV, width], rects = [];
    for(let row = 0; row < hCuts.length - 1; row++) for(let col = 0; col < vCuts.length - 1; col++){
        const y1 = Math.round(row === 0 ? hCuts[row] : hCuts[row] + halfGap), y2 = Math.round(row === hCuts.length - 2 ? hCuts[row + 1] : hCuts[row + 1] - halfGap);
        const x1 = Math.round(col === 0 ? vCuts[col] : vCuts[col] + halfGap), x2 = Math.round(col === vCuts.length - 2 ? vCuts[col + 1] : vCuts[col + 1] - halfGap);
        if(x2 > x1 && y2 > y1) rects.push({row, col, x:x1, y:y1, w:x2 - x1, h:y2 - y1});
    }
    return rects;
}

function gridLayoutFromRects(rects){
    return {type:'grid-split', groupId:uid('grid'), rows:Math.max(1, ...rects.map(r => Number(r.row || 0) + 1)), cols:Math.max(1, ...rects.map(r => Number(r.col || 0) + 1))};
}

function applyGridPreset(rows, cols){
    gridCustomMode = false; gridCustomLines = []; gridCustomHistory = []; gridCustomDrag = null;
    const h = document.getElementById('gridHorizontalLines'), v = document.getElementById('gridVerticalLines');
    if(h){ h.disabled = false; h.value = String(Math.max(0, Number(rows || 1) - 1)); }
    if(v){ v.disabled = false; v.value = String(Math.max(0, Number(cols || 1) - 1)); }
    document.getElementById('gridCustomToggle')?.classList.remove('primary');
    document.getElementById('gridCustomToggle')?.classList.add('secondary');
    syncGridCustomControls();
    syncGridCustomCursor(); syncGridCustomUndoBtn(); refreshGridSplitPreview();
}

function syncGridCustomControls(){
    const join = gridOperationMode === 'join';
    const custom = document.getElementById('gridCustomControls');
    if(custom) custom.style.display = !join && gridCustomMode ? 'flex' : 'none';
    document.querySelectorAll('.grid-split-control.grid-preset-row').forEach(row => {
        row.style.display = !join && !gridCustomMode ? 'flex' : 'none';
    });
    const regular = document.getElementById('gridRegularControls');
    if(regular) regular.style.display = !join && !gridCustomMode ? 'contents' : 'none';
}

function toggleGridCustomMode(){
    gridCustomMode = !gridCustomMode;
    if(gridCustomMode){ gridCustomLines = []; gridCustomHistory = []; }
    gridCustomDrag = null;
    const toggle = document.getElementById('gridCustomToggle');
    toggle.classList.toggle('primary', gridCustomMode); toggle.classList.toggle('secondary', !gridCustomMode);
    ['gridHorizontalLines','gridVerticalLines'].forEach(id => { const el = document.getElementById(id); if(el) el.disabled = gridCustomMode; });
    syncGridCustomControls();
    syncGridCustomCursor(); syncGridCustomUndoBtn(); refreshGridSplitPreview();
}

function setGridCustomOrientation(orient){
    gridCustomOrientation = orient;
    document.getElementById('gridOrientH').classList.toggle('primary', orient === 'h');
    document.getElementById('gridOrientH').classList.toggle('secondary', orient !== 'h');
    document.getElementById('gridOrientV').classList.toggle('primary', orient === 'v');
    document.getElementById('gridOrientV').classList.toggle('secondary', orient !== 'v');
    syncGridCustomCursor();
}

function clearGridCustomLines(){ gridCustomHistory = []; gridCustomLines = []; gridCustomDrag = null; syncGridCustomUndoBtn(); refreshGridSplitPreview(); }

function undoGridCustomLine(){ if(!gridCustomHistory.length) return; gridCustomLines = gridCustomHistory.pop(); gridCustomDrag = null; syncGridCustomUndoBtn(); refreshGridSplitPreview(); }

function syncGridCustomUndoBtn(){
    const btn = document.getElementById('gridUndoBtn');
    if(!btn) return;
    btn.disabled = gridCustomHistory.length === 0;
    btn.style.opacity = gridCustomHistory.length === 0 ? '0.4' : '1';
}

function clampImageResizeScale(value){
    const num = Number(value);
    if(!Number.isFinite(num)) return 0.5;
    return Math.max(0.05, Math.min(1, Math.round(num * 100) / 100));
}

function imageResizeDimensions(){
    const img = document.getElementById('cropImage');
    const sourceW = Math.max(1, Math.round(Number(img?.naturalWidth || 0)));
    const sourceH = Math.max(1, Math.round(Number(img?.naturalHeight || 0)));
    const scale = clampImageResizeScale(imageResizeScale);
    return {
        sourceW,
        sourceH,
        scale,
        targetW:Math.max(1, Math.round(sourceW * scale)),
        targetH:Math.max(1, Math.round(sourceH * scale))
    };
}

function syncImageResizeControls(){
    imageResizeScale = clampImageResizeScale(imageResizeScale);
    const range = document.getElementById('imageResizeScaleRange');
    const input = document.getElementById('imageResizeScaleInput');
    const label = document.getElementById('imageResizeResolution');
    const overlay = document.getElementById('resizeResolutionOverlay');
    const dims = imageResizeDimensions();
    const text = `${dims.targetW}×${dims.targetH}`;
    if(range && Number(range.value) !== dims.scale) range.value = String(dims.scale);
    if(input && Number(input.value) !== dims.scale) input.value = String(dims.scale);
    if(label) label.textContent = text;
    if(overlay) overlay.textContent = text;
}

function setImageResizeScale(value){
    imageResizeScale = clampImageResizeScale(value);
    syncImageResizeControls();
}

async function resizedImageBlobFromEditor(){
    const img = document.getElementById('cropImage');
    if(!img?.naturalWidth || !img?.naturalHeight) return null;
    const dims = imageResizeDimensions();
    const canvasEl = document.createElement('canvas');
    canvasEl.width = dims.targetW;
    canvasEl.height = dims.targetH;
    const ctx = canvasEl.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, 0, 0, img.naturalWidth, img.naturalHeight, 0, 0, dims.targetW, dims.targetH);
    const blob = await new Promise(resolve => canvasEl.toBlob(resolve, 'image/png'));
    return blob ? {blob, ...dims} : null;
}

function applyImageEditZoom(scaleOverride=null){
    ensureImageEditBaseSize();
    if(!imageEditBaseW) return;
    const img = document.getElementById('cropImage');
    const box = cropState && (imageEditMode === 'crop' || imageEditMode === 'outpaint') ? cropImageDisplaySize() : null;
    img.style.maxWidth = 'none'; img.style.maxHeight = 'none';
    img.style.width = Math.round(imageEditBaseW * imageEditZoom) + 'px';
    img.style.height = Math.round(imageEditBaseH * imageEditZoom) + 'px';
    resizeEditDrawCanvas();
    if(box && cropState.w){
        const nextSize = cropImageDisplaySize();
        const sx = Number(scaleOverride) || nextSize.w / box.w;
        const sy = Number(scaleOverride) || nextSize.h / box.h;
        cropState.x *= sx; cropState.y *= sy;
        cropState.w *= sx; cropState.h *= sy;
        clampCrop(); renderCropBox();
    }
    if(imageEditMode === 'grid') refreshGridSplitPreview();
    syncImageResizeControls();
    syncImageEditOverflow(); updateZoomLabel();
}

function ensureImageEditBaseSize(force=false){
    if(imageEditBaseW && imageEditBaseH && !force) return;
    const img = document.getElementById('cropImage');
    const naturalW = img.naturalWidth || img.clientWidth || 0;
    const naturalH = img.naturalHeight || img.clientHeight || 0;
    if(!naturalW || !naturalH) return;
    const stage = document.getElementById('imageEditStage');
    const maxW = Math.max(1, Math.min(1300, stage?.clientWidth ? stage.clientWidth - 36 : window.innerWidth - 100));
    const maxH = Math.max(1, Math.min(840, stage?.clientHeight ? stage.clientHeight - 36 : window.innerHeight - 200));
    const fit = Math.min(1, maxW / naturalW, maxH / naturalH);
    imageEditBaseW = Math.max(1, Math.round(naturalW * fit));
    imageEditBaseH = Math.max(1, Math.round(naturalH * fit));
}

function syncImageEditOverflow(){
    const stage = document.getElementById('imageEditStage');
    const crop = document.getElementById('cropCanvas');
    if(!stage || !crop) return;
    const pad = 36;
    stage.classList.toggle('overflow-x', crop.offsetWidth + pad > stage.clientWidth);
    stage.classList.toggle('overflow-y', crop.offsetHeight + pad > stage.clientHeight);
}

function resetImageEditZoom(){
    if(imageEditMode === 'preview'){
        resetPreviewTransform();
        return;
    }
    const stage = document.getElementById('imageEditStage');
    imageEditZoom = 1.0; applyImageEditZoom();
    if(stage){ stage.scrollLeft = 0; stage.scrollTop = 0; }
}

function syncGridCustomCursor(){
    const el = document.getElementById('cropCanvas');
    el.classList.toggle('grid-custom-h', imageEditMode === 'grid' && gridOperationMode !== 'join' && gridCustomMode && gridCustomOrientation === 'h');
    el.classList.toggle('grid-custom-v', imageEditMode === 'grid' && gridOperationMode !== 'join' && gridCustomMode && gridCustomOrientation === 'v');
}

function gridJoinCanvasSize(layout=gridJoinLayout){
    if(!layout) return {w:1, h:1};
    const gap = Math.max(0, Number(layout.gap || 0));
    const byGrid = {
        w:Math.max(1, Number(layout.cols || 1) * Number(layout.cellW || 1) + Math.max(0, Number(layout.cols || 1) - 1) * gap),
        h:Math.max(1, Number(layout.rows || 1) * Number(layout.cellH || 1) + Math.max(0, Number(layout.rows || 1) - 1) * gap)
    };
    const byItems = (layout.items || []).reduce((acc, item) => ({
        w:Math.max(acc.w, Number(item.x || 0) + Number(item.w || 0)),
        h:Math.max(acc.h, Number(item.y || 0) + Number(item.h || 0))
    }), byGrid);
    return {w:Math.ceil(byItems.w), h:Math.ceil(byItems.h)};
}

function renderGridJoinPreview(){
    const host = document.getElementById('gridJoinCanvas');
    const countEl = document.getElementById('gridSplitCount');
    const cropCanvasEl = document.getElementById('cropCanvas');
    if(!host) return;
    host.innerHTML = '';
    if(imageEditMode !== 'grid' || gridOperationMode !== 'join'){
        host.style.display = 'none';
        if(cropCanvasEl){ cropCanvasEl.style.width = ''; cropCanvasEl.style.height = ''; }
        return;
    }
    const items = currentGridJoinItems();
    if(items.length <= 1){
        host.style.display = 'none';
        if(cropCanvasEl){ cropCanvasEl.style.width = ''; cropCanvasEl.style.height = ''; }
        if(countEl) countEl.textContent = tr('smart.gridJoinMinImages');
        return;
    }
    const layout = ensureGridJoinLayout();
    const size = gridJoinCanvasSize(layout);
    const zoom = Math.max(0.05, Number(imageEditZoom || 1));
    const displayW = Math.max(1, Math.round(size.w * zoom));
    const displayH = Math.max(1, Math.round(size.h * zoom));
    host.style.display = 'block';
    host.style.width = `${Math.max(1, Math.round(size.w))}px`;
    host.style.height = `${Math.max(1, Math.round(size.h))}px`;
    host.style.transform = `scale(${zoom})`;
    host.style.transformOrigin = '0 0';
    if(cropCanvasEl){
        cropCanvasEl.style.width = `${displayW}px`;
        cropCanvasEl.style.height = `${displayH}px`;
    }
    const byIndex = new Map(items.map(entry => [entry.index, entry]));
    (layout.items || []).forEach(item => {
        const entry = byIndex.get(item.index);
        if(!entry) return;
        const img = document.createElement('img');
        img.className = 'grid-join-item';
        img.draggable = false;
        img.dataset.gridJoinIndex = String(item.index);
        img.style.left = `${Math.round(item.x)}px`;
        img.style.top = `${Math.round(item.y)}px`;
        img.style.width = `${Math.round(item.w)}px`;
        img.style.height = `${Math.round(item.h)}px`;
        img.alt = entry.item.name || `image-${item.index + 1}`;
        img.crossOrigin = 'anonymous';
        img.onload = () => {
            const hadNaturalSize = Boolean(entry.source.natural_w && entry.source.natural_h);
            gridJoinImageCache.set(item.index, img);
            if(!entry.source.natural_w && img.naturalWidth) entry.source.natural_w = img.naturalWidth;
            if(!entry.source.natural_h && img.naturalHeight) entry.source.natural_h = img.naturalHeight;
            if(!hadNaturalSize && img.naturalWidth && img.naturalHeight && imageEditMode === 'grid' && gridOperationMode === 'join'){
                ensureGridJoinLayout();
                renderGridJoinPreview();
            }
        };
        img.onerror = () => {
            if(img.dataset.proxyFallbackTried === '1') return;
            const fallback = proxiedMediaUrl(entry.item);
            if(!fallback || fallback === img.getAttribute('src')) return;
            img.dataset.proxyFallbackTried = '1';
            img.src = fallback;
        };
        img.src = canvasCore.displayMediaUrl(entry.item);
        host.appendChild(img);
    });
    if(countEl) countEl.textContent = canvasCore.trf('smart.gridJoinPreview', {n: items.length, k: Math.round(gridJoinOutputSize / 1024)});
}

function refreshGridSplitPreview(){
    const canvasEl = editDrawCanvas();
    const ctx = canvasEl.getContext('2d');
    ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
    renderGridJoinPreview();
    if(imageEditMode !== 'grid') return;
    if(gridOperationMode === 'join') return;
    const countEl = document.getElementById('gridSplitCount');
    const lineWidth = Math.max(2, Math.round(Math.min(canvasEl.width, canvasEl.height) / 320));
    const drawLine = (x1, y1, x2, y2) => {
        ctx.save(); ctx.lineWidth = lineWidth + 2; ctx.strokeStyle = getComputedStyle(canvasEl).getPropertyValue('--shadow-color').trim(); ctx.globalAlpha = .72; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        ctx.lineWidth = lineWidth; ctx.strokeStyle = 'rgba(255,255,255,0.92)'; ctx.globalAlpha = 1; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.restore();
    };
    if(gridCustomMode){
        const gap = Math.max(0, Math.min(240, Number(document.getElementById('gridGapSize')?.value || 0)));
        const hLines = gridCustomLines.filter(l => l.type === 'h'), vLines = gridCustomLines.filter(l => l.type === 'v');
        if(countEl) countEl.textContent = tr('canvas.gridWillOutput').replace('{n}', (hLines.length + 1) * (vLines.length + 1));
        hLines.forEach(l => { const y = l.pos * canvasEl.height; gap > 0 ? (drawLine(0, y - gap / 2, canvasEl.width, y - gap / 2), drawLine(0, y + gap / 2, canvasEl.width, y + gap / 2)) : drawLine(0, y, canvasEl.width, y); });
        vLines.forEach(l => { const x = l.pos * canvasEl.width; gap > 0 ? (drawLine(x - gap / 2, 0, x - gap / 2, canvasEl.height), drawLine(x + gap / 2, 0, x + gap / 2, canvasEl.height)) : drawLine(x, 0, x, canvasEl.height); });
        return;
    }
    const {rows, cols, gap} = gridSplitSettings();
    if(countEl) countEl.textContent = tr('canvas.gridWillOutput').replace('{n}', rows * cols);
    for(let i = 1; i < cols; i++){ const x = i * canvasEl.width / cols; gap > 0 ? (drawLine(x - gap / 2, 0, x - gap / 2, canvasEl.height), drawLine(x + gap / 2, 0, x + gap / 2, canvasEl.height)) : drawLine(x, 0, x, canvasEl.height); }
    for(let i = 1; i < rows; i++){ const y = i * canvasEl.height / rows; gap > 0 ? (drawLine(0, y - gap / 2, canvasEl.width, y - gap / 2), drawLine(0, y + gap / 2, canvasEl.width, y + gap / 2)) : drawLine(0, y, canvasEl.width, y); }
}

function renderCropBox(){
    if(!cropState) return;
    const cropCanvasEl = document.getElementById('cropCanvas');
    const img = document.getElementById('cropImage');
    const draw = editDrawCanvas();
    const textCanvas = editTextCanvas();
    let boxX = cropState.x;
    let boxY = cropState.y;
    if(imageEditMode === 'outpaint' && cropCanvasEl && img){
        cropCanvasEl.style.width = `${Math.round(cropState.w)}px`;
        cropCanvasEl.style.height = `${Math.round(cropState.h)}px`;
        img.style.position = 'absolute';
        img.style.left = `${Math.round(cropState.x)}px`;
        img.style.top = `${Math.round(cropState.y)}px`;
        boxX = 0;
        boxY = 0;
        if(draw){
            draw.style.left = img.style.left;
            draw.style.top = img.style.top;
        }
        if(textCanvas){
            textCanvas.style.left = img.style.left;
            textCanvas.style.top = img.style.top;
        }
        updateOutpaintResolutionLabel();
    } else if(cropCanvasEl && img){
        cropCanvasEl.style.width = '';
        cropCanvasEl.style.height = '';
        img.style.position = '';
        img.style.left = '';
        img.style.top = '';
        if(draw){
            draw.style.left = '';
            draw.style.top = '';
        }
        if(textCanvas){
            textCanvas.style.left = '';
            textCanvas.style.top = '';
        }
    }
    const box = document.getElementById('cropBox');
    if(box){
        box.style.left = `${boxX}px`; box.style.top = `${boxY}px`; box.style.width = `${cropState.w}px`; box.style.height = `${cropState.h}px`;
    }
    const outpaintFrame = document.getElementById('outpaintFrame');
    if(outpaintFrame){
        outpaintFrame.style.left = imageEditMode === 'outpaint' ? '0px' : `${boxX}px`;
        outpaintFrame.style.top = imageEditMode === 'outpaint' ? '0px' : `${boxY}px`;
        outpaintFrame.style.width = `${cropState.w}px`;
        outpaintFrame.style.height = `${cropState.h}px`;
    }
}

function outpaintNaturalSize(){
    const img = document.getElementById('cropImage');
    if(!img || !cropState) return {w:1, h:1};
    const display = cropImageDisplaySize();
    const scaleX = Math.max(1, Number(img.naturalWidth || 1)) / Math.max(1, Number(display.w || img.clientWidth || 1));
    const scaleY = Math.max(1, Number(img.naturalHeight || 1)) / Math.max(1, Number(display.h || img.clientHeight || 1));
    return {
        w:Math.max(1, Math.round((cropState.w || 1) * scaleX)),
        h:Math.max(1, Math.round((cropState.h || 1) * scaleY))
    };
}

function updateOutpaintResolutionLabel(){
    const label = document.getElementById('outpaintResolution');
    const cropCanvasEl = document.getElementById('cropCanvas');
    if(!label || !cropState) return;
    const size = outpaintNaturalSize();
    const warning = exceedsFourKStandard(size.w, size.h);
    cropCanvasEl?.classList.toggle('outpaint-warning', warning);
    label.textContent = `${Math.round(size.w)} x ${Math.round(size.h)}`;
}

function clampOutpaint(){
    if(!cropState) return;
    const {w, h} = cropBounds();
    cropState.w = Math.max(w, cropState.w);
    cropState.h = Math.max(h, cropState.h);
    cropState.x = Math.min(cropState.w - w, Math.max(0, cropState.x));
    cropState.y = Math.min(cropState.h - h, Math.max(0, cropState.y));
}

function resetOutpaintBox(){
    if(!cropState) return;
    ensureImageEditBaseSize(true);
    applyImageEditZoom();
    const {w, h} = cropBounds();
    cropState.w = w;
    cropState.h = h;
    cropState.x = 0;
    cropState.y = 0;
    clampOutpaint();
    renderCropBox();
}

function syncOutpaintRatioButtons(){
    document.querySelectorAll('[data-outpaint-ratio]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.outpaintRatio === outpaintAspectPreset);
    });
}

function setOutpaintAspectPreset(preset='free'){
    outpaintAspectPreset = preset || 'free';
    syncOutpaintRatioButtons();
    if(!cropState || imageEditMode !== 'outpaint' || outpaintAspectPreset === 'free') return;
    if(outpaintAspectPreset === 'source') return resetOutpaintBox();
    const img = document.getElementById('cropImage');
    const fit = window.OutpaintRatios?.fitContaining(img?.naturalWidth, img?.naturalHeight, outpaintAspectPreset);
    if(!img || !fit) return;
    const display = cropImageDisplaySize();
    const scaleX = Math.max(1, Number(img.naturalWidth || 1)) / Math.max(1, Number(display.w || img.clientWidth || 1));
    const scaleY = Math.max(1, Number(img.naturalHeight || 1)) / Math.max(1, Number(display.h || img.clientHeight || 1));
    cropState.w = fit.width / scaleX;
    cropState.h = fit.height / scaleY;
    cropState.x = fit.offsetX / scaleX;
    cropState.y = fit.offsetY / scaleY;
    clampOutpaint();
    renderCropBox();
}

function cropRatioFromPreset(preset){
    if(!preset || preset === 'free') return null;
    if(preset === 'source'){
        const {w, h} = cropBounds();
        return w > 0 && h > 0 ? w / h : null;
    }
    const parts = String(preset).split(':').map(v => Math.max(0, Number(v)));
    return parts.length === 2 && parts[0] > 0 && parts[1] > 0 ? parts[0] / parts[1] : null;
}

function syncCropRatioButtons(){
    document.querySelectorAll('[data-crop-ratio]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.cropRatio === cropAspectPreset);
    });
}

function fitCropRectToAspect(ratio, sourceRect=null){
    const {w:boundsW, h:boundsH} = cropBounds();
    const rect = sourceRect || cropState || {x:0, y:0, w:boundsW, h:boundsH};
    const minSize = 24;
    let nextW = Math.max(minSize, Number(rect.w || boundsW));
    let nextH = Math.max(minSize, Number(rect.h || boundsH));
    if(ratio){
        if(nextW / nextH > ratio) nextW = nextH * ratio;
        else nextH = nextW / ratio;
        if(nextW > boundsW){ nextW = boundsW; nextH = nextW / ratio; }
        if(nextH > boundsH){ nextH = boundsH; nextW = nextH * ratio; }
    } else {
        nextW = Math.min(nextW, boundsW);
        nextH = Math.min(nextH, boundsH);
    }
    const cx = Number(rect.x || 0) + Number(rect.w || nextW) / 2;
    const cy = Number(rect.y || 0) + Number(rect.h || nextH) / 2;
    cropState.w = Math.round(nextW);
    cropState.h = Math.round(nextH);
    cropState.x = Math.round(cx - cropState.w / 2);
    cropState.y = Math.round(cy - cropState.h / 2);
    clampCrop();
}

function setCropAspectPreset(preset='free'){
    cropAspectPreset = preset || 'free';
    cropAspectRatio = cropRatioFromPreset(cropAspectPreset);
    syncCropRatioButtons();
    if(cropState && imageEditMode === 'crop' && cropAspectRatio){
        fitCropRectToAspect(cropAspectRatio);
        renderCropBox();
    }
}

function resetCropBox(){
    if(!cropState) return;
    if(imageEditMode === 'outpaint') return resetOutpaintBox();
    const {w, h} = cropBounds();
    const rect = {x:Math.round(w * 0.08), y:Math.round(h * 0.08), w:Math.round(w * 0.84), h:Math.round(h * 0.84)};
    cropState.x = rect.x; cropState.y = rect.y; cropState.w = rect.w; cropState.h = rect.h;
    if(cropAspectRatio) fitCropRectToAspect(cropAspectRatio, rect);
    renderCropBox();
    if(imageEditorSession && cropAspectPreset === 'free') imageEditorSession.cropBaseline = imageEditorBoxFractions();
}

function imageEditorBoxFractions(){
    const {w, h} = cropBounds();
    return {x:cropState.x / w, y:cropState.y / h, w:cropState.w / w, h:cropState.h / h};
}

function initializeImageEditorBox(){
    const saved = imageEditorSession.boxes[imageEditMode];
    if(saved){
        const {w, h} = cropBounds();
        Object.assign(cropState, {x:saved.x * w, y:saved.y * h, w:saved.w * w, h:saved.h * h});
        renderCropBox();
    } else if(imageEditMode === 'crop') resetCropBox();
    else if(imageEditMode === 'outpaint') resetOutpaintBox();
    syncImageEditOverflow();
}

function openImageEditor(nodeId, imageIndex=0){
    if(imageEditorSession?.applying || imageEditorSession?.discarding) return;
    const node = canvasState.nodes.find(n => n.id === nodeId);
    const image = canvasCore.imageForDisplay(node?.images?.[imageIndex]);
    if(!image?.url) return;
    const kind = canvasCore.mediaKindForItem(image);
    if(kind !== 'image' && kind !== 'video'){
        canvasCore.downloadPreviewFile(image);
        return;
    }
    canvasState.selectedId = nodeId;
    canvasState.selectedImage = {nodeId, index:imageIndex};
    previewNavState = {nodeId, index:imageIndex, count:(node.images || []).filter(img => img?.url).length};
    imageEditorSession?.stageObserver?.disconnect();
    const session = imageEditorSession = {loaded:false, applying:false, discarding:false, boxes:Object.create(null), drawings:Object.create(null), cropBaseline:null, stageObserver:null};
    cropState = {nodeId, imageIndex, x:0, y:0, w:0, h:0};
    imageEditMode = 'preview';
    cropDrag = null; editDrawState = null;
    clearEditDrawing(true);
    resetEditDrawingHistory();
    gridCustomMode = false; gridCustomLines = []; gridCustomHistory = []; gridCustomDrag = null; gridCustomOrientation = 'h';
    gridOperationMode = 'split'; gridJoinLayout = null; gridJoinDrag = null; gridJoinImageCache = new Map(); gridJoinUserMoved = false; gridJoinGroupId = '';
    imageEditZoom = 1.0; imageEditBaseW = 0; imageEditBaseH = 0; imageResizeScale = 0.5;
    cropAspectPreset = 'free'; cropAspectRatio = null; syncCropRatioButtons();
    outpaintAspectPreset = 'free'; syncOutpaintRatioButtons();
    const toggle = document.getElementById('gridCustomToggle');
    if(toggle){ toggle.classList.add('secondary'); toggle.classList.remove('primary'); }
    syncGridCustomControls();
    syncGridOperationControls();
    ['gridHorizontalLines','gridVerticalLines'].forEach(id => { const el = document.getElementById(id); if(el) el.disabled = false; });
    const orientH = document.getElementById('gridOrientH'), orientV = document.getElementById('gridOrientV');
    if(orientH){ orientH.classList.add('primary'); orientH.classList.remove('secondary'); }
    if(orientV){ orientV.classList.add('secondary'); orientV.classList.remove('primary'); }
    syncGridCustomUndoBtn(); updateZoomLabel();
    const img = document.getElementById('cropImage');
    img.style.width = ''; img.style.height = ''; img.style.maxWidth = ''; img.style.maxHeight = '';
    img.onload = null; img.onerror = null; img.removeAttribute('src');
    renderCropBox();
    imageEditModal.classList.add('open');
    releaseImageEditorEscape();
    releaseImageEditorEscape = window.FireflyEscapeStack?.push(() => closeImageEditor(), 'image-editor') || (() => {});
    previewCompareOn = false;
    previewCompareIndex = -1;
    resetPreviewTransform();
    setImageEditMode('preview');
    if(kind === 'video') return;
    const stage = document.getElementById('imageEditStage');
    if(stage && typeof ResizeObserver === 'function'){
        // A toolbar can wrap after its translated labels or grid count have been rendered.
        session.stageObserver = new ResizeObserver(() => {
            if(imageEditorSession !== session || !session.loaded || imageEditMode === 'preview') return;
            const w = imageEditBaseW, h = imageEditBaseH;
            ensureImageEditBaseSize(true);
            if(w !== imageEditBaseW || h !== imageEditBaseH) applyImageEditZoom();
        });
        session.stageObserver.observe(stage, {box:'border-box'});
    }
    // 原图加载失败时的兜底链：依次尝试 download-output 代理、缩略图同款的 media-preview 代理（PIL 渲染，
    // 对截断/半下载的文件比浏览器宽容，所以缩略图能显示而原图破损时它仍能出图）。兜底时去掉 crossOrigin——
    // 预览不需要导出画布，带 crossOrigin 反而会因跨域/CORS 直接加载失败。
    const primaryEditorSrc = canvasCore.displayMediaUrl(image);
    const editorFallbackUrls = [proxiedMediaUrl(image), smartMediaPreviewUrl(image, 2048)]
        .filter(Boolean)
        .filter((u, i, arr) => u !== primaryEditorSrc && arr.indexOf(u) === i);
    let editorFallbackIndex = 0;
    let request = null;
    let primaryRequested = false;
    const loadPrimary = () => {
        if(primaryRequested || imageEditorSession !== session) return;
        primaryRequested = true;
        loadEditorSource(primaryEditorSrc, true);
    };
    const loadEditorSource = (src, primary=false) => {
        if(imageEditorSession !== session) return;
        const token = request = {};
        let settled = false;
        img.onload = () => {
            if(imageEditorSession !== session || request !== token || settled || !img.complete || img.currentSrc !== img.src || !img.naturalWidth) return;
            settled = true;
            // Preview and fallback sizes are not the source media's dimensions.
            const targetImage = node.images?.[imageIndex];
            if(primary && targetImage && (!targetImage.natural_w || !targetImage.natural_h)){
                targetImage.natural_w = img.naturalWidth;
                targetImage.natural_h = img.naturalHeight;
                canvasCore.scheduleStateSave();
            }
            const first = !session.loaded;
            session.loaded = true;
            if(editTextInlineEditor) removeEditTextInlineEditor(true);
            if(editDrawState) endEditDraw({pointerId:editDrawState.pointerId});
            if(editTextDrag) endEditText();
            cropDrag = null;
            ensureImageEditBaseSize(true);
            applyImageEditZoom();
            if(first && !cropState.w) initializeImageEditorBox();
            syncImageEditApplyState();
            updatePreviewMetaHint();
        };
        img.onerror = () => {
            if(imageEditorSession !== session || request !== token || settled || !img.complete) return;
            settled = true;
            if(!primaryRequested) loadPrimary();
            else if(editorFallbackIndex < editorFallbackUrls.length) loadEditorSource(editorFallbackUrls[editorFallbackIndex++]);
        };
        img.src = src;
    };
    // 不设 crossOrigin：displayMediaUrl 已把所有地址收敛为同源（http 走本地代理），同源图片不会污染画布，
    // 裁剪/涂抹等导出操作照常可用。而带 crossOrigin 会让浏览器对“缩略图已无 CORS 缓存的同源图”重新发起
    // CORS 请求并失败——表现就是预览先闪一下（命中缓存）随即变成破损图。
    img.removeAttribute('crossorigin');
    const quickEditorSrc = smartMediaPreviewUrl(image, 2048);
    if(quickEditorSrc && quickEditorSrc !== primaryEditorSrc){
        loadEditorSource(quickEditorSrc);
        requestAnimationFrame(() => setTimeout(loadPrimary, 120));
    } else loadPrimary();
}

function imageEditorHasDraft(session){
    if(editTextInlineEditor) removeEditTextInlineEditor(true);
    if(isImageDrawingMode(imageEditMode) && editCanvasHasPixels()) return true;
    if(Object.values(session.drawings).some(drawing => drawing.dirty)) return true;
    if(imageResizeScale !== 0.5 || gridCustomLines.length || gridJoinUserMoved) return true;
    const {w, h} = cropBounds();
    const differs = (box, baseline) => box && baseline && ['x', 'y', 'w', 'h'].some(key => {
        const dimension = key === 'x' || key === 'w' ? w : h;
        return Math.abs(box[key] - baseline[key]) * dimension > 0.5;
    });
    const crop = imageEditMode === 'crop' && cropState.w ? imageEditorBoxFractions() : session.boxes.crop;
    const outpaint = imageEditMode === 'outpaint' && cropState.w ? imageEditorBoxFractions() : session.boxes.outpaint;
    return !!(crop && (cropAspectPreset !== 'free' || differs(crop, session.cropBaseline)))
        || !!differs(outpaint, {x:0, y:0, w:1, h:1});
}

async function closeImageEditor(backdropEvent){
    if(backdropEvent){
        backdropEvent.stopPropagation();
        if(backdropEvent.target !== imageEditModal) return;
    }
    const session = imageEditorSession;
    // Dialog has its own document Escape listener; do not replace it or dismiss both layers.
    if(!session || session.applying || session.discarding || document.querySelector('.ff-dialog-backdrop')) return;
    if(imageEditorHasDraft(session)){
        session.discarding = true;
        try {
            const confirmed = await window.ffDialog.ask({
                title:tr('smart.discardImageEditsTitle'),
                message:tr('smart.discardImageEditsMessage'),
                confirmText:tr('smart.discardImageEditsConfirm'),
                cancelText:tr('common.cancel'),
                danger:true
            });
            if(!confirmed || imageEditorSession !== session) return;
        } finally {
            session.discarding = false;
        }
    }
    dismissImageEditor();
}

function dismissImageEditor(){
    imageEditorSession?.stageObserver?.disconnect();
    imageEditorSession = null;
    syncImageEditApplyState();
    releaseImageEditorEscape();
    releaseImageEditorEscape = () => {};
    cleanupSmartHistoryPreviewNode();
    imageEditModal.classList.remove('open');
    document.querySelector('.image-edit-panel')?.classList.remove('video-preview-mode');
    const img = document.getElementById('cropImage');
    const previewVideo = document.getElementById('previewCurrentVideo');
    img.onload = null; img.onerror = null; img.removeAttribute('src'); img.style.width = ''; img.style.height = ''; img.style.maxWidth = ''; img.style.maxHeight = '';
    img.style.position = ''; img.style.left = ''; img.style.top = '';
    if(previewVideo){
        previewVideo.pause?.();
        previewVideo.onloadedmetadata = null;
        previewVideo.onloadeddata = null;
        previewVideo.removeAttribute('src');
        previewVideo.load?.();
        previewVideo.style.display = 'none';
    }
    clearEditDrawing(true);
    cropState = null; cropDrag = null; editDrawState = null; resetEditDrawingHistory(); gridCustomDrag = null; gridJoinDrag = null; gridJoinLayout = null; gridJoinImageCache = new Map(); gridJoinUserMoved = false; gridOperationMode = 'split'; gridJoinGroupId = '';
    previewNavState = {nodeId:'', index:0, count:0};
    imageEditZoom = 1.0; imageEditBaseW = 0; imageEditBaseH = 0; imageResizeScale = 0.5;
    cropAspectPreset = 'free'; cropAspectRatio = null; syncCropRatioButtons();
    outpaintAspectPreset = 'free'; syncOutpaintRatioButtons();
    previewPanDrag = null; previewCompareDrag = false; imageEditPanDrag = null; resetPreviewTransform();
    document.getElementById('imageEditStage')?.classList.remove('overflow-x', 'overflow-y', 'preview-mode');
    const cropCanvasEl = document.getElementById('cropCanvas');
    cropCanvasEl?.classList.remove('grid-custom-h', 'grid-custom-v', 'outpaint-mode', 'outpaint-warning', 'dragging-image', 'text-mode', 'resize-mode');
    cropCanvasEl?.classList.remove('grid-join-mode');
    document.getElementById('cropImage')?.classList.remove('grid-join-hidden');
    const joinCanvas = document.getElementById('gridJoinCanvas');
    if(joinCanvas){ joinCanvas.innerHTML = ''; joinCanvas.style.display = 'none'; joinCanvas.style.width = ''; joinCanvas.style.height = ''; }
    if(cropCanvasEl){ cropCanvasEl.style.width = ''; cropCanvasEl.style.height = ''; }
    const textCanvas = editTextCanvas();
    if(textCanvas){ textCanvas.style.left = ''; textCanvas.style.top = ''; }
    updatePreviewNavButtons();
}

function clampCrop(){
    if(!cropState) return;
    if(imageEditMode === 'outpaint') return clampOutpaint();
    const {w, h} = cropBounds();
    cropState.w = Math.max(24, Math.min(cropState.w, w)); cropState.h = Math.max(24, Math.min(cropState.h, h));
    cropState.x = Math.max(0, Math.min(cropState.x, w - cropState.w)); cropState.y = Math.max(0, Math.min(cropState.y, h - cropState.h));
}

function beginCropDrag(event, mode){
    if(!cropState) return;
    event.preventDefault(); event.stopPropagation();
    if(imageEditMode === 'outpaint' && mode === 'move') return;
    cropDrag = {mode, sx:event.clientX, sy:event.clientY, start:{...cropState}};
}

function resizeOutpaintFromDrag(dx, dy){
    const start = cropDrag?.start;
    if(!start) return;
    let growX = 0, growY = 0;
    if(cropDrag.mode === 'outpaint-left') growX = -dx;
    else if(cropDrag.mode === 'outpaint-right') growX = dx;
    else if(cropDrag.mode === 'outpaint-top') growY = -dy;
    else if(cropDrag.mode === 'outpaint-bottom') growY = dy;
    else if(cropDrag.mode === 'outpaint-corner'){ growX = dx; growY = dy; }
    const {w, h} = cropBounds();
    let nextW = Math.max(w, start.w + growX * 2);
    let nextH = Math.max(h, start.h + growY * 2);
    
    // 如果选择了预设比例，保持该比例
    if(outpaintAspectPreset && outpaintAspectPreset !== 'free' && outpaintAspectPreset !== 'source'){
        const parsed = window.OutpaintRatios?.parsePreset(outpaintAspectPreset);
        if(parsed){
            // 保持中心点，按预设比例调整
            const centerX = start.x + start.w / 2;
            const centerY = start.y + start.h / 2;
            // 以较大的增长方向为准，按预设比例计算另一个方向
            if(nextW / nextH > parsed.width / parsed.height){
                // 宽度增长更多，按预设比例调整高度
                nextH = nextW * parsed.height / parsed.width;
            } else {
                // 高度增长更多，按预设比例调整宽度
                nextW = nextH * parsed.width / parsed.height;
            }
            // 确保不小于原图尺寸
            nextW = Math.max(w, nextW);
            nextH = Math.max(h, nextH);
            // 重新计算位置，保持中心
            cropState.x = centerX - nextW / 2;
            cropState.y = centerY - nextH / 2;
        }
    } else {
        cropState.x = start.x + Math.round((nextW - start.w) / 2);
        cropState.y = start.y + Math.round((nextH - start.h) / 2);
    }
    
    cropState.w = nextW;
    cropState.h = nextH;
    clampOutpaint();
}

function clampAspectCropToBounds(anchorX, anchorY, movingX, movingY, ratio, handle){
    const {w:boundsW, h:boundsH} = cropBounds();
    const minSize = 24;
    let width = Math.max(minSize, Math.abs(movingX - anchorX));
    let height = Math.max(minSize, Math.abs(movingY - anchorY));
    if(width / height > ratio) width = height * ratio;
    else height = width / ratio;
    const dirX = handle.includes('w') ? -1 : 1;
    const dirY = handle.includes('n') ? -1 : 1;
    width = Math.min(width, dirX < 0 ? anchorX : boundsW - anchorX);
    height = Math.min(height, dirY < 0 ? anchorY : boundsH - anchorY);
    if(width / height > ratio) width = height * ratio;
    else height = width / ratio;
    return {
        x:dirX < 0 ? anchorX - width : anchorX,
        y:dirY < 0 ? anchorY - height : anchorY,
        w:width,
        h:height
    };
}

function resizeCropFromDrag(dx, dy){
    const start = cropDrag?.start;
    if(!start) return;
    const handle = String(cropDrag.mode || 'resize').replace(/^crop-/, '') || 'se';
    if(!cropAspectRatio){
        let left = start.x;
        let top = start.y;
        let right = start.x + start.w;
        let bottom = start.y + start.h;
        if(handle.includes('w')) left += dx;
        if(handle.includes('e') || handle === 'resize') right += dx;
        if(handle.includes('n')) top += dy;
        if(handle.includes('s') || handle === 'resize') bottom += dy;
        cropState.x = Math.min(left, right - 24);
        cropState.y = Math.min(top, bottom - 24);
        cropState.w = Math.max(24, right - cropState.x);
        cropState.h = Math.max(24, bottom - cropState.y);
        return;
    }
    const normalized = handle === 'resize' ? 'se' : handle;
    const centerX = start.x + start.w / 2;
    const centerY = start.y + start.h / 2;
    if(normalized === 'e' || normalized === 'w'){
        const {w:boundsW, h:boundsH} = cropBounds();
        let width = Math.max(24, normalized === 'e' ? start.w + dx : start.w - dx);
        const maxW = normalized === 'e' ? boundsW - start.x : start.x + start.w;
        const maxH = Math.max(24, 2 * Math.min(centerY, boundsH - centerY));
        width = Math.min(width, maxW, maxH * cropAspectRatio);
        const height = width / cropAspectRatio;
        cropState.x = Math.round(normalized === 'e' ? start.x : start.x + start.w - width);
        cropState.y = Math.round(centerY - height / 2);
        cropState.w = Math.round(width);
        cropState.h = Math.round(height);
        return;
    }
    if(normalized === 'n' || normalized === 's'){
        const {w:boundsW, h:boundsH} = cropBounds();
        let height = Math.max(24, normalized === 's' ? start.h + dy : start.h - dy);
        const maxH = normalized === 's' ? boundsH - start.y : start.y + start.h;
        const maxW = Math.max(24, 2 * Math.min(centerX, boundsW - centerX));
        height = Math.min(height, maxH, maxW / cropAspectRatio);
        const width = height * cropAspectRatio;
        cropState.x = Math.round(centerX - width / 2);
        cropState.y = Math.round(normalized === 's' ? start.y : start.y + start.h - height);
        cropState.w = Math.round(width);
        cropState.h = Math.round(height);
        return;
    }
    const anchorX = normalized.includes('w') ? start.x + start.w : start.x;
    const anchorY = normalized.includes('n') ? start.y + start.h : start.y;
    const movingX = normalized.includes('w') ? start.x + dx : start.x + start.w + dx;
    const movingY = normalized.includes('n') ? start.y + dy : start.y + start.h + dy;
    const next = clampAspectCropToBounds(anchorX, anchorY, movingX, movingY, cropAspectRatio, normalized);
    cropState.x = Math.round(next.x);
    cropState.y = Math.round(next.y);
    cropState.w = Math.round(next.w);
    cropState.h = Math.round(next.h);
}

async function uploadCroppedBlob(blob, name){
    const form = new FormData();
    form.append('files', blob, name);
    const res = await fetch('/api/ai/upload', {method:'POST', body:form});
    const data = await res.json().catch(() => ({}));
    if(!res.ok){
        // #348: 上传失败以前静默（.then(r => r.json()) 不检查 res.ok）——用户点了裁剪/扩图却无反应。
        const detail = typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`;
        console.warn('[image-editor] 编辑结果上传失败', name, detail);
        window.ffToast?.error?.(`编辑结果上传失败：${detail}`);
        return null;
    }
    return data.files?.[0] || null;
}

function applyOutpaintSizeToSmartParams(width, height, subject, aspectPreset, defaultSettings){
    const w = Math.max(1, Math.round(Number(width) || 0));
    const h = Math.max(1, Math.round(Number(height) || 0));
    if(!w || !h) return;
    if(!subject || !isSmartImageNode(subject)) return;
    const outpaintAspectRatio = /^\d+\s*:\s*\d+$/.test(aspectPreset)
        ? aspectPreset.replace(/\s/g, '')
        : canvasCore.reducedAspectRatio(w, h);
    subject.outpaintSize = {width:w, height:h, aspectRatio:outpaintAspectRatio};
    subject.runSettings = withOutpaintDisplaySettings(subject, cloneSmartSettings(subject.runSettings || defaultSettings));
    if(activeSettingsSubject()?.id === subject.id){
        settings = canvasCore.smartSettingsForNode(subject);
        renderDynamicParams();
    }
}

async function applyImageCrop(){
    if(!cropState) return;
    const {node, image} = currentEditImage();
    const img = document.getElementById('cropImage');
    if(!node || !image || !img.naturalWidth || !img.naturalHeight) return;
    // #255：显示像素 → 原图像素走分数坐标骨架（见 editScaleRectToNatural）。
    const target = editScaleRectToNatural(cropState, img);
    const sx = Math.max(0, Math.round(target.x)), sy = Math.max(0, Math.round(target.y));
    const sw = Math.max(1, Math.round(target.w)), sh = Math.max(1, Math.round(target.h));
    const editTarget = captureImageEditTarget();
    const canvasEl = document.createElement('canvas');
    canvasEl.width = sw; canvasEl.height = sh;
    canvasEl.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
    const blob = await new Promise(resolve => canvasEl.toBlob(resolve, 'image/png'));
    const base = (image.name || 'image').replace(/\.[^.]+$/, '');
    const file = blob ? await uploadCroppedBlob(blob, `${base}_crop.png`) : null;
    if(file && replaceEditedImage(editTarget, file)) finishImageEdit(editTarget);
}

async function applyImageOutpaint(){
    if(!cropState) return;
    const {node, image} = currentEditImage();
    const img = document.getElementById('cropImage');
    if(!node || !image || !img.naturalWidth || !img.naturalHeight) return;
    clampOutpaint();
    // #255：显示像素 → 原图像素走分数坐标骨架（见 editScaleRectToNatural）。
    const target = editScaleRectToNatural(cropState, img);
    const outW = Math.max(img.naturalWidth, Math.round(target.w));
    const outH = Math.max(img.naturalHeight, Math.round(target.h));
    const dx = Math.round(target.x);
    const dy = Math.round(target.y);
    const editTarget = captureImageEditTarget();
    const aspectPreset = outpaintAspectPreset;
    const defaultSettings = cloneSmartSettings(node.runSettings || settings);
    const canvasEl = document.createElement('canvas');
    canvasEl.width = outW; canvasEl.height = outH;
    const ctx = canvasEl.getContext('2d');
    // 使用高饱和红色填充扩图区域：相比白色，红色在真实场景中极少出现，
    // 提示词 "remove the red background/area" 更稳定地被模型识别并移除。
    ctx.fillStyle = '#ff0044';
    ctx.fillRect(0, 0, outW, outH);
    ctx.drawImage(img, dx, dy, img.naturalWidth, img.naturalHeight);
    const blob = await new Promise(resolve => canvasEl.toBlob(resolve, 'image/png'));
    const base = (image.name || 'image').replace(/\.[^.]+$/, '');
    const file = blob ? await uploadCroppedBlob(blob, `${base}_outpaint_${Date.now()}.png`) : null;
    if(!file || !liveImageEditTarget(editTarget)) return;
    const owned = await importProjectMediaReference(file);
    const live = liveImageEditTarget(editTarget);
    if(!owned?.asset_id || !live) return;
    // The composite is an input for a later manual run, never a replacement for the source (#513).
    const rect = canvasCore.nodeRect(live.node);
    const select = cropState === editTarget.editor;
    const output = createNode(rect.x + rect.width + 40, rect.y, [{
        url:file.url, name:file.name, kind:'image', asset_id:owned.asset_id, natural_w:outW, natural_h:outH
    }], {select});
    if(select){
        canvasState.selectedIds = [];
        canvasState.selectedImage = {nodeId:output.id, index:0};
    }
    applyOutpaintSizeToSmartParams(outW, outH, output, aspectPreset, defaultSettings);
    setPromptDraftForNode(output, tr('smart.outpaintPromptSuggestion'));
    finishImageEdit(editTarget);
}

async function applyImageMask(){
    if(!cropState || !editCanvasHasPixels()) return;
    const {node, image} = currentEditImage();
    if(!node || !image) return;
    const editTarget = captureImageEditTarget();
    const mask = maskCanvasFromDrawCanvas(editDrawCanvas());
    const blob = await new Promise(resolve => mask.toBlob(resolve, 'image/png'));
    const base = (image.name || 'image').replace(/\.[^.]+$/, '');
    const file = blob ? await uploadCroppedBlob(blob, `${base}_mask.png`) : null;
    if(file){
        const live = liveImageEditTarget(editTarget);
        if(!live) return;
        const action = beginMediaHumanEdit([live.node], ['images']);
        pushUndo();
        live.node.images.push({url:file.url, name:file.name, role:'mask'});
        canvasCore.finishSmartHumanEdit(action, canvasState.nodes);
        if(cropState === editTarget.editor){
            canvasState.selectedId = live.node.id;
            canvasState.selectedImage = {nodeId:live.node.id, index:live.node.images.length - 1};
        }
        finishImageEdit(editTarget);
    }
}

function maskCanvasFromDrawCanvas(src){
    const mask = document.createElement('canvas');
    mask.width = src.width;
    mask.height = src.height;
    const srcCtx = src.getContext('2d');
    const srcData = srcCtx.getImageData(0, 0, src.width, src.height);
    const ctx = mask.getContext('2d');
    const out = ctx.createImageData(mask.width, mask.height);
    for(let i = 0; i < srcData.data.length; i += 4){
        const painted = srcData.data[i + 3] > 8;
        const v = painted ? 255 : 0;
        out.data[i] = v;
        out.data[i + 1] = v;
        out.data[i + 2] = v;
        out.data[i + 3] = 255;
    }
    ctx.putImageData(out, 0, 0);
    return mask;
}

async function applyImageBrush(){
    if(!cropState) return;
    removeEditTextInlineEditor(true);
    if(!editCanvasHasPixels()) return;
    const {node, image} = currentEditImage();
    const img = document.getElementById('cropImage');
    if(!node || !image || !img.naturalWidth || !img.naturalHeight) return;
    const canvasEl = document.createElement('canvas');
    canvasEl.width = img.naturalWidth; canvasEl.height = img.naturalHeight;
    const editTarget = captureImageEditTarget();
    const ctx = canvasEl.getContext('2d');
    ctx.drawImage(img, 0, 0, canvasEl.width, canvasEl.height); ctx.drawImage(editDrawCanvas(), 0, 0); ctx.drawImage(editTextCanvas(), 0, 0);
    const blob = await new Promise(resolve => canvasEl.toBlob(resolve, 'image/png'));
    const base = (image.name || 'image').replace(/\.[^.]+$/, '');
    const file = blob ? await uploadCroppedBlob(blob, `${base}_paint.png`) : null;
    if(file && replaceEditedImage(editTarget, file)) finishImageEdit(editTarget);
}

async function applyImageGridSplit(){
    if(!cropState) return;
    if(gridOperationMode === 'join') return applyImageGridJoin();
    const {node, image} = currentEditImage();
    const img = document.getElementById('cropImage');
    if(!node || !image || !img.naturalWidth || !img.naturalHeight) return;
    const rects = gridSplitRects(img.naturalWidth, img.naturalHeight).sort((a, b) => (Number(a.row || 0) - Number(b.row || 0)) || (Number(a.col || 0) - Number(b.col || 0)));
    if(!rects.length) return;
    const base = safeExportFileName((downloadNameForMediaItem(image, 'image') || 'image').replace(/\.[^.]+$/, ''), 'image');
    const digits = String(rects.length).length;
    const editTarget = captureImageEditTarget();
    // Snapshot all source pixels before yielding: the shared editor <img> may
    // navigate while toBlob/upload is pending.
    const blobs = await Promise.all(rects.map(async (rect, i) => {
        const canvasEl = document.createElement('canvas');
        canvasEl.width = rect.w; canvasEl.height = rect.h;
        canvasEl.getContext('2d').drawImage(img, rect.x, rect.y, rect.w, rect.h, 0, 0, rect.w, rect.h);
        const blob = await new Promise(resolve => canvasEl.toBlob(resolve, 'image/png'));
        const order = String(i + 1).padStart(digits, '0');
        return {blob, name:`${base}_${order}_r${rect.row + 1}_c${rect.col + 1}.png`};
    }));
    // Canvas.toBlob explicitly returns null when encoding fails. Do not drop
    // that tile and silently upload a partial grid with shifted cell indexes.
    if(blobs.some(item => item.blob === null)){
        toast(tr('smart.gridSplitEncodeFailed'));
        return false;
    }
    let files;
    try {
        files = await uploadImageBlobs(blobs);
    } catch(error) {
        console.warn('[image-editor] grid split upload failed', error);
        toast(tr('smart.gridSplitUploadFailed'));
        return false;
    }
    if(files?.length !== rects.length || files.some(file => !file?.url)){
        toast(tr('smart.gridSplitUploadFailed'));
        return false;
    }
    const live = liveImageEditTarget(editTarget);
    if(!live) return false;
    const source = live.node;
    const layout = gridLayoutFromRects(rects);
    createNode((source.x || 0) + canvasCore.imageLayout(source.images, nodeScale(source), source).width + 40, source.y || 0, files.map((file, i) => ({
        url:file.url, name:file.name,
        grid:{...layout, row:rects[i].row, col:rects[i].col, w:rects[i].w, h:rects[i].h}
    })), {title:tr('canvas.gridSplit')});
    finishImageEdit(editTarget);
}

function loadGridJoinImage(entry){
    const cache = gridJoinImageCache;
    const cached = cache.get(entry.index);
    if(cached?.complete && cached.naturalWidth) return Promise.resolve(cached);
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
            cache.set(entry.index, img);
            resolve(img);
        };
        img.onerror = () => {
            if(img.dataset.proxyFallbackTried === '1'){
                reject(new Error(tr('smart.imageLoadFailed')));
                return;
            }
            const fallback = proxiedMediaUrl(entry.item);
            if(!fallback || fallback === img.src){
                reject(new Error(tr('smart.imageLoadFailed')));
                return;
            }
            img.dataset.proxyFallbackTried = '1';
            img.src = fallback;
        };
        img.src = canvasCore.displayMediaUrl(entry.item);
    });
}

async function applyImageGridJoin(){
    const {node, image} = currentEditImage();
    const items = currentGridJoinItems();
    if(!node || items.length <= 1){ toast(tr('smart.gridJoinNeedsGroup')); return; }
    const layout = ensureGridJoinLayout();
    if(!layout?.items?.length) return;
    const editTarget = captureImageEditTarget();
    const sources = items.map(entry => {
        const owner = canvasState.nodes.find(item => Array.isArray(item.images) && item.images.includes(entry.source));
        return imageEditTarget(owner, owner.images.indexOf(entry.source));
    });
    const size = gridJoinCanvasSize(layout);
    const targetLong = Math.max(256, Number(gridJoinOutputSize) || 2048);
    const outputScale = Math.max(1, targetLong / Math.max(1, Math.max(size.w, size.h)));
    const canvasEl = document.createElement('canvas');
    canvasEl.width = Math.max(1, Math.round(size.w * outputScale));
    canvasEl.height = Math.max(1, Math.round(size.h * outputScale));
    const loaded = new Map(await Promise.all(items.map(async entry => [entry.index, await loadGridJoinImage(entry)])));
    const tiles = layout.items.map(item => ({
        img:loaded.get(item.index), x:Math.round(item.x * outputScale), y:Math.round(item.y * outputScale),
        w:Math.max(1, Math.round(item.w * outputScale)), h:Math.max(1, Math.round(item.h * outputScale))
    }));
    if(tiles.some(({img, w, h}) => {
        const scale = Math.max(w / img.naturalWidth, h / img.naturalHeight);
        return img.naturalWidth * scale - w > 1 || img.naturalHeight * scale - h > 1;
    }) && !await window.ffDialog.ask({
        title:tr('smart.gridJoinCropTitle'), message:tr('smart.gridJoinCropMessage'),
        confirmText:tr('smart.outputGridJoin'), cancelText:tr('common.cancel')
    })) return false;
    if(!liveImageEditTarget(editTarget) || sources.some(source => !liveImageEditTarget(source))) return false;
    const ctx = canvasEl.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvasEl.width, canvasEl.height);
    for(const {img, x, y, w, h} of tiles) drawImageCover(ctx, img, x, y, w, h);
    const blob = await new Promise(resolve => canvasEl.toBlob(resolve, 'image/png'));
    const base = safeExportFileName((downloadNameForMediaItem(image || items[0]?.item, 'image') || 'image').replace(/\.[^.]+$/, ''), 'image');
    const file = blob ? await uploadCroppedBlob(blob, `${base}_join.png`) : null;
    if(file){
        const live = liveImageEditTarget(editTarget);
        if(!live || sources.some(source => !liveImageEditTarget(source))) return;
        const rect = canvasCore.nodeRect(live.node);
        createImageNodeAt({x:rect.x + rect.width + 240, y:rect.y + rect.height / 2}, [{
            url:file.url,
            name:file.name,
            kind:'image',
            natural_w:canvasEl.width,
            natural_h:canvasEl.height
        }], {select:true, title:tr('canvas.gridJoin')});
        finishImageEdit(editTarget);
        toast(tr('smart.gridJoinOutputDone'));
    }
}

async function applyImageResize(){
    if(!cropState) return;
    const {node, image} = currentEditImage();
    if(!node || !image) return;
    const editTarget = captureImageEditTarget();
    let resized = null;
    try {
        resized = await resizedImageBlobFromEditor();
    } catch(err) {
        toast(tr('smart.zoomFailedRewrite'));
        return;
    }
    if(!resized?.blob) return;
    const base = safeExportFileName((downloadNameForMediaItem(image, 'image') || image.name || 'image').replace(/\.[^.]+$/, ''), 'image');
    const suffix = `${Math.round(resized.scale * 100)}pct`;
    const file = await uploadCroppedBlob(resized.blob, `${base}_resize_${suffix}.png`);
    if(!file) return;
    if(!replaceEditedImage(editTarget, file, {kind:'image', role:image.role || '', natural_w:resized.targetW, natural_h:resized.targetH})){
        return;
    }
    finishImageEdit(editTarget);
}

function syncImageEditApplyState(){
    const applying = !!imageEditorSession?.applying;
    const applyBtn = document.getElementById('imageEditApplyBtn');
    if(applyBtn){
        applyBtn.disabled = applying || !imageEditorSession?.loaded;
        if(applying){
            applyBtn.setAttribute('aria-busy', 'true');
            applyBtn.setAttribute('aria-label', tr('smart.imageEditApplying'));
        } else {
            applyBtn.removeAttribute('aria-busy');
            applyBtn.removeAttribute('aria-label');
        }
    }
    const head = document.querySelector('.image-edit-head');
    if(head) head.inert = applying;
    const editLocked = applying || (imageEditMode !== 'preview' && !imageEditorSession?.loaded);
    for(const control of document.querySelectorAll('.image-edit-tools, .image-edit-stage')) control.inert = editLocked;
    const cancelBtn = document.getElementById('imageEditCancelBtn');
    if(cancelBtn) cancelBtn.disabled = applying;
}

async function applyImageEdit(){
    const session = imageEditorSession;
    if(!session?.loaded || session.applying || session.discarding) return;
    const worker = {
        crop:applyImageCrop, outpaint:applyImageOutpaint, mask:applyImageMask,
        brush:applyImageBrush, resize:applyImageResize, grid:applyImageGridSplit
    }[imageEditMode];
    if(!worker) return;
    session.applying = true;
    let needsError = false;
    try {
        if(isImageDrawingMode(imageEditMode)){
            if(editTextInlineEditor) removeEditTextInlineEditor(true);
            if(editDrawState) endEditDraw({pointerId:editDrawState.pointerId});
            if(!editCanvasHasPixels()) return;
        }
        syncImageEditApplyState();
        // Workers finish the session on success. False denotes user cancellation.
        needsError = (await worker()) !== false;
    } catch(error) {
        console.warn('[image-editor] apply failed', error);
        needsError = true;
    } finally {
        if(imageEditorSession === session){
            session.applying = false;
            syncImageEditApplyState();
            if(needsError){
                const message = tr('smart.imageEditApplyFailed');
                if(window.ffToast?.error) window.ffToast.error(message);
                else toast(message);
            }
        }
    }
}

function cropDragModeFromPointer(event){
    const explicit = event.target.closest?.('[data-crop-handle]')?.dataset?.cropHandle;
    if(explicit) return `crop-${explicit}`;
    if(imageEditMode !== 'crop') return 'move';
    const box = document.getElementById('cropBox');
    const rect = box?.getBoundingClientRect?.();
    if(!rect) return 'move';
    const slop = 16;
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const nearL = x <= slop;
    const nearR = rect.width - x <= slop;
    const nearT = y <= slop;
    const nearB = rect.height - y <= slop;
    if(nearT && nearL) return 'crop-nw';
    if(nearT && nearR) return 'crop-ne';
    if(nearB && nearL) return 'crop-sw';
    if(nearB && nearR) return 'crop-se';
    if(nearT) return 'crop-n';
    if(nearR) return 'crop-e';
    if(nearB) return 'crop-s';
    if(nearL) return 'crop-w';
    return 'move';
}

// 这些名字本来属于本模块，#200 从 smart-canvas.js 搬回（核心不引用它们，纯搬家）。
let previewPanDrag = null;
let previewCompareDrag = false;
let previewNavState = {nodeId:'', index:0, count:0};
function exceedsFourKStandard(width, height){
    const standard = nearestFourKSizeFor(width, height);
    if(!standard) return false;
    return Number(width) > standard.width || Number(height) > standard.height;
}
function updateImagePreviewMarkButton(){
    const btn = document.getElementById('imagePreviewMarkBtn');
    if(!btn) return;
    const ref = canvasState.selectedImage || {};
    const node = canvasState.nodes.find(n => n.id === ref.nodeId);
    const raw = node?.images?.[Number(ref.index || 0)];
    const item = canvasCore.imageForDisplay(raw) || raw;
    const assetId = String(raw?.asset_id || item?.asset_id || '');
    btn.dataset.markedAsset = assetId;
    btn.hidden = !assetId || canvasCore.mediaKindForItem(item) !== 'image' || imageEditMode !== 'preview';
    syncMarkedImageButtons();
}
function textItemFont(item){
    const size = Math.max(10, Math.min(120, Number(item?.size) || 28));
    return `900 ${size}px Arial, sans-serif`;
}
function beginTextEditChange(){
    if(editTextDirty) return;
    pushEditDrawHistory();
    editTextDirty = true;
}
function selectInlineEditorText(el){
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
}
function inlineEditorText(){
    return String(editTextInlineEditor?.el?.innerText || editTextInlineEditor?.el?.textContent || '').replace(/\u00a0/g, ' ');
}
function autosizeEditTextInlineEditor(){
    const editor = editTextInlineEditor;
    if(!editor?.el) return;
    const el = editor.el;
    el.style.width = 'auto';
    el.style.height = 'auto';
    el.style.width = `${Math.max(Number(editor.minW || 48), el.scrollWidth + 10)}px`;
    el.style.height = `${Math.max(Number(editor.minH || 28), el.scrollHeight + 4)}px`;
}
let previewCompareOn = false;
let previewCompareIndex = -1;
function resetPreviewTransform(){
    previewZoom = 1.0;
    previewPan = {x:0, y:0};
    previewComparePos = 50;
    document.getElementById('previewStage')?.style.setProperty('--compare-pos', `${previewComparePos}%`);
    applyPreviewTransform();
}
function editCanvasHasPixels(){
    if(editTextHasContent()) return true;
    const canvasEl = editDrawCanvas();
    return editImageDataHasPixels(canvasEl.getContext('2d').getImageData(0, 0, canvasEl.width, canvasEl.height));
}
function editImageDataHasPixels({data}){
    for(let i = 3; i < data.length; i += 4) if(data[i] > 0) return true;
    return false;
}
function drawNumberLabel(point){
    const ctx = editDrawCanvas().getContext('2d');
    const size = Math.max(18, editBrushSize() * 2.2);
    const text = circledNumber(brushLabelCounter++);
    setupDrawStyle(ctx);
    ctx.save(); ctx.font = `900 ${size}px Arial, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.lineWidth = Math.max(3, size / 8);
    ctx.strokeStyle = 'rgba(255,255,255,0.92)'; ctx.strokeText(text, point.x, point.y); ctx.fillStyle = brushColor(); ctx.fillText(text, point.x, point.y); ctx.restore();
}
function updatePreviewNavButtons(){
    let count;
    if(previewNavState.groupId && Array.isArray(previewNavState.seq)){
        count = previewNavState.seq.length;
    } else {
        const node = canvasState.nodes.find(n => n.id === previewNavState.nodeId);
        count = Math.max(0, (node?.images || []).filter(img => img?.url).length);
    }
    previewNavState.count = count;
    const show = imageEditModal.classList.contains('open') && imageEditMode === 'preview' && count > 1;
    document.getElementById('previewPrevBtn')?.classList.toggle('visible', show);
    document.getElementById('previewNextBtn')?.classList.toggle('visible', show);
}
function imageEditTarget(node, index){
    return {projectId:canvasId, nodeId:node.id, createdAt:node.created_at,
        image:JSON.parse(JSON.stringify(node.images[index])), editor:cropState};
}
function captureImageEditTarget(){
    const {node, index} = currentEditImage();
    return imageEditTarget(node, index);
}
function liveImageEditTarget(target){
    const node = canvasState.nodes.find(item => item.id === target.nodeId);
    // The upload boundary may outlive navigation, deletion, replacement or a
    // project switch. Match media identity, not the editor's current selection.
    const matches = node && Array.isArray(node.images) && canvasId === target.projectId && node.created_at === target.createdAt
        ? node.images.map((image, index) => ({image, index})).filter(({image}) => image
            && ['output_id', 'id', 'asset_id', 'url'].every(key => image[key] === target.image[key])) : [];
    if(matches.length !== 1){
        toast(tr('smart.imageEditTargetChanged'));
        return null;
    }
    return {node, index:matches[0].index};
}
function finishImageEdit(target){
    finalizePendingCanvasUndo();
    if(cropState && cropState === target.editor) dismissImageEditor();
    canvasCore.render();
    canvasCore.scheduleSave();
}
function replaceEditedImage(target, file, extra={}){
    const live = liveImageEditTarget(target);
    if(!live) return false;
    const {node, index} = live;
    // #205: Keep the star on the original asset; an edit must not inherit its asset ID.
    const assetId = (extra && extra.asset_id) || file.asset_id || '';
    const next = {...(node.images[index] || {}), url:file.url, name:file.name, kind:file.kind || canvasCore.mediaKindForItem(file), natural_w:0, natural_h:0, asset_id:assetId, ...extra};
    delete next.marked;
    const action = beginMediaHumanEdit([node], ['images', 'curated', 'w', 'h']);
    pushUndo();
    node.images[index] = next;
    if((node.images || []).length === 1){ delete node.w; delete node.h; }
    if(cropState === target.editor){
        canvasState.selectedId = node.id; canvasState.selectedImage = {nodeId:node.id, index};
    }
    // 旧图若是节点上唯一星标图，节点 curated（收集/导出的「保留」信号）需随真实星标重同步。
    syncNodeCuratedFromMarks(node);
    const changed = canvasCore.finishSmartHumanEdit(action, canvasState.nodes);
    if(!changed) finalizePendingCanvasUndo();
    return changed;
}
function drawImageCover(ctx, img, dx, dy, dw, dh){
    const sw = Math.max(1, Number(img?.naturalWidth || img?.videoWidth || img?.width || 1));
    const sh = Math.max(1, Number(img?.naturalHeight || img?.videoHeight || img?.height || 1));
    const targetW = Math.max(1, Number(dw || 1));
    const targetH = Math.max(1, Number(dh || 1));
    const scale = Math.max(targetW / sw, targetH / sh);
    const cropW = Math.max(1, targetW / scale);
    const cropH = Math.max(1, targetH / scale);
    const sx = Math.max(0, (sw - cropW) / 2);
    const sy = Math.max(0, (sh - cropH) / 2);
    ctx.drawImage(img, sx, sy, cropW, cropH, dx, dy, targetW, targetH);
}
function setPromptDraftForNode(node, text){
    if(!isSmartRunnableNode(node)) return;
    const value = String(text || '');
    node.promptDraftHtml = escapeHtml(value);
    node.promptDraftText = value;
    if(activeSettingsSubject()?.id === node.id && canvasCore.promptInput){
        canvasCore.promptInput.textContent = value;
        canvasCore.promptInput.dataset.preserveDraftOnce = '1';
    }
}
