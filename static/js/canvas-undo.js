// 画布撤销/重做模块（从 smart-canvas.js 抽离，#159 P2b）。
// 撤销/重做栈管理、快照、按钮同步、媒体悬空引用过滤。
// 引用主脚本全局（canvas/nodes/selectedId/selectedIds/selectedImage/toast/tr/render
// scheduleSave/activeComposerSubject/lastComposerNodeId/projectMediaLibrary 等），
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
    }
}

function discardPendingUndo(){ pendingUndoSnapshot = null; }

function snapshotForUndo(){
    return {
        nodes: JSON.parse(JSON.stringify(nodes)),
        connections: JSON.parse(JSON.stringify(canvas?.connections || [])),
        selectedId,
        selectedIds: selectedIds.slice(),
        selectedImage: {...selectedImage}
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
        nodes = restoredNodes.nodes; if(canvas) canvas.nodes = nodes;
        if(restoredNodes.dropped > 0) toast(tr('smart.toastDanglingMediaDropped'));
        if(canvas) canvas.connections = snap.connections;
        selectedId = snap.selectedId;
        selectedIds = snap.selectedIds;
        selectedImage = snap.selectedImage;
        activeComposerSubject = null;
        lastComposerNodeId = '';
        render();
        scheduleSave();
        undoSuppressed = false;
        toast(tr('smart.toastUndone'));
        syncUndoRedoButtons();
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
        nodes.forEach(n => currentImages.push(...(n.images || [])));
        const restoredImageIds = new Set();
        restoredNodes.nodes.forEach(n => (n.images || []).forEach(img => {
            const id = img?.asset_id || '';
            if(id) restoredImageIds.add(id);
        }));
        const lostImages = currentImages.filter(img => {
            const id = img?.asset_id || '';
            return id && !restoredImageIds.has(id);
        });
        nodes = restoredNodes.nodes; if(canvas) canvas.nodes = nodes;
        if(restoredNodes.dropped > 0) toast(tr('smart.toastDanglingMediaDropped'));
        if(canvas) canvas.connections = snap.connections;
        selectedId = snap.selectedId;
        selectedIds = snap.selectedIds;
        selectedImage = snap.selectedImage;
        activeComposerSubject = null;
        lastComposerNodeId = '';
        render();
        scheduleSave();
        // #146: 重做删除后，对丢失的图片重新调度归档。
        if(lostImages.length) scheduleMediaArchive(lostImages);
        undoSuppressed = false;
        toast(tr('smart.toastRedone'));
        syncUndoRedoButtons();
        return;
    }
    toast(tr('smart.toastNoRedo'));
    syncUndoRedoButtons();
}

// #62 B-5(L2): 应用 undo/redo 快照前，剔除项目媒体已不存在（已硬删）的 image 引用。
// 资源库未加载（空）时无法判定，跳过过滤——由保存 400 自愈（C-4）兜底。
function filterDanglingProjectMediaImages(snapshotNodes){
    const libraryItems = projectMediaLibrary?.items || [];
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
// projectMediaLibrary，悬空过滤会把存活媒体误判为悬空而剥离（复制→撤回丢光图片事故）。
async function refreshProjectMediaLibraryItems(){
    const projectId = canvas?.project_id || canvas?.project || sourceProjectId || canvasId || '';
    if(!projectId) return false;
    try {
        const res = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/media`);
        if(!res.ok) return false;
        const data = await res.json();
        if(data && Array.isArray(data.items)) projectMediaLibrary = {items: data.items};
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
    if(removed) syncUndoRedoButtons();
    return removed;
}

// #73: 清理与删除身份匹配的 projectOriginalDelete undo 令牌（彻底删除后恢复目标已不存在）。
function removeProjectOriginalUndoTokens(assetId, deletionId, projectId){
    if(!assetId) return;
    const match = token => token && token.assetId === assetId
        && (!deletionId || token.deletionId === deletionId)
        && (token.projectId || projectId) === projectId;
    for(let index = undoStack.length - 1; index >= 0; index--){
        if(match(undoStack[index]?.projectOriginalDelete)) undoStack.splice(index, 1);
    }
    for(let index = redoStack.length - 1; index >= 0; index--){
        if(match(redoStack[index]?.projectOriginalDelete)) redoStack.splice(index, 1);
    }
    syncUndoRedoButtons();
}
