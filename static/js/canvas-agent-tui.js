// 画布 Agent TUI 会话节点（从 smart-canvas.js 抽离，#154）。
// invariant: classic script，按 HTML 顺序先于 smart-canvas.js 加载；本文件顶层只有声明，
// 初始化器只取空容器或字面量，不读其他模块的状态。函数声明整体提升，实际调用都发生在
// 主脚本初始化之后。跨模块可变状态一律走 canvasState（window.canvasState），不要引用裸 nodes/selectedId。

function isSmartAgentNode(node){
    return Boolean(node && node.type === NODE_TYPE_AGENT);
}

// #316：Agent 卡内图标统一 SVG sprite 图标（与画布其它节点同一视觉语言，emoji 跨系统渲染不一致）
function agentIco(name){ return ico(name); }

function isSmartAgentTaskNode(node){
    return Boolean(node && node.type === NODE_TYPE_TASK);
}

// Agent TUI state management
const agentTuiState = new Map(); // nodeId -> { socket, messages, status, model, autonomy }

// All host views are transient. Persisted settings/bindings belong to the canvas receiver.
function createAgentHostView() {
    return {
        messages: [], toolActivities: new Map(), status: 'idle', model: '', autonomy: 'standard',
        thinkingVisible: false, pendingImages: [], attachWorking: '', draft: '', composing: false,
        pendingRerender: false, scrollState: null, inputFocus: null, nodeRefsDraft: null,
        thinking: [], timeline: null, timelineMessages: null, timelineTools: null, timelineSeq: 0,
        timelineSourceMessages: null, timelineSourceTools: null, toolGroupExpanded: {}, thoughtExpanded: {},
        _timelineContent: null, _turnTextMessage: null, _turnSeq: 0, _turnFailed: false, _aborted: false,
        lastSeq: 0, _replayBuffer: [], _replayInFlight: false, _applyingReplay: false,
        historyLoaded: false, historyCursor: null, historyHasMore: false, historyLoadingOlder: false,
        historyBlocked: false, historyRetry: '', olderPageSerial: 0, _batchHistory: false,
        _historyPrefetch: null, _replayAgain: false, _historyGapSince: null,
        confirm: null, pendingConfirms: new Map(), confirmModalShown: null, confirmModalOwner: null, stopPending: false, queuedInputs: new Set(),
        effortState: null, eventsError: '', stopStatusError: '', lastError: '', sessionModel: undefined,
        planPreviews: new Map(),
    };
}
function agentNodeHost(node) { return node.agentCli || 'pi'; } // legacy nodes belong only to Pi
function agentHostSetting(node, host) {
    if (node.agentHostSettings && node.agentHostSettings[host]) return node.agentHostSettings[host];
    return { model: host === agentNodeHost(node) ? (node.model || '') : '', effort: null };
}
function getAgentTuiState(nodeId) {
    if (!agentTuiState.has(nodeId)) {
        const node = canvasState.nodes.find(n => n.id === nodeId);
        const host = node ? agentNodeHost(node) : 'pi';
        agentTuiState.set(nodeId, {
            ...createAgentHostView(), socket: null, activeHost: host, viewHost: host, hostViews: new Map(),
            viewEpoch: 0, retryCount: 0, reconnectTimer: null, reconnectStopped: false, closing: false,
        });
    }
    agentNodeRefsInit(agentTuiState.get(nodeId));
    return agentTuiState.get(nodeId);
}
function agentIsReadonly(state) { return state.viewHost !== state.activeHost; }
function agentCompletionBridge() {
    const completion = window.canvasAgentCompletion;
    completion?.configure({
        getView: id => agentTuiState.get(id),
        getViews: () => Array.from(agentTuiState.entries()),
        hasNode: id => canvasState.nodes.some(node => node.id === id && isSmartAgentNode(node)),
        refreshView: renderAgentNode,
        append: agentAppendMessage,
        locked: state => agentParametersLocked(state) || !canvasState.nodes.some(node =>
            agentTuiState.get(node.id) === state && agentNodeHost(node) === state.viewHost),
        ask: async message => Boolean(await window.ffDialog?.ask({title: '确认撤销 Agent 操作', message, confirmText: '确认撤销', cancelText: '取消'})),
        reload: project => window.waitForCanvasRefresh?.(project),
        revealRow: (state, body, row) => {
            const rect = body.getBoundingClientRect();
            body.scrollTop += (row.getBoundingClientRect().top - rect.top) * agentScrollGeometryScale(body, rect);
            captureAgentScroll(state, body, true);
            if (state.scrollBinding) state.scrollBinding.programTop = body.scrollTop;
            updateAgentBackLatest(state);
        },
        marks: (project, marks) => window.canvasAgentActivity?.setDiffMarks(project, marks),
    });
    return completion;
}
function agentChatBridge() {
    const chat = window.canvasAgentChat;
    chat?.configure({
        getOwner: id => canvasState.nodes.some(node => node.id === id && isSmartAgentNode(node)) ? agentTuiState.get(id) : null,
        hasNode: id => canvasState.nodes.some(node => node.id === id && isSmartAgentNode(node)),
        getComposer: agentNodeRefsInput,
    });
    return chat;
}
function agentCaptureComposer(state, nodeId) {
    const input = agentNodeRefsInput(nodeId);
    if (input && !input.disabled && !state.viewNeedsMount) {
        state.draft = input.value;
        state.inputFocus = { focused: document.activeElement === input, start: input.selectionStart,
            end: input.selectionEnd, direction: input.selectionDirection, scrollTop: input.scrollTop };
    }
    if (state.scrollBinding) captureAgentScroll(state, state.scrollBinding.body);
}
function agentChangeHostView(nodeId, state, host) {
    agentCaptureComposer(state, nodeId);
    if (state.nodeRefsDraft) state.nodeRefsDraft.lastDepartureEpoch = state.viewEpoch;
    for (const view of state.planPreviews.values()) if (view.busy) {
        if (view.requestEpoch !== state.viewEpoch) view.invalidOwner = true;
        view.departed = true;
    }
    disposeAgentScroll(state);
    state.hostViews.set(state.viewHost, Object.fromEntries(Object.keys(createAgentHostView()).map(key => [key, state[key]])));
    state.hostViews.get(state.viewHost)._replayInFlight = false;
    state.hostViews.get(state.viewHost).historyLoadingOlder = false;
    const view = state.hostViews.get(host) || createAgentHostView();
    Object.assign(state, view);
    agentNodeRefsInit(state);
    state.viewHost = host;
    state.viewNeedsMount = true;
    state.viewEpoch++;
    state.nodeRefsDraft.lastArrivalEpoch = state.viewEpoch;
    for (const view of state.planPreviews.values()) if (view.busy && view.departed && !view.invalidOwner) {
        view.requestEpoch = state.viewEpoch;
        view.departed = false;
    }
    agentChatBridge()?.invalidate();
    agentCompletionBridge()?.invalidate();
    state._replayInFlight = false;
    state._replayBuffer = [];
    closeAgentModelPicker(state);
    if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
    state.retryCount = 0;
    state.reconnectStopped = false;
    // Invalidate callbacks BEFORE close; late old-host frames must never reach the new view.
    const socket = state.socket;
    state.socket = null;
    if (socket) socket.close();
    queueMicrotask(() => {
        if (agentTuiState.get(nodeId) !== state || state.viewHost !== host || state.closing) return;
        for (const snapshot of state.nodeRefsDraft.pending.values()) {
            if (!snapshot.deferredSettlement) continue;
            const { receipt, error, uncertain } = snapshot.deferredSettlement;
            delete snapshot.deferredSettlement;
            agentNodeRefsSettle(nodeId, state, snapshot, receipt, error, uncertain);
        }
        for (const view of state.planPreviews.values()) view.resume?.();
    });
}
function agentSyncHostView(node, state) {
    const host = agentNodeHost(node);
    if (host === state.activeHost) return;
    const wasReadonly = agentIsReadonly(state);
    state.activeHost = host;
    if (!wasReadonly || state.viewHost === host) agentChangeHostView(node.id, state, host);
    else { // An external authoritative switch must also invalidate the old active socket.
        state.viewEpoch++;
        const socket = state.socket;
        state.socket = null;
        if (socket) socket.close();
    }
}
function agentParametersLocked(state) {
    const active = agentIsReadonly(state) ? state.hostViews.get(state.activeHost) : state;
    return Boolean(state.settingsPending || state.hostConfirmPending || state.composing || state.attachWorking || (active && (
        ['busy', 'starting', 'connecting', 'retrying', 'waiting_confirm', 'stopping', 'reconnecting'].includes(active.status)
        || active.confirm || active.stopPending || active._replayInFlight || active.queuedInputs.size || active.nodeRefsDraft?.pending.size)));
}
async function agentViewHost(nodeId, projectId, host) {
    const state = getAgentTuiState(nodeId);
    if (state.composing || state.settingsPending || state.attachWorking || host === state.viewHost) return;
    agentChangeHostView(nodeId, state, host);
    renderAgentNode(nodeId);
    state.projectId = projectId;
    return replayAgentEvents(nodeId, state);
}

// #409: references belong to this input draft, never to a saved canvas node.
const agentNodeRefsRuntime = { activeId: '', sequence: 0 };
function agentNodeRefsInit(state) {
    if (!state.nodeRefsDraft) state.nodeRefsDraft = {
        selected: [], manual: [], dismissed: new Set(), labels: new Map(), selectionKey: null,
        revision: 0, menu: null, pending: new Map(), error: '',
    };
    return state.nodeRefsDraft;
}
function agentNodeRefsIds(state) {
    const refs = agentNodeRefsInit(state);
    return [...new Set([...refs.selected.filter(id => !refs.dismissed.has(id)), ...refs.manual])];
}
function agentNodeRefsLabel(nodeId, refs) {
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if (node) {
        const label = { node_id: nodeId, type: node.type || '', title: String(node.title || node.task_id || node.id) };
        refs.labels.set(nodeId, label);
        return label;
    }
    return refs.labels.get(nodeId) || { node_id: nodeId, type: '', title: nodeId };
}
function syncAgentInputNodeRefs(nodeId) {
    const agents = canvasState.nodes.filter(isSmartAgentNode);
    const target = agents.find(n => n.id === nodeId)
        || agents.find(n => n.id === agentNodeRefsRuntime.activeId)
        || agents.find(n => n.dock === 'left' || n.dock === 'right')
        || (agents.length === 1 ? agents[0] : null);
    if (!target) return;
    if (nodeId) agentNodeRefsRuntime.activeId = target.id;
    const state = getAgentTuiState(target.id), refs = agentNodeRefsInit(state);
    if (agentIsReadonly(state)) return;
    const selected = [...new Set(canvasState.selectedIds && canvasState.selectedIds.length
        ? canvasState.selectedIds : (canvasState.selectedId ? [canvasState.selectedId] : []))]
        .filter(id => id !== target.id && canvasState.nodes.some(n => n.id === id));
    const key = JSON.stringify(selected.slice().sort());
    if (key === refs.selectionKey) return;
    refs.selectionKey = key;
    refs.selected = selected;
    refs.dismissed.clear();
    refs.revision++;
    selected.forEach(id => agentNodeRefsLabel(id, refs));
    agentNodeRefsRefresh(target.id);
}
function agentNodeRefsRefresh(nodeId) {
    // The hoisted composer adapter updates only references, not the focused/IME textarea.
    agentNodeRefsRefreshPanel(nodeId);
}
function agentNodeRefsPrepare(nodeId, kind, message, images = [], options = {}) {
    const state = getAgentTuiState(nodeId), refs = agentNodeRefsInit(state);
    const control = options.preserveComposer === true;
    const snapshot = {
        id: `input-${Date.now()}-${++agentNodeRefsRuntime.sequence}`, kind, message, host: state.viewHost,
        draft: state.nodeRefsControlDraft !== undefined ? state.nodeRefsControlDraft : message,
        control, controlCurrent: options.current, controlRefs: control ? refs : null, controlEpoch: state.viewEpoch,
        images: images.slice(), selected: refs.selected.slice(), manual: refs.manual.slice(),
        dismissed: new Set(refs.dismissed), previousStatus: state.status, turn: state._turnSeq || 0,
        labels: control ? [] : agentNodeRefsIds(state).map(id => ({ ...agentNodeRefsLabel(id, refs) })),
    };
    delete state.nodeRefsControlDraft;
    // Card actions are independent user inputs, not submissions of the unfinished composer (#411).
    if (!control) {
        refs.selected = [];
        refs.manual = [];
        refs.menu = null;
        refs.error = '';
        snapshot.clearedRevision = ++refs.revision;
    }
    snapshot.row = {
        role: kind === 'prompt' ? 'user' : 'system',
        text: kind === 'prompt' ? message : `${kind === 'steer' ? '↪ steer' : '⏳ follow_up queued'}: ${message}`,
        attachments: images.map(img => (img && img.name) || '').filter(Boolean),
        node_refs: snapshot.labels, request_id: snapshot.id, input_type: kind, delivery: 'pending',
    };
    agentAppendMessage(state, snapshot.row);
    if (kind === 'prompt') state.status = 'busy';
    else state.queuedInputs.add(snapshot.id);
    refs.pending.set(snapshot.id, snapshot);
    options.onPrepared?.(snapshot);
    snapshot.timer = setTimeout(() => agentNodeRefsSettle(nodeId, state, snapshot, null, tr('smart.agentRefUnconfirmed'), true), 30000);
    return snapshot;
}
function agentNodeRefsSettle(nodeId, state, snapshot, receipt, error = '', uncertain = false) {
    if (agentTuiState.get(nodeId) !== state) return;
    if (snapshot.host !== state.viewHost) {
        snapshot.deferredSettlement = { receipt, error, uncertain };
        clearTimeout(snapshot.timer);
        return;
    }
    const refs = agentNodeRefsInit(state);
    // A read-only host visit changes the render epoch, not the admitted input's owner (#411).
    const restoredView = snapshot.control && refs === snapshot.controlRefs
        && refs.lastDepartureEpoch >= snapshot.controlEpoch && refs.lastArrivalEpoch === state.viewEpoch;
    if (snapshot.controlCurrent && !snapshot.controlCurrent(restoredView)) {
        clearTimeout(snapshot.timer);
        snapshot.controlRefs.pending.delete(snapshot.id);
        state.queuedInputs.delete(snapshot.id);
        return;
    }
    if (!refs.pending.has(snapshot.id)) return;
    // A timeout is not a rejection: retain correlation for late REST/WS receipts.
    if (!uncertain) refs.pending.delete(snapshot.id);
    if (!receipt && !uncertain) state.queuedInputs.delete(snapshot.id);
    clearTimeout(snapshot.timer);
    if (receipt) {
        if (refs.error === snapshot.row.delivery_error) refs.error = '';
        delete snapshot.row.delivery_error;
        snapshot.row.node_refs = Array.isArray(receipt.node_refs) ? receipt.node_refs : [];
        snapshot.row.delivery = 'accepted';
        if (snapshot.kind === 'prompt') delete refs.rejectedPrompt;
        if (snapshot.notice) snapshot.notice.text = tr('smart.agentRefConfirmed');
    } else {
        snapshot.row.delivery = 'unconfirmed';
        snapshot.row.delivery_error = error;
        refs.error = error;
        // A late failure must not replace text/references/images typed for a newer input.
        if (!snapshot.control && !uncertain && !state.draft && !(state.pendingImages || []).length && refs.revision === snapshot.clearedRevision) {
            state.draft = snapshot.draft;
            const input = agentNodeRefsInput(nodeId);
            if (input) input.value = snapshot.draft;
            refs.selected = snapshot.selected;
            refs.manual = snapshot.manual;
            refs.dismissed = snapshot.dismissed;
            refs.revision++;
            if (!(state.pendingImages || []).length) state.pendingImages = snapshot.images;
        }
        if (!uncertain && snapshot.kind === 'prompt') refs.rejectedPrompt = snapshot;
        const notice = tr('smart.agentRefFailed').replace('{error}', error);
        if (snapshot.notice) snapshot.notice.text = notice;
        else snapshot.notice = agentAppendMessage(state, { role: 'system', text: notice });
    }
    // A prompt can reject before its optimistic follow-ups settle. Keep its
    // rollback until the whole batch ends; never undo a real newer turn.
    if (!refs.pending.size && refs.rejectedPrompt) {
        const rejected = refs.rejectedPrompt;
        if (state.status === 'busy' && (state._turnSeq || 0) === rejected.turn) state.status = rejected.previousStatus;
        delete refs.rejectedPrompt;
    }
    renderAgentNode(nodeId);
}
function agentNodeRefsError(body, fallback) {
    const detail = body && (body.detail || body.message);
    return typeof detail === 'string' ? detail : (detail && detail.message) || fallback;
}
function agentNodeRefsTransport(nodeId, projectId, snapshot) {
    const state = getAgentTuiState(nodeId);
    const body = { message: snapshot.message, request_id: snapshot.id };
    if (snapshot.labels.length) body.refs = snapshot.labels.map(ref => ({ node_id: ref.node_id }));
    if (snapshot.kind === 'prompt') body.images = snapshot.images.map(img => typeof img === 'string' ? img : (img && img.dataUrl) || '').filter(Boolean);
    const attachments = snapshot.images.map(img => img && img.attachmentId).filter(Boolean);
    if (attachments.length) body.attachments = attachments;
    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        try { state.socket.send(JSON.stringify({ type: snapshot.kind, ...body })); }
        catch (error) { agentNodeRefsSettle(nodeId, state, snapshot, null, String(error.message || error)); }
        renderAgentNode(nodeId);
        return;
    }
    renderAgentNode(nodeId);
    return fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/${snapshot.kind}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(async response => {
        let receipt;
        try { receipt = await response.json(); }
        catch (_error) { throw new Error(`HTTP ${response.status}: ${tr('smart.agentRefInvalidReceipt')}`); }
        if (!response.ok) throw new Error(agentNodeRefsError(receipt, `HTTP ${response.status}`));
        if (!receipt || receipt.request_id !== snapshot.id || receipt.input_type !== snapshot.kind || !Array.isArray(receipt.node_refs)) throw new Error(tr('smart.agentRefInvalidReceipt'));
        agentNodeRefsSettle(nodeId, state, snapshot, receipt);
    }).catch(error => agentNodeRefsSettle(nodeId, state, snapshot, null, String(error.message || error)));
}
function agentNodeRefsApplyEvent(nodeId, event, recordedAt) {
    const state = getAgentTuiState(nodeId), refs = agentNodeRefsInit(state);
    if (event.type === 'input_accepted' || event.type === 'input_rejected') {
        const pending = refs.pending.get(event.request_id);
        if (pending && event.input_type === pending.kind) {
            if (event.type === 'input_accepted' && Array.isArray(event.node_refs)) agentNodeRefsSettle(nodeId, state, pending, event);
            else agentNodeRefsSettle(nodeId, state, pending, null, String(event.message || tr('smart.agentRefInvalidReceipt')));
        }
        return true;
    }
    if (event.type === 'session') state.queueStopped = ['stopped', 'crashed', 'aborted'].includes(event.status);
    else if (event.type === 'agent_start') state.queueStopped = false;
    if (event.type === 'input_delivery_terminal') {
        state.queuedInputs.delete(event.request_id);
        const row = state.messages.find(msg => msg.request_id === event.request_id);
        if (row) {
            row.delivery = event.delivery;
            row.text = `${event.delivery === 'uncertain' ? '状态不确定' : '未发送'}: ${row.text.replace(/^⏳ follow_up queued: /, '')}`;
            if (row.attachments_missing_on_restart) row.text += `（${row.attachments_missing_on_restart} 个附件不可恢复，请重新添加）`;
        }
        renderAgentNode(nodeId);
        return true;
    }
    if (event.type === 'queued_input' || (event.type === 'user_prompt' && (event.request_id ||
        (Array.isArray(event.node_refs) && event.node_refs.length) ||
        (Array.isArray(event.attachments) && event.attachments.length)))) {
        // Reconnect may replay the already-optimistic row; reconcile by per-input identity.
        if (event.type === 'queued_input' && event.replay_delivery === 'delivered') return true;
        if (event.type === 'queued_input' && !event.replay_delivery) state.queuedInputs.add(event.request_id || `queued:${event.seq}`);
        else if (event.request_id) state.queuedInputs.delete(event.request_id);
        const existing = event.request_id && state.messages.find(msg => msg.request_id === event.request_id);
        const pending = refs.pending.get(event.request_id);
        const canonical = Array.isArray(event.node_refs) ? event.node_refs : [];
        const attachments = Array.isArray(event.attachments) ? event.attachments.map(item => item.name).filter(Boolean) : [];
        if (pending) agentNodeRefsSettle(nodeId, state, pending, { node_refs: canonical });
        const timestamp = event.type === 'user_prompt' ? recordedAt : undefined;
        if (existing) {
            existing.node_refs = canonical;
            existing.attachments_missing_on_restart = event.attachments_missing_on_restart || 0;
            if (event.attachments) existing.attachments = attachments;
            existing.delivery = 'accepted';
            if (event.type === 'user_prompt') {
                existing.role = 'user';
                existing.text = String(event.text || '');
            } else existing.input_type = event.input_type;
            if (timestamp !== undefined) existing.timestamp = timestamp;
        } else agentAppendMessage(state, { role: event.type === 'user_prompt' ? 'user' : 'system',
            text: event.type === 'user_prompt' ? String(event.text || '') : `${event.input_type === 'steer' ? '↪ steer' : '⏳ follow_up queued'}: ${String(event.text || '')}`,
            node_refs: canonical, attachments, request_id: event.request_id, queue_id: event.type === 'queued_input' ? (event.request_id || `queued:${event.seq}`) : undefined, input_type: event.input_type, delivery: 'accepted',
            attachments_missing_on_restart: event.attachments_missing_on_restart || 0,
            ...(timestamp !== undefined ? { timestamp } : {}) });
        if (event.type === 'queued_input' && event.replay_delivery) {
            const row = existing || state.messages[state.messages.length - 1];
            row.delivery = event.replay_delivery;
            row.text = `${event.replay_delivery === 'unsent' ? '未发送' : '状态不确定'}: ${String(event.text || '')}`;
            if (row.attachments_missing_on_restart) row.text += `（${row.attachments_missing_on_restart} 个附件不可恢复，请重新添加）`;
        }
        renderAgentNode(nodeId);
        return true;
    }
    return false;
}
function agentNodeRefsForget(nodeId) {
    const state = agentTuiState.get(nodeId);
    if (state && state.nodeRefsDraft) {
        for (const pending of state.nodeRefsDraft.pending.values()) clearTimeout(pending.timer);
        state.nodeRefsDraft.pending.clear();
    }
    if (agentNodeRefsRuntime.activeId === nodeId) agentNodeRefsRuntime.activeId = '';
}

function agentNodeRefsInput(nodeId) {
    return [...document.querySelectorAll('[data-agent-tui-input]')].find(el => el.getAttribute('data-agent-tui-input') === nodeId) || null;
}
function agentNodeRefsType(type) {
    return ({
        'smart-image': ['node-image', 'smart.agentRefImage'],
        'smart-prompt': ['text-cursor-input', 'smart.agentRefPrompt'],
        'smart-group': ['group', 'smart.agentRefGroup'],
        'smart-agent': ['bot', 'smart.agentRefAgent'],
        'smart-conversation': ['messages-square', 'smart.agentRefConversation'],
        'agent-task': ['list-checks', 'smart.agentRefTask'],
    })[type] || ['file', 'smart.agentRefNode'];
}
function agentNodeRefsVisual(ref) {
    const [icon, key] = agentNodeRefsType(ref.type);
    // Reuse only a thumbnail already shown on this canvas; never request original media for context.
    const card = [...document.querySelectorAll('[data-id]')].find(el => el.getAttribute('data-id') === ref.node_id);
    const image = card && card.querySelector('img');
    const src = image && (image.currentSrc || image.getAttribute('src'));
    const thumb = ref.type === 'smart-image' && src && /^(https?:|\/|data:image\/)/i.test(src)
        ? `<img class="agent-node-ref-thumb" src="${escapeAttr(src)}" alt="">` : agentIco(icon);
    return `<span class="agent-node-ref-visual" title="${escapeAttr(tr(key))}">${thumb}</span>`;
}
function agentNodeRefsMessageHtml(message) {
    const chips = (Array.isArray(message.node_refs) ? message.node_refs : []).map(ref =>
        `<button type="button" class="agent-node-ref-jump" data-agent-ref-jump="${escapeAttr(ref.node_id)}" title="${escapeAttr(tr('smart.agentRefLocate'))}">${agentNodeRefsVisual(ref)}<span>${escapeHtml(String(ref.title || ref.node_id))}</span></button>`).join('');
    const status = message.delivery === 'pending' ? tr('smart.agentRefPending')
        : message.delivery === 'unconfirmed' ? String(message.delivery_error || tr('smart.agentRefUnconfirmed')) : '';
    return (chips ? `<div class="agent-node-ref-message">${chips}</div>` : '')
        + (status ? `<div class="agent-node-ref-delivery" role="status">${escapeHtml(status)}</div>` : '');
}
function agentNodeRefsContent(nodeId) {
    const state = getAgentTuiState(nodeId), refs = agentNodeRefsInit(state);
    const ids = agentNodeRefsIds(state);
    const chips = ids.map(id => {
        const label = agentNodeRefsLabel(id, refs);
        const missing = !canvasState.nodes.some(node => node.id === id);
        const locate = `<button type="button" class="agent-node-ref-chip${missing ? ' agent-node-ref-missing' : ''}" data-agent-ref-draft-jump="${escapeAttr(id)}" title="${escapeAttr(missing ? tr('smart.agentRefDeleted') : label.title)}"${missing ? ' disabled' : ''}><span class="agent-node-ref-title">${escapeHtml(label.title)}</span><span class="agent-node-ref-arrow" aria-hidden="true">↗</span></button>`;
        const remove = `<button type="button" class="agent-node-ref-remove" data-agent-ref-remove="${escapeAttr(id)}" aria-label="${escapeAttr(tr('smart.agentRefRemove').replace('{title}', label.title))}">${agentIco('x')}</button>`;
        return ids.length === 1 ? locate + remove : `<span class="agent-node-ref-pair">${locate}${remove}</span>`;
    }).join('');
    const menu = refs.menu;
    const options = menu ? menu.items.map((ref, index) => `<button type="button" role="option" tabindex="-1" id="agent-ref-option-${escapeAttr(nodeId)}-${index}" aria-selected="${index === menu.index}" data-agent-ref-choice="${escapeAttr(ref.node_id)}">${agentNodeRefsVisual(ref)}<span>${escapeHtml(ref.title)}</span><span class="agent-node-ref-kind">${escapeHtml(tr(agentNodeRefsType(ref.type)[1]))}</span></button>`).join('') : '';
    return (chips ? `<div class="agent-node-ref-chips" aria-label="${escapeAttr(tr('smart.agentRefDraft'))}"><span class="agent-tui-ref-label">本轮引用</span>${chips}</div>` : '')
        + (menu ? `<div class="agent-node-refs-menu" id="agent-ref-menu-${escapeAttr(nodeId)}" role="listbox" aria-label="${escapeAttr(tr('smart.agentRefSearch'))}">${options || `<div class="agent-node-ref-empty" role="status">${escapeHtml(tr('smart.agentRefNoMatches'))}</div>`}</div>` : '')
        + (refs.error ? `<div class="agent-node-ref-error" role="alert">${escapeHtml(refs.error)}</div>` : '');
}
function agentNodeRefsComposerHtml(nodeId) {
    return `<div class="agent-node-refs" data-agent-refs="${escapeAttr(nodeId)}">${agentNodeRefsContent(nodeId)}</div>`;
}
function agentNodeRefsRefreshPanel(nodeId) {
    const root = [...document.querySelectorAll('[data-agent-refs]')].find(el => el.getAttribute('data-agent-refs') === nodeId);
    if (root) { root.innerHTML = agentNodeRefsContent(nodeId); agentNodeRefsBindPanel(root, nodeId); }
    const input = agentNodeRefsInput(nodeId);
    if (!input) return;
    const menu = agentNodeRefsInit(getAgentTuiState(nodeId)).menu;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', menu ? 'true' : 'false');
    input.setAttribute('aria-controls', `agent-ref-menu-${nodeId}`);
    if (menu && menu.items.length) input.setAttribute('aria-activedescendant', `agent-ref-option-${nodeId}-${menu.index}`);
    else input.removeAttribute('aria-activedescendant');
}
function agentNodeRefsMention(input, nodeId) {
    const state = getAgentTuiState(nodeId), refs = agentNodeRefsInit(state);
    if (state.composing) return;
    const caret = input.selectionStart;
    const match = input.selectionStart === input.selectionEnd && input.value.slice(0, caret).match(/(?:^|\s)@([^\s@]*)$/u);
    if (!match) refs.menu = null;
    else {
        const query = match[1].toLocaleLowerCase();
        const items = canvasState.nodes.filter(node => node.id !== nodeId
            && `${node.title || ''} ${node.task_id || ''} ${node.id}`.toLocaleLowerCase().includes(query))
            .slice(0, 8).map(node => agentNodeRefsLabel(node.id, refs));
        refs.menu = { items, index: 0, start: caret - match[1].length - 1, end: caret };
    }
    agentNodeRefsRefreshPanel(nodeId);
}
function agentNodeRefsChoose(nodeId, id) {
    const state = getAgentTuiState(nodeId), refs = agentNodeRefsInit(state);
    const input = agentNodeRefsInput(nodeId), menu = refs.menu;
    if (!input || !menu || state.composing || !canvasState.nodes.some(node => node.id === id)) return;
    refs.manual = [...new Set([...refs.manual, id])];
    refs.dismissed.delete(id);
    refs.revision++;
    refs.menu = null;
    input.setRangeText('', menu.start, menu.end, 'end');
    state.draft = input.value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    agentNodeRefsRefreshPanel(nodeId);
    input.focus();
}
function agentNodeRefsBindPanel(root, nodeId) {
    root.querySelectorAll('button').forEach(button => button.addEventListener('mousedown', event => { event.preventDefault(); event.stopPropagation(); }));
    root.querySelectorAll('[data-agent-ref-remove]').forEach(button => button.addEventListener('click', event => {
        event.stopPropagation();
        const refs = agentNodeRefsInit(getAgentTuiState(nodeId)), id = button.getAttribute('data-agent-ref-remove');
        refs.manual = refs.manual.filter(value => value !== id);
        refs.dismissed.add(id);
        refs.revision++;
        agentNodeRefsRefreshPanel(nodeId);
    }));
    root.querySelectorAll('[data-agent-ref-choice]').forEach(button => button.addEventListener('click', event => {
        event.stopPropagation();
        agentNodeRefsChoose(nodeId, button.getAttribute('data-agent-ref-choice'));
    }));
    root.querySelectorAll('[data-agent-ref-draft-jump]').forEach(button => button.addEventListener('click', event => {
        event.stopPropagation();
        agentNodeRefsJump(nodeId, button.getAttribute('data-agent-ref-draft-jump'));
    }));
}
function agentNodeRefsJump(nodeId, id) {
    if (!canvasState.nodes.some(target => target.id === id)) {
        agentNodeRefsInit(getAgentTuiState(nodeId)).error = tr('smart.agentRefDeleted');
        agentNodeRefsRefreshPanel(nodeId);
        return;
    }
    canvasState.agentNodeRefsLocate(id);
    const target = [...document.querySelectorAll('[data-id]')].find(card => card.getAttribute('data-id') === id);
    if (target) {
        target.classList.remove('agent-node-ref-flash');
        void target.offsetWidth;
        target.classList.add('agent-node-ref-flash');
        target.addEventListener('animationend', () => target.classList.remove('agent-node-ref-flash'), { once: true });
    }
}
function agentNodeRefsBind(el, node) {
    const nodeId = node.id, state = getAgentTuiState(nodeId), refs = agentNodeRefsInit(state);
    if (agentIsReadonly(state)) return;
    const input = el.querySelector('[data-agent-tui-input]');
    const panel = el.querySelector('[data-agent-refs]');
    if (panel) agentNodeRefsBindPanel(panel, nodeId);
    if (input) {
        input.addEventListener('focus', () => { syncAgentInputNodeRefs(nodeId); agentNodeRefsRefreshPanel(nodeId); });
        input.addEventListener('input', () => {
            if (state.draft !== input.value) refs.revision++;
            agentNodeRefsMention(input, nodeId);
        });
        input.addEventListener('click', () => agentNodeRefsMention(input, nodeId));
        input.addEventListener('compositionstart', () => { refs.menu = null; agentNodeRefsRefreshPanel(nodeId); });
        input.addEventListener('compositionend', () => queueMicrotask(() => {
            const current = agentNodeRefsInput(nodeId);
            if (current) agentNodeRefsMention(current, nodeId);
        }));
        input.addEventListener('keyup', event => {
            if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) agentNodeRefsMention(input, nodeId);
        });
        input.addEventListener('blur', () => queueMicrotask(() => {
            const active = document.activeElement;
            if (!active || active.getAttribute('data-agent-tui-input') !== nodeId) { refs.menu = null; agentNodeRefsRefreshPanel(nodeId); }
        }));
        input.addEventListener('keydown', event => {
            if (event.isComposing || event.keyCode === 229 || state.composing || !refs.menu) return;
            const menu = refs.menu;
            if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); refs.menu = null; agentNodeRefsRefreshPanel(nodeId); }
            else if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && menu.items.length) {
                event.preventDefault(); event.stopImmediatePropagation();
                menu.index = (menu.index + (event.key === 'ArrowDown' ? 1 : -1) + menu.items.length) % menu.items.length;
                agentNodeRefsRefreshPanel(nodeId);
            } else if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault(); event.stopImmediatePropagation();
                if (menu.items.length) agentNodeRefsChoose(nodeId, menu.items[menu.index].node_id);
            }
        }, true);
    }
    el.querySelectorAll('[data-agent-steer],[data-agent-followup]').forEach(button => button.addEventListener('click', () => {
        if (input) state.nodeRefsControlDraft = input.value;
    }, true));
    el.querySelectorAll('[data-agent-ref-jump]').forEach(button => {
        button.addEventListener('mousedown', event => event.stopPropagation());
        button.addEventListener('click', event => {
            event.stopPropagation();
            agentNodeRefsJump(nodeId, button.getAttribute('data-agent-ref-jump'));
        });
    });
    agentNodeRefsRefreshPanel(nodeId);
}

// Display metadata is memory-only; the source records retain their existing shape.
function resetAgentTimeline(state) {
    disposeAgentScroll(state);
    state.timeline = [];
    state.timelineMessages = new WeakMap();
    state.timelineTools = new Map();
    state.timelineSeq = 0;
    state.toolGroupExpanded = {};
    state.thoughtExpanded = {};
    state.thinking = [];
    state.scrollState = null;
    state._timelineContent = null;
    state._turnTextMessage = null;
    state._turnSeq = 0;
    state.timelineSourceMessages = state.messages;
    state.timelineSourceTools = state.toolActivities;
    state._timelineGeneration = (state._timelineGeneration || 0) + 1;
    agentCompletionBridge()?.invalidate();
}

function agentTimelineMessage(state, message, kind = 'message') {
    if (!state.timeline) {
        state.timeline = [];
        state.timelineMessages = new WeakMap();
        state.timelineTools = new Map();
        state.timelineSeq = 0;
    }
    let entry = state.timelineMessages.get(message);
    if (!entry) {
        entry = { key: `entry:${++state.timelineSeq}`, kind, message };
        state.timelineMessages.set(message, entry);
        state.timeline.push(entry);
    }
    return entry;
}

function archiveAgentThinking(state) {
    const thinking = state.thinking || [];
    if (!thinking.length) return;
    const text = thinking.map(t => t.text || '').join('');
    const entry = agentTimelineMessage(state, thinking[0], 'thinking');
    const message = { role: 'thinking', text };
    entry.kind = 'message';
    entry.message = message;
    state.timelineMessages.set(message, entry);
    if (text) state.messages.push(message);
    else state.timeline = state.timeline.filter(item => item !== entry);
    state.thinking = [];
}

function agentAppendMessage(state, message) {
    archiveAgentThinking(state);
    state.messages.push(message);
    agentTimelineMessage(state, message);
    return message;
}

function agentTimelineTool(state, toolCallId, activity) {
    // Also supports legacy/history fixtures whose records predate the display metadata.
    if (!state.timeline) agentTimelineSync(state);
    if (state.timelineTools.has(toolCallId)) return;
    const last = state.timeline[state.timeline.length - 1];
    if (last && last.kind === 'tools' && last.turn === activity.turn && !last.sealed) {
        last.items.push({ toolCallId, activity });
    } else {
        state.timeline.push({ kind: 'tools', key: `tools:${toolCallId || ++state.timelineSeq}`,
            turn: activity.turn, items: [{ toolCallId, activity }] });
    }
    state.timelineTools.set(toolCallId, activity);
}

function agentTimelineSync(state) {
    // History restore can replace the source containers without going through an event.
    if (state.timelineSourceMessages && (state.timelineSourceMessages !== state.messages || state.timelineSourceTools !== state.toolActivities)) {
        resetAgentTimeline(state);
    }
    state.timelineSourceMessages = state.messages;
    state.timelineSourceTools = state.toolActivities;
    if (!state.timeline) {
        state.timeline = [];
        state.timelineMessages = new WeakMap();
        state.timelineTools = new Map();
        state.timelineSeq = 0;
    }
    state.timeline = state.timeline.filter(entry => entry.kind === 'tools'
        ? entry.items.some(item => state.toolActivities.has(item.toolCallId))
        : entry.kind === 'thinking' ? (state.thinking || []).includes(entry.message) : state.messages.includes(entry.message));
    for (const entry of state.timeline) {
        if (entry.kind === 'tools') entry.items = entry.items.filter(item => state.toolActivities.has(item.toolCallId));
    }
    for (const id of state.timelineTools.keys()) if (!state.toolActivities.has(id)) state.timelineTools.delete(id);
    for (const msg of state.messages) agentTimelineMessage(state, msg);
    for (const thought of state.thinking || []) agentTimelineMessage(state, thought, 'thinking');
    for (const [id, activity] of state.toolActivities) {
        if (state.timelineTools.has(id)) {
            for (const entry of state.timeline) {
                if (entry.kind === 'tools') {
                    const item = entry.items.find(item => item.toolCallId === id);
                    if (item) item.activity = activity;
                }
            }
        } else agentTimelineTool(state, id, activity);
    }
    return state.timeline;
}

function agentDurationKnown(value) {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

function agentProgressHtml(node) {
    const run = node && node.agentRun;
    const states = ['空闲', '澄清', '计划预览', '执行中', '批量确认', '失败自处理', '等待用户', '已停止', '完成'];
    if (!run || typeof run !== 'object' || Array.isArray(run) || !states.includes(run.state) || run.state === '空闲') return '';
    const known = Number.isInteger(run.step) && Number.isInteger(run.total)
        && run.total > 0 && run.step >= 0 && run.step <= run.total;
    const label = run.state + (known ? ` · ${run.step}/${run.total}` : '');
    const stoppable = !['已停止', '完成'].includes(run.state);
    return `<div class="agent-tui-progress" data-agent-progress="${escapeAttr(node.id)}" role="status">
        <span class="agent-tui-progress-label">${escapeHtml(label)}</span>
        ${known ? `<span class="agent-tui-progress-track"><span class="agent-tui-progress-fill" style="width:${run.step / run.total * 100}%"></span></span>` : ''}
        ${stoppable ? `<button type="button" class="agent-tui-progress-stop" data-agent-abort="${escapeAttr(node.id)}">停止</button>` : ''}
    </div>`;
}

function agentToolNodeIds(args) {
    if (!args || typeof args !== 'object') return [];
    const ids = [args.node_id, ...(Array.isArray(args.node_ids) ? args.node_ids : [])];
    return [...new Set(ids.filter(id => typeof id === 'string' && id && canvasState.nodes.some(node => node.id === id)))];
}

// Names are the real registered executors in app/agent_extensions/canvas-tools.ts.
// Unknown tools retain their original name; targets are only labels we can verify now.
function agentToolSummary(activity) {
    const name = String(activity.name || '');
    const labels = {
        'canvas.list_nodes': '查看节点', 'canvas.get_node': '读取节点',
        'canvas.create_node': '新建节点', 'canvas.update_node': '更新节点',
        'canvas.delete_node': '删除节点', 'canvas.connect': '连接节点',
        'canvas.disconnect': '断开连接', 'canvas.search_projects': '搜索项目',
        'canvas.view': '查看画布', 'canvas.view_media': '查看素材',
        'canvas.edit_crop': '裁切素材', 'canvas.batch_edit': '批量修改素材',
        'canvas.edit_rotate': '旋转素材', 'canvas.edit_flip': '翻转素材',
        'canvas.edit_outpaint': '扩展画面', 'canvas.edit_erase': '擦除素材',
        'generate.trigger': '发起生成', 'generate.status': '查看生成状态',
        'generate.wait': '等待生成结果',
    };
    if (!Object.prototype.hasOwnProperty.call(labels, name)) return name;
    const args = activity.args;
    const targets = agentToolNodeIds(args).map(id => {
        const node = canvasState.nodes.find(item => item.id === id);
        return node.title || id;
    });
    if (name === 'canvas.create_node' && args && typeof args.title === 'string' && args.title.trim()) targets.push(args.title);
    return labels[name] + (targets.length ? ` · ${[...new Set(targets)].join('、')}` : '');
}

const agentTitleCompletion = { owned: false, listener: null };
function agentClearCompletionTitle() {
    if (typeof document === 'undefined') return;
    if (agentTitleCompletion.owned && typeof document.title === 'string' && document.title.startsWith('(✓) ')) {
        document.title = document.title.slice(4);
    }
    agentTitleCompletion.owned = false;
    if (agentTitleCompletion.listener && document.removeEventListener) {
        document.removeEventListener('visibilitychange', agentTitleCompletion.listener);
    }
    agentTitleCompletion.listener = null;
}
function agentMarkBackgroundCompletion() {
    if (typeof document === 'undefined' || !document.hidden || typeof document.title !== 'string') return;
    if (!document.title.startsWith('(✓) ')) {
        document.title = '(✓) ' + document.title;
        agentTitleCompletion.owned = true;
    }
    if (!agentTitleCompletion.listener && document.addEventListener) {
        agentTitleCompletion.listener = () => { if (!document.hidden) agentClearCompletionTitle(); };
        document.addEventListener('visibilitychange', agentTitleCompletion.listener);
    }
}

// #280：连续连接失败上限——达到后停止自动重连，节点保持失败态直到用户点「重试连接」。
const AGENT_RECONNECT_MAX_ATTEMPTS = 8;

// #280：重连指数退避（纯函数，tests/canvas_module_units/test-canvas-agent-tui.mjs 单测）。
// failCount 为已发生的连接失败次数（>=1），返回值是下一次连接前的等待毫秒数：
// 1s 起步、每次翻倍、上限 30s → 1, 2, 4, 8, 16, 30, 30, 30（第 8 次失败触顶直接停止，不再排程）。
function agentReconnectDelayMs(failCount, baseMs = 1000, capMs = 30000) {
    if (!Number.isFinite(failCount) || failCount < 1) return baseMs;
    const exponent = Math.min(failCount - 1, 30);
    return Math.min(capMs, baseMs * Math.pow(2, exponent));
}

// #280：安排一次退避重连（定时器到期直接尝试连接；失败后的下一次排程由 onclose 负责）。
function scheduleAgentReconnect(state, nodeId, projectId) {
    state.reconnectTimer = setTimeout(() => {
        state.reconnectTimer = null;
        openAgentWebSocket(nodeId, projectId);
    }, agentReconnectDelayMs(state.retryCount));
}

// #280：一次连接失败（后端断开 / 构造函数抛错）的统一记账：
// 累加连续失败次数；触顶停止等手动重试；否则按退避排下一次——
// 后端已明确报告失败（lastError 非空，如 spawn 失败）时徽标保持「启动失败」，
// 纯网络断开则显示「重连中」。然后重渲染。
function recordAgentConnectFailure(state, nodeId, projectId) {
    state.retryCount += 1;
    if (state.retryCount >= AGENT_RECONNECT_MAX_ATTEMPTS) {
        state.reconnectStopped = true;
        state.status = 'reconnect_stopped';
    } else {
        state.status = state.lastError ? 'failed' : 'reconnecting';
        scheduleAgentReconnect(state, nodeId, projectId);
    }
    renderAgentNode(nodeId);
}

function connectAgentWebSocket(nodeId, projectId, options = {}) {
    const state = getAgentTuiState(nodeId);
    // #318 票 C：存 projectId——session 事件触发的 events 回放需要拼 GET URL。
    state.projectId = projectId;
    if (agentIsReadonly(state)) return null; // History browsing never starts a host.
    // CONNECTING(0)/OPEN(1) 都算「连接在途」：不再新建（重复建连正是重连风暴的根源之一）。
    if (state.socket && Number(state.socket.readyState) <= 1) {
        return state.socket;
    }

    // #280：重连策略。此前每次渲染重绑定都立即 new WebSocket，后端失败
    // （握手成功但 spawn 炸掉→服务端立刻断开）会形成 关闭→重渲染→重连 的高频风暴。
    // 现在：失败后按 agentReconnectDelayMs 退避排程；达到 AGENT_RECONNECT_MAX_ATTEMPTS
    // 停止自动重连；force（手动「重试连接」）清退避状态并立即连接。
    const force = Boolean(options && options.force);
    if (force) {
        if (state.reconnectTimer) {
            clearTimeout(state.reconnectTimer);
            state.reconnectTimer = null;
        }
        state.retryCount = 0;
        state.reconnectStopped = false;
    } else if (state.reconnectStopped || state.reconnectTimer) {
        // 已触顶停止，或已有退避在途：不重复建连/排程（渲染重绑定也会走到这里）。
        return null;
    } else if (state.retryCount > 0) {
        // 有失败记录且无在途定时器（防御路径，正常情况 onclose 已排好）。
        state.status = state.lastError ? 'failed' : 'reconnecting';
        scheduleAgentReconnect(state, nodeId, projectId);
        renderAgentNode(nodeId);
        return null;
    }

    return openAgentWebSocket(nodeId, projectId);
}

function openAgentWebSocket(nodeId, projectId) {
    const state = getAgentTuiState(nodeId);

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/stream`;

    try {
        state.socket = new WebSocket(wsUrl);
    } catch (e) {
        console.error('Failed to connect agent WebSocket:', e);
        // #280：构造函数失败也算一次连续失败（拿不到 close 事件，否则不会进入退避）。
        recordAgentConnectFailure(state, nodeId, projectId);
        return null;
    }
    const socket = state.socket;

    // #280：本实例已被新连接（如手动「重试连接」）顶替时，忽略它迟到的事件——
    // 否则旧实例的 close/error 会误触发一次退避排程、或把新连接的状态改回 error。
    const epoch = state.viewEpoch, socketHost = state.activeHost;
    const isStale = () => state.socket !== socket || state.viewEpoch !== epoch || state.activeHost !== socketHost;

    socket.onmessage = (event) => {
        if (isStale()) return;
        let data;
        try { data = JSON.parse(event.data); }
        catch (e) { console.error('Failed to parse agent event:', e); return; }
        handleAgentEvent(nodeId, data);
    };

    socket.onclose = () => {
        if (isStale()) return;
        state.socket = null;
        state._turnFailed = true;
        finalizeAgentStreamingTurn(state);
        if (state.closing) {
            // #280：节点删除/主动清理——只标记已断开，不安排重连。
            state.status = 'disconnected';
            return;
        }
        // #280：后端主动断开（含「握手成功但 spawn 失败被服务端切断」）算一次连续失败：
        // 指数退避排下一次，触顶则停止等手动重试。
        recordAgentConnectFailure(state, nodeId, projectId);
    };

    socket.onerror = () => {
        if (isStale()) return;
        state.status = 'error';
        state._turnFailed = true;
        finalizeAgentStreamingTurn(state);
        // 真实契约里 onerror 之后总会跟 onclose，退避排程由 onclose 统一负责，这里不重复。
    };

    state.status = 'connecting';
    return state.socket;
}

// 本轮结束（turn_end）或本轮永远结束不了（进程崩溃 / WS 断开 / 出错）时必须把末条
// 流式消息定型：streaming 标记只在追加增量时写入且之前没人清，下一轮的
// text_delta / thinking_delta 就会续写到上一轮末尾（#222 的用户可见症状）。
function finalizeAgentStreamingTurn(state){
    const reply = state._turnTextMessage;
    if (reply) delete reply.streaming;
    const lastMessage = state.messages[state.messages.length - 1];
    if (lastMessage && lastMessage.streaming) delete lastMessage.streaming;
    archiveAgentThinking(state);
}

// Pi message.timestamp is in milliseconds. The Firefly replay envelope (not the WS event)
// has a server-recorded Unix time in seconds. Neither source exists on optimistic user rows.
function agentMessageTimestamp(ms) {
    return Number.isFinite(ms) && ms > 0 && ms <= 8.64e15 ? ms : undefined;
}

// Start the read-only first page as soon as the render has node and project IDs.
// A failed speculative request is discarded; the mounted view retries normally.
async function agentReadHistoryResponse(url) {
    const r = await fetch(url);
    const data = await r.json();
    if (!r.ok) throw new Error(agentNodeRefsError(data, `HTTP ${r.status}`));
    if (!Array.isArray(data.events)) throw new Error('Invalid Agent events response');
    return data;
}
function agentPrefetchInitialHistory(nodeId, projectId, state) {
    if (!projectId || state.historyLoaded || state.historyBlocked || state._replayInFlight || state._historyPrefetch) return;
    const host = state.viewHost;
    const url = `/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/events/page?host=${encodeURIComponent(host)}&turns=5`;
    const prefetch = { projectId, host, epoch: state.viewEpoch, generation: state._timelineGeneration || 0 };
    prefetch.promise = agentReadHistoryResponse(url).then(data => {
        if (typeof data.has_more !== 'boolean' || (data.has_more && typeof data.cursor !== 'string')) throw new Error('Invalid Agent history cursor');
        return data;
    }).catch(() => null);
    state._historyPrefetch = prefetch;
}

// #318 票 C：事件回放。WS session 确认事件后拉 GET events?since=lastSeq 重建：
// 页面刷新=全量（lastSeq 0）；断线重连=补缺口。竞态：回放在途时 live 事件先进
// 缓冲，批处理完再按 seq 刷（去重统一由 seq 承担）。
function replayAgentEvents(nodeId, state) {
    const projectId = state.projectId;
    if (!projectId) return;
    if (state._replayInFlight) {
        if (!state._applyingReplay) state._replayAgain = true;
        return state._replayPromise;
    }
    state._replayInFlight = true;
    state._replayBuffer = state._replayBuffer || [];
    const since = state.lastSeq || 0;
    const initial = !state.historyLoaded && !since;
    const beforeInitial = initial ? new Set(state.messages) : null;
    let generation = state._timelineGeneration || 0;
    const epoch = state.viewEpoch, host = state.viewHost;
    const current = () => agentTuiState.get(nodeId) === state && state.viewEpoch === epoch
        && generation === (state._timelineGeneration || 0) && !state.closing;
    const base = `/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/events`;
    const url = initial ? `${base}/page?host=${encodeURIComponent(host)}&turns=5`
        : `${base}?since=${since}&host=${encodeURIComponent(host)}`;
    let succeeded = false;
    const prefetch = state._historyPrefetch;
    state._historyPrefetch = null;
    const usablePrefetch = initial && prefetch && prefetch.projectId === projectId && prefetch.host === host
        && prefetch.epoch === epoch && prefetch.generation === generation;
    const request = usablePrefetch ? prefetch.promise.then(data => data || agentReadHistoryResponse(url)) : agentReadHistoryResponse(url);
    const replay = request.then(data => {
        if (initial && (typeof data.has_more !== 'boolean' || (data.has_more && typeof data.cursor !== 'string'))) {
            throw new Error('Invalid Agent history cursor');
        }
        return data;
    }).then(d => {
        if (!current()) return;
        state.eventsError = '';
        let optimistic = [];
        if (initial) {
            // A prompt may be submitted after the server took the snapshot. Its
            // user_prompt is persisted but not broadcast, so keep local rows.
            optimistic = state.messages.filter(message => !beforeInitial.has(message)
                || (message.request_id && message.delivery && message.delivery !== 'accepted'));
            state.messages = [];
            state.toolActivities.clear();
            resetAgentTimeline(state);
            generation = state._timelineGeneration;
            state.historyCursor = d.cursor || null;
            state.historyHasMore = d.has_more;
            state.historyLoaded = true;
        }
        state._applyingReplay = true;
        state._batchHistory = true;
        try {
            for (const rec of d.events) {
                const ev = rec && rec.event;
                if (!ev || (ev.host || rec.host || 'pi') !== host) continue;
                const s = Number(rec.seq || ev.seq || 0);
                if (s && s > (state.lastSeq || 0)) {
                    state.lastSeq = s;
                    const recordedAt = Number.isFinite(rec.ts) ? agentMessageTimestamp(rec.ts * 1000) : undefined;
                    applyAgentEventBody(nodeId, ev, recordedAt);
                }
            }
            state.lastSeq = Math.max(state.lastSeq || 0, Number(d.last_seq) || 0);
            for (const message of optimistic) {
                const index = message.request_id ? state.messages.findIndex(row => row.request_id === message.request_id) : -1;
                if (index < 0) {
                    agentAppendMessage(state, message);
                } else {
                    const canonical = state.messages[index];
                    Object.assign(message, canonical);
                    state.messages[index] = message;
                    const entry = state.timelineMessages.get(canonical);
                    if (entry) { entry.message = message; state.timelineMessages.set(message, entry); }
                }
            }
        } finally { state._applyingReplay = false; state._batchHistory = false; }
        succeeded = true;
        state.historyBlocked = false;
        state.historyRetry = '';
    }).catch(e => {
        if (!current()) return;
        state.eventsError = `读取宿主历史失败：${e.message || e}`;
        state.historyBlocked = true;
        state.historyRetry = 'replay';
        console.warn('[agent-tui] 事件回放请求失败', e);
    }).finally(() => {
        if (!current()) return;
        state._replayInFlight = false;
        const replayAgain = state._replayAgain;
        state._replayAgain = false;
        // Catch up from the snapshot high-water before consuming later live events.
        // Otherwise a buffered higher seq can hide an unbroadcast user prompt.
        if (succeeded && replayAgain) {
            renderAgentNode(nodeId);
            return replayAgentEvents(nodeId, state);
        }
        if (succeeded) {
            const buf = state._replayBuffer || [];
            state._replayBuffer = [];
            buf.sort((a, b) => (Number(a.seq) || 0) - (Number(b.seq) || 0));
            let gap = false;
            state._batchHistory = true;
            try {
                for (let i = 0; i < buf.length; i++) {
                    const ev = buf[i], s = Number(ev.seq) || 0;
                    if (s && s <= (state.lastSeq || 0)) continue;
                    // A persisted user_prompt is not broadcast. A higher buffered
                    // live seq cannot advance the cursor across that missing row.
                    if (s && s > (state.lastSeq || 0) + 1) {
                        state._replayBuffer = buf.slice(i);
                        gap = true;
                        break;
                    }
                    if (s) state.lastSeq = s;
                    applyAgentEventBody(nodeId, ev);
                }
            } finally { state._batchHistory = false; }
            if (gap) {
                const since = state.lastSeq || 0;
                if (state._historyGapSince === since) {
                    state.eventsError = '历史事件存在缺口，请重试历史加载。';
                    state.historyBlocked = true;
                    state.historyRetry = 'replay';
                } else {
                    state._historyGapSince = since;
                    renderAgentNode(nodeId);
                    return replayAgentEvents(nodeId, state);
                }
            } else {
                state._historyGapSince = null;
            }
        }
        renderAgentNode(nodeId);
    });
    state._replayPromise = replay;
    return replay;
}

// Older pages are complete turns. Reduce into an isolated view, then prepend
// its timeline without changing the active run or the existing anchor keys.
function loadOlderAgentEvents(nodeId, state) {
    if (!state.projectId || !state.historyHasMore || !state.historyCursor || state.historyLoadingOlder) return;
    state.historyLoadingOlder = true;
    const cursor = state.historyCursor, host = state.viewHost, epoch = state.viewEpoch;
    const generation = state._timelineGeneration || 0;
    const current = () => agentTuiState.get(nodeId) === state && state.viewEpoch === epoch
        && generation === (state._timelineGeneration || 0) && !state.closing && state.historyCursor === cursor;
    const base = `/api/projects/${encodeURIComponent(state.projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/events/page`;
    fetch(`${base}?host=${encodeURIComponent(host)}&turns=5&cursor=${encodeURIComponent(cursor)}`)
        .then(async r => {
            const d = await r.json();
            if (!r.ok) {
                const error = new Error(agentNodeRefsError(d, `HTTP ${r.status}`));
                error.status = r.status;
                throw error;
            }
            if (!Array.isArray(d.events) || typeof d.has_more !== 'boolean' || (d.has_more && typeof d.cursor !== 'string')) throw new Error('Invalid Agent history page');
            return d;
        }).then(d => {
            if (!current()) return;
            const older = { ...createAgentHostView(), activeHost: host, viewHost: host, viewEpoch: epoch,
                _applyingReplay: true, _batchHistory: true, _replayInFlight: true };
            agentTuiState.set(nodeId, older);
            try {
                for (const rec of d.events) {
                    const ev = rec && rec.event;
                    if (!ev || (ev.host || rec.host || 'pi') !== host) continue;
                    const recordedAt = Number.isFinite(rec.ts) ? agentMessageTimestamp(rec.ts * 1000) : undefined;
                    applyAgentEventBody(nodeId, ev, recordedAt);
                }
                agentTimelineSync(older);
            } finally { agentTuiState.set(nodeId, state); }
            // A receipt can be on a newer page than its original question. Keep its
            // newest data and stable anchor, but restore the original chronological slot.
            const moved = new Set();
            for (const entry of older.timeline || []) {
                const id = entry.message?.presentation?.id;
                const existing = id && state.messages.find(message => message.presentation?.id === id);
                if (!existing) continue;
                const currentEntry = state.timelineMessages.get(existing);
                const index = older.messages.indexOf(entry.message);
                older.messages[index] = existing;
                entry.message = existing;
                if (currentEntry) { entry.key = currentEntry.key; moved.add(entry); }
                state.messages.splice(state.messages.indexOf(existing), 1);
                state.timeline = state.timeline.filter(item => item !== currentEntry);
            }
            const prefix = `older:${++state.olderPageSerial}:`;
            for (const [id, activity] of older.toolActivities) {
                state.toolActivities.set(prefix + id, activity);
                state.timelineTools.set(prefix + id, activity);
            }
            for (const entry of older.timeline || []) {
                if (!moved.has(entry)) entry.key = prefix + entry.key;
                if (entry.kind === 'tools') entry.items.forEach(item => { item.toolCallId = prefix + item.toolCallId; });
                else if (entry.message) state.timelineMessages.set(entry.message, entry);
            }
            const duplicateCards = new Set(older.messages.filter(message => message.completion?.turnId
                && state.messages.some(existing => existing.completion?.turnId === message.completion.turnId)));
            state.messages.unshift(...older.messages.filter(message => !duplicateCards.has(message)));
            state.timeline.unshift(...(older.timeline || []).filter(entry => !duplicateCards.has(entry.message)));
            state.historyCursor = d.cursor || null;
            state.historyHasMore = d.has_more;
            state.eventsError = '';
            state.historyRetry = '';
        }).catch(e => {
            if (!current()) return;
            state.eventsError = e.status === 409 ? '历史已变化，重试将重新加载最近消息。' : `读取更早历史失败：${e.message || e}`;
            state.historyRetry = e.status === 409 ? 'reload' : 'older';
        }).finally(() => {
            if (agentTuiState.get(nodeId) !== state || state.viewEpoch !== epoch
                || generation !== (state._timelineGeneration || 0) || state.closing) return;
            state.historyLoadingOlder = false;
            renderAgentNode(nodeId);
        });
}

function handleAgentEvent(nodeId, event) {
    const state = getAgentTuiState(nodeId);
    if (agentIsReadonly(state) || (event.host || 'pi') !== state.viewHost) return;

    // #318 票 C：seq 去重（live/回放共用一条链）；回放在途先缓冲防丢缺口。
    const seq = Number(event.seq);
    if (Number.isFinite(seq)) {
        if (state._replayInFlight || state.historyBlocked) {
            state._replayBuffer = state._replayBuffer || [];
            state._replayBuffer.push(event);
            return;
        }
        if (seq <= (state.lastSeq || 0)) return;
        if (state.historyLoaded && state.projectId && seq > (state.lastSeq || 0) + 1) {
            state._replayBuffer.push(event);
            replayAgentEvents(nodeId, state);
            return;
        }
        state.lastSeq = seq;
    }
    applyAgentEventBody(nodeId, event);
}

// A continuing text delta changes only the streaming row. Keeping the rest of the
// DOM mounted avoids reparsing long history and preserves native scrolling/focus.
function updateAgentStreamingText(state, message) {
    const binding = state.scrollBinding;
    const entry = state.timelineMessages && state.timelineMessages.get(message);
    if (!binding || binding.body.isConnected === false || !entry) return false;
    const row = binding.body.querySelector(`[data-agent-entry-key="${entry.key}"]`);
    const text = row && row.querySelector('.agent-tui-msg-streaming .agent-tui-msg-text');
    if (!text) return false;
    text.innerHTML = renderAgentAssistantHtml(message.text);
    if (state.scrollState && !state.scrollState.atBottom) state.scrollState.unread = true;
    updateAgentBackLatest(state);
    scheduleAgentScrollLayout(state);
    return true;
}

function applyAgentEventBody(nodeId, event, recordedAt) {
    const state = getAgentTuiState(nodeId);
    // Older-page reducers have no projectId; read-only hosts cannot publish active canvas state.
    if (state.projectId && !agentIsReadonly(state) && (event.host || 'pi') === state.activeHost
        && (event.type === 'confirm' || event.type === 'confirm_resolved')) {
        window.canvasAgentActivity?.consumeConfirmation({project_id: state.projectId, node_id: nodeId,
            host: state.activeHost, event});
    }
    if (agentNodeRefsApplyEvent(nodeId, event, recordedAt)) return;
    const eventType = event.type;
    let streamingText = null;

    switch (eventType) {
        case 'plan':
        case 'question':
        case 'diff': {
            const p = event.presentation;
            if (!p || p.schema !== 1 || p.kind !== eventType || typeof p.id !== 'string' || !p.id || !p.data) break;
            const row = state.messages.find(message => message.presentation?.id === p.id);
            const deliveryError = event.delivery_error ? agentNodeRefsError({ detail: event.delivery_error }, tr('smart.agentPresentDeliveryUnconfirmed')) : '';
            if (row) {
                row.presentationVersion = (row.presentationVersion || 0) + 1;
                row.presentation = p;
                if (deliveryError) row.presentationDeliveryError = deliveryError;
                if (p.state === 'answered' || p.state === 'expired') {
                    row.presentationError = '';
                    row.presentationSending = false;
                }
            } else {
                finalizeAgentStreamingTurn(state);
                agentAppendMessage(state, { role: 'presentation', presentation: p, presentationReadonly: Boolean(state._applyingReplay),
                    presentationThread: agentPresentationThread(nodeId, state), presentationProject: agentProjectId(),
                    presentationCanvas: agentViewCanvasId(), presentationDeliveryError: deliveryError });
            }
            break;
        }
        case 'user_prompt':
            // user_prompt 仅回放，live 不广播，避免乐观用户消息产生回声。
            agentAppendMessage(state, { role: 'user', text: String(event.text || ''),
                ...(recordedAt !== undefined ? { timestamp: recordedAt } : {}) });
            break;
        case 'session':
            state.status = event.status === 'running' || event.status === 'stopped' || event.status === 'aborted' ? 'idle' : (event.status || 'idle');
            if (event.effort_state) state.effortState = event.effort_state;
            if (['idle', 'running', 'stopped', 'crashed', 'aborted'].includes(event.status)) {
                state.stopPending = false;
                state.stopStatusError = '';
                state.queuedInputs.clear();
            }
            state.retryCount = 0;
            state.reconnectStopped = false;
            state.lastError = '';
            if (state.reconnectTimer) {
                clearTimeout(state.reconnectTimer);
                state.reconnectTimer = null;
            }
            replayAgentEvents(nodeId, state);
            break;
        case 'agent_start':
            finalizeAgentStreamingTurn(state);
            agentTimelineSync(state);
            if (state.timeline.length) state.timeline[state.timeline.length - 1].sealed = true;
            state._turnTextMessage = null;
            state._turnFailed = false;
            state._aborted = false;
            state.status = 'busy';
            state._turnSeq = (state._turnSeq || 0) + 1;
            break;
        case 'agent_end':
            if (event.willRetry !== true) {
                state.stopStatusError = '';
                state.stopPending = false;
                // Steering belongs to the completed turn; follow-ups may await later turns.
                for (const id of state.queuedInputs) {
                    if (state.messages.some(message => (message.queue_id || message.request_id) === id && message.input_type === 'steer')) state.queuedInputs.delete(id);
                }
            }
            finalizeAgentStreamingTurn(state);
            if (event.willRetry !== true && !state._applyingReplay && !state._turnFailed && !state._aborted && !['crashed', 'failed', 'error'].includes(state.status)) agentMarkBackgroundCompletion();
            state.status = event.willRetry === true ? 'retrying' : 'idle';
            agentCompletionBridge()?.complete(nodeId, state, event);
            break;
        case 'turn_start':
            finalizeAgentStreamingTurn(state);
            agentTimelineSync(state);
            if (state.timeline.length) state.timeline[state.timeline.length - 1].sealed = true;
            state._turnTextMessage = null;
            state.status = 'busy';
            break;
        case 'turn_end': {
            if (!state.stopPending && !['busy', 'retrying'].includes(state.status)) state.status = 'idle';
            const msg = event.message || {};
            if (msg.stopReason === 'aborted') state._aborted = true;
            const content = msg.content || [];
            const text = content
                .filter(c => c && c.type === 'text')
                .map(c => c.text || '')
                .join('');
            // 轮次结束必须定型，避免下一轮增量续写上一轮回复（#222）。
            const lastMessage = state._turnTextMessage || state.messages[state.messages.length - 1];
            const streamedThisTurn = Boolean(state._turnTextMessage || (lastMessage && lastMessage.role === 'assistant' && lastMessage.streaming));
            const nativeTime = agentMessageTimestamp(msg.timestamp);
            const timestamp = nativeTime ?? recordedAt;
            finalizeAgentStreamingTurn(state);
            if (streamedThisTurn) {
                if (text && !lastMessage.text) lastMessage.text = text;
                const knownTime = nativeTime ?? lastMessage.timestamp ?? recordedAt;
                if (knownTime !== undefined) lastMessage.timestamp = knownTime;
            } else if (text) {
                agentAppendMessage(state, { role: 'assistant', text,
                    ...(timestamp !== undefined ? { timestamp } : {}) });
            }
            // Pi 错误可只有 errorMessage 而没有 content，仍须显示原因（#288）。
            if (msg.stopReason === 'error' || msg.errorMessage) {
                state._turnFailed = true;
                const em = String(msg.errorMessage || '未知错误').slice(0, 300);
                agentAppendMessage(state, { role: 'assistant', text:
                    '⚠️ **模型错误（本轮无回复）**：' + em +
                    '\n\n处置：模型下拉换一个再试；百炼报 quota → 控制台充值或关掉“仅用免费额度”；'
                    + '报 model_not_found → 当前账号无此模型，改选其它项。', diagnostic: true });
            }
            break;
        }
        case 'message_update': {
            const ame = event.assistantMessageEvent || {};
            if (ame.type === 'text_delta') {
                const delta = ame.delta || '';
                archiveAgentThinking(state);
                const last = state.messages[state.messages.length - 1];
                const nativeTime = agentMessageTimestamp(event.message?.timestamp);
                const timestamp = nativeTime ?? recordedAt;
                if (last && last.role === 'assistant' && last.streaming) {
                    last.text += delta;
                    const knownTime = nativeTime ?? last.timestamp ?? recordedAt;
                    if (knownTime === last.timestamp) streamingText = last;
                    if (knownTime !== undefined) last.timestamp = knownTime;
                } else {
                    agentAppendMessage(state, { role: 'assistant', text: delta, streaming: true,
                        ...(timestamp !== undefined ? { timestamp } : {}) });
                }
                state._turnTextMessage = state.messages[state.messages.length - 1];
            } else if (ame.type === 'thinking_delta') {
                // Thought boundaries must not move behind later text deltas.
                const textMessage = state.messages[state.messages.length - 1];
                if (textMessage) delete textMessage.streaming;
                if (!state.thinking) state.thinking = [];
                const delta = ame.delta || '';
                const last = state.thinking[state.thinking.length - 1];
                if (last && last.streaming) {
                    last.text += delta;
                } else {
                    const thought = { text: delta, streaming: true };
                    state.thinking.push(thought);
                    agentTimelineMessage(state, thought, 'thinking');
                }
            }
            break;
        }
        case 'tool_execution_start': {
            const toolName = event.toolName || '';
            const toolCallId = event.toolCallId || '';
            if (state.toolActivities.has(toolCallId)) break;
            finalizeAgentStreamingTurn(state);
            agentTimelineSync(state);
            state.toolActivities.set(toolCallId, {
                name: toolName,
                status: 'running',
                args: event.args,
                result: null,
                isError: false,
                turn: state._turnSeq || 0,
                duration_ms: null,
            });
            agentTimelineTool(state, toolCallId, state.toolActivities.get(toolCallId));
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
                activity.duration_ms = agentDurationKnown(event.duration_ms) ? event.duration_ms : null;
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
                state._turnFailed = true;
                agentAppendMessage(state, { role: 'system', text: 'Agent 自动重试失败' });
            }
            break;
        case 'process_exited':
            state.stopPending = false;
            state.queuedInputs.clear();
            state._turnFailed = true;
            state.status = 'crashed';
            finalizeAgentStreamingTurn(state);
            break;
        case 'confirm': {
            state.pendingConfirms.set(event.confirm_id, event);
            state.confirm = event;
            state.status = 'waiting_confirm';
            // 模态结果须仍匹配待确认ID；关闭后仍可内联确认，两路共用单次发送防双发（#357）。
            if (!agentIsReadonly(state) && !state._applyingReplay && window.ffDialog && state.confirmModalShown !== event.confirm_id) {
                state.confirmModalShown = event.confirm_id;
                // Restoring a pending ID must not revive a preempted dialog's callback (#487).
                const modalOwner = {};
                state.confirmModalOwner = modalOwner;
                window.ffDialog.ask({
                    title: '确认 Agent 操作',
                    message: event.message || '请确认操作',
                    scrollMessage: event.tool === 'plan.apply' || event.tool === 'generate.request_batch',
                    confirmText: '批准',
                    cancelText: '取消',
                    danger: true,
                }).then(ok => {
                    if (state.confirmModalOwner === modalOwner && state.confirm && state.confirm.confirm_id === event.confirm_id) {
                        sendAgentConfirm(nodeId, state.projectId, !!ok);
                    }
                });
            }
            break;
        }
        case 'confirm_resolved': {
            // 服务端终结时静默关闭模态，不能把撤卡误当人工拒绝（#389）。
            if (agentResolveConfirmation(state, event.confirm_id)) {
                if (!state._applyingReplay && window.ffDialog && window.ffDialog.close) window.ffDialog.close();
                const outcome = event.approved ? '已批准'
                    : (event.reason === 'timeout' ? '已超时' : '已拒绝');
                agentAppendMessage(state, { role: 'system',
                    text: `Agent 操作确认${outcome}：${event.tool || ''}` });
            }
            break;
        }
        case 'response':
            break;
        case 'error': {
            // Spawn failures arrive without assistant content and must remain visible and retryable (#280).
            state.status = 'failed';
            state._turnFailed = true;
            const errMsg = String(event.message || 'Agent 会话启动失败');
            if (state.lastError !== errMsg) {
                // 后端每次重连失败都会重发同一原因，历史区不重复堆叠相同警告。
                state.lastError = errMsg;
                agentAppendMessage(state, { role: 'system', text: `⚠️ ${errMsg}` });
            }
            break;
        }
        default:
            break;
    }

    if (state._applyingReplay || !streamingText || !updateAgentStreamingText(state, streamingText)) renderAgentNode(nodeId);
}

function sendAgentPrompt(nodeId, projectId, message, images = [], options = {}) {
    if (agentIsReadonly(getAgentTuiState(nodeId))) return;
    return sendAgentCommand(nodeId, projectId, 'prompt', {
        nodeRefsSubmission: agentNodeRefsPrepare(nodeId, 'prompt', message, images, options),
    });
}

// 原文件流式上传后，图片/视频/音频仍使用现有预处理。
function readAsDataURL(file){
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error || new Error('FileReader failed'));
        reader.readAsDataURL(file);
    });
}

async function convertAgentMediaAttachment(file) {
    const mime = (file.type || '').toLowerCase();
    const isAudio = mime.startsWith('audio/');
    const isVideo = mime.startsWith('video/');
    if (!isAudio && !isVideo) throw new Error('only video/audio supported');
    const fd = new FormData();
    fd.append('file', file);
    const url = isAudio ? '/api/chat-media/audio-card' : '/api/chat-media/video-contact-sheet';
    const resp = await fetch(url, { method: 'POST', body: fd });
    if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { detail = (await resp.json()).detail || detail; } catch (e) { /* #348: 非 JSON 错误体，detail 回落 HTTP 状态码，随后 throw 由调用方提示 */ }
        throw new Error(detail);
    }
    return resp.json();
}

async function handleAgentAttachments(nodeId, projectId, files) {
    const state = getAgentTuiState(nodeId);
    if (agentIsReadonly(state)) return;
    if (!Array.isArray(state.pendingImages)) state.pendingImages = [];
    for (const file of files) {
        const name = file.name || 'attachment';
        try {
            const mime = (file.type || '').toLowerCase();
            state.attachWorking = name;
            renderAgentNode(nodeId);
            const url = `/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/attachments`;
            const response = await fetch(url, { method: 'POST', headers: {
                'Content-Type': file.type || 'application/octet-stream', 'X-Attachment-Name': encodeURIComponent(name),
            }, body: file });
            if (!response.ok) {
                const body = await response.json();
                if (response.status === 413 && body.detail && body.detail.max_bytes !== undefined) {
                    agentAppendMessage(state, { role: 'system', text: tr('smart.agentAttachTooBig')
                        .replace('{name}', name).replace('{size}', String(file.size)).replace('{max}', String(body.detail.max_bytes)) });
                    continue;
                }
                throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
            }
            const uploaded = await response.json();
            if (mime.startsWith('image/')) {
                const dataUrl = await readAsDataURL(file);
                state.pendingImages.push({ dataUrl, name, caption: '', attachmentId: uploaded.id });
            } else if (mime.startsWith('video/')) {
                const body = await convertAgentMediaAttachment(file);
                const ts = body.frames_at_s || [];
                const range = ts.length ? `${ts[0]}s–${ts[ts.length - 1]}s` : '';
                state.pendingImages.push({ dataUrl: body.data_url, name, attachmentId: uploaded.id,
                    caption: tr('smart.agentAttachVideoReady')
                        .replace('{name}', name).replace('{frames}', String(ts.length)).replace('{range}', range) });
            } else if (mime.startsWith('audio/')) {
                const body = await convertAgentMediaAttachment(file);
                state.pendingImages.push({ dataUrl: body.data_url, name, attachmentId: uploaded.id,
                    caption: tr('smart.agentAttachAudioReady').replace('{name}', name) });
            } else {
                state.pendingImages.push({ name, attachmentId: uploaded.id });
            }
        } catch (e) {
            console.error('Failed to prepare attachment:', name, e);
            agentAppendMessage(state, { role: 'system',
                text: tr('smart.agentAttachFailed').replace('{name}', name).replace('{err}', String(e.message || e)) });
        } finally {
            if (state.attachWorking === name) state.attachWorking = ''; // #317：无论成败清除进度
        }
    }
    renderAgentNode(nodeId);
}

function sendAgentCommand(nodeId, projectId, command, options = {}) {
    if (agentIsReadonly(getAgentTuiState(nodeId))) return;
    if (options.nodeRefsSubmission) return agentNodeRefsTransport(nodeId, projectId, options.nodeRefsSubmission);
    const state = getAgentTuiState(nodeId);
    const { body, wsPayload, optimistic, onSuccess, onFailure } = options;
    const url = `/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/${command}`;
    // WebSocket not connected, use HTTP API
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
        const fetchOpts = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
        if (body !== undefined) fetchOpts.body = JSON.stringify(body);
        // #221：这里不能直接 return（不跑本地状态的话，WS 断开时 abort 后按钮永远
        // 停在「运行中」），但也不能 fire-and-forget 地当成功——issue 备注原文
        // 「别修成『失败也显示已停止』」。拆三段：optimistic 同步复位本地状态（它代的是
        // 用户意图，与走哪条通道无关），请求真送达才写成功文案，没送达写警告。
        if (optimistic) optimistic(state);
        renderAgentNode(nodeId);
        fetch(url, fetchOpts).then(async response => {
            if (!response.ok) {
                // #348: HTTP 错误以前被当成功（.then 不检查 res.ok）→ 按钮假成功。拆出原因交给 onFailure。
                let detail = `HTTP ${response.status}`;
                try { detail = String((await response.json()).detail || detail); } catch (_e) { /* #348: 非 JSON 错误体，detail 保留 HTTP 状态码 */ }
                const error = new Error(`agent ${command}: ${detail}`);
                console.error(`Failed to send agent ${command}:`, error);
                if (onFailure) onFailure(state, error);
                renderAgentNode(nodeId);
                return;
            }
            if (onSuccess) onSuccess(state);
            renderAgentNode(nodeId);
        }).catch(e => {
            console.error(`Failed to send agent ${command}:`, e);
            if (onFailure) onFailure(state, e);
            renderAgentNode(nodeId);
        });
        return;
    }
    try {
        state.socket.send(JSON.stringify(wsPayload));
    } catch (error) {
        console.error(`Failed to send agent ${command}:`, error);
        if (onFailure) onFailure(state, error);
        renderAgentNode(nodeId);
        return;
    }
    if (optimistic) optimistic(state);
    if (onSuccess) onSuccess(state);
    renderAgentNode(nodeId);
}

function sendAgentSteer(nodeId, projectId, message, attachments = []) {
    if (agentIsReadonly(getAgentTuiState(nodeId))) return;
    return sendAgentCommand(nodeId, projectId, 'steer', {
        nodeRefsSubmission: agentNodeRefsPrepare(nodeId, 'steer', message, attachments),
    });
}

function sendAgentFollowUp(nodeId, projectId, message, attachments = [], options = {}) {
    if (agentIsReadonly(getAgentTuiState(nodeId))) return;
    return sendAgentCommand(nodeId, projectId, 'follow_up', {
        nodeRefsSubmission: agentNodeRefsPrepare(nodeId, 'follow_up', message, attachments, options),
    });
}

async function pollAgentStopStatus(nodeId, projectId, state, epoch) {
    if (agentTuiState.get(nodeId) !== state || state.viewEpoch !== epoch || !state.stopPending || state.closing) return;
    try {
        const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/status`);
        const data = await response.json();
        if (!response.ok) throw new Error(agentNodeRefsError(data, `HTTP ${response.status}`));
        if (typeof data.status !== 'string' || data.host !== state.activeHost) throw new Error('宿主停止状态响应无效');
        if (agentTuiState.get(nodeId) !== state || state.viewEpoch !== epoch || state.closing) return;
        if (data.effort_state) state.effortState = data.effort_state;
        state.stopStatusError = '';
        const idle = ['idle', 'running'].includes(data.status) && !state.queuedInputs.size && !state.confirm && state.status !== 'retrying';
        if (idle || ['stopped', 'crashed', 'aborted'].includes(data.status)) {
            state.stopPending = false;
            state.queuedInputs.clear();
            state.status = ['running', 'stopped', 'aborted'].includes(data.status) ? 'idle' : data.status;
        } else state.stopStatusTimer = setTimeout(() => pollAgentStopStatus(nodeId, projectId, state, epoch), 750);
    } catch (error) {
        if (agentTuiState.get(nodeId) !== state || state.viewEpoch !== epoch || state.closing) return;
        state.stopStatusError = `停止尚未确认：${error.message || error}`;
    }
    renderAgentNode(nodeId);
}
function sendAgentAbort(nodeId, projectId) {
    sendAgentCommand(nodeId, projectId, 'abort', {
        wsPayload: { type: 'abort' },
        // Receipt confirms delivery only. Parameter changes wait for terminal host feedback.
        optimistic: (state) => { state.stopPending = true; state.status = 'stopping'; state._aborted = true; },
        onSuccess: (state) => {
            agentAppendMessage(state, { role: 'system', text: '⏹ 停止请求已发送，等待宿主结束' });
            pollAgentStopStatus(nodeId, projectId, state, state.viewEpoch);
        },
        onFailure: (state) => agentAppendMessage(state, { role: 'system', text: '⚠️ 停止请求未送达，agent 可能仍在生成' }),
    });
}

// #392：四个人工设置共享节点级顺序，跨设置的旧完成也不能重建新 UI/清新会话。
// token 只属于当前 TUI 生命周期；cleanup 后同 id 的新条目不继承在途响应。
function agentSettingsHistoryFingerprint(state) {
    // #413: read-only completion refreshes must not keep an old branch alive; new rows/turn identities and host activity still forbid a reset.
    const messages = state.messages.map(message => message.completion
        ? {...message, completion: {turnId: message.completion.turnId}} : message);
    return JSON.stringify([messages, [...state.toolActivities], state.thinking || [],
        state._turnSeq || 0, state.lastSeq || 0, state._replayBuffer || []]);
}

function beginAgentSettingsRequest(nodeId, resetHistory = false) {
    const state = getAgentTuiState(nodeId);
    if (state.settingsPending) state.settingsPending = false;
    state.settingsRequestSeq = (state.settingsRequestSeq || 0) + 1;
    return {
        state, seq: state.settingsRequestSeq, viewEpoch: state.viewEpoch,
        hadNode: canvasState.nodes.some(n => n.id === nodeId),
        beforeNode: JSON.parse(JSON.stringify(canvasState.nodes.find(n => n.id === nodeId) || null)),
        history: resetHistory ? agentSettingsHistoryFingerprint(state) : null,
    };
}

function resetAgentSettingsHistory(request) {
    const state = request.state;
    // 回包前有新消息、工具、D2 思考/轮次或缓冲事件时保留新活动。
    if (request.viewEpoch !== state.viewEpoch || request.history !== agentSettingsHistoryFingerprint(state)) return;
    state.messages = [];
    state.toolActivities.clear();
    // #408: a confirmed branch boundary also invalidates display and replay state.
    resetAgentTimeline(state);
    state.lastSeq = 0;
    state._replayBuffer = [];
    state._replayInFlight = false;
    state.historyLoaded = false;
    state.historyCursor = null;
    state.historyHasMore = false;
    state.historyLoadingOlder = false;
    state.historyBlocked = false;
    state.historyRetry = '';
    state.eventsError = '';
}

function isCurrentAgentSettingsRequest(nodeId, request) {
    return agentTuiState.get(nodeId) === request.state && !request.state.closing
        && request.state.settingsRequestSeq === request.seq
        && (!request.hadNode || canvasState.nodes.some(n => n.id === nodeId));
}

function receiveAgentSettingsResponse(nodeId, request, data, fields = []) {
    if (!isCurrentAgentSettingsRequest(nodeId, request) || !data.canvas
        || typeof canvasCore.receiveAgentSettingsCanvas !== 'function') return false;
    return canvasCore.receiveAgentSettingsCanvas(data.canvas, request.beforeNode ? {
        before: request.beforeNode, fields, addedBranchId: data.branch?.id,
    } : null) === true && isCurrentAgentSettingsRequest(nodeId, request);
}

async function setAgentModel(nodeId, projectId, model) {
    const request = beginAgentSettingsRequest(nodeId);
    // #392：设置值只由权威画布接收器采用；不能让并行 autosave 保存未确认的值。
    renderAgentNode(nodeId);
    try {
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/${encodeURI(nodeId)}/model`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: model, user_edit: true }),
        });
        if (!response.ok) {
            let detail = `HTTP ${response.status}`;
            try { detail = String((await response.json()).detail || detail); } catch (_e) { /* #348: 非 JSON 错误体，detail 保留 HTTP 状态码 */ }
            throw new Error(detail);
        }
        const data = await response.json();
        if (!isCurrentAgentSettingsRequest(nodeId, request)) return;
        if (data.persisted === false) {
            // #285：纯会话成功是运行态事实，不是假造的节点/归属；画布存在时仍走统一接收。
            if (data.canvas) receiveAgentSettingsResponse(nodeId, request, data);
            if (!isCurrentAgentSettingsRequest(nodeId, request) || typeof data.model !== 'string') return;
            request.state.model = data.model;
            request.state.sessionModel = data.model;
        } else {
            if (!receiveAgentSettingsResponse(nodeId, request, data, ['model'])) return;
            delete request.state.sessionModel;
        }
        renderAgentNode(nodeId);
    } catch (e) {
        if (!isCurrentAgentSettingsRequest(nodeId, request)) return;
        console.error('Failed to set agent model:', e);
        window.ffToast?.error?.(`Agent 模型切换失败：${e?.message || e}`);
    }
}

async function setAgentAutonomy(nodeId, projectId, autonomy) {
    const request = beginAgentSettingsRequest(nodeId);
    renderAgentNode(nodeId);
    try {
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/${encodeURI(nodeId)}/autonomy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ autonomy: autonomy, user_edit: true }),
        });
        if (!response.ok) {
            let detail = `HTTP ${response.status}`;
            try { detail = String((await response.json()).detail || detail); } catch (_e) { /* #348: 非 JSON 错误体，detail 保留 HTTP 状态码 */ }
            throw new Error(detail);
        }
        const data = await response.json();
        if (!receiveAgentSettingsResponse(nodeId, request, data, ['autonomy'])) return;
        renderAgentNode(nodeId);
    } catch (e) {
        if (!isCurrentAgentSettingsRequest(nodeId, request)) return;
        console.error('Failed to set agent autonomy:', e);
        window.ffToast?.error?.(`Agent 自主级别切换失败：${e?.message || e}`);
    }
}

async function forkAgentSession(nodeId, projectId, entryId, name) {
    const request = beginAgentSettingsRequest(nodeId, true);
    try {
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/${encodeURI(nodeId)}/fork`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entry_id: entryId, name: name, user_edit: true }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(agentNodeRefsError(data, `HTTP ${response.status}`));
        if (!receiveAgentSettingsResponse(nodeId, request, data, ['branches', 'current_branch'])) {
            if (isCurrentAgentSettingsRequest(nodeId, request)) throw new Error('未收到可应用的分叉结果');
            return;
        }
        resetAgentSettingsHistory(request);
        renderAgentNode(nodeId);
    } catch (e) {
        if (!isCurrentAgentSettingsRequest(nodeId, request)) return;
        console.error('Failed to fork agent session:', e);
        // #348: 用户触发的 fork 失败——选择器已关，不提示用户会以为成功。
        window.ffToast.error(`分叉失败：${String(e)}`);
    }
}

async function switchAgentBranch(nodeId, projectId, branchId) {
    const request = beginAgentSettingsRequest(nodeId, true);
    try {
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/${encodeURI(nodeId)}/switch-branch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ branch_id: branchId, user_edit: true }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(agentNodeRefsError(data, `HTTP ${response.status}`));
        if (!receiveAgentSettingsResponse(nodeId, request, data, ['current_branch'])) {
            if (isCurrentAgentSettingsRequest(nodeId, request)) throw new Error('未收到可应用的分支设置');
            return;
        }
        if (request.beforeNode && branchId === request.beforeNode.current_branch) return;
        resetAgentSettingsHistory(request);
        renderAgentNode(nodeId);
    } catch (e) {
        if (!isCurrentAgentSettingsRequest(nodeId, request)) return;
        console.error('Failed to switch agent branch:', e);
        // #348: 用户触发的分支切换失败——不提示用户会以为已切换。
        window.ffToast.error(`切换分支失败：${String(e)}`);
    }
}

// The HTTP canvas receiver does not rebuild an existing docked element. Both
// ordinary settings and settings undo must adopt runtime evidence and refresh it.
function applyAgentHostSettingsAuthority(nodeId, data) {
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if (!node) throw new Error('权威设置对应的 Agent 节点已不存在');
    const state = getAgentTuiState(nodeId);
    agentSyncHostView(node, state);
    delete state.sessionModel;
    const activeView = agentIsReadonly(state) ? state.hostViews.get(state.activeHost) : state;
    if (data.effort_state) activeView.effortState = data.effort_state;
    renderAgentNode(nodeId);
}

// #421/#424: one atomic settings request, one authoritative canvas adoption.
async function setAgentHostSettings(nodeId, projectId, setting) {
    const state = getAgentTuiState(nodeId);
    if (agentParametersLocked(state)) return false;
    const request = beginAgentSettingsRequest(nodeId);
    state.settingsPending = true;
    state.settingsError = '';
    renderAgentNode(nodeId);
    try {
        const result = await fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/settings`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ host: setting.host, model: setting.model, effort: setting.effort, user_edit: true }),
        }).then(async response => {
            const data = await response.json();
            if (!response.ok) throw new Error(agentNodeRefsError(data, `HTTP ${response.status}`));
            if (!data || typeof data !== 'object') throw new Error('宿主设置响应无效');
            return { data };
        }).catch(error => {
            if (isCurrentAgentSettingsRequest(nodeId, request)) {
                state.settingsError = `Agent 参数设置失败：${error.message || error}`;
                window.ffToast.error(state.settingsError);
            }
            return { error };
        });
        if (result.error || !isCurrentAgentSettingsRequest(nodeId, request)) return false;
        const data = result.data;
        if (!receiveAgentSettingsResponse(nodeId, request, data, ['agentCli', 'agentHostSettings', 'model'])) {
            if (!isCurrentAgentSettingsRequest(nodeId, request)) return false;
            state.settingsError = '权威画布未接受设置，请刷新后重试';
            window.ffToast.error(state.settingsError);
            return false;
        }
        applyAgentHostSettingsAuthority(nodeId, data);
        return true;
    } finally {
        if (isCurrentAgentSettingsRequest(nodeId, request)) {
            state.settingsPending = false;
            renderAgentNode(nodeId);
        }
    }
}
async function agentSelectHost(nodeId, projectId, host) {
    const state = getAgentTuiState(nodeId);
    if (host === state.activeHost || agentParametersLocked(state)) return;
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if (!node) return;
    const sourceHost = state.activeHost;
    const sourceSeq = state.settingsRequestSeq;
    const binding = node.agentHostSessions && node.agentHostSessions[host];
    const known = Boolean(binding && (binding.extSessionId || binding.branches?.length));
    state.hostConfirmPending = true;
    renderAgentNode(nodeId);
    const ok = await window.ffDialog.ask({
        title: known ? `切回 ${host === 'pi' ? 'Pi' : 'Codex'} 会话？` : `为 ${host === 'pi' ? 'Pi' : 'Codex'} 新开独立会话？`,
        message: known ? '恢复此宿主保存的原生会话与节点参数，可继续对话。另一宿主的会话保留。'
            : '每个宿主各保留一条独立会话；历史、原生 ID 和上下文不会跨宿主复用。旧宿主会话保留，可只读查看或切回继续。',
        confirmText: known ? '恢复会话' : '确认新开', cancelText: '取消',
    });
    if (agentTuiState.get(nodeId) !== state || state.closing) return;
    state.hostConfirmPending = false;
    renderAgentNode(nodeId);
    if (!ok || agentParametersLocked(state)) return;
    const live = canvasState.nodes.find(n => n.id === nodeId);
    if (!live || agentNodeHost(live) !== sourceHost || state.settingsRequestSeq !== sourceSeq) return;
    return setAgentHostSettings(nodeId, projectId, { host, ...agentHostSetting(live, host) });
}
function closeAgentModelPicker(state) {
    const picker = state.modelPicker;
    if (!picker) return;
    state.modelPicker = null;
    document.removeEventListener('pointerdown', picker.outside, true);
    document.removeEventListener('keydown', picker.keydown, true);
    picker.menu.remove();
}
function agentCapabilitiesValid(data, host) {
    return data && data.host === host && ['available', 'unavailable'].includes(data.status)
        && Array.isArray(data.models) && data.models.every(m => m && typeof m.id === 'string' && typeof m.name === 'string')
        && data.defaults && (data.defaults.model === null || typeof data.defaults.model === 'string')
        && (data.defaults.effort === null || typeof data.defaults.effort === 'string')
        && data.selection && typeof data.selection.model === 'string' && Array.isArray(data.selection.efforts)
        && data.selection.efforts.every(e => typeof e === 'string')
        && (typeof data.selection.source === 'string' || (data.selection.source && typeof data.selection.source === 'object'
            && Object.values(data.selection.source).every(value => typeof value === 'string')));
}
async function loadAgentPickerCapabilities(nodeId, projectId, picker) {
    const state = getAgentTuiState(nodeId), seq = ++picker.seq;
    picker.loading = true;
    picker.error = '';
    renderAgentModelPicker(nodeId, projectId, picker);
    try {
        const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/capabilities?host=${encodeURIComponent(picker.host)}&model=${encodeURIComponent(picker.model)}`, { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(agentNodeRefsError(data, `HTTP ${response.status}`));
        if (!agentCapabilitiesValid(data, picker.host) || (data.status === 'available' && picker.model && data.selection.model !== picker.model)) throw new Error('宿主能力响应无效');
        if (state.modelPicker !== picker || seq !== picker.seq || state.closing) return;
        picker.capabilities = data;
        if (data.status !== 'available') picker.error = data.error?.message || '宿主能力不可用，不能确认模型或思考档位';
    } catch (error) {
        if (state.modelPicker !== picker || seq !== picker.seq || state.closing) return;
        picker.capabilities = null;
        picker.error = `读取宿主能力失败：${error.message || error}`;
    } finally {
        if (state.modelPicker === picker && seq === picker.seq && !state.closing) {
            picker.loading = false;
            renderAgentModelPicker(nodeId, projectId, picker);
        }
    }
}
function renderAgentModelPicker(nodeId, projectId, picker) {
    const cap = picker.capabilities;
    const models = [{ id: '', name: '跟随宿主' }, ...(cap?.models || [])];
    if (picker.model && !models.some(m => m.id === picker.model)) models.push({ id: picker.model, name: picker.model });
    const options = picker.kind === 'model' ? models : [{ id: '', name: '跟随宿主' },
        ...(cap?.selection.efforts || []).filter(e => e && e.toLowerCase() !== 'ultra').map(e => ({ id: e, name: e }))];
    const selected = picker.kind === 'model' ? picker.model : (picker.effort || '');
    picker.menu.innerHTML = picker.loading ? '<div role="status">正在读取宿主能力…</div>'
        : picker.error ? `<div role="alert">${escapeHtml(picker.error)} <button type="button" data-agent-picker-retry>重试</button></div>`
        : options.map((option, i) => `<button type="button" role="menuitemradio" aria-checked="${option.id === selected}" data-agent-picker-option="${i}">${escapeHtml(option.name)}</button>`).join('');
    picker.menu.querySelector('[data-agent-picker-retry]')?.addEventListener('click', () => loadAgentPickerCapabilities(nodeId, projectId, picker));
    picker.menu.querySelectorAll('[data-agent-picker-option]').forEach(button => button.addEventListener('click', () => {
        applyAgentModelPicker(nodeId, projectId, picker, options[Number(button.getAttribute('data-agent-picker-option'))].id);
    }));
    if (document.activeElement === picker.menu) picker.menu.querySelector('[aria-checked="true"]')?.focus();
}
function agentFloorEffort(current, supported) {
    const order = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh'];
    const index = order.indexOf(current.toLowerCase());
    if (index < 0) return supported[0];
    let floor, lowest;
    for (const value of supported) {
        const rank = order.indexOf(value.toLowerCase());
        if (rank < 0) continue;
        if (!lowest || rank < lowest.rank) lowest = { value, rank };
        if (rank <= index && (!floor || rank > floor.rank)) floor = { value, rank };
    }
    return floor?.value ?? lowest?.value ?? supported[0];
}
async function applyAgentModelPicker(nodeId, projectId, picker, value) {
    const state = getAgentTuiState(nodeId);
    if (state.modelPicker !== picker || picker.loading || picker.error || agentIsReadonly(state) || agentParametersLocked(state)) return;
    const selectionSeq = ++state.modelPickerSeq;
    closeAgentModelPicker(state);
    let model = picker.kind === 'model' ? value : picker.model;
    let effort = picker.kind === 'effort' ? (value || null) : picker.effort;
    if (model === picker.model && effort === picker.effort) return;
    if (picker.kind === 'model' && effort !== null) {
        const seq = state.settingsRequestSeq, epoch = state.viewEpoch;
        try {
            const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/capabilities?host=${encodeURIComponent(picker.host)}&model=${encodeURIComponent(model)}`, { cache: 'no-store' });
            const cap = await response.json();
            if (!response.ok || !agentCapabilitiesValid(cap, picker.host) || cap.status !== 'available' || (model && cap.selection.model !== model)) throw new Error('宿主能力不可用');
            const live = canvasState.nodes.find(n => n.id === nodeId);
            if (agentTuiState.get(nodeId) !== state || state.closing || state.viewEpoch !== epoch || state.settingsRequestSeq !== seq
                || state.modelPickerSeq !== selectionSeq || !live || agentNodeHost(live) !== picker.host
                || JSON.stringify(agentHostSetting(live, picker.host)) !== JSON.stringify({ model: picker.model, effort: picker.effort })) return;
            const efforts = cap.selection.efforts.filter(e => e && e.toLowerCase() !== 'ultra');
            if (!efforts.includes(effort)) {
                if (!efforts.length) throw new Error('新模型没有可用的思考档位');
                effort = agentFloorEffort(effort, efforts);
            }
        } catch (error) {
            if (agentTuiState.get(nodeId) === state && !state.closing && state.viewEpoch === epoch && state.settingsRequestSeq === seq
                && state.modelPickerSeq === selectionSeq) {
                state.modelPicker = picker;
                picker.error = `读取宿主能力失败：${error.message || error}`;
                document.body.appendChild(picker.menu);
                document.addEventListener('pointerdown', picker.outside, true);
                document.addEventListener('keydown', picker.keydown, true);
                renderAgentModelPicker(nodeId, projectId, picker);
                picker.menu.focus();
            }
            return;
        }
    }
    const changedEffort = effort !== picker.effort;
    if (await setAgentHostSettings(nodeId, projectId, { host: picker.host, model, effort }) && changedEffort && picker.kind === 'model') {
        state.effortNotice = `已改为 ${effort}`;
        renderAgentNode(nodeId);
        clearTimeout(state.effortNoticeTimer);
        state.effortNoticeTimer = setTimeout(() => { state.effortNotice = ''; renderAgentNode(nodeId); }, 3000);
    }
}
function openAgentModelPicker(nodeId, projectId, kind = 'model', anchor = null) {
    const state = getAgentTuiState(nodeId);
    if (agentIsReadonly(state) || agentParametersLocked(state)) return;
    if (state.modelPicker) closeAgentModelPicker(state);
    state.modelPickerSeq = (state.modelPickerSeq || 0) + 1;
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if (!node) return;
    const setting = agentHostSetting(node, state.viewHost), menu = document.createElement('div');
    menu.className = 'agent-tui-model-picker';
    menu.setAttribute('role', 'menu');
    menu.tabIndex = -1;
    anchor = anchor || Array.from(document.querySelectorAll('[data-agent-model-picker]')).find(el => el.getAttribute('data-agent-model-picker') === nodeId);
    const rect = anchor?.getBoundingClientRect();
    if (rect) { menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 228))}px`; menu.style.bottom = `${window.innerHeight - rect.top + 4}px`; }
    const picker = { projectId, host: state.viewHost, model: setting.model, effort: setting.effort, kind, menu, seq: 0, capabilities: null };
    picker.outside = event => { if (!menu.contains(event.target) && event.target !== anchor) closeAgentModelPicker(state); };
    picker.keydown = event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeAgentModelPicker(state); anchor?.focus(); return; }
        if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229
            && menu.contains(document.activeElement) && document.activeElement !== menu) {
            event.preventDefault(); event.stopPropagation(); document.activeElement.click(); return;
        }
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        const items = Array.from(menu.querySelectorAll('button'));
        if (!items.length) return;
        event.preventDefault(); event.stopPropagation();
        const index = items.indexOf(document.activeElement);
        const next = index < 0 ? (event.key === 'ArrowDown' ? 0 : items.length - 1)
            : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items[next].focus();
    };
    state.modelPicker = picker;
    document.body.appendChild(menu);
    document.addEventListener('pointerdown', picker.outside, true);
    document.addEventListener('keydown', picker.keydown, true);
    menu.focus();
    return loadAgentPickerCapabilities(nodeId, projectId, picker);
}

// #317：fork 选择器从两次 prompt() 换为画布自有 <dialog>（与 project-list.js showModal 同一体系）
function agentForkPickerDialog(messages, context) {
    return new Promise(resolve => {
        const dlg = document.createElement('dialog');
        dlg.className = 'agent-fork-dialog';
        const items = messages.map((m, i) => {
            const text = m.text.replace(/\s+/g, ' ');
            const radio = context ? 'required' : (i === messages.length - 1 ? 'checked' : '');
            const title = context ? ` title="${escapeAttr(text)}"` : '';
            return `<label><input type="radio" name="forkidx" value="${i}" ${radio}><span class="agent-fork-idx">${i + 1}.</span><span${title}>${escapeHtml(text.slice(0, 120))}</span></label>`;
        }).join('');
        const defaultName = context ? '' : escapeAttr(messages[messages.length - 1].text.replace(/\s+/g, ' ').slice(0, 30));
        const excerpt = text => escapeHtml(text.replace(/\s+/g, ' ').trim().slice(0, 120));
        const contextHtml = context ? `<p>萤火回复：${excerpt(context.reply.text)}<br>画布中的下一条用户消息：${excerpt(context.nextUser.text)}</p>
            <p>Pi 只能在选中的用户消息之前分叉，不能直接选择助手回复。要保留这条回复，请在 Pi 列表中确认并选择它之后的用户消息；无法确认时请取消。</p>` : '';
        dlg.innerHTML = `<h3>${context ? '在 Pi 中选择分叉点' : '从哪轮分叉？'}</h3>
            ${contextHtml}
            <form method="dialog">
                <div class="agent-fork-list">${items}</div>
                <input class="agent-fork-name" name="branchname" placeholder="分支名称（留空自动）" value="${defaultName}">
                <div class="agent-fork-actions">
                    <button value="cancel" formnovalidate>取消</button>
                    <button value="ok" class="primary">分叉</button>
                </div>
            </form>`;
        dlg.addEventListener('close', () => {
            const checked = dlg.querySelector('input[name="forkidx"]:checked');
            const pick = dlg.returnValue === 'ok' && checked ? {
                index: Number(checked.value), name: dlg.querySelector('input[name="branchname"]').value.trim(),
            } : null;
            dlg.remove();
            resolve(pick);
        });
        document.body.appendChild(dlg);
        try { dlg.showModal(); }
        catch (error) { dlg.remove(); throw error; }
    });
}

async function showForkPicker(nodeId, projectId, context) {
    const state = getAgentTuiState(nodeId);
    const initiallyDisabled = agentSessionForkDisabled(state);
    if (initiallyDisabled) { window.ffToast.error(`无法分叉：${initiallyDisabled}`); return; }
    const request = beginAgentSettingsRequest(nodeId);
    const stillEligible = () => {
        const state = request.state;
        let reason = state.viewEpoch !== request.viewEpoch ? '会话已切换' : agentSessionForkDisabled(state);
        if (!reason && context) reason = agentMessageForkDisabled(state, context.reply, agentMessageForkAnchors(state.messages).get(context.reply) === context.nextUser);
        if (reason) window.ffToast.error(`无法分叉：${reason}`);
        return !reason;
    };
    try {
        const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/fork-messages`);
        const data = await response.json();
        if (!isCurrentAgentSettingsRequest(nodeId, request) || !stillEligible()) return;
        if (!response.ok) throw new Error(agentNodeRefsError(data, `HTTP ${response.status}`));
        const messages = data.messages;
        if (!Array.isArray(messages) || messages.some(m => !m || typeof m.entryId !== 'string' || !m.entryId || typeof m.text !== 'string')) {
            throw new Error('Pi 分叉点响应无效');
        }
        if (!messages.length) {
            window.ffToast.error('Pi 没有可选的用户消息分叉点');
            return;
        }
        const picked = await agentForkPickerDialog(messages, context);
        if (!picked || !isCurrentAgentSettingsRequest(nodeId, request) || !stillEligible()) return;
        const message = messages[picked.index];
        if (!message) throw new Error('所选分叉点无效');
        await forkAgentSession(nodeId, projectId, message.entryId, picked.name);
    } catch (e) {
        if (!isCurrentAgentSettingsRequest(nodeId, request)) return;
        console.error('Failed to show fork picker:', e);
        window.ffToast.error(`打开分叉选择器失败：${String(e)}`);
    }
}

// #325 票 G：外部 CLI 接管。命令文本由服务端 snippet 端点生成（端口/基址取运行时值，
// #279 同源要求），前端只拉取+展示+复制，不自行拼接 URL。
async function fetchAgentCliSnippet(nodeId, projectId) {
    try {
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/external-cli-snippet?node=${encodeURIComponent(nodeId)}`);
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return await response.json();
    } catch (e) {
        console.error('Failed to get CLI snippet:', e);
        return null;
    }
}

function agentCliSnippetDialogHtml(data) {
    const rows = [
        ['codex', 'Codex CLI', data.codex_cmd],
        ['claude', 'Claude Code', data.claude_cmd],
    ].map(([key, label, cmd]) => `
        <div class="agent-cli-row">
            <div class="agent-cli-label">${escapeHtml(label)}</div>
            <textarea class="agent-cli-cmd agent-cli-${key}-cmd" readonly rows="2" spellcheck="false">${escapeHtml(String(cmd || ''))}</textarea>
            <button type="button" class="agent-tui-btn" data-agent-cli-copy="${key}">复制</button>
        </div>`).join('');
    return `<h3>由外部 CLI 接管本节点</h3>
        <div class="agent-cli-note">外部 CLI 经 MCP 连到本节点（与画布内 Agent 共享同一节点会话归属）。粘贴到终端执行：</div>
        ${rows}
        <form method="dialog" class="agent-fork-actions"><button value="close" class="primary">关闭</button></form>`;
}

async function showAgentCliTakeover(nodeId, projectId) {
    const data = await fetchAgentCliSnippet(nodeId, projectId);
    if (!data) return;
    const dlg = document.createElement('dialog');
    dlg.className = 'agent-cli-dialog';
    dlg.innerHTML = agentCliSnippetDialogHtml(data);
    dlg.addEventListener('click', event => {
        const btn = event.target && event.target.closest ? event.target.closest('[data-agent-cli-copy]') : null;
        if (!btn) return;
        const key = btn.getAttribute('data-agent-cli-copy');
        const cmd = key === 'codex' ? data.codex_cmd : data.claude_cmd;
        const done = () => { btn.textContent = '已复制'; setTimeout(() => { btn.textContent = '复制'; }, 1500); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(cmd).then(done, () => { fallbackCopy(dlg, key); done(); });
        } else {
            fallbackCopy(dlg, key);
            done();
        }
    });
    function fallbackCopy(dlg, key) {
        const ta = dlg.querySelector(`.agent-cli-${key}-cmd`);
        if (!ta) return;
        ta.removeAttribute('readonly');
        ta.select();
        try { document.execCommand('copy'); } catch (e) {
            // #348: 剪贴板双通道都失败（clipboard API 与 execCommand）；对话框仍开着，命令文本可手动全选复制。
            console.warn('[agent-tui] 复制 CLI 命令失败', e);
        }
        ta.setAttribute('readonly', '');
    }
    dlg.addEventListener('close', () => dlg.remove());
    document.body.appendChild(dlg);
    dlg.showModal();
}

// Independent batch gates coexist; accepting one must not discard another pending card (#487).
function agentResolveConfirmation(state, confirmId) {
    state.pendingConfirms.delete(confirmId);
    if (state.confirm?.confirm_id !== confirmId) return false;
    state.confirmModalOwner = null;
    state.confirm = Array.from(state.pendingConfirms.values()).at(-1) || null;
    if (state.confirm) state.status = 'waiting_confirm';
    else if (state.status === 'waiting_confirm') state.status = 'busy';
    return true;
}

function sendAgentConfirm(nodeId, projectId, approved) {
    const state = getAgentTuiState(nodeId);
    if (agentIsReadonly(state) || !state.confirm) return;
    const confirmation = state.confirm;
    const confirmId = confirmation.confirm_id;
    // Browser WebSockets cannot carry the project header required by batch decisions (#487).
    if (typeof confirmId === 'string' && confirmId.startsWith('batch-confirm-')) {
        if (confirmation.sending) return;
        confirmation.sending = true;
        const epoch = state.viewEpoch, host = state.viewHost, project = state.projectId;
        const owned = () => agentTuiState.get(nodeId) === state && !state.closing
            && state.viewEpoch === epoch && state.viewHost === host && state.projectId === project
            && !agentIsReadonly(state);
        const current = () => owned() && state.confirm === confirmation;
        return fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/confirm`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-Agent-Project': projectId },
            body: JSON.stringify({ confirm_id: confirmId, approved: approved }),
        }).then(async res => {
            let data;
            try { data = await res.json(); } catch (_) { data = null; }
            if (!res.ok) throw new Error(agentNodeRefsError(data, `HTTP ${res.status}`));
            if (data?.ok !== true || data.confirm_id !== confirmId || data.approved !== approved) throw new Error('确认响应无效');
            // A resolved/replaced gate, including the next cautious paid gate, owns its own UI.
            if (!current()) {
                if (owned()) state.pendingConfirms.delete(confirmId);
                return;
            }
            agentResolveConfirmation(state, confirmId);
            agentAppendMessage(state, { role: 'system', text: approved ? '✅ 已批准' : '❌ 已取消' });
            renderAgentNode(nodeId);
        }).catch(e => {
            console.error('Failed to send agent batch confirm:', e);
            if (!current()) return;
            agentAppendMessage(state, { role: 'system', text: `⚠️ 确认未获接受回执：${e.message || e}` });
            renderAgentNode(nodeId);
        }).finally(() => { delete confirmation.sending; });
    }
    agentResolveConfirmation(state, confirmId);
    if (!state.confirm) state.status = 'busy';

    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(JSON.stringify({
            type: 'confirm',
            confirm_id: confirmId,
            approved: approved,
        }));
    } else {
        fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm_id: confirmId, approved: approved }),
        }).then(res => {
            if (!res.ok) {
                console.warn(`[agent-tui] confirm HTTP fallback 未送达 (HTTP ${res.status} ${res.statusText}, confirm_id=${confirmId})，WS 可能未连接`);
            }
        }).catch(e => console.error('Failed to send agent confirm:', e));
    }

    const text = approved ? '✅ 已批准' : '❌ 已取消';
    agentAppendMessage(state, { role: 'system', text });
    renderAgentNode(nodeId);
}

// Get status light + last line preview HTML for agent node card
// #220：卡片状态行和节点正文原来各写一遇 switch，两处会各自漏 case——
// retrying / crashed 就是卡片侧漏的，结果重试中和已崩溃都显示成「空闲」。
// 状态文案只留一份表；confirm 覆盖只在卡片侧（正文另有 confirm 区），
// 跟原来一致，不进这张表。
const AGENT_STATUS_PRESENTATION = {
    busy: { statusClass: 'agent-tui-status-busy', statusText: '运行中' },
    starting: { statusClass: 'agent-tui-status-connecting', statusText: '启动中' },
    stopping: { statusClass: 'agent-tui-status-busy', statusText: '等待停止' },
    connecting: { statusClass: 'agent-tui-status-connecting', statusText: '连接中' },
    retrying: { statusClass: 'agent-tui-status-busy', statusText: '重试中' },
    error: { statusClass: 'agent-tui-status-error', statusText: '错误' },
    crashed: { statusClass: 'agent-tui-status-error', statusText: '已崩溃' },
    disconnected: { statusClass: 'agent-tui-status-disconnected', statusText: '已断开' },
    // #280：连接失败三态（启动失败可见 + 退避中 + 触顶停止）
    failed: { statusClass: 'agent-tui-status-error', statusText: '启动失败' },
    reconnecting: { statusClass: 'agent-tui-status-disconnected', statusText: '重连中' },
    reconnect_stopped: { statusClass: 'agent-tui-status-error', statusText: '重连停止' },
};
const AGENT_STATUS_IDLE = { statusClass: 'agent-tui-status-idle', statusText: '就绪' };

function agentStatusPresentation(state) {
    return AGENT_STATUS_PRESENTATION[state && state.status] || AGENT_STATUS_IDLE;
}

// #284：Agent 回复里的 MEDIA: 行渲染为图片缩略。
// 此前显示原始文本「MEDIA:/media/generated_image_*.png」且该 URL 404：
// StaticFiles 实际挂载 /output 与 /assets（main.py:1180-1182），生成目录
// OUTPUT_OUTPUT_DIR=ASSETS_DIR/output（main.py:1090）→ 可服务前缀 /assets/output/。
function normalizeAgentMediaUrl(raw){
    const file = String(raw || '').trim();
    if (!file) return '';
    if (/^(https?:|\/assets\/output\/|\/output\/)/i.test(file)) return file;
    if (/^\/media\//i.test(file)) return '/assets/output/' + file.replace(/^\/media\//i, '');
    const served = /[\\/](?:assets[\\/])?output[\\/]([^\\/]+)$/.exec(file);
    if (served && (/^([A-Za-z]:[\\/]|\/|\.)/.test(file) || /assets[\\/]output/i.test(file))) {
        // 绝对/磁盘路径里的 output/<file>（含 Windows D:\...\output\x.png）→ 可服务前缀。
        return '/assets/output/' + served[1];
    }
    if (/\//.test(file)) {
        const name = file.split(/[\\/]/).filter(Boolean).pop();
        return /^generated_image|^generated_video|^generated_audio|^frame_|^thumbnail_/.test(name)
            ? '/assets/output/' + name : file;
    }
    return '/assets/output/' + file;
}

function splitAgentMediaLines(text){
    // Keep Markdown paragraphs and fences intact; MEDIA: inside a fence is literal code.
    const segments = [];
    let lines = [];
    let fenced = false;
    const flush = () => {
        if (lines.some(line => line.trim())) segments.push({ text: lines.join('\n'), media: [] });
        lines = [];
    };
    for (const line of String(text || '').split('\n')) {
        const match = !fenced && /^\s*MEDIA:\s*(\S.*)$/.exec(line);
        if (match) {
            flush();
            segments.push({ text: '', media: match[1].split(/\s+/).map(t => t.replace(/^MEDIA:/i, '')).filter(Boolean) });
        } else {
            lines.push(line);
            if (/^```/.test(line)) fenced = !fenced;
        }
    }
    flush();
    return segments;
}

function agentMediaThumbsHtml(urls){
    return urls.map((raw) => {
        const src = normalizeAgentMediaUrl(raw);
        if (!src) return '';
        const safe = escapeAttr(src);
        return `<span class="agent-tui-media-thumb"><a href="${safe}" target="_blank" rel="noopener"><img src="${safe}" loading="lazy" alt="agent media" onerror="this.closest('.agent-tui-media-thumb').style.display='none'"></a></span>`;
    }).join('');
}

function renderAgentAssistantHtml(text){
    // 文本段走 renderMarkdown；MEDIA 段渲染缩略图（点开大图）。
    return splitAgentMediaLines(text)
        .map((seg) => renderMarkdown(seg.text) + agentMediaThumbsHtml(seg.media))
        .join('');
}

function agentCardStatusHtml(nodeId) {
    const state = agentTuiState.get(nodeId);
    if (!state) {
        return `<span class="agent-card-status ${AGENT_STATUS_IDLE.statusClass}" role="status">${AGENT_STATUS_IDLE.statusText}</span>`;
    }
    
    // Determine status class and text
    let { statusClass, statusText } = agentStatusPresentation(state);
    
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
    return `<span class="agent-card-status ${statusClass}" role="status">${statusText}</span>${previewHtml}`;
}

// #280：失败/重连横幅——此前 spawn 失败在画布上完全静默（节点一直「空闲」）。
// 失败三态（启动失败 / 重连中 / 重连停止）展示明确原因与可操作提示 + 手动「重试连接」。
function agentReconnectBannerHtml(state) {
    const status = state && state.status;
    if (status !== 'failed' && status !== 'reconnecting' && status !== 'reconnect_stopped') return '';
    const reason = state.lastError ? `（${escapeHtml(state.lastError)}）` : '';
    let text;
    if (status === 'failed') {
        const retrying = state.retryCount > 0 && state.retryCount < AGENT_RECONNECT_MAX_ATTEMPTS
            ? ` 正在自动重试：已连续失败 ${state.retryCount} 次，${Math.max(1, Math.round(agentReconnectDelayMs(state.retryCount) / 1000))} 秒后再次尝试。`
            : '';
        text = `Agent 启动失败${reason}。请检查智能体运行时是否安装，或设置 FIREFLY_PI_CLI 指向可用的 pi 可执行文件。${retrying}`;
    } else if (status === 'reconnecting') {
        const seconds = Math.max(1, Math.round(agentReconnectDelayMs(state.retryCount) / 1000));
        text = `连接中断，${seconds} 秒后自动重连（已连续失败 ${state.retryCount} 次）${reason}…`;
    } else {
        text = `自动重连已停止（连续失败 ${AGENT_RECONNECT_MAX_ATTEMPTS} 次）${reason}。请检查智能体运行时安装或设置 FIREFLY_PI_CLI 后手动重试。`;
    }
    return `<div class="agent-tui-error-banner" role="alert"><span class="agent-tui-error-banner-text">${text}</span><button type="button" class="agent-tui-btn agent-tui-btn-retry" data-agent-retry="1">重试连接</button></div>`;
}

function agentMessageForkAnchors(messages) {
    // Local neighbors provide context only. Pi's fork-messages exposes no timestamps or
    // assistant IDs; only an explicit selection from its native list can supply an ID.
    const anchors = new Map();
    let priorUser = false;
    const replies = [];
    for (const msg of messages) {
        if (msg.role === 'user') {
            const accepted = (msg.delivery === undefined || msg.delivery === 'accepted') && msg.text.trim();
            if (accepted && priorUser) for (const reply of replies) anchors.set(reply, msg);
            replies.length = 0;
            priorUser = accepted;
        } else if (msg.role === 'assistant' && priorUser) replies.push(msg);
    }
    return anchors;
}

function agentSessionForkDisabled(state) {
    if (agentIsReadonly(state)) return '只读会话不能分叉';
    if (state.viewHost !== 'pi') return '仅 Pi 宿主支持分叉';
    if (agentParametersLocked(state)) return '请等待当前操作结束后再分叉';
    return '';
}

function agentMessageForkDisabled(state, msg, hasAnchor) {
    const reason = agentSessionForkDisabled(state);
    if (reason) return reason;
    if (msg.streaming) return '请等待这条回复完成后再分叉';
    if (!msg.text.trim() || msg.diagnostic) return '这条消息没有可分叉的回复';
    if (!hasAnchor) return '缺少可确认的前后用户消息，无法从这条回复分叉';
    return '';
}

function agentMessageCopyDisabled(msg) {
    if (msg.streaming) return '请等待这条回复完成后再复制';
    if (!msg.text.trim()) return '没有可以复制的文本';
    if (!navigator.clipboard?.writeText) return '当前页面无法访问系统剪贴板，请选中文本手动复制';
    return '';
}

function agentMessageActionsHtml(state, entry, hasAnchor) {
    const button = (label, attr, reason, hint) => `<button type="button" ${attr}="${escapeAttr(entry.key)}" title="${escapeAttr(reason || hint)}"${reason ? ' disabled' : ''}>${label}</button>`;
    return `<div class="agent-tui-msg-actions">${button('复制', 'data-agent-msg-copy', agentMessageCopyDisabled(entry.message), '复制回复原文')}${button('从此分叉', 'data-agent-msg-fork', agentMessageForkDisabled(state, entry.message, hasAnchor), '打开 Pi 选择器，手动选择这条回复之后的用户消息')}</div>`;
}

function agentMessageBylineHtml(msg, date) {
    const author = msg.role === 'user' ? '你' : '<span class="agent-tui-msg-mark" aria-hidden="true">✳</span> 萤火';
    const time = date ? `<time datetime="${date.toISOString()}">${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}</time>` : '';
    return `<div class="agent-tui-msg-byline"><span>${author}</span>${time}</div>`;
}

function agentPresentationThread(nodeId, state) {
    const node = canvasState.nodes.find(item => item.id === nodeId);
    const session = node?.agentHostSessions?.[state.viewHost];
    // Runtime generation advances per Codex turn; only the thread/branch identifies the importer (#482).
    return JSON.stringify([state.viewHost, session?.extSessionId ?? node?.extSessionId ?? '', session?.current_branch ?? node?.current_branch ?? '']);
}

function agentPresentationOwner(nodeId, projectId, state, epoch, message, allowReadonly = false) {
    const project = agentProjectId(), host = state.viewHost;
    return (restoredView = false) => agentTuiState.get(nodeId) === state && (restoredView || state.viewEpoch === epoch) && !state.closing
        && (allowReadonly || (!agentIsReadonly(state) && message.presentationThread === agentPresentationThread(nodeId, state))) && state.viewHost === host
        && agentViewCanvasId() === projectId && agentProjectId() === project
        && message.presentationProject === project && message.presentationCanvas === projectId
        && canvasState.nodes.some(node => node.id === nodeId && isSmartAgentNode(node)
            && (allowReadonly || agentNodeHost(node) === host)) && state.messages.includes(message);
}

function agentPlanView(state, message) {
    const id = message.presentation.data.plan_id;
    if (!state.planPreviews.has(id)) state.planPreviews.set(id, { receipt: null, busy: '', attempted: false, error: '', notice: '', drafts: new Map(), serial: 0 });
    return state.planPreviews.get(id);
}

function agentPlanValidResult(result, committed = true) {
    return result && Array.isArray(result.groups) && Array.isArray(result.tasks) && Array.isArray(result.already_applied)
        && result.groups.every(group => typeof group?.group_id === 'string' && typeof group.section === 'string')
        && result.tasks.every(task => typeof task?.task_id === 'string' && typeof task.prompt_node === 'string' && typeof task.gen_node === 'string')
        && result.already_applied.every(id => typeof id === 'string')
        && (!committed || (typeof result.op_id === 'string' && result.op_id.length > 0
            && Array.isArray(result.touched_node_ids) && result.touched_node_ids.every(id => typeof id === 'string')));
}

function agentPlanValidApplication(application, generation) {
    if (!application || application.generation !== generation
        || !['ready', 'awaiting_confirmation', 'applied', 'unknown'].includes(application.state)) return false;
    const receipt = application.receipt;
    if (receipt !== null && (!receipt || !Number.isSafeInteger(receipt.generation) || receipt.generation < 1 || receipt.generation > generation
        || !['human', 'agent'].includes(receipt.applied_by) || !agentPlanValidResult(receipt.result))) return false;
    return (application.state !== 'applied' || receipt?.generation === generation)
        && (application.state !== 'awaiting_confirmation' || (typeof application.confirm_id === 'string' && application.confirm_id.length > 0));
}

function agentPlanCanRequestApply(view, nodeId) {
    const receipt = view.receipt;
    // Only a current manual GET supplies capability; presentations and native bindings do not (#484).
    return Boolean(view.authoritative && receipt?.apply_via === 'human_apply' && receipt.agent_node_id === nodeId
        && agentPlanValidApplication(receipt.application, receipt.generation));
}

function agentPlanApplicationHtml(view) {
    const application = view.application;
    if (!application) return '';
    const text = key => escapeHtml(tr(`smart.agentPlan${key}`));
    const error = application.error || view.applyError;
    const status = view.expired ? 'Expired' : application.state === 'requesting' ? 'Requesting'
        : application.state === 'awaiting_confirmation' ? 'AwaitingConfirmation'
        : application.state === 'unknown' ? 'Unknown'
        : application.state === 'applied' ? (error ? 'AppliedWithError' : 'Applied')
        : error?.code === 'PLAN_NOT_CONFIRMED' ? 'Declined' : 'Ready';
    let html = `<div class="agent-plan-notice" role="status">${text(status)}</div>`;
    if (!view.expired && application.state === 'awaiting_confirmation') html += `<div class="agent-plan-meta">${text('ConfirmationId')} <code>${escapeHtml(application.confirm_id)}</code></div>`;
    if (error) html += `<div class="agent-plan-error" role="alert">${escapeHtml([error.code, error.message, error.hint,
        error.details ? JSON.stringify(error.details) : ''].filter(Boolean).join(' · '))}</div>`;
    const receipt = application.receipt;
    const resultHtml = result => {
        let content = result.op_id ? `<div>${text('OperationId')} <code>${escapeHtml(result.op_id)}</code></div>` : '';
        content += `<ul>${result.groups.map(group => `<li>${escapeHtml(group.section)} · <code>${escapeHtml(group.group_id)}</code></li>`).join('')}
            ${result.tasks.map(task => `<li><code>${escapeHtml(task.task_id)}</code> · ${text('PromptNode')} <code>${escapeHtml(task.prompt_node)}</code> · ${text('EmptySlot')} <code>${escapeHtml(task.gen_node)}</code></li>`).join('')}</ul>`;
        if (result.already_applied.length) content += `<div>${text('ExistingTasks')} ${result.already_applied.map(id => `<code>${escapeHtml(id)}</code>`).join(' · ')}</div>`;
        return content;
    };
    if (receipt) html += `<details><summary>${text('Receipt')} ${receipt.generation} · ${escapeHtml(receipt.applied_by)}</summary>${resultHtml(receipt.result)}</details>`;
    if (view.noChanges) html += `<div role="status">${text('NoChanges')}</div>${resultHtml(view.noChanges)}`;
    return html;
}

function agentPlanIssueText(value, exclusion = false) {
    const raw = typeof value === 'string' ? value : JSON.stringify(value);
    let issue = value;
    if (typeof value === 'string') {
        try { issue = JSON.parse(value); } catch { return raw; }
    }
    if (!issue || typeof issue !== 'object' || Array.isArray(issue)) return raw;
    const labels = exclusion
        ? { video: 'ExcludedVideo', mask: 'ExcludedMask', no_remake: 'ExcludedNoRemake' }
        : { CONTINUATION_OR_NON_TASK_ROW: 'IssueContinuation', ANCHOR_OUT_OF_RANGE: 'IssueAnchor', NO_WORKBOOK_IMAGE: 'IssueNoImage', PROMPT_NEEDS_REVIEW: 'IssuePrompt' };
    const code = exclusion ? issue.skip_reason : issue.code;
    if (!Object.hasOwn(labels, code)) return raw;
    const where = typeof issue.source_row === 'string' || typeof issue.source_row === 'number' ? [String(issue.source_row)] : [];
    if (Number.isInteger(issue.column) && issue.column > 0) where.push(`${tr('smart.agentPlanColumn')} ${issue.column}`);
    return [...where, tr(`smart.agentPlan${labels[code]}`)].join(' · ');
}

function agentPlanHtml(message, readonly, state, nodeId) {
    const ref = message.presentation.data, view = agentPlanView(state, message), receipt = view.receipt;
    const live = !readonly && !message.presentationReadonly && message.presentation.state === 'presented'
        && message.presentationThread === agentPresentationThread(nodeId, state)
        && message.presentationProject === agentProjectId() && message.presentationCanvas === agentViewCanvasId() && !view.stale && !view.expired;
    const canApply = agentPlanCanRequestApply(view, nodeId), editable = live && canApply;
    const owner = escapeAttr(message.presentation.id), disabled = view.busy ? ' disabled' : '';
    const text = key => escapeHtml(tr(`smart.agentPlan${key}`));
    let html = `<div class="agent-plan-meta">${text('Reference')} ${escapeHtml(String(ref.generation))} · <code>${escapeHtml(ref.plan_id)}</code></div>`;
    if (receipt) {
        const { tasks, warnings, source } = receipt.preview;
        html += `<div class="agent-plan-summary"><span>${text('Current')} ${receipt.generation}</span><span>${tasks.filter(task => task.selected).length} / ${tasks.length} ${text('Selected')}</span></div>`;
        if (source?.name) html += `<div class="agent-plan-meta">${escapeHtml(source.name)}${source.sheet ? ` · ${escapeHtml(source.sheet)}` : ''}</div>`;
        const groups = new Map();
        tasks.forEach(task => { if (!groups.has(task.section)) groups.set(task.section, []); groups.get(task.section).push(task); });
        for (const [section, items] of groups) {
            html += `<section class="agent-plan-section"><h4>${escapeHtml(section)} <span>${items.length}</span></h4>`;
            for (const task of items) {
                const id = escapeAttr(task.task_id), attrs = `data-agent-plan-owner="${owner}"`;
                const draft = view.drafts.get(task.task_id) || {};
                html += `<div class="agent-plan-task"><label class="agent-plan-check">${editable
                    ? `<input type="checkbox" data-agent-plan-select="${id}" ${attrs}${task.selected ? ' checked' : ''}${disabled}>`
                    : `<span aria-label="${text(task.selected ? 'Selected' : 'NotSelected')}">${task.selected ? '☑' : '☐'}</span>`}<span>${escapeHtml(task.name)}</span></label>`;
                const rows = Array.isArray(task.source_rows) ? task.source_rows : [task.source_row];
                html += `<div class="agent-plan-meta">${text('SourceRow')} ${escapeHtml(rows.filter(row => row != null).join(', '))}</div>`;
                if (editable) {
                    const field = (name, label, value) => `<label>${text(label)}<input type="text" maxlength="200" value="${escapeAttr(value)}" data-agent-plan-draft="${id}" data-agent-plan-field="${name}" ${attrs}${disabled}></label>`;
                    html += `<details class="agent-plan-editor" data-agent-plan-editor="${id}" ${attrs}${draft.open ? ' open' : ''}><summary>${text('Edit')}</summary><div class="agent-plan-fields">`
                        + field('rename', 'Name', draft.rename ?? task.name) + field('set_section', 'Section', draft.set_section ?? task.section)
                        + `<label>${text('MergeInto')}<select data-agent-plan-draft="${id}" data-agent-plan-field="merge_into" ${attrs}${disabled}><option value="">${text('NoMerge')}</option>${tasks.filter(other => other.task_id !== task.task_id).map(other => `<option value="${escapeAttr(other.task_id)}"${draft.merge_into === other.task_id ? ' selected' : ''}>${escapeHtml(`${other.section} · ${other.name}`)}</option>`).join('')}</select></label>`
                        + `<div class="agent-plan-meta">${text('PreviewOnly')}</div><button type="button" data-agent-plan-save="${id}" ${attrs}${disabled}>${text('Save')}</button></div></details>`;
                }
                html += '</div>';
            }
            html += '</section>';
        }
        if (!tasks.length) html += `<div>${text('Empty')}</div>`;
        if (warnings.length) html += `<div class="agent-plan-warnings"><strong>${text('Warnings')}</strong><ul>${warnings.map(warning => `<li>${escapeHtml(agentPlanIssueText(warning))}</li>`).join('')}</ul></div>`;
        if (receipt.preview.excluded?.length) html += `<details><summary>${text('Excluded')} ${receipt.preview.excluded.length}</summary><ul>${receipt.preview.excluded.map(item => `<li>${escapeHtml(agentPlanIssueText(item, true))}</li>`).join('')}</ul></details>`;
    } else html += `<div role="status">${text(view.busy ? 'Loading' : 'NotLoaded')}</div>`;
    if (view.error) html += `<div class="agent-plan-error" role="alert">${text('Unconfirmed')} ${escapeHtml(view.error)}</div>`;
    // Application status owns expiry when present; a failed first read has no application payload.
    if (view.notice && (!view.expired || !view.application)) html += `<div class="agent-plan-notice" role="status">${escapeHtml(view.notice)}</div>`;
    if (view.busy && receipt) html += `<div role="status">${text(view.busy === 'save' ? 'Saving' : 'Loading')}</div>`;
    if (!live) html += `<div class="agent-plan-meta">${escapeHtml(tr('smart.agentPresentReadonly'))} · ${text('ReadCurrentOnly')}</div>`;
    html += agentPlanApplicationHtml(view);
    html += `<div class="agent-presentation-actions"><button type="button" data-agent-plan-refresh="${owner}"${disabled}>${text('Refresh')}</button>`;
    if (editable) html += `<button type="button" class="agent-presentation-primary" data-agent-plan-apply="${owner}"${view.busy || agentPlanDirty(view) || view.application?.state !== 'ready' || !receipt.preview.tasks.some(task => task.selected) ? ' disabled' : ''}>${text('ApplyRequest')}</button>`;
    return html + `</div><div class="agent-plan-meta">${text(canApply ? 'ApplyBoundary' : 'ApplyUnavailable')}</div>`;
}

function agentPresentationHtml(message, readonly, state, nodeId) {
    const p = message.presentation;
    const empty = `<div>${escapeHtml(tr('smart.agentPresentEmpty'))}</div>`;
    let content = '';
    if (p.kind === 'plan') {
        const steps = Array.isArray(p.data.steps) ? p.data.steps : [];
        content = p.data.source === 'plan_preview' && typeof p.data.plan_id === 'string' && Number.isInteger(p.data.generation) && p.data.generation > 0
            ? agentPlanHtml(message, readonly, state, nodeId)
            : `<div>${escapeHtml(tr('smart.agentPresentProposal'))}</div>`
                + (steps.length ? `<ol>${steps.map(step => `<li>${escapeHtml(step?.text || '')}</li>`).join('')}</ol>` : empty);
    } else if (p.kind === 'diff') {
        // Only server-resolved data is displayed; it never becomes HTML or an apply action.
        content = Object.keys(p.data).length ? `<pre>${escapeHtml(JSON.stringify(p.data, null, 2))}</pre>` : empty;
    } else if (p.kind === 'question') {
        const questions = Array.isArray(p.data.questions) ? p.data.questions.filter(question => question && typeof question === 'object') : [];
        const editable = p.state === 'pending' && !readonly && !message.presentationReadonly
            && message.presentationThread === agentPresentationThread(nodeId, state)
            && message.presentationProject === agentProjectId() && message.presentationCanvas === agentViewCanvasId();
        content = questions.length ? questions.map(question => {
            const options = Array.isArray(question.options) ? question.options.filter(option => option && typeof option === 'object') : [];
            const chosen = message.presentationChoices?.[question.id] ?? question.recommended;
            // Do not let the browser silently substitute its first option after a chosen ID disappears.
            const missingChoice = options.some(option => option.id === chosen) ? '' : `<option value="" selected disabled>${escapeHtml(tr('smart.agentPresentChooseValid'))}</option>`;
            const choices = editable ? `<label class="agent-question-choice"><span>${escapeHtml(question.text || '')}</span><select data-agent-question-choice="${escapeAttr(question.id)}" data-agent-question-owner="${escapeAttr(p.id)}"${message.presentationSending ? ' disabled' : ''}>${missingChoice}${options.map(option => `<option value="${escapeAttr(option.id)}"${chosen === option.id ? ' selected' : ''}>${escapeHtml(option.text || '')}${option.id === question.recommended ? ` · ${escapeHtml(tr('smart.agentPresentRecommended'))}` : ''}</option>`).join('')}</select></label>` : `<div>${escapeHtml(question.text || '')}</div>`;
            return `<div class="agent-question">${choices}<ul>${options.map(option => `<li>${escapeHtml(option.text || '')}${option.id === question.recommended ? ` <small>${escapeHtml(tr('smart.agentPresentRecommended'))}</small>` : ''}${p.answers?.[question.id] === option.id ? ` <small>${escapeHtml(tr('smart.agentPresentSelected'))}</small>` : ''}</li>`).join('')}</ul></div>`;
        }).join('') : empty;
        const status = p.state === 'answered' ? tr('smart.agentPresentAnswered') : p.state === 'expired' ? tr('smart.agentPresentExpired') : tr('smart.agentPresentPending');
        content += `<div role="status">${escapeHtml(status)}</div>`;
        if (p.state === 'answered') content += `<div class="agent-plan-meta">${escapeHtml(tr('smart.agentPresentAnswerBoundary'))}</div>`;
        if (message.presentationSending) content += `<div role="status">${escapeHtml(tr('smart.agentPresentSending'))}</div>`;
        if (message.presentationError) content += `<div role="alert">${escapeHtml(tr('smart.agentPresentAnswerUnconfirmed'))} ${escapeHtml(message.presentationError)}</div>`;
        if (message.presentationDeliveryError) content += `<div role="alert">${escapeHtml(tr('smart.agentPresentDeliveryUnconfirmed'))} ${escapeHtml(message.presentationDeliveryError)}</div>`;
        else if (message.presentationRequestError && p.state === 'answered') content += `<div role="alert">${escapeHtml(tr('smart.agentPresentRequestRejected'))} ${escapeHtml(message.presentationRequestError)}</div>`;
        if (readonly || message.presentationReadonly || (p.state === 'pending' && !editable)) content += `<div>${escapeHtml(tr('smart.agentPresentReadonly'))}</div>`;
        const answerable = questions.length && questions.every(q => typeof q.id === 'string' && Array.isArray(q.options) && q.options.some(option => option && typeof option.id === 'string' && option.id === q.recommended));
        if (editable && answerable) content += `<div class="agent-presentation-actions"><button type="button" class="agent-presentation-primary" data-agent-question-submit="${escapeAttr(p.id)}"${message.presentationSending ? ' disabled' : ''}>${escapeHtml(tr('smart.agentPresentSubmitChoices'))}</button></div><button type="button" class="agent-tui-btn" data-agent-question-answer="${escapeAttr(p.id)}"${message.presentationSending ? ' disabled' : ''}>${escapeHtml(tr('smart.agentPresentStartRecommended'))}</button>`;
    }
    return `<div class="agent-tui-msg agent-tui-presentation" data-agent-presentation="${escapeAttr(p.id)}"><strong>${escapeHtml(p.title || '')}</strong>${content}</div>`;
}

function agentNodeBodyHtml(node) {
    const nodeId = node.id;
    const state = getAgentTuiState(nodeId);
    agentSyncHostView(node, state);
    agentPrefetchInitialHistory(nodeId, canvasId || '', state);
    const readonly = agentIsReadonly(state);
    const parameterDisabled = readonly || agentParametersLocked(state);
    const timeline = agentTimelineSync(state);
    if (state.scrollBinding && state.scrollBinding.body.isConnected !== false) captureAgentScroll(state, state.scrollBinding.body);
    const rowHtml = new Map();
    const dateHeaders = new Map();
    const today = new Date().toDateString();
    const title = typeof node.title === 'string' ? node.title.trim() : '';
    const topic = title.split(' · ').at(-1);
    const dateSuffix = topic && !/^agent$/i.test(topic) ? ` · ${escapeHtml(topic)}` : '';
    const forkAnchors = agentMessageForkAnchors(state.messages);
    let previousDay;
    for (const entry of timeline) {
        if (entry.kind === 'tools') continue;
        const msg = entry.message;
        const timestamp = agentMessageTimestamp(msg.timestamp);
        const date = (msg.role === 'user' || msg.role === 'assistant') && timestamp !== undefined ? new Date(timestamp) : null;
        if (date && date.toDateString() !== previousDay) {
            previousDay = date.toDateString();
            const day = previousDay === today ? '今天' : `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
            dateHeaders.set(entry.key, `<div class="agent-tui-conversation-date">${day}${dateSuffix}</div>`);
        }
        let messagesHtml = '';
        if (msg.role === 'user') {
            const attachNames = (msg.attachments || []).filter(Boolean);
            const attachHtml = attachNames.length
                ? `<div class="agent-tui-msg-attach">${agentIco('paperclip')} ${attachNames.map(escapeHtml).join(' · ')}</div>` : '';
            messagesHtml += `<div class="agent-tui-msg agent-tui-msg-user">${agentMessageBylineHtml(msg, date)}<span class="agent-tui-msg-text">${escapeHtml(msg.text)}</span>${attachHtml}</div>`;
        } else if (msg.role === 'assistant') {
            const streamingClass = msg.streaming ? ' agent-tui-msg-streaming' : '';
            messagesHtml += `<div class="agent-tui-msg agent-tui-msg-assistant${streamingClass}">${agentMessageBylineHtml(msg, date)}<div class="agent-tui-msg-text">${renderAgentAssistantHtml(msg.text)}</div>${agentMessageActionsHtml(state, entry, forkAnchors.has(msg))}</div>`;
        } else if (msg.role === 'thinking' || entry.kind === 'thinking') {
            const live = entry.kind === 'thinking';
            const open = state.thoughtExpanded && state.thoughtExpanded[entry.key] !== undefined
                ? state.thoughtExpanded[entry.key] : state.thinkingVisible;
            messagesHtml += `<details class="agent-tui-thought${live ? ' agent-tui-thought-live' : ''}" data-agent-thought="${escapeAttr(entry.key)}"${open ? ' open' : ''}><summary>${agentIco('brain')} ${live ? '思考中' : '思考了'}</summary><div class="agent-tui-thinking-text">${escapeHtml(msg.text)}</div></details>`;
        } else if (msg.role === 'presentation') {
            messagesHtml += agentPresentationHtml(msg, readonly, state, nodeId);
        } else if (msg.role === 'system') {
            messagesHtml += `<div class="agent-tui-msg agent-tui-msg-system"><span class="agent-tui-msg-text">${escapeHtml(msg.text)}</span></div>`;
        } else if (msg.role === 'completion') {
            messagesHtml += agentCompletionBridge()?.html(msg.completion, state) || '';
        }
        messagesHtml += agentNodeRefsMessageHtml(msg);
        rowHtml.set(entry.key, messagesHtml);
    }

    // 仅连续执行合组；空待办隐藏，未知工具仍保留原始详情。
    const isTodoTool = (name) => /^(todo|write_todos|todowrite|todo_list)$/i.test(String(name || ''));
    const checklistHtml = (args) => {
        const items = args && (args.todos || args.updates);
        if (!Array.isArray(items) || !items.length) return '';
        return `<div class="agent-tui-checklist">` + items.map(it => {
            const st = it && it.status === 'completed' ? '☑'
                : (it && it.status === 'in_progress' ? '▶' : '☐');
            const done = it && it.status === 'completed' ? ' done' : '';
            const body = String((it && (it.content || it.subject || it.text)) || '');
            return `<div class="agent-tui-checklist-item${done}"><span class="agent-tui-checklist-mark">${st}</span> ${escapeHtml(body)}</div>`;
        }).join('') + `</div>`;
    };
    const toolGroups = timeline.filter(entry => entry.kind === 'tools');
    for (const group of toolGroups) {
        const runningN = group.items.filter(i => i.activity.status === 'running').length;
        const finishedN = group.items.filter(i => ['done', 'error'].includes(i.activity.status)).length;
        const running = runningN > 0;
        const errN = group.items.filter(i => i.activity.status === 'error').length;
        const totalKnown = !running && group.items.every(i => agentDurationKnown(i.activity.duration_ms));
        const totalMs = totalKnown ? group.items.reduce((sum, i) => sum + i.activity.duration_ms, 0) : null;
        const groupOpen = Boolean(state.toolGroupExpanded && state.toolGroupExpanded[group.key]);
        const groupSummary = (finishedN || !running ? `执行了 ${finishedN} 个步骤` : '')
            + (running ? `${finishedN ? ' · ' : ''}${runningN} 个步骤执行中…` : '')
            + (totalKnown && Number.isFinite(totalMs) ? ` · 总 ${(totalMs / 1000).toFixed(1)}s` : '')
            + (errN ? ` · ${errN} 失败` : '');
        let cardsHtml = '';
        for (const { toolCallId, activity } of group.items) {
            let icon = agentIco('settings');
            if (activity.status === 'done') icon = agentIco('check');
            else if (activity.status === 'error') icon = agentIco('x');
            else if (activity.status === 'running') icon = agentIco('loader-2');
            const argsFull = activity.args ? JSON.stringify(activity.args, null, 2) : '';
            const resultFull = activity.result != null ? String(activity.result) : '';
            const expandedClass = activity.expanded ? ' agent-tui-tool-expanded' : '';
            const durationHtml = agentDurationKnown(activity.duration_ms)
                ? `<span class="agent-tui-tool-duration">${(activity.duration_ms / 1000).toFixed(1)}s</span>` : '';
            const longOutput = resultFull.length > 600 || (resultFull.split('\n').length > 12);
            const expandBtn = (longOutput && activity.status !== 'running')
                ? `<button type="button" class="agent-tui-expand-output" data-expand-output="${escapeAttr(toolCallId)}">${activity.resultExpanded ? '收起' : '展开全文'}</button>` : '';
            const resultClass = (longOutput && !activity.resultExpanded) ? ' agent-tui-tool-result-clamped' : '';
            const todoHtml = isTodoTool(activity.name) ? checklistHtml(activity.args) : '';
            const jumpHtml = agentToolNodeIds(activity.args).map(id => `<button type="button" class="agent-tui-step-jump" data-agent-step-jump="${escapeAttr(id)}">定位</button>`).join('');
            const statusLabel = activity.status === 'running' ? '执行中' : activity.status === 'error' ? '失败' : '完成';
            cardsHtml += `<div class="agent-tui-tool${expandedClass}${activity.status === 'error' ? ' agent-tui-tool-error' : ''}" data-tool-call-id="${escapeAttr(toolCallId)}">
                <div class="agent-tui-tool-header">
                    <span class="agent-tui-tool-toggle">${agentIco(activity.expanded ? 'chevron-down' : 'chevron-right')}</span>
                    <span class="agent-tui-tool-icon">${icon}</span>
                    <span class="agent-tui-tool-name" title="${escapeAttr(activity.name)}">${escapeHtml(agentToolSummary(activity))}</span>
                    <span class="agent-tui-tool-status">${escapeHtml(statusLabel)}</span>${durationHtml}${jumpHtml}
                </div>
                <div class="agent-tui-tool-details">
                    ${todoHtml || (argsFull ? `<div class="agent-tui-tool-args"><pre>${escapeHtml(argsFull)}</pre></div>` : '')}
                    ${resultFull ? `<div class="agent-tui-tool-result${resultClass}"><pre>${escapeHtml(resultFull)}</pre></div>${expandBtn}` : ''}
                </div>
            </div>`;
        }
        rowHtml.set(group.key, `<details class="agent-tui-tool-group${running ? ' agent-tui-tool-group-running' : ''}"${groupOpen ? ' open' : ''} data-tool-group="${escapeAttr(group.key)}">`
            + `<summary><span class="agent-tui-tool-group-toggle">${agentIco(groupOpen ? 'chevron-down' : 'chevron-right')}</span><span class="agent-tui-tool-group-summary">${escapeHtml(groupSummary)}</span></summary>`
            + `<div class="agent-tui-tool-group-items">${cardsHtml}</div></details>`);
    }

    // Separators are presentation only: no timeline key, reading anchor, tick, or unread content.
    const timelineHtml = timeline.map(entry => `${dateHeaders.get(entry.key) ?? ''}<div class="agent-tui-entry" data-agent-entry-key="${escapeAttr(entry.key)}">${rowHtml.get(entry.key)}</div>`).join('');
    // Expansion, selection and the global brain toggle are UI changes, not new unread content.
    const timelineContent = JSON.stringify([timeline.map(entry => entry.kind === 'tools'
        ? [entry.key, entry.items.map(({ toolCallId, activity: a }) => [toolCallId, a.name, a.status, a.args, a.result, a.update, a.duration_ms])]
        : [entry.key, entry.message.text, entry.message.attachments, entry.message.presentation, entry.message.presentationDeliveryError,
            entry.message.completion ? agentCompletionBridge()?.fingerprint(entry.message.completion) : null]), state.confirm || null]);
    if (state._timelineContent != null && state._timelineContent !== timelineContent && state.scrollState && !state.scrollState.atBottom) state.scrollState.unread = true;
    state._timelineContent = timelineContent;

    let confirmHtml = '';
    if (state.confirm) {
        const c = state.confirm;
        confirmHtml = `<div class="agent-tui-confirm" data-agent-confirm="${escapeAttr(nodeId)}">
            <div class="agent-tui-confirm-message${c.tool === 'plan.apply' ? ' agent-tui-confirm-plan' : ''}">${agentIco('triangle-alert')} ${escapeHtml(c.message || '请确认操作')}</div>
            <div class="agent-tui-confirm-buttons">
                <button type="button" class="agent-tui-confirm-btn agent-tui-confirm-approve" data-agent-confirm-approve="${escapeAttr(nodeId)}">批准</button>
                <button type="button" class="agent-tui-confirm-btn agent-tui-confirm-reject" data-agent-confirm-reject="${escapeAttr(nodeId)}">取消</button>
            </div>
        </div>`;
    }

    const setting = agentHostSetting(node, state.viewHost);
    const currentModel = state.sessionModel !== undefined ? state.sessionModel : setting.model;
    const disabled = parameterDisabled ? ' disabled' : '';
    const currentAutonomy = node.autonomy || 'standard';
    const modelLabel = currentModel || '跟随宿主';
    const shortModel = modelLabel.length > 24 ? `${modelLabel.slice(0, 11)}…${modelLabel.slice(-11)}` : modelLabel;
    const effortLabel = setting.effort === null ? '跟随宿主' : setting.effort;
    const hostLabel = state.viewHost === 'pi' ? 'Pi' : 'Codex';
    const chevron = agentIco('chevron-down');
    const toolbarHtml = `<div class="agent-tui-setting-toolbar" aria-label="节点参数">
        <details class="agent-tui-host-picker">
            <summary class="agent-tui-setting-trigger" data-agent-host-picker aria-label="宿主 ${hostLabel}" aria-disabled="${parameterDisabled}"><span class="agent-tui-setting-caption">宿主</span><b>${hostLabel}</b>${chevron}</summary>
            <div class="agent-tui-host-options" role="group" aria-label="选择宿主">
                <button type="button" data-agent-host-choice="pi" aria-pressed="${state.viewHost === 'pi'}"${disabled}>Pi</button><button type="button" data-agent-host-choice="codex" aria-pressed="${state.viewHost === 'codex'}"${disabled}>Codex</button>
            </div>
        </details>
        <span class="agent-tui-setting-dot" aria-hidden="true">·</span><button type="button" class="agent-tui-setting-trigger" data-agent-model-picker="${escapeAttr(nodeId)}" aria-haspopup="menu" title="${escapeAttr(modelLabel)}" aria-label="模型 ${escapeAttr(modelLabel)}"${disabled}><span class="agent-tui-setting-caption">模型</span><b>${escapeHtml(shortModel)}</b>${chevron}</button>
        <span class="agent-tui-setting-dot" aria-hidden="true">·</span><button type="button" class="agent-tui-setting-trigger" data-agent-effort-picker="${escapeAttr(nodeId)}" aria-haspopup="menu"${disabled}><span class="agent-tui-setting-caption">思考</span><b>${escapeHtml(effortLabel)}</b>${chevron}</button>
    </div>`;
    const autonomyHtml = `<div class="agent-tui-autonomy" role="group" aria-label="自治确认档位">${[['cautious', '每张'], ['standard', '每批'], ['autonomous', '无需']].map(([value, label]) =>
        `<button type="button" data-agent-autonomy-choice="${value}" aria-pressed="${value === currentAutonomy}"${disabled}>${label}</button>`).join('')}</div>`;
    const canSteer = !readonly && !state.stopPending && ['busy', 'retrying'].includes(state.status);
    const canStop = canSteer || (!readonly && !state.stopPending && state.status === 'waiting_confirm');
    const forkDisabledReason = agentSessionForkDisabled(state);
    const readonlyBanner = readonly ? `<div class="agent-tui-readonly-banner" role="status">只读查看 ${escapeHtml(state.viewHost)} · 当前活跃宿主 ${escapeHtml(state.activeHost)}
        <button type="button" data-agent-view-current>返回当前会话</button><button type="button" data-agent-resume-host${agentParametersLocked(state) ? ' disabled' : ''}>切回此宿主继续</button></div>` : '';

    const pendingImages = state.pendingImages || [];
    let pendingHtml = '';
    if (state.attachWorking) {
        pendingHtml += `<div class="agent-tui-attach-working">${agentIco('loader-2')} ${escapeHtml(tr('smart.agentAttachWorking').replace('{name}', state.attachWorking))}</div>`;
    }
    if (pendingImages.length) {
        const chips = pendingImages.map((img, idx) => `
            <div class="agent-tui-attach">
                ${img.dataUrl ? `<img class="agent-tui-attach-thumb" src="${escapeAttr(img.dataUrl)}" alt="${escapeAttr(img.name)}">` : agentIco('file')}
                <div class="agent-tui-attach-meta">
                    <div class="agent-tui-attach-name">${escapeHtml(img.name)}</div>
                    ${img.caption ? `<div class="agent-tui-attach-caption">${escapeHtml(img.caption)}</div>` : ''}
                </div>
                <button type="button" class="agent-tui-attach-remove" data-agent-attach-remove="${idx}" title="${tr('smart.agentAttachRemove')}" aria-label="${tr('smart.agentAttachRemove')}">×</button>
            </div>`).join('');
        pendingHtml += `<div class="agent-tui-attach-row">${chips}</div>`;
    }

    const hasTuiContent = Boolean(timeline.length || state.confirm);

    return `<div class="agent-tui-card">
        ${readonlyBanner}
        ${state.settingsError || state.stopStatusError ? `<div class="agent-tui-error-banner" role="alert">${escapeHtml(state.settingsError || state.stopStatusError)}</div>` : ''}
        ${state.eventsError ? `<div class="agent-tui-error-banner" role="alert">${escapeHtml(state.eventsError)} <button type="button" data-agent-events-retry>重试历史加载</button></div>` : ''}
        ${agentReconnectBannerHtml(state)}
        ${state.stopPending && !readonly ? `<div class="agent-tui-parameter-hint" role="status">等待宿主确认停止，参数仍锁定。<button type="button" data-agent-stop-status>重新核验停止状态</button></div>` : ''}
        ${agentProgressHtml(node)}
        ${agentNodeRefsComposerHtml(nodeId)}
        <div class="agent-tui-body-wrap">
        <nav class="agent-message-navigation" aria-label="用户消息导航"></nav>
        <div class="agent-tui-body${hasTuiContent ? '' : ' agent-tui-body-empty'}" data-agent-tui-body="${escapeAttr(nodeId)}">
            <div class="agent-tui-messages" data-agent-tui-messages="${escapeAttr(nodeId)}" aria-live="polite" aria-atomic="false">
                ${state.historyHasMore ? `<button type="button" data-agent-events-older${state.historyLoadingOlder ? ' disabled' : ''}>${state.historyLoadingOlder ? '加载中…' : '加载更早的消息'}</button>` : ''}
                ${timelineHtml || '<div class="agent-tui-empty">Agent TUI 已就绪：在下方输入创作指令，Enter 发送 / Shift+Enter 换行</div>'}
                ${confirmHtml}
            </div>
        </div>
        <button type="button" class="agent-tui-back-latest" data-agent-back-latest="${escapeAttr(nodeId)}"${state.scrollState && state.scrollState.unread && !state.scrollState.atBottom ? '' : ' hidden'}>${agentIco('chevron-down')} 回到最新</button>
        </div>
        <div class="agent-tui-composer">
        ${pendingHtml}
        <div class="agent-tui-input-row">
            <div class="agent-tui-input-line">
                <textarea class="agent-tui-input" data-agent-tui-input="${escapeAttr(nodeId)}" placeholder="继续描述你的想法…" title="Enter 发送 / Shift+Enter 换行；运行中 Enter 排队为后续指令" aria-label="Agent 指令输入框" rows="3" style="min-height:96px"${readonly ? ' disabled' : ''}>${escapeHtml(state.draft || '')}</textarea>
            </div>
            ${toolbarHtml}
            <div class="agent-tui-action-row">
                <div class="agent-tui-action-buttons">
                    <button type="button" class="agent-tui-btn agent-tui-btn-attach" data-agent-attach="${escapeAttr(nodeId)}" title="${tr('smart.agentAttachFile')}" aria-label="${tr('smart.agentAttachFile')}"${readonly ? ' disabled' : ''}>${agentIco('paperclip')}</button>
                    <button type="button" class="agent-tui-btn" data-agent-fork title="${forkDisabledReason || '从会话分叉'}" aria-label="从会话分叉"${forkDisabledReason ? ' disabled' : ''}>${agentIco('git-branch')}</button>
                    <button type="button" class="agent-tui-btn" data-agent-cli title="由外部 CLI 接管" aria-label="由外部 CLI 接管">${agentIco('terminal')}</button>
                    <span class="agent-tui-action-divider" aria-hidden="true"></span>${autonomyHtml}
                </div>
                <button type="button" class="agent-tui-btn agent-tui-btn-send" data-agent-send="${escapeAttr(nodeId)}" title="发送指令" aria-label="发送指令"${readonly || state.stopPending || state.settingsPending ? ' disabled' : ''}>${agentIco('arrow-up')}</button>
            </div>
            ${canStop ? `<div class="agent-tui-action-row agent-tui-runtime-actions">
                ${canSteer ? `<button type="button" class="agent-tui-btn agent-tui-btn-steer" data-agent-steer="${escapeAttr(nodeId)}" title="重定向当前回合" aria-label="重定向当前回合">${agentIco('corner-up-right')}</button>
                <button type="button" class="agent-tui-btn agent-tui-btn-followup" data-agent-followup="${escapeAttr(nodeId)}" title="排队后续指令" aria-label="排队后续指令">${agentIco('loader-2')}</button>` : ''}
                <button type="button" class="agent-tui-btn agent-tui-btn-abort" data-agent-abort="${escapeAttr(nodeId)}" title="中止当前回合" aria-label="中止当前回合">${agentIco('square')}</button>
            </div>` : ''}
            <input type="file" class="agent-tui-file-input" data-agent-tui-file="${escapeAttr(nodeId)}" multiple style="display:none">
        </div>
        <div class="agent-tui-composer-hint"><span>Enter 发送 · Shift+Enter 换行</span><span class="agent-tui-effort-hint" role="status">${escapeHtml(state.effortNotice || '')}</span></div>
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
    const retrieval = window.canvasResultRetrieval;
    const retrievalGroup = (canvas?.history || []).map(log => retrieval?.historyGroup(log))
        .find(group => group && group.task_id === taskId && group.node_id === resultNodeId);
    const retrievalHtml = retrievalGroup ? `${retrieval.summaryHtml(retrievalGroup)}<div class="retrieval-project-slots">${retrievalGroup.items.map(item => retrieval.slotHtml(retrievalGroup,item)).join('')}</div>` : '';
    const resultLink = resultNodeId && retrievalGroup?.node_exists !== false
        ? `<a class="agent-task-result-link" data-agent-result-link="${escapeAttr(resultNodeId)}" href="#${escapeAttr(resultNodeId)}">${escapeHtml(tr('smart.viewResultNode'))}</a>`
        : '<span class="agent-task-result-link muted">' + escapeHtml(tr('smart.waitingResultNode')) + '</span>';
    return `<div class="agent-task-card agent-task-card-readonly" data-agent-task-card="${escapeAttr(taskId)}">
        <div class="agent-task-heading">
            <div><div class="agent-task-title">${escapeHtml(trf('smart.agentTaskTitle', {kind: kind}))}</div><div class="agent-task-id">${escapeHtml(taskId)}</div></div>
            <span class="agent-task-status" data-agent-task-status="${escapeAttr(taskId)}" data-status="${escapeAttr(status)}" role="status">${escapeHtml(retrievalGroup ? retrieval.generatedText(retrievalGroup) : agentTaskStatusText(status))}</span>
        </div>
        <div class="agent-task-meta"><span>${escapeHtml(trf('smart.agentTaskModelCount', {m: node?.model_type || 'online', c: Math.max(1, Number(node?.count || 1))}))}</span></div>
        <div class="agent-task-prompt">${escapeHtml(node?.prompt || '')}</div>
        <div class="agent-task-params" title="${escapeHtml(tr('smart.taskParams'))}">${escapeHtml(trf('smart.agentTaskParams', {p: paramsText}))}</div>
        ${error ? `<div class="agent-task-error" data-agent-task-error role="alert">${escapeHtml(error)}</div>` : ''}
        ${retrievalHtml}
        <div class="agent-task-result">${resultLink}</div>
    </div>`;
}

function agentProjectId(){ return canvas?.project_id || canvas?.project || sourceProjectId || ''; }
function agentViewCanvasId(){ return canvasId || ''; }

// #403：steer 和排队输入携带项目内附件引用，不传 inline 媒体。
// #313：输入框随内容自动增高，上限 140px（与 CSS max-height 一致）后出内部滚动
const AGENT_TUI_INPUT_MAX_H = 140;
function agentTuiAutoGrow(inputEl){
    if (!inputEl) return;
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.max(96, Math.min(inputEl.scrollHeight, AGENT_TUI_INPUT_MAX_H)) + 'px';
}
function agentTuiResetGrow(inputEl){
    if (!inputEl) return;
    inputEl.style.height = '';
}

function agentTuiSendInput(inputEl, nodeId, projectId, kind) {
    if (!inputEl) return;
    const inputState = getAgentTuiState(nodeId);
    const pending = (inputState.pendingImages || []).slice();
    const message = inputEl.value.trim() || (pending.length ? tr('smart.agentAttachOnlyMessage') : '');
    if (!message) return;
    if (inputState.composing || agentIsReadonly(inputState) || inputState.stopPending || inputState.settingsPending || inputState.attachWorking) return;
    inputState.nodeRefsControlDraft = inputEl.value;
    // Clear before the sender rebuilds the DOM, not on the now-detached old textarea afterward.
    inputEl.value = '';
    inputState.draft = '';
    agentTuiResetGrow(inputEl);
    inputState.pendingImages = [];
    const shouldQueue = ['busy', 'retrying'].includes(inputState.status)
        || (inputState.status === 'idle' && !inputState.queueStopped && inputState.queuedInputs.size);
    if (kind === 'steer') {
        sendAgentSteer(nodeId, projectId, message, pending);
    } else if (kind === 'follow_up' || shouldQueue) {
        sendAgentFollowUp(nodeId, projectId, message, pending);
    } else {
        sendAgentPrompt(nodeId, projectId, message, pending);
    }
}

// clientHeight is integer-rounded, whereas DOMRect preserves fractional pixels.
// Mixing the two turns a large scrollTop into accumulated anchor drift. Use the
// computed untransformed border-box height so canvas zoom is still accounted for.
function agentScrollGeometryScale(body, rect) {
    if (!(rect.height > 0)) return 1;
    if (typeof getComputedStyle === 'function') {
        const style = getComputedStyle(body);
        let height = parseFloat(style.height);
        if (height > 0 && style.boxSizing !== 'border-box') {
            for (const part of ['paddingTop', 'paddingBottom', 'borderTopWidth', 'borderBottomWidth']) {
                height += parseFloat(style[part]) || 0;
            }
        }
        if (height > 0) return height / rect.height;
    }
    return body.clientHeight / rect.height;
}

// Capture against the OLD layout, never against newly appended/taller content.
function captureAgentScroll(state, body, user = false) {
    if (!body || !(body.clientHeight > 0)) return state.scrollState;
    const previous = state.scrollState;
    const binding = state.scrollBinding;
    const top = Math.max(0, Number(body.scrollTop) || 0);
    const distance = Math.max(0, body.scrollHeight - body.clientHeight - top);
    const atBottom = !user && binding && previous ? previous.atBottom : distance <= 48;
    const rect = body.getBoundingClientRect();
    const scale = agentScrollGeometryScale(body, rect);
    let anchor = null;
    for (const row of body.querySelectorAll('[data-agent-entry-key]')) {
        const r = row.getBoundingClientRect();
        if (r.bottom > rect.top && r.top < rect.bottom) {
            anchor = { key: row.getAttribute('data-agent-entry-key'), offset: (r.top - rect.top) * scale };
            break;
        }
    }
    state.scrollState = { scrollTop: top, atBottom, anchor, unread: atBottom ? false : Boolean(previous && previous.unread) };
    return state.scrollState;
}

function updateAgentBackLatest(state) {
    const binding = state.scrollBinding;
    if (binding && binding.latest) binding.latest.hidden = !state.scrollState || state.scrollState.atBottom || !state.scrollState.unread;
}

function restoreAgentScroll(state, body) {
    // D1's collapsed strip hides the entire body. Zero geometry is not a new reading position.
    if (!(body.clientHeight > 0)) return;
    const ss = state.scrollState;
    const messages = body.querySelector('[data-agent-tui-messages]');
    let row = null;
    let top = ss && !ss.atBottom ? ss.scrollTop : Math.max(0, body.scrollHeight - body.clientHeight);
    if (ss && !ss.atBottom && ss.anchor) {
        row = Array.from(body.querySelectorAll('[data-agent-entry-key]')).find(row => row.getAttribute('data-agent-entry-key') === ss.anchor.key);
        if (row) {
            const rect = body.getBoundingClientRect();
            const scale = agentScrollGeometryScale(body, rect);
            top = body.scrollTop + (row.getBoundingClientRect().top - rect.top) * scale - ss.anchor.offset;
        }
    }
    const maxTop = Math.max(0, body.scrollHeight - body.clientHeight);
    body.scrollTop = Math.max(0, Math.min(top, maxTop));
    if (messages) {
        if (row && top > 0 && top < maxTop) {
            // Chromium quantizes scrollTop even though row layout is fractional.
            // Compensate only its subpixel remainder, without changing layout or
            // moving content beyond a real scroll boundary.
            const rect = body.getBoundingClientRect();
            const scale = agentScrollGeometryScale(body, rect);
            const delta = ss.anchor.offset - (row.getBoundingClientRect().top - rect.top) * scale;
            const existing = parseFloat((messages.style.translate || '').split(' ')[1]) || 0;
            if (Math.abs(delta) < 1) messages.style.translate = `0px ${existing + delta}px`;
        } else messages.style.translate = '';
    }
    const binding = state.scrollBinding;
    if (binding) binding.programTop = body.scrollTop;
    if (state.messageNavigation) state.messageNavigation.update();
    if (!ss) state.scrollState = { scrollTop: body.scrollTop, atBottom: true, anchor: null, unread: false };
    else ss.scrollTop = body.scrollTop;
    updateAgentBackLatest(state);
}

function disposeAgentScroll(state) {
    if (state.messageNavigation) { state.messageNavigation.destroy(); state.messageNavigation = null; }
    const binding = state.scrollBinding;
    if (!binding) return;
    if (binding.observer) binding.observer.disconnect();
    if (binding.detachObserver) binding.detachObserver.disconnect();
    if (binding.raf != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(binding.raf);
    if (binding.intentTimer != null) clearTimeout(binding.intentTimer);
    for (const [target, type, listener, options] of binding.listeners) {
        if (target.removeEventListener) target.removeEventListener(type, listener, options);
    }
    state.scrollBinding = null;
}

function scheduleAgentScrollLayout(state) {
    const binding = state.scrollBinding;
    if (!binding || binding.raf != null) return;
    if (binding.body.isConnected === false) { disposeAgentScroll(state); return; }
    if (typeof requestAnimationFrame !== 'function') return;
    binding.raf = requestAnimationFrame(() => {
        binding.raf = null;
        if (state.scrollBinding !== binding) return;
        if (binding.body.isConnected === false) { disposeAgentScroll(state); return; }
        restoreAgentScroll(state, binding.body);
        // No render and no recurring frame: observer notifications caused by scrolling do not loop.
    });
}

function bindAgentScroll(el, state) {
    const body = el.querySelector('[data-agent-tui-body]');
    if (!body) { disposeAgentScroll(state); return; }
    if (state.scrollBinding && state.scrollBinding.body === body) return;
    disposeAgentScroll(state);
    const binding = { body, latest: el.querySelector('[data-agent-back-latest]'), listeners: [], raf: null, userIntent: false, intentTimer: null };
    state.scrollBinding = binding;
    const on = (target, type, listener, options) => {
        if (!target || !target.addEventListener) return;
        target.addEventListener(type, listener, options);
        binding.listeners.push([target, type, listener, options]);
    };
    const armIntent = () => {
        binding.userIntent = true;
        if (binding.intentTimer != null) clearTimeout(binding.intentTimer);
        binding.intentTimer = setTimeout(() => { binding.userIntent = false; binding.intentTimer = null; }, 200);
    };
    const intent = () => {
        // Flush a pending layout before accepting user intent, without interpreting that as a user scroll.
        if (binding.raf != null) {
            cancelAnimationFrame(binding.raf);
            binding.raf = null;
            restoreAgentScroll(state, body);
        }
        captureAgentScroll(state, body);
        armIntent();
    };
    ['wheel', 'touchstart', 'touchmove', 'pointerdown', 'mousedown', 'keydown'].forEach(type => on(body, type, intent, { passive: true }));
    on(body, 'scroll', () => {
        state.messageNavigation.update();
        // Native anchoring, focus(), and our restoration also fire scroll; only explicit user intent
        // may change follow mode. In particular a resize must not turn a follower into a reader.
        if (!binding.userIntent || body.scrollTop === binding.programTop) return;
        captureAgentScroll(state, body, true);
        binding.programTop = null;
        armIntent();
        updateAgentBackLatest(state);
        if (body.scrollTop <= 96 && state.historyHasMore && !state.historyRetry) loadOlderAgentEvents(body.getAttribute('data-agent-tui-body'), state);
    }, { passive: true });
    // A resize/load can arrive between a wheel event and its native scroll event.
    // Do not erase that in-progress user intent.
    const layout = () => { scheduleAgentScrollLayout(state); };
    on(body, 'load', layout, true);
    on(body, 'toggle', layout, true);
    on(window, 'resize', layout);
    on(binding.latest, 'mousedown', event => event.stopPropagation());
    on(binding.latest, 'click', event => {
        event.stopPropagation();
        state.scrollState = { scrollTop: body.scrollTop, atBottom: true, anchor: null, unread: false };
        binding.userIntent = false;
        restoreAgentScroll(state, body);
    });
    if (typeof ResizeObserver === 'function') {
        binding.observer = new ResizeObserver(layout);
        binding.observer.observe(body);
        const messages = el.querySelector('[data-agent-tui-messages]');
        if (messages) binding.observer.observe(messages);
    }
    // Full canvas renders can remove the old world node without invoking TUI cleanup.
    if (typeof MutationObserver === 'function' && document.documentElement) {
        binding.detachObserver = new MutationObserver(() => {
            if (body.isConnected === false) disposeAgentScroll(state);
        });
        binding.detachObserver.observe(document.documentElement, { childList: true, subtree: true });
    }
    const rows = [...body.querySelectorAll('[data-agent-entry-key]')];
    const rowByKey = new Map(rows.map(row => [row.getAttribute('data-agent-entry-key'), row]));
    const users = agentTimelineSync(state).filter(entry => entry.kind === 'message' && entry.message.role === 'user');
    const entries = users.map(entry => ({ id: entry.key, label: '你', text: entry.message.text,
        element: rowByKey.get(entry.key) })).filter(entry => entry.element);
    state.messageNavigation = createAgentMessageNavigation({
        body, mount: el.querySelector('.agent-message-navigation'), entries,
        readingRect: currentId => {
            const rect = body.getBoundingClientRect();
            const current = rowByKey.get(currentId)?.getBoundingClientRect();
            if (current && current.bottom > rect.top && current.top < rect.bottom) return current;
            const reading = rows.find(row => { const r = row.getBoundingClientRect(); return r.bottom > rect.top && r.top < rect.bottom; });
            return reading ? reading.getBoundingClientRect() : rect;
        },
        onJump: entry => {
            const rect = body.getBoundingClientRect(), target = entry.element.getBoundingClientRect();
            const scale = agentScrollGeometryScale(body, rect);
            body.scrollTop += (target.top - rect.top) * scale;
            captureAgentScroll(state, body, true);
            binding.programTop = body.scrollTop;
            updateAgentBackLatest(state);
        },
    });
    if (!state.scrollState) state.scrollState = { scrollTop: 0, atBottom: true, anchor: null, unread: false };
    restoreAgentScroll(state, body);
    if (!state.scrollState.atBottom && !state.scrollState.anchor) captureAgentScroll(state, body);
    scheduleAgentScrollLayout(state);
}

function bindAgentMessageActions(el, nodeId, projectId) {
    const state = getAgentTuiState(nodeId);
    const epoch = state.viewEpoch;
    const buttons = el.querySelectorAll('[data-agent-msg-copy], [data-agent-msg-fork]');
    if (!buttons.length) return;
    const replies = new Map(state.timeline.filter(entry => entry.kind === 'message' && entry.message.role === 'assistant').map(entry => [entry.key, entry.message]));
    buttons.forEach(button => {
        const copy = button.hasAttribute('data-agent-msg-copy');
        const reply = replies.get(button.getAttribute(copy ? 'data-agent-msg-copy' : 'data-agent-msg-fork'));
        button.addEventListener('mousedown', event => { if (event.button === 0) event.stopPropagation(); });
        button.addEventListener('click', async event => {
            event.stopPropagation();
            if (agentTuiState.get(nodeId) !== state || state.closing || state.viewEpoch !== epoch || !state.messages.includes(reply)) {
                window.ffToast.error('这条回复已不在当前会话视图中');
                return;
            }
            if (copy) {
                const reason = agentMessageCopyDisabled(reply);
                if (reason) { window.ffToast.error(reason); return; }
                try {
                    await navigator.clipboard.writeText(reply.text);
                } catch (error) {
                    window.ffToast.error(`复制失败：${String(error)}。请选中文本手动复制。`);
                    return;
                }
                window.ffToast.success('已复制回复');
            } else {
                const nextUser = agentMessageForkAnchors(state.messages).get(reply);
                const reason = agentMessageForkDisabled(state, reply, Boolean(nextUser));
                if (reason) { window.ffToast.error(`无法分叉：${reason}`); return; }
                await showForkPicker(nodeId, projectId, { reply, nextUser });
            }
        });
    });
}

function agentRenderPresentation(nodeId) {
    const state = getAgentTuiState(nodeId);
    state.presentationRenderAllowed = true;
    try { renderAgentNode(nodeId); } finally { state.presentationRenderAllowed = false; }
}

async function agentReadPlanResponse(response, planId, allowPending = false) {
    const body = await response.json();
    if (!response.ok) throw Object.assign(new Error(agentNodeRefsError(body, `HTTP ${response.status}`)), {
        planStale: response.status === 409 && body?.detail?.code === 'PLAN_STALE',
        planAlreadyApplied: response.status === 409 && body?.detail?.code === 'PLAN_ALREADY_APPLIED', detail: body?.detail,
    });
    if (allowPending && body?.code === 'CONFIRM_PENDING' && body.status === 'awaiting_confirmation'
        && typeof body.confirm_id === 'string' && body.confirm_id.length > 0) return body;
    const preview = body?.preview;
    if (body?.plan_id !== planId || !Number.isSafeInteger(body.generation) || body.generation < 1
        || !preview || !Array.isArray(preview.tasks) || !Array.isArray(preview.warnings)
        || !preview.sections || typeof preview.sections !== 'object' || Array.isArray(preview.sections)
        || preview.tasks.some(task => !task || typeof task.task_id !== 'string' || !task.task_id
            || typeof task.name !== 'string' || typeof task.section !== 'string' || typeof task.selected !== 'boolean')
        || new Set(preview.tasks.map(task => task.task_id)).size !== preview.tasks.length) throw new Error(tr('smart.agentPlanInvalidReceipt'));
    return body;
}

function agentPlanDirty(view) {
    return view.receipt?.preview.tasks.some(task => {
        const draft = view.drafts.get(task.task_id);
        return draft && ((draft.rename !== undefined && draft.rename !== task.name)
            || (draft.set_section !== undefined && draft.set_section !== task.section) || draft.merge_into);
    });
}

async function agentPlanAction(nodeId, projectId, state, epoch, message, action, ops = []) {
    const map = state.planPreviews, view = agentPlanView(state, message), presentation = message.presentation;
    const planId = presentation.data.plan_id, host = state.viewHost, thread = agentPresentationThread(nodeId, state);
    const owns = agentPresentationOwner(nodeId, projectId, state, epoch, message, action === 'read');
    const current = (restoredView = false) => owns(restoredView) && state.planPreviews === map && map.get(planId) === view
        && message.presentation === presentation && presentation.data.plan_id === planId
        && agentPresentationThread(nodeId, state) === thread && (action === 'read'
            || (!message.presentationReadonly && presentation.state === 'presented'
                && !state.stopPending && !state.settingsPending && message.presentationThread === agentPresentationThread(nodeId, state)));
    if (!current() || view.busy || (action !== 'read' && (view.stale || view.expired))
        || (action === 'apply' && (!agentPlanCanRequestApply(view, nodeId) || agentPlanDirty(view)
            || view.application?.state !== 'ready' || !view.receipt.preview.tasks.some(task => task.selected)))
        || (action === 'save' && (!agentPlanCanRequestApply(view, nodeId) || !ops.length))) return;
    const serial = ++view.serial, generation = view.receipt?.generation;
    view.requestEpoch = epoch; view.invalidOwner = false; view.departed = false;
    const valid = () => !view.invalidOwner && current(true) && view.requestEpoch === state.viewEpoch && serial === view.serial;
    // A read-only host visit suspends settlement, not ownership or a second application (#411).
    const resume = () => state.viewHost === host ? Promise.resolve(valid()) : new Promise(resolve => {
        view.resume = () => { delete view.resume; resolve(valid()); };
    });
    const base = `/api/projects/${encodeURIComponent(projectId)}/plan/previews/${encodeURIComponent(planId)}`;
    const query = `?agent_node_id=${encodeURIComponent(nodeId)}`;
    const headers = { 'X-Agent-Project': projectId };
    const read = () => fetch(`${base}${query}`, { credentials: 'same-origin', cache: 'no-store', headers }).then(response => agentReadPlanResponse(response, planId));
    const adopt = (receipt, fromRead = false) => {
        if (view.receipt && receipt.generation < view.receipt.generation) throw new Error(tr('smart.agentPlanInvalidReceipt'));
        const trusted = receipt.apply_via === 'human_apply' && receipt.agent_node_id === nodeId
            && agentPlanValidApplication(receipt.application, receipt.generation);
        if (!trusted && ['unknown', 'awaiting_confirmation', 'requesting'].includes(view.application?.state)) {
            view.authoritative = false;
            throw new Error(tr('smart.agentPlanInvalidReceipt'));
        }
        if (receipt.generation !== view.receipt?.generation) { view.applyError = null; view.noChanges = null; }
        view.receipt = receipt;
        view.application = trusted ? receipt.application : null;
        if (fromRead) view.authoritative = true;
        if (!trusted) view.authoritative = false;
        if (trusted && receipt.application.error?.code === 'PLAN_STALE') view.expired = true;
        view.stale = false;
    };
    let postStarted = false;
    view.busy = action; view.busyCurrent = valid; view.attempted = true; view.error = ''; view.notice = '';
    agentRenderPresentation(nodeId);
    try {
        const receipt = action === 'save'
            ? await agentReadPlanResponse(await fetch(`${base}/edits${query}`, { method: 'POST', credentials: 'same-origin', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify({ generation, ops }) }), planId)
            : await read();
        if (!await resume()) return;
        if ((generation && receipt.generation < generation) || (action === 'save' && receipt.generation !== generation + 1)) throw new Error(tr('smart.agentPlanInvalidReceipt'));
        adopt(receipt, action !== 'save');
        if (action === 'read') view.drafts.clear();
        if (action === 'save') {
            for (const op of ops) {
                const draft = view.drafts.get(op.task_id);
                if (draft) for (const field of ['rename', 'set_section', 'merge_into']) if (field in op) delete draft[field];
            }
            for (const id of view.drafts.keys()) if (!receipt.preview.tasks.some(task => task.task_id === id)) view.drafts.delete(id);
            view.notice = tr('smart.agentPlanSaved');
        }
        if (action !== 'apply') return;
        if (receipt.generation !== generation) { view.drafts.clear(); view.notice = tr('smart.agentPlanChanged'); return; }
        if (!agentPlanCanRequestApply(view, nodeId)) throw new Error(tr('smart.agentPlanApplyUnavailable'));
        if (view.expired || view.application.state !== 'ready') return;
        if (!receipt.preview.tasks.some(task => task.selected)) throw new Error(tr('smart.agentPlanNoneSelected'));
        view.application = { ...receipt.application, state: 'requesting' }; view.applyError = null; view.noChanges = null;
        postStarted = true;
        agentRenderPresentation(nodeId);
        const applied = await agentReadPlanResponse(await fetch(`${base}/apply${query}`, {
            method: 'POST', credentials: 'same-origin', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify({ generation }),
        }), planId, true);
        if (!await resume()) return;
        if (applied.code === 'CONFIRM_PENDING') {
            view.application = { ...receipt.application, state: 'awaiting_confirmation', confirm_id: applied.confirm_id };
        } else {
            if (applied.generation !== generation || applied.apply_via !== 'human_apply' || applied.agent_node_id !== nodeId
                || !agentPlanValidApplication(applied.application, generation)) throw new Error(tr('smart.agentPlanInvalidReceipt'));
            adopt(applied);
            if (applied.no_changes && agentPlanValidResult(applied.result, false)) view.noChanges = applied.result;
        }
    } catch (error) {
        if (!await resume()) return;
        if (error.planStale) {
            if (action !== 'save') {
                view.expired = true; view.notice = tr('smart.agentPlanExpired');
                return;
            }
            view.error = String(error.message || error); view.stale = true; view.notice = tr('smart.agentPlanConflict');
            // Editing CAS only refreshes review authority; it never resubmits old operations.
            try {
                const fresh = await read();
                if (await resume()) { adopt(fresh, true); view.drafts.clear(); }
            } catch (readError) {
                if (await resume()) {
                    if (readError.planStale) { view.expired = true; view.notice = tr('smart.agentPlanExpired'); }
                    view.error += ` · ${String(readError.message || readError)}`;
                }
            }
        } else if (postStarted) {
            const details = error.planAlreadyApplied ? error.detail?.details : null;
            const proof = details?.receipt;
            const committed = details?.plan_id === planId && details.generation === generation && details.state === 'applied'
                && details.op_id === proof?.result?.op_id && details.applied_by === proof?.applied_by
                && agentPlanValidApplication({ state: 'applied', generation, receipt: proof }, generation);
            // A matching 409 retains commit evidence, never authority over a possibly newer preview (#484).
            view.application = { ...view.application, state: 'unknown', ...(committed ? { receipt: proof } : {}) };
            view.authoritative = false;
            view.applyError = error.planAlreadyApplied ? null : error.detail || { message: String(error.message || error) };
            // HTTP failure is not proof of rollback. Only GET reconciles the reserved generation (#484).
            try {
                const canonical = await read();
                if (await resume()) adopt(canonical, true);
            } catch (readError) {
                if (await resume()) {
                    if (readError.planStale) view.expired = true;
                    view.error = String(readError.message || readError);
                }
            }
        } else view.error = String(error.message || error);
    } finally {
        if (serial === view.serial) {
            view.busy = ''; view.busyCurrent = null;
            if (postStarted && !valid() && view.application?.state === 'requesting') {
                view.application = { ...view.application, state: 'unknown' }; view.authoritative = false;
            }
            if (valid()) agentRenderPresentation(nodeId);
        }
    }
}

function bindAgentPlanControls(el, nodeId, projectId, state, epoch) {
    const messageFor = id => state.messages.find(row => row.presentation?.kind === 'plan' && row.presentation.id === id && row.presentation.data.source === 'plan_preview');
    const attach = (attr, action) => el.querySelectorAll(`[${attr}]`).forEach(control => {
        const message = messageFor(control.getAttribute('data-agent-plan-owner') || control.getAttribute(attr));
        if (!message) return;
        const owns = agentPresentationOwner(nodeId, projectId, state, epoch, message, action === 'read');
        control.addEventListener('mousedown', event => event.stopPropagation());
        const run = () => {
            if (control.isConnected === false || !owns()) return;
            const view = agentPlanView(state, message);
            if (action === 'select') {
                const task = view.receipt?.preview.tasks.find(task => task.task_id === control.getAttribute(attr));
                if (!task) return;
                const selected = control.checked;
                control.checked = task.selected;
                agentPlanAction(nodeId, projectId, state, epoch, message, 'save', [{ task_id: task.task_id, select: selected }]);
            } else if (action === 'save') {
                const id = control.getAttribute(attr), task = view.receipt?.preview.tasks.find(task => task.task_id === id), draft = view.drafts.get(id);
                if (!task || !draft) return;
                const ops = ['rename', 'set_section', 'merge_into'].filter(field => draft[field] !== undefined && (field === 'merge_into'
                    ? Boolean(draft[field]) : draft[field] !== task[field === 'rename' ? 'name' : 'section'])).map(field => ({ task_id: id, [field]: draft[field] }));
                agentPlanAction(nodeId, projectId, state, epoch, message, 'save', ops);
            } else agentPlanAction(nodeId, projectId, state, epoch, message, action);
        };
        control.addEventListener(action === 'select' ? 'change' : 'click', event => { event.stopPropagation(); run(); });
        if (action === 'read') {
            const view = agentPlanView(state, message);
            if (view.busy && !view.busyCurrent?.()) {
                view.serial++; view.resume?.(); view.busy = ''; view.attempted = false; view.authoritative = false;
                if (view.application?.state === 'requesting') view.application = { ...view.application, state: 'unknown' };
            }
            if (!view.attempted) run();
        }
    });
    attach('data-agent-plan-refresh', 'read');
    attach('data-agent-plan-select', 'select');
    attach('data-agent-plan-save', 'save');
    attach('data-agent-plan-apply', 'apply');
    el.querySelectorAll('[data-agent-plan-draft]').forEach(input => {
        const message = messageFor(input.getAttribute('data-agent-plan-owner'));
        if (!message) return;
        const owns = agentPresentationOwner(nodeId, projectId, state, epoch, message);
        const update = () => {
            if (input.isConnected === false || !owns() || message.presentationReadonly) return;
            const view = agentPlanView(state, message), id = input.getAttribute('data-agent-plan-draft'), field = input.getAttribute('data-agent-plan-field');
            if (view.busy || view.stale || view.expired || !agentPlanCanRequestApply(view, nodeId)
                || !['rename', 'set_section', 'merge_into'].includes(field)) return;
            view.drafts.set(id, { ...view.drafts.get(id), [field]: input.value, open: true });
            el.querySelectorAll('[data-agent-plan-apply]').forEach(button => { button.disabled = true; });
        };
        input.addEventListener('mousedown', event => event.stopPropagation());
        input.addEventListener('keydown', event => event.stopPropagation());
        input.addEventListener('input', update); input.addEventListener('change', update);
    });
    el.querySelectorAll('[data-agent-plan-editor]').forEach(editor => {
        editor.addEventListener('mousedown', event => event.stopPropagation());
        editor.addEventListener('toggle', () => {
            if (editor.isConnected === false) return;
            const message = messageFor(editor.getAttribute('data-agent-plan-owner'));
            if (!message || !agentPresentationOwner(nodeId, projectId, state, epoch, message)()) return;
            const view = agentPlanView(state, message), id = editor.getAttribute('data-agent-plan-editor');
            if (Boolean(view.drafts.get(id)?.open) !== editor.open) view.drafts.set(id, { ...view.drafts.get(id), open: editor.open });
        });
        editor.addEventListener('focusout', () => queueMicrotask(() => {
            // Focus transfer precedes click; remounting another card control here would eat its action.
            if (state.planDraftRerender && el.isConnected !== false && !el.contains?.(document.activeElement)
                && !document.activeElement?.closest?.('[data-agent-plan-editor]')) {
                state.planDraftRerender = false; renderAgentNode(nodeId);
            }
        }));
    });
}

async function answerAgentPresentation(nodeId, projectId, state, epoch, message, useChoices = false) {
    const owns = agentPresentationOwner(nodeId, projectId, state, epoch, message);
    const current = () => owns() && !state.stopPending && !state.settingsPending;
    if (!current() || message.presentationReadonly
        || message.presentationSending || message.presentation.state !== 'pending') return;
    const id = message.presentation.id, version = message.presentationVersion || 0;
    const chosen = Object.fromEntries((message.presentation.data.questions || []).map(q => [q.id, message.presentationChoices?.[q.id] ?? q.recommended]));
    const url = `/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/questions/${encodeURIComponent(id)}`;
    let answerable = true;
    const read = async (response, isAnswer = false) => {
        const data = await response.json();
        if (!isAnswer && data.answerable === false) answerable = false;
        if (!response.ok) throw Object.assign(new Error(agentNodeRefsError(data, `HTTP ${response.status}`)), { answerRejected: isAnswer });
        const p = data.presentation;
        if (!p || p.schema !== 1 || p.id !== id || p.kind !== 'question' || !p.data || !Array.isArray(p.data.questions)
            || !['pending', 'answered', 'expired'].includes(p.state)
            || !p.data.questions.length || p.data.questions.some(q => !q || typeof q.id !== 'string' || !q.id
                || typeof q.text !== 'string' || !Array.isArray(q.options) || q.options.length < 2
                || q.options.some(option => !option || typeof option.id !== 'string' || !option.id || typeof option.text !== 'string')
                || new Set(q.options.map(option => option.id)).size !== q.options.length || !q.options.some(option => option.id === q.recommended))
            || new Set(p.data.questions.map(q => q.id)).size !== p.data.questions.length) throw new Error(tr('smart.agentPresentInvalidReceipt'));
        if (data.delivery_error && current() && (message.presentationVersion || 0) === version) message.presentationDeliveryError = agentNodeRefsError({ detail: data.delivery_error }, tr('smart.agentPresentDeliveryUnconfirmed'));
        return p;
    };
    message.presentationSending = true;
    message.presentationError = '';
    message.presentationRequestError = '';
    renderAgentNode(nodeId);
    try {
        // A replayed/persisted pending record is not proof that its original runtime still accepts input.
        const fresh = await read(await fetch(url));
        if (!current()) return;
        if ((message.presentationVersion || 0) !== version || message.presentation.state !== 'pending') return;
        message.presentation = fresh;
        if (fresh.state !== 'pending') return;
        if (!answerable) throw new Error(tr('smart.agentPresentReadonly'));
        const questions = fresh.data?.questions;
        if (!Array.isArray(questions) || !questions.length || questions.some(q => !q || typeof q.id !== 'string'
            || !Array.isArray(q.options) || !q.options.some(option => option && typeof option.id === 'string' && option.id === q.recommended))) throw new Error(tr('smart.agentPresentInvalidReceipt'));
        if (useChoices && (Object.keys(chosen).length !== questions.length || questions.some(q => !q.options.some(option => option?.id === chosen[q.id])))) throw new Error(tr('smart.agentPresentChoicesChanged'));
        const answers = useChoices ? chosen : Object.fromEntries(questions.map(q => [q.id, q.recommended]));
        const receipt = await read(await fetch(`${url}/answers`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answers }) }), true);
        if (!current()) return;
        if (receipt.state !== 'answered' || !receipt.answers || Object.keys(receipt.answers).length !== questions.length
            || questions.some(q => receipt.answers[q.id] !== answers[q.id])) throw new Error(tr('smart.agentPresentInvalidReceipt'));
        if ((message.presentationVersion || 0) === version) message.presentation = receipt;
    } catch (error) {
        // A lost response is ambiguous; an explicit server rejection must remain visible even after answer storage.
        if (current() && error.answerRejected) message.presentationRequestError = String(error.message || error);
        if (current() && message.presentation.state !== 'answered') message.presentationError = String(error.message || error);
    } finally {
        message.presentationSending = false;
        if (current()) renderAgentNode(nodeId);
    }
}

function bindAgentNodeControls(el, node){
    agentSyncHostView(node, getAgentTuiState(node.id));
    agentNodeRefsBind(el, node);
    const nodeId = node.id;
    const projectId = agentViewCanvasId();

    // Normal binding must honor reconnect backoff instead of starting a new connection on each render (#280).
    connectAgentWebSocket(nodeId, projectId);
    const initialState = getAgentTuiState(nodeId);
    if (!initialState.historyLoaded && !initialState.historyBlocked && !initialState._replayInFlight) replayAgentEvents(nodeId, initialState);

    const historyRetry = el.querySelector('[data-agent-events-retry]');
    if (historyRetry) historyRetry.addEventListener('click', event => {
        event.stopPropagation();
        const state = getAgentTuiState(nodeId);
        if (state.historyRetry === 'older') loadOlderAgentEvents(nodeId, state);
        else {
            if (state.historyRetry === 'reload') { state.historyLoaded = false; state.lastSeq = 0; }
            replayAgentEvents(nodeId, state);
        }
    });
    const olderHistory = el.querySelector('[data-agent-events-older]');
    if (olderHistory) olderHistory.addEventListener('click', event => {
        event.stopPropagation();
        loadOlderAgentEvents(nodeId, getAgentTuiState(nodeId));
    });

    const stopStatus = el.querySelector('[data-agent-stop-status]');
    if (stopStatus) stopStatus.addEventListener('click', event => {
        event.stopPropagation();
        const state = getAgentTuiState(nodeId);
        if (state.stopStatusTimer) clearTimeout(state.stopStatusTimer);
        pollAgentStopStatus(nodeId, projectId, state, state.viewEpoch);
    });

    const retryBtn = el.querySelector('[data-agent-retry]');
    if (retryBtn) {
        retryBtn.addEventListener('mousedown', event => event.stopPropagation());
        retryBtn.addEventListener('click', event => {
            event.stopPropagation();
            const rState = getAgentTuiState(nodeId);
            if (rState) rState.lastError = '';
            connectAgentWebSocket(nodeId, projectId, { force: true });
            renderAgentNode(nodeId);
        });
    }

    bindAgentMessageActions(el, nodeId, projectId);
    const presentationState = getAgentTuiState(nodeId), presentationEpoch = presentationState.viewEpoch;
    bindAgentPlanControls(el, nodeId, projectId, presentationState, presentationEpoch);
    for (const attr of ['data-agent-question-answer', 'data-agent-question-submit']) el.querySelectorAll(`[${attr}]`).forEach(button => {
        const message = presentationState.messages.find(row => row.presentation?.id === button.getAttribute(attr));
        button.addEventListener('mousedown', event => event.stopPropagation());
        button.addEventListener('click', event => {
            event.stopPropagation();
            if (message && button.isConnected !== false) answerAgentPresentation(nodeId, projectId, presentationState, presentationEpoch, message, attr === 'data-agent-question-submit');
        });
    });
    el.querySelectorAll('[data-agent-question-choice]').forEach(input => {
        const message = presentationState.messages.find(row => row.presentation?.id === input.getAttribute('data-agent-question-owner'));
        if (!message) return;
        const owns = agentPresentationOwner(nodeId, projectId, presentationState, presentationEpoch, message);
        input.addEventListener('mousedown', event => event.stopPropagation());
        input.addEventListener('keydown', event => event.stopPropagation());
        input.addEventListener('change', event => {
            event.stopPropagation();
            if (!owns() || input.isConnected === false || message.presentationReadonly || message.presentationSending || message.presentation.state !== 'pending') return;
            const id = input.getAttribute('data-agent-question-choice'), question = message.presentation.data.questions.find(q => q.id === id);
            if (question?.options.some(option => option.id === input.value)) message.presentationChoices = { ...message.presentationChoices, [id]: input.value };
        });
    });

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

    el.querySelectorAll('[data-tool-group]').forEach(groupEl => {
        groupEl.addEventListener('mousedown', event => event.stopPropagation());
        groupEl.addEventListener('toggle', () => {
            const gState = getAgentTuiState(nodeId);
            gState.toolGroupExpanded = gState.toolGroupExpanded || {};
            gState.toolGroupExpanded[groupEl.getAttribute('data-tool-group')] = groupEl.open;
            scheduleAgentScrollLayout(gState);
        });
    });

    const thoughtState = getAgentTuiState(nodeId), thoughtEpoch = thoughtState.viewEpoch;
    el.querySelectorAll('[data-agent-thought]').forEach(thought => {
        let previousOpen = Boolean(thought.open);
        const overrides = thoughtState.thoughtExpanded;
        thought.addEventListener('toggle', () => {
            // Initial `open` and detached old rows can emit an asynchronous toggle.
            if (!thought.isConnected || agentTuiState.get(nodeId) !== thoughtState || thoughtEpoch !== thoughtState.viewEpoch
                || overrides !== thoughtState.thoughtExpanded || previousOpen === thought.open) return;
            previousOpen = thought.open;
            overrides[thought.getAttribute('data-agent-thought')] = thought.open;
            scheduleAgentScrollLayout(thoughtState);
        });
    });
    el.querySelectorAll('[data-agent-step-jump]').forEach(button => {
        button.addEventListener('mousedown', event => event.stopPropagation());
        button.addEventListener('click', event => {
            event.stopPropagation();
            const id = button.getAttribute('data-agent-step-jump');
            if (canvasState.nodes.some(node => node.id === id) && typeof focusRevealNode === 'function') focusRevealNode(id);
        });
    });

    el.querySelectorAll('[data-expand-output]').forEach(btn => {
        btn.addEventListener('mousedown', event => event.stopPropagation());
        btn.addEventListener('click', event => {
            event.stopPropagation();
            const gState = getAgentTuiState(nodeId);
            const activity = gState.toolActivities.get(btn.getAttribute('data-expand-output'));
            if (activity) {
                activity.resultExpanded = !activity.resultExpanded;
                renderAgentNode(nodeId);
            }
        });
    });

    const inputEl = el.querySelector('[data-agent-tui-input]');
    const state0 = getAgentTuiState(nodeId);
    if (inputEl) {
        inputEl.addEventListener('mousedown', event => event.stopPropagation());
        inputEl.addEventListener('input', () => {
            state0.draft = inputEl.value;
            agentTuiAutoGrow(inputEl);
        });
        inputEl.addEventListener('compositionstart', () => { state0.composing = true; });
        inputEl.addEventListener('compositionend', () => {
            state0.composing = false;
            state0.draft = inputEl.value;
            if (state0.pendingRerender) {
                state0.pendingRerender = false;
                renderAgentNode(nodeId);
                // renderAgentNode restores focus and the current selection, not forcibly the end.
            }
            // Dock changes must wait for compositionend to preserve the IME session (#407).
            if (state0.pendingDock !== undefined) {
                const request = state0.pendingDock;
                state0.pendingDock = undefined;
                prepareAgentDockContinuation(request);
                agentDockSet(request.nodeId, request.dockSide);
            }
        });
        inputEl.addEventListener('keydown', event => {
            event.stopPropagation();
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { // #252：IME 候选确认回车不触发发送
                event.preventDefault();
                agentTuiSendInput(inputEl, nodeId, projectId);
            }
        });
    }

    const sendBtn = el.querySelector('[data-agent-send]');
    if (sendBtn) {
        sendBtn.addEventListener('mousedown', event => event.stopPropagation());
        sendBtn.addEventListener('click', event => {
            event.stopPropagation();
            agentTuiSendInput(inputEl, nodeId, projectId);
        });
    }

    const steerBtn = el.querySelector('[data-agent-steer]');
    if (steerBtn) {
        steerBtn.addEventListener('mousedown', event => event.stopPropagation());
        steerBtn.addEventListener('click', event => {
            event.stopPropagation();
            agentTuiSendInput(el.querySelector('[data-agent-tui-input]'), nodeId, projectId, 'steer');
        });
    }

    const followupBtn = el.querySelector('[data-agent-followup]');
    if (followupBtn) {
        followupBtn.addEventListener('mousedown', event => event.stopPropagation());
        followupBtn.addEventListener('click', event => {
            event.stopPropagation();
            agentTuiSendInput(el.querySelector('[data-agent-tui-input]'), nodeId, projectId, 'follow_up');
        });
    }

    el.querySelectorAll('[data-agent-abort]').forEach(abortBtn => {
        abortBtn.addEventListener('mousedown', event => event.stopPropagation());
        abortBtn.addEventListener('click', event => {
            event.stopPropagation();
            sendAgentAbort(nodeId, projectId);
        });
    });

    const approveBtn = el.querySelector('[data-agent-confirm-approve]');
    if (approveBtn) {
        approveBtn.addEventListener('mousedown', event => event.stopPropagation());
        approveBtn.addEventListener('click', event => {
            event.stopPropagation();
            sendAgentConfirm(nodeId, projectId, true);
        });
    }

    const rejectBtn = el.querySelector('[data-agent-confirm-reject]');
    if (rejectBtn) {
        rejectBtn.addEventListener('mousedown', event => event.stopPropagation());
        rejectBtn.addEventListener('click', event => {
            event.stopPropagation();
            sendAgentConfirm(nodeId, projectId, false);
        });
    }

    el.querySelectorAll('[data-agent-model-picker],[data-agent-effort-picker]').forEach(button => {
        button.addEventListener('mousedown', event => event.stopPropagation());
        button.addEventListener('click', event => { event.stopPropagation(); openAgentModelPicker(nodeId, projectId, button.hasAttribute('data-agent-effort-picker') ? 'effort' : 'model', button); });
    });
    const hostPicker = /** @type {HTMLDetailsElement|null} */ (el.querySelector('.agent-tui-host-picker'));
    if (hostPicker) {
        const summary = hostPicker.querySelector('[data-agent-host-picker]');
        hostPicker.addEventListener('mousedown', event => event.stopPropagation());
        summary.addEventListener('click', event => {
            event.stopPropagation();
            if (summary.getAttribute('aria-disabled') === 'true') event.preventDefault();
        });
        hostPicker.addEventListener('keydown', event => {
            if (event.key !== 'Escape' || !hostPicker.open) return;
            event.preventDefault();
            event.stopPropagation();
            hostPicker.open = false;
            summary.focus({preventScroll: true});
        });
        hostPicker.querySelectorAll('[data-agent-host-choice]').forEach(option => option.addEventListener('click', event => {
            event.stopPropagation();
            hostPicker.open = false;
            summary.focus({preventScroll: true});
            agentSelectHost(nodeId, projectId, option.getAttribute('data-agent-host-choice'));
        }));
    }
    const viewCurrent = el.querySelector('[data-agent-view-current]');
    if (viewCurrent) viewCurrent.addEventListener('click', event => { event.stopPropagation(); agentViewHost(nodeId, projectId, getAgentTuiState(nodeId).activeHost); });
    const resumeHost = el.querySelector('[data-agent-resume-host]');
    if (resumeHost) resumeHost.addEventListener('click', event => { event.stopPropagation(); agentSelectHost(nodeId, projectId, getAgentTuiState(nodeId).viewHost); });

    // Legacy /model seam remains supported; product controls use the atomic picker.
    const modelSelect = el.querySelector('[data-agent-model]');
    if (modelSelect) {
        modelSelect.addEventListener('mousedown', event => event.stopPropagation());
        modelSelect.addEventListener('change', event => {
            event.stopPropagation();
            const model = modelSelect.value;
            setAgentModel(nodeId, projectId, model);
        });
    }

    el.querySelectorAll('[data-agent-autonomy-choice]').forEach(option => {
        option.addEventListener('mousedown', event => event.stopPropagation());
        option.addEventListener('click', event => {
            event.stopPropagation();
            const s = getAgentTuiState(nodeId);
            if (agentIsReadonly(s) || agentParametersLocked(s) || option.getAttribute('aria-pressed') === 'true') return;
            setAgentAutonomy(nodeId, projectId, option.getAttribute('data-agent-autonomy-choice'));
        });
    });

    bindAgentMoreControls(el, node);

    const forkBtn = el.querySelector('[data-agent-fork]');
    if (forkBtn) {
        forkBtn.addEventListener('mousedown', event => event.stopPropagation());
        forkBtn.addEventListener('click', event => {
            event.stopPropagation();
            const state = getAgentTuiState(nodeId);
            if (!agentIsReadonly(state) && !agentParametersLocked(state) && state.activeHost === 'pi') showForkPicker(nodeId, projectId);
        });
    }

    const cliBtn = el.querySelector('[data-agent-cli]');
    if (cliBtn) {
        cliBtn.addEventListener('mousedown', event => event.stopPropagation());
        cliBtn.addEventListener('click', event => {
            event.stopPropagation();
            showAgentCliTakeover(nodeId, projectId);
        });
    }

    const attachBtn = el.querySelector('[data-agent-attach]');
    const fileInput = el.querySelector('[data-agent-tui-file]');
    if (attachBtn && fileInput) {
        attachBtn.addEventListener('mousedown', event => event.stopPropagation());
        attachBtn.addEventListener('click', event => {
            event.stopPropagation();
            fileInput.click();
        });
        fileInput.addEventListener('change', event => {
            const files = Array.from(fileInput.files || []);
            fileInput.value = '';
            if (files.length) handleAgentAttachments(nodeId, projectId, files);
        });
    }
    el.addEventListener('dragover', event => {
        if (Array.from(event.dataTransfer?.types || []).includes('Files')) {
            event.preventDefault();
            event.stopPropagation();
        }
    });
    el.addEventListener('drop', event => {
        const files = Array.from(event.dataTransfer?.files || []);
        if (!files.length) return;
        event.preventDefault();
        event.stopPropagation();
        const state = getAgentTuiState(nodeId);
        if (!agentIsReadonly(state) && !state.attachWorking) handleAgentAttachments(nodeId, projectId, files);
    });
    el.querySelectorAll('[data-agent-attach-remove]').forEach(btn => {
        btn.addEventListener('mousedown', event => event.stopPropagation());
        btn.addEventListener('click', event => {
            event.stopPropagation();
            const idx = Number(btn.getAttribute('data-agent-attach-remove'));
            const s = getAgentTuiState(nodeId);
            if (s && s.pendingImages && s.pendingImages[idx]) {
                s.pendingImages.splice(idx, 1);
                renderAgentNode(nodeId);
            }
        });
    });

    bindAgentScroll(el, getAgentTuiState(nodeId));
    agentCompletionBridge()?.bind(el, nodeId, initialState);
    const bodyEl = el.querySelector('[data-agent-tui-body]');
    if (bodyEl) {
        // #314/#317：左键仍阻止冒泡（防误拖节点/选区）；中键放行——从消息区起手即可平移画布（shell pan 支持 button 0/1）
        bodyEl.addEventListener('mousedown', event => { if (event.button === 0) event.stopPropagation(); });
    }
}

function renderAgentNode(nodeId) {
    const st314 = agentTuiState.get(nodeId);
    if (st314 && st314._batchHistory) return;
    // Native IME and in-progress plan edits must not be remounted by unrelated live events.
    if (st314 && st314.composing) { st314.pendingRerender = true; return; }

    const el = document.querySelector(`[data-id="${nodeId}"]`);
    if (!el) return;
    const active = document.activeElement;
    const planEditor = active?.closest?.('[data-agent-plan-editor]');
    const planFocus = planEditor && el.contains?.(active) ? {
        owner: active.getAttribute('data-agent-plan-owner'), task: active.getAttribute('data-agent-plan-draft'),
        field: active.getAttribute('data-agent-plan-field'), start: active.selectionStart, end: active.selectionEnd,
    } : null;
    if (planFocus && st314 && !st314.presentationRenderAllowed && !st314.viewNeedsMount
        && canvasState.nodes.some(node => node.id === nodeId && agentNodeHost(node) === st314.viewHost)) {
        st314.planDraftRerender = true;
        return;
    }
    if (st314) st314.planDraftRerender = false;

    const prevBody = el.querySelector('[data-agent-tui-body]');
    const prevInput = el.querySelector('[data-agent-tui-input]');
    const mountingHost = st314 && st314.viewNeedsMount;
    const hadFocus = !mountingHost && prevInput && document.activeElement === prevInput;
    const selStart = prevInput ? prevInput.selectionStart : 0;
    const selEnd = prevInput ? prevInput.selectionEnd : 0;
    if (st314) agentCaptureComposer(st314, nodeId);
    if (prevBody && st314 && !mountingHost) captureAgentScroll(st314, prevBody);
    if (prevInput && st314 && !mountingHost) st314.draft = prevInput.value;

    const node = canvasState.nodes.find(n => n.id === nodeId);
    if (!node) return;

    const previousHost = st314 && st314.viewHost;
    if (st314) agentSyncHostView(node, st314);
    const changedHost = st314 && (mountingHost || previousHost !== st314.viewHost);
    const restoreFocus = changedHost ? st314.inputFocus : null;

    const imgs = node.images || [];
    const scale = nodeScale(node);
    const layout = imageLayout(imgs, scale, node);
    if (!layout) return;

    const body = nodeBodyHtml(node, layout);
    const bodyContainer = el.querySelector('.node-body');
    if (bodyContainer) {
        if (st314) disposeAgentScroll(st314);
        bodyContainer.innerHTML = body;
        bindAgentNodeControls(bodyContainer, node);
        if (st314) st314.viewNeedsMount = false;
        const focus = restoreFocus || (!changedHost && st314 ? st314.inputFocus : null);
        const newInput = bodyContainer.querySelector('[data-agent-tui-input]');
        if (newInput && !newInput.disabled && (focus || (!changedHost && prevInput))) {
            // A focused history reader must not steal the composer's selection when live events rebuild it.
            if (focus?.focused || (!changedHost && hadFocus)) newInput.focus({ preventScroll: true });
            newInput.setSelectionRange(focus ? focus.start : selStart, focus ? focus.end : selEnd, focus?.direction);
            if (focus) newInput.scrollTop = focus.scrollTop;
        }
        if (planFocus && !changedHost) {
            const input = [...bodyContainer.querySelectorAll('[data-agent-plan-draft]')].find(input => input.getAttribute('data-agent-plan-owner') === planFocus.owner
                && input.getAttribute('data-agent-plan-draft') === planFocus.task && input.getAttribute('data-agent-plan-field') === planFocus.field);
            if (input && !input.disabled) {
                input.focus({ preventScroll: true });
                if (typeof planFocus.start === 'number') input.setSelectionRange?.(planFocus.start, planFocus.end);
            }
        }
    }

    const more = el.querySelector('[data-agent-more-content]');
    if (more) {
        more.innerHTML = agentMoreActionsHtml(node);
        bindAgentMoreControls(more, node);
        bindAgentHeadControls(more, node);
    }
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

// ============ #407 D1：Agent 节点停靠（左/右贴边，不随画布缩放/平移） ============
// 数据模型：只有 node.dock ∈ {'left','right'}（无=浮动）和 node.w 随画布持久化。
// 收起/手动展开只在内存 Set 中，刷新后按当前视口重判；不写节点，不触发保存。
// 三种形态同一实体：会话/消息/草稿全在 agentTuiState（按 nodeId），停靠只换 DOM 挂载位置，
// 不动 agentTuiState、不改 node.x/y、不动视口。全量 render() 跳过停靠节点，
// 消息/流式更新走 renderAgentNode 就地路径（它按 [data-id] 全文档查找，元素在停靠槽里同样工作）——
// 所以输入/IME 永不被全量重建打断（#314 同一原因）；只有显式切换形态才搬 DOM，搬前抓焦点/选区、搬完恢复。
const AGENT_DOCK_GAP = 12;              // 收起窄条与屏幕边缘的间距（CSS .agent-dock-node left/right）
const AGENT_DOCK_COLLAPSED_W = 44;      // 窄条态宽度
const AGENT_DOCK_NARROW_VW = 900;       // 视口宽低于此值 → 自动收起（只收不展，展开由用户手动）
const AGENT_DOCK_CLEAR_VW = 480;        // 面板宽 + 此值 > 视口宽 → 自动收起（给中央内容留位置）

function agentDockState(node){
    return node && (node.dock === 'left' || node.dock === 'right') ? node.dock : null;
}

const agentDockCollapsed = new Set();
const agentDockUserExpanded = new Set();

function agentDockIsCollapsed(nodeId){
    return agentDockCollapsed.has(nodeId);
}

function agentDockForget(nodeId){
    agentDockCollapsed.delete(nodeId);
    agentDockUserExpanded.delete(nodeId);
}

// 旧数据兼容：按 canvasState.nodes 顺序保留首个 Agent 的有效 dock。
// 必须在 render 过滤 world 节点之前运行，不能等到尾部同步时才释放其它节点。
function normalizeAgentDockNodes(nodes){
    let kept = false;
    (nodes || []).forEach(node => {
        delete node.dockCollapsed;
        if(!agentDockState(node)) return;
        if(!isSmartAgentNode(node) || kept){
            node.dock = null;
            agentDockForget(node.id);
        } else {
            kept = true;
        }
    });
}

// 停靠宽度与画布内布局同公式（imageLayout 的 Agent 特判：node.w 有效则用，否则 480；下限 330）
function agentDockNodeWidth(node){
    const w = Number(node && node.w);
    const base = (Number.isFinite(w) && w > 24) ? w : 480;
    return Math.max(330, Math.round(base));
}

function agentDockElement(nodeId){
    return document.querySelector(`#agentDock .agent-dock-node[data-id="${CSS.escape(String(nodeId))}"]`);
}

function agentDockBodyEl(nodeId){
    const el = agentDockElement(nodeId) || document.querySelector(`#world .image-node[data-id="${CSS.escape(String(nodeId))}"]`);
    return el;
}

// 切换形态前抓输入焦点/选区（#314 同款手法；草稿已在 agentTuiState.draft，重建模板会自动注入）
function agentDockCaptureFocus(nodeId){
    const el = agentDockBodyEl(nodeId);
    const input = el && el.querySelector('[data-agent-tui-input]');
    if(!input || document.activeElement !== input) return null;
    return { selStart: input.selectionStart, selEnd: input.selectionEnd };
}

function agentDockRestoreFocus(nodeId, info){
    if(!info) return;
    const el = agentDockBodyEl(nodeId);
    const input = el && el.querySelector('[data-agent-tui-input]');
    if(!input) return;
    try {
        input.focus();
        input.setSelectionRange(info.selStart, info.selEnd);
    } catch (e) { /* #348: 防御性——焦点/选区恢复失败不影响停靠切换功能（元素已到位） */ }
}

// 停靠节点 DOM（与画布节点同一 markup 族，去掉画布专属件：hint/端口/拖柄/浮动删除钮/工具栏）。
// 头部与 render() 一致：title + head-meta（状态段）+ node-actions（含 agentHeadActionButtonsHtml）。
function agentDockNodeHtml(node){
    const nodeId = String(node.id);
    const scale = nodeScale(node);
    const layout = imageLayout(node.images || [], scale, node) || { width: 480, height: 360 };
    const title = node.title || 'Agent';
    return `<div class="image-node agent-smart-node agent-dock-node dock-${node.dock}${agentDockIsCollapsed(nodeId) ? ' dock-collapsed' : ''}${isNodeSelected(nodeId) ? ' selected' : ''}" data-id="${escapeHtml(nodeId)}" style="width:${agentDockNodeWidth(node)}px">
        <div class="node-head"><div class="node-title"><span class="agent-title-kind">Agent</span>${escapeHtml(title)}</div><div class="node-head-meta">${agentCardStatusHtml(nodeId)}</div><div class="node-actions">${agentHeadActionButtonsHtml(node)}</div></div>
        <div class="node-body">${nodeBodyHtml(node, layout)}</div>
        <button type="button" class="dock-collapse-strip" data-agent-dock-expand="${escapeAttr(nodeId)}" title="展开停靠面板" aria-label="展开停靠面板"><svg class="ico" aria-hidden="true"><use href="#i-generate"/></svg><span class="dock-strip-title">${escapeHtml(title)}</span></button>
    </div>`;
}

// 停靠节点的事件绑定（bindNodeEvents 只覆盖 world 内节点，停靠槽自己绑）
function bindAgentDockNodeControls(el, node){
    if(!el || !node) return;
    bindAgentNodeControls(el, node);
    bindAgentHeadControls(el, node);
    el.onclick = e => {
        e.stopPropagation();
        const id = el.dataset.id;
        const nodeNow = canvasState.nodes.find(n => n.id === id);
        if(nodeNow) hideRunTimerForNode(nodeNow);
        const alreadySelected = canvasState.selectedId === id && canvasState.selectedIds.length === 0 && canvasState.selectedImage.nodeId === '';
        canvasState.selectedId = id;
        canvasState.selectedIds = [];
        canvasState.selectedImage = {nodeId:'', index:-1};
        if(alreadySelected){ syncSelectionUi(); updateComposer(); return; }
        render();
    };
    el.querySelectorAll('[data-agent-dock-expand]').forEach(btn => {
        btn.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); agentDockCollapseToggle(node.id); });
    });
    bindAgentDockDragOut(el, node.id);
}

// 停靠态拖节点头拖出画布 → 落回浮动（落点=世界坐标；拖回停靠区则不动）
function bindAgentDockDragOut(el, nodeId){
    const head = el.querySelector('.node-head');
    if(!head) return;
    head.addEventListener('mousedown', e => {
        if(e.button !== 0) return;
        if(e.target.closest('.agent-tui-more,button,select,input,textarea,a')) return;
        e.preventDefault();
        const startX = e.clientX, startY = e.clientY;
        const rect = el.getBoundingClientRect();
        const grabDX = startX - rect.left, grabDY = startY - rect.top;
        let dragging = false;
        const onMove = ev => {
            if(!dragging && Math.hypot(ev.clientX - startX, ev.clientY - startY) > 6){
                dragging = true;
                el.classList.add('drag-out');
            }
            if(dragging){
                el.style.left = (ev.clientX - grabDX) + 'px';
                el.style.top = (ev.clientY - grabDY) + 'px';
            }
        };
        const cleanup = () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
            window.removeEventListener('pointercancel', onCancel);
            window.removeEventListener('blur', onCancel);
            el.classList.remove('drag-out');
            el.style.left = ''; el.style.top = '';
        };
        const onCancel = () => { cleanup(); dragging = false; };
        const onUp = ev => {
            cleanup();
            if(!dragging || Math.hypot(ev.clientX - startX, ev.clientY - startY) <= 6) return;
            const node = canvasState.nodes.find(n => n.id === nodeId);
            const worldEl = document.getElementById('world');
            const inWorld = worldEl && worldEl.getBoundingClientRect()
                && ev.clientX >= worldEl.getBoundingClientRect().left && ev.clientX <= worldEl.getBoundingClientRect().right
                && ev.clientY >= worldEl.getBoundingClientRect().top && ev.clientY <= worldEl.getBoundingClientRect().bottom;
            if(isSmartAgentNode(node) && inWorld){
                const p = screenToWorld(ev);
                const x = Math.round(p.x - 20), y = Math.round(p.y - 20);
                if(!agentDockState(node) && node.x === x && node.y === y) return;
                const action = beginAgentCanvasHumanEdit([node], ['dock', 'x', 'y'], 'user');
                if(action) canvasCore.pushUndo();
                agentDockHumanIntentVersion++;
                node.x = x;
                node.y = y;
                node.dock = null;
                agentDockForget(nodeId);
                finishAgentCanvasHumanEdit(action);
                scheduleSave && scheduleSave();
                render(); // 全量重建：world 里重新出节点，syncAgentDock 卸载停靠元素
            } else {
                render(); // 拖回停靠区：原样恢复
            }
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
        window.addEventListener('pointercancel', onCancel);
        window.addEventListener('blur', onCancel);
    });
}

// IME carries explicit intent, never a field snapshot or an action timestamp.
// A newer manual request (including a no-op) cancels older queued intentions.
const agentDockHumanIntents = new WeakMap();
let agentDockHumanIntentVersion = 0;
let agentDockContinuation = null;
function prepareAgentDockContinuation(request){
    agentDockContinuation = {request, intent:agentDockHumanIntents.get(request)};
    agentDockHumanIntents.delete(request);
}

// 设置停靠侧（null=按钮取回原坐标）。最多一个 Agent 停靠；替换不改任何 x/y。
// #314：目标或被取回者正在 IME 组合时，整次切换延后到阻塞者 compositionend。
function agentDockSet(nodeId, dockSide, {actor=null}={}){
    // Consume before validation/no-op exits so no actor can reach a later call.
    const continuation = agentDockContinuation;
    agentDockContinuation = null;
    const intent = actor === 'user'
        ? {version:++agentDockHumanIntentVersion, projectId:agentProjectId()}
        : continuation?.request.nodeId === nodeId && continuation.request.dockSide === dockSide ? continuation.intent : null;
    if(intent && (intent.version !== agentDockHumanIntentVersion || intent.projectId !== agentProjectId())) return;
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if(!isSmartAgentNode(node)) return;
    const side = (dockSide === 'left' || dockSide === 'right') ? dockSide : null;
    const displaced = side ? canvasState.nodes.filter(n => n !== node && isSmartAgentNode(n) && agentDockState(n)) : [];
    const affected = [node, ...displaced];
    const changed = affected.filter(n => agentDockState(n) !== (n === node ? side : null));
    if(!changed.length) return; // no absent→null normalization or same-side history
    const blocker = affected.find(n => agentTuiState.get(n.id)?.composing);
    if(blocker){
        const request = {nodeId, dockSide:side};
        agentTuiState.get(blocker.id).pendingDock = request;
        if(intent) agentDockHumanIntents.set(request, intent);
        return;
    }
    const action = beginAgentCanvasHumanEdit(changed, ['dock'], intent ? 'user' : null);
    if(action) canvasCore.pushUndo();
    const focus = affected.map(n => ({nodeId:n.id, info:agentDockCaptureFocus(n.id)})).find(item => item.info);
    displaced.forEach(n => {
        n.dock = null;
        agentDockForget(n.id);
    });
    if(changed.includes(node)) node.dock = side;
    if(!side) agentDockForget(nodeId);
    finishAgentCanvasHumanEdit(action);
    if(typeof scheduleSave === 'function') scheduleSave();
    render(); // world 重建 + 尾部 syncAgentDock；收起态仅在同步入口按视口判断
    if(focus) agentDockRestoreFocus(focus.nodeId, focus.info);
}

function agentDockCollapseToggle(nodeId){
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if(!node || !agentDockState(node)) return;
    if(agentDockIsCollapsed(nodeId)){
        agentDockCollapsed.delete(nodeId);
        agentDockUserExpanded.add(nodeId); // 手动展开：本次页面中不再被自动收起覆盖
    } else {
        agentDockCollapsed.add(nodeId);
        agentDockUserExpanded.delete(nodeId);
    }
    syncAgentDock();
}

function agentDockShouldAutoCollapse(node){
    const vw = window.innerWidth;
    if(vw < AGENT_DOCK_NARROW_VW) return true;
    return agentDockNodeWidth(node) + AGENT_DOCK_CLEAR_VW > vw;
}

// 停靠槽同步（每次全量 render() 尾部调用）：挂载/卸载停靠元素、宽度/收起态/选中态、
// 以及 #shell 上的 --agent-dock-left/--agent-dock-right（历史抽屉/小地图/工具栏据此让出空间）。
function syncAgentDock(){
    normalizeAgentDockNodes(canvasState.nodes);
    const dock = document.getElementById('agentDock');
    if(!dock) return;
    const dockedNodes = (canvasState.nodes || []).filter(n => agentDockState(n));
    // 不再停靠的节点 → 卸载停靠元素（world 里由 render() 重新出现）
    dock.querySelectorAll('.agent-dock-node').forEach(el => {
        if(!dockedNodes.some(n => n.id === el.dataset.id)) {
            const state = agentTuiState.get(el.dataset.id);
            if (state && state.scrollBinding?.body === el.querySelector('[data-agent-tui-body]')) disposeAgentScroll(state);
            el.remove();
        }
    });
    // 新停靠的节点 → 挂载（元素是新建的，草稿/消息由模板从 agentTuiState 注入）
    dockedNodes.forEach(node => {
        if(agentDockElement(node.id)) return;
        const wrap = document.createElement('div');
        wrap.innerHTML = agentDockNodeHtml(node);
        const nodeEl = wrap.firstElementChild;
        if(!nodeEl) return;
        dock.appendChild(nodeEl);
        bindAgentDockNodeControls(nodeEl, node);
    });
    // 现有元素同步宽度/收起/选中（render 后 world 里的类不同步到停靠元素，这里补）；
    // 窄视口自动收起（只收不展：收起态是用户/自动选择，宽视口恢复后不自动展开）
    let leftReserve = 0, rightReserve = 0;
    dockedNodes.forEach(node => {
        if(!agentDockUserExpanded.has(node.id) && agentDockShouldAutoCollapse(node)) agentDockCollapsed.add(node.id);
        const collapsed = agentDockIsCollapsed(node.id);
        const el = agentDockElement(node.id);
        if(el){
            el.classList.toggle('dock-collapsed', collapsed);
            el.classList.toggle('dock-left', node.dock === 'left');
            el.classList.toggle('dock-right', node.dock === 'right');
            el.classList.toggle('selected', isNodeSelected(node.id));
            // 收起态宽度用 CSS 类的 44px——但挂载时写了内联 width（同画布节点模板），内联优先，
            // 所以收起时把内联改成 44，展开时改回节点宽。
            el.style.width = (collapsed ? AGENT_DOCK_COLLAPSED_W : agentDockNodeWidth(node)) + 'px';
            el.querySelectorAll('[data-agent-dock-collapse]').forEach(btn => {
                btn.textContent = collapsed ? '展开' : '收起';
                btn.title = collapsed ? '展开停靠面板' : '收起为窄条';
                btn.setAttribute('aria-label', btn.textContent);
            });
        }
        // 展开态贴边（CSS left/right:0），只有收起窄条离边 AGENT_DOCK_GAP；展开时再加 GAP 会让小地图与面板间距变成双份。
        const reserve = collapsed ? AGENT_DOCK_GAP + AGENT_DOCK_COLLAPSED_W : agentDockNodeWidth(node);
        if(node.dock === 'left') leftReserve = reserve;
        else rightReserve = reserve;
    });
    const shellEl = document.getElementById('shell');
    if(shellEl){
        shellEl.style.setProperty('--agent-dock-left', leftReserve + 'px');
        shellEl.style.setProperty('--agent-dock-right', rightReserve + 'px');
    }
    dock.hidden = dockedNodes.length === 0;
}

// 视口缩放时重判停靠几何（自动收起/展开变量）；syncAgentDock 是幂等的，防抖后直调
let agentDockResizeTimer = null;
// #409: the host publishes selection; the owning TUI consumes it synchronously.
window.addEventListener('studio-canvas-selection-sync', event => {
    canvasState.agentNodeRefsLocate = event.detail.locate;
    syncAgentInputNodeRefs('');
});

window.addEventListener('resize', () => {
    if(agentDockResizeTimer) return;
    agentDockResizeTimer = setTimeout(() => {
        agentDockResizeTimer = null;
        if(typeof canvasState !== 'undefined' && canvasState && canvasState.nodes && typeof syncAgentDock === 'function') syncAgentDock();
    }, 200);
});

// 清理节点时同步卸载停靠元素（deleteNode 路径兜底：render() 后的 syncAgentDock 也会处理，
// 这里保证删除帧里停靠槽不残留一帧）
function agentDockUnmount(nodeId){
    if(!document.getElementById('agentDock')) return;
    const el = agentDockElement(nodeId);
    if(el) {
        const state = agentTuiState.get(nodeId);
        if (state && state.scrollBinding?.body === el.querySelector('[data-agent-tui-body]')) disposeAgentScroll(state);
        el.remove();
    }
}

function cleanupAgentTuiState(nodeId) {
    window.canvasAgentChat?.forget(nodeId);
    agentNodeRefsForget(nodeId);
    agentDockForget(nodeId);
    const state = agentTuiState.get(nodeId);
    if (state) {
        disposeAgentScroll(state);
        closeAgentModelPicker(state);
        if (state.stopStatusTimer) clearTimeout(state.stopStatusTimer);
        if (state.effortNoticeTimer) clearTimeout(state.effortNoticeTimer);
        for (const view of state.hostViews.values()) {
            if (view.nodeRefsDraft) for (const pending of view.nodeRefsDraft.pending.values()) clearTimeout(pending.timer);
        }
        // Invalidate before closing: onclose must not schedule a reconnect for a deleted node.
        state.closing = true;
        if (state.reconnectTimer) {
            clearTimeout(state.reconnectTimer);
            state.reconnectTimer = null;
        }
        if (state.socket) {
            try { state.socket.close(); } catch (e) { /* #348: 防御性——close 可能因已关闭再抛，清理流程继续 */ }
        }
        agentTuiState.delete(nodeId);
        agentCompletionBridge()?.invalidate();
        if (!agentTuiState.size) agentClearCompletionTitle();
    }
    agentDockUnmount(nodeId);
}

// ── #323 票 E：会话框（agent.chatFrameId）+ 参考框（group.refFrame / agent.refFrameId）───────────────────
// 服务端只把它们当节点字段用（create_node_core 按 chatFrameId 落位；refFrame 由服务端从
// canvas store 读）——前端负责字段写入 + UI。Refs #323。

// Agent 的会话框（smart-group）。自愈降级：chatFrameId 指向不存在/非分组节点 → 按无处理。
function agentChatFrameGroup(agent){
    const id = String(agent?.chatFrameId || '');
    if(!id) return null;
    const node = canvasState.nodes.find(n => n.id === id);
    return (node && isSmartGroupNode(node)) ? node : null;
}

// Agent 当前绑定的参考框（smart-group）。自愈方式同上。
function agentRefFrameGroup(agent){
    const id = String(agent?.refFrameId || '');
    if(!id) return null;
    const node = canvasState.nodes.find(n => n.id === id);
    return (node && isSmartGroupNode(node)) ? node : null;
}

// 参考框内素材数：items→nodes，images 非空的成员节点计 1（#323 票 E 口径）。
function agentRefFrameAssetCount(group){
    if(!isSmartGroupNode(group)) return 0;
    const ids = Array.isArray(group.items) ? group.items : [];
    let count = 0;
    ids.forEach(id => {
        const member = canvasState.nodes.find(n => n.id === id);
        if(member && (member.images || []).length > 0) count += 1;
    });
    return count;
}

// E1：agent 卡「创建会话框/打开会话框」文字钮（监理钉B2：绑定后一键可达=文案直接说明动作，
// 非纯图标）——无框时在 agent 右侧（宽 + 40 偏移）创建 smart-group，
// group.title = (agent.title||'会话') + ' · 会话框'（监理钉B1：框面可见归属标记），且框节点带
// chatFrame/chatFrameFor 机器可读标记（Refs #323）；已有框时点击 = 视口居中到该 frame 真中心。
// Explicit manual entry only. Standalone presentation callers without the editor's
// storage adapter retain their existing behavior; never guess a durable projection.
function beginAgentCanvasHumanEdit(nodes, fields, actor){
    if(actor !== 'user' || typeof canvasCore.canvasForStorage !== 'function') return null;
    return canvasCore.beginSmartHumanEdit(nodes, {
        fields,
        project:node => canvasCore.canvasForStorage().nodes.find(stored => stored.id === node.id) || {},
    });
}

function finishAgentCanvasHumanEdit(action){
    if(!action) return;
    canvasCore.finishSmartHumanEdit(action, canvasState.nodes);
    canvasCore.finalizePendingCanvasUndo();
}

function agentChatFrameAction(agentId, {actor=null}={}){
    const agent = canvasState.nodes.find(n => n.id === agentId);
    if(!agent || !isSmartAgentNode(agent)) return null;
    const existing = agentChatFrameGroup(agent);
    if(existing){
        centerViewportOnWorldPoint({x:Number(existing.x || 0) + (Number(existing.w) || 640) / 2, y:Number(existing.y || 0) + (Number(existing.h) || 480) / 2});
        return {action:'center', groupId:existing.id};
    }
    const action = beginAgentCanvasHumanEdit([agent], ['chatFrameId'], actor);
    // The constructor owns the single creation undo. Initialize the new user
    // frame and finish the existing Agent's edit before its first publication.
    const group = createSmartGroupNode(Number(agent.x || 0) + (Number(agent.w || 240)) + 40, Number(agent.y || 0), {
        initialize(frame){
            frame.title = (agent.title || '会话') + ' · 会话框';
            frame.chatFrame = true;
            frame.chatFrameFor = agent.id;
            agent.chatFrameId = frame.id;
            finishAgentCanvasHumanEdit(action);
        },
    });
    return {action:'created', groupId:group.id};
}

// #323 binding status lives next to the real frame action in the r3 overflow menu.
function agentChatFrameStatusHtml(node){
    const frame = agentChatFrameGroup(node);
    if(frame){
        const title = String(frame.title || '会话框');
        const short = title.length > 14 ? title.slice(0, 14) + '…' : title;
        return `<span class="agent-chat-frame-status is-bound" title="会话框：${escapeHtml(title)}">会话框 ✓ ${escapeHtml(short)}</span>`;
    }
    return '<span class="agent-chat-frame-status is-unbound" title="未绑定会话框（点「创建会话框」建框，生成节点自动排入）">会话框 —</span>';
}

// E2：agent 卡「参考」按钮文案——解绑态「参考」，绑定态「参考 ×N」（N = 绑定框素材数）。
function agentRefFrameButtonLabel(agent){
    const frame = agentRefFrameGroup(agent);
    if(!frame) return '参考';
    return `参考 ×${agentRefFrameAssetCount(frame)}`;
}

// E2：agent 卡「参考」按钮点击循环：未绑定 → 第一个 refFrame=true group → 下一个 → … → 未绑定。
// 当前绑定不在参考框集合（如该组刚被取消参考框）时按未绑定处理（从第一个开始）。
// 无 refFrame group 时只更新按钮 title 提示（禁止 alert），不改绑定。写/清 agent.refFrameId + 保存。
function agentRefFrameCycle(agentId, buttonEl, {actor=null}={}){
    const agent = canvasState.nodes.find(n => n.id === agentId);
    if(!agent || !isSmartAgentNode(agent)) return null;
    const frames = canvasState.nodes.filter(n => isSmartGroupNode(n) && n.refFrame === true);
    if(!frames.length){
        if(buttonEl) buttonEl.title = '暂无参考框：右键画布上的分组选「设为参考框」';
        return {action:'none', refFrameId:String(agent.refFrameId || ''), frames:0};
    }
    const index = frames.findIndex(frame => frame.id === String(agent.refFrameId || ''));
    let nextId = '';
    if(index === -1) nextId = frames[0].id;
    else if(index === frames.length - 1) nextId = '';
    else nextId = frames[index + 1].id;
    const action = beginAgentCanvasHumanEdit([agent], ['refFrameId'], actor);
    if(action) canvasCore.pushUndo();
    if(nextId) agent.refFrameId = nextId; else delete agent.refFrameId;
    finishAgentCanvasHumanEdit(action);
    render();
    scheduleSave();
    return {action:'cycled', refFrameId:nextId, frames:frames.length};
}

function agentHeadActionButtonsHtml(node){
    return `<button type="button" class="agent-tui-btn agent-chat-head-button" data-agent-chat-open="search" aria-label="搜索本画布对话" title="搜索本画布对话">${agentIco('search')}</button><button type="button" class="agent-tui-btn agent-chat-head-button" data-agent-chat-open="history" aria-label="本画布历史会话" title="本画布历史会话">${agentIco('history')}</button><details class="agent-tui-more"><summary aria-label="更多会话操作">${agentIco('ellipsis')}</summary><div data-agent-more-content>${agentMoreActionsHtml(node)}</div></details>`;
}

function agentMoreActionsHtml(node) {
    const id = escapeAttr(node.id);
    const hasChatFrame = Boolean(agentChatFrameGroup(node));
    // 监理钉B2：文字钮直接说明动作——未绑定「创建会话框」、已绑定「打开会话框」（点击跳转）。
    const chatLabel = hasChatFrame ? '打开会话框' : '创建会话框';
    const frame = agentRefFrameGroup(node);
    const refTitle = frame
        ? `参考框：${frame.title || '智能分组'}（${agentRefFrameAssetCount(frame)} 个素材），点击切换下一个`
        : '绑定参考框（循环所有参考框）';
    const chatBtn = `<button type="button" class="agent-tui-btn agent-chat-frame-btn${hasChatFrame ? ' agent-tui-btn-active' : ''}" data-agent-chat-frame="${id}">${escapeHtml(chatLabel)}${hasChatFrame ? ` ${agentChatFrameStatusHtml(node)}` : ''}</button>`;
    const refBtn = `<button type="button" class="agent-tui-btn agent-ref-frame-btn${frame ? ' agent-tui-btn-active' : ''}" data-agent-ref-frame="${id}" title="${escapeHtml(refTitle)}" aria-label="${escapeHtml(refTitle)}">${escapeHtml(agentRefFrameButtonLabel(node))}</button>`;
    const state = getAgentTuiState(node.id), locked = agentIsReadonly(state) || agentParametersLocked(state);
    const branches = node.branches || [];
    const other = state.activeHost === 'pi' ? 'codex' : 'pi';
    const binding = node.agentHostSessions && node.agentHostSessions[other];
    const forkReason = agentSessionForkDisabled(state);
    return chatBtn + refBtn + agentDockButtonsHtml(node)
        + `<button type="button" class="agent-tui-btn" data-agent-thinking="${id}" aria-pressed="${Boolean(state.thinkingVisible)}">${agentIco('brain')} ${state.thinkingVisible ? '隐藏思考过程' : '显示思考过程'}</button>
        <button type="button" class="agent-tui-btn" data-agent-more-fork title="${escapeAttr(forkReason || '从历史轮次分叉')}"${forkReason ? ' disabled' : ''}>${agentIco('git-branch')} 从历史轮次分叉</button>
        ${branches.length && state.activeHost === 'pi' ? `<select class="agent-tui-branch-select" data-agent-more-branch aria-label="切换分支"${locked ? ' disabled' : ''}>${branches.map(branch => `<option value="${escapeAttr(branch.id)}" ${branch.id === node.current_branch ? 'selected' : ''}>${escapeHtml(branch.name)}</option>`).join('')}</select>` : ''}
        <button type="button" class="agent-tui-btn" data-agent-more-cli>${agentIco('terminal')} 由外部 CLI 接管</button>
        <button type="button" class="agent-tui-btn" data-agent-more-view="${other}"${binding ? '' : ' disabled'}>只读查看 ${other === 'pi' ? 'Pi' : 'Codex'} 会话</button>
        <button type="button" class="agent-tui-btn" data-agent-more-delete>${agentIco('trash-2')} ${escapeHtml(tr('smart.deleteNode'))}</button>`;
}
function bindAgentMoreControls(el, node) {
    const state = getAgentTuiState(node.id), projectId = canvasId || '';
    el.querySelectorAll('.agent-tui-more').forEach(menu => {
        menu.addEventListener('mousedown', event => event.stopPropagation());
        menu.addEventListener('click', event => event.stopPropagation());
        menu.addEventListener('keydown', event => {
            if (event.key !== 'Escape' || !menu.open) return;
            event.preventDefault();
            event.stopPropagation();
            closeAgentMoreMenu(menu);
        });
    });
    el.querySelectorAll('[data-agent-more-fork],[data-agent-more-cli],[data-agent-more-view],[data-agent-more-branch],[data-agent-thinking]').forEach(control => {
        control.addEventListener('mousedown', event => event.stopPropagation());
        control.addEventListener(control.hasAttribute('data-agent-more-branch') ? 'change' : 'click', event => {
            event.stopPropagation();
            if (control.disabled) return;
            closeAgentMoreMenu(control.closest('.agent-tui-more'));
            if (control.hasAttribute('data-agent-thinking')) {
                state.thinkingVisible = !state.thinkingVisible;
                state.thoughtExpanded = {};
                renderAgentNode(node.id);
            } else if (control.hasAttribute('data-agent-more-view')) {
                agentViewHost(node.id, projectId, control.getAttribute('data-agent-more-view'));
            } else if (control.hasAttribute('data-agent-more-cli')) showAgentCliTakeover(node.id, projectId);
            else if (!agentIsReadonly(state) && !agentParametersLocked(state) && state.activeHost === 'pi') {
                if (control.hasAttribute('data-agent-more-fork')) showForkPicker(node.id, projectId);
                else {
                    const branchId = control.value;
                    const live = canvasState.nodes.find(item => item.id === node.id);
                    if (!live) return;
                    // The select must not show a rejected/unfinished switch as the current branch.
                    control.value = live.current_branch ?? '';
                    if (branchId && branchId !== live.current_branch) switchAgentBranch(node.id, projectId, branchId);
                }
            }
        });
    });
}

// #407 D1：停靠按钮 HTML（浮动态两个、停靠态两个），与「创建会话框/参考框」同族文字钮。
function agentDockButtonsHtml(node){
    const id = escapeAttr(node.id);
    const dock = agentDockState(node);
    if(!dock){
        return `<button type="button" class="agent-tui-btn agent-dock-btn" data-agent-dock-set="${id}" data-agent-dock-side="left" title="停靠到画布左侧（不随画布缩放/平移）" aria-label="停靠左侧">停左</button>`
             + `<button type="button" class="agent-tui-btn agent-dock-btn" data-agent-dock-set="${id}" data-agent-dock-side="right" title="停靠到画布右侧（不随画布缩放/平移）" aria-label="停靠右侧">停右</button>`;
    }
    const collapseLabel = agentDockIsCollapsed(node.id) ? '展开' : '收起';
    return `<button type="button" class="agent-tui-btn agent-dock-btn" data-agent-dock-collapse="${id}" title="${collapseLabel === '展开' ? '展开停靠面板' : '收起为窄条'}" aria-label="${escapeHtml(collapseLabel)}">${escapeHtml(collapseLabel)}</button>`
         + `<button type="button" class="agent-tui-btn agent-dock-btn" data-agent-dock-undock="${id}" title="取回画布（拖节点头也可）" aria-label="取回落回浮动">取回</button>`;
}

function closeAgentMoreMenu(menu) {
    if (!menu?.open) return;
    const summary = menu.querySelector('summary');
    summary.focus({preventScroll: true});
    menu.open = false;
}

function bindAgentHeadControls(el, node){
    if(!el || !node) return;
    agentChatBridge()?.bind(el, node.id);
    el.querySelectorAll('[data-agent-chat-frame],[data-agent-ref-frame],[data-agent-dock-set],[data-agent-dock-collapse],[data-agent-dock-undock],[data-agent-more-delete]').forEach(btn => {
        btn.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            if (btn.hasAttribute('data-agent-ref-frame')) {
                if (agentRefFrameCycle(node.id, btn, {actor:'user'})?.action === 'cycled') closeAgentMoreMenu(btn.closest('.agent-tui-more'));
                return;
            }
            closeAgentMoreMenu(btn.closest('.agent-tui-more'));
            if (btn.hasAttribute('data-agent-chat-frame')) agentChatFrameAction(node.id, {actor:'user'});
            else if (btn.hasAttribute('data-agent-dock-set')) agentDockSet(node.id, btn.dataset.agentDockSide, {actor:'user'});
            else if (btn.hasAttribute('data-agent-dock-collapse')) agentDockCollapseToggle(node.id);
            else if (btn.hasAttribute('data-agent-dock-undock')) agentDockSet(node.id, null, {actor:'user'});
            else if (btn.hasAttribute('data-agent-more-delete')) deleteNodeFromButton(node.id);
        });
    });
}

// 这些名字本来属于本模块，#200 从 smart-canvas.js 搬回（核心不引用它们，纯搬家）。
function renderMarkdown(text){
    if(text == null) return '';
    let html = escapeHtml(String(text));
    // #252 修复围栏渲染：先用占位符保护代码块/内联码，避免后续全局 \n→<br>
    // 污染 <pre> 内部（行距翻倍）；流式输出未闭合围栏（``` 到文末）也按代码块
    // 处理，不再把 ``` 当转义文本显示；围栏首行语言名不再混进代码正文。
    const mdBlocks = [];
    html = html.replace(/```[^\S\n]*([A-Za-z0-9+#._-]*)\n?([\s\S]*?)(?:```|$)/g, (match, lang, body) => {
        const trimmed = body.replace(/\n+$/, '');
        if (!trimmed.trim()) return '';
        const langAttr = /^[A-Za-z0-9+#._-]+$/.test(lang || '') ? ` data-lang="${lang}"` : '';
        mdBlocks.push(`<pre class="agent-md-code-block"${langAttr}><code>${trimmed}</code></pre>`);
        return `\u0000MDB${mdBlocks.length - 1}\u0000`;
    });
    const mdInlines = [];
    html = html.replace(/`([^`\n]+)`/g, (match, body) => {
        mdInlines.push(`<code class="agent-md-inline-code">${body}</code>`);
        return `\u0000MDI${mdInlines.length - 1}\u0000`;
    });
    // Bold (**text**)
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Italic (*text*) - must be after bold
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    // Links [text](url) - only allow safe protocols (block javascript: etc.)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
        const safeUrl = /^(https?:|mailto:|\/|#|\.\/|\.\.\/)/i.test(url) ? url : '#';
        return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${text}</a>`;
    });
    // Flat lists; keep them outside paragraphs like fenced code. An explicitly
    // hard-broken bold lead plus indented text renders as a heading and description.
    const lines = html.split(/\r?\n/);
    const listMarker = /^( {0,3}(\d{1,9}\.|-)[ \t]+)(\S.*)$/;
    const parsed = [];
    for (let i = 0; i < lines.length;) {
        let item = listMarker.exec(lines[i]);
        if (!item) { parsed.push(lines[i++]); continue; }
        const ordered = item[2] !== '-';
        const tag = ordered ? 'ol' : 'ul';
        const first = ordered ? Number.parseInt(item[2], 10) : 1;
        const start = first !== 1 ? ` start="${first}"` : '';
        const items = [];
        do {
            const indent = ' '.repeat(item[1].length);
            const lead = item[3];
            const rest = [];
            for (i++; i < lines.length && lines[i].startsWith(indent) && lines[i].trim() && !listMarker.test(lines[i]); i++) {
                rest.push(lines[i].slice(indent).trim());
            }
            const hardBreak = / {2,}$/.test(lead);
            const continuation = rest.join(' ');
            const body = !continuation ? lead.trimEnd()
                : ordered && hardBreak && /^<strong>.+<\/strong>$/.test(lead.trimEnd())
                    ? `${lead.trimEnd()}<span>${continuation}</span>`
                    : `${lead.trimEnd()}${hardBreak ? '<br>' : ' '}${continuation}`;
            items.push(`<li>${body}</li>`);
            item = i < lines.length ? listMarker.exec(lines[i]) : null;
        } while (item && (item[2] !== '-') === ordered);
        mdBlocks.push(`<${tag} class="agent-md-list"${start}>${items.join('')}</${tag}>`);
        parsed.push(`\u0000MDB${mdBlocks.length - 1}\u0000`);
    }
    html = parsed.join('\n');
    // Paragraphs (double newline)
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    html = '<p>' + html + '</p>';
    // Restore protected block snippets outside paragraphs, not as their children.
    html = html.replace(/(?:<br>)*\u0000MDB(\d+)\u0000(?:<br>)*/g, (match, index) => `</p>${mdBlocks[Number(index)] || ''}<p>`);
    html = html.replace(/<p><\/p>/g, '');
    html = html.replace(/\u0000MDI(\d+)\u0000/g, (match, index) => mdInlines[Number(index)] || '');
    return html;
}
function getChatModels(){
    // #285：优先用 pi 会话模型池（/agent-nodes/chat-models 异步加载）。
    // 旧回退：Firefly 生成模型池——其中含图片端点，选中即会话无响应（#285 根因）。
    if (agentChatModelPool.models) return agentChatModelPool.models.slice();
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

// #285：会话模型池（只读自 pi 的 models.json），加载完成前用旧回退池。
const agentChatModelPool = { models: null };
function applyAgentChatModelPool(payload){
    const list = payload && Array.isArray(payload.models) ? payload.models : null;
    agentChatModelPool.models = list
        ? list.map(m => ({ id: m.id, name: m.name && m.name !== m.model ? `${m.provider}/${m.name}` : m.id }))
        : null;
    return agentChatModelPool.models;
}
function fetchAgentChatModels(projectId){
    // cache:no-store —— 否则浏览器启发式缓存会回放旧池（实测：改 models.json+重启后
    // 下拉仍是旧模型 ID，选不到新选项）。
    return fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/chat-models`, {cache: 'no-store'})
        .then(r => (r && r.ok ? r.json() : Promise.reject(new Error('chat-models HTTP ' + (r && r.status)))))
        .then(payload => applyAgentChatModelPool(payload));
}
