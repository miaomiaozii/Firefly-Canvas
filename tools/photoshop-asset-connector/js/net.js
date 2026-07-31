/* Loopback-only HTTP client for the Photoshop round-trip protocol. */
(function () {
  const state = DX.state;
  let socket = null;
  let heartbeat = null;
  function parseHost(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const candidate = /^[a-z][a-z\d+.-]*:\/\//i.test(raw) ? raw : `http://${raw}`;
    let parsed;
    try { parsed = new URL(candidate); }
    catch (_) { throw new Error('PS 连接器地址无效'); }
    if (parsed.protocol !== 'http:' || parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) {
      throw new Error('PS 连接器只允许本机 HTTP 地址');
    }
    const host = String(parsed.hostname || '').replace(/^\[|\]$/g, '').toLowerCase();
    if (host !== '127.0.0.1' && host !== 'localhost' && host !== '::1') {
      throw new Error('PS 连接器只允许 127.0.0.1');
    }
    const port = parsed.port;
    if (port && (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535)) {
      throw new Error('PS 连接器端口无效');
    }
    const displayHost = host.includes(':') ? `[${host}]` : host;
    return `${displayHost}${port ? `:${port}` : ''}`;
  }
  function base() { return `http://${state.host}`; }
  async function request(method, path, body) {
    const response = await fetch(`${base()}${path}`, {
      method, cache:'no-store', headers: body ? {'Content-Type':'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await response.text();
    let value = {}; try { value = JSON.parse(text || '{}'); } catch (_) { value = {detail:text}; }
    if (!response.ok) {
      const detail = value.detail || value.error || text || `HTTP ${response.status}`;
      const error = new Error(typeof detail === 'object' ? (detail.message || detail.code || JSON.stringify(detail)) : String(detail));
      error.status = response.status;
      error.code = typeof detail === 'object' ? String(detail.code || '') : '';
      throw error;
    }
    return value;
  }
  function toBase64(buffer) {
    const bytes = new Uint8Array(buffer); let out = '';
    for (let i=0; i<bytes.length; i+=0x8000) out += String.fromCharCode(...bytes.subarray(i, i+0x8000));
    return btoa(out);
  }
  async function sha256(value) {
    const digest = await crypto.subtle.digest('SHA-256', value);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2,'0')).join('');
  }
  function closeSocket() {
    if (heartbeat) { clearInterval(heartbeat); heartbeat = null; }
    if (socket) { try { socket.close(); } catch (_) {} socket = null; }
  }
  function openSocket(onRoundTrip, onStatus) {
    closeSocket();
    if (typeof WebSocket !== 'function' || !state.host) return false;
    try {
      socket = new WebSocket(`ws://${state.host}/ws/stats?client_id=${encodeURIComponent(state.clientId)}`);
      socket.onopen = () => {
        onStatus?.(true);
        heartbeat = setInterval(() => { try { socket?.send('ping'); } catch (_) {} }, 15000);
      };
      socket.onmessage = event => {
        try {
          const value = JSON.parse(event.data || '{}');
          if (value.type === 'photoshop_round_trip_created') onRoundTrip?.(value.data || {});
        } catch (_) {}
      };
      socket.onerror = () => onStatus?.(false);
      socket.onclose = () => { if (heartbeat) { clearInterval(heartbeat); heartbeat = null; } socket = null; onStatus?.(false); };
      return true;
    } catch (_) { onStatus?.(false); return false; }
  }
  DX.net = { parseHost, request, toBase64, sha256, openSocket, closeSocket };
})();
