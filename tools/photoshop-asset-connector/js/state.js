window.DX = window.DX || {};
DX.state = {
  host: localStorage.getItem('firefly.ps.host') || '127.0.0.1:3000',
  clientId: localStorage.getItem('firefly.ps.clientId') || `ps_${Date.now()}_${Math.random().toString(16).slice(2)}`,
  // Project/source IDs are intentionally not global preferences.  They are
  // restored only as part of an active, one-time binding below.
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
