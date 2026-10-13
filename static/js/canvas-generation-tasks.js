// 画布生成任务提交与轮询恢复（从 smart-canvas.js 抽离，#154）。
// 本文件只含函数定义与状态声明，无顶层执行代码；引用主脚本全局（nodes/canvas/selectedId/
// toast/tr/render/scheduleSave/canvasState 等）。先于 smart-canvas.js 加载，函数声明整体提升，
// 所有调用都发生在主脚本全局初始化之后。

let transientSmartCloudLinks = [];

const activeSmartTaskPolls = new Map();

const smartGenerationNodeRevisions = new Map();

const activeSmartSubmissionRecoveries = new Set();

const smartGenerationRegistrationQueues = new Map();

async function runPromptLLMNode(nodeId){
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if(!node || node.type !== 'smart-prompt') return;
    const projectId = canvas?.project_id || canvas?.project || canvasId || '';
    const message = promptNodeLLMInputText(node).trim();
    if(!message){ toast(tr('smart.promptLlmNeedText')); return; }
    const systemPrompt = (node.llmSystemPrompt || '').trim();
    node.llmEnabled = true;
    node.running = true;
    render();
    try {
        const provider = resolveChatProviderId(node.llmProvider || '');
        const model = resolveChatModel(node.llmModel || '', provider);
        const mediaRefs = promptNodeInputMediaForLLM(node);
        const images = imageRefsOnly(mediaRefs).map(img => img.url).filter(Boolean);
        const videos = videoRefsOnly(mediaRefs).map(video => video.url).filter(Boolean);
        const result = await fetch('/api/canvas-llm', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
                project_id:projectId,
                message,
                messages:[],
                images,
                videos,
                model,
                provider,
                ms_model: provider === 'modelscope' ? model : '',
                system_prompt:node.llmSystemEnabled ? (systemPrompt || 'You are a helpful prompt assistant.') : ''
            })
        }).then(async r => {
            if(!r.ok) throw new Error(await r.text());
            return r.json();
        });
        node.text = (result.text || '').trim();
        node.llmProvider = provider;
        node.llmModel = model;
        scheduleSave();
    } catch(e) {
        toast((e.message || tr('smart.promptLlmFailed')).slice(0, 160));
    } finally {
        node.running = false;
        render();
    }
}

async function queueSmartGenerationRegistration(nodeId, operation){
    const key = String(nodeId || '');
    const previous = smartGenerationRegistrationQueues.get(key) || Promise.resolve();
    const current = previous.catch(() => {}).then(operation);
    smartGenerationRegistrationQueues.set(key, current);
    try {
        return await current;
    } finally {
        if(smartGenerationRegistrationQueues.get(key) === current){
            smartGenerationRegistrationQueues.delete(key);
        }
    }
}

function upsertSmartGenerationSubmission(node, submission){
    if(!node || !submission?.idempotencyKey) return null;
    const key = String(submission.idempotencyKey);
    const current = smartGenerationSubmissions(node).filter(item => String(item.idempotencyKey) !== key);
    const saved = {...submission, idempotencyKey:key, outcome:'unknown'};
    node.generationSubmissions = [...current, saved];
    node.pending = Math.max(Number(node.pending || 0), node.generationSubmissions.length);
    return saved;
}

function clearSmartGenerationSubmission(node, idempotencyKey){
    if(!node || !idempotencyKey) return;
    const remaining = smartGenerationSubmissions(node).filter(item => String(item.idempotencyKey) !== String(idempotencyKey));
    if(remaining.length) node.generationSubmissions = remaining;
    else delete node.generationSubmissions;
}

async function postProjectImageTaskWithReplay(projectId, payload){
    let lastError = null;
    for(let attempt = 0; attempt < 2; attempt += 1){
        try {
            const response = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/image-generation-tasks`, {
                method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
            });
            const data = await response.json().catch(() => ({}));
            if(response.ok) return data;
            const error = new Error(apiErrorMessage(data, tr('smart.errRunFailed')));
            if(response.status < 500){
                error.smartSubmissionRejected = true;
                throw error;
            }
            lastError = error;
        } catch(error) {
            if(error?.smartSubmissionRejected) throw error;
            if(error?.message && !lastError) lastError = error;
            if(attempt >= 1 || error?.name === 'AbortError') break;
        }
        await sleep(250);
    }
    const unknown = new Error(trf('smart.submitUnknownRecoverDetail', {m: lastError?.message || tr('smart.networkInterrupted')}));
    unknown.smartSubmissionUnknown = true;
    unknown.idempotencyKey = payload.idempotency_key;
    throw unknown;
}

async function runApiGeneration(prompt, refs, runSettings=settings, resultNode=null, runOptions={}){
    const count = Math.max(1, Math.min(8, Number(runSettings.count || 1)));
    const pendingHistoryId = runOptions.pendingHistoryId || beginSmartPendingHistoryLog(resultNode, {
            prompt,
            kind:'image',
            providerId:runSettings.providerId || runSettings.provider_id || '',
            model:runSettings.model || '',
            runSettings
        });
    if(canvas?.format === 'firefly-canvas-v1' && !runOptions.registrationQueued){
        if(!resultNode?.id) throw new Error(tr('smart.projectImageMissingResult'));
        return queueSmartGenerationRegistration(resultNode.id, () => runApiGeneration(prompt, refs, runSettings, resultNode, {
            ...runOptions,
            pendingHistoryId,
            registrationQueued:true
        }));
    }
    if(canvas?.format === 'firefly-canvas-v1'){
        if(!resultNode?.id) throw new Error(tr('smart.projectImageMissingResult'));
        const outpaintSize = validOutpaintSize(resultNode);
        const usesExplicitResolution = !outpaintSize && apiImageUsesExplicitResolution(runSettings);
        // 扩图时不发送 size/width/height，只发送 aspect_ratio 和 resolution
        const size = usesExplicitResolution ? sizeForRun(runSettings) : '';
        const parsed = usesExplicitResolution ? parseSizeValue(size) : null;
        if(usesExplicitResolution && !parsed) throw new Error(tr('smart.imageSizeInvalid'));
        const ownedRefs = await Promise.all(imageRefsOnly(refs).slice(0, SMART_REFERENCE_IMAGE_MAX).map(importProjectMediaReference));
        const reference_asset_ids = ownedRefs.filter(Boolean).map(item => item.asset_id).filter(Boolean);
        if(reference_asset_ids.length !== imageRefsOnly(refs).slice(0, SMART_REFERENCE_IMAGE_MAX).length) throw new Error(tr('smart.refsNotInProject'));
        const projectId = canvas.project_id || canvas.project || canvasId;
        const payload = {
            prompt,
            routing_mode:runSettings.routingMode === 'manual' ? 'manual' : 'auto',
            provider_id:runSettings.routingMode === 'manual' ? (runSettings.provider_id || '') : '',
            model:runSettings.routingMode === 'manual' ? (runSettings.model || '') : '',
            size,
            width:parsed?.width || null,
            height:parsed?.height || null,
            aspect_ratio:aspectRatioForRun(runSettings),
            resolution:usesExplicitResolution ? (runSettings.resolution || '') : '',
            quality:runSettings.quality || 'auto',
            n:count,
            reference_asset_ids,
            media_type:'image',
            result_node_id:resultNode.id,
            angle_preset:runSettings.anglePreset || 'none',
            base_revision:Number(canvas.revision || 0),
            idempotency_key:runOptions.idempotencyKey || `browser:${projectId}:${pendingHistoryId || uid('generation')}`
        };
        const submission = upsertSmartGenerationSubmission(resultNode, {
            idempotencyKey:payload.idempotency_key,
            requestPayload:{...payload},
            pendingHistoryId,
            count,
            prompt,
            createdAt:nowMs()
        });
        scheduleSave();
        if(!await flushProjectCanvasBeforeGeneration()){
            clearSmartGenerationSubmission(resultNode, payload.idempotency_key);
            scheduleSave();
            throw new Error(tr('smart.canvasUnsavedImageStopped'));
        }
        // The durable submission marker was saved at the previous revision.
        // A first-time replay uses the latest revision; duplicate resolution
        // ignores this mutable field and still returns the original task.
        payload.base_revision = Number(canvas.revision || payload.base_revision || 0);
        if(submission) submission.requestPayload = {...payload};
        const task = await postProjectImageTaskWithReplay(projectId, payload);
        const taskResult = {taskIds:[task.task_id].filter(Boolean), count, providerId:task.route?.provider_id || '', model:task.route?.model || '', route:task.route, projectRevision:task.project_revision, registeredNode:task.registered_node, pendingHistoryId, idempotencyKey:task.idempotency_key || payload.idempotency_key};
        await acceptProjectGenerationRegistration(taskResult);
        // 新运行重置计时器：项目 API 提交路径不经过 runCascadeStepIntoNode，
        // 必须显式重置，否则持久化节点会带着上次生成的旧 runStartedAt 无限涨。
        const refreshedNode = canvasState.nodes.find(item => item.id === resultNode.id) || resultNode;
        refreshedNode.runStartedAt = nowMs();
        delete refreshedNode.runFinishedAt;
        delete refreshedNode.runElapsedMs;
        refreshedNode.runTimerHidden = false;
        clearSmartGenerationSubmission(canvasState.nodes.find(item => item.id === resultNode.id) || resultNode, payload.idempotency_key);
        scheduleSave();
        return taskResult;
    }
    if(!runSettings.provider_id || !runSettings.model) throw new Error(tr('smart.errNoApiModel'));
    const payload = {prompt, provider_id:runSettings.provider_id, model:runSettings.model, size:sizeForRun(runSettings), quality:runSettings.quality || 'auto', n:1, reference_images:imageRefsOnly(refs).slice(0, SMART_REFERENCE_IMAGE_MAX)};
    const tasks = await Promise.all(Array.from({length:count}, () => fetch('/api/canvas-image-tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}).then(async r => {
        if(!r.ok) throw new Error(await r.text());
        return r.json();
    })));
    return {taskIds:tasks.map(task => task.task_id).filter(Boolean), count, providerId:payload.provider_id, model:payload.model};
}

async function runRunningHubGeneration(prompt, refs, runSettings=settings){
    const ref = selectedRunningHubRef(runSettings);
    if(!ref) throw new Error(tr('smart.rhNeedConfig'));
    const fields = rhActiveFields(runSettings);
    if(!fields.length) throw new Error(tr('smart.rhNeedFields'));
    const randomValues = {};
    const mode = ref.kind === 'app' ? 'app' : 'model';
    if(mode !== 'app') throw new Error(tr('smart.rhNeedConfig'));
    const media = rhMediaForRun(prompt, refs);
    const nodeInfoList = await rhBuildNodeInfoList(media, runSettings, randomValues);
    const endpoint = '/api/runninghub/submit';
    const body = {webappId:ref.id, nodeInfoList, instanceType:runSettings.rhInstanceType || '', useWallet:runSettings.rhPayment === 'wallet'};
    const submit = await fetch(endpoint, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body)
    }).then(async r => {
        const data = await r.json();
        if(!r.ok || data.success === false) throw new Error(data.detail || data.error || tr('smart.rhFailed'));
        return data.data || data;
    });
    const taskId = submit.taskId;
    if(!taskId) throw new Error(tr('smart.rhNoTaskId'));
    const useWallet = runSettings.rhPayment === 'wallet';
    for(let i = 0; i < 720; i++){
        await sleep(2500);
        const data = await fetch(`/api/runninghub/query?taskId=${encodeURIComponent(taskId)}&useWallet=${useWallet ? '1' : '0'}`).then(async r => {
            const json = await r.json();
            if(!r.ok || json.success === false) throw new Error(json.detail || json.error || tr('smart.rhFailed'));
            return json.data || json;
        });
        if(data.status === 'SUCCESS'){
            const urls = resultMediaUrls(data.image_items?.length ? data.image_items : (data.urls || []));
            if(!urls.length) throw new Error(tr('smart.rhOutputsEmpty'));
            return urls;
        }
        if(data.status === 'FAILED') throw new Error(data.failReason || tr('smart.rhFailed'));
    }
    throw new Error(tr('smart.rhTimeout'));
}

function projectVideoSizeForRun(runSettings=settings){
    const ratio = runSettings.videoAspect || '16:9';
    const base = {
        '16:9':[1280,720], '9:16':[720,1280], '1:1':[720,720],
        '4:3':[960,720], '3:4':[720,960], '21:9':[1280,544], '9:21':[544,1280]
    }[ratio] || [1280,720];
    const resolution = String(runSettings.videoResolution || '720p').toLowerCase();
    const scale = resolution === '480p' ? 2 / 3 : resolution === '1080p' ? 1.5 : 1;
    const width = Math.max(64, Math.round(base[0] * scale / 8) * 8);
    const height = Math.max(64, Math.round(base[1] * scale / 8) * 8);
    return {width, height, size:`${width}x${height}`};
}

async function runApiVideoGeneration(prompt, refs, runSettings=settings, resultNode=null){
    if(!runSettings.videoModel) throw new Error(tr('smart.errNoVideoModel'));
    if(canvas?.format === 'firefly-canvas-v1'){
        if(!resultNode?.id) throw new Error(tr('smart.projectVideoMissingResult'));
        if(!await flushProjectCanvasBeforeGeneration()) throw new Error(tr('smart.canvasUnsavedVideoStopped'));
        const projectRefs = (refs || []).filter(ref => ['image','video','audio'].includes(mediaKindForItem(ref)));
        const ownedRefs = await Promise.all(projectRefs.map(ref => importProjectMediaReference(ref)));
        if(ownedRefs.some(item => !item?.asset_id) || ownedRefs.length !== projectRefs.length) throw new Error(tr('smart.videoRefsNotInProject'));
        const projectId = canvas.project_id || canvas.project || canvasId;
        const size = projectVideoSizeForRun(runSettings);
        const payload = {
            prompt,
            routing_mode:(runSettings.engine === 'volcengine' || runSettings.videoRoutingMode === 'manual') ? 'manual' : 'auto',
            provider_id:(runSettings.engine === 'volcengine' || runSettings.videoRoutingMode === 'manual') ? (runSettings.videoProvider || '') : '',
            model:(runSettings.engine === 'volcengine' || runSettings.videoRoutingMode === 'manual') ? (runSettings.videoModel || '') : '',
            size:size.size,
            width:size.width,
            height:size.height,
            aspect_ratio:runSettings.videoAspect || '16:9',
            resolution:runSettings.videoResolution || '',
            duration:Math.max(1, Math.min(600, Number(runSettings.videoDuration) || 5)),
            quality:runSettings.videoQuality || runSettings.quality || 'auto',
            n:1,
            seed:Number.isFinite(Number(runSettings.videoSeed)) ? Number(runSettings.videoSeed) : null,
            enhance_prompt:Boolean(runSettings.videoEnhancePrompt),
            enable_upsample:Boolean(runSettings.videoEnableUpsample),
            watermark:Boolean(runSettings.videoWatermark),
            camera_fixed:Boolean(runSettings.videoCameraFixed),
            return_last_frame:Boolean(runSettings.videoReturnLastFrame),
            generate_audio:Boolean(runSettings.videoGenerateAudio),
            image_reference_asset_ids:ownedRefs.filter(item => mediaKindForItem(item) === 'image').map(item => item.asset_id),
            video_reference_asset_ids:ownedRefs.filter(item => mediaKindForItem(item) === 'video').map(item => item.asset_id),
            audio_reference_asset_ids:ownedRefs.filter(item => mediaKindForItem(item) === 'audio').map(item => item.asset_id),
            media_type:'video',
            result_node_id:resultNode.id,
            base_revision:Number(canvas.revision || 0)
        };
        const task = await fetch(`/api/project-library/${encodeURIComponent(projectId)}/video-generation-tasks`, {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
        }).then(async response => {
            const data = await response.json().catch(() => ({}));
            if(!response.ok) throw new Error(apiErrorMessage(data, tr('smart.errRunFailed')));
            return data;
        });
        return {taskIds:[task.task_id].filter(Boolean), count:1, providerId:task.route?.provider_id || '', model:task.route?.model || '', route:task.route, projectRevision:task.project_revision, registeredNode:task.registered_node, kind:'video'};
    }
    try {
        const uploadedRefs = applyUploadedUrlsToSmartRefs(refs, runSettings);
        const trustedMode = Boolean(runSettings.videoTrustedAsset);
        const trustedSource = trustedMode ? (['library','cloud','manual'].includes(runSettings.videoTrustedSource) ? runSettings.videoTrustedSource : 'library') : 'none';
        // 仅「素材库链接」来源才走 asset:// 认证地址 + 后端可信素材路由；上传云端/手动网址走普通直链。
        const useAssetUris = trustedSource === 'library';
        const targetPlatform = videoProviderPlatform(runSettings.videoProvider || 'comfly');
        let mismatchedAsset = false;
        const effUrl = ref => {
            const uris = (ref && ref.asset_uris && typeof ref.asset_uris === 'object') ? ref.asset_uris : null;
            if(useAssetUris && uris && Object.keys(uris).length){
                // asset:// 与平台绑定：取当前视频平台对应的认证地址；该素材没注册到这个平台就回退本地 url
                if(targetPlatform && uris[targetPlatform]) return uris[targetPlatform];
                mismatchedAsset = true;
            }
            return ref?.url;
        };
        const refImages = imageRefsOnly(uploadedRefs).map((ref, i) => {
            const item = {url:effUrl(ref), name:ref.name || `图${i + 1}`};
            if(runSettings.videoUseFrameRoles){
                if(i === 0) item.role = 'first_frame';
                else if(i === 1) item.role = 'last_frame';
            }
            return item;
        });
        const manualVideo = manualSmartVideoLink(runSettings)?.url || '';
        const refVideos = manualVideo ? manualSmartMediaLinks(runSettings).map(item => item.url).filter(Boolean) : videoRefsOnly(uploadedRefs).map(ref => effUrl(ref)).filter(Boolean);
        const refAudios = audioRefsOnly(uploadedRefs).map(ref => effUrl(ref)).filter(Boolean).slice(0, 3);
        const videoSize = projectVideoSizeForRun(runSettings);
        if(mismatchedAsset) toast(tr('smart.assetPlatformMismatch'));
        const payload = {
            prompt,
            provider_id: runSettings.videoProvider || 'comfly',
            model: runSettings.videoModel || 'veo3-fast',
            size:videoSize.size,
            width:videoSize.width,
            height:videoSize.height,
            duration: Math.max(1, Math.min(60, Number(runSettings.videoDuration) || 5)),
            aspect_ratio: runSettings.videoAspect || '16:9',
            resolution: runSettings.videoResolution || '',
            quality:runSettings.videoQuality || runSettings.quality || 'auto',
            media_type:'video',
            images: refImages,
            videos: refVideos,
            audios: refAudios,
            enhance_prompt: Boolean(runSettings.videoEnhancePrompt),
            enable_upsample: Boolean(runSettings.videoEnableUpsample),
            watermark: Boolean(runSettings.videoWatermark),
            camerafixed: Boolean(runSettings.videoCameraFixed),
            return_last_frame: Boolean(runSettings.videoReturnLastFrame),
            generate_audio: Boolean(runSettings.videoGenerateAudio),
            multimodal: Boolean(runSettings.videoMultimodal),
            trusted_asset: useAssetUris
        };
        const result = await fetch('/api/canvas-video', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(payload)
        }).then(async r => { if(!r.ok) throw new Error(await smartResponseErrorMessage(r, tr('smart.errRunFailed'))); return r.json(); });
        if(result && result.jimeng_pending) throw new JimengPendingSignal({submitId:result.submit_id, kind:result.kind || 'video', queueInfo:result.queue_info, message:result.message});
        return resultMediaUrls(result);
    } finally {
        transientSmartCloudLinks = [];
    }
}

function smartPendingTasks(node){
    if(!node || !Array.isArray(node.pendingTasks)) return [];
    return node.pendingTasks.filter(task => task && task.taskId);
}

function projectImageTaskUrl(taskId){
    const projectId = canvas?.project_id || canvas?.project || canvasId;
    const tid = String(taskId || '');
    if(canvas?.format === 'firefly-canvas-v1' && tid.startsWith('project_img_')){
        return `/api/project-library/${encodeURIComponent(projectId)}/image-generation-tasks/${encodeURIComponent(taskId)}`;
    }
    if(canvas?.format === 'firefly-canvas-v1' && tid.startsWith('project_vid_')){
        return `/api/project-library/${encodeURIComponent(projectId)}/video-generation-tasks/${encodeURIComponent(taskId)}`;
    }
    return `/api/canvas-image-tasks/${encodeURIComponent(taskId)}`;
}

class JimengPendingSignal extends Error {
    constructor(info){
        const data = info || {};
        super(data.message || tr('smart.jimengQueuedFallback'));
        this.jimengPending = true;
        this.submitId = data.submitId || data.submit_id || '';
        this.kind = data.kind || 'image';
        this.queueInfo = data.queueInfo || data.queue_info || {};
    }
}

class ImageTaskRecoverSignal extends Error {
    constructor(info){
        const data = info || {};
        super(data.message || tr('smart.taskNotLostLater'));
        this.imageTaskRecover = true;
        this.taskId = data.taskId || data.task_id || '';
        this.recoverTaskId = data.recoverTaskId || data.upstream_task_id || data.task_id || '';
        this.providerId = data.providerId || data.provider_id || '';
        this.kind = data.kind || 'image';
    }
}

function extractUpstreamTaskId(text){
    const match = String(text || '').match(/(?:task_id|taskId|task id)\s*[=:：]\s*([A-Za-z0-9_.:-]+)/i);
    return match ? match[1] : '';
}

const activeJimengPolls = new Set();

const JIMENG_POLL_INTERVAL = 60000;

const JIMENG_POLL_MAX = 1440;

function jimengQueueText(queueInfo){
    const qi = queueInfo || {};
    const idx = qi.queue_idx;
    const len = qi.queue_length;
    if(idx != null && len != null) return trf('smart.jimengCloudQueued', {idx: idx, len: len});
    return tr('smart.jimengCloudGenerating');
}

function setNodeJimengPending(node, signal){
    if(!node || !signal || !signal.submitId) return;
    const prev = node.jimengPending && node.jimengPending.submitId === signal.submitId ? node.jimengPending : null;
    node.jimengPending = {
        submitId:signal.submitId,
        kind:signal.kind || (prev && prev.kind) || 'image',
        queueInfo:signal.queueInfo || (prev && prev.queueInfo) || {},
        message:signal.message || (prev && prev.message) || '',
        startedAt:(prev && prev.startedAt) || nowMs(),
        updatedAt:nowMs(),
        querying:prev ? prev.querying : false
    };
    node.running = false;
    node.pending = 0;
    delete node.pendingTasks;
    if(!node.runStartedAt) node.runStartedAt = node.jimengPending.startedAt;
    delete node.runFinishedAt;
    delete node.runElapsedMs;
    node.runTimerHidden = false;
    render();
    scheduleStateSave();
    startJimengPoll(node);
}

function handleJimengPendingSignal(node, e){
    if(!(e && e.jimengPending && e.submitId)) return false;
    setNodeJimengPending(node, e);
    toast((e.message || jimengQueueText(e.queueInfo)).slice(0, 160));
    return true;
}

function finalizeJimengPending(node, urls, kind='image'){
    if(!node) return false;
    const ext = kind === 'video' ? 'mp4' : kind === 'audio' ? 'mp3' : kind === 'text' ? 'txt' : 'png';
    const additions = (urls || []).map((item, i) => {
        const url = typeof item === 'string' ? item : item?.url || '';
        const itemKind = (typeof item === 'object' && item.kind) || kind;
        return stripImageGenerationMeta(copyMediaSizeFields(item, {url, name:(typeof item === 'object' && item.name) || `output-${i + 1}.${ext}`, kind:itemKind, generatedResult:true, ...(typeof item === 'object' && item.mime_type ? {mime_type:item.mime_type} : {})}));
    }).filter(item => item.url);
    if(!additions.length) return false;
    delete node.jimengPending;
    replaceOutputsToNodeWithHistory(node, additions, kind, null, {skipShift:true});
    node.running = false;
    node.pending = 0;
    node.runFinishedAt = nowMs();
    if(!node.runStartedAt) node.runStartedAt = node.runFinishedAt;
    node.runElapsedMs = Math.max(0, node.runFinishedAt - Number(node.runStartedAt || node.runFinishedAt));
    node.runTimerHidden = false;
    render();
    scheduleSave();
    return true;
}

function applyJimengQueryResult(node, data){
    if(!node || !data) return false;
    if(data.status === 'succeeded'){
        const kind = data.kind || node.jimengPending?.kind || 'image';
        return finalizeJimengPending(node, data.urls || [], kind);
    }
    if(data.status === 'failed'){
        delete node.jimengPending;
        node.running = false;
        node.pending = 0;
        toast((data.error || tr('smart.jimengTaskFailed')).slice(0, 160));
        render();
        scheduleStateSave();
        return true;
    }
    if(node.jimengPending){
        node.jimengPending.queueInfo = data.queue_info || node.jimengPending.queueInfo || {};
        node.jimengPending.message = data.message || node.jimengPending.message || '';
        node.jimengPending.updatedAt = nowMs();
    }
    render();
    scheduleStateSave();
    return false;
}

async function fetchJimengQuery(submitId, kind){
    return fetch('/api/jimeng/query-media', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({submit_id:submitId, kind:kind || 'image'})
    }).then(async r => { if(!r.ok) throw new Error(await r.text()); return r.json(); });
}

async function queryJimengNow(nodeId){
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if(!node || !node.jimengPending || !node.jimengPending.submitId) return;
    if(node.jimengPending.querying) return;
    const submitId = node.jimengPending.submitId;
    const kind = node.jimengPending.kind || 'image';
    node.jimengPending.querying = true;
    render();
    try {
        const data = await fetchJimengQuery(submitId, kind);
        applyJimengQueryResult(node, data);
    } catch(e){
        toast((e.message || tr('smart.queryFailed')).slice(0, 160));
    } finally {
        if(node.jimengPending) node.jimengPending.querying = false;
        render();
    }
}

function providerIdForSmartTask(node, task){
    return task?.providerId || node?.runSettings?.provider_id || settings.provider_id || 'comfly';
}

async function fetchImageTaskQuery(providerId, taskId){
    return fetch('/api/image-task-query', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({provider_id:providerId || 'comfly', task_id:taskId})
    }).then(async r => {
        if(!r.ok) throw new Error(await r.text());
        return r.json();
    });
}

async function querySmartImageTaskNow(nodeId, localTaskId){
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if(!node) return;
    const task = smartPendingTasks(node).find(item => item.taskId === localTaskId) || smartRecoverableImageTask(node);
    if(!task || task.querying) return;
    const recoverTaskId = task.recoverTaskId || extractUpstreamTaskId(task.error || '');
    if(!recoverTaskId){
        toast(tr('smart.queryMissingTaskId'));
        return;
    }
    task.querying = true;
    task.recoverTaskId = recoverTaskId;
    render();
    try {
        const data = await fetchImageTaskQuery(providerIdForSmartTask(node, task), recoverTaskId);
        if(data.status === 'succeeded'){
            task.failed = false;
            task.querying = false;
            finalizeSmartPendingTask(node, task.taskId, resultMediaUrls(data.image_items?.length ? data.image_items : (data.images?.length ? data.images : data)), task.kind || 'image');
            node.recoveredGenerationTasks = (node.recoveredGenerationTasks || []).filter(item => item.taskId !== task.taskId);
            if(!node.recoveredGenerationTasks.length) delete node.recoveredGenerationTasks;
            render();
            scheduleSave();
            return;
        }
        if(data.status === 'failed'){
            task.error = data.error || tr('smart.errRunFailed');
            toast(task.error.slice(0, 160));
        } else {
            task.error = data.message || tr('smart.taskStillGenerating');
            toast(task.error);
        }
    } catch(e){
        task.error = e.message || tr('smart.queryFailed');
        toast(task.error.slice(0, 160));
    } finally {
        const latest = smartPendingTasks(node).find(item => item.taskId === localTaskId);
        if(latest) latest.querying = false;
        render();
        scheduleStateSave();
    }
}

function startJimengPoll(node){
    if(!node || !node.jimengPending || !node.jimengPending.submitId) return;
    const submitId = node.jimengPending.submitId;
    if(activeJimengPolls.has(submitId)) return;
    activeJimengPolls.add(submitId);
    const nodeId = node.id;
    (async () => {
        try {
            for(let i = 0; i < JIMENG_POLL_MAX; i++){
                await new Promise(resolve => setTimeout(resolve, JIMENG_POLL_INTERVAL));
                const cur = canvasState.nodes.find(n => n.id === nodeId);
                if(!cur || !cur.jimengPending || cur.jimengPending.submitId !== submitId) return;
                if(cur.jimengPending.querying) continue;
                let data;
                try {
                    data = await fetchJimengQuery(submitId, cur.jimengPending.kind || 'image');
                } catch(err){ continue; }
                const done = applyJimengQueryResult(cur, data);
                if(done) return;
                const after = canvasState.nodes.find(n => n.id === nodeId);
                if(!after || !after.jimengPending || after.jimengPending.submitId !== submitId) return;
            }
        } finally {
            activeJimengPolls.delete(submitId);
        }
    })();
}

function resumeJimengPendingNodes(){
    canvasState.nodes.filter(n => n && n.jimengPending && n.jimengPending.submitId).forEach(n => {
        n.jimengPending.querying = false;
        startJimengPoll(n);
    });
}

async function pollSmartCanvasTask(taskId){
    if(!taskId) throw new Error(tr('smart.errRunFailed'));
    if(activeSmartTaskPolls.has(taskId)) return activeSmartTaskPolls.get(taskId);
    const promise = (async () => {
        for(let i = 0; i < 900; i++){
            await new Promise(resolve => setTimeout(resolve, 2000));
            const task = await fetch(projectImageTaskUrl(taskId)).then(async r => {
                if(!r.ok) throw new Error(await r.text());
                return r.json();
            });
            applySmartTaskProgress(taskId, task.progress);
            if(task.status === 'succeeded'){
                const result = task.result || {};
                absorbProjectGenerationResult(result, taskId);
                return result;
            }
            if(task.status === 'jimeng_pending') throw new JimengPendingSignal({submitId:task.submit_id, kind:task.kind, queueInfo:task.queue_info, message:task.message});
            if(task.status === 'cancelled'){
                const cancelledResult = absorbProjectGenerationResult(task.result || {}, taskId);
                const error = new Error(task.error || tr('smart.taskCancelled'));
                error.generationCancelled = true;
                // 服务端终态：前端必须按服务商失败处理，不能当网络错误重试
                error.providerFailure = true;
                if(cancelledResult?.history_recorded) error.smartGenerationLogged = true;
                if(cancelledResult?.project_node_applied) error.projectNodeApplied = true;
                throw error;
            }
            if(task.status === 'failed'){
                const failureResult = absorbProjectGenerationResult(task.result || {}, taskId);
                if(!failureResult?.history_recorded) failSmartPendingHistoryLog(taskId, task.error || tr('smart.errRunFailed'));
                const recoverTaskId = task.upstream_task_id || extractUpstreamTaskId(task.error || '');
                const error = recoverTaskId
                    ? new ImageTaskRecoverSignal({taskId, recoverTaskId, providerId:task.provider_id, kind:task.kind || 'image', message:task.error || tr('smart.errRunFailed')})
                    : new Error(task.error || tr('smart.errRunFailed'));
                // 服务端终态失败：前端必须直接进入 failedGenerationTasks，
                // 不能按关键词误判成网络错误做 3 轮无效重试（用户反馈的
                // “失败取回机制不生效”根因之一）。
                error.providerFailure = true;
                if(failureResult?.history_recorded) error.smartGenerationLogged = true;
                if(failureResult?.project_node_applied) error.projectNodeApplied = true;
                throw error;
            }
            if(task.status === 'timeout'){
                // #41：任务级硬超时是终态——按失败处理，立即清理 pending/计时器，
                // 不再进入 30 分钟轮询超时路径。
                const timeoutResult = absorbProjectGenerationResult(task.result || {}, taskId);
                if(!timeoutResult?.history_recorded) failSmartPendingHistoryLog(taskId, task.error || tr('smart.errRunTimeout'));
                const error = new Error(task.error || tr('smart.errRunTimeout'));
                error.generationTimedOut = true;
                error.providerFailure = true;
                if(timeoutResult?.history_recorded) error.smartGenerationLogged = true;
                if(timeoutResult?.project_node_applied) error.projectNodeApplied = true;
                throw error;
            }
        }
        throw new Error(tr('smart.errRunTimeout'));
    })();
    activeSmartTaskPolls.set(taskId, promise);
    try {
        return await promise;
    } finally {
        activeSmartTaskPolls.delete(taskId);
    }
}

function absorbProjectGenerationResult(result, taskId=''){
    if(!result?.history_recorded || !canvas) return result;
    const revision = Number(result.project_revision || 0);
    const registeredRevision = Number(result.registered_revision || 0);
    if(result.history_record?.id){
        canvas.history = Array.isArray(canvas.history) ? canvas.history : [];
        let pendingProgress = null;
        if(taskId){
            const pendingId = smartPendingHistoryId(taskId);
            const taskLog = canvas.history.find(log => String(log?.id || '') === pendingId || smartHistoryTaskId(log) === String(taskId));
            if(taskLog?.progress) pendingProgress = {...taskLog.progress};
            canvas.history = canvas.history.filter(log => String(log?.id || '') !== pendingId && !(log?.status === 'pending' && smartPendingHistoryTaskId(log) === String(taskId)));
        }
        const hydratedRecord = hydrateProjectMediaReferences(result.history_record);
        if(['failed', 'cancelled'].includes(result.history_record.status) && pendingProgress) hydratedRecord.progress = {...pendingProgress, failed:result.history_record.status === 'failed'};
        const index = canvas.history.findIndex(item => item?.id === result.history_record.id);
        if(index >= 0) canvas.history[index] = hydratedRecord;
        else canvas.history.unshift(hydratedRecord);
        canvas.history = mergeSmartHistoryRecordLists(canvas.history, []);
        if(smartHistoryModal?.classList.contains('open')) scheduleSmartHistoryWindowRender(true);
    }
    const resultNode = canvasState.nodes.find(item => item.id === result.result_node_id);
    const resultNodeId = String(resultNode?.id || '');
    const appliedNodeRevision = resultNodeId ? Number(smartGenerationNodeRevisions.get(resultNodeId) || 0) : 0;
    const nodeSnapshotCurrent = revision <= 0 || revision >= appliedNodeRevision;
    if(resultNode && result.final_node && nodeSnapshotCurrent){
        const finalNode = hydrateProjectMediaReferences(result.final_node);
        for(const key of ['images', 'pendingTasks', 'pending', 'running', 'queued', 'title', 'outputKind', '_generationBatchApplied']){
            if(Object.prototype.hasOwnProperty.call(finalNode, key)) resultNode[key] = finalNode[key];
            else delete resultNode[key];
        }
        if(!smartPendingTasks(resultNode).length){
            delete resultNode._pendingHistoryId;
            resultNode.runFinishedAt = nowMs();
            if(!resultNode.runStartedAt) resultNode.runStartedAt = resultNode.runFinishedAt;
            resultNode.runElapsedMs = Math.max(0, resultNode.runFinishedAt - Number(resultNode.runStartedAt || resultNode.runFinishedAt));
            resultNode.runTimerHidden = false;
        }
        if(revision > 0 && resultNodeId) smartGenerationNodeRevisions.set(resultNodeId, revision);
        result.project_node_applied = true;
        if(Array.isArray(resultNode.images) && resultNode.images.length && result.history_record?.status === 'succeeded'){
            requestSmartEdgeReveal(resultNode);
        }
    } else if(resultNode && !nodeSnapshotCurrent && taskId){
        // #41：服务端快照落后于本地编辑（revision < appliedNodeRevision）时不能覆盖
        // 节点内容，但任务已是终态——必须终结该任务的 pending 状态，否则节点永远
        // “生成中”、计时器无限增长。
        const pendingTask = smartPendingTasks(resultNode).find(item => item.taskId === taskId);
        finalizeSmartPendingTask(resultNode, taskId, [], 'image', Number(pendingTask?.count || 1));
        result.project_node_applied = true;
    }
    if(
        revision > 0
        && registeredRevision > 0
        && revision === registeredRevision + 1
        && Number(canvas.revision || 0) === registeredRevision
        && nodeSnapshotCurrent
    ){
        canvas.revision = revision;
        if(result.final_node?.id && persistedCoreSnapshot?.nodes){
            persistedCoreSnapshot.nodes[String(result.final_node.id)] = JSON.stringify(result.final_node);
        }
    }
    if(resultNode && result.history_record?.status === 'succeeded') resultNode._serverGenerationHistoryRecorded = true;
    return result;
}

function finalizeSmartPendingTask(node, taskId, images, kind='image', completedCount=1){
    if(!node || !taskId) return;
    node.pendingTasks = smartPendingTasks(node).filter(task => task.taskId !== taskId);
    node.pending = Math.max(0, Number(node.pending || 0) - Math.max(1, Number(completedCount) || 1));
    const ext = kind === 'video' ? 'mp4' : kind === 'audio' ? 'mp3' : kind === 'text' ? 'txt' : 'png';
    const mediaItems = resultMediaUrls(images);
    const existing = cleanHistoryImages(node.images || []);
    const seen = new Set(existing.map(img => `${img.kind || ''}|${img.url || ''}`));
    const additions = cleanHistoryImages((mediaItems || []).map((item, i) => {
        const url = typeof item === 'string' ? item : item?.url || '';
        const itemKind = (typeof item === 'object' && item.kind) || kind;
        return stripImageGenerationMeta(copyMediaSizeFields(item, {url, name:(typeof item === 'object' && item.name) || `output-${i + 1}.${ext}`, kind:itemKind, generatedResult:true, ...(typeof item === 'object' && item.mime_type ? {mime_type:item.mime_type} : {})}));
    }).filter(item => item.url)).filter(item => {
        const key = `${item.kind || ''}|${item.url || ''}`;
        if(seen.has(key)) return false;
        seen.add(key);
        return true;
    });
    node.images = [...existing, ...additions];
    if(additions.length){
        node.outputKind = kind;
        requestSmartEdgeReveal(node);
    }
    if(!node.pending && smartPendingTasks(node).length === 0){
        delete node.pendingTasks;
        node.runFinishedAt = nowMs();
        if(!node.runStartedAt) node.runStartedAt = node.runFinishedAt;
        node.runElapsedMs = Math.max(0, node.runFinishedAt - Number(node.runStartedAt || node.runFinishedAt));
        node.runTimerHidden = false;
        node.running = false;
        node.title = node.images.length > 1 ? (kind === 'video' ? 'Videos' : kind === 'audio' ? 'Audios' : kind === 'text' ? 'Texts' : 'Group') : (kind === 'video' ? 'Video' : kind === 'audio' ? 'Audio' : kind === 'text' ? 'Text' : 'Image');
        if(node.images.length > 1 && (!Number.isFinite(Number(node.scale)) || Number(node.scale) === MEDIA_NODE_DEFAULT_SCALE || Number(node.scale) === MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE)) node.scale = MEDIA_GROUP_DEFAULT_SCALE;
        else node.scale = mediaNodeDefaultScale(node);
        // 保留固化后的尺寸（占位图、写回、完成三个阶段统一），不再删除
        // delete node.w;
        // delete node.h;
        // 批量生图完成时，如果节点在组框内，重新整理成员并计算组框尺寸
        //（与 finalizePendingNode 保持一致，避免占位图→真实图切换时组框尺寸不匹配）
        const group = smartGroupContainingNode(node.id);
        if(group){
            arrangeSmartGroupMembers(group, {skipUndo:true});
            scheduleSave();
        }
    }
}

async function resumeSmartPendingNode(node, logContext={}){
    const scopedTaskIds = new Set(Array.isArray(logContext.taskIds) ? logContext.taskIds.filter(Boolean) : []);
    const tasks = smartPendingTasks(node).filter(task => !scopedTaskIds.size || scopedTaskIds.has(task.taskId));
    if(!node || !tasks.length) return;
    const logTaskFailure = (message, task) => {
        if(!logContext?.run || !message) return;
        const runMs = Math.max(0, nowMs() - Number(logContext.runLogStart || nowMs()));
        addSmartGenerationHistory({
            run:logContext.run,
            outputs:[],
            runMs,
            error:message
        });
    };
    node.pending = Math.max(tasks.length, Number(node.pending || 0) || tasks.length);
    node.running = false;
    render();
    const failures = [];
    await Promise.all(tasks.map(async task => {
        if(task.failed && task.recoverTaskId) return;
        try {
            const result = await pollSmartCanvasTask(task.taskId);
            const resultItems = task.kind === 'video'
                ? (result?.video_items?.length ? result.video_items : (result?.videos?.length ? result.videos : result))
                : (result?.image_items?.length ? result.image_items : (result?.images?.length ? result.images : result));
            const resultImages = result?.project_node_applied
                ? []
                : resultMediaUrls(resultItems);
            if(!result?.project_node_applied){
                finalizeSmartPendingTask(node, task.taskId, resultImages, task.kind || 'image', task.count || 1);
            }
            render();
            scheduleSave();
        } catch(e) {
            if(e && e.jimengPending && e.submitId){
                node.pendingTasks = smartPendingTasks(node).filter(item => item.taskId !== task.taskId);
                setNodeJimengPending(node, e);
                render();
                scheduleStateSave();
                return;
            }
            if(e && e.imageTaskRecover && e.recoverTaskId){
                // #41：任务在服务端已丢失（终态），不能继续留在 pendingTasks 里让节点
                // 永远“生成中”。把可手动查询的恢复信息移入 recoveredGenerationTasks，
                // 再按终态清理 pending，停止计时器/轮询/loading。
                task.failed = true;
                task.querying = false;
                task.recoverTaskId = e.recoverTaskId;
                task.providerId = e.providerId || task.providerId || providerIdForSmartTask(node, task);
                task.error = e.message || tr('smart.errRunFailed');
                if(!e?.smartGenerationLogged) logTaskFailure(task.error, task);
                node.recoveredGenerationTasks = Array.isArray(node.recoveredGenerationTasks) ? node.recoveredGenerationTasks : [];
                if(!node.recoveredGenerationTasks.some(item => item.taskId === task.taskId && item.recoverTaskId === task.recoverTaskId)){
                    node.recoveredGenerationTasks.push({taskId:task.taskId, recoverTaskId:task.recoverTaskId, providerId:task.providerId, kind:task.kind || 'image', error:task.error, failed:true, at:nowMs()});
                }
                finalizeSmartPendingTask(node, task.taskId, [], task.kind || 'image', task.count || 1);
                node.running = false;
                toast(tr('smart.taskNotLostLater'));
                render();
                scheduleSave();
                return;
            }
            // 区分服务商失败和网络问题：服务端终态失败带 providerFailure 标记；
            // 其余情况再按错误文本关键词兜底判断。
            const isProviderFailure = e && (e.providerFailure || (e.message && (e.message.includes('failed') || e.message.includes('cancelled') || e.message.includes('timeout') || e.message.includes('sensitive') || e.message.includes('FAILURE'))));
            
            if(isProviderFailure){
                // 服务商失败（终态）：无论节点状态是否已被服务端同步，都必须登记
                // failedGenerationTasks，让节点显示"重试/取回"UI。
                // （后端 fail_image_generation 已把任务从 pendingTasks 移除并同步节点，
                //  若像旧逻辑那样用 projectNodeApplied 作守卫，失败任务会被静默吞掉，
                //  用户永远看不到重试入口——即"失败取回机制不生效"的根因。）
                node.failedGenerationTasks = Array.isArray(node.failedGenerationTasks) ? node.failedGenerationTasks : [];
                if(!node.failedGenerationTasks.some(item => item.taskId === task.taskId)){
                    node.failedGenerationTasks.push({taskId:task.taskId, error:String(e?.message || tr('smart.errRunFailed')).slice(0, 240), at:nowMs()});
                }
                if(!e?.projectNodeApplied){
                    // 服务端未同步节点状态时，前端负责清理该任务的 pending
                    const remainingTasks = smartPendingTasks(node).filter(item => item.taskId !== task.taskId);
                    node.pendingTasks = remainingTasks;
                    node.pending = remainingTasks.length
                        ? Math.max(remainingTasks.length, remainingTasks.reduce((total, item) => total + Math.max(1, Number(item.count || 1)), 0))
                        : 0;
                }
                if(!node.pending && smartPendingTasks(node).length === 0){
                    delete node.pendingTasks;
                    node.running = false;
                    if(!(node.images || []).length){
                        delete node.w;
                        delete node.h;
                    }
                }
                if(!e?.smartGenerationLogged){
                    logTaskFailure(e.message || tr('smart.errRunFailed'), task);
                    if(e && typeof e === 'object') e.smartGenerationLogged = true;
                }
                toast((e.message || tr('smart.errRunFailed')).slice(0, 160));
                render();
                scheduleSave();
                return;
            }
            
            // 网络问题，自动重试
            task._retryCount = (task._retryCount || 0) + 1;
            if(task._retryCount <= 3){
                const delay = task._retryCount * 5000; // 5s, 10s, 15s
                toast(trf('smart.writeFailedRetry', {n: task._retryCount, s: delay/1000}));
                await sleep(delay);
                // 图片回载后取消重试
                if(activeSmartTaskPolls.has(task.taskId)){
                    return; // 图片已回载，取消重试
                }
                return resumeSmartPendingNode(node, logContext);
            }
            
            // 重试耗尽
            if(!e?.projectNodeApplied){
                const remainingTasks = smartPendingTasks(node).filter(item => item.taskId !== task.taskId);
                node.pendingTasks = remainingTasks;
                node.pending = remainingTasks.length
                    ? Math.max(remainingTasks.length, remainingTasks.reduce((total, item) => total + Math.max(1, Number(item.count || 1)), 0))
                    : 0;
                node.failedGenerationTasks = Array.isArray(node.failedGenerationTasks) ? node.failedGenerationTasks : [];
                if(!node.failedGenerationTasks.some(item => item.taskId === task.taskId)){
                    node.failedGenerationTasks.push({taskId:task.taskId, error:trf('smart.networkRetryExhausted', {n: task._retryCount}), at:nowMs(), retryable:true});
                }
            }
            if(!node.pending && smartPendingTasks(node).length === 0){
                delete node.pendingTasks;
                node.running = false;
                if(!(node.images || []).length){
                    delete node.w;
                    delete node.h;
                }
            }
            failures.push(e);
            if(!e?.smartGenerationLogged){
                logTaskFailure(e.message || tr('smart.errRunFailed'), task);
                if(e && typeof e === 'object') e.smartGenerationLogged = true;
            }
            toast(tr('smart.writeFailedMaxRetries'));
            render();
            scheduleSave();
        }
    }));
    if(failures.length){
        throw failures[0];
    }
}

async function retryFailedGeneration(nodeId){
    const node = canvasState.nodes.find(item => String(item.id || '') === String(nodeId));
    if(!node) return;
    // 防连点：重试在途时不重复提交（会生成新的幂等键并重复计费）
    if(node._retryInFlight) return;
    node._retryInFlight = true;
    try{
        await retryFailedGenerationInner(node);
    } finally {
        node._retryInFlight = false;
    }
}

async function retryFailedGenerationInner(node){
    const failedTasks = Array.isArray(node.failedGenerationTasks) ? node.failedGenerationTasks.slice() : [];
    if(failedTasks.length){
        // 1) 先尝试取回失败任务里已归档成功的图（服务端兑底 outputs）
        const projectId = canvas.project_id || canvas.project || canvasId;
        for(const failedTask of failedTasks){
            try{
                const response = await fetch(projectImageTaskUrl(failedTask.taskId));
                if(!response.ok) continue;
                const task = await response.json();
                const outputs = Array.isArray(task?.result?.history_record?.outputs) ? task.result.history_record.outputs : [];
                if(task.status === 'failed' && outputs.length){
                    const base = `/api/project-library/${encodeURIComponent(projectId)}/media/`;
                    const images = outputs.map(item => ({url: base + encodeURIComponent(item.asset_id || ''), kind: item.kind || 'image'}));
                    node.failedGenerationTasks = [];
                    finalizeSmartPendingTask(node, failedTask.taskId, images, 'image', images.length);
                    render();
                    scheduleSave();
                    toast(trf('smart.recoveredResults', {n: images.length}));
                    return;
                }
            }catch(_){
                // 取回失败则继续尝试重新提交
            }
        }
    }
    // 2) 无已归档结果 → 用原请求重新生成。
    // 显式重试是用户主动的新尝试：换新的幂等键，避免服务端按幂等重放
    // 同一个已失败的任务（那样重试永远返回旧失败，取回机制无法生效）。
    node.failedGenerationTasks = [];
    const submission = smartGenerationSubmissions(node).find(item => item && item.requestPayload);
    if(submission){
        submission.idempotencyKey = `regen-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
        await resumeSmartGenerationSubmission(node, submission);
        render();
        return;
    }
    render();
    toast(tr('smart.noRetryableRequest'));
}

async function resumeSmartGenerationSubmission(node, submission){
    const nodeId = String(node?.id || '');
    const idempotencyKey = String(submission?.idempotencyKey || '');
    const recoveryKey = `${nodeId}:${idempotencyKey}`;
    if(!nodeId || !idempotencyKey || activeSmartSubmissionRecoveries.has(recoveryKey)) return;
    activeSmartSubmissionRecoveries.add(recoveryKey);
    try {
        const projectId = canvas.project_id || canvas.project || canvasId;
        const payload = {...(submission.requestPayload || {}), idempotency_key:idempotencyKey, result_node_id:nodeId, base_revision:Number(canvas.revision || 0)};
        const task = await postProjectImageTaskWithReplay(projectId, payload);
        const pendingHistoryId = String(submission.pendingHistoryId || smartPendingHistoryId(task.task_id));
        const taskResult = {
            taskIds:[task.task_id].filter(Boolean),
            count:Number(submission.count || payload.n || 1),
            providerId:task.route?.provider_id || '',
            model:task.route?.model || '',
            route:task.route,
            projectRevision:task.project_revision,
            registeredNode:task.registered_node,
            pendingHistoryId,
            idempotencyKey:task.idempotency_key || idempotencyKey
        };
        await acceptProjectGenerationRegistration(taskResult);
        const currentNode = canvasState.nodes.find(item => String(item.id || '') === nodeId) || node;
        clearSmartGenerationSubmission(currentNode, idempotencyKey);
        const taskIds = taskResult.taskIds;
        const newTasks = taskIds.map(taskId => ({
            taskId,
            kind:'image',
            count:taskResult.count,
            providerId:taskResult.providerId,
            model:taskResult.model,
            prompt:submission.prompt || payload.prompt || '',
            createdAt:Number(submission.createdAt || nowMs()),
            pendingHistoryId,
            idempotencyKey
        }));
        currentNode.pendingTasks = [...smartPendingTasks(currentNode).filter(item => !taskIds.includes(item.taskId)), ...newTasks];
        // 恢复提交同样固化比例（size 取原始请求快照，服务端 task_record 也带 size）。
        const resumeSize = String((submission?.requestPayload?.size || payload?.size || submission?.size || ''));
        currentNode._generationAspect = parseSizeAspect(resumeSize);
        ensureSmartPendingHistoryLogs(currentNode, newTasks, {prompt:submission.prompt || payload.prompt || '', kind:'image'});
        currentNode.pending = Math.max(currentNode.pendingTasks.length, currentNode.pendingTasks.reduce((total, item) => total + Math.max(1, Number(item.count || 1)), 0));
        // 恢复提交：保持占位格总数固化（已有则保留，避免覆盖）
        if(!Number(currentNode._batchTotal)) currentNode._batchTotal = currentNode.pending;
        currentNode.running = false;
        render();
        scheduleSave();
        await resumeSmartPendingNode(currentNode, {taskIds});
    } catch(error) {
        if(!error?.smartSubmissionUnknown){
            const currentNode = canvasState.nodes.find(item => String(item.id || '') === nodeId) || node;
            clearSmartGenerationSubmission(currentNode, idempotencyKey);
            scheduleSave();
        }
        toast((error?.message || tr('smart.recoverSubmitFailed')).slice(0, 160));
    } finally {
        activeSmartSubmissionRecoveries.delete(recoveryKey);
    }
}

function resumeSmartPendingTasks(){
    canvasState.nodes.filter(node => smartGenerationSubmissions(node).length).forEach(node => {
        smartGenerationSubmissions(node).forEach(submission => {
            void resumeSmartGenerationSubmission(node, submission);
        });
    });
    canvasState.nodes.filter(node => smartPendingTasks(node).length).forEach(node => {
        void resumeSmartPendingNode(node).catch(() => {});
    });
}
