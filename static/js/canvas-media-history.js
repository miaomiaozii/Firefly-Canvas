// 画布媒体/历史模块（从 smart-canvas.js 抽离，#161 P2d）。
// 媒体归档、回收站面板、历史面板、标记面板、未使用媒体面板、媒体 URL/存储。
// 引用主脚本全局（canvasState.nodes/canvas/canvasState.selectedId/toast/tr/render/scheduleSave/pushUndo/
// smartPendingTasks/smartLoopContext 等），本文件只含函数定义和状态声明，
// 先于 smart-canvas.js 加载，所有调用都发生在主脚本全局初始化之后，安全。
// 注意：undo 栈操作函数（stripDeletedMediaFromUndoStacks/removeProjectOriginalUndoTokens）
// 属 canvas-undo.js，本批不碰；拖拽事件接线属核心状态机，留主脚本。

// 这些状态本来属于本模块，#173 核心下沉第 2 步从 smart-canvas.js 搬回此处。
const smartHistoryModal = document.getElementById('smartHistoryModal');

const smartHistoryList = document.getElementById('smartHistoryList');

const smartHistoryCollapse = document.getElementById('smartHistoryCollapse');

const smartHistoryPurgeTrash = document.getElementById('smartHistoryPurgeTrash');

const smartHistoryVirtualState = {
    start:0,
    end:0,
    count:0,
    stride:0,
    gap:0,
    signature:'',
    frame:0,
    force:false,
    focusCard:null,
    focusElement:null
};
const ARCHIVE_DELAY_MS = 2500; // 延迟 2.5s（> 450ms 保存防抖 + 网络往返）
let pendingMediaArchives = []; // [{ images: [...], timer: number }]

function scheduleMediaArchive(images){
    if(!images.length) return;
    const timer = setTimeout(() => {
        pendingMediaArchives = pendingMediaArchives.filter(entry => entry.timer !== timer);
        void cascadeArchiveDeletedMedia(images);
    }, ARCHIVE_DELAY_MS);
    pendingMediaArchives.push({ images, timer });
}

function cancelPendingMediaArchives(){
    pendingMediaArchives.forEach(entry => clearTimeout(entry.timer));
    pendingMediaArchives = [];
}

function smartOriginalMediaUrl(itemOrUrl){
    const raw = typeof itemOrUrl === 'string' ? itemOrUrl : (itemOrUrl?.url || '');
    const text = String(raw || '');
    if(!text) return '';
    try {
        const parsed = new URL(text, window.location.origin);
        if(parsed.pathname === '/api/media-preview'){
            const original = parsed.searchParams.get('url') || '';
            return original || text;
        }
    } catch(e) {}
    return text;
}

function smartMediaPreviewUrl(itemOrUrl, size=512){
    const width = Math.max(64, Math.min(2048, Math.round(Number(size) || 512)));
    if(typeof itemOrUrl === 'object' && itemOrUrl?.preview_url){
        const preview = String(itemOrUrl.preview_url || '');
        if(preview) return `${preview}${preview.includes('?') ? '&' : '?'}w=${width}`;
    }
    const raw = smartOriginalMediaUrl(itemOrUrl);
    try {
        const parsed = new URL(raw, window.location.origin);
        if(/^\/api\/project-library\/[^/]+\/media\/[^/]+$/.test(parsed.pathname)) return `${parsed.pathname}/preview?w=${width}`;
    } catch(e) {}
    const displayItem = typeof itemOrUrl === 'object' && itemOrUrl ? {...itemOrUrl, url:raw} : raw;
    const displayUrl = canvasCore.displayMediaUrl(displayItem);
    if(!raw || raw.startsWith('data:') || raw.startsWith('blob:')) return displayUrl;
    if(!raw.startsWith('/output/') && !raw.startsWith('/assets/')) return displayUrl;
    if(!/\.(png|jpe?g|webp|gif|bmp|avif|tiff?|mp4|webm|mov|m4v|avi|mkv)(\?|#|$)/i.test(raw)) return displayUrl;
    return `/api/media-preview?w=${width}&url=${encodeURIComponent(raw)}`;
}

function mediaItemForStorage(item){
    if(!item || typeof item !== 'object') return item;
    const clean = {...item};
    delete clean.cloudUrl;
    delete clean.uploadedUrl;
    delete clean.originalRemoteUrl;
    delete clean.original_url;
    delete clean.tempCloudUrl;
    delete clean._inlineVideoActive;
    delete clean._naturalSizeLoading;
    delete clean.layout_w;
    delete clean.layout_h;
    if(clean.asset_id){
        ['url','src','path','output','output_url','outputUrl','uri','href','file','local_path','localPath','remote_url','remoteUrl','preview_url','previewUrl'].forEach(key => delete clean[key]);
    }
    return clean;
}

function mediaReferencesForStorage(value){
    if(Array.isArray(value)) return value.map(mediaReferencesForStorage);
    if(!value || typeof value !== 'object') return value;
    if(value.asset_id) return mediaItemForStorage(value);
    const clean = {};
    Object.entries(value).forEach(([key, child]) => {
        clean[key] = mediaReferencesForStorage(child);
    });
    return clean;
}

function projectMediaUrl(assetId){
    const projectId = canvas?.project_id || canvas?.project || canvasCore.sourceProjectId || '';
    return `/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(assetId)}`;
}

function projectMediaPreviewUrl(assetId){
    return `${projectMediaUrl(assetId)}/preview`;
}

function hydrateProjectMediaReferences(value){
    if(Array.isArray(value)) return value.map(hydrateProjectMediaReferences);
    if(!value || typeof value !== 'object') return value;
    const hydrated = {...value};
    Object.keys(hydrated).forEach(key => { hydrated[key] = hydrateProjectMediaReferences(hydrated[key]); });
    if(hydrated.asset_id && !hydrated.url){
        hydrated.url = projectMediaUrl(hydrated.asset_id);
        hydrated.preview_url = '';
        if(canvasCore.mediaKindForItem(hydrated) === 'image') hydrated.preview_url = projectMediaPreviewUrl(hydrated.asset_id);
    }
    return hydrated;
}

function mediaReferenceSource(value){
    if(typeof value === 'string') return value;
    if(!value || typeof value !== 'object') return '';
    return ['url','src','output','output_url','outputUrl','path','uri']
        .map(key => value[key])
        .find(item => typeof item === 'string' && item.trim()) || '';
}

function reconcileMaterializedMediaReferences(current, before, materialized){
    if(Array.isArray(materialized)){
        return materialized.map((item, index) => reconcileMaterializedMediaReferences(current?.[index], before?.[index], item));
    }
    if(!materialized || typeof materialized !== 'object') return current;
    if(materialized.asset_id){
        const beforeAssetId = String(before?.asset_id || '');
        const currentAssetId = String(current?.asset_id || '');
        const unchanged = beforeAssetId
            ? currentAssetId === beforeAssetId
            : typeof before === 'string'
                ? current === before
                : Boolean(mediaReferenceSource(before)) && mediaReferenceSource(current) === mediaReferenceSource(before);
        if(unchanged){
            const hydrated = hydrateProjectMediaReferences(materialized);
            return current && typeof current === 'object' && !Array.isArray(current)
                ? {...current, ...hydrated}
                : hydrated;
        }
        return current;
    }
    const result = current && typeof current === 'object' && !Array.isArray(current) ? {...current} : {};
    Object.entries(materialized).forEach(([key, value]) => {
        result[key] = reconcileMaterializedMediaReferences(current?.[key], before?.[key], value);
    });
    return result;
}

function syncLiveProjectMediaReferences(storageCanvas, materializedCanvas){
    if(!canvas || !materializedCanvas) return;
    const beforeNodes = new Map((storageCanvas?.nodes || []).map(node => [String(node?.id || ''), node]));
    const savedNodes = new Map((materializedCanvas?.nodes || []).map(node => [String(node?.id || ''), node]));
    canvasState.nodes.forEach(node => {
        const id = String(node?.id || '');
        const savedNode = savedNodes.get(id);
        if(!savedNode) return;
        const merged = reconcileMaterializedMediaReferences(node, beforeNodes.get(id), savedNode);
        Object.assign(node, merged);
    });
    canvas.nodes = canvasState.nodes;
    const beforeLogs = new Map((storageCanvas?.history || []).map(log => [String(log?.id || ''), log]));
    const savedLogs = new Map((materializedCanvas?.history || []).map(log => [String(log?.id || ''), log]));
    canvas.history = (canvas.history || []).map(log => {
        const id = String(log?.id || '');
        const savedLog = savedLogs.get(id);
        return savedLog
            ? reconcileMaterializedMediaReferences(log, beforeLogs.get(id), savedLog)
            : log;
    });
}

function isHistoryGroupNode(node){
    return Boolean(isSmartImageNode(node) && (node.isHistoryGroup || node.historyFor));
}

function recoverStuckLoopOutputsFromHistory(){
    const used = usedCanvasOutputUrls();
    let changed = false;
    const slots = (canvasState.nodes || [])
        .filter(node => node && isSmartImageNode(node) && !isHistoryGroupNode(node))
        .filter(node => (node.loopSourceId || node.loopRootId || Number.isFinite(Number(node.loopSlotIndex))) && !smartNodeHasDisplayResult(node))
        .filter(node => (node.pending || node.running || node.queued) && !smartPendingTasks(node).length)
        .sort((a, b) => (Number(a.loopSlotIndex || 0) - Number(b.loopSlotIndex || 0)) || (Number(a.y || 0) - Number(b.y || 0)));
    slots.forEach(slot => {
        const sourceId = slot.loopRootId || slot.sourceNodeId || '';
        const output = successfulRecentGenerationLogOutputs(sourceId).find(item => !used.has(item.url));
        if(!output) return;
        const kind = canvasCore.mediaKindForUrls([output.url], 'image');
        const ext = kind === 'video' ? 'mp4' : kind === 'audio' ? 'mp3' : kind === 'text' ? 'txt' : 'png';
        slot.images = [canvasCore.stripImageGenerationMeta({url:output.url, name:`recovered-${Number(slot.loopSlotIndex || 0) + 1}.${ext}`, kind, generatedResult:true})];
        canvasCore.markSmartNodeComplete(slot);
        if(kind) slot.outputKind = kind;
        slot.title = slot.title || 'Image';
        slot.scale = canvasCore.mediaNodeDefaultScale(slot);
        delete slot.w;
        delete slot.h;
        used.add(output.url);
        changed = true;
        canvasCore.clearSourceBusyStateIfDownstreamDone(canvasState.nodes.find(n => n.id === sourceId));
    });
    return changed;
}

function smartHistoryTaskId(log){
    return String(log?.task_id || log?.request?.task_id || log?.request?.taskId || '').trim();
}

function mergeSmartHistoryRecordLists(localLogs, remoteLogs){
    const merged = new Map();
    [...(localLogs || []), ...(remoteLogs || [])].forEach(log => {
        if(!log || typeof log !== 'object') return;
        const taskId = smartHistoryTaskId(log);
        const recordId = String(log.id || '');
        const key = taskId ? `task:${taskId}` : `id:${recordId}`;
        if(!taskId && !recordId) return;
        const current = merged.get(key);
        if(!current){
            merged.set(key, hydrateProjectMediaReferences(log));
            return;
        }
        const currentTerminal = ['succeeded', 'failed', 'cancelled'].includes(String(current.status || ''));
        const nextTerminal = ['succeeded', 'failed', 'cancelled'].includes(String(log.status || ''));
        if(currentTerminal && !nextTerminal) return;
        if(nextTerminal || !currentTerminal){
            const hydrated = hydrateProjectMediaReferences(log);
            if(nextTerminal && !hydrated?.progress && current?.progress) hydrated.progress = {...current.progress};
            merged.set(key, hydrated);
        }
    });
    return sortSmartHistoryLogs([...merged.values()]).slice(0, 5000);
}

function formatTrashTime(ms){
    const value = Number(ms || 0);
    if(!value) return '';
    const d = new Date(value);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function trashDaysLeft(expiresAt){
    const left = Number(expiresAt || 0) - Date.now();
    if(left <= 0) return 0;
    return Math.ceil(left / 86400000);
}

function renderSmartTrashItems(items){
    if(!smartTrashList) return;
    if(!Array.isArray(items) || !items.length){
        smartTrashList.innerHTML = `<div class="smart-trash-empty">${escapeHtml(tr('smart.trashEmpty'))}</div>`;
        if(smartTrashPurgeBtn) smartTrashPurgeBtn.disabled = true;
        return;
    }
    if(smartTrashPurgeBtn) smartTrashPurgeBtn.disabled = false;
    const projectId = smartProjectId() || '';
    smartTrashList.innerHTML = items.map(item => {
        const assetId = String(item?.asset_id || '');
        const deletionId = String(item?.deletion_id || '');
        const name = String(item?.name || item?.filename || assetId);
        const isVideo = String(item?.kind || '') === 'video' || String(item?.mime_type || '').startsWith('video/');
        const days = trashDaysLeft(item?.expires_at);
        // #86/#208：回收区条目带缩略图（显式请求预览图，无预览回退原文件），点缩略图可放大预览。
        const thumbUrl = projectId && assetId ? `/api/project-library/${encodeURIComponent(projectId)}/trash/media/${encodeURIComponent(assetId)}/preview?view=preview` : '';
        return `<div class="smart-trash-item" data-trash-asset="${escapeHtml(assetId)}" data-trash-deletion="${escapeHtml(deletionId)}">
            <div class="smart-trash-thumb-wrap" data-trash-preview="${escapeHtml(assetId)}" title="${escapeHtml(tr('smart.trashPreview'))}">
                ${thumbUrl ? `<img class="smart-trash-thumb" loading="lazy" src="${escapeAttr(thumbUrl)}" alt="${escapeHtml(name)}">` : ''}
                <i data-lucide="${isVideo ? 'video' : 'image'}" class="smart-trash-icon"></i>
            </div>
            <div class="smart-trash-meta">
                <div class="smart-trash-name" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
                <div class="smart-trash-sub">${escapeHtml(formatTrashTime(item?.deleted_at))}${days > 0 ? ` · ${escapeHtml(tr('smart.trashExpiresIn').replace('{n}', String(days)))}` : ''}</div>
            </div>
            <button class="asset-mini-btn" type="button" data-trash-restore title="${escapeHtml(tr('smart.trashRestore'))}"><i data-lucide="rotate-ccw"></i></button>
            <button class="asset-mini-btn danger" type="button" data-trash-forever title="${escapeHtml(tr('smart.trashDeleteForever'))}"><i data-lucide="trash-2"></i></button>
        </div>`;
    }).join('');
    canvasCore.refreshIcons();
    smartTrashList.querySelectorAll('[data-trash-restore]').forEach(btn => {
        btn.onclick = () => void smartTrashRestoreItem(
            btn.closest('[data-trash-asset]')?.dataset?.trashAsset || '',
            btn.closest('[data-trash-asset]')?.dataset?.trashDeletion || ''
        );
    });
    smartTrashList.querySelectorAll('[data-trash-forever]').forEach(btn => {
        btn.onclick = () => void smartTrashPurgeItem(btn.closest('[data-trash-asset]')?.dataset?.trashAsset || '');
    });
    // #86：缩略图加载失败回退到图标；点缩略图打开大图预览（滚轮缩放 + 拖拽 + 恢复）。
    const byAsset = new Map(items.map(item => [String(item?.asset_id || ''), item]));
    smartTrashList.querySelectorAll('.smart-trash-thumb').forEach(img => {
        img.addEventListener('error', () => img.closest('.smart-trash-thumb-wrap')?.classList.add('no-thumb'), {once:true});
    });
    smartTrashList.querySelectorAll('[data-trash-preview]').forEach(el => {
        el.onclick = () => openTrashMediaPreview(byAsset.get(el.dataset.trashPreview) || {});
    });
}
// #207/#87：回收区预览收口到「生成详情」统一面板（与画布缩略图/生图历史抽屉同一组件，
// 以后改详情 UI 只改一处）：图片请求生成原件（#208 view=original，原件缺失时服务端回退预览图），
// 视频走新标签页（openSmartHistoryDetail 内部处理）。动作行追加「恢复」按钮
//（对应该自建面板原有的恢复入口），面板关闭时由 closeSmartHistoryLightbox 统一清理。
function openTrashMediaPreview(item){
    const projectId = smartProjectId();
    const assetId = String(item?.asset_id || '');
    if(!projectId || !assetId) return;
    const previewUrl = `/api/project-library/${encodeURIComponent(projectId)}/trash/media/${encodeURIComponent(assetId)}/preview?view=original`;
    const isVideo = String(item?.kind || '').includes('video') || String(item?.mime_type || '').startsWith('video/');
    openSmartHistoryDetail({
        url: previewUrl,
        kind: isVideo ? 'video' : 'image',
        historyId: '',
        outputIndex: 0
    });
    if(isVideo) return; // 视频走新标签页打开，无需附恢复按钮
    const box = document.getElementById('smartHistoryLightbox');
    const actions = box?.querySelector('.smart-log-detail-actions');
    if(!actions) return;
    const restoreBtn = document.createElement('button');
    restoreBtn.type = 'button';
    restoreBtn.className = 'smart-trash-detail-restore';
    restoreBtn.title = tr('smart.trashRestore');
    restoreBtn.setAttribute('aria-label', tr('smart.trashRestore'));
    restoreBtn.innerHTML = `<i data-lucide="rotate-ccw"></i><span>${escapeHtml(tr('smart.trashRestore'))}</span>`;
    restoreBtn.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        void smartTrashRestoreItem(assetId, String(item?.deletion_id || ''));
        closeSmartHistoryLightbox();
    };
    actions.prepend(restoreBtn);
    canvasCore.refreshIcons(box);
}

function closeSmartTrashPanel(){
    if(!smartTrashBackdrop) return;
    smartTrashBackdrop.classList.remove('open');
    smartTrashBackdrop.hidden = true;
}
// 单个恢复：有 deletion_id 走服务端 /original/restore（生成输出 occurrence 恢复，还原画布引用）；
// 无 deletion_id（如生成孤儿回收，#79）走 /trash/media/restore（通用恢复，只移回资源库）。

function closeSmartUnusedPanel(){
    if(!smartUnusedBackdrop) return;
    smartUnusedBackdrop.classList.remove('open');
    smartUnusedBackdrop.hidden = true;
}

// 一键清理：逐个走 ADR 0015 删除流（用户主动删除 = 硬删，不进回收区）

async function cascadeArchiveDeletedMedia(images){
    const assetIds = new Set();
    (images || []).forEach(img => { const id = img?.asset_id || ''; if(id) assetIds.add(id); });
    if(!assetIds.size) return;
    const stillReferenced = new Set();
    canvasState.nodes.forEach(node => (node.images || []).forEach(img => { const id = img?.asset_id || ''; if(id) stillReferenced.add(id); }));
    // #88：生成历史（canvas.history）引用的原图不在节点集合里——仅删画布图时也要豁免，
    // 否则会把历史里还挂着的原图归档进回收区，历史缩略图 404。
    (Array.isArray(canvas?.history) ? canvas.history : []).forEach(log => {
        (Array.isArray(log?.outputs) ? log.outputs : []).forEach(output => {
            const id = String(output?.asset_id || '');
            if(id) stillReferenced.add(id);
        });
    });
    const toArchive = [...assetIds].filter(id => !stillReferenced.has(id));
    if(!toArchive.length) return;
    await Promise.all(toArchive.map(async assetId => {
        try {
            const result = await archiveProjectMediaAsset(assetId, 'node-delete');
            if(result.ok){
                removeProjectMediaFromCache(assetId);
                if(assetLibraryIsProject()) renderAssetLibrary();
            }
        } catch(err) { /* 服务端判定仍被引用（409）或其他失败：保留在库，不阻断删除 */ }
    }));
    refreshSmartProjectMediaTrashButton();
}

function getMarkedAssetIds(){
    if(!canvas) return [];
    if(!Array.isArray(canvas.markedAssetIds)) canvas.markedAssetIds = [];
    return canvas.markedAssetIds;
}

function updateSmartMarkedBadge(){
    const badge = document.getElementById('smartMarkedCount');
    if(!badge) return;
    const count = getMarkedAssetIds().length;
    badge.hidden = count === 0;
    badge.textContent = String(count);
}

function setMarkedImage(assetId, marked){
    if(!assetId) return;
    const ids = getMarkedAssetIds();
    const at = ids.indexOf(assetId);
    if(marked && at < 0) ids.push(assetId);
    if(!marked && at >= 0) ids.splice(at, 1);
    // #89 第四轮：节点级 flagged 才是持久载体——项目增量快照只抓 canvasState.nodes/edges/title/icon/logs，
    // canvas.markedAssetIds 顶层字段永不被保存；只有 upsert_node 携带的 images[].marked 能落盘。
    canvasState.nodes.forEach(node => (node.images || []).forEach(img => {
        if(img && String(img.asset_id || '') === assetId){
            if(marked) img.marked = true;
            else delete img.marked;
        }
    }));
    syncCuratedFromMarks(); // #53/#58：星标即“保留”信号，同步节点 curated 供收集/导出 API 读取
    canvasCore.render();
    canvasCore.scheduleSave();
    renderSmartMarkedPanel();
    syncMarkedImageButtons();
}
// #89 第四轮：画布加载/远端 rebase 后，从节点 images[].marked 重建星标索引。

function rebuildMarkedAssetIdsFromNodes(){
    if(!canvas) return;
    const found = new Set();
    canvasState.nodes.forEach(node => (node.images || []).forEach(img => {
        if(img && img.marked && img.asset_id) found.add(String(img.asset_id));
    }));
    canvas.markedAssetIds = [...found];
    syncCuratedFromMarks(); // #53/#58：加载/rebase 后保证 curated 与星标一致（历史画布可能缺 curated 字段）
}
// #53/#58：星标 → 节点 curated 同步。任一输出图被星标 ⇒ curated=true（/api/excel/collect-nodes
// 与 pi 收集工具据此筛选）；全部取消星标 ⇒ curated=false。只改有星标图的节点。

function syncMarkedImageButtons(){
    const ids = getMarkedAssetIds();
    document.querySelectorAll('[data-marked-asset]').forEach(el => {
        const marked = ids.includes(String(el.dataset.markedAsset || ''));
        el.classList.toggle('image-marked', marked);
        if(el.dataset.markedTitle) el.title = marked ? tr('smart.unmarkImage') : tr('smart.markImage');
    });
    if(window.lucide) lucide.createIcons();
}
// #89 第四轮：生成详情/生图历史的 output 项 → 画布节点 {nodeId,index}（优先 asset_id 匹配，回退 url）。

function smartHistoryDetailNodeImageRef(item){
    const assetId = String(item?.asset_id || '');
    const url = String(item?.url || '');
    let ref = null;
    let urlRef = null;
    for(const node of canvasState.nodes){
        (node.images || []).forEach((img, index) => {
            if(!img) return;
            if(assetId && String(img.asset_id || '') === assetId) ref = {nodeId:node.id, index};
            else if(urlRef === null && url && String(img.url || '') === url) urlRef = {nodeId:node.id, index};
        });
    }
    return ref || urlRef;
}
// #89 第四轮：预览大图里的星标（对当前预览的节点图）。

function toggleMarkedImage(node, imageIndex){
    const raw = node?.images?.[imageIndex];
    const item = canvasCore.imageForDisplay(raw) || raw;
    const assetId = String(raw?.asset_id || item?.asset_id || '');
    if(!assetId){ toast(tr('smart.markUnavailable')); return; }
    setMarkedImage(assetId, !getMarkedAssetIds().includes(assetId));
}
// asset_id → 可用文件（含 url）：先查生成历史输出，再查节点图片；文件已删的自动跳过。

function markedImageItems(){
    const found = new Map();
    (Array.isArray(canvas?.history) ? canvas.history : []).forEach(log => {
        (Array.isArray(log?.outputs) ? log.outputs : []).forEach(output => {
            const id = String(output?.asset_id || '');
            if(id && output?.url && !found.has(id)) found.set(id, output);
        });
    });
    canvasState.nodes.forEach(node => (node.images || []).forEach(img => {
        const id = String(img?.asset_id || '');
        if(id && img?.url && !found.has(id)) found.set(id, img);
    }));
    return getMarkedAssetIds().map(id => found.get(id)).filter(item => item?.url);
}
// #89 星标列表显示名：优先 name/alias，其次 url 里的文件名（generated-1.png 这类），最后才退回 asset_id。
// 裸 asset_id（32 位十六进制）当文件名展示既丑又无信息量。

function markedItemDisplayName(item){
    const name = String(item?.name || item?.alias || '').trim();
    if(name) return name;
    try{
        const path = String(item?.url || '').split('?')[0];
        const file = path.slice(path.lastIndexOf('/') + 1);
        if(file && !/^[0-9a-f]{24,}$/i.test(file)) return decodeURIComponent(file);
    }catch(e){}
    return String(item?.asset_id || '');
}

function renderSmartMarkedPanel(){
    const count = getMarkedAssetIds().length;
    const badge = document.getElementById('smartMarkedCount');
    if(badge){ badge.hidden = count === 0; badge.textContent = String(count); }
    const list = document.getElementById('smartMarkedList');
    if(!list) return;
    const items = markedImageItems();
    if(!items.length){
        list.innerHTML = `<div class="smart-marked-empty">${tr('smart.markedEmpty')}</div>`;
        if(window.lucide) lucide.createIcons();
        return;
    }
    // 列表内的图必然都是已标记状态：星标按钮直接渲染填充态（image-marked）。
    list.innerHTML = items.map((item, i) => `
        <div class="smart-marked-item" data-asset-id="${escapeAttr(item.asset_id)}">
            <img class="smart-marked-thumb" src="${escapeAttr(item.url)}" alt="">
            <span class="smart-marked-name" title="${escapeAttr(markedItemDisplayName(item))}">${escapeHtml(markedItemDisplayName(item))}</span>
            <button class="mini-x smart-marked-download" type="button" title="${escapeAttr(tr('smart.nodeDownload'))}" data-i="${i}"><i data-lucide="download"></i></button>
            <button class="mini-x smart-marked-unmark image-marked" type="button" title="${escapeHtml(tr('smart.unmarkImage'))}" data-asset-id="${escapeAttr(item.asset_id)}"><i data-lucide="star"></i></button>
        </div>`).join('');
    list.querySelectorAll('.smart-marked-download').forEach(btn => btn.addEventListener('click', e => {
        e.preventDefault(); e.stopPropagation();
        canvasCore.downloadPreviewFile(items[Number(btn.dataset.i)]);
    }));
    list.querySelectorAll('.smart-marked-unmark').forEach(btn => btn.addEventListener('click', e => {
        e.preventDefault(); e.stopPropagation();
        setMarkedImage(btn.dataset.assetId, false);
    }));
    if(window.lucide) lucide.createIcons();
}

function toggleSmartMarkedPanel(open){
    const panel = document.getElementById('smartMarkedPanel');
    if(!panel) return;
    const next = open ?? panel.hidden;
    panel.hidden = !next;
    if(next) renderSmartMarkedPanel();
}
// 把一组图片打包成 zip 下载（预览“下载全部”和分组小菜单“批量下载”共用）。

function smartHistoryRunMeta(log){
    const request = log?.request && typeof log.request === 'object' ? log.request : {};
    const params = log?.params && typeof log.params === 'object' ? log.params : {};
    const snapshot = cloneSmartSettings(log?.runSettings || log?.settings || {});
    if(!snapshot.provider_id && (request.provider_id || params.provider_id)) snapshot.provider_id = request.provider_id || params.provider_id;
    if(!snapshot.model && (request.model || params.model)) snapshot.model = request.model || params.model;
    if(!snapshot.quality && (request.quality || params.quality)) snapshot.quality = request.quality || params.quality;
    if(!snapshot.count && (request.n || params.n || params.count)) snapshot.count = request.n || params.n || params.count;
    if(!snapshot.size && (request.size || params.size)) snapshot.size = request.size || params.size;
    if(!snapshot.resolution && (request.resolution || params.resolution)) snapshot.resolution = request.resolution || params.resolution;
    if(!snapshot.videoAspect && (request.aspect_ratio || params.aspect_ratio)) snapshot.videoAspect = request.aspect_ratio || params.aspect_ratio;
    if(!snapshot.videoDuration && (request.duration || params.duration)) snapshot.videoDuration = request.duration || params.duration;
    if(!snapshot.routingMode) snapshot.routingMode = snapshot.provider_id || snapshot.model ? 'manual' : 'auto';
    const refs = (log?.promptRefs || log?.runPromptRefs || log?.refs || []).map(ref => ({
        asset_id:ref?.asset_id || '',
        url:ref?.url || '',
        name:ref?.name || 'image',
        nodeId:ref?.nodeId || '',
        imageIndex:ref?.imageIndex ?? '',
        kind:ref?.kind || 'image'
    })).filter(ref => ref.url);
    const prompt = String(log?.prompt || '');
    const displayPrompt = String(log?.displayPrompt || log?.promptText || prompt);
    return {
        prompt,
        displayPrompt,
        promptHtml:log?.promptHtml || '',
        promptText:displayPrompt,
        promptRefs:refs,
        inputRefs:(log?.inputRefs || refs).map(ref => ({...ref})),
        sourceNodeId:log?.sourceNodeId || log?.nodeId || '',
        settings:snapshot,
        createdAt:Number(log?.createdAt || Date.now())
    };
}

function smartHistoryLogTimestamp(log){
    const value = Number(log?.createdAt ?? log?.created_at ?? 0);
    if(!Number.isFinite(value) || value <= 0) return 0;
    return value < 1000000000000 ? value * 1000 : value;
}

function smartHistoryRunMs(log){
    const value = Number(log?.runMs ?? 0);
    if(Number.isFinite(value) && value > 0) return value;
    const start = smartHistoryLogTimestamp(log);
    const end = Number(log?.completedAt ?? log?.completed_at ?? 0);
    const endMs = Number.isFinite(end) && end > 0 ? (end < 1000000000000 ? end * 1000 : end) : 0;
    return start && endMs > start ? endMs - start : 0;
}

function historyLogModelMeta(log){
    const raw = String(log?.actual_model || log?.model || log?.request?.model || log?.params?.model || '').trim();
    if(!raw) return {raw:'', label:''};
    const providerKey = String(
        log?.actual_provider_id
        || log?.request?.provider_id
        || log?.request?.providerId
        || log?.params?.provider_id
        || log?.actual_provider_name
        || log?.platform
        || ''
    ).trim();
    let provider = (apiProviders || []).find(item =>
        String(item?.id || '') === providerKey || String(item?.name || '') === providerKey
    );
    if(!provider){
        provider = (apiProviders || []).find(item =>
            item?.model_names && Object.prototype.hasOwnProperty.call(item.model_names, raw)
        );
    }
    return {raw, label:modelDisplayName(provider, raw)};
}

function sortSmartHistoryLogs(logs){
    const list = Array.isArray(logs) ? logs : [];
    return list.slice().sort((a, b) => smartHistoryLogTimestamp(b) - smartHistoryLogTimestamp(a) || 0);
}

function smartPendingHistoryId(taskId){
    return `pending-generation-${String(taskId || '').trim()}`;
}

function smartPendingHistoryTaskId(log){
    const request = log?.request || {};
    return String(request.task_id || request.taskId || '').trim();
}

function beginSmartPendingHistoryLog(node, defaults={}){
    if(!canvas || !node) return '';
    canvas.history = Array.isArray(canvas.history) ? canvas.history : [];
    if(!defaults.forceNew){
        const currentId = String(node._pendingHistoryId || '');
        const current = canvas.history.find(log => String(log?.id || '') === currentId && log?.status === 'pending');
        if(current) return currentId;
        const matching = canvas.history.find(log =>
            log?.status === 'pending'
            && String(log?.nodeId || '') === String(node.id || '')
            && !smartPendingHistoryTaskId(log)
        );
        if(matching){
            node._pendingHistoryId = matching.id;
            return matching.id;
        }
    }
    const runSettings = defaults.runSettings || canvasCore.smartSettingsForNode(node);
    const providerId = String(defaults.providerId || runSettings.providerId || runSettings.provider_id || '');
    const model = String(defaults.model || runSettings.model || '');
    const prompt = String(defaults.prompt || node.runPrompt || node.promptDraftText || '').trim();
    const id = `pending-local-${uid('generation')}`;
    canvas.history.push({
        id,
        createdAt:Number(defaults.createdAt || node.runStartedAt || Date.now()),
        status:'pending',
        kind:String(defaults.kind || runSettings.apiKind || 'image'),
        platform:String(defaults.providerName || providerId || ''),
        nodeId:node.id || '',
        nodeType:node.type || NODE_TYPE_IMAGE,
        model,
        actual_provider_id:providerId,
        request:{provider_id:providerId, model},
        prompt,
        displayPrompt:prompt,
        runSettings:cloneSmartSettings(runSettings),
        outputs:[]
    });
    node._pendingHistoryId = id;
    canvas.history = sortSmartHistoryLogs(canvas.history);
    if(smartHistoryModal?.classList.contains('open')) scheduleSmartHistoryWindowRender(true);
    return id;
}

function ensureSmartPendingHistoryLogs(node, tasks=smartPendingTasks(node), defaults={}){
    if(!canvas || !node) return false;
    canvas.history = Array.isArray(canvas.history) ? canvas.history : [];
    let changed = false;
    const runSettings = canvasCore.smartSettingsForNode(node);
    (Array.isArray(tasks) ? tasks : []).forEach(task => {
        const taskId = String(task?.taskId || task?.task_id || '').trim();
        if(!taskId) return;
        const id = smartPendingHistoryId(taskId);
        if(canvas.history.some(log => String(log?.id || '') === id || smartHistoryTaskId(log) === taskId)) return;
        const createdAt = Number(task?.createdAt || task?.created_at || defaults.createdAt || node.runStartedAt || Date.now());
        const kind = String(task?.kind || defaults.kind || runSettings.apiKind || 'image');
        const providerId = String(task?.providerId || task?.provider_id || defaults.providerId || runSettings.providerId || '');
        const model = String(task?.model || defaults.model || runSettings.model || '');
        const prompt = String(task?.prompt || defaults.prompt || node.runPrompt || node.promptDraftText || '').trim();
        const localId = String(task?.pendingHistoryId || node._pendingHistoryId || '');
        const localEntry = canvas.history.find(log =>
            log?.status === 'pending'
            && !smartPendingHistoryTaskId(log)
            && (String(log?.id || '') === localId || String(log?.nodeId || '') === String(node.id || ''))
        );
        if(localEntry){
            localEntry.id = id;
            localEntry.createdAt = createdAt;
            localEntry.kind = kind;
            localEntry.platform = String(task?.providerName || task?.provider_name || defaults.providerName || providerId || '');
            localEntry.model = model;
            localEntry.actual_provider_id = providerId;
            localEntry.request = {...(localEntry.request || {}), task_id:taskId, provider_id:providerId, model};
            localEntry.prompt = prompt || localEntry.prompt || '';
            localEntry.displayPrompt = localEntry.prompt;
            localEntry.runSettings = cloneSmartSettings(runSettings);
            if(task?.progress) localEntry.progress = {...task.progress};
            if(String(node._pendingHistoryId || '') === localId) node._pendingHistoryId = id;
            changed = true;
            return;
        }
        canvas.history.push({
            id,
            createdAt,
            status:'pending',
            kind,
            platform:String(task?.providerName || task?.provider_name || defaults.providerName || providerId || ''),
            nodeId:node.id || '',
            nodeType:node.type || NODE_TYPE_IMAGE,
            model,
            actual_provider_id:providerId,
            request:{task_id:taskId, provider_id:providerId, model},
            prompt,
            displayPrompt:prompt,
            runSettings:cloneSmartSettings(runSettings),
            progress:task?.progress ? {...task.progress} : undefined,
            outputs:[]
        });
        changed = true;
    });
    if(changed){
        canvas.history = sortSmartHistoryLogs(canvas.history);
        if(smartHistoryModal?.classList.contains('open')) scheduleSmartHistoryWindowRender(true);
    }
    return changed;
}

function failSmartPendingHistoryLog(taskId, error=''){
    const id = smartPendingHistoryId(taskId);
    const entry = (canvas?.history || []).find(log => String(log?.id || '') === id || (log?.status === 'pending' && smartPendingHistoryTaskId(log) === String(taskId || '')));
    if(!entry) return false;
    entry.status = 'failed';
    entry.error = String(error || tr('smart.errRunFailed'));
    entry.completed_at = Date.now();
    entry.runMs = Math.max(0, entry.completed_at - smartHistoryLogTimestamp(entry));
    entry.id = uid('log');
    canvas.history = sortSmartHistoryLogs(canvas.history || []);
    if(smartHistoryModal?.classList.contains('open')) scheduleSmartHistoryWindowRender(true);
    return true;
}

function addSmartGenerationHistory({run, outputs=[], runMs=0, error=''}) {
    if(!canvas) return;
    canvas.history = canvas.history || [];
    const runNodeId = String(run?.nodeId || '');
    const runNode = canvasState.nodes.find(node => String(node?.id || '') === runNodeId);
    const pendingId = String(run?.pendingHistoryId || runNode?._pendingHistoryId || '');
    const pendingEntry = canvas.history.find(log =>
        log?.status === 'pending'
        && (String(log?.id || '') === pendingId || (runNodeId && String(log?.nodeId || '') === runNodeId))
    );
    if(pendingEntry){
        canvas.history = canvas.history.filter(log => log !== pendingEntry);
        if(runNode && String(runNode._pendingHistoryId || '') === pendingId) delete runNode._pendingHistoryId;
    }
    const outputItems = resultMediaUrls(outputs).map(item => {
        if(typeof item === 'string') return {url:item};
        if(!item || typeof item !== 'object') return null;
        const url = item.url || item.path || item.src || item.uri || '';
        if(!url) return null;
        return canvasCore.copyMediaSizeFields(item, {
            url,
            kind:item.kind || item.type || item.mediaKind || '',
            name:item.name || item.filename || ''
        });
    }).filter(item => item?.url);
    if(!error && outputItems.length) playGenerationCompleteSound();
    const entry = {
        id:uid('log'),
        createdAt:Number(pendingEntry?.createdAt || Date.now()),
        status:error ? 'failed' : 'success',
        kind:run?.kind || 'image',
        platform:smartRunPlatformLabel(run),
        nodeId:run?.nodeId || '',
        nodeType:run?.nodeType || NODE_TYPE_IMAGE,
        model:smartRunTaskLabel(run),
        request:smartRunRequestMeta(run),
        prompt:run?.prompt || '',
        displayPrompt:run?.displayPrompt || run?.prompt || '',
        runSettings:cloneSmartSettings(run?.runSettings || run?.settings || {}),
        promptRefs:(run?.refs || []).map(ref => ({...ref})),
        outputs:outputItems,
        refs:run?.refs || [],
        runMs:Number(runMs || 0),
        error:error ? String(error) : ''
    };
    canvas.history = [entry, ...canvas.history].slice(0, 500);
    if(!error && recoverStuckLoopOutputsFromHistory()) canvasCore.render();
    canvasCore.scheduleSave();
}
const SMART_LOG_PREVIEW_NODE_ID = '__smart_log_preview__';
let smartHistoryPreviewRestore = null;

function smartHistoryOutputItem(output){
    if(typeof output === 'string') return {url:output};
    if(!output || typeof output !== 'object') return null;
    const url = output.url || output.path || output.src || output.uri || '';
    if(!url) return null;
    return canvasCore.copyMediaSizeFields(output, {
        ...output,
        url,
        kind:output.kind || output.type || output.mediaKind || '',
        name:output.name || output.filename || ''
    });
}

function smartHistoryEntryById(historyId){
    return (canvas?.history || []).find(log => String(log?.id || '') === String(historyId || '')) || null;
}

function smartHistoryImageOutputs(log){
    return (log?.outputs || []).map((output, outputIndex) => {
        const item = smartHistoryOutputItem(output);
        const kind = item?.kind || (outputUrlLooksVideo(item?.url || '') ? 'video' : 'image');
        return item?.url && kind === 'image' ? {
            ...item,
            outputIndex,
            outputId:String(item.output_id || ''),
            kind:'image',
            name:item.name || smartImageNameFromUrl(item.url)
        } : null;
    }).filter(Boolean);
}

function sendSmartHistoryOutputsToCanvas(historyId, outputIndex=null, dropPoint=null){
    const log = smartHistoryEntryById(historyId);
    let outputs = smartHistoryImageOutputs(log);
    if(Number.isInteger(outputIndex)) outputs = outputs.filter(output => output.outputIndex === outputIndex);
    if(log?.kind === 'video' && !outputs.length) return;
    const rect = canvasCore.shell.getBoundingClientRect();
    const point = dropPoint || screenToWorld({clientX:rect.left + rect.width / 2, clientY:rect.top + rect.height / 2});
    const failed = log?.status === 'failed' || !outputs.length;
    const runMeta = smartHistoryRunMeta(log);
    pushUndo();
    const node = createImageNodeAt(point, outputs.map(output => {
        const clean = {...output};
        delete clean.outputIndex;
        return clean;
    }), {
        skipUndo:true,
        title:outputs.length > 1 ? 'Group' : 'Image',
        runMeta,
        historyId:log?.id || '',
        needsRegeneration:failed,
        generationState:failed ? 'ready' : 'complete'
    });
    node.runStatus = failed ? 'failed' : 'succeeded';
    canvasCore.scheduleSave();
    closeSmartCanvasLog();
    toast(outputs.length ? tr('canvas.sentToCanvas').replace('{n}', outputs.length) : tr('smart.sentFailedLogToCanvas'));
    return node;
}

function setSmartHistoryDragState(active, card=null){
    smartHistoryModal?.classList.toggle('log-drag-active', active);
    smartHistoryModal?.querySelectorAll('.log-media-card.is-dragging,.log-history-image.is-dragging').forEach(el => el.classList.remove('is-dragging'));
    if(active){
        card?.classList.add('is-dragging');
        card?.querySelector?.('.log-history-image')?.classList.add('is-dragging');
    }
}

function clearSmartHistoryTap(){
    if(canvasState.smartHistoryTap?.timer) clearTimeout(canvasState.smartHistoryTap.timer);
    canvasState.smartHistoryTap = null;
}

function selectSmartHistoryImage(card){
    if(!card) return;
    const logItem = card.closest('[data-canvas-history-id]');
    canvasState.smartHistorySelectedImage = {
        historyId:logItem?.dataset.canvasHistoryId || '',
        outputIndex:Number(card.dataset.historyOutputIndex || 0),
        outputId:String(card.dataset.outputId || '')
    };
    smartHistoryModal?.querySelectorAll('.log-media-card.is-selected').forEach(item => {
        item.classList.remove('is-selected');
        item.removeAttribute('data-history-selected');
        item.querySelector('.log-history-image')?.setAttribute('aria-pressed', 'false');
    });
    smartHistoryModal?.querySelectorAll('.log-output-preview.is-selected').forEach(item => {
        item.classList.remove('is-selected');
        item.setAttribute('aria-current', 'false');
    });
    card.classList.add('is-selected');
    card.dataset.historySelected = 'true';
    card.querySelector('.log-history-image')?.setAttribute('aria-pressed', 'true');
    const preview = logItem?.querySelector(`.log-output-preview[data-history-output-preview="${canvasState.smartHistorySelectedImage.outputIndex}"]`);
    preview?.classList.add('is-selected');
    preview?.setAttribute('aria-current', 'true');
}

function smartHistoryDrawerContainsPoint(clientX, clientY){
    const rect = smartHistoryModal?.getBoundingClientRect?.();
    return Boolean(rect && clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom);
}

function smartHistoryCanvasDropPoint(clientX, clientY){
    const target = document.elementFromPoint(clientX, clientY);
    if(!target?.closest?.('#world')) return null;
    return screenToWorld({clientX, clientY});
}

function handleSmartHistoryTap(image, historyId, outputIndex, clientX, clientY){
    const now = performance.now();
    const previous = canvasState.smartHistoryTap;
    const sameImage = previous
        && previous.historyId === historyId
        && previous.outputIndex === outputIndex
        && now - previous.time <= 320
        && Math.hypot(clientX - previous.clientX, clientY - previous.clientY) <= 20;
    if(sameImage){
        clearSmartHistoryTap();
        openSmartHistoryDetail({url:image?.dataset?.originalSrc || image?.dataset?.url || '', kind:'image', historyId, outputIndex});
        return;
    }
    if(previous?.timer) clearTimeout(previous.timer);
    const card = image?.closest('.log-media-card');
    const tap = {historyId, outputIndex, clientX, clientY, time:now, timer:null};
    tap.timer = setTimeout(() => {
        selectSmartHistoryImage(card);
        if(canvasState.smartHistoryTap === tap) canvasState.smartHistoryTap = null;
    }, 320);
    canvasState.smartHistoryTap = tap;
}

function clearSmartHistoryPointerState(){
    const state = canvasState.smartHistoryPointerState;
    if(!state) return;
    state.image?.classList.remove('is-dragging');
    try { state.image?.releasePointerCapture?.(state.pointerId); } catch(_) {}
    setSmartHistoryDragState(false);
    canvasState.smartHistoryPointerState = null;
    scheduleSmartHistoryWindowRender();
}

function setSmartHistoryDragPayload(event, item){
    const transfer = event.dataTransfer;
    if(!transfer) return;
    const payload = {
        asset_id:item?.asset_id || '',
        url:item?.url || '',
        preview_url:item?.preview_url || '',
        name:item?.name || 'image',
        kind:'image',
        mime_type:item?.mime_type || 'image/png'
    };
    transfer.effectAllowed = 'copy';
    transfer.setData('application/x-smart-asset', JSON.stringify(payload));
    if(payload.url) transfer.setData('text/plain', payload.url);
}

function bindSmartHistoryImage(image, item, historyId, outputIndex){
    image.draggable = true;
    image.onpointerdown = event => {
        if(event.button !== 0) return;
        event.stopPropagation();
        const pointerId = event.pointerId ?? 1;
        try { image.setPointerCapture?.(pointerId); } catch(_) {}
        canvasState.smartHistoryPointerState = {
            image,
            card:image.closest('.log-media-card'),
            historyId,
            outputIndex,
            pointerId,
            startX:event.clientX,
            startY:event.clientY,
            moved:false,
            nativeDrag:false
        };
    };
    image.onpointermove = event => {
        const state = canvasState.smartHistoryPointerState;
        if(!state || state.image !== image || state.pointerId !== (event.pointerId ?? 1)) return;
        const distance = Math.hypot(event.clientX - state.startX, event.clientY - state.startY);
        if(!state.moved && distance >= 8){
            state.moved = true;
            clearSmartHistoryTap();
            setSmartHistoryDragState(true, state.card);
        }
        if(state.moved) event.preventDefault();
        event.stopPropagation();
    };
    image.onpointerup = event => {
        const state = canvasState.smartHistoryPointerState;
        if(!state || state.image !== image) return;
        event.stopPropagation();
        if(state.moved){
            event.preventDefault();
            const dropPoint = !state.nativeDrag && !smartHistoryDrawerContainsPoint(event.clientX, event.clientY)
                ? smartHistoryCanvasDropPoint(event.clientX, event.clientY)
                : null;
            if(dropPoint){
                sendSmartHistoryOutputsToCanvas(historyId, outputIndex, dropPoint);
            }
            clearSmartHistoryPointerState();
            return;
        }
        clearSmartHistoryPointerState();
        handleSmartHistoryTap(image, historyId, outputIndex, event.clientX, event.clientY);
    };
    image.onpointercancel = event => {
        event.stopPropagation();
        clearSmartHistoryPointerState();
    };
    image.ondragstart = event => {
        const state = canvasState.smartHistoryPointerState || {
            image,
            card:image.closest('.log-media-card'),
            historyId,
            outputIndex,
            pointerId:event.pointerId ?? 1,
            startX:event.clientX,
            startY:event.clientY,
            moved:true,
            nativeDrag:true
        };
        state.moved = true;
        state.nativeDrag = true;
        canvasState.smartHistoryPointerState = state;
        clearSmartHistoryTap();
        setSmartHistoryDragPayload(event, item);
        setSmartHistoryDragState(true, state.card);
        event.stopPropagation();
    };
    image.ondragend = event => {
        event.stopPropagation();
        clearSmartHistoryPointerState();
    };
    image.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
    };
    image.ondblclick = event => {
        event.preventDefault();
        event.stopPropagation();
    };
    image.onkeydown = event => {
        if(event.key === 'Enter'){
            event.preventDefault();
            event.stopPropagation();
            selectSmartHistoryImage(image.closest('.log-media-card'));
        } else if(event.key === ' ' || event.key === 'Spacebar'){
            event.preventDefault();
            event.stopPropagation();
            openSmartHistoryDetail({url:image?.dataset?.originalSrc || image?.dataset?.url || '', kind:'image', historyId, outputIndex});
        }
    };
}

function smartHistoryResolutionTier(log, outputs=[]){
    if(log?.kind === 'video' || log?.outputKind === 'video' || outputs.some(item => item?.kind === 'video' || outputUrlLooksVideo(item?.url || ''))) return '';
    const sources = [log?.params, log?.request, log?.runSettings].filter(Boolean);
    for(const source of sources){
        const tier = normalizedResolutionTier(source.resolution || source.image_size || source.imageSize || '');
        if(tier) return tier;
    }
    for(const source of sources){
        const parsed = canvasCore.parseSizeValue(source.size || '');
        const longest = Math.max(Number(parsed?.width || 0), Number(parsed?.height || 0));
        if(longest >= 3072) return '4K';
        if(longest >= 1536) return '2K';
        if(longest >= 768) return '1K';
    }
    const longest = outputs.reduce((maxValue, item) => Math.max(maxValue, Number(item?.natural_w || item?.width || 0), Number(item?.natural_h || item?.height || 0)), 0);
    if(longest >= 3072) return '4K';
    if(longest >= 1536) return '2K';
    if(longest >= 768) return '1K';
    return '';
}

function smartHistorySizeSummary(log, outputs=[]){
    const req = log?.request || {};
    const requestLabel = normalizedSizeLabel(req.size || req.resolution || '');
    const actualLabels = [...new Set(outputs.map(imageResolutionLabel).filter(Boolean))];
    if(!actualLabels.length) return '';
    const actualText = actualLabels.slice(0, 3).join(', ');
    const more = actualLabels.length > 3 ? ` +${actualLabels.length - 3}` : '';
    const actualLabel = `${actualText}${more}`;
    if(requestLabel && actualLabels.some(label => label !== requestLabel)){
        return canvasCore.trf('smart.requestActual', {r: requestLabel, a: actualLabel});
    }
    return canvasCore.trf('smart.actualOnly', {a: actualLabel});
}
// 移除临时预览节点并还原选中态。供 closeImageEditor 调用。

function cleanupSmartHistoryPreviewNode(){
    if(canvasState.nodes.some(n => n.id === SMART_LOG_PREVIEW_NODE_ID)){ canvasState.nodes = canvasState.nodes.filter(n => n.id !== SMART_LOG_PREVIEW_NODE_ID); if(canvas) canvas.nodes = canvasState.nodes; }
    if(smartHistoryPreviewRestore){
        canvasState.selectedId = smartHistoryPreviewRestore.selectedId;
        canvasState.selectedImage = smartHistoryPreviewRestore.selectedImage;
        smartHistoryPreviewRestore = null;
    }
}
let smartHistoryDetailLastFocus = null;
let smartHistoryDetailSession = {historyId:'', outputs:[], outputIndex:0, outputId:''};
let smartHistoryDetailView = {zoom:1, pan:{x:0, y:0}, drag:null};

function applySmartHistoryDetailTransform(){
    const box = document.getElementById('smartHistoryLightbox');
    const stage = box?.querySelector('.smart-log-detail-stage');
    const frame = box?.querySelector('.smart-log-detail-frame');
    if(frame){
        frame.style.transform = `translate(${smartHistoryDetailView.pan.x}px, ${smartHistoryDetailView.pan.y}px) scale(${smartHistoryDetailView.zoom})`;
        frame.dataset.zoom = String(smartHistoryDetailView.zoom);
    }
    stage?.classList.toggle('panning', Boolean(smartHistoryDetailView.drag));
    const label = box?.querySelector('.smart-log-detail-zoom-label');
    if(label) label.textContent = `${Math.round(smartHistoryDetailView.zoom * 100)}%`;
}

function resetSmartHistoryDetailView(){
    smartHistoryDetailView = {zoom:1, pan:{x:0, y:0}, drag:null};
    applySmartHistoryDetailTransform();
}

function zoomSmartHistoryDetail(factor, clientX=null, clientY=null){
    const frame = document.querySelector('#smartHistoryLightbox.open .smart-log-detail-frame');
    const rect = frame?.getBoundingClientRect();
    if(!rect) return;
    const next = zoomPanAroundPoint(
        smartHistoryDetailView.zoom,
        smartHistoryDetailView.pan,
        factor,
        rect,
        clientX ?? rect.left + rect.width / 2,
        clientY ?? rect.top + rect.height / 2,
        {min:0.1, max:8}
    );
    smartHistoryDetailView.zoom = next.zoom;
    smartHistoryDetailView.pan = next.pan;
    applySmartHistoryDetailTransform();
}

function finishSmartHistoryDetailPan(stage, pointerId){
    if(!smartHistoryDetailView.drag) return;
    try { stage?.releasePointerCapture?.(pointerId ?? smartHistoryDetailView.drag.pointerId); } catch (_) {}
    smartHistoryDetailView.drag = null;
    applySmartHistoryDetailTransform();
}

function setupSmartHistoryDetailInteractions(box){
    const stage = box.querySelector('.smart-log-detail-stage');
    stage.addEventListener('wheel', event => {
        event.preventDefault();
        event.stopPropagation();
        zoomSmartHistoryDetail(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX, event.clientY);
    }, {passive:false});
    stage.addEventListener('pointerdown', event => {
        if(event.button !== 0 || event.target.closest('.smart-log-detail-zoom-tools,.smart-log-detail-output-nav')) return;
        event.stopPropagation();
        stage.setPointerCapture?.(event.pointerId);
        smartHistoryDetailView.drag = {
            pointerId:event.pointerId,
            clientX:event.clientX,
            clientY:event.clientY,
            startX:smartHistoryDetailView.pan.x,
            startY:smartHistoryDetailView.pan.y
        };
        applySmartHistoryDetailTransform();
    });
    stage.addEventListener('pointermove', event => {
        const drag = smartHistoryDetailView.drag;
        if(!drag || drag.pointerId !== event.pointerId) return;
        event.preventDefault();
        event.stopPropagation();
        smartHistoryDetailView.pan = {
            x:drag.startX + event.clientX - drag.clientX,
            y:drag.startY + event.clientY - drag.clientY
        };
        applySmartHistoryDetailTransform();
    });
    stage.addEventListener('pointerup', event => finishSmartHistoryDetailPan(stage, event.pointerId));
    stage.addEventListener('pointercancel', event => finishSmartHistoryDetailPan(stage, event.pointerId));
    stage.addEventListener('dblclick', event => {
        if(event.target.closest('.smart-log-detail-zoom-tools,.smart-log-detail-output-nav')) return;
        event.preventDefault();
        event.stopPropagation();
        resetSmartHistoryDetailView();
    });
    box.querySelectorAll('[data-smart-log-detail-zoom]').forEach(button => {
        button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            const action = button.dataset.smartLogDetailZoom;
            if(action === 'reset') resetSmartHistoryDetailView();
            else zoomSmartHistoryDetail(action === 'in' ? 1.12 : 1 / 1.12);
        });
    });
}

function closeSmartHistoryLightbox(){
    const box = document.getElementById('smartHistoryLightbox');
    if(!box) return;
    box.classList.remove('open');
    box.setAttribute('aria-hidden', 'true');
    const img = box.querySelector('.smart-log-detail-image');
    if(img){ img.onerror = null; img.removeAttribute('src'); }
    smartHistoryDetailSession = {historyId:'', outputs:[], outputIndex:0, outputId:''};
    box.querySelector('.smart-log-detail-output-strip')?.replaceChildren();
    // #207：openTrashMediaPreview 注入的回收区「恢复」按钮随面板关闭统一清理。
    box.querySelector('.smart-trash-detail-restore')?.remove();
    resetSmartHistoryDetailView();
    const restore = smartHistoryDetailLastFocus;
    smartHistoryDetailLastFocus = null;
    if(restore?.isConnected) requestAnimationFrame(() => restore.focus({preventScroll:true}));
}

function ensureSmartHistoryDetail(){
    let box = document.getElementById('smartHistoryLightbox');
    if(box) return box;
    box = document.createElement('div');
    box.id = 'smartHistoryLightbox';
    box.className = 'smart-log-lightbox';
    box.setAttribute('aria-hidden', 'true');
    box.innerHTML = `<article class="smart-log-detail" role="dialog" aria-modal="true" aria-labelledby="smartHistoryDetailTitle">
        <div class="smart-log-detail-stage preview-stage" aria-label="${escapeAttr(tr('smart.generationDetailPreview'))}">
            <div class="smart-log-detail-frame preview-frame"><img class="smart-log-detail-image" alt="${escapeAttr(tr('canvas.originalImage'))}" draggable="false"></div>
            <button class="smart-log-detail-output-nav is-previous" type="button" data-smart-log-detail-output-nav="previous" title="${escapeAttr(tr('smart.generationPreviousOutput'))}" aria-label="${escapeAttr(tr('smart.generationPreviousOutput'))}"><i data-lucide="chevron-left"></i></button>
            <button class="smart-log-detail-output-nav is-next" type="button" data-smart-log-detail-output-nav="next" title="${escapeAttr(tr('smart.generationNextOutput'))}" aria-label="${escapeAttr(tr('smart.generationNextOutput'))}"><i data-lucide="chevron-right"></i></button>
            <div class="smart-log-detail-zoom-tools" aria-label="${escapeAttr(tr('smart.previewZoomControls'))}">
                <button type="button" data-smart-log-detail-zoom="out" title="${escapeAttr(tr('smart.previewZoomOut'))}" aria-label="${escapeAttr(tr('smart.previewZoomOut'))}"><i data-lucide="minus"></i></button>
                <output class="smart-log-detail-zoom-label" aria-live="polite">100%</output>
                <button type="button" data-smart-log-detail-zoom="in" title="${escapeAttr(tr('smart.previewZoomIn'))}" aria-label="${escapeAttr(tr('smart.previewZoomIn'))}"><i data-lucide="plus"></i></button>
                <button type="button" data-smart-log-detail-zoom="reset" title="${escapeAttr(tr('smart.previewZoomReset'))}" aria-label="${escapeAttr(tr('smart.previewZoomReset'))}"><i data-lucide="rotate-ccw"></i></button>
            </div>
            <span class="smart-log-detail-preview-hint">${escapeHtml(tr('smart.generationDetailPreviewHint'))}</span>
        </div>
        <aside class="smart-log-detail-info">
            <header class="smart-log-detail-head"><h2 id="smartHistoryDetailTitle">${escapeHtml(tr('smart.generationDetail'))}</h2><button class="smart-log-lightbox-close" type="button" title="${escapeAttr(tr('common.close'))}" aria-label="${escapeAttr(tr('common.close'))}"><i data-lucide="x"></i></button></header>
            <dl class="smart-log-detail-meta"><div><dt>${escapeHtml(tr('smart.generationModel'))}</dt><dd class="smart-log-detail-model"></dd></div><div><dt>${escapeHtml(tr('smart.generationTime'))}</dt><dd class="smart-log-detail-time"></dd></div></dl>
            <section class="smart-log-detail-outputs" aria-labelledby="smartHistoryDetailOutputsTitle"><div class="smart-log-detail-output-head"><h3 id="smartHistoryDetailOutputsTitle">${escapeHtml(tr('smart.generationOutputs'))}</h3><output class="smart-log-detail-output-position" aria-live="polite"></output></div><div class="smart-log-detail-output-strip" role="group"></div></section>
            <section class="smart-log-detail-modes" aria-label="${escapeAttr(tr('canvas.editImage'))}">
                <button type="button" data-smart-log-detail-mode="preview"><i data-lucide="eye"></i><span>${escapeHtml(tr('smart.modePreview'))}</span></button>
                <button type="button" data-smart-log-detail-mode="crop"><i data-lucide="crop"></i><span>${escapeHtml(tr('canvas.modeCrop'))}</span></button>
                <button type="button" data-smart-log-detail-mode="outpaint"><i data-lucide="expand"></i><span>${escapeHtml(tr('canvas.modeOutpaint'))}</span></button>
                <button type="button" data-smart-log-detail-mode="mask"><i data-lucide="brush"></i><span>${escapeHtml(tr('canvas.modeMask'))}</span></button>
                <button type="button" data-smart-log-detail-mode="brush"><i data-lucide="paintbrush"></i><span>${escapeHtml(tr('canvas.modeBrush'))}</span></button>
                <button type="button" data-smart-log-detail-mode="resize"><i data-lucide="minimize-2"></i><span>${escapeHtml(tr('canvas.modeResize'))}</span></button>
                <button type="button" data-smart-log-detail-mode="grid"><i data-lucide="grid-3x3"></i><span>${escapeHtml(tr('canvas.modeGrid'))}</span></button>
            </section>
            <button type="button" class="smart-log-detail-mark image-mark" data-smart-log-detail-mark data-marked-asset="" data-marked-title="1" title="${escapeAttr(tr('smart.markImage'))}"><i data-lucide="star"></i></button>
            <section class="smart-log-detail-prompt-wrap"><h3>${escapeHtml(tr('smart.generationPromptDetail'))}</h3><div class="smart-log-detail-prompt" tabindex="0"></div></section>
            <div class="smart-log-detail-actions"><button class="smart-log-detail-download" type="button"><i data-lucide="download"></i><span>${escapeHtml(tr('smart.downloadOriginal'))}</span></button><button class="smart-log-detail-copy-2k" type="button"><i data-lucide="copy"></i><span>${escapeHtml(tr('smart.copy2k'))}</span></button><button class="smart-log-detail-delete-original" type="button"><i data-lucide="image-minus"></i><span>${escapeHtml(tr('canvas.deleteSelectedOriginal'))}</span></button></div>
        </aside>
    </article>`;
    document.body.appendChild(box);
    box.addEventListener('click', event => {
        if(event.target === box || event.target.closest('.smart-log-lightbox-close')) closeSmartHistoryLightbox();
    });
    setupSmartHistoryDetailInteractions(box);
    box.querySelectorAll('[data-smart-log-detail-output-nav]').forEach(button => {
        button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            switchSmartHistoryDetailOutput(button.dataset.smartLogDetailOutputNav === 'previous' ? -1 : 1);
        });
    });
    // #89 第四轮：详情面板工具排——与预览大图同一套编辑器（openImageEditor + setImageEditMode）。
    box.querySelectorAll('[data-smart-log-detail-mode]').forEach(button => {
        button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            const outputs = smartHistoryDetailSession.outputs || [];
            const item = outputs.find(output => output.outputIndex === smartHistoryDetailSession.outputIndex) || null;
            if(!item?.url) return;
            const ref = smartHistoryDetailNodeImageRef(item);
            if(!ref){ toast(tr('smart.markNotOnCanvas')); return; }
            openImageEditor(ref.nodeId, ref.index);
            if(button.dataset.smartLogDetailMode !== 'preview') setImageEditMode(button.dataset.smartLogDetailMode, true);
        });
    });
    box.querySelector('[data-smart-log-detail-mark]')?.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const outputs = smartHistoryDetailSession.outputs || [];
        const item = outputs.find(output => output.outputIndex === smartHistoryDetailSession.outputIndex) || null;
        const assetId = String(item?.asset_id || '');
        if(!assetId){ toast(tr('smart.markUnavailable')); return; }
        setMarkedImage(assetId, !getMarkedAssetIds().includes(assetId));
    });
    return box;
}

function smartHistoryDetailPositionLabel(current, total){
    return tr('smart.generationOutputPosition')
        .replace('{current}', String(current))
        .replace('{total}', String(total));
}

function selectSmartHistoryDetailOutput(outputIndex, {focus=false}={}){
    const box = document.querySelector('#smartHistoryLightbox.open') || document.getElementById('smartHistoryLightbox');
    const outputs = smartHistoryDetailSession.outputs;
    const requested = Number(outputIndex);
    const sessionIndex = outputs.findIndex(output => output.outputIndex === requested);
    if(!box || !outputs.length || sessionIndex < 0) return false;
    const item = outputs[sessionIndex];
    smartHistoryDetailSession.outputIndex = item.outputIndex;
    smartHistoryDetailSession.outputId = String(item.output_id || '');
    resetSmartHistoryDetailView();
    const total = outputs.length;
    const position = smartHistoryDetailPositionLabel(sessionIndex + 1, total);
    const positionOutput = box.querySelector('.smart-log-detail-output-position');
    if(positionOutput) positionOutput.textContent = position;
    box.querySelectorAll('.smart-log-detail-output').forEach(button => {
        const selected = String(button.dataset.outputId || '') === String(item.output_id || '');
        button.classList.toggle('is-selected', selected);
        button.setAttribute('aria-current', selected ? 'true' : 'false');
        if(selected && focus) requestAnimationFrame(() => button.focus({preventScroll:true}));
    });
    box.querySelectorAll('.smart-log-detail-output-nav').forEach(button => {
        button.hidden = total < 2;
    });
    const download = box.querySelector('.smart-log-detail-download');
    download.dataset.outputIndex = String(item.outputIndex);
    download.dataset.outputId = String(item.output_id || '');
    download.dataset.assetId = String(item.asset_id || '');
    download.title = `${tr('smart.downloadOriginal')} · ${position}`;
    download.setAttribute('aria-label', download.title);
    download.onclick = event => {
        event.preventDefault();
        canvasCore.downloadPreviewFile(item);
    };
    const deleteOriginal = box.querySelector('.smart-log-detail-delete-original');
    deleteOriginal.dataset.outputIndex = String(item.outputIndex);
    deleteOriginal.dataset.outputId = String(item.output_id || '');
    deleteOriginal.dataset.assetId = String(item.asset_id || '');
    deleteOriginal.title = `${tr('canvas.deleteSelectedOriginal')} · ${position}`;
    deleteOriginal.setAttribute('aria-label', deleteOriginal.title);
    // #87：「删除当前原图」需要 log 墓碑上下文，无 log（简化形态）时隐藏。
    deleteOriginal.hidden = !item.asset_id || !smartHistoryEntryById(smartHistoryDetailSession?.historyId) || canvas?.format !== 'firefly-canvas-v1';
    deleteOriginal.onclick = event => {
        event.preventDefault();
        void deleteSmartProjectOriginal(smartHistoryDetailSession.historyId, item.outputIndex);
    };
    // #89 第四轮：工具排对视频隐藏；当前 output 不在画布节点上时禁用（提示后再点）。
    const modesSection = box.querySelector('.smart-log-detail-modes');
    if(modesSection){
        const isVideo = String(item.kind || '').includes('video') || outputUrlLooksVideo(String(item.url || ''));
        modesSection.style.display = isVideo ? 'none' : '';
        const nodeRef = smartHistoryDetailNodeImageRef(item);
        modesSection.querySelectorAll('[data-smart-log-detail-mode]').forEach(btn => {
            btn.disabled = !nodeRef;
        });
    }
    const markBtn = box.querySelector('[data-smart-log-detail-mark]');
    if(markBtn){
        markBtn.dataset.markedAsset = String(item.asset_id || '');
        markBtn.disabled = !item.asset_id;
        markBtn.hidden = String(item.kind || '').includes('video') || outputUrlLooksVideo(String(item.url || ''));
        syncMarkedImageButtons();
    }
    const copy2k = box.querySelector('.smart-log-detail-copy-2k');
    copy2k.dataset.outputIndex = String(item.outputIndex);
    copy2k.dataset.outputId = String(item.output_id || '');
    copy2k.dataset.assetId = String(item.asset_id || '');
    copy2k.title = `${tr('smart.copy2k')} · ${position}`;
    copy2k.setAttribute('aria-label', copy2k.title);
    copy2k.disabled = false;
    copy2k.removeAttribute('aria-busy');
    copy2k.onclick = async event => {
        event.preventDefault();
        copy2k.disabled = true;
        copy2k.setAttribute('aria-busy', 'true');
        try {
            await copySmartHistoryImage2k(item);
            toast(tr('smart.copy2kDone'));
        } catch(error){
            toast(error?.message || tr('smart.pairCopyFailed'));
        } finally {
            copy2k.disabled = false;
            copy2k.removeAttribute('aria-busy');
        }
    };
    const img = box.querySelector('.smart-log-detail-image');
    let triedFallback = false;
    img.onerror = () => {
        if(triedFallback) return;
        triedFallback = true;
        const fallback = smartMediaPreviewUrl(item, 2048);
        if(fallback && fallback !== img.getAttribute('src')) img.src = fallback;
    };
    img.dataset.outputIndex = String(item.outputIndex);
    img.dataset.outputId = String(item.output_id || '');
    img.dataset.assetId = String(item.asset_id || '');
    img.alt = `${tr('canvas.originalImage')} · ${position}`;
    img.src = canvasCore.displayMediaUrl(item);
    return true;
}

function switchSmartHistoryDetailOutput(delta){
    const outputs = smartHistoryDetailSession.outputs;
    if(outputs.length < 2) return false;
    const current = outputs.findIndex(output => output.outputIndex === smartHistoryDetailSession.outputIndex);
    const next = (Math.max(0, current) + Number(delta) + outputs.length) % outputs.length;
    return selectSmartHistoryDetailOutput(outputs[next].outputIndex, {focus:true});
}

function renderSmartHistoryDetailOutputs(box){
    const outputs = smartHistoryDetailSession.outputs;
    const strip = box.querySelector('.smart-log-detail-output-strip');
    strip.style.cssText = `--smart-log-output-count:${Math.min(outputs.length, 8)};--smart-log-output-columns:${Math.min(outputs.length, 4)}`;
    strip.innerHTML = outputs.map((item, index) => {
        const position = smartHistoryDetailPositionLabel(index + 1, outputs.length);
        const preview = smartPreviewImgHtml(item, 256, `alt="${escapeAttr(position)}" draggable="false"`);
        return `<button class="smart-log-detail-output" type="button" data-output-index="${item.outputIndex}" data-output-id="${escapeAttr(item.output_id || '')}" data-asset-id="${escapeAttr(item.asset_id || '')}" aria-current="false" aria-label="${escapeAttr(position)}" title="${escapeAttr(position)}">${preview}<span>${index + 1}</span></button>`;
    }).join('');
    bindSmartPreviewImageFallbacks(strip);
    strip.querySelectorAll('.smart-log-detail-output').forEach(button => {
        button.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            selectSmartHistoryDetailOutput(Number(button.dataset.outputIndex), {focus:true});
        };
    });
}

function openSmartHistoryDetail({url, kind='image', historyId='', outputIndex=0}={}){
    if(!url) return;
    if(kind === 'video' || outputUrlLooksVideo(url)){ window.open(canvasCore.displayMediaUrl({url}), '_blank'); return; }
    const log = smartHistoryEntryById(historyId);
    const fallback = smartHistoryOutputItem({url, kind, name:smartImageNameFromUrl(url)});
    const outputs = smartHistoryImageOutputs(log);
    const requestedIndex = Number(outputIndex);
    if(!outputs.some(output => output.outputIndex === requestedIndex) && fallback?.url){
        outputs.push({...fallback, outputIndex:requestedIndex, kind:'image'});
    }
    if(!outputs.length) return;
    const modelMeta = historyLogModelMeta(log);
    const timestamp = smartHistoryLogTimestamp(log);
    const box = ensureSmartHistoryDetail();
    smartHistoryDetailLastFocus = document.activeElement;
    const requestedOutput = outputs.find(output => output.outputIndex === requestedIndex) || outputs[0];
    smartHistoryDetailSession = {
        historyId:String(historyId || ''),
        outputs,
        outputIndex:requestedOutput.outputIndex,
        outputId:String(requestedOutput.output_id || ''),
    };
    box.querySelector('.smart-log-detail-model').textContent = modelMeta.label || modelMeta.raw || tr('canvas.historyUnrecorded');
    box.querySelector('.smart-log-detail-time').textContent = timestamp
        ? new Date(timestamp).toLocaleString(window.StudioI18n?.lang() === 'en' ? 'en-US' : 'zh-CN')
        : tr('canvas.historyUnrecorded');
    box.querySelector('.smart-log-detail-prompt').textContent = String(log?.prompt || tr('canvas.historyNoPrompt'));
    // #87：无 log（手动导入图等）→ 同一面板的简化形态：隐藏模型/时间/提示词行，
    // 不是另一套 UI；「删除当前原图」需要 log 墓碑上下文，无 log 时隐藏。
    const hasLogContext = Boolean(log);
    box.querySelector('.smart-log-detail-meta').style.display = hasLogContext ? '' : 'none';
    box.querySelector('.smart-log-detail-prompt-wrap').style.display = hasLogContext ? '' : 'none';
    if(!hasLogContext){
        const deleteOriginal = box.querySelector('.smart-log-detail-delete-original');
        if(deleteOriginal) deleteOriginal.hidden = true;
    }
    renderSmartHistoryDetailOutputs(box);
    box.classList.add('open');
    box.setAttribute('aria-hidden', 'false');
    selectSmartHistoryDetailOutput(requestedIndex);
    canvasCore.refreshIcons(box);
    requestAnimationFrame(() => box.querySelector('.smart-log-lightbox-close')?.focus({preventScroll:true}));
}

function smartHistoryPreviewNode(url, kind='image', context={}){
    openSmartHistoryDetail({url, kind, ...context});
}
// #87：画布图片「详情」入口——统一打开「生成详情」面板（与生图历史抽屉同一组件，
// 以后改详情 UI 只改一处）；能匹配到生成历史时展示完整元数据，
// 匹配不到（手动导入等）则同一组件的简化形态（隐藏模型/时间/提示词行）。

function smartHistoryContextForImage(node, imageIndex){
    const source = node?.images?.[imageIndex];
    const item = canvasCore.imageForDisplay(source) || source;
    const assetId = String(source?.asset_id || item?.asset_id || '');
    const url = String(item?.url || source?.url || '');
    if(!assetId && !url) return null;
    const logs = Array.isArray(canvas?.history) ? canvas.history : [];
    for(const log of logs){
        if(!log || typeof log !== 'object' || !String(log?.id || '')) continue;
        const outputs = smartHistoryImageOutputs(log);
        const outputIndex = outputs.findIndex(output => {
            if(assetId && String(output?.asset_id || '') === assetId) return true;
            if(!assetId && url && String(output?.url || '') === url) return true;
            return false;
        });
        if(outputIndex >= 0) return {historyId:String(log.id || ''), outputIndex};
    }
    return null;
}

function smartActiveHistoryTaskLogs(logs, excludedHistoryId=''){
    return (logs || []).filter(item =>
        item?.status === 'pending'
        && smartPendingHistoryTaskId(item)
        && String(item.id || '') !== String(excludedHistoryId || '')
    );
}

function mergeSmartActiveHistoryTaskLogs(serverLogs, activeTaskLogs){
    const hydratedServerLogs = hydrateProjectMediaReferences(serverLogs || []);
    const serverHistoryIds = new Set(hydratedServerLogs.map(item => String(item?.id || '')));
    return sortSmartHistoryLogs([
        ...hydratedServerLogs,
        ...(activeTaskLogs || []).filter(item => !serverHistoryIds.has(String(item?.id || ''))),
    ]);
}
let smartHistoryDeletingBusy = false;

function captureSmartOriginalDeletionUiState(historyId, outputId, outputIndex=-1){
    const normalizedOutputId = String(outputId || '');
    const logOutputs = smartHistoryImageOutputs(smartHistoryEntryById(historyId));
    const deletionPosition = logOutputs.findIndex(output => String(output.output_id || '') === normalizedOutputId);
    const historySelectedOutputId = String(canvasState.smartHistorySelectedImage?.outputId || '');
    const historyOutput = canvasState.smartHistorySelectedImage?.historyId === String(historyId || '')
        ? historySelectedOutputId
            ? logOutputs.find(output => String(output.output_id || '') === historySelectedOutputId)
            : logOutputs.find(output => output.outputIndex === Number(canvasState.smartHistorySelectedImage.outputIndex))
        : null;
    const detailOpen = Boolean(document.querySelector('#smartHistoryLightbox.open'));
    const detailSelectedOutputId = String(smartHistoryDetailSession.outputId || '');
    const detailOutput = detailSelectedOutputId
        ? smartHistoryDetailSession.outputs.find(output => String(output.output_id || '') === detailSelectedOutputId)
        : smartHistoryDetailSession.outputs.find(output => output.outputIndex === smartHistoryDetailSession.outputIndex);
    const detailPosition = detailSelectedOutputId
        ? smartHistoryDetailSession.outputs.findIndex(output => String(output.output_id || '') === detailSelectedOutputId)
        : smartHistoryDetailSession.outputs.findIndex(output => output.outputIndex === smartHistoryDetailSession.outputIndex);
    const selectedNode = canvasState.nodes.find(node => String(node?.id || '') === String(canvasState.selectedImage.nodeId || ''));
    const selectedCanvasImages = Array.isArray(selectedNode?.images) ? selectedNode.images : [];
    const canvasSelectedOutputId = String(canvasState.selectedImage.outputId || '');
    const canvasPosition = canvasSelectedOutputId
        ? selectedCanvasImages.findIndex(image => String(image?.output_id || '') === canvasSelectedOutputId)
        : Number(canvasState.selectedImage.index);
    const selectedCanvasItem = selectedCanvasImages[canvasPosition];
    return {
        deletionPosition:deletionPosition >= 0 ? deletionPosition : Number(outputIndex),
        historySelectedOutputId:String(historyOutput?.output_id || ''),
        detailOpen,
        detailHistoryId:String(smartHistoryDetailSession.historyId || ''),
        detailPosition,
        detailSelectedOutputId:String(detailOutput?.output_id || ''),
        canvasNodeId:String(selectedNode?.id || ''),
        canvasPosition:canvasPosition >= 0 ? canvasPosition : Number(canvasState.selectedImage.index),
        canvasSelectedOutputId:String(selectedCanvasItem?.output_id || ''),
    };
}

function reconcileSmartOriginalDeletionUi(historyId, deletedOutputId, state){
    const outputs = smartHistoryImageOutputs(smartHistoryEntryById(historyId));
    if(canvasState.smartHistorySelectedImage?.historyId === String(historyId || '') || state.historySelectedOutputId){
        const nextHistory = smartOutputAtPreservedPosition(
            outputs,
            state.historySelectedOutputId === String(deletedOutputId || '') ? '' : state.historySelectedOutputId,
            state.deletionPosition,
        );
        canvasState.smartHistorySelectedImage = nextHistory
            ? {
                historyId:String(historyId || ''),
                outputIndex:nextHistory.outputIndex,
                outputId:String(nextHistory.output_id || ''),
            }
            : null;
    }
    if(state.canvasNodeId){
        const selectedNode = canvasState.nodes.find(node => String(node?.id || '') === state.canvasNodeId);
        const images = Array.isArray(selectedNode?.images) ? selectedNode.images : [];
        const preservedIndex = images.findIndex(image =>
            String(image?.output_id || '') === state.canvasSelectedOutputId
            && state.canvasSelectedOutputId !== String(deletedOutputId || '')
        );
        const nextIndex = preservedIndex >= 0
            ? preservedIndex
            : Math.max(0, Math.min(images.length - 1, state.canvasPosition));
        canvasState.selectedImage = images.length
            ? {
                nodeId:state.canvasNodeId,
                index:nextIndex,
                outputId:String(images[nextIndex]?.output_id || ''),
            }
            : {nodeId:'', index:-1, outputId:''};
    }
    if(state.detailOpen && state.detailHistoryId === String(historyId || '')){
        const box = document.querySelector('#smartHistoryLightbox.open');
        if(!box || !outputs.length){
            closeSmartHistoryLightbox();
        } else {
            const nextDetail = smartOutputAtPreservedPosition(
                outputs,
                state.detailSelectedOutputId === String(deletedOutputId || '') ? '' : state.detailSelectedOutputId,
                state.detailPosition,
            );
            smartHistoryDetailSession = {
                historyId:String(historyId || ''),
                outputs,
                outputIndex:nextDetail.outputIndex,
                outputId:String(nextDetail.output_id || ''),
            };
            renderSmartHistoryDetailOutputs(box);
            selectSmartHistoryDetailOutput(nextDetail.outputIndex);
            canvasCore.refreshIcons(box);
        }
    }
}

function smartHistoryIdForDeletedOutput(assetId, deletionId=''){
    const normalizedAssetId = String(assetId || '');
    const normalizedDeletionId = String(deletionId || '');
    const log = (canvas?.history || []).find(item => (item?.deletedOutputTombstones || []).some(tombstone =>
        normalizedDeletionId
            ? String(tombstone?.deletion_id || '') === normalizedDeletionId
            : String(tombstone?.deleted_asset_id || tombstone?.asset_id || '') === normalizedAssetId
    ));
    return String(log?.id || '');
}

function smartHistoryLogCardHtml(log, historyIndex){
        const outputs = (log.outputs || []).map((output, outputIndex) => {
            const item = smartHistoryOutputItem(output);
            return item?.url ? {...item, outputIndex} : null;
        }).filter(Boolean);
        const first = outputs[0] || null;
        const date = new Date(smartHistoryLogTimestamp(log) || Date.now()).toLocaleString(window.StudioI18n?.lang() === 'en' ? 'en-US' : 'zh-CN');
        const req = log.request || {};
        const prompt = String(log.prompt || '').trim();
        const taskId = log.task_id || log.taskId || req.task_id || req.taskId || req.prompt_id || req.promptId || '';
        const backend = req.provider_id || req.providerId || req.backend || '';
        const projectTask = String(taskId || '').startsWith('project_img_') || String(taskId || '').startsWith('project_vid_');
        const sizeSummary = smartHistorySizeSummary(log, outputs);
        const resolutionTier = smartHistoryResolutionTier(log, outputs);
        const imageOutputCount = smartHistoryImageOutputs(log).length;
        const modelMeta = historyLogModelMeta(log);
        const subParts = [
            date,
            tr('canvas.historyOutputsCount').replace('{n}', String(outputs.length)),
            sizeSummary,
            taskId ? `ID ${taskId}` : '',
            backend
        ].filter(Boolean);
        const originalDeleted = log.originalDeleted || log.status === 'original_deleted';
        const deletedOriginalTombstone = (log.deletedOutputTombstones || [])
            .find(item => item?.deleted_asset_id || item?.asset_id) || null;
        const deletedOriginalAssetId = deletedOriginalTombstone?.deleted_asset_id
            || deletedOriginalTombstone?.asset_id
            || '';
        const upstreamDeleted = Array.isArray(log.deletedReferenceTombstones) && log.deletedReferenceTombstones.length > 0;
        const failed = log.status === 'failed';
        const cancelled = log.status === 'cancelled';
        const pending = log.status === 'pending';
        let thumbHtml = '';
        if(originalDeleted){
            thumbHtml = `<div class="log-thumb-square log-thumb-deleted"><div class="log-deleted-media"><i data-lucide="image-off"></i><span>${escapeHtml(tr('canvas.originalDeleted'))}</span></div></div>`;
        } else if(first){
            const outputCards = outputs.map(item => {
                const outputIndex = item.outputIndex;
                const safe = escapeAttr(item.url);
                const kind = item.kind || (outputUrlLooksVideo(item.url) ? 'video' : 'image');
                const label = imageResolutionLabel(item);
                const name = item.name || smartImageNameFromUrl(item.url);
                const selected = canvasState.smartHistorySelectedImage?.historyId === String(log.id || '')
                    && canvasState.smartHistorySelectedImage.outputIndex === outputIndex;
                const imageAriaLabel = [tr('canvas.originalImage'), outputIndex + 1, label, tr('canvas.dragImageHint')].filter(Boolean).join(' · ');
                const attrs = kind === 'image'
                    ? `class="log-history-image" role="button" tabindex="0" aria-pressed="${selected ? 'true' : 'false'}" aria-label="${escapeAttr(imageAriaLabel)}" title="${escapeAttr(label || tr('canvas.originalImage'))}" alt="${escapeAttr(imageAriaLabel)}" draggable="true" loading="lazy" decoding="async"`
                    : `title="${escapeAttr(label || tr('canvas.originalImage'))}" alt="output" draggable="false"`;
                const preview = kind === 'video' ? smartVideoPreviewHtml(item, 512, attrs) : smartPreviewImgHtml(item, 512, attrs);
                const actionsHtml = item.asset_id && canvas?.format === 'firefly-canvas-v1'
                    ? `<button type="button" class="log-media-download-original" data-history-download-original="${outputIndex}" title="${escapeAttr(tr('smart.downloadOriginal'))}" aria-label="${escapeAttr(tr('smart.downloadOriginal'))}"><i data-lucide="download"></i><span>${escapeHtml(tr('smart.downloadOriginal'))}</span></button><button type="button" class="log-media-mark image-mark${getMarkedAssetIds().includes(String(item.asset_id)) ? ' image-marked' : ''}" data-history-mark="${escapeAttr(String(item.asset_id))}" data-marked-asset="${escapeAttr(String(item.asset_id))}" data-marked-title="1" title="${escapeAttr(tr(getMarkedAssetIds().includes(String(item.asset_id)) ? 'smart.unmarkImage' : 'smart.markImage'))}"><i data-lucide="star"></i></button>`
                    : '';
                return `<article class="log-media-card ${kind === 'image' ? 'is-image' : ''}${selected ? ' is-selected' : ''}" draggable="false" data-history-output-index="${outputIndex}" data-output-id="${escapeAttr(item.output_id || '')}" data-url="${safe}" data-asset-id="${escapeAttr(item.asset_id || '')}" data-kind="${escapeAttr(kind)}" data-name="${escapeAttr(name)}"${selected ? ' data-history-selected="true"' : ''}>${kind === 'video' ? `<button class="log-media-preview" type="button" data-history-preview title="${escapeAttr(label || tr('canvas.originalImage'))}">${preview}</button>` : preview}${actionsHtml}</article>`;
            }).join('');
            const outputPreviews = outputs.length > 1 ? outputs.map((item, index) => {
                const position = smartHistoryDetailPositionLabel(index + 1, outputs.length);
                const preview = smartPreviewImgHtml(item, 192, `alt="${escapeAttr(position)}" draggable="false"`);
                const previewSelected = canvasState.smartHistorySelectedImage?.historyId === String(log.id || '')
                    && canvasState.smartHistorySelectedImage.outputIndex === item.outputIndex;
                const previewHint = `${position} · ${tr('smart.generationOutputPreviewHint')}`;
                return `<button class="log-output-preview${previewSelected ? ' is-selected' : ''}" type="button" data-history-output-preview="${item.outputIndex}" data-output-id="${escapeAttr(item.output_id || '')}" data-asset-id="${escapeAttr(item.asset_id || '')}" aria-current="${previewSelected ? 'true' : 'false'}" aria-label="${escapeAttr(previewHint)}" title="${escapeAttr(previewHint)}">${preview}</button>`;
            }).join('') : '';
            const previewCount = Math.min(outputs.length, 8);
            const previewColumns = previewCount;
            const previewRows = 1;
            const previewTrackHeight = 25;
            const previewLayout = `--log-output-count:${previewCount};--log-output-columns:${previewColumns};--log-output-rows:${previewRows};--log-output-track-height:${previewTrackHeight}px`;
            thumbHtml = `<div class="log-thumb-square" style="${previewLayout}"><div class="log-output-gallery" role="group" aria-label="${escapeAttr(tr('canvas.originalImage'))}">${outputCards}</div>
                ${outputPreviews ? `<div class="log-output-preview-list" role="group" aria-label="${escapeAttr(tr('smart.generationOutputs'))}">${outputPreviews}</div>` : ''}
                ${outputs.length > 1 ? `<span class="log-count-badge">×${outputs.length}</span>` : ''}
            </div>`;
        } else if(pending){
            thumbHtml = `<div class="log-thumb-square"><div class="log-thumb-placeholder log-history-placeholder" role="status" aria-label="${escapeAttr(tr('canvas.generating'))}"><span class="log-history-placeholder-spinner" aria-hidden="true"></span>${generationStageProgressHtml(log.progress || {})}</div></div>`;
        } else if(failed || cancelled){
            const salvageableOutputs = failed && !cancelled && outputs.length ? outputs.slice(0, 1) : [];
            if(salvageableOutputs.length){
                const salvageItem = salvageableOutputs[0];
                const salvageLabel = imageResolutionLabel(salvageItem);
                const salvagePreview = smartPreviewImgHtml(salvageItem, 512, `alt="${escapeAttr(salvageLabel || tr('canvas.originalImage'))}" draggable="false"`);
                thumbHtml = `<div class="log-thumb-square log-thumb-partial-failed"><div class="log-output-gallery" role="group" aria-label="${escapeAttr(tr('canvas.originalImage'))}">${salvagePreview}</div>${outputs.length > 1 ? `<span class="log-count-badge">×${outputs.length}</span>` : ''}<div class="log-failed-flag"><i data-lucide="triangle-alert"></i><span>${escapeHtml(tr('canvas.failed'))}</span></div></div>`;
            } else {
            thumbHtml = `<div class="log-thumb-square"><div class="log-thumb-placeholder log-history-failed-placeholder"><i data-lucide="${cancelled ? 'circle-x' : 'image-off'}"></i>${log.progress ? generationStageProgressHtml({...log.progress, failed}) : ''}</div></div>`;
            }
        }
        const promptPreview = prompt
            ? `<button class="log-prompt-preview long-content-trigger" type="button" data-long-content-open="history-prompt" aria-haspopup="dialog" title="${escapeAttr(tr('smart.historyPromptFull'))}" aria-label="${escapeAttr(tr('smart.historyPromptFull'))}"><span class="log-prompt-label">${escapeHtml(tr('canvas.prompt'))}</span><span class="log-prompt-text">${escapeHtml(prompt)}</span><i data-lucide="book-open-text"></i></button>`
            : `<div class="log-prompt-preview is-empty">${escapeHtml(tr('canvas.noPromptMeta'))}</div>`;
        // 只要记录上仍存活原图就保留「彻底删除记录和原图」按钮：单张原图删除（occurrence 删除）
        // 会给记录写入 deletedOutputTombstones 并出现「恢复原图」按钮，此时不能连带隐藏彻底删除
        // （否则剩余原图无法从该卡片删除）；仅当全部原图已删（outputs 为空）时才隐藏。
        const destructiveRecordDelete = !(log.deletedOutputTombstones || []).length || outputs.length > 0;
        const deleteOriginalLabel = outputs.length > 1
            ? tr('canvas.deleteSelectedOriginal')
            : tr('canvas.deleteOriginal');
        return `<div class="log-item ${failed ? 'failed' : cancelled ? 'cancelled' : pending ? 'pending' : ''}" data-canvas-history-id="${escapeAttr(log.id || '')}" data-history-index="${historyIndex}">
            ${thumbHtml}
            <div class="log-prompt-data" hidden data-prompt="${escapeAttr(log.prompt || '')}"></div>
            <div class="log-main">
                <div class="log-meta">
                    <span class="log-chip ${failed ? 'status-failed' : cancelled ? 'status-deleted' : pending ? 'status-pending' : originalDeleted ? 'status-deleted' : 'status-ok'}">${escapeHtml(failed ? tr('canvas.failed') : cancelled ? tr('canvas.historyCancelled') : pending ? tr('canvas.generating') : originalDeleted ? tr('canvas.originalDeleted') : tr('canvas.success'))}</span>
                    <span class="log-chip">${escapeHtml(log.platform || log.actual_provider_name || '-')}</span>
                    ${modelMeta.label ? `<span class="log-chip model-chip" title="${escapeAttr(modelMeta.raw)}">${escapeHtml(modelMeta.label)}</span>` : ''}
                    ${resolutionTier ? `<span class="log-chip resolution-chip">${escapeHtml(resolutionTier)}</span>` : ''}
                 </div>
                 <div class="log-subline">${subParts.map(part => `<span title="${escapeAttr(part)}">${escapeHtml(part)}</span>`).join('')}</div>
                 ${promptPreview}
                 ${upstreamDeleted ? `<div class="log-reference-deleted" role="status"><i data-lucide="unlink"></i><span>${escapeHtml(tr('canvas.upstreamMediaDeleted'))}</span></div>` : ''}
                 ${log.error ? `<button class="log-error long-content-trigger" type="button" data-long-content-open="history-error" aria-haspopup="dialog" title="${escapeAttr(tr('smart.generationErrorFull'))}" aria-label="${escapeAttr(tr('smart.generationErrorFull'))}"><span>${escapeHtml(log.error)}</span><i data-lucide="book-open-text"></i></button>` : ''}
             </div>
             <div class="log-buttons">
                  ${pending && projectTask ? `<button type="button" data-history-cancel-task="${escapeAttr(taskId)}" title="${escapeAttr(tr('canvas.historyCancelTaskTitle'))}" aria-label="${escapeAttr(tr('canvas.historyCancelTaskTitle'))}"><i data-lucide="square"></i><span>${escapeHtml(tr('canvas.historyCancelTask'))}</span></button>` : ''}
                  ${failed && projectTask ? `<button type="button" data-history-retry-task="${escapeAttr(taskId)}" title="${escapeAttr(tr('canvas.historyRetryTaskTitle'))}" aria-label="${escapeAttr(tr('canvas.historyRetryTaskTitle'))}"><i data-lucide="rotate-cw"></i><span>${escapeHtml(tr('canvas.historyRetryTask'))}</span></button>` : ''}
                  ${!pending && (imageOutputCount || (failed && log.kind !== 'video')) ? `<button type="button" data-history-send-all title="${escapeAttr(tr('canvas.sendToCanvas'))}" aria-label="${escapeAttr(tr('canvas.sendToCanvas'))}"><i data-lucide="send"></i><span>${escapeHtml(tr('canvas.sendToCanvas'))}</span></button>` : ''}
                  ${!pending && !originalDeleted && first?.asset_id && canvas?.format === 'firefly-canvas-v1' ? `<button type="button" class="danger" data-history-delete-original="${String(first.output_id || '').trim()}" data-history-delete-original-index="${first.outputIndex}" title="${escapeAttr(deleteOriginalLabel)}" aria-label="${escapeAttr(deleteOriginalLabel)}"><i data-lucide="image-minus"></i><span>${escapeHtml(deleteOriginalLabel)}</span></button>` : ''}
                  ${!pending && deletedOriginalAssetId ? `<button type="button" data-history-restore-original="${escapeAttr(deletedOriginalAssetId)}" data-deletion-id="${escapeAttr(deletedOriginalTombstone?.deletion_id || '')}" data-output-id="${escapeAttr(deletedOriginalTombstone?.deleted_output_id || '')}" title="${escapeAttr(tr('canvas.restoreOriginal'))}" aria-label="${escapeAttr(tr('canvas.restoreOriginal'))}"><i data-lucide="rotate-ccw"></i><span>${escapeHtml(tr('canvas.restoreOriginal'))}</span></button>` : ''}
                  ${!pending ? `<button type="button" data-history-copy-prompt ${log.prompt ? '' : 'disabled'} title="${escapeAttr(tr('canvas.copyPrompt'))}" aria-label="${escapeAttr(tr('canvas.copyPrompt'))}"><i data-lucide="copy"></i><span>${escapeHtml(tr('canvas.copyPrompt'))}</span></button>
                  <button type="button" data-history-delete="record" title="${escapeAttr(tr('canvas.deleteLog'))}" aria-label="${escapeAttr(tr('canvas.deleteLog'))}"><i data-lucide="trash-2"></i><span>${escapeHtml(tr('canvas.deleteLog'))}</span></button>
                  ${destructiveRecordDelete ? `<button type="button" class="danger" data-history-delete="media" title="${escapeAttr(tr('canvas.deleteLogAndMedia'))}" aria-label="${escapeAttr(tr('canvas.deleteLogAndMedia'))}"><i data-lucide="trash-2"></i><span>${escapeHtml(tr('canvas.deleteLogAndMedia'))}</span></button>` : ''}
                  <span class="log-duration">${escapeHtml(formatRunDuration(smartHistoryRunMs(log)))}</span>` : ''}
              </div>
         </div>`;
}

function smartHistoryFocusedCard(logs){
    const activeCard = document.activeElement?.closest?.('.log-item[data-canvas-history-id]');
    const card = smartHistoryList?.contains(activeCard)
        ? activeCard
        : (smartHistoryVirtualState.focusCard?.isConnected ? smartHistoryVirtualState.focusCard : null);
    if(!card || !smartHistoryList?.contains(card)) return {card:null, index:-1, historyId:''};
    const historyId = card.dataset.canvasHistoryId || '';
    return {card, index:logs.findIndex(log => String(log?.id || '') === historyId), historyId};
}

function protectSmartHistoryFocus(){
    const active = document.activeElement;
    const card = active?.closest?.('.log-item[data-canvas-history-id]');
    if(card && smartHistoryList?.contains(card)){
        smartHistoryVirtualState.focusCard = card;
        smartHistoryVirtualState.focusElement = active;
    } else {
        smartHistoryVirtualState.focusCard = null;
        smartHistoryVirtualState.focusElement = null;
    }
}

function smartHistoryVisibleRange(count){
    const stride = smartHistoryVirtualState.stride;
    if(!stride) return {start:0, end:Math.min(count, 1)};
    const left = Math.max(0, Number(smartHistoryList?.scrollLeft || 0));
    const width = Math.max(1, Number(smartHistoryList?.clientWidth || stride));
    const start = Math.max(0, Math.min(count - 1, Math.floor(left / stride)));
    const end = Math.max(start + 1, Math.min(count, Math.ceil((left + width) / stride)));
    return {start, end};
}

function smartHistoryWindowBounds(count, targetCount){
    const target = Math.max(1, Math.min(count, targetCount));
    const visible = smartHistoryVisibleRange(count);
    const currentSize = smartHistoryVirtualState.end - smartHistoryVirtualState.start;
    if(smartHistoryVirtualState.count === count && currentSize === target){
        const leftBuffered = smartHistoryVirtualState.start === 0
            || visible.start - smartHistoryVirtualState.start >= SMART_HISTORY_RENDER_BUFFER;
        const rightBuffered = smartHistoryVirtualState.end === count
            || smartHistoryVirtualState.end - visible.end >= SMART_HISTORY_RENDER_BUFFER;
        if(leftBuffered && rightBuffered) return {start:smartHistoryVirtualState.start, end:smartHistoryVirtualState.end};
    }
    let start = Math.max(0, visible.start - SMART_HISTORY_OVERSCAN);
    start = Math.min(start, Math.max(0, count - target));
    if(visible.end > start + target) start = Math.min(count - target, visible.end - target);
    return {start, end:start + target};
}

function smartHistorySpacerHtml(position, omitted){
    return `<div data-history-spacer="${position}" data-history-omitted="${omitted}" aria-hidden="true"></div>`;
}

function smartHistoryRailHtml(logs, indexes){
    let html = smartHistorySpacerHtml('before', indexes[0]);
    let previous = -1;
    indexes.forEach(index => {
        if(previous >= 0 && index > previous + 1){
            html += smartHistorySpacerHtml('gap', index - previous - 1);
        }
        html += smartHistoryLogCardHtml(logs[index], index);
        previous = index;
    });
    html += smartHistorySpacerHtml('after', logs.length - previous - 1);
    return html;
}

function replaceSmartHistoryRail(html, focused, preserveFocusedCard=true){
    const savedScrollLeft = smartHistoryList.scrollLeft;
    if(!preserveFocusedCard || !focused.card || focused.index < 0){
        smartHistoryList.innerHTML = html;
        return savedScrollLeft;
    }
    const template = document.createElement('template');
    template.innerHTML = html;
    const desiredNodes = [...template.content.children];
    const focusPosition = desiredNodes.findIndex(node => node.dataset.canvasHistoryId === focused.historyId);
    if(focusPosition < 0){
        smartHistoryList.replaceChildren(template.content);
        return savedScrollLeft;
    }
    const generatedFocusedCard = desiredNodes[focusPosition];
    focused.card.dataset.historyIndex = generatedFocusedCard.dataset.historyIndex || String(focused.index);
    [...smartHistoryList.childNodes].forEach(node => {
        if(node !== focused.card) node.remove();
    });
    const before = document.createDocumentFragment();
    desiredNodes.slice(0, focusPosition).forEach(node => before.appendChild(node));
    smartHistoryList.insertBefore(before, focused.card);
    desiredNodes.slice(focusPosition + 1).forEach(node => smartHistoryList.appendChild(node));
    return savedScrollLeft;
}

function updateSmartHistorySpacerWidths(){
    const card = smartHistoryList.querySelector('.log-item');
    if(card){
        const cardWidth = Number(card.getBoundingClientRect().width || card.offsetWidth || 0);
        const listStyle = getComputedStyle(smartHistoryList);
        const gap = Number.parseFloat(listStyle.columnGap || listStyle.gap || '0') || 0;
        if(cardWidth > 0){
            smartHistoryVirtualState.gap = gap;
            smartHistoryVirtualState.stride = cardWidth + gap;
        }
    }
    const stride = smartHistoryVirtualState.stride;
    const gap = smartHistoryVirtualState.gap;
    smartHistoryList.querySelectorAll('[data-history-spacer]').forEach(spacer => {
        const omitted = Math.max(0, Number(spacer.dataset.historyOmitted || 0));
        spacer.hidden = omitted === 0;
        const width = omitted && stride ? Math.max(0, omitted * stride - gap) : 0;
        spacer.style.flex = `0 0 ${width}px`;
        spacer.style.width = `${width}px`;
        spacer.style.minWidth = `${width}px`;
        spacer.style.height = '1px';
        spacer.style.pointerEvents = 'none';
    });
}

function bindLongContentHistoryTriggers(){
    smartHistoryList?.querySelectorAll?.('[data-long-content-open="history-prompt"], [data-long-content-open="history-error"]').forEach(button => {
        const activate = event => {
            event.preventDefault();
            event.stopPropagation();
            const historyId = button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            const log = smartHistoryEntryById(historyId);
            const kind = button.dataset.longContentOpen;
            const isError = kind === 'history-error';
            const text = String(isError ? log?.error || '' : log?.prompt || '');
            if(!text) return;
            openLongContentReader({
                title:tr(isError ? 'smart.generationErrorFull' : 'smart.historyPromptFull'),
                text,
                trigger:button,
                triggerIdentity:{kind, historyId}
            });
        };
        button.onclick = activate;
        button.onkeydown = event => {
            if(event.key === 'Enter' || event.key === ' ') activate(event);
        };
    });
}

function bindSmartHistoryWindow(){
    bindSmartPreviewImageFallbacks(smartHistoryList);
    bindLongContentHistoryTriggers();
    smartHistoryList.querySelectorAll('[data-history-preview]').forEach(el => {
        el.onclick = e => {
            e.stopPropagation();
            const card = el.closest('.log-media-card');
            const url = el.dataset.url || card?.dataset.url || '';
            const kind = el.dataset.kind || card?.dataset.kind || 'image';
            const historyId = el.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            const outputIndex = Number(el.dataset.historyOutputIndex ?? card?.dataset.historyOutputIndex ?? 0);
            if(url) smartHistoryPreviewNode(url, kind, {historyId, outputIndex});
        };
    });
    const bindLogCopy = (selector, key) => {
        smartHistoryList.querySelectorAll(selector).forEach(el => {
            el.onclick = async e => {
                e.stopPropagation();
                const text = el.dataset[key] || '';
                const copied = await copyTextToClipboard(text);
                const oldText = el.textContent;
                el.textContent = copied ? tr('canvas.copied') : tr('canvas.copyFailed');
                if(copied) el.classList.add('copied');
                setTimeout(() => {
                    el.textContent = oldText;
                    el.classList.remove('copied');
                }, 900);
            };
        });
    };
    bindLogCopy('[data-prompt]', 'prompt');
    bindLogCopy('[data-error]', 'error');
    smartHistoryList.querySelectorAll('[data-history-copy-prompt]').forEach(button => {
        button.onclick = async e => {
            e.stopPropagation();
            const text = button.closest('[data-canvas-history-id]')?.querySelector('[data-prompt]')?.dataset.prompt || '';
            const copied = await copyTextToClipboard(text);
            const label = button.querySelector('span');
            const oldText = label?.textContent || tr('canvas.copyPrompt');
            if(label) label.textContent = copied ? tr('canvas.copied') : tr('canvas.copyFailed');
            button.classList.toggle('copied', copied);
            setTimeout(() => {
                if(label) label.textContent = oldText;
                button.classList.remove('copied');
            }, 1200);
        };
    });
    smartHistoryList.querySelectorAll('[data-history-send-output]').forEach(button => {
        button.onclick = e => {
            e.stopPropagation();
            const historyId = button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            sendSmartHistoryOutputsToCanvas(historyId, Number(button.dataset.historySendOutput));
        };
    });
    smartHistoryList.querySelectorAll('[data-history-send-all]').forEach(button => {
        button.onclick = e => {
            e.stopPropagation();
            sendSmartHistoryOutputsToCanvas(button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '');
        };
    });
    smartHistoryList.querySelectorAll('[data-history-output-preview]').forEach(button => {
        button.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const card = button.closest('[data-canvas-history-id]');
            const historyId = card?.dataset.canvasHistoryId || '';
            const outputIndex = Number(button.dataset.historyOutputPreview || 0);
            const item = smartHistoryImageOutputs(smartHistoryEntryById(historyId)).find(output => output.outputIndex === outputIndex);
            const gallery = card?.querySelector('.log-output-gallery');
            const outputCard = card?.querySelector(`[data-history-output-index="${outputIndex}"]`);
            if(gallery && outputCard) gallery.scrollTo({left:outputCard.offsetLeft, behavior:'instant'});
            selectSmartHistoryImage(outputCard);
        };
        button.ondblclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const historyId = button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            const outputIndex = Number(button.dataset.historyOutputPreview || 0);
            const item = smartHistoryImageOutputs(smartHistoryEntryById(historyId)).find(output => output.outputIndex === outputIndex);
            if(item?.url) openSmartHistoryDetail({url:item.url, kind:'image', historyId, outputIndex});
        };
        button.onkeydown = event => {
            if(event.key !== ' ' && event.key !== 'Spacebar') return;
            event.preventDefault();
            event.stopPropagation();
            const historyId = button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            const outputIndex = Number(button.dataset.historyOutputPreview || 0);
            const item = smartHistoryImageOutputs(smartHistoryEntryById(historyId)).find(output => output.outputIndex === outputIndex);
            if(item?.url) openSmartHistoryDetail({url:item.url, kind:'image', historyId, outputIndex});
        };
    });
    smartHistoryList.querySelectorAll('.log-output-gallery').forEach(gallery => {
        gallery.addEventListener('wheel', event => {
            const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
            if(!delta) return;
            const before = gallery.scrollLeft;
            gallery.scrollLeft += delta;
            if(Math.abs(gallery.scrollLeft - before) > 0.5){
                event.preventDefault();
                event.stopPropagation();
            }
        }, {passive:false});
    });
    smartHistoryList.querySelectorAll('.log-output-preview-list').forEach(track => {
        track.addEventListener('wheel', event => {
            const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
            if(!delta) return;
            const before = track.scrollLeft;
            track.scrollLeft += delta;
            if(Math.abs(track.scrollLeft - before) > 0.5){
                event.preventDefault();
                event.stopPropagation();
            }
        }, {passive:false});
    });
    smartHistoryList.querySelectorAll('.log-history-image').forEach(image => {
        const card = image.closest('.log-media-card');
        const historyId = card?.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
        bindSmartHistoryImage(image, smartHistoryOutputItem({
            ...((smartHistoryEntryById(historyId)?.outputs || [])[Number(card?.dataset.historyOutputIndex)] || {}),
            url:card?.dataset.url || ''
        }), historyId, Number(card?.dataset.historyOutputIndex));
    });
    smartHistoryList.querySelectorAll('[data-history-cancel-task]').forEach(button => {
        button.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const historyId = button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            void controlProjectImageTask(historyId, button.dataset.historyCancelTask || '', 'cancel', button);
        };
    });
    smartHistoryList.querySelectorAll('[data-history-retry-task]').forEach(button => {
        button.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const historyId = button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            const retryTaskId = button.dataset.historyRetryTask || '';
            if(String(retryTaskId).startsWith('project_vid_')){
                // 视频暂无服务端重试路由：走节点级重新生成（重建提交）。
                const nodeId = smartHistoryEntryById(historyId)?.result_node_id || '';
                void retryFailedGeneration(nodeId);
                return;
            }
            void controlProjectImageTask(historyId, retryTaskId, 'retry', button);
        };
    });
    smartHistoryList.querySelectorAll('[data-history-download-original]').forEach(button => {
        button.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const historyId = button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            const output = (smartHistoryEntryById(historyId)?.outputs || [])[Number(button.dataset.historyDownloadOriginal)];
            const item = smartHistoryOutputItem(output);
            if(item?.url) canvasCore.downloadPreviewFile(item);
        };
    });
    // #89 第四轮：生图历史卡片上的星标。
    smartHistoryList.querySelectorAll('[data-history-mark]').forEach(button => {
        button.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const assetId = String(button.dataset.historyMark || '');
            if(!assetId) return;
            setMarkedImage(assetId, !getMarkedAssetIds().includes(assetId));
        };
    });
    smartHistoryList.querySelectorAll('[data-history-delete-original]').forEach(button => {
        button.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const card = button.closest('[data-canvas-history-id]');
            const historyId = card?.dataset.canvasHistoryId || '';
            const log = smartHistoryEntryById(historyId);
            const images = smartHistoryImageOutputs(log);
            // Bug fix: 删除当前选中的图片（通过 output_id 匹配），否则删除第一张
            const selectedIndex = (() => {
                if(canvasState.smartHistorySelectedImage?.historyId === historyId && canvasState.smartHistorySelectedImage?.outputId){
                    const matched = images.find(img => String(img.output_id || '') === canvasState.smartHistorySelectedImage.outputId);
                    if(matched) return matched.outputIndex;
                }
                return images[0]?.outputIndex ?? 0;
            })();
            deleteSmartProjectOriginal(historyId, selectedIndex);
        };
    });
    smartHistoryList.querySelectorAll('[data-history-restore-original]').forEach(button => {
        button.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const historyId = button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            restoreSmartProjectOriginal(
                button.dataset.historyRestoreOriginal || '',
                button.dataset.deletionId || '',
                button.dataset.outputId || '',
                historyId,
            );
        };
    });
    smartHistoryList.querySelectorAll('[data-history-delete]').forEach(button => {
        button.onclick = e => {
            e.stopPropagation();
            const historyId = button.closest('[data-canvas-history-id]')?.dataset.canvasHistoryId || '';
            if(!historyId) return;
            button.disabled = true;
            button.setAttribute('aria-busy','true');
            deleteCanvasLogEntry(historyId, button.dataset.historyDelete === 'media').finally(() => {
                button.disabled = false;
                button.removeAttribute('aria-busy');
            });
        };
    });
    smartHistoryList.querySelectorAll('button').forEach(button => {
        button.onmousedown = event => {
            if(event.button !== 0) return;
            const state = {
                image:null,
                card:button.closest('.log-item'),
                pointerId:1,
                actionButton:button,
            };
            canvasState.smartHistoryPointerState = state;
            window.addEventListener('mouseup', () => {
                setTimeout(() => {
                    if(canvasState.smartHistoryPointerState === state) clearSmartHistoryPointerState();
                }, 0);
            }, {once:true});
        };
    });
    canvasCore.refreshIcons();
}

function renderSmartHistoryWindow(force=false){
    if(!smartHistoryList) return;
    if(canvasState.smartHistoryPointerState){
        smartHistoryVirtualState.force = smartHistoryVirtualState.force || force;
        return;
    }
    const logs = canvas?.history || [];
    if(!logs.length){
        smartHistoryList.innerHTML = `<div class="log-empty">${escapeHtml(tr('canvas.noLogs'))}</div>`;
        smartHistoryVirtualState.start = 0;
        smartHistoryVirtualState.end = 0;
        smartHistoryVirtualState.count = 0;
        smartHistoryVirtualState.signature = 'empty';
        smartHistoryVirtualState.focusCard = null;
        smartHistoryVirtualState.focusElement = null;
        canvasCore.refreshIcons();
        return;
    }
    if(canvasState.smartHistorySelectedImage && !logs.some(log => String(log?.id || '') === canvasState.smartHistorySelectedImage.historyId)){
        canvasState.smartHistorySelectedImage = null;
    }
    const focused = smartHistoryFocusedCard(logs);
    let bounds = smartHistoryWindowBounds(logs.length, SMART_HISTORY_DOM_LIMIT);
    const focusOutside = focused.index >= 0 && (focused.index < bounds.start || focused.index >= bounds.end);
    if(focusOutside && logs.length > 1){
        bounds = smartHistoryWindowBounds(logs.length, SMART_HISTORY_DOM_LIMIT - 1);
    }
    const indexes = [];
    for(let index = bounds.start; index < bounds.end; index++) indexes.push(index);
    if(focused.index >= 0 && !indexes.includes(focused.index)) indexes.push(focused.index);
    indexes.sort((a, b) => a - b);
    const pinnedIndex = focused.index >= 0 && (focused.index < bounds.start || focused.index >= bounds.end)
        ? focused.index
        : -1;
    const signature = `${logs.length}:${bounds.start}:${bounds.end}:${pinnedIndex}`;
    if(!force && signature === smartHistoryVirtualState.signature){
        smartHistoryVirtualState.focusCard = null;
        smartHistoryVirtualState.focusElement = null;
        return;
    }
    const protectedFocusElement = focused.card?.contains(document.activeElement)
        ? document.activeElement
        : smartHistoryVirtualState.focusElement;
    const savedScrollLeft = replaceSmartHistoryRail(smartHistoryRailHtml(logs, indexes), focused, !force);
    smartHistoryVirtualState.start = bounds.start;
    smartHistoryVirtualState.end = bounds.end;
    smartHistoryVirtualState.count = logs.length;
    smartHistoryVirtualState.signature = signature;
    updateSmartHistorySpacerWidths();
    smartHistoryList.scrollLeft = Math.min(savedScrollLeft, Math.max(0, smartHistoryList.scrollWidth - smartHistoryList.clientWidth));
    bindSmartHistoryWindow();
    if(protectedFocusElement?.isConnected && document.activeElement !== protectedFocusElement){
        protectedFocusElement.focus({preventScroll:true});
    }
    smartHistoryVirtualState.focusCard = null;
    smartHistoryVirtualState.focusElement = null;
}

function renderSmartCanvasHistory(){
    renderSmartHistoryWindow(true);
}

function scheduleSmartHistoryWindowRender(force=false){
    smartHistoryVirtualState.force = smartHistoryVirtualState.force || force;
    if(smartHistoryVirtualState.frame) return;
    smartHistoryVirtualState.frame = requestAnimationFrame(() => {
        smartHistoryVirtualState.frame = 0;
        const shouldForce = smartHistoryVirtualState.force;
        smartHistoryVirtualState.force = false;
        renderSmartHistoryWindow(shouldForce);
    });
}

function updateSmartHistoryDrawerCollapseUi(){
    smartHistoryModal?.classList.toggle('collapsed', canvasState.smartHistoryDrawerCollapsed);
    smartHistoryModal?.setAttribute('aria-expanded', String(!canvasState.smartHistoryDrawerCollapsed && smartHistoryModal.classList.contains('open')));
    if(smartHistoryCollapse){
        smartHistoryCollapse.title = canvasState.smartHistoryDrawerCollapsed ? tr('smart.historyExpand') : tr('smart.historyCollapse');
        smartHistoryCollapse.setAttribute('aria-label', smartHistoryCollapse.title);
        smartHistoryCollapse.innerHTML = `<i data-lucide="${canvasState.smartHistoryDrawerCollapsed ? 'chevron-up' : 'chevron-down'}" class="w-4 h-4"></i>`;
        canvasCore.refreshIcons();
    }
}

function toggleSmartHistoryDrawerCollapsed(){
    if(!smartHistoryModal?.classList.contains('open')) openSmartCanvasLog();
    canvasState.smartHistoryDrawerCollapsed = !canvasState.smartHistoryDrawerCollapsed;
    updateSmartHistoryDrawerCollapseUi();
    if(!canvasState.smartHistoryDrawerCollapsed) scheduleSmartHistoryWindowRender(true);
}

function scrollSmartHistoryRailTo(value, immediate=false){
    if(!smartHistoryList) return false;
    const before = smartHistoryList.scrollLeft;
    const max = Math.max(0, smartHistoryList.scrollWidth - smartHistoryList.clientWidth);
    const next = Math.max(0, Math.min(max, Number(value) || 0));
    if(Math.abs(next - before) < 0.5) return false;
    protectSmartHistoryFocus();
    smartHistoryList.scrollLeft = next;
    if(immediate) renderSmartHistoryWindow();
    else scheduleSmartHistoryWindowRender();
    return Math.abs(smartHistoryList.scrollLeft - before) >= 0.5;
}

function handleSmartHistoryWheel(event){
    event.stopPropagation();
    const unit = event.deltaMode === 1
        ? 40
        : event.deltaMode === 2
            ? Math.max(1, smartHistoryList.clientWidth)
            : 1;
    const delta = (Number(event.deltaX || 0) + Number(event.deltaY || 0)) * unit;
    if(delta && scrollSmartHistoryRailTo(smartHistoryList.scrollLeft + delta)) event.preventDefault();
}

function handleSmartHistoryKeydown(event){
    if(event.altKey || event.ctrlKey || event.metaKey) return;
    const stride = smartHistoryVirtualState.stride || Math.max(1, smartHistoryList.clientWidth / 2);
    let next = smartHistoryList.scrollLeft;
    if(event.key === 'ArrowLeft') next -= stride;
    else if(event.key === 'ArrowRight') next += stride;
    else if(event.key === 'Home') next = 0;
    else if(event.key === 'End') next = smartHistoryList.scrollWidth - smartHistoryList.clientWidth;
    else return;
    event.stopPropagation();
    if(scrollSmartHistoryRailTo(next, event.key === 'Home' || event.key === 'End')) event.preventDefault();
}

function setupSmartHistoryDrawer(){
    if(!smartHistoryModal) return;
    smartHistoryModal.addEventListener('pointerdown', event => event.stopPropagation());
    smartHistoryModal.addEventListener('mousedown', event => event.stopPropagation());
    smartHistoryModal.addEventListener('click', event => event.stopPropagation());
    smartHistoryModal.addEventListener('wheel', event => event.stopPropagation(), {passive:true});
    if(smartHistoryList){
        smartHistoryList.tabIndex = 0;
        smartHistoryList.addEventListener('scroll', () => scheduleSmartHistoryWindowRender(), {passive:true});
        smartHistoryList.addEventListener('wheel', handleSmartHistoryWheel, {passive:false});
        smartHistoryList.addEventListener('keydown', handleSmartHistoryKeydown);
    }
    updateSmartHistoryDrawerCollapseUi();
}

function cleanHistoryImages(images=[]){
    const seen = new Set();
    return nonPreviewOutputImages(images)
        .map(img => canvasCore.stripImageGenerationMeta({...img}))
        .filter(img => {
            const key = `${img.kind || ''}|${img.url || ''}`;
            if(seen.has(key)) return false;
            seen.add(key);
            return true;
        });
}

function demoteHistoryGroupNode(group){
    if(!group) return;
    delete group.historyFor;
    delete group.isHistoryGroup;
    // #91：身份判定用 titleTouched 标志而不是比对中文标题——当前画布节点无用户重命名入口，
    // 候选池标题永远由系统设置，titleTouched 恒缺失（视为未改名）；未来加重命名入口时置 true 即可保留用户标题。
    if(!group.titleTouched){
        const count = (group.images || []).length;
        group.title = count > 1 ? 'Group' : count === 1 ? 'Image' : tr('smart.createImportNode');
    }
    delete group.titleTouched;
}
// #85：候选池摆放位置——生图框右侧列（x = 右缘 + 80，顶边对齐），
// 与现有节点碰撞时同列下移，仍放不下才回退到节点下方（旧版摆放）。
// 新建时池内还没有图，调用方用 estW/estH 估计尺寸参与碰撞检测。

function historyGroupForNode(node){
    if(!node?.id) return null;
    // #85：候选池不再绘制 history 连线（视觉上无连线），关联唯一以 historyFor 字段为准；
    // 级联删除/撤销/保存恢复均依赖 historyFor，不依赖连线。
    return canvasState.nodes.find(n => isHistoryGroupNode(n) && n.historyFor === node.id) || null;
}

function positionHistoryGroupForNode(node, group){
    if(!node || !group) return;
    const r = canvasCore.nodeRect(node);
    const gr = canvasCore.nodeRect(group);
    if(!Number.isFinite(Number(group.x))) group.x = Math.round((Number(node.x) || 0) + Math.max(0, (r.width - gr.width) / 2));
    if(!Number.isFinite(Number(group.y))) group.y = Math.round((Number(node.y) || 0) + r.height + 56);
}

function ensureHistoryGroupForNode(node){
    if(!node?.id) return null;
    let group = historyGroupForNode(node);
    if(!group){
        const r = canvasCore.nodeRect(node);
        // #85：候选池固定摆在生图框右侧列（碰撞时同列下移/回退下方）；
        // 新建时池内还没图，用「至少一行缩略格」估计尺寸参与碰撞检测。
        const estW = Math.max(316, Math.round(r.width || 0));
        const estH = 200;
        const pos = candidatePoolPosition(node, estW, estH);
        group = {
            id:uid('smart'),
            type:NODE_TYPE_IMAGE,
            x:pos.x,
            y:pos.y,
            title:tr('smart.historyGroupTitle'),
            images:[],
            historyFor:node.id,
            isHistoryGroup:true,
            scale:MEDIA_GROUP_DEFAULT_SCALE,
            created_at:Date.now()
        };
        canvasState.nodes.push(group);
    }
    group.type = NODE_TYPE_IMAGE;
    group.title = tr('smart.historyGroupTitle');
    group.isHistoryGroup = true;
    group.historyFor = node.id;
    if(!Number.isFinite(Number(group.scale))) group.scale = MEDIA_GROUP_DEFAULT_SCALE;
    // #85：不再绘制 history 连线（含线上的 X 与端点圆点）；数据关联走 historyFor，
    // 删生图框级联删池、撤销/重做、保存恢复全部照旧。
    positionHistoryGroupForNode(node, group);
    return group;
}

function replaceOutputsToNodeWithHistory(node, additions, kind='image', meta=null, options={}){
    if(!node || !additions?.length) return [];
    node = canvasCore.liveSmartNode(node);
    const beforeRight = (Number(node.x) || 0) + canvasCore.nodeRect(node).width;
    const existing = cleanHistoryImages(node.images || []);
    const next = cleanHistoryImages(additions);
    if(!next.length) return [];
    const history = existing.length ? ensureHistoryGroupForNode(node) : historyGroupForNode(node);
    if(history){
        const archived = cleanHistoryImages([...existing, ...(history.images || [])]);
        history.images = archived;
        history.title = tr('smart.historyGroupTitle');
        history.outputKind = kind;
        history.scale = MEDIA_GROUP_DEFAULT_SCALE;
        delete history.w;
        delete history.h;
    }
    node.images = next;
    canvasCore.markSmartNodeComplete(node, meta, true);
    node.outputKind = kind;
    node.title = cascadeOutputTitle(kind, node.images.length);
    node.scale = node.images.length > 1 ? MEDIA_GROUP_DEFAULT_SCALE : canvasCore.MEDIA_NODE_DEFAULT_SCALE;
    delete node.w;
    delete node.h;
    if(meta) canvasCore.attachRunMeta(node, meta);
    const afterRight = (Number(node.x) || 0) + canvasCore.nodeRect(node).width;
    const skipShift = options.skipShift || Boolean(canvasCore.smartLoopContext?.nodeId);
    if(!skipShift) pushRightSideNodes(node, afterRight - beforeRight + 36);
    canvasState.selectedImage = {nodeId:'', index:-1};
    return next;
}

// 这些名字本来属于本模块，#200 从 smart-canvas.js 搬回（核心不引用它们，纯搬家）。
const SMART_HISTORY_DOM_LIMIT = 140;
const SMART_HISTORY_OVERSCAN = 48;
const SMART_HISTORY_RENDER_BUFFER = 24;
let apiProviders = [];
function playGenerationCompleteSound(){
    const now = Date.now();
    if(now - generationCompleteSoundAt < 1200) return;
    generationCompleteSoundAt = now;
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if(!AudioCtx) return;
        const ctx = playGenerationCompleteSound._ctx || (playGenerationCompleteSound._ctx = new AudioCtx());
        const play = () => {
            const start = ctx.currentTime + 0.015;
            [
                {freq:660, at:0, duration:0.12},
                {freq:880, at:0.12, duration:0.16}
            ].forEach(tone => {
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(tone.freq, start + tone.at);
                gain.gain.setValueAtTime(0.0001, start + tone.at);
                gain.gain.exponentialRampToValueAtTime(0.075, start + tone.at + 0.018);
                gain.gain.exponentialRampToValueAtTime(0.0001, start + tone.at + tone.duration);
                osc.connect(gain).connect(ctx.destination);
                osc.start(start + tone.at);
                osc.stop(start + tone.at + tone.duration + 0.02);
            });
        };
        if(ctx.state === 'suspended') ctx.resume().then(play).catch(() => {});
        else play();
    } catch(e) {}
}
function usedCanvasOutputUrls(){
    const used = new Set();
    (canvasState.nodes || []).forEach(node => (node.images || []).forEach(img => {
        if(img?.url && !img.loopInputPreview) used.add(img.url);
    }));
    return used;
}
function successfulRecentGenerationLogOutputs(sourceNodeId='', withinMs=30 * 60 * 1000){
    const cutoff = Date.now() - withinMs;
    const logs = (canvas?.history || [])
        .filter(log => log && log.status === 'success' && Number(log.createdAt || 0) >= cutoff)
        .filter(log => Array.isArray(log.outputs) && log.outputs.length)
        .sort((a, b) => Number(a.createdAt || 0) - Number(b.createdAt || 0));
    const scoped = sourceNodeId ? logs.filter(log => log.nodeId === sourceNodeId) : logs;
    const usable = scoped.length ? scoped : logs.filter(log => !log.nodeId);
    return usable.flatMap(log => (log.outputs || []).map(output => ({
        url:typeof output === 'string' ? output : (output?.url || output?.src || output?.path || ''),
        createdAt:log.createdAt,
        nodeId:log.nodeId
    }))).filter(item => item.url);
}
const smartTrashBackdrop = document.getElementById('smartTrashBackdrop');
const smartTrashList = document.getElementById('smartTrashList');
const smartTrashPurgeBtn = document.getElementById('smartTrashPurgeBtn');
function setupSimpleImageStage(stage){
    if(!stage) return null;
    const frame = stage.querySelector('.preview-frame');
    const label = stage.querySelector('[data-simple-zoom-label]');
    const view = {scale:1, panX:0, panY:0, drag:null};
    const apply = () => {
        if(!frame) return;
        frame.style.transform = `translate(${view.panX}px, ${view.panY}px) scale(${view.scale})`;
        if(label) label.textContent = `${Math.round(view.scale * 100)}%`;
    };
    const zoomAt = (factor, clientX, clientY) => {
        const rect = stage.getBoundingClientRect();
        const cx = clientX - rect.left - rect.width / 2;
        const cy = clientY - rect.top - rect.height / 2;
        const next = Math.min(8, Math.max(0.2, view.scale * factor));
        const k = next / view.scale;
        view.panX = cx - (cx - view.panX) * k;
        view.panY = cy - (cy - view.panY) * k;
        view.scale = next;
        apply();
    };
    stage.addEventListener('wheel', event => {
        event.preventDefault();
        event.stopPropagation();
        zoomAt(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX, event.clientY);
    }, {passive:false});
    stage.addEventListener('pointerdown', event => {
        if(event.button !== 0) return;
        event.stopPropagation();
        stage.setPointerCapture?.(event.pointerId);
        view.drag = {pointerId:event.pointerId, clientX:event.clientX, clientY:event.clientY, startX:view.panX, startY:view.panY};
    });
    stage.addEventListener('pointermove', event => {
        const drag = view.drag;
        if(!drag || drag.pointerId !== event.pointerId) return;
        event.preventDefault();
        event.stopPropagation();
        view.panX = drag.startX + event.clientX - drag.clientX;
        view.panY = drag.startY + event.clientY - drag.clientY;
        apply();
    });
    const finish = event => { if(view.drag && view.drag.pointerId === event.pointerId) view.drag = null; };
    stage.addEventListener('pointerup', finish);
    stage.addEventListener('pointercancel', finish);
    stage.addEventListener('dblclick', event => {
        event.preventDefault();
        event.stopPropagation();
        view.scale = 1; view.panX = 0; view.panY = 0;
        apply();
    });
    return {apply, reset(){ view.scale = 1; view.panX = 0; view.panY = 0; apply(); }};
}
const smartUnusedBackdrop = document.getElementById('smartUnusedBackdrop');
function smartRunTaskLabel(run){
    const s = run?.settings || {};
    if(run?.kind === 'video') return s.videoModel || 'Video';
    if(s.engine === 'modelscope'){
        return s.msgenModel === 'custom' ? (s.msCustomModel || 'Modelscope') : (MS_GEN_MODELS[s.msgenModel]?.label || s.msgenModel || 'Modelscope');
    }
    return s.model || 'API Image';
}
function outputUrlLooksVideo(url){
    return /\.(mp4|webm|mov|m4v|avi|mkv)(\?|$)/.test(smartOriginalMediaUrl(url).toLowerCase());
}
function syncCuratedFromMarks(){
    canvasState.nodes.forEach(node => {
        const imgs = node.images || [];
        if(!imgs.some(img => img && img.marked) && !node.curated) return;
        const curated = imgs.some(img => img && img.marked);
        if(Boolean(node.curated) !== curated) node.curated = curated;
    });
}
function smartRunPlatformLabel(run){
    const s = run?.settings || {};
    if(s.engine === 'modelscope') return 'Modelscope';
    if(run?.kind === 'video') return videoProviderById(s.videoProvider || '')?.name || s.videoProvider || 'Video';
    return apiProviderById(s.provider_id || '')?.name || s.provider_id || 'API';
}
function smartRunRequestMeta(run){
    const s = run?.settings || {};
    if(s.engine === 'modelscope') return {backend:'Modelscope', model:s.msgenModel || '', custom_model:s.msCustomModel || ''};
    if(run?.kind === 'video') return {provider_id:s.videoProvider || '', model:s.videoModel || '', duration:s.videoDuration || '', aspect_ratio:s.videoAspect || '', resolution:s.videoResolution || ''};
    return {provider_id:s.provider_id || '', model:s.model || '', size:run?.size || '', quality:s.quality || '', n:s.count || 1};
}
function normalizedSizeLabel(value){
    const parsed = canvasCore.parseSizeValue(value);
    const w = Number(parsed?.width || 0);
    const h = Number(parsed?.height || 0);
    return w > 0 && h > 0 ? `${Math.round(w)} x ${Math.round(h)}` : '';
}
function normalizedResolutionTier(value){
    const match = String(value || '').trim().toLowerCase().match(/^([124])\s*k$/);
    return match ? `${match[1]}K` : '';
}
function smartOutputAtPreservedPosition(outputs, preservedOutputId, fallbackPosition){
    const preserved = outputs.find(output => String(output.output_id || '') === String(preservedOutputId || ''));
    if(preserved) return preserved;
    if(!outputs.length) return null;
    return outputs[Math.max(0, Math.min(outputs.length - 1, Number(fallbackPosition) || 0))];
}
function openSmartCanvasLog(){
    if(!canvas) return;
    if(assetLibraryOpen) toggleAssetLibrary(false);
    renderSmartCanvasHistory();
    smartHistoryModal.classList.add('open');
    updateSmartHistoryDrawerCollapseUi();
    refreshSmartProjectMediaTrashButton();
}
