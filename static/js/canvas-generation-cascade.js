// 画布级联与循环生成调度：生成输出项、下游目标解析、并行上限与停止请求（从 smart-canvas.js 抽离，#154）。
// invariant: classic script，按 HTML 顺序先于 smart-canvas.js 加载；本文件顶层只有声明，
// 初始化器只取空容器或字面量，不读其他模块的状态。函数声明整体提升，实际调用都发生在
// 主脚本初始化之后。跨模块可变状态一律走 canvasState（window.canvasState），不要引用裸 nodes/selectedId。

let smartCascadeSilentSelection = false;

let smartRunStateToken = 0;

const smartNodeRunTokens = new Map();

function nextOutputPositionForSource(sourceNode, pendingBox, options={}){
    const sourceRect = nodeRect(sourceNode);
    const x = (sourceRect.x || 0) + sourceRect.width + 80;
    const gap = 28;
    const outputs = outgoingConnectionsFor(sourceNode, ['input','flow'])
        .map(conn => canvasState.nodes.find(n => n.id === conn.to))
        .filter(n => isSmartImageNode(n))
        .map(n => nodeRect(n))
        .filter(rect => Math.abs((rect.x || 0) - x) < Math.max(320, (pendingBox?.w || 260) + 120))
        .sort((a, b) => (a.y || 0) - (b.y || 0));
    if(!outputs.length) return {x, y:sourceRect.y || 0};
    let y = sourceRect.y || 0;
    for(const rect of outputs){
        const bottom = (rect.y || 0) + (rect.height || 0) + gap;
        if(y < bottom) y = bottom;
    }
    return {x, y};
}

function createPendingOutputFromSource(sourceNode, expectedCount, meta, options={}){
    const pendingBox = pendingBoxSize(expectedCount, {sourceNode, refs:options.refs || meta?.promptRefs || []});
    const pos = nextOutputPositionForSource(sourceNode, pendingBox);
    const output = {
        id:uid('smart'),
        type:'smart-image',
        x:pos.x,
        y:pos.y,
        title:'Image',
        images:[],
        pending:Math.max(1, Number(expectedCount) || 1),
        _batchTotal:Math.max(1, Number(expectedCount) || 1),
        runStartedAt:nowMs(),
        runTimerHidden:false,
        w:pendingBox.w,
        h:pendingBox.h,
        scale:MEDIA_NODE_DEFAULT_SCALE,
        created_at:Date.now()
    };
    output._selectAfterRunId = options.selectOutput ? output.id : sourceNode.id;
    canvasState.nodes.push(output);
    if(options.connectSource === false) addConnection(sourceNode.id, output.id, 'flow');
    else connectInputNode(sourceNode.id, output.id);
    attachRunMeta(output, options.stripInputMeta ? stripRunInputMeta(meta) : meta);
    canvasState.selectedId = sourceNode.id;
    canvasState.selectedImage = {nodeId:'', index:-1};
    return output;
}

function loopOutputSlotsForRoot(rootNode){
    if(!rootNode?.id) return [];
    return downstreamNodesForId(rootNode.id)
        .filter(n => isSmartImageNode(n) && !isHistoryGroupNode(n))
        .sort((a, b) => {
            const ax = Number(a.x) || 0, bx = Number(b.x) || 0;
            if(ax !== bx) return ax - bx;
            return (Number(a.y) || 0) - (Number(b.y) || 0);
        });
}

function loopOutputSlotForRound(rootNode, loopNode, roundIndex, slotIndex){
    if(!rootNode?.id) return null;
    const candidates = loopOutputSlotsForRoot(rootNode)
        .filter(node => node.sourceNodeId === rootNode.id)
        .filter(node => !loopNode?.id || !node.loopSourceId || node.loopSourceId === loopNode.id);
    const untagged = candidates.filter(node => !Number.isFinite(Number(node.loopRoundIndex)) && !Number.isFinite(Number(node.loopSlotIndex)));
    return candidates.find(node => Number(node.loopRoundIndex) === Number(roundIndex))
        || candidates.find(node => Number(node.loopSlotIndex) === Number(slotIndex))
        || untagged[Math.max(0, Number(slotIndex) || 0)]
        || null;
}

function tagLoopOutputSlot(output, rootNode, loopNode, roundIndex, slotIndex){
    if(!output) return output;
    output.sourceNodeId = rootNode?.id || output.sourceNodeId || '';
    output.loopSourceId = loopNode?.id || output.loopSourceId || '';
    output.loopRootId = rootNode?.id || output.loopRootId || '';
    output.loopRoundIndex = Number(roundIndex) || 0;
    output.loopSlotIndex = Math.max(0, Number(slotIndex) || 0);
    return output;
}

function createLoopOutputSlot(rootNode, roundIndex, roundOffset=0, options={}){
    const rootRect = nodeRect(rootNode);
    const output = cloneSmartNode(rootNode, 0, 0);
    output.id = uid('smart');
    output.type = 'smart-image';
    output.x = (Number(rootNode.x) || 0) + (Number(rootRect.width) || 260) + 80;
    output.title = `Image ${roundIndex}`;
    output.images = [];
    output.pending = options.pending ? Math.max(1, Number(options.pending) || 1) : 0;
    output.running = Boolean(options.pending);
    output.queued = Boolean(options.queued);
    if(options.pending){
        output.runStartedAt = nowMs();
        output.runTimerHidden = false;
    }
    output.created_at = Date.now();
    delete output.w;
    delete output.h;
    delete output.historyFor;
    delete output.isHistoryGroup;
    delete output.sourceNodeId;
    delete output.runAt;
    delete output.runPrompt;
    delete output.runModelPrompt;
    delete output.runPromptRefs;
    delete output.runInputRefs;
    delete output.runFinishedAt;
    delete output.runElapsedMs;
    // 同 createParallelLoopOutputNode：清空克隆带来的 inputNodeIds，否则输出槽虽只用 flow
    // 连接到 root，却会因继承 root 的 inputNodeIds 而误显示上游提示词输入。
    output.inputNodeIds = [];
    delete output.blockedInputRefs;
    delete output.manualInputRefs;
    tagLoopOutputSlot(output, rootNode, options.loopNode || null, roundIndex, options.slotIndex ?? roundOffset);
    const slots = loopOutputSlotsForRoot(rootNode).map(nodeRect);
    let y = (Number(rootNode.y) || 0) + roundOffset * ((Number(rootRect.height) || 180) + 28);
    slots.forEach(rect => {
        if((Number(rect.x) || 0) >= (Number(output.x) || 0) - 24){
            y = Math.max(y, (Number(rect.y) || 0) + (Number(rect.height) || 0) + 28);
        }
    });
    output.y = y;
    canvasState.nodes.push(output);
    addConnection(rootNode.id, output.id, 'flow');
        const runPath = smartCascadePathForCtx(options.ctx || options.runState);
        if(runPath?.states) runPath.states[`${rootNode.id}->${output.id}`] = 'wait';
    return output;
}

function finalizePendingNode(pendingNode, urls, meta, kind='image'){
    if(!pendingNode) return;
    pendingNode = liveSmartNode(pendingNode);
    const ext = kind === 'video' ? 'mp4' : kind === 'audio' ? 'mp3' : kind === 'text' ? 'txt' : 'png';
    const imgs = cleanHistoryImages(urls.map((item, i) => {
        const url = typeof item === 'string' ? item : item?.url || '';
        const itemKind = (typeof item === 'object' && item.kind) || kind;
        return copyMediaSizeFields(item, {url, name:(typeof item === 'object' && item.name) || `output-${i + 1}.${ext}`, kind:itemKind, generatedResult:true, ...(typeof item === 'object' && item.mime_type ? {mime_type:item.mime_type} : {})});
    }).filter(img => img.url));
    pendingNode.images = imgs;
    markSmartNodeComplete(pendingNode, meta, true);
    pendingNode.outputKind = kind;
    if(imgs.length > 1) pendingNode.title = kind === 'video' ? 'Videos' : kind === 'audio' ? 'Audios' : kind === 'text' ? 'Texts' : 'Group';
    else pendingNode.title = kind === 'video' ? 'Video' : kind === 'audio' ? 'Audio' : kind === 'text' ? 'Text' : kind === 'file' ? 'File' : 'Image';
    pendingNode.scale = mediaNodeDefaultScale(pendingNode);
    delete pendingNode.w;
    delete pendingNode.h;
    const metaTarget = pendingNode._runMetaTargetId ? canvasState.nodes.find(n => n.id === pendingNode._runMetaTargetId) : pendingNode;
    if(metaTarget) attachRunMeta(metaTarget, meta);
    pendingNode.images = (pendingNode.images || []).map(img => stripImageGenerationMeta(img));
    clearSourceBusyStateIfDownstreamDone(canvasState.nodes.find(n => n.id === meta?.sourceNodeId));
    // 生成完成不抢占选择:仅当用户仍停留在该生成节点上(或当前无选择)时才切换选择;
    // 否则保留用户当前选择 —— 支持并发生成时去调整/编辑别的卡片,A 节点的参数栏不被打断。
    const afterRunSelection = pendingNode._selectAfterRunId || pendingNode.id;
    if(!canvasState.selectedId || canvasState.selectedId === pendingNode.id) canvasState.selectedId = afterRunSelection;
    delete pendingNode._runMetaTargetId;
    delete pendingNode._selectAfterRunId;
    if(canvasState.activeComposerSubject?.id && canvasState.selectedId === canvasState.activeComposerSubject.id) canvasState.lastComposerNodeId = `${canvasState.selectedId}:node`;
    canvasState.selectedImage = {nodeId:'', index:-1};
    // pending 节点完成时，如果节点在组框内，重新整理成员并计算组框高度
    const group = smartGroupContainingNode(pendingNode.id);
    if(group){
        arrangeSmartGroupMembers(group, {skipUndo:true});
        scheduleSave();
    }
}

function restoreFromExtraction(node, extracted){
    if(!node || !extracted) return;
    node.images = extracted.images.slice();
    if(Number.isFinite(Number(extracted.w))) node.w = extracted.w;
    if(Number.isFinite(Number(extracted.h))) node.h = extracted.h;
    canvasState.nodes = canvasState.nodes.filter(n => n.id !== extracted.id); if(canvas) canvas.nodes = canvasState.nodes;
    canvas.connections = (canvas.connections || []).filter(c => !(c.from === extracted.id && c.to === node.id));
    if(Array.isArray(node.inputNodeIds)){
        node.inputNodeIds = node.inputNodeIds.filter(id => id !== extracted.id);
    }
}

function restoreSourceVisualState(node, state){
    if(!node || !state) return;
    node.images = (state.images || []).map(img => ({...img}));
    node.title = state.title || (node.images.length > 1 ? 'Group' : 'Image');
    ['w','h','scale','outputKind'].forEach(key => {
        if(state[key] === undefined) delete node[key];
        else node[key] = state[key];
    });
}

function finishLoopTargetPreviewState(node){
    if(!node) return;
    node = liveSmartNode(node);
    markSmartNodeComplete(node);
    if((node.images || []).some(img => img?.url)){
        node.title = node.images.length > 1 ? 'Group' : 'Image';
        node.scale = node.images.length > 1 ? MEDIA_GROUP_DEFAULT_SCALE : MEDIA_NODE_DEFAULT_SCALE;
        node.outputKind = mediaKindForUrls(node.images || [], (node.images || []).some(isVideoMediaItem) ? 'video' : 'image');
        delete node.w;
        delete node.h;
    }
}

function refsForDirectLoopRound(loopNode, loopIndex, total){
    if(!loopNode?.imageInput) return [];
    return outputImagesForNode(loopNode, true, {index:loopIndex, total, nodeId:loopNode.id})
        .filter(ref => ref?.url)
        .map((ref, index) => ({
            ...ref,
            role:ref.role || `image_${index + 1}`,
            name:ref.name || trf('canvas.loopImageLabel', {n:loopIndex + index})
        }));
}

function showDirectLoopRoundPreview(loopNode, target, refs, loopIndex, total){
    if(!loopNode?.imageInput || !isSmartImageNode(target)) return false;
    const cleanRefs = (refs || []).filter(ref => ref?.url);
    if(!cleanRefs.length) return false;
    const preview = cleanRefs.map((ref, index) => stripImageGenerationMeta({
        asset_id:ref.asset_id || '',
        url:ref.url || '',
        name:ref.name || trf('canvas.loopImageLabel', {n:loopIndex + index}),
        kind:ref.kind || (isVideoMediaItem(ref) ? 'video' : 'image'),
        nodeId:ref.nodeId || '',
        imageIndex:ref.imageIndex ?? '',
        loopInputPreview:true
    })).filter(ref => ref.url);
    if(!preview.length) return false;
    target.images = preview;
    target.pending = 0;
    target.running = true;
    target.runStartedAt = nowMs();
    delete target.runFinishedAt;
    delete target.runElapsedMs;
    target.runTimerHidden = false;
    target.runInputRefs = cleanRefs.map(ref => ({
        asset_id:ref.asset_id || '',
        url:ref.url || '',
        name:ref.name || '',
        nodeId:ref.nodeId || '',
        imageIndex:ref.imageIndex ?? '',
        kind:ref.kind || ''
    })).filter(ref => ref.url);
    target.outputKind = mediaKindForUrls(preview, preview.some(isVideoMediaItem) ? 'video' : 'image');
    target.scale = preview.length > 1 ? MEDIA_GROUP_DEFAULT_SCALE : MEDIA_NODE_DEFAULT_SCALE;
    target.title = total > 1 ? `Image ${loopIndex}/${total}` : (target.title || 'Image');
    delete target.w;
    delete target.h;
    render();
    return true;
}

function directImageInputsFor(node){
    const upstream = smartImageUsesWorkflowInput(node) ? workflowInputNodesFor(node) : inputNodesFor(node);
    return upstream
        .filter(n => isSmartImageNode(n) && !isHistoryGroupNode(n) && (n.images || []).some(img => img?.url))
        .sort((a, b) => {
            const ax = Number(a.x) || 0, bx = Number(b.x) || 0;
            if(ax !== bx) return bx - ax;
            return (Number(a.y) || 0) - (Number(b.y) || 0);
        });
}

function directImageInputsForKinds(node, kinds=['input']){
    const upstream = upstreamNodesForKinds(node, kinds);
    return upstream
        .filter(n => isSmartImageNode(n) && !isHistoryGroupNode(n) && (n.images || []).some(img => img?.url))
        .sort((a, b) => {
            const ax = Number(a.x) || 0, bx = Number(b.x) || 0;
            if(ax !== bx) return bx - ax;
            return (Number(a.y) || 0) - (Number(b.y) || 0);
        });
}

function primaryImageInputFor(node, options={}){
    const direct = options.includeFlow
        ? directImageInputsForKinds(node, ['input', 'flow'])[0]
        : directImageInputsFor(node)[0];
    if(direct) return direct;
    const inputs = options.includeFlow ? upstreamNodesForKinds(node, ['input', 'flow']) : (smartImageUsesWorkflowInput(node) ? workflowInputNodesFor(node) : inputNodesFor(node));
    const loop = inputs.find(n => n?.type === 'smart-loop');
    if(loop?.imageInput){
        const upstream = upstreamNodesForKinds(loop, options.includeFlow ? ['input', 'flow'] : ['input']).find(n => isSmartImageNode(n) && (n.images || []).some(img => img?.url));
        if(upstream) return upstream;
    }
    return null;
}

function hasDownstreamImageNode(node){
    return downstreamNodesForId(node?.id).some(n => isSmartImageNode(n) && !isHistoryGroupNode(n));
}

function isGeneratedOutputForNode(sourceNode, targetNode){
    return Boolean(sourceNode?.id && targetNode?.sourceNodeId === sourceNode.id);
}

function downstreamWorkflowImageTargetsFor(node){
    return downstreamImageTargetsFor(node).filter(target => !isGeneratedOutputForNode(node, target));
}

function hasDownstreamWorkflowImageNode(node){
    return downstreamWorkflowImageTargetsFor(node).length > 0;
}

function smartImageChainTo(nodeId, options={}){
    const tail = canvasState.nodes.find(n => n.id === nodeId);
    if(!isSmartImageNode(tail) || isHistoryGroupNode(tail)) return [];
    const chain = [];
    const seen = new Set();
    let cur = tail;
    while(cur && !seen.has(cur.id)){
        seen.add(cur.id);
        chain.unshift(cur);
        cur = primaryImageInputFor(cur, options);
    }
    return chain;
}

function upstreamNodesForId(nodeId, kinds=['input']){
    const result = [];
    const seen = new Set([nodeId]);
    const walk = id => {
        upstreamNodesForKinds(canvasState.nodes.find(n => n.id === id), kinds).forEach(input => {
            if(seen.has(input.id)) return;
            seen.add(input.id);
            walk(input.id);
            result.push(input);
        });
    };
    walk(nodeId);
    return result;
}

function resolveSmartCascadeLoop(nodeId){
    const loops = upstreamNodesForId(nodeId, ['input', 'flow']).filter(n => n.type === 'smart-loop');
    if(!loops.length) return null;
    const loop = loops[loops.length - 1];
    return {node:loop, count:smartLoopCount(loop), mode:loop.mode === 'parallel' ? 'parallel' : 'serial'};
}

function relayLoopPromptNodesForEdge(sourceNode, targetNode){
    if(!sourceNode?.id || !targetNode?.id) return [];
    const directLoopIds = new Set(promptInputNodesFor(targetNode).filter(n => n?.type === 'smart-loop' && n.showPrompt).map(n => n.id));
    return inputNodesFor(sourceNode)
        .filter(n => n?.type === 'smart-loop' && n.showPrompt && !directLoopIds.has(n.id));
}

function relayLoopPromptNodesForTarget(node){
    if(!node?.id) return [];
    return inputNodesFor(node).filter(n => n?.type === 'smart-loop' && n.showPrompt);
}

function downstreamNodesForId(nodeId){
    const result = [];
    const seen = new Set([nodeId]);
    const walk = id => {
        (canvas?.connections || [])
            .filter(conn => conn.from === id && ['input','flow'].includes(conn.kind || 'flow'))
            .map(conn => canvasState.nodes.find(n => n.id === conn.to))
            .filter(Boolean)
            .forEach(next => {
                if(seen.has(next.id)) return;
                seen.add(next.id);
                result.push(next);
                walk(next.id);
            });
    };
    walk(nodeId);
    return result;
}

function downstreamImageTargetsFor(node){
    if(!node?.id) return [];
    return (canvas?.connections || [])
        .filter(conn => conn.from === node.id && ['input','flow'].includes(conn.kind || 'flow'))
        .map(conn => canvasState.nodes.find(n => n.id === conn.to))
        .filter(n => isSmartImageNode(n) && !isHistoryGroupNode(n))
        .sort((a, b) => {
            const ax = Number(a.x) || 0, bx = Number(b.x) || 0;
            if(ax !== bx) return ax - bx;
            return (Number(a.y) || 0) - (Number(b.y) || 0);
        });
}

function downstreamCascadeTargetsFor(node){
    if(!node?.id) return [];
    return (canvas?.connections || [])
        .filter(conn => conn.from === node.id && ['input','flow'].includes(conn.kind || 'flow'))
        .map(conn => canvasState.nodes.find(n => n.id === conn.to))
        .filter(n => n && !isHistoryGroupNode(n) && (isSmartImageNode(n) || n.type === 'smart-loop'))
        .sort((a, b) => {
            const ax = Number(a.x) || 0, bx = Number(b.x) || 0;
            if(ax !== bx) return ax - bx;
            return (Number(a.y) || 0) - (Number(b.y) || 0);
        });
}

function directLoopRunTargets(loop){
    if(!loop?.id) return [];
    return downstreamImageTargetsFor(loop)
        .filter(node => !hasDownstreamWorkflowImageNode(node));
}

function smartCascadeGraphForTail(tail){
    const path = smartImageChainTo(tail?.id, {includeFlow:true}).filter(n => isSmartImageNode(n) && !isHistoryGroupNode(n));
    if(!path.length) return {root:null, path:[], edges:[], children:new Map()};
    const loop = resolveSmartCascadeLoop(tail?.id);
    const loopRoots = loop?.node?.id ? downstreamImageTargetsFor(loop.node) : [];
    const loopRoot = loopRoots.find(n => path.some(p => p.id === n.id));
    const root = loopRoot || path[0];
    const edges = [];
    const children = new Map();
    const seenEdges = new Set();
    const visiting = new Set();
    const walk = node => {
        if(!node?.id || visiting.has(node.id)) return;
        visiting.add(node.id);
        const targets = downstreamCascadeTargetsFor(node);
        children.set(node.id, targets);
        targets.forEach(target => {
            const key = `${node.id}->${target.id}`;
            if(!seenEdges.has(key)){
                seenEdges.add(key);
                edges.push({source:node, target, key});
            }
            walk(target);
        });
        visiting.delete(node.id);
    };
    walk(root);
    return {root, path, edges, children};
}

function cascadeTailForLoop(loopId){
    const loop = canvasState.nodes.find(n => n.id === loopId && n.type === 'smart-loop');
    const directTargets = directLoopRunTargets(loop);
    if(directTargets.length) return directTargets[directTargets.length - 1];
    const directImages = downstreamImageTargetsFor({id:loopId});
    const directIds = new Set(directImages.map(n => n.id));
    const candidates = downstreamNodesForId(loopId)
        .filter(n => isSmartImageNode(n))
        .filter(n => !isHistoryGroupNode(n))
        .filter(n => canRunSmartCascade(n));
    if(!candidates.length) return null;
    return candidates.sort((a, b) => {
        const ad = directIds.has(a.id) ? 1 : 0;
        const bd = directIds.has(b.id) ? 1 : 0;
        if(ad !== bd) return ad - bd;
        const ax = Number(a.x) || 0, bx = Number(b.x) || 0;
        if(ax !== bx) return bx - ax;
        return (Number(b.y) || 0) - (Number(a.y) || 0);
    })[0];
}

function canRunSmartCascade(node){
    if(!isSmartImageNode(node) || isHistoryGroupNode(node)) return false;
    const graph = smartCascadeGraphForTail(node);
    const loop = resolveSmartCascadeLoop(node.id);
    if(loop && isDirectLoopTargetRun(loop, node, graph)) return true;
    if(hasDownstreamImageNode(node)) return false;
    if(graph.edges.length) return true;
    return Boolean(loop);
}

function isDirectLoopTargetRun(loop, tail, graph){
    if(!loop?.node?.id || !tail?.id) return false;
    if(graph?.root?.id !== tail.id) return false;
    if(hasDownstreamWorkflowImageNode(tail)) return false;
    return downstreamImageTargetsFor(loop.node).some(node => node.id === tail.id);
}

function coolNodeRunningState(node, ms=2000){
    if(!node) return 0;
    const token = ++smartRunStateToken;
    smartNodeRunTokens.set(node.id, token);
    node.running = true;
    setTimeout(() => {
        if(smartNodeRunTokens.get(node.id) !== token) return;
        smartNodeRunTokens.delete(node.id);
        const current = canvasState.nodes.find(n => n.id === node.id);
        if(current){
            current.running = false;
            render();
        }
    }, ms);
    return token;
}

function clearNodeRunningState(node){
    if(!node) return;
    smartNodeRunTokens.delete(node.id);
    node.running = false;
}

function pushRightSideNodes(sourceNode, delta){
    const shift = Math.ceil(Number(delta) || 0);
    if(!sourceNode || shift <= 0) return;
    const sourceRight = (Number(sourceNode.x) || 0) + nodeRect(sourceNode).width - shift;
    const downstreamIds = new Set(downstreamNodesForId(sourceNode.id).map(n => n.id));
    canvasState.nodes.forEach(n => {
        if(!n || n.id === sourceNode.id) return;
        const r = nodeRect(n);
        const shouldShift = downstreamIds.has(n.id) || (Number(r.x) > sourceRight && Math.abs((Number(r.y) || 0) - (Number(sourceNode.y) || 0)) < 520);
        if(shouldShift) n.x = (Number(n.x) || 0) + shift;
    });
}

function cascadeOutputTitle(kind='image', count=1){
    if(Number(count) > 1) return kind === 'video' ? 'Videos' : kind === 'audio' ? 'Audios' : kind === 'text' ? 'Texts' : 'Group';
    return kind === 'video' ? 'Video' : kind === 'audio' ? 'Audio' : kind === 'text' ? 'Text' : kind === 'file' ? 'File' : 'Image';
}

function nonPreviewOutputImages(images=[]){
    return (images || []).filter(img => img?.url && !img.loopInputPreview);
}

function candidatePoolPosition(node, estW, estH){
    const r = nodeRect(node);
    const x = Math.round((Number(node.x) || 0) + r.width + 80);
    let y = Math.round(Number(node.y) || 0);
    const overlapsAny = (px, py) => canvasState.nodes.some(n => {
        if(!n || n.id === node.id) return false;
        if(n.isHistoryGroup && n.historyFor === node.id) return false;
        const nr = nodeRect(n);
        return px < nr.x + nr.width && px + estW > nr.x && py < nr.y + nr.height && py + estH > nr.y;
    });
    for(let i = 0; i < 8; i++){
        if(!overlapsAny(x, y)) break;
        const blocker = canvasState.nodes.find(n => {
            if(!n || n.id === node.id) return false;
            if(n.isHistoryGroup && n.historyFor === node.id) return false;
            const nr = nodeRect(n);
            return y < nr.y + nr.height && y + estH > nr.y && x < nr.x + nr.width && x + estW > nr.x;
        });
        if(!blocker) break;
        const br = nodeRect(blocker);
        y = Math.round((Number(blocker.y) || 0) + br.height + 24);
    }
    if(!overlapsAny(x, y)) return {x, y};
    return {x:Math.round((Number(node.x) || 0) + Math.max(0, (r.width - estW) / 2)), y:Math.round((Number(node.y) || 0) + r.height + 56)};
}

function appendOutputsToNode(node, additions, kind='image', options={}){
    if(!node || !additions?.length) return [];
    node = liveSmartNode(node);
    const beforeRight = (Number(node.x) || 0) + nodeRect(node).width;
    const existing = cleanHistoryImages(node.images || []);
    const seen = new Set(existing.map(img => `${img.kind || ''}|${img.url || ''}`));
    const next = cleanHistoryImages(additions).filter(img => {
        const key = `${img.kind || ''}|${img.url || ''}`;
        if(seen.has(key)) return false;
        seen.add(key);
        return true;
    });
    if(!next.length) return [];
    node.images = [...existing, ...next];
    markSmartNodeComplete(node, undefined, true);
    node.outputKind = kind;
    node.title = node.images.length > 1 ? (kind === 'video' ? 'Videos' : kind === 'audio' ? 'Audios' : kind === 'text' ? 'Texts' : 'Group') : (kind === 'video' ? 'Video' : kind === 'audio' ? 'Audio' : kind === 'text' ? 'Text' : kind === 'file' ? 'File' : 'Image');
    delete node.w;
    delete node.h;
    const afterRight = (Number(node.x) || 0) + nodeRect(node).width;
    const skipShift = options.skipShift || Boolean(smartLoopContext?.nodeId);
    if(!skipShift) pushRightSideNodes(node, afterRight - beforeRight + 36);
    return next;
}

function appendLoopOutputsToNode(node, additions, kind='image', ctx=smartLoopContext){
    if(!node || !additions?.length) return [];
    const runState = ctx?.runState;
    if(runState && !runState.loopAppendInitialized) runState.loopAppendInitialized = new Set();
    const initialized = runState?.loopAppendInitialized;
    if(initialized && !initialized.has(node.id)){
        initialized.add(node.id);
        const existing = cleanHistoryImages(node.images || []);
        if(existing.length){
            const history = ensureHistoryGroupForNode(node);
            history.images = cleanHistoryImages([...existing, ...(history.images || [])]);
            history.title = tr('smart.historyGroupTitle');
            history.outputKind = kind;
            history.scale = MEDIA_GROUP_DEFAULT_SCALE;
            delete history.w;
            delete history.h;
        }
        node.images = [];
    }
    return appendOutputsToNode(node, additions, kind, {skipShift:true});
}

function promptDraftHtmlForNode(node){
    const draftHtml = String(node?.promptDraftHtml || '');
    if(draftHtml){
        if(draftHtml.includes('mention-image-token')) return draftHtml;
        return promptHtmlWithMentionTokens(node.promptDraftText || node.runPrompt || '', node.runPromptRefs || []) || draftHtml;
    }
    const prompt = node?.promptDraftText || node?.runPrompt || '';
    return promptHtmlWithMentionTokens(prompt, node?.runPromptRefs || []) || escapeHtml(prompt);
}

function buildPromptRequestForNode(node, defaultImages, ctx=smartLoopContext){
    // Button-state checks run on every keystroke. Build against a detached root
    // so IME composition, selection, and the caret in the live editor are never
    // interrupted by a temporary draft load/restore cycle.
    const promptRoot = document.createElement('div');
    promptRoot.innerHTML = promptDraftHtmlForNode(node);
    return buildPromptRequest(node, defaultImages, false, ctx, promptRoot);
}

async function generateUrlsForCurrentSettings(node, prompt, refs, runSettings=settings){
    const activeSettings = runSettings || settings;
    if(activeSettings.engine === 'runninghub' && runningHubSelectedModel(activeSettings)){
        const taskResult = await runApiGeneration(prompt, refs, runningHubModelApiSettings(activeSettings), node);
        const taskIds = Array.isArray(taskResult?.taskIds) ? taskResult.taskIds : [];
        if(taskIds.length){
            const settled = await pollGenerationTaskResultForNode(node, taskResult);
            const urls = settled.flatMap(result => resultMediaUrls(result?.image_items?.length ? result.image_items : (result?.images?.length ? result.images : result))).filter(Boolean);
            return {urls, kind:mediaKindForUrls(urls, 'image')};
        }
        const urls = resultMediaUrls(taskResult);
        return {urls, kind:mediaKindForUrls(urls, 'image')};
    }
    if(isApiLikeEngine(activeSettings.engine) && activeSettings.apiKind === 'video'){
        const taskResult = await runApiVideoGeneration(prompt, refs, activeSettings, node);
        if(Array.isArray(taskResult?.taskIds) && taskResult.taskIds.length){
            const settled = await pollGenerationTaskResultForNode(node, taskResult, 'video');
            const appliedUrls = resultMediaUrls(node?.images || []);
            const urls = appliedUrls.length
                ? appliedUrls
                : settled.flatMap(result => {
                    const items = result?.video_items?.length ? result.video_items : (result?.videos?.length ? result.videos : result);
                    return resultMediaUrls(items);
                }).filter(Boolean);
            return {urls, kind:'video'};
        }
        return {urls:resultMediaUrls(taskResult), kind:'video'};
    }
    if(isApiLikeEngine(activeSettings.engine)){
        const taskResult = await runApiGeneration(prompt, refs, activeSettings, node);
        const taskIds = Array.isArray(taskResult?.taskIds) ? taskResult.taskIds : [];
        if(taskIds.length){
            const settled = await pollGenerationTaskResultForNode(node, taskResult);
            const urls = settled.flatMap(result => resultMediaUrls(result?.image_items?.length ? result.image_items : (result?.images?.length ? result.images : result))).filter(Boolean);
            return {urls, kind:mediaKindForUrls(urls, 'image')};
        }
        const urls = resultMediaUrls(taskResult);
        return {urls, kind:mediaKindForUrls(urls, 'image')};
    }
    if(activeSettings.engine === 'modelscope'){
        const routed = {
            ...activeSettings,
            engine:'api',
            apiKind:'image',
            routingMode:'manual',
            provider_id:'modelscope',
            model:activeSettings.msCustomModel || modelscopeImageModels()[0] || ''
        };
        const taskResult = await runApiGeneration(prompt, refs, routed, node);
        const taskIds = Array.isArray(taskResult?.taskIds) ? taskResult.taskIds : [];
        if(taskIds.length){
            const settled = await pollGenerationTaskResultForNode(node, taskResult);
            const urls = settled.flatMap(result => resultMediaUrls(result?.image_items?.length ? result.image_items : (result?.images?.length ? result.images : result))).filter(Boolean);
            return {urls, kind:mediaKindForUrls(urls, 'image')};
        }
        const urls = resultMediaUrls(taskResult);
        return {urls, kind:mediaKindForUrls(urls, 'image')};
    }
    return {urls:[], kind:'image'};
}

async function runCascadeStepIntoNode(sourceNode, targetNode, inputRefs, ctx=smartLoopContext){
    const outputNode = targetNode || sourceNode;
    if(!sourceNode || !targetNode || !outputNode) return [];
    const requestNode = sourceNode?.type === 'smart-loop' ? targetNode : sourceNode;
    const previousSettings = cloneSmartSettings(settings);
    const runSettings = smartLoopRoundSettings({...cloneSmartSettings(settings), ...cloneSmartSettings(smartSettingsForNode(requestNode) || {})}, ctx);
    settings = runSettings;
    const outpaintSize = validOutpaintSize(requestNode);
    const selfRefs = sourceNode?.type === 'smart-loop' ? [] : selfReferenceImagesForNode(sourceNode, false, ctx).filter(img => img?.url);
    const sourceRefs = (selfRefs.length ? selfRefs : defaultReferenceImagesFor(requestNode, false, ctx)).filter(img => img?.url);
    const refsForRequest = sourceRefs.length
        ? sourceRefs
        : (inputRefs && inputRefs.length ? inputRefs : null);
    const request = buildPromptRequestForNode(
        requestNode,
        refsForRequest,
        ctx
    );
    const prompt = (request.prompt || '').trim();
    const displayPrompt = (request.displayPrompt || '').trim();
    if((!prompt || !displayPrompt) && smartRunNeedsPrompt(runSettings)){
        settings = previousSettings;
        throw new Error(tr('smart.chainNodeMissingPrompt'));
    }
    const meta = {
        prompt,
        displayPrompt:request.displayPrompt || '',
        promptRefs:(request.refs || []).map(ref => ({asset_id:ref.asset_id || '', url:ref.url || '', name:ref.name || '', nodeId:ref.nodeId || '', imageIndex:ref.imageIndex ?? ''})).filter(ref => ref.url),
        inputRefs:(request.refs || []).map(ref => ({asset_id:ref.asset_id || '', url:ref.url || '', name:ref.name || '', nodeId:ref.nodeId || '', imageIndex:ref.imageIndex ?? '', kind:ref.kind || ''})).filter(ref => ref.url),
        sourceNodeId:sourceNode.id,
        settings:JSON.parse(JSON.stringify(runSettings)),
        createdAt:Date.now()
    };
    if(requestNode.promptDraftHtml != null){
        meta.promptHtml = requestNode.promptDraftHtml;
        meta.promptText = requestNode.promptDraftText || request.displayPrompt || '';
    }
    const logKind = isApiLikeEngine(runSettings.engine) && runSettings.apiKind === 'video' ? 'video' : 'image';
    const runLog = smartRunSnapshot(requestNode, prompt, request.refs || [], logKind);
    const runLogStart = nowMs();
    const targetPromptState = {
        promptDraftHtml:targetNode.promptDraftHtml,
        promptDraftText:targetNode.promptDraftText,
        runPrompt:targetNode.runPrompt,
        runModelPrompt:targetNode.runModelPrompt,
        runPromptRefs:targetNode.runPromptRefs ? targetNode.runPromptRefs.map(ref => ({...ref})) : undefined,
        runInputRefs:targetNode.runInputRefs ? targetNode.runInputRefs.map(ref => ({...ref})) : undefined,
        runSettings:targetNode.runSettings ? cloneSmartSettings(targetNode.runSettings) : undefined,
        sourceNodeId:targetNode.sourceNodeId,
        runAt:targetNode.runAt
    };
    outputNode.running = true;
    outputNode.runStartedAt = nowMs();
    delete outputNode.runFinishedAt;
    delete outputNode.runElapsedMs;
    outputNode.runTimerHidden = false;
    rememberRecentSmartSettings(runSettings, requestNode);
    render();
    settings = previousSettings;
    try {
        const result = await generateUrlsForCurrentSettings(outputNode, prompt, request.refs || [], runSettings);
        if(!result.urls?.length) throw new Error(result.kind === 'video' ? tr('smart.errNoOutVideos') : tr('smart.errNoOutImages'));
        if(outpaintSize) delete requestNode.outpaintSize;
        if(!outputNode._serverGenerationHistoryRecorded) addSmartGenerationHistory({run:{...runLog, kind:result.kind || logKind}, outputs:result.urls, runMs:nowMs() - runLogStart});
        delete outputNode._serverGenerationHistoryRecorded;
        const ext = result.kind === 'video' ? 'mp4' : result.kind === 'audio' ? 'mp3' : result.kind === 'text' ? 'txt' : 'png';
        const additions = result.urls.map((item, i) => {
            const url = typeof item === 'string' ? item : item?.url || '';
            return stripImageGenerationMeta(copyMediaSizeFields(item, {url, name:(typeof item === 'object' && item.name) || `output-${i + 1}.${ext}`, kind:(typeof item === 'object' && item.kind) || result.kind, generatedResult:true, ...(typeof item === 'object' && item.mime_type ? {mime_type:item.mime_type} : {})}));
        }).filter(item => item.url);
        if(ctx?.appendLoopOutputs) {
            appendLoopOutputsToNode(outputNode, additions, result.kind, ctx);
        } else {
            replaceOutputsToNodeWithHistory(outputNode, additions, result.kind, null, {skipShift:Boolean(ctx?.nodeId)});
        }
        outputNode.runPrompt = targetPromptState.runPrompt;
        outputNode.runModelPrompt = targetPromptState.runModelPrompt;
        outputNode.runPromptRefs = targetPromptState.runPromptRefs || [];
        outputNode.runInputRefs = targetPromptState.runInputRefs || [];
        outputNode.runSettings = targetPromptState.runSettings;
        outputNode.sourceNodeId = targetPromptState.sourceNodeId;
        outputNode.runAt = targetPromptState.runAt;
        if(targetPromptState.promptDraftHtml === undefined) delete outputNode.promptDraftHtml;
        else outputNode.promptDraftHtml = targetPromptState.promptDraftHtml;
        if(targetPromptState.promptDraftText === undefined) delete outputNode.promptDraftText;
        else outputNode.promptDraftText = targetPromptState.promptDraftText;
        ['runPrompt','runModelPrompt','runSettings','sourceNodeId','runAt'].forEach(key => {
            if(targetPromptState[key] === undefined) delete outputNode[key];
        });
        settings = previousSettings;
        render();
        return rememberRoundOutputs(ctx, outputNode, additions);
    } catch(e) {
        settings = previousSettings;
        if(handleJimengPendingSignal(outputNode, e)){
            render();
            return [];
        }
        outputNode.running = false;
        if(!e?.smartGenerationLogged) addSmartGenerationHistory({run:runLog, outputs:[], runMs:nowMs() - runLogStart, error:e.message || String(e)});
        render();
        throw e;
    }
}

async function runLoopRoundIntoSlot(loopNode, rootNode, outputSlot, loopIndex, ctx){
    if(!loopNode || !rootNode || !outputSlot) return [];
    outputSlot = liveSmartNode(outputSlot);
    const previousSettings = cloneSmartSettings(settings);
    const edgeKey = `${rootNode.id}->${outputSlot.id}`;
    const runSettings = smartLoopRoundSettings({...cloneSmartSettings(settings), ...cloneSmartSettings(smartSettingsForNode(rootNode) || {})}, ctx);
    settings = runSettings;
    try {
        const refsForRequest = outputImagesForNode(loopNode, true, ctx).filter(img => img?.url);
        const request = buildPromptRequestForNode(rootNode, refsForRequest.length ? refsForRequest : null, ctx);
        const prompt = (request.prompt || '').trim();
        const displayPrompt = (request.displayPrompt || '').trim();
        if((!prompt || !displayPrompt) && smartRunNeedsPrompt(runSettings)) throw new Error(tr('smart.chainNodeMissingPrompt'));
        const meta = {
            prompt,
            displayPrompt:request.displayPrompt || '',
            promptRefs:(request.refs || []).map(ref => ({asset_id:ref.asset_id || '', url:ref.url || '', name:ref.name || '', nodeId:ref.nodeId || '', imageIndex:ref.imageIndex ?? ''})).filter(ref => ref.url),
            inputRefs:(request.refs || []).map(ref => ({asset_id:ref.asset_id || '', url:ref.url || '', name:ref.name || '', nodeId:ref.nodeId || '', imageIndex:ref.imageIndex ?? '', kind:ref.kind || ''})).filter(ref => ref.url),
            sourceNodeId:rootNode.id,
            settings:JSON.parse(JSON.stringify(runSettings)),
            createdAt:Date.now()
        };
        const logKind = isApiLikeEngine(runSettings.engine) && runSettings.apiKind === 'video' ? 'video' : 'image';
        const runLog = smartRunSnapshot(rootNode, prompt, request.refs || [], logKind);
        const runLogStart = nowMs();
        const expectedCount = isApiLikeEngine(runSettings.engine) && runSettings.apiKind !== 'video'
            ? Math.max(1, Math.min(8, Number(runSettings.count || 1)))
            : 1;
        outputSlot.queued = false;
        outputSlot.running = true;
        outputSlot.pending = expectedCount;
        outputSlot.runStartedAt = nowMs();
        delete outputSlot.runFinishedAt;
        delete outputSlot.runElapsedMs;
        outputSlot.runTimerHidden = false;
        const runPath = smartCascadePathForCtx(ctx);
        if(runPath?.states) {
            runPath.states[edgeKey] = 'active';
            scheduleConnectionLayerRefresh();
        }
        render();
        settings = previousSettings;
        let result;
        if(isApiLikeEngine(runSettings.engine) && runSettings.apiKind !== 'video'){
            const existing = cleanHistoryImages(outputSlot.images || []);
            let archiveRollback = null;
            if(existing.length){
                const previousHistory = historyGroupForNode(outputSlot);
                const previousHistorySnapshot = previousHistory ? JSON.parse(JSON.stringify(previousHistory)) : null;
                const history = ensureHistoryGroupForNode(outputSlot);
                archiveRollback = {
                    outputNodeId:outputSlot.id,
                    outputImages:JSON.parse(JSON.stringify(outputSlot.images || [])),
                    historyNodeId:history.id,
                    previousHistory:previousHistorySnapshot
                };
                history.images = cleanHistoryImages([...existing, ...(history.images || [])]);
                history.title = tr('smart.historyGroupTitle');
                history.outputKind = 'image';
                history.scale = MEDIA_GROUP_DEFAULT_SCALE;
                delete history.w;
                delete history.h;
                outputSlot.images = [];
                archiveRollback.archivedOutputImages = JSON.parse(JSON.stringify(outputSlot.images));
                archiveRollback.archivedHistory = JSON.parse(JSON.stringify(history));
                archiveRollback.archivedHistoryConnections = JSON.parse(JSON.stringify(
                    (canvas.connections || []).filter(connection =>
                        connection.from === history.id || connection.to === history.id
                    )
                ));
            }
            let taskResult;
            try {
                taskResult = await runApiGeneration(prompt, request.refs || [], runSettings, outputSlot);
                if(!Array.isArray(taskResult?.taskIds) || !taskResult.taskIds.length) throw new Error(tr('smart.errRunFailed'));
            } catch(submitError) {
                const restoreArchive = (conditional=false) => {
                    if(!archiveRollback) return;
                    const liveOutput = canvasState.nodes.find(item => item.id === archiveRollback.outputNodeId);
                    if(liveOutput && (
                        !conditional
                        || JSON.stringify(liveOutput.images || []) === JSON.stringify(archiveRollback.archivedOutputImages || [])
                    )) liveOutput.images = JSON.parse(JSON.stringify(archiveRollback.outputImages));
                    const historyIndex = canvasState.nodes.findIndex(item => item.id === archiveRollback.historyNodeId);
                    if(archiveRollback.previousHistory){
                        if(historyIndex >= 0 && conditional){
                            const currentHistory = canvasState.nodes[historyIndex];
                            if(JSON.stringify(currentHistory.images || []) === JSON.stringify(archiveRollback.archivedHistory?.images || [])){
                                currentHistory.images = JSON.parse(JSON.stringify(archiveRollback.previousHistory.images || []));
                            }
                        } else {
                            const restoredHistory = JSON.parse(JSON.stringify(archiveRollback.previousHistory));
                            if(historyIndex >= 0) canvasState.nodes[historyIndex] = restoredHistory;
                            else canvasState.nodes.push(restoredHistory);
                        }
                    } else {
                        const exactArchivedHistory = historyIndex >= 0
                            && JSON.stringify(canvasState.nodes[historyIndex]) === JSON.stringify(archiveRollback.archivedHistory);
                        const currentHistoryConnections = (canvas.connections || []).filter(connection =>
                            connection.from === archiveRollback.historyNodeId || connection.to === archiveRollback.historyNodeId
                        );
                        const connectionSnapshot = connections => (connections || [])
                            .map(connection => JSON.stringify(connection)).sort();
                        const exactArchivedConnections = JSON.stringify(connectionSnapshot(currentHistoryConnections))
                            === JSON.stringify(connectionSnapshot(archiveRollback.archivedHistoryConnections));
                        if(historyIndex >= 0 && (!conditional || (exactArchivedHistory && exactArchivedConnections))){
                            canvasState.nodes.splice(historyIndex, 1);
                            canvas.connections = (canvas.connections || []).filter(connection =>
                                connection.from !== archiveRollback.historyNodeId && connection.to !== archiveRollback.historyNodeId
                            );
                        } else if(historyIndex >= 0 && conditional){
                            const currentHistory = canvasState.nodes[historyIndex];
                            if(JSON.stringify(currentHistory.images || []) === JSON.stringify(archiveRollback.archivedHistory?.images || [])){
                                currentHistory.images = [];
                            }
                        }
                    }
                };
                restoreArchive();
                render();
                if(canvas?.format === 'firefly-canvas-v1'){
                    const restored = await saveCanvas();
                    if(restored === false){
                        restoreArchive(true);
                        render();
                        await saveCanvas();
                    }
                } else {
                    scheduleSave();
                }
                throw submitError;
            }
            const taskIds = Array.isArray(taskResult?.taskIds) ? taskResult.taskIds : [];
            outputSlot.pendingTasks = taskIds.map(taskId => ({taskId, kind:'image', count:taskResult.count || 1, providerId:taskResult.providerId, model:taskResult.model}));
            ensureSmartPendingHistoryLogs(outputSlot, outputSlot.pendingTasks, {prompt, kind:'image'});
            outputSlot.pending = Math.max(taskIds.length, Number(outputSlot.pending || 0) || taskIds.length);
            outputSlot.running = false;
            render();
            if(!await acceptProjectGenerationRegistration(taskResult)){
                scheduleSave();
                await saveCanvas();
            }
            await resumeSmartPendingNode(outputSlot, {run:runLog, runLogStart});
            if(outputSlot.jimengPending || smartRecoverableImageTask(outputSlot)){
                outputSlot.queued = false;
                return [];
            }
            result = {urls:(outputSlot.images || []).map(img => img?.url ? img : null).filter(Boolean), kind:'image'};
        } else {
            result = await generateUrlsForCurrentSettings(outputSlot, prompt, request.refs || [], runSettings);
        }
        if(!result.urls?.length) throw new Error(result.kind === 'video' ? tr('smart.errNoOutVideos') : tr('smart.errNoOutImages'));
        let additions;
        if(isApiLikeEngine(runSettings.engine) && runSettings.apiKind !== 'video'){
            additions = (outputSlot.images || []).map(img => stripImageGenerationMeta({...img})).filter(img => img?.url);
            if(meta) attachRunMeta(outputSlot, meta);
        } else {
            const ext = result.kind === 'video' ? 'mp4' : result.kind === 'audio' ? 'mp3' : result.kind === 'text' ? 'txt' : 'png';
            additions = result.urls.map((item, i) => {
                const url = typeof item === 'string' ? item : item?.url || '';
                return stripImageGenerationMeta(copyMediaSizeFields(item, {url, name:(typeof item === 'object' && item.name) || `output-${i + 1}.${ext}`, kind:(typeof item === 'object' && item.kind) || result.kind, generatedResult:true, ...(typeof item === 'object' && item.mime_type ? {mime_type:item.mime_type} : {})}));
            }).filter(item => item.url);
            outputSlot = liveSmartNode(outputSlot);
            outputSlot.images = nonPreviewOutputImages(outputSlot.images);
            replaceOutputsToNodeWithHistory(outputSlot, additions, result.kind, meta, {skipShift:Boolean(ctx?.nodeId)});
        }
        outputSlot = liveSmartNode(outputSlot);
        markSmartNodeComplete(outputSlot, meta, true);
        clearSourceBusyStateIfDownstreamDone(rootNode);
        if(runPath?.states) {
            runPath.states[edgeKey] = 'done';
            scheduleConnectionLayerRefresh();
        }
        if(!outputSlot._serverGenerationHistoryRecorded) addSmartGenerationHistory({run:{...runLog, kind:result.kind || logKind}, outputs:result.urls, runMs:nowMs() - runLogStart});
        delete outputSlot._serverGenerationHistoryRecorded;
        return rememberRoundOutputs(ctx, outputSlot, additions);
    } catch(e) {
        if(handleJimengPendingSignal(outputSlot, e)){
            outputSlot.queued = false;
            return [];
        }
        outputSlot.queued = false;
        outputSlot.pending = 0;
        outputSlot.running = false;
        throw e;
    } finally {
        settings = previousSettings;
    }
}

function appendCascadeRefsToReceiver(node, refs, ctx=smartLoopContext){
    if(!node || !refs?.length) return [];
    const additions = refs
        .filter(ref => ref?.url)
        .map((ref, i) => stripImageGenerationMeta({
            asset_id:ref.asset_id || '',
            url:ref.url,
            name:ref.name || `output-${i + 1}.png`,
            kind:ref.kind || (isVideoMediaItem(ref) ? 'video' : 'image')
        }));
    if(!additions.length) return [];
    replaceOutputsToNodeWithHistory(node, additions, mediaKindForUrls(additions, additions.some(isVideoMediaItem) ? 'video' : 'image'), null, {skipShift:Boolean(ctx?.nodeId)});
    render();
    return rememberRoundOutputs(ctx, node, additions);
}

function cascadeRefsFromOutputs(outputs, targetNode){
    return (outputs || []).filter(img => img?.url).map((img, index) => ({
        asset_id:img.asset_id || '',
        url:img.url,
        name:img.name || `图${index + 1}`,
        kind:img.kind || 'image',
        role:`image_${index + 1}`,
        nodeId:targetNode?.id || '',
        imageIndex:targetNode ? (targetNode.images || []).length - outputs.length + index : index
    }));
}

function smartCascadeStopText(stopping=false){
    return stopping ? tr('smart.stopping') : tr('smart.stopRun');
}

function smartCascadeAbortError(){
    const err = new Error(tr('smart.oneClickStopped'));
    err.smartCascadeStopped = true;
    return err;
}

function throwIfSmartCascadeStopRequested(runState=null){
    if(runState?.stopRequested) throw smartCascadeAbortError();
}

function requestSmartCascadeStop(loopId=''){
    const runState = loopId ? smartCascadeRunForLoop(loopId) : ([...smartCascadeRuns.values()].pop() || null);
    if(!runState) return;
    if(runState.stopRequested) return;
    runState.stopRequested = true;
    toast(tr('smart.stopRequested'));
    render();
}

function smartCascadeParallelLimit(chain=[]){
    return 6;
}

async function runSmartCascadeRoundsWithLimit(roundIndexes, limit, runner, runState=null){
    let next = 0;
    const workerCount = Math.max(1, Math.min(Number(limit) || 1, roundIndexes.length));
    const workers = Array.from({length:workerCount}, async () => {
        while(next < roundIndexes.length){
            if(runState?.stopRequested) break;
            const roundOffset = next++;
            const current = roundIndexes[roundOffset];
            try {
                await runner(current, roundOffset);
            } catch(e) {
                if(e?.smartCascadeStopped) break;
                throw e;
            }
        }
    });
    await Promise.all(workers);
}

async function runSmartCascade(targetNode=null){
    const tail = targetNode || selectedNode();
    if(!canRunSmartCascade(tail)){ toast(tr('smart.cascadeTailRequired')); return; }
    savePromptDraftForCurrent();
    const graph = smartCascadeGraphForTail(tail);
    const chain = graph.path;
    const loop = resolveSmartCascadeLoop(tail.id);
    const loopId = loop?.node?.id || '';
    if(loopId && smartCascadeIsLoopRunning(loopId)){ requestSmartCascadeStop(loopId); return; }
    if(!loopId && smartCascadeAnyRunning()){ requestSmartCascadeStop(); return; }
    const directLoopTargetRun = Boolean(loop && isDirectLoopTargetRun(loop, tail, graph));
    const singleNodeLoopRun = Boolean(loop && (chain.length === 1 || directLoopTargetRun));
    if(!graph.edges.length && !singleNodeLoopRun){ toast(tr('smart.loopNoChain')); return; }
    const originalSelected = canvasState.selectedId;
    const originalSettings = cloneSmartSettings(settings);
    const originalPromptHtml = promptInput.innerHTML;
    const runKey = loopId || `cascade-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const runState = {runKey, loopId, stopRequested:false, runPath:null};
    smartCascadeRuns.set(runKey, runState);
    smartCascadeSilentSelection = true;
    runBtn.disabled = true;
    pushUndo();
    const totalRounds = loop?.count || 1;
    const startIndex = Math.max(1, Number(loop?.node?.loopStart) || 1);
    const batchSize = loop?.node?.imageInput ? Math.max(1, Math.min(100, Number(loop.node.imageBatchSize) || 1)) : 1;
    const endIndex = startIndex + (totalRounds - 1) * batchSize;
    const loopMode = loop?.mode === 'parallel' ? 'parallel' : 'serial';
    const parallelLimit = loopMode === 'parallel' && totalRounds > 1 ? smartCascadeParallelLimit(chain) : 1;
    const precreateSingleSlots = singleNodeLoopRun && loopMode === 'parallel' && totalRounds > 1 && parallelLimit > 1;
    let singleLoopSlots = [];
    if(singleNodeLoopRun){
        runState.runPath = {states:{}};
    }
    if(singleNodeLoopRun){
        singleLoopSlots = Array.from({length:totalRounds}, (_, round) => {
            const loopIndex = startIndex + round * batchSize;
            const slot = loopOutputSlotForRound(tail, loop.node, loopIndex, round);
            return slot ? tagLoopOutputSlot(slot, tail, loop.node, loopIndex, round) : null;
        });
        singleLoopSlots.filter(Boolean).forEach(slot => { runState.runPath.states[`${tail.id}->${slot.id}`] = 'wait'; });
        if(precreateSingleSlots){
            for(let slotOffset = 0; slotOffset < totalRounds; slotOffset++){
                if(singleLoopSlots[slotOffset]) continue;
                const loopIndex = startIndex + slotOffset * batchSize;
                singleLoopSlots[slotOffset] = createLoopOutputSlot(tail, loopIndex, slotOffset, {queued:true, loopNode:loop.node, slotIndex:slotOffset, runState});
            }
        }
        render();
    }
    if(!singleNodeLoopRun){
        const runStates = {};
        if(loop?.node?.id && graph.root?.id) runStates[`${loop.node.id}->${graph.root.id}`] = 'wait';
        graph.edges.forEach(edge => { runStates[edge.key] = 'wait'; });
        runState.runPath = {states:runStates};
        scheduleConnectionLayerRefresh();
        updateComposer();
    }
    try {
        const runRound = async (loopIndex=startIndex, options={}) => {
            throwIfSmartCascadeStopRequested(runState);
            const ctx = loop
                ? {index:loopIndex, total:endIndex, nodeId:loop.node.id, forceWorkflow:chain.length > 1 && !singleNodeLoopRun, runState, roundOutputs:new Map()}
                : {runState, roundOutputs:new Map()};
            if(parallelLimit === 1) smartLoopContext = ctx;
            if(singleNodeLoopRun){
                const refs = refsForDirectLoopRound(loop.node, loopIndex, endIndex);
                if(directLoopTargetRun && parallelLimit === 1) showDirectLoopRoundPreview(loop.node, tail, refs, loopIndex, endIndex);
                const slotIndex = Math.max(0, Math.floor((loopIndex - startIndex) / batchSize));
                const outputTarget = tagLoopOutputSlot(
                    options.outputTarget || singleLoopSlots[slotIndex] || loopOutputSlotForRound(tail, loop.node, loopIndex, slotIndex) || createLoopOutputSlot(tail, loopIndex, slotIndex, {loopNode:loop.node, slotIndex, runState}),
                    tail,
                    loop.node,
                    loopIndex,
                    slotIndex
                );
                singleLoopSlots[slotIndex] = outputTarget;
                await runLoopRoundIntoSlot(loop.node, tail, outputTarget, loopIndex, ctx);
                return;
            }
            const producedRefs = new Map();
            const runBranch = async (source, incomingRefs=[]) => {
                throwIfSmartCascadeStopRequested(runState);
                let targets = graph.children.get(source.id) || [];
                const loopPrompts = isSmartImageNode(source) ? upstreamLoopPromptNodesFor(source) : [];
                const sourceLoopPrompts = isSmartImageNode(source) ? relayLoopPromptNodesForTarget(source) : [];
                if(runState.runPath && sourceLoopPrompts.length && source?.id){
                    sourceLoopPrompts.forEach(loopNode => {
                        runState.runPath.states[`${loopNode.id}->${source.id}`] = 'done';
                    });
                    scheduleConnectionLayerRefresh();
                }
                if(loopPrompts.length && targets.length > 1){
                    const firstLoop = loopPrompts[0];
                    const startBase = Math.max(1, Number(firstLoop.loopStart) || 1);
                    const currentIndex = Math.max(1, Number(ctx?.index || startBase) || startBase);
                    const selectedTarget = targets[(currentIndex - 1) % targets.length];
                    if(runState.runPath && firstLoop?.id && source?.id){
                        runState.runPath.states[`${firstLoop.id}->${source.id}`] = 'done';
                        scheduleConnectionLayerRefresh();
                    }
                    targets = [selectedTarget].filter(Boolean);
                }
                let sharedRefs = incomingRefs;
                for(let index = 0; index < targets.length; index++){
                    throwIfSmartCascadeStopRequested(runState);
                    const target = targets[index];
                    const edgeKey = `${source.id}->${target.id}`;
                    let outputs = [];
                    const targetChildren = (graph.children.get(target.id) || []).filter(child => child && child.type !== 'smart-loop');
                    const targetIsLeaf = target.type !== 'smart-loop' && targetChildren.length === 0;
                    const relayLoops = isSmartImageNode(source) && isSmartImageNode(target)
                        ? relayLoopPromptNodesForEdge(source, target)
                        : [];
                    const stepCtx = relayLoops.length && isSmartImageNode(target)
                        ? {...(ctx || {}), appendLoopOutputs:Boolean(ctx?.nodeId && targetIsLeaf), relayPromptNodeIds:[...new Set([...(ctx?.relayPromptNodeIds || []), ...relayLoops.map(n => n.id)])]}
                        : {...(ctx || {}), appendLoopOutputs:Boolean(ctx?.nodeId && targetIsLeaf)};
                    try {
                        if(runState.runPath && relayLoops.length && source?.id && isSmartImageNode(target)){
                            relayLoops.forEach(loopNode => {
                                runState.runPath.states[`${loopNode.id}->${source.id}`] = 'done';
                            });
                            scheduleConnectionLayerRefresh();
                        }
                        if(runState.runPath){
                            runState.runPath.states[edgeKey] = 'active';
                            scheduleConnectionLayerRefresh();
                        }
                        if(target.type === 'smart-loop'){
                            outputs = outputImagesForNode(source, true, ctx).filter(img => img?.url);
                            sharedRefs = cascadeRefsFromOutputs(outputs, source);
                        } else if(index === 0){
                            outputs = await runCascadeStepIntoNode(source, target, incomingRefs, stepCtx);
                            sharedRefs = cascadeRefsFromOutputs(outputs, target);
                        } else {
                            outputs = appendCascadeRefsToReceiver(target, sharedRefs, stepCtx);
                        }
                    } catch(err) {
                        if(/缺少提示词|需要输入文本|need prompt/i.test(err.message || '') && incomingRefs.length){
                            outputs = appendCascadeRefsToReceiver(target, incomingRefs, stepCtx);
                            if(index === 0){
                                sharedRefs = cascadeRefsFromOutputs(outputs, target);
                            }
                        } else {
                            throw err;
                        }
                    }
                    if(runState.runPath){
                        runState.runPath.states[edgeKey] = 'done';
                        scheduleConnectionLayerRefresh();
                    }
                    const refs = target.type === 'smart-loop' ? sharedRefs : (index === 0 ? sharedRefs : cascadeRefsFromOutputs(outputs, target));
                    producedRefs.set(target.id, refs);
                    throwIfSmartCascadeStopRequested(runState);
                    await runBranch(target, refs);
                }
            };
            const rootRefs = defaultReferenceImagesFor(graph.root, true, ctx).filter(img => img?.url);
            producedRefs.set(graph.root.id, rootRefs);
            await runBranch(graph.root, rootRefs);
        };
        const roundIndexes = Array.from({length:totalRounds}, (_, round) => startIndex + round * batchSize);
        if(loopMode === 'parallel' && totalRounds > 1){
            const parallelTargets = singleNodeLoopRun
                ? singleLoopSlots
                : [];
            if(parallelTargets.length) render();
            await runSmartCascadeRoundsWithLimit(roundIndexes, parallelLimit, (loopIndex, roundOffset) => {
                const outputTarget = parallelTargets[roundOffset] || null;
                return runRound(loopIndex, {outputTarget});
            }, runState);
        } else {
            for(const loopIndex of roundIndexes){
                throwIfSmartCascadeStopRequested(runState);
                await runRound(loopIndex);
            }
        }
        throwIfSmartCascadeStopRequested(runState);
        if(parallelLimit === 1) smartLoopContext = null;
        canvasState.selectedId = '';
        canvasState.selectedIds = [];
        canvasState.selectedImage = {nodeId:'', index:-1};
        canvasState.activeComposerSubject = null;
        canvasState.lastComposerNodeId = '';
        composer.classList.remove('open');
        settings = originalSettings;
        promptInput.innerHTML = originalPromptHtml;
        scheduleSave();
        toast(totalRounds > 1
            ? trf(loopMode === 'parallel' ? 'smart.loopParallelRoundsDone' : 'smart.loopRunRoundsDone', {n:totalRounds})
            : tr('smart.loopRunDone'));
    } catch(e) {
        if(parallelLimit === 1) smartLoopContext = null;
        canvasState.selectedId = originalSelected;
        settings = originalSettings;
        promptInput.innerHTML = originalPromptHtml;
        toast(e?.smartCascadeStopped ? tr('smart.oneClickStopped') : (e.message || tr('smart.errRunFailed')).slice(0, 160));
    } finally {
        smartCascadeRuns.delete(runKey);
        smartCascadeSilentSelection = false;
        syncRunButtonState();
        if(directLoopTargetRun) finishLoopTargetPreviewState(tail);
        scheduleSave();
        render();
    }
}

function runSmartCascadeFromLoop(loopId){
    const loop = canvasState.nodes.find(n => n.id === loopId && n.type === 'smart-loop');
    if(!loop){ toast(tr('smart.loopNodeNotFound')); return; }
    const tail = cascadeTailForLoop(loop.id);
    if(!tail){ toast(tr('smart.loopNeedsTail')); return; }
    canvasState.selectedId = tail.id;
    canvasState.selectedIds = [];
    canvasState.selectedImage = {nodeId:'', index:-1};
    runSmartCascade(tail);
}
