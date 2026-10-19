// 画布节点连线模块（从 smart-canvas.js 抽离，#158 P2a）。
// 连线渲染/操作函数：渲染、刷新、事件绑定、拖拽连线、擦除、断开、
// reroute、级联键等。引用主脚本全局（canvas/canvasState.nodes/shell/world 等），
// 本文件只含函数定义（无顶层执行代码），先于 smart-canvas.js 加载，
// 所有调用都发生在主脚本全局初始化之后，安全。
// 注意：WebSocket 的 connectAssetLibrarySyncSocket / connectAgentWebSocket
// 不是画布连线，仍留在主脚本。

// 这些状态本来属于本模块，#173 核心下沉第 2 步从 smart-canvas.js 搬回此处。
let connectionEraseState = null;
function rerouteSmartConnections(fromId, toId){
    if(canvas){
        canvas.connections = (canvas.connections || []).map(c => {
            let conn = c;
            if(c.from === fromId) conn = {...conn, from:toId};
            if(c.to === fromId) conn = {...conn, to:toId};
            return conn;
        }).filter((c, i, arr) => c.from !== c.to && arr.findIndex(x => x.from === c.from && x.to === c.to && (x.kind || 'flow') === (c.kind || 'flow')) === i);
    }
    canvasState.nodes.forEach(n => {
        if(Array.isArray(n.inputNodeIds)) n.inputNodeIds = Array.from(new Set(n.inputNodeIds.map(id => id === fromId ? toId : id).filter(id => id !== n.id)));
    });
}

function connectedSmartClusterIds(seedId){
    const ids = new Set(canvasState.nodes.map(n => n.id));
    if(!ids.has(seedId)) return [];
    const seen = new Set([seedId]);
    const queue = [seedId];
    while(queue.length){
        const id = queue.shift();
        (canvas?.connections || []).forEach(conn => {
            if(conn.from !== id && conn.to !== id) return;
            const next = conn.from === id ? conn.to : conn.from;
            if(!ids.has(next) || seen.has(next)) return;
            seen.add(next);
            queue.push(next);
        });
    }
    return [...seen];
}

function arrangeSmartIdsByConnections(ids){
    const idSet = new Set(smartArrangeAtomicIds(ids));
    const selectedNodes = [...idSet].map(id => canvasState.nodes.find(n => n.id === id)).filter(Boolean);
    if(selectedNodes.length < 2) return false;
    const rects = selectedNodes.map(node => ({node, rect:nodeRect(node)}));
    const startX = Math.min(...rects.map(item => item.rect.x));
    const startY = Math.min(...rects.map(item => item.rect.y));
    const internal = (canvas?.connections || []).filter(c => idSet.has(c.from) && idSet.has(c.to));
    const depth = new Map(selectedNodes.map(node => [node.id, 0]));
    if(internal.length){
        const indegree = new Map(selectedNodes.map(node => [node.id, 0]));
        internal.forEach(c => indegree.set(c.to, (indegree.get(c.to) || 0) + 1));
        const roots = [...indegree.entries()].filter(([, n]) => n === 0).map(([id]) => id);
        const queue = roots.length ? roots.slice() : [selectedNodes[0].id];
        const seen = new Set(queue);
        while(queue.length){
            const id = queue.shift();
            internal.filter(c => c.from === id).forEach(c => {
                depth.set(c.to, Math.max(depth.get(c.to) || 0, (depth.get(id) || 0) + 1));
                if(!seen.has(c.to)){
                    seen.add(c.to);
                    queue.push(c.to);
                }
            });
        }
    }
    const groups = new Map();
    selectedNodes.forEach(node => {
        const d = depth.get(node.id) || 0;
        if(!groups.has(d)) groups.set(d, []);
        groups.get(d).push(node);
    });
    const sortedDepths = [...groups.keys()].sort((a, b) => a - b);
    let x = startX;
    sortedDepths.forEach(d => {
        const col = groups.get(d).slice().sort((a, b) => nodeRect(a).y - nodeRect(b).y || String(a.id).localeCompare(String(b.id)));
        let y = startY;
        let maxW = 0;
        col.forEach(node => {
            const rect = nodeRect(node);
            moveSmartNodeAtom(node, x, y);
            y += Math.max(110, rect.height) + 56;
            maxW = Math.max(maxW, Math.max(180, rect.width));
        });
        x += maxW + 180;
    });
    return true;
}

function mergeSmartConnections(localConns, remoteConns, nodeIds){
    const out = [];
    const seen = new Set();
    [...(localConns || []), ...(remoteConns || [])].forEach(c => {
        if(!c || !nodeIds.has(c.from) || !nodeIds.has(c.to)) return;
        const key = `${c.from}->${c.to}:${c.kind || 'flow'}`;
        if(seen.has(key)) return;
        seen.add(key);
        out.push(c);
    });
    return out;
}

function renderConnections(){
    const conns = (canvas?.connections || []).map((conn, index) => ({...conn, index})).filter(c => canvasState.nodes.some(n => n.id === c.from) && canvasState.nodes.some(n => n.id === c.to));
    const cascadeKeys = cascadeConnectionKeys();
    const activeRunPath = [...smartCascadeRuns.values()].filter(run => run?.runPath).pop()?.runPath || null;
    const activeCascadeCount = (activeRunPath?.states && Object.values(activeRunPath.states).filter(state => state && state !== 'done').length) || 0;
    const reduceMotion = activeCascadeCount > 24;
    const selectedConnIds = new Set(selectedNodeIds());
    // 合并连线：同一来源连到同一分组的多个成员，合成一条到分组的连线（A→A1/A2/A3 显示为 A→分组），
    // 减少“每张图都拖一条线”的杂乱。history 连线不合并。
    const buckets = new Map();
    const items = [];
    conns.forEach(conn => {
        const kind = conn.kind || 'flow';
        // #85：候选池不再绘制 history 连线（数据关联保留在 canvas.connections 与 historyFor 字段，
        // 级联删除/撤销/保存恢复均不受影响）。
        if(kind === 'history') return;
        const fromScope = kind === 'history' ? '' : smartGroupScopeId(conn.from);
        const toScope = kind === 'history' ? '' : smartGroupScopeId(conn.to);
        // 同一分组内部的连线（成员↔成员、成员↔分组本体）属于内部关系，入组后隐藏，保持整洁。
        if(fromScope && fromScope === toScope) return;
        // 终点是某分组的成员：把同一来源连到该分组各成员的线合并成一条到分组的线。
        const isMemberTarget = toScope && toScope !== conn.to;
        if(isMemberTarget){
            const key = `${conn.from}|${toScope}|${kind}`;
            let b = buckets.get(key);
            if(!b){ b = {merged:true, from:conn.from, toId:toScope, kind, indices:[], targets:[]}; buckets.set(key, b); items.push(b); }
            b.indices.push(conn.index);
            b.targets.push(conn.to);
        } else {
            items.push({merged:false, from:conn.from, toId:conn.to, kind, indices:[conn.index], targets:[conn.to]});
        }
    });
    const paths = items.map(item => {
        const fromNode = canvasState.nodes.find(n => n.id === item.from);
        const toNode = canvasState.nodes.find(n => n.id === item.toId);
        if(!fromNode || !toNode) return '';
        const fr = nodeRect(fromNode), tr = nodeRect(toNode);
        const kind = item.kind;
        const isHistory = kind === 'history';
        const dataIndex = item.indices.join(',');
        const isInsertPreview = item.indices.some(i => loopInsertPreview?.index === i);
        const edgeKeys = item.targets.map(t => `${item.from}->${t}`);
        const states = edgeKeys.map(smartCascadeEdgeState).filter(Boolean);
        let cascadeState = '';
        if(states.includes('active')) cascadeState = 'active';
        else if(states.some(s => s !== 'done')) cascadeState = states.find(s => s !== 'done');
        else if(states.length) cascadeState = 'done';
        const isCascade = !isHistory && (edgeKeys.some(k => cascadeKeys.has(k)) || Boolean(cascadeState) || isInsertPreview);
        const isPendingLine = !isCascade && item.targets.some(t => canvasState.nodes.find(n => n.id === t)?.pending);
        const isSelectedLine = selectedConnIds.size > 0 && (selectedConnIds.has(item.from) || selectedConnIds.has(item.toId) || item.targets.some(t => selectedConnIds.has(t)));
        const fx = isHistory ? fr.x + fr.width / 2 : fr.x + fr.width;
        const fy = isHistory ? fr.y + fr.height : fr.y + fr.height / 2;
        const tx = isHistory ? tr.x + tr.width / 2 : tr.x;
        const ty = isHistory ? tr.y : tr.y + tr.height / 2;
        const dx = Math.max(50, Math.abs(tx - fx) * 0.45);
        const dy = Math.max(36, Math.abs(ty - fy) * 0.45);
        const curve = isHistory
            ? `M${fx} ${fy} C ${fx} ${fy+dy}, ${tx} ${ty-dy}, ${tx} ${ty}`
            : `M${fx} ${fy} C ${fx+dx} ${fy}, ${tx-dx} ${ty}, ${tx} ${ty}`;
        const mx = (fx + tx) / 2, my = (fy + ty) / 2;
        const cls = [
            isPendingLine ? 'conn-pending' : '',
            isCascade ? 'conn-cascade' : '',
            isCascade && cascadeState === 'done' ? 'conn-cascade-done' : '',
            isCascade && Boolean(cascadeState) && cascadeState !== 'done' ? 'conn-cascade-wait' : '',
            isCascade && cascadeState === 'active' ? 'conn-cascade-active' : '',
            isHistory ? 'conn-history' : '',
            isSelectedLine ? 'conn-selected' : ''
        ].filter(Boolean).join(' ');
        const color = isCascade ? '#16a34a' : isHistory ? 'rgba(100,116,139,0.46)' : kind === 'input' ? 'rgba(100,116,139,0.62)' : 'rgba(148,163,184,0.62)';
        const opacity = isPendingLine ? '.82' : '1';
        const width = kind === 'input' ? '1.9' : '1.6';
        return `<path class="${cls} conn-line" data-conn-index="${dataIndex}" d="${curve}" stroke="${color}" stroke-width="${width}" fill="none" opacity="${opacity}"></path><path class="conn-hit" data-conn-index="${dataIndex}" d="${curve}" stroke="transparent" stroke-width="14" fill="none"></path><circle class="conn-end" data-conn-index="${dataIndex}" cx="${tx}" cy="${ty}" r="3.5" fill="${color}" opacity=".66"></circle><g class="conn-cut" data-conn-index="${dataIndex}" transform="translate(${mx} ${my})"><circle r="8" fill="var(--card)" stroke="${color}" stroke-width="1.4"></circle><path d="M-3 -3 L3 3 M3 -3 L-3 3" stroke="${color}" stroke-width="1.5" stroke-linecap="round"></path></g>`;
    }).join('');
    return `<svg class="connection-layer ${reduceMotion ? 'conn-reduce-motion' : ''}" width="6000" height="4000" viewBox="0 0 6000 4000" xmlns="http://www.w3.org/2000/svg">${paths}</svg>`;
}

function refreshConnectionLayer(){
    canvasState.connectionLayerRaf = 0;
    const oldSvg = world.querySelector('svg.connection-layer');
    if(!oldSvg) return;
    const tpl = document.createElement('template');
    tpl.innerHTML = renderConnections().trim();
    const nextSvg = tpl.content.firstElementChild;
    if(nextSvg) oldSvg.replaceWith(nextSvg);
    bindConnectionEvents();
}

function scheduleConnectionLayerRefresh(){
    if(canvasState.connectionLayerRaf) return;
    canvasState.connectionLayerRaf = requestAnimationFrame(refreshConnectionLayer);
}

function bindConnectionEvents(){
    world.querySelectorAll('[data-conn-index]').forEach(el => {
        if(el.classList.contains('conn-hit')){
            el.addEventListener('dblclick', e => {
                e.preventDefault(); e.stopPropagation();
                disconnectConnections(el.dataset.connIndex);
            });
            return;
        }
        el.addEventListener('click', e => {
            e.preventDefault(); e.stopPropagation();
            disconnectConnections(el.dataset.connIndex);
        });
    });
}

function dragConnectTargetFor(sourceNode, point=lastMouseWorld){
    if(!sourceNode || (dragState?.group || []).length > 1) return null;
    if([NODE_TYPE_PROMPT, NODE_TYPE_LOOP].includes(sourceNode.type) && point){
        return rectOverlapNode(sourceNode.id, point.x - 1, point.y - 1, 2, 2, dragState?.groupIds || []);
    }
    const r = nodeRect(sourceNode);
    return rectOverlapNode(sourceNode.id, r.x, r.y, r.width, r.height, dragState?.groupIds || []);
}

function canAutoConnectDraggedNode(sourceNode, targetNode){
    if(!sourceNode || !targetNode || sourceNode.id === targetNode.id) return false;
    if(isHistoryGroupNode(sourceNode) || isHistoryGroupNode(targetNode)) return false;
    if(isSmartGroupNode(targetNode)) return false;
    if(isSmartImageNode(sourceNode)) return isSmartImageNode(targetNode) || targetNode.type === NODE_TYPE_LOOP || targetNode.type === NODE_TYPE_PROMPT || targetNode.type === NODE_TYPE_CONVERSATION;
    if(sourceNode.type === NODE_TYPE_PROMPT) return isSmartImageNode(targetNode) || targetNode.type === NODE_TYPE_LOOP;
    if(sourceNode.type === NODE_TYPE_LOOP) return isSmartImageNode(targetNode);
    if(sourceNode.type === NODE_TYPE_GROUP) return isSmartImageNode(targetNode) || targetNode.type === NODE_TYPE_LOOP;
    // #149 C7: 补齐 local-edit/conversation/agent 源类型（与端口拖拽 connectInputNode 一致）
    if(sourceNode.type === NODE_TYPE_LOCAL_EDIT) return isSmartImageNode(targetNode);
    if(sourceNode.type === NODE_TYPE_CONVERSATION) return isSmartImageNode(targetNode) || targetNode.type === NODE_TYPE_LOOP || targetNode.type === NODE_TYPE_PROMPT;
    if(sourceNode.type === 'agent') return isSmartImageNode(targetNode) || targetNode.type === NODE_TYPE_LOOP || targetNode.type === NODE_TYPE_PROMPT;
    // agent-task 是只读结果卡，不接受输入连线（#149 C8）
    return false;
}

function disconnectConnections(spec){
    if(!canvas || !Array.isArray(canvas.connections)) return;
    const indices = (Array.isArray(spec) ? spec : String(spec).split(','))
        .map(v => Number(v))
        .filter(n => Number.isInteger(n) && n >= 0 && n < canvas.connections.length);
    if(!indices.length) return;
    const set = new Set(indices);
    const removed = canvas.connections.filter((_, i) => set.has(i));
    if(!removed.length) return;
    pushUndo();
    canvas.connections = canvas.connections.filter((_, i) => !set.has(i));
    removed.forEach(conn => {
        const toNode = canvasState.nodes.find(n => n.id === conn.to);
        if(toNode && Array.isArray(toNode.inputNodeIds)){
            toNode.inputNodeIds = toNode.inputNodeIds.filter(id => id !== conn.from);
        }
        if(toNode?.type === NODE_TYPE_LOCAL_EDIT) syncLocalEditInputRoles(toNode);
        if(toNode && ['input','flow'].includes(conn.kind || 'flow')) clearDetachedRunInputRefs(toNode);
        if((conn.kind || 'flow') === 'history'){
            const group = canvasState.nodes.find(n => n.id === conn.to && isHistoryGroupNode(n) && n.historyFor === conn.from);
            demoteHistoryGroupNode(group);
        }
    });
    render();
    scheduleSave();
}

function connectionIndexSpecFromPoint(clientX, clientY){
    const el = document.elementFromPoint(clientX, clientY);
    const connEl = el?.closest?.('[data-conn-index]');
    return connEl?.dataset?.connIndex || '';
}

function eraseConnectionsAtClientPoint(clientX, clientY){
    if(!connectionEraseState || !canvas || !Array.isArray(canvas.connections)) return false;
    const spec = connectionIndexSpecFromPoint(clientX, clientY);
    if(!spec) return false;
    const indices = String(spec).split(',')
        .map(v => Number(v))
        .filter(n => Number.isInteger(n) && n >= 0 && n < canvas.connections.length && !connectionEraseState.indices.has(n));
    if(!indices.length) return false;
    indices.forEach(index => connectionEraseState.indices.add(index));
    connectionEraseState.started = true;
    connectionEraseState.count = connectionEraseState.indices.size;
    world.querySelectorAll('[data-conn-index]').forEach(el => {
        const hasHit = String(el.dataset.connIndex || '').split(',').some(v => connectionEraseState.indices.has(Number(v)));
        if(hasHit) el.classList.add('conn-erasing-mark');
    });
    return true;
}

function finishConnectionErase(){
    if(!connectionEraseState || !canvas || !Array.isArray(canvas.connections)) return false;
    const set = new Set(connectionEraseState.indices || []);
    if(!set.size) return false;
    const removed = canvas.connections.filter((_, i) => set.has(i));
    if(!removed.length) return false;
    pushUndo();
    canvas.connections = canvas.connections.filter((_, i) => !set.has(i));
    removed.forEach(conn => {
        const toNode = canvasState.nodes.find(n => n.id === conn.to);
        if(toNode && Array.isArray(toNode.inputNodeIds)){
            toNode.inputNodeIds = toNode.inputNodeIds.filter(id => id !== conn.from);
        }
        if(toNode?.type === NODE_TYPE_LOCAL_EDIT) syncLocalEditInputRoles(toNode);
        if(toNode && ['input','flow'].includes(conn.kind || 'flow')) clearDetachedRunInputRefs(toNode);
        if((conn.kind || 'flow') === 'history'){
            const group = canvasState.nodes.find(n => n.id === conn.to && isHistoryGroupNode(n) && n.historyFor === conn.from);
            demoteHistoryGroupNode(group);
        }
    });
    render();
    return true;
}

function eraseConnectionsAtPoint(event){
    return eraseConnectionsAtClientPoint(event.clientX, event.clientY);
}

function eraseConnectionsAlongPointer(event){
    if(!connectionEraseState) return false;
    const lastX = Number.isFinite(connectionEraseState.lastX) ? connectionEraseState.lastX : event.clientX;
    const lastY = Number.isFinite(connectionEraseState.lastY) ? connectionEraseState.lastY : event.clientY;
    const dx = event.clientX - lastX;
    const dy = event.clientY - lastY;
    const steps = Math.max(1, Math.min(12, Math.ceil(Math.hypot(dx, dy) / 8)));
    let changed = false;
    for(let i = 1; i <= steps; i++){
        const t = i / steps;
        changed = eraseConnectionsAtClientPoint(lastX + dx * t, lastY + dy * t) || changed;
    }
    connectionEraseState.lastX = event.clientX;
    connectionEraseState.lastY = event.clientY;
    return changed;
}

function ensureConnectionEraseTrail(){
    let svg = shell.querySelector(':scope > svg.connection-erase-trail');
    if(svg) return svg;
    svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'connection-erase-trail');
    svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = '<path class="connection-erase-trail-glow" fill="none"></path><path class="connection-erase-trail-line" fill="none"></path>';
    shell.appendChild(svg);
    return svg;
}

function updateConnectionEraseTrail(event){
    if(!connectionEraseState) return;
    const p = shellPoint(event);
    connectionEraseState.trail = [...(connectionEraseState.trail || []), p].slice(-80);
    const points = connectionEraseState.trail;
    const svg = ensureConnectionEraseTrail();
    const rect = shell.getBoundingClientRect();
    svg.setAttribute('viewBox', `0 0 ${Math.max(1, rect.width)} ${Math.max(1, rect.height)}`);
    const d = points.map((pt, index) => `${index ? 'L' : 'M'}${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`).join(' ');
    svg.querySelectorAll('path').forEach(path => path.setAttribute('d', d));
}

function clearConnectionEraseTrail(){
    const svg = shell.querySelector(':scope > svg.connection-erase-trail');
    if(!svg) return;
    svg.classList.add('fading');
    setTimeout(() => svg.remove(), 180);
}

function connectionMidpoint(conn){
    const fromNode = canvasState.nodes.find(n => n.id === conn?.from);
    const toNode = canvasState.nodes.find(n => n.id === conn?.to);
    if(!fromNode || !toNode) return null;
    const fr = nodeRect(fromNode), tr = nodeRect(toNode);
    if((conn.kind || 'flow') === 'history'){
        return {x:(fr.x + fr.width / 2 + tr.x + tr.width / 2) / 2, y:(fr.y + fr.height + tr.y) / 2};
    }
    return {x:(fr.x + fr.width + tr.x) / 2, y:(fr.y + fr.height / 2 + tr.y + tr.height / 2) / 2};
}

function insertionConnectionForNode(node){
    if(!node || node.type !== NODE_TYPE_LOOP || !canvas?.connections?.length) return null;
    const r = nodeRect(node);
    const cx = (Number(r.x) || 0) + (Number(r.width) || 0) / 2;
    const cy = (Number(r.y) || 0) + (Number(r.height) || 0) / 2;
    let best = null;
    (canvas.connections || []).forEach((conn, index) => {
        const kind = conn.kind || 'flow';
        if(!['input','flow'].includes(kind)) return;
        if(conn.from === node.id || conn.to === node.id) return;
        const fromNode = canvasState.nodes.find(n => n.id === conn.from);
        const toNode = canvasState.nodes.find(n => n.id === conn.to);
        if(!fromNode || !toNode || isHistoryGroupNode(fromNode) || isHistoryGroupNode(toNode)) return;
        const mid = connectionMidpoint(conn);
        if(!mid) return;
        const score = Math.hypot(cx - mid.x, cy - mid.y);
        if(score > 96) return;
        if(!best || score < best.score) best = {conn, index, score};
    });
    return best;
}

function addConnection(fromId, toId, kind='flow'){
    if(!fromId || !toId || fromId === toId) return;
    if(!canvas) return;
    canvas.connections = canvas.connections || [];
    if(canvas.connections.some(c => c.from === fromId && c.to === toId && (c.kind || 'flow') === kind)) return;
    canvas.connections.push({from:fromId, to:toId, kind});
}

function connectInputNode(fromId, toId){
    const from = canvasState.nodes.find(n => n.id === fromId);
    const to = canvasState.nodes.find(n => n.id === toId);
    if(!from || !to || from.id === to.id) return false;
    // #149 C8: agent-task 是只读结果卡，不接受输入连线
    if(to.type === NODE_TYPE_TASK){
        toast(tr('smart.connectAgentTaskReadOnly'));
        return false;
    }
    // #149 C8: Prompt→Conversation 连线不生效（Conversation 只把图片类上游当参考图，Prompt 文本被忽略）
    if(from.type === NODE_TYPE_PROMPT && to.type === NODE_TYPE_CONVERSATION){
        toast(tr('smart.connectPromptToConversationIgnored'));
        return false;
    }
    if(to.type === NODE_TYPE_LOCAL_EDIT){
        const role = ['original','references','prompt'].includes(to.localEditActiveRole) ? to.localEditActiveRole : '';
        if(!role) return false; // role must be explicitly selected before connecting
        to.localEditInputRoles = to.localEditInputRoles || {originalNode:'', referenceNodes:[], promptNode:'', promptNodes:[]};
        to.localEditInputRoles.promptNodes = Array.from(new Set([...(to.localEditInputRoles.promptNodes || []), to.localEditInputRoles.promptNode].filter(Boolean)));
        if(role === 'original') to.localEditInputRoles.originalNode = from.id;
        else if(role === 'prompt') {
            to.localEditInputRoles.promptNodes = Array.from(new Set([...(to.localEditInputRoles.promptNodes || []), from.id]));
            to.localEditInputRoles.promptNode = to.localEditInputRoles.promptNodes[0] || '';
        }
        else to.localEditInputRoles.referenceNodes = Array.from(new Set([...(to.localEditInputRoles.referenceNodes || []), from.id]));
        to.inputNodeIds = Array.from(new Set([...(to.inputNodeIds || []), from.id]));
        if(!canvas) return true;
        canvas.connections = canvas.connections || [];
        if(role === 'original') canvas.connections = canvas.connections.filter(c => !(c.to === to.id && c.kind === 'input' && c.role === role));
        if(!canvas.connections.some(c => c.from === from.id && c.to === to.id && (c.role || '') === role)) canvas.connections.push({from:from.id, to:to.id, kind:'input', role});
        return true;
    }
    if(to.type === NODE_TYPE_LOOP){
        const groupImages = isSmartGroupNode(from) ? imagesForNode(from).filter(img => img?.url) : [];
        const groupPrompts = isSmartGroupNode(from) ? promptTextItemsForNode(from).filter(Boolean) : [];
        const looksImage = isSmartImageNode(from) || groupImages.length > 0 || (from.type === NODE_TYPE_LOOP && from.imageInput);
        const looksPrompt = from.type === NODE_TYPE_PROMPT || groupPrompts.length > 0 || (from.type === NODE_TYPE_LOOP && from.showPrompt);
        if(looksImage && !to.imageInput) to.imageInput = true;
        if(looksPrompt && !to.showPrompt) to.showPrompt = true;
        if(looksImage || looksPrompt) fitSmartLoopNode(to);
        const canImage = Boolean(to.imageInput) && looksImage;
        const canPrompt = Boolean(to.showPrompt) && looksPrompt;
        if(!canImage && !canPrompt) return false;
    }
    to.inputNodeIds = Array.from(new Set([...(to.inputNodeIds || []), from.id]));
    addConnection(from.id, to.id, 'input');
    return true;
}

function lineConnectionsFor(node){
    if(!node) return [];
    return (canvas?.connections || []).filter(conn => {
        if(!conn?.from || !conn?.to || conn.from === conn.to) return false;
        return ['input', 'flow'].includes(conn.kind || 'flow');
    });
}

function outgoingConnectionsFor(node, kinds=['input']){
    if(!node) return [];
    const allowed = new Set(kinds);
    return (canvas?.connections || []).filter(conn => conn.from === node.id && allowed.has(conn.kind || 'flow'));
}

function cascadeConnectionKeys(){
    const keys = new Set();
    const addKey = (from, to) => {
        if(from && to) keys.add(`${from}->${to}`);
    };
    const activeLoopIds = new Set(smartCascadeRuns.keys());
    const loops = activeLoopIds.size
        ? canvasState.nodes.filter(n => n?.type === NODE_TYPE_LOOP && activeLoopIds.has(n.id))
        : canvasState.nodes.filter(n => n?.type === NODE_TYPE_LOOP);
    loops.forEach(loop => {
        const tail = cascadeTailForLoop(loop.id);
        if(!tail) return;
        const graph = smartCascadeGraphForTail(tail);
        if(!graph.root) return;
        const chainIds = new Set(graph.path.map(n => n.id));
        graph.edges.forEach(edge => addKey(edge.source.id, edge.target.id));
        (canvas?.connections || []).forEach(conn => {
            if((conn.kind || 'flow') === 'history') return;
            const toNode = canvasState.nodes.find(n => n.id === conn.to);
            if(conn.from === loop.id && (chainIds.has(conn.to) || downstreamNodesForId(conn.to).some(n => chainIds.has(n.id)))) addKey(conn.from, conn.to);
            if(toNode && chainIds.has(toNode.id)){
                inputNodesFor(toNode).filter(n => n?.type === NODE_TYPE_LOOP && n.showPrompt).forEach(inputLoop => addKey(inputLoop.id, toNode.id));
            }
        });
    });
    return keys;
}

// 这些名字本来属于本模块，#200 从 smart-canvas.js 搬回（核心不引用它们，纯搬家）。
let loopInsertPreview = null;
let lastMouseWorld = null;
function smartCascadeEdgeState(edgeKey){
    for(const run of smartCascadeRuns.values()){
        const state = run?.runPath?.states?.[edgeKey];
        if(state) return state;
    }
    return '';
}
function smartGroupScopeId(nodeId){
    const group = smartGroupContainingNode(nodeId);
    if(group) return group.id;
    const node = canvasState.nodes.find(n => n.id === nodeId);
    return isSmartGroupNode(node) ? node.id : '';
}
function moveSmartNodeAtom(node, x, y){
    const dx = Math.round(x - (Number(node.x) || 0));
    const dy = Math.round(y - (Number(node.y) || 0));
    translateSmartNodeWithMembers(node, dx, dy);
}
function shellPoint(event){
    const rect = shell.getBoundingClientRect();
    return {x:event.clientX - rect.left, y:event.clientY - rect.top};
}
