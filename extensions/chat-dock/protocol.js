(() => {
  'use strict';

  const CHANNEL = 'firefly.chat-capture.v1';
  const DEFAULT_ORIGIN = 'http://127.0.0.1:3000';
  const ORIGIN_KEY = 'fireflyOrigin';
  const CANVAS_PATH = '/static/smart-canvas.html';
  const RELAY_ID = 'firefly-chat-capture-canvas';
  const SET_ORIGIN = 'firefly.chat-capture.set-origin';
  const MAX_TEXT = 20_000;
  const MAX_BYTES = 8 * 1024 * 1024;
  const SITES = new Set([
    'chatgpt.com', 'gemini.google.com', 'chat.deepseek.com', 'chat.qwen.ai',
    'www.doubao.com', 'www.kimi.com', 'yuanbao.tencent.com',
  ]);
  const DOCK_CAPTURE_SITES = new Set(['www.doubao.com', 'www.kimi.com', 'yuanbao.tencent.com']);
  // #515: a site that only renders in the panel through the user's per-site framing switch may capture there
  // exactly while that switch is on; `framed` is FireflyChatFraming.flags(), never page-supplied data.
  const DOCK_READY = 'firefly.chat-dock.capture-ready';
  const DOCK_PROBE = 'firefly.chat-dock.capture-probe';
  const DOCK_PROOF = 'firefly.chat-dock.capture-proof';
  const MIMES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);
  const ERRORS = new Set([
    'SELECT_ONE_IMAGE', 'CANVAS_NOT_READY', 'PROJECT_CHANGED', 'CAPTURE_FAILED',
    'INVALID_IMAGE', 'NO_FREE_SPOT', 'CHOOSE_CANVAS', 'IMAGE_UNAVAILABLE', 'PERMISSION_DENIED',
  ]);
  const STRINGS = {
    SELECT_ONE_IMAGE: '请先在画布中选中一个可编辑的生图节点。',
    CANVAS_NOT_READY: '请打开并刷新 Firefly 画布，确认项目已加载。',
    PROJECT_CHANGED: '画布已切换项目，未复制。',
    CAPTURE_FAILED: '复制失败，请重试。',
    INVALID_IMAGE: '仅支持不超过 8 MiB 的 PNG、JPEG、GIF 或 WebP 图片。',
    NO_FREE_SPOT: '画布上没有可放置新节点的空位。',
    CHOOSE_CANVAS: '打开了多个 Firefly 画布，请关闭多余的标签页后重试。',
    IMAGE_UNAVAILABLE: '无法读取这张图片：网站可能限制跨域读取。可以右键复制图片，再到画布按 Ctrl+V 粘贴。',
    PERMISSION_DENIED: '尚未授权此 Firefly 地址。',
  };

  function asURL(value) {
    if (typeof value !== 'string') return null;
    try { return new URL(value); } catch { return null; }
  }

  function chatSite(value) {
    const url = asURL(value);
    return url?.protocol === 'https:' && !url.port && !url.username && !url.password && SITES.has(url.hostname)
      ? url.hostname : null;
  }

  function parseOrigin(value) {
    const url = asURL(value);
    return url && (url.protocol === 'http:' || url.protocol === 'https:') && !url.username && !url.password &&
      !url.hash && !url.search && url.pathname === '/' ? url.origin : null;
  }

  function originSetting(stored) {
    return parseOrigin(stored?.[ORIGIN_KEY]) ?? DEFAULT_ORIGIN;
  }

  function isCanvasURL(value, origin) {
    const url = asURL(value);
    return Boolean(url && url.origin === origin && url.pathname === CANVAS_PATH && !url.username && !url.password);
  }

  function canvasPatterns(origin) {
    const url = new URL(origin);
    // Host permissions may cover every port; both routing and relay still check the exact configured origin.
    const host = `${url.protocol}//${url.hostname}`;
    return {permission: `${host}/*`, match: `${host}${CANVAS_PATH}*`};
  }

  function chooseCanvas(tabs, origin) {
    const matching = tabs.filter(tab => Number.isInteger(tab.id) && isCanvasURL(tab.url, origin) &&
      (!tab.pendingUrl || isCanvasURL(tab.pendingUrl, origin)));
    if (matching.length <= 1) return {id: matching[0]?.id, error: 'CANVAS_NOT_READY'};
    const active = matching.filter(tab => tab.active === true);
    return active.length === 1 ? {id: active[0].id} : {error: 'CHOOSE_CANVAS'};
  }

  function exactKeys(value, keys) {
    return Boolean(value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === keys.length &&
      keys.every(key => Object.hasOwn(value, key)));
  }

  function validEncodedImage(bytes) {
    if (typeof bytes !== 'string' || !bytes.length || bytes.length % 4 || bytes.length > Math.ceil(MAX_BYTES / 3) * 4) {
      return false;
    }
    const padding = bytes.endsWith('==') ? 2 : bytes.endsWith('=') ? 1 : 0;
    return bytes.length / 4 * 3 - padding <= MAX_BYTES &&
      !/[^A-Za-z0-9+/]/.test(padding ? bytes.slice(0, -padding) : bytes) && bytes.length > padding;
  }

  function validCapture(data, site) {
    if (!data || data.channel !== CHANNEL || !SITES.has(data.site) || (site !== undefined && data.site !== site)) return false;
    const keys = ['channel', 'action', 'site'];
    switch (data.action) {
      case 'status': return exactKeys(data, keys);
      case 'append-prompt':
      case 'new-text': return exactKeys(data, [...keys, 'text']) && typeof data.text === 'string' &&
        data.text.length <= MAX_TEXT && Boolean(data.text.trim());
      case 'new-image': return exactKeys(data, [...keys, 'mime', 'bytes']) && MIMES.has(data.mime) && validEncodedImage(data.bytes);
      default: return false;
    }
  }

  function dockChild() {
    return window !== window.top && window.parent === window.top &&
      location.ancestorOrigins?.length === 1 && location.ancestorOrigins[0] === `chrome-extension://${chrome.runtime.id}`;
  }

  function listenForDockProof(role) {
    window.addEventListener('message', event => {
      if (!event.isTrusted || event.source !== window.parent || event.origin !== `chrome-extension://${chrome.runtime.id}`) return;
      const data = event.data;
      if (!exactKeys(data, ['action', 'role', 'nonce']) || data.action !== DOCK_PROBE || data.role !== role ||
          typeof data.nonce !== 'string') return;
      chrome.runtime.sendMessage({action: DOCK_PROOF, role, nonce: data.nonce}).catch(() => {});
    });
  }

  function failure(error) { return {ok: false, error}; }

  function response(value) {
    if (!value || typeof value.ok !== 'boolean') return failure('CAPTURE_FAILED');
    if (!value.ok) return failure(ERRORS.has(value.error) ? value.error : 'CAPTURE_FAILED');
    return typeof value.canAppend === 'boolean' ? {ok: true, canAppend: value.canAppend} : {ok: true};
  }

  globalThis.FireflyChatProtocol = Object.freeze({
    CHANNEL, DEFAULT_ORIGIN, ORIGIN_KEY, CANVAS_PATH, RELAY_ID, SET_ORIGIN, MAX_TEXT, MAX_BYTES, DOCK_READY, DOCK_PROBE, DOCK_PROOF,
    dockChild, listenForDockProof, dockCaptureSite: (site, framed) => DOCK_CAPTURE_SITES.has(site) || (SITES.has(site) && framed?.[site] === true),
    chatSite, parseOrigin, originSetting, isCanvasURL, canvasPatterns, chooseCanvas, exactKeys, validCapture,
    failure, response, supportedMime: mime => MIMES.has(mime), errorText: error => STRINGS[error] ?? STRINGS.CAPTURE_FAILED,
  });
})();
