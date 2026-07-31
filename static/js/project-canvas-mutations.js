(function(root, factory){
    const api = factory();
    if(typeof module === 'object' && module.exports) module.exports = api;
    if(root) root.FireflyProjectCanvasMutations = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function(){
    'use strict';

    function clone(value){
        if(value === undefined) return undefined;
        return JSON.parse(JSON.stringify(value));
    }

    function equal(left, right){
        return JSON.stringify(left) === JSON.stringify(right);
    }

    function isObject(value){
        return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
    }

    function keyedArray(value){
        return Array.isArray(value)
            && value.every(item => isObject(item) && String(item.id || item.request_id || item.asset_id || ''));
    }

    function conversationMessageKey(item){
        const requestId = String(item?.request_id || '');
        const role = String(item?.role || '');
        return requestId && (role === 'user' || role === 'assistant')
            ? `conversation-message:${requestId}:${role}`
            : '';
    }

    function itemKey(item){
        return conversationMessageKey(item) || String(item?.id || item?.request_id || item?.asset_id || '');
    }

    function mergeKeyedArray(base, local, remote){
        const baseItems = new Map((base || []).map(item => [itemKey(item), item]));
        const localItems = new Map((local || []).map(item => [itemKey(item), item]));
        const remoteItems = new Map((remote || []).map(item => [itemKey(item), item]));
        const order = [];
        (remote || []).forEach(item => order.push(itemKey(item)));
        (local || []).forEach(item => {
            const key = itemKey(item);
            if(!order.includes(key)) order.push(key);
        });
        return order.flatMap(key => {
            const baseHas = baseItems.has(key);
            const localHas = localItems.has(key);
            const remoteHas = remoteItems.has(key);
            if(baseHas && !localHas) return [];
            if(!localHas) return remoteHas ? [clone(remoteItems.get(key))] : [];
            if(!remoteHas) return baseHas ? [] : [clone(localItems.get(key))];
            if(!baseHas && conversationMessageKey(localItems.get(key))){
                return [clone(remoteItems.get(key))];
            }
            return [mergeThreeWay(
                baseHas ? baseItems.get(key) : undefined,
                localItems.get(key),
                remoteItems.get(key),
            )];
        });
    }

    function isAgentTerminalStatus(value){
        return ['succeeded', 'failed', 'completed', 'uncertain'].includes(String(value || ''));
    }

    function isAgentTransientStatus(value){
        return ['planned', 'awaiting_confirmation', 'queued', 'running', 'confirmed'].includes(String(value || ''));
    }

    function mergeAgentTerminalState(result, local, remote){
        if(!isObject(local) || !isObject(remote)) return result;
        const remoteType = String(remote.type || '');
        const localType = String(local.type || '');
        const remoteStatus = remoteType === 'smart-agent'
            ? String(remote.agent_plan?.status || '')
            : remoteType === 'smart-image' && remote.generationState ? String(remote.generationState) : String(remote.status || '');
        const localStatus = localType === 'smart-agent'
            ? String(local.agent_plan?.status || '')
            : localType === 'smart-image' && local.generationState ? String(local.generationState) : String(local.status || '');
        if(!isAgentTerminalStatus(remoteStatus) || !isAgentTransientStatus(localStatus)) return result;
        if(remoteType === 'smart-agent' && remote.agent_plan) result.agent_plan = clone(remote.agent_plan);
        if(remoteType === 'agent-task' || localType === 'agent-task'){
            ['status', 'error', 'result', 'completed_at', 'uncertain', 'dispatch_state'].forEach(key => {
                if(Object.prototype.hasOwnProperty.call(remote, key)) result[key] = clone(remote[key]);
            });
        }
        if(remoteType === 'smart-image' && remote.generationState){
            ['images', 'pending', 'running', 'generationState', 'error', 'updated_at'].forEach(key => {
                if(Object.prototype.hasOwnProperty.call(remote, key)) result[key] = clone(remote[key]);
            });
        }
        return result;
    }

    function mergeThreeWay(base, local, remote){
        if(equal(local, base)) return clone(remote);
        if(equal(remote, base) || remote === undefined) return clone(local);
        if(Array.isArray(local)){
            if(keyedArray(local) && keyedArray(remote || []) && keyedArray(base || [])){
                return mergeKeyedArray(base || [], local, remote || []);
            }
            return clone(local);
        }
        if(!isObject(local) || !isObject(remote)) return clone(local);
        const baseObject = isObject(base) ? base : {};
        const result = clone(remote) || {};
        const keys = new Set([...Object.keys(baseObject), ...Object.keys(local)]);
        keys.forEach(key => {
            if(!Object.prototype.hasOwnProperty.call(local, key)){
                if(Object.prototype.hasOwnProperty.call(baseObject, key)) delete result[key];
                return;
            }
            result[key] = mergeThreeWay(baseObject[key], local[key], remote[key]);
        });
        return mergeAgentTerminalState(result, local, remote);
    }

    function parse(encoded, fallback){
        if(typeof encoded !== 'string') return clone(encoded ?? fallback);
        try { return JSON.parse(encoded); } catch(_) { return clone(fallback); }
    }

    function entityId(value){
        if(value?.id) return String(value.id);
        const source = value?.from ?? value?.source ?? value?.fromNode ?? value?.sourceNodeId ?? '';
        const target = value?.to ?? value?.target ?? value?.toNode ?? value?.targetNodeId ?? '';
        const sourcePort = value?.fromPort ?? value?.sourcePort ?? '';
        const targetPort = value?.toPort ?? value?.targetPort ?? '';
        return `${source}->${target}:${sourcePort}:${targetPort}`;
    }

    function snapshotFromCanvas(canvas){
        return {
            nodes:Object.fromEntries((canvas?.nodes || []).filter(node => node?.id).map(node => [String(node.id), JSON.stringify(node)])),
            edges:Object.fromEntries((canvas?.connections || []).map(edge => [entityId(edge), JSON.stringify(edge)])),
            meta:JSON.stringify({
                title:canvas?.title || '',
                icon:canvas?.icon || 'sparkles',
                logs:canvas?.logs || [],
            }),
        };
    }

    // Runtime task flags make the node repaint while a request is queued or
    // polling. They are useful in the live editor, but should not start an
    // autosave window by themselves. Keep them out of the durable comparison;
    // the normal save payload still retains them when a real content edit is
    // being persisted.
    const TRANSIENT_NODE_KEYS = [
        'running', 'pending', 'queued', 'pendingTasks', 'jimengPending',
        'runStartedAt', 'runFinishedAt', 'runElapsedMs', 'runTimerHidden',
        '_runMetaTargetId', 'conversationSending', 'agentPlanning', 'agentExecuting'
    ];

    function durableMediaItem(value){
        const clean = clone(value);
        if(!clean || typeof clean !== 'object' || Array.isArray(clean)) return clean;
        delete clean._naturalSizeLoading;
        // These values come from the rendered preview element and can change
        // after a successful save without changing the user's content.
        delete clean.layout_w;
        delete clean.layout_h;
        return clean;
    }

    function durableNode(node){
        const clean = clone(node || {});
        TRANSIENT_NODE_KEYS.forEach(key => delete clean[key]);
        if(Array.isArray(clean.images)) clean.images = clean.images.map(durableMediaItem);
        if(clean.inputImage) clean.inputImage = durableMediaItem(clean.inputImage);
        return clean;
    }

    function durableSnapshotFromCanvas(canvas){
        const clean = clone(canvas || {});
        clean.nodes = (clean.nodes || []).map(durableNode);
        return snapshotFromCanvas(clean);
    }

    function mutationsFromCanvas(canvas, previousSnapshot){
        const next = snapshotFromCanvas(canvas);
        const previous = previousSnapshot || {nodes:{}, edges:{}, meta:''};
        const mutations = [];
        Object.entries(next.nodes).forEach(([id, encoded]) => {
            if(previous.nodes?.[id] !== encoded) mutations.push({op:'upsert_node', node:parse(encoded, {})});
        });
        Object.keys(previous.nodes || {}).forEach(id => {
            if(!(id in next.nodes)) mutations.push({op:'remove_node', node_id:id});
        });
        Object.entries(next.edges).forEach(([id, encoded]) => {
            if(previous.edges?.[id] !== encoded){
                const edge = parse(encoded, {});
                if(!edge.id) edge.id = id;
                mutations.push({op:'upsert_edge', edge});
            }
        });
        Object.keys(previous.edges || {}).forEach(id => {
            if(!(id in next.edges)) mutations.push({op:'remove_edge', edge_id:id});
        });
        if(previous.meta !== next.meta) mutations.push({op:'set_canvas_meta', ...parse(next.meta, {})});
        return {mutations, next};
    }

    function upsert(values, incoming){
        const id = entityId(incoming);
        const result = clone(values || []);
        const index = result.findIndex(item => entityId(item) === id);
        if(index >= 0) result[index] = clone(incoming);
        else result.push(clone(incoming));
        return result;
    }

    function applyMutation(canvas, mutation){
        const next = canvas;
        if(mutation.op === 'upsert_node') next.nodes = upsert(next.nodes, mutation.node);
        else if(mutation.op === 'remove_node'){
            const id = String(mutation.node_id || '');
            next.nodes = (next.nodes || []).filter(node => String(node.id) !== id);
            next.connections = (next.connections || []).filter(edge => {
                const source = String(edge.from ?? edge.source ?? edge.fromNode ?? edge.sourceNodeId ?? '');
                const target = String(edge.to ?? edge.target ?? edge.toNode ?? edge.targetNodeId ?? '');
                return source !== id && target !== id;
            });
        } else if(mutation.op === 'upsert_edge') next.connections = upsert(next.connections, mutation.edge);
        else if(mutation.op === 'remove_edge') next.connections = (next.connections || []).filter(edge => entityId(edge) !== String(mutation.edge_id || ''));
        // Viewport is application UI state and is intentionally not part of
        // project mutations. Keep accepting the old operation as a no-op so a
        // stale client cannot reintroduce it during conflict replay.
        else if(mutation.op === 'set_canvas_meta'){
            ['title','icon','logs'].forEach(key => {
                if(Object.prototype.hasOwnProperty.call(mutation, key)) next[key] = clone(mutation[key]);
            });
        }
        return next;
    }

    function rebaseProjectCanvasMutations(serverCanvas, baseline, mutations){
        const working = clone(serverCanvas);
        working.nodes = Array.isArray(working.nodes) ? working.nodes : [];
        working.connections = Array.isArray(working.connections) ? working.connections : [];
        const rebased = [];
        for(const mutation of mutations || []){
            let nextMutation = clone(mutation);
            if(mutation.op === 'upsert_node'){
                const id = String(mutation.node?.id || '');
                const base = parse(baseline?.nodes?.[id], undefined);
                const remote = working.nodes.find(node => String(node.id) === id);
                if(base !== undefined && remote === undefined) continue;
                const node = mergeThreeWay(base, mutation.node, remote);
                if(equal(node, remote)) continue;
                nextMutation = {op:'upsert_node', node};
            } else if(mutation.op === 'remove_node'){
                if(!working.nodes.some(node => String(node.id) === String(mutation.node_id || ''))) continue;
            } else if(mutation.op === 'upsert_edge'){
                const id = entityId(mutation.edge || {});
                const base = parse(baseline?.edges?.[id], undefined);
                const remote = working.connections.find(edge => entityId(edge) === id);
                if(base !== undefined && remote === undefined) continue;
                const edge = mergeThreeWay(base, mutation.edge, remote);
                if(equal(edge, remote)) continue;
                nextMutation = {op:'upsert_edge', edge};
            } else if(mutation.op === 'remove_edge'){
                if(!working.connections.some(edge => entityId(edge) === String(mutation.edge_id || ''))) continue;
            } else if(mutation.op === 'set_viewport'){
                // A viewport mutation from an older client is UI-only. Drop it
                // instead of carrying it into the project revision.
                continue;
            } else if(mutation.op === 'set_canvas_meta'){
                const base = parse(baseline?.meta, {});
                const remote = {
                    title:working.title || '', icon:working.icon || 'sparkles',
                    logs:working.logs || [],
                };
                const local = {...base};
                ['title','icon','logs'].forEach(key => {
                    if(Object.prototype.hasOwnProperty.call(mutation, key)) local[key] = mutation[key];
                });
                const meta = mergeThreeWay(base, local, remote);
                nextMutation = {op:'set_canvas_meta'};
                ['title','icon','logs'].forEach(key => {
                    if(!equal(meta[key], remote[key])) nextMutation[key] = meta[key];
                });
                if(Object.keys(nextMutation).length === 1) continue;
            }
            rebased.push(nextMutation);
            applyMutation(working, nextMutation);
        }
        return {canvas:working, mutations:rebased};
    }

    function rebaseProjectCanvasResponse(serverCanvas, baseline, localCanvas){
        // A delayed response from another tab must never roll the current
        // canvas back to an older revision.  Applying it would also replace
        // the local message list and silently discard newer conversation
        // turns before any save can be scheduled.
        const serverRevision = Number(serverCanvas?.revision || 0);
        const localRevision = Number(localCanvas?.revision || 0);
        if(Number.isFinite(serverRevision) && Number.isFinite(localRevision) && serverRevision < localRevision){
            return {
                ignored:true,
                canvas:clone(localCanvas),
                mutations:[],
                baseline:clone(baseline),
                serverSnapshot:snapshotFromCanvas(serverCanvas),
                localSnapshot:snapshotFromCanvas(localCanvas),
            };
        }
        const localChanges = mutationsFromCanvas(localCanvas, baseline);
        const rebased = rebaseProjectCanvasMutations(serverCanvas, baseline, localChanges.mutations);
        const serverSnapshot = snapshotFromCanvas(serverCanvas);
        return {
            ignored:false,
            canvas:rebased.canvas,
            mutations:rebased.mutations,
            baseline:serverSnapshot,
            serverSnapshot,
            localSnapshot:localChanges.next,
        };
    }

    async function requestAfterProjectCanvasFlush(options){
        if(typeof options?.flush !== 'function') throw new Error('canvas flush is required');
        if(typeof options?.request !== 'function') throw new Error('request callback is required');
        const saved = await options.flush();
        if(saved !== true) throw new Error(options.failureMessage || '项目画布保存失败，请重试');
        return options.request();
    }

    async function flushProjectCanvasMutations(options){
        const send = options?.send;
        if(typeof send !== 'function') throw new Error('mutation sender is required');
        let canvas = clone(options.canvas || {});
        let baseline = clone(options.baseline || snapshotFromCanvas(canvas));
        let mutations = clone(options.mutations || []);
        const maxConflictRetries = Math.max(0, Number(options.maxConflictRetries ?? 2));
        let conflictRetries = 0;
        while(true){
            if(!mutations.length) return {saved:true, canvas, mutations:[], conflictRetries, confirmedWithoutWrite:true};
            const response = await send(Number(canvas.revision || 0), clone(mutations));
            const status = Number(response?.status || 0);
            if(status >= 200 && status < 300){
                mutations.forEach(mutation => applyMutation(canvas, mutation));
                canvas.revision = response.revision ?? canvas.revision;
                canvas.updated_at = response.updated_at ?? canvas.updated_at;
                let saved = response.saved !== false;
                if(!saved && typeof options?.confirmSaved === 'function'){
                    saved = await options.confirmSaved(response, clone(canvas)) === true;
                }
                return {saved, canvas, mutations, conflictRetries, response};
            }
            if(status !== 409 || !response?.canvas) return {saved:false, canvas, mutations, conflictRetries, response};
            const latest = clone(response.canvas);
            const rebased = rebaseProjectCanvasMutations(latest, baseline, mutations);
            canvas = rebased.canvas;
            mutations = rebased.mutations;
            baseline = snapshotFromCanvas(latest);
            if(conflictRetries >= maxConflictRetries){
                return {
                    saved:false,
                    canvas,
                    serverCanvas:latest,
                    baseline,
                    mutations,
                    conflictRetries,
                    response,
                    conflictLimitReached:true,
                };
            }
            conflictRetries += 1;
        }
    }

    return {
        flushProjectCanvasMutations,
        rebaseProjectCanvasMutations,
        rebaseProjectCanvasResponse,
        requestAfterProjectCanvasFlush,
        mutationsFromCanvas,
        snapshotFromCanvas,
        durableSnapshotFromCanvas,
    };
});
