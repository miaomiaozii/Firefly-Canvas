// canvas-core.js — 画布前端的共享底层（#173 核心下沉，classic 脚本，必须最先加载）
// 只放两类东西：无状态工具，以及跨模块共享的可变状态。
// 判据：名字被多个已拆出模块回调，且它自己不依赖 smart-canvas.js 的任何定义，才允许搬进来。

// 跨模块共享的可变状态。放在最前，因为后面的工具（如 cloneSmartSettings 的默认参数）会读它们。
const params = new URLSearchParams(location.search);

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

let canvas = null;
function tr(key){ return window.StudioI18n?.t ? window.StudioI18n.t(key) : key; }

function uid(prefix){ return `${prefix}_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`; }

function escapeHtml(str){ return String(str == null ? '' : str).replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }

const escapeAttr = escapeHtml;

function nowMs(){ return Date.now(); }

function selectedNode(){ return canvasState.nodes.find(n => n.id === canvasState.selectedId) || null; }

function isSmartImageNode(node){
    return Boolean(node && (node.type === NODE_TYPE_IMAGE || !node.type));
}

function isSmartGroupNode(node){
    return Boolean(node && node.type === NODE_TYPE_GROUP);
}

const MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE = 1.6;

const MEDIA_GROUP_DEFAULT_SCALE = 0.8;

function nodeScale(node){
    const v = Number(node?.scale);
    if((node?.images || []).length > 1 && v === MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE) return MEDIA_GROUP_DEFAULT_SCALE;
    return Number.isFinite(v) && v > 0 ? v : 1;
}

function cloneSmartSettings(source=settings){
    try {
        return JSON.parse(JSON.stringify(source || {}));
    } catch(e) {
        return {...(source || {})};
    }
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

// 生成提交标记读取器（#189 下沉：smart-canvas.js 顶层名被 canvas-generation-tasks.js
// 反向引用，按 tests/test_canvas_core_layer.py 的守卫要求下沉到 core 层）。
function smartGenerationSubmissions(node){
    return Array.isArray(node?.generationSubmissions)
        ? node.generationSubmissions.filter(item => item && item.idempotencyKey)
        : [];
}

// 提示条（#189 四道关卡下沉：canvas-generation-tasks.js 与 smart-canvas.js 都反向引用它，按 tests/test_canvas_core_layer.py 的守卫要求下沉到 core 层。只碰 #toast 元素与 setTimeout，无核心依赖。）
function toast(text){
    const el = document.getElementById('toast');
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.remove('show'), 1800);
}

// #197: pending-action persistence support (used by canvas-generation-tasks.js).
// A persisted mark survives the page reload, but its live `replay` closure does not:
// each gate's own module registers a rebuild closure (kind -> (entry, liveNode) => replayFn)
// here at load time, and registers the persister that commits the "entry cleared" node
// state to disk BEFORE the corresponding fetch fires (clear-on-send protocol). This file
// is the core layer and must not reference any smart-canvas.js symbol (layer guard).
const smartPendingActionRebuilders = new Map();
let smartPendingActionPersisterFn = null;
function registerSmartPendingActionRebuilder(kind, rebuild){
    if(kind && typeof rebuild === 'function') smartPendingActionRebuilders.set(String(kind), rebuild);
}
function smartPendingActionRebuilderFor(kind){
    return smartPendingActionRebuilders.get(String(kind || '')) || null;
}
function registerSmartPendingActionPersister(persister){
    if(typeof persister === 'function') smartPendingActionPersisterFn = persister;
}
function smartPendingActionPersister(){
    return smartPendingActionPersisterFn;
}
