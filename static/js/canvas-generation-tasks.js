// 画布生成任务：提交、待恢复任务续跑与失败重试（从 smart-canvas.js 抽离，#154）。
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
// Pending action runtime claims; durable records live on the anchored node.
// video/local-edit/photoshop retain clear-on-send. Conversation keeps its record
// and claim until formal registration is confirmed under the original request key;
// the server atomically consumes its outbox. Reload rebuilds the same intent.
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
    const current = previous.catch(error => { /* #348: 串行队列的故障隔离——前一个操作失败不阻塞下一个；每个操作自己负责 toast/错误处理 */ }).then(operation);
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

function requireImageProviderSelection(runSettings){
    const selection = canvasCore.imageProviderSelection(runSettings);
    if(selection.error) throw new Error(selection.error);
    return selection;
}

async function runApiGeneration(prompt, refs, runSettings=settings, resultNode=null, runOptions={}){
    const selection = requireImageProviderSelection(runSettings);
    const projectCanvas = canvas?.format === 'firefly-canvas-v1';
    const count = Math.max(1, Math.min(8, Number(runSettings.count || 1)));
    const pendingHistoryId = runOptions.pendingHistoryId || beginSmartPendingHistoryLog(resultNode, {
            prompt,
            kind:'image',
            providerId:runSettings.providerId || runSettings.provider_id || '',
            model:runSettings.model || '',
            runSettings
        });
    if(projectCanvas && !runOptions.registrationQueued){
        if(!resultNode?.id) throw new Error(tr('smart.projectImageMissingResult'));
        return queueSmartGenerationRegistration(resultNode.id, () => runApiGeneration(prompt, refs, runSettings, resultNode, {
            ...runOptions,
            pendingHistoryId,
            registrationQueued:true
        }));
    }
    // 经典画布入口没有自动路由；未固定模型时用当前服务商的第一个已勾选模型。
    const model = selection.selectedModel || (projectCanvas ? '' : selection.models[0]);
    // 两条发送路径都按对齐后的副本计算尺寸，不能改写节点保存的设置。
    runSettings = {...runSettings, provider_id:selection.providerId, model};
    canvasCore.alignRatioToModel(runSettings);
    if(projectCanvas){
        if(!resultNode?.id) throw new Error(tr('smart.projectImageMissingResult'));
        const outpaintSize = validOutpaintSize(resultNode);
        const usesExplicitResolution = apiImageUsesExplicitResolution(runSettings);
        // #187：扩图几何由 outpaintSize 的比例表达，不发像素尺寸，但仍保留用户的分辨率档位。
        const sendsExplicitSize = usesExplicitResolution && !outpaintSize;
        const size = sendsExplicitSize && runSettings.resolution !== 'auto' ? sizeForRun(runSettings) : '';
        const parsed = size ? canvasCore.parseSizeValue(size) : null;
        if(size && !parsed) throw new Error(tr('smart.imageSizeInvalid'));
        const imageRefs = imageRefsOnly(refs).slice(0, SMART_REFERENCE_IMAGE_MAX);
        const ownedRefs = await Promise.all(imageRefs.map(canvasCore.importProjectMediaReference));
        const reference_asset_ids = ownedRefs.filter(Boolean).map(item => item.asset_id).filter(Boolean);
        if(reference_asset_ids.length !== imageRefs.length) throw new Error(tr('smart.refsNotInProject'));
        // #396：角色位置必须对应实际上传顺序，Agent 和浏览器共用服务端提示词格式。
        const roles = imageRefs.map(ref => ref.agentRefRole || null);
        const projectId = canvas.project_id || canvas.project || canvasCore.canvasId;
        const payload = {
            prompt,
            ...(roles.some(Boolean) ? {reference_roles:roles} : {}),
            routing_mode:runSettings.routingMode === 'manual' ? 'manual' : 'auto',
            // 自动路由仍校验保存的模型；只有手动路由固定使用它。
            provider_id:selection.providerId,
            model:runSettings.model,
            size,
            width:parsed?.width || null,
            height:parsed?.height || null,
            aspect_ratio:aspectRatioForRun(runSettings),
            resolution:usesExplicitResolution ? (runSettings.resolution || '') : '',
            quality:runSettings.quality || 'auto',
            output_format:runSettings.outputFormat || '',
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
            // #189：保存未确认时保留提交标记，落盘后用同一幂等键重放，避免取消或重复注册。
            // 等待键独立于节点，因为 409 rebase 会替换节点并丢掉尚未落盘的内存标记。
            smartPendingPersistenceKeys.add(payload.idempotency_key);
            canvasCore.scheduleSave();
            const pendingError = new Error(tr('smart.canvasSavePendingResume'));
            pendingError.smartSavePendingResume = true;
            throw pendingError;
        }
        // 提交标记保存于前一 revision；首次注册要用最新版本，重复请求仍按幂等键返回原任务。
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
    requireImageProviderSelection(runSettings);
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
                if(taskIds.length) void resumeSmartPendingNode(freshNode, {taskIds}).catch(error => {
                    // #348: fire-and-forget 完成跟踪（上方注释：不得阻塞 resume 日志）；resumeSmartPendingNode 内部已按任务自行处理失败。
                    console.warn('[generation-tasks] 视频 pending 节点恢复失败', error);
                });
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
        const targetPlatform = videoProviderPlatform(runSettings.videoProvider || '');
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
            provider_id: runSettings.videoProvider || '',
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

function providerIdForSmartTask(node, task){
    // #349：末尾那个 || 'comfly' 拿掉了。查的是已有任务，猜一个服务商只会得到「任务不存在」，
    // 情原不查、说清楚原因，也不能拿别家的接口去查别家的任务。
    return task?.providerId || node?.runSettings?.provider_id || settings.provider_id || '';
}

async function fetchImageTaskQuery(providerId, taskId){
    if(!String(providerId || '').trim()){
        throw new Error('不知道这条任务属于哪家服务商，没法查询。请在该节点重新选定服务商后再查询。');
    }
    return fetch('/api/image-task-query', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({provider_id:providerId, task_id:taskId})
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
                const pendingHistoryRecorded = !failureResult?.history_recorded && failSmartPendingHistoryLog(taskId, task.error || tr('smart.errRunFailed'));
                const recoverTaskId = task.upstream_task_id || extractUpstreamTaskId(task.error || '');
                const error = recoverTaskId
                    ? new ImageTaskRecoverSignal({taskId, recoverTaskId, providerId:task.provider_id, kind:task.kind || 'image', message:task.error || tr('smart.errRunFailed')})
                    : new Error(task.error || tr('smart.errRunFailed'));
                // 服务端终态失败：前端必须直接进入 failedGenerationTasks，
                // 不能按关键词误判成网络错误做 3 轮无效重试（用户反馈的
                // “失败取回机制不生效”根因之一）。
                error.providerFailure = true;
                if(failureResult?.history_recorded || pendingHistoryRecorded) error.smartGenerationLogged = true;
                if(failureResult?.project_node_applied) error.projectNodeApplied = true;
                throw error;
            }
            if(task.status === 'timeout'){
                // #41：任务级硬超时是终态——按失败处理，立即清理 pending/计时器，
                // 不再进入 30 分钟轮询超时路径。
                const timeoutResult = absorbProjectGenerationResult(task.result || {}, taskId);
                const pendingHistoryRecorded = !timeoutResult?.history_recorded && failSmartPendingHistoryLog(taskId, task.error || tr('smart.errRunTimeout'));
                const error = new Error(task.error || tr('smart.errRunTimeout'));
                error.generationTimedOut = true;
                error.providerFailure = true;
                if(timeoutResult?.history_recorded || pendingHistoryRecorded) error.smartGenerationLogged = true;
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
        const group = canvasCore.smartGroupContainingNode(node.id);
        if(group && !isSmartLayoutFrame(group)){
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
    const outcomes = await Promise.all(tasks.map(async task => {
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
            return {succeeded:true};
        } catch(e) {
            if(e && e.imageTaskRecover && e.recoverTaskId){
                // #41：本地任务已终态失败，但有上游任务 ID 可供手动查询；
                // 恢复信息移入 recoveredGenerationTasks，再清理 pending，停止计时器和轮询。
                task.failed = true;
                task.querying = false;
                task.recoverTaskId = e.recoverTaskId;
                task.providerId = e.providerId || task.providerId || providerIdForSmartTask(node, task);
                task.error = e.message || tr('smart.errRunFailed');
                if(!e?.smartGenerationLogged){
                    logTaskFailure(task.error, task);
                    if(logContext.run) e.smartGenerationLogged = true;
                }
                node.recoveredGenerationTasks = Array.isArray(node.recoveredGenerationTasks) ? node.recoveredGenerationTasks : [];
                if(!node.recoveredGenerationTasks.some(item => item.taskId === task.taskId && item.recoverTaskId === task.recoverTaskId)){
                    node.recoveredGenerationTasks.push({taskId:task.taskId, recoverTaskId:task.recoverTaskId, providerId:task.providerId, kind:task.kind || 'image', error:task.error, failed:true, at:nowMs()});
                }
                if(smartPendingTasks(node).some(item => item.taskId === task.taskId)){
                    finalizeSmartPendingTask(node, task.taskId, [], task.kind || 'image', task.count || 1);
                }
                node.running = false;
                toast(tr('smart.taskNotLostLater'));
                canvasCore.render();
                canvasCore.scheduleSave();
                return {failure:e, recoverable:true};
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
                    if(!(node.images || []).length && !layoutFrameContainingNode(node.id)){
                        delete node.w;
                        delete node.h;
                    }
                }
                // 节点仍挂着该任务（如快照因 revision 冲突未应用）时补一次中性提示；
                // 常规 stop 流程里 POST /cancel 处理已 toast，不重复。
                if(taskStillPending) toast(tr('smart.taskCancelled'));
                canvasCore.render();
                canvasCore.scheduleSave();
                return {cancelled:true};
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
                    const remainingTasks = smartPendingTasks(node).filter(item => item.taskId !== task.taskId);
                    node.pendingTasks = remainingTasks;
                    node.pending = remainingTasks.length
                        ? Math.max(remainingTasks.length, remainingTasks.reduce((total, item) => total + Math.max(1, Number(item.count || 1)), 0))
                        : 0;
                }
                if(!node.pending && smartPendingTasks(node).length === 0){
                    delete node.pendingTasks;
                    node.running = false;
                    if(!(node.images || []).length && !layoutFrameContainingNode(node.id)){
                        delete node.w;
                        delete node.h;
                    }
                }
                if(!e?.smartGenerationLogged){
                    logTaskFailure(e.message || tr('smart.errRunFailed'), task);
                    if(logContext.run && e && typeof e === 'object') e.smartGenerationLogged = true;
                }
                toast((e.message || tr('smart.errRunFailed')).slice(0, 160));
                canvasCore.render();
                canvasCore.scheduleSave();
                return {failure:e};
            }
            
            task._retryCount = (task._retryCount || 0) + 1;
            if(task._retryCount <= 3){
                const delay = task._retryCount * 5000; // 5s, 10s, 15s
                toast(canvasCore.trf('smart.writeFailedRetry', {n: task._retryCount, s: delay/1000}));
                await sleep(delay);
                if(activeSmartTaskPolls.has(task.taskId)){
                    return;
                }
                // Retry only this task: siblings already have their own pollers and
                // must not be finalized a second time by the recursive call.
                return resumeSmartPendingNode(node, {...logContext, taskIds:[task.taskId]});
            }
            
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
                if(!(node.images || []).length && !layoutFrameContainingNode(node.id)){
                    delete node.w;
                    delete node.h;
                }
            }
            failures.push(e);
            if(!e?.smartGenerationLogged){
                logTaskFailure(e.message || tr('smart.errRunFailed'), task);
                if(logContext.run && e && typeof e === 'object') e.smartGenerationLogged = true;
            }
            toast(tr('smart.writeFailedMaxRetries'));
            canvasCore.render();
            canvasCore.scheduleSave();
        }
    }));
    if(failures.length){
        throw failures[0];
    }
    // Foreground callers need the original failure's history ownership, while
    // background recovery must continue to resolve on ordinary terminal errors.
    return {
        failure:outcomes.find(outcome => outcome?.failure)?.failure,
        cancelled:outcomes.some(outcome => outcome?.cancelled),
        recoverable:outcomes.some(outcome => outcome?.recoverable),
        succeeded:outcomes.some(outcome => outcome?.succeeded)
    };
}

async function retryFailedGeneration(nodeId){
    const node = canvasState.nodes.find(item => String(item.id || '') === String(nodeId));
    if(!node) return;
    // 防连点：旧提交标记的重发会生成新幂等键，不能并发执行。
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
    const readProjectId = () => canvas.project_id || canvas.project || canvasCore.canvasId;
    const projectId = readProjectId();
    let durableRetry;
    if(failedTasks.length){
        for(const failedTask of failedTasks){
            if(readProjectId() !== projectId) return;
            try{
                const url = projectImageTaskUrl(failedTask.taskId);
                const response = await fetch(url);
                if(!response.ok) continue;
                const task = await response.json();
                if(readProjectId() !== projectId) return;
                const outputs = Array.isArray(task?.result?.history_record?.outputs) ? task.result.history_record.outputs : [];
                if((task.status === 'succeeded' || task.status === 'failed') && outputs.length){
                    const base = `/api/project-library/${encodeURIComponent(projectId)}/media/`;
                    const images = outputs.map(item => ({url: base + encodeURIComponent(item.asset_id || ''), kind: item.kind || 'image'}));
                    node.failedGenerationTasks = [];
                    finalizeSmartPendingTask(node, failedTask.taskId, images, 'image', images.length);
                    canvasCore.render();
                    canvasCore.scheduleSave();
                    toast(canvasCore.trf('smart.recoveredResults', {n: images.length}));
                    return;
                }
                const payload = task.request_payload;
                // 带上游诊断的任务走原有取回流程，不能把未取回当作允许重新计费。
                if(!durableRetry && url.startsWith('/api/project-library/') &&
                    String(failedTask.taskId || '').startsWith('project_img_') &&
                    task.status === 'failed' && !task.upstream_task_id && !task.recover &&
                    payload?.result_node_id === node.id && payload.prompt){
                    durableRetry = {url, payload, failedId:failedTask.taskId};
                }
            }catch(_){
                // #348: 取回失败仍可检查原请求是否能重新提交，保留日志用于排查。
                console.warn('[generation-tasks] 失败任务取回请求出错，将检查是否可重新提交', _);
            }
        }
    }
    if(readProjectId() !== projectId) return;
    if(durableRetry){
        const {url, payload, failedId} = durableRetry;
        try {
            // #438：服务端按原任务去重。即使 POST 已落盘但响应丢失，再点也是同一次重试。
            const response = await fetch(`${url}/retry`, {method:'POST'});
            const task = await response.json();
            if(!response.ok || !task.task_id || !task.idempotency_key){
                throw new Error(typeof task.detail === 'string' ? task.detail : tr('smart.recoverSubmitFailed'));
            }
            if(readProjectId() !== projectId) return;
            await resumeSmartGenerationSubmission(node, {
                requestPayload:payload,
                idempotencyKey:task.idempotency_key
            }, {task, failedId});
        } catch(error) {
            toast((error?.message || tr('smart.recoverSubmitFailed')).slice(0, 160));
        }
        return;
    }
    const submission = smartGenerationSubmissions(node).find(item => item && item.requestPayload);
    if(submission){
        node.failedGenerationTasks = [];
        // 显式重试使用新幂等键，避免服务端重放同一个已失败任务。
        submission.idempotencyKey = `regen-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
        await resumeSmartGenerationSubmission(node, submission);
        canvasCore.render();
        return;
    }
    canvasCore.render();
    toast(tr('smart.noRetryableRequest'));
}

async function resumeSmartGenerationSubmission(node, submission, registeredRetry){
    const nodeId = String(node?.id || '');
    const idempotencyKey = String(submission?.idempotencyKey || '');
    const recoveryKey = `${nodeId}:${idempotencyKey}`;
    if(!nodeId || !idempotencyKey || activeSmartSubmissionRecoveries.has(recoveryKey)) return;
    activeSmartSubmissionRecoveries.add(recoveryKey);
    try {
        const projectId = canvas.project_id || canvas.project || canvasCore.canvasId;
        const payload = {...(submission.requestPayload || {}), idempotency_key:idempotencyKey, result_node_id:nodeId, base_revision:Number(canvas.revision || 0)};
        const task = registeredRetry?.task || await postProjectImageTaskWithReplay(projectId, payload);
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
        if(registeredRetry){
            currentNode.failedGenerationTasks = currentNode.failedGenerationTasks?.filter(item => item.taskId !== registeredRetry.failedId) || [];
        }else{
            clearSmartGenerationSubmission(currentNode, idempotencyKey);
        }
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
        if(!registeredRetry && !error?.smartSubmissionUnknown){
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
        // #348: 页面加载后的后台恢复；resumeSmartPendingNode 内部已按任务自行处理失败。
        void resumeSmartPendingNode(node).catch(error => { /* #348: 同上——后台恢复的 fire-and-forget 跟踪 */ });
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
// Queue a failed save gate, or EVERY new conversation intent. Conversation can
// omit the closure: both first send and reload use its registered rebuilder.
// scheduleSave persists the node record; save-settle dispatches under one claim.
function queueSmartPendingActionResume(kind, nodeId, replay, payload){
    if(!nodeId) return null;
    smartPendingActionResumeSeq += 1;
    const conversationKey = kind === 'conversation'
        ? JSON.stringify([payload.projectId, String(nodeId), payload.requestId]) : '';
    if(conversationKey){
        for(const pending of smartPendingActionResumes.values()){
            if(pending.kind === kind && pending.nodeId === String(nodeId) && pending.payload?.requestId === payload.requestId) return pending.actionId;
        }
    }
    const actionId = conversationKey ? `action-conversation-${conversationKey}` : `action-${kind}-${smartPendingActionResumeSeq}`;
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
    } catch(error){
        // #348: mirror 是尽力而为（见上方 #197 注释）；内存项仍是权威来源，下一轮 dispatch 会再 mirror。
        console.warn('[generation-tasks] pending-action mirror 写入节点失败', error);
    }
}
// #197: drop the persisted mark from the anchored node (used when the entry can no
// longer be replayed — node gone). The next save lands the removal.
function clearPendingActionFromStorage(entry){
    try {
        const node = (typeof canvasState !== 'undefined' && canvasState?.nodes || []).find(n => n && n.id === entry.nodeId);
        if(!node || !Array.isArray(node.pendingActionResumes)) return;
        node.pendingActionResumes = node.pendingActionResumes.filter(rec => rec?.actionId !== entry.actionId);
    } catch(error){ /* #348: 尽力而为——清不掉时下次保存会覆盖；内存队列已移除 */ }
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
        // Expected save/network failures are handled per entry. Unexpected
        // dispatcher bugs must propagate instead of disappearing into a log.
        if(smartPendingActionResumes.has(entry.queueKey || entry.actionId)) void resumeSmartPendingActionResumes(canvasState.nodes);
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
    if(entry.kind === 'conversation'){
        // The server may already have consumed this outbox. Never mirror it back,
        // dispatch pagehide, or discard the only durable intent on retry exhaustion.
        entry.blocked = entry.conversationConflict === true;
        entry.nextRetryAt = now + SMART_PENDING_ACTION_RETRY_BACKOFF_MS[Math.min(entry.replayAttempts - 1, SMART_PENDING_ACTION_RETRY_BACKOFF_MS.length - 1)];
        smartPendingActionResumes.set(entry.queueKey || entry.actionId, entry);
        if(!entry.blocked) schedulePendingActionRetryTimer(entry);
        return;
    }
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
    try { window.dispatchEvent(new Event('pagehide')); } catch(error){ /* #348: 尽力而为——pagehide 监听器自身出错不影响退避重排已完成 */ }
}
function hasSmartPendingActionResume(kind, nodeId){
    for(const entry of smartPendingActionResumes.values()){
        if(entry.kind === kind && entry.nodeId === nodeId) return true;
    }
    return false;
}
// Replay live-node gates after save-settle. Non-conversation entries are removed
// before fetch; conversation keeps one in-flight claim until confirmed. Node gone => drop.
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
            const alreadyQueued = [...smartPendingActionResumes.values()].some(e => e.kind === rec.kind && e.nodeId === node.id && (rec.kind === 'conversation' && rec.payload?.requestId ? e.payload?.requestId === rec.payload.requestId : (rec.actionId ? e.actionId === rec.actionId : true)));
            if(alreadyQueued) continue;
            const actionId = rec.actionId || `action-${rec.kind}-seeded-${smartPendingActionResumeSeq}`;
            const attempts = Number(rec.replayAttempts || 0);
            if(rec.kind !== 'conversation' && attempts >= SMART_PENDING_ACTION_MAX_REPLAY_ATTEMPTS){
                // #198: the retry budget was exhausted in an earlier session — the
                // persisted mark is dead weight now; drop it (the next save persists
                // the removal) instead of firing one more doomed attempt.
                node.pendingActionResumes = node.pendingActionResumes.filter(r => r?.actionId !== actionId);
                smartPendingActionResumeLog.push({t:Date.now(), event:'dropped-retry-exhausted', kind:String(rec.kind), nodeId:String(node.id), attempts});
                continue;
            }
            const queueKey = rec.kind === 'conversation'
                ? `action-conversation-${JSON.stringify([rec.payload?.projectId || '', String(node.id), rec.payload?.requestId || actionId])}` : actionId;
            smartPendingActionResumes.set(queueKey, {actionId, queueKey, kind:String(rec.kind), nodeId:String(node.id), replay:null, payload:rec.payload || null, createdAt:Number(rec.enqueuedAt), releasing:false, seededFromStorage:true, replayAttempts:attempts, nextRetryAt:Number(rec.nextRetryAt || 0)});
            smartPendingActionResumeLog.push({t:Date.now(), event:'seeded', kind:String(rec.kind), nodeId:String(node.id), attempts});
        }
    }
    if(!smartPendingActionResumes.size) return;
    // The registered persister clears non-conversation marks before sending.
    // For conversation it instead verifies/migrates the durable original outbox.
    const persister = smartPendingActionPersister();
    for(const entry of [...smartPendingActionResumes.values()]){
        if(entry.releasing || entry.blocked) continue; // one claim across save-settle, first send and reload
        const live = nodes.find(n => n && n.id === entry.nodeId);
        if(!live){
            smartPendingActionResumes.delete(entry.queueKey || entry.actionId);
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
        entry.releasing = true;
        if(persister){
            let cleared = false;
            if(entry.kind === 'conversation'){
                try { cleared = (await persister(entry)) === true; } catch(error) {
                    entry.conversationConflict = error.conversationConflict === true;
                    toast(error.message || tr('smart.conversationSendFailed'));
                    handlePendingActionReplayFailure(entry, live);
                    continue;
                }
            } else {
                cleared = (await persister(entry)) === true;
            }
            if(!cleared){
                entry.releasing = false;
                smartPendingActionResumeLog.push({t:Date.now(), event:'clear-persist-pending', kind:entry.kind, nodeId:entry.nodeId});
                continue; // mark still on disk; retry on the next save settle
            }
        }
        // Non-deduplicated gates remain single-shot. Conversation holds the claim.
        if(entry.kind !== 'conversation') smartPendingActionResumes.delete(entry.actionId);
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
                if(entry.kind === 'conversation') smartPendingActionResumes.delete(entry.queueKey || entry.actionId);
                smartPendingActionResumeLog.push({t:Date.now(), event:'replayed', kind:entry.kind, nodeId:entry.nodeId});
            } else {
                // Non-conversation needs #198 write-back. Conversation's failure
                // handler retains only runtime state: its mark may be consumed.
                const detail = replayError ? (replayError?.message || String(replayError)) : 'no-observable-effect';
                smartPendingActionResumeLog.push({t:Date.now(), event:'replay-failed', kind:entry.kind, nodeId:entry.nodeId, error:detail});
                if(typeof toast === 'function') toast((replayError ? String(replayError?.message || '').slice(0, 160) : '') || tr('smart.errRunFailed'));
                entry.conversationConflict = replayError?.conversationConflict === true;
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
