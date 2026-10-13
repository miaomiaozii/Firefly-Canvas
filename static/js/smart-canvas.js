console.log("smart-canvas.js loading...");
const params = new URLSearchParams(location.search);
const canvasId = params.get('id') || '';
const sourceProjectId = params.get('project') || '';
window.canvasId = canvasId;
const shell = document.getElementById('shell');
const world = document.getElementById('world');
const composer = document.getElementById('composer');
const createMenu = document.getElementById('createMenu');
const canvasEmptyState = document.getElementById('canvasEmptyState');
const canvasEmptyCreate = document.getElementById('canvasEmptyCreate');
const canvasEmptyImport = document.getElementById('canvasEmptyImport');
const smartSaveButton = document.getElementById('smartSaveButton');
const promptInput = document.getElementById('promptInput');
const mentionPicker = document.getElementById('mentionPicker');
const mentionPreview = document.getElementById('mentionPreview');
const engineSelect = document.getElementById('engineSelect');
const dynamicParams = document.getElementById('dynamicParams');
const runBtn = document.getElementById('runBtn');
const fileInput = document.getElementById('fileInput');
const apiKindToggle = document.getElementById('apiKindToggle');
const inputThumbsRow = document.getElementById('inputThumbsRow');
const promptFlowContext = document.getElementById('promptFlowContext');
const SMART_UPLOAD_MAX = 20;
const SMART_REFERENCE_IMAGE_MAX = 20;
const SMART_ONLINE_ENGINES = new Set(['api','volcengine']);
function normalizeSmartOnlineEngine(value){ return SMART_ONLINE_ENGINES.has(value) ? value : 'api'; }
const inputPromptPreview = document.getElementById('inputPromptPreview');
const minimap = document.getElementById('minimap');
const minimapContent = document.getElementById('minimapContent');
const smartArrangeBtn = document.getElementById('smartArrangeBtn');
const smartTitle = document.getElementById('smartTitle');
let smartTitleText = document.getElementById('smartTitleText');
const smartTitleEdit = document.getElementById('smartTitleEdit');
function setSmartTitleText(text){
    const label = String(text || '').trim() || tr('smart.title');
    if(smartTitleText) smartTitleText.textContent = label;
    return label;
}
let smartTitleEditing = false;
function openSmartTitleEdit(){
    if(smartTitleEditing || !smartTitle || !smartTitleText) return;
    smartTitleEditing = true;
    const current = String(canvas?.title || '').trim() || tr('smart.title');
    const input = document.createElement('input');
    input.id = 'smartTitleInput';
    input.type = 'text';
    input.value = current;
    input.maxLength = 60;
    input.setAttribute('aria-label', tr('smart.title'));
    smartTitleText.replaceWith(input);
    const finish = (save) => {
        if(!smartTitleEditing) return;
        smartTitleEditing = false;
        const next = document.createElement('span');
        next.id = 'smartTitleText';
        next.className = 'smart-title-text';
        next.setAttribute('data-i18n', 'smart.title');
        input.replaceWith(next);
        // 更新全局引用：否则第二次重命名时 replaceWith 作用于已脱离文档的旧节点，输入框永远无法打开
        smartTitleText = next;
        if(save){
            const value = input.value.trim();
            if(value && canvas && value !== canvas.title){
                canvas.title = value;
                setSmartTitleText(value);
                document.title = value;
                scheduleSave();
            } else if(value){
                setSmartTitleText(value);
            }
        } else {
            setSmartTitleText(canvas?.title);
        }
    };
    input.addEventListener('keydown', event => {
        if(event.key === 'Enter'){ event.preventDefault(); finish(true); }
        else if(event.key === 'Escape'){ event.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
    input.addEventListener('pointerdown', event => event.stopPropagation());
    input.addEventListener('mousedown', event => event.stopPropagation());
    input.addEventListener('keydown', event => event.stopPropagation());
    requestAnimationFrame(() => { input.focus(); input.select(); });
}
function setupSmartTitleEdit(){
    smartTitleEdit?.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); openSmartTitleEdit(); });
    smartTitleEdit?.addEventListener('pointerdown', event => event.stopPropagation());
    smartTitleEdit?.addEventListener('mousedown', event => event.stopPropagation());
    smartTitle?.addEventListener('click', event => { if(!event.target.closest?.('#smartTitleEdit')) openSmartTitleEdit(); });
    smartTitle?.addEventListener('dblclick', event => { event.preventDefault(); openSmartTitleEdit(); });
}
const smartHistoryModal = document.getElementById('smartHistoryModal');
const smartHistoryList = document.getElementById('smartHistoryList');
const smartHistoryCollapse = document.getElementById('smartHistoryCollapse');
const smartHistoryPurgeTrash = document.getElementById('smartHistoryPurgeTrash');
const SMART_HISTORY_DOM_LIMIT = 140;
const SMART_HISTORY_OVERSCAN = 48;
const SMART_HISTORY_RENDER_BUFFER = 24;
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
let smartProjectMediaTrashRefreshVersion = 0;
const smartShortcutModal = document.getElementById('smartShortcutModal');
const smartSettingsLink = document.getElementById('smartSettingsLink');
const smartSettingsModal = document.getElementById('smartSettingsModal');
const smartSettingsFrame = document.getElementById('smartSettingsFrame');
const smartSettingsClose = document.getElementById('smartSettingsClose');
let smartSettingsLastFocus = null;
const selectionBox = document.getElementById('selectionBox');
const assetToggle = document.getElementById('assetToggle');
const assetPanel = document.getElementById('assetPanel');
const assetCloseBtn = document.getElementById('assetCloseBtn');
const assetLibrarySelect = document.getElementById('assetLibrarySelect');
const assetCategorySelect = document.getElementById('assetCategorySelect');
const assetGrid = document.getElementById('assetGrid');
const assetDropZone = document.getElementById('assetDropZone');
const assetImageControls = document.getElementById('assetImageControls');
const assetAddCategoryBtn = document.getElementById('assetAddCategoryBtn');
const assetRenameCategoryBtn = document.getElementById('assetRenameCategoryBtn');
const assetDialogBackdrop = document.getElementById('assetDialogBackdrop');
const assetDialogTitle = document.getElementById('assetDialogTitle');
const assetDialogInput = document.getElementById('assetDialogInput');
const assetDialogCancel = document.getElementById('assetDialogCancel');
const assetDialogOk = document.getElementById('assetDialogOk');
const assetHoverPreview = document.getElementById('assetHoverPreview');
const promptPresetPanel = document.getElementById('promptPresetPanel');
const promptPresetClose = document.getElementById('promptPresetClose');
const promptPresetStatus = document.getElementById('promptPresetStatus');
const promptPresetSelect = document.getElementById('promptPresetSelect');
const promptPresetName = document.getElementById('promptPresetName');
const promptPresetText = document.getElementById('promptPresetText');
const promptPresetApply = document.getElementById('promptPresetApply');
const promptPresetDelete = document.getElementById('promptPresetDelete');
const promptPresetNew = document.getElementById('promptPresetNew');
const promptPresetSave = document.getElementById('promptPresetSave');
const promptTemplatePanel = document.getElementById('promptTemplatePanel');
const promptTemplateClose = document.getElementById('promptTemplateClose');
const promptTemplateSearch = document.getElementById('promptTemplateSearch');
const promptTemplateLibrarySelect = document.getElementById('promptTemplateLibrarySelect');
const promptTemplateCats = document.getElementById('promptTemplateCats');
const promptTemplateBody = document.getElementById('promptTemplateBody');
const composerTemplateBtn = document.getElementById('composerTemplateBtn');
const composerExpandBtn = document.getElementById('composerExpandBtn');
const composerFlowBtn = document.getElementById('composerFlowBtn');
const longContentReader = document.getElementById('longContentReader');
const longContentReaderTitle = document.getElementById('longContentReaderTitle');
const longContentReaderBody = document.getElementById('longContentReaderBody');
const longContentReaderClose = document.getElementById('longContentReaderClose');
let minimapViewport = document.getElementById('minimapViewport');
let canvas = null;
let canvasUsesConnections = true;
let dragState = null;
let composerDragSettleTimer = 0;
let loopInsertPreview = null;
let selectionState = null;
let isRKeyDown = false;
let selectionJustFinished = false;
let resizeState = null;
let llmInstructionResizeState = null;
let promptSplitResizeState = null;
let thumbDragState = null;
let uploadTargetId = '';
let pendingGroupUploadPoint = null;
let mentionRange = null;
let mentionAnchorEl = null;
let promptCompositionActive = false;
const composerPromptView = {
    mode:'manual',
    returnMode:'manual',
    nodeId:'',
    manualHeight:84,
    expandedHeight:84,
    expandedExtra:0,
    scrollTop:0,
    restoreFlowScroll:false,
    applyingScrollRestore:false,
    expandedByNode:new Map() // 按节点 ID 保存展开状态，取消选择后重新选择同一节点时恢复
};
const promptNodeFlowView = {
    mode:'idle',
    nodeId:'',
    element:null,
    textarea:null,
    trigger:null,
    scrollTop:0,
    selection:null,
    compositionActive:false
};
const longContentSession = {
    mode:'idle',
    kind:'',
    trigger:null,
    triggerIdentity:null
};
let mentionInsertMode = 'token';
let panState = null;
let didPan = false;
let portDragState = null;
let connectionEraseState = null;
const DEBOUNCED_SAVE_MS = 3000;
let debouncedSaveTimer = null;
let contentDirty = false;
let persistedCoreSnapshot = null;
let persistedDurableSnapshot = null;
// Empty-canvas guard (H39 incident): true only after loadCanvas has successfully
// populated the canvas from the server. Until then an empty local node set is
// dangerous (a failed load looks identical to "user deleted everything") and
// must neither autosave an empty state nor claim force_empty on the server.
let canvasLoadReady = false;
// Conversation polling can receive responses out of order. Keep an explicit
// accepted watermark independent of the snapshot shape so a stale response
// cannot become authoritative even if another reload temporarily regresses
// the in-memory canvas revision.
let conversationCanvasLastAcceptedRevision = 0;
const projectMediaImportCache = new Map();
const conversationTaskPolls = new Map();
let activeProjectMediaImports = 0;
const projectMediaImportWaiters = [];
let apiProviders = [];
// model_specs.json is the authoritative registry of adapted models + their
// parameter options. Loaded via /api/model-specs; drives model lists & params.
let modelSpecs = {chat_models:{}, image_models:{}, video_models:{}, chat_model_ids:[], image_model_ids:[], video_model_ids:[], schema_version:null, last_updated:null};
let assetLibrary = {categories:[]};
let assetLibraryOpen = false;
let activeAssetCategoryId = '';
let activeAssetLibraryId = '';
let assetLibrarySelectionTouched = false;
const LOCAL_ASSET_LIBRARY_ID = '__local_assets__';
const PROJECT_ASSET_LIBRARY_ID = '__project_media__';
let localAssetLibrary = {items:[], tree:null};
let activeLocalAssetFolderId = '__root__';
let mentionSource = 'input';
let mentionAssetCategoryId = '';
let assetLibraryUpdatedAt = 0;
let assetLibraryRefreshTimer = null;
let activeAssetSmartClassId = '';
const ASSET_SMART_CATEGORY_PREFIX = '__smart_class__::';
const PROMPT_PRESETS_KEY = 'smart_canvas_prompt_presets_v1';
const PROMPT_TEMPLATE_GROUPS_KEY = 'smart_canvas_prompt_template_groups_v1';
const PROMPT_TEMPLATE_OVERRIDES_KEY = 'smart_canvas_prompt_template_overrides_v1';
let promptPresets = [];
let builtinPromptTemplates = [];
let promptLibraries = [];
let activePromptLibraryId = 'system';
let promptTemplateGroups = [];
let promptTemplateOverrides = {hiddenBuiltinIds:[], editedBuiltins:{}};
let promptTemplateCategory = 'all';
let promptTemplateSelectedId = '';
let promptTemplateEditing = false;
let promptTemplateGroupEditMode = false;
let promptPresetDeleteArmed = false;
let createMenuPoint = {x:0, y:0};
let nodeClipboard = null;
let imageClickTimer = null;
let suppressImageClickUntil = 0;
let lastMouseWorld = null;
let lastConfigRefreshAt = 0;
let smartMinimapState = null;
let smartEdgeRevealTimer = 0;
let smartEdgeRevealNodeId = '';
let smartEdgeRevealHiding = false;
let smartMinimapDrag = false;
let zoomPreviewState = null;
let runTimerInterval = null;
const smartCascadeRuns = new Map();
let smartLoopContext = null;
let runBtnCooldownToken = 0;
let smartRhRandomValues = {};
let lastImagePasteAt = 0;
let lastNodePasteAt = 0;
let suppressNodeClickUntil = 0;
let textSelectionGuard = null;
let conversationSelection = null;
let conversationSelectionCandidate = null;
let conversationExtractionReturnFocus = null;
// #146: 删除节点后媒体归档延迟到撤销窗口结束（用户不太可能再撤销时）才执行；
// 若用户在窗口内撤销，则取消待归档，避免撤销恢复的节点引用已归档资产 → 404。
function activeSmartCascadeCount(){ return smartCascadeRuns.size; }
function smartCascadeRunForLoop(loopId){ return loopId ? smartCascadeRuns.get(loopId) || null : null; }
function smartCascadeIsLoopRunning(loopId){ return Boolean(smartCascadeRunForLoop(loopId)); }
function smartCascadeAnyRunning(){ return activeSmartCascadeCount() > 0; }
function smartCascadeEdgeState(edgeKey){
    for(const run of smartCascadeRuns.values()){
        const state = run?.runPath?.states?.[edgeKey];
        if(state) return state;
    }
    return '';
}
function smartCascadePathForCtx(ctx=null){
    return ctx?.runState?.runPath || ctx?.runPath || null;
}
let previewZoom = 1.0;
let previewPan = {x:0, y:0};
let previewPanDrag = null;
let previewCompareDrag = false;
let previewComparePos = 50;
let previewNavState = {nodeId:'', index:0, count:0};
let viewport = {x:0, y:0, scale:1};
const PROJECT_VIEWPORT_STORAGE_PREFIX = 'firefly_canvas_viewport_v1:';
function isProjectCanvasData(source=canvas){
    return source?.format === 'firefly-canvas-v1' || params.get('project_mode') === '1';
}
function projectViewportStorageKey(projectId=canvasId){
    return projectId ? `${PROJECT_VIEWPORT_STORAGE_PREFIX}${projectId}` : '';
}
function normalizeProjectViewport(value){
    const next = value && typeof value === 'object' ? value : {};
    const x = Number(next.x);
    const y = Number(next.y);
    const scale = Number(next.scale);
    return {
        x:Number.isFinite(x) ? x : 0,
        y:Number.isFinite(y) ? y : 0,
        scale:safeScale(Number.isFinite(scale) ? scale : 1),
    };
}
function loadProjectViewport(projectId=canvasId){
    const key = projectViewportStorageKey(projectId);
    if(!key) return {x:0, y:0, scale:1};
    try {
        return normalizeProjectViewport(JSON.parse(localStorage.getItem(key) || '{}'));
    } catch(error) {
        return {x:0, y:0, scale:1};
    }
}
function persistProjectViewport(){
    const key = projectViewportStorageKey();
    if(!key) return;
    try { localStorage.setItem(key, JSON.stringify(normalizeProjectViewport(viewport))); } catch(error) {}
}
let settings = {
    engine:'api',
    apiKind:'image',
    routingMode:'auto',
    anglePreset:'none',
    provider_id:'',
    model:'',
    ratio:'square',
    resolution:'4k',
    customRatio:'',
    customRatioWidth:'',
    customRatioHeight:'',
    customSize:'',
    customWidth:'',
    customHeight:'',
    quality:'auto',
    count:1,
    videoRoutingMode:'auto',
    videoProvider:'',
    videoModel:'',
    videoDuration:5,
    videoAspect:'16:9',
    videoResolution:'',
    videoEnhancePrompt:false,
    videoEnableUpsample:false,
    videoWatermark:false,
    videoCameraFixed:false,
    videoReturnLastFrame:false,
    videoGenerateAudio:false,
    videoMultimodal:true,
    _videoMultimodalUserSet:false,
    videoUseFrameRoles:false,
    videoTrustedAsset:false,
    videoTrustedSource:'library',
    videoTempShLinks:[],
    msgenModel:'custom',
    msCustomModel:'',
    msRatio:'square',
    msResolution:'1k',
    msCustomRatio:'',
    msCustomRatioWidth:'',
    msCustomRatioHeight:'',
    msCustomSize:'',
    msCustomWidth:'',
    msCustomHeight:'',
    rhConfigKey:'',
    rhPayment:'free',
    rhInstanceType:'',
    rhParams:{},
    rhRandomActive:{},
    width:1024,
    height:1024,
    promptH:84
};
const MS_GEN_MODELS = {
    custom: { label:tr('smart.custom'), modelId:'', acceptsImage:true }
};
const SIZE_MAP = {
    square: {'1k':'1024x1024','2k':'2048x2048','4k':'4096x4096'},
    portrait: {'1k':'1024x1536','2k':'1360x2048','4k':'2352x3520'},
    portrait43: {'1k':'1008x1344','2k':'1536x2048','4k':'2448x3264'},
    landscape43: {'1k':'1344x1008','2k':'2048x1536','4k':'3264x2448'},
    landscape: {'1k':'1536x1024','2k':'2048x1360','4k':'3520x2352'},
    story: {'1k':'720x1280','2k':'1152x2048','4k':'2160x3840'},
    wide: {'1k':'1280x720','2k':'2048x1152','4k':'3840x2160'},
    ultrawide: {'1k':'1280x544','2k':'2048x880','4k':'3840x1648'},
    ultratall: {'1k':'544x1280','2k':'880x2048','4k':'1648x3840'}
};
const RES_LONG_SIDE = { '1k':1536, '2k':2048, '4k':3840 };
const RES_PIXEL_LIMIT = { '1k':1572864, '2k':4194304, '4k':8294400 };
function tr(key){ return window.StudioI18n?.t ? window.StudioI18n.t(key) : key; }
function trf(key, values={}){
    return Object.entries(values).reduce((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), tr(key));
}
function copyTextWithCopyEvent(value){
    let handled = false;
    const onCopy = event => {
        event.preventDefault();
        event.clipboardData?.setData('text/plain', value);
        handled = true;
    };
    document.addEventListener('copy', onCopy);
    try {
        return document.execCommand('copy') && handled;
    } catch(_) {
        return false;
    } finally {
        document.removeEventListener('copy', onCopy);
    }
}
function copyTextWithTextarea(value){
    let ta = null;
    try {
        ta = document.createElement('textarea');
        ta.value = value;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        ta.style.top = '0';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.focus({preventScroll:true});
        ta.select();
        ta.setSelectionRange(0, ta.value.length);
        return document.execCommand('copy');
    } catch(_) {
        return false;
    } finally {
        ta?.remove();
    }
}
async function clipboardMatchesText(value){
    try {
        if(navigator.clipboard?.readText && window.isSecureContext){
            return (await navigator.clipboard.readText()) === value;
        }
    } catch(_) {}
    return null;
}
async function copyTextToClipboard(text){
    const value = String(text || '');
    if(!value) return false;
    if(copyTextWithCopyEvent(value) || copyTextWithTextarea(value)){
        const verified = await clipboardMatchesText(value);
        return verified !== false;
    }
    try {
        if(navigator.clipboard?.writeText && window.isSecureContext !== false){
            await navigator.clipboard.writeText(value);
            const verified = await clipboardMatchesText(value);
            return verified !== false;
        }
    } catch(_) {}
    return false;
}
function syncIconButtonAccessibility(root=document){
    root?.querySelectorAll?.('button').forEach(button => {
        if(button.textContent.trim()) return;
        const title = String(button.getAttribute('title') || '').trim();
        const accessibleName = String(button.getAttribute('aria-label') || '').trim();
        if(title && !accessibleName) button.setAttribute('aria-label', title);
        else if(accessibleName && !title) button.title = accessibleName;
    });
}
function refreshIcons(root=document){
    if(window.lucide) lucide.createIcons();
    syncIconButtonAccessibility(root);
}
function uid(prefix){ return `${prefix}_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`; }
function escapeHtml(str){ return String(str == null ? '' : str).replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
const escapeAttr = escapeHtml;

// Lightweight XSS-safe markdown renderer for agent TUI messages
// Escapes HTML first, then renders markdown syntax.
// Supports: code blocks, inline code, bold, italic, links, unordered lists.
function renderMarkdown(text){
    if(text == null) return '';
    let html = escapeHtml(String(text));
    // Code blocks (```) - must be done before inline code
    html = html.replace(/```([\s\S]*?)```/g, '<pre class="agent-md-code-block"><code>$1</code></pre>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code class="agent-md-inline-code">$1</code>');
    // Bold (**text**)
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Italic (*text*) - must be after bold
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    // Links [text](url) - only allow safe protocols (block javascript: etc.)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
        const safeUrl = /^(https?:|mailto:|\/|#|\.\/|\.\.\/)/i.test(url) ? url : '#';
        return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${text}</a>`;
    });
    // Unordered lists (- item)
    html = html.replace(/(^|\n)- (.+)/g, '$1<li>$2</li>');
    html = html.replace(/(<li>.*?<\/li>)(\s*(?:<li>.*?<\/li>))+/g, '<ul class="agent-md-list">$1</ul>');
    // Paragraphs (double newline)
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    html = '<p>' + html + '</p>';
    // Clean up empty paragraphs
    html = html.replace(/<p><\/p>/g, '');
    return html;
}
function smartPreviewImgHtml(itemOrUrl, size=512, attrs=''){
    const original = smartOriginalMediaUrl(itemOrUrl);
    const preview = smartMediaPreviewUrl(itemOrUrl, size);
    return `<img src="${escapeHtml(preview)}" data-preview-src="${escapeAttr(preview)}" data-original-src="${escapeAttr(original)}"${attrs ? ` ${attrs}` : ''}>`;
}
function loadSmartOriginalImageDimensions(url){
    const src = displayMediaUrl({url:smartOriginalMediaUrl(url)});
    if(!src || /^data:/i.test(src) || /^blob:/i.test(src)) return Promise.resolve(null);
    return new Promise(resolve => {
        const img = new Image();
        img.onload = () => resolve(img.naturalWidth && img.naturalHeight ? {w:img.naturalWidth, h:img.naturalHeight} : null);
        img.onerror = () => resolve(null);
        img.src = src;
    });
}
function smartVideoPreviewHtml(itemOrUrl, size=512, attrs=''){
    const original = smartOriginalMediaUrl(itemOrUrl);
    return smartVideoFallbackHtml(original, attrs);
}
function smartVideoFallbackHtml(url, attrs=''){
    const original = smartOriginalMediaUrl(url);
    const src = displayMediaUrl({url:original});
    return `<video src="${escapeHtml(src)}" data-url="${escapeAttr(original)}" muted preload="metadata" playsinline disablepictureinpicture controlslist="nodownload noplaybackrate noremoteplayback"${attrs ? ` ${attrs}` : ''}></video>`;
}
function smartVideoPlayerHtml(url, attrs=''){
    const original = smartOriginalMediaUrl(url);
    const safe = escapeHtml(displayMediaUrl({url:original}));
    return `<video src="${safe}" data-url="${escapeAttr(original)}" data-inline-video-active="1" controls autoplay playsinline preload="metadata" disablepictureinpicture controlslist="nodownload noplaybackrate noremoteplayback"${attrs ? ` ${attrs}` : ''}></video>`;
}
function smartActivateVideoPreview(target){
    const root = target?.closest?.('.media-video-card,.video-thumb,.image-wrap,.thumb-item') || target?.parentElement || null;
    const img = target?.matches?.('img[data-preview-kind="video"]') ? target : root?.querySelector?.('img[data-preview-kind="video"]');
    if(!img){
        const fallback = target?.matches?.('video[data-url]') ? target : root?.querySelector?.('video[data-url]');
        if(fallback){
            fallback.controls = true;
            fallback.muted = false;
            fallback.play?.().catch(() => {});
            return true;
        }
        return false;
    }
    const original = smartOriginalMediaUrl(img.dataset.originalSrc || img.dataset.url || img.getAttribute('src') || '');
    if(!original) return false;
    const itemEl = target?.closest?.('[data-image-index]') || root?.closest?.('[data-image-index]') || root;
    const nodeEl = target?.closest?.('.image-node') || root?.closest?.('.image-node');
    const node = canvasState.nodes.find(n => n.id === nodeEl?.dataset.id);
    const imageIndex = Number(itemEl?.dataset?.imageIndex ?? 0);
    const image = node?.images?.[imageIndex];
    if(image) image._inlineVideoActive = true;
    const tpl = document.createElement('template');
    tpl.innerHTML = smartVideoPlayerHtml(original);
    const video = tpl.content.firstElementChild;
    if(!video) return false;
    img.replaceWith(video);
    video.parentElement?.querySelector?.('.smart-video-play')?.style?.setProperty('display', 'none');
    video.addEventListener('ended', () => {
        if(image) image._inlineVideoActive = true;
        video.dataset.inlineVideoActive = '1';
    });
    video.play?.().catch(() => {});
    return true;
}
function isSmartPreviewImage(img){
    return img?.tagName?.toLowerCase?.() === 'img'
        && img.dataset?.previewSrc
        && img.dataset?.originalSrc
        && img.dataset.previewSrc !== img.dataset.originalSrc
        && img.getAttribute('src') !== img.dataset.originalSrc;
}
function bindSmartPreviewImageFallbacks(root=document){
    root.querySelectorAll?.('img[data-preview-src][data-original-src]:not([data-preview-fallback-bound])').forEach(img => {
        img.dataset.previewFallbackBound = '1';
        img.addEventListener('error', () => {
            const original = img.dataset.originalSrc || '';
            if(img.dataset.previewKind === 'video'){
                const tpl = document.createElement('template');
                tpl.innerHTML = smartVideoFallbackHtml(original, img.dataset.videoFallbackAttrs || '');
                img.replaceWith(tpl.content.firstElementChild);
                return;
            }
            if(original && img.getAttribute('src') !== original) img.src = original;
        });
    });
}
const SMART_SELECTED_HIGH_RES_DELAY = 320;
let smartSelectedHighResTimer = 0;
let smartSelectedHighResSeq = 0;
let smartSelectedHighResNodeIds = new Set();
const smartSelectedHighResLoaded = new Set();
const smartSelectedHighResLoading = new Map();
function preloadSmartSelectedHighRes(src){
    if(!src || smartSelectedHighResLoaded.has(src)) return Promise.resolve(true);
    if(smartSelectedHighResLoading.has(src)) return smartSelectedHighResLoading.get(src);
    const task = new Promise(resolve => {
        const img = new Image();
        img.decoding = 'async';
        img.onload = async () => {
            try { if(img.decode) await img.decode(); } catch(e) {}
            smartSelectedHighResLoaded.add(src);
            resolve(true);
        };
        img.onerror = () => resolve(false);
        img.src = src;
    }).finally(() => smartSelectedHighResLoading.delete(src));
    smartSelectedHighResLoading.set(src, task);
    return task;
}
function smartNodeElementsByIds(ids){
    const wanted = ids instanceof Set ? ids : new Set(ids || []);
    const elements = [];
    if(!wanted.size) return elements;
    world.querySelectorAll?.('.image-node').forEach(el => {
        const id = el.dataset?.id || '';
        if(wanted.has(id)) elements.push(el);
    });
    return elements;
}
function smartNodeElementsForHighResSync(root){
    if(root && root !== world) return [root];
    const ids = new Set([...smartSelectedHighResNodeIds, ...selectedNodeIds()]);
    return smartNodeElementsByIds(ids);
}
function syncSmartSelectedImageResolution(root=null){
    const selectedImages = [];
    smartNodeElementsForHighResSync(root).forEach(scope => {
        const nodeEl = scope?.classList?.contains('image-node') ? scope : scope?.closest?.('.image-node');
        const nodeId = nodeEl?.dataset?.id || '';
        const selectedNode = Boolean(nodeId && isNodeSelected(nodeId));
        scope.querySelectorAll?.('img[data-preview-src][data-original-src]').forEach(img => {
            if(img.dataset.previewKind === 'video') return;
            const preview = img.dataset.previewSrc || '';
            const original = img.dataset.originalSrc || '';
            if(!selectedNode){
                delete img.dataset.selectedHighResTarget;
                if(preview && img.getAttribute('src') !== preview) img.src = preview;
                return;
            }
            const target = displayMediaUrl({url:smartOriginalMediaUrl(original)});
            if(!target) return;
            img.dataset.selectedHighResTarget = target;
            if(smartSelectedHighResLoaded.has(target)){
                if(img.getAttribute('src') !== target) img.src = target;
                return;
            }
            if(preview && img.getAttribute('src') !== preview) img.src = preview;
            selectedImages.push({img, target});
        });
    });
    if(smartSelectedHighResTimer) clearTimeout(smartSelectedHighResTimer);
    const seq = ++smartSelectedHighResSeq;
    smartSelectedHighResNodeIds = new Set(selectedNodeIds());
    if(!selectedImages.length || smartImageEditorIsOpen()) return;
    smartSelectedHighResTimer = setTimeout(async () => {
        smartSelectedHighResTimer = 0;
        if(seq !== smartSelectedHighResSeq || smartImageEditorIsOpen()) return;
        await Promise.all(selectedImages.map(item => preloadSmartSelectedHighRes(item.target)));
        if(seq !== smartSelectedHighResSeq || smartImageEditorIsOpen()) return;
        selectedImages.forEach(({img, target}) => {
            if(!img.isConnected || img.dataset.selectedHighResTarget !== target) return;
            const nodeEl = img.closest('.image-node');
            if(!nodeEl?.dataset?.id || !isNodeSelected(nodeEl.dataset.id)) return;
            if(smartSelectedHighResLoaded.has(target) && img.getAttribute('src') !== target) img.src = target;
        });
    }, SMART_SELECTED_HIGH_RES_DELAY);
}
function cloneSmartSettings(source=settings){
    try {
        return JSON.parse(JSON.stringify(source || {}));
    } catch(e) {
        return {...(source || {})};
    }
}
function settingsForStorage(source=settings){
    const clean = cloneSmartSettings(source);
    clean.engine = normalizeSmartOnlineEngine(clean.engine);
    clean.videoTempShLinks = (clean.videoTempShLinks || []).filter(item => item?.manual === true);
    return clean;
}
function normalizeSmartVideoModeSettings(target, preferMultimodal=false){
    if(!target || typeof target !== 'object') return target;
    target.videoUseFrameRoles = Boolean(target.videoUseFrameRoles);
    if(preferMultimodal && !target.videoUseFrameRoles && target._videoMultimodalUserSet !== true) target.videoMultimodal = true;
    else target.videoMultimodal = Boolean(target.videoMultimodal);
    if(target.videoUseFrameRoles) target.videoMultimodal = false;
    return target;
}
function isApiLikeEngine(engine){
    return ['api', 'volcengine'].includes(String(engine || '').toLowerCase());
}
function smartLoopRoundSettings(runSettings, ctx=smartLoopContext){
    const next = {...(runSettings || {})};
    const imageCountEngine = isApiLikeEngine(next.engine)
        ? next.apiKind !== 'video'
        : next.engine === 'modelscope';
    if(ctx?.nodeId && imageCountEngine){
        next.count = 1;
    }
    return next;
}
function isGptImageAutoSizeModel(model){
    const raw = String(model || '').trim().toLowerCase();
    const normalized = raw.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    const compact = raw.replace(/[^a-z0-9]+/g, '');
    return normalized === 'gpt-image-2'
        || normalized.startsWith('gpt-image-2-')
        || normalized.endsWith('-gpt-image-2')
        || normalized.includes('-gpt-image-2-')
        || compact === 'gptimage2'
        || compact.startsWith('gptimage2')
        || compact.endsWith('gptimage2');
}
function defaultSmartApiResolution(model){
    return isGptImageAutoSizeModel(model) ? '4k' : '1k';
}
async function withProjectMediaImportSlot(operation){
    if(activeProjectMediaImports >= 3){
        await new Promise(resolve => projectMediaImportWaiters.push(resolve));
    }
    activeProjectMediaImports += 1;
    try {
        return await operation();
    } finally {
        activeProjectMediaImports -= 1;
        projectMediaImportWaiters.shift()?.();
    }
}
async function importProjectMediaReference(item){
    const locatorKeys = ['url','src','output','output_url','outputUrl','path','uri'];
    const sourceUrl = locatorKeys.map(key => item?.[key]).find(value => typeof value === 'string' && value.trim());
    if(item?.asset_id){
        const clean = {...item};
        locatorKeys.concat(['href','file','local_path','localPath','remote_url','remoteUrl','preview_url','previewUrl']).forEach(key => delete clean[key]);
        return clean;
    }
    if(!sourceUrl) return null;
    const projectId = canvas.project_id || canvas.project || canvasId;
    const projectMediaPrefix = `/api/project-library/${encodeURIComponent(projectId)}/media/`;
    try {
        const parsed = new URL(sourceUrl, window.location.origin);
        if(parsed.origin === window.location.origin && parsed.pathname.startsWith(projectMediaPrefix)){
            const assetId = decodeURIComponent(parsed.pathname.slice(projectMediaPrefix.length).split('/')[0] || '');
            if(assetId){
                const clean = {...item, asset_id:assetId};
                locatorKeys.concat(['href','file','local_path','localPath','remote_url','remoteUrl','preview_url','previewUrl']).forEach(key => delete clean[key]);
                return clean;
            }
        }
    } catch(_error) {
        // Keep the normal import path for malformed or external locators.
    }
    const itemKind = mediaKindForItem(item);
    const expectedKind = ['image','video','audio'].includes(itemKind) ? itemKind : '';
    const cacheKey = `${projectId}\n${expectedKind}\n${sourceUrl}`;
    let task = projectMediaImportCache.get(cacheKey);
    if(!task){
        const sourceLocator = item.originalRemoteUrl || item.originalLocalUrl || item.remote_url || item.remoteUrl || item.local_path || item.localPath || sourceUrl;
        task = withProjectMediaImportSlot(() => fetch(`/api/project-library/${encodeURIComponent(projectId)}/media/import`, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({source_url:sourceUrl, source_locator:sourceLocator, name:item.name || item.filename || '', mime_type:item.mime || item.mime_type || '', expected_kind:expectedKind}),
        }).then(async response => {
            if(!response.ok) throw new Error(await responseErrorMessage(response, tr('smart.mediaArchiveFailed')));
            const data = await response.json();
            if(data.media) return data.media;
            const taskId = data.task?.id;
            if(!taskId) throw new Error(tr('smart.mediaImportBadResponse'));
            setProjectSaveState('importing', tr('smart.projectImporting'));
            const deadline = Date.now() + 10 * 60 * 1000;
            let pollDelay = 150;
            while(Date.now() < deadline){
                await new Promise(resolve => setTimeout(resolve, pollDelay));
                pollDelay = Math.min(1500, Math.round(pollDelay * 1.5));
                const statusResponse = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/media/import/${encodeURIComponent(taskId)}`);
                if(!statusResponse.ok) throw new Error(await responseErrorMessage(statusResponse, tr('smart.mediaImportStatusFailed')));
                const status = (await statusResponse.json()).task || {};
                if(status.status === 'completed' && status.media) return status.media;
                if(status.status === 'failed') throw new Error(status.error || tr('smart.mediaArchiveFailed'));
            }
            throw new Error(tr('smart.mediaArchiveTimeout'));
        }).catch(error => {
            projectMediaImportCache.delete(cacheKey);
            throw error;
        }));
        projectMediaImportCache.set(cacheKey, task);
    }
    const media = await task;
    const catalogItem = {
        ...media,
        original_url:projectMediaUrl(media.asset_id),
        preview_url:(media.kind || mediaKindForItem(media)) === 'image' ? projectMediaPreviewUrl(media.asset_id) : ''
    };
    const existingIndex = (canvasState.projectMediaLibrary.items || []).findIndex(entry => entry.asset_id === media.asset_id);
    if(existingIndex >= 0) canvasState.projectMediaLibrary.items[existingIndex] = catalogItem;
    else canvasState.projectMediaLibrary.items = [catalogItem, ...(canvasState.projectMediaLibrary.items || [])];
    if(assetLibraryOpen && assetLibraryIsProject()) renderAssetLibrary();
    const clean = {...item, asset_id:media.asset_id, name:item.name || media.name, mime_type:item.mime_type || item.mime || media.mime_type};
    locatorKeys.concat(['href','file','local_path','localPath','remote_url','remoteUrl','preview_url','previewUrl']).forEach(key => delete clean[key]);
    return clean;
}
async function materializeProjectMediaReferences(value, mediaContext=false){
    if(Array.isArray(value)) return Promise.all(value.map(item => materializeProjectMediaReferences(item, mediaContext)));
    if(typeof value === 'string' && mediaContext) return importProjectMediaReference({url:value});
    if(!value || typeof value !== 'object') return value;
    const imported = value.asset_id || mediaContext ? await importProjectMediaReference(value) : null;
    if(imported) return imported;
    const clean = {};
    const mediaKeys = new Set([
        'image','images','video','videos','audio','media','outputs','references','input_images',
        'refs','promptrefs','runpromptrefs','runinputrefs'
    ]);
    for(const [key, child] of Object.entries(value)) clean[key] = await materializeProjectMediaReferences(child, mediaKeys.has(String(key).toLowerCase()));
    return clean;
}
function canvasForStorage(){
    const clean = JSON.parse(JSON.stringify(canvas || {}));
    if(isProjectCanvasData(clean)){
        // Composer defaults and viewport are application/global UI state. Core
        // project files contain only creative content and reopen metadata.
        delete clean.settings;
        delete clean.viewport;
    } else {
        // Classic canvases are retained for old-data compatibility and still
        // own their historical viewport/composer fields.
        clean.settings = settingsForStorage(canvasDefaultSmartSettings || initialSmartSettings);
        clean.viewport = {...viewport};
    }
    // 日志预览的临时节点（编辑器打开期间临时塞进 canvasState.nodes）绝不能被持久化，否则刷新后会留下幽灵节点。
    if(Array.isArray(clean.nodes)) clean.nodes = clean.nodes.filter(node => node.id !== SMART_LOG_PREVIEW_NODE_ID);
    (clean.nodes || []).forEach(node => {
        delete node._pendingHistoryId;
        if(Array.isArray(node.images)) node.images = node.images.map(mediaItemForStorage);
        if(node.runSettings) node.runSettings = settingsForStorage(node.runSettings);
        if(node.type === 'smart-conversation') delete node.conversationSending;
        if(node.type === 'local-edit') delete node._leBusy;
    });
    clean.nodes = mediaReferencesForStorage(clean.nodes || []);
    clean.history = mediaReferencesForStorage((clean.history || []).filter(log => log?.status !== 'pending'));
    return clean;
}
function apiErrorMessage(data, fallback=tr('chat.requestFailed')){
    if(!data) return fallback;
    if(typeof data === 'string') return data || fallback;
    const detail = data.detail ?? data.error ?? data.message;
    if(typeof detail === 'string') return detail || fallback;
    if(Array.isArray(detail)){
        const messages = detail.map(item => {
            if(typeof item === 'string') return item;
            const loc = Array.isArray(item?.loc) ? item.loc.filter(x => x !== 'body').join('.') : '';
            const msg = item?.msg || item?.message || JSON.stringify(item);
            return loc ? `${loc}: ${msg}` : msg;
        }).filter(Boolean);
        return messages.join('\n') || fallback;
    }
    if(detail && typeof detail === 'object') return detail.message || detail.msg || JSON.stringify(detail);
    try {
        return JSON.stringify(data);
    } catch(e) {
        return fallback;
    }
}
async function responseErrorMessage(response, fallback=tr('chat.requestFailed')){
    try {
        const data = await response.clone().json();
        return apiErrorMessage(data, fallback);
    } catch(e) {
        try {
            const text = await response.text();
            return text || fallback;
        } catch(_) {
            return fallback;
        }
    }
}
function clearSmartNodeTransientRunState(node, options={}){
    if(!node) return node;
    node.running = false;
    node.pending = 0;
    node.queued = false;
    delete node.jimengPending;
    delete node.pendingTasks;
    delete node._runMetaTargetId;
    if(options.clearRunHistory){
        delete node.runStartedAt;
        delete node.runFinishedAt;
        delete node.runElapsedMs;
        delete node.runTimerHidden;
    }
    return node;
}
const RECENT_SMART_SETTINGS_KEY = 'smart_canvas_recent_run_settings_v1';
const initialSmartSettings = cloneSmartSettings(settings);
let canvasDefaultSmartSettings = cloneSmartSettings(settings);
let recentSmartSettingsByMode = {};
function smartSettingsModeKey(source=settings){
    const engine = normalizeSmartOnlineEngine(source?.engine);
    if(engine === 'api') return `api:${source?.apiKind === 'video' ? 'video' : 'image'}`;
    if(engine === 'volcengine') return `volcengine:${source?.apiKind === 'video' ? 'video' : 'image'}`;
    if(engine === 'runninghub') return 'runninghub';
    return 'modelscope';
}
function loadRecentSmartSettings(){
    try {
        const data = JSON.parse(localStorage.getItem(RECENT_SMART_SETTINGS_KEY) || '{}');
        recentSmartSettingsByMode = data && typeof data === 'object' ? data : {};
    } catch(e) {
        recentSmartSettingsByMode = {};
    }
}
function saveRecentSmartSettings(){
    localStorage.setItem(RECENT_SMART_SETTINGS_KEY, JSON.stringify(recentSmartSettingsByMode));
}
function recentSmartSettingsForMode(modeKey=''){
    const key = modeKey || recentSmartSettingsByMode.__lastKey || smartSettingsModeKey(settings);
    const saved = recentSmartSettingsByMode[key];
    return saved && typeof saved === 'object' ? cloneSmartSettings(saved) : {};
}
function rememberRecentSmartSettings(source=settings, node=null){
    const clean = stripOutpaintDisplaySettings(settingsForStorage(source), node);
    sanitizeSmartApiSelection(clean);
    if(clean.outpaintResolutionLocked === true && clean.resolution === 'custom'){
        clean.resolution = '1k';
        clean.ratio = clean.ratio || 'square';
        clean.customWidth = '';
        clean.customHeight = '';
        clean.customSize = '';
    }
    delete clean.outpaintResolutionLocked;
    const key = smartSettingsModeKey(clean);
    recentSmartSettingsByMode[key] = settingsForStorage(clean);
    recentSmartSettingsByMode.__lastKey = key;
    saveRecentSmartSettings();
}
function applyRecentSmartSettingsForCurrentMode(){
    const requestedEngine = normalizeSmartOnlineEngine(settings.engine);
    const requestedApiKind = settings.apiKind === 'video' ? 'video' : 'image';
    const key = smartSettingsModeKey(settings);
    const saved = recentSmartSettingsForMode(key);
    if(!Object.keys(saved).length){
        settings.engine = requestedEngine;
        if(isApiLikeEngine(requestedEngine)) settings.apiKind = requestedApiKind;
        clearVolcengineSelectionOutsideVolcengine(settings);
        sanitizeSmartApiSelection(settings);
        return;
    }
    settings = {...settings, ...saved, engine:requestedEngine};
    if(isApiLikeEngine(requestedEngine)) settings.apiKind = requestedApiKind;
    clearVolcengineSelectionOutsideVolcengine(settings);
    sanitizeSmartApiSelection(settings);
}
function clearVolcengineSelectionOutsideVolcengine(target=settings){
    if(!target || typeof target !== 'object' || target.engine === 'volcengine') return target;
    if(target.provider_id === 'volcengine') target.provider_id = '';
    if(target.videoProvider === 'volcengine') target.videoProvider = '';
    return target;
}
function isSmartImageNode(node){
    return Boolean(node && (node.type === 'smart-image' || !node.type));
}
function isSmartConversationNode(node){
    return Boolean(node && node.type === 'smart-conversation');
}
function isSmartAgentNode(node){
    return Boolean(node && node.type === 'smart-agent');
}
function isSmartAgentTaskNode(node){
    return Boolean(node && node.type === 'agent-task');
}
function isSmartGroupNode(node){
    return Boolean(node && node.type === 'smart-group');
}
function isSmartRunnableNode(node){
    return Boolean(isSmartImageNode(node) || isSmartGroupNode(node));
}
function smartImageMode(node){
    return 'self';
}
function smartImageUsesWorkflowInput(node, ctx=smartLoopContext){
    return Boolean(isSmartImageNode(node) && ctx?.forceWorkflow);
}
function normalizeLegacySmartNode(node){
    if(!node || typeof node !== 'object') return node;
    if(node.type === 'smart-container'){
        const fallbackImage = node.inputImage?.url ? stripImageGenerationMeta({
            url:node.inputImage.url,
            name:node.inputImage.name || 'image',
            kind:node.inputImage.kind || mediaKindForItem(node.inputImage),
            natural_w:Number(node.inputImage.natural_w || 0),
            natural_h:Number(node.inputImage.natural_h || 0)
        }) : null;
        const images = Array.isArray(node.images) && node.images.length
            ? node.images
            : (fallbackImage ? [fallbackImage] : []);
        const normalized = {
            ...node,
            type:'smart-image',
            title:images.length > 1 ? 'Group' : (images.length ? 'Image' : tr('smart.createImportNode')),
            images
        };
        delete normalized.imageMode;
        delete normalized.inputImage;
        delete normalized.steps;
        delete normalized.resultGrouping;
        return normalized;
    }
    if(!node.type) node.type = 'smart-image';
    if(node.type === 'smart-image') delete node.imageMode;
    if(node.type === 'smart-image' && node.historyFor) node.isHistoryGroup = true;
    return node;
}
function parseSizePair(value){
    const match = String(value || '').match(/(\d+)\s*x\s*(\d+)/i);
    return match ? {width:Number(match[1]), height:Number(match[2])} : null;
}
function reducedAspectRatio(width, height){
    let w = Math.max(1, Math.round(Number(width) || 1));
    let h = Math.max(1, Math.round(Number(height) || 1));
    let a = w, b = h;
    while(b){ const remainder = a % b; a = b; b = remainder; }
    return `${w / a}:${h / a}`;
}
function nearestFourKSizeFor(width, height){
    const w = Math.max(1, Number(width) || 1);
    const h = Math.max(1, Number(height) || 1);
    const ratio = w / h;
    let best = null;
    Object.entries(SIZE_MAP).forEach(([key, values]) => {
        const size = parseSizePair(values?.['4k']);
        if(!size) return;
        const score = Math.abs(Math.log(ratio / (size.width / size.height)));
        if(!best || score < best.score) best = {...size, key, score};
    });
    return best;
}
function exceedsFourKStandard(width, height){
    const standard = nearestFourKSizeFor(width, height);
    if(!standard) return false;
    return Number(width) > standard.width || Number(height) > standard.height;
}
function smartSettingsForNode(node){
    const nodeSettings = stripOutpaintDisplaySettings(node?.runSettings || {}, node);
    const recentSettings = Object.keys(nodeSettings).length ? {} : recentSmartSettingsForMode();
    const base = {
        ...cloneSmartSettings(canvasDefaultSmartSettings || initialSmartSettings),
        ...recentSettings,
        ...nodeSettings
    };
    normalizeSmartVideoModeSettings(base, true);
    return withOutpaintDisplaySettings(node, base);
}
function activeSettingsSubject(){
    const active = canvasState.activeComposerSubject?.id
        ? (canvasState.nodes.find(n => n.id === canvasState.activeComposerSubject.id) || canvasState.activeComposerSubject)
        : selectedNode();
    return isSmartRunnableNode(active) ? active : null;
}
function activeComposerNode(){
    if(!canvasState.lastComposerNodeId) return null;
    const id = String(canvasState.lastComposerNodeId).split(':')[0] || '';
    const node = canvasState.nodes.find(n => n.id === id);
    return isSmartRunnableNode(node) ? node : null;
}
function persistActiveSmartSettings(){
    if(!composer?.classList?.contains('open')) return;
    const subject = activeComposerNode();
    if(!subject) return;
    subject.runSettings = settingsForStorage(settings);
    rememberRecentSmartSettings(settings, subject);
}
async function backToProjectList(){
    savePromptDraftForCurrent();
    if(canvas?.format === 'firefly-canvas-v1' || params.get('project_mode') === '1'){
        clearTimeout(debouncedSaveTimer);
        debouncedSaveTimer = null;
        setProjectSaveState('saving', tr('smart.projectSaving'));
        while(canvasSyncInFlight) await new Promise(resolve => setTimeout(resolve, 50));
        clearTimeout(debouncedSaveTimer);
        debouncedSaveTimer = null;
        if(await saveCanvas() === false) return;
        window.location.href = '/';
        return;
    }
    window.location.href = '/';
}
function promptPlainText(){
    return originalPromptTextFromParts(collectPromptParts());
}
function setPromptInputLocked(locked){
    promptInput.dataset.promptLocked = locked ? '1' : '0';
    promptInput.setAttribute('contenteditable', locked ? 'false' : 'true');
    promptInput.classList.toggle('prompt-input-locked', Boolean(locked));
    if(locked) closeMentionPicker();
}
function setPromptText(text){
    promptInput.textContent = text || '';
}
function clearPromptInput(options={}){
    if(options.preserveDraft){
        promptInput.dataset.preserveDraftOnce = '1';
        closeMentionPicker();
        return;
    }
    promptInput.textContent = '';
    closeMentionPicker();
    if(canvasState.activeComposerSubject){
        canvasState.activeComposerSubject.promptDraftHtml = '';
        canvasState.activeComposerSubject.promptDraftText = '';
    }
}
function applyTheme(theme){
    const dark = theme === 'dark';
    document.documentElement.classList.toggle('theme-dark', dark);
    document.documentElement.classList.toggle('studio-theme-dark', dark);
    document.body?.classList.toggle('theme-dark', dark);
    document.body?.classList.toggle('studio-theme-dark', dark);
}
function toast(text){
    const el = document.getElementById('toast');
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.remove('show'), 1800);
}
let generationCompleteSoundAt = 0;
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
function selectedNode(){ return canvasState.nodes.find(n => n.id === canvasState.selectedId) || null; }
// 任务等待期间，服务端 WebSocket 可能用最新画布替换 canvasState.nodes 数组；不要继续读旧的节点对象。
function liveNode(node){ return node?.id ? (canvasState.nodes.find(item => item.id === node.id) || node) : node; }
function clearSelection(){
    savePromptDraftForCurrent();
    canvasState.selectedId = '';
    canvasState.selectedIds = [];
    canvasState.selectedImage = {nodeId:'', index:-1};
}
function clearImageClickTimer(){
    if(imageClickTimer){
        clearTimeout(imageClickTimer);
        imageClickTimer = null;
    }
}
let smartSelectionUiNodeIds = new Set();
let smartSelectionUiImage = {nodeId:'', index:-1};
function syncSelectionUi(){
    const ids = selectedNodeIds();
    const nextIds = new Set(ids);
    const touchedIds = new Set([...smartSelectionUiNodeIds, ...nextIds]);
    if(smartSelectionUiImage.nodeId) touchedIds.add(smartSelectionUiImage.nodeId);
    if(canvasState.selectedImage.nodeId) touchedIds.add(canvasState.selectedImage.nodeId);
    world.classList.toggle('smart-multi-selected', ids.length > 1);
    if(smartArrangeBtn) smartArrangeBtn.disabled = ids.length < 2;
    smartNodeElementsByIds(touchedIds).forEach(el => {
        const id = el.dataset.id || '';
        el.classList.toggle('selected', isNodeSelected(id));
        el.querySelectorAll('.thumb-item,.image-wrap').forEach(item => {
            const targetNodeId = item.dataset.refNodeId || id;
            const index = Number(item.dataset.refImageIndex ?? item.dataset.imageIndex ?? 0);
            item.classList.toggle('image-selected', canvasState.selectedImage.nodeId === targetNodeId && canvasState.selectedImage.index === index);
        });
    });
    smartSelectionUiNodeIds = nextIds;
    smartSelectionUiImage = {nodeId:canvasState.selectedImage.nodeId || '', index:Number(canvasState.selectedImage.index ?? -1)};
    syncSmartSelectedImageResolution(world);
    syncRunButtonState();
    scheduleConnectionLayerRefresh();
}
function isNodeSelected(id){
    return canvasState.selectedId === id || canvasState.selectedIds.includes(id);
}
function selectedNodeIds(){
    return canvasState.selectedIds.length ? canvasState.selectedIds.slice() : (canvasState.selectedId ? [canvasState.selectedId] : []);
}
function isEditableTarget(target){
    const el = target || document.activeElement;
    return !!el?.closest?.('input, textarea, select, option, [contenteditable="true"], .prompt-node-control, .prompt-input');
}
function safeScale(value){
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? n : 1;
}
function nodeScale(node){
    const v = Number(node?.scale);
    if((node?.images || []).length > 1 && v === MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE) return MEDIA_GROUP_DEFAULT_SCALE;
    return Number.isFinite(v) && v > 0 ? v : 1;
}
const MEDIA_NODE_DEFAULT_SCALE = 2;
const MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE = 1.6;
const MEDIA_GROUP_DEFAULT_SCALE = 0.8;
const ZOOM_PREVIEW_NODE_DEFAULT_SCALE = 1;
const ZOOM_PREVIEW_NODE_MAX_SCALE = 1.15;
const MEDIA_GROUP_THUMB_BASE = 224;
const MEDIA_GROUP_MAX_VISIBLE_ROWS = 3;
// 智能分组卡片内的图片网格：超过这么多排就出现纵向滚动（区别于多图节点的 3 排）。
const SMART_GROUP_MAX_VISIBLE_ROWS = 4;

// ========== 批量生图缩略图规范（单一数据源） ==========
// 参考生图历史面板：136px 宽、4:3 比例的卡片

// 组框布局规范（基于现有 CSS: .smart-group-node padding:16px）

// 批量生图独立节点外框 padding 规范：占位 / 逐张写回 / 完成三阶段必须共用同一组值，
// 保证外框尺寸恒定不跳变。PAD_H/V 是相对网格的外框增量：
// 水平 = 节点左右 padding 16+16（网格横向铺满内容区）；
// 垂直 = 节点上下 padding 16+16 + 网格上下居中 16+16（网格在内容区内上下留 16px）。
// 最终视觉边距：左右 16px、上下 32px，三阶段一致。
// #84：批量 pending 节点底部状态条高度（40px 条 + 8px 间隙），只加在 pending 阶段，
// 完成时回到完成态几何（gridH + PAD_V），状态条消失即「生成完成」的视觉信号。

// 单图节点规范

// ========== 布局计算工具函数（单一数据源） ==========

// 解析图片比例（优先级：_generationAspect → 首图真实比例 → 请求尺寸 → 1:1）
function resolveImageAspect(node){
    // 1. 提交生成时固化的比例
    const fixed = node?._generationAspect;
    if(fixed && Number(fixed.w) > 0 && Number(fixed.h) > 0){
        return {w:Math.max(1, Number(fixed.w)), h:Math.max(1, Number(fixed.h))};
    }
    // 2. 已写回的首图真实宽高比
    const firstDone = (node?.images || []).find(img => Number(img?.width) > 0 && Number(img?.height) > 0);
    if(firstDone){
        return {w:Math.max(1, Number(firstDone.width)), h:Math.max(1, Number(firstDone.height))};
    }
    // 3. 请求尺寸
    const pendingTask0 = smartPendingTasks(node)[0] || {};
    const sizeText = pendingTask0.size || pendingTask0.requestPayload?.size || pendingTask0.request_payload?.size || '';
    return parseSizeAspect(String(sizeText));
}

// 计算批量缩略图网格尺寸（count > 1）

// 计算单图布局尺寸（原比例）

// 解组后计算单图尺寸（参考批量缩略图宽度 136px）
// 分组可缩小到的最小尺寸（缩小分组时组内图片随之等比缩小，靠这个区间产生缩放系数）。
// 组内成员（提示词/循环）的最大缩放。已移除“放大不超过原始”的限制：向外拉分组时成员随之放大。
function mediaNodeDefaultScale(node){
    if((node?.images || []).length > 1 && !Number.isFinite(Number(node?.scale))) return MEDIA_GROUP_DEFAULT_SCALE;
    return Number.isFinite(Number(node?.scale)) && Number(node.scale) > 0 ? Number(node.scale) : MEDIA_NODE_DEFAULT_SCALE;
}
function smartGroupMembers(node){
    if(!isSmartGroupNode(node)) return [];
    const ids = Array.isArray(node.items) ? node.items : [];
    const seen = new Set([node.id]);
    return ids.map(id => canvasState.nodes.find(n => n.id === id)).filter(member => {
        if(!member || seen.has(member.id) || isSmartGroupNode(member)) return false;
        seen.add(member.id);
        return true;
    });
}
function smartGroupCompactMembers(node){
    return smartGroupMembers(node).filter(member => member?.type === 'smart-prompt' || member?.type === 'smart-loop');
}
function isSmartGroupCompactMember(node){
    return Boolean(node && (node.type === 'smart-prompt' || node.type === 'smart-loop') && smartGroupContainingNode(node.id));
}
// 可作为分组"画布成员节点"的类型（图片走网格吸收，prompt/loop 走成员节点，分组走合并）。其余类型（agent/conversation/local-edit/agent-task）拒绝进组。
function isSmartGroupableNode(node){
    return Boolean(node && (isSmartImageNode(node) || node.type === 'smart-prompt' || node.type === 'smart-loop' || isSmartGroupNode(node)));
}
// 分组当前缩放比例（1=原始）。分组就像“画布中的画布”：缩放分组时组内所有成员（含提示词）整体等比缩放+
// 重排。缩放过程用每次手势开始时的快照实时计算（见 resize 处理），不存持久基准，避免移动成员后再缩放位置回退。
// _memberZoom 仅用于：新入组的成员按它缩小，以匹配已经缩小的分组。
// 让新入组的成员贴合分组当前缩放（只改尺寸、保持落点不跳动）。
function scaleSmartGroupMemberToZoom(group, member, zoom){
    if(!member || !(zoom > 0) || zoom === 1) return;
    const r = nodeRect(member);
    member.w = Math.max(40, Math.round((Number(r.width) || 0) * zoom));
    member.h = Math.max(40, Math.round((Number(r.height) || 0) * zoom));
    if(isSmartImageNode(member)) member.scale = 1;
}
// 把指向 fromId 的连线/输入引用改接到 toId（吸收图片入组后保留“来源 → 分组”的连线），并去重去自环。
// 把一张图片节点的图片吸收进分组（收进卡片内的缩略图网格），然后删除该图片节点。
function absorbImageNodeIntoSmartGroup(group, child){
    const add = (child.images || []).map(img => stripImageGenerationMeta({...img}));
    if(!add.length) return false;
    group.images = [...(group.images || []), ...add];
    // 清掉显式尺寸，让缩略图网格按图片数自动整理排列（“放入图片自动整理”）。
    delete group.w; delete group.h;
    rerouteSmartConnections(child.id, group.id);
    canvasState.nodes = canvasState.nodes.filter(n => n.id !== child.id); if(canvas) canvas.nodes = canvasState.nodes;
    canvasState.nodes.forEach(g => { if(isSmartGroupNode(g) && Array.isArray(g.items)) g.items = g.items.filter(id => id !== child.id); });
    return true;
}
function addNodeToSmartGroup(group, child){
    if(!isSmartGroupNode(group) || !child || child.id === group.id) return false;
    const items = Array.isArray(group.items) ? group.items.slice() : [];
    const zoom = smartGroupZoom(group);
    if(isSmartGroupNode(child)){
        // 把一个分组拖进另一个分组：吸收它的图片到本组网格，把它的非图片成员并入本组，然后删除被拖分组本体。
        const mergedImages = (child.images || []).map(img => stripImageGenerationMeta({...img}));
        group.images = [...(group.images || []), ...mergedImages];
        if(mergedImages.length){ delete group.w; delete group.h; }
        const childMemberIds = smartGroupMembers(child)
            .map(m => m.id)
            .filter(id => id !== group.id && !items.includes(id));
        group.items = [...items, ...childMemberIds];
        childMemberIds.forEach(id => {
            const m = canvasState.nodes.find(n => n.id === id);
            if(m) scaleSmartGroupMemberToZoom(group, m, zoom);
        });
        rerouteSmartConnections(child.id, group.id);
        canvasState.nodes = canvasState.nodes.filter(n => n.id !== child.id); if(canvas) canvas.nodes = canvasState.nodes;
        canvasState.nodes.forEach(g => { if(isSmartGroupNode(g) && Array.isArray(g.items)) g.items = g.items.filter(id => id !== child.id); });
        return true;
    }
    // 图片节点：收进卡片内的缩略图网格（不再作为画布上的独立节点）。
    if(isSmartImageNode(child)) return absorbImageNodeIntoSmartGroup(group, child);
    // 提示词 / 循环：仍作为画布上的成员节点。其余类型（agent/conversation/local-edit/agent-task）拒绝进组。
    if(child.type !== 'smart-prompt' && child.type !== 'smart-loop') return false;
    if(items.includes(child.id)) return false;
    group.items = [...items, child.id];
    scaleSmartGroupMemberToZoom(group, child, zoom);
    return true;
}
// 找到把某个节点作为成员的智能分组（用于双击组内图片时按整组左右切换）。
function smartGroupContainingNode(nodeId){
    if(!nodeId) return null;
    return canvasState.nodes.find(n => isSmartGroupNode(n) && Array.isArray(n.items) && n.items.includes(nodeId)) || null;
}
// 节点所属的“分组作用域”ID：成员返回其分组，分组本体返回自身，否则空。
// 用于连线合并/隐藏：同一作用域内部的连线属于分组内部关系，无需画出。
function smartGroupScopeId(nodeId){
    const group = smartGroupContainingNode(nodeId);
    if(group) return group.id;
    const node = canvasState.nodes.find(n => n.id === nodeId);
    return isSmartGroupNode(node) ? node.id : '';
}
// 汇总分组内所有图片成员的图片，按阅读顺序（先行后列：成员当前 y 再 x）排成一维序列。
// 返回 [{nodeId, index, source, item}]——source 是成员节点里真实的图片对象（写回自然尺寸用），
// item 是 imageForDisplay 后的展示对象。预览左右切换、批量下载、宫格拼接都以它为数据源。
function smartGroupImageRefs(group){
    if(!isSmartGroupNode(group)) return [];
    const refs = [];
    (group.images || []).forEach((img, index) => {
        const item = imageForDisplay(img);
        if(item?.url) refs.push({nodeId:group.id, index, source:img, item});
    });
    const members = smartGroupMembers(group)
        .filter(isSmartImageNode)
        .slice()
        .sort((a, b) => {
            const ra = nodeRect(a), rb = nodeRect(b);
            const dy = (Number(ra.y) || 0) - (Number(rb.y) || 0);
            if(Math.abs(dy) > 24) return dy;
            return (Number(ra.x) || 0) - (Number(rb.x) || 0);
        });
    members.forEach(node => {
        (node.images || []).forEach((img, index) => {
            const item = imageForDisplay(img);
            if(item?.url) refs.push({nodeId:node.id, index, source:img, item});
        });
    });
    return refs;
}
// 组框内“生成中”占位成员的额外高度（它们排在网格下方、统一尺寸），
// 让组框高度从一开始就包住占位成员，避免成员漂在组框外。
function smartGroupPendingMembersExtraHeight(node){
    const pendingMembers = smartGroupMembers(node).filter(member => isSmartImageNode(member)
        && !(member.images || []).some(img => img?.url)
        && (member.pending || smartPendingTasks(member).length));
    if(!pendingMembers.length) return 0;
    let extra = 0;
    pendingMembers.forEach(member => {
        const memberLayout = imageLayout(member.images || [], 1, member);
        extra += Math.max(40, Math.round(Number(memberLayout.height) || 102)) + 12;
    });
    return extra;
}
function smartGroupThumbLayout(node){
    const refs = smartGroupImageRefs(node).filter(ref => ref.item?.url);
    if(!refs.length) return null;
    const compactMembers = smartGroupCompactMembers(node);
    const count = refs.length + compactMembers.length;
    const pendingExtra = smartGroupPendingMembersExtraHeight(node);
    
    // 单图：原比例布局
    if(count === 1){
        // 从首图数据解析比例（node 是组框，没有 _generationAspect）
        const img = refs[0].item;
        const aspect = img.width && img.height
            ? {w:Math.max(1, Number(img.width)), h:Math.max(1, Number(img.height))}
            : BATCH_THUMB.ASPECT;
        const layout = getSingleImageLayoutSize(aspect);
        return {
            refs,
            compactMembers,
            cols:1,
            rows:1,
            visibleRows:1,
            width:layout.groupBoxW,
            height:layout.groupBoxH + pendingExtra,
            thumb:layout.imgW,
            single:true,
            innerW:layout.imgW,
            innerH:layout.imgH
        };
    }
    
    // 批量：固定 4:3 缩略图网格（与生图历史一致）
    const grid = getBatchThumbGridSize(count);
    const maxVisibleRows = compactMembers.length ? count : SMART_GROUP_MAX_VISIBLE_ROWS;
    const visibleRows = Math.min(maxVisibleRows, grid.rows);
    
    return {
        refs,
        compactMembers,
        cols:grid.cols,
        rows:grid.rows,
        visibleRows,
        thumb:BATCH_THUMB.WIDTH,
        thumbH:BATCH_THUMB.HEIGHT,
        gridHeight:grid.gridH,
        width:grid.boxW,
        height:grid.boxH + pendingExtra
    };
}
const SMART_GROUP_ARRANGE_PADDING = 18;
const SMART_GROUP_ARRANGE_GAP = 16;
const SMART_GROUP_ARRANGE_HEADER = 44;
// 把分组内的成员整理成整齐的网格（先行后列保持当前阅读顺序），列数尽量接近正方形，
// 每个成员在所属单元格内居中；最后把分组框尺寸收敛到正好包住所有成员。
function arrangeSmartGroupMembers(group, options={}){
    if(!isSmartGroupNode(group)) return false;
    const hasThumbImages = smartGroupImageRefs(group).some(ref => ref.item?.url);
    if(hasThumbImages){
        const compactMembers = smartGroupCompactMembers(group);
        if(!options.skipUndo) pushUndo();
        const layout = smartGroupThumbLayout(group);
        if(!layout) return true;
        const refs = layout.refs || [];
        const thumb = layout.single ? Math.round(Number(layout.thumb) || BATCH_THUMB.WIDTH) : BATCH_THUMB.WIDTH;
        const thumbH = layout.single ? Math.round(Number(layout.innerH) || BATCH_THUMB.HEIGHT) : BATCH_THUMB.HEIGHT;
        const gap = BATCH_THUMB.GAP;
        const cols = Math.max(1, Number(layout.cols) || 1);
        const gridW = cols * thumb + Math.max(0, cols - 1) * gap;
        const contentW = Math.max(0, Math.round(Number(layout.width) || SMART_GROUP_DEFAULT_WIDTH) - 32);
        const originX = (Number(group.x) || 0) + SMART_GROUP_LAYOUT.PADDING + Math.max(0, Math.round((contentW - gridW) / 2));
        const originY = (Number(group.y) || 0) + SMART_GROUP_LAYOUT.PADDING + SMART_GROUP_LAYOUT.SUMMARY_H + SMART_GROUP_LAYOUT.SUMMARY_GAP;
        group.w = Math.max(SMART_GROUP_MIN_WIDTH, Math.round(Number(layout.width) || SMART_GROUP_DEFAULT_WIDTH));
        group.h = Math.max(SMART_GROUP_MIN_HEIGHT, Math.round(Number(layout.height) || SMART_GROUP_DEFAULT_HEIGHT));
        const ordered = compactMembers.slice().sort((a, b) => {
            const ra = nodeRect(a), rb = nodeRect(b);
            const dy = (Number(ra.y) || 0) - (Number(rb.y) || 0);
            if(Math.abs(dy) > 24) return dy;
            return (Number(ra.x) || 0) - (Number(rb.x) || 0);
        });
        ordered.forEach((member, memberIndex) => {
            const index = refs.length + memberIndex;
            const col = index % cols;
            const row = Math.floor(index / cols);
            member.x = Math.round(originX + col * (thumb + gap));
            member.y = Math.round(originY + row * (thumbH + gap));
            member.w = thumb;
            member.h = thumbH;
            member.scale = 1;
        });
        if(group._memberZoom !== undefined) group._memberZoom = 1;
        // 生成中的占位成员排到网格下方，使用统一尺寸
        const pendingMembers = smartGroupMembers(group).filter(member => isSmartImageNode(member)
            && !(member.images || []).some(img => img?.url)
            && (member.pending || smartPendingTasks(member).length));
        if(pendingMembers.length){
            let cursorY = originY + Number(layout.gridHeight || 0) + 12;
            pendingMembers.forEach(member => {
                // 统一尺寸：直接用 imageLayout（与渲染同一数据源），保证占位成员摆放尺寸与渲染结果一致
                const memberLayout = imageLayout(member.images || [], 1, member);
                const size = {w:Math.max(40, Math.round(Number(memberLayout.width) || 136)), h:Math.max(40, Math.round(Number(memberLayout.height) || 102))};
                member.x = Math.round((Number(group.x) || 0) + SMART_GROUP_LAYOUT.PADDING + Math.max(0, Math.round(((Number(group.w) || SMART_GROUP_DEFAULT_WIDTH) - 32 - size.w) / 2)));
                member.y = Math.round(cursorY);
                member.w = size.w;
                member.h = size.h;
                member.scale = 1;
                cursorY += size.h + 12;
            });
            const neededW = Math.max(...pendingMembers.map(member => Math.round(Number(member.w) || 0) + 32), 0);
            if(neededW > (Number(group.w) || 0)) group.w = Math.max(SMART_GROUP_MIN_WIDTH, Math.round(neededW));
            const neededBottom = cursorY - 12 + SMART_GROUP_LAYOUT.PADDING;
            if(neededBottom > (Number(group.y) || 0) + (Number(group.h) || 0)) group.h = Math.max(SMART_GROUP_MIN_HEIGHT, Math.round(neededBottom - (Number(group.y) || 0)));
        }
        if(options.syncDom) syncSmartGroupMemberElements(group);
        return true;
    }
    const members = smartGroupMembers(group);
    if(!members.length) return false;
    if(!options.skipUndo) pushUndo();
    const ordered = members.slice().sort((a, b) => {
        const ra = nodeRect(a), rb = nodeRect(b);
        const dy = (Number(ra.y) || 0) - (Number(rb.y) || 0);
        if(Math.abs(dy) > 24) return dy;
        return (Number(ra.x) || 0) - (Number(rb.x) || 0);
    });
    // 归一化图片成员尺寸：清掉入组缩放写入的 w/h，回到自然尺寸。否则反复拖出/拖入会越缩越小，
    // 且“整理”无法恢复——这是用户反馈的“拖出再拖入图片变小、整理也救不回来”的根因。
    ordered.forEach(node => {
        if(isSmartImageNode(node)){
            delete node.w;
            delete node.h;
        }
    });
    const sizes = ordered.map(node => {
        const r = nodeRect(node);
        return {node, w:Math.max(40, Number(r.width) || 120), h:Math.max(40, Number(r.height) || 120)};
    });
    const count = sizes.length;
    const pad = SMART_GROUP_ARRANGE_PADDING;
    const gap = SMART_GROUP_ARRANGE_GAP;
    const headerH = SMART_GROUP_ARRANGE_HEADER;
    const cols = Math.max(1, Math.min(count, Math.round(Math.sqrt(count)) || 1));
    const rows = Math.ceil(count / cols);
    const colW = new Array(cols).fill(0);
    const rowH = new Array(rows).fill(0);
    sizes.forEach((s, i) => {
        const c = i % cols, r = Math.floor(i / cols);
        colW[c] = Math.max(colW[c], s.w);
        rowH[r] = Math.max(rowH[r], s.h);
    });
    const colX = [];
    let accX = 0;
    for(let c = 0; c < cols; c++){ colX[c] = accX; accX += colW[c] + gap; }
    const rowY = [];
    let accY = 0;
    for(let r = 0; r < rows; r++){ rowY[r] = accY; accY += rowH[r] + gap; }
    const originX = (Number(group.x) || 0) + pad;
    const originY = (Number(group.y) || 0) + headerH + pad;
    sizes.forEach((s, i) => {
        const c = i % cols, r = Math.floor(i / cols);
        s.node.x = Math.round(originX + colX[c] + (colW[c] - s.w) / 2);
        s.node.y = Math.round(originY + rowY[r] + (rowH[r] - s.h) / 2);
    });
    const totalW = colW.reduce((a, b) => a + b, 0) + gap * (cols - 1) + pad * 2;
    const totalH = rowH.reduce((a, b) => a + b, 0) + gap * (rows - 1) + pad * 2 + headerH;
    group.w = Math.max(SMART_GROUP_MIN_WIDTH, Math.round(totalW));
    group.h = Math.max(SMART_GROUP_MIN_HEIGHT, Math.round(totalH));
    // 成员已回到自然尺寸，分组缩放基准随之归零，避免后续再缩放时跳变。
    if(group._memberZoom !== undefined) group._memberZoom = 1;
    return true;
}
function mediaLayoutSize(img){
    const width = Number(img?.natural_w || img?.width || img?.w || img?.layout_w || img?.preview_w || 0);
    const height = Number(img?.natural_h || img?.height || img?.h || img?.layout_h || img?.preview_h || 0);
    return width > 0 && height > 0 ? {width, height} : {width:0, height:0};
}
function copyMediaSizeFields(source, target={}){
    if(!source || typeof source !== 'object') return target;
    ['natural_w','natural_h','width','height','w','h','layout_w','layout_h'].forEach(key => {
        const n = Number(source[key]);
        if(Number.isFinite(n) && n > 0) target[key] = n;
    });
    return target;
}
function singleImageLayout(image, node, scale){
    const explicitW = Number(node?.w);
    const explicitH = Number(node?.h);
    if(Number.isFinite(explicitW) && explicitW > 24 && Number.isFinite(explicitH) && explicitH > 24){
        return {cols:1, rows:1, width:Math.round(explicitW), height:Math.round(explicitH), thumb:Math.round(96 * scale), single:true};
    }
    // 音频没有自然宽高，否则会套用图片的 260x180 默认框，导致卡片四周大片空白。给一个贴合内容的紧凑尺寸。
    if(isAudioMediaItem(image)){
        return {cols:1, rows:1, width:Math.round(288 * scale), height:Math.round(150 * scale), thumb:Math.round(96 * scale), single:true};
    }
    const layoutSize = mediaLayoutSize(image);
    const naturalW = layoutSize.width;
    const naturalH = layoutSize.height;
    if(naturalW > 0 && naturalH > 0){
        const maxW = 260 * scale;
        const maxH = 220 * scale;
        const fit = Math.min(maxW / naturalW, maxH / naturalH);
        return {
            cols:1,
            rows:1,
            width:Math.max(72, Math.round(naturalW * fit)),
            height:Math.max(72, Math.round(naturalH * fit)),
            thumb:Math.round(96 * scale),
            single:true
        };
    }
    return {cols:1, rows:1, width:Math.round(260*scale), height:Math.round(180*scale), thumb:Math.round(96*scale), single:true};
}
function smartNodeInputThumbRows(count){
    return count ? Math.ceil(Math.min(10, count) / 5) : 0;
}
function smartNodeInputThumbsHeight(images){
    const rows = smartNodeInputThumbRows((images || []).length);
    return rows ? rows * 44 + (rows - 1) * 6 + 8 : 0;
}
function promptNodeInputImages(node){
    return promptNodeInputMediaForLLM(node).filter(img => img?.url);
}
function promptNodeInputMediaForLLM(node){
    const refs = smartImageUsesWorkflowInput(node) ? workflowInputImagesFor(node) : inputImagesFor(node);
    return (refs || []).filter(ref => ref?.url);
}
function smartNodeInputThumbsHtml(images, opts={}){
    const refs = (images || []).filter(img => img?.url);
    if(!refs.length) return '';
    const limit = Math.min(10, refs.length);
    const items = refs.slice(0, limit).map((img, index) => {
        const label = opts.labelPrefix ? `${opts.labelPrefix}${index + 1}` : (window.StudioI18n?.lang?.() === 'en' ? `Image ${index + 1}` : `图${index + 1}`);
        const media = isAudioMediaItem(img)
            ? `<div class="media-thumb audio-thumb"><i data-lucide="file-audio"></i><span>${escapeHtml(img.name || 'Audio')}</span></div>`
            : isVideoMediaItem(img)
            ? smartVideoPreviewHtml(img, 256, 'alt=""')
            : smartPreviewImgHtml(img, 256, 'alt=""');
        return `<div class="smart-node-input-thumb" title="${escapeHtml(label)}">${media}<span class="smart-node-input-badge">${escapeHtml(label)}</span></div>`;
    }).join('');
    const more = refs.length > limit ? `<div class="smart-node-input-thumb smart-node-input-more">+${refs.length - limit}</div>` : '';
    return `<div class="smart-node-input-thumbs">${items}${more}</div>`;
}
const PROMPT_LLM_INSTRUCTION_DEFAULT_H = 58;
const PROMPT_LLM_INSTRUCTION_MIN_H = 40;
const PROMPT_LLM_INSTRUCTION_MAX_H = 400;
const PROMPT_SPLIT_PREVIEW_DEFAULT_H = 70;
const PROMPT_SPLIT_PREVIEW_MIN_H = 40;
const PROMPT_SPLIT_PREVIEW_MAX_H = 220;
const PROMPT_SPLIT_RESIZE_BAR_H = 9;
function promptLlmInstructionHeight(node){
    const h = Number(node?.llmInstructionHeight);
    if(!Number.isFinite(h)) return PROMPT_LLM_INSTRUCTION_DEFAULT_H;
    return Math.max(PROMPT_LLM_INSTRUCTION_MIN_H, Math.min(PROMPT_LLM_INSTRUCTION_MAX_H, Math.round(h)));
}
function promptNodeSeparator(node){
    const raw = String(node?.promptSeparator ?? ';');
    return raw === '' ? ';' : raw;
}
function promptNodePromptItems(node){
    const text = String(node?.text || '').trim();
    if(!text) return [];
    if(node?.promptSplitEnabled !== true) return [text];
    const sep = promptNodeSeparator(node);
    if(!sep) return [text];
    const items = text.split(sep).map(item => item.trim()).filter(Boolean);
    return items.length > 1 ? items : [text];
}
function promptNodeSplitExtraHeight(node){
    if(node?.promptSplitEnabled !== true) return 0;
    return 25 + promptNodeSplitPreviewHeight(node) + PROMPT_SPLIT_RESIZE_BAR_H;
}
function promptNodeSplitPreviewHeight(node){
    const h = Number(node?.promptSplitPreviewHeight);
    if(!Number.isFinite(h)) return PROMPT_SPLIT_PREVIEW_DEFAULT_H;
    return Math.max(PROMPT_SPLIT_PREVIEW_MIN_H, Math.min(PROMPT_SPLIT_PREVIEW_MAX_H, Math.round(h)));
}
function syncPromptNodeHeightForSplit(node, prevExtra=0){
    if(!node) return;
    const nextExtra = promptNodeSplitExtraHeight(node);
    const explicitH = Number(node.h);
    const currentH = Number.isFinite(explicitH) ? explicitH : 0;
    const fallbackH = promptNodeMinHeight(node);
    node.h = Math.max(fallbackH, currentH ? currentH - Math.max(0, prevExtra) + nextExtra : fallbackH);
    node.w = Math.max(Number(node.w) || 0, 316);
}
function promptNodeMinHeight(node){
    return node?.llmEnabled ? promptNodeExpandedHeight(node) : 240 + promptNodeSplitExtraHeight(node);
}
function promptTextItemsForNode(node, ctx=smartLoopContext){
    if(!node) return [];
    if(node.type === 'smart-prompt') return promptNodePromptItems(node);
    if(node.type === 'smart-loop'){
        const text = smartLoopPrompt(node, ctx);
        return text ? [text] : [];
    }
    if(node.type === 'smart-group') return smartGroupMembers(node).flatMap(member => promptTextItemsForNode(member, ctx));
    return [];
}
function promptNodeUpstreamPromptItems(node, ctx=smartLoopContext){
    const seen = new Set();
    return inputNodesFor(node).flatMap(input => promptTextItemsForNode(input, ctx)).map(text => String(text || '').trim()).filter(text => {
        if(!text || seen.has(text)) return false;
        seen.add(text);
        return true;
    });
}
function promptNodeUpstreamPromptText(node, ctx=smartLoopContext){
    return promptNodeUpstreamPromptItems(node, ctx).join('\n\n');
}
function promptNodeLLMInputText(node, ctx=smartLoopContext){
    const upstream = promptNodeUpstreamPromptText(node, ctx).trim();
    const instruction = String(node?.llmInstruction || '').trim() || promptNodePromptItems(node).join('\n\n').trim();
    return [upstream, instruction].filter(Boolean).join('\n\n');
}
function promptNodeExpandedHeight(node){
    // 指令文本框（发送给 LLM 的内容）可拖动加高，超出默认高度的部分要叠加进节点高度。
    const extra = Math.max(0, promptLlmInstructionHeight(node) - PROMPT_LLM_INSTRUCTION_DEFAULT_H);
    const upstreamExtra = node?.llmEnabled && promptNodeUpstreamPromptItems(node).length ? 74 : 0;
    return (node?.llmSystemEnabled ? 420 : 360) + smartNodeInputThumbsHeight(promptNodeInputImages(node)) + extra + upstreamExtra + promptNodeSplitExtraHeight(node);
}
function promptNodeLayoutSize(node){
    const oldCollapsedH = 230;
    const oldExpandedH = node?.llmSystemEnabled ? 400 : 340;
    const explicitW = Number(node?.w);
    const explicitH = Number(node?.h);
    if(isSmartGroupCompactMember(node) && Number.isFinite(explicitW) && explicitW > 24 && Number.isFinite(explicitH) && explicitH > 24){
        return {width:Math.round(explicitW), height:Math.round(explicitH)};
    }
    const width = !Number.isFinite(explicitW) || explicitW === 360 ? 316 : explicitW;
    const fallbackH = promptNodeMinHeight(node);
    const legacyExpandedH = node?.llmSystemEnabled ? 344 : 292;
    const height = !Number.isFinite(explicitH) || explicitH === 194 || explicitH === oldCollapsedH || explicitH === oldExpandedH || explicitH === legacyExpandedH
        ? fallbackH
        : Math.max(explicitH, fallbackH);
    return {width:Math.round(width), height:Math.round(height)};
}
function conversationNodeLayoutSize(node){
    const width = Number(node?.w);
    const height = Number(node?.h);
    // 拖小低于阈值时钳制到阈值（280/240），不弹回默认值（390/430）
    return {
        width:Math.round(Number.isFinite(width) ? Math.max(280, width) : 390),
        height:node?.conversation_collapsed === true
            ? 96
            : Math.round(Number.isFinite(height) ? Math.max(240, height) : 430),
    };
}
// 智能分组的图片网格布局：跟多图节点一致，但可见排数上限为 4（超过出现滚动），且缩略图无放大上限
//（用户拉大分组时图片随之变大，不再封顶在原始尺寸）。
function parseSizeAspect(sizeText){
    // 解析 sizeForRun 输出的尺寸串（如 1024x768 / 1024×768）或比例串（4:3）。
    const text = String(sizeText || '').trim();
    const dims = text.match(/(\d+)\s*[x×]\s*(\d+)/i);
    if(dims) return {w:Math.max(1, Number(dims[1])), h:Math.max(1, Number(dims[2]))};
    const ratio = text.match(/(\d+)\s*[:：]\s*(\d+)/);
    if(ratio) return {w:Math.max(1, Number(ratio[1])), h:Math.max(1, Number(ratio[2]))};
    return {w:1, h:1};
}
// imageLayout 对外框几何加锁（不尊重 node.w/h）的节点判定：
// - pending/写回/排队分支：按任务固化的 _batchTotal 与原比例锁几何（三阶段一致）
// - 批量完成分支：按 _batchTotal / _generationAspect 锁网格几何（防几何漂移，有意锁定）
// 这类节点的 resize 手柄拖拽会被 imageLayout 静默拉回 → 不渲染手柄，避免用户误以为 bug
function isLockedBatchGenNode(node){
    if(!node || node.type !== 'smart-image' || node.smartGroupMember) return false;
    if(node.id && smartGroupContainingNode(node.id)) return false;
    const count = Array.isArray(node?.images) ? node.images.length : 0;
    const pending = Number(node?.pending) > 0 || Boolean(node?.queued);
    // #149 A3：生成中（pending/queued）才锁尺寸；空节点（未生成）允许用户预设大小。
    if(pending) return true;
    if(count === 1) return false; // singleImageLayout 尊重用户拉伸尺寸，手柄有效
    const genAspect = node?._generationAspect || null;
    const batchTotal = Math.max(0, Math.min(8, Number(node?._batchTotal) || 0));
    return batchTotal > 1 || (Number(genAspect?.w) > 0 && Number(genAspect?.h) > 0);
}
function imageLayout(images, scale=1, node=null){
    if(node?.type === 'smart-group'){
        const groupThumbLayout = smartGroupThumbLayout(node);
        if(groupThumbLayout) return groupThumbLayout;
        return {cols:1, rows:1, ...smartGroupLayoutSize(node), thumb:96, single:true};
    }
    if(isSmartConversationNode(node)) return {cols:1, rows:1, ...conversationNodeLayoutSize(node), thumb:96, single:true};
    if(node?.type === 'smart-prompt') return {cols:1, rows:1, ...promptNodeLayoutSize(node), thumb:96, single:true};
    if(node?.type === 'agent-task'){
        const explicitW = Number(node.w);
        const explicitH = Number(node.h);
        return {
            cols:1,
            rows:1,
            width:Math.round(Number.isFinite(explicitW) && explicitW > 24 ? explicitW : 300),
            height:Math.round(Number.isFinite(explicitH) && explicitH > 24 ? explicitH : 236),
            thumb:96,
            single:true,
        };
    }
    if(node?.type === 'smart-agent'){
        // Agent 节点是 TUI 型节点：尺寸由节点自身 w/h 决定（可拖拽拉伸），
        // 不走媒体节点的图片网格占位布局，否则 resize 设置的 w/h 不生效
        // （用户反馈"agent 节点没法拉伸"——渲染尺寸恒为占位图大小，与 w/h 无关）。
        const explicitW = Number(node.w);
        const explicitH = Number(node.h);
        return {
            cols:1,
            rows:1,
            width:Math.round(Number.isFinite(explicitW) && explicitW > 24 ? explicitW : 382),
            height:Math.round(Number.isFinite(explicitH) && explicitH > 24 ? explicitH : 360),
            thumb:96,
            single:true,
        };
    }
    if(node?.type === 'local-edit'){
        // 局部编辑是表单型节点：尺寸由节点自身 w/h 决定（可拖拽调大小），
        // 不走媒体节点的单图/批量布局，否则卡片会溢出外框。
        const explicitW = Number(node.w);
        const explicitH = Number(node.h);
        return {
            cols:1,
            rows:1,
            width:Math.round(Number.isFinite(explicitW) && explicitW > 24 ? explicitW : LOCAL_EDIT_NODE_W),
            height:Math.round(Number.isFinite(explicitH) && explicitH > 24 ? explicitH : LOCAL_EDIT_NODE_H),
            thumb:96,
            single:true,
        };
    }
    if(node?.type === 'smart-loop'){
        const explicitW = Number(node.w);
        const explicitH = Number(node.h);
        if(isSmartGroupCompactMember(node) && Number.isFinite(explicitW) && explicitW > 24 && Number.isFinite(explicitH) && explicitH > 24){
            return {cols:1, rows:1, width:Math.round(explicitW), height:Math.round(explicitH), thumb:96, single:true};
        }
        return {cols:1, rows:1, width:Math.round(Number(node.w) || smartLoopWidth(node)), height:Math.round(Math.max(Number(node.h) || 0, smartLoopHeight(node))), thumb:96, single:true};
    }
    const count = (images || []).length;
    const s = node?.type === 'smart-image' || !node?.type ? mediaNodeDefaultScale(node) : (Number.isFinite(scale) && scale > 0 ? scale : 1);
    const pending = Number(node?.pending) > 0 || Boolean(node?.queued);
    if(count === 0 || pending){
        // 生成中（含逐张写回期间）：使用统一布局计算，占位图与完成图尺寸一致。
        // node.pending 会随任务完成递减，以提交时固化的 _batchTotal 为准，保证写回期间网格几何稳定。
        const total = Math.max(1, Math.min(8, Number(node?._batchTotal) || Number(node?.pending) || count || 1));
        const aspect = resolveImageAspect(node);
        
        // 智能分组框内的 pending 成员：使用与组网格一致的统一尺寸，组框包住成员
        if(node?.id && smartGroupContainingNode(node.id)){
            const pad = SMART_GROUP_LAYOUT.PADDING * 2;
            if(total > 1){
                const memberGrid = getBatchThumbGridSize(total);
                return {
                    cols:memberGrid.cols,
                    rows:memberGrid.rows,
                    visibleRows:Math.min(SMART_GROUP_MAX_VISIBLE_ROWS, memberGrid.rows),
                    width:memberGrid.gridW + pad,
                    height:memberGrid.gridH + pad,
                    thumb:BATCH_THUMB.WIDTH,
                    gridHeight:memberGrid.gridH,
                    pendingLock:true
                };
            }
            const memberSize = getUngroupedImageSize(aspect);
            return {cols:1, rows:1, width:memberSize.w, height:memberSize.h, thumb:memberSize.w, single:true, pendingLock:true};
        }
        
        // 批量：固定 4:3 缩略图网格（独立生成卡片：无 summary 行）
        // 与 pendingBoxSize / 完成阶段共用 BATCH_NODE_PAD_H/V，三阶段外框尺寸一致。
        // #84：pending 阶段额外加高 BATCH_STATUSBAR_H，容纳节点底部状态条（完成态不加）。
        if(total > 1){
            const grid = getBatchThumbGridSize(total);
            return {
                cols:grid.cols,
                rows:grid.rows,
                visibleRows:Math.min(SMART_GROUP_MAX_VISIBLE_ROWS, grid.rows),
                width:grid.gridW + BATCH_NODE_PAD_H,
                height:grid.gridH + BATCH_NODE_PAD_V + BATCH_STATUSBAR_H,
                thumb:BATCH_THUMB.WIDTH,
                gridHeight:grid.gridH,
                pendingLock:true
            };
        }
        
        // 单图：原比例布局
        const layout = getSingleImageLayoutSize(aspect);
        return {
            cols:1,
            rows:1,
            width:layout.boxW,
            height:layout.boxH,
            thumb:layout.imgW,
            single:true,
            pendingLock:true
        };
    }
    if(count === 1) return singleImageLayout(images[0], node, s);
    const thumb = Math.round(MEDIA_GROUP_THUMB_BASE * s);
    const cell = thumb + 8;
    const PAD = 32; // group-node has 16px padding on each side
    const grid = images.find(img => img?.grid?.type === 'grid-split')?.grid;
    const explicitW = Number(node?.w);
    const explicitH = Number(node?.h);
    if(grid){
        const cols = Math.max(1, Number(grid.cols || 1));
        const rows = Math.max(1, Number(grid.rows || 1));
        // 第四轮：自然模式显示全部行——框高=内容高，不再有「3 排+滚动」的丑滚条。
        const visibleRows = rows;
        if(Number.isFinite(explicitW) && explicitW > 40 && Number.isFinite(explicitH) && explicitH > 40){
            // 第四轮：显式尺寸完全尊重用户拖拽（不弹回），全部行 fit 进显式框。
            const fittedThumb = Math.max(28, Math.floor(Math.min((explicitW - PAD - (cols - 1) * 8) / cols, (explicitH - PAD - (visibleRows - 1) * 8) / visibleRows)));
            const gridHeight = mediaGroupGridHeight(images, cols, visibleRows, fittedThumb);
            return {cols, rows, visibleRows, width:Math.round(explicitW), height:Math.round(explicitH), thumb:fittedThumb, gridHeight};
        }
        const gridHeight = mediaGroupGridHeight(images, cols, visibleRows, thumb);
        return {cols, rows, visibleRows, width:Math.max(Math.round(226*s), cols * cell + PAD), height:gridHeight + PAD, thumb, gridHeight};
    }
    // 批量生图节点（提交时固化了 _batchTotal / _generationAspect）：完成阶段强制使用
    // 批量缩略图网格规范，且网格行列以提交时固化的 _batchTotal 为准（而非当前图片数）——
    // 占位 → 逐张写回 → 完成三个阶段几何恒定；部分失败只留空格不改变框尺寸，
    // 也不会因图片数与占位数不一致而溢出 / 浮出外框。
    const genAspect = node?._generationAspect;
    const batchTotal = Math.max(0, Math.min(8, Number(node?._batchTotal) || 0));
    const isBatchGenNode = batchTotal > 1 || (Number(genAspect?.w) > 0 && Number(genAspect?.h) > 0);
    if(isBatchGenNode){
        const gridCount = Math.max(2, batchTotal > 1 ? batchTotal : count);
        const genGrid = getBatchThumbGridSize(gridCount);
        const genBoxW = genGrid.gridW + BATCH_NODE_PAD_H;
        const genBoxH = genGrid.gridH + BATCH_NODE_PAD_V;
        return {cols:genGrid.cols, rows:genGrid.rows, visibleRows:Math.min(SMART_GROUP_MAX_VISIBLE_ROWS, genGrid.rows), width:genBoxW, height:genBoxH, thumb:BATCH_THUMB.WIDTH, gridHeight:genGrid.gridH};
    }
    const cols = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(count))));
    const rows = Math.ceil(count / cols);
    // 第四轮：自然模式显示全部行——框贴合内容，不再 3 排封顶出滚条。
    const visibleRows = rows;
    if(Number.isFinite(explicitW) && explicitW > 40 && Number.isFinite(explicitH) && explicitH > 40){
        const fitted = groupImageGridLayout(count, explicitW, explicitH, thumb, PAD, 8, Math.min(count, 8));
        // 完成阶段与占位阶段共用同一比例：提交时固化的 _generationAspect 优先（用户
        // 要求“第一次取到图就固定尺寸、后面不再跳变”）；旧任务无固化比例时按真实图。
        const fixed = node?._generationAspect;
        let gridHeight;
        if(fixed && Number(fixed.w) > 0 && Number(fixed.h) > 0){
            const cellH = Math.round(fitted.thumb * Number(fixed.h) / Math.max(1, Number(fixed.w)));
            gridHeight = fitted.visibleRows * cellH + Math.max(0, fitted.visibleRows - 1) * 8;
        }else{
            gridHeight = mediaGroupGridHeight(images, fitted.cols, fitted.visibleRows, fitted.thumb);
        }
        // #89 第四轮：显式框完全尊重用户拖拽（放大/缩小双向都不弹回）；格子尺寸由
        // groupImageGridLayout 拟合进显式框，内容小于框时格子充满，更大时被框裁掉（无滚条）。
        return {cols:fitted.cols, rows:fitted.rows, visibleRows:fitted.visibleRows, width:Math.round(explicitW), height:Math.round(explicitH), thumb:fitted.thumb, gridHeight};
    }
    // #89 返工：框宽收敛到实际格宽（竖图列窄于满宽列），图不再在框内居中漂浮。
    const colWidths = mediaGridColumnWidths(images, cols, thumb);
    const width = Math.max(Math.round(226*s), colWidths.reduce((a, b) => a + b, 0) + (cols - 1) * 8 + PAD);
    const gridHeight = mediaGroupGridHeight(images, cols, visibleRows, thumb);
    return {cols, rows, visibleRows, width, height:gridHeight + PAD, thumb, gridHeight, colWidths};
}
function smartLoopCount(node){
    return Math.max(1, Math.min(100, Number(node?.count || 1) || 1));
}
function smartLoopWidth(node){
    return 340;
}
function smartLoopHeight(node){
    let h = 168;
    if(node?.imageInput) h += 72;
    if(node?.showPrompt) {
        const promptCount = Math.max(1, smartLoopPromptFieldValues(node).length);
        h += 94 + promptCount * 58 + smartLoopUpstreamPromptPreviewHeight(node);
    }
    h += smartNodeInputThumbsHeight(smartLoopPreviewImages(node));
    return h;
}
function fitSmartLoopNode(node){
    if(!node || node.type !== 'smart-loop') return;
    node.w = smartLoopWidth(node);
    node.h = smartLoopHeight(node);
}
function nodeRect(node){
    const layout = imageLayout(node.images || [], nodeScale(node), node);
    return {x:node.x || 0, y:node.y || 0, width:layout.width, height:layout.height};
}
function smartArrangeAtomicIds(ids){
    const out = new Set((ids || []).filter(id => canvasState.nodes.some(n => n.id === id)));
    let changed = true;
    while(changed){
        changed = false;
        canvasState.nodes.filter(isSmartGroupNode).forEach(group => {
            (group.items || []).forEach(itemId => {
                if(!out.has(itemId)) return;
                out.delete(itemId);
                out.add(group.id);
                changed = true;
            });
        });
    }
    return [...out];
}
function translateSmartNodeWithMembers(node, dx, dy, seen=new Set()){
    if(!node || seen.has(node.id)) return;
    seen.add(node.id);
    node.x = Math.round((Number(node.x) || 0) + dx);
    node.y = Math.round((Number(node.y) || 0) + dy);
    if(isSmartGroupNode(node)){
        smartGroupMembers(node).forEach(member => translateSmartNodeWithMembers(member, dx, dy, seen));
    }
}
function moveSmartNodeAtom(node, x, y){
    const dx = Math.round(x - (Number(node.x) || 0));
    const dy = Math.round(y - (Number(node.y) || 0));
    translateSmartNodeWithMembers(node, dx, dy);
}
function arrangeSelectedSmartNodes(){
    if(!canvas) return;
    const explicit = selectedNodeIds().filter(id => canvasState.nodes.some(n => n.id === id));
    if(!explicit.length) return;
    const ids = smartArrangeAtomicIds(explicit.length > 1 ? explicit : connectedSmartClusterIds(explicit[0]));
    if(ids.length < 2) return;
    pushUndo();
    if(!arrangeSmartIdsByConnections(ids)) return;
    render();
    toast(tr('smart.arrangedSelected'));
}
let smartNodeMenuPositionFrame = 0;
function positionSmartNodeFloatingMenus(){
    smartNodeMenuPositionFrame = 0;
    if(!world || !shell) return;
    const shellRect = shell.getBoundingClientRect();
    const margin = 12;
    world.querySelectorAll('.image-node .smart-node-floating-menu').forEach(menu => {
        menu.style.removeProperty('left');
        menu.style.removeProperty('top');
        const nodeEl = menu.closest('.image-node');
        if(!nodeEl?.classList.contains('selected') || world.classList.contains('smart-multi-selected')) return;
        const nodeRect = nodeEl.getBoundingClientRect();
        const menuRect = menu.getBoundingClientRect();
        if(!nodeRect.width || !nodeRect.height || !menuRect.width || !menuRect.height) return;
        const minX = shellRect.left + margin;
        const maxX = shellRect.right - margin;
        const desiredCenter = nodeRect.left + nodeRect.width / 2;
        const clampCenter = value => Math.min(
            Math.max(value, minX + menuRect.width / 2),
            maxX - menuRect.width / 2
        );
        const clampTop = value => Math.min(
            Math.max(value, shellRect.top + margin),
            shellRect.bottom - margin - menuRect.height
        );
        const nameBadge = nodeEl.querySelector('.image-name-badge-outside');
        const nameRect = nameBadge && getComputedStyle(nameBadge).visibility !== 'hidden'
            ? nameBadge.getBoundingClientRect()
            : null;
        const uiBlockers = ['#composer','#minimap','.smart-top-left','.smart-title'].map(selector => {
            const element = document.querySelector(selector);
            if(!element || getComputedStyle(element).display === 'none' || getComputedStyle(element).visibility === 'hidden') return null;
            return element.getBoundingClientRect();
        }).filter(Boolean);
        const centerCandidates = [
            desiredCenter,
            // 名称徽章是节点自身的标签，擦到它用户就认不出菜单归属；
            // 必须给出「恰好贴住徽章边缘」的候选（零边距），否则 694px 宽菜单在窄视口
            // （如 1280x720）里只有从 minimap 锚点推出的粗粒度位置可选，会残留 ~1.4px 擦碰。
            ...(nameRect ? [nameRect.left - menuRect.width / 2, nameRect.right + menuRect.width / 2] : []),
            ...uiBlockers.flatMap(rect => [
                rect.left - margin - menuRect.width / 2,
                rect.right + margin + menuRect.width / 2
            ])
        ].map(clampCenter).filter((value, index, values) => values.indexOf(value) === index);
        const topCandidates = [
            clampTop(Math.min(nodeRect.top, nameRect?.top ?? nodeRect.top) - menuRect.height - 12),
            clampTop(nodeRect.bottom + 12)
        ];
        const blockers = [nodeRect, nameRect, ...uiBlockers].filter(Boolean);
        const overlapArea = (left, top, rect) => {
            const width = Math.max(0, Math.min(left + menuRect.width, rect.right) - Math.max(left, rect.left));
            const height = Math.max(0, Math.min(top + menuRect.height, rect.bottom) - Math.max(top, rect.top));
            return width * height;
        };
        const candidates = [
            ...centerCandidates.flatMap(center => topCandidates.map(top => ({center, top}))),
            {
                center:clampCenter(nodeRect.right + margin + menuRect.width / 2),
                top:clampTop(nodeRect.top)
            },
            {
                center:clampCenter(nodeRect.left - margin - menuRect.width / 2),
                top:clampTop(nodeRect.top)
            },
            {
                center:clampCenter(nodeRect.right + margin + menuRect.width / 2),
                top:clampTop(nodeRect.top + menuRect.height + margin)
            },
            {
                center:clampCenter(nodeRect.left - margin - menuRect.width / 2),
                top:clampTop(nodeRect.top + menuRect.height + margin)
            }
        ];
        // 字典序评分：chrome 表面（工具栏/minimap/composer/标题行）与名称徽章是硬约束——
        // 它们与菜单同属固定 chrome 层，盖住它们会破坏可点击性（#world 的 transform 堆叠
        // 上下文使菜单 z-index 无法覆盖 chrome，800x600 实测）；节点本身重叠是设计允许
        // （菜单本就浮在节点上），只是软偏好。全部候选都在硬约束上打平时才比节点重叠。
        const score = candidate => {
            let nodeOverlap = 0;
            let badgeOverlap = 0;
            let chromeOverlap = 0;
            blockers.forEach((rect, index) => {
                const area = overlapArea(candidate.center - menuRect.width / 2, candidate.top, rect);
                if (index === 0) nodeOverlap += area;
                else if (index === 1 && nameRect) badgeOverlap += area;
                else chromeOverlap += area;
            });
            return [chromeOverlap, badgeOverlap, nodeOverlap, Math.abs(candidate.center - desiredCenter)];
        };
        const betterScore = (a, b) => {
            for (let i = 0; i < a.length; i++) {
                if (a[i] !== b[i]) return a[i] < b[i];
            }
            return false;
        };
        const position = candidates.reduce((best, candidate) => betterScore(score(candidate), score(best)) ? candidate : best, candidates[0]);
        const centerX = position.center;
        const screenTop = position.top;
        const scaleX = nodeRect.width / Math.max(1, nodeEl.offsetWidth);
        const scaleY = nodeRect.height / Math.max(1, nodeEl.offsetHeight);
        menu.style.left = `${(centerX - nodeRect.left) / Math.max(scaleX, 0.001)}px`;
        menu.style.top = `${(screenTop - nodeRect.top) / Math.max(scaleY, 0.001)}px`;
    });
}
function scheduleSmartNodeFloatingMenuPosition(){
    if(smartNodeMenuPositionFrame) return;
    smartNodeMenuPositionFrame = requestAnimationFrame(positionSmartNodeFloatingMenus);
}
function applyViewport(){
    world.style.transform = `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`;
    if(isProjectCanvasData()) persistProjectViewport();
    // world 被 transform:scale 缩放后，其内部带 backdrop-filter 的卡片（参数设置/合成卡等）
    // 会被部分浏览器（Chrome/Edge 等 Blink 内核）当作独立合成层先按 1x 栅格化、再整体缩放，
    // 缩小时位图被降采样 → 组件发虚。缩放态下关闭这些 backdrop-filter（底色本身已接近不透明，
    // 观感几乎无差），让卡片随矢量重新栅格化，保持清晰。
    world.classList.toggle('canvas-scaled', Math.abs(viewport.scale - 1) > 0.001);
    shell.style.backgroundSize = '24px 24px';
    shell.style.backgroundPosition = '0 0';
    renderMinimap();
    scheduleSmartNodeFloatingMenuPosition();
}
function screenToWorld(event){
    const rect = shell.getBoundingClientRect();
    return {
        x:(event.clientX - rect.left - viewport.x) / viewport.scale,
        y:(event.clientY - rect.top - viewport.y) / viewport.scale
    };
}
function canvasWheelZoomFactor(event, pageSize){
    const unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? pageSize : 1;
    const isMac = /^Mac/.test(navigator.platform || '');
    const sensitivity = 0.0008;
    const macMultiplier = isMac ? 1.15 : 1;
    return Math.exp(-event.deltaY * unit * sensitivity * macMultiplier);
}
function viewportCenter(){
    return {
        x:(shell.clientWidth / 2 - viewport.x) / viewport.scale,
        y:(shell.clientHeight / 2 - viewport.y) / viewport.scale
    };
}
function frameSmartCanvasContentIfNeeded(){
    const compactViewport = Number(shell?.clientWidth || window.innerWidth || 0) < 720;
    if(!shell || !canvasState.nodes.length || (!compactViewport && (viewport.x !== 0 || viewport.y !== 0 || viewport.scale !== 1))) return false;
    const content = canvasState.nodes
        .filter(node => node.id !== SMART_LOG_PREVIEW_NODE_ID)
        .map(nodeRect)
        .filter(rect => rect.width > 0 && rect.height > 0);
    if(!content.length) return false;

    const drawerOpen = smartHistoryModal?.classList.contains('open');
    const safe = {
        left:32,
        top:86,
        right:Math.max(32, shell.clientWidth - 32),
        bottom:Math.max(180, shell.clientHeight - (drawerOpen ? 300 : 92)),
    };
    const contentIsReadable = content.some(rect => {
        const screen = {
            left:rect.x * viewport.scale + viewport.x,
            top:rect.y * viewport.scale + viewport.y,
            right:(rect.x + rect.width) * viewport.scale + viewport.x,
            bottom:(rect.y + rect.height) * viewport.scale + viewport.y,
        };
        const overlapW = Math.max(0, Math.min(screen.right, safe.right) - Math.max(screen.left, safe.left));
        const overlapH = Math.max(0, Math.min(screen.bottom, safe.bottom) - Math.max(screen.top, safe.top));
        const overlap = (overlapW * overlapH) / Math.max(1, (screen.right - screen.left) * (screen.bottom - screen.top));
        const centerX = (screen.left + screen.right) / 2;
        const centerY = (screen.top + screen.bottom) / 2;
        return overlap >= 0.55 && centerX >= safe.left && centerX <= safe.right && centerY >= safe.top && centerY <= safe.bottom;
    });
    if(contentIsReadable) return false;

    const bounds = content.reduce((acc, rect) => ({
        left:Math.min(acc.left, rect.x),
        top:Math.min(acc.top, rect.y),
        right:Math.max(acc.right, rect.x + rect.width),
        bottom:Math.max(acc.bottom, rect.y + rect.height),
    }), {left:Infinity, top:Infinity, right:-Infinity, bottom:-Infinity});
    const contentW = Math.max(1, bounds.right - bounds.left);
    const contentH = Math.max(1, bounds.bottom - bounds.top);
    const availableW = Math.max(320, safe.right - safe.left - 64);
    const availableH = Math.max(240, safe.bottom - safe.top - 64);
    viewport.scale = Math.min(1, availableW / (contentW + 96), availableH / (contentH + 96));
    viewport.scale = Math.max(0.55, safeScale(viewport.scale));
    const centerX = (bounds.left + bounds.right) / 2;
    const centerY = (bounds.top + bounds.bottom) / 2;
    viewport.x = (safe.left + safe.right) / 2 - centerX * viewport.scale;
    viewport.y = (safe.top + safe.bottom) / 2 - centerY * viewport.scale;
    applyViewport();
    return true;
}
function renderMinimap(){
    if(!minimapContent || !minimapViewport) return;
    if(smartArrangeBtn) smartArrangeBtn.disabled = selectedNodeIds().length < 2;
    const width = minimapContent.clientWidth || 170;
    const height = minimapContent.clientHeight || 108;
    const viewW = shell.clientWidth / viewport.scale;
    const viewH = shell.clientHeight / viewport.scale;
    const viewX = -viewport.x / viewport.scale;
    const viewY = -viewport.y / viewport.scale;
    const rects = canvasState.nodes.filter(n => n.id !== SMART_LOG_PREVIEW_NODE_ID).map(nodeRect);
    rects.push({x:viewX, y:viewY, width:viewW, height:viewH});
    const minX = Math.min(...rects.map(r => r.x), -200);
    const minY = Math.min(...rects.map(r => r.y), -200);
    const maxX = Math.max(...rects.map(r => r.x + r.width), viewX + viewW + 200);
    const maxY = Math.max(...rects.map(r => r.y + r.height), viewY + viewH + 200);
    const scale = Math.min(width / Math.max(1, maxX - minX), height / Math.max(1, maxY - minY));
    const offsetX = (width - (maxX - minX) * scale) / 2;
    const offsetY = (height - (maxY - minY) * scale) / 2;
    smartMinimapState = {minX, minY, scale, offsetX, offsetY, width, height};
    const project = r => ({
        left:offsetX + (r.x - minX) * scale,
        top:offsetY + (r.y - minY) * scale,
        width:Math.max(4, r.width * scale),
        height:Math.max(4, r.height * scale)
    });
    const nodeHtml = rects.slice(0, -1).map(r => {
        const p = project(r);
        return `<div class="minimap-node" style="left:${p.left}px;top:${p.top}px;width:${p.width}px;height:${p.height}px"></div>`;
    }).join('');
    const view = project({x:viewX, y:viewY, width:viewW, height:viewH});
    minimapContent.innerHTML = `${nodeHtml}<div id="minimapViewport" class="smart-minimap-viewport" style="left:${view.left}px;top:${view.top}px;width:${view.width}px;height:${view.height}px"></div>`;
    minimapViewport = document.getElementById('minimapViewport');
}
function minimapEventToWorld(event){
    if(!smartMinimapState) renderMinimap();
    const state = smartMinimapState;
    if(!state) return viewportCenter();
    const rect = minimapContent.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    return {
        x:state.minX + (mx - state.offsetX) / Math.max(0.0001, state.scale),
        y:state.minY + (my - state.offsetY) / Math.max(0.0001, state.scale)
    };
}
function centerViewportOnWorldPoint(point){
    viewport.x = shell.clientWidth / 2 - point.x * viewport.scale;
    viewport.y = shell.clientHeight / 2 - point.y * viewport.scale;
    applyViewport();
}
const SMART_EDGE_REVEAL_MS = 8000;
const smartEdgeReveal = document.getElementById('smartEdgeReveal');
const smartEdgeRevealLabel = document.getElementById('smartEdgeRevealLabel');
function hideSmartEdgeReveal(){
    if(!smartEdgeReveal) return;
    clearTimeout(smartEdgeRevealTimer);
    smartEdgeRevealNodeId = '';
    smartEdgeReveal.classList.remove('visible');
}
function smartEdgeRevealPlacement(node){
    const rect = nodeRect(node);
    if(!rect || !shell) return null;
    const cx = (rect.x + rect.width / 2) * viewport.scale + viewport.x;
    const cy = (rect.y + rect.height / 2) * viewport.scale + viewport.y;
    const w = shell.clientWidth;
    const h = shell.clientHeight;
    const margin = 18;
    if(cx >= 0 && cx <= w && cy >= 0 && cy <= h) return null;
    const dx = cx - w / 2;
    const dy = cy - h / 2;
    const primary = Math.abs(dx) / Math.max(1, w) >= Math.abs(dy) / Math.max(1, h);
    if(primary) return {left: dx > 0 ? w - margin : margin, top: h / 2, side: dx > 0 ? 'right' : 'left'};
    return {left: w / 2, top: dy > 0 ? h - margin : margin, side: dy > 0 ? 'bottom' : 'top'};
}
function requestSmartEdgeReveal(node){
    if(!node?.id || !smartEdgeReveal) return;
    const live = liveSmartNode(node);
    if(!live || !canvasState.nodes.some(n => n.id === live.id)) return;
    animateSmartGenerationResult(live);
    const placement = smartEdgeRevealPlacement(live);
    if(!placement) return;
    const isVideo = live.outputKind === 'video';
    const isAudio = live.outputKind === 'audio';
    const count = (live.images || []).length;
    if(smartEdgeRevealNodeId === live.id && smartEdgeReveal.classList.contains('visible')){
        clearTimeout(smartEdgeRevealTimer);
        smartEdgeRevealTimer = setTimeout(hideSmartEdgeReveal, SMART_EDGE_REVEAL_MS);
        return;
    }
    smartEdgeRevealNodeId = live.id;
    if(smartEdgeRevealLabel){
        const kind = isVideo ? tr('smart.kindVideo') : isAudio ? tr('smart.kindAudio') : tr('smart.kindImage');
        smartEdgeRevealLabel.textContent = count > 1 ? trf('smart.edgeRevealNewMultiple', {k: kind, n: count}) : trf('smart.edgeRevealNewOne', {k: kind});
    }
    smartEdgeReveal.style.left = `${placement.left}px`;
    smartEdgeReveal.style.top = `${placement.top}px`;
    smartEdgeReveal.classList.add('visible');
    clearTimeout(smartEdgeRevealTimer);
    smartEdgeRevealTimer = setTimeout(hideSmartEdgeReveal, SMART_EDGE_REVEAL_MS);
}
function animateSmartGenerationResult(node){
    if(!node?.id || !world) return;
    requestAnimationFrame(() => {
        const element = [...world.querySelectorAll('.image-node[data-id]')]
            .find(item => item.dataset.id === String(node.id));
        if(!element) return;
        element.classList.remove('generation-result-enter');
        void element.offsetWidth;
        element.classList.add('generation-result-enter');
        setTimeout(() => element.classList.remove('generation-result-enter'), 420);
    });
}
function jumpToSmartEdgeRevealNode(){
    const id = smartEdgeRevealNodeId;
    hideSmartEdgeReveal();
    if(!id) return;
    const node = canvasState.nodes.find(n => String(n.id) === String(id));
    if(!node) return;
    canvasState.selectedId = node.id;
    canvasState.selectedIds = [];
    canvasState.selectedImage = {nodeId:'', index:-1};
    centerViewportOnWorldPoint({x:Number(node.x || 0) + 120, y:Number(node.y || 0) + 100});
    render();
}
function fitBoundsViewport(bounds){
    if(!bounds) return;
    const pad = 160;
    const minX = bounds.min_x;
    const minY = bounds.min_y;
    const maxX = bounds.max_x;
    const maxY = bounds.max_y;
    const width = Math.max(1, maxX - minX + pad * 2);
    const height = Math.max(1, maxY - minY + pad * 2);
    const nextScale = Math.max(0.06, Math.min(0.82, (shell.clientWidth - 80) / width, (shell.clientHeight - 80) / height));
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    viewport.scale = nextScale;
    viewport.x = shell.clientWidth / 2 - cx * viewport.scale;
    viewport.y = shell.clientHeight / 2 - cy * viewport.scale;
    applyViewport();
}
function fitAllNodesViewport(){
    if(!canvasState.nodes.length){
        viewport.scale = 0.45;
        viewport.x = shell.clientWidth / 2;
        viewport.y = shell.clientHeight / 2;
        applyViewport();
        return;
    }
    const rects = canvasState.nodes.map(nodeRect);
    const minX = Math.min(...rects.map(r => r.x));
    const minY = Math.min(...rects.map(r => r.y));
    const maxX = Math.max(...rects.map(r => r.x + r.width));
    const maxY = Math.max(...rects.map(r => r.y + r.height));
    const pad = 160;
    const width = Math.max(1, maxX - minX + pad * 2);
    const height = Math.max(1, maxY - minY + pad * 2);
    const nextScale = Math.max(0.06, Math.min(0.82, (shell.clientWidth - 80) / width, (shell.clientHeight - 80) / height));
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    viewport.scale = nextScale;
    viewport.x = shell.clientWidth / 2 - cx * viewport.scale;
    viewport.y = shell.clientHeight / 2 - cy * viewport.scale;
    applyViewport();
}
function enterZoomPreview(){
    if(zoomPreviewState) return;
    zoomPreviewState = {...viewport};
    shell.classList.add('zoom-preview');
    closeCreateMenu();
    fitAllNodesViewport();
}
function exitZoomPreview(point=null){
    if(!zoomPreviewState) return false;
    const prev = zoomPreviewState;
    zoomPreviewState = null;
    shell.classList.remove('zoom-preview');
    viewport.scale = prev.scale;
    if(point){
        viewport.x = shell.clientWidth / 2 - point.x * viewport.scale;
        viewport.y = shell.clientHeight / 2 - point.y * viewport.scale;
    } else {
        viewport.x = prev.x;
        viewport.y = prev.y;
    }
    applyViewport();
    return true;
}
function exitZoomPreviewToNode(nodeId){
    if(!zoomPreviewState) return false;
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if(!node) return exitZoomPreview();
    const prev = zoomPreviewState;
    const rect = nodeRect(node);
    const cx = rect.x + rect.width / 2;
    const cy = rect.y + rect.height / 2;
    const fitW = Math.max(1, shell.clientWidth - 160);
    const fitH = Math.max(1, shell.clientHeight - 160);
    const fitScale = Math.min(
        ZOOM_PREVIEW_NODE_MAX_SCALE,
        fitW / Math.max(1, rect.width),
        fitH / Math.max(1, rect.height)
    );
    const readableScale = Math.min(ZOOM_PREVIEW_NODE_MAX_SCALE, Math.max(ZOOM_PREVIEW_NODE_DEFAULT_SCALE, fitScale));
    zoomPreviewState = null;
    shell.classList.remove('zoom-preview');
    viewport.scale = Math.max(safeScale(prev.scale), readableScale);
    viewport.x = shell.clientWidth / 2 - cx * viewport.scale;
    viewport.y = shell.clientHeight / 2 - cy * viewport.scale;
    applyViewport();
    return true;
}
function toggleZoomPreview(){
    if(zoomPreviewState) exitZoomPreview();
    else enterZoomPreview();
}
function imageProviders(){
    return (apiProviders || []).filter(p => p.enabled !== false && p.id !== 'modelscope' && p.id !== 'volcengine' && (p.image_models || []).length);
}
function volcengineProvider(){
    return (apiProviders || []).find(p => p.id === 'volcengine' && p.enabled !== false) || {
        id:'volcengine',
        name:'火山引擎',
        image_models:[],
        video_models:DEFAULT_VIDEO_MODELS,
        enabled:true
    };
}
function runningHubProvider(){
    return (apiProviders || []).find(p => p.id === 'runninghub' && p.enabled !== false) || null;
}
function runningHubEntries(kind){
    const provider = runningHubProvider();
    if(kind === 'model'){
        return (provider?.image_models || []).map(model => ({
            id:String(model || '').trim(),
            title:String(model || '').trim(),
            enabled:true
        })).filter(item => item.id);
    }
    const key = 'rh_apps';
    return Array.isArray(provider?.[key]) ? provider[key].filter(item => item?.enabled !== false && item?.hidden !== true) : [];
}
function runningHubEntryId(entry, kind){
    if(kind === 'model') return String(entry?.id || entry?.model || entry?.title || '').trim();
    return String(entry?.appId || entry?.webappId || entry?.id || '').trim();
}
function runningHubEntryLabel(entry, kind){
    const id = runningHubEntryId(entry, kind);
    if(kind === 'model') return entry?.title || entry?.name || id;
    return entry?.title || entry?.name || `AI App ${id}`;
}
function runningHubEntryKey(kind, id){
    return `${kind}:${String(id || '').trim()}`;
}
function parseRunningHubEntryKey(value){
    const text = String(value || '').trim();
    const match = text.match(/^(app|model):(.+)$/);
    return match ? {kind:match[1], id:match[2].trim()} : null;
}
function runningHubAllEntries(){
    return [
        ...runningHubEntries('model').map(entry => ({kind:'model', id:runningHubEntryId(entry, 'model'), entry})).filter(x => x.id),
        ...runningHubEntries('app').map(entry => ({kind:'app', id:runningHubEntryId(entry, 'app'), entry})).filter(x => x.id),
    ];
}
function selectedRunningHubRef(sourceSettings=settings){
    const all = runningHubAllEntries();
    sourceSettings = sourceSettings || settings;
    const parsed = parseRunningHubEntryKey(sourceSettings.rhConfigKey || '');
    let ref = parsed ? all.find(item => item.kind === parsed.kind && item.id === parsed.id) : null;
    if(!ref && all.length) ref = all[0];
    if(ref && sourceSettings === settings) settings.rhConfigKey = runningHubEntryKey(ref.kind, ref.id);
    return ref || null;
}
function rhEntryFields(entry){
    return Array.isArray(entry?.fields) ? entry.fields : [];
}
function runningHubSelectedModel(sourceSettings=settings){
    const ref = selectedRunningHubRef(sourceSettings);
    return ref?.kind === 'model' ? ref.id : '';
}
function runningHubModelApiSettings(sourceSettings=settings){
    const model = runningHubSelectedModel(sourceSettings);
    return {...(sourceSettings || settings), engine:'api', apiKind:'image', provider_id:'runninghub', model};
}
function rhUsableFields(fields){
    const list = Array.isArray(fields) ? fields : [];
    if(!list.length) return [];
    const enabled = list.filter(f => f.enabled === true);
    return enabled.length ? enabled : list;
}
function rhActiveFields(sourceSettings=settings){
    const ref = selectedRunningHubRef(sourceSettings);
    let fields = rhEntryFields(ref?.entry);
    fields = rhUsableFields(fields);
    return sortRunningHubFields(fields);
}
function runningHubRunNeedsPrompt(sourceSettings=settings){
    if((sourceSettings || settings).engine !== 'runninghub') return true;
    if(runningHubSelectedModel(sourceSettings)) return true;
    const fields = rhActiveFields(sourceSettings);
    const promptFields = fields.filter(field => rhFieldRole(field) === 'prompt');
    if(!promptFields.length) return false;
    return promptFields.some(field => field.required === true && !rhDefaultValue(field).trim());
}
function smartRunNeedsPrompt(sourceSettings=settings){
    sourceSettings = sourceSettings || settings;
    if(sourceSettings.engine === 'runninghub') return runningHubRunNeedsPrompt(sourceSettings);
    return true;
}
function sortRunningHubFields(fields){
    return [...(fields || [])].sort((a, b) => {
        const ak = rhFieldKind(a), bk = rhFieldKind(b);
        if(ak === 'image' && bk === 'image'){
            const ao = Number(a.imageOrder) || 9999;
            const bo = Number(b.imageOrder) || 9999;
            if(ao !== bo) return ao - bo;
        }
        if(ak === 'image' && bk !== 'image') return -1;
        if(ak !== 'image' && bk === 'image') return 1;
        return String(a.nodeId || '').localeCompare(String(b.nodeId || ''), undefined, {numeric:true}) || String(a.fieldName || '').localeCompare(String(b.fieldName || ''));
    });
}
function chatApiProviders(){
    return (apiProviders || []).filter(p => p.enabled !== false && (p.chat_models || []).length);
}
function orderedProviderModels(provider, field, kind){
    if(!provider) return [];
    const models = Array.isArray(provider[field]) ? provider[field].filter(Boolean) : [];
    const recommended = provider.recommended_models?.[kind];
    // A fetched recommendation is only a default hint. It must already be
    // in the user's selected active list before it can appear on the canvas.
    const preferred = recommended && models.includes(recommended) ? recommended : models[0];
    return [...new Set([preferred, ...models].filter(Boolean))];
}
// 模型展示名：优先 provider.model_names（桥豆麻衣酱友好名），否则回退原始模型 ID。
function modelDisplayName(provider, model){
    const raw = String(model ?? '');
    if(provider?.model_names && Object.prototype.hasOwnProperty.call(provider.model_names, raw)){
        const label = String(provider.model_names[raw] ?? '').trim();
        if(label && label !== raw) return label;
    }
    return raw;
}
function providerModelNameLookup(provider){
    return (value) => modelDisplayName(provider, value);
}
function resolveChatProviderId(providerId=''){
    const providers = chatApiProviders();
    if(providers.some(p => p.id === providerId)) return providerId;
    return providers[0]?.id || 'comfly';
}
function getChatModels(){
    // Return a list of chat model IDs across all providers
    const models = [];
    for (const provider of chatApiProviders()) {
        const providerModels = providerChatModels(provider.id);
        for (const model of providerModels) {
            models.push({ id: model, name: model });
        }
    }
    return models;
}

function providerChatModels(providerId){
    const provider = chatApiProviders().find(p => p.id === providerId);
    return mergeSpecModels('chat', orderedProviderModels(provider, 'chat_models', 'chat'), provider);
}
function resolveChatModel(model='', providerId=''){
    const models = providerChatModels(resolveChatProviderId(providerId));
    return models.includes(model) ? model : (models[0] || model || 'gpt-5.4');
}
function chatProviderOptions(selectedId=''){
    const selected = resolveChatProviderId(canvasState.selectedId);
    return chatApiProviders().map(provider => `<option value="${escapeHtml(provider.id)}" ${provider.id === selected ? 'selected' : ''}>${escapeHtml(provider.name || provider.id)}</option>`).join('');
}
function chatModelOptions(selectedModel='', providerId=''){
    const selectedProvider = resolveChatProviderId(providerId);
    const models = providerChatModels(selectedProvider);
    const selected = resolveChatModel(selectedModel, selectedProvider);
    return [...new Set([selected, ...models].filter(Boolean))].map(model => `<option value="${escapeHtml(model)}" ${model === selected ? 'selected' : ''}>${escapeHtml(model)}</option>`).join('');
}
function apiProviderById(providerId){
    if(providerId === 'volcengine') return volcengineProvider();
    return (apiProviders || []).find(p => p.id === providerId) || imageProviders()[0] || null;
}
// 认证素材 asset:// 是平台绑定的：返回某 provider 所属的认证平台键（与后端一致）
function videoProviderPlatform(providerId){
    const p = (apiProviders || []).find(x => x.id === providerId);
    const proto = String(p?.protocol || '').toLowerCase();
    const base = String(p?.base_url || '').toLowerCase();
    if(proto === 'apimart' || base.includes('apimart.ai')) return 'apimart';
    if(proto === 'volcengine' || providerId === 'volcengine') return 'volcengine';
    return '';
}
function providerImageModels(providerId){
    const provider = providerId === 'volcengine' ? volcengineProvider() : (apiProviders || []).find(p => p.id === providerId);
    return mergeSpecModels('image', orderedProviderModels(provider, 'image_models', 'image'), provider);
}
// 即梦图生图（挂了参考图）不支持 3.0/3.1，此时从模型下拉里隐藏它们。
const JIMENG_IMAGE2IMAGE_UNSUPPORTED = ['3.0', '3.1'];
function filterJimengImageModels(models){
    if(settings.provider_id !== 'jimeng' || !jimengImageEditMode()) return models;
    return (models || []).filter(m => !JIMENG_IMAGE2IMAGE_UNSUPPORTED.includes(String(m)));
}
let _jimengLastEditMode = null;
let _jimengModelRefreshing = false;
// 参考图增删导致即梦文生图/图生图切换时，重新渲染参数面板以更新模型下拉。
function syncJimengModelPillForRefs(){
    if(_jimengModelRefreshing) return;
    if(settings.provider_id !== 'jimeng' || settings.engine !== 'api' || settings.apiKind === 'video'){
        _jimengLastEditMode = null;
        return;
    }
    const mode = jimengImageEditMode();
    if(mode === _jimengLastEditMode) return;
    _jimengLastEditMode = mode;
    _jimengModelRefreshing = true;
    try { scheduleDynamicParamsRefresh(80); } finally { _jimengModelRefreshing = false; }
}
// 即梦各视频指令支持的模型集合不同，按当前参考素材推断指令并过滤模型下拉。
const JIMENG_SEEDANCE_VIDEO_MODELS = ['seedance2.0_vip', 'seedance2.0fast_vip', 'seedance2.0', 'seedance2.0fast'];
const JIMENG_VIDEO_MODELS_BY_COMMAND = {
    text2video: JIMENG_SEEDANCE_VIDEO_MODELS,
    multimodal2video: JIMENG_SEEDANCE_VIDEO_MODELS,
    image2video: ['3.0', '3.0fast', '3.0pro', '3.5pro', ...JIMENG_SEEDANCE_VIDEO_MODELS],
    frames2video: ['3.0', '3.5pro', ...JIMENG_SEEDANCE_VIDEO_MODELS],
};
function jimengVideoCommand(){
    const node = activeComposerNode() || selectedNode();
    const refs = node ? visibleReferenceImagesFor(node) : [];
    const imageRefs = imageRefsOnly(refs);
    const hasVideoRef = videoRefsOnly(refs).length > 0 || Boolean(manualSmartVideoLink(settings));
    if(settings.videoMultimodal || hasVideoRef) return 'multimodal2video';
    if(imageRefs.length >= 2) return settings.videoUseFrameRoles ? 'frames2video' : 'multiframe2video';
    if(imageRefs.length >= 1) return 'image2video';
    return 'text2video';
}
function filterJimengVideoModels(models){
    if(settings.videoProvider !== 'jimeng') return models;
    const allowed = JIMENG_VIDEO_MODELS_BY_COMMAND[jimengVideoCommand()];
    if(!allowed) return models; // multiframe2video 等：官方规格未知，不过滤
    return (models || []).filter(m => allowed.includes(String(m)));
}
let _jimengLastVideoCommand = null;
function syncJimengVideoModelPillForRefs(){
    if(_jimengModelRefreshing) return;
    if(settings.videoProvider !== 'jimeng' || settings.engine !== 'api' || settings.apiKind !== 'video'){
        _jimengLastVideoCommand = null;
        return;
    }
    const command = jimengVideoCommand();
    if(command === _jimengLastVideoCommand) return;
    _jimengLastVideoCommand = command;
    _jimengModelRefreshing = true;
    try { scheduleDynamicParamsRefresh(80); } finally { _jimengModelRefreshing = false; }
}
function sanitizeSmartApiSelection(target=settings){
    if(!target || typeof target !== 'object') return target;
    if(target.engine === 'volcengine'){
        if(target.apiKind === 'video'){
            target.videoProvider = 'volcengine';
            const models = volcengineVideoModels();
            if(!models.includes(target.videoModel)) target.videoModel = models[0] || '';
        } else {
            target.provider_id = 'volcengine';
            const models = providerImageModels('volcengine');
            if(!models.includes(target.model)) target.model = models[0] || '';
        }
        return target;
    }
    clearVolcengineSelectionOutsideVolcengine(target);
    if(target.provider_id){
        const models = providerImageModels(target.provider_id);
        if(models.length && !models.includes(target.model)) target.model = models[0] || '';
    }
    if((target.engine || 'api') === 'api' && (target.apiKind || 'image') !== 'video'){
        const allowAuto = isGptImageAutoSizeModel(target.model);
        if(!target.resolution) target.resolution = allowAuto ? defaultSmartApiResolution(target.model) : '1k';
        if(!allowAuto && target.resolution === 'auto') target.resolution = '1k';
    }
    if(target.videoProvider){
        const models = providerVideoModels(target.videoProvider);
        if(models.length && !models.includes(target.videoModel)) target.videoModel = models[0] || '';
    }
    return target;
}
function modelscopeProvider(){
    return (apiProviders || []).find(p => p.id === 'modelscope' && p.enabled !== false) || null;
}
function modelscopeImageModels(){
    return modelscopeProvider()?.image_models || [];
}
const DEFAULT_VIDEO_MODELS = ['veo3-fast','veo3','sora','runway','kling','pika','minimax-video','wan-v2','seedance-1.0-pro','jimeng-vide-3.0','jimeng-video-3.0-pro'];
function videoApiProviders(){
    const fromConfig = (apiProviders || []).filter(p => p.enabled !== false && p.id !== 'volcengine' && (p.video_models || []).length);
    if(fromConfig.length) return fromConfig;
    return [{id:'comfly', name:'Comfly', video_models:DEFAULT_VIDEO_MODELS, enabled:true}];
}
function videoProviderById(providerId){
    if(providerId === 'volcengine') return volcengineProvider();
    return videoApiProviders().find(p => p.id === providerId) || videoApiProviders()[0] || null;
}
function providerVideoModels(providerId){
    if(providerId === 'volcengine') return volcengineVideoModels();
    const provider = videoApiProviders().find(p => p.id === providerId);
    const models = provider?.video_models || DEFAULT_VIDEO_MODELS;
    return mergeSpecModels('video', orderedProviderModels(provider, 'video_models', 'video'), provider);
}
function volcengineVideoModels(){
    const provider = (apiProviders || []).find(p => p.id === 'volcengine');
    return orderedProviderModels(provider, 'video_models', 'video').length
        ? orderedProviderModels(provider, 'video_models', 'video')
        : DEFAULT_VIDEO_MODELS;
}
function renderVideoProviderControl(providers){
    const current = (providers || []).find(p => p.id === settings.videoProvider) || videoProviderById(settings.videoProvider);
    return `<div class="smart-control provider-control">
        <button class="smart-pill" type="button"><i data-lucide="plug-zap"></i><span class="sub">${escapeHtml(current?.name || settings.videoProvider || tr('smart.platform'))}</span></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.videoPlatform'))}</div>
            <div class="model-list">
                ${providers.map(p => `<button type="button" class="direct-option ${p.id === settings.videoProvider ? 'active' : ''}" data-smart-param="videoProvider" data-smart-value="${escapeHtml(p.id)}"><span>${escapeHtml(p.name || p.id)}</span></button>`).join('') || `<div class="muted-note">${escapeHtml(tr('smart.noVideoPlatform'))}</div>`}
            </div>
        </div>
    </div>`;
}
function modelSearchInputHtml(param, count){
    const countText = Number(count) > 0 ? ` <span class="model-search-count">${Number(count)}</span>` : '';
    return `<label class="model-search-row">
        <i data-lucide="search"></i>
        <input type="search" class="model-search-input" data-model-search="${escapeHtml(param)}" autocomplete="off" spellcheck="false" placeholder="${escapeHtml(tr('smart.searchModel'))}" aria-label="${escapeHtml(tr('smart.searchModel'))}">
        ${countText}
    </label>`;
}
function videoAdapterHint(model){
    const m = String(model ?? '').toLocaleLowerCase();
    if (m.startsWith('kling-v1') || m.startsWith('kling-video-v1') || m.startsWith('kling-v3') || m === 'kling-video-v3' || m.startsWith('kling-omni')) return '';
    if (m.startsWith('veo')) return '';
    if (m.startsWith('wan2') || m.startsWith('wanx')) return '';
    if (m.startsWith('doubao-seedance') || m.startsWith('seedance') || m.startsWith('grok-video') || m.startsWith('grok-imagine-video') || m.startsWith('grok-1.5-video') || m.startsWith('minimax-hailuo') || m === 'hailuo-video' || m.startsWith('sora-2') || m === 'sora2') return '';
    return '';
}
function searchableModelOptions(models, param, selected, emptyText, labelFor=(value => value)){
    const list = Array.isArray(models) ? models : [];
    const options = list.map(value => {
        const rawValue = String(value ?? '');
        const label = String(labelFor(value) ?? rawValue);
        const searchable = `${rawValue} ${label}`.toLocaleLowerCase();
        const hint = param === 'videoModel' ? videoAdapterHint(rawValue) : '';
        return `<button type="button" class="direct-option ${rawValue === String(selected ?? '') ? 'active' : ''}" data-smart-param="${escapeHtml(param)}" data-smart-value="${escapeHtml(rawValue)}" data-model-option="${escapeHtml(rawValue)}" ${hint ? 'data-unadapted="1"' : ''}>${escapeHtml(label)}${hint ? `<span class="model-adapt-badge">${escapeHtml(hint)}</span>` : ''}</button>`;
    }).join('');
    return `${modelSearchInputHtml(param, list.length)}
        <div class="model-list" data-model-options="${escapeHtml(param)}">
            ${options || `<div class="muted-note">${escapeHtml(emptyText)}</div>`}
            <div class="model-search-empty" data-model-search-empty hidden>${escapeHtml(tr('smart.modelSearchEmpty'))}</div>
        </div>`;
}
function renderVideoRoutingControl(){
    // 自动路由为默认无感行为：按视频参数自动选择兼容模型。
    // 不再渲染显式切换开关；用户主动点选服务商或模型时即切换为固定（manual）。
    return '';
}
function renderVideoModelControl(models){
    return `<div class="smart-control model-control">
        <button class="smart-pill" type="button"><i data-lucide="film"></i><span class="sub">${escapeHtml(settings.videoModel || tr('smart.model'))}</span></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.videoModel'))}</div>
            ${searchableModelOptions(models, 'videoModel', settings.videoModel, tr('smart.noVideoModel'))}
        </div>
    </div>`;
}
function renderVideoDurationControl(){
    const v = Math.max(1, Math.min(60, Number(settings.videoDuration) || 5));
    const quick = [3, 4, 5, 6, 8, 10, 12, 15];
    return `<div class="smart-control duration-control" title="${escapeHtml(tr('smart.videoDurationTip'))}">
        <button class="smart-pill" type="button"><i data-lucide="timer"></i><span>${v}s</span></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.videoDuration'))}</div>
            <div class="duration-grid">
                ${quick.map(n => `<button type="button" class="duration-option ${n === v ? 'active' : ''}" data-smart-param="videoDuration" data-smart-value="${n}">${n}s</button>`).join('')}
            </div>
            <label class="duration-custom">
                <span>${escapeHtml(tr('smart.custom'))}</span>
                <input type="number" min="1" max="60" step="1" data-param="videoDuration" value="${v}">
            </label>
        </div>
    </div>`;
}
function renderVideoAspectControl(){
    const options = [
        ['16:9','16:9'], ['9:16','9:16'], ['1:1','1:1'], ['4:3','4:3'], ['3:4','3:4'],
        ['21:9','21:9'], ['9:21','9:21'], ['keep_ratio', tr('smart.videoAspectKeep')], ['adaptive', tr('smart.videoAspectAdaptive')]
    ];
    const value = settings.videoAspect || '16:9';
    const labelMap = Object.fromEntries(options);
    return `<div class="smart-control aspect-control">
        <button class="smart-pill" type="button"><i data-lucide="scan"></i><span>${escapeHtml(labelMap[value] || value)}</span></button>
        <div class="smart-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.videoAspect'))}</div>
            <div class="ratio-grid">
                ${options.map(([v,l]) => `<button type="button" class="ratio-option ${v === value ? 'active' : ''}" data-smart-param="videoAspect" data-smart-value="${escapeHtml(v)}"><span class="ratio-icon ${videoAspectIconClass(v)}"></span><span>${escapeHtml(l)}</span></button>`).join('')}
            </div>
        </div>
    </div>`;
}
function renderVideoResolutionControl(){
    const options = [['', tr('smart.videoResAuto')], ['480p','480P'], ['720p','720P'], ['1080p','1080P']];
    const value = settings.videoResolution || '';
    const labelMap = Object.fromEntries(options);
    return `<div class="smart-control resolution-control">
        <button class="smart-pill" type="button"><i data-lucide="monitor"></i><span>${escapeHtml(labelMap[value] || value || tr('smart.videoResAuto'))}</span></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.videoResolution'))}</div>
            <div class="model-list">
                ${options.map(([v,l]) => `<button type="button" class="direct-option ${v === value ? 'active' : ''}" data-smart-param="videoResolution" data-smart-value="${escapeHtml(v)}"><span>${escapeHtml(l)}</span></button>`).join('')}
            </div>
        </div>
    </div>`;
}
function renderVideoToggleControl(key, label){
    const on = !!settings[key];
    return `<button type="button" class="setting-check ${on ? 'active' : ''}" data-toggle-param="${escapeHtml(key)}"><span class="check-box"></span><span>${escapeHtml(label)}</span></button>`;
}
// 可信素材模式：打开后可选择素材来源——素材库认证链接 / 自行上传云端 / 自行输入网址。
function renderVideoTrustedAssetControl(){
    const on = !!settings.videoTrustedAsset;
    let html = renderVideoToggleControl('videoTrustedAsset', tr('smart.videoTrustedAsset'));
    if(!on) return html;
    const src = ['library','cloud','manual'].includes(settings.videoTrustedSource) ? settings.videoTrustedSource : 'library';
    html += `<div class="trusted-source-row">
        <button type="button" class="smart-pill trusted-src-pill ${src === 'library' ? 'active' : ''}" data-trusted-source="library" title="${escapeHtml(tr('smart.trustedSrcLibrary'))}"><i data-lucide="library"></i><span>${escapeHtml(tr('smart.trustedSrcLibraryLabel'))}</span></button>
        <button type="button" class="smart-pill trusted-src-pill ${src === 'cloud' ? 'active' : ''}" data-trusted-source="cloud" title="${escapeHtml(tr('smart.trustedSrcCloud'))}"><i data-lucide="upload-cloud"></i><span>${escapeHtml(tr('smart.trustedSrcCloudLabel'))}</span></button>
        <button type="button" class="smart-pill trusted-src-pill ${src === 'manual' ? 'active' : ''}" data-trusted-source="manual" title="${escapeHtml(tr('smart.trustedSrcManual'))}"><i data-lucide="link"></i><span>${escapeHtml(tr('smart.trustedSrcManualLabel'))}</span></button>
    </div>`;
    return html;
}
function parseSizeValue(value){
    const match = String(value || '').trim().match(/^(\d+)\s*[xX*]\s*(\d+)$/);
    return match ? {width:match[1], height:match[2]} : null;
}
function parseRatioValue(value){
    const raw = String(value || '').trim();
    const parts = raw.includes(':') ? raw.split(':') : raw.split(/[xX*]/);
    if(parts.length !== 2) return 0;
    const w = Number(parts[0]);
    const h = Number(parts[1]);
    return w > 0 && h > 0 ? w / h : 0;
}
function apiImageSize(ratioValue, resolutionValue, customRatioValue='', customSizeValue=''){
    if(resolutionValue === 'auto') return 'auto';
    if(resolutionValue === 'custom') return String(customSizeValue || '').trim();
    const resolutionKey = resolutionValue || '1k';
    if(ratioValue === 'custom' || ratioValue === 'source'){
        const parsed = parseRatioValue(customRatioValue);
        const longSide = RES_LONG_SIDE[resolutionKey] || 1024;
        if(parsed){
            const pixelLimit = RES_PIXEL_LIMIT[resolutionKey] || (longSide * longSide);
            const rawWidth = parsed >= 1 ? longSide : Math.min(longSide * parsed, Math.sqrt(pixelLimit * parsed));
            const rawHeight = parsed >= 1 ? Math.min(longSide / parsed, Math.sqrt(pixelLimit / parsed)) : longSide;
            const width = Math.floor(rawWidth / 16) * 16;
            const height = Math.floor(rawHeight / 16) * 16;
            return `${Math.max(64, width)}x${Math.max(64, height)}`;
        }
    }
    const ratioKey = ratioValue && SIZE_MAP[ratioValue] ? ratioValue : 'square';
    return SIZE_MAP[ratioKey]?.[resolutionKey] || SIZE_MAP.square[resolutionKey] || SIZE_MAP.square['1k'];
}
function apiImageUsesExplicitResolution(sourceSettings=settings){
    if(String(sourceSettings?.engine || '') !== 'api') return true;
    const providerId = String(sourceSettings?.provider_id || '').trim();
    const provider = apiProviderById(providerId) || {};
    const capabilities = provider?.image_model_capabilities?.[String(sourceSettings?.model || '').trim()];
    if(capabilities) return capabilities.arbitrary_resolution === true || (Array.isArray(capabilities.resolutions) && capabilities.resolutions.length > 0);
    return true;
}
function normalizeApiSizeSettings(prefix=''){
    const ratioKey = prefix ? `${prefix}Ratio` : 'ratio';
    const resKey = prefix ? `${prefix}Resolution` : 'resolution';
    if(!prefix && !apiImageUsesExplicitResolution(settings)){
        settings[resKey] = 'auto';
        if(!settings[ratioKey]) settings[ratioKey] = 'square';
        return;
    }
    const allowAuto = !prefix && settings.engine === 'api' && settings.apiKind !== 'video' && isGptImageAutoSizeModel(settings.model);
    if(!settings[resKey]) settings[resKey] = allowAuto ? defaultSmartApiResolution(settings.model) : '1k';
    if(!allowAuto && settings[resKey] === 'auto') settings[resKey] = '1k';
    if(settings[resKey] === 'auto' && !settings[ratioKey]) settings[ratioKey] = 'square';
}
function updateProviderModels(){ renderDynamicParams(); }
let dynamicParamsRefreshTimer = 0;
let dynamicParamsRefreshIdle = 0;
let dynamicParamsRefreshSeq = 0;
function scheduleDynamicParamsRefresh(delay=120){
    if(dynamicParamsRefreshTimer){
        clearTimeout(dynamicParamsRefreshTimer);
        dynamicParamsRefreshTimer = 0;
    }
    if(dynamicParamsRefreshIdle && window.cancelIdleCallback){
        window.cancelIdleCallback(dynamicParamsRefreshIdle);
        dynamicParamsRefreshIdle = 0;
    }
    const seq = ++dynamicParamsRefreshSeq;
    const run = () => {
        dynamicParamsRefreshTimer = 0;
        dynamicParamsRefreshIdle = 0;
        if(seq !== dynamicParamsRefreshSeq) return;
        renderDynamicParams();
    };
    if(window.requestIdleCallback){
        dynamicParamsRefreshIdle = window.requestIdleCallback(run, {timeout:Math.max(180, Number(delay) + 260)});
    } else {
        dynamicParamsRefreshTimer = setTimeout(run, Math.max(0, Number(delay) || 0));
    }
}
function controlTypeKey(el){
    return el ? Array.from(el.classList).find(c => c !== 'smart-control' && c.endsWith('-control')) || '' : '';
}
// 记住重渲染前哪个控件的弹层是打开的：pinned=点击药丸锁定，interacting=悬浮打开后点了里面的参数。
// 重渲染会重建 DOM、丢掉这两个状态，所以渲染后要按原样恢复，否则点一下就收起来了。
function openControlState(){
    const el = dynamicParams?.querySelector('.smart-control.pinned, .smart-control.interacting');
    const key = controlTypeKey(el);
    if(!key) return null;
    return { key, pinned: el.classList.contains('pinned'), interacting: el.classList.contains('interacting') };
}
function restoreOpenControl(state){
    if(!state) return;
    const match = dynamicParams?.querySelector(`.smart-control.${state.key}`);
    if(!match) return;
    if(state.pinned) match.classList.add('pinned');
    if(state.interacting) match.classList.add('interacting');
}
function dynamicParamsScrollSnapshot(){
    if(!dynamicParams) return null;
    return {
        top:dynamicParams.scrollTop || 0,
        left:dynamicParams.scrollLeft || 0,
        sizePickers:[...dynamicParams.querySelectorAll('.size-picker-control')].map(ctrl => ({
            key:controlTypeKey(ctrl),
            lists:[...ctrl.querySelectorAll('.size-picker-list')].map(list => ({top:list.scrollTop || 0, left:list.scrollLeft || 0}))
        }))
    };
}
function restoreDynamicParamsScroll(snapshot){
    if(!snapshot || !dynamicParams) return;
    const apply = () => {
        dynamicParams.scrollTop = snapshot.top || 0;
        dynamicParams.scrollLeft = snapshot.left || 0;
        const used = new Set();
        (snapshot.sizePickers || []).forEach(item => {
            const pickers = [...dynamicParams.querySelectorAll('.size-picker-control')];
            const index = pickers.findIndex((ctrl, i) => !used.has(i) && (!item.key || controlTypeKey(ctrl) === item.key));
            if(index < 0) return;
            used.add(index);
            const lists = pickers[index].querySelectorAll('.size-picker-list');
            (item.lists || []).forEach((pos, listIndex) => {
                const list = lists[listIndex];
                if(!list) return;
                list.scrollTop = pos.top || 0;
                list.scrollLeft = pos.left || 0;
            });
        });
    };
    apply();
    requestAnimationFrame(apply);
}
function renderDynamicParams(){
    if(!dynamicParams) return;
    const keepOpen = openControlState();
    const scrollState = dynamicParamsScrollSnapshot();
    settings.engine = normalizeSmartOnlineEngine(settings.engine);
    settings.apiKind = settings.apiKind === 'video' ? 'video' : 'image';
    clearVolcengineSelectionOutsideVolcengine(settings);
    engineSelect.value = settings.engine;
    syncApiKindToggleVisibility();
    if(settings.engine === 'api'){
        if(settings.apiKind === 'video') renderApiVideoParams();
        else renderApiParams();
    }
    else if(settings.engine === 'volcengine'){
        if(settings.apiKind === 'video') renderVolcengineVideoParams();
        else renderVolcengineParams();
    }
    else if(settings.engine === 'modelscope') renderMsParams();
    else if(settings.engine === 'runninghub') renderRunningHubParams();
    else renderApiParams();
    bindDynamicParams();
    restoreOpenControl(keepOpen);
    restoreDynamicParamsScroll(scrollState);
    updatePromptPlaceholder();
    persistActiveSmartSettings();
    if(activeComposerNode()?.id) requestAnimationFrame(() => positionComposerForNode(activeComposerNode()));
    if(window.lucide) lucide.createIcons();
}
function renderApiParams(){
    const providers = imageProviders();
    if(!settings.provider_id || !providers.some(p => p.id === settings.provider_id)) settings.provider_id = providers[0]?.id || '';
    const models = filterJimengImageModels(providerImageModels(settings.provider_id));
    if(!settings.model || !models.includes(settings.model)) settings.model = models[0] || '';
    // 切换平台/模型时保留用户已选的分辨率（记忆），normalizeApiSizeSettings 只会修正非法的 auto。
    normalizeApiSizeSettings('');
    const outpaintLocked = settings.outpaintResolutionLocked === true;
    dynamicParams.innerHTML = `
        ${renderImageRoutingControl()}
        ${renderProviderControl(providers)}
        ${renderModelControl(models)}
        ${renderCountVisualControl()}
        ${renderSizePickerControl('', true)}
        ${renderAnglePresetControl()}
        ${renderQualityControl()}
    `;
}
function renderApiVideoParams(){
    const providers = videoApiProviders();
    if(!settings.videoProvider || !providers.some(p => p.id === settings.videoProvider)) settings.videoProvider = providers[0]?.id || 'comfly';
    const models = filterJimengVideoModels(providerVideoModels(settings.videoProvider));
    if(!settings.videoModel || !models.includes(settings.videoModel)) settings.videoModel = models[0] || 'veo3-fast';
    dynamicParams.innerHTML = `
        ${renderVideoRoutingControl()}
        ${renderVideoProviderControl(providers)}
        ${renderVideoModelControl(models)}
        ${renderVideoResolutionControl()}
        ${renderVideoAspectControl()}
        ${renderVideoDurationControl()}
        ${renderVideoToggleControl('videoEnhancePrompt', tr('smart.videoEnhancePrompt'))}
        ${renderVideoToggleControl('videoEnableUpsample', tr('smart.videoUpsample'))}
        ${renderVideoToggleControl('videoGenerateAudio', tr('smart.videoGenerateAudio'))}
        ${renderVideoToggleControl('videoReturnLastFrame', tr('smart.videoReturnLastFrame'))}
        ${renderVideoToggleControl('videoCameraFixed', tr('smart.videoCameraFixed'))}
        ${renderVideoToggleControl('videoWatermark', tr('smart.videoWatermark'))}
        ${renderVideoToggleControl('videoMultimodal', tr('smart.videoMultimodal'))}
        ${renderVideoToggleControl('videoUseFrameRoles', tr('smart.videoUseFrameRoles'))}
        ${settings.videoProvider === 'jimeng' ? '' : renderVideoTrustedAssetControl()}
    `;
}
function renderVolcengineParams(){
    const provider = volcengineProvider();
    const providers = [provider];
    const models = providerImageModels('volcengine');
    settings.provider_id = 'volcengine';
    if(!settings.model || !models.includes(settings.model)) settings.model = models[0] || '';
    normalizeApiSizeSettings('');
    const outpaintLocked = settings.outpaintResolutionLocked === true;
    dynamicParams.innerHTML = `
        ${renderImageRoutingControl()}
        ${renderProviderControl(providers)}
        ${renderModelControl(models)}
        ${renderCountVisualControl()}
        ${renderSizePickerControl('', true)}
        ${renderAnglePresetControl()}
        ${renderQualityControl()}
    `;
}
function renderVolcengineVideoParams(){
    const provider = volcengineProvider();
    const providers = [provider];
    const models = volcengineVideoModels();
    settings.videoProvider = 'volcengine';
    if(!settings.videoModel || !models.includes(settings.videoModel)) settings.videoModel = models[0] || 'seedance-1.0-pro';
    dynamicParams.innerHTML = `
        ${renderVideoProviderControl(providers)}
        ${renderVideoModelControl(models)}
        ${renderVideoResolutionControl()}
        ${renderVideoAspectControl()}
        ${renderVideoDurationControl()}
        ${renderVideoToggleControl('videoEnhancePrompt', tr('smart.videoEnhancePrompt'))}
        ${renderVideoToggleControl('videoEnableUpsample', tr('smart.videoUpsample'))}
        ${renderVideoToggleControl('videoGenerateAudio', tr('smart.videoGenerateAudio'))}
        ${renderVideoToggleControl('videoCameraFixed', tr('smart.videoCameraFixed'))}
        ${renderVideoToggleControl('videoWatermark', tr('smart.videoWatermark'))}
        ${renderVideoToggleControl('videoMultimodal', tr('smart.videoMultimodal'))}
        ${renderVideoToggleControl('videoUseFrameRoles', tr('smart.videoUseFrameRoles'))}
        ${renderVideoTrustedAssetControl()}
    `;
}
function renderRunningHubParams(){
    const ref = selectedRunningHubRef();
    const fields = rhActiveFields();
    settings.rhPayment = settings.rhPayment === 'wallet' ? 'wallet' : 'free';
    settings.rhParams = settings.rhParams || {};
    settings.rhRandomActive = settings.rhRandomActive || {};
    if(!ref){
        dynamicParams.innerHTML = `<div class="muted-note">${escapeHtml(tr('smart.rhNeedConfig'))}</div>`;
        return;
    }
    if(ref.kind === 'model'){
        settings.provider_id = 'runninghub';
        settings.model = ref.id;
        normalizeApiSizeSettings('');
        dynamicParams.innerHTML = `
            ${renderRhConfigControl(ref)}
            ${renderImageRoutingControl()}
            ${renderCountVisualControl()}
            ${renderSizePickerControl('', true)}
            ${renderAnglePresetControl()}
            ${renderQualityControl()}
        `;
        return;
    }
    const mediaFields = fields.filter(f => ['image','video','audio'].includes(rhFieldRole(f))).length;
    const promptFields = fields.filter(f => rhFieldRole(f) === 'prompt').length;
    dynamicParams.innerHTML = `
        ${renderRhConfigControl(ref)}
        ${renderRhPaymentControl()}
        ${renderRhMachineControl()}
        <div class="rh-mini-summary">${escapeHtml(trf('smart.rhFieldsSummary', {m: mediaFields, p: promptFields}))}</div>
        ${fields.length ? fields.filter(f => !['image','video','audio','prompt'].includes(rhFieldRole(f))).map(renderRhSettingField).join('') : `<div class="muted-note">${escapeHtml(tr('smart.rhNeedFields'))}</div>`}
    `;
}
function renderRhConfigControl(ref){
    const models = runningHubEntries('model');
    const apps = runningHubEntries('app');
    const selected = ref ? runningHubEntryKey(ref.kind, ref.id) : '';
    const groupHtml = (kind, entries, label) => entries.length ? `
        <div class="model-list-label rh-list-label">${escapeHtml(label)}<span class="count">${entries.length}</span></div>
        ${entries.map(entry => {
            const id = runningHubEntryId(entry, kind);
            const key = runningHubEntryKey(kind, id);
            const icon = kind === 'model' ? 'box' : 'sparkles';
            const labelText = runningHubEntryLabel(entry, kind);
            return `<button type="button" class="direct-option rh-entry-option ${key === selected ? 'active' : ''}" data-smart-param="rhConfigKey" data-smart-value="${escapeHtml(key)}" data-model-option="${escapeHtml(`${key} ${labelText}`.toLocaleLowerCase())}"><i data-lucide="${icon}"></i><span>${escapeHtml(labelText)}</span></button>`;
        }).join('')}
    ` : '';
    return `<div class="smart-control rh-config-control">
        <button class="smart-pill" type="button"><i data-lucide="workflow"></i><span class="sub">${escapeHtml(ref ? runningHubEntryLabel(ref.entry, ref.kind) : tr('smart.rhConfig'))}</span><i data-lucide="chevron-down" class="pill-caret"></i></button>
        <div class="smart-popover compact-popover rh-picker-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.rhConfig'))}</div>
            ${modelSearchInputHtml('rhConfigKey', models.length + apps.length)}
            <div class="model-list rh-config-list" data-model-options="rhConfigKey">
                ${groupHtml('model', models, tr('smart.rhModelApi'))}${groupHtml('app', apps, tr('smart.rhApp'))}
                <div class="model-search-empty" data-model-search-empty hidden>${escapeHtml(tr('smart.modelSearchEmpty'))}</div>
            </div>
        </div>
    </div>`;
}
function renderRhPaymentControl(){
    const value = settings.rhPayment === 'wallet' ? 'wallet' : 'free';
    const labels = {free:tr('smart.rhFreeKey'), wallet:tr('smart.rhWalletKey')};
    return `<div class="smart-control rh-payment-control">
        <button class="smart-pill" type="button"><i data-lucide="key-round"></i><span>${escapeHtml(labels[value])}</span><i data-lucide="chevron-down" class="pill-caret"></i></button>
        <div class="smart-popover compact-popover rh-picker-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.rhKey'))}</div>
            <div class="model-list">
                ${Object.entries(labels).map(([key, label]) => `<button type="button" class="direct-option ${key === value ? 'active' : ''}" data-smart-param="rhPayment" data-smart-value="${escapeHtml(key)}"><span>${escapeHtml(label)}</span></button>`).join('')}
            </div>
        </div>
    </div>`;
}
function renderRhMachineControl(){
    const value = settings.rhInstanceType === 'plus' ? 'plus' : '';
    const labels = {'':'24G', plus:'48G'};
    return `<div class="smart-control rh-machine-control">
        <button class="smart-pill" type="button"><i data-lucide="cpu"></i><span>${escapeHtml(labels[value])}</span><i data-lucide="chevron-down" class="pill-caret"></i></button>
        <div class="smart-popover compact-popover rh-picker-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.rhMachine'))}</div>
            <div class="model-list">
                ${Object.entries(labels).map(([key, label]) => `<button type="button" class="direct-option ${key === value ? 'active' : ''}" data-smart-param="rhInstanceType" data-smart-value="${escapeHtml(key)}"><span>${escapeHtml(label)}</span></button>`).join('')}
            </div>
        </div>
    </div>`;
}
function renderMsParams(){
    settings.msgenModel = 'custom';
    if(!settings.msCustomModel) settings.msCustomModel = modelscopeImageModels()[0] || '';
    normalizeApiSizeSettings('ms');
    dynamicParams.innerHTML = `
        ${renderMsFunctionControl()}
        ${renderMsCustomModelPill()}
        ${renderCountVisualControl()}
        ${renderSizePickerControl('ms', false)}
    `;
}
function ratioLabel(prefix=''){
    const ratioKey = prefix ? `${prefix}Ratio` : 'ratio';
    const customKey = prefix ? `${prefix}CustomRatio` : 'customRatio';
    const sourceLabel = sourceImageRatioLabel(prefix) || tr('smart.imageRatio');
    const map = {square:'1:1', portrait:'2:3', landscape:'3:2', portrait43:'3:4', landscape43:'4:3', story:'9:16', wide:'16:9', ultrawide:'21:9', ultratall:'9:21', source:sourceLabel, custom:settings[customKey] || tr('smart.custom')};
    return map[settings[ratioKey] || 'square'] || '1:1';
}
function gcdInt(a, b){
    a = Math.abs(Math.round(Number(a) || 0));
    b = Math.abs(Math.round(Number(b) || 0));
    while(b){ const t = b; b = a % b; a = t; }
    return a || 1;
}
function imageSizeForRatio(img){
    const w = Math.round(Number(img?.natural_w || img?.width || img?.w || 0));
    const h = Math.round(Number(img?.natural_h || img?.height || img?.h || 0));
    return w > 0 && h > 0 ? {w, h} : null;
}
function sourceRatioImageForNode(node){
    const images = (node?.images || []).filter(img => img?.url && !isAudioMediaItem(img));
    if(!images.length) return null;
    if(canvasState.selectedImage.nodeId === node?.id && canvasState.selectedImage.index >= 0 && imagesForNode(node)[canvasState.selectedImage.index]){
        const selected = imagesForNode(node)[canvasState.selectedImage.index];
        if(imageSizeForRatio(selected)) return selected;
    }
    return images.find(img => imageSizeForRatio(img)) || images[0];
}
function reducedRatioForImage(img){
    const size = imageSizeForRatio(img);
    if(!size) return null;
    const d = gcdInt(size.w, size.h);
    return {w:Math.max(1, Math.round(size.w / d)), h:Math.max(1, Math.round(size.h / d))};
}
function sourceImageRatioLabel(prefix=''){
    const node = activeComposerNode() || selectedNode();
    const ratio = reducedRatioForImage(sourceRatioImageForNode(node));
    if(!ratio) return '';
    return `${ratio.w}:${ratio.h}`;
}
function applySourceRatioToSettings(prefix=''){
    const ratioKey = prefix ? `${prefix}Ratio` : 'ratio';
    if(settings[ratioKey] !== 'source') return;
    const ratio = reducedRatioForImage(sourceRatioImageForNode(activeComposerNode() || selectedNode()));
    if(!ratio) return;
    const customKey = prefix ? `${prefix}CustomRatio` : 'customRatio';
    const wKey = prefix ? `${prefix}CustomRatioWidth` : 'customRatioWidth';
    const hKey = prefix ? `${prefix}CustomRatioHeight` : 'customRatioHeight';
    settings[wKey] = ratio.w;
    settings[hKey] = ratio.h;
    settings[customKey] = `${ratio.w}:${ratio.h}`;
}
function resolutionLabel(prefix=''){
    const resKey = prefix ? `${prefix}Resolution` : 'resolution';
    const sizeKey = prefix ? `${prefix}CustomSize` : 'customSize';
    const value = settings[resKey] || ((!prefix && settings.engine === 'api') ? defaultSmartApiResolution(settings.model) : '1k');
    if(value === 'auto') return tr('smart.sizeAuto');
    return value === 'custom' ? (settings[sizeKey] || tr('smart.custom')) : value.toUpperCase();
}
function videoAspectIconClass(value){
    if(value === '16:9' || value === '21:9') return 'r-wide';
    if(value === '9:16' || value === '9:21') return 'r-story';
    if(value === '4:3') return 'r-landscape43';
    if(value === '3:4') return 'r-portrait43';
    if(value === 'keep_ratio' || value === 'adaptive') return 'r-source';
    return '';
}
function renderProviderControl(providers){
    const current = (providers || []).find(p => p.id === settings.provider_id) || apiProviderById(settings.provider_id);
    return `<div class="smart-control provider-control">
        <button class="smart-pill" type="button"><i data-lucide="plug-zap"></i><span class="sub">${escapeHtml(current?.name || settings.provider_id || tr('smart.platform'))}</span></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.apiPlatform'))}</div>
            <div class="model-list">
                ${providers.map(p => `<button type="button" class="direct-option ${p.id === settings.provider_id ? 'active' : ''}" data-smart-param="provider_id" data-smart-value="${escapeHtml(p.id)}"><span>${escapeHtml(p.name || p.id)}</span></button>`).join('') || `<div class="muted-note">${escapeHtml(tr('smart.noApiPlatform'))}</div>`}
            </div>
        </div>
    </div>`;
}
function renderImageRoutingControl(){
    // 自动路由为默认无感行为：按请求尺寸/分辨率自动选择兼容模型（1K/2K/4K）。
    // 不再渲染显式切换开关；用户主动点选服务商或模型时即切换为固定（manual）。
    return '';
}
function renderAnglePresetControl(){
    const options = [
        ['none', tr('smart.angleNone')], ['front', tr('smart.angleFront')], ['front-three-quarter', tr('smart.angleFrontQuarter')],
        ['side', tr('smart.angleSide')], ['rear-three-quarter', tr('smart.angleRearQuarter')], ['rear', tr('smart.angleRear')],
        ['top', tr('smart.angleTop')], ['low', tr('smart.angleLow')]
    ];
    const value = options.some(([key]) => key === settings.anglePreset) ? settings.anglePreset : 'none';
    const label = Object.fromEntries(options)[value];
    return `<div class="smart-control angle-preset-control">
        <button class="smart-pill" type="button"><i data-lucide="orbit"></i><span>${escapeHtml(label)}</span><i data-lucide="chevron-down" class="pill-caret"></i></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.angleTitle'))}</div>
            <div class="model-list">
                ${options.map(([key,text]) => `<button type="button" class="direct-option ${key === value ? 'active' : ''}" data-smart-param="anglePreset" data-smart-value="${escapeHtml(key)}"><span>${escapeHtml(text)}</span></button>`).join('')}
            </div>
        </div>
    </div>`;
}
function renderModelControl(models){
    const labelFor = providerModelNameLookup(settings.provider_id === 'volcengine' ? volcengineProvider() : (apiProviders || []).find(p => p.id === settings.provider_id));
    return `<div class="smart-control model-control">
        <button class="smart-pill" type="button"><i data-lucide="sparkles"></i><span class="sub">${escapeHtml(labelFor(settings.model) || tr('smart.model'))}</span></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.imageModel'))}</div>
            ${searchableModelOptions(models, 'model', settings.model, tr('smart.noImageModel'), labelFor)}
            <div class="model-selection-note">${escapeHtml(tr('smart.modelSelectionNote'))}</div>
        </div>
    </div>`;
}
function msModelLabel(key){
    if(key === 'custom') return tr('smart.custom');
    return MS_GEN_MODELS[key]?.label || key;
}
function renderMsFunctionControl(){
    return `<div class="smart-control provider-control">
        <button class="smart-pill" type="button"><i data-lucide="sparkles"></i><span class="sub">${escapeHtml(msModelLabel(settings.msgenModel) || 'Modelscope')}</span></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.msFunction'))}</div>
            <div class="model-list">
                ${Object.entries(MS_GEN_MODELS).map(([key]) => `<button type="button" class="direct-option ${key === settings.msgenModel ? 'active' : ''}" data-smart-param="msgenModel" data-smart-value="${escapeHtml(key)}"><span>${escapeHtml(msModelLabel(key))}</span></button>`).join('')}
            </div>
        </div>
    </div>`;
}
function renderMsCustomModelPill(){
    if(settings.msgenModel !== 'custom') return '';
    const models = modelscopeImageModels();
    const label = settings.msCustomModel || tr('smart.customModel');
    return `<div class="smart-control model-control">
        <button class="smart-pill" type="button"><i data-lucide="boxes"></i><span class="sub">${escapeHtml(label)}</span></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.msCustomModel'))}</div>
            ${searchableModelOptions(models, 'msCustomModel', settings.msCustomModel, tr('smart.noMsModel'))}
        </div>
    </div>`;
}
function sizePickerScope(prefix=''){
    const resKey = prefix ? `${prefix}Resolution` : 'resolution';
    const ratioKey = prefix ? `${prefix}Ratio` : 'ratio';
    const value = settings[resKey] || ((!prefix && settings.engine === 'api') ? defaultSmartApiResolution(settings.model) : '1k');
    if(value === 'auto') return 'auto';
    if(value === 'custom' || settings[ratioKey] === 'custom') return 'custom';
    return 'preset';
}
function sizePickerDefaultResolution(prefix=''){
    const value = (!prefix && settings.engine === 'api') ? defaultSmartApiResolution(settings.model) : '1k';
    return value === 'auto' ? '1k' : value;
}
function sizePickerLabel(prefix=''){
    if(!prefix && !apiImageUsesExplicitResolution(settings)) return ratioLabel(prefix);
    const scope = sizePickerScope(prefix);
    if(scope === 'auto') return tr('smart.sizeAuto');
    if(scope === 'custom'){
        const resKey = prefix ? `${prefix}Resolution` : 'resolution';
        const ratioKey = prefix ? `${prefix}Ratio` : 'ratio';
        const resText = resolutionLabel(prefix);
        const ratioText = ratioLabel(prefix);
        if(settings[resKey] === 'custom' && settings[ratioKey] === 'custom') return trf('smart.sizeCustomComboResRatio', {res: resText, ratio: ratioText});
        if(settings[resKey] === 'custom') return trf('smart.sizeCustomComboRes', {res: resText});
        if(settings[ratioKey] === 'custom') return trf('smart.sizeCustomComboRatio', {ratio: ratioText, res: resText});
        return trf('smart.sizeCustomComboRes', {res: resText});
    }
    return `${ratioLabel(prefix)} · ${resolutionLabel(prefix)}`;
}
function renderSizePickerControl(prefix='', includeSource=false){
    const ratioKey = prefix ? `${prefix}Ratio` : 'ratio';
    const resKey = prefix ? `${prefix}Resolution` : 'resolution';
    const customRatioKey = prefix ? `${prefix}CustomRatio` : 'customRatio';
    if(settings[ratioKey] === 'source') applySourceRatioToSettings(prefix);
    const scope = sizePickerScope(prefix);
    const options = (!prefix && settings.engine === 'api') ? ['auto','1k','2k','4k'] : ['1k','2k','4k'];
    const currentRes = settings[resKey] || ((!prefix && settings.engine === 'api') ? defaultSmartApiResolution(settings.model) : '1k');
    const currentRatio = settings[ratioKey] || 'square';
    const currentCustomRatio = settings[customRatioKey] || (currentRatio === 'source' ? sourceImageRatioLabel(prefix) : '');
    const allowAuto = !prefix && settings.engine === 'api' && settings.apiKind !== 'video' && isGptImageAutoSizeModel(settings.model);
    const ratios = [
        ['square','1:1',tr('smart.sizeRatioSquare')], ['portrait','2:3',tr('smart.sizeRatioPortrait')], ['landscape','3:2',tr('smart.sizeRatioLandscape')], ['portrait43','3:4',tr('smart.sizeRatioPortrait')], ['landscape43','4:3',tr('smart.sizeRatioLandscape')],
        ['story','9:16',tr('smart.sizeRatioStory')], ['wide','16:9',tr('smart.sizeRatioWide')], ['ultrawide','21:9',tr('smart.sizeRatioUltrawide')], ['ultratall','9:21',tr('smart.sizeRatioUltratall')],
        ...(includeSource ? [['source', sourceImageRatioLabel(prefix) || tr('smart.sizeRatioSource'), tr('smart.sizeRatioSourceAdaptive')]] : [])
    ];
    if(!prefix && !apiImageUsesExplicitResolution(settings)){
        return `<div class="smart-control size-picker-control resolution-free">
            <button class="smart-pill size-picker-pill" type="button"><i data-lucide="scan-line"></i><span class="size-picker-label"><span class="size-picker-type">${escapeHtml(tr('smart.sizeTypeAspect'))}</span><span class="size-picker-dot"></span><span class="size-picker-value">${escapeHtml(ratioLabel(prefix))}</span></span></button>
            <div class="smart-popover size-picker-popover resolution-free">
                <div class="smart-popover-title">${escapeHtml(tr('smart.sizeTitleAspect'))}</div>
                <div class="size-picker-pane size-picker-preset resolution-free">
                    <div class="size-picker-list">
                        ${ratios.map(([value, label, sub]) => `<button type="button" class="size-picker-option ${value === currentRatio ? 'active' : ''}" data-smart-param="${ratioKey}" data-smart-value="${escapeHtml(value)}"><span>${escapeHtml(label)}</span><small>${escapeHtml(sub)}</small></button>`).join('')}
                    </div>
                </div>
            </div>
        </div>`;
    }
    const wKey = prefix ? `${prefix}CustomWidth` : 'customWidth';
    const hKey = prefix ? `${prefix}CustomHeight` : 'customHeight';
    return `<div class="smart-control size-picker-control ${scope === 'auto' ? 'auto-mode' : ''} ${scope === 'custom' ? 'custom-mode' : ''}">
        <button class="smart-pill size-picker-pill" type="button"><i data-lucide="scan-line"></i><span class="size-picker-label"><span class="size-picker-type">${escapeHtml(tr('smart.sizeTypeSize'))}</span><span class="size-picker-dot"></span><span class="size-picker-value">${escapeHtml(sizePickerLabel(prefix))}</span></span></button>
        <div class="smart-popover size-picker-popover">
            <div class="size-picker-head">
                <div class="smart-popover-title">${escapeHtml(tr('smart.sizeTitlePicker'))}</div>
                <div class="size-picker-scope">
                    <button type="button" class="${scope === 'auto' ? 'active' : ''}" data-size-scope="auto" data-size-prefix="${escapeHtml(prefix)}" ${allowAuto ? '' : 'disabled'}>${escapeHtml(tr('smart.sizeAuto'))}</button>
                    <button type="button" class="${scope === 'preset' ? 'active' : ''}" data-size-scope="preset" data-size-prefix="${escapeHtml(prefix)}">${escapeHtml(tr('smart.sizeScopeSystem'))}</button>
                    <button type="button" class="${scope === 'custom' ? 'active' : ''}" data-size-scope="custom" data-size-prefix="${escapeHtml(prefix)}">${escapeHtml(tr('smart.custom'))}</button>
                </div>
            </div>
            ${scope === 'auto' ? `<div class="size-picker-pane size-picker-auto"><div class="size-picker-note"><strong>${escapeHtml(tr('smart.sizeAutoTitle'))}</strong><span>${escapeHtml(tr('smart.sizeAutoNote'))}</span></div></div>` : ''}
            ${scope === 'preset' ? `<div class="size-picker-pane size-picker-preset">
                <div class="size-picker-list">
                    ${ratios.map(([value, label, sub]) => `<button type="button" class="size-picker-option ${value === currentRatio ? 'active' : ''}" data-smart-param="${ratioKey}" data-smart-value="${escapeHtml(value)}"><span>${escapeHtml(label)}</span><small>${escapeHtml(sub)}</small></button>`).join('')}
                </div>
                <div class="size-picker-list">
                    ${options.filter(v => v !== 'auto').map(value => `<button type="button" class="size-picker-option ${value === currentRes ? 'active' : ''}" data-smart-param="${resKey}" data-smart-value="${value}"><span>${value.toUpperCase()}</span></button>`).join('')}
                </div>
            </div>` : ''}
            ${scope === 'custom' ? `<div class="size-picker-pane size-picker-custom">
                <div class="size-custom-box">
                    <div class="size-custom-title">${escapeHtml(tr('smart.sizeCustomTitle'))}</div>
                    <div class="size-custom-presets">
                        ${['1k','2k','4k'].map(value => `<button type="button" class="size-custom-preset-btn ${value === currentRes ? 'active' : ''}" data-smart-param="${resKey}" data-smart-value="${value}">${value.toUpperCase()}</button>`).join('')}
                    </div>
                    <div class="size-custom-row"><input type="number" data-param="${wKey}" value="${escapeHtml(settings[wKey] || '')}" placeholder="${escapeHtml(tr('canvas.width'))}"><span>×</span><input type="number" data-param="${hKey}" value="${escapeHtml(settings[hKey] || '')}" placeholder="${escapeHtml(tr('canvas.height'))}"></div>
                </div>
            </div>` : ''}
        </div>
    </div>`;
}
function renderQualityControl(){
    const value = settings.quality || 'auto';
    const labels = {auto:tr('smart.qualityAuto'), low:tr('smart.qualityLow'), medium:tr('smart.qualityMid'), high:tr('smart.qualityHigh')};
    return `<div class="smart-control quality-control">
        <button class="smart-pill" type="button"><i data-lucide="sliders-horizontal"></i><span>${escapeHtml(labels[value] || value)}</span></button>
        <div class="smart-popover compact-popover">
            <div class="smart-popover-title">${escapeHtml(tr('smart.quality'))}</div>
            <div class="seg-row">
                ${Object.entries(labels).map(([k, l]) => `<button type="button" class="${k === value ? 'active' : ''}" data-smart-param="quality" data-smart-value="${escapeHtml(k)}">${escapeHtml(l)}</button>`).join('')}
            </div>
        </div>
    </div>`;
}
function renderCountVisualControl(){
    const value = Number(settings.count || 1);
    return `<div class="smart-control count-control">
        <button class="smart-pill" type="button"><i data-lucide="copy"></i><span>${value}${window.StudioI18n?.lang?.() !== 'en' ? ' ' + escapeHtml(tr('smart.countUnit')) : ''}</span></button>
        <div class="smart-popover compact-popover" style="min-width:170px">
            <div class="smart-popover-title">${escapeHtml(tr('smart.count'))}</div>
            <div class="count-grid">
                ${[1,2,3,4,5,6,7,8].map(n => `<button type="button" class="count-cell ${n === value ? 'active' : ''}" data-smart-param="count" data-smart-value="${n}">${n}</button>`).join('')}
            </div>
        </div>
    </div>`;
}
const RH_KNOWN_FIELD_OPTIONS = {
    aspectRatio:['1:1','16:9','9:16','4:3','3:4','4:5','5:4','3:2','2:3','21:9','9:21'],
    aspect_ratio:['1:1','16:9','9:16','4:3','3:4','4:5','5:4','3:2','2:3','21:9','9:21'],
    ratio:['1:1','16:9','9:16','21:9','9:21','4:3','3:4','4:5','5:4','3:2','2:3'],
    resolution:['1k','2k','4k','8k'],
    size:['512','768','1024','1280','1536','2048'],
    quality:['low','medium','high','best'],
    scheduler:['normal','karras','exponential','sgm_uniform','simple','ddim_uniform'],
    sampler:['euler','euler_ancestral','heun','dpm_2','dpm_2_ancestral','lms','dpmpp_2m','dpmpp_sde','ddim','uni_pc']
};
function rhParamKey(nodeId, fieldName){
    return `${nodeId ?? ''}::${fieldName ?? ''}`;
}
function rhFieldKind(field){
    const type = String(field?.fieldType || '').trim().toUpperCase();
    if(type === 'IMAGE') return 'image';
    if(type === 'VIDEO') return 'video';
    if(type === 'AUDIO') return 'audio';
    if(type === 'SLIDER') return 'slider';
    if(['NUMBER','FLOAT','INTEGER','INT'].includes(type)) return 'number';
    if(['BOOLEAN','BOOL'].includes(type)) return 'boolean';
    const key = `${field?.fieldName || ''} ${field?.fieldValue || ''}`.toLowerCase();
    if(/\b(image|img|mask|photo|picture)\b/.test(key) || /\.(png|jpe?g|webp|gif|bmp)(\?|$)/i.test(key)) return 'image';
    if(/\b(video|movie|mp4)\b/.test(key) || /\.(mp4|webm|mov|m4v|mkv)(\?|$)/i.test(key)) return 'video';
    if(/\b(audio|sound|music|voice)\b/.test(key) || /\.(mp3|wav|ogg|m4a|flac|aac)(\?|$)/i.test(key)) return 'audio';
    return 'text';
}
function rhFieldRole(field){
    const kind = rhFieldKind(field);
    if(['image','video','audio','number','slider','boolean'].includes(kind)) return kind;
    const text = `${field?.fieldName || ''} ${field?.label || ''} ${field?.group || ''}`.toLowerCase();
    if(/prompt|positive|negative|text|caption|description|关键词|提示词|正向|负向/.test(text)) return 'prompt';
    return 'text';
}
function rhExtractFieldOptions(field){
    const candidates = [field?.fieldData, field?.options, field?.list, field?.values, field?.enum, field?.choices, field?.items, field?.selectOptions, field?.dropdown];
    for(const candidate of candidates){
        if(!Array.isArray(candidate) || !candidate.length) continue;
        if(candidate.every(x => ['string','number'].includes(typeof x))) return candidate.map(String);
        if(candidate.every(x => x && typeof x === 'object' && ('value' in x || 'label' in x || 'name' in x))){
            return candidate.map(x => x.value ?? x.label ?? x.name).filter(v => v !== undefined && v !== null).map(String);
        }
    }
    const name = String(field?.fieldName || '').trim();
    if(name){
        if(RH_KNOWN_FIELD_OPTIONS[name]) return RH_KNOWN_FIELD_OPTIONS[name].map(String);
        const hit = Object.keys(RH_KNOWN_FIELD_OPTIONS).find(k => k.toLowerCase() === name.toLowerCase());
        if(hit) return RH_KNOWN_FIELD_OPTIONS[hit].map(String);
    }
    return null;
}
function rhDefaultValue(field){
    let value = field?.fieldValue;
    if(Array.isArray(value)) value = value[0];
    if(value === undefined || value === null || typeof value === 'object') return '';
    return String(value);
}
function rhRandomEnabled(field){
    return rhFieldKind(field) === 'number' && field?.random_enabled === true;
}
function smartRhRandomActive(key){
    return smartRhRandomActiveFor(settings, key);
}
function smartRhRandomActiveFor(sourceSettings=settings, key){
    sourceSettings = sourceSettings || settings;
    sourceSettings.rhRandomActive = sourceSettings.rhRandomActive || {};
    return sourceSettings.rhRandomActive[key] !== false;
}
function toggleSmartRhRandom(key){
    const field = rhActiveFields().find(f => rhParamKey(f.nodeId, f.fieldName) === key);
    if(!rhRandomEnabled(field)) return;
    settings.rhRandomActive = settings.rhRandomActive || {};
    settings.rhRandomActive[key] = !smartRhRandomActive(key);
    persistActiveSmartSettings();
    renderDynamicParams();
    scheduleSave();
}
function smartRhRandomValue(field){
    return smartRandomValue({
        input:field.fieldName,
        name:field.label || field.fieldName,
        min:field.min,
        max:field.max,
        step:field.step,
        type:'number'
    });
}
function rhParamValue(field, media=null, sourceSettings=settings, fields=null, randomValues=smartRhRandomValues){
    sourceSettings = sourceSettings || settings;
    sourceSettings.rhParams = sourceSettings.rhParams || {};
    const key = rhParamKey(field.nodeId, field.fieldName);
    const param = sourceSettings.rhParams[key];
    const kind = rhFieldKind(field);
    if(['image','video','audio'].includes(kind)){
        const idx = rhFieldIndexes(fields || rhActiveFields(sourceSettings))[key] || 0;
        const up = media?.[kind]?.[idx]?.url || '';
        return up || param?.value || rhDefaultValue(field);
    }
    if(rhRandomEnabled(field) && smartRhRandomActiveFor(sourceSettings, key)){
        if(randomValues[key] === undefined) randomValues[key] = smartRhRandomValue(field);
        return randomValues[key];
    }
    if(rhFieldRole(field) === 'prompt') return param?.value ?? (media?.prompt || rhDefaultValue(field));
    return param?.value ?? rhDefaultValue(field);
}
function rhDefaultPromptSuggestion(){
    if(settings.engine !== 'runninghub') return '';
    const fields = rhActiveFields().filter(field => rhFieldRole(field) === 'prompt');
    for(const field of fields){
        const value = rhDefaultValue(field).trim();
        if(value) return value;
    }
    return '';
}
function updatePromptPlaceholder(){
    if(!promptInput) return;
    const suggestion = rhDefaultPromptSuggestion();
    promptInput.dataset.placeholder = suggestion || tr('smart.promptPlaceholder');
}
function rhFieldIndexes(fields){
    const counters = {image:0, video:0, audio:0};
    const map = {};
    sortRunningHubFields(fields).forEach(field => {
        const kind = rhFieldKind(field);
        if(['image','video','audio'].includes(kind)){
            map[rhParamKey(field.nodeId, field.fieldName)] = counters[kind]++;
        }
    });
    return map;
}
function rhMediaForRun(prompt, refs){
    const cleanRefs = (refs || []).filter(ref => ref?.url);
    return {
        refs:cleanRefs,
        image:imageRefsOnly(cleanRefs),
        video:videoRefsOnly(cleanRefs),
        audio:audioRefsOnly(cleanRefs),
        prompt:String(prompt || '').trim()
    };
}
function tempShUploadedUrlFor(url, sourceSettings=settings){
    const source = String(url || '');
    const manualLinks = ((sourceSettings || settings).videoTempShLinks || []).filter(item => item?.manual === true);
    const links = [...(transientSmartCloudLinks || []), ...manualLinks];
    const match = links.find(item =>
        item?.url && (item?.source === source || item?.originalLocalUrl === source || item?.url === source)
    );
    return match?.url || url;
}
function mediaRefSourceUrl(ref){
    return localDisplayUrlForMediaItem(ref) || ref?.sourceUrl || ref?.originalLocalUrl || ref?.url || '';
}
function applyUploadedUrlsToSmartRefs(refs, sourceSettings=settings){
    return (refs || []).map(ref => {
        if(!ref?.url) return ref;
        const sourceUrl = mediaRefSourceUrl(ref);
        const url = tempShUploadedUrlFor(sourceUrl, sourceSettings);
        return url && url !== ref.url ? {...ref, url, originalLocalUrl:ref.originalLocalUrl || ref.url} : ref;
    });
}
function manualSmartVideoLink(sourceSettings=settings){
    return ((sourceSettings || settings).videoTempShLinks || []).find(item => item?.manual === true && item?.url) || null;
}
function manualSmartMediaLinks(sourceSettings=settings){
    return ((sourceSettings || settings).videoTempShLinks || []).filter(item => item?.manual === true && item?.url);
}
function renderedInputMediaRefs(){
    if(!inputThumbsRow) return [];
    return [...inputThumbsRow.querySelectorAll('.input-thumb')].map((el, index) => ({
        url:el.dataset.url || '',
        sourceUrl:el.dataset.sourceUrl || el.dataset.url || '',
        nodeId:el.dataset.nodeId || '',
        imageIndex:Number.isFinite(Number(el.dataset.imageIndex)) ? Number(el.dataset.imageIndex) : index,
        name:tr('smart.inputNum').replace('{n}', String(index + 1)),
        role:`image_${index + 1}`
    })).filter(ref => ref.url);
}
function currentSmartMediaRefs(node){
    if(!node) return [];
    const request = buildPromptRequest(node, null, true, smartLoopContext);
    return (request.refs || []).filter(ref => ref?.url && ['image','video'].includes(mediaKindForItem(ref)));
}
function currentUploadMediaRefs(node){
    const rendered = renderedInputMediaRefs();
    if(rendered.length) return rendered;
    return currentSmartMediaRefs(node);
}
function currentSmartMediaLinks(node=null){
    return currentUploadMediaRefs(node || activeSettingsSubject()).map(ref => {
        const sourceUrl = mediaRefSourceUrl(ref);
        const uploaded = tempShUploadedUrlFor(sourceUrl);
        return uploaded && uploaded !== sourceUrl ? uploaded : '';
    }).filter(Boolean);
}
function clearManualSmartVideoUrl(){
    settings.videoTempShLinks = (settings.videoTempShLinks || []).filter(item => item?.manual !== true);
}
function splitManualMediaUrls(text){
    return String(text || '')
        .split(/[\s,，]+/)
        .map(url => url.trim())
        .filter(Boolean);
}
async function uploadMediaRefToCloud(ref){
    const kind = mediaKindForItem(ref);
    const sourceUrl = mediaRefSourceUrl(ref);
    if(!sourceUrl) throw new Error(tr('smart.noMediaToUpload'));
    const existing = tempShUploadedUrlFor(sourceUrl);
    if(existing && existing !== sourceUrl) return existing;
    if(/^https?:\/\//i.test(sourceUrl)) return sourceUrl;
    const response = await fetch('/api/cloud-video/upload', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({url:sourceUrl, service:'auto'})
    });
    if(!response.ok) throw new Error(await smartResponseErrorMessage(response, tr('smart.cloudUploadFailed')));
    const data = await response.json();
    const uploadedUrl = data.url || '';
    if(!uploadedUrl) throw new Error(tr('smart.cloudNoUrl'));
    transientSmartCloudLinks = [
        ...(transientSmartCloudLinks || []).filter(item => item?.source !== sourceUrl),
        {source:sourceUrl, url:uploadedUrl, expires:data.expires || '3 days', kind}
    ];
    return uploadedUrl;
}
function applyManualVideoUrlToSmartRef(ref, manualUrl){
    if(!manualUrl) return;
    const sourceUrl = mediaRefSourceUrl(ref) || manualUrl;
    settings.videoTempShLinks = [
        ...(settings.videoTempShLinks || []).filter(item => item?.source !== sourceUrl),
        {source:sourceUrl, url:manualUrl, manual:true}
    ];
}
async function setCurrentSmartManualVideoUrl(){
    const node = activeSettingsSubject();
    if(!node) return '';
    savePromptDraftForCurrent();
    const refs = currentUploadMediaRefs(node);
    const firstLocal = refs.find(ref => ref?.url && !isRemoteVideoReferenceUrl(ref.url));
    const firstAny = firstLocal || refs[0] || null;
    const linkedUrls = currentSmartMediaLinks(node);
    const currentLinks = linkedUrls.length ? linkedUrls : (firstAny ? [tempShUploadedUrlFor(mediaRefSourceUrl(firstAny))] : []);
    const value = await openAssetNameDialog({
        title:refs.length > 1 ? trf('smart.mediaUrlDialogTitleMultiple', {n: refs.length}) : tr('smart.mediaUrlDialogTitle'),
        value:currentLinks.filter(isRemoteVideoReferenceUrl).join('\n'),
        placeholder:refs.length > 1 ? tr('smart.mediaUrlPlaceholderMultiple') : tr('smart.mediaUrlPlaceholderSingle'),
        cancelValue:null,
        multiline:refs.length > 1
    });
    if(value === null) return '';
    const urls = splitManualMediaUrls(value);
    if(!urls.length){
        clearManualSmartVideoUrl();
        persistActiveSmartSettings();
        scheduleSave();
        render();
        toast(tr('smart.manualUrlCleared'));
        return '';
    }
    const invalid = urls.find(url => !isRemoteVideoReferenceUrl(url));
    if(invalid){
        toast(tr('smart.manualUrlInvalid'));
        return '';
    }
    clearManualSmartVideoUrl();
    const targets = refs.length ? refs : [firstAny].filter(Boolean);
    urls.forEach((url, index) => {
        const target = targets[index] || targets[targets.length - 1] || {url};
        applyManualVideoUrlToSmartRef(target, url);
    });
    persistActiveSmartSettings();
    scheduleSave();
    render();
    toast(trf('smart.mediaUrlsSet', {n: urls.length}));
    return urls[0] || '';
}
async function uploadCurrentSmartVideosToCloud(){
    const node = activeSettingsSubject();
    if(!node) return [];
    savePromptDraftForCurrent();
    const refs = currentUploadMediaRefs(node);
    const localRefs = refs.filter(ref => {
        const sourceUrl = ref?.sourceUrl || ref?.originalLocalUrl || ref?.url || '';
        if(!sourceUrl) return false;
        const uploaded = tempShUploadedUrlFor(sourceUrl);
        return uploaded !== sourceUrl || !isRemoteVideoReferenceUrl(sourceUrl);
    });
    if(!localRefs.length){
        toast(tr('smart.inputAlreadyCloud'));
        return [];
    }
    const btn = dynamicParams?.querySelector('[data-trusted-source="cloud"]') || inputThumbsRow?.querySelector('[data-temp-sh-upload-video]');
    if(btn) btn.disabled = true;
    toast(trf('smart.uploadingMedia', {n: localRefs.length}));
    try {
        const urls = [];
        for(const ref of localRefs){
            urls.push(await uploadMediaRefToCloud(ref));
        }
        toast(trf('smart.cloudUploadDone', {n: urls.length}));
        return urls;
    } finally {
        if(btn) btn.disabled = false;
    }
}
async function rhUploadValueIfNeeded(value, sourceSettings=settings){
    const text = String(value || '').trim();
    if(!text) return '';
    if(!/^https?:\/\//i.test(text) && !text.startsWith('/output/') && !text.startsWith('/assets/')) return text;
    const res = await fetch('/api/runninghub/upload-asset', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({url:text, useWallet:(sourceSettings || settings).rhPayment === 'wallet'})
    });
    const data = await res.json();
    if(!res.ok || data.success === false) throw new Error(data.detail || data.error || tr('smart.rhUploadFailed'));
    return data.data?.fileName || text;
}
async function rhBuildNodeInfoList(media, sourceSettings=settings, randomValues=smartRhRandomValues){
    const fields = rhActiveFields(sourceSettings);
    const result = [];
    const indexes = rhFieldIndexes(fields);
    for(const field of fields){
        const kind = rhFieldKind(field);
        const key = rhParamKey(field.nodeId, field.fieldName);
        let value = rhParamValue(field, media, sourceSettings, fields, randomValues);
        if(rhFieldRole(field) === 'prompt' && !String(value || '').trim()) value = rhDefaultValue(field);
        if(['image','video','audio'].includes(kind)) value = await rhUploadValueIfNeeded(value, sourceSettings);
        if(['number','slider'].includes(kind) && String(value ?? '').trim() !== '' && !Number.isNaN(Number(value))) value = Number(value);
        result.push({nodeId:field.nodeId, fieldName:field.fieldName, fieldValue:value});
    }
    return result;
}
function renderRhSettingField(field){
    const key = rhParamKey(field.nodeId, field.fieldName);
    const kind = rhFieldRole(field);
    const label = field.label || field.fieldName || 'Field';
    const value = rhParamValue(field, null);
    const options = rhExtractFieldOptions(field);
    if(kind === 'boolean'){
        const active = String(value).toLowerCase() === 'true';
        return `<button type="button" class="setting-check ${active ? 'active' : ''}" data-rh-bool="${escapeHtml(key)}"><span class="check-box"></span><span>${escapeHtml(label)}</span></button>`;
    }
    if(kind === 'slider'){
        const min = Number.isFinite(Number(field.min)) ? Number(field.min) : 0;
        const max = Number.isFinite(Number(field.max)) && Number(field.max) > min ? Number(field.max) : 1;
        const step = Number.isFinite(Number(field.step)) && Number(field.step) > 0 ? Number(field.step) : 0.01;
        const numericValue = Number.isFinite(Number(value)) ? Number(value) : min;
        return `<div class="smart-control rh-slider-control" title="${escapeHtml(label)}">
            <button class="smart-pill" type="button"><span class="sub">${escapeHtml(label)}</span><span class="rh-slider-pill-value">${escapeHtml(numericValue)}</span></button>
            <div class="smart-popover compact-popover rh-picker-popover rh-param-popover rh-slider-popover">
                <div class="smart-popover-title"><span>${escapeHtml(label)}</span><span class="rh-slider-value">${escapeHtml(numericValue)}</span></div>
                <input type="range" class="smart-range rh-slider-input" data-rh-param="${escapeHtml(key)}" data-rh-type="slider" min="${escapeHtml(min)}" max="${escapeHtml(max)}" step="${escapeHtml(step)}" value="${escapeHtml(numericValue)}">
            </div>
        </div>`;
    }
    if(options?.length){
        const curLabel = String(value || options[0] || label);
        return `<div class="smart-control rh-dropdown-control" title="${escapeHtml(label)}">
            <button class="smart-pill" type="button"><span class="sub">${escapeHtml(curLabel)}</span><i data-lucide="chevron-down" class="pill-caret"></i></button>
            <div class="smart-popover compact-popover rh-picker-popover rh-param-popover">
                <div class="smart-popover-title">${escapeHtml(label)}</div>
                <div class="model-list rh-param-list">
                    ${options.map(o => `<button type="button" class="direct-option ${String(o) === String(value) ? 'active' : ''}" data-rh-pick="${escapeHtml(key)}" data-rh-value="${escapeHtml(o)}"><span>${escapeHtml(o)}</span></button>`).join('') || `<div class="muted-note">${escapeHtml(tr('smart.noOption'))}</div>`}
                </div>
            </div>
        </div>`;
    }
    const type = kind === 'number' ? 'number' : 'text';
    const inputHtml = `<input type="${type}" data-rh-param="${escapeHtml(key)}" value="${escapeHtml(value)}">`;
    if(kind === 'number' && rhRandomEnabled(field)){
        const active = smartRhRandomActive(key);
        return `<div class="num-with-dice" title="${escapeHtml(label)}">
            <span class="num-label">${escapeHtml(label)}</span>
            ${inputHtml}
            <button type="button" class="dice-btn ${active ? 'active' : ''}" data-rh-random="${escapeHtml(key)}" title="${escapeHtml(active ? tr('smart.diceOn') : tr('smart.diceOff'))}"><i data-lucide="dice-5"></i></button>
        </div>`;
    }
    return `<div class="num-compact ${type === 'text' ? 'rh-text-param' : ''}" title="${escapeHtml(label)}"><span class="num-label">${escapeHtml(label)}</span>${inputHtml}</div>`;
}
function smartRandomValue(field){
    const isFloat = Number(field.step) > 0 && Number(field.step) < 1;
    let min = Number.isFinite(Number(field.min)) ? Number(field.min) : null;
    let max = Number.isFinite(Number(field.max)) ? Number(field.max) : null;
    const name = `${field.input || ''} ${field.name || ''}`.toLowerCase();
    const looksSeed = name.includes('seed') || name.includes('noise') || name.includes('随机') || name.includes('噪');
    if(min === null) min = looksSeed ? 1 : 0;
    if(max === null || max <= min) max = looksSeed ? 4294967295 : 999999;
    if(looksSeed) max = Math.min(max, 4294967295);
    const value = min + Math.random() * (max - min);
    if(isFloat){
        const precision = Math.min(8, Math.max(1, String(field.step).split('.')[1]?.length || 2));
        return Number(value.toFixed(precision));
    }
    return Math.floor(value);
}
function setDynamicSetting(key, value){
    const numericKeys = new Set(['count','width','height','videoDuration','enhanceStrength','enhanceUpscaleRes','editUpscaleRes','customRatioWidth','customRatioHeight','customWidth','customHeight','msCustomRatioWidth','msCustomRatioHeight','msCustomWidth','msCustomHeight']);
    const layoutKeys = new Set(['routingMode','anglePreset','provider_id','model','resolution','ratio','msgenModel','msCustomModel','msResolution','msRatio','videoRoutingMode','videoProvider','videoModel','videoAspect','videoResolution','quality','count','rhConfigKey','rhPayment','rhInstanceType']);
    settings[key] = numericKeys.has(key) && value !== '' ? Number(value) : value;
    if(key === 'provider_id') settings.model = '';
    if(key === 'videoProvider') settings.videoModel = '';
    // 模型选择器在自动路由下也可见；用户明确点选模型时，将这次生成切到固定模型，
    // 这样 UI 选择和实际请求不会出现“看起来选了但 payload 仍是 auto”的错觉。
    if(key === 'model' && settings.routingMode !== 'manual') settings.routingMode = 'manual';
    if(key === 'videoModel' && settings.videoRoutingMode !== 'manual') settings.videoRoutingMode = 'manual';
    if(key === 'videoMultimodal') settings._videoMultimodalUserSet = true;
    if(key === 'videoMultimodal' && settings.videoMultimodal) settings.videoUseFrameRoles = false;
    normalizeSmartVideoModeSettings(settings, key === 'videoUseFrameRoles');
    if(key === 'resolution'){
        if(settings.resolution === 'custom') settings.ratio = '';
        else if(!settings.ratio) settings.ratio = 'square';
    }
    if(key === 'ratio') applySourceRatioToSettings('');
    if(key === 'msResolution'){
        if(settings.msResolution === 'custom') settings.msRatio = '';
        else if(!settings.msRatio) settings.msRatio = 'square';
    }
    if(key === 'msRatio') applySourceRatioToSettings('ms');
    if(key === 'customRatioWidth' || key === 'customRatioHeight'){
        settings.customRatio = settings.customRatioWidth && settings.customRatioHeight ? `${settings.customRatioWidth}:${settings.customRatioHeight}` : '';
        settings.ratio = 'custom';
    }
    if(key === 'msCustomRatioWidth' || key === 'msCustomRatioHeight'){
        settings.msCustomRatio = settings.msCustomRatioWidth && settings.msCustomRatioHeight ? `${settings.msCustomRatioWidth}:${settings.msCustomRatioHeight}` : '';
        settings.msRatio = 'custom';
    }
    if(key === 'customWidth' || key === 'customHeight'){
        settings.customSize = settings.customWidth && settings.customHeight ? `${settings.customWidth}x${settings.customHeight}` : '';
        settings.resolution = 'custom';
    }
    if(key === 'msCustomWidth' || key === 'msCustomHeight'){
        settings.msCustomSize = settings.msCustomWidth && settings.msCustomHeight ? `${settings.msCustomWidth}x${settings.msCustomHeight}` : '';
        settings.msResolution = 'custom';
    }
    const sizeKeys = new Set(['resolution','ratio','customRatio','customRatioWidth','customRatioHeight','customWidth','customHeight','customSize']);
    const unlockOutpaintSize = settings.outpaintResolutionLocked && sizeKeys.has(key);
    if(unlockOutpaintSize){
        delete settings.outpaintResolutionLocked;
        const subject = activeSettingsSubject();
        if(subject) delete subject.outpaintSize;
    }
    if(key === 'rhConfigKey'){
        settings.rhParams = {};
        settings.rhRandomActive = {};
    }
    persistActiveSmartSettings();
    rememberRecentSmartSettings(settings, activeSettingsSubject());
    if(layoutKeys.has(key)) renderDynamicParams();
    scheduleSave();
}
function closeAllSmartPopovers(){
    document.querySelectorAll('.smart-control.pinned, .smart-control.interacting').forEach(c => c.classList.remove('pinned', 'interacting'));
}
// 悬浮打开弹层后点了里面的参数：标记 interacting，让它熬过重渲染不收起；鼠标真正离开该控件时才关闭。
function markControlInteracting(el){
    const ctrl = el?.closest?.('.smart-control');
    if(ctrl && !ctrl.classList.contains('pinned')) ctrl.classList.add('interacting');
}
function bindDynamicParams(){
    dynamicParams.querySelectorAll('.smart-control').forEach(ctrl => {
        // 悬浮态的多选：鼠标移出整个控件（含上方弹层，弹层是 DOM 子节点）才解除，途中点参数不收起。
        ctrl.onmouseleave = () => ctrl.classList.remove('interacting');
    });
    dynamicParams.querySelectorAll('.smart-control > .smart-pill').forEach(pill => {
        pill.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const ctrl = pill.parentElement;
            const wasPinned = ctrl.classList.contains('pinned');
            closeAllSmartPopovers();
            if(!wasPinned) ctrl.classList.add('pinned');
        };
    });
    dynamicParams.querySelectorAll('[data-smart-param]').forEach(btn => {
        btn.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            markControlInteracting(btn);
            setDynamicSetting(btn.dataset.smartParam, btn.dataset.smartValue);
            if(btn.dataset.smartParam === 'videoDuration') renderDynamicParams();
        };
    });
    dynamicParams.querySelectorAll('[data-model-search]').forEach(modelSearchInput => {
        const optionList = modelSearchInput.closest('.smart-popover')?.querySelector('[data-model-options]');
        if(!optionList) return;
        const emptyState = optionList.querySelector('[data-model-search-empty]');
        const applyModelSearch = () => {
            const query = String(modelSearchInput.value || '').trim().toLocaleLowerCase();
            let visible = 0;
            optionList.querySelectorAll('[data-model-option]').forEach(option => {
                const matches = !query || String(option.dataset.modelOption || '').includes(query);
                option.hidden = !matches;
                if(matches) visible += 1;
            });
            if(emptyState) emptyState.hidden = visible > 0 || !query;
        };
        modelSearchInput.oninput = applyModelSearch;
        modelSearchInput.onmousedown = event => {
            event.stopPropagation();
            markControlInteracting(modelSearchInput);
        };
        modelSearchInput.onclick = event => {
            event.stopPropagation();
            markControlInteracting(modelSearchInput);
        };
        modelSearchInput.onkeydown = event => {
            if(event.key === 'Escape'){
                event.preventDefault();
                modelSearchInput.value = '';
                applyModelSearch();
            }
            event.stopPropagation();
        };
    });
    dynamicParams.querySelectorAll('[data-size-scope]').forEach(btn => {
        btn.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            markControlInteracting(btn);
            const prefix = btn.dataset.sizePrefix || '';
            const scope = btn.dataset.sizeScope;
            const resKey = prefix ? `${prefix}Resolution` : 'resolution';
            const ratioKey = prefix ? `${prefix}Ratio` : 'ratio';
            const allowAuto = !prefix && settings.engine === 'api' && settings.apiKind !== 'video' && isGptImageAutoSizeModel(settings.model);
            if(scope === 'auto'){
                if(!allowAuto) return;
                settings[resKey] = 'auto';
                if(!settings[ratioKey]) settings[ratioKey] = 'square';
            } else if(scope === 'custom'){
                settings[resKey] = 'custom';
            } else {
                settings[resKey] = ['1k','2k','4k'].includes(settings[resKey]) ? settings[resKey] : sizePickerDefaultResolution(prefix);
                if(!settings[ratioKey] || settings[ratioKey] === 'custom') settings[ratioKey] = 'square';
            }
            persistActiveSmartSettings();
            rememberRecentSmartSettings(settings, activeSettingsSubject());
            renderDynamicParams();
            scheduleSave();
        };
    });
    dynamicParams.querySelectorAll('[data-param]').forEach(input => {
        input.onclick = event => event.stopPropagation();
        input.oninput = input.onchange = event => {
            event?.stopPropagation?.();
            setDynamicSetting(input.dataset.param, input.value);
            if(input.dataset.param === 'videoDuration' && event?.type === 'change') renderDynamicParams();
        };
    });
    dynamicParams.querySelectorAll('[data-toggle-param]').forEach(btn => {
        btn.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            settings[btn.dataset.toggleParam] = !settings[btn.dataset.toggleParam];
            if(btn.dataset.toggleParam === 'videoMultimodal') settings._videoMultimodalUserSet = true;
            if(btn.dataset.toggleParam === 'videoMultimodal' && settings.videoMultimodal) settings.videoUseFrameRoles = false;
            normalizeSmartVideoModeSettings(settings, btn.dataset.toggleParam === 'videoUseFrameRoles');
            persistActiveSmartSettings();
            renderDynamicParams();
            scheduleSave();
        };
    });
    dynamicParams.querySelectorAll('[data-trusted-source]').forEach(btn => {
        btn.onclick = async event => {
            event.preventDefault();
            event.stopPropagation();
            const src = btn.dataset.trustedSource;
            settings.videoTrustedSource = ['library','cloud','manual'].includes(src) ? src : 'library';
            persistActiveSmartSettings();
            renderDynamicParams();
            scheduleSave();
            try {
                if(src === 'cloud') await uploadCurrentSmartVideosToCloud();
                else if(src === 'manual') await setCurrentSmartManualVideoUrl();
            } catch(e) {
                toast((e.message || tr('smart.opFailed')).slice(0, 180));
            }
        };
    });
    dynamicParams.querySelectorAll('[data-rh-bool]').forEach(btn => {
        btn.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            settings.rhParams = settings.rhParams || {};
            const key = btn.dataset.rhBool;
            const field = rhActiveFields().find(f => rhParamKey(f.nodeId, f.fieldName) === key);
            const cur = settings.rhParams[key] || {};
            const on = String(rhParamValue(field, null)).toLowerCase() === 'true';
            settings.rhParams[key] = {...cur, value:String(!on)};
            persistActiveSmartSettings();
            renderDynamicParams();
            scheduleSave();
        };
    });
    dynamicParams.querySelectorAll('[data-rh-param]').forEach(input => {
        input.onclick = event => event.stopPropagation();
        input.oninput = input.onchange = event => {
            event?.stopPropagation?.();
            const key = input.dataset.rhParam;
            settings.rhParams = settings.rhParams || {};
            const cur = settings.rhParams[key] || {};
            settings.rhParams[key] = {...cur, value:input.value};
            const control = input.closest('.smart-control');
            const valueText = control?.querySelector('.rh-slider-value');
            const pillValue = control?.querySelector('.rh-slider-pill-value');
            if(valueText) valueText.textContent = input.value;
            if(pillValue) pillValue.textContent = input.value;
            persistActiveSmartSettings();
            scheduleSave();
        };
        if(input.dataset.rhType === 'slider'){
            input.onpointerup = () => input.blur();
            input.onmouseleave = () => {
                if(!input.closest('.smart-control')?.matches(':hover')) input.blur();
            };
        }
    });
    dynamicParams.querySelectorAll('[data-rh-pick]').forEach(btn => {
        btn.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            const key = btn.dataset.rhPick;
            const value = btn.dataset.rhValue;
            settings.rhParams = settings.rhParams || {};
            const cur = settings.rhParams[key] || {};
            settings.rhParams[key] = {...cur, value};
            const popover = btn.closest('.smart-popover');
            const control = btn.closest('.smart-control');
            const pillSub = control?.querySelector('.smart-pill .sub');
            if(pillSub) pillSub.textContent = value;
            if(popover){
                popover.querySelectorAll('[data-rh-pick]').forEach(b => {
                    if(b.dataset.rhPick === key) b.classList.toggle('active', b.dataset.rhValue === value);
                });
            }
            closeAllSmartPopovers();
            persistActiveSmartSettings();
            scheduleSave();
        };
    });
    dynamicParams.querySelectorAll('[data-rh-random]').forEach(btn => {
        btn.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            toggleSmartRhRandom(btn.dataset.rhRandom);
        };
    });
}
async function loadConfig(){
    try {
        const cfg = await fetch('/api/config').then(r => r.json());
        apiProviders = Array.isArray(cfg.api_providers) ? cfg.api_providers : [];
        // 加载 model_specs.json（模型能力/参数的权威来源）。失败不阻塞，UI 回退 provider 配置。
        try {
            const spec = await fetch('/api/model-specs').then(r => r.ok ? r.json() : null);
            if(spec && typeof spec === 'object'){ modelSpecs = Object.assign({}, modelSpecs, spec); }
        } catch(_){}
        // 提供商配置已就绪即先渲染参数面板。
        sanitizeSmartApiSelection(settings);
        updateProviderModels();
        lastConfigRefreshAt = Date.now();
        sanitizeSmartApiSelection(settings);
        updateProviderModels();
    } catch(e) {
        toast(tr('smart.toastApiSettingsFail'));
    }
}

// 从 model_specs.json 取某类别的适配模型 ID 列表。
function specModelIds(kind){
    const map = {chat:'chat_model_ids', image:'image_model_ids', video:'video_model_ids'};
    const arr = modelSpecs?.[map[kind]];
    return Array.isArray(arr) ? arr : [];
}
// 取某模型在 spec 中的定义（参数选项/能力）。未适配返回 null。
// 判断是否为通用 OpenAI 兼容 provider（非 codex/jimeng/runninghub/volcengine/gemini-cli 专属）。
// spec 模型是针对这类网关的；provider 专属入口沿用各自的模型来源。
function isGenericOpenAIProvider(provider){
    if(!provider) return false;
    const proto = String(provider.protocol || 'openai').toLowerCase();
    const id = String(provider.id || '').toLowerCase();
    const specific = ['codex','jimeng','runninghub','volcengine','gemini-cli'];
    return !specific.includes(proto) && !specific.includes(id);
}
// 将 spec 的适配模型合并进 provider 模型列表（additive：保留原序，补入缺失的 spec 模型）。
function mergeSpecModels(kind, providerModels, provider){
    const base = [...new Set((providerModels || []).filter(Boolean))];
    if(!isGenericOpenAIProvider(provider)) return base;
    for(const id of specModelIds(kind)){
        if(id && !base.some(m => String(m).toLowerCase() === String(id).toLowerCase())) base.push(id);
    }
    return base;
}
async function refreshSmartConfigFromSettings(){
    await loadConfig();
    renderDynamicParams();
    // Provider changes refresh the available choices only. They must not
    // rewrite the selected node's generation snapshot while a settings panel
    // is open; each node keeps its own runSettings for reproducibility.
    if(composer?.classList?.contains('open')) render();
}
function loadPromptPresets(){
    try {
        const list = JSON.parse(localStorage.getItem(PROMPT_PRESETS_KEY) || '[]');
        promptPresets = Array.isArray(list) ? list.filter(p => p?.id && typeof p.text === 'string') : [];
    } catch(e) {
        promptPresets = [];
    }
}
function savePromptPresets(){
    localStorage.setItem(PROMPT_PRESETS_KEY, JSON.stringify(promptPresets));
}
function defaultPromptTemplateGroups(){
    return [
        {id:'view', name:tr('smart.tplCatView')},
        {id:'storyboard', name:tr('smart.tplCatStoryboard')},
        {id:'character', name:tr('smart.tplCatCharacter')},
        {id:'product', name:tr('smart.tplCatProduct')},
        {id:'lighting', name:tr('smart.tplCatLighting')},
        {id:'mine', name:tr('smart.tplCatMine')}
    ];
}
function loadPromptTemplateGroups(){
    try {
        const list = JSON.parse(localStorage.getItem(PROMPT_TEMPLATE_GROUPS_KEY) || '[]');
        const valid = Array.isArray(list) ? list.filter(g => g?.id && g?.name) : [];
        const defaults = defaultPromptTemplateGroups();
        promptTemplateGroups = defaults.map(group => valid.find(g => g.id === group.id) || group);
        valid.filter(g => !promptTemplateGroups.some(x => x.id === g.id)).forEach(g => promptTemplateGroups.push(g));
    } catch(e) {
        promptTemplateGroups = defaultPromptTemplateGroups();
    }
}
function savePromptTemplateGroups(){
    localStorage.setItem(PROMPT_TEMPLATE_GROUPS_KEY, JSON.stringify(promptTemplateGroups));
}
function loadPromptTemplateOverrides(){
    try {
        const data = JSON.parse(localStorage.getItem(PROMPT_TEMPLATE_OVERRIDES_KEY) || '{}');
        promptTemplateOverrides = {
            hiddenBuiltinIds:Array.isArray(data.hiddenBuiltinIds) ? data.hiddenBuiltinIds : [],
            editedBuiltins:data.editedBuiltins && typeof data.editedBuiltins === 'object' ? data.editedBuiltins : {}
        };
    } catch(e) {
        promptTemplateOverrides = {hiddenBuiltinIds:[], editedBuiltins:{}};
    }
}
function savePromptTemplateOverrides(){
    localStorage.setItem(PROMPT_TEMPLATE_OVERRIDES_KEY, JSON.stringify(promptTemplateOverrides));
}
async function loadPromptTemplates(){
    try {
        const data = await fetch('/api/prompt-libraries').then(r => r.ok ? r.json() : {library:{libraries:[]}});
        promptLibraries = Array.isArray(data.library?.libraries) ? data.library.libraries : [];
        if(!promptLibraries.length) {
            const fallback = await fetch('/api/smart-canvas/prompt-templates').then(r => r.ok ? r.json() : {templates:[]});
            builtinPromptTemplates = Array.isArray(fallback.templates) ? fallback.templates.filter(t => t?.id && t?.positive) : [];
            promptLibraries = [{id:'system', name:tr('smart.systemPromptLibrary'), readonly:true, items:builtinPromptTemplates}];
        } else {
            const system = promptLibraries.find(lib => lib.id === 'system') || promptLibraries[0];
            builtinPromptTemplates = Array.isArray(system?.items) ? system.items.filter(t => t?.id && t?.positive) : [];
        }
        if(!promptLibraries.some(lib => lib.id === activePromptLibraryId)) activePromptLibraryId = promptLibraries[0]?.id || 'system';
        renderPromptLibrarySelect();
    } catch(e) {
        builtinPromptTemplates = [];
        promptLibraries = [];
    }
}
function activePromptLibrary(){
    return promptLibraries.find(lib => lib.id === activePromptLibraryId) || promptLibraries[0] || {id:'system', name:tr('smart.systemPromptLibrary'), readonly:true, items:builtinPromptTemplates};
}
function renderPromptLibrarySelect(){
    if(!promptTemplateLibrarySelect) return;
    promptTemplateLibrarySelect.innerHTML = promptLibraries.map(lib => `<option value="${escapeAttr(lib.id)}" ${lib.id === activePromptLibraryId ? 'selected' : ''}>${escapeHtml(lib.name || tr('smart.promptLibrary'))}</option>`).join('');
}
function promptTemplateItems(){
    const activeLibrary = activePromptLibrary();
    if(activeLibrary.id !== 'system'){
        return (activeLibrary.items || []).filter(t => t?.id && t?.positive).map(t => ({
            ...t,
            sourceId:t.id,
            builtin:false,
            remote:true,
            libraryId:activeLibrary.id
        }));
    }
    // 系统库的条目同样走后端（/api/prompt-libraries），与素材库管理共用一套数据。
    // 这样画布里修改/删除系统提示词会实时同步，不再依赖各端不互通的 localStorage 覆盖。
    // 仍保留 builtin:true 用于“内置”标签与完整提示词（含负向/参数）的展示。
    const source = Array.isArray(activeLibrary.items) && activeLibrary.items.length ? activeLibrary.items : builtinPromptTemplates;
    const builtins = source
        .filter(t => t?.id && t?.positive)
        .map(t => ({...t, sourceId:t.id, builtin:true, remote:true, libraryId:'system'}));
    const mine = promptPresets.map(p => ({
        id:`mine:${p.id}`,
        sourceId:p.id,
        name:p.name || tr('smart.promptPresetUnnamed'),
        // 系统库分组以后端为准（custom=“我的”），本地旧预设归到 custom 分组下展示，避免无对应标签。
        category:(p.category && p.category !== 'mine') ? p.category : 'custom',
        scene:'我的提示词预设',
        positive:p.text || '',
        negative:'',
        params:{},
        builtin:false
    }));
    return [...builtins, ...mine];
}
function promptTemplateText(template, mode='positive'){
    const positive = String(template?.positive || '').trim();
    if(mode === 'positive' || !template?.builtin) return positive;
    const negative = String(template?.negative || '').trim();
    const params = Object.entries(template?.params || {})
        .map(([key, value]) => `${key}: ${value}`)
        .join('\n');
    return [positive, negative ? `Negative prompt:\n${negative}` : '', params ? `Params:\n${params}` : ''].filter(Boolean).join('\n\n');
}
function promptTemplateName(template){
    if(window.StudioI18n?.lang?.() === 'en' && template?.name_en) return template.name_en;
    return template?.name || '';
}
function promptTemplateScene(template){
    if(window.StudioI18n?.lang?.() === 'en' && template?.scene_en) return template.scene_en;
    return template?.scene || '';
}
function promptTemplateSearchText(template){
    return [
        template?.name,
        template?.name_en,
        template?.scene,
        template?.scene_en,
        template?.positive,
        template?.negative
    ].join(' ').toLowerCase();
}
function activePromptTemplateGroups(){
    const lib = activePromptLibrary();
    // 系统库的分组也以后端 categories 为准，与素材库管理共用同一份分组数据（可重命名/删除并同步）。
    const fromLib = Array.isArray(lib?.categories) ? lib.categories.filter(c => c?.id && c?.name) : [];
    if(fromLib.length) return fromLib;
    if(!lib || lib.id === 'system') return promptTemplateGroups;
    return [];
}
function promptTemplateCategoryLabel(category){
    if(category === 'all') return tr('smart.tplAll');
    // 分组名优先以后端 categories 为准（含内置分组重命名），保证两端显示一致。
    const fromGroups = activePromptTemplateGroups().find(g => g.id === category)?.name;
    if(fromGroups) return fromGroups;
    const builtin = {
        view:tr('smart.tplCatView'),
        storyboard:tr('smart.tplCatStoryboard'),
        character:tr('smart.tplCatCharacter'),
        product:tr('smart.tplCatProduct'),
        lighting:tr('smart.tplCatLighting'),
        custom:tr('smart.tplCatMine'),
        mine:tr('smart.tplCatMine')
    };
    return builtin[category] || promptTemplateGroups.find(g => g.id === category)?.name || category;
}
function promptTemplateSelectedItem(){
    return promptTemplateItems().find(item => item.id === promptTemplateSelectedId) || promptTemplateItems()[0] || null;
}
function currentPromptPreset(id){
    return promptPresets.find(p => p.id === id) || null;
}
function defaultPromptPresetName(text){
    return (String(text || '').trim().split(/\r?\n/)[0] || tr('smart.promptPresetDefault')).slice(0, 28);
}
function promptPresetPanelNode(){
    return canvasState.nodes.find(n => n.id === promptPresetPanel?.dataset.nodeId) || null;
}
function setPromptPresetStatus(text='', tone=''){
    if(!promptPresetStatus) return;
    promptPresetStatus.textContent = text;
    promptPresetStatus.classList.toggle('warn', tone === 'warn');
    promptPresetStatus.classList.toggle('ok', tone === 'ok');
}
function resetPromptPresetDeleteState(){
    promptPresetDeleteArmed = false;
    if(promptPresetDelete){
        promptPresetDelete.textContent = tr('common.delete');
        promptPresetDelete.classList.remove('confirm-danger');
    }
}
function createPromptPresetFromNode(node, {openPanel=true, openTemplatePanel=false}={}){
    const text = String(node?.text || '').trim();
    if(!text){ toast(tr('smart.promptPresetEmpty')); return null; }
    const preset = {id:uid('preset'), name:defaultPromptPresetName(text), text, createdAt:Date.now(), updatedAt:Date.now()};
    promptPresets.unshift(preset);
    savePromptPresets();
    if(node) node.promptPresetId = preset.id;
    render();
    scheduleSave();
    if(openPanel) openPromptPresetPanel(node?.id || '', preset.id, {status:tr('smart.promptPresetSavedNew'), tone:'ok'});
    if(openTemplatePanel) {
        promptTemplateCategory = 'mine';
        promptTemplateSelectedId = `mine:${preset.id}`;
        promptTemplateEditing = true;
        openPromptTemplatePanel(node?.id || '', promptTemplateSelectedId);
    }
    return preset;
}
function renderPromptPresetPanel(selectedId='', message=''){
    if(!promptPresetSelect) return;
    resetPromptPresetDeleteState();
    promptPresetSelect.innerHTML = promptPresets.length
        ? promptPresets.map(p => `<option value="${escapeHtml(p.id)}" ${p.id === canvasState.selectedId ? 'selected' : ''}>${escapeHtml(p.name || tr('smart.promptPresetUnnamed'))}</option>`).join('')
        : `<option value="">${escapeHtml(tr('smart.promptPresetNone'))}</option>`;
    const preset = currentPromptPreset(canvasState.selectedId) || promptPresets[0] || null;
    if(preset && promptPresetSelect.value !== preset.id) promptPresetSelect.value = preset.id;
    promptPresetName.value = preset?.name || '';
    promptPresetText.value = preset?.text || '';
    const hasPreset = Boolean(preset);
    const nodeHasText = Boolean(String(promptPresetPanelNode()?.text || '').trim());
    promptPresetApply.disabled = !hasPreset;
    promptPresetDelete.disabled = !hasPreset;
    promptPresetSave.disabled = !hasPreset;
    if(promptPresetNew) promptPresetNew.disabled = !nodeHasText;
    setPromptPresetStatus(message || (hasPreset ? tr('smart.promptPresetPanelHint') : tr('smart.promptPresetPanelEmpty')));
}
function openPromptPresetPanel(nodeId='', presetId='', options={}){
    if(!promptPresetPanel) return;
    promptPresetPanel.dataset.nodeId = nodeId || '';
    const node = canvasState.nodes.find(n => n.id === nodeId);
    const preferred = presetId || node?.promptPresetId || promptPresets[0]?.id || '';
    renderPromptPresetPanel(preferred, options.status || '');
    if(options.tone) setPromptPresetStatus(options.status || '', options.tone);
    const nodeEl = nodeId ? world.querySelector(`.image-node[data-id="${CSS.escape(nodeId)}"]`) : null;
    const rect = nodeEl?.getBoundingClientRect();
    const shellRect = shell.getBoundingClientRect();
    const maxLeft = Math.max(18, shellRect.width - 410);
    const maxTop = Math.max(18, shellRect.height - 330);
    const left = rect ? Math.min(maxLeft, Math.max(18, rect.right - shellRect.left + 12)) : 80;
    const top = rect ? Math.min(maxTop, Math.max(18, rect.top - shellRect.top)) : 80;
    promptPresetPanel.style.left = `${left}px`;
    promptPresetPanel.style.top = `${top}px`;
    promptPresetPanel.classList.add('open');
    refreshIcons();
}
function closePromptPresetPanel(){
    promptPresetPanel?.classList.remove('open');
    resetPromptPresetDeleteState();
}
function promptTemplateScrollSnapshot(){
    if(!promptTemplatePanel) return null;
    return {
        panelTop:promptTemplatePanel.scrollTop || 0,
        tabLeft:promptTemplatePanel.querySelector('.prompt-template-tabs')?.scrollLeft || 0,
        listTop:promptTemplatePanel.querySelector('.prompt-template-list')?.scrollTop || 0,
        detailTop:promptTemplatePanel.querySelector('.prompt-template-preview-content')?.scrollTop || 0
    };
}
function restorePromptTemplateScroll(snapshot){
    if(!snapshot || !promptTemplatePanel) return;
    requestAnimationFrame(() => {
        promptTemplatePanel.scrollTop = snapshot.panelTop || 0;
        const tabs = promptTemplatePanel.querySelector('.prompt-template-tabs');
        const list = promptTemplatePanel.querySelector('.prompt-template-list');
        const detail = promptTemplatePanel.querySelector('.prompt-template-preview-content');
        if(tabs) tabs.scrollLeft = snapshot.tabLeft || 0;
        if(list) list.scrollTop = snapshot.listTop || 0;
        if(detail) detail.scrollTop = snapshot.detailTop || 0;
    });
}
function renderPromptTemplatePanel(options={}){
    if(!promptTemplatePanel || !promptTemplateBody || !promptTemplateCats) return;
    renderPromptLibrarySelect();
    const scrollSnapshot = options.preserveScroll === false ? null : promptTemplateScrollSnapshot();
    const query = String(promptTemplateSearch?.value || '').trim().toLowerCase();
    const allTemplates = promptTemplateItems();
    const activeGroups = activePromptTemplateGroups();
    // 防御：若当前分类筛选不属于当前词库（例如刚切换词库或分类已被删除），回到“全部”，避免列表被过滤为空。
    if(promptTemplateCategory !== 'all' && !activeGroups.some(g => g.id === promptTemplateCategory)) promptTemplateCategory = 'all';
    const categories = [{id:'all', name:tr('smart.tplAll')}, ...activeGroups.map(group => ({...group, name:promptTemplateCategoryLabel(group.id)}))];
    const groupCounts = allTemplates.reduce((map, item) => {
        map[item.category || 'mine'] = (map[item.category || 'mine'] || 0) + 1;
        return map;
    }, {all:allTemplates.length});
    promptTemplateCats.innerHTML = promptTemplateGroupEditMode ? `
        <div class="prompt-template-group-panel">
            <div class="prompt-template-group-title">
                <div>
                    <strong>${escapeHtml(tr('smart.tplGroupManage'))}</strong>
                    <span>${escapeHtml(tr('smart.tplGroupHint'))}</span>
                </div>
                <div class="prompt-template-group-tools">
                    <button type="button" data-template-cat-new><i data-lucide="plus"></i><span>${escapeHtml(tr('smart.tplAdd'))}</span></button>
                    <button type="button" class="primary" data-template-group-edit><i data-lucide="check"></i><span>${escapeHtml(tr('smart.tplDone'))}</span></button>
                </div>
            </div>
            <div class="prompt-template-group-list">
                ${activeGroups.map(group => `
                    <div class="prompt-template-group-row has-delete">
                        <button type="button" class="group-name ${group.id === promptTemplateCategory ? 'active' : ''}" data-template-cat="${escapeHtml(group.id)}">
                            <span>${escapeHtml(promptTemplateCategoryLabel(group.id))}</span>
                            <small>${groupCounts[group.id] || 0}</small>
                        </button>
                        <button type="button" class="group-tool" data-template-cat-edit="${escapeHtml(group.id)}" title="${escapeAttr(tr('smart.tplRename'))}"><i data-lucide="pencil"></i></button>
                        <button type="button" class="group-tool danger" data-template-cat-delete="${escapeHtml(group.id)}" title="${escapeAttr(tr('common.delete'))}"><i data-lucide="trash-2"></i></button>
                    </div>
                `).join('')}
            </div>
        </div>
    ` : `
        <div class="prompt-template-nav">
            <div class="prompt-template-tabs">
                ${categories.map(cat => `
                    <button type="button" class="${cat.id === promptTemplateCategory ? 'active' : ''}" data-template-cat="${escapeHtml(cat.id)}">
                        <span>${escapeHtml(cat.name)}</span>
                        <small>${groupCounts[cat.id] || 0}</small>
                    </button>
                `).join('')}
            </div>
            <button type="button" class="prompt-template-manage-groups" data-template-group-edit><i data-lucide="settings-2"></i><span>${escapeHtml(tr('smart.tplManageGroups'))}</span></button>
        </div>
    `;
    const items = allTemplates.filter(item => {
        if(promptTemplateCategory !== 'all' && item.category !== promptTemplateCategory) return false;
        if(!query) return true;
        return promptTemplateSearchText(item).includes(query);
    });
    if(items.length && !items.some(item => item.id === promptTemplateSelectedId)) promptTemplateSelectedId = items[0].id;
    const selected = items.find(item => item.id === promptTemplateSelectedId) || items[0] || null;
    const selectedPreset = selected?.builtin || selected?.remote
        ? {id:selected.id, name:selected.name || '', text:selected.positive || '', category:selected.category || 'storyboard', builtin:Boolean(selected.builtin)}
        : (selected ? currentPromptPreset(selected.sourceId) : null);
    const target = promptTemplatePanel.dataset.target || 'node';
    const node = canvasState.nodes.find(n => n.id === promptTemplatePanel.dataset.nodeId);
    const activeLibrary = activePromptLibrary();
    // 系统库 readonly=false，其条目也可编辑/删除（经后端持久化），因此只看 readonly。
    const canEditCurrentLibrary = !activeLibrary.readonly;
    const editMode = Boolean(promptTemplateEditing && selectedPreset);
    promptTemplateBody.innerHTML = `
        <div class="prompt-template-list">
            <div class="prompt-template-list-tools">
                <button type="button" data-template-save-current><i data-lucide="bookmark-plus"></i><span>${escapeHtml(tr('smart.tplSaveCurrent'))}</span></button>
                <button type="button" data-template-new><i data-lucide="file-plus-2"></i><span>${escapeHtml(tr('smart.tplNewTemplate'))}</span></button>
            </div>
            ${items.length ? items.map(item => `<button type="button" class="prompt-template-card ${item.id === selected?.id ? 'active' : ''}" data-template-id="${escapeHtml(item.id)}">
                <span class="prompt-template-card-top">
                    <span class="prompt-template-name">${escapeHtml(promptTemplateName(item))}</span>
                    <span class="prompt-template-source">${escapeHtml(item.builtin ? tr('smart.tplBuiltin') : tr('smart.tplMine'))}</span>
                </span>
                <span class="prompt-template-scene">${escapeHtml(promptTemplateScene(item) || item.positive || '')}</span>
                <span class="prompt-template-tag">${escapeHtml(promptTemplateCategoryLabel(item.category || 'mine'))}</span>
            </button>`).join('') : `<div class="prompt-template-list-empty">${escapeHtml(tr('smart.tplNoMatches'))}</div>`}
        </div>
        <div class="prompt-template-detail">
            ${selected ? `
                <div class="prompt-template-detail-head">
                    <div>
                        <strong>${escapeHtml(promptTemplateName(selected) || '')}</strong>
                        <span>${escapeHtml(promptTemplateCategoryLabel(selected.category || ''))} · ${escapeHtml(selected.builtin ? tr('smart.tplBuiltinTemplate') : tr('smart.tplMineTemplate'))}</span>
                    </div>
                    ${editMode ? '' : `
                        <div class="prompt-template-icon-actions">
                            <button type="button" ${!canEditCurrentLibrary ? 'disabled' : ''} data-template-edit title="${escapeAttr(tr('smart.tplEditTemplate'))}"><i data-lucide="pencil"></i><span>${escapeHtml(tr('common.edit'))}</span></button>
                            <button type="button" ${!canEditCurrentLibrary ? 'disabled' : ''} class="danger" data-template-delete title="${escapeAttr(tr('smart.tplDeleteTemplate'))}"><i data-lucide="trash-2"></i><span>${escapeHtml(tr('common.delete'))}</span></button>
                        </div>
                    `}
                </div>
            ${editMode ? `
                <div class="prompt-template-edit-fields">
                    <label>${escapeHtml(tr('smart.tplName'))}</label>
                    <input data-template-edit-name value="${escapeAttr(selectedPreset.name || '')}" placeholder="${escapeAttr(tr('smart.tplName'))}">
                    <label>${escapeHtml(tr('smart.tplGroup'))}</label>
                    <select data-template-edit-category>
                        ${activeGroups.map(group => `<option value="${escapeAttr(group.id)}" ${group.id === (selectedPreset.category || selected?.category || 'mine') ? 'selected' : ''}>${escapeHtml(promptTemplateCategoryLabel(group.id))}</option>`).join('')}
                    </select>
                    <label>${escapeHtml(tr('smart.tplContent'))}</label>
                    <textarea data-template-edit-text placeholder="${escapeAttr(tr('smart.tplContent'))}">${escapeHtml(selectedPreset.text || '')}</textarea>
                </div>
            ` : `
                <div class="prompt-template-preview-content">
                <div class="prompt-template-section">
                    <label>${escapeHtml(tr('smart.tplPositive'))}</label>
                    <p>${escapeHtml(selected?.positive || '')}</p>
                </div>
                ${selected?.negative ? `<div class="prompt-template-section">
                    <label>${escapeHtml(tr('smart.tplNegative'))}</label>
                    <p>${escapeHtml(selected.negative)}</p>
                </div>` : ''}
                ${Object.keys(selected?.params || {}).length ? `<div class="prompt-template-section">
                    <label>${escapeHtml(tr('smart.tplParams'))}</label>
                    <p>${escapeHtml(Object.entries(selected.params).map(([k,v]) => `${k}: ${v}`).join('\n'))}</p>
                </div>` : ''}
                </div>
            `}
            <div class="prompt-template-actions">
                ${editMode ? `
                    <button type="button" data-template-edit-cancel><i data-lucide="x"></i><span>${escapeHtml(tr('common.cancel'))}</span></button>
                    <button type="button" class="danger" data-template-delete><i data-lucide="trash-2"></i><span>${escapeHtml(tr('common.delete'))}</span></button>
                    <button type="button" class="primary" data-template-edit-save><i data-lucide="save"></i><span>${escapeHtml(tr('common.save'))}</span></button>
                ` : `
                    <button type="button" data-template-apply="positive"><i data-lucide="corner-down-left"></i><span>${escapeHtml(tr('smart.tplApplyPositive'))}</span></button>
                    <button type="button" class="primary" data-template-apply="full"><i data-lucide="wand-sparkles"></i><span>${escapeHtml(tr('smart.tplApplyFull'))}</span></button>
                `}
            </div>
            ` : `<div class="prompt-template-empty">${escapeHtml(tr('smart.tplPickOrCreate'))}</div>`}
        </div>
    `;
    refreshIcons();
    restorePromptTemplateScroll(scrollSnapshot);
}
function activePromptTemplateNodeId(){
    return promptTemplatePanel?.classList?.contains('open') && promptTemplatePanel.dataset.target !== 'composer' ? (promptTemplatePanel.dataset.nodeId || '') : '';
}
function syncComposerTemplateButton(){
    if(!composerTemplateBtn || !promptTemplatePanel) return;
    const active = promptTemplatePanel.classList.contains('open') && promptTemplatePanel.dataset.target === 'composer';
    composerTemplateBtn.classList.toggle('active', active);
    composerTemplateBtn.setAttribute('aria-pressed', active ? 'true' : 'false');
}
async function openPromptTemplatePanel(nodeId='', templateId='', options={}){
    if(!promptTemplatePanel) return;
    const target = options.target === 'composer' ? 'composer' : 'node';
    promptTemplatePanel.dataset.target = target;
    promptTemplatePanel.dataset.nodeId = nodeId || '';
    if(promptTemplatePanel.parentElement !== shell) shell.appendChild(promptTemplatePanel);
    if(templateId) promptTemplateSelectedId = templateId;
    promptTemplatePanel.classList.add('open');
    // 每次打开都从后端拉取最新提示词库，确保素材库管理里的新增/修改/删除实时反映到画布（同根同源）。
    try { await loadPromptTemplates(); } catch(e){}
    if(!promptTemplateSelectedId || !promptTemplateItems().some(it => it.id === promptTemplateSelectedId)){
        promptTemplateSelectedId = promptTemplateItems()[0]?.id || '';
    }
    renderPromptTemplatePanel();
    if(target === 'node' && nodeId){
        canvasState.selectedId = nodeId;
        canvasState.selectedIds = [];
        canvasState.selectedImage = {nodeId:'', index:-1};
    }
    render();
    syncComposerTemplateButton();
    promptTemplateSearch?.focus();
}
function closePromptTemplatePanel(){
    promptTemplatePanel?.classList.remove('open');
    syncComposerTemplateButton();
    render();
}
function applyPromptTemplateToNode(mode='positive'){
    const template = promptTemplateItems().find(item => item.id === promptTemplateSelectedId);
    if(!template) return;
    if(promptTemplatePanel?.dataset.target === 'composer'){
        const text = promptTemplateText(template, mode);
        setPromptText(text);
        delete promptInput.dataset.preserveDraftOnce;
        savePromptDraftForCurrent();
        renderInputThumbsRow(selectedNode());
        closePromptTemplatePanel();
        scheduleSave();
        return;
    }
    const node = canvasState.nodes.find(n => n.id === promptTemplatePanel?.dataset.nodeId);
    if(!node) return;
    node.text = promptTemplateText(template, mode);
    node.promptPresetId = template.builtin ? '' : template.sourceId || '';
    closePromptTemplatePanel();
    render();
    scheduleSave();
}
async function saveCurrentPromptAsTemplate(){
    const library = activePromptLibrary();
    // 系统库 readonly=false，也允许新增条目（走后端，与素材库管理同步）。
    if(library.readonly){ toast(tr('smart.selectEditableLibrary')); return; }
    const text = promptTemplatePanel?.dataset.target === 'composer'
        ? promptPlainText()
        : String(canvasState.nodes.find(n => n.id === promptTemplatePanel?.dataset.nodeId)?.text || '').trim();
    if(!text){ toast(tr('smart.promptPresetEmpty')); return; }
    try {
        const data = await fetch('/api/prompt-libraries/items', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({library_id:library.id, name:defaultPromptPresetName(text), category:promptTemplateCategory === 'all' ? 'custom' : promptTemplateCategory, positive:text, scene:'我的提示词预设'})
        }).then(async r => {
            if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.saveFailed'));
            return r.json();
        });
        promptLibraries = data.library?.libraries || promptLibraries;
        activePromptLibraryId = library.id;
        promptTemplateCategory = data.item?.category || 'custom';
        promptTemplateSelectedId = data.item?.id || '';
        promptTemplateEditing = true;
        renderPromptTemplatePanel({preserveScroll:false});
    } catch(err) {
        toast(err.message || tr('smart.saveFailed'));
    }
}
async function createBlankPromptTemplate(){
    const library = activePromptLibrary();
    // 系统库 readonly=false，也允许新建空白条目（走后端，与素材库管理同步）。
    if(library.readonly){ toast(tr('smart.selectEditableLibrary')); return; }
    const category = promptTemplateCategory && promptTemplateCategory !== 'all' ? promptTemplateCategory : 'custom';
    try {
        const data = await fetch('/api/prompt-libraries/items', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({library_id:library.id, name:tr('smart.tplNewTemplateName'), category, positive:'新提示词', scene:'我的提示词预设'})
        }).then(async r => {
            if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.createFailed'));
            return r.json();
        });
        promptLibraries = data.library?.libraries || promptLibraries;
        activePromptLibraryId = library.id;
        promptTemplateCategory = category;
        promptTemplateSelectedId = data.item?.id || '';
        promptTemplateEditing = true;
        renderPromptTemplatePanel({preserveScroll:false});
    } catch(err) {
        toast(err.message || tr('smart.createFailed'));
    }
}
async function savePromptTemplateEdit(){
    const item = promptTemplateSelectedItem();
    if(!item) return;
    const name = promptTemplatePanel.querySelector('[data-template-edit-name]')?.value?.trim() || '';
    const text = promptTemplatePanel.querySelector('[data-template-edit-text]')?.value?.trim() || '';
    const category = promptTemplatePanel.querySelector('[data-template-edit-category]')?.value || 'mine';
    if(!name || !text){ toast(tr('smart.tplRequired')); return; }
    if(item.remote){
        try {
            const data = await fetch(`/api/prompt-libraries/items/${encodeURIComponent(item.id)}`, {
                method:'PATCH',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({library_id:item.libraryId || activePromptLibrary().id, name, category, positive:text, scene:item.scene || '', negative:item.negative || ''})
            }).then(async r => {
                if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.saveFailed'));
                return r.json();
            });
            promptLibraries = data.library?.libraries || promptLibraries;
            promptTemplateSelectedId = data.item?.id || item.id;
        } catch(err) {
            toast(err.message || tr('smart.saveFailed'));
            return;
        }
    } else if(item.builtin){
        promptTemplateOverrides.editedBuiltins = promptTemplateOverrides.editedBuiltins || {};
        promptTemplateOverrides.editedBuiltins[item.id] = {
            ...(promptTemplateOverrides.editedBuiltins[item.id] || {}),
            name,
            positive:text,
            category
        };
        savePromptTemplateOverrides();
    } else {
        const preset = currentPromptPreset(item.sourceId);
        if(!preset) return;
        const idx = promptPresets.findIndex(p => p.id === preset.id);
        if(idx >= 0) promptPresets[idx] = {...promptPresets[idx], name, text, category, updatedAt:Date.now()};
        savePromptPresets();
        canvasState.nodes.forEach(node => { if(node.promptPresetId === preset.id) node.text = text; });
    }
    promptTemplateEditing = false;
    renderPromptTemplatePanel();
    render();
    scheduleSave();
}
async function deletePromptTemplate(){
    const item = promptTemplateSelectedItem();
    if(!item) return;
    if(item.remote){
        try {
            const data = await fetch(`/api/prompt-libraries/items/${encodeURIComponent(item.id)}`, {method:'DELETE'}).then(async r => {
                if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.deleteFailed'));
                return r.json();
            });
            promptLibraries = data.library?.libraries || promptLibraries;
        } catch(err) {
            toast(err.message || tr('smart.deleteFailed'));
            return;
        }
    } else if(item.builtin){
        promptTemplateOverrides.hiddenBuiltinIds = [...new Set([...(promptTemplateOverrides.hiddenBuiltinIds || []), item.id])];
        savePromptTemplateOverrides();
    } else {
        promptPresets = promptPresets.filter(p => p.id !== item.sourceId);
        canvasState.nodes.forEach(node => { if(node.promptPresetId === item.sourceId) node.promptPresetId = ''; });
        savePromptPresets();
    }
    promptTemplateSelectedId = '';
    promptTemplateEditing = false;
    renderPromptTemplatePanel({preserveScroll:false});
    render();
    scheduleSave();
}
async function createPromptTemplateGroup(){
    const name = window.prompt(tr('smart.tplNewGroupPrompt'), tr('smart.tplNewGroupDefault'));
    if(!String(name || '').trim()) return;
    const lib = activePromptLibrary();
    // 系统库（readonly=false）也走后端新增分组，与素材库管理同步。
    if(lib && !lib.readonly){
        try {
            const data = await fetch('/api/prompt-libraries/categories', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body:JSON.stringify({name:String(name).trim().slice(0, 24), library_id:lib.id})
            }).then(async r => { if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.addGroupFailed')); return r.json(); });
            promptLibraries = data.library?.libraries || promptLibraries;
            promptTemplateCategory = data.category?.id || promptTemplateCategory;
            renderPromptTemplatePanel({preserveScroll:false});
        } catch(err){ if(typeof setStatus === 'function') setStatus(err.message || tr('smart.addGroupFailed')); }
        return;
    }
    const group = {id:uid('tpl_group'), name:String(name).trim().slice(0, 24)};
    promptTemplateGroups.push(group);
    savePromptTemplateGroups();
    promptTemplateCategory = group.id;
    renderPromptTemplatePanel({preserveScroll:false});
}
async function renamePromptTemplateGroup(groupId){
    const lib = activePromptLibrary();
    const group = activePromptTemplateGroups().find(g => g.id === groupId);
    if(!group) return;
    const name = window.prompt(tr('smart.tplGroupNamePrompt'), group.name || '');
    if(!String(name || '').trim()) return;
    // 系统库的内置分组也走后端重命名（后端已放开内置分组限制），两端同步。
    if(lib && !lib.readonly){
        try {
            const data = await fetch(`/api/prompt-libraries/categories/${encodeURIComponent(groupId)}`, {
                method:'PATCH', headers:{'Content-Type':'application/json'},
                body:JSON.stringify({name:String(name).trim().slice(0, 24), library_id:lib.id})
            }).then(async r => { if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.renameFailed')); return r.json(); });
            promptLibraries = data.library?.libraries || promptLibraries;
            renderPromptTemplatePanel();
        } catch(err){ if(typeof setStatus === 'function') setStatus(err.message || tr('smart.renameFailed')); }
        return;
    }
    group.name = String(name).trim().slice(0, 24);
    savePromptTemplateGroups();
    renderPromptTemplatePanel();
}
async function deletePromptTemplateGroup(groupId){
    const lib = activePromptLibrary();
    // 系统库的内置分组也走后端删除（后端已放开限制并把孤立条目改挂到剩余分组），两端同步。
    if(lib && !lib.readonly){
        if(!window.confirm(tr('smart.tplDeleteGroupConfirm'))) return;
        try {
            const data = await fetch(`/api/prompt-libraries/categories/${encodeURIComponent(groupId)}`, {method:'DELETE'})
                .then(async r => { if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.deleteFailed')); return r.json(); });
            promptLibraries = data.library?.libraries || promptLibraries;
            if(promptTemplateCategory === groupId) promptTemplateCategory = 'all';
            renderPromptTemplatePanel({preserveScroll:false});
        } catch(err){ if(typeof setStatus === 'function') setStatus(err.message || tr('smart.deleteFailed')); }
        return;
    }
    if(!window.confirm(tr('smart.tplDeleteGroupConfirm'))) return;
    promptTemplateGroups = promptTemplateGroups.filter(g => g.id !== groupId);
    promptPresets = promptPresets.map(p => p.category === groupId ? {...p, category:'mine'} : p);
    Object.entries(promptTemplateOverrides.editedBuiltins || {}).forEach(([id, item]) => {
        if(item?.category === groupId) promptTemplateOverrides.editedBuiltins[id] = {...item, category:'mine'};
    });
    if(promptTemplateCategory === groupId) promptTemplateCategory = 'all';
    savePromptTemplateGroups();
    savePromptPresets();
    savePromptTemplateOverrides();
    renderPromptTemplatePanel({preserveScroll:false});
}
function editPromptPresetForNode(node){
    openPromptTemplatePanel(node?.id || '', node?.promptPresetId ? `mine:${node.promptPresetId}` : '');
}
function assetCategories(type='image'){
    const library = activeAssetLibrary();
    return (library?.categories || assetLibrary.categories || []).filter(cat => (cat.type || 'image') === type);
}
function assetSmartClassKey(entry){
    if(!entry?.dimension || !entry?.tag) return '';
    return `${String(entry.dimension)}::${String(entry.tag)}`;
}
function assetSmartClassOptionId(entry){
    const key = assetSmartClassKey(entry);
    return key ? `${ASSET_SMART_CATEGORY_PREFIX}${key}` : '';
}
function parseAssetSmartClassId(id=''){
    const value = String(id || '');
    if(!value.startsWith(ASSET_SMART_CATEGORY_PREFIX)) return null;
    const raw = value.slice(ASSET_SMART_CATEGORY_PREFIX.length);
    const index = raw.indexOf('::');
    if(index < 0) return null;
    return {dimension:raw.slice(0, index), tag:raw.slice(index + 2)};
}
function assetSmartClassEntries(){
    const groups = new Map();
    assetCategories('image').forEach(cat => {
        (cat.items || []).forEach(item => {
            const flat = Array.isArray(item?.classification?.flat) ? item.classification.flat : [];
            flat.forEach(entry => {
                const key = assetSmartClassKey(entry);
                if(!key) return;
                const prev = groups.get(key) || {
                    id:assetSmartClassOptionId(entry),
                    dimension:String(entry.dimension || ''),
                    label:String(entry.label || entry.dimension || tr('smart.category')),
                    tag:String(entry.tag || ''),
                    count:0
                };
                prev.count += 1;
                groups.set(key, prev);
            });
        });
    });
    return [...groups.values()].sort((a, b) => {
        if(a.label !== b.label) return a.label.localeCompare(b.label, 'zh-CN');
        return b.count - a.count || a.tag.localeCompare(b.tag, 'zh-CN');
    });
}
function itemsForAssetSmartClass(optionId=''){
    const parsed = parseAssetSmartClassId(optionId);
    if(!parsed) return [];
    return assetCategories('image').flatMap(cat => cat.items || []).filter(item => {
        const flat = Array.isArray(item?.classification?.flat) ? item.classification.flat : [];
        return flat.some(entry => String(entry.dimension || '') === parsed.dimension && String(entry.tag || '') === parsed.tag);
    });
}
function assetLibraries(){
    return Array.isArray(assetLibrary.libraries) && assetLibrary.libraries.length ? assetLibrary.libraries : [{id:'default', name:tr('smart.defaultAssetLibrary'), categories:assetLibrary.categories || []}];
}
function projectMediaSourceLibrary(){
    const items = (canvasState.projectMediaLibrary.items || []).map(item => ({
        ...item,
        id:item.asset_id,
        url:item.original_url,
        thumbnail:item.preview_url || item.original_url,
        preview:item.preview_url || '',
        readonly:true,
        source:'project'
    }));
    const categories = [
        {id:'project-all', name:tr('smart.projectMediaAll'), type:'image', items},
        {id:'project-images', name:tr('smart.projectMediaImages'), type:'image', items:items.filter(item => assetMediaKind(item) === 'image')},
        {id:'project-videos', name:tr('smart.projectMediaVideos'), type:'image', items:items.filter(item => assetMediaKind(item) === 'video')},
        {id:'project-audio', name:tr('smart.projectMediaAudio'), type:'image', items:items.filter(item => assetMediaKind(item) === 'audio')},
    ];
    return {id:PROJECT_ASSET_LIBRARY_ID, name:tr('smart.projectMedia'), categories, readonly:true, source:'project'};
}
function localAssetFolderCategories(){
    const result = [];
    const walk = node => {
        if(!node) return;
        const isRoot = (node.id || node.path || '__root__') === '__root__';
        result.push({
            id: node.id || (node.path ? node.path : '__root__'),
            name: node.name || (node.path ? node.path.split('/').pop() : '全部上传'),
            type: 'image',
            items: isRoot ? (localAssetLibrary.items || []) : (node.items || []),
            readonly: true,
            source: 'local',
        });
        (node.children || []).forEach(walk);
    };
    walk(localAssetLibrary.tree || {id:'__root__', name:'全部上传', items:localAssetLibrary.items || [], children:[]});
    return result;
}
function assetLibraryIsLocal(){
    return activeAssetLibraryId === LOCAL_ASSET_LIBRARY_ID;
}
function assetLibraryIsProject(){
    return activeAssetLibraryId === PROJECT_ASSET_LIBRARY_ID;
}
function currentAssetSourceLibraries(){
    return [
        projectMediaSourceLibrary(),
        ...assetLibraries().map(library => ({...library, name:`${tr('smart.globalResources')} · ${library.name || tr('smart.assetLibrary')}`, source:'global'})),
        {id:LOCAL_ASSET_LIBRARY_ID, name:`${tr('smart.globalResources')} · ${tr('smart.localAssets')}`, categories:localAssetFolderCategories(), readonly:true, source:'local'}
    ];
}
function activeAssetLibrary(){
    if(assetLibraryIsProject()) return projectMediaSourceLibrary();
    if(assetLibraryIsLocal()) return currentAssetSourceLibraries().find(lib => lib.id === LOCAL_ASSET_LIBRARY_ID);
    const libs = assetLibraries();
    return libs.find(lib => lib.id === activeAssetLibraryId) || libs[0] || null;
}
function activeAssetCategory(){
    const cats = assetCategories('image');
    if(parseAssetSmartClassId(activeAssetCategoryId)) return null;
    if(!cats.length) return null;
    return cats.find(cat => cat.id === activeAssetCategoryId) || cats[0];
}
function activeAssetTabCategory(){ return activeAssetCategory(); }
function setActiveAssetTabCategory(categoryId=''){ activeAssetCategoryId = categoryId || ''; }
async function loadAssetLibrary(){
    try {
        const projectId = canvas?.project_id || canvas?.project || sourceProjectId || canvasId || '';
        const [data, localData, projectData] = await Promise.all([
            fetch('/api/asset-library').then(r => r.json()),
            fetch('/api/local-assets').then(r => r.ok ? r.json() : {items:[], tree:null}).catch(() => ({items:[], tree:null})),
            projectId ? fetch(`/api/project-library/${encodeURIComponent(projectId)}/media`).then(r => r.ok ? r.json() : {items:[]}).catch(() => ({items:[]})) : Promise.resolve({items:[]})
        ]);
        localAssetLibrary = {items:Array.isArray(localData.items) ? localData.items : [], tree:localData.tree || null};
        canvasState.projectMediaLibrary = {items:Array.isArray(projectData.items) ? projectData.items : []};
        setAssetLibraryFromResponse(data, {render:false});
        if(!assetLibrarySelectionTouched) activeAssetLibraryId = PROJECT_ASSET_LIBRARY_ID;
        renderAssetLibrary();
    } catch(e) {
        toast(tr('smart.assetLoadFail'));
    }
}
function refreshAssetLibrarySoon(delay=120){
    clearTimeout(assetLibraryRefreshTimer);
    assetLibraryRefreshTimer = setTimeout(async () => {
        await loadAssetLibrary();
        if(mentionPicker?.classList?.contains('open') && mentionSource === 'asset') renderMentionPicker('asset');
    }, delay);
}
function handleAssetLibraryUpdatedMessage(data={}){
    const remoteUpdatedAt = Number(data.updated_at || 0);
    if(remoteUpdatedAt && remoteUpdatedAt <= Number(assetLibraryUpdatedAt || 0)) return;
    refreshAssetLibrarySoon();
}
// 多人协作同步：一个稳定的客户端 id，既用于 WS 连接，也随 saveCanvas 上报，
// 服务器广播 canvas_updated 时带回 client_id，自己发的就忽略，避免自我刷新。
const smartClientId = `canvas_smart_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`;
let canvasSyncInFlight = false;
let pendingRemoteUpdatedAt = 0;
let canvasSyncTimer = null;
let canvasMetaPollTimer = null;
function mergeSmartImageLists(localImgs, remoteImgs){
    const merged = new Map();
    const keyFor = (image, fallbackIndex) => {
        const outputId = String(image?.output_id || '').trim();
        if(outputId) return `output:${outputId}`;
        const assetId = String(image?.asset_id || '').trim();
        if(assetId) return `asset:${assetId}`;
        const url = String(image?.url || '').trim();
        return url ? `url:${url}` : `anonymous:${fallbackIndex}`;
    };
    (localImgs || []).forEach((image, index) => {
        merged.set(keyFor(image, `local:${index}`), image);
    });
    (remoteImgs || []).forEach((image, index) => {
        const key = keyFor(image, `remote:${index}`);
        merged.set(key, {...(merged.get(key) || {}), ...image});
    });
    const images = [...merged.values()];
    if(images.every(image => Number.isInteger(Number(image?.output_order)))){
        images.sort((left, right) => Number(left.output_order) - Number(right.output_order));
    }
    return images;
}
function smartGenerationSubmissions(node){
    return Array.isArray(node?.generationSubmissions)
        ? node.generationSubmissions.filter(item => item && item.idempotencyKey)
        : [];
}
function smartNodeInFlight(node){
    if(smartNodeHasCompletedResult(node)) return false;
    // #145: agent-task 节点用 node.status（'queued'/'running'/'succeeded'/'failed'/'uncertain'/'planned'），
    // 不映射到 running/pending 字段，需单独判定。'planned' 未派发不算 in-flight。
    if(isSmartAgentTaskNode(node)) return node.status === 'queued' || node.status === 'running';
    return Boolean(node && (node.running || node.pending || node.queued || node.jimengPending || smartPendingTasks(node).length || smartGenerationSubmissions(node).length));
}
function smartNodeHasDisplayResult(node){
    return Boolean((node?.images || []).some(img => img?.url && !img.loopInputPreview));
}
function smartNodeHasCompletedResult(node){
    if(!smartNodeHasDisplayResult(node)) return false;
    if(node?.runFinishedAt) return true;
    return !node?.jimengPending && !smartPendingTasks(node).length && !smartGenerationSubmissions(node).length && !Number(node?.pending || 0) && !node?.queued;
}
function liveSmartNode(node){
    if(!node?.id) return node;
    return canvasState.nodes.find(n => n.id === node.id) || node;
}
function clearSmartNodeBusyState(node){
    if(!node) return node;
    smartNodeRunTokens.delete(node.id);
    node.running = false;
    node.pending = 0;
    node.queued = false;
    delete node.jimengPending;
    delete node.pendingTasks;
    return node;
}
function markSmartNodeComplete(node, meta=null, revealResult=false){
    if(!node) return node;
    const keepHidden = node.runTimerHidden === true;
    clearSmartNodeBusyState(node);
    node.runFinishedAt = Number(node.runFinishedAt || 0) || nowMs();
    if(!node.runStartedAt) node.runStartedAt = meta?.createdAt || node.runFinishedAt;
    node.runElapsedMs = Math.max(0, Number(node.runFinishedAt || nowMs()) - Number(node.runStartedAt || node.runFinishedAt || nowMs()));
    node.runTimerHidden = meta?.hideTimer === true || keepHidden;
    if(revealResult) requestSmartEdgeReveal(node);
    return node;
}
function completedDownstreamOutputForNode(sourceNode){
    if(!sourceNode?.id) return null;
    const startedAt = Number(sourceNode.runStartedAt || 0);
    return downstreamImageTargetsFor(sourceNode).find(target => {
        if(!smartNodeHasCompletedResult(target)) return false;
        if(target.sourceNodeId && target.sourceNodeId !== sourceNode.id) return false;
        const finishedAt = Number(target.runFinishedAt || 0);
        return !startedAt || !finishedAt || finishedAt >= startedAt;
    }) || null;
}
function clearSourceBusyStateIfDownstreamDone(sourceNode, options={}){
    if(!sourceNode || !smartNodeInFlight(sourceNode)) return false;
    if(sourceNode.jimengPending || smartPendingTasks(sourceNode).length) return false;
    if(!completedDownstreamOutputForNode(sourceNode)) return false;
    clearSmartNodeBusyState(sourceNode);
    if(!sourceNode.runFinishedAt){
        sourceNode.runFinishedAt = nowMs();
        if(!sourceNode.runStartedAt) sourceNode.runStartedAt = sourceNode.runFinishedAt;
        sourceNode.runElapsedMs = Math.max(0, sourceNode.runFinishedAt - Number(sourceNode.runStartedAt || sourceNode.runFinishedAt));
        sourceNode.runTimerHidden = options.hideTimer === true || sourceNode.runTimerHidden === true;
    }
    return true;
}
function clearCompletedSourceBusyStates(){
    let changed = false;
    (canvasState.nodes || []).forEach(node => {
        if(clearSourceBusyStateIfDownstreamDone(node)) changed = true;
    });
    return changed;
}
function hideCompletedRunTimers(){
    let changed = false;
    (canvasState.nodes || []).forEach(node => {
        if(!node || node.type === 'smart-prompt') return;
        if(node.pending || node.running || node.jimengPending || !node.runFinishedAt || node.runTimerHidden) return;
        node.runTimerHidden = true;
        changed = true;
    });
    return changed;
}
function clearCompletedNodeBusyStates(){
    let changed = false;
    (canvasState.nodes || []).forEach(node => {
        if(!node || !smartNodeHasCompletedResult(node) || !smartNodeInFlight(node)) return;
        markSmartNodeComplete(node);
        changed = true;
    });
    if(clearCompletedSourceBusyStates()) changed = true;
    return changed;
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
function completeSmartNodeWithImages(node, images){
    const copy = {...node, images};
    if(smartNodeHasDisplayResult(copy)) markSmartNodeComplete(copy);
    return copy;
}
function smartNodeAllowsOverlappingRun(node){
    if(!node || canvas?.format !== 'firefly-canvas-v1') return false;
    const runSettings = smartSettingsForNode(node);
    return isApiLikeEngine(runSettings.engine) && runSettings.apiKind !== 'video';
}
function syncRunButtonState(node=selectedNode()){
    if(!runBtn) return;
    // 只在“当前选中节点自己”忙时禁用运行：节点正在生成/排队，或它本身是正在跑的循环。
    // 不再因为“画布上有任意循环/级联在跑”就全局禁用——跑循环时仍可对其他节点点生成。
    let missingPrompt = false;
    if(isSmartRunnableNode(node)){
        const request = buildPromptRequestForNode(node, null);
        missingPrompt = !request.prompt.trim() && smartRunNeedsPrompt(settings);
    }
    const flightLocked = smartNodeInFlight(node) && !smartNodeAllowsOverlappingRun(node);
    runBtn.disabled = !isSmartRunnableNode(node) || flightLocked || smartCascadeIsLoopRunning(node?.id) || missingPrompt;
    if(missingPrompt) runBtn.title = tr('smart.toastNeedPrompt');
    else runBtn.removeAttribute('title');
}
function terminalSmartGenerationTaskIds(...logLists){
    const terminal = new Set();
    logLists.flat().forEach(log => {
        if(!log || !['succeeded', 'failed', 'cancelled'].includes(String(log.status || ''))) return;
        const taskId = String(log.task_id || log.request?.task_id || log.request?.taskId || '');
        const idempotencyKey = String(log.idempotency_key || log.idempotencyKey || log.request?.idempotency_key || log.request?.idempotencyKey || '');
        if(taskId) terminal.add(taskId);
        if(idempotencyKey) terminal.add(`key:${idempotencyKey}`);
    });
    return terminal;
}
function mergeSmartPendingTaskLists(local, remote, terminalTaskIds=new Set()){
    const merged = new Map();
    [...smartPendingTasks(local), ...smartPendingTasks(remote)].forEach(task => {
        const taskId = String(task?.taskId || '');
        if(!taskId || terminalTaskIds.has(taskId)) return;
        merged.set(taskId, {...(merged.get(taskId) || {}), ...task, taskId});
    });
    return [...merged.values()];
}
function mergeSmartSubmissionLists(local, remote, terminalTaskIds=new Set()){
    const merged = new Map();
    [...smartGenerationSubmissions(local), ...smartGenerationSubmissions(remote)].forEach(submission => {
        const key = String(submission?.idempotencyKey || '');
        if(key && !terminalTaskIds.has(`key:${key}`)) merged.set(key, {...(merged.get(key) || {}), ...submission, idempotencyKey:key});
    });
    return [...merged.values()];
}
function smartNodeUsesGenerationMerge(node){
    if(!node || typeof node !== 'object') return false;
    return Array.isArray(node.images)
        || Array.isArray(node.pendingTasks)
        || Array.isArray(node.generationSubmissions)
        || Object.prototype.hasOwnProperty.call(node, 'pending')
        || Object.prototype.hasOwnProperty.call(node, 'running')
        || Object.prototype.hasOwnProperty.call(node, 'queued')
        || Object.prototype.hasOwnProperty.call(node, 'jimengPending');
}
function mergeSmartNode(local, remote, terminalTaskIds=new Set()){
    // Remote task broadcasts pass every canvas node through this merge.  Nodes
    // that do not own media/task state must keep their exact persisted shape;
    // otherwise a read-only poll invents images:[] and busy flags that the next
    // authoritative save writes back as unrelated user changes.
    if(!smartNodeUsesGenerationMerge(local) && !smartNodeUsesGenerationMerge(remote)){
        return {...remote};
    }
    const images = mergeSmartImageLists(local.images, remote.images);
    const localDone = smartNodeHasCompletedResult(local);
    const remoteDone = smartNodeHasCompletedResult(remote);
    let base = remote;
    if(localDone && remoteDone){
        base = Number(remote.runFinishedAt || 0) >= Number(local.runFinishedAt || 0) ? remote : local;
    }else if(localDone && !remoteDone){
        base = local;
    }else if(!remoteDone && smartNodeInFlight(local)){
        base = local;
    }
    const merged = {...base, images};
    const pendingTasks = mergeSmartPendingTaskLists(local, remote, terminalTaskIds);
    const submissions = mergeSmartSubmissionLists(local, remote, terminalTaskIds);
    if(pendingTasks.length){
        merged.pendingTasks = pendingTasks;
        merged.pending = Math.max(pendingTasks.length, pendingTasks.reduce((total, task) => total + Math.max(1, Number(task.count || 1)), 0));
        merged.running = Boolean(local.running || remote.running);
        merged.queued = !merged.running && Boolean(local.queued || remote.queued);
        delete merged.runFinishedAt;
    }else{
        delete merged.pendingTasks;
        merged.pending = 0;
        merged.running = false;
        merged.queued = false;
    }
    if(submissions.length){
        merged.generationSubmissions = submissions;
        merged.pending = Math.max(Number(merged.pending || 0), submissions.length);
        delete merged.runFinishedAt;
    }else{
        delete merged.generationSubmissions;
    }
    return smartNodeHasDisplayResult(merged) && !pendingTasks.length && !submissions.length
        ? completeSmartNodeWithImages(merged, images)
        : merged;
}
function mergeSmartNodeLists(localNodes, remoteNodes, terminalTaskIds=new Set()){
    const localById = new Map((localNodes || []).map(n => [n.id, n]));
    const remoteById = new Map((remoteNodes || []).map(n => [n.id, n]));
    const order = [];
    const seen = new Set();
    (localNodes || []).forEach(n => { if(!seen.has(n.id)){ seen.add(n.id); order.push(n.id); } });
    (remoteNodes || []).forEach(n => { if(!seen.has(n.id)){ seen.add(n.id); order.push(n.id); } });
    return order.map(id => {
        const local = localById.get(id);
        const remote = remoteById.get(id);
        if(local && !remote) return local;     // 仅本地存在：保留（我新建的节点；对方删了也宁可复活也不丢结果）
        if(remote && !local) return remote;     // 仅对方存在：加入对方新建的节点
        return mergeSmartNode(local, remote, terminalTaskIds);
    }).filter(Boolean);
}
function applyMergedServerCanvas(serverCanvas){
    if(!serverCanvas || !canvas) return false;
    const remoteNodes = hydrateProjectMediaReferences((Array.isArray(serverCanvas.nodes) ? serverCanvas.nodes : []).map(normalizeLegacySmartNode).filter(Boolean));
    const serverRevision = Number(serverCanvas.revision || 0);
    if(serverRevision > 0){
        remoteNodes.forEach(node => {
            const nodeId = String(node?.id || '');
            if(nodeId) smartGenerationNodeRevisions.set(nodeId, Math.max(serverRevision, Number(smartGenerationNodeRevisions.get(nodeId) || 0)));
        });
    }
    canvas.history = mergeSmartHistoryRecordLists(canvas.history || [], serverCanvas.history || []);
    const terminalTaskIds = terminalSmartGenerationTaskIds(canvas.history || []);
    const mergedNodes = mergeSmartNodeLists(canvasState.nodes, remoteNodes, terminalTaskIds);
    const nodeIds = new Set(mergedNodes.map(n => n.id));
    canvasState.nodes = mergedNodes;
    canvas.nodes = canvasState.nodes;
    canvas.connections = mergeSmartConnections(canvas.connections, serverCanvas.connections, nodeIds);
    let createdRemoteTaskCards = false;
    canvasState.nodes.forEach(node => {
        if(smartPendingTasks(node).length){
            createdRemoteTaskCards = ensureSmartPendingHistoryLogs(node, smartPendingTasks(node)) || createdRemoteTaskCards;
        }
    });
    const cleanedState = clearCompletedNodeBusyStates();
    const recoveredLoopOutputs = recoverStuckLoopOutputsFromHistory();
    canvas.updated_at = Number(serverCanvas.updated_at || canvas.updated_at || 0);
    const serverRev = Number(serverCanvas.revision || 0);
    if(serverRev > 0) canvas.revision = Math.max(serverRev, Number(canvas.revision || 0));
    if(canvas.title !== serverCanvas.title && serverCanvas.title){
        canvas.title = serverCanvas.title;
        setSmartTitleText(canvas.title);
    }
    render();
    if(typeof scheduleConnectionLayerRefresh === 'function') scheduleConnectionLayerRefresh();
    if(recoveredLoopOutputs || createdRemoteTaskCards) scheduleSave();
    if(smartHistoryModal?.classList.contains('open')) scheduleSmartHistoryWindowRender(true);
    resumeSmartPendingTasks();
    resumeJimengPendingNodes();
    return true;
}
async function mergeReloadCanvasNow(){
    if(!canvasId) return;
    if(dragState || selectionState){
        // 用户正在拖拽/框选，稍后再合并，别打断操作
        scheduleCanvasMergeReload(600);
        return;
    }
    try {
        const res = await fetch(`/api/canvases/${encodeURIComponent(canvasId)}`);
        if(!res.ok) return;
        const data = await res.json();
        if(data && data.canvas) applyMergedServerCanvas(data.canvas);
    } catch(e) {}
}

// 导出刷新函数供外部模块调用
window.refreshSmartCanvas = () => mergeReloadCanvasNow();
window.waitForCanvasRefresh = () => mergeReloadCanvasNow();
window.fitBoundsViewport = fitBoundsViewport;

function scheduleCanvasMergeReload(delay=200){
    clearTimeout(canvasSyncTimer);
    canvasSyncTimer = setTimeout(() => { mergeReloadCanvasNow(); }, delay);
}
function handleCanvasUpdatedMessage(data={}){
    if(!data || data.type !== 'canvas_updated') return;
    if(!canvasId || data.canvas_id !== canvasId) return;
    if(data.client_id && data.client_id === smartClientId) return; // 自己发的，忽略
    const remoteUpdatedAt = Number(data.updated_at || 0);
    if(canvasSyncInFlight){
        // 正在保存：记下这次更新的时间戳，保存完成后若确实比本地新再补偿拉取合并
        if(remoteUpdatedAt > pendingRemoteUpdatedAt) pendingRemoteUpdatedAt = remoteUpdatedAt;
        return;
    }
    if(remoteUpdatedAt && remoteUpdatedAt <= Number(canvas?.updated_at || 0)) return;
    scheduleCanvasMergeReload(200);
}
function startCanvasMetaPoll(){
    // WS / iframe 转发不可靠时的兜底：定期看服务器 updated_at 是否变新，变新就合并拉取
    if(canvasMetaPollTimer) return;
    canvasMetaPollTimer = setInterval(async () => {
        if(!canvasId || !canvas) return;
        if(canvasSyncInFlight || dragState || selectionState) return;
        try {
            const res = await fetch(`/api/canvases/${encodeURIComponent(canvasId)}/meta`);
            if(!res.ok) return;
            const meta = await res.json();
            if(Number(meta.updated_at || 0) > Number(canvas.updated_at || 0)) mergeReloadCanvasNow();
        } catch(e) {}
    }, 8000);
}
function connectAssetLibrarySyncSocket(){
    if(window.parent && window.parent !== window) return;
    const host = window.location.host;
    if(!host) return;
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    const clientId = smartClientId;
    let socket;
    let retryTimer = null;
    const connect = () => {
        try {
            const projectQuery = canvasId ? `&project_id=${encodeURIComponent(canvasId)}` : '';
            socket = new WebSocket(`${protocol}://${host}/ws/stats?client_id=${clientId}${projectQuery}`);
        } catch(e) {
            retryTimer = setTimeout(connect, 3000);
            return;
        }
        socket.onmessage = event => {
            try {
                const data = JSON.parse(event.data);
                if(data?.type === 'asset_library_updated') handleAssetLibraryUpdatedMessage(data);
                if(data?.type === 'canvas_updated') handleCanvasUpdatedMessage(data);
            } catch(e) {}
        };
        socket.onclose = () => {
            retryTimer = setTimeout(connect, 3000);
        };
        socket.onerror = () => {
            try { socket.close(); } catch(e) {}
        };
    };
    window.addEventListener('beforeunload', () => {
        clearTimeout(retryTimer);
        try { socket?.close(); } catch(e) {}
    });
    connect();
}
function setAssetLibraryFromResponse(data, options={}){
    assetLibrary = data.library || assetLibrary;
    assetLibraryUpdatedAt = Number(assetLibrary.updated_at || assetLibraryUpdatedAt || 0);
    const libs = assetLibraries();
    if(!activeAssetLibraryId) activeAssetLibraryId = assetLibrary.active_library_id || libs[0]?.id || '';
    if(activeAssetLibraryId && activeAssetLibraryId !== LOCAL_ASSET_LIBRARY_ID && activeAssetLibraryId !== PROJECT_ASSET_LIBRARY_ID && !libs.some(lib => lib.id === activeAssetLibraryId)) activeAssetLibraryId = libs[0]?.id || '';
    const cats = assetCategories('image');
    if(activeAssetCategoryId && !cats.some(cat => cat.id === activeAssetCategoryId)) activeAssetCategoryId = '';
    if(!activeAssetCategoryId) activeAssetCategoryId = activeAssetCategory()?.id || '';
    if(mentionAssetCategoryId && !cats.some(cat => cat.id === mentionAssetCategoryId)) mentionAssetCategoryId = '';
    if(!mentionAssetCategoryId) mentionAssetCategoryId = activeAssetCategoryId;
    if(options.render !== false) {
        renderAssetLibrary();
        if(mentionPicker?.classList?.contains('open') && mentionSource === 'asset') renderMentionPicker('asset');
    }
}
function toggleAssetLibrary(open=!assetLibraryOpen){
    if(!assetPanel || !assetToggle) return;
    if(open && smartHistoryModal?.classList.contains('open')) closeSmartCanvasLog();
    assetLibraryOpen = !!open;
    assetPanel.classList.toggle('open', assetLibraryOpen);
    assetToggle?.classList.toggle('active', assetLibraryOpen);
    if(assetLibraryOpen) loadAssetLibrary();
    render();
}
function assetCategoryForMention(){
    const cats = assetCategories('image');
    if(!cats.length) return null;
    return cats.find(cat => cat.id === mentionAssetCategoryId)
        || cats.find(cat => cat.id === activeAssetCategoryId)
        || cats.find(cat => (cat.items || []).length)
        || cats[0];
}
function assetMediaKind(item){
    if(!item) return 'image';
    if(item.kind === 'video' || item.type === 'video') return 'video';
    if(item.kind === 'audio' || item.type === 'audio') return 'audio';
    const url = String(item.url || item.thumbnail || '').toLowerCase().split('?')[0];
    const name = String(item.name || '').toLowerCase();
    if(/\.(mp4|webm|mov|m4v|avi|mkv)$/.test(url) || /\.(mp4|webm|mov|m4v|avi|mkv)$/.test(name)) return 'video';
    if(/\.(mp3|wav|m4a|aac|ogg|flac)$/.test(url) || /\.(mp3|wav|m4a|aac|ogg|flac)$/.test(name)) return 'audio';
    return 'image';
}
function assetNodeImageFromItem(item, fallbackName='asset'){
    const image = {
        url:item?.url || '',
        name:item?.name || fallbackName,
        kind:item?.kind || assetMediaKind(item)
    };
    copyMediaSizeFields(item, image);
    if(item?.asset_id) image.asset_id = item.asset_id;
    if(item?.preview_url) image.preview_url = item.preview_url;
    if(item?.asset_uris && typeof item.asset_uris === 'object') image.asset_uris = {...item.asset_uris};
    return image;
}
function assetThumbHtml(item){
    const url = escapeAttr(item.url || '');
    const thumb = item.thumbnail || item.thumb || item.preview || item.url || '';
    const kind = assetMediaKind(item);
    if(kind === 'video'){
        return `<div class="asset-thumb-wrap">${smartVideoPreviewHtml(item, 256, 'class="asset-thumb" loading="lazy" decoding="async" alt=""')}<span class="asset-video-badge"><i data-lucide="film"></i>VIDEO</span></div>`;
    }
    if(kind === 'audio'){
        return `<div class="asset-thumb-wrap media-thumb audio-thumb asset-thumb"><i data-lucide="file-audio"></i><span>${escapeHtml(item.name || 'Audio')}</span></div>`;
    }
    // 网格缩略图用较小尺寸 + 懒加载/异步解码：素材多时滚动不再一次性加载解码全部图片。
    return smartPreviewImgHtml({...item, url:thumb}, 256, 'class="asset-thumb" loading="lazy" decoding="async" alt=""');
}
function renderAssetLibrary(){
    if(!assetPanel || !assetGrid || !assetCategorySelect) return;
    const libs = currentAssetSourceLibraries();
    if(!activeAssetLibraryId || !libs.some(lib => lib.id === activeAssetLibraryId)) activeAssetLibraryId = assetLibrary.active_library_id || assetLibraries()[0]?.id || LOCAL_ASSET_LIBRARY_ID;
    if(assetLibrarySelect){
        assetLibrarySelect.innerHTML = libs.map(lib => `<option value="${escapeHtml(lib.id)}" ${lib.id === activeAssetLibraryId ? 'selected' : ''}>${escapeHtml(lib.name || tr('smart.assetLibrary'))}</option>`).join('');
    }
    assetImageControls.style.display = 'block';
    const localMode = assetLibraryIsLocal();
    const projectMode = assetLibraryIsProject();
    assetDropZone.style.display = 'flex';
    assetDropZone.classList.toggle('readonly', projectMode);
    assetDropZone.textContent = projectMode ? tr('smart.projectMediaHint') : tr('smart.globalSaveHint');
    assetGrid.style.display = 'grid';
    const baseCats = assetCategories('image');
    const smartClassCats = !localMode && !projectMode ? assetSmartClassEntries().map(entry => ({
        ...entry,
        id:entry.id,
        name:`${entry.label} / ${entry.tag} (${entry.count})`,
        type:'image',
        smartClass:true,
        items:[]
    })) : [];
    const cats = [...baseCats, ...smartClassCats];
    if(!cats.some(cat => cat.id === activeAssetCategoryId)) activeAssetCategoryId = cats[0]?.id || '';
    assetCategorySelect.innerHTML = cats.map(cat => `<option value="${escapeHtml(cat.id)}" ${cat.id === activeAssetCategoryId ? 'selected' : ''}>${escapeHtml(cat.name || tr('smart.assetFolder'))}</option>`).join('');
    const cat = activeAssetCategory();
    const smartClass = parseAssetSmartClassId(activeAssetCategoryId);
    const items = smartClass ? itemsForAssetSmartClass(activeAssetCategoryId) : (cat?.items || []);
    if(assetAddCategoryBtn) assetAddCategoryBtn.disabled = Boolean(smartClass) || projectMode;
    if(assetRenameCategoryBtn) assetRenameCategoryBtn.disabled = projectMode || !cat || Boolean(smartClass) || (localMode && (cat.id === '__root__' || !cat.id));
    if(assetBatchToggleBtn) assetBatchToggleBtn.hidden = !projectMode || Boolean(smartClass);
    if(assetDetectUnusedBtn) assetDetectUnusedBtn.hidden = !projectMode || Boolean(smartClass);
    if(!projectMode) setAssetBatchMode(false);
    updateAssetBatchBar();
    assetGrid.innerHTML = items.length ? items.map(item => `
        <div class="asset-item" draggable="true" data-asset-id="${escapeHtml(item.id)}" data-url="${escapeHtml(item.url)}" data-name="${escapeHtml(item.name || 'asset')}" data-kind="${escapeHtml(assetMediaKind(item))}">
            ${assetThumbHtml(item)}
            <div class="asset-meta">
                <span class="asset-name" ${localMode ? `data-rename-local-asset="${escapeHtml(item.id)}"` : ''} title="${escapeHtml(item.name || '')}">${escapeHtml(item.name || 'asset')}</span>
                ${projectMode ? `${assetBatchMode ? `<input type="checkbox" class="asset-batch-check" data-batch-asset="${escapeHtml(item.id)}" ${assetBatchSelected.has(item.id) ? 'checked' : ''} aria-label="${escapeHtml(tr('smart.select'))}">` : ''}<span class="asset-owned-badge">${escapeHtml(tr('smart.projectOwned'))}</span><button class="asset-mini-btn" type="button" data-save-project-global="${escapeHtml(item.id)}" title="${escapeHtml(tr('smart.saveToGlobal'))}"><i data-lucide="copy-plus"></i></button><button class="asset-mini-btn" type="button" data-delete-project-asset="${escapeHtml(item.id)}" title="${escapeHtml(tr('common.delete'))}"><i data-lucide="trash-2"></i></button>`
                    : localMode ? `<button class="asset-mini-btn" type="button" data-rename-local-asset="${escapeHtml(item.id)}" title="${escapeHtml(tr('smart.assetRename'))}"><i data-lucide="pencil"></i></button>
                       <button class="asset-mini-btn" type="button" data-delete-local-asset="${escapeHtml(item.id)}" title="${escapeHtml(tr('common.delete'))}"><i data-lucide="trash-2"></i></button>` : `<button class="asset-mini-btn" type="button" data-rename-asset="${escapeHtml(item.id)}" title="${escapeHtml(tr('smart.assetRename'))}"><i data-lucide="pencil"></i></button>
                       <button class="asset-mini-btn" type="button" data-delete-asset="${escapeHtml(item.id)}" title="${escapeHtml(tr('common.delete'))}"><i data-lucide="trash-2"></i></button>`}
            </div>
        </div>
    `).join('') : `<div class="asset-empty">${escapeHtml(projectMode ? tr('smart.projectMediaEmpty') : (localMode ? tr('smart.localAssetEmpty') : (smartClass ? tr('smart.smartClassEmpty') : tr('smart.assetEmpty'))))}</div>`;
    bindAssetItemEvents();
    bindSmartPreviewImageFallbacks(assetGrid);
    refreshIcons();
}
function openAssetNameDialog({title='', value='', placeholder='', cancelValue='', multiline=false }={}){
    if(!assetDialogBackdrop || !assetDialogInput || !assetDialogOk || !assetDialogCancel) return Promise.resolve(cancelValue);
    return new Promise(resolve => {
        assetDialogTitle.textContent = title || tr('smart.assetRename');
        assetDialogInput.value = value || '';
        assetDialogInput.placeholder = placeholder || '';
        assetDialogInput.classList.toggle('is-multiline', Boolean(multiline));
        assetDialogInput.rows = multiline ? 5 : 1;
        assetDialogBackdrop.hidden = false;
        assetDialogBackdrop.classList.add('open');
        assetDialogInput.focus();
        assetDialogInput.select();
        const cleanup = result => {
            assetDialogBackdrop.classList.remove('open');
            assetDialogBackdrop.hidden = true;
            assetDialogOk.onclick = null;
            assetDialogCancel.onclick = null;
            assetDialogInput.onkeydown = null;
            assetDialogBackdrop.onmousedown = null;
            assetDialogInput.classList.remove('is-multiline');
            assetDialogInput.rows = 1;
            resolve(result);
        };
        assetDialogOk.onclick = () => cleanup(assetDialogInput.value.trim());
        assetDialogCancel.onclick = () => cleanup(cancelValue);
        assetDialogInput.onkeydown = event => {
            if(event.key === 'Enter' && !multiline) cleanup(assetDialogInput.value.trim());
            if(event.key === 'Enter' && multiline && (event.ctrlKey || event.metaKey)) cleanup(assetDialogInput.value.trim());
            if(event.key === 'Escape') cleanup(cancelValue);
        };
        assetDialogBackdrop.onmousedown = event => {
            if(event.target === assetDialogBackdrop) cleanup(cancelValue);
        };
    });
}
let assetHoverTimer = 0;
function positionAssetHoverPreview(event){
    if(!assetHoverPreview || assetHoverPreview.hidden || assetHoverPreview.style.display === 'none') return;
    const pad = 14;
    const w = assetHoverPreview.offsetWidth || 260;
    const h = assetHoverPreview.offsetHeight || 300;
    let left = event.clientX - w - 16;
    if(left < pad) left = event.clientX + 16;
    left = Math.max(pad, Math.min(window.innerWidth - w - pad, left));
    const top = Math.max(pad, Math.min(window.innerHeight - h - pad, event.clientY + 12));
    assetHoverPreview.style.left = `${left}px`;
    assetHoverPreview.style.top = `${top}px`;
}
function showAssetHoverPreview(event, item){
    if(!assetHoverPreview || !item?.url) return;
    let media = assetHoverPreview.querySelector('img,video');
    const name = assetHoverPreview.querySelector('.asset-hover-name');
    const kind = assetMediaKind(item);
    if(kind === 'video' && media?.tagName?.toLowerCase() !== 'video'){
        media?.replaceWith(document.createElement('video'));
        media = assetHoverPreview.querySelector('video');
    } else if(kind !== 'video' && media?.tagName?.toLowerCase() !== 'img'){
        media?.replaceWith(document.createElement('img'));
        media = assetHoverPreview.querySelector('img');
    }
    if(kind === 'video'){
        media.muted = true;
        media.loop = true;
        media.playsInline = true;
        media.preload = 'metadata';
        media.controls = false;
        media.disablePictureInPicture = true;
        media.setAttribute('disablepictureinpicture', '');
        media.setAttribute('controlslist', 'nodownload noplaybackrate noremoteplayback');
        media.src = item.url;
        media.play?.().catch(() => {});
    } else {
        // 用预览代理（缩放图）而非原图，悬浮预览更快、不卡。
        media.loading = 'lazy';
        media.decoding = 'async';
        media.src = smartMediaPreviewUrl(item, 768);
        media.alt = 'asset preview';
    }
    name.textContent = item.name || 'asset';
    assetHoverPreview.hidden = false;
    assetHoverPreview.style.display = 'block';
    positionAssetHoverPreview(event);
}
function hideAssetHoverPreview(){
    if(!assetHoverPreview) return;
    assetHoverPreview.style.display = 'none';
    assetHoverPreview.hidden = true;
    const media = assetHoverPreview.querySelector('img,video');
    media?.pause?.();
    media?.removeAttribute('src');
    media?.load?.();
}
function beginAssetInlineRename(assetId){
    const item = (activeAssetCategory()?.items || []).find(x => x.id === assetId);
    const card = [...assetGrid.querySelectorAll('.asset-item')].find(el => el.dataset.assetId === assetId);
    const nameEl = card?.querySelector('.asset-name');
    if(!item || !card || !nameEl || card.querySelector('.asset-rename-input')) return;
    hideAssetHoverPreview();
    const previousName = item.name || 'asset';
    const previousDraggable = card.draggable;
    const input = document.createElement('input');
    input.className = 'asset-rename-input';
    input.type = 'text';
    input.value = previousName;
    input.setAttribute('aria-label', tr('smart.assetRename'));
    card.draggable = false;
    nameEl.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const restore = () => {
        if(input.isConnected) input.replaceWith(nameEl);
        card.draggable = previousDraggable;
    };
    const finish = async save => {
        if(done) return;
        done = true;
        const name = input.value.trim();
        if(!save || !name || name === previousName){
            restore();
            return;
        }
        input.disabled = true;
        try {
            if(assetLibraryIsLocal() || item.file){
                const data = await fetch('/api/local-assets/items', {
                    method:'PATCH',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({path:item.file || item.id, name})
                }).then(async r => {
                    if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.renameFailed'));
                    return r.json();
                });
                localAssetLibrary = {items:Array.isArray(data.items) ? data.items : localAssetLibrary.items, tree:data.tree || localAssetLibrary.tree};
                activeAssetCategoryId = data.item?.folder || activeAssetCategoryId;
                if(data.old_path && data.item?.url){
                    const oldUrl = `/assets/uploads/${String(data.old_path).split('/').map(encodeURIComponent).join('/')}`;
                    canvasState.nodes.forEach(node => (node.images || []).forEach(img => {
                        if(img?.url !== oldUrl) return;
                        img.url = data.item.url;
                        img.name = data.item.name || img.name;
                        copyMediaSizeFields(data.item, img);
                    }));
                    scheduleSave();
                }
                renderAssetLibrary();
                render();
                toast(tr('smart.localAssetRenamedSynced'));
            } else {
                const data = await fetch(`/api/asset-library/items/${encodeURIComponent(assetId)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})}).then(r => r.json());
                setAssetLibraryFromResponse(data);
            }
        } catch(err){
            restore();
            toast(err.message || tr('smart.assetAddFail'));
        }
    };
    input.addEventListener('keydown', event => {
        event.stopPropagation();
        if(event.key === 'Enter'){
            event.preventDefault();
            finish(true);
        } else if(event.key === 'Escape'){
            event.preventDefault();
            finish(false);
        }
    });
    input.addEventListener('pointerdown', event => event.stopPropagation());
    input.addEventListener('mousedown', event => event.stopPropagation());
    input.addEventListener('click', event => event.stopPropagation());
    input.addEventListener('blur', () => finish(true));
}
function bindAssetItemEvents(){
    assetGrid.querySelectorAll('.asset-item').forEach(el => {
        const thumb = el.querySelector('.asset-thumb');
        // 悬浮预览延迟显示：滚动时缩略图会从光标下快速划过、连发 mouseenter，立即加载大图会卡。延迟后只在
        // 光标真正停留时才加载预览，滚动划过不触发。
        thumb?.addEventListener('mouseenter', e => {
            clearTimeout(assetHoverTimer);
            const data = {url:el.dataset.url, name:el.dataset.name, kind:el.dataset.kind};
            const cx = e.clientX, cy = e.clientY;
            assetHoverTimer = setTimeout(() => showAssetHoverPreview({clientX:cx, clientY:cy}, data), 160);
        });
        thumb?.addEventListener('mousemove', e => positionAssetHoverPreview(e));
        thumb?.addEventListener('mouseleave', () => { clearTimeout(assetHoverTimer); hideAssetHoverPreview(); });
        el.addEventListener('dragstart', e => {
            hideAssetHoverPreview();
            e.dataTransfer.effectAllowed = 'copy';
            const item = (activeAssetCategory()?.items || []).find(x => x.id === el.dataset.assetId);
            e.dataTransfer.setData('application/x-smart-asset', JSON.stringify(assetNodeImageFromItem(item || {url:el.dataset.url, name:el.dataset.name, kind:el.dataset.kind})));
            e.dataTransfer.setData('text/plain', el.dataset.url || '');
        });
    });
    // 批量多选（issue #56）：勾选框控制选择，不重渲染整个网格
    assetGrid.querySelectorAll('[data-batch-asset]').forEach(chk => {
        chk.onclick = e => {
            e.preventDefault(); e.stopPropagation();
            if(chk.checked) assetBatchSelected.add(chk.dataset.batchAsset);
            else assetBatchSelected.delete(chk.dataset.batchAsset);
            updateAssetBatchBar();
        };
    });
    assetGrid.querySelectorAll('[data-rename-asset]').forEach(btn => {
        btn.onclick = async e => {
            e.preventDefault(); e.stopPropagation();
            beginAssetInlineRename(btn.dataset.renameAsset);
        };
    });
    assetGrid.querySelectorAll('[data-save-project-global]').forEach(btn => {
        btn.onclick = async e => {
            e.preventDefault(); e.stopPropagation();
            const item = (activeAssetCategory()?.items || []).find(entry => entry.id === btn.dataset.saveProjectGlobal);
            if(!item) return;
            btn.disabled = true;
            try {
                await copyProjectMediaToGlobal(item);
            } catch(err) {
                toast(err.message || tr('smart.assetAddFail'));
            } finally {
                btn.disabled = false;
            }
        };
    });
    assetGrid.querySelectorAll('[data-rename-local-asset]').forEach(btn => {
        btn.onclick = async e => {
            e.preventDefault(); e.stopPropagation();
            beginAssetInlineRename(btn.dataset.renameLocalAsset || '');
        };
    });
    assetGrid.querySelectorAll('[data-delete-local-asset]').forEach(btn => {
        btn.onclick = async e => {
            e.preventDefault(); e.stopPropagation();
            btn.disabled = true;
            try {
                await deleteLocalAssetFromPanel(btn.dataset.deleteLocalAsset || '');
            } finally {
                btn.disabled = false;
            }
        };
    });
    assetGrid.querySelectorAll('[data-delete-asset]').forEach(btn => {
        btn.onclick = async e => {
            e.preventDefault(); e.stopPropagation();
            // #66: 全局库单删=硬删，先确认（声明不可恢复）。
            if(!confirm(tr('smart.assetDeleteGlobalConfirm'))) return;
            btn.disabled = true;
            try {
                const data = await fetch(`/api/asset-library/items/${encodeURIComponent(btn.dataset.deleteAsset)}`, {method:'DELETE'}).then(r => r.json());
                setAssetLibraryFromResponse(data);
            } catch(err){
                toast(err.message || tr('smart.assetDeleteFail'));
            } finally {
                btn.disabled = false;
            }
        };
    });
    assetGrid.querySelectorAll('[data-delete-project-asset]').forEach(btn => {
        btn.onclick = async e => {
            e.preventDefault(); e.stopPropagation();
            const projectId = canvas?.project_id || canvas?.project || sourceProjectId || canvasId || '';
            const assetId = btn.dataset.deleteProjectAsset;
            if(!projectId || !assetId) return;
            btn.disabled = true;
            try {
                // 先检查引用
                let references = [];
                let refCheckFailed = false;
                try {
                    const refRes = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(assetId)}/references`);
                    const refData = await refRes.json();
                    references = refData.references || [];
                } catch(refErr) {
                    refCheckFailed = true;
                    references = [];
                }
                
                if(references.length > 0 || refCheckFailed){
                    // 有引用或检查失败，显示警告对话框
                    const confirmed = await showAssetDeleteWarningDialog(assetId, references, refCheckFailed);
                    if(confirmed === 'wide'){
                        // #70 (D4): 项目范围删原图入口——删原图+全部引用（后端 /original/delete-project-wide）
                        await deleteProjectOriginalWide(assetId, btn);
                        return;
                    }
                    if(!confirmed){
                        btn.disabled = false;
                        return;
                    }
                } else {
                    // #66: 无引用时硬删同样需要确认（声明不可恢复）
                    if(!confirm(tr('smart.assetDeleteNoRefConfirm'))){
                        btn.disabled = false;
                        return;
                    }
                }
                
                // 执行删除（携带画布版本：服务端清理引用会 bump revision，冲突时返回 409 + 最新画布）
                // #76 (C-3)：统一走 projectCanvasRequest——409 自动 rebase 到服务端最新画布
                const delResult = await projectCanvasRequest(`/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(assetId)}`, {
                    method: 'DELETE',
                    payload: () => JSON.stringify({base_revision: Number(canvas?.revision || 0)})
                });
                if(!delResult.ok && delResult.status !== 404){
                    // 409：helper 已 rebase 到最新画布，提示冲突即可
                    if(delResult.canvasSynced){
                        btn.disabled = false;
                        toast(delResult.message || tr('smart.deleteConflict'));
                        return;
                    }
                    throw new Error(delResult.message || tr('smart.deleteFailed'));
                }
                // 200：服务端已清理项目内引用，用返回的最新画布同步前端节点；404：已删除，静默刷新
                if(delResult.data?.canvas) await adoptServerCanvas(delResult.data.canvas);
                removeProjectMediaFromCache(assetId);
                // #62 B-5(L1): 硬删成功后剔除引用该媒体的 undo/redo 快照，防止删除的资产在下次保存时复活
                if(stripDeletedMediaFromUndoStacks(assetId)) toast(tr('smart.toastDanglingMediaDropped'));
                if(delResult.status === 404) toast(tr('smart.assetDeleted'));
                if(assetLibraryIsProject()) renderAssetLibrary();
            } catch(err){
                btn.disabled = false;
                toast(err.message || tr('smart.deleteFailed'));
            }
        };
    });
}

// ─── 资源库批量清理 + 回收区入口 + 未使用检测（issue #56）────────────

function smartProjectId(){
    return canvas?.project_id || canvas?.project || sourceProjectId || canvasId || '';
}

// C-3（#76）：「服务端画布 → 本地」统一水合：hydrate + 活动任务日志合并 + 节点 normalize +
// materialize + 刷新 persistedCoreSnapshot/persistedDurableSnapshot + render。
// 项目画布变更端点的 409 冲突恢复与成功响应统一走这里（此前 deleteSmartProjectOriginal
// 一处两份、其他路径各写各的轻量版，已漂移）。
async function adoptServerCanvas(serverCanvasData, {activeTaskLogs = null, settleMs = 0} = {}){
    if(!serverCanvasData) return Number(canvas?.revision || 0);
    const serverCanvas = hydrateProjectMediaReferences(serverCanvasData);
    // #144: 不再整体替换本地画布，而是把本地未保存的 mutation rebase 到服务端
    // 画布上。否则在 autosave 防抖窗口内画的连线/节点（本地独有、尚未落库）
    // 会被历史删除等返回的服务端画布静默丢弃（数据丢失）。
    let nextCanvas = serverCanvas;
    let nextBaseline = null;
    const adoptMutationClient = window.FireflyProjectCanvasMutations;
    if(canvas && persistedCoreSnapshot && adoptMutationClient?.rebaseProjectCanvasResponse){
        try {
            const localStorageCanvas = await materializeProjectMediaReferences(canvasForStorage());
            const rebased = adoptMutationClient.rebaseProjectCanvasResponse(serverCanvas, persistedCoreSnapshot, localStorageCanvas);
            if(!rebased.ignored && rebased.canvas){
                nextCanvas = rebased.canvas;
                nextBaseline = rebased.baseline;
            }
        } catch(_){ /* rebase 异常回退整体替换，保证不阻断同步 */ }
    }
    canvas = nextCanvas;
    canvas.history = activeTaskLogs !== null
        ? mergeSmartActiveHistoryTaskLogs(canvas.history, activeTaskLogs)
        : (canvas.history || []);
    canvas.connections = Array.isArray(canvas.connections) ? canvas.connections : [];
    canvasState.nodes = (canvas.nodes || []).map(hydrateProjectMediaReferences).map(normalizeLegacySmartNode).filter(Boolean);
    canvas.nodes = canvasState.nodes;
    rebuildMarkedAssetIdsFromNodes();
    updateSmartMarkedBadge(); // 刷新后角标立即可见（badge 不再等面板打开才同步）
    canvas.updated_at = Number(canvas.updated_at || Date.now());
    canvas.revision = Number(canvas.revision ?? 0);
    const savedCanvas = await materializeProjectMediaReferences(canvasForStorage());
    persistedCoreSnapshot = nextBaseline || projectCanvasSnapshot(savedCanvas);
    persistedDurableSnapshot = durableSnapshotForCanvas(savedCanvas);
    render();
    if(settleMs > 0) await new Promise(resolve => setTimeout(resolve, settleMs));
    return canvas.revision;
}

// C-3（#76）：项目画布变更端点统一请求 helper。
// 409 + detail.canvas → 先 adoptServerCanvas rebase 到服务端最新画布（canvasSynced=true，
// 不计为成功），调用方按自身语义继续；payload 可为函数（重试时重算 base_revision）。
async function projectCanvasRequest(url, {method = 'POST', payload, retries = 0, settleMs = 0} = {}){
    let attempt = 0;
    for(;;){
        const res = await fetch(url, {
            method,
            headers:{'Content-Type':'application/json'},
            body: typeof payload === 'function' ? payload() : (payload === undefined ? undefined : JSON.stringify(payload)),
        });
        const data = await res.json().catch(() => ({}));
        if(res.status === 409 && data?.detail?.canvas){
            await adoptServerCanvas(data.detail.canvas, {settleMs});
            if(attempt < retries){ attempt++; continue; }
            return {ok:false, status:409, canvasSynced:true, message: typeof data?.detail?.message === 'string' ? data.detail.message : '', data};
        }
        if(!res.ok){
            const detail = data?.detail;
            const message = (detail && typeof detail === 'object')
                ? (typeof detail.message === 'string' ? detail.message : '')
                : (typeof detail === 'string' ? detail : '');
            return {ok:false, status:res.status, canvasSynced:false, message, data};
        }
        return {ok:true, status:res.status, canvasSynced:false, message:'', data};
    }
}

// ── 片②：全局资源库多选批量删除 ───────────────────────────────
const assetBatchToggleBtn = document.getElementById('assetBatchToggleBtn');
const assetBatchBar = document.getElementById('assetBatchBar');
const assetBatchSelectAll = document.getElementById('assetBatchSelectAll');
const assetBatchCount = document.getElementById('assetBatchCount');
const assetBatchDeleteBtn = document.getElementById('assetBatchDeleteBtn');
const assetBatchCloseBtn = document.getElementById('assetBatchCloseBtn');
let assetBatchMode = false;
let assetBatchBusy = false;
const assetBatchSelected = new Set();

function currentBatchAssetIds(){
    if(!assetGrid) return [];
    return [...assetGrid.querySelectorAll('[data-batch-asset]')].map(el => el.dataset.batchAsset);
}
function updateAssetBatchBar(){
    if(!assetBatchBar) return;
    assetBatchBar.hidden = !assetBatchMode;
    if(assetBatchToggleBtn) assetBatchToggleBtn.classList.toggle('active', assetBatchMode);
    const ids = currentBatchAssetIds();
    if(assetBatchSelectAll) assetBatchSelectAll.checked = ids.length > 0 && ids.every(id => assetBatchSelected.has(id));
    if(assetBatchCount) assetBatchCount.textContent = assetBatchSelected.size > 0
        ? tr('smart.batchSelected').replace('{n}', String(assetBatchSelected.size))
        : tr('smart.batchSelectHint');
    if(assetBatchDeleteBtn) assetBatchDeleteBtn.disabled = assetBatchSelected.size === 0 || assetBatchBusy;
}
function setAssetBatchMode(on){
    if(on === assetBatchMode) return;
    assetBatchMode = on;
    if(!on) assetBatchSelected.clear();
    updateAssetBatchBar();
    if(assetGrid) renderAssetLibrary();
}
if(assetBatchToggleBtn) assetBatchToggleBtn.onclick = () => setAssetBatchMode(!assetBatchMode);
if(assetBatchCloseBtn) assetBatchCloseBtn.onclick = () => setAssetBatchMode(false);
if(assetBatchSelectAll) assetBatchSelectAll.onchange = () => {
    currentBatchAssetIds().forEach(id => {
        if(assetBatchSelectAll.checked) assetBatchSelected.add(id);
        else assetBatchSelected.delete(id);
    });
    updateAssetBatchBar();
    renderAssetLibrary();
};

// 批量删除：逐个走 ADR 0015 删除流（硬删 + 引用清理 + 墓碑），
// 删除前汇总引用节点数提示；每次删除用最新画布版本避免 409 连锁
async function smartAssetBatchDelete(){
    if(assetBatchBusy || !assetBatchMode) return;
    const projectId = smartProjectId();
    const ids = [...assetBatchSelected];
    if(!projectId || !ids.length) return;
    assetBatchBusy = true;
    updateAssetBatchBar();
    // 1) 汇总引用
    let totalRefs = 0;
    let refCheckFailed = false;
    for(const assetId of ids){
        try {
            const refRes = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(assetId)}/references`);
            const refData = await refRes.json().catch(() => ({}));
            totalRefs += (refData.references || []).length;
        } catch(_){ refCheckFailed = true; }
    }
    const message = (totalRefs > 0 || refCheckFailed)
        ? tr('smart.batchDeleteRefsConfirm').replace('{n}', String(ids.length)).replace('{r}', String(totalRefs))
        : tr('smart.batchDeleteConfirm').replace('{n}', String(ids.length));
    if(!confirm(message)){
        assetBatchBusy = false;
        updateAssetBatchBar();
        return;
    }
    // 2) 逐个删除（携带画布版本：服务端清理引用会 bump revision，冲突时用返回画布继续）
    let okCount = 0;
    let failCount = 0;
    for(const assetId of ids){
        try {
            // #76 (C-3)：统一 helper——409 rebase 到最新画布后按新版本继续，不计成功
            const delResult = await projectCanvasRequest(`/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(assetId)}`, {
                method: 'DELETE',
                payload: () => JSON.stringify({base_revision: Number(canvas?.revision || 0)})
            });
            if(delResult.ok && delResult.data?.canvas) await adoptServerCanvas(delResult.data.canvas);
            // #59: 409 = 画布版本冲突，删除未执行，绝不能计成功，也不能从本地缓存移除资产
            // （原「已删除 n 个」的 toast 会被假成功污染，资源下次渲染复现）
            const deleted = delResult.ok || delResult.status === 404;
            if(deleted){
                okCount += 1;
                assetBatchSelected.delete(assetId);
                removeProjectMediaFromCache(assetId);
                // #62 B-5(L1): 硬删成功后剔除引用该媒体的 undo/redo 快照，防止删除的资产在下次保存时复活
                if(stripDeletedMediaFromUndoStacks(assetId)) toast(tr('smart.toastDanglingMediaDropped'));
            } else {
                failCount += 1;
            }
        } catch(_){ failCount += 1; }
        updateAssetBatchBar();
    }
    // 3) 刷新 + 汇总提示
    if(assetLibraryIsProject()) renderAssetLibrary();
    renderSmartCanvasHistory();
    refreshSmartProjectMediaTrashButton();
    if(assetBatchSelected.size === 0) setAssetBatchMode(false);
    assetBatchBusy = false;
    updateAssetBatchBar();
    if(failCount > 0) toast(tr('smart.batchDeletePartial').replace('{ok}', String(okCount)).replace('{fail}', String(failCount)));
    else toast(tr('smart.batchDeleteDone').replace('{n}', String(okCount)));
}
if(assetBatchDeleteBtn) assetBatchDeleteBtn.onclick = () => void smartAssetBatchDelete();

// ── 片①：回收区面板（列表 / 单个恢复 / 单个彻底删除）─────────────────
const smartTrashBackdrop = document.getElementById('smartTrashBackdrop');
const smartTrashList = document.getElementById('smartTrashList');
const smartTrashOpenBtn = document.getElementById('smartTrashOpenBtn');
const smartTrashPurgeBtn = document.getElementById('smartTrashPurgeBtn');
const smartTrashCloseBtn = document.getElementById('smartTrashCloseBtn');
if(smartTrashCloseBtn) smartTrashCloseBtn.onclick = closeSmartTrashPanel;
if(smartTrashBackdrop) smartTrashBackdrop.addEventListener('mousedown', e => { if(e.target === smartTrashBackdrop) closeSmartTrashPanel(); });
if(smartTrashPurgeBtn) smartTrashPurgeBtn.onclick = () => {
    // #73: 不再 400ms 盲等重开（面板会显示旧列表）——purgeSmartProjectMediaTrash
    // 成功后自行 await 重开。
    void purgeSmartProjectMediaTrash();
};

async function openSmartTrashPanel(){
    if(!canvas || canvas.format !== 'firefly-canvas-v1') return;
    const projectId = smartProjectId();
    if(!smartTrashBackdrop || !projectId) return;
    smartTrashBackdrop.hidden = false;
    smartTrashBackdrop.classList.add('open');
    if(smartTrashList) smartTrashList.innerHTML = `<div class="smart-trash-empty">${escapeHtml(tr('smart.trashLoading'))}</div>`;
    try {
        const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/trash/media`);
        if(!response.ok) throw new Error('trash status unavailable');
        const data = await response.json().catch(() => ({}));
        renderSmartTrashItems(data.items || []);
    } catch(_){
        renderSmartTrashItems([]);
        toast(tr('smart.trashLoadFailed'));
    }
}
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
// #86：回收区大图预览（缩略图点击），带「恢复」快捷按钮。
async function smartTrashRestoreItem(assetId, deletionId){
    const projectId = smartProjectId();
    if(!assetId || !projectId || smartTrashRestoring) return;
    smartTrashRestoring = true;
    try {
        const restoreUrl = deletionId
            ? `/api/project-library/${encodeURIComponent(projectId)}/original/restore`
            : `/api/project-library/${encodeURIComponent(projectId)}/trash/media/restore`;
        // #76 (C-3)：统一 helper——409 自动 rebase 到最新画布（不计成功）
        const res = await projectCanvasRequest(restoreUrl, {
            method: 'POST',
            payload: () => JSON.stringify({
                asset_id: assetId,
                base_revision: Number(canvas?.revision || 0),
                ...(deletionId ? {deletion_id: deletionId} : {})
            })
        });
        // #61: 409 = 画布版本冲突，未恢复成功，不再误报「原图已恢复」
        if(!res.ok){
            const msg = res.status === 409
                ? (res.message || tr('smart.projectConflict'))
                : (res.message || tr('smart.trashRestoreFailed'));
            if(res.status !== 409) throw new Error(msg);
            toast(msg);
            renderSmartCanvasHistory();
            refreshSmartProjectMediaTrashButton();
            await openSmartTrashPanel();
            return;
        }
        if(res.data?.canvas) await adoptServerCanvas(res.data.canvas);
        // #151: 明确恢复去向——有 deletion_id 还原到画布+历史，无则只回资源库
        toast(deletionId ? tr('canvas.originalRestored') : tr('smart.trashRestoredToLibrary'));
        renderSmartCanvasHistory();
        refreshSmartProjectMediaTrashButton();
        await openSmartTrashPanel();
    } catch(err){
        toast(err?.message || tr('smart.trashRestoreFailed'));
    } finally {
        smartTrashRestoring = false;
    }
}
let smartTrashRestoring = false;
// 单个彻底删除：仅清回收区条目，不影响画布（资源早已从画布清除）
let smartTrashPurging = false;
async function smartTrashPurgeItem(assetId){
    const projectId = smartProjectId();
    if(!assetId || !projectId || smartTrashPurging) return;
    if(!confirm(tr('smart.trashForeverConfirm'))) return;
    smartTrashPurging = true;
    try {
        const res = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/trash/media?asset_id=${encodeURIComponent(assetId)}`, {method: 'DELETE'});
        if(!res.ok) throw new Error(tr('smart.trashPurgeFailed'));
        // #73: 条目被彻底删除后，对应的撤销令牌已失效——清掉匹配的 projectOriginalDelete
        // 令牌，避免「撤销」尝试恢复已清除的资产而报误导错误（此前令牌留在栈里）。
        removeProjectOriginalUndoTokens(assetId, '', projectId);
        toast(tr('smart.trashPurged'));
        refreshSmartProjectMediaTrashButton();
        await openSmartTrashPanel();
    } catch(err){
        toast(err?.message || tr('smart.trashPurgeFailed'));
    } finally {
        smartTrashPurging = false;
    }
}

// ── 片③：未使用媒体检测与一键清理 ─────────────────────────
const smartUnusedBackdrop = document.getElementById('smartUnusedBackdrop');
const smartUnusedList = document.getElementById('smartUnusedList');
const smartUnusedCleanBtn = document.getElementById('smartUnusedCleanBtn');
const smartUnusedCloseBtn = document.getElementById('smartUnusedCloseBtn');
const assetDetectUnusedBtn = document.getElementById('assetDetectUnusedBtn');
let smartUnusedBusy = false;
if(smartUnusedCloseBtn) smartUnusedCloseBtn.onclick = closeSmartUnusedPanel;
if(smartUnusedBackdrop) smartUnusedBackdrop.addEventListener('mousedown', e => { if(e.target === smartUnusedBackdrop) closeSmartUnusedPanel(); });
if(assetDetectUnusedBtn) assetDetectUnusedBtn.onclick = () => void openSmartUnusedPanel();

function formatAssetSize(size){
    const value = Number(size || 0);
    if(!value) return '';
    if(value > 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
    if(value > 1024) return `${(value / 1024).toFixed(0)} KB`;
    return `${value} B`;
}
async function openSmartUnusedPanel(){
    if(!canvas || canvas.format !== 'firefly-canvas-v1') return;
    const projectId = smartProjectId();
    if(!smartUnusedBackdrop || !projectId || smartUnusedBusy) return;
    smartUnusedBusy = true;
    smartUnusedBackdrop.hidden = false;
    smartUnusedBackdrop.classList.add('open');
    if(smartUnusedList) smartUnusedList.innerHTML = `<div class="smart-trash-empty">${escapeHtml(tr('smart.unusedDetecting'))}</div>`;
    if(smartUnusedCleanBtn) smartUnusedCleanBtn.disabled = true;
    let items = [];
    try {
        const res = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/media/unused`);
        if(!res.ok) throw new Error('unused status unavailable');
        const data = await res.json().catch(() => ({}));
        items = data.items || [];
    } catch(_){
        items = [];
        toast(tr('smart.unusedDetectFailed'));
    }
    if(!Array.isArray(items) || !items.length){
        if(smartUnusedList) smartUnusedList.innerHTML = `<div class="smart-trash-empty">${escapeHtml(tr('smart.unusedEmpty'))}</div>`;
    } else {
        if(smartUnusedList) smartUnusedList.innerHTML = items.map(item => {
            const assetId = String(item?.asset_id || '');
            const name = String(item?.name || assetId);
            const isVideo = String(item?.kind || '') === 'video' || String(item?.mime_type || '').startsWith('video/');
            return `<div class="smart-trash-item" data-unused-asset="${escapeHtml(assetId)}">
                <i data-lucide="${isVideo ? 'video' : 'image'}" class="smart-trash-icon"></i>
                <div class="smart-trash-meta">
                    <div class="smart-trash-name" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
                    <div class="smart-trash-sub">${escapeHtml(formatAssetSize(item?.size))}</div>
                </div>
            </div>`;
        }).join('');
        refreshIcons();
        if(smartUnusedCleanBtn) smartUnusedCleanBtn.disabled = false;
    }
    smartUnusedBusy = false;
}
async function smartUnusedCleanAll(){
    if(smartUnusedBusy) return;
    const projectId = smartProjectId();
    const entries = smartUnusedList ? [...smartUnusedList.querySelectorAll('[data-unused-asset]')].map(el => el.dataset.unusedAsset).filter(Boolean) : [];
    if(!projectId || !entries.length) return;
    if(!confirm(tr('smart.unusedCleanConfirm').replace('{n}', String(entries.length)))) return;
    smartUnusedBusy = true;
    if(smartUnusedCleanBtn) smartUnusedCleanBtn.disabled = true;
    let okCount = 0;
    let failCount = 0;
    for(const assetId of entries){
        try {
            // #76 (C-3)：统一 helper——409 rebase 到最新画布后按新版本继续，不计成功
            const delResult = await projectCanvasRequest(`/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(assetId)}`, {
                method: 'DELETE',
                payload: () => JSON.stringify({base_revision: Number(canvas?.revision || 0)})
            });
            if(delResult.ok && delResult.data?.canvas) await adoptServerCanvas(delResult.data.canvas);
            // #60: 同 #59——409 是删除未执行，不计成功，不移除本地缓存。
            const deleted = delResult.ok || delResult.status === 404;
            if(deleted){
                okCount += 1;
                removeProjectMediaFromCache(assetId);
                // #62 B-5(L1): 同批量删除——剔除快照。
                if(stripDeletedMediaFromUndoStacks(assetId)) toast(tr('smart.toastDanglingMediaDropped'));
            } else {
                failCount += 1;
            }
        } catch(_){ failCount += 1; }
    }
    if(assetLibraryIsProject()) renderAssetLibrary();
    // #60: 删除会影响历史引用的媒体时，同步刷新历史列表与回收区入口按钮（此前不刷新，
    // 历史卡片/按钮状态滞后到下次手动打开面板）。
    renderSmartCanvasHistory();
    refreshSmartProjectMediaTrashButton();
    if(failCount > 0) toast(tr('smart.unusedCleanPartial').replace('{ok}', String(okCount)).replace('{fail}', String(failCount)));
    else toast(tr('smart.unusedCleanDone').replace('{n}', String(okCount)));
    smartUnusedBusy = false;
    await openSmartUnusedPanel();
}
if(smartUnusedCleanBtn) smartUnusedCleanBtn.onclick = () => void smartUnusedCleanAll();

function showAssetDeleteWarningDialog(assetId, references, checkFailed = false){
    return new Promise(resolve => {
        const backdrop = document.createElement('div');
        backdrop.className = 'asset-delete-warning-backdrop';
        const title = checkFailed ? tr('smart.deleteRefCheckFailedTitle') : tr('smart.deleteReferredTitle').replace('{n}', String(references.length));
        const desc = checkFailed ? tr('smart.deleteRefCheckFailedDesc') : tr('smart.deleteReferredDesc');
        backdrop.innerHTML = `
            <div class="asset-delete-warning-dialog">
                <h3>${title}</h3>
                <p>${desc}</p>
                <p>${tr('smart.deleteIrrecoverable')}</p>
                ${!checkFailed && references.length > 0 ? `
                <div class="asset-delete-warning-list">
                    ${references.map(ref => `
                        <div class="asset-delete-warning-item">
                            <span class="asset-delete-warning-label">${escapeHtml(ref.node_title || ref.node_id)}</span>
                            <button class="asset-delete-warning-jump" data-node-id="${escapeHtml(ref.node_id)}">${tr('smart.deleteJump')}</button>
                        </div>
                    `).join('')}
                </div>` : ''}
                <div class="asset-delete-warning-actions">
                    <button class="asset-delete-warning-cancel">${tr('common.cancel')}</button>
                    ${!checkFailed && references.length > 1 ? `<button class="asset-delete-warning-wide" title="${escapeHtml(tr('smart.deleteProjectWideConfirm'))}">${tr('smart.deleteProjectWideButton')}</button>` : ''}
                    <button class="asset-delete-warning-confirm">${tr('smart.deleteConfirmAnyway')}</button>
                </div>
            </div>
        `;
        document.body.appendChild(backdrop);
        
        // 绑定跳转按钮
        backdrop.querySelectorAll('[data-node-id]').forEach(btn => {
            btn.onclick = () => {
                const nodeId = btn.dataset.nodeId;
                // 跳转到指定节点
                focusAndSelectNode(nodeId);
                backdrop.remove();
                resolve(false); // 跳转后不删除
            };
        });
        
        // 绑定取消按钮
        backdrop.querySelector('.asset-delete-warning-cancel').onclick = () => {
            backdrop.remove();
            resolve(false);
        };
        
        // #70 (D4): 项目范围删原图（仅多节点引用时显示）
        const wideBtn = backdrop.querySelector('.asset-delete-warning-wide');
        if(wideBtn){
            wideBtn.onclick = () => {
                if(confirm(tr('smart.deleteProjectWideConfirm'))){
                    backdrop.remove();
                    resolve('wide');
                }
            };
        }
        
        // 绑定确认按钮
        backdrop.querySelector('.asset-delete-warning-confirm').onclick = () => {
            backdrop.remove();
            resolve('delete');
        };
        
        // 点击背景关闭
        backdrop.onclick = (e) => {
            if(e.target === backdrop){
                backdrop.remove();
                resolve(false);
            }
        };
    });
}

function removeProjectMediaFromCache(assetId){
    if(!assetId) return;
    canvasState.projectMediaLibrary.items = (canvasState.projectMediaLibrary.items || []).filter(item => item.asset_id !== assetId);
}

// #74 (D1): 级联归档——节点/单图删除后，无其他引用的媒体移入回收区（7 天可恢复），
// 替代「留在资源库变孤儿、等用户自己去未使用面板清理」。
async function archiveProjectMediaAsset(assetId, reason){
    const projectId = canvas?.project_id || canvas?.project || canvasId;
    if(!projectId || !assetId) return {ok:false};
    const res = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(assetId)}/archive`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({reason: reason || 'node-delete'})
    });
    if(!res.ok) return {ok:false, status:res.status};
    return {ok:true};
}
function focusAndSelectNode(nodeId){
    // 滚动到节点并选中
    const node = (canvas?.nodes || []).find(n => n.id === nodeId);
    if(!node) return;
    // 设置选中状态（清掉多选残留，保持单节点选中语义）
    canvasState.selectedIds = [];
    canvasState.selectedId = nodeId;
    canvasState.selectedImage = {nodeId:'', index:-1};
    // 渲染并滚动到节点
    render();
    const nodeEl = document.querySelector(`[data-node-id="${nodeId}"]`);
    if(nodeEl){
        nodeEl.scrollIntoView({behavior: 'smooth', block: 'center'});
        nodeEl.classList.add('node-selected-pulse');
        setTimeout(() => nodeEl.classList.remove('node-selected-pulse'), 2000);
    }
}
async function copyProjectMediaToGlobal(item){
    const projectId = canvas?.project_id || canvas?.project || sourceProjectId || canvasId || '';
    let assetId = item?.asset_id || '';
    if(!assetId){
        const match = String(item?.url || '').match(/\/api\/project-library\/[^/]+\/media\/([^/?#]+)/);
        if(match) assetId = decodeURIComponent(match[1]);
    }
    if(!projectId || !assetId) throw new Error(tr('smart.assetAddFail'));
    const saved = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(assetId)}/copy-to-global`, {method:'POST'});
    const copied = await saved.json().catch(() => ({}));
    if(!saved.ok || !copied.item) throw new Error(typeof copied.detail === 'string' ? copied.detail : tr('smart.assetAddFail'));
    const localData = await fetch('/api/local-assets').then(r => r.ok ? r.json() : {items:[], tree:null});
    setLocalAssetLibraryFromResponse(localData);
    renderAssetLibrary();
    toast(tr('smart.savedToGlobal'));
}
async function addUrlToAssetLibrary(url, name='', item=null){
    if(assetLibraryIsProject()){
        toast(tr('smart.projectMediaAutomatic'));
        return;
    }
    if(item?.asset_id || /^\/api\/project-library\/[^/]+\/media\/[^/]+/.test(String(url || ''))){
        return copyProjectMediaToGlobal({...item, url});
    }
    if(assetLibraryIsLocal()) return addUrlToLocalAssetLibrary(url, name);
    const cat = activeAssetCategory();
    if(!cat){ toast(tr('smart.assetNoFolder')); return; }
    const data = await fetch('/api/asset-library/items', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({library_id:activeAssetLibraryId, category_id:cat.id, url, name})}).then(async r => {
        if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.assetAddFail'));
        return r.json();
    });
    setAssetLibraryFromResponse(data);
    toast(tr('smart.assetSaved'));
}
function localAssetFolderPath(){
    const cat = activeAssetCategory();
    return cat && cat.id !== '__root__' ? (cat.id || '') : '';
}
function setLocalAssetLibraryFromResponse(data){
    localAssetLibrary = {items:Array.isArray(data.items) ? data.items : localAssetLibrary.items, tree:data.tree || localAssetLibrary.tree};
}
async function addFilesToLocalAssetLibrary(files=[]){
    const supported = [...(files || [])].filter(isSupportedUploadFile);
    if(!supported.length) return [];
    const form = new FormData();
    form.append('folder', localAssetFolderPath());
    supported.forEach(file => form.append('files', file, file.name || 'media'));
    const data = await fetch('/api/local-assets/upload', {method:'POST', body:form}).then(async r => {
        if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.assetAddFail'));
        return r.json();
    });
    const localData = await fetch('/api/local-assets').then(r => r.ok ? r.json() : {items:[], tree:null});
    setLocalAssetLibraryFromResponse(localData);
    renderAssetLibrary();
    toast(trf('smart.savedLocalAssets', {n: data.files?.length || 0}));
    return data.files || [];
}
async function addLocalPathsToLocalAssetLibrary(paths=[]){
    const imported = await importSmartLocalImages(paths);
    return addUrlItemsToLocalAssetLibrary(imported.map(item => ({url:item.url, name:item.name || smartImageNameFromUrl(item.url)})));
}
async function addUrlItemsToLocalAssetLibrary(items=[]){
    const list = (items || []).filter(item => item?.url);
    if(!list.length) return [];
    const data = await fetch('/api/local-assets/import-urls', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({folder:localAssetFolderPath(), items:list.map(item => ({url:item.url, name:item.name || smartImageNameFromUrl(item.url)}))})
    }).then(async r => {
        if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.assetAddFail'));
        return r.json();
    });
    setLocalAssetLibraryFromResponse(data);
    renderAssetLibrary();
    toast(trf('smart.savedLocalAssets', {n: data.count || 0}));
    return data.files || [];
}
async function addUrlToLocalAssetLibrary(url, name=''){
    return addUrlItemsToLocalAssetLibrary([{url, name:name || smartImageNameFromUrl(url)}]);
}
async function deleteLocalAssetFromPanel(itemId){
    const item = (activeAssetCategory()?.items || []).find(x => x.id === itemId)
        || (localAssetLibrary.items || []).find(x => x.id === itemId || x.file === itemId);
    if(!item) return;
    // #66: 本地素材删=磁盘硬删，先确认（声明不可恢复）。
    if(!confirm(tr('smart.localAssetDeleteConfirm'))) return;
    try {
        const data = await fetch('/api/local-assets/delete', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({names:[item.file || item.id]})
        }).then(async r => {
            if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.localAssetDeleteFail'));
            return r.json();
        });
        const localData = await fetch('/api/local-assets').then(r => r.ok ? r.json() : {items:[], tree:null});
        setLocalAssetLibraryFromResponse(localData);
        renderAssetLibrary();
        toast(data.deleted?.length ? tr('smart.localAssetDeleted') : tr('smart.localAssetNotFound'));
    } catch(err){
        toast(err.message || tr('smart.localAssetDeleteFail'));
    }
}
// #70 (D4): 项目范围删原图——删除生成原图+项目中全部引用（节点/历史）。
// 后端端点按 ADR-0014 早已存在，此前无前端入口；此处为统一删除对话框补充显式选项。
async function deleteProjectOriginalWide(assetId, btn){
    const projectId = canvas?.project_id || canvas?.project || sourceProjectId || canvasId || '';
    if(!projectId || !assetId) return;
    if(btn){ btn.disabled = true; btn.setAttribute('aria-busy','true'); }
    try {
        // #76 (C-3)：统一 helper——409 自动 rebase 到最新画布后再报错
        const result = await projectCanvasRequest(`/api/project-library/${encodeURIComponent(projectId)}/original/delete-project-wide`, {
            method:'POST',
            payload: () => JSON.stringify({asset_id: assetId, base_revision: Number(canvas?.revision || 0)})
        });
        if(!result.ok){
            toast(result.message || tr('smart.assetDeleteFail'));
            return;
        }
        if(result.data.canvas) await adoptServerCanvas(result.data.canvas);
        // 原图已硬删：清本地缓存 + 剔除含该媒体的 undo/redo 快照（P0-4 防护）
        removeProjectMediaFromCache(assetId);
        if(stripDeletedMediaFromUndoStacks(assetId)) toast(tr('smart.toastDanglingMediaDropped'));
        if(assetLibraryIsProject()) renderAssetLibrary();
        refreshSmartProjectMediaTrashButton();
        toast(tr('smart.assetDeleted'));
    } catch(err){
        toast(err?.message || tr('smart.assetDeleteFail'));
    } finally {
        if(btn){ btn.disabled = false; btn.removeAttribute('aria-busy'); }
    }
}
// 迁移旧数据：早期把图片节点作为成员（items[]）放进分组的画布，统一把这些图片吸收进 group.images，
// 让它们显示为卡片内的缩略图网格（新模型）。一次性、幂等。
function migrateSmartGroupImageMembers(){
    let changed = false;
    canvasState.nodes.filter(isSmartGroupNode).forEach(group => {
        const imageMemberIds = (Array.isArray(group.items) ? group.items : [])
            .map(id => canvasState.nodes.find(n => n.id === id))
            .filter(m => m && isSmartImageNode(m) && (m.images || []).some(img => img?.url))
            .map(m => m.id);
        imageMemberIds.forEach(id => {
            const member = canvasState.nodes.find(n => n.id === id);
            if(member && absorbImageNodeIntoSmartGroup(group, member)) changed = true;
        });
    });
    return changed;
}
let canvasRecoveryBlocked = false;

function smartRecoveryLabel(file){
    if(file === 'canvas.json') return tr('smart.recoveryCanvas');
    if(file === 'project.json') return tr('smart.recoveryProjectMeta');
    const suffix = String(file || '').replace(/^.*\.(bak(?:-\d+)?)$/, '$1');
    const index = suffix === 'bak' ? 1 : Number(suffix.replace(/^bak-/, '')) || 0;
    return `${tr('smart.recoveryBackup')} ${index}`;
}

function smartRecoveryRow(entry){
    const baseFile = String(entry.file || '').replace(/\.bak(?:-\d+)?$/, '');
    const canRestore = !entry.primary && !!entry.exists && !!entry.valid;
    const time = entry.mtime ? new Date(entry.mtime).toLocaleString() : '—';
    const size = entry.exists ? `${(Number(entry.size || 0) / 1024).toFixed(1)} KB` : '—';
    const status = !entry.exists ? tr('smart.recoveryMissing') : (entry.valid ? tr('smart.recoveryValid') : tr('smart.recoveryCorrupt'));
    const badge = entry.exists && !entry.valid ? 'bad' : 'ok';
    return `<div class="smart-recovery-row${entry.primary ? ' is-primary' : ''}${entry.exists && !entry.valid ? ' is-corrupt' : ''}">
        <div class="smart-recovery-row-main">
            <span class="smart-recovery-row-name">${escapeHtml(smartRecoveryLabel(entry.file))}</span>
            <span class="smart-recovery-row-meta">${escapeHtml(time)} · ${size}</span>
        </div>
        <div class="smart-recovery-row-side">
            <span class="smart-recovery-badge ${badge}">${escapeHtml(status)}</span>
            ${canRestore ? `<button class="smart-recovery-btn small" type="button" data-recovery-file="${escapeAttr(baseFile)}" data-recovery-backup="${escapeAttr(entry.file)}">${escapeHtml(tr('smart.recoveryRestore'))}</button>` : ''}
        </div>
    </div>`;
}

function smartRecoveryRender(){
    const list = document.getElementById('smartRecoveryList');
    if(!list) return;
    const entries = Array.isArray(window.__smartRecoveryEntries) ? window.__smartRecoveryEntries : [];
    if(!entries.length){
        list.innerHTML = `<div class="smart-recovery-empty">${escapeHtml(tr('smart.recoveryNone'))}</div><div class="smart-recovery-status" role="status"></div>`;
        return;
    }
    list.innerHTML = entries.map(smartRecoveryRow).join('') + '<div class="smart-recovery-status" role="status"></div>';
    list.querySelectorAll('[data-recovery-file]').forEach(btn => {
        btn.addEventListener('click', () => void smartRecoveryRestore(btn.dataset.recoveryFile, btn.dataset.recoveryBackup));
    });
}

function smartRecoveryStatus(text){
    const list = document.getElementById('smartRecoveryList');
    const line = list && list.querySelector('.smart-recovery-status');
    if(line) line.textContent = text || '';
}

async function smartRecoveryRestore(file, backup){
    smartRecoveryStatus('');
    try {
        const res = await fetch(`/api/canvases/${encodeURIComponent(canvasId)}/recovery/restore`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({file, backup}),
        });
        const data = await res.json().catch(() => ({}));
        if(!res.ok){
            smartRecoveryStatus(String(data.detail || res.status));
            return;
        }
        smartRecoveryStatus(tr('smart.recoveryRestored'));
        setTimeout(() => location.reload(), 600);
    } catch(error){
        smartRecoveryStatus(String(error));
    }
}

async function openCanvasRecoveryWizard(recovery){
    canvasRecoveryBlocked = true;
    window.__smartRecoveryEntries = [];
    const modal = document.getElementById('smartRecoveryModal');
    if(modal) modal.classList.add('open');
    const hint = document.getElementById('smartRecoveryHint');
    if(hint){
        hint.textContent = recovery && recovery.restored_from
            ? tr('smart.recoveryAutoRecovered').replace('{backup}', smartRecoveryLabel(recovery.restored_from))
            : tr('smart.recoveryHint');
    }
    const retry = document.getElementById('smartRecoveryRetry');
    if(retry && !retry.dataset.bound){
        retry.dataset.bound = '1';
        retry.addEventListener('click', () => void retryCanvasRecovery());
    }
    smartRecoveryStatus(tr('smart.recoveryLoading'));
    smartRecoveryRender();
    try {
        const res = await fetch(`/api/canvases/${encodeURIComponent(canvasId)}/recovery/backups`);
        if(res.ok){
            const data = await res.json();
            window.__smartRecoveryEntries = Array.isArray(data.backups) ? data.backups : [];
        }
    } catch(error) { /* 旧格式画布没有备份：列表保持为空 */ }
    smartRecoveryRender();
}

async function retryCanvasRecovery(){
    const modal = document.getElementById('smartRecoveryModal');
    if(modal) modal.classList.remove('open');
    canvasRecoveryBlocked = false;
    const loaded = await loadCanvas();
    if(loaded){
        syncApiKindToggleVisibility();
        render();
    }
}

async function loadCanvas(){
    if(!canvasId) return true;
    try {
        const res = await fetch(`/api/canvases/${encodeURIComponent(canvasId)}`);
        if(!res.ok){
            // issue #94（ADR 0018）：文件损坏时不静默进空画布，否则自动保存会覆盖损坏文件。
            void openCanvasRecoveryWizard();
            return false;
        }
        const data = await res.json();
        if(data.canvas && data.canvas._recovery){
            // issue #94（ADR 0018）：后端自动回退到了备份副本，必须让用户显式确认恢复目标。
            void openCanvasRecoveryWizard(data.canvas._recovery);
            return false;
        }
        canvas = data.canvas;
        conversationCanvasLastAcceptedRevision = Number(canvas?.revision || 0);
        persistedCoreSnapshot = canvas.format === 'firefly-canvas-v1' ? projectCanvasSnapshot(canvasForStorage()) : null;
        if(canvas.format === 'firefly-canvas-v1'){
            const backLabel = document.querySelector('.smart-back span');
            if(backLabel) backLabel.textContent = tr('smart.backToProjects');
            setProjectSaveState('saved', tr('smart.projectSaved'));
        }
        canvasUsesConnections = Object.prototype.hasOwnProperty.call(canvas || {}, 'connections');
        document.title = canvas.title || tr('canvas.smartCanvas');
        setSmartTitleText(canvas.title || tr('canvas.smartCanvas'));
        canvasState.nodes = (Array.isArray(canvas.nodes) ? canvas.nodes : []).map(hydrateProjectMediaReferences).map(normalizeLegacySmartNode).filter(Boolean);
        canvas.history = sortSmartHistoryLogs(hydrateProjectMediaReferences(canvas.history || []));
        migrateSmartGroupImageMembers();
        canvas.connections = Array.isArray(canvas.connections) ? canvas.connections : [];
        canvasState.nodes.forEach(n => {
            const pendingTasks = smartPendingTasks(n);
            if(pendingTasks.length){
                n.pending = Math.max(pendingTasks.length, Number(n.pending || 0) || pendingTasks.length);
                n.running = false;
                ensureSmartPendingHistoryLogs(n, pendingTasks);
            } else if(smartNodeHasDisplayResult(n)){
                markSmartNodeComplete(n, {hideTimer:true});
            } else if(n.pending || n.queued){
                clearSmartNodeBusyState(n);
            }
        });
        const cleanedCompletedState = clearCompletedNodeBusyStates();
        const recoveredLoopOutputs = recoverStuckLoopOutputsFromHistory();
        const hiddenCompletedTimers = hideCompletedRunTimers();
        const cleanedDetachedInputs = cleanupDetachedRunInputRefs();
        canvas.nodes = canvasState.nodes;
        rebuildMarkedAssetIdsFromNodes();
        updateSmartMarkedBadge(); // 刷新后角标立即可见（badge 不再等面板打开才同步）
        if(isProjectCanvasData(canvas)){
            // Core project files never carry UI state. Keep the per-project
            // viewport in browser-local storage instead.
            viewport = loadProjectViewport(canvasId);
        } else {
            // Classic canvases retain their historical viewport/settings fields.
            viewport = {...viewport, ...(canvas.viewport || {})};
            viewport.scale = safeScale(viewport.scale);
            if(canvas.settings) settings = {...settings, ...canvas.settings};
        }
        normalizeSmartVideoModeSettings(settings, true);
        canvasState.nodes.forEach(node => {
            if(node.runSettings){
                node.runSettings.engine = normalizeSmartOnlineEngine(node.runSettings.engine);
                normalizeSmartVideoModeSettings(node.runSettings, true);
            }
        });
        loadRecentSmartSettings();
        applyRecentSmartSettingsForCurrentMode();
        canvasDefaultSmartSettings = cloneSmartSettings(settings);
        updateProviderModels();
        refreshPersistedDurableSnapshot(canvasForStorage());
        applyViewport();
        render();
        frameSmartCanvasContentIfNeeded();
        requestAnimationFrame(() => {
            const subject = selectedNode();
            if(subject && composer && getComputedStyle(composer).display !== 'none'){
                positionComposerForNode(subject);
            }
        });
        if(cleanedDetachedInputs || recoveredLoopOutputs) scheduleSave();
        resumeConversationTasks();
        resumeSmartPendingTasks();
        resumeJimengPendingNodes();
        startCanvasMetaPoll();
        // Mark the canvas as authoritatively loaded. From this point on an empty
        // local node set is treated as a deliberate user action and autosave may
        // commit it (with force_empty) instead of being treated as a load failure.
        canvasLoadReady = true;
        return true;
    } catch(e) {
        toast(tr('smart.toastCanvasFail'));
        void openCanvasRecoveryWizard();
        return false;
    }
}
async function loadAutosavePreferences(){
    try {
        await window.FireflyGlobalPreferences?.load?.();
    } catch(error) {
        // ignore
    }
}
async function refreshAutosavePreferences(preferences=null){
    try {
        let next = preferences;
        if(!next){
            const response = await fetch('/api/global-settings/preferences', {cache:'no-store'});
            if(!response.ok) return false;
            next = (await response.json()).preferences || {};
        }
        return true;
    } catch(error) {
        return false;
    }
}
function durableSnapshotForCanvas(sourceCanvas=canvas){
    const helper = window.FireflyProjectCanvasMutations?.durableSnapshotFromCanvas;
    return helper ? helper(sourceCanvas) : null;
}
function refreshPersistedDurableSnapshot(sourceCanvas=canvas){
    persistedDurableSnapshot = sourceCanvas?.format === 'firefly-canvas-v1'
        ? durableSnapshotForCanvas(sourceCanvas)
        : null;
}
function hasDurableCanvasChanges(){
    if(canvas?.format !== 'firefly-canvas-v1') return true;
    if(!persistedDurableSnapshot) return true;
    const current = durableSnapshotForCanvas(canvasForStorage());
    return JSON.stringify(current) !== JSON.stringify(persistedDurableSnapshot);
}
function scheduleStateSave(){
    // 运行/排队/轮询等临时状态只在确实伴随持久内容变化时加入自动保存，
    // 不会因为计时器刷新或任务状态广播单独启动新的 20 分钟窗口。
    if(hasDurableCanvasChanges()) markContentDirty();
}
function markAutosaveSaved(){
    contentDirty = false;
    clearTimeout(debouncedSaveTimer);
    debouncedSaveTimer = null;
    if(canvas?.format === 'firefly-canvas-v1' && !persistedDurableSnapshot) refreshPersistedDurableSnapshot(canvasForStorage());
    setProjectSaveState('saved', tr('smart.projectSaved'));
}
function markContentDirty(){
    if(canvas?.format !== 'firefly-canvas-v1') return;
    contentDirty = true;
    scheduleDebouncedSave();
    if(canvas?.format === 'firefly-canvas-v1') setProjectSaveState('dirty', tr('smart.projectDirty'));
}
function scheduleDebouncedSave(){
    // Content mutations are also flushed after a short quiet period, so an
    // active user never waits the full autosave interval before data lands
    // on disk. A burst keeps resetting the timer, coalescing into one save.
    clearTimeout(debouncedSaveTimer);
    debouncedSaveTimer = setTimeout(() => {
        debouncedSaveTimer = null;
        if(!contentDirty) return;
        saveCanvas({automatic:true});
    }, DEBOUNCED_SAVE_MS);
}
function scheduleSave(options={}){
    // Content mutations start a coalescing window; later mutations keep its
    // due time so a burst can result in at most one automatic save per interval.
    if(options?.state){
        scheduleStateSave();
        return;
    }
    markContentDirty();
}
function setProjectSaveState(state, text){
    const indicator = document.getElementById('projectSaveState');
    if(!indicator) return;
    indicator.hidden = canvas?.format !== 'firefly-canvas-v1';
    indicator.dataset.state = state;
    indicator.textContent = text;
    if(smartSaveButton){
        smartSaveButton.dataset.state = state;
        smartSaveButton.disabled = smartSaveButton.dataset.busy === '1' || state === 'saving';
    }
}
async function manualSaveCanvas(){
    if(!canvas || canvas.format !== 'firefly-canvas-v1' || !canvasId) return true;
    if(smartSaveButton?.dataset.busy === '1') return false;
    if(smartSaveButton){
        smartSaveButton.dataset.busy = '1';
        smartSaveButton.setAttribute('aria-busy', 'true');
        smartSaveButton.disabled = true;
    }
    try {
        const saved = await saveProjectCanvasUntilSettled();
        if(saved) toast(tr('smart.projectSaved'));
        return saved === true;
    } catch(error){
        toast(error?.message || tr('smart.projectSaveFailed'));
        return false;
    } finally {
        if(smartSaveButton){
            delete smartSaveButton.dataset.busy;
            smartSaveButton.removeAttribute('aria-busy');
            smartSaveButton.disabled = document.getElementById('projectSaveState')?.dataset.state === 'saving';
        }
    }
}
async function saveProjectCanvasUntilSettled({maxAttempts=4}={}){
    if(canvas?.format !== 'firefly-canvas-v1') return true;
    for(let attempt = 0; attempt < maxAttempts; attempt++){
        clearTimeout(debouncedSaveTimer);
        debouncedSaveTimer = null;
        while(canvasSyncInFlight) await new Promise(resolve => setTimeout(resolve, 50));
        const saved = await saveCanvas({automatic:false});
        if(saved !== true) return false;
        if(!hasDurableCanvasChanges()) return true;
    }
    return false;
}
function projectCanvasMutationClient(){
    const client = window.FireflyProjectCanvasMutations;
    if(!client?.snapshotFromCanvas || !client?.mutationsFromCanvas) throw new Error(tr('smart.incrementalModuleMissing'));
    return client;
}
function projectCanvasSnapshot(storageCanvas){
    return projectCanvasMutationClient().snapshotFromCanvas(storageCanvas);
}
function buildCoreMutations(storageCanvas, previousSnapshot=persistedCoreSnapshot){
    return projectCanvasMutationClient().mutationsFromCanvas(storageCanvas, previousSnapshot);
}
async function saveCoreCanvasIncrementally(storageCanvas){
    const projectStorageCanvas = await materializeProjectMediaReferences(storageCanvas);
    const changeSet = buildCoreMutations(projectStorageCanvas);
    if(!changeSet.mutations.length){
        syncLiveProjectMediaReferences(storageCanvas, projectStorageCanvas);
        persistedDurableSnapshot = durableSnapshotForCanvas(projectStorageCanvas);
        setProjectSaveState('saved', tr('smart.projectSaved'));
        return {saved:true, hasLateChanges:false};
    }
    setProjectSaveState('saving', tr('smart.projectSaving'));
    const mutationClient = window.FireflyProjectCanvasMutations;
    if(!mutationClient?.flushProjectCanvasMutations) throw new Error(tr('smart.incrementalModuleMissing'));
    const flushResult = await mutationClient.flushProjectCanvasMutations({
        canvas:{...projectStorageCanvas, revision:canvas.revision ?? 0},
        baseline:persistedCoreSnapshot,
        mutations:changeSet.mutations,
        maxConflictRetries:2,
        confirmSaved:async accepted => {
            const projectId = canvas.project_id || canvas.project || canvasId;
            const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/flush`, {method:'POST'});
            if(!response.ok) throw new Error(await responseErrorMessage(response, tr('smart.canvasFlushFailed')));
            const result = await response.json();
            return Number(result.revision || 0) >= Number(accepted.revision || 0);
        },
        send:async (baseRevision, mutations) => {
            // Empty-canvas guard (H39 incident): declare force_empty only when the
            // canvas was authoritatively loaded and the user has (deliberately)
            // emptied it. A failed load leaves canvasLoadReady=false, so a bogus
            // empty state is rejected server-side instead of wiping the canvas.
            const localEmpty = (Array.isArray(canvasState.nodes) ? canvasState.nodes : []).length === 0;
            const forceEmpty = canvasLoadReady && localEmpty;
            const response = await fetch(`/api/project-library/${encodeURIComponent(canvas.project_id || canvas.project || canvasId)}/canvas`, {
                method:'PATCH',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({mutations, client_id:smartClientId, base_revision:baseRevision, force_empty:forceEmpty}),
            });
            if(response.status === 409){
                const conflict = await response.json().catch(() => ({}));
                return {status:409, canvas:conflict?.detail?.canvas || null};
            }
            if(response.status === 400){
                // #62 C-4: InvalidMutation 类 400（如「项目媒体不存在」——undo/redo 复活的
                // 悬空引用）重试多少次都不会成功——返回标记让调用方触发自愈
                // （rebase 服务端画布），而不是无限重试进入错误状态。
                const errBody = await response.json().catch(() => ({}));
                const detail = typeof errBody?.detail === 'string' ? errBody.detail : '';
                if(/项目媒体不存在|project media not found/i.test(detail)){
                    return {status:400, invalidMutation:true, invalidMutationMessage:detail};
                }
            }
            if(!response.ok) throw new Error(await responseErrorMessage(response, tr('smart.toastSaveFail')));
            return {status:response.status, ...(await response.json())};
        },
    });
    // #62 C-4: 悬空媒体引用自愈——本地状态永远存不上时，放弃本次不可保存的修改，
    // 以服务端画布为准 rebase（服务端画布不含悬空引用），一次性提示用户。
    if(flushResult.response?.invalidMutation){
        const projectId = canvas.project_id || canvas.project || canvasId;
        try {
            const res = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/canvas`);
            if(res.ok){
                const body = await res.json().catch(() => ({}));
                const serverCanvas = body?.canvas || body;
                if(serverCanvas && Array.isArray(serverCanvas.nodes)){
                    canvas = {...serverCanvas};
                    const restoredNodes = filterDanglingProjectMediaImages(
                        (serverCanvas.nodes || []).map(hydrateProjectMediaReferences).map(normalizeLegacySmartNode).filter(Boolean)
                    );
                    canvasState.nodes = restoredNodes.nodes;
                    canvas.nodes = canvasState.nodes;
                    canvas.history = sortSmartHistoryLogs(hydrateProjectMediaReferences(serverCanvas.history || []));
                    canvas.connections = Array.isArray(serverCanvas.connections) ? serverCanvas.connections : [];
                    persistedCoreSnapshot = projectCanvasSnapshot(serverCanvas);
                    persistedDurableSnapshot = durableSnapshotForCanvas(serverCanvas);
                    applyViewport();
                    render();
                    setProjectSaveState('saved', tr('smart.projectSaved'));
                    toast(tr('smart.toastDanglingMediaDropped'));
                    return {saved:true, hasLateChanges:false};
                }
            }
        } catch(_){ /* 自愈失败落入下方错误路径 */ }
        setProjectSaveState('error', tr('smart.projectConflict'));
        toast(flushResult.response?.invalidMutationMessage || tr('smart.toastSaveFail'));
        return {saved:false, hasLateChanges:false};
    }
    if(!flushResult.saved){
        if(flushResult.response?.status >= 200 && flushResult.response?.status < 300){
            throw new Error(tr('smart.backgroundSaveTimeout'));
        }
        if(flushResult.conflictLimitReached && flushResult.serverCanvas){
            const lateChangeSet = buildCoreMutations(canvasForStorage(), changeSet.next);
            const localView = mutationClient.rebaseProjectCanvasMutations(flushResult.canvas, changeSet.next, lateChangeSet.mutations);
            canvas = localView.canvas;
            canvasState.nodes = (Array.isArray(canvas.nodes) ? canvas.nodes : []).map(hydrateProjectMediaReferences).map(normalizeLegacySmartNode).filter(Boolean);
            canvas.history = sortSmartHistoryLogs(hydrateProjectMediaReferences(canvas.history || []));
            canvas.connections = Array.isArray(canvas.connections) ? canvas.connections : [];
            persistedCoreSnapshot = projectCanvasSnapshot(flushResult.serverCanvas);
            persistedDurableSnapshot = durableSnapshotForCanvas(flushResult.serverCanvas);
            applyViewport();
            render();
            // The original mutation batch is still unsaved when the conflict
            // retry budget is exhausted, even if no edits arrived during the
            // request. Keep the dirty work scheduled for a later user-visible
            // retry instead of silently treating the local canvas as clean.
            if(flushResult.mutations.length || lateChangeSet.mutations.length) scheduleStateSave();
        }
        setProjectSaveState('error', tr('smart.projectConflict'));
        toast(flushResult.conflictLimitReached
            ? tr('smart.canvasConflictDetailed')
            : tr('smart.canvasConflict'));
        return {saved:false, hasLateChanges:false};
    }
    syncLiveProjectMediaReferences(storageCanvas, flushResult.canvas);
    let hasLateChanges = false;
    if(flushResult.conflictRetries > 0){
        const lateChangeSet = buildCoreMutations(canvasForStorage(), changeSet.next);
        hasLateChanges = lateChangeSet.mutations.length > 0;
        const localView = mutationClient.rebaseProjectCanvasMutations(flushResult.canvas, changeSet.next, lateChangeSet.mutations);
        canvas = localView.canvas;
        canvasState.nodes = (Array.isArray(canvas.nodes) ? canvas.nodes : []).map(hydrateProjectMediaReferences).map(normalizeLegacySmartNode).filter(Boolean);
        canvas.history = sortSmartHistoryLogs(hydrateProjectMediaReferences(canvas.history || []));
        canvas.connections = Array.isArray(canvas.connections) ? canvas.connections : [];
        applyViewport();
        render();
    } else {
        canvas.updated_at = flushResult.canvas.updated_at || canvas.updated_at;
        canvas.revision = flushResult.canvas.revision ?? canvas.revision;
    }
    persistedCoreSnapshot = projectCanvasSnapshot(flushResult.canvas);
    persistedDurableSnapshot = durableSnapshotForCanvas(flushResult.canvas);
    setProjectSaveState('saved', tr('smart.projectSaved'));
    if(hasLateChanges) scheduleStateSave();
    return {saved:true, hasLateChanges};
}
async function saveCanvas({automatic=false}={}){
    if(!canvasId || !canvas) return;
    if(automatic && !contentDirty) return true;
    if(canvasSyncInFlight){
        markContentDirty();
        return;
    }
    if(!automatic){
        clearTimeout(debouncedSaveTimer);
        debouncedSaveTimer = null;
    }
    savePromptDraftForCurrent();
    canvas.nodes = canvasState.nodes;
    // Empty-canvas guard, layer 1 (H39 incident): if the canvas has not been
    // authoritatively loaded yet and the local node set is empty, this is a
    // dangerous empty state (a failed load looks like "everything deleted").
    // Do not submit it; the server-side guard remains as the final backstop.
    if(canvas.format === 'firefly-canvas-v1' && !canvasLoadReady && (Array.isArray(canvasState.nodes) ? canvasState.nodes : []).length === 0){
        setProjectSaveState('error', tr('smart.projectLoadNotReady'));
        return false;
    }
    if(!isProjectCanvasData(canvas)){
        canvas.settings = settingsForStorage(canvasDefaultSmartSettings || initialSmartSettings);
        canvas.viewport = {...viewport};
    }
    const storageCanvas = canvasForStorage();
    if(canvas.format === 'firefly-canvas-v1'){
        canvasSyncInFlight = true;
        let result = {saved:false, hasLateChanges:false};
        try {
            result = await saveCoreCanvasIncrementally(storageCanvas);
            const hasLateDurableChanges = result?.saved && hasDurableCanvasChanges();
            if(result?.saved && !result.hasLateChanges && !hasLateDurableChanges) markAutosaveSaved();
            else if(!result?.saved || hasLateDurableChanges) markContentDirty();
        } catch(e) {
            setProjectSaveState('error', tr('smart.projectSaveFailed'));
            toast(e?.message || tr('smart.toastSaveFail'));
            markContentDirty();
        } finally {
            canvasSyncInFlight = false;
            // 保存期间到达的 canvas_updated 广播被 in-flight 跳过且确实比本地新时，
            // 补偿一次远程合并，避免对方在保存窗口内的更新丢失（配合 meta poll 兜底）。
            if(pendingRemoteUpdatedAt && pendingRemoteUpdatedAt > Number(canvas?.updated_at || 0)){
                pendingRemoteUpdatedAt = 0;
                mergeReloadCanvasNow();
            }
        }
        return result?.saved === true;
    }
    canvasSyncInFlight = true;
    try {
        const res = await fetch(`/api/canvases/${encodeURIComponent(canvasId)}`, {
            method:'PUT',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
                title:storageCanvas.title || tr('smart.title'),
                icon:storageCanvas.icon || 'sparkles',
                nodes:storageCanvas.nodes || [],
                connections:storageCanvas.connections || [],
                viewport:storageCanvas.viewport || {x:0,y:0,scale:1},
                history:storageCanvas.history || [],
                settings:storageCanvas.settings || {},
                base_updated_at:storageCanvas.updated_at || canvas.updated_at || 0,
                client_id:smartClientId
            })
        });
        if(res.ok){
            const data = await res.json();
            if(data.canvas && data.canvas.updated_at) canvas.updated_at = data.canvas.updated_at;
            markAutosaveSaved();
            return true;
        } else if(res.status === 409) {
            // 冲突：别人先保存了。合并对方的状态（节点 id 合并、图片取并集，谁都不丢），
            // 然后用对方最新的 updated_at 作为基底重存，把合并结果落盘——而不是直接覆盖对方。
            const data = await res.json().catch(() => ({}));
            const serverCanvas = data.detail?.canvas;
            if(serverCanvas){
                applyMergedServerCanvas(serverCanvas);
                canvas.nodes = canvasState.nodes;
            } else if(data.detail?.updated_at) {
                canvas.updated_at = data.detail.updated_at;
            }
            markContentDirty();
            return false;
        }
        setProjectSaveState('error', tr('smart.projectSaveFailed'));
        markContentDirty();
        return false;
    } catch(e) {
        setProjectSaveState('error', tr('smart.projectSaveFailed'));
        markContentDirty();
        return false;
    } finally {
        canvasSyncInFlight = false;
        // 保存期间到达的 canvas_updated 广播被 in-flight 跳过且确实比本地新时，
        // 补偿一次远程合并，避免对方在保存窗口内的更新丢失（配合 meta poll 兜底）。
        if(pendingRemoteUpdatedAt && pendingRemoteUpdatedAt > Number(canvas?.updated_at || 0)){
            pendingRemoteUpdatedAt = 0;
            mergeReloadCanvasNow();
        }
    }
}
function flushPendingCanvasSaveOnExit(){
    // 崩溃恢复兜底：页面被隐藏/关闭/刷新时，不等 3s 防抖立即落盘，
    // 避免防抖窗口内关页导致未保存数据丢失。
    if(!canvas || canvas.format !== 'firefly-canvas-v1' || !canvasId) return;
    if(canvasSyncInFlight) return;
    if(!contentDirty && !hasDurableCanvasChanges()) return;
    clearTimeout(debouncedSaveTimer);
    debouncedSaveTimer = null;
    void saveCanvas({automatic:false});
}
function applyNodeMetaToImage(image, node){
    return stripImageGenerationMeta(image);
}
function inheritNodeMetaFromImage(node){
    if(!node) return;
    node.images = (node.images || []).map(img => stripImageGenerationMeta(img));
}
function cloneSmartNode(node, dx=0, dy=0){
    const copy = JSON.parse(JSON.stringify(node));
    copy.id = uid(
        node.type === 'smart-prompt'
            ? 'prompt'
            : node.type === 'smart-loop'
            ? 'loop'
            : node.type === 'smart-agent'
            ? 'agent'
            : node.type === 'smart-group'
            ? 'group'
            : 'smart'
    );
    copy.x = (Number(node.x) || 0) + dx;
    copy.y = (Number(node.y) || 0) + dy;
    clearSmartNodeTransientRunState(copy, {clearRunHistory:true});
    if(copy.type === 'smart-group') copy.title = copy.title || '智能分组';
    return copy;
}
function copySelectedNodes(){
    if(!canvas || isEditableTarget(document.activeElement)) return;
    const ids = selectedNodeIds();
    const copiedNodes = ids.map(id => canvasState.nodes.find(n => n.id === id)).filter(Boolean);
    if(!copiedNodes.length) return;
    const idSet = new Set(copiedNodes.map(n => n.id));
    // 复制所有与选中节点相关的连线（包括与外部节点的连线），粘贴时保持连线不断
    const copiedConnections = (canvas.connections || []).filter(c => idSet.has(c.from) || idSet.has(c.to));
    nodeClipboard = {
        nodes:JSON.parse(JSON.stringify(copiedNodes)),
        connections:JSON.parse(JSON.stringify(copiedConnections))
    };
    toast(trf('smart.copiedNodes', {n: copiedNodes.length}));
}
function pasteNodes(){
    if(!canvas || !nodeClipboard?.nodes?.length || isEditableTarget(document.activeElement)) return;
    lastNodePasteAt = Date.now();
    pushUndo();
    const sourceNodes = nodeClipboard.nodes;
    const xs = sourceNodes.map(n => Number(n.x) || 0);
    const ys = sourceNodes.map(n => Number(n.y) || 0);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const p = lastMouseWorld || viewportCenter();
    const dx = p.x - cx;
    const dy = p.y - cy;
    const idMap = new Map();
    const copies = sourceNodes.map(n => {
        const copy = cloneSmartNode(n, dx, dy);
        idMap.set(n.id, copy.id);
        return copy;
    });
    // inputNodeIds/sourceNodeId：选中集内的映射到新 ID，外部的保留原 ID（保持连线不断）
    copies.forEach(copy => {
        if(Array.isArray(copy.inputNodeIds)){
            copy.inputNodeIds = copy.inputNodeIds.map(id => idMap.get(id) || id).filter(Boolean);
        }
        if(copy.sourceNodeId) copy.sourceNodeId = idMap.get(copy.sourceNodeId) || copy.sourceNodeId;
    });
    // 连线：选中集内的端点映射到新 ID，外部的保留原 ID（保持连线不断）
    const newConnections = (nodeClipboard.connections || []).map(conn => ({
        ...conn,
        from:idMap.get(conn.from) || conn.from,
        to:idMap.get(conn.to) || conn.to
    })).filter(conn => conn.from && conn.to && conn.from !== conn.to);
    canvas.connections = [...(canvas.connections || []), ...newConnections];
    canvasState.nodes.push(...copies);
    canvasState.selectedId = copies.length === 1 ? copies[0].id : '';
    canvasState.selectedIds = copies.length > 1 ? copies.map(n => n.id) : [];
    canvasState.selectedImage = {nodeId:'', index:-1};
    render();
    scheduleSave();
}
// 跨页"素材库 → 画布"剪贴板：素材库管理页把所选素材写进这个 localStorage key，
// 画布里按 Ctrl+V 读取并批量生成图片节点（网格平铺），用完即清空（一次性）。
const SMART_CANVAS_ASSET_INBOX_KEY = 'smart_canvas_asset_inbox';
function readAssetInbox(){
    try {
        const data = JSON.parse(localStorage.getItem(SMART_CANVAS_ASSET_INBOX_KEY) || 'null');
        const items = Array.isArray(data?.items) ? data.items.filter(it => it && it.url) : [];
        if(!items.length) return null;
        if(data.ts && (Date.now() - Number(data.ts)) > 30 * 60 * 1000) return null; // 30 分钟内有效
        return items;
    } catch(e){ return null; }
}
function pasteAssetsFromInbox(){
    const items = readAssetInbox();
    if(!items) return false;
    const center = lastMouseWorld || viewportCenter();
    const cell = 260; // 网格间距（世界坐标）
    const cols = Math.max(1, Math.min(items.length, Math.ceil(Math.sqrt(items.length))));
    const rows = Math.ceil(items.length / cols);
    const startX = center.x - (cols - 1) * cell / 2;
    const startY = center.y - (rows - 1) * cell / 2;
    pushUndo();
    const created = [];
    items.forEach((it, i) => {
        const r = Math.floor(i / cols), c = i % cols;
        const p = {x: startX + c * cell, y: startY + r * cell};
        const node = createImageNodeAt(p, [assetNodeImageFromItem(it)], {skipUndo:true, select:false});
        if(node) created.push(node.id);
    });
    canvasState.selectedId = created.length === 1 ? created[0] : '';
    canvasState.selectedIds = created.length > 1 ? created : [];
    canvasState.selectedImage = {nodeId:'', index:-1};
    lastNodePasteAt = Date.now();
    try { localStorage.removeItem(SMART_CANVAS_ASSET_INBOX_KEY); } catch(e){}
    render();
    scheduleSave();
    toast(trf('smart.pastedAssets', {n: created.length}));
    return true;
}
function duplicateForAltDrag(node, preserveConnections=false){
    const ids = (isNodeSelected(node.id) ? selectedNodeIds() : [node.id]);
    const sourceNodes = ids.map(id => canvasState.nodes.find(n => n.id === id)).filter(Boolean);
    if(!sourceNodes.length) return node;
    pushUndo();
    const idMap = new Map();
    const copies = sourceNodes.map(n => {
        const copy = cloneSmartNode(n, 0, 0);
        idMap.set(n.id, copy.id);
        return copy;
    });
    copies.forEach(copy => {
        if(Array.isArray(copy.inputNodeIds)){
            copy.inputNodeIds = preserveConnections
                ? copy.inputNodeIds.map(id => idMap.get(id) || id).filter(Boolean)
                : [];
        }
        if(copy.sourceNodeId) copy.sourceNodeId = preserveConnections ? (idMap.get(copy.sourceNodeId) || copy.sourceNodeId) : '';
    });
    if(preserveConnections){
        const idSet = new Set(sourceNodes.map(n => n.id));
        const newConnections = (canvas.connections || [])
            .filter(conn => idSet.has(conn.to))
            .map(conn => ({...conn, from:idMap.get(conn.from) || conn.from, to:idMap.get(conn.to) || conn.to}))
            .filter(conn => conn.from && conn.to && conn.from !== conn.to);
        const nextConnections = [...(canvas.connections || [])];
        newConnections.forEach(conn => {
            const kind = conn.kind || 'flow';
            if(nextConnections.some(c => c.from === conn.from && c.to === conn.to && (c.kind || 'flow') === kind)) return;
            nextConnections.push(conn);
            const toNode = canvasState.nodes.find(n => n.id === conn.to) || copies.find(n => n.id === conn.to);
            if(toNode && (conn.kind || 'flow') === 'input'){
                toNode.inputNodeIds = Array.from(new Set([...(toNode.inputNodeIds || []), conn.from]));
            }
        });
        canvas.connections = nextConnections;
    }
    canvasState.nodes.push(...copies);
    canvasState.selectedId = '';
    canvasState.selectedIds = [];
    canvasState.selectedImage = {nodeId:'', index:-1};
    const dragCopy = copies.find(c => c.id === idMap.get(node.id)) || copies[0];
    render();
    scheduleSave();
    return dragCopy;
}
function shellPoint(event){
    const rect = shell.getBoundingClientRect();
    return {x:event.clientX - rect.left, y:event.clientY - rect.top};
}
let interactionLayerRaf = 0;
// 拖动/缩放节点时，每个 mousemove 都全量重建连线 SVG + 小地图会掉帧；
// 用 requestAnimationFrame 把它们合并成每帧最多刷新一次（节点本身的位移仍是即时的）。
function scheduleInteractionLayerRefresh(){
    if(interactionLayerRaf) return;
    interactionLayerRaf = requestAnimationFrame(() => {
        interactionLayerRaf = 0;
        refreshConnectionLayer();
        renderMinimap();
    });
}
function syncSmartGroupMemberElements(group){
    if(!isSmartGroupNode(group)) return;
    smartGroupCompactMembers(group).forEach(member => {
        const el = world.querySelector(`.image-node[data-id="${CSS.escape(member.id)}"]`);
        if(el){
            el.style.left = `${member.x || 0}px`;
            el.style.top = `${member.y || 0}px`;
        }
        updateNodeElementDuringResize(member);
    });
}
function isVideoMediaItem(img){
    if(!img) return false;
    if(img.kind === 'video') return true;
    const url = smartOriginalMediaUrl(img).toLowerCase();
    return /\.(mp4|webm|mov|m4v|avi|mkv)(\?|$)/.test(url);
}
function isInlineVideoActive(img){
    return Boolean(img && img._inlineVideoActive);
}
function isAudioMediaItem(img){
    if(!img) return false;
    if(img.kind === 'audio') return true;
    const url = smartOriginalMediaUrl(img).toLowerCase();
    return /\.(mp3|wav|m4a|aac|ogg|flac)(\?|$)/.test(url);
}
function isTextMediaItem(img){
    if(!img) return false;
    if(img.kind === 'text') return true;
    const url = smartOriginalMediaUrl(img).toLowerCase();
    return /\.(txt|json|csv|srt|vtt|md)(\?|$)/.test(url);
}
function isFileMediaItem(img){
    if(!img) return false;
    return img.kind === 'file';
}
function mediaKindForFile(file){
    const type = String(file?.type || '').toLowerCase();
    const name = String(file?.name || '').toLowerCase();
    if(type.startsWith('video/') || /\.(mp4|webm|mov|m4v|avi|mkv)(\?|$)/.test(name)) return 'video';
    if(type.startsWith('audio/') || /\.(mp3|wav|m4a|aac|ogg|flac)(\?|$)/.test(name)) return 'audio';
    if(type.startsWith('text/') || /\.(txt|json|csv|srt|vtt|md)(\?|$)/.test(name)) return 'text';
    return 'image';
}
function mediaKindForItem(img){
    if(isFileMediaItem(img)) return 'file';
    if(isTextMediaItem(img)) return 'text';
    if(isAudioMediaItem(img)) return 'audio';
    if(isVideoMediaItem(img)) return 'video';
    return 'image';
}
function localDisplayUrlForMediaItem(img){
    if(!img) return '';
    const candidates = [
        img.originalLocalUrl,
        img.localUrl,
        img.sourceUrl,
        img.local_url,
        img.source_url,
        img.url
    ];
    const local = candidates.find(url => url && !/^https?:\/\//i.test(String(url)));
    return local || img.url || '';
}
function imageForDisplay(img){
    if(!img || typeof img !== 'object') return img;
    const localUrl = localDisplayUrlForMediaItem(img);
    if(!localUrl || localUrl === img.url) return img;
    return {
        ...img,
        url:localUrl,
        originalLocalUrl:img.originalLocalUrl || localUrl
    };
}
function resultMediaUrls(result){
    const urls = [];
    const add = value => {
        if(!value) return;
        if(typeof value === 'string'){
            urls.push(value);
            return;
        }
        if(Array.isArray(value)){
            value.forEach(add);
            return;
        }
        if(typeof value === 'object'){
            if(value.url || value.path || value.src || value.uri){
                const url = value.url || value.path || value.src || value.uri;
                if(url){
                    const item = {url, kind:value.kind || value.type || value.mediaKind || '', name:value.name || value.filename || ''};
                    if(value.mime_type) item.mime_type = value.mime_type;
                    if(value.asset_id) item.asset_id = value.asset_id;
                    ['natural_w','natural_h','width','height','w','h','layout_w','layout_h'].forEach(key => {
                        const n = Number(value[key]);
                        if(Number.isFinite(n) && n > 0) item[key] = n;
                    });
                    urls.push(item);
                }
            }
            ['image_items','media_items','items','outputs','videos','images','urls','data','result'].forEach(key => add(value[key]));
            ['url','path','src','uri','output','output_url','outputUrl','video','video_url','videoUrl','mp4_url','mp4Url','download_url','downloadUrl','preview_url','previewUrl'].forEach(key => add(value[key]));
        }
    };
    add(result);
    ['image_items','media_items','items','outputs','videos','audios','texts','files','images','urls','data','result','output','url'].forEach(key => add(result?.[key]));
    const seen = new Set();
    return urls.map(item => {
        const url = typeof item === 'string' ? item : item?.url || item?.path || '';
        if(!url) return null;
        return typeof item === 'object' ? {...item, url} : url;
    }).filter(item => {
        const url = typeof item === 'string' ? item : item?.url || '';
        return url && !seen.has(url) && seen.add(url);
    });
}
function mediaKindForUrls(urls, fallback='image'){
    const items = (urls || []).map(item => typeof item === 'string' ? {url:item} : (item || {}));
    if(fallback && fallback !== 'image') return fallback;
    if(items.some(isVideoMediaItem)) return 'video';
    if(items.some(isAudioMediaItem)) return 'audio';
    if(items.some(isTextMediaItem)) return 'text';
    return fallback;
}
function imageRefsOnly(refs){
    return (refs || []).filter(ref => ref?.url && mediaKindForItem(ref) === 'image').slice(0, SMART_REFERENCE_IMAGE_MAX);
}
function looksLikeImageMediaUrl(url){
    const text = String(url || '').trim().toLowerCase();
    if(!text) return false;
    if(text.startsWith('data:image/')) return true;
    if(text.startsWith('asset://')) return false;
    const path = text.split('?', 1)[0].split('#', 1)[0];
    return /\.(png|jpe?g|webp|gif|bmp|tiff)$/i.test(path);
}
function videoRefsOnly(refs){
    return (refs || []).filter(ref => ref?.url && mediaKindForItem(ref) === 'video' && !looksLikeImageMediaUrl(ref.url));
}
function isRemoteVideoReferenceUrl(url){
    return /^https?:\/\//i.test(String(url || '')) || /^asset:\/\//i.test(String(url || ''));
}
function audioRefsOnly(refs){
    return (refs || []).filter(ref => ref?.url && mediaKindForItem(ref) === 'audio');
}
function thumbMediaHtml(img){
    if(isFileMediaItem(img) || isTextMediaItem(img)) return `<div class="media-thumb file-thumb" data-media-url="${escapeAttr(img.url || '')}" data-media-kind="${escapeAttr(mediaKindForItem(img))}"><i data-lucide="${isTextMediaItem(img) ? 'file-text' : 'file'}"></i><span>${escapeHtml(img.name || (isTextMediaItem(img) ? 'Text' : 'File'))}</span></div>`;
    if(isAudioMediaItem(img)) return `<div class="media-thumb audio-thumb" data-media-url="${escapeAttr(img.url || '')}" data-media-kind="audio"><i data-lucide="file-audio"></i><span>${escapeHtml(img.name || 'Audio')}</span></div>`;
    if(isVideoMediaItem(img)) return `<div class="media-thumb video-thumb">${isInlineVideoActive(img) ? smartVideoPlayerHtml(img.url || '') : `${smartVideoPreviewHtml(img, 512, 'alt=""')}<button class="smart-video-play thumb-video-play" type="button" title="${escapeHtml(tr('smart.play'))}"><i data-lucide="play"></i></button>`}</div>`;
    return smartPreviewImgHtml(img, 512, 'draggable="false"');
}
function imageResolutionLabel(img){
    const w = Number(img?.natural_w || img?.width || img?.w || 0);
    const h = Number(img?.natural_h || img?.height || img?.h || 0);
    return w > 0 && h > 0 ? `${Math.round(w)} x ${Math.round(h)}` : '';
}
function imageResolutionBadgeHtml(img){
    const label = imageResolutionLabel(img);
    return label ? `<span class="image-resolution-badge">${escapeHtml(label)}</span>` : '';
}
function imageNameLabel(img, fallback='image'){
    const raw = String(img?.name || fileNameFromUrl(img?.url || '') || fallback || 'image').trim();
    return raw || 'image';
}
function imageNameBadgeHtml(img, options={}){
    if(!img?.url) return '';
    const label = imageNameLabel(img);
    const outsideClass = options.outside ? ' image-name-badge-outside' : '';
    return `<span class="image-name-badge${outsideClass}" data-image-name="1" title="${escapeAttr(label)}">${escapeHtml(label)}</span>`;
}
function thumbDisplaySize(img, maxSize){
    const limit = Math.max(28, Math.round(Number(maxSize) || 96));
    const size = mediaLayoutSize(img);
    const w = size.width;
    const h = size.height;
    if(!(w > 0 && h > 0)) return {width:limit, height:limit};
    const fit = Math.min(limit / w, limit / h);
    return {
        width:Math.max(28, Math.round(w * fit)),
        height:Math.max(28, Math.round(h * fit))
    };
}
// #89 返工：多图网格每列的实际宽度（行主序 i%cols）。contain 压缩的竖图列窄于
// limit 盒，框宽若按每列满宽计算，图就会“浮”在过宽列中央。
function mediaGridColumnWidths(images, cols, thumb){
    const columnCount = Math.max(1, Number(cols) || 1);
    const widths = Array.from({length: columnCount}, () => 0);
    (images || []).forEach((img, i) => {
        if(!img) return;
        const w = thumbDisplaySize(img, thumb).width;
        const c = i % columnCount;
        if(c < widths.length && w > widths[c]) widths[c] = w;
    });
    return widths;
}
function mediaGroupGridHeight(images, cols, visibleRows, thumb){
    const columnCount = Math.max(1, Number(cols) || 1);
    const rowCount = Math.max(1, Number(visibleRows) || 1);
    let height = 0;
    for(let row = 0; row < rowCount; row += 1){
        const items = (images || []).slice(row * columnCount, (row + 1) * columnCount);
        if(!items.length) break;
        height += Math.max(...items.map(item => thumbDisplaySize(item, thumb).height));
        if(row > 0) height += 8;
    }
    return height;
}
function thumbItemStyle(img, maxSize){
    const size = thumbDisplaySize(img, maxSize);
    return `--thumb-w:${size.width}px;--thumb-h:${size.height}px`;
}
function applyThumbDisplaySizeToElement(itemEl, img, maxSize=0){
    if(!itemEl?.classList?.contains('thumb-item')) return;
    const limit = Math.max(
        28,
        Math.round(
            Number(maxSize || 0)
            || Number(itemEl.style.getPropertyValue('--thumb-size').replace('px', ''))
            || Math.max(itemEl.clientWidth || 0, itemEl.clientHeight || 0)
            || 96
        )
    );
    const size = thumbDisplaySize(img, limit);
    itemEl.style.setProperty('--thumb-w', `${size.width}px`);
    itemEl.style.setProperty('--thumb-h', `${size.height}px`);
}
function updateImageResolutionBadgeElement(itemEl, img){
    if(!itemEl) return;
    const label = imageResolutionLabel(img);
    let badge = itemEl.querySelector('.image-resolution-badge');
    if(!label){
        badge?.remove();
        return;
    }
    if(!badge){
        badge = document.createElement('span');
        badge.className = 'image-resolution-badge';
        itemEl.appendChild(badge);
    }
    badge.textContent = label;
}
function singleMediaHtml(img, w, h){
    if(isFileMediaItem(img) || isTextMediaItem(img)) return `<div class="node-img media-card media-file-card" style="width:${w}px;height:${h}px"><div class="media-card-icon"><i data-lucide="${isTextMediaItem(img) ? 'file-text' : 'file'}"></i></div><div class="media-card-title">${escapeHtml(img.name || (isTextMediaItem(img) ? 'Text' : 'File'))}</div><div class="media-card-sub">${isTextMediaItem(img) ? 'TEXT' : 'FILE'}</div></div>`;
    if(isAudioMediaItem(img)) return `<div class="node-img media-card media-audio-card" style="width:${w}px;height:${h}px"><div class="media-card-icon"><i data-lucide="file-audio"></i></div><div class="media-card-title">${escapeHtml(img.name || 'Audio')}</div><div class="media-card-sub">AUDIO</div><audio src="${escapeAttr(img.url || '')}" data-url="${escapeAttr(img.url || '')}" controls preload="metadata"></audio></div>`;
    if(isVideoMediaItem(img)) return `<div class="node-img media-card media-video-card" style="width:${w}px;height:${h}px">${isInlineVideoActive(img) ? smartVideoPlayerHtml(img.url || '') : `${smartVideoPreviewHtml(img, 768, 'alt=""')}<button class="smart-video-play" type="button" title="${escapeHtml(tr('smart.play'))}"><i data-lucide="play"></i></button>`}</div>`;
    return smartPreviewImgHtml(img, 768, `class="node-img" draggable="false" style="width:${w}px;height:${h}px"`);
}
function smartNodeHasLiveMedia(node){
    return Boolean(!node?.pending && (node?.images || []).some(img => img?.url));
}
function captureMediaPlaybackState(media){
    if(!media) return null;
    return {
        currentTime:Number.isFinite(media.currentTime) ? media.currentTime : 0,
        paused:Boolean(media.paused),
        playbackRate:Number.isFinite(media.playbackRate) ? media.playbackRate : 1,
        muted:Boolean(media.muted),
        volume:Number.isFinite(media.volume) ? media.volume : 1
    };
}
function restoreMediaPlaybackState(media, state){
    if(!media || !state) return;
    try { media.playbackRate = state.playbackRate || 1; } catch(e) {}
    try { media.muted = state.muted; } catch(e) {}
    try { media.volume = state.volume; } catch(e) {}
    const applyTime = () => {
        if(Number.isFinite(state.currentTime) && state.currentTime > 0 && Math.abs((media.currentTime || 0) - state.currentTime) > 0.2){
            try { media.currentTime = state.currentTime; } catch(e) {}
        }
        if(!state.paused && typeof media.play === 'function'){
            const playPromise = media.play();
            if(playPromise?.catch) playPromise.catch(() => {});
        }
    };
    if(media.readyState >= 1) applyTime();
    else media.addEventListener('loadedmetadata', applyTime, {once:true});
}
function transplantSmartMediaElements(oldNodeEl, newNodeEl){
    const oldItems = [...(oldNodeEl?.querySelectorAll?.('.thumb-item,.image-wrap') || [])];
    const newItems = [...(newNodeEl?.querySelectorAll?.('.thumb-item,.image-wrap') || [])];
    oldItems.forEach((oldItem, index) => {
        const oldMedia = oldItem.querySelector('video,audio,img.node-img,.thumb-item > img,.media-thumb img');
        if(!oldMedia) return;
        const selector = oldMedia.tagName.toLowerCase();
        const oldUrl = oldMedia.dataset?.url || oldMedia.dataset?.originalSrc || oldMedia.getAttribute('src') || '';
        const oldSignature = oldItem.dataset?.mediaSignature || `${selector}:${oldUrl}`;
        const newItem = newItems.find(item => item.dataset?.mediaSignature === oldSignature)
            || newItems.find(item => item.querySelector?.(selector)?.dataset?.url === oldUrl)
            || newItems.find(item => item.querySelector?.(selector)?.dataset?.originalSrc === oldUrl)
            || newItems.find(item => item.querySelector?.(selector)?.getAttribute?.('src') === oldMedia.getAttribute('src'))
            || newItems[index];
        const newMedia = newItem?.querySelector?.(selector);
        const newUrl = newMedia?.dataset?.url || newMedia?.dataset?.originalSrc || newMedia?.getAttribute?.('src') || '';
        if(!newMedia || oldUrl !== newUrl) return;
        if(selector === 'img'){
            oldMedia.className = newMedia.className;
            oldMedia.draggable = false;
            oldMedia.alt = newMedia.getAttribute('alt') || oldMedia.getAttribute('alt') || '';
            oldMedia.style.cssText = newMedia.style.cssText;
            oldMedia.dataset.originalSrc = newMedia.dataset?.originalSrc || oldMedia.dataset?.originalSrc || '';
            newMedia.replaceWith(oldMedia);
            return;
        }
        const state = captureMediaPlaybackState(oldMedia);
        newMedia.replaceWith(oldMedia);
        restoreMediaPlaybackState(oldMedia, state);
        requestAnimationFrame(() => restoreMediaPlaybackState(oldMedia, state));
    });
}
function captureMediaPlaybackStates(){
    const states = new Map();
    world.querySelectorAll('video[data-url], audio[data-url]').forEach(media => {
        const tag = media.tagName.toLowerCase();
        const url = media.dataset.url || media.getAttribute('src') || '';
        if(url) states.set(`${tag}:${url}`, captureMediaPlaybackState(media));
    });
    return states;
}
function restoreMediaPlaybackStates(states){
    if(!states?.size) return;
    world.querySelectorAll('video[data-url], audio[data-url]').forEach(media => {
        const tag = media.tagName.toLowerCase();
        const url = media.dataset.url || media.getAttribute('src') || '';
        restoreMediaPlaybackState(media, states.get(`${tag}:${url}`));
    });
}
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
function proxiedMediaUrl(itemOrUrl, name=''){
    const url = smartOriginalMediaUrl(itemOrUrl);
    if(!url || String(url).startsWith('/assets/') || String(url).startsWith('/output/') || String(url).startsWith('/api/project-library/') || String(url).startsWith('data:') || String(url).startsWith('blob:')) return url;
    const filename = name || (typeof itemOrUrl === 'object' ? (itemOrUrl.name || '') : '') || fileNameFromUrl(url) || 'preview';
    return `/api/download-output?inline=1&url=${encodeURIComponent(url)}&name=${encodeURIComponent(filename)}`;
}
function displayMediaUrl(itemOrUrl, name=''){
    const url = smartOriginalMediaUrl(itemOrUrl);
    if(/^https?:\/\//i.test(String(url || ''))) return proxiedMediaUrl(itemOrUrl, name);
    return url;
}
function bindImageProxyFallback(imgEl, itemOrUrl){
    if(!imgEl || imgEl.dataset.proxyFallbackBound === '1') return;
    imgEl.dataset.proxyFallbackBound = '1';
    imgEl.addEventListener('error', () => {
        if(imgEl.dataset.proxyFallbackTried === '1') return;
        const fallback = proxiedMediaUrl(itemOrUrl);
        if(!fallback || fallback === imgEl.getAttribute('src')) return;
        imgEl.dataset.proxyFallbackTried = '1';
        imgEl.src = fallback;
    });
}
function safeExportFileName(name, fallback='download.zip'){
    const cleaned = String(name || fallback).replace(/[\\/:*?"<>|]+/g, '_').trim();
    return cleaned || fallback;
}
function fileNameFromUrl(url=''){
    try {
        const parsed = new URL(String(url || ''), window.location.href);
        return decodeURIComponent(parsed.pathname.split('/').filter(Boolean).pop() || '');
    } catch(e) {
        return decodeURIComponent(String(url || '').split('?')[0].split('#')[0].split('/').filter(Boolean).pop() || '');
    }
}
function extensionForMediaItem(item, fallback='.png'){
    const source = [item?.name, item?.url].map(value => String(value || '').split('?')[0].split('#')[0]).find(value => /\.[a-z0-9]{2,8}$/i.test(value));
    if(source) return source.match(/(\.[a-z0-9]{2,8})$/i)?.[1] || fallback;
    const kind = mediaKindForItem(item);
    if(kind === 'video') return '.mp4';
    if(kind === 'audio') return '.mp3';
    if(kind === 'text') return '.txt';
    return fallback;
}
function downloadNameForMediaItem(item, fallbackPrefix='canvas-output'){
    const localName = fileNameFromUrl(item?.url || '');
    const preferred = localName || item?.name || '';
    const ext = extensionForMediaItem(item);
    const randomName = `${fallbackPrefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}${ext}`;
    let name = safeExportFileName(preferred || randomName, randomName);
    if(!/\.[a-z0-9]{2,8}$/i.test(name)) name += ext;
    return name;
}
function downloadPreviewImage(){
    const node = canvasState.nodes.find(n => n.id === previewNavState.nodeId);
    const image = node?.images?.[previewNavState.index];
    if(!image?.url) return;
    const name = downloadNameForMediaItem(image, 'image');
    const link = document.createElement('a');
    link.href = `/api/download-output?url=${encodeURIComponent(image.url)}&name=${encodeURIComponent(name)}`;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
}
function downloadPreviewFile(item){
    if(!item?.url) return;
    const name = downloadNameForMediaItem(item, 'output');
    const link = document.createElement('a');
    link.href = `/api/download-output?url=${encodeURIComponent(item.url)}&name=${encodeURIComponent(name)}`;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
}
function previewDownloadGroupItems(){
    // 分组预览：整组所有成员图片按阅读顺序打包。
    if(previewNavState.groupId){
        const group = canvasState.nodes.find(n => n.id === previewNavState.groupId && isSmartGroupNode(n));
        if(group) return smartGroupImageRefs(group).map((r, index) => ({...r.item, __index:index}));
    }
    const node = canvasState.nodes.find(n => n.id === previewNavState.nodeId);
    return (node?.images || [])
        .filter(item => item?.url)
        .map((item, index) => ({...item, __index:index}))
        .sort((a, b) => {
            const ga = a.grid || {};
            const gb = b.grid || {};
            const rowDiff = Number(ga.row ?? a.__index) - Number(gb.row ?? b.__index);
            if(rowDiff) return rowDiff;
            const colDiff = Number(ga.col ?? a.__index) - Number(gb.col ?? b.__index);
            return colDiff || a.__index - b.__index;
        });
}
// #89 已标记图片：canvas.markedAssetIds 随画布 JSON 持久化（canvasForStorage 原样保留顶层字段）。
function imageMarkButtonHtml(item, index){
    const assetId = String(item?.asset_id || '');
    if(!assetId) return '';
    const marked = getMarkedAssetIds().includes(assetId);
    return `<button class="mini-x image-mark${marked ? ' image-marked' : ''}" type="button" data-image-index="${index}" title="${escapeHtml(tr(marked ? 'smart.unmarkImage' : 'smart.markImage'))}"><i data-lucide="star"></i></button>`;
}
function syncCuratedFromMarks(){
    canvasState.nodes.forEach(node => {
        const imgs = node.images || [];
        if(!imgs.some(img => img && img.marked) && !node.curated) return;
        const curated = imgs.some(img => img && img.marked);
        if(Boolean(node.curated) !== curated) node.curated = curated;
    });
}
// #89 第四轮：同步所有带 data-marked-asset 的星位按钮（画布缩略图/生图历史/生成详情/预览大图）。
function toggleImagePreviewMark(){
    const ref = canvasState.selectedImage || {};
    const node = canvasState.nodes.find(n => n.id === ref.nodeId);
    const raw = node?.images?.[Number(ref.index || 0)];
    const item = imageForDisplay(raw) || raw;
    const assetId = String(raw?.asset_id || item?.asset_id || '');
    if(!assetId){ toast(tr('smart.markUnavailable')); return; }
    setMarkedImage(assetId, !getMarkedAssetIds().includes(assetId));
}
function updateImagePreviewMarkButton(){
    const btn = document.getElementById('imagePreviewMarkBtn');
    if(!btn) return;
    const ref = canvasState.selectedImage || {};
    const node = canvasState.nodes.find(n => n.id === ref.nodeId);
    const raw = node?.images?.[Number(ref.index || 0)];
    const item = imageForDisplay(raw) || raw;
    const assetId = String(raw?.asset_id || item?.asset_id || '');
    btn.dataset.markedAsset = assetId;
    btn.hidden = !assetId || mediaKindForItem(item) !== 'image' || imageEditMode !== 'preview';
    syncMarkedImageButtons();
}
async function zipDownloadImageItems(title, items){
    const list = (items || []).filter(item => item?.url);
    if(!list.length) return;
    try {
        const filename = safeExportFileName(`${title || 'image-group'}.zip`, 'image-group.zip');
        const response = await fetch('/api/canvas-assets/download', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
                filename,
                urls:list.map(item => item.url).filter(Boolean),
                items:list.map((item, index) => ({url:item.url, name:downloadNameForMediaItem(item, `image-${String(index + 1).padStart(2, '0')}`)}))
            })
        });
        if(!response.ok) throw new Error((await response.text()) || tr('smart.batchDownloadFailed'));
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = href;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(href), 1200);
    } catch(e) {
        toast((e.message || tr('smart.batchDownloadFailed')).slice(0, 160));
    }
}
async function downloadPreviewGroup(){
    const group = previewNavState.groupId ? canvasState.nodes.find(n => n.id === previewNavState.groupId) : null;
    const owner = group || canvasState.nodes.find(n => n.id === previewNavState.nodeId);
    return zipDownloadImageItems(owner?.title, previewDownloadGroupItems());
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
function smartRunSnapshot(node, prompt, refs=[], kind='image'){
    const settingsSnapshot = cloneSmartSettings(settings);
    if(kind === 'image' && !apiImageUsesExplicitResolution(settingsSnapshot)) settingsSnapshot.resolution = '';
    return {
        nodeId:node?.id || '',
        nodeType:node?.type || 'smart-image',
        kind,
        settings:settingsSnapshot,
        runSettings:cloneSmartSettings(settingsSnapshot),
        displayPrompt:node?.runPrompt || prompt || '',
        prompt:prompt || '',
        refs:(refs || []).map(ref => ({asset_id:ref.asset_id || '', url:ref.url || '', name:ref.name || 'image', kind:ref.kind || ''})).filter(ref => ref.url),
        size: kind === 'image' && isApiLikeEngine(settingsSnapshot.engine) ? sizeForRun(settingsSnapshot) : ''
    };
}
const GENERATION_PROGRESS_PHASES = [
    ['queued', tr('smart.phaseQueued')],
    ['preparing_reference', tr('smart.phasePreparingReference')],
    ['uploading', tr('smart.phaseUploading')],
    ['provider_generating', tr('smart.phaseProviderGenerating')],
    ['receiving', tr('smart.phaseReceiving')],
    ['saving', tr('smart.phaseSaving')]
];
function generationProgressBytes(value){
    const bytes = Math.max(0, Number(value) || 0);
    if(bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
    if(bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${Math.round(bytes)} B`;
}
function generationStageProgressHtml(progress={}){
    const phase = String(progress?.phase || 'queued');
    const failed = Boolean(progress?.failed);
    const configuredIndex = Number(progress?.phase_index);
    const fallbackIndex = GENERATION_PROGRESS_PHASES.findIndex(item => item[0] === phase);
    const phaseIndex = Math.max(0, Math.min(5, Number.isFinite(configuredIndex) ? configuredIndex : Math.max(0, fallbackIndex)));
    const phaseLabel = String(progress?.label || GENERATION_PROGRESS_PHASES[phaseIndex]?.[1] || tr('smart.phaseQueued'));
    const label = failed ? trf('smart.phaseFailed', {label: phaseLabel}) : phaseLabel;
    const startedAt = Number(progress?.started_at || 0);
    const elapsedMs = startedAt > 0 ? Math.max(Number(progress?.elapsed_ms || 0), Date.now() - startedAt) : Math.max(0, Number(progress?.elapsed_ms || 0));
    const elapsed = formatRunDuration(elapsedMs) || '0s';
    const bytesDone = Number(progress?.bytes_done || 0);
    const bytesTotal = Number(progress?.bytes_total || 0);
    const bytesText = bytesTotal > 0 ? `${generationProgressBytes(bytesDone)} / ${generationProgressBytes(bytesTotal)}` : '';
    const current = Number(progress?.current || 0);
    const total = Number(progress?.total || 0);
    const stepText = current > 0 && total > 0 ? `${current} / ${total}` : '';
    const detailText = stepText || bytesText || elapsed;
    const segments = GENERATION_PROGRESS_PHASES.map(([key, title], index) => `<span class="generation-stage-segment${index < phaseIndex ? ' done' : index === phaseIndex ? ' active' : ''}" title="${escapeAttr(title)}" aria-label="${escapeAttr(title)}" data-stage="${escapeAttr(key)}"></span>`).join('');
    return `<div class="generation-stage-progress${failed ? ' is-failed' : ''}" data-phase="${escapeAttr(phase)}" data-phase-index="${phaseIndex}" role="status" aria-label="${escapeAttr(`${label} · ${elapsed}`)}"><div class="generation-stage-copy"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(detailText)}</span></div><div class="generation-stage-track" aria-hidden="true">${segments}</div><div class="generation-stage-labels" aria-hidden="true">${GENERATION_PROGRESS_PHASES.map(([, title]) => `<span>${escapeHtml(title)}</span>`).join('')}</div></div>`;
}
function applySmartTaskProgress(taskId, progress){
    const normalizedTaskId = String(taskId || '').trim();
    if(!normalizedTaskId || !progress || typeof progress !== 'object') return false;
    let changed = false;
    canvasState.nodes.forEach(node => {
        const tasks = Array.isArray(node?.pendingTasks) ? node.pendingTasks : [];
        tasks.forEach(task => {
            if(String(task?.taskId || task?.task_id || '') !== normalizedTaskId) return;
            const next = JSON.stringify(progress);
            if(JSON.stringify(task.progress || {}) === next) return;
            task.progress = {...progress};
            changed = true;
        });
    });
    const taskLog = (canvas?.history || []).find(log => smartHistoryTaskId(log) === normalizedTaskId);
    if(taskLog){
        const nextProgress = {...progress, failed:taskLog.status === 'failed' || Boolean(progress.failed)};
        if(JSON.stringify(taskLog.progress || {}) !== JSON.stringify(nextProgress)){
            taskLog.progress = nextProgress;
            changed = true;
        }
    }
    if(changed){
        render();
        if(smartHistoryModal?.classList.contains('open')) scheduleSmartHistoryWindowRender(true);
    }
    return changed;
}
function normalizedSizeLabel(value){
    const parsed = parseSizeValue(value);
    const w = Number(parsed?.width || 0);
    const h = Number(parsed?.height || 0);
    return w > 0 && h > 0 ? `${Math.round(w)} x ${Math.round(h)}` : '';
}
function normalizedResolutionTier(value){
    const match = String(value || '').trim().toLowerCase().match(/^([124])\s*k$/);
    return match ? `${match[1]}K` : '';
}
async function smartHistoryImage2kPngBlob(item){
    const sourceUrl = displayMediaUrl(item);
    if(!sourceUrl) throw new Error(tr('smart.imageUrlUnavailable'));
    const response = await fetch(sourceUrl, {credentials:'same-origin'});
    if(!response.ok) throw new Error(trf('smart.imageReadFailed', {code: response.status}));
    const sourceBlob = await response.blob();
    const bitmap = await createImageBitmap(sourceBlob);
    try {
        const longest = Math.max(bitmap.width, bitmap.height, 1);
        const scale = Math.min(1, 2048 / longest);
        const canvas2k = document.createElement('canvas');
        canvas2k.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas2k.height = Math.max(1, Math.round(bitmap.height * scale));
        const context = canvas2k.getContext('2d', {alpha:false});
        if(!context) throw new Error(tr('smart.cannotCreatePair'));
        context.fillStyle = '#ffffff';
        context.fillRect(0, 0, canvas2k.width, canvas2k.height);
        context.drawImage(bitmap, 0, 0, canvas2k.width, canvas2k.height);
        return await new Promise((resolve, reject) => canvas2k.toBlob(
            blob => blob ? resolve(blob) : reject(new Error(tr('smart.pairExportFailed'))),
            'image/png'
        ));
    } finally {
        bitmap.close?.();
    }
}
async function copySmartHistoryImage2k(item){
    if(!item?.url) throw new Error(tr('smart.imageUrlUnavailable'));
    if(typeof ClipboardItem !== 'function' || !navigator.clipboard?.write){
        throw new Error(tr('smart.copyImageUnsupported'));
    }
    const blob = await smartHistoryImage2kPngBlob(item);
    await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);
    return {width:'≤2048', mime:'image/png'};
}
function openSmartImageDetail(nodeId, imageIndex=0){
    const node = canvasState.nodes.find(n => n.id === nodeId);
    const source = node?.images?.[imageIndex];
    const item = imageForDisplay(source) || source;
    if(!item?.url) return;
    const kind = mediaKindForItem(item);
    if(kind !== 'image'){
        window.open(displayMediaUrl({url:item.url}), '_blank');
        return;
    }
    const ctx = smartHistoryContextForImage(node, imageIndex);
    openSmartHistoryDetail({
        url:item.url,
        kind:'image',
        historyId:ctx?.historyId || '',
        outputIndex:ctx?.outputIndex ?? 0
    });
}
// 查找图片对应的生成历史条目（asset_id 优先，url 兜底），返回 {historyId, outputIndex} 或 null。
async function deleteCanvasLogEntry(historyId, deleteMedia=false){
    if(!canvas || !canvasId || !historyId) return;
    if(smartHistoryDeletingBusy) return;
    const confirmText = deleteMedia ? tr('canvas.deleteLogMediaConfirm') : tr('canvas.deleteLogConfirm');
    if(!confirm(confirmText)) return;
    smartHistoryDeletingBusy = true;
    const activeTaskLogs = smartActiveHistoryTaskLogs(canvas.history, historyId);
    try {
        if(debouncedSaveTimer){
            clearTimeout(debouncedSaveTimer);
            debouncedSaveTimer = null;
            await saveCanvas();
        }
        const projectHistory = canvas.format === 'firefly-canvas-v1';
        const endpoint = projectHistory
            ? (deleteMedia
                ? `/api/project-library/${encodeURIComponent(canvas.project_id || canvas.project || canvasId)}/history/delete-with-media`
                : `/api/project-library/${encodeURIComponent(canvas.project_id || canvas.project || canvasId)}/history/delete`)
            : `/api/canvases/${encodeURIComponent(canvasId)}/history/delete`;
        const payload = projectHistory ? {
            history_id:historyId,
            base_revision:Number(canvas.revision || 0),
            delete_media:Boolean(deleteMedia)
        } : {
            history_id:historyId,
            delete_unreferenced_media:deleteMedia,
            reset_referencing_nodes:deleteMedia,
            base_updated_at:Number(canvas.updated_at || 0)
        };
        // #76 (C-3)：统一 helper——409 自动 rebase 到最新画布
        const result = await projectCanvasRequest(endpoint, {method:'POST', payload});
        if(!result.ok){
            // #68: 409 = 画布版本冲突（revision 已被其他操作推进）——rebase 后停止本次删除，
            // 不让过期状态继续连锁冲突。
            if(result.canvasSynced){
                toast(result.message || tr('canvas.historyDeleteConflict'));
                return;
            }
            throw new Error(result.message || tr('canvas.historyDeleteFailed'));
        }
        const data = result.data;
        if(projectHistory && data.canvas){
            // 重型水合：合并活动任务日志 + materialize + 刷新 persisted 快照（与删原图流一致）
            await adoptServerCanvas(data.canvas, {activeTaskLogs});
        } else {
            // 遗留 canvas 路径：本地合并删除结果
            canvas.history = mergeSmartActiveHistoryTaskLogs(
                data.canvas?.history || (canvas.history || []).filter(item => item.id !== historyId),
                activeTaskLogs,
            );
            if(data.canvas?.nodes){
                canvas.nodes = data.canvas.nodes;
                canvas.connections = data.canvas.connections || [];
                canvasState.nodes = canvas.nodes;
                render();
            }
            canvas.updated_at = Number(data.canvas?.updated_at || canvas.updated_at || Date.now());
            canvas.revision = Number(data.canvas?.revision ?? canvas.revision ?? 0);
        }
        renderSmartCanvasHistory();
        if(projectHistory && deleteMedia) refreshSmartProjectMediaTrashButton();
        const notes = [deleteMedia ? tr('canvas.deleteLogAndMedia') : tr('canvas.historyDeleted')];
        if(data.removed_files?.length) notes.push(tr('canvas.historyMediaRemoved').replace('{n}', data.removed_files.length));
        if(data.reset_node_ids?.length) notes.push(tr('canvas.historyNodesReset').replace('{n}', data.reset_node_ids.length));
        if(data.skipped_referenced?.length) notes.push(tr('canvas.historyMediaReferenced').replace('{n}', data.skipped_referenced.length));
        toast(notes.join(' · '));
    } catch(err) {
        toast(err?.message || tr('canvas.historyDeleteFailed'));
    } finally {
        smartHistoryDeletingBusy = false;
    }
}
function smartOutputAtPreservedPosition(outputs, preservedOutputId, fallbackPosition){
    const preserved = outputs.find(output => String(output.output_id || '') === String(preservedOutputId || ''));
    if(preserved) return preserved;
    if(!outputs.length) return null;
    return outputs[Math.max(0, Math.min(outputs.length - 1, Number(fallbackPosition) || 0))];
}
async function settleProjectCanvasBeforeOriginalMutation(){
    if(!canvas || canvas.format !== 'firefly-canvas-v1') return true;
    clearTimeout(debouncedSaveTimer);
    debouncedSaveTimer = null;
    while(canvasSyncInFlight) await new Promise(resolve => setTimeout(resolve, 50));
    return await saveProjectCanvasUntilSettled();
}
async function deleteSmartProjectOriginal(historyId, outputIndex, options = {}){
    if(!canvas || canvas.format !== 'firefly-canvas-v1') return;
    const log = smartHistoryEntryById(historyId);
    // Bug fix: 实时查找 output_id 在 log.outputs 中的当前位置，而不是使用缓存的 outputIndex
    const allOutputs = smartHistoryImageOutputs(log);
    const item = allOutputs.find(output => output.outputIndex === Number(outputIndex));
    const assetId = item?.asset_id || '';
    const outputId = String(item?.output_id || '');
    if(!assetId || !outputId){
        toast(tr('canvas.originalDeleteUnavailable'));
        return;
    }
    if(!options.skipConfirm && !confirm(tr('canvas.deleteOriginalConfirm'))) return;
    // Bug fix: 实时查找当前 outputIndex（基于 output_id），避免从生图节点再次生图后 outputIndex 失效
    const currentIndex = allOutputs.find(o => String(o.output_id || '') === outputId)?.outputIndex ?? Number(outputIndex);
    const deletionUiState = captureSmartOriginalDeletionUiState(historyId, outputId, currentIndex);
    const activeTaskLogs = smartActiveHistoryTaskLogs(canvas.history);
    try {
        if(await settleProjectCanvasBeforeOriginalMutation() !== true){
            throw new Error(tr('smart.projectSaveFailed'));
        }
        const projectId = canvas.project_id || canvas.project || canvasId;
        const deletePayload = () => JSON.stringify({
            asset_id:assetId,
            base_revision:Number(canvas.revision || 0),
            history_id:historyId,
            output_id:outputId,
            output_index:Number(currentIndex),
        });
        // #76 (C-3)：统一 helper——409 时 adoptServerCanvas rebase 到最新画布并自动重试 ×3
        // （payload 闭包每次重算 base_revision；100ms 等待 revision 稳定）
        const result = await projectCanvasRequest(`/api/project-library/${encodeURIComponent(projectId)}/original/delete`, {
            method:'POST',
            payload:deletePayload,
            retries:3,
            settleMs:100,
        });
        if(!result.ok) throw new Error(result.message || tr('canvas.originalDeleteFailed'));
        const data = result.data;
        const deletionId = String(data.deleted?.deletion_id || '');
        if(!deletionId) throw new Error(tr('canvas.originalDeleteFailed'));
        pushUndo({projectOriginalDelete:{assetId, outputId, deletionId, historyId, projectId}});
        await adoptServerCanvas(data.canvas || canvas, {activeTaskLogs});
        reconcileSmartOriginalDeletionUi(historyId, outputId, deletionUiState);
        render();
        renderSmartCanvasHistory();
        refreshSmartProjectMediaTrashButton();
        toast(tr('canvas.originalDeleted'));
    } catch(err){
        toast(err?.message || tr('canvas.originalDeleteFailed'));
    }
}
async function restoreSmartProjectOriginalUndo(snapshot){
    if(!canvas || canvas.format !== 'firefly-canvas-v1' || !snapshot?.projectOriginalDelete?.assetId){
        throw new Error(tr('canvas.originalDeleteFailed'));
    }
    const restoreAssetId = String(snapshot.projectOriginalDelete.assetId || '');
    const restoreDeletionId = String(snapshot.projectOriginalDelete.deletionId || '');
    if(await settleProjectCanvasBeforeOriginalMutation() !== true){
        throw new Error(tr('smart.projectSaveFailed'));
    }
    const restoreHistoryId = String(snapshot.projectOriginalDelete.historyId || '')
        || smartHistoryIdForDeletedOutput(restoreAssetId, restoreDeletionId);
    const restoreUiState = restoreHistoryId
        ? captureSmartOriginalDeletionUiState(restoreHistoryId, '')
        : null;
    const projectId = snapshot.projectOriginalDelete.projectId || canvas.project_id || canvas.project || canvasId;
    canvas.nodes = canvasState.nodes;
    const localCanvasBeforeRestore = canvasForStorage();
    const localBaseline = persistedCoreSnapshot || projectCanvasSnapshot(localCanvasBeforeRestore);
    const localChanges = buildCoreMutations(localCanvasBeforeRestore, localBaseline);
    const activeTaskLogs = smartActiveHistoryTaskLogs(canvas.history);
    const res = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/original/restore`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
            asset_id:snapshot.projectOriginalDelete.assetId,
            base_revision:Number(canvas.revision || 0),
            deletion_id:snapshot.projectOriginalDelete.deletionId || null,
        })
    });
    const data = await res.json().catch(() => ({}));
    if(!res.ok){
        const error = new Error(data.detail || tr('canvas.originalDeleteFailed'));
        error.permanentUndoFailure = res.status === 404 || res.status === 410;
        throw error;
    }
    let restoredCanvas = data.canvas || canvas;
    if(localChanges.mutations.length){
        const rebased = projectCanvasMutationClient().rebaseProjectCanvasMutations(
            restoredCanvas,
            localBaseline,
            localChanges.mutations,
        );
        restoredCanvas = rebased.canvas;
    }
    canvas = restoredCanvas;
    canvas = hydrateProjectMediaReferences(canvas);
    canvas.history = mergeSmartActiveHistoryTaskLogs(canvas.history, activeTaskLogs);
    canvas.connections = Array.isArray(canvas.connections) ? canvas.connections : [];
    canvasState.nodes = (canvas.nodes || []).map(hydrateProjectMediaReferences).map(normalizeLegacySmartNode).filter(Boolean);
    canvas.nodes = canvasState.nodes;
    rebuildMarkedAssetIdsFromNodes();
    updateSmartMarkedBadge(); // 刷新后角标立即可见（badge 不再等面板打开才同步）
    canvas.updated_at = Number(canvas.updated_at || Date.now());
    canvas.revision = Number(canvas.revision ?? 0);
    if(restoreUiState) reconcileSmartOriginalDeletionUi(restoreHistoryId, '', restoreUiState);
    render();
    if(localChanges.mutations.length){
        if(await saveProjectCanvasUntilSettled() !== true) throw new Error(tr('smart.projectSaveFailed'));
    } else {
        const savedCanvas = await materializeProjectMediaReferences(canvasForStorage());
        persistedCoreSnapshot = projectCanvasSnapshot(savedCanvas);
        persistedDurableSnapshot = durableSnapshotForCanvas(savedCanvas);
        markAutosaveSaved();
    }
    renderSmartCanvasHistory();
    refreshSmartProjectMediaTrashButton();
}
async function restoreSmartProjectOriginal(assetId, deletionId='', outputId='', historyId=''){
    const normalizedAssetId = String(assetId || '').trim();
    if(!normalizedAssetId) return;
    const projectId = canvas?.project_id || canvas?.project || canvasId;
    const token = {
        assetId:normalizedAssetId,
        deletionId:String(deletionId || ''),
        outputId:String(outputId || ''),
        historyId:String(historyId || ''),
        projectId,
    };
    try {
        await restoreSmartProjectOriginalUndo({projectOriginalDelete:token});
        for(let index = undoStack.length - 1; index >= 0; index--){
            const token = undoStack[index]?.projectOriginalDelete;
            if(
                token?.assetId === normalizedAssetId
                && (!deletionId || token.deletionId === deletionId)
                && (token.projectId || projectId) === projectId
            ){
                undoStack.splice(index, 1);
            }
        }
        syncUndoRedoButtons();
        toast(tr('canvas.originalRestored'));
    } catch(err){
        toast(err?.message || tr('canvas.originalDeleteFailed'));
    }
}
async function refreshSmartProjectMediaTrashButton(){
    if(!smartHistoryPurgeTrash) return;
    const refreshVersion = ++smartProjectMediaTrashRefreshVersion;
    if(!canvas || canvas.format !== 'firefly-canvas-v1'){
        smartHistoryPurgeTrash.hidden = true;
        if(smartTrashOpenBtn) smartTrashOpenBtn.hidden = true;
        return;
    }
    try {
        const projectId = canvas.project_id || canvas.project || canvasId;
        const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/trash/media`);
        if(!response.ok) throw new Error('trash status unavailable');
        const data = await response.json().catch(() => ({}));
        if(refreshVersion !== smartProjectMediaTrashRefreshVersion || smartHistoryPurgeTrash.getAttribute('aria-busy') === 'true') return;
        const hasItems = Array.isArray(data.items) && data.items.length > 0;
        smartHistoryPurgeTrash.hidden = !hasItems;
        if(smartTrashOpenBtn) smartTrashOpenBtn.hidden = !hasItems;
    } catch(_) {
        if(refreshVersion !== smartProjectMediaTrashRefreshVersion || smartHistoryPurgeTrash.getAttribute('aria-busy') === 'true') return;
        smartHistoryPurgeTrash.hidden = true;
        if(smartTrashOpenBtn) smartTrashOpenBtn.hidden = true;
    }
}
async function purgeSmartProjectMediaTrash(){
    if(!canvas || canvas.format !== 'firefly-canvas-v1' || smartHistoryPurgeTrash?.getAttribute('aria-busy') === 'true' || !confirm(tr('canvas.clearProjectTrashConfirm'))) return;
    smartProjectMediaTrashRefreshVersion++;
    smartHistoryPurgeTrash.disabled = true;
    smartHistoryPurgeTrash.setAttribute('aria-busy', 'true');
    try {
        const projectId = canvas.project_id || canvas.project || canvasId;
        const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/trash/media`, {method:'DELETE'});
        const data = await response.json().catch(() => ({}));
        if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : tr('smart.trashPurgeFailed'));
        // #73: 清空后回收区条目全部消失，恢复目标不复存在——清掉本项目所有
        // projectOriginalDelete 令牌。此前只清 undoStack，redoStack 里的残留令牌
        // 重做后按「撤销」会报误导错误。
        const clearTokens = stack => {
            for(let index = stack.length - 1; index >= 0; index--){
                const token = stack[index]?.projectOriginalDelete;
                if(token && (token.projectId || projectId) === projectId) stack.splice(index, 1);
            }
        };
        clearTokens(undoStack);
        clearTokens(redoStack);
        syncUndoRedoButtons();
        smartHistoryPurgeTrash.hidden = true;
        toast(tr('canvas.projectTrashCleared'));
        // #73: 面板打开时 await 重开（列表已空），不再 400ms 盲等显示旧列表。
        if(smartTrashBackdrop && !smartTrashBackdrop.hidden) await openSmartTrashPanel();
    } catch(err){
        // #69: 失败文案匹配「清空回收区」语义，而非「删原图失败」
        toast(err?.message || tr('smart.trashPurgeFailed'));
    } finally {
        smartHistoryPurgeTrash.disabled = false;
        smartHistoryPurgeTrash.setAttribute('aria-busy', 'false');
    }
}
async function controlProjectImageTask(historyId, taskId, action, button=null){
    if(!taskId || !['cancel', 'retry'].includes(action)) return;
    const projectId = canvas?.project_id || canvas?.project || canvasId;
    const sourceLog = smartHistoryEntryById(historyId);
    if(button){
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
    }
    try {
        const response = await fetch(`${projectImageTaskUrl(taskId)}/${action}`, {method:'POST'});
        const data = await response.json().catch(() => ({}));
        if(!response.ok) throw new Error(apiErrorMessage(data, action === 'cancel' ? tr('smart.cancelTaskFailed') : tr('smart.retryTaskFailed')));
        if(action === 'cancel'){
            absorbProjectGenerationResult(data.result || {}, taskId);
            toast(tr('smart.taskCancelled'));
            render();
            return;
        }
        const retryTaskId = String(data.task_id || '');
        if(!retryTaskId) throw new Error(tr('smart.retryMissingTaskId'));
        const taskResult = {
            taskIds:[retryTaskId],
            count:Number(data.registered_node?.pendingTasks?.find(item => item?.taskId === retryTaskId)?.count || 1),
            providerId:data.route?.provider_id || '',
            model:data.route?.model || '',
            route:data.route || {},
            projectRevision:data.project_revision,
            registeredNode:data.registered_node,
            idempotencyKey:data.idempotency_key || '',
            retry_of_task_id:data.retry_of_task_id || taskId
        };
        await acceptProjectGenerationRegistration(taskResult);
        const nodeId = data.registered_node?.id || sourceLog?.result_node_id || sourceLog?.nodeId || '';
        const resultNode = canvasState.nodes.find(node => String(node?.id || '') === String(nodeId));
        if(!resultNode) throw new Error(tr('smart.retryMissingResultNode'));
        const retryTask = smartPendingTasks(resultNode).find(item => item.taskId === retryTaskId);
        if(retryTask){
            retryTask.prompt = sourceLog?.prompt || retryTask.prompt || '';
            retryTask.createdAt = Date.now();
            retryTask.idempotencyKey = data.idempotency_key || retryTask.idempotencyKey || '';
            retryTask.retryOfTaskId = data.retry_of_task_id || taskId;
        }
        ensureSmartPendingHistoryLogs(resultNode, smartPendingTasks(resultNode), {
            kind:'image',
            providerId:taskResult.providerId,
            providerName:taskResult.route?.provider_name || '',
            model:taskResult.model,
            prompt:sourceLog?.prompt || ''
        });
        render();
        toast(tr('smart.taskRequeued'));
        void resumeSmartPendingNode(resultNode, {taskIds:[retryTaskId]}).catch(() => {});
    } catch(error){
        toast((error?.message || (action === 'cancel' ? tr('smart.cancelTaskFailed') : tr('smart.retryTaskFailed'))).slice(0, 160));
    } finally {
        if(button?.isConnected){
            button.disabled = false;
            button.removeAttribute('aria-busy');
        }
    }
}
function openSmartCanvasLog(){
    if(!canvas) return;
    if(assetLibraryOpen) toggleAssetLibrary(false);
    renderSmartCanvasHistory();
    smartHistoryModal.classList.add('open');
    updateSmartHistoryDrawerCollapseUi();
    refreshSmartProjectMediaTrashButton();
}
function closeSmartCanvasLog(){
    smartHistoryModal?.classList.remove('open');
    updateSmartHistoryDrawerCollapseUi();
}
function openSmartSettings(){
    if(!smartSettingsModal) return;
    smartSettingsLastFocus = document.activeElement;
    if(smartSettingsFrame && !smartSettingsFrame.getAttribute('src')){
        smartSettingsFrame.setAttribute('src', '/static/global-settings.html?embedded=1');
    }
    smartSettingsModal.classList.add('open');
    smartSettingsModal.setAttribute('aria-hidden', 'false');
    smartSettingsLink?.setAttribute('aria-expanded', 'true');
    closeCreateMenu();
    requestAnimationFrame(() => smartSettingsClose?.focus());
}
function closeSmartSettings(){
    if(!smartSettingsModal) return;
    smartSettingsModal.classList.remove('open');
    smartSettingsModal.setAttribute('aria-hidden', 'true');
    smartSettingsLink?.setAttribute('aria-expanded', 'false');
    if(smartSettingsLastFocus && typeof smartSettingsLastFocus.focus === 'function') smartSettingsLastFocus.focus();
}
function setupSmartSettingsOverlay(){
    smartSettingsLink?.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        if(smartSettingsModal?.classList.contains('open')) closeSmartSettings();
        else openSmartSettings();
    });
    smartSettingsLink?.addEventListener('pointerdown', event => event.stopPropagation());
    smartSettingsLink?.addEventListener('mousedown', event => event.stopPropagation());
    smartSettingsClose?.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        closeSmartSettings();
    });
    smartSettingsModal?.addEventListener('click', event => {
        if(event.target === smartSettingsModal) closeSmartSettings();
        event.stopPropagation();
    });
    smartSettingsModal?.addEventListener('pointerdown', event => event.stopPropagation());
    smartSettingsModal?.addEventListener('mousedown', event => event.stopPropagation());
    smartSettingsModal?.addEventListener('wheel', event => event.stopPropagation(), {passive:true});
}
function positionSmartCanvasShortcuts(){
    if(!smartShortcutModal?.classList.contains('open') || !shell) return;
    smartShortcutModal.style.removeProperty('top');
    const panel = smartShortcutModal.getBoundingClientRect();
    const toolbar = document.querySelector('.smart-toolbar')?.getBoundingClientRect();
    const title = document.querySelector('.smart-title')?.getBoundingClientRect();
    const saveState = document.querySelector('.smart-save-state')?.getBoundingClientRect();
    const safeTop = Math.max(16, ...[toolbar, title, saveState].filter(Boolean).map(rect => rect.bottom + 10));
    const maxTop = Math.max(safeTop, shell.clientHeight - panel.height - 16);
    const obstacles = [...(world?.querySelectorAll?.('.image-node') || []), composer]
        .filter(Boolean)
        .map(element => ({rect:element.getBoundingClientRect(), style:getComputedStyle(element)}))
        .filter(item => item.style.display !== 'none' && item.style.visibility !== 'hidden')
        .map(item => item.rect)
        .filter(rect => rect.right > 0 && rect.bottom > 0 && rect.left < shell.clientWidth && rect.top < shell.clientHeight);
    const overlapArea = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
        * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
    const candidates = Array.from({length:7}, (_, index) => safeTop + (maxTop - safeTop) * index / 6);
    const bestTop = candidates
        .map(top => {
            const candidate = {left:panel.left, right:panel.right, top, bottom:top + panel.height};
            const overlap = obstacles.reduce((total, rect) => total + overlapArea(candidate, rect), 0);
            return {top, score:overlap + (top - safeTop) * .01};
        })
        .sort((a, b) => a.score - b.score)[0]?.top ?? safeTop;
    smartShortcutModal.style.top = `${Math.round(bestTop)}px`;
}
function openSmartCanvasShortcuts(){
    smartShortcutModal?.classList.add('open');
    shell?.classList.add('shortcut-panel-open');
    refreshIcons();
    requestAnimationFrame(positionSmartCanvasShortcuts);
}
function closeSmartCanvasShortcuts(){
    smartShortcutModal?.classList.remove('open');
    shell?.classList.remove('shortcut-panel-open');
    smartShortcutModal?.style.removeProperty('top');
}
function conversationConnectedReferences(node){
    if(!node || !canvas) return [];
    const nodesById = new Map(canvasState.nodes.map(item => [String(item.id), item]));
    const seen = new Set();
    const references = [];
    (canvas.connections || []).forEach(connection => {
        const target = connection?.to ?? connection?.target ?? connection?.toNode ?? connection?.targetNodeId;
        const sourceId = connection?.from ?? connection?.source ?? connection?.fromNode ?? connection?.sourceNodeId;
        if(String(target || '') !== String(node.id) || !sourceId || !['input', 'flow'].includes(connection?.kind || 'flow')) return;
        const source = nodesById.get(String(sourceId));
        if(!source || !['', 'image', 'smart-image'].includes(String(source.type || ''))) return;
        (source?.images || []).forEach(item => {
            const assetId = String(item?.asset_id || '').trim();
            const kind = mediaKindForItem(item);
            if(!assetId || !['image', 'video', 'audio'].includes(kind) || seen.has(assetId)) return;
            seen.add(assetId);
            references.push({asset_id:assetId, kind, name:String(item.name || assetId).slice(0, 160)});
        });
    });
    return references;
}
function conversationReferenceHtml(reference){
    const icon = reference.kind === 'video' ? 'film' : reference.kind === 'audio' ? 'file-audio' : 'image';
    return `<span class="conversation-node-reference" data-reference-kind="${escapeHtml(reference.kind)}" title="${escapeHtml(reference.name)}"><i data-lucide="${icon}"></i><span>${escapeHtml(reference.name)}</span></span>`;
}
function conversationNodeBodyHtml(node){
    const messages = (Array.isArray(node.messages) ? node.messages : []).filter(item => item && typeof item === 'object');
    const hasPendingMessage = messages.some(message => message.role === 'user' && message.status === 'pending');
    const collapsed = node.conversation_collapsed === true;
    const panelId = `conversation-panel-${String(node.id || '').replace(/[^A-Za-z0-9_-]/g, '-')}`;
    const references = conversationConnectedReferences(node);
    const referenceHtml = references.length
        ? `<div class="conversation-node-references" aria-label="${escapeHtml(tr('smart.directReferences'))}">${references.map(conversationReferenceHtml).join('')}</div>`
        : '';
    const messagesHtml = messages.length
        ? messages.map((message, index) => {
            const role = message.role === 'assistant' ? 'assistant' : 'user';
            const messageId = String(message.id || `${node.id}-${role}-${index}`);
            const status = String(message.status || 'completed');
            const statusLabel = status === 'pending' ? tr('smart.processing') : status === 'failed' ? tr('smart.failed') : '';
            const retryButton = status === 'failed' ? `<button type="button" class="conversation-node-message-retry" data-conversation-retry="${escapeHtml(messageId)}" title="${escapeHtml(tr('smart.resendMessage'))}">↻ ${escapeHtml(tr('smart.retry'))}</button>` : '';
            return `<article class="conversation-node-message" data-role="${role}" data-status="${escapeHtml(status)}" data-message-id="${escapeHtml(messageId)}">
                <div class="conversation-node-message-role">${role === 'assistant' ? tr('smart.assistant') : tr('smart.you')}</div>
                <div class="conversation-node-message-text" data-message-id="${escapeHtml(messageId)}" tabindex="0" aria-haspopup="menu" aria-label="${escapeHtml(tr('smart.conversationMessageAria'))}">${escapeHtml(message.text || message.content || '')}</div>
                ${statusLabel ? `<div class="conversation-node-message-status" data-status="${escapeHtml(status)}" role="status">${escapeHtml(statusLabel)}</div>` : ''}
                ${retryButton}
            </article>`;
        }).join('')
        : '<div class="conversation-node-empty">' + escapeHtml(tr('smart.conversationEmpty')) + '</div>';
    return `<div class="conversation-node-card ${collapsed ? 'collapsed' : ''}">
        <div class="conversation-node-heading">
            <div class="conversation-node-subtitle">${escapeHtml(tr('smart.conversationSubtitle'))}</div>
            <button type="button" class="conversation-node-collapse conversation-node-control" data-conversation-collapse aria-expanded="${collapsed ? 'false' : 'true'}" aria-controls="${escapeHtml(panelId)}" aria-label="${escapeHtml(collapsed ? tr('smart.conversationExpand') : tr('smart.conversationCollapse'))}">${collapsed ? tr('smart.historyExpand') : tr('smart.historyCollapse')}</button>
        </div>
        <div class="conversation-node-panel" id="${escapeHtml(panelId)}" data-conversation-panel ${collapsed ? 'hidden' : ''}>
            ${referenceHtml}
            <div class="conversation-node-messages">${messagesHtml}</div>
            <div class="conversation-node-composer conversation-node-control">
                <textarea data-conversation-input rows="2" placeholder="${escapeHtml(tr('smart.conversationInputPlaceholder'))}"></textarea>
                <button type="button" data-conversation-send ${hasPendingMessage ? 'disabled aria-disabled="true"' : ''}>${escapeHtml(tr('smart.sendMessage'))}</button>
            </div>
        </div>
    </div>`;
}
// Agent TUI state management
const agentTuiState = new Map(); // nodeId -> { socket, messages, status, model, autonomy }

function getAgentTuiState(nodeId) {
    if (!agentTuiState.has(nodeId)) {
        agentTuiState.set(nodeId, {
            socket: null,
            messages: [],
            status: 'idle',
            model: '',
            autonomy: 'cautious',
            toolActivities: new Map(),
            thinkingVisible: false,
        });
    }
    return agentTuiState.get(nodeId);
}

function connectAgentWebSocket(nodeId, projectId) {
    const state = getAgentTuiState(nodeId);
    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        return state.socket;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/api/projects/${encodeURIComponent(projectId)}/agent-canvasState.nodes/${encodeURIComponent(nodeId)}/stream`;

    try {
        state.socket = new WebSocket(wsUrl);
    } catch (e) {
        console.error('Failed to connect agent WebSocket:', e);
        return null;
    }

    state.socket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleAgentEvent(nodeId, data);
        } catch (e) {
            console.error('Failed to parse agent event:', e);
        }
    };

    state.socket.onclose = () => {
        state.status = 'disconnected';
        state.socket = null;
        // Re-render to show disconnected state
        renderAgentNode(nodeId);
    };

    state.socket.onerror = () => {
        state.status = 'error';
    };

    state.status = 'connecting';
    return state.socket;
}

function handleAgentEvent(nodeId, event) {
    const state = getAgentTuiState(nodeId);
    const eventType = event.type;

    switch (eventType) {
        case 'session':
            state.status = 'idle';
            break;
        case 'agent_start':
            state.status = 'busy';
            break;
        case 'agent_end':
            state.status = 'idle';
            break;
        case 'turn_start':
            state.status = 'busy';
            break;
        case 'turn_end': {
            state.status = 'idle';
            const msg = event.message || {};
            const content = msg.content || [];
            const text = content
                .filter(c => c && c.type === 'text')
                .map(c => c.text || '')
                .join('');
            if (text) {
                state.messages.push({ role: 'assistant', text });
            }
            break;
        }
        case 'message_update': {
            const ame = event.assistantMessageEvent || {};
            if (ame.type === 'text_delta') {
                const delta = ame.delta || '';
                // Append to last assistant message or create new
                const last = state.messages[state.messages.length - 1];
                if (last && last.role === 'assistant' && last.streaming) {
                    last.text += delta;
                } else {
                    state.messages.push({ role: 'assistant', text: delta, streaming: true });
                }
            } else if (ame.type === 'thinking_delta') {
                // Store thinking separately (hidden by default)
                if (!state.thinking) state.thinking = [];
                const delta = ame.delta || '';
                const last = state.thinking[state.thinking.length - 1];
                if (last && last.streaming) {
                    last.text += delta;
                } else {
                    state.thinking.push({ text: delta, streaming: true });
                }
            }
            break;
        }
        case 'tool_execution_start': {
            const toolName = event.toolName || '';
            const toolCallId = event.toolCallId || '';
            state.toolActivities.set(toolCallId, {
                name: toolName,
                status: 'running',
                args: event.args,
                result: null,
                isError: false,
            });
            state.status = 'busy';
            break;
        }
        case 'tool_execution_update': {
            const toolCallId = event.toolCallId || '';
            const activity = state.toolActivities.get(toolCallId);
            if (activity) {
                activity.update = event.update;
            }
            break;
        }
        case 'tool_execution_end': {
            const toolCallId = event.toolCallId || '';
            const activity = state.toolActivities.get(toolCallId);
            if (activity) {
                activity.status = event.isError ? 'error' : 'done';
                activity.result = event.result;
                activity.isError = event.isError;
            }
            break;
        }
        case 'auto_retry_start':
            state.status = 'retrying';
            break;
        case 'auto_retry_end':
            if (event.success) {
                state.status = 'busy';
            } else {
                state.status = 'error';
                state.messages.push({ role: 'system', text: 'Agent 自动重试失败' });
            }
            break;
        case 'process_exited':
            state.status = 'crashed';
            break;
        case 'confirm': {
            // Confirmation request from agent for destructive operation
            state.confirm = event;
            state.status = 'waiting_confirm';
            break;
        }
        case 'response':
            // Command response, ignore for now
            break;
        default:
            break;
    }

    // Re-render the agent node
    renderAgentNode(nodeId);
}

function sendAgentPrompt(nodeId, projectId, message, images = []) {
    const state = getAgentTuiState(nodeId);
    
    // Add user message to state
    state.messages.push({ role: 'user', text: message });
    state.status = 'busy';

    // Send via WebSocket if connected
    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(JSON.stringify({
            type: 'prompt',
            message: message,
            images: images,
        }));
    } else {
        // WebSocket not connected, use HTTP API
        fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-canvasState.nodes/${encodeURIComponent(nodeId)}/prompt`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, images }),
        }).catch(e => console.error('Failed to send agent prompt:', e));
    }

    renderAgentNode(nodeId);
}

function sendAgentCommand(nodeId, projectId, command, options = {}) {
    const state = getAgentTuiState(nodeId);
    const { body, wsPayload, onSuccess } = options;
    const url = `/api/projects/${encodeURIComponent(projectId)}/agent-canvasState.nodes/${encodeURIComponent(nodeId)}/${command}`;
    // WebSocket not connected, use HTTP API
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
        const fetchOpts = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
        if (body !== undefined) fetchOpts.body = JSON.stringify(body);
        fetch(url, fetchOpts).catch(e => console.error(`Failed to send agent ${command}:`, e));
        return;
    }
    state.socket.send(JSON.stringify(wsPayload));
    if (onSuccess) onSuccess(state);
    renderAgentNode(nodeId);
}

function sendAgentSteer(nodeId, projectId, message) {
    sendAgentCommand(nodeId, projectId, 'steer', {
        body: { message },
        wsPayload: { type: 'steer', message },
        onSuccess: (state) => state.messages.push({ role: 'system', text: `↪ steer: ${message}` }),
    });
}

function sendAgentFollowUp(nodeId, projectId, message) {
    sendAgentCommand(nodeId, projectId, 'follow_up', {
        body: { message },
        wsPayload: { type: 'follow_up', message },
        onSuccess: (state) => state.messages.push({ role: 'system', text: `⏳ follow_up queued: ${message}` }),
    });
}

function sendAgentAbort(nodeId, projectId) {
    sendAgentCommand(nodeId, projectId, 'abort', {
        wsPayload: { type: 'abort' },
        onSuccess: (state) => {
            state.status = 'idle';
            state.messages.push({ role: 'system', text: '⏹ aborted' });
        },
    });
}

function setAgentModel(nodeId, projectId, model) {
    const state = getAgentTuiState(nodeId);
    state.model = model;
    // Optimistically update the local node (source of truth for rendering)
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if (node) node.model = model;
    fetch(`/api/projects/${encodeURI(projectId)}/agent-canvasState.nodes/${encodeURI(nodeId)}/model`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: model }),
    }).catch(e => console.error('Failed to set agent model:', e));
    renderAgentNode(nodeId);
}

function setAgentAutonomy(nodeId, projectId, autonomy) {
    const state = getAgentTuiState(nodeId);
    state.autonomy = autonomy;
    // Optimistically update the local node (source of truth for rendering)
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if (node) node.autonomy = autonomy;
    fetch(`/api/projects/${encodeURI(projectId)}/agent-canvasState.nodes/${encodeURI(nodeId)}/autonomy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ autonomy: autonomy }),
    }).catch(e => console.error('Failed to set agent autonomy:', e));
    renderAgentNode(nodeId);
}

async function forkAgentSession(nodeId, projectId, entryId, name) {
    const state = getAgentTuiState(nodeId);
    const node = canvasState.nodes.find(n => n.id === nodeId);
    try {
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-canvasState.nodes/${encodeURI(nodeId)}/fork`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entry_id: entryId, name: name }),
        });
        if (!response.ok) throw new Error('Fork failed');
        const data = await response.json();
        // Update local node with new branches
        if (node) {
            node.branches = data.branches;
            node.current_branch = data.current_branch;
        }
        // Clear messages (new branch starts fresh from fork point)
        state.messages = [];
        state.toolActivities.clear();
        renderAgentNode(nodeId);
    } catch (e) {
        console.error('Failed to fork agent session:', e);
    }
}

async function switchAgentBranch(nodeId, projectId, branchId) {
    const state = getAgentTuiState(nodeId);
    const node = canvasState.nodes.find(n => n.id === nodeId);
    try {
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-canvasState.nodes/${encodeURI(nodeId)}/switch-branch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ branch_id: branchId }),
        });
        if (!response.ok) throw new Error('Switch branch failed');
        const data = await response.json();
        // Update local node
        if (node) {
            node.current_branch = data.current_branch;
        }
        // Clear messages (switching to a different branch)
        state.messages = [];
        state.toolActivities.clear();
        renderAgentNode(nodeId);
    } catch (e) {
        console.error('Failed to switch agent branch:', e);
    }
}

async function showForkPicker(nodeId, projectId) {
    const state = getAgentTuiState(nodeId);
    try {
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-canvasState.nodes/${encodeURI(nodeId)}/fork-messages`);
        if (!response.ok) throw new Error('Failed to get fork messages');
        const data = await response.json();
        const messages = data.messages || [];
        if (messages.length === 0) {
            // No forkable messages
            return;
        }
        // Show a simple prompt to select which message to fork from
        // (In a full implementation, this would be a UI picker)
        const messageTexts = messages.map((m, i) => `${i + 1}. ${m.text}`).join('\n');
        const choice = prompt(`从哪轮分叉？\n${messageTexts}\n\n输入序号：`);
        if (!choice) return;
        const index = parseInt(choice, 10) - 1;
        if (index < 0 || index >= messages.length) return;
        const message = messages[index];
        const branchName = prompt('分支名称：', message.text.slice(0, 30));
        await forkAgentSession(nodeId, projectId, message.entryId, branchName || '');
    } catch (e) {
        console.error('Failed to show fork picker:', e);
    }
}

function sendAgentConfirm(nodeId, projectId, approved) {
    const state = getAgentTuiState(nodeId);
    if (!state.confirm) return;
    const confirmId = state.confirm.confirm_id;
    state.confirm = null;
    state.status = 'busy';

    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(JSON.stringify({
            type: 'confirm',
            confirm_id: confirmId,
            approved: approved,
        }));
    } else {
        // WS not connected, send via HTTP (fire-and-forget)
        fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-canvasState.nodes/${encodeURIComponent(nodeId)}/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm_id: confirmId, approved: approved }),
        }).catch(e => console.error('Failed to send agent confirm:', e));
    }

    const text = approved ? '✅ 已批准' : '❌ 已取消';
    state.messages.push({ role: 'system', text });
    renderAgentNode(nodeId);
}

// Get status light + last line preview HTML for agent node card
function agentCardStatusHtml(nodeId) {
    const state = agentTuiState.get(nodeId);
    if (!state) {
        return '<span class="agent-card-status agent-tui-status-idle">空闲</span>';
    }
    
    // Determine status class and text
    let statusClass = 'agent-tui-status-idle';
    let statusText = '空闲';
    if (state.status === 'busy') {
        statusClass = 'agent-tui-status-busy';
        statusText = '运行中';
    } else if (state.status === 'connecting') {
        statusClass = 'agent-tui-status-connecting';
        statusText = '连接中';
    } else if (state.status === 'error') {
        statusClass = 'agent-tui-status-error';
        statusText = '错误';
    } else if (state.status === 'disconnected') {
        statusClass = 'agent-tui-status-disconnected';
        statusText = '已断开';
    }
    
    // Waiting for confirmation overrides other statuses
    if (state.confirm) {
        statusClass = 'agent-tui-status-busy';
        statusText = '等待确认';
    }
    
    // Get last message/activity for preview
    let lastLine = '';
    if (state.messages && state.messages.length > 0) {
        const lastMsg = state.messages[state.messages.length - 1];
        lastLine = lastMsg.text ? lastMsg.text.slice(0, 40) : '';
        if (lastMsg.text && lastMsg.text.length > 40) lastLine += '…';
    } else if (state.toolActivities && state.toolActivities.size > 0) {
        const lastActivity = [...state.toolActivities.values()].pop();
        if (lastActivity) {
            lastLine = `${lastActivity.name}(${lastActivity.status})`;
        }
    }
    
    const previewHtml = lastLine ? `<span class="agent-card-preview">${escapeHtml(lastLine)}</span>` : '';
    return `<span class="agent-card-status ${statusClass}">${statusText}</span>${previewHtml}`;
}

function agentNodeBodyHtml(node) {
    const nodeId = node.id;
    const state = getAgentTuiState(nodeId);
    const projectId = canvasId || '';

    // Determine status indicator
    let statusClass = 'agent-tui-status-idle';
    let statusText = '空闲';
    switch (state.status) {
        case 'busy':
            statusClass = 'agent-tui-status-busy';
            statusText = '运行中';
            break;
        case 'connecting':
            statusClass = 'agent-tui-status-connecting';
            statusText = '连接中';
            break;
        case 'retrying':
            statusClass = 'agent-tui-status-busy';
            statusText = '重试中';
            break;
        case 'error':
            statusClass = 'agent-tui-status-error';
            statusText = '错误';
            break;
        case 'crashed':
            statusClass = 'agent-tui-status-error';
            statusText = '已崩溃';
            break;
        case 'disconnected':
            statusClass = 'agent-tui-status-disconnected';
            statusText = '已断开';
            break;
    }

    // Render thinking (hidden by default, toggleable via 💭 button)
    let thinkingHtml = '';
    if (state.thinkingVisible && state.thinking && state.thinking.length > 0) {
        const thinkingText = state.thinking.map(t => t.text || '').join('');
        if (thinkingText) {
            thinkingHtml = `<div class="agent-tui-thinking"><span class="agent-tui-thinking-label">💭 thinking</span><div class="agent-tui-thinking-text">${escapeHtml(thinkingText)}</div></div>`;
        }
    }

    // Render messages
    let messagesHtml = '';
    for (const msg of state.messages) {
        if (msg.role === 'user') {
            messagesHtml += `<div class="agent-tui-msg agent-tui-msg-user"><span class="agent-tui-msg-role">&gt;</span> <span class="agent-tui-msg-text">${escapeHtml(msg.text)}</span></div>`;
        } else if (msg.role === 'assistant') {
            const streamingClass = msg.streaming ? ' agent-tui-msg-streaming' : '';
            messagesHtml += `<div class="agent-tui-msg agent-tui-msg-assistant${streamingClass}"><span class="agent-tui-msg-text">${renderMarkdown(msg.text)}</span></div>`;
        } else if (msg.role === 'system') {
            messagesHtml += `<div class="agent-tui-msg agent-tui-msg-system"><span class="agent-tui-msg-text">${escapeHtml(msg.text)}</span></div>`;
        }
    }

    // Render tool activities (collapsible)
    let toolsHtml = '';
    for (const [toolCallId, activity] of state.toolActivities) {
        let icon = '⚙';
        if (activity.status === 'done') icon = '✅';
        else if (activity.status === 'error') icon = '❌';
        else if (activity.status === 'running') icon = '⏳';

        const argsPreview = activity.args ? JSON.stringify(activity.args).slice(0, 80) : '';
        const argsFull = activity.args ? JSON.stringify(activity.args, null, 2) : '';
        const resultPreview = activity.result ? String(activity.result).slice(0, 120) : '';
        const expandedClass = activity.expanded ? ' agent-tui-tool-expanded' : '';
        toolsHtml += `<div class="agent-tui-tool${expandedClass}" data-tool-call-id="${escapeAttr(toolCallId)}">
            <div class="agent-tui-tool-header">
                <span class="agent-tui-tool-toggle">${activity.expanded ? '▾' : '▸'}</span>
                <span class="agent-tui-tool-icon">${icon}</span>
                <span class="agent-tui-tool-name">${escapeHtml(activity.name)}</span>
            </div>
            <div class="agent-tui-tool-details">
                ${argsFull ? `<div class="agent-tui-tool-args"><pre>${escapeHtml(argsFull)}</pre></div>` : ''}
                ${resultPreview ? `<div class="agent-tui-tool-result"><pre>${escapeHtml(resultPreview)}</pre></div>` : ''}
            </div>
        </div>`;
    }

    // Render confirmation card if waiting for user decision
    let confirmHtml = '';
    if (state.confirm) {
        const c = state.confirm;
        confirmHtml = `<div class="agent-tui-confirm" data-agent-confirm="${escapeAttr(nodeId)}">
            <div class="agent-tui-confirm-message">⚠️ ${escapeHtml(c.message || '请确认操作')}</div>
            <div class="agent-tui-confirm-buttons">
                <button type="button" class="agent-tui-confirm-btn agent-tui-confirm-approve" data-agent-confirm-approve="${escapeAttr(nodeId)}">批准</button>
                <button type="button" class="agent-tui-confirm-btn agent-tui-confirm-reject" data-agent-confirm-reject="${escapeAttr(nodeId)}">取消</button>
            </div>
        </div>`;
    }

    // Model select options — node is the source of truth for the persisted value
    const currentModel = node.model || '';
    let modelOptionsHtml = `<option value="" ${!currentModel ? 'selected' : ''}>默认</option>`;
    const chatModels = getChatModels();
    for (const model of chatModels) {
        const modelValue = model.id || model;
        const modelLabel = model.name || model.id || model;
        const selected = currentModel === modelValue ? 'selected' : '';
        modelOptionsHtml += `<option value="${escapeAttr(modelValue)}" ${selected}>${escapeHtml(modelLabel)}</option>`;
    }
    const modelSelectHtml = `<select class="agent-tui-model-select" data-agent-model="${escapeAttr(nodeId)}" title="模型">${modelOptionsHtml}</select>`;

    // Autonomy select options — node is the source of truth
    const currentAutonomy = node.autonomy || 'cautious';
    const autonomySelectHtml = `<select class="agent-tui-autonomy-select" data-agent-autonomy="${escapeAttr(nodeId)}" title="自治级别">
        <option value="cautious" ${currentAutonomy === 'cautious' ? 'selected' : ''}>谨慎</option>
        <option value="standard" ${currentAutonomy === 'standard' ? 'selected' : ''}>标准</option>
        <option value="autonomous" ${currentAutonomy === 'autonomous' ? 'selected' : ''}>自主</option>
    </select>`;

    // Branch select (if node has branches)
    const branches = node.branches || [];
    const currentBranchId = node.current_branch || '';
    let branchSelectHtml = '';
    if (branches.length > 0) {
        let branchOptionsHtml = '';
        for (const branch of branches) {
            const selected = branch.id === currentBranchId ? 'selected' : '';
            branchOptionsHtml += `<option value="${escapeAttr(branch.id)}" ${selected}>${escapeHtml(branch.name)}</option>`;
        }
        branchSelectHtml = `<select class="agent-tui-branch-select" data-agent-branch="${escapeAttr(nodeId)}" title="切换分支">${branchOptionsHtml}</select>`;
    }

    // Fork button
    const forkButtonHtml = `<button type="button" class="agent-tui-btn agent-tui-btn-fork" data-agent-fork="${escapeAttr(nodeId)}" title="从历史轮次分叉">🌿</button>`;

    return `<div class="agent-tui-card">
        <div class="agent-tui-header">
            <span class="agent-tui-title">${escapeHtml(node.title || 'Agent')}</span>
            <span class="agent-tui-status ${statusClass}">${statusText}</span>
            <span class="agent-tui-settings">
                ${modelSelectHtml}
                ${autonomySelectHtml}
                ${branchSelectHtml}
                ${forkButtonHtml}
            </span>
        </div>
        <div class="agent-tui-body" data-agent-tui-body="${escapeAttr(nodeId)}">
            <div class="agent-tui-messages" data-agent-tui-messages="${escapeAttr(nodeId)}">
                ${thinkingHtml}
                ${messagesHtml || '<div class="agent-tui-empty">输入自然语言描述你的创作目标...</div>'}
                ${toolsHtml}
                ${confirmHtml}
            </div>
        </div>
        <div class="agent-tui-input-row">
            <span class="agent-tui-prompt">&gt;</span>
            <textarea class="agent-tui-input" data-agent-tui-input="${escapeAttr(nodeId)}" placeholder="输入指令... (Enter 发送 / Shift+Enter 换行，运行中 Enter 排队)" rows="1"></textarea>
            <div class="agent-tui-action-buttons">
                <button type="button" class="agent-tui-btn agent-tui-btn-thinking" data-agent-thinking="${escapeAttr(nodeId)}" title="显示/隐藏思考过程" ${state.thinkingVisible ? 'agent-tui-btn-active' : ''}>💭</button>
                <button type="button" class="agent-tui-btn agent-tui-btn-steer" data-agent-steer="${escapeAttr(nodeId)}" title="Steer (重定向当前回合)" ${state.status !== 'busy' ? 'disabled' : ''}>↪</button>
                <button type="button" class="agent-tui-btn agent-tui-btn-followup" data-agent-followup="${escapeAttr(nodeId)}" title="Follow-up (排队后续指令)" ${state.status !== 'busy' ? 'disabled' : ''}>⏳</button>
                <button type="button" class="agent-tui-btn agent-tui-btn-abort" data-agent-abort="${escapeAttr(nodeId)}" title="Abort (中止当前回合)" ${state.status !== 'busy' ? 'disabled' : ''}>⏹</button>
            </div>
        </div>
    </div>`;
}
function agentTaskStatusText(status){
    return status === 'queued' ? tr('smart.stQueued') : status === 'running' ? tr('smart.stRunning') : status === 'succeeded' ? tr('smart.stSucceeded') : status === 'failed' ? tr('smart.failed') : status === 'uncertain' ? tr('smart.stUncertain') : status || tr('smart.agentStatusPlanning');
}
function agentTaskNodeBodyHtml(node){
    const taskId = String(node?.task_id || node?.id || '');
    const status = String(node?.status || 'planned');
    const kind = node?.kind === 'video' ? tr('smart.agentKindVideo') : node?.kind === 'prompt' ? tr('smart.agentKindPrompt') : tr('smart.agentKindImage');
    const params = node?.params && typeof node.params === 'object' ? node.params : {};
    const paramsText = JSON.stringify(params, null, 0);
    const error = String(node?.error || '').trim();
    const resultNodeId = String(node?.result_node_id || '');
    const resultLink = resultNodeId
        ? `<a class="agent-task-result-link" data-agent-result-link="${escapeAttr(resultNodeId)}" href="#${escapeAttr(resultNodeId)}">${escapeHtml(tr('smart.viewResultNode'))}</a>`
        : '<span class="agent-task-result-link muted">' + escapeHtml(tr('smart.waitingResultNode')) + '</span>';
    return `<div class="agent-task-card agent-task-card-readonly" data-agent-task-card="${escapeAttr(taskId)}">
        <div class="agent-task-heading">
            <div><div class="agent-task-title">${escapeHtml(trf('smart.agentTaskTitle', {kind: kind}))}</div><div class="agent-task-id">${escapeHtml(taskId)}</div></div>
            <span class="agent-task-status" data-agent-task-status="${escapeAttr(taskId)}" data-status="${escapeAttr(status)}" role="status">${escapeHtml(agentTaskStatusText(status))}</span>
        </div>
        <div class="agent-task-meta"><span>${escapeHtml(trf('smart.agentTaskModelCount', {m: node?.model_type || 'online', c: Math.max(1, Number(node?.count || 1))}))}</span></div>
        <div class="agent-task-prompt">${escapeHtml(node?.prompt || '')}</div>
        <div class="agent-task-params" title="${escapeHtml(tr('smart.taskParams'))}">${escapeHtml(trf('smart.agentTaskParams', {p: paramsText}))}</div>
        ${error ? `<div class="agent-task-error" data-agent-task-error role="alert">${escapeHtml(error)}</div>` : ''}
        <div class="agent-task-result">${resultLink}</div>
    </div>`;
}
function promptNodeBodyHtml(node){
    node.llmProvider = resolveChatProviderId(node.llmProvider || '');
    node.llmModel = resolveChatModel(node.llmModel || '', node.llmProvider);
    node.llmSystemEnabled = node.llmSystemEnabled === true;
    node.promptSplitEnabled = node.promptSplitEnabled === true;
    node.promptSeparator = promptNodeSeparator(node);
    const systemPrompt = (node.llmSystemPrompt || '').trim();
    const inputThumbs = smartNodeInputThumbsHtml(promptNodeInputImages(node));
    const templateActive = activePromptTemplateNodeId() === node.id;
    const promptItems = promptNodePromptItems(node);
    const promptSplitPreviewH = promptNodeSplitPreviewHeight(node);
    const upstreamPromptItems = promptNodeUpstreamPromptItems(node);
    const upstreamPromptHtml = upstreamPromptItems.length ? `<div class="prompt-node-upstream">
        <div class="prompt-node-section-title">${escapeHtml(tr('smart.upstreamInput'))}</div>
        <div class="prompt-node-upstream-list">${upstreamPromptItems.map((item, index) => `<div class="prompt-node-segment"><span>${index + 1}</span><p>${escapeHtml(item)}</p></div>`).join('')}</div>
    </div>` : '';
    const llmParams = node.llmEnabled ? `
        <div class="prompt-node-llm">
            <select class="prompt-node-control prompt-llm-provider">${chatProviderOptions(node.llmProvider)}</select>
            <select class="prompt-node-control prompt-llm-model">${chatModelOptions(node.llmModel, node.llmProvider)}</select>
            <div class="prompt-llm-instruction-wrap">
                <textarea class="prompt-node-control prompt-llm-instruction" placeholder="${escapeHtml(tr('smart.promptLlmInstructionPlaceholder'))}" style="height:${promptLlmInstructionHeight(node)}px">${escapeHtml(node.llmInstruction || '')}</textarea>
                <div class="prompt-llm-instruction-resize prompt-node-control" data-llm-instruction-resize="1" title="${escapeHtml(tr('smart.dragResizeHeight'))}"><span></span></div>
            </div>
            ${upstreamPromptHtml}
            <div class="prompt-node-llm-actions">
                <button class="prompt-node-run prompt-node-control" type="button" ${node.running ? 'disabled' : ''}><i data-lucide="${node.running ? 'loader-2' : 'play'}"></i><span>${node.running ? escapeHtml(tr('common.running')) : escapeHtml(tr('common.run'))}</span></button>
                <button class="prompt-node-pill prompt-node-control prompt-system-toggle ${node.llmSystemEnabled ? 'active' : ''}" type="button"><i data-lucide="${node.llmSystemEnabled ? 'toggle-right' : 'toggle-left'}"></i><span>${escapeHtml(node.llmSystemEnabled ? tr('smart.promptLlmDisableSystem') : tr('smart.promptLlmEnableSystem'))}</span></button>
            </div>
            ${node.llmSystemEnabled ? `<textarea class="prompt-node-control prompt-llm-system" placeholder="${escapeHtml(tr('smart.promptLlmSystemPlaceholder'))}">${escapeHtml(systemPrompt || 'You are a helpful prompt assistant.')}</textarea>` : ''}
        </div>` : '';
    return `<div class="prompt-node-card">
        <div class="prompt-node-view-actions">
            <button class="prompt-node-flow-toggle prompt-node-control" type="button" aria-pressed="false" title="${escapeAttr(tr('smart.promptNodeFlowEnter'))}" aria-label="${escapeAttr(tr('smart.promptNodeFlowEnter'))}"><i data-lucide="maximize-2"></i><span>${escapeHtml(tr('smart.promptNodeFlowEnter'))}</span></button>
        </div>
        <textarea class="prompt-node-text prompt-node-control" placeholder="${escapeHtml(tr('smart.promptPlaceholderNode'))}">${escapeHtml(node.text || '')}</textarea>
        <div class="prompt-node-tools">
            <button class="prompt-node-pill prompt-node-control prompt-preset-edit ${templateActive ? 'active' : ''}" type="button"><i data-lucide="library"></i><span>${escapeHtml(tr('smart.promptTemplateLibrary'))}</span></button>
            <button class="prompt-node-pill prompt-node-control prompt-split-toggle ${node.promptSplitEnabled ? 'active' : ''}" type="button"><i data-lucide="split"></i><span>${escapeHtml(tr('smart.separator'))}</span></button>
            <button class="prompt-node-pill prompt-llm-toggle ${node.llmEnabled ? 'active' : ''}" type="button"><i data-lucide="sparkles"></i><span>LLM</span></button>
        </div>
        ${node.promptSplitEnabled ? `<div class="prompt-node-split-row">
            <label class="prompt-node-split-control prompt-node-control"><span>${escapeHtml(tr('smart.separator'))}</span><input class="prompt-node-separator" type="text" value="${escapeHtml(node.promptSeparator)}" maxlength="8" placeholder=";"></label>
            <span class="prompt-node-split-count">${escapeHtml(trf('smart.segmentCount', {n: promptItems.length || 0}))}</span>
        </div>
        <div class="prompt-node-segments" style="height:${promptSplitPreviewH}px">${promptItems.length ? promptItems.map((item, index) => `<div class="prompt-node-segment"><span>${index + 1}</span><p>${escapeHtml(item)}</p></div>`).join('') : ''}</div>
        <div class="prompt-split-preview-resize prompt-node-control" data-prompt-split-resize="1" title="${escapeHtml(tr('smart.dragResizeHeight'))}"><span></span></div>` : ''}
        ${inputThumbs}
        ${llmParams}
    </div>`;
}
function refreshPromptNodeSegmentsUi(el, node){
    const items = promptNodePromptItems(node);
    const count = el.querySelector('.prompt-node-split-count');
    if(count) count.textContent = trf('smart.segmentCount', {n: items.length || 0});
    const list = el.querySelector('.prompt-node-segments');
    if(list){
        list.innerHTML = items.length
            ? items.map((item, index) => `<div class="prompt-node-segment"><span>${index + 1}</span><p>${escapeHtml(item)}</p></div>`).join('')
            : '';
    }
}
function loopNumberControlHtml({label, value, key, min=1, max=100, quick=[1,2,3,4,5,6,8,10]}){
    const v = Math.max(min, Math.min(max, Number(value) || min));
    return `<div class="loop-number-control">
        <button class="loop-smart-control loop-number-trigger" type="button"><span>${escapeHtml(label)}</span><strong>${v}</strong></button>
        <div class="loop-number-popover">
            <div class="loop-number-grid">
                ${quick.map(n => `<button type="button" class="loop-smart-control loop-number-cell ${n === v ? 'active' : ''}" data-loop-number="${escapeHtml(key)}" data-loop-value="${n}">${n}</button>`).join('')}
            </div>
            <label class="loop-number-custom">
                <span>${escapeHtml(tr('common.custom'))}</span>
                <input class="loop-smart-control loop-number-input" type="number" min="${min}" max="${max}" step="1" data-loop-number-input="${escapeHtml(key)}" value="${v}">
            </label>
        </div>
    </div>`;
}
function smartLoopTokenLabel(token){
    if(token === '《计数》' || token === '[计数]') return tr('canvas.counterToken');
    return token;
}
function smartLoopTokenChipHtml(token){
    return `<span class="loop-smart-token-chip" contenteditable="false" data-token="${escapeHtml(token)}"><span>${escapeHtml(smartLoopTokenLabel(token))}</span><button type="button" aria-label="${escapeHtml(tr('common.delete'))}" title="${escapeHtml(tr('common.delete'))}">×</button></span>`;
}
function smartLoopVariableHtml(text){
    return String(text || '').split(/(《计数》|\[计数\])/g).map(part => {
        if(part === '《计数》' || part === '[计数]') return smartLoopTokenChipHtml('《计数》');
        return escapeHtml(part);
    }).join('');
}
function smartLoopEditorText(editor){
    const walk = node => {
        if(node.nodeType === Node.TEXT_NODE) return node.nodeValue || '';
        if(node.nodeType !== Node.ELEMENT_NODE) return '';
        if(node.classList?.contains('loop-smart-token-chip')) return node.dataset.token || '';
        if(node.tagName === 'BR') return '\n';
        return [...node.childNodes].map(walk).join('');
    };
    return [...(editor?.childNodes || [])].map(walk).join('').replace(/\u00a0/g, ' ');
}
function insertSmartLoopToken(editor, token){
    if(!editor) return;
    editor.focus();
    const chipWrap = document.createElement('span');
    chipWrap.innerHTML = smartLoopTokenChipHtml(token);
    const chip = chipWrap.firstElementChild;
    const spacer = document.createTextNode(' ');
    const sel = window.getSelection();
    if(sel && sel.rangeCount && editor.contains(sel.anchorNode)){
        const range = sel.getRangeAt(0);
        range.deleteContents();
        range.insertNode(spacer);
        range.insertNode(chip);
        range.setStartAfter(spacer);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
    } else {
        editor.appendChild(chip);
        editor.appendChild(spacer);
    }
}
function smartLoopBodyHtml(node){
    node.count = smartLoopCount(node);
    node.mode = node.mode === 'parallel' ? 'parallel' : 'serial';
    node.loopStart = Math.max(1, Number(node.loopStart) || 1);
    node.imageBatchSize = Math.max(1, Math.min(100, Number(node.imageBatchSize) || 1));
    node.showPrompt = Boolean(node.showPrompt);
    node.imageInput = Boolean(node.imageInput);
    const imageCount = smartLoopInputImages(node, {index:node.loopStart}).length;
    const loopThumbs = smartNodeInputThumbsHtml(smartLoopPreviewImages(node));
    const promptItems = smartLoopInputPromptItems(node);
    const promptFields = smartLoopPromptFieldValues(node);
    const visiblePromptFields = promptFields.length ? promptFields : [''];
    const promptHint = promptItems.length
        ? trf('smart.loopPromptHintFound', {n:promptItems.length})
        : tr('smart.loopPromptHintVariable');
    const currentUpstreamPrompt = smartLoopSelectedInputPrompt(node, {index:node.loopStart});
    const defaultPrompt = smartLoopDefaultPromptText();
    const loopRunState = smartCascadeRunForLoop(node.id);
    const loopRunning = Boolean(loopRunState);
    const loopStopping = Boolean(loopRunState?.stopRequested);
    return `<div class="loop-smart-card ${node.imageInput ? 'has-image' : ''} ${node.showPrompt ? 'has-prompt' : ''}">
        <div class="loop-smart-row loop-smart-top">
            <div class="loop-smart-seg">
                <button type="button" class="loop-smart-control ${node.mode !== 'parallel' ? 'active' : ''}" data-loop-mode="serial">${escapeHtml(tr('canvas.loopSerial'))}</button>
                <button type="button" class="loop-smart-control ${node.mode === 'parallel' ? 'active' : ''}" data-loop-mode="parallel" title="${escapeHtml(tr('smart.loopParallelTip'))}">${escapeHtml(tr('canvas.loopParallel'))}</button>
            </div>
        </div>
        <div class="loop-smart-row">
            <button class="loop-smart-control loop-smart-toggle ${node.imageInput ? 'active' : ''}" type="button" data-loop-toggle="image"><i data-lucide="image"></i><span>${escapeHtml(tr('canvas.loopImageToggle'))}</span></button>
            <button class="loop-smart-control loop-smart-toggle ${node.showPrompt ? 'active' : ''}" type="button" data-loop-toggle="prompt"><i data-lucide="text-cursor-input"></i><span>${escapeHtml(tr('canvas.loopPromptToggle'))}</span></button>
        </div>
        ${node.imageInput ? `<div class="loop-smart-panel">
            ${loopThumbs}
            <div class="loop-smart-mini">
                ${loopNumberControlHtml({label:tr('canvas.loopBatchSize'), value:node.imageBatchSize, key:'imageBatchSize', max:100, quick:[1,2,3,4,5,6,8,10]})}
            </div>
            <div class="loop-smart-note">${imageCount ? escapeHtml(trf('canvas.loopImageWillOutput', {n:imageCount})) : escapeHtml(tr('canvas.loopImageEmpty'))}</div>
        </div>` : ''}
        ${node.showPrompt ? `<div class="loop-smart-panel prompt-panel">
            ${currentUpstreamPrompt ? `<div class="loop-smart-upstream">
                <div class="loop-smart-upstream-label">${escapeHtml(promptHint)}</div>
                <div class="loop-smart-upstream-text">${escapeHtml(currentUpstreamPrompt)}</div>
            </div>` : ''}
            <div class="loop-smart-prompt-list">
                ${visiblePromptFields.map((value, index) => {
                    const displayValue = promptItems.length && isSmartLoopDefaultPrompt(value) ? '' : value;
                    return `<div class="loop-smart-prompt-item">
                    <div class="loop-smart-prompt-index">${index + 1}</div>
                    <div class="loop-smart-control loop-smart-text" contenteditable="true" data-loop-prompt-index="${index}" data-placeholder="${escapeHtml(tr('canvas.loopVariablePlaceholder'))}">${smartLoopVariableHtml(displayValue || (index === 0 && !promptFields.length && !promptItems.length ? defaultPrompt : ''))}</div>
                    <button class="loop-smart-control loop-smart-icon-btn" type="button" data-loop-prompt-delete="${index}" ${visiblePromptFields.length <= 1 ? 'disabled' : ''} title="${escapeHtml(tr('common.delete'))}" aria-label="${escapeHtml(tr('common.delete'))}">×</button>
                </div>`;
                }).join('')}
            </div>
            <div class="loop-smart-row loop-smart-prompt-actions">
                <button class="loop-smart-control loop-smart-token loop-smart-counter-token" type="button" data-loop-token="《计数》">${escapeHtml(tr('canvas.counterToken'))}</button>
                <span class="loop-smart-note">${escapeHtml(promptHint)}</span>
                <button class="loop-smart-control loop-smart-add-prompt" type="button" data-loop-prompt-add="1" title="${escapeHtml(tr('smart.addNew'))}" aria-label="${escapeHtml(tr('smart.addNew'))}"><i data-lucide="plus"></i></button>
            </div>
        </div>` : ''}
        <div class="loop-smart-footer">
            ${loopNumberControlHtml({label:tr('canvas.loopImageStart'), value:node.loopStart, key:'loopStart', max:9999, quick:[1,2,3,4,5,6,8,10]})}
            ${loopNumberControlHtml({label:tr('canvas.loopCount'), value:node.count, key:'count', max:100, quick:[1,2,3,4,5,6,8,10]})}
            <button class="loop-smart-control loop-smart-run ${loopRunning ? 'is-stop' : ''}" type="button" data-loop-run="${escapeHtml(node.id)}" ${loopStopping ? 'disabled' : ''}><i data-lucide="${loopRunning ? 'square' : 'workflow'}"></i><span>${escapeHtml(loopRunning ? smartCascadeStopText(loopStopping) : tr('smart.loopRunAll'))}</span></button>
        </div>
    </div>`;
}
function smartGroupBodyHtml(node){
    const groupThumbLayout = smartGroupThumbLayout(node);
    const refThumbs = groupThumbLayout?.refs || [];
    const members = smartGroupMembers(node);
    const counts = members.reduce((acc, member) => {
        if(member.type === 'smart-prompt') acc.prompt += 1;
        else if(member.type === 'smart-loop') acc.loop += 1;
        return acc;
    }, {prompt:0, media:refThumbs.length, loop:0});
    const summary = [
        counts.prompt ? trf('smart.promptCount', {n: counts.prompt}) : '',
        counts.media ? trf('smart.imageCountLabel', {n: counts.media}) : '',
        counts.loop ? trf('smart.loopCount', {n: counts.loop}) : ''
    ].filter(Boolean).join(' · ') || tr('smart.promptDropHint');
    if(refThumbs.length){
        const totalThumbs = Math.max(1, Number(groupThumbLayout?.rows || 1) * Number(groupThumbLayout?.cols || 1));
        if(totalThumbs === 1 && refThumbs.length === 1){
            const ref = refThumbs[0];
            const innerW = Math.max(24, Number(groupThumbLayout.innerW || groupThumbLayout.width || SMART_GROUP_DEFAULT_WIDTH));
            const innerH = Math.max(24, Number(groupThumbLayout.innerH || groupThumbLayout.height || SMART_GROUP_DEFAULT_HEIGHT));
            const canDelete = ref.nodeId === node.id;
            return `<div class="smart-group-card has-thumbs">
                <div class="smart-group-summary"><i data-lucide="group"></i><span>${escapeHtml(summary)}</span></div>
                <div class="image-wrap smart-group-single-thumb ${canvasState.selectedImage.nodeId === ref.nodeId && Number(canvasState.selectedImage.index) === Number(ref.index) ? 'image-selected' : ''}" data-ref-node-id="${escapeAttr(ref.nodeId)}" data-ref-image-index="${ref.index}" data-image-index="${ref.index}" data-media-signature="${escapeAttr(`${mediaKindForItem(ref.item)}:${ref.item?.url || ''}`)}" style="--node-img-w:${innerW}px;--node-img-h:${innerH}px">${singleMediaHtml(ref.item, innerW, innerH)}${imageNameBadgeHtml(ref.item)}${imageResolutionBadgeHtml(ref.item)}${canDelete ? `<button class="mini-x image-delete" type="button" data-image-index="${ref.index}" title="${escapeHtml(tr('smart.deleteImage'))}"><i data-lucide="trash-2"></i></button>` : ''}${imageMarkButtonHtml(ref.item, ref.index)}</div>
            </div>`;
        }
        const groupMaxVisibleRows = (groupThumbLayout.compactMembers || []).length ? Number(groupThumbLayout.rows || 1) : SMART_GROUP_MAX_VISIBLE_ROWS;
        const visibleRows = Math.max(1, Math.min(groupMaxVisibleRows, Number(groupThumbLayout.visibleRows || groupThumbLayout.rows || 1)));
        const thumbW = BATCH_THUMB.WIDTH;
        const thumbH = BATCH_THUMB.HEIGHT;
        const maxHeight = Math.max(44, Number(groupThumbLayout.gridHeight));
        return `<div class="smart-group-card has-thumbs">
            <div class="smart-group-summary"><i data-lucide="group"></i><span>${escapeHtml(summary)}</span></div>
            <div class="thumb-grid smart-group-thumb-grid" data-thumb-scroll="1" style="--thumb-cols:${groupThumbLayout.cols}; --thumb-size:${thumbW}px; --thumb-max-height:${maxHeight}px">${refThumbs.map(ref => {
                const canDelete = ref.nodeId === node.id;
                return `<div class="thumb-item ${canvasState.selectedImage.nodeId === ref.nodeId && Number(canvasState.selectedImage.index) === Number(ref.index) ? 'image-selected' : ''}" data-ref-node-id="${escapeAttr(ref.nodeId)}" data-ref-image-index="${ref.index}" data-image-index="${ref.index}" data-media-signature="${escapeAttr(`${mediaKindForItem(ref.item)}:${ref.item?.url || ''}`)}" style="--thumb-w:${thumbW}px;--thumb-h:${thumbH}px">${thumbMediaHtml(ref.item)}${imageNameBadgeHtml(ref.item)}${imageResolutionBadgeHtml(ref.item)}${canDelete ? `<button class="mini-x image-delete" type="button" data-image-index="${ref.index}" title="${escapeHtml(tr('smart.deleteImage'))}"><i data-lucide="trash-2"></i></button>` : ''}${imageMarkButtonHtml(ref.item, ref.index)}</div>`;
            }).join('')}</div>
        </div>`;
    }
    return `<div class="smart-group-card">
        <div class="smart-group-summary"><i data-lucide="group"></i><span>${escapeHtml(summary)}</span></div>
        ${members.length ? '' : `<div class="smart-group-empty"><i data-lucide="plus"></i><span>${escapeHtml(tr('smart.groupDropHint'))}</span></div>`}
    </div>`;
}
// #98：局部编辑节点默认尺寸——表单内容（模式/角色/选区舞台/参数/结果）需要高度，
// 360x330 会溢出外框；卡片内部可滚动，用户仍可拖右下角调整。
const LOCAL_EDIT_NODE_W = 400;
const LOCAL_EDIT_NODE_H = 520;
function syncLocalEditInputRoles(node){
    if(!node || node.type !== 'local-edit') return node?.localEditInputRoles || {originalNode:'', referenceNodes:[], promptNode:'', promptNodes:[]};
    if(!Array.isArray(canvas?.connections)) return node.localEditInputRoles || {originalNode:'', referenceNodes:[], promptNode:'', promptNodes:[]};
    const map = {originalNode:'', referenceNodes:[], promptNode:'', promptNodes:[]};
    const edges = canvas.connections.filter(edge => edge?.to === node.id && edge?.kind === 'input' && ['original','references','prompt'].includes(edge?.role));
    edges.forEach(edge => {
        if(edge.role === 'original') map.originalNode = edge.from;
        else if(edge.role === 'references' && !map.referenceNodes.includes(edge.from)) map.referenceNodes.push(edge.from);
        else if(edge.role === 'prompt' && !map.promptNodes.includes(edge.from)) map.promptNodes.push(edge.from);
    });
    map.promptNode = map.promptNodes[0] || '';
    node.localEditInputRoles = map;
    node.inputNodeIds = Array.from(new Set(edges.map(edge => edge.from).filter(Boolean)));
    if(map.originalNode){
        const originalNode = canvasState.nodes.find(item => item.id === map.originalNode);
        node.original_asset_id = (originalNode?.images || []).map(image => image?.asset_id).find(Boolean) || '';
    } else node.original_asset_id = '';
    node.reference_asset_ids = map.referenceNodes.flatMap(id => {
        const referenceNode = canvasState.nodes.find(item => item.id === id);
        return (referenceNode?.images || []).map(image => image?.asset_id).filter(Boolean);
    }).filter((assetId, index, all) => all.indexOf(assetId) === index);
    return map;
}
function localEditFullImageRect(imageW, imageH, ratio){
    // 原图内满足精确比例的最大内接矩形（居中）。用于连接原图后的初始选区与「重置选区」，
    // 保证 modify 模式提交时后端 ±0.02 比例校验必然通过。
    if(!(imageW > 0) || !(imageH > 0) || !(ratio > 0)) return null;
    let w = imageW, h = imageW / ratio;
    if(h > imageH){ h = imageH; w = imageH * ratio; }
    const left = Math.round((imageW - w) / 2), top = Math.round((imageH - h) / 2);
    return [left, top, Math.min(imageW, Math.round(left + w)), Math.min(imageH, Math.round(top + h))];
}
function localEditBodyHtml(node){
    const roleMap = syncLocalEditInputRoles(node);
    const promptNodeIds = Array.from(new Set([...(roleMap.promptNodes || []), roleMap.promptNode].filter(Boolean)));
    const originalNode = canvasState.nodes.find(item => item.id === roleMap.originalNode);
    const originalItem = (originalNode?.images || []).find(item => item?.asset_id || item?.url);
    const originalPreviewUrl = originalItem?.url || (originalItem?.asset_id ? projectMediaUrl(originalItem.asset_id) : '');
    const promptConnected = promptNodeIds.length > 0;
    const connectedPrompt = promptNodeIds.map(id => canvasState.nodes.find(item => item.id === id))
        .map(item => String(item?.text || item?.prompt || '').trim()).filter(Boolean).join('\n\n');
    const promptValue = promptConnected ? connectedPrompt : (node.prompt || '');
    const selectionOk = node.mode === 'modify'
        ? (Array.isArray(node.selection) && node.selection.length === 4 && node.selection[2] > node.selection[0] && node.selection[3] > node.selection[1])
        : (Array.isArray(node.selection) && node.selection.length >= (node.selectionType === 'polygon' ? 3 : 2));
    const selState = !roleMap.originalNode || !node.original_asset_id
        ? 'needOriginal'
        : !selectionOk ? 'needSelection'
        : !String(node.prompt || '').trim() ? 'needPrompt'
        : node._leBusy ? 'busy'
        : node.output_asset_id ? 'done'
        : 'ready';
    const selStateText = {
        needOriginal: tr('smart.localEditNeedOriginal'),
        needSelection: tr('smart.localEditNeedSelection'),
        needPrompt: tr('smart.localEditNeedPrompt'),
        busy: tr('smart.localEditGenerating'),
        done: tr('smart.localEditResultReady'),
        ready: tr('smart.localEditReady')
    }[selState];
    // 三轮 UI 精修：角色行从「3 行标签+芯片」压缩为「单行 3 芯片」（图标+角色名+状态），
    // 节点高度显著下降，4:3 原图下默认高度不再出现内部滚条。
    const roleChip = (role, icon, label, filled, statusText) =>
        `<button type="button" class="local-edit-role-chip${node.localEditActiveRole === role ? ' active' : ''}${filled ? ' filled' : ''}" data-local-edit-connect-role="${role}" title="${escapeAttr(tr('smart.localEditRoleHint') + ' ' + statusText)}"><i data-lucide="${icon}" aria-hidden="true"></i><span class="local-edit-role-name">${escapeHtml(label)}</span><span class="local-edit-role-status">${escapeHtml(statusText)}</span></button>`;
    const notConnectedText = tr('smart.localEditRoleNotConnected');
    const originalStatus = originalNode ? (originalNode.title || originalNode.id) : notConnectedText;
    const referenceCount = (roleMap.referenceNodes || []).length;
    const referenceStatus = referenceCount ? trf('smart.localEditRoleReferencesCount', {n: referenceCount}) : notConnectedText;
    const promptStatus = promptConnected ? trf('smart.localEditRolePromptCount', {n: promptNodeIds.length}) : notConnectedText;
    const stage = originalPreviewUrl
        ? '<div class="local-edit-selection-stage" data-local-edit-stage><img data-local-edit-preview src="' + escapeAttr(originalPreviewUrl) + '" alt="' + escapeHtml(tr('smart.originalSelectionPreview')) + '" draggable="false"><canvas data-local-edit-selection-canvas aria-label="' + escapeHtml(tr('smart.localEditSelection')) + '"></canvas></div>'
        : '<div class="local-edit-selection-empty">' + escapeHtml(tr('smart.localEditConnectHint')) + '</div>';
    const stageHint = !originalPreviewUrl ? ''
        : node.mode === 'modify' ? tr('smart.localEditHintModify')
        : node.selectionType === 'polygon' ? tr('smart.localEditHintPolygon')
        : node.selectionType === 'curve' ? tr('smart.localEditHintCurve')
        : tr('smart.localEditHintFreehand');
    const resultHtml = node.images?.[0]?.asset_id
        ? '<div class="local-edit-result" data-local-edit-result><img src="' + escapeAttr(projectMediaUrl(node.images[0].asset_id)) + '" alt="' + escapeHtml(tr('smart.localEditResultTitle')) + '"></div>'
        : '';
    return `<div class="local-edit-card" data-local-edit-node="${escapeAttr(node.id)}">
        <div class="local-edit-heading">
            <span class="local-edit-heading-title"><i data-lucide="lasso"></i>${escapeHtml(tr('smart.localEditTitle'))}</span>
            <div class="local-edit-mode" role="group" aria-label="${escapeHtml(tr('smart.localEditMode'))}">
                <button type="button" data-local-edit-mode="modify" class="${node.mode === 'modify' ? 'active' : ''}">${escapeHtml(tr('smart.localModify'))}</button>
                <button type="button" data-local-edit-mode="retain" class="${node.mode === 'retain' ? 'active' : ''}">${escapeHtml(tr('smart.localRetain'))}</button>
            </div>
        </div>
        <div class="local-edit-scroll">
            <div class="local-edit-roles">
                ${roleChip('original', 'image', tr('smart.originalImage'), Boolean(originalNode), originalStatus)}
                ${roleChip('references', 'images', tr('smart.referenceLabel'), referenceCount > 0, referenceStatus)}
                ${roleChip('prompt', 'type', tr('smart.promptLabel'), promptConnected, promptStatus)}
            </div>
            <div class="local-edit-stage-wrap">
                ${stage}
                <div class="local-edit-stage-bar">
                    <span class="local-edit-stage-hint">${escapeHtml(stageHint)}</span>
                    ${originalPreviewUrl ? `<button type="button" class="local-edit-stage-action" data-local-edit-reset-selection>${escapeHtml(tr('smart.localEditResetSelection'))}</button>` : ''}
                </div>
            </div>
            <div class="local-edit-prompt-wrap">
                <span class="local-edit-prompt-label">${escapeHtml(tr('smart.promptLabel'))}${promptConnected ? ' · ' + escapeHtml(tr('smart.localEditPromptFromNodes')) : ''}</span>
                <textarea data-local-edit-role="prompt" ${promptConnected ? 'readonly' : ''} placeholder="${escapeHtml(tr('smart.promptPlaceholderLocal'))}">${escapeHtml(promptValue)}</textarea>
            </div>
            <div class="local-edit-params">
                ${node.mode === 'modify'
                    ? `<label>${escapeHtml(tr('smart.fixedRatio'))}<select data-local-edit-ratio><option value="1:1" ${node.aspect_ratio === '1:1' ? 'selected' : ''}>1:1</option><option value="4:3" ${node.aspect_ratio === '4:3' ? 'selected' : ''}>4:3</option><option value="16:9" ${node.aspect_ratio === '16:9' ? 'selected' : ''}>16:9</option><option value="3:4" ${node.aspect_ratio === '3:4' ? 'selected' : ''}>3:4</option></select></label>`
                    : `<label>${escapeHtml(tr('smart.freeShape'))}<select data-local-edit-shape><option value="freehand" ${node.selectionType === 'freehand' ? 'selected' : ''}>${escapeHtml(tr('smart.freehand'))}</option><option value="polygon" ${node.selectionType === 'polygon' ? 'selected' : ''}>${escapeHtml(tr('smart.polygon'))}</option><option value="curve" ${node.selectionType === 'curve' ? 'selected' : ''}>${escapeHtml(tr('smart.curve'))}</option></select></label>`}
                <label>${escapeHtml(tr('smart.generationSize'))}<select data-local-edit-size>${['1024x1024','2048x2048','4096x4096'].map(value => `<option value="${value}" ${String(node.generation_size || '1024x1024') === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label>
                <label>${escapeHtml(tr('smart.resolution'))}<select data-local-edit-resolution>${['1k','2k','4k'].map(value => `<option value="${value}" ${String(node.generation_resolution || '1k') === value ? 'selected' : ''}>${value}</option>`).join('')}</select></label>
                <label>${escapeHtml(tr('smart.feather'))}<input type="number" min="0" max="256" step="1" data-local-edit-feather value="${Number(node.feather_radius || 0)}"></label>
            </div>
            ${resultHtml}
        </div>
        <div class="local-edit-footer">
            <span class="local-edit-status ${selState === 'needOriginal' || selState === 'needSelection' || selState === 'needPrompt' ? 'is-blocked' : ''}" data-local-edit-status role="status">${escapeHtml(selStateText)}</span>
            <button type="button" class="local-edit-submit" data-local-edit-submit="${escapeAttr(node.id)}" ${selState === 'needOriginal' || selState === 'needSelection' || selState === 'needPrompt' || node._leBusy ? 'disabled' : ''}>${escapeHtml(tr('smart.submitLocalEdit'))}</button>
        </div>
    </div>`;
}
function smartProjectTaskInfo(node){
    // #81：节点上是否存在项目级生成任务（图片/视频）——供占位面板渲染取消按钮与已等待时长。
    if(!node) return null;
    for(const task of smartPendingTasks(node)){
        const tid = String(task?.taskId || '');
        if(tid.startsWith('project_img_') || tid.startsWith('project_vid_')){
            return {
                taskId:tid,
                kind:tid.startsWith('project_vid_') ? 'video' : 'image',
                startedAt:Number(task?.progress?.started_at) || Number(node?.runStartedAt) || Number(task?.createdAt) || 0
            };
        }
    }
    return null;
}
async function cancelNodeGenerationTask(nodeId, button=null){
    const node = canvasState.nodes.find(item => String(item?.id || '') === String(nodeId || ''));
    const info = node ? smartProjectTaskInfo(node) : null;
    if(!info?.taskId) return;
    if(!window.confirm(tr('smart.nodeCancelConfirm'))) return;
    if(button){ button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    try {
        await controlProjectImageTask('', info.taskId, 'cancel', button);
    } catch(err){
        if(button){ button.disabled = false; button.removeAttribute('aria-busy'); }
        toast(err?.message || tr('smart.nodeCancelFailed'));
    }
}
// #90：批量待出图格子 → 生成任务详情弹窗：展示当前节点全部任务
//（任务 ID / 阶段 / 进度 / 已用时），支持取消指定任务。
// 数据从 node.pendingTasks + canvas.history 现读，每秒重渲（状态签名变化时），
// 任务完成/失败后行内状态实时变化；关闭时清理定时器与事件。
let batchTaskDetailState = null; // {nodeId, cellIndex, timer, backdrop, onKey}
function smartBatchTaskRows(node){
    const tasks = smartPendingTasks(node);
    const batchProgress = (tasks.find(task => task?.progress) || {}).progress || {};
    const candidatePhases = Array.isArray(batchProgress.candidate_phases) ? batchProgress.candidate_phases : [];
    return tasks.map((task, index) => {
        const taskId = String(task?.taskId || '');
        const log = (canvas?.history || []).find(item => smartHistoryTaskId(item) === taskId);
        const status = log?.status === 'failed' ? 'failed' : log?.status === 'completed' ? 'completed' : 'active';
        const phaseKey = String(task?.progress?.phase || candidatePhases[index] || '');
        const phaseIndex = Math.max(0, GENERATION_PROGRESS_PHASES.findIndex(item => item[0] === phaseKey));
        const phaseLabel = GENERATION_PROGRESS_PHASES[phaseIndex]?.[1] || phaseKey || '';
        const taskProgress = task?.progress || {};
        const startedAt = Number(taskProgress.started_at || task?.createdAt || 0);
        let progressText = '—';
        if(Number(taskProgress.current) > 0 && Number(taskProgress.total) > 0) progressText = `${taskProgress.current} / ${taskProgress.total}`;
        else if(Number(taskProgress.percent) > 0) progressText = `${Math.round(Number(taskProgress.percent))}%`;
        else if(Number(taskProgress.bytes_total) > 0) progressText = `${generationProgressBytes(taskProgress.bytes_done)} / ${generationProgressBytes(taskProgress.bytes_total)}`;
        const elapsedMs = status === 'active'
            ? (startedAt > 0 ? Math.max(0, Date.now() - startedAt) : 0)
            : (Number(taskProgress.elapsed_ms) || (startedAt > 0 ? Math.max(0, (Number(log?.finished_at) || Date.now()) - startedAt) : 0));
        return { taskId, status, phaseKey, phaseLabel, progressText, elapsedMs, isProject:/^project_(img|vid)_/.test(taskId) };
    });
}
function renderBatchTaskDetail(){
    const state = batchTaskDetailState;
    if(!state) return;
    const node = canvasState.nodes.find(item => String(item?.id || '') === state.nodeId);
    const rows = node ? smartBatchTaskRows(node) : [];
    const backdrop = state.backdrop;
    const listEl = backdrop.querySelector('[data-batch-task-detail-list]');
    if(!listEl) return;
    const total = Math.max(1, rows.length, Number(node?._batchTotal) || 0, Number(node?.pending) || 0);
    const cellEl = backdrop.querySelector('[data-batch-task-detail-cell]');
    if(cellEl) cellEl.textContent = trf('smart.batchTaskCellOf', {i: state.cellIndex + 1, n: total});
    const subEl = backdrop.querySelector('[data-batch-task-detail-sub]');
    if(subEl) subEl.textContent = trf('smart.batchTaskSub', {name: node?.title || tr('smart.nodeTitleImage'), count: rows.length});
    listEl.innerHTML = rows.length
        ? rows.map((row, index) => {
            const activeCell = index === state.cellIndex;
            const statusLabel = row.status === 'failed' ? tr('smart.failed') : row.status === 'completed' ? tr('smart.stSucceeded') : (row.phaseKey === 'queued' ? tr('smart.stQueued') : tr('smart.stRunning'));
            const statusClass = row.status === 'active' ? (row.phaseKey === 'queued' ? 'is-queued' : 'is-running') : `is-${row.status}`;
            const shortId = row.taskId.length > 30 ? `${row.taskId.slice(0, 22)}…${row.taskId.slice(-6)}` : row.taskId;
            const cancelBtn = (row.status === 'active' && row.isProject)
                ? `<button type="button" class="batch-task-detail-cancel" data-batch-task-cancel="${escapeAttr(row.taskId)}"><i data-lucide="square"></i><span>${escapeHtml(tr('smart.batchTaskCancel'))}</span></button>`
                : '';
            return `<div class="batch-task-detail-row${activeCell ? ' is-active' : ''}" data-batch-task-row="${index}">
                <span class="batch-task-detail-idx">${index + 1}/${rows.length}</span>
                <div class="batch-task-detail-main">
                    <div class="batch-task-detail-top">
                        <span class="batch-task-detail-status ${statusClass}">${escapeHtml(statusLabel)}</span>
                        <code class="batch-task-detail-id" title="${escapeAttr(row.taskId)}">${escapeHtml(shortId)}</code>
                    </div>
                    <div class="batch-task-detail-meta">
                        <span>${escapeHtml(tr('smart.batchTaskColStage'))} <b>${escapeHtml(row.phaseLabel || '—')}</b></span>
                        <span>${escapeHtml(tr('smart.batchTaskColProgress'))} <b>${escapeHtml(row.progressText)}</b></span>
                    </div>
                </div>
                <span class="batch-task-detail-elapsed" title="${escapeAttr(tr('smart.batchTaskColElapsed'))}">${escapeHtml(formatRunDuration(row.elapsedMs) || '0s')}</span>
                ${cancelBtn}
            </div>`;
        }).join('')
        : `<div class="batch-task-detail-empty">${escapeHtml(tr('smart.batchTaskEmpty'))}</div>`;
    listEl.querySelectorAll('[data-batch-task-cancel]').forEach(btn => {
        btn.addEventListener('click', async e => {
            e.preventDefault(); e.stopPropagation();
            const taskId = btn.dataset.batchTaskCancel;
            if(!window.confirm(trf('smart.batchTaskCancelConfirm', {task: taskId}))) return;
            btn.disabled = true;
            btn.setAttribute('aria-busy', 'true');
            try {
                await controlProjectImageTask('', taskId, 'cancel', btn);
            } catch(err){
                toast(err?.message || tr('smart.nodeCancelFailed'));
                renderBatchTaskDetail();
            }
        });
    });
    const activeRow = listEl.querySelector('.batch-task-detail-row.is-active');
    if(activeRow) activeRow.scrollIntoView({block:'nearest'});
    refreshIcons(listEl);
}
function openBatchTaskDetail(nodeId, cellIndex=0){
    const node = canvasState.nodes.find(item => String(item?.id || '') === String(nodeId || ''));
    if(!node) return;
    closeBatchTaskDetail();
    const backdrop = document.createElement('div');
    backdrop.id = 'smartBatchTaskDetailBackdrop';
    backdrop.className = 'batch-task-detail-backdrop';
    backdrop.innerHTML = `<div class="batch-task-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="smartBatchTaskDetailTitle">
        <div class="batch-task-detail-head">
            <h3 id="smartBatchTaskDetailTitle">${escapeHtml(tr('smart.batchTaskDetailTitle'))}<span class="batch-task-detail-cell" data-batch-task-detail-cell></span></h3>
            <button type="button" class="batch-task-detail-close" data-batch-task-detail-close aria-label="${escapeAttr(tr('common.close'))}"><i data-lucide="x"></i></button>
        </div>
        <p class="batch-task-detail-sub" data-batch-task-detail-sub></p>
        <div class="batch-task-detail-list" data-batch-task-detail-list></div>
    </div>`;
    const onKey = e => { if(e.key === 'Escape'){ e.stopPropagation(); closeBatchTaskDetail(); } };
    batchTaskDetailState = {nodeId: String(node.id), cellIndex, timer:null, backdrop, onKey};
    document.addEventListener('keydown', onKey, true);
    backdrop.addEventListener('click', e => { if(e.target === backdrop) closeBatchTaskDetail(); });
    backdrop.querySelector('[data-batch-task-detail-close]').addEventListener('click', e => { e.stopPropagation(); closeBatchTaskDetail(); });
    document.body.appendChild(backdrop);
    renderBatchTaskDetail();
    batchTaskDetailState.timer = setInterval(renderBatchTaskDetail, 1000);
    refreshIcons(backdrop);
}
function closeBatchTaskDetail(){
    const state = batchTaskDetailState;
    if(!state) return;
    batchTaskDetailState = null;
    if(state.timer) clearInterval(state.timer);
    document.removeEventListener('keydown', state.onKey, true);
    state.backdrop.remove();
}
setInterval(() => {
    // #81：轻量秒级刷新占位面板的「已等待」文案（只改文本节点，不触发画布重渲）。
    document.querySelectorAll('[data-elapsed-since]').forEach(el => {
        const since = Number(el.dataset.elapsedSince);
        if(!since) return;
        const sec = Math.max(0, Math.floor((Date.now() - since) / 1000));
        el.textContent = sec < 60 ? tr('canvas.taskElapsedSec').replace('{n}', String(sec)) : sec < 3600 ? tr('canvas.taskElapsedMin').replace('{n}', String(Math.floor(sec / 60))) : tr('canvas.taskElapsedHour').replace('{n}', String(Math.floor(sec / 3600)));
    });
}, 1000);
function generationPlaceholderHtml(layout, count=1, queued=false, progress={}, completedImgs=[], candidatePhases=[], taskInfo=null, nodeId=''){
    // #90：待出图格子可点击 → 任务详情弹窗（nodeId 空时不挂点击属性）。
    // 占位数量按真实并发数渲染（上限 8），与 pendingBoxSize 的网格公式保持一致，
    // 避免「8 张并发只显示 4 个占位」以及框尺寸与网格不一致。
    const total = Math.max(1, Math.min(8, Number(count) || 1));
    const label = queued ? tr('smart.queuedWaiting') : tr('canvas.generating');
    const normalizedProgress = Object.keys(progress || {}).length ? progress : {phase:queued ? 'queued' : 'provider_generating', label:queued ? tr('smart.phaseQueued') : tr('smart.phaseProviderGenerating'), phase_index:queued ? 0 : 3, phase_count:6};
    // #81：项目级任务（图片/视频）在占位面板上直接提供取消按钮 + 已等待时长，
    // 不必进生图历史抽屉找按钮；长视频渲染时不再「无限干等无任何反馈」。
    let cancelBtnHtml = '';
    let elapsedHtml = '';
    if(taskInfo?.taskId){
        cancelBtnHtml = `<button type="button" class="generation-cancel-btn" data-node-cancel-task="${escapeAttr(taskInfo.taskId)}" title="${escapeAttr(tr('smart.nodeCancelTaskTitle'))}"><i data-lucide="square"></i><span>${escapeHtml(tr('smart.nodeCancelTask'))}</span></button>`;
        if(Number(taskInfo.startedAt) > 0) elapsedHtml = `<span class="generation-placeholder-elapsed" data-elapsed-since="${Number(taskInfo.startedAt)}"></span>`;
    }
    const copy = `<div class="generation-placeholder-panel"><span class="generation-placeholder-copy"><span class="generation-placeholder-spinner" aria-hidden="true"></span><span>${escapeHtml(label)}</span>${elapsedHtml}</span>${generationStageProgressHtml(normalizedProgress)}${cancelBtnHtml}</div>`;
    if(total === 1){
        if(completedImgs[0]){
            const first = imageForDisplay(completedImgs[0]);
            if(first?.url) return `<div class="loading-cell single filled generation-filled-cell" style="width:100%;height:100%" data-image-index="0">${thumbMediaHtml(first)}</div>`;
        }
        // 单图占位与批量分支同规范：铺满 node-body（width/height:100%），
        // 节点 16px padding 即外边距；此前用 layout.width/height（=节点外框尺寸）
        // 会让占位框贴住/越出外框、与 pending 呼吸边框错位。
        return `<div class="loading-cell single generation-placeholder${queued ? ' queued' : ''}${nodeId ? ' batch-cell-clickable' : ''}" style="width:100%;height:100%" role="status" aria-label="${escapeAttr(label)}"${nodeId ? ` data-batch-cell-detail="${escapeAttr(nodeId)}" data-batch-cell-index="0" title="${escapeAttr(tr('smart.batchTaskCellHint'))}"` : ''}>${copy}</div>`;
    }
    // 与 pendingBoxSize/imageLayout 使用同一套网格规范（getBatchThumbGridSize），
    // 占位格固定 136×102，不再用 1fr 拉伸，保证与完成阶段缩略图尺寸一致。
    const grid = getBatchThumbGridSize(total);
    const cols = grid.cols;
    const rows = grid.rows;
    // 每张候选的真实阶段（后端 progress.candidate_phases）：按格显示各自进度。
    const phases = Array.isArray(candidatePhases) ? candidatePhases.map(phase => String(phase || '')) : [];
    const cells = Array.from({length:total}).map((_, index) => {
        const done = completedImgs[index];
        const doneImg = done ? imageForDisplay(done) : null;
        if(doneImg?.url){
            return `<div class="loading-cell filled generation-filled-cell" data-image-index="${index}" role="img" aria-label="${escapeAttr(`${index + 1}/${total}`)}">${thumbMediaHtml(doneImg)}</div>`;
        }
        // #90：待出图格子（未写回）挂点击属性 → 打开任务详情弹窗。
        const cellDetail = nodeId ? ` data-batch-cell-detail="${escapeAttr(nodeId)}" data-batch-cell-index="${index}" title="${escapeAttr(tr('smart.batchTaskCellHint'))}"` : '';
        const cellPhase = phases[index] || '';
        if(cellPhase && cellPhase !== 'completed'){
            const stageIndex = Math.max(0, GENERATION_PROGRESS_PHASES.findIndex(item => item[0] === cellPhase));
            const stageLabel = GENERATION_PROGRESS_PHASES[stageIndex]?.[1] || cellPhase;
            const stageSegments = GENERATION_PROGRESS_PHASES.map(([key, title], stageIdx) => `<span class="cell-stage-seg${stageIdx < stageIndex ? ' done' : stageIdx === stageIndex ? ' active' : ''}" title="${escapeAttr(title)}"></span>`).join('');
            return `<div class="loading-cell generation-placeholder cell-with-stage${nodeId ? ' batch-cell-clickable' : ''}"${cellDetail} role="status" aria-label="${escapeAttr(stageLabel)}"><div class="generation-placeholder-panel cell-stage-panel"><span class="cell-stage-label">${escapeHtml(stageLabel)}</span><span class="cell-stage-track" aria-hidden="true">${stageSegments}</span></div></div>`;
        }
        // #84：空格不再塞完整状态面板（会溢出 136×102 格子），统一只显示序号；
        // 状态面板整体移到节点底部状态条（见下方 .pending-batch-stack）。
        // 三轮 UI 精修：序号去掉重胶囊（描边+阴影+毛玻璃），改为轻量居中小字，网格更安静。
        return `<div class="loading-cell generation-placeholder${nodeId ? ' batch-cell-clickable' : ''}"${cellDetail} role="status" aria-label="${escapeAttr(label)}"><span class="generation-placeholder-idx">${index + 1}/${total}</span></div>`;
    }).join('');
    // #84：网格 + 底部整宽状态条（spinner + 生成中/已等待 + 阶段进度 + 取消按钮一行横排）。
    // 此前状态面板竖排塞进单个 136×102 缩略格（高约 108px > 格高 102px），
    // 导致文字裁切、取消按钮被切、右上计时徽标与状态格重叠。
    // #89 返工：状态条不再重复「已等待 Ns」——节点右上角计时徽标（run-time-pill）已实时显示同一计时，
    // 重复展示会挤掉阶段名（2 列节点框宽仅约 295px）。
    const statusbar = `<div class="batch-statusbar" role="status" aria-label="${escapeAttr(label)}"><span class="generation-placeholder-spinner" aria-hidden="true"></span><span class="batch-statusbar-label">${escapeHtml(label)}</span><span class="batch-statusbar-stage">${generationStageProgressHtml(normalizedProgress)}</span>${cancelBtnHtml}</div>`;
    // 网格区 = node-body 高度减去状态条（flex 布局自动分配），136×102 固定格在剩余区域居中，
    // 与完成阶段 thumb-grid 的几何一致。
    return `<div class="pending-batch-stack"><div class="loading-skeleton generation-placeholder-grid" style="grid-template-columns:repeat(${cols}, ${BATCH_THUMB.WIDTH}px);grid-template-rows:repeat(${rows}, ${BATCH_THUMB.HEIGHT}px);gap:${BATCH_THUMB.GAP}px;width:100%;box-sizing:border-box;justify-content:center;align-content:center">${cells}</div>${statusbar}</div>`;
}
function nodeBodyHtml(node, layout){
    if(isSmartAgentTaskNode(node)) return agentTaskNodeBodyHtml(node);
    if(node.type === 'smart-group') return smartGroupBodyHtml(node);
    if(isSmartConversationNode(node)) return conversationNodeBodyHtml(node);
    if(isSmartAgentNode(node)) return agentNodeBodyHtml(node);
    if(node.type === 'smart-prompt') return promptNodeBodyHtml(node);
    if(node.type === 'smart-loop') return smartLoopBodyHtml(node);
    if(node.type === 'local-edit') return localEditBodyHtml(node);
    const imgs = (node.images || []).map(imageForDisplay);
    if(node.jimengPending && node.jimengPending.submitId && imgs.length === 0){
        return jimengPendingBodyHtml(node, layout);
    }
    const recoverTask = smartRecoverableImageTask(node);
    if(recoverTask && imgs.length === 0){
        return imageTaskRecoverBodyHtml(node, recoverTask, layout);
    }
    const failedGenerationTasks = Array.isArray(node?.failedGenerationTasks) ? node.failedGenerationTasks : [];
    if(failedGenerationTasks.length && imgs.length === 0 && !node.pending && !smartPendingTasks(node).length){
        return failedGenerationBodyHtml(node, failedGenerationTasks, layout);
    }
    if(node.queued && imgs.length === 0 && !node.pending){
        const queuedProgress = smartPendingTasks(node).find(task => task?.progress)?.progress || {};
        return generationPlaceholderHtml(layout, 1, true, queuedProgress, [], [], smartProjectTaskInfo(node), node.id);
    }
    if(node.pending){
        // 以提交时固化的 _batchTotal 渲染占位格（写回期间格子数/几何不变），已写回的图按序填入
        const count = Math.max(1, Math.min(8, Number(node?._batchTotal) || Number(node.pending) || 1));
        const progress = smartPendingTasks(node).find(task => task?.progress)?.progress || {};
        const completed = (imgs || []).slice(0, count);
        const candidatePhases = Array.isArray(progress?.candidate_phases) ? progress.candidate_phases : [];
        return generationPlaceholderHtml(layout, count, false, progress, completed, candidatePhases, smartProjectTaskInfo(node), node.id);
    }
    if(imgs.length > 1){
        const visibleRows = Math.max(1, Math.min(MEDIA_GROUP_MAX_VISIBLE_ROWS, Number(layout.visibleRows || layout.rows || 1)));
        const maxHeight = Number(layout.gridHeight) || mediaGroupGridHeight(imgs, layout.cols, visibleRows, layout.thumb);
        // 固化比例（_generationAspect）存在时，缩略格尺寸锁定为 thumb×cellH，
        // 图在格内 contain 居中——行高不再由图片自身比例决定，卡片尺寸与占位阶段一致。
        // 批量节点没有固化比例时（旧数据），按批量缩略格规范 4:3 兑底。
        const fixedAspect = node?._generationAspect || (Number(node?._batchTotal) > 1 ? {w:BATCH_THUMB.ASPECT.w, h:BATCH_THUMB.ASPECT.h} : null);
        // 批量生图节点：固定 136×102（4:3）缩略格 + 9px 间距，与占位阶段/生图历史一致
        const isBatchSpec = Number(layout.thumb) === BATCH_THUMB.WIDTH && (fixedAspect && Number(fixedAspect.w) > 0 && Number(fixedAspect.h) > 0);
        const fixedCellH = isBatchSpec ? BATCH_THUMB.HEIGHT : (fixedAspect && Number(fixedAspect.w) > 0 && Number(fixedAspect.h) > 0) ? Math.round(layout.thumb * Number(fixedAspect.h) / Math.max(1, Number(fixedAspect.w))) : 0;
        const thumbItemStyleFor = img => fixedCellH > 0 ? `--thumb-w:${layout.thumb}px;--thumb-h:${fixedCellH}px` : thumbItemStyle(img, layout.thumb);
        const gridGapStyle = isBatchSpec ? `; gap:${BATCH_THUMB.GAP}px` : '';
        // #89 返工：自然比例路径列宽=实际格宽（与 imageLayout 收敛后的框宽一致），
        // 批量/固化比例路径保持 repeat(--thumb-cols, --thumb-size) 不变。
        const gridColStyle = (fixedCellH > 0 || !Array.isArray(layout.colWidths) || layout.colWidths.length !== layout.cols) ? '' : `grid-template-columns:${layout.colWidths.map(w => `${Math.round(w)}px`).join(' ')};`;
        // 批量生图终态（未运行中）若只成功部分张数：补齐缺失的格子为虚线空位，
        // 让网格几何与占位/生图阶段完全一致，避免 3/8 时只浮 3 张图、框内大片空白。
        const batchSlots = !node.pending && Number(node?._batchTotal) > 1 ? Math.min(8, Number(node._batchTotal)) : 0;
        const missingSlots = isBatchSpec && fixedCellH > 0 ? Math.max(0, batchSlots - imgs.length) : 0;
        const emptySlotHtml = Array.from({length: missingSlots}).map(() => `<div class="thumb-empty-slot" style="--thumb-w:${layout.thumb}px;--thumb-h:${fixedCellH}px" aria-hidden="true"></div>`).join('');
        return `<div class="thumb-grid" data-thumb-scroll="1" style="${gridColStyle}--thumb-cols:${layout.cols}; --thumb-size:${layout.thumb}px; --thumb-max-height:${maxHeight}px${gridGapStyle}">${imgs.map((img, i) => `<div class="thumb-item has-outside-image-name ${canvasState.selectedImage.nodeId === node.id && canvasState.selectedImage.index === i ? 'image-selected' : ''}" data-image-index="${i}" data-output-id="${escapeAttr(img?.output_id || '')}" data-asset-id="${escapeAttr(img?.asset_id || '')}" data-media-signature="${escapeAttr(`${mediaKindForItem(img)}:${img?.url || ''}`)}" style="${thumbItemStyleFor(img)}">${thumbMediaHtml(img)}${imageNameBadgeHtml(img, {outside:true})}${imageResolutionBadgeHtml(img)}<button class="mini-x image-delete" type="button" data-image-index="${i}" title="${escapeHtml(tr('smart.deleteImage'))}"><i data-lucide="trash-2"></i></button>${imageMarkButtonHtml(img, i)}</div>`).join('')}${emptySlotHtml}</div>`;
    }
    if(imgs[0]) return `<div class="image-wrap has-outside-image-name ${canvasState.selectedImage.nodeId === node.id && canvasState.selectedImage.index === 0 ? 'image-selected' : ''}" data-image-index="0" data-output-id="${escapeAttr(imgs[0]?.output_id || '')}" data-asset-id="${escapeAttr(imgs[0]?.asset_id || '')}" data-media-signature="${escapeAttr(`${mediaKindForItem(imgs[0])}:${imgs[0]?.url || ''}`)}" style="--node-img-w:${layout.width}px;--node-img-h:${layout.height}px">${singleMediaHtml(imgs[0], layout.width, layout.height)}${imageNameBadgeHtml(imgs[0], {outside:true})}${imageResolutionBadgeHtml(imgs[0])}<button class="mini-x image-delete" type="button" data-image-index="0" title="${escapeHtml(tr('smart.deleteImage'))}"><i data-lucide="trash-2"></i></button>${imageMarkButtonHtml(imgs[0], 0)}</div>`;
    return `<div class="node-drop" data-upload-action="files">
        <span class="upload-node-main"><i data-lucide="upload-cloud"></i></span>
        <span class="upload-node-title">${escapeHtml(tr('smart.createImportNode'))}</span>
        <span class="upload-node-sub">${escapeHtml(tr('smart.uploadNodeHint'))}</span>
    </div>`;
}
function jimengPendingBodyHtml(node, layout){
    const jp = node.jimengPending || {};
    const querying = Boolean(jp.querying);
    const queueText = jimengQueueText(jp.queueInfo);
    return `<div class="jimeng-pending-cell loading-cell single" style="width:100%;height:100%">
        <div class="jimeng-pending-overlay">
            <div class="jimeng-pending-spinner"><i data-lucide="loader-2"></i></div>
            <div class="jimeng-pending-text">${escapeHtml(queueText)}</div>
            <div class="jimeng-pending-sub">${escapeHtml(tr('smart.taskNotLost'))}</div>
            <button class="jimeng-pending-query" type="button" data-jimeng-query="${escapeAttr(node.id)}" ${querying ? 'disabled' : ''}><i data-lucide="${querying ? 'loader-2' : 'refresh-cw'}"></i><span>${querying ? tr('smart.queryingEllipsis') : tr('smart.queryResult')}</span></button>
        </div>
    </div>`;
}
function smartRecoverableImageTask(node){
    const fromPending = smartPendingTasks(node).find(task => task.failed && task.recoverTaskId) || null;
    if(fromPending) return fromPending;
    if(!Array.isArray(node?.recoveredGenerationTasks)) return null;
    return node.recoveredGenerationTasks.find(task => task.failed && task.recoverTaskId) || null;
}
function failedGenerationBodyHtml(node, tasks, layout){
    const latest = tasks[tasks.length - 1] || {};
    const error = String(latest.error || tr('smart.errRunFailed')).slice(0, 200);
    const recovered = tasks.length > 1 ? trf('smart.includingPrevFailed', {n: tasks.length - 1}) : '';
    return `<div class="loading-cell single generation-failed" style="width:${layout.width}px;height:${layout.height}px" role="status" aria-label="${escapeHtml(tr('smart.generationFailed'))}">
        <div class="generation-failed-panel">
            <span class="generation-failed-icon"><i data-lucide="alert-triangle"></i></span>
            <span class="generation-failed-title">${escapeHtml(tr('smart.generationFailed'))}${escapeHtml(recovered)}</span>
            <span class="generation-failed-error">${escapeHtml(error)}</span>
            <div class="generation-failed-actions">
                <button type="button" class="generation-failed-retry" data-image-task-retry="${escapeAttr(node.id)}"><i data-lucide="refresh-cw"></i><span>${escapeHtml(tr('smart.regenerate'))}</span></button>
                <button type="button" class="generation-failed-url" data-image-task-url="${escapeAttr(node.id)}"><i data-lucide="link"></i><span>${escapeHtml(tr('smart.manualUrlFill'))}</span></button>
            </div>
        </div>
    </div>`;
}
function imageTaskRecoverBodyHtml(node, task, layout){
    const querying = Boolean(task.querying);
    const failedCount = smartPendingTasks(node).filter(item => item.failed && item.recoverTaskId).length
        + (Array.isArray(node.recoveredGenerationTasks) ? node.recoveredGenerationTasks.filter(item => item.failed && item.recoverTaskId).length : 0);
    const title = querying ? tr('smart.querying') : tr('smart.taskNotLost');
    const sub = failedCount > 1 ? trf('smart.moreTasksToQuery', {n: failedCount}) : trf('smart.taskIdLabel', {id: task.recoverTaskId || ''});
    return `<div class="jimeng-pending-cell loading-cell single" style="width:100%;height:100%">
        <div class="jimeng-pending-overlay">
            <div class="jimeng-pending-spinner"><i data-lucide="${querying ? 'loader-2' : 'refresh-cw'}"></i></div>
            <div class="jimeng-pending-text">${escapeHtml(title)}</div>
            <div class="jimeng-pending-sub">${escapeHtml(sub)}</div>
            <button class="jimeng-pending-query" type="button" data-image-task-query="${escapeAttr(node.id)}" data-task-id="${escapeAttr(task.taskId)}" ${querying ? 'disabled' : ''}><i data-lucide="${querying ? 'loader-2' : 'refresh-cw'}"></i><span>${querying ? tr('smart.queryingEllipsis') : tr('smart.queryResult')}</span></button>
        </div>
    </div>`;
}
function smartNodeToolbarImageIndex(node){
    const images = node?.images || [];
    if(canvasState.selectedImage.nodeId === node?.id){
        const index = Number(canvasState.selectedImage.index);
        if(Number.isFinite(index) && index >= 0 && index < images.length) return index;
    }
    return 0;
}
function smartNodeToolbarHtml(node){
    const isImageNode = node?.type === 'smart-image' || !node?.type;
    const images = node?.images || [];
    if(!isImageNode || !images.some(img => img?.url || img?.asset_id)) return '';
    const source = images[smartNodeToolbarImageIndex(node)] || images.find(img => img?.url || img?.asset_id);
    const item = imageForDisplay(source) || source;
    if(!item?.url && !source?.asset_id) return '';
    const kind = item?.url ? mediaKindForItem(item) : (source?.kind || source?.media_kind || 'image');
    const canEditImage = kind === 'image';
    const imageCount = images.filter(img => {
        const display = imageForDisplay(img) || img;
        const displayKind = display?.url ? mediaKindForItem(display) : (img?.kind || img?.media_kind);
        return displayKind === 'image' && (display?.url || img?.asset_id);
    }).length;
    const gridLabel = imageCount > 1 ? tr('smart.nodeToolbarGridJoin') : tr('smart.nodeToolbarGridSplit');
    const actions = [
        {key:'preview', icon:'eye', label:tr('smart.modePreview'), enabled:kind === 'image' || kind === 'video'},
        {key:'detail', icon:'info', label:tr('smart.nodeDetail'), enabled:kind === 'image'},
        {key:'crop', icon:'crop', label:tr('smart.nodeToolbarCrop'), enabled:canEditImage},
        {key:'outpaint', icon:'expand', label:tr('smart.nodeToolbarOutpaint'), enabled:canEditImage},
        {key:'mask', icon:'brush', label:tr('smart.nodeToolbarMask'), enabled:canEditImage},
        {key:'local-edit', icon:'lasso', label:tr('smart.localEditTitle'), enabled:canEditImage},
        {key:'brush', icon:'paintbrush', label:tr('smart.nodeToolbarBrush'), enabled:canEditImage},
        {key:'grid', icon:'grid-3x3', label:gridLabel, enabled:canEditImage},
        {key:'send-ps', icon:'send', label:tr('smart.nodeToolbarSendPs'), enabled:canEditImage || Boolean(source?.asset_id)},
        {key:'download', icon:'download', label:tr('smart.nodeDownload'), enabled:true}
    ];
    return `<div class="smart-node-floating-menu" data-smart-node-menu="1">${actions.map(action => `
        <button type="button" data-smart-node-action="${escapeAttr(action.key)}" data-node-id="${escapeAttr(node.id)}" ${action.enabled ? '' : 'disabled'} title="${escapeAttr(action.label)}" aria-label="${escapeAttr(action.label)}">
            <i data-lucide="${escapeAttr(action.icon)}"></i><span>${escapeHtml(action.label)}</span>
        </button>`).join('')}</div>`;
}
function duplicateSmartNodeMediaToCanvas(node, imageIndex){
    const source = node?.images?.[imageIndex];
    const item = imageForDisplay(source);
    if(!node || !item?.url){ toast(tr('smart.noExportableMedia')); return; }
    pushUndo();
    const rect = nodeRect(node);
    const point = {x:rect.x + rect.width + 220, y:rect.y + rect.height / 2};
    const copy = {...item};
    const newNode = createImageNodeAt(point, [copy], {select:true, skipUndo:true});
    canvasState.selectedIds = [];
    canvasState.selectedImage = {nodeId:newNode.id, index:0};
    render();
    scheduleSave();
    toast(tr('smart.addedToCanvas'));
}
async function sendSmartNodeImageToPhotoshop(node, imageIndex){
    const item = node?.images?.[imageIndex];
    const assetId = String(item?.asset_id || '').trim();
    const projectId = canvas?.project_id || canvas?.project || canvasId || '';
    if(!projectId || !node?.id || !assetId){ toast(tr('smart.noProjectMediaOriginal')); return; }
    try{
        if(typeof flushProjectCanvasBeforeGeneration === 'function' && !await flushProjectCanvasBeforeGeneration()){
            throw new Error(tr('smart.canvasNotSavedPs'));
        }
        const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/photoshop/round-trips`, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({source_node_id:node.id, source_media_id:assetId, ttl_seconds:300})
        });
        if(!response.ok) throw new Error(await responseErrorMessage(response, tr('smart.sendPsFailed')));
        const binding = await response.json();
        toast(trf('smart.sentToPs', {id: binding.request_id}));
    }catch(error){ toast(error.message || tr('smart.sendPsFailed')); }
}
function runSmartNodeToolbarAction(nodeId, action){
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if(!node) return;
    const index = smartNodeToolbarImageIndex(node);
    const source = node.images?.[index];
    const item = imageForDisplay(source) || source;
    const kind = mediaKindForItem(item);
    canvasState.selectedId = nodeId;
    canvasState.selectedIds = [];
    canvasState.selectedImage = {nodeId, index};
    if(action === 'download'){
        downloadPreviewFile(node.images?.[index] || item);
        return;
    }
    if(action === 'canvas'){
        duplicateSmartNodeMediaToCanvas(node, index);
        return;
    }
    if(action === 'send-ps'){
        sendSmartNodeImageToPhotoshop(node, index);
        return;
    }
    if(action === 'local-edit'){
        createLocalEditNodeFromImage(nodeId, index);
        return;
    }
    if(!item?.url) return;
    if(kind !== 'image' && action !== 'preview'){
        toast(tr('smart.mediaNotSupported'));
        return;
    }
    if(action === 'preview'){
        openImagePreview(nodeId, index);
        return;
    }
    if(action === 'detail'){
        // #87：画布缩略图「详情」统一走「生成详情」面板（与生图历史抽屉同一组件）。
        openSmartImageDetail(nodeId, index);
        return;
    }
    const modeMap = {crop:'crop', outpaint:'outpaint', mask:'mask', brush:'brush', grid:'grid'};
    openImageEditor(nodeId, index);
    setImageEditMode(modeMap[action] || 'preview', true);
    if(action === 'grid' && canGridJoinCurrentNode()){
        setGridOperationMode('join');
    }
}
// 智能分组顶部小菜单：整理排列 / 预览（整组左右切换）/ 宫格拼接 / 批量下载 / 解散分组。
// 与多图节点的 smart-node-floating-menu 同款样式与定位（选中分组时浮在卡片上方）。
function smartGroupToolbarHtml(node){
    if(!isSmartGroupNode(node)) return '';
    const hasContent = (node.images || []).some(img => img?.url) || smartGroupMembers(node).length > 0;
    const imageCount = (node.images || []).filter(img => img?.url).length;
    const actions = [
        {key:'arrange', icon:'layout-grid', label:tr('smart.groupToolbarArrange'), enabled:hasContent},
        {key:'preview', icon:'eye', label:tr('smart.modePreview'), enabled:imageCount > 0},
        {key:'grid', icon:'grid-3x3', label:tr('smart.nodeToolbarGridJoin'), enabled:imageCount > 1},
        {key:'download', icon:'archive', label:tr('smart.groupToolbarDownload'), enabled:imageCount > 0},
        {key:'ungroup', icon:'ungroup', label:tr('smart.groupToolbarUngroup'), enabled:true}
    ];
    return `<div class="smart-node-floating-menu" data-smart-group-menu="1">${actions.map(action => `
        <button type="button" data-smart-group-action="${escapeAttr(action.key)}" data-node-id="${escapeAttr(node.id)}" ${action.enabled ? '' : 'disabled'} title="${escapeAttr(action.label)}" aria-label="${escapeAttr(action.label)}">
            <i data-lucide="${escapeAttr(action.icon)}"></i><span>${escapeHtml(action.label)}</span>
        </button>`).join('')}</div>`;
}
function runSmartGroupToolbarAction(nodeId, action){
    const group = canvasState.nodes.find(n => n.id === nodeId);
    if(!isSmartGroupNode(group)) return;
    canvasState.selectedId = nodeId;
    canvasState.selectedIds = [];
    canvasState.selectedImage = {nodeId:'', index:-1};
    if(action === 'arrange'){
        if(arrangeSmartGroupMembers(group)){ render(); toast(tr('smart.groupArranged')); }
        else toast(tr('smart.groupNothingToArrange'));
        return;
    }
    if(action === 'ungroup'){ ungroupNode(nodeId); return; }
    // 图片已收进分组（group.images），预览/下载/拼接直接复用单节点机器（分组就是一个多图容器）。
    const imageCount = (group.images || []).filter(img => img?.url).length;
    if(!imageCount){ toast(tr('smart.groupNoImages')); return; }
    if(action === 'preview'){
        const first = (group.images || []).findIndex(img => img?.url);
        openImagePreview(nodeId, Math.max(0, first));
        return;
    }
    if(action === 'download'){ zipDownloadImageItems(group.title, (group.images || []).map(imageForDisplay)); return; }
    if(action === 'grid'){
        if(imageCount <= 1){ toast(tr('smart.groupNeedsTwoImages')); return; }
        const first = (group.images || []).findIndex(img => img?.url);
        openImageEditor(nodeId, Math.max(0, first));
        if(imageEditModal.classList.contains('open')){
            setImageEditMode('grid', true);
            setGridOperationMode('join');
        }
        return;
    }
}
function nowMs(){ return Date.now(); }
function formatRunDuration(ms){
    const total = Math.max(0, Math.floor(Number(ms || 0) / 1000));
    const min = Math.floor(total / 60);
    const sec = total % 60;
    return min ? `${min}:${String(sec).padStart(2, '0')}s` : `${sec}s`;
}
function nodeRunElapsedMs(node){
    if(!node) return 0;
    if(node.runFinishedAt && node.runStartedAt) return Number(node.runElapsedMs) || (Number(node.runFinishedAt) - Number(node.runStartedAt));
    if(node.runStartedAt) return nowMs() - Number(node.runStartedAt);
    return 0;
}
function runTimePillHtml(node){
    if(!node || node.runTimerHidden || node.type === 'smart-prompt') return '';
    const running = Boolean(node.pending || node.running || node.jimengPending);
    if(!running && !node.runFinishedAt) return '';
    const elapsed = nodeRunElapsedMs(node);
    // Bug fix: 已完成但耗时为 0 的节点（如重试/重新生成时 runStartedAt 与 runFinishedAt 相同）不显示计时器
    if(!running && elapsed <= 0) return '';
    const cls = running ? '' : ' done';
    return `<span class="run-time-pill${cls}" data-run-timer="${escapeHtml(node.id)}">${formatRunDuration(elapsed)}</span>`;
}
function hideRunTimerForNode(node){
    if(!node || node.runTimerHidden || node.pending || node.running || node.jimengPending || !node.runFinishedAt) return false;
    node.runTimerHidden = true;
    scheduleStateSave();
    return true;
}
function refreshRunTimerPills(){
    const active = canvasState.nodes.some(n => n.type !== 'smart-prompt' && !n.runTimerHidden && (n.pending || n.running || n.jimengPending || n.runFinishedAt));
    document.querySelectorAll('[data-run-timer]').forEach(el => {
        const node = canvasState.nodes.find(n => n.id === el.dataset.runTimer);
        if(!node || node.runTimerHidden || node.type === 'smart-prompt') {
            el.remove();
            return;
        }
        el.textContent = formatRunDuration(nodeRunElapsedMs(node));
        el.classList.toggle('done', Boolean(!node.pending && !node.running && !node.jimengPending && node.runFinishedAt));
    });
    if(active && !runTimerInterval) runTimerInterval = setInterval(refreshRunTimerPills, 1000);
    if(!active && runTimerInterval){ clearInterval(runTimerInterval); runTimerInterval = null; }
}
function syncCanvasEmptyState(){
    if(!canvasEmptyState) return;
    const hasCanvasNodes = canvasState.nodes.some(node => node && node.id !== SMART_LOG_PREVIEW_NODE_ID);
    canvasEmptyState.hidden = hasCanvasNodes;
}
function rememberInlineVideoActivations(){
    world.querySelectorAll('.image-node [data-image-index] video[data-inline-video-active="1"]').forEach(video => {
        const nodeEl = video.closest('.image-node');
        const itemEl = video.closest('[data-image-index]');
        const node = canvasState.nodes.find(n => n.id === nodeEl?.dataset.id);
        const index = Number(itemEl?.dataset.imageIndex ?? 0);
        const image = node?.images?.[index];
        if(image && mediaKindForItem(image) === 'video') image._inlineVideoActive = true;
    });
}
function render(){
    syncCanvasEmptyState();
    rememberInlineVideoActivations();
    world.classList.toggle('smart-multi-selected', selectedNodeIds().length > 1);
    const composerEl = composer;
    const mediaStates = captureMediaPlaybackStates();
    // 用户正在提示词框(contenteditable)输入时,本次重渲染不要移动 composer:
    // 移动 DOM 节点会打断输入法合成会话,导致输入中断(即使保留焦点描边也接不上)。
    const promptHadFocus = document.activeElement === promptInput;
    const reusableNodes = new Map();
    world.querySelectorAll('.image-node').forEach(el => {
        const node = canvasState.nodes.find(n => n.id === el.dataset.id);
        if(smartNodeHasLiveMedia(node)) reusableNodes.set(node.id, el);
    });
    const nodeHtmlEntries = canvasState.nodes
        .filter(node => node.id !== SMART_LOG_PREVIEW_NODE_ID)
        // 分组节点先渲染（DOM 靠前→层级在下），作为成员的背板；成员渲染在后、盖在分组之上，
        // 否则缩小分组把成员挪进卡片区域时会被分组卡片背景遮住而“消失”。
        .slice()
        .sort((a, b) => (isSmartGroupNode(a) ? 0 : 1) - (isSmartGroupNode(b) ? 0 : 1))
        .map(node => {
        const imgs = node.images || [];
        // Keep persisted titles raw until the final text interpolation.  The
        // Agent/group/conversation titles can be user-controlled; escaping
        // here and again in the template would render entities literally.
        const title = node.type === 'local-edit' ? (node.title || tr('smart.localEditTitle')) : node.type === 'smart-group' ? (node.title === '万能分组' ? tr('smart.groupTitleSmart') : (node.title || tr('smart.groupTitleSmart'))) : node.type === 'smart-conversation' ? (node.title || 'Conversation') : node.type === 'smart-agent' ? (node.title || 'Agent') : node.type === 'agent-task' ? 'Agent Task' : node.type === 'smart-prompt' ? 'Prompt' : node.type === 'smart-loop' ? 'Loop' : (imgs.length > 1 ? 'Group' : imgs.length ? 'Image' : tr('smart.createImportNode'));
        const scale = nodeScale(node);
        const layout = imageLayout(imgs, scale, node);
        const isPrompt = node.type === 'smart-prompt';
        const isLoop = node.type === 'smart-loop';
        const isConversation = isSmartConversationNode(node);
        const isAgent = isSmartAgentNode(node);
        const isAgentTask = isSmartAgentTaskNode(node);
        const isLocalEdit = node.type === 'local-edit';
        const sourceNodeId = isPrompt ? String(node.source_node_id || node.sourceNodeId || '') : '';
        const sourceMessageId = isPrompt ? String(node.source_message_id || node.sourceMessageId || '') : '';
        const isSmartGroup = node.type === 'smart-group';
        const isCompactMember = isSmartGroupCompactMember(node);
        const isImageNode = node.type === 'smart-image' || !node.type;
        const isJimengPending = Boolean(node.jimengPending && node.jimengPending.submitId && imgs.length === 0);
        const isQueued = Boolean(node.queued && imgs.length === 0 && !node.pending && !isJimengPending);
        const isEmpty = isImageNode && imgs.length === 0 && !node.pending && !isQueued && !isJimengPending;
        const isHistory = isHistoryGroupNode(node);
        const isGroup = isImageNode && imgs.length > 1;
        const isPending = Boolean(node.pending || isQueued || isJimengPending);
        const body = nodeBodyHtml(node, layout);
        // #149 B4: 多图节点（isGroup）也显示删除按钮（与单图一致，删整个节点）
        const deleteBtn = `<button class="mini-x node-delete" type="button" title="${escapeHtml(tr('smart.deleteNode'))}"><i data-lucide="trash-2"></i></button>`;
        const hint = isSmartGroup ? escapeHtml(tr('smart.hintGroup')) : isAgentTask ? escapeHtml(tr('smart.hintAgentTask')) : isPending ? escapeHtml(tr('smart.hintPending')) : (imgs.length > 1 ? escapeHtml(tr('smart.hintMulti')) : imgs.length ? escapeHtml(tr('smart.hintSingle')) : escapeHtml(tr('smart.hintEmpty')));
        const deletedInputCount = Array.isArray(node.deletedInputTombstones) ? node.deletedInputTombstones.length : 0;
        const deletedInputMarker = deletedInputCount
            ? `<div class="node-upstream-deleted-marker" role="status"><i data-lucide="unlink"></i><span>${escapeHtml(tr('canvas.upstreamMediaDeleted'))}</span></div>`
            : '';
        const agentCardStatus = isAgent ? agentCardStatusHtml(node.id) : '';
        const html = `<div class="image-node ${isEmpty ? 'empty-node' : ''} ${isGroup ? 'group-node' : ''} ${isHistory ? 'history-group-node' : ''} ${isPrompt ? 'prompt-smart-node' : ''} ${isLoop ? 'loop-smart-node' : ''} ${isConversation ? 'conversation-smart-node' : ''} ${isAgent ? 'agent-smart-node' : ''} ${isAgentTask ? 'agent-task-node' : ''} ${isLocalEdit ? 'local-edit-node' : ''} ${isSmartGroup ? 'smart-group-node' : ''} ${isCompactMember ? 'smart-group-member-node' : ''} ${isNodeSelected(node.id) ? 'selected' : ''} ${(dragState?.groupIds?.includes(node.id) || dragState?.id === node.id) ? 'dragging' : ''} ${node.running ? 'node-running' : ''} ${isPending ? 'node-pending' : ''}" data-id="${escapeHtml(node.id)}" data-source-node-id="${escapeHtml(sourceNodeId)}" data-source-message-id="${escapeHtml(sourceMessageId)}" style="left:${node.x || 0}px;top:${node.y || 0}px;width:${layout.width}px;height:${layout.height}px">
            <div class="node-head"><div class="node-title">${escapeHtml(title)}</div><div class="node-head-meta">${agentCardStatus}</div><div class="node-actions">${deleteBtn}</div></div>
            ${!isEmpty && !isGroup ? `<div class="floating-node-actions"><button class="mini-x node-delete" type="button" title="${escapeHtml(tr('smart.deleteNode'))}"><i data-lucide="trash-2"></i></button></div>` : ''}
            ${smartNodeToolbarHtml(node)}${smartGroupToolbarHtml(node)}
            ${runTimePillHtml(node)}
            ${deletedInputMarker}
            <div class="node-body">${body}</div>
            ${isCompactMember && (isPrompt || isLoop) ? '<div class="smart-group-member-grab" title="' + escapeHtml(tr('smart.dragToUngroup')) + '"></div>' : ''}
            <div class="node-hint">${hint}</div>
            ${(node.type === 'smart-image' || imgs.length || node.pending || isQueued || isJimengPending || isPrompt || isLoop || isConversation || isAgent || isAgentTask || isLocalEdit || isSmartGroup) && !isLockedBatchGenNode(node) ? '<div class="node-resize-handle" data-resize="1"></div>' : ''}
            <div class="node-port port-in" data-port="in" title="input"></div>
            <div class="node-port port-out" data-port="out" title="output"></div>
        </div>`;
        return {node, html};
    });
    const tpl = document.createElement('template');
    tpl.innerHTML = nodeHtmlEntries.map(entry => entry.html).join('');
    const renderedNodeEls = new Map();
    nodeHtmlEntries.forEach(entry => {
        const fresh = tpl.content.querySelector(`.image-node[data-id="${CSS.escape(entry.node.id)}"]`);
        if(fresh) renderedNodeEls.set(entry.node.id, fresh);
    });
    const keepEls = new Set();
    reusableNodes.forEach(el => keepEls.add(el));
    if(promptNodeFlowView.mode === 'flow' && promptNodeFlowView.element?.isConnected){
        keepEls.add(promptNodeFlowView.element);
    }
    if(composerEl) keepEls.add(composerEl);
    [...world.childNodes].forEach(child => {
        if(!keepEls.has(child)) child.remove();
    });
    // 用户正在提示词框输入时不要移动 composer:移动 DOM 会打断输入法合成、中断输入。
    // composer 已在 keepEls 中(未被移除),不重排也不影响显示(z-index 固定)。
    if(composerEl && !promptHadFocus) world.appendChild(composerEl);
    world.insertAdjacentHTML('beforeend', renderConnections());
    nodeHtmlEntries.forEach(entry => {
        if(promptNodeFlowView.mode === 'flow' && promptNodeFlowView.nodeId === entry.node.id && promptNodeFlowView.element?.isConnected) return;
        const fresh = renderedNodeEls.get(entry.node.id);
        if(!fresh) return;
        world.appendChild(fresh);
        const reusable = reusableNodes.get(entry.node.id);
        if(reusable){
            transplantSmartMediaElements(reusable, fresh);
            if(reusable !== fresh) reusable.remove();
        }
    });
    restoreMediaPlaybackStates(mediaStates);
    bindNodeEvents();
    bindConnectionEvents();
    updateComposer();
    renderMinimap();
    refreshIcons();
    bindSmartPreviewImageFallbacks(world);
    syncSmartSelectedImageResolution(world);
    measureSmartNodeImages();
    scheduleSmartNodeFloatingMenuPosition();
    refreshRunTimerPills();
    return;
    world.innerHTML = '';
    if(composerEl) world.appendChild(composerEl);
    world.insertAdjacentHTML('beforeend', renderConnections());
    const nodesHtml = canvasState.nodes.map(node => {
        const imgs = node.images || [];
        const title = node.type === 'smart-prompt' ? 'Prompt' : node.type === 'smart-loop' ? 'Loop' : (imgs.length > 1 ? 'Group' : 'Image');
        const scale = nodeScale(node);
        const layout = imageLayout(imgs, scale, node);
        const isPrompt = node.type === 'smart-prompt';
        const isLoop = node.type === 'smart-loop';
        const isImageNode = node.type === 'smart-image' || !node.type;
        const isQueued = Boolean(node.queued && imgs.length === 0 && !node.pending);
        const isEmpty = isImageNode && imgs.length === 0 && !node.pending && !isQueued;
        const isGroup = isImageNode && imgs.length > 1;
        const isPending = (node.pending || isQueued) && imgs.length === 0;
        const body = nodeBodyHtml(node, layout);
        const deleteBtn = isGroup ? '' : `<button class="mini-x node-delete" type="button" title="${escapeHtml(tr('smart.deleteNode'))}"><i data-lucide="trash-2"></i></button>`;
        return `<div class="image-node ${isEmpty ? 'empty-node' : ''} ${isGroup ? 'group-node' : ''} ${isPrompt ? 'prompt-smart-node' : ''} ${isLoop ? 'loop-smart-node' : ''} ${isNodeSelected(node.id) ? 'selected' : ''} ${(dragState?.groupIds?.includes(node.id) || dragState?.id === node.id) ? 'dragging' : ''} ${node.running ? 'node-running' : ''} ${isPending ? 'node-pending' : ''}" data-id="${escapeHtml(node.id)}" style="left:${node.x || 0}px;top:${node.y || 0}px;width:${layout.width}px;height:${layout.height}px">
            <div class="node-head"><div class="node-title">${title}</div><div class="node-actions">${deleteBtn}</div></div>
            ${!isEmpty && !isGroup ? `<div class="floating-node-actions"><button class="mini-x node-delete" type="button" title="${escapeHtml(tr('smart.deleteNode'))}"><i data-lucide="trash-2"></i></button></div>` : ''}
            ${smartNodeToolbarHtml(node)}
            ${runTimePillHtml(node)}
            <div class="node-body">${body}</div>
            <div class="node-hint">${isPending ? escapeHtml(tr('smart.hintPending')) : (imgs.length > 1 ? escapeHtml(tr('smart.hintMulti')) : imgs.length ? escapeHtml(tr('smart.hintSingle')) : escapeHtml(tr('smart.hintEmpty')))}</div>
            ${(node.type === 'smart-image' || imgs.length || node.pending || isQueued || isPrompt || isLoop) && !isLockedBatchGenNode(node) ? '<div class="node-resize-handle" data-resize="1"></div>' : ''}
            <div class="node-port port-in" data-port="in" title="${escapeHtml(tr('smart.portIn'))}"></div>
            <div class="node-port port-out" data-port="out" title="${escapeHtml(tr('smart.portOut'))}"></div>
        </div>`;
    }).join('');
    world.insertAdjacentHTML('beforeend', nodesHtml);
    bindNodeEvents();
    bindConnectionEvents();
    updateComposer();
    renderMinimap();
    refreshIcons();
    measureSmartNodeImages();
    scheduleSmartNodeFloatingMenuPosition();
    refreshRunTimerPills();
    updateSmartMarkedBadge();
}
function measureSmartNodeImages(){
    world.querySelectorAll('.image-node img,.image-node video').forEach(imgEl => {
        const nodeEl = imgEl.closest('.image-node');
        const itemEl = imgEl.closest('[data-image-index]');
        const containerNode = canvasState.nodes.find(n => n.id === nodeEl?.dataset.id);
        const targetNodeId = itemEl?.dataset.refNodeId || nodeEl?.dataset.id;
        const index = Number(itemEl?.dataset.refImageIndex ?? itemEl?.dataset.imageIndex ?? 0);
        const node = canvasState.nodes.find(n => n.id === targetNodeId);
        const image = node?.images?.[index];
        if(imgEl.tagName?.toLowerCase() === 'img' && image?.url) bindImageProxyFallback(imgEl, image);
        if(!node || !image || image.natural_w || image.natural_h) return;
        const isPreview = isSmartPreviewImage(imgEl);
        const originalSrc = imgEl.dataset?.originalSrc || image.url || '';
        if(isPreview && imgEl.dataset?.previewKind !== 'video' && originalSrc && !image._naturalSizeLoading){
            image._naturalSizeLoading = true;
            loadSmartOriginalImageDimensions(originalSrc).then(size => {
                image._naturalSizeLoading = false;
                if(!size || image.natural_w || image.natural_h) return;
                image.natural_w = size.w;
                image.natural_h = size.h;
                delete image.layout_w;
                delete image.layout_h;
                applyThumbDisplaySizeToElement(itemEl, image, Math.max(itemEl?.clientWidth || 0, itemEl?.clientHeight || 0));
                updateImageResolutionBadgeElement(itemEl, image);
                if(!isSmartGroupNode(node) && (node.images || []).length === 1 && !node.w && !node.h){
                    const layout = singleImageLayout(image, node, mediaNodeDefaultScale(node));
                    node.w = layout.width;
                    node.h = layout.height;
                }
                updateNodeElementDuringResize(node);
                if(containerNode && containerNode.id !== node.id) updateNodeElementDuringResize(containerNode);
                if(isNodeSelected(node.id)) updateComposer();
                scheduleSave();
            });
        }
        if(isPreview && image.layout_w && image.layout_h) return;
        const apply = () => {
            const w = imgEl.naturalWidth || imgEl.videoWidth || 0;
            const h = imgEl.naturalHeight || imgEl.videoHeight || 0;
            if(w <= 0 || h <= 0 || image.natural_w || image.natural_h) return;
            const prevW = Number(image.layout_w || 0);
            const prevH = Number(image.layout_h || 0);
            if(isPreview){
                if(prevW === w && prevH === h) return;
                image.layout_w = w;
                image.layout_h = h;
            } else {
                image.natural_w = w;
                image.natural_h = h;
                delete image.layout_w;
                delete image.layout_h;
            }
            applyThumbDisplaySizeToElement(itemEl, image, Math.max(itemEl?.clientWidth || 0, itemEl?.clientHeight || 0));
            updateImageResolutionBadgeElement(itemEl, image);
            if(!isSmartGroupNode(node) && (node.images || []).length === 1 && !node.w && !node.h){
                const layout = singleImageLayout(image, node, mediaNodeDefaultScale(node));
                node.w = layout.width;
                node.h = layout.height;
            }
            updateNodeElementDuringResize(node);
            if(containerNode && containerNode.id !== node.id) updateNodeElementDuringResize(containerNode);
            if(isNodeSelected(node.id)) updateComposer();
            scheduleStateSave();
        };
        const isVideo = imgEl.tagName?.toLowerCase() === 'video';
        if(!isVideo && imgEl.complete) apply();
        else imgEl.addEventListener('load', apply, {once:true});
        imgEl.addEventListener('loadedmetadata', apply, {once:true});
    });
}
function ensurePortDragPathElement(){
    const svg = world.querySelector('svg.connection-layer');
    if(!svg) return null;
    let path = svg.querySelector('path.port-drag-temp');
    if(!path){
        path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('class', 'port-drag-temp conn-pending');
        path.setAttribute('stroke', 'rgba(100,116,139,0.92)');
        path.setAttribute('stroke-width', '1.9');
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke-linecap', 'round');
        svg.appendChild(path);
    }
    return path;
}
function clearPortDragVisual(){
    world.querySelector('path.port-drag-temp')?.remove();
    world.querySelectorAll('.node-port.is-active').forEach(el => el.classList.remove('is-active'));
    world.querySelectorAll('.image-node.port-hover').forEach(el => el.classList.remove('port-hover'));
}
function bindPromptNodeControls(el, node){
    el.querySelectorAll('.prompt-node-control, .prompt-node-pill').forEach(control => {
        control.addEventListener('mousedown', e => e.stopPropagation());
        control.addEventListener('click', e => e.stopPropagation());
        control.addEventListener('dblclick', e => e.stopPropagation());
    });
    const textEl = el.querySelector('.prompt-node-text');
    const flowToggle = el.querySelector('.prompt-node-flow-toggle');
    if(flowToggle){
        flowToggle.addEventListener('mousedown', e => {
            if(e.button !== 0) return;
            e.preventDefault();
            e.stopPropagation();
        });
        const activate = e => {
            e.preventDefault();
            e.stopPropagation();
            if(promptNodeFlowView.mode === 'flow' && promptNodeFlowView.nodeId === node.id) exitPromptNodeFlow();
            else enterPromptNodeFlow(node.id, flowToggle);
        };
        flowToggle.onclick = activate;
        flowToggle.onkeydown = event => {
            if(event.key === 'Enter' || event.key === ' ') activate(event);
        };
    }
    if(textEl) {
        bindScrollableText(textEl);
        textEl.addEventListener('compositionstart', () => { promptNodeFlowView.compositionActive = true; });
        textEl.addEventListener('compositionend', () => { promptNodeFlowView.compositionActive = false; });
        textEl.addEventListener('keydown', event => {
            if(event.key === 'Escape' && promptNodeFlowView.mode === 'flow' && promptNodeFlowView.nodeId === node.id){
                event.preventDefault();
                event.stopPropagation();
                exitPromptNodeFlow();
            }
        });
        textEl.oninput = e => {
            const prevExtra = promptNodeSplitExtraHeight(node);
            node.text = e.target.value;
            refreshPromptNodeSegmentsUi(el, node);
            if(node.promptSplitEnabled === true){
                syncPromptNodeHeightForSplit(node, prevExtra);
                updateNodeElementDuringResize(node);
            }
            scheduleSave();
        };
    }
    const separatorEl = el.querySelector('.prompt-node-separator');
    if(separatorEl) {
        separatorEl.oninput = e => {
            const prevExtra = promptNodeSplitExtraHeight(node);
            node.promptSeparator = e.target.value || ';';
            refreshPromptNodeSegmentsUi(el, node);
            syncPromptNodeHeightForSplit(node, prevExtra);
            updateNodeElementDuringResize(node);
            scheduleSave();
        };
    }
    const splitToggle = el.querySelector('.prompt-split-toggle');
    if(splitToggle) splitToggle.onclick = e => {
        e.preventDefault();
        e.stopPropagation();
        const prevExtra = promptNodeSplitExtraHeight(node);
        node.promptSplitEnabled = node.promptSplitEnabled !== true;
        if(node.promptSplitEnabled){
            node.promptSeparator = promptNodeSeparator(node);
        }
        syncPromptNodeHeightForSplit(node, prevExtra);
        render();
        scheduleSave();
    };
    const presetEdit = el.querySelector('.prompt-preset-edit');
    if(presetEdit) presetEdit.onclick = e => {
        e.preventDefault();
        e.stopPropagation();
        editPromptPresetForNode(node);
    };
    const toggle = el.querySelector('.prompt-llm-toggle');
    if(toggle) toggle.onclick = e => {
        e.preventDefault(); e.stopPropagation();
        node.llmEnabled = !node.llmEnabled;
        if(node.llmEnabled){
            node.llmProvider = resolveChatProviderId(node.llmProvider || '');
            node.llmModel = resolveChatModel(node.llmModel || '', node.llmProvider);
            node.h = Math.max(Number(node.h) || 0, promptNodeExpandedHeight(node));
            node.w = Math.max(Number(node.w) || 0, 316);
        } else {
            node.h = promptNodeMinHeight(node);
            node.w = Math.max(Number(node.w) || 0, 316);
        }
        render();
        scheduleSave();
    };
    const providerEl = el.querySelector('.prompt-llm-provider');
    if(providerEl) providerEl.onchange = e => {
        e.stopPropagation();
        node.llmProvider = resolveChatProviderId(e.target.value);
        node.llmModel = resolveChatModel('', node.llmProvider);
        render();
        scheduleSave();
    };
    const modelEl = el.querySelector('.prompt-llm-model');
    if(modelEl) modelEl.onchange = e => { e.stopPropagation(); node.llmModel = e.target.value; scheduleSave(); };
    const systemToggleEl = el.querySelector('.prompt-system-toggle');
    if(systemToggleEl) systemToggleEl.onclick = e => {
        e.preventDefault();
        e.stopPropagation();
        const prevHeight = Number(node.h) || 0;
        node.llmSystemEnabled = !node.llmSystemEnabled;
        if(node.llmSystemEnabled) node.h = Math.max(prevHeight, promptNodeExpandedHeight(node));
        else if(prevHeight <= 364) node.h = promptNodeExpandedHeight(node);
        render();
        scheduleSave();
    };
    const systemEl = el.querySelector('.prompt-llm-system');
    if(systemEl) { bindScrollableText(systemEl); systemEl.oninput = e => { node.llmSystemPrompt = e.target.value; scheduleSave(); }; }
    const instructionEl = el.querySelector('.prompt-llm-instruction');
    if(instructionEl) { bindScrollableText(instructionEl); instructionEl.oninput = e => { node.llmInstruction = e.target.value; scheduleSave(); }; }
    const instructionResizeEl = el.querySelector('[data-llm-instruction-resize]');
    if(instructionResizeEl) instructionResizeEl.addEventListener('mousedown', e => {
        if(e.button !== 0) return;
        e.preventDefault(); e.stopPropagation();
        llmInstructionResizeState = {id:node.id, startY:e.clientY, startH:promptLlmInstructionHeight(node), startNodeH:promptNodeLayoutSize(node).height};
        // 拖动期间屏蔽文本框的指针/选区，否则上拉时光标滑到上方输入框会被它抢走（选中文字/失焦）。
        document.body.classList.add('smart-node-resize', 'smart-llm-instr-resize');
        capturePendingUndo();
    });
    const splitResizeEl = el.querySelector('[data-prompt-split-resize]');
    if(splitResizeEl) splitResizeEl.addEventListener('mousedown', e => {
        if(e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        promptSplitResizeState = {id:node.id, startY:e.clientY, startH:promptNodeSplitPreviewHeight(node), startNodeH:promptNodeLayoutSize(node).height};
        document.body.classList.add('smart-node-resize', 'smart-prompt-split-resize');
        capturePendingUndo();
    });
    const runEl = el.querySelector('.prompt-node-run');
    if(runEl) runEl.onclick = e => { e.preventDefault(); e.stopPropagation(); runPromptLLMNode(node.id); };
}
function bindLoopNodeControls(el, node){
    el.querySelectorAll('.loop-smart-control').forEach(control => {
        control.addEventListener('mousedown', e => e.stopPropagation());
        control.addEventListener('click', e => e.stopPropagation());
        control.addEventListener('dblclick', e => e.stopPropagation());
    });
    const loopNumberBounds = key => {
        if(key === 'loopStart') return {min:1, max:9999};
        if(key === 'imageBatchSize') return {min:1, max:100};
        return {min:1, max:100};
    };
    const normalizeLoopNumber = (key, rawValue) => {
        const bounds = loopNumberBounds(key);
        return Math.max(bounds.min, Math.min(bounds.max, Number(rawValue) || bounds.min));
    };
    const syncLoopNumberUi = (source, key, value) => {
        const control = source?.closest?.('.loop-number-control');
        if(!control) return;
        const display = control.querySelector('.loop-number-trigger strong');
        if(display) display.textContent = value;
        control.querySelectorAll('[data-loop-value]').forEach(cell => {
            cell.classList.toggle('active', Number(cell.dataset.loopValue) === value);
        });
    };
    const setLoopNumber = (key, rawValue, rerender=true, source=null) => {
        const value = normalizeLoopNumber(key, rawValue);
        if(key === 'count') node.count = smartLoopCount({count:value});
        if(key === 'loopStart') node.loopStart = value;
        if(key === 'imageBatchSize') node.imageBatchSize = value;
        scheduleSave();
        if(rerender) render();
        else syncLoopNumberUi(source, key, value);
    };
    el.querySelectorAll('[data-loop-number]').forEach(btn => {
        btn.onclick = e => {
            e.preventDefault();
            e.stopPropagation();
            setLoopNumber(btn.dataset.loopNumber, btn.dataset.loopValue, true);
        };
    });
    el.querySelectorAll('[data-loop-number-input]').forEach(input => {
        input.oninput = e => {
            e.stopPropagation();
            setLoopNumber(input.dataset.loopNumberInput, input.value, false, input);
        };
        input.onchange = e => {
            e.stopPropagation();
            setLoopNumber(input.dataset.loopNumberInput, input.value, true);
        };
    });
    el.querySelectorAll('[data-loop-mode]').forEach(btn => {
        btn.onclick = e => {
            e.preventDefault();
            e.stopPropagation();
            node.mode = btn.dataset.loopMode === 'parallel' ? 'parallel' : 'serial';
            render();
            scheduleSave();
        };
    });
    el.querySelectorAll('[data-loop-toggle]').forEach(btn => {
        btn.onclick = e => {
            e.preventDefault();
            e.stopPropagation();
            if(btn.dataset.loopToggle === 'image') node.imageInput = !node.imageInput;
            if(btn.dataset.loopToggle === 'prompt') {
                node.showPrompt = !node.showPrompt;
                if(node.showPrompt && !smartLoopInputPromptItems(node).length && !smartLoopActivePromptFieldValues(node).length) setSmartLoopPromptFieldValues(node, [smartLoopDefaultPromptText()]);
            }
            fitSmartLoopNode(node);
            render();
            scheduleSave();
        };
    });
    const syncPromptFieldsFromDom = () => {
        const values = [...el.querySelectorAll('[data-loop-prompt-index]')]
            .sort((a, b) => Number(a.dataset.loopPromptIndex) - Number(b.dataset.loopPromptIndex))
            .map(input => smartLoopEditorText(input));
        setSmartLoopPromptFieldValues(node, values);
    };
    let activePromptEditor = null;
    el.querySelectorAll('.loop-smart-text').forEach(text => {
        bindScrollableText(text);
        text.onfocus = () => { activePromptEditor = text; };
        text.oninput = () => { syncPromptFieldsFromDom(); scheduleSave(); };
        text.addEventListener('paste', e => {
            e.preventDefault();
            const textContent = e.clipboardData?.getData('text/plain') || '';
            if(!textContent) return;
            const sel = window.getSelection();
            if(!sel.rangeCount) return;
            const range = sel.getRangeAt(0);
            range.deleteContents();
            range.insertNode(document.createTextNode(textContent));
            range.collapse(false);
            sel.removeAllRanges();
            sel.addRange(range);
        });
        text.addEventListener('click', e => {
            const remove = e.target.closest?.('.loop-smart-token-chip button');
            if(!remove) return;
            e.preventDefault();
            e.stopPropagation();
            remove.closest('.loop-smart-token-chip')?.remove();
            syncPromptFieldsFromDom();
            scheduleSave();
        });
    });
    el.querySelectorAll('[data-loop-prompt-add]').forEach(btn => {
        btn.onclick = e => {
            e.preventDefault();
            e.stopPropagation();
            syncPromptFieldsFromDom();
            const values = smartLoopPromptFieldValues(node);
            setSmartLoopPromptFieldValues(node, [...values, '']);
            fitSmartLoopNode(node);
            render();
            scheduleSave();
        };
    });
    el.querySelectorAll('[data-loop-prompt-delete]').forEach(btn => {
        btn.onclick = e => {
            e.preventDefault();
            e.stopPropagation();
            syncPromptFieldsFromDom();
            const removeIndex = Number(btn.dataset.loopPromptDelete);
            const values = smartLoopPromptFieldValues(node);
            if(values.length <= 1) return;
            values.splice(removeIndex, 1);
            setSmartLoopPromptFieldValues(node, values);
            fitSmartLoopNode(node);
            render();
            scheduleSave();
        };
    });
    const firstText = el.querySelector('.loop-smart-text');
    const targetPromptEditor = () => activePromptEditor && el.contains(activePromptEditor) ? activePromptEditor : firstText;
    el.querySelectorAll('[data-loop-token]').forEach(btn => {
        btn.onclick = e => {
            e.preventDefault();
            e.stopPropagation();
            const text = targetPromptEditor();
            if(!text) return;
            const token = btn.dataset.loopToken || '《计数》';
            insertSmartLoopToken(text, token);
            syncPromptFieldsFromDom();
            scheduleSave();
        };
    });
    el.querySelectorAll('[data-loop-run]').forEach(btn => {
        btn.onclick = e => {
            e.preventDefault();
            e.stopPropagation();
            const loopId = btn.dataset.loopRun || node.id;
            if(smartCascadeIsLoopRunning(loopId)){
                requestSmartCascadeStop(loopId);
                return;
            }
            runSmartCascadeFromLoop(loopId);
        };
    });
}
function bindScrollableText(el){
    if(!el || el.dataset.scrollBound === '1') return;
    el.dataset.scrollBound = '1';
    const stop = e => e.stopPropagation();
    const beginSelection = e => {
        e.stopPropagation();
        textSelectionGuard = {
            el,
            scrollTop:el.scrollTop || 0,
            scrollLeft:el.scrollLeft || 0,
            clientY:e.clientY,
            wheelUntil:0,
            active:true
        };
    };
    el.addEventListener('mousedown', beginSelection);
    el.addEventListener('mousemove', e => {
        e.stopPropagation();
        if(textSelectionGuard?.el === el) textSelectionGuard.clientY = e.clientY;
    });
    el.addEventListener('mouseup', e => {
        e.stopPropagation();
        if(textSelectionGuard?.el === el) textSelectionGuard.active = false;
    });
    el.addEventListener('mouseleave', e => {
        e.stopPropagation();
        if(textSelectionGuard?.el === el) {
            el.scrollTop = textSelectionGuard.scrollTop;
            el.scrollLeft = textSelectionGuard.scrollLeft;
        }
    });
    el.addEventListener('scroll', () => {
        const guard = textSelectionGuard;
        if(!guard || guard.el !== el || !guard.active || Date.now() < guard.wheelUntil) {
            if(guard?.el === el) {
                guard.scrollTop = el.scrollTop || 0;
                guard.scrollLeft = el.scrollLeft || 0;
            }
            return;
        }
        const nextTop = el.scrollTop || 0;
        const prevTop = guard.scrollTop || 0;
        const rect = el.getBoundingClientRect();
        const pointerBelow = Number.isFinite(guard.clientY) && guard.clientY > rect.bottom - 10;
        const pointerAbove = Number.isFinite(guard.clientY) && guard.clientY < rect.top + 10;
        const jumpedToTop = prevTop > Math.max(80, el.clientHeight * 0.45) && nextTop < 4 && !pointerAbove;
        const wrongDirectionJump = pointerBelow && nextTop < prevTop - Math.max(40, el.clientHeight * 0.25);
        if(jumpedToTop || wrongDirectionJump) {
            requestAnimationFrame(() => {
                if(textSelectionGuard?.el === el && textSelectionGuard.active) {
                    el.scrollTop = prevTop;
                    el.scrollLeft = guard.scrollLeft || 0;
                }
            });
            return;
        }
        guard.scrollTop = nextTop;
        guard.scrollLeft = el.scrollLeft || 0;
    }, {passive:true});
    el.addEventListener('click', stop);
    el.addEventListener('dblclick', stop);
    el.addEventListener('wheel', e => {
        e.stopPropagation();
        if(textSelectionGuard?.el === el) textSelectionGuard.wheelUntil = Date.now() + 180;
    }, {passive:true});
}
function updatePortDragVisual(){
    if(!portDragState) return;
    const fromNode = canvasState.nodes.find(n => n.id === portDragState.fromId);
    if(!fromNode) return;
    const fr = nodeRect(fromNode);
    const isOut = portDragState.fromPort === 'out';
    const fx = isOut ? fr.x + fr.width : fr.x;
    const fy = fr.y + fr.height / 2;
    const tx = portDragState.currentWorld.x;
    const ty = portDragState.currentWorld.y;
    const dx = Math.max(50, Math.abs(tx - fx) * 0.45);
    const sign = isOut ? 1 : -1;
    const path = ensurePortDragPathElement();
    if(path) path.setAttribute('d', `M${fx} ${fy} C ${fx + dx * sign} ${fy}, ${tx - dx * sign} ${ty}, ${tx} ${ty}`);
    world.querySelectorAll('.node-port.is-active').forEach(el => el.classList.remove('is-active'));
    world.querySelectorAll('.image-node.port-hover').forEach(el => el.classList.remove('port-hover'));
    if(portDragState.hoverTargetId){
        const targetNodeEl = world.querySelector(`.image-node[data-id="${portDragState.hoverTargetId}"]`);
        targetNodeEl?.classList.add('port-hover');
        targetNodeEl?.querySelector(`.node-port[data-port="${portDragState.hoverPort}"]`)?.classList.add('is-active');
    }
}
function handlePortDrop(drag, e){
    const {targetId, targetPort, hit} = (() => {
        const hitEl = document.elementFromPoint(e.clientX, e.clientY);
        const portEl = hitEl?.closest?.('.node-port');
        const nodeEl = portEl?.closest?.('.image-node') || hitEl?.closest?.('.image-node');
        let id = '', port = '';
        if(nodeEl && nodeEl.dataset.id && nodeEl.dataset.id !== drag.fromId){
            id = nodeEl.dataset.id;
            if(portEl){
                port = portEl.dataset.port;
            } else {
                const rect = nodeEl.getBoundingClientRect();
                port = (e.clientX - rect.left) < rect.width / 2 ? 'in' : 'out';
            }
        }
        return {targetId:id, targetPort:port, hit:hitEl};
    })();
    if(targetId){
        const compatible = (drag.fromPort === 'out' && targetPort === 'in') || (drag.fromPort === 'in' && targetPort === 'out');
        if(!compatible){ discardPendingUndo(); render(); return; }
        const fromId = drag.fromPort === 'out' ? drag.fromId : targetId;
        const toId = drag.fromPort === 'out' ? targetId : drag.fromId;
        if(connectInputNode(fromId, toId)){
            commitPendingUndo();
            render();
            scheduleSave();
        } else {
            // #149 C6: 连线被拒时 toast 提示原因（不再静默）。
            // C8 的 agent-task / prompt→conversation 已在 connectInputNode 内 toast，此处跳过避免重复。
            const toNode = canvasState.nodes.find(n => n.id === toId);
            const fromNode = canvasState.nodes.find(n => n.id === fromId);
            const c8Handled = Boolean(toNode) && (toNode.type === 'agent-task' || (fromNode?.type === 'smart-prompt' && toNode.type === 'smart-conversation'));
            if(toNode && !c8Handled){
                if(toNode.type === 'local-edit' && !toNode.localEditActiveRole){
                    toast(tr('smart.connectLocalEditNoRole'));
                } else if(toNode.type === 'smart-loop' && !toNode.imageInput && !toNode.showPrompt){
                    toast(tr('smart.connectLoopNoInput'));
                } else {
                    toast(tr('smart.connectRejected'));
                }
            }
            discardPendingUndo();
            render();
        }
        return;
    }
    if(!drag.moved){ discardPendingUndo(); render(); return; }
    if(hit?.closest?.('.composer,.smart-settings-modal,.smart-settings,.smart-back,.asset-panel,.asset-toggle,.smart-log-toggle,.smart-shortcut-toggle,.log-modal,.shortcut-modal,.image-edit-modal,.smart-minimap')){
        discardPendingUndo(); render(); return;
    }
    const p = screenToWorld(e);
    undoSuppressed = true;
    const newNode = createImageNodeAt(p, [], {select:true, skipUndo:true});
    undoSuppressed = false;
    const fromId = drag.fromPort === 'out' ? drag.fromId : newNode.id;
    const toId = drag.fromPort === 'out' ? newNode.id : drag.fromId;
    connectInputNode(fromId, toId);
    commitPendingUndo();
    render();
    scheduleSave();
}
function pickMediaForSmartNode(nodeId){
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*,video/*,audio/*';
    input.multiple = true;
    input.onchange = () => {
        if(input.files?.length) handleFiles(input.files, nodeId);
        input.remove();
    };
    input.style.position = 'fixed';
    input.style.left = '-9999px';
    input.style.top = '-9999px';
    input.style.opacity = '0';
    document.body.appendChild(input);
    input.click();
}
function conversationProjectId(){
    return canvas?.project_id || canvas?.project || sourceProjectId || '';
}
function conversationRequestId(){
    const suffix = Math.random().toString(36).slice(2);
    return `conversation-${Date.now().toString(36)}-${suffix}`;
}
function conversationTaskUrl(projectId, nodeId, requestId){
    return `/api/project-library/${encodeURIComponent(projectId)}/conversation/${encodeURIComponent(nodeId)}/messages/${encodeURIComponent(requestId)}`;
}
function conversationSleep(ms){ return new Promise(resolve => setTimeout(resolve, ms)); }
const CONVERSATION_MAX_NOT_FOUND_POLLS = 30;
function conversationMessageForRequest(node, requestId){
    return (node?.messages || []).find(item => String(item?.request_id || '') === String(requestId) && item.role === 'user') || null;
}
function ensureLocalPendingConversationMessage(node, text, requestId){
    if(!node || conversationMessageForRequest(node, requestId)) return;
    node.messages = [...(node.messages || []), {
        id:`local-${requestId}`,
        role:'user',
        status:'pending',
        request_id:requestId,
        text,
        references:conversationConnectedReferences(node),
        created_at:Date.now(),
    }].slice(-500);
}
function abandonUnregisteredConversationTask(node, requestId){
    const liveNode = canvasState.nodes.find(item => item.id === node?.id) || node;
    if(!liveNode) return;
    const before = (liveNode.messages || []).length;
    liveNode.messages = (liveNode.messages || []).filter(item => !(
        item?.role === 'user'
        && String(item?.request_id || '') === String(requestId)
        && String(item?.id || '').startsWith('local-')
        && item?.status === 'pending'
    ));
    if(liveNode.messages.length !== before){
        render();
        toast(tr('smart.conversationTaskReleased'));
    }
}
function startConversationTaskPolling(node, requestId){
    if(!node || !requestId || conversationTaskPolls.has(requestId)) return;
    const pollState = {cancelled:false};
    conversationTaskPolls.set(requestId, pollState);
    (async() => {
        const projectId = conversationProjectId();
        const deadline = Date.now() + 30 * 60 * 1000;
        let notFoundPolls = 0;
        try {
            while(!pollState.cancelled && Date.now() < deadline){
                let response = null;
                let controller = null;
                let timeoutId = null;
                try {
                    controller = new AbortController();
                    timeoutId = setTimeout(() => controller.abort(), 15000);
                    response = await fetch(conversationTaskUrl(projectId, node.id, requestId), {signal:controller.signal});
                } catch(_) {
                    response = null;
                } finally {
                    if(timeoutId) clearTimeout(timeoutId);
                }
                if(response?.ok){
                    notFoundPolls = 0;
                    const data = await response.json().catch(() => ({}));
                    if(data.canvas) applyConversationCanvasResponse(data.canvas);
                    if(data.status === 'completed' || data.status === 'failed'){
                        if(data.status === 'failed') toast(data.assistant?.error || data.message?.error || tr('smart.conversationTaskFailed'));
                        render();
                        return;
                    }
                    render();
                } else if(response?.status === 404){
                    // The initial POST may still be committing. A bounded
                    // window avoids leaving an AbortError placeholder pending
                    // for 30 minutes when the request was never registered.
                    notFoundPolls += 1;
                    if(notFoundPolls >= CONVERSATION_MAX_NOT_FOUND_POLLS){
                        abandonUnregisteredConversationTask(node, requestId);
                        return;
                    }
                }
                await conversationSleep(1000);
            }
        } finally {
            if(conversationTaskPolls.get(requestId) === pollState) conversationTaskPolls.delete(requestId);
        }
    })();
}
function resumeConversationTasks(){
    canvasState.nodes.filter(isSmartConversationNode).forEach(node => {
        (node.messages || []).filter(item => item?.role === 'user' && item.status === 'pending' && item.request_id)
            .forEach(item => startConversationTaskPolling(node, item.request_id));
    });
}
function ensureConversationExtractionMenu(){
    let menu = document.querySelector('[data-extract-prompt-selection]');
    if(menu) return menu;
    menu = document.createElement('div');
    menu.className = 'conversation-extract-menu';
    menu.dataset.extractPromptSelection = '';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-hidden', 'true');
    menu.innerHTML = '<button type="button" role="menuitem" tabindex="0" aria-label="' + escapeHtml(tr('smart.extractSelectedText')) + '" data-extract-prompt-action><i data-lucide="text-cursor-input"></i><span>' + escapeHtml(tr('smart.extractSelectedText')) + '</span></button>';
    menu.addEventListener('mousedown', event => event.stopPropagation());
    menu.addEventListener('click', event => {
        event.stopPropagation();
        if(event.target.closest('[data-extract-prompt-action]')) extractConversationSelection();
    });
    menu.addEventListener('keydown', event => {
        if(event.key === 'Escape'){
            event.preventDefault();
            closeConversationExtractionMenu();
            return;
        }
        if((event.key === 'Enter' || event.key === ' ') && event.target.closest('[data-extract-prompt-action]')){
            event.preventDefault();
            extractConversationSelection();
        }
    });
    document.body.appendChild(menu);
    return menu;
}
function conversationSelectionFromDom(messageEl){
    const selection = window.getSelection?.();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    const text = String(selection?.toString() || '');
    if(!text.trim() || !range || !messageEl?.contains(range.commonAncestorContainer)) return null;
    return {
        text,
        sourceNodeId:messageEl.closest('.image-node')?.dataset.id || '',
        sourceMessageId:messageEl.dataset.messageId || '',
    };
}
function rememberConversationTextSelection(){
    const selection = window.getSelection?.();
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    const text = String(selection?.toString() || '');
    if(!text.trim() || !range) return;
    const container = range.commonAncestorContainer;
    const element = container?.nodeType === 1 ? container : container?.parentElement;
    const messageEl = element?.closest?.('.conversation-node-message-text');
    const nodeEl = messageEl?.closest?.('.image-node');
    if(!messageEl || !nodeEl?.dataset.id) return;
    conversationSelectionCandidate = {
        text,
        sourceNodeId:nodeEl.dataset.id,
        sourceMessageId:messageEl.dataset.messageId || '',
        rememberedAt:performance.now(),
    };
}
function openConversationExtractionMenu(event, selection, returnFocus=null){
    if(!selection?.text?.trim() || !selection.sourceNodeId || !selection.sourceMessageId) return false;
    const menu = ensureConversationExtractionMenu();
    conversationSelection = {...selection};
    conversationExtractionReturnFocus = returnFocus;
    const left = Math.max(10, Math.min(window.innerWidth - 190, Number(event.clientX) + 6));
    const top = Math.max(10, Math.min(window.innerHeight - 48, Number(event.clientY) + 6));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.classList.add('open');
    menu.setAttribute('aria-hidden', 'false');
    menu.querySelector('[data-extract-prompt-action]')?.focus();
    refreshIcons();
    return true;
}
function closeConversationExtractionMenu(){
    const menu = document.querySelector('[data-extract-prompt-selection]');
    menu?.classList.remove('open');
    menu?.setAttribute('aria-hidden', 'true');
    if(menu?.contains(document.activeElement) && conversationExtractionReturnFocus?.isConnected){
        conversationExtractionReturnFocus.focus({preventScroll:true});
    }
    conversationExtractionReturnFocus = null;
    conversationSelection = null;
}
function extractConversationSelection(){
    const selection = conversationSelection;
    if(!selection?.text || !selection.sourceNodeId || !selection.sourceMessageId) return;
    const source = canvasState.nodes.find(node => node.id === selection.sourceNodeId);
    const sourceRect = source ? nodeRect(source) : null;
    const point = sourceRect
        ? {x:sourceRect.x + sourceRect.width + 80, y:sourceRect.y}
        : viewportCenter();
    pushUndo();
    createPromptNode(point.x, point.y, {
        skipUndo:true,
        text:selection.text,
        sourceNodeId:selection.sourceNodeId,
        sourceMessageId:selection.sourceMessageId,
    });
    conversationSelectionCandidate = null;
    window.getSelection()?.removeAllRanges();
    closeConversationExtractionMenu();
    flushImportantProjectMutation();
}
function applyConversationCanvasResponse(nextCanvas){
    if(!nextCanvas || typeof nextCanvas !== 'object') return false;
    const currentRevision = Number(canvas?.revision || 0);
    const responseRevision = Number(nextCanvas?.revision || 0);
    const acceptedRevision = Math.max(
        Number(conversationCanvasLastAcceptedRevision || 0),
        Number.isFinite(currentRevision) ? currentRevision : 0,
    );
    if(Number.isFinite(responseRevision) && responseRevision < acceptedRevision){
        return false;
    }
    const mutationClient = projectCanvasMutationClient();
    const localCanvas = canvasForStorage();
    const baseline = persistedCoreSnapshot || mutationClient.snapshotFromCanvas(nextCanvas);
    const responseState = mutationClient.rebaseProjectCanvasResponse(nextCanvas, baseline, localCanvas);
    if(responseState.ignored) return false;
    canvas = responseState.canvas;
    conversationCanvasLastAcceptedRevision = Math.max(
        Number(conversationCanvasLastAcceptedRevision || 0),
        Number(canvas?.revision || 0),
        Number.isFinite(responseRevision) ? responseRevision : 0,
    );
    canvasUsesConnections = Object.prototype.hasOwnProperty.call(canvas || {}, 'connections');
    canvasState.nodes = (Array.isArray(canvas.nodes) ? canvas.nodes : []).map(hydrateProjectMediaReferences).map(normalizeLegacySmartNode).filter(Boolean);
    canvas.history = sortSmartHistoryLogs(hydrateProjectMediaReferences(canvas.history || []));
    canvas.connections = Array.isArray(canvas.connections) ? canvas.connections : [];
    if(canvas.format === 'firefly-canvas-v1'){
        persistedCoreSnapshot = responseState.serverSnapshot;
        persistedDurableSnapshot = durableSnapshotForCanvas(nextCanvas);
    }
    if(responseState.mutations.length) scheduleSave();
    return true;
}
if(params.get('browserTest') === '1') window.__issue13ApplyConversationCanvasResponse = applyConversationCanvasResponse;
async function sendConversationMessage(node, input, button){
    const text = String(input?.value || '').trim();
    const liveNode = canvasState.nodes.find(item => item.id === node?.id) || node;
    const hasPendingMessage = (liveNode?.messages || []).some(message => message?.role === 'user' && message?.status === 'pending');
    if(!liveNode || !text || liveNode.conversationSending || hasPendingMessage) return;
    node = liveNode;
    node.conversationSending = true;
    if(button) button.disabled = true;
    const requestId = conversationRequestId();
    let controller = null;
    let timeoutId = null;
    try {
        const projectId = conversationProjectId();
        if(!projectId) throw new Error(tr('smart.conversationMissingProject'));
        const mutationClient = projectCanvasMutationClient();
        const response = await mutationClient.requestAfterProjectCanvasFlush({
            flush:flushProjectCanvasBeforeGeneration,
            failureMessage:tr('smart.canvasUnsavedMessageNotSent'),
            request:async () => {
                controller = new AbortController();
                timeoutId = setTimeout(() => controller.abort(), 15000);
                return fetch(`/api/project-library/${encodeURIComponent(projectId)}/conversation/${encodeURIComponent(node.id)}/messages`, {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({text, request_id:requestId}),
                    signal:controller.signal,
                });
            },
        });
        if(!response.ok) throw new Error(await responseErrorMessage(response, tr('smart.conversationSendFailed')));
        const data = await response.json();
        if(data.canvas) applyConversationCanvasResponse(data.canvas);
        else if(data.message) node.messages = [...(node.messages || []), data.message].slice(-500);
        input.value = '';
        if(data.status === 'pending') startConversationTaskPolling(node, requestId);
        render();
    } catch(error) {
        if(error?.name === 'AbortError'){
            ensureLocalPendingConversationMessage(node, text, requestId);
            startConversationTaskPolling(node, requestId);
            toast(tr('smart.conversationTimeoutStillRunning'));
            render();
        } else {
            toast(error?.message || tr('smart.conversationSendFailed'));
        }
    } finally {
        if(timeoutId !== null) clearTimeout(timeoutId);
        node.conversationSending = false;
        if(button) button.disabled = false;
    }
}
function bindConversationNodeControls(el, node){
    el.querySelectorAll('.conversation-node-control').forEach(control => {
        control.addEventListener('mousedown', event => event.stopPropagation());
        control.addEventListener('click', event => event.stopPropagation());
    });
    const input = el.querySelector('[data-conversation-input]');
    const send = el.querySelector('[data-conversation-send]');
    const collapse = el.querySelector('[data-conversation-collapse]');
    collapse?.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        node.conversation_collapsed = node.conversation_collapsed !== true;
        render();
        scheduleSave();
        flushImportantProjectMutation();
    });
    send?.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        sendConversationMessage(node, input, send);
    });
    input?.addEventListener('keydown', event => {
        if(event.key === 'Enter' && (event.ctrlKey || event.metaKey)){
            event.preventDefault();
            sendConversationMessage(node, input, send);
        }
    });
    el.querySelectorAll('[data-conversation-retry]').forEach(retryEl => {
        retryEl.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            const messageId = retryEl.getAttribute('data-conversation-retry');
            const liveNode = canvasState.nodes.find(item => item.id === node?.id) || node;
            const failedMessage = (liveNode?.messages || []).find(item => String(item.id || '') === String(messageId));
            const retryText = String(failedMessage?.text || failedMessage?.content || '').trim();
            if(!retryText) return;
            const liveInput = el.querySelector('[data-conversation-input]');
            const liveSend = el.querySelector('[data-conversation-send]');
            if(liveInput) liveInput.value = retryText;
            const hasPending = (liveNode?.messages || []).some(item => item.role === 'user' && item.status === 'pending');
            if(hasPending) return;
            sendConversationMessage(liveNode, liveInput, liveSend);
        });
    });
    el.querySelectorAll('.conversation-node-message-text').forEach(messageEl => {
        messageEl.addEventListener('pointerdown', event => event.stopPropagation());
        messageEl.addEventListener('mousedown', event => event.stopPropagation());
        messageEl.addEventListener('click', event => {
            event.stopPropagation();
            if(canvasState.selectedId !== node.id){
                canvasState.selectedId = node.id;
                canvasState.selectedIds = [];
                canvasState.selectedImage = {nodeId:'', index:-1};
                render();
            }
        });
        messageEl.addEventListener('contextmenu', event => {
            const liveSelection = conversationSelectionFromDom(messageEl);
            const messageId = messageEl.dataset.messageId || '';
            const cachedSelection = conversationSelectionCandidate;
            const selection = liveSelection || (
                cachedSelection?.sourceNodeId === node.id
                && cachedSelection?.sourceMessageId === messageId
                && performance.now() - Number(cachedSelection.rememberedAt || 0) < 1500
                    ? cachedSelection
                    : null
            );
            if(!selection?.text || !selection.sourceNodeId || !selection.sourceMessageId) return;
            event.preventDefault();
            event.stopPropagation();
            openConversationExtractionMenu(event, selection, messageEl);
        });
        messageEl.addEventListener('keydown', event => {
            if(!(event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) return;
            const messageId = messageEl.dataset.messageId || '';
            const selection = conversationSelectionFromDom(messageEl);
            if(!selection?.text || !selection.sourceMessageId) return;
            event.preventDefault();
            event.stopPropagation();
            const rect = messageEl.getBoundingClientRect();
            openConversationExtractionMenu({clientX:rect.left + 12, clientY:rect.bottom - 8}, selection, messageEl);
        });
    });
}
function agentProjectId(){ return canvas?.project_id || canvas?.project || sourceProjectId || ''; }
function bindAgentNodeControls(el, node){
    // TUI-style agent node: bind terminal input
    const nodeId = node.id;
    const projectId = canvasId || '';

    // Connect WebSocket when node is rendered
    connectAgentWebSocket(nodeId, projectId);

    // Bind tool activity row toggle (collapsible)
    const toolRows = el.querySelectorAll('[data-tool-call-id]');
    toolRows.forEach(row => {
        row.addEventListener('click', event => {
            event.stopPropagation();
            const toolCallId = row.getAttribute('data-tool-call-id');
            const state = getAgentTuiState(nodeId);
            const activity = state.toolActivities.get(toolCallId);
            if (activity) {
                activity.expanded = !activity.expanded;
                renderAgentNode(nodeId);
            }
        });
    });

    // Bind input
    const inputEl = el.querySelector('[data-agent-tui-input]');
    if (inputEl) {
        inputEl.addEventListener('mousedown', event => event.stopPropagation());
        inputEl.addEventListener('keydown', event => {
            event.stopPropagation();
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                const message = inputEl.value.trim();
                if (message) {
                    const inputState = getAgentTuiState(nodeId);
                    if (inputState && inputState.status === 'busy') {
                        sendAgentFollowUp(nodeId, projectId, message);
                    } else {
                        sendAgentPrompt(nodeId, projectId, message);
                    }
                    inputEl.value = '';
                }
            }
        });
    }

    // Bind thinking toggle
    const thinkingBtn = el.querySelector('[data-agent-thinking]');
    if (thinkingBtn) {
        thinkingBtn.addEventListener('mousedown', event => event.stopPropagation());
        thinkingBtn.addEventListener('click', event => {
            event.stopPropagation();
            const tState = getAgentTuiState(nodeId);
            if (tState) {
                tState.thinkingVisible = !tState.thinkingVisible;
                renderAgentNode(nodeId);
            }
        });
    }

    // Bind steer button
    const steerBtn = el.querySelector('[data-agent-steer]');
    if (steerBtn) {
        steerBtn.addEventListener('mousedown', event => event.stopPropagation());
        steerBtn.addEventListener('click', event => {
            event.stopPropagation();
            const inputEl = el.querySelector('[data-agent-tui-input]');
            const message = inputEl ? inputEl.value.trim() : '';
            if (message) {
                sendAgentSteer(nodeId, projectId, message);
                inputEl.value = '';
            }
        });
    }

    // Bind follow-up button
    const followupBtn = el.querySelector('[data-agent-followup]');
    if (followupBtn) {
        followupBtn.addEventListener('mousedown', event => event.stopPropagation());
        followupBtn.addEventListener('click', event => {
            event.stopPropagation();
            const inputEl = el.querySelector('[data-agent-tui-input]');
            const message = inputEl ? inputEl.value.trim() : '';
            if (message) {
                sendAgentFollowUp(nodeId, projectId, message);
                inputEl.value = '';
            }
        });
    }

    // Bind abort button
    const abortBtn = el.querySelector('[data-agent-abort]');
    if (abortBtn) {
        abortBtn.addEventListener('mousedown', event => event.stopPropagation());
        abortBtn.addEventListener('click', event => {
            event.stopPropagation();
            sendAgentAbort(nodeId, projectId);
        });
    }

    // Bind confirm approve button
    const approveBtn = el.querySelector('[data-agent-confirm-approve]');
    if (approveBtn) {
        approveBtn.addEventListener('mousedown', event => event.stopPropagation());
        approveBtn.addEventListener('click', event => {
            event.stopPropagation();
            sendAgentConfirm(nodeId, projectId, true);
        });
    }

    // Bind confirm reject button
    const rejectBtn = el.querySelector('[data-agent-confirm-reject]');
    if (rejectBtn) {
        rejectBtn.addEventListener('mousedown', event => event.stopPropagation());
        rejectBtn.addEventListener('click', event => {
            event.stopPropagation();
            sendAgentConfirm(nodeId, projectId, false);
        });
    }

    // Bind model select
    const modelSelect = el.querySelector('[data-agent-model]');
    if (modelSelect) {
        modelSelect.addEventListener('mousedown', event => event.stopPropagation());
        modelSelect.addEventListener('change', event => {
            event.stopPropagation();
            const model = modelSelect.value;
            setAgentModel(nodeId, projectId, model);
        });
    }

    // Bind autonomy select
    const autonomySelect = el.querySelector('[data-agent-autonomy]');
    if (autonomySelect) {
        autonomySelect.addEventListener('mousedown', event => event.stopPropagation());
        autonomySelect.addEventListener('change', event => {
            event.stopPropagation();
            const autonomy = autonomySelect.value;
            setAgentAutonomy(nodeId, projectId, autonomy);
        });
    }

    // Bind fork button
    const forkBtn = el.querySelector('[data-agent-fork]');
    if (forkBtn) {
        forkBtn.addEventListener('mousedown', event => event.stopPropagation());
        forkBtn.addEventListener('click', event => {
            event.stopPropagation();
            showForkPicker(nodeId, projectId);
        });
    }

    // Bind branch select
    const branchSelect = el.querySelector('[data-agent-branch]');
    if (branchSelect) {
        branchSelect.addEventListener('mousedown', event => event.stopPropagation());
        branchSelect.addEventListener('change', event => {
            event.stopPropagation();
            const branchId = branchSelect.value;
            if (branchId) switchAgentBranch(nodeId, projectId, branchId);
        });
    }

    // Bind body scroll to bottom on new messages
    const bodyEl = el.querySelector('[data-agent-tui-body]');
    if (bodyEl) {
        bodyEl.addEventListener('mousedown', event => event.stopPropagation());
    }
}

function renderAgentNode(nodeId) {
    // Find the rendered element for this node and re-render its body
    const el = document.querySelector(`[data-id="${nodeId}"]`);
    if (!el) return;

    // Find the node data
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if (!node) return;

    // Get the layout
    const imgs = node.images || [];
    const scale = nodeScale(node);
    const layout = imageLayout(imgs, scale, node);
    if (!layout) return;

    // Re-render just the body
    const body = nodeBodyHtml(node, layout);
    const bodyContainer = el.querySelector('.node-body');
    if (bodyContainer) {
        bodyContainer.innerHTML = body;
        // Re-bind controls
        bindAgentNodeControls(bodyContainer, node);
        // Scroll to bottom
        const messagesEl = bodyContainer.querySelector('[data-agent-tui-messages]');
        if (messagesEl) {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }
    }

    // Update node card status light + last line preview
    const headMeta = el.querySelector('.node-head-meta');
    if (headMeta) {
        headMeta.innerHTML = agentCardStatusHtml(nodeId);
    }
}

function bindAgentTaskNodeControls(el, node){
    el.querySelectorAll('[data-agent-result-link]').forEach(link => {
        link.addEventListener('mousedown', event => event.stopPropagation());
        link.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            const resultId = String(link.dataset.agentResultLink || '');
            const result = canvasState.nodes.find(item => String(item.id || '') === resultId);
            if(!result) return;
            canvasState.selectedId = result.id;
            canvasState.selectedIds = [];
            canvasState.selectedImage = {nodeId:'', index:-1};
            centerViewportOnWorldPoint({x:Number(result.x || 0) + 120, y:Number(result.y || 0) + 100});
            render();
        });
    });
}
function bindLocalEditNodeControls(el, node){
    syncLocalEditInputRoles(node);
    el.querySelectorAll('[data-local-edit-connect-role]').forEach(btn => {
        btn.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); });
        btn.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); node.localEditActiveRole = btn.dataset.localEditConnectRole; render(); });
    });
    el.querySelectorAll('[data-local-edit-mode]').forEach(btn => {
        btn.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); });
        btn.addEventListener('click', e => {
            e.preventDefault(); e.stopPropagation();
            const nextMode = btn.dataset.localEditMode === 'retain' ? 'retain' : 'modify';
            if(nextMode === node.mode) return;
            const size = sourceImageSize();
            node.mode = nextMode;
            if(nextMode === 'modify'){
                const isRect = Array.isArray(node.selection) && node.selection.length === 4 && node.selection[2] > node.selection[0];
                const ratioValue = String(node.aspect_ratio || '1:1').split(':').map(Number);
                const ratio = ratioValue.length === 2 && ratioValue[0] > 0 && ratioValue[1] > 0 ? ratioValue[0] / ratioValue[1] : 1;
                if(!isRect) node.selection = localEditFullImageRect(size.width, size.height, ratio) || [];
            } else if(Array.isArray(node.selection) && node.selection.length && !Array.isArray(node.selection[0])){
                // 矩形选区无法直接转点列：清空，等用户按新形状重画。
                node.selection = [];
            }
            render(); scheduleSave();
        });
    });
    const prompt = el.querySelector('[data-local-edit-role="prompt"]');
    const ratio = el.querySelector('[data-local-edit-ratio]');
    const shape = el.querySelector('[data-local-edit-shape]');
    const generationSize = el.querySelector('[data-local-edit-size]');
    const generationResolution = el.querySelector('[data-local-edit-resolution]');
    const feather = el.querySelector('[data-local-edit-feather]');
    const selectionCanvas = el.querySelector('[data-local-edit-selection-canvas]');
    const selectionPreview = el.querySelector('[data-local-edit-preview]');
    let selectionDrag = null;
    let lastOriginalAsset = node.original_asset_id || '';
    const sourceImageSize = () => {
        const sourceNode = canvasState.nodes.find(item => item.id === (node.localEditInputRoles?.originalNode || ''));
        const sourceItem = (sourceNode?.images || []).find(item => item?.asset_id || item?.url) || {};
        return {
            width: Math.max(1, Number(selectionPreview?.naturalWidth || sourceItem.natural_w || sourceItem.width || 1)),
            height: Math.max(1, Number(selectionPreview?.naturalHeight || sourceItem.natural_h || sourceItem.height || 1))
        };
    };
    const displayPoint = event => {
        const bounds = selectionCanvas?.getBoundingClientRect();
        if(!bounds || !bounds.width || !bounds.height) return {x:0, y:0};
        return {
            x: Math.max(0, Math.min(bounds.width, event.clientX - bounds.left)),
            y: Math.max(0, Math.min(bounds.height, event.clientY - bounds.top))
        };
    };
    const sourcePoint = point => {
        const bounds = selectionCanvas?.getBoundingClientRect() || {width:1, height:1};
        const size = sourceImageSize();
        return [
            Math.max(0, Math.min(size.width - 1, Math.round(point.x * size.width / Math.max(1, bounds.width)))),
            Math.max(0, Math.min(size.height - 1, Math.round(point.y * size.height / Math.max(1, bounds.height))))
        ];
    };
    const selectionAspect = () => {
        const ratioValue = String(node.aspect_ratio || '1:1').split(':').map(Number);
        return ratioValue.length === 2 && ratioValue[0] > 0 && ratioValue[1] > 0 ? ratioValue[0] / ratioValue[1] : 1;
    };
    const displayRectFromDrag = draft => {
        if(!draft?.start || !draft?.current || !selectionCanvas) return null;
        const bounds = selectionCanvas.getBoundingClientRect();
        const maxWidth = Math.max(1, bounds.width), maxHeight = Math.max(1, bounds.height);
        const aspect = selectionAspect();
        let x = Math.min(draft.start.x, draft.current.x), y = Math.min(draft.start.y, draft.current.y);
        let w = Math.abs(draft.current.x - draft.start.x), h = Math.abs(draft.current.y - draft.start.y);
        if(w < 1 && h < 1) return null;
        if(w / Math.max(1, h) > aspect) h = w / aspect; else w = h * aspect;
        if(w > maxWidth){ w = maxWidth; h = w / aspect; }
        if(h > maxHeight){ h = maxHeight; w = h * aspect; }
        x = Math.max(0, Math.min(maxWidth - w, x));
        y = Math.max(0, Math.min(maxHeight - h, y));
        return {x, y, w, h};
    };
    const strokeSmoothPath = (ctx, pts) => {
        ctx.beginPath();
        if(pts.length < 2){ ctx.stroke(); return; }
        ctx.moveTo(pts[0].x, pts[0].y);
        if(pts.length === 2){ ctx.lineTo(pts[1].x, pts[1].y); }
        else {
            for(let i = 1; i < pts.length - 1; i++){
                const midX = (pts[i].x + pts[i + 1].x) / 2, midY = (pts[i].y + pts[i + 1].y) / 2;
                ctx.quadraticCurveTo(pts[i].x, pts[i].y, midX, midY);
            }
            ctx.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y);
        }
        ctx.stroke();
    };
    const drawSelection = draft => {
        if(!selectionCanvas) return;
        const bounds = selectionCanvas.getBoundingClientRect();
        const width = Math.max(1, Math.round(bounds.width));
        const height = Math.max(1, Math.round(bounds.height));
        const dpr = Math.max(1, Number(window.devicePixelRatio || 1));
        if(selectionCanvas.width !== Math.round(width * dpr) || selectionCanvas.height !== Math.round(height * dpr)){
            selectionCanvas.width = Math.round(width * dpr);
            selectionCanvas.height = Math.round(height * dpr);
        }
        const ctx = selectionCanvas.getContext('2d');
        if(!ctx) return;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = 'rgba(14, 165, 233, .20)';
        ctx.strokeStyle = '#0284c7';
        ctx.lineWidth = 2;
        if(node.mode === 'modify'){
            const dragRect = displayRectFromDrag(draft);
            if(dragRect){
                ctx.fillRect(dragRect.x, dragRect.y, dragRect.w, dragRect.h);
                ctx.strokeRect(dragRect.x, dragRect.y, dragRect.w, dragRect.h);
                return;
            }
            const rect = Array.isArray(node.selection) && node.selection.length === 4 ? node.selection : null;
            if(!rect) return;
            const size = sourceImageSize();
            const x = Number(rect[0]) * width / size.width;
            const y = Number(rect[1]) * height / size.height;
            const w = Math.max(1, (Number(rect[2]) - Number(rect[0])) * width / size.width);
            const h = Math.max(1, (Number(rect[3]) - Number(rect[1])) * height / size.height);
            ctx.fillRect(x, y, w, h); ctx.strokeRect(x, y, w, h);
            return;
        }
        // retain：freehand / polygon / curve 三种可视化与后端 rasterize 语义对齐
        // （freehand 与 curve 是描边笔迹、polygon 是填充多边形，线宽 12 原图像素）。
        const size = sourceImageSize();
        let displayPts;
        if(Array.isArray(draft?.points) && draft.points.length) displayPts = draft.points;
        else displayPts = (Array.isArray(node.selection) ? node.selection : []).map(p => ({
            x: Number(p?.[0] || 0) * width / size.width,
            y: Number(p?.[1] || 0) * height / size.height
        }));
        if(!displayPts.length) return;
        ctx.lineJoin = 'round'; ctx.lineCap = 'round';
        const strokeW = Math.max(2, 12 * width / size.width);
        if(node.selectionType === 'polygon'){
            ctx.beginPath();
            displayPts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
            if(draft?.rubber) ctx.lineTo(draft.rubber.x, draft.rubber.y);
            if(draft){ ctx.stroke(); }
            else { ctx.closePath(); ctx.fill(); ctx.stroke(); }
            ctx.beginPath(); ctx.arc(displayPts[0].x, displayPts[0].y, 5, 0, Math.PI * 2);
            ctx.fillStyle = '#0284c7'; ctx.fill();
            return;
        }
        ctx.lineWidth = strokeW;
        if(node.selectionType === 'curve'){
            const pts = (draft?.rubber && draft.points?.length) ? [...displayPts, draft.rubber] : displayPts;
            strokeSmoothPath(ctx, pts);
            return;
        }
        ctx.beginPath();
        displayPts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
        ctx.stroke();
    };
    const commitSelection = displayPoints => {
        if(!selectionCanvas) return;
        if(node.mode === 'modify'){
            const displayRect = displayRectFromDrag(selectionDrag);
            if(!displayRect || displayRect.w < 2 || displayRect.h < 2) return;
            const bounds = selectionCanvas.getBoundingClientRect();
            const size = sourceImageSize();
            let left = Math.round(displayRect.x * size.width / Math.max(1, bounds.width));
            let top = Math.round(displayRect.y * size.height / Math.max(1, bounds.height));
            let w = Math.round(displayRect.w * size.width / Math.max(1, bounds.width));
            let h = Math.round(displayRect.h * size.height / Math.max(1, bounds.height));
            const ratio = selectionAspect();
            if(w / Math.max(1, h) > ratio) w = Math.max(1, Math.round(h * ratio));
            else h = Math.max(1, Math.round(w / ratio));
            left = Math.max(0, Math.min(size.width - w, left));
            top = Math.max(0, Math.min(size.height - h, top));
            node.selection = [left, top, left + w, top + h];
        } else {
            const points = (displayPoints || []).map(sourcePoint);
            const min = node.selectionType === 'polygon' ? 3 : 2;
            if(points.length < min) return;
            node.selection = points;
        }
        node._leSelectionAsset = node.original_asset_id || '';
        sync();
        drawSelection();
    };
    const beginSelectionPointer = event => {
        if(event.button !== 0 || !selectionCanvas) return;
        event.preventDefault(); event.stopPropagation();
        try { selectionCanvas.setPointerCapture?.(event.pointerId); } catch (_) {}
        const point = displayPoint(event);
        if(node.mode === 'modify'){
            selectionDrag = {start:point, current:point};
            drawSelection(selectionDrag);
            return;
        }
        // retain
        if(!selectionDrag || node.selectionType === 'freehand'){
            selectionDrag = {points:[point]};
            drawSelection(selectionDrag);
            return;
        }
        // polygon / curve：每次点击加一个点；polygon 点回起点（14px 内）闭合提交
        if(node.selectionType === 'polygon' && selectionDrag.points.length >= 3){
            const first = selectionDrag.points[0];
            if(Math.hypot(point.x - first.x, point.y - first.y) < 14){ commitSelection(selectionDrag.points); selectionDrag = null; return; }
        }
        const lastPt = selectionDrag.points[selectionDrag.points.length - 1];
        if(!selectionDrag.points.length || Math.hypot(point.x - lastPt.x, point.y - lastPt.y) >= 3) selectionDrag.points.push(point);
        drawSelection(selectionDrag);
    };
    const moveSelectionPointer = event => {
        if(!selectionDrag) return;
        event.preventDefault(); event.stopPropagation();
        const point = displayPoint(event);
        if(node.mode === 'modify'){
            selectionDrag.current = point;
        } else if(node.selectionType === 'freehand'){
            if(!selectionDrag.points.length || Math.hypot(point.x - selectionDrag.points[selectionDrag.points.length - 1].x, point.y - selectionDrag.points[selectionDrag.points.length - 1].y) >= 2) selectionDrag.points.push(point);
        } else {
            selectionDrag.rubber = point;
        }
        drawSelection(selectionDrag);
    };
    const endSelectionPointer = event => {
        if(!selectionDrag) return;
        event.preventDefault(); event.stopPropagation();
        try { selectionCanvas?.releasePointerCapture?.(event.pointerId); } catch (_) {}
        if(node.mode === 'modify' || node.selectionType === 'freehand'){
            commitSelection(node.mode === 'modify' ? null : selectionDrag.points);
            selectionDrag = null;
        }
    };
    const doubleClickSelection = event => {
        event.preventDefault(); event.stopPropagation();
        if(node.mode !== 'retain' || node.selectionType === 'freehand') return;
        const min = node.selectionType === 'polygon' ? 3 : 2;
        if(selectionDrag?.points?.length >= min) commitSelection(selectionDrag.points);
        selectionDrag = null;
        drawSelection();
    };
    selectionCanvas?.addEventListener('pointerdown', beginSelectionPointer);
    selectionCanvas?.addEventListener('pointermove', moveSelectionPointer);
    selectionCanvas?.addEventListener('pointerup', endSelectionPointer);
    selectionCanvas?.addEventListener('pointercancel', endSelectionPointer);
    selectionCanvas?.addEventListener('dblclick', doubleClickSelection);
    const resetSelection = () => {
        const size = sourceImageSize();
        if(node.mode === 'modify'){
            const ratioValue = String(node.aspect_ratio || '1:1').split(':').map(Number);
            const ratio = ratioValue.length === 2 && ratioValue[0] > 0 && ratioValue[1] > 0 ? ratioValue[0] / ratioValue[1] : 1;
            node.selection = localEditFullImageRect(size.width, size.height, ratio) || [0, 0, size.width, size.height];
        } else node.selection = [];
        node._leSelectionAsset = node.original_asset_id || '';
        sync();
        drawSelection();
    };
    el.querySelector('[data-local-edit-reset-selection]')?.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); resetSelection(); });
    selectionPreview?.addEventListener('load', () => {
        // 舞台盒锁定为原图比例，消除 object-fit 信箱化造成的 display→原图像素映射误差。
        const stage = selectionPreview.closest('[data-local-edit-stage]');
        const size = sourceImageSize();
        if(stage) stage.style.aspectRatio = `${size.width} / ${size.height}`;
        const currentAsset = node.original_asset_id || '';
        if(lastOriginalAsset && currentAsset && lastOriginalAsset !== currentAsset){
            // 原图被换成另一张：旧选区像素坐标失效，重置（modify=全图按比例，retain=清空）。
            const ratioValue = String(node.aspect_ratio || '1:1').split(':').map(Number);
            const ratio = ratioValue.length === 2 && ratioValue[0] > 0 && ratioValue[1] > 0 ? ratioValue[0] / ratioValue[1] : 1;
            node.selection = node.mode === 'modify' ? (localEditFullImageRect(size.width, size.height, ratio) || []) : [];
            sync();
        }
        lastOriginalAsset = currentAsset;
        if(node.mode === 'modify' && !(Array.isArray(node.selection) && node.selection.length === 4 && node.selection[2] > node.selection[0])){
            const ratioValue = String(node.aspect_ratio || '1:1').split(':').map(Number);
            const ratio = ratioValue.length === 2 && ratioValue[0] > 0 && ratioValue[1] > 0 ? ratioValue[0] / ratioValue[1] : 1;
            node.selection = localEditFullImageRect(size.width, size.height, ratio) || [];
            sync();
        }
        drawSelection();
    });
    requestAnimationFrame(() => drawSelection());
    const sync = () => {
        syncLocalEditInputRoles(node);
        const roleMap = node.localEditInputRoles || {};
        const originalNode = canvasState.nodes.find(item => item.id === roleMap.originalNode);
        node.original_asset_id = ((originalNode?.images || []).find(img => img?.asset_id) || {})?.asset_id || '';
        node.reference_asset_ids = (roleMap.referenceNodes || []).flatMap(id => {
            const item = canvasState.nodes.find(candidate => candidate.id === id);
            return (item?.images || []).map(img => img?.asset_id).filter(Boolean);
        }).filter((v, i, arr) => arr.indexOf(v) === i);
        const promptNodeIds = Array.from(new Set([...(roleMap.promptNodes || []), roleMap.promptNode].filter(Boolean)));
        const promptTexts = promptNodeIds.map(id => canvasState.nodes.find(item => item.id === id))
            .map(item => String(item?.text || item?.prompt || '').trim()).filter(Boolean);
        node.prompt = promptTexts.length ? promptTexts.join('\n\n') : String(prompt?.value || '');
        node.feather_radius = Math.max(0, Number(feather?.value || 0));
        node.aspect_ratio = String(ratio?.value || node.aspect_ratio || '1:1');
        node.generation_size = String(generationSize?.value || node.generation_size || '1024x1024');
        node.generation_resolution = String(generationResolution?.value || node.generation_resolution || '1k');
        node.selectionType = String(shape?.value || node.selectionType || 'polygon');
        updateSubmitState();
        drawSelection();
        scheduleSave();
    };
    const updateSubmitState = () => {
        const roleMap = node.localEditInputRoles || {};
        const selOk = node.mode === 'modify'
            ? (Array.isArray(node.selection) && node.selection.length === 4 && node.selection[2] > node.selection[0] && node.selection[3] > node.selection[1])
            : (Array.isArray(node.selection) && node.selection.length >= (node.selectionType === 'polygon' ? 3 : 2));
        const state = !roleMap.originalNode || !node.original_asset_id ? 'needOriginal'
            : !selOk ? 'needSelection'
            : !String(node.prompt || '').trim() ? 'needPrompt'
            : node._leBusy ? 'busy'
            : node.output_asset_id ? 'done'
            : 'ready';
        const texts = {
            needOriginal: tr('smart.localEditNeedOriginal'),
            needSelection: tr('smart.localEditNeedSelection'),
            needPrompt: tr('smart.localEditNeedPrompt'),
            busy: tr('smart.localEditGenerating'),
            done: tr('smart.localEditResultReady'),
            ready: tr('smart.localEditReady')
        };
        const status = el.querySelector('[data-local-edit-status]');
        const submit = el.querySelector('[data-local-edit-submit]');
        if(status){ status.textContent = texts[state]; status.classList.toggle('is-blocked', ['needOriginal','needSelection','needPrompt'].includes(state)); }
        if(submit){ submit.disabled = state === 'needOriginal' || state === 'needSelection' || state === 'needPrompt' || state === 'busy'; submit.classList.toggle('is-busy', state === 'busy'); }
    };
    [prompt, feather].forEach(input => input?.addEventListener('input', sync));
    [ratio, shape, generationSize, generationResolution].forEach(input => input?.addEventListener('change', sync));
    el.querySelector('[data-local-edit-submit]')?.addEventListener('click', async e => {
        e.preventDefault(); e.stopPropagation(); sync();
        const roleMap = node.localEditInputRoles || {};
        if(!roleMap.originalNode || !node.original_asset_id){ toast(tr('smart.localEditNeedOriginal')); return; }
        if(!String(node.prompt || '').trim()){ toast(tr('smart.localEditNeedPrompt')); return; }
        const projectId = canvas?.project_id || canvas?.project || canvasId;
        const button = e.currentTarget;
        node._leBusy = true;
        button.disabled = true;
        const statusEl = el.querySelector('[data-local-edit-status]');
        if(statusEl) statusEl.textContent = tr('smart.localEditGenerating');
        try {
            if(typeof flushProjectCanvasBeforeGeneration === 'function' && !await flushProjectCanvasBeforeGeneration()) throw new Error(tr('smart.canvasUnsavedLocalEdit'));
            const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/local-edit`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
                original_asset_id:node.original_asset_id, reference_asset_ids:node.reference_asset_ids || [], prompt:node.prompt,
                mode:node.mode, aspect_ratio:node.aspect_ratio || '', size:node.generation_size || '1024x1024', resolution:node.generation_resolution || '1k', selection:node.mode === 'modify' ? node.selection : {type:node.selectionType || 'freehand', points:node.selection}, feather_radius:node.feather_radius, result_node_id:node.id, base_revision:Number(canvas?.revision || 0)
            })});
            if(!response.ok) throw new Error(await responseErrorMessage(response, tr('smart.localEditFailed')));
            const data = await response.json();
            node.output_asset_id = data.assets?.output?.asset_id || '';
            node.model_result_asset_id = data.assets?.model_result?.asset_id || '';
            node.images = node.output_asset_id ? [{asset_id:node.output_asset_id, url:`/api/project-library/${encodeURIComponent(projectId)}/media/${encodeURIComponent(node.output_asset_id)}`, kind:'image', name:'local-edit-output.png'}] : [];
            // 先清 busy 再 rebase：applyConversationCanvasResponse 会把本地快照（canvasForStorage）
            // 合入服务端响应，busy 是瞬态 UI 标志，绝不能泄漏进合并后的节点状态。
            node._leBusy = false;
            if(data.canvas?.canvas) applyConversationCanvasResponse(data.canvas.canvas);
            const live = canvasState.nodes.find(n => n.id === node.id) || node;
            live._leBusy = false;
            if(!live.output_asset_id){ live.output_asset_id = node.output_asset_id; live.model_result_asset_id = node.model_result_asset_id; live.images = node.images; }
            toast(tr('smart.localEditDone')); render();
        } catch(error){
            const live = canvasState.nodes.find(n => n.id === node.id) || node;
            live._leBusy = false;
            toast(error.message || tr('smart.localEditFailed'));
            render();
        }
        finally { button.disabled = false; }
    });
    sync();
}
function bindNodeEvents(){
    world.querySelectorAll('.image-node').forEach(el => {
        const id = el.dataset.id;
        if(promptNodeFlowView.mode === 'flow' && promptNodeFlowView.element === el) return;
        const nodeForControls = canvasState.nodes.find(n => n.id === id);
        if(isSmartConversationNode(nodeForControls)) bindConversationNodeControls(el, nodeForControls);
        if(isSmartAgentNode(nodeForControls)) bindAgentNodeControls(el, nodeForControls);
        if(isSmartAgentTaskNode(nodeForControls)) bindAgentTaskNodeControls(el, nodeForControls);
        if(nodeForControls?.type === 'local-edit') bindLocalEditNodeControls(el, nodeForControls);
        if(nodeForControls?.type === 'smart-prompt') bindPromptNodeControls(el, nodeForControls);
        if(nodeForControls?.type === 'smart-loop') bindLoopNodeControls(el, nodeForControls);
        if(nodeForControls?.type === 'smart-group') {
            el.ondblclick = e => {
                e.preventDefault();
                e.stopPropagation();
                canvasState.selectedId = id;
                canvasState.selectedIds = [];
                canvasState.selectedImage = {nodeId:'', index:-1};
                openCreateMenu(e, {groupId:id});
            };
        }
        el.onclick = e => {
            e.stopPropagation();
            if(Date.now() < suppressNodeClickUntil) return;
            const node = canvasState.nodes.find(n => n.id === id);
            hideRunTimerForNode(node);
            const alreadySelected = canvasState.selectedId === id && canvasState.selectedIds.length === 0 && canvasState.selectedImage.nodeId === '';
            canvasState.selectedId = id;
            canvasState.selectedIds = [];
            canvasState.selectedImage = {nodeId:'', index:-1};
            if(smartCascadeAnyRunning()) smartCascadeSilentSelection = false;
            if(alreadySelected){
                syncSelectionUi();
                updateComposer();
                return;
            }
            render();
        };
        if(nodeForControls?.type !== 'smart-group') el.ondblclick = e => e.stopPropagation();
        const nodeDrop = el.querySelector('.node-drop');
        nodeDrop?.addEventListener('mousedown', e => {
            if(e.button !== 0) return;
            e.preventDefault();
            e.stopPropagation();
        }, true);
        nodeDrop?.addEventListener('dblclick', e => {
            e.preventDefault(); e.stopPropagation();
            hideRunTimerForNode(canvasState.nodes.find(n => n.id === id));
            canvasState.selectedId = id;
            canvasState.selectedIds = [];
            canvasState.selectedImage = {nodeId:'', index:-1};
            pendingGroupUploadPoint = null;
            uploadTargetId = id;
            syncSelectionUi();
            updateComposer();
            pickMediaForSmartNode(id);
        });
        el.querySelectorAll('.node-delete').forEach(btn => {
            btn.addEventListener('click', e => {
                e.preventDefault(); e.stopPropagation();
                deleteNodeFromButton(id);
            });
        });
        el.querySelectorAll('[data-smart-node-action]').forEach(btn => {
            btn.addEventListener('mousedown', e => {
                e.preventDefault();
                e.stopPropagation();
            }, true);
            btn.addEventListener('click', e => {
                e.preventDefault();
                e.stopPropagation();
                runSmartNodeToolbarAction(btn.dataset.nodeId || id, btn.dataset.smartNodeAction);
            });
        });
        el.querySelectorAll('[data-smart-group-action]').forEach(btn => {
            btn.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); }, true);
            btn.addEventListener('click', e => {
                e.preventDefault();
                e.stopPropagation();
                runSmartGroupToolbarAction(btn.dataset.nodeId || id, btn.dataset.smartGroupAction);
            });
        });
        el.querySelectorAll('[data-jimeng-query]').forEach(btn => {
            btn.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); }, true);
            btn.addEventListener('click', e => {
                e.preventDefault(); e.stopPropagation();
                queryJimengNow(btn.dataset.jimengQuery);
            });
        });
        el.querySelectorAll('[data-image-task-query]').forEach(btn => {
            btn.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); }, true);
            btn.addEventListener('click', e => {
                e.preventDefault(); e.stopPropagation();
                querySmartImageTaskNow(btn.dataset.imageTaskQuery, btn.dataset.taskId);
            });
        });
        el.querySelectorAll('[data-image-task-retry]').forEach(btn => {
            btn.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); }, true);
            btn.addEventListener('click', e => {
                e.preventDefault(); e.stopPropagation();
                retryFailedGeneration(btn.dataset.imageTaskRetry);
            });
        });
        el.querySelectorAll('[data-image-task-url]').forEach(btn => {
            btn.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); }, true);
            btn.addEventListener('click', async e => {
                e.preventDefault(); e.stopPropagation();
                const nodeId = btn.dataset.imageTaskUrl;
                const node = canvasState.nodes.find(n => String(n.id || '') === String(nodeId));
                if(!node) return;
                const url = prompt(tr('smart.imageUrlPrompt'));
                if(!url) return;
                try{
                    toast(tr('smart.downloadingImage'));
                    const response = await fetch(url);
                    if(!response.ok) throw new Error(trf('smart.downloadFailedHttp', {code: response.status}));
                    const blob = await response.blob();
                    const file = new File([blob], `image_${Date.now()}.png`, {type:'image/png'});
                    toast(tr('smart.uploadingToCanvas'));
                    const media = await uploadProjectMedia(file);
                    // 使用 media 创建图片节点，完成该任务
                    const images = [{id:media.id, url:media.url}];
                    finalizeSmartPendingTask(node, null, images, 'image', 1);
                    node.failedGenerationTasks = []; // 清除失败任务
                    render();
                    scheduleSave();
                    toast(tr('smart.imageWrittenToCanvas'));
                }catch(err){
                    toast(trf('smart.operationFailed', {m: err.message}));
                }
            });
        });
        el.querySelectorAll('[data-thumb-scroll]').forEach(scroller => {
            scroller.addEventListener('wheel', e => {
                e.stopPropagation();
            }, {passive:false});
        });
        el.querySelectorAll('.image-delete').forEach(btn => {
            btn.addEventListener('click', e => {
                e.preventDefault(); e.stopPropagation();
                deleteImage(id, Number(btn.dataset.imageIndex));
            });
        });
        // #89：图片星标（标记/取消），分组内图片用 ref 节点定位。
        el.querySelectorAll('.image-mark').forEach(btn => {
            btn.addEventListener('click', e => {
                e.preventDefault(); e.stopPropagation();
                const item = btn.closest('[data-image-index]');
                const targetNodeId = item?.dataset.refNodeId || id;
                const imageIndex = Number(item?.dataset.refImageIndex ?? item?.dataset.imageIndex ?? 0);
                toggleMarkedImage(canvasState.nodes.find(n => n.id === targetNodeId), imageIndex);
            });
        });
        el.querySelectorAll('[data-node-cancel-task]').forEach(btn => {
            btn.addEventListener('mousedown', e => {
                e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
            }, true);
            btn.addEventListener('click', e => {
                e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
                clearImageClickTimer();
                suppressImageClickUntil = Date.now() + 260;
                void cancelNodeGenerationTask(id, btn);
            });
        });
        // #90：批量待出图格子点击 → 任务详情弹窗（mousedown 拦截避免触发节点拖动/图片点选）。
        el.querySelectorAll('[data-batch-cell-detail]').forEach(cell => {
            cell.addEventListener('mousedown', e => {
                e.preventDefault(); e.stopPropagation();
            }, true);
            cell.addEventListener('click', e => {
                e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
                clearImageClickTimer();
                suppressImageClickUntil = Date.now() + 260;
                openBatchTaskDetail(cell.dataset.batchCellDetail, Number(cell.dataset.batchCellIndex || 0));
            });
        });
        el.querySelectorAll('.image-name-badge').forEach(badge => {
            const item = badge.closest('[data-image-index]');
            const targetNodeId = item?.dataset.refNodeId || id;
            const imageIndex = Number(item?.dataset.refImageIndex ?? item?.dataset.imageIndex ?? 0);
            badge.addEventListener('mousedown', e => {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
            }, true);
            badge.addEventListener('click', e => {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
            }, true);
            badge.addEventListener('dblclick', e => {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                clearImageClickTimer();
                suppressImageClickUntil = Date.now() + 260;
                renameSmartNodeImage(targetNodeId, imageIndex);
            }, true);
        });
        el.querySelectorAll('.smart-video-play').forEach(btn => {
            btn.addEventListener('mousedown', e => {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
            }, true);
            btn.addEventListener('click', e => {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const item = btn.closest('[data-image-index]');
                const targetNodeId = item?.dataset.refNodeId || id;
                const imageIndex = Number(item?.dataset.refImageIndex ?? item?.dataset.imageIndex ?? 0);
                const owner = canvasState.nodes.find(n => n.id === targetNodeId);
                if(mediaKindForItem(owner?.images?.[imageIndex] || {}) !== 'video') return;
                clearImageClickTimer();
                suppressImageClickUntil = Date.now() + 260;
                hideRunTimerForNode(owner);
                smartActivateVideoPreview(btn);
            }, true);
        });
        el.querySelectorAll('.thumb-item,.image-wrap').forEach(item => {
            const thumbTarget = () => {
                const targetNodeId = item.dataset.refNodeId || id;
                const imageIndex = Number(item.dataset.refImageIndex ?? item.dataset.imageIndex ?? 0);
                const owner = canvasState.nodes.find(n => n.id === targetNodeId);
                return {targetNodeId, imageIndex, owner, image:owner?.images?.[imageIndex]};
            };
            item.setAttribute('draggable', 'false');
            item.addEventListener('dragstart', e => {
                e.preventDefault();
            });
            item.addEventListener('mousedown', e => {
                if(e.target.closest('video,audio')) return;
                if(e.button !== 0 || e.target.closest('.image-delete,.image-name-badge')) return;
                if(e.detail < 2) return;
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                clearImageClickTimer();
                suppressImageClickUntil = Date.now() + 260;
                const target = thumbTarget();
                if(mediaKindForItem(target.image || {}) === 'video'){
                    smartActivateVideoPreview(item);
                    return;
                }
                canvasState.selectedId = id;
                canvasState.selectedIds = [];
                canvasState.selectedImage = {nodeId:target.targetNodeId, index:target.imageIndex};
                /* #87：双击/快拍打开「生成详情」（无 log 为简化形态）；「预览图片」编辑器入口保留在节点工具栏 */
                openSmartImageDetail(target.targetNodeId, target.imageIndex);
            }, true);
            item.addEventListener('click', e => {
                if(e.target.closest('video,audio')) return;
                if(e.target.closest('.image-delete,.image-name-badge')) return;
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                if(Date.now() < suppressImageClickUntil) return;
                const target = thumbTarget();
                const owner = canvasState.nodes.find(n => n.id === id);
                if(mediaKindForItem(target.image || {}) === 'video'){
                    clearImageClickTimer();
                    suppressImageClickUntil = Date.now() + 260;
                    hideRunTimerForNode(target.owner || owner);
                    smartActivateVideoPreview(item);
                    return;
                }
                if(e.detail >= 2){
                    clearImageClickTimer();
                    suppressImageClickUntil = Date.now() + 260;
                    canvasState.selectedId = id;
                    canvasState.selectedIds = [];
                    canvasState.selectedImage = {nodeId:target.targetNodeId, index:target.imageIndex};
                    openSmartImageDetail(target.targetNodeId, target.imageIndex);
                    return;
                }
                clearImageClickTimer();
                imageClickTimer = setTimeout(() => {
                    imageClickTimer = null;
                hideRunTimerForNode(owner);
                canvasState.selectedId = id;
                canvasState.selectedIds = [];
                // Composer 绑定节点本身；这里记录图层焦点，用于交叠时置顶和工具栏目标。
                canvasState.selectedImage = {nodeId:target.targetNodeId, index:target.imageIndex};
                    if(smartCascadeAnyRunning()) smartCascadeSilentSelection = false;
                    syncSelectionUi();
                    scheduleComposerUpdate(180);
                }, 220);
            });
        item.addEventListener('dblclick', e => {
            if(e.target.closest('video,audio')) return;
            if(e.target.closest('.image-delete,.image-name-badge')) return;
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            clearImageClickTimer();
            suppressImageClickUntil = Date.now() + 260;
            const target = thumbTarget();
            if(mediaKindForItem(target.image || {}) === 'video'){
                smartActivateVideoPreview(item);
                return;
            }
            canvasState.selectedId = id;
            canvasState.selectedIds = [];
            canvasState.selectedImage = {nodeId:target.targetNodeId, index:target.imageIndex};
            openSmartImageDetail(target.targetNodeId, target.imageIndex);
        }, true);
        });
        el.querySelectorAll('.thumb-item,.smart-group-single-thumb').forEach(item => {
            item.addEventListener('mousedown', e => {
                if(e.target.closest('video,audio')) return;
                if(e.button !== 0 || e.target.closest('.mini-x')) return;
                if(e.detail >= 2) return;
                const node = canvasState.nodes.find(n => n.id === id);
                const refNodeId = item.dataset.refNodeId || '';
                if(refNodeId && refNodeId !== id) return;
                if(!node) return;
                const imgIndex = Number(item.dataset.imageIndex || 0);
                if(isSmartGroupNode(node)){
                    if(!node.images?.[imgIndex]) return;
                } else if((node.images || []).length <= 1) return;
                e.preventDefault(); e.stopPropagation();
                thumbDragState = {nodeId:id, imgIndex, startX:e.clientX, startY:e.clientY, detached:false};
                capturePendingUndo();
            });
        });
        el.querySelector('.node-resize-handle')?.addEventListener('mousedown', e => {
            if(e.button !== 0) return;
            e.preventDefault(); e.stopPropagation();
            const node = canvasState.nodes.find(n => n.id === id);
            if(!node) return;
            const rect = nodeRect(node);
            resizeState = {id, startX:e.clientX, startY:e.clientY, startW:rect.width, startH:rect.height};
            // 分组缩放：记录本次手势开始时所有成员的位置/尺寸快照与起始缩放，缩放过程按相对快照的比例实时计算，
            // 整体等比缩放+重排。用快照而非持久基准，移动成员后再缩放也不会回退到旧位置。
            if(isSmartGroupNode(node)){
                resizeState.startZoom = smartGroupZoom(node);
                const gx0 = Number(node.x) || 0, gy0 = Number(node.y) || 0;
                let maxR = gx0, maxB = gy0, hasM = false;
                resizeState.members = smartGroupMembers(node).map(m => {
                    const r = nodeRect(m);
                    const sx = Number(m.x) || 0, sy = Number(m.y) || 0, sw = Number(r.width) || 0, sh = Number(r.height) || 0;
                    hasM = true; maxR = Math.max(maxR, sx + sw); maxB = Math.max(maxB, sy + sh);
                    return {id:m.id, sx, sy, sw, sh, isImage:isSmartImageNode(m)};
                });
                // 记录“贴合内容时的框尺寸”作为缩放映射基准（而不是当前可能含留白的框宽），
                // 这样从放大很多的框往回缩时，框是线性跟随手柄缩小、而不是一下子跳到内容边缘。
                resizeState.contentFitW = hasM ? Math.max(1, maxR - gx0 + 16) : (rect.width || 1);
                resizeState.contentFitH = hasM ? Math.max(1, maxB - gy0 + 16) : (rect.height || 1);
            }
            document.body.classList.add('smart-node-resize');
            capturePendingUndo();
        });
        const beginNodeDrag = e => {
            if(e.button !== 0 || e.target.closest('.mini-x, .smart-node-floating-menu, .node-resize-handle, .thumb-item, .node-port, .prompt-node-control, .conversation-node-message-text, select, input, textarea, button')) return;
            if(e.target.closest('.prompt-node-pill, textarea:not(.prompt-node-text)')) return;
            e.preventDefault(); e.stopPropagation();
            window.getSelection?.()?.removeAllRanges?.();
            if(document.activeElement?.blur) document.activeElement.blur();
            let node = canvasState.nodes.find(n => n.id === id);
            if(!node) return;
            let altDuplicated = false;
            if(e.altKey){
                const copied = duplicateForAltDrag(node, e.shiftKey);
                if(copied && copied.id !== node.id){ node = copied; altDuplicated = true; }
            }
            let dragIds = canvasState.selectedIds.includes(node.id) ? canvasState.selectedIds.slice() : [node.id];
            if(isSmartGroupNode(node)){
                const memberIds = smartGroupMembers(node).map(member => member.id);
                dragIds = Array.from(new Set([...dragIds, ...memberIds]));
            }
            const group = dragIds.map(dragId => {
                const n = canvasState.nodes.find(x => x.id === dragId);
                return n ? {id:n.id, ox:Number(n.x) || 0, oy:Number(n.y) || 0} : null;
            }).filter(Boolean);
            dragState = {id:node.id, startX:e.clientX, startY:e.clientY, ox:node.x || 0, oy:node.y || 0, group, groupIds:group.map(item => item.id), ctrlGroup:Boolean(e.ctrlKey)};
            if(composerDragSettleTimer){
                clearTimeout(composerDragSettleTimer);
                composerDragSettleTimer = 0;
            }
            document.body.classList.remove('smart-node-drag-settling');
            document.body.classList.add('smart-node-drag');
            // Alt+drag 复制+放置是同一步撤销：duplicateForAltDrag 已推送复制前快照。
            // 若这里再捕获 pending 快照（其中已含复制节点），一次 Ctrl+Z 只回退“拖动”，
            // 复制节点仍留在画布上，用户需再按一次才撤掉复制。
            if(!altDuplicated) capturePendingUndo();
        };
        el.querySelectorAll('.node-port').forEach(port => {
            port.addEventListener('mousedown', e => {
                if(e.button !== 0) return;
                e.preventDefault(); e.stopPropagation();
                const portType = port.dataset.port;
                const p = screenToWorld(e);
                portDragState = {
                    fromId:id,
                    fromPort:portType,
                    currentWorld:p,
                    hoverTargetId:'',
                    hoverPort:'',
                    moved:false
                };
                shell.classList.add('port-dragging');
                capturePendingUndo();
                ensurePortDragPathElement();
                updatePortDragVisual();
            });
            port.addEventListener('click', e => { e.stopPropagation(); });
            port.addEventListener('dblclick', e => { e.stopPropagation(); });
        });
        el.onmousedown = beginNodeDrag;
        el.ondragover = e => setSmartDropCopyEffect(e);
        el.ondrop = async e => {
            e.preventDefault();
            e.stopPropagation();
            const payload = await resolveSmartImageDropPayload(e.dataTransfer);
            if(payload.type === 'none') return;
            await handleSmartImageDropPayload(payload, id);
        };
    });
}
function rectOverlapNode(draggedId, x, y, w, h, excludeIds=[]){
    const cx = x + w/2, cy = y + h/2;
    const excluded = new Set([draggedId, ...(excludeIds || [])]);
    for(const n of canvasState.nodes){
        if(excluded.has(n.id)) continue;
        const r = nodeRect(n);
        if(cx >= r.x && cx <= r.x + r.width && cy >= r.y && cy <= r.y + r.height) return n;
    }
    return null;
}
function restoreDraggedNodePosition(){
    if(!dragState) return;
    (dragState.group || [{id:dragState.id, ox:dragState.ox, oy:dragState.oy}]).forEach(item => {
        const n = canvasState.nodes.find(x => x.id === item.id);
        if(n){
            n.x = item.ox;
            n.y = item.oy;
        }
    });
}
function pruneSmartGroupMembershipsForNode(node){
    if(!node || !node.id) return false;
    // 节点拖出分组：从所有分组移除自己（保持当前尺寸，不自动放大）。
    // 分组合并已改为“释放被拖分组、只并入其成员”，所以这里只需处理单个节点的退组。
    let changed = false;
    canvasState.nodes.forEach(group => {
        if(!isSmartGroupNode(group) || !Array.isArray(group.items) || !group.items.includes(node.id)) return;
        group.items = group.items.filter(id => id !== node.id);
        changed = true;
    });
    return changed;
}
function clearDropHighlight(){
    world.querySelectorAll('.image-node.drop-target').forEach(el => el.classList.remove('drop-target'));
}
function setDropHighlight(targetId){
    clearDropHighlight();
    if(!targetId) return;
    const el = world.querySelector(`.image-node[data-id="${targetId}"]`);
    if(el) el.classList.add('drop-target');
}
function cleanupAgentTuiState(nodeId) {
    const state = agentTuiState.get(nodeId);
    if (state) {
        if (state.socket) {
            try { state.socket.close(); } catch (e) {}
        }
        agentTuiState.delete(nodeId);
    }
}

function insertLoopNodeIntoConnection(loopNode, hit){
    if(!loopNode || loopNode.type !== 'smart-loop' || !hit?.conn) return false;
    const conn = hit.conn;
    const kind = conn.kind || 'flow';
    canvas.connections = (canvas.connections || []).filter((c, index) => index !== hit.index);
    canvasState.nodes.forEach(n => {
        if(Array.isArray(n.inputNodeIds)) n.inputNodeIds = n.inputNodeIds.filter(id => !(n.id === conn.to && id === conn.from));
    });
    addConnection(conn.from, loopNode.id, kind === 'flow' ? 'flow' : 'input');
    connectInputNode(loopNode.id, conn.to);
    return true;
}
function updateLoopInsertPreview(){
    const node = dragState ? canvasState.nodes.find(n => n.id === dragState.id) : null;
    const next = node?.type === 'smart-loop' && dragState.ctrlGroup && (dragState.group || []).length <= 1
        ? insertionConnectionForNode(node)
        : null;
    const nextPreview = next ? {index:next.index} : null;
    const changed = (loopInsertPreview?.index ?? -1) !== (nextPreview?.index ?? -1);
    loopInsertPreview = nextPreview;
    if(changed) scheduleConnectionLayerRefresh();
    return next;
}
// #88：删除范围三选一对话框（仅删画布图 / 画布+历史原图 / 取消），Promise 返回 'canvas'|'both'|'cancel'。
function confirmCanvasImageDeleteScope(){
    return new Promise(resolve => {
        const existing = document.getElementById('smartImageDeleteScopeBackdrop');
        if(existing) existing.remove();
        const backdrop = document.createElement('div');
        backdrop.id = 'smartImageDeleteScopeBackdrop';
        backdrop.className = 'asset-delete-warning-backdrop';
        const finish = choice => {
            backdrop.remove();
            document.removeEventListener('keydown', onKey, true);
            resolve(choice);
        };
        const onKey = e => { if(e.key === 'Escape'){ e.stopPropagation(); finish('cancel'); } };
        backdrop.innerHTML = `
            <div class="asset-delete-warning-dialog" role="dialog" aria-modal="true">
                <h3>${tr('smart.imageDeleteScopeTitle')}</h3>
                <p>${tr('smart.imageDeleteScopeDesc')}</p>
                <div class="asset-delete-warning-actions">
                    <button class="asset-delete-warning-cancel" data-scope="cancel">${tr('smart.imageDeleteScopeCancel')}</button>
                    <button class="asset-delete-warning-cancel" data-scope="canvas">${tr('smart.imageDeleteScopeCanvasOnly')}</button>
                    <button class="asset-delete-warning-confirm" data-scope="both">${tr('smart.imageDeleteScopeBoth')}</button>
                </div>
            </div>`;
        document.addEventListener('keydown', onKey, true);
        backdrop.addEventListener('click', e => {
            if(e.target === backdrop){ finish('cancel'); return; }
            const scope = e.target.closest('[data-scope]')?.dataset.scope;
            if(scope) finish(scope);
        });
        document.body.appendChild(backdrop);
    });
}
async function deleteImage(id, imageIndex){
    const node = canvasState.nodes.find(n => n.id === id);
    if(!node || imageIndex < 0) return;
    // #65: 生成中的节点禁止删单图（写回仍在飞，删图会破坏批次几何/引用）。
    if(smartNodeInFlight(node)){ toast(tr('smart.nodeDeleteBusy')); return; }
    // #88 删除双向：该图同时是某条生成历史的「当前原图」时，先问删除范围；
    // 无历史关联维持原静默删除。选「画布+历史原图」走项目级删除链路
    //（墓碑+回收区 7 天可恢复+undo 令牌），不再级联归档。
    const logCtx = smartHistoryContextForImage(node, imageIndex);
    let alsoDeleteOriginal = false;
    if(logCtx){
        const choice = await confirmCanvasImageDeleteScope();
        if(choice === 'cancel') return;
        alsoDeleteOriginal = choice === 'both';
    }
    pushUndo();
    const removedImage = (node.images || [])[imageIndex] || null;
    node.images = (node.images || []).filter((_, index) => index !== imageIndex);
    const batchTotal = Number(node?._batchTotal) || 0;
    if(batchTotal > 0 && Number(node?.pending || 0) === 0){
        // 用户手动删图：任务规模随之下调，外框/网格按剩余图片自适应（不留虚线空位）。
        // 生成中（pending）不动——写回仍在飞，保留 _batchTotal 稳住网格几何。
        if(node.images.length === 0){
            node._batchTotal = 0;
            delete node._generationAspect;
        }else{
            node._batchTotal = node.images.length;
        }
    }
    if(node.images.length <= 1) node.title = tr('smart.nodeTitleImage');
    if(canvasState.selectedImage.nodeId === id) canvasState.selectedImage = {nodeId:id, index:Math.min(canvasState.selectedImage.index, node.images.length - 1)};
    if(canvasState.selectedImage.index < 0) canvasState.selectedImage = {nodeId:'', index:-1};
    render();
    scheduleSave();
    if(alsoDeleteOriginal){
        void deleteSmartProjectOriginal(logCtx.historyId, logCtx.outputIndex, {skipConfirm:true});
    } else if(removedImage){
        // #74 (D1) + #146: 被删的单图若无其他引用 → 移入回收区。
        // #146: 延迟归档，撤销时取消，避免撤销后图片 404。
        scheduleMediaArchive([removedImage]);
    }
}
function currentEditImage(){
    const node = canvasState.nodes.find(n => n.id === cropState?.nodeId);
    const index = Number(cropState?.imageIndex || 0);
    return {node, index, image:imageForDisplay(node?.images?.[index])};
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
let previewMetaExtraText = '';
function zoomPanAroundPoint(zoom, pan, factor, rect, clientX, clientY, {min=0.05, max=8}={}){
    const nextZoom = Math.max(min, Math.min(max, zoom * factor));
    if(!rect || nextZoom === zoom) return {zoom:nextZoom, pan:{...pan}};
    const originX = clientX - rect.left - rect.width / 2;
    const originY = clientY - rect.top - rect.height / 2;
    const ratio = nextZoom / zoom;
    return {
        zoom:nextZoom,
        pan:{
            x:pan.x - originX * (ratio - 1),
            y:pan.y - originY * (ratio - 1)
        }
    };
}
function applyPreviewTransform(){
    const frame = document.getElementById('previewFrame');
    if(frame){
        frame.style.transform = `translate(${previewPan.x}px, ${previewPan.y}px) scale(${previewZoom})`;
    }
    updateZoomLabel();
}
function resetPreviewTransform(){
    previewZoom = 1.0;
    previewPan = {x:0, y:0};
    previewComparePos = 50;
    document.getElementById('previewStage')?.style.setProperty('--compare-pos', `${previewComparePos}%`);
    applyPreviewTransform();
}
function setPreviewComparePos(clientX){
    const frame = document.getElementById('previewFrame');
    const stage = document.getElementById('previewStage');
    if(!frame || !stage) return;
    const rect = frame.getBoundingClientRect();
    const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / Math.max(1, rect.width)) * 100));
    previewComparePos = pct;
    stage.style.setProperty('--compare-pos', `${pct}%`);
}
function syncPreviewFrameSize(){
    const frame = document.getElementById('previewFrame');
    const currentImg = document.getElementById('previewCurrentImage');
    const currentVideo = document.getElementById('previewCurrentVideo');
    const compareImg = document.getElementById('previewCompareImage');
    const currentMedia = currentVideo && currentVideo.style.display !== 'none' ? currentVideo : currentImg;
    if(!frame || !currentMedia) return;
    const w = currentMedia.clientWidth || currentMedia.videoWidth || currentMedia.naturalWidth || 1;
    const h = currentMedia.clientHeight || currentMedia.videoHeight || currentMedia.naturalHeight || 1;
    frame.style.width = `${w}px`;
    frame.style.height = `${h}px`;
    if(compareImg){
        compareImg.style.width = `${w}px`;
        compareImg.style.height = `${h}px`;
    }
}
function previewResolutionText(){
    const editing = currentEditImage();
    const image = editing.image || {};
    const currentImg = document.getElementById('previewCurrentImage');
    const currentVideo = document.getElementById('previewCurrentVideo');
    const cropImg = document.getElementById('cropImage');
    const w = Number(image.natural_w || image.width || image.w || 0) || Number(currentVideo?.videoWidth || 0) || Number(currentImg?.naturalWidth || 0) || Number(cropImg?.naturalWidth || 0);
    const h = Number(image.natural_h || image.height || image.h || 0) || Number(currentVideo?.videoHeight || 0) || Number(currentImg?.naturalHeight || 0) || Number(cropImg?.naturalHeight || 0);
    if(!w || !h) return '';
    return `${tr('smart.resolution')}: ${Math.round(w)} x ${Math.round(h)}`;
}
function updatePreviewMetaHint(extraText=previewMetaExtraText){
    previewMetaExtraText = extraText || '';
    const hint = document.getElementById('previewMetaHint');
    if(!hint) return;
    hint.textContent = [previewResolutionText(), previewMetaExtraText].filter(Boolean).join(' · ');
}
function rememberPreviewImageResolution(){
    const editing = currentEditImage();
    const image = editing.image;
    if(!image) return;
    const currentImg = document.getElementById('previewCurrentImage');
    const currentVideo = document.getElementById('previewCurrentVideo');
    const cropImg = document.getElementById('cropImage');
    const w = Number(currentVideo?.videoWidth || 0) || Number(currentImg?.naturalWidth || 0) || Number(cropImg?.naturalWidth || 0);
    const h = Number(currentVideo?.videoHeight || 0) || Number(currentImg?.naturalHeight || 0) || Number(cropImg?.naturalHeight || 0);
    if(w > 0 && h > 0 && (!image.natural_w || !image.natural_h)){
        image.natural_w = w;
        image.natural_h = h;
        scheduleSave();
    }
}
function previewCompareSources(){
    const editing = currentEditImage();
    const node = editing.node;
    if(!node) return [];
    const savedRefs = Array.isArray(node.runInputRefs) ? node.runInputRefs.filter(ref => ref?.url) : [];
    const upstream = savedRefs.length ? savedRefs : inputImagesFor(node);
    const dedup = [];
    const seen = new Set();
    for(const img of upstream){
        if(!img?.url || seen.has(img.url) || mediaKindForItem(img) !== 'image') continue;
        seen.add(img.url);
        dedup.push(img);
    }
    if(dedup.length) return dedup;
    const sourceId = node.sourceNodeId;
    if(sourceId){
        const src = canvasState.nodes.find(n => n.id === sourceId);
        if(src && (src.images || []).length){
            for(const img of src.images){
                if(!img?.url || seen.has(img.url) || mediaKindForItem(img) !== 'image') continue;
                seen.add(img.url);
                dedup.push(img);
            }
        }
    }
    return dedup;
}
function refreshComparePanel(){
    const stage = document.getElementById('previewStage');
    const compareImg = document.getElementById('previewCompareImage');
    const currentImg = document.getElementById('previewCurrentImage');
    const currentVideo = document.getElementById('previewCurrentVideo');
    const compareLayer = document.getElementById('previewCompareLayer');
    const compareHandle = document.getElementById('previewCompareHandle');
    const thumbsEl = document.getElementById('compareThumbs');
    const toggle = document.getElementById('compareToggleBtn');
    const editing = currentEditImage();
    const curUrl = editing.image?.url || '';
    const isVideoPreview = mediaKindForItem(editing.image || {}) === 'video';
    const onCurrentLoaded = () => {
        if(currentImg.dataset.previewQuick !== '1') rememberPreviewImageResolution();
        syncPreviewFrameSize();
        updatePreviewMetaHint();
    };
    if(isVideoPreview){
        currentImg.onload = null;
        currentImg.onerror = null;
        currentImg.removeAttribute('src');
        currentImg.style.display = 'none';
        if(currentVideo){
            const previewSrc = displayMediaUrl(editing.image || curUrl);
            currentVideo.style.display = 'block';
            currentVideo.onloadedmetadata = onCurrentLoaded;
            currentVideo.onloadeddata = onCurrentLoaded;
            if(currentVideo.getAttribute('src') !== previewSrc){
                currentVideo.src = previewSrc;
                currentVideo.load?.();
            }
            if(currentVideo.readyState >= 1) requestAnimationFrame(onCurrentLoaded);
        }
        previewCompareOn = false;
        previewCompareIndex = -1;
        stage.classList.remove('compare-on');
        if(compareLayer) compareLayer.style.display = 'none';
        if(compareHandle) compareHandle.style.display = 'none';
        if(thumbsEl){ thumbsEl.style.display = 'none'; thumbsEl.innerHTML = ''; }
        if(toggle){
            toggle.disabled = true;
            toggle.style.opacity = '.45';
            toggle.classList.remove('active');
            toggle.title = tr('smart.compareEmpty');
        }
        updatePreviewMetaHint(editing.node?.runPrompt ? `${tr('smart.runPromptPrefix')}${editing.node.runPrompt.slice(0, 60)}` : '');
        return;
    }
    if(currentVideo){
        currentVideo.pause?.();
        currentVideo.onloadedmetadata = null;
        currentVideo.onloadeddata = null;
        currentVideo.removeAttribute('src');
        currentVideo.load?.();
        currentVideo.style.display = 'none';
    }
    currentImg.style.display = 'block';
    currentImg.onload = onCurrentLoaded;
    currentImg.onerror = () => {
        if(currentImg.dataset.proxyFallbackTried === '1') return;
        const fallback = proxiedMediaUrl(editing.image || curUrl);
        if(!fallback || fallback === currentImg.getAttribute('src')) return;
        currentImg.dataset.proxyFallbackTried = '1';
        currentImg.src = fallback;
    };
    const previewSrc = displayMediaUrl(editing.image || curUrl);
    const quickPreviewSrc = smartMediaPreviewUrl(editing.image || curUrl, 1536);
    const previewToken = `${editing.node?.id || ''}:${editing.index ?? 0}:${Date.now()}`;
    currentImg.dataset.previewSrcToken = previewToken;
    const loadFullPreview = () => {
        if(imageEditMode !== 'preview' || !imageEditModal.classList.contains('open')) return;
        if(currentImg.dataset.previewSrcToken !== previewToken) return;
        currentImg.dataset.previewQuick = '';
        if(currentImg.getAttribute('src') !== previewSrc) currentImg.src = previewSrc;
    };
    if(quickPreviewSrc && quickPreviewSrc !== previewSrc){
        currentImg.dataset.proxyFallbackTried = '';
        currentImg.dataset.previewQuick = '1';
        if(currentImg.getAttribute('src') !== quickPreviewSrc) currentImg.src = quickPreviewSrc;
        requestAnimationFrame(() => setTimeout(loadFullPreview, 120));
    } else if(currentImg.getAttribute('src') !== previewSrc) {
        currentImg.dataset.proxyFallbackTried = '';
        currentImg.dataset.previewQuick = '';
        currentImg.src = previewSrc;
    } else {
        currentImg.dataset.previewQuick = '';
    }
    if(currentImg.complete && currentImg.naturalWidth) requestAnimationFrame(onCurrentLoaded);
    const sources = previewCompareSources();
    const hasSource = sources.length > 0;
    if(toggle){
        toggle.disabled = !hasSource;
        toggle.style.opacity = hasSource ? '1' : '.45';
        toggle.title = hasSource ? tr('smart.compareHover') : tr('smart.compareEmpty');
        toggle.classList.toggle('active', hasSource && previewCompareOn);
    }
    if(!hasSource){
        previewCompareOn = false;
        previewCompareIndex = -1;
        stage.classList.remove('compare-on');
        if(compareLayer) compareLayer.style.display = 'none';
        if(compareHandle) compareHandle.style.display = 'none';
        thumbsEl.style.display = 'none';
        updatePreviewMetaHint(editing.node?.runPrompt ? `${tr('smart.runPromptPrefix')}${editing.node.runPrompt.slice(0, 60)}` : '');
        return;
    }
    const sliderActive = previewCompareOn && previewCompareIndex >= 0 && previewCompareIndex < sources.length;
    if(sliderActive){
        const src = sources[previewCompareIndex];
        compareImg.src = src?.url || '';
        compareImg.onload = syncPreviewFrameSize;
        syncPreviewFrameSize();
        stage.classList.add('compare-on');
        if(compareLayer) compareLayer.style.display = '';
        if(compareHandle) compareHandle.style.display = '';
    } else {
        stage.classList.remove('compare-on');
        if(compareLayer) compareLayer.style.display = 'none';
        if(compareHandle) compareHandle.style.display = 'none';
    }
    if(previewCompareOn){
        thumbsEl.style.display = 'inline-flex';
        thumbsEl.innerHTML = sources.map((s, i) => `<button type="button" class="compare-thumb ${i === previewCompareIndex ? 'active' : ''}" data-compare-idx="${i}" title="${escapeHtml(i === previewCompareIndex ? tr('smart.compareCancelTip') : tr('smart.compareUseTip'))}">${smartPreviewImgHtml(s.url, 256)}</button>`).join('');
        bindSmartPreviewImageFallbacks(thumbsEl);
        thumbsEl.querySelectorAll('[data-compare-idx]').forEach(btn => {
            btn.onclick = e => {
                e.preventDefault(); e.stopPropagation();
                const idx = Number(btn.dataset.compareIdx);
                previewCompareIndex = (previewCompareIndex === idx) ? -1 : idx;
                refreshComparePanel();
            };
        });
    } else {
        thumbsEl.style.display = 'none';
        thumbsEl.innerHTML = '';
    }
    let txt = editing.node?.runPrompt ? `${tr('smart.runPromptPrefix')}${editing.node.runPrompt.slice(0, 60)}` : '';
    if(previewCompareOn && !sliderActive) txt = (txt ? `${txt} · ` : '') + tr('smart.compareHintPick');
    updatePreviewMetaHint(txt);
}
function togglePreviewCompare(){
    const sources = previewCompareSources();
    if(!sources.length){ toast(tr('smart.compareNoSource')); return; }
    previewCompareOn = !previewCompareOn;
    if(previewCompareOn && (previewCompareIndex < 0 || previewCompareIndex >= sources.length)) previewCompareIndex = 0;
    if(!previewCompareOn) previewCompareIndex = -1;
    refreshComparePanel();
}
function currentPreviewVideo(){
    if(!imageEditModal.classList.contains('open')) return null;
    if(mediaKindForItem(currentEditImage().image || {}) !== 'video') return null;
    return document.getElementById('previewCurrentVideo');
}
function videoFrameStep(){
    const image = currentEditImage().image || {};
    const fps = Number(image.fps || image.frameRate || image.frame_rate || image.framespersecond || image.frames_per_second || 0);
    return 1 / Math.max(1, Math.min(120, Number.isFinite(fps) && fps > 0 ? fps : 30));
}
function seekPreviewVideoFrames(direction){
    const video = currentPreviewVideo();
    if(!video || video.readyState < 1) return false;
    video.pause?.();
    const step = videoFrameStep();
    const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
    const maxTime = duration ? Math.max(0, duration - step / 2) : Number.MAX_SAFE_INTEGER;
    video.currentTime = Math.max(0, Math.min(maxTime, Number(video.currentTime || 0) + direction * step));
    return true;
}
function waitForVideoEvent(video, eventName, timeout=1500){
    return new Promise(resolve => {
        let done = false;
        const finish = () => {
            if(done) return;
            done = true;
            clearTimeout(timer);
            video.removeEventListener(eventName, finish);
            resolve();
        };
        const timer = setTimeout(finish, timeout);
        video.addEventListener(eventName, finish, {once:true});
    });
}
async function seekVideoForFrame(video, time){
    if(Math.abs(Number(video.currentTime || 0) - time) <= 0.002) return;
    video.currentTime = time;
    await waitForVideoEvent(video, 'seeked', 2200);
}
async function exportVideoFrame(which='current'){
    const video = currentPreviewVideo();
    if(!video){ toast(tr('smart.noVideoFrames')); return; }
    if(video.readyState < 2) await waitForVideoEvent(video, 'loadeddata', 2200);
    if(!video.videoWidth || !video.videoHeight){ toast(tr('smart.videoNotLoaded')); return; }
    const originalTime = Number(video.currentTime || 0);
    const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
    const step = videoFrameStep();
    const target = which === 'first'
        ? 0
        : which === 'last'
            ? Math.max(0, duration - step / 2)
            : originalTime;
    const suffix = which === 'first' ? 'first-frame' : which === 'last' ? 'last-frame' : 'current-frame';
    try {
        video.pause?.();
        await seekVideoForFrame(video, target);
        const canvasEl = document.createElement('canvas');
        canvasEl.width = video.videoWidth;
        canvasEl.height = video.videoHeight;
        const ctx = canvasEl.getContext('2d');
        ctx.drawImage(video, 0, 0, canvasEl.width, canvasEl.height);
        const blob = await new Promise(resolve => canvasEl.toBlob(resolve, 'image/png'));
        if(!blob) throw new Error(tr('smart.frameExportFailed'));
        const editing = currentEditImage();
        const rawName = editing.image?.name || fileNameFromUrl(editing.image?.url || '') || 'video';
        const base = String(rawName).replace(/\.[a-z0-9]{2,8}$/i, '') || 'video';
        const filename = safeExportFileName(`${base}-${suffix}.png`, `${suffix}.png`);
        const uploaded = await uploadFiles([new File([blob], filename, {type:'image/png'})]);
        let frame = uploaded[0];
        if(!frame?.url) throw new Error(tr('smart.exportToCanvasFailed'));
        if(canvas?.format === 'firefly-canvas-v1'){
            const owned = await importProjectMediaReference({...frame, kind:'image', mime:'image/png'});
            if(!owned?.asset_id) throw new Error(tr('smart.videoFrameNotInProject'));
            frame = {...owned, url:projectMediaUrl(owned.asset_id), preview_url:projectMediaPreviewUrl(owned.asset_id)};
        }
        frame.kind = 'image';
        frame.natural_w = video.videoWidth;
        frame.natural_h = video.videoHeight;
        const rect = editing.node ? nodeRect(editing.node) : null;
        const point = rect
            ? {x:rect.x + rect.width + 240, y:rect.y + rect.height / 2}
            : viewportCenter();
        pushUndo();
        const newNode = createImageNodeAt(point, [frame], {select:true, skipUndo:true});
        canvasState.selectedIds = [];
        canvasState.selectedImage = {nodeId:newNode.id, index:0};
        render();
        scheduleSave();
        toast(tr('smart.exportedToCanvas'));
        if(which !== 'current') await seekVideoForFrame(video, originalTime);
    } catch(e) {
        toast((e.message || tr('smart.frameExportFailed')).slice(0, 120));
    }
}
function editCanvasHasPixels(){
    if(editTextHasContent()) return true;
    const canvasEl = editDrawCanvas();
    const data = canvasEl.getContext('2d').getImageData(0, 0, canvasEl.width, canvasEl.height).data;
    for(let i = 3; i < data.length; i += 4) if(data[i] > 0) return true;
    return false;
}
function circledNumber(n){ return n >= 1 && n <= 20 ? String.fromCharCode(0x2460 + n - 1) : String(n); }
function drawNumberLabel(point){
    const ctx = editDrawCanvas().getContext('2d');
    const size = Math.max(18, editBrushSize() * 2.2);
    const text = circledNumber(brushLabelCounter++);
    setupDrawStyle(ctx);
    ctx.save(); ctx.font = `900 ${size}px Arial, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.lineWidth = Math.max(3, size / 8);
    ctx.strokeStyle = 'rgba(255,255,255,0.92)'; ctx.strokeText(text, point.x, point.y); ctx.fillStyle = brushColor(); ctx.fillText(text, point.x, point.y); ctx.restore();
}
function updateZoomLabel(){
    const el = document.getElementById('imageEditZoomLabel');
    if(!el) return;
    el.textContent = Math.round((imageEditMode === 'preview' ? previewZoom : imageEditZoom) * 100) + '%';
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
function navigatePreviewImage(delta){
    if(!imageEditModal.classList.contains('open') || imageEditMode !== 'preview') return;
    // 分组预览：跨成员节点按整组序列左右切换（openImageEditor 会重置 previewNavState，故切换后重新挂回分组上下文）。
    if(previewNavState.groupId && Array.isArray(previewNavState.seq) && previewNavState.seq.length > 1){
        const groupId = previewNavState.groupId;
        const seq = previewNavState.seq;
        const pos = (Number(previewNavState.seqPos || 0) + Number(delta || 0) + seq.length) % seq.length;
        const ref = seq[pos];
        openImageEditor(ref.nodeId, ref.index);
        if(imageEditModal.classList.contains('open')){
            previewNavState.groupId = groupId;
            previewNavState.seq = seq;
            previewNavState.seqPos = pos;
            // openImageEditor 重置了 previewNavState，需在恢复分组上下文后重算导航/下载全部按钮。
            setImageEditMode('preview');
        }
        return;
    }
    const node = canvasState.nodes.find(n => n.id === previewNavState.nodeId);
    const images = (node?.images || []).filter(img => img?.url);
    if(!node || images.length <= 1) return;
    const count = images.length;
    const next = (Number(previewNavState.index || 0) + Number(delta || 0) + count) % count;
    openImageEditor(node.id, next);
}
function openImagePreview(nodeId, imageIndex=0){
    openImageEditor(nodeId, imageIndex);
    setImageEditMode('preview');
}
async function uploadImageBlobs(blobs){
    const form = new FormData();
    blobs.forEach(item => form.append('files', item.blob, item.name));
    const data = await fetch('/api/ai/upload', {method:'POST', body:form}).then(r => r.json());
    return data.files || [];
}
function replaceEditedImage(file, extra={}){
    const {node, index} = currentEditImage();
    if(!node || !file) return false;
    const assetId = file.asset_id || extra.asset_id || (node.images[index] || {}).asset_id || '';
    node.images[index] = {...(node.images[index] || {}), url:file.url, name:file.name, kind:file.kind || mediaKindForItem(file), natural_w:0, natural_h:0, asset_id:assetId, ...extra};
    if((node.images || []).length === 1){ delete node.w; delete node.h; }
    canvasState.selectedId = node.id; canvasState.selectedImage = {nodeId:node.id, index};
    return true;
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
function savePromptDraftForCurrent(){
    if(promptInput?.dataset?.promptLocked === '1') return;
    const subject = activeComposerNode();
    if(!subject) return;
    if(promptInput?.dataset?.preserveDraftOnce === '1' && subject.promptDraftHtml){
        delete promptInput.dataset.preserveDraftOnce;
        return;
    }
    subject.promptDraftHtml = promptInput.innerHTML;
    subject.promptDraftText = promptPlainText();
    subject.runSettings = cloneSmartSettings(settings);
}
function setPromptDraftForNode(node, text){
    if(!isSmartRunnableNode(node)) return;
    const value = String(text || '');
    node.promptDraftHtml = escapeHtml(value);
    node.promptDraftText = value;
    node.promptDraftTouched = true;
    if(activeSettingsSubject()?.id === node.id && promptInput){
        promptInput.textContent = value;
        delete promptInput.dataset.preserveDraftOnce;
    }
}
function loadPromptDraft(subject){
    if(subject?.promptDraftHtml){
        const hasToken = String(subject.promptDraftHtml || '').includes('mention-image-token');
        promptInput.innerHTML = hasToken
            ? subject.promptDraftHtml
            : (promptHtmlWithMentionTokens(subject.promptDraftText || subject.runPrompt || '', subject.runPromptRefs || []) || subject.promptDraftHtml);
    } else if(typeof subject?.runPrompt === 'string'){
        const rebuilt = promptHtmlWithMentionTokens(subject.runPrompt, subject.runPromptRefs || []);
        if(rebuilt) promptInput.innerHTML = rebuilt;
        else setPromptText(subject.runPrompt);
    } else {
        setPromptText('');
    }
}
function restoreLongContentTriggerFocus(identity, fallback){
    let target = fallback?.isConnected ? fallback : null;
    const kind = String(identity?.kind || '');
    if(!target && kind === 'input-prompt'){
        const nodeId = String(identity?.nodeId || '');
        if(activeComposerNode()?.id === nodeId){
            target = inputPromptPreview?.querySelector?.('[data-long-content-open="input-prompt"]') || null;
        }
    }
    if(!target && (kind === 'history-prompt' || kind === 'history-error')){
        const historyId = String(identity?.historyId || '');
        const cardSelector = `.log-item[data-canvas-history-id="${CSS.escape(historyId)}"]`;
        let card = smartHistoryList?.querySelector?.(cardSelector) || null;
        if(!card && smartHistoryList){
            const index = (canvas?.history || []).findIndex(log => String(log?.id || '') === historyId);
            if(index >= 0){
                const stride = smartHistoryVirtualState.stride || Math.max(1, smartHistoryList.clientWidth || 1);
                smartHistoryList.scrollLeft = Math.max(0, index * stride);
                renderSmartHistoryWindow(true);
                card = smartHistoryList.querySelector(cardSelector);
            }
        }
        target = card?.querySelector?.(`[data-long-content-open="${kind}"]`) || null;
    }
    if(target?.isConnected){
        target.focus({preventScroll:true});
        return true;
    }
    return false;
}
function openLongContentReader({title, text, trigger=null, triggerIdentity=null}){
    if(!longContentReader || !longContentReaderBody) return false;
    if(longContentSession.mode === 'readonly') closeLongContentReader({restoreFocus:false});
    if(promptNodeFlowView.mode === 'flow') exitPromptNodeFlow();
    if(longContentReader.parentElement !== document.body) document.body.appendChild(longContentReader);
    longContentSession.mode = 'readonly';
    longContentSession.kind = String(triggerIdentity?.kind || 'readonly');
    longContentSession.trigger = trigger;
    longContentSession.triggerIdentity = triggerIdentity;
    longContentReaderTitle.textContent = String(title || tr('smart.readFullContent'));
    longContentReaderBody.textContent = String(text || '');
    longContentReaderBody.scrollTop = 0;
    longContentReader.classList.add('open');
    longContentReader.setAttribute('aria-hidden', 'false');
    document.body.classList.add('long-content-reader-active');
    requestAnimationFrame(() => longContentReaderBody.focus({preventScroll:true}));
    return true;
}
function closeLongContentReader({restoreFocus=true}={}){
    if(longContentSession.mode !== 'readonly') return false;
    const trigger = longContentSession.trigger;
    const identity = longContentSession.triggerIdentity;
    longContentSession.mode = 'idle';
    longContentSession.kind = '';
    longContentSession.trigger = null;
    longContentSession.triggerIdentity = null;
    longContentReader?.classList.remove('open');
    longContentReader?.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('long-content-reader-active');
    if(restoreFocus) requestAnimationFrame(() => restoreLongContentTriggerFocus(identity, trigger));
    return true;
}
function promptNodeFlowSelection(textarea){
    return {
        start:Number(textarea?.selectionStart || 0),
        end:Number(textarea?.selectionEnd || 0),
        direction:textarea?.selectionDirection || 'none'
    };
}
function restorePromptNodeFlowSelection(textarea, selection){
    if(!textarea || !selection) return;
    try { textarea.setSelectionRange(selection.start, selection.end, selection.direction); } catch (_) {}
}
function clearPromptNodeFlowGeometry(element){
    if(!element) return;
    ['--prompt-node-flow-left','--prompt-node-flow-top','--prompt-node-flow-width','--prompt-node-flow-height','--prompt-node-flow-scale-x','--prompt-node-flow-scale-y']
        .forEach(property => element.style.removeProperty(property));
}
function positionPromptNodeFlow(){
    if(promptNodeFlowView.mode !== 'flow' || !promptNodeFlowView.element) return;
    const margin = Math.max(14, Math.min(38, Math.floor(Math.min(window.innerWidth, window.innerHeight) * 0.035)));
    const scale = composerWorldScale();
    const worldRect = world.getBoundingClientRect();
    const element = promptNodeFlowView.element;
    element.style.setProperty('--prompt-node-flow-left', `${(margin - worldRect.left) / scale.x}px`);
    element.style.setProperty('--prompt-node-flow-top', `${(margin - worldRect.top) / scale.y}px`);
    element.style.setProperty('--prompt-node-flow-width', `${Math.max(292, window.innerWidth - margin * 2)}px`);
    element.style.setProperty('--prompt-node-flow-height', `${Math.max(320, window.innerHeight - margin * 2)}px`);
    element.style.setProperty('--prompt-node-flow-scale-x', String(1 / scale.x));
    element.style.setProperty('--prompt-node-flow-scale-y', String(1 / scale.y));
}
function syncPromptNodeFlowControl(element, active){
    const button = element?.querySelector?.('.prompt-node-flow-toggle');
    if(!button) return;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    button.title = tr(active ? 'smart.promptNodeFlowExit' : 'smart.promptNodeFlowEnter');
    button.setAttribute('aria-label', button.title);
    button.innerHTML = active
        ? `<i data-lucide="minimize-2"></i><span>${escapeHtml(tr('smart.promptNodeFlowExit'))}</span><small>${escapeHtml(tr('smart.promptFlowEscape'))}</small>`
        : `<i data-lucide="maximize-2"></i><span>${escapeHtml(tr('smart.promptNodeFlowEnter'))}</span>`;
    refreshIcons();
}
function enterPromptNodeFlow(nodeId, trigger=null){
    const nodeElement = world?.querySelector?.(`.prompt-smart-node[data-id="${CSS.escape(String(nodeId || ''))}"]`);
    const textarea = nodeElement?.querySelector?.('.prompt-node-text');
    if(!nodeElement || !textarea || promptNodeFlowView.mode === 'flow') return false;
    if(longContentSession.mode === 'readonly') closeLongContentReader({restoreFocus:false});
    if(composerPromptView.mode === 'flow') exitPromptFlow();
    promptNodeFlowView.mode = 'flow';
    promptNodeFlowView.nodeId = String(nodeId || '');
    promptNodeFlowView.element = nodeElement;
    promptNodeFlowView.textarea = textarea;
    promptNodeFlowView.trigger = trigger;
    promptNodeFlowView.scrollTop = textarea.scrollTop;
    promptNodeFlowView.selection = promptNodeFlowSelection(textarea);
    nodeElement.classList.add('prompt-node-flow');
    document.body.classList.add('prompt-node-flow-active');
    syncPromptNodeFlowControl(nodeElement, true);
    positionPromptNodeFlow();
    textarea.focus({preventScroll:true});
    restorePromptNodeFlowSelection(textarea, promptNodeFlowView.selection);
    requestAnimationFrame(() => { textarea.scrollTop = promptNodeFlowView.scrollTop; });
    return true;
}
function exitPromptNodeFlow(){
    if(promptNodeFlowView.mode !== 'flow') return false;
    const element = promptNodeFlowView.element;
    const textarea = promptNodeFlowView.textarea;
    const selection = promptNodeFlowSelection(textarea);
    const scrollTop = textarea?.scrollTop || 0;
    promptNodeFlowView.mode = 'idle';
    promptNodeFlowView.selection = selection;
    promptNodeFlowView.scrollTop = scrollTop;
    element?.classList.remove('prompt-node-flow');
    clearPromptNodeFlowGeometry(element);
    document.body.classList.remove('prompt-node-flow-active');
    syncPromptNodeFlowControl(element, false);
    requestAnimationFrame(() => {
        if(textarea?.isConnected){
            textarea.focus({preventScroll:true});
            restorePromptNodeFlowSelection(textarea, selection);
            textarea.scrollTop = Math.min(scrollTop, Math.max(0, textarea.scrollHeight - textarea.clientHeight));
        }
        if(promptNodeFlowView.mode === 'idle' && promptNodeFlowView.element === element){
            promptNodeFlowView.nodeId = '';
            promptNodeFlowView.element = null;
            promptNodeFlowView.textarea = null;
            promptNodeFlowView.trigger = null;
        }
    });
    return true;
}
function closeActiveLongContentSession(){
    if(longContentSession.mode === 'readonly') return closeLongContentReader();
    if(promptNodeFlowView.mode === 'flow') return exitPromptNodeFlow();
    return false;
}
function composerWorldScale(){
    const transform = getComputedStyle(world).transform;
    const matrix = transform && transform !== 'none' ? new DOMMatrix(transform) : new DOMMatrix();
    return {
        x:Math.max(0.01, Math.abs(matrix.a || 1)),
        y:Math.max(0.01, Math.abs(matrix.d || 1))
    };
}
function promptExpandedHeightLimit(){
    const viewportH = Math.max(1, Number(shell?.clientHeight || window.innerHeight || 1));
    const currentInputH = Math.max(1, Number(promptInput?.getBoundingClientRect?.().height || promptInput?.offsetHeight || 84));
    const currentComposerH = Math.max(currentInputH, Number(composer?.getBoundingClientRect?.().height || composer?.offsetHeight || currentInputH));
    const chromeH = Math.max(112, currentComposerH - currentInputH);
    return Math.max(160, Math.floor(viewportH - chromeH - 36));
}
function clampPromptManualHeight(value){
    return Math.max(60, Math.min(promptExpandedHeightLimit(), Number(value) || 84));
}
function setPromptEditorHeight(value){
    const height = Math.max(60, Math.round(Number(value) || 84));
    promptInput.style.setProperty('--prompt-h', `${height}px`);
    return height;
}
function promptNaturalHeight(){
    return Math.max(60, Math.ceil(Number(promptInput?.scrollHeight || 0) + 2));
}
function syncComposerPromptViewControls(){
    const expanded = composerPromptView.mode === 'expanded';
    const flow = composerPromptView.mode === 'flow';
    composer?.classList.toggle('prompt-expanded', expanded);
    composer?.classList.toggle('prompt-flow', flow);
    document.body.classList.toggle('prompt-flow-active', flow);
    if(composerExpandBtn){
        composerExpandBtn.classList.toggle('active', expanded);
        composerExpandBtn.setAttribute('aria-pressed', expanded ? 'true' : 'false');
        composerExpandBtn.title = tr(expanded ? 'smart.promptCollapse' : 'smart.promptExpand');
        composerExpandBtn.setAttribute('aria-label', composerExpandBtn.title);
        composerExpandBtn.innerHTML = expanded
            ? `<i data-lucide="panel-top-close"></i><span>${escapeHtml(tr('smart.promptCollapse'))}</span>`
            : `<i data-lucide="panel-top-open"></i><span>${escapeHtml(tr('smart.promptExpand'))}</span>`;
    }
    if(composerFlowBtn){
        composerFlowBtn.classList.toggle('active', flow);
        composerFlowBtn.setAttribute('aria-pressed', flow ? 'true' : 'false');
        composerFlowBtn.title = tr(flow ? 'smart.promptFlowExit' : 'smart.promptFlowEnter');
        composerFlowBtn.setAttribute('aria-label', composerFlowBtn.title);
        composerFlowBtn.innerHTML = flow
            ? `<i data-lucide="minimize-2"></i><span>${escapeHtml(tr('smart.promptFlowExit'))}</span><small>${escapeHtml(tr('smart.promptFlowEscape'))}</small>`
            : `<i data-lucide="focus"></i><span>${escapeHtml(tr('smart.promptFlowEnter'))}</span>`;
    }
    refreshIcons();
}
function clearPromptFlowGeometry(){
    ['--prompt-flow-width','--prompt-flow-height','transform','transform-origin'].forEach(property => composer.style.removeProperty(property));
}
function recentPromptFlowImages(limit=2){
    const seen = new Set();
    const results = [];
    for(const log of sortSmartHistoryLogs(canvas?.history || [])){
        if(['pending','failed','cancelled','original_deleted'].includes(String(log?.status || ''))) continue;
        for(const output of smartHistoryImageOutputs(log)){
            const key = output.asset_id || output.url;
            if(!key || seen.has(key)) continue;
            seen.add(key);
            results.push({...output, historyId:String(log?.id || '')});
            if(results.length >= limit) return results;
        }
    }
    return results;
}
function promptFlowContextImageHtml(item, {kind='reference', index=0}={}){
    const label = tr(kind === 'recent' ? 'smart.flowRecentImage' : 'smart.flowReferenceImage').replace('{n}', String(index + 1));
    const image = smartPreviewImgHtml(item, 512, `alt="${escapeAttr(label)}" draggable="false"`);
    if(kind === 'recent'){
        return `<button class="prompt-flow-context-image is-recent" type="button" data-flow-recent-log-id="${escapeAttr(item.historyId || '')}" data-flow-recent-output-index="${Number(item.outputIndex || 0)}" data-flow-recent-url="${escapeAttr(item.url || '')}" title="${escapeAttr(trf('smart.viewNodeDetails', {label: label}))}">${image}<span>${escapeHtml(label)}</span></button>`;
    }
    return `<figure class="prompt-flow-context-image is-reference" title="${escapeAttr(item.name || label)}">${image}<figcaption>${escapeHtml(label)}</figcaption></figure>`;
}
function fitPromptFlowRecentImages(root){
    const list = root?.querySelector?.('.prompt-flow-context-group.is-recent .prompt-flow-context-list');
    const cards = [...(list?.querySelectorAll?.('.prompt-flow-context-image.is-recent') || [])];
    if(!list || !cards.length || !list.clientWidth || !list.clientHeight) return;
    const ratios = cards.map(card => {
        const image = card.querySelector('img');
        return image?.naturalWidth > 0 && image?.naturalHeight > 0
            ? image.naturalWidth / image.naturalHeight
            : 0;
    });
    if(ratios.some(ratio => !ratio)) return;
    const styles = getComputedStyle(list);
    const gap = Number.parseFloat(styles.columnGap || styles.gap || '0') || 0;
    const availableWidth = Math.max(1, list.clientWidth - gap * Math.max(0, cards.length - 1));
    const fittedHeight = Math.max(1, Math.min(list.clientHeight, availableWidth / ratios.reduce((sum, ratio) => sum + ratio, 0)));
    cards.forEach((card, index) => {
        const fittedWidth = fittedHeight * ratios[index];
        card.style.height = `${fittedHeight}px`;
        card.style.width = `${fittedWidth}px`;
        card.style.flexBasis = `${fittedWidth}px`;
    });
}
function bindPromptFlowImageAspectRatios(root){
    root?.querySelectorAll?.('.prompt-flow-context-image img').forEach(image => {
        const sync = () => {
            const width = Number(image.naturalWidth || 0);
            const height = Number(image.naturalHeight || 0);
            if(width > 0 && height > 0){
                image.closest('.prompt-flow-context-image')?.style.setProperty('--prompt-flow-media-ratio', String(width / height));
                fitPromptFlowRecentImages(root);
            }
        };
        if(image.complete) sync();
        else image.addEventListener('load', sync, {once:true});
    });
    root?._promptFlowRecentResizeObserver?.disconnect?.();
    const recentList = root?.querySelector?.('.prompt-flow-context-group.is-recent .prompt-flow-context-list');
    if(recentList && typeof ResizeObserver === 'function'){
        root._promptFlowRecentResizeObserver = new ResizeObserver(() => fitPromptFlowRecentImages(root));
        root._promptFlowRecentResizeObserver.observe(recentList);
    }
    requestAnimationFrame(() => fitPromptFlowRecentImages(root));
}
function promptFlowReferenceImagesFor(node){
    if(!node) return [];
    const upstream = smartImageUsesWorkflowInput(node)
        ? workflowInputImagesFor(node)
        : inputImagesFor(node);
    const combined = [...upstream, ...manualReferenceImagesFor(node), ...collectMentionedImagesFromPrompt()];
    const seen = new Set();
    return combined.filter(item => {
        const key = String(item?.url || '');
        if(!key || seen.has(key)) return false;
        seen.add(key);
        return !isVideoMediaItem(item) && mediaKindForItem(item) === 'image';
    }).map((item, index) => ({
        ...item,
        name:item.name || `图${index + 1}`,
        role:item.role || `image_${index + 1}`,
        imageIndex:Number.isFinite(Number(item.imageIndex)) ? Number(item.imageIndex) : index
    }));
}
function renderPromptFlowContext(node){
    if(!promptFlowContext) return;
    if(composerPromptView.mode !== 'flow' || !node){
        promptFlowContext._promptFlowRecentResizeObserver?.disconnect?.();
        promptFlowContext._promptFlowRecentResizeObserver = null;
        promptFlowContext.innerHTML = '';
        promptFlowContext.classList.remove('has-content');
        return;
    }
    const recent = recentPromptFlowImages(2);
    const references = promptFlowReferenceImagesFor(node);
    const recentHtml = recent.map((item, index) => promptFlowContextImageHtml(item, {kind:'recent', index})).join('');
    const referencesHtml = references.map((item, index) => promptFlowContextImageHtml(item, {kind:'reference', index})).join('');
    const recentCount = recent.length ? tr('smart.flowRecentCount').replace('{n}', String(recent.length)) : '—';
    const referenceCount = tr('smart.flowReferenceCount').replace('{n}', String(references.length));
    promptFlowContext.innerHTML = `<div class="prompt-flow-context-group is-recent"><header><strong>${escapeHtml(tr('smart.flowRecent'))}</strong><span>${escapeHtml(recentCount)}</span></header><div class="prompt-flow-context-list">${recentHtml || `<span class="prompt-flow-context-empty">${escapeHtml(tr('smart.flowRecentEmpty'))}</span>`}</div></div><div class="prompt-flow-context-group is-reference"><header><strong>${escapeHtml(tr('smart.flowReferences'))}</strong><span>${escapeHtml(referenceCount)}</span></header><div class="prompt-flow-context-list">${referencesHtml || `<span class="prompt-flow-context-empty">${escapeHtml(tr('smart.flowReferenceEmpty'))}</span>`}</div></div>`;
    promptFlowContext.classList.add('has-content');
    bindSmartPreviewImageFallbacks(promptFlowContext);
    bindPromptFlowImageAspectRatios(promptFlowContext);
    promptFlowContext.querySelectorAll('[data-flow-recent-log-id]').forEach(button => {
        button.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            openSmartHistoryDetail({
                url:button.dataset.flowRecentUrl || '',
                kind:'image',
                historyId:button.dataset.flowRecentHistoryId || '',
                outputIndex:Number(button.dataset.flowRecentOutputIndex || 0)
            });
        };
    });
}
function positionPromptFlowComposer(){
    if(composerPromptView.mode !== 'flow') return;
    const margin = Math.max(12, Math.min(32, Math.floor(Math.min(window.innerWidth, window.innerHeight) * 0.03)));
    const scale = composerWorldScale();
    const worldRect = world.getBoundingClientRect();
    const targetWidth = Math.max(320, Math.min(1320, window.innerWidth - margin * 2));
    const targetHeight = Math.max(360, window.innerHeight - margin * 2);
    const screenLeft = Math.max(margin, (window.innerWidth - targetWidth) / 2);
    composer.style.left = `${(screenLeft - worldRect.left) / scale.x}px`;
    composer.style.top = `${(margin - worldRect.top) / scale.y}px`;
    composer.style.setProperty('--prompt-flow-width', `${targetWidth}px`);
    composer.style.setProperty('--prompt-flow-height', `${targetHeight}px`);
    composer.style.transformOrigin = 'top left';
    composer.style.transform = `scale(${1 / scale.x}, ${1 / scale.y})`;
}
function enterPromptFlow(returnMode=composerPromptView.mode){
    if(!promptInput || !composer || composerPromptView.mode === 'flow') return;
    composerPromptView.returnMode = returnMode === 'expanded' ? 'expanded' : 'manual';
    if(!composerPromptView.restoreFlowScroll) composerPromptView.scrollTop = promptInput.scrollTop;
    composerPromptView.restoreFlowScroll = false;
    composerPromptView.mode = 'flow';
    syncComposerPromptViewControls();
    renderPromptFlowContext(activeComposerNode());
    positionPromptFlowComposer();
    requestAnimationFrame(() => {
        promptInput.scrollTop = composerPromptView.scrollTop;
    });
}
function exitPromptFlow(){
    if(composerPromptView.mode !== 'flow') return false;
    const scrollTop = promptInput.scrollTop;
    composerPromptView.scrollTop = scrollTop;
    composerPromptView.restoreFlowScroll = true;
    composerPromptView.applyingScrollRestore = true;
    const canReturnExpanded = composerPromptView.returnMode === 'expanded'
        && promptNaturalHeight() <= promptExpandedHeightLimit();
    composerPromptView.mode = canReturnExpanded ? 'expanded' : 'manual';
    clearPromptFlowGeometry();
    syncComposerPromptViewControls();
    renderPromptFlowContext(null);
    if(canReturnExpanded){
        const target = Math.min(promptExpandedHeightLimit(), Math.max(composerPromptView.manualHeight, promptNaturalHeight()) + composerPromptView.expandedExtra);
        composerPromptView.expandedHeight = setPromptEditorHeight(target);
    } else {
        setPromptEditorHeight(composerPromptView.manualHeight);
    }
    requestAnimationFrame(() => {
        promptInput.scrollTop = Math.min(scrollTop, Math.max(0, promptInput.scrollHeight - promptInput.clientHeight));
        const node = activeComposerNode();
        if(node) positionComposerForNode(node);
        keepComposerReachableByPanningCanvas();
        requestAnimationFrame(() => { composerPromptView.applyingScrollRestore = false; });
    });
    return true;
}
function collapseExpandedPrompt(){
    if(composerPromptView.mode !== 'expanded') return false;
    composerPromptView.mode = 'manual';
    composerPromptView.expandedExtra = 0;
    setPromptEditorHeight(composerPromptView.manualHeight);
    // 清除节点的展开状态
    const node = activeComposerNode();
    if(node) composerPromptView.expandedByNode.delete(node.id);
    syncComposerPromptViewControls();
    if(node) requestAnimationFrame(() => {
        positionComposerForNode(node);
        keepComposerReachableByPanningCanvas();
    });
    return true;
}
function expandPromptToContent(){
    if(composerPromptView.mode === 'flow') return exitPromptFlow();
    if(composerPromptView.mode === 'expanded') return collapseExpandedPrompt();
    composerPromptView.manualHeight = clampPromptManualHeight(settings.promptH || promptInput.offsetHeight || 84);
    composerPromptView.expandedExtra = 0;
    const natural = promptNaturalHeight();
    const limit = promptExpandedHeightLimit();
    if(natural > limit){
        enterPromptFlow('manual');
        return true;
    }
    composerPromptView.mode = 'expanded';
    composerPromptView.expandedHeight = setPromptEditorHeight(Math.max(composerPromptView.manualHeight, natural));
    // 保存展开状态到节点，取消选择后重新选择同一节点时恢复
    const node = activeComposerNode();
    if(node) composerPromptView.expandedByNode.set(node.id, true);
    syncComposerPromptViewControls();
    if(node) requestAnimationFrame(() => {
        positionComposerForNode(node);
        keepComposerReachableByPanningCanvas();
    });
    return true;
}
function refreshExpandedPromptHeight(){
    if(composerPromptView.mode !== 'expanded') return;
    const natural = promptNaturalHeight();
    const limit = promptExpandedHeightLimit();
    if(natural > limit){
        enterPromptFlow('manual');
        return;
    }
    composerPromptView.expandedHeight = setPromptEditorHeight(
        Math.min(limit, Math.max(composerPromptView.manualHeight, natural) + composerPromptView.expandedExtra)
    );
    const node = activeComposerNode();
    if(node) requestAnimationFrame(() => {
        positionComposerForNode(node);
        keepComposerReachableByPanningCanvas();
    });
}
function bindComposerPromptViewToNode(node){
    const nodeId = String(node?.id || '');
    if(composerPromptView.nodeId === nodeId) return;
    if(composerPromptView.mode === 'flow'){
        composerPromptView.mode = 'manual';
        clearPromptFlowGeometry();
    }
    composerPromptView.nodeId = nodeId;
    // 根据节点的展开状态恢复 mode，取消选择后重新选择同一节点时保持展开
    composerPromptView.mode = node && composerPromptView.expandedByNode.get(nodeId) ? 'expanded' : 'manual';
    composerPromptView.returnMode = 'manual';
    composerPromptView.expandedExtra = 0;
    composerPromptView.restoreFlowScroll = false;
    composerPromptView.applyingScrollRestore = false;
    syncComposerPromptViewControls();
    // 恢复展开状态时，更新提示词高度
    if(composerPromptView.mode === 'expanded'){
        requestAnimationFrame(refreshExpandedPromptHeight);
    }
}
function positionComposerForNode(node){
    if(!node || !composer) return;
    if(composerPromptView.mode === 'flow'){
        positionPromptFlowComposer();
        return;
    }
    const modelRect = nodeRect(node);
    composer.style.removeProperty('width');
    composer.style.removeProperty('max-width');
    const composerWidth = Math.max(1, Number(composer.offsetWidth || modelRect.width || 1));
    // Keep the ordinary Composer in world coordinates so node and editor share
    // one transform. Viewport zoom/pan must never flip or clamp it elsewhere.
    composer.style.left = `${modelRect.x + (modelRect.width - composerWidth) / 2}px`;
    composer.style.top = `${modelRect.y + modelRect.height + 14}px`;
}
function keepComposerReachableByPanningCanvas(){
    if(!shell || !composer?.classList.contains('open') || composerPromptView.mode === 'flow') return false;
    const frame = shell.getBoundingClientRect();
    const rect = composer.getBoundingClientRect();
    const safeTop = frame.top + 16;
    const safeBottom = frame.bottom - 16;
    let contentTop = rect.top;
    let contentBottom = rect.bottom;
    const node = activeComposerNode();
    const nodeElement = node?.id
        ? world?.querySelector(`.image-node[data-id="${CSS.escape(node.id)}"]`)
        : null;
    const nodeRect = nodeElement?.getBoundingClientRect?.();
    if(nodeRect?.height > 0){
        const unionTop = Math.min(contentTop, nodeRect.top);
        const unionBottom = Math.max(contentBottom, nodeRect.bottom);
        if(unionBottom - unionTop <= safeBottom - safeTop){
            contentTop = unionTop;
            contentBottom = unionBottom;
        }
    }
    let shiftY = contentBottom > safeBottom ? safeBottom - contentBottom : 0;
    if(contentTop + shiftY < safeTop) shiftY += safeTop - (contentTop + shiftY);
    if(Math.abs(shiftY) < 0.5) return false;
    viewport.y += shiftY;
    applyViewport();
    return true;
}
let composerUpdateTimer = 0;
let composerUpdateSeq = 0;
function scheduleComposerUpdate(delay=120){
    if(composerUpdateTimer){
        clearTimeout(composerUpdateTimer);
        composerUpdateTimer = 0;
    }
    const seq = ++composerUpdateSeq;
    composerUpdateTimer = setTimeout(() => {
        composerUpdateTimer = 0;
        if(seq !== composerUpdateSeq) return;
        updateComposer();
    }, Math.max(0, Number(delay) || 0));
}
function updateComposer(){
    if(composerUpdateTimer){
        clearTimeout(composerUpdateTimer);
        composerUpdateTimer = 0;
    }
    composerUpdateSeq++;
    const node = selectedNode();
    syncRunButtonState(node);
    if(smartCascadeSilentSelection && !canvasState.activeComposerSubject){
        composer.classList.remove('open');
        canvasState.activeComposerSubject = null;
        canvasState.lastComposerNodeId = '';
        return;
    }
    composer.classList.toggle('open', !!node);
    if(!isSmartRunnableNode(node)){
        bindComposerPromptViewToNode(null);
        savePromptDraftForCurrent();
        composer.classList.remove('open');
        canvasState.activeComposerSubject = null;
        canvasState.lastComposerNodeId = ''; // 取消选择时清空，重新选择节点时 switchedNode=true，loadPromptDraft 恢复提示词
        setPromptInputLocked(false);
        if(!node) setPromptText('');
        return;
    }
    // composer 只绑定节点本身：图片只是素材/结果，不携带提示词或参数状态。
    const subject = node;
    bindComposerPromptViewToNode(node);
    const composerKey = `${node.id}:node`;
    const switchedNode = canvasState.lastComposerNodeId !== composerKey;
    if(switchedNode) savePromptDraftForCurrent();
    canvasState.lastComposerNodeId = composerKey;
    canvasState.activeComposerSubject = subject;
    const hasPromptInput = promptInputNodesFor(node).length > 0;
    if(switchedNode){
        settings = smartSettingsForNode(subject);
        loadPromptDraft(subject);
    }
    setPromptInputLocked(false);
    const storedPromptH = Number(settings.promptH);
    const ph = clampPromptManualHeight(storedPromptH === 124 ? 84 : (storedPromptH || 84));
    if(storedPromptH !== ph) settings.promptH = ph;
    composerPromptView.manualHeight = ph;
    if(composerPromptView.mode === 'manual') setPromptEditorHeight(ph);
    else if(composerPromptView.mode === 'expanded') refreshExpandedPromptHeight();
    renderInputThumbsRow(node);
    renderInputPromptPreview(node);
    if(composerPromptView.mode === 'flow') renderPromptFlowContext(node);
    scheduleDynamicParamsRefresh(140);
    syncRunButtonState(node);
    requestAnimationFrame(() => {
        if(activeComposerNode()?.id === node.id) positionComposerForNode(node);
    });
}
function renderInputPromptPreview(node){
    if(!inputPromptPreview) return;
    const groupText = isSmartGroupNode(node) ? textForNode(node).trim() : '';
    const text = node ? [groupText, inputPromptTextFor(node).trim()].filter(Boolean).join('\n\n') : '';
    inputPromptPreview.classList.toggle('has-text', Boolean(text));
    inputPromptPreview.innerHTML = text
        ? `<div class="input-prompt-preview-head"><div class="input-prompt-preview-label">${escapeHtml(tr('smart.inputUpstream'))}</div><button class="long-content-trigger input-prompt-preview-open" type="button" data-long-content-open="input-prompt" title="${escapeAttr(tr('smart.connectionPromptFull'))}" aria-label="${escapeAttr(tr('smart.connectionPromptFull'))}"><i data-lucide="book-open-text"></i><span>${escapeHtml(tr('smart.readFullContent'))}</span></button></div><div class="input-prompt-preview-text">${escapeHtml(text)}</div>`
        : '';
    const openButton = inputPromptPreview.querySelector('[data-long-content-open="input-prompt"]');
    if(openButton){
        const activate = event => {
            event.preventDefault();
            event.stopPropagation();
            openLongContentReader({
                title:tr('smart.connectionPromptFull'),
                text,
                trigger:openButton,
                triggerIdentity:{kind:'input-prompt', nodeId:String(node?.id || '')}
            });
        };
        openButton.addEventListener('mousedown', event => event.stopPropagation());
        openButton.onclick = activate;
        openButton.onkeydown = event => {
            if(event.key === 'Enter' || event.key === ' ') activate(event);
        };
    }
    refreshIcons();
}
function renderInputThumbsRow(node){
    if(!inputThumbsRow) return;
    syncJimengModelPillForRefs();
    syncJimengVideoModelPillForRefs();
    const dedup = node ? visibleReferenceImagesFor(node) : [];
    const manualRefKeys = new Set(manualReferenceImagesFor(node).map(img => inputRefKey(img)));
    const addActive = mentionInsertMode === 'manual-ref';
    // 仅当参考图集合/状态真正变化时才重建缩略图 DOM。否则每敲一个字都重建并重新解码所有图片，
    // 参考图多时会让输入框打字明显卡顿。
    const thumbsSignature = JSON.stringify({
        node: node?.id || '',
        items: dedup.map(img => `${inputRefKey(img)}@${img.url || ''}`),
        manual: [...manualRefKeys],
        add: addActive,
        mode: node ? smartImageMode(node) : ''
    });
    if(inputThumbsRow.dataset.thumbsSig === thumbsSignature) return;
    inputThumbsRow.dataset.thumbsSig = thumbsSignature;
    inputThumbsRow.classList.toggle('has-items', Boolean(node));
    if(!node){ inputThumbsRow.innerHTML = ''; return; }
    const addButton = `<button class="input-thumb-add ${addActive ? 'active' : ''}" type="button" data-input-add-reference title="${escapeHtml(addActive ? tr('smart.collapseRefs') : tr('smart.addRefImage'))}" aria-label="${escapeHtml(addActive ? tr('smart.collapseRefs') : tr('smart.addRefImage'))}"><i data-lucide="image-plus"></i></button>`;
    if(!dedup.length){
        inputThumbsRow.innerHTML = `<div class="input-thumb-list empty"></div><div class="input-thumb-actions">${addButton}</div>`;
        bindInputThumbReferenceActions();
        refreshIcons();
        return;
    }
    const mediaCounters = {image:0, video:0, audio:0, text:0, file:0};
    const thumbsHtml = dedup.map((img, i) => {
        const isVid = isVideoMediaItem(img);
        const kind = mediaKindForItem(img);
        const isSelf = node ? isSelfReferenceForNode(node, img) : false;
        const title = isSelf
            ? tr('smart.inputSelf')
            : (smartImageMode(node) === 'workflow' ? tr('smart.inputUpstreamWorkflow') : tr('smart.inputUpstream'));
        const inner = kind === 'audio'
            ? `<div class="input-thumb-audio"><i data-lucide="file-audio"></i></div>`
            : isVid
            ? smartVideoPreviewHtml(img, 256, 'draggable="false" alt=""')
            : smartPreviewImgHtml(img, 256, 'draggable="false"');
        const count = (mediaCounters[kind] = (mediaCounters[kind] || 0) + 1);
        const label = kind === 'audio' ? `音频${count}` : kind === 'video' ? `视频${count}` : `图${count}`;
        const referenceMarker = kind === 'image' ? count : 0;
        const markerHint = referenceMarker
            ? (window.StudioI18n?.lang?.() === 'en' ? `Click to insert @${referenceMarker}` : `单击插入 @${referenceMarker}`)
            : '';
        const sourceUrl = img.originalLocalUrl || img.url || '';
        const key = inputRefKey(img);
        const isUpstream = !manualRefKeys.has(key);
        const removeBtn = `<button class="input-thumb-remove" type="button" data-input-remove-reference="${escapeHtml(key)}" title="${escapeHtml(isUpstream ? tr('smart.disconnectRef') : tr('smart.removeRef'))}" aria-label="${escapeHtml(isUpstream ? tr('smart.disconnectRef') : tr('smart.removeRef'))}">×</button>`;
        const tooltip = [img.name || tr('smart.inputNum').replace('{n}', String(i + 1)), title, markerHint].filter(Boolean).join(' · ');
        return `<div class="input-thumb ${isSelf ? 'input-self' : ''} ${manualRefKeys.has(key) ? 'input-manual-ref' : ''}" draggable="false" data-thumb-index="${i}" data-reference-marker="${referenceMarker || ''}" data-node-id="${escapeHtml(img.nodeId || '')}" data-image-index="${img.imageIndex ?? ''}" data-url="${escapeHtml(img.url || '')}" data-source-url="${escapeHtml(sourceUrl)}" title="${escapeHtml(tooltip)}">${inner}<span class="input-thumb-label">${escapeHtml(label)}</span>${removeBtn}</div>`;
    }).join('');
    inputThumbsRow.innerHTML = `<div class="input-thumb-list">${thumbsHtml}${dedup.length > 1 ? `<span class="input-thumb-count">${escapeHtml(tr('smart.inputCount').replace('{n}', String(dedup.length)))}</span>` : ''}</div><div class="input-thumb-actions">${addButton}</div>`;
    bindSmartPreviewImageFallbacks(inputThumbsRow);
    bindInputThumbsDrag(node, dedup, manualRefKeys);
    bindInputThumbReferenceActions();
    refreshIcons();
}
function bindInputThumbReferenceActions(){
    inputThumbsRow?.querySelectorAll('[data-input-add-reference]').forEach(btn => {
        btn.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            toggleAssetMentionPickerFromThumbs();
        });
    });
    inputThumbsRow?.querySelectorAll('[data-input-remove-reference]').forEach(btn => {
        btn.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            const node = selectedNode();
            if(!node) return;
            const key = btn.dataset.inputRemoveReference || '';
            const thumbs = Array.from(inputThumbsRow.querySelectorAll('.input-thumb'));
            const thumb = thumbs.find(el => el.querySelector(`[data-input-remove-reference="${key}"]`));
            if(!thumb) return;
            const nodeId = thumb.dataset.nodeId || '';
            const isManual = thumb.classList.contains('input-manual-ref');
            if(isManual){
                removeManualReferenceFromSelectedNode(key);
            } else if(nodeId && canvas){
                // 断开上游节点的连线
                canvas.connections = (canvas.connections || []).filter(c => !(c.from === nodeId && c.to === node.id && c.kind === 'input'));
                render();
                scheduleSave();
            }
        });
    });
}
function bindInputThumbsDrag(node, items, manualRefKeys=new Set()){
    if(!inputThumbsRow) return;
    let thumbDragIndex = -1;
    inputThumbsRow.querySelectorAll('.input-thumb').forEach(el => {
        let didDrag = false;
        const index = Number(el.dataset.thumbIndex || -1);
        const item = items[index];
        const key = inputRefKey(item);
        const canReorderManual = items.length > 1 && manualRefKeys.has(key);
        const canReorderSource = items.length > 1 && Boolean(item?.nodeId);
        el.draggable = canReorderManual || canReorderSource;
        el.addEventListener('click', e => {
            e.preventDefault();
            e.stopPropagation();
            if(didDrag){ didDrag = false; return; }
            if(e.button !== 0 || e.target.closest?.('[data-input-remove-reference]')) return;
            const referenceNumber = Number(el.dataset.referenceMarker || 0);
            if(referenceNumber > 0) insertInputReferenceMarker(referenceNumber);
        });
        if(!el.draggable) return;
        el.addEventListener('dragstart', e => {
            e.stopPropagation();
            didDrag = true;
            thumbDragIndex = index;
            el.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            if(canReorderManual) e.dataTransfer.setData('application/x-smart-manual-ref', key);
            else e.dataTransfer.setData('application/x-smart-input-thumb', String(index));
        });
        el.addEventListener('dragend', e => {
            e.stopPropagation();
            thumbDragIndex = -1;
            clearInputThumbDropMarkers();
            el.classList.remove('dragging');
            setTimeout(() => { didDrag = false; }, 0);
        });
        el.addEventListener('dragover', e => {
            const manualFromKey = e.dataTransfer.getData('application/x-smart-manual-ref');
            if(manualFromKey){
                if(!manualRefKeys.has(key) || manualFromKey === key) return;
                e.preventDefault();
                e.stopPropagation();
                e.dataTransfer.dropEffect = 'move';
                clearInputThumbDropMarkers();
                const placement = inputThumbDropPlacement(el, e);
                el.dataset.dropPlacement = placement;
                el.classList.add(placement === 'before' ? 'drop-before' : 'drop-after');
                return;
            }
            const rawFrom = e.dataTransfer.getData('application/x-smart-input-thumb');
            const from = rawFrom === '' ? thumbDragIndex : Number(rawFrom);
            if(!Number.isFinite(from) || from < 0 || from === index || !items[index]?.nodeId) return;
            e.preventDefault();
            e.stopPropagation();
            e.dataTransfer.dropEffect = 'move';
            clearInputThumbDropMarkers();
            const placement = inputThumbDropPlacement(el, e);
            el.dataset.dropPlacement = placement;
            el.classList.add(placement === 'before' ? 'drop-before' : 'drop-after');
        });
        el.addEventListener('dragleave', e => {
            if(el.contains(e.relatedTarget)) return;
            delete el.dataset.dropPlacement;
            el.classList.remove('drop-before', 'drop-after');
        });
        el.addEventListener('drop', e => {
            const manualFromKey = e.dataTransfer.getData('application/x-smart-manual-ref');
            if(manualFromKey){
                if(!manualRefKeys.has(key) || manualFromKey === key) return;
                e.preventDefault();
                e.stopPropagation();
                const placement = inputThumbDropPlacement(el, e);
                clearInputThumbDropMarkers();
                reorderManualInputRefs(node, manualFromKey, key, placement);
                return;
            }
            const rawFrom = e.dataTransfer.getData('application/x-smart-input-thumb');
            const from = rawFrom === '' ? thumbDragIndex : Number(rawFrom);
            if(!Number.isFinite(from) || from < 0 || from === index || !items[index]?.nodeId) return;
            e.preventDefault();
            e.stopPropagation();
            const placement = inputThumbDropPlacement(el, e);
            clearInputThumbDropMarkers();
            reorderInputThumb(node, items, from, index, placement);
        });
    });
}
function reorderManualInputRefs(currentNode, fromKey, targetKey, placement='before'){
    if(!currentNode || !fromKey || !targetKey || fromKey === targetKey) return false;
    const refs = Array.isArray(currentNode.manualInputRefs) ? currentNode.manualInputRefs.slice() : [];
    const from = refs.findIndex(item => inputRefKey(item) === fromKey);
    const target = refs.findIndex(item => inputRefKey(item) === targetKey);
    if(from < 0 || target < 0 || from === target) return false;
    pushUndo();
    const [moved] = refs.splice(from, 1);
    let insertAt = refs.findIndex(item => inputRefKey(item) === targetKey);
    if(insertAt < 0) return false;
    if(placement === 'after') insertAt += 1;
    refs.splice(insertAt, 0, moved);
    currentNode.manualInputRefs = refs;
    if(inputThumbsRow) delete inputThumbsRow.dataset.thumbsSig;
    renderInputThumbsRow(currentNode);
    scheduleSave();
    return true;
}
function inputThumbDropPlacement(el, event){
    const rect = el.getBoundingClientRect();
    return event.clientX < rect.left + rect.width / 2 ? 'before' : 'after';
}
function clearInputThumbDropMarkers(){
    inputThumbsRow?.querySelectorAll('.input-thumb.drop-before,.input-thumb.drop-after,.input-thumb.dragging')
        .forEach(el => {
            delete el.dataset.dropPlacement;
            el.classList.remove('drop-before', 'drop-after', 'dragging');
        });
}
function movedBeforeAfterIds(ids, movedId, targetId, placement='before'){
    const list = (ids || []).filter(Boolean);
    const from = list.indexOf(movedId);
    const target = list.indexOf(targetId);
    if(from < 0 || target < 0 || movedId === targetId) return list;
    const [moved] = list.splice(from, 1);
    let insertAt = list.indexOf(targetId);
    if(insertAt < 0) return ids || [];
    if(placement === 'after') insertAt += 1;
    list.splice(insertAt, 0, moved);
    return list;
}
function sameOrderedIds(a, b){
    if((a || []).length !== (b || []).length) return false;
    return (a || []).every((id, index) => id === b[index]);
}
function reorderInputSourceNodes(currentNode, movedId, targetId, placement='before'){
    if(!currentNode || !movedId || !targetId || movedId === targetId) return false;
    const sourceNodes = smartImageUsesWorkflowInput(currentNode, smartLoopContext)
        ? workflowInputNodesFor(currentNode)
        : inputNodesFor(currentNode);
    const sourceIds = sourceNodes.map(n => n.id).filter(Boolean);
    if(!sourceIds.includes(movedId) || !sourceIds.includes(targetId)) return false;
    const nextIds = movedBeforeAfterIds(sourceIds, movedId, targetId, placement);
    if(sameOrderedIds(sourceIds, nextIds)) return false;
    const oldExplicitIds = Array.isArray(currentNode.inputNodeIds) ? currentNode.inputNodeIds.filter(Boolean) : [];
    currentNode.inputNodeIds = [
        ...nextIds.filter(id => oldExplicitIds.includes(id)),
        ...oldExplicitIds.filter(id => !nextIds.includes(id))
    ];
    if(canvas && Array.isArray(canvas.connections)){
        const order = new Map(nextIds.map((id, index) => [id, index]));
        const relevantSlots = new Set();
        const relevant = [];
        canvas.connections.forEach((conn, index) => {
            const kind = conn?.kind || 'flow';
            if(conn?.to === currentNode.id && ['input', 'flow'].includes(kind) && order.has(conn.from)){
                relevantSlots.add(index);
                relevant.push({conn, index});
            }
        });
        if(relevant.length){
            relevant.sort((a, b) => (order.get(a.conn.from) - order.get(b.conn.from)) || (a.index - b.index));
            let cursor = 0;
            canvas.connections = canvas.connections.map((conn, index) => relevantSlots.has(index) ? relevant[cursor++].conn : conn);
        }
    }
    return true;
}
function reorderInputThumb(currentNode, items, from, to, placement='before'){
    // items are already sourced from inputImagesFor → multiple source canvasState.nodes possible.
    // Reorder within a source group's images first; separate input canvasState.nodes use the
    // current node's input order, with a visual-position swap as a final fallback.
    if(from < 0 || to < 0 || from >= items.length || to >= items.length) return;
    // 强制让缩略图条在重排后重建（绕过 renderInputThumbsRow 的“无变化跳过”缓存），否则顺序看起来没变。
    if(inputThumbsRow) delete inputThumbsRow.dataset.thumbsSig;
    const fromImg = items[from];
    const toImg = items[to];
    if(!fromImg || !toImg) return;
    if(fromImg.nodeId === toImg.nodeId){
        const src = canvasState.nodes.find(n => n.id === fromImg.nodeId);
        if(!src) return;
        pushUndo();
        const fi = Number(fromImg.imageIndex);
        const ti = Number(toImg.imageIndex);
        if(Number.isFinite(fi) && Number.isFinite(ti) && (src.images || [])[fi]){
            const arr = src.images;
            let insertAt = Math.max(0, Math.min(arr.length, ti + (placement === 'after' ? 1 : 0)));
            const item = arr.splice(fi, 1)[0];
            if(fi < insertAt) insertAt -= 1;
            arr.splice(Math.max(0, Math.min(arr.length, insertAt)), 0, item);
        }
        render();
        scheduleSave();
        return;
    }
    // 分组：缩略图来自各成员节点，跨成员拖动应改变 group.items 的成员顺序（= 图1234 的编号顺序），
    // 而不是去交换节点的画布位置（那只改了图层上下层级）。
    if(isSmartGroupNode(currentNode) && Array.isArray(currentNode.items)
        && fromImg.nodeId !== toImg.nodeId
        && currentNode.items.includes(fromImg.nodeId) && currentNode.items.includes(toImg.nodeId)){
        const next = movedBeforeAfterIds(currentNode.items.slice(), fromImg.nodeId, toImg.nodeId, placement);
        if(!sameOrderedIds(currentNode.items, next)){
            pushUndo();
            currentNode.items = next;
            if(inputThumbsRow) delete inputThumbsRow.dataset.thumbsSig;
            render();
            scheduleSave();
        }
        return;
    }
    const canReorderSources = currentNode && fromImg.nodeId && toImg.nodeId;
    const a = canvasState.nodes.find(n => n.id === fromImg.nodeId);
    const b = canvasState.nodes.find(n => n.id === toImg.nodeId);
    if(!canReorderSources || !a || !b) return;
    pushUndo();
    if(reorderInputSourceNodes(currentNode, fromImg.nodeId, toImg.nodeId, placement)){
        render();
        scheduleSave();
        return;
    }
    // Cross-node fallback: swap X positions of source canvasState.nodes
    const ax = a.x, ay = a.y;
    a.x = b.x; a.y = b.y;
    b.x = ax; b.y = ay;
    render();
    scheduleSave();
}
function isSupportedUploadFile(file){
    const type = String(file?.type || '').toLowerCase();
    const name = String(file?.name || '').toLowerCase();
    return type.startsWith('image/') || type.startsWith('video/') || type.startsWith('audio/')
        || /\.(png|jpe?g|webp|gif|mp4|webm|mov|m4v|mp3|wav|m4a|aac|ogg|flac)(\?|$)/.test(name);
}
function dataTransferItemEntry(item){
    try { return item?.webkitGetAsEntry?.() || null; } catch { return null; }
}
async function filesFromEntry(entry){
    if(!entry) return [];
    if(entry.isFile){
        return new Promise(resolve => entry.file(file => resolve(file ? [file] : []), () => resolve([])));
    }
    if(!entry.isDirectory) return [];
    const reader = entry.createReader();
    const children = [];
    while(true){
        const batch = await new Promise(resolve => reader.readEntries(resolve, () => resolve([])));
        if(!batch.length) break;
        children.push(...batch);
    }
    const nested = await Promise.all(children.map(filesFromEntry));
    return nested.flat();
}
async function uploadFilesFromDataTransfer(dataTransfer){
    const items = [...(dataTransfer?.items || [])];
    const entries = items.map(dataTransferItemEntry).filter(Boolean);
    const raw = entries.length
        ? (await Promise.all(entries.map(filesFromEntry))).flat()
        : [...(dataTransfer?.files || [])];
    return raw.filter(isSupportedUploadFile);
}
function uploadTitleForItems(items, fallback='Upload'){
    const list = [...(items || [])];
    if(!list.length) return fallback;
    const kinds = new Set(list.map(item => item instanceof File ? mediaKindForFile(item) : mediaKindForItem(item)));
    if(kinds.size > 1) return list.length > 1 ? 'Media' : fallback;
    if(kinds.has('video')) return list.length > 1 ? 'Videos' : 'Video';
    if(kinds.has('audio')) return 'Audio';
    return list.length > 1 ? 'Group' : 'Image';
}
const SMART_IMAGE_DROP_EXT_RE = /\.(png|jpe?g|webp|gif)$/i;
const SMART_IMAGE_DROP_TEXT_TYPES = [
    'text/uri-list',
    'text/plain',
    'text/html',
    'DownloadURL',
    'text/x-moz-url',
    'text/x-file-url',
    'public.file-url',
    'public.url',
    'UniformResourceLocator',
    'FileName',
    'FileNameW'
];
const SMART_IMAGE_DROP_TYPE_HINT_RE = /^(?:files?|image\/.+|text\/(?:uri-list|html|plain|x-moz-url|x-file-url)|downloadurl|public\.(?:file-url|url)|uniformresourcelocator|filenamew?)$|application\/x-qt-(?:windows-mime|image)|application\/x-moz-file|com\.eagle/i;
function smartImageFilesFromDataTransfer(dataTransfer){
    return [...(dataTransfer?.files || [])].filter(isSupportedUploadFile);
}
async function smartResponseErrorMessage(response, fallback=tr('chat.requestFailed')){
    try {
        const data = await response.clone().json();
        const detail = data.detail ?? data.error ?? data.message;
        if(typeof detail === 'string') return detail || fallback;
        if(Array.isArray(detail)) return detail.map(item => item?.msg || item?.message || String(item)).join('\n') || fallback;
    } catch(_) {}
    try {
        const text = await response.text();
        if(text) return text;
    } catch(_) {}
    return fallback;
}
function smartDropDataTypes(dataTransfer){
    return [...(dataTransfer?.types || [])].map(type => String(type || ''));
}
function readSmartDropData(dataTransfer, type){
    try { return dataTransfer?.getData?.(type) || ''; } catch(_) { return ''; }
}
function decodeSmartDropText(value){
    const text = String(value || '').trim();
    if(!text) return '';
    try { return decodeURIComponent(text); } catch(_) { return text; }
}
function smartDropTextFragments(value){
    const text = String(value || '').trim();
    if(!text) return [];
    const fragments = [];
    if(/<img|<a\s/i.test(text)){
        const doc = new DOMParser().parseFromString(text, 'text/html');
        doc.querySelectorAll('img[src],a[href]').forEach(el => fragments.push(el.getAttribute('src') || el.getAttribute('href') || ''));
    }
    text.split(/\r?\n/).forEach(line => {
        const item = line.trim();
        if(item) fragments.push(item);
    });
    const downloadUrl = text.match(/^image\/[^\s:]+:(.+)$/i);
    if(downloadUrl) fragments.push(downloadUrl[1]);
    return fragments;
}
function uniqueSmartDropValues(values){
    const seen = new Set();
    return values.filter(value => {
        const key = String(value || '').trim();
        if(!key || seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}
function smartDropTextCandidates(dataTransfer){
    if(!dataTransfer) return [];
    const types = uniqueSmartDropValues([...SMART_IMAGE_DROP_TEXT_TYPES, ...smartDropDataTypes(dataTransfer)]);
    const values = types.map(type => readSmartDropData(dataTransfer, type)).filter(Boolean);
    return uniqueSmartDropValues(values.flatMap(smartDropTextFragments).map(decodeSmartDropText))
        .filter(s => s && !s.startsWith('#'));
}
function isRemoteSmartImageDropValue(value){
    const text = String(value || '').trim();
    return /^https?:\/\/.+/i.test(text) || /^data:image\//i.test(text) || /^blob:/i.test(text);
}
function isLocalSmartImageDropValue(value){
    const text = String(value || '').trim();
    if(!text) return false;
    let path = text;
    if(/^file:/i.test(path)){
        try {
            const url = new URL(path);
            if(url.protocol !== 'file:') return false;
            path = decodeURIComponent(url.pathname || path);
        } catch(_) {
            return false;
        }
    }
    if(/^\/[a-zA-Z]:[\\/]/.test(path)) path = path.slice(1);
    const clean = path.split(/[?#]/, 1)[0];
    const isWindowsPath = /^[a-zA-Z]:[\\/]/.test(clean);
    const isPosixPath = clean.startsWith('/');
    return (isWindowsPath || isPosixPath) && SMART_IMAGE_DROP_EXT_RE.test(clean);
}
function smartLocalImagePathsFromDataTransfer(dataTransfer){
    return uniqueSmartDropValues(smartDropTextCandidates(dataTransfer).filter(isLocalSmartImageDropValue));
}
function smartImageNameFromUrl(url){
    try {
        const clean = String(url || '').split('?', 1)[0].split('#', 1)[0];
        return decodeURIComponent(clean.split('/').pop() || 'image');
    } catch(_) {
        return 'image';
    }
}
function smartImageDropPayload(dataTransfer){
    const files = smartImageFilesFromDataTransfer(dataTransfer);
    if(files.length) return {type:'files', files};
    const localPaths = smartLocalImagePathsFromDataTransfer(dataTransfer);
    if(localPaths.length) return {type:'localPaths', localPaths};
    const url = smartDropTextCandidates(dataTransfer).find(isRemoteSmartImageDropValue) || '';
    if(url) return {type:'url', url};
    return {type:'none'};
}
async function resolveSmartImageDropPayload(dataTransfer){
    const payload = smartImageDropPayload(dataTransfer);
    if(payload.type !== 'none') return payload;
    const files = await uploadFilesFromDataTransfer(dataTransfer);
    return files.length ? {type:'files', files} : payload;
}
function hasSmartImageDropData(dataTransfer){
    if(!dataTransfer) return false;
    if(smartImageFilesFromDataTransfer(dataTransfer).length) return true;
    const types = smartDropDataTypes(dataTransfer);
    if(types.some(type => SMART_IMAGE_DROP_TYPE_HINT_RE.test(type.toLowerCase()))) return true;
    return smartImageDropPayload(dataTransfer).type !== 'none';
}
function hasSmartAssetDrag(dataTransfer){
    return smartDropDataTypes(dataTransfer).includes('application/x-smart-asset');
}
function hasSmartInputThumbDrag(dataTransfer){
    return smartDropDataTypes(dataTransfer).includes('application/x-smart-input-thumb');
}
function setSmartDropCopyEffect(e, includeAsset=false){
    e.preventDefault();
    if(hasSmartInputThumbDrag(e.dataTransfer)) return;
    if(hasSmartImageDropData(e.dataTransfer) || (includeAsset && hasSmartAssetDrag(e.dataTransfer))){
        e.dataTransfer.dropEffect = 'copy';
    }
}
async function uploadFiles(files){
    const supported = [...(files || [])].filter(isSupportedUploadFile).slice(0, SMART_UPLOAD_MAX);
    if(!supported.length) return [];
    const form = new FormData();
    supported.forEach(file => form.append('files', file, file.name || 'media'));
    const data = await fetch('/api/ai/upload', {method:'POST', body:form}).then(async r => {
        if(!r.ok) throw new Error((await r.text()) || tr('smart.toastUploadFail'));
        return r.json();
    });
    return (data.files || []).map((file, index) => ({
        ...file,
        kind:file.kind || mediaKindForFile(supported[index])
    }));
}
function appendImagesToSmartNode(uploaded, targetId='', opts={}){
    const images = [...(uploaded || [])].filter(file => file?.url);
    if(!images.length) return null;
    const targetGroup = canvasState.nodes.find(n => n.id === targetId && isSmartGroupNode(n));
    let node = targetGroup ? null : (canvasState.nodes.find(n => n.id === targetId) || selectedNode());
    if(node && !isSmartImageNode(node)) node = null;
    if(opts.forceNew) node = null;
    if(!node){
        const groupRect = targetGroup ? nodeRect(targetGroup) : null;
        const center = opts.point || (groupRect ? {x:groupRect.x + groupRect.width / 2, y:groupRect.y + groupRect.height / 2} : viewportCenter());
        undoSuppressed = true;
        node = createImageNodeAt(center, []);
        undoSuppressed = false;
    }
    const previousCount = (node.images || []).length;
    node.images = [...(node.images || []), ...images.map(file => ({...file, kind:file.kind || mediaKindForItem(file)}))];
    if(node.images.length > 1){
        node.title = uploadTitleForItems(node.images, 'Group');
        if(previousCount <= 1 && (!Number.isFinite(Number(node.scale)) || Number(node.scale) === MEDIA_NODE_DEFAULT_SCALE || Number(node.scale) === MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE)){
            node.scale = MEDIA_GROUP_DEFAULT_SCALE;
        }
        delete node.w;
        delete node.h;
    }
    if(node.images.length === 1){ node.title = uploadTitleForItems(node.images, node.title || 'Image'); delete node.w; delete node.h; }
    if(targetGroup) addNodeToSmartGroup(targetGroup, node);
    canvasState.selectedId = node.id;
    render();
    scheduleSave();
    return node;
}
async function handleFiles(files, targetId='', opts={}){
    try {
        const fileList = [...(files || [])].filter(isSupportedUploadFile).slice(0, SMART_UPLOAD_MAX);
        if(!fileList.length) return;
        const uploaded = await uploadFiles(fileList);
        if(!uploaded.length) return;
        if(!opts.skipUndo) pushUndo();
        appendImagesToSmartNode(uploaded.map((file, index) => ({...file, kind:file.kind || mediaKindForFile(fileList[index])})), targetId, opts);
    } catch(e) { toast(e.message || tr('smart.toastUploadFail')); }
}
async function importSmartLocalImages(paths){
    if(!paths?.length) return [];
    const response = await fetch('/api/ai/import-local-image', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({paths:(paths || []).slice(0, SMART_UPLOAD_MAX)})
    });
    if(!response.ok) throw new Error(await smartResponseErrorMessage(response, tr('smart.toastUploadFail')));
    const data = await response.json();
    return data.files || [];
}
async function handleSmartImageDropPayload(payload, targetId='', opts={}){
    try {
        if(payload.type === 'files') await handleFiles(payload.files, targetId, opts);
        else if(payload.type === 'localPaths') {
            if(!opts.skipUndo) pushUndo();
            appendImagesToSmartNode(await importSmartLocalImages(payload.localPaths), targetId, opts);
        } else if(payload.type === 'url') {
            if(!opts.skipUndo) pushUndo();
            appendImagesToSmartNode([{url:payload.url, name:smartImageNameFromUrl(payload.url), kind:'image'}], targetId, opts);
        }
    } catch(e) {
        toast(e.message || tr('smart.toastUploadFail'));
    }
}
function sizeForRun(sourceSettings=settings){
    const fallbackResolution = sourceSettings.engine === 'api' && isGptImageAutoSizeModel(sourceSettings.model)
        ? defaultSmartApiResolution(sourceSettings.model)
        : '1k';
    return apiImageSize(sourceSettings.ratio || 'square', sourceSettings.resolution || fallbackResolution, sourceSettings.customRatio || '', sourceSettings.customSize || '') || '1024x1024';
}
async function flushProjectCanvasBeforeGeneration(){
    if(canvas?.format !== 'firefly-canvas-v1') return true;
    // A user action can arrive while the request is in flight. Retry a small
    // number of times so those late durable mutations are not left behind.
    const saved = await saveProjectCanvasUntilSettled();
    if(saved !== true) setProjectSaveState('error', tr('smart.projectSaveFailed'));
    return saved;
}
function flushImportantProjectMutation(){
    if(canvas?.format !== 'firefly-canvas-v1') return;
    void flushProjectCanvasBeforeGeneration().then(saved => {
        if(saved !== true) setProjectSaveState('error', tr('smart.projectSaveFailed'));
    }).catch(() => {
        setProjectSaveState('error', tr('smart.projectSaveFailed'));
    });
}
async function acceptProjectGenerationRegistration(taskResult){
    const revision = Number(taskResult?.projectRevision || 0);
    if(canvas?.format !== 'firefly-canvas-v1' || !revision) return false;
    canvas.revision = Math.max(Number(canvas.revision || 0), revision);
    const registeredNode = taskResult.registeredNode;
    if(!registeredNode?.id || !persistedCoreSnapshot?.nodes) return false;
    const currentNode = canvasState.nodes.find(item => item.id === registeredNode.id);
    if(currentNode){
        const localTasks = smartPendingTasks(currentNode);
        const currentTaskIds = new Set((taskResult.taskIds || []).filter(Boolean));
        const pendingHistoryId = String(taskResult.pendingHistoryId || '');
        const preserved = {
            pendingHistoryId:currentNode._pendingHistoryId,
            generationBatchApplied:currentNode._generationBatchApplied,
            runStartedAt:currentNode.runStartedAt
        };
        Object.assign(currentNode, hydrateProjectMediaReferences(registeredNode));
        const mergedTasks = smartPendingTasks(registeredNode).map(task => {
            const local = localTasks.find(item => item.taskId && item.taskId === task.taskId)
                || (currentTaskIds.has(task.taskId) ? localTasks.find(item => !item.taskId && String(item.pendingHistoryId || '') === pendingHistoryId) : null);
            return {
                ...task,
                ...(local || {}),
                taskId:task.taskId,
                pendingHistoryId:String(local?.pendingHistoryId || (currentTaskIds.has(task.taskId) ? pendingHistoryId : '') || '')
            };
        });
        currentNode.pendingTasks = mergedTasks;
        currentNode.pending = Math.max(mergedTasks.length, mergedTasks.reduce((total, task) => total + Math.max(1, Number(task.count || 1)), 0));
        if(preserved.pendingHistoryId) currentNode._pendingHistoryId = preserved.pendingHistoryId;
        if(preserved.generationBatchApplied !== undefined) currentNode._generationBatchApplied = preserved.generationBatchApplied;
        if(preserved.runStartedAt) currentNode.runStartedAt = preserved.runStartedAt;
    }
    persistedCoreSnapshot.nodes[String(registeredNode.id)] = JSON.stringify(registeredNode);
    setProjectSaveState('saved', tr('smart.projectSaved'));
    return true;
}
async function pollGenerationTaskResultForNode(node, taskResult, taskKind='image'){
    const taskIds = Array.isArray(taskResult?.taskIds) ? taskResult.taskIds.filter(Boolean) : [];
    if(!taskIds.length) return [];
    const projectRegistered = canvas?.format === 'firefly-canvas-v1' && Number(taskResult?.projectRevision || 0) > 0;
    if(projectRegistered){
        const registeredTasks = taskIds.map(taskId => ({
            taskId,
            kind:taskKind,
            count:taskResult.count || 1,
            providerId:taskResult.providerId,
            providerName:taskResult?.route?.provider_name || '',
            model:taskResult.model,
            prompt:node.runPrompt || node.promptDraftText || '',
            createdAt:Date.now(),
            idempotencyKey:taskResult.idempotencyKey || ''
        }));
        node.pendingTasks = mergeSmartPendingTaskLists(smartPendingTasks(node), registeredTasks);
        ensureSmartPendingHistoryLogs(node, registeredTasks, {
            kind:taskKind,
            providerId:taskResult?.providerId || '',
            providerName:taskResult?.route?.provider_name || '',
            model:taskResult?.model || '',
            prompt:node.runPrompt || node.promptDraftText || ''
        });
        node.pending = Math.max(node.pendingTasks.length, node.pendingTasks.reduce((total, task) => total + Math.max(1, Number(task.count || 1)), 0));
        node.running = false;
        render();
    }
    await acceptProjectGenerationRegistration(taskResult);
    try {
        return await Promise.all(taskIds.map(taskId => pollSmartCanvasTask(taskId)));
    } finally {
        if(projectRegistered){
            const completed = new Set(taskIds);
            const currentNode = canvasState.nodes.find(item => String(item.id || '') === String(node.id || '')) || node;
            currentNode.pendingTasks = smartPendingTasks(currentNode).filter(task => !completed.has(task.taskId));
            currentNode.pending = currentNode.pendingTasks.reduce((total, task) => total + Math.max(1, Number(task.count || 1)), 0);
            if(!currentNode.pendingTasks.length) delete currentNode.pendingTasks;
            currentNode.running = false;
            render();
        }
    }
}
function aspectRatioForRun(sourceSettings=settings){
    const ratio = sourceSettings.ratio || 'square';
    const presets = {
        square:'1:1', portrait:'2:3', landscape:'3:2', portrait43:'3:4',
        landscape43:'4:3', story:'9:16', wide:'16:9', ultrawide:'21:9', ultratall:'9:21'
    };
    if(ratio === 'custom' || ratio === 'source'){
        const explicit = String(sourceSettings.customRatio || '').trim();
        if(explicit) return explicit;
        const parsed = parseSizePair(sizeForRun(sourceSettings));
        if(parsed) return reducedAspectRatio(parsed.width, parsed.height);
        return '';
    }
    return presets[ratio] || '1:1';
}
function pendingBoxSize(count, options={}){
    const c = Math.max(1, Number(count) || 1);
    const node = options.node || options.sourceNode;
    const aspect = resolveImageAspect(node);
    
    // 单图：原比例布局
    if(c <= 1){
        const layout = getSingleImageLayoutSize(aspect);
        return {w:layout.boxW, h:layout.boxH};
    }
    
    // 批量：固定 4:3 缩略图网格（与生图历史一致）
    // 独立生成节点没有 summary 行，box = 网格 + padding（与 imageLayout 三阶段共用同一常量）
    const grid = getBatchThumbGridSize(c);
    return {w:grid.gridW + BATCH_NODE_PAD_H, h:grid.gridH + BATCH_NODE_PAD_V + BATCH_STATUSBAR_H};
}
function mentionTokenHtml(img){
    if(!img?.url) return '';
    const kind = mediaKindForItem(img);
    const name = img.alias || img.name || (kind === 'audio' ? '音频' : kind === 'video' ? '视频' : '图片');
    const media = mentionTokenMediaHtml(img, kind);
    const assetUris = escapeHtml(JSON.stringify(img.asset_uris || {}) || '{}');
    return `<span class="mention-image-token" contenteditable="false" data-url="${escapeHtml(img.url)}" data-kind="${escapeHtml(kind)}" data-name="${escapeHtml(name)}" data-node-id="${escapeHtml(img.nodeId || '')}" data-image-index="${escapeHtml(img.imageIndex ?? '')}" data-asset-uris="${assetUris}">${media}<span>${escapeHtml(name)}</span></span>`;
}
function mentionTokenMediaHtml(img, kind=mediaKindForItem(img)){
    if(kind === 'audio'){
        return `<div class="mention-audio-thumb"><i data-lucide="file-audio"></i></div>`;
    }
    if(kind === 'video'){
        return smartVideoPreviewHtml(img, 256, 'alt=""');
    }
    return smartPreviewImgHtml(img, 256, 'alt=""');
}
function mentionOptionMediaHtml(img){
    const kind = mediaKindForItem(img);
    if(kind === 'audio'){
        return `<div class="media-thumb audio-thumb mention-option-audio"><i data-lucide="file-audio"></i><span>${escapeHtml(img.alias || img.name || 'Audio')}</span></div>`;
    }
    return kind === 'video' ? smartVideoPreviewHtml(img, 256, 'alt=""') : smartPreviewImgHtml(img, 256, 'alt=""');
}
function promptHtmlWithMentionTokens(text, refs=[]){
    const value = String(text || '');
    const items = (refs || []).filter(ref => ref?.url && ref?.name).sort((a, b) => String(b.name || '').length - String(a.name || '').length);
    if(!value || !items.length || !value.includes('@')) return '';
    let html = '';
    let index = 0;
    while(index < value.length){
        if(value[index] === '@'){
            const hit = items.find(ref => value.slice(index + 1, index + 1 + String(ref.name || '').length) === String(ref.name || ''));
            if(hit){
                html += mentionTokenHtml(hit);
                index += 1 + String(hit.name || '').length;
                continue;
            }
        }
        html += escapeHtml(value[index]);
        index += 1;
    }
    return html;
}
function snapshotRunMeta(prompt, sourceId, displayPrompt='', refs=[]){
    return {
        prompt,
        displayPrompt:displayPrompt || promptPlainText() || prompt,
        promptHtml: promptInput ? promptInput.innerHTML : '',
        promptText: promptPlainText(),
        promptRefs:(refs || []).map(ref => ({asset_id:ref.asset_id || '', url:ref.url || '', name:ref.name || '', nodeId:ref.nodeId || '', imageIndex:ref.imageIndex ?? ''})).filter(ref => ref.url),
        inputRefs:(refs || []).map(ref => ({asset_id:ref.asset_id || '', url:ref.url || '', name:ref.name || '', nodeId:ref.nodeId || '', imageIndex:ref.imageIndex ?? '', kind:ref.kind || ''})).filter(ref => ref.url),
        sourceNodeId:sourceId,
        settings:JSON.parse(JSON.stringify(settings)),
        createdAt:Date.now()
    };
}
function attachRunMeta(targetNode, meta){
    if(!targetNode || !meta) return;
    targetNode.runPrompt = meta.displayPrompt || meta.promptText || meta.prompt;
    targetNode.runModelPrompt = meta.prompt;
    targetNode.runPromptRefs = meta.promptRefs || [];
    targetNode.runInputRefs = (meta.inputRefs || meta.promptRefs || []).map(ref => ({
        asset_id:ref.asset_id || '',
        url:ref.url || '',
        name:ref.name || '',
        nodeId:ref.nodeId || '',
        imageIndex:ref.imageIndex ?? '',
        kind:ref.kind || ''
    })).filter(ref => ref.url);
    targetNode.runSettings = meta.settings;
    if(meta.sourceNodeId) targetNode.sourceNodeId = meta.sourceNodeId;
    else delete targetNode.sourceNodeId;
    targetNode.runAt = meta.createdAt;
    // 保存可编辑的 @-提及表单到草稿字段，方便点输出节点时还原原始可编辑形式
    if(meta.promptHtml != null){
        const htmlHasToken = String(meta.promptHtml || '').includes('mention-image-token');
        const rebuiltHtml = htmlHasToken ? '' : promptHtmlWithMentionTokens(meta.displayPrompt || meta.promptText || '', meta.promptRefs || []);
        targetNode.promptDraftHtml = htmlHasToken ? meta.promptHtml : (rebuiltHtml || meta.promptHtml);
        targetNode.promptDraftText = meta.promptText || '';
    }
    targetNode.images = (targetNode.images || []).map(img => stripImageGenerationMeta(img));
}
function stripRunInputMeta(meta){
    if(!meta) return meta;
    const cleanPrompt = meta.promptText || meta.displayPrompt || meta.prompt || '';
    return {
        ...meta,
        promptHtml:escapeHtml(cleanPrompt),
        promptText:cleanPrompt,
        promptRefs:[],
        inputRefs:meta.inputRefs || meta.promptRefs || [],
        sourceNodeId:''
    };
}
function stripImageGenerationMeta(img){
    if(!img) return img;
    delete img.runPrompt;
    delete img.runModelPrompt;
    delete img.runSettings;
    delete img.sourceNodeId;
    delete img.runAt;
    delete img.promptDraftHtml;
    delete img.promptDraftText;
    return img;
}
function upstreamNodesForKinds(node, kinds=['input']){
    if(!node) return [];
    const allowed = new Set(kinds);
    const ids = new Set();
    (canvas?.connections || []).forEach(conn => {
        if(conn.to === node.id && allowed.has(conn.kind || 'flow')) ids.add(conn.from);
    });
    if(!canvasUsesConnections && allowed.has('input')){
        (node.inputNodeIds || []).forEach(id => ids.add(id));
    }
    return [...ids].map(id => canvasState.nodes.find(n => n.id === id)).filter(Boolean);
}
function inputNodesFor(node){
    return upstreamNodesForKinds(node, ['input']);
}
function workflowInputNodesFor(node){
    return upstreamNodesForKinds(node, ['input', 'flow']);
}
function clearDetachedRunInputRefs(node){
    if(!node) return;
    const hasUpstream = Boolean((canvas?.connections || []).some(conn => conn.to === node.id && ['input','flow'].includes(conn.kind || 'flow')));
    if(hasUpstream || (!canvasUsesConnections && Array.isArray(node.inputNodeIds) && node.inputNodeIds.some(id => canvasState.nodes.some(n => n.id === id)))) return;
    delete node.runInputRefs;
    delete node.runPromptRefs;
    delete node.sourceNodeId;
}
function cleanupDetachedRunInputRefs(){
    if(!canvasUsesConnections) return false;
    let changed = false;
    canvasState.nodes.forEach(node => {
        const hadRefs = Array.isArray(node?.runInputRefs) && node.runInputRefs.length;
        const hadPromptRefs = Array.isArray(node?.runPromptRefs) && node.runPromptRefs.length;
        const hadSource = Boolean(node?.sourceNodeId);
        clearDetachedRunInputRefs(node);
        if(hadRefs !== (Array.isArray(node?.runInputRefs) && node.runInputRefs.length)
            || hadPromptRefs !== (Array.isArray(node?.runPromptRefs) && node.runPromptRefs.length)
            || hadSource !== Boolean(node?.sourceNodeId)){
            changed = true;
        }
    });
    return changed;
}
function imagesForNode(node){
    if(isSmartGroupNode(node)){
        return smartGroupImageRefs(node).map((ref, index) => ({
            ...ref.item,
            nodeId:ref.nodeId,
            imageIndex:ref.index,
            groupNodeId:node.id,
            groupImageIndex:index
        }));
    }
    return (node?.images || []).map((img, index) => ({...imageForDisplay(img), nodeId:node.id, imageIndex:index}));
}
function isSelfReferenceForNode(node, img){
    return Boolean(node?.id && img?.nodeId === node.id);
}
function splitSmartPromptItems(text){
    const trimmed = String(text || '').trim();
    if(!trimmed) return [];
    const numbered = trimmed.split(/\s*(?:^|\s)\d+\s*[.、)）．]\s+/).map(s => s.trim()).filter(Boolean);
    if(numbered.length >= 2) return numbered;
    const lines = trimmed.split(/\r?\n+/).map(s => s.trim()).filter(Boolean);
    return lines.length >= 2 ? lines : [trimmed];
}
function smartLoopPromptFieldValues(node){
    const fields = Array.isArray(node?.variablePrompts)
        ? node.variablePrompts.map(text => String(text || '').trim())
        : [];
    if(fields.length) return fields;
    return splitSmartPromptItems(node?.variablePrompt || '');
}
function smartLoopActivePromptFieldValues(node){
    return smartLoopPromptFieldValues(node).filter(Boolean);
}
function smartLoopDefaultPromptText(){
    return tr('smart.loopDefaultPrompt') || '现在生成第《计数》张卖点图片';
}
function isSmartLoopDefaultPrompt(text){
    const value = String(text || '').trim();
    if(!value) return false;
    return value === smartLoopDefaultPromptText()
        || value === '现在生成第《计数》张卖点图片'
        || value === 'Generate selling-point image 《计数》';
}
function setSmartLoopPromptFieldValues(node, values){
    if(!node || node.type !== 'smart-loop') return;
    const fields = (values || []).map(text => String(text || '').trim());
    node.variablePrompts = fields.length ? fields : [''];
    node.variablePrompt = fields.filter(Boolean).join('\n');
}
function smartLoopSelectedLocalPrompt(node, ctx=smartLoopContext){
    const values = smartLoopActivePromptFieldValues(node);
    if(!values.length) return '';
    const startBase = Math.max(1, Number(node?.loopStart) || 1);
    const index = Math.max(1, Number(ctx?.index || startBase) || startBase);
    return values[(index - 1) % values.length] || '';
}
function smartLoopUpstreamPromptPreviewHeight(node){
    return smartLoopInputPromptItems(node).length ? 78 : 0;
}
const smartLoopPromptVisiting = new Set();
function smartLoopInputPromptItems(node){
    if(!node?.showPrompt || smartLoopPromptVisiting.has(node.id)) return [];
    smartLoopPromptVisiting.add(node.id);
    try {
        return inputNodesFor(node)
            .flatMap(input => promptTextItemsForNode(input))
            .map(text => String(text || '').trim())
            .filter(Boolean);
    } finally {
        smartLoopPromptVisiting.delete(node.id);
    }
}
function smartLoopSelectedInputPrompt(node, ctx=smartLoopContext){
    const items = smartLoopInputPromptItems(node);
    if(!items.length) return '';
    const startBase = Math.max(1, Number(node?.loopStart) || 1);
    const index = Math.max(1, Number(ctx?.index || startBase) || startBase);
    return items[(index - 1) % items.length] || '';
}
function smartLoopPrompt(node, ctx=smartLoopContext){
    if(!node?.showPrompt) return '';
    const count = smartLoopCount(node);
    const startBase = Math.max(1, Number(node.loopStart) || 1);
    const index = Math.max(1, Number(ctx?.index || startBase) || startBase);
    const total = Math.max(1, Number(ctx?.total || count) || count);
    const selected = smartLoopSelectedInputPrompt(node, ctx);
    const localPrompt = smartLoopSelectedLocalPrompt(node, ctx);
    const effectiveLocalPrompt = selected && isSmartLoopDefaultPrompt(localPrompt) ? '' : localPrompt;
    const combined = [selected, effectiveLocalPrompt].map(text => String(text || '').trim()).filter(Boolean).join('\n\n');
    return String(combined || '')
        .replaceAll('《计数》', String(index))
        .replaceAll('[计数]', String(index))
        .replaceAll(`[${tr('canvas.counterToken')}]`, String(index))
        .replaceAll('《总数》', String(total))
        .replaceAll('[总数]', String(total))
        .replaceAll('《进度》', `${index}/${total}`)
        .replaceAll('[进度]', `${index}/${total}`)
        .trim();
}
function smartLoopInputImages(node, ctx=smartLoopContext){
    if(!node?.imageInput) return [];
    const refs = inputNodesFor(node).flatMap(input => {
        if(input?.type === 'smart-loop') return smartLoopInputImages(input, ctx);
        return imagesForNode(input);
    }).filter(img => img?.url);
    if(!refs.length) return [];
    const startBase = Math.max(1, Number(node.loopStart) || 1);
    const batchSize = Math.max(1, Math.min(100, Number(node.imageBatchSize) || 1));
    const currentIndex = Math.max(1, Number(ctx?.index || startBase) || startBase);
    return refs.slice(Math.max(0, currentIndex - 1), Math.max(0, currentIndex - 1) + batchSize)
        .map((img, i) => ({...img, name:img.name || trf('canvas.loopImageLabel', {n:currentIndex + i})}));
}
function smartLoopPreviewImages(node){
    if(!node?.imageInput) return [];
    return inputNodesFor(node).flatMap(input => {
        if(input?.type === 'smart-loop') return smartLoopInputImages(input, {index:Number(node.loopStart) || 1});
        return imagesForNode(input);
    }).filter(img => img?.url);
}
function outputImagesForNode(node, consume=false, ctx=smartLoopContext){
    if(node?.type === 'smart-group') return imagesForNode(node).filter(img => img?.url);
    if(node?.type === 'smart-loop') return smartLoopInputImages(node, ctx);
    const roundOutputs = ctx?.roundOutputs;
    if(node?.id && roundOutputs && typeof roundOutputs.get === 'function' && roundOutputs.has(node.id)){
        return (roundOutputs.get(node.id) || []).filter(img => img?.url);
    }
    return imagesForNode(node).filter(img => img?.url);
}
function selfReferenceImagesForNode(node, consume=false, ctx=smartLoopContext){
    return outputImagesForNode(node, consume, ctx).filter(img => img?.url);
}
function textForNode(node, ctx=smartLoopContext){
    if(!node) return '';
    if(node.type === 'smart-prompt') return promptNodePromptItems(node).join('\n\n');
    if(node.type === 'smart-loop') return smartLoopPrompt(node, ctx);
    if(node.type === 'smart-group') return smartGroupMembers(node).map(member => textForNode(member, ctx)).filter(Boolean).join('\n\n');
    return '';
}
function promptInputNodesFor(node){
    return inputNodesFor(node).filter(input => input?.type === 'smart-prompt' || input?.type === 'smart-loop' || input?.type === 'smart-group');
}
function inputPromptTextFor(node, ctx=smartLoopContext){
    const directText = promptInputNodesFor(node).map(input => textForNode(input, ctx)).filter(Boolean);
    const relayText = Array.isArray(ctx?.relayPromptNodeIds)
        ? ctx.relayPromptNodeIds.map(id => canvasState.nodes.find(n => n.id === id)).map(input => textForNode(input, ctx)).filter(Boolean)
        : [];
    const seen = new Set();
    return [...directText, ...relayText].filter(text => {
        const key = String(text || '').trim();
        if(!key || seen.has(key)) return false;
        seen.add(key);
        return true;
    }).join('\n\n');
}
function upstreamLoopPromptNodesFor(node){
    return promptInputNodesFor(node).filter(input => input?.type === 'smart-loop' && input.showPrompt);
}
function inputImagesFor(node, consume=false, ctx=smartLoopContext){
    return inputNodesFor(node).flatMap(input => outputImagesForNode(input, consume, ctx));
}
function workflowInputImagesFor(node, consume=false, ctx=smartLoopContext){
    return workflowInputNodesFor(node).flatMap(input => outputImagesForNode(input, consume, ctx));
}
function rememberRoundOutputs(ctx, node, outputs){
    if(!ctx || !node?.id || !Array.isArray(outputs)) return outputs || [];
    if(!ctx.roundOutputs || typeof ctx.roundOutputs.set !== 'function') ctx.roundOutputs = new Map();
    ctx.roundOutputs.set(node.id, outputs.filter(img => img?.url).map(img => ({...img})));
    return outputs;
}
function inputRefKey(img){
    if(!img?.url) return '';
    const nodeId = img.nodeId || '';
    const imageIndex = Number.isFinite(Number(img.imageIndex)) ? String(Number(img.imageIndex)) : '';
    if(nodeId && imageIndex !== '') return `${nodeId}|${imageIndex}`;
    return `url|${img.url}`;
}
function blockedInputRefKeys(node){
    return new Set(Array.isArray(node?.blockedInputRefs) ? node.blockedInputRefs.filter(Boolean) : []);
}
function manualReferenceImagesFor(node){
    if(!node || !Array.isArray(node.manualInputRefs)) return [];
    return node.manualInputRefs.filter(img => img?.url).map((img, index) => ({
        ...img,
        kind:img.kind || mediaKindForItem(img),
        name:img.name || `图${index + 1}`,
        imageIndex:Number.isFinite(Number(img.imageIndex)) ? Number(img.imageIndex) : index,
        manualAdded:true
    }));
}
function defaultReferenceImagesFor(node, consume=false, ctx=smartLoopContext){
    if(!node) return [];
    const upstream = (smartImageUsesWorkflowInput(node, ctx) ? workflowInputImagesFor(node, consume, ctx) : inputImagesFor(node, consume, ctx))
        .filter(img => img?.url);
    const manual = manualReferenceImagesFor(node);
    return uniqueReferenceImages([...upstream, ...manual]);
}
function upstreamLineNodeIds(node){
    if(!node) return [];
    const conns = lineConnectionsFor(node);
    const upstream = [];
    const seen = new Set([node.id]);
    const walk = id => {
        conns.filter(conn => conn.to === id).forEach(conn => {
            if(seen.has(conn.from)) return;
            seen.add(conn.from);
            walk(conn.from);
            upstream.push(conn.from);
        });
    };
    walk(node.id);
    return [...upstream, node.id];
}
function lineImagesFor(node){
    const ids = upstreamLineNodeIds(node);
    return ids.flatMap(id => {
        const source = canvasState.nodes.find(n => n.id === id);
        return imagesForNode(source);
    }).filter(img => img?.url);
}
function collectMentionedImagesFromPrompt(){
    const images = [];
    collectPromptParts().forEach(part => {
        if(part.type === 'image' && part.url) images.push(part);
    });
    return images;
}
function uniqueReferenceImages(images){
    const refs = [];
    const seen = new Set();
    (images || []).forEach((img, index) => {
        if(!img?.url || seen.has(img.url)) return;
        seen.add(img.url);
        if(refs.length >= SMART_REFERENCE_IMAGE_MAX) return;
        refs.push({
            ...img,
            name:img.name || `图${refs.length + 1}`,
            role:img.role || `image_${refs.length + 1}`,
            imageIndex:Number.isFinite(Number(img.imageIndex)) ? Number(img.imageIndex) : index
        });
    });
    return refs;
}
function visibleReferenceImagesFor(node){
    const base = defaultReferenceImagesFor(node);
    return uniqueReferenceImages([...base, ...collectMentionedImagesFromPrompt()]);
}
function inputMentionCandidateImages(node){
    const current = node ? [...lineImagesFor(node), ...manualReferenceImagesFor(node)] : [];
    const seen = new Set();
    return current.filter(img => {
        if(!img?.url || seen.has(img.url)) return false;
        seen.add(img.url);
        return true;
    }).map((img, index) => ({
        ...img,
        mentionId:`mention_${index}_${Math.random().toString(36).slice(2, 7)}`,
        alias:img.name || `图片${index + 1}`
    }));
}
// 一个素材可注册到多个平台：收集所有「已通过」的 asset:// 地址，按平台映射。
function assetRegisteredUris(item){
    const regs = (item && item.registrations && typeof item.registrations === 'object') ? item.registrations : {};
    const out = {};
    Object.keys(regs).forEach(platform => {
        const reg = regs[platform];
        if(reg && reg.status === 'Active' && reg.asset_uri) out[platform] = reg.asset_uri;
    });
    return out;
}
function assetMentionCandidateImages(categoryId=''){
    const cats = assetCategories('image');
    const cat = cats.find(c => c.id === categoryId) || assetCategoryForMention();
    if(!cat) return [];
    mentionAssetCategoryId = cat.id;
    const items = (cat.items || []).map(item => ({...item, categoryName:cat.name || '', categoryId:cat.id}));
    const seen = new Set();
    return items.filter(item => {
        if(!item?.url || seen.has(item.url)) return false;
        seen.add(item.url);
        return true;
    }).map((item, index) => ({
        url:item.url,
        asset_id:item.asset_id || '',
        preview_url:item.preview_url || '',
        kind:assetMediaKind(item),
        name:item.name || `资产${index + 1}`,
        alias:item.name || `资产${index + 1}`,
        role:'asset',
        categoryName:item.categoryName || '',
        asset_uris:assetRegisteredUris(item),
        mentionId:`asset_${index}_${Math.random().toString(36).slice(2, 7)}`
    }));
}
function closeMentionPicker(){
    mentionPicker.classList.remove('open');
    mentionPicker.innerHTML = '';
    mentionAnchorEl = null;
    mentionInsertMode = 'token';
    if(selectedNode()) renderInputThumbsRow(selectedNode());
}
function saveMentionRange(){
    const sel = window.getSelection();
    if(sel && sel.rangeCount && promptInput.contains(sel.anchorNode)){
        mentionRange = sel.getRangeAt(0).cloneRange();
    }
}
function textBeforeCaret(){
    const sel = window.getSelection();
    if(!sel || !sel.rangeCount || !promptInput.contains(sel.anchorNode)) return '';
    const range = sel.getRangeAt(0).cloneRange();
    range.selectNodeContents(promptInput);
    range.setEnd(sel.anchorNode, sel.anchorOffset);
    return range.toString();
}
function renderMentionPicker(source){
    const node = selectedNode();
    const inputItems = inputMentionCandidateImages(node);
    const assetLibs = currentAssetSourceLibraries();
    if(!activeAssetLibraryId || !assetLibs.some(lib => lib.id === activeAssetLibraryId)) activeAssetLibraryId = assetLibrary.active_library_id || assetLibs[0]?.id || '';
    const libraryWithMentionAssets = assetLibs.find(lib => (lib.categories || []).some(cat => (cat.type || 'image') === 'image' && (cat.items || []).some(item => item?.url)));
    const assetCats = assetCategories('image');
    const hasInput = inputItems.length > 0;
    const hasAssets = Boolean(libraryWithMentionAssets);
    mentionSource = source || (hasInput ? 'input' : 'asset');
    if(mentionSource === 'asset' && hasAssets && !assetCats.some(cat => (cat.items || []).some(item => item?.url)) && libraryWithMentionAssets){
        activeAssetLibraryId = libraryWithMentionAssets.id;
        activeAssetCategoryId = '';
        mentionAssetCategoryId = '';
    }
    if(mentionSource === 'input' && !hasInput && hasAssets) mentionSource = 'asset';
    if(mentionSource === 'asset' && !hasAssets && hasInput) mentionSource = 'input';
    if(!hasInput && !hasAssets){ closeMentionPicker(); return; }
    const nextAssetCats = assetCategories('image');
    const currentAssetCat = assetCategoryForMention();
    const assetItems = assetMentionCandidateImages(currentAssetCat?.id || '');
    const candidates = (mentionSource === 'asset' ? assetItems : inputItems).slice(0, 36);
    const body = candidates.length ? `<div class="mention-option-grid">${candidates.map((img, i) => `
            <button class="mention-option" type="button" data-mention-index="${i}">
                ${mentionOptionMediaHtml(img)}
                <span>${escapeHtml(img.alias)}</span>
            </button>
        `).join('')}</div>` : `<div class="mention-empty">${escapeHtml(tr('smart.mentionEmpty'))}</div>`;
    const librarySelect = (mentionSource === 'asset' && assetLibs.length)
        ? `<label class="mention-library-row"><span>${escapeHtml(tr('smart.assetLibrary'))}</span><select class="mention-library-select" data-mention-library>${assetLibs.map(lib => `<option value="${escapeHtml(lib.id)}" ${lib.id === activeAssetLibraryId ? 'selected' : ''}>${escapeHtml(lib.name || '全局资源库')}</option>`).join('')}</select></label>`
        : '';
    const folderChips = (mentionSource === 'asset' && nextAssetCats.length)
        ? nextAssetCats.map(cat => {
            const label = cat.name || tr('smart.assetFolder');
            return `<button class="mention-folder-chip ${cat.id === mentionAssetCategoryId ? 'active' : ''}" type="button" data-mention-folder="${escapeHtml(cat.id)}" title="${escapeHtml(label)}">${escapeHtml(label)}</button>`;
          }).join('')
        : '';
    mentionPicker.innerHTML = `
        <div class="mention-picker-shell">
            <div class="mention-source-tabs">
                <button class="mention-source-tab ${mentionSource === 'input' ? 'active' : ''}" type="button" data-mention-source="input" title="${escapeHtml(tr('smart.mentionInput'))}" ${hasInput ? '' : 'disabled'}>
                    <i data-lucide="image"></i><span>${escapeHtml(tr('smart.mentionInput'))}</span>
                </button>
                <button class="mention-source-tab ${mentionSource === 'asset' ? 'active' : ''}" type="button" data-mention-source="asset" title="${escapeHtml(tr('smart.mentionAssets'))}" ${hasAssets ? '' : 'disabled'}>
                    <i data-lucide="library"></i><span>${escapeHtml(tr('smart.mentionAssets'))}</span>
                </button>
            </div>
            ${librarySelect}
            <div class="mention-folder-chips ${folderChips ? '' : 'hidden'}">
                ${folderChips}
            </div>
            <div class="mention-content">
                ${body}
            </div>
        </div>
    `;
    mentionPicker._items = candidates;
    bindSmartPreviewImageFallbacks(mentionPicker);
    if(mentionInsertMode === 'manual-ref'){
        placeMentionPickerInComposerCard();
        renderInputThumbsRow(selectedNode());
        mentionAnchorEl = inputThumbsRow?.querySelector('[data-input-add-reference]') || inputThumbsRow;
    } else {
        placeMentionPickerInPromptRow();
    }
    positionMentionPickerAtCaret();
    mentionPicker.classList.add('open');
    mentionPicker.querySelectorAll('[data-mention-source]').forEach(btn => {
        btn.addEventListener('mousedown', e => {
            e.preventDefault(); e.stopPropagation();
            if(btn.disabled) return;
            renderMentionPicker(btn.dataset.mentionSource);
        });
    });
    mentionPicker.querySelectorAll('[data-mention-library]').forEach(select => {
        select.addEventListener('mousedown', e => e.stopPropagation());
        select.addEventListener('change', e => {
            activeAssetLibraryId = e.target.value || '';
            activeAssetCategoryId = '';
            mentionAssetCategoryId = '';
            renderAssetLibrary();
            renderMentionPicker('asset');
        });
    });
    mentionPicker.querySelectorAll('[data-mention-folder]').forEach(btn => {
        btn.addEventListener('mousedown', e => {
            e.preventDefault(); e.stopPropagation();
            mentionAssetCategoryId = btn.dataset.mentionFolder || '';
            renderMentionPicker('asset');
        });
    });
    mentionPicker.querySelectorAll('[data-mention-index]').forEach(btn => {
        btn.addEventListener('mousedown', e => {
            e.preventDefault(); e.stopPropagation();
            const item = mentionPicker._items[Number(btn.dataset.mentionIndex)];
            if(mentionInsertMode === 'manual-ref') addManualReferenceToSelectedNode(item);
            else insertMentionToken(item);
        });
    });
    refreshIcons();
}
function showMentionPicker(){
    const node = selectedNode();
    const hasInput = inputMentionCandidateImages(node).length > 0;
    mentionInsertMode = 'token';
    mentionAnchorEl = null;
    placeMentionPickerInPromptRow();
    mentionSource = hasInput ? 'input' : 'asset';
    renderMentionPicker(mentionSource);
}
function promptRangeIsUsable(range){
    const container = range?.commonAncestorContainer;
    return Boolean(container && (container === promptInput || promptInput.contains(container)));
}
function insertInputReferenceMarker(referenceNumber){
    const number = Math.floor(Number(referenceNumber));
    if(!promptInput || !Number.isFinite(number) || number < 1 || promptInput.dataset.promptLocked === '1') return false;
    const marker = `@${number}`;
    const savedPromptRange = promptRangeIsUsable(mentionRange) ? mentionRange.cloneRange() : null;
    promptInput.focus();
    const sel = window.getSelection();
    // Clicking a thumbnail may move the live browser selection away from the
    // contenteditable before the click handler runs. Prefer the last caret or
    // selection captured from the prompt itself.
    let range = savedPromptRange
        ? savedPromptRange
        : (sel?.rangeCount ? sel.getRangeAt(0) : null);
    if(!promptRangeIsUsable(range)){
        range = document.createRange();
        range.selectNodeContents(promptInput);
        range.collapse(false);
    }
    range.deleteContents();
    const markerNode = document.createTextNode(marker);
    range.insertNode(markerNode);
    range.setStartAfter(markerNode);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
    mentionRange = range.cloneRange();
    promptInput.dispatchEvent(new Event('input', {bubbles:true}));
    promptInput.focus();
    return true;
}
function toggleAssetMentionPickerFromThumbs(){
    if(!selectedNode()) return;
    if(mentionInsertMode === 'manual-ref'){
        closeMentionPicker();
        return;
    }
    mentionInsertMode = 'manual-ref';
    renderInputThumbsRow(selectedNode());
    mentionAnchorEl = inputThumbsRow?.querySelector('[data-input-add-reference]') || inputThumbsRow;
    renderMentionPicker('asset');
}
function addManualReferenceToSelectedNode(img){
    const node = selectedNode();
    if(!node || !img?.url) return;
    const kind = img.kind || mediaKindForItem(img);
    const ref = {
        url:img.url,
        name:img.alias || img.name || (kind === 'audio' ? '音频' : kind === 'video' ? '视频' : '图片'),
        kind,
        nodeId:img.nodeId || '',
        imageIndex:Number.isFinite(Number(img.imageIndex)) ? Number(img.imageIndex) : '',
        asset_uris:img.asset_uris || {},
        manualAdded:true
    };
    if(img.asset_id) ref.asset_id = img.asset_id;
    if(img.preview_url) ref.preview_url = img.preview_url;
    if(img.originalLocalUrl) ref.originalLocalUrl = img.originalLocalUrl;
    const refs = Array.isArray(node.manualInputRefs) ? node.manualInputRefs.slice() : [];
    const key = inputRefKey(ref);
    const exists = refs.some(item => inputRefKey(item) === key || item.url === ref.url);
    if(exists){
        closeMentionPicker();
        return;
    }
    pushUndo();
    refs.push(ref);
    node.manualInputRefs = refs;
    closeMentionPicker();
    renderInputThumbsRow(node);
    scheduleSave();
}
function removeManualReferenceFromSelectedNode(key){
    const node = selectedNode();
    if(!node || !key || !Array.isArray(node.manualInputRefs)) return;
    const refs = node.manualInputRefs.slice();
    const index = refs.findIndex(ref => inputRefKey(ref) === key || ref?.url === key.replace(/^url\|/, ''));
    if(index < 0) return;
    pushUndo();
    refs.splice(index, 1);
    node.manualInputRefs = refs;
    if(!refs.length) delete node.manualInputRefs;
    renderInputThumbsRow(node);
    scheduleSave();
}
function placeMentionPickerInPromptRow(){
    const row = promptInput?.closest?.('.prompt-row');
    if(row && mentionPicker.parentElement !== row) row.insertBefore(mentionPicker, promptResize || null);
}
function placeMentionPickerInComposerCard(){
    const card = promptInput?.closest?.('.composer-card');
    if(card && mentionPicker.parentElement !== card) card.appendChild(mentionPicker);
}
function positionMentionPickerAtCaret(){
    const row = promptInput.closest('.prompt-row');
    const rowRect = row.getBoundingClientRect();
    if(mentionAnchorEl){
        const anchorRect = mentionAnchorEl.getBoundingClientRect();
        const scale = (typeof viewport !== 'undefined' && Number(viewport?.scale)) || 1;
        const safeScale = scale > 0 ? scale : 1;
        const pickerWidth = mentionPicker.offsetWidth || 340;
        const base = mentionPicker.offsetParent || mentionPicker.parentElement || row;
        const baseRect = base.getBoundingClientRect();
        const baseLogicalWidth = baseRect.width / safeScale;
        const rawLeft = (anchorRect.right - baseRect.left) / safeScale - pickerWidth;
        const rawTop = (anchorRect.bottom - baseRect.top) / safeScale + 2;
        const left = Math.max(4, Math.min(rawLeft, Math.max(4, baseLogicalWidth - pickerWidth - 4)));
        mentionPicker.style.left = `${left}px`;
        mentionPicker.style.top = `${Math.max(2, rawTop)}px`;
        return;
    }
    let caretRect = null;
    const sel = window.getSelection();
    if(sel && sel.rangeCount){
        const range = sel.getRangeAt(0).cloneRange();
        caretRect = range.getClientRects()[0] || range.getBoundingClientRect();
    }
    const inputRect = promptInput.getBoundingClientRect();
    // composer 在 world 里被 viewport.scale 缩放过，getBoundingClientRect 返回的是缩放后的屏幕像素，
    // 而 style.left/top 是逻辑像素 → 需要除以 scale 才能正确还原 caret 的逻辑坐标
    const scale = (typeof viewport !== 'undefined' && Number(viewport?.scale)) || 1;
    const safeScale = scale > 0 ? scale : 1;
    const rowLogicalWidth = rowRect.width / safeScale;
    const pickerWidth = mentionPicker.offsetWidth || 340;
    const maxLeft = Math.max(4, rowLogicalWidth - pickerWidth - 4);
    const rawLeft = ((caretRect?.left || inputRect.left) - rowRect.left) / safeScale - 6;
    const rawTop = ((caretRect?.bottom || inputRect.top + 24) - rowRect.top) / safeScale + 2;
    const left = Math.max(4, Math.min(rawLeft, maxLeft));
    const top = Math.max(2, rawTop);
    mentionPicker.style.left = `${left}px`;
    mentionPicker.style.top = `${top}px`;
}
function maybeOpenMentionPicker(){
    saveMentionRange();
    const before = textBeforeCaret();
    if(/@$/.test(before)) showMentionPicker();
    else closeMentionPicker();
}
function insertMentionToken(img){
    if(!img?.url) return;
    promptInput.focus();
    const sel = window.getSelection();
    if(mentionRange){
        sel.removeAllRanges();
        sel.addRange(mentionRange);
    }
    const range = sel.rangeCount ? sel.getRangeAt(0) : document.createRange();
    let removedAt = false;
    if(range.startContainer?.nodeType === Node.TEXT_NODE && range.startOffset > 0){
        const text = range.startContainer.textContent || '';
        if(text[range.startOffset - 1] === '@'){
            range.setStart(range.startContainer, range.startOffset - 1);
            range.deleteContents();
            removedAt = true;
        }
    }
    if(!removedAt) {
        const walker = document.createTreeWalker(promptInput, NodeFilter.SHOW_TEXT);
        let lastText = null;
        while(walker.nextNode()) lastText = walker.currentNode;
        if(lastText && /@$/.test(lastText.textContent || '')) {
            lastText.textContent = lastText.textContent.slice(0, -1);
            range.selectNodeContents(promptInput);
            range.collapse(false);
        }
    }
    const token = document.createElement('span');
    token.className = 'mention-image-token';
    token.contentEditable = 'false';
    token.dataset.url = img.url;
    token.dataset.kind = mediaKindForItem(img);
    token.dataset.name = img.alias || img.name || (token.dataset.kind === 'audio' ? '音频' : token.dataset.kind === 'video' ? '视频' : '图片');
    token.dataset.nodeId = img.nodeId || '';
    token.dataset.imageIndex = String(img.imageIndex ?? '');
    token.dataset.assetUris = JSON.stringify(img.asset_uris || {});
    token.innerHTML = `${mentionTokenMediaHtml(img, token.dataset.kind)}<span>${escapeHtml(token.dataset.name)}</span>`;
    range.insertNode(token);
    bindSmartPreviewImageFallbacks(token);
    const spacer = document.createTextNode(' ');
    token.after(spacer);
    range.setStartAfter(spacer);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
    closeMentionPicker();
    promptInput.focus();
    renderInputThumbsRow(selectedNode());
}
function collectPromptParts(root=promptInput){
    const parts = [];
    if(!root) return parts;
    const walk = node => {
        if(node.nodeType === Node.TEXT_NODE){
            if(node.textContent) parts.push({type:'text', text:node.textContent});
            return;
        }
        if(node.nodeType !== Node.ELEMENT_NODE) return;
        if(node.classList?.contains('mention-image-token')){
            let assetUris = {};
            try { assetUris = JSON.parse(node.dataset.assetUris || '{}') || {}; } catch(e) { assetUris = {}; }
            const kind = node.dataset.kind || 'image';
            parts.push({type:'image', kind, url:node.dataset.url || '', name:node.dataset.name || (kind === 'audio' ? '音频' : '图片'), nodeId:node.dataset.nodeId || '', imageIndex:Number(node.dataset.imageIndex || 0), asset_uris:assetUris});
            return;
        }
        if(node.tagName === 'BR'){
            parts.push({type:'text', text:'\n'});
            return;
        }
        const blockTags = new Set(['DIV','P','LI','SECTION','ARTICLE','HEADER','FOOTER','BLOCKQUOTE']);
        const isBlock = node !== root && blockTags.has(node.tagName);
        if(isBlock && parts.length && parts[parts.length - 1]?.text && !/\n$/.test(parts[parts.length - 1].text)) parts.push({type:'text', text:'\n'});
        node.childNodes.forEach(walk);
        if(isBlock) parts.push({type:'text', text:'\n'});
    };
    root.childNodes.forEach(walk);
    return parts;
}
function originalPromptTextFromParts(parts){
    let text = '';
    (parts || []).forEach(part => {
        if(part.type === 'text'){
            text += part.text || '';
            return;
        }
        if(part.type === 'image') text += `@${part.name || '图片'}`;
    });
    return text.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}
function buildPromptRequest(node, overrideDefaultImages=null, consumeDefault=false, ctx=smartLoopContext, promptRoot=promptInput){
    const parts = collectPromptParts(promptRoot);
    const originalPrompt = originalPromptTextFromParts(parts);
    const blockedRefs = blockedInputRefKeys(node);
    const hasOverrideImages = Array.isArray(overrideDefaultImages);
    const filteredDefaultImages = (hasOverrideImages ? overrideDefaultImages : defaultReferenceImagesFor(node, consumeDefault, ctx))
        .filter(img => !blockedRefs.has(inputRefKey(img)));
    const defaultRefs = uniqueReferenceImages(filteredDefaultImages);
    const refs = defaultRefs.map((img, index) => ({...img, role:`image_${index + 1}`}));
    // 扩图修复：扩图激活时，必须把扩图底图（红底+原图合成图，即节点当前图）作为参考图发给模型，
    // 否则模型只拿到提示词而没有底图，会凭空生成全新图而不是扩展原图（"扩图不生效"根因）。
    // 注意：不能把原始 URL 存到节点顶层字段（会触发"只能引用项目媒体资源ID"校验），
    // 直接取 node.images[0]，生成时 importProjectMediaReference 会把它归档为项目媒体。
    if(validOutpaintSize(node)){
        const selfImg = (node.images || []).find(img => img?.url);
        if(selfImg?.url && !refs.some(ref => ref.url === selfImg.url)){
            if(refs.length >= SMART_REFERENCE_IMAGE_MAX){
                // 扩图底图是必需参考，默认参考图是可选的：让出最后一位，保持不超过安全上限
                refs.pop();
            }
            refs.push({url:selfImg.url, asset_id:selfImg.asset_id || '', name:selfImg.name || '扩图底图', kind:selfImg.kind || 'image', nodeId:node.id, imageIndex:0, selfReference:true, role:`image_${refs.length + 1}`});
        }
    }
    let hasMentionToken = false;
    const refMap = new Map();
    refs.forEach((img, index) => refMap.set(img.url, index + 1));
    let body = '';
    parts.forEach(part => {
        if(part.type === 'text'){
            body += part.text;
            return;
        }
        if(!part.url) return;
        hasMentionToken = true;
        const mentionedKey = inputRefKey(part);
        if(blockedRefs.has(mentionedKey)){
            body += `@${part.name || '图片'}`;
            return;
        }
        if(!refMap.has(part.url)){
            if(refs.length >= SMART_REFERENCE_IMAGE_MAX){
                body += `@${part.name || '图片'}`;
                return;
            }
            refMap.set(part.url, refs.length + 1);
            refs.push({url:part.url, name:part.name || `图${refs.length + 1}`, nodeId:part.nodeId, imageIndex:part.imageIndex, kind:part.kind || 'image', asset_uris:part.asset_uris || {}, role:`image_${refs.length + 1}`});
        }
        body += `图${refMap.get(part.url)}`;
    });
    body = body.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    const groupPrompt = isSmartGroupNode(node) ? textForNode(node, ctx).trim() : '';
    const inputPrompt = inputPromptTextFor(node, ctx).trim();
    if(groupPrompt || inputPrompt) body = [groupPrompt, inputPrompt, body].filter(Boolean).join('\n\n');
    if(!body && settings.engine === 'runninghub'){
        body = rhDefaultPromptSuggestion();
    }
    const displayPrompt = originalPrompt || body;
    if(hasMentionToken && refs.length){
        const mapText = refs.map((img, i) => `图${i + 1}：${img.name || `图片${i + 1}`}`).join('\n');
        return {
            prompt:`${tr('smart.refMapHeader')}\n${mapText}\n\n${tr('smart.refUserNeed')}\n${body}`,
            displayPrompt,
            refs:refs.map((img, index) => ({url:img.url, name:img.name || `图${index + 1}`, kind:img.kind || mediaKindForItem(img), asset_uris:img.asset_uris || {}, role:`image_${index + 1}`})),
            mentioned:true
        };
    }
    return {
        prompt:body,
        displayPrompt,
        refs:refs.map((img, index) => ({url:img.url, name:img.name || `图${index + 1}`, kind:img.kind || mediaKindForItem(img), asset_uris:img.asset_uris || {}, role:`image_${index + 1}`})),
        mentioned:false
    };
}

// Local ComfyUI task transport and workflow parameter serialization were removed.

// Local ComfyUI generation URL construction was removed.

async function runGeneration(){
    const node = selectedNode();
    const request = buildPromptRequest(node, null, true, smartLoopContext);
    const prompt = request.prompt.trim();
    if(!node) return;
    if(smartNodeInFlight(node) && !smartNodeAllowsOverlappingRun(node)) return;
    const refs = request.refs;
    const previousSettings = cloneSmartSettings(settings);
    const runSettings = smartSettingsForNode(node);
    settings = {...settings, ...cloneSmartSettings(runSettings || {})};
    if(!prompt && smartRunNeedsPrompt(settings)){
        settings = previousSettings;
        toast(tr('smart.toastNeedPrompt'));
        return;
    }
    const outpaintSize = validOutpaintSize(node);
    if(outpaintSize && isApiLikeEngine(settings.engine) && settings.apiKind !== 'video'){
        // 扩图时只设置比例，不设置自定义分辨率（保留用户选择的 4K/2K/1K）
        settings = {
            ...settings,
            ratio:'custom',
            customRatio:outpaintSize.aspectRatio
        };
    }
    const meta = snapshotRunMeta(prompt, node.id, request.displayPrompt, refs);
    const logKind = isApiLikeEngine(settings.engine) && settings.apiKind === 'video' ? 'video' : 'image';
    const runLog = smartRunSnapshot(node, prompt, refs, logKind);
    rememberRecentSmartSettings(settings, node);
    const runLogStart = nowMs();
    const expectedCount = settings.engine === 'runninghub'
        ? 1
        : Math.max(1, Math.min(8, Number(settings.count || 1)));
    const apiConcurrentRun = isApiLikeEngine(settings.engine) || settings.engine === 'runninghub' || settings.engine === 'modelscope';
    const nodeHasImages = isSmartGroupNode(node) ? imagesForNode(node).some(img => img?.url) : (node.images || []).some(img => img?.url);
    const workflowModeRun = smartImageUsesWorkflowInput(node, smartLoopContext);
    const groupRun = isSmartGroupNode(node);
    const shouldCreateBranchOutput = groupRun;
    const sourceVisualState = shouldCreateBranchOutput && isSmartImageNode(node) && nodeHasImages && !workflowModeRun ? {
        images:(node.images || []).map(img => ({...img})),
        title:node.title,
        w:node.w,
        h:node.h,
        scale:node.scale,
        outputKind:node.outputKind
    } : null;
    pushUndo();
    let extracted = null;
    let branchNode = null;
    const pendingMeta = shouldCreateBranchOutput ? stripRunInputMeta(meta) : meta;
    undoSuppressed = true;
    if(shouldCreateBranchOutput) branchNode = createPendingOutputFromSource(node, expectedCount, pendingMeta, {connectSource:false, selectOutput:true, refs});
    undoSuppressed = false;
    const pendingNode = branchNode || node;
    const priorPendingTasks = smartPendingTasks(pendingNode);
    const pendingHistoryId = apiConcurrentRun && logKind === 'image'
        ? beginSmartPendingHistoryLog(pendingNode, {
            prompt,
            kind:logKind,
            providerId:runSettings.providerId || runSettings.provider_id || '',
            model:runSettings.model || '',
            runSettings,
            forceNew:true
        })
        : '';
    if(pendingHistoryId) runLog.pendingHistoryId = pendingHistoryId;
    if(extracted) pendingNode._runMetaTargetId = extracted.id;
    if(!branchNode){
        // 新一轮生成开始：框内已有上一轮残留图片（且没有仍在飞行的任务）时，
        // 先把旧图归档到候选池再清空，按本轮真实并发数(1-8)重建占位框。
        // 否则旧图会占用占位格、写回的新图只能追加在旧图之后，8 并发时后半截图
        // 写不进去、节点永远停在“生成中”。
        const leftoverImages = (pendingNode.images || []).filter(img => img?.url);
        if(leftoverImages.length && !priorPendingTasks.length){
            const history = ensureHistoryGroupForNode(pendingNode);
            history.images = cleanHistoryImages([...leftoverImages.map(img => ({...img})), ...(history.images || [])]);
            history.title = tr('smart.historyGroupTitle');
            history.outputKind = mediaKindForUrls(history.images || [], 'image');
            history.scale = MEDIA_GROUP_DEFAULT_SCALE;
            delete history.w;
            delete history.h;
            pendingNode.images = [];
            pendingNode.failedGenerationTasks = [];
            pendingNode.recoveredGenerationTasks = [];
            canvasState.selectedImage = {nodeId:'', index:-1};
        }
        const priorPendingCount = priorPendingTasks.reduce((total, task) => total + Math.max(1, Number(task?.count || 1)), 0);
        pendingNode.pending = Math.max(1, priorPendingCount + Math.max(1, Number(expectedCount) || 1));
        // 固化本批占位格总数：写回/完成阶段布局都以它为准，避免 node.pending 递减导致尺寸跳变
        pendingNode._batchTotal = pendingNode.pending;
        if(!priorPendingTasks.length) pendingNode._generationBatchApplied = false;
        pendingNode.runStartedAt = Number(pendingNode.runStartedAt || 0) || nowMs();
        delete pendingNode.runFinishedAt;
        delete pendingNode.runElapsedMs;
        pendingNode.runTimerHidden = false;
        // 提交生成时就固化目标比例，pendingBoxSize 使用它计算占位图尺寸
        // 扩图激活时，使用扩图的比例（sizeForRun 返回空字符串）
        const outpaintForAspect = validOutpaintSize(node);
        const aspectForRun = outpaintForAspect 
            ? {w: outpaintForAspect.width, h: outpaintForAspect.height}
            : parseSizeAspect(String(sizeForRun(settings) || ''));
        pendingNode._generationAspect = aspectForRun || parseSizeAspect('1:1');
        const pendingBox = pendingBoxSize(pendingNode.pending, {node:pendingNode, sourceNode:node, refs});
        pendingNode.w = pendingBox.w;
        pendingNode.h = pendingBox.h;
        attachRunMeta(pendingNode, pendingMeta);
    }
    if(apiConcurrentRun){
        coolNodeRunningState(pendingNode, 2000);
        syncRunButtonState();
    } else {
        pendingNode.running = true;
        syncRunButtonState();
    }
    render();
    try {
        if(isApiLikeEngine(settings.engine) && settings.apiKind === 'video'){
            const videoResult = await runApiVideoGeneration(prompt, refs, settings, pendingNode);
            if(Array.isArray(videoResult?.taskIds)){
                const taskIds = videoResult.taskIds.filter(Boolean);
                if(!taskIds.length) throw new Error(tr('smart.errRunFailed'));
                const newTasks = taskIds.map((taskId, index) => ({taskId, kind:'video', count:videoResult.count || 1, providerId:videoResult.providerId, model:videoResult.model, pendingHistoryId:index === 0 ? pendingHistoryId : ''}));
                pendingNode.pendingTasks = [...smartPendingTasks(pendingNode).filter(task => !taskIds.includes(task.taskId)), ...newTasks];
                ensureSmartPendingHistoryLogs(pendingNode, newTasks, {prompt, kind:'video'});
                pendingNode.pending = Math.max(pendingNode.pendingTasks.length, pendingNode.pendingTasks.reduce((total, task) => total + Math.max(1, Number(task.count || 1)), 0));
                pendingNode.runTimerHidden = false;
                pendingNode.running = false;
                render();
                if(!await acceptProjectGenerationRegistration(videoResult)){
                    scheduleSave();
                    await saveCanvas();
                }
                await resumeSmartPendingNode(pendingNode, {run:runLog, runLogStart, taskIds});
                const completedVideoNode = liveNode(pendingNode);
                if(completedVideoNode.jimengPending || smartRecoverableImageTask(completedVideoNode)){
                    if(sourceVisualState) restoreSourceVisualState(node, sourceVisualState);
                    clearPromptInput({preserveDraft:true});
                    settings = previousSettings;
                    scheduleSave();
                    return;
                }
                if(!(completedVideoNode.images || []).length) throw new Error(tr('smart.errNoOutVideos'));
                if(sourceVisualState) restoreSourceVisualState(node, sourceVisualState);
                if(!completedVideoNode._serverGenerationHistoryRecorded) addSmartGenerationHistory({run:runLog, outputs:completedVideoNode.images || [], runMs:nowMs() - runLogStart});
                delete completedVideoNode._serverGenerationHistoryRecorded;
                clearPromptInput({preserveDraft:true});
                settings = previousSettings;
                scheduleSave();
                return;
            }
            const outVideos = videoResult;
            if(!outVideos.length) throw new Error(tr('smart.errNoOutVideos'));
            finalizePendingNode(pendingNode, outVideos, pendingMeta, 'video');
            if(sourceVisualState) restoreSourceVisualState(node, sourceVisualState);
            addSmartGenerationHistory({run:runLog, outputs:outVideos, runMs:nowMs() - runLogStart});
            clearPromptInput({preserveDraft:true});
            settings = previousSettings;
            scheduleSave();
            return;
        }
        const rhModelMode = settings.engine === 'runninghub' && Boolean(runningHubSelectedModel(settings));
        const outImages = rhModelMode
            ? await runApiGeneration(prompt, refs, runningHubModelApiSettings(settings), pendingNode, {pendingHistoryId})
            : settings.engine === 'runninghub'
                ? await runRunningHubGeneration(prompt, refs)
                : settings.engine === 'modelscope'
                ? await runApiGeneration(prompt, refs, {
                    ...settings,
                    engine:'api',
                    apiKind:'image',
                    routingMode:'manual',
                    provider_id:'modelscope',
                    model:settings.msCustomModel || modelscopeImageModels()[0] || '',
                    ratio:settings.msRatio || 'square',
                    resolution:settings.msResolution || '1k',
                    customRatio:settings.msCustomRatio || '',
                    customSize:settings.msCustomSize || ''
                }, pendingNode, {pendingHistoryId})
                : await runApiGeneration(prompt, refs, settings, pendingNode, {pendingHistoryId, overlapping:priorPendingTasks.length > 0});
        if(isApiLikeEngine(settings.engine) || rhModelMode){
            const taskIds = Array.isArray(outImages?.taskIds) ? outImages.taskIds : [];
            if(!taskIds.length) throw new Error(tr('smart.errRunFailed'));
            const newTasks = taskIds.map((taskId, index) => ({taskId, kind:'image', count:outImages.count || 1, providerId:outImages.providerId, model:outImages.model, prompt, size:sizeForRun(settings) || '', createdAt:runLogStart, pendingHistoryId:index === 0 ? pendingHistoryId : ''}));
            pendingNode.pendingTasks = [...smartPendingTasks(pendingNode).filter(task => !taskIds.includes(task.taskId)), ...newTasks];
            // 提交时就固化目标比例：占位/逐张写入/完成三个阶段共用同一个格子比例，
            // 任何阶段都不再按真实图重算，彻底消除「上传方形、写入跳回」的跳变。
            // 扩图激活时，使用扩图的比例
            const outpaintForWriteback = validOutpaintSize(node);
            pendingNode._generationAspect = outpaintForWriteback 
                ? {w: outpaintForWriteback.width, h: outpaintForWriteback.height}
                : parseSizeAspect(String(sizeForRun(settings) || '')) || parseSizeAspect('1:1');
            // 节点尺寸已在上方 pendingBoxSize 处固化，这里不再重复设置
            ensureSmartPendingHistoryLogs(pendingNode, newTasks, {prompt, kind:'image'});
            pendingNode.pending = Math.max(pendingNode.pendingTasks.length, pendingNode.pendingTasks.reduce((total, task) => total + Math.max(1, Number(task.count || 1)), 0));
            pendingNode.runTimerHidden = false;
            pendingNode.running = false;
            render();
            if(!await acceptProjectGenerationRegistration(outImages)){
                scheduleSave();
                await saveCanvas();
            }
            await resumeSmartPendingNode(pendingNode, {run:runLog, runLogStart, taskIds});
            const completedImageNode = liveNode(pendingNode);
            if(completedImageNode.jimengPending || smartRecoverableImageTask(completedImageNode)){
                if(sourceVisualState) restoreSourceVisualState(node, sourceVisualState);
                clearPromptInput({preserveDraft:true});
                settings = previousSettings;
                scheduleSave();
                return;
            }
            if(!(completedImageNode.images || []).length) throw new Error(tr('smart.errNoOutImages'));
            if(outpaintSize) delete node.outpaintSize;
            if(sourceVisualState) restoreSourceVisualState(node, sourceVisualState);
            if(!completedImageNode._serverGenerationHistoryRecorded) addSmartGenerationHistory({run:runLog, outputs:completedImageNode.images || [], runMs:nowMs() - runLogStart});
            delete completedImageNode._serverGenerationHistoryRecorded;
            clearPromptInput({preserveDraft:true});
            settings = previousSettings;
            scheduleSave();
            return;
        }
        if(!outImages.length) throw new Error(tr('smart.errNoOutImages'));
        if(outpaintSize) delete node.outpaintSize;
        finalizePendingNode(pendingNode, outImages, pendingMeta);
        if(sourceVisualState) restoreSourceVisualState(node, sourceVisualState);
        addSmartGenerationHistory({run:runLog, outputs:outImages, runMs:nowMs() - runLogStart});
        clearPromptInput({preserveDraft:true});
        settings = previousSettings;
        scheduleSave();
    } catch(e) {
        settings = previousSettings;
        if(e?.smartSubmissionUnknown){
            pendingNode.running = false;
            pendingNode.pending = Math.max(1, Number(pendingNode.pending || 0), smartGenerationSubmissions(pendingNode).length);
            scheduleSave();
            toast((e.message || tr('smart.submitUnknownRecover')).slice(0, 160));
            return;
        }
        if(handleJimengPendingSignal(pendingNode, e)){
            if(sourceVisualState) restoreSourceVisualState(node, sourceVisualState);
            delete pendingNode._runMetaTargetId;
            clearPromptInput({preserveDraft:true});
            return;
        }
        const remainingTasks = smartPendingTasks(pendingNode);
        pendingNode.pending = remainingTasks.length
            ? Math.max(remainingTasks.length, remainingTasks.reduce((total, task) => total + Math.max(1, Number(task.count || 1)), 0))
            : 0;
        if(branchNode){
            canvasState.nodes = canvasState.nodes.filter(n => n.id !== branchNode.id); if(canvas) canvas.nodes = canvasState.nodes;
            canvas.connections = (canvas.connections || []).filter(c => c.from !== branchNode.id && c.to !== branchNode.id);
            canvasState.selectedId = node.id;
        } else {
            pendingNode.pending = remainingTasks.length ? pendingNode.pending : 0;
            pendingNode.running = false;
            if(!(pendingNode.images || []).length){
                delete pendingNode.w;
                delete pendingNode.h;
            }
        }
        if(extracted) restoreFromExtraction(node, extracted);
        delete pendingNode._runMetaTargetId;
        if(!e?.smartGenerationLogged) addSmartGenerationHistory({run:runLog, outputs:[], runMs:nowMs() - runLogStart, error:e.message || String(e)});
        toast((e.message || tr('smart.errRunFailed')).slice(0, 160));
    } finally {
        if(!apiConcurrentRun){
            clearNodeRunningState(pendingNode);
            syncRunButtonState();
        }
        render();
    }
}
function sleep(ms){ return new Promise(resolve => setTimeout(resolve, ms)); }
// Local ComfyUI generation execution was removed.

function updateSelectionBox(event){
    if(!selectionState) return;
    const sx = selectionState.startScreen.x, sy = selectionState.startScreen.y;
    const x = Math.min(sx, event.clientX), y = Math.min(sy, event.clientY);
    selectionBox.style.display = 'block';
    selectionBox.style.left = `${x}px`;
    selectionBox.style.top = `${y}px`;
    selectionBox.style.width = `${Math.abs(event.clientX - sx)}px`;
    selectionBox.style.height = `${Math.abs(event.clientY - sy)}px`;
}
function finishSelection(event){
    if(!selectionState) return;
    const a = selectionState.startWorld;
    const b = screenToWorld(event);
    const minX = Math.min(a.x, b.x), minY = Math.min(a.y, b.y);
    const maxX = Math.max(a.x, b.x), maxY = Math.max(a.y, b.y);
    canvasState.selectedIds = canvasState.nodes.filter(node => {
        const r = nodeRect(node);
        return r.x < maxX && r.x + r.width > minX && r.y < maxY && r.y + r.height > minY;
    }).map(n => n.id);
    canvasState.selectedId = canvasState.selectedIds.length === 1 ? canvasState.selectedIds[0] : '';
    canvasState.selectedImage = {nodeId:'', index:-1};
    selectionState = null;
    selectionJustFinished = true;
    selectionBox.style.display = 'none';
    render();
    setTimeout(() => { selectionJustFinished = false; }, 0);
}
function groupSelectedNodes(){
    const ids = canvasState.selectedIds.length ? canvasState.selectedIds.slice() : (canvasState.selectedId ? [canvasState.selectedId] : []);
    const selected = ids.map(id => canvasState.nodes.find(n => n.id === id)).filter(n => n && !isSmartGroupNode(n));
    if(selected.length < 1){ toast(tr('smart.groupSelectRequired')); return; }
    // #148: 过滤掉不能进组的节点类型（agent/conversation/local-edit/agent-task），并 toast 提示。
    const groupable = selected.filter(n => isSmartGroupableNode(n));
    const rejected = selected.filter(n => !isSmartGroupableNode(n));
    if(rejected.length > 0){ toast(tr('smart.groupUnsupportedType')); }
    if(groupable.length < 1){ return; }
    pushUndo();
    const rects = groupable.map(nodeRect);
    const minX = Math.min(...rects.map(r => r.x));
    const minY = Math.min(...rects.map(r => r.y));
    const maxX = Math.max(...rects.map(r => r.x + r.width));
    const maxY = Math.max(...rects.map(r => r.y + r.height));
    const group = {
        id:uid('group'),
        type:'smart-group',
        x:Math.round(minX - 18),
        y:Math.round(minY - 44),
        w:Math.max(340, Math.round(maxX - minX + 36)),
        h:Math.max(220, Math.round(maxY - minY + 72)),
        title:'智能分组',
        items:[],
        images:[],
        created_at:Date.now()
    };
    canvasState.nodes.push(group);
    // 图片成员吸收进分组网格，提示词/循环作为成员节点；随后自动整理。
    groupable.forEach(node => addNodeToSmartGroup(group, node));
    arrangeSmartGroupMembers(group, {skipUndo:true});
    canvasState.selectedIds = [];
    canvasState.selectedId = group.id;
    canvasState.selectedImage = {nodeId:'', index:-1};
    render();
    scheduleSave();
}
function ungroupNode(groupId){
    const group = canvasState.nodes.find(n => n.id === groupId);
    if(!group) return false;
    // 释放智能分组：把收进卡片的图片重新拆成独立图片节点（平铺在分组原位置），提示词/循环成员原地保留，再删除分组容器。
    if(isSmartGroupNode(group)){
        pushUndo();
        const memberIds = smartGroupMembers(group).map(m => m.id);
        const groupImages = (group.images || []).filter(img => img?.url);
        let created = [];
        if(groupImages.length){
            const pad = SMART_GROUP_LAYOUT.PADDING;
            const gap = SINGLE_IMAGE_LAYOUT.UNGROUP_GAP;
            const count = groupImages.length;
            // 列数与批量网格规范一致（getBatchThumbGridSize 单一数据源）
            const cols = getBatchThumbGridSize(count).cols;
            // 解组后每张图片恢复原比例，参考宽度 136px
            const sizes = groupImages.map(img => {
                // 从图片数据解析比例（mediaLayoutSize 读 natural_w/width/w/layout_w/preview_w）
                const layoutSize = mediaLayoutSize(img);
                const aspect = layoutSize.width > 0 && layoutSize.height > 0
                    ? {w:Math.max(1, Math.round(layoutSize.width)), h:Math.max(1, Math.round(layoutSize.height))}
                    : BATCH_THUMB.ASPECT;
                return getUngroupedImageSize(aspect);
            });
            // 每列最大宽 / 每行最大高作为步长，混比例图片也能整齐对齐
            const colWidths = new Array(cols).fill(0);
            const rowsCount = Math.ceil(count / cols);
            const rowHeights = new Array(rowsCount).fill(0);
            sizes.forEach((size, index) => {
                const col = index % cols;
                const row = Math.floor(index / cols);
                colWidths[col] = Math.max(colWidths[col], size.w);
                rowHeights[row] = Math.max(rowHeights[row], size.h);
            });
            created = groupImages.map((img, index) => {
                const col = index % cols;
                const row = Math.floor(index / cols);
                const size = sizes[index];
                const x = Math.round(Number(group.x || 0) + pad + colWidths.slice(0, col).reduce((sum, w) => sum + w + gap, 0));
                const y = Math.round(Number(group.y || 0) + pad + rowHeights.slice(0, row).reduce((sum, h) => sum + h + gap, 0));
                const node = {id:uid('smart'), type:'smart-image', x, y, w:size.w, h:size.h, title:'Image', images:[stripImageGenerationMeta({...img})], scale:MEDIA_NODE_DEFAULT_SCALE, created_at:Date.now()};
                inheritNodeMetaFromImage(node);
                clearDetachedRunInputRefs(node);
                return node;
            });
        }
        canvasState.nodes = canvasState.nodes.filter(n => n.id !== groupId); if(canvas) canvas.nodes = canvasState.nodes;
        canvasState.nodes.push(...created);
        if(canvas) canvas.connections = (canvas.connections || []).filter(c => c.from !== groupId && c.to !== groupId);
        canvasState.nodes.forEach(node => {
            if(Array.isArray(node.inputNodeIds)) node.inputNodeIds = node.inputNodeIds.filter(id => id !== groupId);
            if(isSmartGroupNode(node) && Array.isArray(node.items)) node.items = node.items.filter(id => id !== groupId);
        });
        canvasState.selectedIds = [...created.map(n => n.id), ...memberIds].filter(id => canvasState.nodes.some(n => n.id === id));
        canvasState.selectedId = canvasState.selectedIds.length === 1 ? canvasState.selectedIds[0] : '';
        canvasState.selectedImage = {nodeId:'', index:-1};
        render();
        scheduleSave();
        return true;
    }
    if(!Array.isArray(group.images) || group.images.length < 2) return false;
    pushUndo();
    const layout = imageLayout(group.images || [], nodeScale(group), group);
    const pad = 16;
    const gap = 8;
    const cell = Math.max(28, Math.round(layout.thumb || 96));
    const created = (group.images || []).map((img, index) => {
        const col = index % Math.max(1, layout.cols || 1);
        const row = Math.floor(index / Math.max(1, layout.cols || 1));
        const size = thumbDisplaySize(img, cell);
        const x = Math.round(Number(group.x || 0) + pad + col * (cell + gap) + Math.max(0, (cell - size.width) / 2));
        const y = Math.round(Number(group.y || 0) + pad + row * (cell + gap) + Math.max(0, (cell - size.height) / 2));
        const node = {
            id:uid('smart'),
            type:'smart-image',
            x,
            y,
            w:size.width,
            h:size.height,
            title:'Image',
            images:[stripImageGenerationMeta({...img})],
            scale:MEDIA_NODE_DEFAULT_SCALE,
            created_at:Date.now()
        };
        inheritNodeMetaFromImage(node);
        clearDetachedRunInputRefs(node);
        return node;
    });
    canvasState.nodes = canvasState.nodes.filter(n => n.id !== groupId); if(canvas) canvas.nodes = canvasState.nodes;
    canvasState.nodes.push(...created);
    if(canvas) canvas.connections = (canvas.connections || []).filter(c => c.from !== groupId && c.to !== groupId);
    canvasState.nodes.forEach(node => {
        if(Array.isArray(node.inputNodeIds)){
            node.inputNodeIds = node.inputNodeIds.filter(inputId => inputId !== groupId);
        }
    });
    canvasState.selectedIds = created.map(node => node.id);
    canvasState.selectedId = canvasState.selectedIds.length === 1 ? canvasState.selectedIds[0] : '';
    canvasState.selectedImage = {nodeId:'', index:-1};
    render();
    scheduleSave();
    return true;
}
function mergeImageNodesIntoGroup(sourceId, targetId){
    const source = canvasState.nodes.find(n => n.id === sourceId);
    const target = canvasState.nodes.find(n => n.id === targetId);
    if(!source || !target || source.id === target.id) return false;
    if(!(source.images || []).length || !(target.images || []).length) return false;
    const sourceImages = (source.images || []).map(img => stripImageGenerationMeta({...img}));
    target.images = [...(target.images || []).map(img => stripImageGenerationMeta(img)), ...sourceImages];
    target.title = 'Group';
    if(!Number.isFinite(Number(target.scale)) || Number(target.scale) === MEDIA_NODE_DEFAULT_SCALE) target.scale = MEDIA_GROUP_DEFAULT_SCALE;
    delete target.w;
    delete target.h;
    canvas.connections = (canvas.connections || []).map(c => {
        if(c.from === source.id) return {...c, from:target.id};
        if(c.to === source.id) return {...c, to:target.id};
        return c;
    }).filter((c, index, arr) => c.from !== c.to && arr.findIndex(x => x.from === c.from && x.to === c.to && (x.kind || 'flow') === (c.kind || 'flow')) === index);
    canvasState.nodes.forEach(node => {
        if(Array.isArray(node.inputNodeIds)){
            node.inputNodeIds = Array.from(new Set(node.inputNodeIds.map(id => id === source.id ? target.id : id).filter(id => id !== node.id)));
        }
    });
    canvasState.nodes = canvasState.nodes.filter(n => n.id !== source.id); if(canvas) canvas.nodes = canvasState.nodes;
    canvasState.selectedIds = [];
    canvasState.selectedId = target.id;
    canvasState.selectedImage = {nodeId:'', index:-1};
    return true;
}
function smartGroupTargetForDraggedNode(draggedNode){
    // 允许把一个分组拖进另一个分组（拖来的分组的成员会作为输入并入目标分组）。自身/被拖分组及其成员已在 excluded 中排除。
    if(!draggedNode) return null;
    const r = nodeRect(draggedNode);
    const excluded = new Set([draggedNode.id, ...(dragState?.groupIds || [])]);
    const cx = r.x + r.width / 2;
    const cy = r.y + r.height / 2;
    const groups = canvasState.nodes
        .filter(node => isSmartGroupNode(node) && !excluded.has(node.id))
        .map(group => ({group, rect:nodeRect(group)}))
        .filter(item => cx >= item.rect.x && cx <= item.rect.x + item.rect.width && cy >= item.rect.y && cy <= item.rect.y + item.rect.height);
    if(!groups.length) return null;
    groups.sort((a, b) => (canvasState.nodes.indexOf(b.group) - canvasState.nodes.indexOf(a.group)));
    return groups[0].group;
}
// 把一个或多个被拖动的节点批量加入目标分组（支持多选拖入）。入组后只整理一次并选中目标分组。
function addDraggedNodesToSmartGroup(draggedNodes, group){
    if(!group || !isSmartGroupNode(group)) return false;
    const list = (draggedNodes || []).filter(n => n && n.id !== group.id);
    if(!list.length) return false;
    // #148: 过滤掉不能进组的节点类型（agent/conversation/local-edit/agent-task），并 toast 提示。
    const groupable = list.filter(n => isSmartGroupableNode(n));
    const rejected = list.filter(n => !isSmartGroupableNode(n));
    if(rejected.length > 0){ toast(tr('smart.groupUnsupportedType')); }
    if(!groupable.length) return false;
    let added = false;
    groupable.forEach(n => {
        if(addNodeToSmartGroup(group, n)) added = true;
    });
    if(!added) return false;
    // 提示词/循环成员入组后自动整理成网格（图片已收进卡片网格，自动平铺）。
    arrangeSmartGroupMembers(group, {skipUndo:true});
    canvasState.selectedIds = [];
    // 图片被吸收进分组（原节点已删除），统一选中目标分组；仅当单个提示词/循环节点拖入时保持选中它。
    const survivingSingle = list.length === 1 && canvasState.nodes.some(n => n.id === list[0].id) ? list[0].id : '';
    canvasState.selectedId = survivingSingle || group.id;
    canvasState.selectedImage = {nodeId:'', index:-1};
    return true;
}
function closeCreateMenu(){
    createMenu?.classList.remove('open');
    canvasState.createMenuGroupId = '';
}
function openCreateMenu(event, options={}){
    if(!createMenu) return;
    createMenuPoint = screenToWorld(event);
    canvasState.createMenuGroupId = options.groupId || '';
    createMenu.style.left = '14px';
    createMenu.style.top = '14px';
    createMenu.classList.add('open');
    const menuRect = createMenu.getBoundingClientRect();
    const w = Math.max(1, menuRect.width);
    const h = Math.max(1, menuRect.height);
    const left = Math.max(14, Math.min(window.innerWidth - w - 14, event.clientX + 8));
    const top = Math.max(14, Math.min(window.innerHeight - h - 14, event.clientY + 8));
    createMenu.style.left = `${left}px`;
    createMenu.style.top = `${top}px`;
    refreshIcons();
}
function addCreatedNodeToMenuGroup(node){
    const group = canvasState.createMenuGroupId ? canvasState.nodes.find(n => n.id === canvasState.createMenuGroupId) : null;
    if(addNodeToSmartGroup(group, node)){
        // 通过分组小菜单新建的节点入组后自动整理（节点创建已压过 undo，这里不再重复）。
        arrangeSmartGroupMembers(group, {skipUndo:true});
        render();
        scheduleSave();
    }
}
shell.addEventListener('mousedown', e => {
    if(!zoomPreviewState) return;
    if(e.button !== 0) return;
    if(e.target.closest('.composer,.smart-settings-modal,.smart-settings,.smart-back,.asset-panel,.asset-toggle,.smart-log-toggle,.smart-shortcut-toggle,.log-modal,.shortcut-modal,.image-edit-modal,.create-menu,.smart-minimap')) return;
    e.preventDefault();
    e.stopPropagation();
}, true);
shell.addEventListener('click', e => {
    if(!zoomPreviewState) return;
    if(e.button !== 0) return;
    if(e.target.closest('.composer,.smart-settings-modal,.smart-settings,.smart-back,.asset-panel,.asset-toggle,.smart-log-toggle,.smart-shortcut-toggle,.log-modal,.shortcut-modal,.image-edit-modal,.create-menu,.smart-minimap')) return;
    e.preventDefault();
    e.stopPropagation();
    const nodeEl = e.target.closest('.image-node');
    if(nodeEl?.dataset?.id) exitZoomPreviewToNode(nodeEl.dataset.id);
    else exitZoomPreview(screenToWorld(e));
}, true);
shell.onmousedown = e => {
    if(zoomPreviewState && e.button === 0 && !e.target.closest('.composer,.smart-settings-modal,.smart-settings,.smart-back,.asset-panel,.asset-toggle,.smart-log-toggle,.smart-shortcut-toggle,.log-modal,.shortcut-modal,.image-edit-modal,.create-menu,.smart-minimap')) return;
    if(e.target.closest('.image-node,.composer,.smart-settings-modal,.smart-settings,.smart-back,.asset-panel,.asset-toggle,.smart-log-toggle,.smart-shortcut-toggle,.log-modal,.shortcut-modal,.create-menu,.smart-minimap')) return;
    closeCreateMenu();
    if(e.button === 0 && e.shiftKey){
        e.preventDefault();
        didPan = false;
        connectionEraseState = {started:false, count:0, indices:new Set(), lastX:e.clientX, lastY:e.clientY, trail:[]};
        shell.classList.add('connection-erasing');
        updateConnectionEraseTrail(e);
        eraseConnectionsAtPoint(e);
        return;
    }
    if(e.button === 0 && isRKeyDown){
        e.preventDefault();
        didPan = false;
        shell.tabIndex = -1;
        shell.focus({preventScroll:true});
        selectionState = {startScreen:{x:e.clientX, y:e.clientY}, startWorld:screenToWorld(e)};
        updateSelectionBox(e);
        return;
    }
    if(e.button === 0 && (e.ctrlKey || e.metaKey)){
        e.preventDefault();
        didPan = false;
        shell.tabIndex = -1;
        shell.focus({preventScroll:true});
        selectionState = {startScreen:{x:e.clientX, y:e.clientY}, startWorld:screenToWorld(e)};
        updateSelectionBox(e);
        return;
    }
    if(e.button !== 0 && e.button !== 1) return;
    e.preventDefault();
    didPan = false;
    panState = {button:e.button, startX:e.clientX, startY:e.clientY, ox:viewport.x, oy:viewport.y};
    shell.classList.add('panning');
};
shell.oncontextmenu = e => {
    if((e.ctrlKey || e.metaKey) || isRKeyDown){
        e.preventDefault();
        e.stopPropagation();
        return;
    }
    if(didPan || e.target.closest('.composer,.smart-settings-modal,.smart-settings,.smart-back,.asset-panel,.asset-toggle,.smart-log-toggle,.smart-shortcut-toggle,.log-modal,.shortcut-modal,.image-edit-modal,.create-menu,.smart-minimap')) return;
    if(document.getElementById('imageEditModal')?.classList.contains('open')) return;
    e.preventDefault();
    e.stopPropagation();
    const groupEl = e.target.closest('.image-node.smart-group-node');
    if(groupEl?.dataset?.id){
        canvasState.selectedId = groupEl.dataset.id;
        canvasState.selectedIds = [];
        canvasState.selectedImage = {nodeId:'', index:-1};
        openCreateMenu(e, {groupId:groupEl.dataset.id});
        return;
    }
    if(e.target.closest('.image-node')) return;
    openCreateMenu(e);
};
shell.ondblclick = e => {
    if(didPan || e.target.closest('.image-node,.composer,.smart-settings-modal,.smart-settings,.smart-back,.asset-panel,.asset-toggle,.smart-log-toggle,.smart-shortcut-toggle,.log-modal,.shortcut-modal,.image-edit-modal,.create-menu')) return;
    if(document.getElementById('imageEditModal')?.classList.contains('open')) return;
    e.preventDefault();
    openCreateMenu(e);
};
shell.onclick = e => {
    if(selectionJustFinished) return;
    if(didPan || e.target.closest('.image-node,.composer,.smart-settings-modal,.smart-settings,.smart-back,.asset-panel,.asset-toggle,.smart-log-toggle,.smart-shortcut-toggle,.log-modal,.shortcut-modal,.image-edit-modal,.create-menu')) return;
    if(document.getElementById('imageEditModal')?.classList.contains('open')) return;
    closeCreateMenu();
    clearSelection();
    render();
};
minimap?.addEventListener('mousedown', e => {
    if(e.button !== 0) return;
    if(e.target.closest?.('#smartArrangeBtn')) return;
    e.preventDefault();
    e.stopPropagation();
    smartMinimapDrag = true;
    centerViewportOnWorldPoint(minimapEventToWorld(e));
});
smartArrangeBtn?.addEventListener('mousedown', e => e.stopPropagation());
smartArrangeBtn?.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    arrangeSelectedSmartNodes();
});

// Excel 导出：选中节点输出媒体 → zip 下载（issue #53）
async function exportSelectedSmartNodes(){
    const ids = selectedNodeIds();
    if(!ids || !ids.length){
        toast(tr('smart.exportNoSelection'));
        return;
    }
    const projectId = (typeof canvas !== 'undefined' && (canvas?.project_id || canvas?.project)) || sourceProjectId || canvasId || '';
    if(!projectId){
        toast(tr('smart.exportNoProject'));
        return;
    }
    try{
        const res = await fetch('/api/excel/export-zip', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({project_id: projectId, node_ids: ids}),
        });
        if(!res.ok){
            let detail = res.status;
            try{
                const j = await res.json();
                detail = j?.detail || j?.message || detail;
            }catch(e){}
            toast(`${tr('smart.exportFailed')}: ${detail}`);
            return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `excel_export_${Date.now()}.zip`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 4000);
    }catch(err){
        toast(`${tr('smart.exportFailed')}: ${err?.message || err}`);
    }
}
const smartExcelExportBtn = document.getElementById('smartExcelExportBtn');
smartExcelExportBtn?.addEventListener('mousedown', e => e.stopPropagation());
smartExcelExportBtn?.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    exportSelectedSmartNodes();
});
// #53/#58：星标 → 收集/导出。已标记图片面板内的「导出星标节点」：
// 目录留空 = 浏览器 zip 下载；填服务器目录（项目库根之下）= 按任务 ID 命名写入服务器目录。
function markedNodeIds(){
    const ids = getMarkedAssetIds();
    if(!ids.length) return [];
    const out = [];
    canvasState.nodes.forEach(n => {
        if((n.images || []).some(img => img && ids.includes(String(img.asset_id || '')))) out.push(String(n.id));
    });
    return out;
}
function downloadBlobAsZip(blob, filename){
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
}
function renderMarkedExportFiles(files, dir){
    const el = document.getElementById('smartMarkedExportFiles');
    if(!el) return;
    if(!files?.length){ el.hidden = true; el.innerHTML = ''; return; }
    el.innerHTML = files.map(f => {
        const name = String(f).split(/[\\/]/).pop();
        return `<div class="smart-marked-export-file" title="${escapeAttr(f)}"><i data-lucide="check" aria-hidden="true"></i><span>${escapeHtml(name)}</span></div>`;
    }).join('');
    el.hidden = false;
    refreshIcons(el);
}
async function exportMarkedSmartNodes(){
    const projectId = (typeof canvas !== 'undefined' && (canvas?.project_id || canvas?.project)) || sourceProjectId || canvasId || '';
    if(!projectId){ toast(tr('smart.exportNoProject')); return; }
    const nodeIds = markedNodeIds();
    if(!nodeIds.length){ toast(tr('smart.exportNoMarked')); return; }
    const dir = String(document.getElementById('smartMarkedExportDir')?.value || '').trim();
    const btn = document.getElementById('smartMarkedExportBtn');
    if(btn){ btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
    try{
        const res = await fetch('/api/excel/export-zip', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(dir ? {project_id: projectId, node_ids: nodeIds, export_dir: dir} : {project_id: projectId, node_ids: nodeIds}),
        });
        if(!res.ok){
            let detail = res.status;
            try{ const j = await res.json(); detail = j?.detail || j?.message || detail; }catch(e){}
            toast(`${tr('smart.exportFailed')}: ${detail}`);
            return;
        }
        if(dir){
            const j = await res.json().catch(() => null);
            renderMarkedExportFiles(j?.files || [], dir);
            toast(trf('smart.exportDone', {n: (j?.files || []).length || 0}));
        } else {
            const blob = await res.blob();
            downloadBlobAsZip(blob, `excel_marked_${Date.now()}.zip`);
            toast(tr('smart.exportZipOk'));
        }
    }catch(err){
        toast(`${tr('smart.exportFailed')}: ${err?.message || err}`);
    }finally{
        if(btn){ btn.disabled = false; btn.removeAttribute('aria-busy'); }
    }
}
// #89：已标记图片面板（工具栏星标钮）。
document.getElementById('smartMarkedToggle')?.addEventListener('mousedown', e => e.stopPropagation());
document.getElementById('smartMarkedToggle')?.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    toggleSmartMarkedPanel();
});
document.getElementById('smartMarkedClose')?.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    toggleSmartMarkedPanel(false);
});
document.getElementById('smartMarkedDownloadAll')?.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    const items = markedImageItems();
    if(!items.length){ toast(tr('smart.markedEmpty')); return; }
    void zipDownloadImageItems('marked-images', items);
});
// #53/#58：已标记面板「导出星标节点」（目录输入 + 导出）。
document.getElementById('smartMarkedExportBtn')?.addEventListener('mousedown', e => e.stopPropagation());
document.getElementById('smartMarkedExportBtn')?.addEventListener('click', e => {
    e.preventDefault();
    e.stopPropagation();
    void exportMarkedSmartNodes();
});
window.onmousemove = e => {
    lastMouseWorld = screenToWorld(e);
    if(smartMinimapDrag){
        e.preventDefault();
        centerViewportOnWorldPoint(minimapEventToWorld(e));
        return;
    }
    if(connectionEraseState){
        e.preventDefault();
        updateConnectionEraseTrail(e);
        eraseConnectionsAlongPointer(e);
        return;
    }
    if(portDragState){
        e.preventDefault();
        const p = screenToWorld(e);
        portDragState.currentWorld = p;
        portDragState.moved = true;
        const hitEl = document.elementFromPoint(e.clientX, e.clientY);
        const portEl = hitEl?.closest?.('.node-port');
        const nodeEl = portEl?.closest?.('.image-node') || hitEl?.closest?.('.image-node');
        let targetId = '', targetPort = '';
        if(nodeEl && nodeEl.dataset.id && nodeEl.dataset.id !== portDragState.fromId){
            targetId = nodeEl.dataset.id;
            if(portEl){
                targetPort = portEl.dataset.port;
            } else {
                const rect = nodeEl.getBoundingClientRect();
                targetPort = (e.clientX - rect.left) < rect.width / 2 ? 'in' : 'out';
            }
            const compatible = (portDragState.fromPort === 'out' && targetPort === 'in') || (portDragState.fromPort === 'in' && targetPort === 'out');
            if(!compatible){ targetId = ''; targetPort = ''; }
        }
        portDragState.hoverTargetId = targetId;
        portDragState.hoverPort = targetPort;
        updatePortDragVisual();
        return;
    }
    if(promptResizeState){
        e.preventDefault();
        const dy = e.clientY - promptResizeState.startY;
        const target = clampPromptManualHeight(promptResizeState.startH + dy);
        if(promptResizeState.mode === 'expanded'){
            const naturalBase = Math.max(composerPromptView.manualHeight, promptNaturalHeight());
            composerPromptView.expandedExtra = Math.max(0, target - naturalBase);
            composerPromptView.expandedHeight = setPromptEditorHeight(Math.max(naturalBase, target));
        } else {
            settings.promptH = target;
            composerPromptView.manualHeight = target;
            setPromptEditorHeight(target);
            persistActiveSmartSettings();
        }
        const node = activeComposerNode();
        if(node) positionComposerForNode(node);
        keepComposerReachableByPanningCanvas();
        return;
    }
    if(selectionState){
        e.preventDefault();
        updateSelectionBox(e);
        return;
    }
    if(previewCompareDrag){
        e.preventDefault();
        setPreviewComparePos(e.clientX);
        return;
    }
    if(previewPanDrag){
        const stage = document.getElementById('previewStage');
        previewPan = {
            x:previewPanDrag.startX + (e.clientX - previewPanDrag.clientX),
            y:previewPanDrag.startY + (e.clientY - previewPanDrag.clientY)
        };
        stage?.classList.add('panning');
        applyPreviewTransform();
        return;
    }
    if(imageEditPanDrag){
        const stage = document.getElementById('imageEditStage');
        if(stage){
            stage.scrollLeft = imageEditPanDrag.scrollLeft - (e.clientX - imageEditPanDrag.clientX);
            stage.scrollTop = imageEditPanDrag.scrollTop - (e.clientY - imageEditPanDrag.clientY);
        }
        return;
    }
    if(cropDrag && cropState){
        const dx = e.clientX - cropDrag.sx;
        const dy = e.clientY - cropDrag.sy;
        if(cropDrag.mode === 'move'){
            cropState.x = cropDrag.start.x + dx;
            cropState.y = cropDrag.start.y + dy;
        } else if(cropDrag.mode === 'image'){
            cropState.x = cropDrag.start.x + dx;
            cropState.y = cropDrag.start.y + dy;
        } else if(String(cropDrag.mode || '').startsWith('outpaint-')){
            resizeOutpaintFromDrag(dx, dy);
        } else {
            resizeCropFromDrag(dx, dy);
        }
        clampCrop();
        renderCropBox();
        return;
    }
    if(resizeState){
        const node = canvasState.nodes.find(n => n.id === resizeState.id);
        if(!node) return;
        const dx = (e.clientX - resizeState.startX) / viewport.scale;
        const dy = (e.clientY - resizeState.startY) / viewport.scale;
        const minW = node.type === 'smart-prompt' ? 260 : node.type === 'smart-loop' ? 252 : node.type === 'smart-agent' ? 330 : node.type === 'agent-task' ? 280 : node.type === 'local-edit' ? 360 : node.type === 'smart-group' ? SMART_GROUP_MIN_WIDTH : 48;
        // 拖拽预览阶段就用内容最小高度钳制，使拖拽中即不低于 min，松手后尺寸与拖拽末态一致（不跳变）
        const minH = node.type === 'smart-prompt' ? promptNodeMinHeight(node) : node.type === 'smart-loop' ? smartLoopHeight(node) : node.type === 'smart-agent' ? 270 : node.type === 'agent-task' ? 210 : node.type === 'local-edit' ? 360 : node.type === 'smart-group' ? SMART_GROUP_MIN_HEIGHT : 48;
        if(node.type === 'smart-group' && smartGroupImageRefs(node).some(ref => ref.item?.url)){
            // 图片分组：和普通节点一样直接改 w/h，缩略图网格按新尺寸实时重排。不要走下面的“成员缩放”那套，
            // 否则拖动过程里会按成员包围盒/缩放比例收缩，松手才回到拖动宽度（用户反馈的“变宽时先缩小”）。
            node.w = Math.max(minW, Math.round(resizeState.startW + dx));
            node.h = Math.max(minH, Math.round(resizeState.startH + dy));
            if(smartGroupCompactMembers(node).length) arrangeSmartGroupMembers(node, {skipUndo:true, syncDom:true});
            updateNodeElementDuringResize(node);
            return;
        }
        if(node.type === 'smart-group'){
            // 分组当“画布中的画布”：拖手柄按宽度方向算出统一缩放比例，组内所有成员（图片+提示词）按相对手势
            // 起点的快照整体缩放+重排，然后把分组框自动收紧到成员的包围盒——盒子始终贴合内容，右侧不会留空白。
            const startZoom = resizeState.startZoom || 1;
            // 目标框宽 = 手柄拖出的框宽；缩放映射以“贴合内容的框宽”为基准，保证两个阶段都线性跟随手柄、衔接连续。
            const targetW = resizeState.startW + dx;
            const fitBase = resizeState.contentFitW || resizeState.startW || 1;
            const desiredZoom = startZoom * (targetW / fitBase);
            // 成员缩放上限 SMART_GROUP_MAX_MEMBER_ZOOM（默认 1=原始尺寸）：到上限就不再放大成员，改为让分组框继续扩大。
            const effectiveZoom = Math.max(0.2, Math.min(SMART_GROUP_MAX_MEMBER_ZOOM, desiredZoom));
            const memberRatio = effectiveZoom / startZoom;
            const capped = desiredZoom > SMART_GROUP_MAX_MEMBER_ZOOM;
            node._memberZoom = effectiveZoom;
            const gx = Number(node.x) || 0, gy = Number(node.y) || 0;
            const SMART_GROUP_PAD = 16;
            let maxRight = gx, maxBottom = gy, hasMember = false;
            (resizeState.members || []).forEach(snap => {
                const member = canvasState.nodes.find(n => n.id === snap.id);
                if(!member) return;
                hasMember = true;
                member.x = gx + (snap.sx - gx) * memberRatio;
                member.y = gy + (snap.sy - gy) * memberRatio;
                member.w = Math.max(40, Math.round(snap.sw * memberRatio));
                member.h = Math.max(40, Math.round(snap.sh * memberRatio));
                if(snap.isImage) member.scale = 1;
                maxRight = Math.max(maxRight, member.x + member.w);
                maxBottom = Math.max(maxBottom, member.y + member.h);
                const memberEl = world.querySelector(`.image-node[data-id="${CSS.escape(member.id)}"]`);
                if(memberEl){
                    memberEl.style.left = `${member.x}px`;
                    memberEl.style.top = `${member.y}px`;
                }
                updateNodeElementDuringResize(member);
            });
            if(capped || !hasMember){
                // 成员已到上限（或空分组）：分组框随手柄继续扩大，成员不再放大。
                node.w = Math.max(minW, Math.round(resizeState.startW + dx));
                node.h = Math.max(minH, Math.round(resizeState.startH + dy));
            } else {
                // 未到上限：分组框收紧到成员包围盒，贴合内容无空白。
                node.w = Math.max(minW, Math.round(maxRight - gx + SMART_GROUP_PAD));
                node.h = Math.max(minH, Math.round(maxBottom - gy + SMART_GROUP_PAD));
            }
            node.scale = 1;
            updateNodeElementDuringResize(node);
            return;
        }
        node.w = Math.max(minW, Math.round(resizeState.startW + dx));
        node.h = Math.max(minH, Math.round(resizeState.startH + dy));
        node.scale = 1;
        updateNodeElementDuringResize(node);
        return;
    }
    if(llmInstructionResizeState){
        const node = canvasState.nodes.find(n => n.id === llmInstructionResizeState.id);
        if(!node) return;
        const dy = (e.clientY - llmInstructionResizeState.startY) / viewport.scale;
        const newInstrH = Math.max(PROMPT_LLM_INSTRUCTION_MIN_H, Math.min(PROMPT_LLM_INSTRUCTION_MAX_H, Math.round(llmInstructionResizeState.startH + dy)));
        node.llmInstructionHeight = newInstrH;
        // 只把“指令框的高度变化量”叠加到节点总高度上，保留用户手动拉大的上方区域，避免上方被重置变小。
        node.h = Math.max(promptNodeExpandedHeight(node), Math.round(llmInstructionResizeState.startNodeH + (newInstrH - llmInstructionResizeState.startH)));
        node.w = Math.max(Number(node.w) || 0, 316);
        node.scale = 1;
        updateNodeElementDuringResize(node);
        const ta = world.querySelector(`.image-node[data-id="${CSS.escape(node.id)}"] .prompt-llm-instruction`);
        if(ta) ta.style.height = `${promptLlmInstructionHeight(node)}px`;
        return;
    }
    if(promptSplitResizeState){
        const node = canvasState.nodes.find(n => n.id === promptSplitResizeState.id);
        if(!node) return;
        const dy = (e.clientY - promptSplitResizeState.startY) / viewport.scale;
        // 让拖拽预览阶段就用 promptNodeMinHeight 钳制，使拖拽中即不低于 min，松手后尺寸与拖拽末态一致（不跳变）
        const minPreviewH = Math.max(PROMPT_SPLIT_PREVIEW_MIN_H, promptNodeMinHeight(node) - promptSplitResizeState.startNodeH + promptSplitResizeState.startH);
        const newPreviewH = Math.max(minPreviewH, Math.min(PROMPT_SPLIT_PREVIEW_MAX_H, Math.round(promptSplitResizeState.startH + dy)));
        node.promptSplitPreviewHeight = newPreviewH;
        node.h = Math.max(promptNodeMinHeight(node), Math.round(promptSplitResizeState.startNodeH + (newPreviewH - promptSplitResizeState.startH)));
        node.w = Math.max(Number(node.w) || 0, 316);
        node.scale = 1;
        updateNodeElementDuringResize(node);
        const list = world.querySelector(`.image-node[data-id="${CSS.escape(node.id)}"] .prompt-node-segments`);
        if(list) list.style.height = `${promptNodeSplitPreviewHeight(node)}px`;
        return;
    }
    if(thumbDragState){
        const dx = e.clientX - thumbDragState.startX;
        const dy = e.clientY - thumbDragState.startY;
        const source = canvasState.nodes.find(n => n.id === thumbDragState.nodeId);
        if(!thumbDragState.detached && Math.abs(dx) + Math.abs(dy) > 6){
            const canDetachThumb = source && (isSmartGroupNode(source) ? (source.images || []).length >= 1 : (source.images || []).length > 1);
            if(canDetachThumb){
                const img = source.images[thumbDragState.imgIndex];
                if(img){
                    commitPendingUndo();
                    undoSuppressed = true;
                    applyNodeMetaToImage(img, source);
                    source.images.splice(thumbDragState.imgIndex, 1);
                    if(isSmartGroupNode(source)){
                        arrangeSmartGroupMembers(source, {skipUndo:true, syncDom:true});
                    } else if(source.images.length <= 1){
                        source.title = 'Image';
                        delete source.w; delete source.h;
                        inheritNodeMetaFromImage(source);
                    }
                    const point = screenToWorld(e);
                    canvasState.selectedId = '';
                    canvasState.selectedImage = {nodeId:'', index:-1};
                    const newNode = createImageNodeAt(point, [img], {select:false, skipUndo:true});
                    undoSuppressed = false;
                    dragState = {id:newNode.id, startX:e.clientX, startY:e.clientY, ox:newNode.x, oy:newNode.y, thumbDetached:true};
                    thumbDragState.detached = true;
                    render();
                }
            }
        }
        if(thumbDragState.detached) thumbDragState = null;
        else return;
    }
    if(panState){
        const dx = e.clientX - panState.startX;
        const dy = e.clientY - panState.startY;
        if(Math.abs(dx) + Math.abs(dy) > 3) didPan = true;
        viewport.x = panState.ox + dx;
        viewport.y = panState.oy + dy;
        applyViewport();
        return;
    }
    if(!dragState) return;
    const node = canvasState.nodes.find(n => n.id === dragState.id);
    if(!node) return;
    const moveDx = (e.clientX - dragState.startX) / viewport.scale;
    const moveDy = (e.clientY - dragState.startY) / viewport.scale;
    if(Math.abs(moveDx) > 1 || Math.abs(moveDy) > 1) dragState.moved = true;
    (dragState.group || [{id:dragState.id, ox:dragState.ox, oy:dragState.oy}]).forEach(item => {
        const n = canvasState.nodes.find(x => x.id === item.id);
        if(!n) return;
        n.x = item.ox + moveDx;
        n.y = item.oy + moveDy;
    });
    if(assetLibraryOpen){
        const hit = document.elementFromPoint(e.clientX, e.clientY);
        if(hit && assetPanel?.contains(hit)){
            setAssetDragOver(true);
            clearDropHighlight();
            setAssetDragOver(true);
            return;
        }
        setAssetDragOver(false);
    }
    const draggedRect = nodeRect(node);
    const rawTarget = dragState.ctrlGroup
        ? (['smart-prompt','smart-loop'].includes(node.type)
            ? dragConnectTargetFor(node, screenToWorld(e))
            : rectOverlapNode(node.id, draggedRect.x, draggedRect.y, draggedRect.width, draggedRect.height, dragState.groupIds))
        : null;
    const target = isSmartGroupNode(rawTarget) ? null : rawTarget;
    setDropHighlight(target?.id || '');
    moveNodeElementsDuringDrag();
    updateLoopInsertPreview();
    if(target) setDropHighlight(target.id);
};
window.onmouseup = e => {
    const nodeDragMoved = Boolean(dragState?.moved);
    document.body.classList.remove('smart-node-drag');
    document.body.classList.remove('smart-node-resize');
    if(nodeDragMoved){
        document.body.classList.add('smart-node-drag-settling');
        if(composerDragSettleTimer) clearTimeout(composerDragSettleTimer);
        composerDragSettleTimer = setTimeout(() => {
            composerDragSettleTimer = 0;
            document.body.classList.remove('smart-node-drag-settling');
        }, 180);
    }
    if(connectionEraseState){
        const changed = finishConnectionErase();
        connectionEraseState = null;
        shell.classList.remove('connection-erasing');
        clearConnectionEraseTrail();
        if(changed) scheduleSave();
        return;
    }
    if(portDragState){
        const drag = portDragState;
        portDragState = null;
        shell.classList.remove('port-dragging');
        clearPortDragVisual();
        handlePortDrop(drag, e);
        return;
    }
    // Prompt/composer panel resizing is a view/layout adjustment. It must not
    // enter the content autosave queue unless the node's actual content changed.
    if(promptResizeState){ promptResizeState = null; }
    if(selectionState) finishSelection(e);
    if(previewCompareDrag) previewCompareDrag = false;
    if(previewPanDrag){
        previewPanDrag = null;
        document.getElementById('previewStage')?.classList.remove('panning');
    }
    if(imageEditPanDrag) imageEditPanDrag = null;
    if(cropDrag){
        document.getElementById('cropCanvas')?.classList.remove('dragging-image');
        cropDrag = null;
    }
    if(resizeState){
        const node = canvasState.nodes.find(n => n.id === resizeState.id);
        const rect = node ? nodeRect(node) : null;
        const changed = rect && (Math.abs(rect.width - resizeState.startW) > 1 || Math.abs(rect.height - resizeState.startH) > 1);
        if(changed){
            commitPendingUndo();
        } else { discardPendingUndo(); }
        resizeState = null;
        if(changed) render();
    }
    if(llmInstructionResizeState){
        const node = canvasState.nodes.find(n => n.id === llmInstructionResizeState.id);
        const changed = node && promptLlmInstructionHeight(node) !== llmInstructionResizeState.startH;
        document.body.classList.remove('smart-node-resize', 'smart-llm-instr-resize');
        if(changed) commitPendingUndo(); else discardPendingUndo();
        llmInstructionResizeState = null;
        render();
    }
    if(promptSplitResizeState){
        const node = canvasState.nodes.find(n => n.id === promptSplitResizeState.id);
        const changed = node && promptNodeSplitPreviewHeight(node) !== promptSplitResizeState.startH;
        document.body.classList.remove('smart-node-resize', 'smart-prompt-split-resize');
        if(changed) commitPendingUndo(); else discardPendingUndo();
        promptSplitResizeState = null;
        render();
    }
    if(thumbDragState){
        if(!thumbDragState.detached) discardPendingUndo();
        thumbDragState = null;
    }
    if(panState) {
        panState = null;
        shell.classList.remove('panning');
        setTimeout(() => { didPan = false; }, 0);
    }
    if(smartMinimapDrag){
        smartMinimapDrag = false;
    }
    if(dragState){
        const draggedNode = canvasState.nodes.find(n => n.id === dragState.id);
        let stateChanged = false;
        let contentChanged = false;
        const hit = document.elementFromPoint(e.clientX, e.clientY);
        const droppedOnAssetPanel = assetLibraryOpen && hit && assetPanel?.contains(hit);
        if(droppedOnAssetPanel && draggedNode && (draggedNode.images || []).length){
            const imagesToSave = (draggedNode.images || []).filter(img => img?.url);
            imagesToSave.forEach(img => addUrlToAssetLibrary(img.url, img.name || draggedNode.title || 'image', img));
            (dragState.group || [{id:dragState.id, ox:dragState.ox, oy:dragState.oy}]).forEach(item => {
                const n = canvasState.nodes.find(x => x.id === item.id);
                if(n){ n.x = item.ox; n.y = item.oy; }
            });
            setAssetDragOver(false);
            discardPendingUndo();
            clearDropHighlight();
            dragState = null;
            document.body.classList.remove('smart-node-drag');
            render();
            return;
        }
        const autoTarget = draggedNode && dragState.ctrlGroup ? dragConnectTargetFor(draggedNode, screenToWorld(e)) : null;
        const insertHit = draggedNode?.type === 'smart-loop' && dragState.ctrlGroup && (dragState.group || []).length <= 1
            ? insertionConnectionForNode(draggedNode)
            : null;
        const draggedRect = draggedNode ? nodeRect(draggedNode) : null;
        const groupTarget = draggedNode && (draggedNode.images || []).length && (dragState.group || []).length <= 1 && draggedRect
            ? rectOverlapNode(draggedNode.id, draggedRect.x, draggedRect.y, draggedRect.width, draggedRect.height, dragState.groupIds)
            : null;
        // 拖入分组：单个节点、多选（批量拖入）或整个分组（其成员会并入目标分组）都允许并入主分组下的目标分组。
        // 目标分组由主拖动节点的中心命中决定；smartGroupTargetForDraggedNode 已排除正在被拖动的节点/分组。
        const draggedNodes = (dragState.group || []).map(item => canvasState.nodes.find(n => n.id === item.id)).filter(Boolean);
        const smartGroupTarget = draggedNode ? smartGroupTargetForDraggedNode(draggedNode) : null;
        if(
            insertHit &&
            insertLoopNodeIntoConnection(draggedNode, insertHit)
        ){
            stateChanged = true;
            contentChanged = true;
            render();
        } else if(
            smartGroupTarget &&
            addDraggedNodesToSmartGroup(draggedNodes.length ? draggedNodes : [draggedNode], smartGroupTarget)
        ){
            stateChanged = true;
            contentChanged = true;
            render();
        } else if(
            groupTarget &&
            dragState.ctrlGroup &&
            (groupTarget.images || []).length > 1 &&
            mergeImageNodesIntoGroup(draggedNode.id, groupTarget.id)
        ){
            stateChanged = true;
            contentChanged = true;
            render();
        } else if(
            draggedNode &&
            autoTarget &&
            dragState.ctrlGroup &&
            (dragState.group || []).length <= 1 &&
            canAutoConnectDraggedNode(draggedNode, autoTarget) &&
            connectInputNode(draggedNode.id, autoTarget.id)
        ){
            stateChanged = true;
            contentChanged = true;
            restoreDraggedNodePosition();
            if(canvasState.selectedId === draggedNode.id) canvasState.selectedId = '';
            render();
        } else if(draggedNode && (draggedNode.images || []).length && (dragState.group || []).length <= 1){
            const r = nodeRect(draggedNode);
            const target = rectOverlapNode(draggedNode.id, r.x, r.y, r.width, r.height, dragState.groupIds);
            if(target && isSmartGroupNode(target)){
                if((dragState.group || []).some(item => {
                    const n = canvasState.nodes.find(x => x.id === item.id);
                    return n && (Math.abs((Number(n.x) || 0) - item.ox) > 1 || Math.abs((Number(n.y) || 0) - item.oy) > 1);
                })) stateChanged = true;
            } else if(target && dragState.ctrlGroup && !isSmartGroupNode(target) && canAutoConnectDraggedNode(draggedNode, target)){
                stateChanged = true;
                contentChanged = true;
                connectInputNode(draggedNode.id, target.id);
                if(!dragState.thumbDetached) restoreDraggedNodePosition();
                if(canvasState.selectedId === draggedNode.id) canvasState.selectedId = '';
                render();
            } else if((dragState.group || []).some(item => {
                const n = canvasState.nodes.find(x => x.id === item.id);
                return n && (Math.abs((Number(n.x) || 0) - item.ox) > 1 || Math.abs((Number(n.y) || 0) - item.oy) > 1);
            })){
                stateChanged = true;
            }
        } else if((dragState.group || []).some(item => {
            const n = canvasState.nodes.find(x => x.id === item.id);
            return n && (Math.abs((Number(n.x) || 0) - item.ox) > 1 || Math.abs((Number(n.y) || 0) - item.oy) > 1);
        }) || (draggedNode && (Math.abs((draggedNode.x || 0) - dragState.ox) > 1 || Math.abs((draggedNode.y || 0) - dragState.oy) > 1))){
            stateChanged = true;
        }
        if(dragState.thumbDetached){
            stateChanged = true;
            contentChanged = true;
        }
        // 拖出（没落到任何分组上）：普通节点退出所在分组；子分组退出时把它并入过的成员从主分组里撤掉。
        if(draggedNode && !smartGroupTarget && pruneSmartGroupMembershipsForNode(draggedNode)){
            stateChanged = true;
            contentChanged = true;
            render();
        }
        if(stateChanged) commitPendingUndo();
        else discardPendingUndo();
        if(stateChanged && !contentChanged){
            // 纯位置拖动（未产生连线/分组等结构性变更）也是 durable 内容：x/y 会随手动保存落盘，
            // 必须进入自动保存队列，否则界面显示"已保存"而关页后位置静默丢失。
            scheduleSave();
        }
        if(stateChanged || dragState.thumbDetached) suppressNodeClickUntil = Date.now() + 180;
        clearDropHighlight();
        loopInsertPreview = null;
        dragState = null;
        if(contentChanged) scheduleSave();
        scheduleConnectionLayerRefresh();
    }
};
function canvasWheelIsReserved(target){
    if(composerPromptView.mode === 'flow' || promptNodeFlowView.mode === 'flow') return true;
    return Boolean(target?.closest?.([
        '.prompt-input',
        '[contenteditable="true"]',
        'textarea',
        'input[type="text"]',
        'input[type="search"]',
        '.composer .smart-popover',
        '.composer .mention-picker',
        '.composer .prompt-preset-panel',
        '.composer .prompt-template-panel',
        '.composer .prompt-flow-context-list',
        '.smart-settings-modal',
        '.smart-settings',
        '.smart-back',
        '.image-edit-modal',
        '.asset-panel',
        '.asset-toggle',
        '.smart-log-toggle',
        '.smart-shortcut-toggle',
        '.log-modal',
        '.shortcut-modal',
        '.prompt-node-segments',
        '.prompt-node-text',
        '.prompt-node-llm',
        '.smart-group-list',
        '[data-thumb-scroll]'
    ].join(',')));
}
shell.addEventListener('wheel', e => {
    if(canvasWheelIsReserved(e.target)) return;
    e.preventDefault();
    const rect = shell.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const before = {x:(sx - viewport.x) / viewport.scale, y:(sy - viewport.y) / viewport.scale};
    const factor = canvasWheelZoomFactor(e, shell.clientHeight || window.innerHeight || 800);
    viewport.scale = safeScale(viewport.scale * factor);
    viewport.x = sx - before.x * viewport.scale;
    viewport.y = sy - before.y * viewport.scale;
    applyViewport();
}, {passive:false});
shell.ondragover = e => setSmartDropCopyEffect(e, true);
shell.ondrop = async e => {
    e.preventDefault();
    if(e.target.closest('.history-drawer')) return;
    if(e.target.closest('.image-node')) return;
    const p = screenToWorld(e);
    const assetRaw = e.dataTransfer.getData('application/x-smart-asset');
    if(assetRaw){
        try {
            const asset = JSON.parse(assetRaw);
            if(asset?.url) {
                const historyDropPoint = smartHistoryCanvasDropPoint(e.clientX, e.clientY);
                if(!historyDropPoint) return;
                pushUndo();
                createImageNodeAt(historyDropPoint, [assetNodeImageFromItem(asset)], {skipUndo:true});
            }
            return;
        } catch {}
    }
    const payload = await resolveSmartImageDropPayload(e.dataTransfer);
    if(payload.type === 'none') return;
    await handleSmartImageDropPayload(payload, '', {point:p, forceNew:true});
};
window.addEventListener('paste', e => {
    // Ctrl+C 节点后，Ctrl+V 优先粘贴节点（不管剪贴板里有什么）
    if(nodeClipboard?.nodes?.length && !isEditableTarget(e.target)){
        e.preventDefault();
        pasteNodes();
        return;
    }
    const files = [...(e.clipboardData?.files || [])].filter(isSupportedUploadFile);
    if(files.length){
        lastImagePasteAt = Date.now();
        handleFiles(files, canvasState.selectedId);
        return;
    }
    // 素材库管理页「复制到画布」过来的素材：Ctrl+V 批量粘贴成图片节点
    if(!isEditableTarget(e.target) && pasteAssetsFromInbox()){
        e.preventDefault();
        return;
    }
});
window.addEventListener('keydown', e => {
    const key = String(e.key || '').toLowerCase();
    if((e.ctrlKey || e.metaKey) && key === 's'){
        e.preventDefault();
        void manualSaveCanvas();
        return;
    }
    if(key === 'r' && !isEditableTarget(e.target)) isRKeyDown = true;
    if(imageEditModal.classList.contains('open') && imageEditMode === 'preview' && !isEditableTarget(e.target)){
        if(e.key === 'ArrowLeft' || e.key === 'ArrowRight'){
            e.preventDefault();
            if(!seekPreviewVideoFrames(e.key === 'ArrowLeft' ? -1 : 1)){
                navigatePreviewImage(e.key === 'ArrowLeft' ? -1 : 1);
            }
            return;
        }
    }
    if(!e.ctrlKey && !e.metaKey && !e.altKey && !isEditableTarget(e.target)){
        if(key === 'z'){
            if(e.repeat) return;
            e.preventDefault();
            toggleZoomPreview();
            return;
        }
        if(key === 'a'){
            if(e.repeat) return;
            e.preventDefault();
            toggleAssetLibrary();
            return;
        }
    }
    if((e.ctrlKey || e.metaKey) && key === 'c' && !isEditableTarget(e.target)){
        const selectionText = window.getSelection?.().toString() || '';
        if(selectionText) return;
        e.preventDefault();
        copySelectedNodes();
        return;
    }
    if((e.ctrlKey || e.metaKey) && key === 'v' && !isEditableTarget(e.target) && nodeClipboard?.nodes?.length){
        const requestedAt = Date.now();
        setTimeout(() => {
            if(lastImagePasteAt >= requestedAt) return;
            if(lastNodePasteAt >= requestedAt) return;
            pasteNodes();
        }, 90);
    }
    if(e.key === 'Escape' && imageEditModal.classList.contains('open')){
        closeImageEditor();
        return;
    }
    if((e.ctrlKey || e.metaKey) && (e.shiftKey && key === 'z' || key === 'y') && !isEditableTarget(e.target)){
        e.preventDefault();
        performRedo();
        return;
    }
    if((e.ctrlKey || e.metaKey) && key === 'z' && !isEditableTarget(e.target)){
        e.preventDefault();
        performUndo();
        return;
    }
    if((e.key === 'Delete' || e.key === 'Backspace') && (canvasState.selectedId || canvasState.selectedIds.length) && !isEditableTarget(e.target)){
        e.preventDefault();
        const ids = canvasState.selectedIds.length ? canvasState.selectedIds.slice() : [canvasState.selectedId];
        // #65: 选中项里有生成中的节点时禁止删除（先取消任务）。
        if(ids.map(id => canvasState.nodes.find(n => n.id === id)).some(node => node && smartNodeInFlight(node))){
            toast(tr('smart.nodeDeleteBusy'));
            return;
        }
        pushUndo();
        ids.forEach(id => { undoSuppressed = true; deleteNode(id); undoSuppressed = false; });
        render();
        scheduleSave();
    }
    if((e.ctrlKey || e.metaKey) && e.shiftKey && key === 'g' && !isEditableTarget(e.target)){
        e.preventDefault();
        const ids = canvasState.selectedIds.length ? canvasState.selectedIds.slice() : (canvasState.selectedId ? [canvasState.selectedId] : []);
        const ok = ids.map(id => ungroupNode(id)).some(Boolean);
        if(ok) return;
    }
    if((e.ctrlKey || e.metaKey) && key === 'g' && !e.shiftKey && !isEditableTarget(e.target)){
        e.preventDefault();
        groupSelectedNodes();
    }
});
window.addEventListener('keyup', e => {
    if(String(e.key || '').toLowerCase() === 'r') isRKeyDown = false;
});
window.addEventListener('blur', () => {
    isRKeyDown = false;
});
engineSelect.onchange = () => {
    settings.engine = engineSelect.value;
    applyRecentSmartSettingsForCurrentMode();
    syncApiKindToggleVisibility();
    renderDynamicParams();
    persistActiveSmartSettings();
    scheduleSave();
};
function syncApiKindToggleVisibility(){
    if(!apiKindToggle) return;
    apiKindToggle.style.display = isApiLikeEngine(settings.engine) ? 'inline-flex' : 'none';
    apiKindToggle.querySelectorAll('[data-kind]').forEach(btn => btn.classList.toggle('active', btn.dataset.kind === (settings.apiKind || 'image')));
}
if(apiKindToggle){
    apiKindToggle.querySelectorAll('[data-kind]').forEach(btn => {
        btn.onclick = e => {
            e.preventDefault();
            e.stopPropagation();
            const kind = btn.dataset.kind;
            if(kind === settings.apiKind) return;
            settings.apiKind = kind;
            applyRecentSmartSettingsForCurrentMode();
            syncApiKindToggleVisibility();
            renderDynamicParams();
            persistActiveSmartSettings();
            scheduleSave();
        };
    });
}
let promptResizeState = null;
const promptResize = document.getElementById('promptResize');
if(promptResize){
    promptResize.addEventListener('mousedown', e => {
        if(e.button !== 0 || composerPromptView.mode === 'flow') return;
        e.preventDefault(); e.stopPropagation();
        promptResizeState = {
            startY: e.clientY,
            startH: promptInput.getBoundingClientRect().height / composerWorldScale().y,
            mode:composerPromptView.mode
        };
    });
}
[composerExpandBtn, composerFlowBtn].forEach(button => {
    button?.addEventListener('mousedown', event => event.preventDefault());
});
if(composerExpandBtn) composerExpandBtn.onclick = event => {
    event.preventDefault();
    event.stopPropagation();
    expandPromptToContent();
};
if(composerFlowBtn) composerFlowBtn.onclick = event => {
    event.preventDefault();
    event.stopPropagation();
    if(composerPromptView.mode === 'flow') exitPromptFlow();
    else enterPromptFlow(composerPromptView.mode);
};
runBtn.onclick = runGeneration;
fileInput.onchange = () => {
    const groupPoint = pendingGroupUploadPoint;
    if(!fileInput.files?.length){
        pendingGroupUploadPoint = null;
        uploadTargetId = '';
        return;
    }
    const targetId = groupPoint ? '' : (uploadTargetId || canvasState.selectedId);
    handleFiles(fileInput.files, targetId, groupPoint ? {point:groupPoint} : {});
    pendingGroupUploadPoint = null;
    uploadTargetId = '';
    fileInput.value = '';
};
if(assetToggle) assetToggle.onclick = () => toggleAssetLibrary();
if(assetCloseBtn) assetCloseBtn.onclick = () => toggleAssetLibrary(false);
assetPanel?.addEventListener('pointerdown', e => e.stopPropagation());
assetPanel?.addEventListener('mousedown', e => e.stopPropagation());
assetPanel?.addEventListener('click', e => e.stopPropagation());
assetPanel?.addEventListener('wheel', e => {
    e.stopPropagation();
    // 滚动时取消待显示的悬浮预览并隐藏，避免滚动中加载大图卡顿。
    clearTimeout(assetHoverTimer);
    hideAssetHoverPreview();
    const scroller = e.target.closest?.('.asset-grid') || assetGrid;
    if(!scroller || getComputedStyle(scroller).display === 'none') return;
    const canScroll = scroller.scrollHeight > scroller.clientHeight || scroller.scrollWidth > scroller.clientWidth;
    if(!canScroll) return;
    e.preventDefault();
    scroller.scrollTop += e.deltaY;
    scroller.scrollLeft += e.deltaX;
}, {passive:false, capture:true});
assetDialogBackdrop?.addEventListener('pointerdown', e => e.stopPropagation());
assetDialogBackdrop?.addEventListener('mousedown', e => e.stopPropagation());
assetDialogBackdrop?.addEventListener('click', e => e.stopPropagation());
promptPresetPanel?.addEventListener('pointerdown', e => e.stopPropagation());
promptPresetPanel?.addEventListener('mousedown', e => e.stopPropagation());
promptPresetPanel?.addEventListener('click', e => e.stopPropagation());
promptTemplatePanel?.addEventListener('pointerdown', e => e.stopPropagation());
promptTemplatePanel?.addEventListener('mousedown', e => e.stopPropagation());
promptTemplatePanel?.addEventListener('wheel', e => e.stopPropagation(), {passive:false});
promptTemplatePanel?.addEventListener('click', e => {
    e.stopPropagation();
    const apply = e.target.closest('[data-template-apply]');
    if(apply){ applyPromptTemplateToNode(apply.dataset.templateApply || 'positive'); return; }
    if(e.target.closest('[data-template-save-current]')){ saveCurrentPromptAsTemplate(); return; }
    if(e.target.closest('[data-template-new]')){ createBlankPromptTemplate(); return; }
    if(e.target.closest('[data-template-edit]')) { promptTemplateEditing = true; renderPromptTemplatePanel(); return; }
    if(e.target.closest('[data-template-edit-cancel]')) { promptTemplateEditing = false; renderPromptTemplatePanel(); return; }
    if(e.target.closest('[data-template-edit-save]')){ savePromptTemplateEdit(); return; }
    if(e.target.closest('[data-template-delete]')){ deletePromptTemplate(); return; }
    const cat = e.target.closest('[data-template-cat]');
    if(cat){
        promptTemplateCategory = cat.dataset.templateCat || 'all';
        promptTemplateSelectedId = '';
        promptTemplateEditing = false;
        renderPromptTemplatePanel({preserveScroll:false});
        return;
    }
    const catEdit = e.target.closest('[data-template-cat-edit]');
    if(catEdit){
        const id = catEdit.dataset.templateCatEdit || '';
        renamePromptTemplateGroup(id);
        return;
    }
    const catDelete = e.target.closest('[data-template-cat-delete]');
    if(catDelete){
        deletePromptTemplateGroup(catDelete.dataset.templateCatDelete || '');
        return;
    }
    if(e.target.closest('[data-template-group-edit]')){
        promptTemplateGroupEditMode = !promptTemplateGroupEditMode;
        renderPromptTemplatePanel({preserveScroll:false});
        return;
    }
    if(e.target.closest('[data-template-cat-new]')) { createPromptTemplateGroup(); return; }
    const card = e.target.closest('[data-template-id]');
    if(card){
        promptTemplateSelectedId = card.dataset.templateId || '';
        promptTemplateEditing = false;
        renderPromptTemplatePanel();
        return;
    }
});
if(promptPresetClose) promptPresetClose.onclick = closePromptPresetPanel;
if(promptTemplateClose) promptTemplateClose.onclick = closePromptTemplatePanel;
if(promptTemplateSearch) promptTemplateSearch.oninput = () => renderPromptTemplatePanel({preserveScroll:false});
if(promptTemplateLibrarySelect) promptTemplateLibrarySelect.onchange = async () => {
    activePromptLibraryId = promptTemplateLibrarySelect.value || 'system';
    promptTemplateSelectedId = '';
    // 切换词库必须重置分类筛选，否则上一个库的分类（如系统的“视角”）会把新库内容过滤为空。
    promptTemplateCategory = 'all';
    promptTemplateEditing = false;
    // 拉取最新数据，确保素材库管理里新建/新增的词库与提示词在画布即时可见。
    const want = activePromptLibraryId;
    try { await loadPromptTemplates(); } catch(e){}
    if(promptLibraries.some(lib => lib.id === want)) activePromptLibraryId = want;
    renderPromptLibrarySelect();
    renderPromptTemplatePanel({preserveScroll:false});
};
if(composerTemplateBtn) composerTemplateBtn.onclick = event => {
    event.preventDefault();
    event.stopPropagation();
    if(promptTemplatePanel?.classList?.contains('open') && promptTemplatePanel.dataset.target === 'composer'){
        closePromptTemplatePanel();
        return;
    }
    openPromptTemplatePanel(activeComposerNode()?.id || selectedNode()?.id || '', promptTemplateSelectedId, {target:'composer'});
};
if(promptPresetSelect) promptPresetSelect.onchange = () => renderPromptPresetPanel(promptPresetSelect.value);
[promptPresetName, promptPresetText].forEach(input => {
    input?.addEventListener('input', () => {
        resetPromptPresetDeleteState();
        setPromptPresetStatus(tr('smart.promptPresetEditing'));
    });
});
if(promptPresetApply) promptPresetApply.onclick = () => {
    const preset = currentPromptPreset(promptPresetSelect.value);
    const node = promptPresetPanelNode();
    if(!preset || !node) return;
    node.promptPresetId = preset.id;
    node.text = preset.text || '';
    closePromptPresetPanel();
    render();
    scheduleSave();
};
if(promptPresetSave) promptPresetSave.onclick = () => {
    const preset = currentPromptPreset(promptPresetSelect.value);
    if(!preset) return;
    const name = promptPresetName.value.trim();
    const text = promptPresetText.value.trim();
    if(!name || !text){ setPromptPresetStatus(tr('smart.promptPresetRequired'), 'warn'); return; }
    const idx = promptPresets.findIndex(p => p.id === preset.id);
    if(idx >= 0) promptPresets[idx] = {...promptPresets[idx], name, text, updatedAt:Date.now()};
    savePromptPresets();
    const node = promptPresetPanelNode();
    if(node?.promptPresetId === preset.id) node.text = text;
    renderPromptPresetPanel(preset.id, tr('smart.promptPresetSaved'));
    setPromptPresetStatus(tr('smart.promptPresetSaved'), 'ok');
    render();
    scheduleSave();
};
if(promptPresetNew) promptPresetNew.onclick = () => {
    const node = promptPresetPanelNode();
    const preset = createPromptPresetFromNode(node, {openPanel:false});
    if(!preset) return;
    renderPromptPresetPanel(preset.id, tr('smart.promptPresetSavedNew'));
    setPromptPresetStatus(tr('smart.promptPresetSavedNew'), 'ok');
    promptPresetName?.focus();
    promptPresetName?.select();
};
if(promptPresetDelete) promptPresetDelete.onclick = () => {
    const preset = currentPromptPreset(promptPresetSelect.value);
    if(!preset) return;
    if(!promptPresetDeleteArmed){
        promptPresetDeleteArmed = true;
        promptPresetDelete.textContent = tr('smart.promptPresetDeleteAgain');
        promptPresetDelete.classList.add('confirm-danger');
        setPromptPresetStatus(tr('smart.promptPresetDeleteConfirm').replace('{name}', preset.name || tr('smart.promptPresetUnnamed')), 'warn');
        return;
    }
    promptPresets = promptPresets.filter(p => p.id !== preset.id);
    canvasState.nodes.forEach(node => { if(node.promptPresetId === preset.id) node.promptPresetId = ''; });
    savePromptPresets();
    renderPromptPresetPanel(promptPresets[0]?.id || '', tr('smart.promptPresetDeleted'));
    setPromptPresetStatus(tr('smart.promptPresetDeleted'), 'ok');
    render();
    scheduleSave();
};
if(assetLibrarySelect) assetLibrarySelect.onchange = () => {
    assetLibrarySelectionTouched = true;
    activeAssetLibraryId = assetLibrarySelect.value || '';
    activeAssetCategoryId = '';
    mentionAssetCategoryId = '';
    renderAssetLibrary();
};
if(assetCategorySelect) assetCategorySelect.onchange = () => {
    activeAssetCategoryId = assetCategorySelect.value;
    renderAssetLibrary();
};
if(assetAddCategoryBtn) assetAddCategoryBtn.onclick = async () => {
    const fallbackName = tr('smart.assetFolder');
    const name = await openAssetNameDialog({title:tr('smart.assetNewFolder'), value:fallbackName, placeholder:fallbackName});
    if(!name) return;
    if(assetLibraryIsLocal()){
        const data = await fetch('/api/local-assets/folders', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({parent:localAssetFolderPath(), name})
        }).then(async r => {
            if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.folderCreateFailed'));
            return r.json();
        });
        setLocalAssetLibraryFromResponse(data);
        activeAssetCategoryId = data.folder?.path || activeAssetCategoryId;
        renderAssetLibrary();
        return;
    }
    const data = await fetch('/api/asset-library/categories', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({library_id:activeAssetLibraryId, name, type:'image'})}).then(r => r.json());
    setActiveAssetTabCategory(data.category?.id || '');
    setAssetLibraryFromResponse(data);
};
if(assetRenameCategoryBtn) assetRenameCategoryBtn.onclick = async () => {
    const cat = activeAssetTabCategory();
    if(!cat) return;
    const name = await openAssetNameDialog({title:tr('smart.assetRenameFolder'), value:cat.name || '', placeholder:tr('smart.assetFolder')});
    if(!name) return;
    if(assetLibraryIsLocal()){
        const data = await fetch('/api/local-assets/folders', {
            method:'PATCH',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({path:cat.id || '', name})
        }).then(async r => {
            if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || tr('smart.folderRenameFailed'));
            return r.json();
        });
        setLocalAssetLibraryFromResponse(data);
        activeAssetCategoryId = data.folder?.path || activeAssetCategoryId;
        renderAssetLibrary();
        return;
    }
    const data = await fetch(`/api/asset-library/categories/${encodeURIComponent(cat.id)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})}).then(r => r.json());
    setAssetLibraryFromResponse(data);
};
function hasCanvasImageDrag(event){
    return Array.from(event.dataTransfer?.types || []).includes('application/x-smart-canvas-image');
}
function setAssetDragOver(active){
    if(!assetDropZone || !assetPanel) return;
    assetDropZone.classList.toggle('drag-over', !!active);
    assetPanel.classList.toggle('drag-over', !!active);
}
function handleAssetPanelDragOver(e){
    if(hasCanvasImageDrag(e) || hasSmartImageDropData(e.dataTransfer)){
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = 'copy';
        setAssetDragOver(true);
    }
}
async function handleAssetPanelDrop(e){
    if(!hasCanvasImageDrag(e) && !hasSmartImageDropData(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    setAssetDragOver(false);
    const raw = e.dataTransfer.getData('application/x-smart-canvas-image');
    if(raw){
        try {
            const payload = JSON.parse(raw);
            if(payload?.url) await addUrlToAssetLibrary(payload.url, payload.name || '', payload);
            return;
        } catch(e) {
            toast(tr('smart.assetAddFail'));
            return;
        }
    }
    try {
        const payload = await resolveSmartImageDropPayload(e.dataTransfer);
        if(payload.type === 'files') {
            if(assetLibraryIsLocal()) await addFilesToLocalAssetLibrary(payload.files);
            else {
                const uploaded = await uploadFiles(payload.files);
                for(const file of uploaded) if(file?.url) await addUrlToAssetLibrary(file.url, file.name || '');
            }
        } else if(payload.type === 'localPaths') {
            if(assetLibraryIsLocal()) await addLocalPathsToLocalAssetLibrary(payload.localPaths);
            else {
                const imported = await importSmartLocalImages(payload.localPaths);
                for(const file of imported) if(file?.url) await addUrlToAssetLibrary(file.url, file.name || '');
            }
        } else if(payload.type === 'url') {
            await addUrlToAssetLibrary(payload.url, smartImageNameFromUrl(payload.url));
        }
    } catch(err) {
        toast(err.message || tr('smart.assetAddFail'));
    }
}
assetDropZone?.addEventListener('dragover', e => {
    if(hasCanvasImageDrag(e) || hasSmartImageDropData(e.dataTransfer)){
        e.preventDefault();
        e.stopPropagation();
        assetDropZone?.classList.add('drag-over');
    }
});
assetDropZone?.addEventListener('dragleave', () => assetDropZone?.classList.remove('drag-over'));
assetDropZone?.addEventListener('drop', handleAssetPanelDrop);
assetPanel?.addEventListener('dragover', handleAssetPanelDragOver);
assetPanel?.addEventListener('dragleave', e => { if(!assetPanel?.contains(e.relatedTarget)) setAssetDragOver(false); });
assetPanel?.addEventListener('drop', handleAssetPanelDrop);
createMenu?.addEventListener('mousedown', event => event.stopPropagation());
createMenu?.addEventListener('click', event => {
    event.stopPropagation();
    const card = event.target.closest('[data-create-type]');
    if(card) createNodeFromMenu(card.dataset.createType || 'image');
});
function createCanvasStarterImage(pickMedia=false){
    closeCreateMenu();
    const point = viewportCenter();
    // 给节点下方的生成面板留出首屏空间，同时保留上传节点的完整卡片。
    // 720px 高的窗口中，节点上移约 140px 后，上传卡片和运行按钮都能同时进入首屏。
    point.y -= 140 / safeScale(viewport.scale);
    const node = createImageNodeAt(point, [], {select:true});
    if(pickMedia && node?.id) setTimeout(() => pickMediaForSmartNode(node.id), 0);
    return node;
}
canvasEmptyState?.addEventListener('pointerdown', event => event.stopPropagation());
canvasEmptyState?.addEventListener('mousedown', event => event.stopPropagation());
canvasEmptyState?.addEventListener('click', event => event.stopPropagation());
canvasEmptyCreate?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    createCanvasStarterImage(false);
});
canvasEmptyImport?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    createCanvasStarterImage(true);
});
composer.addEventListener('pointerdown', event => event.stopPropagation());
composer.addEventListener('mousedown', event => event.stopPropagation());
composer.addEventListener('click', event => {
    // 点「生成」按钮不要收起已展开的参数栏:否则每生成一次参数栏就被收起,需重新点开。
    // 参数控件(.smart-control)内部点击本就不关;运行按钮也排除,让参数栏熬过生成与重渲染。
    if(!event.target.closest('.smart-control') && !event.target.closest('#runBtn')) closeAllSmartPopovers();
    event.stopPropagation();
});
longContentReader?.addEventListener('pointerdown', event => event.stopPropagation());
longContentReader?.addEventListener('mousedown', event => event.stopPropagation());
longContentReader?.addEventListener('wheel', event => event.stopPropagation(), {passive:true});
longContentReader?.addEventListener('click', event => {
    event.stopPropagation();
    if(event.target === longContentReader) closeLongContentReader();
});
longContentReader?.addEventListener('keydown', event => {
    event.stopPropagation();
    if(event.key === 'Escape'){
        event.preventDefault();
        closeLongContentReader();
        return;
    }
    if(event.key !== 'Tab') return;
    const focusable = [longContentReaderClose, longContentReaderBody].filter(Boolean);
    if(!focusable.length) return;
    event.preventDefault();
    const index = focusable.indexOf(document.activeElement);
    const next = event.shiftKey
        ? (index <= 0 ? focusable.length - 1 : index - 1)
        : (index < 0 || index === focusable.length - 1 ? 0 : index + 1);
    focusable[next].focus({preventScroll:true});
});
longContentReaderClose?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    closeLongContentReader();
});
promptInput.addEventListener('scroll', () => {
    if(composerPromptView.mode !== 'flow' && !composerPromptView.applyingScrollRestore){
        composerPromptView.restoreFlowScroll = false;
    }
});
promptInput.addEventListener('paste', e => {
    e.preventDefault();
    const text = e.clipboardData?.getData('text/plain') || '';
    if(!text) return;
    const sel = window.getSelection();
    if(!sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    range.deleteContents();
    range.insertNode(document.createTextNode(text));
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
    // 手动触发 input 事件，让浏览器撤销栈更新
    promptInput.dispatchEvent(new Event('input', {bubbles:true}));
});
promptInput.addEventListener('input', maybeOpenMentionPicker);
promptInput.addEventListener('input', () => {
    // A model popover can remain visible while the pointer is over the
    // parameter row. Close it as soon as the user edits the prompt so it can
    // never cover the editor or interfere with IME candidate composition.
    closeAllSmartPopovers();
    delete promptInput.dataset.preserveDraftOnce;
    savePromptDraftForCurrent();
    const currentNode = selectedNode();
    renderInputThumbsRow(currentNode);
    if(composerPromptView.mode === 'flow') renderPromptFlowContext(currentNode);
    syncRunButtonState(selectedNode());
    scheduleSave();
    if(!promptCompositionActive) refreshExpandedPromptHeight();
});
promptInput.addEventListener('compositionstart', () => { promptCompositionActive = true; });
promptInput.addEventListener('compositionend', () => {
    promptCompositionActive = false;
    refreshExpandedPromptHeight();
});
promptInput.addEventListener('keyup', maybeOpenMentionPicker);
promptInput.addEventListener('mouseup', saveMentionRange);
promptInput.addEventListener('focus', () => {
    closeAllSmartPopovers();
    saveMentionRange();
});
document.addEventListener('selectionchange', () => {
    rememberConversationTextSelection();
    if(document.activeElement === promptInput) saveMentionRange();
});
promptInput.addEventListener('keydown', event => {
    if(event.key === 'Escape' && composerPromptView.mode === 'flow'){
        event.preventDefault();
        event.stopPropagation();
        exitPromptFlow();
        return;
    }
    if(event.key === 'Escape') closeMentionPicker();
});
promptInput.addEventListener('mouseover', event => {
    const token = event.target.closest?.('.mention-image-token');
    if(!token) return;
    // 音频没有可预览的图像，不能把音频 URL 塞进 <img>（会显示破损图标），直接不弹悬浮预览。
    if(token.dataset.kind === 'audio'){ mentionPreview.style.display = 'none'; return; }
    let media = mentionPreview.querySelector('img,video');
    const isVideo = token.dataset.kind === 'video' || isVideoMediaItem({url:token.dataset.url, kind:token.dataset.kind});
    if(isVideo && media?.tagName?.toLowerCase() !== 'video'){
        media?.replaceWith(document.createElement('video'));
        media = mentionPreview.querySelector('video');
    } else if(!isVideo && media?.tagName?.toLowerCase() !== 'img'){
        media?.replaceWith(document.createElement('img'));
        media = mentionPreview.querySelector('img');
    }
    if(isVideo){
        media.muted = true;
        media.loop = true;
        media.playsInline = true;
        media.preload = 'metadata';
        media.disablePictureInPicture = true;
        media.setAttribute('disablepictureinpicture', '');
        media.setAttribute('controlslist', 'nodownload noplaybackrate noremoteplayback');
        media.src = token.dataset.url || '';
        media.play?.().catch(() => {});
    } else {
        media.src = token.dataset.url || '';
        media.alt = 'preview';
    }
    const rect = token.getBoundingClientRect();
    mentionPreview.style.left = `${Math.min(window.innerWidth - 236, rect.left)}px`;
    mentionPreview.style.top = `${Math.min(window.innerHeight - 236, rect.bottom + 8)}px`;
    mentionPreview.style.display = 'block';
});
promptInput.addEventListener('mouseout', event => {
    if(event.target.closest?.('.mention-image-token')){
        mentionPreview.style.display = 'none';
        const media = mentionPreview.querySelector('img,video');
        media?.pause?.();
        media?.removeAttribute('src');
        media?.load?.();
    }
});
mentionPicker.addEventListener('mousedown', event => event.stopPropagation());
document.addEventListener('click', event => {
    if(!event.target.closest('[data-extract-prompt-selection]')) closeConversationExtractionMenu();
    if(!event.target.closest('.smart-control')) closeAllSmartPopovers();
    if(!event.target.closest('.mention-picker') && !event.target.closest('#promptInput') && !event.target.closest('[data-input-add-reference]')) closeMentionPicker();
    if(!event.target.closest('.prompt-preset-panel') && !event.target.closest('.prompt-preset-edit') && !event.target.closest('.prompt-preset-save')) closePromptPresetPanel();
    if(!event.target.closest('.prompt-template-panel') && !event.target.closest('.prompt-preset-edit') && !event.target.closest('#composerTemplateBtn')) closePromptTemplatePanel();
});
document.addEventListener('keydown', event => {
    if(document.querySelector('#smartHistoryLightbox.open') && !event.altKey && !event.ctrlKey && !event.metaKey){
        if(event.key === 'ArrowLeft'){
            event.preventDefault();
            event.stopPropagation();
            switchSmartHistoryDetailOutput(-1);
            return;
        }
        if(event.key === 'ArrowRight'){
            event.preventDefault();
            event.stopPropagation();
            switchSmartHistoryDetailOutput(1);
            return;
        }
    }
    if(event.key === 'Escape' && document.querySelector('#smartHistoryLightbox.open')){
        event.preventDefault();
        event.stopPropagation();
        closeSmartHistoryLightbox();
        return;
    }
    if(event.key === 'Escape' && closeActiveLongContentSession()){
        event.preventDefault();
        event.stopPropagation();
        return;
    }
    if(event.key === 'Escape' && composerPromptView.mode === 'flow'){
        event.preventDefault();
        exitPromptFlow();
        return;
    }
    if(event.key === 'Escape' && document.querySelector('[data-extract-prompt-selection].open')){
        event.preventDefault();
        closeConversationExtractionMenu();
        return;
    }
    // Generation history is persistent workspace chrome: Escape closes
    // transient layers only; the drawer itself is closed by its button.
    if(event.key === 'Escape') { closeAllSmartPopovers(); closeCreateMenu(); closeSmartCanvasShortcuts(); closePromptPresetPanel(); closePromptTemplatePanel(); }
});
document.getElementById('cropBox').addEventListener('mousedown', event => beginCropDrag(event, cropDragModeFromPointer(event)));
document.querySelectorAll('[data-crop-handle]').forEach(handle => {
    handle.addEventListener('mousedown', event => beginCropDrag(event, `crop-${handle.dataset.cropHandle || 'se'}`));
});
document.querySelectorAll('[data-crop-ratio]').forEach(btn => {
    btn.addEventListener('click', event => {
        event.stopPropagation();
        setCropAspectPreset(btn.dataset.cropRatio || 'free');
    });
});
document.querySelectorAll('[data-outpaint-ratio]').forEach(btn => {
    btn.addEventListener('click', event => {
        event.stopPropagation();
        setOutpaintAspectPreset(btn.dataset.outpaintRatio || 'free');
    });
});
document.getElementById('outpaintFrame').addEventListener('mousedown', event => {
    if(event.target.closest('[data-outpaint-handle]')) return;
    beginCropDrag(event, 'image');
});
document.querySelectorAll('[data-outpaint-handle]').forEach(handle => {
    handle.addEventListener('mousedown', event => beginCropDrag(event, `outpaint-${handle.dataset.outpaintHandle || 'corner'}`));
});
document.getElementById('cropImage').addEventListener('mousedown', event => {
    if(imageEditMode !== 'outpaint' || !cropState) return;
    document.getElementById('cropCanvas')?.classList.add('dragging-image');
    beginCropDrag(event, 'image');
});
document.querySelectorAll('[data-image-edit-mode]').forEach(btn => {
    btn.addEventListener('click', event => {
        event.stopPropagation();
        setImageEditMode(btn.dataset.imageEditMode || 'crop', true);
    });
});
imageEditModal.addEventListener('pointerdown', event => {
    event.stopPropagation();
});
imageEditModal.addEventListener('mousedown', event => {
    event.stopPropagation();
});
imageEditModal.addEventListener('mousemove', event => {
    if(previewPanDrag || previewCompareDrag || imageEditPanDrag || cropDrag) return;
    event.stopPropagation();
});
imageEditModal.addEventListener('click', event => {
    event.stopPropagation();
    if(event.target === imageEditModal) closeImageEditor();
});
imageEditModal.addEventListener('wheel', event => {
    event.stopPropagation();
}, {passive:false});
document.getElementById('previewStage').addEventListener('mousedown', event => {
    if(imageEditMode !== 'preview' || event.button !== 0) return;
    if(event.target.closest('.preview-tools-overlay, .preview-download-overlay')) return;
    if(event.target.closest('.preview-compare-handle')) return;
    if(event.target.closest('video')) return;
    event.preventDefault();
    event.stopPropagation();
    previewPanDrag = {clientX:event.clientX, clientY:event.clientY, startX:previewPan.x, startY:previewPan.y};
});
document.getElementById('imageEditStage').addEventListener('mousedown', event => {
    if(imageEditMode === 'preview' || event.button !== 0) return;
    if(event.target.closest('.image-edit-actions, .preview-tools-overlay, .preview-download-overlay, .crop-box, .crop-handle')) return;
    if(event.target.closest('#editDrawCanvas, #editTextCanvas, .edit-text-inline') && imageEditMode !== 'crop') return;
    const stage = event.currentTarget;
    if(stage.scrollWidth <= stage.clientWidth && stage.scrollHeight <= stage.clientHeight) return;
    event.preventDefault();
    event.stopPropagation();
    imageEditPanDrag = {
        clientX:event.clientX,
        clientY:event.clientY,
        scrollLeft:stage.scrollLeft,
        scrollTop:stage.scrollTop
    };
});
document.getElementById('previewCompareHandle').addEventListener('mousedown', event => {
    if(imageEditMode !== 'preview' || !previewCompareOn || previewCompareIndex < 0) return;
    event.preventDefault();
    event.stopPropagation();
    previewPanDrag = null;
    previewCompareDrag = true;
    setPreviewComparePos(event.clientX);
});
document.getElementById('previewCompareHandle').addEventListener('pointerdown', event => {
    if(imageEditMode !== 'preview' || !previewCompareOn || previewCompareIndex < 0) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    previewPanDrag = null;
    previewCompareDrag = true;
    setPreviewComparePos(event.clientX);
});
document.getElementById('previewCompareHandle').addEventListener('pointermove', event => {
    if(!previewCompareDrag) return;
    event.preventDefault();
    event.stopPropagation();
    setPreviewComparePos(event.clientX);
});
document.getElementById('previewCompareHandle').addEventListener('pointerup', event => {
    if(previewCompareDrag){
        event.preventDefault();
        event.stopPropagation();
    }
    previewCompareDrag = false;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
});
document.getElementById('previewCompareHandle').addEventListener('pointercancel', event => {
    previewCompareDrag = false;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
});
document.getElementById('editDrawCanvas').addEventListener('pointerdown', beginEditDraw);
document.getElementById('editDrawCanvas').addEventListener('pointermove', moveEditDraw);
document.getElementById('editDrawCanvas').addEventListener('pointerup', endEditDraw);
document.getElementById('editDrawCanvas').addEventListener('pointercancel', endEditDraw);
document.getElementById('editDrawCanvas').addEventListener('pointerleave', endEditDraw);
document.getElementById('gridJoinCanvas')?.addEventListener('pointerdown', beginGridJoinDrag);
document.getElementById('gridJoinCanvas')?.addEventListener('pointermove', moveGridJoinDrag);
document.getElementById('gridJoinCanvas')?.addEventListener('pointerup', endGridJoinDrag);
document.getElementById('gridJoinCanvas')?.addEventListener('pointercancel', endGridJoinDrag);
document.getElementById('gridJoinCanvas')?.addEventListener('pointerleave', endGridJoinDrag);
document.getElementById('editTextCanvas')?.addEventListener('pointerdown', beginEditText);
document.getElementById('editTextCanvas')?.addEventListener('pointermove', moveEditText);
document.getElementById('editTextCanvas')?.addEventListener('pointerup', endEditText);
document.getElementById('editTextCanvas')?.addEventListener('pointercancel', endEditText);
document.getElementById('editTextCanvas')?.addEventListener('pointerleave', endEditText);
document.getElementById('editTextCanvas')?.addEventListener('dblclick', event => {
    if(imageEditMode !== 'brush' || brushTool !== 'text') return;
    event.preventDefault(); event.stopPropagation();
    const hit = hitEditTextItem(editTextPoint(event));
    if(hit){
        setSelectedEditTextItem(hit.id);
        beginEditTextInline(hit);
    }
});
['paintBrushSize','paintBrushColor'].forEach(id => {
    const control = document.getElementById(id);
    if(!control) return;
    control.addEventListener('input', syncSelectedEditTextStyleFromBrush);
    control.addEventListener('change', () => { editTextDirty = false; });
});
['gridHorizontalLines','gridVerticalLines','gridGapSize'].forEach(id => {
    document.getElementById(id).addEventListener('input', () => {
        syncGridGapValue();
        refreshGridSplitPreview();
    });
});
['imageResizeScaleRange','imageResizeScaleInput'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', event => setImageResizeScale(event.target.value));
});
document.getElementById('imageEditStage').addEventListener('wheel', event => {
    if(!cropState) return;
    event.preventDefault();
    event.stopPropagation();
    if(imageEditMode === 'preview'){
        if(seekPreviewVideoFrames(event.deltaY > 0 ? 1 : -1)) return;
        const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
        const frame = document.getElementById('previewFrame');
        const next = zoomPanAroundPoint(
            previewZoom,
            previewPan,
            factor,
            frame?.getBoundingClientRect(),
            event.clientX,
            event.clientY
        );
        previewZoom = next.zoom;
        previewPan = next.pan;
        applyPreviewTransform();
        return;
    }
    if(imageEditMode === 'grid' && gridOperationMode === 'join'){
        const stage = event.currentTarget;
        const oldZoom = imageEditZoom;
        const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
        imageEditZoom = Math.max(0.15, Math.min(6.0, imageEditZoom * factor));
        const stageRect = stage.getBoundingClientRect();
        const mx = event.clientX - stageRect.left;
        const my = event.clientY - stageRect.top;
        const contentX = stage.scrollLeft + mx;
        const contentY = stage.scrollTop + my;
        const scale = imageEditZoom / oldZoom;
        refreshGridSplitPreview();
        syncImageEditOverflow();
        updateZoomLabel();
        stage.scrollLeft = contentX * scale - mx;
        stage.scrollTop = contentY * scale - my;
        return;
    }
    const stage = event.currentTarget;
    const oldZoom = imageEditZoom;
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    imageEditZoom = Math.max(0.15, Math.min(6.0, imageEditZoom * factor));
    const stageRect = stage.getBoundingClientRect();
    const mx = event.clientX - stageRect.left;
    const my = event.clientY - stageRect.top;
    const contentX = stage.scrollLeft + mx;
    const scale = imageEditZoom / oldZoom;
    const contentY = stage.scrollTop + my;
    applyImageEditZoom(scale);
    stage.scrollLeft = contentX * scale - mx;
    stage.scrollTop = contentY * scale - my;
}, {passive:false});
window.addEventListener('resize', () => {
    if(cropState) syncImageEditOverflow();
    if(smartHistoryModal?.classList.contains('open')) scheduleSmartHistoryWindowRender(true);
    const repositionComposer = () => {
        const active = activeComposerNode();
        if(active?.id && composer?.classList.contains('open')) positionComposerForNode(active);
        if(promptNodeFlowView.mode === 'flow') positionPromptNodeFlow();
        if(smartShortcutModal?.classList.contains('open')) positionSmartCanvasShortcuts();
    };
    if((shell?.clientWidth || window.innerWidth || 0) < 720) {
        requestAnimationFrame(() => {
            frameSmartCanvasContentIfNeeded();
            requestAnimationFrame(repositionComposer);
        });
    } else {
        requestAnimationFrame(repositionComposer);
    }
});
window.addEventListener('studio-theme-change', event => applyTheme(event.detail?.theme || 'light'));
try {
    const apiChannel = new BroadcastChannel('studio-api');
    apiChannel.onmessage = async event => {
        if(event.data?.type === 'close-settings') closeSmartSettings();
        if(event.data?.type === 'providers-changed'){
            await refreshSmartConfigFromSettings();
        }
        if(event.data?.type === 'preferences-changed'){
            await refreshAutosavePreferences(event.data.preferences || null);
            if(event.data.preferences?.theme) applyTheme(event.data.preferences.theme);
            if(event.data.preferences?.language && window.StudioI18n) window.StudioI18n.set(event.data.preferences.language);
        }
        if(event.data?.type === 'asset_library_updated') handleAssetLibraryUpdatedMessage(event.data);
        if(event.data?.type === 'canvas_updated') handleCanvasUpdatedMessage(event.data);
    };
} catch(e) {}
window.addEventListener('focus', () => {
    if(Date.now() - lastConfigRefreshAt > 1200) refreshSmartConfigFromSettings();
});
window.addEventListener('message', event => {
    if(event.origin && event.origin !== location.origin) return;
    if(event.data?.type === 'settings-prepare-project-library'){
        const requestId = event.data.requestId;
        const source = event.source;
        void (async () => {
            let ok = true;
            if(isProjectCanvasData()){
                while(canvasSyncInFlight) await new Promise(resolve => setTimeout(resolve, 50));
                if(contentDirty) ok = await saveCanvas({automatic:false}) === true;
            }
            try { source?.postMessage({type:'settings-project-library-ready', requestId, ok}, location.origin); } catch(error) {}
        })();
        return;
    }
    if(event.data?.type === 'close-settings') closeSmartSettings();
    if(event.data?.type === 'studio-theme') applyTheme(event.data.theme || 'light');
    if(event.data?.type === 'providers-changed') refreshSmartConfigFromSettings();
    if(event.data?.type === 'preferences-changed'){
        refreshAutosavePreferences(event.data.preferences || null);
        if(event.data.preferences?.theme) applyTheme(event.data.preferences.theme);
        if(event.data.preferences?.language && window.StudioI18n) window.StudioI18n.set(event.data.preferences.language);
    }
    if(event.data?.type === 'asset_library_updated') handleAssetLibraryUpdatedMessage(event.data);
    if(event.data?.type === 'canvas_updated') handleCanvasUpdatedMessage(event.data);
    if(event.data?.type === 'studio-lang' && window.StudioI18n) {
        window.StudioI18n.set(event.data.lang || 'zh');
    }
});
window.addEventListener('keydown', event => {
    if(event.key === 'Escape' && smartSettingsModal?.classList.contains('open')){
        event.preventDefault();
        closeSmartSettings();
    }
});
window.addEventListener('studio-lang-change', () => {
    const saveStateEl = document.getElementById('projectSaveState');
    if(saveStateEl && saveStateEl.dataset.state){
        const saveText = {importing: tr('smart.projectImporting'), saving: tr('smart.projectSaving'), saved: tr('smart.projectSaved'), dirty: tr('smart.projectDirty'), error: tr('smart.projectConflict')};
        if(saveText[saveStateEl.dataset.state]) setProjectSaveState(saveStateEl.dataset.state, saveText[saveStateEl.dataset.state]);
    }
    renderDynamicParams();
    renderInputThumbsRow(selectedNode());
    renderAssetLibrary();
    updateSmartHistoryDrawerCollapseUi();
    if(document.getElementById('imageEditModal')?.classList.contains('open')){
        setImageEditMode(imageEditMode);
    }
    if(promptTemplatePanel?.classList?.contains('open')) renderPromptTemplatePanel();
    render();
});
setupSmartSettingsOverlay();
setupSmartTitleEdit();
smartEdgeReveal?.addEventListener('click', jumpToSmartEdgeRevealNode);
shell?.addEventListener('pointerdown', () => {
    if(smartEdgeReveal?.classList.contains('visible')) hideSmartEdgeReveal();
}, true);
window.addEventListener('pagehide', () => flushPendingCanvasSaveOnExit());
document.addEventListener('visibilitychange', () => {
    if(document.visibilityState === 'hidden') flushPendingCanvasSaveOnExit();
});
smartUndoButton?.addEventListener('click', () => { void performUndo(); });
smartRedoButton?.addEventListener('click', () => { void performRedo(); });
smartSaveButton?.addEventListener('click', event => {
    event.preventDefault();
    void manualSaveCanvas();
});
setupSmartHistoryDrawer();
window.onload = async () => {
    applyTheme(localStorage.getItem('studio_theme') || localStorage.getItem('canvas_theme') || 'light');
    loadPromptPresets();
    loadPromptTemplateGroups();
    loadPromptTemplateOverrides();
    await loadPromptTemplates();
    if(window.StudioI18n) window.StudioI18n.apply();
    if(window.lucide) lucide.createIcons();
    await loadAutosavePreferences();
    connectAssetLibrarySyncSocket();
    await loadConfig();
    await loadAssetLibrary();
    const canvasLoaded = await loadCanvas();
    if(canvasLoaded){
        syncApiKindToggleVisibility();
        render();
    }
};
