// 画布撤销/重做模块（从 smart-canvas.js 抽离，#159 P2b）。
// 撤销/重做栈管理、快照、按钮同步、媒体悬空引用过滤。
// 引用主脚本全局（canvas/nodes/canvasState.selectedId/canvasState.selectedIds/canvasState.selectedImage/toast/tr/render
// scheduleSave/canvasState.activeComposerSubject/canvasState.lastComposerNodeId/canvasState.projectMediaLibrary 等），
// 本文件含顶层状态声明与函数定义，先于 smart-canvas.js 加载，
// 所有调用都发生在主脚本全局初始化之后，安全。
// 注意：媒体归档延迟（scheduleMediaArchive/cancelPendingMediaArchives）属 P2d 领域，
// 仍留在主脚本；restoreSmartProjectOriginalUndo 深度依赖主脚本其他函数，也留在主脚本。

const UNDO_LIMIT = 40;
const undoStack = [];
const redoStack = [];
let undoSuppressed = false;
let pendingUndoSnapshot = null;
let redoPushing = false;
const smartUndoButton = document.getElementById('smartUndoButton');
const smartRedoButton = document.getElementById('smartRedoButton');

function syncUndoRedoButtons(){
    if(smartUndoButton) smartUndoButton.disabled = !undoStack.length;
    if(smartRedoButton) smartRedoButton.disabled = !redoStack.length;
}

function capturePendingUndo(){ pendingUndoSnapshot = snapshotForUndo(); }

function commitPendingUndo(){
    if(pendingUndoSnapshot){
        undoStack.push(pendingUndoSnapshot);
        if(undoStack.length > UNDO_LIMIT) undoStack.shift();
        pendingUndoSnapshot = null;
        scheduleUndoStackPersist();
    }
}

function discardPendingUndo(){ pendingUndoSnapshot = null; }

function snapshotForUndo(){
    return {
        nodes: JSON.parse(JSON.stringify(canvasState.nodes)),
        connections: JSON.parse(JSON.stringify(canvas?.connections || [])),
        selectedId: canvasState.selectedId,
        selectedIds: canvasState.selectedIds.slice(),
        selectedImage: {...canvasState.selectedImage},
        // #272：批注=可持久画布对象 → 进 undo 快照（可撤销）；持久化走 anno_* 租户，不进 canvas.json。
        annotations: (canvasState.annotations || []).map(a => ({...a}))
    };
}

function pushUndo(extra=null){
    if(undoSuppressed) return;
    if(!canvas) return;
    const snapshot = snapshotForUndo();
    if(extra && typeof extra === 'object') Object.assign(snapshot, extra);
    undoStack.push(snapshot);
    if(undoStack.length > UNDO_LIMIT) undoStack.shift();
    if(!redoPushing && redoStack.length) redoStack.length = 0;
    syncUndoRedoButtons();
    scheduleUndoStackPersist();
}

async function performUndo(){
    // #146: 撤销时取消所有待归档，避免撤销恢复的节点引用已归档资产 → 404。
    cancelPendingMediaArchives();
    while(undoStack.length){
        const snap = undoStack.pop();
        if(snap.projectOriginalDelete){
            try {
                await restoreSmartProjectOriginalUndo(snap);
                toast(tr('smart.toastUndone'));
                scheduleUndoStackPersist();
                return;
            } catch(err){
                if(err?.permanentUndoFailure) continue;
                undoStack.push(snap);
                toast(err?.message || tr('smart.toastSaveFail'));
                return;
            }
        }
        redoStack.push(snapshotForUndo());
        if(redoStack.length > UNDO_LIMIT) redoStack.shift();
        undoSuppressed = true;
        // #62 B-5(L2): 兜底过滤——快照里引用的媒体若已被硬删（悬空 asset_id），
        // 服务端 upsert 校验会 400「项目媒体不存在」，画布会进入持续保存失败死锁。
        let restoredNodes = filterDanglingProjectMediaImages(snap.nodes);
        if(restoredNodes.dropped > 0 && await refreshProjectMediaLibraryItems()){
            // 内存库可能陈旧（本会话新生产的媒体尚未登记）→ 重新拉取后再验，避免误删存活图片
            restoredNodes = filterDanglingProjectMediaImages(snap.nodes);
        }
        canvasState.nodes = restoredNodes.nodes; if(canvas) canvas.nodes = canvasState.nodes;
        if(restoredNodes.dropped > 0) toast(tr('smart.toastDanglingMediaDropped'));
        if(canvas) canvas.connections = snap.connections;
        canvasState.selectedId = snap.selectedId;
        canvasState.selectedIds = snap.selectedIds;
        canvasState.selectedImage = snap.selectedImage;
        // #272：批注随 undo 快照恢复（render() 内的 renderAnnotationLayer 会重绘）
        canvasState.annotations = (snap.annotations || []).slice();
        canvasState.activeComposerSubject = null;
        canvasState.lastComposerNodeId = '';
        render();
        scheduleSave();
        undoSuppressed = false;
        toast(tr('smart.toastUndone'));
        syncUndoRedoButtons();
        scheduleUndoStackPersist();
        return;
    }
    toast(tr('smart.toastNoUndo'));
    syncUndoRedoButtons();
}

async function performRedo(){
    // #146: 重做时同样取消待归档（重做删除会让节点再次消失，归档应重新调度）。
    cancelPendingMediaArchives();
    while(redoStack.length){
        const snap = redoStack.pop();
        if(snap.projectOriginalDelete) continue;
        redoPushing = true;
        pushUndo();
        redoPushing = false;
        undoSuppressed = true;
        // #62 B-5(L2): 同 performUndo——过滤已硬删媒体的悬空引用，避免保存死锁。
        let restoredNodes = filterDanglingProjectMediaImages(snap.nodes);
        if(restoredNodes.dropped > 0 && await refreshProjectMediaLibraryItems()){
            restoredNodes = filterDanglingProjectMediaImages(snap.nodes);
        }
        // #146: 计算重做后丢失的图片（当前有、恢复后无），重新调度归档。
        const currentImages = [];
        canvasState.nodes.forEach(n => currentImages.push(...(n.images || [])));
        const restoredImageIds = new Set();
        restoredNodes.nodes.forEach(n => (n.images || []).forEach(img => {
            const id = img?.asset_id || '';
            if(id) restoredImageIds.add(id);
        }));
        const lostImages = currentImages.filter(img => {
            const id = img?.asset_id || '';
            return id && !restoredImageIds.has(id);
        });
        canvasState.nodes = restoredNodes.nodes; if(canvas) canvas.nodes = canvasState.nodes;
        if(restoredNodes.dropped > 0) toast(tr('smart.toastDanglingMediaDropped'));
        if(canvas) canvas.connections = snap.connections;
        canvasState.selectedId = snap.selectedId;
        canvasState.selectedIds = snap.selectedIds;
        canvasState.selectedImage = snap.selectedImage;
        // #272：批注随 redo 快照恢复（render() 内的 renderAnnotationLayer 会重绘）
        canvasState.annotations = (snap.annotations || []).slice();
        canvasState.activeComposerSubject = null;
        canvasState.lastComposerNodeId = '';
        render();
        scheduleSave();
        // #146: 重做删除后，对丢失的图片重新调度归档。
        if(lostImages.length) scheduleMediaArchive(lostImages);
        undoSuppressed = false;
        toast(tr('smart.toastRedone'));
        syncUndoRedoButtons();
        scheduleUndoStackPersist();
        return;
    }
    toast(tr('smart.toastNoRedo'));
    syncUndoRedoButtons();
}

// #62 B-5(L2): 应用 undo/redo 快照前，剔除项目媒体已不存在（已硬删）的 image 引用。
// 资源库未加载（空）时无法判定，跳过过滤——由保存 400 自愈（C-4）兜底。
function filterDanglingProjectMediaImages(snapshotNodes){
    const libraryItems = canvasState.projectMediaLibrary?.items || [];
    if(!Array.isArray(snapshotNodes) || !libraryItems.length) return {nodes: snapshotNodes, dropped: 0};
    const liveAssets = new Set(libraryItems.map(item => String(item?.asset_id || '')).filter(Boolean));
    let dropped = 0;
    const nodes = snapshotNodes.map(node => {
        if(!node || !Array.isArray(node.images)) return node;
        const images = node.images.filter(image => {
            const assetId = String(image?.asset_id || '');
            return !assetId || liveAssets.has(assetId);
        });
        if(images.length === node.images.length) return node;
        dropped += node.images.length - images.length;
        return {...node, images};
    });
    return {nodes, dropped};
}

// 内存媒体库刷新（仅项目媒体列表）：页面加载后新生产的媒体未登记进
// canvasState.projectMediaLibrary，悬空过滤会把存活媒体误判为悬空而剥离（复制→撤回丢光图片事故）。
async function refreshProjectMediaLibraryItems(){
    const projectId = canvas?.project_id || canvas?.project || sourceProjectId || canvasId || '';
    if(!projectId) return false;
    try {
        const res = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/media`);
        if(!res.ok) return false;
        const data = await res.json();
        if(data && Array.isArray(data.items)) canvasState.projectMediaLibrary = {items: data.items};
        return true;
    } catch { return false; }
}

// #62 B-5(L1): 媒体硬删成功后，移除仍引用它的 undo/redo 快照（返回移除数量）。
// 快照里的悬空引用会让下一次保存 400「项目媒体不存在」，画布进入持续保存失败死锁。
function stripDeletedMediaFromUndoStacks(assetId){
    if(!assetId) return 0;
    let removed = 0;
    const stripFrom = stack => {
        for(let index = stack.length - 1; index >= 0; index--){
            const snap = stack[index];
            if(!snap || !Array.isArray(snap.nodes)) continue;
            const hit = snap.nodes.some(node => Array.isArray(node?.images) && node.images.some(image => String(image?.asset_id || '') === assetId));
            if(hit){ stack.splice(index, 1); removed += 1; }
        }
    };
    stripFrom(undoStack);
    stripFrom(redoStack);
    if(removed){ syncUndoRedoButtons(); scheduleUndoStackPersist(); }
    return removed;
}

// #73: 清理与删除身份匹配的 projectOriginalDelete undo 令牌（彻底删除后恢复目标已不存在）。
function removeProjectOriginalUndoTokens(assetId, deletionId, projectId){
    if(!assetId) return;
    const match = token => token && token.assetId === assetId
        && (!deletionId || token.deletionId === deletionId)
        && (token.projectId || projectId) === projectId;
    let removed = 0;
    for(let index = undoStack.length - 1; index >= 0; index--){
        if(match(undoStack[index]?.projectOriginalDelete)){ undoStack.splice(index, 1); removed++; }
    }
    for(let index = redoStack.length - 1; index >= 0; index--){
        if(match(redoStack[index]?.projectOriginalDelete)){ redoStack.splice(index, 1); removed++; }
    }
    syncUndoRedoButtons();
    if(removed) scheduleUndoStackPersist();
}

// ── #251：undo/redo 栈跨刷新持久化（ops.db ADR 0021 租户之二：undo_snapshots）────────────────
// 快照栈原先只在内存（buildSavePayload 不含 undoStack，canvas 加载也不重建）→ 刷新即失忆。
// 现在整栈（undo + redo）按项目持久化进工程 ops.db 的 undo_snapshots 租户（单行 upsert，
// 后端裁剪到 40 条 = 本模块 UNDO_LIMIT 同口径）：任何栈变更触发防抖自动保存，页面加载时
// 由 smart-canvas.js loadCanvas 调 restorePersistedUndoStack() 从服务端拉回。
// 与 #257 versions 端点不同概念：这里是会话级 undo 连续性（不是版本树），不同表、勿混。
// 恢复的快照走同一条 performUndo/performRedo 路径——悬空媒体守卫（#62 L1/B-5、#146）原样生效。
const UNDO_PERSIST_DELAY_MS = 1500;
let undoPersistTimer = null;

// 只有项目画布才持久化（API 是 project-library 作用域）：classic 画布没有 project_id → 空。
function undoStackProjectId(){
    return canvas?.project_id || canvas?.project || '';
}

// 防抖自动保存：栈变更后等 UNDO_PERSIST_DELAY_MS 静默窗口发一次 PUT（连串操作合并成一条请求）。
function scheduleUndoStackPersist(){
    const projectId = undoStackProjectId();
    if(!projectId || !canvas) return;
    if(undoPersistTimer) clearTimeout(undoPersistTimer);
    undoPersistTimer = setTimeout(() => {
        undoPersistTimer = null;
        void persistUndoStackNow(projectId);
    }, UNDO_PERSIST_DELAY_MS);
}

async function persistUndoStackNow(projectId){
    try {
        // 发送时取整栈深拷贝（快照窗口内的最终状态；栈可能仍在变，下次变更会再排一次）。
        const payload = {
            undo: JSON.parse(JSON.stringify(undoStack)),
            redo: JSON.parse(JSON.stringify(redoStack)),
        };
        const res = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/undo-stack`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        if(!res.ok) console.warn('[canvas-undo] undo-stack 持久化失败', res.status);
    } catch(err) {
        console.warn('[canvas-undo] undo-stack 持久化失败', err && err.message);
    }
}

// 页面加载恢复：loadCanvas 完成后 fire-and-forget 调用（void，不阻塞渲染）。
// 本地栈非空时不覆盖（项目切换/已在操作中）；404（无 ops.db）= 首次使用，静默跳过。
async function restorePersistedUndoStack(){
    const projectId = undoStackProjectId();
    if(!projectId || !canvas) return;
    if(undoStack.length || redoStack.length) return;
    try {
        const res = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/undo-stack`);
        if(!res.ok){ syncUndoRedoButtons(); return; }   // 404（无 ops.db）= 首次使用；按钮与空栈对齐
        const data = await res.json();
        const undoEntries = (data && Array.isArray(data.undo)) ? data.undo : [];
        const redoEntries = (data && Array.isArray(data.redo)) ? data.redo : [];
        undoEntries.forEach(snap => { if(snap && typeof snap === 'object') undoStack.push(snap); });
        redoEntries.forEach(snap => { if(snap && typeof snap === 'object') redoStack.push(snap); });
        syncUndoRedoButtons();
    } catch(err) {
        // 静默：恢复失败只回到「刷新失忆」的旧行为，不影响画布可用。
    }
}
