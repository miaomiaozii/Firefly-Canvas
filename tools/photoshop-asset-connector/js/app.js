/* 萤火画布 PS 连接器：连接 → 发送一次绑定源 → 返回一次结果 → 展示状态。 */
(function () {
  const state = DX.state, net = DX.net, ps = DX.ps;
  let reconnectTimer = null;
  let reconnectDelay = 1000;
  let sendInFlight = false;
  let wsAlive = false;
  const roundTripQueue = [];
  const $ = id => document.getElementById(id);
  const els = {
    host: $('host'), project: $('projectId'), sourceNode: $('sourceNodeId'), sourceMedia: $('sourceMediaId'),
    connect: $('connect'), send: $('send'), returnBtn: $('return'), scope: $('returnScope'), name: $('returnName'),
    dot: $('connectionDot'), connectionStatus: $('connectionStatus'), requestStatus: $('requestStatus'),
    returnStatus: $('returnStatus'), roundState: $('roundTripState'), refresh: $('refresh'),
  };

  function message(el, text, kind = '') { el.textContent = text; el.className = `status ${kind}`; }

  function syncFields() {
    let parsedHost;
    try { parsedHost = net.parseHost(els.host.value); }
    catch (error) { state.host = ''; els.send.disabled = true; throw error; }
    state.host = parsedHost;
    state.projectId = els.project.value.trim();
    state.sourceNodeId = els.sourceNode.value.trim();
    state.sourceMediaId = els.sourceMedia.value.trim();
    localStorage.setItem('firefly.ps.host', state.host);
    els.send.disabled = !(state.connected && wsAlive && state.projectId && state.sourceNodeId && state.sourceMediaId);
  }

  function lockBinding(locked) {
    [els.project, els.sourceNode, els.sourceMedia].forEach(input => { input.disabled = locked; });
  }

  function isStaleRoundTrip(error) {
    return Number(error?.status) === 404 || Number(error?.status) === 410 || ['REQUEST_NOT_FOUND', 'REQUEST_EXPIRED'].includes(String(error?.code || ''));
  }

  function clearActiveRoundTrip(text) {
    state.roundTrip = null;
    state.activatingRequestId = '';
    state.projectId = ''; state.sourceNodeId = ''; state.sourceMediaId = '';
    localStorage.removeItem('firefly.ps.roundTrip');
    els.project.value = ''; els.sourceNode.value = ''; els.sourceMedia.value = '';
    lockBinding(false); els.returnBtn.disabled = true; els.roundState.textContent = '无活动往返';
    try { syncFields(); } catch (_) { els.send.disabled = true; }
    if (text) message(els.returnStatus, text, 'error');
    drainRoundTripQueue();
  }

  function drainRoundTripQueue() {
    if (state.roundTrip || state.activatingRequestId || !roundTripQueue.length) return;
    const next = roundTripQueue.shift();
    if (next) setTimeout(() => activateRoundTrip(next), 0);
  }

  function scheduleReconnect() {
    if (reconnectTimer || !state.host) return;
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(false); }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  }

  function markConnected(ok, text) {
    state.connected = ok;
    els.dot.className = `dot ${ok ? 'on' : 'err'}`;
    message(els.connectionStatus, text, ok ? 'ok' : 'error');
    try { syncFields(); } catch (_) { els.send.disabled = true; }
    if (ok) {
      reconnectDelay = 1000;
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    } else {
      scheduleReconnect();
    }
  }

  function basePath() { return `/api/project-library/${encodeURIComponent(state.projectId)}/photoshop-round-trips`; }

  function decodeBase64(value) {
    const binary = atob(String(value || ''));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  async function activateRoundTrip(binding) {
    const requestId = String(binding?.request_id || '').trim();
    if (!requestId || state.roundTrip?.request_id === requestId || state.activatingRequestId === requestId) return;
    if (state.roundTrip || state.activatingRequestId) {
      if (!roundTripQueue.some(item => item.request_id === requestId)) roundTripQueue.push(binding);
      message(els.requestStatus, `有活动往返，已排入队列 ${roundTripQueue.length} 项 Photoshop 请求`);
      return;
    }
    state.activatingRequestId = requestId;
    try {
      state.projectId = String(binding.project_id || '').trim();
      state.sourceNodeId = String(binding.source_node_id || '').trim();
      state.sourceMediaId = String(binding.source_media_id || '').trim();
      els.project.value = state.projectId; els.sourceNode.value = state.sourceNodeId; els.sourceMedia.value = state.sourceMediaId;
      syncFields();
      const path = `/api/project-library/${encodeURIComponent(state.projectId)}/photoshop-round-trips`;
      const source = await net.request('GET', `${path}/${encodeURIComponent(requestId)}/source`);
      const documentId = await ps.placeSource(source);
      state.roundTrip = { ...binding, project_id: state.projectId, source_node_id: state.sourceNodeId, source_media_id: state.sourceMediaId, document_id: documentId || '' };
      localStorage.setItem('firefly.ps.roundTrip', JSON.stringify(state.roundTrip));
      lockBinding(true); els.returnBtn.disabled = !ps.hasDocument(); els.roundState.textContent = `已激活 ${requestId}`;
      message(els.requestStatus, '源图已放置到 Photoshop，可开始编辑后返回。', 'ok');
    } catch (error) {
      console.error('[firefly-ps] activate FAILED', error?.message || String(error));
      if (isStaleRoundTrip(error)) clearActiveRoundTrip('该 Photoshop 请求已过期或已返回，请从画布重新发送。');
      else message(els.requestStatus, error.message || '获取源图失败', 'error');
    }
    finally { state.activatingRequestId = ''; drainRoundTripQueue(); }
  }

  function connectSocket() {
    net.openSocket(activateRoundTrip, ok => {
      wsAlive = ok;
      if (!ok && state.connected) markConnected(false, 'Photoshop 实时通道已断开，正在自动恢复…');
      else if (ok && state.connected) { try { syncFields(); } catch (_) {} }
    });
  }

  async function connect(manual = true) {
    if (manual && reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; reconnectDelay = 1000; }
    try {
      net.parseHost(els.host.value);
      syncFields();
      await net.request('GET', '/api/config');
      markConnected(true, '已连接到萤火画布服务');
      connectSocket();
    } catch (error) {
      markConnected(false, error.message || '连接失败');
    }
  }

  async function send() {
    syncFields();
    if (els.send.disabled || sendInFlight) return;
    sendInFlight = true; els.send.disabled = true;
    try {
      message(els.requestStatus, '正在创建一次往返请求…');
      const created = await net.request('POST', basePath(), { source_node_id: state.sourceNodeId, source_media_id: state.sourceMediaId, ttl_seconds: 300 });
      await activateRoundTrip(created);
    } catch (error) {
      message(els.requestStatus, error.message || '发送失败', 'error');
    }
    finally { sendInFlight = false; if (!state.roundTrip) { try { syncFields(); } catch (_) {} } }
  }

  async function returnToProject() {
    if (!state.roundTrip) return;
    const binding = state.roundTrip;
    try {
      els.returnBtn.disabled = true;
      message(els.returnStatus, '正在导出并返回…');
      const exported = await ps.exportCurrent(els.scope.value, binding.document_id);
      const buffer = decodeBase64(exported.bytes_base64);
      const boundPath = `/api/project-library/${encodeURIComponent(binding.project_id)}/photoshop-round-trips`;
      const response = await net.request('POST', `${boundPath}/${encodeURIComponent(binding.request_id)}/return`, {
        project_id: binding.project_id, source_node_id: binding.source_node_id, request_id: binding.request_id,
        name: els.name.value.trim() || `${exported.name || 'photoshop-edit'}.png`,
        mime_type: exported.mime_type || 'image/png', sha256: await net.sha256(buffer), bytes_base64: exported.bytes_base64,
      });
      els.roundState.textContent = `已返回 ${response.result_node.id}`;
      message(els.returnStatus, '已创建新的 PS 编辑结果节点，原图保持不变。', 'ok');
      state.roundTrip = null;
      localStorage.removeItem('firefly.ps.roundTrip');
      lockBinding(false);
      drainRoundTripQueue();
    } catch (error) {
      if (isStaleRoundTrip(error)) {
        clearActiveRoundTrip('该 Photoshop 请求已过期或已返回，请从画布重新发送。');
      } else {
        els.returnBtn.disabled = false;
        message(els.returnStatus, error.message || '返回失败', 'error');
      }
    }
  }

  async function refresh() {
    if (!state.roundTrip) return;
    const binding = state.roundTrip;
    try {
      const boundPath = `/api/project-library/${encodeURIComponent(binding.project_id)}/photoshop-round-trips`;
      const value = await net.request('GET', `${boundPath}/${encodeURIComponent(binding.request_id)}`);
      els.roundState.textContent = `${value.state} · ${value.request_id}`;
    } catch (error) {
      if (isStaleRoundTrip(error)) clearActiveRoundTrip('该 Photoshop 请求已过期或已返回，状态已清空。');
      else message(els.returnStatus, error.message || '状态获取失败', 'error');
    }
  }

  [els.host, els.project, els.sourceNode, els.sourceMedia].forEach(input => input.addEventListener('input', () => { try { syncFields(); } catch (_) {} }));
  els.connect.addEventListener('click', connect);
  els.send.addEventListener('click', send);
  els.returnBtn.addEventListener('click', returnToProject);
  els.refresh.addEventListener('click', refresh);
  ps.onDocChange(() => { els.returnBtn.disabled = !state.roundTrip || !ps.hasDocument(); });

  if (state.roundTrip) {
    state.projectId = String(state.roundTrip.project_id || '');
    state.sourceNodeId = String(state.roundTrip.source_node_id || '');
    state.sourceMediaId = String(state.roundTrip.source_media_id || '');
  }
  els.host.value = state.host; els.project.value = state.projectId; els.sourceNode.value = state.sourceNodeId; els.sourceMedia.value = state.sourceMediaId;
  lockBinding(Boolean(state.roundTrip)); syncFields();
  if (state.roundTrip) {
    els.returnBtn.disabled = !ps.hasDocument();
    els.roundState.textContent = `已绑定 ${state.roundTrip.request_id}`;
  }
  // 恢复连接只探测健康端点，绝不创建或重放往返请求；用户可点连接重置退避。
  connect(false);
})();
