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
    outputFormat:'',
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
// Pure authoritative copy shared by receive and undo; never a replayed view.
let smartCanvasServerShadow = null;
function tr(key){ return window.StudioI18n?.t ? window.StudioI18n.t(key) : key; }
function trf(key, values={}){
    return Object.entries(values).reduce((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), tr(key));
}

function uid(prefix){ return `${prefix}_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`; }

// #380：图标统一 SVG sprite（<symbol> 内联在 smart-canvas.html 顶部），id = i-<总表 key>。
function ico(id, cls){ return `<svg class="ico${cls ? ' ' + cls : ''}" aria-hidden="true"><use href="#i-${id}"/></svg>`; }
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
function toast(text, duration=1800){
    const el = document.getElementById('toast');
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.remove('show'), duration);
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

// #392 D3: pure execution-intent projection, shared by human copy and history.
// Missing optional arrays are legitimate pre-outbox documents. Present malformed
// arrays/records are data errors: never turn corruption into permission to copy.
function smartConversationPendingIntents(node){
    const intents = [];
    if(node.pendingActionResumes !== undefined){
        if(!Array.isArray(node.pendingActionResumes)) throw new TypeError('Invalid pendingActionResumes');
        for(const record of node.pendingActionResumes){
            if(!smartCanvasDataObject(record)) throw new TypeError('Invalid pending action record');
            if(record.kind !== 'conversation') continue;
            if(!smartCanvasDataObject(record.payload)) throw new TypeError('Invalid conversation intent payload');
            intents.push({source:'outbox', actionId:record.actionId, payload:smartCanvasDataClone(record.payload)});
        }
    }
    if(node.type === NODE_TYPE_CONVERSATION && node.messages !== undefined){
        if(!Array.isArray(node.messages)) throw new TypeError('Invalid conversation messages');
        for(const message of node.messages){
            if(!smartCanvasDataObject(message)) throw new TypeError('Invalid conversation message');
            // Same registered-user shape as core.py and the conversation UI:
            // legacy receipts may omit id, but local-* bubbles are never work.
            if(message.role === 'user' && message.status === 'pending' && message.request_id
                && !String(message.id === undefined ? '' : message.id).startsWith('local-')){
                intents.push({source:'message', requestId:message.request_id, payload:smartCanvasDataClone(message)});
            }
        }
    }
    return intents;
}

// #392: read-only defaults match app/services/agent_node_state.py::_ownership.
// Identifiers remain opaque; bool/string timestamps are not numeric timestamps.
function smartNodeOwnership(node){
    const agentId = typeof node?.agentNodeId === 'string' && node.agentNodeId.trim() ? node.agentNodeId : null;
    const isAgent = node?.createdBy === 'agent' && agentId !== null;
    const touched = node?.userTouchedAt;
    return {
        createdBy:isAgent ? 'agent' : 'user',
        agentNodeId:isAgent ? agentId : null,
        userTouchedAt:typeof touched === 'number' && Number.isFinite(touched) && touched >= 0 ? touched : null,
        promptEditedByUser:node?.promptEditedByUser === true,
    };
}

function smartNewNodeOwnership(node, creation={}){
    if(!smartCanvasDataObject(creation)) throw new TypeError('Node creation context must be an object');
    const {createdBy='user', agentNodeId=null} = creation;
    if(createdBy !== 'user' && createdBy !== 'agent') throw new TypeError('Invalid node creation source');
    if(createdBy === 'agent' && (typeof agentNodeId !== 'string' || !agentNodeId.trim())) throw new TypeError('Agent creation requires a nonblank Agent node ID');
    return {
        ...smartCanvasDataClone(node || {}),
        createdBy,
        agentNodeId:createdBy === 'agent' ? agentNodeId : null,
        userTouchedAt:null,
        promptEditedByUser:false,
        created_at:Date.now(),
    };
}

// Explicit caller seam only: never called implicitly by save/render/clone.
// `at` is epoch milliseconds (Date.now), consistent with browser created_at.
function smartHumanEditedNode(before, after, {prompt=false, at}={}){
    const result = smartCanvasDataClone(after || {});
    const ownershipKeys = ['createdBy', 'agentNodeId', 'userTouchedAt', 'promptEditedByUser'];
    ownershipKeys.forEach(key => {
        if(Object.prototype.hasOwnProperty.call(before || {}, key)) result[key] = smartCanvasDataClone(before[key]);
        else delete result[key];
    });
    if(smartCanvasDataEqual(smartHumanEditContent(before), smartHumanEditContent(after))) return result;
    const timestamp = at === undefined ? Date.now() : at;
    if(typeof timestamp !== 'number' || !Number.isFinite(timestamp) || timestamp < 0) throw new TypeError('Human edit timestamp must be nonnegative epoch milliseconds');
    result.userTouchedAt = timestamp;
    if(prompt && !smartCanvasDataEqual(smartHumanEditContent(before, {prompt:true}), smartHumanEditContent(after, {prompt:true}))) result.promptEditedByUser = true;
    return result;
}

// Default durable comparison shared with the phase 2a helper. An injected
// storage projection is already durable: exclude ownership, not its other keys.
function smartHumanEditContent(node, {transient=true, prompt=false}={}){
    const ownershipKeys = ['createdBy', 'agentNodeId', 'userTouchedAt', 'promptEditedByUser'];
    const transientKeys = ['running', 'pending', 'queued', 'pendingTasks', 'runStartedAt', 'runFinishedAt', 'runElapsedMs', 'runTimerHidden', '_runMetaTargetId', 'conversationSending', 'agentPlanning', 'agentExecuting'];
    const promptKeys = ['text', 'prompt', 'promptDraftHtml', 'promptDraftText', 'llmSystemPrompt', 'llmInstruction', 'variablePrompt', 'variablePrompts', 'promptSeparator', 'promptSplitEnabled', 'llmSystemEnabled'];
    const clean = Object.fromEntries(Object.keys(node || {}).filter(key => !ownershipKeys.includes(key)
        && (!transient || !transientKeys.includes(key)) && (!prompt || promptKeys.includes(key))).map(key => [key, node[key]]));
    if(transient){
        const mediaContent = item => smartCanvasDataObject(item)
            ? Object.fromEntries(Object.keys(item).filter(key => !['_naturalSizeLoading', 'layout_w', 'layout_h'].includes(key)).map(key => [key, item[key]])) : item;
        if(Array.isArray(clean.images)) clean.images = clean.images.map(mediaContent);
        if(clean.inputImage) clean.inputImage = mediaContent(clean.inputImage);
    }
    return clean;
}

// Explicit action scope, not a canvas-wide diff. No pending/save/undo side effects.
// `fields` lists top-level persisted node fields, AFTER the injected node projection.
function smartHumanEditFields(node, fields){
    const content = smartHumanEditContent(node, {transient:false});
    return fields === undefined ? content : Object.fromEntries(fields
        .filter(key => Object.prototype.hasOwnProperty.call(content, key)).map(key => [key, content[key]]));
}
// The caller must inject its actual storage projector; guessing here would
// stamp UI caches (for example _leBusy) that never reach durable storage.
function beginSmartHumanEdit(nodes, {prompt=false, at, fields, project}={}){
    if(fields !== undefined && (!Array.isArray(fields) || fields.some(key => typeof key !== 'string' || !key))) throw new TypeError('Human edit fields must be node field names');
    if(typeof project !== 'function') throw new TypeError('Human edit projection must be a function');
    return {targets:nodes.map(node => ({id:node.id, before:smartCanvasDataClone(smartHumanEditFields(project(node), fields))})), prompt:prompt === true, at, fields:smartCanvasDataClone(fields), project};
}
function finishSmartHumanEdit(action, liveNodes){
    const edits = [];
    for(const target of action.targets){
        const node = liveNodes.find(item => item.id === target.id);
        if(!node) continue;
        const after = smartHumanEditFields(action.project(node), action.fields);
        if(smartCanvasDataEqual(target.before, after)) continue;
        const prompt = action.prompt && !smartCanvasDataEqual(smartHumanEditContent(target.before, {prompt:true, transient:false}), smartHumanEditContent(after, {prompt:true, transient:false}));
        edits.push({node, prompt});
    }
    if(!edits.length) return false;
    const timestamp = action.at === undefined ? Date.now() : action.at;
    if(typeof timestamp !== 'number' || !Number.isFinite(timestamp) || timestamp < 0) throw new TypeError('Human edit timestamp must be nonnegative epoch milliseconds');
    edits.forEach(({node, prompt}) => {
        node.userTouchedAt = timestamp;
        if(prompt) node.promptEditedByUser = true;
    });
    return true;
}

// #392: pure action deltas. Paths use [collection, {id}, ...fieldNames],
// or {edge:[source,target,sourcePort,targetPort]} for legacy ID-less edges.
// Never array offsets. All other arrays are indivisible value leaves.
function smartCanvasDataClone(value){
    if(Array.isArray(value)) return value.map(smartCanvasDataClone);
    if(value && typeof value === 'object'){
        return Object.fromEntries(Object.keys(value).map(key => [key, smartCanvasDataClone(value[key])]));
    }
    return value;
}
function smartCanvasDataObject(value){
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function smartCanvasDataEqual(left, right){
    if(Object.is(left, right)) return true;
    if(Array.isArray(left) !== Array.isArray(right)) return false;
    if(!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length && keys.every(key =>
        Object.prototype.hasOwnProperty.call(right, key) && smartCanvasDataEqual(left[key], right[key]));
}
function smartCanvasEntitySelector(entity, legacy=false){
    if(!legacy && entity.id !== undefined && entity.id !== null && entity.id !== '') return {id:entity.id};
    return {edge:[
        entity.from ?? entity.source ?? entity.fromNode ?? entity.sourceNodeId ?? '',
        entity.to ?? entity.target ?? entity.toNode ?? entity.targetNodeId ?? '',
        entity.fromPort ?? entity.sourcePort ?? '',
        entity.toPort ?? entity.targetPort ?? '',
    ]};
}
function canvasUndoContents(snapshot){
    return {nodes:snapshot.nodes, connections:snapshot.connections || [], annotations:snapshot.annotations || []};
}
function smartCanvasActionDelta(beforeCanvas, afterCanvas){
    const changes = [];
    function leaf(before, after, path, beforePresent, afterPresent){
        const change = {path, beforePresent, afterPresent};
        if(beforePresent) change.before = smartCanvasDataClone(before);
        if(afterPresent) change.after = smartCanvasDataClone(after);
        changes.push(change);
    }
    function visit(before, after, path, beforePresent, afterPresent){
        if(beforePresent === afterPresent && smartCanvasDataEqual(before, after)) return;
        const entityPresenceChange = path.length === 2 && (path[0] === 'nodes' || path[0] === 'connections') && (!beforePresent || !afterPresent);
        const oldObject = beforePresent && smartCanvasDataObject(before);
        const newObject = afterPresent && smartCanvasDataObject(after);
        if(!entityPresenceChange && (oldObject || newObject)){
            // Empty-object presence markers keep missing/null distinct, while
            // every nested array still reaches the injected collection resolver.
            if(!oldObject) leaf(before, {}, path, beforePresent, true);
            const oldFields = oldObject ? before : {};
            const newFields = newObject ? after : {};
            const keys = new Set([...Object.keys(oldFields), ...Object.keys(newFields)]);
            keys.forEach(key => visit(oldFields[key], newFields[key], [...path, key],
                Object.prototype.hasOwnProperty.call(oldFields, key), Object.prototype.hasOwnProperty.call(newFields, key)));
            if(!newObject) leaf({}, after, path, true, afterPresent);
            return;
        }
        leaf(before, after, path, beforePresent, afterPresent);
    }
    const before = beforeCanvas || {};
    const after = afterCanvas || {};
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    keys.forEach(key => {
        if((key === 'nodes' || key === 'connections') && (Array.isArray(before[key]) || Array.isArray(after[key]))){
            const beforeArray = Array.isArray(before[key]);
            const afterArray = Array.isArray(after[key]);
            // Container markers contain only []; entities remain separate ID
            // operations, even on the first action of an empty legacy canvas.
            if(!beforeArray) visit(before[key], [], [key], Object.prototype.hasOwnProperty.call(before, key), true);
            const index = items => {
                const mapped = new Map();
                for(const item of items){
                    const identity = smartCanvasDataObject(item) ? JSON.stringify(smartCanvasEntitySelector(item)) : '';
                    if(!identity || (key === 'nodes' && !Object.prototype.hasOwnProperty.call(JSON.parse(identity), 'id')) || mapped.has(identity)){
                        const error = new Error('Canvas entity identity is missing or ambiguous; no action was recorded.');
                        error.name = 'CanvasCollectionConflict';
                        throw error;
                    }
                    mapped.set(identity, item);
                }
                return mapped;
            };
            const oldItems = index(beforeArray ? before[key] : []);
            const newItems = index(afterArray ? after[key] : []);
            new Set([...oldItems.keys(), ...newItems.keys()]).forEach(identity => {
                visit(oldItems.get(identity), newItems.get(identity), [key, JSON.parse(identity)], oldItems.has(identity), newItems.has(identity));
            });
            if(!afterArray) visit([], after[key], [key], true, Object.prototype.hasOwnProperty.call(after, key));
        } else {
            visit(before[key], after[key], [key], Object.prototype.hasOwnProperty.call(before, key), Object.prototype.hasOwnProperty.call(after, key));
        }
    });
    return {version:1, changes};
}
function applySmartCanvasDelta(sourceCanvas, delta, {direction='forward', mergeValue, remoteCanvas}={}){
    const result = smartCanvasDataClone(sourceCanvas || {});
    const conflicts = [];
    // One lookup per collection, not a full entity scan for each field. Sets
    // retain ambiguity; the endpoint index includes subsequently assigned IDs.
    const entityIndexes = new Map();
    const remoteEntityIndexes = new Map();
    const identityFields = new Set(['id', 'from', 'source', 'fromNode', 'sourceNodeId', 'to', 'target', 'toNode', 'targetNodeId', 'fromPort', 'sourcePort', 'toPort', 'targetPort']);
    function updateIndex(index, item, add){
        for(const [map, selector] of [[index.primary, smartCanvasEntitySelector(item)], [index.legacy, smartCanvasEntitySelector(item, true)]]){
            const identity = JSON.stringify(selector);
            if(add){
                if(!map.has(identity)) map.set(identity, new Set());
                map.get(identity).add(item);
            } else {
                const matches = map.get(identity);
                matches?.delete(item);
                if(!matches?.size) map.delete(identity);
            }
        }
    }
    function indexFor(collection, remote=false){
        const source = (remote ? remoteCanvas : result)?.[collection];
        const indexes = remote ? remoteEntityIndexes : entityIndexes;
        let index = indexes.get(collection);
        if(index && index.source === source) return index;
        index = {source, items:Array.isArray(source) ? source : [], primary:new Map(), legacy:new Map()};
        index.items.forEach(item => updateIndex(index, item, true));
        indexes.set(collection, index);
        return index;
    }
    const orderedChanges = direction === 'backward' ? [...delta.changes].reverse() : delta.changes;
    changes: for(const change of orderedChanges){
        const present = direction === 'backward' ? change.beforePresent : change.afterPresent;
        const value = direction === 'backward' ? change.before : change.after;
        const path = change.path;
        let target = result;
        let offset = 0;
        let entityIndex = null;
        let entity = null;
        if(path.length === 1 && (path[0] === 'nodes' || path[0] === 'connections')){
            if(present && Array.isArray(value) && Array.isArray(result[path[0]])) continue;
            if((!present || !Array.isArray(value)) && Array.isArray(result[path[0]]) && result[path[0]].length){
                conflicts.push({name:'CanvasCollectionConflict', path:smartCanvasDataClone(path), message:'Restoring collection presence would remove unrelated remote entities.'});
                continue;
            }
        }
        if((path[0] === 'nodes' || path[0] === 'connections') && smartCanvasDataObject(path[1])){
            entityIndex = indexFor(path[0]);
            const items = entityIndex.items;
            const identity = JSON.stringify(path[1]);
            // ID-less legacy intent may meet a subsequently assigned server ID.
            // Endpoint fallback is allowed only if it still has a unique match.
            const matches = (path[1].edge ? entityIndex.legacy : entityIndex.primary).get(identity);
            if(matches?.size > 1){
                conflicts.push({name:'CanvasCollectionConflict', path:smartCanvasDataClone(path), message:'Remote entity identity is ambiguous; action remains pending.'});
                continue;
            }
            entity = matches?.values().next().value;
            if(path.length === 2){
                let restored = value;
                if(present && typeof mergeValue === 'function'){
                    const remoteIndex = indexFor(path[0], true);
                    const remoteMatches = (path[1].edge ? remoteIndex.legacy : remoteIndex.primary).get(identity);
                    if(remoteMatches?.size > 1){
                        conflicts.push({name:'CanvasCollectionConflict', path:smartCanvasDataClone(path), message:'Remote restoration identity is ambiguous; action remains pending.'});
                        continue;
                    }
                    const remote = remoteMatches?.values().next().value || entity;
                    if(remote) restored = mergeValue(direction === 'backward' ? change.after : change.before, value, remote);
                }
                if(present && !Array.isArray(result[path[0]])){
                    result[path[0]] = items;
                    entityIndex.source = items;
                }
                if(entity){
                    updateIndex(entityIndex, entity, false);
                    const position = items.indexOf(entity);
                    if(!present) items.splice(position, 1);
                    else {
                        items[position] = smartCanvasDataClone(restored);
                        updateIndex(entityIndex, items[position], true);
                    }
                } else if(present){
                    const added = smartCanvasDataClone(restored);
                    items.push(added);
                    updateIndex(entityIndex, added, true);
                }
                continue;
            }
            if(!entity){
                conflicts.push({name:'CanvasEntityMissing', path:smartCanvasDataClone(path), message:'Remote entity was deleted; field intent remains pending.'});
                continue;
            }
            target = entity;
            offset = 2;
        }
        for(let i = offset; i < path.length - 1; i++){
            if(!smartCanvasDataObject(target)){
                if(!present) continue changes; // A child under null/false is already absent.
                conflicts.push({name:'CanvasPathConflict', path:smartCanvasDataClone(path), message:'Remote parent is no longer an object; field intent remains pending.'});
                continue changes;
            }
            const key = path[i];
            if(!Object.prototype.hasOwnProperty.call(target, key)){
                if(!present) continue changes;
                Object.defineProperty(target, key, {value:{}, enumerable:true, writable:true, configurable:true});
            }
            target = target[key];
        }
        if(!smartCanvasDataObject(target)){
            if(!present) continue;
            conflicts.push({name:'CanvasPathConflict', path:smartCanvasDataClone(path), message:'Remote parent is no longer an object; field intent remains pending.'});
            continue;
        }
        const key = path[path.length - 1];
        const base = direction === 'backward' ? change.after : change.before;
        if(present && smartCanvasDataObject(value) && !Object.keys(value).length && smartCanvasDataObject(target[key])) continue;
        // Object presence markers run after their children on removal. Only an
        // empty remainder may restore missing/null/false; remote siblings win.
        if((!present || !smartCanvasDataObject(value)) && smartCanvasDataObject(base)
            && smartCanvasDataObject(target[key]) && Object.keys(target[key]).length) continue;
        const removingArray = Array.isArray(base) && (!present || !Array.isArray(value)) && Array.isArray(target[key]);
        const arrayValue = removingArray ? [] : value;
        let next = value;
        if((removingArray || (present && Array.isArray(value))) && !(path.length === 1 && (key === 'nodes' || key === 'connections'))){
            if(typeof mergeValue === 'function'){
                try {
                    next = mergeValue(smartCanvasDataClone(base), smartCanvasDataClone(arrayValue), smartCanvasDataClone(target[key]), key);
                } catch(error){
                    if(error?.name !== 'CanvasCollectionConflict') throw error;
                    conflicts.push({name:error.name, path:smartCanvasDataClone(path), message:String(error.message || 'Collection identity is ambiguous.')});
                    continue;
                }
            } else if(!smartCanvasDataEqual(target[key], base) && !smartCanvasDataEqual(target[key], arrayValue)){
                conflicts.push({name:'CanvasCollectionConflict', path:smartCanvasDataClone(path), message:'Concurrent collection edit requires an injected mergeValue resolver.'});
                continue;
            }
        }
        const keepRemoteMembers = removingArray && Array.isArray(next) && next.length > 0;
        if(removingArray && !keepRemoteMembers) next = value;
        const reindex = entityIndex && path.length === 3 && identityFields.has(key);
        if(reindex) updateIndex(entityIndex, entity, false);
        if(present || keepRemoteMembers) Object.defineProperty(target, key, {value:smartCanvasDataClone(next), enumerable:true, writable:true, configurable:true});
        else delete target[key];
        if(reindex) updateIndex(entityIndex, entity, true);
        if(path.length === 1) entityIndexes.delete(key);
    }
    return {canvas:result, conflicts};
}

// Runtime-only, caller-owned journal: record adjacent action snapshots, NOT a
// server shadow comparison. A later compensating action must survive an old ack
// even when its after value happens to equal the old server shadow.
function createSmartCanvasPendingJournal({mergeValue}={}){
    let seq = 0;
    let pending = [];
    return {
        record(beforeCanvas, afterCanvas, {actionId}={}){
            const delta = smartCanvasActionDelta(beforeCanvas, afterCanvas);
            if(!delta.changes.length) return null;
            const entry = {seq:++seq, delta};
            if(actionId !== undefined) entry.actionId = smartCanvasDataClone(actionId);
            pending.push(entry);
            return smartCanvasDataClone(entry);
        },
        checkpoint(){ return {seq}; },
        acknowledge(token){
            if(!Number.isSafeInteger(token?.seq) || token.seq < 0) throw new TypeError('Invalid canvas journal checkpoint');
            pending = pending.filter(entry => entry.seq > token.seq);
        },
        replay(serverCanvas){
            let replayed = smartCanvasDataClone(serverCanvas || {});
            const conflicts = [];
            for(const entry of pending){
                const applied = applySmartCanvasDelta(replayed, entry.delta, {mergeValue});
                replayed = applied.canvas;
                applied.conflicts.forEach(conflict => conflicts.push({...conflict, seq:entry.seq, actionId:smartCanvasDataClone(entry.actionId)}));
            }
            return {canvas:replayed, conflicts};
        },
        rewriteDeltas(rewrite, {afterSeq=0}={}){
            // Import is an identity reconciliation, not a new user action.
            // Publish only after every frozen delta has transformed safely.
            pending = pending.map(entry => entry.seq > afterSeq
                ? {...entry, delta:rewrite(smartCanvasDataClone(entry.delta))} : entry);
        },
        hasPending(){ return pending.length > 0; },
        entries(){ return smartCanvasDataClone(pending); },
    };
}

// #201 轨道 1（ADR 0020）：显式接口 window.canvasCore。
// 模块访问共享服务（smart-canvas.js 里被 >=2 个模块引用的顶层名字）统一走接口：
//     canvasCore.render()          // 而不是裸 render
//     canvasCore.shell             // 而不是裸 shell（值引用同样支持，不只是调用）
//     canvasCore.smartLoopContext  // let 绑定，读/写都落到原始绑定
// 收益不在引用处数（测量口径不变，见 tools/canvas_coupling_report.py），在可测性：
// 模块独立单测只需桩 canvasCore 一个对象，而不是 N 个独立外部名字
// （#194/#203 实测过的单测成本驱动因子）。
//
// 解析必须惰性：本文件先于 smart-canvas.js 加载（层守卫要求），而接口名字大多定义在
// smart-canvas.js 顶层，其中一部分是 let/const 词法绑定。在定义处直接引用它们会
// TDZ 报错或读到 undefined，所以所有解析都推迟到 canvasCore.NAME 被访问时（运行期，
// 全部脚本加载完成后）。
//
// 为什么用 new Function 而不是 window.NAME：顶层 function/var 在 window 上，但
// 顶层 let/const 只存在于全局词法环境（global declarative record），window.NAME
// 对它们是 undefined。new Function 构造的函数作用域链就是全局环境，两类绑定都能
// 按名解析到。缓存的是解析器（每次调用仍读绑定的当前值，重绑定的 let 不会读到
// 旧值），不是值本身。
window.canvasCore = (function () {
    const _resolvers = Object.create(null);
    function resolve(name) {
        let resolver = _resolvers[name];
        if (!resolver) {
            resolver = new Function("return " + name + ";");
            _resolvers[name] = resolver;
        }
        return resolver();
    }
    const IDENTIFIER = /^[A-Za-z_$][A-Za-z0-9_$]*$/;
    return new Proxy(Object.create(null), {
        get(_target, name) {
            // 只解析合法标识符；symbol/元属性探测一律返回 undefined。
            if (typeof name !== "string" || !IDENTIFIER.test(name)) return undefined;
            try {
                return resolve(name);
            } catch (_err) {
                // 名字不存在（拼错/已搬走）：返回 undefined 而不是抛 ReferenceError，
                // 行为与「读一个不存在的属性」一致，调用点该报的错在下一步调用时照报。
                return undefined;
            }
        },
        // canvasCore.NAME = v：写回原始全局绑定（如 smartLoopContext 是 let）。
        set(_target, name, value) {
            if (typeof name !== "string" || !IDENTIFIER.test(name)) return false;
            if (name in window) {
                window[name] = value;
                return true;
            }
            // let/const 绑定：new Function 的赋值落在全局词法环境（与裸赋值同语义）。
            // const 会抛 TypeError，与裸赋值行为一致，故不吞错。
            new Function("value", "return (" + name + " = value);")(value);
            return true;
        },
    });
})();
