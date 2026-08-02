window.DX = window.DX || {};
DX.state = {
  host: localStorage.getItem('firefly.ps.host') || '127.0.0.1:3000',
  clientId: localStorage.getItem('firefly.ps.clientId') || `ps_${Date.now()}_${Math.random().toString(16).slice(2)}`,
  // 项目 / 源节点 / 源媒体 ID 不属于全局偏好：只在一次性往返绑定时恢复，
  // 避免把一次会话的上下文泄漏给下一次。
  projectId: '',
  sourceNodeId: '',
  sourceMediaId: '',
  connected: false,
  roundTrip: (() => {
    try { return JSON.parse(localStorage.getItem('firefly.ps.roundTrip') || 'null'); }
    catch (_) { return null; }
  })(),
};
localStorage.setItem('firefly.ps.clientId', DX.state.clientId);
