/* 回环 HTTP/WS 客户端：地址校验、REST、base64、SHA-256、实时通道。 */
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
      throw new Error('PS 连接器只接受纯 HTTP 地址');
    }
    const host = String(parsed.hostname || '').replace(/^\[|\]$/g, '').toLowerCase();
    if (host !== '127.0.0.1' && host !== 'localhost' && host !== '::1') {
      throw new Error('PS 连接器只允许连接 127.0.0.1');
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
      method,
      cache: 'no-store',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await response.text();
    let value = {};
    try { value = JSON.parse(text || '{}'); } catch (_) { value = { detail: text }; }
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
    const bytes = new Uint8Array(buffer);
    let out = '';
    for (let i = 0; i < bytes.length; i += 0x8000) out += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(out);
  }

  // Photoshop/UXP 打不开的图片格式：走后端 /api/image-jpeg 转成 JPEG 再给 PS 用。
  // 大熊插件的同款策略（UXP_UNSUPPORTED），我们按 mime 而非 url 后缀判断。
  const UXP_UNSUPPORTED = /^(image\/(webp|bmp|avif|x-ms-bmp)|image\/tiff|image\/heic|image\/heif)/;
  function needsJpeg(mime) { return UXP_UNSUPPORTED.test(String(mime || '').toLowerCase().split(';', 1)[0]); }

  // 上传 base64 原图换一个可访问 url，再走后端转 JPEG（绕开 UXP 的 FormData，与本地素材导入同链）。
  async function jpegForBase64(bytesBase64, mime) {
    const uploaded = await request('POST', '/api/ai/upload-base64', { data: bytesBase64, name: 'firefly_source', content_type: mime || 'image/png' });
    const url = (uploaded.files || [])[0]?.url;
    if (!url) throw new Error('上传源图失败，后端未返回地址');
    const response = await fetch(`${base()}${'/api/image-jpeg?url='}${encodeURIComponent(url)}`);
    if (!response.ok) throw new Error(`图片格式转换失败 HTTP ${response.status}`);
    return { buffer: await response.arrayBuffer(), mime_type: 'image/jpeg' };
  }

  async function sha256(value) {
    const digest = await crypto.subtle.digest('SHA-256', value);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
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
      socket.onclose = () => {
        if (heartbeat) { clearInterval(heartbeat); heartbeat = null; }
        socket = null;
        onStatus?.(false);
      };
      return true;
    } catch (_) { onStatus?.(false); return false; }
  }

  DX.net = { parseHost, request, toBase64, needsJpeg, jpegForBase64, sha256, openSocket, closeSocket };
})();
