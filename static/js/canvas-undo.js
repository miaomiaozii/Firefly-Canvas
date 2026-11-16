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
const pendingUndoRemoteBaselines = new WeakMap(); // runtime-only receipt deduplication
let redoPushing = false;
const smartUndoButton = document.getElementById('smartUndoButton');
const smartRedoButton = document.getElementById('smartRedoButton');

function syncUndoRedoButtons(){
    if(smartUndoButton) smartUndoButton.disabled = !undoStack.length;
    if(smartRedoButton) smartRedoButton.disabled = !redoStack.length;
}

function canvasUndoDeltaTools(){
    const core = window.canvasCore;
    return typeof core?.smartCanvasActionDelta === 'function' && typeof core?.applySmartCanvasDelta === 'function'
        ? {build:core.smartCanvasActionDelta, apply:core.applySmartCanvasDelta} : null;
}

// #392: 封口只针对本版本尚未封口的动作。调用方须在保存/远端接收前调用，
// 否则之后到达的远端实体会被误当成本次动作；旧持久快照绝不猜测或迁移。
// capture 尚未 commit 时，远端应用后还须传 {afterRemote:true}：将已记录的本地
// 前缀反向应用到新 live 基线上，再开放同一 capture 的异步后续（不把远端算进去）。
function finalizePendingCanvasUndo({afterRemote=false, materializedMedia=null}={}){
    if(materializedMedia){
        const rewrite = window.canvasCore?.reconcileCanvasDeltaMediaReferences;
        if(typeof rewrite !== 'function') return;
        for(const snapshot of [...undoStack, ...redoStack, pendingUndoSnapshot]){
            if(snapshot?.actionDeltaVersion === 1 && snapshot.actionDelta){
                snapshot.actionDelta = rewrite(snapshot.actionDelta, materializedMedia.frozen, materializedMedia.imported);
            }
        }
        return;
    }
    const tools = canvasUndoDeltaTools();
    if(!tools || !canvas) return;
    const after = canvasUndoContents(snapshotForUndo());
    if(afterRemote && pendingUndoSnapshot?.actionDeltaVersion === 1
        && (!pendingUndoRemoteBaselines.has(pendingUndoSnapshot)
            || tools.build(pendingUndoRemoteBaselines.get(pendingUndoSnapshot), after).changes.length)){
        try {
            const rebased = tools.apply(after, pendingUndoSnapshot.actionDelta || {version:1, changes:[]}, {
                direction:'backward', mergeValue:window.FireflyProjectCanvasMutations?.mergeCanvasValue
            });
            if(rebased.conflicts?.length){ toast(rebased.conflicts[0]?.message || tr('smart.toastSaveFail')); return; }
            Object.assign(pendingUndoSnapshot, canvasUndoContents(rebased.canvas));
            delete pendingUndoSnapshot.actionDelta;
            pendingUndoRemoteBaselines.set(pendingUndoSnapshot, after);
        } catch(err){ toast(err?.message || tr('smart.toastSaveFail')); return; }
    }
    let changed = false;
    const seal = snap => {
        if(snap?.actionDeltaVersion !== 1 || (snap.actionDelta && snap !== pendingUndoSnapshot) || snap.projectOriginalDelete) return;
        const delta = tools.build(canvasUndoContents(snap), after);
        if(snap.actionDelta){
            // Saving seals a prefix, not the open capture. Re-measure its local
            // fields, but do not absorb identities first received after sealing.
            // Explicit afterRemote rebasing opens a fresh prefix for new entities.
            const createdPaths = new Set(snap.actionDelta.changes.filter(change =>
                change.path.length === 2 && !change.beforePresent).map(change => JSON.stringify(change.path)));
            delta.changes = delta.changes.filter(change => change.path.length !== 2 || change.beforePresent
                || !['nodes', 'connections'].includes(change.path[0]) || createdPaths.has(JSON.stringify(change.path)));
        }
        // thumb-detach 的 commit 发生在真正修改之前；尚无变化的 capture 必须
        // 留给后续 scheduleSave/接收前封口，不能永久锁死成空动作。
        if(snap === pendingUndoSnapshot && !delta.changes.length && !snap.actionDelta) return;
        if(JSON.stringify(snap.actionDelta) === JSON.stringify(delta)) return;
        snap.actionDelta = delta;
        if(delta.changes.length && undoStack.includes(snap) && !redoPushing) redoStack.length = 0;
        changed = true;
    };
    undoStack.forEach(seal);
    if(!afterRemote) seal(pendingUndoSnapshot);
    for(let index = undoStack.length - 1; index >= 0; index--){
        const snap = undoStack[index];
        if(snap.actionDeltaVersion === 1 && snap.actionDelta && !snap.actionDelta.changes.length && !snap.projectOriginalDelete){
            undoStack.splice(index, 1);
            changed = true;
        }
    }
    if(undoStack.length > UNDO_LIMIT){ undoStack.splice(0, undoStack.length - UNDO_LIMIT); changed = true; }
    if(changed){ syncUndoRedoButtons(); scheduleUndoStackPersist(); }
}

function capturePendingUndo(){
    finalizePendingCanvasUndo();
    pendingUndoSnapshot = snapshotForUndo();
}

function commitPendingUndo(){
    if(pendingUndoSnapshot){
        finalizePendingCanvasUndo();
        if(!pendingUndoSnapshot.actionDelta || pendingUndoSnapshot.actionDelta.changes.length) undoStack.push(pendingUndoSnapshot);
        if(undoStack.length > UNDO_LIMIT && (pendingUndoSnapshot.actionDelta || pendingUndoSnapshot.actionDeltaVersion !== 1)) undoStack.shift();
        if(pendingUndoSnapshot.actionDelta?.changes.length && !redoPushing) redoStack.length = 0;
        pendingUndoSnapshot = null;
        syncUndoRedoButtons();
        scheduleUndoStackPersist();
    }
}

function discardPendingUndo(){ pendingUndoSnapshot = null; }

function snapshotForUndo(){
    return {
        ...(canvasUndoDeltaTools() ? {actionDeltaVersion:1} : {}),
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
    finalizePendingCanvasUndo();
    const snapshot = snapshotForUndo();
    if(extra && typeof extra === 'object') Object.assign(snapshot, extra);
    undoStack.push(snapshot);
    // An unsealed new-format candidate is not a history step yet. Let it use a
    // temporary slot until finalization can discard a no-op before FIFO eviction.
    if(undoStack.length > UNDO_LIMIT && (snapshot.actionDelta || snapshot.actionDeltaVersion !== 1 || snapshot.projectOriginalDelete)) undoStack.shift();
    // 新动作等确有差量再使 redo 失效；取消/同值的空动作不能毁掉旧分支。
    if(!redoPushing && redoStack.length && (snapshot.projectOriginalDelete
        || snapshot.actionDeltaVersion !== 1 || snapshot.actionDelta?.changes.length)) redoStack.length = 0;
    syncUndoRedoButtons();
    scheduleUndoStackPersist();
}

// 新差量与旧快照共用恢复副作用，保留 #62/#146 的顺序及选区/批注语义。
function applyCanvasUndoSnapshot(snap, restoredNodes){
    canvasState.nodes = restoredNodes.nodes; if(canvas) canvas.nodes = canvasState.nodes;
    if(restoredNodes.dropped > 0) toast(tr('smart.toastDanglingMediaDropped'));
    if(canvas) canvas.connections = snap.connections;
    canvasState.selectedId = snap.selectedId;
    canvasState.selectedIds = snap.selectedIds;
    canvasState.selectedImage = snap.selectedImage;
    canvasState.annotations = (snap.annotations || []).slice();
    canvasState.activeComposerSubject = null;
    canvasState.lastComposerNodeId = '';
    render();
    scheduleSave();
}

// 差量恢复成功后才移动栈。歧义/异常留在原栈，允许用户处理后重试；
// redo 复用同一 actionDelta，而不是把恢复过程再算成一个新动作。
async function restoreCanvasUndoDelta(snap, direction){
    const tools = canvasUndoDeltaTools();
    const apply = () => {
        try {
            const shadow = window.canvasCore?.smartCanvasServerShadow;
            const remoteCanvas = shadow && String(shadow.id || shadow.project_id || '') === String(canvas?.id || canvas?.project_id || '') ? shadow : null;
            const result = tools.apply(canvasUndoContents(snapshotForUndo()), snap.actionDelta, {
                direction, remoteCanvas,
                mergeValue:window.FireflyProjectCanvasMutations?.mergeCanvasValue
            });
            if(!result.conflicts?.length) return result;
            toast(result.conflicts[0]?.message || tr('smart.toastSaveFail'));
        } catch(err){ toast(err?.message || tr('smart.toastSaveFail')); }
        return null;
    };
    let result = apply();
    if(!result) return;
    let restoredNodes = filterDanglingProjectMediaImages(result.canvas.nodes);
    if(restoredNodes.dropped > 0){
        await refreshProjectMediaLibraryItems();
        // A local action (or another undo) may have changed the stack during IO.
        // Leave both entries untouched rather than popping an unrelated action.
        const stack = direction === 'backward' ? undoStack : redoStack;
        if(stack[stack.length - 1] !== snap) return;
        // 拉库期间也可能收到远端实体；基于最新 live 状态重新应用同一操作范围。
        result = apply();
        if(!result) return;
        restoredNodes = filterDanglingProjectMediaImages(result.canvas.nodes);
    }
    const reverse = {...snapshotForUndo(), actionDeltaVersion:1, actionDelta:snap.actionDelta};
    if(direction === 'backward'){
        undoStack.pop();
        redoStack.push(reverse);
        if(redoStack.length > UNDO_LIMIT) redoStack.shift();
    } else {
        redoStack.pop();
        redoPushing = true;
        try { pushUndo(reverse); } finally { redoPushing = false; }
    }
    const currentImages = canvasState.nodes.flatMap(n => n.images || []);
    const restoredAssets = new Set(restoredNodes.nodes.flatMap(n => (n.images || []).map(img => img?.asset_id)).filter(Boolean));
    const lostImages = currentImages.filter(img => img?.asset_id && !restoredAssets.has(img.asset_id));
    undoSuppressed = true;
    try {
        applyCanvasUndoSnapshot({...snap, ...result.canvas}, restoredNodes);
        if(direction === 'forward' && lostImages.length) scheduleMediaArchive(lostImages);
    } finally { undoSuppressed = false; }
    toast(tr(direction === 'backward' ? 'smart.toastUndone' : 'smart.toastRedone'));
    syncUndoRedoButtons();
    scheduleUndoStackPersist();
}

async function performUndo(){
    finalizePendingCanvasUndo();
    // #146: 撤销时取消所有待归档，避免撤销恢复的节点引用已归档资产 → 404。
    cancelPendingMediaArchives();
    while(undoStack.length){
        const next = undoStack[undoStack.length - 1];
        if(!next.projectOriginalDelete && next.actionDeltaVersion === 1 && next.actionDelta && canvasUndoDeltaTools()){
            if(!next.actionDelta.changes.length){ undoStack.pop(); scheduleUndoStackPersist(); continue; }
            await restoreCanvasUndoDelta(next, 'backward');
            return;
        }
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
        const reverse = snapshotForUndo();
        delete reverse.actionDeltaVersion; // 旧条目的反向条目也保持旧快照语义，不隐式迁移。
        redoStack.push(reverse);
        if(redoStack.length > UNDO_LIMIT) redoStack.shift();
        undoSuppressed = true;
        // #62 B-5(L2): 兜底过滤——快照里引用的媒体若已被硬删（悬空 asset_id），
        // 服务端 upsert 校验会 400「项目媒体不存在」，画布会进入持续保存失败死锁。
        let restoredNodes = filterDanglingProjectMediaImages(snap.nodes);
        if(restoredNodes.dropped > 0 && await refreshProjectMediaLibraryItems()){
            // 内存库可能陈旧（本会话新生产的媒体尚未登记）→ 重新拉取后再验，避免误删存活图片
            restoredNodes = filterDanglingProjectMediaImages(snap.nodes);
        }
        applyCanvasUndoSnapshot(snap, restoredNodes);
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
    finalizePendingCanvasUndo();
    // #146: 重做时同样取消待归档（重做删除会让节点再次消失，归档应重新调度）。
    cancelPendingMediaArchives();
    while(redoStack.length){
        const next = redoStack[redoStack.length - 1];
        if(!next.projectOriginalDelete && next.actionDeltaVersion === 1 && next.actionDelta && canvasUndoDeltaTools()){
            await restoreCanvasUndoDelta(next, 'forward');
            return;
        }
        const snap = redoStack.pop();
        if(snap.projectOriginalDelete) continue;
        const reverse = snapshotForUndo();
        delete reverse.actionDeltaVersion;
        undoStack.push(reverse);
        if(undoStack.length > UNDO_LIMIT) undoStack.shift();
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
        applyCanvasUndoSnapshot(snap, restoredNodes);
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
function filterDanglingProjectMediaImages(snapshotNodes, {libraryKnownLoaded=false}={}){
    const libraryItems = canvasState.projectMediaLibrary?.items || [];
    if(!Array.isArray(snapshotNodes) || (!libraryItems.length && !libraryKnownLoaded)) return {nodes: snapshotNodes, dropped: 0};
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
        if(!Array.isArray(data?.items)) return false;
        canvasState.projectMediaLibrary = {items: data.items};
        return true;
    } catch { return false; }
}

// #62 B-5(L1): 媒体硬删成功后，移除仍引用它的 undo/redo 快照（返回移除数量）。
// 快照里的悬空引用会让下一次保存 400「项目媒体不存在」，画布进入持续保存失败死锁。
function stripDeletedMediaFromUndoStacks(assetId){
    if(!assetId) return 0;
    let removed = 0;
    const referencesAsset = value => value && typeof value === 'object'
        && (String(value.asset_id || '') === assetId || Object.values(value).some(referencesAsset));
    const stripFrom = stack => {
        for(let index = stack.length - 1; index >= 0; index--){
            const snap = stack[index];
            if(!snap || !Array.isArray(snap.nodes)) continue;
            const hit = snap.nodes.some(node => Array.isArray(node?.images) && node.images.some(image => String(image?.asset_id || '') === assetId))
                || (snap.actionDelta?.changes || []).some(change => referencesAsset(change.before) || referencesAsset(change.after)
                    || (change.path?.[change.path.length - 1] === 'asset_id'
                        && [change.before, change.after].some(value => String(value || '') === assetId)));
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
        finalizePendingCanvasUndo();
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
        // #348: 后台持久化（防抖触发）；失败时本次会话内撤销仍可用，下次变更会再试。
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
        // #348: 恢复失败回落「刷新失忆」的旧行为，不影响画布可用（原注释保留）。
        console.warn('[canvas-undo] 恢复持久化 undo 栈失败', err && err.message);
    }
}
