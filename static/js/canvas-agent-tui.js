// 画布 Agent TUI 会话节点（从 smart-canvas.js 抽离，#154）。
// invariant: classic script，按 HTML 顺序先于 smart-canvas.js 加载；本文件顶层只有声明，
// 初始化器只取空容器或字面量，不读其他模块的状态。函数声明整体提升，实际调用都发生在
// 主脚本初始化之后。跨模块可变状态一律走 canvasState（window.canvasState），不要引用裸 nodes/selectedId。

function isSmartAgentNode(node){
    return Boolean(node && node.type === NODE_TYPE_AGENT);
}

function isSmartAgentTaskNode(node){
    return Boolean(node && node.type === NODE_TYPE_TASK);
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
            pendingImages: [], // #268：待随下一条 prompt 发出的附件（图片/视频 contact sheet/音频波形卡 dataURL）
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
    const wsUrl = `${protocol}//${host}/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/stream`;

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
        finalizeAgentStreamingTurn(state);
        // Re-render to show disconnected state
        renderAgentNode(nodeId);
    };

    state.socket.onerror = () => {
        state.status = 'error';
        finalizeAgentStreamingTurn(state);
    };

    state.status = 'connecting';
    return state.socket;
}

// 本轮结束（turn_end）或本轮永远结束不了（进程崩溃 / WS 断开 / 出错）时必须把末条
// 流式消息定型：streaming 标记只在追加增量时写入且之前没人清，下一轮的
// text_delta / thinking_delta 就会续写到上一轮末尾（#222 的用户可见症状）。
function finalizeAgentStreamingTurn(state){
    const lastMessage = state.messages[state.messages.length - 1];
    if (lastMessage && lastMessage.streaming) delete lastMessage.streaming;
    // 默认 state 里没有 thinking（它在 thinking_delta 分支里懒建），取下标前先取好数组。
    const thinking = state.thinking || [];
    const lastThinking = thinking[thinking.length - 1];
    if (lastThinking && lastThinking.streaming) delete lastThinking.streaming;
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
            // #222：streaming 标记只在追加增量时写入，之前没有任何地方清它。
            // 后果是下一轮的 text_delta / thinking_delta 直接续写到上一轮末尾，
            // 相邻两轮回复在历史区显示成一条。turn_end 就是「本轮结束」的边界，
            // 在这里把末条流式消息定型。
            const lastMessage = state.messages[state.messages.length - 1];
            const streamedThisTurn = Boolean(lastMessage && lastMessage.role === 'assistant' && lastMessage.streaming);
            finalizeAgentStreamingTurn(state);
            if (text && streamedThisTurn && lastMessage && !lastMessage.text) {
                // delta 全是空串时末条是空的，无条件跳过 turn_end 会让本轮一个字都不显示。
                lastMessage.text = text;
            } else if (text && !streamedThisTurn) {
                // 流式增量已经把文本写进历史，turn_end 再推一遇就是重复回复。
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
            finalizeAgentStreamingTurn(state);
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
    // #268：images 可能是 {dataUrl,name,caption} 对象，发给后端的 payload 统一为 dataURL 字符串列表
    const imageList = (images || []).map(img => (typeof img === 'string' ? img : (img && img.dataUrl) || ''))
        .filter(Boolean);

    // Add user message to state（#268：记录附件名，消息可追溯）
    state.messages.push({ role: 'user', text: message,
        attachments: (images || []).map(img => (img && img.name) || '').filter(Boolean) });
    state.status = 'busy';

    // Send via WebSocket if connected
    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(JSON.stringify({
            type: 'prompt',
            message: message,
            images: imageList,
        }));
    } else {
        // WebSocket not connected, use HTTP API
        fetch(`/api/projects/${encodeURIComponent(projectId)}/agent-nodes/${encodeURIComponent(nodeId)}/prompt`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, images: imageList }),
        }).catch(e => console.error('Failed to send agent prompt:', e));
    }

    renderAgentNode(nodeId);
}

// ---- #268：聊天附件（图片/视频/音频） ----
// 图片 → dataURL 直发；视频 → 后端 contact sheet（抽帧+时间戳拼贴）；
// 音频 → 后端波形卡（保守版：不转写，transcription=None）。
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
        try { detail = (await resp.json()).detail || detail; } catch (e) { /* 非 JSON 错误体 */ }
        throw new Error(detail);
    }
    return resp.json();
}

async function handleAgentAttachments(nodeId, projectId, files) {
    const state = getAgentTuiState(nodeId);
    if (!Array.isArray(state.pendingImages)) state.pendingImages = [];
    for (const file of files) {
        const name = file.name || 'attachment';
        try {
            const mime = (file.type || '').toLowerCase();
            if (mime.startsWith('image/')) {
                const dataUrl = await readAsDataURL(file);
                state.pendingImages.push({ dataUrl, name, caption: '' });
            } else if (mime.startsWith('video/')) {
                const body = await convertAgentMediaAttachment(file);
                const ts = body.frames_at_s || [];
                const range = ts.length ? `${ts[0]}s–${ts[ts.length - 1]}s` : '';
                state.pendingImages.push({ dataUrl: body.data_url, name,
                    caption: tr('smart.agentAttachVideoReady')
                        .replace('{name}', name).replace('{frames}', String(ts.length)).replace('{range}', range) });
            } else if (mime.startsWith('audio/')) {
                const body = await convertAgentMediaAttachment(file);
                state.pendingImages.push({ dataUrl: body.data_url, name,
                    caption: tr('smart.agentAttachAudioReady').replace('{name}', name) });
            } else {
                state.messages.push({ role: 'system',
                    text: tr('smart.agentAttachUnsupported').replace('{name}', name) });
            }
        } catch (e) {
            console.error('Failed to prepare attachment:', name, e);
            state.messages.push({ role: 'system',
                text: tr('smart.agentAttachFailed').replace('{name}', name).replace('{err}', String(e.message || e)) });
        }
    }
    renderAgentNode(nodeId);
}

function sendAgentCommand(nodeId, projectId, command, options = {}) {
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
        fetch(url, fetchOpts).then(() => {
            if (onSuccess) onSuccess(state);
            renderAgentNode(nodeId);
        }).catch(e => {
            console.error(`Failed to send agent ${command}:`, e);
            if (onFailure) onFailure(state, e);
            renderAgentNode(nodeId);
        });
        return;
    }
    state.socket.send(JSON.stringify(wsPayload));
    if (optimistic) optimistic(state);
    if (onSuccess) onSuccess(state);
    renderAgentNode(nodeId);
}

function sendAgentSteer(nodeId, projectId, message) {
    sendAgentCommand(nodeId, projectId, 'steer', {
        body: { message },
        wsPayload: { type: 'steer', message },
        optimistic: (state) => state.messages.push({ role: 'system', text: `↪ steer: ${message}` }),
        onFailure: (state) => state.messages.push({ role: 'system', text: '⚠️ steer 请求未送达，可能没生效' }),
    });
}

function sendAgentFollowUp(nodeId, projectId, message) {
    sendAgentCommand(nodeId, projectId, 'follow_up', {
        body: { message },
        wsPayload: { type: 'follow_up', message },
        optimistic: (state) => state.messages.push({ role: 'system', text: `⏳ follow_up queued: ${message}` }),
        onFailure: (state) => state.messages.push({ role: 'system', text: '⚠️ follow_up 请求未送达，可能没生效' }),
    });
}

function sendAgentAbort(nodeId, projectId) {
    sendAgentCommand(nodeId, projectId, 'abort', {
        wsPayload: { type: 'abort' },
        // 复位 idle 同步做（否则按钮卡在「运行中」），但「已停止」这句必须等请求真送达。
        optimistic: (state) => { state.status = 'idle'; },
        onSuccess: (state) => state.messages.push({ role: 'system', text: '⏹ aborted' }),
        onFailure: (state) => state.messages.push({ role: 'system', text: '⚠️ 停止请求未送达，agent 可能仍在生成' }),
    });
}

function setAgentModel(nodeId, projectId, model) {
    const state = getAgentTuiState(nodeId);
    state.model = model;
    // Optimistically update the local node (source of truth for rendering)
    const node = canvasState.nodes.find(n => n.id === nodeId);
    if (node) node.model = model;
    fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/${encodeURI(nodeId)}/model`, {
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
    fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/${encodeURI(nodeId)}/autonomy`, {
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
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/${encodeURI(nodeId)}/fork`, {
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
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/${encodeURI(nodeId)}/switch-branch`, {
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
        const response = await fetch(`/api/projects/${encodeURI(projectId)}/agent-nodes/${encodeURI(nodeId)}/fork-messages`);
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
        // #241：fallback 目标是真实存在的 agent-nodes/{nodeId}/confirm 端点
        //（app/routers/api_agent_nodes.py，与 WS 确认同一状态机）；
        // HTTP 错误状态不再静默吞——至少 console.warn 让 WS 断线时的失败可见。
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
    state.messages.push({ role: 'system', text });
    renderAgentNode(nodeId);
}

// Get status light + last line preview HTML for agent node card
// #220：卡片状态行和节点正文原来各写一遇 switch，两处会各自漏 case——
// retrying / crashed 就是卡片侧漏的，结果重试中和已崩溃都显示成「空闲」。
// 状态文案只留一份表；confirm 覆盖只在卡片侧（正文另有 confirm 区），
// 跟原来一致，不进这张表。
const AGENT_STATUS_PRESENTATION = {
    busy: { statusClass: 'agent-tui-status-busy', statusText: '运行中' },
    connecting: { statusClass: 'agent-tui-status-connecting', statusText: '连接中' },
    retrying: { statusClass: 'agent-tui-status-busy', statusText: '重试中' },
    error: { statusClass: 'agent-tui-status-error', statusText: '错误' },
    crashed: { statusClass: 'agent-tui-status-error', statusText: '已崩溃' },
    disconnected: { statusClass: 'agent-tui-status-disconnected', statusText: '已断开' },
};
const AGENT_STATUS_IDLE = { statusClass: 'agent-tui-status-idle', statusText: '空闲' };

function agentStatusPresentation(state) {
    return AGENT_STATUS_PRESENTATION[state && state.status] || AGENT_STATUS_IDLE;
}

function agentCardStatusHtml(nodeId) {
    const state = agentTuiState.get(nodeId);
    if (!state) {
        return '<span class="agent-card-status agent-tui-status-idle">空闲</span>';
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
    return `<span class="agent-card-status ${statusClass}">${statusText}</span>${previewHtml}`;
}

function agentNodeBodyHtml(node) {
    const nodeId = node.id;
    const state = getAgentTuiState(nodeId);
    const projectId = canvasId || '';

    // Determine status indicator
    let { statusClass, statusText } = agentStatusPresentation(state);

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
            // #268：附件名列表（小字跟在消息下方）
            const attachNames = (msg.attachments || []).filter(Boolean);
            const attachHtml = attachNames.length
                ? `<div class="agent-tui-msg-attach">📎 ${attachNames.map(escapeHtml).join(' · ')}</div>` : '';
            messagesHtml += `<div class="agent-tui-msg agent-tui-msg-user"><span class="agent-tui-msg-role">&gt;</span> <span class="agent-tui-msg-text">${escapeHtml(msg.text)}</span>${attachHtml}</div>`;
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

    // #268：待发送附件行（缩略图 + 文件名 + 说明 + 移除）
    const pendingImages = state.pendingImages || [];
    let pendingHtml = '';
    if (pendingImages.length) {
        const chips = pendingImages.map((img, idx) => `
            <div class="agent-tui-attach">
                <img class="agent-tui-attach-thumb" src="${escapeAttr(img.dataUrl)}" alt="${escapeAttr(img.name)}">
                <div class="agent-tui-attach-meta">
                    <div class="agent-tui-attach-name">${escapeHtml(img.name)}</div>
                    ${img.caption ? `<div class="agent-tui-attach-caption">${escapeHtml(img.caption)}</div>` : ''}
                </div>
                <button type="button" class="agent-tui-attach-remove" data-agent-attach-remove="${idx}" title="${tr('smart.agentAttachRemove')}">×</button>
            </div>`).join('');
        pendingHtml = `<div class="agent-tui-attach-row">${chips}</div>`;
    }

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
        ${pendingHtml}
        <div class="agent-tui-input-row">
            <span class="agent-tui-prompt">&gt;</span>
            <textarea class="agent-tui-input" data-agent-tui-input="${escapeAttr(nodeId)}" placeholder="输入指令... (Enter 发送 / Shift+Enter 换行，运行中 Enter 排队)" rows="1"></textarea>
            <div class="agent-tui-action-buttons">
                <button type="button" class="agent-tui-btn agent-tui-btn-attach" data-agent-attach="${escapeAttr(nodeId)}" title="${tr('smart.agentAttachFile')}">📎</button>
                <button type="button" class="agent-tui-btn agent-tui-btn-thinking" data-agent-thinking="${escapeAttr(nodeId)}" title="显示/隐藏思考过程" ${state.thinkingVisible ? 'agent-tui-btn-active' : ''}>💭</button>
                <button type="button" class="agent-tui-btn agent-tui-btn-steer" data-agent-steer="${escapeAttr(nodeId)}" title="Steer (重定向当前回合)" ${state.status !== 'busy' ? 'disabled' : ''}>↪</button>
                <button type="button" class="agent-tui-btn agent-tui-btn-followup" data-agent-followup="${escapeAttr(nodeId)}" title="Follow-up (排队后续指令)" ${state.status !== 'busy' ? 'disabled' : ''}>⏳</button>
                <button type="button" class="agent-tui-btn agent-tui-btn-abort" data-agent-abort="${escapeAttr(nodeId)}" title="Abort (中止当前回合)" ${state.status !== 'busy' ? 'disabled' : ''}>⏹</button>
            </div>
            <input type="file" class="agent-tui-file-input" data-agent-tui-file="${escapeAttr(nodeId)}" accept="image/*,video/*,audio/*" multiple style="display:none">
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
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { // #252：IME 候选确认回车不触发发送
                event.preventDefault();
                const message = inputEl.value.trim();
                if (message) {
                    const inputState = getAgentTuiState(nodeId);
                    if (inputState && inputState.status === 'busy') {
                        // #268：follow-up 通道无 images 参数，附件留在队列随下一条 prompt 发出
                        sendAgentFollowUp(nodeId, projectId, message);
                    } else {
                        // #268：带上待发附件（图片/视频 contact sheet/音频波形卡）
                        const pending = (inputState && inputState.pendingImages) ? inputState.pendingImages.slice() : [];
                        if (inputState) inputState.pendingImages = [];
                        sendAgentPrompt(nodeId, projectId, message, pending);
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

    // #268：附件按钮 + 隐藏 file input + 待发送附件移除
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

function cleanupAgentTuiState(nodeId) {
    const state = agentTuiState.get(nodeId);
    if (state) {
        if (state.socket) {
            try { state.socket.close(); } catch (e) {}
        }
        agentTuiState.delete(nodeId);
    }
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
    // Unordered lists (- item)
    html = html.replace(/(^|\n)- (.+)/g, '$1<li>$2</li>');
    html = html.replace(/(<li>.*?<\/li>)(\s*(?:<li>.*?<\/li>))+/g, '<ul class="agent-md-list">$1</ul>');
    // Paragraphs (double newline)
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    html = '<p>' + html + '</p>';
    // Clean up empty paragraphs
    html = html.replace(/<p><\/p>/g, '');
    // Restore protected snippets last: their content is already escaped.
    html = html.replace(/\u0000MDI(\d+)\u0000/g, (match, index) => mdInlines[Number(index)] || '');
    html = html.replace(/\u0000MDB(\d+)\u0000/g, (match, index) => mdBlocks[Number(index)] || '');
    return html;
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
