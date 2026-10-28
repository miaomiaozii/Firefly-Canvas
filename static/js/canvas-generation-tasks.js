// 画布生成任务：提交、即梦轮询、待恢复任务续跑与失败重试（从 smart-canvas.js 抽离，#154）。
// invariant: classic script，按 HTML 顺序先于 smart-canvas.js 加载；本文件顶层只有声明，
// 初始化器只取空容器或字面量，不读其他模块的状态。函数声明整体提升，实际调用都发生在
// 主脚本初始化之后。跨模块可变状态一律走 canvasState（window.canvasState），不要引用裸 nodes/selectedId。

let transientSmartCloudLinks = [];

const activeSmartTaskPolls = new Map();

const smartGenerationNodeRevisions = new Map();

const activeSmartSubmissionRecoveries = new Set();
// #189: idempotency keys of generation requests whose pre-generation flush failed.
// They stay pending until the canvas save settles, when markAutosaveSaved() replays
// them (the server dedupes by key). In-memory only: after a page reload the persisted
// marker is recovered by the existing load-path resumeSmartPendingTasks() instead.
const smartPendingPersistenceKeys = new Set();
// #189 follow-up: in-memory pending action resumes for the four remaining save gates
// (video / local-edit / photoshop / conversation). Each entry carries exactly one replay
// closure and is removed from the table BEFORE its fetch fires, so one user action => at
// most one send attempt. Server-side dedup only exists for the conversation gate: the
// endpoint replays by request_id (app/routers/api_project_library.py:3277-3282 returns the
// existing snapshot instead of re-running). video / local-edit / photoshop have NO server
// dedup (idempotency_key handling in that router is image-only), which is why the
// single-shot dispatcher is required for them. Node deletion drops the entry
// automatically (no timeout/cancel by design). Unlike the image entry these queues are
// in-memory only: a page reload before the save settles drops the queued request.
const smartPendingActionResumes = new Map();
let smartPendingActionResumeSeq = 0;
const smartPendingActionResumeLog = [];
// #198: bounded retry budget for replayed gate requests. clear-on-send removes the
// persisted mark from disk BEFORE the replay fetch fires (double-billing prevention —
// video / local-edit / photoshop have no server-side dedup), so a FAILED replay must
// write the mark back or the user's action is lost forever. The count persists inside
// the same mark (node.pendingActionResumes[i].replayAttempts) so a reload cannot reset
// it into an unbounded retry loop. The budget is deliberately small: every attempt of a
// non-deduplicated gate can be a paid request even when its response is lost.
const SMART_PENDING_ACTION_MAX_REPLAY_ATTEMPTS = 3; // total attempts (1 initial + 2 retries)
const SMART_PENDING_ACTION_RETRY_BACKOFF_MS = [60000, 180000]; // retry 1 after 1 min, retry 2 after 3 min

const smartGenerationRegistrationQueues = new Map();

async function runPromptLLMNode(nodeId){
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if(!node || node.type !== NODE_TYPE_PROMPT) return;
    const projectId = canvas?.project_id || canvas?.project || canvasCore.canvasId || '';
    const message = promptNodeLLMInputText(node).trim();
    if(!message){ toast(tr('smart.promptLlmNeedText')); return; }
    const systemPrompt = (node.llmSystemPrompt || '').trim();
    node.llmEnabled = true;
    node.running = true;
    canvasCore.render();
    try {
        const provider = canvasCore.resolveChatProviderId(node.llmProvider || '');
        const model = canvasCore.resolveChatModel(node.llmModel || '', provider);
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
        canvasCore.scheduleSave();
    } catch(e) {
        toast((e.message || tr('smart.promptLlmFailed')).slice(0, 160));
    } finally {
        node.running = false;
        canvasCore.render();
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
    const unknown = new Error(canvasCore.trf('smart.submitUnknownRecoverDetail', {m: lastError?.message || tr('smart.networkInterrupted')}));
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
        // #187: resolution 不得被 !outpaintSize 门掉——扩图参数面板保留用户选择的
        // 分辨率预设（4K/2K/1K），请求必须照发同一值，否则服务商按默认档位出图（1K），
        // 界面承诺与请求实态不一致（ADR 0006：用户输入不得静默降级）。
        // 节点可能残留上一个模型支持、当前模型不支持的比例（banana 的 1:4 切到
        // gpt-image 家族）：发送前先收敛到该模型支持的最接近比例，否则 aspect_ratio 与
        // 由它推导的 size 双双超能力，请求在路由预检被拒（不兼容参数：aspect_ratio）。
        canvasCore.alignRatioToModel(runSettings);
        const usesExplicitResolution = apiImageUsesExplicitResolution(runSettings);
        // 扩图时不发送 size/width/height，只发送 aspect_ratio 和 resolution：
        // 扩图几何由 outpaintSize 的 aspect_ratio 表达，服务商契约按「比例 + 分辨率档位」
        // 推导像素尺寸（见 app/services/generation.py：aspect_ratio 存在时 body 不写 size）。
        const sendsExplicitSize = usesExplicitResolution && !outpaintSize;
        const size = sendsExplicitSize ? sizeForRun(runSettings) : '';
        const parsed = sendsExplicitSize ? canvasCore.parseSizeValue(size) : null;
        if(sendsExplicitSize && !parsed) throw new Error(tr('smart.imageSizeInvalid'));
        const ownedRefs = await Promise.all(imageRefsOnly(refs).slice(0, SMART_REFERENCE_IMAGE_MAX).map(canvasCore.importProjectMediaReference));
        const reference_asset_ids = ownedRefs.filter(Boolean).map(item => item.asset_id).filter(Boolean);
        if(reference_asset_ids.length !== imageRefsOnly(refs).slice(0, SMART_REFERENCE_IMAGE_MAX).length) throw new Error(tr('smart.refsNotInProject'));
        const projectId = canvas.project_id || canvas.project || canvasCore.canvasId;
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
        canvasCore.scheduleSave();
        if(!await flushProjectCanvasBeforeGeneration()){
            // #189: a failed pre-generation flush no longer cancels the request. Keep the durable
            // submission marker and let the debounced autosave carry it to disk; once the canvas
            // save settles, markAutosaveSaved() replays the same idempotency key, so the task
            // registers exactly once (the server dedupes by key) and the node moves straight
            // into the generating state instead of failing.
            // The pending state is tracked in the module-level key set (not a marker field)
            // because a 409 rebase (applyMergedServerCanvas) can swap the canvas nodes for
            // server copies and wipe in-memory marker annotations before they are persisted.
            smartPendingPersistenceKeys.add(payload.idempotency_key);
            canvasCore.scheduleSave();
            const pendingError = new Error(tr('smart.canvasSavePendingResume'));
            pendingError.smartSavePendingResume = true;
            throw pendingError;
        }
        // The durable submission marker was saved at the previous revision.
        // A first-time replay uses the latest revision; duplicate resolution
        // ignores this mutable field and still returns the original task.
        payload.base_revision = Number(canvas.revision || payload.base_revision || 0);
        if(submission) submission.requestPayload = {...payload};
        const task = await postProjectImageTaskWithReplay(projectId, payload);
        const taskResult = {taskIds:[task.task_id].filter(Boolean), count, providerId:task.route?.provider_id || '', model:task.route?.model || '', route:task.route, projectRevision:task.project_revision, registeredNode:task.registered_node, pendingHistoryId, idempotencyKey:task.idempotency_key || payload.idempotency_key};
        await canvasCore.acceptProjectGenerationRegistration(taskResult);
        // 新运行重置计时器：项目 API 提交路径不经过 runCascadeStepIntoNode，
        // 必须显式重置，否则持久化节点会带着上次生成的旧 runStartedAt 无限涨。
        const refreshedNode = canvasState.nodes.find(item => item.id === resultNode.id) || resultNode;
        refreshedNode.runStartedAt = nowMs();
        delete refreshedNode.runFinishedAt;
        delete refreshedNode.runElapsedMs;
        refreshedNode.runTimerHidden = false;
        clearSmartGenerationSubmission(canvasState.nodes.find(item => item.id === resultNode.id) || resultNode, payload.idempotency_key);
        canvasCore.scheduleSave();
        return taskResult;
    }
    canvasCore.alignRatioToModel(runSettings);
    if(!runSettings.provider_id || !runSettings.model) throw new Error(tr('smart.errNoApiModel'));
    const payload = {prompt, provider_id:runSettings.provider_id, model:runSettings.model, size:sizeForRun(runSettings), quality:runSettings.quality || 'auto', n:1, reference_images:imageRefsOnly(refs).slice(0, SMART_REFERENCE_IMAGE_MAX)};
    const tasks = await Promise.all(Array.from({length:count}, () => fetch('/api/canvas-image-tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}).then(async r => {
        if(!r.ok) throw new Error(await r.text());
        return r.json();
    })));
    return {taskIds:tasks.map(task => task.task_id).filter(Boolean), count, providerId:payload.provider_id, model:payload.model};
}

// #254 逐图生成（数据层，切片 1）：一句 prompt × N 参考图 → N 个标准图片子任务。
// expandEachRuns 是纯展开器：不碰 DOM、不读全局画布状态（rect 由 ctx 注入），
// 每个子任务共享 prompt/runSettings，参考图各归各，落点锚定参考节点右侧
// （沿用 createLocalEditNodeFromImage 的 +140px 惯例，垂直居中）。
const EACH_RUN_RESULT_GAP = 140;
function expandEachRuns({refNodes, prompt, ctx = {}} = {}){
    const nodeRect = ctx.nodeRect || (node => ({x:Number(node.x) || 0, y:Number(node.y) || 0, width:Number(node.w) || 0, height:Number(node.h) || 0}));
    const items = [];
    for(const refNode of Array.isArray(refNodes) ? refNodes : []){
        if(!refNode || !refNode.id) continue;
        const rect = nodeRect(refNode) || {};
        items.push({
            refNodeId:refNode.id,
            prompt:String(prompt || ''),
            resultAnchor:{
                x:(Number(rect.x) || 0) + (Number(rect.width) || 0) + EACH_RUN_RESULT_GAP,
                y:Math.max(40, (Number(rect.y) || 0) + Math.max(0, ((Number(rect.height) || 0) - 300) / 2)),
            },
        });
    }
    return items;
}
// 编排器（全局脚本作用域：canvas-undo.js/canvas-node-ops.js 先于本文件加载）：
// 整批一次 pushUndo（撤销粒度=整批，与批量生成惯例一致），
// 每个参考图 → 右侧新建结果节点 + runApiGeneration 单次提交（refs 只含该参考图）。
async function runEachImageGeneration(refNodes, prompt, runSettings = {}){
    const items = expandEachRuns({refNodes, prompt, ctx:{nodeRect:node => canvasCore.nodeRect(node)}});
    if(!items.length) return {runs:[]};
    pushUndo();
    const runs = [];
    for(const item of items){
        const refNode = (canvasState.nodes || []).find(node => node.id === item.refNodeId);
        if(!refNode) continue;
        const imageRefs = imageRefsOnly((refNode.images || []).map(image => ({...image, node:refNode})));
        if(!imageRefs.length) continue;
        const resultNode = createImageNodeAt(item.resultAnchor, [], {skipUndo:true});
        const taskResult = await runApiGeneration(item.prompt, imageRefs.slice(0, 1), runSettings, resultNode);
        runs.push({refNodeId:item.refNodeId, resultNodeId:resultNode.id, taskIds:taskResult.taskIds || []});
    }
    return {runs};
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
        if(!await flushProjectCanvasBeforeGeneration()){
            // #189 gate (m03364/m03420): the video request was never sent. Do not drop it:
            // queue a single-shot replay that re-runs this function once the canvas save
            // settles (markAutosaveSaved → resumeSmartPendingActionResumes); on that pass the
            // flush succeeds. The video endpoint has no server-side dedup and the dispatcher
            // removes the entry before running it, so one click sends at most one request.
            queueSmartPendingActionResume('video', resultNode.id, live => (async () => {
                const freshNode = canvasState.nodes.find(n => n.id === live.id) || live;
                const videoResult = await runApiVideoGeneration(prompt, refs, runSettings, freshNode);
                // Mirror the main caller's success path (shared helper so the reload
                // rebuilder applies the exact same result handling). Fire-and-forget
                // completion tracking: the dispatcher's 'replayed' event must be logged
                // as soon as the request has been re-sent and registered; polling the
                // video task to completion must not gate the resume log.
                const taskIds = applyApiVideoGenerationResult(freshNode, videoResult, prompt);
                if(!await canvasCore.acceptProjectGenerationRegistration(videoResult)){
                    canvasCore.scheduleSave();
                    await canvasCore.saveCanvas();
                }
                if(taskIds.length) void resumeSmartPendingNode(freshNode, {taskIds}).catch(() => {});
            })(), {prompt, refs, runSettings});
            const pendingError = new Error(tr('smart.canvasVideoPendingAfterSave'));
            pendingError.smartSavePendingResume = true;
            toast(pendingError.message);
            throw pendingError;
        }
        const projectRefs = (refs || []).filter(ref => ['image','video','audio'].includes(canvasCore.mediaKindForItem(ref)));
        const ownedRefs = await Promise.all(projectRefs.map(ref => canvasCore.importProjectMediaReference(ref)));
        if(ownedRefs.some(item => !item?.asset_id) || ownedRefs.length !== projectRefs.length) throw new Error(tr('smart.videoRefsNotInProject'));
        const projectId = canvas.project_id || canvas.project || canvasCore.canvasId;
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
            image_reference_asset_ids:ownedRefs.filter(item => canvasCore.mediaKindForItem(item) === 'image').map(item => item.asset_id),
            video_reference_asset_ids:ownedRefs.filter(item => canvasCore.mediaKindForItem(item) === 'video').map(item => item.asset_id),
            audio_reference_asset_ids:ownedRefs.filter(item => canvasCore.mediaKindForItem(item) === 'audio').map(item => item.asset_id),
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
    const projectId = canvas?.project_id || canvas?.project || canvasCore.canvasId;
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

// #249 审计：两者均为自设上限，非即梦 SLA。60s×1440=24h 轮询总上限（工程选择：
// 付费任务不能过早判丢）；间隔 60s 为负载平滑。未知任务终态化（连续未知提前
// 落终态）属 W1 终态机（#235，本分支 main 尚未合入），待其落地后与之对齐。
const JIMENG_POLL_INTERVAL = 60000;

const JIMENG_POLL_MAX = 1440;

function jimengQueueText(queueInfo){
    const qi = queueInfo || {};
    const idx = qi.queue_idx;
    const len = qi.queue_length;
    if(idx != null && len != null) return canvasCore.trf('smart.jimengCloudQueued', {idx: idx, len: len});
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
    canvasCore.render();
    canvasCore.scheduleStateSave();
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
        return canvasCore.stripImageGenerationMeta(canvasCore.copyMediaSizeFields(item, {url, name:(typeof item === 'object' && item.name) || `output-${i + 1}.${ext}`, kind:itemKind, generatedResult:true, ...(typeof item === 'object' && item.mime_type ? {mime_type:item.mime_type} : {})}));
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
    canvasCore.render();
    canvasCore.scheduleSave();
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
        canvasCore.render();
        canvasCore.scheduleStateSave();
        return true;
    }
    if(node.jimengPending){
        node.jimengPending.queueInfo = data.queue_info || node.jimengPending.queueInfo || {};
        node.jimengPending.message = data.message || node.jimengPending.message || '';
        node.jimengPending.updatedAt = nowMs();
    }
    canvasCore.render();
    canvasCore.scheduleStateSave();
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
    canvasCore.render();
    try {
        const data = await fetchJimengQuery(submitId, kind);
        applyJimengQueryResult(node, data);
    } catch(e){
        toast((e.message || tr('smart.queryFailed')).slice(0, 160));
    } finally {
        if(node.jimengPending) node.jimengPending.querying = false;
        canvasCore.render();
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
    const task = smartPendingTasks(node).find(item => item.taskId === localTaskId) || canvasCore.smartRecoverableImageTask(node);
    if(!task || task.querying) return;
    const recoverTaskId = task.recoverTaskId || extractUpstreamTaskId(task.error || '');
    if(!recoverTaskId){
        toast(tr('smart.queryMissingTaskId'));
        return;
    }
    task.querying = true;
    task.recoverTaskId = recoverTaskId;
    canvasCore.render();
    try {
        let data;
        const taskUrl = projectImageTaskUrl(task.taskId || localTaskId);
        if(taskUrl.startsWith('/api/project-library/')){
            // #234：项目任务走项目 GET 主动取回（?recover=1），succeeded 成品直接落本项目画布/历史
            const response = await fetch(taskUrl + (taskUrl.includes('?') ? '&' : '?') + 'recover=1');
            if(!response.ok) throw new Error(await response.text());
            const body = await response.json();
            const result = body.result || {};
            data = {
                status: (body.recover && body.recover.status === 'running') ? 'running' : body.status,
                images: result.images || [],
                image_items: result.image_items || [],
                error: (body.recover && body.recover.error) || body.error || '',
                message: (body.recover && body.recover.status === 'running') ? (body.recover.error || tr('smart.taskStillGenerating')) : ''
            };
        } else {
            data = await fetchImageTaskQuery(providerIdForSmartTask(node, task), recoverTaskId);
        }
        if(data.status === 'succeeded'){
            task.failed = false;
            task.querying = false;
            finalizeSmartPendingTask(node, task.taskId, resultMediaUrls(data.image_items?.length ? data.image_items : (data.images?.length ? data.images : data)), task.kind || 'image');
            node.recoveredGenerationTasks = (node.recoveredGenerationTasks || []).filter(item => item.taskId !== task.taskId);
            if(!node.recoveredGenerationTasks.length) delete node.recoveredGenerationTasks;
            canvasCore.render();
            canvasCore.scheduleSave();
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
        canvasCore.render();
        canvasCore.scheduleStateSave();
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
                // #237：用户主动停止（stop≠error）——后端 /cancel 端点打的标记，
                // 与 service_shutdown 的服务端终止区分；前端据此走中性终态而非失败/重试 UI。
                error.cancelledByUser = task.cancelled_by_user === true;
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
        return canvasCore.stripImageGenerationMeta(canvasCore.copyMediaSizeFields(item, {url, name:(typeof item === 'object' && item.name) || `output-${i + 1}.${ext}`, kind:itemKind, generatedResult:true, ...(typeof item === 'object' && item.mime_type ? {mime_type:item.mime_type} : {})}));
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
        if(node.images.length > 1 && (!Number.isFinite(Number(node.scale)) || Number(node.scale) === canvasCore.MEDIA_NODE_DEFAULT_SCALE || Number(node.scale) === MEDIA_GROUP_PREVIOUS_DEFAULT_SCALE)) node.scale = MEDIA_GROUP_DEFAULT_SCALE;
        else node.scale = canvasCore.mediaNodeDefaultScale(node);
        // 保留固化后的尺寸（占位图、写回、完成三个阶段统一），不再删除
        // delete node.w;
        // delete node.h;
        // 批量生图完成时，如果节点在组框内，重新整理成员并计算组框尺寸
        //（与 finalizePendingNode 保持一致，避免占位图→真实图切换时组框尺寸不匹配）
        const group = canvasCore.smartGroupContainingNode(node.id);
        if(group){
            canvasCore.arrangeSmartGroupMembers(group, {skipUndo:true});
            canvasCore.scheduleSave();
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
    canvasCore.render();
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
            canvasCore.render();
            canvasCore.scheduleSave();
        } catch(e) {
            if(e && e.jimengPending && e.submitId){
                node.pendingTasks = smartPendingTasks(node).filter(item => item.taskId !== task.taskId);
                setNodeJimengPending(node, e);
                canvasCore.render();
                canvasCore.scheduleStateSave();
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
                canvasCore.render();
                canvasCore.scheduleSave();
                return;
            }
            if(e?.generationCancelled && e?.cancelledByUser){
                // #237 用户主动停止（stop≠error）：后端 /cancel 已写即时终态快照
                // （final_node 清除「生成中」占位 + 取消历史含 partial 成品 + 中性文案「任务已取消」），
                // 轮询端在抛错前已用 absorb 写回——这里只保证该任务的 pending 被移除，
                // 不走服务商失败「失败/重试」UI，不记 error 日志，不弹 error toast。
                const taskStillPending = smartPendingTasks(node).some(item => item.taskId === task.taskId);
                if(taskStillPending){
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
                // 节点仍挂着该任务（如快照因 revision 冲突未应用）时补一次中性提示；
                // 常规 stop 流程里 POST /cancel 处理已 toast，不重复。
                if(taskStillPending) toast(tr('smart.taskCancelled'));
                canvasCore.render();
                canvasCore.scheduleSave();
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
                canvasCore.render();
                canvasCore.scheduleSave();
                return;
            }
            
            // 网络问题，自动重试
            task._retryCount = (task._retryCount || 0) + 1;
            if(task._retryCount <= 3){
                const delay = task._retryCount * 5000; // 5s, 10s, 15s
                toast(canvasCore.trf('smart.writeFailedRetry', {n: task._retryCount, s: delay/1000}));
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
                    node.failedGenerationTasks.push({taskId:task.taskId, error:canvasCore.trf('smart.networkRetryExhausted', {n: task._retryCount}), at:nowMs(), retryable:true});
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
            canvasCore.render();
            canvasCore.scheduleSave();
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
        const projectId = canvas.project_id || canvas.project || canvasCore.canvasId;
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
                    canvasCore.render();
                    canvasCore.scheduleSave();
                    toast(canvasCore.trf('smart.recoveredResults', {n: images.length}));
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
        canvasCore.render();
        return;
    }
    canvasCore.render();
    toast(tr('smart.noRetryableRequest'));
}

async function resumeSmartGenerationSubmission(node, submission){
    const nodeId = String(node?.id || '');
    const idempotencyKey = String(submission?.idempotencyKey || '');
    const recoveryKey = `${nodeId}:${idempotencyKey}`;
    if(!nodeId || !idempotencyKey || activeSmartSubmissionRecoveries.has(recoveryKey)) return;
    activeSmartSubmissionRecoveries.add(recoveryKey);
    try {
        const projectId = canvas.project_id || canvas.project || canvasCore.canvasId;
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
        await canvasCore.acceptProjectGenerationRegistration(taskResult);
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
        canvasCore.render();
        canvasCore.scheduleSave();
        await resumeSmartPendingNode(currentNode, {taskIds});
    } catch(error) {
        if(!error?.smartSubmissionUnknown){
            const currentNode = canvasState.nodes.find(item => String(item.id || '') === nodeId) || node;
            clearSmartGenerationSubmission(currentNode, idempotencyKey);
            canvasCore.scheduleSave();
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
// #189: after a settled canvas save, replay only the generation requests whose
// pre-generation flush failed (keys recorded in smartPendingPersistenceKeys). Markers on
// the normal submit path are not in the set and self-register; replaying them here would
// double-POST the same idempotency key on the happy path.
function resumeSmartPersistencePendingTasks(canvasNodes){
    if(!smartPendingPersistenceKeys.size) return;
    (canvasNodes || []).forEach(node => {
        smartGenerationSubmissions(node).filter(s => smartPendingPersistenceKeys.has(s.idempotencyKey)).forEach(submission => {
            void resumeSmartGenerationSubmission(node, submission);
        });
    });
    // Drop keys whose marker is gone (replayed and cleared, or node removed).
    const liveKeys = new Set();
    (canvasNodes || []).forEach(node => smartGenerationSubmissions(node).forEach(s => liveKeys.add(s.idempotencyKey)));
    for(const key of [...smartPendingPersistenceKeys]) if(!liveKeys.has(key)) smartPendingPersistenceKeys.delete(key);
}
// #189 follow-up: queue one gate request (video/local-edit/photoshop/conversation) whose
// pre-action flush failed. The entry is anchored to nodeId (liveness check on replay) and
// scheduleSave() is called here: the failed gate flush left the canvas dirty, and the
// debounced autosave that then succeeds settles the save (markAutosaveSaved), which calls
// resumeSmartPendingActionResumes().
function queueSmartPendingActionResume(kind, nodeId, replay, payload){
    if(!nodeId || typeof replay !== 'function') return null;
    smartPendingActionResumeSeq += 1;
    const actionId = `action-${kind}-${smartPendingActionResumeSeq}`;
    const entry = {actionId, kind:String(kind || ''), nodeId:String(nodeId), replay, payload:payload === undefined ? null : payload, createdAt:Date.now(), releasing:false};
    smartPendingActionResumes.set(actionId, entry);
    mirrorPendingActionToStorage(entry);
    smartPendingActionResumeLog.push({t:Date.now(), event:'queued', kind:entry.kind, nodeId:entry.nodeId});
    if(typeof canvasCore.scheduleSave === 'function') canvasCore.scheduleSave();
    return actionId;
}
// #197: mirror the in-memory queue entry onto the anchored node so the next successful
// save persists it. The incremental save path carries whole nodes via upsert_node
// mutations, while top-level canvas fields are NOT in the mutation snapshot (meta
// whitelist is {title, icon, history} only — project-canvas-mutations.js), so the mark
// must live on the node. Best-effort: if the mirror fails, the in-memory entry stays
// authoritative and the next dispatch pass re-mirrors (the node is still dirty then).
function mirrorPendingActionToStorage(entry){
    try {
        const node = (typeof canvasState !== 'undefined' && canvasState?.nodes || []).find(n => n && n.id === entry.nodeId);
        if(!node) return;
        if(!Array.isArray(node.pendingActionResumes)) node.pendingActionResumes = [];
        if(!node.pendingActionResumes.some(rec => rec?.actionId === entry.actionId)){
            node.pendingActionResumes.push({actionId:entry.actionId, kind:entry.kind, payload:entry.payload, enqueuedAt:entry.createdAt});
        }
    } catch(error){ /* mirror is best-effort; in-memory entry remains authoritative */ }
}
// #197: drop the persisted mark from the anchored node (used when the entry can no
// longer be replayed — node gone). The next save lands the removal.
function clearPendingActionFromStorage(entry){
    try {
        const node = (typeof canvasState !== 'undefined' && canvasState?.nodes || []).find(n => n && n.id === entry.nodeId);
        if(!node || !Array.isArray(node.pendingActionResumes)) return;
        node.pendingActionResumes = node.pendingActionResumes.filter(rec => rec?.actionId !== entry.actionId);
    } catch(error){ /* best-effort */ }
}
// #198: per-kind replay success check. conversation / photoshop / video replay
// closures THROW on a failed request, so a resolved replay means success. local-edit
// is the exception: submitLocalEditRequest (smart-canvas.js) swallows its own fetch
// error (toast + resolve), so the dispatcher verifies the observable effect — a
// successful local edit always assigns a FRESH output_asset_id (the endpoint has no
// server-side dedup, every accepted POST mints a new asset), so success means the
// node's output_asset_id changed from the pre-replay value.
function smartPendingActionReplaySucceeded(kind, beforeOutput, node){
    if(kind === 'local-edit'){
        const after = String(node?.output_asset_id || '');
        return Boolean(after) && after !== beforeOutput;
    }
    return true;
}
// #198: retry timer for a backoff-queued entry. Session-local: after a reload the
// timer is gone, but the persisted nextRetryAt on the mark re-arms it at seed time
// (the dispatcher schedules it whenever it sees a future nextRetryAt).
function schedulePendingActionRetryTimer(entry){
    if(!entry || entry._retryTimer) return;
    const delay = Math.max(0, Number(entry.nextRetryAt || 0) - Date.now());
    entry._retryTimer = setTimeout(() => {
        entry._retryTimer = null;
        if(smartPendingActionResumes.has(entry.actionId)) void resumeSmartPendingActionResumes(canvasState.nodes).catch(() => {});
    }, delay);
}
// #198: a replayed gate request failed AFTER its mark had already been cleared from
// disk (clear-on-send). Write the mark back onto the anchored node WITH the persisted,
// bounded retry count, re-queue with a backoff, and only drop permanently once the
// budget is exhausted.
function handlePendingActionReplayFailure(entry, freshNode){
    const now = Date.now();
    entry.replayAttempts = Number(entry.replayAttempts || 0) + 1;
    entry.lastReplayAttemptAt = now;
    entry.releasing = false;
    if(entry._retryTimer){ clearTimeout(entry._retryTimer); entry._retryTimer = null; }
    if(entry.replayAttempts >= SMART_PENDING_ACTION_MAX_REPLAY_ATTEMPTS){
        // Budget exhausted: drop permanently. The mark was already cleared from disk
        // by the persister right before this attempt fired (clear-on-send landed, or
        // the replay would not have run), so there is nothing left to persist.
        smartPendingActionResumes.delete(entry.actionId);
        clearPendingActionFromStorage(entry);
        smartPendingActionResumeLog.push({t:now, event:'dropped-retry-exhausted', kind:entry.kind, nodeId:entry.nodeId, attempts:entry.replayAttempts});
        return;
    }
    entry.nextRetryAt = now + SMART_PENDING_ACTION_RETRY_BACKOFF_MS[Math.min(entry.replayAttempts - 1, SMART_PENDING_ACTION_RETRY_BACKOFF_MS.length - 1)];
    // Write the mark back. The record carries the counter (replayAttempts) and the
    // backoff deadline (nextRetryAt) so a reload resumes with the same budget.
    const nodes = (typeof canvasState !== 'undefined' && canvasState?.nodes) || [];
    const node = freshNode || nodes.find(n => n && n.id === entry.nodeId);
    if(node){
        if(!Array.isArray(node.pendingActionResumes)) node.pendingActionResumes = [];
        node.pendingActionResumes = node.pendingActionResumes.filter(rec => rec?.actionId !== entry.actionId);
        node.pendingActionResumes.push({actionId:entry.actionId, kind:entry.kind, payload:entry.payload, enqueuedAt:entry.createdAt, replayAttempts:entry.replayAttempts, nextRetryAt:entry.nextRetryAt});
    }
    // Re-queue (the dispatcher removed the entry from the Map before the attempt fired).
    smartPendingActionResumes.set(entry.actionId, entry);
    smartPendingActionResumeLog.push({t:now, event:'replay-scheduled-retry', kind:entry.kind, nodeId:entry.nodeId, attempt:entry.replayAttempts, retryInMs:entry.nextRetryAt - now});
    schedulePendingActionRetryTimer(entry);
    // Persist the written-back mark NOW (not on the 3s debounce): the mark is a
    // durable node field, so the app's pagehide crash-recovery flush
    // (flushPendingCanvasSaveOnExit — the only registered pagehide listener) sees a
    // durable-snapshot diff and saves immediately. Dispatched through the built-in
    // Event API on purpose: the layer ratchet (tests/test_canvas_core_layer.py)
    // forbids any new bare reference from this module to a smart-canvas top-level
    // name, and #201 track 1 will later interface-ify this module properly.
    try { window.dispatchEvent(new Event('pagehide')); } catch(error){ /* best-effort */ }
}
function hasSmartPendingActionResume(kind, nodeId){
    for(const entry of smartPendingActionResumes.values()){
        if(entry.kind === kind && entry.nodeId === nodeId) return true;
    }
    return false;
}
// #189 follow-up: after a settled canvas save, replay queued gate requests whose node is
// still alive. Each entry is removed BEFORE its fetch fires, so a user action can never
// send twice (these endpoints have no server-side dedup). Node deleted => entry dropped.
async function resumeSmartPendingActionResumes(canvasNodes){
    const nodes = Array.isArray(canvasNodes) ? canvasNodes : [];
    // #197: seed the in-memory queue from marks persisted on nodes. After a reload the
    // Map is empty and the persisted mark on the node IS the "not-yet-sent" ledger
    // (clear-on-send removes it from disk the moment the fetch fires — no mark means
    // already sent, and the gate's own recovery path picks it up). In-session entries
    // already carry their live replay closure: they match their persisted record by
    // actionId and are not seeded twice.
    for(const node of nodes){
        const records = Array.isArray(node?.pendingActionResumes) ? node.pendingActionResumes : [];
        for(const rec of records){
            if(!rec || !rec.kind || !node?.id) continue;
            const alreadyQueued = [...smartPendingActionResumes.values()].some(e => e.kind === rec.kind && e.nodeId === node.id && (rec.actionId ? e.actionId === rec.actionId : true));
            if(alreadyQueued) continue;
            const actionId = rec.actionId || `action-${rec.kind}-seeded-${smartPendingActionResumeSeq}`;
            const attempts = Number(rec.replayAttempts || 0);
            if(attempts >= SMART_PENDING_ACTION_MAX_REPLAY_ATTEMPTS){
                // #198: the retry budget was exhausted in an earlier session — the
                // persisted mark is dead weight now; drop it (the next save persists
                // the removal) instead of firing one more doomed attempt.
                node.pendingActionResumes = node.pendingActionResumes.filter(r => r?.actionId !== actionId);
                smartPendingActionResumeLog.push({t:Date.now(), event:'dropped-retry-exhausted', kind:String(rec.kind), nodeId:String(node.id), attempts});
                continue;
            }
            smartPendingActionResumes.set(actionId, {actionId, kind:String(rec.kind), nodeId:String(node.id), replay:null, payload:rec.payload || null, createdAt:Number(rec.enqueuedAt || Date.now()), releasing:false, seededFromStorage:true, replayAttempts:attempts, nextRetryAt:Number(rec.nextRetryAt || 0)});
            smartPendingActionResumeLog.push({t:Date.now(), event:'seeded', kind:String(rec.kind), nodeId:String(node.id), attempts});
        }
    }
    if(!smartPendingActionResumes.size) return;
    // #197 clear-on-send: the persister commits "entry cleared" to disk BEFORE the
    // fetch fires. While it is pending or fails, the entry stays in the Map and on the
    // node, so a reload in that window still replays it; once it lands, a reload finds
    // no mark and the already-sent request is picked up by the gate's own recovery path.
    const persister = smartPendingActionPersister();
    for(const entry of [...smartPendingActionResumes.values()]){
        if(entry.releasing) continue; // another pass is already persisting this entry's clear
        const live = nodes.find(n => n && n.id === entry.nodeId);
        if(!live){
            smartPendingActionResumes.delete(entry.actionId);
            clearPendingActionFromStorage(entry);
            smartPendingActionResumeLog.push({t:Date.now(), event:'dropped-node-gone', kind:entry.kind, nodeId:entry.nodeId});
            continue;
        }
        // #198: bounded-retry backoff — a failed replay is re-queued with a future
        // nextRetryAt; save-settle passes before then must NOT re-fire it (the
        // write-back autosave settles one shortly after the failure). The retry timer
        // re-runs this pass when the window elapses.
        if(Number(entry.nextRetryAt || 0) > Date.now()){
            schedulePendingActionRetryTimer(entry);
            continue;
        }
        if(persister){
            entry.releasing = true;
            let cleared = false;
            try { cleared = (await persister(entry)) === true; } catch(error) { cleared = false; }
            if(!cleared){
                entry.releasing = false;
                smartPendingActionResumeLog.push({t:Date.now(), event:'clear-persist-pending', kind:entry.kind, nodeId:entry.nodeId});
                continue; // mark still on disk; retry on the next save settle
            }
        }
        // Single-shot: remove BEFORE running so a later save settle can never
        // re-fire it.
        smartPendingActionResumes.delete(entry.actionId);
        let replay = entry.replay;
        if(typeof replay !== 'function'){
            // #197: seeded from a persisted mark after a reload — the live closure is
            // gone; rebuild it from the serializable payload registered by the gate.
            const rebuild = smartPendingActionRebuilderFor(entry.kind);
            if(typeof rebuild !== 'function'){
                smartPendingActionResumeLog.push({t:Date.now(), event:'replay-missing-rebuilder', kind:entry.kind, nodeId:entry.nodeId});
                continue;
            }
            try { replay = rebuild(entry, live); } catch(error){
                smartPendingActionResumeLog.push({t:Date.now(), event:'replay-rebuild-failed', kind:entry.kind, nodeId:entry.nodeId, error:error?.message || String(error)});
                continue;
            }
        }
        if(typeof replay !== 'function'){
            smartPendingActionResumeLog.push({t:Date.now(), event:'replay-missing', kind:entry.kind, nodeId:entry.nodeId});
            continue;
        }
        // Fire-and-forget (void) like resumeSmartGenerationSubmission: replays can run
        // for minutes (video task polling) and must not block the markAutosaveSaved hook.
        void (async () => {
            let replayError = null;
            const beforeOutput = entry.kind === 'local-edit' ? String(live?.output_asset_id || '') : '';
            try {
                await replay(live);
            } catch(error){ replayError = error; }
            const fresh = (typeof canvasState !== 'undefined' && canvasState?.nodes || []).find(n => n && n.id === entry.nodeId) || live;
            const succeeded = !replayError && smartPendingActionReplaySucceeded(entry.kind, beforeOutput, fresh);
            if(succeeded){
                smartPendingActionResumeLog.push({t:Date.now(), event:'replayed', kind:entry.kind, nodeId:entry.nodeId});
            } else {
                // #198: the mark was already cleared from disk before this fetch fired;
                // without a write-back the user's action is permanently lost.
                const detail = replayError ? (replayError?.message || String(replayError)) : 'no-observable-effect';
                smartPendingActionResumeLog.push({t:Date.now(), event:'replay-failed', kind:entry.kind, nodeId:entry.nodeId, error:detail});
                if(typeof toast === 'function') toast((replayError ? String(replayError?.message || '').slice(0, 160) : '') || tr('smart.errRunFailed'));
                handlePendingActionReplayFailure(entry, fresh);
            }
        })();
    }
}
if(typeof window !== 'undefined'){
    // Test observability handles: publish this module's own state only (no cross-module reads).
    window.__fcPendingActionResumes = smartPendingActionResumes;
    window.__fcPendingActionResumeLog = smartPendingActionResumeLog;
}

// 这些名字本来属于本模块，#200 从 smart-canvas.js 搬回（核心不引用它们，纯搬家）。
const SMART_REFERENCE_IMAGE_MAX = 20;
let persistedCoreSnapshot = null;
function promptNodeLLMInputText(node, ctx=canvasCore.smartLoopContext){
    const upstream = promptNodeUpstreamPromptText(node, ctx).trim();
    const instruction = String(node?.llmInstruction || '').trim() || promptNodePromptItems(node).join('\n\n').trim();
    return [upstream, instruction].filter(Boolean).join('\n\n');
}
function videoProviderPlatform(providerId){
    const p = (apiProviders || []).find(x => x.id === providerId);
    const proto = String(p?.protocol || '').toLowerCase();
    const base = String(p?.base_url || '').toLowerCase();
    if(proto === 'apimart' || base.includes('apimart.ai')) return 'apimart';
    if(proto === 'volcengine' || providerId === 'volcengine') return 'volcengine';
    return '';
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
function applyUploadedUrlsToSmartRefs(refs, sourceSettings=settings){
    return (refs || []).map(ref => {
        if(!ref?.url) return ref;
        const sourceUrl = mediaRefSourceUrl(ref);
        const url = tempShUploadedUrlFor(sourceUrl, sourceSettings);
        return url && url !== ref.url ? {...ref, url, originalLocalUrl:ref.originalLocalUrl || ref.url} : ref;
    });
}
function manualSmartMediaLinks(sourceSettings=settings){
    return ((sourceSettings || settings).videoTempShLinks || []).filter(item => item?.manual === true && item?.url);
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
        canvasCore.render();
        if(smartHistoryModal?.classList.contains('open')) scheduleSmartHistoryWindowRender(true);
    }
    return changed;
}
function aspectRatioForRun(sourceSettings=settings){
    const ratio = sourceSettings.ratio || 'square';
    const presets = {
        square:'1:1', portrait:'2:3', landscape:'3:2', portrait43:'3:4',
        landscape43:'4:3', story:'9:16', wide:'16:9', ultrawide:'21:9', ultratall:'9:21',
        portrait45:'4:5', landscape45:'5:4', portrait14:'1:4', landscape14:'4:1', portrait18:'1:8', landscape18:'8:1'
    };
    if(ratio === 'custom' || ratio === 'source'){
        const explicit = String(sourceSettings.customRatio || '').trim();
        if(explicit) return explicit;
        const parsed = parseSizePair(sizeForRun(sourceSettings));
        if(parsed) return canvasCore.reducedAspectRatio(parsed.width, parsed.height);
        return '';
    }
    return presets[ratio] || '1:1';
}
function sleep(ms){ return new Promise(resolve => setTimeout(resolve, ms)); }
