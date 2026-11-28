(() => {
  'use strict';

  const regularLayoutKey = 'firefly.chat-dock.layout.v1';
  const direct = FireflyChatDockDirect;
  const directMode = direct.isDirect(location.search);
  const SETTINGS = directMode ? 'firefly.chat-dock.direct-layout.v1' : regularLayoutKey;
  const P = FireflyChatProtocol;
  const embed = FireflyChatEmbed;
  const byId = id => document.getElementById(id);
  const shell = byId('dock-shell');
  shell.toggleAttribute('data-direct', directMode);
  const panel = byId('chat-dock-panel');
  const divider = byId('dock-divider');
  const move = byId('move-panel');
  const titleBar = panel.querySelector('.dock-heading');
  const resize = byId('resize-panel');
  const resizeHandles = [...panel.querySelectorAll('[data-resize]')];
  const defaultDockWidth = 420;
  const hide = byId('hide-panel');
  const reveal = byId('show-panel');
  const sitePicker = byId('chat-site');
  const modeButtons = [...panel.querySelectorAll('button[data-mode]')];
  const sites = Object.keys(embed.names);
  function readLayout(key) {
    try { return JSON.parse(localStorage.getItem(key) ?? 'null'); }
    catch { return null; }
  }
  const stored = readLayout(SETTINGS);
  const source = stored ?? (directMode ? readLayout(regularLayoutKey) : null);
  const lastSite = sites.includes(source?.site) ? source.site : undefined;
  let site = lastSite ?? sites[0];
  let drag;

  // #521: migrate only the last selected site's geometry; a first direct visit borrows geometry, not visibility.
  const saved = source?.layout ?? (lastSite ? source?.layouts?.[lastSite] : undefined);
  const savedNumber = (field, fallback) => Number.isFinite(saved?.[field]) ? saved[field] : fallback;
  const layout = {
    mode: ['left', 'right', 'float'].includes(saved?.mode) ? saved.mode : 'right',
    dockWidth: savedNumber('dockWidth', defaultDockWidth), x: savedNumber('x', 84), y: savedNumber('y', 24),
    width: savedNumber('width', 460), height: savedNumber('height', 590),
    collapsed: stored && saved ? saved.collapsed === true : directMode,
  };

  function persist() {
    try { localStorage.setItem(SETTINGS, JSON.stringify({site, layout})); }
    catch { /* The layout still works if persistence is disabled. */ }
  }

  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }

  function bounds(value) {
    const w = shell.clientWidth;
    const h = shell.clientHeight;
    const maxDock = Math.max(0, w - divider.offsetWidth - Math.min(144, Math.floor(w * .39)));
    const minDock = Math.min(232, maxDock);
    const margin = Math.min(8, Math.floor(Math.min(w, h) / 4));
    const minWidth = Math.min(256, w - 2 * margin);
    const minHeight = Math.min(220, h - 2 * margin);
    const fw = clamp(value.width, minWidth, w - 2 * margin);
    const fh = clamp(value.height, minHeight, h - 2 * margin);
    return {
      w, h, margin, minWidth, minHeight,
      minDock, maxDock, dockWidth: clamp(value.dockWidth, minDock, maxDock),
      x: clamp(value.x, margin, w - fw - margin), y: clamp(value.y, margin, h - fh - margin),
      width: fw, height: fh,
    };
  }

  function render() {
    const floating = layout.mode === 'float';
    shell.dataset.mode = layout.mode;
    shell.toggleAttribute('data-collapsed', layout.collapsed);
    panel.hidden = layout.collapsed;
    divider.hidden = layout.collapsed || floating;
    reveal.hidden = !layout.collapsed;
    reveal.setAttribute('aria-expanded', String(!layout.collapsed));
    move.hidden = resize.hidden = !floating;
    const b = bounds(layout);
    for (const [key, px] of Object.entries({
      'dock-width': b.dockWidth, 'float-width': b.width, 'float-height': b.height, 'float-x': b.x, 'float-y': b.y,
    })) shell.style.setProperty(`--${key}`, `${px}px`);
    divider.setAttribute('aria-valuemin', String(b.minDock));
    divider.setAttribute('aria-valuemax', String(b.maxDock));
    divider.setAttribute('aria-valuenow', String(b.dockWidth));
    modeButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === layout.mode)));
  }

  const mounted = embed.mount({
    view: byId('chat-view'), link: byId('open-chat'), unavailable: byId('chat-unavailable'),
    heading: byId('chat-site-heading'), note: byId('chat-frame-note'), framingHint: byId('chat-framing-hint'),
  });
  let framed = {};
  let shown;
  function showSite(key, reload = false) {
    // A collapsed same-tab shell must not load or reload a chat site the user is not looking at.
    if (directMode && (layout.collapsed || (shown === key && !reload))) return;
    // #515 "AC": the per-site framing switches apply to every shell entry, including the same-tab one (#520).
    mounted(key, framed);
    shown = key;
  }
  sites.forEach(key => sitePicker.add(new Option(embed.names[key], key)));
  const expand = directMode && new URLSearchParams(location.search).get('expand') === '1';
  if (expand) {
    layout.collapsed = false;
    const clean = new URL(location.href);
    clean.searchParams.delete('expand');
    history.replaceState(null, '', clean.href);
  }
  if (expand || stored && !stored.layout) persist();
  sitePicker.value = site;
  showSite(site);
  chrome.storage.local.get(FireflyChatFraming.KEY).then(stored => {
    framed = FireflyChatFraming.flags(stored);
    if (framed[site] === true) showSite(site, true);
  }, () => {});
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !Object.hasOwn(changes, FireflyChatFraming.KEY)) return;
    const next = FireflyChatFraming.flags({[FireflyChatFraming.KEY]: changes[FireflyChatFraming.KEY].newValue});
    const changed = (framed[site] === true) !== (next[site] === true);
    framed = next;
    // Reloading an unaffected site would discard the conversation the user is looking at.
    if (changed) showSite(site, true);
  });
  sitePicker.addEventListener('change', () => {
    if (!sites.includes(sitePicker.value)) return;
    site = sitePicker.value;
    showSite(site);
    persist();
  });
  let modeChange = 0;
  const afterPaint = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  async function changeMode(mode) {
    if (drag || layout.mode === mode && !shell.hasAttribute('data-layout-changing')) return;
    const change = ++modeChange;
    // A cross-origin iframe can still own Chromium's native hit region after DOM geometry moves.
    // End its old hit region before moving the divider and restore it after the new layout paints.
    shell.setAttribute('data-layout-changing', '');
    await afterPaint();
    if (change !== modeChange) return;
    layout.mode = mode;
    render();
    persist();
    await afterPaint();
    if (change === modeChange) shell.removeAttribute('data-layout-changing');
  }
  modeButtons.forEach(button => button.addEventListener('click', () => { void changeMode(button.dataset.mode); }));

  function collapse(value, returnFocus) {
    layout.collapsed = value;
    if (directMode && !value) showSite(site);
    render();
    persist();
    returnFocus?.focus();
  }
  hide.addEventListener('click', () => collapse(true, reveal));
  reveal.addEventListener('click', () => collapse(false, hide));

  function applyDelta(kind, value, start, dx, dy, edge = '') {
    if (kind === 'dock') {
      value.dockWidth = clamp(start.dockWidth + (value.mode === 'left' ? dx : -dx), start.minDock, start.maxDock);
    } else if (kind === 'move') {
      value.x = clamp(start.x + dx, start.margin, start.w - start.width - start.margin);
      value.y = clamp(start.y + dy, start.margin, start.h - start.height - start.margin);
    } else {
      // Resize from the starting rectangle so that clamping one edge never moves its opposite edge.
      let {x, y} = start;
      let right = x + start.width;
      let bottom = y + start.height;
      if (edge.includes('w')) x = clamp(x + dx, start.margin, right - start.minWidth);
      if (edge.includes('e')) right = clamp(right + dx, x + start.minWidth, start.w - start.margin);
      if (edge.includes('n')) y = clamp(y + dy, start.margin, bottom - start.minHeight);
      if (edge.includes('s')) bottom = clamp(bottom + dy, y + start.minHeight, start.h - start.margin);
      Object.assign(value, {x, y, width: right - x, height: bottom - y});
    }
    render();
  }

  function bindGesture(handle, kind, {edge = '', keys = true} = {}) {
    handle.addEventListener('pointerdown', event => {
      if (!event.isPrimary || event.button !== 0 || drag) return;
      if (handle === titleBar && (layout.mode !== 'float' || event.target.closest('button, a'))) return;
      // Native text/element dragging cancels pointer capture before the divider crosses an iframe.
      event.preventDefault();
      if (keys) handle.focus({preventScroll: true});
      drag = {id: event.pointerId, original: {...layout}, start: bounds(layout), x: event.clientX, y: event.clientY};
      handle.setPointerCapture(event.pointerId);
      shell.setAttribute('data-dragging', '');
    });
    handle.addEventListener('pointermove', event => {
      if (drag?.id !== event.pointerId) return;
      applyDelta(kind, layout, drag.start, event.clientX - drag.x, event.clientY - drag.y, edge);
    });
    function end(event, cancel) {
      if (drag?.id !== event.pointerId) return;
      if (cancel) Object.assign(layout, drag.original);
      else persist();
      drag = null;
      shell.removeAttribute('data-dragging');
      render();
    }
    handle.addEventListener('pointerup', event => end(event, false));
    handle.addEventListener('pointercancel', event => end(event, true));
    handle.addEventListener('lostpointercapture', event => end(event, true));
    if (!keys) return;
    handle.addEventListener('keydown', event => {
      const delta = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[event.key];
      if (!delta || (kind === 'dock' && !delta[0]) || event.altKey || event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      const step = event.shiftKey ? 40 : 10;
      applyDelta(kind, layout, bounds(layout), delta[0] * step, delta[1] * step, edge);
      persist();
    });
  }
  bindGesture(divider, 'dock');
  divider.addEventListener('dblclick', () => {
    layout.dockWidth = defaultDockWidth;
    render();
    persist();
  });
  bindGesture(move, 'move');
  bindGesture(titleBar, 'move', {keys: false});
  for (const handle of resizeHandles) bindGesture(handle, 'resize', {edge: handle.dataset.resize, keys: handle === resize});

  const color = matchMedia('(prefers-color-scheme: dark)');
  const setTheme = () => document.documentElement.classList.toggle('studio-theme-dark', color.matches);
  color.addEventListener('change', setTheme);
  window.addEventListener('resize', render);
  setTheme();
  render();

  function probeCapture(data) {
    const chatFrame = byId('chat-view').querySelector('iframe');
    const canvasFrame = byId('firefly-frame');
    // The shell owns exactly these two direct frames. Do not pick a destination if another frame appears.
    if (!P.dockCaptureSite(site, framed) || data.site !== site || window.length !== 2 ||
        !chatFrame?.contentWindow || !canvasFrame.contentWindow) return false;
    for (const [role, frame, origin] of [
      ['chat', chatFrame, `https://${site}`], ['canvas', canvasFrame, data.origin],
    ]) {
      frame.contentWindow.postMessage({action: P.DOCK_PROBE, role, nonce: data.nonce}, origin);
    }
    return true;
  }

  const fireflyFrame = byId('firefly-frame');
  let fireflyOrigin;
  let proofNonce;
  fireflyFrame.addEventListener('load', () => { proofNonce = undefined; });

  chrome.tabs.getCurrent().then(async tab => {
    if (!tab || window !== window.top) return;
    const contexts = directMode && typeof chrome.runtime.getContexts === 'function' ? await chrome.runtime.getContexts({
      contextTypes: ['TAB'], tabIds: [tab.id], frameIds: [0],
    }) : [];
    const documentId = contexts.find(context => direct.isShellURL(context.documentUrl))?.documentId;
    chrome.runtime.onMessage.addListener((data, sender, respond) => {
      if (sender.id !== chrome.runtime.id || sender.tab || sender.url !== chrome.runtime.getURL('background.js') ||
          data?.tabId !== tab.id) return;
      if (data.action === P.DOCK_PROBE) return respond(probeCapture(data));
      if (!directMode || !documentId || data.documentId !== documentId || data.origin !== fireflyOrigin ||
          typeof data.nonce !== 'string') return;
      if (data.action === direct.probe) {
        proofNonce = data.nonce;
        fireflyFrame.contentWindow.postMessage({action: direct.probe, nonce: data.nonce}, fireflyOrigin);
        respond(true);
      } else if (data.action === direct.request && data.nonce === proofNonce && direct.actions.has(data.command)) {
        proofNonce = undefined;
        if (data.command === 'toggle-dock') collapse(!layout.collapsed);
        else location.replace(`${fireflyOrigin}/`);
        respond(true);
      }
    });
  }).catch(() => { /* Unsupported browsers keep the ordinary page entry available. */ });

  async function openFirefly() {
    const origin = P.originSetting(await chrome.storage.local.get(P.ORIGIN_KEY));
    if (!(await chrome.permissions.contains({origins: [P.canvasPatterns(origin).permission]}))) return false;
    fireflyOrigin = origin;
    // Canvas clipboard and fullscreen support belongs only to the configured origin.
    fireflyFrame.allow = ['clipboard-read', 'clipboard-write', 'fullscreen'].map(feature => `${feature} ${origin}`).join('; ');
    // The canvas entry opens the shell on its own project; anything malformed falls back to the project list.
    fireflyFrame.src = new URL(FireflyChatDockSettingsHello.shellCanvasPath(location.search), origin).href;
    fireflyFrame.hidden = false;
    return true;
  }
  openFirefly().then(opened => { byId('firefly-problem').hidden = opened; }, () => { byId('firefly-problem').hidden = false; });
})();
